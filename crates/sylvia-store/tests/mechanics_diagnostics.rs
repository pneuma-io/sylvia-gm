//! The owner's safe mechanics diagnostics against Python (`tests/vectors/
//! mechanics_diagnostics_vectors.json`, written by `generate_mechanics_diagnostics_vectors.py`):
//! each diagnostic Python built is kept as Python keeps it (the last 200, whatever Full Audit's
//! state), and each `admin_full_audit_status()` must be Python's, in Python's key order.

mod common;

use serde_json::json;
use sylvia_base::testing::assert_same_json;
use sylvia_base::Json;
use sylvia_store::narration::NarrationState;

#[test]
fn mechanics_diagnostics_match_python() {
    let record = common::read_json("tests/vectors/mechanics_diagnostics_vectors.json");
    let steps = record["steps"].as_array().unwrap();
    assert_eq!(steps.len(), 217);
    let state = NarrationState::new();
    let mut statuses = 0;
    for (index, step) in steps.iter().enumerate() {
        match step["op"].as_str().unwrap() {
            "record" => state.record_mechanics_diagnostic(step["diagnostic"].as_object().unwrap().clone()),
            "set" => {
                state.set_admin_full_audit(step["value"].as_bool().unwrap());
            }
            "trace" => state
                .record_admin_full_audit(
                    json!({"kind": "provider_request", "exchange_id": step["exchange_id"]}),
                )
                .unwrap(),
            "status" => {
                statuses += 1;
                let got = Json::Object(state.admin_full_audit_status()).to_string();
                assert_eq!(got, step["status"].to_string(), "status step #{index}");
            }
            op => panic!("unknown step {op}"),
        }
    }
    assert_eq!(statuses, 6);
    let last = &steps.last().unwrap()["status"]["safe_mechanics_diagnostics"];
    assert_same_json(&Json::Array(state.mechanics_diagnostics()), last, "the kept diagnostics");
}
