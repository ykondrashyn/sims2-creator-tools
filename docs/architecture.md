# Static architecture

Seven lazy-loaded TypeScript controllers share a Rust/WASM package and image
engine. Module workers handle processing and hashing. The local upscaler uses a
separate ONNX Runtime worker with CPU and WebGPU backends and the same pinned FP32
RealESRGAN_x4plus model. No request to a runtime API or inference provider is made.

`web/src/deployment.ts` resolves the versioned manifest relative to the entry page.
The manifest selects content-addressed engines, templates, palettes, projection
maps, previews, model weights and notices. Every loaded asset is integrity checked.
A bounded LRU cache reuses immutable Blobs. Plain static hosting works at the root
and the GitHub project prefix. Optional documentation links are external.

Six creator tools save jobs in the existing version-1 IndexedDB database. Immutable
snapshots, pinned engines, checkpoints, leases and attempt tokens protect retries
and downloads. Static releases preserve record serialization. Origin isolation
means LAN records are not shared with GitHub Pages. Upscaling is session-only.

`tools/static_release.py` builds and validates the entire site. Source and file
hashes bind the frontend to the runtime. Pages only publishes verified artifacts.
Corresponding engine source and dependencies are included in a downloadable archive.
