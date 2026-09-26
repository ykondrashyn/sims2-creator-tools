from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

import convert


def test_profile() -> dict:
    return {
        "source": {"width": 4, "height": 8},
        "target": {"width": 4, "height": 4},
        "template": {"filename": "template.blend", "sha256": ""},
    }


class JobResolutionTests(unittest.TestCase):
    def test_single_mode_requires_input_and_output(self) -> None:
        jobs = convert.resolve_jobs([Path("input.png"), Path("result.png")], None)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].input_path.name, "input.png")
        self.assertEqual(jobs[0].output_path.name, "result.png")

    def test_batch_mode_rejects_duplicate_basenames(self) -> None:
        with self.assertRaisesRegex(convert.ConversionError, "same output"):
            convert.resolve_jobs(
                [Path("one/shared.png"), Path("two/shared.png")],
                Path("output"),
            )

    def test_output_cannot_overwrite_input(self) -> None:
        with self.assertRaisesRegex(convert.ConversionError, "overwrite an input"):
            convert.resolve_jobs([Path("same.png"), Path("same.png")], None)


class BlenderDiscoveryTests(unittest.TestCase):
    def test_explicit_executable_is_used(self) -> None:
        discovered = convert.discover_blender(Path("/bin/sh"), environ={})
        self.assertEqual(discovered, Path("/bin/sh").resolve())

    def test_bad_explicit_executable_fails(self) -> None:
        with self.assertRaisesRegex(convert.ConversionError, "not runnable"):
            convert.discover_blender(Path("/definitely/missing/blender"), environ={})


class PngValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="tattooer-tests-")
        self.root = Path(self.temp_dir.name)
        self.profile = test_profile()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_rgba(self, name: str, size: tuple[int, int], pixel) -> Path:
        path = self.root / name
        image = Image.new("RGBA", size, pixel)
        image.save(path)
        return path

    def test_valid_input_reports_alpha(self) -> None:
        path = self.write_rgba("source.png", (4, 8), (20, 30, 40, 128))
        stats = convert.validate_input(path, self.profile)
        self.assertEqual(stats["alpha_nonzero_pixels"], 32)
        self.assertEqual(stats["alpha_partial_pixels"], 32)

    def test_wrong_size_fails(self) -> None:
        path = self.write_rgba("wrong.png", (4, 4), (20, 30, 40, 128))
        with self.assertRaisesRegex(convert.ConversionError, "unexpected PNG dimensions"):
            convert.validate_input(path, self.profile)

    def test_blank_output_fails(self) -> None:
        path = self.write_rgba("blank.png", (4, 4), (0, 0, 0, 0))
        with self.assertRaisesRegex(convert.ConversionError, "implausibly small|completely transparent"):
            convert.validate_output(path, self.profile, {"alpha_partial_pixels": 1})

    def test_rgba_is_required(self) -> None:
        path = self.root / "rgb.png"
        Image.new("RGB", (4, 8), (20, 30, 40)).save(path)
        with self.assertRaisesRegex(convert.ConversionError, "expected RGBA"):
            convert.validate_input(path, self.profile)

    def test_implausibly_dark_output_fails(self) -> None:
        profile = {
            "source": {"width": 64, "height": 128},
            "target": {"width": 64, "height": 64},
        }
        source = Image.new("RGBA", (64, 128))
        source.putdata(
            [
                ((x * 11) % 256, (y * 7) % 256, ((x + y) * 5) % 256, 128)
                for y in range(128)
                for x in range(64)
            ]
        )
        source_path = self.root / "color-source.png"
        source.save(source_path)
        input_stats = convert.validate_input(source_path, profile)

        dark = Image.new("RGBA", (64, 64))
        dark.putdata(
            [
                ((x * 11) % 24, (y * 7) % 24, ((x + y) * 5) % 24, 128)
                for y in range(64)
                for x in range(64)
            ]
        )
        dark_path = self.root / "dark-output.png"
        dark.save(dark_path, compress_level=0)
        with self.assertRaisesRegex(convert.ConversionError, "implausibly dark"):
            convert.validate_output(dark_path, profile, input_stats)


class ArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="tattooer-artifacts-")
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_json_output_is_stable(self) -> None:
        first = self.root / "first.json"
        second = self.root / "second.json"
        value = {"z": 1, "a": {"second": 2, "first": 1}}
        convert.write_json_atomic(first, value)
        convert.write_json_atomic(second, value)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(json.loads(first.read_text()), value)

    def test_template_hash_is_verified(self) -> None:
        template = self.root / "template.blend"
        template.write_bytes(b"template data")
        profile = test_profile()
        profile["template"]["sha256"] = convert.sha256_path(template)
        self.assertEqual(convert.verify_template(template, profile), profile["template"]["sha256"])
        template.write_bytes(b"changed")
        with self.assertRaisesRegex(convert.ConversionError, "SHA-256 mismatch"):
            convert.verify_template(template, profile)

    def test_contact_sheet_supports_unicode_paths(self) -> None:
        source = self.root / "źródło.png"
        output = self.root / "wynik.png"
        Image.new("RGBA", (4, 8), (200, 40, 20, 128)).save(source)
        Image.new("RGBA", (4, 4), (200, 40, 20, 128)).save(output)
        job = convert.Job(source, output)
        result = {
            "input": {"alpha_coverage": 1.0},
            "output": {"alpha_coverage": 1.0},
        }
        contact_sheet = self.root / "contact.png"
        convert.create_contact_sheet([(job, result)], contact_sheet)
        with Image.open(contact_sheet) as image:
            self.assertEqual(image.format, "PNG")
            self.assertGreater(image.width, 900)


if __name__ == "__main__":
    unittest.main()
