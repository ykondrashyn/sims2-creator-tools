# Static edition validation

## Navigation and welcome page checks

Run `tools.static_browser_check --suite synthetic` at both root and project
paths to check Home, grouped navigation, direct links, browser history, focus,
responsive drawer behavior and retained drafts. Home must fetch no processing
engine, model weights or reference assets. Unit checks exercise joined imports,
retry after failure and rejection of stale navigation results.

The creator suite checks real downloads and saved-batch recovery. Its body
conversion check now visits Home during processing. CPU and GPU suites also
leave the upscaler during a run, return to its result and verify the retained
download. Browser captures and machine-readable reports are written to the
selected artifact output directory. These checks do not establish gameplay
compatibility.

The dated results below describe earlier releases.

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


## Four-model update, 6 October 2026

TypeScript, ESLint, formatting and all 97 frontend tests passed.

The new Compact, AnimeVideo v3 and Nomos SPAN exports passed eight deterministic
cases each: random pixels, flat color, gradients, sharp edges, tile seams, small,
odd and one-pixel dimensions. Repeated exports reproduced their pinned ONNX hashes.
SPAN's frozen evaluation convolutions matched the original network exactly.
Compact and AnimeVideo use an equivalent PRelu lowering because the pinned WebGPU
runtime lacks that operator. All native ONNX float errors remained below 0.0001.

All eight model/backend selections were exercised. Chromium hardware WebGPU used
the Apple Metal adapter with fallback false and CPU fallback disabled. Chromium
and Firefox CPU results and hardware GPU results stayed within one RGB byte of
the model-specific native PNG references. The three new models also passed 24
CPU/GPU comparisons with identical alpha channels. The original Full model's hash
and processing policy are unchanged. Firefox in this configuration has no WebGPU
adapter, and all four GPU choices correctly remain unavailable without fetching
inference assets.

Tests covered actual downloads, original-input cross-model repeat runs, immutable
captions, selection before image upload, duplicate submissions, cancellation,
Retry, deterministic PNG bytes, session clearing and narrow layouts. Worker tests
cover device loss, initialization failure and no fallback. Image preparation and
PNG/alpha restoration matched native/WASM bytes across 30 combinations at 2x and
4x. No image or result was saved to browser storage or uploaded. Captured network
traffic consisted only of same-origin static reads.

The in-app browser separately completed Compact CPU and Nomos WebGPU, including
regular Download PNG clicks and filesystem validation of their 128x96 and 64x48
RGBA files. Both matched native RGB within one byte. Its automated download-event
helper still timed out, but normal link clicks saved valid files. Model switching
kept the previous result caption and download until replacement succeeded.

Both Chromium and Firefox exercised all six existing creators against the new
static runtime, including exact AM/AF conversion pixels, mixed tattoos, object
size and RefPack controls, paintings, Rose's five-texture bundle, completed saved
batch recovery and the existing restricted Sim experiment. No new gameplay tests
were performed and export restrictions remain unchanged.

### Timing and memory observations

Illustrative Chromium measurements on the same host, using a 32x24 input.
Times exclude model fetch and hash verification. These are first-session samples,
not controlled cold-cache or large-image benchmarks. OS and GPU driver caches
were not purged.

| Model | CPU session init | CPU first tile | GPU session init | GPU first tile |
| --- | --- | --- | --- | --- |
| Compact | 0.09 s | 0.08 s | 0.25 s | 0.04 s |
| AnimeVideo v3 | 0.09 s | 0.04 s | 0.23 s | 0.03 s |
| Nomos SPAN | 0.08 s | 0.03 s | 0.21 s | 0.02 s |
| Full | 0.27 s | 1.10 s | 0.53 s | 0.10 s |

On the 133x37 two-tile fixture, the second tile took CPU/GPU 0.18/0.01 s for
Compact, 0.10/0.01 s for AnimeVideo, 0.06/0.01 s for Nomos, and 2.77/0.15 s for
Full. Tiles differ in size, so this is not a direct cold-versus-warm speed ratio.
The measurements do not establish a universal GPU speedup.

ONNX sizes are about 4.9, 2.5, 1.7 and 67.1 MB respectively. Verified per-buffer
requirements are documented in `tools/local-upscale/README.md`. The model assets
remain within the existing cache policy and inference is tiled. Browser-total and
GPU peak memory were not instrumented. Buffer limits are not peak-memory readings.

Numerical parity is distinct from subjective image quality. Diagnostic previews
were checked for orientation and usable transparency. No ranking on photographs,
illustrations or game textures is claimed by these synthetic tests.

Reproduce with `tools/local-upscale/export_models.py`, `npm test`,
`tools.static_browser_check` (cpu, gpu, creators and synthetic suites), and
`package_creation/tests/upscale_wasm_parity.mjs` with explicit fresh artifact paths.
