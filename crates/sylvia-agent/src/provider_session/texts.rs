//! The GM request's fixed texts and the small messages built from them.
//!
//! The long texts are data: `gm_texts.json`, exported from the Python values by
//! `generate_gm_texts.py`. The short ones are literals copied byte for byte.

use serde_json::{json, Value as Json};
use std::sync::OnceLock;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::JsonMap;
use sylvia_provider::model_facts::uses_gm_narration_hint;
use sylvia_provider::Provider;

fn texts() -> &'static JsonMap {
    static TEXTS: OnceLock<JsonMap> = OnceLock::new();
    TEXTS.get_or_init(|| match pyjson::loads(include_str!("gm_texts.json")) {
        Ok(Json::Object(map)) => map,
        _ => panic!("gm_texts.json is an exported JSON object"),
    })
}

fn text(key: &str) -> &'static str {
    texts()[key].as_str().expect("an exported text")
}

/// `_dm_system_prompt(...)`: the stable same-agent GM contract (its arguments are unused).
pub fn dm_system_prompt() -> &'static str {
    text("gm_system_prompt")
}

/// `_GM_SYSTEM_CONTRACT_VERSION`.
pub fn gm_system_contract_version() -> i64 {
    texts()["gm_system_contract_version"].as_i64().expect("an exported version")
}

/// `_GM_CONTEXT_PRIVACY`.
pub fn gm_context_privacy() -> &'static str {
    text("gm_context_privacy")
}

/// `_model_gm_narration_block(provider)`: the narration reminder for the models that need it.
pub fn model_gm_narration_block(provider: &dyn Provider) -> Option<&'static str> {
    provider
        .model_info()
        .filter(|model| uses_gm_narration_hint(model))
        .map(|_| text("gm_model_narration_block"))
}

/// A `{"role": "system", "content": ...}` message.
pub(crate) fn system(content: String) -> JsonMap {
    let mut message = JsonMap::new();
    message.insert("role".into(), "system".into());
    message.insert("content".into(), content.into());
    message
}

/// `_response_format_contract_message()`.
pub fn response_format_contract_message() -> JsonMap {
    system(text("response_format_contract").to_owned())
}

/// The current host interface added beside a saved contract of another version.
pub fn context_contract_update_message() -> JsonMap {
    system(format!(
        "SYLVIA_GM_CONTEXT_CONTRACT v{} (current application control): {} When a required tool call is \
         missing, prose is held as an unsettled draft until the action and Campaign File disposition are \
         settled. Preserve committed results. Use exact IDs from SYLVIA_GM_CAMPAIGN_PREPARATION or \
         SYLVIA_GM_FRESH_CAMPAIGN_STATE.",
        gm_system_contract_version(),
        gm_context_privacy(),
    ))
}

/// `_campaign_opening_task_message(opening_footing)`: the one-time opening task, with the
/// footing last.
pub fn campaign_opening_task_message(opening_footing: &Json) -> JsonMap {
    system(format!("{}{}", text("opening_task_prefix"), pyjson::dumps(opening_footing, Dump::CANONICAL)))
}

/// `_player_turn_text(content)`: in-character text alone, or an explicit IC/OOC record when the
/// player wrote an out-of-character part.
pub fn player_turn_text(content: &JsonMap) -> String {
    let in_character = content.get("text").and_then(Json::as_str).unwrap_or("");
    let Some(out_of_character) =
        content.get("ooc_text").and_then(Json::as_str).filter(|text| !text.is_empty())
    else {
        return in_character.to_owned();
    };
    let record = json!({
        "in_character": if in_character.is_empty() { Json::Null } else { in_character.into() },
        "out_of_character": out_of_character,
    });
    format!("SYLVIA_PLAYER_TURN\n{}", pyjson::dumps(&record, Dump::WIRE.ascii(false)))
}
