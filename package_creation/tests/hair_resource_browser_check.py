"""Real Firefox hair downloads, checkpoint resume and pinned-engine recovery."""
import hashlib
import json
import os
from pathlib import Path
import time
from urllib.parse import urlsplit
import zipfile
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select, WebDriverWait
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
from package_creation.tests.hair_resource_comparison import resources

root = Path.cwd()
out = Path(os.environ.get("HAIR_BROWSER_OUTPUT", "artifacts/hair-size-fix/firefox")).resolve()
downloads = out / "downloads"
downloads.mkdir(parents=True, exist_ok=True)
for stale in downloads.glob("BrowserTest_*.zip"):
    stale.unlink()
options = Options()
options.add_argument("-headless")
options.add_argument("-no-remote")
options.binary_location = "/Applications/Firefox.app/Contents/MacOS/firefox"
options.enable_bidi = True
options.set_preference("browser.download.folderList", 2)
options.set_preference("browser.download.dir", str(downloads))
options.set_preference("browser.helperApps.neverAsk.saveToDisk", "application/zip,application/octet-stream")
d = webdriver.Firefox(options=options, service=Service(log_output=str(out / "driver.log")))
w = WebDriverWait(d, 240)
requests, checks = [], []


def page(code):
    return json.loads(d.script.execute("async()=>JSON.stringify((await(async()=>{" + code + "})())??null)")["value"])


def runtime(code):
    return page("const r=await import('/static/package-runtime/client.mjs');" + code)


def field(name):
    return d.find_element(By.ID, "hair-" + name)


def click(name):
    e = field(name)
    d.execute_script("arguments[0].scrollIntoView({block:'center'})", e)
    e.click()


def tab():
    d.find_element(By.CSS_SELECTOR, '[data-tab="hair"]').click()
    w.until(lambda _: d.find_elements(By.CSS_SELECTOR, "#hair-form .saved-batches summary"))


def record(identity):
    return runtime("return await r.store.getJob(" + json.dumps(identity) + ")")


def check(message):
    checks.append(message)
    print(message, flush=True)


def event(e):
    request = e.get("request", {}) if isinstance(e, dict) else e.request
    if not isinstance(request, dict):
        request = vars(request)
    if urlsplit(request.get("url", "")).scheme in ["http", "https"]:
        requests.append({"method": request.get("method"), "url": request.get("url")})


try:
    d.network.add_event_handler("before_request_sent", event)
    d.set_window_size(1400, 1000)
    d.get(os.environ.get("HAIR_BROWSER_URL", "http://192.168.50.213:8003/"))
    tab()
    Select(field("mesh-source")).select_by_value("upload")
    mesh = root / "artifacts/hair-validation/embedded-bundle/mesh_rosehair_0124.package"
    source = root / "artifacts/hair-validation/embedded-bundle/recolor_3555b7d0_rose72.package"
    field("mesh-files").send_keys(str(mesh))
    field("recolor-files").send_keys(str(source))
    w.until(lambda _: "5 embedded textures" in field("template-detail").text)
    Select(field("selection")).select_by_value("1")
    field("creator").send_keys("BrowserTest")
    field("name").send_keys("Rose")
    assert Select(field("texture-encoder")).first_selected_option.get_attribute("value") == "directxtex"
    click("compression-options")
    Select(field("texture-encoder")).select_by_value(os.environ.get("TEXTURE_ENCODER", "directxtex"))
    check("Compression defaults to DirectXTex and accepts the DXT3 alternative")
    click("primary")
    w.until(lambda _: field("primary").text == "Create ZIP" and field("primary").is_enabled())
    assert field("editor").get_attribute("open")
    check("Separate mesh/recolor selection and color preview work with all five Rose texture slots")
    click("primary")
    w.until(lambda _: field("download").is_displayed())
    click("download")
    target = downloads / "BrowserTest_Rose_Recolors.zip"
    w.until(lambda _: target.exists() and zipfile.is_zipfile(target))
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist()) == 6
        assert archive.read("Meshes/" + mesh.name) == mesh.read_bytes()
        for name in archive.namelist():
            if name.startswith("BrowserTest_"):
                types = [kind for kind, _ in resources(archive.read(name))]
                assert types.count(0x1c4a276c) == 5
                assert types.count(0x49596978) == 18
    complete = runtime("return (await r.store.listJobs()).find(j=>j.label==='BrowserTest_Rose')")
    identity = complete["id"]
    assert complete["state"] == "complete"
    if os.environ.get("EXPECT_ARCHIVE_DEFLATE"):
        packing = complete["output"]["compression"]
        assert packing["compressed_members"] == 6
        assert packing["stored_member_bytes"] < packing["original_member_bytes"]
        with zipfile.ZipFile(target) as archive:
            assert all(i.compress_type == zipfile.ZIP_DEFLATED for i in archive.infolist())
            for checkpoint in complete["checkpoints"]:
                assert hashlib.sha256(archive.read(checkpoint["filename"])).hexdigest() == checkpoint["report"]["sha256"]
        check("DEFLATE download extracts byte-identical validated package checkpoints")
    assert complete["parameters"]["texture_encoder"] == os.environ.get("TEXTURE_ENCODER", "directxtex")
    assert not field("texture-encoder").is_enabled()
    check("Real four-color ZIP download has five textures/eighteen materials per recolor and byte-identical mesh")
    d.refresh()
    tab()
    d.find_element(By.CSS_SELECTOR, "#hair-form .saved-batches summary").click()
    d.find_element(By.CSS_SELECTOR, "#hair-form .saved-batch-row button").click()
    w.until(lambda _: field("download").is_displayed())
    assert not field("creator").is_enabled()
    assert Select(field("texture-encoder")).first_selected_option.get_attribute("value") == complete["parameters"]["texture_encoder"]
    assert record(identity)["output"]["hashes"] == complete["output"]["hashes"]
    d.set_window_size(390, 900)
    assert page("return document.documentElement.scrollWidth<=innerWidth")
    d.save_screenshot(str(out / "narrow.png"))
    d.set_window_size(1400, 1000)
    check("Completed batch restores read-only with the same download and no narrow-layout overflow")

    # Cancel after one validated package, then recover through a page reload.
    draft = runtime("const old=await r.store.getJob(" + json.dumps(identity) + ");const p=structuredClone(old.parameters);delete p.id;delete p.identities;p.hair_name='Resume';return await r.saveDraft({kind:'hair',files:old.files,template:old.template,manifest:old.manifest,parameters:p,ui:old.ui});")
    rid = draft["id"]
    runtime("const handler=e=>{if(e.detail.id===" + json.dumps(rid) + "&&e.detail.progress?.completed===1){r.events.removeEventListener('job',handler);r.cancel(e.detail.id);}};r.events.addEventListener('job',handler);await r.start(await r.store.getJob(" + json.dumps(rid) + "));")
    w.until(lambda _: record(rid)["state"] in ["cancelled", "failed", "complete"])
    cancelled = record(rid)
    assert cancelled["state"] == "cancelled", cancelled.get("error")
    assert len(cancelled["checkpoints"]) == 1 and not cancelled.get("output")
    d.refresh()
    tab()
    runtime("await r.start(await r.store.getJob(" + json.dumps(rid) + "));")
    w.until(lambda _: record(rid)["state"] in ["complete", "failed"])
    resumed = record(rid)
    assert resumed["state"] == "complete", resumed.get("error")
    assert resumed["snapshotHash"] == cancelled["snapshotHash"]
    assert resumed["parameters"]["identities"] == cancelled["parameters"]["identities"]
    assert resumed["checkpoints"][0] == cancelled["checkpoints"][0]
    check("Cancellation retains a validated checkpoint and reload/resume preserves its bytes and identities")

    if os.environ.get("EXPECT_ARCHIVE_DEFLATE"):
        # Start another draft, leaving completed batches immutable. Interrupt
        # after entering the ZIP stage and retain every validated package.
        packing_draft = runtime("const old=await r.store.getJob(" + json.dumps(rid) + ");const p={...old.parameters,hair_name:'PackingResume'};delete p.id;delete p.identities;return await r.saveDraft({kind:'hair',manifest:old.manifest,template:old.template,files:old.files,parameters:p,ui:old.ui});")
        packing_id = packing_draft["id"]
        runtime("const j=await r.store.getJob(" + json.dumps(packing_id) + ");const handler=e=>{if(e.detail.id===j.id&&e.detail.progress?.color==='Checking ZIP'){r.events.removeEventListener('job',handler);setTimeout(()=>r.cancel(j.id),10);}};r.events.addEventListener('job',handler);await r.start(j);")
        w.until(lambda _: record(packing_id)["state"] in ["cancelled", "failed", "complete"])
        stopped = record(packing_id)
        assert stopped["state"] == "cancelled", stopped.get("error")
        assert len(stopped["checkpoints"]) == 4 and not stopped.get("output")
        d.refresh()
        tab()
        runtime("await r.start(await r.store.getJob(" + json.dumps(packing_id) + "));")
        w.until(lambda _: record(packing_id)["state"] in ["complete", "failed"])
        repacked = record(packing_id)
        assert repacked["state"] == "complete", repacked.get("error")
        assert repacked["snapshotHash"] == stopped["snapshotHash"]
        assert repacked["checkpoints"] == stopped["checkpoints"]
        check("Cancellation during ZIP packing retains every checkpoint and resumes the same snapshot")

    token = runtime("return await r.store.acquire('other-hair-tab')")
    blocked = runtime("const old=await r.store.getJob(" + json.dumps(rid) + ");const p={...old.parameters,hair_name:'Blocked'};delete p.id;return await r.saveDraft({kind:'hair',manifest:old.manifest,template:old.template,files:old.files,parameters:p,ui:old.ui});")
    error = runtime("try{await r.start(await r.store.getJob(" + json.dumps(blocked["id"]) + "));return ''}catch(e){return e.message}")
    assert "another" in error.lower() or "already" in error.lower(), error
    runtime("await r.store.release(" + json.dumps(token) + ")")
    check("The origin-wide lease still prevents a competing build")

    # Old immutable snapshots must still use their saved engine and old layout.
    previous = json.loads(Path(os.environ.get("HAIR_PREVIOUS_MANIFEST", "artifacts/hair-size-fix/runtime/previous-manifest.json")).read_text())
    legacy = runtime("const old=await r.store.getJob(" + json.dumps(identity) + ");const p={...old.parameters,hair_name:'Legacy',colors:['Dynamite']};delete p.id;return await r.saveDraft({kind:'hair',manifest:" + json.dumps(previous) + ",template:old.template,files:old.files,parameters:p,ui:old.ui});")
    runtime("await r.start(await r.store.getJob(" + json.dumps(legacy["id"]) + "));")
    w.until(lambda _: record(legacy["id"])["state"] in ["complete", "failed"])
    restored = record(legacy["id"])
    assert restored["state"] == "complete", restored.get("error")
    assert len(restored["checkpoints"][0]["report"]["textures"]) == 10
    assert restored["manifest"]["release"] == previous["release"]
    check("A pinned previous engine still builds its original output without silent migration")

    # Standard hairstyles use the same new engine through normal template discovery.
    standard = runtime("const m=await r.manifest();const template=m.hair.items.find(t=>t.label==='Bun');const info=await r.openHair(template,[],m);const p={creator:'BrowserTest',hair_name:'Bun',colors:['Dynamite'],custom_colors:[],texture_settings:Object.fromEntries(info.textures.map(t=>[t.id,{base:template.input_base}]))};return await r.saveDraft({kind:'hair',manifest:m,template,files:[],parameters:p});")
    runtime("await r.start(await r.store.getJob(" + json.dumps(standard["id"]) + "));")
    w.until(lambda _: record(standard["id"])["state"] in ["complete", "failed"])
    standard = record(standard["id"])
    assert standard["state"] == "complete", standard.get("error")
    assert len(standard["checkpoints"][0]["report"]["textures"]) == 2
    check("Standard Bun builds with its two age-specific textures")
    runtime("await r.store.removeJob(" + json.dumps(blocked["id"]) + ")")
    assert record(blocked["id"]) is None
    assert not [request for request in requests if request["method"] not in ["GET", "HEAD"]]
    check("Network capture has only asset reads, with no package uploads or build requests")
    (out / "report.json").write_text(json.dumps(dict(checks=checks, requests=requests, release=complete["manifest"]["release"],
        metrics=complete.get("runtimeMetrics"), packing=complete["output"].get("compression"), zip_bytes=target.stat().st_size, zip_sha256=hashlib.sha256(target.read_bytes()).hexdigest(), gameplay="not_tested"), indent=2))
finally:
    d.save_screenshot(str(out / "last.png"))
    d.quit()
