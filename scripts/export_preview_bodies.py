"""Export only the TS2 target bodies for the web viewer, without saving the templates.

Run with Blender 3.4.1 --background --python scripts/export_preview_bodies.py.
The original world transforms orient both bodies upright, facing glTF +Z.
"""

import hashlib
import json
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "package_creation/service/preview_assets"
EXPECTED = {
    "am": "f709db0a1ae34b663805ce7c95da2f363dcb1d67029c197c4ef15098a4a5a108",
    "af": "e87f48a83350623abe97b1d8fe325153131305e40c43027df32a923e489e2958",
}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    if bpy.app.version[:3] != (3, 4, 1):
        raise RuntimeError("Export using the pinned Blender 3.4.1")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    manifest = {"blender": bpy.app.version_string, "bodies": {}}
    for gender, expected in EXPECTED.items():
        template = ROOT / "templates" / f"{gender.upper()}-body-4t2-1024.blend"
        if sha256(template) != expected:
            raise RuntimeError(f"Template hash changed: {template.name}")
        bpy.ops.wm.open_mainfile(filepath=str(template))
        body = bpy.data.objects["ts2 body"]
        bpy.ops.object.select_all(action="DESELECT")
        body.hide_set(False)
        body.select_set(True)
        bpy.context.view_layer.objects.active = body
        if body.data.shape_keys:
            for key in body.data.shape_keys.key_blocks:
                key.value = 0
        body.data.calc_loop_triangles()
        output = OUTPUT / f"{gender}.glb"
        bpy.ops.export_scene.gltf(
            filepath=str(output), export_format="GLB", use_selection=True,
            export_materials="NONE", export_texcoords=True, export_normals=True,
            export_yup=True, export_morph=False, export_skins=False,
            export_animations=False, export_cameras=False, export_lights=False,
            export_colors=False, export_extras=False,
        )
        assert sha256(template) == expected, "Source template was modified"
        manifest["bodies"][gender] = {
            "template": str(template.relative_to(ROOT)), "template_sha256": expected,
            "object": body.name, "uv_layer": body.data.uv_layers.active.name,
            "source_vertices": len(body.data.vertices),
            "triangles": len(body.data.loop_triangles),
            "glb_sha256": sha256(output), "bytes": output.stat().st_size,
        }
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
