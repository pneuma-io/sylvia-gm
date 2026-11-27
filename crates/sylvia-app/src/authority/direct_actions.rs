//! A player's direct action from a declared control: `AuthorityOwner.player_action` with
//! `_player_action_in_owned_work`, `_resolve_card_control`, `_check_player_action`,
//! `_resolve_panel_control`, `_player_control_scope`, `_player_action_preview` and
//! `_append_player_action_references`.
//!
//! The action runs under the direct-action work (refused while GM work runs), is identified by
//! its control (`player_control_origin`), previews or confirms the same visible intent, and
//! executes through `_execute_action` with its origin. A control is a saved result card's button
//! or a panel's action: an inventory, piece table, party or unit list, tactical grid, coordinate
//! map or network graph surface, read through lane 2's projections. A panel's preview carries its
//! segment preview, and a confirmation is checked again against the player-known map.

use super::actions::{execute_action_in_work, ActionError, ActionExecution, ExecuteOptions};
use sylvia_base::hash::sha256_hex;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{
    owned_payload, validate_authority_identity, ActionCommand, ActionReceipt, CampaignSnapshot, WriterRole,
};
use sylvia_domain::package_library::canonical_json_bytes;
use sylvia_domain::DomainError;
use sylvia_store::mechanics_choices::awaiting_receipt;
use sylvia_store::{AuthorityStore, StoreError, StoreHandle};

/// `player_action(campaign_id=..., source=..., operation_id=..., expected_revision=...,
/// selection=..., session_id=..., confirmation=..., preview_only=...)`'s arguments.
#[derive(Debug, Clone, PartialEq)]
pub struct PlayerActionRequest {
    pub campaign_id: String,
    pub source: Json,
    pub operation_id: String,
    pub expected_revision: i64,
    /// The client's selection as sent: any JSON, `Null` for Python's `None`.
    pub selection: Json,
    pub session_id: Option<String>,
    pub confirmation: Option<String>,
    pub preview_only: bool,
}

/// What a player action answers: a receipt, an awaiting choice, or (previewing) the proposal.
#[derive(Debug, Clone, PartialEq)]
pub enum PlayerActionAnswer {
    Receipt(ActionReceipt),
    AwaitingChoice(sylvia_domain::authority::AwaitingChoiceReceipt),
    Preview(JsonMap),
}

fn rejected(message: &str) -> DomainError {
    DomainError::command_rejected(message)
}

fn rejected_with(message: &str, code: &str) -> DomainError {
    DomainError::command_rejected_with(message, code, Vec::new())
}

/// The resolved control: `(tool, parameters, label, actor_parameter)`.
struct Control {
    tool: super::content_reads::CatalogTool,
    parameters: JsonMap,
    label: String,
    actor_parameter: Option<String>,
}

/// `AuthorityOwner.player_action(**request)`: the direct-action work, then the action.
pub async fn player_action(
    store: &StoreHandle,
    owner_id: &str,
    request: &PlayerActionRequest,
) -> Result<PlayerActionAnswer, ActionError> {
    let owned = request.campaign_id.clone();
    let _work = store
        .lanes()
        .work
        .begin_direct_action(&request.campaign_id, || {
            let campaign_id = owned.clone();
            async move { store.call(move |store| store.campaign_lifecycle(&campaign_id)).await }
        })
        .await?;
    player_action_in_owned_work(store, owner_id, request).await
}

/// `_player_action_in_owned_work(...)`: preview or execute a declared player control through the
/// shared action transaction.
pub(crate) async fn player_action_in_owned_work(
    store: &StoreHandle,
    owner_id: &str,
    request: &PlayerActionRequest,
) -> Result<PlayerActionAnswer, ActionError> {
    validate_authority_identity(&request.campaign_id, "Campaign identity", false)?;
    validate_authority_identity(&request.operation_id, "Panel action operation identity", false)?;
    let origin = sylvia_domain::player_actions::player_control_origin(
        &request.source,
        request.expected_revision,
        request.selection.as_object(),
        request.session_id.as_deref(),
    )?;
    let owned = request.clone();
    let origin_for_store = origin.clone();
    let prepared = store
        .call(move |store| Ok(prepare(store, &owned, &origin_for_store)))
        .await?
        .map_err(ActionError::from)?;
    let (control, token, policy, turn_id, writer) = match prepared {
        Prepared::Answer(answer) => {
            if let PlayerActionAnswer::Receipt(receipt) = &answer {
                append_references(store, request.session_id.as_deref(), receipt).await?;
            }
            return Ok(answer);
        }
        Prepared::Control { control, token, policy, turn_id, writer } => {
            (control, token, policy, turn_id, writer)
        }
    };
    if control_requires_confirmation(&policy) && request.confirmation.as_deref() != Some(token.as_str()) {
        return Err(rejected_with(
            "Preview and confirm the current action first",
            "action_confirmation_required",
        )
        .into());
    }
    let mut origin = origin;
    origin.insert("policy".into(), Json::Object(policy));
    let command = ActionCommand {
        campaign_id: request.campaign_id.clone(),
        action_id: request.operation_id.clone(),
        idempotency_key: request.operation_id.clone(),
        owner_id: owner_id.to_owned(),
        writer,
        expected_revision: request.expected_revision,
        turn_id,
        package_id: control.tool.package_id.clone(),
        tool_name: control.tool.name.clone(),
        parameters: owned_payload(&Json::Object(control.parameters.clone()))?,
        initiating_session_id: request.session_id.clone(),
    };
    let options = ExecuteOptions { player_action_origin: Some(origin), ..ExecuteOptions::default() };
    match execute_action_in_work(store, owner_id, &command, &options).await? {
        ActionExecution::Receipt(receipt) => {
            append_references(store, request.session_id.as_deref(), &receipt).await?;
            Ok(PlayerActionAnswer::Receipt(receipt))
        }
        ActionExecution::AwaitingChoice(receipt) => Ok(PlayerActionAnswer::AwaitingChoice(receipt)),
    }
}

fn control_requires_confirmation(policy: &JsonMap) -> bool {
    policy.get("confirmation").and_then(Json::as_str) == Some("required")
}

/// What the store-thread half decided.
enum Prepared {
    /// A replay, an awaiting choice, or the preview.
    Answer(PlayerActionAnswer),
    /// The control to execute, its confirmation token, its policy values, the open TURN and the
    /// campaign's writer.
    Control { control: Box<Control>, token: String, policy: JsonMap, turn_id: String, writer: WriterRole },
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

/// `_player_action_in_owned_work`'s reads and checks before its execution, on the store thread.
#[allow(clippy::too_many_lines)]
fn prepare(
    store: &AuthorityStore,
    request: &PlayerActionRequest,
    origin: &JsonMap,
) -> Result<Prepared, Failure> {
    let campaign_id = request.campaign_id.as_str();
    let snapshot = store.snapshot(campaign_id)?;
    if snapshot.state.writer != WriterRole::Dm {
        return Err(rejected("Player actions require GM campaign authority").into());
    }
    if let Some(session_id) = &request.session_id {
        let context = super::content_reads::active_dm_context(store, session_id)?;
        if context.session.campaign_id() != Some(campaign_id) {
            return Err(rejected("Player action belongs to another campaign").into());
        }
    }
    if let Some(existing) = store.action_hit(campaign_id, &request.operation_id)? {
        let origins = crate::player_actions::player_action_origins(&snapshot);
        let stored = origins.iter().find(|(id, _)| *id == request.operation_id).map(|(_, origin)| origin);
        if !crate::player_actions::player_action_request_matches(stored, origin) {
            return Err(DomainError::authority(
                "IdempotencyConflict",
                "Player action identity payload does not match",
            )
            .into());
        }
        if request.preview_only {
            return Err(rejected("This player action has already resolved").into());
        }
        return Ok(Prepared::Answer(PlayerActionAnswer::Receipt(existing.receipt)));
    }
    let pending = store.pending_mechanics_action(campaign_id)?;
    if let Some(pending) = pending.as_ref().filter(|pending| pending.action_id == request.operation_id) {
        if !crate::player_actions::player_action_request_matches(
            pending.player_action_origin.as_ref(),
            origin,
        ) {
            return Err(DomainError::authority(
                "IdempotencyConflict",
                "Player action identity payload does not match",
            )
            .into());
        }
        if request.preview_only {
            return Err(rejected("This player action is awaiting a choice").into());
        }
        return Ok(Prepared::Answer(PlayerActionAnswer::AwaitingChoice(awaiting_receipt(pending))));
    }
    if snapshot.state.revision != request.expected_revision {
        return Err(DomainError::authority(
            "StaleRevision",
            "Panel action expected an obsolete campaign revision",
        )
        .into());
    }
    if pending.is_some() {
        return Err(rejected("Resolve the pending mechanics choice first").into());
    }
    let panel = request.source.get("kind").and_then(Json::as_str) == Some("panel");
    let control = if panel {
        resolve_panel_control(store, &snapshot, &request.source, &request.selection, request.preview_only)?
    } else {
        resolve_card_control(store, &snapshot, &request.source, &request.selection)?
    };
    let Some(turn_id) = store.current_open_turn(campaign_id)? else {
        return Err(rejected_with("Panel action requires an open turn", "turn_unavailable").into());
    };
    let controls = &control.tool.player_controls;
    if !controls.available {
        return Err(rejected_with(
            "This Ruleset's player controls are unavailable",
            "panel_action_unavailable",
        )
        .into());
    }
    sylvia_domain::schema_admission::validate_parameters(&control.parameters, &control.tool.input_schema)?;
    let basis = store.action_basis(campaign_id, &turn_id)?;
    let participant_ids: Vec<&str> =
        basis.participants.iter().map(|participant| participant.fields().participant_id.as_str()).collect();
    sylvia_domain::schema_admission::validate_references(
        &control.parameters,
        &control.tool.input_schema,
        &participant_ids,
    )?;
    let control_scope = player_control_scope(store, &snapshot)?;
    crate::player_actions::validate_player_control(
        control_scope.as_ref(),
        &control.parameters,
        control.actor_parameter.as_deref(),
    )?;
    check_player_action(&control.tool, &control.parameters, &snapshot, request.preview_only)?;
    let mut policy = JsonMap::new();
    policy.insert("confirmation".into(), controls.confirmation.as_str().into());
    policy.insert("response".into(), controls.response.as_str().into());
    // Confirm the same visible intent; execution keeps the independently bound full-scene route.
    let confirmation_parameters = if !request.preview_only && controls.confirmation == "required" && panel {
        resolve_panel_control(store, &snapshot, &request.source, &request.selection, true)?.parameters
    } else {
        control.parameters.clone()
    };
    let mut identity = JsonMap::new();
    identity.insert("operation_id".into(), request.operation_id.as_str().into());
    identity.insert("origin".into(), Json::Object(origin.clone()));
    identity.insert("parameters".into(), Json::Object(confirmation_parameters));
    identity.insert("package_sha256".into(), control.tool.package_sha256.as_str().into());
    identity.insert("tool".into(), control.tool.name.as_str().into());
    identity.insert("policy".into(), Json::Object(policy.clone()));
    let token = sha256_hex(canonical_json_bytes(&Json::Object(identity)));
    if request.preview_only {
        let presentation = player_action_preview(
            store,
            &control.tool,
            &control.parameters,
            &snapshot,
            &request.source,
            &request.selection,
        )?;
        // Confirm the resolved actor even when a saved card has no live selection.
        let actor = control.actor_parameter.as_deref().and_then(|parameter| {
            let wanted = control.parameters.get(parameter)?;
            let actors = control_scope.as_ref()?.get("actors")?.as_array()?;
            actors
                .iter()
                .find(|item| sylvia_domain::pyops::py_eq(item.get("id").unwrap_or(&Json::Null), wanted))
                .cloned()
        });
        let mut answer = JsonMap::new();
        answer.insert("campaign_id".into(), campaign_id.into());
        answer.insert("revision".into(), request.expected_revision.into());
        answer.insert("label".into(), control.label.as_str().into());
        // `selection or {}`.
        let selection = if sylvia_domain::pyops::py_truthy(&request.selection) {
            request.selection.clone()
        } else {
            Json::Object(JsonMap::new())
        };
        answer.insert("selection".into(), selection);
        answer.insert("actor".into(), actor.unwrap_or(Json::Null));
        answer.insert("confirmation_required".into(), (controls.confirmation == "required").into());
        answer.insert("confirmation".into(), token.as_str().into());
        answer.insert("response".into(), controls.response.as_str().into());
        let preview = crate::result_cards::project_result_card(
            controls.preview.clone().map(Json::Object).as_ref(),
            &control.parameters,
            &JsonMap::new(),
        );
        answer.insert("preview".into(), preview.map_or(Json::Null, Json::Object));
        // `**(presentation or {})`: a key already present keeps its place.
        for (key, value) in presentation.unwrap_or_default() {
            answer.insert(key, value);
        }
        return Ok(Prepared::Answer(PlayerActionAnswer::Preview(owned_payload(&Json::Object(answer))?)));
    }
    Ok(Prepared::Control {
        control: Box::new(control),
        token,
        policy,
        turn_id,
        writer: snapshot.state.writer,
    })
}

/// `_resolve_card_control(snapshot, source, selection)`: an immutable saved button, validated
/// against the current package.
fn resolve_card_control(
    store: &AuthorityStore,
    snapshot: &CampaignSnapshot,
    source: &Json,
    selection: &Json,
) -> Result<Control, Failure> {
    if sylvia_domain::pyops::py_truthy(selection) {
        return Err(rejected("Result-card actions do not accept replacement parameters").into());
    }
    let unavailable = || rejected("The saved card action is unavailable");
    let source_action = source.get("action_id").cloned().unwrap_or(Json::Null);
    let original = snapshot
        .actions
        .iter()
        .find(|action| sylvia_domain::pyops::py_eq(&Json::from(action.action_id.as_str()), &source_action));
    let card = original.and_then(|action| action.result.get("result_card"));
    let index = source.get("button_index").and_then(Json::as_i64).unwrap_or(-1);
    let Some(Json::Object(card)) = card else { return Err(unavailable().into()) };
    let version_ok =
        matches!(card.get("version"), Some(Json::Number(n)) if n.is_i64() && n.as_i64() == Some(1));
    let Some(Json::Array(actions)) = card.get("actions") else { return Err(unavailable().into()) };
    let Ok(index) = usize::try_from(index) else { return Err(unavailable().into()) };
    if !version_ok || actions.len() > 8 || index >= actions.len() {
        return Err(unavailable().into());
    }
    let Some(Json::Object(action)) = actions.get(index) else { return Err(unavailable().into()) };
    let Some(Json::Object(parameters)) = action.get("parameters") else { return Err(unavailable().into()) };
    let original = original.expect("a card came from a saved action");
    let tools: Vec<super::content_reads::CatalogTool> =
        super::content_reads::catalog_on(store, &snapshot.state.campaign_id, false, false)?
            .into_iter()
            .filter(|tool| {
                sylvia_domain::pyops::py_eq(
                    &Json::from(tool.name.as_str()),
                    action.get("tool").unwrap_or(&Json::Null),
                ) && tool.package_id == original.package_id
            })
            .collect();
    let [tool] = <[_; 1]>::try_from(tools)
        .map_err(|_| rejected("The saved card mechanic is currently unavailable"))?;
    let label = match action.get("label") {
        None => "Action".to_owned(),
        Some(label) => sylvia_domain::pyops::py_str(label),
    };
    let actor_parameter = tool.player_controls.actor_parameter.clone();
    Ok(Control {
        tool,
        parameters: owned_payload(&Json::Object(parameters.clone()))?,
        label,
        actor_parameter,
    })
}

/// `_player_control_scope(snapshot)`: the player's actor identities, through the same Ruleset
/// scope as the GM's context.
fn player_control_scope(
    store: &AuthorityStore,
    snapshot: &CampaignSnapshot,
) -> Result<Option<JsonMap>, Failure> {
    let Some(declared) = super::content_reads::decision_scope_declaration(store, &snapshot.state)? else {
        return Ok(None);
    };
    crate::decision_scope::project_player_control_scope(&snapshot.state, &declared)
        .map(Some)
        .map_err(|error| Failure::Store(super::snapshots::projection_rejected(error)))
}

/// The participants' context payloads, as the package checks read them.
fn participants(snapshot: &CampaignSnapshot) -> JsonMap {
    snapshot
        .state
        .participants
        .iter()
        .map(|participant| {
            (participant.fields().participant_id.clone(), super::actions::participant_context(participant))
        })
        .collect()
}

/// `_check_player_action(tool, parameters, snapshot, player_view=...)`: the package's own checks,
/// at projection and right before execution; a player's view sees only known geometry.
fn check_player_action(
    tool: &super::content_reads::CatalogTool,
    parameters: &JsonMap,
    snapshot: &CampaignSnapshot,
    player_view: bool,
) -> Result<(), Failure> {
    let Some(checker) = &tool.check_player_action else { return Ok(()) };
    let mut state = snapshot.state.state.clone();
    let mut participants = participants(snapshot);
    if player_view {
        let known = |value: JsonMap| -> Result<JsonMap, Failure> {
            match sylvia_engine::spatial::player_known::player_known_spatial_value(&Json::Object(value)) {
                Ok(Json::Object(map)) => Ok(owned_payload(&Json::Object(map))?),
                Ok(_) => Err(DomainError::type_error("Safe payload must be a JSON object").into()),
                Err(error) => {
                    let class = match &error {
                        sylvia_engine::spatial::SpatialError::Validation(_) => "SpatialValidationError",
                        sylvia_engine::spatial::SpatialError::Uncaught { error_type, .. } => error_type,
                    };
                    Err(DomainError::uncaught(class, error.to_string()).into())
                }
            }
        };
        state = known(state)?;
        participants = known(participants)?;
    }
    match checker.check(parameters, &state, &participants) {
        Ok(None) => Ok(()),
        Ok(Some(reason)) => Err(rejected_with(&reason, "player_action_unavailable").into()),
        Err(pending) => {
            Err(StoreError::Coded(sylvia_base::CodedError::new(super::actions::PORT_PENDING, pending.0))
                .into())
        }
    }
}

/// The current lock's player UI surfaces: `project_surfaces(...)` with the lock's capabilities,
/// then `player_ui_surfaces`.
fn player_surfaces(store: &AuthorityStore, campaign_id: &str) -> Result<Vec<JsonMap>, Failure> {
    let lock = store.current_package_lock(campaign_id)?.package_lock;
    let capabilities: Vec<String> =
        lock.entries().iter().flat_map(|entry| entry.capabilities().iter().cloned()).collect();
    let (prompts, ui) = store.package_surfaces_for_current_lock(campaign_id)?;
    let surfaces = crate::package_library::project_surfaces(&prompts, &ui, &capabilities)?.ui_surfaces;
    Ok(crate::launch_compatibility::player_ui_surfaces(&surfaces, &lock)?)
}

/// `next(item for item in items if item.get("id") == wanted)` over a list's mappings.
fn find_by_id<'a>(items: Option<&'a Json>, wanted: &Json) -> Option<&'a JsonMap> {
    let Some(Json::Array(items)) = items else { return None };
    items
        .iter()
        .filter_map(Json::as_object)
        .find(|item| sylvia_domain::pyops::py_eq(item.get("id").unwrap_or(&Json::Null), wanted))
}

fn key_error(key: &str) -> DomainError {
    DomainError::uncaught("KeyError", format!("'{key}'"))
}

fn spatial_failure(error: sylvia_engine::spatial::SpatialError) -> Failure {
    let class = match &error {
        sylvia_engine::spatial::SpatialError::Validation(_) => "SpatialValidationError",
        sylvia_engine::spatial::SpatialError::Uncaught { error_type, .. } => error_type,
    };
    DomainError::uncaught(class, error.to_string()).into()
}

/// A `TacticalGridError` as `except TacticalGridError` sees it: the invalid selection refusal,
/// or Python's uncaught spatial failure passing through.
fn tactical_failure(error: crate::tactical_grid::TacticalGridError) -> Failure {
    match error {
        crate::tactical_grid::TacticalGridError::Invalid(_) => {
            rejected_with("Panel action selection is invalid", "panel_action_invalid").into()
        }
        crate::tactical_grid::TacticalGridError::Uncaught(error) => spatial_failure(error),
    }
}

/// `segment_preview`'s errors: every one is a failure Python lets through.
fn segment_failure(error: crate::tactical_grid::TacticalGridError) -> Failure {
    match error {
        crate::tactical_grid::TacticalGridError::Invalid(message) => {
            DomainError::uncaught("TacticalGridError", message).into()
        }
        crate::tactical_grid::TacticalGridError::Uncaught(error) => spatial_failure(error),
    }
}

/// `_resolve_panel_control(snapshot, source, selection, known_map=...)`: a current panel
/// declaration, binding only its advertised selection fields.
#[allow(clippy::too_many_lines)]
fn resolve_panel_control(
    store: &AuthorityStore,
    snapshot: &CampaignSnapshot,
    source: &Json,
    selection: &Json,
    known_map: bool,
) -> Result<Control, Failure> {
    use std::collections::BTreeSet;
    let campaign_id = snapshot.state.campaign_id.as_str();
    let surface_id = source.get("surface_id").cloned().unwrap_or(Json::Null);
    let panel_action_id = source.get("action_id").cloned().unwrap_or(Json::Null);
    let unavailable = || rejected_with("Panel action is unavailable", "panel_action_unavailable");
    let invalid = || rejected_with("Panel action is invalid", "panel_action_invalid");
    let selection_invalid = || rejected_with("Panel action selection is invalid", "panel_action_invalid");
    let surfaces = player_surfaces(store, campaign_id)?;
    let Some(surface) = surfaces
        .iter()
        .find(|item| sylvia_domain::pyops::py_eq(item.get("id").unwrap_or(&Json::Null), &surface_id))
    else {
        return Err(unavailable().into());
    };
    let Some(action) = find_by_id(surface.get("actions"), &panel_action_id) else {
        return Err(unavailable().into());
    };
    let Some(Json::String(intent)) = action.get("intent") else { return Err(invalid().into()) };
    let matching: Vec<super::content_reads::CatalogTool> =
        super::content_reads::catalog_on(store, campaign_id, false, false)?
            .into_iter()
            .filter(|tool| tool.name == *intent)
            .collect();
    let [tool] = <[_; 1]>::try_from(matching)
        .map_err(|_| rejected_with("Panel mechanic is unavailable", "panel_action_unavailable"))?;
    let Some(Json::Object(fixed)) = action.get("params") else { return Err(invalid().into()) };
    let renderer = surface.get("renderer").and_then(Json::as_str);
    let surface_source = surface.get("source").unwrap_or(&Json::Null);
    let (state, participants) = (&snapshot.state.state, &snapshot.state.participants);
    let source_data = || -> Result<Json, Failure> {
        Ok(crate::tactical_grid::resolve_surface_source(surface_source, state, participants)?)
    };
    let bindings = action.get("bindings").filter(|bindings| !bindings.is_null());
    let parameters = match (bindings, renderer) {
        (None, _) => {
            if sylvia_domain::pyops::py_truthy(selection) {
                return Err(invalid().into());
            }
            owned_payload(&Json::Object(fixed.clone()))?
        }
        (Some(_), Some("inventory")) => {
            crate::player_actions::resolve_inventory_action_parameters(action, selection, &source_data()?)?
        }
        (Some(_), Some("piece_table")) => {
            let data = source_data()?;
            let declared = super::content_reads::decision_scope_declaration(store, &snapshot.state)?;
            let viewers = crate::decision_scope::player_viewer_ids(&snapshot.state, declared.as_ref());
            // `frozenset(item["id"] for item in surface["actions"])`.
            let mut action_ids = BTreeSet::new();
            if let Some(Json::Array(actions)) = surface.get("actions") {
                for item in actions {
                    action_ids
                        .insert(sylvia_domain::pyops::py_str(item.get("id").ok_or_else(|| key_error("id"))?));
                }
            }
            crate::piece_table::project_piece_table(&data, &viewers, &action_ids)
                .and_then(|table| {
                    crate::piece_table::resolve_piece_action_parameters(action, selection, &table)
                })
                .map_err(|_| selection_invalid())?
        }
        (Some(_), Some(kind @ ("party" | "unit_list"))) => {
            crate::player_actions::resolve_party_action_parameters(
                action,
                selection,
                &source_data()?,
                kind == "unit_list",
            )?
        }
        (Some(_), Some(kind @ ("tactical_grid" | "coordinate_map"))) => {
            let (data, state_paths) = crate::tactical_grid::resolve_surface_source_with_state_paths(
                surface_source,
                state,
                participants,
            )?;
            let projection = if kind == "coordinate_map" {
                let actions = surface.get("actions").unwrap_or(&Json::Null);
                let actor =
                    crate::tactical_grid::coordinate_actor_reference(actions).map_err(tactical_failure)?;
                crate::tactical_grid::project_coordinate_map(&data, actor.as_deref(), &state_paths)
            } else {
                crate::tactical_grid::project_tactical_grid(&data)
            }
            .map_err(tactical_failure)?;
            let scene = data.get("scene").cloned();
            let authoritative = if known_map {
                let known = crate::tactical_grid::player_known_spatial_scene(
                    scene.as_ref().unwrap_or(&Json::Null),
                    false,
                )
                .map_err(spatial_failure)?;
                Some(Json::Object(known))
            } else {
                scene
            };
            crate::tactical_grid::resolve_tactical_action_parameters(
                action,
                selection,
                &projection,
                authoritative.as_ref(),
            )
            .map_err(tactical_failure)?
        }
        (Some(_), Some("network_graph")) => {
            let data = source_data()?;
            let graph = |error: DomainError| -> Failure {
                if error.python_class() == "NetworkGraphError" {
                    selection_invalid().into()
                } else {
                    error.into()
                }
            };
            let projection =
                crate::network_graph::project_network_graph(&data, &BTreeSet::new()).map_err(graph)?;
            let scope = player_control_scope(store, snapshot)?;
            let controlled: BTreeSet<String> = match &scope {
                Some(scope) if scope.get("available") == Some(&Json::Bool(true)) => scope
                    .get("actors")
                    .ok_or_else(|| key_error("actors"))?
                    .as_array()
                    .into_iter()
                    .flatten()
                    .map(|actor| actor.get("id").map(sylvia_domain::pyops::py_str).unwrap_or_default())
                    .collect(),
                _ => BTreeSet::new(),
            };
            crate::network_graph::resolve_network_action_parameters(
                &Json::Object(action.clone()),
                selection,
                &projection,
                &controlled,
            )
            .map_err(graph)?
        }
        (Some(_), _) => return Err(invalid().into()),
    };
    let binding_actor = match bindings {
        Some(Json::Object(bindings)) => {
            bindings.iter().find(|(_, value)| value.as_str() == Some("actor")).map(|(key, _)| key.clone())
        }
        _ => None,
    };
    let label = sylvia_domain::pyops::py_str(action.get("label").unwrap_or(&panel_action_id));
    let actor_parameter = tool.player_controls.actor_parameter.clone().or(binding_actor);
    Ok(Control { tool, parameters, label, actor_parameter })
}

/// `_player_action_preview(tool, parameters, snapshot, source, selection)`: display-only paths
/// bound to the known geometry (a panel's segment preview), then the package's own preview;
/// commitment keeps the full-scene parameters.
fn player_action_preview(
    store: &AuthorityStore,
    tool: &super::content_reads::CatalogTool,
    parameters: &JsonMap,
    snapshot: &CampaignSnapshot,
    source: &Json,
    selection: &Json,
) -> Result<Option<JsonMap>, Failure> {
    let panel = source.get("kind").and_then(Json::as_str) == Some("panel");
    let mut segment: Option<JsonMap> = None;
    if panel && sylvia_domain::pyops::py_truthy(selection) {
        let surfaces = player_surfaces(store, &snapshot.state.campaign_id)?;
        let surface_id = source.get("surface_id").cloned().unwrap_or(Json::Null);
        let surface = surfaces
            .iter()
            .find(|row| sylvia_domain::pyops::py_eq(row.get("id").unwrap_or(&Json::Null), &surface_id));
        let action_id = source.get("action_id").unwrap_or(&Json::Null);
        let action = surface.and_then(|surface| find_by_id(surface.get("actions"), action_id));
        if let (Some(surface), Some(action)) = (surface, action) {
            if action.get("spatial_preview").is_some_and(sylvia_domain::pyops::py_truthy) {
                let data = crate::tactical_grid::resolve_surface_source(
                    surface.get("source").unwrap_or(&Json::Null),
                    &snapshot.state.state,
                    &snapshot.state.participants,
                )?;
                let Json::Object(mut data) = data else {
                    return Err(DomainError::type_error("'NoneType' object is not subscriptable").into());
                };
                let scene = data.get("scene").cloned().ok_or_else(|| key_error("scene"))?;
                let known = crate::tactical_grid::player_known_spatial_scene(&scene, false)
                    .map_err(spatial_failure)?;
                data.insert("scene".into(), Json::Object(known));
                let mut with_action = surface.clone();
                with_action.insert("action".into(), Json::Object(action.clone()));
                segment =
                    crate::map_segment_preview::segment_preview(&with_action, &Json::Object(data), selection)
                        .map_err(segment_failure)?;
                if tool.player_controls.preflight.is_empty() {
                    return Ok(segment);
                }
            }
        }
    }
    let Some(previewer) = &tool.preview_player_action else { return Ok(None) };
    // `except (CommandRejected, TypeError, ValueError, KeyError): return None`.
    let parameters = if panel {
        match resolve_panel_control(store, snapshot, source, selection, true) {
            Ok(control) => control.parameters,
            Err(Failure::Domain(error))
                if matches!(
                    error.python_class(),
                    "CommandRejected"
                        | "MechanicsRejected"
                        | "RulesetPreconditionRejected"
                        | "TypeError"
                        | "ValueError"
                        | "KeyError"
                ) =>
            {
                return Ok(None)
            }
            Err(failure) => return Err(failure),
        }
    } else {
        parameters.clone()
    };
    let presentation = match previewer.preview(&parameters, &snapshot.state.state, &participants(snapshot)) {
        Ok(presentation) => presentation,
        Err(pending) => {
            return Err(StoreError::Coded(sylvia_base::CodedError::new(
                super::actions::PORT_PENDING,
                pending.0,
            ))
            .into())
        }
    };
    match (presentation, segment) {
        (Some(presentation), Some(segment)) => {
            // `{**segment, **presentation, "map_preview": presentation.get(...) or segment.get(...)}`.
            let map_preview = presentation
                .get("map_preview")
                .filter(|value| sylvia_domain::pyops::py_truthy(value))
                .cloned()
                .or_else(|| segment.get("map_preview").cloned())
                .unwrap_or(Json::Null);
            let mut merged = segment;
            for (key, value) in presentation {
                merged.insert(key, value);
            }
            merged.insert("map_preview".into(), map_preview);
            Ok(Some(owned_payload(&Json::Object(merged))?))
        }
        (presentation, _) => Ok(presentation),
    }
}

/// `_append_player_action_references(session_id, receipt)`: the direct action's public call and
/// result placed in its initiating session.
async fn append_references(
    store: &StoreHandle,
    session_id: Option<&str>,
    receipt: &ActionReceipt,
) -> Result<(), ActionError> {
    let Some(session_id) = session_id else { return Ok(()) };
    let _session = store.lanes().sessions.acquire(session_id).await;
    let (session_id, campaign_id, action_id) =
        (session_id.to_owned(), receipt.campaign_id.clone(), receipt.action_id.clone());
    store
        .call(move |store| {
            let snapshot = store.snapshot(&campaign_id)?;
            let events: Vec<_> = snapshot
                .events
                .into_iter()
                .filter(|event| {
                    event.action_id.as_deref() == Some(action_id.as_str())
                        && matches!(
                            event.event_type,
                            sylvia_domain::authority::CanonicalEventType::ToolCall
                                | sylvia_domain::authority::CanonicalEventType::ToolResult
                        )
                })
                .collect();
            store.append_canonical_references(&session_id, &events).map(drop)
        })
        .await?;
    Ok(())
}
