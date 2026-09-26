//! Lossless storage policy for finished packages, independent of image encoding.
use anyhow::{ensure, Context, Result};
use binrw::BinRead;
use dbpf::{CompressionType, DBPFFile, IndexMinorVersion};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{collections::BTreeMap, io::Cursor};

const DIR: u32 = 0xe86b1eef;
pub fn default_enabled() -> bool {
    true
}
pub fn from_job(job: &Value) -> Result<bool> {
    match job.get("refpack_compression") {
        None => Ok(true),
        Some(value) => value
            .as_bool()
            .context("RefPack package compression must be enabled or disabled"),
    }
}

/// Select storage per resource, then check the serialized container with the DBPF
/// reader. Hashes cover all raw resource bytes, including unknown metadata and
/// RGB under transparent pixels. No texture decoding or re-encoding occurs here.
pub fn serialize(
    package: &mut DBPFFile,
    reader: &mut Cursor<Vec<u8>>,
    enabled: bool,
) -> Result<Vec<u8>> {
    let dir_bytes = if package.header.index_minor_version == IndexMinorVersion::V2 {
        20
    } else {
        16
    };
    let mut expected = BTreeMap::new();
    let mut saving = 0usize;
    for entry in &mut package.index {
        if entry.type_id.code() == DIR {
            continue;
        }
        let key = (entry.type_id.code(), entry.group_id, entry.instance_id.id);
        let previous_type = entry.compression;
        let data = entry.data(reader)?;
        let previous = if enabled && previous_type == CompressionType::RefPack {
            data.cached_compressed()
                .filter(|p| p.compression_type == CompressionType::RefPack)
                .cloned()
        } else {
            None
        };
        let raw = &data.decompressed()?.data;
        let size = raw.len();
        ensure!(
            expected.insert(key, Sha256::digest(raw).to_vec()).is_none(),
            "Duplicate package resource identity"
        );
        // The Sims 2 Maxis RefPack header has a 24-bit expanded-size field.
        // Empty and larger resources retain their uncompressed representation.
        let selected = if enabled && size > 0 && size < 0x1000000 {
            let candidate = data.compressed(CompressionType::RefPack)?;
            if let Some(previous) = previous {
                if previous.data.len() < candidate.data.len() {
                    *candidate = previous;
                }
            }
            let cost = candidate.data.len() + dir_bytes;
            if cost < size {
                saving += size - cost;
                CompressionType::RefPack
            } else {
                CompressionType::Uncompressed
            }
        } else {
            CompressionType::Uncompressed
        };
        data.compressed(selected)?;
        entry.compression = selected;
    }
    // A new compression directory also needs one ordinary index entry.
    if saving <= dir_bytes + 4 {
        for entry in &mut package.index {
            entry.compression = CompressionType::Uncompressed;
        }
    }
    let mut writer = Cursor::new(Vec::new());
    package
        .write(&mut writer, reader)
        .context("Serialize package with lossless compression")?;
    let output = writer.into_inner();
    let mut input = Cursor::new(output);
    let mut reopened = DBPFFile::read(&mut input)?;
    let mut actual = BTreeMap::new();
    for entry in &mut reopened.index {
        if entry.type_id.code() == DIR {
            continue;
        }
        let key = (entry.type_id.code(), entry.group_id, entry.instance_id.id);
        let raw = &entry.data(&mut input)?.decompressed()?.data;
        ensure!(
            actual.insert(key, Sha256::digest(raw).to_vec()).is_none(),
            "Duplicate saved package resource identity"
        );
    }
    ensure!(
        actual == expected,
        "Lossless package compression changed resource data"
    );
    Ok(input.into_inner())
}

pub fn report(path: &str, enabled: bool) -> Result<Value> {
    let mut reader = Cursor::new(crate::assets::read(path)?);
    let mut package = DBPFFile::read(&mut reader)?;
    let (mut count, mut compressed, mut raw, mut stored) = (0usize, 0usize, 0usize, 0usize);
    for entry in &mut package.index {
        if entry.type_id.code() == DIR {
            continue;
        }
        let kind = entry.compression;
        let data = entry.data(&mut reader)?.compressed(kind)?;
        count += 1;
        compressed += usize::from(kind == CompressionType::RefPack);
        stored += data.data.len();
        raw += if kind == CompressionType::RefPack {
            data.decompressed_size as usize
        } else {
            data.data.len()
        };
    }
    ensure!(
        enabled || compressed == 0,
        "RefPack was disabled but the package contains compressed resources"
    );
    Ok(
        json!({"version":1,"refpack_enabled":enabled,"algorithm":"refpack-5.0.4-optimal",
        "resources":count,"compressed_resources":compressed,"raw_resource_bytes":raw,"stored_resource_bytes":stored,"lossless":true}),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture(bytes: Vec<u8>) -> (DBPFFile, Cursor<Vec<u8>>) {
        // A single unknown resource in a DBPF 1.1 container. This generic
        // storage test must compile without any licensed game templates.
        let mut source = vec![0u8; 120];
        source[..4].copy_from_slice(b"DBPF");
        for (offset, value) in [
            (4, 1u32),
            (8, 1),
            (32, 7),
            (36, 1),
            (40, 96),
            (44, 24),
            (60, 2),
            (96, 0x42424242),
            (100, 1),
            (104, 1),
            (112, 120),
            (116, bytes.len() as u32),
        ] {
            source[offset..offset + 4].copy_from_slice(&value.to_le_bytes());
        }
        source.extend_from_slice(&bytes);
        let mut reader = Cursor::new(source);
        let package = DBPFFile::read(&mut reader).unwrap();
        (package, reader)
    }
    #[test]
    fn defaults_and_invalid_values() {
        assert!(from_job(&json!({})).unwrap());
        assert!(!from_job(&json!({"refpack_compression":false})).unwrap());
        for value in [json!(null), json!("false"), json!(0)] {
            assert!(from_job(&json!({"refpack_compression":value})).is_err());
        }
    }
    #[test]
    fn lossless_adaptive_storage_and_deterministic_retry() {
        for bytes in [vec![], vec![9; 3], (0..=255).collect(), vec![42; 65536]] {
            let (mut package, mut reader) = fixture(bytes.clone());
            let packed = serialize(&mut package, &mut reader, true).unwrap();
            let mut input = Cursor::new(packed.clone());
            let mut reopened = DBPFFile::read(&mut input).unwrap();
            assert_eq!(
                reopened.index[0].compression == CompressionType::RefPack,
                bytes.len() == 65536
            );
            assert_eq!(serialize(&mut reopened, &mut input, true).unwrap(), packed);
            let (mut package, mut reader) = fixture(bytes);
            let unpacked = serialize(&mut package, &mut reader, false).unwrap();
            assert!(packed.len() <= unpacked.len());
            let mut input = Cursor::new(unpacked);
            let reopened = DBPFFile::read(&mut input).unwrap();
            assert!(reopened
                .index
                .iter()
                .all(|e| e.compression == CompressionType::Uncompressed));
        }
    }
}
