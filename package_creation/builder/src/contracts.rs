//! Version-one wire contracts. Unknown resource metadata remains JSON.
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub enum Operation {
    #[serde(rename = "upscale_prepare_input")]
    UpscalePrepareInput,
    #[serde(rename = "upscale_inspect_output")]
    UpscaleInspectOutput,
    #[serde(rename = "upscale_png")]
    UpscalePng,
    #[serde(rename = "build_hair")]
    BuildHair,
    #[serde(rename = "build_tattoo")]
    BuildTattoo,
    #[serde(rename = "capabilities")]
    Capabilities,
    #[serde(rename = "conversion_begin")]
    ConversionBegin,
    #[serde(rename = "conversion_finish")]
    ConversionFinish,
    #[serde(rename = "conversion_step")]
    ConversionStep,
    #[serde(rename = "conversion_validate")]
    ConversionValidate,
    #[serde(rename = "encode_texture")]
    EncodeTexture,
    #[serde(rename = "init")]
    Init,
    #[serde(rename = "inspect_packages")]
    InspectPackages,
    #[serde(rename = "inventory")]
    Inventory,
    #[serde(rename = "normalize_curve")]
    NormalizeCurve,
    #[serde(rename = "object_build")]
    ObjectBuild,
    #[serde(rename = "object_extract")]
    ObjectExtract,
    #[serde(rename = "object_inspect")]
    ObjectInspect,
    #[serde(rename = "object_layout")]
    ObjectLayout,
    #[serde(rename = "object_prepare")]
    ObjectPrepare,
    #[serde(rename = "object_preview")]
    ObjectPreview,
    #[serde(rename = "object_profile")]
    ObjectProfile,
    #[serde(rename = "object_readme")]
    ObjectReadme,
    #[serde(rename = "open_hair")]
    OpenHair,
    #[serde(rename = "painting_build")]
    PaintingBuild,
    #[serde(rename = "painting_compose")]
    PaintingCompose,
    #[serde(rename = "painting_inspect_image")]
    PaintingInspectImage,
    #[serde(rename = "painting_open")]
    PaintingOpen,
    #[serde(rename = "painting_prepare")]
    PaintingPrepare,
    #[serde(rename = "painting_validate")]
    PaintingValidate,
    #[serde(rename = "parse_curve")]
    ParseCurve,
    #[serde(rename = "prepare_hair_job")]
    PrepareHairJob,
    #[serde(rename = "prepare_tattoo_job")]
    PrepareTattooJob,
    #[serde(rename = "preview")]
    Preview,
    #[serde(rename = "process_pixels")]
    ProcessPixels,
    #[serde(rename = "readme")]
    Readme,
    #[serde(rename = "reset_inputs")]
    ResetInputs,
    #[serde(rename = "sim_align")]
    SimAlign,
    #[serde(rename = "sim_build")]
    SimBuild,
    #[serde(rename = "sim_experimental_build")]
    SimExperimentalBuild,
    #[serde(rename = "sim_fit")]
    SimFit,
    #[serde(rename = "sim_guided_fit")]
    SimGuidedFit,
    #[serde(rename = "sim_inspect_model")]
    SimInspectModel,
    #[serde(rename = "sim_inspect_scaffold")]
    SimInspectScaffold,
    #[serde(rename = "sim_landmarks")]
    SimLandmarks,
    #[serde(rename = "sim_preview")]
    SimPreview,
    #[serde(rename = "sim_prepare")]
    SimPrepare,
    #[serde(rename = "sim_reference")]
    SimReference,
    #[serde(rename = "sim_validate")]
    SimValidate,
    #[serde(rename = "validate_hair")]
    ValidateHair,
    #[serde(rename = "validate_tattoo")]
    ValidateTattoo,
}

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub struct EngineRequest {
    pub version: u32,
    pub op: Operation,
    #[serde(default)]
    pub params: Value,
}

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub struct EngineError {
    pub code: String,
    pub operation: String,
    pub message: String,
}

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub struct WorkerResponse {
    pub version: u32,
    pub id: u32,
    pub job: String,
    pub revision: u32,
    pub attempt: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<EngineError>,
}

pub fn capabilities() -> Value {
    let value: Value = serde_json::from_str(include_str!("runtime-capabilities.json"))
        .expect("Validated engine metadata");
    let _: RuntimeLimits =
        serde_json::from_value(value["limits"].clone()).expect("Validated engine limits");
    value
}

pub fn schemas() -> Value {
    serde_json::json!({"engine_request":schemars::schema_for!(EngineRequest),
        "worker_response":schemars::schema_for!(WorkerResponse), "saved_record":schemars::schema_for!(SavedRecord),
        "conversion_step":schemars::schema_for!(ConversionStep),
        "conversion_progress":schemars::schema_for!(ConversionProgress),
        "input_asset":schemars::schema_for!(InputAsset),
        "conversion_begin":schemars::schema_for!(ConversionBegin), "runtime_limits":schemars::schema_for!(RuntimeLimits)})
}

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub struct InputAsset {
    pub input: String,
}

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub struct ConversionBegin {
    pub input: String,
    pub mapping: String,
    pub body: String,
}

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub struct ConversionStep {
    #[schemars(range(min = 1, max = 8192))]
    pub pixels: u32,
}

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub struct ConversionProgress {
    pub done: bool,
    pub processed: usize,
    pub total: usize,
    pub progress: f64,
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rejects_unknown_operations_and_preserves_extra_metadata() {
        assert!(serde_json::from_value::<EngineRequest>(
            serde_json::json!({"version":1,"op":"unknown"})
        )
        .is_err());
        let request: EngineRequest = serde_json::from_value(
            serde_json::json!({"version":1,"op":"preview","params":{"unknown_property":42}}),
        )
        .unwrap();
        assert_eq!(request.params["unknown_property"], 42);
        assert_eq!(capabilities()["limits"]["hair"]["texture_slots"], 16);
    }
}

/// The saved envelope is validated without deserializing and reserializing its
/// opaque feature snapshots. Asset versions, unknown fields and numbers survive.
#[derive(Debug, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum JobKind {
    Conversion,
    Tattoo,
    Hair,
    Object,
    Painting,
    Sim,
}

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub struct StoredFile {
    pub name: String,
    pub filename: String,
    pub blob: String,
    pub sha256: String,
    pub size: u64,
}

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub struct SavedRecord {
    pub schema_version: u32,
    pub id: String,
    pub kind: JobKind,
    pub revision: u32,
    pub state: String,
    pub manifest: Value,
    #[serde(default)]
    pub files: Vec<StoredFile>,
    #[serde(default)]
    pub parameters: Value,
    #[serde(default)]
    pub ui: Value,
    #[serde(flatten)]
    pub extra: std::collections::BTreeMap<String, Value>,
}

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub struct SimLimits {
    pub files: u64,
    pub file_bytes: u64,
    pub input_bytes: u64,
    pub output_bytes: u64,
}

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub struct ConversionLimits {
    pub files: u64,
    pub input_bytes: u64,
    pub output_bytes: u64,
}

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub struct PaintingLimits {
    pub files: u64,
    pub input_bytes: u64,
    pub pixels: u64,
    pub dimension: u64,
    pub output_bytes: u64,
    pub formats: Vec<String>,
}

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub struct TattooLimits {
    pub entries: u64,
    pub files: u64,
    pub file_bytes: u64,
    pub input_bytes: u64,
    pub output_bytes: u64,
}

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub struct HairLimits {
    pub packages: u64,
    pub package_bytes: u64,
    pub input_bytes: u64,
    pub texture_slots: u64,
    pub builtin_colors: u64,
    pub custom_colors: u64,
    pub zip_bytes: u64,
}

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub struct ObjectLimits {
    pub packages: u64,
    pub package_bytes: u64,
    pub input_bytes: u64,
    pub output_bytes: u64,
    pub model_groups: u64,
    pub vertices_per_group: u64,
    pub texture_dimension: u64,
}

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub struct RuntimeLimits {
    pub sim: SimLimits,
    pub storage_bytes: u64,
    pub wasm_heap_bytes: u64,
    pub build_timeout_ms: u64,
    pub conversion: ConversionLimits,
    pub painting: PaintingLimits,
    pub tattoo: TattooLimits,
    pub hair: HairLimits,
    pub object: ObjectLimits,
}
