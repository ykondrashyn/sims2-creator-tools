//! Offline native comparison wrapper for the browser conversion engine.
use anyhow::{ensure, Result};
use ts2_package_builder::conversion::Conversion;
fn main() -> Result<()> {
    let args: Vec<String> = std::env::args().collect();
    ensure!(
        args.len() == 5,
        "Usage: conversion am|af mapping.zip input.png output.png"
    );
    let mut state = Conversion::new(
        &std::fs::read(&args[2])?,
        &std::fs::read(&args[3])?,
        &args[1],
    )?;
    if let Ok(path) = std::env::var("CONVERSION_DEBUG_INPUT") {
        let bytes: Vec<u8> = state
            .prepared_input()
            .iter()
            .flat_map(|v| v.to_le_bytes())
            .collect();
        std::fs::write(path, bytes)?;
    }
    while state.step(8192)?["done"] != true {}
    let (png, report) = state.finish()?;
    std::fs::write(&args[4], png)?;
    println!("{report}");
    Ok(())
}
