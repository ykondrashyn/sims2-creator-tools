"""Real browser checks against plain static files at root and project paths."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
from urllib.parse import urlsplit

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select
from tools.browser_verification import BrowserCheck, browser
from tools.local_upscale_verification import verify as upscale
from tools.project import ROOT


@contextmanager
def static_server(site):
    with tempfile.TemporaryDirectory() as temporary:
        parent = Path(temporary)
        (parent / "sims2-creator-tools").symlink_to(site.resolve(), target_is_directory=True)

        class Handler(SimpleHTTPRequestHandler):
            def translate_path(self, path):
                if urlsplit(path).path.startswith("/sims2-creator-tools/"):
                    self.directory = str(parent)
                else:
                    self.directory = str(site.resolve())
                return super().translate_path(path)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(site)))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/"
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


def run_suite(name, suite, url, folder):
    folder.mkdir(parents=True, exist_ok=True)
    downloads = folder if suite in ("cpu", "gpu") else folder / "downloads"
    downloads.mkdir(exist_ok=True)
    driver = browser(name, downloads)
    try:
        if suite in ("cpu", "gpu"):
            if suite == "gpu":
                driver.get(url)
                available = driver.execute_async_script(
                    "const done=arguments[0]; navigator.gpu ? navigator.gpu.requestAdapter().then(a=>done(!!a),()=>done(false)) : done(false);"
                )
                if not available:
                    check = BrowserCheck(driver, url, folder)
                    driver.find_element(By.CSS_SELECTOR, '[data-tab="upscale"]').click()
                    check.wait.until(
                        lambda _: len(Select(driver.find_element(By.ID, "upscale-model")).options)
                        == 8
                    )
                    driver.find_element(By.ID, "upscale-file").send_keys(
                        str(ROOT / "tools/local-upscale/fixtures/compact/small.png")
                    )
                    check.wait.until(
                        lambda _: "Image ready" in driver.find_element(By.ID, "upscale-status").text
                    )
                    profiles = json.loads((ROOT / "tools/local-upscale/models.json").read_text())
                    for profile in profiles:
                        Select(driver.find_element(By.ID, "upscale-model")).select_by_value(
                            profile["selection_id"] + "-webgpu"
                        )
                        check.wait.until(
                            lambda _: driver.find_element(By.ID, "upscale-availability").text
                            not in ("", "Checking WebGPU availability…")
                        )
                        assert not driver.find_element(By.ID, "upscale-start").is_enabled()
                    manifest = check.module(
                        "package-runtime/assets.js", "return await r.manifest();"
                    )
                    blocked = {manifest["assets"][p["asset"]]["url"] for p in profiles}
                    blocked.update(
                        manifest["assets"]["local-upscale-webgpu-" + k]["url"]
                        for k in ("runtime", "glue", "wasm")
                    )
                    assert not any(
                        any(r["url"].endswith(path) for path in blocked) for r in check.requests
                    )
                    return {
                        "available": False,
                        "unavailable_ui": "passed for all four models",
                        "no_inference_assets_downloaded": True,
                    }
            results = {}
            for i, model in enumerate(("compact", "animevideo", "nomos", "full")):
                if i:
                    # Each probe owns its BiDi request listener and clean storage.
                    driver.quit()
                    driver = browser(name, downloads)
                results[model] = upscale(
                    driver, url, folder, "webgpu" if suite == "gpu" else "wasm", model
                )
                (folder / "models-report.json").write_text(json.dumps(results, indent=2) + "\n")
            return results
        check = BrowserCheck(driver, url, folder)
        if suite == "creators":
            from tools.browser_scenarios import exercise

            exercise(check)
        else:
            check.synthetic()
        origin = urlsplit(url).netloc
        assert all(
            r["method"] in ("GET", "HEAD")
            and urlsplit(r["url"]).netloc == origin
            and "/api/" not in r["url"]
            for r in check.requests
        )
        return {"checks": check.checks, "requests": check.requests}
    except Exception:
        driver.save_screenshot(str(folder / "failure.png"))
        (folder / "failure.html").write_text(driver.page_source)
        raise
    finally:
        driver.quit()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, default=ROOT / "site")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--browser", choices=["chromium", "firefox", "both"], default="both")
    parser.add_argument(
        "--suite", choices=["synthetic", "creators", "cpu", "gpu", "all"], default="all"
    )
    parser.add_argument("--prefix", choices=["root", "project", "both"], default="both")
    parser.add_argument("--url", help="Existing deployed site, instead of a local static server")
    args = parser.parse_args()
    native = ROOT / "artifacts/static-pages/build/target/release"
    for key, value in {
        "OBJECT_NATIVE_BINARY": native / "examples/object",
        "TS2_HAIR_BUILDER": native / "ts2-hair-builder",
    }.items():
        os.environ.setdefault(key, str(value))
    results = {}

    def check(base):
        for prefix in (
            [""]
            if args.url
            else ["", "sims2-creator-tools/"]
            if args.prefix == "both"
            else ["" if args.prefix == "root" else "sims2-creator-tools/"]
        ):
            for name in ["chromium", "firefox"] if args.browser == "both" else [args.browser]:
                for suite in (
                    ["synthetic", "creators", "cpu", "gpu"] if args.suite == "all" else [args.suite]
                ):
                    key = f"{'project' if prefix else 'root'}/{name}/{suite}"
                    print(key, flush=True)
                    results[key] = run_suite(
                        name, suite, base + prefix, (args.output / key).resolve()
                    )
                    (args.output / "report.json").write_text(json.dumps(results, indent=2) + "\n")

    if args.url:
        check(args.url)
    else:
        with static_server(args.site) as url:
            check(url)


if __name__ == "__main__":
    main()
