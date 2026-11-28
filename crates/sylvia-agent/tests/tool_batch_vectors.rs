//! Compare the tool batch's helpers (T3a) with Python.
//!
//! Regenerate with `tests/vectors/generate_tool_batch_vectors.py`. Every expected value was read
//! back from the Python functions.

mod common;

use common::{session_record, turn_state, Recorded};
use serde_json::Value as J;
use std::sync::{Arc, Mutex};
use sylvia_agent::provider_session::context::ProviderSessionAdapter;
use sylvia_agent::provider_session::reads::{OpeningReads, TurnOwner};
use sylvia_agent::provider_turn::tool_checks::{
    load_arguments, prepare_campaign_file_revision, read_selected_arguments, record_campaign_file_rejection,
    record_missing_tool_fields, record_unchanged_campaign_file, record_updated_campaign_file,
    reject_invalid_revision, reject_invalid_tool_json, reject_unavailable_tool, restore_unchanged_narration,
    retry_rejected_tool_call, settle_campaign_file_narration, unwrap_selected_arguments,
};
use sylvia_agent::session_loop::{ProviderControl, SessionProviderInput};
use sylvia_agent::stateless_mcp::{safe_error, tool_result_text, AuthorityRefusal};
use sylvia_base::testing::assert_same_json;
use sylvia_base::CodedResult;
use sylvia_domain::error::{MechanicsDetails, MechanicsValidationIssue};
use sylvia_domain::session::SessionContext;
use sylvia_provider::config::env::Environment;
use sylvia_provider::types::{ModelInfo, ProviderResponse};
use sylvia_provider::{BoxFuture, ChunkStream, Provider, ProviderRequest, ProviderResult};

fn vectors() -> J {
    serde_json::from_str(include_str!("vectors/tool_batch_vectors.json")).expect("vector file is valid JSON")
}

fn text(value: &J) -> Option<String> {
    value.as_str().map(str::to_owned)
}

fn issue(payload: &J) -> MechanicsValidationIssue {
    MechanicsValidationIssue {
        path: payload["path"].as_str().expect("a path").to_owned(),
        kind: payload["kind"].as_str().expect("a kind").to_owned(),
        expected_type: text(&payload["expected_type"]),
        actual_type: text(&payload["actual_type"]),
        parent_path: text(&payload["parent_path"]),
        allowed_values: payload["allowed_values"]
            .as_array()
            .map(|values| values.iter().map(|value| value.as_str().expect("text").to_owned()).collect())
            .unwrap_or_default(),
        minimum: payload["minimum"].as_number().cloned(),
        maximum: payload["maximum"].as_number().cloned(),
        actual_length: payload["actual_length"].as_i64(),
    }
}

fn strings(value: &J) -> Vec<String> {
    value.as_array().expect("a list").iter().map(|item| item.as_str().expect("text").to_owned()).collect()
}

/// The `MechanicsRejected` fields of a described error.
fn mechanics_details(fields: &J) -> MechanicsDetails {
    MechanicsDetails {
        field_paths: strings(&fields["field_paths"]),
        validation_issues: fields["validation_issues"]
            .as_array()
            .expect("issues")
            .iter()
            .map(issue)
            .collect(),
        state_changed: fields["state_changed"].as_bool().expect("a flag"),
        refusal_reasons: strings(&fields["refusal_reasons"]),
    }
}

#[test]
fn safe_error_matches_python() {
    let vectors = vectors();
    let cases = vectors["safe_error"].as_array().expect("cases");
    assert_eq!(cases.len(), 15);
    for (index, case) in cases.iter().enumerate() {
        let error = &case["error"];
        let mechanics = error.get("mechanics").map(mechanics_details);
        let refusal = AuthorityRefusal {
            error_type: error["class"].as_str().expect("a class"),
            code: error["code"].as_str().expect("a code"),
            message: error["message"].as_str().expect("a message"),
            mechanics: mechanics.as_ref(),
        };
        let structured = safe_error(refusal);
        assert_eq!(
            serde_json::to_string(&structured).expect("serialisable"),
            serde_json::to_string(&case["structured"]).expect("serialisable"),
            "case {index}: {}",
            error["class"]
        );
        assert_eq!(tool_result_text(&structured), case["text"].as_str().expect("text"), "case {index}");
        assert_eq!(case["is_error"], J::Bool(true));
    }
}

/// A provider the helpers never call; with no model facts the route is a legacy one.
struct Unused;

impl Provider for Unused {
    fn model_info(&self) -> Option<&ModelInfo> {
        None
    }
    fn generate(&self, _request: ProviderRequest) -> BoxFuture<'_, ProviderResult<ProviderResponse>> {
        unreachable!()
    }
    fn generate_stream(&self, _request: ProviderRequest) -> ChunkStream<'_> {
        unreachable!()
    }
    fn close(&self) -> BoxFuture<'_, ()> {
        Box::pin(async {})
    }
}

/// `{"__srd_input_schema__": <index>}` written back as the package record's input schema.
fn expand(value: &J, record: &J) -> J {
    match value {
        J::Object(map) => match map.get("__srd_input_schema__") {
            Some(index) if map.len() == 1 => {
                record["tools"][index.as_u64().expect("an index") as usize]["input_schema"].clone()
            }
            _ => J::Object(map.iter().map(|(key, item)| (key.clone(), expand(item, record))).collect()),
        },
        J::Array(items) => J::Array(items.iter().map(|item| expand(item, record)).collect()),
        other => other.clone(),
    }
}

fn provider_input() -> SessionProviderInput {
    let session = session_record(&serde_json::json!({
        "session_id": "session-1", "campaign_id": "campaign-1", "role": "dm", "provider_id": "prov",
        "model_id": "mod", "status": "active", "parent_session_id": null, "selected_skill": null, "scope": [],
        "reasoning_effort": null, "reasoning_effort_once": null,
    }));
    let history = SessionContext { session: session.clone(), messages: Vec::new() };
    SessionProviderInput::new(session, history, Vec::new(), Arc::new(ProviderControl::new()))
}

#[tokio::test]
async fn helpers_match_python() {
    let record: J = serde_json::from_str(include_str!("../../../rulesets/srd-lite/package-record.json"))
        .expect("the package record is JSON");
    let vectors = expand(&vectors(), &record);
    let cases = vectors["helpers"].as_array().expect("cases");
    assert_eq!(cases.len(), 33);
    let input = provider_input();
    for case in cases {
        let name = case["name"].as_str().expect("a name");
        let owner = Arc::new(Recorded::new(case["calls"].as_array().expect("calls")));
        let warnings = Arc::new(Mutex::new(Vec::<String>::new()));
        let kept = Arc::clone(&warnings);
        let adapter = ProviderSessionAdapter::new(
            Arc::clone(&owner) as Arc<dyn OpeningReads>,
            Arc::new(Unused),
            "owner",
            None,
            120.0,
            None,
            None,
            None,
            &Environment::default(),
        )
        .expect("an adapter")
        .with_turn_owner(Arc::clone(&owner) as Arc<dyn TurnOwner>, Arc::new(|_, _| Ok(Vec::new())))
        .with_record_warning(Arc::new(move |warning: &str| kept.lock().unwrap().push(warning.to_owned())));
        let mut state = turn_state::load(&case["state"]);
        let outcome: CodedResult<J> = match case["helper"].as_str().expect("a helper") {
            "reject_unavailable_tool" => {
                reject_unavailable_tool(&adapter, &mut state, &input).map(|()| J::Null)
            }
            "read_selected_arguments" => {
                read_selected_arguments(&adapter, &mut state, &input).map(|()| J::Null)
            }
            "unwrap_selected_arguments" => unwrap_selected_arguments(&mut state).map(J::Bool),
            "reject_invalid_tool_json" => {
                let arguments = state.locals.assembly.get().unwrap().arguments.clone();
                let error = load_arguments(&arguments).unwrap().expect_err("the arguments are not JSON");
                reject_invalid_tool_json(&mut state, &error).map(|()| J::Null)
            }
            "record_missing_tool_fields" => {
                record_missing_tool_fields(&adapter, &mut state, &input, "turn-1").map(|()| J::Null)
            }
            "reject_invalid_revision" => reject_invalid_revision(&mut state).map(|()| J::Null),
            "record_campaign_file_rejection" => {
                let error = &case["arguments"]["error"];
                let mechanics = error.get("mechanics").map(mechanics_details);
                let refusal = AuthorityRefusal {
                    error_type: error["class"].as_str().expect("a class"),
                    code: error["code"].as_str().expect("a code"),
                    message: error["message"].as_str().expect("a message"),
                    mechanics: mechanics.as_ref(),
                };
                record_campaign_file_rejection(&mut state, refusal).map(|()| J::Null)
            }
            "restore_unchanged_narration" => restore_unchanged_narration(&mut state).map(|()| J::Null),
            "record_unchanged_campaign_file" => record_unchanged_campaign_file(&mut state).map(|()| J::Null),
            "record_updated_campaign_file" => record_updated_campaign_file(&mut state).map(|()| J::Null),
            "settle_campaign_file_narration" => settle_campaign_file_narration(&mut state).map(|()| J::Null),
            "retry_rejected_tool_call" => retry_rejected_tool_call(&mut state).map(|()| J::Null),
            "prepare_campaign_file_revision" => {
                prepare_campaign_file_revision(&adapter, &mut state).await.map(|()| J::Null)
            }
            other => panic!("unknown helper {other}"),
        };
        match (outcome, case.get("raised")) {
            (Ok(returned), None) => {
                assert_same_json(&returned, &case["returned"], format!("{name}: what it returned"))
            }
            (Err(error), Some(raised)) => {
                assert_eq!(J::from(error.message.as_str()), raised["message"], "{name}")
            }
            (outcome, raised) => panic!("{name}: got {outcome:?}, Python raised {raised:?}"),
        }
        owner.check(name);
        assert_same_json(
            &J::from(warnings.lock().unwrap().clone()),
            &case["warnings"],
            format!("{name}: warnings"),
        );
        turn_state::compare(name, &turn_state::dump(&state), &case["after"]);
    }
}

fn ordered(value: &J) -> String {
    serde_json::to_string(value).expect("serialisable")
}

#[test]
fn arguments_match_python() {
    use sylvia_agent::provider_session::arguments::{
        derive_mechanics_parameters, missing_required_arguments, normalize_state_references, DerivationError,
    };
    use sylvia_agent::provider_session::campaign_file::speaker_names_overlap;
    let vectors = vectors();
    let cases = &vectors["arguments"];
    for case in cases["normalize"].as_array().expect("normalize cases") {
        let name = case["name"].as_str().unwrap();
        let outcome = normalize_state_references(
            case["parameters"].as_object().unwrap(),
            case["schema"].as_object().unwrap(),
            case["state"].as_object().unwrap(),
        );
        let actual = match outcome {
            Ok(value) => serde_json::json!({"ok": value}),
            Err(error) => {
                serde_json::json!({"raised": {"class": error.error_type, "message": error.message}})
            }
        };
        match case["expected"].get("raised") {
            Some(raised) => {
                assert_eq!(actual["raised"]["class"], raised["class"], "{name}");
                assert_eq!(actual["raised"]["message"], raised["message"], "{name}");
            }
            None => assert_eq!(ordered(&actual), ordered(&case["expected"]), "{name}"),
        }
    }
    for case in cases["derive"].as_array().expect("derive cases") {
        let name = case["name"].as_str().unwrap();
        let participants: Vec<_> =
            case["participants"].as_array().unwrap().iter().map(common::participant).collect();
        let outcome = derive_mechanics_parameters(
            case["parameters"].as_object().unwrap(),
            case["derivations"].as_object().unwrap(),
            &participants,
        );
        match (outcome, case["expected"].get("raised")) {
            (Ok(value), None) => {
                assert_eq!(ordered(&J::Object(value)), ordered(&case["expected"]["ok"]), "{name}")
            }
            (Err(DerivationError::Rejected { code, message, field_paths }), Some(raised)) => {
                assert_eq!(J::from(code), raised["code"], "{name}");
                assert_eq!(J::from(message), raised["message"], "{name}");
                assert_eq!(
                    field_paths.map(J::from),
                    raised.get("field_paths").cloned(),
                    "{name}: the MechanicsRejected fields"
                );
                let class =
                    if raised.get("field_paths").is_some() { "MechanicsRejected" } else { "CommandRejected" };
                assert_eq!(raised["class"], class, "{name}");
            }
            (Err(DerivationError::Fault(error)), Some(raised)) => {
                assert_eq!(J::from(error.error_type), raised["class"], "{name}");
                assert_eq!(J::from(error.message), raised["message"], "{name}");
            }
            (outcome, raised) => panic!("{name}: got {outcome:?}, Python {raised:?}"),
        }
    }
    for case in cases["missing"].as_array().expect("missing cases") {
        let missing = missing_required_arguments(
            case["parameters"].as_object().unwrap(),
            case["schema"].as_object().unwrap(),
        );
        assert_eq!(J::from(missing), case["expected"], "{case}");
    }
    for case in cases["overlap"].as_array().expect("overlap cases") {
        let overlap =
            speaker_names_overlap(case["first"].as_str().unwrap(), case["second"].as_str().unwrap());
        assert_eq!(J::Bool(overlap), case["expected"], "{case}");
    }
}

/// A narration helper's outcome as the generator writes it: `{"ok": ...}` or `{"raised": ...}`.
fn helper_outcome(result: sylvia_base::CodedResult<J>) -> J {
    match result {
        Ok(value) => serde_json::json!({"ok": value}),
        Err(error) => {
            let class = error.error_type.unwrap_or("CommandRejected");
            let code = if class == "TypeError" { J::Null } else { J::from(error.code) };
            serde_json::json!({"raised": {"class": class, "message": error.message, "code": code}})
        }
    }
}

#[test]
fn narration_helpers_match_python() {
    use sylvia_agent::provider_session::narration_facts::{mechanics_narration_facts, public_action_result};
    let vectors = vectors();
    let cases = &vectors["narration"];
    let facts = cases["facts"].as_array().expect("facts cases");
    assert_eq!(facts.len(), 18);
    for case in facts {
        let name = case["name"].as_str().unwrap();
        let arguments = case["arguments"].as_array().unwrap();
        let names: Option<indexmap::IndexMap<String, String>> = arguments[6].as_object().map(|names| {
            names.iter().map(|(key, value)| (key.clone(), value.as_str().unwrap().to_owned())).collect()
        });
        let outcome = mechanics_narration_facts(
            arguments[0].as_str().unwrap(),
            &arguments[1],
            arguments[2].as_object().unwrap(),
            arguments[3].as_object().unwrap(),
            arguments[4].as_object(),
            arguments[5].as_object(),
            names.as_ref(),
        )
        .map(J::Object);
        // Byte for byte: the facts' key order is what the GM's narration reads.
        assert_eq!(ordered(&helper_outcome(outcome)), ordered(&case["expected"]), "{name}");
    }
    let public = cases["public"].as_array().expect("public cases");
    assert_eq!(public.len(), 6);
    for case in public {
        let name = case["name"].as_str().unwrap();
        let arguments = case["arguments"].as_array().unwrap();
        let outcome = public_action_result(arguments[0].as_str(), &arguments[1]);
        assert_eq!(ordered(&helper_outcome(outcome)), ordered(&case["expected"]), "{name}");
    }
}
