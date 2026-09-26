"""Install verified, already-downloaded third-party assets once on the server."""
from __future__ import annotations
import argparse
import os
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from .colors import ASSETS, TEMPLATE, TEMPLATE_SHA256, ARCHIVE_SHA256, curves

BUILDER = Path(os.environ.get("TS2_HAIR_BUILDER", Path(__file__).resolve().parents[1] / "builder/target/release/ts2-hair-builder"))


def inspect(package: Path, export: Path | None = None) -> dict:
    args = [str(BUILDER), "inspect", "--package", str(package)]
    if export:
        args += ["--export-dir", str(export)]
    result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError(result.stderr.strip().removeprefix("Error: ")[:1000])
    return json.loads(result.stdout)


def install(curve_archive: Path, template_archive: Path) -> dict:
    if hashlib.sha256(curve_archive.read_bytes()).hexdigest() != ARCHIVE_SHA256:
        raise ValueError("IaKoa archive SHA-256 does not match the pinned download")
    member = "mansion and garden swirl f/Pooklet-MG-Swirl-Primer-platinum-blond.package"
    package = subprocess.run(["/usr/bin/tar", "-xOf", str(template_archive), member], capture_output=True, check=True).stdout
    if hashlib.sha256(package).hexdigest() != TEMPLATE_SHA256:
        raise ValueError("Swirl template SHA-256 does not match")
    ASSETS.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ASSETS) as temporary:
        target = Path(temporary)
        subprocess.run(["/usr/bin/tar", "-xf", str(curve_archive), "-C", str(target)], check=True)
        shutil.copytree(target, ASSETS / "curves", dirs_exist_ok=True)
    TEMPLATE.write_bytes(package)
    curves.cache_clear()
    curves()
    info = inspect(TEMPLATE, ASSETS / "template-textures")
    from .processing import load_texture, prepare
    example = load_texture(ASSETS / "template-textures" / (info["textures"][0]["id"] + ".png"))
    converted = prepare(example, {"base": "Primer"})
    converted.putalpha(example.getchannel("A"))
    converted.save(ASSETS / "Swirl_Volatile_Example.png")
    manifest = {"template_sha256": TEMPLATE_SHA256, "curve_archive_sha256": ARCHIVE_SHA256, "inspection": info}
    (ASSETS / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--curves", type=Path, default=Path.home() / "Downloads/IaKoa-Pooklet Curves.7z")
    parser.add_argument("--template", type=Path, default=Path.home() / "Downloads/mansion and garden swirl f.rar")
    args = parser.parse_args()
    print(json.dumps(install(args.curves, args.template), indent=2))
