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
/// message and class; anything else is the owner's internal error.
pub(crate) fn store_error(error: sylvia_store::StoreError) -> sylvia_base::CodedError {
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
fn seed_campaign_plan(storage_root: &std::path::Path, campaign_id: &str, text: &str) -> CodedResult<()> {
    use std::io::Write;
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
        return Ok(());
    }
    let nonce =
        std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0, |time| time.as_nanos());
    let temporary = folder.join(format!(".campaign-plan-{}-{nonce}.tmp", std::process::id()));
    let written = (|| -> std::io::Result<()> {
        let mut file = std::fs::OpenOptions::new().write(true).create_new(true).open(&temporary)?;
        file.write_all(encoded)?;
        file.flush()?;
        file.sync_all()?;
        drop(file);
        std::fs::rename(&temporary, &target)
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

/// `handle_start_guided_campaign` for a Draft with no Ruleset. An installed Ruleset is refused
/// as not yet ported once it reaches the Interview content lane 1 owns, and a saved Campaign Plan
/// (`plan_id`) needs lane 1's library selection and party materialisation.
pub async fn handle_start_guided_campaign(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let operation_id = required(params, "operation_id")?;
    let campaign_id = required(params, "campaign_id")?;
    let builder_session_id = required(params, "builder_session_id")?;
    let candidate_id = params.get("candidate_id").cloned().unwrap_or(Json::Null);
    if !matches!(params.get("plan_id"), None | Some(Json::Null)) {
        required(params, "candidate_id")?;
        required(params, "setting_id")?;
        required(params, "plan_id")?;
        return Err(sylvia_base::CodedError::new(
            "internal_error",
            "not yet ported: a saved Campaign Plan Start (lane 1's resolve_campaign_library_selection)",
        )
        .with_error_type("RuntimeError"));
    }
    let package_lock = match &candidate_id {
        Json::Null => sylvia_domain::authority::PackageLock::new(Vec::new())
            .map_err(|error| sylvia_base::CodedError::new("internal_error", error.to_string()))?,
        Json::String(id) if !id.is_empty() => {
            let id = id.clone();
            let candidate =
                ctx.store.call(move |store| store.package_candidate(&id)).await.map_err(store_error)?;
            candidate.package_lock().clone()
        }
        _ => return Err(protocol("candidate_id must be a nonempty string or null")),
    };
    let premise = match params.get("premise") {
        None => String::new(),
        Some(Json::String(text)) if text.chars().count() <= MAX_USER_TEXT_CHARS => text.clone(),
        Some(_) => return Err(protocol("premise must be a bounded string")),
    };
    let premise = sylvia_base::pystr::strip(&premise).to_owned();
    let mut canonical_input = JsonMap::new();
    if !premise.is_empty() {
        canonical_input.insert("premise".into(), premise.as_str().into());
    }
    if truthy(params.get("player_character_id")) || truthy(params.get("companion_ids")) {
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
        &sylvia_app::authority::guided::BindRequest {
            operation_id: &format!("{operation_id}-bind"),
            campaign_id: &campaign_id,
            display_name: &display_name,
            package_lock: &package_lock,
            turn_id: &format!("turn-construction-{digest}"),
            builder_session_id: &builder_session_id,
            package_candidate_id: package_candidate_id.as_deref(),
        },
    )
    .await
    .map_err(store_error)?;
    if package_candidate_id.is_none() {
        seed_campaign_plan(&ctx.storage_root, &campaign_id, &no_ruleset_plan(&display_name, &premise))?;
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

/// `_required_nonnegative_int(payload, key)`: an integer, not a bool, at least zero. An integer
/// beyond 64 bits, which Python would take, is refused as a declared limit.
fn required_nonnegative_int(params: &JsonMap, key: &str) -> CodedResult<i64> {
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
fn required(params: &JsonMap, key: &str) -> CodedResult<String> {
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Read;
    use std::path::Path;

    fn workspace(path: &str) -> std::path::PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../..").join(path)
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
        let _ = std::fs::remove_dir_all(&folder);
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
}
