//! The Builder agent (lane 1, handed over from lane 4): `agent/_builder_session.py`, the
//! Builder half of `agent/_runner.py`, `_mode`, `_skills`, `_builder_title`,
//! `_builder_question_answers` and the Builder tools.
//!
//! B1, the foundations: the mode, the tracked skills, the first-reply title, the question form,
//! the provider history projection and native continuation, the Builder prompt and the tool
//! evidence. The texts the model reads are exported from Python into `builder_texts.json` by
//! `generate_builder_texts.py`, never retyped.

pub mod evidence;
pub mod history;
pub mod mode;
pub mod prompt;
pub mod question_answers;
pub mod runner;
pub mod skills;
pub mod title;
pub mod tools;

use std::sync::OnceLock;
use sylvia_base::{pyjson, CodedError, Json};

/// `builder_texts.json`, read once.
pub(crate) fn texts() -> &'static Json {
    static TEXTS: OnceLock<Json> = OnceLock::new();
    TEXTS.get_or_init(|| match pyjson::loads(include_str!("builder_texts.json")) {
        Ok(texts) => texts,
        Err(error) => panic!("builder_texts.json is exported JSON: {}", error.message),
    })
}

/// Python's `CommandRejected(message)`.
pub(crate) fn rejected(message: &str) -> CodedError {
    CodedError::rejected(message).with_error_type("CommandRejected")
}

/// Python's `CommandRejected(message, code=code)`.
pub(crate) fn rejected_code(message: &str, code: &str) -> CodedError {
    CodedError::new(code, message).with_error_type("CommandRejected")
}

/// Python's `ValueError(message)`.
pub(crate) fn value_error(message: &str) -> CodedError {
    CodedError::rejected(message).with_error_type("ValueError")
}

/// Python's `TypeError(message)`.
pub(crate) fn type_error(message: String) -> CodedError {
    CodedError::rejected(message).with_error_type("TypeError")
}

/// `type(value).__name__` for saved session content, which Python keeps frozen: lists and
/// mappings are `FrozenJsonList` and `FrozenJsonDict`.
pub(crate) fn frozen_type_name(value: &Json) -> &'static str {
    match value {
        Json::Array(_) => "FrozenJsonList",
        Json::Object(_) => "FrozenJsonDict",
        other => sylvia_domain::pyops::py_type_name(other),
    }
}

/// `value in {...}` over saved (frozen) content: the set hashes `value` first.
pub(crate) fn frozen_in(value: &Json, names: &[&str]) -> sylvia_base::CodedResult<bool> {
    if matches!(value, Json::Array(_) | Json::Object(_)) {
        return Err(type_error(format!("unhashable type: '{}'", frozen_type_name(value))));
    }
    Ok(names.iter().any(|name| sylvia_domain::pyops::py_eq(value, &Json::from(*name))))
}
