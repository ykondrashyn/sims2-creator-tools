//! Guided fitting v2. The game skeleton is immutable. All edits describe the source.
use crate::{
    assets, object_model,
    sim::{digest, field},
    sim_fit,
};
use anyhow::{ensure, Context, Result};
use dbpf::internal_file::resource_collection::geometric_data_container::AttributeType;
use serde_json::{json, Value};
use std::collections::BTreeMap;
type V = [f32; 3];
type Marks = BTreeMap<String, V>;
const H: f32 = object_model::REFERENCE_HEIGHT;
const NAMES: [(&str, &str); 16] = [
    ("neck", "neck"),
    ("pelvis", "pelvis"),
    ("l_shoulder", "l_upperarm"),
    ("r_shoulder", "r_upperarm"),
    ("l_elbow", "l_forearm"),
    ("r_elbow", "r_forearm"),
    ("l_wrist", "l_hand"),
    ("r_wrist", "r_hand"),
    ("l_hip", "l_thigh"),
    ("r_hip", "r_thigh"),
    ("l_knee", "l_calf"),
    ("r_knee", "r_calf"),
    ("l_ankle", "l_foot"),
    ("r_ankle", "r_foot"),
    ("l_toe", "l_toe"),
    ("r_toe", "r_toe"),
];
const SEGMENTS: [(&str, &str, &str); 15] = [
    ("pelvis", "neck", "torso"),
    ("neck", "l_shoulder", "torso"),
    ("neck", "r_shoulder", "torso"),
    ("l_shoulder", "l_elbow", "l_arm"),
    ("l_elbow", "l_wrist", "l_arm"),
    ("r_shoulder", "r_elbow", "r_arm"),
    ("r_elbow", "r_wrist", "r_arm"),
    ("pelvis", "l_hip", "torso"),
    ("pelvis", "r_hip", "torso"),
    ("l_hip", "l_knee", "l_leg"),
    ("l_knee", "l_ankle", "l_leg"),
    ("l_ankle", "l_toe", "l_leg"),
    ("r_hip", "r_knee", "r_leg"),
    ("r_knee", "r_ankle", "r_leg"),
    ("r_ankle", "r_toe", "r_leg"),
];
fn add(a: V, b: V) -> V {
    std::array::from_fn(|i| a[i] + b[i])
}
fn sub(a: V, b: V) -> V {
    std::array::from_fn(|i| a[i] - b[i])
}
fn mul(a: V, s: f32) -> V {
    a.map(|x| x * s)
}
fn dot(a: V, b: V) -> f32 {
    (0..3).map(|i| a[i] * b[i]).sum()
}
fn len(a: V) -> f32 {
    dot(a, a).sqrt()
}
fn cross(a: V, b: V) -> V {
    [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]
}
fn unit(a: V) -> V {
    mul(a, 1. / len(a).max(1e-12))
}
fn vec(v: &Value) -> Result<V> {
    let a: V = serde_json::from_value(v.clone()).context("Every marker needs three coordinates")?;
    ensure!(
        a.iter().all(|x| x.is_finite() && x.abs() < H * 4.),
        "Marker coordinates are outside the fitting area"
    );
    Ok(a)
}
fn quat(q: [f32; 4], v: V) -> V {
    let u = [q[0], q[1], q[2]];
    add(
        v,
        add(mul(cross(u, v), 2. * q[3]), mul(cross(u, cross(u, v)), 2.)),
    )
}
fn bind_positions(r: &Value) -> Result<Vec<V>> {
    r["bones"]
        .as_array()
        .context("Missing bind transforms")?
        .iter()
        .map(|b| {
            let q: [f32; 4] = serde_json::from_value(b["rotation"].clone())?;
            let t: V = serde_json::from_value(b["translation"].clone())?;
            Ok(quat([-q[0], -q[1], -q[2], q[3]], mul(t, -1.)))
        })
        .collect()
}
fn targets(r: &Value) -> Result<Marks> {
    let positions = bind_positions(r)?;
    NAMES
        .iter()
        .map(|(name, joint)| {
            let i = r["joints"]
                .as_array()
                .unwrap()
                .iter()
                .find(|j| j[0] == *joint)
                .with_context(|| format!("Rig joint {joint} is missing"))?[1]
                .as_u64()
                .unwrap() as usize;
            Ok((name.to_string(), positions[i]))
        })
        .collect()
}
fn markers(p: &Value) -> Result<Marks> {
    NAMES
        .iter()
        .map(|(name, _)| {
            Ok((
                name.to_string(),
                vec(&p["markers"][name])
                    .with_context(|| format!("Place the {} marker", name.replace('_', " ")))?,
            ))
        })
        .collect()
}
fn bounds(points: impl Iterator<Item = V>) -> (V, V) {
    let (mut lo, mut hi) = ([f32::INFINITY; 3], [f32::NEG_INFINITY; 3]);
    for p in points {
        for i in 0..3 {
            lo[i] = lo[i].min(p[i]);
            hi[i] = hi[i].max(p[i]);
        }
    }
    (lo, hi)
}
fn rotate(v: V, a: V) -> V {
    let mut v = v;
    for (axis, d) in a.into_iter().enumerate() {
        let (s, c) = (d.to_radians().sin(), d.to_radians().cos());
        let (j, k) = ((axis + 1) % 3, (axis + 2) % 3);
        let (x, y) = (v[j], v[k]);
        v[j] = x * c - y * s;
        v[k] = x * s + y * c;
    }
    v
}
fn aligned(parts: &[object_model::Part], p: &Value) -> Result<(Vec<Vec<V>>, V)> {
    let a: V = if p["alignment"].is_null() {
        [0.; 3]
    } else {
        serde_json::from_value(p["alignment"].clone())
            .context("Alignment requires three numeric angles")?
    };
    ensure!(
        a.iter().all(|x| x.is_finite() && x.abs() <= 360.),
        "Alignment angles must be between -360 and 360 degrees"
    );
    let (lo, hi) = bounds(
        parts
            .iter()
            .flat_map(|p| p.positions.iter().map(|v| rotate(*v, a))),
    );
    ensure!(
        hi[2] - lo[2] > 1e-6,
        "Stand the model upright before fitting"
    );
    let s = H / (hi[2] - lo[2]);
    let origin = [(lo[0] + hi[0]) / 2., (lo[1] + hi[1]) / 2., lo[2]];
    Ok((
        parts
            .iter()
            .map(|p| {
                p.positions
                    .iter()
                    .map(|v| mul(sub(rotate(*v, a), origin), s))
                    .collect()
            })
            .collect(),
        a,
    ))
}
fn suggestions(points: &[Vec<V>], t: &Marks, p: &Value) -> Marks {
    let (lo, hi) = bounds(points.iter().flatten().copied());
    let mut m = t.clone();
    let span = (hi[0] - lo[0]).max(0.1);
    let canonical = (t["l_wrist"][0] - t["r_wrist"][0]).abs();
    let xscale = (span / canonical).clamp(0.5, 1.5);
    for v in m.values_mut() {
        v[0] *= xscale;
    }
    if p["pose"] == "a" {
        for side in ["l", "r"] {
            let shoulder = m[&format!("{side}_shoulder")];
            for joint in ["elbow", "wrist"] {
                let k = format!("{side}_{joint}");
                let v = m[&k];
                let d = sub(v, shoulder);
                let angle = if d[0] > 0. { 35_f32 } else { -35_f32 };
                m.insert(k, add(shoulder, rotate(d, [0., angle, 0.])));
            }
        }
    }
    m
}
fn segment_distance(v: V, a: V, b: V) -> f32 {
    let d = sub(b, a);
    len(sub(
        v,
        add(
            a,
            mul(d, (dot(sub(v, a), d) / dot(d, d).max(1e-12)).clamp(0., 1.)),
        ),
    ))
}
fn region(v: V, m: &Marks) -> &'static str {
    SEGMENTS
        .iter()
        .map(|(a, b, r)| (*r, segment_distance(v, m[*a], m[*b])))
        .min_by(|a, b| a.1.total_cmp(&b.1))
        .unwrap()
        .0
}
fn rotate_between(v: V, a: V, b: V) -> V {
    let a = unit(a);
    let b = unit(b);
    let c = dot(a, b).clamp(-1., 1.);
    if c > 0.999999 {
        return v;
    }
    if c < -0.9999 {
        let axis = unit(cross(
            a,
            if a[0].abs() < 0.8 {
                [1., 0., 0.]
            } else {
                [0., 1., 0.]
            },
        ));
        return sub(mul(axis, 2. * dot(axis, v)), v);
    }
    let k = cross(a, b);
    add(
        v,
        add(cross(k, v), mul(cross(k, cross(k, v)), 1. / (1. + c))),
    )
}
fn warp(v: V, m: &Marks, t: &Marks) -> V {
    let mut out = [0.; 3];
    let mut sum = 0.;
    let r = region(v, m);
    for (a, b, sr) in SEGMENTS {
        if sr != r && sr != "torso" && r != "torso" {
            continue;
        }
        let d = sub(m[b], m[a]);
        let td = sub(t[b], t[a]);
        let local = sub(v, m[a]);
        let axis = unit(d);
        let along = dot(local, axis);
        let adjusted = add(
            sub(local, mul(axis, along)),
            mul(axis, along * len(td) / len(d)),
        );
        let q = add(t[a], rotate_between(adjusted, d, td));
        let distance = segment_distance(v, m[a], m[b]);
        let w = 1. / (distance * distance + 0.0004).powi(2);
        out = add(out, mul(q, w));
        sum += w;
    }
    mul(out, 1. / sum)
}
#[derive(Default)]
struct Buffers {
    bytes: Vec<u8>,
}
impl Buffers {
    fn f32(&mut self, v: impl Iterator<Item = f32>) -> Value {
        let offset = self.bytes.len();
        self.bytes.extend(v.flat_map(f32::to_le_bytes));
        json!({"offset":offset,"length":(self.bytes.len()-offset)/4,"type":"f32"})
    }
    fn u32(&mut self, v: impl Iterator<Item = u32>) -> Value {
        let offset = self.bytes.len();
        self.bytes.extend(v.flat_map(u32::to_le_bytes));
        json!({"offset":offset,"length":(self.bytes.len()-offset)/4,"type":"u32"})
    }
    fn finish(self, r: &mut Value) -> Result<()> {
        ensure!(
            self.bytes.len() <= 256 * 1024 * 1024,
            "Fitting geometry exceeds the browser buffer limit. Use a lower-detail model"
        );
        r["buffer_asset"] = json!("sim-buffer");
        r["buffer_sha256"] = json!(digest(&self.bytes));
        assets::put_owned("sim-buffer", self.bytes)?;
        Ok(())
    }
}
fn images(parts: &[object_model::Part], p: &Value) -> Result<Vec<Value>> {
    if p["include_images"] == false {
        return Ok(vec![]);
    }
    parts
        .iter()
        .enumerate()
        .map(|(i, part)| {
            let k = format!("sim-texture-cache-{i}");
            if !assets::exists(&k) {
                assets::write(&k, crate::core::images::png(&part.image)?)?;
            }
            let out = format!("sim-texture-{i}");
            assets::write(&out, assets::read(k)?)?;
            Ok(json!({"asset":out,"part":part.name}))
        })
        .collect()
}
fn reference(p: &Value) -> Result<Value> {
    let mut r = sim_fit::reference(field(p, "reference")?)?;
    let parents = r["parents"].as_array().context("Missing rig hierarchy")?;
    ensure!(
        parents.iter().filter(|v| !v.is_null()).count() >= 60,
        "The game skeleton hierarchy is incomplete"
    );
    for start in 0..parents.len() {
        let mut at = start;
        for step in 0..=parents.len() {
            if let Some(parent) = parents[at].as_u64() {
                ensure!(
                    parent < (parents.len() as u64) && step < parents.len(),
                    "Invalid skeleton hierarchy"
                );
                at = parent as usize;
            } else {
                break;
            }
        }
    }
    r.as_object_mut().unwrap().remove("attributes");
    r["joint_positions"] = json!(bind_positions(&r)?);
    Ok(r)
}
// Connected pieces are selectable even when they share one glTF material.
fn components(part: &object_model::Part) -> Result<Vec<u32>> {
    let key = format!("sim-components-{}", part.name);
    if assets::exists(&key) {
        return Ok(assets::read(&key)?
            .chunks_exact(4)
            .map(|b| u32::from_le_bytes(b.try_into().unwrap()))
            .collect());
    }
    let mut parent: Vec<usize> = (0..part.positions.len()).collect();
    fn root(p: &mut [usize], mut i: usize) -> usize {
        while p[i] != i {
            p[i] = p[p[i]];
            i = p[i];
        }
        i
    }
    fn join(p: &mut [usize], a: usize, b: usize) {
        let a = root(p, a);
        let b = root(p, b);
        p[a.max(b)] = a.min(b);
    }
    let mut seams = BTreeMap::new();
    for (i, v) in part.positions.iter().enumerate() {
        let k = v.map(|x| if x == 0. { 0 } else { x.to_bits() });
        if let Some(j) = seams.insert(k, i) {
            join(&mut parent, i, j);
        }
    }
    for tri in part.indices.chunks_exact(3) {
        join(&mut parent, tri[0] as usize, tri[1] as usize);
        join(&mut parent, tri[0] as usize, tri[2] as usize);
    }
    let mut ids = BTreeMap::new();
    let mut out = vec![];
    for i in 0..parent.len() {
        let r = root(&mut parent, i);
        let next = ids.len() as u32;
        out.push(*ids.entry(r).or_insert(next));
    }
    assets::write(
        key,
        out.iter().flat_map(|n| n.to_le_bytes()).collect::<Vec<_>>(),
    )?;
    Ok(out)
}
pub fn align(p: &Value) -> Result<Value> {
    let parts = object_model::character_parts(field(p, "model")?)?;
    let (points, a) = aligned(&parts, p)?;
    let r = reference(p)?;
    let t = targets(&r)?;
    let marks = suggestions(&points, &t, p);
    let mut b = Buffers::default();
    let meshes:Vec<_>=parts.iter().zip(&points).map(|(part,points)|{let pieces=components(part)?;Ok(json!({"component_count":pieces.iter().max().map(|x|x+1).unwrap_or(0),"components":b.u32(pieces.into_iter()),"id":part.name,"positions":b.f32(points.iter().flatten().copied()),"normals":b.f32(part.normals.iter().flat_map(|v|rotate(*v,a))),"uvs":b.f32(part.uvs.iter().flatten().copied()),"indices":b.u32(part.indices.iter().copied()),"cutout":part.cutout,"double_sided":part.double_sided}))}).collect::<Result<Vec<_>>>()?;
    let mut out = json!({"version":2,"parts":meshes,"reference":r,"targets":t,"suggestions":marks,"height":H,"textures":images(&parts,p)?});
    b.finish(&mut out)?;
    Ok(out)
}
pub fn validate(p: &Value) -> Result<Value> {
    let m = markers(p)?;
    let r = reference(p)?;
    let t = targets(&r)?;
    let mut errors = vec![];
    let mut warnings = vec![];
    for (a, b, region) in SEGMENTS {
        let l = len(sub(m[b], m[a]));
        if l < 0.015 {
            errors.push(json!({"region":region,"markers":[a,b],"message":format!("Move {a} and {b} apart. This limb segment has collapsed.")}));
        } else {
            let ratio = len(sub(t[b], t[a])) / l;
            if !(0.4..=2.5).contains(&ratio) {
                warnings.push(json!({"region":region,"markers":[a,b],"message":format!("Check {a} and {b}. Fitting will substantially change this segment's length.")}));
            }
        }
    }
    if m["neck"][2] <= m["pelvis"][2] {
        errors.push(json!({"region":"torso","markers":["neck","pelvis"],"message":"Place the neck above the pelvis."}));
    }
    for s in ["l", "r"] {
        for (a, b) in [("hip", "knee"), ("knee", "ankle")] {
            if m[&format!("{s}_{a}")][2] <= m[&format!("{s}_{b}")][2] {
                errors.push(json!({"region":format!("{s}_leg"),"markers":[format!("{s}_{a}"),format!("{s}_{b}")],"message":"Use a standing neutral pose, with hips above knees and knees above ankles."}));
            }
        }
    }
    for j in ["shoulder", "elbow", "wrist", "hip", "knee", "ankle", "toe"] {
        let (l, r) = (format!("l_{j}"), format!("r_{j}"));
        if (m[&l][0] - m[&r][0]) * (t[&l][0] - t[&r][0]) <= 0. {
            errors.push(json!({"region":j,"markers":[l,r],"message":format!("Check left and right {j} markers from the model's perspective.")}));
        }
    }
    for s in ["l", "r"] {
        let wrist = m[&format!("{s}_wrist")];
        if wrist[2] < m["pelvis"][2] || segment_distance(wrist, m["pelvis"], m["neck"]) < 0.18 {
            errors.push(json!({"region":format!("{s}_arm"),"markers":[format!("{s}_wrist")],"message":"Use an A or T pose with arms separated from the torso."}));
        }
    }
    Ok(json!({"version":2,"valid":errors.is_empty(),"errors":errors,"warnings":warnings}))
}
// Closest point and barycentric coordinates, including edge and vertex regions.
fn closest(p: V, a: V, b: V, c: V) -> (V, [f32; 3]) {
    let ab = sub(b, a);
    let ac = sub(c, a);
    let ap = sub(p, a);
    let d1 = dot(ab, ap);
    let d2 = dot(ac, ap);
    if d1 <= 0. && d2 <= 0. {
        return (a, [1., 0., 0.]);
    }
    let bp = sub(p, b);
    let d3 = dot(ab, bp);
    let d4 = dot(ac, bp);
    if d3 >= 0. && d4 <= d3 {
        return (b, [0., 1., 0.]);
    }
    let vc = d1 * d4 - d3 * d2;
    if vc <= 0. && d1 >= 0. && d3 <= 0. {
        let v = d1 / (d1 - d3);
        return (add(a, mul(ab, v)), [1. - v, v, 0.]);
    }
    let cp = sub(p, c);
    let d5 = dot(ab, cp);
    let d6 = dot(ac, cp);
    if d6 >= 0. && d5 <= d6 {
        return (c, [0., 0., 1.]);
    }
    let vb = d5 * d2 - d1 * d6;
    if vb <= 0. && d2 >= 0. && d6 <= 0. {
        let w = d2 / (d2 - d6);
        return (add(a, mul(ac, w)), [1. - w, 0., w]);
    }
    let va = d3 * d6 - d5 * d4;
    if va <= 0. && (d4 - d3) >= 0. && (d5 - d6) >= 0. {
        let w = (d4 - d3) / ((d4 - d3) + (d5 - d6));
        return (add(b, mul(sub(c, b), w)), [0., 1. - w, w]);
    }
    let den = 1. / (va + vb + vc);
    let v = vb * den;
    let w = vc * den;
    (add(a, add(mul(ab, v), mul(ac, w))), [1. - v - w, v, w])
}
struct Tri {
    ids: [usize; 3],
    region: &'static str,
    lo: V,
    hi: V,
}
fn box_distance(p: V, lo: V, hi: V) -> f32 {
    (0..3)
        .map(|i| {
            if p[i] < lo[i] {
                (p[i] - lo[i]).powi(2)
            } else if p[i] > hi[i] {
                (p[i] - hi[i]).powi(2)
            } else {
                0.
            }
        })
        .sum()
}
// Region-specific BVHs keep dense imports bounded rather than scanning every face.
struct Tree {
    lo: V,
    hi: V,
    ids: Vec<usize>,
    children: Vec<Tree>,
}
impl Tree {
    fn new(ids: Vec<usize>, tris: &[Tri]) -> Self {
        let (lo, hi) = bounds(ids.iter().flat_map(|i| [tris[*i].lo, tris[*i].hi]));
        if ids.len() <= 8 {
            return Self {
                lo,
                hi,
                ids,
                children: vec![],
            };
        }
        let axis = (0..3)
            .max_by(|a, b| (hi[*a] - lo[*a]).total_cmp(&(hi[*b] - lo[*b])))
            .unwrap();
        let mut ids = ids;
        ids.sort_by(|a, b| {
            (tris[*a].lo[axis] + tris[*a].hi[axis])
                .total_cmp(&(tris[*b].lo[axis] + tris[*b].hi[axis]))
                .then(a.cmp(b))
        });
        let right = ids.split_off(ids.len() / 2);
        Self {
            lo,
            hi,
            ids: vec![],
            children: vec![Self::new(ids, tris), Self::new(right, tris)],
        }
    }
    fn nearest(&self, p: V, tris: &[Tri], points: &[V], best: &mut (f32, usize, [f32; 3])) {
        if box_distance(p, self.lo, self.hi) > best.0 {
            return;
        }
        for i in &self.ids {
            let t = &tris[*i];
            let (q, w) = closest(p, points[t.ids[0]], points[t.ids[1]], points[t.ids[2]]);
            let d = dot(sub(p, q), sub(p, q));
            if d < best.0 || (d == best.0 && *i < best.1) {
                *best = (d, *i, w);
            }
        }
        if let [a, b] = self.children.as_slice() {
            // Equal distances retain construction order, like the stable sort.
            let (first, second) = if box_distance(p, a.lo, a.hi)
                .total_cmp(&box_distance(p, b.lo, b.hi))
                .is_gt()
            {
                (b, a)
            } else {
                (a, b)
            };
            first.nearest(p, tris, points, best);
            second.nearest(p, tris, points, best);
        }
    }
}
fn warped_normal(v: V, n: V, base_warp: V, m: &Marks, t: &Marks) -> V {
    // Transform a tangent frame to preserve authored normals under nonuniform fitting.
    let tangent = unit(cross(
        n,
        if n[0].abs() < 0.8 {
            [1., 0., 0.]
        } else {
            [0., 1., 0.]
        },
    ));
    let bitangent = cross(n, tangent);
    let a = sub(warp(add(v, mul(tangent, 0.0001)), m, t), base_warp);
    let c = sub(warp(add(v, mul(bitangent, 0.0001)), m, t), base_warp);
    unit(cross(a, c))
}
pub fn fit(p: &Value) -> Result<Value> {
    ensure!(p["guided_version"] == 2, "Use guided fitting version 2");
    let report = validate(p)?;
    ensure!(
        report["valid"] == true,
        "Correct the highlighted joint markers before applying the fit"
    );
    ensure!(
        p["review"]["align"] == true
            && p["review"]["markers"] == true
            && p["review"]["head"] == true,
        "Review alignment, joint markers and the head separation before applying the fit"
    );
    let m = markers(p)?;
    let r = reference(p)?;
    let t = targets(&r)?;
    let parts = object_model::character_parts(field(p, "model")?)?;
    let (points, angles) = aligned(&parts, p)?;
    let neck = p["neck_height"].as_f64().unwrap_or(m["neck"][2] as f64) as f32;
    ensure!(
        neck.is_finite() && neck > m["pelvis"][2] && neck < H,
        "Place the head boundary between the pelvis and the top of the head"
    );
    let mesh = sim_fit::geometry(field(p, "reference")?)?;
    let rp = sim_fit::triples(sim_fit::attr(&mesh, AttributeType::Positions)?)?;
    let bw = sim_fit::triples(sim_fit::attr(&mesh, AttributeType::BoneWeights)?)?;
    let bk = &sim_fit::attr(&mesh, AttributeType::BoneKeys)?.data;
    let tris: Vec<_> = mesh.meshes[0]
        .indices
        .chunks_exact(3)
        .map(|i| {
            let ids = [i[0].0 as usize, i[1].0 as usize, i[2].0 as usize];
            let (lo, hi) = bounds(ids.iter().map(|i| rp[*i]));
            let center = mul(add(add(rp[ids[0]], rp[ids[1]]), rp[ids[2]]), 1. / 3.);
            Tri {
                ids,
                lo,
                hi,
                region: region(center, &t),
            }
        })
        .filter(|tri| {
            len(cross(
                sub(rp[tri.ids[1]], rp[tri.ids[0]]),
                sub(rp[tri.ids[2]], rp[tri.ids[0]]),
            )) > 1e-10
        })
        .collect();
    let trees: BTreeMap<_, _> = ["torso", "l_arm", "r_arm", "l_leg", "r_leg"]
        .into_iter()
        .map(|region| {
            let ids = tris
                .iter()
                .enumerate()
                .filter(|(_, t)| t.region == region)
                .map(|(i, _)| i)
                .collect::<Vec<_>>();
            (region, Tree::new(ids, &tris))
        })
        .collect();
    let mut deltas = vec![];
    for (target, binding) in mesh.blend_group_bindings.iter().enumerate() {
        if binding.element.to_string().is_empty() {
            continue;
        }
        let mut delta = vec![[0.; 3]; rp.len()];
        let attrs =
            &mesh.attribute_groups[mesh.meshes[0].attribute_group_index as usize].attributes;
        for a in attrs
            .iter()
            .map(|r| &mesh.attribute_buffers[r.0 as usize])
            .filter(|a| a.binding.binding_type == AttributeType::PositionDeltas)
        {
            let keys = attrs
                .iter()
                .map(|r| &mesh.attribute_buffers[r.0 as usize])
                .find(|b| {
                    b.binding.binding_type == AttributeType::BlendKeys
                        && b.binding.binding_slot == a.binding.binding_slot / 4
                })
                .context("Missing morph keys")?;
            let data = sim_fit::triples(a)?;
            for i in 0..rp.len() {
                if keys.data[i * 4 + (a.binding.binding_slot % 4) as usize] as usize == target {
                    delta[i] = data[i];
                }
            }
        }
        deltas.push((binding.element.to_string(), delta));
    }
    let mut b = Buffers::default();
    let mut outparts = vec![];
    let mut problems = BTreeMap::<String, usize>::new();
    let mut allbounds = vec![];
    for (part, points) in parts.iter().zip(&points) {
        let pieces = components(part)?;
        let component_roles: Vec<_> = pieces
            .iter()
            .map(|c| p["roles"][format!("{}#{c}", part.name)].as_str())
            .collect();
        let role = p["roles"][&part.name].as_str().unwrap_or("split");
        ensure!(
            ["split", "head", "body"].contains(&role),
            "Choose head or body for every part"
        );
        let mut pos = vec![];
        let mut norms = vec![];
        let mut weights = vec![];
        let mut joints = vec![];
        let mut morphs = vec![Vec::new(); deltas.len()];
        let mut issues = vec![];
        let mut head_vertices = vec![];
        for (i, v) in points.iter().enumerate() {
            let role = component_roles[i].unwrap_or(role);
            ensure!(
                ["split", "head", "body"].contains(&role),
                "Invalid connected-piece assignment"
            );
            let head = role == "head" || (role == "split" && v[2] >= neck);
            head_vertices.push(u32::from(head));
            let rigid = add(*v, sub(t["neck"], m["neck"]));
            let base_warp = if head { rigid } else { warp(*v, &m, &t) };
            let mut q = base_warp;
            if !head && role == "split" {
                let blend = ((v[2] - (neck - 0.06)) / 0.06).clamp(0., 1.);
                q = add(mul(q, 1. - blend), mul(rigid, blend));
            }
            pos.push(q);
            let n = rotate(part.normals[i], angles);
            if head {
                norms.push(n);
                joints.extend([7, 0, 0, 0]);
                weights.extend([1., 0., 0., 0.]);
                for d in &mut morphs {
                    d.push([0.; 3]);
                }
                issues.push(0.);
                continue;
            }
            let region = region(*v, &m);
            let mut best = (f32::INFINITY, usize::MAX, [0.; 3]);
            trees[region].nearest(q, &tris, &rp, &mut best);
            ensure!(best.1 != usize::MAX, "Reference surface region is missing");
            let tri = &tris[best.1];
            let mut influence = BTreeMap::<u32, f32>::new();
            for (k, idx) in tri.ids.iter().enumerate() {
                let keys = &bk[*idx * 4..*idx * 4 + 4];
                let w = [
                    bw[*idx][0],
                    bw[*idx][1],
                    bw[*idx][2],
                    (1. - bw[*idx].iter().sum::<f32>()).max(0.),
                ];
                for n in 0..4 {
                    if keys[n] != 255 {
                        *influence
                            .entry(mesh.meshes[0].bone_references[keys[n] as usize].0)
                            .or_default() += w[n] * best.2[k];
                    }
                }
            }
            let mut sorted = influence.into_iter().collect::<Vec<_>>();
            sorted.sort_by(|a, b| b.1.total_cmp(&a.1).then(a.0.cmp(&b.0)));
            sorted.truncate(4);
            let total: f32 = sorted.iter().map(|x| x.1).sum();
            ensure!(total > 0., "No usable game weights for this region");
            for k in 0..4 {
                joints.push(sorted.get(k).map(|x| x.0).unwrap_or(0));
                weights.push(sorted.get(k).map(|x| x.1 / total).unwrap_or(0.));
            }
            for (j, (_, d)) in deltas.iter().enumerate() {
                morphs[j]
                    .push((0..3).fold([0.; 3], |sum, k| add(sum, mul(d[tri.ids[k]], best.2[k]))));
            }
            let distance = best.0.sqrt();
            let issue = if distance > 0.18 {
                *problems.entry(region.into()).or_default() += 1;
                1.
            } else {
                0.
            };
            issues.push(issue);
            norms.push(warped_normal(*v, n, base_warp, &m, &t));
        }
        allbounds.extend(pos.iter().copied());
        outparts.push(json!({"id":part.name,"role":role,"positions":b.f32(pos.into_iter().flatten()),"normals":b.f32(norms.into_iter().flatten()),"uvs":b.f32(part.uvs.iter().flatten().copied()),"indices":b.u32(part.indices.iter().copied()),"joints":b.u32(joints.into_iter()),"weights":b.f32(weights.into_iter()),"issues":b.f32(issues.into_iter()),"head_vertices":b.u32(head_vertices.into_iter()),"morphs":deltas.iter().zip(morphs).map(|((name,_),d)|json!({"name":name,"positions":b.f32(d.into_iter().flatten())})).collect::<Vec<_>>(),"cutout":part.cutout,"double_sided":part.double_sided}));
    }
    let (lo, hi) = bounds(allbounds.into_iter());
    let mut warnings = report["warnings"].as_array().unwrap().clone();
    for (region, count) in problems {
        warnings.push(json!({"region":region,"vertices":count,"message":format!("{count} vertices in {region} are far from the game body. Check the highlighted geometry and movement.")}));
    }
    let mut result = json!({"version":2,"parts":outparts,"reference":r,"targets":t,"height":hi[2]-lo[2],"textures":images(&parts,p)?,"warnings":warnings,"gameplay":"not_tested"});
    b.finish(&mut result)?;
    Ok(result)
}
#[cfg(test)]
mod tests {
    use super::*;

    // Prior traversal retained here to compare exact distances, ties and weights.
    fn sorted_nearest(tree: &Tree, p: V, tris: &[Tri], points: &[V], best: &mut (f32, usize, V)) {
        if box_distance(p, tree.lo, tree.hi) > best.0 {
            return;
        }
        for i in &tree.ids {
            let t = &tris[*i];
            let (q, w) = closest(p, points[t.ids[0]], points[t.ids[1]], points[t.ids[2]]);
            let d = dot(sub(p, q), sub(p, q));
            if d < best.0 || (d == best.0 && *i < best.1) {
                *best = (d, *i, w);
            }
        }
        let mut children: Vec<_> = tree.children.iter().collect();
        children
            .sort_by(|a, b| box_distance(p, a.lo, a.hi).total_cmp(&box_distance(p, b.lo, b.hi)));
        for child in children {
            sorted_nearest(child, p, tris, points, best);
        }
    }

    #[test]
    fn direct_bvh_matches_stable_sorted_traversal_including_ties() {
        let mut points = Vec::new();
        let tris: Vec<_> = (0..64)
            .map(|i| {
                // Duplicate every surface so nearest-index tie breaking is exercised.
                let x = ((i / 2) % 8) as f32 * 0.17;
                let y = ((i / 2) / 8) as f32 * 0.13;
                let ids = [points.len(), points.len() + 1, points.len() + 2];
                points.extend([[x, y, 0.], [x + 0.1, y, 0.], [x, y + 0.1, 0.]]);
                let (lo, hi) = bounds(ids.iter().map(|i| points[*i]));
                Tri {
                    ids,
                    lo,
                    hi,
                    region: "body",
                }
            })
            .collect();
        let tree = Tree::new((0..tris.len()).collect(), &tris);
        for i in 0..1000 {
            let p = [
                (i % 31) as f32 * 0.053 - 0.2,
                (i % 19) as f32 * 0.047 - 0.1,
                if i % 2 == 0 { 0. } else { -0.13 },
            ];
            let mut expected = (f32::INFINITY, usize::MAX, [0.; 3]);
            let mut actual = expected;
            sorted_nearest(&tree, p, &tris, &points, &mut expected);
            tree.nearest(p, &tris, &points, &mut actual);
            assert_eq!(actual.0.to_bits(), expected.0.to_bits());
            assert_eq!(actual.1, expected.1);
            assert_eq!(actual.2.map(f32::to_bits), expected.2.map(f32::to_bits));
        }
    }

    #[test]
    fn normal_reuses_unblended_warp_at_the_neck() {
        let mut m = Marks::new();
        for (i, (name, _)) in NAMES.iter().enumerate() {
            m.insert(
                name.to_string(),
                [
                    i as f32 * 0.08,
                    (i % 3) as f32 * 0.03,
                    (16 - i) as f32 * 0.1,
                ],
            );
        }
        let t: Marks = m
            .iter()
            .map(|(name, p)| (name.clone(), [p[0] * 0.83, p[1] * 1.17, p[2] * 1.04]))
            .collect();
        for i in 0..128 {
            let v = [0.3, 0.07, m["neck"][2] - 0.06 + i as f32 * 0.0005];
            let n = unit([0.2, 0.8, 0.1]);
            let tangent = unit(cross(n, [1., 0., 0.]));
            let bitangent = cross(n, tangent);
            let base = warp(v, &m, &t);
            let expected = unit(cross(
                sub(warp(add(v, mul(tangent, 0.0001)), &m, &t), warp(v, &m, &t)),
                sub(
                    warp(add(v, mul(bitangent, 0.0001)), &m, &t),
                    warp(v, &m, &t),
                ),
            ));
            let actual = warped_normal(v, n, base, &m, &t);
            assert_eq!(actual.map(f32::to_bits), expected.map(f32::to_bits));
        }
    }
    #[test]
    fn barycentric_surface_interpolation() {
        let (p, w) = closest([0.25, 0.25, 2.], [0., 0., 0.], [1., 0., 0.], [0., 1., 0.]);
        assert_eq!(p, [0.25, 0.25, 0.]);
        assert_eq!(w, [0.5, 0.25, 0.25]);
    }
    #[test]
    fn identity_segment_warp() {
        let mut m = Marks::new();
        for (i, (a, _)) in NAMES.iter().enumerate() {
            m.insert(
                a.to_string(),
                [
                    i as f32 * 0.08,
                    (i % 3) as f32 * 0.03,
                    (16 - i) as f32 * 0.1,
                ],
            );
        }
        for p in [[0.1, 0.2, 0.8], [0.3, 0., 0.1]] {
            assert!(len(sub(warp(p, &m, &m), p)) < 1e-5);
        }
    }
}
