//! `advance_guided_campaign_draft`: one Campaign Interview answer, the next question and the new
//! resume state, in the store (`SQLiteAuthorityStore`). The application half (`AuthorityOwner`,
//! with `_next_guided_question` and `_guided_answer_value`, and the steps it does not run yet) is
//! `sylvia_app::authority::guided`.

use crate::campaigns::{json_str, named_rows, Named};
use crate::error::{schema_identity, StoreError, StoreResult};
use crate::guided::digest24;
use crate::sessions::{append_message, message_from_row, read_session};
use crate::store::AuthorityStore;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{owned_payload, GuidedCampaignDraft, WriterRole};
use sylvia_domain::pyops::py_eq;
use sylvia_domain::session::{SessionMessage, SessionMessageKind, SessionStatus};

fn coded(kind: &'static str, message: &str) -> StoreError {
    StoreError::Coded(crate::error::authority_coded(kind, message))
}

fn rejected(message: &str, code: &str) -> StoreError {
    crate::actions::rejected(message, code)
}

/// `{**value, key: item, ...}` with Python's dict semantics: existing keys keep their place.
fn merged(mut value: JsonMap, changes: Vec<(&str, Json)>) -> JsonMap {
    for (key, item) in changes {
        value.insert(key.to_owned(), item);
    }
    value
}

/// The store's arguments beyond the application's own (`preceding_steps`, `next_step`,
/// `next_question`).
pub struct AdvanceDraft<'a> {
    pub operation_id: &'a str,
    pub campaign_id: &'a str,
    pub builder_session_id: &'a str,
    pub expected_construction_revision: i64,
    pub current_step: &'a str,
    pub visible_answer: &'a str,
    pub answer: &'a JsonMap,
    pub preceding_steps: &'a [&'a str],
    pub next_step: &'a str,
    pub next_question: &'a str,
}

impl AuthorityStore {
    /// `SQLiteAuthorityStore.advance_guided_campaign_draft`: append one exact answer, the next
    /// Builder question and the new resume state.
    pub fn advance_guided_campaign_draft(
        &self,
        advance: &AdvanceDraft<'_>,
    ) -> StoreResult<GuidedCampaignDraft> {
        let suffix = digest24(advance.operation_id);
        let answer_message_id = format!("guided-answer-{suffix}");
        let question_message_id = format!("guided-question-{suffix}");
        let state_message_id = format!("guided-state-{suffix}");
        let draft = |revision: i64| GuidedCampaignDraft {
            campaign_id: advance.campaign_id.to_owned(),
            builder_session_id: advance.builder_session_id.to_owned(),
            current_step: advance.next_step.to_owned(),
            construction_revision: revision,
            resulting_revision: 0,
            answer_message_id: Some(answer_message_id.clone()),
        };
        self.in_immediate(|connection| {
            crate::verify::verify_exact_schema_identity(
                connection,
                Some(crate::schema::CAMPAIGN_SCHEMA_VERSION),
            )?;
            let session = read_session(connection, advance.builder_session_id)?;
            if session.campaign_id() != Some(advance.campaign_id)
                || session.role() != WriterRole::Builder
                || session.fields().status != SessionStatus::Active
            {
                return Err(rejected("Guided Campaign Builder session is unavailable", "command_rejected"));
            }
            let (columns, rows) = named_rows(
                connection,
                "SELECT * FROM agent_session_messages WHERE session_id = ? AND message_id = ?",
                &[&advance.builder_session_id, &state_message_id],
            )?;
            if let Some(values) = rows.into_iter().next() {
                let replay = message_from_row(&Named { columns: &columns, values })?;
                let content = replay.content();
                // `isinstance(revision, int)`: a JSON integer (Python would also take a bool).
                let revision = content.get("construction_revision").and_then(Json::as_i64);
                let conflict = replay.kind() != SessionMessageKind::Evidence
                    || content.get("kind") != Some(&Json::String("campaign_interview_state".into()))
                    || content.get("last_operation_id") != Some(&Json::String(advance.operation_id.into()))
                    || revision.is_none()
                    || content.get("current_step") != Some(&Json::String(advance.next_step.into()));
                if conflict {
                    return Err(coded(
                        "IdempotencyConflict",
                        "Guided Campaign answer identity payload does not match",
                    ));
                }
                // Python rolls back here; nothing was written, so committing is the same.
                return Ok(draft(revision.unwrap_or_default()));
            }
            let (columns, rows) = named_rows(
                connection,
                "SELECT * FROM agent_session_messages WHERE session_id = ? AND kind = 'evidence' \
                 ORDER BY sequence DESC",
                &[&advance.builder_session_id],
            )?;
            let mut current_state: Option<SessionMessage> = None;
            for values in rows {
                let candidate = message_from_row(&Named { columns: &columns, values })?;
                if candidate.content().get("kind") == Some(&Json::String("campaign_interview_state".into())) {
                    current_state = Some(candidate);
                    break;
                }
            }
            let Some(current_state) = current_state else {
                return Err(rejected("Guided Campaign Interview State is unavailable", "command_rejected"));
            };
            let state = current_state.content().clone();
            let Some(Json::Object(answers_value)) = state.get("answers") else {
                return Err(schema_identity("Guided Campaign Interview State is invalid"));
            };
            let stale = state.get("phase") != Some(&Json::String("draft".into()))
                || !state
                    .get("construction_revision")
                    .is_some_and(|value| py_eq(value, &Json::from(advance.expected_construction_revision)))
                || !matches!(state.get("active_subflow"), None | Some(Json::Null))
                || (state.get("current_step") != Some(&Json::String(advance.current_step.into()))
                    && !answers_value.contains_key(advance.current_step));
            if stale {
                return Err(coded("StaleRevision", "Guided Campaign Interview revision is stale"));
            }
            let mut answers = JsonMap::new();
            for (key, value) in answers_value {
                if advance.preceding_steps.contains(&key.as_str()) {
                    answers.insert(key.clone(), json_str(value).into());
                }
            }
            // `list(value) if isinstance(value, Sequence) else []`: a string is a sequence of its
            // characters.
            let retained: Vec<Json> = match state.get("subflows") {
                Some(Json::Array(items)) => items.clone(),
                Some(Json::String(text)) => text.chars().map(|c| Json::String(c.to_string())).collect(),
                _ => Vec::new(),
            };
            let user_content = object(vec![
                ("text", advance.visible_answer.into()),
                ("interview_step", advance.current_step.into()),
                ("interview_value", Json::Object(advance.answer.clone())),
            ]);
            let question_content = object(vec![
                ("text", advance.next_question.into()),
                ("interview_step", advance.next_step.into()),
            ]);
            let next_revision = advance
                .expected_construction_revision
                .checked_add(1)
                .ok_or_else(|| StoreError::Other("construction revision overflow".into()))?;
            let mut detached = Vec::with_capacity(retained.len());
            for item in retained {
                let Json::Object(item) = item else {
                    detached.push(item);
                    continue;
                };
                let resume_step = item.get("resume_step");
                let keeps = match resume_step {
                    None | Some(Json::Null) => true,
                    Some(Json::String(step)) => advance.preceding_steps.contains(&step.as_str()),
                    Some(_) => false,
                };
                if keeps {
                    detached.push(Json::Object(owned_payload(&Json::Object(item))?));
                } else {
                    detached.push(Json::Object(merged(
                        item,
                        vec![("status", "detached".into()), ("detached_revision", next_revision.into())],
                    )));
                }
            }
            answers.insert(advance.current_step.to_owned(), answer_message_id.as_str().into());
            let next_state = merged(
                state,
                vec![
                    ("current_step", advance.next_step.into()),
                    ("construction_revision", next_revision.into()),
                    ("answers", Json::Object(answers)),
                    ("navigation_frontier", advance.next_step.into()),
                    ("subflows", Json::Array(detached)),
                    ("last_operation_id", advance.operation_id.into()),
                    ("current_question_message_id", question_message_id.as_str().into()),
                ],
            );
            let session_id = advance.builder_session_id;
            append_message(
                connection,
                session_id,
                &answer_message_id,
                SessionMessageKind::User,
                &user_content,
                None,
            )?;
            append_message(
                connection,
                session_id,
                &question_message_id,
                SessionMessageKind::Talk,
                &question_content,
                None,
            )?;
            append_message(
                connection,
                session_id,
                &state_message_id,
                SessionMessageKind::Evidence,
                &next_state,
                None,
            )?;
            crate::stored::verify_stored_authority(connection)?;
            Ok(draft(next_revision))
        })
    }
}

fn object(pairs: Vec<(&str, Json)>) -> JsonMap {
    pairs.into_iter().map(|(key, value)| (key.to_owned(), value)).collect()
}
