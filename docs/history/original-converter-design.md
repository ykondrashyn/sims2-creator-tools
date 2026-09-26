> Historical design and offline reference. For the current six-tool application, see the [project README](../../README.md). Commands and relative links below describe the earlier layout.

## Working automated MVP

This repository now contains a headless Blender implementation of the Tattooer adult-male and adult-female body TS4 to TS2 conversion. It uses the original `AM-body-4t2-1024.blend` and `AF-body-4t2-1024.blend` templates and starts a clean Blender 3.4.1 process for every source texture.

Quick start:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python scripts/fetch_template.py

.venv/bin/python convert.py \
  ash_kiryu.png \
  ash_majima.png \
  --output-dir output \
  --blender /absolute/path/to/blender
```

For a minimal browser interface on this Mac and the local network:

```bash
.venv/bin/python server.py \
  --blender /absolute/path/to/blender
```

Open the local-network URL printed at startup, choose Adult male (AM) or Adult female (AF), and upload one 1024 by 2048 RGBA PNG. AM is selected by default. The successful response downloads a TS2-mapped 1024 by 1024 PNG directly. This development server has no authentication, so use it only on a trusted local network.

The command writes converted PNGs and `output/manifest.json`, with logs and a checkerboard contact sheet under `artifacts/`. See [AUTOMATION.md](AUTOMATION.md) for prerequisites, template inspection, single-file usage, validation details, and limitations.

## Experimental Sims 2 package service

The service under `package_creation/service/` provides one interface with two tabs. The first converts 1024 by 2048 TS4 body textures to TS2 using the pinned Blender workflow. The second accepts package-ready 1024 by 1024 RGBA textures and builds one `.package` containing the overlay box, every tattoo, and the shared no-face resource. The standalone conversion server remains available for compatibility.

Prepare the pinned private template inputs once, then start the loopback service:

```bash
.venv/bin/python package_creation/harness.py prepare
.venv/bin/python -m package_creation.service
```

Use `--lan` only on a trusted local network. Open the token-bearing URL printed at startup. Generated packages are structurally validated, not gameplay tested. See [package_creation/README.md](package_creation/README.md) for the package format, API, limits, and acceptance boundary.

The remaining sections are the original design and expansion notes. `AUTOMATION.md` is the source of truth for the implemented MVP.

## Recommended Blender-first MVP

The first version should **not** try to convert an entire `.package` file. That adds package parsing, swatch metadata, naming, and TS2 package creation before we have proven that automated baking is reliable.

Build this vertical slice first:

```text
Folder of TS4 diffuse PNGs
          |
          v
Select one Tattooer template
          |
          v
Headless Blender bake
          |
          v
Folder of TS2 PNGs
          |
          v
Validation report
```

Start with exactly one template:

```text
AM-body-4t2-1024.blend
```

That is the correct adult-male template for the supplied Ash textures. It outputs 1024 by 1024 textures, so the MVP avoids the additional 2048 to 1024 resizing step. The manual operation is already very constrained: replace the image used by `ts4 body`, select `ts4 body`, make `ts2 body` active, run Bake, and save the baked image. ([Tumblr][1])

## 1. Freeze a reproducible baseline

Use a portable, pinned Blender version, initially Blender 3.4.x. Do not start by supporting every available Blender release. The templates were built for Blender 3.4 or newer, but that does not guarantee that every future version produces identical pixels or handles the template’s alpha setup identically. ([Tumblr][1])

Create five to ten manual reference conversions:

1. A small torso tattoo.
2. An arm tattoo.
3. A shoulder tattoo.
4. Something crossing a UV seam.
5. A mostly transparent image.
6. A dense or full-body tattoo.
7. A completely transparent test image.
8. A solid diagnostic grid with numbered squares.

These become your **golden outputs**. Automation is accepted only when it reproduces them.

The diagnostic grid is important. A normal tattoo can hide subtle mirroring, projection, or seam errors. A numbered grid immediately exposes them.

## 2. Inspect the Blender template once

Before writing the converter, make a small Blender inspection script:

```text
scripts/inspect_template.py
```

Run it like this:

```bash
blender --background AM-body-4t2-1024.blend \
  --python scripts/inspect_template.py
```

It should write a JSON description of:

* Scene render engine
* Bake configuration
* Object names
* Active object
* Selected objects
* Material slots
* Image Texture nodes
* Currently active node in every material
* Source image name and dimensions
* Target bake image name and dimensions
* Color space and alpha settings
* UV map names

The tutorial gives us the object names `ts4 body` and `ts2 body`, but it does not document all internal material, node, and image datablock names. Those should be discovered from the actual file rather than guessed. ([Tumblr][1])

Save the result as a template profile:

```json
{
  "id": "am-body-4t2-1024",
  "blend_file": "AM-body-4t2-1024.blend",
  "source_object": "ts4 body",
  "target_object": "ts2 body",
  "source_material": "discovered-material-name",
  "source_image_node": "discovered-source-node",
  "target_material": "discovered-material-name",
  "target_image_node": "discovered-target-node",
  "width": 1024,
  "height": 1024
}
```

Do not try to make the first script magically infer every possible template. Explicit profiles are safer and easier to debug.

## 3. Implement one-file conversion

Create:

```text
scripts/bake_one.py
```

The outer command should look approximately like:

```bash
blender \
  --background templates/AM-body-4t2-1024.blend \
  --python-exit-code 1 \
  --python scripts/bake_one.py \
  -- \
  --job temp/job.json
```

Blender officially supports running without a graphical interface using background mode, and its Python API exposes selected-to-active texture baking directly. There is no need to simulate clicks or use computer vision to operate the UI. ([Blender Documentation][2])

A job file could contain:

```json
{
  "profile": "profiles/body_4t2.json",
  "input": "input/tattoo_01.png",
  "output": "output/.tattoo_01.tmp.png",
  "device": "CPU"
}
```

Using a JSON job file is more robust than passing many paths through command-line options, especially on Windows and with spaces or Unicode characters.

### What `bake_one.py` should do

#### A. Validate the template

Fail clearly when:

* `ts4 body` is missing.
* `ts2 body` is missing.
* Either object is not a mesh.
* The expected material or node is missing.
* The target node does not contain an image.
* The output image has an unexpected size.
* The scene is using an unsupported render engine.

Do not silently select the first vaguely matching node. Silent guessing will eventually generate plausible-looking but incorrect output.

#### B. Load the TS4 texture

Conceptually:

```python
source_image = bpy.data.images.load(
    input_path,
    check_existing=False,
)

source_node.image = source_image
```

Preserve the original template node’s color-space and alpha behavior. Do not force arbitrary color management settings until the automated output has been compared with the manual reference.

#### C. Activate the target bake image

For the target material:

```python
for node in target_material.node_tree.nodes:
    node.select = False

target_image_node.select = True
target_material.node_tree.nodes.active = target_image_node
```

Blender needs the correct Image Texture node to be active so it knows where the bake should go.

#### D. Reproduce the selection order

The tutorial’s selection state is:

```text
Selected source: ts4 body
Selected target: ts2 body
Active object:   ts2 body
```

In Python:

```python
bpy.ops.object.select_all(action="DESELECT")

source_object.select_set(True)
target_object.select_set(True)

bpy.context.view_layer.objects.active = target_object
```

That selection order is central to selected-to-active baking. ([Tumblr][1])

#### E. Use the template’s stored bake settings

The template already contains the carefully tuned projection and baking values. Read those values and pass them explicitly to the operator:

```python
bake = scene.render.bake
bpy.ops.object.bake(
    type=scene.cycles.bake_type,
    pass_filter={"COLOR"},
    use_selected_to_active=bake.use_selected_to_active,
    margin=bake.margin,
    max_ray_distance=bake.max_ray_distance,
    cage_extrusion=bake.cage_extrusion,
)
```

This explicit mapping is required. In Blender 3.4.1, `bpy.ops.object.bake()` has an operator default of `COMBINED`. Calling it without arguments produced a dark shaded bake even though the scene stored `DIFFUSE`. The manual Bake button uses the configured diffuse color pass.

The important principle is:

```text
Template remains the source of truth for bake settings.
Script controls only input, selection, execution, and output.
```

Do not hard-code margin, extrusion, cage, or ray distance. Read them from the loaded template so the operator cannot drift from the source configuration.

#### F. Save without saving the `.blend`

Set the output path on the baked image and save it as PNG:

```python
target_image.filepath_raw = output_path
target_image.file_format = "PNG"
target_image.save()
```

Then exit Blender without saving the template.

The manual tutorial also recommends closing the template without saving after conversion, so that every future conversion begins from the original state. ([Tumblr][1])

## 4. Intentionally launch Blender once per image

For the first correct implementation:

```text
one input PNG
    =
one fresh Blender process
```

That sounds inefficient, but it is the right MVP tradeoff.

Each process starts with:

* A clean template
* A clean target image
* Original node assignments
* Original selection state
* No leaked image datablocks
* No stale pixels from the previous swatch
* No need to reset Blender state correctly

The outer Python program can loop over the folder and run Blender repeatedly.

Only after correctness is proven should the converter load the template once and process all swatches inside a single Blender process. Persistent batching is faster, but it creates additional failure modes around clearing target images, releasing source images, restoring node state, and recovering after a failed bake.

## 5. Build the outer batch CLI

Suggested command:

```bash
tattooer-auto convert \
  --template am-body-4t2-1024 \
  --input exported-ts4/ \
  --output converted-ts2/ \
  --blender "C:/Tools/Blender/blender.exe"
```

The outer program should:

1. Find PNG files recursively.
2. Sort them deterministically.
3. Create one job JSON per file.
4. Launch Blender with `subprocess.run(..., shell=False)`.
5. Capture Blender stdout and stderr.
6. Save initially to a temporary output path.
7. Validate the temporary PNG.
8. Atomically rename it to the final name.
9. Continue processing after an individual failure.
10. Write a final manifest.

Example result:

```text
Converting 27 textures

[01/27] rose_black.png          OK
[02/27] rose_red.png            OK
[03/27] sleeve_left.png         OK, alpha warning
[04/27] chest_symbol.png        FAILED, bake returned no image
...
```

Suggested manifest:

```json
{
  "tool_version": "0.1.0",
  "blender_version": "3.4.x",
  "template": "am-body-4t2-1024",
  "template_sha256": "...",
  "results": [
    {
      "input": "rose_black.png",
      "output": "rose_black_ts2.png",
      "input_sha256": "...",
      "status": "ok",
      "width": 1024,
      "height": 1024,
      "alpha_coverage": 0.037
    }
  ]
}
```

Hashing the template matters. Otherwise a user can accidentally modify the `.blend`, run the tool later, and receive different results without realizing why.

## 6. Add deterministic validation

The tool cannot prove that every tattoo looks good, but it can reject many broken results automatically.

For every output, verify:

* File exists.
* File is a readable PNG.
* Dimensions are exactly 1024 by 1024.
* Image contains four channels.
* Output is not unexpectedly all transparent.
* Output is not unexpectedly all opaque.
* RGB content exists inside the non-transparent region.
* The output differs from the template’s original blank image.
* File size is within a plausible range.
* No NaN or invalid pixel values appeared before saving.

Compare golden tests against their manual references.

Exact byte equality may be too strict if CPU and GPU baking differ slightly. Use:

* Exact dimensions
* Exact alpha mode expectations
* Per-pixel maximum difference
* Mean absolute difference
* Alpha-mask overlap
* A visual diff image

For example:

```text
PASS:
mean channel error <= 1/255
alpha intersection-over-union >= 0.999
no unexplained changed region
```

Those thresholds are starting hypotheses, not facts. Set them from the actual golden results.

## 7. Generate a human review sheet

The first MVP should acknowledge that validation cannot determine whether a conversion is aesthetically acceptable.

Produce a checkerboard contact sheet:

```text
rose_black_ts2.png
rose_red_ts2.png
sleeve_left_ts2.png
...
```

Each thumbnail should show:

* Filename
* Texture over a checkerboard background
* Alpha coverage
* Any warning
* Red border or warning icon for suspicious output

A later version can render the tattoo on a TS2 body from front, back, left, and right views. That is more useful for seam and distortion review, but it should not block the first working converter.

Paluding notes that the templates are not perfect and that distorted or stretched regions can require manual adjustment of the overlapping meshes. That means a human review step remains justified even when the mechanical conversion is fully automated. ([Tumblr][1])

## 8. Keep CPU as the initial default

The tutorial recommends GPU compute because it makes baking faster, but it also supports CPU baking. ([Tumblr][1])

For the first version:

```text
Default: CPU
Optional later: CUDA, OptiX, HIP, Metal
```

CPU is slower, but easier to reproduce across systems. Use it for golden tests.

After correctness is established, add:

```bash
--device auto
--device cpu
--device cuda
--device optix
--device hip
--device metal
```

The GPU implementation must enumerate available Cycles devices and fail back to CPU, rather than assuming that an Nvidia card means CUDA is correctly configured.

## 9. Repository structure

```text
tattooer-auto/
├── pyproject.toml
├── README.md
├── templates/
│   ├── README.md
│   └── AM-body-4t2-1024.blend
├── profiles/
│   └── body_4t2.json
├── scripts/
│   ├── inspect_template.py
│   └── bake_one.py
├── tattooer_auto/
│   ├── __main__.py
│   ├── cli.py
│   ├── jobs.py
│   ├── blender_runner.py
│   ├── validation.py
│   ├── manifest.py
│   └── contact_sheet.py
└── tests/
    ├── fixtures/
    ├── golden/
    ├── test_validation.py
    └── test_manifest.py
```

Keep the Blender-side script dependency-free. Blender ships with its own Python environment, so importing random packages installed in the system Python will be unreliable.

The outer application can use Pillow for PNG validation and contact sheets.

## 10. Definition of done for MVP 1

The version is complete when this works:

```bash
tattooer-auto convert \
  --template am-body-4t2-1024 \
  --input test-swatches \
  --output result
```

And all of these are true:

* It converts at least 20 swatches unattended.
* Each conversion begins from an untouched template.
* Automated golden outputs match manual golden outputs.
* Transparency is preserved.
* Spaces and Unicode filenames work.
* One broken PNG does not stop the remaining batch.
* Partial files are not presented as successful output.
* A JSON report records every conversion.
* A human can review the entire batch from one contact sheet.
* No Blender window appears.
* The original `.blend` file is never modified.

## 11. What should explicitly remain outside the first MVP

Do not include these yet:

* Reading TS4 `.package` files
* Automatically exporting diffuse textures
* Creating ready-to-install TS2 packages
* Automatically choosing male, female, face, or scalp templates
* 2048 output and resizing
* AI visual review
* AI texture repair
* Web hosting
* Multiple simultaneous Blender workers
* Supporting every Blender version
* Automatically modifying meshes when a bake looks distorted

Trying to include those would turn a small, testable automation project into several unrelated projects.

## 12. Expansion after the first template works

### MVP 2: all Tattooer templates

Add profiles for:

```text
AF body, TS4 to TS2
AM body, TS4 to TS2
AF TS4 to AM TS2
AF TS2 to AM TS2
Face
Face plus scalp
```

Paluding’s release contains these separate conversion directions, with face and scalp still having different limitations. ([Tumblr][1])

Do not infer the template from image pixels. Make the user choose it:

```bash
--template am-body-4t2-1024
```

A PNG alone generally does not contain enough reliable information to tell whether it is a body, face, male, female, or scalp texture.

### MVP 3: persistent batch mode

Open Blender once, then:

1. Load the next source image.
2. Replace the source node image.
3. Allocate or clear a fresh target image.
4. Bake.
5. Save.
6. Remove the source image datablock.
7. Restore known state.
8. Continue.

Keep the original one-process-per-file mode as:

```bash
--isolation full
```

It will remain valuable for debugging and problematic conversions.

### MVP 4: 2048 baking and resizing

For thin details:

```text
Bake at 2048
Resize to 1024
Use nearest-neighbor resampling
```

That follows Paluding’s recommendation for preserving thin, hard-edged details. Face and scalp templates normally use 512 output. ([Tumblr][1])

### MVP 5: package integration

Only then extend the flow:

```text
TS4 package
    |
extract diffuse swatches
    |
Blender converter
    |
converted PNGs
    |
clone TS2 overlay template package
    |
replace textures and metadata
    |
finished TS2 package
```

## 13. One distribution issue to resolve

Paluding says users may modify the templates, save new copies, and share them. That is encouraging, but it is not the same as a conventional software license with explicit redistribution terms. ([Tumblr][1])

For a private MVP:

```text
Use the locally downloaded template directly.
```

For a public release, the safer choices are:

1. Obtain explicit permission to bundle the `.blend` files.
2. Require users to download the Tattooer archive themselves and point the program at it.
3. Publish only scripts and template profiles, not the original templates.

The third option is the least legally and socially contentious.

## Concrete first implementation backlog

The correct order is:

```text
1. Add template and pin Blender
2. Produce manual golden outputs
3. Write inspect_template.py
4. Create the AF 1024 profile
5. Write bake_one.py
6. Convert one PNG headlessly
7. Compare it with the manual output
8. Write the outer folder-processing CLI
9. Add temporary files and failure isolation
10. Add PNG validation
11. Add manifest generation
12. Add contact sheet generation
13. Run the full golden suite
14. Test a real multi-swatch tattoo set
```

The most important architectural decision is **one clean Blender process per texture for the first release**. It deliberately sacrifices speed to remove state-management bugs. Once the output is demonstrably correct, optimizing it into a single persistent Blender process is straightforward.

[1]: https://paluding.tumblr.com/post/722575234618834944/updating-the-tattooer-ver-34 "Paluding - Updating... The Tattooer (ver. 3.4)!"
[2]: https://docs.blender.org/api/current/bpy.ops.object.html?utm_source=chatgpt.com "Object Operators - Blender Python API"
