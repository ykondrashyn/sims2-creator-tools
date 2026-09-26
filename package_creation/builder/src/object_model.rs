//! Static glTF import from buffers, with no filesystem or URL resolution.
pub(crate) use crate::core::geometry::Part;
use crate::{
    assets,
    core::resources::{self, Node},
    object_scene,
};
use anyhow::{bail, ensure, Context, Result};
use base64::{engine::general_purpose::STANDARD, Engine as _};
use dbpf::{
    header_v1::InstanceId,
    internal_file::resource_collection::{
        geometric_data_container::{
            math::Vertex, AttributeBinding, AttributeBuffer, AttributeGroup, AttributeType,
            BlockFormat, BoundingMesh, GeometricDataContainer, Mesh, Reference,
        },
        material_definition::Property,
        texture_resource::{decoded_texture::DecodedTexture, TextureFormat},
        ResourceData,
    },
};
use gltf::{buffer::Source, image::Source as ImageSource, mesh::Mode};
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, BTreeSet},
    io::{Cursor, Read},
    rc::Rc,
};
const TEXTURE_BUDGET: usize = 128 * 1024 * 1024;
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
struct TextureKey {
    source: Option<usize>,
    factor: [u32; 4],
    cutout: bool,
    cutoff: u32,
    // Version of the power-of-two Lanczos3 resize and color preparation policy.
    policy: u8,
}
#[derive(Default)]
struct Textures {
    prepared: BTreeMap<TextureKey, Rc<image::RgbaImage>>,
    logical_bytes: usize,
}
impl Textures {
    fn reserve(&mut self, width: u32, height: u32) -> Result<()> {
        ensure!(
            width > 0 && height > 0 && width <= 2048 && height <= 2048,
            "Model textures must be at most 2048 by 2048"
        );
        let bytes = width.next_power_of_two() as usize * height.next_power_of_two() as usize * 4;
        ensure!(
            bytes <= TEXTURE_BUDGET - self.logical_bytes,
            "Decoded model textures exceed 128 MiB"
        );
        self.logical_bytes += bytes;
        Ok(())
    }
    fn get(&mut self, key: &TextureKey) -> Result<Option<Rc<image::RgbaImage>>> {
        if let Some(image) = self.prepared.get(key).cloned() {
            // Logical per-part limits stay unchanged even when pixels are shared.
            self.reserve(image.width(), image.height())?;
            return Ok(Some(image));
        }
        Ok(None)
    }
}
const GMDC: u32 = 0xac4f8687;
const TXMT: u32 = 0x49596978;
const TXTR: u32 = 0x1c4a276c;
const SHPE: u32 = 0xfc6eb1f7;
const MMAT: u32 = 0x4c697e5a;
type Mat = [[f32; 4]; 4];
const ID: Mat = [
    [1., 0., 0., 0.],
    [0., 1., 0., 0.],
    [0., 0., 1., 0.],
    [0., 0., 0., 1.],
];
fn mul(a: Mat, b: Mat) -> Mat {
    let mut r = [[0.; 4]; 4];
    for c in 0..4 {
        for row in 0..4 {
            r[c][row] = (0..4).map(|i| a[i][row] * b[c][i]).sum();
        }
    }
    r
}
fn pos(m: Mat, p: [f32; 3]) -> [f32; 3] {
    std::array::from_fn(|i| m[0][i] * p[0] + m[1][i] * p[1] + m[2][i] * p[2] + m[3][i])
}
fn sub(a: [f32; 3], b: [f32; 3]) -> [f32; 3] {
    std::array::from_fn(|i| a[i] - b[i])
}
fn cross(a: [f32; 3], b: [f32; 3]) -> [f32; 3] {
    [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]
}
fn unit(a: [f32; 3]) -> [f32; 3] {
    let l = a.iter().map(|v| v * v).sum::<f32>().sqrt();
    if l > 1e-12 {
        a.map(|v| v / l)
    } else {
        [0., 0., 1.]
    }
}
fn normal(m: Mat, n: [f32; 3]) -> [f32; 3] {
    let x = [m[0][0], m[0][1], m[0][2]];
    let y = [m[1][0], m[1][1], m[1][2]];
    let z = [m[2][0], m[2][1], m[2][2]];
    let a = cross(y, z);
    let b = cross(z, x);
    let c = cross(x, y);
    let det = x.iter().zip(a).map(|(x, a)| x * a).sum::<f32>();
    unit(std::array::from_fn(|i| {
        (a[i] * n[0] + b[i] * n[1] + c[i] * n[2]) * det.signum()
    }))
}
fn game_axes(v: [f32; 3]) -> [f32; 3] {
    [-v[0], v[2], v[1]]
}
fn safe_path(s: &str) -> Result<()> {
    ensure!(
        !s.is_empty()
            && !s.starts_with('/')
            && !s.contains('\\')
            && !s.contains(':')
            && !s.contains('%')
            && !s.split('/').any(|s| s == ".." || s == "."),
        "Model references must be plain relative paths inside the ZIP"
    );
    Ok(())
}
fn uri(s: &str, base: &str, files: &BTreeMap<String, Vec<u8>>) -> Result<Vec<u8>> {
    if s.starts_with("data:") {
        let (header, data) = s.split_once(',').context("Malformed model data URI")?;
        ensure!(
            header.ends_with(";base64"),
            "Model data URIs must use base64"
        );
        return Ok(STANDARD.decode(data)?);
    }
    safe_path(s)?;
    files
        .get(&format!("{base}{s}"))
        .cloned()
        .with_context(|| format!("Missing model file {s}. Include it inside the ZIP"))
}
fn load_mode(path: &str, character: bool) -> Result<Vec<Part>> {
    let bytes = assets::read_shared(path)?;
    ensure!(bytes.len() <= 64 * 1024 * 1024, "Model file exceeds 64 MiB");
    let mut files = BTreeMap::new();
    let (bytes, base) = if path.ends_with(".zip") {
        let mut zip =
            zip::ZipArchive::new(Cursor::new(bytes.as_slice())).context("Invalid model ZIP")?;
        ensure!(zip.len() <= 256, "Model ZIP has too many entries");
        let mut total = 0;
        let mut names = BTreeSet::new();
        for i in 0..zip.len() {
            let mut f = zip.by_index(i)?;
            if f.is_dir() {
                continue;
            }
            let name = f.name().to_string();
            safe_path(&name)?;
            ensure!(
                names.insert(name.to_ascii_lowercase()),
                "Model ZIP has conflicting filenames"
            );
            ensure!(
                f.size() <= 64 * 1024 * 1024,
                "Model ZIP entry exceeds 64 MiB"
            );
            total += f.size();
            ensure!(
                total <= 128 * 1024 * 1024,
                "Expanded model ZIP exceeds 128 MiB"
            );
            let mut b = vec![];
            f.read_to_end(&mut b)?;
            files.insert(name, b);
        }
        let scenes = files
            .keys()
            .filter(|n| n.to_ascii_lowercase().ends_with(".gltf"))
            .cloned()
            .collect::<Vec<_>>();
        ensure!(
            scenes.len() == 1,
            "The ZIP must contain exactly one .gltf scene"
        );
        let name = &scenes[0];
        let base = name
            .rsplit_once('/')
            .map(|(a, _)| format!("{a}/"))
            .unwrap_or_default();
        (Rc::new(files[name].clone()), base)
    } else {
        ensure!(
            bytes.starts_with(b"glTF"),
            "Choose a binary GLB or ZIP containing a glTF scene"
        );
        (bytes, String::new())
    };
    let doc = gltf::Gltf::from_slice(&bytes).context("Invalid or unsupported glTF 2 model")?;
    ensure!(
        doc.animations().next().is_none() && doc.skins().next().is_none(),
        "{}",
        if character {
            "This fitting prototype accepts neutral geometry. Export a neutral A or T pose without skinning or animation. The original rig is not modified"
        } else {
            "Use a static model without animations or skinning"
        }
    );
    ensure!(
        doc.extensions_required().next().is_none(),
        "Export an ordinary glTF 2 model without required compression or material extensions"
    );
    let mut buffers = vec![];
    let mut total = 0;
    for b in doc.buffers() {
        let data = match b.source() {
            Source::Bin => doc.blob.clone().context("Missing GLB binary buffer")?,
            Source::Uri(s) => uri(s, &base, &files)?,
        };
        ensure!(data.len() >= b.length(), "Truncated model buffer");
        total += data.len();
        ensure!(
            total <= 128 * 1024 * 1024,
            "Decoded model buffers exceed 128 MiB"
        );
        buffers.push(data);
    }
    let scene = doc
        .default_scene()
        .or_else(|| doc.scenes().next())
        .context("Model has no scene")?;
    let mut parts = Vec::new();
    let mut visited = BTreeSet::new();
    let mut textures = Textures::default();
    // Keep traversal inputs explicit, including character preservation policy.
    #[allow(clippy::too_many_arguments)]
    fn visit(
        node: gltf::Node,
        parent: Mat,
        depth: usize,
        buffers: &[Vec<u8>],
        base: &str,
        files: &BTreeMap<String, Vec<u8>>,
        parts: &mut Vec<Part>,
        visited: &mut BTreeSet<usize>,
        textures: &mut Textures,
        character: bool,
    ) -> Result<()> {
        ensure!(
            depth < 64 && visited.insert(node.index()),
            "Model scene is cyclic or repeats a node"
        );
        let m = mul(parent, node.transform().matrix());
        ensure!(
            m.iter().flatten().all(|f| f.is_finite()),
            "Model transform is not finite"
        );
        if let Some(mesh) = node.mesh() {
            for primitive in mesh.primitives() {
                ensure!(
                    parts.len() < if character { 64 } else { 16 },
                    "Too many material groups. Optimize or combine materials before importing"
                );
                ensure!(
                    primitive.mode() == Mode::Triangles
                        && primitive.morph_targets().next().is_none(),
                    "Use triangle geometry without morph targets"
                );
                let r = primitive.reader(|b| Some(buffers[b.index()].as_slice()));
                ensure!(r.read_colors(0).is_none(), "Vertex colors are unsupported. Bake them into the base-color texture before exporting");
                let source = r
                    .read_positions()
                    .context("Model positions are missing")?
                    .collect::<Vec<_>>();
                ensure!(
                    (3..=65535).contains(&source.len()),
                    "Each model group requires 3 to 65535 vertices"
                );
                let positions = source
                    .iter()
                    .map(|p| game_axes(pos(m, *p)))
                    .collect::<Vec<_>>();
                ensure!(
                    positions
                        .iter()
                        .flatten()
                        .all(|f| f.is_finite() && f.abs() < 1e6),
                    "Model coordinates are invalid or too large"
                );
                let mut indices = r
                    .read_indices()
                    .map(|x| x.into_u32().collect::<Vec<_>>())
                    .unwrap_or_else(|| (0..positions.len() as u32).collect());
                ensure!(
                    !indices.is_empty()
                        && indices.len() % 3 == 0
                        && indices.len() <= 600000
                        && indices.iter().all(|i| (*i as usize) < positions.len()),
                    "Model has invalid or excessive triangle indices"
                );
                let x = [m[0][0], m[0][1], m[0][2]];
                let y = [m[1][0], m[1][1], m[1][2]];
                let z = [m[2][0], m[2][1], m[2][2]];
                if x.iter().zip(cross(y, z)).map(|(a, b)| a * b).sum::<f32>() < 0. {
                    for t in indices.chunks_exact_mut(3) {
                        t.swap(1, 2);
                    }
                }
                let uvs = r
                    .read_tex_coords(0)
                    .context("Every model group needs UV coordinates (TEXCOORD_0)")?
                    .into_f32()
                    .collect::<Vec<_>>();
                ensure!(
                    uvs.len() == positions.len() && uvs.iter().flatten().all(|f| f.is_finite()),
                    "Model UV coordinates are invalid"
                );
                let normals = if let Some(n) = r.read_normals() {
                    let n = n.map(|n| game_axes(normal(m, n))).collect::<Vec<_>>();
                    ensure!(n.len() == positions.len(), "Model normal count differs");
                    n
                } else {
                    let mut n = vec![[0.; 3]; positions.len()];
                    for t in indices.chunks_exact(3) {
                        let v = cross(
                            sub(positions[t[1] as usize], positions[t[0] as usize]),
                            sub(positions[t[2] as usize], positions[t[0] as usize]),
                        );
                        for i in t {
                            for (k, component) in v.iter().enumerate() {
                                n[*i as usize][k] += component;
                            }
                        }
                    }
                    n.into_iter().map(unit).collect()
                };
                let material = primitive.material();
                ensure!(
                    material.alpha_mode() != gltf::material::AlphaMode::Blend,
                    "Blended model materials are unsupported. Use opaque or alpha-cutout materials"
                );
                let pbr = material.pbr_metallic_roughness();
                ensure!(material.normal_texture().is_none() && material.occlusion_texture().is_none()
                    && material.emissive_texture().is_none() && pbr.metallic_roughness_texture().is_none()
                    && material.emissive_factor() == [0.0; 3],
                    "Model lighting maps are unsupported. Bake the appearance into base-color textures and remove normal, occlusion, emissive and metallic-roughness maps");
                let factor = pbr.base_color_factor();
                let cutout = material.alpha_mode() == gltf::material::AlphaMode::Mask;
                let cutoff = material.alpha_cutoff().unwrap_or(0.5);
                let key = TextureKey {
                    source: pbr
                        .base_color_texture()
                        .map(|info| info.texture().source().index()),
                    factor: factor.map(f32::to_bits),
                    cutout,
                    cutoff: cutoff.to_bits(),
                    policy: 1,
                };
                // Validate per-material UV selection even on a shared-image hit.
                if let Some(info) = pbr.base_color_texture() {
                    ensure!(info.tex_coord() == 0, "Use TEXCOORD_0 for model textures");
                }
                let image = if let Some(image) = textures.get(&key)? {
                    image
                } else {
                    let mut image = if let Some(info) = pbr.base_color_texture() {
                        ensure!(info.tex_coord() == 0, "Use TEXCOORD_0 for model textures");
                        let source = info.texture().source();
                        let bytes: std::borrow::Cow<'_, [u8]> = match source.source() {
                            ImageSource::View { view, .. } => {
                                let b = &buffers[view.buffer().index()];
                                let end = view
                                    .offset()
                                    .checked_add(view.length())
                                    .context("Image length overflow")?;
                                b.get(view.offset()..end)
                                    .context("Truncated embedded model image")?
                                    .into()
                            }
                            ImageSource::Uri { uri: s, .. } => uri(s, base, files)?.into(),
                        };
                        let format = image::guess_format(&bytes)?;
                        ensure!(
                            [image::ImageFormat::Png, image::ImageFormat::Jpeg].contains(&format),
                            "Use PNG or JPEG model textures"
                        );
                        let reader = image::ImageReader::with_format(Cursor::new(&bytes), format);
                        let (w, h) = reader.into_dimensions()?;
                        textures.reserve(w, h)?;
                        image::load_from_memory_with_format(&bytes, format)?.into_rgba8()
                    } else {
                        textures.reserve(4, 4)?;
                        image::RgbaImage::from_pixel(4, 4, image::Rgba([255; 4]))
                    };
                    if !image.width().is_power_of_two() || !image.height().is_power_of_two() {
                        image = image::imageops::resize(
                            &image,
                            image.width().next_power_of_two(),
                            image.height().next_power_of_two(),
                            image::imageops::FilterType::Lanczos3,
                        );
                    }
                    for p in image.pixels_mut() {
                        for i in 0..3 {
                            let s = p[i] as f32 / 255.;
                            let linear = if s <= 0.04045 {
                                s / 12.92
                            } else {
                                ((s + 0.055) / 1.055).powf(2.4)
                            } * factor[i];
                            let s = if linear <= 0.0031308 {
                                linear * 12.92
                            } else {
                                1.055 * linear.powf(1. / 2.4) - 0.055
                            };
                            p[i] = (s.clamp(0., 1.) * 255.).round() as u8;
                        }
                        p[3] = if cutout && p[3] as f32 / 255. * factor[3] < cutoff {
                            0
                        } else {
                            255
                        };
                    }
                    let image = Rc::new(image);
                    textures.prepared.insert(key, image.clone());
                    image
                };
                parts.push(Part {
                    name: format!("part{}", parts.len()),
                    positions,
                    normals,
                    uvs,
                    indices,
                    image,
                    cutout,
                    double_sided: material.double_sided(),
                });
            }
        }
        for child in node.children() {
            visit(
                child,
                m,
                depth + 1,
                buffers,
                base,
                files,
                parts,
                visited,
                textures,
                character,
            )?;
        }
        Ok(())
    }
    for node in scene.nodes() {
        visit(
            node,
            ID,
            0,
            &buffers,
            &base,
            &files,
            &mut parts,
            &mut visited,
            &mut textures,
            character,
        )?;
    }
    ensure!(!parts.is_empty(), "Model contains no mesh triangles");
    ensure!(
        parts.iter().map(|p| p.image.len()).sum::<usize>() <= 128 * 1024 * 1024,
        "Decoded model textures exceed 128 MiB"
    );
    Ok(parts)
}
fn bounds(points: impl Iterator<Item = [f32; 3]>) -> ([f32; 3], [f32; 3]) {
    let (mut min, mut max) = ([f32::INFINITY; 3], [f32::NEG_INFINITY; 3]);
    for p in points {
        for i in 0..3 {
            min[i] = min[i].min(p[i]);
            max[i] = max[i].max(p[i]);
        }
    }
    (min, max)
}
// Canonical AM body at source scale, floor aligned, with a proportional head.
// The identical value and assembly recipe are published in object-reference.json.
pub const REFERENCE_HEIGHT: f32 = 1.8788737;
fn cached(path: &str) -> Result<std::rc::Rc<Vec<Part>>> {
    let key = format!("{path}:{}", assets::cache_identity(path)?);
    if let Some(model) = crate::caches::model_get(&key) {
        return Ok(model);
    }
    let model = std::rc::Rc::new(load_mode(path, false)?);
    crate::caches::model_insert(&key, model.clone());
    Ok(model)
}
pub(crate) fn character_parts(path: &str) -> Result<std::rc::Rc<Vec<Part>>> {
    let key = format!("sim:{path}:{}", assets::cache_identity(path)?);
    if let Some(model) = crate::caches::model_get(&key) {
        return Ok(model);
    }
    let model = std::rc::Rc::new(load_mode(path, true)?);
    crate::caches::model_insert(&key, model.clone());
    Ok(model)
}
pub(crate) fn template_bounds(nodes: &[Node]) -> Result<([f32; 3], [f32; 3])> {
    let visible = crate::object::preview_meshes(nodes)?;
    let mut points = Vec::new();
    for n in nodes.iter().filter(|n| visible.contains(&n.key())) {
        let c = n.collection()?;
        if let ResourceData::Mesh(m) = &c.entries[0].data {
            points.extend(m.bounding_mesh.vertices.iter().map(|v| [v.x, v.y, v.z]));
        }
    }
    let (min, max) = bounds(points.into_iter());
    ensure!(
        min.iter().chain(max.iter()).all(|v| v.is_finite()),
        "Decoration has no valid selection bounds"
    );
    Ok((min, max))
}
#[derive(Clone)]
struct Layout {
    scale: f32,
    angle: f32,
    source_center: [f32; 3],
    anchor: [f32; 3],
    info: Value,
}
impl Layout {
    fn position(&self, p: [f32; 3]) -> [f32; 3] {
        let x = p[0] - self.source_center[0];
        let y = p[1] - self.source_center[1];
        [
            self.anchor[0] + self.scale * (x * self.angle.cos() - y * self.angle.sin()),
            self.anchor[1] + self.scale * (x * self.angle.sin() + y * self.angle.cos()),
            self.anchor[2] + self.scale * (p[2] - self.source_center[2]),
        ]
    }
}
// Test the actual union of occupied tiles, not just its enclosing rectangle.
fn floor_covered(lo: [f32; 2], hi: [f32; 2], tiles: &[[f32; 2]], tolerance: f32) -> bool {
    let mut splits = [vec![lo[0], hi[0]], vec![lo[1], hi[1]]];
    for tile in tiles {
        for axis in 0..2 {
            for edge in [tile[axis] - 0.5, tile[axis] + 0.5] {
                if edge > lo[axis] && edge < hi[axis] {
                    splits[axis].push(edge);
                }
            }
        }
    }
    for axis in &mut splits {
        axis.sort_by(f32::total_cmp);
    }
    splits[0].windows(2).all(|x| {
        splits[1].windows(2).all(|y| {
            let center = [(x[0] + x[1]) / 2., (y[0] + y[1]) / 2.];
            tiles.iter().any(|t| {
                (center[0] - t[0]).abs() <= 0.5 + tolerance
                    && (center[1] - t[1]).abs() <= 0.5 + tolerance
            })
        })
    })
}
fn fitted_scale(
    lo: [f32; 3],
    hi: [f32; 3],
    anchor: [f32; 3],
    cap: f32,
    tiles: &[[f32; 2]],
) -> Result<f32> {
    ensure!(floor_covered([anchor[0], anchor[1]], [anchor[0], anchor[1]], tiles, 0.),
        "The template's placement anchor is outside its occupied tiles. Choose another template or set the height manually");
    let fits = |scale: f32| {
        // Include the fixed anchor so shrinking is monotonic even for an
        // asymmetric model whose rotated bounds do not contain their origin.
        let a = [
            anchor[0] + lo[0].min(0.) * scale,
            anchor[1] + lo[1].min(0.) * scale,
        ];
        let b = [
            anchor[0] + hi[0].max(0.) * scale,
            anchor[1] + hi[1].max(0.) * scale,
        ];
        floor_covered(a, b, tiles, 0.)
    };
    if fits(cap) {
        return Ok(cap);
    }
    let (mut low, mut high) = (0., cap);
    for _ in 0..32 {
        let mid = (low + high) / 2.;
        if fits(mid) {
            low = mid;
        } else {
            high = mid;
        }
    }
    // Leave a tiny margin for f32 vertex transforms at the outside tile edge.
    Ok(low * (1. - 2e-6))
}
fn calculate(nodes: &[Node], job: &Value, parts: &[Part]) -> Result<Layout> {
    use sha2::{Digest, Sha256};
    let visible = crate::object::preview_meshes(nodes)?;
    ensure!(visible.len() == 1, "This template has multiple simultaneous visible meshes. Use a standard decoration or a template with one visible model route for independent sizing");
    for node in nodes.iter().filter(|n| n.key().0 == 0xe519c933) {
        let scene = object_scene::read(&node.bytes)?;
        ensure!(scene.transforms.iter().all(|t| t[..6].iter().all(|v| v.abs() < 1e-6) && (t[6].abs()-1.).abs() < 1e-6),
            "This template has custom scene transforms. Use a standard decoration or an unchanged decoration scene for independent sizing");
    }
    let fit = if job["fit_to_template"].is_null() {
        false
    } else {
        job["fit_to_template"]
            .as_bool()
            .context("Fit within template must be enabled or disabled")?
    };
    let angle = job["rotation"].as_f64().unwrap_or(0.) as f32;
    let (tmin, tmax) = template_bounds(nodes)?;
    let template_height = tmax[2] - tmin[2];
    let stored_fit = job["height_from_fit"] == true;
    let mut height = if fit {
        template_height
    } else {
        job["target_height"]
            .as_f64()
            .context("Set the target height for this batch")? as f32
    };
    ensure!(height.is_finite() && height > 1e-6 &&
        (fit || (REFERENCE_HEIGHT * 0.1..=REFERENCE_HEIGHT * 3.).contains(&height) || height == template_height ||
            (stored_fit && height <= template_height.max(REFERENCE_HEIGHT * 3.))),
        "Height must be between 10% and 300% of the reference Sim, match the template's original height, or retain a valid fitted height");
    ensure!(
        angle.is_finite() && (-180.0..=180.0).contains(&angle),
        "Rotation must be between -180 and 180 degrees"
    );
    let (min, max) = bounds(parts.iter().flat_map(|p| p.positions.iter().copied()));
    ensure!(max[2] - min[2] > 1e-6, "The model has no measurable height");
    let placement = crate::object::placement(nodes)?;
    let tiles = placement["tiles"]
        .as_array()
        .context("Invalid placement metadata")?
        .iter()
        .map(|t| {
            Ok([
                t[0].as_f64().context("Invalid placement tile")? as f32,
                t[1].as_f64().context("Invalid placement tile")? as f32,
            ])
        })
        .collect::<Result<Vec<_>>>()?;
    let mut layout = Layout {
        scale: height / (max[2] - min[2]),
        angle: angle.to_radians(),
        source_center: [(min[0] + max[0]) / 2., (min[1] + max[1]) / 2., min[2]],
        anchor: [(tmin[0] + tmax[0]) / 2., (tmin[1] + tmax[1]) / 2., tmin[2]],
        info: Value::Null,
    };
    if fit {
        ensure!(placement["known"] == true, "The template's occupied floor area is unknown. Choose a standard template or set the height manually");
        let unit = Layout {
            scale: 1.,
            anchor: [0.; 3],
            ..layout.clone()
        };
        let (lo, hi) = bounds(
            parts
                .iter()
                .flat_map(|p| p.positions.iter().map(|v| unit.position(*v))),
        );
        let scale = fitted_scale(lo, hi, layout.anchor, layout.scale, &tiles)?;
        height = (scale * (max[2] - min[2])).min(template_height);
        ensure!(height > 1e-6, "This model cannot fit the template's occupied tiles at a usable size. Choose a wider template");
        layout.scale = height / (max[2] - min[2]);
    }
    let (lo, hi) = bounds(
        parts
            .iter()
            .flat_map(|p| p.positions.iter().map(|v| layout.position(*v))),
    );
    let covered = placement["known"] == true
        && floor_covered([lo[0], lo[1]], [hi[0], hi[1]], &tiles, 0.00001);
    ensure!(!fit || covered, "The fitted model could not be contained in the template's occupied tiles. Choose another template");
    let token = format!(
        "{:x}",
        Sha256::digest(serde_json::to_vec(&json!({"height":height,"angle":angle,
        "model": assets::digest(job["model_file"].as_str().unwrap())?,
        "template":assets::digest("object-template")?}))?)
    );
    let zero = layout.position([0.; 3]);
    let a = layout.angle;
    let s = layout.scale;
    // GMDC export uses [-x,z,y]. This matrix transforms that same preview basis.
    let matrix = [
        s * a.cos(),
        0.,
        -s * a.sin(),
        0.,
        0.,
        s,
        0.,
        0.,
        s * a.sin(),
        0.,
        s * a.cos(),
        0.,
        -zero[0],
        zero[2],
        zero[1],
        1.,
    ];
    layout.info = json!({"version":2,"reference_height":REFERENCE_HEIGHT,"height":height,"scale":s,"fit_to_template":fit,"height_from_fit":stored_fit,"template_height":template_height,
        "matrix":matrix,"game_min":lo,"game_max":hi,"dimensions":{"width":hi[0]-lo[0],"height":hi[2]-lo[2],"depth":hi[1]-lo[1]},
        "placement":placement,"requires_acknowledgement":!covered,"signature":token,
        "warning": if covered {""} else if placement["known"] != true {"Placement coverage is unknown for this template. The game will keep the template's placement and routing rules. The visible model may overlap other objects or Sims."} else {"The model extends beyond the template's occupied floor area. Placement and routing still follow the template, so other objects or Sims may overlap it. Choose a wider template or acknowledge this to continue."}});
    Ok(layout)
}
pub fn layout(job: &Value) -> Result<Value> {
    let (_, nodes) = resources::load_raw(std::path::Path::new("object-template"))?;
    let parts = cached(
        job["model_file"]
            .as_str()
            .context("Choose a GLB or model ZIP")?,
    )?;
    Ok(calculate(&nodes, job, &parts)?.info)
}
fn sized<T: std::fmt::Debug + Default>(data: Vec<T>) -> dbpf::common::SizedVec<u32, T> {
    let mut v = dbpf::common::SizedVec::default();
    v.data = data;
    v
}
fn buffer(kind: AttributeType, values: Vec<u8>, count: usize, width: usize) -> AttributeBuffer {
    AttributeBuffer {
        number_elements: count as u32,
        binding: AttributeBinding {
            binding_type: kind,
            binding_slot: 0,
        },
        block_format: if width == 3 {
            BlockFormat::F32Vec3
        } else {
            BlockFormat::F32Vec2
        },
        data: sized(values),
        ..Default::default()
    }
}
fn encoded<const N: usize>(v: &[[f32; N]]) -> Vec<u8> {
    v.iter().flatten().flat_map(|v| v.to_le_bytes()).collect()
}
pub fn replace(nodes: &mut Vec<Node>, job: &Value) -> Result<()> {
    let mut parts = (*cached(
        job["model_file"]
            .as_str()
            .context("Choose a GLB or model ZIP")?,
    )?)
    .clone();
    if !job["target_height"].is_null() || job["fit_to_template"] == true {
        let layout = calculate(nodes, job, &parts)?;
        ensure!(layout.info["requires_acknowledgement"] != true || job["placement_ack"] == layout.info["signature"],
            "Review and acknowledge the placement warning for this model, height, rotation and template before building");
        for part in &mut parts {
            for p in &mut part.positions {
                *p = layout.position(*p);
            }
            for n in &mut part.normals {
                let x = n[0];
                let y = n[1];
                n[0] = x * layout.angle.cos() - y * layout.angle.sin();
                n[1] = x * layout.angle.sin() + y * layout.angle.cos();
            }
        }
    } else {
        let visible = crate::object::preview_meshes(nodes)?;
        let source = nodes
            .iter()
            .find(|n| visible.contains(&n.key()))
            .context("Decoration has no editable mesh")?
            .collection()?;
        let ResourceData::Mesh(source) = &source.entries[0].data else {
            bail!("Invalid decoration mesh")
        };
        let (target_min, target_max) = bounds(
            source
                .bounding_mesh
                .vertices
                .iter()
                .map(|v| [v.x, v.y, v.z]),
        );
        ensure!(
            target_min
                .iter()
                .chain(target_max.iter())
                .all(|v| v.is_finite()),
            "Decoration has no valid selection bounds"
        );
        let (min, max) = bounds(parts.iter().flat_map(|p| p.positions.iter().copied()));
        let multiplier = job["scale"].as_f64().unwrap_or(1.) as f32;
        let angle = job["rotation"].as_f64().unwrap_or(0.) as f32;
        ensure!(
            (0.1..=1.).contains(&multiplier) && (-180.0..=180.0).contains(&angle),
            "Scale must be between 0.1 and 1, rotation between -180 and 180"
        );
        let angle = angle.to_radians();
        for part in &mut parts {
            for p in &mut part.positions {
                let x = p[0] - (min[0] + max[0]) / 2.;
                let y = p[1] - (min[1] + max[1]) / 2.;
                p[0] = x * angle.cos() - y * angle.sin();
                p[1] = x * angle.sin() + y * angle.cos();
            }
            for n in &mut part.normals {
                let x = n[0];
                let y = n[1];
                n[0] = x * angle.cos() - y * angle.sin();
                n[1] = x * angle.sin() + y * angle.cos();
            }
        }
        let (min, max) = bounds(parts.iter().flat_map(|p| p.positions.iter().copied()));
        let scale = (0..3)
            .filter(|i| max[*i] - min[*i] > 1e-6)
            .map(|i| (target_max[i] - target_min[i]) / (max[i] - min[i]))
            .fold(f32::INFINITY, f32::min)
            * multiplier;
        ensure!(
            scale.is_finite() && scale > 0.,
            "Model or decoration bounds are degenerate"
        );
        for part in &mut parts {
            for v in &mut part.positions {
                for i in 0..3 {
                    v[i] = (v[i]
                        - if i == 2 {
                            min[i]
                        } else {
                            (min[i] + max[i]) / 2.
                        })
                        * scale
                        + if i == 2 {
                            target_min[i]
                        } else {
                            (target_min[i] + target_max[i]) / 2.
                        };
                }
            }
        }
    }
    let prefix = job["identities"]["prefix"].as_str().unwrap();
    let mut materials = Vec::new();
    let mut replacements = Vec::new();
    let material=nodes.iter().find(|n|n.key().0==TXMT&&n.collection().is_ok_and(|c|matches!(&c.entries[0].data,ResourceData::Material(m) if m.material_type.to_string()=="StandardMaterial"))).context("No standard decoration material")?.clone();
    let texture = nodes
        .iter()
        .find(|n| n.key().0 == TXTR)
        .context("No decoration texture")?
        .clone();
    for (i, part) in parts.iter().enumerate() {
        let texture_name = format!("{prefix}-model-{i}_txtr");
        let material_name = format!("{prefix}-model-{i}_txmt");
        let mut n = texture.clone();
        let mut c = n.collection()?;
        let ResourceData::Texture(t) = &mut c.entries[0].data else {
            bail!("Invalid texture")
        };
        t.file_name.name = texture_name.clone().into();
        t.compress_replace(
            DecodedTexture {
                width: part.image.width() as usize,
                height: part.image.height() as usize,
                data: part.image.as_raw().clone(),
            },
            Some(TextureFormat::RawARGB32),
        );
        t.add_max_mip_levels(Some(127));
        crate::texture_encoding::recompress(
            t,
            TextureFormat::DXT3,
            crate::texture_encoding::Encoder::from_job(job)?,
        )?;
        n.set(&c)?;
        n.entry.group_id = 0x1c050000;
        n.entry.instance_id = InstanceId {
            id: resources::named_id(&texture_name),
        };
        replacements.push(n);
        let mut n = material.clone();
        let mut c = n.collection()?;
        let ResourceData::Material(m) = &mut c.entries[0].data else {
            bail!("Invalid material")
        };
        m.file_name.name = material_name.clone().into();
        m.material_description = material_name.clone().into();
        let base = format!("##0x1c050000!{}", texture_name.trim_end_matches("_txtr"));
        m.names = vec![base.clone().into()];
        let values = [
            ("stdMatBaseTextureName", base.as_str()),
            ("stdMatBaseTextureEnabled", "true"),
            ("stdMatNormalMapTextureEnabled", "false"),
            ("stdMatEnvCubeTextureEnabled", "false"),
            ("stdMatAlphaBlendMode", "none"),
            (
                "stdMatAlphaTestEnabled",
                if part.cutout { "true" } else { "false" },
            ),
            ("stdMatAlphaRefValue", "0.5"),
            ("stdMatDiffCoef", "1,1,1"),
            (
                "stdMatCullMode",
                if part.double_sided {
                    "none"
                } else {
                    "cullClockwise"
                },
            ),
        ];
        m.properties
            .retain(|p| !p.name.to_string().ends_with("TextureName"));
        for (name, value) in values {
            if let Some(p) = m.properties.iter_mut().find(|p| p.name.to_string() == name) {
                p.value = value.to_string().into();
            } else {
                m.properties.push(Property {
                    name: name.into(),
                    value: value.to_string().into(),
                });
            }
        }
        n.set(&c)?;
        n.entry.group_id = 0x1c050000;
        n.entry.instance_id = InstanceId {
            id: resources::named_id(&material_name),
        };
        replacements.push(n);
        materials.push((
            part.name.clone(),
            format!("##0x1c050000!{}", material_name.trim_end_matches("_txmt")),
        ));
    }
    for n in nodes.iter_mut() {
        match n.key().0 {
            GMDC => {
                let mut c = n.collection()?;
                let ResourceData::Mesh(m) = &mut c.entries[0].data else {
                    bail!("Invalid mesh")
                };
                let name = m.file_name.clone();
                *m = GeometricDataContainer {
                    file_name: name,
                    ..Default::default()
                };
                for (i, p) in parts.iter().enumerate() {
                    let offset = m.attribute_buffers.len();
                    let count = p.positions.len();
                    m.attribute_buffers.extend([
                        buffer(AttributeType::Positions, encoded(&p.positions), count, 3),
                        buffer(AttributeType::Normals, encoded(&p.normals), count, 3),
                        buffer(AttributeType::TexCoords, encoded(&p.uvs), count, 2),
                    ]);
                    m.attribute_groups.push(AttributeGroup {
                        attributes: sized(
                            (offset..offset + 3)
                                .map(|i| Reference(i as u32))
                                .collect::<Vec<_>>(),
                        ),
                        number_elements: count as u32,
                        referenced_active: 3,
                        ..Default::default()
                    });
                    m.meshes.push(Mesh {
                        attribute_group_index: i as u32,
                        name: p.name.clone().into(),
                        indices: sized(p.indices.iter().map(|i| Reference(*i)).collect::<Vec<_>>()),
                        opacity: -1,
                        ..Default::default()
                    });
                }
                let (lo, hi) = bounds(parts.iter().flat_map(|p| p.positions.iter().copied()));
                m.bounding_mesh = BoundingMesh {
                    vertices: (0..8)
                        .map(|i| Vertex {
                            x: if i & 1 == 0 { lo[0] } else { hi[0] },
                            y: if i & 2 == 0 { lo[1] } else { hi[1] },
                            z: if i & 4 == 0 { lo[2] } else { hi[2] },
                        })
                        .collect(),
                    faces: [
                        0, 2, 1, 1, 2, 3, 4, 5, 6, 5, 7, 6, 0, 1, 4, 1, 5, 4, 2, 6, 3, 3, 6, 7, 0,
                        4, 2, 2, 4, 6, 1, 3, 5, 3, 7, 5,
                    ]
                    .into_iter()
                    .map(Reference)
                    .collect(),
                };
                n.set(&c)?;
            }
            SHPE => {
                let s = object_scene::read(&n.bytes)?;
                let (start, end) = s
                    .parts_range
                    .context("Decoration shape has no material slots")?;
                let mut b = (materials.len() as u32).to_le_bytes().to_vec();
                for (subset, material) in &materials {
                    b.extend(object_scene::string(subset));
                    b.extend(object_scene::string(material));
                    b.extend([0u8; 9]);
                }
                n.bytes.splice(start..end, b);
            }
            0x7ba3838c => {
                let s = object_scene::read(&n.bytes)?;
                for (a, b) in s.design_ranges.into_iter().rev() {
                    n.bytes.splice(a..b, 0u32.to_le_bytes());
                }
            }
            _ => {}
        }
    }
    // Imported decor has one appearance, with no inherited finish overrides.
    nodes.retain(|n| n.key().0 != MMAT);
    nodes.extend(replacements);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn textured_glb(materials: Vec<Value>) -> Vec<u8> {
        let positions = [[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]];
        let mut bin = encoded(&positions);
        bin.extend(encoded(&[[0., 0.], [1., 0.], [0., 1.]]));
        let image = image::RgbaImage::from_fn(3, 5, |x, y| {
            image::Rgba([(x * 70 + 10) as u8, (y * 40) as u8, 113, (y * 50) as u8])
        });
        let mut png = Cursor::new(Vec::new());
        image.write_to(&mut png, image::ImageFormat::Png).unwrap();
        let mut json = serde_json::to_vec(&json!({
            "asset":{"version":"2.0"}, "scene":0, "scenes":[{"nodes":[0]}],
            "nodes":[{"mesh":0}], "buffers":[{"byteLength":bin.len()}],
            "bufferViews":[{"buffer":0,"byteOffset":0,"byteLength":36},
                {"buffer":0,"byteOffset":36,"byteLength":24}],
            "accessors":[{"bufferView":0,"componentType":5126,"count":3,"type":"VEC3","min":[0,0,0],"max":[1,1,0]},
                {"bufferView":1,"componentType":5126,"count":3,"type":"VEC2"}],
            "images":[{"uri":format!("data:image/png;base64,{}", STANDARD.encode(png.into_inner()))}],
            "textures":[{"source":0}], "materials":materials,
            "meshes":[{"primitives":(0..materials.len()).map(|i| json!({
                "attributes":{"POSITION":0,"TEXCOORD_0":1},"material":i
            })).collect::<Vec<_>>()}]
        })).unwrap();
        while json.len() % 4 != 0 {
            json.push(b' ');
        }
        let mut glb = b"glTF".to_vec();
        glb.extend(2u32.to_le_bytes());
        glb.extend(((12 + 8 + json.len() + 8 + bin.len()) as u32).to_le_bytes());
        glb.extend((json.len() as u32).to_le_bytes());
        glb.extend(b"JSON");
        glb.extend(json);
        glb.extend((bin.len() as u32).to_le_bytes());
        glb.extend(b"BIN\0");
        glb.extend(bin);
        glb
    }

    #[test]
    fn model_shares_only_identical_preparation_and_reuses_immutable_cache() -> Result<()> {
        let a = json!({"pbrMetallicRoughness":{"baseColorTexture":{"index":0},"baseColorFactor":[0.4,0.7,0.2,0.8]},"alphaMode":"MASK","alphaCutoff":0.3});
        let mut b = a.clone();
        b["doubleSided"] = json!(true);
        let mut c = a.clone();
        c["alphaCutoff"] = json!(0.7);
        let mut d = a.clone();
        d["pbrMetallicRoughness"]["baseColorFactor"][0] = json!(0.6);
        let mut state = assets::Assets::default();
        assets::with(&mut state, || {
            assets::put_owned(
                "model.glb",
                textured_glb(vec![a.clone(), b, c.clone(), d.clone()]),
            )?;
            let parts = cached("model.glb")?;
            assert!(Rc::ptr_eq(&parts, &cached("model.glb")?));
            assert!(Rc::ptr_eq(&parts[0].image, &parts[1].image));
            assert!(!Rc::ptr_eq(&parts[0].image, &parts[2].image));
            assert!(!Rc::ptr_eq(&parts[0].image, &parts[3].image));
            assert!(!parts[0].double_sided && parts[1].double_sided);
            assert_eq!(parts[0].image.dimensions(), (4, 8));
            // Separate imports exercise identical resize/color/alpha work without sharing.
            for (i, material) in [a.clone(), c, d].into_iter().enumerate() {
                assets::put_owned("single.glb", textured_glb(vec![material]))?;
                let single = load_mode("single.glb", false)?;
                let part = &parts[[0, 2, 3][i]];
                assert_eq!(single[0].image.as_raw(), part.image.as_raw());
                assert_eq!(single[0].positions, part.positions);
                assert_eq!(single[0].normals, part.normals);
                assert_eq!(single[0].uvs, part.uvs);
                assert_eq!(single[0].indices, part.indices);
            }
            let character = character_parts("model.glb")?;
            assert!(!Rc::ptr_eq(&parts, &character));
            assets::write("model.glb", b"invalid replacement")?;
            assert!(cached("model.glb").is_err());
            assets::put_owned("model.glb", textured_glb(vec![a.clone()]))?;
            assert_eq!(cached("model.glb")?.len(), 1);
            assets::take("model.glb")?;
            assert!(cached("model.glb").is_err());
            let mut wrong_uv = a.clone();
            wrong_uv["pbrMetallicRoughness"]["baseColorTexture"]["texCoord"] = json!(1);
            assets::put_owned("model.glb", textured_glb(vec![a, wrong_uv]))?;
            assert!(cached("model.glb").is_err());
            Ok(())
        })
    }

    #[test]
    fn texture_budget_counts_resized_bytes_and_shared_parts() -> Result<()> {
        let mut textures = Textures::default();
        for _ in 0..8 {
            textures.reserve(1025, 1025)?;
        }
        assert_eq!(textures.logical_bytes, TEXTURE_BUDGET);
        assert!(textures.reserve(1, 1).is_err());
        assert!(Textures::default().reserve(2049, 1).is_err());
        let key = TextureKey {
            source: None,
            factor: [1f32.to_bits(); 4],
            cutout: false,
            cutoff: 0.5f32.to_bits(),
            policy: 1,
        };
        let image = Rc::new(image::RgbaImage::new(4, 4));
        textures.logical_bytes = TEXTURE_BUDGET - image.len();
        textures.prepared.insert(key, image.clone());
        assert!(Rc::ptr_eq(&textures.get(&key)?.unwrap(), &image));
        assert!(textures.get(&key).is_err());
        Ok(())
    }
    #[test]
    fn fit_respects_tile_edges_and_offset_anchors() {
        let tiles = [[0., 0.]];
        let scale =
            fitted_scale([-0.25, -0.75, 0.], [0.25, 0.75, 2.], [0.; 3], 1., &tiles).unwrap();
        assert!((scale - 2. / 3.).abs() < 1e-5);
        assert!(floor_covered(
            [-0.25 * scale, -0.75 * scale],
            [0.25 * scale, 0.75 * scale],
            &tiles,
            0.00001
        ));
        let shifted =
            fitted_scale([-0.2, -0.2, 0.], [0.2, 0.2, 1.], [0.4, 0., 0.], 1., &tiles).unwrap();
        assert!((shifted - 0.5).abs() < 1e-5);
        assert_eq!(
            fitted_scale([-0.1, -0.1, 0.], [0.1, 0.1, 1.], [0.; 3], 1., &tiles).unwrap(),
            1.
        );
    }
    #[test]
    fn fit_uses_tile_union_without_filling_holes() {
        let adjacent = [[0., 0.], [1., 0.]];
        assert_eq!(
            fitted_scale([-1., -0.4, 0.], [1., 0.4, 1.], [0.5, 0., 0.], 1., &adjacent).unwrap(),
            1.
        );
        let corner = [[0., 0.], [1., 0.], [0., 1.]];
        assert!(!floor_covered([-0.1, -0.1], [1.1, 1.1], &corner, 0.));
        let s = fitted_scale([-0.1, -0.1, 0.], [1.1, 1.1, 1.], [0.; 3], 1., &corner).unwrap();
        assert!((s - 0.5 / 1.1).abs() < 1e-5);
        assert!(floor_covered(
            [-0.1 * s, -0.1 * s],
            [1.1 * s, 1.1 * s],
            &corner,
            0.00001
        ));
        assert!(fitted_scale(
            [-0.1, -0.1, 0.],
            [0.1, 0.1, 1.],
            [0.; 3],
            1.,
            &[[-1., 0.], [1., 0.]]
        )
        .is_err());
    }
    #[test]
    fn relative_model_paths_only() {
        for p in [
            "../x",
            "/tmp/x",
            "https://example.org/a",
            "x\\y",
            "a/%2e%2e/z",
        ] {
            assert!(safe_path(p).is_err());
        }
        assert!(safe_path("textures/wood.png").is_ok());
    }
    #[test]
    fn coordinate_basis_preserves_handedness() {
        assert_eq!(game_axes([1., 2., 3.]), [-1., 3., 2.]);
        assert_eq!(
            cross(game_axes([1., 0., 0.]), game_axes([0., 1., 0.])),
            game_axes([0., 0., 1.])
        );
    }
}
