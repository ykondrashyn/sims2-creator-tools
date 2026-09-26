"""Independently compare exported GLB triangles and UVs with the original bodies.

Run using Blender 3.4.1 --background --python scripts/validate_preview_bodies.py.
"""
import hashlib
import json
from pathlib import Path
import struct

import bpy
from mathutils import Matrix, Quaternion, Vector, kdtree

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "package_creation/service/preview_assets"


def read_glb(path):
    raw = path.read_bytes()
    assert struct.unpack_from("<4sII", raw) == (b"glTF", 2, len(raw))
    length, kind = struct.unpack_from("<II", raw, 12)
    assert kind == 0x4E4F534A
    doc = json.loads(raw[20:20 + length])
    bin_length, kind = struct.unpack_from("<II", raw, 20 + length)
    assert kind == 0x004E4942
    binary = raw[28 + length:28 + length + bin_length]
    return doc, binary


def accessor(doc, binary, index):
    spec = doc["accessors"][index]
    view = doc["bufferViews"][spec["bufferView"]]
    count = {"SCALAR": 1, "VEC2": 2, "VEC3": 3}[spec["type"]]
    fmt = "<" + {5123: "H", 5125: "I", 5126: "f"}[spec["componentType"]] * count
    offset = view.get("byteOffset", 0) + spec.get("byteOffset", 0)
    stride = view.get("byteStride", struct.calcsize(fmt))
    return [struct.unpack_from(fmt, binary, offset + stride * i) for i in range(spec["count"])]


def main():
    manifest = json.loads((ASSETS / "manifest.json").read_text())
    for gender, meta in manifest["bodies"].items():
        template = ROOT / meta["template"]
        assert hashlib.sha256(template.read_bytes()).hexdigest() == meta["template_sha256"]
        path = ASSETS / f"{gender}.glb"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == meta["glb_sha256"]
        doc, binary = read_glb(path)
        assert not any(doc.get(key) for key in ("images", "textures", "skins", "animations", "materials"))
        assert len(doc["meshes"]) == len(doc["nodes"]) == 1
        assert all("uri" not in buffer for buffer in doc["buffers"])
        node = doc["nodes"][0]
        assert "matrix" not in node
        rotation = node.get("rotation", [0, 0, 0, 1])
        transform = Matrix.Translation(Vector(node.get("translation", [0, 0, 0]))) @ Quaternion(
            (rotation[3], *rotation[:3])
        ).to_matrix().to_4x4() @ Matrix.Diagonal((*node.get("scale", [1, 1, 1]), 1))
        primitive, = doc["meshes"][0]["primitives"]
        assert primitive.get("mode", 4) == 4
        assert "targets" not in primitive
        points = [transform @ Vector(point) for point in accessor(doc, binary, primitive["attributes"]["POSITION"])]
        uvs = accessor(doc, binary, primitive["attributes"]["TEXCOORD_0"])
        indices = [value[0] for value in accessor(doc, binary, primitive["indices"])]
        assert len(indices) == meta["triangles"] * 3
        assert len(points) == len(uvs)
        assert max(indices) < len(points)
        bpy.ops.wm.open_mainfile(filepath=str(template))
        body = bpy.data.objects["ts2 body"]
        body.data.calc_loop_triangles()
        tree = kdtree.KDTree(len(body.data.vertices))
        for vertex in body.data.vertices:
            x, y, z = body.matrix_world @ vertex.co
            tree.insert((x, z, -y), vertex.index)
        tree.balance()
        original = {}
        for triangle in body.data.loop_triangles:
            key = tuple(sorted(triangle.vertices))
            original.setdefault(key, []).append({
                body.data.loops[index].vertex_index: tuple(body.data.uv_layers.active.data[index].uv)
                for index in triangle.loops
            })
        for start in range(0, len(indices), 3):
            corners = indices[start:start + 3]
            # UV seams create coincident vertices, so test every matching source triangle.
            candidates = []
            for index in corners:
                near = tree.find_range(points[index], 0.00001)
                assert near, "Exported vertex moved away from the original body"
                candidates.append([item[1] for item in near])
            matched = False
            for a in candidates[0]:
                for b in candidates[1]:
                    for c in candidates[2]:
                        for triangle_uvs in original.get(tuple(sorted((a, b, c))), []):
                            if all(abs(uvs[corner][0] - triangle_uvs[vertex][0]) < 0.00001 and
                                   abs(uvs[corner][1] - (1 - triangle_uvs[vertex][1])) < 0.00001
                                   for corner, vertex in zip(corners, (a, b, c))):
                                matched = True
            assert matched, "A triangle or texture coordinate changed during export"
        print(f"{gender.upper()}: {meta['triangles']} triangles match source positions and UVs, template hash unchanged")


if __name__ == "__main__":
    main()
