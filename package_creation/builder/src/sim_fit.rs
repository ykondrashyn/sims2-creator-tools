//! Character fitting retains game rig data and uses a separate bounded import path.
use crate::{assets, core::resources, object_model, object_scene, sim::field};
use anyhow::{ensure, Context, Result};
use base64::{engine::general_purpose::STANDARD, Engine as _};
use dbpf::internal_file::resource_collection::{
    geometric_data_container::{
        AttributeBuffer, AttributeType, BlockFormat, GeometricDataContainer,
    },
    ResourceData,
};
use serde_json::{json, Value};
use std::path::Path;

pub(crate) fn geometry(path: &str) -> Result<std::rc::Rc<GeometricDataContainer>> {
    let cache_key = assets::cache_identity(path)?;
    if let Some(rig) = crate::caches::sim_rig_get(&cache_key) {
        return Ok(rig);
    }
    let (_, nodes) = resources::load_raw(Path::new(path))?;
    let n = nodes
        .iter()
        .find(|n| n.key().0 == 0xac4f8687)
        .context("Rig reference has no body geometry")?;
    let c = n.collection()?;
    match &c.entries[0].data {
        ResourceData::Mesh(m) => {
            let rig = std::rc::Rc::new(m.clone());
            crate::caches::sim_rig_insert(&cache_key, rig.clone());
            Ok(rig)
        }
        _ => anyhow::bail!("Invalid reference body"),
    }
}
pub(crate) fn attr(m: &GeometricDataContainer, kind: AttributeType) -> Result<&AttributeBuffer> {
    let mesh = m.meshes.first().context("Reference mesh is empty")?;
    let g = m
        .attribute_groups
        .get(mesh.attribute_group_index as usize)
        .context("Invalid reference attribute group")?;
    ensure!(
        g.vertex_indices.is_empty(),
        "Indexed reference geometry needs normalization before fitting"
    );
    g.attributes
        .iter()
        .filter_map(|r| m.attribute_buffers.get(r.0 as usize))
        .find(|a| a.binding.binding_type == kind)
        .context("Reference attribute is missing")
}
pub(crate) fn triples(a: &AttributeBuffer) -> Result<Vec<[f32; 3]>> {
    ensure!(
        a.block_format == BlockFormat::F32Vec3 && a.data.len() == a.number_elements as usize * 12,
        "Invalid reference vector buffer"
    );
    Ok(a.data
        .chunks_exact(12)
        .map(|b| {
            std::array::from_fn(|i| f32::from_le_bytes(b[i * 4..i * 4 + 4].try_into().unwrap()))
        })
        .collect())
}
fn bounds(points: impl Iterator<Item = [f32; 3]>) -> ([f32; 3], [f32; 3]) {
    let (mut lo, mut hi) = ([f32::INFINITY; 3], [f32::NEG_INFINITY; 3]);
    for p in points {
        for k in 0..3 {
            lo[k] = lo[k].min(p[k]);
            hi[k] = hi[k].max(p[k]);
        }
    }
    (lo, hi)
}
struct Nearest {
    index: usize,
    axis: usize,
    left: Option<Box<Nearest>>,
    right: Option<Box<Nearest>>,
}
impl Nearest {
    fn new(points: &[[f32; 3]], mut ids: Vec<usize>, depth: usize) -> Option<Box<Self>> {
        if ids.is_empty() {
            return None;
        }
        let axis = depth % 3;
        ids.sort_by(|a, b| points[*a][axis].total_cmp(&points[*b][axis]).then(a.cmp(b)));
        let middle = ids.len() / 2;
        let right = ids.split_off(middle + 1);
        let index = ids.pop().unwrap();
        Some(Box::new(Self {
            index,
            axis,
            left: Self::new(points, ids, depth + 1),
            right: Self::new(points, right, depth + 1),
        }))
    }
    fn find(&self, points: &[[f32; 3]], v: &[f32; 3], best: &mut (f32, usize)) {
        let p = &points[self.index];
        let d = (0..3).map(|k| (p[k] - v[k]) * (p[k] - v[k])).sum::<f32>();
        if d < best.0 || (d == best.0 && self.index < best.1) {
            *best = (d, self.index);
        }
        let delta = v[self.axis] - p[self.axis];
        let (near, far) = if delta < 0. {
            (&self.left, &self.right)
        } else {
            (&self.right, &self.left)
        };
        if let Some(n) = near {
            n.find(points, v, best);
        }
        if delta * delta <= best.0 {
            if let Some(n) = far {
                n.find(points, v, best);
            }
        }
    }
}
pub fn reference(path: &str) -> Result<Value> {
    let cache_key = assets::cache_identity(path)?;
    if let Some(value) = crate::caches::sim_metadata_get(&cache_key) {
        return Ok(value);
    }
    let m = geometry(path)?;
    let positions = triples(attr(&m, AttributeType::Positions)?)?;
    let (lo, hi) = bounds(positions.iter().copied());
    let (_, nodes) = resources::load_raw(Path::new(path))?;
    let cres = nodes
        .iter()
        .find(|n| n.key().0 == 0xe519c933)
        .context("Rig reference has no skeleton")?;
    let scene = object_scene::read(&cres.bytes)?;
    let mut parents = vec![None; m.bones.len()];
    for (parent, children) in scene.skeleton_nodes.values() {
        if *parent as usize >= parents.len() {
            continue;
        }
        // Non-joint grip nodes can sit between a joint and its descendants.
        let mut pending = children.clone();
        let mut seen = std::collections::BTreeSet::new();
        while let Some(child) = pending.pop() {
            ensure!(seen.insert(child), "Cyclic skeleton scene links");
            if let Some((joint, descendants)) = scene.skeleton_nodes.get(&child) {
                if (*joint as usize) < parents.len() {
                    ensure!(
                        parents[*joint as usize].is_none(),
                        "Skeleton joint has multiple parents"
                    );
                    parents[*joint as usize] = Some(*parent);
                } else {
                    pending.extend(descendants);
                }
            }
        }
    }
    let value = json!({"version":1,"sha256":assets::digest(path)?,"vertices":positions.len(),"bounds":[lo,hi],
        "positions":positions,"indices":m.meshes[0].indices.iter().map(|r|r.0).collect::<Vec<_>>(),
        "bones":m.bones.iter().map(|b|json!({"rotation":[b.rotation.x,b.rotation.y,b.rotation.z,b.rotation.w],"translation":[b.translation.x,b.translation.y,b.translation.z]})).collect::<Vec<_>>(),
        "joints":scene.joints,"parents":parents,"bone_references":m.meshes[0].bone_references.iter().map(|r|r.0).collect::<Vec<_>>(),
        "morphs":m.blend_group_bindings.iter().map(|b|json!({"group":b.blend_group.to_string(),"name":b.element.to_string()})).collect::<Vec<_>>(),
        "attributes":m.attribute_buffers.iter().map(|a|json!({"kind":format!("{:08x}",a.binding.binding_type as u32),"slot":a.binding.binding_slot,"count":a.number_elements,"format":format!("{:?}",a.block_format),"data":STANDARD.encode(&*a.data)})).collect::<Vec<_>>() });
    crate::caches::sim_metadata_insert(&cache_key, value.clone());
    Ok(value)
}
pub fn inspect(path: &str) -> Result<Value> {
    let parts = object_model::character_parts(path)?;
    let (lo, hi) = bounds(parts.iter().flat_map(|p| p.positions.iter().copied()));
    ensure!(
        hi[2] - lo[2] > 1e-6,
        "Model has no usable height. Export it standing upright"
    );
    Ok(
        json!({"version":1,"model_sha256":assets::digest(path)?,"bounds":[lo,hi],"parts":parts.iter().map(|p|json!({"id":p.name,"vertices":p.positions.len(),"triangles":p.indices.len()/3,"texture":[p.image.width(),p.image.height()]})).collect::<Vec<_>>(),"rig_policy":"neutral geometry, game weights transferred after fitting","gameplay":"not_tested"}),
    )
}
fn packed(data: impl Iterator<Item = f32>) -> String {
    STANDARD.encode(data.flat_map(|v| v.to_le_bytes()).collect::<Vec<_>>())
}
pub fn fit(p: &Value) -> Result<Value> {
    let parts = object_model::character_parts(field(p, "model")?)?;
    let ref_path = field(p, "reference")?;
    let m = geometry(ref_path)?;
    let ref_info = reference(ref_path)?;
    let ref_positions = triples(attr(&m, AttributeType::Positions)?)?;
    let tree = Nearest::new(&ref_positions, (0..ref_positions.len()).collect(), 0)
        .context("Empty rig reference")?;
    let bone_keys = attr(&m, AttributeType::BoneKeys)?;
    let bone_weights = triples(attr(&m, AttributeType::BoneWeights)?)?;
    ensure!(
        bone_keys.data.len() == ref_positions.len() * 4
            && bone_weights.len() == ref_positions.len(),
        "Reference weights differ from the reference geometry"
    );
    let head = ref_info["joints"]
        .as_array()
        .context("Missing skeleton joints")?
        .iter()
        .find(|j| {
            j[0].as_str()
                .is_some_and(|n| n.eq_ignore_ascii_case("head"))
        })
        .context("The canonical head joint is missing")?[1]
        .as_u64()
        .context("Invalid head joint")? as u16;
    let neck = p["neck"].as_f64().unwrap_or(0.84) as f32;
    let angle = p["rotation"].as_f64().unwrap_or(0.) as f32;
    ensure!(
        neck.is_finite() && (0.6..=0.95).contains(&neck),
        "Place the neck between 60% and 95% of model height"
    );
    ensure!(
        angle.is_finite() && (-180.0..=180.0).contains(&angle),
        "Rotation must be between -180 and 180 degrees"
    );
    let (lo, hi) = bounds(parts.iter().flat_map(|p| p.positions.iter().copied()));
    let height = hi[2] - lo[2];
    ensure!(height > 1e-6, "Model has no usable height");
    let scale = object_model::REFERENCE_HEIGHT / height;
    let angle = angle.to_radians();
    let (s, c) = angle.sin_cos();
    let mut result = vec![];
    let roles = p["roles"].as_object();
    for part in parts.iter() {
        let role = roles
            .and_then(|r| r.get(&part.name))
            .and_then(Value::as_str)
            .unwrap_or("split");
        ensure!(
            ["head", "body", "split"].contains(&role),
            "Choose head, body or split at neck for every part"
        );
        let positions = part
            .positions
            .iter()
            .map(|v| {
                let x = v[0] - (lo[0] + hi[0]) / 2.;
                let y = v[1] - (lo[1] + hi[1]) / 2.;
                [
                    scale * (x * c - y * s),
                    scale * (x * s + y * c),
                    scale * (v[2] - lo[2]),
                ]
            })
            .collect::<Vec<_>>();
        let mut joints = Vec::<u16>::new();
        let mut weights = vec![];
        let mut nearest = vec![];
        for (i, v) in positions.iter().enumerate() {
            if role == "head"
                || (role == "split" && (part.positions[i][2] - lo[2]) / height >= neck)
            {
                joints.extend([head, 0, 0, 0]);
                weights.extend([1., 0., 0., 0.]);
                nearest.push(None);
                continue;
            }
            let mut best = (f32::INFINITY, usize::MAX);
            tree.find(&ref_positions, v, &mut best);
            let source = best.1;
            let keys = &bone_keys.data[source * 4..source * 4 + 4];
            let source_weights = bone_weights[source];
            let mut w = [
                source_weights[0],
                source_weights[1],
                source_weights[2],
                (1. - source_weights.iter().sum::<f32>()).max(0.),
            ];
            for k in 0..4 {
                if keys[k] == 255 {
                    joints.push(0);
                    w[k] = 0.;
                } else {
                    joints.push(
                        m.meshes[0]
                            .bone_references
                            .get(keys[k] as usize)
                            .context("Invalid bone reference")?
                            .0 as u16,
                    );
                }
            }
            let total = w.iter().sum::<f32>();
            ensure!(
                total > 0. && total.is_finite(),
                "Invalid reference skin weight"
            );
            weights.extend(w.map(|w| w / total));
            nearest.push(Some(source));
        }
        let mut morphs = vec![];
        for (target, binding) in m.blend_group_bindings.iter().enumerate() {
            if binding.element.to_string().is_empty() {
                continue;
            }
            let mut delta = vec![[0.; 3]; ref_positions.len()];
            for a in m.attribute_groups[m.meshes[0].attribute_group_index as usize]
                .attributes
                .iter()
                .map(|r| &m.attribute_buffers[r.0 as usize])
                .filter(|a| a.binding.binding_type == AttributeType::PositionDeltas)
            {
                let keys = m.attribute_groups[m.meshes[0].attribute_group_index as usize]
                    .attributes
                    .iter()
                    .map(|r| &m.attribute_buffers[r.0 as usize])
                    .find(|b| {
                        b.binding.binding_type == AttributeType::BlendKeys
                            && b.binding.binding_slot == a.binding.binding_slot / 4
                    })
                    .context("Morph keys are missing")?;
                let d = triples(a)?;
                ensure!(
                    d.len() == delta.len() && keys.data.len() == d.len() * 4,
                    "Invalid reference morph lengths"
                );
                for i in 0..d.len() {
                    if keys.data[i * 4 + (a.binding.binding_slot % 4) as usize] as usize == target {
                        delta[i] = d[i];
                    }
                }
            }
            morphs.push(json!({"name":binding.element.to_string(),"positions":packed(nearest.iter().flat_map(|i|i.map(|i|delta[i]).unwrap_or([0.;3])))}));
        }
        result.push(json!({"id":part.name,"role":role,"positions":packed(positions.iter().flatten().copied()),
            "normals":packed(part.normals.iter().flat_map(|n|[n[0]*c-n[1]*s,n[0]*s+n[1]*c,n[2]])),"uvs":packed(part.uvs.iter().flatten().copied()),
            "indices":STANDARD.encode(part.indices.iter().flat_map(|i|i.to_le_bytes()).collect::<Vec<_>>()),
            "joints":STANDARD.encode(joints.iter().flat_map(|i|i.to_le_bytes()).collect::<Vec<_>>()),"weights":packed(weights.into_iter()),"morphs":morphs,
            "image":format!("data:image/png;base64,{}",STANDARD.encode(crate::core::images::png(&part.image)?)),"cutout":part.cutout,"double_sided":part.double_sided}));
    }
    Ok(
        json!({"version":1,"height":object_model::REFERENCE_HEIGHT,"head_joint":head,"parts":result,"bones":ref_info["bones"],"joints":ref_info["joints"],"reference_positions":ref_info["positions"],"reference_indices":ref_info["indices"],"neck":neck,"gameplay":"not_tested","method":"nearest reference vertex weight and morph transfer","limitations":["Rigid head has no facial expressions","Fitting and deformation require review before gameplay acceptance"]}),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn spatial_lookup_matches_exhaustive_and_breaks_ties_stably() {
        let points: Vec<_> = (0..1173)
            .map(|i| {
                [
                    ((i * 17) % 101) as f32 / 50.,
                    ((i * 23) % 113) as f32 / 70.,
                    ((i * 31) % 89) as f32 / 60.,
                ]
            })
            .collect();
        let tree = Nearest::new(&points, (0..points.len()).collect(), 0).unwrap();
        for i in 0..200 {
            let v = [i as f32 / 100., 0.5, 1.];
            let mut best = (f32::INFINITY, usize::MAX);
            tree.find(&points, &v, &mut best);
            let expected = points
                .iter()
                .enumerate()
                .map(|(i, p)| {
                    (
                        (0..3).map(|k| (p[k] - v[k]) * (p[k] - v[k])).sum::<f32>(),
                        i,
                    )
                })
                .min_by(|a, b| a.0.total_cmp(&b.0).then(a.1.cmp(&b.1)))
                .unwrap();
            assert_eq!(best, expected);
        }
    }
}
