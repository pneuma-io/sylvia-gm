//! `application/authority.py` operations on top of the store, one by one.
//!
//! So far: `AuthorityOwner.campaign_index` with `_campaign_index_entry`, and the
//! `CampaignIndexEntry` and `CampaignSessionIndexEntry` it returns (`sylvia_domain::authority`,
//! where lane 4's opening reads take them; re-exported here). The owner's reply field order
//! (`_json_value`) is lane 5's handler.
//!
//! `rename_builder_chat` and `delete_builder_chat` are [`StoreHandle`](crate::StoreHandle)
//! operations, because they hold the owner's lanes ([`crate::lanes`]) around the store call.

use crate::campaigns::CampaignIndexRecord;
use crate::error::StoreResult;
use crate::store::AuthorityStore;
use sylvia_domain::authority::{LifecycleState, PackageRole, TurnStatus, WriterRole};
use sylvia_domain::package_library::campaign_setting_entries;
use sylvia_domain::session::SessionStatus;

pub use sylvia_domain::authority::{CampaignIndexEntry, CampaignSessionIndexEntry};

impl AuthorityStore {
    /// `AuthorityOwner.campaign_index`: the store's index projected through
    /// `_campaign_index_entry`. `_operation()` only refuses work once the owner is closed, which
    /// the store's own closed check gives here.
    pub fn campaign_index_entries(&self) -> StoreResult<Vec<CampaignIndexEntry>> {
        Ok(self.campaign_index()?.iter().map(campaign_index_entry).collect())
    }
}

/// `AuthorityOwner._campaign_index_entry`.
pub fn campaign_index_entry(record: &CampaignIndexRecord) -> CampaignIndexEntry {
    let campaign = &record.campaign;
    let package_count = record.package_lock.as_ref().map_or(0, |lock| lock.entries().len());
    let label_of = |package_id: &str| {
        // `dict(record.package_labels)`: the last label given for an identity wins.
        record
            .package_labels
            .iter()
            .rev()
            .find(|(id, _)| id == package_id)
            .map_or_else(|| package_id.to_owned(), |(_, label)| label.clone())
    };
    let mut ruleset_id = None;
    let mut ruleset_label = "No rules".to_owned();
    let mut campaign_setting_id = None;
    let mut campaign_setting_label = "No campaign setting".to_owned();
    let mut turn: Option<(String, TurnStatus)> = None;
    let mut session_fields: Option<(String, SessionStatus, String, String)> = None;
    let open_turns: Vec<&(String, TurnStatus)> =
        record.turns.iter().filter(|(_, status)| *status == TurnStatus::Open).collect();
    let mut supported = campaign.lifecycle == LifecycleState::Active
        && campaign.writer == WriterRole::Dm
        && record.package_lock.is_some()
        && open_turns.len() == 1;
    if open_turns.len() == 1 {
        turn = Some(open_turns[0].clone());
    } else if let Some(last) = record.turns.last() {
        turn = Some(last.clone());
    }
    let writer_sessions: Vec<_> =
        record.top_level_sessions.iter().filter(|session| session.role() == campaign.writer).collect();
    if writer_sessions.len() == 1 {
        let session = writer_sessions[0].fields();
        session_fields = Some((
            session.session_id.clone(),
            session.status,
            session.provider_id.clone(),
            session.model_id.clone(),
        ));
        supported = supported
            && session.campaign_id.as_deref() == Some(campaign.campaign_id.as_str())
            && session.role == WriterRole::Dm
            && !session.provider_id.is_empty()
            && !session.model_id.is_empty()
            && session.status == SessionStatus::Active
            && session.parent_session_id.is_none()
            && session.selected_skill.is_none();
    }
    let (ruleset_summary, package_summary) = match &record.package_lock {
        None => {
            ruleset_label = "Not selected".to_owned();
            campaign_setting_label = "Not selected".to_owned();
            ("Not selected".to_owned(), "Draft Campaign".to_owned())
        }
        Some(_) if package_count == 0 => ("No rules".to_owned(), "No packages".to_owned()),
        Some(lock) => {
            if let Some(primary) = lock.entries().iter().find(|entry| entry.role() == PackageRole::Primary) {
                ruleset_id = Some(primary.package_id().to_owned());
                ruleset_label = label_of(primary.package_id());
                if let Some(setting) = campaign_setting_entries(lock).first() {
                    campaign_setting_id = Some(setting.package_id().to_owned());
                    campaign_setting_label = label_of(setting.package_id());
                }
            }
            ("Package-defined rules".to_owned(), format!("{package_count} packages"))
        }
    };
    let sessions = record
        .top_level_sessions
        .iter()
        .map(|session| {
            let fields = session.fields();
            CampaignSessionIndexEntry {
                session_id: fields.session_id.clone(),
                role: fields.role.as_str().to_owned(),
                status: fields.status.as_str().to_owned(),
                provider_id: fields.provider_id.clone(),
                model_id: fields.model_id.clone(),
                selected_skill: fields.selected_skill.clone(),
                model_guidance: record
                    .handoffs
                    .iter()
                    .find(|handoff| handoff.fields().dm_session_id == fields.session_id)
                    .map(|handoff| handoff.fields().model_guidance.clone()),
            }
        })
        .collect();
    let availability = if supported {
        "available"
    } else if campaign.lifecycle == LifecycleState::Active && campaign.writer == WriterRole::Builder {
        "draft"
    } else {
        "unavailable"
    };
    let (session_id, session_status, provider_id, model_id) = match session_fields {
        Some((id, status, provider, model)) => {
            (Some(id), Some(status.as_str().to_owned()), Some(provider), Some(model))
        }
        None => (None, None, None, None),
    };
    CampaignIndexEntry {
        campaign_id: campaign.campaign_id.clone(),
        display_name: match record.interview_name.as_deref() {
            Some(name) if !name.is_empty() => format!("{name} \u{b7} Interview"),
            _ => campaign.display_name.clone(),
        },
        lifecycle: campaign.lifecycle.as_str().to_owned(),
        writer: campaign.writer.as_str().to_owned(),
        revision: campaign.revision,
        ruleset_summary,
        package_summary,
        package_count: i64::try_from(package_count).unwrap_or(i64::MAX),
        playable: supported,
        availability: availability.to_owned(),
        ruleset_id,
        ruleset_label,
        campaign_setting_id,
        campaign_setting_label,
        session_activity_ordinal: record.session_activity_ordinal,
        turn_id: turn.as_ref().map(|(id, _)| id.clone()),
        turn_status: turn.as_ref().map(|(_, status)| status.as_str().to_owned()),
        session_id,
        session_status,
        provider_id,
        model_id,
        sessions,
    }
}

/// `AuthorityOwner.delete_builder_chat`'s identity refusal.
fn invalid_builder_chat_identity() -> crate::error::StoreError {
    crate::actions::rejected("Builder chat identity is invalid", "invalid_authority_identity")
}

impl crate::handle::StoreHandle {
    /// `AuthorityOwner.rename_builder_chat`: `_operation()`, then `SessionApplication`'s
    /// session lane (waiting for it), then the store's rename.
    ///
    /// `_operation()` refuses work once the owner is closing. Here the store thread outlives every
    /// handle, so closing waits for each operation as Python's does, and a stopped store thread
    /// fails like Python's `RuntimeError`, as an internal fault.
    pub async fn rename_builder_chat(
        &self,
        session_id: &str,
        display_name: &str,
        automatic: bool,
    ) -> StoreResult<sylvia_domain::session::BuilderChatRecord> {
        let _session = self.lanes().sessions.acquire(session_id).await;
        let (session_id, display_name) = (session_id.to_owned(), display_name.to_owned());
        self.call(move |store| store.rename_builder_chat(&session_id, &display_name, automatic)).await
    }

    /// `AuthorityOwner.delete_builder_chat`: the identity and exact-confirmation checks, the
    /// non-blocking `builder-chat:<id>` owner lane (refused, never queued, while Builder work
    /// holds it), `_operation()`, `SessionApplication`'s session lane (waiting for it), then the
    /// store's delete.
    pub async fn delete_builder_chat(
        &self,
        session_id: &str,
        confirmation: &str,
    ) -> StoreResult<sylvia_domain::session::BuilderChatRecord> {
        if sylvia_domain::authority::validate_authority_identity(session_id, "Builder chat identity", false)
            .is_err()
        {
            return Err(invalid_builder_chat_identity());
        }
        if session_id == "." || session_id == ".." || session_id.contains(['/', '\\', ':']) {
            return Err(invalid_builder_chat_identity());
        }
        if confirmation != format!("permanently delete {session_id}") {
            return Err(crate::actions::rejected(
                "Builder chat deletion confirmation is not exact",
                "builder_chat_delete_confirmation_inexact",
            ));
        }
        let Some(_owner) = self.lanes().owner.try_acquire(&crate::lanes::builder_chat_lane(session_id))
        else {
            return Err(crate::actions::rejected(
                "Builder chat has active work; deletion is not queued",
                "builder_chat_work_in_progress",
            ));
        };
        let _session = self.lanes().sessions.acquire(session_id).await;
        let session_id = session_id.to_owned();
        self.call(move |store| store.delete_builder_chat(&session_id)).await
    }
}
