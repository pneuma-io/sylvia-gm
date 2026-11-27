//! `AuthorityOwner.update_campaign_file`: one all-or-nothing batch of GM-owned Campaign File
//! notes (lane 4's Campaign File tool), with `_continuity_turn`.
//!
//! Under the session's campaign work (`_session_campaign_work`), on the store thread: the DM
//! context and optional Plan, the batch's identities and provenance, the open TURN, the fallback
//! inventory (`campaign_inventory::prepare_inventory_payload`) or the Ruleset's character
//! bookkeeping, and each note (memory, entity, scene, reveal, Plan Progress) checked and turned
//! into a continuity command, unchanged notes left out. Then either the bookkeeping action, run
//! through `_execute_action` with the notes committed in its transaction, or the notes alone
//! through `commit_campaign_continuity_batch`.

use super::actions::{execute_action_in_work, ActionError, ActionExecution, ExecuteOptions};
use std::collections::{BTreeMap, BTreeSet};
use sylvia_base::hash::sha256_hex;
use sylvia_base::{pystr, Json, JsonMap};
use sylvia_domain::authority::{
    owned_payload, validate_authority_identity, validate_authority_identity_value, ActionCommand,
    ActionStatus, CampaignContinuityCommand, CampaignSnapshot, TurnStatus, WriterRole,
};
use sylvia_domain::campaign_ending::{check_ending_progress, ending_progress_id, intended_endings};
use sylvia_domain::inventory::{INVENTORY_ENTITY_KIND, INVENTORY_RECORD_ID};
use sylvia_domain::package_library::canonical_json_bytes;
use sylvia_domain::pyops::py_eq;
use sylvia_domain::DomainError;
use sylvia_store::{AuthorityStore, StoreError, StoreHandle};

/// `update_campaign_file(session_id=..., update_id=..., updates=..., basis_message_ids=...,
/// turn_id=...)`'s arguments.
#[derive(Debug, Clone, PartialEq)]
pub struct CampaignFileUpdate {
    pub session_id: String,
    pub update_id: String,
    pub updates: Vec<JsonMap>,
    pub basis_message_ids: Vec<String>,
    pub turn_id: Option<String>,
}

/// A failure on the store thread: Python's exception unchanged, or a store failure.
enum Failure {
    Domain(DomainError),
    Store(StoreError),
}

impl From<DomainError> for Failure {
    fn from(error: DomainError) -> Self {
        Self::Domain(error)
    }
}

impl From<StoreError> for Failure {
    fn from(error: StoreError) -> Self {
        Self::Store(error)
    }
}

impl From<Failure> for ActionError {
    fn from(failure: Failure) -> Self {
        match failure {
            Failure::Domain(error) => error.into(),
            Failure::Store(error) => error.into(),
        }
    }
}

fn rejected(message: &str) -> DomainError {
    DomainError::command_rejected(message)
}

fn rejected_with(message: &str, code: &str) -> DomainError {
    DomainError::command_rejected_with(message, code, Vec::new())
}

fn value(message: &str) -> DomainError {
    DomainError::value(message)
}

/// What the store-thread half decided.
enum Stage {
    /// Nothing changes: the answer.
    Unchanged(JsonMap),
    /// The Ruleset's bookkeeping action, with the notes it commits.
    Character {
        command: Box<ActionCommand>,
        commands: Vec<CampaignContinuityCommand>,
        character_updates: Vec<Json>,
    },
    /// The notes alone; `inventory` when the fallback inventory is among them.
    Batch { commands: Vec<CampaignContinuityCommand>, inventory: bool },
}

/// `AuthorityOwner.update_campaign_file(...)`.
pub async fn update_campaign_file(
    store: &StoreHandle,
    owner_id: &str,
    update: &CampaignFileUpdate,
) -> Result<JsonMap, ActionError> {
    let _work = store.session_campaign_work(&update.session_id).await?;
    let (owned, work, owner) = (update.clone(), store.lanes().work.clone(), owner_id.to_owned());
    let stage = store.call(move |store| Ok(prepare(store, &work, &owner, &owned))).await?;
    let stage = stage.map_err(ActionError::from)?;
    match stage {
        Stage::Unchanged(answer) => Ok(answer),
        Stage::Character { command, commands, character_updates } => {
            let options = ExecuteOptions {
                campaign_file_commands: commands.clone(),
                campaign_file_update: true,
                player_action_origin: None,
            };
            let receipt = match execute_action_in_work(store, owner_id, &command, &options).await {
                Ok(receipt) => receipt,
                // `except (OSError, sqlite3.Error)`.
                Err(ActionError::Store(StoreError::Sqlite(_))) => {
                    return Err(rejected_with(
                        "Character bookkeeping could not be saved; nothing was changed",
                        "campaign_file_save_failed",
                    )
                    .into())
                }
                Err(error) => return Err(error),
            };
            let ActionExecution::Receipt(receipt) = receipt else {
                return Err(rejected("Character bookkeeping was not committed").into());
            };
            if receipt.status != ActionStatus::Committed {
                return Err(rejected("Character bookkeeping was not committed").into());
            }
            let count = i64::try_from(commands.len()).unwrap_or(i64::MAX);
            let mut answer = JsonMap::new();
            answer.insert("status".into(), "campaign_file_updated".into());
            answer.insert("operation_id".into(), update.update_id.as_str().into());
            answer.insert(
                "change_count".into(),
                (i64::try_from(character_updates.len()).unwrap_or(i64::MAX) + count).into(),
            );
            answer.insert("character_updates".into(), Json::Array(character_updates));
            let resulting = receipt
                .resulting_revision
                .checked_add(count)
                .ok_or_else(|| ActionError::Store(StoreError::Other("campaign revision overflow".into())))?;
            answer.insert("resulting_revision".into(), resulting.into());
            Ok(owned_payload(&Json::Object(answer))?)
        }
        Stage::Batch { commands, inventory } => {
            let receipts =
                match store.call(move |store| store.commit_campaign_continuity_batch(&commands)).await {
                    Ok(receipts) => receipts,
                    Err(StoreError::Sqlite(_)) if inventory => {
                        return Err(rejected_with(
                            "Inventory and campaign notes could not be saved; nothing was changed",
                            "campaign_file_save_failed",
                        )
                        .into())
                    }
                    Err(error) => return Err(error.into()),
                };
            let changes: Vec<Json> = receipts
                .iter()
                .map(|receipt| {
                    let mut change = JsonMap::new();
                    change.insert("operation_id".into(), receipt.operation_id.as_str().into());
                    change.insert("record_id".into(), receipt.record_id.as_str().into());
                    change.insert("kind".into(), receipt.kind.as_str().into());
                    change.insert("resulting_revision".into(), receipt.resulting_revision.into());
                    Json::Object(change)
                })
                .collect();
            let mut answer = JsonMap::new();
            answer.insert("status".into(), "campaign_file_updated".into());
            answer.insert("operation_id".into(), update.update_id.as_str().into());
            answer.insert("change_count".into(), receipts.len().into());
            answer.insert("changes".into(), Json::Array(changes));
            answer.insert(
                "resulting_revision".into(),
                receipts.last().map_or(Json::Null, |receipt| receipt.resulting_revision.into()),
            );
            Ok(owned_payload(&Json::Object(answer))?)
        }
    }
}

/// `_continuity_turn(snapshot, requested_turn_id)`: the one open TURN a continuity admission
/// belongs to.
fn continuity_turn(snapshot: &CampaignSnapshot, requested: Option<&str>) -> Result<String, DomainError> {
    if let Some(requested) = requested {
        validate_authority_identity(requested, "Campaign continuity turn", false)?;
        let open =
            snapshot.turns.iter().any(|(turn, status)| turn == requested && *status == TurnStatus::Open);
        if !open {
            return Err(rejected("Campaign continuity TURN is not open"));
        }
        return Ok(requested.to_owned());
    }
    let open: Vec<&String> = snapshot
        .turns
        .iter()
        .filter(|(_, status)| *status == TurnStatus::Open)
        .map(|(turn, _)| turn)
        .collect();
    match open.as_slice() {
        [turn] => Ok((*turn).clone()),
        _ => Err(rejected("Campaign continuity requires exactly one open TURN")),
    }
}

/// `canonical_json_bytes(...)`'s SHA-256, as hex.
fn digest(value: &Json) -> String {
    sha256_hex(canonical_json_bytes(value))
}

fn texts(values: &[String]) -> Json {
    Json::Array(values.iter().cloned().map(Json::String).collect())
}

/// A list of distinct strings, as Python's `Sequence` (not text), `len(set(x)) == len(x)` and the
/// identity checks read it; `None` when any of those refuses it.
fn identity_list(value: Option<&Json>) -> Option<Vec<String>> {
    let Some(Json::Array(items)) = value else { return None };
    let mut seen = BTreeSet::new();
    let mut out = Vec::with_capacity(items.len());
    for item in items {
        let Json::String(text) = item else { return None };
        if !seen.insert(text.clone()) {
            return None;
        }
        out.push(text.clone());
    }
    Some(out)
}

/// Bounded note text: nonempty, already stripped, at most `limit` characters.
fn note_text(value: Option<&Json>, limit: usize) -> Option<&str> {
    match value {
        Some(Json::String(text))
            if !text.is_empty() && pystr::strip(text) == text && pystr::len(text) <= limit =>
        {
            Some(text)
        }
        _ => None,
    }
}

fn keys_are(update: &JsonMap, required: &[&str], optional: &[&[&str]]) -> bool {
    let keys: BTreeSet<&str> = update.keys().map(String::as_str).collect();
    let base: BTreeSet<&str> = required.iter().copied().collect();
    if keys == base {
        return true;
    }
    optional.iter().any(|extra| {
        let mut set = base.clone();
        set.extend(extra.iter().copied());
        keys == set
    })
}

/// The `id`s of a plan collection's mappings, if it is a list.
fn collection_ids(value: Option<&Json>) -> Vec<String> {
    match value {
        Some(Json::Array(items)) => items
            .iter()
            .filter_map(|item| match item.get("id") {
                Some(Json::String(id)) if item.is_object() => Some(id.clone()),
                _ => None,
            })
            .collect(),
        _ => Vec::new(),
    }
}

/// Whether an audience value (`Sequence`, not text) names the player.
fn names_player(value: &Json) -> bool {
    matches!(value, Json::Array(items) if items.iter().any(|item| item.as_str() == Some("player")))
}

/// Everything `update_campaign_file` decides before it writes, on the store thread.
#[allow(clippy::too_many_lines)]
fn prepare(
    store: &AuthorityStore,
    work: &sylvia_store::work::CampaignWork,
    owner_id: &str,
    request: &CampaignFileUpdate,
) -> Result<Stage, Failure> {
    let (context, plan) = super::content_reads::optional_dm_structured_plan(store, &request.session_id)?;
    let all_updates = &request.updates;
    let operation = |item: &JsonMap| item.get("operation").and_then(Json::as_str).map(str::to_owned);
    let character_updates: Vec<&JsonMap> =
        all_updates.iter().filter(|item| operation(item).as_deref() == Some("update_character")).collect();
    let inventory_updates: Vec<&JsonMap> =
        all_updates.iter().filter(|item| operation(item).as_deref() == Some("update_inventory")).collect();
    if all_updates.iter().any(|item| operation(item).as_deref() == Some("prepared_inventory")) {
        return Err(rejected("Internal inventory operations cannot be submitted").into());
    }
    let mut admitted: Vec<JsonMap> = all_updates
        .iter()
        .filter(|item| !matches!(operation(item).as_deref(), Some("update_character" | "update_inventory")))
        .cloned()
        .collect();
    let invalid = || rejected_with("Campaign File update is invalid", "campaign_file_update_invalid");
    validate_authority_identity(&request.update_id, "Campaign File update", false).map_err(|_| invalid())?;
    if all_updates.is_empty() || all_updates.len() > 64 {
        return Err(invalid().into());
    }
    let unique: BTreeSet<&String> = request.basis_message_ids.iter().collect();
    if request.basis_message_ids.is_empty() || unique.len() != request.basis_message_ids.len() {
        return Err(invalid().into());
    }
    for message_id in &request.basis_message_ids {
        validate_authority_identity(message_id, "Campaign File provenance", false).map_err(|_| invalid())?;
    }
    let known: BTreeSet<&str> = context.messages.iter().map(|message| message.message_id()).collect();
    if !request.basis_message_ids.iter().all(|id| known.contains(id.as_str())) {
        return Err(rejected_with(
            "Campaign File provenance is unavailable",
            "campaign_file_provenance_unavailable",
        )
        .into());
    }
    let campaign_id = context.session.campaign_id().expect("an active DM session has a campaign").to_owned();
    let snapshot = store.snapshot(&campaign_id)?;
    let resolved_turn_id = continuity_turn(&snapshot, request.turn_id.as_deref())?;
    let mut inventory_revision = snapshot.state.revision;
    let all_json = Json::Array(all_updates.iter().cloned().map(Json::Object).collect());
    if !inventory_updates.is_empty() {
        work.ensure_not_transitioning(&campaign_id)?;
        if super::turn_reads::inventory_tool(store, &campaign_id)?.is_some() {
            return Err(rejected("Use the active Ruleset inventory; fallback is unavailable").into());
        }
        let mut request_body = JsonMap::new();
        request_body.insert("updates".into(), all_json.clone());
        request_body.insert("session_id".into(), request.session_id.as_str().into());
        request_body.insert("basis_message_ids".into(), texts(&request.basis_message_ids));
        let pending: Vec<&JsonMap> =
            admitted.iter().filter(|item| operation(item).as_deref() == Some("record_entity")).collect();
        let (revision, payload) = crate::campaign_inventory::prepare_inventory_payload(
            &snapshot,
            &inventory_updates,
            &format!("{}-1", request.update_id),
            &digest(&Json::Object(request_body)),
            &pending,
        )?;
        inventory_revision = revision;
        let mut prepared = JsonMap::new();
        prepared.insert("operation".into(), "prepared_inventory".into());
        prepared.insert("payload".into(), Json::Object(payload));
        admitted.insert(0, prepared);
    }
    let mut character_command: Option<ActionCommand> = None;
    let mut character_answers: Vec<Json> = Vec::new();
    if let Some(first) = character_updates.first() {
        work.ensure_not_transitioning(&campaign_id)?;
        let Some(tool) = super::turn_reads::character_update_tool(store, &campaign_id)? else {
            return Err(rejected("Character bookkeeping is unavailable").into());
        };
        // `type(expected_revision) is not int or expected_revision < 0`.
        let revision = first.get("expected_revision").filter(|value| value.is_i64()).and_then(Json::as_i64);
        let Some(expected_revision) = revision.filter(|revision| *revision >= 0) else {
            return Err(rejected("Character update revision is invalid").into());
        };
        if !inventory_updates.is_empty() && expected_revision != inventory_revision {
            return Err(rejected("Character and inventory updates must share one revision").into());
        }
        let admitted_participants: BTreeSet<&str> = snapshot
            .state
            .participants
            .iter()
            .filter(|participant| participant.package_id() == tool.package_id)
            .map(|participant| participant.participant_id())
            .collect();
        let mut seen: BTreeSet<String> = BTreeSet::new();
        let mut mechanical = Vec::with_capacity(character_updates.len());
        for item in &character_updates {
            if !keys_are(item, &["operation", "participant_id", "expected_revision", "fields"], &[]) {
                return Err(rejected("Character update fields are invalid").into());
            }
            if !py_eq(&item["expected_revision"], &Json::from(expected_revision)) {
                return Err(rejected("Character updates must share one revision").into());
            }
            let participant_id = match &item["participant_id"] {
                Json::String(id) if admitted_participants.contains(id.as_str()) && !seen.contains(id) => {
                    id.clone()
                }
                _ => return Err(rejected("Character update identity is invalid or duplicated").into()),
            };
            seen.insert(participant_id.clone());
            let mut entry = JsonMap::new();
            entry.insert("participant_id".into(), participant_id.into());
            entry.insert("fields".into(), item["fields"].clone());
            mechanical.push(Json::Object(entry.clone()));
            character_answers.push(Json::Object(entry));
        }
        let mut identity = JsonMap::new();
        identity.insert("update_id".into(), request.update_id.as_str().into());
        identity.insert("session_id".into(), request.session_id.as_str().into());
        identity.insert("basis_message_ids".into(), texts(&request.basis_message_ids));
        identity.insert("updates".into(), all_json.clone());
        let mut parameters = JsonMap::new();
        parameters.insert("updates".into(), Json::Array(mechanical));
        character_command = Some(ActionCommand {
            campaign_id: campaign_id.clone(),
            action_id: format!("character-{}", digest(&Json::Object(identity))),
            idempotency_key: request.update_id.clone(),
            owner_id: owner_id.to_owned(),
            writer: WriterRole::Dm,
            expected_revision,
            turn_id: resolved_turn_id.clone(),
            package_id: tool.package_id.clone(),
            tool_name: tool.name.clone(),
            parameters: owned_payload(&Json::Object(parameters))?,
            initiating_session_id: Some(request.session_id.clone()),
        });
    }
    // `{str(item["id"]): item ...}`: insertion order, a later secret replacing in place.
    let mut secrets: Vec<(String, JsonMap)> = Vec::new();
    for item in super::content_reads::campaign_secret_catalog(store, &campaign_id, &plan)? {
        if let Some(Json::String(id)) = item.get("id") {
            let id = id.clone();
            match secrets.iter_mut().find(|(known, _)| *known == id) {
                Some(entry) => entry.1 = item,
                None => secrets.push((id, item)),
            }
        }
    }
    let mut revealed_to_player: BTreeSet<String> = snapshot
        .continuity
        .reveals
        .iter()
        .filter(|(_, audience)| names_player(audience))
        .map(|(secret, _)| secret.clone())
        .collect();
    for update in &admitted {
        if operation(update).as_deref() != Some("reveal_secret") {
            continue;
        }
        if let (Some(Json::String(secret)), Some(audience)) =
            (update.get("secret_id"), update.get("audience"))
        {
            if names_player(audience) {
                revealed_to_player.insert(secret.clone());
            }
        }
    }
    // `{str(secret["fact"]): str(secret_id) ...}`: a later secret with the same fact wins.
    let mut protected_facts: BTreeMap<String, String> = BTreeMap::new();
    for (secret_id, secret) in &secrets {
        if let Some(Json::String(fact)) = secret.get("fact") {
            protected_facts.insert(fact.clone(), secret_id.clone());
        }
    }
    let mut plan_items: BTreeSet<String> = BTreeSet::new();
    for ending in intended_endings(&plan) {
        plan_items.insert(ending_progress_id(ending)?);
    }
    let retained = super::content_reads::retained_preparation(store, &campaign_id, &plan)?;
    for collection in ["objectives", "structure"] {
        plan_items.extend(collection_ids(retained.get(collection)));
    }
    let scene_act_ids: BTreeSet<String> = collection_ids(retained.get("structure")).into_iter().collect();
    let persisted: BTreeSet<&str> =
        snapshot.continuity.records.iter().filter_map(|record| record.operation_id.as_deref()).collect();
    let replaying = !character_updates.is_empty()
        || !inventory_updates.is_empty()
        || (0..admitted.len())
            .any(|index| persisted.contains(format!("{}-{}", request.update_id, index + 1).as_str()));
    let mut current_records: BTreeMap<(String, String), &JsonMap> = BTreeMap::new();
    for record in &snapshot.continuity.records {
        let key = (record.kind.clone(), record.record_id.clone());
        if record.status == "active" {
            current_records.insert(key, &record.payload);
        } else {
            current_records.remove(&key);
        }
    }
    let editable = |store: &AuthorityStore| -> Result<Option<BTreeSet<String>>, Failure> {
        work.ensure_not_transitioning(&campaign_id)?;
        let Some(tool) = super::turn_reads::character_update_tool(store, &campaign_id)? else {
            return Ok(None);
        };
        // `input_schema["properties"]["updates"]["items"]["properties"]["fields"]["properties"]`.
        let mut node = Json::Object(tool.input_schema);
        for key in ["properties", "updates", "items", "properties", "fields", "properties"] {
            node = match node {
                Json::Object(mut map) => {
                    map.remove(key).ok_or_else(|| DomainError::uncaught("KeyError", format!("'{key}'")))?
                }
                _ => {
                    return Err(
                        DomainError::type_error("list indices must be integers or slices, not str").into()
                    )
                }
            };
        }
        match node {
            Json::Object(map) => Ok(Some(map.keys().cloned().collect())),
            _ => Err(DomainError::type_error("'set' requires a mapping of fields").into()),
        }
    };
    let mut commands: Vec<CampaignContinuityCommand> = Vec::new();
    let mut changed: BTreeSet<(String, String)> = BTreeSet::new();
    let base_revision = match &character_command {
        Some(command) => command.expected_revision.checked_add(1),
        None => Some(inventory_revision),
    }
    .ok_or_else(|| StoreError::Other("campaign revision overflow".into()))?;
    for (index, update) in admitted.iter().enumerate() {
        let operation_id = format!("{}-{}", request.update_id, index + 1);
        let admitted_note = note(
            store,
            update,
            &snapshot,
            &secrets,
            &revealed_to_player,
            &protected_facts,
            &plan_items,
            &scene_act_ids,
            &editable,
        );
        let (kind, record_id, payload) = match admitted_note {
            Ok(note) => note,
            // `except CommandRejected: raise; except (TypeError, ValueError)`: the rest propagate.
            Err(Failure::Domain(error))
                if matches!(error.python_class(), "ValueError" | "TypeError" | "JSONDecodeError") =>
            {
                return Err(rejected_with(
                    &format!("Campaign File update {} is invalid", index + 1),
                    "campaign_file_update_invalid",
                )
                .into())
            }
            Err(failure) => return Err(failure),
        };
        let key = (kind.clone(), record_id.clone());
        if !changed.insert(key.clone()) {
            return Err(rejected_with(
                "Campaign File update changes one record more than once",
                "campaign_file_duplicate_record",
            )
            .into());
        }
        if !replaying && kind == "reveal" {
            let empty = Json::Array(Vec::new());
            let current = snapshot.continuity.reveals.get(&record_id).unwrap_or(&empty);
            let proposed = payload.get("audience").unwrap_or(&empty);
            let (Json::Array(current), Json::Array(proposed)) = (current, proposed) else {
                return Err(rejected_with(
                    "Campaign File reveal audience is invalid",
                    "campaign_file_update_invalid",
                )
                .into());
            };
            if proposed.iter().all(|item| current.iter().any(|known| py_eq(known, item))) {
                continue;
            }
        } else if !replaying {
            if let Some(current) = current_records.get(&key) {
                let without_origin = |payload: &JsonMap| -> Json {
                    Json::Object(
                        payload
                            .iter()
                            .filter(|(key, _)| *key != "origin")
                            .map(|(k, v)| (k.clone(), v.clone()))
                            .collect(),
                    )
                };
                if canonical_json_bytes(&without_origin(current))
                    == canonical_json_bytes(&without_origin(&payload))
                {
                    continue;
                }
            }
        }
        let offset = i64::try_from(commands.len()).unwrap_or(i64::MAX);
        commands.push(CampaignContinuityCommand {
            campaign_id: campaign_id.clone(),
            operation_id,
            session_id: request.session_id.clone(),
            expected_revision: base_revision
                .checked_add(offset)
                .ok_or_else(|| StoreError::Other("campaign revision overflow".into()))?,
            kind,
            record_id,
            status: "active".into(),
            payload,
            basis_message_ids: request.basis_message_ids.clone(),
            turn_id: resolved_turn_id.clone(),
        });
    }
    let progress: Vec<Json> = snapshot.continuity.plan_progress.iter().cloned().map(Json::Object).collect();
    let proposed: Vec<Json> = commands
        .iter()
        .filter(|command| command.kind == "plan_progress")
        .map(|command| Json::Object(command.payload.clone()))
        .collect();
    if let Err(error) = check_ending_progress(&plan, &progress, &proposed) {
        if error.python_class() == "ValueError" {
            return Err(rejected_with(error.message(), "campaign_ending_not_ready").into());
        }
        return Err(error.into());
    }
    if let Some(command) = character_command {
        return Ok(Stage::Character {
            command: Box::new(command),
            commands,
            character_updates: character_answers,
        });
    }
    if commands.is_empty() {
        let mut answer = JsonMap::new();
        answer.insert("status".into(), "campaign_file_unchanged".into());
        answer.insert("operation_id".into(), request.update_id.as_str().into());
        answer.insert("change_count".into(), 0.into());
        answer.insert("campaign_revision".into(), snapshot.state.revision.into());
        answer.insert("state_changed".into(), false.into());
        return Ok(Stage::Unchanged(owned_payload(&Json::Object(answer))?));
    }
    Ok(Stage::Batch { commands, inventory: !inventory_updates.is_empty() })
}

/// The bookkeeping tool's editable character fields, read when a note names a participant.
type EditableFields<'a> = dyn Fn(&AuthorityStore) -> Result<Option<BTreeSet<String>>, Failure> + 'a;

/// One admitted note checked and made into its continuity record: `(kind, record_id, payload)`.
#[allow(clippy::too_many_arguments, clippy::too_many_lines)]
fn note(
    store: &AuthorityStore,
    update: &JsonMap,
    snapshot: &CampaignSnapshot,
    secrets: &[(String, JsonMap)],
    revealed_to_player: &BTreeSet<String>,
    protected_facts: &BTreeMap<String, String>,
    plan_items: &BTreeSet<String>,
    scene_act_ids: &BTreeSet<String>,
    editable: &EditableFields<'_>,
) -> Result<(String, String, JsonMap), Failure> {
    let get = |key: &str| update.get(key).unwrap_or(&Json::Null);
    let operation = update.get("operation").and_then(Json::as_str).unwrap_or("");
    let mut payload = JsonMap::new();
    let (kind, record_id) = match operation {
        "prepared_inventory" => {
            let Some(Json::Object(prepared)) = update.get("payload") else {
                return Err(DomainError::type_error("Prepared inventory payload is not a mapping").into());
            };
            return Ok(("entity".into(), INVENTORY_RECORD_ID.into(), prepared.clone()));
        }
        "record_memory" => {
            let required = ["operation", "fact_id", "category", "text", "visibility", "known_by"];
            if !keys_are(update, &required, &[&["subjects"]]) {
                return Err(value("Campaign memory fields are invalid").into());
            }
            let fact_id =
                validate_authority_identity_value(get("fact_id"), "Campaign memory fact", false)?.to_owned();
            let categories =
                ["decision", "relationship", "discovery", "promise", "consequence", "continuity"];
            let Some(category) = get("category").as_str().filter(|category| categories.contains(category))
            else {
                return Err(value("Campaign memory category is invalid").into());
            };
            let Some(text) = note_text(update.get("text"), 2_000) else {
                return Err(value("Campaign memory text is invalid").into());
            };
            let Some(visibility) =
                get("visibility").as_str().filter(|v| ["player_known", "gm_private"].contains(v))
            else {
                return Err(value("Campaign memory visibility is invalid").into());
            };
            let empty = Json::Array(Vec::new());
            let (Some(known_by), Some(subjects)) = (
                identity_list(update.get("known_by")),
                identity_list(Some(update.get("subjects").unwrap_or(&empty))),
            ) else {
                return Err(value("Campaign memory knowledge is invalid").into());
            };
            for identity in known_by.iter().chain(&subjects) {
                validate_authority_identity(identity, "Campaign memory subject", false)?;
            }
            if visibility == "player_known" {
                if let Some(secret) = protected_facts.get(text) {
                    if !revealed_to_player.contains(secret) {
                        return Err(rejected("Campaign memory cannot reveal a protected secret").into());
                    }
                }
            }
            payload.insert("category".into(), category.into());
            payload.insert("text".into(), text.into());
            payload.insert("visibility".into(), visibility.into());
            payload.insert("known_by".into(), texts(&known_by));
            payload.insert("subjects".into(), texts(&subjects));
            payload.insert("origin".into(), "gm_commit".into());
            ("fact".to_owned(), fact_id)
        }
        "record_entity" => {
            let required =
                ["operation", "entity_id", "entity_kind", "name", "attributes", "visibility", "known_by"];
            if !keys_are(update, &required, &[&["source_ref"]]) {
                return Err(value("Campaign entity fields are invalid").into());
            }
            let record_id =
                validate_authority_identity_value(get("entity_id"), "Campaign entity", false)?.to_owned();
            let entity_kind =
                validate_authority_identity_value(get("entity_kind"), "Campaign entity kind", false)?
                    .to_owned();
            if record_id == INVENTORY_RECORD_ID || entity_kind == INVENTORY_ENTITY_KIND {
                return Err(rejected("Use inventory_updates for fallback holdings").into());
            }
            let Some(name) = note_text(update.get("name"), 300) else {
                return Err(value("Campaign entity name is invalid").into());
            };
            let Some(Json::Object(attributes)) = update.get("attributes") else {
                return Err(value("Campaign entity attributes are invalid").into());
            };
            if snapshot.state.participants.iter().any(|participant| participant.participant_id() == record_id)
            {
                if let Some(fields) = editable(store)? {
                    if attributes.keys().any(|key| fields.contains(key)) {
                        return Err(rejected_with(
                            "Use character_updates for editable Ruleset fields; narrative attributes do not change character holdings",
                            "character_fields_require_bookkeeping",
                        )
                        .into());
                    }
                }
            }
            let Some(visibility) =
                get("visibility").as_str().filter(|v| ["player_known", "gm_private"].contains(v))
            else {
                return Err(value("Campaign entity visibility is invalid").into());
            };
            let Some(known_by) = identity_list(update.get("known_by")) else {
                return Err(value("Campaign entity knowledge is invalid").into());
            };
            for identity in &known_by {
                validate_authority_identity(identity, "Campaign entity knowledge", false)?;
            }
            let source_ref = get("source_ref").clone();
            if !source_ref.is_null() {
                validate_authority_identity_value(&source_ref, "Campaign entity source", false)?;
            }
            payload.insert("entity_kind".into(), entity_kind.into());
            payload.insert("name".into(), name.into());
            payload.insert("attributes".into(), Json::Object(attributes.clone()));
            payload.insert("visibility".into(), visibility.into());
            payload.insert("known_by".into(), texts(&known_by));
            payload.insert("source_ref".into(), source_ref);
            payload.insert("origin".into(), "gm_commit".into());
            ("entity".to_owned(), record_id)
        }
        "update_scene" => {
            if !keys_are(
                update,
                &["operation", "summary"],
                &[&["act_id"], &["location_id"], &["act_id", "location_id"]],
            ) {
                return Err(value("Campaign scene fields are invalid").into());
            }
            let Some(summary) = note_text(update.get("summary"), 2_000) else {
                return Err(value("Campaign scene summary is invalid").into());
            };
            let (act_id, location_id) = (get("act_id").clone(), get("location_id").clone());
            if !act_id.is_null() {
                let act = validate_authority_identity_value(&act_id, "Campaign scene act", false)?;
                if !scene_act_ids.contains(act) {
                    return Err(value("Campaign scene act is unavailable").into());
                }
            }
            if !location_id.is_null() {
                validate_authority_identity_value(&location_id, "Campaign scene location", false)?;
            }
            payload.insert("act_id".into(), act_id);
            payload.insert("location_id".into(), location_id);
            payload.insert("summary".into(), summary.into());
            payload.insert("origin".into(), "gm_commit".into());
            ("scene".to_owned(), "current_scene".to_owned())
        }
        "reveal_secret" => {
            if !keys_are(update, &["operation", "secret_id", "audience"], &[]) {
                return Err(value("Campaign reveal fields are invalid").into());
            }
            let record_id =
                validate_authority_identity_value(get("secret_id"), "Campaign secret", false)?.to_owned();
            let audience = identity_list(update.get("audience")).filter(|audience| !audience.is_empty());
            let (true, Some(audience)) = (secrets.iter().any(|(id, _)| *id == record_id), audience) else {
                return Err(value("Campaign secret reveal is invalid").into());
            };
            for identity in &audience {
                validate_authority_identity(identity, "Campaign secret audience", false)?;
            }
            payload.insert("secret_id".into(), record_id.as_str().into());
            payload.insert("audience".into(), texts(&audience));
            payload.insert("origin".into(), "gm_commit".into());
            ("reveal".to_owned(), record_id)
        }
        "update_plan_progress" => {
            if !keys_are(update, &["operation", "plan_item_id", "progress_status", "progress_summary"], &[]) {
                return Err(value("Plan Progress fields are invalid").into());
            }
            let record_id =
                validate_authority_identity_value(get("plan_item_id"), "Campaign Plan item", false)?
                    .to_owned();
            if !plan_items.contains(&record_id) {
                return Err(value("Campaign Plan item is unavailable").into());
            }
            let statuses = ["introduced", "engaged", "deferred", "resolved", "retired"];
            let Some(status) = get("progress_status").as_str().filter(|status| statuses.contains(status))
            else {
                return Err(value("Plan Progress status is invalid").into());
            };
            let Some(summary) = note_text(update.get("progress_summary"), 2_000) else {
                return Err(value("Plan Progress summary is invalid").into());
            };
            payload.insert("plan_item_id".into(), record_id.as_str().into());
            payload.insert("status".into(), status.into());
            payload.insert("summary".into(), summary.into());
            payload.insert("origin".into(), "gm_commit".into());
            ("plan_progress".to_owned(), record_id)
        }
        _ => return Err(value("Campaign File operation is unavailable").into()),
    };
    Ok((kind, record_id, owned_payload(&Json::Object(payload))?))
}
