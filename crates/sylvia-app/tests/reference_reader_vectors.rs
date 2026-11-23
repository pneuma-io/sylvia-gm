//! Compare builder_references' reader half and compatible_character_copy with the frozen Python:
//! `launch_references`, `candidate_for_references`, `with_character_examples`,
//! `reader_for_references` (every path, and reads of the generated files, a few package files and
//! two unavailable paths), `authored_character_state` and `copy_compatible_character`.
//!
//! The owner and archives are the selection vectors' fake owner; the references are the Last Cart
//! Through closure Python captured.
//!
//! Regenerate with `tests/vectors/generate_reference_reader_vectors.py` (needs the snapshot's
//! Python).

mod selection_owner;

use selection_owner::{error_of, fresh_work, key, Owner};
use serde_json::json;
use std::path::{Path, PathBuf};
use sylvia_app::builder_references::{
    candidate_for_references, launch_references, reader_for_references, with_character_examples,
};
use sylvia_app::bundled_packages::{BundledPackages, BundledSources};
use sylvia_app::bundled_starter_characters::bundled_starter_participants;
use sylvia_app::character_profiles::save_character_profile;
use sylvia_app::compatible_character_copy::{
    authored_character_state, copy_compatible_character, CopyDestination,
};
use sylvia_app::reusable_participants::save_reusable_participant;
use sylvia_base::hash::sha256_hex;
use sylvia_base::{pyjson, Json, JsonMap};
use sylvia_domain::authority::{PackageRole, WriterRole};
use sylvia_domain::campaign_plan::PackageBinding;
use sylvia_domain::session::{
    SessionContext, SessionMessage, SessionMessageKind, SessionRecord, SessionRecordFields,
};
use sylvia_domain::DomainResult;

fn vectors() -> Json {
    pyjson::loads(include_str!("vectors/reference_reader_vectors.json")).expect("vector file is valid JSON")
}

fn repository() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
}

fn outcome<T>(result: DomainResult<T>, root: &Path, ok: impl FnOnce(T) -> Json) -> Json {
    match result {
        Ok(value) => json!(["ok", ok(value)]),
        Err(error) => error_of(&error, root),
    }
}

fn mapping(value: &Json) -> Option<&JsonMap> {
    value.as_object()
}

fn context(messages: &Json) -> SessionContext {
    let session = SessionRecord::new(SessionRecordFields {
        session_id: "builder-reader".into(),
        campaign_id: None,
        role: WriterRole::Builder,
        provider_id: "provider".into(),
        model_id: "model".into(),
        ..Default::default()
    })
    .unwrap();
    let messages = messages
        .as_array()
        .unwrap()
        .iter()
        .enumerate()
        .map(|(index, item)| {
            SessionMessage::new(
                "builder-reader",
                index as i64 + 1,
                format!("message-{index}"),
                SessionMessageKind::parse(item[0].as_str().unwrap()).unwrap(),
                &item[1],
                None,
            )
            .unwrap()
        })
        .collect();
    SessionContext { session, messages }
}

fn check(failures: &mut Vec<String>, group: &str, name: &Json, python: &Json, rust: Json) {
    if &rust != python {
        let (python, rust) = (python.to_string(), rust.to_string());
        failures.push(format!(
            "{group} {name}: python {}, rust {}",
            &python[..python.len().min(600)],
            &rust[..rust.len().min(600)]
        ));
    }
}

#[test]
fn reference_reader_matches_python() {
    let vectors = vectors();
    let root = repository();
    let bundled = BundledPackages::new(BundledSources::from_repository_root(&root));
    let archives = selection_owner::archives(&bundled, &root, vectors["riichi"].as_str().unwrap());
    let work = fresh_work("reference-reader-vectors");
    let storage = work.join("storage");
    std::fs::create_dir_all(&storage).unwrap();
    let names: Vec<String> = ["cart", "debt", "retained", "current", "riichi"].map(str::to_owned).to_vec();
    let owner = Owner::new(&archives, &names, &Json::Null);
    let source = serde_json::to_vec(&vectors["profile_source"]).unwrap();
    let profile = save_character_profile(&storage, "reader-profile", &source).unwrap();
    let mut failures = Vec::new();

    for case in vectors["launch"].as_array().unwrap() {
        let rust = outcome(launch_references(&context(&case["messages"])), &work, |found| {
            found.map_or(Json::Null, Json::Object)
        });
        check(&mut failures, "launch", &case["name"], &case["outcome"], rust);
    }
    for case in vectors["candidate"].as_array().unwrap() {
        let rust = outcome(candidate_for_references(&owner, mapping(&case["references"])), &work, |found| {
            found.map_or(Json::Null, |candidate| json!(candidate.candidate_id()))
        });
        check(&mut failures, "candidate", &case["name"], &case["outcome"], rust);
    }
    for case in vectors["examples"].as_array().unwrap() {
        let result = with_character_examples(
            &owner,
            &bundled,
            &storage,
            mapping(&case["references"]).unwrap(),
            case["purpose"].as_str().unwrap(),
        );
        let rust = outcome(result, &work, Json::Object);
        check(&mut failures, "examples", &case["name"], &case["outcome"], rust);
    }
    for case in vectors["reader"].as_array().unwrap() {
        let name = &case["name"];
        if name == "examples_profile" {
            assert_eq!(case["references"]["character_profile"], profile.snapshot(), "the saved profile");
        }
        let reader = match reader_for_references(&owner, mapping(&case["references"])) {
            Ok(reader) => reader,
            Err(error) => {
                check(&mut failures, "reader", name, &case["outcome"], error_of(&error, &work));
                continue;
            }
        };
        let Some(reader) = reader else {
            check(&mut failures, "reader", name, &case["outcome"], json!(["ok", null]));
            continue;
        };
        let expected = &case["outcome"][1];
        let mut reads = JsonMap::new();
        for path in expected["reads"]
            .as_object()
            .map(|reads| reads.keys().cloned().collect())
            .unwrap_or_else(Vec::new)
        {
            let read = outcome(reader.read(&path), &work, |text| json!(sha256_hex(text.as_bytes())));
            reads.insert(path, read);
        }
        let rust = json!(["ok", {"paths": reader.paths, "reads": reads}]);
        check(&mut failures, "reader", name, &case["outcome"], rust);
    }

    let cart = &archives["cart"];
    let primary =
        cart.lock.entries().iter().find(|entry| entry.role() == PackageRole::Primary).unwrap().clone();
    let declaration =
        cart.declarations[&key(primary.package_id(), primary.version(), primary.sha256())][0].clone();
    let cart_plan = bundled.bundled_plan("last-cart-through").unwrap().unwrap();
    let setting = &cart_plan.plan.payload["bindings"]["campaign_setting"];
    let binding = PackageBinding {
        package_id: setting["id"].as_str().unwrap().to_owned(),
        version: setting["version"].as_str().unwrap().to_owned(),
        content_sha256: setting["content_sha256"].as_str().unwrap().to_owned(),
    };
    let starter = bundled_starter_participants(&storage, &bundled, &primary, &declaration, &binding, None)
        .unwrap()
        .remove(0);
    for case in vectors["authored"].as_array().unwrap() {
        let mut chosen = declaration.clone();
        if let Some(calculations) = case["calculations"].as_array() {
            chosen.initial_state_calculations =
                calculations.iter().map(|item| item.as_object().unwrap().clone()).collect();
        }
        let rust = outcome(authored_character_state(&starter.entity_state, &chosen), &work, Json::Object);
        check(&mut failures, "authored", &case["name"], &case["outcome"], rust);
    }

    let source = save_reusable_participant(
        &storage,
        "reader-source",
        "player_character",
        "A copied hero.",
        &json!("A hero worth copying."),
        &Json::Object(starter.participant.clone()),
        &Json::Object(authored_character_state(&starter.entity_state, &declaration).unwrap()),
        &primary,
        &declaration,
        &binding,
        None,
        None,
        None,
    )
    .unwrap_or_else(|error| {
        panic!("the copied source was refused: {error:?}\nearlier differences:\n{}", failures.join("\n"))
    });
    assert_eq!(json!(source.artifact_id), vectors["source_artifact_id"], "the copied source");
    let mut sparse = source.clone();
    sparse.authored_profile = None;
    let mut recast = source.clone();
    recast.role = "companion".into();
    let home = pyjson::loads(
        r#"{"type": "object", "properties": {"home": {"type": "string"}}, "required": ["home"], "additionalProperties": false}"#,
    )
    .unwrap();
    let mut no_state_schema = declaration.clone();
    no_state_schema.entity_state_schema = None;
    no_state_schema.initial_state_calculations = Vec::new();
    no_state_schema.initial_state_validation = None;
    let mut narrow = declaration.clone();
    narrow.schema = pyjson::loads(
        r#"{"type": "object", "properties": {"name": {"type": "integer"}}, "required": ["name"], "additionalProperties": true}"#,
    )
    .unwrap()
    .as_object()
    .unwrap()
    .clone();
    let zeros = "0".repeat(64);
    let copies = [
        ("same", &source, source.content_sha256.as_str(), None, &declaration),
        ("stale", &source, zeros.as_str(), None, &declaration),
        ("bad_digest", &source, "ABC", None, &declaration),
        ("sparse", &sparse, sparse.content_sha256.as_str(), None, &declaration),
        ("needs_home", &source, source.content_sha256.as_str(), home.as_object(), &declaration),
        ("recast", &recast, recast.content_sha256.as_str(), None, &declaration),
        ("no_state_schema", &source, source.content_sha256.as_str(), None, &no_state_schema),
        ("narrow_schema", &source, source.content_sha256.as_str(), None, &narrow),
    ];
    let cases = vectors["copy"].as_array().unwrap();
    assert_eq!(cases.len(), copies.len());
    for (case, (name, item, expected, schema, chosen)) in cases.iter().zip(copies) {
        assert_eq!(case["name"], name);
        let operation_id = format!("copy-{name}");
        let destination = CopyDestination {
            operation_id: &operation_id,
            package: &primary,
            declaration: chosen,
            campaign_setting: &binding,
            setting_character_schema: schema,
        };
        let rust =
            outcome(copy_compatible_character(&storage, item, expected, &destination), &work, |saved| {
                json!({"artifact_id": saved.artifact_id, "content_sha256": saved.content_sha256,
                   "entity_state": saved.entity_state})
            });
        check(&mut failures, "copy", &case["name"], &case["outcome"], rust);
    }

    let _ = std::fs::remove_dir_all(&work);
    let total: usize = ["launch", "candidate", "examples", "reader", "authored", "copy"]
        .iter()
        .map(|group| vectors[*group].as_array().unwrap().len())
        .sum();
    assert!(failures.is_empty(), "{} of {total} cases differ:\n{}", failures.len(), failures.join("\n"));
}
