//! `owner_process._json_value`: an authority projection as the strict JSON the owner replies
//! with, for the values that are already JSON (a message's content, a history page's records).
//! Dataclasses are projected field by field by their own helpers (`session_message_value`, ...).
//!
//! A few mapping kinds never cross the wire as saved:
//! - `provider_tool_results`: a `read_session_history` result's content becomes
//!   `{"status":"session_history_read"}`;
//! - `provider_tool_calls`: a `read_session_history` call's arguments become `{}`;
//! - `working_context_checkpoint`: only its marker (the continuation notes stay private);
//! - `gm_wire_journal` and `gm_native_records`: only the context ledger's markers
//!   (`journal_marker`, `native_marker`, `_stub_marker` from `agent/_context_ledger.py` lines
//!   1957–1991, ported here with lane 4's agreement until their L1 exports the ledger's own).

use sylvia_base::{CodedError, CodedResult, Json, JsonMap};
use sylvia_domain::session::SessionMessage;

/// `JOURNAL_KIND`.
const JOURNAL_KIND: &str = "gm_wire_journal";
/// `NATIVE_KIND`.
const NATIVE_KIND: &str = "gm_native_records";

/// A Python exception the owner does not catch: its internal-error reply, with the class.
fn internal(class: &'static str, message: String) -> CodedError {
    CodedError::new("internal_error", message).with_error_type(class)
}

/// `value.get(key)` on a mapping: `None` (`null`) when absent.
fn get(value: &JsonMap, key: &str) -> Json {
    value.get(key).cloned().unwrap_or(Json::Null)
}

/// `iter(value)` as Python iterates a JSON value: a list's items, a string's characters, a
/// mapping's keys; anything else is a `TypeError`.
fn iterate(value: &Json) -> CodedResult<Vec<Json>> {
    sylvia_domain::pyops::py_iter(value).map_err(|error| internal("TypeError", error.message().to_owned()))
}

/// `len(value or ())`: a falsy value counts nothing; a list, mapping or string counts its items,
/// keys or characters; a non-zero number or `True` has no length (`TypeError`).
fn length_or_empty(value: &Json) -> CodedResult<usize> {
    if !sylvia_domain::pyops::py_truthy(value) {
        return Ok(0);
    }
    match value {
        Json::Array(items) => Ok(items.len()),
        Json::Object(map) => Ok(map.len()),
        Json::String(text) => Ok(sylvia_base::pystr::len(text)),
        other => Err(internal(
            "TypeError",
            format!("object of type '{}' has no len()", sylvia_domain::pyops::py_type_name(other)),
        )),
    }
}

/// `_stub_marker(value)`: a record left out of an export keeps only its kind.
fn stub_marker(value: &JsonMap) -> Option<JsonMap> {
    if value.get("omitted_from_export") != Some(&Json::Bool(true)) {
        return None;
    }
    let mut stub = JsonMap::new();
    stub.insert("kind".into(), get(value, "kind"));
    stub.insert("omitted_from_export".into(), Json::Bool(true));
    Some(stub)
}

/// `native_marker(value)`: the only part of a native-records record that may leave the owner.
fn native_marker(value: &JsonMap) -> CodedResult<JsonMap> {
    if let Some(stub) = stub_marker(value) {
        return Ok(stub);
    }
    let mut marker = JsonMap::new();
    marker.insert("kind".into(), NATIVE_KIND.into());
    for key in ["native_record_id", "epoch", "generation", "boundary", "status"] {
        marker.insert(key.into(), get(value, key));
    }
    marker.insert("record_count".into(), length_or_empty(&get(value, "records"))?.into());
    Ok(marker)
}

/// `journal_marker(value)`: the only part of a journal record that may leave the owner.
fn journal_marker(value: &JsonMap) -> CodedResult<JsonMap> {
    if let Some(stub) = stub_marker(value) {
        return Ok(stub);
    }
    let mut marker = JsonMap::new();
    marker.insert("kind".into(), JOURNAL_KIND.into());
    for key in ["journal_record_id", "epoch", "generation", "base_count"] {
        marker.insert(key.into(), get(value, key));
    }
    marker.insert("entry_count".into(), length_or_empty(&get(value, "entries"))?.into());
    Ok(marker)
}

/// `{**mapping, key: value}`: an existing key keeps its place; a new one is appended.
fn with(mapping: &JsonMap, key: &str, value: Json) -> JsonMap {
    let mut copy = mapping.clone();
    copy.insert(key.into(), value);
    copy
}

/// `_json_value(mapping)`.
fn mapping_value(value: &JsonMap) -> CodedResult<Json> {
    let kind = value.get("kind");
    let is = |name: &str| kind == Some(&Json::String(name.into()));
    let mut value = value.clone();
    if is("provider_tool_results") {
        let mut results = Vec::new();
        for item in iterate(value.get("results").unwrap_or(&Json::Array(Vec::new())))? {
            results.push(match &item {
                Json::Object(map)
                    if map.get("name") == Some(&Json::String("read_session_history".into())) =>
                {
                    Json::Object(with(map, "content", "{\"status\":\"session_history_read\"}".into()))
                }
                _ => item,
            });
        }
        value = with(&value, "results", Json::Array(results));
    }
    if is("provider_tool_calls") {
        if let Some(Json::Object(assistant)) = value.get("assistant").cloned() {
            let mut calls = Vec::new();
            for call in iterate(assistant.get("tool_calls").unwrap_or(&Json::Array(Vec::new())))? {
                calls.push(match &call {
                    Json::Object(map) => match map.get("function") {
                        Some(Json::Object(function))
                            if function.get("name") == Some(&Json::String("read_session_history".into())) =>
                        {
                            Json::Object(with(
                                map,
                                "function",
                                Json::Object(with(function, "arguments", "{}".into())),
                            ))
                        }
                        _ => call,
                    },
                    _ => call,
                });
            }
            value =
                with(&value, "assistant", Json::Object(with(&assistant, "tool_calls", Json::Array(calls))));
        }
    }
    if is("working_context_checkpoint") {
        // Continuation notes can contain GM-private facts; only the marker crosses the wire.
        let mut marker = JsonMap::new();
        marker.insert("kind".into(), "working_context_checkpoint".into());
        marker.insert("checkpoint_id".into(), get(&value, "checkpoint_id"));
        marker.insert("covered_exchanges".into(), get(&value, "covered_exchanges"));
        marker.insert("text".into(), "Context compacted · saved conversation is kept".into());
        return Ok(Json::Object(marker));
    }
    if is(JOURNAL_KIND) {
        return project_marker(journal_marker(&value)?);
    }
    if is(NATIVE_KIND) {
        return project_marker(native_marker(&value)?);
    }
    let mut projected = JsonMap::new();
    for (key, item) in &value {
        projected.insert(key.clone(), json_value(item)?);
    }
    Ok(Json::Object(projected))
}

/// `{key: _json_value(item) for key, item in marker.items()}`.
fn project_marker(marker: JsonMap) -> CodedResult<Json> {
    let mut projected = JsonMap::new();
    for (key, item) in marker {
        let item = json_value(&item)?;
        projected.insert(key, item);
    }
    Ok(Json::Object(projected))
}

/// `_json_value(value)` for a JSON value.
pub fn json_value(value: &Json) -> CodedResult<Json> {
    Ok(match value {
        Json::Object(map) => mapping_value(map)?,
        Json::Array(items) => Json::Array(items.iter().map(json_value).collect::<CodedResult<_>>()?),
        scalar => scalar.clone(),
    })
}

/// `_json_value(SessionMessage)`: the dataclass fields in declaration order, the kind by value.
pub fn session_message_value(message: &SessionMessage) -> CodedResult<Json> {
    let mut value = JsonMap::new();
    value.insert("session_id".into(), message.session_id().into());
    value.insert("sequence".into(), message.sequence().into());
    value.insert("message_id".into(), message.message_id().into());
    value.insert("kind".into(), message.kind().as_str().into());
    value.insert("content".into(), mapping_value(message.content())?);
    value.insert("canonical_sequence".into(), message.canonical_sequence().map_or(Json::Null, Json::from));
    Ok(Json::Object(value))
}

/// A list of messages, each through [`session_message_value`].
pub fn session_messages_value(messages: &[SessionMessage]) -> CodedResult<Json> {
    Ok(Json::Array(messages.iter().map(session_message_value).collect::<CodedResult<_>>()?))
}

/// `_json_value(NarrationInterruption)`: the dataclass fields in declaration order.
pub fn interruption_value(interruption: &sylvia_domain::authority::NarrationInterruption) -> Json {
    let text = |value: &Option<String>| value.clone().map_or(Json::Null, Json::String);
    let mut value = JsonMap::new();
    value.insert("campaign_id".into(), interruption.campaign_id.clone().into());
    value.insert("interruption_id".into(), interruption.interruption_id.clone().into());
    value.insert("turn_id".into(), interruption.turn_id.clone().into());
    value.insert("last_action_id".into(), text(&interruption.last_action_id));
    value.insert("failed_attempt_id".into(), interruption.failed_attempt_id.clone().into());
    value.insert("retryable".into(), interruption.retryable.into());
    value.insert("resolving_retry_id".into(), text(&interruption.resolving_retry_id));
    value.insert("resolved_sequence".into(), interruption.resolved_sequence.map_or(Json::Null, Json::from));
    Json::Object(value)
}

/// `_json_value(CanonicalEvent)`.
pub fn canonical_event_value(event: &sylvia_domain::authority::CanonicalEvent) -> CodedResult<Json> {
    let text = |value: &Option<String>| value.clone().map_or(Json::Null, Json::String);
    let mut value = JsonMap::new();
    value.insert("sequence".into(), event.sequence.into());
    value.insert("event_type".into(), event.event_type.as_str().into());
    value.insert("turn_id".into(), event.turn_id.clone().into());
    value.insert("action_id".into(), text(&event.action_id));
    value.insert("safe_payload".into(), mapping_value(&event.safe_payload)?);
    value.insert("visibility".into(), event.visibility.as_str().into());
    value.insert("attempt_id".into(), text(&event.attempt_id));
    value.insert("attempt_ordinal".into(), event.attempt_ordinal.map_or(Json::Null, Json::from));
    Ok(Json::Object(value))
}

/// `_json_value(ActionReceipt | AwaitingChoiceReceipt)`. An awaiting receipt's `status` is a
/// property, not a field, so it is not projected.
pub fn receipt_value(receipt: &sylvia_domain::authority::MechanicsExecutionReceipt) -> CodedResult<Json> {
    use sylvia_domain::authority::MechanicsExecutionReceipt;
    let mut value = JsonMap::new();
    match receipt {
        MechanicsExecutionReceipt::Action(receipt) => {
            value.insert("campaign_id".into(), receipt.campaign_id.clone().into());
            value.insert("action_id".into(), receipt.action_id.clone().into());
            value.insert("status".into(), receipt.status.as_str().into());
            value.insert("result".into(), mapping_value(&receipt.result)?);
            value.insert("resulting_revision".into(), receipt.resulting_revision.into());
        }
        MechanicsExecutionReceipt::AwaitingChoice(receipt) => {
            let options: Vec<Json> = receipt
                .choice
                .options
                .iter()
                .map(|option| {
                    let mut item = JsonMap::new();
                    item.insert("option_id".into(), option.option_id.clone().into());
                    item.insert("label".into(), option.label.clone().into());
                    Json::Object(item)
                })
                .collect();
            let mut choice = JsonMap::new();
            choice.insert("choice_id".into(), receipt.choice.choice_id.clone().into());
            choice.insert("prompt".into(), receipt.choice.prompt.clone().into());
            choice.insert("options".into(), Json::Array(options));
            value.insert("campaign_id".into(), receipt.campaign_id.clone().into());
            value.insert("action_id".into(), receipt.action_id.clone().into());
            value.insert("basis_revision".into(), receipt.basis_revision.into());
            value.insert("turn_id".into(), receipt.turn_id.clone().into());
            value.insert("choice".into(), Json::Object(choice));
            value.insert("choice_sequence".into(), receipt.choice_sequence.into());
        }
    }
    Ok(Json::Object(value))
}

/// `_json_value(SessionRunResult)`: `{canonical, receipts, interruption}`.
pub fn session_run_value(result: &sylvia_agent::session_loop::SessionRunResult) -> CodedResult<Json> {
    let mut value = JsonMap::new();
    value.insert(
        "canonical".into(),
        Json::Array(result.canonical.iter().map(canonical_event_value).collect::<CodedResult<_>>()?),
    );
    value.insert(
        "receipts".into(),
        Json::Array(result.receipts.iter().map(receipt_value).collect::<CodedResult<_>>()?),
    );
    value.insert("interruption".into(), result.interruption.as_ref().map_or(Json::Null, interruption_value));
    Ok(Json::Object(value))
}

fn optional_text(value: &Option<String>) -> Json {
    value.clone().map_or(Json::Null, Json::String)
}

/// `_json_value(Participant)`: the dataclass fields in declaration order.
fn participant_value(participant: &sylvia_domain::authority::Participant) -> CodedResult<Json> {
    let fields = participant.fields();
    let mut value = JsonMap::new();
    value.insert("participant_id".into(), fields.participant_id.clone().into());
    value.insert("kind".into(), fields.kind.clone().into());
    value.insert("package_id".into(), fields.package_id.clone().into());
    value.insert("package_version".into(), fields.package_version.clone().into());
    value.insert("data".into(), mapping_value(&fields.data)?);
    value.insert("package_sha256".into(), optional_text(&fields.package_sha256));
    value.insert("schema_id".into(), optional_text(&fields.schema_id));
    value.insert("schema_sha256".into(), optional_text(&fields.schema_sha256));
    value.insert("validation_status".into(), participant.validation_status().as_str().into());
    value.insert("adaptation_id".into(), optional_text(&fields.adaptation_id));
    Ok(Json::Object(value))
}

/// `_json_value(ActionRecord)`.
fn action_record_value(action: &sylvia_domain::authority::ActionRecord) -> CodedResult<Json> {
    let mut value = JsonMap::new();
    value.insert("action_id".into(), action.action_id.clone().into());
    value.insert("turn_id".into(), action.turn_id.clone().into());
    value.insert("status".into(), action.status.as_str().into());
    value.insert("expected_revision".into(), action.expected_revision.into());
    value.insert("resulting_revision".into(), action.resulting_revision.into());
    value.insert("writer".into(), action.writer.as_str().into());
    value.insert("package_id".into(), action.package_id.clone().into());
    value.insert("package_version".into(), action.package_version.clone().into());
    value.insert("package_sha256".into(), action.package_sha256.clone().into());
    value.insert("tool_name".into(), action.tool_name.clone().into());
    value.insert("parameters".into(), mapping_value(&action.parameters)?);
    value.insert("result".into(), mapping_value(&action.result)?);
    Ok(Json::Object(value))
}

fn mappings_value(items: &[JsonMap]) -> CodedResult<Json> {
    Ok(Json::Array(items.iter().map(mapping_value).collect::<CodedResult<_>>()?))
}

/// `_json_value(CampaignContinuityView)`, with its records.
fn continuity_value(view: &sylvia_domain::authority::CampaignContinuityView) -> CodedResult<Json> {
    let mut records = Vec::with_capacity(view.records.len());
    for record in &view.records {
        let mut value = JsonMap::new();
        value.insert("entry_id".into(), record.entry_id.clone().into());
        value.insert("record_id".into(), record.record_id.clone().into());
        value.insert("kind".into(), record.kind.clone().into());
        value.insert("status".into(), record.status.clone().into());
        value.insert("payload".into(), mapping_value(&record.payload)?);
        value.insert(
            "basis_message_ids".into(),
            Json::Array(record.basis_message_ids.iter().cloned().map(Json::String).collect()),
        );
        value.insert("admitted_revision".into(), record.admitted_revision.into());
        value.insert("operation_id".into(), optional_text(&record.operation_id));
        records.push(Json::Object(value));
    }
    let mut value = JsonMap::new();
    value.insert(
        "current_scene".into(),
        match &view.current_scene {
            Some(scene) => mapping_value(scene)?,
            None => Json::Null,
        },
    );
    value.insert("facts".into(), mappings_value(&view.facts)?);
    value.insert("reveals".into(), mapping_value(&view.reveals)?);
    value.insert("plan_progress".into(), mappings_value(&view.plan_progress)?);
    value.insert("records".into(), Json::Array(records));
    Ok(Json::Object(value))
}

/// `CampaignState`'s fields, which both snapshots begin with.
fn campaign_state_fields(state: &sylvia_domain::authority::CampaignState) -> CodedResult<JsonMap> {
    let mut lock = JsonMap::new();
    lock.insert("entries".into(), sylvia_store::guided_start::package_lock_payload(&state.package_lock));
    let mut value = JsonMap::new();
    value.insert("campaign_id".into(), state.campaign_id.clone().into());
    value.insert("display_name".into(), state.display_name.clone().into());
    value.insert("lifecycle".into(), state.lifecycle.as_str().into());
    value.insert("writer".into(), state.writer.as_str().into());
    value.insert("revision".into(), state.revision.into());
    value.insert("package_lock".into(), Json::Object(lock));
    value.insert("state".into(), mapping_value(&state.state)?);
    value.insert(
        "participants".into(),
        Json::Array(state.participants.iter().map(participant_value).collect::<CodedResult<_>>()?),
    );
    Ok(value)
}

/// The actions and events both snapshots carry after the state.
fn history_fields(
    value: &mut JsonMap,
    actions: &[sylvia_domain::authority::ActionRecord],
    events: &[sylvia_domain::authority::CanonicalEvent],
) -> CodedResult<()> {
    value.insert(
        "actions".into(),
        Json::Array(actions.iter().map(action_record_value).collect::<CodedResult<_>>()?),
    );
    value.insert(
        "events".into(),
        Json::Array(events.iter().map(canonical_event_value).collect::<CodedResult<_>>()?),
    );
    Ok(())
}

/// `turns`, a `Mapping[str, TurnStatus]`, by value in insertion order.
fn turns_value(turns: &[(String, sylvia_domain::authority::TurnStatus)]) -> Json {
    Json::Object(turns.iter().map(|(turn_id, status)| (turn_id.clone(), status.as_str().into())).collect())
}

/// `_json_value(CampaignDisplaySnapshot)`.
pub fn display_snapshot_value(
    snapshot: &sylvia_domain::authority::CampaignDisplaySnapshot,
) -> CodedResult<Json> {
    let mut value = campaign_state_fields(&snapshot.state)?;
    history_fields(&mut value, &snapshot.actions, &snapshot.events)?;
    value.insert("turns".into(), turns_value(&snapshot.turns));
    value.insert("continuity".into(), continuity_value(&snapshot.continuity)?);
    Ok(Json::Object(value))
}

/// `_json_value(CampaignSnapshot)`: mutations and consequences come after the events.
pub fn snapshot_value(snapshot: &sylvia_domain::authority::CampaignSnapshot) -> CodedResult<Json> {
    let mut value = campaign_state_fields(&snapshot.state)?;
    history_fields(&mut value, &snapshot.actions, &snapshot.events)?;
    let mut mutations = Vec::with_capacity(snapshot.mutations.len());
    for item in &snapshot.mutations {
        let mut mutation = JsonMap::new();
        mutation.insert("action_id".into(), item.action_id.clone().into());
        mutation.insert("ordinal".into(), item.ordinal.into());
        mutation.insert("namespace".into(), item.namespace.clone().into());
        mutation.insert("key".into(), item.key.clone().into());
        mutation.insert("before".into(), json_value(&item.before)?);
        mutation.insert("after".into(), json_value(&item.after)?);
        mutations.push(Json::Object(mutation));
    }
    value.insert("mutations".into(), Json::Array(mutations));
    let mut consequences = Vec::with_capacity(snapshot.consequences.len());
    for item in &snapshot.consequences {
        let mut consequence = JsonMap::new();
        consequence.insert("action_id".into(), item.action_id.clone().into());
        consequence.insert("ordinal".into(), item.ordinal.into());
        consequence.insert("payload".into(), mapping_value(&item.payload)?);
        consequences.push(Json::Object(consequence));
    }
    value.insert("consequences".into(), Json::Array(consequences));
    value.insert("turns".into(), turns_value(&snapshot.turns));
    value.insert("continuity".into(), continuity_value(&snapshot.continuity)?);
    Ok(Json::Object(value))
}
