//! `domain/mechanics_choices.py`: bounded public choice records used by durable deterministic
//! action reservations.

use crate::authority::{
    mechanic_outcome_is_safe, validate_authority_identity_value, MechanicOutcome, MechanicsChoiceAnswer,
    MechanicsChoiceOption, MechanicsChoiceRequest,
};
use crate::error::{DomainError, DomainResult};
use crate::pyops::{py_eq, py_require_hashable, py_set_equals};
use serde_json::json;
use sylvia_base::{Json, JsonMap};

fn object(value: Json) -> JsonMap {
    match value {
        Json::Object(map) => map,
        _ => unreachable!("built as an object"),
    }
}

/// `choice_payload(choice)`: only the public descriptor, without an interpreter continuation.
pub fn choice_payload(choice: &MechanicsChoiceRequest) -> JsonMap {
    let options: Vec<Json> =
        choice.options.iter().map(|o| json!({"option_id": o.option_id, "label": o.label})).collect();
    object(json!({"choice_id": choice.choice_id, "prompt": choice.prompt, "options": options}))
}

/// `set(value) != {names}` for a `dict` (its keys are always strings).
fn keys_differ(value: &Json, names: &[&str]) -> bool {
    !py_set_equals(value, names).unwrap_or(false)
}

/// `choice_from_payload(value)`: reject malformed saved requests before replaying any answer.
pub fn choice_from_payload(value: &Json) -> DomainResult<MechanicsChoiceRequest> {
    if !value.is_object() || keys_differ(value, &["choice_id", "prompt", "options"]) {
        return Err(DomainError::value("Saved mechanics choice fields are invalid"));
    }
    let Some(options) = value["options"].as_array() else {
        return Err(DomainError::value("Saved mechanics choice options are invalid"));
    };
    if options.iter().any(|o| !o.is_object() || keys_differ(o, &["option_id", "label"])) {
        return Err(DomainError::value("Saved mechanics choice option fields are invalid"));
    }
    // Python builds the request from whatever the fields hold; `mechanic_outcome_is_safe`
    // then refuses any field that is not text. The Rust record holds text only.
    let invalid = || DomainError::value("Saved mechanics choice is invalid");
    let text = |field: &Json| field.as_str().map(str::to_owned).ok_or_else(invalid);
    let mut parsed = Vec::with_capacity(options.len());
    for option in options {
        parsed.push(MechanicsChoiceOption {
            option_id: text(&option["option_id"])?,
            label: text(&option["label"])?,
        });
    }
    let choice = MechanicsChoiceRequest {
        choice_id: text(&value["choice_id"])?,
        prompt: text(&value["prompt"])?,
        options: parsed,
    };
    let outcome = MechanicOutcome { choice: Some(choice), ..Default::default() };
    if !mechanic_outcome_is_safe(&outcome) {
        return Err(invalid());
    }
    Ok(outcome.choice.expect("set above"))
}

/// `answer_payload(answer)`: the exact request and caller identity needed for safe retries.
pub fn answer_payload(answer: &MechanicsChoiceAnswer) -> JsonMap {
    object(json!({
        "resolution_id": answer.resolution_id,
        "request": choice_payload(&answer.request),
        "option_id": answer.option_id,
        "session_id": answer.session_id,
    }))
}

/// `answers_from_payload(value)`: at most 63 prior answers, leaving room for the current 64th.
pub fn answers_from_payload(value: &Json) -> DomainResult<Vec<MechanicsChoiceAnswer>> {
    if value.is_null() {
        return Ok(Vec::new());
    }
    let Some(items) = value.as_array().filter(|items| items.len() < 64) else {
        return Err(DomainError::value("Saved mechanics choice sequence is invalid"));
    };
    let mut answers = Vec::with_capacity(items.len());
    let mut identities: Vec<String> = Vec::new();
    for item in items {
        if !item.is_object() || keys_differ(item, &["resolution_id", "request", "option_id", "session_id"]) {
            return Err(DomainError::value("Saved mechanics choice answer fields are invalid"));
        }
        let resolution_id =
            validate_authority_identity_value(&item["resolution_id"], "Choice resolution identity", false)?;
        if identities.iter().any(|existing| existing == resolution_id) {
            return Err(DomainError::value("Saved mechanics choice answers repeat a resolution identity"));
        }
        identities.push(resolution_id.to_owned());
        let request = choice_from_payload(&item["request"])?;
        // `item["option_id"] not in {o.option_id for o in request.options}`.
        let option_id = &item["option_id"];
        py_require_hashable(option_id)?;
        if !request.options.iter().any(|o| py_eq(option_id, &Json::from(o.option_id.as_str()))) {
            return Err(DomainError::value("Saved mechanics choice option is invalid"));
        }
        let session_id = match &item["session_id"] {
            Json::Null => None,
            other => {
                Some(validate_authority_identity_value(other, "Choice session identity", false)?.to_owned())
            }
        };
        answers.push(MechanicsChoiceAnswer {
            resolution_id: resolution_id.to_owned(),
            request,
            option_id: option_id.as_str().expect("equal to an option's text").to_owned(),
            session_id,
        });
    }
    Ok(answers)
}
