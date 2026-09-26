"""Persistent, inspected hairstyles imported from loose Sims 2 packages."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import threading
import time
import zipfile
from pathlib import Path

from .install import BUILDER, inspect
from .game_meshes import resolve_game_meshes, requirements as game_requirements
from .worker import age_labels, atomic_json, disk_size

MIB = 1024 * 1024


def sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def inventory(path: Path) -> dict:
    result = subprocess.run([str(BUILDER), "inventory", "--package", str(path)],
                            capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError(result.stderr.strip().removeprefix("Error: ")[:1000])
    return json.loads(result.stdout)


class HairLibrary:
    def __init__(self, root: Path):
        self.root = root
        self.lock = threading.RLock()

    def directory(self, item_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", item_id):
            raise ValueError("Unknown hairstyle. Select one from the library")
        directory = self.root / item_id
        if not (directory / "item.json").is_file():
            raise ValueError("This hairstyle is no longer in the library")
        return directory

    def get(self, item_id: str) -> dict:
        return json.loads((self.directory(item_id) / "item.json").read_text())

    def items(self) -> list[dict]:
        with self.lock:
            return sorted((json.loads(p.read_text()) for p in self.root.glob("*/item.json")
                           if re.fullmatch(r"[0-9a-f]{64}", p.parent.name)), key=lambda i: (i["label"].casefold(), i["id"]))

    def import_files(self, sources: list[tuple[Path, str]], label: str = "") -> list[dict]:
        label = label.strip()
        if len(label) > 96 or any(ord(c) < 32 for c in label):
            raise ValueError("Use a hairstyle name of at most 96 characters")
        with self.lock:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.TemporaryDirectory(prefix="import-", dir=self.root) as temporary:
                staging = Path(temporary)
                packages = self._stage_packages(sources, staging)
                meshes, recolors = [], []
                deadline = time.monotonic() + 120
                keys = {}
                for path in packages:
                    if time.monotonic() > deadline:
                        raise ValueError("Package inspection timed out. Upload fewer packages together")
                    data = inventory(path)
                    if data["kind"] == "mesh":
                        for key in data["resource_keys"]:
                            if key in keys:
                                raise ValueError("Mesh packages contain conflicting resource identities. Upload one matching mesh set")
                            keys[key] = path.name
                        meshes.append((path, data))
                    else:
                        recolors.append(path)
                if not recolors:
                    raise ValueError("The upload contains only a mesh. Select its matching hair recolor .package together with the mesh so its materials, texture layout and ages can be used")
                for path, data in meshes:
                    missing = set(data["links"]) - set(keys)
                    if missing:
                        raise ValueError(f"Mesh {path.name} has unresolved scene references. Include all of its mesh packages")
                prepared = []
                used_meshes = set()
                for index, recolor in enumerate(recolors):
                    if time.monotonic() > deadline:
                        raise ValueError("Package inspection timed out. Upload fewer recolors together")
                    target = staging / f"item-{index}"
                    target.mkdir()
                    shutil.copyfile(recolor, target / "template.package")
                    info = inspect(target / "template.package", target / "template-textures")
                    required = set(info["external_meshes"])
                    game_meshes = resolve_game_meshes(required - set(keys))
                    selected = set(keys[k] for k in required if k in keys)
                    missing = required - set(keys) - {record["key"] for record in game_meshes}
                    if meshes and missing:
                        raise ValueError(f"The supplied mesh does not satisfy every mesh reference in {recolor.name}. Upload its matching mesh and recolor together. Unresolved references: {', '.join(sorted(missing))}. Built-in game references require the server's verified game mesh catalog")
                    # Include dependencies between mesh files as well as direct
                    # CRES/SHPE references from the recolor's age records.
                    while True:
                        before = set(selected)
                        for path, data in meshes:
                            if path.name in selected:
                                selected.update(keys[k] for k in data["links"])
                        if selected == before:
                            break
                    used_meshes.update(selected)
                    dependencies = []
                    for path, data in meshes:
                        if path.name not in selected:
                            continue
                        (target / "meshes").mkdir(exist_ok=True)
                        shutil.copyfile(path, target / "meshes" / path.name)
                        dependencies.append({"filename": path.name, "sha256": sha256(path),
                                             "resource_keys": data["resource_keys"]})
                    dependencies.sort(key=lambda m: m["filename"].casefold())
                    template_hash = sha256(recolor)
                    item_id = hashlib.sha256(json.dumps([template_hash, dependencies], sort_keys=True).encode()).hexdigest()
                    title = label or recolor.stem
                    if label and len(recolors) > 1:
                        title += " / " + recolor.stem
                    gender_flags = 0
                    for age in info["ages"]:
                        gender_flags |= age["gender"]
                    example_name = "Template_textures.zip"
                    with zipfile.ZipFile(target / example_name, "w", zipfile.ZIP_DEFLATED) as archive:
                        for i, slot in enumerate(info["textures"]):
                            archive.write(target / "template-textures" / (slot["id"] + ".png"), f"Texture_{i + 1}.png")
                    item = {"id": item_id, "label": title, "kind": "custom", "inspection": info,
                            "source_filename": recolor.name, "source_filenames": [p.name for p in packages], "recolor_filename": recolor.name,
                            "template_sha256": template_hash, "meshes": dependencies, "game_meshes": game_meshes,
                            "ages": age_labels(info), "gender": {1: "Female", 2: "Male", 3: "Female and male"}[gender_flags],
                            "requirements": ("Install the included mesh files with the recolors. " if dependencies else "Install the original hairstyle's mesh separately. ")
                                + "Any additional game requirements remain those of the original creator." + game_requirements(game_meshes),
                            "example_url": f"/api/v1/hair/templates/{item_id}/example", "example_filename": example_name,
                            "example_label": "Download template textures", "created": time.time()}
                    atomic_json(target / "item.json", item)
                    prepared.append((target, item))
                    if disk_size(staging) > 512 * MIB:
                        raise ValueError("Expanded hairstyle data exceeds 512 MiB. Upload fewer recolors together")
                if meshes and used_meshes != {p.name for p, _ in meshes}:
                    raise ValueError("The upload contains a mesh unrelated to its recolors. Upload one matching hairstyle set")
                if len(self.items()) + len({i["id"] for _, i in prepared if not (self.root / i["id"]).exists()}) > 128:
                    raise ValueError("The hairstyle library has reached its 128-entry limit")
                if disk_size(self.root) > 1024 * MIB:
                    raise ValueError("The hairstyle library exceeds its 1 GiB limit")
                result = []
                for target, item in prepared:
                    destination = self.root / item["id"]
                    if not destination.exists():
                        target.rename(destination)
                    result.append(self.get(item["id"]))
                return result

    @staticmethod
    def _stage_packages(sources: list[tuple[Path, str]], staging: Path) -> list[Path]:
        if not 1 <= len(sources) <= 64:
            raise ValueError("Upload 1 through 64 mesh and recolor packages together")
        directory = staging / "packages"
        directory.mkdir()
        total, names, outputs = 0, set(), []
        for source, filename in sources:
            if not filename or any(c in filename for c in "/\\:") or any(ord(c) < 32 for c in filename):
                raise ValueError("Package filenames must not contain paths or control characters")
            if not filename.lower().endswith(".package"):
                raise ValueError("Upload .package files directly. Extract mesh and matching recolor packages from any ZIP first")
            if filename.casefold() in names:
                raise ValueError("Package filenames collide. Select files with distinct names")
            names.add(filename.casefold())
            size = source.stat().st_size
            total += size
            if size > 64 * MIB or total > 128 * MIB:
                raise ValueError("Package uploads exceed the 128 MiB total or 64 MiB per-file limit")
            destination = directory / filename
            shutil.copyfile(source, destination)
            outputs.append(destination)
        return sorted(outputs, key=lambda p: p.name.casefold())
