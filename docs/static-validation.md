# Static edition validation

Validation was performed on 26 September 2026 with freshly compiled native and
WASM artifacts. The seven-tool site was served by Python's static HTTP server,
without FastAPI, at both the root and `/sims2-creator-tools/`.

## Structural and pixel checks

- TypeScript, ESLint, formatting and all 81 frontend tests passed.
- 76 native/WASM hair and tattoo packages matched byte for byte. Coverage includes
  all 43 colors, the Rose bundle with five textures and eighteen materials, custom
  curves, separate Elder routing, AM/AF tattoos and the 20-entry limit.
- Imported objects and curated object templates matched native/WASM output.
- All four painting frames matched native/WASM bytes for fill and contain crops.
- The supported experimental AM Everyday Sim output matched native/WASM bytes.
- Upscale image preparation and output processing matched native/WASM bytes across
  15 image-format combinations, including transparency, orientation and profiles.

## Browser checks

Chromium and Firefox were tested in disposable profiles. All six creator tools
produced their expected downloads and previews. AM and AF conversion pixels matched
the pinned Blender references. Tests covered saved download recovery, cancellation,
retry, cross-tab locking, narrow layouts and keyboard tab navigation. Network traces
contained only same-origin static reads, with no API calls or creation uploads.

The full RealESRGAN_x4plus CPU backend completed small and seam fixtures, with
maximum RGB byte error of one against the native reference. Original alpha and PNG
downloads were checked. The explicit WebGPU backend passed the same checks in
Chromium on the Apple Metal adapter with fallback reported false. Firefox's current
configuration reported WebGPU unavailable and correctly disabled its start action.
The in-app browser separately completed CPU and WebGPU runs and PNG downloads.
Its download event notification timed out, but the resulting files were found and
validated on disk. This is distinct from a failed download.

These measurements are correctness checks, not a controlled performance benchmark.
No new gameplay testing was performed. Full Sim exports remain gated. Only the
previously supported AM Everyday body experiment is available.
