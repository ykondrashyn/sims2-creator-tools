"""Run one verified selected-to-active bake inside Blender."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import traceback

import bpy


RESULT_PREFIX = "TATTOOER_RESULT="
ERROR_PREFIX = "TATTOOER_ERROR="


class BakeError(RuntimeError):
    """Raised when template, input, bake, or output validation fails."""


def blender_arguments() -> list[str]:
    if "--" not in sys.argv:
        return []
    return sys.argv[sys.argv.index("--") + 1 :]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bake one TS4 body texture to TS2 UVs")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    return parser.parse_args(blender_arguments())


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise BakeError(message)


def float_matches(actual: float, expected: float) -> bool:
    return math.isclose(actual, expected, rel_tol=1e-6, abs_tol=1e-6)


def find_material_node(obj, material_name: str, node_name: str):
    assigned_materials = [slot.material for slot in obj.material_slots if slot.material]
    material = bpy.data.materials.get(material_name)
    require(material is not None, f"missing material {material_name!r}")
    require(material in assigned_materials, f"material {material_name!r} is not assigned to {obj.name!r}")
    require(material.use_nodes, f"material {material_name!r} does not use nodes")
    node = material.node_tree.nodes.get(node_name)
    require(node is not None, f"missing node {node_name!r} in material {material_name!r}")
    require(node.type == "TEX_IMAGE", f"node {node_name!r} in {material_name!r} is not an Image Texture")
    return material, node


def validate_template(profile: dict):
    blend_path = Path(bpy.data.filepath).resolve()
    require(blend_path.is_file(), "the loaded Blender file has no readable path")
    expected_template = profile["template"]
    require(
        blend_path.name == expected_template["filename"],
        f"wrong template filename, expected {expected_template['filename']!r}, got {blend_path.name!r}",
    )
    actual_hash = sha256_path(blend_path)
    require(
        actual_hash == expected_template["sha256"],
        f"template SHA-256 mismatch, expected {expected_template['sha256']}, got {actual_hash}",
    )
    require(
        list(bpy.app.version) == profile["blender_version"],
        f"Blender version mismatch, expected {profile['blender_version']}, got {list(bpy.app.version)}",
    )

    source_profile = profile["source"]
    target_profile = profile["target"]
    source = bpy.data.objects.get(source_profile["object"])
    target = bpy.data.objects.get(target_profile["object"])
    require(source is not None, f"missing source object {source_profile['object']!r}")
    require(target is not None, f"missing target object {target_profile['object']!r}")
    require(source.type == "MESH", f"source object {source.name!r} is not a mesh")
    require(target.type == "MESH", f"target object {target.name!r} is not a mesh")
    require(source.data.name == source_profile["mesh"], f"unexpected source mesh {source.data.name!r}")
    require(target.data.name == target_profile["mesh"], f"unexpected target mesh {target.data.name!r}")
    require(source_profile["uv_layer"] in source.data.uv_layers, "source UV layer is missing")
    require(target_profile["uv_layer"] in target.data.uv_layers, "target UV layer is missing")

    source_material, source_node = find_material_node(
        source, source_profile["material"], source_profile["image_node"]
    )
    target_material, target_node = find_material_node(
        target, target_profile["material"], target_profile["image_node"]
    )
    target_image = target_node.image
    require(target_image is not None, "target Image Texture node has no image")
    require(target_image.name == target_profile["image"], f"unexpected target image {target_image.name!r}")
    require(list(target_image.size) == [target_profile["width"], target_profile["height"]], "unexpected target image size")
    require(target_image.channels == target_profile["channels"], "unexpected target image channel count")
    require(target_image.alpha_mode == target_profile["alpha_mode"], "unexpected target alpha mode")
    require(target_image.colorspace_settings.name == target_profile["colorspace"], "unexpected target color space")

    scene = bpy.context.scene
    bake_profile = profile["bake"]
    bake = scene.render.bake
    require(scene.render.engine == bake_profile["render_engine"], "unexpected render engine")
    require(scene.cycles.bake_type == bake_profile["bake_type"], "unexpected Cycles bake type")
    require(bake.target == bake_profile["target"], "unexpected bake target")
    require(bake.use_selected_to_active == bake_profile["use_selected_to_active"], "unexpected selected-to-active setting")
    require(bake.use_clear == bake_profile["use_clear"], "unexpected bake clear setting")
    require(bake.use_cage == bake_profile["use_cage"], "unexpected cage setting")
    require(bake.margin == bake_profile["margin"], "unexpected bake margin")
    require(float_matches(bake.cage_extrusion, bake_profile["cage_extrusion"]), "unexpected cage extrusion")
    require(float_matches(bake.max_ray_distance, bake_profile["max_ray_distance"]), "unexpected maximum ray distance")

    return blend_path, source, target, source_material, source_node, target_material, target_node


def image_stats(image) -> dict:
    pixels = list(image.pixels)
    require(len(pixels) == image.size[0] * image.size[1] * 4, "unexpected image pixel buffer length")
    alpha_nonzero = 0
    alpha_opaque = 0
    alpha_partial = 0
    rgb_nonzero_in_alpha = 0
    alpha_min = 1.0
    alpha_max = 0.0
    for index in range(0, len(pixels), 4):
        red, green, blue, alpha = pixels[index : index + 4]
        alpha_min = min(alpha_min, alpha)
        alpha_max = max(alpha_max, alpha)
        if alpha > 0.0:
            alpha_nonzero += 1
            if red > 0.0 or green > 0.0 or blue > 0.0:
                rgb_nonzero_in_alpha += 1
        if alpha >= 1.0:
            alpha_opaque += 1
        elif alpha > 0.0:
            alpha_partial += 1
    return {
        "width": image.size[0],
        "height": image.size[1],
        "channels": image.channels,
        "alpha_min": alpha_min,
        "alpha_max": alpha_max,
        "alpha_nonzero_pixels": alpha_nonzero,
        "alpha_opaque_pixels": alpha_opaque,
        "alpha_partial_pixels": alpha_partial,
        "rgb_nonzero_in_alpha_pixels": rgb_nonzero_in_alpha,
    }


def bake(args: argparse.Namespace) -> dict:
    input_path = args.input.resolve()
    output_path = args.output.resolve()
    profile_path = args.profile.resolve()
    require(input_path.is_file(), f"input PNG does not exist: {input_path}")
    require(profile_path.is_file(), f"profile does not exist: {profile_path}")
    require(input_path != output_path, "input and output paths must differ")
    profile = json.loads(profile_path.read_text(encoding="utf-8"))

    (
        blend_path,
        source,
        target,
        _source_material,
        source_node,
        target_material,
        target_node,
    ) = validate_template(profile)
    require(output_path != blend_path, "output path must not overwrite the template")

    source_image = bpy.data.images.load(str(input_path), check_existing=False)
    source_profile = profile["source"]
    require(list(source_image.size) == [source_profile["width"], source_profile["height"]], "input dimensions do not match the profile")
    require(source_image.channels == source_profile["channels"], "input must contain four channels")
    require(source_image.alpha_mode == source_profile["alpha_mode"], "input alpha mode does not match the profile")
    require(source_image.colorspace_settings.name == source_profile["colorspace"], "input color space does not match the profile")
    source_stats = image_stats(source_image)
    require(source_stats["alpha_nonzero_pixels"] > 0, "input contains no nontransparent pixels")
    source_node.image = source_image

    for node in target_material.node_tree.nodes:
        node.select = False
    target_node.select = True
    target_material.node_tree.nodes.active = target_node

    bpy.ops.object.select_all(action="DESELECT")
    source.select_set(True)
    target.select_set(True)
    bpy.context.view_layer.objects.active = target
    bpy.context.scene.cycles.device = "CPU"

    target_image = target_node.image
    bake_settings = bpy.context.scene.render.bake
    pass_filter = set()
    if bake_settings.use_pass_color:
        pass_filter.add("COLOR")
    if bake_settings.use_pass_direct:
        pass_filter.add("DIRECT")
    if bake_settings.use_pass_indirect:
        pass_filter.add("INDIRECT")
    cage_object = bake_settings.cage_object.name if bake_settings.cage_object else ""
    operator_result = bpy.ops.object.bake(
        type=bpy.context.scene.cycles.bake_type,
        pass_filter=pass_filter,
        width=target_image.size[0],
        height=target_image.size[1],
        margin=bake_settings.margin,
        margin_type=bake_settings.margin_type,
        use_selected_to_active=bake_settings.use_selected_to_active,
        max_ray_distance=bake_settings.max_ray_distance,
        cage_extrusion=bake_settings.cage_extrusion,
        cage_object=cage_object,
        normal_space=bake_settings.normal_space,
        normal_r=bake_settings.normal_r,
        normal_g=bake_settings.normal_g,
        normal_b=bake_settings.normal_b,
        target=bake_settings.target,
        save_mode=bake_settings.save_mode,
        use_clear=bake_settings.use_clear,
    )
    require("FINISHED" in operator_result, f"bake did not finish: {sorted(operator_result)}")
    target_stats = image_stats(target_image)
    require(target_stats["alpha_nonzero_pixels"] > 0, "baked image contains no nontransparent pixels")
    require(target_stats["alpha_partial_pixels"] > 0, "baked image contains no partial transparency")
    require(target_stats["rgb_nonzero_in_alpha_pixels"] > 0, "baked image has no RGB content inside its alpha mask")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    target_image.filepath_raw = str(output_path)
    target_image.file_format = "PNG"
    target_image.save()
    require(output_path.is_file(), f"Blender did not write the output PNG: {output_path}")
    require(output_path.stat().st_size > 0, f"Blender wrote an empty output file: {output_path}")

    return {
        "status": "ok",
        "input": str(input_path),
        "output": str(output_path),
        "template_sha256": sha256_path(blend_path),
        "blender_version": list(bpy.app.version),
        "device": bpy.context.scene.cycles.device,
        "bake_type": bpy.context.scene.cycles.bake_type,
        "pass_filter": sorted(pass_filter),
        "operator_result": sorted(operator_result),
        "source": source_stats,
        "target": target_stats,
    }


def main() -> None:
    try:
        result = bake(parse_args())
        print(RESULT_PREFIX + json.dumps(result, sort_keys=True))
    except Exception as exc:
        print(ERROR_PREFIX + json.dumps({"error": str(exc)}, sort_keys=True))
        traceback.print_exc()
        raise


if __name__ == "__main__":
    main()
