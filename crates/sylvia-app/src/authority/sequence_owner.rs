//! The authority as lane 2's [`SequenceOwner`]: the owner operations
//! `application/action_sequences.py` calls, on the owner's store handle.
//!
//! [`AuthoritySequences`] is the store handle with the owner's identity, which direct actions
//! commit under. Each operation is the authority's own (`direct_actions`, `content_reads`,
//! `narration_attempts`, the store's reads), so a sequence runs exactly what a player's direct
//! action or the owner's reads run.
//!
//! Python's `_lane(campaign_id)` and `_narration_lock` are `RLock`s: `read` runs inside
//! `_execute`'s held lane, and `bind_narration_action` takes the narration lock `_execute` already
//! holds. Here the same task's nested call reuses what that task holds, through the task-local
//! marks of `sylvia_store::reentry`, set while a guard is held. A marker does not follow work into `spawn_blocking` or a
//! spawned task, and nothing here re-enters a lane or the narration lock from one: store calls
//! run on the store thread and never take an owner lane.
//!
//! `campaign_panels`, which a sequence reads to rebind a coordinate-map step's selection
//! (`rebind_map`, a panel step whose selection carries `map_revision`), is the authority's own
//! (`campaign_panels`).

use super::actions::ActionError;
use super::content_reads::CatalogTool;
use super::direct_actions;
use crate::action_sequences::{
    PlayerActionAnswer, PlayerActionRequest, ResolvedControl, SequenceFuture, SequenceOwner,
};
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{ActionReceipt, CampaignSnapshot};
use sylvia_domain::session::{SessionContext, SessionMessageKind};
use sylvia_store::actions::IdempotencyHit;
use sylvia_store::StoreHandle;

use sylvia_store::reentry::{holding_lane, holding_narration, holds_lane, holds_narration};

/// The authority as a [`SequenceOwner`]: the owner's store handle and the owner identity its
/// direct actions commit under.
#[derive(Clone, Copy)]
pub struct AuthoritySequences<'a> {
    store: &'a StoreHandle,
    owner_id: &'a str,
}

impl<'a> AuthoritySequences<'a> {
    pub fn new(store: &'a StoreHandle, owner_id: &'a str) -> Self {
        Self { store, owner_id }
    }
}

fn failure(error: direct_actions::Failure) -> ActionError {
    ActionError::from(error)
}

impl SequenceOwner for AuthoritySequences<'_> {
    fn with_operation<'a, R: Send + 'a>(&'a self, body: SequenceFuture<'a, R>) -> SequenceFuture<'a, R> {
        Box::pin(async move {
            let _operation = self.store.operation()?;
            body.await
        })
    }

    fn with_lane<'a, R: Send + 'a>(
        &'a self,
        campaign_id: &'a str,
        body: SequenceFuture<'a, R>,
    ) -> SequenceFuture<'a, R> {
        Box::pin(async move {
            if holds_lane(campaign_id) {
                return body.await;
            }
            let _lane = self.store.lanes().owner.acquire(campaign_id).await;
            holding_lane(campaign_id, body).await
        })
    }

    fn with_direct_action_work<'a, R: Send + 'a>(
        &'a self,
        campaign_id: &'a str,
        body: SequenceFuture<'a, R>,
    ) -> SequenceFuture<'a, R> {
        Box::pin(async move {
            let store = self.store;
            let owned = campaign_id.to_owned();
            let _work = store
                .lanes()
                .work
                .begin_direct_action(campaign_id, || {
                    let campaign_id = owned.clone();
                    async move { store.call(move |store| store.campaign_lifecycle(&campaign_id)).await }
                })
                .await?;
            body.await
        })
    }

    fn with_narration_lock<'a, R: Send + 'a>(&'a self, body: SequenceFuture<'a, R>) -> SequenceFuture<'a, R> {
        Box::pin(async move {
            if holds_narration() {
                return body.await;
            }
            let _region = self.store.narration().region().await;
            holding_narration(body).await
        })
    }

    fn active_dm_context<'a>(&'a self, session_id: &'a str) -> SequenceFuture<'a, SessionContext> {
        Box::pin(async move {
            let id = session_id.to_owned();
            Ok(self.store.call(move |store| super::content_reads::active_dm_context(store, &id)).await?)
        })
    }

    fn session_context<'a>(&'a self, session_id: &'a str) -> SequenceFuture<'a, SessionContext> {
        Box::pin(async move {
            let id = session_id.to_owned();
            Ok(self.store.call(move |store| store.session_context(&id)).await?)
        })
    }

    fn append_session_message<'a>(
        &'a self,
        session_id: &'a str,
        message_id: &'a str,
        kind: SessionMessageKind,
        content: &'a JsonMap,
    ) -> SequenceFuture<'a, ()> {
        Box::pin(async move {
            // `SessionApplication.append_message`: the session's lane, then the store's append.
            let _session = self.store.lanes().sessions.acquire(session_id).await;
            let (session, message, content) = (session_id.to_owned(), message_id.to_owned(), content.clone());
            self.store
                .call(move |store| store.append_agent_message(&session, &message, kind, &content, None))
                .await?;
            Ok(())
        })
    }

    fn snapshot<'a>(&'a self, campaign_id: &'a str) -> SequenceFuture<'a, CampaignSnapshot> {
        Box::pin(async move {
            let id = campaign_id.to_owned();
            Ok(self.store.call(move |store| store.snapshot(&id)).await?)
        })
    }

    fn action_hit<'a>(
        &'a self,
        campaign_id: &'a str,
        action_id: &'a str,
    ) -> SequenceFuture<'a, Option<IdempotencyHit>> {
        Box::pin(async move {
            let (campaign, action) = (campaign_id.to_owned(), action_id.to_owned());
            Ok(self.store.call(move |store| store.action_hit(&campaign, &action)).await?)
        })
    }

    fn has_pending_mechanics_action<'a>(&'a self, campaign_id: &'a str) -> SequenceFuture<'a, bool> {
        Box::pin(async move {
            let id = campaign_id.to_owned();
            Ok(self.store.call(move |store| Ok(store.pending_mechanics_action(&id)?.is_some())).await?)
        })
    }

    fn current_open_turn<'a>(&'a self, campaign_id: &'a str) -> SequenceFuture<'a, Option<String>> {
        Box::pin(async move {
            let id = campaign_id.to_owned();
            Ok(self.store.call(move |store| store.current_open_turn(&id)).await?)
        })
    }

    fn campaign_work_count<'a>(&'a self, campaign_id: &'a str) -> SequenceFuture<'a, usize> {
        // The registry every campaign work lease counts in, the GM turn's `agent_session_work`
        // among them (`_campaign_work_counts`).
        Box::pin(async move { Ok(self.store.lanes().work.count(campaign_id)) })
    }

    fn campaign_session_contexts<'a>(
        &'a self,
        campaign_id: &'a str,
    ) -> SequenceFuture<'a, Vec<SessionContext>> {
        Box::pin(async move {
            let id = campaign_id.to_owned();
            Ok(self.store.call(move |store| store.campaign_session_contexts(&id, &[])).await?)
        })
    }

    fn player_action_markers<'a>(
        &'a self,
        campaign_id: &'a str,
        action_ids: &'a [&'a str],
        kind: &'a str,
    ) -> SequenceFuture<'a, Vec<JsonMap>> {
        Box::pin(async move {
            let (campaign, ids, kind) = (
                campaign_id.to_owned(),
                action_ids.iter().map(|id| (*id).to_owned()).collect::<Vec<String>>(),
                kind.to_owned(),
            );
            Ok(self
                .store
                .call(move |store| {
                    let ids: Vec<&str> = ids.iter().map(String::as_str).collect();
                    store.player_action_markers(&campaign, &ids, &kind)
                })
                .await?)
        })
    }

    fn player_control_scope<'a>(
        &'a self,
        snapshot: &'a CampaignSnapshot,
    ) -> SequenceFuture<'a, Option<JsonMap>> {
        Box::pin(async move {
            let state = snapshot.state.clone();
            self.store
                .call(move |store| Ok(direct_actions::player_control_scope(store, &state)))
                .await?
                .map_err(failure)
        })
    }

    fn resolve_panel_control<'a>(
        &'a self,
        snapshot: &'a CampaignSnapshot,
        source: &'a Json,
        selection: &'a Json,
    ) -> SequenceFuture<'a, ResolvedControl> {
        Box::pin(async move {
            let (snapshot, source, selection) = (snapshot.clone(), source.clone(), selection.clone());
            self.store
                .call(move |store| {
                    Ok(direct_actions::resolve_panel_control(store, &snapshot, &source, &selection, false))
                })
                .await?
                .map_err(failure)
        })
    }

    fn resolve_card_control<'a>(
        &'a self,
        snapshot: &'a CampaignSnapshot,
        source: &'a Json,
        selection: &'a Json,
    ) -> SequenceFuture<'a, ResolvedControl> {
        Box::pin(async move {
            let (snapshot, source, selection) = (snapshot.clone(), source.clone(), selection.clone());
            self.store
                .call(move |store| {
                    Ok(direct_actions::resolve_card_control(store, &snapshot, &source, &selection))
                })
                .await?
                .map_err(failure)
        })
    }

    fn check_player_action<'a>(
        &'a self,
        tool: &'a CatalogTool,
        parameters: &'a JsonMap,
        snapshot: &'a CampaignSnapshot,
    ) -> SequenceFuture<'a, ()> {
        Box::pin(async move {
            direct_actions::check_player_action(tool, parameters, &snapshot.state, false).map_err(failure)
        })
    }

    fn player_action_preview<'a>(
        &'a self,
        tool: &'a CatalogTool,
        parameters: &'a JsonMap,
        snapshot: &'a CampaignSnapshot,
        source: &'a Json,
        selection: &'a Json,
    ) -> SequenceFuture<'a, Option<JsonMap>> {
        Box::pin(async move {
            let (tool, parameters, snapshot, source, selection) =
                (tool.clone(), parameters.clone(), snapshot.clone(), source.clone(), selection.clone());
            self.store
                .call(move |store| {
                    Ok(direct_actions::player_action_preview(
                        store,
                        &tool,
                        &parameters,
                        &snapshot,
                        &source,
                        &selection,
                    ))
                })
                .await?
                .map_err(failure)
        })
    }

    fn campaign_panels<'a>(&'a self, campaign_id: &'a str) -> SequenceFuture<'a, JsonMap> {
        Box::pin(super::campaign_panels::campaign_panels(self.store, campaign_id, None))
    }

    fn player_action_in_owned_work<'a>(
        &'a self,
        request: &'a PlayerActionRequest,
    ) -> SequenceFuture<'a, PlayerActionAnswer> {
        Box::pin(direct_actions::player_action_in_owned_work(self.store, self.owner_id, request))
    }

    fn append_player_action_references<'a>(
        &'a self,
        session_id: &'a str,
        receipt: &'a ActionReceipt,
    ) -> SequenceFuture<'a, ()> {
        Box::pin(direct_actions::append_references(self.store, Some(session_id), receipt))
    }

    fn catalog<'a>(&'a self, campaign_id: &'a str) -> SequenceFuture<'a, Vec<CatalogTool>> {
        Box::pin(async move {
            // `_catalog(campaign_id)`: the transition check, then the admitted tools.
            self.store.lanes().work.ensure_not_transitioning(campaign_id)?;
            let id = campaign_id.to_owned();
            Ok(self
                .store
                .call(move |store| super::content_reads::catalog_on(store, &id, false, false))
                .await?)
        })
    }

    fn open_narration_attempts<'a>(
        &'a self,
        campaign_id: &'a str,
        turn_id: Option<&'a str>,
    ) -> SequenceFuture<'a, Vec<String>> {
        Box::pin(async move {
            let Some(turn_id) = turn_id else { return Ok(Vec::new()) };
            Ok(self.store.narration().with(|inner| {
                inner
                    .attempts
                    .iter()
                    .filter(|((campaign, _), attempt)| {
                        campaign == campaign_id && !attempt.terminal && attempt.turn_id == turn_id
                    })
                    .map(|((_, attempt_id), _)| attempt_id.clone())
                    .collect()
            }))
        })
    }

    fn bind_narration_action<'a>(
        &'a self,
        campaign_id: &'a str,
        attempt_id: &'a str,
        action_id: &'a str,
    ) -> SequenceFuture<'a, ()> {
        Box::pin(async move {
            if holds_narration() {
                // `_execute` already holds `_narration_lock` (an `RLock`).
                let _operation = self.store.operation()?;
                return super::narration_attempts::bind_in_region(
                    self.store,
                    campaign_id,
                    attempt_id,
                    action_id,
                )
                .await;
            }
            super::narration_attempts::bind_narration_action(self.store, campaign_id, attempt_id, action_id)
                .await
        })
    }
}
