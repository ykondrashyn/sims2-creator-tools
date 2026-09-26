//! Offline wrapper for the same painting operations used in the browser.
use anyhow::{Context, Result};
use std::{
    io::{self, Read},
    path::Path,
};
use ts2_package_builder::{
    assets::{self, Assets},
    painting,
};
fn main() -> Result<()> {
    let mut text = String::new();
    io::stdin().read_to_string(&mut text)?;
    let r: serde_json::Value = serde_json::from_str(&text)?;
    let mut a = Assets::default();
    for (k, p) in r["assets"].as_object().context("Missing assets")? {
        a.bytes.insert(
            k.into(),
            std::fs::read(p.as_str().context("Invalid asset path")?)?,
        );
    }
    let result = assets::with(&mut a, || {
        painting::dispatch(r["op"].as_str().context("Missing operation")?, &r["params"])
    })?;
    if let Some(path) = r["output"].as_str() {
        std::fs::write(
            path,
            a.bytes
                .get(Path::new(r["output_asset"].as_str().unwrap_or("output")))
                .context("Missing output")?,
        )?;
    }
    println!("{}", serde_json::to_string(&result)?);
    Ok(())
}
