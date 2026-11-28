//! `agent/_context_ledger.py`: append-only GM request assembly, a frozen system prefix plus a
//! wire journal.
//!
//! Canonical assembly (`ProviderSessionAdapter::messages`, the turn phases and recovery) decides
//! what the GM should know; the ledger reconciles that canonical list against what the model has
//! already been sent in the current context epoch, so each provider request extends the previous
//! one byte for byte. It is the shipped request assembly on `claude_code`, `codex_app_server` and
//! `anthropic_api`.
//!
//! Ported so far: (L1) the tool-list settings, the host seal and the ledger contract, the check
//! that canonical assembly's fixed blocks kept their tags, and the record labels; (L2a) the
//! journal's view of a message ([`wire`]) and the facts and campaign-state updates ([`state`]);
//! (L2b) the tool lists ([`tools`]); (L3) the journal and Claude Code's native records
//! ([`journal`]). The mode and
//! route (`gm_route`, `context_mode`, `LEDGER_REQUEST`) are `sylvia_provider::context_mode`,
//! which providers read, re-exported here. The long model-read texts are data:
//! `ledger_texts.json`, exported from the Python values by `generate_ledger_texts.py`.

pub mod journal;
pub mod state;
pub mod tools;
pub mod wire;

use serde_json::Value as Json;
use std::sync::OnceLock;
use sylvia_base::pyjson;
use sylvia_base::{pystr, CodedError, CodedResult, JsonMap};
use sylvia_domain::pyops::py_truthy;
use sylvia_provider::config::env::Environment;
use sylvia_provider::model_facts::claude_model_facts;

pub use crate::provider_session::tagged::{
    Tagged, ACTIVE_RECORDS_KEY, HOST_RECORD_KEY, LEDGER_ENTRY_KEY, LEDGER_KEY, STABLE_KEY,
};
pub use sylvia_provider::context_mode::{
    context_mode, context_mode_setting, gm_route, ANTHROPIC_API_ROUTE, CONTINUE_LABEL, CONTINUE_RECORD_TEXT,
    DEFAULT_CONTEXT_MODE, DEFAULT_CONTEXT_MODE_BY_TRANSPORT, LEDGER_MODE, LEGACY_MODE, MODE_ENV,
};

/// `JOURNAL_KIND`: the session evidence the journal is saved as.
pub const JOURNAL_KIND: &str = "gm_wire_journal";
/// `JOURNAL_VERSION`.
pub const JOURNAL_VERSION: i64 = 1;
/// `NATIVE_KIND`: Claude Code's own context records, saved per request.
pub const NATIVE_KIND: &str = "gm_native_records";
/// `NATIVE_VERSION`.
pub const NATIVE_VERSION: i64 = 1;
/// `NATIVE_STATUSES`.
pub const NATIVE_STATUSES: [&str; 2] = ["collected", "unknown"];
/// `MARKER_KEYS`: the canonical-message tags, which never reach a provider.
pub const MARKER_KEYS: [&str; 5] =
    [STABLE_KEY, LEDGER_KEY, HOST_RECORD_KEY, LEDGER_ENTRY_KEY, ACTIVE_RECORDS_KEY];

/// `FIXED_BLOCK_PREFIXES`: labels of fixed instruction blocks; such a block is never an
/// in-stream host record.
pub const FIXED_BLOCK_PREFIXES: [&str; 4] = [
    "SYLVIA_GM_CONTEXT_CONTRACT ",
    "SYLVIA_GM_CONTEXT_LEDGER_CONTRACT ",
    "SYLVIA_GM_RULESET_OPERATING_GUIDE ",
    "SYLVIA_GM_RESPONSE_FORMAT",
];

/// `_NATIVE_FIELDS`: private per-exchange provider fields, reattached from `_native_reference`
/// and never persisted in the journal.
pub const NATIVE_FIELDS: [&str; 5] =
    ["anthropic_content", "reasoning_content", "reasoning", "responses_output", "_context_owner"];
/// `LIFTING_TRANSPORTS`: transports that lift system messages out of conversation order into
/// one top-level value.
pub const LIFTING_TRANSPORTS: [&str; 4] =
    ["anthropic_messages", "claude_code", "codex_app_server", "gemini_generate_content"];

/// `STATE_LABEL`.
pub const STATE_LABEL: &str = "SYLVIA_GM_FRESH_CAMPAIGN_STATE";
/// `UPDATE_LABEL`.
pub const UPDATE_LABEL: &str = "SYLVIA_GM_CAMPAIGN_STATE_UPDATE";
/// `NOTE_LABEL`.
pub const NOTE_LABEL: &str = "SYLVIA_CONTINUATION_NOTE";
/// `PREPARATION_LABEL`.
pub const PREPARATION_LABEL: &str = "SYLVIA_GM_CAMPAIGN_PREPARATION";
/// `_PREPARATION_CONTROL_PREFIX`.
pub const PREPARATION_CONTROL_PREFIX: &str = "SYLVIA_GM_CAMPAIGN_PREPARATION (application-owned control,";
/// `LATEST_WINS_LABELS`, sorted (Python's `sorted(LATEST_WINS_LABELS)`, as the contract lists
/// them).
pub const LATEST_WINS_LABELS: [&str; 3] =
    ["SYLVIA_GM_CAMPAIGN_PREPARATION", "SYLVIA_GM_RELEVANT_CHARACTER_FILES", "SYLVIA_PLAYER_CONTROL_RESULTS"];
/// `DROPPED_CONTROL_PREFIXES`: positional controls that only make sense when nothing follows
/// the player message.
pub const DROPPED_CONTROL_PREFIXES: [&str; 1] = ["SYLVIA_GM_CURRENT_PLAYER_MESSAGE "];
/// `WITHDRAWN_LABELS`: latest-wins labels that can leave canonical assembly.
pub const WITHDRAWN_LABELS: [&str; 2] =
    ["SYLVIA_PLAYER_CONTROL_RESULTS", "SYLVIA_GM_RELEVANT_CHARACTER_FILES"];
/// `IDENTITY_PREFIXES`: journaled conversation identities whose absence from canonical history
/// means a rewind.
pub const IDENTITY_PREFIXES: [&str; 4] = ["user:", "talk:", "narration:", "steering:"];
/// `NARRATION_FACTS_LABEL`.
pub const NARRATION_FACTS_LABEL: &str = "SYLVIA_GM_EXACT_NARRATION_FACTS";
/// `TRANSITION_LABEL`.
pub const TRANSITION_LABEL: &str = "SYLVIA_GM_AUTOMATIC_ENGINE_TRANSITION_RESULT";
/// `PLAYER_TEXT_MARKER`: put in front of player text that itself begins like a host seal.
pub const PLAYER_TEXT_MARKER: &str = "(player text) ";

/// `DEFAULT_REPLACE_DEPTH`: a changed campaign-state subtree is replaced whole once its path
/// reaches this depth.
pub const DEFAULT_REPLACE_DEPTH: usize = 2;
/// `SUBTREE_REPLACE_DEPTH`.
pub const SUBTREE_REPLACE_DEPTH: [(&[&str], usize); 2] =
    [(&["campaign_state", "ruleset_owned_values"], 5), (&["campaign_state", "current_scene"], 3)];
/// `FACT_LIST_PATHS`: relevance-ranked fact lists, upserted by record id.
pub const FACT_LIST_PATHS: [&[&str]; 2] =
    [&["PLAYER_KNOWN", "current_facts"], &["GM_PRIVATE_CURRENT", "current_facts"]];
/// `ADDITIVE_MAP_PATHS`: selection-derived maps of lists, merged additively per key.
pub const ADDITIVE_MAP_PATHS: [&[&str]; 1] = [&["GM_PRIVATE_CURRENT", "character_knowledge"]];

// ── tool lists ─────────────────────────────────────────────────────────────────────────────

/// `TOOL_LIST_ENV`.
pub const TOOL_LIST_ENV: &str = "SYLVIA_GM_TOOL_LIST";
/// `STABLE_TOOL_LIST`: one tool list for the whole session in ledger mode.
pub const STABLE_TOOL_LIST: &str = "stable";
/// `FIXED_TOOL_LIST`: the full, state-independent catalog.
pub const FIXED_TOOL_LIST: &str = "fixed";
/// `ROUND_TOOL_LISTS`: the list the turn loop offers each round.
pub const ROUND_TOOL_LISTS: &str = "rounds";
/// `DEFERRED_TOOL_LIST`: the fixed catalog declared once, Ruleset mechanics deferred.
pub const DEFERRED_TOOL_LIST: &str = "deferred";
/// `NATIVE_DEFERRAL`: the provider's own deferred tools and appended tool changes.
pub const NATIVE_DEFERRAL: &str = "native";
/// `LOADER_DEFERRAL`: Sylvia's loader (`_tool_loader`).
pub const LOADER_DEFERRAL: &str = "loader";
/// `PROVIDER_DEFERRAL`: the provider searches and loads deferred tools itself.
pub const PROVIDER_DEFERRAL: &str = "provider";
/// `CALLABLE_LABEL`.
pub const CALLABLE_LABEL: &str = "SYLVIA_GM_CALLABLE_TOOLS";
/// `TOOL_CHANGE_CLASS`: the journal class of an appended record that makes tools usable.
pub const TOOL_CHANGE_CLASS: &str = "tools";
/// `TOOL_CHANGE_LABEL`.
pub const TOOL_CHANGE_LABEL: &str = "SYLVIA_GM_TOOL_ADDITION";
/// `TOOL_CHANGE_BLOCKS`.
pub const TOOL_CHANGE_BLOCKS: [&str; 2] = ["tool_addition", "tool_removal"];
const TOOL_LISTS: [&str; 4] = [STABLE_TOOL_LIST, ROUND_TOOL_LISTS, FIXED_TOOL_LIST, DEFERRED_TOOL_LIST];

/// `DEFAULT_TOOL_LIST`: a route ships with per-round lists unless it is named below.
pub const DEFAULT_TOOL_LIST: &str = ROUND_TOOL_LISTS;
/// `DEFAULT_TOOL_LIST_BY_TRANSPORT`: the routes moved to one list, by route name.
pub const DEFAULT_TOOL_LIST_BY_TRANSPORT: [(&str, &str); 3] = [
    ("claude_code", DEFERRED_TOOL_LIST),
    ("codex_app_server", DEFERRED_TOOL_LIST),
    (ANTHROPIC_API_ROUTE, DEFERRED_TOOL_LIST),
];

/// `NATIVE_DEFERRED_ROUTES`: routes whose provider takes deferred tools itself, and whether a
/// wire model does. The Claude API defers on models with `tool_changes` (`_claude_api_defers`).
fn native_deferred_route(route: &str) -> Option<fn(&str) -> bool> {
    (route == ANTHROPIC_API_ROUTE).then_some(|model_id: &str| claude_model_facts(model_id).tool_changes)
}

/// `PROVIDER_DEFERRED_ROUTES`: routes whose provider loads deferred tools by its own search
/// (Claude Code's tool search), for every model.
fn provider_deferred_route(route: &str) -> Option<fn(&str) -> bool> {
    (route == "claude_code").then_some(|_model_id: &str| true)
}

/// `environ.get(TOOL_LIST_ENV, "")`, stripped and casefolded. A value that is not text reads as
/// unset, as `context_mode_setting` reads one.
fn tool_list_value(environment: &Environment) -> String {
    let value = environment.get(TOOL_LIST_ENV).and_then(Result::ok).unwrap_or("");
    pystr::casefold(pystr::strip(value))
}

/// `tool_list_setting(environ, transport=..., provider_key=...)`: `SYLVIA_GM_TOOL_LIST` when it
/// names a list, else the route's shipped default.
pub fn tool_list_setting(
    environment: &Environment,
    transport: Option<&str>,
    provider_key: Option<&str>,
) -> &'static str {
    let setting = tool_list_value(environment);
    if let Some(named) = TOOL_LISTS.iter().find(|name| **name == setting) {
        return named;
    }
    let route = gm_route(transport, provider_key);
    DEFAULT_TOOL_LIST_BY_TRANSPORT
        .iter()
        .find(|(name, _)| *name == route)
        .map_or(DEFAULT_TOOL_LIST, |(_, list)| list)
}

/// `stable_tool_list(environ, transport=..., provider_key=...)`: whether ledger mode sends one
/// tool list for the session on this route.
pub fn stable_tool_list(
    environment: &Environment,
    transport: Option<&str>,
    provider_key: Option<&str>,
) -> bool {
    [STABLE_TOOL_LIST, FIXED_TOOL_LIST, DEFERRED_TOOL_LIST].contains(&tool_list_setting(
        environment,
        transport,
        provider_key,
    ))
}

/// `fixed_tool_list(environ)`: the full, state-independent catalog is opt-in; route defaults
/// stay unchanged.
pub fn fixed_tool_list(environment: &Environment) -> bool {
    tool_list_value(environment) == FIXED_TOOL_LIST
}

/// `deferred_tool_list(environ, transport=..., provider_key=..., model_id=...)`: how this route
/// defers tools (`native`, `provider` or `loader`), or `None` without `deferred` and on a
/// registered route whose model cannot defer there.
pub fn deferred_tool_list(
    environment: &Environment,
    transport: Option<&str>,
    provider_key: Option<&str>,
    model_id: Option<&str>,
) -> Option<&'static str> {
    if tool_list_setting(environment, transport, provider_key) != DEFERRED_TOOL_LIST {
        return None;
    }
    let route = gm_route(transport, provider_key);
    for (kind, supports) in [
        (PROVIDER_DEFERRAL, provider_deferred_route(&route)),
        (NATIVE_DEFERRAL, native_deferred_route(&route)),
    ] {
        if let Some(supports) = supports {
            return supports(model_id.unwrap_or("")).then_some(kind);
        }
    }
    Some(LOADER_DEFERRAL)
}

// ── seal and contract ──────────────────────────────────────────────────────────────────────

fn texts() -> &'static JsonMap {
    static TEXTS: OnceLock<JsonMap> = OnceLock::new();
    TEXTS.get_or_init(|| match pyjson::loads(include_str!("ledger_texts.json")) {
        Ok(Json::Object(map)) => map,
        _ => panic!("ledger_texts.json is an exported JSON object"),
    })
}

fn text(key: &str) -> &'static str {
    texts()[key].as_str().expect("an exported text")
}

/// `host_seal(session_id)`: a short deterministic token that marks genuine host records for this
/// session.
pub fn host_seal(session_id: &str) -> String {
    let mut bytes = b"sylvia-gm-host-record-seal/v1\x00".to_vec();
    bytes.extend_from_slice(session_id.as_bytes());
    sylvia_base::hash::sha256_hex(&bytes)[..12].to_owned()
}

/// `seal_prefix(seal)`.
pub fn seal_prefix(seal: &str) -> String {
    format!("[host-seal {seal}] ")
}

/// `ledger_contract(seal)`: the fixed instruction block that replaces the data and
/// player-message guidance.
pub fn ledger_contract(seal: &str) -> String {
    let token = pystr::strip(&seal_prefix(seal)).to_owned();
    format!("{}{token}{}", text("ledger_contract_prefix"), text("ledger_contract_suffix"))
}

/// `_withdrawn(label)`: the record that replaces a latest-wins label canonical assembly no
/// longer sends; the earlier records stay true history.
pub fn withdrawn(label: &str) -> String {
    format!("{label}{}", text("withdrawn_suffix"))
}

/// `_FACTS_HEADER`.
pub fn facts_header() -> &'static str {
    text("facts_header")
}

/// `_FACTS_ADDED_HEADER`.
pub fn facts_added_header() -> &'static str {
    text("facts_added_header")
}

/// `_UPDATE_HEADER`.
pub fn update_header() -> &'static str {
    text("update_header")
}

// ── the tag check ──────────────────────────────────────────────────────────────────────────

/// `LedgerTagError`, a `ValueError`: canonical assembly's ledger tags were lost. The owner
/// answers a `ValueError` as a rejection under its own class name.
fn ledger_tag_error(message: &str) -> CodedError {
    CodedError::rejected(message).with_error_type("LedgerTagError")
}

fn tagged_stable(message: &Tagged) -> bool {
    message.tag_of(STABLE_KEY).is_some_and(py_truthy)
}

fn is_system(message: &Tagged) -> bool {
    message.message.get("role").and_then(Json::as_str) == Some("system")
}

/// `check_fixed_blocks_tagged(canonical)`: fail loudly instead of sending the GM contract or a
/// fixed block as a host record. Content that is not text never starts with a label
/// (`str(...)` of a list or `None` does not).
pub fn check_fixed_blocks_tagged(canonical: &[Tagged]) -> CodedResult<()> {
    if let Some(first) = canonical.first() {
        if is_system(first) && !tagged_stable(first) {
            return Err(ledger_tag_error("The GM contract reached the context ledger without its tags."));
        }
    }
    for message in canonical {
        let content = message.message.get("content").and_then(Json::as_str).unwrap_or("");
        if is_system(message)
            && !tagged_stable(message)
            && FIXED_BLOCK_PREFIXES.iter().any(|prefix| content.starts_with(prefix))
        {
            return Err(ledger_tag_error("A fixed instruction block reached the context ledger untagged."));
        }
    }
    Ok(())
}

// ── export markers ─────────────────────────────────────────────────────────────────────────

/// `value.get(key)`: `null` when absent.
fn get(value: &JsonMap, key: &str) -> Json {
    value.get(key).cloned().unwrap_or(Json::Null)
}

/// `len(value or ())`: a falsy value counts nothing; a list, mapping or string counts its items,
/// keys or characters; a non-zero number or `true` has no length, Python's `TypeError`, which
/// the owner does not catch.
fn length_or_empty(value: &Json) -> CodedResult<usize> {
    if !py_truthy(value) {
        return Ok(0);
    }
    match value {
        Json::Array(items) => Ok(items.len()),
        Json::Object(map) => Ok(map.len()),
        Json::String(text) => Ok(pystr::len(text)),
        other => Err(CodedError::new(
            "internal_error",
            format!("object of type '{}' has no len()", sylvia_domain::pyops::py_type_name(other)),
        )
        .with_error_type("TypeError")),
    }
}

/// `_stub_marker(value)`: a record left out of an export keeps only its kind.
pub fn stub_marker(value: &JsonMap) -> Option<JsonMap> {
    if value.get("omitted_from_export") != Some(&Json::Bool(true)) {
        return None;
    }
    let mut stub = JsonMap::new();
    stub.insert("kind".into(), get(value, "kind"));
    stub.insert("omitted_from_export".into(), Json::Bool(true));
    Some(stub)
}

/// `native_marker(value)`: the only part of a native-records record that may leave the owner.
pub fn native_marker(value: &JsonMap) -> CodedResult<JsonMap> {
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

/// `journal_marker(value)`: the only part of a journal record that may leave the owner (it
/// holds GM-private data).
pub fn journal_marker(value: &JsonMap) -> CodedResult<JsonMap> {
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_continue_text_is_the_exported_one() {
        assert_eq!(text("continue_text"), CONTINUE_RECORD_TEXT);
    }
}
