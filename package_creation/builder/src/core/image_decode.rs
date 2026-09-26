//! Static image decoding with explicit limits and sRGB color management.
use anyhow::{ensure, Context, Result};
use image::{DynamicImage, ImageDecoder, ImageFormat, ImageReader, RgbaImage};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::io::Cursor;
#[derive(Clone, Copy)]
pub struct DecodeLimits {
    pub bytes: usize,
    pub pixels: u64,
}
impl DecodeLimits {
    pub const INPUT: Self = Self {
        bytes: 32 * 1024 * 1024,
        pixels: 32_000_000,
    };
    pub const OUTPUT: Self = Self {
        bytes: 128 * 1024 * 1024,
        pixels: 64_000_000,
    };
    pub fn dimensions(self, w: u32, h: u32) -> Result<()> {
        ensure!(
            w > 0 && h > 0 && w <= 8192 && h <= 8192 && w as u64 * h as u64 <= self.pixels,
            "Choose an image no larger than 8192 pixels per side and {} megapixels",
            self.pixels / 1_000_000
        );
        Ok(())
    }
}
fn hash(b: impl AsRef<[u8]>) -> String {
    format!("{:x}", Sha256::digest(b.as_ref()))
}
pub fn decode(bytes: &[u8], limits: DecodeLimits) -> Result<(RgbaImage, Value)> {
    ensure!(
        !bytes.is_empty() && bytes.len() <= limits.bytes,
        "Choose one image no larger than {} MiB",
        limits.bytes / (1024 * 1024)
    );
    let format = image::guess_format(bytes).context("Choose a PNG, JPEG or WebP image")?;
    ensure!(
        [ImageFormat::Png, ImageFormat::Jpeg, ImageFormat::WebP].contains(&format),
        "Choose a static PNG, JPEG or WebP image"
    );
    // Check container animation before decoding a first frame accidentally.
    if format == ImageFormat::Png {
        let d = image::codecs::png::PngDecoder::new(Cursor::new(bytes))?;
        ensure!(
            !d.is_apng()?,
            "Animated PNGs are unsupported. Export one frame as a static PNG"
        );
    } else if format == ImageFormat::WebP {
        let d = image::codecs::webp::WebPDecoder::new(Cursor::new(bytes))?;
        ensure!(
            !d.has_animation(),
            "Animated WebP images are unsupported. Export one frame as a static image"
        );
    }
    let mut reader = ImageReader::with_format(Cursor::new(bytes), format);
    let mut allocation = image::Limits::default();
    allocation.max_image_width = Some(8192);
    allocation.max_image_height = Some(8192);
    allocation.max_alloc = Some(320 * 1024 * 1024);
    reader.limits(allocation);
    let mut decoder = reader.into_decoder()?;
    let (w, h) = decoder.dimensions();
    limits.dimensions(w, h)?;
    let orientation = decoder
        .orientation()
        .context("The image orientation metadata is damaged")?;
    let profile = decoder
        .icc_profile()
        .context("The image color profile is damaged. Export an sRGB image")?;
    let srgb_tag = if format == ImageFormat::Png {
        validate_png_color_tags(bytes, profile.is_some())?
    } else {
        false
    };
    ensure!(
        profile.as_ref().is_none_or(|p| p.len() <= 4 * 1024 * 1024),
        "Image color profile exceeds 4 MiB. Export an sRGB image"
    );
    let mut image = DynamicImage::from_decoder(decoder)?;
    image.apply_orientation(orientation);
    let mut rgba = image.into_rgba8();
    let mut handling = if srgb_tag {
        "sRGB-tagged image"
    } else {
        "Untagged image treated as sRGB"
    };
    if let Some(icc) = profile {
        let source = moxcms::ColorProfile::new_from_slice(&icc)
            .context("Unsupported image color profile. Export a PNG or JPEG in sRGB")?;
        ensure!(
            source.color_space == moxcms::DataColorSpace::Rgb,
            "This image profile is not RGB. Export an sRGB PNG or JPEG"
        );
        let transform = source
            .create_transform_8bit(
                moxcms::Layout::Rgba,
                &moxcms::ColorProfile::new_srgb(),
                moxcms::Layout::Rgba,
                moxcms::TransformOptions::default(),
            )
            .context("Cannot convert this color profile. Export an sRGB image")?;
        let row = rgba.width() as usize * 4;
        let mut output = vec![0; row];
        for input in rgba.as_mut().chunks_exact_mut(row) {
            transform
                .transform(input, &mut output)
                .context("Image profile conversion failed")?;
            for (a, b) in input.chunks_exact_mut(4).zip(output.chunks_exact(4)) {
                a[..3].copy_from_slice(&b[..3]);
            }
        }
        handling = "Embedded RGB profile converted to sRGB";
    }
    let info = json!({"version":1,"width":rgba.width(),"height":rgba.height(),"format":format!("{format:?}"),
        "alpha":rgba.pixels().any(|p|p[3]!=255),"profile":handling,"orientation":format!("{orientation:?}"),"sha256":hash(bytes)});
    Ok((rgba, info))
}

// ICC conversion cannot account for an overriding HDR cICP tag or a separate
// gamma/chromaticity definition. Refuse those definitions instead of silently
// decoding their channel values as sRGB.
fn validate_png_color_tags(bytes: &[u8], icc: bool) -> Result<bool> {
    let mut offset = 8usize;
    let (mut srgb, mut simple_profile) = (false, false);
    while offset + 12 <= bytes.len() {
        let length = u32::from_be_bytes(bytes[offset..offset + 4].try_into().unwrap()) as usize;
        let end = offset
            .checked_add(length + 12)
            .context("Invalid PNG metadata")?;
        ensure!(end <= bytes.len(), "Invalid PNG metadata");
        let tag = &bytes[offset + 4..offset + 8];
        if tag == b"sRGB" {
            srgb = true;
        }
        if tag == b"gAMA" || tag == b"cHRM" {
            simple_profile = true;
        }
        if tag == b"cICP" {
            ensure!(bytes[offset + 8..end - 4] == [1, 13, 0, 1],
                "This PNG declares an unsupported color space or HDR transfer. Export an sRGB image");
            srgb = true;
        }
        offset = end;
        if tag == b"IEND" {
            break;
        }
    }
    ensure!(!simple_profile || srgb || icc,
        "This PNG defines color using gamma or chromaticity tags without a supported profile. Export it as sRGB with an embedded RGB ICC profile");
    Ok(srgb)
}
