//! Per-session feature caches, separate from asset buffer storage.
use crate::{assets, core::geometry::Part};
use dbpf::internal_file::resource_collection::geometric_data_container::GeometricDataContainer;
use std::{collections::BTreeMap, rc::Rc};
#[derive(Default)]
pub(crate) struct FeatureCaches {
    models: BTreeMap<String, Rc<Vec<Part>>>,
    sim_rigs: BTreeMap<String, Rc<GeometricDataContainer>>,
    sim_metadata: BTreeMap<String, serde_json::Value>,
    hair_sources: BTreeMap<String, Rc<crate::hair::Inspection>>,
}
pub(crate) fn hair_source_get(name: &str) -> Option<Rc<crate::hair::Inspection>> {
    assets::cache_read(|a| a.hair_sources.get(name).cloned()).flatten()
}
pub(crate) fn hair_source_insert(name: &str, info: Rc<crate::hair::Inspection>) {
    assets::cache_write(|a| {
        // One fully validated source, with a separate bound on retained pixels.
        a.hair_sources.clear();
        if info.pixel_bytes() <= 128 * 1024 * 1024 {
            a.hair_sources.insert(name.into(), info);
        }
    });
}
pub(crate) fn model_get(name: &str) -> Option<Rc<Vec<Part>>> {
    assets::cache_read(|a| a.models.get(name).cloned()).flatten()
}
pub(crate) fn model_insert(name: &str, model: Rc<Vec<Part>>) {
    assets::cache_write(|a| {
        a.models.clear();
        a.models.insert(name.into(), model);
    });
}
pub(crate) fn sim_rig_get(name: &str) -> Option<Rc<GeometricDataContainer>> {
    assets::cache_read(|a| a.sim_rigs.get(name).cloned()).flatten()
}
pub(crate) fn sim_rig_insert(name: &str, rig: Rc<GeometricDataContainer>) {
    assets::cache_write(|a| {
        a.sim_rigs.insert(name.into(), rig);
    });
}
pub(crate) fn sim_metadata_get(name: &str) -> Option<serde_json::Value> {
    assets::cache_read(|a| a.sim_metadata.get(name).cloned()).flatten()
}
pub(crate) fn sim_metadata_insert(name: &str, value: serde_json::Value) {
    assets::cache_write(|a| {
        a.sim_metadata.insert(name.into(), value);
    });
}
