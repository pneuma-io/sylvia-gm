//! `advance_guided_campaign_draft`: one Campaign Interview answer, the next question and the new
//! resume state, in the store (`SQLiteAuthorityStore`) and the application owner
//! (`AuthorityOwner`, with `_next_guided_question` and `_guided_answer_value`).
//!
//! Not yet ported, so refused rather than run differently: answering a step that needs an
//! Interview Contribution (`_guided_contribution_object`, which reads package sidecars). That is
//! the `plan` step and the three `*_contribution` steps. A No Ruleset interview, which the model
//! sessions take, never reaches them.

use crate::campaigns::{json_str, named_rows, Named};
use crate::error::{schema_identity, StoreError, StoreResult};
use crate::guided::digest24;
use crate::handle::StoreHandle;
use crate::sessions::{append_message, message_from_row, read_session};
use crate::store::AuthorityStore;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{owned_payload, validate_authority_identity, GuidedCampaignDraft, WriterRole};
use sylvia_domain::pyops::py_eq;
use sylvia_domain::session::{SessionMessage, SessionMessageKind, SessionStatus};

/// `_GUIDED_CAMPAIGN_STEPS`.
pub const GUIDED_CAMPAIGN_STEPS: [&str; 11] = [
    "ruleset",
    "setting",
    "plan",
    "ruleset_contribution",
    "setting_contribution",
    "plan_contribution",
    "name",
    "participants",
    "boundaries_preferences",
    "specific_requests",
    "review",
];

/// `_GUIDED_CONTRIBUTION_STEPS` (its keys).
const GUIDED_CONTRIBUTION_STEPS: [&str; 3] =
    ["ruleset_contribution", "setting_contribution", "plan_contribution"];

/// `_MAX_GUIDED_VISIBLE_ANSWER_CHARS`.
const MAX_GUIDED_VISIBLE_ANSWER_CHARS: usize = 131_072;

/// `_GUIDED_CAMPAIGN_QUESTIONS`.
fn guided_question(step: &str) -> &'static str {
    match step {
        "ruleset" => "Choose a Ruleset for this campaign, or choose No Ruleset for narrative-only play.",
        "ruleset_contribution" => "Answer the selected Ruleset's campaign setup questions.",
        "setting" => "Choose a compatible Campaign Setting.",
        "setting_contribution" => "Answer the selected Campaign Setting's setup questions.",
        "plan" => "Choose a compatible Campaign Plan.",
        "plan_contribution" => "Answer the selected Campaign Plan's setup questions.",
        "name" => "Review or edit the suggested campaign name.",
        "participants" => "Choose or create the participants required by this campaign.",
        "boundaries_preferences" => "State any hard Boundaries and softer Preferences.",
        "specific_requests" => "Add any Specific Requests, or choose No additional requests.",
        "review" => "Review the spoiler-free campaign summary before choosing Ready to Begin.",
        other => unreachable!("no question for step {other}"),
    }
}

fn step_index(step: &str) -> usize {
    GUIDED_CAMPAIGN_STEPS.iter().position(|item| *item == step).expect("a known step")
}

fn needs_contribution(step: &str) -> bool {
    step == "plan" || GUIDED_CONTRIBUTION_STEPS.contains(&step)
}

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

/// The application's arguments: `AuthorityOwner.advance_guided_campaign_draft`'s keywords.
pub struct AdvanceAnswer<'a> {
    pub operation_id: &'a str,
    pub campaign_id: &'a str,
    pub builder_session_id: &'a str,
    pub expected_construction_revision: i64,
    pub current_step: &'a str,
    pub visible_answer: &'a str,
    pub answer: &'a Json,
}

impl StoreHandle {
    /// `AuthorityOwner._guided_answer_value(session_id, step)`: one accepted Interview answer from
    /// its canonical User message, as the latest Interview State names it.
    async fn guided_answer_value(&self, session_id: &str, step: &str) -> StoreResult<Option<JsonMap>> {
        let session = session_id.to_owned();
        let context = self.call(move |store| store.session_context(&session)).await?;
        let Some(state) = context.messages.iter().rev().find(|message| {
            message.kind() == SessionMessageKind::Evidence
                && message.content().get("kind") == Some(&Json::String("campaign_interview_state".into()))
        }) else {
            return Ok(None);
        };
        let Some(Json::Object(references)) = state.content().get("answers") else { return Ok(None) };
        let Some(Json::String(message_id)) = references.get(step) else { return Ok(None) };
        let message = context
            .messages
            .iter()
            .find(|item| item.message_id() == message_id && item.kind() == SessionMessageKind::User);
        Ok(match message.and_then(|message| message.content().get("interview_value")) {
            Some(Json::Object(value)) => Some(value.clone()),
            _ => None,
        })
    }

    /// `AuthorityOwner._next_guided_question`: No Ruleset skips the content steps; other steps
    /// follow in order. Steps that need an Interview Contribution are not ported yet.
    async fn next_guided_question(
        &self,
        session_id: &str,
        current_step: &str,
        answer: &JsonMap,
    ) -> StoreResult<(&'static str, &'static str)> {
        let ruleset_answer = if current_step == "ruleset" {
            Some(answer.clone())
        } else {
            self.guided_answer_value(session_id, "ruleset").await?
        };
        if let Some(ruleset) = &ruleset_answer {
            if matches!(ruleset.get("candidate_id"), None | Some(Json::Null)) {
                if current_step == "ruleset" {
                    return Ok(("name", guided_question("name")));
                }
                if current_step == "name" {
                    return Ok(("boundaries_preferences", guided_question("boundaries_preferences")));
                }
            }
        }
        if needs_contribution(current_step) {
            return Err(StoreError::NotPorted(
                "Campaign Interview Contribution steps (_guided_contribution_object)",
            ));
        }
        let step = GUIDED_CAMPAIGN_STEPS[step_index(current_step) + 1];
        Ok((step, guided_question(step)))
    }

    /// `AuthorityOwner.advance_guided_campaign_draft`: `_operation()` and the campaign's work,
    /// the answer checks, the next question, then the store's advance.
    pub async fn advance_guided_campaign_draft(
        &self,
        advance: &AdvanceAnswer<'_>,
    ) -> StoreResult<GuidedCampaignDraft> {
        let campaign_id = advance.campaign_id.to_owned();
        let _work = self
            .lanes()
            .work
            .begin(&campaign_id, || {
                let campaign_id = campaign_id.clone();
                async move { self.call(move |store| store.campaign_lifecycle(&campaign_id)).await }
            })
            .await?;
        let invalid = || rejected("Guided Campaign answer is invalid", "invalid_campaign_interview_answer");
        for (value, label) in [
            (advance.operation_id, "Campaign operation identity"),
            (advance.campaign_id, "Campaign identity"),
            (advance.builder_session_id, "Builder session identity"),
        ] {
            if validate_authority_identity(value, label, false).is_err() {
                return Err(invalid());
            }
        }
        let answerable = &GUIDED_CAMPAIGN_STEPS[..GUIDED_CAMPAIGN_STEPS.len() - 1];
        if !answerable.contains(&advance.current_step)
            || advance.expected_construction_revision < 0
            || sylvia_base::pystr::strip(advance.visible_answer).is_empty()
            || advance.visible_answer.chars().count() > MAX_GUIDED_VISIBLE_ANSWER_CHARS
        {
            return Err(invalid());
        }
        let captured = owned_payload(advance.answer).map_err(|_| invalid())?;
        if GUIDED_CONTRIBUTION_STEPS.contains(&advance.current_step) {
            return Err(StoreError::NotPorted(
                "Campaign Interview Contribution steps (_guided_contribution_object)",
            ));
        }
        let index = step_index(advance.current_step);
        let (next_step, next_question) =
            self.next_guided_question(advance.builder_session_id, advance.current_step, &captured).await?;
        let preceding: Vec<&'static str> = GUIDED_CAMPAIGN_STEPS[..index].to_vec();
        let (operation_id, campaign_id, session_id, current_step, visible_answer) = (
            advance.operation_id.to_owned(),
            advance.campaign_id.to_owned(),
            advance.builder_session_id.to_owned(),
            advance.current_step.to_owned(),
            advance.visible_answer.to_owned(),
        );
        let expected = advance.expected_construction_revision;
        self.call(move |store| {
            store.advance_guided_campaign_draft(&AdvanceDraft {
                operation_id: &operation_id,
                campaign_id: &campaign_id,
                builder_session_id: &session_id,
                expected_construction_revision: expected,
                current_step: &current_step,
                visible_answer: &visible_answer,
                answer: &captured,
                preceding_steps: &preceding,
                next_step,
                next_question,
            })
        })
        .await
    }
}
