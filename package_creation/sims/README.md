# Sim creator, experimental

The sixth tab imports neutral GLB or glTF ZIP models and saves fitting drafts in
the browser. Canonical AM/AF GMDC resources provide skin weights, inverse bind
transforms, named skeleton joints and morph bindings. The existing display-only
mannequins are not rigging sources. Worker inputs never go to a server endpoint.

Complete replacement-Sim downloads remain disabled in the browser and engine.
An explicitly acknowledged Adult Male Everyday body test is available through
`sim_experimental_build`. It keeps the stock head, hair and other outfits.
The custom rigid head is excluded, including triangles crossing its boundary.
The resulting neckline can therefore need further fitting. This output is for
an isolated test profile, not a completed custom Sim.
`sim_build` must not publish a package until the single-file SavedSims and
persistent-head gameplay gates have passed. Browser preview is not game proof.

## References

Run `python scripts/fetch_sim_fixtures.py` to fetch hash-pinned Dellesims AM/AF
Body Shop comparison samples and Xarteras' wolf-head set. Archives are extracted
to `artifacts/sim-creator/fixtures`. They are not installed or distributed as
production templates. Source URLs, creator credits and SHA-256 hashes are saved
with the fixtures.

Run `python scripts/extract_sim_references.py` with the reference machine awake.
It reads pinned AM/AF body GMDC and CRES resources over SSH and writes only local
assets. All extracted source payloads must match their recorded SHA-256 hashes.

`assets/scaffolds.json` describes stock AM/AF samples created using Body Shop
in a dedicated profile at
`~/.local/share/ts2-creator-validation/sim` on the reference machine.
Normal game profiles and their Downloads folders are not modified.

The wolf reference comprises a hair-slot head, skin and eye disabler. Its known
career-hat failure does not satisfy the persistent-head gate. An adult-only
accessory and scoped hiding remain subject to separate in-game validation.

The downloaded AM/AF Sims have 15 DBPF index entries, including the compression
directory. The parser reports 14 actual content resources. Seven explicit
external scene/appearance links in each sample match resources in the installed
Legacy game. The collection reference `6c4f359d-0ffefefe-000000000ffe0001`
was not located in the searched package indexes. This is not complete dependency
validation, and neither downloaded celebrity Sim has been installed for testing.

## Runtime

Native wrapper: `cargo build --example sim`. Supply a JSON object on stdin with
`op`, `assets` (logical names mapped to filesystem paths) and `params`.
The same versioned operations are dispatched by BrowserEngine in WASM:
`sim_inspect_scaffold`, `sim_reference`, `sim_inspect_model`, `sim_fit`,
`sim_preview`, `sim_prepare`, `sim_build`, and `sim_validate`.

Model inspection rejects imported animation, skinning and morph targets with
an explicit neutral-export instruction. It never silently drops them.
Inputs retain the 64 MiB file and 128 MiB expanded-data bounds. Rig previews
share the origin-wide lease, use cancellation through worker termination and
retain saved inputs. Saved drafts pin their engine and selected reference asset.

The guided fitting workflow uses four stages: alignment, anatomical markers,
head separation and movement review. The shared scale room, floor tiles and
neutral mannequins use the Object creator reference assets. Display mannequin
selection never affects the target AM/AF rig or model dimensions.

`sim_align`, `sim_landmarks` and `sim_guided_fit` implement guided version 2.
The source model is uniformly normalized once, then source landmark segments
are mapped to the fixed game skeleton with smoothly blended transforms. The
head keeps its shape after uniform normalization. A body-side neck transition
connects it to the adjusted torso. Weights and morphs use barycentric nearest
surface interpolation, restricted by anatomical region. Distance warnings are
review aids, not a quality score or proof of correct anatomy.

Actual CRES child links define the rig hierarchy. Non-joint grip nodes are
collapsed between their nearest joint ancestors. In particular, the hair joints
under `head_grip` inherit the head transform. The viewer uses the same inverse
bind matrices, with correct parent-relative rest transforms and GPU joint types.

Preview geometry uses one transferable binary buffer and separate PNG buffers.
Decoded inputs, rig metadata, connected pieces and textures are cached. Camera,
marker and display edits do not recolor or recompress textures. Only Apply fit
runs deformation and weight transfer. Connected pieces can be assigned separately
even when they share one material. Marker placement is suggested, not anatomical
detection. Users must review the guides, especially for stylized models.

Drafts pin their engine and scale-reference assets. They save alignment, markers,
partition assignments, review state and display settings. Older drafts remain
unchanged and offer Start guided-fit copy. Failed saves are shown explicitly.
Cancellation is fenced during both preparation and worker processing. Reload
restores inputs and guides without starting an expensive fit automatically.

## Release gates

- Single package loads in Body Shop and CAS without source component packages.
- Adult head persists across clothes, bathing, maternity and career hats.
- Unsupported age transitions and offspring have valid stock appearances.
- Other Sims keep their original faces, eyes and bodies.
- Native/WASM output parity and isolated-game evidence are recorded separately.

Do not enable complete replacement-Sim downloads based on structural validation alone. The generated
artifact will be one `Creator_SimName.package` installed in SavedSims, with a
custom Everyday body, stock alternate clothing and a rigid custom head.

## Initial preview validation, 2026-09-11

The isolated Body Shop profile loaded two sequential development experiments:

1. A stock saved Sim with its custom diagnostic green Everyday recolor embedded.
2. The same experiment with independently named CRES, SHPE, GMND and GMDC
   resources embedded, including the lower-detail geometry variant.

Before each load, source component packages and the preceding prototype were
moved outside SavedSims. Only the current prototype and the stock AF scaffold
remained there. The green garment rendered in the Sim bin, while the AF sample
kept its original appearance. The normal game profile was not touched.

The second prototype has 26 content resources and SHA-256
`c55eea764ee0de814781ccd956969c3a70b34ae81e9c574a4a1658f35fbdae1e`.
Its geometry is a cloned game body, not an imported humanoid. It has no custom
head. Body Shop loading alone does not pass the planned CAS, outfit, bathing,
hat, age-transition or offspring gates.

Evidence lives under `artifacts/sim-creator`:

- `fixtures/manifest.json`, extracted-package reports and `game-link-report.json`.
- `outfit-prototype-report.json`, `mesh-prototype-report.json` and screenshots.
- `parity/report.json`: six exact native/WASM fitting comparisons, both bodies
  at 0, 45 and 90 degrees, with deterministic repeated results.
- `browser/report.json`: Firefox 155.0.1 fitting, saved inputs, body and morph
  controls, reload/restart, cancellation of a Ronald fit, cross-tab locking,
  blocked builds, narrow layout, deletion and GET-only network capture.
- `all-regressions.log`: 107 Python tests passed across the existing tools.
- `rust-tests.log`: 28 Rust tests passed. `three-tests.log`: seven tests passed.
- `conversion-browser-tests.log`: AM/AF PNG parity, downloads, recovery and
  existing tattoo, hair, object and Three.js initialization passed in Firefox.

The small parity fixture used 7,667,712 bytes of WASM memory. Its recorded timing
includes the native comparison process and must not be presented as browser-only
processing time. The original Ronald model contains 28 material groups and over
one million imported vertices. Its unoptimized native fitting result is roughly
120 MB of JSON. Cancellation was tested in Firefox, a complete Ronald browser
fit has not been accepted. These measurements describe the initial preview implementation. Guided fitting
and binary buffer transport are covered by the later validation section.

The native `sim` example exposes `fixture_report` and `prototype_merge` for
offline experiments. `prototype_merge` is excluded from WASM and is not a
production download path. Use the release example for texture compression.

## Guided fitting validation, 2026-09-11

Evidence is under `artifacts/sim-creator`:

- `guided-native-tests.log`: AM/AF hierarchy, alignment, invalid landmarks,
  confirmations, connected pieces sharing one material, topology, UVs, rigid
  heads, weights, morphs and deterministic buffers.
- `guided-parity/report.json`: exact native/WASM metadata and binary geometry
  comparisons for AM, AF and the original Ronald model. Repeated small fits are
  deterministic. The large case verifies computation, not fit quality.
- `guided-browser/report.json`: Firefox scene, marker keyboard editing,
  mirrored pairs, history, partition display, fitting, camera preservation,
  reload, narrow layout, cross-tab locking, legacy copies, deletion and GET-only
  network capture. Rendered geometry is checked separately from resource data.
- `guided-large-browser/report.json`: original Ronald import, cancellation,
  complete retry, quota feedback and browser restart recovery. Suggested markers
  were used for capacity testing, not model-quality acceptance.
- `guided-all-regressions.log`: existing converter, tattoo, hair, object,
  painting, service and Sim regressions. Gameplay gates remain closed.

Timing and memory measurements describe the recorded machine and browser only.
The scene and movement checks are not Body Shop or gameplay acceptance.

The in-app browser passed initial scene, AM/AF mannequin switching, orthographic
and orbit camera checks with no WebGL errors. Its automation tool refused the
local fixture path because of its configured file-access roots, so full file
import automation was performed in Firefox instead.

## Experimental Everyday body export

After reviewing a guided AM fit, enter Creator and Sim name, acknowledge the
body-only limitation and choose **Create test package**. The browser downloads
`Creator_SimName.package` directly for SavedSims. Completed test packages are
read-only. Cancelled builds can retry with the same snapshot and identities.
Older pinned drafts are retained and require a new batch for this operation.

The pinned `am-everyday-test.package` derives from the isolated Body Shop AM
scaffold, exported cord-jacket outfit and extracted Maxis scene resources. Its
SHA-256 is `c55eea764ee0de814781ccd956969c3a70b34ae81e9c574a4a1658f35fbdae1e`.
It contains no downloaded celebrity or wolf components. Source provenance for
the stock rigs remains in `references.json` and `scaffolds.json`.

The exporter replaces body geometry, remaps CAS override indexes and follows
both resource keys and named SHPE-to-GMND links. The earlier mesh prototype
still named the stock GMNDs, so its Body Shop visibility did not prove that
its embedded geometry was being rendered. Experimental exports explicitly
resolve every body shape to the generated embedded mesh.

Identical vertex attributes and texture pixels are shared losslessly. Both
LOD routes use the reviewed mesh, so this prototype has no reduced-detail LOD.
Resource compression stays within the 24-bit RefPack length limit. The 64 MiB
output and 1 GiB WASM heap limits remain in force. Standard Sim package creation
and persistent-head gameplay acceptance remain gated separately.
