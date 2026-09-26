# Hair recolor packages

The third tab in Sims 2 Creator Tools creates independent hair recolors directly from the textures embedded in a recolor package. Upload a mesh and matching recolor together, or choose an installed standard hairstyle. No PNG, BMP, separate alpha or mask uploads are needed or shown in the hair form. There is no Body Shop mode. The download is `Creator_HairName_Recolors.zip`, containing one `Creator_HairName_Color.package` per selected color, `README.txt`, and uploaded mesh dependencies under `Meshes/`. The original texture converter and tattoo package creator keep their image inputs and workflows.

**Hairstyle source** offers **Standard in-game** or **Upload packages**. Standard in-game then shows an **In-game hairstyle** picker populated only with standard catalog entries. No hairstyle is selected implicitly. The picker groups installed styles by game content and gender. Its nine choices are female Ponytail, High ponytail, Bun, French braid and Short cut, male Ponytail, Crew cut and Cornrows, plus female Swirl from Mansion & Garden. The eight base-game styles support Teen, Young Adult, Adult and Elder with 512 by 512 textures. Young Adult requires University or Legacy Collection. Swirl retains its original Young Adult, Adult and Elder support. Pooklet is credited for its Swirl recolor template rather than presented as a mesh category.

**Upload packages** has separate required fields for mesh packages and recolor packages. Each field accepts multiple loose `.package` files. Select the hairstyle's mesh in the first field and at least one matching recolor in the second. Both fields must have files before inspection starts automatically. Missing-file guidance and upload errors appear next to the fields, with a retry action if needed. Extract downloaded ZIPs before selecting their packages. Classification uses resource contents, not filenames. A sole recolor is selected automatically, while several recolors require a choice before color previews. The website requires selecting package files for each custom hairstyle import and does not show saved uploads. Uploaded hairstyles remain separate from standard styles. Identical package sets receive the same inspected template identity regardless of upload order. Saved batches retain their own files and selected recolor. There is no shared hairstyle reuse library or server import API.

Every reachable embedded color atlas supplies its own RGB and alpha. The Rust browser engine extracts each atlas once per job and uses the same extracted source for previews and final packages. Dimensions, UV layouts, material subsets and age routes remain intact. A non-square 1024 by 512 atlas is supported alongside 512 by 512 atlases. Similar-looking younger and Elder atlases stay separate, and Toddler retains its own texture.

The form has three sections: **Choose a hairstyle**, **Choose colors**, and **Preview and download**. Colors and export controls appear after a valid hairstyle is chosen. The compact palette starts with all supported colors selected. **Quick selection** replaces the selection with all colors, a natural family, a color category or an empty set. **Show** only filters the visible choices and never changes the selection. Elder greys are disabled when the hairstyle has no Elder age.

**Texture preview and adjustments** is optional and closed initially. It opens after **Preview colors** to show the original texture, prepared base and selected target together. Its **Texture** picker switches between distinct reachable color TXTRs, with dimensions and supported ages. **Preview color** switches between selected colors without rebuilding previews. A shared younger/Elder atlas also shows the natural elder result. Unused textures and untextured scalp materials retain their data. Dimensions are never used to substitute one texture for another.

**Adjust input base** contains one editor for the currently viewed texture. Settings are independent and retained when switching textures. Standard catalog templates already contain prepared Volatile textures, while the pinned Swirl package contains Primer. Custom recolors default to **Unknown / other color (approximate)**, the existing Arbitrary texture conversion. A known Pooklet base can be selected instead. Black point, white point and gamma appear only for approximate conversion. **Reset this texture** restores only that texture's defaults. The README records each texture's settings.

One primary action follows the batch state: **Preview colors**, **Create ZIP**, then **Download ZIP**. **Cancel build** stops the browser worker, while **Resume build** preserves identities and validated checkpoints. Completed batches are read-only. **New batch** starts another submission and clears custom colors. **Saved batches** provides Open, Download and Delete. Batches are stored in this browser at this site address until explicitly deleted, subject to browser storage clearing or eviction.

## Server installation

Build the existing Rust project, which also builds the separate `ts2-hair-builder` binary:

```sh
.venv/bin/python -c 'from package_creation.harness import build_helper; build_helper()'
.venv/bin/python -m package_creation.hair.install \
  --curves "$HOME/Downloads/IaKoa-Pooklet Curves.7z" \
  --template "$HOME/Downloads/mansion and garden swirl f.rar"
.venv/bin/python -m package_creation.service --lan --port 8001
```

If using the harness through Python, call `package_creation.harness.build_helper()`. Run the service with `--lan` for direct access on the trusted LAN.

The installer verifies the IaKoa archive SHA-256 `ee2b84d55e4c0c762b0a42c86b28497c81ff1afabbc98ac6f590f0846a7a583e` and extracts `Pooklet-MG-Swirl-Primer-platinum-blond.package` from [Pooklet's archive](https://simfileshare.net/download/321854/). The package must match `330a974828e8053cba0a0c6b94d5d729243ddf1dfde7576f1660ba301df34d64`.

The downloaded source assets are ignored under `assets/`. The offline asset build exports normalized mappings for the browser. Raw GIMP presets remain outside the distribution and generated ZIPs. [IaKoa's original post](https://iakoasims.tumblr.com/post/88756518223/pooklet-for-gimp) provides the curve credits and download.

## Standard game catalog

The standard catalog is installed from the user's game files and stored under `assets/standard/` and distributed as hashed runtime assets. There are no hairstyle-specific API or browser branches. `standard_sources.json` contains the source recipes and SHA-256 of each required GZPS, 3IDR, TXMT, TXTR, LIFO, CRES and SHPE resource. The current recipes were verified against the referenced Legacy installation. Base-game geometry is used, with University property-set updates retaining the game's combined Adult and Young Adult age flags.

Run the standalone extractor on the machine holding that installation:

```sh
python3 package_creation/hair/standard_extract.py \
  --game-root "/path/to/The Sims 2 Legacy" \
  --recipes package_creation/hair/standard_sources.json \
  --output /path/to/extracted-hair
```

Copy the extracted packages to the website server, then install:

```sh
.venv/bin/python -m package_creation.hair.standard --extracted /path/to/extracted-hair
```

The installer checks source hashes again, follows the actual resource graph, embeds the externally stored LIFO mip levels, constructs independent catalog entries, and gives each template new identities and material names. Mesh references remain external references to the installed game. Unknown property fields and material settings survive. No game installation files are modified. Repeating an unchanged installation is idempotent. Updated recipes should use new versioned IDs.

The standard templates embed bases prepared approximately from the original Maxis textures through the arbitrary-grey-to-Volatile conversion, with game alpha preserved. Younger and Elder layouts remain separate. Natural-color previews of an Elder-only slot show Mail Bomb grey. Swirl uses its exact supplied Primer-to-Volatile conversion. The website uses the embedded textures directly and does not require separate example-image downloads.

The current batch omits styles with secondary texture dependencies such as normal maps. Supporting those requires preserving and relinking those additional resources. They must not be silently discarded to make a style importable.

## Image processing

The palette includes 16 natural colors, two standalone elder greys, and 25 unnatural colors. Volatile is an identity transform. All other targets use the saved 256-sample RGB tables. The Value and Alpha channels are checked as identity tables. Samples are rounded to 8-bit sRGB values.

Primer, Grenade, Incendiary and Pooklet Grey use the corresponding special conversion curves. Arbitrary texture mode uses sRGB Rec.709 luma desaturation, black and white points, and standard midtone gamma, then `Pooklet Volatile BASE - USE GREY BASE`. This is an approximation. It is deliberately separate from `PookletSpecial Grey-to-Volatile`.

Embedded textures are decoded directly to RGBA buffers and their alpha is retained before compression. Every target is derived independently from the prepared Volatile base. The hair UI has no separate PNG/BMP or mask inputs. Previews and builds consume the same Rust mappings and per-texture settings.

## Package behavior and custom templates

The builder uses the pinned `dbpf` Rust library for DBPF, texture encoding and RefPack. Hair logic is in `builder/src/hair.rs`, separate from tattoo generation. It edits generic CPF properties and preserves unknown properties, original subsets, mesh references and material settings.

Catalog BINX object indices are resolved through their 3IDRs to GZPS or XHTN resources. Rendering indices are resolved through each GZPS's rendering 3IDR. These are different relationships. In the default template, catalog BINX instance 2 references elder GZPS instance 1. See the [3IDR format reference](https://modthesims.info/wiki.php?title=3IDR).

Each selected color has a new group, named scenegraph resources and custom hairtone identity. Natural quartets share a hairstyle family within the submission. Unnatural hairtone and hairstyle family identities are distinct. Retrying a job preserves all identities, while a new submission generates new ones. Older 7.1 indexes are written as 7.2 to retain full scenegraph hashes.

Natural elders receive separate Mail Bomb textures and materials. Unnatural elders retain the selected color. Standalone Mail Bomb and Pipe Bomb packages retain only elder catalog entries. Every package includes its own elder resources, with no generated cross-package dependencies.

Custom templates require one hair recolor per package, unique age and gender coverage, 1 to 16 embedded power-of-two textures up to 2048 by 2048, and external mesh references. Uploads may contain several separate recolor packages, which become separate selectable entries. Every supplied mesh must match a recolor. Mesh CRES/SHPE references and scene links are checked against the included mesh packages. Mesh packages are preserved byte for byte and placed once under `Meshes/` in the generated ZIP. The README lists installation requirements and source filenames, and the output validator checks their hashes, resource identities and reference coverage.

Untextured `SimSkin` scalp materials retain their shader, properties and file list. They do not require image assignments. Age-pruned recolors may retain stale, unused 3IDR slots. The importer clears only unused unresolved local slots and reconstructs a missing hairtone BINX only when its existing 3IDR reference is unambiguous. Live missing references remain errors. Generated output validation reads the raw package without applying those source repairs.

Each active color texture is processed from its own embedded atlas. The source limit remains 16 embedded TXTRs including unused resources. Natural recolors generate target colors for younger-only textures and Mail Bomb for Elder-only textures. Shared textures split only when the prepared results differ, and materials split only when their texture references differ. Untextured scalp materials remain shared. Distinct source atlases are never merged by dimensions or pixel similarity. Generated packages can contain up to 32 textures when all 16 source textures genuinely need both variants.

Unrelated source textures retain their encoded pixels and existing mip chains once, with local names and links relinked. Elder-only exports remove appearance resources used exclusively by omitted younger ages unless another retained dependency needs them. Validation checks required variants against actual age, material, file-list and resource links, rather than relying on texture-name suffixes. Recolored textures retain their format and receive complete mip chains. This avoids unused duplicates without reducing resolution or removing mipmaps. The five-texture Rose 72 source produces five textures and eighteen materials per natural recolor.

Multi-frame textures, external LIFO mip data, unsupported secondary dependencies such as normal maps, combined elder/non-elder property sets, mesh-only uploads, merged styles and default replacements need preparation before upload. A mesh alone does not provide the recolor's textures or age/material routes. DBPF 1.1 and 1.2 packages are supported with the same bounds and resource checks. DXT1 textures require binary alpha. New browser batches use the current engine. Existing saved batches keep their pinned engine, checkpoints and completed downloads.

For example, extract the supplied `letopeggy4033.zip` and select both `f_leto_peggy4033_volatile.package` and `peggy_fh080712_p001_mesh_letoedit.package`. Its recolor uses one 1024 by 1024 DXT5 texture with female Toddler, Child, Teen, Young Adult and Adult support. It has no Elder entry. The picker disables the two elder-only grey selections for such templates. All other 41 colors are available.

Package inspection rejects ZIPs, unsafe or conflicting filenames, malformed packages, conflicting mesh identities, missing dependencies and unrelated meshes. Limits are 64 packages, 64 MiB per package and 128 MiB total. Uploaded mesh bytes are saved locally and included unchanged in the output ZIP. Old server hairstyle-library files are preserved on disk during rollout but are not used by the browser UI.

## Bundles with both custom and game meshes

A custom recolor may use the uploaded mesh for some ages and a built-in game mesh for others. Missing references are accepted only when found in the versioned game mesh catalog shipped with the browser runtime. Game group numbers alone do not establish that a dependency exists. Older 32-bit 3IDR references resolve only when the installed type/group/low-instance match is unambiguous. The uploaded mesh remains byte-identical, while game resources remain external and are not copied into the ZIP.

Build the reference catalog on the machine with the game:

```sh
python3 game_meshes.py --game-root "/path/to/The Sims 2 Legacy" --output game-meshes.json
```

Copy `package_creation/hair/game_meshes.py` to that machine first, then install the generated JSON as `package_creation/hair/assets/game-meshes.json` on the website server. The catalog contains resource keys, source paths and hashes, with no game resource payloads. It scans installed `TSData/Res/Sims3D` and `TSData/Res/3D` packages. Rebuild it when updating the installed game content. Requirements and verified game references are recorded in each job and generated README.

The remote `2texture` fixture contains `mesh_rosehair_0124.package` and DBPF 1.2 `recolor_3555b7d0_rose72.package`. Five embedded DXT3 routes cover younger main hair, younger layers plus the flower accessory, Toddler's combined atlas, Elder main hair and Elder layers. Child through Elder use the supplied mesh. Toddler's CRES and SHPE were verified in the Base Game's Sims06.package and Sims05.package. Both uploaded files are sufficient, with the Base Game dependency retained externally.

## Browser runtime and lifecycle

Creation, curve parsing, package inspection and previews run in the local WASM worker. All package POST APIs return HTTP 410. Authenticated `GET /api/v1/package-runtime/manifest` and content-addressed downloads deliver the engine and assets. `GET /api/v1/hair/templates` reads prebuilt standard metadata. Legacy status, completed ZIP downloads and deletion remain until their original one-hour expiry.

IndexedDB stores inputs, custom curves, immutable snapshots, identities, asset versions, validation reports and completed ZIP chunks. It never expires batches automatically. Hair resumes at the first unfinished package. A single origin-wide lease prevents concurrent builds across tabs and stale attempt tokens cannot publish output. There is a 512 MiB ZIP limit, 2 GiB application storage budget, 1 GiB maximum WASM heap and ten-minute active-processing timeout.

New downloads apply lossless ZIP compression to each member when it saves space. Extracted `.package` files and meshes are byte-identical to the validated build outputs. This archive optimization does not change DXT encoding, RefPack compression, texture dimensions or complete mipmaps. The archive writer is pinned with the batch, so existing saved batches keep their original downloads. No additional compression setting is required.

See [the shared runtime documentation](../service/PACKAGE_RUNTIME.md) for protocol, installation, storage limits and recovery behavior.

## Validation

```sh
.venv/bin/python -m unittest package_creation.tests.test_hair -v
.venv/bin/python -m unittest package_creation.tests.test_service package_creation.tests.test_harness -q
```

Run the Rust binary's tests with the installed toolchain and `package_creation.harness.rust_environment()`:

```python
import subprocess
from package_creation.harness import rust_environment

subprocess.run([
    "package_creation/.tools/cargo/bin/cargo", "test", "--locked", "--release",
    "--manifest-path", "package_creation/builder/Cargo.toml", "--lib",
], env=rust_environment(), check=True)
```

The hair tests exercise the requested four-color ZIP, all 43 colors, explicit multiple texture assignments, old indexes, invalid uploads, origin checks, upload limits, cancellation, timeout, retry, expiry, alpha and profiles. Rust tests cover catalog routing, DXT1/3/5 and RGBA mip chains, compressed alpha error, unknown properties, deterministic retry bytes and independent rejection of corrupted bins.

The standard catalog integration test additionally builds a natural quartet, TNT and Mail Bomb for each installed style, with mixed PNG and RGB V5 BMP assignments. It checks distinct meshes across styles, resource coexistence, age routing, full mip chains and preserved alpha. The offline installer test checks reproducible template bytes and rejection of changed source resources. Standard catalog evidence is in `artifacts/hair-validation/standard/`. These new styles have structural validation, with gameplay validation recorded separately.

Integration evidence and example ZIPs are written to `artifacts/hair-validation/` in the repository root. Structural results and gameplay results must be recorded separately. Passing these tests does not establish gameplay compatibility for every custom template or new uploaded texture.

The [embedded-bundle validation record](../../artifacts/hair-validation/embedded-bundle/VALIDATION.md) covers the remote five-texture DBPF 1.2 bundle, all 43 colors without image uploads, verified Base Game dependencies, unchanged mesh bytes, independent texture settings, and completed in-app browser and Firefox jobs. Elder-only pruning preserves rendering 3IDRs even when they also contain a removed younger catalog entry. The record separates structural and browser results from gameplay testing.

The [2026-09-05 validation record](../../artifacts/hair-validation/VALIDATION.md) includes the 51 automated checks and an isolated Legacy Create-a-Sim session covering catalog entries, natural color switching, Adult/Elder routing, transparency and close/full-body rendering, with screenshots and exact installed package hashes.

## Named custom curves

The 43 built-in Pooklet presets are fixed. Their compiled mappings ship as browser assets. Raw preset files remain outside the distribution. In **Choose colors**, open **Add your own color** to import a GIMP curve or create one in the browser, give it a name, and choose its game category.

The editor offers Value, Red, Green and Blue channels. Add and drag points, adjust their numeric input/output coordinates, or use arrow keys on the focused graph. Shift moves ten levels at a time. Delete removes an interior point. Channels start as identity mappings and interpolate linearly between control points. Imported curves display their exact samples and support renaming, category changes and replacement files.

Every custom target uses the prepared Volatile base, independently of other targets. Value is applied after each individual RGB channel. Alpha is preserved. Save the color, then use **Preview colors** or **Update previews** before generating the ZIP.

Custom-category colors keep their chosen color at Elder age. Black, Brown, Blond and Red use the usual bins and Mail Bomb for Elders. Each added color has an independent hairstyle family, separate from the four built-in Pooklet families. These settings preserve the source hairstyle's supported ages.

Up to 16 custom colors may accompany the 43 built-ins. Names accept 1–48 ASCII letters, numbers, spaces, underscores and hyphens, starting with a letter or number. Spaces are compacted in package filenames. Conflicts with any built-in or added color are rejected, including case-insensitive filename collisions.

Custom curves belong only to the current batch. Editing its draft keeps them, and **New batch** clears them for the new submission. IndexedDB restores their definitions and stable identities after reload or browser closure. They are never added to a shared palette and are excluded from output ZIPs.

GIMP imports accept the text preset format with 256 samples per channel, regardless of extension. Linear-light presets and curves that modify Alpha are rejected. RGB mappings are applied before Value, then quantized to three authoritative 256-entry lookup tables. Editor points use linear interpolation. The Rust engine validates names, categories, samples and filename collisions for previews and builds.

See [WASM migration validation](../../artifacts/wasm-migration/VALIDATION.md) for current structural, parity and browser evidence. Earlier gameplay records describe their exact previously tested packages and are not proof of gameplay acceptance for the WASM release.

## Texture compression

New batches use DirectXTex for DXT1, DXT3 and DXT5. Advanced options offers
Body Shop, which uses the recovered nondithered compressors for DXT1,
DXT3 and DXT5. Formats and full mip chains are preserved. The choice is
disabled when no selected output uses any of these compressed formats.
Hair previews show the prepared colors before compression.

Each batch saves its encoder in the immutable build snapshot. Cancellation,
retry and reload retain it. New batches reset to DirectXTex. Historical jobs
retain their pinned engines and original compression. Reports and README.txt
record the selected algorithm, versions and effective format routing.

The five-texture Rose natural recolors retain five textures and eighteen
materials with either encoder. Uploaded meshes remain byte-identical. Body
Shop compressor matching does not promise identical package bytes or sizes.
See `builder/TEXTURE_ENCODING.md` for sources, build flags and validation.

### RefPack package storage

Advanced options also includes **RefPack package compression**, enabled by
default for new batches. This compresses finished resource bytes losslessly,
without changing texture quality, alpha, formats, dimensions or full mipmaps.
Resources are compressed only when that saves space. Turning it off produces
larger packages with the same resource content. Mesh dependencies are copied
unchanged. The choice stays with the saved batch and is listed in README.txt.
Historical batches retain their original engines and compression behavior.
