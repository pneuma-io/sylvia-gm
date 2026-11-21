//! `application/srd_lite_character_compatibility.py` and `application/_srd_lite_character_legacy.py`:
//! the exact retained Lite character contract admitted through equipment migration.
//!
//! Python keeps the retained Lite 0.2 contract as a compressed literal. Here it is data,
//! `srd_lite_legacy_character_contract.json`, exported by `generate_srd_lite_legacy_contract.py`;
//! its fingerprint is checked before every use, as Python checks it.

use std::sync::OnceLock;
use sylvia_base::{pyjson, Json, JsonMap};
use sylvia_domain::authority::{PackageLockEntry, PackageRole};
use sylvia_domain::error::{DomainError, DomainResult};
use sylvia_domain::package_library::{participant_schema_sha256, ParticipantArtifactDeclaration};

/// `SOURCE_SCHEMA`.
pub const SOURCE_SCHEMA: &str = "f9386ab657153eadab17ff62e296fa40e863db70d8dcfd5a40832398100fdd88";
/// `TARGET_SCHEMA`.
pub const TARGET_SCHEMA: &str = "78a45a5043067374dd53c7b79bf20dfc0463911cc84828acc5a35d80f41a0569";

/// `LEGACY_CHARACTER_CONTRACT`.
pub fn legacy_character_contract() -> &'static JsonMap {
    static CONTRACT: OnceLock<JsonMap> = OnceLock::new();
    CONTRACT.get_or_init(|| {
        let value =
            pyjson::loads(include_str!("srd_lite_legacy_character_contract.json")).expect("exported JSON");
        value.as_object().cloned().expect("an object")
    })
}

fn object(value: &Json) -> Option<JsonMap> {
    value.as_object().cloned()
}

fn calculations(value: &Json) -> Vec<JsonMap> {
    value.as_array().into_iter().flatten().filter_map(Json::as_object).cloned().collect()
}

/// `retained_equipment_character_contract(package, declaration, source_schema)`: one trusted old
/// contract; unknown pairs keep the exact-schema rejection.
pub fn retained_equipment_character_contract(
    package: &PackageLockEntry,
    declaration: &ParticipantArtifactDeclaration,
    source_schema: &JsonMap,
) -> DomainResult<Option<ParticipantArtifactDeclaration>> {
    retained_contract(package, declaration, source_schema, TARGET_SCHEMA)
}

/// The check with `target` standing for `TARGET_SCHEMA`; the tests use the bundled declaration's
/// hash, as no SRD Lite 0.3.0 record survives to supply the real one.
fn retained_contract(
    package: &PackageLockEntry,
    declaration: &ParticipantArtifactDeclaration,
    source_schema: &JsonMap,
    target: &str,
) -> DomainResult<Option<ParticipantArtifactDeclaration>> {
    let text_is = |key: &str, expected: &str| source_schema.get(key).and_then(Json::as_str) == Some(expected);
    if package.package_id() != "sylvia.srd-lite.bg3"
        || package.version() != "0.3.0"
        || package.role() != PackageRole::Primary
        || declaration.kind != "player_character"
        || declaration.schema_id != "sylvia.srd-lite.bg3.player-character"
        || declaration.schema_sha256 != target
        || !text_is("id", &declaration.schema_id)
        || !text_is("content_sha256", SOURCE_SCHEMA)
    {
        return Ok(None);
    }
    let initial: Vec<Json> =
        declaration.initial_state_calculations.iter().cloned().map(Json::Object).collect();
    let actual = participant_schema_sha256(
        &declaration.schema,
        declaration.entity_state_schema.as_ref(),
        &initial,
        declaration.initial_state_validation.as_ref(),
    )?;
    if actual != target {
        return Ok(None);
    }
    let old = legacy_character_contract();
    let schema = old.get("schema").and_then(object).unwrap_or_default();
    let entity_state_schema = old.get("entity_state_schema").and_then(object);
    let old_calculations = old.get("initial_state_calculations").cloned().unwrap_or(Json::Array(Vec::new()));
    let validation = old.get("initial_state_validation").and_then(object);
    let fingerprint = participant_schema_sha256(
        &schema,
        entity_state_schema.as_ref(),
        old_calculations.as_array().map(Vec::as_slice).unwrap_or_default(),
        validation.as_ref(),
    )?;
    if fingerprint != SOURCE_SCHEMA {
        return Err(DomainError::value("Retained Lite character migration contract changed"));
    }
    ParticipantArtifactDeclaration::new(
        &old["kind"],
        &old["schema_id"],
        &Json::from(SOURCE_SCHEMA),
        schema,
        entity_state_schema,
        calculations(&old_calculations),
        validation,
    )
    .map(Some)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bundled_packages::{BundledPackages, BundledSources};
    use serde_json::json;
    use sylvia_base::hash::sha256_hex;
    use sylvia_domain::package_library::canonical_json_bytes;

    fn digest(value: &Json) -> String {
        sha256_hex(canonical_json_bytes(value))
    }

    fn project(declaration: Option<ParticipantArtifactDeclaration>) -> Json {
        let Some(declaration) = declaration else { return Json::Null };
        let optional = |value: Option<JsonMap>| value.map(Json::Object).unwrap_or(Json::Null);
        json!({
            "kind": declaration.kind,
            "schema_id": declaration.schema_id,
            "schema_sha256": declaration.schema_sha256,
            "schema": digest(&Json::Object(declaration.schema)),
            "entity_state_schema": digest(&optional(declaration.entity_state_schema)),
            "initial_state_calculations": digest(&Json::Array(
                declaration.initial_state_calculations.into_iter().map(Json::Object).collect())),
            "initial_state_validation": digest(&optional(declaration.initial_state_validation)),
        })
    }

    /// `generate_srd_lite_compat_vectors.py`: one input changed at a time from an accepted base.
    #[test]
    fn retained_contract_matches_python() {
        let vectors = pyjson::loads(include_str!("../tests/vectors/srd_lite_compat_vectors.json")).unwrap();
        let root = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..");
        let bundled = BundledPackages::new(BundledSources::from_repository_root(&root));
        let target = vectors["target"].as_str().unwrap();
        let mut declaration = bundled.bundled_rift_participant_declaration().unwrap();
        declaration.schema_sha256 = target.to_owned();
        assert_eq!(legacy_character_contract()["schema_sha256"], SOURCE_SCHEMA);
        for case in vectors["cases"].as_array().unwrap() {
            let mut inputs = vectors["base"].clone();
            for (part, values) in case["change"].as_object().unwrap() {
                for (key, value) in values.as_object().unwrap() {
                    inputs[part][key] = value.clone();
                }
            }
            let role = if inputs["package"]["role"] == "primary" {
                PackageRole::Primary
            } else {
                PackageRole::Dependency
            };
            let package = PackageLockEntry::new(
                inputs["package"]["id"].as_str().unwrap(),
                inputs["package"]["version"].as_str().unwrap(),
                "a".repeat(64),
                role,
                0,
                Vec::new(),
                Vec::new(),
            )
            .unwrap();
            let mut candidate = declaration.clone();
            candidate.kind = inputs["declaration"]["kind"].as_str().unwrap().into();
            candidate.schema_id = inputs["declaration"]["schema_id"].as_str().unwrap().into();
            let sha = &inputs["declaration"]["schema_sha256"];
            candidate.schema_sha256 =
                if sha == "target" { target.into() } else { sha.as_str().unwrap().into() };
            if inputs["declaration"]["schema"] == "changed" {
                candidate.schema.insert("title".into(), json!("Changed"));
            }
            let source = if inputs["source"].get("missing").is_some() {
                JsonMap::new()
            } else {
                json!({"id": inputs["source"]["id"], "content_sha256": inputs["source"]["content_sha256"]})
                    .as_object()
                    .cloned()
                    .unwrap()
            };
            let actual = match retained_contract(&package, &candidate, &source, target) {
                Ok(result) => json!(["ok", project(result)]),
                Err(error) => json!([error.python_class(), error.message()]),
            };
            assert_eq!(actual, case["outcome"], "{}", case["change"]);
        }
    }
}
