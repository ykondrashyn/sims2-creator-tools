//! Local preparation only. No model inference, network or credential handling.
use crate::core::image_decode::{decode, DecodeLimits};
use anyhow::{ensure, Context, Result};
use image::{codecs::png::PngEncoder, imageops, GrayImage, ImageEncoder, RgbaImage};
use serde_json::{json, Value};

#[derive(Default)]
pub struct Session {
    alpha: Option<GrayImage>,
    width: u32,
    height: u32,
    result: Option<RgbaImage>,
}
fn png(data: &[u8], width: u32, height: u32, color: image::ExtendedColorType) -> Result<Vec<u8>> {
    let mut output = Vec::new();
    PngEncoder::new(&mut output).write_image(data, width, height, color)?;
    ensure!(
        output.len() <= DecodeLimits::OUTPUT.bytes,
        "Prepared PNG exceeds 128 MiB"
    );
    Ok(output)
}
impl Session {
    pub fn input(&mut self, bytes: Vec<u8>, upload: bool) -> Result<(Value, Vec<u8>)> {
        *self = Self::default();
        let (image, info) = decode(&bytes, DecodeLimits::INPUT)?;
        drop(bytes);
        self.width = image.width();
        self.height = image.height();
        if info["alpha"] == true {
            self.alpha = Some(GrayImage::from_fn(self.width, self.height, |x, y| {
                image::Luma([image[(x, y)][3]])
            }));
        }
        let output = if upload {
            // Copy RGB directly, including color under zero alpha. Never composite.
            let mut rgb = Vec::with_capacity(image.width() as usize * image.height() as usize * 3);
            for pixel in image.pixels() {
                rgb.extend_from_slice(&pixel.0[..3]);
            }
            drop(image);
            png(
                &rgb,
                self.width,
                self.height,
                image::ExtendedColorType::Rgb8,
            )?
        } else {
            Vec::new()
        };
        Ok((info, output))
    }
    pub fn output(&mut self, bytes: Vec<u8>, preserve: bool) -> Result<(Value, Vec<u8>)> {
        self.result = None;
        let (mut image, mut info) = decode(&bytes, DecodeLimits::OUTPUT)?;
        drop(bytes);
        let mut restored = false;
        if preserve {
            if let Some(alpha) = &self.alpha {
                if u64::from(self.width) * u64::from(image.height())
                    != u64::from(self.height) * u64::from(image.width())
                {
                    info["alpha_error"] = json!("The output proportions changed. Original transparency cannot be restored without distortion. The model result is still downloadable.");
                } else {
                    let mask = imageops::resize(
                        alpha,
                        image.width(),
                        image.height(),
                        imageops::FilterType::Lanczos3,
                    );
                    for (pixel, alpha) in image.pixels_mut().zip(mask.pixels()) {
                        pixel[3] = alpha[0];
                    }
                    restored = true;
                }
            }
        }
        info["alpha_restored"] = json!(restored);
        self.result = Some(image);
        let png = if restored { self.png()? } else { Vec::new() };
        Ok((info, png))
    }
    pub fn png(&self) -> Result<Vec<u8>> {
        let image = self
            .result
            .as_ref()
            .context("Inspect the model output before converting it to PNG")?;
        png(
            image.as_raw(),
            image.width(),
            image.height(),
            image::ExtendedColorType::Rgba8,
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use image::{DynamicImage, ImageFormat, Rgba};
    use std::io::Cursor;
    fn encoded(image: &RgbaImage, format: ImageFormat) -> Vec<u8> {
        let mut bytes = Cursor::new(Vec::new());
        DynamicImage::ImageRgba8(image.clone())
            .write_to(&mut bytes, format)
            .unwrap();
        bytes.into_inner()
    }
    #[test]
    fn transparent_rgb_and_alpha_are_independent() {
        let source = RgbaImage::from_fn(8, 6, |x, y| Rgba([x as u8 * 31, 83, 225, y as u8 * 51]));
        let mut s = Session::default();
        let (_, upload) = s.input(encoded(&source, ImageFormat::Png), true).unwrap();
        let rgb = image::load_from_memory(&upload).unwrap().into_rgba8();
        for (a, b) in rgb.pixels().zip(source.pixels()) {
            assert_eq!(a.0[..3], b.0[..3]);
            assert_eq!(a[3], 255);
        }
        let output = RgbaImage::from_pixel(16, 12, Rgba([97, 122, 201, 255]));
        let (info, result) = s.output(encoded(&output, ImageFormat::Png), true).unwrap();
        assert_eq!(info["alpha_restored"], true);
        let result = image::load_from_memory(&result).unwrap().into_rgba8();
        let expected = imageops::resize(
            s.alpha.as_ref().unwrap(),
            16,
            12,
            imageops::FilterType::Lanczos3,
        );
        for (p, a) in result.pixels().zip(expected.pixels()) {
            assert_eq!(p.0[..3], [97, 122, 201]);
            assert_eq!(p[3], a[0]);
        }
    }
    #[test]
    fn conversion_preserves_decoded_webp_pixels() {
        let source = RgbaImage::from_fn(11, 17, |x, y| Rgba([x as u8 * 19, y as u8 * 13, 42, 255]));
        let bytes = encoded(&source, ImageFormat::WebP);
        let expected = image::load_from_memory(&bytes).unwrap().into_rgba8();
        let mut s = Session::default();
        s.output(bytes, false).unwrap();
        let result = image::load_from_memory(&s.png().unwrap())
            .unwrap()
            .into_rgba8();
        assert_eq!(result, expected);
    }
    #[test]
    fn proportion_failure_keeps_raw_result_convertible() {
        let mut s = Session::default();
        s.input(
            encoded(
                &RgbaImage::from_pixel(3, 4, Rgba([1, 2, 3, 0])),
                ImageFormat::Png,
            ),
            false,
        )
        .unwrap();
        let (info, png) = s
            .output(encoded(&RgbaImage::new(8, 8), ImageFormat::Png), true)
            .unwrap();
        assert!(info["alpha_error"].is_string());
        assert!(png.is_empty());
        assert!(!s.png().unwrap().is_empty());
        assert!(s.input(b"bad".to_vec(), true).is_err());
        assert!(s.png().is_err());
    }
    #[test]
    fn limits_apply_before_decode() {
        let mut bytes = encoded(&RgbaImage::new(1, 1), ImageFormat::Png);
        bytes[16..20].copy_from_slice(&8193u32.to_be_bytes());
        assert!(Session::default().input(bytes, true).is_err());
        assert!(DecodeLimits::OUTPUT.dimensions(8001, 8000).is_err());
    }
}
