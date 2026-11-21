//! `AuthorityOwner`'s session operations that hold the owner's lanes or campaign work:
//! `session_context` and `update_agent_session_reasoning`, with `_session_campaign_work`.

use crate::campaigns::{named_rows, parse, Named};
use crate::error::StoreResult;
use crate::handle::StoreHandle;
use crate::lanes::builder_chat_lane;
use crate::store::AuthorityStore;
use crate::work::CampaignWorkGuard;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::LifecycleState;
use sylvia_domain::session::{SessionContext, SessionMessage, SessionMessageKind, SessionRecord};

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
enum SessionWork {
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
    async fn session_campaign_work(&self, session_id: &str) -> StoreResult<SessionWork> {
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
