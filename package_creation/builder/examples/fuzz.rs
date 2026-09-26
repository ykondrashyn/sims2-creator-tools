//! Bounded seeded mutation tests. No external or licensed fixtures are needed.
use anyhow::{ensure, Result};
use std::io::{Cursor, Write};
use ts2_package_builder::{color, engine::BrowserEngine};
fn random(state: &mut u64) -> u64 {
    *state ^= *state << 13;
    *state ^= *state >> 7;
    *state ^= *state << 17;
    *state
}
fn main() -> Result<()> {
    let iterations = std::env::args()
        .nth(1)
        .unwrap_or_else(|| "200".into())
        .parse::<usize>()?;
    ensure!(
        iterations <= 100_000,
        "Fuzz run exceeds the bounded iteration limit"
    );
    let mut package = vec![0; 96];
    package[..4].copy_from_slice(b"DBPF");
    package[4] = 1;
    package[8] = 1;
    package[32] = 7;
    let image = color::png(&image::RgbaImage::from_pixel(
        4,
        4,
        image::Rgba([90, 127, 255, 255]),
    ))?;
    let mut zip = zip::ZipWriter::new(Cursor::new(Vec::new()));
    zip.start_file("scene.gltf", zip::write::SimpleFileOptions::default())?;
    zip.write_all(br#"{"asset":{"version":"2.0"},"scenes":[{"nodes":[]}],"scene":0}"#)?;
    let zip = zip.finish()?.into_inner();
    let curves=b"# GIMP curves tool settings\n(time 0)\n(linear no)\n(channel value)\n(curve (curve-type smooth) (n-samples 256))".to_vec();
    let mut seed = 0x53494d535f46555a_u64;
    let cases = [
        ("inventory", "asset", "input.package", package),
        ("sim_inspect_model", "model", "input.zip", zip),
        ("parse_curve", "asset", "input.curve", curves),
        ("painting_inspect_image", "input", "input.png", image),
    ];
    for (op, key, name, source) in cases {
        for iteration in 0..iterations {
            let mut bytes = source.clone();
            if iteration > 0 {
                for _ in 0..1 + random(&mut seed) % 8 {
                    let index = random(&mut seed) as usize % bytes.len();
                    bytes[index] = random(&mut seed) as u8;
                }
            }
            if iteration % 7 == 1 {
                bytes.truncate(random(&mut seed) as usize % bytes.len());
            }
            let mut engine = BrowserEngine::new();
            engine.put_asset(name, bytes).map_err(anyhow::Error::msg)?;
            let _ = engine
                .call(&serde_json::json!({"version":1,"op":op,"params":{key:name}}).to_string());
        }
        println!("{op}: {iterations} bounded cases completed");
    }
    Ok(())
}
