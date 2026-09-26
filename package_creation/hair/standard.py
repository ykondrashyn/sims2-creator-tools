"""Data-driven, privately installed standard game hairstyles."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from pathlib import Path

from .colors import ASSETS, TEMPLATE
from .install import BUILDER, inspect
from .library import sha256
from .processing import load_texture, prepare
from .worker import age_labels, atomic_json

ROOT = ASSETS / "standard"
RECIPES = Path(__file__).with_name("standard_sources.json")


class StandardCatalog:
    def __init__(self, root: Path = ROOT):
        self.root = root

    def directory(self, item_id: str) -> Path:
        if not re.fullmatch(r"standard-[a-z0-9-]{1,80}", item_id):
            raise ValueError("Unknown standard hairstyle. Select one from the catalog")
        directory = self.root / item_id
        if not (directory / "item.json").is_file():
            raise ValueError("This standard hairstyle is not installed on the server")
        return directory

    def get(self, item_id: str) -> dict:
        return json.loads((self.directory(item_id) / "item.json").read_text())

    def items(self) -> list[dict]:
        return sorted((json.loads(path.read_text()) for path in self.root.glob("standard-*/item.json")),
                      key=lambda item: (item["gender"], item["label"].casefold()))


def builder(*args: str) -> dict:
    result = subprocess.run([str(BUILDER), *map(str, args)], capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise ValueError(result.stderr.strip().removeprefix("Error: "))
    return json.loads(result.stdout)


def install(extracted: Path, recipes: Path = RECIPES, root: Path = ROOT) -> None:
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    catalog = json.loads(recipes.read_text())
    for recipe in catalog["items"]:
        item_id = recipe["id"]
        if not re.fullmatch(r"standard-[a-z0-9-]{1,80}", item_id):
            raise ValueError("Invalid standard catalog identifier")
        destination = root / item_id
        source = extracted / (item_id + ".package")
        fingerprint = hashlib.sha256((sha256(source) + json.dumps(recipe, sort_keys=True)).encode()).hexdigest()
        if destination.exists():
            previous = StandardCatalog(root).get(item_id)
            if previous["source_fingerprint"] == fingerprint and sha256(destination / "template.package") == previous["template_sha256"]:
                print(f"Already installed {recipe['label']} ({recipe['gender']})", flush=True)
                continue
            raise ValueError(f"Installed catalog entry changed: {item_id}. Use a new versioned catalog ID")
        with tempfile.TemporaryDirectory(prefix="prepare-", dir=root) as temporary:
            stage = Path(temporary)
            prepared = stage / "prepared.package"
            report = builder("prepare-standard", "--source", source, "--scaffold", TEMPLATE, "--output", prepared)
            if set(report["source_keys"]) != {entry["key"] for entry in recipe["resources"]}:
                raise ValueError("Extracted game dependency graph differs from the pinned recipe")
            if report["source_hashes"] != {entry["key"]: entry["sha256"] for entry in recipe["resources"]}:
                raise ValueError("Extracted game resources differ from the verified source hashes")
            info = inspect(prepared, stage / "source-textures")
            assignments = {}
            for slot in info["textures"]:
                image = load_texture(stage / "source-textures" / (slot["id"] + ".png"))
                base = prepare(image, {"base": "Arbitrary texture"})
                base.putalpha(image.getchannel("A"))
                path = stage / (slot["id"] + ".png")
                base.save(path)
                assignments[slot["id"]] = str(path)
            identity = lambda name: str(uuid.uuid5(uuid.NAMESPACE_URL, "ts2-standard/" + item_id + "/" + name))
            spec = {"template": str(prepared), "group": 0x50000000 | (int(fingerprint[:8], 16) & 0x0fffffff),
                    "family": identity("family"), "hairtone": identity("tone"), "creator_uuid": identity("creator"),
                    "label": recipe["label"], "color": "Volatile", "bin": 3, "grey_only": False, "grey_elders": False,
                    "textures": assignments, "elder_textures": {}}
            atomic_json(stage / "spec.json", spec)
            validation = builder("build", "--spec", stage / "spec.json", "--output", stage / "template.package")
            inspection = inspect(stage / "template.package", stage / "template-textures")
            example = "Example_textures.zip"
            with zipfile.ZipFile(stage / example, "w", zipfile.ZIP_DEFLATED) as archive:
                notes = [recipe["label"] + " (" + recipe["gender"] + ")", "", "Select Volatile as the input base when using these prepared examples.",
                         "These examples were prepared approximately from Maxis textures using desaturation and the grey-base-to-Volatile curve.",
                         "Their alpha and layout come from the selected game hairstyle. Assign each file to its numbered slot.", ""]
                for index, slot in enumerate(inspection["textures"], 1):
                    filename = f"Texture_{index}_Volatile.png"
                    archive.write(stage / "template-textures" / (slot["id"] + ".png"), filename)
                    notes.append(f"{filename}: " + ", ".join(age_labels({"ages": [{"age": a} for a in slot["ages"]]})))
                notes.extend(["", "Hairstyle and original textures: Maxis / Electronic Arts. Color curves: Pooklet, converted by IaKoa."])
                archive.writestr("README.txt", "\n".join(notes) + "\n")
            item = {"id": item_id, "label": recipe["label"], "kind": "standard", "game_content": recipe["game_content"],
                    "gender": recipe["gender"], "ages": age_labels(inspection), "inspection": inspection,
                    "requirements": "Uses a standard base-game mesh. No custom mesh download is needed. Young Adult requires University or Legacy Collection.",
                    "template_credit": "Maxis / Electronic Arts: " + recipe["label"] + " (" + recipe["gender"] + ")",
                    "template_sha256": sha256(stage / "template.package"), "source_fingerprint": fingerprint,
                    "source_family": recipe["source_family"], "meshes": [],
                    "example_filename": example, "example_url": f"/api/v1/hair/templates/{item_id}/example",
                    "example_label": "Download Volatile example textures", "structural_validation": "passed", "gameplay_validation": "not tested"}
            atomic_json(stage / "item.json", item)
            atomic_json(stage / "validation.json", {"template": validation, "source": recipe})
            # Only the finished catalog assets survive installation.
            for path in list(stage.iterdir()):
                if path.name not in {"item.json", "template.package", "template-textures", example, "validation.json"}:
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink()
            stage.rename(destination)
            print(f"Installed {recipe['label']} ({recipe['gender']}), {len(inspection['textures'])} texture slots", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extracted", type=Path, required=True)
    parser.add_argument("--recipes", type=Path, default=RECIPES)
    args = parser.parse_args()
    install(args.extracted, args.recipes)
