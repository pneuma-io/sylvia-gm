//! `AuthorityOwner.campaign_panels(campaign_id, after_revision=...)`: the package-declared panels
//! of a campaign, projected from canonical campaign records only, with each panel action's
//! current availability, the pending player response, the inventory panel, the Ruleset's starting
//! layout and the shot effects after a revision.
//!
//! Each surface goes through the projection its renderer declares (lane 2's piece table, tactical
//! grid, coordinate map and network graph), and a surface whose saved data cannot be shown is kept
//! with Python's unavailable reason, or dropped where Python drops it.

use super::actions::ActionError;
use super::content_reads::{catalog_on, decision_scope_declaration, CatalogTool};
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{owned_payload, ActionStatus, LifecycleState, PackageRole, WriterRole};
use sylvia_domain::panel_layout::{parse_panel_layout, PANEL_LAYOUT_BYTE_LIMIT, PANEL_LAYOUT_PATH};
use sylvia_domain::player_actions::PLAYER_ACTION_ORIGIN;
use sylvia_domain::pyops::{py_eq, py_truthy};
use sylvia_domain::DomainError;
use sylvia_store::snapshots::DisplayQuery;
use sylvia_store::{AuthorityStore, StoreHandle, StoreResult};

fn rejected(message: &str) -> ActionError {
    DomainError::command_rejected(message).into()
}

fn key_error(key: &str) -> ActionError {
    DomainError::uncaught("KeyError", format!("'{key}'")).into()
}

/// `player_safe_source(source)`: keep legacy installed packages from projecting private Plan data.
fn player_safe_source(source: Option<&Json>) -> bool {
    let Some(Json::Object(source)) = source else { return false };
    match source.get("kind").and_then(Json::as_str) {
        Some("campaign_plan") => false,
        Some("keyed_values") => match source.get("entries") {
            Some(Json::Array(entries)) => entries.iter().all(|entry| {
                matches!(entry, Json::Object(entry)
                    if player_safe_source(entry.get("key")) && player_safe_source(entry.get("value")))
            }),
            _ => false,
        },
        Some("object") => match source.get("fields") {
            Some(Json::Object(fields)) => fields.values().all(|child| player_safe_source(Some(child))),
            _ => false,
        },
        Some(kind) => matches!(kind, "state" | "participants" | "literal"),
        None => false,
    }
}

/// `{**surface_spec, key: value}`: `surface` with `key` set, keeping its place when present.
fn with(surface: &JsonMap, key: &str, value: Json) -> JsonMap {
    let mut copy = surface.clone();
    copy.insert(key.to_owned(), value);
    copy
}

/// A surface whose saved data cannot be shown: the surface (without `source` when `drop_source`),
/// its unavailable reason as its data, and no actions.
fn unavailable(surface: &JsonMap, reason: &str, drop_source: bool) -> JsonMap {
    let mut copy: JsonMap = surface
        .iter()
        .filter(|(key, _)| !(drop_source && *key == "source"))
        .map(|(k, v)| (k.clone(), v.clone()))
        .collect();
    copy.insert("data".into(), serde_json::json!({"unavailable_reason": reason}));
    copy.insert("actions".into(), Json::Array(Vec::new()));
    copy
}

/// What the owner's in-memory state adds to the store's read: whether campaign work runs.
struct Live {
    busy: bool,
}

fn tactical_uncaught(error: crate::tactical_grid::TacticalGridError) -> Result<(), ActionError> {
    match error {
        crate::tactical_grid::TacticalGridError::Invalid(_) => Ok(()),
        crate::tactical_grid::TacticalGridError::Uncaught(error) => {
            let class = match &error {
                sylvia_engine::spatial::SpatialError::Validation(_) => "SpatialValidationError",
                sylvia_engine::spatial::SpatialError::Uncaught { error_type, .. } => error_type,
            };
            Err(DomainError::uncaught(class, error.to_string()).into())
        }
    }
}

/// The surfaces as `campaign_panels` projects them, before the controls are added.
fn project_surface_data(
    store: &AuthorityStore,
    state: &sylvia_domain::authority::CampaignState,
    surfaces: Vec<JsonMap>,
) -> Result<Vec<JsonMap>, ActionError> {
    let mut projected: Vec<JsonMap> = Vec::new();
    for surface in surfaces {
        let source = surface.get("source").cloned();
        if !player_safe_source(source.as_ref()) {
            continue;
        }
        let source = source.unwrap_or(Json::Null);
        let renderer = surface.get("renderer").and_then(Json::as_str).map(str::to_owned);
        let (mut data, state_paths) = crate::tactical_grid::resolve_surface_source_with_state_paths(
            &source,
            &state.state,
            &state.participants,
        )?;
        let mut spec = surface;
        if renderer.as_deref() == Some("piece_table") {
            let declared = decision_scope_declaration(store, state)?;
            let viewers = crate::decision_scope::player_viewer_ids(state, declared.as_ref());
            let mut action_ids = std::collections::BTreeSet::new();
            match spec.get("actions") {
                Some(Json::Array(actions)) => {
                    for action in actions {
                        let id = action.get("id").ok_or_else(|| key_error("id"))?;
                        action_ids.insert(sylvia_domain::pyops::py_str(id));
                    }
                }
                _ => return Err(key_error("actions")),
            }
            match crate::piece_table::project_piece_table(&data, &viewers, &action_ids) {
                Ok(table) => data = Json::Object(table),
                Err(_) => {
                    projected.push(unavailable(&spec, "The saved table cannot be displayed.", true));
                    continue;
                }
            }
            spec.shift_remove("source");
        }
        if let Some(kind @ ("tactical_grid" | "coordinate_map")) = renderer.as_deref() {
            if kind == "coordinate_map" {
                if let Json::Object(map) = &data {
                    if map.get("enabled").is_some_and(|enabled| enabled != &Json::Bool(true)) {
                        continue;
                    }
                }
            }
            let result = if kind == "coordinate_map" {
                crate::tactical_grid::coordinate_actor_reference(spec.get("actions").unwrap_or(&Json::Null))
                    .and_then(|actor| {
                        crate::tactical_grid::project_coordinate_map(&data, actor.as_deref(), &state_paths)
                    })
            } else {
                crate::tactical_grid::project_tactical_grid(&data)
            };
            match result {
                Ok(map) => data = Json::Object(map),
                Err(error) => {
                    tactical_uncaught(error)?;
                    let scene_missing =
                        !matches!(&data, Json::Object(map) if !map.get("scene").is_none_or(Json::is_null));
                    if scene_missing {
                        continue;
                    }
                    projected.push(unavailable(
                        &spec,
                        "The saved map data is invalid. This campaign needs a corrected map.",
                        false,
                    ));
                    continue;
                }
            }
        }
        projected.push(with(&spec, "data", data));
    }
    let as_json: Vec<Json> = projected.iter().cloned().map(Json::Object).collect();
    let visible = crate::network_graph::visible_physical_entity_ids(&as_json)?;
    let mut safe: Vec<Json> = Vec::new();
    for mut surface in projected {
        if surface.get("renderer").and_then(Json::as_str) == Some("network_graph") {
            match crate::network_graph::project_network_graph(
                surface.get("data").unwrap_or(&Json::Null),
                &visible,
            ) {
                Ok(graph) => {
                    surface.insert("data".into(), Json::Object(graph));
                }
                Err(error) if error.python_class() == "NetworkGraphError" => continue,
                Err(error) => return Err(error.into()),
            }
        }
        safe.push(Json::Object(surface));
    }
    Ok(crate::network_graph::bind_physical_network_surfaces(&safe)?)
}

/// `project_control(action)`: the declared actor parameter and its current availability.
fn project_control(
    action: &JsonMap,
    available_tools: &[CatalogTool],
    actions_available: bool,
    control_scope: Option<&JsonMap>,
) -> Result<JsonMap, ActionError> {
    let intent = action.get("intent").unwrap_or(&Json::Null);
    let tools: Vec<&CatalogTool> =
        available_tools.iter().filter(|tool| py_eq(&Json::from(tool.name.as_str()), intent)).collect();
    let empty = JsonMap::new();
    let bindings = match action.get("bindings") {
        None => &empty,
        Some(Json::Object(bindings)) => bindings,
        Some(_) => {
            return Err(DomainError::uncaught("AttributeError", "object has no attribute 'items'").into())
        }
    };
    let one = (tools.len() == 1).then(|| tools[0]);
    let mut actor_parameter = one.and_then(|tool| tool.player_controls.actor_parameter.clone());
    if actor_parameter.as_deref().is_none_or(str::is_empty) {
        actor_parameter =
            bindings.iter().find(|(_, value)| value.as_str() == Some("actor")).map(|(key, _)| key.clone());
    }
    let mut available = actions_available && one.is_some();
    if available && !actor_parameter.as_ref().is_some_and(|parameter| bindings.contains_key(parameter)) {
        let params = match action.get("params") {
            None => JsonMap::new(),
            Some(Json::Object(params)) => params.clone(),
            Some(_) => return Err(DomainError::type_error("Panel action parameters are invalid").into()),
        };
        if let Err(error) =
            crate::player_actions::validate_player_control(control_scope, &params, actor_parameter.as_deref())
        {
            if error.python_class() != "CommandRejected" {
                return Err(error.into());
            }
            available = false;
        }
    }
    let mut projected = action.clone();
    projected.insert("available".into(), available.into());
    projected.insert("actor_parameter".into(), actor_parameter.map_or(Json::Null, Json::from));
    projected.insert(
        "response_policy".into(),
        one.map_or(Json::Null, |tool| Json::from(tool.player_controls.response.as_str())),
    );
    projected
        .insert("checks_required".into(), one.is_some_and(|tool| tool.check_player_action.is_some()).into());
    let preview = py_truthy(action.get("spatial_preview").unwrap_or(&Json::Null))
        || one.is_some_and(|tool| tool.player_controls.map_preview.is_some());
    projected.insert("map_preview_enabled".into(), preview.into());
    Ok(owned_payload(&Json::Object(projected))?)
}

/// `AuthorityOwner.campaign_panels(campaign_id, after_revision=...)`.
pub async fn campaign_panels(
    store: &StoreHandle,
    campaign_id: &str,
    after_revision: Option<&Json>,
) -> Result<JsonMap, ActionError> {
    let _operation = store.operation()?;
    let live = Live { busy: store.lanes().work.count(campaign_id) != 0 };
    let transitioning = store.lanes().work.ensure_not_transitioning(campaign_id).err();
    let (campaign, after) = (campaign_id.to_owned(), after_revision.cloned());
    let answer: StoreResult<Result<JsonMap, ActionError>> =
        store.call(move |store| Ok(panels_on(store, &campaign, after.as_ref(), &live, transitioning))).await;
    answer?
}

#[allow(clippy::too_many_lines)]
fn panels_on(
    store: &AuthorityStore,
    campaign_id: &str,
    after_revision: Option<&Json>,
    live: &Live,
    transitioning: Option<sylvia_store::StoreError>,
) -> Result<JsonMap, ActionError> {
    let query = DisplayQuery {
        actions_after_revision: after_revision.and_then(Json::as_i64),
        ..DisplayQuery::default()
    };
    let snapshot = store.display_snapshot(campaign_id, &query)?;
    let revision = snapshot.state.revision;
    let after = match after_revision {
        None | Some(Json::Null) => None,
        Some(value @ Json::Number(number)) if number.is_i64() && !value.is_boolean() => {
            let after = number.as_i64().expect("checked");
            if after < 0 || after > revision {
                return Err(rejected("Shot presentation revision is invalid"));
            }
            Some(after)
        }
        Some(_) => return Err(rejected("Shot presentation revision is invalid")),
    };
    let lock = store.current_package_lock(campaign_id)?.package_lock;
    let capabilities: Vec<String> =
        lock.entries().iter().flat_map(|entry| entry.capabilities().iter().cloned()).collect();
    let (prompts, ui) = store.package_surfaces_for_current_lock(campaign_id)?;
    let surfaces = crate::package_library::project_surfaces(&prompts, &ui, &capabilities)?.ui_surfaces;
    let surfaces = crate::launch_compatibility::player_ui_surfaces(&surfaces, &lock)?;
    let linked = project_surface_data(store, &snapshot.state, surfaces)?;
    let actions_available = !live.busy
        && snapshot.state.writer == WriterRole::Dm
        && snapshot.state.lifecycle == LifecycleState::Active
        && store.current_open_turn(campaign_id)?.is_some()
        && store.pending_mechanics_action(campaign_id)?.is_none();
    if let Some(error) = transitioning {
        return Err(error.into());
    }
    let available_tools: Vec<CatalogTool> = catalog_on(store, campaign_id, false, false)?
        .into_iter()
        .filter(|tool| tool.player_controls.available)
        .collect();
    let response_available = actions_available;
    let control_scope =
        super::direct_actions::player_control_scope(store, &snapshot.state).map_err(ActionError::from)?;
    let actions_available = actions_available
        && control_scope.as_ref().is_none_or(|scope| scope.get("available") == Some(&Json::Bool(true)));
    let mut linked_surfaces: Vec<JsonMap> = Vec::new();
    for surface in linked {
        let actions = match surface.get("actions") {
            None => Vec::new(),
            Some(Json::Array(actions)) => actions
                .iter()
                .map(|action| match action {
                    Json::Object(action) => {
                        project_control(action, &available_tools, actions_available, control_scope.as_ref())
                            .map(Json::Object)
                    }
                    _ => Err(DomainError::uncaught("AttributeError", "object has no attribute 'get'").into()),
                })
                .collect::<Result<_, _>>()?,
            Some(_) => return Err(DomainError::type_error("Panel actions are not a sequence").into()),
        };
        linked_surfaces.push(with(&surface, "actions", Json::Array(actions)));
    }
    let (reserved, attempts) = super::player_reads::player_response_reservations(store, campaign_id)?;
    let attempts: Vec<String> = attempts.into_iter().collect();
    let recent = store.response_window_actions(campaign_id, &attempts, None)?;
    let unreserved: Vec<&str> =
        recent.iter().filter(|key| !reserved.contains(*key)).map(String::as_str).collect();
    let markers = store.player_action_markers(campaign_id, &unreserved, PLAYER_ACTION_ORIGIN)?;
    let pending: Vec<&JsonMap> =
        markers.iter().filter(|item| !item.get("origin").is_none_or(Json::is_null)).collect();
    let response_action = match pending.last() {
        None => Json::Null,
        Some(record) => {
            let origin = record.get("origin").and_then(Json::as_object).cloned().unwrap_or_default();
            let automatic = origin
                .get("policy")
                .and_then(Json::as_object)
                .and_then(|policy| policy.get("response"))
                .is_some_and(|response| response == "automatic");
            serde_json::json!({
                "action_id": record.get("action_id").cloned().ok_or_else(|| key_error("action_id"))?,
                "pending_count": pending.len(),
                "session_id": origin.get("session_id").cloned().unwrap_or(Json::Null),
                "turn_id": record.get("turn_id").cloned().ok_or_else(|| key_error("turn_id"))?,
                "automatic": automatic,
                "available": response_available,
            })
        }
    };
    let primary = lock.entries().iter().find(|entry| entry.role() == PackageRole::Primary);
    let inventory_tool = super::turn_reads::inventory_tool(store, campaign_id)?;
    let binding = match &inventory_tool {
        None => None,
        Some(tool) => match tool.input_schema.get("x-sylvia-gm-inventory") {
            Some(Json::Object(binding)) => Some(binding.clone()),
            Some(_) => return Err(DomainError::type_error("Ruleset inventory binding is invalid").into()),
            None => return Err(key_error("x-sylvia-gm-inventory")),
        },
    };
    let surface_id = |surface: &JsonMap| surface.get("id").cloned().ok_or_else(|| key_error("id"));
    let mut specialised = false;
    for surface in &linked_surfaces {
        // `renderer == "inventory" or (binding is not None and surface["id"] == ...)`.
        let bound = surface.get("renderer").and_then(Json::as_str) == Some("inventory")
            || match &binding {
                Some(binding) => {
                    py_eq(&surface_id(surface)?, binding.get("surface_id").unwrap_or(&Json::Null))
                }
                None => false,
            };
        if bound {
            specialised = true;
            break;
        }
    }
    if let Some(binding) =
        binding.as_ref().filter(|binding| binding.get("format") == Some(&Json::from("mechanics")))
    {
        let wanted = binding.get("surface_id").cloned().ok_or_else(|| key_error("surface_id"))?;
        let mut found = false;
        for surface in &linked_surfaces {
            if py_eq(&surface_id(surface)?, &wanted) {
                found = true;
                break;
            }
        }
        if !found {
            return Err(rejected("The Ruleset's declared inventory surface is unavailable"));
        }
        specialised = true;
    }
    if !specialised {
        let state_target = match &inventory_tool {
            None => None,
            Some(tool) => match tool.input_schema.get("x-sylvia-gm-character-update") {
                Some(Json::Object(target)) => Some(target.clone()),
                Some(_) => {
                    return Err(DomainError::type_error("Ruleset character update target is invalid").into())
                }
                None => return Err(key_error("x-sylvia-gm-character-update")),
            },
        };
        let entities = store.current_campaign_entities(campaign_id)?;
        linked_surfaces.push(crate::campaign_inventory::generic_inventory_surface(
            &snapshot.state,
            &entities,
            binding.as_ref(),
            state_target.as_ref(),
        )?);
    }
    let mut layout = Json::Null;
    if let Some(primary) = primary {
        let bytes = store.package_file_bytes(
            primary.package_id(),
            primary.version(),
            primary.sha256(),
            PANEL_LAYOUT_PATH,
            PANEL_LAYOUT_BYTE_LIMIT as i64,
        )?;
        if let Some(bytes) = bytes {
            layout = Json::Object(parse_panel_layout(&bytes, &ui)?);
        }
    }
    let shot_effects: Vec<Json> = snapshot
        .actions
        .iter()
        .filter(|action| {
            after.is_some_and(|after| action.resulting_revision > after)
                && action.status == ActionStatus::Committed
                && matches!(action.result.get("shot_effects"), Some(Json::Object(_)))
        })
        .map(|action| {
            let effects = action.result["shot_effects"].as_object().expect("checked");
            serde_json::json!({
                "action_id": action.action_id,
                "revision": action.resulting_revision,
                "shots": effects.get("shots").cloned().unwrap_or_else(|| Json::Array(Vec::new())),
                "unavailable": effects.get("unavailable").cloned().unwrap_or(Json::Bool(false)),
            })
        })
        .collect();
    Ok(owned_payload(&serde_json::json!({
        "campaign_id": snapshot.state.campaign_id,
        "revision": revision,
        "direct_actions_available": actions_available,
        "ruleset_id": primary.map_or(Json::Null, |entry| Json::from(entry.package_id())),
        "layout": layout,
        "player_control": control_scope.map_or(Json::Null, Json::Object),
        "response_action": response_action,
        "surfaces": Json::Array(linked_surfaces.into_iter().map(Json::Object).collect()),
        "shot_effects": shot_effects,
    }))?)
}
