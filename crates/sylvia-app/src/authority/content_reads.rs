//! The GM context's content reads, the application half: `AuthorityOwner.
//! ruleset_gm_operating_guide`, `catalog` (with `_catalog`), `campaign_knowledge_catalog` and
//! `campaign_knowledge_catalog_for_plan` (with `_active_dm_context`,
//! `_optional_dm_structured_plan`, `_retained_preparation`, `_campaign_setting_knowledge`,
//! `_exact_setting_knowledge` and `_campaign_secret_catalog`), and `model_context_owner` (with
//! `_decision_scope_declaration`).
//!
//! `catalog` builds each admitted tool with `_package_tool`, through lane 2's materialization
//! (`materialize_package_mechanic`, the `package_tool_*` readers and the player-action checker
//! and previewer). A campaign that is not active has none, as in Python.
//!
//! Python's owner keeps every built tool (`_tools`) and the last 16 runtimes it read
//! (`_runtime_payloads`). Building an exact package identity's tool is deterministic, so here
//! each catalog builds its tools again; lane 2's 16-entry parse cache keeps that from parsing
//! a runtime twice. Nothing a caller sees differs.

use crate::package_library::{declaration_admitted, dependency_only_manifest_rebind};
use crate::player_actions::PlayerControls;
use crate::ruleless_play::ruleless_gm_guide;
use std::collections::BTreeMap;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{
    owned_payload, validate_authority_identity, ModelToolBehavior, PackageRole, WriterRole,
};
use sylvia_domain::package_library::{
    canonical_json_bytes, parse_campaign_setting_knowledge_bytes, StatePredicate,
    CAMPAIGN_SETTING_KNOWLEDGE_BYTE_LIMIT, CAMPAIGN_SETTING_KNOWLEDGE_PATH,
};
use sylvia_domain::session::{SessionContext, SessionStatus};
use sylvia_domain::DomainError;
use sylvia_store::{AuthorityStore, StoreError, StoreHandle, StoreResult};

/// `_RULESET_GM_OPERATING_GUIDE_PATH`.
pub const RULESET_GM_OPERATING_GUIDE_PATH: &str = "prompts/gm-operating-guide.md";
/// `_RULESET_GM_OPERATING_GUIDE_BYTE_LIMIT`.
pub const RULESET_GM_OPERATING_GUIDE_BYTE_LIMIT: i64 = 64 * 1024;

fn rejected(message: &str) -> StoreError {
    DomainError::command_rejected(message).into()
}

/// `AuthorityOwner.ruleset_gm_operating_guide(campaign_id)`: the locked primary Ruleset's GM
/// guide, Sylvia's No Ruleset guide, or none.
pub async fn ruleset_gm_operating_guide(
    store: &StoreHandle,
    campaign_id: &str,
) -> StoreResult<Option<String>> {
    let _operation = store.operation()?;
    let id = campaign_id.to_owned();
    store
        .call(move |store| {
            let snapshot = store.snapshot(&id)?;
            let Some(primary) = snapshot
                .state
                .package_lock
                .entries()
                .iter()
                .find(|entry| entry.role() == PackageRole::Primary)
            else {
                return Ok(ruleless_gm_guide(&snapshot.state).map(str::to_owned));
            };
            let Some(value) = store.package_file_bytes(
                primary.package_id(),
                primary.version(),
                primary.sha256(),
                RULESET_GM_OPERATING_GUIDE_PATH,
                RULESET_GM_OPERATING_GUIDE_BYTE_LIMIT,
            )?
            else {
                return Ok(None);
            };
            let Ok(guide) = String::from_utf8(value) else {
                return Err(rejected("Ruleset GM operating guide is not valid UTF-8"));
            };
            if sylvia_store::pytext::py_strip(&guide).is_empty() {
                return Err(rejected("Ruleset GM operating guide is empty"));
            }
            Ok(Some(guide))
        })
        .await
}

/// `CatalogTool`: one exact package-bound mechanic, with what the model and the player see of it
/// and the executable parts lane 2 materializes (`execute`, `check_player_action`,
/// `preview_player_action`), which compare by identity as Python's callables do.
#[derive(Debug, Clone, PartialEq)]
pub struct CatalogTool {
    pub package_id: String,
    pub package_version: String,
    pub package_sha256: String,
    pub name: String,
    pub capability: String,
    pub input_schema: JsonMap,
    pub execute: crate::package_mechanic::PackageMechanic,
    pub description: String,
    pub model_behavior: ModelToolBehavior,
    pub model_behavior_state_predicates: Vec<StatePredicate>,
    pub player_controls: PlayerControls,
    pub check_player_action: Option<crate::package_mechanic::PlayerActionChecker>,
    pub preview_player_action: Option<crate::package_mechanic::PlayerActionPreviewer>,
    pub decision_title: String,
    pub permanently_refused: bool,
}

impl CatalogTool {
    /// `CatalogTool.__post_init__`'s identity checks.
    pub fn validate(&self) -> StoreResult<()> {
        validate_authority_identity(&self.package_id, "Catalog package identity", false)?;
        validate_authority_identity(&self.package_version, "Catalog package version", false)?;
        validate_authority_identity(&self.package_sha256, "Catalog package SHA-256", true)?;
        validate_authority_identity(&self.name, "Catalog tool name", false)?;
        validate_authority_identity(&self.capability, "Catalog capability", false)?;
        Ok(())
    }
}

/// `AuthorityOwner.catalog(campaign_id, include_unexposed=...)`: the campaign's admitted tools,
/// sorted by package identity and name.
pub async fn catalog(
    store: &StoreHandle,
    campaign_id: &str,
    include_unexposed: bool,
) -> StoreResult<Vec<CatalogTool>> {
    let _operation = store.operation()?;
    // `_ensure_campaign_not_transitioning`, under the owner's work registry.
    store.lanes().work.ensure_not_transitioning(campaign_id)?;
    let id = campaign_id.to_owned();
    store.call(move |store| catalog_on(store, &id, false, include_unexposed)).await
}

/// `_catalog(campaign_id, include_character_updates=..., include_unexposed=...)` past its
/// transition check.
pub(super) fn catalog_on(
    store: &AuthorityStore,
    campaign_id: &str,
    include_character_updates: bool,
    include_unexposed: bool,
) -> StoreResult<Vec<CatalogTool>> {
    let basis = store.package_catalog_basis(campaign_id)?;
    if basis.lifecycle != "active" {
        return Ok(Vec::new());
    }
    let capabilities: Vec<String> = basis
        .lock
        .package_lock
        .entries()
        .iter()
        .flat_map(|entry| entry.capabilities().iter().cloned())
        .collect();
    let mut available = Vec::new();
    for (entry, declaration) in &basis.declarations {
        let character_update = declaration.input_schema.contains_key("x-sylvia-gm-character-update");
        if character_update && !include_character_updates {
            continue;
        }
        if !include_unexposed
            && !(include_character_updates && character_update)
            && !declaration_admitted(
                declaration,
                &basis.state,
                &basis.turn_status,
                &basis.writer,
                &capabilities,
            )
        {
            continue;
        }
        let Some(tool) = package_tool(store, entry, declaration)? else { continue };
        if !tool.permanently_refused
            && tool.capability == declaration.capability
            && entry.capabilities().contains(&tool.capability)
        {
            let behavior = if include_unexposed
                || crate::package_library::state_predicates_match(
                    &tool.model_behavior_state_predicates,
                    &basis.state,
                ) {
                tool.model_behavior
            } else {
                ModelToolBehavior::Optional
            };
            available.push(CatalogTool { model_behavior: behavior, ..tool });
        }
    }
    available.sort_by(|left, right| {
        (&left.package_id, &left.package_version, &left.package_sha256, &left.name).cmp(&(
            &right.package_id,
            &right.package_version,
            &right.package_sha256,
            &right.name,
        ))
    });
    Ok(available)
}

/// A lane 2 failure Python lets through: its class and message.
fn uncaught(class: &'static str, message: String) -> StoreError {
    DomainError::uncaught(class, message).into()
}

/// `_package_tool(package, declaration)`: the exact package's tool, or `None` when the package
/// has no runtime or its runtime refuses the tool (`except PackageRuntimeError: return None`).
/// `package_tool_permanently_refused` is read after that `try`, so its refusal escapes as
/// Python's `PackageRuntimeError`, a `ValueError`.
///
/// Each tool is materialized once per owner and kept (`_tools`, keyed by the exact package
/// identity and tool name), and the runtime bytes it reads are kept for its siblings
/// (`_runtime_payloads`, the last 16 packages), all under `_tools_lock` ([`AuthorityStore::tool_cache`]).
pub(super) fn package_tool(
    store: &AuthorityStore,
    entry: &sylvia_domain::authority::PackageLockEntry,
    declaration: &sylvia_domain::package_library::ToolDeclaration,
) -> StoreResult<Option<CatalogTool>> {
    let package = (entry.package_id().to_owned(), entry.version().to_owned(), entry.sha256().to_owned());
    let identity = (package.0.clone(), package.1.clone(), package.2.clone(), declaration.name.clone());
    store.tool_cache().with(|cache| {
        if let Some(existing) = cache.tools.get(&identity).and_then(|tool| tool.downcast_ref::<CatalogTool>())
        {
            return Ok(Some(existing.clone()));
        }
        let runtime = match cache.runtime_payload(&package) {
            Some(runtime) => runtime,
            None => {
                let Some(runtime) = store.package_file_bytes(
                    entry.package_id(),
                    entry.version(),
                    entry.sha256(),
                    crate::package_runtime::PACKAGE_RUNTIME_PATH,
                    crate::package_runtime::PACKAGE_RUNTIME_BYTE_LIMIT as i64,
                )?
                else {
                    return Ok(None);
                };
                // Package identities are immutable: sibling tools reuse the verified bytes.
                let runtime = std::sync::Arc::new(runtime);
                cache.keep_runtime_payload(package, std::sync::Arc::clone(&runtime));
                runtime
            }
        };
        let Some(tool) = materialize_tool(entry, declaration, &runtime)? else { return Ok(None) };
        cache.tools.insert(identity, std::sync::Arc::new(tool.clone()));
        Ok(Some(tool))
    })
}

/// The materialization `_package_tool` makes from a package's runtime bytes.
fn materialize_tool(
    entry: &sylvia_domain::authority::PackageLockEntry,
    declaration: &sylvia_domain::package_library::ToolDeclaration,
    runtime: &[u8],
) -> StoreResult<Option<CatalogTool>> {
    use crate::package_mechanic::{
        materialize_package_mechanic, materialize_player_action_checker, materialize_player_action_previewer,
    };
    use crate::package_runtime::{
        package_tool_decision_title, package_tool_description, package_tool_model_behavior,
        package_tool_permanently_refused, package_tool_player_controls, PackageRuntimeFailure,
    };
    let package_id = entry.package_id();
    let built = (|| -> Result<_, PackageRuntimeFailure> {
        let mechanic =
            materialize_package_mechanic(package_id, entry.version(), entry.sha256(), declaration, runtime)?;
        let description = package_tool_description(package_id, declaration, runtime)?;
        let (model_behavior, predicates) = package_tool_model_behavior(package_id, declaration, runtime)?;
        let decision_title = package_tool_decision_title(package_id, declaration, runtime)?;
        let player_controls = package_tool_player_controls(package_id, declaration, runtime)?;
        let checker = materialize_player_action_checker(package_id, declaration, runtime)?;
        let previewer = materialize_player_action_previewer(package_id, declaration, runtime)?;
        Ok((
            mechanic,
            description,
            model_behavior,
            predicates,
            decision_title,
            player_controls,
            checker,
            previewer,
        ))
    })();
    let (
        execute,
        description,
        model_behavior,
        predicates,
        decision_title,
        player_controls,
        checker,
        previewer,
    ) = match built {
        Ok(parts) => parts,
        Err(PackageRuntimeFailure::Rejected(_)) => return Ok(None),
        Err(PackageRuntimeFailure::Uncaught { class, message }) => return Err(uncaught(class, message)),
    };
    let input_schema = declaration.input_schema.clone();
    // `validate_parameters({}, _schema_without_required(captured_schema))`.
    let mut without_required = input_schema.clone();
    without_required.insert("required".into(), Json::Array(Vec::new()));
    sylvia_domain::schema_admission::validate_parameters(&JsonMap::new(), &without_required)?;
    let permanently_refused = match package_tool_permanently_refused(package_id, declaration, runtime) {
        Ok(refused) => refused,
        Err(PackageRuntimeFailure::Rejected(error)) => {
            return Err(sylvia_base::CodedError::new("command_rejected", error.message)
                .with_error_type("PackageRuntimeError")
                .into())
        }
        Err(PackageRuntimeFailure::Uncaught { class, message }) => return Err(uncaught(class, message)),
    };
    let tool = CatalogTool {
        package_id: package_id.to_owned(),
        package_version: entry.version().to_owned(),
        package_sha256: entry.sha256().to_owned(),
        name: declaration.name.clone(),
        capability: declaration.capability.clone(),
        input_schema,
        execute,
        description,
        model_behavior,
        model_behavior_state_predicates: predicates,
        player_controls,
        check_player_action: Some(checker),
        preview_player_action: Some(previewer),
        decision_title,
        permanently_refused,
    };
    tool.validate()?;
    Ok(Some(tool))
}

/// `_active_dm_context(session_id)`: one active campaign-bound DM session.
pub(super) fn active_dm_context(store: &AuthorityStore, session_id: &str) -> StoreResult<SessionContext> {
    let context = store.session_context(session_id)?;
    let session = &context.session;
    if session.role() != WriterRole::Dm
        || session.fields().status != SessionStatus::Active
        || session.campaign_id().is_none()
    {
        return Err(rejected("Campaign narrative state requires an active DM session"));
    }
    Ok(context)
}

/// `_optional_dm_structured_plan(session_id)`: the DM context and its structured plan, empty
/// when there is no accepted one.
pub(super) fn optional_dm_structured_plan(
    store: &AuthorityStore,
    session_id: &str,
) -> StoreResult<(SessionContext, JsonMap)> {
    let context = active_dm_context(store, session_id)?;
    let campaign_id = context.session.campaign_id().expect("an active DM session has a campaign").to_owned();
    let Some(accepted) = store.accepted_campaign_plan(&campaign_id)? else {
        return Ok((context, JsonMap::new()));
    };
    match accepted.content().get("campaign_plan") {
        None => Ok((context, JsonMap::new())),
        Some(Json::Object(plan)) => Ok((context, plan.clone())),
        Some(_) => Err(rejected("Campaign narrative state requires a structured plan")),
    }
}

/// Python's `set` of hashable JSON identities: membership as Python's `==` and hash give it.
fn contains(seen: &[Json], value: &Json) -> bool {
    seen.iter().any(|known| sylvia_domain::pyops::py_eq(known, value))
}

/// `_retained_preparation(campaign_id, plan)`: during an accepted sequel, the earlier adventures'
/// structure, objectives and secrets stay usable.
pub(super) fn retained_preparation(
    store: &AuthorityStore,
    campaign_id: &str,
    plan: &JsonMap,
) -> StoreResult<JsonMap> {
    let Some(current) = store.accepted_campaign_plan(campaign_id)? else { return Ok(plan.clone()) };
    let sequel = matches!(current.content().get("sequel"), Some(Json::Object(_)));
    let current_plan = current.content().get("campaign_plan").cloned().unwrap_or(Json::Null);
    if !sequel || canonical_json_bytes(&Json::Object(plan.clone())) != canonical_json_bytes(&current_plan) {
        return Ok(plan.clone());
    }
    let mut result = plan.clone();
    let adventures = store.campaign_adventures(campaign_id)?;
    for field in ["structure", "objectives", "secrets"] {
        let mut values = Vec::new();
        let mut setting_fact_ids: Vec<Json> = Vec::new();
        for adventure in &adventures {
            let story = match adventure.content().get("campaign_plan") {
                Some(Json::Object(story)) => story,
                _ => {
                    return Err(StoreError::Other(
                        "TypeError: an adventure has no campaign plan mapping".into(),
                    ))
                }
            };
            let items = match story.get(field) {
                None => Vec::new(),
                Some(Json::Array(items)) => items.clone(),
                Some(_) => return Err(StoreError::Other(format!("TypeError: {field} is not a sequence"))),
            };
            for item in items {
                if let Json::Object(map) = &item {
                    if let Some(id) = map.get("setting_fact_id") {
                        if contains(&setting_fact_ids, id) {
                            continue;
                        }
                        setting_fact_ids.push(id.clone());
                    }
                }
                values.push(item);
            }
        }
        result.insert(field.into(), Json::Array(values));
    }
    Ok(owned_payload(&Json::Object(result))?)
}

/// `_exact_setting_knowledge(identity)`: one exact bounded Setting artifact's facts and private
/// references.
fn exact_setting_knowledge(
    store: &AuthorityStore,
    identity: (&str, &str, &str),
) -> StoreResult<(Vec<JsonMap>, Vec<String>)> {
    let Some(value) = store.package_file_bytes(
        identity.0,
        identity.1,
        identity.2,
        CAMPAIGN_SETTING_KNOWLEDGE_PATH,
        CAMPAIGN_SETTING_KNOWLEDGE_BYTE_LIMIT as i64,
    )?
    else {
        return Ok((Vec::new(), Vec::new()));
    };
    let knowledge =
        parse_campaign_setting_knowledge_bytes(&value).map_err(|error| match error.rejection() {
            Some(coded) if coded.error_type == Some("ValueError") => {
                rejected("Campaign Setting knowledge is invalid")
            }
            _ => error.into(),
        })?;
    let facts = knowledge
        .facts
        .iter()
        .map(|fact| {
            let mut payload = JsonMap::new();
            payload.insert("id".into(), fact.identity.as_str().into());
            payload.insert("fact".into(), fact.fact.as_str().into());
            payload
                .insert("known_by".into(), fact.known_by.iter().map(|id| Json::from(id.as_str())).collect());
            payload.insert("source".into(), "campaign_setting".into());
            payload.insert("source_path".into(), fact.source_path.as_str().into());
            payload.insert("selected_by_plan".into(), false.into());
            payload.insert("reveal".into(), Json::Null);
            payload
        })
        .collect();
    Ok((facts, knowledge.private_references.clone()))
}

/// `_campaign_setting_knowledge(campaign_id, plan)`: the Setting knowledge the plan binds, read
/// from the locked package (or its dependency-only rebind).
pub(super) fn campaign_setting_knowledge(
    store: &AuthorityStore,
    campaign_id: &str,
    plan: &JsonMap,
) -> StoreResult<(Vec<JsonMap>, Vec<String>)> {
    let binding = match plan.get("bindings") {
        Some(Json::Object(bindings)) => bindings.get("campaign_setting"),
        _ => None,
    };
    let Some(Json::Object(binding)) = binding else { return Ok((Vec::new(), Vec::new())) };
    let text = |key: &str| binding.get(key).and_then(Json::as_str).map(str::to_owned);
    let (Some(package_id), Some(mut version), Some(mut content_sha256)) =
        (text("id"), text("version"), text("content_sha256"))
    else {
        return Err(rejected("Campaign Setting knowledge binding is invalid"));
    };
    let snapshot = store.snapshot(campaign_id)?;
    let entries = snapshot.state.package_lock.entries();
    let locked = |version: &str, sha: &str| {
        entries.iter().any(|entry| {
            (entry.package_id(), entry.version(), entry.sha256()) == (package_id.as_str(), version, sha)
        })
    };
    if !locked(&version, &content_sha256) {
        if let Some(compatible) =
            store.compatible_package_binding(campaign_id, (&package_id, &version, &content_sha256))?
        {
            if entries.contains(&compatible) {
                version = compatible.version().to_owned();
                content_sha256 = compatible.sha256().to_owned();
            }
        }
    }
    if !locked(&version, &content_sha256) {
        let replacements: Vec<_> = entries
            .iter()
            .filter(|entry| entry.package_id() == package_id && entry.version() == version)
            .collect();
        let historical = store.package_lock_lineage(campaign_id)?.iter().any(|record| {
            record.package_lock.entries().iter().any(|entry| {
                (entry.package_id(), entry.version(), entry.sha256())
                    == (package_id.as_str(), version.as_str(), content_sha256.as_str())
            })
        });
        if replacements.len() != 1 || !historical {
            return Err(rejected("Campaign Setting knowledge binding is stale"));
        }
        let original = store.exact_package_manifest(&package_id, &version, &content_sha256)?;
        let replacement = store.exact_package_manifest(&package_id, &version, replacements[0].sha256())?;
        if !dependency_only_manifest_rebind(&original.canonical_json, &replacement.canonical_json)? {
            return Err(rejected("Campaign Setting knowledge binding is stale"));
        }
        // Read the equivalent current artifact without rewriting the accepted Plan, historical
        // lock, Builder handoff or previously pinned package.
        content_sha256 = replacements[0].sha256().to_owned();
    }
    exact_setting_knowledge(store, (&package_id, &version, &content_sha256))
}

/// `_campaign_secret_catalog(campaign_id, plan)`: the plan's inline secrets merged with the exact
/// Setting facts it references, then the remaining Setting facts by identity.
pub(super) fn campaign_secret_catalog(
    store: &AuthorityStore,
    campaign_id: &str,
    plan: &JsonMap,
) -> StoreResult<Vec<JsonMap>> {
    let plan = retained_preparation(store, campaign_id, plan)?;
    let (setting_facts, _) = campaign_setting_knowledge(store, campaign_id, &plan)?;
    // `{str(item["id"]): item ...}`, sorted for the remainder.
    let mut remaining: BTreeMap<String, JsonMap> = BTreeMap::new();
    for fact in setting_facts {
        let id = sylvia_domain::pyops::py_str(&fact["id"]);
        remaining.insert(id, fact);
    }
    let mut merged = Vec::new();
    if let Some(Json::Array(raw_secrets)) = plan.get("secrets") {
        for (index, raw_secret) in raw_secrets.iter().enumerate() {
            if let Json::String(text) = raw_secret {
                let mut payload = JsonMap::new();
                payload.insert("id".into(), format!("legacy-secret-{index}").into());
                payload.insert("fact".into(), text.as_str().into());
                payload.insert("known_by".into(), Json::Array(Vec::new()));
                payload.insert("source".into(), "campaign_plan".into());
                payload.insert("source_path".into(), Json::Null);
                payload.insert("selected_by_plan".into(), true.into());
                payload.insert("reveal".into(), Json::Null);
                merged.push(payload);
                continue;
            }
            let Json::Object(raw_secret) = raw_secret else { continue };
            let Some(Json::String(secret_id)) = raw_secret.get("id") else { continue };
            if let Some(Json::String(setting_fact_id)) = raw_secret.get("setting_fact_id") {
                let base = remaining.remove(setting_fact_id);
                let Some(mut base) = base.filter(|_| setting_fact_id == secret_id) else {
                    return Err(rejected("Campaign Plan references unavailable Campaign Setting knowledge"));
                };
                base.insert("selected_by_plan".into(), true.into());
                base.insert("reveal".into(), raw_secret.get("reveal").cloned().unwrap_or(Json::Null));
                merged.push(owned_payload(&Json::Object(base))?);
                continue;
            }
            if remaining.contains_key(secret_id) {
                return Err(rejected("Campaign Plan secret collides with Campaign Setting knowledge"));
            }
            let mut payload = raw_secret.clone();
            payload.insert("source".into(), "campaign_plan".into());
            payload.insert("source_path".into(), Json::Null);
            payload.insert("selected_by_plan".into(), true.into());
            merged.push(owned_payload(&Json::Object(payload))?);
        }
    }
    merged.extend(remaining.into_values());
    Ok(merged)
}

/// The catalog both readers return: the merged secrets and the private Setting references.
fn knowledge_catalog(store: &AuthorityStore, campaign_id: &str, plan: &JsonMap) -> StoreResult<JsonMap> {
    let secrets = campaign_secret_catalog(store, campaign_id, plan)?;
    let (_, private_references) = campaign_setting_knowledge(store, campaign_id, plan)?;
    let mut catalog = JsonMap::new();
    catalog.insert("secrets".into(), Json::Array(secrets.into_iter().map(Json::Object).collect()));
    catalog.insert(
        "private_setting_references".into(),
        private_references.into_iter().map(Json::from).collect(),
    );
    Ok(owned_payload(&Json::Object(catalog))?)
}

/// `AuthorityOwner.campaign_knowledge_catalog(session_id)`: the merged private facts and the
/// private Setting reference index of the DM session's campaign.
pub async fn campaign_knowledge_catalog(store: &StoreHandle, session_id: &str) -> StoreResult<JsonMap> {
    let _operation = store.operation()?;
    let id = session_id.to_owned();
    store
        .call(move |store| {
            let (context, plan) = optional_dm_structured_plan(store, &id)?;
            let campaign_id =
                context.session.campaign_id().expect("an active DM session has a campaign").to_owned();
            knowledge_catalog(store, &campaign_id, &plan)
        })
        .await
}

/// `AuthorityOwner.campaign_knowledge_catalog_for_plan(campaign_id, plan)`: the exact opening
/// knowledge, before the DM session is admitted.
pub async fn campaign_knowledge_catalog_for_plan(
    store: &StoreHandle,
    campaign_id: &str,
    plan: &JsonMap,
) -> StoreResult<JsonMap> {
    let _operation = store.operation()?;
    let (id, plan) = (campaign_id.to_owned(), plan.clone());
    store.call(move |store| knowledge_catalog(store, &id, &plan)).await
}

/// `_decision_scope_declaration(snapshot)`: the actor-control metadata the exact locked primary
/// package declares, read from its runtime (lane 2's `package_model_decision_boundary`).
///
/// Python reuses runtime bytes `_package_tool` cached for the catalog; a cached copy is the same
/// immutable bytes, so reading them here gives the same answer.
pub fn decision_scope_declaration(
    store: &AuthorityStore,
    state: &sylvia_domain::authority::CampaignState,
) -> StoreResult<Option<JsonMap>> {
    use crate::package_runtime::{
        package_model_decision_boundary, PackageRuntimeFailure, PACKAGE_RUNTIME_BYTE_LIMIT,
        PACKAGE_RUNTIME_PATH,
    };
    let Some(primary) =
        state.package_lock.entries().iter().find(|entry| entry.role() == PackageRole::Primary)
    else {
        return Ok(None);
    };
    let Some(runtime_bytes) = store.package_file_bytes(
        primary.package_id(),
        primary.version(),
        primary.sha256(),
        PACKAGE_RUNTIME_PATH,
        PACKAGE_RUNTIME_BYTE_LIMIT as i64,
    )?
    else {
        return Ok(None);
    };
    match package_model_decision_boundary(primary.package_id(), &runtime_bytes) {
        Ok(declared) => Ok(declared),
        Err(PackageRuntimeFailure::Rejected(_)) => Err(rejected("Ruleset decision boundary is invalid")),
        Err(PackageRuntimeFailure::Uncaught { class, message }) => {
            Err(StoreError::Other(format!("{class}: {message}")))
        }
    }
}

/// `AuthorityOwner.model_context_owner(campaign_id)`: the GM's native history owner, when the
/// campaign's package opts into isolated participants.
pub async fn model_context_owner(store: &StoreHandle, campaign_id: &str) -> StoreResult<Option<JsonMap>> {
    let _operation = store.operation()?;
    let id = campaign_id.to_owned();
    store
        .call(move |store| {
            let snapshot = store.display_snapshot(&id, &sylvia_store::snapshots::DisplayQuery::default())?;
            let declared = decision_scope_declaration(store, &snapshot.state)?;
            if !declared.is_some_and(|declared| declared.contains_key("isolated_participant")) {
                return Ok(None);
            }
            let primary = snapshot
                .state
                .package_lock
                .entries()
                .iter()
                .find(|entry| entry.role() == PackageRole::Primary)
                .expect("a declaration comes from the primary package");
            let mut owner = JsonMap::new();
            owner.insert("kind".into(), "gm".into());
            owner.insert("campaign_id".into(), id.as_str().into());
            owner.insert("package_id".into(), primary.package_id().into());
            owner.insert("package_version".into(), primary.version().into());
            owner.insert("package_sha256".into(), primary.sha256().into());
            Ok(Some(owned_payload(&Json::Object(owner))?))
        })
        .await
}

#[cfg(test)]
mod tool_cache_tests {
    use super::package_tool;
    use std::io::Read;
    use sylvia_domain::authority::PackageLockEntry;
    use sylvia_store::AuthorityStore;

    fn folder(name: &str) -> std::path::PathBuf {
        std::env::temp_dir().join(format!("sylvia-app-tool-cache-{name}-{}", std::process::id()))
    }

    /// The Riichi game in play, opened from the committed snapshot vector.
    fn riichi(name: &str) -> AuthorityStore {
        let folder = folder(name);
        let _ = std::fs::remove_dir_all(&folder);
        std::fs::create_dir_all(&folder).unwrap();
        let vector = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/vectors/snapshots/riichi_started.sqlite3.gz");
        let mut bytes = Vec::new();
        flate2::read::GzDecoder::new(std::fs::File::open(vector).unwrap()).read_to_end(&mut bytes).unwrap();
        let database = folder.join("authority.sqlite3");
        std::fs::write(&database, bytes).unwrap();
        AuthorityStore::open_for_application_owner(&database, None).unwrap()
    }

    #[test]
    fn a_tool_is_materialized_once_per_exact_package_identity() {
        let store = riichi("hit");
        let basis = store.package_catalog_basis("campaign-table").unwrap();
        let (entry, declaration) = basis.declarations[0].clone();
        let first =
            package_tool(&store, &entry, &declaration).unwrap().expect("the Riichi tool materializes");
        let again = package_tool(&store, &entry, &declaration).unwrap().unwrap();
        // A fresh materialization is a different mechanic (mechanics compare by identity).
        assert_eq!(first.execute, again.execute, "the second read must be the kept tool");
        store.tool_cache().with(|cache| {
            assert_eq!(cache.tools.len(), 1);
            assert_eq!(cache.runtime_payloads.len(), 1);
        });
        // A sibling tool reuses the kept runtime bytes.
        let (sibling_entry, sibling) = basis
            .declarations
            .iter()
            .find(|(other, tool)| other.sha256() == entry.sha256() && tool.name != declaration.name)
            .cloned()
            .expect("Riichi declares more than one tool");
        let sibling_tool = package_tool(&store, &sibling_entry, &sibling).unwrap().unwrap();
        assert_ne!(sibling_tool.execute, first.execute);
        store.tool_cache().with(|cache| {
            assert_eq!(cache.tools.len(), 2);
            assert_eq!(cache.runtime_payloads.len(), 1);
        });
        drop(store);
        let _ = std::fs::remove_dir_all(folder("hit"));
    }

    #[test]
    fn another_sha256_under_the_same_package_id_and_version_misses() {
        let store = riichi("miss");
        let basis = store.package_catalog_basis("campaign-table").unwrap();
        let (entry, declaration) = basis.declarations[0].clone();
        assert!(package_tool(&store, &entry, &declaration).unwrap().is_some());
        let other_sha256 = if entry.sha256().starts_with('0') { "1" } else { "0" }.repeat(64);
        let other = PackageLockEntry::new(
            entry.package_id(),
            entry.version(),
            other_sha256,
            entry.role(),
            entry.load_order(),
            entry.dependencies().to_vec(),
            entry.capabilities().to_vec(),
        )
        .unwrap();
        // No package has that digest, so a miss reads no runtime and finds no tool.
        assert!(package_tool(&store, &other, &declaration).unwrap().is_none());
        store.tool_cache().with(|cache| assert_eq!(cache.tools.len(), 1));
        drop(store);
        let _ = std::fs::remove_dir_all(folder("miss"));
    }
}
