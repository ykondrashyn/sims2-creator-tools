"""Download and verify the Tattooer body templates supported by this project."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_SHA256 = "52a57a89b6be568628afe374d7f2703bfb10dfb890e30e47c92d2cc8b7e56860"
DOWNLOAD_URLS = (
    "https://cdn.simfileshare.net/download/3996307/?dl",
    "https://drive.google.com/uc?export=download&id=1Bl7ZFvmwWIhW74H8TGdVMglgjGz-Qnmk",
)


@dataclass(frozen=True)
class TemplateSpec:
    preset: str
    filename: str
    archive_member: str
    sha256: str


TEMPLATES = {
    "am": TemplateSpec(
        preset="am",
        filename="AM-body-4t2-1024.blend",
        archive_member="3.4.1 Blender Templates/AM-body-4t2-1024.blend",
        sha256="f709db0a1ae34b663805ce7c95da2f363dcb1d67029c197c4ef15098a4a5a108",
    ),
    "af": TemplateSpec(
        preset="af",
        filename="AF-body-4t2-1024.blend",
        archive_member="3.4.1 Blender Templates/AF-body-4t2-1024.blend",
        sha256="e87f48a83350623abe97b1d8fe325153131305e40c43027df32a923e489e2958",
    ),
}


class TemplateFetchError(RuntimeError):
    """Raised when the pinned template cannot be obtained safely."""


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, destination: Path) -> None:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "tattooer-auto/0.1 (+https://www.tumblr.com/paluding/)"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        with destination.open("wb") as output:
            shutil.copyfileobj(response, output, length=1024 * 1024)


def fetch_archive(destination: Path) -> str:
    errors: list[str] = []
    for url in DOWNLOAD_URLS:
        try:
            download(url, destination)
            actual_hash = sha256_path(destination)
            if actual_hash != ARCHIVE_SHA256:
                raise TemplateFetchError(
                    f"archive SHA-256 mismatch, expected {ARCHIVE_SHA256}, got {actual_hash}"
                )
            return url
        except (OSError, urllib.error.URLError, TemplateFetchError) as exc:
            errors.append(f"{url}: {exc}")
    raise TemplateFetchError("all template download sources failed\n" + "\n".join(errors))


def verify_existing(spec: TemplateSpec, destination: Path) -> bool:
    if not destination.exists():
        return False
    actual_hash = sha256_path(destination)
    if actual_hash != spec.sha256:
        raise TemplateFetchError(
            f"refusing to overwrite mismatched template at {destination}\n"
            f"expected SHA-256 {spec.sha256}\n"
            f"actual SHA-256   {actual_hash}"
        )
    return True


def write_template(destination: Path, template_bytes: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "wb") as output:
            output.write(template_bytes)
            output.flush()
            os.fsync(output.fileno())
        if destination.exists():
            raise TemplateFetchError(
                f"destination appeared during download, refusing to overwrite {destination}"
            )
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)


def install_templates(
    specs: list[TemplateSpec], destination_override: Path | None = None
) -> list[tuple[TemplateSpec, Path, str]]:
    if destination_override is not None and len(specs) != 1:
        raise TemplateFetchError("--destination requires --preset am or --preset af")
    destinations = {
        spec.preset: (
            destination_override.resolve()
            if destination_override is not None
            else (PROJECT_ROOT / "templates" / spec.filename).resolve()
        )
        for spec in specs
    }
    outcomes: list[tuple[TemplateSpec, Path, str]] = []
    missing: list[TemplateSpec] = []
    for spec in specs:
        destination = destinations[spec.preset]
        if verify_existing(spec, destination):
            outcomes.append((spec, destination, "already present and verified"))
        else:
            missing.append(spec)

    if not missing:
        return outcomes

    with tempfile.TemporaryDirectory(prefix="tattooer-template-") as temp_dir:
        archive_path = Path(temp_dir) / "tattooer.zip"
        source_url = fetch_archive(archive_path)
        extracted: dict[str, bytes] = {}
        with zipfile.ZipFile(archive_path) as archive:
            for spec in missing:
                try:
                    template_bytes = archive.read(spec.archive_member)
                except KeyError as exc:
                    raise TemplateFetchError(
                        f"archive does not contain {spec.archive_member}"
                    ) from exc
                actual_hash = hashlib.sha256(template_bytes).hexdigest()
                if actual_hash != spec.sha256:
                    raise TemplateFetchError(
                        f"template SHA-256 mismatch for {spec.filename}, "
                        f"expected {spec.sha256}, got {actual_hash}"
                    )
                extracted[spec.preset] = template_bytes

        for spec in missing:
            destination = destinations[spec.preset]
            write_template(destination, extracted[spec.preset])
            outcomes.append((spec, destination, f"downloaded from {source_url}"))
    return sorted(outcomes, key=lambda item: item[0].preset, reverse=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download and verify the AM and AF Tattooer body templates"
    )
    parser.add_argument(
        "--preset",
        choices=("all", "am", "af"),
        default="all",
        help="template preset to install, default: all",
    )
    parser.add_argument(
        "--destination",
        type=Path,
        help="custom destination, only valid with one preset",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    specs = list(TEMPLATES.values()) if args.preset == "all" else [TEMPLATES[args.preset]]
    try:
        outcomes = install_templates(specs, args.destination)
    except TemplateFetchError as exc:
        print(f"Template fetch failed: {exc}", file=sys.stderr)
        return 1
    for spec, destination, outcome in outcomes:
        print(f"Template {outcome}: {destination}")
        print(f"SHA-256: {spec.sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
