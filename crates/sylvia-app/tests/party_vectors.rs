//! Compare sylvia_app::campaign_library_selection's party materialization with the frozen Python:
//! each case resolves a library selection over the selection vectors' fake owner and storage
//! root, then materializes a party from it (`materialize_library_party`, or
//! `materialize_template_party` for the Rift party-template setup).
//!
//! The cases share one `BundledPackages`, as the selection vectors do.
//!
//! Regenerate with `tests/vectors/generate_party_vectors.py` (needs the snapshot's Python).

mod selection_owner;

use selection_owner::{error_of, fresh_work, Owner};
use serde_json::json;
use std::path::PathBuf;
use sylvia_app::bundled_packages::{BundledPackages, BundledSources};
use sylvia_app::campaign_library_selection::{
    materialize_library_party, materialize_template_party, resolve_campaign_library_selection,
};
use sylvia_base::hash::sha256_hex;
use sylvia_base::{pyjson, Json};
use sylvia_domain::authority::{Participant, StateValue};
use sylvia_domain::package_library::canonical_json_bytes;

fn vectors() -> Json {
    pyjson::loads(include_str!("vectors/party_vectors.json")).expect("vector file is valid JSON")
}

fn repository() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
}

fn participant_payload(participant: &Participant) -> Json {
    let fields = participant.fields();
    json!({
        "participant_id": fields.participant_id, "kind": fields.kind, "package_id": fields.package_id,
        "package_version": fields.package_version, "data": fields.data, "package_sha256": fields.package_sha256,
        "schema_id": fields.schema_id, "schema_sha256": fields.schema_sha256,
        "validation_status": fields.validation_status,
    })
}

fn state_payload(state: &[StateValue]) -> Json {
    Json::Array(
        state
            .iter()
            .map(|value| {
                json!([value.namespace(), value.key(), sha256_hex(canonical_json_bytes(value.value()))])
            })
            .collect(),
    )
}

#[test]
fn party_matches_python() {
    let vectors = vectors();
    let root = repository();
    let bundled = BundledPackages::new(BundledSources::from_repository_root(&root));
    let archives = selection_owner::archives(&bundled, &root, vectors["riichi"].as_str().unwrap());
    let work = fresh_work("party-vectors");
    let storage = work.join("storage");
    let (sibling, saved) = selection_owner::storage(&bundled, &root, &storage, &archives["cart"]);
    assert_eq!(sibling, vectors["sibling"]);
    assert_eq!(json!(saved), vectors["saved"]);
    let names: Vec<String> = ["cart", "debt", "retained", "current", "riichi"].map(str::to_owned).to_vec();
    let mut failures = Vec::new();
    let cases = vectors["cases"].as_array().unwrap();
    for (index, case) in cases.iter().enumerate() {
        let owner = Owner::new(&archives, &names, &case["variant"]);
        let text = |key: &str| case[key].as_str().unwrap();
        let selection = resolve_campaign_library_selection(
            &owner,
            &bundled,
            &storage,
            text("candidate_id"),
            text("setting_id"),
            text("plan_id"),
        )
        .unwrap();
        let companions: Vec<String> =
            case["companions"].as_array().unwrap().iter().map(|id| id.as_str().unwrap().to_owned()).collect();
        let outcome = if case["kind"] == "library" {
            match materialize_library_party(
                &owner,
                &selection,
                "party-operation",
                text("player"),
                &companions,
            ) {
                Ok((participants, state, party)) => json!(["ok", {
                    "participants": participants.iter().map(participant_payload).collect::<Vec<_>>(),
                    "state": state_payload(&state),
                    "selected_party": party,
                }]),
                Err(error) => error_of(&error, &work),
            }
        } else {
            let intent = case["intent"].as_object().unwrap();
            match materialize_template_party(
                &owner,
                &bundled,
                &selection,
                "party-operation",
                text("player"),
                &companions,
                intent,
            ) {
                Ok((participants, state, party, plan)) => {
                    let base = &selection.plan.plan.readable_markdown;
                    let tail = plan.readable_markdown.strip_prefix(base.as_str()).map(str::to_owned);
                    json!(["ok", {
                        "participants": participants.iter().map(participant_payload).collect::<Vec<_>>(),
                        "state": state_payload(&state),
                        "selected_party": party,
                        "plan_sha256": sha256_hex(canonical_json_bytes(&Json::Object(plan.payload.clone()))),
                        "markdown_tail": tail,
                        "content_sha256": plan.content_sha256,
                    }])
                }
                Err(error) => error_of(&error, &work),
            }
        };
        if outcome != case["outcome"] {
            let shown: String = outcome.to_string().chars().take(900).collect();
            let wanted: String = case["outcome"].to_string().chars().take(900).collect();
            failures.push(format!(
                "{index} {} {} {}: python {wanted}, rust {shown}",
                case["kind"], case["plan_id"], case["player"]
            ));
        }
    }
    let _ = std::fs::remove_dir_all(&work);
    assert!(
        failures.is_empty(),
        "{} of {} differ: {:#?}",
        failures.len(),
        cases.len(),
        &failures[..failures.len().min(6)]
    );
}
