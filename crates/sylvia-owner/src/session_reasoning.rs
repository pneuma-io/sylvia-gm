//! `interfaces/_session_reasoning.py`: a saved chat's reasoning and speed selection, read from
//! the configured catalog's safe capability projection. `catalog_model_info` is lane 4's
//! (`commands::providers::catalog_model_info`, ported with `set_chat_defaults`).

use crate::commands::providers::catalog_model_info;
use sylvia_base::{CodedError, CodedResult, Json, JsonMap};
use sylvia_domain::session::SessionRecordFields;
use sylvia_provider::reasoning::select_reasoning;
use sylvia_provider::speed::select_speed;
use sylvia_provider::types::ModelInfo;

/// `session.reasoning_effort` as `select_reasoning`'s `requested`: Python's `None`, or the text.
fn requested(value: &Option<String>) -> Option<Option<&str>> {
    value.as_deref().map(Some)
}

/// `a or b` for two optional names: an empty name is falsy.
fn or<'a>(first: &'a Option<String>, second: &'a Option<String>) -> &'a Option<String> {
    match first {
        Some(value) if !value.is_empty() => first,
        _ => second,
    }
}

/// `selected_model_info(session, choices)`: only catalog capability facts; a display model
/// name never grants support.
pub fn selected_model_info(
    session: &SessionRecordFields,
    choices: &[Json],
) -> CodedResult<Option<ModelInfo>> {
    catalog_model_info(&session.provider_id, &session.model_id, choices)
}

/// `validate_model_preferences(session, choices, once=...)`: the chat's explicit efforts must
/// be valid for a destination model before it is accepted.
pub fn validate_model_preferences(
    session: &SessionRecordFields,
    choices: &[Json],
    once: bool,
) -> CodedResult<()> {
    let model = selected_model_info(session, choices)?;
    let values: Vec<&Option<String>> = if once {
        vec![or(&session.reasoning_effort_once, &session.reasoning_effort)]
    } else {
        vec![&session.reasoning_effort, &session.reasoning_effort_once]
    };
    for value in values {
        let Some(text) = value else { continue };
        if select_reasoning(model.as_ref(), session.role.as_str(), requested(value), "mode_default").is_err()
        {
            return Err(CodedError::rejected(format!(
                "The destination model does not support reasoning effort '{text}'. Clear the \
                 next-reply override or select Mode default for this chat before switching models."
            )));
        }
    }
    Ok(())
}

/// `replace(session, provider_id=..., model_id=...)` for a next-reply model.
fn effective(session: &SessionRecordFields, once_model: Option<(&str, &str)>) -> SessionRecordFields {
    let mut effective = session.clone();
    if let Some((provider_id, model_id)) = once_model {
        effective.provider_id = provider_id.to_owned();
        effective.model_id = model_id.to_owned();
    }
    effective
}

/// A selection's `projection()` merged into `result`, as `{**result, **projection}` does.
fn merge(result: &mut JsonMap, projection: Json) {
    if let Json::Object(fields) = projection {
        for (key, value) in fields {
            result.insert(key, value);
        }
    }
}

/// `reasoning_state(session, choices, once_model)`: what the next request asks for, without
/// claiming the provider confirmed the effort it used.
pub fn reasoning_state(
    session: &SessionRecordFields,
    choices: &[Json],
    once_model: Option<(&str, &str)>,
) -> CodedResult<JsonMap> {
    let effective_session = effective(session, once_model);
    let model = selected_model_info(&effective_session, choices)?;
    let requested_value = or(&session.reasoning_effort_once, &session.reasoning_effort);
    let scope = if session.reasoning_effort_once.is_some() {
        "once"
    } else if session.reasoning_effort.is_some() {
        "session"
    } else {
        "mode_default"
    };
    let text = |value: &Option<String>| value.clone().map_or(Json::Null, Json::String);
    let mut result = JsonMap::new();
    result.insert("current".into(), text(&session.reasoning_effort));
    result.insert("once".into(), text(&session.reasoning_effort_once));
    result.insert("provider_id".into(), effective_session.provider_id.clone().into());
    result.insert("model_id".into(), effective_session.model_id.clone().into());
    result.insert(
        "supported".into(),
        match model.as_ref().and_then(|model| model.supported_reasoning_efforts.as_ref()) {
            Some(values) => Json::Array(values.iter().cloned().map(Json::String).collect()),
            None => Json::Null,
        },
    );
    result.insert(
        "capability_source".into(),
        model.as_ref().map_or_else(|| "unknown".to_owned(), |model| model.reasoning_source.clone()).into(),
    );
    match select_reasoning(model.as_ref(), session.role.as_str(), requested(requested_value), scope) {
        Err(error) => {
            result.insert("valid".into(), false.into());
            result.insert("error".into(), error.message.clone().into());
            result.insert("requested".into(), text(requested_value));
            result.insert("scope".into(), scope.into());
            result.insert("effective_request".into(), Json::Null);
        }
        Ok(selection) => {
            result.insert("valid".into(), true.into());
            merge(&mut result, selection.projection());
        }
    }
    Ok(result)
}

/// `speed_state(session, choices, saved_speed, once_model)`: the speed the next reply asks for,
/// for the model that will answer it. `current` is the chat's saved choice; `effective_request`
/// is nothing when that model offers no such tier.
pub fn speed_state(
    session: &SessionRecordFields,
    choices: &[Json],
    saved_speed: Option<&str>,
    once_model: Option<(&str, &str)>,
) -> CodedResult<JsonMap> {
    let effective_session = effective(session, once_model);
    let model = selected_model_info(&effective_session, choices)?;
    let selection = select_speed(model.as_ref(), saved_speed.map(Some), false, "session")?;
    let mut result = JsonMap::new();
    result.insert("current".into(), saved_speed.map_or(Json::Null, Json::from));
    result.insert("provider_id".into(), effective_session.provider_id.clone().into());
    result.insert("model_id".into(), effective_session.model_id.clone().into());
    result.insert(
        "supported".into(),
        match model
            .as_ref()
            .and_then(|model| model.speed_options.as_ref())
            .filter(|options| !options.is_empty())
        {
            Some(options) => Json::Array(options.iter().map(|option| option.projection()).collect()),
            None => Json::Null,
        },
    );
    result.insert(
        "capability_source".into(),
        model.as_ref().map_or_else(|| "unknown".to_owned(), |model| model.speed_source.clone()).into(),
    );
    result.insert("valid".into(), (saved_speed.is_none() || selection.effective.is_some()).into());
    merge(&mut result, selection.projection());
    Ok(result)
}
