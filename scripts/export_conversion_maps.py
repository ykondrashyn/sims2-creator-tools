"""Export pinned Blender 3.4.1 bake inputs without changing the templates.

Run inside Blender with --background TEMPLATE --python this.py -- --output DIR.
The offline exporter reads RenderPass buffers using the pinned RE_pipeline.h ABI.
Browser code never loads Blender, ctypes, or the original blend files.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import sys

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    "AM-body-4t2-1024.blend": "f709db0a1ae34b663805ce7c95da2f363dcb1d67029c197c4ef15098a4a5a108",
    "AF-body-4t2-1024.blend": "e87f48a83350623abe97b1d8fe325153131305e40c43027df32a923e489e2958",
}


class RenderPassPrefix(ctypes.Structure):
    # Blender v3.4.1 source/blender/render/RE_pipeline.h, RenderPass.
    _fields_ = [("next", ctypes.c_void_p), ("prev", ctypes.c_void_p),
                ("channels", ctypes.c_int), ("name", ctypes.c_char * 64),
                ("chan_id", ctypes.c_char * 8),
                ("rect", ctypes.POINTER(ctypes.c_float)),
                ("rectx", ctypes.c_int), ("recty", ctypes.c_int)]


def pass_array(render_pass):
    p = RenderPassPrefix.from_address(render_pass.as_pointer())
    assert p.name.decode() == render_pass.name
    assert p.channels == render_pass.channels and p.rectx > 0 and p.recty > 0
    return np.ctypeslib.as_array(p.rect, shape=(p.rectx * p.recty * p.channels,)).reshape(p.recty, p.rectx, p.channels)


def mesh_data(obj):
    obj = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    obj.data.calc_loop_triangles()
    return {
        "vertices": [list(v.co) for v in obj.data.vertices],
        "normals": [list(v.normal) for v in obj.data.vertices],
        "triangles": [list(t.vertices) for t in obj.data.loop_triangles],
        "triangle_loops": [list(t.loops) for t in obj.data.loop_triangles],
        "triangle_polygons": [t.polygon_index for t in obj.data.loop_triangles],
        "polygons": [list(p.loop_indices) for p in obj.data.polygons],
        "loop_vertices": [l.vertex_index for l in obj.data.loops],
        "loop_edges": [l.edge_index for l in obj.data.loops],
        "edges": [list(e.vertices) for e in obj.data.edges],
        "uv": [list(l.uv) for l in obj.data.uv_layers.active.data],
        "matrix_world": [list(row) for row in obj.matrix_world],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--result-buffer", type=Path)
    parser.add_argument("--image-output", type=Path)
    parser.add_argument("--quantization", action="store_true", help="Export exact Blender linear-float to PNG-byte thresholds")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    assert bpy.app.version == (3, 4, 1)
    template = Path(bpy.data.filepath)
    expected = EXPECTED[template.name]
    assert hashlib.sha256(template.read_bytes()).hexdigest() == expected
    args.output.mkdir(parents=True, exist_ok=True)
    source, target = (bpy.data.objects[n] for n in ("ts4 body", "ts2 body"))
    scene = bpy.context.scene
    b = scene.render.bake
    settings = {k: getattr(b, k) for k in ["margin", "margin_type", "use_cage", "cage_extrusion", "max_ray_distance", "use_pass_color", "use_pass_direct", "use_pass_indirect"]}
    metadata = {
        "schema_version": 1, "exporter_version": 1,
        "template": template.name, "template_sha256": expected,
        "blender_version": list(bpy.app.version), "blender_build": bpy.app.build_hash.decode(),
        "bake": settings, "source": mesh_data(source), "target": mesh_data(target),
        "cycles": {k: getattr(scene.cycles, k) for k in ["samples", "seed", "use_animated_seed", "sampling_pattern", "use_adaptive_sampling", "adaptive_threshold", "adaptive_min_samples", "scrambling_distance", "auto_scrambling_distance"]},
    }
    ocio = Path(bpy.utils.system_resource("DATAFILES")) / "colormanagement"
    metadata["color_management"] = {str(path.relative_to(ocio)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in [ocio / "config.ocio", ocio / "luts/srgb.spi1d", ocio / "luts/srgb_inv.spi1d"]}
    profile = ROOT / "profiles" / ("body_4t2.json" if template.name.startswith("AM") else "body_4t2_af.json")
    metadata["profile_sha256"] = hashlib.sha256(profile.read_bytes()).hexdigest()
    target_image = target.active_material.node_tree.nodes.active.image
    metadata["output_dimensions"] = list(target_image.size)
    (args.output / "mapping.json").write_text(json.dumps(metadata, separators=(",", ":")))
    captured = []
    capture_mode = "source"
    supplied = None

    class MappingExporter(bpy.types.RenderEngine):
        bl_idname = "TS2_MAPPING_EXPORT"
        bl_label = "TS2 offline mapping export"
        bl_use_shading_nodes = True

        def bake(self, depsgraph, obj, pass_type, pass_filter, width, height):
            try:
                result = self.begin_result(0, 0, width, height, layer="TS2Mapping", view="")
                for rp in result.layers[0].passes:
                    data = pass_array(rp)
                    if capture_mode and rp.name in ("BakePrimitive", "BakeDifferential"):
                        prefix = "Target" if capture_mode == "target" else ""
                        data.astype("<f4", copy=False).tofile(args.output / f"{prefix}{rp.name}.f32")
                    elif supplied is not None and rp.name == "Combined":
                        data[:] = supplied.reshape(data.shape)
                    elif args.result_buffer and rp.name == "Combined":
                        data[:] = np.fromfile(args.result_buffer, dtype="<f4").reshape(data.shape)
                captured.append(obj.name)
                self.end_result(result)
            except Exception as exc:
                captured.append(str(exc))
                raise

    bpy.utils.register_class(MappingExporter)
    scene.render.engine = MappingExporter.bl_idname
    bpy.ops.object.select_all(action="DESELECT")
    source.select_set(True)
    target.select_set(True)
    bpy.context.view_layer.objects.active = target
    bpy.ops.object.bake(type="DIFFUSE", pass_filter={"COLOR"},
                        use_selected_to_active=True, use_clear=True,
                        cage_extrusion=b.cage_extrusion, max_ray_distance=b.max_ray_distance,
                        margin=b.margin, margin_type=b.margin_type)
    assert captured == [source.name], captured
    if args.image_output:
        target_image.filepath_raw = str(args.image_output.resolve())
        target_image.file_format = "PNG"
        target_image.save()
        pixels = np.empty(target_image.size[0] * target_image.size[1] * 4, dtype=np.float32)
        target_image.pixels.foreach_get(pixels)
        pixels.tofile(args.output / "postprocess.f32")
    if not args.result_buffer:
        capture_mode = "target"
        source.select_set(False)
        bpy.ops.object.bake(type="DIFFUSE", pass_filter={"COLOR"}, use_selected_to_active=False, margin=0)
        source.select_set(True)
    if args.quantization:
        source_lut = (ocio / "luts/srgb.spi1d").read_text().split("{")[1].split("}")[0]
        values = np.array([float(v) for v in source_lut.split()], dtype="<f4")
        assert len(values) == 65561
        values.tofile(args.output / "srgb.bin")
        capture_mode = None
        primitive = np.fromfile(args.output / "BakePrimitive.f32", dtype="<f4").reshape(-1, 4)
        indices = np.flatnonzero(primitive[:, 1].copy().view(np.int32) >= 0)[:255]
        assert len(indices) == 255
        low = np.zeros(255, dtype=np.uint32)
        high = np.full(255, 0x3F800000, dtype=np.uint32)
        result_pixels = np.empty(1024 * 1024 * 4, dtype=np.float32)
        supplied = np.zeros((1024 * 1024, 4), dtype=np.float32)
        supplied[:, 3] = 1
        for iteration in range(31):
            middle = low + (high - low) // 2
            supplied[indices, :3] = middle.view(np.float32)[:, None]
            bpy.ops.object.bake(type="DIFFUSE", pass_filter={"COLOR"}, use_selected_to_active=True,
                                cage_extrusion=b.cage_extrusion, max_ray_distance=b.max_ray_distance, margin=0)
            target_image.pixels.foreach_get(result_pixels)
            actual = np.rint(result_pixels.reshape(-1, 4)[indices, 0] * 255).astype(int)
            below = actual < np.arange(1, 256)
            low = np.where(below, middle + 1, low).astype(np.uint32)
            high = np.where(below, high, middle).astype(np.uint32)
        assert np.all(low == high)
        high.astype("<u4").tofile(args.output / "quantization.u32")
        print("QUANTIZATION_EXPORTED", flush=True)
    assert hashlib.sha256(template.read_bytes()).hexdigest() == expected
    print("CONVERSION_MAPPING=" + json.dumps({"body": template.name[:2].lower(), "output": str(args.output), "template_unchanged": True}), flush=True)


if __name__ == "__main__":
    main()
