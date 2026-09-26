//! Independent object clones, with explicit supported behavior profiles.
use crate::{
    assets,
    core::resources::{self, Node},
    object_scene::{self as scene, Key},
};
use anyhow::{bail, ensure, Context, Result};
use binrw::{BinRead, BinWrite};
use dbpf::{
    header_v1::InstanceId,
    internal_file::{
        object_data::ObjectData,
        resource_collection::ResourceData,
        text_list::{TextList, VersionedTextList},
    },
    DBPFFile,
};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    io::Cursor,
    path::Path,
};

const OBJD: u32 = 0x4f424a44;
const BHAV: u32 = 0x42484156;
const GLOB: u32 = 0x474c4f42;
const CTSS: u32 = 0x43545353;
const STR: u32 = 0x53545223;
const MMAT: u32 = 0x4c697e5a;
const CRES: u32 = 0xe519c933;
const SHPE: u32 = 0xfc6eb1f7;
const GMND: u32 = 0x7ba3838c;
const GMDC: u32 = 0xac4f8687;
const TXMT: u32 = 0x49596978;
const TXTR: u32 = 0x1c4a276c;
const LGHT: u32 = 0xc9c81ba9;
const SCENE_GROUP: u32 = 0x1c050000;
fn hash(b: impl AsRef<[u8]>) -> String {
    format!("{:x}", Sha256::digest(b.as_ref()))
}
fn field<'a>(v: &'a Value, k: &str) -> Result<&'a str> {
    v[k].as_str().with_context(|| format!("Missing {k}"))
}
fn obj(n: &Node) -> Result<ObjectData> {
    Ok(ObjectData::read_le(&mut Cursor::new(&n.bytes))?)
}
fn named(t: u32) -> bool {
    [CRES, SHPE, GMND, GMDC, TXMT, TXTR, LGHT].contains(&t)
}
fn metadata(n: &Node) -> Result<String> {
    Ok(match n.key().0 {
        CRES | SHPE | GMND | LGHT => scene::read(&n.bytes)?.name,
        GMDC | TXMT | TXTR => match &n
            .collection()?
            .entries
            .first()
            .context("Empty scene collection")?
            .data
        {
            ResourceData::Mesh(m) => m.file_name.name.to_string(),
            ResourceData::Material(m) => m.file_name.name.to_string(),
            ResourceData::Texture(t) => t.file_name.name.to_string(),
        },
        _ => String::new(),
    })
}
fn language(v: dbpf::common::LanguageCode) -> u8 {
    match v {
        dbpf::common::LanguageCode::Known(k) => k as u8,
        dbpf::common::LanguageCode::Unknown(k) => k,
    }
}
fn texts(n: &Node) -> Result<Vec<String>> {
    let t = TextList::read_le(&mut Cursor::new(&n.bytes))?;
    Ok(match t.data {
        VersionedTextList::Tagged { sets, .. } => sets
            .iter()
            .filter(|s| language(s.language_code) == 1)
            .map(|s| s.value.to_string())
            .collect(),
        VersionedTextList::Untagged { sets, .. } => {
            sets.iter().map(|s| s.value.to_string()).collect()
        }
    })
}
fn unused_material_table(n: &Node) -> Result<bool> {
    Ok(n.key().0 == STR
        && n.key().2 == 0x88
        && texts(n)?
            .iter()
            .all(|s| s.is_empty() || (1..=7).any(|i| *s == format!("Material Name {i}"))))
}
fn source(files: &[String]) -> Result<(DBPFFile, Vec<Node>)> {
    ensure!(
        !files.is_empty() && files.len() <= 64,
        "Choose between 1 and 64 object packages"
    );
    let mut bytes = 0;
    let mut container = None;
    let mut nodes = BTreeMap::new();
    let mut names = BTreeSet::new();
    for file in files {
        ensure!(
            file.to_ascii_lowercase().ends_with(".package") || file == "selected-object",
            "Choose .package files for an existing object"
        );
        ensure!(
            names.insert(file.to_ascii_lowercase()),
            "Conflicting package filenames"
        );
        bytes += assets::read(file)?.len();
        ensure!(bytes <= 128 * 1024 * 1024, "Object packages exceed 128 MiB");
        let (p, items) = resources::load_raw(Path::new(file))?;
        if container.is_none() {
            container = Some(p);
        }
        for n in items {
            ensure!(
                nodes.insert(n.key(), n).is_none(),
                "Package resource identities conflict. Select each dependency once"
            );
        }
        ensure!(
            nodes.len() <= 8192,
            "Object package set has too many resources"
        );
    }
    let mut nodes = nodes.into_values().collect::<Vec<_>>();
    resources::embed_game_mips(&mut nodes)?;
    Ok((container.unwrap(), nodes))
}
fn catalog() -> Result<Value> {
    Ok(serde_json::from_slice(&assets::read("object-catalog")?)?)
}
fn game() -> Result<Value> {
    Ok(serde_json::from_slice(&assets::read("object-game")?)?)
}
fn behavior_signature(nodes: &[Node]) -> Result<BTreeSet<String>> {
    let local_guids = nodes
        .iter()
        .filter(|n| n.key().0 == OBJD)
        .map(|n| Ok((obj(n)?.guid, n.key().2 as u32)))
        .collect::<Result<BTreeMap<_, _>>>()?;
    let mut signature = BTreeSet::new();
    for n in nodes {
        let (kind, _, instance) = n.key();
        let bytes = if kind == OBJD {
            let mut o = obj(n)?;
            o.file_name = Default::default();
            o.price = 0;
            for guid in [
                &mut o.guid,
                &mut o.diagonal_selector_guid,
                &mut o.grid_aligned_selector_guid,
                &mut o.proxy_guid,
                &mut o.job_object_guid,
                &mut o.original_guid,
                &mut o.type_attribute_guid,
            ] {
                if let Some(id) = local_guids.get(guid) {
                    *guid = *id;
                }
            }
            let mut b = Cursor::new(Vec::new());
            o.write_le(&mut b)?;
            b.into_inner()
        } else if [
            BHAV, GLOB, 0x42434f4e, 0x534c4f54, 0x4f424a66, 0x54544142, 0x54544173,
        ]
        .contains(&kind)
            || kind == STR && (![0x85, 0x88].contains(&instance) || unused_material_table(n)?)
        {
            n.bytes.clone()
        } else {
            continue;
        };
        signature.insert(format!("{kind:08x}:{instance:016x}:{}", hash(bytes)));
    }
    Ok(signature)
}
fn profile<'a>(nodes: &[Node], catalog: &'a Value) -> Result<&'a Value> {
    let objects = nodes
        .iter()
        .filter(|n| n.key().0 == OBJD)
        .map(obj)
        .collect::<Result<Vec<_>>>()?;
    ensure!(
        !objects.is_empty(),
        "No object definition found. Choose an object package, not a mesh or recolor alone"
    );
    let groups = nodes
        .iter()
        .filter(|n| n.key().0 == OBJD)
        .map(|n| n.key().1)
        .collect::<BTreeSet<_>>();
    ensure!(groups.len()==1,"Choose one object and its appearance dependencies. Merged object collections are unsupported");
    let group = *groups.first().unwrap();
    ensure!(
        objects
            .iter()
            .map(|o| o.guid)
            .collect::<BTreeSet<_>>()
            .len()
            == objects.len(),
        "Object definitions have duplicate GUIDs"
    );
    if objects.len() > 1 {
        let master = objects
            .iter()
            .find(|o| o.multi_tile_sub_index == 0xffff)
            .context("Multi-tile master is missing")?;
        ensure!(
            master.multi_tile_master_id != 0
                && objects
                    .iter()
                    .all(|o| o.multi_tile_master_id == master.multi_tile_master_id),
            "Multi-tile definitions do not share a master"
        );
        let offsets = objects
            .iter()
            .map(|o| o.multi_tile_sub_index)
            .collect::<BTreeSet<_>>();
        ensure!(
            offsets.len() == objects.len(),
            "Multi-tile offsets conflict"
        );
    }
    ensure!(
        objects.iter().all(|o| o.is_global_sim_object == 0),
        "Global objects and default replacements cannot be cloned"
    );
    let masters = objects
        .iter()
        .filter(|o| o.multi_tile_master_id == 0 || o.multi_tile_sub_index == 0xffff)
        .count();
    ensure!(
        masters == 1,
        "The package must contain one complete object, including every tile definition"
    );
    let behavior = nodes
        .iter()
        .filter(|n| n.key().0 == BHAV)
        .map(|n| format!("{:x}:{}", n.key().2, hash(&n.bytes)))
        .collect::<BTreeSet<_>>();
    let global = nodes
        .iter()
        .filter(|n| n.key().0 == GLOB)
        .map(|n| hash(&n.bytes))
        .collect::<BTreeSet<_>>();
    ensure!(
        nodes
            .iter()
            .filter(|n| [
                BHAV, GLOB, OBJD, CTSS, STR, 0x42434f4e, 0x534c4f54, 0x4f424a66, 0x54544142,
                0x54544173, 0x54505250, 0x5452434e
            ]
            .contains(&n.key().0))
            .all(|n| n.key().1 == group),
        "Shared custom behavior modules are unsupported. Choose a standalone object"
    );
    let signature = behavior_signature(nodes)?;
    let mut closest_changed = BTreeSet::new();
    for p in catalog["items"]
        .as_array()
        .context("Object profiles are missing")?
    {
        let known = p["behavior"]
            .as_array()
            .context("Object behavior profile is damaged")?
            .iter()
            .filter_map(Value::as_str)
            .map(str::to_string)
            .collect::<BTreeSet<_>>();
        let globals = p["globals"]
            .as_array()
            .unwrap()
            .iter()
            .filter_map(Value::as_str)
            .map(str::to_string)
            .collect::<BTreeSet<_>>();
        if known == behavior
            && globals == global
            && p["object_count"].as_u64() == Some(objects.len() as u64)
        {
            let expected = p["behavior_resources"]
                .as_array()
                .context("Object profile has no behavior resource validation")?
                .iter()
                .filter_map(Value::as_str)
                .map(str::to_string)
                .collect::<BTreeSet<_>>();
            if expected == signature {
                return Ok(p);
            }
            let changed = signature
                .symmetric_difference(&expected)
                .map(|s| s.rsplit_once(':').unwrap().0.to_string())
                .collect::<BTreeSet<_>>();
            if closest_changed.is_empty() || changed.len() < closest_changed.len() {
                closest_changed = changed;
            }
        }
    }
    if !closest_changed.is_empty() {
        bail!("This object's behavior is outside the supported profile. Changed behavior resources: {}", closest_changed.into_iter().take(8).collect::<Vec<_>>().join(", "));
    }
    bail!("This object's behavior is outside the supported profiles. Choose a standard object or a clone with unchanged behavior")
}
fn aliases(nodes: &[Node]) -> Result<BTreeMap<String, Key>> {
    let mut names = BTreeMap::new();
    for n in nodes.iter().filter(|n| named(n.key().0)) {
        let full = metadata(n)?.to_ascii_lowercase();
        ensure!(
            full.rsplit_once('_')
                .is_some_and(|(name, suffix)| !name.is_empty()
                    && ["cres", "shpe", "gmnd", "gmdc", "txmt", "txtr", "lght"].contains(&suffix)),
            "Object resource has an invalid scene name"
        );
        let stem = full.rsplit_once('_').map(|x| x.0).unwrap_or(&full);
        for s in [&full, stem] {
            let k = format!("{:08x}:{s}", n.key().0);
            ensure!(
                names.insert(k, n.key()).is_none(),
                "Conflicting object resource names"
            );
        }
        for s in [&full, stem] {
            names.insert(
                format!("{:08x}:##0x{:08x}!{s}", n.key().0, n.key().1),
                n.key(),
            );
        }
    }
    Ok(names)
}
fn check_links(nodes: &[Node], game: &Value) -> Result<Vec<String>> {
    let keys = nodes.iter().map(Node::key).collect::<BTreeSet<_>>();
    let names = aliases(nodes)?;
    let mut external = BTreeSet::new();
    let game_keys = game["keys"]
        .as_array()
        .context("Verified game reference catalog is missing")?
        .iter()
        .filter_map(Value::as_str)
        .collect::<BTreeSet<_>>();
    let mut check = |t: u32, name: &str| -> Result<()> {
        if name.is_empty() {
            return Ok(());
        }
        let name = name.to_ascii_lowercase();
        if names.contains_key(&format!("{t:08x}:{name}")) {
            return Ok(());
        }
        let clean = name.split('!').next_back().unwrap();
        let suffix = match t {
            CRES => "cres",
            GMND => "gmnd",
            TXMT => "txmt",
            TXTR => "txtr",
            _ => "",
        };
        let full = if clean.ends_with(&format!("_{suffix}")) {
            clean.to_string()
        } else {
            format!("{clean}_{suffix}")
        };
        ensure!(
            game["names"][&full]
                .as_array()
                .is_some_and(|a| a.iter().any(|k| k
                    .as_str()
                    .is_some_and(|s| s.starts_with(&format!("{t:08x}-"))))),
            "Missing dependency {name}. Include its matching mesh or texture package"
        );
        ensure!(
            !name.starts_with("##") || name.starts_with("##0x1c0532fa!"),
            "Missing custom dependency {name}. Include its package"
        );
        external.insert(full);
        Ok(())
    };
    for n in nodes {
        match n.key().0 {
            CRES | GMND | SHPE | LGHT => {
                let s = scene::read(&n.bytes)?;
                for (_, k) in s.links {
                    ensure!(
                        keys.contains(&k) || game_keys.contains(resources::key_text(k).as_str()),
                        "Missing scene reference {}. Include its package",
                        resources::key_text(k)
                    );
                }
                for (_, m) in s.parts {
                    check(TXMT, &m)?;
                }
                for m in s.geometry {
                    check(GMND, &m)?;
                }
            }
            TXMT => {
                let c = n.collection()?;
                if let ResourceData::Material(m) = &c.entries[0].data {
                    for p in &m.properties {
                        let key = p.name.to_string();
                        if key.ends_with("TextureName") && !p.value.to_string().is_empty() {
                            check(TXTR, &p.value.to_string())?;
                        }
                    }
                    for name in &m.names {
                        check(TXTR, &name.to_string())?;
                    }
                }
            }
            STR if n.key().2 == 0x85 || n.key().2 == 0x88 => {
                if unused_material_table(n)? {
                    continue;
                }
                for s in texts(n)? {
                    check(if n.key().2 == 0x85 { CRES } else { TXMT }, &s)?;
                }
            }
            MMAT => {
                let c = n.cpf()?;
                check(CRES, &resources::string(&c, "modelName")?)?;
                check(TXMT, &resources::string(&c, "name")?)?;
            }
            _ => {}
        }
    }
    Ok(external.into_iter().collect())
}
pub(crate) fn placement(nodes: &[Node]) -> Result<Value> {
    let c = catalog()?;
    let p = profile(nodes, &c)?;
    let mut tiles = Vec::new();
    for n in nodes.iter().filter(|n| n.key().0 == OBJD) {
        let o = obj(n)?;
        let v = o.multi_tile_sub_index;
        if v != 0xffff {
            tiles.push([(v >> 8) as u8 as i8 as i32, (v & 255) as u8 as i8 as i32]);
        }
    }
    if tiles.is_empty() {
        tiles.push([0, 0]);
    }
    tiles.sort();
    tiles.dedup();
    // Only inspected built-in behavior profiles have published placement coverage.
    Ok(
        json!({"known":p["placement"]["known"]==true,"tiles":tiles,"tile_count":tiles.len(),
        "surface":if p["kind"]=="wall-decor" {"wall"} else if p["kind"]=="tabletop-decor" {"tabletop"} else {"floor"},
        "routing":"unchanged template behavior"}),
    )
}
fn info(nodes: &[Node], p: &Value, game: &Value) -> Result<Value> {
    let objects=nodes.iter().filter(|n|n.key().0==OBJD).map(|n|{let o=obj(n)?;Ok(json!({"guid":format!("{:08x}",o.guid),"master":o.multi_tile_master_id,"tile":o.multi_tile_sub_index,"price":o.price,"key":resources::key_text(n.key())}))}).collect::<Result<Vec<_>>>()?;
    let mut meshes = Vec::new();
    let mut textures = Vec::new();
    for n in nodes {
        match n.key().0 {
            GMDC => {
                let c = n.collection()?;
                if let ResourceData::Mesh(m) = &c.entries[0].data {
                    meshes.push(json!({"name":m.file_name.name.to_string(),"key":resources::key_text(n.key()),"subsets":m.meshes.iter().map(|s|json!({"name":s.name.to_string(),"triangles":s.indices.len()/3})).collect::<Vec<_>>()}));
                }
            }
            TXTR => {
                let c = n.collection()?;
                if let ResourceData::Texture(t) = &c.entries[0].data {
                    textures.push(json!({"name":t.file_name.name.to_string(),"width":t.width,"height":t.height,"mips":t.mip_levels(),"format":format!("{:?}",t.get_format())}));
                }
            }
            _ => {}
        }
    }
    let external = check_links(nodes, game)?;
    Ok(
        json!({"profile":p["id"],"label":p["label"],"kind":p["kind"],"requirements":p["requirements"],"placement":placement(nodes)?,"dimensions":crate::object_model::template_bounds(nodes).ok().map(|(a,b)|json!({"width":b[0]-a[0],"height":b[2]-a[2],"depth":b[1]-a[1]})),"objects":objects,"meshes":meshes,"textures":textures,"external":external,"resource_count":nodes.len(),"can_import_model":p["kind"]=="floor-decor"||p["kind"]=="tabletop-decor"}),
    )
}
pub fn inspect(files: &[String], trusted: bool) -> Result<Value> {
    let (container, nodes) = source(files)?;
    let catalog = catalog()?;
    let p = profile(&nodes, &catalog)?;
    let g = game()?;
    if !trusted {
        let guids = g["guids"]
            .as_array()
            .context("Game GUID catalog missing")?
            .iter()
            .filter_map(Value::as_str)
            .collect::<BTreeSet<_>>();
        for n in nodes.iter().filter(|n| n.key().0 == OBJD) {
            ensure!(!guids.contains(format!("{:08x}",obj(n)?.guid).as_str()),"This package uses an existing game GUID. Default replacements are unsupported. Choose a standard template instead");
        }
    }
    let result = info(&nodes, p, &g)?;
    let mut nodes = nodes;
    resources::write(container, &mut nodes, Path::new("object-template"))?;
    Ok(result)
}
pub fn prepare(job: &Value) -> Result<Value> {
    let encoder = crate::texture_encoding::Encoder::from_job(job)?;
    let mut job = job.clone();
    job["texture_encoder"] = json!(encoder);
    job["refpack_compression"] = json!(crate::package_compression::from_job(&job)?);
    let id = field(&job, "id")?;
    ensure!(
        (16..=64).contains(&id.len()) && id.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-'),
        "Invalid object batch identity"
    );
    for key in ["creator", "object_name"] {
        let n = field(&job, key)?;
        ensure!(
            !n.is_empty()
                && n.len() <= 48
                && n.as_bytes()[0].is_ascii_alphanumeric()
                && n.bytes()
                    .all(|b| b.is_ascii_alphanumeric() || [b'_', b'-'].contains(&b)),
            "Creator and object filename need 1 to 48 letters, numbers, underscores or hyphens"
        );
    }
    for (key, limit) in [("title", 100), ("description", 1000)] {
        let n = field(&job, key)?;
        ensure!(
            n.chars().count() <= limit
                && !n.contains('\0')
                && (key != "title" || !n.trim().is_empty()),
            "Invalid catalog {key}"
        );
    }
    ensure!(
        job["credits"].is_null()
            || job["credits"]
                .as_str()
                .is_some_and(|s| s.chars().count() <= 1000 && !s.contains('\0')),
        "Credits must contain at most 1000 characters"
    );
    ensure!(
        job["price"].as_u64().is_some_and(|n| n <= 65535),
        "Price must be a whole number from 0 to 65535"
    );
    ensure!(
        ["clone", "model"].contains(&field(&job, "mode")?),
        "Choose clone or model import"
    );
    let (_, nodes) = resources::load_raw(Path::new("object-template"))?;
    let c = catalog()?;
    let p = profile(&nodes, &c)?;
    ensure!(
        job["mode"] != "model" || p["kind"] == "floor-decor" || p["kind"] == "tabletop-decor",
        "Model replacement is supported for decorations only"
    );
    let mut guids = BTreeMap::new();
    let mut used = game()?["guids"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(Value::as_str)
        .map(str::to_string)
        .collect::<BTreeSet<_>>();
    for n in nodes.iter().filter(|n| n.key().0 == OBJD) {
        used.insert(format!("{:08x}", obj(n)?.guid));
    }
    for n in nodes.iter().filter(|n| n.key().0 == OBJD) {
        let source = format!("{:08x}", obj(n)?.guid);
        let mut nonce = 0;
        loop {
            let v = hash(format!("object:{id}:{source}:{nonce}"))[..8].to_string();
            if v != "00000000" && v != "ffffffff" && used.insert(v.clone()) {
                guids.insert(source, v);
                break;
            }
            nonce += 1;
        }
    }
    let g = game()?;
    let occupied = g["groups"]
        .as_array()
        .map(|a| {
            a.iter()
                .filter_map(Value::as_str)
                .map(str::to_string)
                .collect::<BTreeSet<_>>()
        })
        .unwrap_or_default();
    let mut nonce = 0;
    let group = loop {
        let candidate =
            0x7f000000 | resources::crc(&format!("object-{id}-{nonce}"), 24, 0x1864cfb, 0xb704ce);
        if !occupied.contains(&format!("{candidate:08x}"))
            && !nodes.iter().any(|n| n.key().1 == candidate)
        {
            break candidate;
        }
        nonce += 1;
    };
    job["identities"] = json!({"guids":guids,"group":format!("{group:08x}"),"prefix":format!("obj-{}",&hash(id)[..24])});
    job["profile"] = p["id"].clone();
    job["requirements"] = p["requirements"].clone();
    let source_hash = hash(assets::read("object-template")?);
    ensure!(
        job["source_sha256"].is_null() || job["source_sha256"] == source_hash,
        "Saved object source changed. Start a new batch"
    );
    job["source_sha256"] = json!(source_hash);
    if !job["target_height"].is_null() || job["fit_to_template"] == true {
        job["layout"] = crate::object_model::layout(&job)?;
    }
    Ok(job)
}
fn renamed_typed(names: &BTreeMap<String, String>, kind: u32, s: &str) -> String {
    names
        .get(&format!("{kind:08x}:{}", s.to_ascii_lowercase()))
        .cloned()
        .unwrap_or_else(|| s.into())
}
fn edit_text(n: &mut Node, names: &BTreeMap<String, String>, job: &Value) -> Result<()> {
    let mut t = TextList::read_le(&mut Cursor::new(&n.bytes))?;
    if let VersionedTextList::Tagged { sets, .. } = &mut t.data {
        let mut count = BTreeMap::new();
        for s in sets {
            let i = count.entry(language(s.language_code)).or_insert(0);
            if n.key().0 == CTSS && *i < 2 {
                s.value = if *i == 0 {
                    field(job, "title")?
                } else {
                    field(job, "description")?
                }
                .to_string()
                .into();
            } else if n.key().0 == STR && [0x85, 0x88].contains(&n.key().2) {
                s.value = renamed_typed(
                    names,
                    if n.key().2 == 0x85 { CRES } else { TXMT },
                    &s.value.to_string(),
                )
                .into();
            }
            *i += 1;
        }
    } else {
        bail!("Object text lists require tagged language entries")
    }
    n.set(&t)
}
pub fn build(job: &Value) -> Result<Value> {
    let prepared = prepare(job)?;
    ensure!(
        job["identities"].is_null() || prepared["identities"] == job["identities"],
        "Saved object identities are inconsistent"
    );
    let job = &prepared;
    let (container, source) = resources::load_raw(Path::new("object-template"))?;
    ensure!(
        hash(assets::read("object-template")?) == job["source_sha256"],
        "Object source changed"
    );
    let prefix = field(&job["identities"], "prefix")?;
    let group = u32::from_str_radix(field(&job["identities"], "group")?, 16)?;
    let guids = job["identities"]["guids"]
        .as_object()
        .unwrap()
        .iter()
        .map(|(k, v)| {
            Ok((
                u32::from_str_radix(k, 16)?,
                u32::from_str_radix(v.as_str().unwrap(), 16)?,
            ))
        })
        .collect::<Result<BTreeMap<_, _>>>()?;
    let mut names = BTreeMap::new();
    let mut ambiguous_names = BTreeSet::new();
    let mut keys = BTreeMap::new();
    let mut own = BTreeMap::new();
    for n in &source {
        let k = n.key();
        let target = if named(k.0) {
            let old = metadata(n)?;
            let suffix = old.rsplit('_').next().context("Missing resource suffix")?;
            let new = format!("{prefix}-{:08x}-{:016x}_{suffix}", k.0, k.2);
            for (a, b) in [
                (old.clone(), new.clone()),
                (
                    old.rsplit_once('_').unwrap().0.into(),
                    new.rsplit_once('_').unwrap().0.into(),
                ),
            ] {
                let qualified = format!("##0x{SCENE_GROUP:08x}!{b}");
                for alias in [
                    a.to_ascii_lowercase(),
                    format!("##0x{:08x}!{}", k.1, a.to_ascii_lowercase()),
                ] {
                    names.insert(format!("{:08x}:{alias}", k.0), qualified.clone());
                    if names.get(&alias).is_some_and(|v| v != &qualified) {
                        ambiguous_names.insert(alias.clone());
                    }
                    names.insert(alias, qualified.clone());
                }
            }
            own.insert(k, new.clone());
            (k.0, SCENE_GROUP, resources::named_id(&new))
        } else {
            (k.0, group, k.2)
        };
        ensure!(
            !keys.values().any(|v| *v == target),
            "New resource identities collide"
        );
        keys.insert(k, target);
    }
    for alias in ambiguous_names {
        names.remove(&alias);
    }
    let mut nodes = source.clone();
    for n in &mut nodes {
        let old = n.key();
        match old.0 {
            OBJD => {
                let mut o = obj(n)?;
                o.guid = guids[&o.guid];
                for g in [
                    &mut o.diagonal_selector_guid,
                    &mut o.grid_aligned_selector_guid,
                    &mut o.proxy_guid,
                    &mut o.job_object_guid,
                    &mut o.original_guid,
                    &mut o.type_attribute_guid,
                ] {
                    if let Some(v) = guids.get(g) {
                        *g = *v;
                    }
                }
                if o.multi_tile_master_id == 0 || o.multi_tile_sub_index == 0xffff {
                    o.price = job["price"].as_u64().unwrap() as u16;
                }
                n.set(&o)?;
            }
            CRES | SHPE | GMND | LGHT => {
                let s = scene::read(&n.bytes)?;
                let mut local = names.clone();
                local.insert(s.name.to_ascii_lowercase(), own[&old].clone());
                n.bytes = scene::rewrite(&n.bytes, &s, &local, &keys)?;
            }
            GMDC | TXMT | TXTR => {
                let mut c = n.collection()?;
                for link in &mut c.links {
                    let k = (
                        link.type_id.code(),
                        link.group_id,
                        (link.resource_id as u64) << 32 | link.instance_id as u64,
                    );
                    if let Some(new) = keys.get(&k) {
                        link.group_id = new.1;
                        link.instance_id = new.2 as u32;
                        link.resource_id = (new.2 >> 32) as u32;
                    }
                }
                for e in &mut c.entries {
                    match &mut e.data {
                        ResourceData::Mesh(m) => m.file_name.name = own[&old].clone().into(),
                        ResourceData::Texture(t) => t.file_name.name = own[&old].clone().into(),
                        ResourceData::Material(m) => {
                            m.file_name.name = own[&old].clone().into();
                            m.material_description =
                                renamed_typed(&names, TXMT, &m.material_description.to_string())
                                    .into();
                            for p in &mut m.properties {
                                if p.name.to_string().ends_with("TextureName") {
                                    p.value =
                                        renamed_typed(&names, TXTR, &p.value.to_string()).into();
                                }
                            }
                            for f in &mut m.names {
                                *f = renamed_typed(&names, TXTR, &f.to_string()).into();
                            }
                        }
                    }
                }
                n.set(&c)?;
            }
            MMAT => {
                let mut c = n.cpf()?;
                let g = resources::number(&c, "objectGUID")?;
                resources::uint(
                    &mut c,
                    "objectGUID",
                    *guids
                        .get(&g)
                        .context("Material override belongs to another object")?,
                );
                for key in ["name", "modelName"] {
                    let v = renamed_typed(
                        &names,
                        if key == "name" { TXMT } else { CRES },
                        &resources::string(&c, key)?,
                    );
                    resources::text_prop(&mut c, key, &v);
                }
                let family = resources::string(&c, "family")?;
                let h = hash(format!("{prefix}:{family}"));
                let family = format!(
                    "{}-{}-{}-{}-{}",
                    &h[..8],
                    &h[8..12],
                    &h[12..16],
                    &h[16..20],
                    &h[20..32]
                );
                resources::text_prop(&mut c, "family", &family);
                n.set(&c)?;
            }
            CTSS => edit_text(n, &names, job)?,
            STR if [0x85, 0x88].contains(&n.key().2) && !unused_material_table(n)? => {
                edit_text(n, &names, job)?
            }
            _ => {}
        }
        let new = keys[&old];
        n.entry.group_id = new.1;
        n.entry.instance_id = InstanceId { id: new.2 };
    }
    if job["mode"] == "model" {
        crate::object_model::replace(&mut nodes, job)?;
    }
    resources::write_output(
        container,
        &mut nodes,
        Path::new("output"),
        crate::package_compression::from_job(job)?,
    )?;
    let (_, reopened) = resources::load_raw(Path::new("output"))?;
    ensure!(
        reopened.len() == nodes.len(),
        "Reopened resource count differs"
    );
    for n in &nodes {
        let r = reopened
            .iter()
            .find(|r| r.key() == n.key())
            .context("Output resource is missing")?;
        ensure!(r.bytes == n.bytes, "Output resource failed byte validation");
    }
    let c = catalog()?;
    let p = profile(&reopened, &c)?;
    let g = game()?;
    let mut report = info(&reopened, p, &g)?;
    let old_guids = source
        .iter()
        .filter(|n| n.key().0 == OBJD)
        .map(|n| obj(n).map(|o| o.guid))
        .collect::<Result<BTreeSet<_>>>()?;
    ensure!(
        reopened
            .iter()
            .filter(|n| n.key().0 == OBJD)
            .all(|n| !old_guids.contains(&obj(n).unwrap().guid)),
        "A source GUID survived cloning"
    );
    report["sha256"] = json!(hash(assets::read("output")?));
    report["status"] = json!("passed");
    report["package_compression"] =
        crate::package_compression::report("output", crate::package_compression::from_job(job)?)?;
    report["texture_encoding"] =
        crate::texture_encoding::Encoder::from_job(job)?.usage(if job["mode"] == "model" {
            &[dbpf::internal_file::resource_collection::texture_resource::TextureFormat::DXT3]
        } else {
            &[]
        });
    if !job["target_height"].is_null() || job["fit_to_template"] == true {
        report["layout"] = job["layout"].clone();
    }
    report["gameplay"] = json!("not_tested");
    report["resource_keys"] = json!(reopened
        .iter()
        .map(|n| resources::key_text(n.key()))
        .collect::<Vec<_>>());
    Ok(report)
}
pub fn prepare_profile(path: &str) -> Result<Value> {
    let (_, mut nodes) = resources::load_raw(Path::new(path))?;
    resources::embed_game_mips(&mut nodes)?;
    for n in nodes
        .iter()
        .filter(|n| [CRES, SHPE, GMND, LGHT].contains(&n.key().0))
    {
        scene::read(&n.bytes).with_context(|| resources::key_text(n.key()).to_string())?;
    }
    Ok(
        json!({"behavior":nodes.iter().filter(|n|n.key().0==BHAV).map(|n|format!("{:x}:{}",n.key().2,hash(&n.bytes))).collect::<Vec<_>>(),"globals":nodes.iter().filter(|n|n.key().0==GLOB).map(|n|hash(&n.bytes)).collect::<Vec<_>>(),"object_count":nodes.iter().filter(|n|n.key().0==OBJD).count(), "behavior_resources":behavior_signature(&nodes)?}),
    )
}
fn default_shapes(nodes: &[Node]) -> Result<BTreeSet<Key>> {
    let names = aliases(nodes)?;
    let models = nodes
        .iter()
        .find(|n| n.key().0 == STR && n.key().2 == 0x85)
        .context("Object model list is missing")?;
    let model = texts(models)?
        .into_iter()
        .find(|s| !s.is_empty())
        .context("Object default model is missing")?;
    let key = names
        .get(&format!("{CRES:08x}:{}", model.to_ascii_lowercase()))
        .context("The default model is not embedded. Include its matching appearance package")?;
    let root = nodes.iter().find(|n| n.key() == *key).unwrap();
    Ok(scene::read(&root.bytes)?
        .links
        .into_iter()
        .map(|(_, k)| k)
        .filter(|k| k.0 == SHPE)
        .collect())
}
pub(crate) fn preview_meshes(nodes: &[Node]) -> Result<BTreeSet<Key>> {
    let names = aliases(nodes)?;
    let mut keys = BTreeSet::new();
    let shapes = default_shapes(nodes)?;
    for shape in nodes.iter().filter(|n| shapes.contains(&n.key())) {
        let s = scene::read(&shape.bytes)?;
        let level = s.geometry_lods.iter().map(|(lod, _)| *lod).min();
        for (lod, name) in s.geometry_lods {
            if Some(lod) != level {
                continue;
            }
            if let Some(k) = names.get(&format!("{GMND:08x}:{}", name.to_ascii_lowercase())) {
                let n = nodes
                    .iter()
                    .find(|n| n.key() == *k)
                    .context("Missing preview geometry")?;
                for (_, k) in scene::read(&n.bytes)?.links {
                    if k.0 == GMDC {
                        keys.insert(k);
                    }
                }
            }
        }
        for (_, k) in s.links {
            if k.0 == GMND {
                if let Some(n) = nodes.iter().find(|n| n.key() == k) {
                    for (_, k) in scene::read(&n.bytes)?.links {
                        if k.0 == GMDC {
                            keys.insert(k);
                        }
                    }
                }
            }
        }
    }
    ensure!(
        !keys.is_empty(),
        "This object has no supported embedded mesh route for preview or model replacement"
    );
    Ok(keys)
}
pub(crate) fn render(nodes: &[Node]) -> Result<Value> {
    use base64::{engine::general_purpose::STANDARD, Engine as _};
    let names = aliases(nodes)?;
    let mut materials = BTreeMap::new();
    let mut meshes = Vec::new();
    let shapes = default_shapes(nodes)?;
    for n in nodes.iter().filter(|n| shapes.contains(&n.key())) {
        for (subset, name) in scene::read(&n.bytes)?.parts {
            let Some(key) = names.get(&format!("{TXMT:08x}:{}", name.to_ascii_lowercase())) else {
                continue;
            };
            let material = nodes
                .iter()
                .find(|n| n.key() == *key)
                .unwrap()
                .collection()?;
            let ResourceData::Material(m) = &material.entries[0].data else {
                continue;
            };
            let props = m
                .properties
                .iter()
                .map(|p| (p.name.to_string(), p.value.to_string()))
                .collect::<BTreeMap<_, _>>();
            let image = if let Some(name) = props.get("stdMatBaseTextureName") {
                if let Some(key) = names.get(&format!("{TXTR:08x}:{}", name.to_ascii_lowercase())) {
                    let c = nodes
                        .iter()
                        .find(|n| n.key() == *key)
                        .unwrap()
                        .collection()?;
                    if let ResourceData::Texture(t) = &c.entries[0].data {
                        let d = t.decompress(0, t.mip_levels() - 1)?;
                        let image = image::RgbaImage::from_raw(t.width, t.height, d.data)
                            .context("Invalid decoded object texture")?;
                        let mut b = Cursor::new(Vec::new());
                        image.write_to(&mut b, image::ImageFormat::Png)?;
                        Some(format!(
                            "data:image/png;base64,{}",
                            STANDARD.encode(b.into_inner())
                        ))
                    } else {
                        None
                    }
                } else {
                    None
                }
            } else {
                None
            };
            let diffuse = props
                .get("stdMatDiffCoef")
                .map(|s| {
                    s.split(',')
                        .map(|n| n.trim().parse::<f32>())
                        .collect::<std::result::Result<Vec<_>, _>>()
                })
                .transpose()?
                .unwrap_or_else(|| vec![1.; 3]);
            ensure!(
                diffuse.len() == 3 && diffuse.iter().all(|v| v.is_finite()),
                "Invalid material diffuse color"
            );
            materials.insert(subset,json!({"image":image,"diffuse":diffuse,"blend":props.get("stdMatAlphaBlendMode"),"alpha_test":props.get("stdMatAlphaTestEnabled").is_some_and(|v|v=="1"||v=="true"),"double_sided":props.get("stdMatCullMode").is_some_and(|v|v=="none")}));
        }
    }
    let visible = preview_meshes(nodes)?;
    for n in nodes.iter().filter(|n| visible.contains(&n.key())) {
        let c = n.collection()?;
        let ResourceData::Mesh(m) = &c.entries[0].data else {
            continue;
        };
        let name = m.file_name.name.to_string();
        let b = m
            .export_gltf()
            .context("Could not export this mesh for preview")?;
        meshes.push(json!({"name":name,"glb":STANDARD.encode(b.0)}));
    }
    Ok(json!({"meshes":meshes,"materials":materials}))
}
// Offline template extraction starts from private resource references, then follows
// the actual local scene graph. Candidate filename prefixes are not identities.
fn extract(path: &str) -> Result<Value> {
    let (container, mut nodes) = resources::load_raw(Path::new(path))?;
    resources::embed_game_mips(&mut nodes)?;
    let names = aliases(&nodes)?;
    let mut keep = nodes
        .iter()
        .filter(|n| !named(n.key().0))
        .map(Node::key)
        .collect::<BTreeSet<_>>();
    loop {
        let before = keep.len();
        let mut refs = Vec::new();
        let mut keys = Vec::new();
        for n in nodes.iter().filter(|n| keep.contains(&n.key())) {
            match n.key().0 {
                STR if (n.key().2 == 0x85 || n.key().2 == 0x88) && !unused_material_table(n)? => {
                    for t in texts(n)? {
                        refs.push((if n.key().2 == 0x85 { CRES } else { TXMT }, t));
                    }
                }
                MMAT => {
                    let c = n.cpf()?;
                    refs.push((CRES, resources::string(&c, "modelName")?));
                    refs.push((TXMT, resources::string(&c, "name")?));
                }
                CRES | SHPE | GMND | LGHT => {
                    let s = scene::read(&n.bytes)?;
                    keys.extend(s.links.into_iter().map(|(_, k)| k));
                    refs.extend(
                        s.strings
                            .into_iter()
                            .filter_map(|t| t.resource_kind.map(|k| (k, t.value))),
                    );
                }
                TXMT => {
                    let c = n.collection()?;
                    if let ResourceData::Material(m) = &c.entries[0].data {
                        refs.extend(
                            m.properties
                                .iter()
                                .filter(|p| p.name.to_string().ends_with("TextureName"))
                                .map(|p| (TXTR, p.value.to_string())),
                        );
                        refs.extend(m.names.iter().map(|n| (TXTR, n.to_string())));
                    }
                }
                _ => {}
            }
        }
        for (kind, name) in refs {
            if let Some(k) = names.get(&format!("{kind:08x}:{}", name.to_ascii_lowercase())) {
                keys.push(*k);
            }
        }
        for k in keys {
            if nodes.iter().any(|n| n.key() == k) {
                keep.insert(k);
            }
        }
        if keep.len() == before {
            break;
        }
    }
    nodes.retain(|n| keep.contains(&n.key()));
    let result = json!({"resource_keys":nodes.iter().map(|n|resources::key_text(n.key())).collect::<Vec<_>>(),
        "scenes":nodes.iter().filter(|n| n.key().0==CRES).map(|n|{let s=scene::read(&n.bytes).unwrap();json!({"name":s.name,"transforms":s.transforms,"links":s.links.iter().map(|(_,k)|resources::key_text(*k)).collect::<Vec<_>>()})}).collect::<Vec<_>>()});
    resources::write(container, &mut nodes, Path::new("extracted"))?;
    Ok(result)
}
pub fn dispatch(op: &str, p: &Value) -> Result<Value> {
    match op {
        "object_extract" => extract(field(p, "asset")?),
        "object_inspect" => inspect(
            &p["files"]
                .as_array()
                .context("Choose object packages")?
                .iter()
                .map(|v| Ok(v.as_str().context("Invalid file name")?.into()))
                .collect::<Result<Vec<_>>>()?,
            p["trusted"] == true,
        ),
        "object_layout" => crate::object_model::layout(&p["job"]),
        "object_prepare" => prepare(&p["job"]),
        "object_build" => build(&p["job"]),
        "object_profile" => prepare_profile(field(p, "asset")?),
        "object_preview" => {
            let (_, source) = resources::load_raw(Path::new("object-template"))?;
            let original = render(&source)?;
            let mut preview_job = p["job"].clone();
            let layout = if !preview_job["target_height"].is_null()
                || preview_job["fit_to_template"] == true
            {
                let v = crate::object_model::layout(&preview_job)?;
                preview_job["placement_ack"] = v["signature"].clone();
                Some(v)
            } else {
                None
            };
            let report = build(&preview_job)?;
            let (_, output) = resources::load_raw(Path::new("output"))?;
            Ok(
                json!({"original":original,"converted":render(&output)?,"report":report,"layout":layout}),
            )
        }
        "object_readme" => Ok(
            json!({"text":format!("{}\nCreated by {}\n\nINSTALLATION\nExtract the package into Documents/EA Games/The Sims 2/Downloads (or your Legacy Collection Downloads folder). Enable custom content and restart the game.\n\nREQUIREMENTS\n{}\n\nCLONING\nEvery object definition has a new GUID. Private behavior and shared game behavior references are preserved. Multi-tile definitions and material-state families stay linked. The original custom object is not required when all its appearance resources were supplied.\n\nCREDITS\nOriginal game assets: Maxis / Electronic Arts. Uploaded models and custom objects remain credited to their original creators.\n{}\n\nVALIDATION\nPackages are reopened and structurally checked in this browser. Gameplay has not been validated. Test in an isolated game profile first.\n",field(&p["job"],"title")?,field(&p["job"],"creator")?,field(&p["job"],"requirements")?,p["job"]["credits"].as_str().unwrap_or(""))}),
        ),
        _ => bail!("Unknown object engine operation"),
    }
}
