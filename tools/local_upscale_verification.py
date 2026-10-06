"""Run the real built-in model and download path in disposable browsers."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urlsplit

from PIL import Image, ImageChops
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select

from tools.browser_verification import BrowserCheck, browser, staged_service


def verify(driver, url, out, backend="wasm", profile_id="full"):
    profiles = json.loads((Path(__file__).parent / "local-upscale/models.json").read_text())
    profile = next(p for p in profiles if p["id"] == profile_id)
    model_id = profile["selection_id"] + ("-webgpu" if backend == "webgpu" else "")
    model_name = profile["name"] + (" WebGPU" if backend == "webgpu" else " CPU")
    check = BrowserCheck(driver, url, out)

    def element(key):
        return driver.find_element(By.ID, "upscale-" + key)

    driver.find_element(By.CSS_SELECTOR, '[data-tab="upscale"]').click()
    check.wait.until(lambda _: len(Select(element("model")).options) == 8)
    assert element("model").get_attribute("value") == "local-compact"
    Select(element("model")).select_by_value(model_id)
    assert not driver.find_elements(By.ID, "upscale-token")
    check.check("Eight local options, Compact CPU default, no token field")
    fixtures = Path(__file__).parent / "local-upscale/fixtures" / profile_id
    # An old page-level timer would now abort immediately. Local runs must not
    # schedule one, including preparation, repeat runs and retries.
    check.page(
        "window.localDeadlineCount=0; const original=setTimeout; window.setTimeout=(fn,ms,...args)=>{if(ms===600000){window.localDeadlineCount++;return original(fn,0,...args);}return original(fn,ms,...args);};"
    )
    adapter = check.page(
        "const a=await navigator.gpu?.requestAdapter(); const i=a?.info; return {secure:isSecureContext,available:!!a,fallback:a?.isFallbackAdapter??i?.isFallbackAdapter??null,vendor:i?.vendor,architecture:i?.architecture,device:i?.device,description:i?.description};"
    )
    if backend == "webgpu":
        assert adapter["available"] and adapter["fallback"] is not True

    def ready():
        status = element("status")
        assert "error" not in status.get_attribute("class").split(), status.text
        return "Ready to download" in status.text

    measured = []
    previous_hash = None
    for name in (
        ("small", "seams")
        if profile_id == "full"
        else ("small", "seams", "flat", "gradient", "edges", "random", "tiny", "slim")
    ):
        element("file").send_keys(str((fixtures / f"{name}.png").resolve()))
        check.wait.until(lambda _: "Image ready" in element("status").text)
        assert element("model").get_attribute("value") == model_id
        check.wait.until(lambda _: element("start").is_enabled())
        started = time.monotonic()
        element("start").click()
        check.page(
            "document.getElementById('upscale-form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));"
        )
        check.wait.until(lambda _: ready())
        check.wait.until(lambda _: element("start").is_enabled())
        seconds = time.monotonic() - started
        assert element("after-model").text == "Upscaled with " + model_name
        filename = element("download").get_attribute("download")
        result = out / filename
        result.unlink(missing_ok=True)
        element("download").click()
        check.wait.until(
            lambda _: result.exists()
            and not list(out.glob("*.part"))
            and not list(out.glob("*.crdownload"))
        )
        with (
            Image.open(result) as actual,
            Image.open(fixtures / f"{name}-reference.png") as expected,
        ):
            assert actual.size == expected.size and actual.mode == "RGBA"
            error = max(
                channel[1]
                for channel in ImageChops.difference(actual.convert("RGB"), expected).getextrema()
            )
            assert error <= 1, error
            with Image.open(fixtures / f"{name}.png") as original:
                assert actual.size == tuple(n * profile["scale"] for n in original.size)
                assert actual.getchannel("A").getextrema() == original.getchannel("A").getextrema()
        assert profile["sha256"] in element("result-version").get_attribute("textContent")
        measured.append(
            {
                "case": name,
                "model": profile_id,
                "backend": backend,
                "runtime_timings": element("result-settings").get_attribute("textContent"),
                "seconds": seconds,
                "max_rgb_byte_error": error,
                "bytes": result.stat().st_size,
                "sha256": hashlib.sha256(result.read_bytes()).hexdigest(),
            }
        )
        previous_hash = measured[-1]["sha256"]
        check.check(
            f"{name}: actual local inference, native RGB comparison, original alpha and PNG download"
        )
    assert check.page("return window.localDeadlineCount;") == 0
    check.check("Local processing and result preparation have no ten-minute deadline")
    before = element("download").get_attribute("href")
    Select(element("model")).select_by_value(
        "local-compact" if profile_id != "compact" else "local-nomos"
    )
    assert element("download").get_attribute("href") == before
    assert element("after-model").text == "Upscaled with " + model_name
    Select(element("model")).select_by_value(model_id)
    check.check("Changing next model preserves local result identity and download")
    element("start").click()
    check.wait.until(lambda _: element("cancel").is_displayed())
    element("cancel").click()
    check.wait.until(lambda _: not element("activity").is_displayed())
    assert element("download").get_attribute("href") == before
    assert check.module("package-runtime/store.js", "return await r.lease();") is None
    check.check("Cancellation releases the processing lease and retains the previous result")
    element("retry").click()
    check.wait.until(lambda _: ready())
    check.wait.until(lambda _: element("start").is_enabled())
    assert check.page("return window.localDeadlineCount;") == 0
    result.unlink()
    element("download").click()
    check.wait.until(
        lambda _: result.exists()
        and not list(out.glob("*.part"))
        and not list(out.glob("*.crdownload"))
    )
    assert hashlib.sha256(result.read_bytes()).hexdigest() == previous_hash
    check.check("Retry reuses the original image and produces deterministic PNG bytes")
    # A second model must consume the original prepared input without re-upload.
    other = next(
        p for p in profiles if p["id"] == ("compact" if profile_id != "compact" else "nomos")
    )
    other_id = other["selection_id"] + ("-webgpu" if backend == "webgpu" else "")
    old_caption = element("after-model").text
    before = element("download").get_attribute("href")
    Select(element("model")).select_by_value(other_id)
    check.wait.until(lambda _: element("start").is_enabled())
    assert element("after-model").text == old_caption
    element("start").click()
    check.wait.until(lambda _: element("activity").is_displayed())
    assert element("download").get_attribute("href") == before
    check.wait.until(lambda _: ready() and element("start").is_enabled())
    assert element("after-model").text == "Upscaled with " + other["name"] + (
        " WebGPU" if backend == "webgpu" else " CPU"
    )
    other_file = out / element("download").get_attribute("download")
    other_file.unlink(missing_ok=True)
    element("download").click()
    check.wait.until(lambda _: other_file.exists())
    with Image.open(other_file) as actual, Image.open(fixtures / f"{name}.png") as original:
        assert actual.size == tuple(n * other["scale"] for n in original.size)
    check.check("Cross-model repeat uses original input and replaces result only on success")
    manifest = check.page(
        "return await (await fetch(new URL(document.querySelector('meta[name=runtime-manifest]').content,location.href))).json();"
    )
    used = {profile_id, other["id"]}
    for p in profiles:
        if p["id"] not in used:
            assert not any(
                r["url"].endswith(manifest["assets"][p["asset"]]["url"]) for r in check.requests
            )
    check.check("Only selected model assets are fetched")
    driver.set_window_size(390, 844)
    assert check.page("return document.documentElement.scrollWidth <= innerWidth + 1;")
    driver.save_screenshot(str(out / "mobile.png"))
    check.check("Narrow layout has no horizontal overflow")
    assert not [r for r in check.requests if r["method"] != "GET"]
    assert not [
        r
        for r in check.requests
        if urlsplit(r["url"]).netloc != urlsplit(url).netloc or "/api/" in r["url"]
    ]
    assert check.module("package-runtime/store.js", "return (await r.listJobs()).length;") == 0
    check.check("No uploads, external requests, token or saved image records")
    driver.refresh()
    driver.find_element(By.CSS_SELECTOR, '[data-tab="upscale"]').click()
    check.wait.until(lambda _: len(Select(element("model")).options) == 8)
    assert not element("download").is_displayed()
    check.check("Reload clears session images and outputs")
    return {
        "checks": check.checks,
        "timings": measured,
        "requests": check.requests,
        "adapter": adapter,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--backend", choices=["wasm", "webgpu"], default="wasm")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    results = {}
    with staged_service(args.release.resolve(), args.output) as url:
        for name in ("chromium", "firefox"):
            out = (args.output / name).resolve()
            out.mkdir(exist_ok=True)
            driver = browser(name, out)
            try:
                if args.backend == "webgpu":
                    driver.get(url)
                    available = driver.execute_async_script(
                        "const done=arguments[0]; navigator.gpu ? navigator.gpu.requestAdapter().then(a=>done(!!a),()=>done(false)) : done(false);"
                    )
                    if not available:
                        check = BrowserCheck(driver, url, out)
                        driver.find_element(By.CSS_SELECTOR, '[data-tab="upscale"]').click()
                        check.wait.until(
                            lambda _: len(
                                Select(driver.find_element(By.ID, "upscale-model")).options
                            )
                            == 8
                        )
                        Select(driver.find_element(By.ID, "upscale-model")).select_by_value(
                            "local-real-esrgan-webgpu"
                        )
                        check.wait.until(
                            lambda _: driver.find_element(By.ID, "upscale-availability").text
                            not in {"", "Checking WebGPU availability…"}
                        )
                        driver.find_element(By.ID, "upscale-file").send_keys(
                            str(
                                (
                                    Path(__file__).parent / "local-upscale/fixtures/small.png"
                                ).resolve()
                            )
                        )
                        check.wait.until(
                            lambda _: "Image ready"
                            in driver.find_element(By.ID, "upscale-status").text
                        )
                        assert not driver.find_element(By.ID, "upscale-start").is_enabled()
                        manifest = check.module(
                            "package-runtime/assets.js", "return await r.manifest();"
                        )
                        gpu_urls = {
                            manifest["assets"]["local-upscale-webgpu-" + key]["url"]
                            for key in ("runtime", "glue", "wasm")
                        }
                        gpu_urls.add(manifest["assets"]["local-upscale-model"]["url"])
                        assert not any(
                            any(request["url"].endswith(path) for path in gpu_urls)
                            for request in check.requests
                        )
                        results[name] = {
                            "available": False,
                            "unavailable_ui": "passed",
                            "inference": "not supported",
                        }
                        continue
                results[name] = verify(driver, url, out, args.backend)
            except Exception:
                driver.save_screenshot(str(out / "failure.png"))
                (out / "failure.html").write_text(driver.page_source)
                raise
            finally:
                driver.quit()
    (args.output / "report.json").write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    main()
