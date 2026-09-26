"""Authenticated hair jobs with an independent one-worker queue."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import copy
import functools
import io
import json
import os
import queue
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import tempfile
import time
import uuid
from pathlib import Path

from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.datastructures import UploadFile

from .colors import ASSETS, TEMPLATE, TEMPLATE_SHA256, BASES, BY_NAME, public_palette
from .install import BUILDER, inspect
from .library import HairLibrary, sha256
from .standard import StandardCatalog
from .processing import fit_package_inputs, load_texture, render, settings, texture_settings, source_texture_path
from .custom_colors import MAX_BYTES, MAX_CUSTOM, JobColors, normalize_colors, normalize_curve, parse_gimp
from .worker import atomic_json, age_labels, disk_size, output_filename, archive_filename

MIB = 1024 * 1024
TERMINAL = {"complete", "failed", "cancelled"}
TEXTURE_EXPORT_REMOVED = "Texture export was removed. Start a new hair recolor package submission"


class HairError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def require_package_job(job):
    if job.get("output_kind") == "textures":
        raise HairError(TEXTURE_EXPORT_REMOVED, 410)


def safe_name(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,47}", value):
        raise HairError("Creator and Hair name must contain 1 to 48 letters, numbers, underscores or hyphens, starting with a letter or number")
    return value


@functools.lru_cache(maxsize=128)
def _current_inspection(package: Path, export: Path, fingerprint: tuple) -> dict:
    return inspect(package, export)


def current_inspection(package: Path, export: Path) -> dict:
    # Refresh derived inspection after a builder upgrade without changing saved
    # template identities or bytes. Return a copy before adding UI thumbnails.
    stat = package.stat()
    return copy.deepcopy(_current_inspection(package, export, (stat.st_mtime_ns, stat.st_size, BUILDER.stat().st_mtime_ns)))


def embedded_base(item: dict) -> str:
    if item.get("kind") == "custom":
        return "Arbitrary texture"
    if item.get("id") in {"default", "pooklet-mg-swirl"}:
        return "Primer"
    # Standard catalog templates already contain prepared Volatile textures.
    return "Volatile"


def readiness() -> dict:
    if not BUILDER.is_file() or not (ASSETS / "manifest.json").is_file() or not TEMPLATE.is_file() or hashlib.sha256(TEMPLATE.read_bytes()).hexdigest() != TEMPLATE_SHA256:
        raise HairError("Install the pinned hair template and presets with python -m package_creation.hair.install", 503)
    try:
        palette = public_palette()
        inspection = current_inspection(TEMPLATE, ASSETS / "template-textures")
        add_slot_previews(inspection, ASSETS / "template-textures")
    except (ValueError, OSError) as exc:
        raise HairError("Hair template or presets are unavailable. Run python -m package_creation.hair.install on the server", 503) from exc
    return {"id": "pooklet-mg-swirl", "label": "Swirl (Mansion & Garden)",
            "kind": "standard", "game_content": "Mansion & Garden", "template_credit": "Pooklet Mansion & Garden Swirl recolor template", "meshes": [], "example_filename": "Swirl_Volatile_Example.png", "example_label": "Download example texture",
            "requirements": "Requires Mansion & Garden or The Sims 2 Legacy Collection. Uses the built-in female Swirl mesh.",
            "ages": ["Young Adult", "Adult", "Elder"], "gender": "Female", "width": 512, "height": 512,
            "example_url": "/api/v1/hair/templates/default/example", "template_sha256": TEMPLATE_SHA256,
            "inspection": inspection,
            "palette": palette, "bases": BASES, "input_base": "Primer", "texture_source": "embedded",
            "package_export": {"max_dimension": 2048, "template_required": True, "embedded_textures": True},
            "limits": {"upload_bytes": 128 * MIB, "zip_bytes": 512 * MIB, "temporary_bytes": 1024 * MIB}}


class HairManager:
    def __init__(self, config):
        self.config = config
        self.root = config.spool_root / "hair"
        self.queue = queue.Queue(maxsize=4)
        self.lock = threading.RLock()
        self.job_locks = {}
        self.closed = threading.Event()
        self.thread = None
        self.process = None
        self.active = None

    def start(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        for directory in self.root.iterdir():
            path = directory / "status.json"
            if path.is_file():
                state = json.loads(path.read_text())
                job = json.loads((directory / "job.json").read_text())
                if job.get("output_kind") == "textures" and state["state"] != "complete":
                    # Preserve the existing expiry instead of extending retention at every restart.
                    atomic_json(path, {**state, "state": "failed", "message": TEXTURE_EXPORT_REMOVED})
                elif state["state"] in {"queued", "building"}:
                    self.update(directory, "failed", "Service restarted. Retry to reuse this job's identities")
        self.thread = threading.Thread(target=self.run, daemon=True, name="hair-builder")
        self.thread.start()

    def stop(self):
        self.closed.set()
        with self.lock:
            if self.process and self.process.poll() is None:
                os.killpg(self.process.pid, signal.SIGTERM)
        if self.thread:
            self.thread.join(timeout=5)

    def directory(self, job_id):
        if not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise HairError("Hair job not found", 404)
        directory = self.root / job_id
        if not (directory / "status.json").is_file():
            raise HairError("Hair job expired or was deleted", 404)
        return directory

    def update(self, directory, state, message):
        atomic_json(directory / "status.json", {"id": directory.name, "state": state, "message": message, "updated": time.time()})

    def status(self, job_id):
        with self.lock:
            directory = self.directory(job_id)
            state = json.loads((directory / "status.json").read_text())
            job = json.loads((directory / "job.json").read_text())
            progress = directory / "progress.json"
            return {**state, "expires_at": state["updated"] + self.config.retention_seconds,
                    "inspection": job["inspection"], "supported_ages": age_labels(job["inspection"]),
                    "template_label": job["template_label"], "requirements": job["requirements"],
                    "template_id": job.get("template_id"), "meshes": job.get("meshes", []),
                    "game_meshes": job.get("game_meshes", []),
                    "texture_source": job.get("texture_source", "uploaded"),
                    "mesh_filenames": ["Meshes/" + m["filename"] for m in job.get("meshes", [])],
                    "output_kind": job.get("output_kind", "packages"),
                    **({"output_format": job["output_format"]} if job.get("output_kind") == "textures" else {}),
                    "colors": job["colors"],
                    "custom_colors": job.get("custom_colors", []),
                    "filenames": [output_filename(job, c) for c in job["colors"]],
                    "zip_filename": archive_filename(job),
                    "progress": json.loads(progress.read_text()) if progress.exists() else None}

    def enqueue(self, job_id):
        with self.lock:
            directory = self.directory(job_id)
            state = json.loads((directory / "status.json").read_text())["state"]
            job = json.loads((directory / "job.json").read_text())
            require_package_job(job)
            if job_id == self.active:
                raise HairError("The cancelled process is still stopping. Retry in a moment", 409)
            if state in {"queued", "building", "complete"}:
                raise HairError("This job is already queued, building or complete", 409)
            if not job.get("prepared"):
                raise HairError("Prepare color previews before building")
            if self.queue.full():
                raise HairError("The hair queue has four waiting jobs. Try again shortly", 429)
            (directory / "progress.json").unlink(missing_ok=True)
            (directory / "recolors.zip").unlink(missing_ok=True)
            self.update(directory, "queued", "Waiting for the hair builder")
            self.queue.put_nowait(job_id)
            return self.status(job_id)

    def cancel(self, job_id, *, delete=False):
        with self.lock:
            directory = self.directory(job_id)
            state = json.loads((directory / "status.json").read_text())["state"]
            if state == "queued":
                with self.queue.mutex:
                    if job_id in self.queue.queue:
                        self.queue.queue.remove(job_id)
                        self.queue.unfinished_tasks -= 1
                        self.queue.not_full.notify()
            if job_id == self.active and self.process and self.process.poll() is None:
                os.killpg(self.process.pid, signal.SIGTERM)
            if state not in TERMINAL:
                self.update(directory, "cancelled", "Hair build cancelled")
            if delete and job_id != self.active:
                shutil.rmtree(directory)
                self.job_locks.pop(job_id, None)
                return {"id": job_id, "state": "deleted"}
            if delete:
                (directory / "delete-requested").touch()
            return self.status(job_id)

    def expire(self):
        with self.lock:
            for directory in list(self.root.iterdir()):
                if directory.name == self.active or not (directory / "status.json").is_file():
                    continue
                state = json.loads((directory / "status.json").read_text())
                if state["state"] not in {"queued", "building"} and time.time() - state["updated"] > self.config.retention_seconds:
                    lock = self.job_locks.get(directory.name)
                    if lock and lock.locked():
                        continue
                    shutil.rmtree(directory)
                    self.job_locks.pop(directory.name, None)

    def run(self):
        while not self.closed.is_set():
            self.expire()
            try:
                job_id = self.queue.get(timeout=1)
            except queue.Empty:
                continue
            try:
                with self.lock:
                    try:
                        directory = self.directory(job_id)
                    except HairError:
                        continue
                    if json.loads((directory / "status.json").read_text())["state"] != "queued":
                        continue
                    self.active = job_id
                    self.update(directory, "building", "Generating and validating recolor packages")
                    log = (directory / "build.log").open("wb")
                    self.process = subprocess.Popen([sys.executable, "-m", "package_creation.hair.worker", str(directory.resolve())],
                        cwd=Path(__file__).resolve().parents[2], stdout=log, stderr=log, start_new_session=True)
                deadline = time.monotonic() + self.config.hair_timeout_seconds
                problem = None
                while self.process.poll() is None:
                    if time.monotonic() > deadline or disk_size(directory) > self.config.hair_temporary_bytes:
                        problem = "Hair build exceeded its ten-minute timeout or 1 GiB temporary data limit"
                        os.killpg(self.process.pid, signal.SIGKILL)
                        break
                    self.closed.wait(0.15)
                self.process.wait(timeout=5)
                log.close()
                with self.lock:
                    state = json.loads((directory / "status.json").read_text())["state"]
                    if state != "cancelled":
                        if self.process.returncode == 0 and (directory / "recolors.zip").exists():
                            self.update(directory, "complete", "Every selected package passed structural validation")
                        else:
                            detail = (directory / "build.log").read_text(errors="replace")[-2000:]
                            self.update(directory, "failed", problem or detail or "Hair build interrupted. Retry this job")
                    if state == "cancelled" or self.process.returncode != 0:
                        (directory / "recolors.zip").unlink(missing_ok=True)
                        (directory / "recolors.partial").unlink(missing_ok=True)
                        shutil.rmtree(directory / "work", ignore_errors=True)
                    if (directory / "delete-requested").exists():
                        shutil.rmtree(directory)
            except Exception as exc:
                with self.lock:
                    if 'directory' in locals() and directory.exists():
                        self.update(directory, "failed", str(exc)[:1000])
            finally:
                with self.lock:
                    self.active = None
                    self.process = None
                self.queue.task_done()


def thumbnail(image) -> str:
    image = image.copy()
    image.thumbnail((160, 160))
    data = io.BytesIO()
    image.save(data, format="PNG")
    return "data:image/png;base64," + base64.b64encode(data.getvalue()).decode()


def add_slot_previews(inspection: dict, directory: Path) -> None:
    for slot in inspection["textures"]:
        slot["preview"] = thumbnail(load_texture(directory / f"{slot['id']}.png"))
        slot["supported_ages"] = age_labels({"ages": [{"age": age} for age in slot["ages"]]})
        slot["subsets"] = sorted({use["subset"] for use in slot.get("uses", [])})
        for use in slot.get("uses", []):
            use["supported_ages"] = age_labels({"ages": [use]})
    inspection["material_count"] = len({material for age in inspection["ages"] for material in age["materials"]})


def attach(app, config, security) -> HairManager:
    manager = HairManager(config)
    library = HairLibrary(config.spool_root / "hair-library")
    standard = StandardCatalog()

    def catalog_for(template_id):
        return standard if template_id.startswith("standard-") else library
    # Caps concurrent decompression and image processing without holding the event loop.
    processing_gate = asyncio.Semaphore(2)

    @app.exception_handler(HairError)
    async def error_handler(request, exc):
        return JSONResponse({"error": {"code": "hair_error", "message": str(exc)}}, status_code=exc.status)

    async def multipart(request, files=33, limit=None):
        # Count actual received bytes, including requests without Content-Length.
        original = request._receive
        received = 0
        async def bounded_receive():
            nonlocal received
            message = await original()
            received += len(message.get("body", b""))
            if received > (limit or config.hair_upload_bytes):
                from starlette.formparsers import MultiPartException
                raise MultiPartException("Hair upload exceeds the 128 MiB limit")
            return message
        request._receive = bounded_receive
        try:
            return await request.form(max_files=files, max_fields=8, max_part_size=64 * 1024)
        except HairError:
            raise
        except Exception as exc:
            if received > (limit or config.hair_upload_bytes):
                raise HairError("Curve upload exceeds 256 KiB" if limit else "Hair upload exceeds the 128 MiB limit", 413) from exc
            raise HairError("Invalid hair upload form") from exc

    async def save(upload, path):
        if not isinstance(upload, UploadFile):
            raise HairError("Choose a file for this upload")
        with path.open("xb") as output:
            while chunk := await upload.read(1024 * 1024):
                output.write(chunk)

    @app.post("/api/v1/hair/curves/parse")
    async def parse_custom_curve(request: Request):
        security(request, config, modifying=True)
        form = await multipart(request, files=1, limit=MAX_BYTES + 8192)
        try:
            if set(form) != {"file"} or len(form.getlist("file")) != 1 or not isinstance(form["file"], UploadFile):
                raise ValueError("Choose one GIMP curve file")
            upload = form["file"]
            data = await upload.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise HairError("Curve file exceeds 256 KiB", 413)
            curve, tables = normalize_curve(parse_gimp(data, upload.filename or ""))
            return {"curve": curve, "tables": tables}
        except ValueError as exc:
            raise HairError(str(exc), getattr(exc, "status", 422)) from exc
        finally:
            await form.close()

    @app.get("/api/v1/hair/templates")
    async def templates(request: Request):
        security(request, config)
        def listing():
            default = readiness()
            items = [*standard.items(), default, *library.items()]
            return {**default, "items": [{k: v for k, v in item.items() if k not in {"palette", "bases", "limits"}} for item in items]}
        return await asyncio.to_thread(listing)

    @app.post("/api/v1/hair/templates", status_code=201)
    async def import_template(request: Request):
        security(request, config, modifying=True)
        form = await multipart(request, files=64)
        try:
            if set(form) - {"file", "label"} or "file" not in form or len(form.getlist("label")) > 1:
                raise HairError("Upload mesh and matching recolor .package files with an optional name")
            label = form.get("label", "")
            if not isinstance(label, str):
                raise HairError("The hairstyle name must be text")
            with tempfile.TemporaryDirectory(prefix="hair-library-upload-") as temporary:
                sources = []
                for index, upload in enumerate(form.getlist("file")):
                    path = Path(temporary) / str(index)
                    await save(upload, path)
                    sources.append((path, upload.filename or ""))
                async with processing_gate:
                    items = await asyncio.to_thread(library.import_files, sources, label)
            return {"items": items}
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            raise HairError(str(exc)) from exc
        finally:
            await form.close()

    @app.get("/api/v1/hair/templates/{template_id}")
    async def template_details(template_id: str, request: Request):
        security(request, config)
        if template_id in {"default", "pooklet-mg-swirl"}:
            return await asyncio.to_thread(readiness)
        try:
            catalog = catalog_for(template_id)
            item = catalog.get(template_id)
            item["inspection"] = await asyncio.to_thread(current_inspection, catalog.directory(template_id) / "template.package", catalog.directory(template_id) / "template-textures")
            await asyncio.to_thread(add_slot_previews, item["inspection"], catalog.directory(template_id) / "template-textures")
            item.update(input_base=embedded_base(item), texture_source="embedded")
            return item
        except (ValueError, OSError) as exc:
            raise HairError(str(exc), 404) from exc

    @app.get("/api/v1/hair/templates/{template_id}/example")
    async def template_example(template_id: str, request: Request):
        security(request, config)
        if template_id in {"default", "pooklet-mg-swirl"}:
            readiness()
            return FileResponse(ASSETS / "Swirl_Volatile_Example.png", filename="Swirl_Volatile_Example.png")
        try:
            catalog = catalog_for(template_id)
            item = catalog.get(template_id)
            return FileResponse(catalog.directory(template_id) / item["example_filename"], filename=item["example_filename"])
        except ValueError as exc:
            raise HairError(str(exc), 404) from exc

    @app.post("/api/v1/hair/jobs", status_code=201)
    async def create(request: Request):
        security(request, config, modifying=True)
        default = readiness()
        form = await multipart(request, files=1)
        directory = None
        try:
            if set(form) - {"creator", "hair_name", "template", "template_id", "output_kind", "output_format"} or any(len(form.getlist(k)) != 1 for k in form):
                raise HairError("Supply Creator, Hair name and an optional matching recolor package")
            creator, hair_name = safe_name(form.get("creator")), safe_name(form.get("hair_name"))
            if form.get("output_kind", "packages") != "packages" or "output_format" in form:
                raise HairError(TEXTURE_EXPORT_REMOVED)
            custom = form.get("template")
            template_id = form.get("template_id", default["id"])
            if not isinstance(template_id, str):
                raise HairError("Select a hairstyle from the library")
            if custom and "template_id" in form:
                raise HairError("Choose a saved hairstyle or upload one recolor package")
            selected = default if template_id in {"default", default["id"]} else catalog_for(template_id).get(template_id)
            job_id = uuid.uuid4().hex
            directory = manager.root / job_id
            directory.mkdir(mode=0o700)
            if custom:
                await save(custom, directory / "template.package")
            elif selected["id"] != default["id"]:
                source = catalog_for(selected["id"]).directory(selected["id"])
                if sha256(source / "template.package") != selected["template_sha256"]:
                    raise HairError("The saved template changed. Import its source packages again")
                shutil.copyfile(source / "template.package", directory / "template.package")
                if selected["meshes"]:
                    shutil.copytree(source / "meshes", directory / "meshes")
            else:
                shutil.copyfile(TEMPLATE, directory / "template.package")
            async with processing_gate:
                inspection = await asyncio.to_thread(inspect, directory / "template.package", directory / "template-textures")
                await asyncio.to_thread(add_slot_previews, inspection, directory / "template-textures")
            for slot in inspection["textures"]:
                slot.update(source_width=slot["width"], source_height=slot["height"])
            if disk_size(directory) > config.hair_temporary_bytes:
                raise HairError("Extracted hair textures exceed the temporary data limit", 413)
            families = [str(uuid.uuid4()) for _ in range(4)]
            creator_uuid = str(uuid.uuid4())
            used = {int(t["id"].split("-")[1], 16) for t in inspection["textures"]}
            identities = {}
            for color, item in BY_NAME.items():
                group = 0x50000000 | secrets.randbits(28)
                while group in used:
                    group = 0x50000000 | secrets.randbits(28)
                used.add(group)
                identities[color] = {"group": group, "family": families[item["family"] - 1] if item["family"] else str(uuid.uuid4()),
                                     "hairtone": str(uuid.uuid4()), "creator_uuid": creator_uuid}
            job = {"creator": creator, "hair_name": hair_name, "inspection": inspection, "identities": identities,
                   "output_kind": "packages", "texture_source": "embedded",
                   "template_id": None if custom else selected["id"], "meshes": [] if custom else selected.get("meshes", []),
                   "game_meshes": [] if custom else selected.get("game_meshes", []),
                   "template_label": "Custom recolor: " + Path(custom.filename or "hair.package").name if custom else selected["label"],
                   "template_credit": Path(custom.filename or "hair.package").name if custom else selected.get("template_credit", selected.get("recolor_filename", selected["label"])),
                   "requirements": "Requires the original hairstyle's mesh and its game content. Consult the source creator's instructions." if custom else selected["requirements"],
                   "settings": settings({"base": "Arbitrary texture" if custom else embedded_base(selected)}),
                   "colors": list(BY_NAME), "assignments": {}, "masks": {}, "prepared": False,
                   "limits": {"zip_bytes": config.hair_zip_bytes, "temporary_bytes": config.hair_temporary_bytes},
                   "uploaded_bytes": (directory / "template.package").stat().st_size if custom else 0}
            atomic_json(directory / "job.json", job)
            manager.update(directory, "draft", "Embedded textures are ready. Review their bases and prepare previews")
            manager.job_locks[job_id] = asyncio.Lock()
            return manager.status(job_id)
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            if directory:
                shutil.rmtree(directory, ignore_errors=True)
            raise HairError(str(exc)) from exc
        finally:
            await form.close()

    @app.get("/api/v1/hair/jobs/{job_id}")
    async def status(job_id: str, request: Request):
        security(request, config)
        return manager.status(job_id)

    @app.post("/api/v1/hair/jobs/{job_id}/assets")
    async def assets(job_id: str, request: Request):
        security(request, config, modifying=True)
        directory = manager.directory(job_id)
        async with manager.job_locks.setdefault(job_id, asyncio.Lock()):
            job = json.loads((directory / "job.json").read_text())
            require_package_job(job)
            if job["assignments"] or job["prepared"] or manager.status(job_id)["state"] in {"queued", "building", "complete"}:
                raise HairError("Assets are already stored. Start a new submission to change uploaded files", 409)
            form = await multipart(request)
            upload_dir = directory / "uploads"
            upload_dir.mkdir(exist_ok=True)
            try:
                slots = {s["id"]: s for s in job["inspection"]["textures"]}
                required = set(slots)
                if not required.issubset(form) or set(form) - (required | {"mask:" + s for s in slots}):
                    raise HairError("Explicitly assign one PNG or BMP to every inspected texture slot")
                assignments, masks = {}, {}
                # Validate color textures before masks regardless of multipart order.
                for key in sorted(form, key=lambda k: k.startswith("mask:")):
                    if len(form.getlist(key)) != 1:
                        raise HairError("Texture assignments must be unique")
                    is_mask = key.startswith("mask:")
                    slot = slots[key.removeprefix("mask:")]
                    path = upload_dir / (secrets.token_hex(8) + ".texture")
                    await save(form[key], path)
                    async with processing_gate:
                        image = await asyncio.to_thread(load_texture, path, mask=is_mask)
                    if is_mask:
                        if image.size != (slot["source_width"], slot["source_height"]):
                            raise HairError("The recolor mask must match the uploaded texture dimensions")
                    else:
                        slot.update(source_width=image.width, source_height=image.height)
                    (masks if is_mask else assignments)[slot["id"]] = path.name
                if job["uploaded_bytes"] + disk_size(upload_dir) > config.hair_upload_bytes:
                    raise HairError("Combined hair uploads exceed 128 MiB", 413)
                # Compatibility for API clients that supply images before their
                # first preview. The website always uses the embedded source.
                job.update(assignments=assignments, masks=masks, texture_source="uploaded", settings=settings({}))
                atomic_json(directory / "job.json", job)
                manager.update(directory, "draft", "Assets stored. Preview the selected colors")
            except Exception as exc:
                shutil.rmtree(upload_dir, ignore_errors=True)
                if isinstance(exc, HairError):
                    raise
                raise HairError(str(exc)) from exc
            finally:
                await form.close()
            return manager.status(job_id)

    @app.post("/api/v1/hair/jobs/{job_id}/preview")
    async def preview(job_id: str, request: Request):
        security(request, config, modifying=True)
        directory = manager.directory(job_id)
        require_package_job(json.loads((directory / "job.json").read_text()))
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_BYTES:
                raise HairError("Preview settings exceed 256 KiB", 413)
        try:
            value = json.loads(body)
            if not isinstance(value, dict) or not {"settings", "colors"}.issubset(value) or set(value) - {"settings", "colors", "texture_settings", "custom_colors"}:
                raise ValueError("Supply preparation settings and selected colors")
            config_value = settings(value["settings"])
            colors = value["colors"]
            if not isinstance(colors, list) or not 1 <= len(colors) <= 43 + MAX_CUSTOM or any(not isinstance(c, str) for c in colors) or len(set(colors)) != len(colors):
                raise ValueError("Select 1 through 59 distinct colors")
        except (ValueError, TypeError) as exc:
            raise HairError(str(exc)) from exc
        async with manager.job_locks.setdefault(job_id, asyncio.Lock()):
            state = manager.status(job_id)["state"]
            if state in {"queued", "building", "complete"}:
                raise HairError("Start a new submission to change a queued or completed build", 409)
            job = json.loads((directory / "job.json").read_text())
            try:
                previous = [{k: c[k] for k in ("id", "name", "bin", "curve")} for c in job.get("custom_colors", [])]
                custom_colors = normalize_colors(value.get("custom_colors", previous))
                candidate = {**job, "custom_colors": custom_colors}
                palette = JobColors(candidate)
                if any(c not in palette.items for c in colors):
                    raise ValueError("Select colors from the built-in palette or this job's custom colors")
                if job.get("texture_source") == "embedded":
                    config_value = settings({**job["settings"], **value["settings"], "png_alpha": False})
                configs = texture_settings(value.get("texture_settings"), job["inspection"]["textures"], config_value)
                if job.get("texture_source") == "embedded":
                    for config_item in configs.values():
                        config_item["png_alpha"] = False
            except ValueError as exc:
                raise HairError(str(exc)) from exc
            if job.get("texture_source") != "embedded" and not job["assignments"]:
                raise HairError("Upload every texture assignment first")
            if any(palette.items[c]["kind"] == "grey" for c in colors) and not any(a["age"] & 16 for a in job["inspection"]["ages"]):
                raise HairError("This template has no elder age. Deselect Mail Bomb and Pipe Bomb")
            names = [output_filename(candidate, c) for c in colors]
            if len({n.casefold() for n in names}) != len(names):
                raise HairError("Selected package filenames collide")
            def process():
                previews = []
                for slot in job["inspection"]["textures"]:
                    key = slot["id"]
                    image = load_texture(source_texture_path(directory, job, key))
                    template = load_texture(directory / "template-textures" / f"{key}.png")
                    mask = load_texture(directory / "uploads" / job["masks"][key], mask=True) if key in job["masks"] else None
                    original = thumbnail(image)
                    image, mask = fit_package_inputs(image, template.size, mask)
                    targets = []
                    has_elders = any(age & 16 for age in slot["ages"])
                    elder_only = has_elders and all(age == 16 for age in slot["ages"])
                    elder_preview = (thumbnail(render(image, template, configs[key], "Mail Bomb", mask)[1])
                                     if has_elders and not elder_only and any(palette.items[c]["kind"] == "natural" for c in colors) else None)
                    for color in colors:
                        target_color = palette.elder(color, elder_only)
                        base, target = render(image, template, configs[key], target_color, mask, tables=palette.tables.get(target_color))
                        targets.append({"color": color, "rendered_color": palette.name(target_color), "image": thumbnail(target),
                                        "active": has_elders or palette.items[color]["kind"] != "grey",
                                        "elder_image": elder_preview if palette.items[color]["kind"] == "natural" else None})
                    previews.append({"slot": key, "settings": configs[key], "original": original, "base": thumbnail(base), "targets": targets})
                return previews
            try:
                async with processing_gate:
                    images = await asyncio.to_thread(process)
            except ValueError as exc:
                raise HairError(str(exc)) from exc
            identities = dict(job["identities"])
            used = {entry["group"] for entry in identities.values()} | {int(t["id"].split("-")[1], 16) for t in job["inspection"]["textures"]}
            creator_uuid = next(iter(identities.values()))["creator_uuid"]
            for color in custom_colors:
                if color["id"] not in identities:
                    group = 0x50000000 | secrets.randbits(28)
                    while group in used:
                        group = 0x50000000 | secrets.randbits(28)
                    used.add(group)
                    identities[color["id"]] = {"group": group, "family": str(uuid.uuid4()), "hairtone": str(uuid.uuid4()), "creator_uuid": creator_uuid}
            job.update(settings=config_value, texture_settings=configs, colors=colors, prepared=True,
                       custom_colors=custom_colors, identities=identities)
            atomic_json(directory / "job.json", job)
            manager.update(directory, "ready", "Preview ready. Review filenames and generate the ZIP")
            return {"previews": images, **manager.status(job_id)}

    @app.post("/api/v1/hair/jobs/{job_id}/build", status_code=202)
    async def build(job_id: str, request: Request):
        security(request, config, modifying=True)
        async with manager.job_locks.setdefault(job_id, asyncio.Lock()):
            return manager.enqueue(job_id)

    @app.post("/api/v1/hair/jobs/{job_id}/cancel")
    async def cancel(job_id: str, request: Request):
        security(request, config, modifying=True)
        async with manager.job_locks.setdefault(job_id, asyncio.Lock()):
            return manager.cancel(job_id)

    @app.delete("/api/v1/hair/jobs/{job_id}")
    async def delete(job_id: str, request: Request):
        security(request, config, modifying=True)
        async with manager.job_locks.setdefault(job_id, asyncio.Lock()):
            return manager.cancel(job_id, delete=True)

    @app.get("/api/v1/hair/jobs/{job_id}/download")
    async def download(job_id: str, request: Request):
        security(request, config)
        status = manager.status(job_id)
        if status["state"] != "complete":
            raise HairError("The archive is not ready for download", 409)
        return FileResponse(manager.directory(job_id) / "recolors.zip", filename=status["zip_filename"], media_type="application/zip")

    @app.get("/static/hair-curves.js")
    async def curve_javascript():
        return FileResponse(Path(__file__).resolve().parents[1] / "service/static/hair-curves.js", media_type="text/javascript")

    @app.get("/static/hair.js")
    async def script():
        return FileResponse(Path(__file__).resolve().parents[1] / "service/static/hair.js", media_type="text/javascript")

    return manager
