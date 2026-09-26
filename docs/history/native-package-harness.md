> Historical design and offline reference. For the current six-tool application, see the [project README](../../README.md). Commands and relative links below describe the earlier layout.

# Sims 2 package-creation harness

This directory contains a standalone, SimPE-free package builder. The original schema-v1 path turns one validated 1024 by 1024 RGBA PNG into one adult AM or AF body-overlay bundle. The schema-v2 path and its dedicated service build multi-tattoo Adult and Elder collections.

The harness is intentionally separate from the parent upload server. Version 1 is a private GPL prototype and makes no claim of in-game correctness.

The package service presents four tools in one interface. Its texture tab uses the browser WASM converter, and its tattoo package tab accepts already-mapped textures. [Hair recolors](hair/README.md) generates a ZIP of selected built-in and custom colors from embedded textures in a standard hairstyle or uploaded mesh and recolor packages. [Object creator](objects/README.md) uses a decoration template and an imported 3D model to create an independent object with new GUIDs. Package creation runs in the browser through the shared Rust WASM engine.

## Scope

Each job builds one gender and one controller box. Menu groups are disabled, the pinned box graphic is retained, supernatural compatibility remains enabled, Remove All behavior remains present, and all controller BHAV bytes must match the template exactly.

The output directory contains:

- `<slug>_OverlayBox.package`
- `<slug>_AM_Overlay.package` or `<slug>_AF_Overlay.package`
- `NoFaceOverlay.package`
- `manifest.json`
- `validation.json`
- `build.log`

The overlay package contains exactly 12 logical resources: one selected adult body branch, the no-face branch, and the collection resources. Its replacement TXTR is encoded as DXT5 or BC3 with 11 mip levels.

## Commands

Run the harness with the parent project's virtual environment:

```bash
.venv/bin/python package_creation/harness.py prepare
```

`prepare` downloads only the hash-pinned Pick'N'Mix archive, extracts the three required packages into the ignored `templates/` area, provisions Rust 1.89.0 under ignored `.tools/`, and builds the pinned Rust helper with `Cargo.lock`.

Create a persistent job once:

```bash
.venv/bin/python package_creation/harness.py init package_creation/jobs/ash-kiryu-am.json \
  --slug ash-kiryu-am \
  --catalog-name "Ash Kiryu Tattoo" \
  --catalog-description "Adult male body overlay" \
  --preset am \
  --input-png output/ash_kiryu.png
```

`init` generates the object GUID, overlay group ID, and family UUID once. Reusing the job reuses those identities.

Build a new bundle:

```bash
.venv/bin/python package_creation/harness.py build package_creation/jobs/ash-kiryu-am.json
```

The default destination is `package_creation/output/<slug>/`. The harness refuses an existing destination and publishes by renaming a complete temporary directory.

Validate an existing bundle without rebuilding it:

```bash
.venv/bin/python package_creation/harness.py validate package_creation/output/ash-kiryu-am
```

Run deterministic AM coverage and generated AF structural coverage:

```bash
.venv/bin/python package_creation/harness.py smoke
```

## Multi-tattoo jobs

Schema-v2 jobs contain one controller GUID and one overlay group and family UUID per tattoo. They are fixed to Adult and Elder, age mask `0x18`, and normal Sims only. Menu order and visual layer order must each be a unique sequence from zero. Visual priorities are derived from layer order, starting at `0x65` and ending at `0x78` for a 20-tattoo build.

Schema-v2 controllers reuse the in-game `Osho Nuff Tablet` model through the original `sculptureOshoKoma` scene reference. They do not embed Maxis mesh or texture resources, so Mansion & Garden Stuff or The Sims 2 Legacy Collection is required.

Build and independently revalidate one merged package:

```bash
.venv/bin/python package_creation/harness.py build-merged job.json --output-dir merged
.venv/bin/python package_creation/harness.py validate-merged job.json merged
```

The merged output directory contains:

```text
<slug>.package
manifest.json
validation.json
build.log
```

The `.package` contains the controller, every tattoo overlay resource, and the shared no-face texture. Rebuilding the same accepted internal job produces byte-identical package and report files. The earlier `build-multi` and `validate-multi` commands remain available for split-package regression coverage.

## Package service

Install the pinned Python dependencies and prepare the package inputs before starting the service:

```bash
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python package_creation/harness.py prepare
.venv/bin/python -m package_creation.service --lan --port 8001
```

The default listener is `127.0.0.1:8001`. `--lan` binds to all interfaces and prints a plain URL for the trusted LAN. There are no access keys or sign-in prompts. API requests require an allowed Host and a same-origin Origin header for state-changing methods. CORS is not enabled.

Open the printed URL on another device, or enter the server IP address and port directly. The tattoo editor supports plain LAN HTTP, including browsers that expose `crypto.getRandomValues()` but restrict `crypto.randomUUID()` to secure contexts.

Public routes:

- `GET /`, the four-tab creator tools
- `GET /health`, listener liveness
- `GET /ready`, helper and pinned-template integrity
- `GET /api/v1/capabilities`, supported formats and limits
- `POST /api/v1/convert`, retired with HTTP 410, conversion runs locally in the browser
- `POST /api/v1/builds`, one multipart `spec` text part plus one file part named for each referenced asset ID
- `GET /api/v1/builds/{id}`, state, progress, warnings, and sanitized errors
- `GET /api/v1/builds/{id}/package`, completed merged `.package` download
- `GET /api/v1/builds/{id}/bundle`, compatibility alias returning the same `.package` bytes
- `POST /api/v1/builds/{id}/retry`, retry a failed accepted build with its persisted identities
- `DELETE /api/v1/builds/{id}`, cancellation and private spool deletion

The service uses one package worker and four waiting slots. Each PNG is limited to 8 MiB. Requests are limited to 40 files and 128 MiB. Generated packages are limited to 64 MiB and complete private job directories to 256 MiB. Completed jobs expire after one hour.

Accepted uploads are written under random server names in a private `0700` spool, decoded as single-frame 1024 by 1024 RGBA PNGs, and re-encoded without metadata. The worker uses a fixed argument vector and environment allowlist. It stops the process group after 120 seconds. Raw worker output is bounded and is never downloaded with the package.

The service does not download templates, install Rust, or build dependencies. `/ready` fails until the offline `prepare` command has completed.

## Validation model

The Rust helper reopens the merged package and validates its complete resource union, unique TGIs, selected genders, age metadata, persistent identities, every retained BINX, COLL, GZPS, XTOL, 3IDR, TXMT, and TXTR relationship, DXT5 encoding, all 11 mip levels, decoded alpha similarity, controller arrays, the single shared no-face texture, the exact Maxis `sculptureOshoKoma` model reference, removal of all six embedded template scene resources, and unchanged BHAV bytes.

The Python probe does not call the Rust parser. It independently checks DBPF magic, DBPF 1.1 and index 7.2, index sizing, entry boundaries, overlapping entries, duplicate keys, compressed-directory references and RefPack headers, TGI-set hashes, and resource-type counts.

The pinned controller template contains no NREF resource. Its object names live in the OBJD, and the scene-graph resources use narrow, asserted edits of their length-prefixed strings and numeric links. If a future template adds NREF or changes the asserted layouts, the build stops instead of guessing.

## Deliberate stop conditions

The builder fails before publication when a template or starting TGI set changes, a required resource is missing, a TXMT layout differs, a scene-graph raw replacement is not exact, BHAV bytes differ, or the independent DBPF probe fails.

In-game application, removal, multiple-layer behavior, clothing and shower changes, lot reload, Adult-to-Elder transition, save, restart, and conflict testing remain a separate acceptance phase. A structural pass is not an install recommendation.

## Provenance and licensing

See [THIRD_PARTY.md](THIRD_PARTY.md) and [templates/manifest.json](templates/manifest.json). Downloaded template packages, generated packages, build targets, and the local toolchain are ignored.
