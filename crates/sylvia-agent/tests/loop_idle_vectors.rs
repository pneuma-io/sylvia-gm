//! Compare the session loop stub's answers with Python's loop when no run is live.
//!
//! Regenerate with `tests/vectors/generate_loop_idle_vectors.py`: Python's `CampaignSessionLoop`
//! over a recording owner, with no run started. Every `builder_activity` branch (each answered
//! from a given saved activity basis), the empty live reads, `assert_idle`, and the methods
//! that need a live run. Rust is answered the owner calls Python made, in order; its result or
//! refusal must be Python's. The run entry points answer `rust_port_pending` until T5.

mod common;

use common::Recorded;
use serde_json::Value as J;
use std::sync::Arc;
use sylvia_agent::session_loop::campaign::{CampaignSessionLoop, LoopHooks};
use sylvia_base::testing::assert_same_json;
use sylvia_base::{CodedError, CodedResult};

/// Python's outcome: `{"result": ...}` or `{"raised": {class, message, code}}`.
fn outcome<T: serde::Serialize>(result: CodedResult<T>) -> J {
    match result {
        Ok(value) => serde_json::json!({"result": serde_json::to_value(value).unwrap()}),
        Err(error) => serde_json::json!({"raised": raised(&error)}),
    }
}

fn raised(error: &CodedError) -> J {
    let class = error.error_type.unwrap_or("CommandRejected");
    // Python's `KeyError` carries no code; an authority refusal carries its own.
    let code = if class == "KeyError" { J::Null } else { J::from(error.code.as_str()) };
    serde_json::json!({"class": class, "message": error.message, "code": code})
}

#[tokio::test]
async fn idle_loop_matches_python() {
    let vectors: J = serde_json::from_str(include_str!("vectors/loop_idle_vectors.json"))
        .expect("vector file is valid JSON");
    let cases = vectors["cases"].as_array().expect("cases");
    assert_eq!(cases.len(), 23);
    for case in cases {
        let name = case["name"].as_str().unwrap();
        let owner = Arc::new(Recorded::new(case["calls"].as_array().unwrap()));
        let session_loop = CampaignSessionLoop::new(owner.clone(), LoopHooks::default());
        let session = "session-1";
        let found = match case["method"].as_str().unwrap() {
            "builder_activity" => {
                outcome(session_loop.builder_activity(session, case["attempt_id"].as_str()).await)
            }
            "activity_progress" => outcome(session_loop.activity_progress(session).await),
            "context_status_hint" => outcome::<Option<String>>(Ok(session_loop.context_status_hint(session))),
            "provisional_session_projection" => {
                outcome(Ok(session_loop.provisional_session_projection(session)))
            }
            "assert_idle" => outcome(session_loop.assert_idle(session).map(|()| J::Null)),
            "steer" => outcome(session_loop.steer(session, "Wait", "message-9").await.map(|()| J::Null)),
            "stop" => outcome(session_loop.stop(session).await.map(|_| J::Null)),
            "expire" => outcome(session_loop.expire(session, 10.0).await.map(|_| J::Null)),
            "disconnect" => outcome(session_loop.disconnect(session, 10.0).await),
            "reconnect" => outcome(session_loop.reconnect(session, 10.0, 3).await.map(|_| J::Null)),
            other => panic!("unknown method {other}"),
        };
        let expected = match case.get("raised") {
            Some(raised) => serde_json::json!({"raised": raised}),
            None => serde_json::json!({"result": case["result"]}),
        };
        assert_same_json(&found, &expected, name);
        owner.check(name);
    }
}
