//! Compare sylvia_app::package_library and initial_character_state with the frozen Python.
//!
//! Regenerate with `tests/vectors/generate_package_library_vectors.py` (needs the snapshot's
//! Python). Repository archives and the SRD record are read from the repository; built archives
//! are rebuilt with this crate's Ruleset compiler and materializer.

use serde_json::json;
use std::cell::RefCell;
use std::path::PathBuf;
use sylvia_app::initial_character_state::{
    initial_character_input_schema, validate_initial_character_contract,
};
use sylvia_app::package_library::{
    declaration_admitted, dependency_only_manifest_rebind, install_package_archive, project_surfaces,
    projection_bytes, run_migration_outside_sqlite, state_predicates_match, PackageStorePort,
};
use sylvia_app::package_materialization::{materialize_package_archive, NormalizedPackageDraft};
use sylvia_app::ruleset_source::compile_ruleset_source;
use sylvia_base::hash::sha256_hex;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{Json, JsonMap};
use sylvia_domain::error::{DomainError, DomainResult};
use sylvia_domain::package_archive::AdmittedPackageArchive;
use sylvia_domain::package_library::{
    canonical_json_bytes, closure_sha256, CandidateStatus, PackageCandidate, StatePredicate,
    SurfaceDeclaration, ToolDeclaration,
};

const VECTORS: &str = include_str!("vectors/package_library_vectors.json");

fn repository() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
}

fn step<'a>(value: &'a mut Json, part: &Json) -> &'a mut Json {
    match part {
        Json::String(key) => value.get_mut(key.as_str()).unwrap(),
        index => value.get_mut(index.as_u64().unwrap() as usize).unwrap(),
    }
}

fn apply(document: &Json, edits: &Json) -> Json {
    let mut document = document.clone();
    for edit in edits.as_array().into_iter().flatten() {
        let (op, path, value) = (edit[0].as_str().unwrap(), edit[1].as_array().unwrap(), &edit[2]);
        let mut parent = &mut document;
        for part in &path[..path.len() - 1] {
            parent = step(parent, part);
        }
        match (op, &path[path.len() - 1]) {
            ("delete", Json::String(key)) => {
                parent.as_object_mut().unwrap().shift_remove(key.as_str());
            }
            ("delete", index) => {
                parent.as_array_mut().unwrap().remove(index.as_u64().unwrap() as usize);
            }
            ("append", last) => step(parent, last).as_array_mut().unwrap().push(value.clone()),
            (_, Json::String(key)) => {
                parent.as_object_mut().unwrap().insert(key.clone(), value.clone());
            }
            (_, index) => *step(parent, index) = value.clone(),
        }
    }
    document
}

fn key_order(value: &Json) -> Json {
    let mut order = Vec::new();
    let mut pending = vec![value];
    while let Some(item) = pending.pop() {
        match item {
            Json::Object(map) => {
                order.push(Json::Array(map.keys().map(|key| Json::from(key.as_str())).collect()));
                pending.extend(map.values().rev());
            }
            Json::Array(items) => pending.extend(items.iter().rev()),
            _ => {}
        }
    }
    Json::Array(order)
}

fn error_outcome(error: &DomainError) -> Vec<Json> {
    let diagnostics: Vec<Json> =
        error.diagnostics().iter().map(|item| Json::Object(item.as_payload())).collect();
    vec![
        Json::from(error.python_class()),
        Json::from(error.message()),
        Json::Array(diagnostics),
        error.code().map(Json::from).unwrap_or(Json::Null),
    ]
}

/// The generator's recording store.
#[derive(Default)]
struct Store(RefCell<Vec<Json>>);

impl PackageStorePort for Store {
    type Error = DomainError;

    fn stage_package_archive(&self, admitted: &AdmittedPackageArchive, install_id: &str) -> DomainResult<()> {
        self.0.borrow_mut().push(json!(["stage", install_id, closure_sha256(&admitted.package_lock)]));
        Ok(())
    }

    fn publish_package_archive(
        &self,
        admitted: &AdmittedPackageArchive,
        install_id: &str,
    ) -> DomainResult<PackageCandidate> {
        self.0.borrow_mut().push(json!(["publish", install_id]));
        PackageCandidate::new(
            "candidate",
            install_id,
            CandidateStatus::Validated,
            closure_sha256(&admitted.package_lock),
            admitted.package_lock.clone(),
            JsonMap::new(),
        )
    }
}

fn install_outcome(archive: &[u8]) -> Json {
    let store = Store::default();
    let result = install_package_archive(&store, archive, "install-1");
    let calls = Json::Array(store.0.borrow().clone());
    match result {
        Ok(candidate) => json!(["ok", calls, candidate.closure_sha256()]),
        Err(error) => {
            let mut outcome = error_outcome(&error);
            outcome.push(calls);
            Json::Array(outcome)
        }
    }
}

/// The generator's `built_archive(spec)`, through this crate's compiler and materializer.
fn built_archive(spec: &Json, vectors: &Json) -> DomainResult<Vec<u8>> {
    let mut edits = vectors["installable"].as_array().unwrap().clone();
    edits.extend(spec.get("source_edits").and_then(Json::as_array).cloned().unwrap_or_default());
    let source = pyjson::dumps(&apply(&vectors["small"], &Json::Array(edits)), Dump::DEFAULT);
    let draft = compile_ruleset_source(source.as_bytes())?.draft;
    let mut payloads = draft.payloads.clone();
    for (path, text) in spec.get("payloads").and_then(Json::as_object).into_iter().flatten() {
        let data = text.as_str().unwrap().as_bytes().to_vec();
        match payloads.iter_mut().find(|(name, _)| name == path) {
            Some((_, slot)) => *slot = data,
            None => payloads.push((path.clone(), data)),
        }
    }
    if let Some(link) = spec.get("link").and_then(Json::as_array) {
        let (_, declarations) =
            payloads.iter_mut().find(|(name, _)| name == "content/content-declarations.json").unwrap();
        let mut value = pyjson::loads(std::str::from_utf8(declarations).unwrap()).unwrap();
        let artifacts = &mut value["inclusions"][0]["links"]["artifacts"];
        let mut names: Vec<String> = artifacts
            .as_array()
            .unwrap()
            .iter()
            .chain(link)
            .map(|item| item.as_str().unwrap().to_owned())
            .collect();
        names.sort();
        names.dedup();
        *artifacts = json!(names);
        *declarations = canonical_json_bytes(&value);
    }
    let record = apply(&Json::Object(draft.record.clone()), spec.get("record_edits").unwrap_or(&Json::Null));
    let draft =
        NormalizedPackageDraft::new(record.as_object().unwrap().clone(), payloads, draft.format_version);
    Ok(materialize_package_archive(&[draft])?.archive)
}

fn predicate(spec: &Json) -> StatePredicate {
    StatePredicate {
        namespace: spec[0].as_str().unwrap().into(),
        key: spec[1].as_str().unwrap().into(),
        equals: spec[2].clone(),
        negated: spec[3].as_bool().unwrap(),
    }
}

fn surface(spec: &Json) -> SurfaceDeclaration {
    let text = |key: &str| spec.get(key).and_then(Json::as_str).map(str::to_owned);
    SurfaceDeclaration {
        identity: text("identity").unwrap(),
        capability: text("capability").unwrap(),
        title: text("title"),
        icon: text("icon"),
        renderer: text("renderer"),
        source: spec.get("source").and_then(Json::as_object).cloned(),
        group: text("group"),
        order: spec.get("order").and_then(Json::as_i64).unwrap_or(0),
        default_size: spec.get("default_size").and_then(Json::as_i64).unwrap_or(180),
        minimum_size: spec.get("minimum_size").and_then(Json::as_i64).unwrap_or(96),
        default_placement: text("default_placement").unwrap_or_else(|| "docked".into()),
        allowed_placements: spec
            .get("allowed_placements")
            .and_then(Json::as_array)
            .map(|items| items.iter().map(|item| item.as_str().unwrap().to_owned()).collect())
            .unwrap_or_else(|| vec!["docked".into()]),
        actions: spec
            .get("actions")
            .and_then(Json::as_array)
            .map(|items| items.iter().map(|item| item.as_object().unwrap().clone()).collect())
            .unwrap_or_default(),
    }
}

fn strings(value: &Json) -> Vec<String> {
    value.as_array().unwrap().iter().map(|item| item.as_str().unwrap().to_owned()).collect()
}

#[test]
fn package_library_matches_python() {
    let vectors = pyjson::loads(VECTORS).unwrap();
    let record =
        pyjson::loads(&std::fs::read_to_string(repository().join(vectors["srd"].as_str().unwrap())).unwrap())
            .unwrap();
    let base = &record["participant_artifacts"][0];
    let mut failures = Vec::new();
    let mut check = |label: String, actual: Json, expected: &Json| {
        if &actual != expected {
            failures.push(format!("{label}: python {expected}, rust {actual}"));
        }
    };

    for case in vectors["contracts"].as_array().unwrap() {
        let declaration = apply(base, &case["edits"]);
        let schema = declaration["entity_state_schema"].as_object().unwrap();
        let calculations = declaration["initial_state_calculations"].as_array().unwrap();
        let validation = declaration.get("initial_state_validation");
        let contract = match validate_initial_character_contract(schema, calculations, validation) {
            Ok(()) => json!(["ok", null]),
            Err(error) => Json::Array(error_outcome(&error)),
        };
        check(format!("contract {}", case["edits"]), contract, &case["contract"]);
        let input_schema = match initial_character_input_schema(schema, calculations) {
            Ok(value) => {
                let value = Json::Object(value);
                json!([
                    "ok",
                    sha256_hex(canonical_json_bytes(&value)),
                    sha256_hex(canonical_json_bytes(&key_order(&value)))
                ])
            }
            Err(error) => Json::Array(error_outcome(&error)),
        };
        check(format!("input schema {}", case["edits"]), input_schema, &case["input_schema"]);
    }

    for case in vectors["installs"].as_array().unwrap() {
        if let Some(path) = case.get("repository").and_then(Json::as_str) {
            let archive = std::fs::read(repository().join(path)).unwrap();
            check(format!("install {path}"), install_outcome(&archive), &case["outcome"]);
            continue;
        }
        let spec = &case["built"];
        let actual = match built_archive(spec, &vectors) {
            Ok(archive) => {
                check(format!("built archive {spec}"), Json::from(sha256_hex(&archive)), &case["sha256"]);
                install_outcome(&archive)
            }
            Err(error) => {
                let mut outcome = vec![Json::from("build")];
                outcome.extend(error_outcome(&error));
                Json::Array(outcome)
            }
        };
        check(format!("install {spec}"), actual, &case["outcome"]);
    }

    for case in vectors["predicates"].as_array().unwrap() {
        let predicates: Vec<StatePredicate> =
            case["predicates"].as_array().unwrap().iter().map(predicate).collect();
        let actual = state_predicates_match(&predicates, case["state"].as_object().unwrap());
        check(
            format!("predicates {} {}", case["predicates"], case["state"]),
            Json::from(actual),
            &case["outcome"],
        );
    }

    let tool = ToolDeclaration {
        name: "t".into(),
        capability: "c1".into(),
        input_schema: JsonMap::new(),
        state_predicates: vec![predicate(&json!(["ns", "phase", "open", false]))],
        turn_statuses: vec!["open".into()],
        writers: vec!["dm".into()],
    };
    for case in vectors["admissions"].as_array().unwrap() {
        let actual = declaration_admitted(
            &tool,
            case["state"].as_object().unwrap(),
            case["status"].as_str().unwrap(),
            case["writer"].as_str().unwrap(),
            &strings(&case["capabilities"]),
        );
        check(format!("admission {case}"), Json::from(actual), &case["outcome"]);
    }

    let specs: Vec<SurfaceDeclaration> =
        vectors["surface_specs"].as_array().unwrap().iter().map(surface).collect();
    for case in vectors["surfaces"].as_array().unwrap() {
        let active = strings(&case["active"]);
        let result = match case.get("extra") {
            Some(extra) => project_surfaces(&[], &[surface(extra)], &active).map(|_| Json::Null),
            None => project_surfaces(&specs[..3], &specs, &active).map(|projection| {
                json!({
                    "prompt_fragment_ids": projection.prompt_fragment_ids,
                    "ui_surface_ids": projection.ui_surface_ids,
                    "bytes": String::from_utf8(projection_bytes(&projection)).unwrap(),
                })
            }),
        };
        let actual = match result {
            Ok(value) => json!(["ok", value]),
            Err(error) => Json::Array(error_outcome(&error)),
        };
        if case.get("extra").is_some() && actual[0] == "ok" && case["outcome"][0] == "ok" {
            continue;
        }
        check(format!("surfaces {}", case["active"]), actual, &case["outcome"]);
    }

    for case in vectors["rebinds"].as_array().unwrap() {
        let actual = match dependency_only_manifest_rebind(
            case["original"].as_str().unwrap(),
            case["current"].as_str().unwrap(),
        ) {
            Ok(value) => json!(["ok", value]),
            Err(error) => Json::Array(error_outcome(&error)),
        };
        check(format!("rebind {case}"), actual, &case["outcome"]);
    }

    for case in vectors["migrations"].as_array().unwrap() {
        let state = case["state"].as_object().unwrap();
        let migrate = |state: JsonMap| -> Json {
            let mut state = state;
            match case["migrate"].as_str().unwrap() {
                "identity" => Json::Object(state),
                "list" => json!([1]),
                "plain_key" => {
                    state.insert("plain".into(), json!(1));
                    Json::Object(state)
                }
                _ => {
                    state.insert("ns.added".into(), json!({"x": 1}));
                    Json::Object(state)
                }
            }
        };
        let actual = match run_migration_outside_sqlite(state, migrate) {
            Ok(value) => json!(["ok", value]),
            Err(error) => Json::Array(error_outcome(&error)),
        };
        check(format!("migration {}", case["migrate"]), actual, &case["outcome"]);
    }

    assert!(failures.is_empty(), "{} differ: {:#?}", failures.len(), &failures[..failures.len().min(8)]);
}
