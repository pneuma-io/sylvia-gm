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
    let chats =
        ctx.store.call(|store| store.builder_chat_index()).await.map_err(super::campaigns::store_error)?;
    Ok(Json::Array(chats.iter().map(builder_chat_value).collect()))
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
}
