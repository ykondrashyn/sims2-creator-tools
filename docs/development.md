# Development

Use Node 22.18.0, npm 10.9.3, Python 3.11.14, uv 0.9.5, Rust 1.89.0,
wasm-bindgen 0.2.127 and WASI SDK 27.0. The existing dependency locks and
encoder provenance are authoritative. Set `PACKAGE_RUST_BIN`, `PACKAGE_WASI_SDK`
and `PACKAGE_WASM_BINDGEN` when tools are not installed under `package_creation/.tools`.

```sh
python3.11 -m pip install uv==0.9.5
python3.11 -m uv sync --locked --extra test --extra offline
npm ci
npm run typecheck
npm run lint
npm test
```

Maintained frontend lives under `web/src` with TypeScript and esbuild. Rust source
is under `package_creation/builder`. Vendored codec implementations retain their
upstream formatting and arithmetic. The native examples are offline test oracles.
Reference Python modules and historical design documents are not browser services.

Build the static edition using `npm run build:static`. Reference preparation is
listed in `tests/fixtures/registry.json` and feature documentation. Set
`PROJECT_FIXTURE_ROOT` to a reference checkout. Raw curves and game install files
are development inputs, not browser uploads. Real-ESRGAN export instructions are
in `tools/local-upscale/README.md`. The prepared site contains the verified runtime
assets needed to run the tools without development dependencies.
