//! Paired runs for the turns-and-actions family (`tests/vectors/actions`, written by
//! `generate_action_runs.py`): from Python's starting database, the Rust store runs each
//! run's operations and must give Python's outcomes and a database that dumps the same.

mod common;

use flate2::read::GzDecoder;
use std::io::Read;
use std::path::Path;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{ActionReceipt, ActionStatus, MechanicOutcome, StateMutation, WriterRole};
use sylvia_store::actions::{CommitAction, IdempotencyHit};
use sylvia_store::store::Faults;
use sylvia_store::{AuthorityStore, StoreError, StoreResult};

fn gunzip(name: &str) -> Vec<u8> {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join(format!("tests/vectors/actions/{name}.sqlite3.gz"));
    let mut bytes = Vec::new();
    GzDecoder::new(std::fs::File::open(&path).unwrap()).read_to_end(&mut bytes).unwrap();
    bytes
}

fn text(op: &Json, key: &str) -> String {
    op[key].as_str().unwrap_or_else(|| panic!("{key} in {op}")).to_owned()
}

fn map(value: &Json) -> JsonMap {
    value.as_object().cloned().unwrap()
}

fn command(op: &Json) -> CommitAction {
    CommitAction {
        campaign_id: text(op, "campaign_id"),
        action_id: text(op, "action_id"),
        idempotency_key: text(op, "idempotency_key"),
        payload_hash: text(op, "payload_hash"),
        expected_revision: op["expected_revision"].as_i64().unwrap(),
        writer: WriterRole::parse(&text(op, "writer")).unwrap(),
        turn_id: text(op, "turn_id"),
        package_id: text(op, "package_id"),
        package_version: text(op, "package_version"),
        package_sha256: text(op, "package_sha256"),
        tool_name: text(op, "tool_name"),
        parameters: map(&op["parameters"]),
        outcome: MechanicOutcome {
            result: map(&op["result"]),
            mutations: op["mutations"]
                .as_array()
                .unwrap()
                .iter()
                .map(|item| StateMutation {
                    namespace: item[0].as_str().unwrap().to_owned(),
                    key: item[1].as_str().unwrap().to_owned(),
                    value: item[2].clone(),
                })
                .collect(),
            consequences: op["consequences"].as_array().unwrap().iter().map(map).collect(),
            choice: None,
            participant_mutations: Vec::new(),
        },
        status: ActionStatus::parse(&text(op, "status")).unwrap(),
        choice_resolution: None,
        campaign_file_commands: Vec::new(),
    }
}

fn receipt(value: &ActionReceipt) -> Json {
    serde_json::json!({
        "campaign_id": value.campaign_id,
        "action_id": value.action_id,
        "status": value.status.as_str(),
        "result": Json::Object(value.result.clone()),
        "resulting_revision": value.resulting_revision,
    })
}

fn hit(value: Option<IdempotencyHit>) -> Json {
    value.map_or(
        Json::Null,
        |hit| serde_json::json!({"payload_hash": hit.payload_hash, "receipt": receipt(&hit.receipt)}),
    )
}

fn run(store: &mut AuthorityStore, op: &Json) -> StoreResult<Json> {
    let campaign = text(op, "campaign_id");
    match op["op"].as_str().unwrap() {
        "commit_action" => {
            if op.get("fault").is_some() {
                store.set_faults(Faults {
                    before_commit: Some(Box::new(|| Err(StoreError::Other("fault before commit".into())))),
                    ..Faults::default()
                });
            }
            let outcome = store.commit_action(&command(op)).map(|value| receipt(&value));
            store.set_faults(Faults::default());
            outcome
        }
        "action_receipt" => {
            store.action_receipt(&campaign, &text(op, "action_id")).map(|value| receipt(&value))
        }
        "idempotency_hit" => store.idempotency_hit(&campaign, &text(op, "key")).map(hit),
        "action_hit" => store.action_hit(&campaign, &text(op, "action_id")).map(hit),
        "open_turn" => store.open_turn(&campaign, &text(op, "turn_id")).map(|()| Json::Null),
        "complete_turn" => store.complete_turn(&campaign, &text(op, "turn_id")).map(|()| Json::Null),
        "current_open_turn" => {
            store.current_open_turn(&campaign).map(|turn| turn.map_or(Json::Null, Json::String))
        }
        "turn_is_completed" => store.turn_is_completed(&campaign, &text(op, "turn_id")).map(Json::Bool),
        other => panic!("unknown operation {other}"),
    }
}

fn check(label: &str, got: StoreResult<Json>, want: &Json) {
    match (got, want.get("error")) {
        (Ok(value), None) => assert_eq!(value, want["ok"], "{label}"),
        (Err(StoreError::Coded(error)), Some(kind)) => {
            assert_eq!(error.error_type, kind.as_str(), "{label}: type");
            assert_eq!(Json::String(error.code.clone()), want["code"], "{label}: code");
            assert_eq!(error.message, want["message"].as_str().unwrap(), "{label}: message");
        }
        // The generator's own crash stand-in.
        (Err(StoreError::Other(message)), Some(kind)) if kind == "_Fault" => {
            assert_eq!(message, want["message"].as_str().unwrap(), "{label}");
        }
        (got, _) => panic!("{label}: Rust {got:?}, Python {want}"),
    }
}

#[test]
fn every_action_run_matches_python() {
    let record = common::read_json("tests/vectors/actions/action_runs.json");
    let runs = record["runs"].as_object().unwrap();
    assert!(runs.len() >= 8);
    let rules = sylvia_compare::diff::Rules::defaults();
    for (name, run_record) in runs {
        let folder = common::work_folder(&format!("actions-{name}"));
        let database = folder.join("authority.sqlite3");
        std::fs::write(&database, gunzip("seed")).unwrap();
        let mut store = AuthorityStore::open_for_application_owner(&database, None).unwrap();
        let operations = run_record["operations"].as_array().unwrap();
        let outcomes = run_record["outcomes"].as_array().unwrap();
        for (index, (op, want)) in operations.iter().zip(outcomes).enumerate() {
            check(&format!("{name} #{index} {}", op["op"]), run(&mut store, op), want);
        }
        store.close();

        let python = folder.join("python.sqlite3");
        std::fs::write(&python, gunzip(name)).unwrap();
        let reference =
            sylvia_compare::diff::normalise(&sylvia_compare::dbdump::dump(&python).unwrap(), &rules).unwrap();
        let candidate =
            sylvia_compare::diff::normalise(&sylvia_compare::dbdump::dump(&database).unwrap(), &rules)
                .unwrap();
        let differences = sylvia_compare::diff::compare(&reference, &candidate, 20);
        assert!(differences.is_empty(), "{name}: database differs from Python's: {differences:#?}");
    }
}
