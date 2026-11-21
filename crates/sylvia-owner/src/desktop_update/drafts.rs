//! Local unsent text, scoped to the selected data folder; never executable work:
//! `application/desktop_update/drafts.py`.

use super::manifest::{bounded_json, canonical_bytes, MANIFEST_LIMIT};
use super::storage::{atomic_receipt, checked_file, directory_identity, operation_id, Identity};
use super::update_error;
use serde_json::json;
use std::collections::HashSet;
use std::path::{Path, PathBuf};
use sylvia_base::{CodedResult, Json};

/// `MAX_SESSIONS`.
pub const MAX_SESSIONS: usize = 128;
/// `MAX_QUESTIONS`.
pub const MAX_QUESTIONS: usize = 256;
/// `MAX_TEXT`, in characters.
pub const MAX_TEXT: usize = 256 * 1024;

/// `_text(value, maximum, nonempty=)`: a string of at most `maximum` characters.
fn text(value: &Json, maximum: usize, nonempty: bool) -> bool {
    value.as_str().is_some_and(|text| (!nonempty || !text.is_empty()) && text.chars().count() <= maximum)
}

/// Python's `type(value) is int and value == 1`.
fn is_int_one(value: &Json) -> bool {
    value.as_number().is_some_and(|number| (number.is_i64() || number.is_u64()) && number.as_i64() == Some(1))
}

fn keys_are(value: &serde_json::Map<String, Json>, expected: &[&str]) -> bool {
    value.len() == expected.len() && expected.iter().all(|key| value.contains_key(*key))
}

/// `validate_drafts(value)`: reject a whole invalid snapshot rather than truncating or
/// dropping unsent fields.
pub fn validate_drafts(value: &Json) -> CodedResult<()> {
    let invalid = || update_error("invalid_drafts", "Unsent drafts could not be saved safely.");
    let Some(top) = value.as_object() else { return Err(invalid()) };
    let allowed = ["format_version", "sessions", "update_view"];
    if !(top.contains_key("format_version") && top.contains_key("sessions"))
        || top.keys().any(|key| !allowed.contains(&key.as_str()))
        || !is_int_one(&top["format_version"])
    {
        return Err(invalid());
    }
    let Some(sessions) = top["sessions"].as_array().filter(|sessions| sessions.len() <= MAX_SESSIONS) else {
        return Err(invalid());
    };
    if let Some(view) = top.get("update_view") {
        let valid = view.as_object().is_some_and(|view| {
            keys_are(view, &["operation_id", "kind", "campaign_id", "session_id"])
                && matches!(view["kind"].as_str(), Some("home" | "session"))
                && [&view["campaign_id"], &view["session_id"]]
                    .iter()
                    .all(|item| item.is_null() || text(item, 256, true))
                && !(view["kind"] == "home"
                    && (!view["campaign_id"].is_null() || !view["session_id"].is_null()))
                && !(view["kind"] == "session" && view["session_id"].is_null())
        });
        if !valid {
            return Err(update_error("invalid_drafts", "The saved update view is invalid."));
        }
        operation_id(&view["operation_id"])?;
    }
    let mut seen_sessions: HashSet<&str> = HashSet::new();
    for row in sessions {
        let valid = row.as_object().is_some_and(|row| {
            keys_are(row, &["session_id", "text", "ooc_text", "ooc_open", "questions"])
                && text(&row["session_id"], 256, true)
                && !seen_sessions.contains(row["session_id"].as_str().unwrap_or_default())
                && text(&row["text"], MAX_TEXT, false)
                && text(&row["ooc_text"], MAX_TEXT, false)
                && row["ooc_open"].is_boolean()
                && row["questions"].as_array().is_some_and(|questions| questions.len() <= MAX_QUESTIONS)
        });
        if !valid {
            return Err(invalid());
        }
        seen_sessions.insert(row["session_id"].as_str().expect("checked"));
        let mut seen_questions: HashSet<&str> = HashSet::new();
        for question in row["questions"].as_array().expect("checked") {
            let valid = question.as_object().is_some_and(|question| {
                let allowed = ["question_key", "text", "custom", "selection"];
                question.contains_key("question_key")
                    && question.keys().all(|key| allowed.contains(&key.as_str()))
                    && text(&question["question_key"], 256, true)
                    && !seen_questions.contains(question["question_key"].as_str().unwrap_or_default())
                    && question.iter().all(|(key, item)| key == "question_key" || text(item, MAX_TEXT, false))
            });
            if !valid {
                return Err(update_error("invalid_drafts", "Unsent question fields could not be saved."));
            }
            seen_questions.insert(question["question_key"].as_str().expect("checked"));
        }
    }
    if canonical_bytes(value).len() > MANIFEST_LIMIT {
        return Err(update_error(
            "draft_limit",
            "Unsent drafts are too large to save. Keep this window open.",
        ));
    }
    Ok(())
}

/// `DraftStore`: `.desktop-drafts.json` in the data folder it was opened for.
pub struct DraftStore {
    root: PathBuf,
    identity: Identity,
    path: PathBuf,
}

impl DraftStore {
    /// `DraftStore(storage_root)`. Refuses an aliased or linked folder.
    pub fn new(storage_root: &Path) -> CodedResult<Self> {
        let identity = directory_identity(storage_root)?;
        Ok(Self {
            root: storage_root.to_path_buf(),
            identity,
            path: storage_root.join(".desktop-drafts.json"),
        })
    }

    fn check(&self) -> CodedResult<()> {
        if directory_identity(&self.root)? != self.identity {
            return Err(update_error("changed_draft_root", "The selected draft storage folder changed."));
        }
        Ok(())
    }

    /// `load()`: the saved drafts, or an empty set when none were saved.
    pub fn load(&self) -> CodedResult<Json> {
        self.check()?;
        if !self.path.exists() && !self.path.is_symlink() {
            return Ok(json!({"format_version": 1, "sessions": []}));
        }
        let value = bounded_json(&checked_file(&self.path, MANIFEST_LIMIT)?, MANIFEST_LIMIT)?;
        validate_drafts(&value)?;
        Ok(value)
    }

    /// `save(value)`.
    pub fn save(&self, value: &Json) -> CodedResult<Json> {
        self.check()?;
        validate_drafts(value)?;
        atomic_receipt(&self.path, value)?;
        Ok(json!({"status": "saved"}))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn message(value: Json) -> String {
        validate_drafts(&value).unwrap_err().message
    }

    #[test]
    fn validates_like_python() {
        let row = json!({"session_id": "s", "text": "", "ooc_text": "", "ooc_open": false, "questions": []});
        assert!(validate_drafts(&json!({"format_version": 1, "sessions": [row]})).is_ok());
        assert_eq!(
            message(json!({"format_version": true, "sessions": []})),
            "Unsent drafts could not be saved safely."
        );
        assert_eq!(
            message(json!({"format_version": 1.0, "sessions": []})),
            "Unsent drafts could not be saved safely."
        );
        assert_eq!(
            message(json!({"format_version": 1, "sessions": [row, row]})),
            "Unsent drafts could not be saved safely."
        );
        assert_eq!(
            message(
                json!({"format_version": 1, "sessions": [], "update_view": {"operation_id": "x", "kind": "home",
                "campaign_id": "c", "session_id": null}})
            ),
            "The saved update view is invalid."
        );
        assert_eq!(
            validate_drafts(
                &json!({"format_version": 1, "sessions": [], "update_view": {"operation_id": "x",
                "kind": "home", "campaign_id": null, "session_id": null}})
            )
            .unwrap_err()
            .code,
            "invalid_operation"
        );
        let question = json!({"session_id": "s", "text": "", "ooc_text": "", "ooc_open": false,
            "questions": [{"question_key": "q", "selection": 3}]});
        assert_eq!(
            message(json!({"format_version": 1, "sessions": [question]})),
            "Unsent question fields could not be saved."
        );
    }
}
