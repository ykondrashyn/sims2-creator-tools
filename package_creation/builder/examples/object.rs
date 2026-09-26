//! Offline adapter for template verification and native/browser parity.
use anyhow::{Context, Result};
use std::{
    io::{self, Read},
    path::Path,
};
use ts2_package_builder::{
    assets::{self, Assets},
    object,
};
fn main() -> Result<()> {
    let mut text = String::new();
    io::stdin().read_to_string(&mut text)?;
    let r: serde_json::Value = serde_json::from_str(&text)?;
    let mut a = Assets::default();
    for (key, path) in r["assets"].as_object().context("Missing assets")? {
        a.bytes.insert(
            key.into(),
            std::fs::read(path.as_str().context("Invalid asset path")?)?,
        );
    }
    let result = assets::with(&mut a, || {
        object::dispatch(r["op"].as_str().context("Missing operation")?, &r["params"])
    })?;
    if let Some(out) = r["output"].as_str() {
        let key = r["output_asset"].as_str().unwrap_or("output");
        std::fs::write(out, a.bytes.get(Path::new(key)).context("No output")?)?;
    }
    println!("{}", serde_json::to_string(&result)?);
    Ok(())
}
