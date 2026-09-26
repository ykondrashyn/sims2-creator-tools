//! SavedSims research and browser fitting. Gameplay acceptance is a release gate.
use crate::{assets, core::resources, sim_fit, sim_package};
use anyhow::{bail, ensure, Context, Result};
use binrw::BinRead;
use dbpf::internal_file::sim_outfits::SimOutfits;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{collections::BTreeSet, io::Cursor, path::Path};

pub const APPEARANCE: u32 = 0xac598eac;
pub const IDR: u32 = 0xac506764;
pub const GAMEPLAY_ACCEPTED: bool = false;
pub fn digest(bytes: impl AsRef<[u8]>) -> String {
    format!("{:x}", Sha256::digest(bytes.as_ref()))
}
pub fn field<'a>(v: &'a Value, name: &str) -> Result<&'a str> {
    v[name]
        .as_str()
        .with_context(|| format!("Missing Sim {name}"))
}
pub fn release_gate() -> Result<()> {
    ensure!(GAMEPLAY_ACCEPTED, "Sim package downloads are experimental and disabled until single-package loading and persistent-head gameplay checks pass. Your model and fitting settings are saved.");
    Ok(())
}

pub fn inspect(path: &str) -> Result<Value> {
    let (_, nodes) = resources::load_raw(Path::new(path))?;
    let appearances: Vec<_> = nodes.iter().filter(|n| n.key().0 == APPEARANCE).collect();
    ensure!(
        appearances.len() == 1,
        "Choose one Body Shop saved-Sim package with exactly one appearance record"
    );
    // A positive allowlist avoids accidentally treating neighborhood data as a scaffold.
    let supported = [
        APPEARANCE, IDR, 0xebcf3e27, 0xcccef852, 0x49596978, 0xfc6eb1f7, 0x7ba3838c, 0xac4f8687,
        0xe519c933, 0x0c560f39,
    ];
    ensure!(nodes.iter().all(|n| supported.contains(&n.key().0)), "Saved-Sim scaffold contains unsupported or neighborhood resources. Create a minimal Sim in Body Shop");
    let p = appearances[0].cpf()?;
    let age = resources::number(&p, "age")?;
    let gender = resources::number(&p, "gender")?;
    ensure!(
        age == 8 && [1, 2].contains(&gender),
        "Only Adult Male and Adult Female scaffolds are supported"
    );
    let keys: BTreeSet<_> = nodes.iter().map(|n| n.key()).collect();
    let mut external = BTreeSet::new();
    let mut local = 0;
    for node in nodes.iter().filter(|n| n.key().0 == IDR) {
        let idr = SimOutfits::read_le(&mut Cursor::new(&node.bytes))?;
        for link in &idr.entries {
            let k = (link.type_id.code(), link.group_id, link.instance_id.id);
            if k == (0, 0, 0) {
                continue;
            }
            if keys.contains(&k) {
                local += 1;
            } else {
                external.insert(resources::key_text(k));
            }
        }
    }
    Ok(
        json!({"version":1,"body":if gender==2 {"am"} else {"af"},"age":age,
        "resources":nodes.len(),"sha256":digest(assets::read(path)?),"local_links":local,
        "external_links":external,"external_dependencies_verified":false,"gameplay":"not_tested",
        "resource_keys":keys.into_iter().map(resources::key_text).collect::<Vec<_>>()}),
    )
}

/// Offline evidence for reference components, never a production scaffold permit.
pub fn inspect_fixture(path: &str) -> Result<Value> {
    use dbpf::internal_file::cpf::Data;
    let (_, nodes) = resources::load_raw(Path::new(path))?;
    let mut resources = vec![];
    for n in &nodes {
        let mut item = json!({"key":resources::key_text(n.key()),"bytes":n.bytes.len(),"sha256":digest(&n.bytes)});
        if [
            APPEARANCE, 0xebcf3e27, 0x0c560f39, 0x6c4f359d, 0x4c158081, 0x2c1fd8a1,
        ]
        .contains(&n.key().0)
        {
            let p = n.cpf()?;
            item["properties"] = json!(p
                .entries
                .iter()
                .map(|e| {
                    let v = match &e.data {
                        Data::UInt(v) => json!(v),
                        Data::Int(v) => json!(v),
                        Data::String(v) => json!(String::from_utf8_lossy(v)),
                        Data::Float(v) => json!(v),
                        Data::Bool(v) => json!(v),
                    };
                    (String::from_utf8_lossy(&e.name).into_owned(), v)
                })
                .collect::<serde_json::Map<_, _>>());
        } else if n.key().0 == IDR {
            let idr = SimOutfits::read_le(&mut Cursor::new(&n.bytes))?;
            item["links"] = json!(idr
                .entries
                .iter()
                .map(|e| resources::key_text((e.type_id.code(), e.group_id, e.instance_id.id)))
                .collect::<Vec<_>>());
        } else if [0xe519c933, 0xfc6eb1f7, 0x7ba3838c].contains(&n.key().0) {
            match crate::object_scene::read(&n.bytes) {
                Ok(s) => {
                    item["name"] = json!(s.name);
                    item["links"] = json!(s
                        .links
                        .iter()
                        .map(|(_, k)| resources::key_text(*k))
                        .collect::<Vec<_>>());
                    item["parts"] = json!(s.parts);
                    item["geometry"] = json!(s.geometry);
                }
                Err(e) => {
                    item["inspection_error"] = json!(e.to_string());
                }
            }
        }
        resources.push(item);
    }
    Ok(
        json!({"version":1,"sha256":digest(assets::read(path)?),"resources":resources,"gameplay":"not_tested"}),
    )
}

/// Development-only Body Shop merge experiment. This is not a release builder.
#[cfg(not(target_arch = "wasm32"))]
pub fn merge_outfit_prototype(p: &Value) -> Result<Value> {
    use dbpf::internal_file::resource_collection::{texture_resource::TextureFormat, ResourceData};
    let scaffold = field(p, "scaffold")?;
    inspect(scaffold)?;
    let (container, mut nodes) = resources::load_raw(Path::new(scaffold))?;
    let (_, mut outfit) = resources::load_raw(Path::new(field(p, "outfit")?))?;
    ensure!(
        outfit.iter().all(
            |n| [0x0c560f39, IDR, 0x53545223, 0xebcf3e27, 0x49596978, 0x1c4a276c]
                .contains(&n.key().0)
        ),
        "Prototype expects one exported Body Shop outfit"
    );
    let original_keys: BTreeSet<_> = nodes.iter().map(|n| n.key()).collect();
    ensure!(
        outfit.iter().all(|n| !original_keys.contains(&n.key())),
        "Prototype resources collide"
    );
    let mut textures = 0;
    for n in &mut outfit {
        if n.key().0 != 0x1c4a276c {
            continue;
        }
        let mut c = n.collection()?;
        let ResourceData::Texture(t) = &mut c.entries[0].data else {
            bail!("Invalid outfit texture")
        };
        let mut d = t.decompress(0, t.mip_levels() - 1)?;
        // Unmistakable diagnostic green distinguishes a loaded custom outfit
        // from the stock garment that Body Shop could otherwise substitute.
        for pixel in d.data.chunks_exact_mut(4) {
            pixel[0] = 32;
            pixel[1] = 220;
            pixel[2] = 48;
        }
        let format = t.get_format();
        t.compress_replace(d, Some(TextureFormat::RawARGB32));
        t.add_max_mip_levels(None);
        crate::texture_encoding::recompress(
            t,
            format,
            crate::texture_encoding::Encoder::from_job(p)?,
        )?;
        n.set(&c)?;
        textures += 1;
    }
    ensure!(
        textures == 1,
        "Prototype expects exactly one embedded outfit texture"
    );
    nodes.extend(outfit);
    let mesh_embedded = if let Some(path) = p["mesh"].as_str() {
        use crate::object_scene as scene;
        use dbpf::header_v1::InstanceId;
        use std::collections::BTreeMap;
        let (_, mut mesh) = resources::load_raw(Path::new(path))?;
        ensure!(
            mesh.iter()
                .all(|n| [0xe519c933, 0xfc6eb1f7, 0x7ba3838c, 0xac4f8687].contains(&n.key().0)),
            "Prototype mesh must contain only scene and geometry resources"
        );
        let (mut keys, mut names, mut own) = (BTreeMap::new(), BTreeMap::new(), BTreeMap::new());
        for n in &mesh {
            let old = n.key();
            let name = if old.0 == 0xac4f8687 {
                let c = n.collection()?;
                let ResourceData::Mesh(m) = &c.entries[0].data else {
                    bail!("Invalid prototype mesh")
                };
                m.file_name.name.to_string()
            } else {
                scene::read(&n.bytes)?.name
            };
            let suffix = name
                .rsplit('_')
                .next()
                .context("Missing prototype suffix")?;
            let new = format!("simproof-{:08x}-{:016x}_{suffix}", old.0, old.2);
            keys.insert(old, (old.0, 0x1c050000, resources::named_id(&new)));
            names.insert(name.to_ascii_lowercase(), format!("##0x1c050000!{new}"));
            names.insert(
                format!("##0x{:08x}!{}", old.1, name.to_ascii_lowercase()),
                format!("##0x1c050000!{new}"),
            );
            own.insert(old, new);
        }
        for n in &mut mesh {
            let old = n.key();
            if old.0 == 0xac4f8687 {
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
                let ResourceData::Mesh(m) = &mut c.entries[0].data else {
                    bail!("Invalid prototype mesh")
                };
                m.file_name.name = own[&old].clone().into();
                n.set(&c)?;
            } else {
                let s = scene::read(&n.bytes)?;
                let mut local = names.clone();
                local.insert(s.name.to_ascii_lowercase(), own[&old].clone());
                n.bytes = scene::rewrite(&n.bytes, &s, &local, &keys)?;
            }
            let new = keys[&old];
            n.entry.group_id = new.1;
            n.entry.instance_id = InstanceId { id: new.2 };
        }
        let mut replaced = 0;
        for n in &mut nodes {
            if n.key().0 != IDR {
                continue;
            }
            let mut idr = SimOutfits::read_le(&mut Cursor::new(&n.bytes))?;
            for link in &mut idr.entries {
                if let Some(new) =
                    keys.get(&(link.type_id.code(), link.group_id, link.instance_id.id))
                {
                    link.group_id = new.1;
                    link.instance_id = InstanceId { id: new.2 };
                    replaced += 1;
                }
            }
            n.set(&idr)?;
        }
        ensure!(
            replaced == 2,
            "Prototype must relink its actual outfit CRES and SHPE"
        );
        ensure!(
            mesh.iter()
                .all(|n| !nodes.iter().any(|x| x.key() == n.key())),
            "Prototype scene identities collide"
        );
        nodes.extend(mesh);
        true
    } else {
        false
    };
    let output = field(p, "output")?;
    resources::write_output(
        container,
        &mut nodes,
        Path::new(output),
        crate::package_compression::from_job(p)?,
    )?;
    let (_, check) = resources::load_raw(Path::new(output))?;
    ensure!(
        check.len() == nodes.len()
            && check.iter().all(|n| nodes
                .iter()
                .any(|old| old.key() == n.key() && old.bytes == n.bytes)),
        "Prototype failed resource round trip"
    );
    Ok(
        json!({"experimental":true,"resources":check.len(),"sha256":digest(assets::read(output)?),"gameplay":"not_tested","mesh_embedded":mesh_embedded,"scope":"SavedSim with embedded diagnostic Everyday recolor and optional independently identified mesh, no replacement head"}),
    )
}

pub fn dispatch(op: &str, p: &Value) -> Result<Value> {
    match op {
        "sim_align" => crate::sim_guided::align(p),
        "sim_landmarks" => crate::sim_guided::validate(p),
        "sim_guided_fit" => crate::sim_guided::fit(p),
        "sim_inspect_scaffold" => inspect(field(p, "asset")?),
        "sim_reference" => sim_fit::reference(field(p, "asset")?),
        "sim_inspect_model" => sim_fit::inspect(field(p, "model")?),
        "sim_fit" | "sim_preview" => sim_fit::fit(p),
        "sim_prepare" => sim_package::prepare(p),
        "sim_experimental_build" => crate::sim_experimental::build(p.get("job").unwrap_or(p)),
        "sim_build" => {
            release_gate()?;
            bail!("This engine has no accepted Sim construction profile")
        }
        "sim_validate" => {
            release_gate()?;
            inspect(field(p, "asset")?)
        }
        _ => bail!("Unknown Sim operation {op}"),
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn public_download_gate_is_closed() {
        assert!(super::release_gate()
            .unwrap_err()
            .to_string()
            .contains("persistent-head"));
    }
}
