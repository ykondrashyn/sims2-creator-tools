//! Browser protocol v1. Inputs and outputs are asset buffers, never host files.
use crate::{
    assets::{self, Assets},
    color, hair, tattoo,
};
use anyhow::{bail, ensure, Context, Result};
use base64::{engine::general_purpose::STANDARD, Engine as _};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::Path,
};
#[cfg(target_arch = "wasm32")]
use wasm_bindgen::prelude::*;
fn hash(data: impl AsRef<[u8]>) -> String {
    format!("{:x}", Sha256::digest(data.as_ref()))
}
fn strings(v: &Value) -> Result<Vec<String>> {
    v.as_array()
        .context("Expected a list")?
        .iter()
        .map(|v| Ok(v.as_str().context("Expected text")?.into()))
        .collect()
}
fn field<'a>(v: &'a Value, k: &str) -> Result<&'a str> {
    v[k].as_str().with_context(|| format!("Missing {k}"))
}
fn uuid(seed: &str) -> String {
    let h = hash(seed);
    format!(
        "{}-{}-4{}-a{}-{}",
        &h[..8],
        &h[8..12],
        &h[13..16],
        &h[17..20],
        &h[20..32]
    )
}
fn group(seed: &str) -> u32 {
    0x50000000 | (u32::from_str_radix(&hash(seed)[..8], 16).unwrap() & 0x0fffffff)
}
fn uri(image: &image::RgbaImage) -> Result<String> {
    Ok(format!(
        "data:image/png;base64,{}",
        STANDARD.encode(color::thumb(image)?)
    ))
}
fn name(v: &Value) -> Result<&str> {
    let n = v.as_str().context("Enter a name")?;
    ensure!(
        !n.is_empty()
            && n.len() <= 48
            && n.as_bytes()[0].is_ascii_alphanumeric()
            && n.bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-'),
        "Creator and Hair name require 1 to 48 letters, numbers, underscores or hyphens"
    );
    Ok(n)
}
#[cfg_attr(target_arch = "wasm32", wasm_bindgen)]
#[derive(Default)]
pub struct BrowserEngine {
    assets: Assets,
    palette: Value,
    game: Value,
    conversion: Option<crate::conversion::Conversion>,
    upscale: crate::upscale::Session,
}
#[cfg_attr(target_arch = "wasm32", wasm_bindgen)]
impl BrowserEngine {
    #[cfg_attr(target_arch = "wasm32", wasm_bindgen(constructor))]
    pub fn new() -> Self {
        Self::default()
    }
    pub fn put_asset(&mut self, name: &str, bytes: Vec<u8>) -> std::result::Result<String, String> {
        if bytes.len() > 128 * 1024 * 1024 || name.len() > 256 {
            return Err("Asset exceeds limits".into());
        }
        self.assets.bytes.insert(name.into(), bytes);
        Ok(self
            .assets
            .bytes
            .digest(Path::new(name))
            .unwrap()
            .to_owned())
    }
    pub fn take_asset(&mut self, name: &str) -> Vec<u8> {
        self.assets
            .bytes
            .remove(Path::new(name))
            .unwrap_or_default()
    }
    pub fn drop_asset(&mut self, name: &str) {
        self.assets.bytes.remove(Path::new(name));
    }
    pub fn sha256(&self, bytes: &[u8]) -> String {
        hash(bytes)
    }
    pub fn call(&mut self, request: &str) -> std::result::Result<String, String> {
        if request.len() > 256 * 1024 {
            return Err("Request exceeds 256 KiB".into());
        }
        let result = (|| -> Result<Value> {
            let r: Value = serde_json::from_str(request)?;
            // Validate the envelope without reserializing parameters. This keeps
            // stored floating point snapshots and unknown fields unchanged.
            let _: crate::contracts::EngineRequest = serde_json::from_value(r.clone())?;
            ensure!(r["version"] == 1, "Unsupported worker protocol version");
            let op = field(&r, "op")?;
            let p = &r["params"];
            if op == "capabilities" {
                return Ok(crate::contracts::capabilities());
            }
            match op {
                "upscale_prepare_input" | "upscale_inspect_output" => {
                    let bytes = self
                        .assets
                        .bytes
                        .remove(Path::new(field(p, "input")?))
                        .context("The image buffer is missing")?;
                    let (mut info, output) = if op == "upscale_prepare_input" {
                        self.upscale
                            .input(bytes, p["upload"].as_bool().unwrap_or(true))?
                    } else {
                        self.upscale
                            .output(bytes, p["preserve_alpha"].as_bool().unwrap_or(true))?
                    };
                    info["output_bytes"] = json!(output.len());
                    self.assets.bytes.insert("output".into(), output);
                    return Ok(info);
                }
                "upscale_png" => {
                    let output = self.upscale.png()?;
                    let size = output.len();
                    self.assets.bytes.insert("output".into(), output);
                    return Ok(json!({"version":1,"output_bytes":size}));
                }
                "conversion_validate" => {
                    return crate::conversion::validate(
                        self.assets
                            .bytes
                            .get(Path::new(field(p, "input")?))
                            .context("Conversion PNG is missing")?,
                    )
                }
                "conversion_begin" => {
                    self.conversion = None;
                    let input = self
                        .assets
                        .bytes
                        .remove(Path::new(field(p, "input")?))
                        .context("Conversion PNG is missing")?;
                    let mapping = self
                        .assets
                        .bytes
                        .remove(Path::new(field(p, "mapping")?))
                        .context("Conversion mapping is missing")?;
                    self.conversion = Some(crate::conversion::Conversion::new(
                        &mapping,
                        &input,
                        field(p, "body")?,
                    )?);
                    return Ok(json!({"version":1,"total":1048576}));
                }
                "conversion_step" => {
                    return self
                        .conversion
                        .as_mut()
                        .context("Initialize conversion before processing")?
                        .step(p["pixels"].as_u64().unwrap_or(4096).try_into()?)
                }
                "conversion_finish" => {
                    let state = self.conversion.take().context("No conversion is running")?;
                    let (png, report) = state.finish()?;
                    self.assets.bytes.insert("output".into(), png);
                    return Ok(report);
                }
                "reset_inputs" => {
                    self.conversion = None;
                    self.upscale = crate::upscale::Session::default();
                }
                _ => {}
            }
            if op == "init" {
                self.palette = serde_json::from_slice(
                    self.assets
                        .bytes
                        .get(Path::new("palette"))
                        .context("Palette asset is missing")?,
                )?;
                self.game = if let Some(b) = self.assets.bytes.get(Path::new("game-meshes")) {
                    serde_json::from_slice(b)?
                } else {
                    json!({"resources":{}})
                };
                return Ok(json!({"version":1}));
            }
            assets::with(&mut self.assets, || {
                dispatch(op, p, &self.palette, &self.game)
            })
        })();
        result
            .and_then(|v| Ok(serde_json::to_string(&v)?))
            .map_err(|e| format!("{e:#}"))
    }
}
fn palette(job: &Value, builtin: &Value) -> Result<Vec<Value>> {
    let mut colors = builtin["palette"]
        .as_array()
        .context("Palette missing")?
        .clone();
    let own = job
        .get("custom_colors")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    ensure!(own.len() <= 16, "Use at most 16 custom colors");
    let mut names: BTreeSet<_> = colors
        .iter()
        .map(|c| {
            c["name"]
                .as_str()
                .unwrap()
                .replace(' ', "")
                .to_ascii_lowercase()
        })
        .collect();
    let mut ids = BTreeSet::new();
    for mut c in own {
        let id = field(&c, "id")?;
        ensure!(
            id.len() == 39
                && id.starts_with("custom:")
                && id[7..].bytes().all(|b| b.is_ascii_hexdigit())
                && ids.insert(id.to_string()),
            "Custom color IDs must be distinct"
        );
        let n = field(&c, "name")?;
        ensure!(
            !n.is_empty()
                && n.len() <= 48
                && n.trim() == n
                && n.as_bytes()[0].is_ascii_alphanumeric()
                && n.bytes()
                    .all(|b| b.is_ascii_alphanumeric() || [b' ', b'_', b'-'].contains(&b)),
            "Color names require 1 to 48 ASCII letters, numbers, spaces, underscores or hyphens"
        );
        ensure!(
            names.insert(n.replace(' ', "").to_ascii_lowercase()),
            "Color name conflicts with another color or package filename"
        );
        let bin = c["bin"]
            .as_u64()
            .context("Choose Custom, Black, Brown, Blond or Red")?;
        ensure!(bin <= 4, "Choose Custom, Black, Brown, Blond or Red");
        let normalized = color::normalize(&c["curve"])?;
        c["curve"] = normalized["curve"].clone();
        c["tables"] = normalized["tables"].clone();
        c["kind"] = json!(if bin == 0 { "unnatural" } else { "natural" });
        c["family"] = Value::Null;
        colors.push(c);
    }
    Ok(colors)
}
fn key(c: &Value) -> &str {
    c.get("id")
        .and_then(Value::as_str)
        .unwrap_or(c["name"].as_str().unwrap())
}
fn selected(job: &Value, builtin: &Value) -> Result<Vec<Value>> {
    let colors = palette(job, builtin)?;
    let chosen = strings(&job["colors"])?;
    ensure!(
        !chosen.is_empty()
            && chosen.len() <= 59
            && chosen.iter().collect::<BTreeSet<_>>().len() == chosen.len(),
        "Select 1 through 59 distinct colors"
    );
    chosen
        .iter()
        .map(|id| {
            colors
                .iter()
                .find(|c| key(c) == id)
                .cloned()
                .context("Unknown selected color")
        })
        .collect()
}
fn resolve_game(required: &BTreeSet<String>, game: &Value) -> Result<Vec<Value>> {
    let records = game["resources"]
        .as_object()
        .context("Game mesh catalog missing")?;
    let mut found = vec![];
    for k in required {
        let candidates = if records.contains_key(k) {
            vec![k.as_str()]
        } else if k.len() == 34 && k[18..].starts_with("00000000") {
            records
                .keys()
                .filter(|c| c.len() == 34 && c[..18] == k[..18] && c[26..] == k[26..])
                .map(String::as_str)
                .collect()
        } else {
            vec![]
        };
        if candidates.len() == 1 {
            let mut v = records[candidates[0]].clone();
            v["key"] = json!(k);
            found.push(v);
        }
    }
    Ok(found)
}
fn ages(info: &Value) -> Vec<String> {
    let flags = info["ages"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|a| a["age"].as_u64())
        .fold(0, |a, b| a | b);
    [
        (1, "Toddler"),
        (2, "Child"),
        (4, "Teen"),
        (64, "Young Adult"),
        (8, "Adult"),
        (16, "Elder"),
    ]
    .into_iter()
    .filter(|(n, _)| flags & n != 0)
    .map(|(_, v)| v.into())
    .collect()
}
fn inspect_packages(p: &Value, game: &Value) -> Result<Value> {
    let files = strings(&p["files"])?;
    ensure!(
        !files.is_empty() && files.len() <= 64,
        "Choose 1 through 64 packages"
    );
    let mut names = BTreeSet::new();
    let mut total = 0;
    let mut meshes = vec![];
    let mut recolors = vec![];
    let mut owners: BTreeMap<String, String> = BTreeMap::new();
    for file in &files {
        ensure!(
            file.to_ascii_lowercase().ends_with(".package")
                && !file.contains(['/', '\\', ':'])
                && !file.chars().any(|c| c < ' ')
                && names.insert(file.to_lowercase()),
            "Choose .package files with distinct filenames and no paths"
        );
        let bytes = assets::read(file)?;
        total += bytes.len();
        ensure!(
            bytes.len() <= 64 * 1024 * 1024 && total <= 128 * 1024 * 1024,
            "Package files exceed the 64 MiB each or 128 MiB total limit"
        );
        let inv = hair::inventory_asset(file)?;
        if inv["kind"] == "mesh" {
            for k in strings(&inv["resource_keys"])? {
                ensure!(
                    owners.insert(k, file.clone()).is_none(),
                    "Mesh packages have conflicting resource identities"
                );
            }
            meshes.push((file.clone(), inv));
        } else {
            recolors.push(file.clone());
        }
    }
    ensure!(
        !meshes.is_empty() && !recolors.is_empty(),
        "Both mesh packages and at least one matching recolor package are required"
    );
    for (file, inv) in &meshes {
        ensure!(
            strings(&inv["links"])?
                .iter()
                .all(|k| owners.contains_key(k)),
            "Mesh {file} has unresolved references. Include all matching mesh packages"
        );
    }
    let mut items = vec![];
    let mut all_used = BTreeSet::new();
    for file in recolors {
        let info = hair::inspect_asset(&file, None)?;
        let required: BTreeSet<_> = strings(&info["external_meshes"])?.into_iter().collect();
        let missing: BTreeSet<_> = required
            .iter()
            .filter(|k| !owners.contains_key(*k))
            .cloned()
            .collect();
        let game_refs = resolve_game(&missing, game)?;
        let game_keys: BTreeSet<_> = game_refs
            .iter()
            .map(|v| v["key"].as_str().unwrap().to_string())
            .collect();
        ensure!(
            missing.is_subset(&game_keys),
            "Mesh and recolor do not match. Missing references: {}",
            missing
                .difference(&game_keys)
                .cloned()
                .collect::<Vec<_>>()
                .join(", ")
        );
        let mut used: BTreeSet<String> = required
            .iter()
            .filter_map(|k| owners.get(k).cloned())
            .collect();
        loop {
            let before = used.len();
            for (n, inv) in &meshes {
                if used.contains(n) {
                    for k in strings(&inv["links"])? {
                        used.insert(owners[&k].clone());
                    }
                }
            }
            if before == used.len() {
                break;
            }
        }
        let mut dependencies = vec![];
        for (n, inv) in &meshes {
            if used.contains(n) {
                dependencies.push(json!({"filename":n,"sha256":hash(assets::read(n)?),"resource_keys":inv["resource_keys"],"links":inv["links"]}));
            }
        }
        dependencies.sort_by_key(|v| v["filename"].as_str().unwrap().to_lowercase());
        all_used.extend(used);
        let digest = hash(assets::read(&file)?);
        let id = hash(serde_json::to_vec(&json!([digest, dependencies]))?);
        let flags = info["ages"]
            .as_array()
            .unwrap()
            .iter()
            .filter_map(|a| a["gender"].as_u64())
            .fold(0, |a, b| a | b);
        let requirements = format!(
            "Install the included mesh files with the recolors. {}",
            game_refs
                .iter()
                .filter_map(|g| g["requirement"].as_str())
                .collect::<BTreeSet<_>>()
                .into_iter()
                .collect::<Vec<_>>()
                .join(", ")
        );
        items.push(json!({"id":id,"kind":"custom","label":file.trim_end_matches(".package"),"source_filename":file,"recolor_filename":file,"template_file":file,"template_sha256":digest,"inspection":info,"meshes":dependencies,"game_meshes":game_refs,"ages":ages(&info),"gender":if flags==1{"Female"}else if flags==2{"Male"}else{"Female and male"},"requirements":requirements,"input_base":"Arbitrary texture"}));
    }
    ensure!(
        all_used.len() == meshes.len(),
        "The selection contains a mesh unrelated to its recolors"
    );
    Ok(json!({"items":items}))
}
fn base(slot: &str, config: &Value, mappings: &Value) -> Result<image::RgbaImage> {
    let config = color::settings(config)?;
    let cache = format!("base/{slot}/{}", hash(serde_json::to_vec(&config)?));
    if let Some(i) = assets::image_get(&cache) {
        return Ok(i);
    }
    let image =
        assets::image_get(&format!("source/{slot}.png")).context("Texture is not loaded")?;
    let result = color::prepare(&image, &config, mappings)?;
    assets::image_insert(&cache, result.clone());
    assets::trim_base_cache();
    Ok(result)
}
fn rendered(slot: &str, config: &Value, c: &Value, builtin: &Value) -> Result<image::RgbaImage> {
    let cache = format!("base/elder/{slot}/{}", hash(serde_json::to_vec(config)?));
    if c["name"] == "Mail Bomb" {
        if let Some(image) = assets::image_get(&cache) {
            return Ok(image);
        }
    }
    let b = base(slot, config, &builtin["mappings"])?;
    let table = if c.get("tables").is_some() {
        color::tables(&c["tables"])?
    } else {
        color::tables(&builtin["mappings"][c["name"].as_str().unwrap()])?
    };
    let image = color::apply(&b, &table);
    if c["name"] == "Mail Bomb" {
        assets::image_insert(&cache, image.clone());
        assets::trim_base_cache();
    }
    Ok(image)
}
fn hair_job(p: &Value, builtin: &Value) -> Result<Value> {
    let mut job = p.clone();
    job["texture_encoder"] = json!(crate::texture_encoding::Encoder::from_job(p)?);
    job["refpack_compression"] = json!(crate::package_compression::from_job(p)?);
    name(&job["creator"])?;
    name(&job["hair_name"])?;
    let seed = field(&job, "id")?.to_string();
    ensure!(
        seed.len() == 32 && seed.bytes().all(|b| b.is_ascii_hexdigit()),
        "Invalid job identity"
    );
    let info = hair::inspect_asset("hair-template", None)?;
    let colors = selected(&job, builtin)?;
    let configs = job["texture_settings"]
        .as_object()
        .context("Assign preparation for every texture")?;
    let slots = info["textures"].as_array().unwrap();
    ensure!(
        configs.len() == slots.len()
            && slots
                .iter()
                .all(|s| configs.contains_key(s["id"].as_str().unwrap())),
        "Assign preparation for every texture slot"
    );
    for c in configs.values() {
        color::settings(c)?;
    }
    let has_elder = ages(&info).iter().any(|a| a == "Elder");
    let mut identities = serde_json::Map::new();
    let mut groups = BTreeSet::new();
    let source_groups: BTreeSet<_> =
        strings(&hair::inventory_asset("hair-template")?["resource_keys"])?
            .iter()
            .map(|k| k[9..17].to_string())
            .collect();
    for c in &colors {
        ensure!(
            c["kind"] != "grey" || has_elder,
            "This hairstyle has no Elder entry for standalone grey colors"
        );
        let k = key(c);
        let mut salt = 0;
        let g = loop {
            let g = group(&format!("{seed}/color/{k}/{salt}"));
            if !source_groups.contains(&format!("{g:08x}")) && groups.insert(g) {
                break g;
            }
            salt += 1;
        };
        let family = if let Some(n) = c["family"].as_u64() {
            uuid(&format!("{seed}/family/{n}"))
        } else {
            uuid(&format!("{seed}/family/{k}"))
        };
        let bin = c["bin"].as_u64().unwrap();
        identities.insert(k.into(),json!({"group":g,"family":family,"hairtone":if bin==0{uuid(&format!("{seed}/hairtone/{k}"))}else{format!("{bin:08x}-0000-0000-0000-000000000000")},"creator_uuid":uuid(&format!("{seed}/creator"))}));
    }
    job["identities"] = json!(identities);
    job["inspection"] = info;
    job["custom_colors"] = json!(palette(&job, builtin)?
        .into_iter()
        .filter(|c| c.get("id").is_some())
        .collect::<Vec<_>>());
    Ok(job)
}
fn tattoo_job(p: &Value) -> Result<Value> {
    let encoder = crate::texture_encoding::Encoder::from_job(p)?;
    let seed = field(p, "id")?;
    ensure!(
        seed.len() == 32 && seed.bytes().all(|b| b.is_ascii_hexdigit()),
        "Invalid job identity"
    );
    let spec = &p["spec"];
    let tattoos = spec["tattoos"].as_array().context("Add a tattoo")?;
    ensure!(
        !tattoos.is_empty() && tattoos.len() <= 20,
        "Add 1 through 20 tattoos"
    );
    let mut used = BTreeSet::new();
    let guid = group(&format!("{seed}/box"));
    used.insert(guid);
    let mut result = vec![];
    let mut total = 0usize;
    let mut file_ids = BTreeSet::new();
    for t in tattoos {
        let k = field(t, "key")?;
        let mut n = 0;
        let g = loop {
            let g = group(&format!("{seed}/tattoo/{k}/{n}"));
            if used.insert(g) {
                break g;
            }
            n += 1;
        };
        let mut inputs = json!({"am":null,"af":null});
        for (gender, id) in t["assets"].as_object().context("Choose tattoo textures")? {
            ensure!(["am", "af"].contains(&gender.as_str()), "Choose AM or AF");
            let id = id.as_str().context("Invalid image asset")?;
            ensure!(
                file_ids.insert(id.to_string()),
                "Duplicate tattoo image asset"
            );
            let bytes = assets::read(id)?;
            total += bytes.len();
            ensure!(
                bytes.len() <= 8 * 1024 * 1024 && total <= 128 * 1024 * 1024,
                "PNG inputs exceed the 8 MiB each or 128 MiB total limit"
            );
            ensure!(
                bytes.len() >= 33
                    && bytes[..8] == [137, 80, 78, 71, 13, 10, 26, 10]
                    && bytes[24] == 8
                    && bytes[25] == 6,
                "Use an RGBA8 PNG"
            );
            let mut off = 8;
            while off + 12 <= bytes.len() {
                let len = u32::from_be_bytes(bytes[off..off + 4].try_into().unwrap()) as usize;
                ensure!(
                    off.checked_add(12 + len)
                        .is_some_and(|end| end <= bytes.len()),
                    "Malformed PNG chunk"
                );
                ensure!(
                    &bytes[off + 4..off + 8] != b"acTL",
                    "Animated PNGs are unsupported"
                );
                off += len + 12;
            }
            ensure!(
                u32::from_be_bytes(bytes[16..20].try_into().unwrap()) == 1024
                    && u32::from_be_bytes(bytes[20..24].try_into().unwrap()) == 1024,
                "Tattoo PNGs must be 1024 by 1024 RGBA8"
            );
            let img = assets::open_image(id)?;
            ensure!(
                img.width() == 1024
                    && img.height() == 1024
                    && img.color() == image::ColorType::Rgba8,
                "Tattoo PNGs must be 1024 by 1024 RGBA8"
            );
            let rgba = img.into_rgba8();
            ensure!(
                rgba.pixels().any(|p| p[3] > 0),
                "PNG alpha channel is blank"
            );
            ensure!(
                rgba.pixels()
                    .any(|p| p[3] > 0 && p.0[..3].iter().any(|c| *c > 0)),
                "PNG has no visible RGB content inside its alpha mask"
            );
            inputs[gender] = json!(id);
        }
        let layer = t["layer_order"].as_u64().context("Invalid layer order")?;
        result.push(json!({"key":k,"menu_label":t["menu_label"],"menu_order":t["menu_order"],"layer_order":layer,"priority":101+layer,"input_pngs":inputs,"identity":{"overlay_group_id":format!("0x{g:08X}"),"family_uuid":uuid(&format!("{seed}/tattoo/{k}/family"))}}));
    }
    Ok(
        json!({"refpack_compression":crate::package_compression::from_job(p)?,"texture_encoder":encoder,"schema_version":2,"slug":spec["bundle"]["slug"],"catalog_name":spec["bundle"]["catalog_name"],"catalog_description":spec["bundle"]["catalog_description"],"ages":["adult","elder"],"compatibility":{"plantsim":false,"vampire":false,"werewolf":false,"zombie":false,"servo":false,"bigfoot":false},"identity":{"box_guid":format!("0x{guid:08X}")},"tattoos":result}),
    )
}
fn readme(job: &Value, builtin: &Value) -> Result<String> {
    let colors = selected(job, builtin)?;
    let mut lines=vec![format!("{}_{} Recolors",field(job,"creator")?,field(job,"hair_name")?),String::new(),"INSTALLATION".into(),"Extract the ZIP. Put the selected .package files and any included Meshes folder into Documents/EA Games/The Sims 2/Downloads. Enable custom content and restart the game.".into(),job["requirements"].as_str().unwrap_or("").into(),format!("Hairstyle: {}",job["template_label"].as_str().unwrap_or("Uploaded hairstyle")),format!("Supported ages: {}",ages(&job["inspection"]).join(", ")),"These are independent recolors. Keep required meshes installed.".into(),String::new(),"SELECTED COLORS".into(),colors.iter().map(|c|c["name"].as_str().unwrap()).collect::<Vec<_>>().join(", "),"Built-in palette: 43 fixed Pooklet presets.".into(),String::new(),"CUSTOM COLORS".into()];
    for c in &colors {
        if c.get("id").is_some() {
            lines.push(format!(
                "{}: {}, {}",
                c["name"].as_str().unwrap(),
                ["Custom", "Black", "Brown", "Blond", "Red"][c["bin"].as_u64().unwrap() as usize],
                if c["curve"]["source"] == "gimp" {
                    format!(
                        "imported GIMP curve {}",
                        c["curve"]["filename"].as_str().unwrap_or("")
                    )
                } else {
                    "browser curve editor".into()
                }
            ));
        }
    }
    lines.extend(["Custom colors use the prepared Volatile base and each has an independent hairstyle family.".into(),String::new(),"FAMILIES AND ELDERS".into()]);
    for (i, f) in builtin["families"]
        .as_array()
        .context("Missing families")?
        .iter()
        .enumerate()
    {
        lines.push(format!(
            "Family {}: {} (black / brown / blond / red)",
            i + 1,
            strings(f)?.join(" / ")
        ));
    }
    lines.extend(["Natural colors, including custom Black/Brown/Blond/Red, use Mail Bomb for supported Elders. Custom-category and unnatural colors retain their color. Standalone Mail Bomb and Pipe Bomb are Elder-only.".into(),"No ages are added. Install selected members of each family for color switching. Every color package includes its own supported Elder resources.".into(),String::new(),"BASE PREPARATION".into()]);
    for (k, c) in job["texture_settings"]
        .as_object()
        .context("Missing texture settings")?
    {
        lines.push(format!(
            "{k}: base {}, black {}, white {}, gamma {}. Embedded alpha preserved.",
            c["base"], c["black"], c["white"], c["gamma"]
        ));
    }
    lines.extend(["Each embedded atlas keeps its own age and material routes, dimensions, compression and full mip chain. Accessories sharing an atlas receive the same processing.".into(),"Arbitrary texture preparation is approximate: sRGB luma and levels followed by the dedicated grey-base-to-Volatile mapping. Pooklet Grey uses the distinct PookletSpecial mapping.".into(),"Every target is applied independently to the prepared Volatile base. Compression can quantize alpha.".into(),String::new(),"CREDITS".into(),format!("Submission: {}",field(job,"creator")?),format!("Source hairstyle and recolor: {}",job["template_credit"].as_str().or(job["template_label"].as_str()).unwrap_or("Original package creator")),"Pooklet: original palette and curves. IaKoa: GIMP conversions, https://iakoasims.tumblr.com/post/88756518223/pooklet-for-gimp".into(),"Swirl template when selected: Pooklet, https://simfileshare.net/download/321854/".into(),"Original game assets: Maxis / Electronic Arts. Custom hairstyle credits remain with their original creators.".into(),"Fixed mappings are distributed with the browser engine. Custom curves are saved only in this browser's batch. Raw curves are not included in this ZIP.".into(),String::new(),"VALIDATION".into(),"Every package was reopened and structurally validated. Structural checks do not establish live gameplay compatibility.".into()]);
    let encoder = crate::texture_encoding::Encoder::from_job(job)?;
    use dbpf::internal_file::resource_collection::texture_resource::TextureFormat;
    lines.extend([String::new(), "TEXTURE COMPRESSION".into(), format!("Selected encoder: {}. DXT1 uses {}. DXT3 uses {}. DXT5 uses {}. Full mipmaps are retained. Untouched source textures are preserved.", encoder.name(), encoder.effective(TextureFormat::DXT1), encoder.effective(TextureFormat::DXT3), encoder.effective(TextureFormat::DXT5))]);
    lines.extend([String::new(), "PACKAGE COMPRESSION".into(), format!("RefPack: {}. This lossless storage layer preserves texture quality, alpha and full mipmaps. Uploaded mesh packages are copied unchanged.", if crate::package_compression::from_job(job)? {"enabled"} else {"disabled"})]);
    Ok(lines.join("\n"))
}
fn dispatch(op: &str, p: &Value, builtin: &Value, game: &Value) -> Result<Value> {
    if op == "encode_texture" {
        return crate::texture_encoding::dispatch(p);
    }
    if op.starts_with("object_") {
        return crate::object::dispatch(op, p);
    }
    if op.starts_with("painting_") {
        return crate::painting::dispatch(op, p);
    }
    if op.starts_with("sim_") {
        return crate::sim::dispatch(op, p);
    }
    match op {
        "reset_inputs" => {
            assets::reset_inputs();
            Ok(json!({}))
        }
        "inspect_packages" => inspect_packages(p, game),
        "inventory" => hair::inventory_asset(field(p, "asset")?),
        "parse_curve" => color::parse_gimp(
            &assets::read(field(p, "asset")?)?,
            p["filename"].as_str().unwrap_or(""),
        ),
        "process_pixels" => {
            let image = assets::open_image(field(p, "asset")?)?.into_rgba8();
            let prepared = color::prepare(&image, &p["settings"], &builtin["mappings"])?;
            let rendered = color::apply(&prepared, &color::tables(&p["tables"])?);
            let report = json!({"width":rendered.width(),"height":rendered.height(),"sha256":hash(rendered.as_raw())});
            assets::write("pixels", rendered.as_raw())?;
            Ok(report)
        }
        "normalize_curve" => color::normalize(&p["curve"]),
        "prepare_hair_job" => hair_job(p, builtin),
        "prepare_tattoo_job" => tattoo_job(p),
        "open_hair" => {
            let bytes = assets::read(field(p, "asset")?)?;
            assets::write("hair-template", bytes)?;
            assets::clear_images();
            let mut info = hair::inspect_asset("hair-template", Some("source"))?;
            for slot in info["textures"].as_array_mut().unwrap() {
                let id = slot["id"].as_str().unwrap();
                let image = assets::image_get(&format!("source/{id}.png"))
                    .context("Texture decoding failed")?;
                slot["preview"] = json!(uri(&image)?);
                slot["supported_ages"] = json!(ages(
                    &json!({"ages":slot["ages"].as_array().unwrap().iter().map(|age|json!({"age":age})).collect::<Vec<_>>()})
                ));
            }
            Ok(info)
        }
        "preview" => {
            let job = hair_job(&p["job"], builtin)?;
            let slot = field(p, "slot")?;
            ensure!(
                job["texture_settings"].get(slot).is_some(),
                "Unknown texture slot"
            );
            let config = &job["texture_settings"][slot];
            let original = assets::image_get(&format!("source/{slot}.png"))
                .context("Missing source texture")?;
            let prepared = base(slot, config, &builtin["mappings"])?;
            let elder = job["inspection"]["textures"]
                .as_array()
                .unwrap()
                .iter()
                .find(|s| s["id"] == slot)
                .unwrap()["ages"]
                .as_array()
                .unwrap()
                .iter()
                .all(|a| a == 16);
            let mail = builtin["palette"]
                .as_array()
                .unwrap()
                .iter()
                .find(|c| c["name"] == "Mail Bomb")
                .unwrap();
            let mut targets = vec![];
            for c in selected(&job, builtin)? {
                let target = if elder && c["kind"] == "natural" {
                    mail
                } else {
                    &c
                };
                targets.push(json!({"active":c["kind"]!="grey"||job["inspection"]["textures"].as_array().unwrap().iter().find(|s|s["id"]==slot).unwrap()["ages"].as_array().unwrap().contains(&json!(16)),"color":key(&c),"rendered_color":target["name"],"image":uri(&rendered(slot,config,target,builtin)?)?,"elder_image":if c["kind"]=="natural"&&ages(&job["inspection"]).contains(&"Elder".into()){Some(uri(&rendered(slot,config,mail,builtin)?)?)}else{None::<String>}}));
            }
            Ok(
                json!({"slot":slot,"settings":config,"original":uri(&original)?,"base":uri(&prepared)?,"targets":targets,"job":job}),
            )
        }
        "build_hair" | "validate_hair" => {
            let job = hair_job(&p["job"], builtin)?;
            let colors = selected(&job, builtin)?;
            let c = colors
                .iter()
                .find(|c| key(c) == p["color"].as_str().unwrap_or(""))
                .context("Unknown color")?;
            let mut s = job["identities"][key(c)].clone();
            let mut textures = serde_json::Map::new();
            let mut elders = serde_json::Map::new();
            let mail = builtin["palette"]
                .as_array()
                .unwrap()
                .iter()
                .find(|c| c["name"] == "Mail Bomb")
                .unwrap();
            for (slot, config) in job["texture_settings"].as_object().unwrap() {
                let n = format!("target/{slot}");
                assets::image_insert(&n, rendered(slot, config, c, builtin)?);
                textures.insert(slot.clone(), json!(n));
                if c["kind"] == "natural" {
                    let n = format!("elder/{slot}");
                    assets::image_insert(&n, rendered(slot, config, mail, builtin)?);
                    elders.insert(slot.clone(), json!(n));
                }
            }
            s["texture_encoder"] = job["texture_encoder"].clone();
            s["refpack_compression"] = json!(crate::package_compression::from_job(&job)?);
            s["template"] = json!("hair-template");
            s["textures"] = json!(textures);
            s["elder_textures"] = json!(elders);
            s["label"] = json!(format!(
                "{} {} {}",
                field(&job, "creator")?,
                field(&job, "hair_name")?,
                c["name"].as_str().unwrap()
            ));
            s["color"] = c["name"].clone();
            s["bin"] = c["bin"].clone();
            s["grey_only"] = json!(c["kind"] == "grey");
            s["grey_elders"] = json!(c["kind"] == "natural");
            if op == "validate_hair" {
                hair::validate_asset(s, field(p, "asset")?)
            } else {
                hair::build_asset(s, "output")
            }
        }
        "build_tattoo" => tattoo::build_asset(p["job"].clone(), "output"),
        "validate_tattoo" => tattoo::validate_asset(p["job"].clone(), field(p, "asset")?),
        "readme" => Ok(json!({"text":readme(&p["job"],builtin)?})),
        _ => bail!("Unknown engine operation"),
    }
}
