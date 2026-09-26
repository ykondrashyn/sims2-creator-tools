//! Stable Sim submission identities. Production construction requires gameplay proof.
use crate::sim::{digest, field};
use anyhow::{ensure, Context, Result};
use serde_json::{json, Value};

pub fn prepare(p: &Value) -> Result<Value> {
    let job = p.get("job").unwrap_or(p);
    let id = field(job, "id")?;
    ensure!(
        id.len() >= 16
            && id.len() <= 80
            && id.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-'),
        "Invalid saved Sim identity"
    );
    let creator = field(job, "creator")?;
    let name = field(job, "sim_name")?;
    for value in [creator, name] {
        ensure!(
            !value.is_empty()
                && value.len() <= 48
                && value.as_bytes()[0].is_ascii_alphanumeric()
                && value
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || [b' ', b'_', b'-'].contains(&b)),
            "Creator and Sim name need 1 to 48 letters, numbers, spaces, underscores or hyphens"
        );
    }
    ensure!(
        ["am", "af"].contains(&field(job, "body")?),
        "Choose Adult Male or Adult Female"
    );
    ensure!(
        job["description"].as_str().unwrap_or("").len() <= 2048,
        "Description exceeds 2048 characters"
    );
    ensure!(
        job["partition_confirmed"] == true && job["pose_confirmed"] == true,
        "Confirm the neutral pose and head/body separation before preparing this Sim"
    );
    let hash = digest(format!("sim-v1:{id}"));
    let identity = json!({"seed":hash,"prefix":format!("sim_{}",&hash[..20])});
    ensure!(
        job["identities"].is_null() || job["identities"] == identity,
        "Saved Sim identities are inconsistent"
    );
    let mut result = job
        .as_object()
        .context("Expected a Sim submission")?
        .clone();
    result.insert(
        "texture_encoder".into(),
        json!(crate::texture_encoding::Encoder::from_job(job)?),
    );
    result.insert(
        "refpack_compression".into(),
        json!(crate::package_compression::from_job(job)?),
    );
    result.insert("identities".into(), identity);
    result.insert(
        "filename".into(),
        json!(format!(
            "{}_{}.package",
            creator.replace(' ', "_"),
            name.replace(' ', "_")
        )),
    );
    result.insert("version".into(), json!(1));
    Ok(Value::Object(result))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn identities_belong_to_batch_not_display_name() {
        let mut p = json!({"id":"1234567890abcdef","creator":"Test","sim_name":"Example","body":"am","partition_confirmed":true,"pose_confirmed":true});
        let a = prepare(&p).unwrap();
        p["sim_name"] = json!("Renamed");
        assert_eq!(a["identities"], prepare(&p).unwrap()["identities"]);
        p["id"] = json!("1234567890abcdee");
        assert_ne!(a["identities"], prepare(&p).unwrap()["identities"]);
        p["creator"] = json!("../escape");
        assert!(prepare(&p).is_err());
    }
}
