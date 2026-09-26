//! Explicit AM Everyday-only test export. Stock face, hair and other outfits survive.
use crate::{
    assets,
    core::resources::{self, Key, Node},
    object_model, object_scene as scene, sim, sim_fit, sim_guided,
};
use anyhow::{bail, ensure, Context, Result};
use binrw::BinRead;
use dbpf::{
    common::SizedVec,
    header_v1::InstanceId,
    internal_file::{
        resource_collection::{
            geometric_data_container::{
                math::Vertex, AttributeBinding, AttributeBuffer, AttributeGroup,
                AttributeType as A, BlockFormat as F, BoundingMesh, GeometricDataContainer, Mesh,
                Reference,
            },
            material_definition::Property,
            texture_resource::{decoded_texture::DecodedTexture, TextureFormat},
            ResourceData,
        },
        sim_outfits::{Entry, SimOutfits},
        text_list::{TextList, VersionedTextList},
    },
};
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, BTreeSet, HashMap},
    io::Cursor,
    path::Path,
};
const GMDC: u32 = 0xac4f8687;
const TXMT: u32 = 0x49596978;
const TXTR: u32 = 0x1c4a276c;
const GZPS: u32 = 0xebcf3e27;
const SHPE: u32 = 0xfc6eb1f7;
const SOURCE_HASH: &str = "c55eea764ee0de814781ccd956969c3a70b34ae81e9c574a4a1658f35fbdae1e";
fn sized<T: Default + std::fmt::Debug>(data: Vec<T>) -> SizedVec<u32, T> {
    let mut v = SizedVec::default();
    v.data = data;
    v
}
fn slice<'a>(bytes: &'a [u8], d: &Value) -> Result<&'a [u8]> {
    let o = d["offset"].as_u64().context("Missing fitted offset")? as usize;
    let n = d["length"].as_u64().context("Missing fitted count")? as usize;
    bytes
        .get(
            o..o.checked_add(n.checked_mul(4).context("Invalid fitted size")?)
                .context("Invalid fitted offset")?,
        )
        .context("Damaged fitted buffer")
}
fn floats(bytes: &[u8], d: &Value) -> Result<Vec<f32>> {
    Ok(slice(bytes, d)?
        .chunks_exact(4)
        .map(|x| f32::from_le_bytes(x.try_into().unwrap()))
        .collect())
}
fn ints(bytes: &[u8], d: &Value) -> Result<Vec<u32>> {
    Ok(slice(bytes, d)?
        .chunks_exact(4)
        .map(|x| u32::from_le_bytes(x.try_into().unwrap()))
        .collect())
}
fn attr(kind: A, slot: u32, format: F, bytes: Vec<u8>, count: usize) -> AttributeBuffer {
    AttributeBuffer {
        number_elements: count as u32,
        binding: AttributeBinding {
            binding_type: kind,
            binding_slot: slot,
        },
        block_format: format,
        data: sized(bytes),
        ..Default::default()
    }
}
fn encoded(v: impl Iterator<Item = f32>) -> Vec<u8> {
    v.flat_map(f32::to_le_bytes).collect()
}
fn assign(n: &mut Node, k: Key) {
    n.entry.group_id = k.1;
    n.entry.instance_id = InstanceId { id: k.2 };
}
fn link(k: Key) -> Entry {
    Entry {
        type_id: k.0.into(),
        group_id: k.1,
        instance_id: InstanceId { id: k.2 },
    }
}
fn set_material(
    n: &mut Node,
    name: &str,
    texture: &str,
    cutout: bool,
    two_sided: bool,
) -> Result<()> {
    let mut c = n.collection()?;
    let ResourceData::Material(m) = &mut c.entries[0].data else {
        bail!("Invalid body material")
    };
    m.file_name.name = name.into();
    m.material_description = name.into();
    m.material_type = "SimStandardMaterial".into();
    m.names = vec![texture.into()];
    m.properties
        .retain(|p| !p.name.to_string().ends_with("TextureName"));
    for (k, v) in [
        ("stdMatBaseTextureName", texture),
        ("stdMatBaseTextureEnabled", "true"),
        ("stdMatNormalMapTextureEnabled", "false"),
        ("stdMatEnvCubeTextureEnabled", "false"),
        ("stdMatDiffCoef", "1,1,1"),
        ("stdMatAlphaBlendMode", "none"),
        (
            "stdMatAlphaTestEnabled",
            if cutout { "true" } else { "false" },
        ),
        ("stdMatAlphaRefValue", "0.5"),
        (
            "stdMatCullMode",
            if two_sided { "none" } else { "cullClockwise" },
        ),
    ] {
        if let Some(p) = m.properties.iter_mut().find(|p| p.name.to_string() == k) {
            p.value = v.into()
        } else {
            m.properties.push(Property {
                name: k.into(),
                value: v.into(),
            })
        }
    }
    n.set(&c)
}
// Shared vertices are welded only when every authored and fitted attribute agrees.
// No decimation, UV movement or approximate position merging is performed.
fn append_mesh(
    m: &mut GeometricDataContainer,
    p: &Value,
    b: &[u8],
    part: usize,
) -> Result<Vec<String>> {
    let pos = floats(b, &p["positions"])?;
    let norms = floats(b, &p["normals"])?;
    let uv = floats(b, &p["uvs"])?;
    let joints = ints(b, &p["joints"])?;
    let weights = floats(b, &p["weights"])?;
    let head = ints(b, &p["head_vertices"])?;
    let indices = ints(b, &p["indices"])?;
    let count = pos.len() / 3;
    ensure!(
        norms.len() == count * 3
            && uv.len() == count * 2
            && joints.len() == count * 4
            && weights.len() == count * 4
            && head.len() == count,
        "Invalid fitted attribute counts"
    );
    let mut morphs = vec![];
    for d in p["morphs"].as_array().context("Missing fitted morphs")? {
        let binding = m
            .blend_group_bindings
            .iter()
            .position(|v| v.element.to_string() == d["name"].as_str().unwrap_or(""))
            .context("Unknown body morph binding")?;
        let values = floats(b, &d["positions"])?;
        ensure!(values.len() == count * 3, "Invalid fitted morph");
        morphs.push((binding, values));
    }
    let mut names = vec![];
    let (mut remap, mut source, mut faces) = (
        HashMap::<Vec<u32>, u32>::new(),
        Vec::<usize>::new(),
        Vec::<u32>::new(),
    );
    let flush = |m: &mut GeometricDataContainer,
                 source: &Vec<usize>,
                 faces: &Vec<u32>,
                 names: &mut Vec<String>|
     -> Result<()> {
        if faces.is_empty() {
            return Ok(());
        }
        let name = format!("body{part}_{}", names.len());
        let n = source.len();
        let start = m.attribute_buffers.len();
        let mut palette = BTreeSet::new();
        for i in source {
            for k in 0..4 {
                if weights[i * 4 + k] > 0. {
                    palette.insert(joints[i * 4 + k]);
                }
            }
        }
        let palette: Vec<u32> = palette.into_iter().collect();
        ensure!(
            palette.len() < 255 && palette.iter().all(|v| (*v as usize) < m.bones.len()),
            "Invalid body bone palette"
        );
        let mut keys = vec![];
        for i in source {
            for k in 0..4 {
                keys.push(if weights[i * 4 + k] > 0. {
                    palette
                        .iter()
                        .position(|v| *v == joints[i * 4 + k])
                        .unwrap() as u8
                } else {
                    255
                });
            }
        }
        m.attribute_buffers.extend([
            attr(
                A::Positions,
                0,
                F::F32Vec3,
                encoded(
                    source
                        .iter()
                        .flat_map(|i| pos[i * 3..i * 3 + 3].iter().copied()),
                ),
                n,
            ),
            attr(
                A::Normals,
                0,
                F::F32Vec3,
                encoded(
                    source
                        .iter()
                        .flat_map(|i| norms[i * 3..i * 3 + 3].iter().copied()),
                ),
                n,
            ),
            attr(
                A::TexCoords,
                0,
                F::F32Vec2,
                encoded(
                    source
                        .iter()
                        .flat_map(|i| uv[i * 2..i * 2 + 2].iter().copied()),
                ),
                n,
            ),
            attr(A::BoneKeys, 0, F::U8Vec4, keys, n),
            attr(
                A::BoneWeights,
                0,
                F::F32Vec3,
                encoded(
                    source
                        .iter()
                        .flat_map(|i| weights[i * 4..i * 4 + 3].iter().copied()),
                ),
                n,
            ),
        ]);
        ensure!(morphs.len() <= 4, "Too many body morphs");
        let mut blend = vec![0u8; n * 4];
        for (slot, (binding, d)) in morphs.iter().enumerate() {
            for i in 0..n {
                blend[i * 4 + slot] = *binding as u8
            }
            m.attribute_buffers.push(attr(
                A::PositionDeltas,
                slot as u32,
                F::F32Vec3,
                encoded(
                    source
                        .iter()
                        .flat_map(|i| d[i * 3..i * 3 + 3].iter().copied()),
                ),
                n,
            ));
        }
        m.attribute_buffers
            .push(attr(A::BlendKeys, 0, F::U8Vec4, blend, n));
        let end = m.attribute_buffers.len();
        m.attribute_groups.push(AttributeGroup {
            attributes: sized((start..end).map(|v| Reference(v as u32)).collect()),
            number_elements: n as u32,
            referenced_active: (end - start) as u32,
            ..Default::default()
        });
        m.meshes.push(Mesh {
            attribute_group_index: m.attribute_groups.len() as u32 - 1,
            name: name.clone().into(),
            indices: sized(faces.iter().copied().map(Reference).collect()),
            opacity: -1,
            bone_references: sized(palette.into_iter().map(Reference).collect()),
            ..Default::default()
        });
        names.push(name);
        Ok(())
    };
    for tri in indices.chunks_exact(3) {
        ensure!(
            tri.iter().all(|i| (*i as usize) < count),
            "Invalid fitted triangle"
        );
        // This explicitly body-only prototype does not export the rigid head.
        if tri.iter().any(|i| head[*i as usize] != 0) {
            continue;
        }
        if source.len() + 3 > 60000 {
            flush(m, &source, &faces, &mut names)?;
            remap.clear();
            source.clear();
            faces.clear();
        }
        for i in tri {
            let i = *i as usize;
            let mut key = vec![];
            key.extend(pos[i * 3..i * 3 + 3].iter().map(|v| v.to_bits()));
            key.extend(norms[i * 3..i * 3 + 3].iter().map(|v| v.to_bits()));
            key.extend(uv[i * 2..i * 2 + 2].iter().map(|v| v.to_bits()));
            key.extend(&joints[i * 4..i * 4 + 4]);
            key.extend(weights[i * 4..i * 4 + 4].iter().map(|v| v.to_bits()));
            for (_, d) in &morphs {
                key.extend(d[i * 3..i * 3 + 3].iter().map(|v| v.to_bits()));
            }
            let index = *remap.entry(key).or_insert_with(|| {
                source.push(i);
                source.len() as u32 - 1
            });
            faces.push(index);
        }
    }
    flush(m, &source, &faces, &mut names)?;
    Ok(names)
}
// GMDC has independent position/skin, normal and UV index streams. Packing
// these avoids duplicating skin and morph values at material/normal seams.
fn compact_attributes(m: &mut GeometricDataContainer) -> Result<()> {
    use dbpf::internal_file::resource_collection::geometric_data_container::IndexSet;
    for group in m.attribute_groups.iter_mut() {
        for set in [IndexSet::Main, IndexSet::Norms, IndexSet::UV] {
            let ids: Vec<_> = group
                .attributes
                .iter()
                .map(|r| r.0 as usize)
                .filter(|i| {
                    let target = match m.attribute_buffers[*i].binding.binding_type {
                        A::Normals => IndexSet::Norms,
                        A::TexCoords => IndexSet::UV,
                        _ => IndexSet::Main,
                    };
                    target == set
                })
                .collect();
            if ids.is_empty() {
                continue;
            }
            let stride: usize = ids
                .iter()
                .map(|i| m.attribute_buffers[*i].element_size())
                .sum();
            let mut unique = HashMap::<Vec<u8>, u32>::new();
            let mut sources = vec![];
            let mut indexes = vec![];
            for row in 0..group.number_elements as usize {
                let mut key = Vec::with_capacity(stride);
                for i in &ids {
                    let a = &m.attribute_buffers[*i];
                    let width = a.element_size();
                    key.extend_from_slice(&a.data[row * width..(row + 1) * width]);
                }
                let index = *unique.entry(key).or_insert_with(|| {
                    sources.push(row);
                    sources.len() as u32 - 1
                });
                indexes.push(Reference(index));
            }
            for i in &ids {
                m.attribute_buffers[*i].index_set = set.clone();
            }
            if sources.len() * stride + indexes.len() * 2 >= group.number_elements as usize * stride
            {
                continue;
            }
            for i in ids {
                let a = &mut m.attribute_buffers[i];
                let width = a.element_size();
                let packed: Vec<u8> = sources
                    .iter()
                    .flat_map(|row| a.data[row * width..(row + 1) * width].iter().copied())
                    .collect();
                ensure!(
                    indexes
                        .iter()
                        .enumerate()
                        .all(|(row, index)| a.data[row * width..(row + 1) * width]
                            == packed[index.0 as usize * width..(index.0 as usize + 1) * width]),
                    "Lossless body attribute packing failed"
                );
                a.data = sized(packed);
                a.number_elements = sources.len() as u32;
            }
            match set {
                IndexSet::Main => group.vertex_indices = sized(indexes),
                IndexSet::Norms => group.normal_indices = sized(indexes),
                IndexSet::UV => group.uv_indices = sized(indexes),
                _ => unreachable!(),
            }
        }
    }
    Ok(())
}

pub fn build(job: &Value) -> Result<Value> {
    let encoder = crate::texture_encoding::Encoder::from_job(job)?;
    ensure!(
        job["experimental_everyday"] == true,
        "Acknowledge the experimental Everyday body test"
    );
    ensure!(job["body"]=="am","The first Everyday test scaffold supports Adult Male only. Adult Female export is not available yet");
    ensure!(
        job["review"]["check"] == true,
        "Finish the fit review before exporting"
    );
    let mut prepared = job.clone();
    prepared["pose_confirmed"] = json!(true);
    prepared["partition_confirmed"] = json!(true);
    let prepared = crate::sim_package::prepare(&prepared)?;
    let prefix = sim::field(&prepared["identities"], "prefix")?;
    let raw = assets::read("sim-everyday-template")?;
    ensure!(
        sim::digest(&raw) == SOURCE_HASH,
        "Experimental scaffold failed its integrity check"
    );
    let (container, mut nodes) = resources::load_raw(Path::new("sim-everyday-template"))?;
    let mut params = job.clone();
    params["model"] = job["model_file"].clone();
    params["reference"] = json!("sim-reference");
    params["include_images"] = json!(false);
    let fit = sim_guided::fit(&params)?;
    let buffer = assets::take("sim-buffer")?;
    ensure!(
        sim::digest(assets::read("sim-reference")?)
            == "15ec82ff9592c8189b5a1988683880428bb47a5cf541fad99cb9289553fe4fad",
        "Experimental AM rig failed its integrity check"
    );
    let reference = sim_fit::geometry("sim-reference")?;
    let mut mesh = GeometricDataContainer {
        bones: reference.bones.clone(),
        blend_group_bindings: reference.blend_group_bindings.clone(),
        ..Default::default()
    };
    let parts = object_model::character_parts(sim::field(job, "model_file")?)?;
    let mut subsets = vec![];
    for (i, p) in fit["parts"]
        .as_array()
        .context("Missing fitted parts")?
        .iter()
        .enumerate()
    {
        for name in append_mesh(&mut mesh, p, &buffer, i)? {
            subsets.push((name, i));
        }
    }
    drop(buffer);
    ensure!(
        !subsets.is_empty(),
        "No body triangles remain. Check the head/body separation"
    );
    ensure!(
        subsets.len() <= 64,
        "The body needs too many mesh sections. Use a lower-detail model"
    );
    let vertices: usize = mesh
        .attribute_groups
        .iter()
        .map(|g| g.number_elements as usize)
        .sum();
    let triangles: usize = mesh.meshes.iter().map(|m| m.indices.len() / 3).sum();
    let (mut lo, mut hi) = ([f32::INFINITY; 3], [f32::NEG_INFINITY; 3]);
    for a in mesh
        .attribute_buffers
        .iter()
        .filter(|a| a.binding.binding_type == A::Positions)
    {
        for p in a.data.chunks_exact(12) {
            for k in 0..3 {
                let v = f32::from_le_bytes(p[k * 4..k * 4 + 4].try_into().unwrap());
                ensure!(v.is_finite(), "Nonfinite body geometry");
                lo[k] = lo[k].min(v);
                hi[k] = hi[k].max(v);
            }
        }
    }
    mesh.bounding_mesh = BoundingMesh {
        vertices: (0..8)
            .map(|i| Vertex {
                x: if i & 1 == 0 { lo[0] } else { hi[0] },
                y: if i & 2 == 0 { lo[1] } else { hi[1] },
                z: if i & 4 == 0 { lo[2] } else { hi[2] },
            })
            .collect(),
        faces: [
            0, 2, 1, 1, 2, 3, 4, 5, 6, 5, 7, 6, 0, 1, 4, 1, 5, 4, 2, 6, 3, 3, 6, 7, 0, 4, 2, 2, 4,
            6, 1, 3, 5, 3, 7, 5,
        ]
        .into_iter()
        .map(Reference)
        .collect(),
    };
    compact_attributes(&mut mesh)?;
    let seed = sim::field(&prepared["identities"], "seed")?;
    let group = (u32::from_str_radix(&seed[..8], 16)? & 0x0fffffff) | 0x50000000;
    let (mut keys, mut names, mut own) = (BTreeMap::new(), BTreeMap::new(), BTreeMap::new());
    for n in nodes.iter().filter(|n| n.key().1 != 0xffffffff) {
        let old = n.key();
        let mut new = (old.0, group, old.2);
        if [GMDC, TXMT, TXTR, 0xe519c933, SHPE, 0x7ba3838c].contains(&old.0) {
            let name = if [GMDC, TXMT, TXTR].contains(&old.0) {
                let c = n.collection()?;
                match &c.entries[0].data {
                    ResourceData::Mesh(m) => m.file_name.name.to_string(),
                    ResourceData::Material(m) => m.file_name.name.to_string(),
                    ResourceData::Texture(t) => t.file_name.name.to_string(),
                }
            } else {
                scene::read(&n.bytes)?.name
            };
            let next = format!(
                "{prefix}-{:08x}-{:016x}_{}",
                old.0,
                old.2,
                name.rsplit('_').next().unwrap()
            );
            new.2 = resources::named_id(&next);
            names.insert(name.to_lowercase(), format!("##0x{group:08x}!{next}"));
            names.insert(
                format!("##0x{:08x}!{}", old.1, name.to_lowercase()),
                format!("##0x{group:08x}!{next}"),
            );
            own.insert(old, next);
        }
        keys.insert(old, new);
    }
    // The development scaffold's shape still names the original game GMNDs.
    // Both LOD routes deliberately resolve to our one embedded fitted mesh.
    let gmnd = nodes
        .iter()
        .find(|n| n.key().0 == 0x7ba3838c && n.key().1 != 0xffffffff)
        .context("Missing body geometry node")?
        .key();
    for n in nodes
        .iter()
        .filter(|n| n.key().0 == SHPE && n.key().1 != 0xffffffff)
    {
        for old in scene::read(&n.bytes)?.geometry {
            names.insert(
                format!("7ba3838c:{}", old.to_lowercase()),
                format!("##0x{group:08x}!{}", own[&gmnd]),
            );
        }
    }
    let body_key = nodes
        .iter()
        .find(|n| n.key().0 == GMDC && n.key().1 != 0xffffffff)
        .context("Missing body mesh")?
        .key();
    let template_mat = nodes
        .iter()
        .find(|n| n.key().0 == TXMT && n.key().1 != 0xffffffff)
        .context("Missing body material")?
        .clone();
    let template_tex = nodes
        .iter()
        .find(|n| n.key().0 == TXTR)
        .context("Missing body texture")?
        .clone();
    let mut materials = BTreeMap::new();
    let mut new_nodes = vec![];
    let mut texture_cache = BTreeMap::new();
    for i in subsets.iter().map(|x| x.1).collect::<BTreeSet<_>>() {
        let part = &parts[i];
        let texture_hash = (
            part.image.width(),
            part.image.height(),
            sim::digest(part.image.as_raw()),
        );
        let tn = texture_cache
            .get(&texture_hash)
            .cloned()
            .unwrap_or_else(|| format!("{prefix}-body-{i}_txtr"));
        let mn = format!("{prefix}-body-{i}_txmt");
        if let std::collections::btree_map::Entry::Vacant(e) = texture_cache.entry(texture_hash) {
            let mut tex = template_tex.clone();
            let mut c = tex.collection()?;
            let ResourceData::Texture(t) = &mut c.entries[0].data else {
                bail!("Invalid body texture")
            };
            t.file_name.name = tn.clone().into();
            t.compress_replace(
                DecodedTexture {
                    width: part.image.width() as usize,
                    height: part.image.height() as usize,
                    data: part.image.as_raw().clone(),
                },
                Some(TextureFormat::RawARGB32),
            );
            t.add_max_mip_levels(Some(127));
            crate::texture_encoding::recompress(t, TextureFormat::DXT3, encoder)?;
            tex.set(&c)?;
            assign(&mut tex, (TXTR, group, resources::named_id(&tn)));
            new_nodes.push(tex);
            e.insert(tn.clone());
        }
        let mut mat = template_mat.clone();
        set_material(
            &mut mat,
            &mn,
            &format!("##0x{group:08x}!{}", tn.trim_end_matches("_txtr")),
            part.cutout,
            part.double_sided,
        )?;
        assign(&mut mat, (TXMT, group, resources::named_id(&mn)));
        materials.insert(
            i,
            (
                mat.key(),
                format!("##0x{group:08x}!{}", mn.trim_end_matches("_txmt")),
            ),
        );
        new_nodes.push(mat);
    }
    // Only the recolor 3IDR gets new override entries. Catalog and appearance indexes remain intact.
    let outfit_idr = nodes
        .iter()
        .position(|n| n.key().0 == sim::IDR && n.key().1 != 0xffffffff)
        .context("Missing outfit links")?;
    let mut idr = SimOutfits::read_le(&mut Cursor::new(&nodes[outfit_idr].bytes))?;
    for e in &mut idr.entries {
        if let Some(k) = keys.get(&(e.type_id.code(), e.group_id, e.instance_id.id)) {
            *e = link(*k)
        }
    }
    let mut overrides = vec![];
    for (name, i) in &subsets {
        let k = materials[i].0;
        let index = if overrides.is_empty() {
            idr.entries[2] = link(k);
            2
        } else {
            idr.entries.push(link(k));
            idr.entries.len() - 1
        };
        overrides.push((name.clone(), index));
    }
    nodes[outfit_idr].set(&idr)?;
    for n in &mut nodes {
        let old = n.key();
        if old.0 == sim::IDR && old.1 == 0xffffffff {
            let mut idr = SimOutfits::read_le(&mut Cursor::new(&n.bytes))?;
            for e in &mut idr.entries {
                if let Some(k) = keys.get(&(e.type_id.code(), e.group_id, e.instance_id.id)) {
                    *e = link(*k)
                }
            }
            n.set(&idr)?;
        }
        if old.0 == GZPS && old.1 != 0xffffffff {
            let mut p = n.cpf()?;
            resources::uint(&mut p, "age", 8);
            resources::uint(&mut p, "category", 1);
            resources::uint(&mut p, "numoverrides", overrides.len() as u32);
            resources::text_prop(&mut p, "name", sim::field(job, "sim_name")?);
            for (i, (name, index)) in overrides.iter().enumerate() {
                resources::uint(&mut p, &format!("override{i}resourcekeyidx"), *index as u32);
                resources::uint(&mut p, &format!("override{i}shape"), 0);
                resources::text_prop(&mut p, &format!("override{i}subset"), name);
            }
            n.set(&p)?;
        }
        if old.0 == 0x53545223 {
            let mut t = TextList::read_le(&mut Cursor::new(&n.bytes))?;
            if let VersionedTextList::Tagged { sets, .. } = &mut t.data {
                for s in sets {
                    s.value = format!(
                        "{} by {} (Everyday body test)",
                        sim::field(job, "sim_name")?,
                        sim::field(job, "creator")?
                    )
                    .into()
                }
            }
            n.set(&t)?;
        }
        if old.1 != 0xffffffff {
            if old.0 == GMDC {
                let mut c = n.collection()?;
                let ResourceData::Mesh(m) = &mut c.entries[0].data else {
                    bail!("Invalid body geometry")
                };
                let name = m.file_name.clone();
                if old == body_key {
                    *m = mesh.clone();
                }
                m.file_name = name;
                m.file_name.name = own[&old].clone().into();
                n.set(&c)?;
            } else if [0xe519c933, SHPE, 0x7ba3838c].contains(&old.0) {
                let s = scene::read(&n.bytes)?;
                let mut rename = names.clone();
                rename.insert(s.name.to_lowercase(), own[&old].clone());
                n.bytes = scene::rewrite(&n.bytes, &s, &rename, &keys)?;
                if old.0 == SHPE {
                    let s = scene::read(&n.bytes)?;
                    let (a, b) = s.parts_range.context("Missing body shape slots")?;
                    let mut replacement = (subsets.len() as u32).to_le_bytes().to_vec();
                    for (name, i) in &subsets {
                        replacement.extend(scene::string(name));
                        replacement.extend(scene::string(&materials[i].1));
                        replacement.extend([0u8; 9]);
                    }
                    n.bytes.splice(a..b, replacement);
                }
            }
            assign(n, keys[&old]);
        }
    }
    drop(mesh);
    nodes.retain(|n| ![TXMT, TXTR].contains(&n.key().0) || n.key().1 == 0xffffffff);
    nodes.extend(new_nodes);
    // Both source LOD routes use the same reviewed mesh for this first test.
    // Share its payload instead of storing the complete imported geometry twice.
    let meshes: Vec<_> = nodes
        .iter()
        .filter(|n| n.key().0 == GMDC && n.key().1 == group)
        .map(|n| n.key())
        .collect();
    let first = *meshes.first().context("Missing exported body geometry")?;
    let redirects: BTreeMap<_, _> = meshes.iter().skip(1).map(|k| (*k, first)).collect();
    for n in nodes
        .iter_mut()
        .filter(|n| n.key().0 == 0x7ba3838c && n.key().1 == group)
    {
        let s = scene::read(&n.bytes)?;
        n.bytes = scene::rewrite(&n.bytes, &s, &BTreeMap::new(), &redirects)?;
    }
    nodes.retain(|n| !redirects.contains_key(&n.key()));
    let all: BTreeSet<_> = nodes.iter().map(Node::key).collect();
    ensure!(
        all.len() == nodes.len(),
        "Generated body resource identities collide"
    );
    for n in nodes.iter().filter(|n| n.key().0 == sim::IDR) {
        let idr = SimOutfits::read_le(&mut Cursor::new(&n.bytes))?;
        for e in idr.entries {
            if e.group_id == group {
                ensure!(
                    all.contains(&(e.type_id.code(), e.group_id, e.instance_id.id)),
                    "Unresolved generated CAS link"
                );
            }
        }
    }
    let texture_names: BTreeSet<_> = nodes
        .iter()
        .filter(|n| n.key().0 == TXTR)
        .map(|n| {
            let c = n.collection()?;
            let ResourceData::Texture(t) = &c.entries[0].data else {
                bail!("Invalid output texture")
            };
            ensure!(
                t.mip_levels() == (32 - t.width.max(t.height).leading_zeros()) as usize,
                "Incomplete body mip chain"
            );
            Ok(format!(
                "##0x{:08x}!{}",
                n.key().1,
                t.file_name.name.to_string().trim_end_matches("_txtr")
            ))
        })
        .collect::<Result<_>>()?;
    for n in nodes.iter().filter(|n| n.key().1 == group) {
        if [0xe519c933, SHPE, 0x7ba3838c].contains(&n.key().0) {
            for (_, k) in scene::read(&n.bytes)?.links {
                if k.1 == group {
                    ensure!(all.contains(&k), "Unresolved generated geometry link");
                }
            }
        }
        if n.key().0 == TXMT {
            let c = n.collection()?;
            let ResourceData::Material(m) = &c.entries[0].data else {
                bail!("Invalid output material")
            };
            let base = m
                .properties
                .iter()
                .find(|v| v.name.to_string() == "stdMatBaseTextureName")
                .context("Missing output base texture")?
                .value
                .to_string();
            ensure!(
                texture_names.contains(&base) && m.names.iter().any(|n| n.to_string() == base),
                "Unresolved material texture or file list"
            );
        }
    }
    let gmnd_names: BTreeSet<_> = nodes
        .iter()
        .filter(|n| n.key().0 == 0x7ba3838c && n.key().1 == group)
        .map(|n| Ok(format!("##0x{group:08x}!{}", scene::read(&n.bytes)?.name)))
        .collect::<Result<_>>()?;
    for n in nodes
        .iter()
        .filter(|n| n.key().0 == SHPE && n.key().1 == group)
    {
        ensure!(
            scene::read(&n.bytes)?
                .geometry
                .iter()
                .all(|g| gmnd_names.contains(g)),
            "Body shape still references external source geometry"
        );
    }
    for n in &mut nodes {
        if n.bytes.len() < 0x1000000 {
            n.entry.compression = dbpf::CompressionType::RefPack;
        }
    }
    resources::write_output(
        container,
        &mut nodes,
        Path::new("output"),
        crate::package_compression::from_job(&prepared)?,
    )?;
    let output = assets::read("output")?;
    ensure!(
        output.len() <= 64 * 1024 * 1024,
        "Experimental Sim package is {:.1} MiB with {} body vertices. Use a lower-detail model to stay within 64 MiB", output.len() as f64 / 1048576., vertices
    );
    let (_, check) = resources::load_raw(Path::new("output"))?;
    ensure!(
        check.len() == nodes.len()
            && check.iter().all(|n| nodes
                .iter()
                .any(|o| o.key() == n.key() && o.bytes == n.bytes)),
        "Experimental package failed resource round trip"
    );
    Ok(
        json!({"package_compression":crate::package_compression::report("output", crate::package_compression::from_job(&prepared)?)?,"texture_encoding":encoder.usage(&[TextureFormat::DXT3]),"version":1,"validated":true,"experimental":true,"scope":"Adult Male Everyday body only. Stock head, hair and other outfits retained. No replacement head.","gameplay":"not_tested","filename":prepared["filename"],"sha256":sim::digest(&output),"resources":check.len(),"vertices":vertices,"triangles":triangles,"subsets":subsets.len(),"textures":texture_names.len(),"body_geometry_resources":1,"bounds":{"min":lo,"max":hi},"warnings":fit["warnings"]}),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn indexed_attributes_preserve_seams_and_skin_values() {
        let mut m = GeometricDataContainer::default();
        let mut a = vec![0u8; 12 * 12];
        for i in 0..12 {
            a[i * 12..i * 12 + 4].copy_from_slice(&((i % 2) as f32).to_le_bytes());
        }
        let normals: Vec<_> = (0..12).flat_map(|i| [i as f32, 1., 0.]).collect();
        m.attribute_buffers.extend([
            attr(A::Positions, 0, F::F32Vec3, a.clone(), 12),
            attr(A::PositionDeltas, 0, F::F32Vec3, a, 12),
            attr(A::Normals, 0, F::F32Vec3, encoded(normals.into_iter()), 12),
            attr(A::TexCoords, 0, F::F32Vec2, vec![0; 12 * 8], 12),
        ]);
        m.attribute_groups.push(AttributeGroup {
            attributes: sized((0..4).map(Reference).collect()),
            number_elements: 12,
            referenced_active: 4,
            ..Default::default()
        });
        let originals: Vec<_> = m
            .attribute_buffers
            .iter()
            .map(|a| a.data.data.clone())
            .collect();
        compact_attributes(&mut m).unwrap();
        let g = &m.attribute_groups[0];
        assert!(!g.vertex_indices.is_empty());
        assert!(!g.uv_indices.is_empty());
        assert!(g.normal_indices.is_empty());
        for (i, a) in m.attribute_buffers.iter().enumerate() {
            assert_eq!(
                g.get_buffer_data(a).flatten().copied().collect::<Vec<_>>(),
                originals[i]
            );
        }
    }
}
