//! `agent/_ruleset_test_provider.py`: scripted model replies for saved scenarios, through the
//! ordinary campaign session adapter.
//!
//! [`RulesetTestProvider`] supplies the scenario's authored choices while discovery and turn
//! control stay with production code, and [`RulesetTestProvider::observe`] records what the
//! discovery and the tool evidence showed. Nothing runs it yet: the scenario's turn waits on
//! lane 4's `CampaignSessionLoop.run` (T5), so [`super::run_scenario`] stops before it.

use futures_util::stream;
use indexmap::IndexMap;
use std::collections::BTreeSet;
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::pystr::repr_json;
use sylvia_base::{CodedError, CodedResult, Json, JsonMap};
use sylvia_provider::types::{ModelInfo, ProviderResponse, ProviderStreamChunk, PyValue};
use sylvia_provider::{
    BoxFuture, ChunkStream, ErrorKind, Provider, ProviderError, ProviderRequest, ProviderResult,
};

use super::runner::ScenarioError;
use crate::provider_session::tools::tool_alias;
use crate::session_loop::{
    CatalogSupplier, ProviderEvent, SessionEvents, SessionProvider, SessionProviderError,
    SessionProviderInput,
};

/// `CALLABLE_LABEL` (`agent/_context_ledger.py`): the label of the record saying which listed
/// tools can be called at this step.
pub const CALLABLE_LABEL: &str = "SYLVIA_GM_CALLABLE_TOOLS";

/// `decision_owner()`: who holds the campaign's model decision now (`"none"`, `"gm"`, ...).
pub type DecisionOwner = Arc<dyn Fn() -> BoxFuture<'static, CodedResult<String>> + Send + Sync>;

#[derive(Default)]
struct State {
    /// `_aliases`: provider name to Ruleset tool name, from the latest discovery.
    aliases: IndexMap<String, String>,
    requests: Vec<JsonMap>,
    tool_results: Vec<JsonMap>,
    position: usize,
    failure: Option<String>,
}

/// `RulesetTestProvider(replies, decision_owner=...)`.
pub struct RulesetTestProvider {
    replies: Vec<JsonMap>,
    decision_owner: DecisionOwner,
    state: Mutex<State>,
}

/// `str(value)` for a JSON value: text as itself, anything else as its `repr`.
fn py_str(value: &Json) -> String {
    match value {
        Json::String(text) => text.clone(),
        other => repr_json(other),
    }
}

/// The plain exception a scripted reply raises out of the stream (`ScenarioScriptError`, or
/// what the record parsing raises).
fn raised(message: &str) -> ProviderError {
    ProviderError::new(ErrorKind::Internal, message)
}

/// The items of `expected.get(key, ())`; only text can name an available tool.
fn listed(value: Option<&Json>) -> &[Json] {
    value.and_then(Json::as_array).map_or(&[], Vec::as_slice)
}

/// Whether `item`, an expected tool name, is one of `names`.
fn named(names: &BTreeSet<String>, item: &Json) -> bool {
    item.as_str().is_some_and(|name| names.contains(name))
}

impl RulesetTestProvider {
    /// `RulesetTestProvider(replies, decision_owner=decision_owner)`: the step's saved replies,
    /// copied.
    pub fn new(replies: &[JsonMap], decision_owner: DecisionOwner) -> Arc<Self> {
        Arc::new(Self { replies: replies.to_vec(), decision_owner, state: Mutex::new(State::default()) })
    }

    fn state(&self) -> MutexGuard<'_, State> {
        self.state.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// `requests`: what each request showed, `{"decision_owner", "available_tools"}`.
    pub fn requests(&self) -> Vec<JsonMap> {
        self.state().requests.clone()
    }

    /// `tool_results`: each tool result the turn's evidence held, by Ruleset tool name.
    pub fn tool_results(&self) -> Vec<JsonMap> {
        self.state().tool_results.clone()
    }

    /// `position`: how many replies were used.
    pub fn position(&self) -> usize {
        self.state().position
    }

    /// `failure`: the first script mismatch, kept even if the adapter turned it into a stop.
    pub fn failure(&self) -> Option<String> {
        self.state().failure.clone()
    }

    /// `assert_finished()`: a turn ending before every saved reply was used is not a pass.
    pub fn assert_finished(&self) -> Result<(), ScenarioError> {
        let mut state = self.state();
        if let Some(failure) = &state.failure {
            return Err(ScenarioError::Script(failure.clone()));
        }
        if state.position != self.replies.len() {
            let message = "The game loop finished with unused scripted replies.";
            state.failure = Some(message.into());
            return Err(ScenarioError::Script(message.into()));
        }
        Ok(())
    }

    /// `observe(driver)`: the driver, run with a catalog supplier that records the discovered
    /// tools' aliases and events whose tool results are recorded, without changing its tools
    /// or admission policy.
    pub fn observe(self: &Arc<Self>, driver: Arc<dyn SessionProvider>) -> ObservedProvider {
        ObservedProvider { provider: Arc::clone(self), driver }
    }

    /// `_fail(message)`: keep a concise failure, then raise `ScenarioScriptError`.
    fn fail(&self, message: &str) -> ProviderError {
        self.state().failure = Some(message.to_owned());
        raised(message)
    }

    /// The body of `generate_stream`, which runs before its one chunk.
    async fn reply(&self, request: ProviderRequest) -> ProviderResult<ProviderStreamChunk> {
        let aliases = self.state().aliases.clone();
        let mut exposed: IndexMap<String, Option<String>> = IndexMap::new();
        for tool in request.tools.iter().flatten() {
            let name =
                py_str(tool.get("function").and_then(|function| function.get("name")).unwrap_or(&Json::Null));
            let alias = aliases.get(&name).cloned();
            exposed.insert(name, alias);
        }
        // With one tool list the latest callable-tools record says which listed tools can be
        // called at this step; read it as a model would.
        let record = request
            .messages
            .iter()
            .rev()
            .map(|message| message.get("content").map(py_str).unwrap_or_default())
            .find(|content| content.contains(CALLABLE_LABEL))
            .unwrap_or_default();
        if record.contains("no tool can be called") {
            exposed.clear();
        } else if record.contains(". A call to") {
            // `record.rsplit(": ", 1)[1]`: an `IndexError` without one.
            let Some((_, tail)) = record.rsplit_once(": ") else {
                return Err(raised("list index out of range"));
            };
            let listed = tail.split_once(". A call to").map_or(tail, |(listed, _)| listed);
            let named: BTreeSet<&str> = listed.split(", ").collect();
            let keep = record.contains("only these listed tools");
            exposed.retain(|alias, _| named.contains(alias.as_str()) == keep);
        }
        let names: BTreeSet<String> = exposed.values().flatten().cloned().collect();
        let owner = (self.decision_owner)().await.map_err(|error| raised(&error.message))?;
        let mut record_request = JsonMap::new();
        record_request.insert("decision_owner".into(), Json::String(owner.clone()));
        record_request.insert("available_tools".into(), names.iter().cloned().map(Json::String).collect());
        let position = {
            let mut state = self.state();
            state.requests.push(record_request);
            if state.position >= self.replies.len() {
                drop(state);
                return Err(self.fail("The game loop requested a reply after the saved script ended."));
            }
            state.position += 1;
            state.position
        };
        let reply = &self.replies[position - 1];
        let empty = JsonMap::new();
        let expected = reply.get("expect_request").and_then(Json::as_object).unwrap_or(&empty);
        if expected.get("decision_owner").is_some_and(|value| value.as_str() != Some(owner.as_str())) {
            return Err(self.fail("Decision owner differs from the saved expectation."));
        }
        if !listed(expected.get("available_tools")).iter().all(|item| named(&names, item)) {
            return Err(self.fail("An expected operation is unavailable at this request."));
        }
        if listed(expected.get("unavailable_tools")).iter().any(|item| named(&names, item)) {
            return Err(self.fail("An operation expected to be withheld is available at this request."));
        }
        let aliases_of = |tool: &str| -> Vec<String> {
            exposed
                .iter()
                .filter(|(_, name)| name.as_deref() == Some(tool))
                .map(|(alias, _)| alias.clone())
                .collect()
        };
        let text = |key: &str| reply.get(key).map(py_str).unwrap_or_default();
        let (name, arguments) = if reply.get("kind").and_then(Json::as_str) == Some("tool") {
            let tool = reply.get("tool").and_then(Json::as_str).unwrap_or_default();
            let matches = aliases_of(tool);
            if matches.len() != 1 {
                return Err(self.fail("The scripted operation is not uniquely available at this request."));
            }
            (matches[0].clone(), reply.get("arguments").cloned().unwrap_or(Json::Null))
        } else {
            // Complete narration through the exposed disposition tool when the normal loop
            // uses it.
            let disposition = aliases_of("update_campaign_file");
            if disposition.is_empty() {
                return Ok(ProviderStreamChunk {
                    content: text("text"),
                    finish_reason: Some(PyValue::Text("stop".into())),
                    ..ProviderStreamChunk::default()
                });
            }
            if disposition.len() != 1 {
                return Err(self.fail("The narration disposition operation is ambiguous."));
            }
            let arguments = serde_json::json!({
                "state_change": "unchanged",
                "state_change_summary": "The scenario adds no durable narrative facts.",
                "revised_player_facing_response": [{"kind": "narration", "text": text("text")}],
            });
            (disposition[0].clone(), arguments)
        };
        Ok(ProviderStreamChunk {
            tool_call_delta: Some(serde_json::json!({
                "index": 0,
                "id": format!("scenario-call-{position}"),
                "type": "function",
                "function": {"name": name, "arguments": pyjson::dumps(&arguments, Dump::DEFAULT)},
            })),
            finish_reason: Some(PyValue::Text("tool_calls".into())),
            ..ProviderStreamChunk::default()
        })
    }
}

impl Provider for RulesetTestProvider {
    fn model_info(&self) -> Option<&ModelInfo> {
        None
    }

    /// `generate`: scenarios only stream.
    fn generate(&self, _request: ProviderRequest) -> BoxFuture<'_, ProviderResult<ProviderResponse>> {
        Box::pin(async { Err(raised("Scenario execution requires the ordinary streaming adapter.")) })
    }

    /// `generate_stream`: the next saved reply, as one chunk, after checking the request
    /// against its expectations.
    fn generate_stream(&self, request: ProviderRequest) -> ChunkStream<'_> {
        Box::pin(stream::once(self.reply(request)))
    }

    /// `close()`: no network connection or credential is used.
    fn close(&self) -> BoxFuture<'_, ()> {
        Box::pin(async {})
    }
}

/// `observe(driver)`'s session provider.
pub struct ObservedProvider {
    provider: Arc<RulesetTestProvider>,
    driver: Arc<dyn SessionProvider>,
}

impl SessionProvider for ObservedProvider {
    fn run<'a>(
        &'a self,
        input: SessionProviderInput,
        events: &'a mut dyn SessionEvents,
    ) -> BoxFuture<'a, Result<(), SessionProviderError>> {
        let original = input.clone();
        let provider = Arc::clone(&self.provider);
        // `discover(discovery_id)`: the actual discovery result, its aliases recorded.
        let discover: CatalogSupplier = Arc::new(move |discovery_id: String| {
            let (original, provider) = (original.clone(), Arc::clone(&provider));
            Box::pin(async move {
                let catalog = original.current_catalog(&discovery_id).await?;
                provider.state().aliases =
                    catalog.iter().map(|tool| (tool_alias(tool), tool.name.clone())).collect();
                Ok(catalog)
            })
        });
        let mut replaced = input;
        replaced.catalog_supplier = Some(discover);
        Box::pin(async move {
            let mut observing = Observing { provider: &self.provider, events };
            self.driver.run(replaced, &mut observing).await
        })
    }
}

/// The events of an observed turn: each `provider_tool_results` evidence is recorded, then
/// passed on.
struct Observing<'a> {
    provider: &'a RulesetTestProvider,
    events: &'a mut dyn SessionEvents,
}

/// `mapping[key]` on evidence without it: Python's `KeyError`.
fn key_error(key: &str) -> CodedError {
    CodedError::new("internal_error", format!("'{key}'")).with_error_type("KeyError")
}

impl Observing<'_> {
    fn record(&self, payload: &JsonMap) -> CodedResult<()> {
        if payload.get("kind").and_then(Json::as_str) != Some("provider_tool_results") {
            return Ok(());
        }
        let results = payload.get("results").ok_or_else(|| key_error("results"))?;
        for result in results.as_array().into_iter().flatten() {
            let name = result.get("name").ok_or_else(|| key_error("name"))?;
            let content = result.get("content").ok_or_else(|| key_error("content"))?;
            // `json.loads(content)`: anything but text is a `TypeError`.
            let content = content.as_str().ok_or_else(|| {
                CodedError::new("internal_error", "the JSON object must be str, bytes or bytearray")
                    .with_error_type("TypeError")
            })?;
            let parsed = pyjson::loads(content)?;
            let mut state = self.provider.state();
            let tool = name
                .as_str()
                .and_then(|name| state.aliases.get(name))
                .map_or_else(|| name.clone(), |tool| Json::String(tool.clone()));
            let mut item = JsonMap::new();
            item.insert("tool".into(), tool);
            item.insert("result".into(), parsed);
            state.tool_results.push(item);
        }
        Ok(())
    }
}

impl SessionEvents for Observing<'_> {
    fn emit(&mut self, event: ProviderEvent) -> BoxFuture<'_, Result<(), SessionProviderError>> {
        Box::pin(async move {
            if let ProviderEvent::Evidence { payload, .. } = &event {
                self.record(payload)?;
            }
            self.events.emit(event).await
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn py_str_is_text_or_repr() {
        assert_eq!(py_str(&Json::String("a".into())), "a");
        assert_eq!(py_str(&Json::Null), "None");
        assert_eq!(py_str(&serde_json::json!([1, "b"])), "[1, 'b']");
    }
}
