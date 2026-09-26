# Package texture compression

New runtimes default to `texture_encoder=directxtex`. `bodyshop` selects
the recovered nondithered Body Shop algorithms for DXT1, DXT3 and DXT5.
The older `bodyshop_dxt3` API value retains its DXT3-only routing. Unknown
settings fail validation. Uncompressed formats
use the existing DBPF implementation. Conversion pixels are unaffected.

The C ABI wraps only BC1, BC2 and BC3 from DirectXTex revision
`868198cb4bcbc4e359372e7ba38d7a6dda3a6afa`. DirectXMath and SAL hashes, sources
and MIT notices are in `vendor/directxtex`. Upstream BC.cpp is unchanged.
`build.rs` replaces its platform precompiled-header include in a temporary
translation unit. The wrapper uses scalar perceptual encoding, no dither,
0.5 DXT1 alpha threshold, no floating-point contraction or fast math, and
`-fno-strict-aliasing` for DirectXMath's typed storage casts. The latter is
required for native/WASM DXT5 alpha parity. No Windows or WASI services,
threads, GPU or SIMD are used by the resulting module.

Install the SHA-256-pinned WASI SDK 27.0 with
`python3 scripts/install_texture_toolchain.py` from the repository root.
Alternatively install that exact SDK release for your host and set
`PACKAGE_WASI_SDK` to its directory. Rust is pinned to 1.89.0 and
wasm-bindgen-cli to 0.2.127. Native builds use clang++ or `CXX`.

```
cargo build --locked --release --lib --target wasm32-unknown-unknown
wasm-bindgen --target web target/wasm32-unknown-unknown/release/ts2_package_builder.wasm
```

Set `RUSTFLAGS="-C link-arg=--max-memory=1073741824"` for the production heap
limit. `scripts/build_package_runtime.py` performs source integrity checks,
builds the engine and publishes hashed assets with corresponding source.
Its source archive includes all Cargo dependencies for an offline build.

The Body Shop port models each recovered x87 binary32 rounding boundary,
including temporary round-toward-zero regions. RGB beneath zero alpha is
retained. Its fixed constants and operation order are intentional. The
independent Python specification SHA is
`eb734f9b9cb8f787932412c749aebde71243dec021d9c65762d37c797ac05020`.
The intermediate-state fixtures cover the original float ramp, endpoint
ordering, ties, solid colors and optimizer iterations. Captured game code
and executable files are not distributed.

`TextureResource::recompress_with_encoder` is the only DBPF extension.
Existing APIs retain their behavior. It encodes each original frame and mip
through a checked callback. The creator generates its full mip chain before
encoding. Partial blocks replicate edge pixels, including 1x1 and 2x2 mips.
Painting blocks outside the artwork mask retain their original bytes.

Codec acceptance uses `tests/texture_codec_parity.mjs`, a native example and
the same WASM operation. All 21 captured DXT3 payloads contain 409,600 blocks.
`tests/prepare_texture_codec_corpus.py` reproduces the independent oracle's
10,000 random and 1,025 byte-input structured blocks. The additional original
non-byte float ramp is exercised by the intermediate-state Rust test.
Package parity runners accept `TEXTURE_ENCODER`, so both settings exercise
identical package inputs and identities. Compression matching does not imply
identical whole Body Shop packages, smaller files or gameplay acceptance.

The setting belongs to each saved batch and is frozen into its snapshot.
Historical saved jobs keep their original engine. Prepared hair bases and
fitted model caches exclude this setting because they contain uncompressed
inputs. Compressed previews are rebuilt from the current job parameters,
including the encoder. No compressed output cache is shared across jobs.

## Independent lossless storage

`package_compression.rs` handles the separate RefPack layer for finished DBPF
packages. `refpack_compression` defaults to true and accepts only a boolean.
It does not alter DirectXTex/Body Shop selection or re-encode any texture.
The writer chooses the smaller representation, including compression-directory
cost, and verifies raw resource hashes after serialization. Its report includes
actual stored and expanded sizes. Internal template preparation keeps its
existing storage policy to avoid repeatedly compressing temporary packages.

The version-2 manifest advertises `bodyshop` for all three package formats.
Version-1 saved jobs retain their pinned engine and DXT3-only selector.
`tests/prepare_bodyshop_family_corpus.py` adds pinned independent DXT1/3/5
byte-input oracles to the codec corpus. `bodyshop-family-traces.json` retains
original-instruction float boundary examples and alpha solver states.
