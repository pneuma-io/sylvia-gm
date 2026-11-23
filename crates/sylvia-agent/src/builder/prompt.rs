//! `_creation_subflow_purpose`, `_effective_tracked_skill` and `_builder_prompt` from
//! `agent/_builder_session.py`. The prompt's texts are Python's, exported into
//! `builder_texts.json`.

use super::skills::load_builder_skill;
use super::texts;
use sylvia_base::hash::sha256_hex;
use sylvia_base::{CodedResult, Json, JsonMap};
use sylvia_domain::pyops::py_eq;
use sylvia_domain::session::{SessionContext, SessionMessageKind};

fn is(content: &JsonMap, key: &str, text: &str) -> bool {
    content.get(key).is_some_and(|value| py_eq(value, &Json::from(text)))
}

/// `_creation_subflow_purpose(context)`: the owner-recorded creation purpose from a saved launch
/// or legacy link.
pub fn creation_subflow_purpose(context: &SessionContext) -> Option<String> {
    for message in &context.messages {
        let content = message.content();
        if message.kind() == SessionMessageKind::Evidence
            && (is(content, "kind", "creation_subflow_link") || is(content, "kind", "builder_launch_context"))
        {
            return match content.get("purpose") {
                Some(Json::String(purpose)) if !purpose.is_empty() => Some(purpose.clone()),
                _ => None,
            };
        }
    }
    None
}

fn character_purpose(purpose: Option<&str>) -> bool {
    matches!(purpose, Some("player_character" | "companion"))
}

/// `_effective_tracked_skill(context)`: character and companion work stays general despite the
/// legacy session storage field.
pub fn effective_tracked_skill(context: &SessionContext) -> Option<String> {
    if character_purpose(creation_subflow_purpose(context).as_deref()) {
        return None;
    }
    context.session.fields().selected_skill.clone()
}

/// A text the exporter read from `_builder_prompt`.
fn text(name: &str) -> &'static str {
    texts()["prompt"][name].as_str().unwrap_or_default()
}

/// An exported f-string, its names substituted by `value`.
fn fill(name: &str, value: impl Fn(&str) -> String) -> String {
    texts()["prompt"][name]
        .as_array()
        .into_iter()
        .flatten()
        .map(|part| match part {
            Json::String(text) => text.clone(),
            other => value(other["name"].as_str().unwrap_or_default()),
        })
        .collect()
}

/// `_builder_prompt(context)`.
pub fn builder_prompt(context: &SessionContext) -> CodedResult<String> {
    let skill = effective_tracked_skill(context);
    let mut blocks = Vec::new();
    if let Some(name) = &skill {
        let loaded = load_builder_skill(name, None)?;
        let sha256 = sha256_hex(loaded.text.as_bytes());
        blocks.push(fill("skill_block", |part| match part {
            "skill_name" => name.clone(),
            "skill_sha256" => sha256.clone(),
            "loaded_skill.text" => loaded.text.clone(),
            other => panic!("unexpected skill block part {other}"),
        }));
    }
    let skill_instruction = match &skill {
        None => text("skill_general").to_owned(),
        Some(name) => fill("skill_selected", |_| name.clone()),
    };
    let messages = &context.messages;
    let evidence_kind = |kind: &str| {
        messages.iter().any(|message| {
            message.kind() == SessionMessageKind::Evidence && is(message.content(), "kind", kind)
        })
    };
    let any_kind = |kind: &str| messages.iter().any(|message| is(message.content(), "kind", kind));
    let when = |condition: bool, name: &str| if condition { text(name) } else { "" };
    let purpose = creation_subflow_purpose(context);
    let subflow =
        when(context.session.campaign_id().is_some() && evidence_kind("creation_subflow_link"), "subflow");
    let launch = when(any_kind("builder_launch_context"), "launch");
    let participant = when(character_purpose(purpose.as_deref()), "participant");
    let required_character = when(
        messages.iter().any(|message| {
            let content = message.content();
            is(content, "kind", "builder_launch_context")
                && is(content, "purpose", "plan")
                && is(content, "interview_step", "participants")
        }),
        "required_character",
    );
    let repair = when(any_kind("import_repair_context"), "repair");
    let head = fill("template", |part| match part {
        "skill_instruction" => skill_instruction.clone(),
        "subflow_instruction" => subflow.to_owned(),
        "launch_instruction" => launch.to_owned(),
        "participant_instruction" => participant.to_owned(),
        "repair_instruction" => repair.to_owned(),
        "required_character_instruction" => required_character.to_owned(),
        other => panic!("unexpected prompt part {other}"),
    });
    Ok(head + &blocks.join(text("block_separator")))
}
