"""Exercise fresh frontend, workers and IndexedDB in disposable real browsers."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait

from tools.project import ROOT


@contextmanager
def staged_service(runtime: Path, out: Path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {**os.environ, "PACKAGE_RUNTIME_ASSET_ROOT": str(runtime)}
    historical = json.loads((ROOT / "tests/fixtures/registry.json").read_text()).get(
        "historical_runtime"
    )
    fixture_root = Path(env.get("PROJECT_FIXTURE_ROOT", ROOT))
    if historical and (fixture_root / historical["path"]).exists():
        env["PACKAGE_RETAINED_ASSET_ROOTS"] = str(fixture_root / historical["path"])
    lan = os.environ.get("PROJECT_BROWSER_HOST")

    frozen = runtime / "app"
    if frozen.exists():
        env["PYTHONPATH"] = str(frozen)
        env.pop("PROJECT_STATIC_ROOT", None)
    with (out / "service.log").open("w") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "package_creation.service",
                *(["--lan"] if lan else []),
                "--port",
                str(port),
                "--spool",
                str(out / "spool"),
            ],
            cwd=frozen if frozen.exists() else ROOT,
            env=env,
            stdout=log,
            stderr=log,
        )
        try:
            url = f"http://{lan or '127.0.0.1'}:{port}/"
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("Staged service stopped. See service.log.")
                try:
                    with urllib.request.urlopen(url + "ready", timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(0.1)
            else:
                raise RuntimeError("Staged service did not become ready")
            yield url
        finally:
            process.terminate()
            process.wait(timeout=15)


def browser(name, downloads, profile=None):
    if name == "firefox":
        from selenium.webdriver.firefox.options import Options

        options = Options()
        options.add_argument("-headless")
        options.add_argument("-no-remote")
        binary = os.environ.get("FIREFOX_BINARY")
        mac = Path("/Applications/Firefox.app/Contents/MacOS/firefox")
        options.binary_location = binary or (str(mac) if mac.exists() else "")
        options.set_preference("browser.download.folderList", 2)
        options.set_preference("browser.download.dir", str(downloads))
        options.set_preference(
            "browser.helperApps.neverAsk.saveToDisk",
            "application/octet-stream,application/zip,image/png",
        )
        options.set_preference("dom.disable_beforeunload", True)
        if profile:
            options.add_argument("-profile")
            options.add_argument(str(profile))
        options.enable_bidi = True
        return webdriver.Firefox(options=options)
    from selenium.webdriver.chrome.options import Options

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    binary = os.environ.get("CHROMIUM_BINARY") or os.environ.get("BROWSER_BINARY")
    if binary:
        options.binary_location = binary
    options.add_experimental_option(
        "prefs",
        {
            "download.default_directory": str(downloads),
            "download.prompt_for_download": False,
        },
    )
    if profile:
        options.add_argument("--user-data-dir=" + str(profile))
    options.enable_bidi = True
    driver = webdriver.Chrome(options=options)
    # Headless Chrome needs an explicit download destination for generated
    # binary files. This is confined to the disposable automation profile.
    driver.execute_cdp_cmd(
        "Browser.setDownloadBehavior",
        {"behavior": "allow", "downloadPath": str(downloads), "eventsEnabled": True},
    )
    return driver


class BrowserCheck:
    def __init__(self, driver, url, out):
        self.d = driver
        self.url = url
        self.out = out
        self.checks = []
        self.requests = []
        self.wait = WebDriverWait(driver, 180)
        driver.set_script_timeout(180)
        driver.network.add_event_handler("before_request_sent", self.network)
        driver.set_window_size(1280, 1000)
        driver.get(url)
        self.prefix = self.page(
            "return new URL(document.querySelector('script[src*=lazy-bootstrap],script[src*=app-wasm]').src).pathname.replace(/(?:lazy-bootstrap|app-wasm)\\.js$/, '');"
        )

    def network(self, event):
        request = event.get("request", {}) if isinstance(event, dict) else vars(event.request)
        if request.get("url", "").startswith(("http:", "https:")):
            self.requests.append({"url": request["url"], "method": request["method"]})

    def page(self, code):
        # Firefox's classic WebDriver sandbox has its own module instances.
        # BiDi runs probes in the page realm, observing the actual UI caches.
        remote = self.d.script.execute(
            "async()=>JSON.stringify(await (async()=>{"
            + code
            + "})().then(value=>({value:value??null}), error=>({error:String(error)+'\\n'+String(error.stack||'')})))"
        )
        value = json.loads(remote["value"])
        if "error" in value:
            raise AssertionError(value["error"])
        return value["value"]

    def module(self, name, code):
        return self.page("const r=await import(" + json.dumps(self.prefix + name) + ");" + code)

    def runtime(self, code):
        return self.module("package-runtime/client.js", code)

    def check(self, name):
        self.checks.append(name)
        print(name, flush=True)

    def synthetic(self):
        from tools.navigation_verification import verify

        verify(self)
        metrics = self.module(
            "package-runtime/hashing.js",
            """let ticks=0; const timer=setInterval(()=>ticks++,1); const started=performance.now();
const data=new Uint8Array(32*1024*1024); data.fill(97);
const sha=await r.hashBlob(new Blob([data])); clearInterval(timer);
return {sha,ticks,milliseconds:performance.now()-started};""",
        )
        assert metrics["sha"] == hashlib.sha256(b"a" * (32 * 1024 * 1024)).hexdigest()
        assert metrics["ticks"] > 2, metrics
        self.hash_metrics = metrics
        self.check("32 MiB incremental worker hashing matches SHA-256 while the UI timer runs")
        self.module(
            "package-runtime/session.js",
            """
const {manifest}=await import('./assets.js'); await r.boot(await manifest(),'painting');
const c=await r.call('capabilities'); if(c.limits.wasm_heap_bytes!==1073741824)throw Error('Heap limit');
try {await r.call('not-an-operation'); throw Error('Invalid request accepted');}
catch(e){if(e.code!=='engine_error')throw e;} r.failWorker('Synthetic check finished'); return true;
""".replace(
                "import('./assets.js')",
                "import(" + json.dumps(self.prefix + "package-runtime/assets.js") + ")",
            ),
        )
        self.check("Fresh WASM starts and structured worker errors survive transport")
        record = self.runtime("""
const manifest=await r.manifest(); const id=r.store.id();
const j={schema_version:1,id,kind:'hair',revision:1,state:'draft',manifest,files:[],parameters:{},ui:{},
snapshot:{identities:{texture:'ffffffffffffffff'},number:1.0000000000000002}};
await r.store.saveJob(j); return j;
""")
        job_id = json.dumps(record["id"])
        self.d.refresh()
        restored = self.runtime("return await r.store.getJob(" + job_id + ");")
        assert restored["snapshot"] == record["snapshot"]
        self.check("Reload preserves saved identities and snapshot numbers without rewriting")
        token = self.runtime("return await r.store.acquire(" + job_id + ");")
        first = self.d.current_window_handle
        self.d.switch_to.new_window("tab")
        self.d.get(self.url)
        error = self.runtime(
            "try{await r.store.acquire('second-tab');return '';}catch(e){return e.message;}"
        )
        assert "Another package build" in error
        self.d.close()
        self.d.switch_to.window(first)
        self.runtime("await r.store.interrupt(" + json.dumps(token) + "); return true;")
        error = self.runtime(
            "try{await r.store.putBlob('late',new Blob(['x']),"
            + json.dumps(token)
            + ");return '';}catch(e){return e.message;}"
        )
        assert "interrupted or replaced" in error
        self.check("Cross-tab lease and cancellation fencing reject a stale output")
        damaged = self.runtime(
            "const j=await r.store.getJob("
            + job_id
            + ");j.revision++;j.schema_version=99;await r.store.saveJob(j);try{await r.restore(j.id);return '';}catch(e){return e.code;}"
        )
        assert damaged == "saved_runtime_recovery"
        assert self.runtime("return !!await r.store.getJob(" + job_id + ");")
        self.runtime("await r.store.removeJob(" + job_id + ");return true;")
        assert not self.runtime("return !!await r.store.getJob(" + job_id + ");")
        self.check("Unsupported records remain stored until explicit deletion")
        self.d.set_window_size(390, 900)
        assert self.page("return document.documentElement.scrollWidth<=innerWidth+2")
        self.d.save_screenshot(str(self.out / "narrow.png"))
        assert self.requests and all(r["method"] in {"GET", "HEAD"} for r in self.requests)
        self.check("Narrow layout fits and captured creation traffic contains only asset reads")


def historical_check(check):
    root = Path(os.environ.get("PROJECT_FIXTURE_ROOT", ROOT))
    fixture = json.loads((ROOT / "tests/fixtures/registry.json").read_text())["historical_runtime"]
    manifest = json.loads((root / fixture["path"] / "manifest.json").read_text())
    assert manifest["release"] == fixture["release"]
    record = check.runtime(
        "const old=(await r.store.listJobs()).find(j=>j.kind==='tattoo'&&j.state==='complete');const parameters=structuredClone(old.parameters);delete parameters.id;delete parameters.identities;return await r.saveDraft({kind:'tattoo',files:old.files,spec:old.spec,parameters,manifest:"
        + json.dumps(manifest)
        + "});"
    )
    job_id = json.dumps(record["id"])
    check.runtime("await r.start(await r.store.getJob(" + job_id + ")); return true;")

    def complete(_):
        job = check.runtime("return await r.store.getJob(" + job_id + ");")
        if job["state"] in {"failed", "cancelled"}:
            raise AssertionError(job.get("error"))
        return job if job["state"] == "complete" else False

    job = check.wait.until(complete)
    assert job["manifest"]["release"] == fixture["release"]
    check.runtime("await r.download(" + job_id + "); return true;")
    check.check(
        "An explicitly registered historical engine builds and downloads without migrating its saved manifest"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--synthetic", action="store_true")
    parser.add_argument(
        "--browsers", nargs="+", choices=["firefox", "chromium"], default=["firefox", "chromium"]
    )
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    with staged_service(args.release.resolve(), out) as url:
        for name in args.browsers:
            folder = out / name
            downloads = folder / "downloads"
            downloads.mkdir(parents=True, exist_ok=True)
            profile = folder / "profile"
            profile.mkdir(exist_ok=True)
            driver = browser(name, downloads, profile)
            check = None
            try:
                check = BrowserCheck(driver, url, folder)
                check.synthetic()
                from tools.performance_verification import exercise as performance_checks

                performance_checks(check, full=not args.synthetic)
                if not args.synthetic:
                    from tools.browser_scenarios import exercise

                    exercise(check)
                    historical_check(check)
                    before = check.runtime(
                        "return (await r.store.listJobs()).filter(j=>j.state==='complete').map(j=>({id:j.id,snapshotHash:j.snapshotHash,output:j.output}));"
                    )
                    checks, requests, metrics = check.checks, check.requests, check.hash_metrics
                    driver.quit()
                    driver = browser(name, downloads, profile)
                    check = BrowserCheck(driver, url, folder)
                    check.checks, check.requests, check.hash_metrics = checks, requests, metrics
                    after = check.runtime(
                        "return (await r.store.listJobs()).filter(j=>j.state==='complete').map(j=>({id:j.id,snapshotHash:j.snapshotHash,output:j.output}));"
                    )
                    assert before == after
                    for saved in after:
                        check.runtime(
                            "await r.prepareDownload(" + json.dumps(saved["id"]) + "); return true;"
                        )
                    check.check(
                        "Browser restart preserves all completed snapshots and revalidates every downloadable blob"
                    )
                (folder / "report.json").write_text(
                    json.dumps(
                        {
                            "passed": True,
                            "checks": check.checks,
                            "requests": check.requests,
                            "hashing": check.hash_metrics,
                            "browser": driver.capabilities["browserVersion"],
                            "release": json.loads((args.release / "manifest.json").read_text())[
                                "release"
                            ],
                            "gameplay": "not tested",
                        },
                        indent=2,
                    )
                    + "\n"
                )
            finally:
                if check:
                    driver.save_screenshot(str(folder / "last.png"))
                driver.quit()


if __name__ == "__main__":
    main()
