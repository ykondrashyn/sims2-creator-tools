//! Native wrapper for deterministic texture codec acceptance tests.
use anyhow::{Context, Result};
use dbpf::internal_file::resource_collection::texture_resource::TextureFormat;
use ts2_package_builder::texture_encoding::{encode, Encoder};
fn main() -> Result<()> {
    let a: Vec<_> = std::env::args().collect();
    anyhow::ensure!(
        a.len() == 7,
        "Provide encoder, format, width, height, RGBA input and output path"
    );
    let encoder: Encoder = serde_json::from_value(serde_json::json!(a[1]))?;
    let format = match a[2].as_str() {
        "DXT1" => TextureFormat::DXT1,
        "DXT3" => TextureFormat::DXT3,
        "DXT5" => TextureFormat::DXT5,
        _ => anyhow::bail!("Unsupported texture format"),
    };
    let bytes = std::fs::read(&a[5]).context("Read RGBA input")?;
    let result = encode(format, a[3].parse()?, a[4].parse()?, &bytes, encoder)?;
    std::fs::write(&a[6], result)?;
    Ok(())
}
