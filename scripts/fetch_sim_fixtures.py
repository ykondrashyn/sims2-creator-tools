"""Fetch pinned research fixtures, without installing them into any game profile."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

FIXTURES = [
    dict(id="am", file=2184980, version=1694233686,
         archive_sha256="6144fb2c25ab9791aa6b0126a893067c30d92345bcfc25f1e69690f22b4ecbff",
         credit="Dellesims / dellevanth, Ryan Gosling (Ken)",
         source="https://modthesims.info/d/679401/barbie-2023-margot-robbie-ryan-gosling-issa-rae-and-america-ferrera.html",
         packages={"82c7af9d_9d2b26a1.package":"629eae56fb25e55472f31a5e1b2b45e3b6b9208e1e77ebf305a517c5435a8a95"}),
    dict(id="af", file=2184982, version=1694233686,
         archive_sha256="4f65ed178a5e2425b08a2f2eebf365bd42177626eb70103678542baed81e9e24",
         credit="Dellesims / dellevanth, Margot Robbie (Barbie)",
         source="https://modthesims.info/d/679401/barbie-2023-margot-robbie-ryan-gosling-issa-rae-and-america-ferrera.html",
         packages={"82c7af9d_b1d19ec8.package":"747f08123e3e2789f1ee6d66aa0438156bd1296bccb2d6b32980d3d7d0b27b29"}),
    dict(id="wolf", file=439559, version=1168118470,
         archive_sha256="3651143f539221fed7bbb20c13af9a757a54ba155d8cc248a902631467ecdd9f",
         credit="Xarteras, Wolf head mesh and skin (Hair)",
         source="https://modthesims.info/d/213719/wolf-head-mesh-and-skin-hair.html",
         packages={"0752910E_furry_wolfhead.package":"de37f3dd882ddcb16044dd1256ef92455b7f50bec26515baf48fd62bbff6e98e",
                   "37621a32_wolfsuit.package":"1f6f7e5a725dffa88643da250e95d3cc7d182ae6e6a3f9b6cc01511f7ddc0550",
                   "disableeyes.package":"3a2ca81800726daccdcd01d6564d57cd9a31879e10e232122eb97e1d39ee3b4d"}),
]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def extract(data):
    names = subprocess.check_output(["tar", "-tf", "-"], input=data).decode().splitlines()
    result = {}
    for name in names:
        if Path(name).name != name or not name.lower().endswith((".package", ".sims2pack")):
            raise ValueError("Unexpected fixture archive entry")
        content = subprocess.check_output(["tar", "-xOf", "-", name], input=data)
        if name.lower().endswith(".sims2pack"):
            end = content.index(b"</Sims2Package>") + len(b"</Sims2Package>")
            xml = ET.fromstring(content[content.index(b"<?xml"):end])
            assert xml.attrib["type"] == "Sim"
            for item in xml.findall("PackagedFile"):
                filename = item.findtext("Name")
                length, offset = int(item.findtext("Length")), int(item.findtext("Offset"))
                assert Path(filename).name == filename and length <= 64 * 1024**2 and offset >= 0
                assert filename not in result
                result[filename] = content[end + offset:end + offset + length]
        else:
            assert name not in result
            result[name] = content
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/sim-creator/fixtures"))
    args = parser.parse_args()
    manifest = {"version": 1, "purpose": "Research fixtures only, not production scaffolds", "items": []}
    for spec in FIXTURES:
        directory = args.output / spec["id"]
        archive = directory / "source.rar"
        url = f"https://chii.modthesims.info/getfile.php?file={spec['file']}&v={spec['version']}"
        if archive.exists():
            data = archive.read_bytes()
        else:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=45) as response:
                data = response.read(10 * 1024**2 + 1)
        if digest(data) != spec["archive_sha256"]:
            raise ValueError(f"Fixture integrity check failed: {spec['id']}")
        packages = extract(data)
        assert set(packages) == set(spec["packages"])
        for name, content in packages.items():
            if not content.startswith(b"DBPF") or digest(content) != spec["packages"][name]:
                raise ValueError(f"Package integrity check failed: {name}")
        directory.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(data)
        for name, content in packages.items():
            (directory / name).write_bytes(content)
        manifest["items"].append({**spec, "url": url, "sizes": {n: len(b) for n,b in packages.items()}})
        print(f"Verified {spec['id']}: {len(packages)} packages")
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
