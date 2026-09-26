//! Native oracle for the identical browser protocol, used by parity tests.
use anyhow::{Context, Result};
use ts2_package_builder::engine::BrowserEngine;
fn main() -> Result<()> {
    let args: Vec<_> = std::env::args().collect();
    let plan: serde_json::Value = serde_json::from_slice(&std::fs::read(
        args.get(1).context("Provide a request plan")?,
    )?)?;
    let out = std::path::Path::new(args.get(2).context("Provide an output directory")?);
    std::fs::create_dir_all(out)?;
    let mut e = BrowserEngine::new();
    for (k, v) in plan["assets"].as_object().unwrap() {
        e.put_asset(k, std::fs::read(v.as_str().unwrap())?)
            .map_err(anyhow::Error::msg)?;
    }
    let mut results = vec![];
    for r in plan["requests"].as_array().unwrap() {
        let v = e.call(&r.to_string()).map_err(anyhow::Error::msg)?;
        results.push(serde_json::from_str::<serde_json::Value>(&v)?);
        if let Some(name) = r["output"].as_str() {
            std::fs::write(out.join(name), e.take_asset("output"))?;
        }
    }
    std::fs::write(
        out.join("results.json"),
        serde_json::to_vec_pretty(&results)?,
    )?;
    Ok(())
}
