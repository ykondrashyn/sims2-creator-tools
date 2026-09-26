//! Offline conversion of an extracted game hairstyle into an isolated recolor.
//! Public uploads still require ordinary recolors with embedded textures.
use super::*;
use crate::core::resources::lifo;
use dbpf::internal_file::resource_collection::texture_resource::{
    EmbeddedTextureResourceMipLevel, TextureResourceData,
};

const LIFO: u32 = 0xed534136;

fn reference(key: Key) -> dbpf::internal_file::sim_outfits::Entry {
    dbpf::internal_file::sim_outfits::Entry {
        type_id: key.0.into(),
        group_id: key.1,
        instance_id: InstanceId { id: key.2 },
    }
}

pub(super) fn prepare(source: &Path, scaffold: &Path, output: &Path) -> Result<Value> {
    let (container, base) = load(scaffold)?;
    let (_, game) = load_raw(source)?;
    let sets: Vec<_> = game.iter().filter(|n| n.key().0 == GZPS).collect();
    ensure!(
        !sets.is_empty(),
        "Extract a single hairstyle's property sets first"
    );
    let families = sets
        .iter()
        .map(|n| string(&n.cpf()?, "family"))
        .collect::<Result<BTreeSet<_>>>()?;
    ensure!(
        families.len() == 1,
        "Game extraction mixes hairstyle families"
    );
    let mut selected = BTreeSet::new();
    let mut source_keys = BTreeSet::new();
    let mut external = BTreeSet::new();
    for gz in &sets {
        source_keys.extend([gz.key(), rendering_idr(&game, gz)?]);
        let p = gz.cpf()?;
        ensure!(
            number(&p, "outfit")? == 1 && number(&p, "flags")? & 9 == 0,
            "Only visible, ordinary game hair is supported"
        );
        let idr = find(&game, rendering_idr(&game, gz)?)?.idr()?;
        for field in ["resourcekeyidx", "shapekeyidx"] {
            let k = ref_key(
                idr.entries
                    .get(number(&p, field)? as usize)
                    .context("Invalid game mesh reference index")?,
            );
            find(&game, k).context("The extracted hair has an unverified game mesh reference")?;
            external.insert(key_text(k));
            source_keys.insert(k);
        }
        for k in age_materials(&game, gz)? {
            selected.insert(k);
            let m = find(&game, k)?;
            if !skin_material(m)? {
                selected.insert(resolve_texture(&game, m)?);
            }
        }
    }
    let mut nodes: Vec<_> = game
        .iter()
        .filter(|n| selected.contains(&n.key()))
        .cloned()
        .collect();
    source_keys.extend(selected);
    let mut lifos = BTreeMap::new();
    for n in game.iter().filter(|n| n.key().0 == LIFO) {
        let (name, width, height, data) = lifo(n)?;
        ensure!(
            lifos.insert(name, (n.key(), width, height, data)).is_none(),
            "Duplicate game LIFO name"
        );
    }
    for n in nodes.iter_mut().filter(|n| n.key().0 == TXTR) {
        let mut c = n.collection()?;
        let ResourceData::Texture(t) = &mut c.entries[0].data else {
            bail!("Invalid game texture")
        };
        let format = t.get_format();
        ensure!(
            t.textures.len() == 1,
            "Animated game textures are unsupported"
        );
        let count = t.mip_levels();
        for (i, mip) in t.textures[0].entries.iter_mut().enumerate() {
            if let TextureResourceData::LIFOFile { file_name } = mip {
                let name = format!("{}_lifo", file_name.to_string().trim_end_matches("_lifo"));
                let (key, w, h, data) = lifos
                    .get(&name)
                    .with_context(|| format!("Extract missing game texture level {name}"))?;
                source_keys.insert(*key);
                ensure!(
                    (*w, *h)
                        == (
                            (t.width >> (count - i - 1)).max(1),
                            (t.height >> (count - i - 1)).max(1)
                        )
                        && data.len() == format.compressed_size(*w as usize, *h as usize),
                    "Game LIFO dimensions or compression do not match its texture"
                );
                *mip = TextureResourceData::Embedded(EmbeddedTextureResourceMipLevel {
                    data: data.clone(),
                });
            }
        }
        n.set(&c)?;
    }
    let strings = base
        .iter()
        .find(|n| n.key().0 == STR)
        .context("Missing catalog strings")?
        .clone();
    let mut tone = base
        .iter()
        .find(|n| n.key().0 == XHTN)
        .context("Missing hairtone scaffold")?
        .clone();
    let catalog = base.iter().find(|n| n.key().0 == BINX).unwrap();
    let collection = base
        .iter()
        .filter(|n| n.key().0 == IDR)
        .flat_map(|n| n.idr().unwrap().entries)
        .find(|e| e.type_id.code() == COLL)
        .unwrap();
    let mut tone_properties = tone.cpf()?;
    uint(
        &mut tone_properties,
        "age",
        sets.iter()
            .map(|n| number(&n.cpf().unwrap(), "age").unwrap())
            .fold(0, |a, b| a | b),
    );
    uint(
        &mut tone_properties,
        "gender",
        number(&sets[0].cpf()?, "gender")?,
    );
    tone.set(&tone_properties)?;
    nodes.extend([strings.clone(), tone.clone()]);
    for (i, gz) in sets.iter().enumerate() {
        let mut gz = (*gz).clone();
        let mut p = gz.cpf()?;
        uint(&mut p, "parts", 1);
        uint(&mut p, "product", 0);
        uint(&mut p, "version", 6);
        gz.set(&p)?;
        let mut idr = find(&game, rendering_idr(&game, &gz)?)?.clone();
        let mut refs = idr.idr()?;
        // Preserve the original indexed mesh/material assignments. Remove only
        // unused duplicate rendering entries before adding the catalog links.
        let end = (0..number(&p, "numoverrides")?)
            .map(|j| number(&p, &format!("override{j}resourcekeyidx")).unwrap())
            .chain([number(&p, "resourcekeyidx")?, number(&p, "shapekeyidx")?])
            .max()
            .unwrap() as usize
            + 1;
        refs.entries.truncate(end);
        let icon = refs.entries.len() as u32;
        refs.entries.extend([
            reference((0, 0, 0)),
            reference(strings.key()),
            collection.clone(),
            reference(gz.key()),
        ]);
        idr.set(&refs)?;
        let mut bin = catalog.clone();
        bin.entry.group_id = gz.key().1;
        bin.entry.instance_id = gz.entry.instance_id;
        let mut props = bin.cpf()?;
        for (field, value) in [
            ("iconidx", icon),
            ("stringsetidx", icon + 1),
            ("binidx", icon + 2),
            ("objectidx", icon + 3),
        ] {
            uint(&mut props, field, value);
        }
        bin.set(&props)?;
        nodes.extend([gz, idr, bin]);
        ensure!(i < 16, "Too many game age entries");
    }
    let mut idr = base.iter().find(|n| n.key().0 == IDR).unwrap().clone();
    idr.entry.instance_id = InstanceId { id: 0xfffffff0 };
    idr.set(&SimOutfits {
        version: dbpf::IndexMinorVersion::V2,
        entries: vec![
            reference((0, 0, 0)),
            reference(strings.key()),
            collection,
            reference(tone.key()),
        ],
    })?;
    let mut bin = catalog.clone();
    bin.entry.instance_id = idr.entry.instance_id;
    let mut p = bin.cpf()?;
    for (field, value) in [
        ("iconidx", 0),
        ("stringsetidx", 1),
        ("binidx", 2),
        ("objectidx", 3),
    ] {
        uint(&mut p, field, value);
    }
    bin.set(&p)?;
    nodes.extend([idr, bin]);
    // Intermediate namespace is private and never downloadable. All public
    // templates and user jobs subsequently pass the ordinary clone validator.
    let keys: BTreeSet<_> = nodes.iter().map(Node::key).collect();
    for n in &mut nodes {
        n.entry.group_id = 0x5ffffffe;
        if n.key().0 == IDR {
            let mut refs = n.idr()?;
            for e in &mut refs.entries {
                if keys.contains(&ref_key(e)) {
                    e.group_id = 0x5ffffffe;
                }
            }
            n.set(&refs)?;
        }
    }
    ensure!(
        nodes.iter().map(Node::key).collect::<BTreeSet<_>>().len() == nodes.len(),
        "Game extraction has colliding resource identities"
    );
    write(container, &mut nodes, output)?;
    let (_, reopened) = load_raw(output)?;
    let inspection = inspect(&reopened, None)?;
    ensure!(
        inspection
            .external_meshes
            .iter()
            .cloned()
            .collect::<BTreeSet<_>>()
            == external,
        "Game mesh references changed during preparation"
    );
    let source_hashes = source_keys
        .iter()
        .map(|key| {
            Ok((
                key_text(*key),
                format!("{:x}", Sha256::digest(&find(&game, *key)?.bytes)),
            ))
        })
        .collect::<Result<BTreeMap<_, _>>>()?;
    Ok(
        json!({"inspection": inspection, "source_keys": source_keys.into_iter().map(key_text).collect::<Vec<_>>(), "source_hashes": source_hashes}),
    )
}
