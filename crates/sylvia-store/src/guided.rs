//! The guided campaign interview: Python's `create_guided_campaign_draft`, in the store
//! (`SQLiteAuthorityStore`) and in the application owner (`AuthorityOwner`).
//!
//! Later commands of the interview (`advance_guided_campaign_draft`, `start_guided_campaign`)
//! join this module as they are ported.

use crate::campaigns::{named_rows, parse, Named};
use crate::error::{StoreError, StoreResult};
use crate::sessions::{append_message, insert_session, read_messages, read_session};
use crate::store::AuthorityStore;
use rusqlite::Connection;
use sylvia_base::hash::sha256_hex;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{
    validate_authority_identity, CampaignRecord, GuidedCampaignDraft, LifecycleState, WriterRole,
};
use sylvia_domain::pyops::py_eq;
use sylvia_domain::session::{SessionMessageKind, SessionRecord, SessionRecordFields, SessionStatus};

/// `_UNBOUND_CAMPAIGN_LOCK_HASH`.
const UNBOUND_CAMPAIGN_LOCK_HASH: &str = "unbound";

fn object<const N: usize>(pairs: [(&str, Json); N]) -> JsonMap {
    pairs.into_iter().map(|(key, value)| (key.to_owned(), value)).collect()
}

/// The first 24 hex digits of the SHA-256 of `text`: Python's `hexdigest()[:24]`.
pub(crate) fn digest24(text: &str) -> String {
    sha256_hex(text.as_bytes())[..24].to_owned()
}

/// `SQLiteAuthorityStore._campaign_record`.
pub(crate) fn campaign_record(row: &Named<'_>) -> StoreResult<CampaignRecord> {
    Ok(CampaignRecord {
        campaign_id: row.text("campaign_id")?,
        display_name: row.text("display_name")?,
        lifecycle: parse(&row.text("lifecycle")?, LifecycleState::parse, "LifecycleState")?,
        writer: parse(&row.text("writer")?, WriterRole::parse, "WriterRole")?,
        revision: row.int("revision")?,
    })
}

impl AuthorityStore {
    /// `SQLiteAuthorityStore.create_guided_campaign_draft`: one unbound Draft and its
    /// deterministic first interview question. A replay with the same identity returns the same
    /// Draft; a replay that differs is an `IdempotencyConflict`.
    pub fn create_guided_campaign_draft(
        &self,
        operation_id: &str,
        campaign: &CampaignRecord,
        builder_session: &SessionRecord,
    ) -> StoreResult<GuidedCampaignDraft> {
        let suffix = digest24(operation_id);
        let intro_message_id = format!("guided-intro-{suffix}");
        let state_message_id = format!("guided-state-{suffix}");
        let state_payload = object([
            ("kind", "campaign_interview_state".into()),
            ("version", 1.into()),
            ("creation_operation_id", operation_id.into()),
            ("phase", "draft".into()),
            ("current_step", "ruleset".into()),
            ("construction_revision", 0.into()),
            ("answers", Json::Object(JsonMap::new())),
            ("active_subflow", Json::Null),
            ("current_question_message_id", intro_message_id.as_str().into()),
        ]);
        let intro_payload = object([
            (
                "text",
                "Choose a Ruleset for this campaign, or choose No Ruleset for narrative-only play.".into(),
            ),
            ("interview_step", "ruleset".into()),
        ]);
        let draft = GuidedCampaignDraft {
            campaign_id: campaign.campaign_id.clone(),
            builder_session_id: builder_session.session_id().to_owned(),
            current_step: "ruleset".into(),
            construction_revision: 0,
            resulting_revision: 0,
            answer_message_id: None,
        };
        self.in_immediate(|connection| {
            self.ensure_campaign_schema(connection)?;
            let (columns, rows) =
                named_rows(connection, "SELECT * FROM campaigns WHERE campaign_id = ?", &[&campaign.campaign_id])?;
            if let Some(values) = rows.into_iter().next() {
                let existing = campaign_record(&Named { columns: &columns, values })?;
                let session = read_session(connection, builder_session.session_id())?;
                let messages = read_messages(connection, builder_session.session_id())?;
                let find = |id: &str| messages.iter().find(|message| message.message_id() == id);
                let matches = |id: &str, kind: SessionMessageKind, payload: &JsonMap| {
                    find(id).is_some_and(|message| {
                        message.kind() == kind
                            && py_eq(&Json::Object(message.content().clone()), &Json::Object(payload.clone()))
                    })
                };
                if &existing != campaign
                    || &session != builder_session
                    || !matches(&intro_message_id, SessionMessageKind::Talk, &intro_payload)
                    || !matches(&state_message_id, SessionMessageKind::Evidence, &state_payload)
                {
                    return Err(StoreError::Coded(crate::error::authority_coded(
                        "IdempotencyConflict",
                        "Guided Campaign Draft identity payload does not match",
                    )));
                }
                // Python rolls back here; nothing was written, so committing is the same.
                return Ok(draft.clone());
            }
            connection.execute(
                "INSERT INTO campaigns \
                 (campaign_id, display_name, lifecycle, writer, revision, lock_hash, current_lock_id) \
                 VALUES (?, ?, 'active', 'builder', 0, ?, NULL)",
                rusqlite::params![campaign.campaign_id, campaign.display_name, UNBOUND_CAMPAIGN_LOCK_HASH],
            )?;
            connection.execute(
                "INSERT INTO campaign_lifecycle_state (campaign_id, retained_active_writer) VALUES (?, 'builder')",
                [&campaign.campaign_id],
            )?;
            insert_session(connection, builder_session)?;
            append_message(
                connection,
                builder_session.session_id(),
                &intro_message_id,
                SessionMessageKind::Talk,
                &intro_payload,
                None,
            )?;
            append_message(
                connection,
                builder_session.session_id(),
                &state_message_id,
                SessionMessageKind::Evidence,
                &state_payload,
                None,
            )?;
            connection.execute(
                "INSERT INTO campaign_revisions \
                 (campaign_id, revision, action_id, activation_id, plan_id, handoff_id, \
                 construction_operation_id) VALUES (?, 0, NULL, NULL, NULL, NULL, NULL)",
                [&campaign.campaign_id],
            )?;
            verify_written(connection)?;
            Ok(draft.clone())
        })
    }
}

/// `self._verify_stored_authority(connection)` and `sqlite_packages.verify_package_authority`,
/// run inside the write transaction before it commits, as Python does.
fn verify_written(connection: &Connection) -> StoreResult<()> {
    crate::stored::verify_stored_authority(connection)?;
    crate::packages::verify_package_authority(connection)
}

/// `CommandRejected("Guided Campaign Draft authority is invalid", code=...)`.
fn invalid_draft_identity() -> StoreError {
    crate::actions::rejected("Guided Campaign Draft authority is invalid", "invalid_authority_identity")
}

impl crate::handle::StoreHandle {
    /// `AuthorityOwner.create_guided_campaign_draft`: the identity checks, the Draft's
    /// campaign identity from the operation, its Builder session and campaign records, then
    /// the store's create. `_operation()` only refuses work once the owner is closing (see
    /// [`Self::rename_builder_chat`](crate::handle::StoreHandle::rename_builder_chat)).
    pub async fn create_guided_campaign_draft(
        &self,
        operation_id: &str,
        display_name: &str,
        builder_session_id: &str,
        builder_provider_id: &str,
        builder_model_id: &str,
    ) -> StoreResult<GuidedCampaignDraft> {
        for (value, label) in [
            (operation_id, "Campaign operation identity"),
            (display_name, "Campaign display name"),
            (builder_session_id, "Builder session identity"),
            (builder_provider_id, "Builder provider identity"),
            (builder_model_id, "Builder model identity"),
        ] {
            if validate_authority_identity(value, label, false).is_err() {
                return Err(invalid_draft_identity());
            }
        }
        let campaign_id = format!("campaign-draft-{}", digest24(operation_id));
        let session = SessionRecord::new(SessionRecordFields {
            session_id: builder_session_id.to_owned(),
            campaign_id: Some(campaign_id.clone()),
            role: WriterRole::Builder,
            provider_id: builder_provider_id.to_owned(),
            model_id: builder_model_id.to_owned(),
            status: SessionStatus::Active,
            selected_skill: Some("BUILD CAMPAIGN PLAN".into()),
            ..SessionRecordFields::default()
        })?;
        let campaign = CampaignRecord {
            campaign_id,
            display_name: display_name.to_owned(),
            lifecycle: LifecycleState::Active,
            writer: WriterRole::Builder,
            revision: 0,
        };
        let operation_id = operation_id.to_owned();
        self.call(move |store| store.create_guided_campaign_draft(&operation_id, &campaign, &session)).await
    }
}
