# Pooklet Curves in GIMP

## 1. What IaKoa actually provided

IaKoa's 14 June 2014 post converts the effect of Pooklet's Photoshop color actions into saved GIMP Curves presets. The author explicitly calls them curves rather than actions and says they were made in GIMP 2.8.10.

The ordinary presets expect Pooklet's `Volatile` color as their input base. Files named `PookletSpecial` convert another named base into Volatile, such as Primer to Volatile. The download was described as containing natural and unnatural colors, swatches, text instructions, and a separate texture download.

This distinction matters:

- A Photoshop action is a sequence of editing commands. It can add layers, change blend modes, adjust channels, flatten, and save.
- A GIMP Curves preset stores tone-mapping functions for Value, Red, Green, Blue, and possibly Alpha.
- A curve cannot reproduce a Photoshop action that depends on spatial masks, multiple layers, or other operations unless the final effect can be approximated as channel mappings.

## 2. What a curve is

For an 8-bit channel, the horizontal axis is an input value from 0 to 255 and the vertical axis is the replacement output value from 0 to 255.

With an identity curve:

```text
output = input
```

Every value stays unchanged. When the curve is bent, a selected input range is remapped. In simplified channel notation:

```text
Rout = fR(Rin)
Gout = fG(Gin)
Bout = fB(Bin)
```

The Value curve can additionally change composite brightness and contrast. An Alpha curve can change opacity if the active layer has alpha. The official GIMP documentation describes this as mapping input tones to output tones and provides separate Value, Red, Green, Blue, and Alpha channels.

An old GIMP 2.8 preset can store a 256-value lookup table for each channel. I inspected an independently published 2012 Pooklet curve archive from Mod The Sims as format evidence. All 42 curve files declare 256 samples per channel. The inspected Dynamite preset contains identity Value and Alpha tables plus non-linear Red, Green, and Blue tables. It is not IaKoa's later archive, so its exact values must not be attributed to IaKoa. It does demonstrate the underlying mechanism used by this class of preset.

## 3. Why one curve creates colored shadows and highlights

Suppose a Volatile strand has dark roots, a midtone body, and pale highlights. Those areas contain different input values. A Pooklet curve can send them to different output colors:

```text
dark Volatile pixel      -> target-colored shadow
middle Volatile pixel    -> target-colored midtone
bright Volatile pixel    -> target-colored highlight
```

The mapping can be different for each RGB channel. A red target may add proportionally more red to the highlights while keeping the shadows warmer and darker. A black target may compress much of the range downward while preserving enough separation for strand detail. A blond target may lift the midtones without turning every highlight into pure white.

The curve has no understanding of hair, roots, or strands. It only sees channel values. The source texture already encodes the spatial detail. The curve preserves or reshapes that detail because pixels with different source values receive different outputs.

## 4. Why Volatile is required

The preset is calibrated as a transform from a particular input distribution to a target look. It is better understood as:

```text
target = preset(Volatile base)
```

It is not reliably:

```text
target = preset(any image that looks roughly blond)
```

If another base has darker shadows, clipped highlights, more saturation, or different RGB relationships, the preset samples different parts of its lookup tables. Results can become too contrasty, too shiny, muddy, or simply the wrong hue.

Two images can look similarly light to a person while having different channel values. A warm blond and a neutral gray may have comparable brightness, but the warm blond has unequal R, G, and B values. Since a channel curve receives those values separately, it produces different outputs.

IaKoa's separate "curve-ready" tutorial is often misunderstood here. That tutorial explains how to normalize an arbitrary texture for curve sets that use a gray base. It suggests desaturation and combinations of:

- A `#808080` layer under the desaturated texture with Soft Light
- A dark-value floor and light-value ceiling using lighten-only and darken-only modes
- Reduced contrast

IaKoa's GIMP index explicitly separates gray-base color curves from skin and hair curves that need a specific non-gray base. For the Pooklet hair set, follow the linked Pooklet post and use Volatile. Do not substitute 50 percent gray merely because another curve tutorial uses it.

## 5. The RGB and PNG misconception

The statement "you cannot just change the RGB color of the PNG" is too absolute. You can edit the RGB values, and Curves itself is an RGB edit. The real limitation is the operation you choose.

### Flat replacement

If every visible pixel is assigned one RGB value:

```text
Rout, Gout, Bout = constant target color
```

all painted strand contrast disappears. The result is a flat silhouette with no roots, highlights, or fine texture.

### Simple tint or multiply

A multiply-style tint roughly scales each channel:

```text
Rout = Rin * tintR
Gout = Gin * tintG
Bout = Bin * tintB
```

This retains some variation, but it can only scale what is already there. It often makes shadows muddy and cannot independently design the target's shadows, midtones, and highlights. Other blend modes have different limitations.

### Hue shift or colorize

Hue and saturation changes can preserve brightness, but they tend to impose one hue relationship across the whole tonal range. Realistic dark hair, red hair, and blond hair usually need different color balances in shadows and highlights. Clipping can also erase detail at 0 or 255.

### Curves

Curves use a different output for each input level and can use a different function per channel. They can compress, expand, lift, or lower specific tonal ranges while changing hue. This is why they reproduce a named palette more consistently from a standardized base.

### Equivalent alternatives

Curves are convenient, not magical. The same output can be created by:

- Applying the same lookup tables in code
- Using a correctly authored gradient map
- Rebuilding the original layered Photoshop action
- Painting or grading the texture manually
- Copying a verified target texture that uses the same mapping and creator permissions

What is not sufficient is a single target hex color with no rule for how source tones map to target tones.

## 6. PNG is only an editing interchange in many workflows

A SimPE user may export and import PNG, while Body Shop projects commonly expose BMP files. The game package ultimately uses a TXTR resource, not a loose PNG. That TXTR includes game texture data and mipmap levels, and may use block compression.

Changing a PNG does not update any of these by itself:

- the mesh
- the UV map
- the TXMT material
- the alpha mask, unless alpha is intentionally edited
- the age and gender properties
- the color bin
- the family key
- genetic dominance
- package references

The image must be imported or packaged correctly, and the rest of the resource graph must remain valid.

## 7. Alpha safety while recoloring

Hair color curves normally belong on the color texture, not on the separate black-and-white alpha mask. Recoloring the mask can change opacity and the silhouette.

IaKoa notes that the curve works on a layer that already contains alpha. This means GIMP can map RGB while retaining transparent areas. It does not mean the hair's alpha mask should be color graded.

Keep in mind that fully transparent pixels still have hidden RGB values. Resampling, mipmap generation, and block compression can mix those hidden colors into edge pixels. A pale fringe around dark hair can therefore come from bad edge RGB even when the main alpha looks correct.

## 8. GIMP version and color-space caveats

The original presets were made in GIMP 2.8.10. IaKoa said older 2.x versions should work but had not tested them. That statement does not establish compatibility with GIMP 2.10 or 3.x.

GIMP 2.10 introduced linear-light processing choices and modern versions let Curves operate in different tone-reproduction modes. A legacy 8-bit preset can therefore look different depending on:

- GIMP version
- image bit depth
- linear versus non-linear or perceptual processing
- the source color profile
- whether an imported preset format was converted

For faithful matching, keep a copy of the supplied swatch, use the exact Volatile source, record GIMP version and image precision, and compare sampled shadows, midtones, and highlights. Do not judge only a single bright preview in Body Shop.

## 9. Installing and applying the preset

IaKoa's 2014 Windows instruction was to copy the files into:

```text
C:/USER/.gimp-2.x/curves
```

That is a historical GIMP 2.x profile path. A safer version-independent approach is:

1. Open the Volatile color texture in GIMP.
2. Choose Colors, then Curves.
3. Open the preset menu.
4. Choose Import Settings from File.
5. Select one curve file.
6. Preview the result against the supplied Pooklet swatch.
7. Save it into the quick preset list if the current GIMP version accepts it.

IaKoa's usage tutorial suggests either exporting a recolored texture and undoing back to Volatile, or duplicating the Volatile layer once per target color and applying a different curve to each duplicate. The layer method is safer because the unchanged base remains in the XCF.

## 10. Recommended non-destructive layer layout

```text
accessories and details, preserved or recolored separately
target color output layer
Volatile working copy
original imported texture, hidden and locked
alpha reference, hidden or used only as a mask guide
```

Save the editable master as XCF. Export the exact format expected by the Body Shop project or SimPE only after checking dimensions, alpha, and color profile.

## 11. Diagnostic checklist for a wrong result

1. Confirm the input is the actual Volatile version of this texture, not another blond.
2. Confirm the preset is a Pooklet target curve, not a `PookletSpecial` conversion curve.
3. Compare the histogram and visual range with the supplied base texture.
4. Check that pure black or white clipping was not introduced before the curve.
5. Apply the curve only once.
6. Check image precision and Curves processing mode.
7. Confirm accessories were not graded with the hair.
8. Confirm the separate alpha was not modified.
9. Inspect transparent-edge RGB if a halo appears.
10. Test the imported TXTR at multiple camera distances in game.

If the base is different, normalize or rebuild it into Volatile first. Repeatedly applying the target curve is not a substitute because each pass feeds the already transformed output back through an input-specific mapping.
