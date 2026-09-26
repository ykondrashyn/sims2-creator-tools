# How hair works in The Sims 2

Research status: 2026-09-05

The website now has a [Hair recolors package builder](../package_creation/hair/README.md), including the installed Swirl template, sRGB curve processing, previews and per-color package ZIPs. The research below describes the underlying formats and color workflow.

This folder explains The Sims 2 hair from two connected viewpoints:

1. How the game assembles a hair from catalog metadata, a mesh, materials, color textures, and alpha masks.
2. How IaKoa's Pooklet Curves presets recolor a prepared Volatile texture in GIMP.

The key result is that a TS2 hair is not a PNG with a color attached to it. It is a set of linked resources in a DBPF package. The image contributes color and painted strand detail, the alpha controls which parts of a mapped surface are visible, the mesh supplies shape and movement, the material controls rendering, and the hairtone metadata controls catalog and genetic behavior.

Likewise, a Pooklet curve is not a single RGB swatch. It is an input-sensitive tone transform. It maps the shadows, midtones, and highlights of a specific Volatile base to different RGB outputs. That is why the same preset applied to an arbitrary source can produce the wrong color or crushed detail.

## Read in this order

- [How TS2 hair works](how-ts2-hair-works.md)
- [Pooklet Curves and the RGB question](pooklet-curves.md)
- [Practical recolor workflow](practical-workflow.md)
- [Sources and evidence](sources.md)

## The whole system at a glance

This is a conceptual map, not a byte-level serialization diagram:

```text
GZPS and XHTN
CAS visibility, age, gender, hairtone, family, genetics
             |
             v
           3IDR
       reference bundle
        /            \
       v              v
     CRES           TXMT
       |          material rules
       v              |
     SHPE             v
       |             TXTR
       v       color image and mipmaps
     GMND
       |
       v
     GMDC
geometry, UV coordinates, normals, bones
```

In practice, the recolor package normally contains the CAS and texture side. A separate custom mesh package often contains the CRES, SHPE, GMND, and GMDC side. The recolor still has to point to the exact mesh and its subset names.

## Four changes that are often confused

| Change | What changes | What it cannot do |
| --- | --- | --- |
| Recolor | RGB values while keeping the existing strand pattern | Move highlights, repair a bad texture, or change geometry |
| Retexture | The painted strand pattern and highlight placement | Change the physical volume outside the existing mapped mesh |
| Alpha edit | Which mapped areas are visible | Add geometry where no polygon exists |
| Mesh edit | Geometry, UVs, normals, bones, or subset layout | Automatically create compatible recolors and metadata |

"Pookleted" often means both retextured with a Pooklet texture and recolored into Pooklet colors. It does not inherently mean the hair was correctly binned, familified, compressed, or tested in game.

## Evidence boundary

The documentation is based on the linked 2014 IaKoa post, IaKoa's related GIMP tutorials, the official Body Shop manual, GIMP documentation, and long-running Mod The Sims technical references. The concepts are stable, but exact menus and preset compatibility are version-sensitive. IaKoa made the presets with GIMP 2.8.10. Modern GIMP 2.10 and 3.x have additional precision and linear versus perceptual processing choices.

No game package was created or tested as part of this research. The output is documentation, not gameplay validation.
