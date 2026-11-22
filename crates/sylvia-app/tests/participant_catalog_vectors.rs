//! Compare sylvia_app::campaign_library_selection::participant_catalog_payload with the frozen
//! Python, over a fake owner serving real and edited runtimes.
//!
//! Regenerate with `tests/vectors/generate_participant_catalog_vectors.py` (needs the snapshot's Python).

use serde_json::json;
use std::path::PathBuf;
use sylvia_app::campaign_library_selection::{
    character_state_reference, participant_catalog_payload, participant_choice,
};
use sylvia_app::library_sources::LibraryOwner;
use sylvia_app::reusable_participants::ReusableParticipant;
use sylvia_base::{pyjson, Json, JsonMap};
use sylvia_domain::package_archive::zipfile::ZipArchive;
use sylvia_domain::package_library::PackageCandidate;
use sylvia_domain::{DomainError, DomainResult};

fn vectors() -> Json {
    pyjson::loads(include_str!("vectors/participant_catalog_vectors.json"))
        .expect("vector file is valid JSON")
}

fn repository() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
}

/// The generator's `Owner`: one runtime, or none.
struct Owner(Option<String>);

impl LibraryOwner for Owner {
    type Error = DomainError;
    fn package_candidates(&self) -> DomainResult<Vec<PackageCandidate>> {
        unreachable!("not read")
    }
    fn package_candidate(&self, _: &str) -> DomainResult<PackageCandidate> {
        unreachable!("not read")
    }
    fn package_file_paths(&self, _: &str, _: &str, _: &str, _: i64) -> DomainResult<Vec<String>> {
        let mut paths = vec!["package.json".to_owned()];
        if self.0.is_some() {
            paths.push("runtime/ruleset.yaml".into());
        }
        Ok(paths)
    }
    fn package_text_file(&self, _: &str, _: &str, _: &str, path: &str, _: i64) -> DomainResult<String> {
        match &self.0 {
            Some(text) if path == "runtime/ruleset.yaml" => Ok(text.clone()),
            _ => Err(DomainError::command_rejected("Package source path is unavailable")),
        }
    }
    fn package_record(&self, _: &str, _: &str, _: &str) -> DomainResult<JsonMap> {
        unreachable!("not read")
    }
    fn package_label(&self, _: &str, _: &str, _: &str) -> DomainResult<String> {
        unreachable!("not read")
    }
}

fn participant(spec: &Json, index: usize, package_id: &str) -> ReusableParticipant {
    ReusableParticipant {
        artifact_id: format!("participant-{index:024x}-{index:016x}"),
        role: "player_character".into(),
        name: "Ada".into(),
        summary: "A hero.".into(),
        authored_profile: spec["authored_profile"].as_str().map(str::to_owned),
        content_sha256: "c".repeat(64),
        package_id: package_id.into(),
        package_version: "0.3.29".into(),
        package_sha256: "a".repeat(64),
        schema_id: "schema".into(),
        schema_sha256: "b".repeat(64),
        campaign_setting_id: "pneuma.severed-lands".into(),
        campaign_setting_version: "1.0.0".into(),
        campaign_setting_sha256: "d".repeat(64),
        participant: json!({"name": "Ada"}).as_object().unwrap().clone(),
        entity_state: spec["entity_state"].as_object().unwrap().clone(),
        setting_data: JsonMap::new(),
        source_profile: None,
        source_migration: None,
        builtin_starter: spec["builtin_starter"].as_bool().unwrap(),
    }
}

fn outcome(result: DomainResult<Json>) -> Json {
    match result {
        Ok(value) => json!(["ok", value]),
        Err(error) => {
            let diagnostics: Vec<Json> =
                error.diagnostics().iter().map(|d| Json::Object(d.as_payload())).collect();
            json!([error.python_class(), error.message(), diagnostics])
        }
    }
}

#[test]
fn participant_catalog_payload_matches_python() {
    let vectors = vectors();
    let srd = std::fs::read_to_string(repository().join(vectors["srd"].as_str().unwrap()))
        .unwrap()
        .replace("\r\n", "\n");
    let archive_bytes = std::fs::read(repository().join(vectors["riichi"][0].as_str().unwrap())).unwrap();
    let archive = ZipArchive::open(&archive_bytes).unwrap();
    let info = archive.infos().iter().find(|info| info.filename == vectors["riichi"][1]).unwrap().clone();
    let mut riichi = Vec::new();
    archive
        .read_chunks(&info, 65536, |chunk| {
            riichi.extend_from_slice(chunk);
            Ok::<(), ()>(())
        })
        .unwrap();
    let riichi = String::from_utf8(riichi).unwrap();
    let specs = vectors["participants"].as_array().unwrap();
    let mut failures = Vec::new();
    for case in vectors["cases"].as_array().unwrap() {
        let name = case["runtime"].as_str().unwrap();
        let runtime = match name {
            "riichi" => Some(riichi.clone()),
            "broken" => Some("sylvia_gm_runtime: [\n".into()),
            "absent" => None,
            _ => {
                let mut text = srd.clone();
                for edit in vectors["edits"][name].as_array().unwrap() {
                    let (old, new) = (edit[1].as_str().unwrap(), edit[2].as_str().unwrap());
                    assert_eq!(text.matches(old).count(), 1, "{old}");
                    text = text.replacen(old, new, 1);
                }
                Some(text)
            }
        };
        let owner = Owner(runtime);
        let package_id = case["package_id"].as_str().unwrap();
        let reference = character_state_reference(&owner, package_id, "1.0.0", &"a".repeat(64));
        let reference_json = outcome(reference.clone().map(|r| r.map_or(Json::Null, Json::Object)));
        if reference_json != case["reference"] {
            failures.push(format!("{name} reference: python {}, rust {reference_json}", case["reference"]));
        }
        let item = participant(&specs[0], 0, package_id);
        let payload = outcome(participant_catalog_payload(&owner, &item).map(Json::Object));
        if payload != case["payload"] {
            failures.push(format!("{name} payload: python {}, rust {payload}", case["payload"]));
        }
        if let (Some(choices), Ok(reference)) = (case.get("choices"), reference) {
            for (index, spec) in specs.iter().enumerate() {
                let item = participant(spec, index, "sylvia.srd-lite.bg3");
                let actual = Json::Object(participant_choice(&item, reference.as_ref()));
                if actual != choices[index] {
                    failures.push(format!("{name} choice {index}: python {}, rust {actual}", choices[index]));
                }
            }
        }
    }
    assert!(failures.is_empty(), "{} differ: {:#?}", failures.len(), failures);
}
