"""Pinned FP32 model exports and native pixel oracles. Development only."""

import argparse
import copy
import hashlib
import json
from pathlib import Path
import types
import urllib.request

import numpy as np
import onnx
import onnxruntime as ort
from PIL import Image
import torch
from torch import nn

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CACHE = ROOT / ".cache/local-upscale"
CATALOG = HERE / "models.json"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def network(profile):
    if profile["id"] == "full":
        from export import model

        return model()
    CACHE.mkdir(parents=True, exist_ok=True)
    weights = CACHE / (profile["model"] + ".pth")
    if not weights.exists():
        temporary = weights.with_suffix(".download")
        urllib.request.urlretrieve(profile["weights_url"], temporary)
        if digest(temporary) != profile["weights_sha256"]:
            raise ValueError("Downloaded checkpoint failed integrity verification")
        temporary.replace(weights)
    if digest(weights) != profile["weights_sha256"]:
        raise ValueError("Checkpoint failed integrity verification")
    source = HERE / "vendor" / profile["architecture"]
    if digest(source) != profile["architecture_sha256"]:
        raise ValueError("Architecture failed integrity verification")
    code = source.read_text().replace("from basicsr.utils.registry import ARCH_REGISTRY", "")
    namespace = {"ARCH_REGISTRY": types.SimpleNamespace(register=lambda: lambda value: value)}
    exec(compile(code, str(source), "exec"), namespace)
    if profile["id"] == "nomos":
        net = namespace["SPAN"](3, 3, feature_channels=48, upscale=2)
    else:
        net = namespace["SRVGGNetCompact"](
            3,
            3,
            num_feat=64,
            num_conv=32 if profile["id"] == "compact" else 16,
            upscale=4,
            act_type="prelu",
        )
    state = torch.load(weights, map_location="cpu", weights_only=True)
    net.load_state_dict(state.get("params_ema", state.get("params", state)), strict=True)
    return net.eval()


def freeze_span(net):
    """Freeze the upstream evaluation path after strict checkpoint loading."""
    result = copy.deepcopy(net)

    def visit(module):
        for name, child in list(module.named_children()):
            if child.__class__.__name__ == "Conv3XC":
                child.update_params()
                replacement = copy.deepcopy(child.eval_conv)
                if child.has_relu:
                    replacement = nn.Sequential(replacement, nn.LeakyReLU(0.05))
                setattr(module, name, replacement)
            else:
                visit(child)

    visit(result)
    return result.eval()


def lower_prelu(path):
    """ORT WebGPU 1.22 lacks PRelu. Preserve its elementwise FP32 mapping."""
    graph = onnx.load(path)
    nodes = []
    zero = "webgpu_prelu_zero"
    if any(node.op_type == "PRelu" for node in graph.graph.node):
        graph.graph.initializer.append(
            onnx.numpy_helper.from_array(np.array(0, dtype=np.float32), zero)
        )
    for node in graph.graph.node:
        if node.op_type != "PRelu":
            nodes.append(node)
            continue
        negative, scaled = node.output[0] + "_negative", node.output[0] + "_scaled"
        nodes.extend(
            [
                onnx.helper.make_node("Less", [node.input[0], zero], [negative]),
                onnx.helper.make_node("Mul", list(node.input), [scaled]),
                onnx.helper.make_node(
                    "Where", [negative, scaled, node.input[0]], list(node.output)
                ),
            ]
        )
    del graph.graph.node[:]
    graph.graph.node.extend(nodes)
    onnx.save(graph, path)


def verify_tile_budget(path, profile):
    graph = onnx.load(path)
    side = profile["tile"] + 2 * profile["overlap"]
    for axis in (2, 3):
        graph.graph.input[0].type.tensor_type.shape.dim[axis].dim_value = side
    graph = onnx.shape_inference.infer_shapes(graph, strict_mode=True, data_prop=True)
    largest = 0
    for value in [*graph.graph.input, *graph.graph.value_info, *graph.graph.output]:
        tensor = value.type.tensor_type
        dims = [d.dim_value for d in tensor.shape.dim]
        if not all(d > 0 for d in dims):
            raise ValueError("Cannot bound exported tensor: " + value.name)
        item_bytes = np.dtype(onnx.helper.tensor_dtype_to_np_dtype(tensor.elem_type)).itemsize
        largest = max(largest, int(np.prod(dims)) * item_bytes)
    if largest > profile["gpu_buffer_bytes"]:
        raise ValueError("Model exceeds its GPU buffer requirement: " + profile["id"])
    return dict(
        tile_input_side=side,
        largest_logical_tensor_bytes=largest,
        required_gpu_buffer_bytes=profile["gpu_buffer_bytes"],
        note="Logical tensor bound, not total or peak memory",
    )


def cases():
    for name, width, height in [
        ("small", 32, 24),
        ("seams", 133, 37),
        ("flat", 19, 17),
        ("gradient", 33, 19),
        ("edges", 31, 29),
        ("random", 41, 27),
        ("tiny", 1, 1),
        ("slim", 1, 7),
    ]:
        y, x = np.mgrid[:height, :width]
        rgb = np.stack(
            [
                (x * 7 + y * 3) % 256,
                (x * 11 + y * 13) % 256,
                np.where((x // 9 + y // 7) % 2, 220, 30),
            ],
            axis=-1,
        ).astype(np.uint8)
        if name == "flat":
            rgb[:] = [77, 99, 121]
        if name == "gradient":
            rgb[:] = x[..., None] * 255 // max(1, width - 1)
        if name == "edges":
            rgb[:] = np.where(x[..., None] < width // 2, 0, 255)
        if name == "random":
            rgb = np.random.default_rng(42).integers(0, 256, rgb.shape, dtype=np.uint8)
        yield name, rgb, (x * 255 // max(1, width - 1)).astype(np.uint8)


def tiled(net, rgb, profile):
    height, width = rgb.shape[:2]
    scale, pad, tile, overlap = (profile[k] for k in ("scale", "prepad", "tile", "overlap"))
    padded = np.pad(rgb.astype(np.float32) / 255, ((0, pad), (0, pad), (0, 0)), mode="reflect")
    source = torch.from_numpy(padded.transpose(2, 0, 1)[None].copy())
    full = net(source).numpy()[0, :, : height * scale, : width * scale]
    target = np.zeros_like(full)
    for top in range(0, height, tile):
        for left in range(0, width, tile):
            x0, y0 = max(0, left - overlap), max(0, top - overlap)
            x1, y1 = (
                min(width + pad, left + tile + overlap),
                min(height + pad, top + tile + overlap),
            )
            output = net(source[:, :, y0:y1, x0:x1]).numpy()[0]
            w, h = min(tile, width - left) * scale, min(tile, height - top) * scale
            ox, oy = (left - x0) * scale, (top - y0) * scale
            target[:, top * scale : top * scale + h, left * scale : left * scale + w] = output[
                :, oy : oy + h, ox : ox + w
            ]

    def pack(a):
        return np.rint(np.clip(a, 0, 1) * 255).astype(np.uint8).transpose(1, 2, 0)

    actual, expected = pack(target), pack(full)
    error = int(np.abs(actual.astype(np.int16) - expected.astype(np.int16)).max())
    if error > 1:
        raise ValueError(f"Tile boundary parity failed for {profile['id']}: {error}")
    return actual, error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update-pins", action="store_true")
    parser.add_argument(
        "--model", choices=["compact", "animevideo", "nomos", "full", "all"], default="all"
    )
    args = parser.parse_args()
    torch.set_num_threads(1)
    torch.manual_seed(0)
    profiles = json.loads(CATALOG.read_text())
    for p in profiles:
        if args.model not in ("all", p["id"]):
            continue
        native = network(p)
        exported = freeze_span(native) if p["id"] == "nomos" else native
        path = ROOT / p["path"]
        if p["id"] != "full":
            with torch.inference_mode():
                torch.onnx.export(
                    exported,
                    torch.zeros(1, 3, 16, 16),
                    path,
                    input_names=["image"],
                    output_names=["upscaled"],
                    dynamic_axes={
                        "image": {2: "height", 3: "width"},
                        "upscaled": {2: "out_height", 3: "out_width"},
                    },
                    opset_version=17,
                    do_constant_folding=True,
                )
            lower_prelu(path)
        onnx.checker.check_model(str(path))
        if args.update_pins and p["id"] != "full":
            p.update(sha256=digest(path), size=path.stat().st_size)
        if digest(path) != p["sha256"] or path.stat().st_size != p["size"]:
            raise ValueError("Export differs from pinned model: " + p["id"])
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        session = ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])
        report = []
        folder = HERE / "fixtures" / p["id"]
        folder.mkdir(exist_ok=True)
        (folder / "graph.json").write_text(json.dumps(verify_tile_budget(path, p), indent=2) + "\n")
        with torch.inference_mode():
            for name, rgb, alpha in cases():
                x = rgb.transpose(2, 0, 1)[None].astype(np.float32) / 255
                expected = native(torch.from_numpy(x)).numpy()
                fused = exported(torch.from_numpy(x)).numpy()
                actual = session.run(None, {"image": x})[0]
                error = float(np.max(np.abs(actual - expected)))
                fusion_error = float(np.max(np.abs(fused - expected)))
                if error >= 0.0001 or fusion_error > 0.000001:
                    raise ValueError(
                        f"Export parity failed for {p['id']}/{name}: {error}, {fusion_error}"
                    )
                reference, seam_error = tiled(native, rgb, p)
                Image.fromarray(np.dstack([rgb, alpha]), "RGBA").save(folder / f"{name}.png")
                Image.fromarray(reference, "RGB").save(folder / f"{name}-reference.png")
                report.append(
                    dict(
                        case=name,
                        max_float_error=error,
                        fusion_error=fusion_error,
                        tiled_vs_full_maximum_byte_error=seam_error,
                    )
                )
        (folder / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(
            json.dumps(dict(id=p["id"], sha256=p["sha256"], size=p["size"], checks=report)),
            flush=True,
        )
    if args.update_pins:
        CATALOG.write_text(json.dumps(profiles, indent=2) + "\n")


if __name__ == "__main__":
    main()
