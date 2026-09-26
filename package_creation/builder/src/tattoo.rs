// SPDX-License-Identifier: GPL-3.0-or-later

use crate::assets as fs;
use std::collections::{BTreeMap, BTreeSet};
use std::io::Cursor;
use std::path::{Path, PathBuf};

use anyhow::{anyhow, bail, ensure, Context, Result};
use binrw::BinRead;
use clap::{Parser, Subcommand};
use dbpf::common::{BigString, ByteString};
use dbpf::header_v1::InstanceId;
use dbpf::internal_file::behaviour::behaviour_constants::BehaviourConstants;
use dbpf::internal_file::behaviour::behaviour_constants_labels::{
    BehaviourConstantsLabels, Label, LabelV1,
};
use dbpf::internal_file::cpf::{Data, CPF};
use dbpf::internal_file::resource_collection::material_definition::MaterialDefinition;
use dbpf::internal_file::resource_collection::texture_resource::decoded_texture::DecodedTexture;
use dbpf::internal_file::resource_collection::texture_resource::{TextureFormat, TextureResource};
use dbpf::internal_file::resource_collection::{ResourceCollection, ResourceData};
use dbpf::internal_file::sim_outfits::SimOutfits;
use dbpf::internal_file::text_list::{TaggedString, TextList, VersionedTextList};
use dbpf::internal_file::DecodedFile;
use dbpf::{DBPFFile, IndexEntry};
use image::GenericImageView;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

const OVERLAY_TEMPLATE_TGI_SHA256: &str =
    "61fcc04662d06320af04aac8d5d7b120d3d5f56b9e0301b41e3582d70a96c1cb";
const BOX_TEMPLATE_TGI_SHA256: &str =
    "c33cb91ae9d77c7ed1ca423e09c257565aee4604b72e81cf70a19b629eeffd3f";
const NO_FACE_TEMPLATE_TGI_SHA256: &str =
    "447de4f1dd97b8b579526b0f4e022e087b367977d874aeb47ad8042b8356b11d";
// SHA-256 values for the known-good Pick'N'Mix SportsPaint controller resources after
// replacing only generated scene names and numeric links with fixed audit tokens.
const BOX_CRES_NORMALIZED_SHA256: &str =
    "98b079d750cf5c5ef0cf0e7cb97d6af5582eba0f19239b66edb43610fe374f6b";
const BOX_SHPE_NORMALIZED_SHA256: &str =
    "4c290c13fa3afca1aa713ded4f33c7f5b444fc7a8b9d03f2ecfce8d03af0a051";
const BOX_GMND_NORMALIZED_SHA256: &str =
    "7373cad2476596776ed4da80c77c3bb8db27c161f2a7a20f652b14fecdbd694f";
const BOX_GMDC_NORMALIZED_SHA256: &str =
    "57ad12fa1661d8db3f4aa8cad66ed8970c3cf8fb0cf53aea174d4f19f6594d28";
const BOX_TXMT_NORMALIZED_SHA256: &str =
    "fff38d34d6f66c37452b55141a26a522c9c647c651408b51471861c9e304e836";
const BOX_TXTR_NORMALIZED_SHA256: &str =
    "360986c4166277b60391dc747bce037f70fb362f7791ff604183c08351426c07";
const OLD_BOX_SCENE_NAME: &str = "accessory-box-template";
const OLD_OVERLAY_SCENE_NAME: &str = "armsdense1";
const MULTI_CONTROLLER_MODEL: &str = "sculptureOshoKoma";
const MULTI_CONTROLLER_CATALOG_NAME: &str = "Osho Nuff Tablet";
const MULTI_CONTROLLER_REQUIRED_PRODUCT: &str =
    "Mansion & Garden Stuff or The Sims 2 Legacy Collection";
const SCENE_GROUP: u32 = 0x1C05_0000;
const COLLECTION_GROUP: u32 = 0x4F18_4AA9;

const BINX: u32 = 0x0C56_0F39;
const TXTR: u32 = 0x1C4A_276C;
const XTOL: u32 = 0x2C1F_D8A1;
const BCON: u32 = 0x4243_4F4E;
const BHAV: u32 = 0x4248_4156;
const CTSS: u32 = 0x4354_5353;
const TXMT: u32 = 0x4959_6978;
const OBJD: u32 = 0x4F42_4A44;
const STR: u32 = 0x5354_5223;
const TRCN: u32 = 0x5452_434E;
const COLL: u32 = 0x6C4F_359D;
const GZPS: u32 = 0xEBCF_3E27;
const IDR: u32 = 0xAC50_6764;
const NO_FACE_GROUP: u32 = 0x5F99_DAE1;
const NO_FACE_RESOURCE: u32 = 0x849C_E969;
const NO_FACE_INSTANCE: u32 = 0xFF31_355A;
const NO_FACE_TEXTURE_NAME: &str = "##0x5F99DAE1!uufaceoverlay-face";

#[derive(Parser)]
#[command(about = "Structural Sims 2 body-overlay package builder")]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    Build {
        #[arg(long)]
        job: PathBuf,
        #[arg(long)]
        overlay_template: PathBuf,
        #[arg(long)]
        box_template: PathBuf,
        #[arg(long)]
        png: PathBuf,
        #[arg(long)]
        overlay_output: PathBuf,
        #[arg(long)]
        box_output: PathBuf,
    },
    Validate {
        #[arg(long)]
        job: PathBuf,
        #[arg(long)]
        overlay_template: PathBuf,
        #[arg(long)]
        box_template: PathBuf,
        #[arg(long)]
        overlay: PathBuf,
        #[arg(long)]
        controller: PathBuf,
    },
    BuildMulti {
        #[arg(long)]
        job: PathBuf,
        #[arg(long)]
        overlay_template: PathBuf,
        #[arg(long)]
        box_template: PathBuf,
        #[arg(long)]
        output_dir: PathBuf,
    },
    ValidateMulti {
        #[arg(long)]
        job: PathBuf,
        #[arg(long)]
        overlay_template: PathBuf,
        #[arg(long)]
        box_template: PathBuf,
        #[arg(long)]
        output_dir: PathBuf,
    },
    BuildMerged {
        #[arg(long)]
        job: PathBuf,
        #[arg(long)]
        overlay_template: PathBuf,
        #[arg(long)]
        box_template: PathBuf,
        #[arg(long)]
        no_face_template: PathBuf,
        #[arg(long)]
        output: PathBuf,
    },
    ValidateMerged {
        #[arg(long)]
        job: PathBuf,
        #[arg(long)]
        overlay_template: PathBuf,
        #[arg(long)]
        box_template: PathBuf,
        #[arg(long)]
        no_face_template: PathBuf,
        #[arg(long)]
        package: PathBuf,
    },
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct Job {
    #[serde(default = "crate::package_compression::default_enabled")]
    refpack_compression: bool,
    #[serde(default)]
    texture_encoder: crate::texture_encoding::Encoder,
    schema_version: u32,
    slug: String,
    catalog_name: String,
    catalog_description: String,
    preset: String,
    ages: Vec<String>,
    priority: u32,
    input_png: String,
    identity: Identity,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct Identity {
    box_guid: String,
    overlay_group_id: String,
    family_uuid: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct MultiJob {
    #[serde(default = "crate::package_compression::default_enabled")]
    refpack_compression: bool,
    #[serde(default)]
    texture_encoder: crate::texture_encoding::Encoder,
    schema_version: u32,
    slug: String,
    catalog_name: String,
    catalog_description: String,
    ages: Vec<String>,
    compatibility: Compatibility,
    tattoos: Vec<MultiTattoo>,
    identity: MultiIdentity,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct Compatibility {
    plantsim: bool,
    vampire: bool,
    werewolf: bool,
    zombie: bool,
    servo: bool,
    bigfoot: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct MultiIdentity {
    box_guid: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct MultiTattoo {
    key: String,
    menu_label: String,
    menu_order: u32,
    layer_order: u32,
    priority: u32,
    input_pngs: GenderAssets,
    identity: TattooIdentity,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct GenderAssets {
    am: Option<String>,
    af: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct TattooIdentity {
    overlay_group_id: String,
    family_uuid: String,
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
struct Key {
    type_id: u32,
    group_id: u32,
    resource_id: u32,
    instance_id: u32,
}

#[derive(Debug, Serialize)]
struct ValidationReport {
    texture_encoding: serde_json::Value,
    schema_version: u32,
    status: &'static str,
    scope: &'static str,
    preset: String,
    overlay_group_id: String,
    box_guid: String,
    scene_graph_name: String,
    overlay_resource_counts: BTreeMap<String, usize>,
    controller_resource_counts: BTreeMap<String, usize>,
    txtr: TxtrReport,
    bhav: BhavReport,
    checks: Vec<&'static str>,
}

#[derive(Debug, Serialize)]
struct TxtrReport {
    format: &'static str,
    width: u32,
    height: u32,
    mip_levels: usize,
    decoded_alpha_min: u8,
    decoded_alpha_max: u8,
    alpha_mean_absolute_error: Option<f64>,
    alpha_max_absolute_error: Option<u8>,
}

#[derive(Debug, Serialize)]
struct BhavReport {
    count: usize,
    bytes_unchanged: bool,
}

#[derive(Debug, Serialize)]
struct MultiValidationReport {
    texture_encoding: serde_json::Value,
    schema_version: u32,
    status: &'static str,
    scope: &'static str,
    ages: Vec<String>,
    age_mask: u32,
    compatibility: &'static str,
    box_guid: String,
    scene_graph_name: String,
    controller_model_catalog_name: &'static str,
    controller_model_required_product: &'static str,
    controller_resource_counts: BTreeMap<String, usize>,
    tattoos: Vec<MultiTattooReport>,
    bhav: BhavReport,
    checks: Vec<&'static str>,
}

#[derive(Debug, Serialize)]
struct MergedValidationReport {
    package_compression: serde_json::Value,
    texture_encoding: serde_json::Value,
    schema_version: u32,
    status: &'static str,
    scope: &'static str,
    layout: &'static str,
    ages: Vec<String>,
    age_mask: u32,
    compatibility: &'static str,
    box_guid: String,
    scene_graph_name: String,
    controller_model_catalog_name: &'static str,
    controller_model_required_product: &'static str,
    logical_resource_count: usize,
    resource_counts: BTreeMap<String, usize>,
    controller_resource_counts: BTreeMap<String, usize>,
    tattoos: Vec<MultiTattooReport>,
    no_face_resource_count: usize,
    bhav: BhavReport,
    checks: Vec<&'static str>,
}

#[derive(Debug, Serialize)]
struct MultiTattooReport {
    key: String,
    overlay_group_id: String,
    genders: Vec<&'static str>,
    priority: u32,
    overlay_resource_counts: BTreeMap<String, usize>,
    txtr: BTreeMap<&'static str, TxtrReport>,
}

#[derive(Clone)]
struct ControllerEntry {
    label: String,
    group: u32,
    male: bool,
    female: bool,
    age_mask: u16,
}

#[derive(Clone)]
struct Names {
    box_scene: String,
    overlay_scene: String,
    body_prefix: &'static str,
    txmt_name: String,
    txtr_name: String,
    box_cres: String,
    box_shpe: String,
    box_gmnd: String,
    box_gmdc: String,
    box_txmt: String,
    box_txtr: String,
}

pub fn cli() -> Result<()> {
    match Cli::parse().command {
        Command::Build {
            job,
            overlay_template,
            box_template,
            png,
            overlay_output,
            box_output,
        } => {
            let job = load_job(&job)?;
            build(
                &job,
                &overlay_template,
                &box_template,
                &png,
                &overlay_output,
                &box_output,
            )?;
            let report = validate_bundle(
                &job,
                &overlay_template,
                &box_template,
                &overlay_output,
                &box_output,
            )?;
            println!("{}", serde_json::to_string_pretty(&report)?);
        }
        Command::Validate {
            job,
            overlay_template,
            box_template,
            overlay,
            controller,
        } => {
            let job = load_job(&job)?;
            let report = validate_bundle(
                &job,
                &overlay_template,
                &box_template,
                &overlay,
                &controller,
            )?;
            println!("{}", serde_json::to_string_pretty(&report)?);
        }
        Command::BuildMulti {
            job,
            overlay_template,
            box_template,
            output_dir,
        } => {
            let multi = load_multi_job(&job)?;
            build_multi(
                &multi,
                job.parent().unwrap_or_else(|| Path::new(".")),
                &overlay_template,
                &box_template,
                &output_dir,
            )?;
            let report = validate_multi_bundle(
                &multi,
                job.parent().unwrap_or_else(|| Path::new(".")),
                &overlay_template,
                &box_template,
                &output_dir,
            )?;
            println!("{}", serde_json::to_string_pretty(&report)?);
        }
        Command::ValidateMulti {
            job,
            overlay_template,
            box_template,
            output_dir,
        } => {
            let multi = load_multi_job(&job)?;
            let report = validate_multi_bundle(
                &multi,
                job.parent().unwrap_or_else(|| Path::new(".")),
                &overlay_template,
                &box_template,
                &output_dir,
            )?;
            println!("{}", serde_json::to_string_pretty(&report)?);
        }
        Command::BuildMerged {
            job,
            overlay_template,
            box_template,
            no_face_template,
            output,
        } => {
            let multi = load_multi_job(&job)?;
            build_merged(
                &multi,
                job.parent().unwrap_or_else(|| Path::new(".")),
                &overlay_template,
                &box_template,
                &no_face_template,
                &output,
            )?;
            let report = validate_merged(
                &multi,
                job.parent().unwrap_or_else(|| Path::new(".")),
                &overlay_template,
                &box_template,
                &no_face_template,
                &output,
            )?;
            println!("{}", serde_json::to_string_pretty(&report)?);
        }
        Command::ValidateMerged {
            job,
            overlay_template,
            box_template,
            no_face_template,
            package,
        } => {
            let multi = load_multi_job(&job)?;
            let report = validate_merged(
                &multi,
                job.parent().unwrap_or_else(|| Path::new(".")),
                &overlay_template,
                &box_template,
                &no_face_template,
                &package,
            )?;
            println!("{}", serde_json::to_string_pretty(&report)?);
        }
    }
    Ok(())
}

fn load_job(path: &Path) -> Result<Job> {
    let bytes = fs::read(path).with_context(|| format!("read job {}", path.display()))?;
    let job: Job = serde_json::from_slice(&bytes).context("parse job JSON")?;
    validate_job(&job)?;
    Ok(job)
}

fn validate_job(job: &Job) -> Result<()> {
    ensure!(job.schema_version == 1, "unsupported job schema version");
    ensure!(
        job.preset == "am" || job.preset == "af",
        "preset must be am or af"
    );
    ensure!(job.ages == ["adult"], "only adult age is supported");
    ensure!(
        (1..=i32::MAX as u32).contains(&job.priority),
        "priority is out of range"
    );
    ensure!(!job.slug.is_empty(), "slug is empty");
    ensure!(
        job.slug
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-'),
        "slug must contain only lowercase ASCII letters, digits, and hyphens"
    );
    let box_guid = parse_hex_id(&job.identity.box_guid, "box_guid")?;
    let overlay_group = parse_hex_id(&job.identity.overlay_group_id, "overlay_group_id")?;
    ensure!(
        box_guid != overlay_group,
        "box GUID and overlay group ID must differ"
    );
    ensure!(
        is_uuid(&job.identity.family_uuid),
        "family_uuid is malformed"
    );
    Ok(())
}

fn load_multi_job(path: &Path) -> Result<MultiJob> {
    let bytes = fs::read(path).with_context(|| format!("read job {}", path.display()))?;
    let job: MultiJob = serde_json::from_slice(&bytes).context("parse multi-job JSON")?;
    validate_multi_job(&job)?;
    Ok(job)
}

fn validate_multi_job(job: &MultiJob) -> Result<()> {
    ensure!(
        job.schema_version == 2,
        "unsupported multi-job schema version"
    );
    ensure!(
        job.ages == ["adult", "elder"],
        "multi-job ages must be adult and elder"
    );
    ensure!(
        !job.slug.is_empty() && job.slug.len() <= 48,
        "multi-job slug length is invalid"
    );
    ensure!(valid_slug(&job.slug), "multi-job slug is invalid");
    ensure!(!job.catalog_name.trim().is_empty(), "catalog name is empty");
    ensure!(job.catalog_name.len() <= 120, "catalog name is too long");
    ensure!(
        job.catalog_description.len() <= 1000,
        "catalog description is too long"
    );
    ensure!(
        !job.tattoos.is_empty() && job.tattoos.len() <= 20,
        "multi-job must contain 1 through 20 tattoos"
    );
    ensure!(
        !job.compatibility.plantsim
            && !job.compatibility.vampire
            && !job.compatibility.werewolf
            && !job.compatibility.zombie
            && !job.compatibility.servo
            && !job.compatibility.bigfoot,
        "multi-job must disable all supernatural compatibility"
    );

    let box_guid = parse_hex_id(&job.identity.box_guid, "box_guid")?;
    let expected_orders: BTreeSet<u32> = (0..job.tattoos.len() as u32).collect();
    let mut menu_orders = BTreeSet::new();
    let mut layer_orders = BTreeSet::new();
    let mut keys = BTreeSet::new();
    let mut labels = BTreeSet::new();
    let mut numeric_ids = BTreeSet::from([box_guid]);
    let mut families = BTreeSet::new();
    for tattoo in &job.tattoos {
        ensure!(
            valid_slug(&tattoo.key) && tattoo.key.len() <= 32,
            "tattoo key is invalid"
        );
        ensure!(keys.insert(tattoo.key.clone()), "duplicate tattoo key");
        ensure!(
            !tattoo.menu_label.trim().is_empty()
                && tattoo.menu_label.len() <= 64
                && !tattoo
                    .menu_label
                    .chars()
                    .any(|c| c.is_control() || c == '/' || c == '\\'),
            "tattoo menu label is invalid"
        );
        ensure!(
            labels.insert(tattoo.menu_label.to_lowercase()),
            "duplicate tattoo menu label"
        );
        menu_orders.insert(tattoo.menu_order);
        layer_orders.insert(tattoo.layer_order);
        ensure!(
            tattoo.priority == 0x65 + tattoo.layer_order,
            "tattoo priority does not match layer order"
        );
        ensure!(
            tattoo.input_pngs.am.is_some() || tattoo.input_pngs.af.is_some(),
            "tattoo must include AM, AF, or both"
        );
        for value in [&tattoo.input_pngs.am, &tattoo.input_pngs.af]
            .into_iter()
            .flatten()
        {
            ensure!(
                !value.is_empty() && !Path::new(value).is_absolute(),
                "input PNG path must be relative"
            );
            ensure!(
                !Path::new(value)
                    .components()
                    .any(|part| matches!(part, std::path::Component::ParentDir)),
                "input PNG path must not escape the job directory"
            );
        }
        let group = parse_hex_id(&tattoo.identity.overlay_group_id, "overlay_group_id")?;
        ensure!(
            numeric_ids.insert(group),
            "duplicate controller or overlay identifier"
        );
        ensure!(
            is_uuid(&tattoo.identity.family_uuid),
            "family_uuid is malformed"
        );
        ensure!(
            families.insert(tattoo.identity.family_uuid.clone()),
            "duplicate family_uuid"
        );
    }
    ensure!(
        menu_orders == expected_orders,
        "menu_order values must be contiguous"
    );
    ensure!(
        layer_orders == expected_orders,
        "layer_order values must be contiguous"
    );
    Ok(())
}

fn valid_slug(value: &str) -> bool {
    !value.starts_with('-')
        && !value.ends_with('-')
        && !value.contains("--")
        && value
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
}

fn parse_hex_id(text: &str, field: &str) -> Result<u32> {
    ensure!(
        text.len() == 10 && text.starts_with("0x"),
        "{field} must be 0x plus eight hex digits"
    );
    let value = u32::from_str_radix(&text[2..], 16).with_context(|| format!("parse {field}"))?;
    ensure!(
        !matches!(
            value,
            0 | u32::MAX | SCENE_GROUP | COLLECTION_GROUP | 0x5F2D_415B | 0x5F99_DAE1
        ),
        "{field} uses a zero or reserved identifier"
    );
    Ok(value)
}

fn is_uuid(value: &str) -> bool {
    let bytes = value.as_bytes();
    bytes.len() == 36
        && [8, 13, 18, 23].iter().all(|&i| bytes[i] == b'-')
        && bytes
            .iter()
            .enumerate()
            .all(|(i, b)| [8, 13, 18, 23].contains(&i) || b.is_ascii_hexdigit())
        && value != "00000000-0000-0000-0000-000000000000"
}

fn build(
    job: &Job,
    overlay_template: &Path,
    box_template: &Path,
    png: &Path,
    overlay_output: &Path,
    box_output: &Path,
) -> Result<()> {
    ensure!(!fs::exists(overlay_output), "overlay output already exists");
    ensure!(!fs::exists(box_output), "controller output already exists");
    let names = names(job);

    let (mut overlay, mut overlay_reader) = open_package(overlay_template)?;
    ensure!(
        tgi_set_sha256(&overlay) == OVERLAY_TEMPLATE_TGI_SHA256,
        "overlay template starting TGI set is not the pinned set"
    );
    mutate_overlay(&mut overlay, &mut overlay_reader, job, &names, png)?;
    write_package(
        &mut overlay,
        &mut overlay_reader,
        overlay_output,
        job.refpack_compression,
    )?;

    let (mut controller, mut controller_reader) = open_package(box_template)?;
    ensure!(
        tgi_set_sha256(&controller) == BOX_TEMPLATE_TGI_SHA256,
        "controller template starting TGI set is not the pinned set"
    );
    mutate_controller(&mut controller, &mut controller_reader, job, &names)?;
    write_package(
        &mut controller,
        &mut controller_reader,
        box_output,
        job.refpack_compression,
    )?;
    Ok(())
}

fn names(job: &Job) -> Names {
    derive_names(
        &job.slug,
        &job.slug,
        &job.identity.overlay_group_id,
        &job.preset,
    )
}

fn multi_controller_names(job: &MultiJob) -> Names {
    derive_names(&job.slug, &job.slug, &job.identity.box_guid, "am")
}

fn multi_tattoo_names(job: &MultiJob, tattoo: &MultiTattoo, preset: &str) -> Names {
    derive_names(
        &job.slug,
        &tattoo.key,
        &tattoo.identity.overlay_group_id,
        preset,
    )
}

fn derive_names(box_slug: &str, overlay_slug: &str, marker: &str, preset: &str) -> Names {
    let mut digest = Sha256::new();
    digest.update(overlay_slug.as_bytes());
    digest.update(marker.as_bytes());
    let hex = format!("{:x}", digest.finalize());
    let box_alnum: String = box_slug
        .chars()
        .filter(|c| c.is_ascii_alphanumeric())
        .collect();
    let mut prefix = box_alnum.chars().take(6).collect::<String>();
    while prefix.len() < 6 {
        prefix.push('x');
    }
    let box_scene = format!("ts2box-{prefix}-{}", &hex[..8]);
    let overlay_alnum: String = overlay_slug
        .chars()
        .filter(|c| c.is_ascii_alphanumeric())
        .collect();
    let mut overlay_prefix = overlay_alnum.chars().take(5).collect::<String>();
    while overlay_prefix.len() < 5 {
        overlay_prefix.push('x');
    }
    let overlay_scene = format!("{overlay_prefix}{}", &hex[..5]);
    let body_prefix = if preset == "am" { "um" } else { "uf" };
    let txmt_name = format!("{body_prefix}bodynakedoverlay_{overlay_scene}_txmt");
    let txtr_name = format!("{body_prefix}bodynakedoverlay-{overlay_scene}_txtr");
    Names {
        box_cres: format!("{box_scene}_cres"),
        box_shpe: format!("{box_scene}_shpe"),
        box_gmnd: format!("{box_scene}_gmnd"),
        box_gmdc: format!("{box_scene}_gmdc"),
        box_txmt: format!("{box_scene}_surface_txmt"),
        box_txtr: format!("{box_scene}_crate_txtr"),
        box_scene,
        overlay_scene,
        body_prefix,
        txmt_name,
        txtr_name,
    }
}

fn open_package(path: &Path) -> Result<(DBPFFile, Cursor<Vec<u8>>)> {
    let bytes = fs::read(path).with_context(|| format!("read package {}", path.display()))?;
    let mut reader = Cursor::new(bytes);
    let package = DBPFFile::read(&mut reader)
        .with_context(|| format!("parse DBPF package {}", path.display()))?;
    Ok((package, reader))
}

fn write_package(
    package: &mut DBPFFile,
    reader: &mut Cursor<Vec<u8>>,
    path: &Path,
    enabled: bool,
) -> Result<()> {
    fs::write(
        path,
        crate::package_compression::serialize(package, reader, enabled)?,
    )?;
    Ok(())
}

fn key(entry: &IndexEntry) -> Key {
    Key {
        type_id: entry.type_id.code(),
        group_id: entry.group_id,
        resource_id: (entry.instance_id.id >> 32) as u32,
        instance_id: entry.instance_id.id as u32,
    }
}

fn set_key(entry: &mut IndexEntry, group: u32, resource: u32, instance: u32) {
    entry.group_id = group;
    entry.instance_id = InstanceId {
        id: ((resource as u64) << 32) | instance as u64,
    };
}

fn find_entry_mut(package: &mut DBPFFile, wanted: Key) -> Result<&mut IndexEntry> {
    let mut matches = package
        .index
        .iter_mut()
        .filter(|entry| key(entry) == wanted);
    let entry = matches
        .next()
        .ok_or_else(|| anyhow!("missing resource {}", key_text(wanted)))?;
    ensure!(
        matches.next().is_none(),
        "duplicate resource {}",
        key_text(wanted)
    );
    Ok(entry)
}

fn key_text(key: Key) -> String {
    format!(
        "{:08X}:{:08X}:{:08X}:{:08X}",
        key.type_id, key.group_id, key.resource_id, key.instance_id
    )
}

fn old_key(type_id: u32, group_id: u32, resource_id: u32, instance_id: u32) -> Key {
    Key {
        type_id,
        group_id,
        resource_id,
        instance_id,
    }
}

fn mutate_overlay(
    package: &mut DBPFFile,
    reader: &mut Cursor<Vec<u8>>,
    job: &Job,
    names: &Names,
    png: &Path,
) -> Result<()> {
    let selected = if job.preset == "am" { 1 } else { 2 };
    let body_txmt_old = if selected == 1 {
        old_key(TXMT, u32::MAX, 0x7596_136A, 0xFFA1_04EE)
    } else {
        old_key(TXMT, u32::MAX, 0xC156_0464, 0xFF3E_3FF0)
    };
    let body_txtr_old = if selected == 1 {
        old_key(TXTR, u32::MAX, 0xAD10_7835, 0xFFAE_95F9)
    } else {
        old_key(TXTR, u32::MAX, 0x19D0_6F3B, 0xFF31_AEE7)
    };

    let keep = BTreeSet::from([
        old_key(COLL, COLLECTION_GROUP, 0, u32::MAX),
        old_key(IDR, COLLECTION_GROUP, 0, u32::MAX),
        old_key(STR, u32::MAX, 0, 1),
        old_key(BINX, u32::MAX, 0, selected),
        old_key(GZPS, u32::MAX, 0, selected),
        old_key(IDR, u32::MAX, 0, selected),
        body_txmt_old,
        body_txtr_old,
        old_key(BINX, u32::MAX, 0, 0x10),
        old_key(XTOL, u32::MAX, 0, 0x10),
        old_key(IDR, u32::MAX, 0, 0x10),
        old_key(TXMT, u32::MAX, 0x1EF6_7347, 0xFF08_76DD),
    ]);
    for wanted in &keep {
        ensure!(
            package.index.iter().any(|entry| key(entry) == *wanted),
            "missing template resource {}",
            key_text(*wanted)
        );
    }
    package.index.retain(|entry| keep.contains(&key(entry)));
    ensure!(
        package.index.len() == 12,
        "overlay branch selection did not produce 12 resources"
    );

    let group = parse_hex_id(&job.identity.overlay_group_id, "overlay_group_id")?;
    find_entry_mut(package, old_key(COLL, COLLECTION_GROUP, 0, u32::MAX))?.instance_id =
        InstanceId { id: group as u64 };

    mutate_idr(
        find_entry_mut(package, old_key(IDR, COLLECTION_GROUP, 0, u32::MAX))?,
        reader,
        |idr| {
            ensure!(idr.entries.len() == 2, "unexpected collection 3IDR layout");
            idr.entries[1].group_id = group;
            Ok(())
        },
    )?;
    find_entry_mut(package, old_key(IDR, COLLECTION_GROUP, 0, u32::MAX))?.instance_id =
        InstanceId { id: group as u64 };

    let str_entry = find_entry_mut(package, old_key(STR, u32::MAX, 0, 1))?;
    str_entry.group_id = group;
    mutate_text_list(str_entry, reader, |list| {
        let sets = tagged_sets_mut(list)?;
        ensure!(!sets.is_empty(), "overlay STR has no strings");
        sets[0].value = job.catalog_name.clone().into();
        Ok(())
    })?;

    find_entry_mut(package, old_key(BINX, u32::MAX, 0, selected))?.group_id = group;
    let gzps_entry = find_entry_mut(package, old_key(GZPS, u32::MAX, 0, selected))?;
    gzps_entry.group_id = group;
    let expected_gender = if job.preset == "am" { 2 } else { 1 };
    let gzps = decoded_mut(gzps_entry, reader)?;
    let DecodedFile::PropertySet(gzps) = gzps else {
        bail!("selected GZPS did not decode as a property set")
    };
    ensure!(
        gzps.gender == expected_gender,
        "template GZPS gender is unexpected"
    );
    ensure!(gzps.age == 0x58, "template GZPS age mask is unexpected");
    gzps.age = 0x08;
    gzps.gender = expected_gender;
    gzps.priority = Some(job.priority);
    gzps.name = format!(
        "##0x{group:08x}!{}bodynakedoverlay_{}",
        names.body_prefix, names.overlay_scene
    )
    .into();
    gzps.family = job.identity.family_uuid.clone().into();

    mutate_idr(
        find_entry_mut(package, old_key(IDR, u32::MAX, 0, selected))?,
        reader,
        |idr| {
            ensure!(idr.entries.len() == 7, "unexpected body 3IDR layout");
            ensure!(
                idr.entries[6].type_id.code() == TXMT,
                "unexpected body 3IDR TXMT slot"
            );
            idr.entries[1].instance_id = InstanceId { id: group as u64 };
            idr.entries[2].group_id = group;
            idr.entries[3].group_id = group;
            idr.entries[6].group_id = group;
            idr.entries[6].instance_id = InstanceId {
                id: ((resource_id_hash(&names.txmt_name) as u64) << 32)
                    | instance_id_hash(&names.txmt_name) as u64,
            };
            Ok(())
        },
    )?;
    find_entry_mut(package, old_key(IDR, u32::MAX, 0, selected))?.group_id = group;

    let txmt_entry = find_entry_mut(package, body_txmt_old)?;
    mutate_material(txmt_entry, reader, |material| {
        ensure!(
            material.properties.len() == 14,
            "unexpected body TXMT property layout"
        );
        material.file_name.name = format!("##0x{group:08x}!{}", names.txmt_name).into();
        material.material_description = format!(
            "##0x{group:08x}!{}bodynakedoverlay_{}",
            names.body_prefix, names.overlay_scene
        )
        .into();
        set_material_property(
            material,
            "stdMatBaseTextureName",
            &format!(
                "##0x{group:08x}!{}bodynakedoverlay-{}",
                names.body_prefix, names.overlay_scene
            ),
        )?;
        for item in &mut material.names {
            replace_big(item, "0xffffffff", &format!("0x{group:08x}"));
            replace_big(item, OLD_OVERLAY_SCENE_NAME, &names.overlay_scene);
        }
        Ok(())
    })?;
    set_key(
        txmt_entry,
        group,
        resource_id_hash(&names.txmt_name),
        instance_id_hash(&names.txmt_name),
    );

    let txtr_entry = find_entry_mut(package, body_txtr_old)?;
    let image =
        crate::assets::open_image(png).with_context(|| format!("decode PNG {}", png.display()))?;
    ensure!(
        image.dimensions() == (1024, 1024),
        "PNG dimensions must be 1024 by 1024"
    );
    ensure!(
        matches!(
            image.color(),
            image::ColorType::Rgba8 | image::ColorType::Rgba16
        ),
        "PNG must contain RGBA pixels"
    );
    let rgba = image.into_rgba8();
    ensure!(
        rgba.pixels().any(|pixel| pixel[3] != 0),
        "PNG alpha channel is blank"
    );
    mutate_texture(txtr_entry, reader, |texture| {
        texture.file_name.name = names.txtr_name.clone().into();
        texture.compress_replace(
            DecodedTexture {
                width: 1024,
                height: 1024,
                data: rgba.into_raw(),
            },
            Some(TextureFormat::RawARGB32),
        );
        texture.add_max_mip_levels(Some(127));
        ensure!(
            texture.mip_levels() == 11,
            "failed to generate 11 TXTR mip levels"
        );
        crate::texture_encoding::recompress(texture, TextureFormat::DXT5, job.texture_encoder)?;
        Ok(())
    })?;
    set_key(
        txtr_entry,
        group,
        resource_id_hash(&names.txtr_name),
        instance_id_hash(&names.txtr_name),
    );

    find_entry_mut(package, old_key(BINX, u32::MAX, 0, 0x10))?.group_id = group;
    let xtol_entry = find_entry_mut(package, old_key(XTOL, u32::MAX, 0, 0x10))?;
    xtol_entry.group_id = group;
    let DecodedFile::GenericCPF(xtol) = decoded_mut(xtol_entry, reader)? else {
        bail!("no-face XTOL did not decode as CPF")
    };
    set_cpf_string(xtol, "name", &format!("##0x{group:08x}!uufaceoverlay_face"))?;
    set_cpf_string(xtol, "family", &job.identity.family_uuid)?;

    mutate_idr(
        find_entry_mut(package, old_key(IDR, u32::MAX, 0, 0x10))?,
        reader,
        |idr| {
            ensure!(idr.entries.len() == 5, "unexpected no-face 3IDR layout");
            idr.entries[1].instance_id = InstanceId { id: group as u64 };
            idr.entries[2].group_id = group;
            idr.entries[3].group_id = group;
            idr.entries[4].group_id = group;
            Ok(())
        },
    )?;
    find_entry_mut(package, old_key(IDR, u32::MAX, 0, 0x10))?.group_id = group;

    let noface_txmt = find_entry_mut(package, old_key(TXMT, u32::MAX, 0x1EF6_7347, 0xFF08_76DD))?;
    mutate_material(noface_txmt, reader, |material| {
        configure_no_face_material(material, group)
    })?;
    noface_txmt.group_id = group;
    ensure_unique_tgis(package)?;
    Ok(())
}

fn multi_controller_filename(job: &MultiJob) -> String {
    format!("{}_OverlayBox.package", job.slug)
}

fn multi_overlay_filename(job: &MultiJob, tattoo: &MultiTattoo) -> String {
    format!("{}_{}_Overlay.package", job.slug, tattoo.key)
}

fn no_face_key() -> Key {
    old_key(TXTR, NO_FACE_GROUP, NO_FACE_RESOURCE, NO_FACE_INSTANCE)
}

fn expected_merged_resource_count(job: &MultiJob) -> usize {
    36 + 1
        + job
            .tattoos
            .iter()
            .map(|tattoo| {
                let genders = usize::from(tattoo.input_pngs.am.is_some())
                    + usize::from(tattoo.input_pngs.af.is_some());
                7 + 5 * genders
            })
            .sum::<usize>()
}

fn append_materialized_entries(
    target: &mut DBPFFile,
    mut source: DBPFFile,
    source_reader: &mut Cursor<Vec<u8>>,
) -> Result<()> {
    for entry in &mut source.index {
        entry
            .data(source_reader)
            .context("materialize resource before merged DBPF write")?;
    }
    target.index.extend(source.index);
    Ok(())
}

fn build_merged(
    job: &MultiJob,
    job_dir: &Path,
    overlay_template: &Path,
    box_template: &Path,
    no_face_template: &Path,
    output: &Path,
) -> Result<()> {
    ensure!(!fs::exists(output), "merged output already exists");

    let (mut merged, mut merged_reader) = open_package(box_template)?;
    ensure!(
        tgi_set_sha256(&merged) == BOX_TEMPLATE_TGI_SHA256,
        "controller template starting TGI set is not the pinned set"
    );
    let mut ordered: Vec<&MultiTattoo> = job.tattoos.iter().collect();
    ordered.sort_by_key(|tattoo| tattoo.menu_order);
    let entries: Vec<ControllerEntry> = ordered
        .iter()
        .map(|tattoo| {
            Ok(ControllerEntry {
                label: tattoo.menu_label.clone(),
                group: parse_hex_id(&tattoo.identity.overlay_group_id, "overlay_group_id")?,
                male: tattoo.input_pngs.am.is_some(),
                female: tattoo.input_pngs.af.is_some(),
                age_mask: 0x18,
            })
        })
        .collect::<Result<Vec<_>>>()?;
    let names = multi_controller_names(job);
    mutate_controller_common(
        &mut merged,
        &mut merged_reader,
        &job.slug,
        &job.catalog_name,
        &job.catalog_description,
        parse_hex_id(&job.identity.box_guid, "box_guid")?,
        &entries,
        [0, 0, 0, 0, 0, 0],
        &names,
        Some(MULTI_CONTROLLER_MODEL),
    )?;

    for tattoo in &job.tattoos {
        let (mut overlay, mut overlay_reader) = open_package(overlay_template)?;
        ensure!(
            tgi_set_sha256(&overlay) == OVERLAY_TEMPLATE_TGI_SHA256,
            "overlay template starting TGI set is not the pinned set"
        );
        mutate_multi_overlay(&mut overlay, &mut overlay_reader, job, tattoo, job_dir)?;
        append_materialized_entries(&mut merged, overlay, &mut overlay_reader)?;
    }

    let (no_face, mut no_face_reader) = open_package(no_face_template)?;
    ensure!(
        tgi_set_sha256(&no_face) == NO_FACE_TEMPLATE_TGI_SHA256,
        "no-face template starting TGI set is not the pinned set"
    );
    ensure!(
        no_face.index.len() == 1 && key(&no_face.index[0]) == no_face_key(),
        "no-face template resource set changed"
    );
    append_materialized_entries(&mut merged, no_face, &mut no_face_reader)?;

    ensure_unique_tgis(&merged)?;
    ensure!(
        merged.index.len() == expected_merged_resource_count(job),
        "merged resource count is incorrect"
    );
    write_package(
        &mut merged,
        &mut merged_reader,
        output,
        job.refpack_compression,
    )
}

fn build_multi(
    job: &MultiJob,
    job_dir: &Path,
    overlay_template: &Path,
    box_template: &Path,
    output_dir: &Path,
) -> Result<()> {
    ensure!(output_dir.is_dir(), "multi output directory does not exist");

    for tattoo in &job.tattoos {
        let output = output_dir.join(multi_overlay_filename(job, tattoo));
        ensure!(!fs::exists(&output), "overlay output already exists");
        let (mut overlay, mut overlay_reader) = open_package(overlay_template)?;
        ensure!(
            tgi_set_sha256(&overlay) == OVERLAY_TEMPLATE_TGI_SHA256,
            "overlay template starting TGI set is not the pinned set"
        );
        mutate_multi_overlay(&mut overlay, &mut overlay_reader, job, tattoo, job_dir)?;
        write_package(
            &mut overlay,
            &mut overlay_reader,
            &output,
            job.refpack_compression,
        )?;
    }

    let controller_output = output_dir.join(multi_controller_filename(job));
    ensure!(
        !fs::exists(&controller_output),
        "controller output already exists"
    );
    let (mut controller, mut controller_reader) = open_package(box_template)?;
    ensure!(
        tgi_set_sha256(&controller) == BOX_TEMPLATE_TGI_SHA256,
        "controller template starting TGI set is not the pinned set"
    );
    let mut ordered: Vec<&MultiTattoo> = job.tattoos.iter().collect();
    ordered.sort_by_key(|tattoo| tattoo.menu_order);
    let entries: Vec<ControllerEntry> = ordered
        .iter()
        .map(|tattoo| {
            Ok(ControllerEntry {
                label: tattoo.menu_label.clone(),
                group: parse_hex_id(&tattoo.identity.overlay_group_id, "overlay_group_id")?,
                male: tattoo.input_pngs.am.is_some(),
                female: tattoo.input_pngs.af.is_some(),
                age_mask: 0x18,
            })
        })
        .collect::<Result<Vec<_>>>()?;
    let names = multi_controller_names(job);
    mutate_controller_common(
        &mut controller,
        &mut controller_reader,
        &job.slug,
        &job.catalog_name,
        &job.catalog_description,
        parse_hex_id(&job.identity.box_guid, "box_guid")?,
        &entries,
        [0, 0, 0, 0, 0, 0],
        &names,
        Some(MULTI_CONTROLLER_MODEL),
    )?;
    write_package(
        &mut controller,
        &mut controller_reader,
        &controller_output,
        job.refpack_compression,
    )?;
    Ok(())
}

fn mutate_multi_overlay(
    package: &mut DBPFFile,
    reader: &mut Cursor<Vec<u8>>,
    job: &MultiJob,
    tattoo: &MultiTattoo,
    job_dir: &Path,
) -> Result<()> {
    let group = parse_hex_id(&tattoo.identity.overlay_group_id, "overlay_group_id")?;
    let mut keep = BTreeSet::from([
        old_key(COLL, COLLECTION_GROUP, 0, u32::MAX),
        old_key(IDR, COLLECTION_GROUP, 0, u32::MAX),
        old_key(STR, u32::MAX, 0, 1),
        old_key(BINX, u32::MAX, 0, 0x10),
        old_key(XTOL, u32::MAX, 0, 0x10),
        old_key(IDR, u32::MAX, 0, 0x10),
        old_key(TXMT, u32::MAX, 0x1EF6_7347, 0xFF08_76DD),
    ]);
    if tattoo.input_pngs.am.is_some() {
        keep.extend([
            old_key(BINX, u32::MAX, 0, 1),
            old_key(GZPS, u32::MAX, 0, 1),
            old_key(IDR, u32::MAX, 0, 1),
            old_key(TXMT, u32::MAX, 0x7596_136A, 0xFFA1_04EE),
            old_key(TXTR, u32::MAX, 0xAD10_7835, 0xFFAE_95F9),
        ]);
    }
    if tattoo.input_pngs.af.is_some() {
        keep.extend([
            old_key(BINX, u32::MAX, 0, 2),
            old_key(GZPS, u32::MAX, 0, 2),
            old_key(IDR, u32::MAX, 0, 2),
            old_key(TXMT, u32::MAX, 0xC156_0464, 0xFF3E_3FF0),
            old_key(TXTR, u32::MAX, 0x19D0_6F3B, 0xFF31_AEE7),
        ]);
    }
    for wanted in &keep {
        ensure!(
            package.index.iter().any(|entry| key(entry) == *wanted),
            "missing template resource {}",
            key_text(*wanted)
        );
    }
    package.index.retain(|entry| keep.contains(&key(entry)));
    let gender_count =
        usize::from(tattoo.input_pngs.am.is_some()) + usize::from(tattoo.input_pngs.af.is_some());
    ensure!(
        package.index.len() == 7 + 5 * gender_count,
        "overlay branch selection produced an unexpected resource count"
    );

    find_entry_mut(package, old_key(COLL, COLLECTION_GROUP, 0, u32::MAX))?.instance_id =
        InstanceId { id: group as u64 };
    mutate_idr(
        find_entry_mut(package, old_key(IDR, COLLECTION_GROUP, 0, u32::MAX))?,
        reader,
        |idr| {
            ensure!(idr.entries.len() == 2, "unexpected collection 3IDR layout");
            idr.entries[1].group_id = group;
            Ok(())
        },
    )?;
    find_entry_mut(package, old_key(IDR, COLLECTION_GROUP, 0, u32::MAX))?.instance_id =
        InstanceId { id: group as u64 };

    let str_entry = find_entry_mut(package, old_key(STR, u32::MAX, 0, 1))?;
    str_entry.group_id = group;
    mutate_text_list(str_entry, reader, |list| {
        let sets = tagged_sets_mut(list)?;
        ensure!(!sets.is_empty(), "overlay STR has no strings");
        sets[0].value = tattoo.menu_label.clone().into();
        Ok(())
    })?;

    if let Some(path) = &tattoo.input_pngs.am {
        mutate_multi_body_branch(package, reader, job, tattoo, "am", &job_dir.join(path))?;
    }
    if let Some(path) = &tattoo.input_pngs.af {
        mutate_multi_body_branch(package, reader, job, tattoo, "af", &job_dir.join(path))?;
    }

    find_entry_mut(package, old_key(BINX, u32::MAX, 0, 0x10))?.group_id = group;
    let xtol_entry = find_entry_mut(package, old_key(XTOL, u32::MAX, 0, 0x10))?;
    xtol_entry.group_id = group;
    let DecodedFile::GenericCPF(xtol) = decoded_mut(xtol_entry, reader)? else {
        bail!("no-face XTOL did not decode as CPF")
    };
    set_cpf_string(xtol, "name", &format!("##0x{group:08x}!uufaceoverlay_face"))?;
    set_cpf_string(xtol, "family", &tattoo.identity.family_uuid)?;

    mutate_idr(
        find_entry_mut(package, old_key(IDR, u32::MAX, 0, 0x10))?,
        reader,
        |idr| {
            ensure!(idr.entries.len() == 5, "unexpected no-face 3IDR layout");
            idr.entries[1].instance_id = InstanceId { id: group as u64 };
            idr.entries[2].group_id = group;
            idr.entries[3].group_id = group;
            idr.entries[4].group_id = group;
            Ok(())
        },
    )?;
    find_entry_mut(package, old_key(IDR, u32::MAX, 0, 0x10))?.group_id = group;

    let noface_txmt = find_entry_mut(package, old_key(TXMT, u32::MAX, 0x1EF6_7347, 0xFF08_76DD))?;
    mutate_material(noface_txmt, reader, |material| {
        configure_no_face_material(material, group)
    })?;
    noface_txmt.group_id = group;
    ensure_unique_tgis(package)?;
    Ok(())
}

fn configure_no_face_material(material: &mut MaterialDefinition, group: u32) -> Result<()> {
    ensure!(
        material.properties.len() == 14,
        "unexpected no-face TXMT property layout"
    );
    let name = format!("##0x{group:08x}!uufaceoverlay_face");
    material.file_name.name = format!("{name}_txmt").into();
    // Match the upstream filename assignment's material-description update.
    material.material_description = name.into();
    material.names = vec![NO_FACE_TEXTURE_NAME.into()];
    validate_no_face_material(material, group)
}

fn validate_no_face_material(material: &MaterialDefinition, group: u32) -> Result<()> {
    let name = format!("##0x{group:08x}!uufaceoverlay_face");
    ensure!(
        material.file_name.name.to_string() == format!("{name}_txmt"),
        "generated no-face TXMT name is incorrect"
    );
    ensure!(
        material.material_description.to_string() == name,
        "generated no-face TXMT material description is incorrect"
    );
    let textures: Vec<_> = material
        .properties
        .iter()
        .filter(|property| property.name.to_string() == "stdMatBaseTextureName")
        .collect();
    ensure!(
        textures.len() == 1
            && textures[0]
                .value
                .to_string()
                .eq_ignore_ascii_case(NO_FACE_TEXTURE_NAME),
        "generated no-face TXMT does not use the shared transparent texture"
    );
    ensure!(
        material.names.len() == 1
            && material.names[0]
                .to_string()
                .eq_ignore_ascii_case(NO_FACE_TEXTURE_NAME),
        "generated no-face TXMT texture file list is incorrect"
    );
    Ok(())
}

fn mutate_multi_body_branch(
    package: &mut DBPFFile,
    reader: &mut Cursor<Vec<u8>>,
    job: &MultiJob,
    tattoo: &MultiTattoo,
    preset: &str,
    png: &Path,
) -> Result<()> {
    let (selected, expected_gender, txmt_old, txtr_old) = if preset == "am" {
        (
            1,
            2,
            old_key(TXMT, u32::MAX, 0x7596_136A, 0xFFA1_04EE),
            old_key(TXTR, u32::MAX, 0xAD10_7835, 0xFFAE_95F9),
        )
    } else {
        (
            2,
            1,
            old_key(TXMT, u32::MAX, 0xC156_0464, 0xFF3E_3FF0),
            old_key(TXTR, u32::MAX, 0x19D0_6F3B, 0xFF31_AEE7),
        )
    };
    let group = parse_hex_id(&tattoo.identity.overlay_group_id, "overlay_group_id")?;
    let names = multi_tattoo_names(job, tattoo, preset);

    find_entry_mut(package, old_key(BINX, u32::MAX, 0, selected))?.group_id = group;
    let gzps_entry = find_entry_mut(package, old_key(GZPS, u32::MAX, 0, selected))?;
    gzps_entry.group_id = group;
    let DecodedFile::PropertySet(gzps) = decoded_mut(gzps_entry, reader)? else {
        bail!("selected GZPS did not decode as a property set")
    };
    ensure!(
        gzps.gender == expected_gender,
        "template GZPS gender is unexpected"
    );
    ensure!(gzps.age == 0x58, "template GZPS age mask is unexpected");
    gzps.age = 0x18;
    gzps.gender = expected_gender;
    gzps.priority = Some(tattoo.priority);
    gzps.name = format!(
        "##0x{group:08x}!{}bodynakedoverlay_{}",
        names.body_prefix, names.overlay_scene
    )
    .into();
    gzps.family = tattoo.identity.family_uuid.clone().into();

    mutate_idr(
        find_entry_mut(package, old_key(IDR, u32::MAX, 0, selected))?,
        reader,
        |idr| {
            ensure!(idr.entries.len() == 7, "unexpected body 3IDR layout");
            ensure!(
                idr.entries[6].type_id.code() == TXMT,
                "unexpected body 3IDR TXMT slot"
            );
            idr.entries[1].instance_id = InstanceId { id: group as u64 };
            idr.entries[2].group_id = group;
            idr.entries[3].group_id = group;
            idr.entries[6].group_id = group;
            idr.entries[6].instance_id = InstanceId {
                id: ((resource_id_hash(&names.txmt_name) as u64) << 32)
                    | instance_id_hash(&names.txmt_name) as u64,
            };
            Ok(())
        },
    )?;
    find_entry_mut(package, old_key(IDR, u32::MAX, 0, selected))?.group_id = group;

    let txmt_entry = find_entry_mut(package, txmt_old)?;
    mutate_material(txmt_entry, reader, |material| {
        ensure!(
            material.properties.len() == 14,
            "unexpected body TXMT property layout"
        );
        material.file_name.name = format!("##0x{group:08x}!{}", names.txmt_name).into();
        material.material_description = format!(
            "##0x{group:08x}!{}bodynakedoverlay_{}",
            names.body_prefix, names.overlay_scene
        )
        .into();
        set_material_property(
            material,
            "stdMatBaseTextureName",
            &format!(
                "##0x{group:08x}!{}bodynakedoverlay-{}",
                names.body_prefix, names.overlay_scene
            ),
        )?;
        for item in &mut material.names {
            replace_big(item, "0xffffffff", &format!("0x{group:08x}"));
            replace_big(item, OLD_OVERLAY_SCENE_NAME, &names.overlay_scene);
        }
        Ok(())
    })?;
    set_key(
        txmt_entry,
        group,
        resource_id_hash(&names.txmt_name),
        instance_id_hash(&names.txmt_name),
    );

    let txtr_entry = find_entry_mut(package, txtr_old)?;
    let image =
        crate::assets::open_image(png).with_context(|| format!("decode PNG {}", png.display()))?;
    ensure!(
        image.dimensions() == (1024, 1024),
        "PNG dimensions must be 1024 by 1024"
    );
    ensure!(
        image.color() == image::ColorType::Rgba8,
        "PNG must contain RGBA8 pixels"
    );
    let rgba = image.into_rgba8();
    ensure!(
        rgba.pixels().any(|pixel| pixel[3] != 0),
        "PNG alpha channel is blank"
    );
    mutate_texture(txtr_entry, reader, |texture| {
        texture.file_name.name = names.txtr_name.clone().into();
        texture.compress_replace(
            DecodedTexture {
                width: 1024,
                height: 1024,
                data: rgba.into_raw(),
            },
            Some(TextureFormat::RawARGB32),
        );
        texture.add_max_mip_levels(Some(127));
        ensure!(
            texture.mip_levels() == 11,
            "failed to generate 11 TXTR mip levels"
        );
        crate::texture_encoding::recompress(texture, TextureFormat::DXT5, job.texture_encoder)?;
        Ok(())
    })?;
    set_key(
        txtr_entry,
        group,
        resource_id_hash(&names.txtr_name),
        instance_id_hash(&names.txtr_name),
    );
    Ok(())
}

fn mutate_controller(
    package: &mut DBPFFile,
    reader: &mut Cursor<Vec<u8>>,
    job: &Job,
    names: &Names,
) -> Result<()> {
    let group = parse_hex_id(&job.identity.overlay_group_id, "overlay_group_id")?;
    let entry = ControllerEntry {
        label: job.catalog_name.clone(),
        group,
        male: job.preset == "am",
        female: job.preset == "af",
        age_mask: 0x08,
    };
    mutate_controller_common(
        package,
        reader,
        &job.slug,
        &job.catalog_name,
        &job.catalog_description,
        parse_hex_id(&job.identity.box_guid, "box_guid")?,
        &[entry],
        [1, 1, 1, 1, 1, 1],
        names,
        None,
    )
}

// This matches the controller resource contract shared by the native wrapper.
#[allow(clippy::too_many_arguments)]
fn mutate_controller_common(
    package: &mut DBPFFile,
    reader: &mut Cursor<Vec<u8>>,
    slug: &str,
    catalog_name: &str,
    catalog_description: &str,
    box_guid: u32,
    entries: &[ControllerEntry],
    compatibility: [u16; 6],
    names: &Names,
    external_model: Option<&str>,
) -> Result<()> {
    if external_model.is_none() {
        ensure!(
            names.box_scene.len() == OLD_BOX_SCENE_NAME.len(),
            "internal box scene name length changed"
        );
    }

    let objd_entry = find_entry_mut(package, old_key(OBJD, u32::MAX, 0, 0x41A7))?;
    let DecodedFile::ObjectData(objd) = decoded_mut(objd_entry, reader)? else {
        bail!("OBJD did not decode as object data")
    };
    ensure!(objd.guid == 0xDF30_0865, "template OBJD GUID is unexpected");
    let object_name = format!("{slug} Overlay Box");
    ensure!(object_name.len() < 64, "internal object name is too long");
    objd.guid = box_guid;
    objd.file_name.name = object_name.clone().into();
    objd.file_name_2 = ByteString::from(object_name);

    mutate_text_list(
        find_entry_mut(package, old_key(CTSS, u32::MAX, 0, 0x07D0))?,
        reader,
        |list| {
            let sets = tagged_sets_mut(list)?;
            ensure!(sets.len() == 2, "unexpected CTSS layout");
            sets[0].value = catalog_name.into();
            sets[1].value = format!("{catalog_name}\n\n{catalog_description}").into();
            Ok(())
        },
    )?;
    mutate_text_list(
        find_entry_mut(package, old_key(STR, u32::MAX, 0, 0x85))?,
        reader,
        |list| {
            let sets = tagged_sets_mut(list)?;
            ensure!(sets.len() == 2, "unexpected model-name STR layout");
            sets[1].value = external_model
                .map(str::to_owned)
                .unwrap_or_else(|| format!("##0x{SCENE_GROUP:08X}!{}", names.box_scene))
                .into();
            Ok(())
        },
    )?;
    mutate_text_list(
        find_entry_mut(package, old_key(STR, u32::MAX, 0, 0x12E))?,
        reader,
        |list| {
            let sets = tagged_sets_mut(list)?;
            ensure!(sets.is_empty(), "template menu STR is not empty");
            for entry in entries {
                sets.push(TaggedString {
                    value: format!("Add.../{}", entry.label).into(),
                    ..Default::default()
                });
                sets.push(TaggedString {
                    value: format!("Remove.../{}", entry.label).into(),
                    ..Default::default()
                });
            }
            Ok(())
        },
    )?;

    set_bcon(package, reader, 0x1000, vec![entries.len() as u16])?;
    let mut collection_words = Vec::with_capacity(entries.len() * 2);
    let mut gender_flags = Vec::with_capacity(entries.len() * 2);
    let mut age_masks = Vec::with_capacity(entries.len() * 2);
    let mut group_codes = Vec::with_capacity(entries.len());
    let mut collection_labels = Vec::with_capacity(entries.len() * 2);
    let mut gender_labels = Vec::with_capacity(entries.len() * 2);
    let mut age_labels = Vec::with_capacity(entries.len() * 2);
    let mut group_labels = Vec::with_capacity(entries.len());
    for entry in entries {
        collection_words.extend([entry.group as u16, (entry.group >> 16) as u16]);
        gender_flags.extend([u16::from(entry.male), u16::from(entry.female)]);
        age_masks.extend([entry.age_mask, entry.age_mask]);
        group_codes.push(0);
        collection_labels.push(format!("{} (last/loword)", entry.label));
        collection_labels.push(format!("{} (first/hiword)", entry.label));
        gender_labels.push(format!("{} (male)", entry.label));
        gender_labels.push(format!("{} (female)", entry.label));
        age_labels.push(format!("{} (male age mask)", entry.label));
        age_labels.push(format!("{} (female age mask)", entry.label));
        group_labels.push(format!("{} (group code)", entry.label));
    }
    set_bcon(package, reader, 0x1001, collection_words)?;
    set_bcon(package, reader, 0x1002, gender_flags)?;
    set_bcon(package, reader, 0x1003, age_masks)?;
    set_bcon(package, reader, 0x1004, compatibility.to_vec())?;
    set_bcon(package, reader, 0x1005, group_codes)?;
    set_bcon(package, reader, 0x1006, vec![0])?;

    let collection_label_refs: Vec<&str> = collection_labels.iter().map(String::as_str).collect();
    let gender_label_refs: Vec<&str> = gender_labels.iter().map(String::as_str).collect();
    let age_label_refs: Vec<&str> = age_labels.iter().map(String::as_str).collect();
    let group_label_refs: Vec<&str> = group_labels.iter().map(String::as_str).collect();
    set_trcn(package, reader, 0x1000, &["Tattoo count"])?;
    set_trcn(package, reader, 0x1001, &collection_label_refs)?;
    set_trcn(package, reader, 0x1002, &gender_label_refs)?;
    set_trcn(package, reader, 0x1003, &age_label_refs)?;
    set_trcn(
        package,
        reader,
        0x1004,
        &[
            "PlantSim", "Vampire", "Werewolf", "Zombie", "Servo", "Bigfoot",
        ],
    )?;
    set_trcn(package, reader, 0x1005, &group_label_refs)?;
    set_trcn(package, reader, 0x1006, &[""])?;

    if external_model.is_some() {
        remove_template_controller_scene(package)?;
        ensure_unique_tgis(package)?;
        return Ok(());
    }

    patch_controller_scene_names(package, reader, names)?;
    patch_scene_link(
        find_entry_mut(
            package,
            old_key(0xE519_C933, SCENE_GROUP, 0x053B_9122, 0xFF67_A0B1),
        )?,
        reader,
        0xEF1B_53CB,
        resource_id_hash(&names.box_shpe),
    )?;
    patch_scene_link(
        find_entry_mut(
            package,
            old_key(0xE519_C933, SCENE_GROUP, 0x053B_9122, 0xFF67_A0B1),
        )?,
        reader,
        0xFFDA_FE75,
        instance_id_hash(&names.box_shpe),
    )?;
    patch_scene_link(
        find_entry_mut(
            package,
            old_key(0x7BA3_838C, SCENE_GROUP, 0x194E_56A6, 0xFF75_2B07),
        )?,
        reader,
        0x2DB3_DBFE,
        resource_id_hash(&names.box_gmdc),
    )?;
    patch_scene_link(
        find_entry_mut(
            package,
            old_key(0x7BA3_838C, SCENE_GROUP, 0x194E_56A6, 0xFF75_2B07),
        )?,
        reader,
        0xFF1F_A936,
        instance_id_hash(&names.box_gmdc),
    )?;

    let mappings = [
        (
            0xE519_C933,
            0x053B_9122,
            0xFF67_A0B1,
            names.box_cres.as_str(),
        ),
        (
            0xFC6E_B1F7,
            0xEF1B_53CB,
            0xFFDA_FE75,
            names.box_shpe.as_str(),
        ),
        (
            0x7BA3_838C,
            0x194E_56A6,
            0xFF75_2B07,
            names.box_gmnd.as_str(),
        ),
        (
            0xAC4F_8687,
            0x2DB3_DBFE,
            0xFF1F_A936,
            names.box_gmdc.as_str(),
        ),
    ];
    for (resource_type, old_resource, old_instance, new_name) in mappings {
        set_key(
            find_entry_mut(
                package,
                old_key(resource_type, SCENE_GROUP, old_resource, old_instance),
            )?,
            SCENE_GROUP,
            resource_id_hash(new_name),
            instance_id_hash(new_name),
        );
    }

    let txmt_entry = find_entry_mut(
        package,
        old_key(TXMT, SCENE_GROUP, 0x96C2_6BE3, 0xFF7B_3B79),
    )?;
    mutate_material(txmt_entry, reader, |material| {
        ensure!(
            material.properties.len() == 41,
            "unexpected controller TXMT property layout"
        );
        replace_material_strings(material, OLD_BOX_SCENE_NAME, &names.box_scene);
        replace_material_strings(
            material,
            &format!("{}-crate", names.box_scene),
            &format!("{}_crate", names.box_scene),
        );
        set_material_property(
            material,
            "stdMatBaseTextureName",
            &format!("##0x{SCENE_GROUP:08X}!{}_crate", names.box_scene),
        )?;
        Ok(())
    })?;
    set_key(
        txmt_entry,
        SCENE_GROUP,
        resource_id_hash(&names.box_txmt),
        instance_id_hash(&names.box_txmt),
    );

    let txtr_entry = find_entry_mut(
        package,
        old_key(TXTR, SCENE_GROUP, 0x0E04_23B3, 0xFFF9_29E1),
    )?;
    mutate_texture(txtr_entry, reader, |texture| {
        texture.file_name.name = names.box_txtr.clone().into();
        Ok(())
    })?;
    set_key(
        txtr_entry,
        SCENE_GROUP,
        resource_id_hash(&names.box_txtr),
        instance_id_hash(&names.box_txtr),
    );

    ensure_unique_tgis(package)?;
    Ok(())
}

fn template_controller_scene_keys() -> BTreeSet<Key> {
    BTreeSet::from([
        old_key(0xE519_C933, SCENE_GROUP, 0x053B_9122, 0xFF67_A0B1),
        old_key(0xFC6E_B1F7, SCENE_GROUP, 0xEF1B_53CB, 0xFFDA_FE75),
        old_key(0x7BA3_838C, SCENE_GROUP, 0x194E_56A6, 0xFF75_2B07),
        old_key(0xAC4F_8687, SCENE_GROUP, 0x2DB3_DBFE, 0xFF1F_A936),
        old_key(TXMT, SCENE_GROUP, 0x96C2_6BE3, 0xFF7B_3B79),
        old_key(TXTR, SCENE_GROUP, 0x0E04_23B3, 0xFFF9_29E1),
    ])
}

fn remove_template_controller_scene(package: &mut DBPFFile) -> Result<()> {
    let scene_keys = template_controller_scene_keys();
    let before = package.index.len();
    package
        .index
        .retain(|entry| !scene_keys.contains(&key(entry)));
    ensure!(
        before - package.index.len() == scene_keys.len(),
        "controller template scene resource set changed"
    );
    Ok(())
}

fn decoded_mut<'a>(
    entry: &'a mut IndexEntry,
    reader: &mut Cursor<Vec<u8>>,
) -> Result<&'a mut DecodedFile> {
    let entry_key = key(entry);
    entry
        .data(reader)?
        .decoded()?
        .ok_or_else(|| anyhow!("resource {} has no typed decoder", key_text(entry_key)))
}

fn mutate_idr(
    entry: &mut IndexEntry,
    reader: &mut Cursor<Vec<u8>>,
    update: impl FnOnce(&mut SimOutfits) -> Result<()>,
) -> Result<()> {
    let DecodedFile::SimOutfits(idr) = decoded_mut(entry, reader)? else {
        bail!("3IDR did not decode as an ID reference file")
    };
    update(idr)
}

fn mutate_text_list(
    entry: &mut IndexEntry,
    reader: &mut Cursor<Vec<u8>>,
    update: impl FnOnce(&mut TextList) -> Result<()>,
) -> Result<()> {
    let DecodedFile::TextList(list) = decoded_mut(entry, reader)? else {
        bail!("string resource did not decode as a text list")
    };
    update(list)
}

fn tagged_sets_mut(list: &mut TextList) -> Result<&mut Vec<TaggedString>> {
    let VersionedTextList::Tagged { sets, .. } = &mut list.data else {
        bail!("expected a tagged text list")
    };
    Ok(sets)
}

fn mutate_material(
    entry: &mut IndexEntry,
    reader: &mut Cursor<Vec<u8>>,
    update: impl FnOnce(&mut MaterialDefinition) -> Result<()>,
) -> Result<()> {
    let DecodedFile::ResourceCollection(collection) = decoded_mut(entry, reader)? else {
        bail!("TXMT did not decode as a resource collection")
    };
    let material = one_material_mut(collection)?;
    update(material)
}

fn one_material_mut(collection: &mut ResourceCollection) -> Result<&mut MaterialDefinition> {
    ensure!(
        collection.entries.len() == 1,
        "unexpected TXMT resource-collection layout"
    );
    let ResourceData::Material(material) = &mut collection.entries[0].data else {
        bail!("TXMT collection does not contain a material definition")
    };
    Ok(material)
}

fn mutate_texture(
    entry: &mut IndexEntry,
    reader: &mut Cursor<Vec<u8>>,
    update: impl FnOnce(&mut TextureResource) -> Result<()>,
) -> Result<()> {
    let DecodedFile::ResourceCollection(collection) = decoded_mut(entry, reader)? else {
        bail!("TXTR did not decode as a resource collection")
    };
    ensure!(
        collection.entries.len() == 1,
        "unexpected TXTR resource-collection layout"
    );
    let ResourceData::Texture(texture) = &mut collection.entries[0].data else {
        bail!("TXTR collection does not contain a texture")
    };
    update(texture)
}

fn set_material_property(material: &mut MaterialDefinition, name: &str, value: &str) -> Result<()> {
    let matches: Vec<_> = material
        .properties
        .iter_mut()
        .filter(|property| property.name.to_string() == name)
        .collect();
    ensure!(
        matches.len() == 1,
        "TXMT property {name} is missing or duplicated"
    );
    matches.into_iter().next().unwrap().value = value.into();
    Ok(())
}

fn replace_material_strings(material: &mut MaterialDefinition, from: &str, to: &str) {
    replace_big(&mut material.file_name.name, from, to);
    replace_big(&mut material.material_description, from, to);
    replace_big(&mut material.material_type, from, to);
    for property in &mut material.properties {
        replace_big(&mut property.name, from, to);
        replace_big(&mut property.value, from, to);
    }
    for name in &mut material.names {
        replace_big(name, from, to);
    }
}

fn replace_big(value: &mut BigString, from: &str, to: &str) {
    let current = value.to_string();
    if current.contains(from) {
        *value = current.replace(from, to).into();
    }
}

fn set_cpf_string(cpf: &mut CPF, name: &str, value: &str) -> Result<()> {
    let mut matches = cpf
        .entries
        .iter_mut()
        .filter(|item| item.name.data == name.as_bytes());
    let item = matches
        .next()
        .ok_or_else(|| anyhow!("CPF string key {name} is missing"))?;
    ensure!(
        matches.next().is_none(),
        "CPF string key {name} is duplicated"
    );
    let Data::String(text) = &mut item.data else {
        bail!("CPF key {name} is not a string")
    };
    *text = value.into();
    Ok(())
}

fn set_bcon(
    package: &mut DBPFFile,
    reader: &mut Cursor<Vec<u8>>,
    instance: u32,
    values: Vec<u16>,
) -> Result<()> {
    let entry = find_entry_mut(package, old_key(BCON, u32::MAX, 0, instance))?;
    let DecodedFile::BehaviourConstants(BehaviourConstants { constants, .. }) =
        decoded_mut(entry, reader)?
    else {
        bail!("BCON did not decode as constants")
    };
    *constants = values;
    Ok(())
}

fn set_trcn(
    package: &mut DBPFFile,
    reader: &mut Cursor<Vec<u8>>,
    instance: u32,
    values: &[&str],
) -> Result<()> {
    let entry = find_entry_mut(package, old_key(TRCN, u32::MAX, 0, instance))?;
    let DecodedFile::BehaviourConstantsLabels(BehaviourConstantsLabels { labels, .. }) =
        decoded_mut(entry, reader)?
    else {
        bail!("TRCN did not decode as constant labels")
    };
    *labels = values
        .iter()
        .enumerate()
        .map(|(id, value)| {
            Label::V1(LabelV1 {
                used: 0,
                id: id as u32,
                name: (*value).into(),
                default: 0,
                min: 0,
                max: 0,
            })
        })
        .collect();
    Ok(())
}

fn encoded_big_string(value: &str) -> Vec<u8> {
    let mut length = value.len();
    let mut encoded = Vec::with_capacity(value.len() + 2);
    loop {
        let byte = (length & 0x7F) as u8;
        length >>= 7;
        encoded.push(byte | if length > 0 { 0x80 } else { 0 });
        if length == 0 {
            break;
        }
    }
    encoded.extend_from_slice(value.as_bytes());
    encoded
}

fn replace_big_string(
    bytes: &mut Vec<u8>,
    from: &str,
    to: &str,
    expected_count: usize,
) -> Result<()> {
    let from_encoded = encoded_big_string(from);
    let to_encoded = encoded_big_string(to);
    let positions: Vec<usize> = bytes
        .windows(from_encoded.len())
        .enumerate()
        .filter_map(|(index, window)| (window == from_encoded).then_some(index))
        .collect();
    ensure!(
        positions.len() == expected_count,
        "scene string occurrence count changed for {from}, expected {expected_count}, found {}",
        positions.len()
    );
    for position in positions.into_iter().rev() {
        bytes.splice(
            position..position + from_encoded.len(),
            to_encoded.iter().copied(),
        );
    }
    Ok(())
}

fn set_empty_big_string_after(bytes: &mut Vec<u8>, marker: &[u8], value: &str) -> Result<()> {
    let positions: Vec<usize> = bytes
        .windows(marker.len())
        .enumerate()
        .filter_map(|(index, window)| (window == marker).then_some(index))
        .collect();
    ensure!(
        positions.len() == 1,
        "shape resource filename marker count changed, found {}",
        positions.len()
    );
    let position = positions[0] + marker.len();
    ensure!(
        bytes.get(position) == Some(&0),
        "shape resource filename is not the expected empty big string"
    );
    bytes.splice(position..position + 1, encoded_big_string(value));
    Ok(())
}

fn patch_controller_scene_names(
    package: &mut DBPFFile,
    reader: &mut Cursor<Vec<u8>>,
    names: &Names,
) -> Result<()> {
    let cres = find_entry_mut(
        package,
        old_key(0xE519_C933, SCENE_GROUP, 0x053B_9122, 0xFF67_A0B1),
    )?;
    let raw = cres.data(reader)?.decompressed()?;
    replace_big_string(
        &mut raw.data,
        "accessory-box-template_cres",
        &names.box_cres,
        1,
    )?;
    replace_big_string(
        &mut raw.data,
        "##0x1c050000!accessory-box-template_cres",
        &names.box_cres,
        1,
    )?;

    let shpe = find_entry_mut(
        package,
        old_key(0xFC6E_B1F7, SCENE_GROUP, 0xEF1B_53CB, 0xFFDA_FE75),
    )?;
    let raw = shpe.data(reader)?.decompressed()?;
    replace_big_string(
        &mut raw.data,
        "accessory-box-template_root_rot_shpe",
        &names.box_shpe,
        1,
    )?;
    const SHAPE_RESOURCE_NAME_MARKER: &[u8] = b"\x10cObjectGraphNode\0\0\0\0\x04\0\0\0\0\0\0\0";
    set_empty_big_string_after(&mut raw.data, SHAPE_RESOURCE_NAME_MARKER, &names.box_shpe)?;
    replace_big_string(
        &mut raw.data,
        "##0x1C050000!accessory-box-template_root_rot_gmnd",
        &format!("##0x{SCENE_GROUP:08X}!{}", names.box_gmnd),
        1,
    )?;
    replace_big_string(
        &mut raw.data,
        "##0x1C050000!accessory-box-template_surface",
        &format!("##0x{SCENE_GROUP:08X}!{}_surface", names.box_scene),
        1,
    )?;

    let gmnd = find_entry_mut(
        package,
        old_key(0x7BA3_838C, SCENE_GROUP, 0x194E_56A6, 0xFF75_2B07),
    )?;
    let raw = gmnd.data(reader)?.decompressed()?;
    replace_big_string(
        &mut raw.data,
        "##0x1c050000!accessory-box-template_root_rot_gmnd",
        &names.box_gmnd,
        1,
    )?;
    replace_big_string(
        &mut raw.data,
        "accessory-box-template_root_rot_gmnd",
        &names.box_gmnd,
        1,
    )?;

    let gmdc = find_entry_mut(
        package,
        old_key(0xAC4F_8687, SCENE_GROUP, 0x2DB3_DBFE, 0xFF1F_A936),
    )?;
    let raw = gmdc.data(reader)?.decompressed()?;
    replace_big_string(
        &mut raw.data,
        "accessory-box-template_root_rot_gmdc",
        &names.box_gmdc,
        1,
    )?;
    Ok(())
}

fn replace_equal_len(
    bytes: &mut [u8],
    from: &[u8],
    to: &[u8],
    expected_count: usize,
) -> Result<()> {
    ensure!(
        from.len() == to.len(),
        "narrow raw string replacement changed length"
    );
    let positions: Vec<usize> = bytes
        .windows(from.len())
        .enumerate()
        .filter_map(|(index, window)| (window == from).then_some(index))
        .collect();
    ensure!(
        positions.len() == expected_count,
        "raw string occurrence count changed, expected {expected_count}, found {}",
        positions.len()
    );
    for position in positions {
        bytes[position..position + to.len()].copy_from_slice(to);
    }
    Ok(())
}

fn patch_scene_link(
    entry: &mut IndexEntry,
    reader: &mut Cursor<Vec<u8>>,
    old: u32,
    new: u32,
) -> Result<()> {
    let raw = entry.data(reader)?.decompressed()?;
    replace_equal_len(&mut raw.data, &old.to_le_bytes(), &new.to_le_bytes(), 1)
}

fn validate_bundle(
    job: &Job,
    overlay_template: &Path,
    box_template: &Path,
    overlay_path: &Path,
    controller_path: &Path,
) -> Result<ValidationReport> {
    let names = names(job);
    let group = parse_hex_id(&job.identity.overlay_group_id, "overlay_group_id")?;
    let box_guid = parse_hex_id(&job.identity.box_guid, "box_guid")?;
    let (mut overlay, mut overlay_reader) = open_package(overlay_path)?;
    let (mut controller, mut controller_reader) = open_package(controller_path)?;
    ensure_unique_tgis(&overlay)?;
    ensure_unique_tgis(&controller)?;
    ensure!(
        overlay.index.len() == 12,
        "generated overlay must contain 12 resources"
    );
    ensure!(
        controller.index.len() == 42,
        "generated controller must contain 42 resources"
    );

    let selected_instance = if job.preset == "am" { 1 } else { 2 };
    let expected_gender = if job.preset == "am" { 2 } else { 1 };
    let gzps_entry = find_entry_mut(&mut overlay, old_key(GZPS, group, 0, selected_instance))?;
    let DecodedFile::PropertySet(gzps) = decoded_mut(gzps_entry, &mut overlay_reader)? else {
        bail!("generated GZPS did not decode")
    };
    ensure!(gzps.age == 0x08, "generated GZPS is not adult only");
    ensure!(
        gzps.gender == expected_gender,
        "generated GZPS gender is incorrect"
    );
    ensure!(
        gzps.priority == Some(job.priority),
        "generated GZPS priority is incorrect"
    );
    ensure!(
        gzps.family.data == job.identity.family_uuid.as_bytes(),
        "generated GZPS family is incorrect"
    );

    let txmt_key = old_key(
        TXMT,
        group,
        resource_id_hash(&names.txmt_name),
        instance_id_hash(&names.txmt_name),
    );
    let txmt_entry = find_entry_mut(&mut overlay, txmt_key)?;
    mutate_material(txmt_entry, &mut overlay_reader, |material| {
        ensure!(
            material.properties.len() == 14,
            "generated body TXMT layout changed"
        );
        let expected = format!(
            "##0x{group:08x}!{}bodynakedoverlay-{}",
            names.body_prefix, names.overlay_scene
        );
        let value = material
            .properties
            .iter()
            .find(|property| property.name.to_string() == "stdMatBaseTextureName")
            .ok_or_else(|| anyhow!("generated TXMT has no base texture property"))?;
        ensure!(
            value.value.to_string() == expected,
            "generated TXMT base texture link is incorrect"
        );
        Ok(())
    })?;

    let txtr_key = old_key(
        TXTR,
        group,
        resource_id_hash(&names.txtr_name),
        instance_id_hash(&names.txtr_name),
    );
    let txtr_entry = find_entry_mut(&mut overlay, txtr_key)?;
    let txtr_report = validate_txtr(txtr_entry, &mut overlay_reader, None)?;

    let idr_entry = find_entry_mut(&mut overlay, old_key(IDR, group, 0, selected_instance))?;
    mutate_idr(idr_entry, &mut overlay_reader, |idr| {
        ensure!(idr.entries.len() == 7, "generated body 3IDR layout changed");
        ensure!(
            idr.entries[6].group_id == group,
            "generated body 3IDR group link is incorrect"
        );
        ensure!(
            idr.entries[6].instance_id.id
                == ((resource_id_hash(&names.txmt_name) as u64) << 32)
                    | instance_id_hash(&names.txmt_name) as u64,
            "generated body 3IDR TXMT link is incorrect"
        );
        Ok(())
    })?;

    let xtol_entry = find_entry_mut(&mut overlay, old_key(XTOL, group, 0, 0x10))?;
    let DecodedFile::GenericCPF(xtol) = decoded_mut(xtol_entry, &mut overlay_reader)? else {
        bail!("generated no-face XTOL did not decode")
    };
    ensure!(
        cpf_string(xtol, "family")? == job.identity.family_uuid,
        "generated no-face family is incorrect"
    );
    let noface_txmt = find_entry_mut(&mut overlay, old_key(TXMT, group, 0x1EF6_7347, 0xFF08_76DD))?;
    mutate_material(noface_txmt, &mut overlay_reader, |material| {
        validate_no_face_material(material, group)
    })?;

    let objd_entry = find_entry_mut(&mut controller, old_key(OBJD, u32::MAX, 0, 0x41A7))?;
    let DecodedFile::ObjectData(objd) = decoded_mut(objd_entry, &mut controller_reader)? else {
        bail!("generated controller OBJD did not decode")
    };
    ensure!(
        objd.guid == box_guid,
        "generated controller GUID is incorrect"
    );
    let expected_object_name = format!("{} Overlay Box", job.slug);
    ensure!(
        objd.file_name.name.to_string() == expected_object_name
            && objd.file_name_2.to_string() == expected_object_name,
        "generated controller object name is incorrect"
    );

    let ctss_entry = find_entry_mut(&mut controller, old_key(CTSS, u32::MAX, 0, 0x07D0))?;
    let DecodedFile::TextList(ctss) = decoded_mut(ctss_entry, &mut controller_reader)? else {
        bail!("generated controller CTSS did not decode")
    };
    let VersionedTextList::Tagged {
        sets: ctss_sets, ..
    } = &ctss.data
    else {
        bail!("generated controller CTSS has an unexpected layout")
    };
    ensure!(
        ctss_sets.len() == 2
            && ctss_sets[0].value.to_string() == job.catalog_name
            && ctss_sets[1].value.to_string()
                == format!("{}\n\n{}", job.catalog_name, job.catalog_description),
        "generated controller catalog strings are incorrect"
    );

    let model_entry = find_entry_mut(&mut controller, old_key(STR, u32::MAX, 0, 0x85))?;
    let DecodedFile::TextList(model_list) = decoded_mut(model_entry, &mut controller_reader)?
    else {
        bail!("generated controller model STR did not decode")
    };
    let VersionedTextList::Tagged {
        sets: model_sets, ..
    } = &model_list.data
    else {
        bail!("generated controller model STR has an unexpected layout")
    };
    ensure!(
        model_sets.len() == 2
            && model_sets[1].value.to_string()
                == format!("##0x{SCENE_GROUP:08X}!{}", names.box_scene),
        "generated controller model string is incorrect"
    );
    validate_bcon(&mut controller, &mut controller_reader, 0x1000, &[1])?;
    validate_bcon(
        &mut controller,
        &mut controller_reader,
        0x1001,
        &[group as u16, (group >> 16) as u16],
    )?;
    validate_bcon(
        &mut controller,
        &mut controller_reader,
        0x1002,
        if job.preset == "am" { &[1, 0] } else { &[0, 1] },
    )?;
    validate_bcon(&mut controller, &mut controller_reader, 0x1003, &[8, 8])?;

    validate_controller_scene(&mut controller, &mut controller_reader, &names)?;

    let template_bhav = bhav_hashes(box_template)?;
    let generated_bhav = bhav_hashes(controller_path)?;
    ensure!(
        template_bhav == generated_bhav,
        "controller BHAV bytes changed"
    );
    ensure!(
        template_bhav.len() == 13,
        "pinned template BHAV count changed"
    );
    let template_graphic = texture_dds_sha256(
        box_template,
        old_key(TXTR, SCENE_GROUP, 0x0E04_23B3, 0xFFF9_29E1),
    )?;
    let generated_graphic = texture_dds_sha256(
        controller_path,
        old_key(
            TXTR,
            SCENE_GROUP,
            resource_id_hash(&names.box_txtr),
            instance_id_hash(&names.box_txtr),
        ),
    )?;
    ensure!(
        template_graphic == generated_graphic,
        "controller box graphic pixels changed"
    );

    let (template_overlay, _) = open_package(overlay_template)?;
    let (template_box, _) = open_package(box_template)?;
    ensure!(
        tgi_set_sha256(&template_overlay) == OVERLAY_TEMPLATE_TGI_SHA256,
        "overlay template TGI set changed"
    );
    ensure!(
        tgi_set_sha256(&template_box) == BOX_TEMPLATE_TGI_SHA256,
        "controller template TGI set changed"
    );

    Ok(ValidationReport {
        texture_encoding: job.texture_encoder.usage(&[TextureFormat::DXT5]),
        schema_version: 1,
        status: "pass",
        scope: "structural-only",
        preset: job.preset.clone(),
        overlay_group_id: job.identity.overlay_group_id.clone(),
        box_guid: job.identity.box_guid.clone(),
        scene_graph_name: names.box_scene,
        overlay_resource_counts: resource_counts(&overlay),
        controller_resource_counts: resource_counts(&controller),
        txtr: txtr_report,
        bhav: BhavReport {
            count: template_bhav.len(),
            bytes_unchanged: true,
        },
        checks: vec![
            "pinned-template-tgi-sets",
            "unique-generated-tgis",
            "body-and-no-face-branches",
            "adult-gender-and-identity-metadata",
            "3idr-and-txmt-links",
            "dxt5-eleven-level-txtr",
            "controller-guid-bcon-and-complete-scenegraph-links",
            "picknmix-controller-scenegraph-name-conventions",
            "exact-known-good-controller-scene-fingerprints",
            "unchanged-controller-box-graphic",
            "unchanged-bhav-bytes",
        ],
    })
}

fn validate_multi_controller_package(
    job: &MultiJob,
    box_template: &Path,
    controller: &mut DBPFFile,
    controller_reader: &mut Cursor<Vec<u8>>,
) -> Result<(BTreeMap<String, usize>, usize)> {
    let box_guid = parse_hex_id(&job.identity.box_guid, "box_guid")?;
    ensure_unique_tgis(controller)?;
    ensure!(
        controller.index.len() == 36,
        "generated multi controller must contain 36 resources"
    );

    let objd_entry = find_entry_mut(controller, old_key(OBJD, u32::MAX, 0, 0x41A7))?;
    let DecodedFile::ObjectData(objd) = decoded_mut(objd_entry, controller_reader)? else {
        bail!("generated controller OBJD did not decode")
    };
    ensure!(
        objd.guid == box_guid,
        "generated controller GUID is incorrect"
    );

    let ctss_entry = find_entry_mut(controller, old_key(CTSS, u32::MAX, 0, 0x07D0))?;
    let DecodedFile::TextList(ctss) = decoded_mut(ctss_entry, controller_reader)? else {
        bail!("generated controller CTSS did not decode")
    };
    let VersionedTextList::Tagged {
        sets: ctss_sets, ..
    } = &ctss.data
    else {
        bail!("generated controller CTSS has an unexpected layout")
    };
    ensure!(
        ctss_sets.len() == 2
            && ctss_sets[0].value.to_string() == job.catalog_name
            && ctss_sets[1].value.to_string()
                == format!("{}\n\n{}", job.catalog_name, job.catalog_description),
        "generated controller catalog strings are incorrect"
    );

    validate_external_controller_model(controller, controller_reader)?;

    let mut ordered: Vec<&MultiTattoo> = job.tattoos.iter().collect();
    ordered.sort_by_key(|tattoo| tattoo.menu_order);
    let mut collection_words = Vec::with_capacity(ordered.len() * 2);
    let mut gender_flags = Vec::with_capacity(ordered.len() * 2);
    let mut age_masks = Vec::with_capacity(ordered.len() * 2);
    let mut collection_labels = Vec::with_capacity(ordered.len() * 2);
    let mut gender_labels = Vec::with_capacity(ordered.len() * 2);
    let mut age_labels = Vec::with_capacity(ordered.len() * 2);
    let mut group_labels = Vec::with_capacity(ordered.len());
    for tattoo in &ordered {
        let group = parse_hex_id(&tattoo.identity.overlay_group_id, "overlay_group_id")?;
        collection_words.extend([group as u16, (group >> 16) as u16]);
        gender_flags.extend([
            u16::from(tattoo.input_pngs.am.is_some()),
            u16::from(tattoo.input_pngs.af.is_some()),
        ]);
        age_masks.extend([0x18, 0x18]);
        collection_labels.push(format!("{} (last/loword)", tattoo.menu_label));
        collection_labels.push(format!("{} (first/hiword)", tattoo.menu_label));
        gender_labels.push(format!("{} (male)", tattoo.menu_label));
        gender_labels.push(format!("{} (female)", tattoo.menu_label));
        age_labels.push(format!("{} (male age mask)", tattoo.menu_label));
        age_labels.push(format!("{} (female age mask)", tattoo.menu_label));
        group_labels.push(format!("{} (group code)", tattoo.menu_label));
    }
    validate_bcon(
        controller,
        controller_reader,
        0x1000,
        &[ordered.len() as u16],
    )?;
    validate_bcon(controller, controller_reader, 0x1001, &collection_words)?;
    validate_bcon(controller, controller_reader, 0x1002, &gender_flags)?;
    validate_bcon(controller, controller_reader, 0x1003, &age_masks)?;
    validate_bcon(controller, controller_reader, 0x1004, &[0, 0, 0, 0, 0, 0])?;
    validate_bcon(
        controller,
        controller_reader,
        0x1005,
        &vec![0; ordered.len()],
    )?;
    validate_bcon(controller, controller_reader, 0x1006, &[0])?;
    validate_trcn(
        controller,
        controller_reader,
        0x1000,
        &["Tattoo count".to_string()],
    )?;
    validate_trcn(controller, controller_reader, 0x1001, &collection_labels)?;
    validate_trcn(controller, controller_reader, 0x1002, &gender_labels)?;
    validate_trcn(controller, controller_reader, 0x1003, &age_labels)?;
    validate_trcn(
        controller,
        controller_reader,
        0x1004,
        &[
            "PlantSim".to_string(),
            "Vampire".to_string(),
            "Werewolf".to_string(),
            "Zombie".to_string(),
            "Servo".to_string(),
            "Bigfoot".to_string(),
        ],
    )?;
    validate_trcn(controller, controller_reader, 0x1005, &group_labels)?;
    validate_trcn(controller, controller_reader, 0x1006, &[String::new()])?;

    let menu_entry = find_entry_mut(controller, old_key(STR, u32::MAX, 0, 0x12E))?;
    let DecodedFile::TextList(menu_list) = decoded_mut(menu_entry, controller_reader)? else {
        bail!("generated menu STR did not decode")
    };
    let VersionedTextList::Tagged { sets, .. } = &menu_list.data else {
        bail!("generated menu STR has an unexpected layout")
    };
    let expected_menu: Vec<String> = ordered
        .iter()
        .flat_map(|tattoo| {
            [
                format!("Add.../{}", tattoo.menu_label),
                format!("Remove.../{}", tattoo.menu_label),
            ]
        })
        .collect();
    let actual_menu: Vec<String> = sets.iter().map(|item| item.value.to_string()).collect();
    ensure!(
        actual_menu == expected_menu,
        "generated controller menu strings are incorrect"
    );

    let template_bhav = bhav_hashes(box_template)?;
    let generated_bhav = bhav_hashes_package(controller, controller_reader)?;
    ensure!(
        template_bhav == generated_bhav,
        "controller BHAV bytes changed"
    );
    ensure!(
        template_bhav.len() == 13,
        "pinned template BHAV count changed"
    );
    Ok((resource_counts(controller), template_bhav.len()))
}

fn validate_external_controller_model(
    controller: &mut DBPFFile,
    controller_reader: &mut Cursor<Vec<u8>>,
) -> Result<()> {
    let model_entry = find_entry_mut(controller, old_key(STR, u32::MAX, 0, 0x85))?;
    let DecodedFile::TextList(model_list) = decoded_mut(model_entry, controller_reader)? else {
        bail!("generated controller model STR did not decode")
    };
    let VersionedTextList::Tagged { sets, .. } = &model_list.data else {
        bail!("generated controller model STR has an unexpected layout")
    };
    ensure!(
        sets.len() == 2 && sets[1].value.to_string() == MULTI_CONTROLLER_MODEL,
        "generated controller does not reference the Osho Nuff Tablet model"
    );
    let template_scene = template_controller_scene_keys();
    ensure!(
        controller
            .index
            .iter()
            .all(|entry| !template_scene.contains(&key(entry))),
        "generated controller retains embedded template scene resources"
    );
    ensure!(
        controller
            .index
            .iter()
            .all(|entry| entry.group_id != SCENE_GROUP),
        "generated controller contains unexpected private scene resources"
    );
    Ok(())
}

fn validate_multi_bundle(
    job: &MultiJob,
    job_dir: &Path,
    overlay_template: &Path,
    box_template: &Path,
    output_dir: &Path,
) -> Result<MultiValidationReport> {
    let controller_path = output_dir.join(multi_controller_filename(job));
    let (mut controller, mut controller_reader) = open_package(&controller_path)?;
    let (controller_resource_counts, bhav_count) = validate_multi_controller_package(
        job,
        box_template,
        &mut controller,
        &mut controller_reader,
    )?;

    let mut tattoo_reports = Vec::with_capacity(job.tattoos.len());
    for tattoo in &job.tattoos {
        tattoo_reports.push(validate_multi_overlay(
            job,
            tattoo,
            job_dir,
            &output_dir.join(multi_overlay_filename(job, tattoo)),
        )?);
    }
    let (template_overlay, _) = open_package(overlay_template)?;
    let (template_box, _) = open_package(box_template)?;
    ensure!(
        tgi_set_sha256(&template_overlay) == OVERLAY_TEMPLATE_TGI_SHA256,
        "overlay template TGI set changed"
    );
    ensure!(
        tgi_set_sha256(&template_box) == BOX_TEMPLATE_TGI_SHA256,
        "controller template TGI set changed"
    );

    Ok(MultiValidationReport {
        texture_encoding: job.texture_encoder.usage(&[TextureFormat::DXT5]),
        schema_version: 2,
        status: "pass",
        scope: "structural-only",
        ages: job.ages.clone(),
        age_mask: 0x18,
        compatibility: "normal-sims-only",
        box_guid: job.identity.box_guid.clone(),
        scene_graph_name: MULTI_CONTROLLER_MODEL.to_string(),
        controller_model_catalog_name: MULTI_CONTROLLER_CATALOG_NAME,
        controller_model_required_product: MULTI_CONTROLLER_REQUIRED_PRODUCT,
        controller_resource_counts,
        tattoos: tattoo_reports,
        bhav: BhavReport {
            count: bhav_count,
            bytes_unchanged: true,
        },
        checks: vec![
            "pinned-template-tgi-sets",
            "unique-generated-tgis",
            "all-overlay-reference-graphs",
            "adult-and-elder-age-mask",
            "normal-sims-only-controller-flags",
            "controller-array-lengths-and-values",
            "controller-catalog-menu-and-trcn-labels",
            "maxis-osho-nuff-tablet-model-reference",
            "embedded-template-controller-scene-removed",
            "exact-all-3idr-links",
            "dxt5-eleven-level-textures",
            "decoded-alpha-similarity",
            "shared-no-face-dependency",
            "unchanged-bhav-bytes",
        ],
    })
}

fn is_tattoo_resource(entry_key: Key, group: u32) -> bool {
    entry_key.group_id == group
        || (entry_key.group_id == COLLECTION_GROUP
            && entry_key.resource_id == 0
            && entry_key.instance_id == group
            && (entry_key.type_id == COLL || entry_key.type_id == IDR))
}

fn materialized_view(
    package: &mut DBPFFile,
    reader: &mut Cursor<Vec<u8>>,
    include: impl Fn(Key) -> bool,
) -> Result<(DBPFFile, Cursor<Vec<u8>>)> {
    let mut entries = Vec::new();
    for entry in &mut package.index {
        let entry_key = key(entry);
        if include(entry_key) {
            entry
                .data(reader)
                .context("materialize resource for merged validation view")?;
            entries.push(entry.clone());
        }
    }
    let mut view = package.clone();
    view.index = entries;
    view.hole_index.clear();
    Ok((view, Cursor::new(Vec::new())))
}

fn validate_merged(
    job: &MultiJob,
    job_dir: &Path,
    overlay_template: &Path,
    box_template: &Path,
    no_face_template: &Path,
    package_path: &Path,
) -> Result<MergedValidationReport> {
    let (mut merged, mut merged_reader) = open_package(package_path)?;
    ensure_unique_tgis(&merged)?;
    let expected_count = expected_merged_resource_count(job);
    ensure!(
        merged.index.len() == expected_count,
        "merged package resource count is incorrect, expected {expected_count}, found {}",
        merged.index.len()
    );

    let tattoo_groups = job
        .tattoos
        .iter()
        .map(|tattoo| parse_hex_id(&tattoo.identity.overlay_group_id, "overlay_group_id"))
        .collect::<Result<BTreeSet<_>>>()?;
    let no_face_count = merged
        .index
        .iter()
        .filter(|entry| entry.group_id == NO_FACE_GROUP)
        .count();
    ensure!(
        no_face_count == 1 && merged.index.iter().any(|entry| key(entry) == no_face_key()),
        "merged package must contain exactly one shared no-face TXTR"
    );

    let (no_face_template_package, _) = open_package(no_face_template)?;
    ensure!(
        tgi_set_sha256(&no_face_template_package) == NO_FACE_TEMPLATE_TGI_SHA256,
        "no-face template TGI set changed"
    );
    let expected_no_face = texture_dds_sha256(no_face_template, no_face_key())?;
    let actual_no_face =
        texture_dds_sha256_package(&mut merged, &mut merged_reader, no_face_key())?;
    ensure!(
        expected_no_face == actual_no_face,
        "merged shared no-face texture changed"
    );

    let (mut controller, mut controller_reader) =
        materialized_view(&mut merged, &mut merged_reader, |entry_key| {
            entry_key != no_face_key()
                && !tattoo_groups
                    .iter()
                    .any(|group| is_tattoo_resource(entry_key, *group))
        })?;
    let (controller_resource_counts, bhav_count) = validate_multi_controller_package(
        job,
        box_template,
        &mut controller,
        &mut controller_reader,
    )?;

    let mut tattoo_reports = Vec::with_capacity(job.tattoos.len());
    let mut claimed_count = controller.index.len() + no_face_count;
    for tattoo in &job.tattoos {
        let group = parse_hex_id(&tattoo.identity.overlay_group_id, "overlay_group_id")?;
        let (mut overlay, mut overlay_reader) =
            materialized_view(&mut merged, &mut merged_reader, |entry_key| {
                is_tattoo_resource(entry_key, group)
            })?;
        claimed_count += overlay.index.len();
        tattoo_reports.push(validate_multi_overlay_package(
            job,
            tattoo,
            job_dir,
            &mut overlay,
            &mut overlay_reader,
        )?);
    }
    ensure!(
        claimed_count == merged.index.len(),
        "merged package contains an unclaimed resource"
    );

    let (template_overlay, _) = open_package(overlay_template)?;
    let (template_box, _) = open_package(box_template)?;
    ensure!(
        tgi_set_sha256(&template_overlay) == OVERLAY_TEMPLATE_TGI_SHA256,
        "overlay template TGI set changed"
    );
    ensure!(
        tgi_set_sha256(&template_box) == BOX_TEMPLATE_TGI_SHA256,
        "controller template TGI set changed"
    );

    Ok(MergedValidationReport {
        package_compression: crate::package_compression::report(
            package_path.to_str().context("Invalid package path")?,
            job.refpack_compression,
        )?,
        texture_encoding: job.texture_encoder.usage(&[TextureFormat::DXT5]),
        schema_version: 2,
        status: "pass",
        scope: "structural-only",
        layout: "single-package",
        ages: job.ages.clone(),
        age_mask: 0x18,
        compatibility: "normal-sims-only",
        box_guid: job.identity.box_guid.clone(),
        scene_graph_name: MULTI_CONTROLLER_MODEL.to_string(),
        controller_model_catalog_name: MULTI_CONTROLLER_CATALOG_NAME,
        controller_model_required_product: MULTI_CONTROLLER_REQUIRED_PRODUCT,
        logical_resource_count: merged.index.len(),
        resource_counts: resource_counts(&merged),
        controller_resource_counts,
        tattoos: tattoo_reports,
        no_face_resource_count: no_face_count,
        bhav: BhavReport {
            count: bhav_count,
            bytes_unchanged: true,
        },
        checks: vec![
            "pinned-template-tgi-sets",
            "complete-merged-resource-union",
            "unique-generated-tgis",
            "all-overlay-reference-graphs",
            "adult-and-elder-age-mask",
            "normal-sims-only-controller-flags",
            "controller-array-lengths-and-values",
            "controller-catalog-menu-and-trcn-labels",
            "maxis-osho-nuff-tablet-model-reference",
            "embedded-template-controller-scene-removed",
            "exact-all-3idr-links",
            "dxt5-eleven-level-textures",
            "decoded-alpha-similarity",
            "single-shared-no-face-resource",
            "unchanged-bhav-bytes",
        ],
    })
}

fn validate_multi_overlay(
    job: &MultiJob,
    tattoo: &MultiTattoo,
    job_dir: &Path,
    overlay_path: &Path,
) -> Result<MultiTattooReport> {
    let (mut overlay, mut reader) = open_package(overlay_path)?;
    validate_multi_overlay_package(job, tattoo, job_dir, &mut overlay, &mut reader)
}

fn validate_multi_overlay_package(
    job: &MultiJob,
    tattoo: &MultiTattoo,
    job_dir: &Path,
    overlay: &mut DBPFFile,
    reader: &mut Cursor<Vec<u8>>,
) -> Result<MultiTattooReport> {
    let group = parse_hex_id(&tattoo.identity.overlay_group_id, "overlay_group_id")?;
    ensure_unique_tgis(overlay)?;
    let gender_count =
        usize::from(tattoo.input_pngs.am.is_some()) + usize::from(tattoo.input_pngs.af.is_some());
    ensure!(
        overlay.index.len() == 7 + 5 * gender_count,
        "generated overlay resource count is incorrect"
    );
    for entry in &overlay.index {
        let entry_key = key(entry);
        let is_collection_resource = entry_key.group_id == COLLECTION_GROUP
            && (entry_key.type_id == COLL || entry_key.type_id == IDR);
        ensure!(
            is_collection_resource || entry_key.group_id == group,
            "generated overlay contains a resource in an unexpected group"
        );
    }

    find_entry_mut(overlay, old_key(COLL, COLLECTION_GROUP, 0, group))?;
    let collection_idr = find_entry_mut(overlay, old_key(IDR, COLLECTION_GROUP, 0, group))?;
    mutate_idr(collection_idr, reader, |idr| {
        ensure!(
            idr.entries.len() == 2,
            "generated collection 3IDR layout changed"
        );
        ensure!(
            idr.entries[1].type_id.code() == STR
                && idr.entries[1].group_id == group
                && idr.entries[1].instance_id.id == 1,
            "generated collection 3IDR string link is incorrect"
        );
        Ok(())
    })?;
    let label_entry = find_entry_mut(overlay, old_key(STR, group, 0, 1))?;
    let DecodedFile::TextList(label_list) = decoded_mut(label_entry, reader)? else {
        bail!("generated overlay label STR did not decode")
    };
    let VersionedTextList::Tagged {
        sets: label_sets, ..
    } = &label_list.data
    else {
        bail!("generated overlay label STR has an unexpected layout")
    };
    ensure!(
        !label_sets.is_empty() && label_sets[0].value.to_string() == tattoo.menu_label,
        "generated overlay label is incorrect"
    );
    find_entry_mut(overlay, old_key(BINX, group, 0, 0x10))?;

    let xtol_entry = find_entry_mut(overlay, old_key(XTOL, group, 0, 0x10))?;
    let DecodedFile::GenericCPF(xtol) = decoded_mut(xtol_entry, reader)? else {
        bail!("generated no-face XTOL did not decode")
    };
    ensure!(
        cpf_string(xtol, "family")? == tattoo.identity.family_uuid,
        "generated no-face family is incorrect"
    );
    ensure!(
        cpf_string(xtol, "name")? == format!("##0x{group:08x}!uufaceoverlay_face"),
        "generated no-face name is incorrect"
    );
    let noface_idr = find_entry_mut(overlay, old_key(IDR, group, 0, 0x10))?;
    mutate_idr(noface_idr, reader, |idr| {
        ensure!(
            idr.entries.len() == 5,
            "generated no-face 3IDR layout changed"
        );
        ensure!(
            idr.entries[1].type_id.code() == COLL
                && idr.entries[1].group_id == COLLECTION_GROUP
                && idr.entries[1].instance_id.id == group as u64,
            "generated no-face COLL link is incorrect"
        );
        ensure!(
            idr.entries[2].type_id.code() == XTOL
                && idr.entries[2].group_id == group
                && idr.entries[2].instance_id.id == 0x10,
            "generated no-face XTOL link is incorrect: type {:08X}, group {:08X}, instance {:016X}",
            idr.entries[2].type_id.code(),
            idr.entries[2].group_id,
            idr.entries[2].instance_id.id
        );
        ensure!(
            idr.entries[3].type_id.code() == STR
                && idr.entries[3].group_id == group
                && idr.entries[3].instance_id.id == 1,
            "generated no-face STR link is incorrect: type {:08X}, group {:08X}, instance {:016X}",
            idr.entries[3].type_id.code(),
            idr.entries[3].group_id,
            idr.entries[3].instance_id.id
        );
        ensure!(
            idr.entries[4].type_id.code() == TXMT
                && idr.entries[4].group_id == group
                && idr.entries[4].instance_id.id == ((0x1EF6_7347u64) << 32) | 0xFF08_76DDu64,
            "generated no-face TXMT link is incorrect"
        );
        Ok(())
    })?;
    let noface_txmt = find_entry_mut(overlay, old_key(TXMT, group, 0x1EF6_7347, 0xFF08_76DD))?;
    mutate_material(noface_txmt, reader, |material| {
        validate_no_face_material(material, group)
    })?;

    let mut genders = Vec::new();
    let mut txtr_reports = BTreeMap::new();
    if let Some(path) = &tattoo.input_pngs.am {
        genders.push("am");
        txtr_reports.insert(
            "am",
            validate_multi_body_branch(job, tattoo, "am", &job_dir.join(path), overlay, reader)?,
        );
    }
    if let Some(path) = &tattoo.input_pngs.af {
        genders.push("af");
        txtr_reports.insert(
            "af",
            validate_multi_body_branch(job, tattoo, "af", &job_dir.join(path), overlay, reader)?,
        );
    }

    Ok(MultiTattooReport {
        key: tattoo.key.clone(),
        overlay_group_id: tattoo.identity.overlay_group_id.clone(),
        genders,
        priority: tattoo.priority,
        overlay_resource_counts: resource_counts(overlay),
        txtr: txtr_reports,
    })
}

fn validate_multi_body_branch(
    job: &MultiJob,
    tattoo: &MultiTattoo,
    preset: &str,
    source_png: &Path,
    overlay: &mut DBPFFile,
    reader: &mut Cursor<Vec<u8>>,
) -> Result<TxtrReport> {
    let (instance, expected_gender) = if preset == "am" { (1, 2) } else { (2, 1) };
    let group = parse_hex_id(&tattoo.identity.overlay_group_id, "overlay_group_id")?;
    let names = multi_tattoo_names(job, tattoo, preset);
    find_entry_mut(overlay, old_key(BINX, group, 0, instance))?;
    let gzps_entry = find_entry_mut(overlay, old_key(GZPS, group, 0, instance))?;
    let DecodedFile::PropertySet(gzps) = decoded_mut(gzps_entry, reader)? else {
        bail!("generated GZPS did not decode")
    };
    ensure!(
        gzps.age == 0x18,
        "generated GZPS does not target Adult and Elder"
    );
    ensure!(
        gzps.gender == expected_gender,
        "generated GZPS gender is incorrect"
    );
    ensure!(
        gzps.priority == Some(tattoo.priority),
        "generated GZPS priority is incorrect"
    );
    ensure!(
        gzps.family.data == tattoo.identity.family_uuid.as_bytes(),
        "generated GZPS family is incorrect"
    );
    ensure!(
        gzps.name.data
            == format!(
                "##0x{group:08x}!{}bodynakedoverlay_{}",
                names.body_prefix, names.overlay_scene
            )
            .as_bytes(),
        "generated GZPS name is incorrect"
    );

    let idr_entry = find_entry_mut(overlay, old_key(IDR, group, 0, instance))?;
    mutate_idr(idr_entry, reader, |idr| {
        ensure!(idr.entries.len() == 7, "generated body 3IDR layout changed");
        ensure!(
            idr.entries[1].type_id.code() == COLL
                && idr.entries[1].group_id == COLLECTION_GROUP
                && idr.entries[1].instance_id.id == group as u64,
            "generated body COLL link is incorrect"
        );
        ensure!(
            idr.entries[2].type_id.code() == GZPS
                && idr.entries[2].group_id == group
                && idr.entries[2].instance_id.id == instance as u64,
            "generated body GZPS link is incorrect"
        );
        ensure!(
            idr.entries[3].type_id.code() == STR
                && idr.entries[3].group_id == group
                && idr.entries[3].instance_id.id == 1,
            "generated body STR link is incorrect"
        );
        ensure!(
            idr.entries[6].group_id == group,
            "generated body TXMT group link is incorrect"
        );
        ensure!(
            idr.entries[6].instance_id.id
                == ((resource_id_hash(&names.txmt_name) as u64) << 32)
                    | instance_id_hash(&names.txmt_name) as u64,
            "generated body TXMT link is incorrect"
        );
        Ok(())
    })?;

    let txmt_entry = find_entry_mut(
        overlay,
        old_key(
            TXMT,
            group,
            resource_id_hash(&names.txmt_name),
            instance_id_hash(&names.txmt_name),
        ),
    )?;
    mutate_material(txmt_entry, reader, |material| {
        ensure!(
            material.file_name.name.to_string() == format!("##0x{group:08x}!{}", names.txmt_name),
            "generated body TXMT name is incorrect"
        );
        ensure!(
            material.material_description.to_string()
                == format!(
                    "##0x{group:08x}!{}bodynakedoverlay_{}",
                    names.body_prefix, names.overlay_scene
                ),
            "generated body TXMT material description is incorrect"
        );
        let expected = format!(
            "##0x{group:08x}!{}bodynakedoverlay-{}",
            names.body_prefix, names.overlay_scene
        );
        let value = material
            .properties
            .iter()
            .find(|property| property.name.to_string() == "stdMatBaseTextureName")
            .ok_or_else(|| anyhow!("generated body TXMT has no base texture property"))?;
        ensure!(
            value.value.to_string() == expected,
            "generated body TXMT base texture link is incorrect"
        );
        Ok(())
    })?;
    let txtr_entry = find_entry_mut(
        overlay,
        old_key(
            TXTR,
            group,
            resource_id_hash(&names.txtr_name),
            instance_id_hash(&names.txtr_name),
        ),
    )?;
    validate_txtr(txtr_entry, reader, Some(source_png))
}

fn validate_controller_scene(
    controller: &mut DBPFFile,
    reader: &mut Cursor<Vec<u8>>,
    names: &Names,
) -> Result<()> {
    for (resource_type, name) in [
        (0xE519_C933, names.box_cres.as_str()),
        (0xFC6E_B1F7, names.box_shpe.as_str()),
        (0x7BA3_838C, names.box_gmnd.as_str()),
        (0xAC4F_8687, names.box_gmdc.as_str()),
        (TXMT, names.box_txmt.as_str()),
    ] {
        find_entry_mut(
            controller,
            old_key(
                resource_type,
                SCENE_GROUP,
                resource_id_hash(name),
                instance_id_hash(name),
            ),
        )?;
    }
    let gmnd_reference = format!("##0x{SCENE_GROUP:08X}!{}", names.box_gmnd);
    let surface_reference = format!("##0x{SCENE_GROUP:08X}!{}_surface", names.box_scene);
    validate_scene_raw(
        controller,
        reader,
        old_key(
            0xE519_C933,
            SCENE_GROUP,
            resource_id_hash(&names.box_cres),
            instance_id_hash(&names.box_cres),
        ),
        &[(names.box_cres.as_str(), 2)],
        &[
            resource_id_hash(&names.box_shpe),
            instance_id_hash(&names.box_shpe),
        ],
    )?;
    validate_scene_raw(
        controller,
        reader,
        old_key(
            0xFC6E_B1F7,
            SCENE_GROUP,
            resource_id_hash(&names.box_shpe),
            instance_id_hash(&names.box_shpe),
        ),
        &[
            (names.box_shpe.as_str(), 2),
            (gmnd_reference.as_str(), 1),
            (surface_reference.as_str(), 1),
        ],
        &[],
    )?;
    validate_scene_raw(
        controller,
        reader,
        old_key(
            0x7BA3_838C,
            SCENE_GROUP,
            resource_id_hash(&names.box_gmnd),
            instance_id_hash(&names.box_gmnd),
        ),
        &[(names.box_gmnd.as_str(), 2)],
        &[
            resource_id_hash(&names.box_gmdc),
            instance_id_hash(&names.box_gmdc),
        ],
    )?;
    validate_scene_raw(
        controller,
        reader,
        old_key(
            0xAC4F_8687,
            SCENE_GROUP,
            resource_id_hash(&names.box_gmdc),
            instance_id_hash(&names.box_gmdc),
        ),
        &[(names.box_gmdc.as_str(), 1)],
        &[],
    )?;
    let box_txmt_entry = find_entry_mut(
        controller,
        old_key(
            TXMT,
            SCENE_GROUP,
            resource_id_hash(&names.box_txmt),
            instance_id_hash(&names.box_txmt),
        ),
    )?;
    mutate_material(box_txmt_entry, reader, |material| {
        let expected = format!("##0x{SCENE_GROUP:08X}!{}_crate", names.box_scene);
        let legacy = format!("{}-crate", names.box_scene);
        let value = material
            .properties
            .iter()
            .find(|property| property.name.to_string() == "stdMatBaseTextureName")
            .ok_or_else(|| anyhow!("generated controller TXMT has no base texture property"))?;
        ensure!(
            value.value.to_string() == expected,
            "generated controller TXMT base texture link is incorrect"
        );
        ensure!(
            material
                .names
                .iter()
                .filter(|name| name.to_string() == expected)
                .count()
                == 1,
            "generated controller TXMT texture-name list is incorrect"
        );
        ensure!(
            !material.file_name.name.to_string().contains(&legacy)
                && !material.material_description.to_string().contains(&legacy)
                && !material.material_type.to_string().contains(&legacy)
                && material.properties.iter().all(|property| {
                    !property.name.to_string().contains(&legacy)
                        && !property.value.to_string().contains(&legacy)
                })
                && material
                    .names
                    .iter()
                    .all(|name| !name.to_string().contains(&legacy)),
            "generated controller TXMT retains the template crate-name separator"
        );
        Ok(())
    })?;
    find_entry_mut(
        controller,
        old_key(
            TXTR,
            SCENE_GROUP,
            resource_id_hash(&names.box_txtr),
            instance_id_hash(&names.box_txtr),
        ),
    )?;
    validate_controller_scene_fingerprints(controller, reader, names)?;
    Ok(())
}

fn normalized_resource_bytes(
    package: &mut DBPFFile,
    reader: &mut Cursor<Vec<u8>>,
    wanted: Key,
    string_replacements: &[(&str, &str, usize)],
    numeric_replacements: &[(u32, &[u8; 4], usize)],
) -> Result<Vec<u8>> {
    let entry = find_entry_mut(package, wanted)?;
    let mut normalized = entry.data(reader)?.decompressed()?.data.clone();
    for (from, to, expected_count) in string_replacements {
        replace_big_string(&mut normalized, from, to, *expected_count)?;
    }
    for (from, to, expected_count) in numeric_replacements {
        replace_equal_len(
            &mut normalized,
            &from.to_le_bytes(),
            to.as_slice(),
            *expected_count,
        )?;
    }
    Ok(normalized)
}

fn validate_normalized_resource(label: &str, bytes: &[u8], expected_sha256: &str) -> Result<()> {
    let actual = format!("{:x}", Sha256::digest(bytes));
    ensure!(
        actual == expected_sha256,
        "generated controller {label} differs from the normalized known-good Pick'N'Mix resource"
    );
    Ok(())
}

fn validate_controller_scene_fingerprints(
    controller: &mut DBPFFile,
    reader: &mut Cursor<Vec<u8>>,
    names: &Names,
) -> Result<()> {
    let cres = normalized_resource_bytes(
        controller,
        reader,
        old_key(
            0xE519_C933,
            SCENE_GROUP,
            resource_id_hash(&names.box_cres),
            instance_id_hash(&names.box_cres),
        ),
        &[(names.box_cres.as_str(), "CRES_NAME", 2)],
        &[
            (resource_id_hash(&names.box_shpe), b"RIDS", 1),
            (instance_id_hash(&names.box_shpe), b"IIDS", 1),
        ],
    )?;
    validate_normalized_resource("CRES", &cres, BOX_CRES_NORMALIZED_SHA256)?;

    let gmnd_reference = format!("##0x{SCENE_GROUP:08X}!{}", names.box_gmnd);
    let surface_reference = format!("##0x{SCENE_GROUP:08X}!{}_surface", names.box_scene);
    let shpe = normalized_resource_bytes(
        controller,
        reader,
        old_key(
            0xFC6E_B1F7,
            SCENE_GROUP,
            resource_id_hash(&names.box_shpe),
            instance_id_hash(&names.box_shpe),
        ),
        &[
            (names.box_shpe.as_str(), "SHPE_NAME", 2),
            (gmnd_reference.as_str(), "GMND_REF", 1),
            (surface_reference.as_str(), "SURFACE_REF", 1),
        ],
        &[],
    )?;
    validate_normalized_resource("SHPE", &shpe, BOX_SHPE_NORMALIZED_SHA256)?;

    let gmnd = normalized_resource_bytes(
        controller,
        reader,
        old_key(
            0x7BA3_838C,
            SCENE_GROUP,
            resource_id_hash(&names.box_gmnd),
            instance_id_hash(&names.box_gmnd),
        ),
        &[(names.box_gmnd.as_str(), "GMND_NAME", 2)],
        &[
            (resource_id_hash(&names.box_gmdc), b"RIDG", 1),
            (instance_id_hash(&names.box_gmdc), b"IIDG", 1),
        ],
    )?;
    validate_normalized_resource("GMND", &gmnd, BOX_GMND_NORMALIZED_SHA256)?;

    let gmdc = normalized_resource_bytes(
        controller,
        reader,
        old_key(
            0xAC4F_8687,
            SCENE_GROUP,
            resource_id_hash(&names.box_gmdc),
            instance_id_hash(&names.box_gmdc),
        ),
        &[(names.box_gmdc.as_str(), "GMDC_NAME", 1)],
        &[],
    )?;
    validate_normalized_resource("GMDC", &gmdc, BOX_GMDC_NORMALIZED_SHA256)?;

    let texture_reference = format!("##0x{SCENE_GROUP:08X}!{}_crate", names.box_scene);
    let surface_name = format!("{}_surface", names.box_scene);
    let txmt = normalized_resource_bytes(
        controller,
        reader,
        old_key(
            TXMT,
            SCENE_GROUP,
            resource_id_hash(&names.box_txmt),
            instance_id_hash(&names.box_txmt),
        ),
        &[
            (names.box_txmt.as_str(), "TXMT_NAME", 1),
            (surface_name.as_str(), "SURFACE_NAME", 1),
            (texture_reference.as_str(), "TEXTURE_REF", 2),
        ],
        &[],
    )?;
    validate_normalized_resource("TXMT", &txmt, BOX_TXMT_NORMALIZED_SHA256)?;

    let txtr = normalized_resource_bytes(
        controller,
        reader,
        old_key(
            TXTR,
            SCENE_GROUP,
            resource_id_hash(&names.box_txtr),
            instance_id_hash(&names.box_txtr),
        ),
        &[(names.box_txtr.as_str(), "TXTR_NAME", 1)],
        &[],
    )?;
    validate_normalized_resource("TXTR", &txtr, BOX_TXTR_NORMALIZED_SHA256)
}

fn validate_txtr(
    entry: &mut IndexEntry,
    reader: &mut Cursor<Vec<u8>>,
    source_png: Option<&Path>,
) -> Result<TxtrReport> {
    let DecodedFile::ResourceCollection(collection) = decoded_mut(entry, reader)? else {
        bail!("generated TXTR did not decode")
    };
    ensure!(
        collection.entries.len() == 1,
        "generated TXTR collection layout changed"
    );
    let ResourceData::Texture(texture) = &mut collection.entries[0].data else {
        bail!("generated TXTR has no texture resource")
    };
    ensure!(
        texture.get_format() == TextureFormat::DXT5,
        "generated TXTR is not DXT5 or BC3"
    );
    ensure!(
        (texture.width, texture.height) == (1024, 1024),
        "generated TXTR dimensions are incorrect"
    );
    ensure!(
        texture.mip_levels() == 11,
        "generated TXTR does not have 11 mip levels"
    );
    for stored_index in 0..texture.mip_levels() {
        let decoded = texture.decompress(0, stored_index)?;
        let shift = texture.mip_levels() - 1 - stored_index;
        let expected = (usize::max(1024 >> shift, 1), usize::max(1024 >> shift, 1));
        ensure!(
            (decoded.width, decoded.height) == expected,
            "decoded TXTR mip {stored_index} dimensions are incorrect"
        );
    }
    let decoded = texture.decompress(0, texture.mip_levels() - 1)?;
    let (alpha_min, alpha_max) = decoded
        .data
        .chunks_exact(4)
        .map(|pixel| pixel[3])
        .fold((u8::MAX, u8::MIN), |(lo, hi), alpha| {
            (lo.min(alpha), hi.max(alpha))
        });
    ensure!(alpha_max > 0, "generated TXTR alpha is blank");
    let (alpha_mean_absolute_error, alpha_max_absolute_error) = if let Some(path) = source_png {
        let source = crate::assets::open_image(path)
            .with_context(|| format!("decode source PNG {}", path.display()))?
            .into_rgba8();
        ensure!(
            source.dimensions() == (decoded.width as u32, decoded.height as u32),
            "source and generated TXTR dimensions differ"
        );
        let source_alpha = source.pixels().map(|pixel| pixel[3]);
        let decoded_alpha = decoded.data.chunks_exact(4).map(|pixel| pixel[3]);
        let mut total_error = 0u64;
        let mut max_error = 0u8;
        let mut count = 0u64;
        for (source_value, decoded_value) in source_alpha.zip(decoded_alpha) {
            let error = source_value.abs_diff(decoded_value);
            total_error += u64::from(error);
            max_error = max_error.max(error);
            count += 1;
        }
        ensure!(
            count == 1024 * 1024,
            "alpha comparison pixel count is incorrect"
        );
        let mean_error = total_error as f64 / count as f64;
        ensure!(
            mean_error <= 18.0,
            "generated TXTR alpha mean error is too high"
        );
        ensure!(
            max_error <= 64,
            "generated TXTR alpha maximum error is too high"
        );
        (Some(mean_error), Some(max_error))
    } else {
        (None, None)
    };
    Ok(TxtrReport {
        format: "DXT5/BC3",
        width: texture.width,
        height: texture.height,
        mip_levels: texture.mip_levels(),
        decoded_alpha_min: alpha_min,
        decoded_alpha_max: alpha_max,
        alpha_mean_absolute_error,
        alpha_max_absolute_error,
    })
}

fn texture_dds_sha256(path: &Path, wanted: Key) -> Result<String> {
    let (mut package, mut reader) = open_package(path)?;
    texture_dds_sha256_package(&mut package, &mut reader, wanted)
}

fn texture_dds_sha256_package(
    package: &mut DBPFFile,
    reader: &mut Cursor<Vec<u8>>,
    wanted: Key,
) -> Result<String> {
    let entry = find_entry_mut(package, wanted)?;
    let DecodedFile::ResourceCollection(collection) = decoded_mut(entry, reader)? else {
        bail!("controller TXTR did not decode")
    };
    ensure!(
        collection.entries.len() == 1,
        "controller TXTR collection layout changed"
    );
    let ResourceData::Texture(texture) = &collection.entries[0].data else {
        bail!("controller TXTR has no texture resource")
    };
    let mut dds = Vec::new();
    texture.export_dds(&mut dds)?;
    Ok(format!("{:x}", Sha256::digest(dds)))
}

fn validate_scene_raw(
    package: &mut DBPFFile,
    reader: &mut Cursor<Vec<u8>>,
    wanted: Key,
    expected_strings: &[(&str, usize)],
    linked_ids: &[u32],
) -> Result<()> {
    let entry = find_entry_mut(package, wanted)?;
    let raw = &entry.data(reader)?.decompressed()?.data;
    for (value, expected_count) in expected_strings {
        let encoded = encoded_big_string(value);
        ensure!(
            count_occurrences(raw, &encoded) == *expected_count,
            "generated scene resource has an incorrect internal scene-graph string: {value}"
        );
    }
    ensure!(
        count_occurrences(raw, OLD_BOX_SCENE_NAME.as_bytes()) == 0,
        "generated scene resource retains the template scene name"
    );
    ensure!(
        count_occurrences(raw, b"_root_rot_") == 0,
        "generated scene resource retains a template root-rotation filename"
    );
    for linked in linked_ids {
        ensure!(
            count_occurrences(raw, &linked.to_le_bytes()) == 1,
            "generated scene resource has an incorrect numeric link"
        );
    }
    Ok(())
}

fn count_occurrences(bytes: &[u8], needle: &[u8]) -> usize {
    bytes
        .windows(needle.len())
        .filter(|window| *window == needle)
        .count()
}

fn cpf_string(cpf: &CPF, name: &str) -> Result<String> {
    let mut matches = cpf
        .entries
        .iter()
        .filter(|item| item.name.data == name.as_bytes());
    let item = matches
        .next()
        .ok_or_else(|| anyhow!("CPF string key {name} is missing"))?;
    ensure!(
        matches.next().is_none(),
        "CPF string key {name} is duplicated"
    );
    let Data::String(text) = &item.data else {
        bail!("CPF key {name} is not a string")
    };
    String::from_utf8(text.data.clone()).context("CPF string is not UTF-8")
}

fn validate_bcon(
    package: &mut DBPFFile,
    reader: &mut Cursor<Vec<u8>>,
    instance: u32,
    expected: &[u16],
) -> Result<()> {
    let entry = find_entry_mut(package, old_key(BCON, u32::MAX, 0, instance))?;
    let DecodedFile::BehaviourConstants(values) = decoded_mut(entry, reader)? else {
        bail!("generated BCON did not decode")
    };
    ensure!(
        values.constants == expected,
        "generated BCON {instance:04X} values are incorrect"
    );
    Ok(())
}

fn validate_trcn(
    package: &mut DBPFFile,
    reader: &mut Cursor<Vec<u8>>,
    instance: u32,
    expected: &[String],
) -> Result<()> {
    let entry = find_entry_mut(package, old_key(TRCN, u32::MAX, 0, instance))?;
    let DecodedFile::BehaviourConstantsLabels(BehaviourConstantsLabels { labels, .. }) =
        decoded_mut(entry, reader)?
    else {
        bail!("generated TRCN did not decode")
    };
    ensure!(
        labels.len() == expected.len(),
        "generated TRCN {instance:04X} label count is incorrect"
    );
    for (index, (label, expected_name)) in labels.iter().zip(expected).enumerate() {
        let Label::V1(label) = label else {
            bail!("generated TRCN {instance:04X} has an unexpected label version")
        };
        ensure!(
            label.id == index as u32
                && label.used == 0
                && label.name.to_string() == *expected_name
                && label.default == 0
                && label.min == 0
                && label.max == 0,
            "generated TRCN {instance:04X} label {index} is incorrect"
        );
    }
    Ok(())
}

fn bhav_hashes(path: &Path) -> Result<BTreeMap<String, String>> {
    let (mut package, mut reader) = open_package(path)?;
    bhav_hashes_package(&mut package, &mut reader)
}

fn bhav_hashes_package(
    package: &mut DBPFFile,
    reader: &mut Cursor<Vec<u8>>,
) -> Result<BTreeMap<String, String>> {
    let mut result = BTreeMap::new();
    for entry in &mut package.index {
        if entry.type_id.code() != BHAV {
            continue;
        }
        let entry_key = key(entry);
        let bytes = entry.data(reader)?.decompressed()?.data.clone();
        result.insert(key_text(entry_key), format!("{:x}", Sha256::digest(bytes)));
    }
    Ok(result)
}

fn resource_counts(package: &DBPFFile) -> BTreeMap<String, usize> {
    let mut counts = BTreeMap::new();
    for entry in &package.index {
        *counts.entry(entry.type_id.abbreviation()).or_insert(0) += 1;
    }
    counts
}

fn ensure_unique_tgis(package: &DBPFFile) -> Result<()> {
    let mut keys = BTreeSet::new();
    for entry in &package.index {
        ensure!(
            keys.insert(key(entry)),
            "duplicate generated TGI {}",
            key_text(key(entry))
        );
    }
    Ok(())
}

fn tgi_set_sha256(package: &DBPFFile) -> String {
    let mut lines: Vec<String> = package
        .index
        .iter()
        .map(|entry| key_text(key(entry)))
        .collect();
    lines.sort();
    let canonical = format!("{}\n", lines.join("\n"));
    format!("{:x}", Sha256::digest(canonical.as_bytes()))
}

fn crc_msb(data: &[u8], width: u32, polynomial: u64, initial: u64, final_xor: u64) -> u32 {
    let mask = (1u64 << width) - 1;
    let top = 1u64 << (width - 1);
    let mut value = initial & mask;
    for byte in data {
        value ^= (*byte as u64) << (width - 8);
        for _ in 0..8 {
            value = if value & top != 0 {
                (value << 1) ^ polynomial
            } else {
                value << 1
            } & mask;
        }
    }
    ((value ^ final_xor) & mask) as u32
}

fn instance_id_hash(name: &str) -> u32 {
    0xFF00_0000
        | crc_msb(
            name.trim().to_ascii_lowercase().as_bytes(),
            24,
            0x0186_4CFB,
            0x00B7_04CE,
            0,
        )
}

fn resource_id_hash(name: &str) -> u32 {
    crc_msb(
        name.trim().to_ascii_lowercase().as_bytes(),
        32,
        0x04C1_1DB7,
        0xFFFF_FFFF,
        0,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    #[cfg(feature = "reference-tests")]
    fn no_face_material_rejects_stale_template_description_and_file_list() {
        let path = std::env::var_os("PROJECT_FIXTURE_ROOT")
            .map(PathBuf::from)
            .unwrap_or_else(|| Path::new(env!("CARGO_MANIFEST_DIR")).join("../.."))
            .join("package_creation/templates/Template_MultiOverlay.package");
        let (mut package, mut reader) = open_package(&path).unwrap();
        let entry = find_entry_mut(
            &mut package,
            old_key(TXMT, u32::MAX, 0x1EF6_7347, 0xFF08_76DD),
        )
        .unwrap();
        mutate_material(entry, &mut reader, |material| {
            let original = material.clone();
            let group = 0x6688_16A0;
            configure_no_face_material(material, group)?;
            validate_no_face_material(material, group)?;
            assert_eq!(material.properties, original.properties);
            assert_eq!(material.material_type, original.material_type);

            let mut corrupt = material.clone();
            corrupt.material_description = original.material_description;
            assert!(validate_no_face_material(&corrupt, group)
                .unwrap_err()
                .to_string()
                .contains("material description"));

            let mut corrupt = material.clone();
            corrupt.names = original.names;
            assert!(validate_no_face_material(&corrupt, group)
                .unwrap_err()
                .to_string()
                .contains("texture file list"));

            let mut corrupt = material.clone();
            set_material_property(
                &mut corrupt,
                "stdMatBaseTextureName",
                "##0xffffffff!uufaceoverlay-face",
            )?;
            assert!(validate_no_face_material(&corrupt, group)
                .unwrap_err()
                .to_string()
                .contains("shared transparent texture"));
            Ok(())
        })
        .unwrap();
    }

    #[test]
    fn sims2_name_hash_vectors_match_template_tgis() {
        assert_eq!(
            instance_id_hash("umbodynakedoverlay_armsdense1_txmt"),
            0xFFA1_04EE
        );
        assert_eq!(
            resource_id_hash("umbodynakedoverlay_armsdense1_txmt"),
            0x7596_136A
        );
        assert_eq!(
            instance_id_hash("umbodynakedoverlay-armsdense1_txtr"),
            0xFFAE_95F9
        );
        assert_eq!(
            resource_id_hash("umbodynakedoverlay-armsdense1_txtr"),
            0xAD10_7835
        );
        assert_eq!(instance_id_hash("accessory-box-template_cres"), 0xFF67_A0B1);
        assert_eq!(resource_id_hash("accessory-box-template_cres"), 0x053B_9122);
    }

    #[test]
    fn derived_scene_names_follow_picknmix_resource_conventions() {
        let job = Job {
            refpack_compression: true,
            texture_encoder: Default::default(),
            schema_version: 1,
            slug: "ash-kiryu-am".into(),
            catalog_name: "Ash Kiryu Tattoo".into(),
            catalog_description: "Adult male body overlay".into(),
            preset: "am".into(),
            ages: vec!["adult".into()],
            priority: 102,
            input_png: "image.png".into(),
            identity: Identity {
                box_guid: "0x12345678".into(),
                overlay_group_id: "0x23456789".into(),
                family_uuid: "11111111-2222-4333-8444-555555555555".into(),
            },
        };
        let names = names(&job);
        assert_eq!(names.box_scene.len(), OLD_BOX_SCENE_NAME.len());
        assert_eq!(names.overlay_scene.len(), OLD_OVERLAY_SCENE_NAME.len());
        assert_eq!(names.box_txtr, format!("{}_crate_txtr", names.box_scene));
    }

    #[test]
    fn scene_big_string_edits_support_length_changes_and_empty_names() {
        let mut bytes = [encoded_big_string("template-name"), vec![0xAA, 0xBB]].concat();
        replace_big_string(&mut bytes, "template-name", "short", 1).unwrap();
        assert_eq!(
            bytes,
            [encoded_big_string("short"), vec![0xAA, 0xBB]].concat()
        );

        let marker = b"marker";
        let mut empty = [marker.as_slice(), &[0, 0xCC]].concat();
        set_empty_big_string_after(&mut empty, marker, "shape_name").unwrap();
        assert_eq!(
            empty,
            [
                marker.as_slice(),
                encoded_big_string("shape_name").as_slice(),
                &[0xCC],
            ]
            .concat()
        );
    }

    #[test]
    fn multi_job_requires_adult_elder_normal_only_and_derived_priority() {
        let value = serde_json::json!({
            "schema_version": 2,
            "slug": "fixture-tattoos",
            "catalog_name": "Fixture Tattoos",
            "catalog_description": "Adult and elder structural fixture",
            "ages": ["adult", "elder"],
            "compatibility": {
                "plantsim": false,
                "vampire": false,
                "werewolf": false,
                "zombie": false,
                "servo": false,
                "bigfoot": false
            },
            "identity": {"box_guid": "0x12345678"},
            "tattoos": [{
                "key": "kiryu",
                "menu_label": "Kiryu",
                "menu_order": 0,
                "layer_order": 0,
                "priority": 0x65,
                "input_pngs": {"am": "image.png", "af": null},
                "identity": {
                    "overlay_group_id": "0x23456789",
                    "family_uuid": "11111111-2222-4333-8444-555555555555"
                }
            }]
        });
        let mut job: MultiJob = serde_json::from_value(value).unwrap();
        validate_multi_job(&job).unwrap();
        assert_eq!(expected_merged_resource_count(&job), 49);
        job.tattoos[0].priority = 0x66;
        assert!(validate_multi_job(&job).is_err());
        job.tattoos[0].priority = 0x65;
        job.compatibility.vampire = true;
        assert!(validate_multi_job(&job).is_err());
    }
}

pub(crate) fn build_asset(value: serde_json::Value, output: &str) -> Result<serde_json::Value> {
    let job: MultiJob = serde_json::from_value(value)?;
    validate_multi_job(&job)?;
    let overlay = Path::new("tattoo-overlay");
    let controller = Path::new("tattoo-controller");
    let face = Path::new("tattoo-face");
    build_merged(
        &job,
        Path::new(""),
        overlay,
        controller,
        face,
        Path::new(output),
    )?;
    Ok(serde_json::to_value(validate_merged(
        &job,
        Path::new(""),
        overlay,
        controller,
        face,
        Path::new(output),
    )?)?)
}

pub(crate) fn validate_asset(value: serde_json::Value, output: &str) -> Result<serde_json::Value> {
    let job: MultiJob = serde_json::from_value(value)?;
    validate_multi_job(&job)?;
    Ok(serde_json::to_value(validate_merged(
        &job,
        Path::new(""),
        Path::new("tattoo-overlay"),
        Path::new("tattoo-controller"),
        Path::new("tattoo-face"),
        Path::new(output),
    )?)?)
}
