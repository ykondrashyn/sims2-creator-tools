"""Object engine regressions using a local fixture, not gameplay acceptance."""
import base64
import copy
import io
import json
import os
import struct
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from package_creation.tests.artifacts import required_artifact, fixture_root

from PIL import Image
from scripts.extract_object_templates import index, unpack, pack

ROOT = Path(__file__).resolve().parents[2]
BINARY = required_artifact("OBJECT_NATIVE_BINARY")
FIXTURE = ROOT / "package_creation/templates/Template_OverlayBox.package"


def model_fixture():
    pixels = Image.new("RGBA", (4, 4), (182, 95, 31, 255))
    pixels.putpixel((0, 0), (22, 42, 62, 0))
    encoded = io.BytesIO()
    pixels.save(encoded, format="PNG")
    position = struct.pack("<12f", 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1)
    uv = struct.pack("<8f", 0, 0, 1, 0, 0, 1, 1, 1)
    indices = struct.pack("<12H", 0, 2, 1, 0, 1, 3, 0, 3, 2, 1, 2, 3)
    binary = position + uv + indices
    doc = {
        "asset": {"version": "2.0"}, "scene": 0,
        "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(position)},
            {"buffer": 0, "byteOffset": len(position), "byteLength": len(uv)},
            {"buffer": 0, "byteOffset": len(position) + len(uv), "byteLength": len(indices)},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 4, "type": "VEC3", "min": [0, 0, 0], "max": [1, 1, 1]},
            {"bufferView": 1, "componentType": 5126, "count": 4, "type": "VEC2"},
            {"bufferView": 2, "componentType": 5123, "count": 12, "type": "SCALAR"},
        ],
        "meshes": [{"primitives": [
            {"attributes": {"POSITION": 0, "TEXCOORD_0": 1}, "indices": 2, "material": 0},
            {"attributes": {"POSITION": 0, "TEXCOORD_0": 1}, "indices": 2, "material": 1},
        ]}],
        "materials": [
            {"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}, "alphaMode": "MASK", "doubleSided": True},
            {"pbrMetallicRoughness": {"baseColorFactor": [0.2, 0.8, 0.3, 1]}},
        ],
        "textures": [{"source": 0}],
        "images": [{"uri": "data:image/png;base64," + base64.b64encode(encoded.getvalue()).decode()}],
    }
    return doc, binary


def glb(doc, binary):
    text = json.dumps(doc, separators=(",", ":")).encode()
    text += b" " * (-len(text) % 4)
    binary += b"\0" * (-len(binary) % 4)
    return struct.pack("<III", 0x46546c67, 2, 28 + len(text) + len(binary)) + struct.pack("<II", len(text), 0x4e4f534a) + text + struct.pack("<II", len(binary), 0x004e4942) + binary


class Objects(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not BINARY.exists():
            raise RuntimeError("Build the native object example before running object regressions")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.assets = {"fixture.package": str(FIXTURE), "object-template": str(FIXTURE)}
        profile = self.call("object_profile", {"asset": "fixture.package"})
        profile.update(id="fixture", label="Test fixture", kind="floor-decor", requirements="Test fixture only")
        self.catalog = {"items": [profile]}
        self.game = {"keys": [], "names": {}, "guids": []}
        self.write_catalog()
        self.job = {"id": "12345678901234567890123456789012", "mode": "clone", "creator": "Test", "object_name": "Tetrahedron", "title": "Catalog test", "description": "Changed description", "price": 42, "credits": "Test fixture"}

    def write_catalog(self):
        for name, value in [("object-catalog", self.catalog), ("object-game", self.game)]:
            path = self.root / (name + ".json")
            path.write_text(json.dumps(value))
            self.assets[name] = str(path)

    def call(self, op, params, output=None):
        request = {"op": op, "params": params, "assets": self.assets}
        if output:
            request["output"] = str(output)
        p = subprocess.run([str(BINARY)], input=json.dumps(request), capture_output=True, text=True)
        if p.returncode:
            raise ValueError(p.stderr)
        return json.loads(p.stdout)

    def model(self, doc=None, zipped=False):
        base, binary = model_fixture()
        doc = doc or base
        path = self.root / ("model.zip" if zipped else "model.glb")
        if zipped:
            doc["buffers"][0]["uri"] = "mesh.bin"
            with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
                z.writestr("scene/model.gltf", json.dumps(doc))
                z.writestr("scene/mesh.bin", binary)
        else:
            path.write_bytes(glb(doc, binary))
        self.assets[path.name] = str(path)
        self.job.update(mode="model", model_file=path.name, scale=1, rotation=0)

    def resources(self, path=FIXTURE):
        stream, entries = index(path)
        try:
            result = {}
            for key, (offset, size) in entries.items():
                if key[0] == 0xe86b1eef:
                    continue
                stream.seek(offset)
                result[key] = unpack(stream.read(size))
            return result
        finally:
            stream.close()

    def test_behavior_tables_and_placement_must_match(self):
        source = self.resources()
        for kind in [0x4f424a44, 0x42484156]:
            changed = dict(source)
            key = next(k for k in changed if k[0] == kind)
            data = bytearray(changed[key])
            data[70 if kind == 0x4f424a44 else -1] ^= 1
            changed[key] = data
            path = self.root / "modified.package"
            path.write_bytes(pack(changed))
            self.assets["modified.package"] = str(path)
            with self.assertRaisesRegex(ValueError, "behavior is outside"):
                self.call("object_inspect", {"files": ["modified.package"]})

    def test_later_profile_can_match_the_same_behavior_family(self):
        matching = self.catalog["items"][0]
        earlier = {**matching, "id": "earlier-mismatch",
                   "behavior_resources": ["42484156:00000000:changed"]}
        self.catalog["items"].insert(0, earlier)
        self.write_catalog()
        result = self.call("object_build", {"job": self.job})
        self.assertEqual(result["status"], "passed")

    def test_multiple_tile_guids_remain_independent(self):
        source = self.resources()
        key = next(k for k in source if k[0] == 0x4f424a44)
        original = source.pop(key)
        for i, tile in enumerate([65535, 0, 256]):
            data = bytearray(original)
            struct.pack_into("<HH", data, 84, 4, tile)
            struct.pack_into("<I", data, 92, 0x12341000 + i)
            source[(key[0], key[1], key[2] + i)] = data
        path = self.root / "tiles.package"
        path.write_bytes(pack(source))
        self.assets["object-template"] = str(path)
        profile = self.call("object_profile", {"asset": "object-template"})
        self.catalog["items"][0].update(profile)
        self.write_catalog()
        first = self.call("object_build", {"job": self.job})
        second = self.call("object_build", {"job": self.job})
        self.assertEqual(first, second)
        self.assertEqual(len({o["guid"] for o in first["objects"]}), 3)
        self.assertEqual({o["tile"] for o in first["objects"]}, {65535, 0, 256})
        self.assertEqual({o["master"] for o in first["objects"]}, {4})

    def test_clone_retry_and_new_batch_identities(self):
        a, b = self.root / "a.package", self.root / "b.package"
        first = self.call("object_build", {"job": self.job}, a)
        self.call("object_build", {"job": self.job}, b)
        self.assertEqual(a.read_bytes(), b.read_bytes())
        self.assertEqual(first["objects"][0]["price"], 42)
        self.job["id"] = "32345678901234567890123456789012"
        second = self.call("object_build", {"job": self.job}, b)
        self.assertFalse(set(first["resource_keys"]) & set(second["resource_keys"]))
        self.assertNotEqual(first["objects"][0]["guid"], second["objects"][0]["guid"])

    def test_custom_clone_can_be_cloned_again(self):
        output = self.root / "clone.package"
        first = self.call("object_build", {"job": self.job}, output)
        self.assets["clone.package"] = str(output)
        inspection = self.call("object_inspect", {"files": ["clone.package"]})
        self.assertEqual(inspection["objects"], first["objects"])

    def test_default_replacement_rejected(self):
        inspection = self.call("object_inspect", {"files": ["fixture.package"]})
        self.game["guids"] = [inspection["objects"][0]["guid"]]
        self.write_catalog()
        with self.assertRaisesRegex(ValueError, "Default replacements"):
            self.call("object_inspect", {"files": ["fixture.package"]})

    def test_model_import_multiple_materials_and_full_mips(self):
        self.model()
        report = self.call("object_build", {"job": self.job}, self.root / "model.package")
        subsets = report["meshes"][0]["subsets"]
        self.assertEqual([s["triangles"] for s in subsets], [4, 4])
        textures = [t for t in report["textures"] if "-model-" in t["name"]]
        self.assertEqual(len(textures), 2)
        self.assertTrue(all(t["mips"] == 3 and t["format"] == "DXT3" for t in textures))

    def test_gltf_zip_matches_glb(self):
        a, b = self.root / "a.package", self.root / "b.package"
        self.model()
        self.call("object_build", {"job": self.job}, a)
        self.model(zipped=True)
        self.call("object_build", {"job": self.job}, b)
        self.assertEqual(a.read_bytes(), b.read_bytes())

    def test_preview_uses_generated_package(self):
        self.model()
        result = self.call("object_preview", {"job": self.job})
        report = self.call("object_build", {"job": self.job})
        self.assertEqual(result["report"]["sha256"], report["sha256"])
        self.assertTrue(result["original"]["meshes"])
        self.assertTrue(result["converted"]["meshes"])
        uri = result["converted"]["materials"]["part0"]["image"]
        image = Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1])))
        self.assertEqual(set(image.getchannel("A").getdata()), {0, 255})

    def test_absolute_height_rotation_and_acknowledgement(self):
        self.model()
        self.job.update(target_height=1.8788737, rotation=0)
        first = self.call("object_layout", {"job": self.job})
        self.assertAlmostEqual(first["dimensions"]["height"], 1.8788737, places=5)
        self.assertFalse(first["placement"]["known"])
        self.assertTrue(first["requires_acknowledgement"])
        with self.assertRaisesRegex(ValueError, "acknowledge"):
            self.call("object_build", {"job": self.job})
        self.job["placement_ack"] = first["signature"]
        built = self.call("object_build", {"job": self.job})
        self.assertAlmostEqual(built["dimensions"]["height"], 1.8788737, places=5)
        self.assertEqual(built["layout"], first)
        self.job["rotation"] = 37
        rotated = self.call("object_layout", {"job": self.job})
        self.assertAlmostEqual(rotated["dimensions"]["height"], first["dimensions"]["height"], places=5)
        self.assertNotEqual(rotated["signature"], first["signature"])
        with self.assertRaisesRegex(ValueError, "acknowledge"):
            self.call("object_build", {"job": self.job})
        self.job["placement_ack"] = rotated["signature"]
        a, b = self.root / "sized-a.package", self.root / "sized-b.package"
        self.call("object_build", {"job": self.job}, a)
        self.call("object_build", {"job": self.job}, b)
        self.assertEqual(a.read_bytes(), b.read_bytes())
        preview = self.call("object_preview", {"job": self.job})
        self.assertEqual(preview["report"], self.call("object_build", {"job": self.job}))

    def test_height_limits_and_known_coverage(self):
        self.model()
        for height in [0, -1, 1.8788737 * .09, 1.8788737 * 3.01, "bad"]:
            self.job["target_height"] = height
            with self.assertRaisesRegex(ValueError, "[Hh]eight"):
                self.call("object_layout", {"job": self.job})
        self.catalog["items"][0]["placement"] = {"known": True}
        self.write_catalog()
        self.job["target_height"] = .2
        layout = self.call("object_layout", {"job": self.job})
        self.assertFalse(layout["requires_acknowledgement"])
        self.call("object_build", {"job": self.job})
        self.job["target_height"] = 1.8788737 * 3
        layout = self.call("object_layout", {"job": self.job})
        self.assertTrue(layout["requires_acknowledgement"])

    def test_fit_preserves_small_custom_template_height(self):
        self.model()
        self.job["scale"] = .1
        small = self.root / "small.package"
        self.call("object_build", {"job": self.job}, small)
        self.assets["small.package"] = str(small)
        original = self.call("object_inspect", {"files": ["small.package"]})["dimensions"]["height"]
        self.assertGreater(original, 0)
        self.assertLess(original, 1.8788737 * .1)
        self.assets["object-template"] = str(small)
        self.job["target_height"] = original
        layout = self.call("object_layout", {"job": self.job})
        self.assertAlmostEqual(layout["height"], original, places=7)
        self.job["placement_ack"] = layout["signature"]
        result = self.call("object_preview", {"job": self.job})
        self.assertAlmostEqual(result["report"]["dimensions"]["height"], original, places=7)
        self.assertEqual(result["report"], self.call("object_build", {"job": self.job}))
        self.job["target_height"] = original * .9
        with self.assertRaisesRegex(ValueError, "original height"):
            self.call("object_layout", {"job": self.job})

    def test_floor_fit_uses_rotation_and_can_shrink_below_manual_limit(self):
        doc, _ = model_fixture()
        doc["nodes"][0]["scale"] = [20, 1, 6]
        self.model(doc)
        self.catalog["items"][0]["placement"] = {"known": True}
        self.write_catalog()
        self.job["fit_to_template"] = True
        original = self.call("object_inspect", {"files": ["fixture.package"]})["dimensions"]["height"]
        heights = []
        for angle in [0, 37, 90]:
            self.job["rotation"] = angle
            layout = self.call("object_layout", {"job": self.job})
            heights.append(layout["height"])
            self.assertTrue(layout["fit_to_template"])
            self.assertFalse(layout["requires_acknowledgement"])
            self.assertLessEqual(layout["height"], original)
            self.assertLess(layout["height"], .1 * 1.8788737)
            self.assertLessEqual(layout["dimensions"]["width"], 1.00001)
            self.assertLessEqual(layout["dimensions"]["depth"], 1.00001)
            preview = self.call("object_preview", {"job": self.job})
            self.assertEqual(preview["layout"], layout)
            self.assertEqual(preview["report"], self.call("object_build", {"job": self.job}))
        self.assertNotEqual(heights[0], heights[1])
        self.catalog["items"][0]["placement"]["known"] = False
        self.write_catalog()
        with self.assertRaisesRegex(ValueError, "floor area is unknown"):
            self.call("object_layout", {"job": self.job})

    def test_saved_fit_keeps_height_and_scale_during_rotation(self):
        doc, _ = model_fixture()
        doc["nodes"][0]["scale"] = [20, 1, 6]
        self.model(doc)
        self.catalog["items"][0]["placement"] = {"known": True}
        self.write_catalog()
        self.job.update(fit_to_template=True, rotation=37)
        fitted = self.call("object_layout", {"job": self.job})
        height = fitted["height"]
        self.assertLess(height, 1.8788737 * .1)
        self.job.update(fit_to_template=False, height_from_fit=True, target_height=height)
        warnings = []
        for angle in [0, 37, 90]:
            self.job["rotation"] = angle
            layout = self.call("object_layout", {"job": self.job})
            self.assertEqual(layout["height"], height)
            self.assertEqual(layout["scale"], fitted["scale"])
            self.assertFalse(layout["fit_to_template"])
            self.assertTrue(layout["height_from_fit"])
            warnings.append(layout["requires_acknowledgement"])
            self.job["placement_ack"] = layout["signature"]
            preview = self.call("object_preview", {"job": self.job})
            self.assertEqual(preview["layout"], layout)
            self.assertEqual(preview["report"], self.call("object_build", {"job": self.job}))
        self.assertIn(True, warnings)
        self.job["height_from_fit"] = False
        with self.assertRaisesRegex(ValueError, "[Hh]eight"):
            self.call("object_layout", {"job": self.job})
        self.job["height_from_fit"] = True
        for invalid in [0, -1, 1.8788737 * 3.01]:
            self.job["target_height"] = invalid
            with self.assertRaisesRegex(ValueError, "[Hh]eight"):
                self.call("object_layout", {"job": self.job})

    def test_unsupported_model_and_functional_replacement(self):
        for mutation in ["blend", "missing_uv", "remote_image", "normal_map"]:
            doc, _ = model_fixture()
            if mutation == "blend":
                doc["materials"][0]["alphaMode"] = "BLEND"
            elif mutation == "missing_uv":
                del doc["meshes"][0]["primitives"][0]["attributes"]["TEXCOORD_0"]
            elif mutation == "normal_map":
                doc["materials"][0]["normalTexture"] = {"index": 0}
            else:
                doc["images"][0]["uri"] = "https://example.com/texture.png"
            self.model(doc)
            with self.assertRaises(ValueError):
                self.call("object_build", {"job": self.job})
        self.model()
        self.catalog["items"][0]["kind"] = "chair"
        self.write_catalog()
        with self.assertRaisesRegex(ValueError, "decorations only"):
            self.call("object_build", {"job": self.job})

    def test_limits_and_duplicate_inputs(self):
        for field, value in [("creator", "../Creator"), ("object_name", ""), ("price", 65536), ("price", -1), ("id", "")]:
            job = {**self.job, field: value}
            with self.assertRaises(ValueError):
                self.call("object_prepare", {"job": job})
        with self.assertRaisesRegex(ValueError, "filenames"):
            self.call("object_inspect", {"files": ["fixture.package", "fixture.package"]})
        self.assets["other.package"] = str(FIXTURE)
        with self.assertRaisesRegex(ValueError, "identities conflict"):
            self.call("object_inspect", {"files": ["fixture.package", "other.package"]})


if __name__ == "__main__":
    unittest.main()
