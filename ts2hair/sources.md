# Sources and evidence notes

Accessed 2026-09-05 unless noted otherwise.

## Starting source and related IaKoa material

1. [IaKoa, Pooklet for GIMP](https://iakoasims.tumblr.com/post/88756518223/pooklet-for-gimp), 14 June 2014

   Establishes that the download contains GIMP Curves rather than actions, ordinary presets use Volatile as the base, `PookletSpecial` files convert other colors to Volatile, and the presets were made in GIMP 2.8.10.

2. [IaKoa, How to use curves](https://iakoasims.tumblr.com/post/88757269148/how-to-use-curves), 14 June 2014

   Shows the Colors, Curves workflow, choosing a named preset, undoing back to Volatile between colors, duplicating a Volatile layer for multiple outputs, and applying different curves to alpha-bearing layers for gradients.

3. [IaKoa, How to make your texture curve-ready](https://iakoasims.tumblr.com/post/91216149594/how-to-make-your-texture-curve-ready), 9 July 2014

   Describes desaturation, a `#808080` Soft Light method, dark and light range clamping, and contrast reduction. This is useful for gray-base curve sets. It must not override the Pooklet post's explicit Volatile requirement.

4. [IaKoa, GIMP index](https://iakoasims.tumblr.com/GIMP)

   Separates color curves that use a gray base from skin and hair curves that use a specific non-gray base. The page lists the Pooklet curves in the latter group.

### Download availability note

The IaKoa post still exposes Box links through the web page. The Box curve-share page did not provide a downloadable artifact to the non-JavaScript automated check used for this research. I did not copy or redistribute the IaKoa archive. Use the creator's link in a normal browser and respect the included terms.

## GIMP behavior

5. [GIMP 2.8 manual, Curves](https://docs.gimp.org/2.8/en/gimp-tool-curves.html)

   Documents importing settings, the Value, Red, Green, Blue, and Alpha channels, and the 0 to 255 input-to-output mapping used by the version contemporary with IaKoa's presets.

6. [Current GIMP manual, Curves](https://docs.gimp.org/en_GB/gimp-tool-curves.html)

   Confirms the same core tone-mapping model and documents modern linear and perceptual choices.

7. [GIMP 2.10 release notes](https://www.gimp.org/release-notes/gimp-2.10.html)

   Documents the addition of linear-light workflow options and the ability to run Levels and Curves in linear or perceptual modes. This is why exact legacy-preset output should be checked on modern GIMP.

## Official game workflow

8. [The Sims 2 Body Shop manual](https://ia903104.us.archive.org/29/items/simsgameguides/TS2BodyShopManual.pdf), pages 10 to 13

   Documents exporting texture sets to an external graphics editor, refreshing the preview, importing to a game package, preserving project filenames and locations, and complete hair sets across toddler, child, teen, adult, and elder ages. It also states that hair sets are male or female.

## Hair and DBPF architecture

9. [Mod The Sims, Hair: Basics and Beyond](https://modthesims.info/article.php?t=139819)

   Describes the CRES, SHPE, GMND, and GMDC mesh resources, age-specific linking, adult mesh reuse for elders, paired inside and outside hair layers, bone assignments, and the importance of alpha group ordering.

10. [Mod The Sims, Understanding the Scenegraph](https://modthesims.info/wiki.php?title=Tutorials%3AUnderstanding_the_Scenegraph)

    Explains CRES to SHPE, SHPE to GMND and TXMT, GMND to GMDC, and TXMT to TXTR relationships.

11. [Mod The Sims, GMDC format](https://modthesims.info/wiki.php?title=GMDC)

    Identifies GMDC as the geometry container and lists vertices, normals, UV coordinates, bone joints, and morphs among its data.

12. [Mod The Sims, 3IDR format](https://modthesims.info/wiki.php?title=3IDR)

    Describes the 3D ID Referencing File and its role linking Body Shop recolors to shape, material, and catalog resources.

13. [Mod The Sims, GZPS format](https://modthesims.info/wiki.php?title=GZPS)

    Lists age, gender, and standard hairtone identifiers for Property Sets.

14. [Mod The Sims, TXMT format](https://modthesims.info/wiki.php?title=TXMT)

    Describes TXMT as the Material Definition that controls how input textures are presented in game.

15. [Mod The Sims, Everything You Wanted to Know About Meshes](https://direct.modthesims.info/article.php?t=155772)

    Explains that hair alphas hide or show mapped regions and can visually cut an existing mesh without changing geometry.

## Binning, families, and genetics

16. [Mod The Sims, How to Bin Hair Recolours Using SimPE](https://db.modthesims.info/showthread.php?t=150742)

    Gives the standard black, brown, blond, red, and grey hairtone identifiers and the required XHTN proxy and GZPS hairtone edits.

17. [Mod The Sims, Quick Easy Hair Binner](https://db.modthesims.info/d/434016)

    Explains binning, geneticizing, familifying, shared grey behavior, and why source backups matter.

18. [Mod The Sims, Hair Texture Not Smooth](https://modthesims.info/showthread.php?t=652704)

    Community troubleshooting reference for DXT3 versus DXT5 alpha handling and common elder-grey layouts. Treat it as practice guidance rather than an engine specification.

## Independent curve-file format check

19. [Mod The Sims, Raon 21 in 6 GIMPified Pooklet colors](https://modthesims.info/d/467796/raon-21-in-6-gimpified-pooklet-colors.html), 31 January 2012

    This predates IaKoa's set and is not the same archive. Its author explains that the curves were extracted from a Volatile base to target Pooklet colors using Get RGB Curves.

For format forensics only, I downloaded the linked `GIMPcurves-pookletcolors.rar` to a temporary directory and inspected it locally.

```text
Size: 87435 bytes
SHA-256: 2c30e3c94bb7050004f67575afb85a4ee12ff2016479011fca0e181d38cfb651
Contents: readme plus 42 GIMP curve presets
Observed preset structure: Value, Red, Green, Blue, and Alpha channels
Observed data: 256 samples per channel in the inspected presets
```

The archive was not copied into this project. Its exact color values are not IaKoa evidence. It was used only to verify how contemporary GIMP Pooklet curve files represented channel lookup tables.

## Reliability and limitations

- The Body Shop manual and GIMP manuals are the strongest sources for their respective tools.
- The Sims 2 package format was reverse-engineered by the modding community, so Mod The Sims and SimsWiki documentation is the practical technical authority.
- Many TS2 tutorials describe old SimPE and GIMP interfaces. Resource roles are more durable than button positions.
- Forum troubleshooting is useful for edge cases but is not a formal engine specification.
- The IaKoa page says compatibility before GIMP 2.8.10 was expected but untested. It makes no claim about GIMP 2.10 or 3.x.
- This research did not include a live TS2 installation, Body Shop run, SimPE package edit, or in-game rendering test.
