//! `AuthorityOwner`'s session operations that hold the owner's lanes or campaign work:
//! `session_context` and `update_agent_session_reasoning`, with `_session_campaign_work`.

use crate::campaigns::{named_rows, parse, Named};
use crate::error::StoreResult;
use crate::handle::StoreHandle;
use crate::lanes::builder_chat_lane;
use crate::store::AuthorityStore;
use crate::work::CampaignWorkGuard;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{LifecycleState, WriterRole};
use sylvia_domain::session::{
    SessionContext, SessionMessage, SessionMessageKind, SessionRecord, SessionStatus,
};

fn rejected(message: &str) -> crate::error::StoreError {
    crate::actions::rejected(message, "command_rejected")
}

impl AuthorityStore {
    /// The lifecycle `_begin_campaign_work` reads from the campaign's snapshot: a missing
    /// campaign is `_read_snapshot`'s refusal (see [`crate::work`]).
    pub fn campaign_lifecycle(&self, campaign_id: &str) -> StoreResult<LifecycleState> {
        let connection = self.connect()?;
        let (columns, rows) = named_rows(
            &connection,
            "SELECT lifecycle FROM campaigns WHERE campaign_id = ?",
            &[&campaign_id],
        )?;
        let Some(values) = rows.into_iter().next() else {
            return Err(rejected("Campaign is unavailable: missing or purged"));
        };
        parse(
            &Named { columns: &columns, values }.text("lifecycle")?,
            LifecycleState::parse,
            "LifecycleState",
        )
    }
}

/// What `_session_campaign_work` holds: a Builder chat's owner lane, or campaign work.
pub enum SessionWork {
    #[allow(dead_code)] // Held for its drop.
    BuilderChat(crate::lanes::LaneGuard),
    #[allow(dead_code)] // Held for its drop.
    Campaign(CampaignWorkGuard),
}

impl StoreHandle {
    /// `SessionApplication.context`: the store's session and messages.
    async fn raw_context(&self, session_id: &str) -> StoreResult<SessionContext> {
        let session_id = session_id.to_owned();
        self.call(move |store| store.session_context(&session_id)).await
    }

    /// `AuthorityOwner.session_context`: the session's context, with the latest Creation
    /// Subflow link enriched with its parent's interview transcript.
    pub async fn session_context(&self, session_id: &str) -> StoreResult<SessionContext> {
        let _operation = self.operation()?;
        let context = self.raw_context(session_id).await?;
        if context.session.campaign_id().is_none() {
            return Ok(context);
        }
        let Some(link) = context
            .messages
            .iter()
            .rev()
            .find(|message| {
                message.kind() == SessionMessageKind::Evidence
                    && message.content().get("kind") == Some(&Json::String("creation_subflow_link".into()))
            })
            .cloned()
        else {
            return Ok(context);
        };
        let (Some(Json::String(parent_session_id)), Some(Json::Array(transcript_ids))) =
            (link.content().get("parent_session_id"), link.content().get("transcript_message_ids"))
        else {
            return Err(rejected("Creation Subflow transcript reference is invalid"));
        };
        let parent = self.raw_context(parent_session_id).await?;
        let mut transcript = Vec::new();
        for message_id in transcript_ids {
            let wanted = crate::campaigns::json_str(message_id);
            let source = parent.messages.iter().rev().find(|message| message.message_id() == wanted);
            let Some(source) = source.filter(|source| {
                matches!(source.kind(), SessionMessageKind::Talk | SessionMessageKind::User)
            }) else {
                return Err(rejected("Creation Subflow transcript source is unavailable"));
            };
            let content = match source.content().get("text") {
                None => String::new(),
                Some(text) => crate::campaigns::json_str(text),
            };
            let mut item = JsonMap::new();
            item.insert("source_message_id".into(), source.message_id().into());
            item.insert(
                "role".into(),
                if source.kind() == SessionMessageKind::Talk { "assistant" } else { "user" }.into(),
            );
            item.insert("content".into(), content.into());
            transcript.push(Json::Object(item));
        }
        let mut content = link.content().clone();
        content.insert("interview_transcript".into(), Json::Array(transcript));
        let enriched = SessionMessage::new(
            link.session_id().to_owned(),
            link.sequence(),
            link.message_id().to_owned(),
            link.kind(),
            &Json::Object(content),
            link.canonical_sequence(),
        )?;
        let messages = context
            .messages
            .into_iter()
            .map(|message| if message.message_id() == link.message_id() { enriched.clone() } else { message })
            .collect();
        Ok(SessionContext { session: context.session, messages })
    }

    /// `AuthorityOwner._session_campaign_work`: a campaign-less Builder chat's owner lane, or the
    /// session campaign's work, each re-checked against the session after it is held.
    pub async fn session_campaign_work(&self, session_id: &str) -> StoreResult<SessionWork> {
        let initial = self.raw_context(session_id).await?;
        match initial.session.campaign_id() {
            None => {
                let guard = self.lanes().owner.acquire(&builder_chat_lane(session_id)).await;
                let current = self.raw_context(session_id).await?;
                if current.session.campaign_id().is_some() {
                    return Err(rejected("Builder chat campaign association changed"));
                }
                Ok(SessionWork::BuilderChat(guard))
            }
            Some(campaign_id) => {
                let campaign_id = campaign_id.to_owned();
                let guard = self
                    .lanes()
                    .work
                    .begin(&campaign_id, || {
                        let campaign_id = campaign_id.clone();
                        async move { self.call(move |store| store.campaign_lifecycle(&campaign_id)).await }
                    })
                    .await?;
                let current = self.raw_context(session_id).await?;
                if current.session.campaign_id() != Some(campaign_id.as_str()) {
                    return Err(rejected("Agent session campaign identity changed"));
                }
                Ok(SessionWork::Campaign(guard))
            }
        }
    }

    /// `AuthorityOwner.update_agent_session_reasoning`: `_operation()`, the session's campaign
    /// work, then `SessionApplication.update_reasoning` under the session lane.
    pub async fn update_agent_session_reasoning(
        &self,
        session_id: &str,
        effort: Option<&str>,
        once: Option<&str>,
    ) -> StoreResult<SessionRecord> {
        let _operation = self.operation()?;
        let _work = self.session_campaign_work(session_id).await?;
        let _session = self.lanes().sessions.acquire(session_id).await;
        let (session_id, effort, once) =
            (session_id.to_owned(), effort.map(str::to_owned), once.map(str::to_owned));
        self.call(move |store| {
            store.update_agent_session_reasoning(&session_id, effort.as_deref(), once.as_deref())
        })
        .await
    }
}

/// One live session operation's admission (`agent_session_work`, `builder_session_work`): the
/// session's context, read again once the work was held, kept with the work until it drops.
pub struct HeldSession {
    pub context: SessionContext,
    _work: SessionWork,
    _operation: crate::owner_lifecycle::OperationGuard,
}

impl StoreHandle {
    /// `_campaign_work(campaign_id)`: the campaign's work, refused as `_begin_campaign_work`
    /// refuses it.
    async fn campaign_work(&self, campaign_id: &str) -> StoreResult<CampaignWorkGuard> {
        let owned = campaign_id.to_owned();
        self.lanes()
            .work
            .begin(campaign_id, || {
                let campaign_id = owned.clone();
                async move { self.call(move |store| store.campaign_lifecycle(&campaign_id)).await }
            })
            .await
    }

    /// `AuthorityOwner.agent_session_work(session_id)`: a DM session's operation under its
    /// campaign's work, while the session is active and still its campaign's writer.
    pub async fn agent_session_work(&self, session_id: &str) -> StoreResult<HeldSession> {
        let operation = self.operation()?;
        let initial = self.raw_context(session_id).await?;
        let Some(campaign_id) = initial.session.campaign_id().map(str::to_owned) else {
            return Err(rejected("DM sessions require campaign authority"));
        };
        let work = self.campaign_work(&campaign_id).await?;
        let context = self.raw_context(session_id).await?;
        if context.session.campaign_id() != Some(campaign_id.as_str()) {
            return Err(rejected("Agent session campaign identity changed"));
        }
        if context.session.fields().status != SessionStatus::Active {
            return Err(rejected("Agent session is not active"));
        }
        let id = campaign_id.clone();
        let writer = self.call(move |store| store.snapshot(&id)).await?.state.writer;
        if writer != context.session.role() {
            return Err(rejected("Agent session no longer holds campaign writer authority"));
        }
        Ok(HeldSession { context, _work: SessionWork::Campaign(work), _operation: operation })
    }

    /// `AuthorityOwner.builder_session_work(session_id)`: one active Builder session's operation,
    /// under a standalone chat's owner lane or its campaign's work.
    pub async fn builder_session_work(&self, session_id: &str) -> StoreResult<HeldSession> {
        let operation = self.operation()?;
        let initial = self.raw_context(session_id).await?;
        let check = |context: &SessionContext| -> StoreResult<()> {
            if context.session.fields().status != SessionStatus::Active {
                return Err(rejected("Agent session is not active"));
            }
            if context.session.role() != WriterRole::Builder {
                return Err(rejected("Full Builder tools require a Builder session"));
            }
            Ok(())
        };
        match initial.session.campaign_id().map(str::to_owned) {
            None => {
                let guard = self.lanes().owner.acquire(&builder_chat_lane(session_id)).await;
                let context = self.raw_context(session_id).await?;
                if context.session.campaign_id().is_some() {
                    return Err(rejected("Builder chat campaign association changed"));
                }
                check(&context)?;
                Ok(HeldSession { context, _work: SessionWork::BuilderChat(guard), _operation: operation })
            }
            Some(campaign_id) => {
                let work = self.campaign_work(&campaign_id).await?;
                let context = self.raw_context(session_id).await?;
                if context.session.campaign_id() != Some(campaign_id.as_str()) {
                    return Err(rejected("Agent session campaign identity changed"));
                }
                check(&context)?;
                Ok(HeldSession { context, _work: SessionWork::Campaign(work), _operation: operation })
            }
        }
    }

    /// `AuthorityOwner.append_live_steering_message(...)`: steering-bound prose for an active
    /// Builder or DM session, under its session lane but without its campaign work (the live
    /// operation already holds that). Only safe prose and steering are accepted.
    pub async fn append_live_steering_message(
        &self,
        session_id: &str,
        message_id: &str,
        kind: SessionMessageKind,
        content: &JsonMap,
    ) -> StoreResult<SessionMessage> {
        let _operation = self.operation()?;
        if !matches!(
            kind,
            SessionMessageKind::SafeThink | SessionMessageKind::Talk | SessionMessageKind::Steering
        ) {
            return Err(sylvia_domain::DomainError::type_error(
                "Live steering persistence only accepts safe prose or steering",
            )
            .into());
        }
        let context = self.raw_context(session_id).await?;
        if context.session.fields().status != SessionStatus::Active {
            return Err(rejected("Agent session is not active"));
        }
        if !matches!(context.session.role(), WriterRole::Builder | WriterRole::Dm) {
            return Err(rejected("Live steering requires an agent writer session"));
        }
        let _session = self.lanes().sessions.acquire(session_id).await;
        let (session_id, message_id, content) =
            (session_id.to_owned(), message_id.to_owned(), content.clone());
        self.call(move |store| store.append_agent_message(&session_id, &message_id, kind, &content, None))
            .await
    }

    /// `AuthorityOwner.append_session_canonical_references(session_id, events)`: the session's
    /// campaign work, then one canonical reference per event under the session lane.
    pub async fn append_session_canonical_references(
        &self,
        session_id: &str,
        events: &[sylvia_domain::authority::CanonicalEvent],
    ) -> StoreResult<Vec<SessionMessage>> {
        let _operation = self.operation()?;
        let _work = self.session_campaign_work(session_id).await?;
        let _session = self.lanes().sessions.acquire(session_id).await;
        let (session_id, events) = (session_id.to_owned(), events.to_vec());
        self.call(move |store| store.append_canonical_references(&session_id, &events)).await
    }

    /// `AuthorityOwner.set_builder_chat_archived(session_id, archived=...)`: stop or resume an
    /// idle standalone Builder chat, refused while its lane is busy rather than waiting.
    pub async fn set_builder_chat_archived(
        &self,
        session_id: &str,
        archived: bool,
    ) -> StoreResult<sylvia_domain::session::BuilderChatRecord> {
        let Some(_lane) = self.lanes().owner.try_acquire(&builder_chat_lane(session_id)) else {
            return Err(crate::actions::rejected(
                "Finish or stop this Builder's work before archiving it",
                "builder_chat_work_in_progress",
            ));
        };
        let _operation = self.operation()?;
        let context = self.raw_context(session_id).await?;
        if context.session.campaign_id().is_some() {
            return Err(rejected("Archive the campaign to archive its Interview or Builder"));
        }
        let status = context.session.fields().status;
        if !matches!(status, SessionStatus::Active | SessionStatus::Stopped) {
            return Err(rejected("Resolve this Builder's session state before archiving it"));
        }
        let target = if archived { SessionStatus::Stopped } else { SessionStatus::Active };
        if status != target {
            let id = session_id.to_owned();
            if archived {
                // `SessionApplication.update_status`, under the session lane.
                let _session = self.lanes().sessions.acquire(session_id).await;
                self.call(move |store| store.update_agent_session_status(&id, target)).await?;
            } else {
                self.call(move |store| store.resume_agent_session(&id)).await?;
            }
        }
        let id = session_id.to_owned();
        let chats = self.call(|store| store.builder_chat_index()).await?;
        chats
            .into_iter()
            .find(|chat| chat.session().session_id() == id)
            .ok_or_else(|| crate::error::StoreError::Other("StopIteration".into()))
    }

    /// `AuthorityOwner.append_private_provider_record(record)`.
    pub async fn append_private_provider_record(
        &self,
        record: &sylvia_domain::provider_records::ProviderPrivateRecord,
    ) -> StoreResult<()> {
        let _operation = self.operation()?;
        let record = record.clone();
        self.call(move |store| store.append_private_provider_record(&record)).await
    }

    /// `AuthorityOwner.finish_private_provider_attempt(session_id, attempt_id, completed=...)`.
    pub async fn finish_private_provider_attempt(
        &self,
        session_id: &str,
        attempt_id: &str,
        completed: bool,
    ) -> StoreResult<()> {
        let _operation = self.operation()?;
        let (session_id, attempt_id) = (session_id.to_owned(), attempt_id.to_owned());
        self.call(move |store| store.finish_private_provider_attempt(&session_id, &attempt_id, completed))
            .await
    }
}

impl StoreHandle {
    /// `AuthorityOwner.stop_agent_session(session_id)`: under the session's campaign work, refused
    /// while its campaign awaits a mechanics choice, then the session stopped under its session
    /// lane (`SessionApplication.update_status`). The answer is the stopped session.
    pub async fn stop_agent_session(&self, session_id: &str) -> StoreResult<SessionRecord> {
        let _operation = self.operation()?;
        let _work = self.session_campaign_work(session_id).await?;
        let context = self.raw_context(session_id).await?;
        if let Some(campaign_id) = context.session.campaign_id().map(str::to_owned) {
            if self.call(move |store| store.mechanics_choice_is_pending(&campaign_id)).await? {
                return Err(crate::actions::rejected(
                    "Campaign is awaiting a mechanics choice",
                    "mechanics_choice_pending",
                ));
            }
        }
        let _session = self.lanes().sessions.acquire(session_id).await;
        let id = session_id.to_owned();
        self.call(move |store| store.update_agent_session_status(&id, SessionStatus::Stopped)).await
    }
}
