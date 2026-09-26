"""One sRGB image pipeline shared by previews and package generation."""
from __future__ import annotations

import io
import math
import struct
from pathlib import Path

from PIL import Image, ImageCms, UnidentifiedImageError

from .colors import BASES, curves


def source_texture_path(directory: Path, job: dict, key: str) -> Path:
    """New jobs use embedded atlases, older image-upload jobs retain their inputs."""
    if job.get("texture_source") == "embedded":
        return directory / "template-textures" / f"{key}.png"
    return directory / "uploads" / job["assignments"][key]


def bmp_profile(path: Path) -> bytes | None:
    """Read BMP color metadata that Pillow's BMP decoder does not expose.

    BITMAPV5HEADER profile offsets are relative to the DIB header, after the
    14-byte file header. Linked profiles must never be opened on the server.
    https://learn.microsoft.com/en-us/windows/win32/api/wingdi/ns-wingdi-bitmapv5header
    """
    with path.open("rb") as source:
        header = source.read(138)
        dib_size = struct.unpack_from("<I", header, 14)[0]
        if dib_size not in (108, 124):
            return None
        color_space = struct.unpack_from("<I", header, 14 + 56)[0]
        if color_space in (0x73524742, 0x57696E20):  # sRGB, Windows color space
            return None
        if color_space == 0 and not any(header[14 + 60:14 + 108]):
            return None  # No calibration or profile was supplied.
        if color_space == 0x4C494E4B:
            raise ValueError("The BMP uses a linked color profile. Embed the profile or export an sRGB PNG")
        if dib_size != 124 or color_space != 0x4D424544:
            raise ValueError("The BMP color space is unsupported. Export an sRGB PNG or embed an ICC profile in the BMP")
        offset, length = struct.unpack_from("<II", header, 14 + 112)
        if offset < dib_size or not 0 < length <= 4 * 1024 * 1024 or 14 + offset + length > path.stat().st_size:
            raise ValueError("The BMP has an invalid embedded color profile")
        source.seek(14 + offset)
        return source.read(length)


def load_texture(path: Path, size: tuple[int, int] | None = None, *, mask: bool = False) -> Image.Image:
    try:
        with Image.open(path) as image:
            if image.format not in {"PNG", "BMP"} or getattr(image, "n_frames", 1) != 1:
                raise ValueError("Upload a single-frame PNG or BMP")
            if max(image.size) > 2048 or min(image.size) < 1:
                raise ValueError("Texture dimensions must be between 1 and 2048 pixels")
            if size and image.size != size:
                raise ValueError(f"Texture dimensions are {image.width} by {image.height}, but this slot requires {size[0]} by {size[1]}. Use a matching recolor package for this texture")
            image.load()
            if mask:
                rgb = image.convert("RGB")
                r, g, b = rgb.split()
                if r.tobytes() != g.tobytes() or r.tobytes() != b.tobytes():
                    raise ValueError("The recolor mask must be grayscale, white recolors and black preserves the input")
                return r
            alpha = image.convert("RGBA").getchannel("A")
            rgb = image.convert("RGB")
            profile = bmp_profile(path) if image.format == "BMP" else image.info.get("icc_profile")
            if profile:
                try:
                    source = ImageCms.ImageCmsProfile(io.BytesIO(profile))
                    # A grayscale profile must be applied to a grayscale source.
                    mode = "L" if source.profile.xcolor_space.strip() == "GRAY" else "RGB"
                    rgb = ImageCms.profileToProfile(image.convert(mode), source,
                                                   ImageCms.createProfile("sRGB"), outputMode="RGB")
                except Exception as exc:
                    raise ValueError("The texture has an invalid or unsupported embedded color profile") from exc
            rgb.putalpha(alpha)
            return rgb
    except (UnidentifiedImageError, OSError, struct.error, Image.DecompressionBombError) as exc:
        raise ValueError("The file is not a valid supported PNG or BMP") from exc


def settings(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) - {"base", "black", "white", "gamma", "png_alpha"}:
        raise ValueError("Invalid color preparation settings")
    result = {"base": "Volatile", "black": 0, "white": 255, "gamma": 1.0, "png_alpha": False, **value}
    if result["base"] not in BASES or not isinstance(result["png_alpha"], bool):
        raise ValueError("Select a supported input base and alpha option")
    for key in ["black", "white", "gamma"]:
        if isinstance(result[key], bool) or not isinstance(result[key], (int, float)) or not math.isfinite(result[key]):
            raise ValueError("Levels must be finite numbers")
    if not 0 <= result["black"] < result["white"] <= 255 or not 0.1 <= result["gamma"] <= 5:
        raise ValueError("Use black < white within 0 to 255 and gamma within 0.1 to 5")
    return result


def texture_settings(value: dict | None, slots: list[dict], defaults: dict) -> dict:
    """Resolve explicit per-TXTR preparation, with legacy global settings fallback."""
    defaults = settings(defaults)
    keys = {slot["id"] for slot in slots}
    if value is None:
        return {key: dict(defaults) for key in keys}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("Supply preparation settings for every texture slot using its inspected ID")
    result = {}
    for key, config in value.items():
        if not isinstance(config, dict):
            raise ValueError("Each texture's preparation settings must be an object")
        result[key] = settings({**defaults, **config})
    return result


def fit_package_inputs(image: Image.Image, size: tuple[int, int],
                       mask: Image.Image | None = None) -> tuple[Image.Image, Image.Image | None]:
    """Fit the entire atlas to its assigned slot, without cropping or remapping UVs."""
    if mask is not None and mask.size != image.size:
        raise ValueError("The recolor mask must match the uploaded texture dimensions")
    if image.size != size:
        # Resize RGB separately so transparent input pixels keep their color when
        # the user chooses the template's alpha instead of the uploaded alpha.
        rgb = image.convert("RGB").resize(size, Image.Resampling.LANCZOS)
        rgb.putalpha(image.getchannel("A").resize(size, Image.Resampling.LANCZOS))
        image = rgb
        if mask is not None:
            mask = mask.resize(size, Image.Resampling.LANCZOS)
    return image, mask


def apply_curve(image: Image.Image, name: str, tables=None) -> Image.Image:
    return image.convert("RGB").point(sum((list(t) for t in (tables if tables is not None else curves()[name])), []))


def prepare(image: Image.Image, config: dict) -> Image.Image:
    config = settings(config)
    base = config["base"]
    if base == "Arbitrary texture":
        # Explicit sRGB Rec.709 luma, followed by standard levels gamma.
        grey = image.convert("RGB").convert("L", (0.2126, 0.7152, 0.0722, 0))
        low, high, gamma = (config[k] for k in ["black", "white", "gamma"])
        grey = grey.point([round(255 * min(1, max(0, (i - low) / (high - low))) ** (1 / gamma)) for i in range(256)])
        return apply_curve(grey.convert("RGB"), "base:arbitrary-grey")
    return apply_curve(image, "Volatile" if base == "Volatile" else "base:" + base.replace("Pooklet ", ""))


def render(image: Image.Image, template: Image.Image, config: dict, color: str,
           mask: Image.Image | None = None, *, tables=None) -> tuple[Image.Image, Image.Image]:
    config = settings(config)
    base = prepare(image, config)
    target = apply_curve(base, color, tables)
    if mask is not None:
        target = Image.composite(target, image.convert("RGB"), mask)
    alpha = (image if config["png_alpha"] else template).getchannel("A")
    target.putalpha(alpha)
    base.putalpha(alpha)
    return base, target
