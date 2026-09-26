"""Outer command-line wrapper for isolated Tattooer Blender bakes."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from typing import Iterable, Mapping, Sequence

from PIL import Image, ImageDraw, ImageFont, ImageOps


TOOL_VERSION = "0.1.0"
PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_TEMPLATE = PROJECT_ROOT / "templates" / "AM-body-4t2-1024.blend"
DEFAULT_PROFILE = PROJECT_ROOT / "profiles" / "body_4t2.json"
DEFAULT_ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"
BAKE_SCRIPT = PROJECT_ROOT / "scripts" / "bake_texture.py"
RESULT_PREFIX = "TATTOOER_RESULT="
ERROR_PREFIX = "TATTOOER_ERROR="


class ConversionError(RuntimeError):
    """Raised for a configuration, input, Blender, or output error."""


@dataclass(frozen=True)
class Job:
    input_path: Path
    output_path: Path


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(resolved)


def load_profile(path: Path) -> dict:
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConversionError(f"cannot read profile {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ConversionError(f"profile is not valid JSON: {exc}") from exc
    for key in ("id", "blender_version", "template", "source", "target", "bake"):
        if key not in profile:
            raise ConversionError(f"profile is missing {key!r}")
    return profile


def verify_template(path: Path, profile: dict) -> str:
    if not path.is_file():
        raise ConversionError(
            f"template does not exist: {path}\n"
            "Run: python scripts/fetch_template.py"
        )
    expected = profile["template"]
    if path.name != expected["filename"]:
        raise ConversionError(
            f"wrong template filename, expected {expected['filename']!r}, got {path.name!r}"
        )
    actual_hash = sha256_path(path)
    if actual_hash != expected["sha256"]:
        raise ConversionError(
            f"template SHA-256 mismatch, expected {expected['sha256']}, got {actual_hash}"
        )
    return actual_hash


def executable_candidate(path: str | os.PathLike[str] | None) -> Path | None:
    if not path:
        return None
    candidate = Path(path).expanduser().resolve()
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate
    return None


def discover_blender(
    explicit: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path:
    environment = os.environ if environ is None else environ
    if explicit is not None:
        candidate = executable_candidate(explicit)
        if candidate:
            return candidate
        raise ConversionError(f"Blender executable is not runnable: {explicit}")

    configured = environment.get("BLENDER_BIN")
    if configured:
        candidate = executable_candidate(configured)
        if candidate:
            return candidate
        raise ConversionError(f"BLENDER_BIN is not runnable: {configured}")

    on_path = shutil.which("blender")
    if on_path:
        candidate = executable_candidate(on_path)
        if candidate:
            return candidate

    application_candidates = (
        Path("/Applications/Blender.app/Contents/MacOS/Blender"),
        Path.home() / "Applications" / "Blender.app" / "Contents" / "MacOS" / "Blender",
        PROJECT_ROOT / ".tools" / "Blender.app" / "Contents" / "MacOS" / "Blender",
    )
    for application_path in application_candidates:
        candidate = executable_candidate(application_path)
        if candidate:
            return candidate

    raise ConversionError(
        "Blender was not found. Install Blender 3.4.1 or pass --blender /absolute/path/to/blender"
    )


def blender_version(blender: Path) -> tuple[int, int, int]:
    completed = subprocess.run(
        [str(blender), "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ConversionError(
            f"Blender version check failed with exit code {completed.returncode}: "
            f"{completed.stderr.strip()}"
        )
    match = re.search(r"^Blender (\d+)\.(\d+)\.(\d+)", completed.stdout, re.MULTILINE)
    if not match:
        raise ConversionError("could not parse Blender version output")
    return tuple(int(part) for part in match.groups())


def resolve_jobs(paths: Sequence[Path], output_dir: Path | None) -> list[Job]:
    if output_dir is None:
        if len(paths) != 2:
            raise ConversionError(
                "without --output-dir, provide exactly one input PNG and one output PNG"
            )
        jobs = [Job(paths[0].resolve(), paths[1].resolve())]
    else:
        if not paths:
            raise ConversionError("with --output-dir, provide at least one input PNG")
        resolved_output_dir = output_dir.resolve()
        jobs = [Job(path.resolve(), resolved_output_dir / path.name) for path in paths]

    normalized_outputs: dict[str, Path] = {}
    input_paths = {str(job.input_path).casefold() for job in jobs}
    for job in jobs:
        if job.input_path.suffix.lower() != ".png":
            raise ConversionError(f"input is not a PNG path: {job.input_path}")
        if job.output_path.suffix.lower() != ".png":
            raise ConversionError(f"output is not a PNG path: {job.output_path}")
        output_key = str(job.output_path).casefold()
        if output_key in normalized_outputs:
            raise ConversionError(
                f"multiple inputs resolve to the same output: {job.output_path}"
            )
        if output_key in input_paths:
            raise ConversionError(f"output would overwrite an input PNG: {job.output_path}")
        normalized_outputs[output_key] = job.output_path
    return jobs


def rgba_stats(image: Image.Image) -> dict:
    if image.mode != "RGBA":
        raise ConversionError(f"expected RGBA image, got {image.mode}")
    alpha = image.getchannel("A")
    histogram = alpha.histogram()
    total = image.width * image.height
    alpha_nonzero = total - histogram[0]
    alpha_opaque = histogram[255]
    alpha_partial = alpha_nonzero - alpha_opaque
    rgb_nonzero_in_alpha = 0
    alpha_weight = 0.0
    weighted_luma = 0.0
    weighted_saturation = 0.0
    visible_rgb_max = 0
    for red, green, blue, alpha_value in image.get_flattened_data():
        if alpha_value > 0 and (red > 0 or green > 0 or blue > 0):
            rgb_nonzero_in_alpha += 1
        if alpha_value > 0:
            weight = alpha_value / 255.0
            maximum = max(red, green, blue)
            minimum = min(red, green, blue)
            alpha_weight += weight
            weighted_luma += (0.2126 * red + 0.7152 * green + 0.0722 * blue) * weight
            weighted_saturation += (
                ((maximum - minimum) / maximum) if maximum else 0.0
            ) * weight
            visible_rgb_max = max(visible_rgb_max, maximum)
    return {
        "width": image.width,
        "height": image.height,
        "mode": image.mode,
        "alpha_min": alpha.getextrema()[0],
        "alpha_max": alpha.getextrema()[1],
        "alpha_nonzero_pixels": alpha_nonzero,
        "alpha_opaque_pixels": alpha_opaque,
        "alpha_partial_pixels": alpha_partial,
        "alpha_coverage": round(alpha_nonzero / total, 9),
        "rgb_nonzero_in_alpha_pixels": rgb_nonzero_in_alpha,
        "alpha_weighted_luma": round(weighted_luma / alpha_weight, 6) if alpha_weight else 0.0,
        "alpha_weighted_saturation": (
            round(weighted_saturation / alpha_weight, 9) if alpha_weight else 0.0
        ),
        "visible_rgb_max": visible_rgb_max,
    }


def inspect_png(path: Path, expected_size: tuple[int, int]) -> dict:
    if not path.is_file():
        raise ConversionError(f"PNG does not exist: {path}")
    try:
        with Image.open(path) as image:
            image.load()
            if image.format != "PNG":
                raise ConversionError(f"file is not encoded as PNG: {path}")
            if image.size != expected_size:
                raise ConversionError(
                    f"unexpected PNG dimensions for {path.name}, expected "
                    f"{expected_size[0]}x{expected_size[1]}, got {image.width}x{image.height}"
                )
            stats = rgba_stats(image)
            extrema = image.getextrema()
    except (OSError, ValueError) as exc:
        raise ConversionError(f"cannot read PNG {path}: {exc}") from exc
    stats.update(
        {
            "sha256": sha256_path(path),
            "file_size": path.stat().st_size,
            "channel_extrema": [list(channel) for channel in extrema],
        }
    )
    return stats


def validate_input(path: Path, profile: dict) -> dict:
    source = profile["source"]
    stats = inspect_png(path, (source["width"], source["height"]))
    if stats["alpha_nonzero_pixels"] == 0:
        raise ConversionError(f"input PNG is completely transparent: {path}")
    if stats["alpha_partial_pixels"] == 0:
        raise ConversionError(f"input PNG has no partial transparency: {path}")
    if stats["rgb_nonzero_in_alpha_pixels"] == 0:
        raise ConversionError(f"input PNG has no RGB content inside its alpha mask: {path}")
    return stats


def validate_output(path: Path, profile: dict, input_stats: dict) -> dict:
    target = profile["target"]
    stats = inspect_png(path, (target["width"], target["height"]))
    if stats["file_size"] < 1024:
        raise ConversionError(f"output PNG file size is implausibly small: {stats['file_size']} bytes")
    if stats["alpha_nonzero_pixels"] == 0:
        raise ConversionError("output PNG is completely transparent")
    if stats["alpha_partial_pixels"] == 0 and input_stats["alpha_partial_pixels"] > 0:
        raise ConversionError("output PNG lost all partial transparency")
    if stats["rgb_nonzero_in_alpha_pixels"] == 0:
        raise ConversionError("output PNG has no RGB content inside its alpha mask")
    if stats["alpha_weighted_luma"] < input_stats["alpha_weighted_luma"] * 0.35:
        raise ConversionError(
            "output PNG is implausibly dark relative to the source, check the Blender bake type"
        )
    if (
        input_stats["alpha_weighted_saturation"] >= 0.05
        and stats["alpha_weighted_saturation"]
        < input_stats["alpha_weighted_saturation"] * 0.5
    ):
        raise ConversionError(
            "output PNG lost too much color saturation relative to the source"
        )
    if stats["channel_extrema"] in (
        [[0, 0], [0, 0], [0, 0], [0, 0]],
        [[0, 0], [0, 0], [0, 0], [255, 255]],
    ):
        raise ConversionError("output PNG is identical to a blank target")
    return stats


def parse_prefixed_json(output: str, prefix: str) -> dict | None:
    for line in reversed(output.splitlines()):
        if line.startswith(prefix):
            try:
                return json.loads(line[len(prefix) :])
            except json.JSONDecodeError:
                return None
    return None


def safe_stem(path: Path) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", path.stem).strip("._")
    return normalized or "texture"


def write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def write_json_atomic(path: Path, value: dict) -> None:
    write_text_atomic(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def job_log(
    path: Path,
    command: Sequence[str],
    completed: subprocess.CompletedProcess[str],
) -> None:
    content = (
        f"Command: {shlex.join(command)}\n"
        f"Exit code: {completed.returncode}\n\n"
        "STDOUT\n"
        f"{completed.stdout.rstrip()}\n\n"
        "STDERR\n"
        f"{completed.stderr.rstrip()}\n"
    )
    write_text_atomic(path, content)


def failure_message(combined_output: str, return_code: int) -> str:
    parsed = parse_prefixed_json(combined_output, ERROR_PREFIX)
    if parsed and parsed.get("error"):
        return str(parsed["error"])
    meaningful = [line.strip() for line in combined_output.splitlines() if line.strip()]
    if meaningful:
        return meaningful[-1]
    return f"Blender exited with code {return_code}"


def run_job(
    job: Job,
    index: int,
    blender: Path,
    template: Path,
    profile_path: Path,
    profile: dict,
    input_stats: dict,
    artifacts_dir: Path,
) -> dict:
    job.output_path.parent.mkdir(parents=True, exist_ok=True)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    log_path = artifacts_dir / "logs" / f"{index:03d}_{safe_stem(job.input_path)}.log"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{job.output_path.stem}.", suffix=".png", dir=job.output_path.parent
    )
    os.close(descriptor)
    temporary_output = Path(temporary_name)
    command = [
        str(blender),
        "--background",
        str(template),
        "--python-exit-code",
        "1",
        "--python",
        str(BAKE_SCRIPT),
        "--",
        "--input",
        str(job.input_path),
        "--output",
        str(temporary_output),
        "--profile",
        str(profile_path),
    ]
    warnings: list[str] = []
    try:
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        job_log(log_path, command, completed)
        combined_output = completed.stdout + "\n" + completed.stderr
        circular_dependency_text = (
            f'Circular dependency for image "{profile["target"]["image"]}"'
        )
        if circular_dependency_text in combined_output:
            warnings.append(
                "Blender reported the template's known target-image circular dependency warning"
            )
        if completed.returncode != 0:
            raise ConversionError(failure_message(combined_output, completed.returncode))
        blender_result = parse_prefixed_json(completed.stdout, RESULT_PREFIX)
        if not blender_result:
            raise ConversionError("Blender completed without a machine-readable bake result")
        output_stats = validate_output(temporary_output, profile, input_stats)
        os.replace(temporary_output, job.output_path)
        return {
            "status": "ok",
            "input": {
                "file": display_path(job.input_path),
                **input_stats,
            },
            "output": {
                "file": display_path(job.output_path),
                **output_stats,
            },
            "warnings": warnings,
            "log": display_path(log_path),
        }
    except (OSError, ConversionError) as exc:
        return {
            "status": "failed",
            "input": {"file": display_path(job.input_path), **input_stats},
            "output": {"file": display_path(job.output_path)},
            "error": str(exc),
            "warnings": warnings,
            "log": display_path(log_path),
        }
    finally:
        temporary_output.unlink(missing_ok=True)


def checkerboard(size: tuple[int, int], square: int = 24) -> Image.Image:
    image = Image.new("RGBA", size, (238, 238, 238, 255))
    draw = ImageDraw.Draw(image)
    for top in range(0, size[1], square):
        for left in range(0, size[0], square):
            if (left // square + top // square) % 2:
                draw.rectangle(
                    (left, top, min(left + square - 1, size[0]), min(top + square - 1, size[1])),
                    fill=(198, 198, 198, 255),
                )
    return image


def image_panel(path: Path, panel_size: int) -> Image.Image:
    panel = checkerboard((panel_size, panel_size))
    with Image.open(path) as image:
        contained = ImageOps.contain(
            image.convert("RGBA"),
            (panel_size, panel_size),
            method=Image.Resampling.LANCZOS,
        )
    left = (panel_size - contained.width) // 2
    top = (panel_size - contained.height) // 2
    panel.alpha_composite(contained, (left, top))
    return panel


def create_contact_sheet(successes: Iterable[tuple[Job, dict]], destination: Path) -> None:
    rows = list(successes)
    if not rows:
        return
    panel_size = 480
    outer_padding = 20
    gutter = 20
    label_height = 56
    row_height = panel_size + label_height + gutter
    width = outer_padding * 2 + panel_size * 2 + gutter
    height = outer_padding * 2 + row_height * len(rows) - gutter
    sheet = Image.new("RGBA", (width, height), (255, 255, 255, 255))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=16)
    for row_index, (job, result) in enumerate(rows):
        top = outer_padding + row_index * row_height
        source_panel = image_panel(job.input_path, panel_size)
        output_panel = image_panel(job.output_path, panel_size)
        output_left = outer_padding + panel_size + gutter
        sheet.alpha_composite(source_panel, (outer_padding, top + label_height))
        sheet.alpha_composite(output_panel, (output_left, top + label_height))
        source_coverage = result["input"]["alpha_coverage"]
        output_coverage = result["output"]["alpha_coverage"]
        draw.text(
            (outer_padding, top),
            f"SOURCE  {job.input_path.name}\nalpha coverage {source_coverage:.4f}",
            fill=(20, 20, 20, 255),
            font=font,
        )
        draw.text(
            (output_left, top),
            f"CONVERTED  {job.output_path.name}\nalpha coverage {output_coverage:.4f}",
            fill=(20, 20, 20, 255),
            font=font,
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    sheet.convert("RGB").save(destination, format="PNG", optimize=False)


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert TS4 adult-male-body RGBA textures to TS2 UVs with Blender"
    )
    parser.add_argument("paths", nargs="+", type=Path, help="input path(s), plus output for single mode")
    parser.add_argument("--output-dir", type=Path, help="batch output directory")
    parser.add_argument("--blender", type=Path, help="absolute Blender executable path")
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--artifacts-dir", type=Path, default=DEFAULT_ARTIFACTS_DIR)
    parser.add_argument("--manifest", type=Path, help="manifest path")
    parser.add_argument("--no-contact-sheet", action="store_true")
    parser.add_argument("--version", action="version", version=TOOL_VERSION)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = create_parser().parse_args(argv)
    try:
        profile_path = args.profile.resolve()
        profile = load_profile(profile_path)
        jobs = resolve_jobs(args.paths, args.output_dir)
        template = args.template.resolve()
        template_hash_before = verify_template(template, profile)
        blender = discover_blender(args.blender)
        actual_blender_version = blender_version(blender)
        expected_blender_version = tuple(profile["blender_version"])
        if actual_blender_version != expected_blender_version:
            raise ConversionError(
                f"Blender version mismatch, expected {'.'.join(map(str, expected_blender_version))}, "
                f"got {'.'.join(map(str, actual_blender_version))}"
            )
    except ConversionError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    artifacts_dir = args.artifacts_dir.resolve()
    if args.manifest:
        manifest_path = args.manifest.resolve()
    elif args.output_dir:
        manifest_path = args.output_dir.resolve() / "manifest.json"
    else:
        manifest_path = jobs[0].output_path.parent / "manifest.json"

    results: list[dict] = []
    successful_rows: list[tuple[Job, dict]] = []
    for index, job in enumerate(jobs, start=1):
        print(f"[{index:02d}/{len(jobs):02d}] {job.input_path.name}", end=" ", flush=True)
        try:
            input_stats = validate_input(job.input_path, profile)
        except ConversionError as exc:
            result = {
                "status": "failed",
                "input": {"file": display_path(job.input_path)},
                "output": {"file": display_path(job.output_path)},
                "error": str(exc),
                "warnings": [],
            }
        else:
            result = run_job(
                job,
                index,
                blender,
                template,
                profile_path,
                profile,
                input_stats,
                artifacts_dir,
            )
        results.append(result)
        if result["status"] == "ok":
            successful_rows.append((job, result))
            print("OK")
        else:
            print(f"FAILED: {result['error']}")

    template_hash_after = sha256_path(template)
    template_unchanged = template_hash_before == template_hash_after
    contact_sheet_path = artifacts_dir / "contact_sheet.png"
    contact_sheet_error = None
    if successful_rows and not args.no_contact_sheet:
        try:
            create_contact_sheet(successful_rows, contact_sheet_path)
        except (OSError, ValueError) as exc:
            contact_sheet_error = str(exc)

    manifest = {
        "tool_version": TOOL_VERSION,
        "profile": profile["id"],
        "blender": {
            "version": ".".join(map(str, actual_blender_version)),
            "executable": blender.name,
        },
        "template": {
            "filename": template.name,
            "sha256": template_hash_after,
            "unchanged": template_unchanged,
        },
        "contact_sheet": (
            display_path(contact_sheet_path)
            if successful_rows and not args.no_contact_sheet and contact_sheet_error is None
            else None
        ),
        "results": results,
    }
    if contact_sheet_error:
        manifest["contact_sheet_error"] = contact_sheet_error
    write_json_atomic(manifest_path, manifest)

    failures = sum(result["status"] != "ok" for result in results)
    global_failure = not template_unchanged or contact_sheet_error is not None
    print(f"Manifest: {display_path(manifest_path)}")
    if successful_rows and not args.no_contact_sheet and contact_sheet_error is None:
        print(f"Contact sheet: {display_path(contact_sheet_path)}")
    if not template_unchanged:
        print("FAILED: template hash changed during conversion", file=sys.stderr)
    if contact_sheet_error:
        print(f"FAILED: contact sheet generation failed: {contact_sheet_error}", file=sys.stderr)
    print(f"Completed: {len(successful_rows)} succeeded, {failures} failed")
    return 1 if failures or global_failure else 0


if __name__ == "__main__":
    raise SystemExit(main())
