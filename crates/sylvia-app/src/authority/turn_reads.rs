//! The reads a Game Master provider round makes that the opening does not (lane 4's T2), the
//! application half: `AuthorityOwner.current_package_lock`, `provider_continuation`,
//! `actor_context`, `campaign_preparation_boundary`, `model_decision_boundary` and
//! `campaign_narrative_options`, with
//! `_character_update_tool`, `_character_update_options` and `_inventory_tool`.

use super::content_reads::{
    campaign_secret_catalog, catalog_on, optional_dm_structured_plan, retained_preparation,
};
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::ModelDecisionBoundary;
use sylvia_domain::campaign_ending::{ending_progress_id, intended_endings};
use sylvia_domain::package_library::{PackageLockRecord, ToolDeclaration};
use sylvia_domain::pyops::py_eq;
use sylvia_store::{AuthorityStore, StoreError, StoreHandle, StoreResult};

fn rejected(message: &str) -> StoreError {
    sylvia_domain::DomainError::command_rejected(message.to_owned()).into()
}

fn key_error(key: &str) -> StoreError {
    StoreError::from(sylvia_domain::DomainError::uncaught("KeyError", format!("'{key}'")))
}

/// `AuthorityOwner.current_package_lock(campaign_id)`.
pub async fn current_package_lock(store: &StoreHandle, campaign_id: &str) -> StoreResult<PackageLockRecord> {
    let campaign_id = campaign_id.to_owned();
    store.call(move |store| store.current_package_lock(&campaign_id)).await
}

/// `AuthorityOwner.provider_continuation(session_id, response_id, provider_id=..., model_id=...)`:
/// one selected provider exchange, never shown to ordinary display reads.
pub async fn provider_continuation(
    store: &StoreHandle,
    session_id: &str,
    response_id: &str,
    provider_id: &str,
    model_id: &str,
) -> StoreResult<Option<Json>> {
    let (session_id, response_id, provider_id, model_id) =
        (session_id.to_owned(), response_id.to_owned(), provider_id.to_owned(), model_id.to_owned());
    store
        .call(move |store| store.provider_continuation(&session_id, &response_id, &provider_id, &model_id))
        .await
}

/// `AuthorityOwner.actor_context(session_id, context_id, provider_id=..., model_id=...)`: only the
/// selected participant's private provider-compatible history.
pub async fn actor_context(
    store: &StoreHandle,
    session_id: &str,
    context_id: &str,
    provider_id: &str,
    model_id: &str,
) -> StoreResult<Option<JsonMap>> {
    let (session_id, context_id, provider_id, model_id) =
        (session_id.to_owned(), context_id.to_owned(), provider_id.to_owned(), model_id.to_owned());
    store.call(move |store| store.actor_context(&session_id, &context_id, &provider_id, &model_id)).await
}

/// `AuthorityOwner.campaign_preparation_boundary(campaign_id)`: the declared starting events
/// only, never unrelated ordinary turn state. A projection failure is a `CommandRejected`.
pub async fn campaign_preparation_boundary(
    store: &StoreHandle,
    campaign_id: &str,
) -> StoreResult<Option<ModelDecisionBoundary>> {
    let campaign_id = campaign_id.to_owned();
    store.call(move |store| preparation_boundary_on(store, &campaign_id)).await
}

/// [`campaign_preparation_boundary`] on the store, for a caller already inside one store call.
pub(super) fn preparation_boundary_on(
    store: &AuthorityStore,
    campaign_id: &str,
) -> StoreResult<Option<ModelDecisionBoundary>> {
    let snapshot = store.snapshot(campaign_id)?;
    let Some(declared) = super::content_reads::decision_scope_declaration(store, &snapshot.state)? else {
        return Ok(None);
    };
    if !declared.contains_key("preparation") {
        return Ok(None);
    }
    crate::decision_scope::project_campaign_preparation_scope(&snapshot.state, &declared)
        .map_err(super::snapshots::projection_rejected)
}

/// `AuthorityOwner.model_decision_boundary(campaign_id)`: the Ruleset-declared Player or GM
/// decision boundary now, through lane 2's `project_model_decision_scope`. Unlike
/// `model_context_snapshot`, it does not replace an isolated participant's state. A projection
/// failure is a `CommandRejected`.
pub async fn model_decision_boundary(
    store: &StoreHandle,
    campaign_id: &str,
) -> StoreResult<Option<ModelDecisionBoundary>> {
    let campaign_id = campaign_id.to_owned();
    store
        .call(move |store| {
            let snapshot = store.snapshot(&campaign_id)?;
            let Some(declared) = super::content_reads::decision_scope_declaration(store, &snapshot.state)?
            else {
                return Ok(None);
            };
            crate::decision_scope::project_model_decision_scope(&snapshot.state, &declared)
                .map_err(super::snapshots::projection_rejected)
        })
        .await
}

/// Python's `isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))` for
/// a JSON value: only a list.
fn sequence(value: Option<&Json>) -> &[Json] {
    match value {
        Some(Json::Array(items)) => items,
        _ => &[],
    }
}

/// `[str(item["id"]) for item in items if isinstance(item, Mapping) and isinstance(item.get("id"), str)]`.
fn string_ids(items: &[Json]) -> Vec<String> {
    items
        .iter()
        .filter_map(|item| match item {
            Json::Object(item) => match item.get("id") {
                Some(Json::String(id)) => Some(id.clone()),
                _ => None,
            },
            _ => None,
        })
        .collect()
}

/// What `campaign_narrative_options` reads before its catalog checks.
struct NarrativeBasis {
    campaign_id: String,
    revision: i64,
    scene_act_ids: Vec<String>,
    plan_item_ids: Vec<String>,
    eligible_secret_ids: Vec<String>,
}

fn narrative_basis(store: &AuthorityStore, session_id: &str) -> StoreResult<NarrativeBasis> {
    let (context, plan) = optional_dm_structured_plan(store, session_id)?;
    let campaign_id = context.session.campaign_id().map(str::to_owned).ok_or_else(|| {
        StoreError::from(sylvia_domain::DomainError::uncaught("AssertionError", String::new()))
    })?;
    let preparation = retained_preparation(store, &campaign_id, &plan)?;
    let acts = sequence(preparation.get("structure"));
    let snapshot = store.snapshot(&campaign_id)?;
    let scene_act_ids = string_ids(acts);
    let objective_ids = string_ids(sequence(preparation.get("objectives")));
    // `list(dict.fromkeys(...))`: first occurrence kept.
    let mut plan_item_ids: Vec<String> = Vec::new();
    let endings = intended_endings(&plan)
        .into_iter()
        .map(ending_progress_id)
        .collect::<Result<Vec<_>, _>>()
        .map_err(StoreError::from)?;
    for id in objective_ids.into_iter().chain(scene_act_ids.iter().cloned()).chain(endings) {
        if !plan_item_ids.contains(&id) {
            plan_item_ids.push(id);
        }
    }
    let player = Json::from("player");
    let revealed: Vec<&String> = snapshot
        .continuity
        .reveals
        .iter()
        .filter(|(_, audience)| sequence(Some(audience)).iter().any(|item| py_eq(&player, item)))
        .map(|(secret_id, _)| secret_id)
        .collect();
    let mut eligible_secret_ids = Vec::new();
    for secret in campaign_secret_catalog(store, &campaign_id, &plan)? {
        if let Some(Json::String(id)) = secret.get("id") {
            if !revealed.contains(&id) {
                eligible_secret_ids.push(id.clone());
            }
        }
    }
    Ok(NarrativeBasis {
        campaign_id,
        revision: snapshot.state.revision,
        scene_act_ids,
        plan_item_ids,
        eligible_secret_ids,
    })
}

/// `_character_update_tool(campaign_id)` past its transition check: the active Ruleset's one
/// bookkeeping mechanic, if it declares one.
fn character_update_options(store: &AuthorityStore, campaign_id: &str) -> StoreResult<JsonMap> {
    let tools: Vec<_> = catalog_on(store, campaign_id, true, false)?
        .into_iter()
        .filter(|tool| tool.input_schema.contains_key("x-sylvia-gm-character-update"))
        .collect();
    if tools.len() > 1 {
        return Err(rejected("Character bookkeeping declaration is ambiguous"));
    }
    let Some(tool) = tools.into_iter().next() else { return Ok(JsonMap::new()) };
    // `_character_update_options`: the exact package schema with the revision it must name.
    let object = |value: Option<&Json>, key: &str| match value {
        Some(Json::Object(object)) => Ok(object.clone()),
        Some(_) => Err(StoreError::from(sylvia_domain::DomainError::uncaught(
            "TypeError",
            format!("'{key}' is not a mapping"),
        ))),
        None => Err(key_error(key)),
    };
    let properties = object(tool.input_schema.get("properties"), "properties")?;
    let updates = object(properties.get("updates"), "updates")?;
    let mut schema = object(updates.get("items"), "items")?;
    let mut item_properties = object(schema.get("properties"), "properties")?;
    let mut revision = JsonMap::new();
    revision.insert("type".into(), "integer".into());
    revision.insert("minimum".into(), 0.into());
    item_properties.insert("expected_revision".into(), Json::Object(revision));
    let mut required = match schema.get("required") {
        Some(Json::Array(required)) => required.clone(),
        Some(_) => {
            return Err(StoreError::from(sylvia_domain::DomainError::uncaught(
                "TypeError",
                "'required' is not iterable",
            )))
        }
        None => return Err(key_error("required")),
    };
    required.push("expected_revision".into());
    schema.insert("properties".into(), Json::Object(item_properties));
    schema.insert("required".into(), Json::Array(required));
    let mut options = JsonMap::new();
    options.insert("character_update_schema".into(), Json::Object(schema));
    options.insert("campaign_revision".into(), store.snapshot(campaign_id)?.state.revision.into());
    Ok(options)
}

/// `_inventory_tool(campaign_id)` past its transition check: the one declaration that selects
/// inventory, whatever the current turn, writer or mechanic restrictions.
fn inventory_tool(store: &AuthorityStore, campaign_id: &str) -> StoreResult<Option<ToolDeclaration>> {
    let basis = store.package_catalog_basis(campaign_id)?;
    let mut tools: Vec<ToolDeclaration> = basis
        .declarations
        .into_iter()
        .map(|(_, tool)| tool)
        .filter(|tool| tool.input_schema.contains_key("x-sylvia-gm-inventory"))
        .collect();
    if tools.len() > 1 {
        return Err(rejected("Ruleset inventory declaration is ambiguous"));
    }
    Ok(tools.pop())
}

/// `AuthorityOwner.campaign_narrative_options(session_id)`: the exact narrative-state mutations
/// available to one DM session. The two catalog checks refuse a transitioning campaign where
/// Python's do, after the plan and secret reads.
pub async fn campaign_narrative_options(store: &StoreHandle, session_id: &str) -> StoreResult<JsonMap> {
    let id = session_id.to_owned();
    let basis = store.call(move |store| narrative_basis(store, &id)).await?;
    let mut operations: Vec<Json> =
        vec!["record_memory".into(), "record_entity".into(), "update_scene".into()];
    if !basis.plan_item_ids.is_empty() {
        operations.push("update_plan_progress".into());
    }
    if !basis.eligible_secret_ids.is_empty() {
        operations.push("reveal_secret".into());
    }
    store.lanes().work.ensure_not_transitioning(&basis.campaign_id)?;
    let campaign_id = basis.campaign_id.clone();
    let mut options = store.call(move |store| character_update_options(store, &campaign_id)).await?;
    if !options.is_empty() {
        operations.push("update_character".into());
    }
    store.lanes().work.ensure_not_transitioning(&basis.campaign_id)?;
    let campaign_id = basis.campaign_id.clone();
    if store.call(move |store| inventory_tool(store, &campaign_id)).await?.is_none() {
        operations.push("update_inventory".into());
    }
    let texts = |ids: Vec<String>| Json::Array(ids.into_iter().map(Json::from).collect());
    // `{**character_options, "campaign_revision": ...}`: the revision keeps its place.
    options.insert("campaign_revision".into(), basis.revision.into());
    options.insert("operations".into(), Json::Array(operations));
    options.insert("scene_act_ids".into(), texts(basis.scene_act_ids));
    options.insert("plan_item_ids".into(), texts(basis.plan_item_ids));
    options.insert("eligible_secret_ids".into(), texts(basis.eligible_secret_ids));
    Ok(options)
}
