//! The owner's admin full audit state against Python (`tests/vectors/admin_audit_vectors.json`,
//! written by `generate_admin_audit_vectors.py`): after each step, the reply, whether audit is
//! enabled, the captured traces and the held usage must be Python's, in Python's order.

mod common;

use serde_json::json;
use sylvia_base::Json;
use sylvia_store::narration::NarrationState;

#[test]
fn admin_audit_state_matches_python() {
    let record = common::read_json("tests/vectors/admin_audit_vectors.json");
    let state = NarrationState::new();
    for (index, step) in record["steps"].as_array().unwrap().iter().enumerate() {
        let outcome = match step["op"].as_str().unwrap() {
            "set" => {
                json!({ "ok": Json::Object(state.set_admin_full_audit(step["value"].as_bool().unwrap())) })
            }
            "record" => match state.record_admin_full_audit(step["value"].clone()) {
                Ok(()) => json!({ "ok": null }),
                Err(error) => {
                    let text = error.to_string();
                    let (class, message) = text.split_once(": ").unwrap();
                    json!({"error": class, "message": message})
                }
            },
            _ => json!({ "ok": state.admin_full_audit_enabled() }),
        };
        let (enabled, traces, pending) = state.with(|inner| {
            let pending: sylvia_base::JsonMap = inner.pending_audit_usage.iter().cloned().collect();
            (
                inner.admin_full_audit_enabled,
                Json::Array(inner.inspector_trace.clone()),
                Json::Object(pending),
            )
        });
        let got = json!({"outcome": outcome, "enabled": enabled, "traces": traces, "pending": pending});
        let want = json!({"outcome": step["outcome"], "enabled": step["enabled"], "traces": step["traces"], "pending": step["pending"]});
        assert_eq!(got.to_string(), want.to_string(), "step #{index} {}", step["op"]);
    }
    assert_eq!(
        state.inspector_traces(),
        record["steps"].as_array().unwrap().last().unwrap()["traces"].as_array().unwrap().clone()
    );
}
