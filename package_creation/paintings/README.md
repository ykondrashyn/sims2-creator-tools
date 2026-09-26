# Painting creator

The fifth creator tab makes one independent wall painting from a static PNG,
JPEG or WebP. Every new painting gets its own catalog name, price and GUIDs.
Download `Creator_PaintingName.package` directly to the game's Downloads folder.
The initial templates require The Sims 2 Legacy Collection. Base Game-only
compatibility is not claimed. No image is sent to the service.

Choose an image, select a frame, position its crop, then enter catalog details.
Fill frame preserves proportions and allows dragging, zooming and keyboard
adjustment. Show whole image adds a canvas-colored border. Transparency is
composited onto that canvas color. Frames and hanging positions retain their
source size. The Three.js wall preview uses the original mesh with the actual
composed texture. Browser lighting is a neutral approximation of game lighting.

Bella Squared, The Lady On Red, "SimCity at Night" and Rolling Hills by H. Sean
are curated templates. The original UVs determine the artwork region. In
particular, Lady's visible canvas has a measured aspect ratio of approximately
0.638 and occupies the upper portion of a shared 256 by 512 atlas. Replacing
that whole atlas or using its 0.5 ratio would corrupt the frame or distort art.
The landscape templates retain their multi-tile definitions and new linked GUIDs.
All artwork mip levels are embedded in each output, including the original
large levels stored in separate LIFO resources in the game installation.

The browser uses the same Rust compositor for preview and final output. It
applies EXIF orientation, converts supported embedded RGB ICC profiles to sRGB,
uses deterministic Lanczos resampling with fractional crops, preserves template alpha and recreates complete mip
chains in each template's original format. Source images and validated outputs
are retained in IndexedDB until explicitly deleted, subject to browser storage
clearing and eviction. Interrupted builds retain their snapshot and identities.
Saved painting engines and templates are pinned to their original release.

Limits: one input, 32 MiB, 32 million pixels, 8192 pixels per side, 64 MiB output.
Painting builds share the existing origin-wide lease, ten-minute timeout,
2 GiB storage budget and 1 GiB maximum WASM heap. Unsupported image formats,
animations, damaged profiles, missing assets and quota failures are reported.
PNG color definitions using HDR cICP or gamma/chromaticities without a supported
ICC or sRGB profile are rejected with an sRGB export instruction.
Custom frame packages, image batches, frame recoloring and resizing are not
supported in this version.

## Development

Read the game and extract candidate resources without modifying the game:

```
.venv/bin/python scripts/extract_painting_templates.py
.venv/bin/python -m scripts.install_painting_templates
.venv/bin/python scripts/build_package_runtime.py --output artifacts/painting-creator/runtime
```

The installer checks pinned source hashes, follows scene and texture references,
and verifies that the artwork polygons are the inspected planar quad. Recipe
UV coordinates map that quad to a front-facing image, including repeat addressing
and orientation correction. Source hashes, texture identities, wall coordinates,
physical dimensions and game requirements accompany each recipe.

The shared painting worker operations are `painting_inspect_image`,
`painting_open`, `painting_compose`, `painting_prepare`, `painting_build` and
`painting_validate`. The `painting` native example accepts the same operations
and asset buffers through an offline JSON adapter. The server only serves the
runtime manifest and immutable assets. No painting POST endpoint exists.

Structural and browser validation must be reported separately from gameplay
checks in an isolated game profile. Cloning, pixel parity and a browser preview
do not prove catalog, wall-cutaway or in-game rendering behavior.

Original template assets: Maxis / Electronic Arts. Uploaded artwork belongs to
its original creator. Enter image credits in Description, which is saved in the
painting's catalog entry. Engine and dependency licenses and corresponding
source remain available through the site's existing source download.
