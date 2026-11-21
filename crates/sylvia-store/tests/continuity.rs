//! Paired runs for campaign continuity, part 1 (`tests/vectors/continuity`, written by
//! `generate_continuity_runs.py`): from Python's starting database, the Rust store runs each
//! run's operations and must give Python's outcomes and a database that dumps the same.

mod common;

use flate2::read::GzDecoder;
use serde_json::json;
use std::io::Read;
use std::path::Path;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{
    CampaignContinuityCommand, CampaignContinuityReceipt, CampaignContinuityView,
};
use sylvia_store::store::Faults;
use sylvia_store::{AuthorityStore, StoreError, StoreResult};

fn gunzip(name: &str) -> Vec<u8> {
    let path =
        Path::new(env!("CARGO_MANIFEST_DIR")).join(format!("tests/vectors/continuity/{name}.sqlite3.gz"));
    let mut bytes = Vec::new();
    GzDecoder::new(std::fs::File::open(&path).unwrap()).read_to_end(&mut bytes).unwrap();
    bytes
}

fn text(op: &Json, key: &str) -> String {
    op[key].as_str().unwrap_or_else(|| panic!("{key} in {op}")).to_owned()
}

fn command(item: &Json) -> CampaignContinuityCommand {
    CampaignContinuityCommand {
        campaign_id: text(item, "campaign_id"),
        operation_id: text(item, "operation_id"),
        session_id: text(item, "session_id"),
        expected_revision: item["expected_revision"].as_i64().unwrap(),
        kind: text(item, "kind"),
        record_id: text(item, "record_id"),
        status: text(item, "status"),
        payload: item["payload"].as_object().cloned().unwrap(),
        basis_message_ids: item["basis_message_ids"]
            .as_array()
            .unwrap()
            .iter()
            .map(|id| id.as_str().unwrap().to_owned())
            .collect(),
        turn_id: text(item, "turn_id"),
    }
}

fn receipt_json(receipt: &CampaignContinuityReceipt) -> Json {
    json!({
        "campaign_id": receipt.campaign_id, "operation_id": receipt.operation_id,
        "record_id": receipt.record_id, "kind": receipt.kind,
        "resulting_revision": receipt.resulting_revision,
    })
}

fn map(value: &JsonMap) -> Json {
    Json::Object(value.clone())
}

fn view_json(view: &CampaignContinuityView) -> Json {
    json!({
        "current_scene": view.current_scene.as_ref().map(map),
        "facts": view.facts.iter().map(map).collect::<Vec<_>>(),
        "reveals": map(&view.reveals),
        "plan_progress": view.plan_progress.iter().map(map).collect::<Vec<_>>(),
        "records": view.records.iter().map(|r| json!({
            "entry_id": r.entry_id, "record_id": r.record_id, "kind": r.kind, "status": r.status,
            "payload": map(&r.payload), "basis_message_ids": r.basis_message_ids,
            "admitted_revision": r.admitted_revision, "operation_id": r.operation_id,
        })).collect::<Vec<_>>(),
    })
}

fn run(store: &mut AuthorityStore, op: &Json) -> StoreResult<Json> {
    match op["op"].as_str().unwrap() {
        "campaign_continuity_view" => {
            store.campaign_continuity_view(&text(op, "campaign_id")).map(|view| view_json(&view))
        }
        "commit_campaign_continuity_batch" => {
            let commands: Vec<_> = op["commands"].as_array().unwrap().iter().map(command).collect();
            if op["fault"].as_bool().unwrap() {
                store.set_faults(Faults {
                    before_commit: Some(Box::new(|| Err(StoreError::Other("fault before commit".into())))),
                    ..Faults::default()
                });
            }
            let result = store.commit_campaign_continuity_batch(&commands);
            store.set_faults(Faults::default());
            result.map(|receipts| Json::Array(receipts.iter().map(receipt_json).collect()))
        }
        other => panic!("unknown operation {other}"),
    }
}

fn check(label: &str, got: StoreResult<Json>, want: &Json) {
    match (got, want.get("error")) {
        (Ok(value), None) => assert_eq!(value, want["ok"], "{label}"),
        (Err(StoreError::Other(message)), Some(kind)) if kind == "_Fault" => {
            assert_eq!(message, want["message"].as_str().unwrap(), "{label}: fault");
        }
        (Err(error), Some(kind)) => {
            let Some(coded) = error.rejection() else {
                panic!("{label}: Rust fault {error:?}, Python {want}")
            };
            assert_eq!(coded.error_type, kind.as_str(), "{label}: type");
            // Python's ValueError carries no code; its owner replies `command_rejected`.
            let code = want["code"].as_str().unwrap_or("command_rejected");
            assert_eq!(coded.code, code, "{label}: code");
            assert_eq!(coded.message, want["message"].as_str().unwrap(), "{label}: message");
        }
        (got, _) => panic!("{label}: Rust {got:?}, Python {want}"),
    }
}

#[test]
fn every_continuity_run_matches_python() {
    let record = common::read_json("tests/vectors/continuity/continuity_runs.json");
    let rules = sylvia_compare::diff::Rules::defaults();
    for (name, run_record) in record["runs"].as_object().unwrap() {
        let folder = common::work_folder(&format!("continuity-{name}"));
        let database = folder.join("authority.sqlite3");
        std::fs::write(&database, gunzip("seed")).unwrap();
        let mut store = AuthorityStore::open_for_application_owner(&database, None).unwrap();
        let operations = run_record["operations"].as_array().unwrap();
        for (index, (op, want)) in
            operations.iter().zip(run_record["outcomes"].as_array().unwrap()).enumerate()
        {
            let outcome = run(&mut store, op);
            check(&format!("{name} #{index} {}", op["op"]), outcome, want);
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
