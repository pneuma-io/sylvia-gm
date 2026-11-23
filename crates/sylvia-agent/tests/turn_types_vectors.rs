//! Compare the GM turn's types and decisions (T1) with Python.
//!
//! Regenerate with `tests/vectors/generate_turn_types_vectors.py`. Every expected value was
//! produced by the Python functions: the turn decisions over seeded random states (each local
//! assigned or not, so `UnboundLocalError` is compared too), `_activity`, `ProviderControl`,
//! the turn's control messages, `_preparation_keeps_intent_pending`,
//! `_mechanics_rejection_feedback` and `ProviderEvent.private_continuation`.

use indexmap::IndexMap;
use serde_json::Value as J;
use std::sync::{Arc, Mutex};
use std::time::Duration;
use sylvia_agent::activity::{operation_category, AttemptProgress, Clock};
use sylvia_agent::provider_session::control_texts as texts;
use sylvia_agent::provider_turn::decisions as d;
use sylvia_agent::provider_turn::state::{Local, ProviderTurnState};
use sylvia_agent::session_loop::{
    mechanics_rejection_feedback, ProviderControl, ProviderEvent, SessionProviderInput,
};
use sylvia_agent::stateless_mcp::ModelMechanicsTool;
use sylvia_base::{CodedError, JsonMap};
use sylvia_domain::authority::{
    ModelDecisionActor, ModelDecisionBoundary, ModelDecisionOwner, ModelToolBehavior, WriterRole,
};
use sylvia_domain::error::{MechanicsDetails, MechanicsValidationIssue};
use sylvia_domain::session::{SessionContext, SessionRecord, SessionRecordFields};

fn vectors() -> J {
    serde_json::from_str(include_str!("vectors/turn_types_vectors.json")).expect("vector file is valid JSON")
}

fn text(value: &J) -> String {
    serde_json::to_string(value).unwrap()
}

fn map(value: &J) -> JsonMap {
    value.as_object().cloned().unwrap_or_default()
}

fn opt(value: &J) -> Option<String> {
    value.as_str().map(str::to_owned)
}

fn tool(name: &str, behavior: &str, category: &str, description: &str) -> ModelMechanicsTool {
    let mut schema = JsonMap::new();
    schema.insert("type".into(), "object".into());
    let mut tool = ModelMechanicsTool::new("sylvia.test", "1.0.0", "0".repeat(64), name, "test", schema);
    tool.model_behavior = ModelToolBehavior::parse(behavior).unwrap();
    tool.category = category.into();
    tool.description = description.into();
    tool
}

fn boundary(value: &J) -> ModelDecisionBoundary {
    ModelDecisionBoundary {
        owner: ModelDecisionOwner::parse(value["owner"].as_str().unwrap()).unwrap(),
        actor_id: opt(&value["actor_id"]),
        actor_name: opt(&value["actor_name"]),
        actor_state: map(&value["actor_state"]),
        campaign_revision: value["campaign_revision"].as_i64().unwrap(),
        active_side_id: opt(&value["active_side_id"]),
        focused_actor_id: opt(&value["focused_actor_id"]),
        eligible_actors: value["eligible_actors"]
            .as_array()
            .unwrap()
            .iter()
            .map(|actor| ModelDecisionActor {
                actor_id: actor["actor_id"].as_str().unwrap().into(),
                actor_name: actor["actor_name"].as_str().unwrap().into(),
                actor_state: map(&actor["actor_state"]),
            })
            .collect(),
        preparation: value["preparation"].as_object().cloned(),
    }
}

fn blocks(value: &J) -> Option<Vec<JsonMap>> {
    value.as_array().map(|items| items.iter().map(map).collect())
}

/// Assign one recorded local, as `build_state` does in the generator.
fn assign(state: &mut ProviderTurnState, name: &str, value: &J, sample_boundary: &J) {
    let flag = || value.as_bool().unwrap();
    let (o, p, k, l) = (&mut state.obligations, &mut state.progress, &mut state.decisions, &mut state.locals);
    match name {
        "obligations.buffered_narration_needs_disposition" => {
            o.buffered_narration_needs_disposition.set(flag())
        }
        "obligations.campaign_file_narration_pending" => o.campaign_file_narration_pending.set(flag()),
        "obligations.force_campaign_file_update" => o.force_campaign_file_update.set(flag()),
        "obligations.pending_failed_tool_call" => o.pending_failed_tool_call.set(flag()),
        "obligations.player_intent_pending" => o.player_intent_pending.set(flag()),
        "obligations.player_intent_resolution_required" => o.player_intent_resolution_required.set(flag()),
        "obligations.engine_transition_review_pending" => o.engine_transition_review_pending.set(flag()),
        "obligations.provisional_tool_prose_pending" => o.provisional_tool_prose_pending.set(flag()),
        "progress.campaign_file_disposition_in_exchange" => {
            p.campaign_file_disposition_in_exchange.set(flag())
        }
        "progress.any_commit_in_exchange" => p.any_commit_in_exchange.set(flag()),
        "progress.campaign_file_commit_in_exchange" => p.campaign_file_commit_in_exchange.set(flag()),
        "progress.state_changing_call_submitted" => p.state_changing_call_submitted.set(flag()),
        "progress.round_failed" => p.round_failed.set(flag()),
        "decisions.gm_boundary_decision" => k.gm_boundary_decision.set(flag()),
        "decisions.player_boundary_narration" => k.player_boundary_narration.set(flag()),
        "buffered_narration" => l.buffered_narration.set(opt(value)),
        "required_transitions" => l.required_transitions.set(
            (0..value.as_i64().unwrap())
                .map(|index| {
                    tool(&format!("transition_{index}"), "required_engine_transition", "mechanic", "")
                })
                .collect(),
        ),
        "participant_ooc_only" => l.participant_ooc_only.set(flag()),
        "round_has_non_narrative_call" => l.round_has_non_narrative_call.set(flag()),
        "narration" => l.narration.set(value.as_str().unwrap().into()),
        "provider_tool_call_required" => l.provider_tool_call_required.set(flag()),
        "current_player_action" => l.current_player_action.set(opt(value)),
        "previous_player_action" => l.previous_player_action.set(opt(value)),
        "previous_player_facing_response" => l.previous_player_facing_response.set(opt(value)),
        "campaign_file_disposition_supported" => l.campaign_file_disposition_supported.set(flag()),
        "opening_request" => l.opening_request.set(flag()),
        "decision_boundary" => l.decision_boundary.set((!value.is_null()).then(|| boundary(sample_boundary))),
        "post_commit_tool_required" => l.post_commit_tool_required.set(flag()),
        "ooc_ack_required" => l.ooc_ack_required.set(flag()),
        "buffered_response_blocks" => l.buffered_response_blocks.set(blocks(value)),
        "buffered_raw_response_blocks" => l.buffered_raw_response_blocks.set(blocks(value)),
        "catalog_tool" => l.catalog_tool.set(tool("lookup", "optional", value.as_str().unwrap(), "")),
        "behavior" => l.behavior.set(ModelToolBehavior::parse(value.as_str().unwrap()).unwrap()),
        other => panic!("unknown local {other}"),
    }
}

fn provider_input(role: &str) -> SessionProviderInput {
    let role = WriterRole::parse(role).unwrap();
    // The predicate reads only the role; a Builder session here is a standalone conversation.
    let session = SessionRecord::new(SessionRecordFields {
        session_id: "session-1".into(),
        campaign_id: (role == WriterRole::Dm).then(|| "campaign-1".into()),
        role,
        provider_id: "provider".into(),
        model_id: "model".into(),
        ..SessionRecordFields::default()
    })
    .expect("a valid session");
    let history = SessionContext { session: session.clone(), messages: Vec::new() };
    SessionProviderInput::new(session, history, Vec::new(), Arc::new(ProviderControl::new()))
}

fn outcome(result: Local<bool>) -> J {
    match result {
        Ok(value) => J::Bool(value),
        Err(error) => serde_json::json!({"unbound": error.message()}),
    }
}

#[test]
fn decisions_match_python() {
    let vectors = vectors();
    let sample_boundary = &vectors["sample_boundary"];
    let inputs = [("dm", provider_input("dm")), ("builder", provider_input("builder"))];
    for (index, case) in vectors["decisions"].as_array().unwrap().iter().enumerate() {
        let mut state = ProviderTurnState::default();
        for (name, value) in case["assigned"].as_object().unwrap() {
            assign(&mut state, name, value, sample_boundary);
        }
        let role = case["role"].as_str().unwrap();
        let input = &inputs.iter().find(|(name, _)| *name == role).unwrap().1;
        let atomic = case["atomic"].as_bool().unwrap();
        let position = case["position"].as_i64().unwrap();
        let s = &state;
        let results: Vec<(&str, Local<bool>)> = vec![
            ("buffered_narration_is_settled", d::buffered_narration_is_settled(s)),
            ("required_transition_can_run", d::required_transition_can_run(s)),
            ("actor_decision_can_be_requested", d::actor_decision_can_be_requested(s)),
            ("tool_round_needs_correction", d::tool_round_needs_correction(s)),
            ("tool_round_can_release_narration", d::tool_round_can_release_narration(s)),
            ("held_campaign_file_narration_is_needed", d::held_campaign_file_narration_is_needed(s)),
            ("narration_repeats_previous_player_response", d::narration_repeats_previous_player_response(s)),
            ("player_boundary_prose_can_end_turn", d::player_boundary_prose_can_end_turn(s)),
            ("required_tool_call_is_missing", d::required_tool_call_is_missing(s)),
            ("narration_needs_private_correction", d::narration_needs_private_correction(s)),
            ("campaign_file_needs_narration", d::campaign_file_needs_narration(s)),
            ("campaign_file_must_be_settled", d::campaign_file_must_be_settled(s)),
            ("required_tool_correction_was_abandoned", d::required_tool_correction_was_abandoned(s)),
            ("round_needs_gm_decision", d::round_needs_gm_decision(s)),
            ("round_needs_player_intent_resolution", d::round_needs_player_intent_resolution(s)),
            ("opening_needs_campaign_file_update", d::opening_needs_campaign_file_update(s)),
            (
                "committed_work_needs_campaign_file_disposition",
                d::committed_work_needs_campaign_file_disposition(s),
            ),
            ("saved_gm_boundary_should_be_read", d::saved_gm_boundary_should_be_read(s)),
            ("engine_transition_needs_review", d::engine_transition_needs_review(s)),
            (
                "campaign_file_requires_atomic_completion",
                d::campaign_file_requires_atomic_completion(s, input),
            ),
            ("round_requires_tool_call", d::round_requires_tool_call(s, atomic)),
            (
                "settled_disposition_still_owes_ooc_acknowledgement",
                d::settled_disposition_still_owes_ooc_acknowledgement(s),
            ),
            ("held_disposition_must_be_retried", d::held_disposition_must_be_retried(s)),
            ("tool_call_needs_fresh_catalog", d::tool_call_needs_fresh_catalog(s, position)),
            ("exchange_has_committed_work", d::exchange_has_committed_work(s)),
            ("round_allows_history_tool", d::round_allows_history_tool(s)),
            ("player_intent_remains_pending", d::player_intent_remains_pending(s)),
            ("buffered_narration_can_be_replaced", d::buffered_narration_can_be_replaced(s)),
            ("committed_tool_resolves_player_boundary", d::committed_tool_resolves_player_boundary(s)),
            ("round_is_at_player_boundary", d::round_is_at_player_boundary(s)),
            ("provisional_tool_prose_needs_review", d::provisional_tool_prose_needs_review(s)),
        ];
        let expected = case["results"].as_object().unwrap();
        assert_eq!(results.len(), expected.len(), "every Python predicate is checked");
        for (name, result) in results {
            assert_eq!(outcome(result), expected[name], "case {index}, {name}, state {}", case["assigned"]);
        }
    }
}

#[test]
fn an_unbound_local_reaches_the_owner_as_python_s_error() {
    let error: CodedError = d::round_needs_gm_decision(&ProviderTurnState::default()).unwrap_err().into();
    assert_eq!(error.code, "internal_error");
    assert_eq!(error.error_type, Some("UnboundLocalError"));
    assert_eq!(
        error.message,
        "cannot access local variable 'gm_boundary_decision' where it is not associated with a value"
    );
}

#[test]
fn operation_categories_match_python() {
    for case in vectors()["operation_category"].as_array().unwrap() {
        assert_eq!(operation_category(case["name"].as_str()), case["category"], "{}", case["name"]);
    }
}

/// A clock the script moves by hand, as the generator's `FakeClock`.
fn fake_clock() -> (Arc<Mutex<f64>>, Clock) {
    let now = Arc::new(Mutex::new(100.0));
    let reader = Arc::clone(&now);
    (now, Arc::new(move || *reader.lock().unwrap()))
}

fn error_text(error: &CodedError) -> J {
    serde_json::json!({"error": error.message})
}

#[test]
fn attempt_progress_matches_python() {
    for case in vectors()["attempt_progress"].as_array().unwrap() {
        let (now, clock) = fake_clock();
        let progress = AttemptProgress::new(clock);
        let mut outputs = Vec::new();
        for step in case["script"].as_array().unwrap() {
            let operation = step[0].as_str().unwrap();
            let output = match operation {
                "tick" => {
                    *now.lock().unwrap() += step[1].as_f64().unwrap();
                    J::Null
                }
                "update" => progress
                    .update(step[1].as_str().unwrap(), step[2].as_str())
                    .map_or_else(|e| error_text(&e), |()| J::Null),
                "observe" => {
                    progress.observe();
                    J::Null
                }
                "expect_correction" => {
                    progress.expect_correction();
                    J::Null
                }
                "finish" => {
                    progress.finish(step[1].as_str().unwrap()).map_or_else(|e| error_text(&e), J::Object)
                }
                _ => J::Object(progress.snapshot()),
            };
            outputs.push(output);
        }
        assert_eq!(J::Array(outputs), case["outputs"], "{}", case["script"]);
    }
}

#[tokio::test]
async fn provider_control_matches_python() {
    for case in vectors()["provider_control"].as_array().unwrap() {
        let (_now, clock) = fake_clock();
        let control = ProviderControl::with_clock(clock);
        let mut outputs = Vec::new();
        let zero = Some(Duration::ZERO);
        for step in case["script"].as_array().unwrap() {
            let operation = step[0].as_str().unwrap();
            let unit = |result: Result<(), CodedError>| result.map_or_else(|e| error_text(&e), |()| J::Null);
            let list = |items: Vec<String>| J::Array(items.into_iter().map(J::from).collect());
            let output = match operation {
                "cancelled" => J::Bool(control.cancelled()),
                "count" => J::from(control.cancellation_count()),
                "admit" => control.admit(|| "admitted").map_or_else(|e| error_text(&e), J::from),
                "cancel" => unit(control.cancel()),
                "wait_cancelled" => J::Bool(control.wait_cancelled(zero).await),
                "add_steering" => unit(control.add_steering(step[1].as_str().unwrap()).await),
                "admit_failing" => unit(
                    control
                        .admit_steering(step[1].as_str().unwrap(), || async {
                            Err(CodedError::rejected("persist failed"))
                        })
                        .await,
                ),
                "wait_for_steering" => list(control.wait_for_steering(zero).await),
                "take_steering" => list(control.take_steering().await),
                "take_steering_or_close" => list(control.take_steering_or_close().await),
                "status_hint" => unit(control.set_status_hint(step[1].as_str())),
                "hint" => control.status_hint().map_or(J::Null, J::from),
                _ => J::Object(control.progress.snapshot()),
            };
            outputs.push(output);
        }
        assert_eq!(J::Array(outputs), case["outputs"], "{}", case["script"]);
    }
}

#[test]
fn control_texts_match_python() {
    for case in vectors()["control_texts"].as_array().unwrap() {
        let kind = case["kind"].as_str().unwrap();
        let message = match kind {
            "engine_turn_control" => {
                let aliases: IndexMap<String, ModelMechanicsTool> = case["aliases"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|row| {
                        let field = |index: usize| row[index].as_str().unwrap();
                        (field(0).to_owned(), tool(field(1), field(2), "mechanic", field(3)))
                    })
                    .collect();
                texts::engine_turn_control_message(&aliases).map_or(J::Null, J::Object)
            }
            "campaign_preparation" => {
                J::Object(texts::campaign_preparation_message(&boundary(&case["boundary"])))
            }
            "gm_decision" => J::Object(texts::gm_decision_control_message(&boundary(&case["boundary"]))),
            "player_boundary" => {
                let operations: Vec<String> = case["committed_operations"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|item| item.as_str().unwrap().to_owned())
                    .collect();
                J::Object(texts::player_boundary_narration_message(
                    &boundary(&case["boundary"]),
                    case["current_player_action"].as_str(),
                    &operations,
                    case["player_intent_pending"].as_bool().unwrap(),
                ))
            }
            "campaign_file_disposition" => J::Object(texts::campaign_file_disposition_message(
                case["opening_footing"].as_object(),
                case["current_player_action"].as_str(),
            )),
            _ => J::Object(texts::campaign_file_updated_narration_message(
                case["opening_footing"].as_object(),
                case["current_player_action"].as_str(),
            )),
        };
        assert_eq!(text(&message), text(&case["message"]), "{kind}: {case}");
    }
}

#[test]
fn preparation_scopes_match_python() {
    for case in vectors()["preparation_scope"].as_array().unwrap() {
        let scope = (case["scope"] != "<unspecified>").then(|| &case["scope"]);
        let result = match texts::preparation_keeps_intent_pending(scope, case["pending"].as_bool().unwrap())
        {
            Ok(value) => J::Bool(value),
            Err(error) => {
                assert_eq!(error.error_type, Some("CommandRejected"));
                serde_json::json!({"code": error.code, "error": error.message})
            }
        };
        assert_eq!(result, case["result"], "{case}");
    }
}

fn issue(value: &J) -> MechanicsValidationIssue {
    let number = |key: &str| value[key].as_number().cloned();
    MechanicsValidationIssue {
        path: value["path"].as_str().unwrap().into(),
        kind: value["kind"].as_str().unwrap().into(),
        expected_type: opt(&value["expected_type"]),
        actual_type: opt(&value["actual_type"]),
        parent_path: opt(&value["parent_path"]),
        allowed_values: value["allowed_values"]
            .as_array()
            .map(|items| items.iter().map(|item| item.as_str().unwrap().to_owned()).collect())
            .unwrap_or_default(),
        minimum: number("minimum"),
        maximum: number("maximum"),
        actual_length: value["actual_length"].as_i64(),
    }
}

#[test]
fn rejection_feedback_matches_python() {
    let strings = |value: &J| -> Vec<String> {
        value.as_array().unwrap().iter().map(|item| item.as_str().unwrap().to_owned()).collect()
    };
    for case in vectors()["rejection_feedback"].as_array().unwrap() {
        let details = MechanicsDetails {
            field_paths: strings(&case["field_paths"]),
            validation_issues: case["issues"].as_array().unwrap().iter().map(issue).collect(),
            state_changed: case["state_changed"].as_bool().unwrap(),
            refusal_reasons: strings(&case["reasons"]),
        };
        let feedback = mechanics_rejection_feedback(
            case["code"].as_str().unwrap(),
            case["message"].as_str().unwrap(),
            &details,
        );
        assert_eq!(text(&J::Object(feedback)), text(&case["feedback"]), "{}", case["message"]);
    }
}

#[test]
fn private_continuations_match_python() {
    for case in vectors()["private_continuation"].as_array().unwrap() {
        let event = ProviderEvent::private_continuation(
            &map(&case["payload"]),
            case["response_id"].as_str().unwrap(),
        );
        match (event, case.get("error")) {
            (Ok(event), None) => assert_eq!(event.text(), case["text"].as_str()),
            (Err(error), Some(expected)) => {
                assert_eq!(error.error_type, Some("ValueError"));
                assert_eq!(&J::from(error.message), expected);
            }
            (event, expected) => panic!("{case}: got {event:?}, expected error {expected:?}"),
        }
    }
}
