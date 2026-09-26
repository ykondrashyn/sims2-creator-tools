"""Pinned server-side IaKoa curves and a public palette without raw presets."""
from __future__ import annotations

import functools
import os
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ASSETS = Path(os.environ.get("PROJECT_FIXTURE_ROOT", ROOT.parents[1])) / "package_creation/hair/assets"
TEMPLATE = ASSETS / "swirl.package"
TEMPLATE_SHA256 = "330a974828e8053cba0a0c6b94d5d729243ddf1dfde7576f1660ba301df34d64"
ARCHIVE_SHA256 = "ee2b84d55e4c0c762b0a42c86b28497c81ff1afabbc98ac6f590f0846a7a583e"
FAMILIES = [
    ["Dynamite", "Depth Charge", "Incendiary", "Explosive"],
    ["Shrapnel", "Safety Fuse", "Volatile", "Pyrotechnic"],
    ["Land Mine", "Fission", "Grenade", "Comburent"],
    ["Flash Powder", "Brisance", "Primer", "Molotov"],
]
UNNATURAL = ["Afterburner", "Blasting Agent", "Cannonball", "Detonator", "Firework",
             "Flammable", "Fluorophore", "Fulminant", "Hangfire", "Hazardous", "HMX",
             "Hydrazine", "Napalm", "Nitroglycerin", "Nuclear", "Pentolite", "Powder Cake",
             "Pyrodex", "Semtex", "Shockwave", "Tetryl", "Time Bomb", "TNT", "Torpedo", "Unstable"]
PALETTE = [dict(name=name, kind="natural", family=f + 1, bin=b + 1)
           for f, names in enumerate(FAMILIES) for b, name in enumerate(names)]
PALETTE += [dict(name=n, kind="grey", family=None, bin=5) for n in ["Mail Bomb", "Pipe Bomb"]]
PALETTE += [dict(name=n, kind="unnatural", family=None, bin=0) for n in UNNATURAL]
BY_NAME = {c["name"]: c for c in PALETTE}
BASES = ["Volatile", "Primer", "Grenade", "Incendiary", "Pooklet Grey", "Arbitrary texture"]


def parse_curve(path: Path) -> tuple[tuple[int, ...], ...]:
    text = path.read_text(encoding="utf-8")
    blocks = re.findall(r"\(channel (value|red|green|blue|alpha)\)(.*?)(?=\(channel |\Z)", text, re.S)
    tables = {}
    for channel, block in blocks:
        match = re.search(r"\(samples 256\s+([^)]*)\)", block)
        if not match:
            raise ValueError(f"Unsupported curve format in {path.name}")
        values = [float(v) for v in match[1].split()]
        if len(values) != 256 or any(not math.isfinite(v) or not 0 <= v <= 1 for v in values):
            raise ValueError(f"Invalid curve samples in {path.name}")
        tables[channel] = tuple(int(v * 255 + 0.5) for v in values)
    if set(tables) != {"value", "red", "green", "blue", "alpha"}:
        raise ValueError("Each curve must have all five channels")
    if any(tables[c] != tuple(range(256)) for c in ["value", "alpha"]):
        raise ValueError("This palette requires identity value and alpha curves")
    return tuple(tables[c] for c in ["red", "green", "blue"])


@functools.lru_cache(maxsize=1)
def curves() -> dict[str, tuple[tuple[int, ...], ...]]:
    result = {"Volatile": (tuple(range(256)),) * 3}
    root = ASSETS / "curves" / "IaKoa-Pooklet Curves"
    for path in root.rglob("Pooklet*"):
        if not path.is_file() or path.suffix:
            continue
        name = path.name
        if name.startswith("PookletSpecial "):
            key = "base:" + name.removeprefix("PookletSpecial ").removesuffix("-to-Volatile")
        elif "Volatile BASE" in name:
            key = "base:arbitrary-grey"
        elif name.startswith("PookletUnnatural "):
            key = name.removeprefix("PookletUnnatural ").strip()
        else:
            key = name.removeprefix("Pooklet ").split("-")[0].strip().replace("Combulent", "Comburent")
        result[key] = parse_curve(path)
    expected = set(BY_NAME) | {"base:" + b for b in ["Primer", "Grenade", "Incendiary", "Grey", "arbitrary-grey"]}
    if set(result) != expected:
        raise ValueError("Hair presets are missing or unexpected, run python -m package_creation.hair.install")
    if result["base:Grey"] == result["base:arbitrary-grey"]:
        raise ValueError("The two grey conversion curves must remain distinct")
    return result


def age_color(color: str, elder: bool) -> str:
    return "Mail Bomb" if elder and BY_NAME[color]["kind"] == "natural" else color


def public_palette() -> list[dict]:
    from PIL import Image, ImageStat
    tables = curves()
    # Sample the installed Volatile atlas so the initial swatches reflect the
    # same colored base as the actual previews, rather than a neutral grey.
    with Image.open(ASSETS / "Swirl_Volatile_Example.png") as image:
        rgba = image.convert("RGBA")
        sample = [round(v) for v in ImageStat.Stat(rgba.convert("RGB"), rgba.getchannel("A")).mean]
    return [{**c, "swatch": "#" + "".join(f"{t[v]:02x}" for t, v in zip(tables[c["name"]], sample))} for c in PALETTE]
