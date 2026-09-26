//! Plan color variants from live age routes, preserving unrelated source data.
use super::*;

pub(super) struct ResourcePlan {
    pub removed: BTreeSet<Key>,
    pub elder_idrs: BTreeSet<Key>,
    pub retained_slots: BTreeSet<(Key, usize)>,
    variants: BTreeMap<(Key, bool), bool>,
}

fn linked_key(link: &dbpf::internal_file::resource_collection::FileLink) -> Key {
    (
        link.type_id.code(),
        link.group_id,
        ((link.resource_id as u64) << 32) | link.instance_id as u64,
    )
}

pub(super) fn named_texture(source: &[Node], name: &str) -> Result<Key> {
    let matches: Vec<_> = source
        .iter()
        .filter(|n| n.key().0 == TXTR)
        .filter(|n| texture_aliases(n).is_ok_and(|a| a.contains(&name.to_ascii_lowercase())))
        .map(Node::key)
        .collect();
    ensure!(
        matches.len() == 1,
        "A material file list references an unresolved or ambiguous texture"
    );
    Ok(matches[0])
}

fn dependencies(source: &[Node], node: &Node) -> Result<Vec<Key>> {
    let c = node.collection()?;
    for key in c
        .links
        .iter()
        .map(linked_key)
        .filter(|k| [TXMT, TXTR].contains(&k.0))
    {
        ensure!(
            source.iter().any(|n| n.key() == key),
            "An appearance resource contains an unresolved texture or material link"
        );
    }
    let mut keys: BTreeSet<_> = c
        .links
        .iter()
        .map(linked_key)
        .filter(|k| source.iter().any(|n| n.key() == *k))
        .collect();
    if node.key().0 == TXMT && !skin_material(node)? {
        keys.insert(resolve_texture(source, node)?);
        let ResourceData::Material(m) = &c.entries[0].data else {
            bail!("Invalid material")
        };
        // A sole legacy file-list entry can still name the original Maxis atlas.
        // The base property is authoritative in that case, as in the importer.
        if m.names.len() > 1 {
            for name in &m.names {
                keys.insert(named_texture(source, &name.to_string())?);
            }
        }
    }
    Ok(keys.into_iter().collect())
}

fn demand(
    deps: &BTreeMap<Key, Vec<Key>>,
    scalps: &BTreeSet<Key>,
    reached: &mut BTreeSet<(Key, bool)>,
    roots: impl IntoIterator<Item = (Key, bool)>,
) {
    let mut pending: Vec<_> = roots.into_iter().collect();
    while let Some((key, elder)) = pending.pop() {
        let elder = elder && !scalps.contains(&key);
        if reached.insert((key, elder)) {
            if let Some(links) = deps.get(&key) {
                pending.extend(links.iter().map(|k| (*k, elder)));
            }
        }
    }
}

impl ResourcePlan {
    pub fn new(s: &Spec, source: &[Node]) -> Result<Self> {
        let mut removed = BTreeSet::new();
        let mut elder_idrs = BTreeSet::new();
        let mut all_roots = vec![];
        let mut retained_roots = vec![];
        for gz in source.iter().filter(|n| n.key().0 == GZPS) {
            let age = number(&gz.cpf()?, "age")?;
            let materials = age_materials(source, gz)?;
            all_roots.extend(materials.iter().map(|k| (*k, false)));
            if age == 16 {
                elder_idrs.insert(rendering_idr(source, gz)?);
            }
            if s.grey_only && age != 16 {
                removed.insert(gz.key());
            } else {
                retained_roots.extend(
                    materials
                        .iter()
                        .map(|k| (*k, !s.grey_only && s.grey_elders && age == 16)),
                );
            }
        }
        if s.grey_only {
            let mut retained_idrs = elder_idrs.clone();
            for binx in source.iter().filter(|n| n.key().0 == BINX) {
                let idr_key = (IDR, binx.key().1, binx.key().2);
                let idr = find(source, idr_key)?.idr()?;
                let target = ref_key(&idr.entries[number(&binx.cpf()?, "objectidx")? as usize]);
                if removed.contains(&target) {
                    removed.insert(binx.key());
                } else {
                    retained_idrs.insert(idr_key);
                }
            }
            removed.extend(
                source
                    .iter()
                    .filter(|n| n.key().0 == IDR && !retained_idrs.contains(&n.key()))
                    .map(Node::key),
            );
        }
        let retained_slots = active_idr_slots(source, &removed)?;
        let deps: BTreeMap<_, _> = source
            .iter()
            .filter(|n| [TXMT, TXTR].contains(&n.key().0))
            .map(|n| Ok((n.key(), dependencies(source, n)?)))
            .collect::<Result<_>>()?;
        let scalps = source
            .iter()
            .filter(|n| n.key().0 == TXMT)
            .map(|n| Ok((n.key(), skin_material(n)?)))
            .collect::<Result<Vec<_>>>()?
            .into_iter()
            .filter(|(_, skin)| *skin)
            .map(|(key, _)| key)
            .collect();
        let mut all = BTreeSet::new();
        demand(&deps, &scalps, &mut all, all_roots);
        let mut reached = BTreeSet::new();
        demand(&deps, &scalps, &mut reached, retained_roots);
        // Preserve resources unrelated to any original age once, including their
        // dependencies. Inactive 3IDR slots alone do not keep removed ages alive.
        demand(
            &deps,
            &scalps,
            &mut reached,
            deps.keys()
                .filter(|k| !all.contains(&(**k, false)))
                .map(|k| (*k, false)),
        );
        for binx in source
            .iter()
            .filter(|n| n.key().0 == BINX && !removed.contains(&n.key()))
        {
            let idr_key = (IDR, binx.key().1, binx.key().2);
            let idr = find(source, idr_key)?.idr()?;
            let properties = binx.cpf()?;
            let roots = ["objectidx", "stringsetidx", "binidx", "iconidx"]
                .iter()
                .map(|field| {
                    let index = number(&properties, field)? as usize;
                    Ok((
                        ref_key(
                            idr.entries
                                .get(index)
                                .context("Invalid catalog reference index")?,
                        ),
                        false,
                    ))
                })
                .collect::<Result<Vec<_>>>()?;
            demand(&deps, &scalps, &mut reached, roots);
        }
        ensure!(
            reached.iter().all(|(k, _)| !removed.contains(k)),
            "A retained resource depends on an omitted age or catalog entry"
        );
        removed.extend(
            deps.keys()
                .filter(|k| !reached.contains(&(**k, false)) && !reached.contains(&(**k, true)))
                .copied(),
        );

        // Equal inputs share one encoded result. Compare decoded RGBA, including
        // transparent RGB, rather than filenames or lossy compressed pixels.
        let mut different = BTreeSet::new();
        for n in source.iter().filter(|n| n.key().0 == TXTR) {
            if reached.contains(&(n.key(), false)) && reached.contains(&(n.key(), true)) {
                if let Some(normal) = s.textures.get(&key_text(n.key())) {
                    let elder = s
                        .elder_textures
                        .get(&key_text(n.key()))
                        .context("Elder texture assignments are incomplete")?;
                    if crate::assets::open_image(normal)?.into_rgba8()
                        != crate::assets::open_image(elder)?.into_rgba8()
                    {
                        different.insert(n.key());
                    }
                }
            }
        }
        // A material needs two versions only if a dependency does. A fixed
        // point also handles supported collections with cycles in local links.
        loop {
            let before = different.len();
            for (key, links) in &deps {
                if reached.contains(&(*key, false))
                    && reached.contains(&(*key, true))
                    && links.iter().any(|k| different.contains(k))
                {
                    different.insert(*key);
                }
            }
            if before == different.len() {
                break;
            }
        }
        let mut variants = BTreeMap::new();
        for (key, elder) in &reached {
            if !deps.contains_key(key) {
                continue;
            }
            let coalesce = !different.contains(key) && reached.contains(&(*key, false));
            let scalp = key.0 == TXMT && skin_material(find(source, *key)?)?;
            variants.insert((*key, *elder), *elder && !coalesce && !scalp);
        }
        Ok(Self {
            removed,
            elder_idrs,
            retained_slots,
            variants,
        })
    }

    pub fn variants(&self, key: Key) -> BTreeSet<bool> {
        self.variants
            .iter()
            .filter(|((k, _), _)| *k == key)
            .map(|(_, v)| *v)
            .collect()
    }

    pub fn variant(&self, key: Key, elder: bool) -> Result<bool> {
        ensure!(
            !self.removed.contains(&key),
            "A retained reference targets a removed resource"
        );
        if ![TXMT, TXTR].contains(&key.0) {
            return Ok(false);
        }
        self.variants
            .get(&(key, elder))
            .copied()
            // A stale, inactive 3IDR slot may point to an otherwise live resource.
            // Relink it to that resource's surviving variant, without adding one.
            .or_else(|| self.variants(key).first().copied())
            .context("A retained reference has no planned color variant")
    }

    pub fn relink(
        &self,
        s: &Spec,
        source: &[Node],
        c: &mut ResourceCollection,
        elder: bool,
    ) -> Result<()> {
        for link in &mut c.links {
            let old = linked_key(link);
            if source.iter().any(|n| n.key() == old) {
                let new = new_key(s, old, self.variant(old, elder)?);
                link.group_id = new.1;
                link.instance_id = new.2 as u32;
                link.resource_id = (new.2 >> 32) as u32;
            }
        }
        Ok(())
    }
}
