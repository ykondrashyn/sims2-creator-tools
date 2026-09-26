# Practical TS2 hair recolor workflow

This workflow assumes the mesh already exists and the goal is to retexture or recolor it. Creating or converting a mesh also requires a 3D editor, correct bone assignments, subset ordering, and package relinking.

## 1. Preserve a known-good baseline

- Keep the original mesh and recolor packages unchanged.
- Work on copies in a separate folder.
- Keep an unbinned master until all recolors and age states are correct.
- Record the source creator, mesh name, texture system, and permissions.
- Delete or move cache files only when your normal TS2 test procedure calls for it.

Binning tools can remove redundant elder resources and rewrite metadata. Re-binning already processed files is riskier than starting from the unbinned master.

## 2. Decide what kind of edit is needed

Ask these questions before opening GIMP:

| Question | If yes |
| --- | --- |
| Is the shape correct and only the hue wrong? | Recolor with Curves or another tone transform |
| Are the strand pattern and highlights wrong? | Retexture, then create a Volatile base |
| Should existing bangs or wisps be shorter? | Edit the alpha if the mesh has mapped surface there |
| Is a new lock needed outside the existing silhouette? | Edit the mesh |
| Does the hair disappear only from some angles? | Inspect faces, normals, and inside or outside layers |
| Does the correct shape render with bad transparency? | Inspect alpha group order and TXMT settings |

This prevents a common mistake, trying to solve geometry or alpha problems with color editing.

## 3. Export from the exact mesh and mapping

Use Body Shop to create a new project from a known-good recolor of the exact hair mesh. A Volatile recolor is ideal for the IaKoa presets.

Body Shop writes the editable texture set into its Projects folder. Do not rename, delete, or move those files before import. The official manual warns that Body Shop expects the exported names and locations.

If using SimPE instead:

- Work on a copy of the recolor package.
- Inspect every TXTR and its TXMT reference.
- Export the full color texture without accidentally baking the display alpha into its RGB edges.
- Preserve dimensions and inspect existing mipmaps and compression.

## 4. Inventory the project by age and subset

Do not edit only the first adult texture you see. Make a table like this for the actual project:

| Age | Color texture | Alpha | Mesh subset | Unique image? |
| --- | --- | --- | --- | --- |
| Toddler | filename | filename_alpha | hair_alpha5 | yes or shared |
| Child | filename | filename_alpha | hair_alpha5 | yes or shared |
| Teen | filename | filename_alpha | hair_alpha5 | yes or shared |
| Adult or YA | filename | filename_alpha | hair_alpha5 | yes or shared |
| Elder | filename | filename_alpha | hair_alpha5 | yes or shared |

There may be multiple alpha groups per age. There may also be blank scalp textures or duplicate color images with different masks. Body Shop can deduplicate identical images internally, so package resource counts do not always equal project-file counts.

## 5. Build or preserve the Volatile master

For a pure recolor:

1. Open the verified Volatile color texture.
2. Keep the separate alpha unchanged.
3. Preserve accessory regions on their own layer or mask.
4. Lock a hidden copy of the source.

For a retexture:

1. Open the color texture, alpha, and a UV or color-location reference if available.
2. Place strand texture sections along the mapped hair direction.
3. Avoid stretching one narrow strand sample across a broad UV island.
4. Match seams and mirrored regions.
5. Preview on the mesh before mass-producing colors.
6. Grade the completed texture into a verified Volatile base.

The color curve is the last palette step. It does not replace mapping work.

## 6. Generate target colors in GIMP

1. Save an XCF with the original and Volatile layers intact.
2. Duplicate the Volatile layer for each required color.
3. Select one target layer.
4. Choose Colors, Curves.
5. Import or select the named IaKoa preset.
6. Apply it once.
7. Check the result against the palette swatch.
8. Mask accessories or streaks separately.
9. Export one target at a time to the exact project filename.

For gradients or multicolor hair, apply different curves to separate layers and blend them with layer masks. Do not colorize the alpha mask. Keep the transition wide enough to survive mipmap reduction.

## 7. Repeat across required age textures

A curve produces a color transform, not an age set. Apply the target to every unique color texture needed by the package.

If all ages share identical mapping, one output may be reusable. If age meshes use different UV layouts, each one needs its own aligned texture. Confirm visually. Do not assume matching dimensions mean matching UVs.

## 8. Refresh and import with Body Shop

After saving a project texture:

1. Switch to Body Shop.
2. Refresh the preview.
3. Inspect all available ages and rotations.
4. Fix texture, alpha, or mapping problems before import.
5. Enter a meaningful tooltip.
6. Import to game.

Body Shop creates a new package in SavedSims or the location used by the installed edition. Move the finished package into the controlled test Downloads folder only after identifying it.

## 9. Alternative SimPE import

Direct SimPE import provides more control over texture format and mipmaps, but also makes it easier to damage package links.

- Prefer Build DXT or the appropriate established import method rather than replacing only the largest mip level.
- Preserve the existing TXTR dimensions and format unless there is a tested reason to change them.
- Use an alpha-capable format when the material relies on graduated opacity.
- Inspect all generated mip levels for bright or dark halos.
- Commit each resource and save to a new file.

Body Shop commonly produces DXT3 hair textures. DXT5 can represent graduated alpha differently and may be chosen by experienced creators for suitable assets. The right choice depends on the source material and target renderer. "Higher number" does not automatically mean a better result.

## 10. Bin, familify, and set elder behavior

Do this after the visuals are stable.

For standard natural recolors:

1. Assign the intended standard hairtone to the XHTN proxy.
2. Assign the same hairtone to each non-elder GZPS.
3. Assign grey to the elder GZPS when elders should grey.
4. Give all colors of the same hairstyle one family key.
5. Decide which package owns the shared elder grey, if there is only one.
6. Check eyebrow and facial-hair behavior if the chosen tool edits those links.
7. Keep the source masters and tool backups.

Pooklet color names do not dictate the bin. The creator chooses which natural colors represent black, brown, blond, and red. Unnatural colors normally remain custom unless a deliberate genetic system says otherwise.

## 11. Structural validation

Inspect the finished package or set for:

- Expected GZPS entry for every intended age
- Matching 3IDR instance for each age entry
- Valid CRES and SHPE references to the installed mesh
- TXMT names that point to the intended TXTR
- Correct subset names and count
- Consistent XHTN proxy and GZPS hairtone values
- Shared family key across the intended color set
- Exactly the intended elder-grey layout
- Expected texture dimensions, format, and complete mip chain
- No unrelated resources copied from another mesh

This catches structural mistakes. It does not validate drawing order, animation, lighting, or inheritance in a running game.

## 12. Gameplay test matrix

| Test | What it proves |
| --- | --- |
| Every age appears in CAS | Age flags and basic resource links work |
| Rotate under and behind the hair | Inside faces and alpha layers draw correctly |
| Animate head and upper body | Bone assignments and clipping are acceptable |
| Change among family colors | Family keys preserve the style |
| Age an adult to elder | Elder mesh, texture, and family routing work |
| View close and far away | Mipmaps and compression do not create halos |
| Test several lighting conditions | Painted contrast and material response remain readable |
| Test from a clean folder | No undeclared mesh or recolor dependency is masking a problem |

Record Body Shop and in-game results separately. A clean package structure plus a good Body Shop preview is not the same as in-game validation.

## 13. Release checklist

- State whether the mesh is included.
- Name the mesh creator and source.
- List supported ages and gender.
- List exact Pooklet colors.
- State which colors are binned and which are custom.
- Explain the family and elder-grey arrangement.
- Mention texture system and version when known, such as Pooklet v3 or v3.5.
- Credit IaKoa for the GIMP curve conversion and Pooklet plus the underlying texture contributors.
- Include an in-game swatch made under neutral lighting.
- Keep editable XCF files and unbinned masters outside the release archive.
