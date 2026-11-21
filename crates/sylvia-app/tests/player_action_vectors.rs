//! Compare sylvia_app::player_actions with the frozen Python.
//!
//! Regenerate with `tests/vectors/generate_player_action_vectors.py` (needs the snapshot's Python).

use serde_json::json;
use sylvia_app::player_actions::{
    player_control_origin, valid_player_action_consequence, PLAYER_ACTION_ORIGIN,
};
use sylvia_base::pyjson;
use sylvia_base::Json;

fn vectors() -> Json {
    pyjson::loads(include_str!("vectors/player_action_vectors.json")).expect("vector file is valid JSON")
}

#[test]
fn origin_kind_matches_python() {
    assert_eq!(vectors()["PLAYER_ACTION_ORIGIN"], json!(PLAYER_ACTION_ORIGIN));
}

#[test]
fn control_origins_match_python() {
    let vectors = vectors();
    let cases = vectors["origins"].as_array().unwrap();
    assert!(cases.len() > 400);
    for case in cases {
        let selection = case["selection"].as_object();
        let session_id = case["session_id"].as_str();
        let revision = case["expected_revision"].as_i64().unwrap();
        let actual = match player_control_origin(&case["source"], revision, selection, session_id) {
            Ok(origin) => json!({"ok": origin, "keys": origin.keys().collect::<Vec<_>>()}),
            Err(error) => json!({"raise": error.python_class(), "message": error.message()}),
        };
        assert_eq!(actual, case["result"], "source {}", case["source"]);
    }
}

#[test]
fn consequences_match_python() {
    let vectors = vectors();
    let cases = vectors["consequences"].as_array().unwrap();
    assert!(cases.len() > 8000);
    let valid = cases.iter().filter(|case| case["valid"] == json!(true)).count();
    assert!(valid > 200, "the owner's own markers are recognised");
    for case in cases {
        let payload = case["payload"].as_object().unwrap();
        let expected = case["expected_revision"].as_i64().unwrap();
        assert_eq!(
            json!(valid_player_action_consequence(payload, expected)),
            case["valid"],
            "payload {} at revision {expected}",
            case["payload"]
        );
    }
}
