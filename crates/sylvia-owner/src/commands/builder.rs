//! Builder chat commands: `interfaces/commands/builder.py`.

use crate::dispatch::DispatchContext;
use sylvia_base::{CodedResult, Json, JsonMap};
use sylvia_domain::session::{BuilderChatRecord, SessionRecord};

/// `_json_value(SessionRecord)`: the dataclass fields in declaration order, enums by value.
pub(crate) fn session_record_value(record: &SessionRecord) -> Json {
    let fields = record.fields();
    let mut value = JsonMap::new();
    value.insert("session_id".into(), fields.session_id.clone().into());
    value.insert("campaign_id".into(), fields.campaign_id.clone().into());
    value.insert("role".into(), fields.role.as_str().into());
    value.insert("provider_id".into(), fields.provider_id.clone().into());
    value.insert("model_id".into(), fields.model_id.clone().into());
    value.insert("status".into(), fields.status.as_str().into());
    value.insert("parent_session_id".into(), fields.parent_session_id.clone().into());
    value.insert("selected_skill".into(), fields.selected_skill.clone().into());
    value.insert("scope".into(), Json::Array(fields.scope.iter().cloned().map(Json::from).collect()));
    value.insert("reasoning_effort".into(), fields.reasoning_effort.clone().into());
    value.insert("reasoning_effort_once".into(), fields.reasoning_effort_once.clone().into());
    Json::Object(value)
}

/// `_json_value(BuilderChatRecord)`.
pub(crate) fn builder_chat_value(chat: &BuilderChatRecord) -> Json {
    let mut value = JsonMap::new();
    value.insert("session".into(), session_record_value(chat.session()));
    value.insert("display_name".into(), chat.display_name().into());
    value.insert("created_ordinal".into(), chat.created_ordinal().into());
    Json::Object(value)
}

/// `handle_builder_chat_index`: `application_owner.builder_chat_index()`, which is
/// `_operation()` around the store's read. Parameters are not read.
pub async fn handle_builder_chat_index(ctx: &DispatchContext, _params: &JsonMap) -> CodedResult<Json> {
    let _operation = super::operation(ctx)?;
    let chats =
        ctx.store.call(|store| store.builder_chat_index()).await.map_err(super::campaigns::store_error)?;
    Ok(Json::Array(chats.iter().map(builder_chat_value).collect()))
}

/// `_started_builder_chat(ctx, chat, start, replayed=...)`: save a new chat's start settings
/// and answer the chat as it now stands. A replay answers the saved chat unchanged.
async fn started_builder_chat(
    ctx: &DispatchContext,
    chat: &BuilderChatRecord,
    start: &crate::chat_start::ChatStart,
    replayed: bool,
) -> CodedResult<Json> {
    let mut value = builder_chat_value(chat);
    if replayed {
        return Ok(value);
    }
    crate::chat_start::apply_chat_start(ctx, chat.session().fields().session_id.as_str(), start).await?;
    // `dataclasses.replace(chat, session=replace(chat.session, reasoning_effort=...))`.
    if let (Some(effort), Some(session)) = (&start.reasoning_effort, value.get_mut("session")) {
        session["reasoning_effort"] = Json::String(effort.clone());
    }
    Ok(value)
}

/// `handle_create_builder_chat`: a new standalone Builder chat. Its session id comes from the
/// operation id, so the same operation replays the same chat. Without both `provider_id` and
/// `model_id` it uses Settings' default model; its start settings are resolved before the chat
/// exists and saved only when this is not a replay.
pub async fn handle_create_builder_chat(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let operation_id = required(params, "operation_id")?;
    let session_id =
        format!("session-builder-{}", &sylvia_base::hash::sha256_hex(operation_id.as_bytes())[..24]);
    let (provider_id, model_id) = chat_model(ctx, params)?;
    let start = crate::chat_start::chat_start_for(ctx, "builder", &provider_id, &model_id, params).await?;
    let replayed = crate::chat_start::session_exists(ctx, &session_id).await?;
    let display_name =
        super::campaigns::optional_text(params, "display_name")?.unwrap_or_else(|| "New chat".to_owned());
    let chat = ctx
        .store
        .call(move |store| store.create_builder_chat(&session_id, &provider_id, &model_id, &display_name))
        .await
        .map_err(super::campaigns::store_error)?;
    started_builder_chat(ctx, &chat, &start, replayed).await
}

/// A new chat's `provider_id` and `model_id`: both given, or else both from Settings' default
/// model (a missing or non-object default refuses).
fn chat_model(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<(String, String)> {
    let provider_id = super::campaigns::optional_text(params, "provider_id")?;
    let model_id = super::campaigns::optional_text(params, "model_id")?;
    if let (Some(provider_id), Some(model_id)) = (provider_id, model_id) {
        return Ok((provider_id, model_id));
    }
    let path = super::providers::config_path(ctx);
    let settings = super::providers::with_sources(|sources| {
        sylvia_provider::config::provider_settings::provider_settings(sources, &path)
    })?;
    let settings = sylvia_provider::config::reply_json(&settings).map_err(super::providers::config_error)?;
    let Some(Json::Object(default)) = settings.get("default_model") else {
        return Err(sylvia_base::CodedError::rejected("Configure a default model before creating a chat"));
    };
    Ok((required(default, "provider_id")?, required(default, "model_id")?))
}

/// `handle_start_import_repair`: a Builder repair chat for a failed import the player chose to
/// fix (`start_import_repair_chat`). Its model is chosen as `create_builder_chat` chooses one and
/// its start settings are resolved first; the chat index is read before the start, so a chat
/// already in it is a replay, answered unchanged. `repair_id` and `operation_id` are read last.
pub async fn handle_start_import_repair(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let (provider_id, model_id) = chat_model(ctx, params)?;
    let start = crate::chat_start::chat_start_for(ctx, "builder", &provider_id, &model_id, params).await?;
    let existing: Vec<String> = {
        let _operation = super::operation(ctx)?;
        let chats = ctx
            .store
            .call(|store| store.builder_chat_index())
            .await
            .map_err(super::campaigns::store_error)?;
        chats.iter().map(|chat| chat.session().fields().session_id.clone()).collect()
    };
    let repair_id = required(params, "repair_id")?;
    let operation_id = required(params, "operation_id")?;
    let chat = sylvia_app::import_repair_sessions::start_import_repair_chat(
        &ctx.store,
        &ctx.storage_root,
        &repair_id,
        &operation_id,
        &provider_id,
        &model_id,
    )
    .await
    .map_err(super::campaigns::store_error)?;
    let replayed = existing.contains(&chat.session().fields().session_id);
    started_builder_chat(ctx, &chat, &start, replayed).await
}

/// `_required_text(params, key)` as a handler's error.
fn required(params: &JsonMap, key: &str) -> CodedResult<String> {
    crate::dispatch::required_text(params, key).map_err(|failure| match failure {
        crate::dispatch::Failure::Rejected(error) => error,
        _ => unreachable!("a missing parameter is a rejection"),
    })
}

/// `handle_rename_builder_chat`: `application_owner.rename_builder_chat(session_id,
/// display_name)`, a manual rename (`automatic=False`). Lane 3's store operation holds the
/// session lane, as `AuthorityOwner` does.
pub async fn handle_rename_builder_chat(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let session_id = required(params, "session_id")?;
    let display_name = required(params, "display_name")?;
    let chat = ctx
        .store
        .rename_builder_chat(&session_id, &display_name, false)
        .await
        .map_err(super::campaigns::store_error)?;
    Ok(builder_chat_value(&chat))
}

/// `handle_delete_builder_chat`: `application_owner.delete_builder_chat(session_id,
/// confirmation=)`. Lane 3's store operation makes `AuthorityOwner`'s identity and exact
/// confirmation checks and takes the Builder chat's owner lane without waiting.
pub async fn handle_delete_builder_chat(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let session_id = required(params, "session_id")?;
    let confirmation = required(params, "confirmation")?;
    let chat = ctx
        .store
        .delete_builder_chat(&session_id, &confirmation)
        .await
        .map_err(super::campaigns::store_error)?;
    Ok(builder_chat_value(&chat))
}

#[cfg(test)]
mod tests {
    use super::*;
    use sylvia_domain::authority::WriterRole;
    use sylvia_domain::session::{SessionRecordFields, SessionStatus};

    fn text(value: &Json) -> Option<String> {
        value.as_str().map(str::to_owned)
    }

    /// Rebuild a Builder chat from Python's rendering of it.
    fn chat_from(value: &Json) -> BuilderChatRecord {
        let session = &value["session"];
        let fields = SessionRecordFields {
            session_id: text(&session["session_id"]).unwrap(),
            campaign_id: text(&session["campaign_id"]),
            role: WriterRole::parse(session["role"].as_str().unwrap()).unwrap(),
            provider_id: text(&session["provider_id"]).unwrap(),
            model_id: text(&session["model_id"]).unwrap(),
            status: SessionStatus::parse(session["status"].as_str().unwrap()).unwrap(),
            parent_session_id: text(&session["parent_session_id"]),
            selected_skill: text(&session["selected_skill"]),
            scope: session["scope"].as_array().unwrap().iter().filter_map(text).collect(),
            reasoning_effort: text(&session["reasoning_effort"]),
            reasoning_effort_once: text(&session["reasoning_effort_once"]),
        };
        BuilderChatRecord::new(
            SessionRecord::new(fields).unwrap(),
            value["display_name"].as_str().unwrap(),
            value["created_ordinal"].as_i64().unwrap(),
        )
        .unwrap()
    }

    #[test]
    fn builder_chats_keep_pythons_field_order() {
        // Lane 3's Python run of the Builder chat operations, rendered in dataclass order.
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../crates/sylvia-store/tests/vectors/sessions/session_runs.json");
        let runs: Json = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
        let run = &runs["runs"]["builder_chats"];
        let mut checked = 0;
        for (operation, outcome) in
            run["operations"].as_array().unwrap().iter().zip(run["outcomes"].as_array().unwrap())
        {
            if operation["op"] != "builder_chat_index" {
                continue;
            }
            let chats = outcome["ok"].as_array().unwrap();
            for chat in chats {
                assert_eq!(builder_chat_value(&chat_from(chat)).to_string(), chat.to_string());
                checked += 1;
            }
        }
        assert!(checked >= 2, "the run lists Builder chats");
    }

    #[test]
    fn renamed_and_deleted_chats_reply_in_pythons_field_order() {
        // The records lane 3's Python run returned from a rename and a delete: the handlers'
        // replies. The owner session `builder-chat-edit` covers the refusals, and
        // `builder-chat-create` a rename and a delete of real chats.
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../crates/sylvia-store/tests/vectors/sessions/session_runs.json");
        let runs: Json = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
        let run = &runs["runs"]["builder_chats"];
        let mut checked = std::collections::BTreeMap::new();
        for (operation, outcome) in
            run["operations"].as_array().unwrap().iter().zip(run["outcomes"].as_array().unwrap())
        {
            let op = operation["op"].as_str().unwrap();
            if !matches!(op, "rename_builder_chat" | "delete_builder_chat") {
                continue;
            }
            if let Some(chat) = outcome.get("ok") {
                assert_eq!(builder_chat_value(&chat_from(chat)).to_string(), chat.to_string());
                *checked.entry(op).or_insert(0) += 1;
            }
        }
        assert_eq!(checked.get("rename_builder_chat"), Some(&3));
        assert_eq!(checked.get("delete_builder_chat"), Some(&1));
    }
}
