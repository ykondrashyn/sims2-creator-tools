//! Pinned Blender 3.4.1 color-only body bake. No scene evaluator or host process.
//! Sampling and color arithmetic are ported from Cycles (Apache-2.0, Blender
//! Foundation 2011-2022). Margin recipes are exported from Blender (GPL-2.0+).
//! Keep explicit f32 operation order. FMA, idealized gamma curves and premultiply
//! changes break the decoded-pixel contract with the original converter.
use anyhow::{ensure, Context, Result};
use image::{ImageDecoder, ImageEncoder};
use serde_json::{json, Value};
use std::io::{Cursor, Read};

const PIXELS: usize = 1024 * 1024;
const INPUT_LIMIT: usize = 32 * 1024 * 1024;
const OUTPUT_LIMIT: usize = 8 * 1024 * 1024;

fn words(bytes: &[u8]) -> Result<Vec<u32>> {
    ensure!(bytes.len() % 4 == 0, "Damaged conversion mapping");
    Ok(bytes
        .chunks_exact(4)
        .map(|v| u32::from_le_bytes(v.try_into().unwrap()))
        .collect())
}
fn floats(bytes: &[u8]) -> Result<Vec<f32>> {
    Ok(words(bytes)?.into_iter().map(f32::from_bits).collect())
}
fn decode(input: &[u8]) -> Result<image::DynamicImage> {
    ensure!(
        input.len() <= INPUT_LIMIT,
        "Select a PNG no larger than 32 MiB"
    );
    let decoder = image::codecs::png::PngDecoder::new(Cursor::new(input))
        .context("Cannot decode this PNG")?;
    ensure!(
        input.get(25) == Some(&6),
        "Select an RGBA PNG, not an indexed, RGB or grayscale image"
    );
    ensure!(
        decoder.dimensions() == (1024, 2048),
        "Select a 1024×2048 PNG"
    );
    ensure!(
        matches!(
            decoder.color_type(),
            image::ColorType::Rgba8 | image::ColorType::Rgba16
        ),
        "Select an RGBA PNG with transparency"
    );
    // The original Blender image node interprets PNG samples as sRGB. Embedded
    // profiles do not alter this node's explicit colorspace setting.
    let decoded = image::DynamicImage::from_decoder(decoder)?;
    // PIL validates a 16-bit RGBA PNG using its high bytes.
    let pixels = if let image::DynamicImage::ImageRgba16(im) = &decoded {
        im.as_raw().iter().map(|v| (v >> 8) as u8).collect()
    } else {
        decoded.to_rgba8().into_raw()
    };
    ensure!(
        pixels.chunks_exact(4).any(|p| p[3] != 0),
        "The PNG is completely transparent"
    );
    ensure!(
        pixels.chunks_exact(4).any(|p| p[3] != 0 && p[3] != 255),
        "The PNG has no partial transparency"
    );
    ensure!(
        pixels
            .chunks_exact(4)
            .any(|p| p[3] != 0 && p[..3].iter().any(|c| *c != 0)),
        "The PNG has no RGB content inside its alpha mask"
    );
    Ok(decoded)
}
pub fn validate(input: &[u8]) -> Result<Value> {
    decode(input)?;
    Ok(json!({"width":1024,"height":2048,"channels":4,"input_bytes":input.len()}))
}

pub struct Conversion {
    input: Vec<f32>,
    primitive: Vec<u32>,
    differential: Vec<f32>,
    uv: Vec<f32>,
    margin: Vec<u32>,
    thresholds: Vec<u32>,
    pmj: Vec<[f32; 2]>,
    output: Vec<u8>,
    cursor: usize,
    body: String,
}
impl Conversion {
    #[cfg(not(target_arch = "wasm32"))]
    pub fn prepared_input(&self) -> &[f32] {
        &self.input
    }
    pub fn new(mapping: &[u8], input: &[u8], body: &str) -> Result<Self> {
        ensure!(["am", "af"].contains(&body), "Choose Male or Female");
        ensure!(
            mapping.len() <= 64 * 1024 * 1024,
            "Conversion mapping exceeds its limit"
        );
        let mut archive = zip::ZipArchive::new(Cursor::new(mapping))?;
        ensure!(archive.len() == 7, "Unsupported conversion mapping layout");
        let mut read = |name: &str, limit: usize| -> Result<Vec<u8>> {
            let mut entry = archive
                .by_name(name)
                .with_context(|| format!("Missing conversion asset {name}"))?;
            ensure!(
                entry.size() <= limit as u64,
                "Conversion asset exceeds its decoded limit"
            );
            let mut bytes = Vec::new();
            entry.read_to_end(&mut bytes)?;
            Ok(bytes)
        };
        let metadata: Value = serde_json::from_slice(&read("metadata.json", 4 * 1024 * 1024)?)?;
        ensure!(
            metadata["schema_version"] == 1
                && metadata["body"] == body
                && metadata["cycles"]["samples"] == 128,
            "Unsupported conversion profile"
        );
        let primitive = words(&read("primitive.bin", PIXELS * 16)?)?;
        let differential = floats(&read("differential.bin", PIXELS * 16)?)?;
        let uv = floats(&read("uv.bin", 1024 * 1024)?)?;
        let margin = words(&read("margin.bin", 8 * 1024 * 1024)?)?;
        let thresholds = words(&read("quantization.bin", 1020)?)?;
        let pmj_raw = floats(&read("pmj.bin", 256 * 256 * 8)?)?;
        ensure!(
            primitive.len() == PIXELS * 4
                && differential.len() == PIXELS * 4
                && !uv.is_empty()
                && uv.len() % 6 == 0
                && pmj_raw.len() == 256 * 256 * 2,
            "Damaged conversion buffer dimensions"
        );
        ensure!(
            differential
                .iter()
                .chain(uv.iter())
                .chain(pmj_raw.iter())
                .all(|f| f.is_finite()),
            "Nonfinite conversion mapping"
        );
        ensure!(
            uv.iter().all(|v| v.abs() <= 16.) && pmj_raw.iter().all(|v| (0.0..1.0).contains(v)),
            "Invalid conversion coordinates"
        );
        ensure!(
            thresholds.len() == 255
                && thresholds.windows(2).all(|v| v[0] < v[1])
                && thresholds[254] <= 1f32.to_bits(),
            "Damaged color quantization mapping"
        );
        for p in primitive.chunks_exact(4) {
            if p[1] == u32::MAX {
                continue;
            }
            ensure!(
                (p[1] as usize) < uv.len() / 6,
                "Conversion mapping references a missing triangle"
            );
            let (u, v) = (f32::from_bits(p[2]), f32::from_bits(p[3]));
            ensure!(
                u.is_finite() && v.is_finite() && u.abs() <= 2. && v.abs() <= 2.,
                "Invalid conversion barycentric coordinates"
            );
            // These pinned profiles contain no exact-vertex hits. A future
            // exporter must resolve Cycles' vertex nudge before publishing one.
            ensure!(
                !((u == 0. || u == 1.) && (v == 0. || v == 1.)),
                "Conversion profile requires an unsupported vertex nudge"
            );
        }
        validate_margin(&margin)?;
        let decoded = decode(input)?;
        let mut input: Vec<f32> = if let image::DynamicImage::ImageRgba16(im) = decoded {
            let mut pixels = im.into_raw();
            for p in pixels.chunks_exact_mut(4) {
                for c in 0..3 {
                    p[c] = (p[c] as u32 * p[3] as u32 / 65535) as u16;
                }
            }
            pixels.iter().map(|v| *v as f32 * (1. / 65535.)).collect()
        } else {
            let mut pixels = decoded.to_rgba8().into_raw();
            // Blender's byte image upload premultiplies with integer division before
            // interpolation, including discarding hidden RGB under zero alpha.
            for pixel in pixels.chunks_exact_mut(4) {
                for c in 0..3 {
                    pixel[c] = (pixel[c] as u16 * pixel[3] as u16 / 255) as u8;
                }
            }
            pixels.iter().map(|v| *v as f32 * (1. / 255.)).collect()
        };
        for y in 0..1024 {
            for x in 0..4096 {
                input.swap(y * 4096 + x, (2047 - y) * 4096 + x);
            }
        }
        Ok(Self {
            input,
            primitive,
            differential,
            uv,
            margin,
            thresholds,
            pmj: pmj_raw.chunks_exact(2).map(|v| [v[0], v[1]]).collect(),
            output: vec![0; PIXELS * 4],
            cursor: 0,
            body: body.into(),
        })
    }
    pub fn step(&mut self, count: usize) -> Result<Value> {
        ensure!(
            (1..=8192).contains(&count),
            "Conversion step must contain 1 to 8192 pixels"
        );
        let end = (self.cursor + count).min(PIXELS);
        for i in self.cursor..end {
            self.pixel(i);
        }
        self.cursor = end;
        Ok(serde_json::to_value(
            crate::contracts::ConversionProgress {
                done: end == PIXELS,
                processed: end,
                total: PIXELS,
                progress: end as f64 / PIXELS as f64,
            },
        )?)
    }
    fn pixel(&mut self, i: usize) {
        let p = &self.primitive[i * 4..i * 4 + 4];
        if p[1] == u32::MAX {
            return;
        }
        let h = hash_uint(p[0]);
        let pattern = shuffle(h) as usize;
        let scramble = wang(h);
        let uv = &self.uv[p[1] as usize * 6..p[1] as usize * 6 + 6];
        let d = &self.differential[i * 4..i * 4 + 4];
        let mut sum = [0f32; 4];
        for n in 0..128u32 {
            let (mut u, mut v) = (f32::from_bits(p[2]), f32::from_bits(p[3]));
            if n > 0 {
                let r = self.pmj[pattern * 256 + (scramble_sample(n, scramble) & 255) as usize];
                u = mirror(u + d[0] * (r[0] - 0.5) + d[1] * (r[1] - 0.5), 1.);
                v = mirror(v + d[2] * (r[0] - 0.5) + d[3] * (r[1] - 0.5), 1. - u);
            }
            let temp = u;
            u = v;
            v = 1. - temp - v;
            let w = 1. - u - v;
            let x = (u * uv[2] + v * uv[4] + w * uv[0]) * 1024. - 0.5;
            let y = (u * uv[3] + v * uv[5] + w * uv[1]) * 2048. - 0.5;
            let ix = x as i32 - i32::from(x < 0.);
            let iy = y as i32 - i32::from(y < 0.);
            let tx = x - ix as f32;
            let ty = y - iy as f32;
            let ix = ix.rem_euclid(1024) as usize;
            let iy = iy.rem_euclid(2048) as usize;
            let offsets = [
                (iy * 1024 + ix) * 4,
                (iy * 1024 + (ix + 1) % 1024) * 4,
                (((iy + 1) % 2048) * 1024 + ix) * 4,
                (((iy + 1) % 2048) * 1024 + (ix + 1) % 1024) * 4,
            ];
            let weights = [
                (1. - ty) * (1. - tx),
                (1. - ty) * tx,
                ty * (1. - tx),
                ty * tx,
            ];
            let mut pixel = [0f32; 4];
            for (c, channel) in pixel.iter_mut().enumerate() {
                *channel = weights[0] * self.input[offsets[0] + c]
                    + weights[1] * self.input[offsets[1] + c]
                    + weights[2] * self.input[offsets[2] + c]
                    + weights[3] * self.input[offsets[3] + c];
            }
            let a = pixel[3];
            let mut color = [0f32; 3];
            for c in 0..3 {
                let mut x = pixel[c];
                if a != 0. && a != 1. {
                    x *= 1. / a;
                }
                color[c] = (srgb_to_linear(x) * a).max(0.);
            }
            if (color[0] + color[1] + color[2]) * (1. / 3.) >= 1e-5 {
                for c in 0..3 {
                    sum[c] += color[c];
                }
            }
            let tr = 1. - a;
            sum[3] += (tr + tr + tr) * (1. / 3.);
        }
        for (c, channel) in sum.iter().take(3).enumerate() {
            let bits = (channel * (1. / 128.)).to_bits();
            self.output[i * 4 + c] = self.thresholds.partition_point(|t| *t <= bits) as u8;
        }
        self.output[i * 4 + 3] = ((1. - sum[3] * (1. / 128.)).clamp(0., 1.) * 255. + 0.5) as u8;
    }
    pub fn finish(mut self) -> Result<(Vec<u8>, Value)> {
        ensure!(self.cursor == PIXELS, "Conversion is not finished");
        apply_margin(&self.margin, &mut self.output);
        ensure!(
            self.output.chunks_exact(4).any(|p| p[3] > 0),
            "No visible texture content maps onto this body"
        );
        ensure!(
            self.output.chunks_exact(4).any(|p| p[3] > 0 && p[3] < 255),
            "The converted body lost all partial transparency"
        );
        ensure!(
            self.output
                .chunks_exact(4)
                .any(|p| p[3] > 0 && p[..3].iter().any(|c| *c > 0)),
            "No visible color maps onto this body"
        );
        for y in 0..512 {
            for x in 0..4096 {
                self.output.swap(y * 4096 + x, (1023 - y) * 4096 + x);
            }
        }
        let mut png = Vec::new();
        image::codecs::png::PngEncoder::new(&mut png).write_image(
            &self.output,
            1024,
            1024,
            image::ExtendedColorType::Rgba8,
        )?;
        ensure!(png.len() <= OUTPUT_LIMIT, "Output exceeds 8 MiB");
        let report = json!({"schema_version":1,"kind":"conversion","body":self.body,"width":1024,"height":1024,"output_bytes":png.len(),"samples":128,"margin":4,"processing":"Blender 3.4.1 color-only bake","validated":true});
        Ok((png, report))
    }
}

fn validate_margin(m: &[u32]) -> Result<()> {
    ensure!(m.first() == Some(&0x3147524d), "Unsupported margin recipe");
    let mut i = 1;
    let mut extending = false;
    while i < m.len() {
        let op = m[i];
        i += 1;
        match op {
            0 => {
                ensure!(!extending, "Nested margin pass");
                extending = true;
            }
            3 => {
                ensure!(extending, "Unexpected margin commit");
                extending = false;
            }
            1 => {
                ensure!(extending && i + 3 <= m.len(), "Truncated margin pass");
                let (dest, n, div) = (m[i], m[i + 1] as usize, m[i + 2]);
                i += 3;
                ensure!(
                    dest < PIXELS as u32 && (1..=8).contains(&n) && i + n * 2 <= m.len(),
                    "Invalid margin stencil"
                );
                let mut sum = 0;
                for p in m[i..i + n * 2].chunks_exact(2) {
                    ensure!(
                        p[0] < PIXELS as u32 && (1..=2).contains(&p[1]),
                        "Invalid margin sample"
                    );
                    sum += p[1];
                }
                ensure!(sum == div, "Invalid margin divisor");
                i += n * 2;
            }
            2 => {
                ensure!(
                    !extending && i + 9 <= m.len() && m[i] < PIXELS as u32,
                    "Truncated margin interpolation"
                );
                ensure!(
                    m[i + 1..i + 5]
                        .iter()
                        .all(|s| *s == u32::MAX || *s < PIXELS as u32),
                    "Invalid margin interpolation source"
                );
                ensure!(
                    m[i + 5..i + 9]
                        .iter()
                        .all(|w| (0.0..=1.0).contains(&f32::from_bits(*w))),
                    "Invalid margin interpolation weight"
                );
                i += 9;
            }
            _ => anyhow::bail!("Unknown margin operation"),
        }
    }
    ensure!(!extending, "Incomplete margin pass");
    Ok(())
}
fn apply_margin(m: &[u32], output: &mut Vec<u8>) {
    let mut i = 1;
    let mut temporary = Vec::new();
    while i < m.len() {
        let op = m[i];
        i += 1;
        match op {
            0 => temporary.clone_from(output),
            3 => std::mem::swap(output, &mut temporary),
            1 => {
                let (dest, n, div) = (m[i] as usize, m[i + 1] as usize, m[i + 2] as f32);
                i += 3;
                for c in 0..4 {
                    let mut sum = 0f32;
                    for p in m[i..i + n * 2].chunks_exact(2) {
                        sum += output[p[0] as usize * 4 + c] as f32 * p[1] as f32;
                    }
                    temporary[dest * 4 + c] = (sum / div + 0.5) as u8;
                }
                i += n * 2;
            }
            2 => {
                let dest = m[i] as usize;
                let src = &m[i + 1..i + 5];
                let weights = &m[i + 5..i + 9];
                for c in 0..4 {
                    let mut sum = 0f32;
                    for j in 0..4 {
                        let sample = if src[j] == u32::MAX {
                            0
                        } else {
                            output[src[j] as usize * 4 + c]
                        };
                        sum += sample as f32 * f32::from_bits(weights[j]);
                    }
                    output[dest * 4 + c] = (sum + 0.5) as u8;
                }
                i += 9;
            }
            _ => unreachable!(),
        }
    }
}
fn mirror(mut u: f32, max: f32) -> f32 {
    u /= max;
    let f = u.floor();
    u -= f;
    if (f as i32) & 1 != 0 {
        (1. - u) * max
    } else {
        u * max
    }
}
fn srgb_to_linear(c: f32) -> f32 {
    if c < 0.04045 {
        return c.max(0.) * (1. / 12.92);
    }
    let arg = (c + 0.055) * (1. / 1.055);
    let tmp = (arg * f32::from_bits(0x4f55a7fb)).to_bits() as i32 as f32 * (4. / 5.);
    let mut x = f32::from_bits(tmp.round_ties_even() as i32 as u32);
    let a2 = arg * arg;
    let a4 = a2 * a2;
    for _ in 0..3 {
        let x2 = x * x;
        let x4 = x2 * x2;
        x = (4. * x + a4 / x4) * (1. / 5.);
    }
    x * (x * x)
}
fn hash_uint(k: u32) -> u32 {
    let mut a = 0xdeadbf00u32.wrapping_add(k);
    let mut b = 0xdeadbf00u32;
    let mut c = b;
    c = (c ^ b).wrapping_sub(b.rotate_left(14));
    a = (a ^ c).wrapping_sub(c.rotate_left(11));
    b = (b ^ a).wrapping_sub(a.rotate_left(25));
    c = (c ^ b).wrapping_sub(b.rotate_left(16));
    a = (a ^ c).wrapping_sub(c.rotate_left(4));
    b = (b ^ a).wrapping_sub(a.rotate_left(14));
    (c ^ b).wrapping_sub(b.rotate_left(24))
}
fn wang(seed: u32) -> u32 {
    let mut i = 61 ^ seed;
    i = i.wrapping_add(i << 3);
    i ^= i >> 4;
    i.wrapping_mul(0x27d4eb2d)
}
fn scramble_sample(i: u32, seed: u32) -> u32 {
    let mut n = i.reverse_bits();
    n ^= n.wrapping_mul(0x3d20adea);
    n = n.wrapping_add(seed);
    n = n.wrapping_mul((seed >> 16) | 1);
    n ^= n.wrapping_mul(0x05526c56);
    n ^= n.wrapping_mul(0x53a22864);
    n.reverse_bits()
}
fn shuffle(seed: u32) -> u32 {
    let mut i = seed;
    i = i.wrapping_mul(0xe170893d);
    i ^= seed >> 16;
    i ^= (i & 255) >> 4;
    i ^= seed >> 8;
    i = i.wrapping_mul(0x0929eb3f);
    i ^= seed >> 23;
    i ^= (i & 255) >> 1;
    i = i.wrapping_mul(1 | seed >> 27);
    i = i.wrapping_mul(0x6935fa69);
    i ^= (i & 255) >> 11;
    i = i.wrapping_mul(0x74dcb303);
    i ^= (i & 255) >> 2;
    i = i.wrapping_mul(0x9e501cc3);
    i ^= (i & 255) >> 2;
    i = i.wrapping_mul(0xc860a3df);
    i &= 255;
    i ^= i >> 5;
    i
}

#[cfg(test)]
mod tests {
    use super::*;
    fn png(width: u32, height: u32, pixel: [u8; 4]) -> Vec<u8> {
        let image = image::RgbaImage::from_pixel(width, height, image::Rgba(pixel));
        let mut bytes = Vec::new();
        image::codecs::png::PngEncoder::new(&mut bytes)
            .write_image(&image, width, height, image::ExtendedColorType::Rgba8)
            .unwrap();
        bytes
    }
    #[test]
    fn invalid_png_contract() {
        assert!(validate(b"not a PNG").is_err());
        assert!(validate(&png(8, 8, [1, 2, 3, 128]))
            .unwrap_err()
            .to_string()
            .contains("1024"));
        for p in [[1, 2, 3, 0], [1, 2, 3, 255], [0, 0, 0, 128]] {
            assert!(validate(&png(1024, 2048, p)).is_err());
        }
        assert!(validate(&png(1024, 2048, [127, 80, 30, 128])).is_ok());
        assert!(validate(&vec![0; INPUT_LIMIT + 1]).is_err());
    }
    #[test]
    fn margin_recipe_validates_before_processing() {
        assert!(validate_margin(&[0x3147524d, 1]).is_err());
        assert!(validate_margin(&[0x3147524d, 0, 1, 0, 1, 1, PIXELS as u32, 1, 3]).is_err());
        assert!(validate_margin(&[0x3147524d, 0, 1, 0, 1, 2, 1, 1, 3]).is_err());
        assert!(validate_margin(&[0x3147524d, 0, 3]).is_ok());
    }
    #[test]
    fn margin_passes_round_and_read_the_previous_pass() {
        let recipe = [0x3147524d, 0, 1, 1, 1, 1, 0, 1, 1, 2, 1, 1, 1, 1, 3];
        validate_margin(&recipe).unwrap();
        let mut pixels = vec![0; PIXELS * 4];
        pixels[0] = 100;
        apply_margin(&recipe, &mut pixels);
        assert_eq!(pixels[4], 100);
        assert_eq!(pixels[8], 0);
    }
    #[test]
    fn sampling_and_srgb_boundaries_are_stable() {
        assert_eq!(mirror(-0.25, 1.), 0.25);
        assert_eq!(mirror(1.25, 1.), 0.75);
        assert_eq!(srgb_to_linear(0.), 0.);
        assert!((srgb_to_linear(1.) - 1.).abs() < 1e-6);
        for seed in 0..1000 {
            assert!(shuffle(seed) < 256);
        }
        assert!(Conversion::new(b"damaged", b"png", "am").is_err());
        assert!(Conversion::new(b"", b"", "unknown").is_err());
    }
}
