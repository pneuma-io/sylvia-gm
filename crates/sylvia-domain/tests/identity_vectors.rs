//! Compare sylvia-domain's identity and lock types with vectors written by the frozen Python.
//!
//! Regenerate with `tests/vectors/generate_identity_vectors.py` (needs the snapshot's Python).
//! The shipped package records are read from the Python owner's startup recording, which is
//! where the generator read them too.

use sylvia_base::hash::sha256_hex;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{
    canonical_payload_is_safe, is_reserved_canonical_channel, mechanic_outcome_is_safe, response_blocks_text,
    validate_authority_identity_value, validate_response_blocks, MechanicOutcome, MechanicsChoiceOption,
    MechanicsChoiceRequest, PackageDependency, PackageLock, PackageLockEntry, PackageRole, Participant,
    ParticipantFields, ParticipantMutation, StateMutation, StateValue,
};
use sylvia_domain::package_library::{
    activation_payload_sha256, canonical_json_bytes, closure_sha256, content_root_for_record,
    parse_canonical_json_bytes, parse_content_json_bytes, participant_schema_sha256,
    validate_portable_path_value, ActivationCommand, CandidateStatus, ExactPackageDependency,
    JsonShapeLimits, PackageCandidate, PackageFile,
};
use sylvia_domain::{pytext, DomainError, DomainResult};

fn vectors() -> Json {
    pyjson::loads(include_str!("vectors/identity_vectors.json")).expect("vector file is valid JSON")
}

fn list<'a>(vectors: &'a Json, name: &str) -> &'a Vec<Json> {
    vectors[name].as_array().unwrap_or_else(|| panic!("vector list {name}"))
}

fn text(value: &Json) -> String {
    value.as_str().expect("text").to_owned()
}

fn kind(error: &DomainError) -> &'static str {
    match error {
        DomainError::Value(_) => "ValueError",
        DomainError::Type(_) => "TypeError",
    }
}

/// Compare a Rust result with Python's `[error kind, message]` or `[null, result]`.
fn check<T>(failures: &mut Vec<String>, case: &Json, actual: DomainResult<T>, result: impl Fn(T) -> Json) {
    let expected = &case["outcome"];
    let actual = match actual {
        Ok(value) => Json::Array(vec![Json::Null, result(value)]),
        Err(error) => Json::Array(vec![Json::from(kind(&error)), Json::from(error.message())]),
    };
    if &actual != expected {
        failures.push(format!("{case}: python {expected}, rust {actual}"));
    }
}

fn report(name: &str, failures: Vec<String>) {
    assert!(
        failures.is_empty(),
        "{name}: {} mismatches, first: {:#?}",
        failures.len(),
        &failures[..failures.len().min(5)]
    );
}

#[test]
fn identities_validate_like_python() {
    let vectors = vectors();
    let mut failures = Vec::new();
    let cases = list(&vectors, "identities");
    assert!(cases.len() > 600);
    for case in cases {
        let sha = case["sha256"].as_bool().unwrap();
        check(&mut failures, case, validate_authority_identity_value(&case["value"], "Label", sha), |_| {
            Json::Null
        });
    }
    report("identities", failures);
}

#[test]
fn portable_paths_validate_like_python() {
    let vectors = vectors();
    let mut failures = Vec::new();
    for case in list(&vectors, "paths") {
        check(&mut failures, case, validate_portable_path_value(&case["value"]), |_| Json::Null);
    }
    report("paths", failures);
}

fn dependency(value: &Json) -> DomainResult<PackageDependency> {
    PackageDependency::new(
        text(&value["package_id"]),
        text(&value["version"]),
        text(&value["sha256"]),
        text(&value["kind"]),
        text(&value["namespace"]),
    )
}

/// Build a lock the way the generator does: per entry, the role, then each dependency, then
/// the entry; then the lock.
fn lock(entries: &Json) -> DomainResult<PackageLock> {
    let mut built = Vec::new();
    for entry in entries.as_array().unwrap() {
        let role = PackageRole::parse(entry["role"].as_str().unwrap()).expect("vector roles are valid");
        let dependencies = entry["dependencies"]
            .as_array()
            .unwrap()
            .iter()
            .map(dependency)
            .collect::<DomainResult<Vec<_>>>()?;
        built.push(PackageLockEntry::new(
            text(&entry["package_id"]),
            text(&entry["version"]),
            text(&entry["sha256"]),
            role,
            entry["load_order"].as_i64().unwrap(),
            dependencies,
            entry["capabilities"].as_array().unwrap().iter().map(text).collect(),
        )?);
    }
    PackageLock::new(built)
}

#[test]
fn shipped_closures_hash_like_python_and_the_recording() {
    let vectors = vectors();
    for closure in list(&vectors, "closures") {
        let built = lock(&closure["entries"]).expect("shipped closures are valid locks");
        let actual = closure_sha256(&built);
        assert_eq!(actual, text(&closure["closure_sha256"]), "{}", closure["candidate_id"]);
        assert_eq!(actual, text(&closure["recorded_closure_sha256"]), "{}", closure["candidate_id"]);
    }
}

#[test]
fn shipped_manifest_records_hash_like_python_and_the_recording() {
    let path =
        concat!(env!("CARGO_MANIFEST_DIR"), "/../sylvia-compare/recordings/windows/startup.recording.json");
    let recording = pyjson::loads(&std::fs::read_to_string(path).expect("startup recording")).unwrap();
    let rows = recording["database"]["tables"]["package_manifests"]["rows"].as_array().unwrap();
    let vectors = vectors();
    let records = list(&vectors, "records");
    assert_eq!(records.len(), rows.len());
    for expected in records {
        let row = rows.iter().find(|row| row["package_id"] == expected["package_id"]).unwrap();
        let record = row["canonical_json"]["$json"].as_object().unwrap();
        let root = content_root_for_record(record);
        assert_eq!(root, text(&expected["content_root"]), "{}", expected["package_id"]);
        assert_eq!(root, text(&expected["recorded_content_sha256"]), "{}", expected["package_id"]);
        assert_eq!(
            sha256_hex(canonical_json_bytes(&row["canonical_json"]["$json"])),
            text(&expected["canonical_sha256"]),
            "{}",
            expected["package_id"]
        );
    }
}

#[test]
fn lock_checks_and_closures_match_python() {
    let vectors = vectors();
    let mut failures = Vec::new();
    let cases = list(&vectors, "locks");
    assert!(cases.len() > 50);
    for case in cases {
        check(&mut failures, case, lock(&case["entries"]).map(|built| closure_sha256(&built)), Json::from);
    }
    report("locks", failures);
}

fn none<T>(_: T) -> Json {
    Json::Null
}

fn optional(value: &Json) -> Option<String> {
    value.as_str().map(str::to_owned)
}

#[test]
fn constructors_validate_like_python() {
    let vectors = vectors();
    let mut failures = Vec::new();
    for case in list(&vectors, "constructors") {
        let args = case["args"].as_array().unwrap();
        let s = |index: usize| text(&args[index]);
        match case["kind"].as_str().unwrap() {
            "package_file" => {
                check(&mut failures, case, PackageFile::new(s(0), args[1].as_i64().unwrap(), s(2)), none)
            }
            "exact_dependency" => {
                check(&mut failures, case, ExactPackageDependency::new(s(0), s(1), s(2), s(3), s(4)), none)
            }
            "package_dependency" => {
                check(&mut failures, case, PackageDependency::new(s(0), s(1), s(2), s(3), s(4)), none)
            }
            "activation_command" => {
                let command = ActivationCommand::new(s(0), s(1), s(2), args[3].as_i64().unwrap(), s(4), s(5));
                check(
                    &mut failures,
                    case,
                    command.map(|command| activation_payload_sha256(&command)),
                    Json::from,
                )
            }
            "participant" => {
                let fields = ParticipantFields {
                    participant_id: s(0),
                    kind: s(1),
                    package_id: s(2),
                    package_version: s(3),
                    data: args[4].as_object().unwrap().clone(),
                    package_sha256: optional(&args[5]),
                    schema_id: optional(&args[6]),
                    schema_sha256: optional(&args[7]),
                    validation_status: s(8),
                    adaptation_id: optional(&args[9]),
                };
                check(&mut failures, case, Participant::new(fields), none)
            }
            "state_value" => check(&mut failures, case, StateValue::new(s(0), s(1), Json::from(1)), none),
            "package_candidate" => {
                let entries = if args[0].as_array().unwrap().is_empty() {
                    Vec::new()
                } else {
                    let sha = "a".repeat(64);
                    vec![PackageLockEntry::new("p", "1", sha, PackageRole::Primary, 0, vec![], vec![])
                        .unwrap()]
                };
                let candidate = PackageCandidate::new(
                    "c",
                    "i",
                    CandidateStatus::Validated,
                    "a".repeat(64),
                    PackageLock::new(entries).unwrap(),
                    JsonMap::new(),
                );
                check(&mut failures, case, candidate, none)
            }
            other => panic!("unknown constructor vector {other}"),
        }
    }
    report("constructors", failures);
}

#[test]
fn participant_schema_hashes_match_python() {
    let vectors = vectors();
    let mut failures = Vec::new();
    for case in list(&vectors, "participant_schema_sha256") {
        let args = case["args"].as_array().unwrap();
        let actual = participant_schema_sha256(
            args[0].as_object().unwrap(),
            args[1].as_object(),
            args[2].as_array().unwrap(),
            args[3].as_object(),
        );
        check(&mut failures, case, actual, Json::from);
    }
    report("participant_schema_sha256", failures);
}

fn limits(value: &Json) -> JsonShapeLimits {
    let field = |name: &str| value.get(name).and_then(Json::as_u64).map(|n| n as usize);
    JsonShapeLimits {
        maximum_depth: field("maximum_depth"),
        maximum_nodes: field("maximum_nodes"),
        maximum_object_members: field("maximum_object_members"),
    }
}

#[test]
fn strict_json_reads_like_python() {
    let vectors = vectors();
    let mut failures = Vec::new();
    let mut declared = 0;
    let cases = list(&vectors, "strict_json");
    assert!(cases.len() > 100);
    for case in cases {
        let raw = hex::decode(case["hex"].as_str().unwrap()).unwrap();
        let limits = limits(&case["limits"]);
        type Parser = fn(&[u8], &str, JsonShapeLimits) -> DomainResult<Json>;
        let parsers: [(&str, Parser); 2] =
            [("content", parse_content_json_bytes), ("canonical", parse_canonical_json_bytes)];
        for (name, parse) in parsers {
            let expected = &case[name];
            let actual = match parse(&raw, "Label", limits) {
                Ok(value) => {
                    Json::Array(vec![Json::from("ok"), Json::from(pyjson::dumps(&value, Dump::DEFAULT))])
                }
                Err(error) => Json::Array(vec![Json::from(kind(&error)), Json::from(error.message())]),
            };
            if let Some(row) = case.get("difference") {
                // docs/rust-port/differences.md: Python accepts, Rust refuses as not strict JSON.
                declared += 1;
                let refused = if name == "content" {
                    "Label is not strict JSON"
                } else {
                    "Label is not strict canonical JSON"
                };
                if actual != Json::Array(vec![Json::from("ValueError"), Json::from(refused)]) {
                    failures.push(format!("difference {row} not refused: {} -> {actual}", case["hex"]));
                }
            } else if &actual != expected {
                failures.push(format!("{name} {}: python {expected}, rust {actual}", case["hex"]));
            }
        }
    }
    assert_eq!(declared, 8, "the four declared-difference inputs, through both readers");
    report("strict_json", failures);
}

#[test]
fn reserved_channels_and_payload_safety_match_python() {
    let vectors = vectors();
    for case in list(&vectors, "channels") {
        let name = case["name"].as_str().unwrap();
        assert_eq!(is_reserved_canonical_channel(name), case["reserved"].as_bool().unwrap(), "{name:?}");
    }
    for case in list(&vectors, "payload_safety") {
        assert_eq!(canonical_payload_is_safe(&case["payload"]), case["safe"].as_bool().unwrap(), "{case}");
    }
}

fn outcome(shape: &Json) -> MechanicOutcome {
    let items = |name: &str| shape.get(name).and_then(Json::as_array).cloned().unwrap_or_default();
    MechanicOutcome {
        result: shape["result"].as_object().unwrap().clone(),
        mutations: items("mutations")
            .iter()
            .map(|item| StateMutation {
                namespace: text(&item["namespace"]),
                key: text(&item["key"]),
                value: item["value"].clone(),
            })
            .collect(),
        consequences: items("consequences").iter().map(|item| item.as_object().unwrap().clone()).collect(),
        choice: shape.get("choice").map(|choice| MechanicsChoiceRequest {
            choice_id: text(&choice["choice_id"]),
            prompt: text(&choice["prompt"]),
            options: choice["options"]
                .as_array()
                .unwrap()
                .iter()
                .map(|option| MechanicsChoiceOption {
                    option_id: text(&option["option_id"]),
                    label: text(&option["label"]),
                })
                .collect(),
        }),
        participant_mutations: items("participant_mutations")
            .iter()
            .map(|item| ParticipantMutation {
                participant_id: text(&item["participant_id"]),
                data: item["data"].as_object().unwrap().clone(),
            })
            .collect(),
    }
}

#[test]
fn mechanic_outcome_safety_matches_python() {
    let vectors = vectors();
    for case in list(&vectors, "mechanic_outcomes") {
        assert_eq!(
            mechanic_outcome_is_safe(&outcome(&case["outcome"])),
            case["safe"].as_bool().unwrap(),
            "{}",
            case["outcome"]
        );
    }
}

#[test]
fn response_blocks_match_python() {
    let vectors = vectors();
    let mut failures = Vec::new();
    for case in list(&vectors, "response_blocks") {
        check(&mut failures, case, response_blocks_text(&case["blocks"]), Json::from);
        let validated = serde_json::json!({"outcome": case["validated"]});
        check(&mut failures, &validated, validate_response_blocks(&case["blocks"]), |blocks| {
            Json::Array(blocks.into_iter().map(Json::Object).collect())
        });
    }
    report("response_blocks", failures);
}

#[test]
fn python_text_behaviour_matches() {
    let vectors = vectors();
    for case in list(&vectors, "text") {
        let value = case["text"].as_str().unwrap();
        assert_eq!(pytext::strip(value), case["strip"].as_str().unwrap(), "strip {value:?}");
        assert_eq!(pytext::casefold(value), case["casefold"].as_str().unwrap(), "casefold {value:?}");
        assert_eq!(pytext::upper(value), case["upper"].as_str().unwrap(), "upper {value:?}");
        assert_eq!(pytext::len(value) as u64, case["len"].as_u64().unwrap(), "len {value:?}");
    }
}
