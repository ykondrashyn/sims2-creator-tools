# Validation

Run `npm run typecheck`, `npm run lint`, and `npm test` for frontend contracts,
persistence, cancellation, local inference backends and deterministic builds.
`python3 -m tools.static_release verify` checks the prepared publication against
its exact source and file inventory. Neither command needs private game assets.

Full local validation uses fresh native and WASM artifacts. Native examples and
`package_creation/tests/*parity.mjs` cover codecs, package structure and pixels.
Set explicit artifact paths, never rely on a historical runtime implicitly.
Reference fixtures have hashes and preparation instructions in `tests/fixtures`.

`python -m tools.static_browser_check --site site --output artifacts/static-check`
serves plain static files, exercises all creator tools in disposable Chromium and
Firefox profiles, records downloads and network reads, and tests the GitHub prefix.
Local inference tests use the small and seam fixtures under `tools/local-upscale`.
The WebGPU test records hardware availability separately from CPU correctness.

Structural and browser results are not gameplay acceptance. Keep the Sim export
gates and other feature limitations visible. Record in-app browser checks separately.
