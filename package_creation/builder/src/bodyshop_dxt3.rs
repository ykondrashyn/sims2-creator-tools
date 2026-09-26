//! Recovered nondithered Body Shop BC1/BC2/BC3 arithmetic. Specifications:
//! RGB bf76b10f0fdbdf3149b1110768d64b019b0a758b0bcf7b306e6adbfd11400b25,
//! alpha 5335c247a31742fc2447ddde5b91e22251f8e0f3943ff7724afc290855467e18.
//! Each helper represents an explicit x87 binary32 rounding boundary. Keep the
//! evaluation order and stored reciprocals, including RGB under zero alpha.
use anyhow::{ensure, Result};
use serde_json::{json, Value};

type V = [f64; 3];
fn bits(x: u32) -> f64 {
    f32::from_bits(x) as f64
}
fn n(x: f64) -> f64 {
    (x as f32) as f64
}
fn z(x: f64) -> f64 {
    let y = x as f32;
    if (y as f64).abs() > x.abs() {
        f32::from_bits(y.to_bits() - 1) as f64
    } else {
        y as f64
    }
}
fn norm(v: V, q: fn(f64) -> f64) -> f64 {
    q(q(q(v[2] * v[2]) + q(v[1] * v[1])) + q(v[0] * v[0]))
}
fn pack(v: V) -> u16 {
    let s = [31., 63., 31.];
    let p: [u16; 3] = std::array::from_fn(|j| z(z(v[j].clamp(0., 1.) * s[j]) + 0.5) as u16);
    (p[0] << 11) | (p[1] << 5) | p[2]
}
fn unpack(v: u16) -> V {
    [
        n((v >> 11) as f64 * n(1. / 31.)),
        n(((v >> 5) & 63) as f64 * n(1. / 63.)),
        n((v & 31) as f64 * n(1. / 31.)),
    ]
}

fn optimize<const TRACE: bool>(
    points: &[V; 16],
    w: V,
    count: usize,
    trace: &mut Value,
) -> Result<(V, V)> {
    let mut x: V = std::array::from_fn(|j| points.iter().fold(w[j], |a, p| a.min(p[j])));
    let mut y: V = std::array::from_fn(|j| points.iter().fold(0f64, |a, p| a.max(p[j])));
    if TRACE {
        trace["bounds"] = json!([x, y]);
    }
    let delta = std::array::from_fn(|j| n(y[j] - x[j]));
    let length = norm(delta, n);
    if length < bits(0x00800000) {
        return Ok((x, y));
    }
    let inv = n(1. / length);
    let direction: V = delta.map(|v| n(v * inv));
    let mid: V = std::array::from_fn(|j| n(n(x[j] + y[j]) * 0.5));
    let mut scores = [0.; 4];
    for p in points {
        let [r, g, b]: V = std::array::from_fn(|j| n(n(p[j] - mid[j]) * direction[j]));
        let projections = [
            n(n(g + b) + r),
            n(n(r + g) - b),
            n(n(r - g) + b),
            n(n(r - g) - b),
        ];
        for k in 0..4 {
            scores[k] = n(scores[k] + n(projections[k] * projections[k]));
        }
    }
    let mut axis = 0;
    for k in 1..4 {
        if scores[k] > scores[axis] {
            axis = k;
        }
    }
    if axis & 2 != 0 {
        std::mem::swap(&mut x[1], &mut y[1]);
    }
    if axis & 1 != 0 {
        std::mem::swap(&mut x[2], &mut y[2]);
    }
    if TRACE {
        trace["axis_scores"] = json!(scores);
        trace["axis"] = json!(axis);
        trace["initial_endpoints"] = json!([x, y]);
        trace["iterations"] = json!([]);
    }
    if length < 1. / 4096. {
        return Ok((x, y));
    }
    let c = if count == 3 {
        [1., 0.5, 0., 0.]
    } else {
        [1., n(2. / 3.), n(1. / 3.), 0.]
    };
    let d = if count == 3 {
        [0., 0.5, 1., 0.]
    } else {
        [0., n(1. / 3.), n(2. / 3.), 1.]
    };
    let last = (count - 1) as f64;
    for iteration in 0..8 {
        let mut assignments = [0; 16];
        let steps: [V; 4] =
            std::array::from_fn(|k| std::array::from_fn(|j| z(z(x[j] * c[k]) + z(y[j] * d[k]))));
        let delta = std::array::from_fn(|j| z(y[j] - x[j]));
        let length = norm(delta, z);
        if length < 1. / 4096. {
            break;
        }
        let scale = z(last / length);
        let direction: V = delta.map(|v| z(v * scale));
        let mut dx = [0.; 3];
        let mut dy = [0.; 3];
        let (mut xx, mut yy) = (0., 0.);
        for (i, p) in points.iter().enumerate() {
            let products: V = std::array::from_fn(|j| z(z(p[j] - x[j]) * direction[j]));
            let t = z(z(products[1] + products[0]) + products[2]);
            let k = if t >= last {
                count as i32 - 1
            } else {
                z(t + 0.5) as i32
            };
            ensure!(
                (0..count as i32).contains(&k),
                "Body Shop optimizer encountered an unsupported block"
            );
            let k = k as usize;
            if TRACE {
                assignments[i] = k;
            }
            let error: V = std::array::from_fn(|j| z(steps[k][j] - p[j]));
            let (cc, dd) = (z(c[k] * 0.125), z(d[k] * 0.125));
            xx = z(xx + z(cc * c[k]));
            yy = z(yy + z(dd * d[k]));
            for j in 0..3 {
                dx[j] = z(dx[j] + z(cc * error[j]));
                dy[j] = z(dy[j] + z(dd * error[j]));
            }
        }
        if xx > 0. {
            let factor = z(-1. / xx);
            x = std::array::from_fn(|j| z(z(dx[j] * factor) + x[j]));
        }
        if yy > 0. {
            let factor = z(-1. / yy);
            y = std::array::from_fn(|j| z(z(dy[j] * factor) + y[j]));
        }
        if TRACE {
            trace["iterations"].as_array_mut().unwrap().push(json!({"number":iteration,"assignments":assignments,"dx":dx,"dy":dy,"xx":xx,"yy":yy,"X":x,"Y":y}));
        }
        if dx.iter().chain(&dy).all(|v| z(v * v) < 1. / 65536.) {
            break;
        }
    }
    ensure!(
        x.iter().chain(&y).all(|v| v.is_finite()),
        "Body Shop optimizer produced invalid endpoints"
    );
    Ok((x, y))
}

fn rgb<const TRACE: bool>(
    pixels: &[V; 16],
    transparent: &[bool; 16],
    trace: &mut Value,
) -> Result<[u8; 8]> {
    if transparent.iter().all(|v| *v) {
        return Ok([0, 0, 255, 255, 255, 255, 255, 255]);
    }
    let count = if transparent.iter().any(|v| *v) { 3 } else { 4 };
    let last = (count - 1) as f64;
    let w = [bits(0x3e981530), 1., bits(0x3dce6734)];
    let wi = [bits(0x4057762e), 1., bits(0x411ec1dd)];
    let reciprocal = [n(1. / 31.), n(1. / 63.), n(1. / 31.)];
    let points: [V; 16] = std::array::from_fn(|i| {
        std::array::from_fn(|j| {
            let q =
                z((z(z(pixels[i][j] * [31., 63., 31.][j]) + 0.5) as i32) as f64 * reciprocal[j]);
            z(q * w[j])
        })
    });
    if TRACE {
        trace["fit_points"] = json!(points);
    }
    let (x, y) = optimize::<TRACE>(&points, w, count, trace)?;
    if TRACE {
        trace["fitted_endpoints"] = json!([x, y]);
    }
    let mut a = pack(std::array::from_fn(|j| n(x[j] * wi[j])));
    let mut b = pack(std::array::from_fn(|j| n(y[j] * wi[j])));
    if TRACE {
        trace["packed_before_sort"] = json!([a, b]);
    }
    let mut word = 0u32;
    let mut indices = [0; 16];
    if a != b || count == 3 {
        if (count == 3 && a > b) || (count == 4 && a < b) {
            std::mem::swap(&mut a, &mut b);
        }
        let aa: V = std::array::from_fn(|j| n(unpack(a)[j] * w[j]));
        let bb: V = std::array::from_fn(|j| n(unpack(b)[j] * w[j]));
        let delta: V = std::array::from_fn(|j| n(bb[j] - aa[j]));
        let scale = if a == b { 0. } else { n(last / norm(delta, n)) };
        let direction: V = delta.map(|v| n(v * scale));
        for (i, p) in pixels.iter().enumerate() {
            if transparent[i] {
                word |= 3 << (i * 2);
                indices[i] = 3;
                continue;
            }
            let products: V = std::array::from_fn(|j| z(z(z(p[j] * w[j]) - aa[j]) * direction[j]));
            let t = z(z(products[2] + products[1]) + products[0]);
            let k = if t <= 0. {
                0
            } else if t >= last {
                1
            } else if count == 3 {
                [0, 2, 1][z(t + 0.5) as usize]
            } else {
                [0, 2, 3, 1][z(t + 0.5) as usize]
            };
            word |= k << (i * 2);
            if TRACE {
                indices[i] = k;
            }
        }
    }
    if TRACE && a != b {
        trace["sorted_endpoints"] = json!([a, b]);
        trace["indices"] = json!(indices);
    }
    let mut output = [0; 8];
    output[..2].copy_from_slice(&a.to_le_bytes());
    output[2..4].copy_from_slice(&b.to_le_bytes());
    output[4..8].copy_from_slice(&word.to_le_bytes());
    Ok(output)
}

pub(crate) fn block(rgba: &[u8; 64]) -> Result<[u8; 16]> {
    let pixels: [V; 16] =
        std::array::from_fn(|i| std::array::from_fn(|j| n(rgba[i * 4 + j] as f64 * n(1. / 255.))));
    let encoded = rgb::<false>(&pixels, &[false; 16], &mut Value::Null)?;
    let mut output = [0; 16];
    for i in 0..8 {
        let lo = (rgba[i * 8 + 3] as u16 + 8) / 17;
        let hi = (rgba[i * 8 + 7] as u16 + 8) / 17;
        output[i] = (lo | hi << 4) as u8;
    }
    output[8..].copy_from_slice(&encoded);
    Ok(output)
}

pub(crate) fn block_dxt1(rgba: &[u8; 64]) -> Result<[u8; 8]> {
    let pixels: [V; 16] =
        std::array::from_fn(|i| std::array::from_fn(|j| n(rgba[i * 4 + j] as f64 * n(1. / 255.))));
    let transparent = std::array::from_fn(|i| rgba[i * 4 + 3] < 128);
    rgb::<false>(&pixels, &transparent, &mut Value::Null)
}

fn fit_alpha<const TRACE: bool>(points: &[f64; 16], count: usize, trace: &mut Value) -> (f64, f64) {
    let last = (count - 1) as f64;
    let c: [f64; 8] = std::array::from_fn(|k| n((last - k as f64) / last));
    let mut d: [f64; 8] = std::array::from_fn(|k| n(k as f64 / last));
    // This is the original executable's coefficient, not modern DirectXTex's 1.
    if count == 8 {
        d[7] = bits(0x3f924925);
    }
    let mut x = points
        .iter()
        .filter(|a| count == 8 || **a > 0.)
        .fold(1f64, |a, b| a.min(*b));
    let mut y = points
        .iter()
        .filter(|a| count == 8 || **a < 1.)
        .fold(0f64, |a, b| a.max(*b));
    if count == 6 && x == y {
        y = 1.;
    }
    if TRACE {
        trace["iterations"] = json!([]);
    }
    for _ in 0..8 {
        let length = z(y - x);
        if length < 1. / 256. {
            break;
        }
        let scale = z(last / length);
        let palette: [f64; 8] = std::array::from_fn(|k| z(z(y * d[k]) + z(x * c[k])));
        let (mut dx, mut dy, mut xx, mut yy) = (0., 0., 0., 0.);
        for a in points {
            let t = z(z(a - x) * scale);
            let k = if t <= 0. {
                if count == 6 && *a <= z(x * 0.5) {
                    count
                } else {
                    0
                }
            } else if t >= last {
                if count == 6 && *a >= z(z(y + 1.) * 0.5) {
                    count + 1
                } else {
                    count - 1
                }
            } else {
                z(t + 0.5) as usize
            };
            if k >= count {
                continue;
            }
            // Preserve the recovered error sign and update order intentionally.
            let error = z(a - palette[k]);
            dx = z(dx + z(error * c[k]));
            xx = z(xx + z(c[k] * c[k]));
            dy = z(dy + z(error * d[k]));
            yy = z(yy + z(d[k] * d[k]));
        }
        if xx > 0. {
            x = z(x - z(dx / xx));
        }
        if yy > 0. {
            y = z(y - z(dy / yy));
        }
        if x > y {
            std::mem::swap(&mut x, &mut y);
        }
        if TRACE {
            trace["iterations"]
                .as_array_mut()
                .unwrap()
                .push(json!({"X":x,"Y":y,"dx":dx,"dy":dy,"xx":xx,"yy":yy}));
        }
        if z(dx * dx) < 1. / 64. && z(dy * dy) < 1. / 64. {
            break;
        }
    }
    (x.clamp(0., 1.), y.clamp(0., 1.))
}

fn alpha5(alpha: &[f64; 16]) -> [u8; 8] {
    let inv = n(1. / 255.);
    let (mut low, mut high) = (alpha[0], alpha[0]);
    let points = alpha.map(|a| {
        let q = z((z(z(a * 255.) + 0.5) as i32) as f64 * inv);
        if q < low {
            low = q;
        } else if q > high {
            high = q;
        }
        q
    });
    if low == 1. {
        return [255, 255, 0, 0, 0, 0, 0, 0];
    }
    let count = if low == 0. || high == 1. { 6 } else { 8 };
    let (x, y) = fit_alpha::<false>(&points, count, &mut Value::Null);
    let (mut a, mut b) = (z(z(x * 255.) + 0.5) as u8, z(z(y * 255.) + 0.5) as u8);
    if count == 8 && a == b {
        return [a, b, 0, 0, 0, 0, 0, 0];
    }
    if count == 8 {
        std::mem::swap(&mut a, &mut b);
    }
    let (aa, bb) = (n(a as f64 * inv), n(b as f64 * inv));
    let last = (count - 1) as f64;
    let scale = if aa == bb { 0. } else { n(last / n(bb - aa)) };
    let mut word = 0u64;
    for (i, v) in alpha.iter().enumerate() {
        let t = z(z(v - aa) * scale);
        let k = if t <= 0. {
            if count == 6 && *v <= z(aa * 0.5) {
                6
            } else {
                0
            }
        } else if t >= last {
            if count == 6 && *v >= z(z(bb + 1.) * 0.5) {
                7
            } else {
                1
            }
        } else if count == 6 {
            [0, 2, 3, 4, 5, 1][z(t + 0.5) as usize]
        } else {
            [0, 2, 3, 4, 5, 6, 7, 1][z(t + 0.5) as usize]
        };
        word |= (k as u64) << (3 * i);
    }
    let mut output = [0; 8];
    output[..2].copy_from_slice(&[a, b]);
    output[2..].copy_from_slice(&word.to_le_bytes()[..6]);
    output
}

pub(crate) fn block_dxt5(rgba: &[u8; 64]) -> Result<[u8; 16]> {
    let pixels: [V; 16] =
        std::array::from_fn(|i| std::array::from_fn(|j| n(rgba[i * 4 + j] as f64 * n(1. / 255.))));
    let alpha = std::array::from_fn(|i| n(rgba[i * 4 + 3] as f64 * n(1. / 255.)));
    let mut output = [0; 16];
    output[..8].copy_from_slice(&alpha5(&alpha));
    output[8..].copy_from_slice(&rgb::<false>(&pixels, &[false; 16], &mut Value::Null)?);
    Ok(output)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn recovered_family_states_and_examples() {
        let fixture: Value =
            serde_json::from_str(include_str!("../tests/bodyshop-family-traces.json")).unwrap();
        for case in fixture["alpha_traces"].as_array().unwrap() {
            let points = std::array::from_fn(|i| n(case["points"][i].as_f64().unwrap()));
            let mut trace = json!({});
            let (x, y) = fit_alpha::<true>(
                &points,
                case["steps"].as_u64().unwrap() as usize,
                &mut trace,
            );
            assert_eq!(
                [x as f32, y as f32],
                std::array::from_fn(|i| case["matching_endpoints"][i].as_f64().unwrap() as f32)
            );
            let actual = trace["iterations"].as_array().unwrap();
            let native = case["native"].as_array().unwrap();
            assert_eq!(actual.len(), native.len());
            for (a, b) in actual.iter().zip(native) {
                for k in ["X", "Y", "dx", "dy", "xx", "yy"] {
                    assert_eq!(
                        (a[k].as_f64().unwrap() as f32).to_bits(),
                        (b[k].as_f64().unwrap() as f32).to_bits(),
                        "{k}"
                    );
                }
            }
        }
        for case in fixture["examples"].as_array().unwrap() {
            let pixels = std::array::from_fn(|i| {
                std::array::from_fn(|j| n(case["input"][i][j].as_f64().unwrap()))
            });
            let alpha = std::array::from_fn(|i| n(case["input"][i][3].as_f64().unwrap()));
            let encoded = if case["format"] == 1 {
                rgb::<false>(&pixels, &alpha.map(|a| a < 0.5), &mut Value::Null)
                    .unwrap()
                    .to_vec()
            } else {
                let mut v = alpha5(&alpha).to_vec();
                v.extend(rgb::<false>(&pixels, &[false; 16], &mut Value::Null).unwrap());
                v
            };
            assert_eq!(
                encoded
                    .iter()
                    .map(|b| format!("{b:02x}"))
                    .collect::<String>(),
                case["encoded"].as_str().unwrap(),
                "{} DXT{}",
                case["name"],
                case["format"]
            );
        }
    }
    #[test]
    fn captured_intermediate_states() {
        // Compare the exact binary32 states. The default JSON decimal parser
        // may land one binary64 ULP from the fixture's exact binary32 value.
        fn states(value: &Value) -> Value {
            match value {
                Value::Number(v) => json!((v.as_f64().unwrap() as f32).to_bits()),
                Value::Array(v) => Value::Array(v.iter().map(states).collect()),
                Value::Object(v) => {
                    Value::Object(v.iter().map(|(k, v)| (k.clone(), states(v))).collect())
                }
                v => v.clone(),
            }
        }
        let cases: Value =
            serde_json::from_str(include_str!("../tests/bodyshop-traces.json")).unwrap();
        for case in cases.as_array().unwrap() {
            let pixels: [V; 16] = std::array::from_fn(|i| {
                std::array::from_fn(|j| n(case["pixels"][i][j].as_f64().unwrap()))
            });
            let mut trace = json!({});
            let output = rgb::<true>(&pixels, &[false; 16], &mut trace).unwrap();
            assert_eq!(
                output
                    .iter()
                    .map(|v| format!("{v:02x}"))
                    .collect::<String>(),
                case["rgb"].as_str().unwrap(),
                "{}",
                case["name"]
            );
            assert_eq!(states(&trace), states(&case["trace"]), "{}", case["name"]);
        }
    }
    #[test]
    fn rounding_boundaries() {
        for v in [1. + 3f64 * 2f64.powi(-25), -1. - 3f64 * 2f64.powi(-25)] {
            assert!(z(v).abs() <= v.abs());
            assert!(n(v).abs() > v.abs());
        }
        assert_eq!(z(0.), 0.);
        assert_eq!(z(-0.).to_bits(), (-0f64).to_bits());
        assert_eq!(z(f32::from_bits(1) as f64 / 2.), 0.);
    }
}
