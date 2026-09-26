"""Test-only native service oracle retained during the WASM parity transition.

Production imports service.app.create_app, which exposes no server package creation.
This factory is only used by TestClient to exercise pre-migration worker, routing,
validation, cancellation and expiry behavior against the preserved native CLIs.
It has no entrypoint and is never imported by the website service.
"""

from __future__ import annotations

import convert as texture_convert
import server as texture_server
from package_creation.service import app as production


import asyncio
import json
import os
import re
import secrets
import subprocess
import threading
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.datastructures import UploadFile

from package_creation import harness
from package_creation.service.app import (
    ApiError,
    _check_api_security,
    _host_allowed,
    _parse_host,
)
from package_creation.tests.reference.native_build import (
    BuildManager,
    _parse_build_form,
    _read_upload,
    normalize_png,
    make_internal_job,
    _atomic_json,
)


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOT = PACKAGE_ROOT / "service/static"
JOB_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
ASSET_ID_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
MIB = 1024 * 1024
TERMINAL_STATES = {"complete", "failed", "cancelled"}
STATUS_LABEL = "Structural validation enabled"


@dataclass(frozen=True)
class ServiceConfig(production.ServiceConfig):
    conversion_handler: Callable | None = None
    blender_path: Path | None = None


def create_app(config: ServiceConfig | None = None) -> FastAPI:
    config = config or ServiceConfig()
    manager = BuildManager(config)
    conversion_lock = threading.Lock()
    conversion_runtime: tuple[Path, dict[str, texture_server.PresetConfig]] | None = None

    def run_conversion(upload: texture_server.Upload) -> tuple[str, bytes]:
        nonlocal conversion_runtime
        if config.conversion_handler is not None:
            return config.conversion_handler(upload)
        with conversion_lock:
            if conversion_runtime is None:
                blender = texture_convert.discover_blender(config.blender_path)
                presets = texture_server.validated_presets(
                    blender,
                    texture_server.AM_TEMPLATE,
                    texture_server.AM_PROFILE,
                    texture_server.AF_TEMPLATE,
                    texture_server.AF_PROFILE,
                )
                conversion_runtime = blender, presets
        blender, presets = conversion_runtime
        return texture_server.convert_upload(upload, blender, presets)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        manager.start()
        hair_manager.start()
        try:
            yield
        finally:
            hair_manager.stop()
            manager.stop()

    app = FastAPI(
        title="Sims 2 Creator Tools",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.manager = manager
    app.state.config = config
    from package_creation.hair.service import attach as attach_hair

    hair_manager = attach_hair(app, config, _check_api_security)
    app.state.hair_manager = hair_manager

    @app.exception_handler(ApiError)
    async def api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.middleware("http")
    async def host_and_headers(request: Request, call_next: Callable[[Request], Any]) -> Response:
        host = _parse_host(request.headers.get("host", ""))
        if not _host_allowed(host, config):
            return JSONResponse(
                status_code=400,
                content={"error": {"code": "invalid_host", "message": "Host is not allowed"}},
            )
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self' blob: data:; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; connect-src 'self'"
        )
        return response

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse((STATIC_ROOT / "index.html").read_text(encoding="utf-8"))

    @app.get("/static/app.js")
    async def javascript() -> FileResponse:
        return FileResponse(STATIC_ROOT / "app.js", media_type="text/javascript")

    @app.get("/static/style.css")
    async def stylesheet() -> FileResponse:
        return FileResponse(STATIC_ROOT / "style.css", media_type="text/css")

    @app.get("/static/{preview_file:path}")
    async def preview_javascript(preview_file: str) -> FileResponse:
        allowed = {
            "body-preview.mjs": "text/javascript",
            "preview-textures.mjs": "text/javascript",
            "vendor/three-preview.js": "text/javascript",
            "vendor/THREE-LICENSE.txt": "text/plain",
        }
        if preview_file not in allowed:
            raise ApiError(404, "not_found", "Static file not found")
        return FileResponse(STATIC_ROOT / preview_file, media_type=allowed[preview_file])

    @app.get("/api/v1/preview/bodies/{body}.glb")
    async def preview_body(body: str, request: Request) -> FileResponse:
        _check_api_security(request, config)
        if body not in {"am", "af"}:
            raise ApiError(404, "not_found", "Choose the male or female preview body")
        return FileResponse(
            STATIC_ROOT.parent / "preview_assets" / f"{body}.glb",
            media_type="model/gltf-binary",
        )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok"}

    @app.get("/ready")
    async def ready() -> JSONResponse:
        try:
            detail = manager.readiness()
        except Exception:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not_ready",
                    "message": "Builder or pinned templates are unavailable",
                },
            )
        return JSONResponse({"status": "ready", "integrity": detail})

    @app.get("/api/v1/capabilities")
    async def capabilities(request: Request) -> dict[str, Any]:
        _check_api_security(request, config)
        return {
            "schema_version": 1,
            "input": {"format": "PNG", "mode": "RGBA8", "width": 1024, "height": 1024},
            "ages": ["adult", "elder"],
            "age_mask": "0x18",
            "genders": ["am", "af"],
            "maximum_tattoos": config.max_tattoos,
            "maximum_files": config.max_files,
            "maximum_file_bytes": config.max_file_bytes,
            "normal_sims_only": True,
            "controller_model": {
                "catalog_name": harness.MULTI_CONTROLLER_CATALOG_NAME,
                "scene_name": harness.MULTI_CONTROLLER_MODEL,
                "required_product": harness.MULTI_CONTROLLER_REQUIRED_PRODUCT,
                "embedded": False,
            },
            "output": {
                "layout": "single-package",
                "format": "DBPF .package",
                "maximum_bytes": config.max_bundle_bytes,
            },
            "validation": STATUS_LABEL,
        }

    @app.post("/api/v1/convert")
    async def convert_texture(request: Request) -> Response:
        _check_api_security(request, config, modifying=True)
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > texture_server.MAX_REQUEST_BYTES:
                    raise ApiError(
                        413, "request_too_large", "Texture upload exceeds the 32 MiB limit"
                    )
            except ValueError as exc:
                raise ApiError(400, "invalid_request", "Content-Length is invalid") from exc
        try:
            form = await request.form(
                max_files=1, max_fields=1, max_part_size=texture_server.MAX_REQUEST_BYTES
            )
        except Exception as exc:
            raise ApiError(422, "invalid_multipart", "Texture upload could not be parsed") from exc
        try:
            if set(form.keys()) != {"preset", "texture"}:
                raise ApiError(
                    422, "invalid_conversion", "Choose one body type and one texture PNG"
                )
            presets = form.getlist("preset")
            textures = form.getlist("texture")
            if (
                len(presets) != 1
                or not isinstance(presets[0], str)
                or presets[0] not in {"am", "af"}
            ):
                raise ApiError(422, "invalid_conversion", "Choose male or female")
            if len(textures) != 1 or not isinstance(textures[0], UploadFile):
                raise ApiError(422, "invalid_conversion", "Choose one texture PNG")
            texture = textures[0]
            if texture.filename and len(texture.filename) > 512:
                raise ApiError(422, "invalid_conversion", "Texture filename is too long")
            data = await texture.read(texture_server.MAX_REQUEST_BYTES + 1)
            if not data:
                raise ApiError(422, "invalid_conversion", "The texture is empty")
            if len(data) > texture_server.MAX_REQUEST_BYTES:
                raise ApiError(413, "request_too_large", "Texture upload exceeds the 32 MiB limit")
            upload = texture_server.Upload(
                filename=texture.filename or "texture.png",
                preset=str(presets[0]),
                data=data,
            )
        finally:
            await form.close()
        try:
            filename, png = await asyncio.to_thread(run_conversion, upload)
        except texture_server.UploadError as exc:
            raise ApiError(422, "conversion_failed", str(exc)) from exc
        except texture_convert.ConversionError as exc:
            raise ApiError(
                503, "converter_unavailable", "Texture converter is unavailable"
            ) from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise ApiError(500, "conversion_failed", "Texture conversion failed") from exc
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.png", filename):
            raise ApiError(
                500, "conversion_failed", "Texture converter returned an invalid filename"
            )
        return Response(
            content=png,
            media_type="image/png",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.post("/api/v1/builds", status_code=202)
    async def create_build(request: Request) -> JSONResponse:
        _check_api_security(request, config, modifying=True)
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > config.max_request_bytes:
                    raise ApiError(413, "request_too_large", "Request exceeds the 128 MiB limit")
            except ValueError as exc:
                raise ApiError(400, "invalid_request", "Content-Length is invalid") from exc
        try:
            form = await request.form(
                max_files=config.max_files,
                max_fields=2,
                max_part_size=config.max_file_bytes,
            )
        except Exception as exc:
            raise ApiError(
                422, "invalid_multipart", "Multipart request could not be parsed"
            ) from exc
        try:
            spec, uploaded = _parse_build_form(form, config.max_tattoos, config.max_files)
        except Exception:
            await form.close()
            raise
        try:
            manager.readiness()
        except Exception as exc:
            await form.close()
            raise ApiError(503, "not_ready", "Builder or pinned templates are unavailable") from exc

        build_id = uuid.uuid4().hex
        job_dir = config.spool_root / build_id
        asset_dir = job_dir / "assets"
        job_dir.mkdir(parents=True, mode=0o700)
        os.chmod(job_dir, 0o700)
        asset_dir.mkdir(mode=0o700)
        warnings: list[str] = []
        reports: dict[str, Any] = {}
        asset_paths: dict[str, str] = {}
        total = [len(json.dumps(spec).encode("utf-8"))]
        try:
            for asset_id in sorted(uploaded):
                upload_path = asset_dir / f"{secrets.token_hex(16)}.upload"
                normalized_path = asset_dir / f"{secrets.token_hex(16)}.png"
                await _read_upload(
                    uploaded[asset_id],
                    upload_path,
                    config.max_file_bytes,
                    total,
                    config.max_request_bytes,
                )
                report, asset_warnings = normalize_png(
                    upload_path,
                    normalized_path,
                    maximum_bytes=config.max_file_bytes,
                )
                upload_path.unlink(missing_ok=True)
                asset_paths[asset_id] = normalized_path.relative_to(job_dir).as_posix()
                reports[asset_id] = report
                warnings.extend(f"{asset_id}: {warning}" for warning in asset_warnings)
            job = make_internal_job(spec, asset_paths)
            _atomic_json(job_dir / "request.json", spec)
            _atomic_json(job_dir / "job.json", job)
            _atomic_json(job_dir / "uploads.json", reports)
            response = manager.enqueue(build_id, warnings)
        except Exception:
            if job_dir.exists():
                manager._safe_remove(job_dir)
            raise
        finally:
            await form.close()
        return JSONResponse(status_code=202, content=response)

    @app.get("/api/v1/builds/{build_id}")
    async def get_build(build_id: str, request: Request) -> dict[str, Any]:
        _check_api_security(request, config)
        return manager.status(build_id)

    @app.get("/api/v1/builds/{build_id}/package")
    async def get_package(build_id: str, request: Request) -> FileResponse:
        _check_api_security(request, config)
        path, name = manager.package(build_id)
        return FileResponse(path, filename=name, media_type="application/octet-stream")

    @app.get("/api/v1/builds/{build_id}/bundle")
    async def get_bundle_compatibility_alias(build_id: str, request: Request) -> FileResponse:
        _check_api_security(request, config)
        path, name = manager.package(build_id)
        return FileResponse(path, filename=name, media_type="application/octet-stream")

    @app.post("/api/v1/builds/{build_id}/retry", status_code=202)
    async def retry_build(build_id: str, request: Request) -> JSONResponse:
        _check_api_security(request, config, modifying=True)
        return JSONResponse(status_code=202, content=manager.retry(build_id))

    @app.delete("/api/v1/builds/{build_id}", status_code=204)
    async def delete_build(build_id: str, request: Request) -> Response:
        _check_api_security(request, config, modifying=True)
        manager.delete(build_id)
        return Response(status_code=204)

    return app
