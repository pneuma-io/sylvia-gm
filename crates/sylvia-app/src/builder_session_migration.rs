//! `application/builder_session_migration.py`: upgrade former campaign-creation content chats
//! without a return handshake, retaining each original launch and shared file snapshot.
//!
//! The owner is [`MigrationOwner`]: the reference owner and the store calls the migration makes.
//! `AuthorityStore` implements it with lane 5's store half (`builder_launch_migration`).

use crate::builder_references::{capture_launch_references, OwnerError, ReferenceOwner};
use crate::bundled_packages::BundledPackages;
use std::path::Path;
use sylvia_base::{pystr, Json, JsonMap};
use sylvia_domain::error::DomainError;
use sylvia_domain::pyops::{py_eq, py_item, py_iter, py_str, py_type_name};
use sylvia_domain::session::{SessionContext, SessionMessageKind};
use sylvia_store::{AuthorityStore, StoreError};

/// The owner `migrate_legacy_builder_chats` uses.
pub trait MigrationOwner: ReferenceOwner {
    /// `owner.legacy_builder_contexts()`.
    fn legacy_builder_contexts(&self) -> Result<Vec<SessionContext>, Self::Error>;
    /// `owner.session_context(session_id)`.
    fn session_context(&self, session_id: &str) -> Result<SessionContext, Self::Error>;
    /// `retain_legacy_workspace(storage_root, campaign_id=..., session_id=...)`.
    fn retain_legacy_workspace(
        &self,
        storage_root: &Path,
        campaign_id: Option<&str>,
        session_id: &str,
    ) -> Result<(), Self::Error>;
    /// `owner.detach_legacy_builder_chat(session_id=..., expected_message_count=...,
    /// launch_context=..., display_name=...)`.
    fn detach_legacy_builder_chat(
        &self,
        session_id: &str,
        expected_message_count: usize,
        launch_context: &JsonMap,
        display_name: &str,
    ) -> Result<(), Self::Error>;
}

impl MigrationOwner for AuthorityStore {
    fn legacy_builder_contexts(&self) -> Result<Vec<SessionContext>, StoreError> {
        AuthorityStore::legacy_builder_contexts(self)
    }
    fn session_context(&self, session_id: &str) -> Result<SessionContext, StoreError> {
        AuthorityStore::session_context(self, session_id)
    }
    fn retain_legacy_workspace(
        &self,
        storage_root: &Path,
        campaign_id: Option<&str>,
        session_id: &str,
    ) -> Result<(), StoreError> {
        // Python's `"/" in value` on `None`.
        let campaign_id = campaign_id
            .ok_or_else(|| DomainError::type_error("argument of type 'NoneType' is not iterable"))?;
        sylvia_store::builder_launch_migration::retain_legacy_workspace(storage_root, campaign_id, session_id)
            .map(|_| ())
    }
    fn detach_legacy_builder_chat(
        &self,
        session_id: &str,
        expected_message_count: usize,
        launch_context: &JsonMap,
        display_name: &str,
    ) -> Result<(), StoreError> {
        AuthorityStore::detach_legacy_builder_chat(
            self,
            session_id,
            expected_message_count,
            launch_context,
            display_name,
        )
    }
}

fn command_rejected(error: &DomainError) -> bool {
    matches!(error.python_class(), "CommandRejected" | "MechanicsRejected")
}

/// The class name Python reports for a stored payload value: loaded content is frozen, so its
/// lists and mappings are `FrozenJsonList` and `FrozenJsonDict`.
fn frozen_type_name(value: &Json) -> &'static str {
    match value {
        Json::Array(_) => "FrozenJsonList",
        Json::Object(_) => "FrozenJsonDict",
        other => py_type_name(other),
    }
}

/// `value.get(key)` on stored content.
fn frozen_get<'a>(value: &'a Json, key: &str) -> Result<Option<&'a Json>, DomainError> {
    match value {
        Json::Object(map) => Ok(map.get(key)),
        other => Err(DomainError::uncaught(
            "AttributeError",
            format!("'{}' object has no attribute 'get'", frozen_type_name(other)),
        )),
    }
}

fn label_of(purpose: &str) -> Option<&'static str> {
    Some(match purpose {
        "ruleset" => "Ruleset",
        "setting" => "Setting",
        "plan" => "Campaign Plan",
        "player_character" => "Character",
        "companion" => "Companion",
        _ => return None,
    })
}

/// `migrate_legacy_builder_chats(owner, storage_root, catalog)`: run before owner requests
/// begin; the migrated chat ids, in order.
pub fn migrate_legacy_builder_chats<O: MigrationOwner>(
    owner: &O,
    bundled: &BundledPackages,
    storage_root: &Path,
    catalog: &[JsonMap],
) -> Result<Vec<String>, O::Error> {
    let mut migrated = Vec::new();
    for context in owner.legacy_builder_contexts()? {
        let session = &context.session;
        let link = context
            .messages
            .iter()
            .map(|message| message.content())
            .find(|content| {
                content.get("kind").is_some_and(|kind| py_eq(kind, &Json::from("creation_subflow_link")))
            })
            .ok_or_else(|| DomainError::uncaught("StopIteration", ""))?
            .clone();
        let link_json = Json::Object(link.clone());
        let parent_session_id = py_str(py_item(&link_json, "parent_session_id")?);
        let parent = owner.session_context(&parent_session_id)?;
        let mut transcript_ids: Vec<Json> = Vec::new();
        for id in py_iter(py_item(&link_json, "transcript_message_ids")?)? {
            if matches!(id, Json::Array(_) | Json::Object(_)) {
                let class = frozen_type_name(&id);
                return Err(DomainError::type_error(format!("unhashable type: '{class}'")).into());
            }
            if !transcript_ids.iter().any(|seen| py_eq(seen, &id)) {
                transcript_ids.push(id);
            }
        }
        let transcript: Vec<_> = parent
            .messages
            .iter()
            .filter(|message| transcript_ids.iter().any(|id| py_eq(id, &Json::from(message.message_id()))))
            .collect();
        if transcript.len() != transcript_ids.len() || transcript.is_empty() {
            return Err(
                DomainError::command_rejected("Legacy Builder launch transcript is unavailable").into()
            );
        }
        let cutoff = transcript.iter().map(|message| message.sequence()).max().expect("nonempty");
        let original_context = SessionContext {
            session: parent.session.clone(),
            messages: parent
                .messages
                .iter()
                .filter(|message| message.sequence() <= cutoff)
                .cloned()
                .collect(),
        };
        let state = original_context
            .messages
            .iter()
            .rev()
            .map(|message| message.content())
            .find(|content| {
                content.get("kind").is_some_and(|kind| py_eq(kind, &Json::from("campaign_interview_state")))
            })
            .cloned()
            .unwrap_or_default();
        let message_of =
            |id: &str| original_context.messages.iter().rev().find(|message| message.message_id() == id);
        let mut selections = JsonMap::new();
        let answers = state.get("answers").cloned().unwrap_or_else(|| Json::Object(JsonMap::new()));
        let Json::Object(answers) = answers else {
            let class = frozen_type_name(&answers);
            return Err(DomainError::uncaught(
                "AttributeError",
                format!("'{class}' object has no attribute 'items'"),
            )
            .into());
        };
        for (step, message_id) in &answers {
            let key = py_str(message_id);
            let message =
                message_of(&key).ok_or_else(|| DomainError::uncaught("KeyError", pystr::repr(&key)))?;
            let mut selection = JsonMap::new();
            selection.insert(
                "text".into(),
                message.content().get("text").cloned().unwrap_or_else(|| Json::from("")),
            );
            selection.insert(
                "value".into(),
                message
                    .content()
                    .get("interview_value")
                    .cloned()
                    .unwrap_or_else(|| Json::Object(JsonMap::new())),
            );
            selections.insert(step.clone(), Json::Object(selection));
        }
        let mut reference_error = Json::Null;
        let captured = (|| -> Result<JsonMap, O::Error> {
            let empty = Json::Object(JsonMap::new());
            // `selections.get("ruleset", {}).get("value", {}).get("candidate_id")`: the selection
            // is a fresh mapping, its value the stored answer.
            let value = selections.get("ruleset").and_then(|ruleset| ruleset.get("value")).unwrap_or(&empty);
            let ruleset_id = frozen_get(value, "candidate_id")?.cloned().unwrap_or(Json::Null);
            if !ruleset_id.is_null() {
                // Earlier bundled interviews sometimes saved only a logical Ruleset name. That
                // cannot prove which historical version this chat originally used.
                owner.package_candidate(&py_str(&ruleset_id))?;
            }
            capture_launch_references(owner, bundled, storage_root, &original_context, catalog)
        })();
        let references = match captured {
            Ok(references) => Json::Object(references),
            Err(error) if command_rejected(&error.as_domain()) => {
                reference_error = Json::from(
                    "This older chat has no recoverable exact source versions. Its history and files remain \
                     available; a new Builder with selected sources can validate them for use.",
                );
                serde_json::json!({"candidate_id": null, "packages": [], "plan": null})
            }
            Err(error) => return Err(error),
        };
        let trigger_id = py_str(link.get("trigger_message_id").unwrap_or(&Json::Null));
        let button = match message_of(&trigger_id) {
            Some(trigger) => {
                trigger.content().get("text").cloned().unwrap_or_else(|| Json::from("New content"))
            }
            None => Json::from("New content"),
        };
        let purpose = py_str(py_item(&link_json, "purpose")?);
        let fields = session.fields();
        let transcript_rows: Vec<Json> = transcript
            .iter()
            .map(|message| {
                let role = if message.kind() == SessionMessageKind::User { "user" } else { "assistant" };
                serde_json::json!({
                    "source_message_id": message.message_id(),
                    "role": role,
                    "content": message.content().get("text").cloned().unwrap_or_else(|| Json::from("")),
                })
            })
            .collect();
        let launch = serde_json::json!({
            "kind": "builder_launch_context",
            "purpose": purpose,
            "launch_request": {
                "operation_id": format!("migrate-{}", session.session_id()),
                "campaign_id": session.campaign_id(),
                "parent_session_id": parent_session_id,
                "session_id": session.session_id(),
                "purpose": purpose,
                "button_label": button,
                "construction_revision": state.get("construction_revision").cloned().unwrap_or_else(|| Json::from(0)),
            },
            "selections": selections,
            "references": references,
            "reference_error": reference_error,
            "interview_transcript": transcript_rows,
            "migrated_from": {
                "campaign_id": session.campaign_id(),
                "parent_session_id": parent_session_id,
                "selected_skill": fields.selected_skill,
                "scope": fields.scope,
            },
        });
        let context_name = ["plan", "setting", "ruleset"]
            .iter()
            .filter_map(|step| selections.get(*step))
            .map(|selection| &selection["text"])
            .find(|text| sylvia_domain::pyops::py_truthy(text))
            .map(py_str)
            .unwrap_or_else(|| "Campaign content".to_owned());
        let label =
            label_of(&purpose).ok_or_else(|| DomainError::uncaught("KeyError", pystr::repr(&purpose)))?;
        owner.retain_legacy_workspace(storage_root, session.campaign_id(), session.session_id())?;
        let display: String = format!("{label} · {context_name}").chars().take(120).collect();
        let Json::Object(launch) = launch else { unreachable!("an object") };
        owner.detach_legacy_builder_chat(
            session.session_id(),
            context.messages.len(),
            &launch,
            pystr::strip(&display),
        )?;
        migrated.push(session.session_id().to_owned());
    }
    Ok(migrated)
}
