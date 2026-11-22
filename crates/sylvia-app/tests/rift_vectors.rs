//! Compare sylvia_app::campaign_rift with the frozen Python (`campaign/rift.py`): template
//! validation, the shipped template, the public setup metadata, party materialization and the
//! final handoff check, all rebuilt from the generator's named inputs.
//!
//! Regenerate with `tests/vectors/generate_rift_vectors.py` (needs the snapshot's Python).

use serde_json::json;
use std::path::PathBuf;
use sylvia_app::bundled_packages::{BundledPackages, BundledSources};
use sylvia_app::campaign_rift::{
    materialize_rift_selection, rift_public_template, validate_rift_party_handoff, validate_rift_template,
    RiftChoices, RiftPartyPolicy, RiftSelection,
};
use sylvia_app::reusable_participants::ReusableParticipant;
use sylvia_base::hash::sha256_hex;
use sylvia_base::{pyjson, Json, JsonMap};
use sylvia_domain::authority::{PackageLockEntry, PackageRole, Participant, ParticipantFields};
use sylvia_domain::package_library::canonical_json_bytes;
use sylvia_domain::{DomainError, DomainResult};

fn vectors() -> Json {
    pyjson::loads(include_str!("vectors/rift_vectors.json")).expect("vector file is valid JSON")
}

fn repository() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
}

fn error_of(error: &DomainError) -> Json {
    json!([error.python_class(), error.message(), error.code()])
}

fn outcome<T>(result: DomainResult<T>, shape: impl FnOnce(T) -> Json) -> Json {
    match result {
        Ok(value) => json!(["ok", shape(value)]),
        Err(error) => error_of(&error),
    }
}

fn text(value: &Json) -> String {
    value.as_str().unwrap().to_owned()
}

fn optional(value: &Json) -> Option<String> {
    value.as_str().map(str::to_owned)
}

fn object(value: &Json) -> JsonMap {
    value.as_object().unwrap().clone()
}

/// The generator's `ReusableParticipant(**fields)`.
fn reusable(fields: &Json) -> ReusableParticipant {
    ReusableParticipant {
        artifact_id: text(&fields["artifact_id"]),
        role: text(&fields["role"]),
        name: text(&fields["name"]),
        summary: text(&fields["summary"]),
        authored_profile: optional(&fields["authored_profile"]),
        content_sha256: text(&fields["content_sha256"]),
        package_id: text(&fields["package_id"]),
        package_version: text(&fields["package_version"]),
        package_sha256: text(&fields["package_sha256"]),
        schema_id: text(&fields["schema_id"]),
        schema_sha256: text(&fields["schema_sha256"]),
        campaign_setting_id: text(&fields["campaign_setting_id"]),
        campaign_setting_version: text(&fields["campaign_setting_version"]),
        campaign_setting_sha256: text(&fields["campaign_setting_sha256"]),
        participant: object(&fields["participant"]),
        entity_state: object(&fields["entity_state"]),
        setting_data: object(&fields["setting_data"]),
        source_profile: fields["source_profile"].as_object().cloned(),
        source_migration: fields["source_migration"].as_object().cloned(),
        builtin_starter: fields["builtin_starter"].as_bool().unwrap(),
    }
}

fn library(vectors: &Json, names: &Json) -> Vec<ReusableParticipant> {
    names
        .as_array()
        .unwrap()
        .iter()
        .map(|name| reusable(&vectors["reusable"][name.as_str().unwrap()]))
        .collect()
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

fn selection_payload(selection: RiftSelection, plan_markdown: &str) -> Json {
    let state: Vec<Json> = selection
        .initial_state
        .iter()
        .map(|value| json!([value.namespace(), value.key(), value.value()]))
        .collect();
    let keys: Vec<Json> = state.iter().map(|row| json!([row[0], row[1]])).collect();
    let markdown = &selection.campaign_plan_markdown;
    assert!(markdown.starts_with(plan_markdown));
    json!({
        "participants": selection.participants.iter().map(participant_payload).collect::<Vec<_>>(),
        "state_keys": keys,
        "state_sha256": sha256_hex(canonical_json_bytes(&Json::Array(state))),
        "canonical_input": selection.canonical_input,
        "plan_sha256": sha256_hex(canonical_json_bytes(&Json::Object(selection.campaign_plan))),
        "markdown_tail": &markdown[plan_markdown.len()..],
    })
}

fn participant_from(payload: &Json) -> Participant {
    Participant::new(ParticipantFields {
        participant_id: text(&payload["participant_id"]),
        kind: text(&payload["kind"]),
        package_id: text(&payload["package_id"]),
        package_version: text(&payload["package_version"]),
        data: object(&payload["data"]),
        package_sha256: optional(&payload["package_sha256"]),
        schema_id: optional(&payload["schema_id"]),
        schema_sha256: optional(&payload["schema_sha256"]),
        validation_status: text(&payload["validation_status"]),
        adaptation_id: None,
    })
    .unwrap()
}

fn check(failures: &mut Vec<String>, label: String, actual: Json, expected: &Json) {
    if actual != *expected {
        let shown: String = actual.to_string().chars().take(700).collect();
        let wanted: String = expected.to_string().chars().take(700).collect();
        failures.push(format!("{label}: python {wanted}, rust {shown}"));
    }
}

#[test]
fn rift_matches_python() {
    let vectors = vectors();
    let bundled = BundledPackages::new(BundledSources::from_repository_root(&repository()));
    let templates = &vectors["templates"];
    let mut failures = Vec::new();

    for (name, expected) in vectors["validate"].as_object().unwrap() {
        let actual = outcome(validate_rift_template(&templates[name]), |value| value);
        check(&mut failures, format!("validate {name}"), actual, expected);
    }

    let mut first = bundled.bundled_rift_template().unwrap();
    check(&mut failures, "bundled".into(), first.clone(), &vectors["bundled"]);
    first["title"] = json!("edited");
    check(
        &mut failures,
        "bundled again".into(),
        bundled.bundled_rift_template().unwrap(),
        &vectors["bundled"],
    );

    for case in vectors["public"].as_array().unwrap() {
        let participants = library(&vectors, &case["reusable"]);
        let template = case["template"].as_str().map(|name| &templates[name]);
        let actual = outcome(rift_public_template(&bundled, &participants, template), Json::Object);
        check(
            &mut failures,
            format!("public {} {}", case["template"], case["reusable"]),
            actual,
            &case["outcome"],
        );
    }

    let package_record = &vectors["package"];
    let package = PackageLockEntry::new(
        text(&package_record["id"]),
        text(&package_record["version"]),
        text(&package_record["sha256"]),
        PackageRole::Primary,
        0,
        Vec::new(),
        Vec::new(),
    )
    .unwrap();
    let declaration = bundled.bundled_rift_participant_declaration().unwrap();
    let plan = bundled.bundled_rift_campaign_plan().unwrap();
    for (index, case) in vectors["materialize"].as_array().unwrap().iter().enumerate() {
        let companions: Vec<String> = case["companions"].as_array().unwrap().iter().map(text).collect();
        let participants = library(&vectors, &case["reusable"]);
        let schema = case["schema"].as_object();
        let choices = RiftChoices {
            participant_namespace: case["namespace"].as_str().unwrap(),
            lane: case["lane"].as_str().unwrap(),
            archetype_id: case["archetype"].as_str().unwrap(),
            companion_ids: &companions,
            customization_notes: case["notes"].as_str().unwrap(),
            package: &package,
            participant_declaration: &declaration,
            starting_party_policy: if case["policy"].is_null() {
                RiftPartyPolicy::Default
            } else {
                RiftPartyPolicy::Payload(&case["policy"])
            },
            reusable_participants: &participants,
            template: case["template"].as_str().map(|name| &templates[name]),
            selected_plan: case["plan"].as_bool().unwrap().then_some(&plan),
            setting_character_schema: schema,
        };
        let actual = outcome(materialize_rift_selection(&bundled, &choices), |selection| {
            selection_payload(selection, &plan.readable_markdown)
        });
        check(
            &mut failures,
            format!("materialize {index} {} {} {}", case["template"], case["archetype"], case["companions"]),
            actual,
            &case["outcome"],
        );
    }

    let policies = &vectors["handoff_policies"];
    for case in vectors["handoff"].as_array().unwrap() {
        let participants: Vec<Participant> =
            case["participants"].as_array().unwrap().iter().map(participant_from).collect();
        let mut canonical_input = JsonMap::new();
        if !case["selected_party"].is_null() {
            canonical_input.insert("selected_party".into(), case["selected_party"].clone());
        }
        let selection = RiftSelection {
            participants,
            initial_state: Vec::new(),
            canonical_input,
            campaign_plan: JsonMap::new(),
            campaign_plan_markdown: String::new(),
        };
        let policy = &policies[case["policy"].as_str().unwrap()];
        let actual = outcome(validate_rift_party_handoff(&selection, policy), |()| Json::Null);
        check(
            &mut failures,
            format!("handoff {} {}", case["roster"], case["policy"]),
            actual,
            &case["outcome"],
        );
    }

    let counts = (
        vectors["validate"].as_object().unwrap().len(),
        vectors["public"].as_array().unwrap().len(),
        vectors["materialize"].as_array().unwrap().len(),
        vectors["handoff"].as_array().unwrap().len(),
    );
    assert!(
        failures.is_empty(),
        "{} differ (of {counts:?}): {:#?}",
        failures.len(),
        &failures[..failures.len().min(8)]
    );
}
