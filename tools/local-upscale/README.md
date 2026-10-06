# Built-in local upscalers

The static site runs four pinned FP32 ONNX models in a dedicated worker using
ONNX Runtime Web 1.22.0. CPU uses single-threaded WASM with fixed SIMD. WebGPU is
explicit, hardware-only, and has CPU fallback disabled.

`models.json` is the shared build/frontend registry. It pins source checkpoint
hashes, architecture revisions and hashes, exported hashes and sizes, native scale,
model-specific GPU buffer requirements and provenance. `model.json` retains the
original full RealESRGAN_x4plus identity for historical compatibility.

The inputs and outputs of every graph are RGB NCHW FP32 in the 0–1 range. SPAN's
original mean/range normalization stays inside its graph. Its checkpoint has no
`no_norm` flag. The exporter uses the pinned official architecture, strictly loads
all checkpoint fields, then freezes its Conv3XC evaluation convolutions. It checks
fused/native equality before accepting the ONNX graph. Compact uses only the named
checkpoint, with no weak-denoise blending. ONNX Runtime WebGPU 1.22 lacks PRelu,
so the exporter lowers it to Less, Mul and Where with the same FP32 mapping.
Native comparisons cover this lowering. No quantization or retraining is used.

Models use 128-pixel tiles, 64-pixel overlap and ten-pixel reflected right/bottom
pre-padding. Scales are native 4× for Compact, AnimeVideo and Full, and 2× for Nomos.
Tiny dimensions use repeated reflection, constant for a one-pixel axis.
The full ONNX bytes and pixel processing are preserved unchanged.

## Reproduce exports and references

Use the pinned Python 3.11 environment and development dependencies:

```sh
python3.11 -m venv .cache/realesrgan-export
.cache/realesrgan-export/bin/python -m pip install -r tools/local-upscale/requirements.txt
.cache/realesrgan-export/bin/python tools/local-upscale/export.py
.cache/realesrgan-export/bin/python tools/local-upscale/export_models.py
```

The first exporter reproduces the historical full graph. The second verifies that
hash, exports the other three, and compares each model to native PyTorch on eight
synthetic cases. It creates per-model PNG oracles under `fixtures`. Normal runs
reject changed ONNX hashes. `--update-pins` is a development-only command for an
intentional, reviewed model update, followed by CPU and hardware WebGPU checks.
Release assembly requires the exact pinned graphs, searching the checkout cache
before the optional `PROJECT_FIXTURE_ROOT` cache.

## Inference and validation

Only verified immutable assets may be cached. Images and results stay in page
memory. The shared processing lease prevents overlapping local builds. Cancel
terminates the worker and releases the lease. Local runs have no duration limit.
The runtime's imported memory maximum remains capped at 1 GiB.

Rust prepares lossless oriented sRGB input and restores original alpha with
Lanczos3. PNG encoding is lossless. The model predicts RGB only.
Native export checks require maximum float error below 0.0001, frozen SPAN error
at most 0.000001, and tiled/full output byte error at most one. Browser checks use
the native PNG oracles with maximum RGB byte error one and verify actual downloads.
Use `python -m tools.static_browser_check --suite cpu` and `--suite gpu` with an
explicit output directory. GPU validation requires a real hardware adapter.

Per-model buffer requirements cover the largest logical FP32 activation at the
maximum 256×256 padded tile: 16 MiB for the 64-channel Compact/AnimeVideo feature
maps, 64 MiB conservatively for SPAN's 192-channel concatenation, and 256 MiB for
the full model's 64-channel 1024×1024 feature map. These are per-buffer requirements,
not total GPU memory or browser peak usage. ONNX shape inference verifies the
logical tensor bounds at 256x256 and records them in each fixture graph.json.
Browser profiling records initialization
and tile timings separately. Model download size is not a speed guarantee.

Credits and redistribution notices are in `NOTICES.txt` and `vendor`.
