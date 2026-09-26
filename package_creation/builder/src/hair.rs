// SPDX-License-Identifier: GPL-3.0-or-later
//! Hair recolor inspection, cloning, and independent output validation.
use crate::assets as fs;
use anyhow::{anyhow, bail, ensure, Context, Result};
use binrw::BinRead;
use clap::{Parser, Subcommand};
use dbpf::internal_file::{
    cpf::Data,
    resource_collection::{
        texture_resource::{decoded_texture::DecodedTexture, TextureFormat},
        ResourceCollection, ResourceData,
    },
    sim_outfits::SimOutfits,
    text_list::{TextList, VersionedTextList},
};
use dbpf::{header_v1::InstanceId, CompressionType, DBPFFile};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    io::Cursor,
    path::{Path, PathBuf},
    rc::Rc,
};

const BINX: u32 = 0x0c560f39;
const IDR: u32 = 0xac506764;
const STR: u32 = 0x53545223;
const GZPS: u32 = 0xebcf3e27;
const XHTN: u32 = 0x8c1580b5;
const TXTR: u32 = 0x1c4a276c;
const TXMT: u32 = 0x49596978;
const CRES: u32 = 0xe519c933;
const SHPE: u32 = 0xfc6eb1f7;
const COLL: u32 = 0x6c4f359d;
use crate::core::properties::*;
use crate::core::resources::*;

#[path = "hair_resources.rs"]
mod hair_resources;
#[path = "standard_hair.rs"]
mod standard_hair;
use hair_resources::ResourcePlan;

#[derive(Parser)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}
#[derive(Subcommand)]
enum Command {
    /// Offline installer only. Convert verified game resources into a recolor.
    PrepareStandard {
        #[arg(long)]
        source: PathBuf,
        #[arg(long)]
        scaffold: PathBuf,
        #[arg(long)]
        output: PathBuf,
    },
    Inventory {
        #[arg(long)]
        package: PathBuf,
    },
    Inspect {
        #[arg(long)]
        package: PathBuf,
        #[arg(long)]
        export_dir: Option<PathBuf>,
    },
    Build {
        #[arg(long)]
        spec: PathBuf,
        #[arg(long)]
        output: PathBuf,
    },
    Validate {
        #[arg(long)]
        spec: PathBuf,
        #[arg(long)]
        package: PathBuf,
    },
}
fn ref_key(e: &dbpf::internal_file::sim_outfits::Entry) -> Key {
    (e.type_id.code(), e.group_id, e.instance_id.id)
}
fn load(path: &Path) -> Result<(DBPFFile, Vec<Node>)> {
    let (p, mut nodes) = load_raw(path)?;
    if nodes.iter().filter(|n| n.key().0 == XHTN).count() == 1 {
        normalize_catalog(&mut nodes)?;
    }
    Ok((p, nodes))
}
fn active_idr_slots(nodes: &[Node], excluded: &BTreeSet<Key>) -> Result<BTreeSet<(Key, usize)>> {
    let mut active = BTreeSet::new();
    for n in nodes
        .iter()
        .filter(|n| [GZPS, BINX].contains(&n.key().0) && !excluded.contains(&n.key()))
    {
        let p = n.cpf()?;
        let idr = (IDR, n.key().1, n.key().2);
        let fields: Vec<String> = if n.key().0 == GZPS {
            let count = number(&p, "numoverrides")?;
            ensure!(count <= 32, "Unsupported hair subset count");
            ["resourcekeyidx".into(), "shapekeyidx".into()]
                .into_iter()
                .chain((0..count).map(|i| format!("override{i}resourcekeyidx")))
                .collect()
        } else {
            ["objectidx", "stringsetidx", "binidx", "iconidx"]
                .map(String::from)
                .to_vec()
        };
        for name in fields {
            active.insert((idr, number(&p, &name)? as usize));
        }
    }
    Ok(active)
}
fn normalize_catalog(nodes: &mut Vec<Node>) -> Result<()> {
    // Age-pruned recolors can retain dead catalog/material entries in their
    // rendering 3IDRs. Only unused slots may be cleared, never a live reference.
    let active = active_idr_slots(nodes, &BTreeSet::new())?;
    let keys: BTreeSet<_> = nodes.iter().map(Node::key).collect();
    for n in nodes.iter_mut().filter(|n| n.key().0 == IDR) {
        let mut idr = n.idr()?;
        for (i, e) in idr.entries.iter_mut().enumerate() {
            let k = ref_key(e);
            if [GZPS, TXMT, XHTN, STR].contains(&k.0) && !keys.contains(&k) {
                ensure!(
                    !active.contains(&(n.key(), i)),
                    "A live hair reference is unresolved: {}",
                    key_text(k)
                );
                e.type_id = 0u32.into();
                e.group_id = 0;
                e.instance_id = InstanceId { id: 0 };
            }
        }
        n.set(&idr)?;
    }
    let tone = nodes.iter().find(|n| n.key().0 == XHTN).unwrap().key();
    let mut has_catalog = false;
    for n in nodes.iter().filter(|n| n.key().0 == BINX) {
        let p = n.cpf()?;
        let idr = find(nodes, (IDR, n.key().1, n.key().2))?.idr()?;
        has_catalog |= idr
            .entries
            .get(number(&p, "objectidx")? as usize)
            .is_some_and(|e| ref_key(e) == tone);
    }
    if !has_catalog {
        let mut candidates = vec![];
        for n in nodes.iter().filter(|n| n.key().0 == IDR) {
            if keys.contains(&(BINX, n.key().1, n.key().2)) {
                continue;
            }
            let idr = n.idr()?;
            if let Some(index) = idr.entries.iter().position(|e| ref_key(e) == tone) {
                candidates.push((n.key(), idr, index));
            }
        }
        ensure!(
            candidates.len() == 1,
            "A missing hairtone catalog entry cannot be resolved unambiguously"
        );
        let (key, idr, index) = candidates.remove(0);
        let mut catalog = nodes
            .iter()
            .find(|n| n.key().0 == BINX)
            .context("Hair has no catalog metadata")?
            .clone();
        catalog.entry.instance_id = InstanceId { id: key.2 };
        catalog.entry.group_id = key.1;
        let mut p = catalog.cpf()?;
        uint(&mut p, "objectidx", index as u32);
        for (name, kind) in [("stringsetidx", STR), ("binidx", COLL), ("iconidx", 0)] {
            let pos = idr
                .entries
                .iter()
                .position(|e| e.type_id.code() == kind)
                .context("Incomplete hairtone catalog links")?;
            uint(&mut p, name, pos as u32);
        }
        catalog.set(&p)?;
        nodes.push(catalog);
    }
    Ok(())
}
fn find(nodes: &[Node], key: Key) -> Result<&Node> {
    nodes
        .iter()
        .find(|n| n.key() == key)
        .ok_or_else(|| anyhow!("Missing local resource {}", key_text(key)))
}
fn material_texture(n: &Node) -> Result<String> {
    let c = n.collection()?;
    ensure!(c.entries.len() == 1, "Unsupported material resource layout");
    let ResourceData::Material(m) = &c.entries[0].data else {
        bail!("Invalid material")
    };
    let values: Vec<_> = m
        .properties
        .iter()
        .filter(|p| p.name.to_string() == "stdMatBaseTextureName")
        .collect();
    ensure!(
        values.len() == 1,
        "Each material must reference one embedded base texture"
    );
    ensure!(m.properties.iter().all(|p|p.name.to_string()=="stdMatBaseTextureName" || !p.name.to_string().ends_with("TextureName") || p.value.to_string().is_empty()),"A material has a secondary texture dependency. Prepare a single recolor using embedded base textures only");
    Ok(values[0].value.to_string().to_lowercase())
}
fn skin_material(n: &Node) -> Result<bool> {
    let c = n.collection()?;
    ensure!(c.entries.len() == 1, "Unsupported material resource layout");
    let ResourceData::Material(m) = &c.entries[0].data else {
        bail!("Invalid material")
    };
    // SimSkin obtains the scalp from the Sim's skin. Its legacy file list is
    // metadata, not an embedded hair texture assignment.
    Ok(m.material_type.to_string() == "SimSkin"
        && !m
            .properties
            .iter()
            .any(|p| p.name.to_string().ends_with("TextureName")))
}

fn inventory(nodes: &[Node]) -> Result<Value> {
    let kind = if nodes.iter().any(|n| n.key().0 == XHTN) {
        "recolor"
    } else {
        ensure!(
            nodes
                .iter()
                .all(|n| [CRES, SHPE, 0x7ba3838c, 0xac4f8687].contains(&n.key().0)),
            "Upload a hair recolor or a separate Sims 2 mesh package"
        );
        for required in [CRES, SHPE, 0x7ba3838c, 0xac4f8687] {
            ensure!(
                nodes.iter().any(|n| n.key().0 == required),
                "The mesh package is incomplete"
            );
        }
        "mesh"
    };
    let mut links = BTreeSet::new();
    if kind == "mesh" {
        for node in nodes {
            let mut r = Cursor::new(&node.bytes);
            let first = u32::read_le(&mut r)?;
            let modern = first == 0xffff0001;
            let count = if modern { u32::read_le(&mut r)? } else { first };
            ensure!(count <= 4096, "Invalid mesh link count");
            for _ in 0..count {
                let group = u32::read_le(&mut r)?;
                let low = u32::read_le(&mut r)?;
                let high = if modern { u32::read_le(&mut r)? } else { 0 };
                let kind = u32::read_le(&mut r)?;
                if kind != 0 {
                    links.insert(key_text((kind, group, ((high as u64) << 32) | low as u64)));
                }
            }
        }
    }
    Ok(
        json!({"kind":kind,"resource_keys":nodes.iter().map(|n|key_text(n.key())).collect::<Vec<_>>(),"links":links}),
    )
}
fn texture_aliases(n: &Node) -> Result<Vec<String>> {
    let c = n.collection()?;
    ensure!(c.entries.len() == 1, "Unsupported texture resource layout");
    let ResourceData::Texture(t) = &c.entries[0].data else {
        bail!("Invalid texture")
    };
    let s = t.file_name.name.to_string().to_lowercase();
    let s = s.strip_suffix("_txtr").unwrap_or(&s).to_string();
    let bare = s.split('!').next_back().unwrap().to_string();
    Ok(vec![s, format!("##0x{:08x}!{bare}", n.key().1), bare])
}
fn resolve_texture(nodes: &[Node], m: &Node) -> Result<Key> {
    let s = material_texture(m)?;
    let candidates: Vec<_> = nodes
        .iter()
        .filter(|n| n.key().0 == TXTR)
        .filter(|n| texture_aliases(n).is_ok_and(|a| a.contains(&s)))
        .collect();
    ensure!(candidates.len()==1,"Material references a missing or ambiguous texture ({s}). Upload a recolor with embedded textures");
    Ok(candidates[0].key())
}
#[derive(Clone, Serialize)]
struct Age {
    key: String,
    age: u32,
    gender: u32,
    materials: Vec<String>,
}
#[derive(Clone, Serialize)]
struct TextureUse {
    age: u32,
    gender: u32,
    subset: String,
    material: String,
}
#[derive(Clone, Serialize)]
struct Slot {
    id: String,
    width: u32,
    height: u32,
    format: String,
    name: String,
    ages: Vec<u32>,
    uses: Vec<TextureUse>,
}
#[derive(Serialize)]
pub(crate) struct Inspection {
    schema_version: u32,
    ages: Vec<Age>,
    textures: Vec<Slot>,
    external_meshes: Vec<String>,
    resource_count: usize,
    catalog: Vec<Value>,
    #[serde(skip)]
    images: BTreeMap<String, Rc<image::RgbaImage>>,
}
impl Inspection {
    pub(crate) fn pixel_bytes(&self) -> usize {
        self.images.values().map(|image| image.len()).sum()
    }
    fn export(&self, dir: &Path) -> Result<()> {
        fs::create_dir_all(dir)?;
        for (id, image) in &self.images {
            fs::save_shared_image(&dir.join(format!("{id}.png")), image.clone())?;
        }
        Ok(())
    }
}
// GZPS resource/override indices address its rendering 3IDR. Catalog BINX object
// indices address a GZPS/XHTN through another 3IDR, which can have a different ID.
fn rendering_idr(nodes: &[Node], gz: &Node) -> Result<Key> {
    let direct = (IDR, gz.key().1, gz.key().2);
    find(nodes, direct).context("The property set has no matching rendering 3IDR")?;
    Ok(direct)
}
fn age_materials(nodes: &[Node], gz: &Node) -> Result<Vec<Key>> {
    let p = gz.cpf()?;
    let idr = find(nodes, rendering_idr(nodes, gz)?)?.idr()?;
    for (field, kind) in [("resourcekeyidx", CRES), ("shapekeyidx", SHPE)] {
        let i = number(&p, field)? as usize;
        ensure!(
            idr.entries.get(i).is_some_and(|e| e.type_id.code() == kind),
            "Invalid mesh reference index {field}"
        );
    }
    let count = number(&p, "numoverrides")?;
    ensure!(count > 0 && count <= 32, "Unsupported hair subset count");
    let mut result = vec![];
    for i in 0..count {
        let slot = number(&p, &format!("override{i}resourcekeyidx"))? as usize;
        let e = idr
            .entries
            .get(slot)
            .ok_or_else(|| anyhow!("Invalid material index"))?;
        ensure!(e.type_id.code() == TXMT, "Hair override is not a material");
        find(nodes, ref_key(e))?;
        result.push(ref_key(e));
    }
    Ok(result)
}
fn inspect(nodes: &[Node], export: Option<&Path>) -> Result<Inspection> {
    inspect_with_limit(nodes, export, 16)
}
fn inspect_with_limit(
    nodes: &[Node],
    export: Option<&Path>,
    texture_limit: usize,
) -> Result<Inspection> {
    inspect_impl(nodes, export, texture_limit, false)
}
fn inspect_impl(
    nodes: &[Node],
    export: Option<&Path>,
    texture_limit: usize,
    retain_images: bool,
) -> Result<Inspection> {
    let mut images = BTreeMap::new();
    ensure!(
        nodes.iter().filter(|n| n.key().0 == XHTN).count() == 1,
        "Upload one hair recolor, not a mesh-only package or merged collection"
    );
    ensure!(
        nodes
            .iter()
            .all(|n| [BINX, IDR, STR, GZPS, XHTN, TXTR, TXMT].contains(&n.key().0)),
        "This package includes unsupported resources. Upload a single recolor with a separate mesh"
    );
    ensure!(
        nodes
            .iter()
            .all(|n| n.key().1 == u32::MAX || (n.key().1 & 0xf0000000) == 0x50000000),
        "Default replacements and game resource overrides are not supported"
    );
    let mut ages = vec![];
    let mut texture_ages: BTreeMap<Key, BTreeSet<u32>> = BTreeMap::new();
    let mut texture_uses: BTreeMap<Key, Vec<TextureUse>> = BTreeMap::new();
    let mut families = BTreeSet::new();
    for gz in nodes.iter().filter(|n| n.key().0 == GZPS) {
        let p = gz.cpf()?;
        ensure!(
            number(&p, "species")? == 1 && number(&p, "parts")? == 1,
            "Only normal Sim hair recolors are supported"
        );
        let age = number(&p, "age")?;
        ensure!(age > 0 && age & !0x7f == 0, "Invalid hair age flags");
        ensure!(
            age & 0x10 == 0 || age == 0x10,
            "Split combined elder and non-elder property sets before uploading this recolor"
        );
        families.insert(string(&p, "family")?);
        let materials = age_materials(nodes, gz)?;
        for (index, m) in materials.iter().enumerate() {
            if skin_material(find(nodes, *m)?)? {
                continue;
            }
            let texture = resolve_texture(nodes, find(nodes, *m)?)?;
            texture_ages.entry(texture).or_default().insert(age);
            texture_uses.entry(texture).or_default().push(TextureUse {
                age,
                gender: number(&p, "gender")?,
                subset: string(&p, &format!("override{index}subset"))?,
                material: key_text(*m),
            });
        }
        ages.push(Age {
            key: key_text(gz.key()),
            age,
            gender: number(&p, "gender")?,
            materials: materials.into_iter().map(key_text).collect(),
        });
    }
    ensure!(
        !ages.is_empty() && families.len() == 1,
        "Upload one hairstyle with a consistent age family"
    );
    let mut age_gender_slots = BTreeSet::new();
    for age in &ages {
        ensure!(
            age.gender > 0 && age.gender <= 3,
            "Unsupported hair gender flags"
        );
        for bit in 0..7 {
            for gender in [1, 2] {
                if age.age & (1 << bit) != 0 && age.gender & gender != 0 {
                    ensure!(age_gender_slots.insert((bit, gender)), "Multiple catalog styles share the same age and gender. Upload a single recolor");
                }
            }
        }
    }
    let mut catalog = vec![];
    let mut catalog_targets = BTreeSet::new();
    for binx in nodes.iter().filter(|n| n.key().0 == BINX) {
        let p = binx.cpf()?;
        let idr = find(nodes, (IDR, binx.key().1, binx.key().2))?.idr()?;
        let slot = number(&p, "objectidx")? as usize;
        let target = ref_key(
            idr.entries
                .get(slot)
                .ok_or_else(|| anyhow!("Invalid catalog object index"))?,
        );
        ensure!(
            [GZPS, XHTN].contains(&target.0),
            "Catalog entry must reference a hair property set or hairtone"
        );
        find(nodes, target)?;
        ensure!(
            catalog_targets.insert(target),
            "Duplicate hair catalog entry. Upload a single recolor"
        );
        for (name, kind) in [("stringsetidx", STR), ("binidx", COLL)] {
            let slot = number(&p, name)? as usize;
            ensure!(
                idr.entries
                    .get(slot)
                    .is_some_and(|e| e.type_id.code() == kind),
                "Invalid catalog {name} reference"
            );
        }
        catalog.push(json!({"key":key_text(binx.key()),"object":key_text(target),"age":if target.0==GZPS {Some(number(&find(nodes,target)?.cpf()?,"age")?)}else{None}}));
    }
    ensure!(
        nodes
            .iter()
            .filter(|n| [GZPS, XHTN].contains(&n.key().0))
            .all(|n| catalog_targets.contains(&n.key())),
        "A hair age or hairtone has no catalog entry"
    );
    let keys: BTreeSet<_> = nodes.iter().map(Node::key).collect();
    let mut external = BTreeSet::new();
    for n in nodes.iter().filter(|n| n.key().0 == IDR) {
        for e in n.idr()?.entries {
            let k = ref_key(&e);
            if !keys.contains(&k) {
                ensure!(
                    [0, CRES, SHPE, COLL].contains(&k.0),
                    "Unresolved local catalog or material reference {}",
                    key_text(k)
                );
                if [CRES, SHPE].contains(&k.0) {
                    external.insert(key_text(k));
                }
            }
        }
    }
    ensure!(
        !external.is_empty(),
        "A recolor must reference an external hair mesh"
    );
    for n in nodes.iter().filter(|n| n.key().0 == TXMT) {
        if skin_material(n)? {
            continue;
        }
        resolve_texture(nodes, n)?;
        let collection = n.collection()?;
        let ResourceData::Material(material) = &collection.entries[0].data else {
            bail!("Invalid material")
        };
        if material.names.len() > 1 {
            ensure!(
                material.names.iter().any(
                    |s| s.to_string().to_lowercase() == material_texture(n).unwrap_or_default()
                ),
                "An ambiguous material file list needs preparation before upload"
            );
            for name in &material.names {
                let matches = nodes
                    .iter()
                    .filter(|n| n.key().0 == TXTR)
                    .filter(|n| {
                        texture_aliases(n)
                            .is_ok_and(|aliases| aliases.contains(&name.to_string().to_lowercase()))
                    })
                    .count();
                ensure!(matches == 1, "A material file list references an unresolved external texture. Embed its textures before upload");
            }
        }
    }
    if let Some(dir) = export {
        fs::create_dir_all(dir)?;
    }
    let mut textures = vec![];
    ensure!(
        nodes.iter().filter(|n| n.key().0 == TXTR).count() <= texture_limit,
        "A recolor may contain at most {texture_limit} embedded textures"
    );
    for n in nodes.iter().filter(|n| n.key().0 == TXTR) {
        let c = n.collection()?;
        let ResourceData::Texture(t) = &c.entries[0].data else {
            bail!("Invalid texture")
        };
        ensure!(
            t.width > 0
                && t.height > 0
                && t.width <= 2048
                && t.height <= 2048
                && t.width.is_power_of_two()
                && t.height.is_power_of_two(),
            "Hair textures must have power-of-two dimensions up to 2048"
        );
        ensure!(
            t.textures.len() == 1 && t.mip_levels() > 0,
            "Animated or empty textures are not supported"
        );
        ensure!(
            matches!(
                t.get_format(),
                TextureFormat::DXT1
                    | TextureFormat::DXT3
                    | TextureFormat::DXT5
                    | TextureFormat::RawARGB32
                    | TextureFormat::AltARGB32
                    | TextureFormat::RawRGB24
                    | TextureFormat::AltRGB24
            ),
            "Unsupported hair texture format"
        );
        let active = texture_ages.contains_key(&n.key());
        let mut top = None;
        for i in 0..t.mip_levels() {
            let decoded = t
                .decompress(0, i)
                .context("Textures must have embedded mip levels, not external LIFO data")?;
            if active && (retain_images || export.is_some()) && i == t.mip_levels() - 1 {
                top = Some(decoded);
            }
        }
        // Keep validating unused resources, but only active age/material routes
        // require an image assignment. Shared subsets deduplicate by TXTR key.
        if !active {
            continue;
        }
        if let Some(d) = top {
            if let Some(dir) = export {
                crate::assets::save_buffer(
                    dir.join(format!("{}.png", key_text(n.key()))),
                    &d.data,
                    t.width,
                    t.height,
                    image::ColorType::Rgba8,
                )?;
            }
            if retain_images {
                let retained: usize = images
                    .values()
                    .map(|image: &Rc<image::RgbaImage>| image.len())
                    .sum();
                if d.data.len() <= 128 * 1024 * 1024 - retained {
                    let image = image::RgbaImage::from_raw(t.width, t.height, d.data)
                        .context("Invalid hair source pixels")?;
                    images.insert(key_text(n.key()), Rc::new(image));
                }
            }
        }
        textures.push(Slot {
            id: key_text(n.key()),
            width: t.width,
            height: t.height,
            format: format!("{:?}", t.get_format()),
            name: t.file_name.name.to_string(),
            ages: texture_ages
                .get(&n.key())
                .map(|v| v.iter().copied().collect())
                .unwrap_or_default(),
            uses: texture_uses.remove(&n.key()).unwrap_or_default(),
        });
    }
    ensure!(
        !textures.is_empty(),
        "A recolor must reference at least one embedded color texture"
    );
    Ok(Inspection {
        schema_version: 2,
        ages,
        textures,
        external_meshes: external.into_iter().collect(),
        resource_count: nodes.len(),
        catalog,
        images,
    })
}
// Only call with nodes freshly loaded from path, with no in-memory edits.
// Both the content digest and mutation revision fence cached validation.
fn inspect_loaded_source(path: &Path, nodes: &[Node]) -> Result<Rc<Inspection>> {
    let key = format!("hair-inspection-v1:{}", fs::cache_identity(path)?);
    if let Some(info) = crate::caches::hair_source_get(&key) {
        return Ok(info);
    }
    let info = Rc::new(inspect_impl(nodes, None, 16, true)?);
    crate::caches::hair_source_insert(&key, info.clone());
    Ok(info)
}
#[derive(Deserialize)]
struct Spec {
    #[serde(default = "crate::package_compression::default_enabled")]
    refpack_compression: bool,
    #[serde(default)]
    texture_encoder: crate::texture_encoding::Encoder,
    template: PathBuf,
    group: u32,
    family: String,
    hairtone: String,
    creator_uuid: String,
    label: String,
    color: String,
    bin: u32,
    grey_only: bool,
    grey_elders: bool,
    textures: BTreeMap<String, PathBuf>,
    elder_textures: BTreeMap<String, PathBuf>,
}
fn generated_name(s: &Spec, k: Key, elder: bool) -> String {
    format!(
        "hair-{:08x}-{:016x}{}{}",
        s.group,
        k.2,
        if elder { "-elder" } else { "" },
        if k.0 == TXTR { "_txtr" } else { "_txmt" }
    )
}
fn new_key(s: &Spec, k: Key, elder: bool) -> Key {
    (
        k.0,
        s.group,
        if [TXTR, TXMT].contains(&k.0) {
            named_id(&generated_name(s, k, elder))
        } else {
            k.2
        },
    )
}
fn qualified(s: &Spec, k: Key, elder: bool) -> String {
    format!("##0x{:08x}!{}", s.group, generated_name(s, k, elder))
}
fn bin_id(bin: u32) -> String {
    format!("{bin:08x}-0000-0000-0000-000000000000")
}
fn produce(s: &Spec, source: &[Node]) -> Result<Vec<Node>> {
    let info = inspect(source, None)?;
    produce_inspected(s, source, &info)
}
fn produce_inspected(s: &Spec, source: &[Node], info: &Inspection) -> Result<Vec<Node>> {
    ensure!(
        (s.group & 0xf0000000) == 0x50000000 && source.iter().all(|n| n.key().1 != s.group),
        "Invalid or reused recolor group"
    );
    ensure!(
        s.bin <= 5 && s.label.len() <= 200,
        "Invalid recolor metadata"
    );
    for v in [&s.family, &s.hairtone, &s.creator_uuid] {
        ensure!(
            v.len() == 36 && v.bytes().all(|b| b.is_ascii_hexdigit() || b == b'-'),
            "Invalid generated identity"
        );
    }
    let slots: BTreeSet<_> = info.textures.iter().map(|t| t.id.clone()).collect();
    ensure!(
        s.textures.keys().cloned().collect::<BTreeSet<_>>() == slots,
        "Every texture slot needs an explicit assignment"
    );
    ensure!(
        !s.grey_only || info.ages.iter().any(|a| a.age & 0x10 != 0),
        "This hair has no elder age for grey-only recolors"
    );
    let plan = ResourcePlan::new(s, source)?;
    if s.grey_elders && !s.grey_only && !plan.elder_idrs.is_empty() {
        ensure!(
            s.elder_textures.keys().cloned().collect::<BTreeSet<_>>() == slots,
            "Elder texture assignments are incomplete"
        );
    }
    let mut nodes = vec![];
    for old in source {
        if plan.removed.contains(&old.key()) {
            continue;
        }
        let variants = if [TXTR, TXMT].contains(&old.key().0) {
            plan.variants(old.key())
        } else {
            BTreeSet::from([false])
        };
        for elder in variants {
            let mut n = old.clone();
            let original = n.key();
            let k = new_key(s, original, elder);
            n.entry.group_id = k.1;
            n.entry.instance_id = InstanceId { id: k.2 };
            n.entry.compression = CompressionType::RefPack;
            match original.0 {
                GZPS | XHTN | BINX => {
                    let mut p = n.cpf()?;
                    if original.0 == GZPS {
                        let age = number(&p, "age")?;
                        let tone = if s.grey_only || (age == 0x10 && s.grey_elders) {
                            bin_id(5)
                        } else if s.bin > 0 {
                            bin_id(s.bin)
                        } else {
                            s.hairtone.clone()
                        };
                        text_prop(&mut p, "family", &s.family);
                        text_prop(&mut p, "creator", &s.creator_uuid);
                        text_prop(&mut p, "hairtone", &tone);
                        if s.grey_only {
                            uint(&mut p, "age", 0x10);
                        }
                    } else if original.0 == XHTN {
                        text_prop(&mut p, "family", &s.hairtone);
                        text_prop(&mut p, "creator", &s.creator_uuid);
                        text_prop(&mut p, "name", &s.color);
                        text_prop(&mut p, "proxy", &bin_id(s.bin));
                        put(
                            &mut p,
                            "genetic",
                            Data::Float(if s.bin == 1 || s.bin == 2 {
                                1.0
                            } else if s.bin == 3 || s.bin == 4 {
                                2.0
                            } else {
                                0.0
                            }),
                        );
                        if s.grey_only {
                            uint(&mut p, "age", 0x10);
                        }
                    } else {
                        text_prop(&mut p, "creatorid", &s.creator_uuid);
                    }
                    n.set(&p)?;
                }
                IDR => {
                    let mut idr = n.idr()?;
                    idr.version = dbpf::IndexMinorVersion::V2;
                    for (index, e) in idr.entries.iter_mut().enumerate() {
                        let oldkey = ref_key(e);
                        if source.iter().any(|n| n.key() == oldkey) {
                            if plan.removed.contains(&oldkey) {
                                ensure!(!plan.retained_slots.contains(&(original, index)), "A retained catalog or rendering entry still uses a removed resource");
                                // This 3IDR is still needed for rendering or a
                                // retained catalog entry. Clear its dead slots.
                                e.type_id = 0u32.into();
                                e.group_id = 0;
                                e.instance_id = InstanceId { id: 0 };
                                continue;
                            }
                            let variant = plan.variant(
                                oldkey,
                                !s.grey_only
                                    && s.grey_elders
                                    && plan.elder_idrs.contains(&original),
                            )?;
                            let target = new_key(s, oldkey, variant);
                            e.group_id = target.1;
                            e.instance_id = InstanceId { id: target.2 };
                        }
                    }
                    n.set(&idr)?;
                }
                TXMT => {
                    let mut c = n.collection()?;
                    let ResourceData::Material(m) = &mut c.entries[0].data else {
                        bail!("Invalid material")
                    };
                    m.file_name.name = qualified(s, original, elder).into();
                    m.material_description = qualified(s, original, elder)
                        .trim_end_matches("_txmt")
                        .into();
                    if !skin_material(old)? {
                        let tk = resolve_texture(source, old)?;
                        let target = qualified(s, tk, plan.variant(tk, elder)?)
                            .trim_end_matches("_txtr")
                            .to_string();
                        for p in &mut m.properties {
                            if p.name.to_string() == "stdMatBaseTextureName" {
                                p.value = target.clone().into();
                            }
                        }
                        let previous = material_texture(old)?;
                        // Legacy recolors can retain the original Maxis name in this list.
                        // Replace the base-texture entry, preserving other material resources.
                        if m.names.len() <= 1 {
                            m.names = vec![target.clone().into()];
                        } else {
                            let mut changed = false;
                            for name in &mut m.names {
                                if name.to_string().to_lowercase() == previous {
                                    *name = target.clone().into();
                                    changed = true;
                                } else {
                                    let matches: Vec<_> = source
                                        .iter()
                                        .filter(|n| n.key().0 == TXTR)
                                        .filter(|n| {
                                            texture_aliases(n).is_ok_and(|a| {
                                                a.contains(&name.to_string().to_lowercase())
                                            })
                                        })
                                        .collect();
                                    ensure!(matches.len() == 1, "A material file list references an unresolved external texture");
                                    *name = qualified(
                                        s,
                                        matches[0].key(),
                                        plan.variant(matches[0].key(), elder)?,
                                    )
                                    .trim_end_matches("_txtr")
                                    .into();
                                }
                            }
                            ensure!(
                                changed,
                                "An ambiguous material texture list needs manual preparation"
                            );
                        }
                    }
                    plan.relink(s, source, &mut c, elder)?;
                    n.set(&c)?;
                }
                TXTR => {
                    let mut c = n.collection()?;
                    let ResourceData::Texture(t) = &mut c.entries[0].data else {
                        bail!("Invalid texture")
                    };
                    t.file_name.name = qualified(s, original, elder).into();
                    t.file_name_repeat = t.file_name.name.clone();
                    let path = if elder {
                        s.elder_textures.get(&key_text(original))
                    } else {
                        s.textures.get(&key_text(original))
                    };
                    let Some(path) = path else {
                        // Unused textures keep their encoded pixels and mips.
                        // Only names and links change so local references remain valid.
                        plan.relink(s, source, &mut c, elder)?;
                        n.set(&c)?;
                        nodes.push(n);
                        continue;
                    };
                    let img = crate::assets::open_image(path)?.into_rgba8();
                    ensure!(
                        img.dimensions() == (t.width, t.height),
                        "PNG dimensions must match its selected texture slot"
                    );
                    let format = t.get_format();
                    if matches!(format, TextureFormat::RawRGB24 | TextureFormat::AltRGB24) {
                        ensure!(
                            img.pixels().all(|p| p[3] == 255),
                            "This texture format cannot store PNG transparency"
                        );
                    }
                    if format == TextureFormat::DXT1 {
                        ensure!(img.pixels().all(|p|p[3]==0||p[3]==255),"DXT1 requires binary alpha. Preserve template alpha or prepare a cutout PNG");
                    }
                    t.compress_replace(
                        DecodedTexture {
                            width: t.width as usize,
                            height: t.height as usize,
                            data: img.into_raw(),
                        },
                        Some(TextureFormat::RawARGB32),
                    );
                    t.add_max_mip_levels(if format == TextureFormat::DXT1 {
                        Some(127)
                    } else {
                        None
                    });
                    crate::texture_encoding::recompress(t, format, s.texture_encoder)?;
                    plan.relink(s, source, &mut c, elder)?;
                    n.set(&c)?;
                }
                STR => {
                    let mut list = TextList::read_le(&mut Cursor::new(&n.bytes))?;
                    match &mut list.data {
                        VersionedTextList::Tagged { sets, .. } => {
                            for set in sets {
                                set.value = s.label.clone().into();
                            }
                        }
                        VersionedTextList::Untagged { sets, .. } => {
                            for set in sets {
                                set.value = s.label.clone().into();
                            }
                        }
                    }
                    n.set(&list)?;
                }
                _ => {}
            }
            nodes.push(n);
        }
    }
    ensure!(
        nodes.iter().map(Node::key).collect::<BTreeSet<_>>().len() == nodes.len(),
        "Generated resource identities collide"
    );
    Ok(nodes)
}
fn validate(s: &Spec, path: &Path) -> Result<Value> {
    let (_, source) = load(&s.template)?;
    let info = inspect_loaded_source(&s.template, &source)?;
    let slots: BTreeSet<_> = info.textures.iter().map(|slot| slot.id.clone()).collect();
    ensure!(
        s.textures.keys().cloned().collect::<BTreeSet<_>>() == slots,
        "Every texture slot needs an explicit assignment"
    );
    if s.grey_elders && !s.grey_only && info.ages.iter().any(|age| age.age == 16) {
        ensure!(
            s.elder_textures.keys().cloned().collect::<BTreeSet<_>>() == slots,
            "Elder texture assignments are incomplete"
        );
    }
    let (_, nodes) = load_raw(path)?;
    let plan = ResourcePlan::new(s, &source)?;
    let expected_keys: BTreeSet<_> = source
        .iter()
        .filter(|n| !plan.removed.contains(&n.key()))
        .flat_map(|n| {
            let variants = if [TXTR, TXMT].contains(&n.key().0) {
                plan.variants(n.key())
            } else {
                BTreeSet::from([false])
            };
            variants
                .into_iter()
                .map(move |elder| new_key(s, n.key(), elder))
        })
        .collect();
    ensure!(
        nodes.iter().map(Node::key).collect::<BTreeSet<_>>() == expected_keys,
        "Generated resources do not match the required age and dependency variants"
    );
    // Each of the 16 source textures can have its own natural elder variant.
    let result = inspect_with_limit(&nodes, None, 32)?;
    // Elder-only packages deliberately remove younger catalog entries and their
    // age-specific geometry. Check every retained 3IDR against its own source,
    // rather than requiring mesh links for ages the package no longer contains.
    for idr in nodes.iter().filter(|n| n.key().0 == IDR) {
        let original = source
            .iter()
            .find(|n| n.key().0 == IDR && n.key().2 == idr.key().2)
            .context("Unexpected generated 3IDR")?;
        let meshes = |node: &Node| -> Result<BTreeSet<Key>> {
            Ok(node
                .idr()?
                .entries
                .iter()
                .map(ref_key)
                .filter(|k| [CRES, SHPE].contains(&k.0))
                .collect())
        };
        ensure!(
            meshes(idr)? == meshes(original)?,
            "The external mesh links changed"
        );
    }
    ensure!(
        nodes.iter().all(|n| n.key().1 == s.group),
        "A resource retained a foreign namespace"
    );
    let expected: BTreeSet<_> = info
        .ages
        .iter()
        .filter(|a| !s.grey_only || a.age == 0x10)
        .map(|a| (a.age, a.gender))
        .collect();
    ensure!(
        result
            .ages
            .iter()
            .map(|a| (a.age, a.gender))
            .collect::<BTreeSet<_>>()
            == expected,
        "Generated ages or gender changed"
    );
    let mut texture_checks = vec![];
    for n in &nodes {
        if [TXMT, TXTR].contains(&n.key().0) {
            let (old, elder) = source
                .iter()
                .filter(|o| o.key().0 == n.key().0)
                .flat_map(|o| plan.variants(o.key()).into_iter().map(move |e| (o, e)))
                .find(|(o, e)| new_key(s, o.key(), *e) == n.key())
                .context("Unknown generated appearance resource")?;
            let mut expected = old.collection()?;
            plan.relink(s, &source, &mut expected, elder)?;
            let actual = n.collection()?;
            ensure!(
                actual.links == expected.links,
                "A local appearance resource link changed"
            );
            if n.key().0 == TXMT && !skin_material(old)? {
                let ResourceData::Material(before) = &expected.entries[0].data else {
                    bail!("Invalid material")
                };
                let ResourceData::Material(after) = &actual.entries[0].data else {
                    bail!("Invalid material")
                };
                let base = resolve_texture(&source, old)?;
                let target = |k| -> Result<String> {
                    Ok(qualified(s, k, plan.variant(k, elder)?)
                        .trim_end_matches("_txtr")
                        .into())
                };
                let names: Vec<String> = if before.names.len() <= 1 {
                    vec![target(base)?]
                } else {
                    before
                        .names
                        .iter()
                        .map(|name| {
                            target(hair_resources::named_texture(&source, &name.to_string())?)
                        })
                        .collect::<Result<_>>()?
                };
                ensure!(
                    material_texture(n)? == target(base)?,
                    "A material base texture is routed to the wrong variant"
                );
                ensure!(
                    after
                        .names
                        .iter()
                        .map(ToString::to_string)
                        .collect::<Vec<_>>()
                        == names,
                    "A material texture file list is routed to the wrong variant"
                );
            }
        }
        if n.key().0 == GZPS {
            let p = n.cpf()?;
            let age = number(&p, "age")?;
            ensure!(
                string(&p, "family")? == s.family,
                "Incorrect hairstyle family"
            );
            let tone = if s.grey_only || (age == 0x10 && s.grey_elders) {
                bin_id(5)
            } else if s.bin > 0 {
                bin_id(s.bin)
            } else {
                s.hairtone.clone()
            };
            ensure!(string(&p, "hairtone")? == tone, "Incorrect age hair bin");
            let original = source
                .iter()
                .find(|o| o.key().0 == GZPS && o.key().2 == n.key().2)
                .ok_or_else(|| anyhow!("Unexpected age record"))?;
            let before = original.cpf()?;
            let old_rendering = find(&source, rendering_idr(&source, original)?)?.idr()?;
            let new_rendering = find(&nodes, rendering_idr(&nodes, n)?)?.idr()?;
            for field in ["resourcekeyidx", "shapekeyidx"] {
                ensure!(
                    ref_key(&old_rendering.entries[number(&before, field)? as usize])
                        == ref_key(&new_rendering.entries[number(&p, field)? as usize]),
                    "The mesh assigned to an age changed"
                );
            }
            for prop in &before.entries {
                if !["family", "creator", "hairtone", "age"]
                    .contains(&String::from_utf8_lossy(&prop.name).as_ref())
                {
                    ensure!(
                        get(&p, &String::from_utf8_lossy(&prop.name))? == &prop.data,
                        "Unrelated age metadata changed"
                    );
                }
            }
            // Validate source subset routes, not an incidental texture-name suffix.
            let elder = !s.grey_only && s.grey_elders && age == 16;
            let old_materials = age_materials(&source, original)?;
            let new_materials = age_materials(&nodes, n)?;
            ensure!(
                old_materials.len() == new_materials.len(),
                "The age subset count changed"
            );
            for (old, actual) in old_materials.into_iter().zip(new_materials) {
                ensure!(
                    actual == new_key(s, old, plan.variant(old, elder)?),
                    "An age or subset is routed to the wrong material variant"
                );
                if skin_material(find(&source, old)?)? {
                    continue;
                }
                let texture = resolve_texture(&source, find(&source, old)?)?;
                ensure!(
                    resolve_texture(&nodes, find(&nodes, actual)?)?
                        == new_key(s, texture, plan.variant(texture, elder)?),
                    "An age or subset is routed to the wrong texture variant"
                );
            }
        } else if n.key().0 == XHTN {
            let p = n.cpf()?;
            ensure!(
                string(&p, "family")? == s.hairtone,
                "Incorrect custom hairtone identity"
            );
            ensure!(
                string(&p, "proxy")? == bin_id(s.bin),
                "Incorrect hairtone proxy"
            );
        } else if n.key().0 == TXTR {
            let c = n.collection()?;
            let ResourceData::Texture(t) = &c.entries[0].data else {
                bail!("Invalid texture")
            };
            let matched = source
                .iter()
                .filter(|o| o.key().0 == TXTR)
                .flat_map(|o| [false, true].map(move |e| (o, e)))
                .find(|(o, e)| new_key(s, o.key(), *e) == n.key())
                .ok_or_else(|| anyhow!("Unknown generated texture"))?;
            let original = matched.0.collection()?;
            let ResourceData::Texture(ot) = &original.entries[0].data else {
                bail!("Invalid source texture")
            };
            ensure!(
                t.get_format() == ot.get_format() && (t.width, t.height) == (ot.width, ot.height),
                "Texture dimensions or compression changed"
            );
            let path = if matched.1 {
                s.elder_textures.get(&key_text(matched.0.key()))
            } else {
                s.textures.get(&key_text(matched.0.key()))
            };
            let Some(path) = path else {
                let mut preserved = t.clone();
                preserved.file_name = ot.file_name.clone();
                preserved.file_name_repeat = ot.file_name_repeat.clone();
                ensure!(&preserved == ot, "Unassigned texture data changed");
                texture_checks.push(json!({"key":key_text(n.key()),"preserved":true,"format":format!("{:?}",t.get_format()),"mips":t.mip_levels(),"encoder":"preserved_source","alpha_max_error":0,"alpha_mean_error":0}));
                continue;
            };
            ensure!(t.mip_levels() == t.max_mip_levels(), "Incomplete mip chain");
            let input = crate::assets::open_image(path)?.into_rgba8();
            let d = t.decompress(0, t.mip_levels() - 1)?;
            let errors: Vec<_> = input
                .pixels()
                .zip(d.data.chunks_exact(4))
                .map(|(a, b)| a[3].abs_diff(b[3]) as u32)
                .collect();
            let max = *errors.iter().max().unwrap();
            let mean = errors.iter().sum::<u32>() as f64 / errors.len() as f64;
            let limit = if t.get_format() == TextureFormat::DXT3 {
                8
            } else if t.get_format() == TextureFormat::DXT5 {
                36
            } else {
                0
            };
            ensure!(
                max <= limit && mean <= if limit == 36 { 8.0 } else { limit as f64 },
                "Texture alpha differs beyond compression tolerance"
            );
            texture_checks.push(json!({"key":key_text(n.key()),"format":format!("{:?}",t.get_format()),"mips":t.mip_levels(),"encoder":s.texture_encoder.effective(t.get_format()),"alpha_max_error":max,"alpha_mean_error":mean}));
        } else if n.key().0 == TXMT {
            let c = n.collection()?;
            let ResourceData::Material(m) = &c.entries[0].data else {
                bail!("Invalid material")
            };
            if !skin_material(n)? {
                let target = material_texture(n)?;
                ensure!(
                    m.names
                        .iter()
                        .any(|n| n.to_string().to_lowercase() == target),
                    "Stale material texture file list"
                );
            }
            let (old, _) = source
                .iter()
                .filter(|o| o.key().0 == TXMT)
                .flat_map(|o| [false, true].map(move |e| (o, e)))
                .find(|(o, e)| new_key(s, o.key(), *e) == n.key())
                .ok_or_else(|| anyhow!("Unknown material"))?;
            let oc = old.collection()?;
            let ResourceData::Material(om) = &oc.entries[0].data else {
                bail!("Invalid material")
            };
            if skin_material(old)? {
                ensure!(om.names == m.names, "Scalp material file list changed");
            }
            ensure!(
                om.material_type == m.material_type,
                "Material shader changed"
            );
            ensure!(
                om.properties.len() == m.properties.len(),
                "Material properties changed"
            );
            for p in &om.properties {
                if p.name.to_string() != "stdMatBaseTextureName" {
                    ensure!(
                        m.properties.contains(p),
                        "Unrelated material settings changed"
                    );
                }
            }
        }
    }
    Ok(
        json!({"package_compression":crate::package_compression::report(path.to_str().context("Invalid package path")?,s.refpack_compression)?,"status":"passed","resource_count":nodes.len(),"resource_keys":nodes.iter().map(|n|key_text(n.key())).collect::<Vec<_>>(),"external_meshes":result.external_meshes,"ages":result.ages,"textures":texture_checks,"texture_encoding":s.texture_encoder.report(),"family":s.family,"hairtone":s.hairtone,"sha256":format!("{:x}",Sha256::digest(fs::read(path)?))}),
    )
}
pub fn cli() -> Result<()> {
    let output = match Cli::parse().command {
        Command::PrepareStandard {
            source,
            scaffold,
            output,
        } => standard_hair::prepare(&source, &scaffold, &output)?,
        Command::Inventory { package } => {
            let (_, nodes) = load_raw(&package)?;
            inventory(&nodes)?
        }
        Command::Inspect {
            package,
            export_dir,
        } => {
            let (_, nodes) = load(&package)?;
            serde_json::to_value(inspect(&nodes, export_dir.as_deref())?)?
        }
        Command::Build { spec, output } => {
            let s: Spec = serde_json::from_slice(&fs::read(spec)?)?;
            let (p, nodes) = load(&s.template)?;
            let mut generated = produce(&s, &nodes)?;
            write_output(p, &mut generated, &output, s.refpack_compression)?;
            validate(&s, &output)?
        }
        Command::Validate { spec, package } => {
            let s: Spec = serde_json::from_slice(&fs::read(spec)?)?;
            validate(&s, &package)?
        }
    };
    println!("{}", serde_json::to_string_pretty(&output)?);
    Ok(())
}

#[cfg(all(test, feature = "reference-tests"))]
mod tests {
    use super::*;

    fn template() -> PathBuf {
        std::env::var_os("PROJECT_FIXTURE_ROOT")
            .map(PathBuf::from)
            .unwrap_or_else(|| Path::new(env!("CARGO_MANIFEST_DIR")).join("../.."))
            .join("package_creation/hair/assets/swirl.package")
    }

    #[test]
    fn cached_source_inspection_retains_exact_top_mips_and_rejects_mutation() -> Result<()> {
        use dbpf::internal_file::resource_collection::texture_resource::TextureResourceData;
        let original = fs::read(template())?;
        let mut state = fs::Assets::default();
        fs::with(&mut state, || {
            fs::put_owned("hair-template", original)?;
            let (package, mut nodes) = load(Path::new("hair-template"))?;
            let uncached = inspect(&nodes, None)?;
            let info = inspect_loaded_source(Path::new("hair-template"), &nodes)?;
            assert!(Rc::ptr_eq(
                &info,
                &inspect_loaded_source(Path::new("hair-template"), &nodes)?
            ));
            assert_eq!(
                serde_json::to_value(&uncached)?,
                serde_json::to_value(info.as_ref())?
            );
            assert_eq!(info.images.len(), info.textures.len());
            assert!(info.pixel_bytes() <= 128 * 1024 * 1024);
            assert_eq!(
                inspect_asset("hair-template", Some("source"))?,
                serde_json::to_value(&uncached)?
            );
            for (id, image) in &info.images {
                let path = format!("source/{id}.png");
                assert_eq!(fs::image_get(&path).unwrap(), **image);
                let node = nodes.iter().find(|n| key_text(n.key()) == *id).unwrap();
                let collection = node.collection()?;
                let ResourceData::Texture(t) = &collection.entries[0].data else {
                    panic!()
                };
                assert_eq!(image.as_raw(), &t.decompress(0, t.mip_levels() - 1)?.data);
            }
            // Damage the smallest mip, which must still be validated on replacement.
            let node = nodes.iter_mut().find(|n| n.key().0 == TXTR).unwrap();
            let mut collection = node.collection()?;
            let ResourceData::Texture(t) = &mut collection.entries[0].data else {
                panic!()
            };
            t.textures[0].entries[0] = TextureResourceData::LIFOFile {
                file_name: "missing".into(),
            };
            node.set(&collection)?;
            write(package, &mut nodes, Path::new("hair-template"))?;
            assert!(inspect_asset("hair-template", None).is_err());
            fs::reset_inputs();
            assert!(inspect_asset("hair-template", None).is_err());
            assert!(crate::caches::hair_source_get("missing").is_none());
            Ok(())
        })?;
        assert!(state.images.is_empty());
        Ok(())
    }

    #[test]
    fn dbpf_12_roundtrip_retains_resources_and_rejects_invalid_bounds() -> Result<()> {
        let dir = tempfile::tempdir()?;
        let input = dir.path().join("v12.package");
        let output = dir.path().join("roundtrip.package");
        let mut bytes = fs::read(template())?;
        bytes[8..12].copy_from_slice(&2u32.to_le_bytes());
        fs::write(&input, &bytes)?;
        let (package, mut nodes) = load_raw(&input)?;
        let before = serde_json::to_value(inspect(&nodes, None)?)?;
        write(package, &mut nodes, &output)?;
        assert_eq!(&fs::read(&output)?[4..12], &[1, 0, 0, 0, 2, 0, 0, 0]);
        let (_, reopened) = load_raw(&output)?;
        assert_eq!(before, serde_json::to_value(inspect(&reopened, None)?)?);
        bytes[40..44].copy_from_slice(&u32::MAX.to_le_bytes());
        fs::write(&input, &bytes)?;
        assert!(
            load_raw(&input).is_err(),
            "DBPF 1.2 still requires valid index bounds"
        );
        Ok(())
    }

    fn multiple_textures(count: usize) -> Result<Vec<Node>> {
        let (_, mut nodes) = load(&template())?;
        let texture = nodes.iter_mut().find(|n| n.key().0 == TXTR).unwrap();
        let mut collection = texture.collection()?;
        let ResourceData::Texture(t) = &mut collection.entries[0].data else {
            panic!()
        };
        t.compress_replace(
            DecodedTexture {
                width: 16,
                height: 16,
                data: vec![255; 16 * 16 * 4],
            },
            Some(TextureFormat::DXT3),
        );
        texture.set(&collection)?;
        let original_texture = texture.clone();
        let original_material = nodes.iter().find(|n| n.key().0 == TXMT).unwrap().clone();
        let mut materials = vec![original_material.key()];
        for index in 1..count {
            let mut texture = original_texture.clone();
            texture.entry.instance_id = InstanceId {
                id: 0xa0000000 + index as u64,
            };
            let mut collection = texture.collection()?;
            let ResourceData::Texture(t) = &mut collection.entries[0].data else {
                panic!()
            };
            let base = format!("##0x{:08x}!fixture-{index}", texture.key().1);
            t.file_name.name = format!("{base}_txtr").into();
            t.file_name_repeat = t.file_name.name.clone();
            texture.set(&collection)?;
            nodes.push(texture);
            let mut material = original_material.clone();
            material.entry.instance_id = InstanceId {
                id: 0xb0000000 + index as u64,
            };
            let mut collection = material.collection()?;
            let ResourceData::Material(m) = &mut collection.entries[0].data else {
                panic!()
            };
            m.file_name.name = format!("{base}_txmt").into();
            m.material_description = base.clone().into();
            for prop in &mut m.properties {
                if prop.name.to_string() == "stdMatBaseTextureName" {
                    prop.value = base.clone().into();
                }
            }
            m.names = vec![base.into()];
            material.set(&collection)?;
            materials.push(material.key());
            nodes.push(material);
        }
        let ages: Vec<_> = nodes
            .iter()
            .filter(|n| n.key().0 == GZPS)
            .map(Node::key)
            .collect();
        for key in ages {
            let mut property = find(&nodes, key)?.cpf()?;
            let idr_key = rendering_idr(&nodes, find(&nodes, key)?)?;
            let mut idr = find(&nodes, idr_key)?.idr()?;
            let reference =
                idr.entries[number(&property, "override0resourcekeyidx")? as usize].clone();
            uint(&mut property, "numoverrides", count as u32);
            for (index, key) in materials.iter().enumerate() {
                let mut entry = reference.clone();
                entry.group_id = key.1;
                entry.instance_id = InstanceId { id: key.2 };
                uint(&mut property, &format!("override{index}shape"), 0);
                text_prop(
                    &mut property,
                    &format!("override{index}subset"),
                    &format!("hair_alpha{index}"),
                );
                uint(
                    &mut property,
                    &format!("override{index}resourcekeyidx"),
                    idr.entries.len() as u32,
                );
                idr.entries.push(entry);
            }
            nodes
                .iter_mut()
                .find(|n| n.key() == key)
                .unwrap()
                .set(&property)?;
            nodes
                .iter_mut()
                .find(|n| n.key() == idr_key)
                .unwrap()
                .set(&idr)?;
        }
        Ok(nodes)
    }

    #[test]
    fn active_texture_routes_deduplicate_and_preserve_unused_data() -> Result<()> {
        let mut nodes = multiple_textures(3)?;
        let info = inspect(&nodes, None)?;
        assert_eq!(info.textures.len(), 3);
        assert!(info
            .textures
            .iter()
            .all(|slot| slot.uses.len() == 3 && slot.ages.len() == 3));
        assert!(info
            .textures
            .iter()
            .any(|slot| slot.uses[0].subset == "hair_alpha2"));
        let mut unused = nodes.iter().find(|n| n.key().0 == TXTR).unwrap().clone();
        unused.entry.instance_id = InstanceId { id: 0xc0000000 };
        let mut collection = unused.collection()?;
        let ResourceData::Texture(t) = &mut collection.entries[0].data else {
            panic!()
        };
        t.file_name.name = "unused_fixture_txtr".into();
        t.file_name_repeat = t.file_name.name.clone();
        unused.set(&collection)?;
        nodes.push(unused);
        let info = inspect(&nodes, None)?;
        assert_eq!(
            info.textures.len(),
            3,
            "Unused TXTR must not require an assignment"
        );
        build_multiple_fixture(nodes, 3, 7, false)?;
        Ok(())
    }

    fn build_multiple_fixture(
        mut nodes: Vec<Node>,
        assignments: usize,
        generated_textures: usize,
        grey_only: bool,
    ) -> Result<()> {
        let dir = std::env::temp_dir().join(format!(
            "hair-multi-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)?
                .as_nanos()
        ));
        fs::create_dir_all(&dir)?;
        let (package, _) = load(&template())?;
        let source = dir.join("source.package");
        write(package.clone(), &mut nodes, &source)?;
        let info = inspect(&nodes, None)?;
        let mut images = BTreeMap::new();
        let mut elders = BTreeMap::new();
        for (index, slot) in info.textures.iter().enumerate() {
            let path = dir.join(format!("input-{index}.png"));
            let elder_path = dir.join(format!("elder-{index}.png"));
            let pixels = [index as u8 * 12, 80, 130, 255].repeat(16 * 16);
            crate::assets::save_buffer(&path, &pixels, 16, 16, image::ColorType::Rgba8)?;
            crate::assets::save_buffer(
                &elder_path,
                &[140, 140, 140, 255].repeat(16 * 16),
                16,
                16,
                image::ColorType::Rgba8,
            )?;
            images.insert(slot.id.clone(), path);
            elders.insert(slot.id.clone(), elder_path);
        }
        assert_eq!(images.len(), assignments);
        let spec = Spec {
            refpack_compression: true,
            texture_encoder: Default::default(),
            template: source,
            group: 0x5deadbee,
            family: "11111111-1111-4111-8111-111111111111".into(),
            hairtone: "22222222-2222-4222-8222-222222222222".into(),
            creator_uuid: "33333333-3333-4333-8333-333333333333".into(),
            label: "Multi texture fixture".into(),
            color: if grey_only { "Mail Bomb" } else { "Dynamite" }.into(),
            bin: if grey_only { 5 } else { 1 },
            grey_only,
            grey_elders: !grey_only,
            textures: images,
            elder_textures: elders,
        };
        let mut generated = produce(&spec, &nodes)?;
        let output = dir.join("output.package");
        write(package, &mut generated, &output)?;
        let report = validate(&spec, &output)?;
        assert_eq!(
            report["textures"].as_array().unwrap().len(),
            generated_textures
        );
        // Decode actual age/material routes and compare RGB against distinct
        // assigned inputs, so swapping equally sized atlases cannot pass.
        for gz in nodes.iter().filter(|n| n.key().0 == GZPS) {
            let age = number(&gz.cpf()?, "age")?;
            if grey_only && age != 16 {
                continue;
            }
            let elder = !grey_only && age == 16;
            let expected = age_materials(&nodes, gz)?;
            let actual = age_materials(
                &generated,
                find(&generated, new_key(&spec, gz.key(), false))?,
            )?;
            for (original, target) in expected.iter().zip(actual) {
                let original = resolve_texture(&nodes, find(&nodes, *original)?)?;
                let collection = find(
                    &generated,
                    resolve_texture(&generated, find(&generated, target)?)?,
                )?
                .collection()?;
                let ResourceData::Texture(t) = &collection.entries[0].data else {
                    panic!()
                };
                let decoded = t.decompress(0, t.mip_levels() - 1)?;
                let paths = if elder {
                    &spec.elder_textures
                } else {
                    &spec.textures
                };
                let input = crate::assets::open_image(&paths[&key_text(original)])?.into_rgba8();
                assert!(
                    input
                        .as_raw()
                        .iter()
                        .zip(decoded.data)
                        .all(|(a, b)| a.abs_diff(b) <= 8),
                    "A texture was routed to the wrong age or subset"
                );
            }
        }
        fs::remove_dir_all(dir)?;
        Ok(())
    }

    #[test]
    fn sixteen_assignments_allow_thirty_two_generated_textures() -> Result<()> {
        build_multiple_fixture(multiple_textures(16)?, 16, 32, false)?;
        assert!(inspect(&multiple_textures(17)?, None).is_err());
        Ok(())
    }

    // Small, fully linked fixture with a younger-only atlas and an Elder-only
    // atlas. Remove only synthetic leftover materials and their inactive slots.
    fn separate_age_fixture() -> Result<Vec<Node>> {
        let mut nodes = multiple_textures(2)?;
        for gz in nodes.iter_mut().filter(|n| n.key().0 == GZPS) {
            let mut p = gz.cpf()?;
            if number(&p, "age")? == 16 {
                let slot = number(&p, "override1resourcekeyidx")?;
                uint(&mut p, "override0resourcekeyidx", slot);
            }
            uint(&mut p, "numoverrides", 1);
            gz.set(&p)?;
        }
        let used: BTreeSet<_> = nodes
            .iter()
            .filter(|n| n.key().0 == GZPS)
            .map(|n| age_materials(&nodes, n))
            .collect::<Result<Vec<_>>>()?
            .into_iter()
            .flatten()
            .collect();
        let removed: BTreeSet<_> = nodes
            .iter()
            .filter(|n| n.key().0 == TXMT && !used.contains(&n.key()))
            .map(Node::key)
            .collect();
        nodes.retain(|n| !removed.contains(&n.key()));
        for n in nodes.iter_mut().filter(|n| n.key().0 == IDR) {
            let mut idr = n.idr()?;
            for e in &mut idr.entries {
                if removed.contains(&ref_key(e)) {
                    e.type_id = 0u32.into();
                    e.group_id = 0;
                    e.instance_id = InstanceId { id: 0 };
                }
            }
            n.set(&idr)?;
        }
        inspect(&nodes, None)?;
        Ok(nodes)
    }

    fn variant_spec(nodes: &[Node], dir: &Path) -> Result<Spec> {
        let source = dir.join("source.package");
        write(load(&template())?.0, &mut nodes.to_vec(), &source)?;
        let info = inspect(nodes, None)?;
        let mut textures = BTreeMap::new();
        let mut elder_textures = BTreeMap::new();
        for (i, slot) in info.textures.iter().enumerate() {
            let normal = dir.join(format!("target-{i}.png"));
            let elder = dir.join(format!("elder-{i}.png"));
            crate::assets::save_buffer(
                &normal,
                &[20, 60, 100, 255].repeat(16 * 16),
                16,
                16,
                image::ColorType::Rgba8,
            )?;
            crate::assets::save_buffer(
                &elder,
                &[150, 150, 150, 255].repeat(16 * 16),
                16,
                16,
                image::ColorType::Rgba8,
            )?;
            textures.insert(slot.id.clone(), normal);
            elder_textures.insert(slot.id.clone(), elder);
        }
        Ok(Spec {
            refpack_compression: true,
            texture_encoder: Default::default(),
            template: source,
            group: 0x5deadbee,
            family: "11111111-1111-4111-8111-111111111111".into(),
            hairtone: "22222222-2222-4222-8222-222222222222".into(),
            creator_uuid: "33333333-3333-4333-8333-333333333333".into(),
            label: "Age variants".into(),
            color: "Dynamite".into(),
            bin: 1,
            grey_only: false,
            grey_elders: true,
            textures,
            elder_textures,
        })
    }

    fn checked_variants(s: &Spec, nodes: &[Node]) -> Result<Vec<Node>> {
        let mut output = produce(s, nodes)?;
        let path = s.template.with_file_name("output.package");
        write(load(&s.template)?.0, &mut output, &path)?;
        validate(s, &path)?;
        let before = fs::read(&path)?;
        write(load(&s.template)?.0, &mut produce(s, nodes)?, &path)?;
        assert_eq!(
            fs::read(&path)?,
            before,
            "Retry must preserve identities and bytes"
        );
        Ok(output)
    }

    #[test]
    fn exclusive_age_resources_and_grey_only_pruning() -> Result<()> {
        let nodes = separate_age_fixture()?;
        let dir = tempfile::tempdir()?;
        let mut spec = variant_spec(&nodes, dir.path())?;
        let output = checked_variants(&spec, &nodes)?;
        assert_eq!(output.iter().filter(|n| n.key().0 == TXTR).count(), 2);
        assert_eq!(output.iter().filter(|n| n.key().0 == TXMT).count(), 2);
        spec.grey_only = true;
        spec.grey_elders = false;
        spec.bin = 5;
        let output = checked_variants(&spec, &nodes)?;
        assert_eq!(output.iter().filter(|n| n.key().0 == TXTR).count(), 1);
        assert_eq!(output.iter().filter(|n| n.key().0 == TXMT).count(), 1);
        assert_eq!(inspect(&output, None)?.ages.len(), 1);
        Ok(())
    }

    #[test]
    fn identical_shared_results_coalesce_without_merging_source_atlases() -> Result<()> {
        let nodes = multiple_textures(2)?;
        let dir = tempfile::tempdir()?;
        let mut spec = variant_spec(&nodes, dir.path())?;
        for (key, path) in &spec.elder_textures {
            fs::write(path, fs::read(&spec.textures[key])?)?;
        }
        let output = checked_variants(&spec, &nodes)?;
        assert_eq!(output.iter().filter(|n| n.key().0 == TXTR).count(), 2);
        assert_eq!(
            output.iter().filter(|n| n.key().0 == TXMT).count(),
            nodes.iter().filter(|n| n.key().0 == TXMT).count()
        );
        // An extra, unused Elder texture must no longer pass validation.
        let mut bad = output.clone();
        let source = nodes.iter().find(|n| n.key().0 == TXTR).unwrap();
        let mut extra = find(&bad, new_key(&spec, source.key(), false))?.clone();
        extra.entry.instance_id = InstanceId {
            id: new_key(&spec, source.key(), true).2,
        };
        bad.push(extra);
        let path = dir.path().join("bad.package");
        write(load(&spec.template)?.0, &mut bad, &path)?;
        assert!(validate(&spec, &path)
            .unwrap_err()
            .to_string()
            .contains("required age"));
        spec.grey_elders = false;
        spec.bin = 0;
        assert_eq!(
            checked_variants(&spec, &nodes)?
                .iter()
                .filter(|n| n.key().0 == TXTR)
                .count(),
            2
        );
        Ok(())
    }

    #[test]
    fn secondary_file_list_and_collection_links_retain_required_variants() -> Result<()> {
        let mut nodes = separate_age_fixture()?;
        let info = inspect(&nodes, None)?;
        let younger = info
            .textures
            .iter()
            .find(|t| !t.ages.contains(&16))
            .unwrap();
        let elder = info.textures.iter().find(|t| t.ages == [16]).unwrap();
        let tk = nodes
            .iter()
            .find(|n| key_text(n.key()) == elder.id)
            .unwrap()
            .key();
        let mk = info.ages.iter().find(|a| a.age != 16).unwrap().materials[0].clone();
        let material = nodes.iter_mut().find(|n| key_text(n.key()) == mk).unwrap();
        let mut collection = material.collection()?;
        let ResourceData::Material(m) = &mut collection.entries[0].data else {
            panic!()
        };
        m.names = vec![
            younger.name.trim_end_matches("_txtr").into(),
            elder.name.trim_end_matches("_txtr").into(),
        ];
        collection
            .links
            .push(dbpf::internal_file::resource_collection::FileLink {
                type_id: TXTR.into(),
                group_id: tk.1,
                instance_id: tk.2 as u32,
                resource_id: (tk.2 >> 32) as u32,
            });
        material.set(&collection)?;
        let dir = tempfile::tempdir()?;
        let spec = variant_spec(&nodes, dir.path())?;
        let output = checked_variants(&spec, &nodes)?;
        assert_eq!(output.iter().filter(|n| n.key().0 == TXTR).count(), 3);
        let mut bad = output.clone();
        let mat = bad
            .iter_mut()
            .find(|n| {
                n.key()
                    == new_key(
                        &spec,
                        (
                            TXMT,
                            tk.1,
                            u64::from_str_radix(mk.rsplit('-').next().unwrap(), 16).unwrap(),
                        ),
                        false,
                    )
            })
            .unwrap();
        let mut c = mat.collection()?;
        c.links[0].instance_id ^= 1;
        mat.set(&c)?;
        let path = dir.path().join("broken-link.package");
        write(load(&spec.template)?.0, &mut bad, &path)?;
        assert!(validate(&spec, &path).is_err());
        Ok(())
    }

    #[test]
    fn swapped_age_texture_routes_fail_validation() -> Result<()> {
        let nodes = separate_age_fixture()?;
        let dir = tempfile::tempdir()?;
        let spec = variant_spec(&nodes, dir.path())?;
        let mut output = checked_variants(&spec, &nodes)?;
        let elder = output
            .iter()
            .find(|n| n.key().0 == GZPS && number(&n.cpf().unwrap(), "age").unwrap() == 16)
            .unwrap();
        let younger = output
            .iter()
            .find(|n| n.key().0 == GZPS && number(&n.cpf().unwrap(), "age").unwrap() != 16)
            .unwrap();
        let wrong = age_materials(&output, younger)?[0];
        let idr_key = rendering_idr(&output, elder)?;
        let slot = number(&elder.cpf()?, "override0resourcekeyidx")? as usize;
        let n = output.iter_mut().find(|n| n.key() == idr_key).unwrap();
        let mut idr = n.idr()?;
        idr.entries[slot].group_id = wrong.1;
        idr.entries[slot].instance_id = InstanceId { id: wrong.2 };
        n.set(&idr)?;
        let path = dir.path().join("wrong-age.package");
        write(load(&spec.template)?.0, &mut output, &path)?;
        assert!(validate(&spec, &path)
            .unwrap_err()
            .to_string()
            .contains("wrong material variant"));
        Ok(())
    }

    #[test]
    fn untextured_scalp_stays_shared_in_natural_and_grey_only_outputs() -> Result<()> {
        let mut nodes = separate_age_fixture()?;
        let mut scalp = nodes.iter().find(|n| n.key().0 == TXMT).unwrap().clone();
        scalp.entry.instance_id = InstanceId { id: 0xfeed1234 };
        let key = scalp.key();
        let mut c = scalp.collection()?;
        let ResourceData::Material(m) = &mut c.entries[0].data else {
            panic!()
        };
        m.material_type = "SimSkin".into();
        m.properties
            .retain(|p| !p.name.to_string().ends_with("TextureName"));
        scalp.set(&c)?;
        nodes.push(scalp);
        let ages: Vec<_> = nodes
            .iter()
            .filter(|n| n.key().0 == GZPS)
            .map(Node::key)
            .collect();
        for age in ages {
            let mut p = find(&nodes, age)?.cpf()?;
            let idr_key = rendering_idr(&nodes, find(&nodes, age)?)?;
            let mut idr = find(&nodes, idr_key)?.idr()?;
            let mut entry = idr.entries[number(&p, "override0resourcekeyidx")? as usize].clone();
            entry.group_id = key.1;
            entry.instance_id = InstanceId { id: key.2 };
            uint(&mut p, "numoverrides", 2);
            uint(&mut p, "override1resourcekeyidx", idr.entries.len() as u32);
            idr.entries.push(entry);
            nodes.iter_mut().find(|n| n.key() == age).unwrap().set(&p)?;
            nodes
                .iter_mut()
                .find(|n| n.key() == idr_key)
                .unwrap()
                .set(&idr)?;
        }
        let dir = tempfile::tempdir()?;
        let mut spec = variant_spec(&nodes, dir.path())?;
        for grey_only in [false, true] {
            spec.grey_only = grey_only;
            spec.bin = if grey_only { 5 } else { 1 };
            let output = checked_variants(&spec, &nodes)?;
            assert!(output.iter().any(|n| n.key() == new_key(&spec, key, false)));
            assert!(!output.iter().any(|n| n.key() == new_key(&spec, key, true)));
        }
        Ok(())
    }

    #[test]
    fn invalid_catalog_index_and_missing_collection_dependency_return_errors() -> Result<()> {
        let mut nodes = separate_age_fixture()?;
        let dir = tempfile::tempdir()?;
        let spec = variant_spec(&nodes, dir.path())?;
        let bin = nodes.iter_mut().find(|n| n.key().0 == BINX).unwrap();
        let original = bin.bytes.clone();
        let mut p = bin.cpf()?;
        uint(&mut p, "iconidx", u32::MAX);
        bin.set(&p)?;
        assert!(produce(&spec, &nodes)
            .err()
            .unwrap()
            .to_string()
            .contains("Invalid catalog reference index"));
        nodes.iter_mut().find(|n| n.key().0 == BINX).unwrap().bytes = original;
        let material = nodes.iter_mut().find(|n| n.key().0 == TXMT).unwrap();
        let mut c = material.collection()?;
        c.links
            .push(dbpf::internal_file::resource_collection::FileLink {
                type_id: TXTR.into(),
                group_id: 0x5fffffff,
                instance_id: 12,
                resource_id: 0,
            });
        material.set(&c)?;
        assert!(produce(&spec, &nodes)
            .err()
            .unwrap()
            .to_string()
            .contains("unresolved texture or material link"));
        Ok(())
    }

    #[test]
    fn elder_rendering_survives_removal_of_younger_catalog_slots() -> Result<()> {
        let mut nodes = multiple_textures(3)?;
        let elder = nodes
            .iter()
            .find(|n| n.key().0 == GZPS && number(&n.cpf().unwrap(), "age").unwrap() == 16)
            .unwrap();
        let idr_key = rendering_idr(&nodes, elder)?;
        let younger = nodes
            .iter()
            .find(|n| n.key().0 == GZPS && n.key() != elder.key())
            .unwrap()
            .key();
        let mut entry = nodes
            .iter()
            .filter(|n| n.key().0 == IDR)
            .flat_map(|n| n.idr().unwrap().entries)
            .find(|e| ref_key(e) == younger)
            .unwrap();
        entry.group_id = younger.1;
        let node = nodes.iter_mut().find(|n| n.key() == idr_key).unwrap();
        let mut idr = node.idr()?;
        idr.entries.push(entry);
        node.set(&idr)?;
        build_multiple_fixture(nodes, 3, 3, true)?;
        Ok(())
    }

    #[test]
    fn catalog_follows_references_instead_of_age_instance_numbers() -> Result<()> {
        let (_, nodes) = load(&template())?;
        let info = inspect(&nodes, None)?;
        let elder = info.catalog.iter().find(|c| c["age"] == 16).unwrap();
        assert!(elder["key"].as_str().unwrap().ends_with("0000000000000002"));
        assert!(elder["object"]
            .as_str()
            .unwrap()
            .ends_with("0000000000000001"));
        Ok(())
    }

    #[test]
    fn pruned_catalog_repairs_only_unused_slots_and_unambiguous_hairtones() -> Result<()> {
        let (_, mut nodes) = load_raw(&template())?;
        let info = inspect(&nodes, None)?;
        let catalog = info.catalog.iter().find(|c| c["age"].is_null()).unwrap()["key"]
            .as_str()
            .unwrap();
        nodes.retain(|n| key_text(n.key()) != catalog);
        assert!(inspect(&nodes, None).is_err());
        let idr = nodes.iter_mut().find(|n| n.key().0 == IDR).unwrap();
        let mut links = idr.idr()?;
        let mut unused = links
            .entries
            .iter()
            .find(|e| e.type_id.code() == TXMT)
            .unwrap()
            .clone();
        unused.instance_id = InstanceId { id: 123456789 };
        links.entries.push(unused);
        idr.set(&links)?;
        normalize_catalog(&mut nodes)?;
        inspect(&nodes, None)?;
        let first = nodes.iter().find(|n| n.key().0 == GZPS).unwrap();
        let id = rendering_idr(&nodes, first)?;
        let slot = number(&first.cpf()?, "override0resourcekeyidx")? as usize;
        let idr = nodes.iter_mut().find(|n| n.key() == id).unwrap();
        let mut links = idr.idr()?;
        links.entries[slot].instance_id = InstanceId { id: 123456789 };
        idr.set(&links)?;
        assert!(normalize_catalog(&mut nodes).is_err());
        Ok(())
    }

    #[test]
    fn untextured_scalp_is_distinct_from_a_missing_hair_texture() -> Result<()> {
        let (_, mut nodes) = load(&template())?;
        let material = nodes.iter_mut().find(|n| n.key().0 == TXMT).unwrap();
        let mut c = material.collection()?;
        let ResourceData::Material(m) = &mut c.entries[0].data else {
            unreachable!()
        };
        m.properties
            .retain(|p| p.name.to_string() != "stdMatBaseTextureName");
        m.material_type = "SimStandardMaterial".into();
        material.set(&c)?;
        assert!(!skin_material(material)?);
        assert!(material_texture(material).is_err());
        let ResourceData::Material(m) = &mut c.entries[0].data else {
            unreachable!()
        };
        m.material_type = "SimSkin".into();
        let original_names = m.names.clone();
        material.set(&c)?;
        assert!(skin_material(material)?);
        inspect(&nodes, None)?;
        let material = nodes
            .iter()
            .find(|n| n.key().0 == TXMT)
            .unwrap()
            .collection()?;
        let ResourceData::Material(m) = &material.entries[0].data else {
            unreachable!()
        };
        assert_eq!(m.names, original_names);
        Ok(())
    }

    #[test]
    fn formats_mips_compressed_alpha_unknown_properties_and_retry() -> Result<()> {
        let unique = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)?
            .as_nanos();
        let dir = std::env::temp_dir().join(format!("hair-rust-{unique}"));
        fs::create_dir_all(&dir)?;
        for format in [
            TextureFormat::DXT1,
            TextureFormat::DXT3,
            TextureFormat::DXT5,
            TextureFormat::RawARGB32,
        ] {
            let (package, mut nodes) = load(&template())?;
            let mut pixels = vec![];
            for y in 0..16 {
                for x in 0..16 {
                    let alpha = if format == TextureFormat::DXT1 {
                        if (x + y) % 2 == 0 {
                            0
                        } else {
                            255
                        }
                    } else {
                        (x * 16 + y) as u8
                    };
                    pixels.extend([x as u8 * 16, y as u8 * 16, 128, alpha]);
                }
            }
            let input = dir.join("input.png");
            crate::assets::save_buffer(&input, &pixels, 16, 16, image::ColorType::Rgba8)?;
            let texture = nodes.iter_mut().find(|n| n.key().0 == TXTR).unwrap();
            let key = key_text(texture.key());
            let mut collection = texture.collection()?;
            let ResourceData::Texture(t) = &mut collection.entries[0].data else {
                panic!()
            };
            t.compress_replace(
                DecodedTexture {
                    width: 16,
                    height: 16,
                    data: pixels,
                },
                Some(format),
            );
            texture.set(&collection)?;
            let age = nodes.iter_mut().find(|n| n.key().0 == GZPS).unwrap();
            let mut cpf = age.cpf()?;
            text_prop(&mut cpf, "future_unknown_property", "must survive intact");
            age.set(&cpf)?;
            let source = dir.join("source.package");
            write(package, &mut nodes, &source)?;
            let (package, source_nodes) = load(&source)?;
            let spec = Spec {
                refpack_compression: true,
                texture_encoder: Default::default(),
                template: source,
                group: 0x5deadbee,
                family: "11111111-1111-4111-8111-111111111111".into(),
                hairtone: "22222222-2222-4222-8222-222222222222".into(),
                creator_uuid: "33333333-3333-4333-8333-333333333333".into(),
                label: "Alpha fixture".into(),
                color: "TNT".into(),
                bin: 0,
                grey_only: false,
                grey_elders: false,
                textures: BTreeMap::from([(key, input)]),
                elder_textures: BTreeMap::new(),
            };
            let mut generated = produce(&spec, &source_nodes)?;
            let output = dir.join("output.package");
            write(package.clone(), &mut generated, &output)?;
            let report = validate(&spec, &output)?;
            assert_eq!(report["textures"][0]["mips"], 5);
            let first = fs::read(&output)?;
            let mut retried = produce(&spec, &source_nodes)?;
            write(package.clone(), &mut retried, &output)?;
            assert_eq!(
                first,
                fs::read(&output)?,
                "Retry bytes must be reproducible"
            );
            let age = retried.iter_mut().find(|n| n.key().0 == GZPS).unwrap();
            let mut cpf = age.cpf()?;
            text_prop(&mut cpf, "hairtone", "00000003-0000-0000-0000-000000000000");
            age.set(&cpf)?;
            write(package, &mut retried, &output)?;
            assert!(
                validate(&spec, &output).is_err(),
                "Wrong bins must fail independent validation"
            );
        }
        fs::remove_dir_all(dir)?;
        Ok(())
    }
}

pub(crate) fn inspect_asset(name: &str, export: Option<&str>) -> Result<Value> {
    let key = format!("hair-inspection-v1:{}", fs::cache_identity(name)?);
    if let Some(info) = crate::caches::hair_source_get(&key) {
        if export.is_none() || info.images.len() == info.textures.len() {
            if let Some(dir) = export {
                info.export(Path::new(dir))?;
            }
            return Ok(serde_json::to_value(info.as_ref())?);
        }
    }
    let (_, nodes) = load(Path::new(name))?;
    let info = inspect_loaded_source(Path::new(name), &nodes)?;
    if let Some(dir) = export {
        if info.images.len() == info.textures.len() {
            info.export(Path::new(dir))?;
        } else {
            // Oversized sources still export every active atlas, without retaining
            // pixels beyond the cache budget or skipping mip validation.
            inspect(&nodes, Some(Path::new(dir)))?;
        }
    }
    Ok(serde_json::to_value(info.as_ref())?)
}
pub(crate) fn inventory_asset(name: &str) -> Result<Value> {
    let (_, nodes) = load_raw(Path::new(name))?;
    inventory(&nodes)
}
pub(crate) fn build_asset(spec: Value, output: &str) -> Result<Value> {
    let spec: Spec = serde_json::from_value(spec)?;
    let (package, nodes) = load(&spec.template)?;
    let info = inspect_loaded_source(&spec.template, &nodes)?;
    let mut generated = produce_inspected(&spec, &nodes, &info)?;
    write_output(
        package,
        &mut generated,
        Path::new(output),
        spec.refpack_compression,
    )?;
    validate(&spec, Path::new(output))
}
pub(crate) fn validate_asset(spec: Value, output: &str) -> Result<Value> {
    validate(&serde_json::from_value(spec)?, Path::new(output))
}
