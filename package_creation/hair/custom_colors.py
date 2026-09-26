"""Bounded, job-local curve definitions. Built-in presets remain immutable."""
from __future__ import annotations

import math
import re
from pathlib import PurePath

from .colors import BY_NAME

MAX_CUSTOM = 16
MAX_BYTES = 256 * 1024
CHANNELS = ("value", "red", "green", "blue")


def number(value, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"Curve values must be finite numbers between {low} and {high}")
    return value


def parse_gimp(data: bytes, filename: str = "") -> dict:
    if len(data) > MAX_BYTES:
        raise ValueError("Curve file exceeds 256 KiB")
    try:
        source = data.decode("utf-8-sig")
    except UnicodeError as exc:
        raise ValueError("Import a GIMP text curve preset with 256 samples per channel") from exc
    if not source.startswith("# GIMP curves tool settings"):
        raise ValueError("Import a GIMP text curve preset with 256 samples per channel")
    # Parse data only, with bounded nesting and no evaluation of uploaded text.
    source = re.sub(r"(?m)^\s*#.*$", "", source)
    tokens = re.findall(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()]+', source)
    forms, stack = [], []
    for token in tokens:
        if token == "(":
            if len(stack) >= 16:
                raise ValueError("GIMP curve nesting is too deep")
            node = []
            (stack[-1] if stack else forms).append(node)
            stack.append(node)
        elif token == ")":
            if not stack:
                raise ValueError("Malformed GIMP curve parentheses")
            stack.pop()
        elif stack:
            stack[-1].append(token)
        else:
            raise ValueError("Malformed GIMP curve text")
    if stack:
        raise ValueError("Malformed GIMP curve parentheses")
    channels, active = {}, None
    for form in forms:
        if not form or not isinstance(form[0], str):
            raise ValueError("Malformed GIMP curve setting")
        key = form[0]
        if key in {"trc", "linear"}:
            if len(form) != 2 or not isinstance(form[1], str) or form[1] not in ({"non-linear", "perceptual"} if key == "trc" else {"no", "false"}):
                raise ValueError("Linear-light curves are unsupported. Export a perceptual sRGB GIMP curve")
        elif key in {"time", "name"}:
            if len(form) != 2 or not isinstance(form[1], str):
                raise ValueError("Malformed GIMP curve metadata")
        elif key == "channel":
            if active is not None or len(form) != 2 or not isinstance(form[1], str) or form[1] not in (*CHANNELS, "alpha") or form[1] in channels:
                raise ValueError("Each GIMP channel must appear exactly once")
            active = form[1]
        elif key == "curve":
            if active is None:
                raise ValueError("GIMP curve has no channel")
            fields = {}
            for entry in form[1:]:
                if not isinstance(entry, list) or not entry or not isinstance(entry[0], str) or entry[0] in fields:
                    raise ValueError("Malformed or duplicate GIMP curve setting")
                fields[entry[0]] = entry[1:]
            if set(fields) - {"curve-type", "n-points", "points", "point-types", "n-samples", "samples"}:
                raise ValueError("Unsupported GIMP curve setting")
            if fields.get("n-samples") != ["256"] or fields.get("curve-type") not in (["free"], ["smooth"]):
                raise ValueError("Export a GIMP curve with 256 samples per channel")
            values = fields.get("samples", [])
            if len(values) != 257 or values[0] != "256":
                raise ValueError("Each GIMP channel must contain exactly 256 samples")
            try:
                channels[active] = [number(float(v), 0, 1) for v in values[1:]]
            except (TypeError, ValueError) as exc:
                raise ValueError("GIMP samples must be finite numbers between 0 and 1") from exc
            active = None
        else:
            raise ValueError("Unsupported GIMP curve setting")
    if active or set(channels) != {*CHANNELS, "alpha"}:
        raise ValueError("A GIMP preset must include Value, Red, Green, Blue and Alpha channels")
    if any(abs(v - i / 255) > 0.00000051 for i, v in enumerate(channels.pop("alpha"))):
        raise ValueError("This curve changes transparency. Reset its Alpha channel to identity in GIMP and export again")
    return {"source": "gimp", "filename": PurePath(filename.replace("\\", "/")).name[:160], "channels": channels}


def sample(points: list) -> list[float]:
    if not isinstance(points, list) or not 2 <= len(points) <= 32:
        raise ValueError("Use 2 through 32 control points per channel")
    for point in points:
        if not isinstance(point, list) or len(point) != 2:
            raise ValueError("Each curve point needs an input and output")
        for value in point:
            number(value, 0, 255)
    if points[0][0] != 0 or points[-1][0] != 255 or any(a[0] >= b[0] for a, b in zip(points, points[1:])):
        raise ValueError("Curve inputs must increase from 0 to 255 without duplicates")
    result, segment = [], 0
    for x in range(256):
        while segment < len(points) - 2 and x > points[segment + 1][0]:
            segment += 1
        (x0, y0), (x1, y1) = points[segment:segment + 2]
        result.append((y0 + (y1 - y0) * (x - x0) / (x1 - x0)) / 255)
    return result


def normalize_curve(curve: dict) -> tuple[dict, list[list[int]]]:
    if not isinstance(curve, dict):
        raise ValueError("Supply a GIMP curve or editor points")
    source = curve.get("source")
    field = "channels" if source == "gimp" else "points"
    if not isinstance(source, str) or source not in {"gimp", "editor"} or set(curve) - {"source", field, "filename"}:
        raise ValueError("Supply a GIMP curve or editor points")
    values = curve.get(field)
    if not isinstance(values, dict) or set(values) != set(CHANNELS):
        raise ValueError("Supply Value, Red, Green and Blue curves")
    if source == "gimp":
        for values_channel in values.values():
            if not isinstance(values_channel, list) or len(values_channel) != 256:
                raise ValueError("Each channel must contain exactly 256 samples")
            for value in values_channel:
                number(value, 0, 1)
        samples = values
    else:
        samples = {key: sample(points) for key, points in values.items()}
    # GIMP maps the individual channel first, then interpolates the Value curve.
    tables = []
    for channel in CHANNELS[1:]:
        mapped = []
        for value in samples[channel]:
            position = value * 255
            i = min(254, int(position))
            fraction = position - i
            result = samples["value"][i] * (1 - fraction) + samples["value"][i + 1] * fraction
            mapped.append(min(255, max(0, int(result * 255 + 0.5))))
        tables.append(mapped)
    filename = curve.get("filename", "")
    if not isinstance(filename, str) or len(filename) > 160 or any(ord(c) < 32 for c in filename):
        raise ValueError("Invalid curve source filename")
    return {"source": source, field: values, **({"filename": filename} if source == "gimp" else {})}, tables


def normalize_colors(value: list) -> list[dict]:
    if not isinstance(value, list) or len(value) > MAX_CUSTOM:
        raise ValueError("Add at most 16 custom colors per job")
    names = {name.replace(" ", "").casefold() for name in BY_NAME}
    ids, result = set(), []
    for color in value:
        if not isinstance(color, dict) or set(color) != {"id", "name", "bin", "curve"}:
            raise ValueError("Each custom color needs an ID, name, game category and curve")
        key, name, category = color["id"], color["name"], color["bin"]
        if not isinstance(key, str) or not re.fullmatch(r"custom:[0-9a-f]{32}", key) or key in ids:
            raise ValueError("Custom color IDs must be distinct")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,47}", name) or name != name.strip():
            raise ValueError("Color names need 1 to 48 letters, numbers, spaces, underscores or hyphens, starting with a letter or number")
        compact = name.replace(" ", "").casefold()
        if compact in names:
            raise ValueError(f"Color name {name} conflicts with another color or package filename")
        if isinstance(category, bool) or not isinstance(category, int) or category not in range(5):
            raise ValueError("Choose Custom, Black, Brown, Blond or Red")
        curve, tables = normalize_curve(color["curve"])
        result.append({**color, "curve": curve, "tables": tables})
        ids.add(key)
        names.add(compact)
    return result


class JobColors:
    def __init__(self, job: dict):
        self.items = dict(BY_NAME)
        self.tables = {}
        for color in job.get("custom_colors", []):
            self.items[color["id"]] = {**color, "kind": "natural" if color["bin"] else "unnatural", "family": None}
            self.tables[color["id"]] = color["tables"]

    def name(self, key):
        return self.items[key]["name"]

    def elder(self, key, elder=True):
        return "Mail Bomb" if elder and self.items[key]["kind"] == "natural" else key
