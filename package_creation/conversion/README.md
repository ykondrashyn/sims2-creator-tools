# Browser body conversion

The browser converts one AM or AF 1024×2048 RGBA PNG into a 1024×1024 TS2 PNG. Inputs stay on the device. The runtime shares the existing worker, IndexedDB storage budget and processing lease. Completed conversions are immutable and remain available until deleted or browser storage is cleared or evicted. Site storage belongs to the exact address used to open the tools.

The native comparison wrapper is `builder/examples/conversion.rs`. It uses the same Rust implementation as WASM. Conversion is separate from tattoo package generation. PNG samples are decoded directly without Canvas. The original sRGB image-node interpretation, integer alpha association, 128 PMJ samples, Cycles channel arithmetic, byte quantization and four-pixel adjacent-face padding are preserved. PNG compression and metadata are not required to match.

## Offline asset generation

1. Use Blender 3.4.1, build `55485cb379f7`, with the original AM and AF blend files. Run `scripts/export_conversion_maps.py` inside Blender for each body. Use `--quantization` on AM to export the shared output-byte thresholds.
2. Run `scripts/build_conversion_assets.py --maps <export-directory>`. This compiles the two offline C++ helpers, preserves lossless buffers and creates deterministic profile ZIPs. The ZIPs are browser assets, not user downloads.
3. Run `scripts/build_package_runtime.py` to publish content-addressed engine and mapping assets. The Blender templates are never modified. Metadata pins template hashes, export sources, bake settings, evaluated topology, UVs and color-management configuration.

The production service does not discover or run Blender. `convert.py`, `server.py` and `scripts/bake_texture.py` remain offline reference tools. Retired POST `/api/v1/convert` returns 410 before reading its body.

## Protocol and recovery

Operations are `conversion_validate`, `conversion_begin`, `conversion_step` and `conversion_finish`. Each processing step handles at most 8192 output pixels. Worker messages carry protocol version, job, revision, request ID and attempt token. Cancellation terminates the worker. Resume restarts from the pinned immutable snapshot and retains the same source file and engine. Results are published only by the current lease owner.

A normal reload records the stopped attempt token in session storage so the new page can finish releasing its IndexedDB lease. This token cannot stop a newer attempt. An abrupt browser crash without a page-close event relies on the existing two-minute lease expiry before resuming.

Limits: one 32 MiB input, 8 MiB output, 2 GiB application storage, 1 GiB WASM memory and ten minutes of active processing. Only the chosen body's mapping is loaded. No hair palettes or package templates are needed.

Exact parity against the pinned Blender executable is a release gate. Structural/browser tests are separate from gameplay testing. See the generated acceptance report for measured results.
