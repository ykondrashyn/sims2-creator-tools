import json, hashlib, random
from pathlib import Path
from PIL import Image
from package_creation.hair.colors import ASSETS, curves
from package_creation.hair.custom_colors import parse_gimp, normalize_curve
from package_creation.hair.processing import prepare
from package_creation.tests.test_custom_curves import gimp, color

root = Path.cwd()
out = root / "artifacts/wasm-migration/fixtures"
out.mkdir(parents=True, exist_ok=True)
r = random.Random(9355)
data = r.randbytes(512 * 512 * 4)
im = Image.frombytes("RGBA", (512, 512), data)
im.save(out / "pixels.png")
settings = [
    {"base": n} for n in ["Volatile", "Primer", "Grenade", "Incendiary", "Pooklet Grey"]
] + [
    {"base": "Arbitrary texture", "black": b, "white": w, "gamma": g}
    for b, w, g in [
        (0, 255, 1),
        (12, 230, 1.4),
        (0, 255, 0.1),
        (0, 255, 5),
        (127, 129, 0.3),
    ]
]
expected = []
for config in settings:
    image = prepare(im, config)
    image.putalpha(im.getchannel("A"))
    expected.append(
        {"settings": config, "sha256": hashlib.sha256(image.tobytes()).hexdigest()}
    )
parsed = []
for path in sorted((ASSETS / "curves").rglob("Pooklet*")):
    if path.is_file():
        definition = parse_gimp(path.read_bytes(), path.name)
        _, tables = normalize_curve(definition)
        parsed.append({"path": str(path), "tables": tables})
editor = color()["curve"]
editor["points"]["value"] = [[0, 0], [255, 127.5]]
invalid = [
    b"not curves",
    gimp() + b"(trc linear)",
    gimp() + b"(linear yes)",
    gimp()[:-1],
    gimp() + b"(unsupported yes)",
]
alpha = {
    c: [i / 255 for i in range(256)] for c in ["value", "red", "green", "blue", "alpha"]
}
alpha["alpha"][100] = 0
invalid.append(gimp(alpha))
for i, b in enumerate(invalid):
    (out / f"bad-{i}").write_bytes(b)
(out / "color-cases.json").write_text(
    json.dumps(
        {
            "processing": expected,
            "imports": parsed,
            "editor": {"curve": editor, "tables": normalize_curve(editor)[1]},
            "invalid_files": [str(out / f"bad-{i}") for i in range(len(invalid))],
        }
    )
)
# Two nonblank RGBA body maps, including colored transparent pixels and overlapping alpha.
for gender in ["am", "af"]:
    image = Image.new("RGBA", (1024, 1024), (123, 45, 67, 0))
    p = image.load()
    for y in range(128, 900):
        for x in range(250, 750):
            p[x, y] = (
                (x // 8) % 255,
                (y // 8) % 255,
                60 if gender == "am" else 160,
                ((x + y) // 8) % 256,
            )
    image.save(out / f"{gender}.png")
print("fixtures prepared", len(parsed), "curve imports", len(expected), "base checks")

# Split the real mesh to exercise cross-package resource resolution.
from package_creation.tests.test_hair import raw_nodes, write_nodes
import struct

rose = root / "artifacts/hair-validation/embedded-bundle"
nodes = raw_nodes(rose / "mesh_rosehair_0124.package")
by_type = {
    kind: [n for n in nodes if n[0][0] == kind] for kind in {n[0][0] for n in nodes}
}
assert len({len(group) for group in by_type.values()}) == 1
parts = []
for i in range(len(next(iter(by_type.values())))):
    path = out / f"mesh-part-{i}.package"
    write_nodes(path, [group[i] for group in by_type.values()])
    parts.append(str(path))
other = bytearray((rose / "recolor_3555b7d0_rose72.package").read_bytes())
struct.pack_into("<I", other, 24, 123456)
(out / "second-recolor.package").write_bytes(other)
(out / "split-mesh.json").write_text(
    json.dumps(
        {
            "parts": parts,
            "recolor": str(rose / "recolor_3555b7d0_rose72.package"),
            "second": str(out / "second-recolor.package"),
        }
    )
)
