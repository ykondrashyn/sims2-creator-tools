# Static architecture

The shell opens Home without loading a processing engine, inference model or 3D
reference. A typed destination registry drives grouped navigation, Home cards and
hash routes. Ordinary links use the same activation path as browser history.
Successful initialization is retained and concurrent requests join one promise.
Failed initialization can retry. Navigation hides mounted panels and emits their
visibility state without cancelling workers or replacing controller state.

Below 1200 pixels, the same navigation moves into a native modal dialog. The
desktop sidebar is 220 pixels wide. Neither navigation mode changes saved-job
serialization or worker contracts.

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
