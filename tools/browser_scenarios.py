"""User-facing creation and saved-download checks against a staged release."""

import hashlib
import json
import zipfile

from PIL import Image
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.support.ui import Select

from package_creation.tests.artifacts import fixture_root
from package_creation.tests.test_objects import glb, model_fixture


def exercise(check):
    d, wait, folder = check.d, check.wait, check.out
    fixtures = fixture_root()
    downloads = folder / "downloads"

    def el(name):
        return d.find_element(By.ID, name)

    def click(name):
        wait.until(lambda _: el(name).is_displayed() and el(name).is_enabled())
        element = el(name)
        d.execute_script('arguments[0].scrollIntoView({block:"center"})', element)
        element.click()

    def tab(name):
        d.find_element(By.CSS_SELECTOR, f'[data-tab="{name}"]').click()

    def text(name, value):
        wait.until(lambda _: el(name).is_displayed() and el(name).is_enabled())
        d.execute_script('arguments[0].scrollIntoView({block:"center"})', el(name))
        el(name).clear()
        el(name).send_keys(value)

    def current(kind):
        return check.runtime(
            "return (await r.store.listJobs()).filter(j=>j.kind==="
            + json.dumps(kind)
            + ").sort((a,b)=>b.updated-a.updated)[0];"
        )

    def completed(kind):
        def done(_):
            job = current(kind)
            if job and job["state"] in {"failed", "cancelled"}:
                raise AssertionError(job.get("error"))
            return job if job and job["state"] == "complete" else False

        return wait.until(done)

    def downloaded(job):
        path = downloads / job["output"]["filename"]
        wait.until(lambda _: path.exists() and path.stat().st_size == job["output"]["size"])
        return path

    d.set_window_size(1280, 1000)
    tab("texture")
    wait.until(lambda _: el("conversion-new").is_enabled())
    for body, name in [("am", "diagnostic"), ("af", "rgba16")]:
        click("conversion-new")
        d.find_element(By.CSS_SELECTOR, f'#converter-form input[value="{body}"]').click()
        el("converter-texture").send_keys(
            str(fixtures / f"artifacts/body-conversion/corpus/{name}.png")
        )
        wait.until(lambda _: el("convert-button").is_enabled())
        click("convert-button")
        if body == "af":
            wait.until(
                lambda _: el("conversion-cancel").is_displayed()
                and current("conversion").get("snapshotHash")
            )
            click("conversion-cancel")
            wait.until(lambda _: el("conversion-retry").is_displayed())
            cancelled = current("conversion")
            click("conversion-retry")
            wait.until(lambda _: current("conversion")["state"] in {"building", "complete"})
        job = completed("conversion")
        if body == "af":
            assert job["id"] == cancelled["id"]
            assert job["snapshotHash"] == cancelled["snapshotHash"]
        path = downloaded(job)
        expected = fixtures / f"artifacts/body-conversion/corpus/blender/{body}-{name}.png"
        assert (
            Image.open(path).convert("RGBA").tobytes()
            == Image.open(expected).convert("RGBA").tobytes()
        )
        wait.until(lambda _: d.find_elements(By.CSS_SELECTOR, "#converted-body-preview canvas"))
    check.check(
        "AM and AF convert through the form with exact decoded Blender pixels, preview and cancellation/retry"
    )

    tab("package")
    wait.until(lambda _: el("catalog-name").is_enabled())
    text("catalog-name", "Refactor Tattoo")
    d.find_element(By.CSS_SELECTOR, '#tattoo-list [data-field="label"]').send_keys("Diagnostic")
    d.find_element(By.CSS_SELECTOR, '#tattoo-list input[data-gender="am"]').send_keys(
        str(fixtures / "artifacts/wasm-migration/fixtures/am.png")
    )
    d.find_element(By.CSS_SELECTOR, '#tattoo-list input[data-gender="af"]').send_keys(
        str(fixtures / "artifacts/wasm-migration/fixtures/af.png")
    )
    wait.until(lambda _: el("build-button").is_enabled())
    click("build-button")
    tattoo = completed("tattoo")
    wait.until(lambda _: el("download-link").is_displayed())
    click("download-link")
    assert downloaded(tattoo).read_bytes().startswith(b"DBPF")
    wait.until(lambda _: d.find_elements(By.CSS_SELECTOR, "#tattoo-body-preview canvas"))
    check.check(
        "Mixed AM/AF tattoo form downloads a validated merged package and renders its preview"
    )

    tab("object")
    wait.until(lambda _: len(Select(el("object-standard")).options) == 5)
    model = folder / "model.glb"
    model.write_bytes(glb(*model_fixture()))
    el("object-model").send_keys(str(model))
    for field, value in [("creator", "Refactor"), ("name", "Object"), ("title", "Refactor object")]:
        text("object-" + field, value)
    click("object-preview")
    wait.until(lambda _: "Preview ready" in el("object-preview-status").text)
    if el("object-placement-warning").is_displayed():
        click("object-placement-ack")
    el("object-compression-options").find_element(By.TAG_NAME, "summary").click()
    Select(el("object-texture-encoder")).select_by_value("bodyshop")
    click("object-preview")
    wait.until(lambda _: "Preview ready" in el("object-preview-status").text)
    if (
        el("object-placement-warning").is_displayed()
        and not el("object-placement-ack").is_selected()
    ):
        click("object-placement-ack")
    click("object-refpack-compression")
    click("object-build")
    obj = completed("object")
    wait.until(lambda _: el("object-download").is_displayed())
    click("object-download")
    original = downloaded(obj).read_bytes()
    assert original.startswith(b"DBPF")
    assert obj["snapshot"]["parameters"]["refpack_compression"] is False
    assert obj["snapshot"]["parameters"]["texture_encoder"] == "bodyshop"
    assert obj["validation"]["object"]["package_compression"]["compressed_resources"] == 0
    check.check("Object form preserves sizing and downloads Body Shop output with RefPack disabled")
    d.refresh()
    tab("object")
    wait.until(lambda _: d.find_elements(By.CSS_SELECTOR, "#object-saved summary"))
    d.find_element(By.CSS_SELECTOR, "#object-saved summary").click()

    def open_saved(_):
        try:
            buttons = d.find_elements(By.CSS_SELECTOR, "#object-saved .saved-batch-row button")
            if not buttons:
                return False
            buttons[0].click()
            return True
        except StaleElementReferenceException:
            return False

    wait.until(open_saved)
    wait.until(lambda _: el("object-download").is_displayed())
    assert not el("object-name").is_enabled()
    assert current("object")["snapshotHash"] == obj["snapshotHash"]
    # Verify the saved bytes, without assuming a browser's duplicate-download suffix.
    same = check.runtime(
        "const j=await r.store.getJob("
        + json.dumps(obj["id"])
        + "); return await (await import("
        + json.dumps(check.prefix + "package-runtime/hashing.js")
        + ")).hashBlob(await r.store.getBlob(j.output.parts[0]));"
    )
    assert same == hashlib.sha256(original).hexdigest()
    check.check("Completed object reopens read-only after reload with identical downloaded bytes")

    tab("painting")
    wait.until(lambda _: len(d.find_elements(By.CSS_SELECTOR, ".painting-frame")) == 4)
    el("painting-image").send_keys(
        str(fixtures / "artifacts/body-conversion/corpus/diagnostic.png")
    )
    wait.until(
        lambda _: el("painting-editor").is_displayed() and el("painting-creator").is_enabled()
    )
    text("painting-creator", "Refactor")
    text("painting-title", "Painting")
    el("painting-crop-stage").send_keys(Keys.ARROW_RIGHT)
    wait.until(lambda _: el("painting-build").is_enabled())
    click("painting-build")
    painting = completed("painting")
    assert downloaded(painting).read_bytes().startswith(b"DBPF")
    wait.until(lambda _: d.find_elements(By.CSS_SELECTOR, "#painting-viewer canvas"))
    check.check(
        "Painting frame, keyboard crop and on-wall preview produce a direct package download"
    )

    tab("hair")
    wait.until(lambda _: el("hair-template-select").is_enabled())
    Select(el("hair-mesh-source")).select_by_value("upload")
    bundle = fixtures / "artifacts/hair-validation/embedded-bundle"
    el("hair-mesh-files").send_keys(str(bundle / "mesh_rosehair_0124.package"))
    el("hair-recolor-files").send_keys(str(bundle / "recolor_3555b7d0_rose72.package"))
    wait.until(lambda _: el("hair-export-section").is_displayed())
    Select(el("hair-selection")).select_by_value("1")
    text("hair-creator", "Refactor")
    text("hair-name", "Rose")
    click("hair-primary")
    wait.until(lambda _: el("hair-primary").text == "Create ZIP")
    click("hair-primary")
    hair = completed("hair")
    wait.until(lambda _: el("hair-download").is_displayed())
    click("hair-download")
    with zipfile.ZipFile(downloaded(hair)) as archive:
        assert "README.txt" in archive.namelist()
        meshes = [name for name in archive.namelist() if name.startswith("Meshes/")]
        assert len(meshes) == 1
        assert archive.read(meshes[0]) == (bundle / "mesh_rosehair_0124.package").read_bytes()
        assert (
            len(
                [
                    name
                    for name in archive.namelist()
                    if name.endswith(".package") and not name.startswith("Meshes/")
                ]
            )
            == 4
        )
    check.check(
        "Five-texture Rose bundle produces the four-color ZIP with an unchanged mesh dependency"
    )
    tab("sim")
    wait.until(lambda _: d.find_elements(By.CSS_SELECTOR, "#sim-viewer canvas"))
    m = check.runtime("return await r.manifest();")
    assert m["sims"]["downloads_enabled"] is False
    assert m["sims"]["everyday_test"]["bodies"] == ["am"]
    check.check(
        "Sim scene initializes with full exports gated and the existing AM Everyday experiment unchanged"
    )
    el("sim-model").send_keys(str(fixtures / "artifacts/sim-creator/guided/humanoid.glb"))
    wait.until(lambda _: "Review the suggested" in el("sim-status").text)
    click("sim-confirm-align")
    click("sim-confirm-markers")
    wait.until(
        lambda _: el("sim-confirm-head").is_displayed() and el("sim-confirm-head").is_enabled()
    )
    click("sim-confirm-head")
    click("sim-preview")
    wait.until(lambda _: "Fit ready" in el("sim-status").text)
    click("sim-confirm-check")
    text("sim-creator", "Refactor")
    text("sim-name", "Everyday")
    assert not el("sim-build").is_enabled()
    click("sim-experimental")
    wait.until(lambda _: el("sim-build").is_enabled())
    click("sim-build")
    sim = completed("sim")
    assert downloaded(sim).read_bytes().startswith(b"DBPF")
    assert "Stock head" in sim["validation"]["sim"]["scope"]
    check.check(
        "Reviewed guided AM fit downloads the existing Everyday experiment after explicit acknowledgement"
    )
    assert all(r["method"] in {"GET", "HEAD"} for r in check.requests)
    check.check("All six tools initialized and network capture contains no creation uploads")
