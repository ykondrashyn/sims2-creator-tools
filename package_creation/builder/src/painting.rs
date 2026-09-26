//! Curated wall paintings. Pixel processing and package creation share one path.
use crate::core::images::png;
use crate::{assets, core::resources, object};
use anyhow::{bail, ensure, Context, Result};
use base64::{engine::general_purpose::STANDARD, Engine as _};
use dbpf::internal_file::resource_collection::{
    texture_resource::{decoded_texture::DecodedTexture, TextureFormat, TextureResourceData},
    ResourceData,
};
use image::{imageops, DynamicImage, Rgba, RgbaImage};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::path::Path;

const TXTR: u32 = 0x1c4a276c;
fn hash(b: impl AsRef<[u8]>) -> String {
    format!("{:x}", Sha256::digest(b.as_ref()))
}
fn field<'a>(v: &'a Value, k: &str) -> Result<&'a str> {
    v[k].as_str()
        .with_context(|| format!("Missing painting {k}"))
}
fn uri(img: &RgbaImage) -> Result<String> {
    Ok(format!(
        "data:image/png;base64,{}",
        STANDARD.encode(png(img)?)
    ))
}

/// A source triangle carries UVs in original TS2 addressing and normalized,
/// front-facing artwork coordinates. This corrects skew and mirrored UVs.
#[derive(Clone, Deserialize, Serialize)]
pub struct Triangle {
    pub uv: [[f64; 2]; 3],
    pub art: [[f64; 2]; 3],
}
#[derive(Clone, Deserialize, Serialize)]
pub struct Recipe {
    pub version: u32,
    pub id: String,
    pub template_sha256: String,
    pub texture: String,
    pub texture_key: String,
    pub subset: String,
    pub width: u32,
    pub height: u32,
    pub format: String,
    pub aspect: f64,
    pub triangles: Vec<Triangle>,
    pub dimensions: Value,
    pub wall_anchor: [f64; 3],
}
fn recipe() -> Result<Recipe> {
    let r: Recipe = serde_json::from_slice(&assets::read("painting-recipe")?)?;
    ensure!(
        r.version == 1
            && r.width > 0
            && r.height > 0
            && r.width <= 2048
            && r.height <= 2048
            && r.aspect.is_finite()
            && r.aspect > 0.05
            && r.aspect < 20.
            && r.triangles.len() == 2,
        "This painting recipe is unsupported or damaged. Its saved input is retained"
    );
    ensure!(
        r.triangles.iter().all(|t| t
            .uv
            .iter()
            .chain(t.art.iter())
            .flatten()
            .all(|v| v.is_finite() && v.abs() < 16.)),
        "Invalid painting UV mapping"
    );
    ensure!(
        hash(assets::read("painting-template")?) == r.template_sha256,
        "Painting template integrity check failed"
    );
    Ok(r)
}
fn decode(bytes: &[u8]) -> Result<(RgbaImage, Value)> {
    crate::core::image_decode::decode(bytes, crate::core::image_decode::DecodeLimits::INPUT)
}
fn input(name: &str) -> Result<(RgbaImage, Value)> {
    let b = assets::read(name)?;
    let key = format!("painting-decoded-{}", hash(&b));
    if let Some(img) = assets::image_get(&key) {
        return Ok((
            img,
            serde_json::from_slice(&assets::read("painting-image-info")?)?,
        ));
    }
    let (img, info) = decode(&b)?;
    assets::clear_images();
    assets::image_insert(&key, img.clone());
    assets::write("painting-image-info", serde_json::to_vec(&info)?)?;
    Ok((img, info))
}
#[derive(Clone, Deserialize, Serialize)]
pub struct Crop {
    pub mode: String,
    pub x: f64,
    pub y: f64,
    pub zoom: f64,
    pub background: [u8; 3],
}
impl Default for Crop {
    fn default() -> Self {
        Self {
            mode: "fill".into(),
            x: 0.5,
            y: 0.5,
            zoom: 1.,
            background: [255; 3],
        }
    }
}
fn settings(job: &Value) -> Result<Crop> {
    let c = if job["crop"].is_null() {
        Crop::default()
    } else {
        serde_json::from_value(job["crop"].clone()).context("Invalid painting crop settings")?
    };
    ensure!(
        ["fill", "contain"].contains(&c.mode.as_str())
            && [c.x, c.y, c.zoom].iter().all(|v| v.is_finite())
            && (0. ..=1.).contains(&c.x)
            && (0. ..=1.).contains(&c.y)
            && (1. ..=8.).contains(&c.zoom),
        "Crop position must be within the image, with zoom from 1 to 8"
    );
    Ok(c)
}
fn artwork(source: &RgbaImage, r: &Recipe, c: &Crop) -> (RgbaImage, Value) {
    let w = (r.width.max(r.height) as f64).min(1024.);
    let (aw, ah) = if r.aspect >= 1. {
        (w.round() as u32, (w / r.aspect).round().max(1.) as u32)
    } else {
        ((w * r.aspect).round().max(1.) as u32, w.round() as u32)
    };
    let (sw, sh) = (source.width() as f64, source.height() as f64);
    let (cw, ch, x, y) = if c.mode == "fill" {
        let cw = sw.min(sh * r.aspect) / c.zoom;
        let ch = cw / r.aspect;
        (cw, ch, (sw - cw) * c.x, (sh - ch) * c.y)
    } else {
        (sw, sh, 0., 0.)
    };
    // Retain fractional crop coordinates, especially for tiny sources. Rounding
    // the crop width and height independently can stretch low-resolution art.
    let mut cut = source.clone();
    for p in cut.pixels_mut() {
        let alpha = p[3] as u32;
        for (v, bg) in p.0[..3].iter_mut().zip(c.background) {
            *v = (((*v as u32) * alpha + bg as u32 * (255 - alpha) + 127) / 255) as u8;
        }
        p[3] = 255;
    }
    let (dw, dh) = if c.mode == "contain" {
        let scale = (aw as f64 / sw).min(ah as f64 / sh);
        (
            (sw * scale).round().max(1.) as u32,
            (sh * scale).round().max(1.) as u32,
        )
    } else {
        (aw, ah)
    };
    let resized = resize_lanczos(&cut, dw, dh, [x, y, cw, ch]);
    let mut out = RgbaImage::from_pixel(
        aw,
        ah,
        Rgba([c.background[0], c.background[1], c.background[2], 255]),
    );
    imageops::overlay(
        &mut out,
        &resized,
        ((aw - dw) / 2) as i64,
        ((ah - dh) / 2) as i64,
    );
    (
        out,
        json!({"source_rect":[x,y,cw,ch],"artwork_size":[aw,ah],"low_resolution":cw<(dw as f64)||ch<(dh as f64)}),
    )
}
// Fixed-point, separable Lanczos3 avoids platform SIMD rounding differences.
// The horizontal pass retains fractional and negative values until the final
// vertical pass. All weights sum to exactly one, including clipped edges.
fn resize_lanczos(source: &RgbaImage, w: u32, h: u32, area: [f64; 4]) -> RgbaImage {
    const ONE: i64 = 1 << 20;
    fn weights(from: u32, to: u32, origin: f64, extent: f64) -> Vec<Vec<(u32, i64)>> {
        (0..to)
            .map(|i| {
                let ratio = extent / to as f64;
                let scale = ratio.max(1.);
                let center = origin + (i as f64 + 0.5) * ratio - 0.5;
                let start = ((center - 3. * scale).ceil() as i64).max(0);
                let end = ((center + 3. * scale).floor() as i64).min(from as i64 - 1);
                let mut raw = Vec::new();
                let mut total = 0.;
                for x in start..=end {
                    let d = (x as f64 - center) / scale;
                    let sinc = |v: f64| {
                        if v.abs() < 1e-12 {
                            1.
                        } else {
                            libm::sin(std::f64::consts::PI * v) / (std::f64::consts::PI * v)
                        }
                    };
                    let weight = if d.abs() < 3. {
                        sinc(d) * sinc(d / 3.)
                    } else {
                        0.
                    };
                    total += weight;
                    raw.push((x as u32, weight));
                }
                let mut fixed: Vec<_> = raw
                    .iter()
                    .map(|&(x, v)| (x, (v / total * ONE as f64).round() as i64))
                    .collect();
                let sum: i64 = fixed.iter().map(|p| p.1).sum();
                let mid = fixed.len() / 2;
                fixed[mid].1 += ONE - sum;
                fixed
            })
            .collect()
    }
    let wx = weights(source.width(), w, area[0], area[2]);
    let wy = weights(source.height(), h, area[1], area[3]);
    let mut rows = vec![[0i64; 3]; w as usize * source.height() as usize];
    for y in 0..source.height() {
        for (x, terms) in wx.iter().enumerate() {
            let dest = &mut rows[y as usize * w as usize + x];
            for &(sx, k) in terms {
                for (c, channel) in dest.iter_mut().enumerate() {
                    *channel += source.get_pixel(sx, y)[c] as i64 * k;
                }
            }
        }
    }
    let mut result = RgbaImage::new(w, h);
    for (y, terms) in wy.iter().enumerate() {
        for x in 0..w as usize {
            let mut sum = [0i64; 3];
            for &(sy, k) in terms {
                for (c, channel) in sum.iter_mut().enumerate() {
                    *channel += rows[sy as usize * w as usize + x][c] * k;
                }
            }
            let rgb = sum.map(|v| ((v + ONE * ONE / 2) / (ONE * ONE)).clamp(0, 255) as u8);
            result.put_pixel(x as u32, y as u32, Rgba([rgb[0], rgb[1], rgb[2], 255]));
        }
    }
    result
}
fn barycentric(p: [f64; 2], t: [[f64; 2]; 3]) -> Option<[f64; 3]> {
    let [a, b, c] = t;
    let d = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1]);
    if d.abs() < 1e-12 {
        return None;
    }
    let u = ((b[1] - c[1]) * (p[0] - c[0]) + (c[0] - b[0]) * (p[1] - c[1])) / d;
    let v = ((c[1] - a[1]) * (p[0] - c[0]) + (a[0] - c[0]) * (p[1] - c[1])) / d;
    let w = 1. - u - v;
    if [u, v, w].iter().all(|v| *v >= -1e-7 && *v <= 1. + 1e-7) {
        Some([u, v, w])
    } else {
        None
    }
}
fn uv_point(r: &Recipe, x: u32, y: u32) -> Option<[f64; 2]> {
    let uv = [
        (x as f64 + 0.5) / r.width as f64,
        (y as f64 + 0.5) / r.height as f64,
    ];
    let mut best = None;
    let mut distance = f64::MAX;
    for t in &r.triangles {
        let u0 =
            t.uv.iter()
                .map(|v| v[0])
                .fold(f64::INFINITY, f64::min)
                .floor() as i32;
        let u1 =
            t.uv.iter()
                .map(|v| v[0])
                .fold(f64::NEG_INFINITY, f64::max)
                .ceil() as i32;
        let v0 =
            t.uv.iter()
                .map(|v| v[1])
                .fold(f64::INFINITY, f64::min)
                .floor() as i32;
        let v1 =
            t.uv.iter()
                .map(|v| v[1])
                .fold(f64::NEG_INFINITY, f64::max)
                .ceil() as i32;
        for u in u0..=u1 {
            for v in v0..=v1 {
                if let Some(b) = barycentric([uv[0] + u as f64, uv[1] + v as f64], t.uv) {
                    let a =
                        std::array::from_fn(|i| (0..3).map(|j| b[j] * t.art[j][i]).sum::<f64>());
                    let d = (a[0] - 0.5).abs() + (a[1] - 0.5).abs();
                    if d < distance {
                        distance = d;
                        best = Some(a);
                    }
                }
            }
        }
    }
    best
}
fn sample(img: &RgbaImage, p: [f64; 2]) -> [u8; 3] {
    let x = (p[0] * img.width() as f64 - 0.5).clamp(0., (img.width() - 1) as f64);
    let y = (p[1] * img.height() as f64 - 0.5).clamp(0., (img.height() - 1) as f64);
    let (ix, iy) = (x.floor() as u32, y.floor() as u32);
    let (fx, fy) = (x - ix as f64, y - iy as f64);
    std::array::from_fn(|c| {
        let a = img.get_pixel(ix, iy)[c] as f64 * (1. - fx)
            + img.get_pixel((ix + 1).min(img.width() - 1), iy)[c] as f64 * fx;
        let b = img.get_pixel(ix, (iy + 1).min(img.height() - 1))[c] as f64 * (1. - fx)
            + img.get_pixel(
                (ix + 1).min(img.width() - 1),
                (iy + 1).min(img.height() - 1),
            )[c] as f64
                * fx;
        (a * (1. - fy) + b * fy).round().clamp(0., 255.) as u8
    })
}
fn compose_pixels(
    original: &RgbaImage,
    art: &RgbaImage,
    r: &Recipe,
) -> (RgbaImage, Vec<bool>, usize) {
    let mut composed = original.clone();
    let mut count = 0;
    let mut coverage = vec![false; r.width as usize * r.height as usize];
    for y in 0..r.height {
        for x in 0..r.width {
            if let Some(p) = uv_point(r, x, y) {
                coverage[y as usize * r.width as usize + x as usize] = true;
                composed.get_pixel_mut(x, y).0[..3].copy_from_slice(&sample(art, p));
                count += 1;
            }
        }
    }
    (composed, coverage, count)
}
fn compose(job: &Value) -> Result<(Value, RgbaImage)> {
    let encoder = crate::texture_encoding::Encoder::from_job(job)?;
    let r = recipe()?;
    let c = settings(job)?;
    let (source, info) = input("painting-input")?;
    let (art, mut report) = artwork(&source, &r, &c);
    let (container, mut nodes) = resources::load_raw(Path::new("painting-template"))?;
    let n = nodes
        .iter_mut()
        .find(|n| resources::key_text(n.key()) == r.texture_key && n.key().0 == TXTR)
        .context("Painting artwork texture is missing")?;
    let mut collection = n.collection()?;
    let ResourceData::Texture(t) = &mut collection.entries[0].data else {
        bail!("Invalid artwork texture");
    };
    ensure!(
        t.width == r.width
            && t.height == r.height
            && format!("{:?}", t.get_format()) == r.format
            && t.textures.len() == 1,
        "Painting artwork does not match its pinned recipe"
    );
    let d = t.decompress(0, t.mip_levels() - 1)?;
    let source_mips = t.textures[0].entries.clone();
    let original =
        RgbaImage::from_raw(t.width, t.height, d.data).context("Invalid artwork pixels")?;
    let (composed, coverage, count) = compose_pixels(&original, &art, &r);
    ensure!(count > 0, "Painting artwork region is empty");
    ensure!(
        composed
            .pixels()
            .zip(original.pixels())
            .all(|(a, b)| a[3] == b[3]),
        "Painting alpha changed before compression"
    );
    report["composed_sha256"] = json!(hash(composed.as_raw()));
    let format = t.get_format();
    t.compress_replace(
        DecodedTexture {
            width: r.width as usize,
            height: r.height as usize,
            data: composed.into_raw(),
        },
        Some(TextureFormat::RawARGB32),
    );
    t.add_max_mip_levels(if format == TextureFormat::DXT1 {
        Some(127)
    } else {
        None
    });
    crate::texture_encoding::recompress(t, format, encoder)?;
    report["texture_encoding"] = encoder.usage(&[format]);
    report["effective_encoder"] = json!(encoder.effective(format));
    ensure!(
        t.mip_levels() == t.max_mip_levels(),
        "Painting mip chain is incomplete"
    );
    // Keep source DXT blocks that are entirely outside the changed artwork.
    // A shared atlas must not accumulate compression loss in its frame.
    let block = if format == TextureFormat::DXT1 { 8 } else { 16 };
    let mut mask = coverage.clone();
    let (mut mw, mut mh) = (r.width as usize, r.height as usize);
    let levels = t.mip_levels();
    for down in 0..levels {
        if down < source_mips.len() {
            if let (TextureResourceData::Embedded(old), TextureResourceData::Embedded(new)) = (
                &source_mips[source_mips.len() - 1 - down],
                &mut t.textures[0].entries[levels - 1 - down],
            ) {
                ensure!(
                    old.data.len() == new.data.len(),
                    "Painting source mip dimensions changed"
                );
                for by in 0..mh.div_ceil(4) {
                    for bx in 0..mw.div_ceil(4) {
                        let changed = (by * 4..(by * 4 + 4).min(mh))
                            .any(|y| (bx * 4..(bx * 4 + 4).min(mw)).any(|x| mask[y * mw + x]));
                        if !changed {
                            let offset = (by * mw.div_ceil(4) + bx) * block;
                            new.data[offset..offset + block]
                                .copy_from_slice(&old.data[offset..offset + block]);
                        }
                    }
                }
            }
        }
        let (nw, nh) = ((mw / 2).max(1), (mh / 2).max(1));
        let mut next = vec![false; nw * nh];
        for y in 0..nh {
            for x in 0..nw {
                next[y * nw + x] = (y * 2..(y * 2 + 2).min(mh))
                    .any(|sy| (x * 2..(x * 2 + 2).min(mw)).any(|sx| mask[sy * mw + sx]));
            }
        }
        mask = next;
        mw = nw;
        mh = nh;
    }
    let decoded = t.decompress(0, t.mip_levels() - 1)?;
    let alpha_max = decoded
        .data
        .chunks_exact(4)
        .zip(original.pixels())
        .map(|(a, b)| a[3].abs_diff(b[3]))
        .max()
        .unwrap_or(0);
    ensure!(
        alpha_max <= if format == TextureFormat::DXT3 { 17 } else { 0 },
        "Painting compressed alpha exceeds its error allowance"
    );
    report["alpha_max_error"] = json!(alpha_max);
    report["mips"] = json!(t.mip_levels());
    report["format"] = json!(r.format);
    let border_max = decoded
        .data
        .chunks_exact(4)
        .zip(original.pixels())
        .enumerate()
        .filter(|(i, _)| !coverage[*i])
        .flat_map(|(_, (a, b))| (0..3).map(move |c| a[c].abs_diff(b[c])))
        .max()
        .unwrap_or(0);
    report["frame_border_max_error"] = json!(border_max);
    n.set(&collection)?;
    nodes.retain(|n| n.key().0 != 0xac2950c1);
    resources::write(container, &mut nodes, Path::new("object-template"))?;
    report["image"] = info;
    report["template"] = json!(r.id);
    report["modified_pixels"] = json!(count);
    report["recipe_version"] = json!(1);
    report["preview_texture_sha256"] = json!(hash(&decoded.data));
    Ok((report, art))
}
fn prepare(job: &Value) -> Result<Value> {
    let (_, _) = compose(job)?;
    let mut p = job.clone();
    p["mode"] = json!("clone");
    if p["description"].is_null() {
        p["description"] = json!("");
    }
    let title = field(job, "title")?;
    // Catalog text can contain Unicode. The filename uses a stable ASCII stem.
    let stem = title
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '_' || c == '-' {
                c
            } else {
                '_'
            }
        })
        .collect::<String>();
    let stem = stem
        .trim_matches(['_', '-'])
        .chars()
        .take(48)
        .collect::<String>();
    p["object_name"] = json!(if stem.is_empty() { "Painting" } else { &stem });
    let p = object::prepare(&p)?;
    Ok(p)
}
fn appearance_fingerprints(path: &str) -> Result<Vec<String>> {
    let (_, nodes) = resources::load_raw(Path::new(path))?;
    let mut hashes = Vec::new();
    for mut n in nodes
        .into_iter()
        .filter(|n| [0xac4f8687, TXTR].contains(&n.key().0))
    {
        let mut c = n.collection()?;
        // Resource names and links are remapped during cloning. Geometry and
        // encoded mip payloads must remain equal to the prepared appearance.
        c.links.clear();
        for entry in &mut c.entries {
            match &mut entry.data {
                ResourceData::Mesh(m) => m.file_name.name = "".into(),
                ResourceData::Texture(t) => t.file_name.name = "".into(),
                _ => bail!("Unexpected painting appearance resource"),
            }
        }
        n.set(&c)?;
        hashes.push(hash(&n.bytes));
    }
    hashes.sort();
    Ok(hashes)
}
pub fn dispatch(op: &str, p: &Value) -> Result<Value> {
    match op {
        "painting_inspect_image" => {
            let (img, mut info) = input(p["input"].as_str().unwrap_or("painting-input"))?;
            info["preview"] = json!(uri(&DynamicImage::ImageRgba8(img)
                .thumbnail(1024, 1024)
                .into_rgba8())?);
            Ok(info)
        }
        "painting_open" => {
            let r = recipe()?;
            assets::write("object-template", assets::read("painting-template")?)?;
            let (_, nodes) = resources::load_raw(Path::new("object-template"))?;
            Ok(json!({"recipe":r,"scene":object::render(&nodes)?}))
        }
        "painting_compose" => {
            let (report, art) = compose(&p["job"])?;
            let (_, nodes) = resources::load_raw(Path::new("object-template"))?;
            Ok(json!({"report":report,"artwork":uri(&art)?,"scene":object::render(&nodes)?}))
        }
        "painting_prepare" => prepare(&p["job"]),
        "painting_build" => {
            let (pixels, _) = compose(&p["job"])?;
            let appearance = appearance_fingerprints("object-template")?;
            let mut job = p["job"].clone();
            if job["description"].is_null() {
                job["description"] = json!("");
            }
            let mut report = object::build(&job)?;
            ensure!(
                assets::read("output")?.len() <= 64 * 1024 * 1024,
                "Painting package exceeds 64 MiB"
            );
            ensure!(
                appearance == appearance_fingerprints("output")?,
                "Painting cloning changed geometry or prepared texture payloads"
            );
            report["appearance_preserved"] = json!(true);
            report["painting"] = pixels;
            Ok(report)
        }
        "painting_validate" => {
            let expected = assets::read(field(p, "asset")?)?;
            let report = dispatch("painting_build", &json!({"job":p["job"]}))?;
            ensure!(
                expected == assets::read("output")?,
                "Painting package does not match its saved artwork and identities"
            );
            Ok(report)
        }
        _ => bail!("Unsupported painting operation"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn test_recipe() -> Recipe {
        Recipe {
            version: 1,
            id: "test".into(),
            template_sha256: String::new(),
            texture: String::new(),
            texture_key: String::new(),
            subset: String::new(),
            width: 8,
            height: 8,
            format: "DXT1".into(),
            aspect: 2.,
            triangles: vec![
                Triangle {
                    uv: [[0., 0.], [1., 0.], [0., 1.]],
                    art: [[0., 0.], [1., 0.], [0., 1.]],
                },
                Triangle {
                    uv: [[1., 1.], [1., 0.], [0., 1.]],
                    art: [[1., 1.], [1., 0.], [0., 1.]],
                },
            ],
            dimensions: json!({}),
            wall_anchor: [0.; 3],
        }
    }
    #[test]
    fn composition_coverage_preserves_wrapped_uv_pixels_and_frame() {
        let original = RgbaImage::from_fn(8, 8, |x, y| Rgba([13, 29, 47, (x + y * 8) as u8]));
        let art = RgbaImage::from_fn(8, 8, |x, y| Rgba([(x * 31) as u8, (y * 31) as u8, 83, 255]));
        for wrap in [-1., 0., 1.] {
            let mut r = test_recipe();
            for triangle in &mut r.triangles {
                for uv in &mut triangle.uv {
                    uv[0] = uv[0] * 0.5 + 0.25 + wrap;
                    uv[1] = uv[1] * 0.5 + 0.25;
                }
            }
            let (composed, coverage, count) = compose_pixels(&original, &art, &r);
            assert_eq!(count, 16);
            assert_eq!(coverage.iter().filter(|inside| **inside).count(), count);
            for y in 0..8 {
                for x in 0..8 {
                    let index = (y * 8 + x) as usize;
                    let expected_inside = (2..6).contains(&x) && (2..6).contains(&y);
                    assert_eq!(coverage[index], expected_inside);
                    assert_eq!(composed.get_pixel(x, y)[3], original.get_pixel(x, y)[3]);
                    if expected_inside {
                        let p = uv_point(&r, x, y).unwrap();
                        assert_eq!(composed.get_pixel(x, y).0[..3], sample(&art, p));
                    } else {
                        assert_eq!(composed.get_pixel(x, y), original.get_pixel(x, y));
                    }
                }
            }
        }
    }
    #[test]
    fn crop_and_transparency() {
        let src = RgbaImage::from_pixel(16, 16, Rgba([255, 0, 0, 128]));
        let (art, r) = artwork(&src, &test_recipe(), &Crop::default());
        assert_eq!(r["source_rect"], json!([0., 4., 16., 8.]));
        assert_eq!(art.get_pixel(3, 2).0, [255, 127, 127, 255]);
        let mut c = Crop::default();
        c.mode = "contain".into();
        let (art, _) = artwork(&src, &test_recipe(), &c);
        assert_eq!(art.get_pixel(0, 0).0, [255; 4]);
    }
    #[test]
    fn fractional_crop_preserves_physical_aspect() {
        let src = RgbaImage::from_pixel(5, 7, Rgba([30, 60, 90, 255]));
        let mut recipe = test_recipe();
        recipe.aspect = 0.637721042;
        let (out, report) = artwork(&src, &recipe, &Crop::default());
        let rect = &report["source_rect"];
        assert!(
            (rect[2].as_f64().unwrap() / rect[3].as_f64().unwrap() - recipe.aspect).abs() < 1e-12
        );
        assert!(out.pixels().all(|p| p.0 == [30, 60, 90, 255]));
    }
    #[test]
    fn uv_repeat_and_orientation() {
        let mut r = test_recipe();
        assert_eq!(uv_point(&r, 0, 0), Some([0.0625, 0.0625]));
        for t in &mut r.triangles {
            for p in &mut t.uv {
                p[0] -= 1.;
            }
        }
        assert_eq!(uv_point(&r, 0, 0), Some([0.0625, 0.0625]));
        for t in &mut r.triangles {
            for p in &mut t.art {
                p[0] = 1. - p[0];
            }
        }
        assert_eq!(uv_point(&r, 0, 0), Some([0.9375, 0.0625]));
    }
    #[test]
    fn decode_limits_and_contents() {
        let img = RgbaImage::from_pixel(8, 9, Rgba([1, 2, 3, 0]));
        let (out, info) = decode(&png(&img).unwrap()).unwrap();
        assert_eq!(out, img);
        assert_eq!(info["alpha"], true);
        assert!(decode(b"bad image").is_err());
        assert!(crate::core::image_decode::DecodeLimits::INPUT
            .dimensions(8193, 1)
            .is_err());
        assert!(crate::core::image_decode::DecodeLimits::INPUT
            .dimensions(8192, 8192)
            .is_err());
        assert!(settings(
            &json!({"crop":{"mode":"fill","x":0.,"y":0.,"zoom":0.,"background":[0,0,0]}})
        )
        .is_err());
    }
}
