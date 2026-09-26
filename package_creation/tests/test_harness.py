from __future__ import annotations

import json
import shutil
import struct
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from package_creation import harness
from scripts.extract_object_templates import index, unpack, pack


def valid_job() -> dict:
    return {
        "schema_version": 1,
        "slug": "fixture-am",
        "catalog_name": "Fixture Tattoo",
        "catalog_description": "Adult male structural fixture",
        "preset": "am",
        "ages": ["adult"],
        "priority": 102,
        "input_png": "image.png",
        "identity": {
            "box_guid": "0x12345678",
            "overlay_group_id": "0x23456789",
            "family_uuid": "11111111-2222-4333-8444-555555555555",
        },
    }


def valid_multi_job() -> dict:
    return {
        "schema_version": 2,
        "slug": "fixture-tattoos",
        "catalog_name": "Fixture Tattoos",
        "catalog_description": "Adult and elder structural fixture",
        "ages": ["adult", "elder"],
        "compatibility": {
            "plantsim": False,
            "vampire": False,
            "werewolf": False,
            "zombie": False,
            "servo": False,
            "bigfoot": False,
        },
        "identity": {"box_guid": "0x12345678"},
        "tattoos": [
            {
                "key": "kiryu",
                "menu_label": "Kiryu",
                "menu_order": 0,
                "layer_order": 0,
                "priority": 0x65,
                "input_pngs": {"am": "kiryu-am.png", "af": None},
                "identity": {
                    "overlay_group_id": "0x23456789",
                    "family_uuid": "11111111-2222-4333-8444-555555555555",
                },
            }
        ],
    }


class JobValidationTests(unittest.TestCase):
    def test_valid_job(self) -> None:
        harness.validate_job(valid_job())

    def test_failure_matrix(self) -> None:
        cases = []
        unknown_version = valid_job()
        unknown_version["schema_version"] = 2
        cases.append(unknown_version)

        malformed_id = valid_job()
        malformed_id["identity"]["box_guid"] = "1234"
        cases.append(malformed_id)

        reserved_id = valid_job()
        reserved_id["identity"]["overlay_group_id"] = "0xFFFFFFFF"
        cases.append(reserved_id)

        duplicate_id = valid_job()
        duplicate_id["identity"]["overlay_group_id"] = duplicate_id["identity"]["box_guid"]
        cases.append(duplicate_id)

        wrong_gender = valid_job()
        wrong_gender["preset"] = "unisex"
        cases.append(wrong_gender)

        wrong_ages = valid_job()
        wrong_ages["ages"] = ["adult", "elder"]
        cases.append(wrong_ages)

        bad_slug = valid_job()
        bad_slug["slug"] = "Bad Slug"
        cases.append(bad_slug)

        zero_family = valid_job()
        zero_family["identity"]["family_uuid"] = "00000000-0000-0000-0000-000000000000"
        cases.append(zero_family)

        for case in cases:
            with self.subTest(case=case):
                with self.assertRaises(harness.HarnessError):
                    harness.validate_job(case)


class PngValidationTests(unittest.TestCase):
    def test_rgba_png(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "valid.png"
            Image.new("RGBA", (1024, 1024), (1, 2, 3, 128)).save(path)
            report = harness.validate_png(path)
            self.assertEqual(report["mode"], "RGBA")
            self.assertEqual(report["alpha_max"], 128)

    def test_wrong_dimensions_mode_and_blank_alpha(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wrong_size = root / "wrong-size.png"
            wrong_mode = root / "wrong-mode.png"
            blank = root / "blank.png"
            Image.new("RGBA", (512, 512), (0, 0, 0, 255)).save(wrong_size)
            Image.new("RGB", (1024, 1024), (0, 0, 0)).save(wrong_mode)
            Image.new("RGBA", (1024, 1024), (0, 0, 0, 0)).save(blank)
            for path in (wrong_size, wrong_mode, blank):
                with self.subTest(path=path.name):
                    with self.assertRaises(harness.HarnessError):
                        harness.validate_png(path)


class MultiJobValidationTests(unittest.TestCase):
    def test_valid_multi_job(self) -> None:
        harness.validate_multi_job(valid_multi_job())

    def test_multi_failure_matrix(self) -> None:
        cases: list[dict] = []

        wrong_age = valid_multi_job()
        wrong_age["ages"] = ["adult"]
        cases.append(wrong_age)

        occult = valid_multi_job()
        occult["compatibility"]["vampire"] = True
        cases.append(occult)

        wrong_priority = valid_multi_job()
        wrong_priority["tattoos"][0]["priority"] = 500
        cases.append(wrong_priority)

        no_gender = valid_multi_job()
        no_gender["tattoos"][0]["input_pngs"] = {"am": None, "af": None}
        cases.append(no_gender)

        traversal = valid_multi_job()
        traversal["tattoos"][0]["input_pngs"]["am"] = "../outside.png"
        cases.append(traversal)

        duplicate_order = valid_multi_job()
        second = json.loads(json.dumps(duplicate_order["tattoos"][0]))
        second.update(
            {
                "key": "majima",
                "menu_label": "Majima",
                "identity": {
                    "overlay_group_id": "0x3456789A",
                    "family_uuid": "66666666-7777-4888-8999-AAAAAAAAAAAA",
                },
            }
        )
        duplicate_order["tattoos"].append(second)
        cases.append(duplicate_order)

        for case in cases:
            with self.subTest(case=case):
                with self.assertRaises(harness.HarnessError):
                    harness.validate_multi_job(case)

    def test_twenty_tattoo_limit_and_priorities(self) -> None:
        job = valid_multi_job()
        job["tattoos"] = []
        for index in range(20):
            job["tattoos"].append(
                {
                    "key": f"tattoo-{index}",
                    "menu_label": f"Tattoo {index}",
                    "menu_order": index,
                    "layer_order": index,
                    "priority": 0x65 + index,
                    "input_pngs": {"am": "image.png", "af": None},
                    "identity": {
                        "overlay_group_id": f"0x{0x30000000 + index:08X}",
                        "family_uuid": str(__import__("uuid").UUID(int=index + 1)),
                    },
                }
            )
        harness.validate_multi_job(job)
        self.assertEqual(job["tattoos"][-1]["priority"], 0x78)
        job["tattoos"].append(json.loads(json.dumps(job["tattoos"][-1])))
        with self.assertRaises(harness.HarnessError):
            harness.validate_multi_job(job)


class ContainerProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.prepared = all(
            (harness.TEMPLATES / name).is_file() for name in harness.TEMPLATE_SPECS
        )

    def test_pinned_template_tgi_sets(self) -> None:
        if not self.prepared:
            self.skipTest("run harness prepare first")
        reports = harness.verify_templates()
        self.assertEqual(reports["Template_MultiOverlay.package"]["logical_entry_count"], 48)
        self.assertEqual(reports["Template_OverlayBox.package"]["logical_entry_count"], 42)
        self.assertEqual(reports["NoFaceOverlay.package"]["compressed_entry_count"], 1)

    def test_wrong_hash_and_truncated_package_fail(self) -> None:
        if not self.prepared:
            self.skipTest("run harness prepare first")
        source = harness.TEMPLATES / "Template_OverlayBox.package"
        with tempfile.TemporaryDirectory() as directory:
            changed = Path(directory) / "changed.package"
            data = source.read_bytes()
            changed.write_bytes(data[:-1] + bytes([data[-1] ^ 0xFF]))
            with self.assertRaises(harness.HarnessError):
                harness.assert_hash(changed, harness.TEMPLATE_SPECS[source.name]["sha256"], source.name)
            truncated = Path(directory) / "truncated.package"
            truncated.write_bytes(data[:80])
            with self.assertRaises(harness.HarnessError):
                harness.probe_dbpf(truncated)


class IntegrationTests(unittest.TestCase):
    def test_malformed_multi_helper_output_is_rejected(self) -> None:
        fake = subprocess.CompletedProcess([], 0, "not-json", "")
        with patch.object(harness, "run_checked", return_value=fake):
            with self.assertRaises(harness.HarnessError):
                harness.invoke_multi_builder("build-multi", Path("job.json"), Path("output"))

    def test_malformed_merged_helper_output_is_rejected(self) -> None:
        fake = subprocess.CompletedProcess([], 0, "not-json", "")
        with patch.object(harness, "run_checked", return_value=fake):
            with self.assertRaises(harness.HarnessError):
                harness.invoke_merged_builder(
                    "build-merged", Path("job.json"), Path("output.package")
                )

    def test_am_bundle_build_validate_and_collision(self) -> None:
        if not harness.builder_path().is_file():
            self.skipTest("run harness prepare first")
        harness.verify_templates()
        source_png = harness.REPO_ROOT / "output" / "ash_kiryu.png"
        if not source_png.is_file():
            self.skipTest("AM sample is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = valid_job()
            job["input_png"] = str(source_png)
            job_path = root / "job.json"
            harness.atomic_write_json(job_path, job)
            bundle = root / "bundle"
            harness.build_bundle(job_path, bundle)
            report = harness.validate_existing(bundle)
            self.assertEqual(report["status"], "pass")
            with self.assertRaises(harness.HarnessError):
                harness.build_bundle(job_path, bundle)

    def test_builder_rejects_missing_resource_and_unexpected_txmt_layout(self) -> None:
        if not harness.builder_path().is_file():
            self.skipTest("run harness prepare first")
        overlay_template = harness.TEMPLATES / "Template_MultiOverlay.package"
        box_template = harness.TEMPLATES / "Template_OverlayBox.package"
        source_png = harness.REPO_ROOT / "output" / "ash_kiryu.png"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = valid_job()
            job["input_png"] = str(source_png)
            job_path = root / "job.json"
            job_path.write_text(json.dumps(job), encoding="utf-8")

            missing = root / "missing.package"
            missing_data = bytearray(overlay_template.read_bytes())
            count, index_offset, _ = struct.unpack_from("<III", missing_data, 0x24)
            for position in range(index_offset, index_offset + count * 24, 24):
                type_id, group, instance, resource = struct.unpack_from("<IIII", missing_data, position)
                if (type_id, group, resource, instance) == (
                    0x0C560F39,
                    0xFFFFFFFF,
                    0,
                    1,
                ):
                    struct.pack_into("<I", missing_data, position, 0xDEADBEEF)
                    break
            missing.write_bytes(missing_data)
            result = run_builder_failure(
                root,
                job_path,
                missing,
                box_template,
                source_png,
                "missing",
            )
            self.assertIn("starting TGI set", result.stderr)

            bad_txmt = root / "bad-txmt.package"
            txmt_data = bytearray(overlay_template.read_bytes())
            for position in range(index_offset, index_offset + count * 24, 24):
                type_id, group, instance, resource, offset, size = struct.unpack_from(
                    "<IIIIII", txmt_data, position
                )
                if (type_id, group, resource, instance) == (
                    0x49596978,
                    0xFFFFFFFF,
                    0x7596136A,
                    0xFFA104EE,
                ):
                    needle = b"stdMatBaseTextureName"
                    found = txmt_data.find(needle, offset, offset + size)
                    self.assertNotEqual(found, -1)
                    txmt_data[found] = ord("x")
                    break
            bad_txmt.write_bytes(txmt_data)
            result = run_builder_failure(
                root,
                job_path,
                bad_txmt,
                box_template,
                source_png,
                "txmt",
            )
            self.assertIn("TXMT property stdMatBaseTextureName", result.stderr)

    def test_multi_bundle_mixed_genders_and_deterministic_retry(self) -> None:
        if not harness.builder_path().is_file():
            self.skipTest("run harness prepare first")
        source_png = harness.REPO_ROOT / "output" / "ash_kiryu.png"
        if not source_png.is_file():
            self.skipTest("AM sample is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("am-only.png", "af-only.png", "paired-am.png", "paired-af.png"):
                shutil.copyfile(source_png, root / name)
            job = valid_multi_job()
            job["tattoos"] = [
                {
                    "key": "am-only",
                    "menu_label": "AM Only",
                    "menu_order": 2,
                    "layer_order": 0,
                    "priority": 0x65,
                    "input_pngs": {"am": "am-only.png", "af": None},
                    "identity": {
                        "overlay_group_id": "0x23456789",
                        "family_uuid": "11111111-2222-4333-8444-555555555555",
                    },
                },
                {
                    "key": "af-only",
                    "menu_label": "AF Only",
                    "menu_order": 0,
                    "layer_order": 1,
                    "priority": 0x66,
                    "input_pngs": {"am": None, "af": "af-only.png"},
                    "identity": {
                        "overlay_group_id": "0x3456789A",
                        "family_uuid": "66666666-7777-4888-8999-AAAAAAAAAAAA",
                    },
                },
                {
                    "key": "paired",
                    "menu_label": "Paired",
                    "menu_order": 1,
                    "layer_order": 2,
                    "priority": 0x67,
                    "input_pngs": {"am": "paired-am.png", "af": "paired-af.png"},
                    "identity": {
                        "overlay_group_id": "0x456789AB",
                        "family_uuid": "BBBBBBBB-CCCC-4DDD-8EEE-FFFFFFFFFFFF",
                    },
                },
            ]
            job_path = root / "job.json"
            harness.atomic_write_json(job_path, job)
            first = root / "first"
            second = root / "second"
            harness.build_multi_bundle(job_path, first)
            report = harness.validate_multi_bundle(job_path, first)
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["rust"]["age_mask"], 0x18)
            self.assertEqual(report["rust"]["compatibility"], "normal-sims-only")
            by_key = {item["key"]: item for item in report["rust"]["tattoos"]}
            self.assertEqual(by_key["am-only"]["genders"], ["am"])
            self.assertEqual(by_key["af-only"]["genders"], ["af"])
            self.assertEqual(by_key["paired"]["genders"], ["am", "af"])
            self.assertTrue(report["rust"]["bhav"]["bytes_unchanged"])

            harness.build_multi_bundle(job_path, second)
            for relative in (
                "Install/fixture-tattoos_OverlayBox.package",
                "Install/fixture-tattoos_am-only_Overlay.package",
                "Install/fixture-tattoos_af-only_Overlay.package",
                "Install/fixture-tattoos_paired_Overlay.package",
                "Install/NoFaceOverlay.package",
                "manifest.json",
                "validation.json",
                "SHA256SUMS",
                "fixture-tattoos.zip",
            ):
                with self.subTest(relative=relative):
                    self.assertEqual((first / relative).read_bytes(), (second / relative).read_bytes())
            with zipfile.ZipFile(first / "fixture-tattoos.zip") as archive:
                for info in archive.infolist():
                    self.assertEqual(info.date_time, harness.FIXED_ZIP_TIME)
                    self.assertEqual((info.external_attr >> 16) & 0o777, 0o644)
                    self.assertFalse(info.filename.startswith("/"))
                    self.assertNotIn("..", Path(info.filename).parts)
                names = archive.namelist()
                self.assertNotIn("build.log", names)
                self.assertFalse(any(name.endswith(".png") for name in names))
            with self.assertRaises(harness.HarnessError):
                harness.build_multi_bundle(job_path, first)

            merged_first = root / "merged-first"
            merged_second = root / "merged-second"
            manifest = harness.build_merged_package(job_path, merged_first)
            merged_report = harness.validate_merged_package(job_path, merged_first)
            self.assertEqual(manifest["layout"], "single-package")
            self.assertEqual(merged_report["status"], "pass")
            self.assertEqual(merged_report["rust"]["layout"], "single-package")
            self.assertEqual(merged_report["rust"]["no_face_resource_count"], 1)
            self.assertEqual(merged_report["rust"]["logical_resource_count"], 78)
            self.assertEqual(
                merged_report["independent_python_dbpf_probe"]["fixture-tattoos.package"][
                    "logical_entry_count"
                ],
                78,
            )
            self.assertEqual(
                merged_report["independent_python_dbpf_probe"]["fixture-tattoos.package"][
                    "duplicate_keys"
                ],
                [],
            )
            self.assertEqual(
                merged_report["independent_python_dbpf_probe"]["fixture-tattoos.package"][
                    "compressed_entry_count"
                ],
                merged_report["rust"]["package_compression"]["compressed_resources"],
            )
            merged_package = merged_first / "fixture-tattoos.package"
            merged_bytes = merged_package.read_bytes()
            scene_name = merged_report["rust"]["scene_graph_name"]
            self.assertEqual(scene_name, harness.MULTI_CONTROLLER_MODEL)
            stream, resource_index = index(merged_package)
            with stream:
                resources = {}
                for key, (offset, size) in resource_index.items():
                    if key[0] == 0xE86B1EEF:
                        continue
                    stream.seek(offset)
                    resources[key] = unpack(stream.read(size))
            decoded = b"".join(resources.values())
            self.assertEqual(decoded.count(scene_name.encode()), 1)
            self.assertNotIn(b"accessory-box-template", decoded)
            self.assertNotIn(b"ts2box-", decoded)
            self.assertEqual(
                merged_report["rust"]["controller_model_catalog_name"],
                harness.MULTI_CONTROLLER_CATALOG_NAME,
            )
            self.assertFalse(manifest["job"]["controller_model"]["embedded"])

            corrupt_scene = root / "corrupt-scene.package"
            corrupt_bytes = pack({key: data.replace(scene_name.encode(), b"sculptureOshoKomX", 1)
                                  for key, data in resources.items()})
            harness.atomic_write_bytes(corrupt_scene, corrupt_bytes)
            with self.assertRaisesRegex(
                harness.HarnessError,
                "does not reference the Osho Nuff Tablet model",
            ):
                harness.invoke_merged_builder("validate-merged", job_path, corrupt_scene)

            harness.build_merged_package(job_path, merged_second)
            for relative in (
                "fixture-tattoos.package",
                "manifest.json",
                "validation.json",
                "build.log",
            ):
                with self.subTest(merged_relative=relative):
                    self.assertEqual(
                        (merged_first / relative).read_bytes(),
                        (merged_second / relative).read_bytes(),
                    )
            with self.assertRaises(harness.HarnessError):
                harness.build_merged_package(job_path, merged_first)

    def test_maximum_twenty_tattoo_bundle(self) -> None:
        if not harness.builder_path().is_file():
            self.skipTest("run harness prepare first")
        source_png = harness.REPO_ROOT / "output" / "ash_kiryu.png"
        if not source_png.is_file():
            self.skipTest("AM sample is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copyfile(source_png, root / "image.png")
            job = valid_multi_job()
            job["tattoos"] = []
            for index in range(20):
                job["tattoos"].append(
                    {
                        "key": f"tattoo-{index}",
                        "menu_label": f"Tattoo {index}",
                        "menu_order": 19 - index,
                        "layer_order": index,
                        "priority": 0x65 + index,
                        "input_pngs": {"am": "image.png", "af": None},
                        "identity": {
                            "overlay_group_id": f"0x{0x30000000 + index:08X}",
                            "family_uuid": str(__import__("uuid").UUID(int=index + 1)),
                        },
                    }
                )
            job_path = root / "job.json"
            harness.atomic_write_json(job_path, job)
            bundle = root / "bundle"
            harness.build_multi_bundle(job_path, bundle)
            report = harness.validate_multi_bundle(job_path, bundle)
            self.assertEqual(len(report["rust"]["tattoos"]), 20)
            self.assertEqual(max(item["priority"] for item in report["rust"]["tattoos"]), 0x78)

            merged = root / "merged"
            harness.build_merged_package(job_path, merged)
            merged_report = harness.validate_merged_package(job_path, merged)
            self.assertEqual(len(merged_report["rust"]["tattoos"]), 20)
            self.assertEqual(merged_report["rust"]["logical_resource_count"], 277)
            self.assertEqual(merged_report["rust"]["no_face_resource_count"], 1)
            self.assertLess(
                (merged / "fixture-tattoos.package").stat().st_size,
                64 * 1024 * 1024,
            )


def run_builder_failure(
    root: Path,
    job: Path,
    overlay_template: Path,
    box_template: Path,
    png: Path,
    label: str,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [
            str(harness.builder_path()),
            "build",
            "--job",
            str(job),
            "--overlay-template",
            str(overlay_template),
            "--box-template",
            str(box_template),
            "--png",
            str(png),
            "--overlay-output",
            str(root / f"{label}-overlay.package"),
            "--box-output",
            str(root / f"{label}-box.package"),
        ],
        check=False,
        capture_output=True,
        text=True,
        shell=False,
    )
    if result.returncode == 0:
        raise AssertionError("corrupt template unexpectedly built")
    return result


if __name__ == "__main__":
    unittest.main()
