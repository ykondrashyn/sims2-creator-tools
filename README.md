# Sims 2 Creator Tools

A static, browser-only toolkit for The Sims 2, published at
[ykondrashyn.github.io/sims2-creator-tools](https://ykondrashyn.github.io/sims2-creator-tools/).
No account, token, server API or external inference service is used.

The seven tabs are:

1. Convert body texture, TS4 to TS2 AM/AF textures.
2. Tattoo creator, layered AM/AF overlay packages.
3. Hair recolors, the fixed Pooklet palette and named custom curves.
4. Object creator, imported models using game objects as behavior templates.
5. Painting creator, images in curated game frames.
6. Sim creator, guided fitting and the existing experimental Everyday-body export.
7. Upscale image, four local models with CPU or explicit WebGPU, including native 2× and 4× output.

Files are processed in browser workers. Saved creator batches live in IndexedDB
until deleted, subject to browser eviction or clearing. Upscaling images and
results last only for the page session. Saved work belongs to the exact browser
and site address. Batches on your LAN site do not automatically appear here.
WebGPU needs a supported GPU and a secure context. CPU remains available.

## Run the prepared site

```sh
python3 -m tools.static_release verify
python3 -m http.server 8080 --directory site
```

Open `http://localhost:8080/`. No FastAPI or game installation is needed to serve
the prepared artifact. Both `/` and `/sims2-creator-tools/` deployment paths work.

## Development and publication

See [development](docs/development.md), [architecture](docs/architecture.md),
[validation](docs/testing.md), and [Pages deployment](docs/deployment.md).
`site/` is checked in with hashes and a source identity. Actions validates it before
publication. Building a new runtime locally requires the pinned toolchains and
reference assets listed in `tests/fixtures/registry.json`.

## Credits and limitations

The Sims 2 and game assets belong to Electronic Arts. This is an independent fan
tool, not an EA product. Template creators and Pooklet/IaKoa are credited in the
runtime metadata and generated instructions. Real-ESRGAN is by Xintao Wang and
contributors. The engine's GPL license, upstream licenses and corresponding source
are distributed with the site. See [third-party notices](package_creation/THIRD_PARTY.md)
and [local upscaler notices](tools/local-upscale/NOTICES.txt).

Package structure and browser checks do not establish gameplay compatibility.
Experimental Sim restrictions remain in place. Downloaded comparison Sims, private user
inputs, local captures and the LAN service are not deployed by this repository.
