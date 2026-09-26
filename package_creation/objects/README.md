# Object creator

Objects and body textures are built locally by the shared Rust WebAssembly
worker. No object inputs are uploaded.

The website follows one workflow: choose a decoration template, import and
preview a model, then name and create the object. The template supplies behavior
and placement rules. The imported model supplies its appearance. Cloning and
allocating independent GUIDs happen automatically during creation.

The picker offers the supported floor and tabletop decoration templates.
Alternatively, upload a complete supported decoration package with its custom
dependencies and use **Check template**. Templates whose behavior does not
support model replacement are rejected with an explanation.

Selecting a template shows its intended use, in-game placement, occupied tile
area, measured original height and game requirements. Uploaded templates show the same
details after inspection. The tabletop description explains that the preview
table is not included in the package and that game surface slots can shrink
decorations. Floor descriptions explain that resizing does not enlarge routing.

A model is required for preview and package creation. Preview works before
catalog details are entered, and partial drafts retain their model files after
reload. Height is a percentage of a fixed AM reference, independent of the template. Set the final
catalog name, description and price in the last step.

Download `Creator_ObjectName.package` directly into your Sims 2 Downloads folder.
The Description field also holds original creator and model credits, and is
written into the object's catalog description.

Source profiles are pinned to the installed Legacy game resources. Each profile
records its source hashes, private behavior functions and shared game references.
Uploaded objects must use a supported behavior profile and include their custom
appearance dependencies. Unsupported behavior is rejected during inspection.

New batches allocate independent GUIDs for every object definition and relink
local scene resources and material overrides. Retries retain their identities.
Saved work belongs to the current browser and site address until explicitly
deleted, subject to browser storage clearing and eviction.

Structural validation and browser rendering do not establish gameplay acceptance.
Test new objects in an isolated game profile before using them in a played lot.

## Installed profiles and input formats

The website offers Ancient Transport Urn Sculpture, Bowl of Plastic Fruit,
"On A Pedestal" by Yucan Byall, "Immobile Chimes" Mobile in Steel and The My-Chi
Sculpture Form. Their occupied areas are one, one, one, two and four tiles.
Tea Party in Teak, The Talking Table and Pix-Arm Drafting Lamp are also installed.
The other profiles and native cloning operations remain available to offline
tools and engine regression tests, with no standalone cloning mode in the UI.
These profiles use Legacy Collection EP9 behavior resources and Base appearance
resources. Their full source hashes are recorded in the installed catalog.
They are not advertised as compatible with a Base-only game installation.

Import a binary glTF 2 `.glb`, or one `.gltf` scene with its buffers and PNG/JPEG
textures inside a ZIP. Import accepts static triangle meshes with UV0, up to
16 material groups and 65535 vertices per group. Textures are limited to 2048
pixels per side. Bake lighting maps and vertex colors into base-color textures.
Animation, skinning, morph targets, blended transparency and required extensions
are rejected. Metallic and roughness factors are flattened to a neutral material.

### Browser model optimization

For dense models, choose **Optimize model** before previewing. A separate local
worker uses glTF Transform 4.5.0 and meshoptimizer 1.2.0 WASM to merge equivalent
materials, flatten transforms, join compatible geometry, weld identical vertices
and simplify with weighted normals and UV coordinates. UV seams, sharp creases
and boundaries between material groups are protected. Images are never decoded
or re-encoded during optimization. Source credits in glTF asset metadata survive.

The default target is 20,000 triangles with Balanced detail protection. The target
is approximate, and each material group keeps at least 100 triangles if it started
above that count. High protection allows less change, Low permits more reduction.
Simplification can change appearance, so inspect the resulting Three.js preview.
It cannot combine distinct materials into an atlas or guarantee an in-game frame
rate. A model with over 16 remaining groups needs material consolidation in a 3D
editor. This is an importer limit, not a verified Sims 2 engine limit.

Both GLB and glTF ZIP inputs are supported, up to 64 MiB compressed, 128 MiB
expanded, one million source triangles, two million vertices and 4096 source
groups. Unsupported material features and animations must be baked before export.
Oversized textures need resizing before import. Optimization has a ten-minute
timeout, cancellation, and the same cross-tab lease as package builds.

The original source, optimized GLB, report and optimizer asset version are saved
with the draft in IndexedDB. **Use original model** restores the source, and each
optimization starts again from it. Selecting another file clears the previous
optimization. **Download optimized GLB** also provides a reusable model file.
Completed packages remain read-only. Models never leave the browser.

Build the optimizer bundle with `npm ci && npm run build` in
`tools/object-optimizer`. Its content hash and dependency notices are published
in the runtime manifest. The corresponding source archive includes the optimizer
and the npm dependency packages used by the bundle.
The pinned meshoptimizer C++ source is included under `tools/object-optimizer/vendor`,
with its revision, SHA-256 and upstream build instructions.

The model is uniformly scaled to an absolute target height. Rotation preserves
height. The source decoration's placement anchor, tile definitions and slot rules
are retained. The importer does not author animations or new
interaction behavior. Material output uses DXT3 with a complete mip chain.

Functional uploads must match one installed profile's behavior functions,
interaction tables, constants, slots and object-definition settings. Missing
appearance dependencies, altered behavior and merged object collections are
rejected. Catalog edits and new resource identities do not affect this match.

## Offline template installation

Run `scripts/extract_object_templates.py` on the game machine with `--game-root`
and an explicit `--output` directory. It reads the installed game without editing
it and requires all eight pinned main-mesh hashes to match. Copy the resulting
directory to the development machine.

Build the native `object` example, then run `scripts/install_object_templates.py`
with `--input`, `--output package_creation/objects/assets`, `--model` pointing to
a static test GLB, and `--reports`. Installation occurs only after all eight
objects clone and all five decoration profiles accept model replacement. Outputs
are reopened and compared against their preview and deterministic retry.

Build a staged runtime with `scripts/build_package_runtime.py --output DIR`.
The tab stays hidden for runtime releases without installed object profiles.
Keep fixture catalogs out of production assets.

## Height and scale preview

New models start at the selected template's measured original height, including
uploaded templates after inspection. **Fit within template** scales the model uniformly
to stay within both that height and the occupied floor area. It preserves the
placement anchor and checks the actual union of tiles, including gaps. It can
reduce height to fit a wide or deep model. Fitting is a one-time action. Rotation
keeps the chosen height and scale, and shows a warning if the model overhangs.
Unknown coverage or an anchor outside the occupied tiles produces an explanation
instead of claiming a fit. Click Fit again to resize for a different orientation.
The template preview always retains its source dimensions. **25%**, **50%**,
**Sim-sized 100%**, **Tall 150%** and custom heights from 10 to 300% remain available.
Fitting may go below that manual range when needed, or preserve a template's
original height above it. The source template in the comparison keeps its size.
The displayed fit percentage is rounded, but preview, builds and saved batches
retain the exact height. Existing saved sizes are kept until explicitly changed.
The chosen height is stored with the batch. Preview and generation use that
exact height. The transient fit calculation and normal layout share Rust code.
Opening an older editable draft with automatic fitting preserves its last saved
height and updates its engine, provided the selected template asset is unchanged.
Completed and interrupted builds retain their pinned engines and cannot be resized.
100% is 1.8788737058639526 game units: the canonical AM source body, aligned at
its feet, plus the proportional head described in `assets/object-reference.json`.
AF is a display alternative and never changes the target height. The displayed
mannequins preserve source body dimensions and are not complete dressed Sims.

A Scale room with floor tiles, two walls and a doorway is the default. Collapsed
Preview display controls offer Studio, AM/AF/hidden mannequin, occupied floor
area and dimension overlays. The table reference uses The Talking Table's
0.7999999523162842-unit surface translation from its pinned CRES. Table elevation
is display-only. The mesh height is authoritative for the package. In-game
surface slots can apply their own additional scale, as observed with small
decoration slots on the NuMica Allinall Card Table. That original slot behavior
is preserved. Reset view frames the imported object and the visible mannequin.
Sizing, rotation and Your object/Template comparison preserve the camera.

The browser and builder call the same `object_layout` operation. It returns a
transform, dimensions, tile coverage and a signature tying placement acknowledgement
to the model, template, height and rotation. Oversized objects are buildable after
acknowledgement. Unknown coverage also requires acknowledgement. The warning uses
the conservative projected selection bounds against the union of occupied tile
squares, not a new routing footprint. No routing or extra tile definitions are
authored. Choose a wider template when a larger reserved area is needed.

Decoded source geometry is cached for the current input set. Existing preview
materials and geometry are transformed directly during layout changes, without
repeating compression. All generated GMDC selection bounds and normals are updated.
Independent sizing accepts a single simultaneous visible mesh route and unchanged
decoration scene transforms. Complex custom scenes are rejected before building
instead of duplicating the imported model or reporting an incorrect height.
The three added game sculptures each have one visible mesh route. Multi-tile
master and child definitions keep their offsets and behavior, with unique new
GUIDs and one imported model.

Sizing and preview settings autosave with the draft. Build snapshots freeze the
absolute target height and acknowledgement. Completed packages keep their engines
and downloads. Drafts from the old sizing system are retained and direct users to
start a new batch. Existing installed packages are never resized automatically.

After template installation, regenerate the versioned reference assets with
`node scripts/build_object_references.mjs REPORTS_DIRECTORY`. The report directory
must contain the installer's dining-table scene graph and clone preview. Reference
body GLBs come from the pinned tattoo body exports. No game files are edited.

Validation commands for this addition:

```sh
.venv/bin/python -m unittest package_creation.tests.test_objects package_creation.tests.test_package_runtime
node package_creation/tests/object_sizing_parity.mjs artifacts/object-sizing/runtime
```

`object_sizing_browser_check.py` exercises a staged LAN server in an isolated
Firefox profile. Structural and browser results belong in the validation report
separately from actual in-game placement and routing observations.
