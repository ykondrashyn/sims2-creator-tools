//! Pixel encoding shared by previews and package tools.
use anyhow::Result;
use image::{DynamicImage, ImageFormat, RgbaImage};
use std::io::Cursor;
pub fn png(img: &RgbaImage) -> Result<Vec<u8>> {
    let mut out = Cursor::new(Vec::new());
    DynamicImage::ImageRgba8(img.clone()).write_to(&mut out, ImageFormat::Png)?;
    Ok(out.into_inner())
}
