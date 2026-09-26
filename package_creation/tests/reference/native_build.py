#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Texture conversion, browser package assets and retiring server downloads."""

from __future__ import annotations

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
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any, BinaryIO

from starlette.datastructures import FormData, UploadFile

from package_creation import harness
from package_creation.service.app import ApiError, ServiceConfig


PACKAGE_ROOT = Path(__file__).resolve().parents[2]
STATIC_ROOT = PACKAGE_ROOT / "service/static"
JOB_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
ASSET_ID_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
MIB = 1024 * 1024
TERMINAL_STATES = {"complete", "failed", "cancelled"}
STATUS_LABEL = "Structural validation enabled"


def _atomic_json(path: Path, value: Any) -> None:
    harness.atomic_write_json(path, value)
    os.chmod(path, 0o600)


def _safe_text(value: Any, maximum: int, *, multiline: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError("must be text")
    value = unicodedata.normalize("NFC", value).strip()
    if not value or len(value) > maximum:
        raise ValueError(f"must contain 1 through {maximum} characters")
    for character in value:
        category = unicodedata.category(character)
        if category.startswith("C") and not (multiline and character in "\n\t"):
            raise ValueError("contains unsupported control characters")
    return value


def validate_public_spec(value: Any, *, max_tattoos: int = 20) -> tuple[dict[str, Any], set[str]]:
    if not isinstance(value, dict) or set(value) != {"schema_version", "bundle", "tattoos"}:
        raise ApiError(422, "invalid_spec", "The spec has an invalid top-level structure")
    if value["schema_version"] != 1:
        raise ApiError(422, "invalid_spec", "Only request schema version 1 is supported")
    bundle = value["bundle"]
    if not isinstance(bundle, dict) or set(bundle) != {
        "slug",
        "catalog_name",
        "catalog_description",
    }:
        raise ApiError(422, "invalid_spec", "The bundle object has an invalid structure")
    slug = bundle["slug"]
    if not isinstance(slug, str) or len(slug) > 48 or not ASSET_ID_RE.fullmatch(slug):
        raise ApiError(
            422,
            "invalid_spec",
            "Bundle slug must use lowercase ASCII letters, digits, and single hyphens",
        )
    try:
        catalog_name = _safe_text(bundle["catalog_name"], 120)
        catalog_description = _safe_text(bundle["catalog_description"], 1000, multiline=True)
    except ValueError as exc:
        raise ApiError(422, "invalid_spec", f"Invalid bundle text: {exc}") from exc

    tattoos = value["tattoos"]
    if not isinstance(tattoos, list) or not 1 <= len(tattoos) <= max_tattoos:
        raise ApiError(422, "invalid_spec", f"A build must contain 1 through {max_tattoos} tattoos")
    keys: set[str] = set()
    labels: set[str] = set()
    assets: set[str] = set()
    menu_orders: set[int] = set()
    layer_orders: set[int] = set()
    normalized_tattoos: list[dict[str, Any]] = []
    for index, tattoo in enumerate(tattoos):
        if not isinstance(tattoo, dict) or set(tattoo) != {
            "key",
            "menu_label",
            "menu_order",
            "layer_order",
            "assets",
        }:
            raise ApiError(422, "invalid_spec", f"Tattoo {index + 1} has an invalid structure")
        key = tattoo["key"]
        if not isinstance(key, str) or len(key) > 32 or not ASSET_ID_RE.fullmatch(key):
            raise ApiError(422, "invalid_spec", f"Tattoo {index + 1} has an invalid key")
        if key in keys:
            raise ApiError(422, "invalid_spec", f"Duplicate tattoo key: {key}")
        keys.add(key)
        try:
            label = _safe_text(tattoo["menu_label"], 64)
        except ValueError as exc:
            raise ApiError(422, "invalid_spec", f"Invalid menu label for {key}: {exc}") from exc
        if "/" in label or "\\" in label:
            raise ApiError(422, "invalid_spec", f"Menu label for {key} contains a menu separator")
        folded = label.casefold()
        if folded in labels:
            raise ApiError(422, "invalid_spec", f"Duplicate menu label: {label}")
        labels.add(folded)

        orders: list[int] = []
        for name, seen in (("menu_order", menu_orders), ("layer_order", layer_orders)):
            order = tattoo[name]
            if isinstance(order, bool) or not isinstance(order, int):
                raise ApiError(422, "invalid_spec", f"{name} for {key} must be an integer")
            orders.append(order)
            seen.add(order)

        asset_map = tattoo["assets"]
        if (
            not isinstance(asset_map, dict)
            or not asset_map
            or not set(asset_map).issubset({"am", "af"})
        ):
            raise ApiError(422, "invalid_spec", f"Tattoo {key} must have AM, AF, or both assets")
        normalized_assets: dict[str, str] = {}
        for gender, asset_id in asset_map.items():
            if (
                not isinstance(asset_id, str)
                or len(asset_id) > 48
                or not ASSET_ID_RE.fullmatch(asset_id)
            ):
                raise ApiError(
                    422, "invalid_spec", f"Tattoo {key} has an invalid {gender.upper()} asset ID"
                )
            if asset_id in assets:
                raise ApiError(
                    422, "invalid_spec", f"Asset ID is referenced more than once: {asset_id}"
                )
            assets.add(asset_id)
            normalized_assets[gender] = asset_id
        normalized_tattoos.append(
            {
                "key": key,
                "menu_label": label,
                "menu_order": orders[0],
                "layer_order": orders[1],
                "assets": normalized_assets,
            }
        )
    expected_orders = set(range(len(normalized_tattoos)))
    if menu_orders != expected_orders:
        raise ApiError(422, "invalid_spec", "Menu orders must be unique and contiguous from zero")
    if layer_orders != expected_orders:
        raise ApiError(422, "invalid_spec", "Layer orders must be unique and contiguous from zero")
    return {
        "schema_version": 1,
        "bundle": {
            "slug": slug,
            "catalog_name": catalog_name,
            "catalog_description": catalog_description,
        },
        "tattoos": normalized_tattoos,
    }, assets


def _identity_value(excluded: set[int]) -> int:
    return harness.random_id(excluded)


def make_internal_job(spec: dict[str, Any], asset_paths: dict[str, str]) -> dict[str, Any]:
    used: set[int] = set()
    box_guid = _identity_value(used)
    used.add(box_guid)
    tattoos: list[dict[str, Any]] = []
    for tattoo in spec["tattoos"]:
        group_id = _identity_value(used)
        used.add(group_id)
        input_pngs: dict[str, str | None] = {"am": None, "af": None}
        for gender, asset_id in tattoo["assets"].items():
            input_pngs[gender] = asset_paths[asset_id]
        tattoos.append(
            {
                "key": tattoo["key"],
                "menu_label": tattoo["menu_label"],
                "menu_order": tattoo["menu_order"],
                "layer_order": tattoo["layer_order"],
                "priority": 0x65 + tattoo["layer_order"],
                "input_pngs": input_pngs,
                "identity": {
                    "overlay_group_id": f"0x{group_id:08X}",
                    "family_uuid": str(uuid.uuid4()),
                },
            }
        )
    job = {
        "schema_version": 2,
        "slug": spec["bundle"]["slug"],
        "catalog_name": spec["bundle"]["catalog_name"],
        "catalog_description": spec["bundle"]["catalog_description"],
        "ages": ["adult", "elder"],
        "compatibility": {key: False for key in harness.MULTI_COMPATIBILITY_KEYS},
        "identity": {"box_guid": f"0x{box_guid:08X}"},
        "tattoos": tattoos,
    }
    harness.validate_multi_job(job)
    return job


def normalize_png(
    source: Path, destination: Path, *, maximum_bytes: int = 8 * MIB
) -> tuple[dict[str, Any], list[str]]:
    from PIL import Image, UnidentifiedImageError

    if source.stat().st_size > maximum_bytes:
        raise ApiError(422, "invalid_asset", "PNG exceeds the 8 MiB per-file limit")
    try:
        with Image.open(source) as image:
            if image.format != "PNG":
                raise ApiError(422, "invalid_asset", "Uploaded asset is not a PNG")
            if getattr(image, "n_frames", 1) != 1:
                raise ApiError(422, "invalid_asset", "Animated PNGs are not supported")
            if image.size != (1024, 1024):
                raise ApiError(422, "invalid_asset", "PNG must be exactly 1024 by 1024")
            if image.mode != "RGBA":
                raise ApiError(422, "invalid_asset", "PNG mode must be exactly RGBA8")
            image.load()
            clean = image.copy()
    except ApiError:
        raise
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        raise ApiError(422, "invalid_asset", "PNG could not be decoded safely") from exc

    alpha = clean.getchannel("A")
    alpha_min, alpha_max = alpha.getextrema()
    if alpha_max == 0:
        raise ApiError(422, "invalid_asset", "PNG alpha channel is blank")
    pixels = clean.get_flattened_data()
    visible_count = 0
    total_alpha = 0
    visible_rgb = False
    for red, green, blue, value in pixels:
        total_alpha += value
        if value:
            visible_count += 1
            if red or green or blue:
                visible_rgb = True
    if not visible_rgb:
        raise ApiError(422, "invalid_asset", "PNG has no visible RGB content inside its alpha mask")
    coverage = visible_count / (1024 * 1024)
    warnings: list[str] = []
    if alpha_min == 255:
        warnings.append("Texture is fully opaque")
    if coverage >= 0.75:
        warnings.append(f"Texture covers {coverage:.1%} of the body map")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = destination.with_name(f".{destination.name}.{secrets.token_hex(8)}.tmp")
    try:
        clean.save(temporary, format="PNG", optimize=True)
        os.chmod(temporary, 0o600)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "sha256": harness.sha256_path(destination),
        "bytes": destination.stat().st_size,
        "width": 1024,
        "height": 1024,
        "mode": "RGBA",
        "alpha_min": alpha_min,
        "alpha_max": alpha_max,
        "alpha_coverage": round(coverage, 6),
        "mean_alpha": round(total_alpha / (1024 * 1024), 3),
    }, warnings


def _directory_size(path: Path) -> int:
    total = 0
    for child in path.rglob("*"):
        if child.is_symlink():
            raise RuntimeError("job directory contains a symbolic link")
        if child.is_file():
            total += child.stat().st_size
    return total


def _drain_stream(stream: BinaryIO, limit: int, result: dict[str, Any], key: str) -> None:
    chunks: list[bytes] = []
    kept = 0
    truncated = False
    try:
        while True:
            chunk = stream.read(8192)
            if not chunk:
                break
            remaining = max(0, limit - kept)
            if kept < limit:
                portion = chunk[:remaining]
                chunks.append(portion)
                kept += len(portion)
            if len(chunk) > remaining:
                truncated = True
    finally:
        stream.close()
    result[key] = b"".join(chunks).decode("utf-8", errors="replace")
    result[f"{key}_truncated"] = truncated


class BuildManager:
    def __init__(self, config: ServiceConfig) -> None:
        self.config = config
        self.pending: queue.Queue[str] = queue.Queue(maxsize=config.queue_capacity)
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.processes: dict[str, subprocess.Popen[bytes]] = {}
        self.deleted: set[str] = set()
        self.worker = threading.Thread(
            target=self._worker_loop, name="tattoo-package-worker", daemon=True
        )

    def start(self) -> None:
        self.config.spool_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.config.spool_root, 0o700)
        self._cleanup_startup()
        if not self.worker.is_alive():
            self.worker.start()

    def stop(self) -> None:
        self.stop_event.set()
        with self.lock:
            processes = list(self.processes.values())
        for process in processes:
            self._terminate(process)
        if self.worker.is_alive():
            self.worker.join(timeout=5)

    def _job_dir(self, build_id: str) -> Path:
        if not JOB_ID_RE.fullmatch(build_id):
            raise ApiError(404, "not_found", "Build was not found")
        return self.config.spool_root / build_id

    def _safe_remove(self, path: Path) -> None:
        if path.parent.resolve() != self.config.spool_root.resolve() or not JOB_ID_RE.fullmatch(
            path.name
        ):
            raise RuntimeError("refusing unsafe job cleanup")
        if path.is_symlink():
            path.unlink(missing_ok=True)
        elif path.exists():
            shutil.rmtree(path)

    def _cleanup_startup(self) -> None:
        now = time.time()
        for path in self.config.spool_root.iterdir():
            if not path.is_dir() or path.is_symlink() or not JOB_ID_RE.fullmatch(path.name):
                continue
            try:
                status = json.loads((path / "status.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self._safe_remove(path)
                continue
            state = status.get("state")
            completed_value = status.get("completed_at")
            completed = float(completed_value) if isinstance(completed_value, (int, float)) else 0.0
            if (
                state not in TERMINAL_STATES
                or not completed
                or completed + self.config.retention_seconds <= now
            ):
                self._safe_remove(path)

    def _expire(self) -> None:
        now = time.time()
        for path in list(self.config.spool_root.iterdir()):
            if not path.is_dir() or path.is_symlink() or not JOB_ID_RE.fullmatch(path.name):
                continue
            try:
                status = json.loads((path / "status.json").read_text(encoding="utf-8"))
                expiry = float(status.get("expires_at", 0))
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                continue
            if status.get("state") in TERMINAL_STATES and expiry and expiry <= now:
                with self.lock:
                    if path.name not in self.processes:
                        self._safe_remove(path)

    def readiness(self) -> dict[str, Any]:
        check = self.config.readiness_check
        if check is not None:
            return check()
        reports = harness.verify_templates()
        builder = harness.builder_path()
        if not builder.is_file() or not os.access(builder, os.X_OK):
            raise harness.HarnessError("the prepared Rust builder is unavailable")
        return {
            "builder": "ready",
            "builder_sha256": harness.sha256_path(builder),
            "templates": {name: report["sha256"] for name, report in reports.items()},
        }

    def enqueue(self, build_id: str, warnings: list[str]) -> dict[str, Any]:
        status = {
            "id": build_id,
            "state": "queued",
            "progress": 25,
            "warnings": warnings,
            "validation_scope": "structural-only",
            "message": STATUS_LABEL,
            "created_at": time.time(),
            "completed_at": None,
            "expires_at": None,
            "error": None,
            "attempt": 1,
        }
        _atomic_json(self._job_dir(build_id) / "status.json", status)
        try:
            self.pending.put_nowait(build_id)
        except queue.Full as exc:
            self._safe_remove(self._job_dir(build_id))
            raise ApiError(503, "queue_full", "The four-build queue is full") from exc
        return self.public_status(status)

    def _load_status(self, build_id: str) -> dict[str, Any]:
        path = self._job_dir(build_id) / "status.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ApiError(404, "not_found", "Build was not found") from exc

    def _update(self, build_id: str, **changes: Any) -> dict[str, Any]:
        with self.lock:
            if build_id in self.deleted:
                raise ApiError(404, "not_found", "Build was not found")
            status = self._load_status(build_id)
            status.update(changes)
            _atomic_json(self._job_dir(build_id) / "status.json", status)
            return status

    def public_status(self, status: dict[str, Any]) -> dict[str, Any]:
        result = {
            key: status.get(key)
            for key in (
                "id",
                "state",
                "progress",
                "warnings",
                "validation_scope",
                "message",
                "created_at",
                "completed_at",
                "expires_at",
                "error",
                "validation",
                "attempt",
            )
        }
        if status.get("state") == "complete":
            result["package_url"] = f"/api/v1/builds/{status['id']}/package"
            result["bundle_url"] = f"/api/v1/builds/{status['id']}/bundle"
        return result

    def status(self, build_id: str) -> dict[str, Any]:
        self._expire()
        return self.public_status(self._load_status(build_id))

    def package(self, build_id: str) -> tuple[Path, str]:
        status = self._load_status(build_id)
        if status.get("state") != "complete":
            raise ApiError(409, "not_complete", "Build package is not ready")
        package_name = status.get("package_name")
        if not isinstance(package_name, str) or Path(package_name).name != package_name:
            raise ApiError(500, "invalid_output", "Completed build has an invalid package record")
        package = self._job_dir(build_id) / "output" / package_name
        if not package.is_file() or package.stat().st_size > self.config.max_bundle_bytes:
            raise ApiError(500, "invalid_output", "Completed build package is unavailable")
        return package, package_name

    def delete(self, build_id: str) -> None:
        path = self._job_dir(build_id)
        if not path.exists():
            raise ApiError(404, "not_found", "Build was not found")
        with self.lock:
            self.deleted.add(build_id)
            process = self.processes.get(build_id)
        if process is not None:
            self._terminate(process)
        with self.lock:
            self.processes.pop(build_id, None)
            self._safe_remove(path)

    def retry(self, build_id: str) -> dict[str, Any]:
        path = self._job_dir(build_id)
        with self.lock:
            if build_id in self.deleted or build_id in self.processes:
                raise ApiError(409, "not_retryable", "Build cannot be retried in its current state")
            status = self._load_status(build_id)
            if status.get("state") != "failed":
                raise ApiError(409, "not_retryable", "Only a failed accepted build can be retried")
            original = dict(status)
            shutil.rmtree(path / "output", ignore_errors=True)
            for child in path.glob(".output.tmp-*"):
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
            (path / "worker.log").unlink(missing_ok=True)
            status.update(
                state="queued",
                progress=25,
                error=None,
                validation=None,
                completed_at=None,
                expires_at=None,
                attempt=int(status.get("attempt", 1)) + 1,
            )
            _atomic_json(path / "status.json", status)
            try:
                self.pending.put_nowait(build_id)
            except queue.Full as exc:
                _atomic_json(path / "status.json", original)
                raise ApiError(503, "queue_full", "The four-build queue is full") from exc
        return self.public_status(status)

    def _worker_loop(self) -> None:
        while not self.stop_event.is_set():
            self._expire()
            try:
                build_id = self.pending.get(timeout=1)
            except queue.Empty:
                continue
            try:
                with self.lock:
                    if build_id in self.deleted:
                        continue
                self._run_build(build_id)
            finally:
                self.pending.task_done()

    def _run_build(self, build_id: str) -> None:
        try:
            self._update(build_id, state="building", progress=45)
            job_dir = self._job_dir(build_id)
            command = [
                sys.executable,
                str(PACKAGE_ROOT / "harness.py"),
                "build-merged",
                str(job_dir / "job.json"),
                "--output-dir",
                str(job_dir / "output"),
            ]
            allowed_environment = {
                key: value
                for key, value in os.environ.items()
                if key in {"PATH", "LANG", "LC_ALL", "TMPDIR"}
            }
            allowed_environment["PYTHONUTF8"] = "1"
            process = subprocess.Popen(
                command,
                cwd=PACKAGE_ROOT,
                env=allowed_environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                start_new_session=True,
            )
            with self.lock:
                if build_id in self.deleted:
                    self._terminate(process)
                    if process.stdout is not None:
                        process.stdout.close()
                    if process.stderr is not None:
                        process.stderr.close()
                    return
                self.processes[build_id] = process
            captured: dict[str, Any] = {}
            readers = [
                threading.Thread(
                    target=_drain_stream,
                    args=(process.stdout, self.config.max_log_bytes, captured, "stdout"),
                    daemon=True,
                ),
                threading.Thread(
                    target=_drain_stream,
                    args=(process.stderr, self.config.max_log_bytes, captured, "stderr"),
                    daemon=True,
                ),
            ]
            for reader in readers:
                reader.start()
            timed_out = False
            try:
                return_code = process.wait(timeout=self.config.build_timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                self._terminate(process)
                return_code = process.wait(timeout=5)
            finally:
                for reader in readers:
                    reader.join(timeout=5)
                with self.lock:
                    self.processes.pop(build_id, None)
            if build_id in self.deleted:
                return
            _atomic_json(job_dir / "worker.log", captured)
            if timed_out:
                self._fail(build_id, "build_timeout", "Package generation exceeded 120 seconds")
                return
            if return_code != 0:
                self._fail(
                    build_id, "builder_failed", "Package generation or structural validation failed"
                )
                return
            self._update(build_id, state="validating", progress=85)
            output = job_dir / "output"
            if _directory_size(job_dir) > self.config.max_job_bytes:
                shutil.rmtree(output, ignore_errors=True)
                self._fail(build_id, "job_too_large", "Generated job exceeded the 256 MiB limit")
                return
            job = harness.load_multi_job(job_dir / "job.json")
            package_name = f"{job['slug']}.package"
            package = output / package_name
            if not package.is_file() or package.stat().st_size > self.config.max_bundle_bytes:
                shutil.rmtree(output, ignore_errors=True)
                self._fail(
                    build_id, "package_too_large", "Generated package exceeded the 64 MiB limit"
                )
                return
            try:
                validation = json.loads((output / "validation.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                shutil.rmtree(output, ignore_errors=True)
                raise RuntimeError("harness did not publish a validation report") from exc
            if validation.get("status") != "pass" or validation.get("scope") != "structural-only":
                shutil.rmtree(output, ignore_errors=True)
                raise RuntimeError("harness validation report was not a structural pass")
            completed = time.time()
            self._update(
                build_id,
                state="complete",
                progress=100,
                package_name=package_name,
                validation=validation,
                completed_at=completed,
                expires_at=completed + self.config.retention_seconds,
            )
        except ApiError:
            return
        except Exception:
            try:
                self._fail(build_id, "internal_error", "Build failed safely")
            except ApiError:
                pass

    def _fail(self, build_id: str, code: str, message: str) -> None:
        completed = time.time()
        self._update(
            build_id,
            state="failed",
            progress=100,
            completed_at=completed,
            expires_at=completed + self.config.retention_seconds,
            error={"code": code, "message": message},
        )

    @staticmethod
    def _terminate(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=2)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


async def _read_upload(
    upload: UploadFile, path: Path, maximum: int, total: list[int], total_limit: int
) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.upload")
    written = 0
    try:
        with temporary.open("xb") as handle:
            os.chmod(temporary, 0o600)
            while True:
                chunk = await upload.read(64 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                total[0] += len(chunk)
                if written > maximum:
                    raise ApiError(422, "invalid_asset", "PNG exceeds the 8 MiB per-file limit")
                if total[0] > total_limit:
                    raise ApiError(
                        413, "request_too_large", "Uploaded files exceed the 128 MiB request limit"
                    )
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
        await upload.close()


def _parse_build_form(
    form: FormData, max_tattoos: int, max_files: int
) -> tuple[dict[str, Any], dict[str, UploadFile]]:
    items = list(form.multi_items())
    specs = [item for key, item in items if key == "spec"]
    if len(specs) != 1 or not isinstance(specs[0], str):
        raise ApiError(
            422, "invalid_spec", "Multipart request must contain exactly one text spec part"
        )
    try:
        raw_spec = json.loads(specs[0])
    except json.JSONDecodeError as exc:
        raise ApiError(422, "invalid_spec", "Spec is not valid JSON") from exc
    spec, referenced_assets = validate_public_spec(raw_spec, max_tattoos=max_tattoos)
    uploaded: dict[str, UploadFile] = {}
    for key, value in items:
        if key == "spec":
            continue
        if not isinstance(value, UploadFile):
            raise ApiError(422, "invalid_asset", "Every non-spec part must be a file")
        if key in uploaded:
            raise ApiError(422, "duplicate_asset", f"Asset was uploaded more than once: {key}")
        uploaded[key] = value
    uploaded_assets = set(uploaded)
    missing = sorted(referenced_assets - uploaded_assets)
    extra = sorted(uploaded_assets - referenced_assets)
    if missing:
        raise ApiError(422, "missing_asset", f"Referenced asset was not uploaded: {missing[0]}")
    if extra:
        raise ApiError(422, "unreferenced_asset", f"Uploaded asset is not referenced: {extra[0]}")
    if len(uploaded) > max_files:
        raise ApiError(422, "too_many_assets", f"At most {max_files} PNG files are allowed")
    return spec, uploaded
