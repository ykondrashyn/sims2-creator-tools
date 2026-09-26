# Built-in Real-ESRGAN

The website runs the original full RealESRGAN_x4plus model locally in a dedicated
worker using ONNX Runtime Web 1.22.0, single-threaded WASM with fixed SIMD.
It works on the HTTP LAN site without a Replicate token, HTTPS or WebGPU.
Inference uses RGB FP32, 4× scaling, 128-pixel tiles, 64-pixel overlap and reflected
right/bottom pre-padding, following RealESRGANer. Tiny dimensions use repeated
reflection (constant for a one-pixel axis). This is not the lightweight x4v3 model.

Model weights are about 67 MB, loaded lazily from the LAN service with the runtime.
Only verified immutable assets may be cached. Inputs, results and settings remain
in page memory. The shared processing lease prevents overlapping local builds.
Cancel terminates the worker and releases the lease. No server inference exists.

## Reproduce the model

Use Python 3.11 in an isolated development environment:

```sh
python3.11 -m venv .cache/realesrgan-export
.cache/realesrgan-export/bin/python -m pip install -r tools/local-upscale/requirements.txt
.cache/realesrgan-export/bin/python tools/local-upscale/export.py
```

The script downloads checksum-pinned official weights, checks the vendored
architecture, exports a dynamic-shape ONNX graph with PyTorch 2.5.1 and ONNX 1.17,
and compares it against the original network. The release builder verifies the
exact generated hash in model.json. It never invokes Python for live inference.
No face enhancement, quantization or silent source resizing is used.

The network predicts RGB. Existing Rust image handling supplies oriented sRGB
input and restores original alpha with Lanczos3. PNG encoding is lossless.
Tile edges and browser outputs require separate validation. Tiled execution and
floating-point backends are not guaranteed to match Replicate byte for byte.
CPU inference on large textures can take many minutes. Local processing has no
elapsed-time limit. Cancel stops the worker. WebGPU is a separate FP32 testing
option with CPU fallback disabled, using the pinned runtime's JSEP assets.
The delivered runtime's imported memory maximum is capped at 1 GiB by a checked
build transformation. Input/output allocations are bounded before decoding.
