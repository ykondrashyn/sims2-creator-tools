"""Inspect the currently loaded Blender template and write deterministic JSON."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import bpy


def blender_arguments() -> list[str]:
    if "--" not in sys.argv:
        return []
    return sys.argv[sys.argv.index("--") + 1 :]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect the loaded Tattooer template")
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(blender_arguments())


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def simple(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, set):
        return sorted(simple(item) for item in value)
    if isinstance(value, (list, tuple)):
        return [simple(item) for item in value]
    if hasattr(value, "name"):
        return value.name
    try:
        return [simple(item) for item in value]
    except (TypeError, ValueError):
        return str(value)


def rna_values(value) -> dict:
    result = {}
    for prop in value.bl_rna.properties:
        key = prop.identifier
        if key == "rna_type":
            continue
        try:
            result[key] = simple(getattr(value, key))
        except (AttributeError, TypeError, ValueError) as exc:
            result[key] = f"unavailable: {exc}"
    return result


def colorspace_name(image) -> str | None:
    try:
        return image.colorspace_settings.name
    except (TypeError, ValueError):
        return None


def image_info(image) -> dict | None:
    if image is None:
        return None
    return {
        "name": image.name,
        "source": image.source,
        "size": list(image.size),
        "channels": image.channels,
        "depth": image.depth,
        "alpha_mode": image.alpha_mode,
        "colorspace": colorspace_name(image),
        "file_format": image.file_format,
        "filepath": image.filepath,
        "packed": image.packed_file is not None,
        "generated_type": image.generated_type,
        "generated_color": list(image.generated_color),
        "is_float": image.is_float,
    }


def node_info(node) -> dict:
    result = {
        "name": node.name,
        "label": node.label,
        "type": node.type,
        "bl_idname": node.bl_idname,
        "selected": node.select,
    }
    if node.type == "TEX_IMAGE":
        result.update(
            {
                "image": image_info(node.image),
                "interpolation": node.interpolation,
                "projection": node.projection,
                "projection_blend": node.projection_blend,
                "extension": node.extension,
            }
        )
    return result


def material_info(material) -> dict:
    result = {
        "name": material.name,
        "use_nodes": material.use_nodes,
        "blend_method": material.blend_method,
    }
    if not material.use_nodes:
        return result
    tree = material.node_tree
    result["active_node"] = tree.nodes.active.name if tree.nodes.active else None
    result["nodes"] = [node_info(node) for node in sorted(tree.nodes, key=lambda n: n.name)]
    result["links"] = [
        {
            "from_node": link.from_node.name,
            "from_socket": link.from_socket.name,
            "to_node": link.to_node.name,
            "to_socket": link.to_socket.name,
        }
        for link in sorted(
            tree.links,
            key=lambda item: (
                item.from_node.name,
                item.from_socket.name,
                item.to_node.name,
                item.to_socket.name,
            ),
        )
    ]
    return result


def object_info(obj) -> dict:
    result = {
        "name": obj.name,
        "type": obj.type,
        "selected": obj.select_get(),
        "hide_render": obj.hide_render,
        "hide_viewport": obj.hide_viewport,
        "data_name": obj.data.name if obj.data else None,
        "material_slots": [slot.material.name if slot.material else None for slot in obj.material_slots],
        "modifiers": [
            {"name": modifier.name, "type": modifier.type, "show_render": modifier.show_render}
            for modifier in obj.modifiers
        ],
    }
    if obj.type == "MESH":
        result.update(
            {
                "vertices": len(obj.data.vertices),
                "polygons": len(obj.data.polygons),
                "uv_layers": [
                    {
                        "name": uv.name,
                        "active": obj.data.uv_layers.active == uv,
                        "active_render": uv.active_render,
                    }
                    for uv in obj.data.uv_layers
                ],
            }
        )
    return result


def inspect() -> dict:
    scene = bpy.context.scene
    blend_path = Path(bpy.data.filepath)
    return {
        "blender_version": list(bpy.app.version),
        "blend_file": blend_path.name,
        "blend_sha256": sha256_path(blend_path),
        "scene": scene.name,
        "active_object": (
            bpy.context.view_layer.objects.active.name
            if bpy.context.view_layer.objects.active
            else None
        ),
        "selected_objects": sorted(obj.name for obj in bpy.context.selected_objects),
        "objects": [object_info(obj) for obj in sorted(bpy.data.objects, key=lambda item: item.name)],
        "materials": [
            material_info(material)
            for material in sorted(bpy.data.materials, key=lambda item: item.name)
        ],
        "images": [image_info(image) for image in sorted(bpy.data.images, key=lambda item: item.name)],
        "render_engine": scene.render.engine,
        "render_bake": rna_values(scene.render.bake),
        "render_image_settings": rna_values(scene.render.image_settings),
        "film_transparent": scene.render.film_transparent,
        "cycles": rna_values(scene.cycles),
        "view_settings": rna_values(scene.view_settings),
        "display_settings": rna_values(scene.display_settings),
        "sequencer_colorspace": scene.sequencer_colorspace_settings.name,
    }


def main() -> None:
    args = parse_args()
    result = inspect()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        "TATTOOER_INSPECTION_JSON="
        + json.dumps(
            {
                "output": str(args.output),
                "template": result["blend_file"],
                "template_sha256": result["blend_sha256"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
