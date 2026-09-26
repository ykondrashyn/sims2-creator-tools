#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Prepare, build, and structurally validate Sims 2 body-overlay bundles."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import secrets
import shutil
import struct
import subprocess
import sys
import tempfile
import unicodedata
import urllib.request
import uuid
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any



ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
TEMPLATES = Path(os.environ.get("PROJECT_FIXTURE_ROOT", REPO_ROOT)) / "package_creation/templates"
TOOLS = ROOT / ".tools"
BUILDER = ROOT / "builder"
DEFAULT_OUTPUT = ROOT / "output"
MULTI_MAX_TATTOOS = 20
MULTI_AGE_MASK = 0x18
MULTI_CONTROLLER_MODEL = "sculptureOshoKoma"
MULTI_CONTROLLER_CATALOG_NAME = "Osho Nuff Tablet"
MULTI_CONTROLLER_REQUIRED_PRODUCT = "Mansion & Garden Stuff or The Sims 2 Legacy Collection"
MULTI_COMPATIBILITY_KEYS = (
    "plantsim",
    "vampire",
    "werewolf",
    "zombie",
    "servo",
    "bigfoot",
)
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)

ARCHIVE_URL = (
    "https://www.picknmixmods.com/Sims2/Notes/ScriptedOverlayBoxes/"
    "MultiOverlaysWithBox_V1.zip"
)
ARCHIVE_SHA256 = "b8f4134d1cf98d45c5a08e6d21a4bd529534f73d96ae6cf673e7a60242865111"
RUST_TOOLCHAIN = "1.89.0"
DBPF_REVISION = "99b167f80781f7a3862dcd1edf9ce6ca169ac087"

TEMPLATE_SPECS = {
    "Template_MultiOverlay.package": {
        "member": "MultiOverlaysWithBox/Template/Template_MultiOverlay.package",
        "sha256": "99420d4db856892c4f8a4332a34c0324744d99df3eaee453fcf05b40798fa482",
        "logical_tgi_sha256": "61fcc04662d06320af04aac8d5d7b120d3d5f56b9e0301b41e3582d70a96c1cb",
        "entries": 48,
    },
    "Template_OverlayBox.package": {
        "member": "MultiOverlaysWithBox/Template/Template_OverlayBox.package",
        "sha256": "10ffec6d0b9e148110b33a48791ad3222d6e4a2df862cc0e98e0ca9ffe1d6488",
        "logical_tgi_sha256": "c33cb91ae9d77c7ed1ca423e09c257565aee4604b72e81cf70a19b629eeffd3f",
        "entries": 42,
    },
    "NoFaceOverlay.package": {
        "member": "MultiOverlaysWithBox/Template/NoFaceOverlay.package",
        "sha256": "0e401a47925a282b5e0c6830fc9e02dd897c52570a88a8f0781f6643a3cea9bb",
        "logical_tgi_sha256": "447de4f1dd97b8b579526b0f4e022e087b367977d874aeb47ad8042b8356b11d",
        "entries": 2,
    },
}

TYPE_NAMES = {
    0x0C560F39: "BINX",
    0x1C4A276C: "TXTR",
    0x2C1FD8A1: "XTOL",
    0x42434F4E: "BCON",
    0x42484156: "BHAV",
    0x43545353: "CTSS",
    0x49596978: "TXMT",
    0x4F424A44: "OBJD",
    0x4F424A66: "OBJF",
    0x534C4F54: "SLOT",
    0x53545223: "STR",
    0x54505250: "TPRP",
    0x5452434E: "TRCN",
    0x54544142: "TTAB",
    0x54544173: "TTAS",
    0x6C4F359D: "COLL",
    0x7BA3838C: "GMND",
    0xAC4F8687: "GMDC",
    0xAC506764: "3IDR",
    0xE519C933: "CRES",
    0xE86B1EEF: "DIR",
    0xEBCF3E27: "GZPS",
    0xFC6EB1F7: "SHPE",
}

RESERVED_IDS = {
    0,
    0xFFFFFFFF,
    0x1C050000,
    0x4F184AA9,
    0x5F2D415B,
    0x5F99DAE1,
}
SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
HEX_ID_RE = re.compile(r"0x[0-9A-Fa-f]{8}\Z")


class HarnessError(RuntimeError):
    """A user-correctable harness failure."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
        temp = Path(handle.name)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    temp.replace(path)


def atomic_write_json(path: Path, value: Any) -> None:
    data = (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    atomic_write_bytes(path, data)


def run_checked(command: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            shell=False,
            env=env,
        )
    except FileNotFoundError as exc:
        raise HarnessError(f"required executable was not found: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "command failed").strip()
        raise HarnessError(f"command failed: {detail}") from exc


def load_job(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HarnessError(f"cannot read job {path}: {exc}") from exc
    validate_job(value)
    return value


def validate_job(job: Any) -> None:
    if not isinstance(job, dict):
        raise HarnessError("job must be a JSON object")
    required = {
        "schema_version",
        "slug",
        "catalog_name",
        "catalog_description",
        "preset",
        "ages",
        "priority",
        "input_png",
        "identity",
    }
    if set(job) != required:
        raise HarnessError(f"job keys must be exactly: {', '.join(sorted(required))}")
    if job["schema_version"] != 1:
        raise HarnessError("unsupported job schema_version, expected 1")
    if not isinstance(job["slug"], str) or not SLUG_RE.fullmatch(job["slug"]):
        raise HarnessError("slug must contain lowercase ASCII letters, digits, and single hyphens")
    if len(job["slug"]) > 48:
        raise HarnessError("slug must be at most 48 characters")
    for key, maximum in (("catalog_name", 120), ("catalog_description", 1000)):
        value = job[key]
        if not isinstance(value, str) or not value.strip() or "\x00" in value or len(value) > maximum:
            raise HarnessError(f"{key} must be nonempty text of at most {maximum} characters")
    if job["preset"] not in {"am", "af"}:
        raise HarnessError("preset must be am or af")
    if job["ages"] != ["adult"]:
        raise HarnessError('ages must be exactly ["adult"]')
    if not isinstance(job["priority"], int) or isinstance(job["priority"], bool) or not 1 <= job["priority"] <= 0x7FFFFFFF:
        raise HarnessError("priority must be an integer from 1 through 2147483647")
    if not isinstance(job["input_png"], str) or not job["input_png"]:
        raise HarnessError("input_png must be a nonempty path string")
    identity = job["identity"]
    if not isinstance(identity, dict) or set(identity) != {"box_guid", "overlay_group_id", "family_uuid"}:
        raise HarnessError("identity must contain exactly box_guid, overlay_group_id, and family_uuid")
    values = []
    for field in ("box_guid", "overlay_group_id"):
        text = identity[field]
        if not isinstance(text, str) or not HEX_ID_RE.fullmatch(text):
            raise HarnessError(f"{field} must be 0x plus eight hexadecimal digits")
        value = int(text[2:], 16)
        if value in RESERVED_IDS:
            raise HarnessError(f"{field} uses a zero or reserved identifier")
        values.append(value)
    if len(set(values)) != len(values):
        raise HarnessError("box_guid and overlay_group_id must differ")
    try:
        family = uuid.UUID(identity["family_uuid"])
    except (AttributeError, TypeError, ValueError) as exc:
        raise HarnessError("family_uuid is malformed") from exc
    if family.int == 0:
        raise HarnessError("family_uuid must not be zero")


def load_multi_job(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HarnessError(f"cannot read multi-job {path}: {exc}") from exc
    validate_multi_job(value)
    return value


def _valid_text(value: Any, maximum: int, *, allow_newlines: bool = True) -> bool:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        return False
    for character in value:
        is_control = unicodedata.category(character).startswith("C")
        if character == "\x00" or (is_control and (not allow_newlines or character not in "\n\t")):
            return False
    return True


def _parse_identity_id(value: Any, field: str) -> int:
    if not isinstance(value, str) or not HEX_ID_RE.fullmatch(value):
        raise HarnessError(f"{field} must be 0x plus eight hexadecimal digits")
    parsed = int(value[2:], 16)
    if parsed in RESERVED_IDS:
        raise HarnessError(f"{field} uses a zero or reserved identifier")
    return parsed


def _validate_family(value: Any) -> str:
    try:
        family = uuid.UUID(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise HarnessError("family_uuid is malformed") from exc
    if family.int == 0:
        raise HarnessError("family_uuid must not be zero")
    return str(family)


def validate_multi_job(job: Any) -> None:
    if not isinstance(job, dict):
        raise HarnessError("multi-job must be a JSON object")
    required = {
        "schema_version",
        "slug",
        "catalog_name",
        "catalog_description",
        "ages",
        "compatibility",
        "tattoos",
        "identity",
    }
    if set(job) != required:
        raise HarnessError(f"multi-job keys must be exactly: {', '.join(sorted(required))}")
    if job["schema_version"] != 2:
        raise HarnessError("unsupported multi-job schema_version, expected 2")
    if not isinstance(job["slug"], str) or not SLUG_RE.fullmatch(job["slug"]) or len(job["slug"]) > 48:
        raise HarnessError("slug must be at most 48 lowercase ASCII letters, digits, and single hyphens")
    if not _valid_text(job["catalog_name"], 120, allow_newlines=False):
        raise HarnessError("catalog_name must be nonempty text of at most 120 characters")
    if not _valid_text(job["catalog_description"], 1000):
        raise HarnessError("catalog_description must be nonempty text of at most 1000 characters")
    if job["ages"] != ["adult", "elder"]:
        raise HarnessError('ages must be exactly ["adult", "elder"]')
    compatibility = job["compatibility"]
    if not isinstance(compatibility, dict) or tuple(sorted(compatibility)) != tuple(sorted(MULTI_COMPATIBILITY_KEYS)):
        raise HarnessError("compatibility must contain exactly the six supported supernatural keys")
    if any(compatibility[key] is not False for key in MULTI_COMPATIBILITY_KEYS):
        raise HarnessError("all supernatural compatibility values must be false")
    identity = job["identity"]
    if not isinstance(identity, dict) or set(identity) != {"box_guid"}:
        raise HarnessError("multi-job identity must contain exactly box_guid")
    used_ids = {_parse_identity_id(identity["box_guid"], "box_guid")}
    used_families: set[str] = set()

    tattoos = job["tattoos"]
    if not isinstance(tattoos, list) or not 1 <= len(tattoos) <= MULTI_MAX_TATTOOS:
        raise HarnessError(f"tattoos must contain 1 through {MULTI_MAX_TATTOOS} entries")
    menu_orders: set[int] = set()
    layer_orders: set[int] = set()
    keys: set[str] = set()
    labels: set[str] = set()
    for tattoo in tattoos:
        expected = {
            "key",
            "menu_label",
            "menu_order",
            "layer_order",
            "priority",
            "input_pngs",
            "identity",
        }
        if not isinstance(tattoo, dict) or set(tattoo) != expected:
            raise HarnessError("each tattoo has an invalid key set")
        key = tattoo["key"]
        if not isinstance(key, str) or not SLUG_RE.fullmatch(key) or len(key) > 32:
            raise HarnessError("tattoo key must be at most 32 lowercase ASCII letters, digits, and single hyphens")
        if key in keys:
            raise HarnessError(f"duplicate tattoo key: {key}")
        keys.add(key)
        label = tattoo["menu_label"]
        if (
            not _valid_text(label, 64, allow_newlines=False)
            or "/" in label
            or "\\" in label
        ):
            raise HarnessError("menu_label is invalid or contains a menu separator")
        folded_label = label.casefold()
        if folded_label in labels:
            raise HarnessError(f"duplicate tattoo menu label: {label}")
        labels.add(folded_label)
        for order_name, target in (("menu_order", menu_orders), ("layer_order", layer_orders)):
            value = tattoo[order_name]
            if not isinstance(value, int) or isinstance(value, bool):
                raise HarnessError(f"{order_name} must be an integer")
            target.add(value)
        if tattoo["priority"] != 0x65 + tattoo["layer_order"]:
            raise HarnessError("priority must be 0x65 plus layer_order")
        inputs = tattoo["input_pngs"]
        if not isinstance(inputs, dict) or set(inputs) != {"am", "af"}:
            raise HarnessError("input_pngs must contain exactly am and af")
        if inputs["am"] is None and inputs["af"] is None:
            raise HarnessError("each tattoo must provide AM, AF, or both")
        for preset in ("am", "af"):
            value = inputs[preset]
            if value is None:
                continue
            if not isinstance(value, str) or not value or Path(value).is_absolute() or ".." in Path(value).parts:
                raise HarnessError(f"{preset} input path must be a safe relative path")
        tattoo_identity = tattoo["identity"]
        if not isinstance(tattoo_identity, dict) or set(tattoo_identity) != {"overlay_group_id", "family_uuid"}:
            raise HarnessError("tattoo identity must contain overlay_group_id and family_uuid")
        group = _parse_identity_id(tattoo_identity["overlay_group_id"], "overlay_group_id")
        if group in used_ids:
            raise HarnessError("box and overlay identifiers must be unique")
        used_ids.add(group)
        family = _validate_family(tattoo_identity["family_uuid"])
        if family in used_families:
            raise HarnessError("family UUIDs must be unique")
        used_families.add(family)

    expected_orders = set(range(len(tattoos)))
    if menu_orders != expected_orders:
        raise HarnessError("menu_order values must be unique and contiguous from zero")
    if layer_orders != expected_orders:
        raise HarnessError("layer_order values must be unique and contiguous from zero")


def resolve_multi_inputs(job_path: Path, job: dict[str, Any]) -> dict[str, dict[str, Path]]:
    root = job_path.resolve().parent
    resolved: dict[str, dict[str, Path]] = {}
    for tattoo in job["tattoos"]:
        tattoo_paths: dict[str, Path] = {}
        for preset in ("am", "af"):
            value = tattoo["input_pngs"][preset]
            if value is None:
                continue
            path = (root / value).resolve()
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise HarnessError(f"{tattoo['key']} {preset} input escapes the job directory") from exc
            tattoo_paths[preset] = path
        resolved[tattoo["key"]] = tattoo_paths
    return resolved


def resolve_input(job_path: Path, job: dict[str, Any]) -> Path:
    path = Path(job["input_png"])
    if not path.is_absolute():
        path = job_path.parent / path
    return path.resolve()


def validate_png(path: Path) -> dict[str, Any]:
    from PIL import Image, UnidentifiedImageError
    try:
        with Image.open(path) as image:
            image.load()
            if image.format != "PNG":
                raise HarnessError("input image must be a PNG")
            if image.size != (1024, 1024):
                raise HarnessError("input PNG must be exactly 1024 by 1024")
            if image.mode != "RGBA":
                raise HarnessError("input PNG mode must be exactly RGBA")
            alpha_min, alpha_max = image.getchannel("A").getextrema()
    except HarnessError:
        raise
    except (OSError, UnidentifiedImageError) as exc:
        raise HarnessError(f"cannot decode input PNG {path}: {exc}") from exc
    if alpha_max == 0:
        raise HarnessError("input PNG alpha channel is blank")
    return {
        "sha256": sha256_path(path),
        "width": 1024,
        "height": 1024,
        "mode": "RGBA",
        "alpha_min": alpha_min,
        "alpha_max": alpha_max,
    }


def random_id(excluded: set[int]) -> int:
    while True:
        value = secrets.randbits(32)
        if value not in RESERVED_IDS and value not in excluded:
            return value


def create_job(args: argparse.Namespace) -> None:
    path = args.job.resolve()
    if path.exists():
        raise HarnessError(f"refusing to overwrite existing job: {path}")
    box_guid = random_id(set())
    group_id = random_id({box_guid})
    input_path = args.input_png.resolve()
    relative_input = os.path.relpath(input_path, path.parent)
    job = {
        "schema_version": 1,
        "slug": args.slug,
        "catalog_name": args.catalog_name,
        "catalog_description": args.catalog_description,
        "preset": args.preset,
        "ages": ["adult"],
        "priority": args.priority,
        "input_png": relative_input,
        "identity": {
            "box_guid": f"0x{box_guid:08X}",
            "overlay_group_id": f"0x{group_id:08X}",
            "family_uuid": str(uuid.uuid4()),
        },
    }
    validate_job(job)
    validate_png(input_path)
    atomic_write_json(path, job)
    print(path)


def assert_hash(path: Path, expected: str, label: str) -> None:
    if not path.is_file():
        raise HarnessError(f"missing {label}: {path}")
    actual = sha256_path(path)
    if actual != expected:
        raise HarnessError(f"{label} SHA-256 mismatch, expected {expected}, found {actual}")


def download_archive(source: Path | None) -> Path:
    cache = TEMPLATES / ".cache"
    cache.mkdir(parents=True, exist_ok=True)
    destination = cache / "MultiOverlaysWithBox_V1.zip"
    if source is not None:
        data = source.read_bytes()
    elif destination.is_file() and sha256_path(destination) == ARCHIVE_SHA256:
        return destination
    else:
        request = urllib.request.Request(ARCHIVE_URL, headers={"User-Agent": "ts2-package-harness/1"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read()
        except OSError as exc:
            raise HarnessError(f"cannot download pinned template archive: {exc}") from exc
    actual = sha256_bytes(data)
    if actual != ARCHIVE_SHA256:
        raise HarnessError(
            f"template archive SHA-256 mismatch, expected {ARCHIVE_SHA256}, found {actual}"
        )
    atomic_write_bytes(destination, data)
    return destination


def extract_templates(archive: Path) -> None:
    try:
        with zipfile.ZipFile(archive) as bundle:
            names = set(bundle.namelist())
            for filename, spec in TEMPLATE_SPECS.items():
                member = spec["member"]
                if member not in names:
                    raise HarnessError(f"pinned archive is missing {member}")
                data = bundle.read(member)
                actual = sha256_bytes(data)
                if actual != spec["sha256"]:
                    raise HarnessError(
                        f"{filename} SHA-256 mismatch, expected {spec['sha256']}, found {actual}"
                    )
                atomic_write_bytes(TEMPLATES / filename, data)
    except zipfile.BadZipFile as exc:
        raise HarnessError(f"template archive is not a valid ZIP: {exc}") from exc


def rust_environment() -> dict[str, str]:
    env = os.environ.copy()
    env["CARGO_HOME"] = str(TOOLS / "cargo")
    env["RUSTUP_HOME"] = str(TOOLS / "rustup")
    env["PATH"] = str(TOOLS / "cargo" / "bin") + os.pathsep + env.get("PATH", "")
    return env


def rustup_target() -> str:
    machine = platform.machine().lower()
    system = platform.system().lower()
    arch = {"arm64": "aarch64", "aarch64": "aarch64", "x86_64": "x86_64", "amd64": "x86_64"}.get(machine)
    os_name = {"darwin": "apple-darwin", "linux": "unknown-linux-gnu"}.get(system)
    if not arch or not os_name:
        raise HarnessError(f"automatic Rust provisioning is unsupported on {machine}-{system}")
    return f"{arch}-{os_name}"


def provision_rust() -> None:
    cargo = TOOLS / "cargo" / "bin" / "cargo"
    rustup = TOOLS / "cargo" / "bin" / "rustup"
    env = rust_environment()
    if not cargo.is_file() or not rustup.is_file():
        url = f"https://static.rust-lang.org/rustup/dist/{rustup_target()}/rustup-init"
        request = urllib.request.Request(url, headers={"User-Agent": "ts2-package-harness/1"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                installer = response.read()
        except OSError as exc:
            raise HarnessError(f"cannot download rustup-init: {exc}") from exc
        installer_path = TOOLS / "rustup-init"
        atomic_write_bytes(installer_path, installer)
        installer_path.chmod(0o700)
        run_checked(
            [
                str(installer_path),
                "-y",
                "--no-modify-path",
                "--profile",
                "minimal",
                "--default-toolchain",
                RUST_TOOLCHAIN,
            ],
            env=env,
        )
    run_checked([str(rustup), "toolchain", "install", RUST_TOOLCHAIN, "--profile", "minimal"], env=env)
    run_checked([str(rustup), "default", RUST_TOOLCHAIN], env=env)
    run_checked([str(rustup), "component", "add", "rustfmt", "--toolchain", RUST_TOOLCHAIN], env=env)


def builder_path() -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    return Path(os.environ.get("TS2_PACKAGE_BUILDER", BUILDER / "target" / "release" / f"ts2-package-builder{suffix}"))


def build_helper() -> None:
    cargo = TOOLS / "cargo" / "bin" / "cargo"
    result = run_checked(
        [
            str(cargo),
            "build",
            "--locked",
            "--release",
            "--manifest-path",
            str(BUILDER / "Cargo.toml"),
        ],
        env=rust_environment(),
    )
    if not builder_path().is_file():
        raise HarnessError(f"Rust build completed without producing {builder_path()}")
    if result.stderr:
        print(result.stderr.strip())


def verify_templates() -> dict[str, Any]:
    report = {}
    for filename, spec in TEMPLATE_SPECS.items():
        path = TEMPLATES / filename
        assert_hash(path, spec["sha256"], filename)
        probe = probe_dbpf(path)
        if probe["physical_entry_count"] != spec["entries"]:
            raise HarnessError(f"{filename} entry count differs from the pinned template")
        if probe["logical_tgi_sha256"] != spec["logical_tgi_sha256"]:
            raise HarnessError(f"{filename} starting TGI set differs from the pinned template")
        report[filename] = probe
    return report


def prepare(args: argparse.Namespace) -> None:
    archive = download_archive(args.archive.resolve() if args.archive else None)
    extract_templates(archive)
    templates = verify_templates()
    provision_rust()
    build_helper()
    report = {
        "status": "prepared",
        "archive_sha256": sha256_path(archive),
        "rust_toolchain": RUST_TOOLCHAIN,
        "dbpf_revision": DBPF_REVISION,
        "builder_sha256": sha256_path(builder_path()),
        "templates": {
            name: {
                "sha256": TEMPLATE_SPECS[name]["sha256"],
                "entries": value["physical_entry_count"],
            }
            for name, value in templates.items()
        },
    }
    print(json.dumps(report, indent=2, sort_keys=True))


def probe_dbpf(path: Path) -> dict[str, Any]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise HarnessError(f"cannot read DBPF package {path}: {exc}") from exc
    if len(data) < 0x60:
        raise HarnessError(f"truncated DBPF header: {path}")
    if data[:4] != b"DBPF":
        raise HarnessError(f"invalid DBPF magic: {path}")
    major, minor = struct.unpack_from("<II", data, 4)
    index_major = struct.unpack_from("<I", data, 0x20)[0]
    count, index_offset, index_size = struct.unpack_from("<III", data, 0x24)
    index_minor = struct.unpack_from("<I", data, 0x3C)[0]
    if (major, minor) != (1, 1):
        raise HarnessError(f"unsupported DBPF version {major}.{minor}: {path}")
    if (index_major, index_minor) != (7, 2):
        raise HarnessError(f"unsupported DBPF index version {index_major}.{index_minor}: {path}")
    if index_size != count * 24:
        raise HarnessError(f"DBPF index size does not match its entry count: {path}")
    if index_offset < 0x60 or index_offset + index_size > len(data):
        raise HarnessError(f"DBPF index is outside package boundaries: {path}")

    entries = []
    keys = set()
    ranges = []
    for position in range(index_offset, index_offset + index_size, 24):
        type_id, group_id, instance_id, resource_id, offset, size = struct.unpack_from(
            "<IIIIII", data, position
        )
        key = (type_id, group_id, resource_id, instance_id)
        if key in keys:
            raise HarnessError(f"duplicate DBPF key {key_text(key)}: {path}")
        keys.add(key)
        if offset < 0x60 or size == 0 or offset + size > len(data):
            raise HarnessError(f"DBPF entry {key_text(key)} is outside package boundaries: {path}")
        entry_end = offset + size
        index_end = index_offset + index_size
        if not (entry_end <= index_offset or offset >= index_end):
            raise HarnessError(f"DBPF entry {key_text(key)} overlaps the index: {path}")
        entry_range = (offset, offset + size, key)
        ranges.append(entry_range)
        entries.append(
            {
                "key": key,
                "offset": offset,
                "size": size,
            }
        )
    for left, right in zip(sorted(ranges), sorted(ranges)[1:]):
        if right[0] < left[1]:
            raise HarnessError(
                f"DBPF entries {key_text(left[2])} and {key_text(right[2])} overlap: {path}"
            )

    directory_entries = [entry for entry in entries if entry["key"][0] == 0xE86B1EEF]
    if len(directory_entries) > 1:
        raise HarnessError(f"multiple compressed directories found: {path}")
    compressed_keys = set()
    if directory_entries:
        directory = directory_entries[0]
        payload = data[directory["offset"] : directory["offset"] + directory["size"]]
        if len(payload) % 20:
            raise HarnessError(f"compressed directory has a partial record: {path}")
        for position in range(0, len(payload), 20):
            type_id, group_id, instance_id, resource_id, expanded_size = struct.unpack_from(
                "<IIIII", payload, position
            )
            key = (type_id, group_id, resource_id, instance_id)
            if key in compressed_keys or key not in keys or type_id == 0xE86B1EEF:
                raise HarnessError(f"compressed directory is inconsistent for {key_text(key)}: {path}")
            if expanded_size == 0:
                raise HarnessError(f"compressed directory has a zero expanded size: {path}")
            compressed_keys.add(key)

    for entry in entries:
        key = entry["key"]
        if key[0] == 0xE86B1EEF:
            continue
        payload = data[entry["offset"] : entry["offset"] + entry["size"]]
        has_refpack_header = payload[:2] == b"\x10\xfb" or payload[4:6] == b"\x10\xfb"
        if has_refpack_header != (key in compressed_keys):
            raise HarnessError(
                f"compressed directory and RefPack header disagree for {key_text(key)}: {path}"
            )

    logical_keys = sorted(key for key in keys if key[0] != 0xE86B1EEF)
    canonical = "".join(key_text(key) + "\n" for key in logical_keys).encode("ascii")
    counts = Counter(TYPE_NAMES.get(key[0], f"0x{key[0]:08X}") for key in keys)
    return {
        "path": path.name,
        "sha256": sha256_bytes(data),
        "dbpf_version": "1.1",
        "index_version": "7.2",
        "physical_entry_count": count,
        "logical_entry_count": len(logical_keys),
        "compressed_entry_count": len(compressed_keys),
        "logical_tgi_sha256": sha256_bytes(canonical),
        "resource_type_counts": dict(sorted(counts.items())),
        "entry_boundaries_valid": True,
        "compressed_directory_consistent": True,
        "duplicate_keys": [],
    }


def key_text(key: tuple[int, int, int, int]) -> str:
    return ":".join(f"{part:08X}" for part in key)


def require_prepared() -> None:
    verify_templates()
    if not builder_path().is_file():
        raise HarnessError("Rust helper is not built, run the prepare command first")


def output_names(job: dict[str, Any]) -> tuple[str, str]:
    slug = job["slug"]
    gender = job["preset"].upper()
    return f"{slug}_OverlayBox.package", f"{slug}_{gender}_Overlay.package"


def invoke_builder(
    mode: str,
    job_path: Path,
    overlay: Path,
    controller: Path,
    png: Path | None = None,
) -> tuple[dict[str, Any], list[str], str]:
    command = [
        str(builder_path()),
        mode,
        "--job",
        str(job_path),
        "--overlay-template",
        str(TEMPLATES / "Template_MultiOverlay.package"),
        "--box-template",
        str(TEMPLATES / "Template_OverlayBox.package"),
    ]
    if mode == "build":
        if png is None:
            raise HarnessError("internal error: build requires a PNG path")
        command.extend(
            [
                "--png",
                str(png),
                "--overlay-output",
                str(overlay),
                "--box-output",
                str(controller),
            ]
        )
    else:
        command.extend(["--overlay", str(overlay), "--controller", str(controller)])
    result = run_checked(command)
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise HarnessError("Rust helper returned invalid validation JSON") from exc
    return report, command, result.stderr


def invoke_multi_builder(
    mode: str,
    job_path: Path,
    output_dir: Path,
) -> tuple[dict[str, Any], list[str], str]:
    if mode not in {"build-multi", "validate-multi"}:
        raise HarnessError(f"unsupported multi-builder mode: {mode}")
    command = [
        str(builder_path()),
        mode,
        "--job",
        str(job_path),
        "--overlay-template",
        str(TEMPLATES / "Template_MultiOverlay.package"),
        "--box-template",
        str(TEMPLATES / "Template_OverlayBox.package"),
        "--output-dir",
        str(output_dir),
    ]
    result = run_checked(command)
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise HarnessError("Rust multi-builder returned invalid validation JSON") from exc
    return report, command, result.stderr


def invoke_merged_builder(
    mode: str,
    job_path: Path,
    package: Path,
) -> tuple[dict[str, Any], list[str], str]:
    if mode not in {"build-merged", "validate-merged"}:
        raise HarnessError(f"unsupported merged-builder mode: {mode}")
    command = [
        str(builder_path()),
        mode,
        "--job",
        str(job_path),
        "--overlay-template",
        str(TEMPLATES / "Template_MultiOverlay.package"),
        "--box-template",
        str(TEMPLATES / "Template_OverlayBox.package"),
        "--no-face-template",
        str(TEMPLATES / "NoFaceOverlay.package"),
        "--output" if mode == "build-merged" else "--package",
        str(package),
    ]
    result = run_checked(command)
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise HarnessError("Rust merged builder returned invalid validation JSON") from exc
    return report, command, result.stderr


def multi_output_names(job: dict[str, Any]) -> tuple[str, list[str]]:
    controller = f"{job['slug']}_OverlayBox.package"
    overlays = [
        f"{job['slug']}_{tattoo['key']}_Overlay.package"
        for tattoo in job["tattoos"]
    ]
    return controller, overlays


def merged_output_name(job: dict[str, Any]) -> str:
    return f"{job['slug']}.package"


def expected_merged_resource_count(job: dict[str, Any]) -> int:
    return 37 + sum(
        7 + 5 * sum(tattoo["input_pngs"][gender] is not None for gender in ("am", "af"))
        for tattoo in job["tattoos"]
    )


def deterministic_zip(path: Path, root: Path, members: list[str]) -> None:
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for member in sorted(members):
                source = root / member
                if not source.is_file() or source.is_symlink():
                    raise HarnessError(f"archive member is not a regular file: {member}")
                info = zipfile.ZipInfo(member, FIXED_ZIP_TIME)
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, source.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _multi_public_manifest_job(
    job: dict[str, Any], input_reports: dict[str, dict[str, dict[str, Any]]]
) -> dict[str, Any]:
    return {
        "slug": job["slug"],
        "catalog_name": job["catalog_name"],
        "catalog_description": job["catalog_description"],
        "ages": job["ages"],
        "age_mask": f"0x{MULTI_AGE_MASK:02X}",
        "compatibility": job["compatibility"],
        "controller_model": {
            "catalog_name": MULTI_CONTROLLER_CATALOG_NAME,
            "scene_name": MULTI_CONTROLLER_MODEL,
            "required_product": MULTI_CONTROLLER_REQUIRED_PRODUCT,
            "embedded": False,
        },
        "identity": job["identity"],
        "tattoos": [
            {
                "key": tattoo["key"],
                "menu_label": tattoo["menu_label"],
                "menu_order": tattoo["menu_order"],
                "layer_order": tattoo["layer_order"],
                "priority": tattoo["priority"],
                "identity": tattoo["identity"],
                "genders": sorted(input_reports[tattoo["key"]]),
                "inputs": input_reports[tattoo["key"]],
            }
            for tattoo in job["tattoos"]
        ],
    }


def _multi_readme(job: dict[str, Any], package_names: list[str]) -> str:
    lines = [
        job["catalog_name"],
        "=" * len(job["catalog_name"]),
        "",
        "Structural validation passed.",
        "",
        "This bundle provides body tattoo overlays for Adult and Elder Sims.",
        "It supports normal Sims only. Young Adult, Teen, and supernatural states are disabled.",
        "",
        "Installation",
        "------------",
        "Copy every .package file from the Install folder into your Sims 2 Downloads folder.",
        "Keep NoFaceOverlay.package installed with the controller and tattoo packages.",
        "Find the overlay box in Miscellaneous / Miscellaneous, then use its Add and Remove menus.",
        "",
        "Files",
        "-----",
        *[f"- {name}" for name in package_names],
        "",
        "Identity and updates",
        "--------------------",
        "This is a one-off build. A new submission receives new object and overlay identities.",
        "Installing a separately regenerated build will create a separate in-game collection.",
        "",
        "Safe removal",
        "------------",
        "Remove every tattoo from affected Sims in game, save, quit, and reload before deleting these package files.",
        "",
        "Validation",
        "----------",
        "manifest.json records package identities, inputs, tool versions, and hashes.",
        "validation.json records structural checks. Structural validation does not prove gameplay persistence.",
        "SHA256SUMS contains checksums for the downloadable files.",
        "",
    ]
    return "\n".join(lines)


def _write_checksums(root: Path, members: list[str]) -> None:
    content = "".join(f"{sha256_path(root / name)}  {name}\n" for name in sorted(members))
    atomic_write_bytes(root / "SHA256SUMS", content.encode("utf-8"))


def build_multi_bundle(job_path: Path, destination: Path) -> dict[str, Any]:
    if destination.exists():
        raise HarnessError(f"refusing to overwrite existing output directory: {destination}")
    job_path = job_path.resolve()
    job = load_multi_job(job_path)
    inputs = resolve_multi_inputs(job_path, job)
    input_reports = {
        key: {preset: validate_png(path) for preset, path in paths.items()}
        for key, paths in inputs.items()
    }
    require_prepared()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.tmp-{uuid.uuid4().hex}"
    if temporary.exists():
        raise HarnessError(f"temporary output collision: {temporary}")
    install = temporary / "Install"
    install.mkdir(parents=True)
    controller_name, overlay_names = multi_output_names(job)
    package_names = [controller_name, *overlay_names, "NoFaceOverlay.package"]
    try:
        rust_report, command, stderr = invoke_multi_builder("build-multi", job_path, install)
        noface = install / "NoFaceOverlay.package"
        shutil.copyfile(TEMPLATES / "NoFaceOverlay.package", noface)
        assert_hash(
            noface,
            TEMPLATE_SPECS["NoFaceOverlay.package"]["sha256"],
            "NoFaceOverlay.package",
        )
        package_paths = [install / name for name in package_names]
        probes = {f"Install/{path.name}": probe_dbpf(path) for path in package_paths}
        validation = {
            "schema_version": 2,
            "status": "pass",
            "scope": "structural-only",
            "game_validation": "not-performed",
            "rust": rust_report,
            "independent_python_dbpf_probe": probes,
        }
        atomic_write_json(temporary / "validation.json", validation)
        manifest = {
            "schema_version": 2,
            "scope": "structural-only",
            "game_validation": "not-performed",
            "one_off_build": True,
            "job": _multi_public_manifest_job(job, input_reports),
            "toolchain": {
                "rust": RUST_TOOLCHAIN,
                "dbpf_git_revision": DBPF_REVISION,
                "builder_sha256": sha256_path(builder_path()),
            },
            "templates": {
                "archive_url": ARCHIVE_URL,
                "archive_sha256": ARCHIVE_SHA256,
                "files": {name: spec["sha256"] for name, spec in TEMPLATE_SPECS.items()},
            },
            "outputs": {
                f"Install/{path.name}": {
                    "sha256": sha256_path(path),
                    "bytes": path.stat().st_size,
                    "resource_type_counts": probes[f"Install/{path.name}"]["resource_type_counts"],
                    "logical_entry_count": probes[f"Install/{path.name}"]["logical_entry_count"],
                }
                for path in package_paths
            },
        }
        atomic_write_json(temporary / "manifest.json", manifest)
        atomic_write_bytes(
            temporary / "README.txt",
            _multi_readme(job, package_names).encode("utf-8"),
        )
        checksum_members = [
            *[f"Install/{name}" for name in package_names],
            "README.txt",
            "manifest.json",
            "validation.json",
        ]
        _write_checksums(temporary, checksum_members)
        archive_members = [*checksum_members, "SHA256SUMS"]
        archive_name = f"{job['slug']}.zip"
        deterministic_zip(temporary / archive_name, temporary, archive_members)
        if (temporary / archive_name).stat().st_size > 64 * 1024 * 1024:
            raise HarnessError("generated ZIP exceeds the 64 MiB limit")
        command_for_log = [
            Path(part).name if part.startswith(str(temporary)) or part.startswith(str(job_path.parent)) else part
            for part in command
        ]
        atomic_write_json(
            temporary / "build.log",
            {
                "status": "pass",
                "scope": "structural-only",
                "command": command_for_log,
                "stderr": stderr.strip(),
                "rust_report": rust_report,
            },
        )
        temporary.replace(destination)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return manifest


def validate_multi_bundle(job_path: Path, directory: Path) -> dict[str, Any]:
    job_path = job_path.resolve()
    directory = directory.resolve()
    job = load_multi_job(job_path)
    manifest_path = directory / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HarnessError(f"cannot read multi-bundle manifest: {exc}") from exc
    if manifest.get("schema_version") != 2:
        raise HarnessError("multi-bundle manifest schema is unsupported")
    controller_name, overlay_names = multi_output_names(job)
    package_names = [controller_name, *overlay_names, "NoFaceOverlay.package"]
    install = directory / "Install"
    assert_hash(
        install / "NoFaceOverlay.package",
        TEMPLATE_SPECS["NoFaceOverlay.package"]["sha256"],
        "NoFaceOverlay.package",
    )
    rust_report, _, stderr = invoke_multi_builder("validate-multi", job_path, install)
    if stderr.strip():
        raise HarnessError(f"Rust validator wrote unexpected diagnostics: {stderr.strip()}")
    probes = {f"Install/{name}": probe_dbpf(install / name) for name in package_names}
    for name, expected in manifest.get("outputs", {}).items():
        path = directory / name
        if not path.is_file() or sha256_path(path) != expected.get("sha256"):
            raise HarnessError(f"multi-bundle file hash differs from manifest: {name}")
    checksum_members = [
        *[f"Install/{name}" for name in package_names],
        "README.txt",
        "manifest.json",
        "validation.json",
    ]
    expected_checksums = "".join(
        f"{sha256_path(directory / name)}  {name}\n" for name in sorted(checksum_members)
    )
    if (directory / "SHA256SUMS").read_text(encoding="utf-8") != expected_checksums:
        raise HarnessError("SHA256SUMS does not match the bundle")
    archive_members = sorted([*checksum_members, "SHA256SUMS"])
    archive_path = directory / f"{job['slug']}.zip"
    try:
        with zipfile.ZipFile(archive_path) as archive:
            if archive.namelist() != archive_members:
                raise HarnessError("ZIP member list is incorrect")
            for member in archive_members:
                if archive.read(member) != (directory / member).read_bytes():
                    raise HarnessError(f"ZIP member differs from published file: {member}")
    except zipfile.BadZipFile as exc:
        raise HarnessError(f"generated archive is invalid: {exc}") from exc
    return {
        "schema_version": 2,
        "status": "pass",
        "scope": "structural-only",
        "game_validation": "not-performed",
        "rust": rust_report,
        "independent_python_dbpf_probe": probes,
        "manifest_hashes": "pass",
        "checksums": "pass",
        "archive": "pass",
    }


def build_merged_package(job_path: Path, destination: Path) -> dict[str, Any]:
    if destination.exists():
        raise HarnessError(f"refusing to overwrite existing output directory: {destination}")
    job_path = job_path.resolve()
    job = load_multi_job(job_path)
    inputs = resolve_multi_inputs(job_path, job)
    input_reports = {
        key: {preset: validate_png(path) for preset, path in paths.items()}
        for key, paths in inputs.items()
    }
    require_prepared()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.tmp-{uuid.uuid4().hex}"
    if temporary.exists():
        raise HarnessError(f"temporary output collision: {temporary}")
    temporary.mkdir()
    package_name = merged_output_name(job)
    package = temporary / package_name
    try:
        rust_report, command, stderr = invoke_merged_builder("build-merged", job_path, package)
        if stderr.strip():
            raise HarnessError(f"Rust merged builder wrote unexpected diagnostics: {stderr.strip()}")
        probe = probe_dbpf(package)
        expected_count = expected_merged_resource_count(job)
        if probe["logical_entry_count"] != expected_count:
            raise HarnessError(
                "merged package resource count differs from the independently calculated count"
            )
        if rust_report.get("logical_resource_count") != expected_count:
            raise HarnessError("Rust merged resource count differs from the expected count")
        validation = {
            "schema_version": 2,
            "status": "pass",
            "scope": "structural-only",
            "game_validation": "not-performed",
            "layout": "single-package",
            "rust": rust_report,
            "independent_python_dbpf_probe": {package_name: probe},
            "expected_logical_resource_count": expected_count,
        }
        atomic_write_json(temporary / "validation.json", validation)
        manifest = {
            "schema_version": 2,
            "scope": "structural-only",
            "game_validation": "not-performed",
            "layout": "single-package",
            "one_off_build": True,
            "job": _multi_public_manifest_job(job, input_reports),
            "toolchain": {
                "rust": RUST_TOOLCHAIN,
                "dbpf_git_revision": DBPF_REVISION,
                "builder_sha256": sha256_path(builder_path()),
            },
            "templates": {
                "archive_url": ARCHIVE_URL,
                "archive_sha256": ARCHIVE_SHA256,
                "files": {name: spec["sha256"] for name, spec in TEMPLATE_SPECS.items()},
            },
            "outputs": {
                package_name: {
                    "sha256": sha256_path(package),
                    "bytes": package.stat().st_size,
                    "resource_type_counts": probe["resource_type_counts"],
                    "logical_entry_count": probe["logical_entry_count"],
                }
            },
        }
        atomic_write_json(temporary / "manifest.json", manifest)
        command_for_log = [
            Path(part).name
            if part.startswith(str(temporary)) or part.startswith(str(job_path.parent))
            else part
            for part in command
        ]
        atomic_write_json(
            temporary / "build.log",
            {
                "status": "pass",
                "scope": "structural-only",
                "layout": "single-package",
                "command": command_for_log,
                "stderr": stderr.strip(),
                "rust_report": rust_report,
            },
        )
        temporary.replace(destination)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return manifest


def validate_merged_package(job_path: Path, directory: Path) -> dict[str, Any]:
    job_path = job_path.resolve()
    directory = directory.resolve()
    job = load_multi_job(job_path)
    manifest_path = directory / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HarnessError(f"cannot read merged package manifest: {exc}") from exc
    if manifest.get("schema_version") != 2 or manifest.get("layout") != "single-package":
        raise HarnessError("merged package manifest schema or layout is unsupported")
    package_name = merged_output_name(job)
    package = directory / package_name
    rust_report, _, stderr = invoke_merged_builder("validate-merged", job_path, package)
    if stderr.strip():
        raise HarnessError(f"Rust merged validator wrote unexpected diagnostics: {stderr.strip()}")
    probe = probe_dbpf(package)
    expected_count = expected_merged_resource_count(job)
    if probe["logical_entry_count"] != expected_count:
        raise HarnessError("merged package resource count is incorrect")
    expected_output = manifest.get("outputs", {}).get(package_name)
    if not isinstance(expected_output, dict) or sha256_path(package) != expected_output.get("sha256"):
        raise HarnessError("merged package hash differs from manifest")
    if rust_report.get("logical_resource_count") != expected_count:
        raise HarnessError("Rust merged resource count differs from the expected count")
    return {
        "schema_version": 2,
        "status": "pass",
        "scope": "structural-only",
        "game_validation": "not-performed",
        "layout": "single-package",
        "rust": rust_report,
        "independent_python_dbpf_probe": {package_name: probe},
        "expected_logical_resource_count": expected_count,
        "manifest_hashes": "pass",
    }


def manifest_outputs(directory: Path, names: list[str]) -> dict[str, Any]:
    result = {}
    for name in names:
        path = directory / name
        probe = probe_dbpf(path) if path.suffix == ".package" else None
        result[name] = {
            "sha256": sha256_path(path),
            "bytes": path.stat().st_size,
        }
        if probe is not None:
            result[name]["resource_type_counts"] = probe["resource_type_counts"]
            result[name]["logical_entry_count"] = probe["logical_entry_count"]
    return result


def build_bundle(job_path: Path, destination: Path) -> dict[str, Any]:
    if destination.exists():
        raise HarnessError(f"refusing to overwrite existing output directory: {destination}")
    job = load_job(job_path)
    png = resolve_input(job_path, job)
    png_report = validate_png(png)
    require_prepared()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.parent / f".{destination.name}.tmp-{uuid.uuid4().hex}"
    if temp.exists():
        raise HarnessError(f"temporary output collision: {temp}")
    temp.mkdir()
    controller_name, overlay_name = output_names(job)
    controller = temp / controller_name
    overlay = temp / overlay_name
    try:
        rust_report, command, stderr = invoke_builder(
            "build", job_path, overlay, controller, png
        )
        noface = temp / "NoFaceOverlay.package"
        shutil.copyfile(TEMPLATES / "NoFaceOverlay.package", noface)
        assert_hash(noface, TEMPLATE_SPECS["NoFaceOverlay.package"]["sha256"], "NoFaceOverlay.package")

        container_reports = {
            path.name: probe_dbpf(path) for path in (controller, overlay, noface)
        }
        validation = {
            "schema_version": 1,
            "status": "pass",
            "scope": "structural-only",
            "game_validation": "not-performed",
            "rust": rust_report,
            "independent_python_dbpf_probe": container_reports,
        }
        atomic_write_json(temp / "validation.json", validation)
        manifest = {
            "schema_version": 1,
            "scope": "structural-only",
            "game_validation": "not-performed",
            "job": job,
            "input": png_report,
            "toolchain": {
                "rust": RUST_TOOLCHAIN,
                "dbpf_git_revision": DBPF_REVISION,
                "builder_sha256": sha256_path(builder_path()),
            },
            "templates": {
                "archive_url": ARCHIVE_URL,
                "archive_sha256": ARCHIVE_SHA256,
                "files": {
                    name: spec["sha256"] for name, spec in TEMPLATE_SPECS.items()
                },
            },
            "outputs": manifest_outputs(
                temp,
                [controller_name, overlay_name, "NoFaceOverlay.package", "validation.json"],
            ),
        }
        atomic_write_json(temp / "manifest.json", manifest)
        command_for_log = [
            Path(part).name if part.startswith(str(temp)) else part for part in command
        ]
        log = {
            "status": "pass",
            "scope": "structural-only",
            "command": command_for_log,
            "stderr": stderr.strip(),
            "rust_report": rust_report,
        }
        atomic_write_json(temp / "build.log", log)
        temp.replace(destination)
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    return manifest


def build_command(args: argparse.Namespace) -> None:
    job_path = args.job.resolve()
    job = load_job(job_path)
    destination = (
        args.output_dir.resolve()
        if args.output_dir
        else DEFAULT_OUTPUT / job["slug"]
    )
    manifest = build_bundle(job_path, destination)
    print(
        json.dumps(
            {
                "status": "built",
                "scope": manifest["scope"],
                "output_directory": str(destination),
                "files": sorted(path.name for path in destination.iterdir()),
            },
            indent=2,
            sort_keys=True,
        )
    )


def build_multi_command(args: argparse.Namespace) -> None:
    job_path = args.job.resolve()
    job = load_multi_job(job_path)
    destination = (
        args.output_dir.resolve()
        if args.output_dir
        else DEFAULT_OUTPUT / job["slug"]
    )
    manifest = build_multi_bundle(job_path, destination)
    print(
        json.dumps(
            {
                "status": "built",
                "scope": manifest["scope"],
                "output_directory": str(destination),
                "archive": str(destination / f"{job['slug']}.zip"),
                "tattoos": len(job["tattoos"]),
            },
            indent=2,
            sort_keys=True,
        )
    )


def build_merged_command(args: argparse.Namespace) -> None:
    job_path = args.job.resolve()
    job = load_multi_job(job_path)
    destination = (
        args.output_dir.resolve()
        if args.output_dir
        else DEFAULT_OUTPUT / f"{job['slug']}-merged"
    )
    manifest = build_merged_package(job_path, destination)
    print(
        json.dumps(
            {
                "status": "built",
                "scope": manifest["scope"],
                "layout": manifest["layout"],
                "output_directory": str(destination),
                "package": str(destination / merged_output_name(job)),
                "tattoos": len(job["tattoos"]),
            },
            indent=2,
            sort_keys=True,
        )
    )


def validate_existing(directory: Path) -> dict[str, Any]:
    manifest_path = directory / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HarnessError(f"cannot read bundle manifest {manifest_path}: {exc}") from exc
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("job"), dict):
        raise HarnessError("bundle manifest schema is unsupported")
    job = manifest["job"]
    validate_job(job)
    require_prepared()
    controller_name, overlay_name = output_names(job)
    controller = directory / controller_name
    overlay = directory / overlay_name
    noface = directory / "NoFaceOverlay.package"
    assert_hash(noface, TEMPLATE_SPECS["NoFaceOverlay.package"]["sha256"], "NoFaceOverlay.package")
    with tempfile.TemporaryDirectory(prefix="ts2-package-validate-") as temporary:
        job_path = Path(temporary) / "job.json"
        atomic_write_json(job_path, job)
        rust_report, _, stderr = invoke_builder(
            "validate", job_path, overlay, controller
        )
    if stderr.strip():
        raise HarnessError(f"Rust validator wrote unexpected diagnostics: {stderr.strip()}")
    probes = {path.name: probe_dbpf(path) for path in (controller, overlay, noface)}
    for name, expected in manifest.get("outputs", {}).items():
        path = directory / name
        if not path.is_file():
            raise HarnessError(f"manifest output is missing: {path}")
        actual = sha256_path(path)
        if actual != expected.get("sha256"):
            raise HarnessError(f"bundle file hash differs from manifest: {name}")
    return {
        "schema_version": 1,
        "status": "pass",
        "scope": "structural-only",
        "game_validation": "not-performed",
        "rust": rust_report,
        "independent_python_dbpf_probe": probes,
        "manifest_hashes": "pass",
    }


def validate_command(args: argparse.Namespace) -> None:
    print(json.dumps(validate_existing(args.bundle.resolve()), indent=2, sort_keys=True))


def validate_multi_command(args: argparse.Namespace) -> None:
    print(
        json.dumps(
            validate_multi_bundle(args.job.resolve(), args.bundle.resolve()),
            indent=2,
            sort_keys=True,
        )
    )


def validate_merged_command(args: argparse.Namespace) -> None:
    print(
        json.dumps(
            validate_merged_package(args.job.resolve(), args.bundle.resolve()),
            indent=2,
            sort_keys=True,
        )
    )


def smoke_job(
    path: Path,
    *,
    slug: str,
    preset: str,
    png: Path,
    box_guid: str,
    overlay_group_id: str,
    family_uuid: str,
) -> None:
    job = {
        "schema_version": 1,
        "slug": slug,
        "catalog_name": f"{slug} structural smoke",
        "catalog_description": f"Adult {preset.upper()} structural coverage only",
        "preset": preset,
        "ages": ["adult"],
        "priority": 102,
        "input_png": str(png),
        "identity": {
            "box_guid": box_guid,
            "overlay_group_id": overlay_group_id,
            "family_uuid": family_uuid,
        },
    }
    validate_job(job)
    atomic_write_json(path, job)


def make_af_pattern(path: Path) -> None:
    from PIL import Image
    image = Image.new("RGBA", (1024, 1024))
    pixels = image.load()
    for y in range(1024):
        for x in range(1024):
            alpha = (x + y) % 256
            pixels[x, y] = (x % 256, y % 256, (x ^ y) % 256, alpha)
    image.save(path, format="PNG", optimize=False)


def smoke(args: argparse.Namespace) -> None:
    require_prepared()
    sample = (REPO_ROOT / "output" / "ash_kiryu.png").resolve()
    validate_png(sample)
    with tempfile.TemporaryDirectory(prefix="ts2-package-smoke-") as temporary:
        root = Path(temporary)
        am_job = root / "am-job.json"
        smoke_job(
            am_job,
            slug="ash-kiryu-am",
            preset="am",
            png=sample,
            box_guid="0x12345678",
            overlay_group_id="0x23456789",
            family_uuid="11111111-2222-4333-8444-555555555555",
        )
        first = root / "am-first"
        second = root / "am-second"
        build_bundle(am_job, first)
        build_bundle(am_job, second)
        controller_name, overlay_name = output_names(load_job(am_job))
        compared = [controller_name, overlay_name, "NoFaceOverlay.package", "manifest.json"]
        mismatches = [
            name
            for name in compared
            if sha256_path(first / name) != sha256_path(second / name)
        ]
        if mismatches:
            raise HarnessError(f"repeat AM builds differ: {', '.join(mismatches)}")

        af_png = root / "af-pattern.png"
        make_af_pattern(af_png)
        af_job = root / "af-job.json"
        smoke_job(
            af_job,
            slug="generated-af",
            preset="af",
            png=af_png,
            box_guid="0x3456789A",
            overlay_group_id="0x456789AB",
            family_uuid="66666666-7777-4888-8999-AAAAAAAAAAAA",
        )
        af_output = root / "af-structural"
        build_bundle(af_job, af_output)
        af_validation = validate_existing(af_output)
        report = {
            "status": "pass",
            "scope": "structural-only",
            "am_repeat_builds": {
                "byte_identical": True,
                "compared": compared,
            },
            "af_generated_pattern": {
                "status": af_validation["status"],
                "coverage": "structural-only",
            },
            "game_validation": "not-performed",
        }
    print(json.dumps(report, indent=2, sort_keys=True))


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest="command", required=True)

    prepare_parser = commands.add_parser("prepare", help="download pinned templates and build the helper")
    prepare_parser.add_argument("--archive", type=Path, help="use a local copy of the pinned ZIP")
    prepare_parser.set_defaults(function=prepare)

    init_parser = commands.add_parser("init", help="create a persistent version 1 job")
    init_parser.add_argument("job", type=Path)
    init_parser.add_argument("--slug", required=True)
    init_parser.add_argument("--catalog-name", required=True)
    init_parser.add_argument("--catalog-description", required=True)
    init_parser.add_argument("--preset", choices=("am", "af"), required=True)
    init_parser.add_argument("--input-png", type=Path, required=True)
    init_parser.add_argument("--priority", type=int, default=102)
    init_parser.set_defaults(function=create_job)

    build_parser = commands.add_parser("build", help="atomically publish one new bundle")
    build_parser.add_argument("job", type=Path)
    build_parser.add_argument("--output-dir", type=Path)
    build_parser.set_defaults(function=build_command)

    build_multi_parser = commands.add_parser(
        "build-multi",
        help="atomically publish an Adult and Elder multi-tattoo bundle",
    )
    build_multi_parser.add_argument("job", type=Path)
    build_multi_parser.add_argument("--output-dir", type=Path)
    build_multi_parser.set_defaults(function=build_multi_command)

    build_merged_parser = commands.add_parser(
        "build-merged",
        help="atomically publish one merged Adult and Elder tattoo package",
    )
    build_merged_parser.add_argument("job", type=Path)
    build_merged_parser.add_argument("--output-dir", type=Path)
    build_merged_parser.set_defaults(function=build_merged_command)

    validate_parser = commands.add_parser("validate", help="inspect an existing bundle without rebuilding")
    validate_parser.add_argument("bundle", type=Path)
    validate_parser.set_defaults(function=validate_command)

    validate_multi_parser = commands.add_parser(
        "validate-multi",
        help="validate an existing multi-tattoo bundle against its internal job",
    )
    validate_multi_parser.add_argument("job", type=Path)
    validate_multi_parser.add_argument("bundle", type=Path)
    validate_multi_parser.set_defaults(function=validate_multi_command)

    validate_merged_parser = commands.add_parser(
        "validate-merged",
        help="validate an existing merged tattoo package against its internal job",
    )
    validate_merged_parser.add_argument("job", type=Path)
    validate_merged_parser.add_argument("bundle", type=Path)
    validate_merged_parser.set_defaults(function=validate_merged_command)

    smoke_parser = commands.add_parser("smoke", help="run AM repeatability and AF structural coverage")
    smoke_parser.set_defaults(function=smoke)
    return cli


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        args.function(args)
    except HarnessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
