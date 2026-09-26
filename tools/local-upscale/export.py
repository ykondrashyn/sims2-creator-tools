"""Export the original FP32 RealESRGAN_x4plus weights, without quantization.

Development only. No Python, PyTorch or network inference is used by the site.
"""

import hashlib
import json
from pathlib import Path
import types
import urllib.request

import numpy as np
import onnx
import onnxruntime as ort
import torch
from torch import nn

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
CACHE = ROOT / ".cache/local-upscale"
WEIGHTS_URL = (
    "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth"
)
WEIGHTS_SHA = "4fa0d38905f75ac06eb49a7951b426670021be3018265fd191d2125df9d682f1"
ARCH_SHA = "6aa8eb3f77e918b90eeff4ebfaf8745abe0c40eb8fcc03abbc1928693d70ab58"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def model():
    CACHE.mkdir(parents=True, exist_ok=True)
    weights = CACHE / "RealESRGAN_x4plus.pth"
    if not weights.exists():
        temporary = weights.with_suffix(".download")
        urllib.request.urlretrieve(WEIGHTS_URL, temporary)
        if digest(temporary) != WEIGHTS_SHA:
            raise ValueError("Real-ESRGAN weights failed integrity verification")
        temporary.replace(weights)
    assert digest(weights) == WEIGHTS_SHA
    source = HERE / "vendor/rrdbnet_arch.py"
    assert digest(source) == ARCH_SHA
    # Keep the upstream architecture verbatim. Adapt only training/registry
    # imports in memory. Initialization is irrelevant after strict weight loading.
    code = source.read_text().replace("from basicsr.utils.registry import ARCH_REGISTRY", "")
    code = code.replace(
        "from .arch_util import default_init_weights, make_layer, pixel_unshuffle", ""
    )
    namespace = {
        "ARCH_REGISTRY": types.SimpleNamespace(register=lambda: lambda value: value),
        "default_init_weights": lambda *_args: None,
        "make_layer": lambda block, count, **kwargs: nn.Sequential(
            *(block(**kwargs) for _ in range(count))
        ),
    }
    exec(compile(code, "upstream_rrdbnet_arch.py", "exec"), namespace)
    net = namespace["RRDBNet"](3, 3, scale=4, num_feat=64, num_block=23, num_grow_ch=32)
    net.load_state_dict(
        torch.load(weights, map_location="cpu", weights_only=True)["params_ema"], strict=True
    )
    return net.eval()


def main():
    torch.set_num_threads(1)
    torch.manual_seed(0)
    net = model()
    output = CACHE / "RealESRGAN_x4plus.onnx"
    with torch.inference_mode():
        torch.onnx.export(
            net,
            torch.zeros(1, 3, 16, 16),
            output,
            input_names=["image"],
            output_names=["upscaled"],
            dynamic_axes={
                "image": {2: "height", 3: "width"},
                "upscaled": {2: "out_height", 3: "out_width"},
            },
            opset_version=17,
            do_constant_folding=True,
        )
    onnx.checker.check_model(str(output))
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    session = ort.InferenceSession(str(output), options, providers=["CPUExecutionProvider"])
    reports = []
    for shape in [(1, 3, 1, 1), (1, 3, 17, 23), (1, 3, 64, 64)]:
        image = np.random.default_rng(42).random(shape, dtype=np.float32)
        with torch.inference_mode():
            expected = net(torch.from_numpy(image)).numpy()
        actual = session.run(None, {"image": image})[0]
        error = float(np.max(np.abs(actual - expected)))
        assert error < 0.0001, error
        reports.append({"shape": shape, "maximum_float_error": error})
    report = {
        "model": "RealESRGAN_x4plus",
        "weights_sha256": WEIGHTS_SHA,
        "onnx_sha256": digest(output),
        "size": output.stat().st_size,
        "parity": reports,
    }
    (CACHE / "export-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
