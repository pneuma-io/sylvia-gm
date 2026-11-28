//! Campaign commands: `interfaces/commands/campaigns.py`.

use crate::dispatch::DispatchContext;
use sylvia_base::{CodedResult, Json, JsonMap};

/// `handle_handshake_status`: `ctx.bus.handshake_status()`, the supervisor's view of the
/// launch handshake (`LocalCommandBus.handshake_status` in `interfaces/local_api.py`).
pub async fn handle_handshake_status(ctx: &DispatchContext, _params: &JsonMap) -> CodedResult<Json> {
    let verified = ctx.handshake_verified;
    let mut status = JsonMap::new();
    status.insert("verified".into(), verified.into());
    status.insert("role".into(), if verified { "owner" } else { "pending" }.into());
    status.insert(
        "reason".into(),
        if verified { "launch handshake verified" } else { "handshake pending" }.into(),
    );
    Ok(Json::Object(status))
}

/// A store failure as a handler's error: a rejection Python's owner catches keeps its code,
/// message and class; behaviour the port has not reached yet (`StoreError::NotPorted`) answers
/// `rust_port_pending`, as every unported branch does; anything else is the owner's internal
/// error.
pub(crate) fn store_error(error: sylvia_store::StoreError) -> sylvia_base::CodedError {
    if let sylvia_store::StoreError::NotPorted(what) = &error {
        return sylvia_base::CodedError::new(
            crate::dispatch::PORT_PENDING,
            format!("not yet ported: {what}"),
        );
    }
    error.rejection().unwrap_or_else(|| sylvia_base::CodedError::new("internal_error", error.to_string()))
}

/// `handle_create_guided_campaign_draft`: a new Guided Campaign Draft with its owner-assigned
/// Builder session, whose effort and speed are settled before the Draft exists and saved only
/// when this is not a replay.
pub async fn handle_create_guided_campaign_draft(
    ctx: &DispatchContext,
    params: &JsonMap,
) -> CodedResult<Json> {
    if params.contains_key("builder_session_id") {
        return Err(protocol("builder_session_id is owner-assigned"));
    }
    let operation_id = required(params, "operation_id")?;
    let builder_session_id =
        format!("session-guided-{}", &sylvia_base::hash::sha256_hex(operation_id.as_bytes())[..24]);
    let provider_id = required(params, "provider_id")?;
    let model_id = required(params, "model_id")?;
    let start = crate::chat_start::chat_start_for(ctx, "builder", &provider_id, &model_id, params).await?;
    let replayed = crate::chat_start::session_exists(ctx, &builder_session_id).await?;
    let display_name = required(params, "display_name")?;
    let draft = sylvia_app::authority::guided::create_guided_campaign_draft(
        &ctx.store,
        &operation_id,
        &display_name,
        &builder_session_id,
        &provider_id,
        &model_id,
    )
    .await
    .map_err(store_error)?;
    if !replayed {
        crate::chat_start::apply_chat_start(ctx, &draft.builder_session_id, &start).await?;
    }
    Ok(guided_draft_value(&draft))
}

/// `handle_advance_guided_campaign_draft`: one Campaign Interview answer, which must be an
/// object, then the application's advance.
pub async fn handle_advance_guided_campaign_draft(
    ctx: &DispatchContext,
    params: &JsonMap,
) -> CodedResult<Json> {
    let answer = match params.get("answer") {
        Some(answer @ Json::Object(_)) => answer,
        _ => return Err(protocol("answer must be an object")),
    };
    let operation_id = required(params, "operation_id")?;
    let campaign_id = required(params, "campaign_id")?;
    let builder_session_id = required(params, "builder_session_id")?;
    let expected_construction_revision = required_nonnegative_int(params, "expected_construction_revision")?;
    let current_step = required(params, "current_step")?;
    let visible_answer = required(params, "visible_answer")?;
    let draft = sylvia_app::authority::guided::advance_guided_campaign_draft(
        &ctx.store,
        &ctx.bundled,
        &sylvia_app::authority::guided::AdvanceAnswer {
            operation_id: &operation_id,
            campaign_id: &campaign_id,
            builder_session_id: &builder_session_id,
            expected_construction_revision,
            current_step: &current_step,
            visible_answer: &visible_answer,
            answer,
        },
    )
    .await
    .map_err(store_error)?;
    Ok(guided_draft_value(&draft))
}

/// `_MAX_USER_TEXT_CHARS`.
const MAX_USER_TEXT_CHARS: usize = 131_072;
/// `_MAX_CAMPAIGN_PLAN_BYTES`.
const MAX_CAMPAIGN_PLAN_BYTES: usize = 1024 * 1024;

/// Python's truthiness of a JSON value.
fn truthy(value: Option<&Json>) -> bool {
    match value {
        None | Some(Json::Null) => false,
        Some(Json::Bool(flag)) => *flag,
        Some(Json::Number(number)) => number.as_f64().is_some_and(|value| value != 0.0),
        Some(Json::String(text)) => !text.is_empty(),
        Some(Json::Array(items)) => !items.is_empty(),
        Some(Json::Object(map)) => !map.is_empty(),
    }
}

fn rejected(message: &str) -> sylvia_base::CodedError {
    sylvia_base::CodedError::rejected(message).with_error_type("CommandRejected")
}

fn result_value(campaign_id: &str, builder_session_id: &str, resulting_revision: i64) -> Json {
    let mut value = JsonMap::new();
    value.insert("campaign_id".into(), campaign_id.into());
    value.insert("builder_session_id".into(), builder_session_id.into());
    value.insert("resulting_revision".into(), resulting_revision.into());
    Json::Object(value)
}

/// `_guided_start_selection_input`: the selected content without the participant IDs each
/// Start attempt generates.
fn guided_start_selection_input(payload: &JsonMap) -> Json {
    let mut selection = payload.clone();
    if let Some(Json::Array(party)) = selection.get("selected_party").cloned() {
        let party = party
            .into_iter()
            .map(|member| match member {
                Json::Object(mut member) => {
                    member.remove("participant_id");
                    Json::Object(member)
                }
                other => other,
            })
            .collect();
        selection.insert("selected_party".into(), Json::Array(party));
    }
    Json::Object(selection)
}

/// `_seed_campaign_plan(storage_root, campaign_id, text)`: create `campaign-plan.md` once, never
/// overwriting a plan the Builder has customised since.
fn seed_campaign_plan(
    storage_root: &std::path::Path,
    campaign_id: &str,
    text: &str,
    structured_plan: Option<&JsonMap>,
) -> CodedResult<()> {
    let encoded = text.as_bytes();
    if encoded.is_empty() || encoded.len() > MAX_CAMPAIGN_PLAN_BYTES {
        return Err(rejected("Campaign plan file exceeds the usability limit"));
    }
    let folder = storage_root.join("campaigns").join(campaign_id);
    let target = folder.join("campaign-plan.md");
    // Python's `mkdir(parents=True, exist_ok=True)`: a failure is an uncaught `OSError`.
    std::fs::create_dir_all(&folder).map_err(|error| {
        sylvia_base::CodedError::new("internal_error", error.to_string()).with_error_type("OSError")
    })?;
    if target.exists() {
        if !target.is_file() {
            return Err(rejected("Campaign plan path is not a regular file"));
        }
    } else {
        write_new_plan(&folder, &target, encoded)?;
    }
    let Some(structured_plan) = structured_plan else {
        return Ok(());
    };
    let structured_target = target.with_extension("json");
    let structured_bytes =
        sylvia_domain::package_library::canonical_json_bytes(&Json::Object(structured_plan.clone()));
    if structured_bytes.len() > MAX_CAMPAIGN_PLAN_BYTES {
        return Err(rejected("Structured Campaign Plan exceeds the usability limit"));
    }
    if structured_target.exists() {
        if !structured_target.is_file() {
            return Err(rejected("Structured Campaign Plan path is not a regular file"));
        }
        return Ok(());
    }
    std::fs::write(&structured_target, structured_bytes)
        .map_err(|_| rejected("Structured Campaign Plan could not be created"))
}

/// The atomic write of a new `campaign-plan.md`.
fn write_new_plan(folder: &std::path::Path, target: &std::path::Path, encoded: &[u8]) -> CodedResult<()> {
    use std::io::Write;
    let nonce =
        std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0, |time| time.as_nanos());
    let temporary = folder.join(format!(".campaign-plan-{}-{nonce}.tmp", std::process::id()));
    let written = (|| -> std::io::Result<()> {
        let mut file = std::fs::OpenOptions::new().write(true).create_new(true).open(&temporary)?;
        file.write_all(encoded)?;
        file.flush()?;
        file.sync_all()?;
        drop(file);
        std::fs::rename(&temporary, target)
    })();
    if written.is_err() {
        let _ = std::fs::remove_file(&temporary);
        return Err(rejected("Campaign plan file could not be created"));
    }
    Ok(())
}

/// The No Ruleset Campaign Plan `handle_start_guided_campaign` seeds.
fn no_ruleset_plan(display_name: &str, premise: &str) -> String {
    let setup = if premise.is_empty() { "No starting premise supplied." } else { premise };
    format!(
        "# {display_name}\n\n\
         This is an open-ended narrative sandbox with no Ruleset or game mechanics. \
         Use the player's setup below as the starting brief. When it leaves the \
         character or opening situation unspecified, ask the player before establishing \
         those details. Preserve player choices and boundaries.\n\n\
         ## Player setup\n\n{setup}\n"
    )
}

/// `handle_campaign_party_options`: a saved Plan's party choices, revalidated through lane 1's
/// library selection (`resolve_campaign_library_selection(...).party_options()`) in one store
/// call.
pub async fn handle_campaign_party_options(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let ruleset_selection = required(params, "ruleset_selection")?;
    let setting_id = required(params, "setting_id")?;
    let plan_id = required(params, "plan_id")?;
    let bundled = std::sync::Arc::clone(&ctx.bundled);
    let storage_root = ctx.storage_root.clone();
    let options = ctx
        .store
        .call(move |store| {
            let selection = sylvia_app::campaign_library_selection::resolve_campaign_library_selection(
                store,
                &bundled,
                &storage_root,
                &ruleset_selection,
                &setting_id,
                &plan_id,
            )?;
            selection.party_options(&bundled).map_err(sylvia_store::StoreError::from)
        })
        .await
        .map_err(store_error)?;
    Ok(Json::Object(options))
}

/// What a saved-Plan Start resolves in one store call: the library selection (lane 1's
/// `resolve_campaign_library_selection`), the candidate's lock and the materialized party.
struct SavedPlanStart {
    candidate_id: String,
    package_lock: sylvia_domain::authority::PackageLock,
    plan_id: String,
    plan_content_sha256: String,
    setting_id: String,
    participants: Vec<sylvia_domain::authority::Participant>,
    initial_state: Vec<sylvia_domain::authority::StateValue>,
    selected_party: Vec<Json>,
    selected_plan: sylvia_domain::campaign_plan::ValidatedCampaignPlan,
    companion_ids: Json,
    intent: JsonMap,
}

/// `premise` as Python reads it: absent, or bounded text.
fn start_premise(params: &JsonMap) -> CodedResult<String> {
    match params.get("premise") {
        None => Ok(String::new()),
        Some(Json::String(text)) if text.chars().count() <= MAX_USER_TEXT_CHARS => Ok(text.clone()),
        Some(_) => Err(protocol("premise must be a bounded string")),
    }
}

/// The saved-Plan half of `handle_start_guided_campaign`, in Python's order inside one store call
/// (the selection borrows the store): the selection, the candidate's lock, the premise, the
/// companions, the construction intent, then the party (a party template's, or the library's
/// with the Plan and the player's instructions).
fn saved_plan_start(
    store: &sylvia_store::AuthorityStore,
    bundled: &sylvia_app::bundled_packages::BundledPackages,
    storage_root: &std::path::Path,
    operation_id: &str,
    params: &JsonMap,
) -> sylvia_store::StoreResult<CodedResult<SavedPlanStart>> {
    use sylvia_app::campaign_library_selection as library;
    let (candidate, setting_id, plan_id) =
        match (required(params, "candidate_id"), required(params, "setting_id"), required(params, "plan_id"))
        {
            (Ok(candidate), Ok(setting_id), Ok(plan_id)) => (candidate, setting_id, plan_id),
            (Err(error), _, _) | (_, Err(error), _) | (_, _, Err(error)) => return Ok(Err(error)),
        };
    let selection = library::resolve_campaign_library_selection(
        store,
        bundled,
        storage_root,
        &candidate,
        &setting_id,
        &plan_id,
    )?;
    let candidate_id = selection.candidate.candidate_id().to_owned();
    let package_lock = store.package_candidate(&candidate_id)?.package_lock().clone();
    if let Err(error) = start_premise(params) {
        return Ok(Err(error));
    }
    let empty = Json::Array(Vec::new());
    let raw_companions = params.get("companion_ids").unwrap_or(&empty);
    let companions: Option<Vec<String>> = match raw_companions {
        Json::Array(items) if items.len() <= 64 => items
            .iter()
            .map(|item| item.as_str().filter(|text| !text.is_empty()).map(str::to_owned))
            .collect(),
        _ => None,
    };
    let Some(companion_ids) = companions else {
        return Ok(Err(protocol("companion_ids must contain bounded character identities")));
    };
    // `params.get("construction_intent") or {}`.
    let intent = match params.get("construction_intent") {
        Some(value) if truthy(Some(value)) => match value {
            Json::Object(map) => map.clone(),
            _ => return Ok(Err(protocol("construction_intent must be an object"))),
        },
        _ => JsonMap::new(),
    };
    let player_character_id = match required(params, "player_character_id") {
        Ok(id) => id,
        Err(error) => return Ok(Err(error)),
    };
    let (participants, initial_state, selected_party, selected_plan) = if selection.party_template.is_some() {
        library::materialize_template_party(
            store,
            bundled,
            &selection,
            operation_id,
            &player_character_id,
            &companion_ids,
            &intent,
        )?
    } else {
        let (participants, initial_state, selected_party) = library::materialize_library_party(
            store,
            &selection,
            operation_id,
            &player_character_id,
            &companion_ids,
        )?;
        let plan = library::plan_with_interview_intent(&selection, &intent, None)?;
        (participants, initial_state, selected_party, plan)
    };
    Ok(Ok(SavedPlanStart {
        candidate_id,
        package_lock,
        plan_id: selection.plan.artifact_id.clone(),
        plan_content_sha256: selection.plan.content_sha256.clone(),
        setting_id: selection.sources.setting.package_id.clone(),
        participants,
        initial_state,
        selected_party,
        selected_plan,
        companion_ids: raw_companions.clone(),
        intent,
    }))
}

/// `handle_start_guided_campaign`: a Draft with no Ruleset, an installed one, or a saved Campaign
/// Plan (`plan_id`: lane 1's library selection and party, bound with its participants and initial
/// state, and seeded with the Plan's Markdown and structured Plan).
pub async fn handle_start_guided_campaign(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let operation_id = required(params, "operation_id")?;
    let campaign_id = required(params, "campaign_id")?;
    let builder_session_id = required(params, "builder_session_id")?;
    let mut candidate_id = params.get("candidate_id").cloned().unwrap_or(Json::Null);
    let saved = if matches!(params.get("plan_id"), None | Some(Json::Null)) {
        None
    } else {
        let (bundled, storage_root, operation, request) = (
            std::sync::Arc::clone(&ctx.bundled),
            ctx.storage_root.clone(),
            operation_id.clone(),
            params.clone(),
        );
        let start = ctx
            .store
            .call(move |store| saved_plan_start(store, &bundled, &storage_root, &operation, &request))
            .await
            .map_err(store_error)??;
        candidate_id = Json::String(start.candidate_id.clone());
        Some(start)
    };
    let package_lock = match (&saved, &candidate_id) {
        (Some(start), _) => start.package_lock.clone(),
        (None, candidate_id) => match candidate_id {
            Json::Null => sylvia_domain::authority::PackageLock::new(Vec::new())
                .map_err(|error| sylvia_base::CodedError::new("internal_error", error.to_string()))?,
            Json::String(id) if !id.is_empty() => {
                let id = id.clone();
                let candidate =
                    ctx.store.call(move |store| store.package_candidate(&id)).await.map_err(store_error)?;
                candidate.package_lock().clone()
            }
            _ => return Err(protocol("candidate_id must be a nonempty string or null")),
        },
    };
    let premise = start_premise(params)?;
    let premise = sylvia_base::pystr::strip(&premise).to_owned();
    let mut canonical_input = JsonMap::new();
    if !premise.is_empty() {
        canonical_input.insert("premise".into(), premise.as_str().into());
    }
    if let Some(start) = &saved {
        let mut player_character = JsonMap::new();
        player_character.insert("archetype_id".into(), params["player_character_id"].clone());
        canonical_input.insert("plan_id".into(), start.plan_id.as_str().into());
        canonical_input.insert("plan_content_sha256".into(), start.plan_content_sha256.as_str().into());
        canonical_input.insert("setting_id".into(), start.setting_id.as_str().into());
        canonical_input.insert("player_character".into(), Json::Object(player_character));
        canonical_input.insert("companion_ids".into(), start.companion_ids.clone());
        canonical_input.insert(
            "starting_party_policy".into(),
            start.selected_plan.payload.get("starting_party").cloned().unwrap_or(Json::Null),
        );
        canonical_input.insert("selected_party".into(), Json::Array(start.selected_party.clone()));
        canonical_input.insert("construction_intent".into(), Json::Object(start.intent.clone()));
    } else if truthy(params.get("player_character_id")) || truthy(params.get("companion_ids")) {
        return Err(rejected("Selected characters require a concrete Campaign Plan"));
    }
    let display_name = required(params, "display_name")?;
    let package_candidate_id = candidate_id.as_str().map(str::to_owned);
    match sylvia_app::authority::guided::campaign_construction_basis(&ctx.store, &campaign_id).await {
        Ok(existing) => {
            if existing.builder_session_id != builder_session_id
                || existing.display_name != display_name
                || existing.package_candidate_id != package_candidate_id
                || !sylvia_domain::pyops::py_eq(
                    &guided_start_selection_input(&existing.canonical_input),
                    &guided_start_selection_input(&canonical_input),
                )
            {
                return Err(rejected(
                    "The interrupted guided Start basis differs from the current selections",
                ));
            }
            return Ok(result_value(&campaign_id, &builder_session_id, existing.begin_revision));
        }
        Err(error) => {
            let coded = store_error(error);
            let resumable = coded.error_type == Some("CommandRejected")
                && matches!(
                    coded.message.as_str(),
                    "Confirmed campaign construction is unavailable" | "Campaign construction has not begun"
                );
            if !resumable {
                return Err(coded);
            }
        }
    }
    let digest = sylvia_base::hash::sha256_hex(operation_id.as_bytes())[..24].to_owned();
    let mut action = JsonMap::new();
    action.insert("action".into(), "ready_to_begin".into());
    let mut ready = JsonMap::new();
    ready.insert("text".into(), "Ready to Begin".into());
    ready.insert("interview_step".into(), "ready".into());
    ready.insert("interview_value".into(), Json::Object(action));
    ctx.store
        .append_agent_message(
            &builder_session_id,
            &format!("guided-ready-{digest}"),
            sylvia_domain::session::SessionMessageKind::User,
            &ready,
        )
        .await
        .map_err(store_error)?;
    let confirmation = sylvia_app::authority::guided::bind_guided_campaign_basis(
        &ctx.store,
        &ctx.bundled,
        &sylvia_app::authority::guided::BindRequest {
            operation_id: &format!("{operation_id}-bind"),
            campaign_id: &campaign_id,
            display_name: &display_name,
            package_lock: &package_lock,
            turn_id: &format!("turn-construction-{digest}"),
            builder_session_id: &builder_session_id,
            package_candidate_id: package_candidate_id.as_deref(),
            participants: saved.as_ref().map_or(&[], |start| start.participants.as_slice()),
            initial_state: saved.as_ref().map_or(&[], |start| start.initial_state.as_slice()),
        },
    )
    .await
    .map_err(store_error)?;
    if let Some(start) = &saved {
        let plan = &start.selected_plan;
        seed_campaign_plan(&ctx.storage_root, &campaign_id, &plan.readable_markdown, Some(&plan.payload))?;
    } else if package_candidate_id.is_none() {
        seed_campaign_plan(&ctx.storage_root, &campaign_id, &no_ruleset_plan(&display_name, &premise), None)?;
    }
    let begun = sylvia_app::authority::guided::begin_campaign_construction(
        &ctx.store,
        &format!("{operation_id}-begin"),
        &campaign_id,
        if premise.is_empty() { "quick_start" } else { "customize" },
        confirmation.resulting_revision,
        &Json::Object(canonical_input),
    )
    .await
    .map_err(store_error)?;
    Ok(result_value(&campaign_id, &builder_session_id, begun.resulting_revision))
}

/// The reply of the lifecycle commands: `_json_value(CampaignLifecycleResult)`, the dataclass
/// fields in declaration order.
fn lifecycle_value(result: &sylvia_domain::authority::CampaignLifecycleResult) -> Json {
    let mut value = JsonMap::new();
    value.insert("campaign_id".into(), result.campaign_id.as_str().into());
    value.insert("lifecycle".into(), result.lifecycle.as_str().into());
    value.insert("writer".into(), result.writer.as_str().into());
    value.insert("resulting_revision".into(), result.resulting_revision.into());
    value.insert("session_id".into(), result.session_id.clone().into());
    Json::Object(value)
}

/// `handle_rename_campaign`: `ctx.bus.rename_campaign(...)`.
pub async fn handle_rename_campaign(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let operation_id = required(params, "operation_id")?;
    let campaign_id = required(params, "campaign_id")?;
    let display_name = required(params, "display_name")?;
    let expected_revision = required_nonnegative_int(params, "expected_revision")?;
    let result = sylvia_app::authority::lifecycle::rename_campaign(
        &ctx.store,
        &operation_id,
        &campaign_id,
        &display_name,
        expected_revision,
    )
    .await
    .map_err(store_error)?;
    Ok(lifecycle_value(&result))
}

/// `handle_archive_campaign`: `ctx.bus.archive_campaign(...)`.
pub async fn handle_archive_campaign(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let operation_id = required(params, "operation_id")?;
    let campaign_id = required(params, "campaign_id")?;
    let expected_revision = required_nonnegative_int(params, "expected_revision")?;
    let designated_session_id = optional_text(params, "designated_session_id")?;
    let result = sylvia_app::authority::lifecycle::archive_campaign(
        &ctx.store,
        &operation_id,
        &campaign_id,
        expected_revision,
        designated_session_id.as_deref(),
    )
    .await
    .map_err(store_error)?;
    Ok(lifecycle_value(&result))
}

/// `handle_restore_archived_campaign`: `ctx.bus.restore_archived_campaign(...)`, whose session
/// parameter is `session_id`.
pub async fn handle_restore_archived_campaign(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let operation_id = required(params, "operation_id")?;
    let campaign_id = required(params, "campaign_id")?;
    let expected_revision = required_nonnegative_int(params, "expected_revision")?;
    let session_id = optional_text(params, "session_id")?;
    let result = sylvia_app::authority::lifecycle::restore_archived_campaign(
        &ctx.store,
        &operation_id,
        &campaign_id,
        expected_revision,
        session_id.as_deref(),
    )
    .await
    .map_err(store_error)?;
    Ok(lifecycle_value(&result))
}

/// `handle_trash_campaign`: `ctx.bus.trash_campaign(...)`.
pub async fn handle_trash_campaign(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let operation_id = required(params, "operation_id")?;
    let campaign_id = required(params, "campaign_id")?;
    let expected_revision = required_nonnegative_int(params, "expected_revision")?;
    let designated_session_id = optional_text(params, "designated_session_id")?;
    let result = sylvia_app::authority::lifecycle::trash_campaign(
        &ctx.store,
        &operation_id,
        &campaign_id,
        expected_revision,
        designated_session_id.as_deref(),
    )
    .await
    .map_err(store_error)?;
    Ok(lifecycle_value(&result))
}

/// `handle_restore_trashed_campaign`: `ctx.bus.restore_trashed_campaign(...)`.
pub async fn handle_restore_trashed_campaign(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let operation_id = required(params, "operation_id")?;
    let campaign_id = required(params, "campaign_id")?;
    let expected_revision = required_nonnegative_int(params, "expected_revision")?;
    let result = sylvia_app::authority::lifecycle::restore_trashed_campaign(
        &ctx.store,
        &operation_id,
        &campaign_id,
        expected_revision,
    )
    .await
    .map_err(store_error)?;
    Ok(lifecycle_value(&result))
}

/// `handle_purge_campaign`: `application_owner.purge_campaign(...)` with the Builder sources'
/// filesystem half: the campaign workspace's sources are quarantined after the store's preflight,
/// restored if the purge fails, and deleted once it commits (or, for a replay, the quarantine
/// this exact earlier purge left).
pub async fn handle_purge_campaign(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    use sylvia_store::builder_sources::{
        finalize_builder_source_purge, finalize_existing_builder_source_purge,
        restore_builder_sources_after_failed_purge, stage_builder_sources_for_purge, BuilderSourcePurgeStage,
    };
    let campaign_id = required(params, "campaign_id")?;
    let operation_id = required(params, "operation_id")?;
    let workspace = ctx.storage_root.join("campaigns").join(&campaign_id);
    let stage: std::sync::Arc<std::sync::Mutex<Option<BuilderSourcePurgeStage>>> = Default::default();
    let expected_revision = required_nonnegative_int(params, "expected_revision")?;
    let confirmation = required(params, "confirmation")?;
    let mut prepare = {
        let (stage, workspace, campaign_id, operation_id) =
            (std::sync::Arc::clone(&stage), workspace.clone(), campaign_id.clone(), operation_id.clone());
        move || {
            let staged = stage_builder_sources_for_purge(&workspace, &campaign_id, &operation_id)?;
            *stage.lock().unwrap_or_else(std::sync::PoisonError::into_inner) = Some(staged);
            Ok(())
        }
    };
    let mut rollback = {
        let stage = std::sync::Arc::clone(&stage);
        move || match stage.lock().unwrap_or_else(std::sync::PoisonError::into_inner).as_ref() {
            Some(staged) => restore_builder_sources_after_failed_purge(staged),
            None => Ok(()),
        }
    };
    let result = sylvia_app::authority::lifecycle::purge_campaign(
        &ctx.store,
        &operation_id,
        &campaign_id,
        expected_revision,
        &confirmation,
        Some(&mut prepare),
        Some(&mut rollback),
    )
    .await
    .map_err(store_error)?;
    let staged = stage.lock().unwrap_or_else(std::sync::PoisonError::into_inner).clone();
    match staged {
        None => finalize_existing_builder_source_purge(&workspace, &campaign_id, &operation_id),
        Some(staged) => finalize_builder_source_purge(&staged),
    }
    .map_err(store_error)?;
    let mut value = JsonMap::new();
    value.insert("campaign_id".into(), result.campaign_id.as_str().into());
    value.insert("resulting_revision".into(), result.resulting_revision.into());
    Ok(Json::Object(value))
}

/// `handle_export_campaign`, for both `export_campaign` and `export_campaign_file`: one portable
/// archive published at `exports/<export_id>.sylvia-gm-campaign` under the storage root. The
/// file command also answers with the archive's resolved path.
pub async fn handle_export_campaign(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let export_id = required_artifact_id(params, "export_id")?;
    let exports = ctx.storage_root.join("exports");
    let target = exports.join(format!("{export_id}.sylvia-gm-campaign"));
    // Python's `mkdir(parents=True, exist_ok=True)`: a failure is an uncaught `OSError`.
    std::fs::create_dir_all(&exports).map_err(|error| {
        sylvia_base::CodedError::new("internal_error", error.to_string()).with_error_type("OSError")
    })?;
    let operation_id = required(params, "operation_id")?;
    let campaign_id = required(params, "campaign_id")?;
    let include_thinking = optional_bool(params, "include_thinking")?;
    let result = sylvia_app::authority::portable::export_campaign(
        &ctx.store,
        &operation_id,
        &campaign_id,
        &target,
        include_thinking,
    )
    .await
    .map_err(store_error)?;
    let mut value = JsonMap::new();
    value.insert("campaign_id".into(), result.campaign_id.into());
    value.insert("captured_revision".into(), result.captured_revision.into());
    value.insert("archive_sha256".into(), result.archive_sha256.into());
    value.insert("byte_count".into(), result.byte_count.into());
    if ctx.method != "export_campaign_file" {
        return Ok(Json::Object(value));
    }
    // `str(target.resolve(strict=True))`; a failure is an uncaught `OSError`.
    let resolved = resolve_strict(&target).map_err(|error| {
        sylvia_base::CodedError::new("internal_error", error.to_string()).with_error_type("OSError")
    })?;
    let mut reply = JsonMap::new();
    reply.insert("export".into(), Json::Object(value));
    reply.insert("artifact_path".into(), resolved.to_string_lossy().into_owned().into());
    Ok(Json::Object(reply))
}

/// `_campaign_plan_text(storage_root, campaign_id)`: the readable Campaign Plan the Builder
/// wrote, `campaigns/<campaign_id>/campaign-plan.md`.
fn campaign_plan_text(storage_root: &std::path::Path, campaign_id: &str) -> CodedResult<String> {
    let path = storage_root.join("campaigns").join(campaign_id).join("campaign-plan.md");
    if !path.is_file() {
        return Err(rejected("Campaign plan file is missing"));
    }
    let unreadable = |_| rejected("Campaign plan file is not readable");
    if std::fs::metadata(&path).map_err(unreadable)?.len() > MAX_CAMPAIGN_PLAN_BYTES as u64 {
        return Err(rejected("Campaign plan file exceeds the usability limit"));
    }
    let bytes = std::fs::read(&path).map_err(unreadable)?;
    let text = String::from_utf8(bytes).map_err(|_| rejected("Campaign plan file is not valid UTF-8"))?;
    if sylvia_base::pystr::strip(&text).is_empty() {
        return Err(rejected("Campaign plan file is empty or unusable"));
    }
    Ok(text)
}

/// `_campaign_plan_payload(storage_root, campaign_id)`: the optional structured Campaign Plan
/// beside its readable rendering, `campaign-plan.json`.
fn campaign_plan_payload(storage_root: &std::path::Path, campaign_id: &str) -> CodedResult<Option<JsonMap>> {
    let path = storage_root.join("campaigns").join(campaign_id).join("campaign-plan.json");
    // `Path.exists()` follows links and answers false for a broken one.
    if std::fs::metadata(&path).is_err() {
        return Ok(None);
    }
    let unreadable = || rejected("Structured Campaign Plan is not readable");
    let too_large = || rejected("Structured Campaign Plan exceeds the usability limit");
    if !path.is_file() {
        return Err(too_large());
    }
    if std::fs::metadata(&path).map_err(|_| unreadable())?.len() > MAX_CAMPAIGN_PLAN_BYTES as u64 {
        return Err(too_large());
    }
    // `read_text(encoding="utf-8")` translates newlines before `json.loads`; JSON treats a
    // carriage return between tokens as whitespace and refuses one inside a string either way.
    let bytes = std::fs::read(&path).map_err(|_| unreadable())?;
    let text = String::from_utf8(bytes).map_err(|_| unreadable())?;
    match sylvia_base::pyjson::loads(&text) {
        Ok(Json::Object(value)) => Ok(Some(value)),
        Ok(_) => Err(rejected("Structured Campaign Plan must be an object")),
        Err(_) => Err(unreadable()),
    }
}

/// `handle_finalize_campaign_construction`: the guided Interview's Campaign Plan finalized and
/// handed to the Game Master, after the Game Master's effort and speed are settled and the
/// mandatory opening request is measured (lane 4's `preflight_guided_start`), then the DM TURN
/// opened. It answers the owner's [`Failure`](crate::dispatch::Failure), so a provider error
/// keeps Python's provider-error reply.
pub async fn handle_finalize_campaign_construction(
    ctx: &DispatchContext,
    params: &JsonMap,
) -> crate::dispatch::DispatchResult {
    let campaign_id = required(params, "campaign_id")?;
    let dm_provider_id = required(params, "dm_provider_id")?;
    let dm_model_id = required(params, "dm_model_id")?;
    let plan_text = campaign_plan_text(&ctx.storage_root, &campaign_id)?;
    let structured_plan = campaign_plan_payload(&ctx.storage_root, &campaign_id)?;
    // Settle the Game Master effort and speed before the handoff commits, so an unsupported
    // choice is refused while the Interview is still the active chat.
    let start = crate::chat_start::chat_start_for(ctx, "dm", &dm_provider_id, &dm_model_id, params).await?;
    // `ctx.provider_resolver` is always set in the product; without one Python's call fails.
    let resolver = ctx.providers.as_deref().ok_or_else(|| {
        sylvia_base::CodedError::new("internal_error", "provider resolver is unavailable")
            .with_error_type("AttributeError")
    })?;
    let reads: std::sync::Arc<dyn crate::preflight::PreflightReads> =
        std::sync::Arc::new(crate::owner_reads::OwnerReads::new(ctx.store.clone()));
    crate::preflight::preflight_guided_start(
        reads,
        resolver,
        crate::preflight::GuidedStartPreflight {
            owner_id: &ctx.owner_id,
            campaign_id: &campaign_id,
            provider_id: &dm_provider_id,
            model_id: &dm_model_id,
            plan_text: &plan_text,
            structured_plan: structured_plan.as_ref(),
            gm_system_prompt: ctx.gm_system_prompt.as_deref(),
            reasoning_effort: start.reasoning_effort.as_deref(),
        },
        &crate::preflight::rank_references,
        &sylvia_provider::config::env::Environment::current(),
    )
    .await?;
    let model_guidance_override = optional_bool(params, "model_guidance_override")?;
    let (construction, plan, handoff) = sylvia_app::authority::guided::finalize_campaign_construction(
        &ctx.store,
        &sylvia_app::authority::guided::FinalizeConstruction {
            campaign_id: &campaign_id,
            dm_provider_id: &dm_provider_id,
            dm_model_id: &dm_model_id,
            plan_artifact_text: &plan_text,
            structured_plan: structured_plan.as_ref(),
            model_guidance_override,
        },
    )
    .await
    .map_err(store_error)?;
    // Saved before this command returns, so the opening call that follows uses it.
    crate::chat_start::apply_chat_start(ctx, &handoff.fields().dm_session_id, &start).await?;
    let digest = sylvia_base::hash::sha256_hex(campaign_id.as_bytes())[..24].to_owned();
    let open = sylvia_app::authority::turns::current_open_turn(&ctx.store, &campaign_id)
        .await
        .map_err(store_error)?;
    let dm_turn_id = match open {
        Some(turn_id) => turn_id,
        None => {
            let turn_id = format!("turn-dm-{digest}");
            sylvia_app::authority::turns::open_turn(&ctx.store, &campaign_id, &turn_id)
                .await
                .map_err(store_error)?;
            turn_id
        }
    };
    let mut reply = JsonMap::new();
    reply.insert("construction".into(), construction_json(&construction));
    reply.insert("plan".into(), plan_json(&plan));
    reply.insert("handoff".into(), handoff_json(&handoff));
    reply.insert("chat_start".into(), crate::chat_start::chat_start_projection(&start));
    reply.insert("dm_turn_id".into(), dm_turn_id.into());
    reply.insert("dm_attempt_id".into(), format!("attempt-campaign-start-{digest}").into());
    Ok(Json::Object(reply))
}

/// `_json_value(CampaignConstructionCompleted)`: its fields in order.
fn construction_json(construction: &sylvia_domain::authority::CampaignConstructionCompleted) -> Json {
    let mut value = JsonMap::new();
    value.insert("campaign_id".into(), construction.campaign_id.as_str().into());
    value.insert("lane".into(), construction.lane.as_str().into());
    value.insert(
        "canonical_representation_sha256".into(),
        construction.canonical_representation_sha256.as_str().into(),
    );
    value.insert("resulting_revision".into(), construction.resulting_revision.into());
    Json::Object(value)
}

/// `_json_value(CampaignPlan)`: its fields in order, each enum as its value.
fn plan_json(plan: &sylvia_domain::session::CampaignPlan) -> Json {
    let fields = plan.fields();
    let mut value = JsonMap::new();
    value.insert("plan_id".into(), fields.plan_id.as_str().into());
    value.insert("campaign_id".into(), fields.campaign_id.as_str().into());
    value.insert("package_lock_id".into(), fields.package_lock_id.as_str().into());
    value.insert("source_session_id".into(), fields.source_session_id.as_str().into());
    value.insert("content_sha256".into(), fields.content_sha256.as_str().into());
    value.insert("content".into(), Json::Object(plan.content().clone()));
    value.insert(
        "provenance_evidence_ids".into(),
        Json::Array(fields.provenance_evidence_ids.iter().map(|id| id.as_str().into()).collect()),
    );
    value.insert("validation_outcome".into(), fields.validation_outcome.as_str().into());
    value.insert("acceptance_status".into(), fields.acceptance_status.as_str().into());
    value.insert("plan_revision".into(), fields.plan_revision.into());
    value.insert("resulting_campaign_revision".into(), fields.resulting_campaign_revision.into());
    value.insert("payload_sha256".into(), fields.payload_sha256.as_str().into());
    Json::Object(value)
}

/// `_json_value(BuilderDMHandoff)`: its fields in order, with its model guidance decision.
fn handoff_json(handoff: &sylvia_domain::session::BuilderDMHandoff) -> Json {
    let fields = handoff.fields();
    let text = |value: &Option<String>| value.as_deref().map_or(Json::Null, Json::from);
    let guidance = fields.model_guidance.fields();
    let mut decision = JsonMap::new();
    decision.insert("registry_version".into(), guidance.registry_version.as_str().into());
    decision.insert("model_id".into(), guidance.model_id.as_str().into());
    decision.insert("minimum_tier_id".into(), text(&guidance.minimum_tier_id));
    decision.insert("placement_tier_id".into(), text(&guidance.placement_tier_id));
    decision.insert("placement_evidence_sha256".into(), text(&guidance.placement_evidence_sha256));
    decision.insert("status".into(), guidance.status.as_str().into());
    decision.insert("warning_code".into(), text(&guidance.warning_code));
    decision.insert("override_accepted".into(), guidance.override_accepted.into());
    let mut value = JsonMap::new();
    value.insert("handoff_id".into(), fields.handoff_id.as_str().into());
    value.insert("campaign_id".into(), fields.campaign_id.as_str().into());
    value.insert("builder_session_id".into(), fields.builder_session_id.as_str().into());
    value.insert("dm_session_id".into(), fields.dm_session_id.as_str().into());
    value.insert("package_lock_id".into(), fields.package_lock_id.as_str().into());
    value.insert("package_candidate_id".into(), text(&fields.package_candidate_id));
    value.insert("plan_id".into(), fields.plan_id.as_str().into());
    value.insert("plan_revision".into(), fields.plan_revision.into());
    value.insert("plan_content_sha256".into(), fields.plan_content_sha256.as_str().into());
    value.insert("expected_revision".into(), fields.expected_revision.into());
    value.insert("resulting_campaign_revision".into(), fields.resulting_campaign_revision.into());
    value.insert("chronology_ordinal".into(), fields.chronology_ordinal.into());
    value.insert("completed_turn_id".into(), fields.completed_turn_id.as_str().into());
    value.insert("model_guidance".into(), Json::Object(decision));
    value.insert("payload_sha256".into(), fields.payload_sha256.as_str().into());
    Json::Object(value)
}

/// `handle_recover_campaign`: recover `exports/<export_id>.sylvia-gm-campaign` under the storage
/// root.
pub async fn handle_recover_campaign(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let export_id = required_artifact_id(params, "export_id")?;
    let source = ctx.storage_root.join("exports").join(format!("{export_id}.sylvia-gm-campaign"));
    let recovery_id = required(params, "recovery_id")?;
    recover_campaign(ctx, &recovery_id, &source).await
}

/// `handle_recover_campaign_file`: recover a chosen file, which must resolve to an existing
/// `.sylvia-gm-campaign` file.
pub async fn handle_recover_campaign_file(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let source_path = required(params, "source_path")?;
    // `Path(...).resolve(strict=True)`: a failure is an uncaught `OSError` of its kind.
    let source = resolve_strict(std::path::Path::new(&source_path)).map_err(|error| {
        let class = match error.kind() {
            std::io::ErrorKind::NotFound => "FileNotFoundError",
            std::io::ErrorKind::PermissionDenied => "PermissionError",
            _ => "OSError",
        };
        sylvia_base::CodedError::new("internal_error", error.to_string()).with_error_type(class)
    })?;
    if source.extension().is_none_or(|extension| extension != "sylvia-gm-campaign") || !source.is_file() {
        return Err(protocol("Selected recovery source is not a Sylvia GM campaign archive"));
    }
    let recovery_id = required(params, "recovery_id")?;
    recover_campaign(ctx, &recovery_id, &source).await
}

async fn recover_campaign(
    ctx: &DispatchContext,
    recovery_id: &str,
    source: &std::path::Path,
) -> CodedResult<Json> {
    let result = sylvia_app::authority::portable::recover_campaign(&ctx.store, recovery_id, source)
        .await
        .map_err(store_error)?;
    let mut value = JsonMap::new();
    value.insert("campaign_id".into(), result.campaign_id.into());
    value.insert("captured_revision".into(), result.captured_revision.into());
    value.insert("archive_sha256".into(), result.archive_sha256.into());
    value.insert("byte_count".into(), result.byte_count.into());
    Ok(Json::Object(value))
}

/// `_required_artifact_id(payload, key)`: a nonempty string matching
/// `[A-Za-z0-9][A-Za-z0-9._-]{0,127}` in full.
pub(crate) fn required_artifact_id(params: &JsonMap, key: &str) -> CodedResult<String> {
    let value = required(params, key)?;
    let mut characters = value.chars();
    let first_ok = characters.next().is_some_and(|first| first.is_ascii_alphanumeric());
    let rest_ok = characters.all(|character| character.is_ascii_alphanumeric() || "._-".contains(character));
    if !first_ok || !rest_ok || value.chars().count() > 128 {
        return Err(protocol(&format!("{key} is not a safe artifact identity")));
    }
    Ok(value)
}

/// `_optional_bool(payload, key)`: absent is `False`; anything present must be a boolean
/// (`null` included).
pub(crate) fn optional_bool(params: &JsonMap, key: &str) -> CodedResult<bool> {
    match params.get(key) {
        None => Ok(false),
        Some(Json::Bool(value)) => Ok(*value),
        Some(_) => Err(protocol(&format!("{key} must be a boolean"))),
    }
}

/// `Path.resolve(strict=True)`, without Windows' `\\?\` prefix, which pathlib does not show.
pub(crate) fn resolve_strict(path: &std::path::Path) -> std::io::Result<std::path::PathBuf> {
    let resolved = std::fs::canonicalize(path)?;
    let text = resolved.to_string_lossy();
    Ok(if let Some(rest) = text.strip_prefix(r"\\?\UNC\") {
        std::path::PathBuf::from(format!(r"\\{rest}"))
    } else if let Some(rest) = text.strip_prefix(r"\\?\") {
        std::path::PathBuf::from(rest)
    } else {
        resolved
    })
}

/// `_optional_text(payload, key)`: absent or `null` is `None`; anything else must be a
/// nonempty string.
pub(crate) fn optional_text(params: &JsonMap, key: &str) -> CodedResult<Option<String>> {
    match params.get(key) {
        None | Some(Json::Null) => Ok(None),
        Some(_) => required(params, key).map(Some),
    }
}

/// `_required_nonnegative_int(payload, key)`: an integer, not a bool, at least zero. An integer
/// beyond 64 bits, which Python would take, is refused as a declared limit.
pub(crate) fn required_nonnegative_int(params: &JsonMap, key: &str) -> CodedResult<i64> {
    match params.get(key) {
        Some(Json::Number(number)) if number.as_i64().is_some_and(|value| value >= 0) => {
            Ok(number.as_i64().unwrap_or_default())
        }
        Some(Json::Number(number)) if number.is_u64() => Err(sylvia_base::CodedError::new(
            "internal_error",
            format!("{key} is beyond the Rust owner's 64-bit integer limit"),
        )),
        _ => Err(protocol(&format!("{key} must be a nonnegative integer"))),
    }
}

/// The reply of the guided Draft commands: the Draft's fields Python's handler lists.
fn guided_draft_value(draft: &sylvia_domain::authority::GuidedCampaignDraft) -> Json {
    let mut value = JsonMap::new();
    value.insert("campaign_id".into(), draft.campaign_id.as_str().into());
    value.insert("builder_session_id".into(), draft.builder_session_id.as_str().into());
    value.insert("current_step".into(), draft.current_step.as_str().into());
    value.insert("construction_revision".into(), draft.construction_revision.into());
    value.insert("resulting_revision".into(), draft.resulting_revision.into());
    value.insert("answer_message_id".into(), draft.answer_message_id.clone().into());
    Json::Object(value)
}

/// `_required_text(params, key)` as a handler's error.
pub(crate) fn required(params: &JsonMap, key: &str) -> CodedResult<String> {
    crate::dispatch::required_text(params, key).map_err(|failure| match failure {
        crate::dispatch::Failure::Rejected(error) => error,
        _ => unreachable!("a missing parameter is a rejection"),
    })
}

/// `OwnerProtocolError(message)`.
fn protocol(message: &str) -> sylvia_base::CodedError {
    sylvia_base::CodedError::rejected(message).with_error_type("OwnerProtocolError")
}

/// `_json_value(ModelGuidanceDecision)`: the dataclass fields in declaration order.
fn model_guidance_value(decision: &sylvia_domain::model_capability::ModelGuidanceDecision) -> Json {
    let fields = decision.fields();
    let mut value = JsonMap::new();
    value.insert("registry_version".into(), fields.registry_version.clone().into());
    value.insert("model_id".into(), fields.model_id.clone().into());
    value.insert("minimum_tier_id".into(), fields.minimum_tier_id.clone().into());
    value.insert("placement_tier_id".into(), fields.placement_tier_id.clone().into());
    value.insert("placement_evidence_sha256".into(), fields.placement_evidence_sha256.clone().into());
    value.insert("status".into(), fields.status.as_str().into());
    value.insert("warning_code".into(), fields.warning_code.clone().into());
    value.insert("override_accepted".into(), fields.override_accepted.into());
    Json::Object(value)
}

/// `_json_value(CampaignSessionIndexEntry)`.
fn session_entry_value(entry: &sylvia_store::authority::CampaignSessionIndexEntry) -> Json {
    let mut value = JsonMap::new();
    value.insert("session_id".into(), entry.session_id.clone().into());
    value.insert("role".into(), entry.role.clone().into());
    value.insert("status".into(), entry.status.clone().into());
    value.insert("provider_id".into(), entry.provider_id.clone().into());
    value.insert("model_id".into(), entry.model_id.clone().into());
    value.insert("selected_skill".into(), entry.selected_skill.clone().into());
    value.insert(
        "model_guidance".into(),
        entry.model_guidance.as_ref().map_or(Json::Null, model_guidance_value),
    );
    Json::Object(value)
}

/// `_json_value(CampaignIndexEntry)`: the dataclass fields in declaration order.
fn campaign_entry_value(entry: &sylvia_store::authority::CampaignIndexEntry) -> Json {
    let mut value = JsonMap::new();
    value.insert("campaign_id".into(), entry.campaign_id.clone().into());
    value.insert("display_name".into(), entry.display_name.clone().into());
    value.insert("lifecycle".into(), entry.lifecycle.clone().into());
    value.insert("writer".into(), entry.writer.clone().into());
    value.insert("revision".into(), entry.revision.into());
    value.insert("ruleset_summary".into(), entry.ruleset_summary.clone().into());
    value.insert("package_summary".into(), entry.package_summary.clone().into());
    value.insert("package_count".into(), entry.package_count.into());
    value.insert("playable".into(), entry.playable.into());
    value.insert("availability".into(), entry.availability.clone().into());
    value.insert("ruleset_id".into(), entry.ruleset_id.clone().into());
    value.insert("ruleset_label".into(), entry.ruleset_label.clone().into());
    value.insert("campaign_setting_id".into(), entry.campaign_setting_id.clone().into());
    value.insert("campaign_setting_label".into(), entry.campaign_setting_label.clone().into());
    value.insert("session_activity_ordinal".into(), entry.session_activity_ordinal.into());
    value.insert("turn_id".into(), entry.turn_id.clone().into());
    value.insert("turn_status".into(), entry.turn_status.clone().into());
    value.insert("session_id".into(), entry.session_id.clone().into());
    value.insert("session_status".into(), entry.session_status.clone().into());
    value.insert("provider_id".into(), entry.provider_id.clone().into());
    value.insert("model_id".into(), entry.model_id.clone().into());
    value.insert("sessions".into(), Json::Array(entry.sessions.iter().map(session_entry_value).collect()));
    Json::Object(value)
}

/// `handle_campaign_index`: `ctx.bus.campaign_index()`, the application owner's campaign rail,
/// as `_json_value` writes it. Parameters are not read.
pub async fn handle_campaign_index(ctx: &DispatchContext, _params: &JsonMap) -> CodedResult<Json> {
    let entries = ctx.store.call(|store| store.campaign_index_entries()).await.map_err(store_error)?;
    Ok(Json::Array(entries.iter().map(campaign_entry_value).collect()))
}

/// `handle_campaign_creation_catalog`: `_campaign_creation_catalog(storage_root, owner,
/// include_participants=...)`, the content a new campaign can be made from. `include_participants`
/// defaults to `True` (`_optional_bool(..., default=True)`); anything present must be a boolean.
pub async fn handle_campaign_creation_catalog(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let include_participants = match params.get("include_participants") {
        None => true,
        Some(Json::Bool(value)) => *value,
        Some(_) => return Err(protocol("include_participants must be a boolean")),
    };
    let (storage_root, bundled) = (ctx.storage_root.clone(), std::sync::Arc::clone(&ctx.bundled));
    let catalog = ctx
        .store
        .call(move |store| {
            crate::creation_catalog::campaign_creation_catalog(
                &storage_root,
                store,
                &bundled,
                include_participants,
            )
        })
        .await
        .map_err(store_error)?;
    Ok(Json::Array(catalog.into_iter().map(Json::Object).collect()))
}

/// `handle_catalog`: the campaign's admitted tools (`AuthorityOwner.catalog(campaign_id)`), each
/// as its package identity, name, capability and input schema.
pub async fn handle_catalog(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let campaign_id = required(params, "campaign_id")?;
    let tools = sylvia_app::authority::content_reads::catalog(&ctx.store, &campaign_id, false)
        .await
        .map_err(store_error)?;
    Ok(Json::Array(
        tools
            .into_iter()
            .map(|tool| {
                let mut entry = JsonMap::new();
                entry.insert("package_id".into(), tool.package_id.into());
                entry.insert("package_version".into(), tool.package_version.into());
                entry.insert("package_sha256".into(), tool.package_sha256.into());
                entry.insert("name".into(), tool.name.into());
                entry.insert("capability".into(), tool.capability.into());
                entry.insert("input_schema".into(), Json::Object(tool.input_schema));
                Json::Object(entry)
            })
            .collect(),
    ))
}

/// `handle_backup_database`: one coherent online copy of the authority database at
/// `backups/<backup_id>.sqlite3` under the storage root.
pub async fn handle_backup_database(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let backup_id = required_artifact_id(params, "backup_id")?;
    let backups = ctx.storage_root.join("backups");
    let target = backups.join(format!("{backup_id}.sqlite3"));
    // `target.parent.mkdir(parents=True, exist_ok=True)`: a failure is an `OSError`.
    std::fs::create_dir_all(&backups).map_err(|error| {
        sylvia_base::CodedError::new("internal_error", error.to_string()).with_error_type("OSError")
    })?;
    ctx.store.call(move |store| store.backup_database(&target)).await.map_err(store_error)?;
    let mut reply = JsonMap::new();
    reply.insert("backup_id".into(), backup_id.into());
    reply.insert("status".into(), "created".into());
    Ok(Json::Object(reply))
}

/// `handle_player_projection_page`: `ctx.application_owner.player_projection_page(campaign_id,
/// after_sequence=...)`, each projection as Python's `_json_value` writes a dataclass (its fields,
/// in order).
pub async fn handle_player_projection_page(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let campaign_id = required(params, "campaign_id")?;
    let after_sequence = match params.get("after_sequence") {
        None | Some(Json::Null) => None,
        Some(_) => Some(Json::from(required_nonnegative_int(params, "after_sequence")?)),
    };
    let projections = sylvia_app::authority::player_projection::player_projection_page(
        &ctx.store,
        &campaign_id,
        after_sequence.as_ref(),
    )
    .await
    .map_err(sylvia_app::authority::actions::ActionError::into_coded)?;
    let items = projections
        .iter()
        .map(|item| {
            let mut value = JsonMap::new();
            value.insert("kind".into(), item.kind.as_str().into());
            value.insert("sequence".into(), item.sequence.map_or(Json::Null, Json::from));
            value.insert("text".into(), item.text.clone().map_or(Json::Null, Json::from));
            value.insert("safe_payload".into(), Json::Object(item.safe_payload.clone()));
            value.insert("collapsed_by_default".into(), item.collapsed_by_default.into());
            value.insert("provisional".into(), item.provisional.into());
            value.insert("action_id".into(), item.action_id.clone().map_or(Json::Null, Json::from));
            value.insert("attempt_id".into(), item.attempt_id.clone().map_or(Json::Null, Json::from));
            Json::Object(value)
        })
        .collect();
    crate::owner_json::json_value(&Json::Array(items))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Read;

    fn workspace(path: &str) -> std::path::PathBuf {
        crate::util::source_root().join(path)
    }

    fn read_json(path: &str) -> Json {
        serde_json::from_str(&std::fs::read_to_string(workspace(path)).unwrap()).unwrap()
    }

    /// Without `model_guidance`, whose key order lane 3's vector writes as the persisted payload.
    fn without_guidance(value: &Json) -> String {
        let mut value = value.clone();
        for session in value["sessions"].as_array_mut().unwrap() {
            session.as_object_mut().unwrap().insert("model_guidance".into(), Json::Null);
        }
        value.to_string()
    }

    #[test]
    fn campaign_rail_entries_keep_pythons_field_order() {
        // Lane 3's Python-made seed and the entries Python's owner projects from it.
        let expected = read_json("crates/sylvia-store/tests/vectors/campaigns/campaign_index.json");
        let folder = std::env::temp_dir().join(format!("sylvia-owner-campaign-index-{}", std::process::id()));
        // A folder left by an earlier run must go, or its files would look like this run's.
        if let Err(error) = std::fs::remove_dir_all(&folder) {
            assert_eq!(
                error.kind(),
                std::io::ErrorKind::NotFound,
                "cannot clear the old work folder {}: {error}",
                folder.display()
            );
        }
        std::fs::create_dir_all(&folder).unwrap();
        let database = folder.join("authority.sqlite3");
        let mut bytes = Vec::new();
        flate2::read::GzDecoder::new(
            std::fs::File::open(workspace("crates/sylvia-store/tests/vectors/campaigns/seed.sqlite3.gz"))
                .unwrap(),
        )
        .read_to_end(&mut bytes)
        .unwrap();
        std::fs::write(&database, bytes).unwrap();
        let mut store = sylvia_store::AuthorityStore::open_for_application_owner(&database, None).unwrap();
        let entries = store.campaign_index_entries().unwrap();
        store.close();
        let want = expected["campaign_index_entries"].as_array().unwrap();
        assert_eq!(entries.len(), want.len());
        let mut guided = 0;
        for (entry, want) in entries.iter().zip(want) {
            let got = campaign_entry_value(entry);
            assert_eq!(without_guidance(&got), without_guidance(want), "entry {}", want["campaign_id"]);
            for (session, wanted) in
                got["sessions"].as_array().unwrap().iter().zip(want["sessions"].as_array().unwrap())
            {
                // Same values; the order is checked against the owner's own reply below.
                assert_eq!(session["model_guidance"], wanted["model_guidance"]);
                guided += usize::from(!session["model_guidance"].is_null());
            }
        }
        assert_eq!(guided, 1, "the seed has one guided session");
        let _ = std::fs::remove_dir_all(folder);
    }

    #[test]
    fn model_guidance_keeps_the_dataclass_order_of_the_owners_reply() {
        // The Python owner's `campaign_index` reply recorded in `ruleless-turn`.
        let recording = read_json("crates/sylvia-compare/recordings/windows/ruleless-turn.recording.json");
        let reply = recording["steps"]
            .as_array()
            .unwrap()
            .iter()
            .rev()
            .find(|step| step["method"] == "campaign_index")
            .unwrap()["reply"]["result"]
            .clone();
        let recorded = &reply[0]["sessions"][0]["model_guidance"];
        let decision = sylvia_domain::model_capability::model_guidance_from_payload(recorded).unwrap();
        assert_eq!(model_guidance_value(&decision).to_string(), recorded.to_string());
    }

    /// The parameters Python's handler reads for one of lane 3's lifecycle runs.
    fn lifecycle_params(run: &Json) -> JsonMap {
        let mut params = JsonMap::new();
        for key in [
            "operation_id",
            "campaign_id",
            "expected_revision",
            "display_name",
            "designated_session_id",
            "session_id",
        ] {
            if let Some(value) = run.get(key) {
                params.insert(key.into(), value.clone());
            }
        }
        params
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn lifecycle_commands_answer_as_pythons_runs() {
        // Lane 3's Python run of the five lifecycle operations (`generate_lifecycle_runs.py`),
        // through the owner's dispatch: each reply in Python's dataclass order, or Python's
        // refusal. The handler refuses an empty identity and a negative revision itself, as
        // Python's `_required_text` and `_required_nonnegative_int` do. Runs that need a commit
        // fault or held campaign work are proven by `sylvia-app/tests/lifecycle.rs` instead.
        let record = read_json("crates/sylvia-app/tests/vectors/lifecycle/lifecycle_runs.json");
        let folder = std::env::temp_dir().join(format!("sylvia-owner-lifecycle-{}", std::process::id()));
        // A folder left by an earlier run must go, or its files would look like this run's.
        if let Err(error) = std::fs::remove_dir_all(&folder) {
            assert_eq!(
                error.kind(),
                std::io::ErrorKind::NotFound,
                "cannot clear the old work folder {}: {error}",
                folder.display()
            );
        }
        std::fs::create_dir_all(&folder).unwrap();
        let database = folder.join("authority.sqlite3");
        let mut bytes = Vec::new();
        flate2::read::GzDecoder::new(
            std::fs::File::open(workspace("crates/sylvia-app/tests/vectors/lifecycle/seed.sqlite3.gz"))
                .unwrap(),
        )
        .read_to_end(&mut bytes)
        .unwrap();
        std::fs::write(&database, bytes).unwrap();
        let path = database.clone();
        let (store, thread) = sylvia_store::StoreHandle::spawn(move || {
            sylvia_store::AuthorityStore::open_for_application_owner(&path, None)
        })
        .unwrap();
        let context = crate::dispatch::DispatchContext {
            owner_id: "lifecycle-test".into(),
            storage_root: folder.clone(),
            handshake_verified: true,
            method: String::new(),
            gm_system_prompt: None,
            store,
            providers: None,
            session_loop: None,
            campaign_loop: None,
            bundled: std::sync::Arc::new(sylvia_app::bundled_packages::BundledPackages::new(
                sylvia_app::bundled_packages::BundledSources::from_repository_root(
                    &crate::util::source_root(),
                ),
            )),
            record_provider_warning: std::sync::Arc::new(|_: &str| {}),
        };
        let mut answered = 0;
        for run in record["runs"].as_array().unwrap() {
            if run.get("fault").is_some() || run.get("busy").is_some() {
                continue;
            }
            let method = run["op"].as_str().unwrap();
            let label = format!("{method} {}", run["operation_id"]);
            let reply = crate::dispatch::dispatch(&context, method, &lifecycle_params(run)).await;
            let want = &run["outcome"];
            let protocol = if run["operation_id"] == "" {
                Some("operation_id must be a nonempty string")
            } else if run["expected_revision"].as_i64().is_some_and(|revision| revision < 0) {
                Some("expected_revision must be a nonnegative integer")
            } else {
                None
            };
            match (reply, protocol) {
                (Err(crate::dispatch::Failure::Rejected(error)), Some(message)) => {
                    assert_eq!(
                        (error.error_type, error.message.as_str()),
                        (Some("OwnerProtocolError"), message),
                        "{label}"
                    );
                }
                (Ok(value), None) => assert_eq!(value.to_string(), want["ok"].to_string(), "{label}"),
                (Err(crate::dispatch::Failure::Rejected(error)), None) => {
                    assert_eq!(error.error_type, want["error"].as_str(), "{label}: type {error:?}");
                    assert_eq!(error.code, want["code"].as_str().unwrap(), "{label}: code");
                    assert_eq!(error.message, want["message"].as_str().unwrap(), "{label}: message");
                }
                (other, _) => panic!("{label}: Rust {other:?}, Python {want}"),
            }
            answered += 1;
        }
        assert_eq!(answered, 43);
        // Python's `_optional_text`: present means a nonempty string.
        for (method, key, value) in [
            ("archive_campaign", "designated_session_id", Json::from("")),
            ("trash_campaign", "designated_session_id", Json::from(5)),
            ("restore_archived_campaign", "session_id", Json::from(false)),
        ] {
            let mut params = JsonMap::new();
            params.insert("operation_id".into(), "life-x".into());
            params.insert("campaign_id".into(), "campaign-story".into());
            params.insert("expected_revision".into(), 0.into());
            params.insert(key.into(), value);
            let Err(crate::dispatch::Failure::Rejected(error)) =
                crate::dispatch::dispatch(&context, method, &params).await
            else {
                panic!("{method} must refuse {key}")
            };
            assert_eq!(error.message, format!("{key} must be a nonempty string"));
        }
        drop(context);
        tokio::task::spawn_blocking(move || thread.join()).await.unwrap();
        let _ = std::fs::remove_dir_all(folder);
    }

    /// Every entry under `campaigns/`, relative, with each file's SHA-256: the generator's `tree`.
    fn workspace_tree(root: &std::path::Path) -> Json {
        fn walk(root: &std::path::Path, folder: &std::path::Path, entries: &mut Vec<(String, Json)>) {
            for entry in std::fs::read_dir(folder).unwrap() {
                let path = entry.unwrap().path();
                let relative = path.strip_prefix(root).unwrap().to_string_lossy().replace('\\', "/");
                if path.is_dir() {
                    entries.push((relative.clone(), serde_json::json!([relative, "dir", null])));
                    walk(root, &path, entries);
                } else {
                    let digest = sylvia_base::hash::sha256_hex(std::fs::read(&path).unwrap());
                    entries.push((relative.clone(), serde_json::json!([relative, "file", digest])));
                }
            }
        }
        let mut entries = Vec::new();
        if root.join("campaigns").exists() {
            walk(root, &root.join("campaigns"), &mut entries);
        }
        entries.sort_by(|left, right| left.0.cmp(&right.0));
        Json::Array(entries.into_iter().map(|(_, entry)| entry).collect())
    }

    /// Every table's rows, sorted, for comparing a database with Python's exactly.
    fn table_rows(path: &std::path::Path) -> std::collections::BTreeMap<String, Vec<String>> {
        let connection = rusqlite::Connection::open(path).unwrap();
        let tables: Vec<String> = connection
            .prepare("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
            .unwrap()
            .query_map([], |row| row.get(0))
            .unwrap()
            .collect::<Result<_, _>>()
            .unwrap();
        tables
            .into_iter()
            .map(|table| {
                let mut statement = connection.prepare(&format!("SELECT * FROM \"{table}\"")).unwrap();
                let width = statement.column_count();
                let mut rows: Vec<String> = statement
                    .query_map([], |row| {
                        let values: Vec<rusqlite::types::Value> =
                            (0..width).map(|index| row.get(index)).collect::<Result<_, _>>()?;
                        Ok(format!("{values:?}"))
                    })
                    .unwrap()
                    .collect::<Result<_, _>>()
                    .unwrap();
                rows.sort();
                (table, rows)
            })
            .collect()
    }

    fn gunzip_to(relative: &str, target: &std::path::Path) {
        let mut bytes = Vec::new();
        flate2::read::GzDecoder::new(std::fs::File::open(workspace(relative)).unwrap())
            .read_to_end(&mut bytes)
            .unwrap();
        std::fs::write(target, bytes).unwrap();
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn purges_answer_and_leave_the_workspace_as_pythons_handler_does() {
        // Lane 3's Python run of `handle_purge_campaign` (`generate_purge_runs.py`): the owner's
        // purge with the Builder sources' quarantine, restore and finalize, and `trash_campaign`,
        // through the owner's dispatch. Each reply, the campaign workspace after each step, and
        // the database afterwards must be Python's.
        let record = read_json("crates/sylvia-app/tests/vectors/purge/purge_runs.json");
        let folder = std::env::temp_dir().join(format!("sylvia-owner-purge-{}", std::process::id()));
        // A folder left by an earlier run must go, or its files would look like this run's.
        if let Err(error) = std::fs::remove_dir_all(&folder) {
            assert_eq!(
                error.kind(),
                std::io::ErrorKind::NotFound,
                "cannot clear the old work folder {}: {error}",
                folder.display()
            );
        }
        std::fs::create_dir_all(&folder).unwrap();
        let database = folder.join("authority.sqlite3");
        gunzip_to("crates/sylvia-app/tests/vectors/purge/seed.sqlite3.gz", &database);
        let armed: std::sync::Arc<std::sync::Mutex<Option<String>>> = Default::default();
        let seam = |name: &'static str| {
            let armed = std::sync::Arc::clone(&armed);
            Box::new(move || -> sylvia_store::StoreResult<()> {
                if armed.lock().unwrap().as_deref() == Some(name) {
                    return Err(sylvia_store::StoreError::Other(format!("{name} lifecycle commit fault")));
                }
                Ok(())
            }) as Box<dyn Fn() -> sylvia_store::StoreResult<()> + Send>
        };
        let faults = sylvia_store::store::Faults {
            before_lifecycle_commit: Some(seam("before")),
            after_lifecycle_commit: Some(seam("after")),
            ..sylvia_store::store::Faults::default()
        };
        let path = database.clone();
        let (store, thread) = sylvia_store::StoreHandle::spawn(move || {
            let mut store = sylvia_store::AuthorityStore::open_for_application_owner(&path, None)?;
            store.set_faults(faults);
            Ok(store)
        })
        .unwrap();
        let context = crate::dispatch::DispatchContext {
            owner_id: "purge-test".into(),
            storage_root: folder.clone(),
            handshake_verified: true,
            method: String::new(),
            gm_system_prompt: None,
            store,
            providers: None,
            session_loop: None,
            campaign_loop: None,
            bundled: std::sync::Arc::new(sylvia_app::bundled_packages::BundledPackages::new(
                sylvia_app::bundled_packages::BundledSources::from_repository_root(
                    &crate::util::source_root(),
                ),
            )),
            record_provider_warning: std::sync::Arc::new(|_: &str| {}),
        };
        for (index, run) in record["runs"].as_array().unwrap().iter().enumerate() {
            let method = run["op"].as_str().unwrap();
            let label = format!("#{index} {method}");
            let want = &run["outcome"];
            if method == "write" {
                let path = folder.join(run["path"].as_str().unwrap());
                std::fs::create_dir_all(path.parent().unwrap()).unwrap();
                std::fs::write(&path, run["text"].as_str().unwrap().as_bytes()).unwrap();
            } else {
                *armed.lock().unwrap() = run["fault"].as_str().map(str::to_owned);
                let params = run["params"].as_object().unwrap().clone();
                let reply = crate::dispatch::dispatch(&context, method, &params).await;
                *armed.lock().unwrap() = None;
                match reply {
                    Ok(value) => assert_eq!(value.to_string(), want["ok"].to_string(), "{label}"),
                    // The generator's own fault class, from a commit seam: the owner's internal error.
                    Err(crate::dispatch::Failure::Internal { .. }) => {
                        assert_eq!(want["error"], "Fault", "{label}")
                    }
                    Err(crate::dispatch::Failure::Rejected(error)) => {
                        assert_eq!(error.error_type, want["error"].as_str(), "{label}: type {error:?}");
                        assert_eq!(
                            error.code,
                            want["code"].as_str().unwrap_or("command_rejected"),
                            "{label}: code"
                        );
                        assert_eq!(error.message, want["message"].as_str().unwrap(), "{label}: message");
                    }
                    Err(other) => panic!("{label}: Rust {other:?}, Python {want}"),
                }
            }
            assert_eq!(workspace_tree(&folder), run["tree"], "{label}: the campaign workspace");
        }
        drop(context);
        tokio::task::spawn_blocking(move || thread.join()).await.unwrap();
        let python = folder.join("python.sqlite3");
        gunzip_to("crates/sylvia-app/tests/vectors/purge/purge_runs.sqlite3.gz", &python);
        assert_eq!(table_rows(&database), table_rows(&python), "the database differs from Python's");
        let _ = std::fs::remove_dir_all(folder);
    }

    /// The generator's `tree`: `exports/` and everything under it, with each file's SHA-256.
    fn exports_tree(root: &std::path::Path) -> Json {
        let exports = root.join("exports");
        if !exports.exists() {
            return Json::Array(Vec::new());
        }
        let mut entries: Vec<(String, Json)> = Vec::new();
        for entry in std::fs::read_dir(&exports).unwrap() {
            let path = entry.unwrap().path();
            let relative = format!("exports/{}", path.file_name().unwrap().to_string_lossy());
            let entry = if path.is_dir() {
                serde_json::json!([relative, "dir", null])
            } else {
                serde_json::json!([
                    relative,
                    "file",
                    sylvia_base::hash::sha256_hex(std::fs::read(&path).unwrap())
                ])
            };
            entries.push((relative, entry));
        }
        entries.sort_by(|left, right| left.0.cmp(&right.0));
        let mut tree = vec![serde_json::json!(["exports", "dir", null])];
        tree.extend(entries.into_iter().map(|(_, entry)| entry));
        Json::Array(tree)
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn exports_answer_and_publish_as_pythons_handler_does() {
        // Lane 3's Python run of `handle_export_campaign` (`generate_export_runs.py`): the
        // handler's and the owner's checks, then exports of the story and Builder campaigns,
        // through the owner's dispatch. Each reply and the exports folder after each call must be
        // Python's.
        let record = read_json("crates/sylvia-app/tests/vectors/export/export_runs.json");
        // A short root, as Python's run had (`tmpXXXXXXXX/storage`): the 128-character export
        // identity's claimed temporary then stays under `MAX_PATH`. A test binary carries no
        // `longPathAware` manifest; the owner executables do, so their Win32 file calls take long
        // paths exactly when Windows enables them, as Python's do.
        let folder = std::env::temp_dir().join(format!("sgx-{}", std::process::id()));
        // A folder left by an earlier run must go, or its files would look like this run's.
        if let Err(error) = std::fs::remove_dir_all(&folder) {
            assert_eq!(
                error.kind(),
                std::io::ErrorKind::NotFound,
                "cannot clear the old work folder {}: {error}",
                folder.display()
            );
        }
        std::fs::create_dir_all(&folder).unwrap();
        let database = folder.join("authority.sqlite3");
        gunzip_to("crates/sylvia-store/tests/vectors/export_capture/story.sqlite3.gz", &database);
        let path = database.clone();
        let (store, thread) = sylvia_store::StoreHandle::spawn(move || {
            sylvia_store::AuthorityStore::open_for_application_owner(&path, None)
        })
        .unwrap();
        let context = crate::dispatch::DispatchContext {
            owner_id: "export-test".into(),
            storage_root: folder.clone(),
            handshake_verified: true,
            method: String::new(),
            gm_system_prompt: None,
            store,
            providers: None,
            session_loop: None,
            campaign_loop: None,
            bundled: std::sync::Arc::new(sylvia_app::bundled_packages::BundledPackages::new(
                sylvia_app::bundled_packages::BundledSources::from_repository_root(
                    &crate::util::source_root(),
                ),
            )),
            record_provider_warning: std::sync::Arc::new(|_: &str| {}),
        };
        let root = resolve_strict(&folder).unwrap();
        for (index, run) in record["runs"].as_array().unwrap().iter().enumerate() {
            let method = run["op"].as_str().unwrap();
            let label = format!("#{index} {method}");
            let want = &run["outcome"];
            let mut asked = context.clone();
            asked.method = method.to_owned();
            let params = run["params"].as_object().unwrap().clone();
            match crate::dispatch::dispatch(&asked, method, &params).await {
                Ok(mut value) => {
                    if let Some(path) = value.get("artifact_path").and_then(Json::as_str).map(str::to_owned) {
                        // Python records the path relative to the resolved storage root.
                        let relative = std::path::Path::new(&path).strip_prefix(&root).unwrap().to_path_buf();
                        let parts: Vec<String> = relative
                            .components()
                            .map(|part| part.as_os_str().to_string_lossy().into_owned())
                            .collect();
                        value["artifact_path"] = parts.join("/").into();
                    }
                    assert_eq!(value.to_string(), want["ok"].to_string(), "{label}");
                }
                Err(crate::dispatch::Failure::Rejected(error)) => {
                    assert_eq!(error.error_type, want["error"].as_str(), "{label}: type {error:?}");
                    assert_eq!(
                        error.code,
                        want["code"].as_str().unwrap_or("command_rejected"),
                        "{label}: code"
                    );
                    assert_eq!(error.message, want["message"].as_str().unwrap(), "{label}: message");
                }
                Err(other) => panic!("{label}: Rust {other:?}, Python {want}"),
            }
            assert_eq!(exports_tree(&folder), run["tree"], "{label}: the exports folder");
        }
        drop(context);
        tokio::task::spawn_blocking(move || thread.join()).await.unwrap();
        let _ = std::fs::remove_dir_all(folder);
    }

    /// The archive validation vectors' archives, by case name.
    fn validation_archives() -> std::collections::HashMap<String, Vec<u8>> {
        let record = read_json("crates/sylvia-store/tests/vectors/archive_validation/validation_runs.json");
        let mut data = Vec::new();
        flate2::read::GzDecoder::new(
            std::fs::File::open(workspace(
                "crates/sylvia-store/tests/vectors/archive_validation/archives.bin.gz",
            ))
            .unwrap(),
        )
        .read_to_end(&mut data)
        .unwrap();
        record["cases"]
            .as_array()
            .unwrap()
            .iter()
            .map(|case| {
                let (offset, length) =
                    (case["offset"].as_u64().unwrap() as usize, case["length"].as_u64().unwrap() as usize);
                (case["name"].as_str().unwrap().to_owned(), data[offset..offset + length].to_vec())
            })
            .collect()
    }

    /// Lower-case hex to bytes.
    fn from_hex(text: &str) -> Vec<u8> {
        (0..text.len()).step_by(2).map(|at| u8::from_str_radix(&text[at..at + 2], 16).unwrap()).collect()
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn recoveries_answer_and_store_as_pythons_handlers_do() {
        // Lane 3's Python run of `handle_recover_campaign` and `handle_recover_campaign_file`
        // (`generate_recovery_runs.py`) on three storage roots: the handlers' checks, staging's
        // refusals, every damaged archive of the validation vectors, replays, collisions, purges
        // of recovered campaigns and both commit seams. Each reply and each final database must be
        // Python's.
        let record = read_json("crates/sylvia-app/tests/vectors/recovery/recovery_runs.json");
        let archives = validation_archives();
        let bases = [
            ("fresh", "crates/sylvia-store/tests/vectors/packages/seed_fresh.sqlite3.gz"),
            ("damaged", "crates/sylvia-store/tests/vectors/packages/seed_fresh.sqlite3.gz"),
            ("purged", "crates/sylvia-app/tests/vectors/purge/purge_runs.sqlite3.gz"),
        ];
        let mut failures = Vec::new();
        for (name, base) in bases {
            // A short root, as in the export test.
            let folder = std::env::temp_dir().join(format!("sgr-{}-{name}", std::process::id()));
            // A folder left by an earlier run must go, or its files would look like this run's.
            if let Err(error) = std::fs::remove_dir_all(&folder) {
                assert_eq!(
                    error.kind(),
                    std::io::ErrorKind::NotFound,
                    "cannot clear the old work folder {}: {error}",
                    folder.display()
                );
            }
            std::fs::create_dir_all(&folder).unwrap();
            let database = folder.join("authority.sqlite3");
            gunzip_to(base, &database);
            let armed: std::sync::Arc<std::sync::Mutex<Option<String>>> = Default::default();
            let seam = |seam: &'static str| {
                let armed = std::sync::Arc::clone(&armed);
                Box::new(move || -> sylvia_store::StoreResult<()> {
                    if armed.lock().unwrap().as_deref() == Some(seam) {
                        return Err(sylvia_store::StoreError::Other(format!("{seam} recovery commit fault")));
                    }
                    Ok(())
                }) as Box<dyn Fn() -> sylvia_store::StoreResult<()> + Send>
            };
            let faults = sylvia_store::store::Faults {
                before_recovery_commit: Some(seam("before")),
                after_recovery_commit: Some(seam("after")),
                ..sylvia_store::store::Faults::default()
            };
            let path = database.clone();
            let (store, thread) = sylvia_store::StoreHandle::spawn(move || {
                let mut store = sylvia_store::AuthorityStore::open_for_application_owner(&path, None)?;
                store.set_faults(faults);
                Ok(store)
            })
            .unwrap();
            let context = crate::dispatch::DispatchContext {
                owner_id: "recovery-test".into(),
                storage_root: folder.clone(),
                handshake_verified: true,
                method: String::new(),
                gm_system_prompt: None,
                store,
                providers: None,
                session_loop: None,
                campaign_loop: None,
                bundled: std::sync::Arc::new(sylvia_app::bundled_packages::BundledPackages::new(
                    sylvia_app::bundled_packages::BundledSources::from_repository_root(
                        &crate::util::source_root(),
                    ),
                )),
                record_provider_warning: std::sync::Arc::new(|_: &str| {}),
            };
            let root = folder.to_string_lossy().replace('\\', "/");
            for (index, run) in record["roots"][name].as_array().unwrap().iter().enumerate() {
                let method = run["op"].as_str().unwrap();
                let label = format!("{name} #{index} {method}");
                let want = &run["outcome"];
                match method {
                    "write" => {
                        let path = folder.join(run["path"].as_str().unwrap());
                        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
                        let bytes = match run.get("hex") {
                            Some(hex) => from_hex(hex.as_str().unwrap()),
                            None => {
                                let mut bytes = archives[run["archive"].as_str().unwrap()].clone();
                                bytes.extend(from_hex(run["suffix"].as_str().unwrap()));
                                bytes
                            }
                        };
                        std::fs::write(&path, bytes).unwrap();
                        continue;
                    }
                    "mkdir" => {
                        std::fs::create_dir_all(folder.join(run["path"].as_str().unwrap())).unwrap();
                        continue;
                    }
                    _ => {}
                }
                let mut params = run["params"].as_object().unwrap().clone();
                for value in params.values_mut() {
                    if let Some(text) = value.as_str() {
                        *value = text.replace("<root>", &root).into();
                    }
                }
                let mut asked = context.clone();
                asked.method = method.to_owned();
                *armed.lock().unwrap() = run["fault"].as_str().map(str::to_owned);
                let reply = crate::dispatch::dispatch(&asked, method, &params).await;
                *armed.lock().unwrap() = None;
                let got = match reply {
                    Ok(value) => serde_json::json!({ "ok": value }),
                    Err(crate::dispatch::Failure::Rejected(error)) => serde_json::json!({
                        "error": error.error_type,
                        "code": error.code,
                        "message": error.message,
                    }),
                    Err(crate::dispatch::Failure::Internal { error_type }) => {
                        serde_json::json!({ "error": error_type })
                    }
                    Err(other) => serde_json::json!({ "other": format!("{other:?}") }),
                };
                // Python's internal errors are compared by class (the generator's `Fault` is a
                // `RuntimeError`); its other failures by class, code and message. A protocol
                // refusal carries the owner's own code.
                let expected = match want["error"].as_str() {
                    None => serde_json::json!({ "ok": want["ok"] }),
                    Some("Fault") => serde_json::json!({ "error": "RuntimeError" }),
                    Some(class @ ("FileNotFoundError" | "BadZipFile")) => {
                        serde_json::json!({ "error": class })
                    }
                    Some(class) => serde_json::json!({
                        "error": class,
                        "code": if class == "OwnerProtocolError" {
                            got["code"].clone()
                        } else {
                            want["code"].as_str().unwrap_or("command_rejected").into()
                        },
                        "message": want["message"],
                    }),
                };
                let (got_text, expected_text) = (got.to_string(), expected.to_string());
                if got_text != expected_text {
                    failures.push(format!("{label}\n  Rust   {got}\n  Python {expected}"));
                }
            }
            drop(context);
            tokio::task::spawn_blocking(move || thread.join()).await.unwrap();
            let python = folder.join("python.sqlite3");
            gunzip_to(&format!("crates/sylvia-app/tests/vectors/recovery/{name}.sqlite3.gz"), &python);
            if table_rows(&database) != table_rows(&python) {
                failures.push(format!("{name}: the final database differs from Python's"));
            }
            let _ = std::fs::remove_dir_all(folder);
        }
        assert!(failures.is_empty(), "{} recovery runs differ:\n{}", failures.len(), failures.join("\n"));
    }
}
