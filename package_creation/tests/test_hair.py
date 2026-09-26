"""Color correctness, hostile input, and end-to-end hair package regressions."""
from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import shutil
import struct
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image, ImageCms

from package_creation.hair.colors import ASSETS, TEMPLATE, BY_NAME, FAMILIES, curves, parse_curve
from package_creation.hair.install import BUILDER, inspect
from package_creation.hair.library import HairLibrary
from package_creation.hair.game_meshes import CATALOG, index_game, resolve_game_meshes
from package_creation.hair.processing import fit_package_inputs, load_texture, prepare, render, settings
from package_creation.hair.service import HairManager, HairError, thumbnail
from package_creation.hair.standard import StandardCatalog, RECIPES, install as install_standard
from package_creation.hair.worker import atomic_json
from package_creation.service.app import ServiceConfig, create_app

from package_creation.tests.native_service_reference import create_app


def png(size=(512, 512), color=(120, 80, 60, 127), **options):
    output = io.BytesIO()
    Image.new("RGBA", size, color).save(output, format="PNG", **options)
    return output.getvalue()


def bmp(size=(512, 512), color=(120, 80, 60), mode="RGB"):
    output = io.BytesIO()
    image = Image.new(mode, size, color)
    if mode == "P":
        image.putpalette([channel for i in range(256) for channel in (i, 255 - i, i * 3 % 256)])
    image.save(output, format="BMP")
    return output.getvalue()


def bmp_v5(profile=None, alpha=127):
    """One BGRA pixel with explicit alpha masks and optional embedded ICC data."""
    dib = bytearray(124)
    struct.pack_into("<IiiHHII", dib, 0, 124, 1, 1, 1, 32, 3, 4)
    struct.pack_into("<4I", dib, 40, 0xff0000, 0xff00, 0xff, 0xff000000)
    struct.pack_into("<I", dib, 56, 0x4d424544 if profile is not None else 0x73524742)
    if profile is not None:
        struct.pack_into("<II", dib, 112, 128, len(profile))
    pixels = bytes([60, 80, 120, alpha])
    return struct.pack("<2sIHHI", b"BM", 138 + len(pixels) + len(profile or b""), 0, 0, 138) + dib + pixels + (profile or b"")


def bmp_v5_rgb(size=(512, 512)):
    """24-bit sRGB V5 BMP, matching the user's Casual1 export format."""
    original = bmp(size)
    header = bytearray(original[:54] + bytes(84))
    struct.pack_into("<I", header, 2, len(original) + 84)
    struct.pack_into("<I", header, 10, 138)
    struct.pack_into("<I", header, 14, 124)
    struct.pack_into("<I", header, 70, 0x73524742)
    return bytes(header) + original[54:]


def unpack_refpack(data):
    """Independent fixture decoder, not used by the production builder."""
    if data[4:6] != b"\x10\xfb":
        return data
    output, pos = bytearray(), 9
    while True:
        command = data[pos]
        pos += 1
        if command >= 0xfc:
            output.extend(data[pos:pos + (command & 3)])
            break
        if command >= 0xe0:
            length = ((command & 31) << 2) + 4
            output.extend(data[pos:pos + length])
            pos += length
            continue
        if command >= 0xc0:
            a, b, c = data[pos:pos + 3]
            pos += 3
            literal, offset, length = command & 3, ((command & 16) << 12) + (a << 8) + b + 1, ((command & 12) << 6) + c + 5
        elif command >= 0x80:
            a, b = data[pos:pos + 2]
            pos += 2
            literal, offset, length = a >> 6, ((a & 63) << 8) + b + 1, (command & 63) + 4
        else:
            a = data[pos]
            pos += 1
            literal, offset, length = command & 3, ((command & 96) << 3) + a + 1, ((command & 28) >> 2) + 3
        output.extend(data[pos:pos + literal])
        pos += literal
        for _ in range(length):
            output.append(output[-offset])
    return bytes(output)


def raw_nodes(path=TEMPLATE):
    data = path.read_bytes()
    count, offset = struct.unpack_from("<II", data, 36)
    result = []
    for index in range(count):
        t, group, low, high, start, size = struct.unpack_from("<6I", data, offset + index * 24)
        if t != 0xe86b1eef:
            result.append([(t, group, low, high), unpack_refpack(data[start:start + size])])
    return result


def write_nodes(path, nodes, old_index=False):
    data = bytearray(TEMPLATE.read_bytes()[:96])
    index = bytearray()
    for key, value in nodes:
        if old_index and key[0] == 0xac506764:
            count = struct.unpack_from("<I", value, 8)[0]
            value = struct.pack("<III", 0xdeadbeef, 1, count) + b"".join(value[12 + i * 16:24 + i * 16] for i in range(count))
        if old_index:
            index.extend(struct.pack("<5I", *key[:3], len(data), len(value)))
        else:
            index.extend(struct.pack("<6I", *key, len(data), len(value)))
        data.extend(value)
    struct.pack_into("<III", data, 36, len(nodes), len(data), len(index))
    struct.pack_into("<III", data, 48, 0, 0, 0)
    struct.pack_into("<I", data, 60, 1 if old_index else 2)
    data.extend(index)
    path.write_bytes(data)


class StandardInstallerTests(unittest.TestCase):
    def test_pinned_resources_reproducible_install_and_hash_rejection(self):
        extracted = Path(__file__).resolve().parents[2] / "artifacts/hair-validation/standard/ts2-standard-extracted"
        if not extracted.exists():
            self.skipTest("Extract the pinned game resources first")
        recipe = json.loads(RECIPES.read_text())["items"][0]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recipes = root / "recipes.json"
            atomic_json(recipes, {"items": [recipe]})
            install_standard(extracted, recipes, root / "first")
            first = StandardCatalog(root / "first").get(recipe["id"])
            # A new output location must not alter resource identities or bytes.
            install_standard(extracted, recipes, root / "second")
            second = StandardCatalog(root / "second").get(recipe["id"])
            self.assertEqual(first["template_sha256"], second["template_sha256"])
            install_standard(extracted, recipes, root / "first")
            altered = root / "altered"
            altered.mkdir()
            nodes = raw_nodes(extracted / (recipe["id"] + ".package"))
            gz = next(n for n in nodes if n[0][0] == 0xebcf3e27 and b"afhairponytail" in n[1])
            gz[1] = gz[1].replace(b"afhairponytail", b"xfhairponytail")
            write_nodes(altered / (recipe["id"] + ".package"), nodes)
            with self.assertRaisesRegex(ValueError, "source hashes"):
                install_standard(altered, recipes, root / "rejected")
            self.assertEqual(StandardCatalog(root / "rejected").items(), [])


class ProcessingTests(unittest.TestCase):
    def test_package_resize_preserves_rgb_alpha_and_mask_alignment(self):
        image = Image.new("RGBA", (16, 8), (120, 80, 60, 0))
        mask = Image.new("L", image.size, 0)
        mask.paste(255, (8, 0, 16, 8))
        template = Image.new("RGBA", (8, 8), (0, 0, 0, 85))
        fitted, fitted_mask = fit_package_inputs(image, template.size, mask)
        self.assertEqual(fitted.size, template.size)
        self.assertEqual(fitted.getpixel((0, 0)), (120, 80, 60, 0))
        self.assertEqual(fitted_mask.size, template.size)
        self.assertEqual((fitted_mask.getpixel((0, 0)), fitted_mask.getpixel((7, 0))), (0, 255))
        result = render(fitted, template, {}, "Dynamite", fitted_mask)[1]
        self.assertEqual(result.getpixel((0, 0)), (120, 80, 60, 85))
        self.assertNotEqual(result.getpixel((7, 0))[:3], (120, 80, 60))
        self.assertEqual(result.getchannel("A").tobytes(), template.getchannel("A").tobytes())
        self.assertEqual(render(fitted, template, {"png_alpha": True}, "Dynamite")[1].getchannel("A").getextrema(), (0, 0))
        same, same_mask = fit_package_inputs(image, image.size, mask)
        self.assertIs(same, image)
        self.assertIs(same_mask, mask)
        with self.assertRaisesRegex(ValueError, "mask must match"):
            fit_package_inputs(image, template.size, Image.new("L", template.size))

    def test_bmp_rgb_palette_grayscale_and_alpha(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "upload.texture"
            template = Image.new("RGBA", (2, 2), (0, 0, 0, 85))
            for mode, color in [("RGB", (120, 80, 60)), ("L", 73), ("P", 3)]:
                path.write_bytes(bmp((2, 2), color, mode))
                with Image.open(path) as original:
                    expected = original.convert("RGB")
                loaded = load_texture(path, (2, 2))
                self.assertEqual(loaded.convert("RGB").tobytes(), expected.tobytes())
                self.assertEqual(loaded.getchannel("A").getextrema(), (255, 255))
                self.assertEqual(render(loaded, template, {}, "TNT")[1].getchannel("A").getextrema(), (85, 85))
                self.assertEqual(render(loaded, template, {"png_alpha": True}, "TNT")[1].getchannel("A").getextrema(), (255, 255))
            path.write_bytes(bmp((2, 2), 73, "L"))
            self.assertEqual(load_texture(path, mask=True).tobytes(), bytes([73] * 4))
            path.write_bytes(bmp((2, 2)))
            with self.assertRaisesRegex(ValueError, "grayscale"):
                load_texture(path, mask=True)
            path.write_bytes(bmp_v5(alpha=85))
            self.assertEqual(load_texture(path).getpixel((0, 0)), (120, 80, 60, 85))
            path.write_bytes(bmp_v5_rgb((1024, 1024)))
            loaded = load_texture(path, (1024, 1024))
            self.assertEqual(loaded.size, (1024, 1024))
            self.assertEqual(loaded.getpixel((0, 0)), (120, 80, 60, 255))

    def test_bmp_profiles_and_invalid_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "upload.bmp"
            profiles = [ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()]
            display_p3 = Path("/System/Library/ColorSync/Profiles/Display P3.icc")
            if display_p3.exists():
                profiles.append(display_p3.read_bytes())
            for profile in profiles:
                path.write_bytes(bmp_v5(profile))
                expected = ImageCms.profileToProfile(Image.new("RGB", (1, 1), (120, 80, 60)),
                    ImageCms.ImageCmsProfile(io.BytesIO(profile)), ImageCms.createProfile("sRGB"), outputMode="RGB")
                self.assertEqual(load_texture(path).getpixel((0, 0)), (*expected.getpixel((0, 0)), 127))
            invalid_offset = bytearray(bmp_v5(profiles[0]))
            struct.pack_into("<I", invalid_offset, 126, 0xfffffff0)
            linked = bytearray(bmp_v5())
            struct.pack_into("<I", linked, 70, 0x4c494e4b)
            for payload in [bmp_v5(b"bad profile"), invalid_offset, linked]:
                path.write_bytes(payload)
                with self.assertRaisesRegex(ValueError, "profile"):
                    load_texture(path)
            for payload in [b"BM invalid", bmp((2, 2))[:54], bmp((2049, 1))]:
                path.write_bytes(payload)
                with self.assertRaises(ValueError):
                    load_texture(path)
            path.write_bytes(bmp((2, 2)))
            with self.assertRaisesRegex(ValueError, "dimensions"):
                load_texture(path, (512, 512))
            other = io.BytesIO()
            Image.new("RGB", (2, 2)).save(other, format="JPEG")
            path.write_bytes(other.getvalue())
            with self.assertRaisesRegex(ValueError, "PNG or BMP"):
                load_texture(path)

    def test_palette_and_author_samples(self):
        self.assertEqual(len(BY_NAME), 43)
        self.assertEqual(len(curves()), 48)
        self.assertEqual(curves()["Volatile"], (tuple(range(256)),) * 3)
        self.assertEqual(curves()["Dynamite"][0][0], 1)
        self.assertEqual(curves()["Dynamite"][0][255], 54)
        self.assertNotEqual(curves()["base:Grey"], curves()["base:arbitrary-grey"])
        for path in (ASSETS / "curves").rglob("Pooklet*"):
            if path.is_file() and not path.suffix:
                tables = parse_curve(path)
                self.assertTrue(all(len(t) == 256 and all(0 <= n <= 255 for n in t) for t in tables))

    def test_known_bases_and_independent_targets(self):
        image = Image.new("RGB", (1, 1), (70, 130, 190))
        for mode in ["Primer", "Grenade", "Incendiary", "Pooklet Grey"]:
            tables = curves()["base:" + mode.replace("Pooklet ", "")]
            self.assertEqual(prepare(image, {"base": mode}).getpixel((0, 0)), tuple(t[v] for t, v in zip(tables, (70, 130, 190))))
        template = Image.new("RGBA", (1, 1), (0, 0, 0, 17))
        one = render(image, template, {}, "Dynamite")[1]
        render(image, template, {}, "TNT")
        self.assertEqual(render(image, template, {}, "Dynamite")[1].tobytes(), one.tobytes())

    def test_arbitrary_levels_and_alpha_mask(self):
        image = Image.new("RGBA", (3, 1))
        image.putdata([(0, 0, 0, 0), (128, 128, 128, 128), (255, 255, 255, 255)])
        template = Image.new("RGBA", (3, 1), (0, 0, 0, 85))
        mask = Image.new("L", (3, 1))
        mask.putdata([0, 128, 255])
        base, target = render(image, template, {}, "TNT", mask)
        self.assertEqual(target.getchannel("A").tobytes(), bytes([85] * 3))
        self.assertEqual(target.getpixel((0, 0))[:3], (0, 0, 0))
        self.assertEqual(render(image, template, {"png_alpha": True}, "TNT")[1].getchannel("A").tobytes(), bytes([0, 128, 255]))
        arbitrary = {"base": "Arbitrary texture", "black": 20, "white": 230, "gamma": 1}
        result = prepare(image, arbitrary)
        self.assertEqual(result.getpixel((0, 0)), tuple(t[0] for t in curves()["base:arbitrary-grey"]))
        self.assertEqual(result.getpixel((2, 0)), tuple(t[255] for t in curves()["base:arbitrary-grey"]))
        self.assertNotEqual(prepare(image, {**arbitrary, "gamma": 2}).tobytes(), result.tobytes())
        for bad in [{"black": 255}, {"white": 0}, {"gamma": float("nan")}, {"gamma": 0}, {"png_alpha": 1}, {"base": "unknown"}]:
            with self.assertRaises(ValueError):
                settings(bad)

    def test_profile_conversion_and_invalid_images(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "image.png"
            profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
            path.write_bytes(png((2, 2), icc_profile=profile))
            self.assertEqual(load_texture(path).getpixel((0, 0)), (120, 80, 60, 127))
            # Use a real non-sRGB display profile if the host provides one.
            display_p3 = Path("/System/Library/ColorSync/Profiles/Display P3.icc")
            if display_p3.exists():
                path.write_bytes(png((2, 2), icc_profile=display_p3.read_bytes()))
                expected = ImageCms.profileToProfile(Image.new("RGB", (2, 2), (120, 80, 60)), str(display_p3), ImageCms.createProfile("sRGB"), outputMode="RGB")
                self.assertEqual(load_texture(path).getpixel((0, 0))[:3], expected.getpixel((0, 0)))
            path.write_bytes(png((2, 2), icc_profile=b"invalid ICC"))
            with self.assertRaisesRegex(ValueError, "profile"):
                load_texture(path)
            path.write_bytes(png((2, 2)))
            with self.assertRaises(ValueError):
                load_texture(path, (512, 512))
            with self.assertRaisesRegex(ValueError, "grayscale"):
                load_texture(path, mask=True)


@unittest.skipUnless(TEMPLATE.exists() and BUILDER.exists(), "Install pinned hair assets and build the Rust helper first")
class GameMeshCatalogTests(unittest.TestCase):
    def test_installed_mesh_index_and_unambiguous_legacy_references(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "game"
            directory = game / "Base/TSData/Res/Sims3D"
            directory.mkdir(parents=True)
            path = directory / "Sims06.package"
            key = (0xe519c933, 0x1c0532fa, 0xff001122, 0x12345678)
            payload = b"mesh resource fixture"
            write_nodes(path, [[key, payload]])
            # A user Downloads package is never treated as installed game data.
            (game / "Downloads").mkdir()
            (game / "Downloads/fake.package").write_bytes(b"invalid")
            catalog = root / "catalog.json"
            info = index_game(game, catalog)
            self.assertEqual(len(info["resources"]), 1)
            legacy = "e519c933-1c0532fa-00000000ff001122"
            resolved = resolve_game_meshes({legacy}, catalog)
            self.assertEqual(resolved[0]["canonical_key"], "e519c933-1c0532fa-12345678ff001122")
            self.assertEqual(resolved[0]["requirement"], "Base game")
            self.assertEqual(resolved[0]["sha256"], hashlib.sha256(payload).hexdigest())
            self.assertEqual(resolve_game_meshes({"e519c933-1c0532fa-00000000ffffffff"}, catalog), [])
            write_nodes(path, [[key, payload], [(key[0], key[1], key[2], 0x87654321), b"different mesh"]])
            index_game(game, catalog)
            self.assertEqual(resolve_game_meshes({legacy}, catalog), [], "Ambiguous low-instance matches must not waive missing dependencies")
            malformed = bytearray(path.read_bytes())
            struct.pack_into("<I", malformed, 40, len(malformed) + 100)
            path.write_bytes(malformed)
            with self.assertRaisesRegex(ValueError, "index"):
                index_game(game, catalog)


@unittest.skipUnless(TEMPLATE.exists() and BUILDER.exists(), "Install pinned hair assets and build the Rust helper first")
class HairIntegrationTests(unittest.TestCase):
    def test_embedded_standard_textures_need_no_image_upload(self):
        candidates = [("pooklet-mg-swirl", "Primer")]
        if StandardCatalog().items():
            candidates.append((StandardCatalog().items()[0]["id"], "Volatile"))
        for template_id, expected_base in candidates:
            with self.subTest(template=template_id):
                detail = self.client.get(f"/api/v1/hair/templates/{template_id}").json()
                self.assertEqual(detail["input_base"], expected_base)
                response = self.client.post("/api/v1/hair/jobs", data={"creator": "Embedded", "hair_name": "Standard", "template_id": template_id})
                self.assertEqual(response.status_code, 201, response.text)
                job = response.json()
                self.assertEqual(job["texture_source"], "embedded")
                base = f"/api/v1/hair/jobs/{job['id']}"
                directory = self.root / "hair" / job["id"]
                self.assertFalse((directory / "uploads").exists())
                response = self.client.post(base + "/preview", json={"colors": ["Dynamite", "TNT"], "settings": {}})
                self.assertEqual(response.status_code, 200, response.text[:1000])
                for preview in response.json()["previews"]:
                    self.assertEqual(preview["settings"]["base"], expected_base)
                    image = load_texture(directory / "template-textures" / (preview["slot"] + ".png"))
                    expected = render(image, image, {"base": expected_base}, "TNT")
                    self.assertEqual(preview["original"], thumbnail(image))
                    self.assertEqual(preview["base"], thumbnail(expected[0]))
                    self.assertEqual(preview["targets"][-1]["image"], thumbnail(expected[1]))
                # A prepared embedded job cannot silently switch to uploaded images.
                slot = job["inspection"]["textures"][0]["id"]
                self.assertEqual(self.client.post(base + "/assets", files={slot: ("unexpected.png", self.example)}).status_code, 409)
                data = self.build(job)
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    self.assertIn("No separate texture images are required", archive.read("README.txt").decode())
                self.assertFalse((directory / "uploads").exists())

    def test_rose_bundle_embedded_textures_game_meshes_and_dbpf_12(self):
        fixture = Path(__file__).resolve().parents[2] / "artifacts/hair-validation/embedded-bundle"
        files = [fixture / "mesh_rosehair_0124.package", fixture / "recolor_3555b7d0_rose72.package"]
        if not all(p.exists() for p in files) or not CATALOG.is_file():
            self.skipTest("Fetch the remote 2texture bundle and install the verified game mesh catalog")
        hashes = ["8dfe569c368f4685ddcce32f9f73ee489a95081a6b189478d16a91773ae607a8",
                  "722d79288b7aad81f4ac1e04ba7bc7f7caa82d25fb77f74b52b26b563a67798f"]
        self.assertEqual([hashlib.sha256(p.read_bytes()).hexdigest() for p in files], hashes)
        entries = [(p.name, p.read_bytes()) for p in files]
        response = self.import_packages(entries)
        self.assertEqual(response.status_code, 201, response.text)
        item = response.json()["items"][0]
        again = self.import_packages(list(reversed(entries)))
        self.assertEqual(again.json()["items"][0]["id"], item["id"])
        self.assertEqual(len(item["meshes"]), 1)
        self.assertEqual(len(item["game_meshes"]), 2)
        self.assertTrue(all(record["requirement"] == "Base game" for record in item["game_meshes"]))
        # Game-group numbers alone are not enough to waive missing dependencies.
        with patch("package_creation.hair.library.resolve_game_meshes", return_value=[]):
            self.assertEqual(self.import_packages(entries).status_code, 422)
        response = self.client.post("/api/v1/hair/jobs", data={"creator": "Embedded", "hair_name": "Rose72", "template_id": item["id"]})
        self.assertEqual(response.status_code, 201, response.text)
        job = response.json()
        slots = job["inspection"]["textures"]
        self.assertEqual(len(slots), 5)
        self.assertEqual(sorted((s["width"], s["height"]) for s in slots), [(512, 512)] * 3 + [(1024, 512)] * 2)
        self.assertEqual(sum(s["ages"] == [1] for s in slots), 1)
        self.assertEqual(sum(s["ages"] == [16] for s in slots), 2)
        self.assertTrue(all(s["format"] == "DXT3" for s in slots))
        colors = [*FAMILIES[0], "TNT", "Mail Bomb"]
        base = f"/api/v1/hair/jobs/{job['id']}"
        response = self.client.post(base + "/preview", json={"colors": colors, "settings": {}})
        self.assertEqual(response.status_code, 200, response.text[:1000])
        self.assertTrue(all(s["settings"]["base"] == "Arbitrary texture" for s in response.json()["previews"]))
        self.assertEqual(len(response.json()["previews"]), 5)
        directory = self.root / "hair" / job["id"]
        self.assertFalse((directory / "uploads").exists())
        data = self.build(job)
        report = json.loads((directory / "validation.json").read_text())
        self.assertEqual(len(report["game_meshes"]), 2)
        self.assertTrue(all(t["alpha_max_error"] == 0 for p in report["packages"] for t in p["textures"]))
        self.assertTrue(all(t["mips"] in {10, 11} for p in report["packages"] for t in p["textures"]))
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual(len(archive.namelist()), 8)
            mesh = archive.read("Meshes/" + files[0].name)
            self.assertEqual(hashlib.sha256(mesh).hexdigest(), hashes[0])
            instructions = archive.read("README.txt").decode()
            self.assertIn("Base game", instructions)
            self.assertIn("No separate texture images", instructions)
            self.assertNotIn("uploaded 512", instructions)
            for color in colors:
                package = archive.read(f"Embedded_Rose72_{color.replace(' ', '')}.package")
                self.assertEqual(struct.unpack_from("<II", package, 4), (1, 2))
        self.assertEqual([hashlib.sha256(p.read_bytes()).hexdigest() for p in files], hashes)

    def test_standard_catalog_styles_build_and_coexist(self):
        catalog = StandardCatalog()
        items = catalog.items()
        if not items:
            self.skipTest("Install the standard game catalog first")
        expected = {item["id"] for item in json.loads(RECIPES.read_text())["items"]}
        self.assertTrue(expected.issubset({item["id"] for item in items}))
        listing = self.client.get("/api/v1/hair/templates").json()
        self.assertTrue(expected.issubset({item["id"] for item in listing["items"]}))
        self.assertEqual(listing["id"], "pooklet-mg-swirl")
        seen_keys, seen_meshes, reports = set(), set(), []
        colors = [*FAMILIES[0], "TNT", "Mail Bomb"]
        evidence = Path(__file__).resolve().parents[2] / "artifacts/hair-validation/standard"
        evidence.mkdir(parents=True, exist_ok=True)
        for item in items:
            with self.subTest(style=item["id"]):
                details = self.client.get("/api/v1/hair/templates/" + item["id"])
                self.assertEqual(details.status_code, 200, details.text)
                self.assertTrue(all(t["preview"].startswith("data:image/png") for t in details.json()["inspection"]["textures"]))
                example = self.client.get(item["example_url"])
                self.assertEqual(example.status_code, 200)
                with zipfile.ZipFile(io.BytesIO(example.content)) as archive:
                    self.assertEqual(len(archive.namelist()), len(item["inspection"]["textures"]) + 1)
                    self.assertIn("Volatile", archive.read("README.txt").decode())
                response = self.client.post("/api/v1/hair/jobs", data={"creator": "Test", "hair_name": item["id"], "template_id": item["id"]})
                self.assertEqual(response.status_code, 201, response.text)
                job = response.json()
                base = f"/api/v1/hair/jobs/{job['id']}"
                self.assertEqual(job["supported_ages"], ["Teen", "Young Adult", "Adult", "Elder"])
                self.assertEqual(job["meshes"], [])
                files = {}
                for index, slot in enumerate(job["inspection"]["textures"]):
                    image = load_texture(catalog.directory(item["id"]) / "template-textures" / (slot["id"] + ".png"))
                    data = io.BytesIO()
                    # Mix PNG and the user's RGB V5 BMP type in every template.
                    if index:
                        data.write(bmp_v5_rgb(image.size))
                        files[slot["id"]] = ("input.bmp", data.getvalue(), "image/bmp")
                    else:
                        image.save(data, format="PNG")
                        files[slot["id"]] = ("input.png", data.getvalue(), "image/png")
                missing = dict(list(files.items())[:1])
                self.assertEqual(self.client.post(base + "/assets", files=missing).status_code, 422)
                response = self.client.post(base + "/assets", files=files)
                self.assertEqual(response.status_code, 200, response.text)
                response = self.client.post(base + "/preview", json={"colors": colors, "settings": {}})
                self.assertEqual(response.status_code, 200, response.text[:500])
                for slot, preview in zip(job["inspection"]["textures"], response.json()["previews"]):
                    if slot["ages"] == [16]:
                        image = load_texture(catalog.directory(item["id"]) / "template-textures" / (slot["id"] + ".png"))
                        uploaded = self.root / "elder-preview-input.texture"
                        uploaded.write_bytes(files[slot["id"]][1])
                        expected_grey = thumbnail(render(load_texture(uploaded), image, {}, "Mail Bomb")[1])
                        for target in preview["targets"]:
                            if target["color"] in FAMILIES[0]:
                                self.assertEqual(target["rendered_color"], "Mail Bomb")
                                self.assertEqual(target["image"], expected_grey)
                            elif target["color"] == "TNT":
                                self.assertEqual(target["rendered_color"], "TNT")
                data = self.build(job)
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    self.assertEqual(len(archive.namelist()), len(colors) + 1)
                    self.assertIn("standard base-game mesh", archive.read("README.txt").decode())
                report = json.loads((self.root / "hair" / job["id"] / "validation.json").read_text())
                families = {p["family"] for p in report["packages"] if p["color"] in FAMILIES[0]}
                self.assertEqual(len(families), 1)
                mesh_set = tuple(item["inspection"]["external_meshes"])
                self.assertNotIn(mesh_set, seen_meshes)
                seen_meshes.add(mesh_set)
                for package in report["packages"]:
                    keys = set(package["resource_keys"])
                    self.assertFalse(keys & seen_keys)
                    seen_keys.update(keys)
                    if package["color"] == "Mail Bomb":
                        self.assertTrue(set(package["external_meshes"]).issubset(item["inspection"]["external_meshes"]))
                        self.assertEqual(len(package["external_meshes"]), 2)
                    else:
                        self.assertEqual(package["external_meshes"], item["inspection"]["external_meshes"])
                    expected_ages = {16} if package["color"] == "Mail Bomb" else {4, 16, 72}
                    self.assertEqual({a["age"] for a in package["ages"]}, expected_ages)
                    self.assertTrue(all(t["mips"] == 10 and t["format"] == "DXT3" and t["alpha_max_error"] == 0 for t in package["textures"]))
                reports.append({"id": item["id"], "validation": report})
                (evidence / (item["id"] + "_Recolors.zip")).write_bytes(data)
        atomic_json(evidence / "catalog-build-validation.json", {"styles": reports, "gameplay": "not tested"})
        self.assertEqual(self.client.get("/api/v1/hair/templates/standard-missing").status_code, 404)
        self.assertEqual(self.client.post("/api/v1/hair/jobs", data={"creator": "C", "hair_name": "H", "template_id": "standard-missing"}).status_code, 422)

    def test_saved_hairstyle_selection_and_library_survives_job_expiry(self):
        before = len(self.client.get("/api/v1/hair/templates").json()["items"])
        response = self.client.post("/api/v1/hair/templates", data={"label": "My saved hair"},
                                    files={"file": ("my-hair.package", TEMPLATE.read_bytes())})
        self.assertEqual(response.status_code, 201, response.text)
        item = response.json()["items"][0]
        again = self.client.post("/api/v1/hair/templates", files={"file": ("my-hair.package", TEMPLATE.read_bytes())})
        self.assertEqual(again.json()["items"][0]["id"], item["id"])
        catalog = self.client.get("/api/v1/hair/templates").json()
        self.assertEqual(len(catalog["items"]), before + 1)
        response = self.client.post("/api/v1/hair/jobs", data={"creator": "Creator", "hair_name": "Saved", "template_id": item["id"]})
        self.assertEqual(response.status_code, 201, response.text)
        job = self.prepare_job(response.json(), ["Dynamite"])
        self.assertEqual(job["template_label"], "My saved hair")
        self.build(job)
        self.assertEqual(self.client.delete(f"/api/v1/hair/jobs/{job['id']}").status_code, 200)
        self.app.state.hair_manager.expire()
        library = HairLibrary(self.root / "hair-library")
        self.assertEqual(library.get(item["id"])["label"], "My saved hair")
        self.assertEqual(self.client.get(item["example_url"]).status_code, 200)
        self.assertEqual(self.client.get("/api/v1/hair/templates/" + item["id"]).status_code, 200)
        self.assertEqual(self.client.post("/api/v1/hair/jobs", data={"creator": "C", "hair_name": "H", "template_id": "missing"}).status_code, 422)

    def import_packages(self, entries, **data):
        return self.client.post("/api/v1/hair/templates", data=data,
                                files=[("file", (name, content)) for name, content in entries])

    def custom_files(self):
        root = Path(__file__).resolve().parents[2] / "artifacts/hair-validation/peggy4033"
        paths = [root / "peggy_fh080712_p001_mesh_letoedit.package", root / "f_leto_peggy4033_volatile.package"]
        if not all(p.exists() for p in paths):
            self.skipTest("User-supplied custom mesh and recolor are not installed on this host")
        return [(p.name, p.read_bytes()) for p in paths]

    def test_library_rejects_unsafe_conflicting_or_missing_inputs(self):
        before = len(self.client.get("/api/v1/hair/templates").json()["items"])
        zipped = io.BytesIO()
        with zipfile.ZipFile(zipped, "w") as z:
            z.writestr("hair.package", TEMPLATE.read_bytes())
        for entries in [[("../outside.package", TEMPLATE.read_bytes())],
                        [("hair.package", TEMPLATE.read_bytes()), ("HAIR.package", TEMPLATE.read_bytes())],
                        [("readme.txt", b"no packages")], [("bad.package", b"not DBPF")],
                        [("old.zip", zipped.getvalue())], [("disguised.package", zipped.getvalue())]]:
            response = self.import_packages(entries)
            self.assertEqual(response.status_code, 422, response.text)
        files = self.custom_files()
        for entries in [[files[0]], [files[0], ("wrong-recolor.package", TEMPLATE.read_bytes())],
                        [*files, ("duplicate-mesh.package", files[0][1])]]:
            response = self.import_packages(entries)
            self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(len(self.client.get("/api/v1/hair/templates").json()["items"]), before)
        self.assertEqual(self.client.post("/api/v1/hair/templates", headers={"Origin": "http://other.example"}).status_code, 403)
        self.assertEqual(self.client.post("/api/v1/hair/templates", headers={"Origin": "http://evil.test"}).status_code, 403)

    def test_custom_packages_mesh_references_png_bmp_and_saved_zip_compatibility(self):
        entries = self.custom_files()
        response = self.import_packages(entries, label="Custom hair")
        self.assertEqual(response.status_code, 201, response.text)
        item = response.json()["items"][0]
        self.assertEqual(self.import_packages(list(reversed(entries))).json()["items"][0]["id"], item["id"])
        self.assertEqual(item["ages"], ["Toddler", "Child", "Teen", "Young Adult", "Adult"])
        self.assertEqual(len(item["meshes"]), 1)
        self.assertEqual(len(item["inspection"]["external_meshes"]), 8)
        source = self.root / "hair-library" / item["id"]
        slot = item["inspection"]["textures"][0]
        self.assertEqual((slot["width"], slot["height"], slot["format"]), (1024, 1024, "DXT5"))
        # Older ZIP imports have the same content-derived identity and no source_filenames field.
        legacy = {k: v for k, v in item.items() if k != "source_filenames"}
        legacy["source_filename"] = "previous-upload.zip"
        atomic_json(source / "item.json", legacy)
        reimport = self.import_packages(entries).json()["items"][0]
        self.assertEqual(reimport, legacy)
        for texture_name, texture in [("source.png", (source / "template-textures" / (slot["id"] + ".png")).read_bytes()),
                                      ("source.bmp", bmp_v5_rgb((1024, 1024)))]:
            with self.subTest(texture=texture_name):
                response = self.client.post("/api/v1/hair/jobs", data={"creator": "Test", "hair_name": "CustomHair", "template_id": item["id"]})
                self.assertEqual(response.status_code, 201, response.text)
                job = response.json()
                base = f"/api/v1/hair/jobs/{job['id']}"
                self.assertEqual(self.client.post(base + "/assets", files={slot["id"]: (texture_name, texture)}).status_code, 200)
                self.assertEqual(self.client.post(base + "/preview", json={"colors": [*FAMILIES[0], "TNT"], "settings": {}}).status_code, 200)
                self.assertEqual(self.client.post(base + "/preview", json={"colors": ["Mail Bomb"], "settings": {}}).status_code, 422)
                content = self.build(job)
                evidence = Path(__file__).resolve().parents[2] / "artifacts/hair-validation/package-only"
                evidence.mkdir(parents=True, exist_ok=True)
                (evidence / ("Custom_" + texture_name + "_Recolors.zip")).write_bytes(content)
                with zipfile.ZipFile(io.BytesIO(content)) as output:
                    self.assertEqual(len(output.namelist()), 7)
                    mesh = item["meshes"][0]
                    self.assertEqual(output.read("Meshes/" + mesh["filename"]), entries[0][1])
                    self.assertIn("no Elder entry", output.read("README.txt").decode())
                report = json.loads((self.root / "hair" / job["id"] / "validation.json").read_text())
                for package in report["packages"]:
                    self.assertEqual(package["external_meshes"], item["inspection"]["external_meshes"])
                    self.assertEqual({a["age"] for a in package["ages"]}, {1, 2, 4, 8, 64})
                    self.assertTrue(all(t["format"] == "DXT5" and t["mips"] == 11 and t["alpha_max_error"] <= 36 and t["alpha_mean_error"] <= 8 for t in package["textures"]))
                self.assertEqual(len(report["meshes"]), 1)
                atomic_json(evidence / ("custom-" + texture_name + "-validation.json"), report)

    def test_multiple_mesh_packages_and_recolors(self):
        entries = self.custom_files()
        mesh_path = self.root / "mesh.package"
        mesh_path.write_bytes(entries[0][1])
        nodes = raw_nodes(mesh_path)
        # Distribute all four resource types across complete mesh packages.
        # References can cross files, so the whole uploaded dependency set must resolve them.
        by_type = {kind: [n for n in nodes if n[0][0] == kind] for kind in {n[0][0] for n in nodes}}
        self.assertTrue(all(len(group) == 4 for group in by_type.values()))
        parts = []
        for index in range(4):
            path = self.root / f"mesh-{index}.package"
            write_nodes(path, [group[index] for group in by_type.values()])
            parts.append((path.name, path.read_bytes()))
        # A second recolor package with a different DBPF timestamp, identical valid resources.
        other = bytearray(entries[1][1])
        struct.pack_into("<I", other, 24, 123456)
        response = self.import_packages([*parts, entries[1], ("another-recolor.package", bytes(other))])
        self.assertEqual(response.status_code, 201, response.text)
        items = response.json()["items"]
        self.assertEqual(len(items), 2)
        self.assertEqual(len({i["id"] for i in items}), 2)
        self.assertTrue(all(len(i["meshes"]) == 4 for i in items))
        self.assertEqual(self.import_packages([*parts[:-1], entries[1]]).status_code, 422)
        job = self.client.post("/api/v1/hair/jobs", data={"creator": "C", "hair_name": "MultiMesh", "template_id": items[0]["id"]}).json()
        self.example = png((1024, 1024))
        job = self.prepare_job(job, ["Volatile"])
        with zipfile.ZipFile(io.BytesIO(self.build(job))) as archive:
            self.assertEqual(len(archive.namelist()), 6)
            for name, content in parts:
                self.assertEqual(archive.read("Meshes/" + name), content)

    def test_direct_package_limits_and_transactional_import(self):
        entries = [(f"hair{i}.package", b"x") for i in range(65)]
        self.assertEqual(self.import_packages(entries).status_code, 422)
        library = HairLibrary(self.root / "limits-library")
        paths = []
        for i in range(3):
            path = self.root / f"large-{i}.package"
            with path.open("wb") as f:
                f.truncate(64 * 1024 * 1024)
            paths.append((path, path.name))
        with self.assertRaisesRegex(ValueError, "128 MiB total"):
            library.import_files(paths)
        with paths[0][0].open("ab") as f:
            f.write(b"x")
        with self.assertRaisesRegex(ValueError, "64 MiB per-file"):
            library.import_files(paths[:1])
        self.assertEqual(library.items(), [])
        self.assertEqual(list(library.root.iterdir()), [])
        limited = dataclasses.replace(self.config, spool_root=self.root / "limited", hair_upload_bytes=1024)
        with TestClient(create_app(limited), headers=self.client.headers) as client:
            response = client.post("/api/v1/hair/templates", files=[("file", ("a.package", b"x" * 600)), ("file", ("b.package", b"x" * 600))])
            self.assertEqual(response.status_code, 413, response.text)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config = ServiceConfig(spool_root=self.root, allowed_hosts=("testserver",), readiness_check=lambda: {})
        self.app = create_app(self.config)
        self.context = TestClient(self.app, headers={"Origin": "http://testserver"})
        self.client = self.context.__enter__()
        self.example = (ASSETS / "Swirl_Volatile_Example.png").read_bytes()

    def tearDown(self):
        self.context.__exit__(None, None, None)
        self.temporary.cleanup()

    def create(self, template=None):
        response = self.client.post("/api/v1/hair/jobs", data={"creator": "Creator", "hair_name": "Swirl"},
                                    files={"template": ("custom.package", template)} if template else None)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def prepare_job(self, job, colors, config=None):
        base = f"/api/v1/hair/jobs/{job['id']}"
        response = self.client.post(base + "/assets", files={s["id"]: ("texture.png", self.example, "image/png") for s in job["inspection"]["textures"]})
        self.assertEqual(response.status_code, 200, response.text)
        response = self.client.post(base + "/preview", json={"colors": colors, "settings": config or {}})
        self.assertEqual(response.status_code, 200, response.text[:1000])
        return response.json()

    def wait(self, job_id):
        # Real multi-atlas fixtures can exceed twenty seconds on a busy server.
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            job = self.client.get(f"/api/v1/hair/jobs/{job_id}").json()
            if job["state"] in {"failed", "complete", "cancelled"}:
                return job
            time.sleep(.05)
        self.fail(f"Hair build did not finish within 120 seconds: {job}")

    def build(self, job):
        base = f"/api/v1/hair/jobs/{job['id']}"
        self.assertEqual(self.client.post(base + "/build").status_code, 202)
        result = self.wait(job["id"])
        self.assertEqual(result["state"], "complete", result["message"])
        response = self.client.get(base + "/download")
        self.assertEqual(response.status_code, 200)
        return response.content

    def test_texture_export_removed_from_ui_and_api(self):
        html = self.client.get("/").text
        self.assertNotIn("Body Shop", html)
        self.assertNotIn('id="hair-output"', html)
        self.assertIn("Upload packages", html)
        self.assertIn('accept=".package" multiple', html)
        self.assertNotIn("texture_export", self.client.get("/api/v1/hair/templates").json())
        for fields in [{"output_kind": "textures"}, {"output_kind": "invalid"}, {"output_format": "BMP"},
                       {"output_kind": "packages", "output_format": "PNG"}]:
            response = self.client.post("/api/v1/hair/jobs", data={"creator": "C", "hair_name": "H", **fields})
            self.assertEqual(response.status_code, 422)
            self.assertIn("Texture export was removed", response.text)
        response = self.client.post("/api/v1/hair/jobs", data={"creator": "C", "hair_name": "H", "output_kind": "packages"})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["output_kind"], "packages")

    def test_legacy_texture_jobs_download_expiry_and_no_resume(self):
        root = self.root / "legacy"
        config = dataclasses.replace(self.config, spool_root=root)
        updated = time.time() - 30
        for i, state in enumerate(["complete", "draft", "ready", "queued", "building", "failed", "cancelled"]):
            directory = root / "hair" / f"{i:032x}"
            directory.mkdir(parents=True)
            atomic_json(directory / "job.json", {"creator": "C", "hair_name": "H", "output_kind": "textures", "output_format": "BMP",
                        "inspection": {"ages": [], "textures": []}, "template_label": "Old upload", "requirements": "Old requirements",
                        "colors": ["Volatile"], "prepared": True, "assignments": {}})
            atomic_json(directory / "status.json", {"id": directory.name, "state": state, "message": "Old status", "updated": updated})
            if state == "complete":
                with zipfile.ZipFile(directory / "recolors.zip", "w") as archive:
                    archive.writestr("C_H_Volatile.bmp", bmp((1, 1)))
        with TestClient(create_app(config), headers=self.client.headers) as client:
            for i in range(7):
                base = f"/api/v1/hair/jobs/{i:032x}"
                status = client.get(base).json()
                self.assertEqual(status["state"], "complete" if i == 0 else "failed")
                self.assertEqual(status["updated"], updated)
                self.assertEqual(status["zip_filename"], "C_H_Textures.zip")
                for suffix in ["assets", "preview", "build"]:
                    response = client.post(base + "/" + suffix)
                    self.assertEqual(response.status_code, 410, response.text)
                    self.assertIn("Texture export was removed", response.text)
                download = client.get(base + "/download")
                self.assertEqual(download.status_code, 200 if i == 0 else 409)
                if i == 0:
                    self.assertIn("C_H_Textures.zip", download.headers["content-disposition"])
                    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
                        self.assertEqual(archive.namelist(), ["C_H_Volatile.bmp"])
            directory = root / "hair" / f"{0:032x}"
            state = json.loads((directory / "status.json").read_text())
            atomic_json(directory / "status.json", {**state, "updated": updated - 3601})
            client.app.state.hair_manager.expire()
            self.assertEqual(client.get(f"/api/v1/hair/jobs/{0:032x}").status_code, 404)
            self.assertEqual(client.delete(f"/api/v1/hair/jobs/{1:032x}").status_code, 200)

    def test_four_colors_full_palette_and_batch_coexistence(self):
        first = self.prepare_job(self.create(), FAMILIES[0])
        content = self.build(first)
        four = zipfile.ZipFile(io.BytesIO(content))
        self.assertEqual(four.namelist(), ["Creator_Swirl_" + c.replace(" ", "") + ".package" for c in FAMILIES[0]] + ["README.txt"])
        self.assertIn("Mansion & Garden", four.read("README.txt").decode())
        second = self.prepare_job(self.create(), list(BY_NAME))
        content_all = self.build(second)
        archive = zipfile.ZipFile(io.BytesIO(content_all))
        self.assertEqual(len(archive.namelist()), 44)
        self.assertTrue(all(name.endswith(".package") or name == "README.txt" for name in archive.namelist()))
        first_report = json.loads((self.root / "hair" / first["id"] / "validation.json").read_text())
        second_report = json.loads((self.root / "hair" / second["id"] / "validation.json").read_text())
        keys1 = {key for p in first_report["packages"] for key in p["resource_keys"]}
        keys2 = {key for p in second_report["packages"] for key in p["resource_keys"]}
        source_keys = {f"{k[0]:08x}-{k[1]:08x}-{k[3]:08x}{k[2]:08x}" for k, _ in raw_nodes()}
        self.assertFalse(keys1 & keys2 or keys1 & source_keys or keys2 & source_keys)
        self.assertEqual(len({p["family"] for p in first_report["packages"]}), 1)
        self.assertEqual(len({p["hairtone"] for p in second_report["packages"]}), 43)
        for package in second_report["packages"]:
            self.assertNotEqual(package["hairtone"], package["family"])
            expected = {16} if BY_NAME[package["color"]]["kind"] == "grey" else {8, 16, 64}
            self.assertEqual({a["age"] for a in package["ages"]}, expected)
            for texture in package["textures"]:
                self.assertEqual(texture["mips"], 10)
                self.assertEqual(texture["format"], "DXT3")
                self.assertEqual(texture["alpha_max_error"], 0)
        evidence = Path(__file__).resolve().parents[2] / "artifacts/hair-validation"
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / "Creator_Swirl_Recolors.zip").write_bytes(content)
        (evidence / "Creator_Swirl_All43_Recolors.zip").write_bytes(content_all)
        atomic_json(evidence / "four-color-validation.json", first_report)
        atomic_json(evidence / "full-palette-validation.json", second_report)

    def test_bmp_upload_mask_preview_and_build(self):
        job = self.create()
        base = f"/api/v1/hair/jobs/{job['id']}"
        slot = job["inspection"]["textures"][0]["id"]
        # Masks match the input, which can differ from the template. Failed
        # assignments must remain retryable without persisting partial sizes.
        bad = self.client.post(base + "/assets", files={slot: ("texture.bmp", bmp((1024, 1024)), "image/bmp"),
            "mask:" + slot: ("wrong.bmp", bmp(color=255, mode="L"), "image/bmp")})
        self.assertEqual(bad.status_code, 422)
        self.assertIn("dimensions", bad.text)
        response = self.client.post(base + "/assets", files={
            slot: ("texture.bmp", bmp_v5_rgb((1024, 1024)), "image/bmp"),
            "mask:" + slot: ("mask.bmp", bmp((1024, 1024), color=255, mode="L"), "image/bmp"),
        })
        self.assertEqual(response.status_code, 200, response.text)
        metadata = response.json()["inspection"]["textures"][0]
        self.assertEqual((metadata["source_width"], metadata["source_height"]), (1024, 1024))
        self.assertEqual((metadata["width"], metadata["height"]), (512, 512))
        response = self.client.post(base + "/preview", json={"colors": FAMILIES[0], "settings": {}})
        self.assertEqual(response.status_code, 200, response.text[:1000])
        directory = self.root / "hair" / job["id"]
        data = self.build(job)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual(len(archive.namelist()), 5)
            self.assertIn("Alpha source: template", archive.read("README.txt").decode())
            self.assertIn("uploaded 1024 by 1024, package 512 by 512", archive.read("README.txt").decode())
        report = json.loads((directory / "validation.json").read_text())
        self.assertEqual(len(report["packages"]), 4)
        self.assertTrue(all(t["alpha_max_error"] == 0 for p in report["packages"] for t in p["textures"]))
        stored = json.loads((directory / "job.json").read_text())
        template = load_texture(directory / "template-textures" / f"{slot}.png")
        input_image = load_texture(directory / "uploads" / stored["assignments"][slot])
        self.assertEqual(input_image.size, (1024, 1024))
        input_image, _ = fit_package_inputs(input_image, template.size)
        self.assertEqual(render(input_image, template, {}, "Dynamite")[1].getchannel("A").tobytes(), template.getchannel("A").tobytes())

    def test_custom_multiple_texture_slots_and_missing_assignments(self):
        nodes = raw_nodes()
        original = next(n for n in nodes if n[0][0] == 0x1c4a276c)
        key = original[0]
        nodes.append([(key[0], key[1], key[2] + 1, key[3]), original[1].replace(b"0x6381731b", b"0x6381731c")])
        adult = next(n for n in nodes if n[0][0] == 0x49596978 and n[0][2] == 0xff6ead01)
        adult[1] = adult[1].replace(b"0x6381731b", b"0x6381731c")
        path = self.root / "multiple.package"
        write_nodes(path, nodes)
        job = self.create(path.read_bytes())
        self.assertEqual(len(job["inspection"]["textures"]), 2)
        base = f"/api/v1/hair/jobs/{job['id']}"
        slot = job["inspection"]["textures"][0]["id"]
        self.assertEqual(self.client.post(base + "/assets", files={slot: ("one.png", self.example)}).status_code, 422)
        self.prepare_job(job, ["Dynamite", "TNT", "Pipe Bomb"])
        self.build(job)

    def test_three_atlases_with_independent_bases_masks_and_preview_build_parity(self):
        nodes = raw_nodes()
        original = next(n for n in nodes if n[0][0] == 0x1c4a276c)
        key = original[0]
        for index, material_id in [(1, 0xff6ead01), (2, 0xffdc13aa)]:
            alias = f"0x{0x6381731b + index:08x}".encode()
            nodes.append([(key[0], key[1], key[2] + index, key[3]), original[1].replace(b"0x6381731b", alias)])
            material = next(n for n in nodes if n[0][0] == 0x49596978 and n[0][2] == material_id)
            material[1] = material[1].replace(b"0x6381731b", alias)
        # A fourth embedded texture has no active material route.
        nodes.append([(key[0], key[1], key[2] + 3, key[3]), original[1].replace(b"0x6381731b", b"0x6381731e")])
        path = self.root / "three-atlases.package"
        write_nodes(path, nodes)
        job = self.create(path.read_bytes())
        slots = job["inspection"]["textures"]
        self.assertEqual(len(slots), 3)
        self.assertEqual({tuple(s["ages"]) for s in slots}, {(8,), (16,), (64,)})
        self.assertTrue(all(s["uses"] and s["subsets"] for s in slots))
        base = f"/api/v1/hair/jobs/{job['id']}"
        files, configs = {}, {}
        for index, slot in enumerate(slots):
            files[slot["id"]] = ("input.bmp", bmp_v5_rgb((512, 512))) if index == 1 else ("input.png", png(color=(80 + 30 * index, 70, 150, 127)))
            configs[slot["id"]] = {"base": ["Volatile", "Arbitrary texture", "Pooklet Grey"][index],
                                    "black": 12, "white": 230, "gamma": 1.4, "png_alpha": index == 2}
        mask = Image.new("L", (512, 512), 0)
        mask.paste(255, (256, 0, 512, 512))
        mask_data = io.BytesIO()
        mask.save(mask_data, format="PNG")
        files["mask:" + slots[1]["id"]] = ("mask.png", mask_data.getvalue())
        response = self.client.post(base + "/assets", files=files)
        self.assertEqual(response.status_code, 200, response.text)
        payload = {"colors": ["Dynamite", "Pipe Bomb", "TNT"], "settings": {}, "texture_settings": configs}
        response = self.client.post(base + "/preview", json=payload)
        self.assertEqual(response.status_code, 200, response.text[:1000])
        directory = self.root / "hair" / job["id"]
        stored = json.loads((directory / "job.json").read_text())
        for slot, preview in zip(slots, response.json()["previews"]):
            key = slot["id"]
            image = load_texture(directory / "uploads" / stored["assignments"][key])
            template = load_texture(directory / "template-textures" / f"{key}.png")
            slot_mask = mask if key == slots[1]["id"] else None
            prepared, target = render(image, template, configs[key], "TNT", slot_mask)
            self.assertEqual(preview["base"], thumbnail(prepared))
            self.assertEqual(preview["targets"][-1]["image"], thumbnail(target))
            self.assertEqual(preview["targets"][0]["rendered_color"], "Mail Bomb" if slot["ages"] == [16] else "Dynamite")
            self.assertEqual(preview["targets"][1]["active"], slot["ages"] == [16])
        for invalid in [{}, {**configs, "unknown": {}}, {**configs, slots[0]["id"]: []},
                        {**configs, slots[0]["id"]: {"base": "Wrong"}},
                        {**configs, slots[0]["id"]: {"black": 200, "white": 100}}]:
            result = self.client.post(base + "/preview", json={**payload, "texture_settings": invalid})
            self.assertEqual(result.status_code, 422, result.text)
            self.assertEqual(json.loads((directory / "job.json").read_text())["texture_settings"], stored["texture_settings"])
        data = self.build(job)
        report = json.loads((directory / "validation.json").read_text())
        # Three separate age atlases plus one preserved unrelated texture.
        # Grey-only output retains its Elder atlas and the unrelated texture.
        self.assertEqual([len(p["textures"]) for p in report["packages"]], [4, 2, 4])
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            instructions = archive.read("README.txt").decode()
            for name in ["Volatile", "Arbitrary texture", "Pooklet Grey"]:
                self.assertIn("Input base: " + name, instructions)
            package = directory / "TNT.package"
            package.write_bytes(archive.read("Creator_Swirl_TNT.package"))
        exported = directory / "decoded"
        output = inspect(package, exported)
        self.assertEqual(len(output["textures"]), 3)
        for slot in slots:
            key = slot["id"]
            actual = next(s for s in output["textures"] if key.rsplit("-", 1)[1] in s["name"])
            source = load_texture(directory / "uploads" / stored["assignments"][key])
            template = load_texture(directory / "template-textures" / f"{key}.png")
            expected = render(source, template, configs[key], "TNT", mask if key == slots[1]["id"] else None)[1]
            decoded = load_texture(exported / f"{actual['id']}.png")
            self.assertLessEqual(max(abs(a - b) for a, b in zip(decoded.convert("RGB").tobytes(), expected.convert("RGB").tobytes())), 8)

    def test_legacy_index_and_png_alpha_compression(self):
        path = self.root / "legacy.package"
        write_nodes(path, raw_nodes(), old_index=True)
        job = self.create(path.read_bytes())
        self.prepare_job(job, ["Dynamite"], {"png_alpha": True})
        self.build(job)
        # A separate PNG-alpha submission exercises DXT3 rounding rather than
        # simply reusing the template's already quantized alpha channel.
        job = self.create()
        self.example = png(size=(1024, 1024), color=(140, 110, 80, 127))
        self.prepare_job(job, ["Dynamite"], {"png_alpha": True})
        self.build(job)
        report = json.loads((self.root / "hair" / job["id"] / "validation.json").read_text())
        self.assertEqual(report["packages"][0]["textures"][0]["alpha_max_error"], 8)

    def test_reject_unsupported_packages_and_bad_inputs(self):
        cases = [b"not a package"]
        for variant in ["mesh_only", "merged", "replacement", "external_texture"]:
            nodes = raw_nodes()
            if variant == "mesh_only":
                nodes = [n for n in nodes if n[0][0] != 0x8c1580b5]
            elif variant == "merged":
                n = next(n for n in nodes if n[0][0] == 0x8c1580b5)
                nodes.append([(n[0][0], n[0][1], 12345, 0), n[1]])
            elif variant == "replacement":
                nodes[0][0] = (nodes[0][0][0], 0x1c0532fa, *nodes[0][0][2:])
            else:
                nodes = [n for n in nodes if n[0][0] != 0x1c4a276c]
            path = self.root / "invalid.package"
            write_nodes(path, nodes)
            cases.append(path.read_bytes())
        for data in cases:
            response = self.client.post("/api/v1/hair/jobs", data={"creator": "Test", "hair_name": "Hair"}, files={"template": ("bad.package", data)})
            self.assertEqual(response.status_code, 422, response.text)
        job = self.create()
        self.prepare_job(job, ["Dynamite"])
        base = f"/api/v1/hair/jobs/{job['id']}"
        for colors in [[], ["Dynamite", "Dynamite"], ["../escape"], [None]]:
            self.assertEqual(self.client.post(base + "/preview", json={"settings": {}, "colors": colors}).status_code, 422)
        self.assertEqual(self.client.post("/api/v1/hair/jobs", data={"creator": "../path", "hair_name": "Bad"}).status_code, 422)
        self.assertEqual(self.client.get(base + "/download").status_code, 409)

    def test_auth_limits_expiry_and_existing_tabs(self):
        self.assertEqual(self.client.get("/api/v1/hair/templates").status_code, 200)
        self.assertEqual(self.client.post("/api/v1/hair/jobs", headers={"Origin": "http://evil.test"}).status_code, 403)
        self.assertEqual(self.client.get("/api/v1/hair/jobs/../../etc").status_code, 404)
        self.assertEqual(self.client.get("/static/hair/assets/curves").status_code, 404)
        html = self.client.get("/").text
        self.assertIn("Sims 2 Creator Tools", html)
        self.assertLess(html.index('data-tab="texture"'), html.index('data-tab="package"'))
        self.assertLess(html.index('data-tab="package"'), html.index('data-tab="hair"'))
        self.assertEqual(self.client.get("/api/v1/capabilities").status_code, 200)
        job = self.create()
        directory = self.root / "hair" / job["id"]
        state = json.loads((directory / "status.json").read_text())
        state["updated"] -= 3601
        atomic_json(directory / "status.json", state)
        self.app.state.hair_manager.expire()
        self.assertEqual(self.client.get(f"/api/v1/hair/jobs/{job['id']}").status_code, 404)
        limited = dataclasses.replace(self.config, spool_root=self.root / "limited", hair_upload_bytes=1024)
        with TestClient(create_app(limited), headers=self.client.headers) as client:
            response = client.post("/api/v1/hair/jobs", data={"creator": "Test", "hair_name": "Hair"}, files={"template": ("too-big.package", b"x" * 2048)})
            self.assertEqual(response.status_code, 413, response.text)

    def test_cancellation_timeout_and_retry_preserve_identities(self):
        job = self.prepare_job(self.create(), list(BY_NAME))
        base = f"/api/v1/hair/jobs/{job['id']}"
        directory = self.root / "hair" / job["id"]
        identities = json.loads((directory / "job.json").read_text())["identities"]
        self.client.post(base + "/build")
        for _ in range(100):
            if self.client.get(base).json()["state"] == "building":
                break
            time.sleep(.01)
        self.assertEqual(self.client.post(base + "/cancel").json()["state"], "cancelled")
        for _ in range(100):
            if self.app.state.hair_manager.active is None:
                break
            time.sleep(.02)
        self.assertEqual(self.client.get(base + "/download").status_code, 409)
        manager = self.app.state.hair_manager
        manager.config = dataclasses.replace(manager.config, hair_timeout_seconds=.001)
        self.client.post(base + "/build")
        self.assertEqual(self.wait(job["id"])["state"], "failed")
        manager.config = self.config
        self.build(job)
        self.assertEqual(json.loads((directory / "job.json").read_text())["identities"], identities)
        self.assertEqual(self.client.delete(base).status_code, 200)
        self.assertEqual(self.client.get(base).status_code, 404)

    def test_zip_and_temporary_limits_do_not_publish_partial_downloads(self):
        job = self.prepare_job(self.create(), FAMILIES[0])
        base = f"/api/v1/hair/jobs/{job['id']}"
        directory = self.root / "hair" / job["id"]
        original = json.loads((directory / "job.json").read_text())
        for field in ["zip_bytes", "temporary_bytes"]:
            limited = {**original, "limits": {**original["limits"], field: 1024}}
            atomic_json(directory / "job.json", limited)
            self.assertEqual(self.client.post(base + "/build").status_code, 202)
            self.assertEqual(self.wait(job["id"])["state"], "failed")
            self.assertEqual(self.client.get(base + "/download").status_code, 409)
            self.assertFalse((directory / "recolors.zip").exists())
            self.assertFalse((directory / "recolors.partial").exists())


class QueueTests(unittest.TestCase):
    def test_four_waiting_jobs_and_cancel_releases_capacity(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = HairManager(ServiceConfig(spool_root=Path(temporary)))
            manager.root.mkdir()
            ids = [f"{i:032x}" for i in range(5)]
            for job_id in ids:
                directory = manager.root / job_id
                directory.mkdir()
                atomic_json(directory / "job.json", {"prepared": True, "creator": "A", "hair_name": "B", "colors": ["Dynamite"],
                            "inspection": {"ages": [], "textures": []}, "template_label": "fixture", "requirements": "fixture"})
                manager.update(directory, "ready", "Ready")
            for job_id in ids[:4]:
                manager.enqueue(job_id)
            with self.assertRaises(HairError) as context:
                manager.enqueue(ids[4])
            self.assertEqual(context.exception.status, 429)
            manager.cancel(ids[0])
            manager.enqueue(ids[4])
            self.assertEqual(manager.queue.qsize(), 4)


if __name__ == "__main__":
    unittest.main()
