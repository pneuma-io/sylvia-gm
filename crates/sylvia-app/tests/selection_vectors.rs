//! Compare sylvia_app::campaign_library_selection's selection half with the frozen Python, over
//! the generator's fake owner: candidates from real admitted archives (bundled Plan snapshots,
//! the current bundled Rift archive, Riichi 0.1.1), recorded installs, archive-backed package
//! reads, participant declarations from the admitted manifests and exports of a candidate's bytes.
//!
//! The cases share one `BundledPackages`. Python clears its caches before each case; the only
//! cache with an effect outside its value is the starter roster's, which saves the same starter
//! files again, and the storage tree is not compared here.
//!
//! Regenerate with `tests/vectors/generate_selection_vectors.py` (needs the snapshot's Python).

mod selection_owner;

use selection_owner::{error_of, fresh_work, Owner};
use serde_json::json;
use std::collections::HashMap;
use std::path::PathBuf;
use sylvia_app::bundled_packages::{BundledPackages, BundledSources};
use sylvia_app::campaign_library_selection::{
    plan_with_interview_intent, require_completed_plan_characters, resolve_campaign_library_selection,
    resolve_campaign_plan_selection, CampaignLibrarySelection,
};
use sylvia_base::hash::sha256_hex;
use sylvia_base::{pyjson, Json, JsonMap};
use sylvia_domain::package_library::canonical_json_bytes;
use sylvia_domain::DomainResult;

fn vectors() -> Json {
    pyjson::loads(include_str!("vectors/selection_vectors.json")).expect("vector file is valid JSON")
}

fn repository() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
}

fn digest_json(value: &Json) -> String {
    sha256_hex(canonical_json_bytes(value))
}

fn selection_shape(
    result: &CampaignLibrarySelection<'_, Owner>,
    bundled: &BundledPackages,
) -> DomainResult<Json> {
    let payload = Json::Object(result.plan.plan.payload.clone());
    Ok(json!({
        "candidate_id": result.candidate.candidate_id(),
        "closure_sha256": result.candidate.closure_sha256(),
        "sources": {
            "ruleset": result.sources.ruleset.as_payload(),
            "setting": result.sources.setting.as_payload(),
            "closure_sha256": result.sources.closure_sha256,
        },
        "plan": {"artifact_id": result.plan.artifact_id, "setup_id": result.plan.setup_id,
                 "payload_sha256": digest_json(&payload)},
        "participants": result.participants.iter().map(|item| item.artifact_id.clone()).collect::<Vec<_>>(),
        "builtin": result.participants.iter().map(|item| item.builtin_starter).collect::<Vec<_>>(),
        "entity_reference": result.entity_reference,
        "required": result.required_participants.iter()
            .map(|item| json!([item.artifact_id, item.role, item.name, item.content_sha256]))
            .collect::<Vec<_>>(),
        "party_template_sha256": result.party_template.as_ref().map(digest_json),
        "party_options": result.party_options(bundled)?,
    }))
}

fn check(failures: &mut Vec<String>, label: String, actual: Json, expected: &Json) {
    if actual != *expected {
        let shown: String = actual.to_string().chars().take(900).collect();
        let wanted: String = expected.to_string().chars().take(900).collect();
        failures.push(format!("{label}: python {wanted}, rust {shown}"));
    }
}

#[test]
fn selection_matches_python() {
    let vectors = vectors();
    let root = repository();
    let bundled = BundledPackages::new(BundledSources::from_repository_root(&root));
    let riichi = vectors["archives"]["riichi"].as_str().unwrap();
    let archives = selection_owner::archives(&bundled, &root, riichi);
    assert_eq!(archives["cart"].closure, vectors["cart_closure"].as_str().unwrap());
    let work = fresh_work("selection-vectors");
    let storage = work.join("storage");
    let (sibling, saved) = selection_owner::storage(&bundled, &root, &storage, &archives["cart"]);
    assert_eq!(sibling, vectors["sibling"]);
    assert_eq!(json!(saved), vectors["saved"]);

    let names = |owner: &str| -> Vec<String> {
        vectors["owners"][owner].as_array().unwrap().iter().map(|n| n.as_str().unwrap().to_owned()).collect()
    };
    let mut failures = Vec::new();
    for case in vectors["resolve"].as_array().unwrap() {
        let owner = Owner::new(&archives, &names(case["owner"].as_str().unwrap()), &case["variant"]);
        let candidate_id = case["candidate_id"].as_str().unwrap();
        let setting_id = case["setting_id"].as_str().unwrap();
        let plan_id = case["plan_id"].as_str().unwrap();
        let result = if case["kind"] == "plan" {
            resolve_campaign_plan_selection(
                &owner,
                &bundled,
                &storage,
                candidate_id,
                setting_id,
                plan_id,
                case["setup"].as_str(),
            )
        } else {
            resolve_campaign_library_selection(&owner, &bundled, &storage, candidate_id, setting_id, plan_id)
        };
        let outcome = match result.and_then(|selection| selection_shape(&selection, &bundled)) {
            Ok(shape) => json!(["ok", shape]),
            Err(error) => error_of(&error, &work),
        };
        let label = format!(
            "{} {} {} {plan_id} {candidate_id} {setting_id} {}",
            case["kind"], case["owner"], case["variant"], case["setup"]
        );
        check(&mut failures, label.clone(), outcome, &case["outcome"]);
        check(
            &mut failures,
            format!("{label} installs"),
            Json::Array(owner.installs.borrow().clone()),
            &case["installs"],
        );
    }

    for case in vectors["complete"].as_array().unwrap() {
        let owner = Owner::new(&archives, &names(case["owner"].as_str().unwrap()), &case["variant"]);
        let plan = bundled.bundled_plan(case["plan_id"].as_str().unwrap()).unwrap().unwrap().plan;
        let outcome =
            match require_completed_plan_characters(&owner, case["candidate_id"].as_str().unwrap(), &plan) {
                Ok(()) => json!(["ok", null]),
                Err(error) => error_of(&error, &work),
            };
        check(
            &mut failures,
            format!("complete {} {}", case["candidate_id"], case["plan_id"]),
            outcome,
            &case["outcome"],
        );
    }

    let intent_owner = Owner::new(&archives, &names("all"), &Json::Null);
    let mut resolved = HashMap::new();
    for case in vectors["intent"].as_array().unwrap() {
        let plan_id = case["plan_id"].as_str().unwrap().to_owned();
        if !resolved.contains_key(&plan_id) {
            let selection = resolve_campaign_plan_selection(
                &intent_owner,
                &bundled,
                &storage,
                case["candidate_id"].as_str().unwrap(),
                "pneuma.severed-lands",
                &plan_id,
                None,
            )
            .unwrap();
            resolved.insert(plan_id.clone(), selection);
        }
        let selection = &resolved[&plan_id];
        let markdown = case["markdown"].as_str();
        let outcome =
            match plan_with_interview_intent(selection, case["intent"].as_object().unwrap(), markdown) {
                Ok(plan) => {
                    let payload = Json::Object(plan.payload.clone());
                    let original = Json::Object(selection.plan.plan.payload.clone());
                    let others = |value: &Json| -> JsonMap {
                        value
                            .as_object()
                            .unwrap()
                            .iter()
                            .filter(|(key, _)| *key != "boundaries" && *key != "gm_guidance")
                            .map(|(key, value)| (key.clone(), value.clone()))
                            .collect()
                    };
                    let base = markdown.unwrap_or(&selection.plan.plan.readable_markdown);
                    json!(["ok", {
                        "boundaries": payload["boundaries"],
                        "instructions": payload["gm_guidance"]["instructions"],
                        "markdown_sha256": sha256_hex(plan.readable_markdown.as_bytes()),
                        "markdown_tail": plan.readable_markdown[base.len()..],
                        "same_otherwise": others(&payload) == others(&original),
                        "content_sha256": plan.content_sha256,
                    }])
                }
                Err(error) => error_of(&error, &work),
            };
        check(
            &mut failures,
            format!("intent {plan_id} {} {:?}", case["intent"], markdown),
            outcome,
            &case["outcome"],
        );
    }
    let _ = std::fs::remove_dir_all(&work);
    assert!(failures.is_empty(), "{} differ: {:#?}", failures.len(), &failures[..failures.len().min(6)]);
}
