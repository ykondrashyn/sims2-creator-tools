"""Production browser-only APIs, pinned assets and legacy retention."""

import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from package_creation.service.app import create_app, ServiceConfig, parser
from package_creation.service.package_runtime import LegacyJobs, readiness, REMOVED


class PackageRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = ServiceConfig(
            spool_root=self.root,
            lan=True,
            allowed_hosts=("testserver",),
        )
        self.context = TestClient(
            create_app(self.config),
            headers={
                "Origin": "http://testserver",
            },
        )
        self.client = self.context.__enter__()

    def tearDown(self):
        self.context.__exit__(None, None, None)
        self.temp.cleanup()

    def test_manifest_assets_and_csp(self):
        r = self.client.get("/api/v1/package-runtime/manifest")
        self.assertEqual(r.status_code, 200)
        value = r.json()
        self.assertEqual(len(value["hair"]["palette"]), 43)
        self.assertEqual(len(value["hair"]["items"]), 9)
        for name in ["wasm", "glue", "worker", "archive", "palette", "game-meshes"]:
            asset = value["assets"][name]
            response = self.client.get(asset["url"])
            self.assertEqual(
                hashlib.sha256(response.content).hexdigest(), asset["sha256"]
            )
            self.assertEqual(
                response.headers["content-type"].split(";")[0], asset["mime"]
            )
        csp = r.headers["content-security-policy"]
        self.assertIn("'wasm-unsafe-eval'", csp)
        self.assertNotIn("'unsafe-eval'", csp)
        self.assertIn("worker-src 'self' blob:", csp)
        self.assertEqual(
            self.client.get("/ready").json()["integrity"]["package_creation"],
            "browser-wasm",
        )
        self.assertEqual(
            self.client.get("/api/v1/capabilities").json()["package_creation"][
                "execution"
            ],
            "browser-wasm",
        )

    def test_texture_encoder_capabilities_and_source(self):
        value = self.client.get("/api/v1/package-runtime/manifest").json()
        encoders = value.get("texture_encoders")
        self.assertIsNotNone(encoders, "Current-release tests require the current engine. Select an explicit runtime root.")
        self.assertEqual(encoders["default"], "directxtex")
        if encoders["version"] == 2:
            self.assertEqual(encoders["formats"]["bodyshop"], ["DXT1", "DXT3", "DXT5"])
            self.assertEqual(encoders["legacy"]["bodyshop_dxt3"]["DXT5"], "directxtex")
        else:
            self.assertEqual(encoders["formats"]["bodyshop_dxt3"], ["DXT3"])
        self.assertEqual(encoders, self.client.get("/api/v1/capabilities").json()["package_creation"]["texture_encoders"])
        self.assertEqual(self.client.get("/static/texture-compression.js").status_code, 200)
        self.assertIn("source", value["assets"])
        self.assertIn("licenses", value["assets"])

    def test_retired_posts_require_same_origin_and_do_not_read_inputs(self):
        before = list(self.root.rglob("*"))
        for path in [
            "/api/v1/builds",
            "/api/v1/builds/" + "a" * 32 + "/retry",
            "/api/v1/hair/templates",
            "/api/v1/hair/curves/parse",
            "/api/v1/hair/jobs",
            "/api/v1/hair/jobs/" + "a" * 32 + "/preview",
            "/api/v1/hair/jobs/" + "a" * 32 + "/build",
        ]:
            response = self.client.post(path, content=b"malformed private input")
            self.assertEqual(response.status_code, 410, (path, response.text))
            self.assertEqual(response.json()["error"]["message"], REMOVED)
            self.assertEqual(
                self.client.post(
                    path, headers={"Origin": "http://other.example"}
                ).status_code,
                403,
            )
        self.assertEqual(before, list(self.root.rglob("*")))
        self.assertIsInstance(self.client.app.state.manager, LegacyJobs)
        self.assertIsInstance(self.client.app.state.hair_manager, LegacyJobs)

    def test_retained_release_assets_are_available_with_original_mime(self):
        from package_creation.service import package_runtime as runtime
        from fastapi import FastAPI
        root = self.root / "runtime"
        root.mkdir()
        current = self.client.get("/api/v1/package-runtime/manifest").json()
        data = b"older browser engine"
        sha = hashlib.sha256(data).hexdigest()
        (root / sha).write_bytes(data)
        if current.get("ui"):
            import shutil
            from package_creation.service import package_runtime
            shutil.copytree(package_runtime.ASSET_ROOT / "ui", root / "ui")
            if current.get("page"):
                shutil.copyfile(package_runtime.ASSET_ROOT / "index.html", root / "index.html")
                if current["page"].get("encodings", {}).get("gzip"):
                    shutil.copyfile(package_runtime.ASSET_ROOT / "index.html.gz", root / "index.html.gz")
        (root / "manifest.json").write_text(json.dumps(current))
        previous = {**current, "release": "a" * 64, "assets": {
            "wasm": {"sha256": sha, "size": len(data), "mime": "application/wasm"}}}
        (root / ("manifest-" + previous["release"] + ".json")).write_text(json.dumps(previous))
        undeclared = "b" * 64
        (root / undeclared).write_bytes(b"not a declared asset")
        with patch.object(runtime, "ASSET_ROOT", root):
            app = FastAPI()
            runtime.attach(app, self.config, lambda *_: None)
            # Production installs this exception handler in create_app.
            from package_creation.service.app import ApiError
            from fastapi.responses import JSONResponse
            @app.exception_handler(ApiError)
            async def error(_request, exc):
                return JSONResponse(status_code=404, content={"error": "not found"})
            with TestClient(app) as client:
                response = client.get(f"/api/v1/package-runtime/assets/{sha}")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, data)
                self.assertEqual(response.headers["content-type"], "application/wasm")
                self.assertEqual(client.get(f"/api/v1/package-runtime/assets/{undeclared}").status_code, 404)

    def test_plain_lan_urls_and_origin_protection(self):
        for host in ["192.168.50.213:8001", "127.0.0.1:8001", "creator.local:8001"]:
            with self.subTest(host=host), TestClient(
                create_app(self.config), base_url=f"http://{host}"
            ) as client:
                self.assertEqual(client.get("/api/v1/capabilities").status_code, 200)
                self.assertEqual(client.get("/api/v1/hair/templates").status_code, 200)
                self.assertEqual(client.post("/api/v1/convert").status_code, 403)
                self.assertEqual(client.post(
                    "/api/v1/convert", headers={"Origin": "http://other.example"}
                ).status_code, 403)
                # Same-origin requests receive retirement guidance without reading input.
                self.assertEqual(client.post(
                    "/api/v1/convert", headers={"Origin": f"http://{host}"}
                ).status_code, 410)
                # Existing clients with stale credentials also work.
                self.assertEqual(client.get(
                    "/api/v1/capabilities", headers={"Authorization": "Bearer obsolete"}
                ).status_code, 200)
        self.assertEqual(self.client.get("/health", headers={"Host": "public.example"}).status_code, 400)
        from dataclasses import replace
        with TestClient(create_app(replace(self.config, lan=False))) as client:
            self.assertEqual(client.get("/health", headers={"Host": "192.168.50.213"}).status_code, 400)
        self.assertNotIn("--access-token", parser().format_help())

    def test_legacy_download_delete_and_original_expiry(self):
        for hair in [False, True]:
            with self.subTest(hair=hair):
                manager = LegacyJobs(self.config, hair)
                key = ("b" if hair else "c") * 32
                directory = manager.root / key
                directory.mkdir(parents=True)
                status = {
                    "id": key,
                    "state": "complete",
                    "updated": time.time(),
                    "expires_at": time.time() + 300,
                    "package_name": "Legacy.package",
                }
                (directory / "status.json").write_text(json.dumps(status))
                if hair:
                    (directory / "job.json").write_text(
                        json.dumps({"creator": "Legacy", "hair_name": "Hair"})
                    )
                    (directory / "recolors.zip").write_bytes(b"legacy-zip")
                else:
                    (directory / "output").mkdir()
                    (directory / "output/Legacy.package").write_bytes(b"legacy-package")
                base = f"/api/v1/hair/jobs/{key}" if hair else f"/api/v1/builds/{key}"
                suffix = "/download" if hair else "/package"
                self.assertEqual(self.client.get(base + suffix).status_code, 200)
                original = (directory / "status.json").read_bytes()
                self.client.get(base)
                self.assertEqual((directory / "status.json").read_bytes(), original)
                status.update(updated=0, expires_at=1)
                (directory / "status.json").write_text(json.dumps(status))
                self.assertEqual(self.client.get(base + suffix).status_code, 404)
                self.assertFalse(directory.exists())
        (self.root / "hair-library").mkdir()
        (self.root / "hair-library/saved.json").write_text("{}")
        LegacyJobs(self.config).expire()
        self.assertTrue((self.root / "hair-library/saved.json").exists())

    def test_converter_retired_without_upload_parsing_or_blender(self):
        from starlette.requests import Request
        with patch.object(Request, "form", side_effect=AssertionError("Upload must not be parsed")), patch("subprocess.run", side_effect=AssertionError("No build process")):
            r = self.client.post("/api/v1/convert", content=b"malformed private texture", headers={"Content-Type":"multipart/form-data"})
            self.assertEqual(r.status_code, 410, r.text)
            self.assertEqual(r.json()["error"]["code"], "browser_conversion_required")
        self.assertNotIn("--blender", parser().format_help())
        capabilities=self.client.get("/api/v1/capabilities").json()["body_conversion"]
        self.assertEqual(capabilities["execution"], "browser-wasm")
        self.assertFalse(capabilities["server_fallback"])
        for gender in ["am", "af"]:
            r = self.client.get(f"/api/v1/preview/bodies/{gender}.glb")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.content[:4], b"glTF")

    def test_static_runtime_allowlist_and_no_package_posts_in_ui(self):
        for name in [
            "app-wasm.js",
            "hair-wasm.js",
            "hair-curves.js",
            "texture-compression.js",
            "conversion-wasm.js",
            "painting-wasm.js",
            "painting-preview.mjs",
            "sim-wasm.js",
            "sim-preview.mjs",
            "package-runtime/client.mjs",
            "package-runtime/store.mjs",
            "package-runtime/zip.mjs",
        ]:
            self.assertEqual(self.client.get("/static/" + name).status_code, 200)
        self.assertEqual(self.client.get("/static/../app.py").status_code, 404)
        self.assertEqual(
            self.client.get("/static/package-runtime/unknown.mjs").status_code, 404
        )
        for name in ["app-wasm.js", "hair-wasm.js", "painting-wasm.js", "sim-wasm.js"]:
            script = self.client.get("/static/" + name).text
            self.assertNotIn('fetch("/api/v1/builds', script)
            self.assertNotIn('fetch("/api/v1/hair', script)
        page = self.client.get("/").text
        self.assertEqual(page.count('role="tab"'), 7)
        self.assertIn('data-tab="painting"', page)
        self.assertIn('data-tab="sim"', page)
        self.assertFalse(self.client.get('/api/v1/capabilities').json()['sim_creation']['downloads_enabled'])
        self.assertEqual(self.client.post('/api/v1/sims', content=b'input').status_code, 404)
        self.assertEqual(self.client.post('/api/v1/paintings', content=b'input').status_code, 404)
        self.assertNotIn("Body Shop", page.split('id="hair-tool"', 1)[1].split('id="object-tool"', 1)[0])
        self.assertNotIn("Reuse a previous upload", page)
        self.assertNotIn("access-panel", page)
        self.assertNotIn("Access link", page)
        for name in ["app.js", "package-runtime/client.mjs", "body-preview.mjs"]:
            script = self.client.get("/static/" + name).text
            self.assertNotIn("Authorization", script)
            self.assertNotIn("tattoo-package-service-token", script)
            self.assertNotIn("creator-tools-access-required", script)


if __name__ == "__main__":
    unittest.main()
