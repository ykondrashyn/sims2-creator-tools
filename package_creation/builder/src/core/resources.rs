//! Container resources, reference identities and external mip resolution.
pub(crate) use super::properties::{number, string, text_prop, uint};
use crate::assets as fs;
use anyhow::{ensure, Context, Result};
use binrw::{BinRead, BinWrite};
use dbpf::common::BigString;
use dbpf::internal_file::{
    cpf::CPF,
    resource_collection::{FileName, ResourceCollection, ResourceData},
    sim_outfits::SimOutfits,
};
use dbpf::{DBPFFile, IndexEntry};
use std::{
    collections::{BTreeMap, BTreeSet},
    io::{Cursor, Read},
    path::Path,
};
pub(crate) type Key = (u32, u32, u64);
const TXTR: u32 = 0x1c4a276c;
const LIFO: u32 = 0xed534136;
#[derive(Clone)]
pub(crate) struct Node {
    pub(crate) entry: IndexEntry,
    pub(crate) bytes: Vec<u8>,
}
impl Node {
    pub(crate) fn key(&self) -> Key {
        (
            self.entry.type_id.code(),
            self.entry.group_id,
            self.entry.instance_id.id,
        )
    }
    pub(crate) fn cpf(&self) -> Result<CPF> {
        Ok(CPF::read_le(&mut Cursor::new(&self.bytes))?)
    }
    pub(crate) fn idr(&self) -> Result<SimOutfits> {
        Ok(SimOutfits::read_le(&mut Cursor::new(&self.bytes))?)
    }
    pub(crate) fn collection(&self) -> Result<ResourceCollection> {
        Ok(ResourceCollection::read_le(&mut Cursor::new(&self.bytes))?)
    }
    pub(crate) fn set<T: BinWrite>(&mut self, value: &T) -> Result<()>
    where
        for<'a> T::Args<'a>: Default,
    {
        let mut w = Cursor::new(Vec::new());
        value.write_le(&mut w)?;
        self.bytes = w.into_inner();
        Ok(())
    }
}
pub(crate) fn key_text(k: Key) -> String {
    format!("{:08x}-{:08x}-{:016x}", k.0, k.1, k.2)
}
pub(crate) fn embed_game_mips(nodes: &mut Vec<Node>) -> Result<()> {
    use dbpf::internal_file::resource_collection::texture_resource::{
        EmbeddedTextureResourceMipLevel, TextureResourceData,
    };
    let mut levels = BTreeMap::new();
    for n in nodes.iter().filter(|n| n.key().0 == 0xed534136) {
        let (name, w, h, data) = lifo(n)?;
        ensure!(
            levels
                .insert(name.to_ascii_lowercase(), (w, h, data))
                .is_none(),
            "Duplicate external mip name"
        );
    }
    for node in nodes.iter_mut().filter(|n| n.key().0 == TXTR) {
        let mut c = node.collection()?;
        for e in &mut c.entries {
            if let ResourceData::Texture(t) = &mut e.data {
                let count = t.mip_levels();
                let format = t.get_format();
                ensure!(
                    t.width > 0
                        && t.height > 0
                        && count > 0
                        && count
                            <= (32 - std::cmp::max(t.width, t.height).leading_zeros()) as usize,
                    "Invalid object texture dimensions or mip count"
                );
                ensure!(
                    t.width <= 4096 && t.height <= 4096 && t.textures.len() <= 6,
                    "Object texture exceeds supported dimensions or frame count"
                );
                for frame in &mut t.textures {
                    for (i, mip) in frame.entries.iter_mut().enumerate() {
                        if let TextureResourceData::LIFOFile { file_name } = mip {
                            let name =
                                format!("{}_lifo", file_name.to_string().trim_end_matches("_lifo"))
                                    .to_ascii_lowercase();
                            let (w,h,data)=levels.get(&name).with_context(||format!("Missing texture mip {name}. Include the matching texture package"))?;
                            ensure!(
                                i < count
                                    && (*w, *h)
                                        == (
                                            (t.width >> (count - i - 1)).max(1),
                                            (t.height >> (count - i - 1)).max(1)
                                        )
                                    && data.len()
                                        == format.compressed_size(*w as usize, *h as usize),
                                "External texture mip dimensions or format do not match"
                            );
                            *mip = TextureResourceData::Embedded(EmbeddedTextureResourceMipLevel {
                                data: data.clone(),
                            });
                        }
                    }
                }
            }
        }
        node.set(&c)?;
    }
    nodes.retain(|n| n.key().0 != 0xed534136);
    Ok(())
}
fn checked_header(b: &[u8]) -> Result<()> {
    ensure!(
        b.len() >= 96 && b.len() <= 64 * 1024 * 1024 && &b[..4] == b"DBPF",
        "Choose a Sims 2 package of at most 64 MiB"
    );
    let u = |p| u32::from_le_bytes(b[p..p + 4].try_into().unwrap()) as usize;
    ensure!(
        u(4) == 1 && [1, 2].contains(&u(8)) && u(32) == 7 && [1, 2].contains(&u(60)),
        "Only Sims 2 DBPF 1.1 and 1.2 packages are supported"
    );
    let (n, off, len) = (u(36), u(40), u(44));
    let width = if u(60) == 2 { 24 } else { 20 };
    ensure!(
        n > 0 && n <= 4096 && len == n * width && off >= 96 && off + len <= b.len(),
        "Invalid DBPF index bounds"
    );
    let mut ranges = vec![(off, off + len)];
    let mut expanded = 0usize;
    for p in (off..off + len).step_by(width) {
        let (start, size) = (u(p + width - 8), u(p + width - 4));
        ensure!(
            start >= 96 && size > 0 && start + size <= b.len(),
            "Invalid DBPF resource bounds"
        );
        let d = &b[start..start + size];
        let growth = if d.len() >= 9 && d[4..6] == [0x10, 0xfb] {
            ((d[6] as usize) << 16) | ((d[7] as usize) << 8) | d[8] as usize
        } else {
            size
        };
        ensure!(
            growth <= 64 * 1024 * 1024,
            "A decompressed resource exceeds 64 MiB"
        );
        expanded += growth;
        ranges.push((start, start + size));
    }
    ensure!(
        expanded <= 256 * 1024 * 1024,
        "The decompressed package exceeds 256 MiB"
    );
    ranges.sort();
    ensure!(
        ranges.windows(2).all(|r| r[0].1 <= r[1].0),
        "Overlapping DBPF resources"
    );
    Ok(())
}
pub(crate) fn load_raw(path: &Path) -> Result<(DBPFFile, Vec<Node>)> {
    let b = fs::read(path)?;
    checked_header(&b)?;
    let mut r = Cursor::new(b);
    let mut p = DBPFFile::read(&mut r)?;
    let mut nodes = vec![];
    let mut seen = BTreeSet::new();
    for e in &mut p.index {
        if e.type_id.code() == 0xe86b1eef {
            continue;
        }
        let bytes = e.data(&mut r)?.decompressed()?.data.clone();
        let node = Node {
            entry: e.clone(),
            bytes,
        };
        ensure!(seen.insert(node.key()), "Duplicate resource identity");
        nodes.push(node);
    }
    Ok((p, nodes))
}
pub(crate) fn crc(name: &str, width: u32, poly: u64, initial: u64) -> u32 {
    let mut value = initial;
    let mask = (1u64 << width) - 1;
    for byte in name.trim().to_ascii_lowercase().bytes() {
        value ^= (byte as u64) << (width - 8);
        for _ in 0..8 {
            value = if value & (1 << (width - 1)) != 0 {
                (value << 1) ^ poly
            } else {
                value << 1
            };
            value &= mask;
        }
    }
    value as u32
}
pub(crate) fn named_id(name: &str) -> u64 {
    ((crc(name, 32, 0x04c11db7, 0xffffffff) as u64) << 32)
        | (0xff000000 | crc(name, 24, 0x1864cfb, 0xb704ce)) as u64
}
pub(crate) fn write(p: DBPFFile, nodes: &mut [Node], out: &Path) -> Result<()> {
    write_impl(p, nodes, out, None)
}
pub(crate) fn write_output(
    p: DBPFFile,
    nodes: &mut [Node],
    out: &Path,
    enabled: bool,
) -> Result<()> {
    write_impl(p, nodes, out, Some(enabled))
}
fn write_impl(
    mut p: DBPFFile,
    nodes: &mut [Node],
    out: &Path,
    compression: Option<bool>,
) -> Result<()> {
    let mut reader = Cursor::new(Vec::<u8>::new());
    p.index.clear();
    p.hole_index.clear();
    // Consistent timestamps make a persisted job reproducible.
    p.header.created = dbpf::Timestamp(0);
    p.header.modified = dbpf::Timestamp(0);
    // Full scenegraph hashes need the 64-bit index and 3IDR representation.
    p.header.index_minor_version = dbpf::IndexMinorVersion::V2;
    for n in nodes {
        n.entry.data(&mut reader)?.decompressed()?.data = n.bytes.clone();
        p.index.push(n.entry.clone());
    }
    let bytes = if let Some(enabled) = compression {
        crate::package_compression::serialize(&mut p, &mut reader, enabled)?
    } else {
        let mut w = Cursor::new(Vec::new());
        p.write(&mut w, &mut reader)?;
        w.into_inner()
    };
    fs::write(out, bytes)?;
    Ok(())
}
pub(crate) fn lifo(node: &Node) -> Result<(String, u32, u32, Vec<u8>)> {
    let mut r = Cursor::new(&node.bytes);
    ensure!(
        u32::read_le(&mut r)? == 0xffff0001,
        "Unsupported game LIFO header"
    );
    ensure!(
        u32::read_le(&mut r)? == 0,
        "Game LIFO has unexpected file links"
    );
    ensure!(
        u32::read_le(&mut r)? == 1 && u32::read_le(&mut r)? == LIFO,
        "Invalid game LIFO index"
    );
    ensure!(
        BigString::read_le(&mut r)?.to_string() == "cLevelInfo",
        "Invalid game LIFO block"
    );
    ensure!(
        u32::read_le(&mut r)? == LIFO && u32::read_le(&mut r)? == 9,
        "Unsupported game LIFO version"
    );
    let name = FileName::read_le(&mut r)?.name.to_string();
    let (width, height) = (u32::read_le(&mut r)?, u32::read_le(&mut r)?);
    let _pitch = u32::read_le(&mut r)?;
    let length = u32::read_le(&mut r)? as usize;
    ensure!(
        length <= 16 * 1024 * 1024 && r.position() as usize + length == node.bytes.len(),
        "Invalid game LIFO size"
    );
    let mut data = vec![0; length];
    r.read_exact(&mut data)?;
    Ok((name, width, height, data))
}
