//! Versioned compression shared by native and browser package creators.
use anyhow::{bail, ensure, Result};
use dbpf::internal_file::resource_collection::texture_resource::{TextureFormat, TextureResource};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Encoder {
    #[default]
    Directxtex,
    BodyshopDxt3,
    Bodyshop,
}
impl Encoder {
    pub fn from_job(job: &Value) -> Result<Self> {
        match job.get("texture_encoder") {
            None => Ok(Self::default()),
            Some(v) => serde_json::from_value(v.clone()).map_err(|_| {
                anyhow::anyhow!("Choose DirectXTex or Body Shop for texture compression")
            }),
        }
    }
    pub fn name(self) -> &'static str {
        match self {
            Self::Directxtex => "directxtex",
            Self::BodyshopDxt3 => "bodyshop_dxt3",
            Self::Bodyshop => "bodyshop",
        }
    }
    pub fn effective(self, format: TextureFormat) -> &'static str {
        match format {
            TextureFormat::DXT1 | TextureFormat::DXT3 | TextureFormat::DXT5
                if self == Self::Bodyshop =>
            {
                "bodyshop"
            }
            TextureFormat::DXT3 if self == Self::BodyshopDxt3 => "bodyshop_dxt3",
            TextureFormat::DXT1 | TextureFormat::DXT3 | TextureFormat::DXT5 => "directxtex",
            _ => "original_uncompressed",
        }
    }
    pub fn usage(self, formats: &[TextureFormat]) -> Value {
        let mut report = self.report();
        report["generated_formats"] = json!(formats
            .iter()
            .map(|f| format!("{f:?}"))
            .collect::<std::collections::BTreeSet<_>>());
        report["effective_encoders"] = json!(formats
            .iter()
            .map(|f| self.effective(*f))
            .collect::<std::collections::BTreeSet<_>>());
        report
    }
    pub fn report(self) -> Value {
        json!({"selected":self,"version":2,"directxtex_revision":"868198cb4bcbc4e359372e7ba38d7a6dda3a6afa","bodyshop_algorithm_version":if self == Self::Bodyshop { 2 } else { 1 },
            "routing":{"DXT1":self.effective(TextureFormat::DXT1),"DXT3":self.effective(TextureFormat::DXT3),"DXT5":self.effective(TextureFormat::DXT5)},"mipmaps":"complete","dithering":false})
    }
}

unsafe extern "C" {
    fn ts2_directxtex_block(format: u32, rgba: *const u8, output: *mut u8);
}

pub fn encode(
    format: TextureFormat,
    width: usize,
    height: usize,
    rgba: &[u8],
    encoder: Encoder,
) -> Result<Vec<u8>> {
    ensure!(
        width > 0 && height > 0 && width <= 8192 && height <= 8192,
        "Invalid texture encoding dimensions"
    );
    ensure!(
        width.checked_mul(height).and_then(|v| v.checked_mul(4)) == Some(rgba.len()),
        "Texture encoding needs exactly one RGBA8 image"
    );
    let size = format.compressed_size(width, height);
    let mut result = vec![0; size];
    if !matches!(
        format,
        TextureFormat::DXT1 | TextureFormat::DXT3 | TextureFormat::DXT5
    ) {
        format.compress(rgba, width, height, &mut result);
        return Ok(result);
    }
    let bytes = if format == TextureFormat::DXT1 { 8 } else { 16 };
    let mut block = [0; 64];
    for by in 0..height.div_ceil(4) {
        for bx in 0..width.div_ceil(4) {
            for y in 0..4 {
                for x in 0..4 {
                    let src =
                        (((by * 4 + y).min(height - 1)) * width + (bx * 4 + x).min(width - 1)) * 4;
                    let dst = (y * 4 + x) * 4;
                    block[dst..dst + 4].copy_from_slice(&rgba[src..src + 4]);
                }
            }
            let offset = (by * width.div_ceil(4) + bx) * bytes;
            let target = &mut result[offset..offset + bytes];
            if encoder == Encoder::Bodyshop {
                match format {
                    TextureFormat::DXT1 => {
                        target.copy_from_slice(&crate::bodyshop_dxt3::block_dxt1(&block)?)
                    }
                    TextureFormat::DXT3 => {
                        target.copy_from_slice(&crate::bodyshop_dxt3::block(&block)?)
                    }
                    TextureFormat::DXT5 => {
                        target.copy_from_slice(&crate::bodyshop_dxt3::block_dxt5(&block)?)
                    }
                    _ => unreachable!(),
                }
            } else if encoder == Encoder::BodyshopDxt3 && format == TextureFormat::DXT3 {
                target.copy_from_slice(&crate::bodyshop_dxt3::block(&block)?);
            } else {
                // All pointer lengths and the format are validated above.
                unsafe {
                    ts2_directxtex_block(format as u32, block.as_ptr(), target.as_mut_ptr());
                }
            }
        }
    }
    Ok(result)
}

pub fn recompress(
    texture: &mut TextureResource,
    format: TextureFormat,
    encoder: Encoder,
) -> Result<()> {
    *texture = texture.recompress_with_encoder(format, |format, width, height, rgba| {
        encode(format, width, height, rgba, encoder).map_err(|e| binrw::Error::AssertFail {
            pos: 0,
            message: e.to_string(),
        })
    })?;
    Ok(())
}

/// The same buffer operation is used by native/WASM codec acceptance tests.
pub fn dispatch(p: &Value) -> Result<Value> {
    let format = match p["format"].as_str() {
        Some("DXT1") => TextureFormat::DXT1,
        Some("DXT3") => TextureFormat::DXT3,
        Some("DXT5") => TextureFormat::DXT5,
        _ => bail!("Choose DXT1, DXT3 or DXT5"),
    };
    let encoder = Encoder::from_job(p)?;
    let input = crate::assets::read(p["asset"].as_str().unwrap_or("input"))?;
    let width = p["width"].as_u64().unwrap_or(0) as usize;
    let height = p["height"].as_u64().unwrap_or(0) as usize;
    let output = encode(format, width, height, &input, encoder)?;
    let size = output.len();
    crate::assets::write("output", output)?;
    Ok(
        json!({"bytes":size,"texture_encoding":encoder.report(),"effective_encoder":encoder.effective(format)}),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn versioned_bodyshop_routing_and_equal_endpoint_transparency() {
        assert_eq!(Encoder::from_job(&json!({})).unwrap(), Encoder::Directxtex);
        assert_eq!(
            Encoder::from_job(&json!({"texture_encoder":"bodyshop"})).unwrap(),
            Encoder::Bodyshop
        );
        assert_eq!(Encoder::Bodyshop.report()["bodyshop_algorithm_version"], 2);
        assert_eq!(
            Encoder::BodyshopDxt3.effective(TextureFormat::DXT5),
            "directxtex"
        );
        assert_eq!(
            Encoder::BodyshopDxt3.effective(TextureFormat::DXT3),
            "bodyshop_dxt3"
        );
        for format in [
            TextureFormat::DXT1,
            TextureFormat::DXT3,
            TextureFormat::DXT5,
        ] {
            assert_eq!(Encoder::Bodyshop.effective(format), "bodyshop");
        }
        let mut rgba = [0; 64];
        for (i, p) in rgba.chunks_exact_mut(4).enumerate() {
            p.copy_from_slice(&[73, 150, 201, if i % 2 == 0 { 127 } else { 128 }]);
        }
        let encoded = encode(TextureFormat::DXT1, 4, 4, &rgba, Encoder::Bodyshop).unwrap();
        assert_eq!(&encoded[..2], &encoded[2..4]);
        let mut decoded = [0; 64];
        TextureFormat::DXT1.decompress(&encoded, 4, 4, &mut decoded);
        for (i, p) in decoded.chunks_exact(4).enumerate() {
            assert_eq!(p[3], if i % 2 == 0 { 0 } else { 255 });
        }
    }
    #[test]
    fn partial_blocks_and_routing() {
        let pixel = [27, 88, 219, 127];
        for format in [
            TextureFormat::DXT1,
            TextureFormat::DXT3,
            TextureFormat::DXT5,
        ] {
            for encoder in [
                Encoder::Directxtex,
                Encoder::BodyshopDxt3,
                Encoder::Bodyshop,
            ] {
                let expected = encode(format, 4, 4, &pixel.repeat(16), encoder).unwrap();
                for (w, h) in [(1, 1), (2, 2), (1, 4), (4, 1), (3, 3)] {
                    assert_eq!(
                        encode(format, w, h, &pixel.repeat(w * h), encoder).unwrap(),
                        expected
                    );
                }
                if format != TextureFormat::DXT3 && encoder != Encoder::Bodyshop {
                    assert_eq!(
                        expected,
                        encode(format, 4, 4, &pixel.repeat(16), Encoder::Directxtex).unwrap()
                    );
                }
            }
        }
        assert!(encode(TextureFormat::DXT3, 0, 1, &[], Encoder::Directxtex).is_err());
        assert!(encode(TextureFormat::DXT3, 1, 1, &[0; 3], Encoder::Directxtex).is_err());
        assert!(Encoder::from_job(&json!({"texture_encoder":"unknown"})).is_err());
    }
    #[test]
    fn varied_partial_blocks_and_dxt1_cutoff() {
        for encoder in [
            Encoder::Directxtex,
            Encoder::BodyshopDxt3,
            Encoder::Bodyshop,
        ] {
            for format in [
                TextureFormat::DXT1,
                TextureFormat::DXT3,
                TextureFormat::DXT5,
            ] {
                let small = [
                    7, 33, 240, 0, 250, 170, 15, 127, 29, 188, 90, 128, 200, 30, 240, 255,
                ];
                let mut expanded = [0; 64];
                for y in 0..4 {
                    for x in 0..4 {
                        let i = (y.min(1) * 2 + x.min(1)) * 4;
                        expanded[(y * 4 + x) * 4..(y * 4 + x) * 4 + 4]
                            .copy_from_slice(&small[i..i + 4]);
                    }
                }
                assert_eq!(
                    encode(format, 2, 2, &small, encoder).unwrap(),
                    encode(format, 4, 4, &expanded, encoder).unwrap()
                );
            }
            for alpha in [0u8, 127, 128, 255] {
                let compressed = encode(
                    TextureFormat::DXT1,
                    4,
                    4,
                    &[70, 200, 20, alpha].repeat(16),
                    encoder,
                )
                .unwrap();
                let mut decoded = [0; 64];
                TextureFormat::DXT1.decompress(&compressed, 4, 4, &mut decoded);
                assert!(decoded
                    .chunks_exact(4)
                    .all(|p| p[3] == if alpha < 128 { 0 } else { 255 }));
            }
        }
    }
    #[test]
    fn alpha_packing_and_rgb_independence() {
        for alpha in 0..=255u8 {
            let mut image = [0u8; 64];
            for (i, p) in image.chunks_exact_mut(4).enumerate() {
                p.copy_from_slice(&[(i * 13) as u8, (i * 7) as u8, 31, alpha]);
            }
            for encoder in [
                Encoder::Directxtex,
                Encoder::BodyshopDxt3,
                Encoder::Bodyshop,
            ] {
                let result = encode(TextureFormat::DXT3, 4, 4, &image, encoder).unwrap();
                let nibble = ((alpha as u16 + 8) / 17) as u8;
                assert_eq!(&result[..8], &[nibble | nibble << 4; 8]);
                for p in image.chunks_exact_mut(4) {
                    p[3] = 255;
                }
                assert_eq!(
                    &result[8..],
                    &encode(TextureFormat::DXT3, 4, 4, &image, encoder).unwrap()[8..]
                );
                for p in image.chunks_exact_mut(4) {
                    p[3] = alpha;
                }
            }
        }
    }
}
