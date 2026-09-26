# How The Sims 2 hair works

## 1. The correct mental model

The Sims 2 does not simulate individual hair strands. A hairstyle is rendered from ordinary game assets:

- A 3D mesh provides surfaces around the head.
- UV coordinates tell the renderer which part of a 2D texture belongs on each surface.
- A color texture paints strand lines, roots, shadows, highlights, and the overall hue.
- An alpha mask cuts the mapped surfaces into locks, wisps, and transparent edges.
- A material says how that texture and alpha should be rendered.
- CAS metadata says who can wear the hair and how the game categorizes and inherits its color.

The visual result is therefore a multiplication of several systems. A perfect texture cannot repair bad UV mapping, missing geometry, wrong bone weights, a broken material link, or incorrect alpha ordering.

## 2. Geometry and UV mapping

The mesh is stored in the GMDC, the Geometry Data Container. It can carry vertices, faces, normals, UV coordinates, bone assignments, and morph data. Hair meshes are usually split into named subsets such as a scalp-like `hair` group and one or more `hair_alpha` groups.

Many classic "alpha hairs" use layered cards, shells, or strips rather than modeling every lock. A strand pattern is painted into the color texture and a matching mask hides the empty area of each polygon. The apparent silhouette can therefore be much more detailed than the polygon outline.

TS2 normally renders only the front side of a face. Hair creators often need paired inside and outside faces so that locks remain visible from both directions. Layer order also matters because partially transparent surfaces are hard to sort correctly. Changing group order or opacity values can make an inner layer draw over an outer layer.

Long hair also needs sensible bone assignments. A rigid assignment can make the entire style move with the head. Multiple weights can let longer sections follow the neck or upper torso more smoothly.

### What the UV map changes

A texture is not laid over the final screenshot. It is sampled through the mesh's UV map. Two hairs can use the same bitmap but look different because:

- One mesh stretches a small texture region over a large surface.
- One mesh mirrors or overlaps UV islands.
- Highlight lines run across rather than along the modeled locks.
- Several alpha groups sample the same texture at different scales.

This is why retexturing often requires placing and warping strand sections to suit the specific hair. A global color curve cannot repair UV direction or highlight placement.

## 3. Color texture versus alpha mask

These are separate jobs.

### Color texture

The color texture contains the visible RGB data:

- dark root and occlusion areas
- midtone body color
- thin strand contrast
- broad painted highlights
- accessory colors if an accessory shares the map

The painted lighting is important in TS2. Much of the apparent strand depth comes from the image, not from geometry or physically based shading.

### Alpha mask

For an ordinary transparent hair material:

- White means visible or opaque.
- Black means hidden or transparent.
- Gray means partial opacity when the material and texture format preserve graduated alpha.

The alpha can shorten bangs, cut gaps between locks, create wispy ends, or hide an entire mapped panel. It cannot create a lock outside the existing polygons.

There is an important exception. The base subset named `hair` may use the special `SimSkin` material behavior, while alpha subsets normally use `SimStandardMaterial`. In a skin-pulling material, black areas can reveal the Sim's skin rather than ordinary transparency. Do not infer semantics from the alpha image alone. Inspect the TXMT material class and compare with a known-good hair that uses the same subset layout.

### Alpha blending versus alpha testing

Two common strategies exist:

- Alpha blending supports soft gray transitions, but overlapping layers can sort badly.
- Alpha testing treats pixels more like a cutout, which reduces sorting problems but loses soft wisps.

The choice lives in the TXMT, not in the PNG or BMP alone. A high quality gray mask cannot produce soft transparency if the material is configured only for a hard cutout.

## 4. The package resource graph

TS2 custom content is stored in DBPF `.package` containers. A hair can be split into a recolor package and a mesh package. Some downloads combine resources, but the logical roles remain separate.

### Common recolor-side resources

| Resource | Role |
| --- | --- |
| GZPS, Property Set | Age, gender, hairtone, visibility, subset overrides, and other CAS properties for an age or state |
| XHTN, Hairtone XML | Hair color identity, proxy, family, and genetic behavior |
| 3IDR, 3D ID Referencing File | Binds a Body Shop entry to mesh and material resources |
| TXMT, Material Definition | Shader and rendering rules, including texture name and alpha behavior |
| TXTR, Texture Image | Game texture data and its mipmap levels |
| BINX, Binary Index | Catalog indexing data in packages that use it |
| STR# or related text | Tooltip or catalog text where present |

The exact set varies by game version, source hair, and tool. Treat the links as the truth, not a checklist of filenames.

### Common mesh-side resources

| Resource | Role |
| --- | --- |
| CRES, Resource Node | Scenegraph root and skeleton-related references |
| SHPE, Shape | Names mesh subsets and associates them with materials |
| GMND, Geometric Node | Structural wrapper that links to geometry and describes subsets |
| GMDC, Geometry Data Container | The actual mesh data, including vertices, normals, UVs, and joints |

Older tutorials sometimes write `GDMC`. The resource's standard short name is `GMDC`.

### Simplified link path

For a given age entry, the GZPS and a matching 3IDR identify what should be used. The 3IDR points into the scenegraph and to recolor materials. On the mesh side the chain is effectively:

```text
CRES -> SHPE -> GMND -> GMDC
```

On the material side:

```text
TXMT -> TXTR
```

The TXMT names its texture in `stdMatBaseTextureName` and also carries a file-list reference. The 3IDR connects the recolor material to the relevant age and mesh. A mismatch anywhere can produce a missing hair, a fallback hair, an untextured part, or the wrong material on a subset.

## 5. Ages and genders

The official Body Shop manual describes a complete hair set as five texture sets:

- Toddler
- Child
- Teen
- Adult
- Elder

It also states that a hair set is male or female. Young Adult support was introduced with University and commonly shares the adult visual resources, but packages still use explicit age flags.

Custom hairs do not have to provide a unique mesh for every age. Elder Sims normally reuse the adult hair mesh while having their own catalog or texture state. A conversion may provide only some ages and let the others fall back to game hair. Always inspect the actual GZPS and 3IDR entries instead of assuming an "all ages" download contains distinct geometry for each age.

Body Shop project filenames encode age, gender, hair name, subset, and whether a file is an alpha. Common prefixes include `pf`, `pm`, `cf`, `cm`, `tf`, `tm`, `af`, and `am`. Exact filenames depend on the source package.

## 6. Color bins, families, and genetics

Pixel color and hairtone metadata are independent.

The standard hairtone identifiers are:

| Bin | Hairtone identifier prefix |
| --- | --- |
| Black | `00000001` |
| Brown | `00000002` |
| Blond | `00000003` |
| Red | `00000004` |
| Grey | `00000005` |

The rest of each standard identifier is zero. The XHTN `proxy` and the GZPS `hairtone` entries must agree for ordinary binning, except that the elder GZPS is normally grey.

### Binning

Binning places a recolor in the normal black, brown, blond, red, or grey catalog section. It can also allow appropriate colors to appear on generated townies, depending on the rest of the metadata. A brown image with a custom hairtone remains custom until its package metadata is changed.

### Familifying

A family key groups the matching black, brown, blond, red, and elder versions of one hairstyle. Correct families help the game retain the same style when a user switches color and when a Sim changes age. A shared family is not the same thing as all colors sharing one hairtone.

### Genetic value

The hairtone resource carries a numeric genetic value used for dominance. Standard game behavior treats black and brown as dominant over blond and red. Body Shop custom hair is not automatically a well-designed new genetic system. Changing genetics without consistently handling family, proxy, brows, age entries, and available styles can cause surprising inheritance or fallback behavior.

### Elder grey

Creators commonly choose one of several layouts:

- Each natural recolor carries its own grey.
- One recolor in the family carries the shared grey.
- A separate grey package carries the elder resources.
- Elders intentionally retain the non-grey color.

These layouts have different deletion risks. If the shared grey lives in the black package, deleting that black package can also remove the elder result for the whole family.

## 7. Hair systems and Pooklet terminology

A community "hair system" is a consistent choice of textures, colors, binning, family rules, and usually elder greys across many meshes. It is not a separate game subsystem.

Pooklet materials provide a texture style and a named color palette. The palette names such as Volatile, Dynamite, Depth Charge, and Incendiary are creator conventions. The game does not know those names unless a creator puts them in filenames or tooltips. The package still uses TS2's standard bins or custom hairtone identifiers.

## 8. Common failure modes and what they imply

| Symptom | Likely layer to inspect |
| --- | --- |
| Hair is missing or replaced by a default style | Missing mesh package, broken 3IDR, or wrong CRES and SHPE link |
| A part is solid, invisible, or skin-colored | TXMT material class, wrong texture assignment, or alpha semantics |
| Wisps draw through the front of the hair | Alpha group order, opacity ordering, or blend mode |
| Hair looks blurry at distance | Mipmap generation, texture compression, texture size, or game texture settings |
| Pale or dark fringe appears around cutout edges | RGB beneath transparent pixels, alpha resampling, or block compression |
| Adult works but another age does not | Missing age GZPS or 3IDR, fallback mesh, or wrong age texture |
| Brown pixels appear in the custom star bin | XHTN and GZPS were not binned |
| Changing color also changes style | Family keys do not match across recolors |
| Elder is bald or repeats several greys | Family and grey resource layout is incomplete or duplicated |
| Pooklet preset gives muddy or neon output | The source is not the expected Volatile base, or its tonal range differs |

## 9. What must be tested in game

Body Shop is useful for iteration, but it is not full validation. In-game testing should cover:

- Every included age and the intended gender
- Front, back, side, top, and underneath views
- Head turning and body animation
- Bright indoor, dark indoor, and outdoor lighting
- Close and distant camera levels to exercise mipmaps
- Alpha sorting against the face, ears, shoulders, and other hair layers
- Color-bin placement and color switching
- Aging to elder when family behavior matters
- A clean user folder or cache clear after package-link changes

Passing a package inspection proves only that resources and links look plausible. It does not prove that the renderer, CAS, or genetics behave correctly.
