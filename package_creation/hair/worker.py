"""Sequential package generation in a cancellable, time-bounded process."""
from __future__ import annotations

import json
import hashlib
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from .colors import FAMILIES
from .custom_colors import JobColors
from .install import BUILDER
from .game_meshes import resolve_game_meshes
from .processing import fit_package_inputs, load_texture, render, texture_settings, source_texture_path

MIB = 1024 * 1024


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def disk_size(directory: Path) -> int:
    return sum(p.stat().st_size for p in directory.rglob("*") if p.is_file())


def filename(creator: str, hair: str, color: str) -> str:
    return f"{creator}_{hair}_{color.replace(' ', '')}.package"


def output_filename(job: dict, color: str) -> str:
    # Read-only compatibility for completed texture jobs until their normal expiry.
    name = filename(job["creator"], job["hair_name"], JobColors(job).name(color))
    if job.get("output_kind") == "textures":
        return name.removesuffix(".package") + "." + job["output_format"].lower()
    return name


def archive_filename(job: dict) -> str:
    suffix = "Textures" if job.get("output_kind") == "textures" else "Recolors"
    return f"{job['creator']}_{job['hair_name']}_{suffix}.zip"


def age_labels(inspection: dict) -> list[str]:
    flags = 0
    for age in inspection["ages"]:
        flags |= age["age"]
    return [name for bit, name in [(1, "Toddler"), (2, "Child"), (4, "Teen"), (64, "Young Adult"),
                                  (8, "Adult"), (16, "Elder"), (32, "Baby")] if flags & bit]


def readme(job: dict) -> str:
    colors = job["colors"]
    palette = JobColors(job)
    config = job["settings"]
    configs = texture_settings(job.get("texture_settings"), job["inspection"]["textures"], config)
    embedded = job.get("texture_source") == "embedded"
    grey_only = all(palette.items[c]["kind"] == "grey" for c in colors)
    ages = ["Elder"] if grey_only else age_labels(job["inspection"])
    return "\n".join([
        f"{job['creator']} / {job['hair_name']} hair recolors", "",
        "INSTALLATION", "Extract the selected .package files into your Sims 2 user folder's Downloads directory.",
        "Enable custom content and restart the game if requested. Keep the hairstyle's required mesh installed.",
        "Remove these generated files to uninstall. Each recolor includes its own elder resources when supported.",
        "", "TEMPLATE AND REQUIREMENTS", job["template_label"], job["requirements"],
        "Supported ages in this selection: " + ", ".join(ages),
        "Gender flags from the template: " + ", ".join(str(a) for a in sorted({a['gender'] for a in job['inspection']['ages']})) + " (1 female, 2 male, 3 both)",
        "External mesh resource references: " + ", ".join(job["inspection"]["external_meshes"]),
        *(["Verified installed game mesh references: " + ", ".join(m["key"] for m in job["game_meshes"])] if job.get("game_meshes") else []),
        *(["Included mesh files: " + ", ".join("Meshes/" + m["filename"] for m in job["meshes"]),
           "Copy the Meshes folder into Downloads together with the recolors. Install each mesh only once.",
           "The included meshes are unchanged copies from the uploaded hairstyle packages."] if job.get("meshes") else []),
        "The package preserves the selected hairstyle's mesh references and UV layouts.",
        "", "TEXTURE SIZES",
        *[f"Texture {i + 1}: {'embedded' if embedded else 'uploaded'} {s.get('source_width', s['width'])} by {s.get('source_height', s['height'])}, package {s['width']} by {s['height']} pixels."
          for i, s in enumerate(job["inspection"]["textures"])],
        *(["Source RGB and alpha come directly from each reachable embedded texture in the recolor package.",
           "No separate texture images are required. Toddler, younger and Elder atlases retain their own resource routes.",
           "Accessories sharing an atlas receive the same color processing as the hair on that atlas."] if embedded else [
           "Inputs and recolor masks are resized to each assigned template slot with Lanczos filtering before color processing.",
           "The entire image is used without cropping. Different aspect ratios are stretched to the template dimensions."]),
        "Template texture dimensions, compression format and full mip chains are preserved.",
        "", "SELECTED COLORS", ", ".join(palette.name(c) for c in colors), "",
        "Built-in palette: 43 fixed Pooklet presets.",
        *(["", "CUSTOM COLORS FOR THIS JOB", *[
            f"{c['name']}: {['Custom', 'Black', 'Brown', 'Blond', 'Red'][c['bin']]}, "
            + ("imported GIMP curve " + c['curve'].get('filename', '') if c['curve']['source'] == 'gimp' else "created in the browser curve editor")
            for c in job.get("custom_colors", []) if c['id'] in colors],
            "Custom curves use the prepared Volatile base and have independent hairstyle families."]
          if any(c.startswith("custom:") for c in colors) else []),
        "FAMILY AND ELDER BEHAVIOR",
        *[f"Family {i + 1}: " + " / ".join(f) + " (black / brown / blond / red)" for i, f in enumerate(FAMILIES)],
        "Install the desired members of a family for color switching. Missing selections are not generated.",
        ("Natural-color elders use Mail Bomb grey. Unnatural colors stay custom and keep their color as elders."
         if "Elder" in age_labels(job["inspection"]) else "This template has no Elder entry. These recolors do not add Elder support."),
        "Standalone Mail Bomb and Pipe Bomb require an Elder entry and are elder-only greys.",
        "Custom hairtone identities are separate from hairstyle families. This batch can coexist with the source and other batches.",
        "", "BASE PREPARATION", "Preparation used for each assigned texture:",
        *[f"Texture {i + 1} ({', '.join(age_labels({'ages': [{'age': a} for a in slot['ages']]}))}), {slot['id']}: "
          f"Input base: {configs[slot['id']]['base']}. "
          f"Black point: {configs[slot['id']]['black']}, white point: {configs[slot['id']]['white']}, gamma: {configs[slot['id']]['gamma']}. "
          f"Alpha source: {'uploaded texture' if configs[slot['id']]['png_alpha'] else 'template'}. "
          f"Mask: {'assigned' if slot['id'] in job.get('masks', {}) else 'none'}."
          for i, slot in enumerate(job['inspection']['textures'])],
        "Arbitrary texture mode is approximate: sRGB luma desaturation and levels, then the dedicated grey-base-to-Volatile curve.",
        "Known Pooklet Grey uses the separate PookletSpecial Grey-to-Volatile curve.",
        "Embedded color profiles are converted to sRGB. Untagged textures are interpreted as sRGB.",
        "Each target curve is applied independently to the prepared base.",
        "Compression can quantize alpha.",
        "Masks: " + ("white recolors, black preserves original input RGB, intermediate values blend" if job.get("masks") else "none"),
        "", "CREDITS", "Texture and recolor submission: " + job["creator"],
        "Source hairstyle and recolor: " + job.get("template_credit", job["template_label"]),
        "Pooklet: original palette, curves, and Mansion & Garden Swirl recolor template when selected.",
        "IaKoa: GIMP curve conversions. https://iakoasims.tumblr.com/post/88756518223/pooklet-for-gimp",
        "Default template source: https://simfileshare.net/download/321854/",
        "Original game hairstyle: Maxis / Electronic Arts. Custom templates retain their original creators' rights.",
        "Built-in presets remain on the server. Custom curves expire with this job. Raw curves are not included in this archive.",
        "", "VALIDATION", "Every package was reopened and structurally validated before this archive was published.",
        "Structural checks do not establish gameplay compatibility for a newly uploaded texture or custom template.", "",
    ])


def build(directory: Path) -> None:
    job = json.loads((directory / "job.json").read_text())
    palette = JobColors(job)
    if job.get("output_kind") == "textures":
        raise ValueError("Texture export was removed. Start a new hair recolor package submission")
    work = directory / "work"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir()
    partial = directory / "recolors.partial"
    partial.unlink(missing_ok=True)
    report_path = directory / "validation.json"
    source = {}
    configs = texture_settings(job.get("texture_settings"), job["inspection"]["textures"], job["settings"])
    for slot in job["inspection"]["textures"]:
        key = slot["id"]
        size = (slot["width"], slot["height"])
        image = load_texture(source_texture_path(directory, job, key))
        mask = load_texture(directory / "uploads" / job["masks"][key], image.size, mask=True) if key in job["masks"] else None
        image, mask = fit_package_inputs(image, size, mask)
        source[key] = (image, load_texture(directory / "template-textures" / f"{key}.png", size), mask)
    reports, keys = [], set()
    with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
        for index, color in enumerate(job["colors"]):
            atomic_json(directory / "progress.json", {"completed": index, "total": len(job["colors"]), "color": palette.name(color)})
            item = palette.items[color]
            textures, elders = {}, {}
            for slot_id, (image, template, mask) in source.items():
                for target, paths, suffix in [(color, textures, ""), (palette.elder(color), elders, "-elder")]:
                    if suffix and item["kind"] != "natural":
                        continue
                    output = work / f"{slot_id}{suffix}.png"
                    render(image, template, configs[slot_id], target, mask, tables=palette.tables.get(target))[1].save(output)
                    paths[slot_id] = str(output)
            spec = {**job["identities"][color], "template": str(directory / "template.package"),
                    "label": f"{job['creator']} {job['hair_name']} {palette.name(color)}", "color": palette.name(color),
                    "bin": item["bin"], "grey_only": item["kind"] == "grey",
                    "grey_elders": item["kind"] == "natural", "textures": textures, "elder_textures": elders}
            spec_path = work / "spec.json"
            atomic_json(spec_path, spec)
            output = work / output_filename(job, color)
            result = subprocess.run([str(BUILDER), "build", "--spec", str(spec_path), "--output", str(output)],
                                    capture_output=True, text=True, timeout=120)
            if result.returncode:
                raise ValueError(result.stderr.strip()[:2000])
            report = json.loads(result.stdout)
            if keys.intersection(report["resource_keys"]):
                raise ValueError("Resource identities collide between selected packages")
            keys.update(report["resource_keys"])
            reports.append({"color": palette.name(color), "color_id": color, "filename": output.name, **report})
            if disk_size(directory) > job["limits"]["temporary_bytes"]:
                raise ValueError("Hair job exceeds the 1 GiB temporary data limit")
            archive.write(output, output.name)
            output.unlink()
            if partial.stat().st_size > job["limits"]["zip_bytes"]:
                raise ValueError("Hair archive exceeds the 512 MiB ZIP limit")
        mesh_reports, mesh_keys = [], set()
        for mesh in job.get("meshes", []):
            path = directory / "meshes" / mesh["filename"]
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != mesh["sha256"]:
                raise ValueError("An included mesh changed after import")
            result = subprocess.run([str(BUILDER), "inventory", "--package", str(path)], capture_output=True, text=True, timeout=30)
            if result.returncode:
                raise ValueError("An included mesh failed DBPF validation")
            info = json.loads(result.stdout)
            if info["kind"] != "mesh" or set(info["resource_keys"]) & (keys | mesh_keys):
                raise ValueError("Included mesh resource identities conflict")
            mesh_keys.update(info["resource_keys"])
            mesh_reports.append({"filename": "Meshes/" + mesh["filename"], "sha256": digest, **info})
            archive.write(path, "Meshes/" + mesh["filename"])
        game_keys = {m["key"] for m in job.get("game_meshes", [])}
        if game_keys and resolve_game_meshes(game_keys) != job["game_meshes"]:
            raise ValueError("The installed game mesh catalog changed. Inspect the hairstyle packages again")
        if mesh_reports and (not set(job["inspection"]["external_meshes"]).issubset(mesh_keys | game_keys)
                             or any(set(m["links"]) - mesh_keys for m in mesh_reports)):
            raise ValueError("Included meshes do not resolve the selected hairstyle's references")
        archive.writestr("README.txt", readme(job))
    if partial.stat().st_size > job["limits"]["zip_bytes"] or disk_size(directory) > job["limits"]["temporary_bytes"]:
        raise ValueError("Hair job exceeds its output or temporary data limit")
    with zipfile.ZipFile(partial) as archive:
        if archive.testzip() is not None:
            raise ValueError("ZIP integrity validation failed")
    atomic_json(report_path, {"status": "passed", "packages": reports, "meshes": mesh_reports,
                              "game_meshes": job.get("game_meshes", []), "gameplay": "not tested for this submission"})
    partial.replace(directory / "recolors.zip")
    shutil.rmtree(work)
    atomic_json(directory / "progress.json", {"completed": len(reports), "total": len(reports), "color": None})


if __name__ == "__main__":
    try:
        build(Path(sys.argv[1]).resolve())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
