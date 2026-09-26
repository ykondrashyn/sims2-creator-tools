//! Deterministic sRGB curve processing shared by native and WASM builds.
use anyhow::{bail, ensure, Context, Result};
use image::{RgbImage, RgbaImage};
use serde_json::{json, Value};
pub const CHANNELS: [&str; 4] = ["value", "red", "green", "blue"];
pub type Tables = Vec<Vec<u8>>;
fn numeric(v: &Value, min: f64, max: f64) -> Result<f64> {
    let n = v.as_f64().context("Curve values must be numbers")?;
    ensure!(
        n.is_finite() && n >= min && n <= max,
        "Curve value is out of range"
    );
    Ok(n)
}
pub fn tables(value: &Value) -> Result<Tables> {
    let a = value.as_array().context("RGB mappings must be arrays")?;
    ensure!(a.len() == 3, "Three RGB mappings are required");
    a.iter()
        .map(|v| {
            let a = v.as_array().context("Invalid RGB mapping")?;
            ensure!(a.len() == 256, "Mappings need 256 samples");
            a.iter()
                .map(|x| {
                    let n = x.as_u64().context("Mapping samples must be bytes")?;
                    ensure!(n <= 255, "Mapping sample exceeds 255");
                    Ok(n as u8)
                })
                .collect()
        })
        .collect()
}
pub fn normalize(curve: &Value) -> Result<Value> {
    let origin = curve["source"]
        .as_str()
        .context("Choose GIMP samples or editor points")?;
    ensure!(
        ["gimp", "editor"].contains(&origin),
        "Choose GIMP samples or editor points"
    );
    let field = if origin == "gimp" {
        "channels"
    } else {
        "points"
    };
    let obj = curve.as_object().context("Invalid curve definition")?;
    ensure!(
        obj.keys()
            .all(|k| ["source", field, "filename"].contains(&k.as_str())),
        "Unsupported curve field"
    );
    let channels = curve[field]
        .as_object()
        .context("Supply all four curve channels")?;
    ensure!(
        channels.len() == 4 && CHANNELS.iter().all(|c| channels.contains_key(*c)),
        "Supply Value, Red, Green and Blue channels"
    );
    let mut samples = Vec::new();
    for c in CHANNELS {
        let data = channels[c].as_array().context("Invalid curve channel")?;
        let values = if origin == "gimp" {
            ensure!(data.len() == 256, "Each channel needs 256 samples");
            data.iter()
                .map(|v| numeric(v, 0., 1.))
                .collect::<Result<Vec<_>>>()?
        } else {
            ensure!(
                (2..=32).contains(&data.len()),
                "Use 2 through 32 control points per channel"
            );
            let points = data
                .iter()
                .map(|p| {
                    let p = p.as_array().context("Each point needs input and output")?;
                    ensure!(p.len() == 2, "Each point needs input and output");
                    Ok((numeric(&p[0], 0., 255.)?, numeric(&p[1], 0., 255.)?))
                })
                .collect::<Result<Vec<_>>>()?;
            ensure!(
                points[0].0 == 0.
                    && points.last().unwrap().0 == 255.
                    && points.windows(2).all(|p| p[0].0 < p[1].0),
                "Curve inputs must increase from 0 to 255 without duplicates"
            );
            let mut segment = 0;
            (0..256)
                .map(|x| {
                    let x = x as f64;
                    while segment < points.len() - 2 && x > points[segment + 1].0 {
                        segment += 1;
                    }
                    let (a, b) = points[segment];
                    let (c, d) = points[segment + 1];
                    (b + (d - b) * (x - a) / (c - a)) / 255.
                })
                .collect()
        };
        samples.push(values);
    }
    let filename = curve.get("filename").and_then(Value::as_str).unwrap_or("");
    ensure!(
        filename.len() <= 640
            && filename.chars().count() <= 160
            && !filename.chars().any(|c| c < ' '),
        "Invalid curve source filename"
    );
    let rgb: Tables = samples[1..]
        .iter()
        .map(|channel| {
            channel
                .iter()
                .map(|v| {
                    let pos = v * 255.;
                    let i = (pos as usize).min(254);
                    let f = pos - i as f64;
                    ((samples[0][i] * (1. - f) + samples[0][i + 1] * f) * 255. + 0.5)
                        .floor()
                        .clamp(0., 255.) as u8
                })
                .collect()
        })
        .collect();
    Ok(json!({"curve":curve,"tables":rgb}))
}
#[derive(Debug)]
enum Form {
    Atom(String),
    List(Vec<Form>),
}
impl Form {
    fn atom(&self) -> Result<&str> {
        match self {
            Self::Atom(v) => Ok(v),
            _ => bail!("Malformed GIMP curve setting"),
        }
    }
    fn list(&self) -> Result<&[Form]> {
        match self {
            Self::List(v) => Ok(v),
            _ => bail!("Malformed GIMP curve setting"),
        }
    }
}
fn forms(text: &str) -> Result<Vec<Form>> {
    let mut stack: Vec<Vec<Form>> = vec![vec![]];
    let mut chars = text.chars().peekable();
    while let Some(c) = chars.next() {
        match c {
            '#' => {
                while chars.peek().is_some_and(|c| *c != '\n') {
                    chars.next();
                }
            }
            '(' => {
                ensure!(stack.len() <= 16, "GIMP curve nesting is too deep");
                stack.push(vec![])
            }
            ')' => {
                ensure!(stack.len() > 1, "Malformed GIMP curve parentheses");
                let form = Form::List(stack.pop().unwrap());
                stack.last_mut().unwrap().push(form);
            }
            c if c.is_whitespace() => {}
            '"' => {
                let mut s = String::new();
                let mut end = false;
                while let Some(c) = chars.next() {
                    if c == '"' {
                        end = true;
                        break;
                    }
                    if c == '\\' {
                        s.push(chars.next().context("Malformed GIMP string")?);
                    } else {
                        s.push(c);
                    }
                }
                ensure!(end, "Malformed GIMP string");
                stack.last_mut().unwrap().push(Form::Atom(s));
            }
            c => {
                let mut s = c.to_string();
                while chars
                    .peek()
                    .is_some_and(|c| !c.is_whitespace() && *c != '(' && *c != ')')
                {
                    s.push(chars.next().unwrap());
                }
                stack.last_mut().unwrap().push(Form::Atom(s));
            }
        }
    }
    ensure!(stack.len() == 1, "Malformed GIMP curve parentheses");
    Ok(stack.pop().unwrap())
}
pub fn parse_gimp(data: &[u8], filename: &str) -> Result<Value> {
    ensure!(data.len() <= 256 * 1024, "Curve file exceeds 256 KiB");
    let source = std::str::from_utf8(data)
        .context("Import a GIMP text curve with 256 samples per channel")?
        .trim_start_matches('\u{feff}');
    ensure!(
        source.starts_with("# GIMP curves tool settings"),
        "Import a GIMP text curve preset with 256 samples per channel"
    );
    let mut channels = serde_json::Map::new();
    let mut active: Option<String> = None;
    for form in forms(source)? {
        let form = form.list()?;
        ensure!(!form.is_empty(), "Malformed GIMP setting");
        let key = form[0].atom()?;
        match key {
            "trc" | "linear" => {
                ensure!(form.len() == 2, "Malformed GIMP processing mode");
                let mode = form[1].atom()?;
                ensure!(
                    if key == "trc" {
                        ["non-linear", "perceptual"].contains(&mode)
                    } else {
                        ["no", "false"].contains(&mode)
                    },
                    "Linear-light curves are unsupported. Export a perceptual sRGB GIMP curve"
                );
            }
            "time" | "name" => {
                ensure!(form.len() == 2, "Malformed GIMP metadata");
                form[1].atom()?;
            }
            "channel" => {
                ensure!(
                    form.len() == 2 && active.is_none(),
                    "Each GIMP channel must appear exactly once"
                );
                let c = form[1].atom()?;
                ensure!(
                    ["value", "red", "green", "blue", "alpha"].contains(&c)
                        && !channels.contains_key(c),
                    "Each GIMP channel must appear exactly once"
                );
                active = Some(c.into());
            }
            "curve" => {
                let channel = active.take().context("GIMP curve has no channel")?;
                let mut fields = std::collections::BTreeMap::new();
                for f in &form[1..] {
                    let f = f.list()?;
                    ensure!(!f.is_empty(), "Malformed GIMP curve setting");
                    let k = f[0].atom()?;
                    ensure!(
                        [
                            "curve-type",
                            "n-points",
                            "points",
                            "point-types",
                            "n-samples",
                            "samples"
                        ]
                        .contains(&k)
                            && !fields.contains_key(k),
                        "Unsupported or duplicate GIMP curve setting"
                    );
                    fields.insert(k, &f[1..]);
                }
                let get = |key| -> Result<&[Form]> {
                    fields
                        .get(key)
                        .copied()
                        .context("GIMP curve needs 256 samples per channel")
                };
                let count = get("n-samples")?;
                let kind = get("curve-type")?;
                ensure!(
                    count.len() == 1
                        && count[0].atom()? == "256"
                        && kind.len() == 1
                        && ["smooth", "free"].contains(&kind[0].atom()?),
                    "GIMP curve needs 256 samples per channel"
                );
                let values = get("samples")?;
                ensure!(
                    values.len() == 257 && values[0].atom()? == "256",
                    "Each GIMP channel needs exactly 256 samples"
                );
                let values = values[1..]
                    .iter()
                    .map(|v| {
                        let n = v.atom()?.parse::<f64>().context("Invalid GIMP sample")?;
                        ensure!(
                            n.is_finite() && (0.0..=1.).contains(&n),
                            "GIMP samples must be finite numbers between 0 and 1"
                        );
                        Ok(n)
                    })
                    .collect::<Result<Vec<_>>>()?;
                channels.insert(channel, json!(values));
            }
            _ => bail!("Unsupported GIMP curve setting"),
        }
    }
    ensure!(
        active.is_none() && channels.len() == 5,
        "A GIMP preset must include Value, Red, Green, Blue and Alpha"
    );
    let alpha = channels.remove("alpha").context("Missing Alpha channel")?;
    ensure!(alpha.as_array().unwrap().iter().enumerate().all(|(i,v)|(v.as_f64().unwrap()-i as f64/255.).abs()<=0.00000051),"This curve changes transparency. Reset its Alpha channel to identity in GIMP and export again");
    let filename = filename
        .replace('\\', "/")
        .rsplit('/')
        .next()
        .unwrap_or("")
        .chars()
        .take(160)
        .collect::<String>();
    let curve = json!({"source":"gimp","filename":filename,"channels":channels});
    normalize(&curve)
}
pub fn settings(v: &Value) -> Result<Value> {
    let mut result = json!({"base":"Volatile","black":0,"white":255,"gamma":1.0,"png_alpha":false});
    for (k, v) in v.as_object().context("Invalid preparation settings")? {
        ensure!(
            ["base", "black", "white", "gamma", "png_alpha"].contains(&k.as_str()),
            "Invalid preparation setting"
        );
        result[k] = v.clone();
    }
    ensure!(
        [
            "Volatile",
            "Primer",
            "Grenade",
            "Incendiary",
            "Pooklet Grey",
            "Arbitrary texture"
        ]
        .contains(&result["base"].as_str().unwrap_or("")),
        "Select a supported input base"
    );
    let low = numeric(&result["black"], 0., 255.)?;
    let high = numeric(&result["white"], 0., 255.)?;
    numeric(&result["gamma"], 0.1, 5.)?;
    ensure!(
        low < high && low.fract() == 0. && high.fract() == 0. && result["png_alpha"].is_boolean(),
        "Use whole-number black and white points within 0 to 255, black below white, and gamma within 0.1 to 5"
    );
    Ok(result)
}
pub fn apply(image: &RgbaImage, tables: &Tables) -> RgbaImage {
    let mut out = image.clone();
    for p in out.pixels_mut() {
        for c in 0..3 {
            p[c] = tables[c][p[c] as usize];
        }
    }
    out
}
pub fn prepare(image: &RgbaImage, config: &Value, mappings: &Value) -> Result<RgbaImage> {
    let c = settings(config)?;
    let name = c["base"].as_str().unwrap();
    if name == "Arbitrary texture" {
        let (low, high, gamma) = (
            c["black"].as_f64().unwrap(),
            c["white"].as_f64().unwrap(),
            c["gamma"].as_f64().unwrap(),
        );
        let levels: Vec<u8> = (0..256)
            .map(|i| {
                (255.
                    * ((i as f64 - low) / (high - low))
                        .clamp(0., 1.)
                        .powf(1. / gamma))
                .round_ties_even() as u8
            })
            .collect();
        let mut grey = image.clone();
        for p in grey.pixels_mut() {
            // Match the pinned Pillow matrix path, including f32 fused rounding.
            // Explicit mul_add keeps the same result on native ARM and WASM.
            let rg = 0.2126_f32.mul_add(p[0] as f32, 0.7152_f32 * p[1] as f32);
            let l = (0.0722_f32.mul_add(p[2] as f32, rg) + 0.5).clamp(0., 255.) as usize;
            let v = levels[l];
            p.0 = [v, v, v, p[3]];
        }
        Ok(apply(&grey, &tables(&mappings["base:arbitrary-grey"])?))
    } else {
        let key = if name == "Volatile" {
            name.into()
        } else {
            format!("base:{}", name.replace("Pooklet ", ""))
        };
        Ok(apply(image, &tables(&mappings[key])?))
    }
}
pub fn png(image: &RgbaImage) -> Result<Vec<u8>> {
    let mut out = std::io::Cursor::new(Vec::new());
    image.write_to(&mut out, image::ImageFormat::Png)?;
    Ok(out.into_inner())
}
pub fn thumb(image: &RgbaImage) -> Result<Vec<u8>> {
    png(&image::imageops::thumbnail(image, 160, 160))
}
#[allow(dead_code)]
pub fn rgb(image: &RgbaImage) -> RgbImage {
    image::DynamicImage::ImageRgba8(image.clone()).into_rgb8()
}
