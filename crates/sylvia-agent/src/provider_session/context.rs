//! The provider session adapter's construction and the campaign context it projects for a GM
//! request (`ProviderSessionAdapter.__init__`, `_current_campaign_context_payload`,
//! `_campaign_context_messages`, `_opening_footing_payload`, `_participant_prompt_payloads`, and
//! the module helpers they use).

use super::reads::OpeningReads;
use crate::campaign_memory::{self, KnowledgeReference};
use crate::stateless_mcp::ModelMechanicsTool;
use serde_json::{json, Value as Json};
use std::collections::HashMap;
use std::sync::Arc;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{pystr, CodedError, CodedResult, JsonMap};
use sylvia_domain::authority::{CanonicalEventType, EventVisibility, Participant, WriterRole};
use sylvia_domain::session::{SessionContext, SessionMessageKind, SessionRecord};
use sylvia_provider::config::env::Environment;
use sylvia_provider::Provider;

const MAX_CAMPAIGN_PLAN_BYTES: usize = 1024 * 1024;
const MAX_GM_SYSTEM_PROMPT_BYTES: usize = 1024 * 1024;
/// `_MAX_SETTING_SOURCE_EXCERPT_CHARS`.
const MAX_SETTING_SOURCE_EXCERPT_CHARS: usize = 2_000;
/// `package_reads.DEFAULT_MAXIMUM_BYTES`, `package_text_file`'s default.
pub const DEFAULT_MAXIMUM_BYTES: i64 = 64 * 1024;

pub use sylvia_provider::context_mode::{context_mode, gm_route, LEDGER_MODE, LEGACY_MODE, MODE_ENV};

/// `SessionProviderInput`: what one provider request is assembled from.
#[derive(Clone)]
pub struct SessionProviderInput {
    pub session: SessionRecord,
    pub history: SessionContext,
    pub catalog: Vec<ModelMechanicsTool>,
    pub basis_message_id: Option<String>,
    pub recovery_kind: Option<String>,
    /// `reference_reader` is present only on live GM turns; the request assembly does not use
    /// it yet ([`super::messages`] refuses a request that has one until it is ported).
    pub has_reference_reader: bool,
}

/// `ProviderSessionAdapter`: the parts the request assembly uses.
pub struct ProviderSessionAdapter {
    pub(crate) reads: Arc<dyn OpeningReads>,
    pub(crate) provider: Arc<dyn Provider>,
    pub(crate) gm_system_prompt: Option<String>,
    pub(crate) campaign_plan_text: Option<String>,
    pub(crate) campaign_plan: Option<JsonMap>,
    pub(crate) campaign_knowledge: Option<JsonMap>,
    pub(crate) context_mode: String,
}

fn value_error(message: &str) -> CodedError {
    CodedError::rejected(message).with_error_type("ValueError")
}

pub(crate) fn rejected(message: &str) -> CodedError {
    CodedError::rejected(message).with_error_type("CommandRejected")
}

/// Whether an error is a Python `CommandRejected` (or a subclass of it).
pub(crate) fn is_command_rejected(error: &CodedError) -> bool {
    matches!(
        error.error_type,
        None | Some("CommandRejected" | "MechanicsRejected" | "PortableRecoveryFailure")
    )
}

impl ProviderSessionAdapter {
    /// `ProviderSessionAdapter(owner, provider, owner_id=..., gm_system_prompt=..., timeout=...,
    /// campaign_plan_text=..., campaign_plan=..., campaign_knowledge=...)`, with Python's
    /// checks in Python's order. `environment` is the process environment the context mode is
    /// read from.
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        reads: Arc<dyn OpeningReads>,
        provider: Arc<dyn Provider>,
        owner_id: &str,
        gm_system_prompt: Option<String>,
        timeout: f64,
        campaign_plan_text: Option<String>,
        campaign_plan: Option<JsonMap>,
        campaign_knowledge: Option<JsonMap>,
        environment: &Environment,
    ) -> CodedResult<Self> {
        if owner_id.is_empty() {
            return Err(value_error("Provider session owner identity must be nonempty"));
        }
        if timeout <= 0.0 {
            return Err(value_error("Provider session timeout must be positive"));
        }
        if let Some(prompt) = &gm_system_prompt {
            if pystr::strip(prompt).is_empty() || prompt.len() > MAX_GM_SYSTEM_PROMPT_BYTES {
                return Err(value_error("GM system prompt is empty or exceeds the usability limit"));
            }
        }
        let model = provider.model_info();
        let context_mode = context_mode(
            environment,
            model.map(|model| model.transport.as_str()),
            model.map(|model| model.provider_key.as_str()),
        );
        if let Some(plan_text) = &campaign_plan_text {
            if pystr::strip(plan_text).is_empty() || plan_text.len() > MAX_CAMPAIGN_PLAN_BYTES {
                return Err(value_error("Provider campaign plan is empty or exceeds the usability limit"));
            }
        }
        Ok(Self {
            reads,
            provider,
            gm_system_prompt,
            campaign_plan_text,
            campaign_plan,
            campaign_knowledge,
            context_mode,
        })
    }

    /// `_ledger_active(provider_input)`: GM play on a ledger-mode route.
    pub fn ledger_active(&self, input: &SessionProviderInput) -> bool {
        self.context_mode == LEDGER_MODE
            && input.session.role() == WriterRole::Dm
            && input.session.campaign_id().is_some()
    }

    /// `self._campaign_plan if isinstance(self._campaign_plan, Mapping) else {}`.
    fn plan(&self) -> JsonMap {
        self.campaign_plan.clone().unwrap_or_default()
    }

    /// `_gm_context_owner(provider_input)`.
    pub(crate) async fn gm_context_owner(
        &self,
        input: &SessionProviderInput,
    ) -> CodedResult<Option<JsonMap>> {
        let Some(campaign_id) = input.session.campaign_id() else { return Ok(None) };
        let Some(mut declared) = self.reads.model_context_owner(campaign_id).await? else { return Ok(None) };
        declared.insert("session_id".into(), input.session.session_id().into());
        Ok(Some(declared))
    }

    /// `_participant_prompt_payloads(provider_input)`: only the roster identity needed to
    /// retrieve exact Character Files.
    pub(crate) async fn participant_prompt_payloads(
        &self,
        input: &SessionProviderInput,
    ) -> CodedResult<Vec<JsonMap>> {
        let Some(campaign_id) = input.session.campaign_id() else { return Ok(Vec::new()) };
        let snapshot = self.reads.snapshot(campaign_id).await?;
        let participants = &snapshot.state.participants;
        let party_roles =
            persisted_selected_party_roles(self.reads.as_ref(), campaign_id, participants).await?;
        let mut projected = Vec::new();
        for participant in participants {
            let data = participant.data();
            let participant_id = participant.participant_id();
            let role = match &party_roles {
                None => data.get("party_role").cloned().unwrap_or(Json::Null),
                Some(roles) => {
                    roles.get(participant_id).map_or(Json::Null, |role| Json::String(role.clone()))
                }
            };
            let mut facts = JsonMap::new();
            for (key, value) in [
                ("participant_id", Json::String(participant_id.into())),
                ("party_role", role),
                ("name", data.get("name").cloned().unwrap_or(Json::Null)),
                ("species", data.get("species").cloned().unwrap_or(Json::Null)),
                ("classes", data.get("classes").cloned().unwrap_or(Json::Null)),
            ] {
                if !value.is_null() {
                    facts.insert(key.into(), value);
                }
            }
            projected.push(facts);
        }
        Ok(projected)
    }

    /// `_opening_footing_payload(provider_input)`: player-known initial truth beside the
    /// one-time opening control.
    pub(crate) async fn opening_footing_payload(&self, input: &SessionProviderInput) -> CodedResult<Json> {
        let projected = self.participant_prompt_payloads(input).await?;
        let role = |item: &JsonMap| item.get("party_role").and_then(Json::as_str).map(str::to_owned);
        let player_character = projected
            .iter()
            .find(|item| role(item).as_deref() == Some("player_character"))
            .map_or(Json::Null, |item| Json::Object(item.clone()));
        let companions: Vec<Json> = projected
            .iter()
            .filter(|item| role(item).as_deref() == Some("companion"))
            .map(|item| Json::Object(item.clone()))
            .collect();
        let plan = self.plan();
        let location = match plan.get("starting_location") {
            Some(Json::Object(starting)) => Json::Object(
                starting
                    .iter()
                    .filter(|(key, value)| matches!(key.as_str(), "id" | "path") && !value.is_null())
                    .map(|(key, value)| (key.clone(), value.clone()))
                    .collect(),
            ),
            _ => Json::Null,
        };
        let Some(campaign_id) = input.session.campaign_id() else {
            return Err(rejected("DM campaign context requires campaign authority"));
        };
        let snapshot = self.reads.snapshot(campaign_id).await?;
        let mut initial: Vec<&JsonMap> = snapshot
            .continuity
            .facts
            .iter()
            .filter(|item| {
                item.get("origin").and_then(Json::as_str) == Some("accepted_initial_state")
                    && item.get("visibility").and_then(Json::as_str) == Some("player_known")
                    && item.get("text").is_some_and(Json::is_string)
            })
            .collect();
        // `sorted(..., key=str(record_id or ""))`, stable.
        initial.sort_by_key(|item| item.get("record_id").map_or_else(String::new, py_str));
        let initial_facts: Vec<Json> = initial.iter().map(|item| item["text"].clone()).collect();
        Ok(json!({
            "player_character": player_character,
            "present_companions": companions,
            "starting_location": location,
            "player_known_initial_facts": initial_facts,
            "first_player_choice_pending": true,
            "established_prior_player_actions": [],
            "established_shared_party_history": [],
        }))
    }
}

/// `str(value)` for a JSON value.
pub(crate) fn py_str(value: &Json) -> String {
    match value {
        Json::String(text) => text.clone(),
        other => pystr::repr_json(other),
    }
}

/// `_persisted_selected_party_roles(owner, campaign_id, participants)`: app-owned party roles
/// joined to the live roster by exact participant identity. `None` when construction has no
/// selected party (or the basis is refused).
pub(crate) async fn persisted_selected_party_roles(
    reads: &dyn OpeningReads,
    campaign_id: &str,
    participants: &[Participant],
) -> CodedResult<Option<HashMap<String, String>>> {
    let construction = match reads.campaign_construction_basis(campaign_id).await {
        Ok(construction) => construction,
        Err(error) if is_command_rejected(&error) => return Ok(None),
        Err(error) => return Err(error),
    };
    let canonical_input = &construction.canonical_input;
    if !canonical_input.contains_key("selected_party") {
        return Ok(None);
    }
    let Some(Json::Array(selected_party)) = canonical_input.get("selected_party") else {
        return Ok(Some(HashMap::new()));
    };
    let participant_ids: std::collections::HashSet<&str> =
        participants.iter().map(Participant::participant_id).collect();
    let mut roles = HashMap::new();
    let mut ambiguous = std::collections::HashSet::new();
    for item in selected_party {
        let Json::Object(item) = item else { continue };
        let (Some(Json::String(participant_id)), Some(Json::String(party_role))) =
            (item.get("participant_id"), item.get("party_role"))
        else {
            continue;
        };
        if !participant_ids.contains(participant_id.as_str())
            || !matches!(party_role.as_str(), "player_character" | "companion")
            || ambiguous.contains(participant_id)
        {
            continue;
        }
        if roles.remove(participant_id).is_some() {
            ambiguous.insert(participant_id.clone());
            continue;
        }
        roles.insert(participant_id.clone(), party_role.clone());
    }
    Ok(Some(roles))
}

/// `_canonical_player_narration(owner, campaign_id)`: the stable prose archive.
pub(crate) async fn canonical_player_narration(
    reads: &dyn OpeningReads,
    campaign_id: &str,
) -> CodedResult<Vec<JsonMap>> {
    let replay = reads.canonical_replay(campaign_id, 0).await?;
    let mut narration = Vec::new();
    for event in &replay.canonical {
        if event.event_type != CanonicalEventType::Talk || event.visibility != EventVisibility::PlayerSafe {
            continue;
        }
        let Some(Json::String(text)) = event.safe_payload.get("text") else { continue };
        if pystr::strip(text).is_empty() {
            continue;
        }
        let mut record = JsonMap::new();
        record.insert("canonical_sequence".into(), event.sequence.into());
        record.insert("turn_id".into(), event.turn_id.clone().into());
        record.insert("attempt_id".into(), event.attempt_id.clone().map_or(Json::Null, Json::String));
        record.insert("text".into(), text.clone().into());
        narration.push(record);
    }
    Ok(narration)
}

/// The items `for row in value` yields, as Python iterates a JSON value; `None` and numbers are
/// Python's `TypeError`.
fn iterated(value: Option<&Json>) -> CodedResult<Vec<Json>> {
    match value {
        None => Ok(Vec::new()),
        Some(Json::Array(items)) => Ok(items.clone()),
        Some(Json::String(text)) => Ok(text.chars().map(|c| Json::String(c.to_string())).collect()),
        Some(Json::Object(map)) => Ok(map.keys().map(|key| Json::String(key.clone())).collect()),
        Some(other) => Err(CodedError::new(
            "internal_error",
            format!(
                "'{}' object is not iterable",
                match other {
                    Json::Null => "NoneType",
                    Json::Bool(_) => "bool",
                    Json::Number(number) if number.is_f64() => "float",
                    _ => "int",
                }
            ),
        )
        .with_error_type("TypeError")),
    }
}

/// `_saved_action_sequence_results(provider_input, owner)`: immutable queue receipts belonging
/// only to this exact saved GM input.
pub(crate) async fn saved_action_sequence_results(
    input: &SessionProviderInput,
    reads: &dyn OpeningReads,
) -> CodedResult<Vec<JsonMap>> {
    if input.session.role() != WriterRole::Dm || input.session.campaign_id().is_none() {
        return Ok(Vec::new());
    }
    let basis_id = match &input.basis_message_id {
        Some(basis) => Some(basis.clone()),
        None => input
            .history
            .messages
            .iter()
            .rev()
            .find(|message| {
                message.kind() == SessionMessageKind::User
                    || (message.kind() == SessionMessageKind::Evidence
                        && matches!(
                            message.content().get("kind").and_then(Json::as_str),
                            Some("campaign_start" | "player_action_response")
                        ))
            })
            .map(|message| message.message_id().to_owned()),
    };
    let mut results = Vec::new();
    for message in &input.history.messages {
        let content = message.content();
        if message.kind() != SessionMessageKind::Evidence
            || content.get("kind").and_then(Json::as_str) != Some("provider_tool_results")
            || content.get("basis_message_id").cloned().unwrap_or(Json::Null)
                != basis_id.clone().map_or(Json::Null, Json::String)
        {
            continue;
        }
        for row in iterated(content.get("results"))? {
            let Some(Json::String(text)) = row.get("content") else { continue };
            let Ok(Json::Object(payload)) = pyjson::loads(text) else { continue };
            if payload.get("kind").and_then(Json::as_str) == Some("action_sequence_result")
                && payload.get("operation").and_then(Json::as_str) == Some("execute_action_sequence")
            {
                if let Some(Json::Object(result)) = payload.get("result") {
                    let mut merged = result.clone();
                    let stopped = payload.get("execution_stopped") == Some(&Json::Bool(true))
                        || input.recovery_kind.as_deref() == Some("post_mechanics_narration");
                    merged.insert("provider_execution_stopped".into(), stopped.into());
                    results.push(merged);
                }
            }
        }
    }
    if results.is_empty() {
        if let Some(basis_id) = &basis_id {
            for mut result in
                reads.action_sequence_response_results(input.session.session_id(), basis_id).await?
            {
                result.insert("provider_execution_stopped".into(), true.into());
                results.push(result);
            }
        }
    }
    Ok(results)
}

/// `str.splitlines()` boundaries.
pub(crate) fn splitlines(text: &str) -> Vec<&str> {
    let mut lines = Vec::new();
    let mut start = 0;
    let mut characters = text.char_indices().peekable();
    while let Some((index, character)) = characters.next() {
        let boundary = matches!(
            character,
            '\n' | '\r'
                | '\u{0b}'
                | '\u{0c}'
                | '\u{1c}'
                | '\u{1d}'
                | '\u{1e}'
                | '\u{85}'
                | '\u{2028}'
                | '\u{2029}'
        );
        if !boundary {
            continue;
        }
        lines.push(&text[start..index]);
        let mut end = index + character.len_utf8();
        if character == '\r' {
            if let Some((next, '\n')) = characters.peek().copied() {
                characters.next();
                end = next + 1;
            }
        }
        start = end;
    }
    if start < text.len() {
        lines.push(&text[start..]);
    }
    lines
}

/// `_bounded_reference_excerpt(value, limit=...)`: a paragraph-bounded leading excerpt and
/// whether it is the whole source. `limit` counts code points.
pub(crate) fn bounded_reference_excerpt(value: &str, limit: usize) -> (String, bool) {
    let text: Vec<char> = pystr::strip(value).chars().collect();
    if text.len() <= limit {
        return (text.iter().collect(), true);
    }
    // `text.rfind("\n\n", 0, limit + 1)`: the last pair that ends within the first limit+1.
    let window = (limit + 1).min(text.len());
    let found = (0..window.saturating_sub(1)).rev().find(|&i| text[i] == '\n' && text[i + 1] == '\n');
    let boundary = match found {
        Some(index) if index >= limit / 2 => index,
        _ => limit,
    };
    let head: String = text[..boundary].iter().collect();
    let mut excerpt = pystr::rstrip(&head).to_owned();
    let last_line = excerpt.rsplit_once('\n').map_or(excerpt.as_str(), |(_, last)| last);
    if pystr::lstrip(last_line).starts_with('#') {
        let before = excerpt.rsplit_once('\n').map_or(excerpt.as_str(), |(before, _)| before);
        excerpt = pystr::rstrip(before).to_owned();
    }
    (excerpt, false)
}

/// `_bounded_markdown_section(value, heading=..., limit=...)`: one Markdown section's body.
pub(crate) fn bounded_markdown_section(value: &str, heading: &str, limit: usize) -> Option<(String, bool)> {
    let lines = splitlines(pystr::strip(value));
    let target = pystr::casefold(&format!("## {heading}"));
    let start = lines.iter().position(|line| pystr::casefold(pystr::strip(line)) == target)?;
    let end = (start + 1..lines.len())
        .find(|&index| {
            let line = pystr::lstrip(lines[index]);
            line.starts_with("# ") || line.starts_with("## ")
        })
        .unwrap_or(lines.len());
    let body = lines[start + 1..end].join("\n");
    let body = pystr::strip(&body);
    if body.is_empty() {
        return None;
    }
    Some(bounded_reference_excerpt(body, limit))
}

impl ProviderSessionAdapter {
    /// `_current_campaign_context_payload(provider_input, include_opening_direction=...)`:
    /// bounded current Campaign State apart from private Plan preparation. Also returns the
    /// active record ids the fresh-state message carries as a (host-only) tag.
    pub(crate) async fn current_campaign_context_payload(
        &self,
        input: &SessionProviderInput,
        include_opening_direction: bool,
        rank: &(dyn Fn(&str, &[KnowledgeReference]) -> Vec<JsonMap> + Sync),
    ) -> CodedResult<JsonMap> {
        let Some(campaign_id) = input.session.campaign_id() else {
            return Err(rejected("DM campaign context requires campaign authority"));
        };
        let (snapshot, private_decision) = self.reads.model_context_snapshot(campaign_id).await?;
        let continuity = &snapshot.continuity;
        let plan = self.plan();
        let acts: Vec<JsonMap> = match plan.get("structure") {
            Some(Json::Array(items)) => items.iter().filter_map(|item| item.as_object().cloned()).collect(),
            _ => Vec::new(),
        };
        let current_scene = continuity.current_scene.clone().unwrap_or_default();
        let current_act_id = current_scene.get("act_id").and_then(Json::as_str);
        let current_act = acts
            .iter()
            .find(|act| current_act_id.is_some_and(|id| act.get("id").and_then(Json::as_str) == Some(id)))
            .or_else(|| acts.first());
        let objective_ids: std::collections::HashSet<&str> = match plan.get("objectives") {
            Some(Json::Array(items)) => {
                items.iter().filter_map(|item| item.as_object()?.get("id")?.as_str()).collect()
            }
            _ => std::collections::HashSet::new(),
        };
        let prepared_acts: Vec<Json> = current_act
            .map(|act| {
                let id = act.get("id");
                let reference = match id.and_then(Json::as_str) {
                    Some(id) => json!({"source": "campaign_plan", "path": format!("structured/structure/{id}")}),
                    None => Json::Null,
                };
                let objective_references: Vec<Json> = act
                    .get("objectives")
                    .and_then(Json::as_array)
                    .map(|items| {
                        items
                            .iter()
                            .filter_map(Json::as_str)
                            .filter(|item| objective_ids.contains(item))
                            .map(|item| json!({"source": "campaign_plan", "path": format!("structured/objectives/{item}")}))
                            .collect()
                    })
                    .unwrap_or_default();
                vec![json!({
                    "id": id.cloned().unwrap_or(Json::Null),
                    "title": act.get("title").cloned().unwrap_or(Json::Null),
                    "reference": reference,
                    "objective_references": objective_references,
                    "detail_handling": concat!(
                        "Read this act before framing its main quest or judging its ending ",
                        "if its exact requirements are absent from context. The act read includes ",
                        "all linked objectives and their completion conditions. Reassess them ",
                        "when a required discovery, quest outcome or closing event occurs. ",
                        "These references are navigation, not source contents. ",
                        "Do not treat its unqueried details ",
                        "as present-world facts or use them to expand ordinary observation."
                    ),
                })]
            })
            .unwrap_or_default();
        let get = |key: &str| plan.get(key).cloned().unwrap_or(Json::Null);
        let mut plan_preparation = JsonMap::new();
        plan_preparation.insert(
            "meaning".into(),
            concat!(
                "Private GM preparation and desired possibilities, not current fiction or a ",
                "required scene order. Interpret naturally in response to play."
            )
            .into(),
        );
        for key in ["id", "title", "premise", "tone", "intended_player_experience", "gm_guidance"] {
            plan_preparation.insert(key.into(), get(key));
        }
        plan_preparation
            .insert("boundaries".into(), plan.get("boundaries").cloned().unwrap_or_else(|| json!([])));
        plan_preparation.insert("prepared_acts".into(), Json::Array(prepared_acts));
        plan_preparation.insert(
            "additional_material_available_via".into(),
            json!({"source": "campaign_plan", "path": "structured"}),
        );
        if let Some(continuation @ Json::Object(_)) = plan.get("_app_adventure_continuation") {
            plan_preparation.insert("adventure_continuation".into(), continuation.clone());
        }
        if include_opening_direction && plan.get("opening_direction").is_some_and(Json::is_string) {
            plan_preparation.insert("opening_direction".into(), get("opening_direction"));
            plan_preparation.insert(
                "opening_direction_meaning".into(),
                concat!(
                    "Creative framing for this opening exchange only. Interpret how to ",
                    "achieve it from current truth; it is not factual or mechanical authority."
                )
                .into(),
            );
        }
        if include_opening_direction && plan.get("starting_location").is_some_and(|value| !value.is_null()) {
            plan_preparation.insert("starting_location".into(), get("starting_location"));
        }
        if plan.is_empty() {
            if let Some(text) = &self.campaign_plan_text {
                plan_preparation
                    .insert("legacy_markdown".into(), text.chars().take(16_000).collect::<String>().into());
            }
        }

        let latest_player_input =
            input.history.messages.iter().rev().find(|message| message.kind() == SessionMessageKind::User);
        let empty = JsonMap::new();
        let player_content = latest_player_input.map_or(&empty, |message| message.content());
        // `str(player_content.get("text", ""))`, `str(... or "")`.
        let truthy_str = |value: Option<&Json>| match value {
            None | Some(Json::Null) | Some(Json::Bool(false)) => String::new(),
            Some(Json::String(text)) if text.is_empty() => String::new(),
            Some(Json::Number(number)) if number.as_f64() == Some(0.0) => String::new(),
            Some(Json::Array(items)) if items.is_empty() => String::new(),
            Some(Json::Object(map)) if map.is_empty() => String::new(),
            Some(other) => py_str(other),
        };
        let queries = [
            player_content.get("text").map_or_else(String::new, py_str),
            truthy_str(player_content.get("ooc_text")),
            truthy_str(current_scene.get("location_id")),
            truthy_str(current_scene.get("summary")),
        ];
        let (current_facts, memory_selection) =
            campaign_memory::select_with(&continuity.facts, &queries, rank);
        let mut player_known = Vec::new();
        let mut gm_private_facts = Vec::new();
        let mut character_knowledge: JsonMap = JsonMap::new();
        let mut know = |identity: &str, entry: Json| {
            let list =
                character_knowledge.entry(identity.to_owned()).or_insert_with(|| Json::Array(Vec::new()));
            if let Json::Array(items) = list {
                items.push(entry);
            }
        };
        for fact in &current_facts {
            if fact.get("visibility").and_then(Json::as_str) == Some("player_known") {
                player_known.push(Json::Object(fact.clone()));
            } else {
                gm_private_facts.push(Json::Object(fact.clone()));
            }
            let known_by = fact.get("known_by").cloned().unwrap_or_else(|| json!([]));
            if let (Some(Json::String(text)), Json::Array(known_by)) = (fact.get("text"), &known_by) {
                for identity in known_by {
                    if let Some(identity) = identity.as_str().filter(|identity| *identity != "player") {
                        let entry = match fact.get("record_id") {
                            Some(Json::String(record_id)) => format!("campaign_state/{record_id}"),
                            _ => text.clone(),
                        };
                        know(identity, entry.into());
                    }
                }
            }
        }

        let knowledge = match &self.campaign_knowledge {
            Some(knowledge) => knowledge.clone(),
            None if !plan.is_empty() => {
                self.reads.campaign_knowledge_catalog(input.session.session_id()).await?
            }
            None => {
                let mut fallback = JsonMap::new();
                fallback.insert("secrets".into(), plan.get("secrets").cloned().unwrap_or_else(|| json!([])));
                fallback.insert("private_setting_references".into(), json!([]));
                fallback
            }
        };
        let reveals: JsonMap = continuity
            .reveals
            .iter()
            .filter(|(_, audience)| audience.is_array())
            .map(|(secret_id, audience)| (secret_id.clone(), audience.clone()))
            .collect();
        let mut prepared_secrets = Vec::new();
        let mut player_revealed_facts = Vec::new();
        if let Some(Json::Array(raw_secrets)) = knowledge.get("secrets") {
            for (index, item) in raw_secrets.iter().take(128).enumerate() {
                let secret: JsonMap = match item {
                    Json::String(fact) => {
                        let mut legacy = JsonMap::new();
                        legacy.insert("id".into(), format!("legacy-secret-{index}").into());
                        legacy.insert("fact".into(), fact.clone().into());
                        legacy
                    }
                    Json::Object(map) => map.clone(),
                    _ => continue,
                };
                let secret_id = secret.get("id").map_or_else(|| format!("secret-{index}"), py_str);
                let audiences = reveals.get(&secret_id).and_then(Json::as_array).cloned().unwrap_or_default();
                let player = audiences.iter().any(|audience| audience.as_str() == Some("player"));
                if let Some(Json::String(fact)) = secret.get("fact") {
                    if player {
                        player_revealed_facts.push(json!({"secret_id": secret_id, "fact": fact}));
                    }
                    for identity in &audiences {
                        if let Some(identity) = identity.as_str().filter(|identity| *identity != "player") {
                            know(identity, fact.clone().into());
                        }
                    }
                }
                let mut prepared = secret.clone();
                prepared.insert("revealed_to".into(), Json::Array(audiences));
                prepared.insert("player_known".into(), player.into());
                prepared_secrets.push(Json::Object(prepared));
            }
        }

        let mut setting_sources = Vec::new();
        if include_opening_direction {
            let setting_binding = plan.get("bindings").and_then(|bindings| bindings.get("campaign_setting"));
            let starting_location = plan.get("starting_location");
            if let (Some(Json::Object(binding)), Some(Json::Object(location))) =
                (setting_binding, starting_location)
            {
                if let Some(Json::String(path)) = location.get("path") {
                    // `str(setting_binding["id"])`: a missing key is Python's `KeyError`.
                    let field = |key: &str| {
                        binding.get(key).map(py_str).ok_or_else(|| {
                            CodedError::new("internal_error", pystr::repr(key)).with_error_type("KeyError")
                        })
                    };
                    let (id, version, sha256) = (field("id")?, field("version")?, field("content_sha256")?);
                    let source = self
                        .reads
                        .package_text_file(&id, &version, &sha256, path, DEFAULT_MAXIMUM_BYTES)
                        .await?;
                    let (excerpt, complete, selection) = match bounded_markdown_section(
                        &source,
                        "Overview",
                        MAX_SETTING_SOURCE_EXCERPT_CHARS,
                    ) {
                        None => {
                            let (excerpt, complete) =
                                bounded_reference_excerpt(&source, MAX_SETTING_SOURCE_EXCERPT_CHARS);
                            (excerpt, complete, None)
                        }
                        Some((excerpt, section_complete)) => (
                            excerpt,
                            false,
                            Some(
                                json!({"kind": "markdown_section", "heading": "Overview", "complete": section_complete}),
                            ),
                        ),
                    };
                    let content_complete = selection
                        .as_ref()
                        .map_or(complete, |selection| selection["complete"].as_bool() == Some(true));
                    let mut entry = JsonMap::new();
                    entry.insert("package".into(), Json::Object(binding.clone()));
                    entry.insert("path".into(), path.clone().into());
                    entry.insert("content".into(), excerpt.into());
                    entry.insert("content_complete".into(), content_complete.into());
                    entry.insert("document_complete".into(), complete.into());
                    entry.insert("purpose".into(), "starting-location overview for opening texture".into());
                    entry.insert(
                        "interpretation".into(),
                        concat!(
                            "Reusable Setting baseline, not current Campaign State. Examples ",
                            "describe what can occur; they do not establish a current object, ",
                            "route, event, or prior action."
                        )
                        .into(),
                    );
                    entry.insert(
                        "reference".into(),
                        json!({"source": "campaign_setting", "path": format!("{path}#overview")}),
                    );
                    entry.insert("additional_sections_discoverable".into(), true.into());
                    if let Some(selection) = selection {
                        entry.insert("selection".into(), selection);
                    }
                    setting_sources.push(Json::Object(entry));
                }
            }
        }

        let state = &snapshot.state;
        let mut payload = JsonMap::new();
        payload.insert(
            "campaign".into(),
            json!({"campaign_id": state.campaign_id, "display_name": state.display_name, "revision": state.revision}),
        );
        if let Some(decision) = &private_decision {
            let eligible: Vec<Json> = decision
                .eligible_actors
                .iter()
                .map(|actor| json!({"actor_id": actor.actor_id, "actor_name": actor.actor_name, "actor_state": actor.actor_state}))
                .collect();
            payload.insert(
                "ruleset_decision".into(),
                json!({
                    "owner": decision.owner.as_str(),
                    "actor_id": decision.actor_id,
                    "actor_name": decision.actor_name,
                    "actor_state": decision.actor_state,
                    "active_side_id": decision.active_side_id,
                    "focused_actor_id": decision.focused_actor_id,
                    "eligible_actors": eligible,
                    "campaign_revision": decision.campaign_revision,
                    "handling": concat!(
                        "Use only this permitted actor view for the current decision. ",
                        "Keep information concealed from the player out of public narration."
                    ),
                }),
            );
        }
        payload.insert(
            "campaign_state".into(),
            json!({
                "meaning": "Current campaign truth and durable GM-screen memory.",
                "current_scene": continuity.current_scene.clone().map_or(Json::Null, Json::Object),
                "reveals": reveals,
                "plan_progress": continuity.plan_progress,
                "ruleset_owned_values": state.state,
                "memory_selection": memory_selection,
                "character_knowledge_meaning": concat!(
                    "campaign_state/<record_id> entries refer to selected facts above; ",
                    "their known_by audiences define who knows them. Other entries are ",
                    "explicitly revealed facts. These lists contain only selected memory."
                ),
                "more_state_available_via": {
                    "tool": "search_campaign_knowledge",
                    "sources": ["campaign_state"],
                    "limit": 5,
                    "query": "A known name, subject, relationship or event to look up.",
                    "then": "read_campaign_reference(source='campaign_state', path=<match path>)",
                    "whole_state_read": "path='current' is size-bounded and may be too large; prefer exact records.",
                },
            }),
        );
        payload.insert("campaign_plan_preparation".into(), Json::Object(plan_preparation));
        payload.insert(
            "participants".into(),
            Json::Array(
                self.participant_prompt_payloads(input).await?.into_iter().map(Json::Object).collect(),
            ),
        );
        let mut revealed_ids: Vec<&String> = reveals
            .iter()
            .filter(|(_, audiences)| {
                audiences
                    .as_array()
                    .is_some_and(|items| items.iter().any(|item| item.as_str() == Some("player")))
            })
            .map(|(key, _)| key)
            .collect();
        revealed_ids.sort();
        payload.insert(
            "PLAYER_KNOWN".into(),
            json!({"current_facts": player_known, "revealed_facts": player_revealed_facts, "revealed_secret_ids": revealed_ids}),
        );
        payload.insert(
            "GM_PRIVATE".into(),
            json!({
                "current_facts": gm_private_facts,
                "prepared_secrets": prepared_secrets,
                "character_knowledge": character_knowledge,
                "private_setting_references": knowledge.get("private_setting_references").cloned().unwrap_or_else(|| json!([])),
            }),
        );
        payload.insert("campaign_setting_references".into(), Json::Array(setting_sources));
        Ok(payload)
    }

    /// `_campaign_context_messages(provider_input, include_opening_direction=...)`: cacheable
    /// campaign explainers apart from fresh authority near the decision.
    pub(crate) async fn campaign_context_messages(
        &self,
        input: &SessionProviderInput,
        include_opening_direction: bool,
        rank: &(dyn Fn(&str, &[KnowledgeReference]) -> Vec<JsonMap> + Sync),
    ) -> CodedResult<(JsonMap, JsonMap)> {
        let payload = self.current_campaign_context_payload(input, include_opening_direction, rank).await?;
        let empty = JsonMap::new();
        let campaign = payload.get("campaign").and_then(Json::as_object).unwrap_or(&empty);
        let gm_private = payload.get("GM_PRIVATE").and_then(Json::as_object).unwrap_or(&empty);
        let get = |map: &JsonMap, key: &str| map.get(key).cloned().unwrap_or(Json::Null);
        let explainers = json!({
            "campaign": {"campaign_id": get(campaign, "campaign_id"), "display_name": get(campaign, "display_name")},
            "campaign_plan_preparation": get(&payload, "campaign_plan_preparation"),
            "participants": get(&payload, "participants"),
            "campaign_setting_references": get(&payload, "campaign_setting_references"),
            "private_preparation": {
                "additional_material_available_via": {"source": "campaign_plan", "path": "structured"},
                "private_setting_references": gm_private.get("private_setting_references").cloned().unwrap_or_else(|| json!([])),
            },
        });
        let mut fresh = JsonMap::new();
        fresh.insert("campaign".into(), Json::Object(campaign.clone()));
        if let Some(decision) = payload.get("ruleset_decision") {
            fresh.insert("ruleset_decision".into(), decision.clone());
        }
        fresh.insert("campaign_state".into(), get(&payload, "campaign_state"));
        fresh.insert("PLAYER_KNOWN".into(), get(&payload, "PLAYER_KNOWN"));
        fresh.insert(
            "GM_PRIVATE_CURRENT".into(),
            json!({
                "character_knowledge": gm_private.get("character_knowledge").cloned().unwrap_or_else(|| json!({})),
                "current_facts": gm_private.get("current_facts").cloned().unwrap_or_else(|| json!([])),
            }),
        );
        let preparation = super::texts::system(format!(
            "SYLVIA_GM_CAMPAIGN_PREPARATION (application-owned campaign data, not player speech):\n{}",
            pyjson::dumps(&explainers, Dump::CANONICAL)
        ));
        let fresh_message = super::texts::system(format!(
            "SYLVIA_GM_FRESH_CAMPAIGN_STATE (application-owned current data, not player speech):\n{}",
            pyjson::dumps(&Json::Object(fresh), Dump::CANONICAL)
        ));
        Ok((preparation, fresh_message))
    }
}
