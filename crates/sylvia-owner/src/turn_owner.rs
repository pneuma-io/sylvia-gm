//! `TurnOwner` for the owner's reads: the GM turn's owner calls, as delegation.
//!
//! Each method is the owner's whole operation (lane 3's: `sylvia_app::authority::turn_reads`,
//! #414 and #420, and the store's own operations). A call added later that lane 3 has not
//! ported yet answers `rust_port_pending`, naming the `AuthorityOwner` method.

use crate::commands::campaigns::store_error;
use crate::owner_reads::OwnerReads;
use sylvia_agent::provider_session::reads::{HeldWork, NarrationAttemptStart, TurnOwner};
use sylvia_agent::stateless_mcp::listing::CatalogEntry;
use sylvia_agent::stateless_mcp::AuthorityFailure;
use sylvia_app::actor_decisions::ActorDecisionRequest;
use sylvia_app::authority::actions::{self, ActionError, ActionExecution};
use sylvia_app::authority::narration_attempts::{self as narration, NarrationStart};
use sylvia_app::authority::{campaign_file, content_reads, knowledge, player_reads, turn_reads, turns};
use sylvia_base::{CodedError, CodedResult, Json, JsonMap};
use sylvia_domain::authority::{
    ActionCommand, ActionReceipt, CanonicalEvent, CanonicalReplay, NarrationInterruption, ProseChannel,
};
use sylvia_domain::provider_records::ProviderPrivateRecord;
use sylvia_domain::authority::ModelDecisionBoundary;
use sylvia_domain::package_library::PackageLockRecord;
use sylvia_domain::session::{SessionActivityBasis, SessionContext, SessionMessage, SessionMessageKind, SessionRecord};
use sylvia_provider::BoxFuture;
use sylvia_store::snapshots::DisplayQuery;

/// A store operation's refusal, in its class.
fn store_failure(error: sylvia_store::StoreError) -> AuthorityFailure {
    AuthorityFailure::Coded(store_error(error))
}

/// An owner refusal in its class (`Domain` keeps a `MechanicsRejected`'s fields).
fn authority_failure(error: ActionError) -> AuthorityFailure {
    match error {
        ActionError::Domain(error) => AuthorityFailure::Domain(error),
        ActionError::Store(error) => AuthorityFailure::Coded(store_error(error)),
    }
}

/// An `AuthorityOwner` method lane 3 has not ported yet.
fn pending(method: &str) -> CodedError {
    CodedError::new(
        crate::dispatch::PORT_PENDING,
        format!("The Rust owner does not answer AuthorityOwner.{method} yet."),
    )
}

impl TurnOwner for OwnerReads {
    fn model_decision_boundary<'a>(
        &'a self,
        campaign_id: &'a str,
    ) -> BoxFuture<'a, CodedResult<Option<ModelDecisionBoundary>>> {
        Box::pin(async move {
            turn_reads::model_decision_boundary(self.store(), campaign_id).await.map_err(store_error)
        })
    }

    fn campaign_preparation_boundary<'a>(
        &'a self,
        campaign_id: &'a str,
    ) -> BoxFuture<'a, CodedResult<Option<ModelDecisionBoundary>>> {
        Box::pin(async move {
            turn_reads::campaign_preparation_boundary(self.store(), campaign_id).await.map_err(store_error)
        })
    }

    fn current_package_lock<'a>(
        &'a self,
        campaign_id: &'a str,
    ) -> BoxFuture<'a, CodedResult<PackageLockRecord>> {
        Box::pin(async move {
            turn_reads::current_package_lock(self.store(), campaign_id).await.map_err(store_error)
        })
    }

    fn record_admin_full_audit(&self, payload: JsonMap) -> CodedResult<()> {
        OwnerReads::record_admin_full_audit(self, Json::Object(payload))
    }

    fn campaign_panels<'a>(&'a self, _campaign_id: &'a str) -> BoxFuture<'a, CodedResult<JsonMap>> {
        Box::pin(async { Err(pending("campaign_panels")) })
    }

    fn action_sequence<'a>(
        &'a self,
        _session_id: &'a str,
        _sequence_id: &'a Json,
    ) -> BoxFuture<'a, CodedResult<Json>> {
        Box::pin(async { Err(pending("action_sequence")) })
    }

    fn compose_action_sequence_from_agent<'a>(
        &'a self,
        _campaign_id: &'a str,
        _session_id: &'a str,
        _arguments: &'a JsonMap,
    ) -> BoxFuture<'a, CodedResult<Json>> {
        Box::pin(async { Err(pending("_compose_action_sequence_from_agent")) })
    }

    fn execute_action_sequence_from_agent<'a>(
        &'a self,
        _campaign_id: &'a str,
        _session_id: &'a str,
        _arguments: &'a JsonMap,
    ) -> BoxFuture<'a, CodedResult<Json>> {
        Box::pin(async { Err(pending("_execute_action_sequence_from_agent")) })
    }

    fn record_mechanics_diagnostic(&self, diagnostic: JsonMap) -> CodedResult<()> {
        // Kept beside the Full Audit trace in lane 3's `NarrationState` (#442).
        self.store().narration().record_mechanics_diagnostic(diagnostic);
        Ok(())
    }

    fn provider_continuation<'a>(
        &'a self,
        session_id: &'a str,
        response_id: &'a str,
        provider_id: &'a str,
        model_id: &'a str,
    ) -> BoxFuture<'a, CodedResult<Option<Json>>> {
        Box::pin(async move {
            turn_reads::provider_continuation(self.store(), session_id, response_id, provider_id, model_id)
                .await
                .map_err(store_error)
        })
    }

    fn package_record<'a>(
        &'a self,
        package_id: &'a str,
        version: &'a str,
        sha256: &'a str,
    ) -> BoxFuture<'a, CodedResult<JsonMap>> {
        Box::pin(async move {
            self.store().package_record(package_id, version, sha256).await.map_err(store_error)
        })
    }

    fn package_lock_lineage<'a>(
        &'a self,
        campaign_id: &'a str,
    ) -> BoxFuture<'a, CodedResult<Vec<PackageLockRecord>>> {
        Box::pin(async move {
            let campaign_id = campaign_id.to_owned();
            self.store()
                .call(move |store| store.package_lock_lineage(&campaign_id))
                .await
                .map_err(store_error)
        })
    }

    fn append_agent_message<'a>(
        &'a self,
        session_id: &'a str,
        message_id: &'a str,
        kind: SessionMessageKind,
        content: &'a JsonMap,
    ) -> BoxFuture<'a, CodedResult<SessionMessage>> {
        Box::pin(async move {
            self.store()
                .append_agent_message(session_id, message_id, kind, content)
                .await
                .map_err(store_error)
        })
    }

    fn catalog<'a>(
        &'a self,
        campaign_id: &'a str,
        include_unexposed: bool,
    ) -> BoxFuture<'a, CodedResult<Vec<CatalogEntry>>> {
        Box::pin(async move {
            let tools = content_reads::catalog(self.store(), campaign_id, include_unexposed)
                .await
                .map_err(store_error)?;
            Ok(tools
                .into_iter()
                .map(|tool| CatalogEntry {
                    package_id: tool.package_id,
                    package_version: tool.package_version,
                    package_sha256: tool.package_sha256,
                    name: tool.name,
                    capability: tool.capability,
                    input_schema: tool.input_schema,
                    description: tool.description,
                    model_behavior: tool.model_behavior,
                })
                .collect())
        })
    }

    fn accepted_campaign_plan<'a>(&'a self, campaign_id: &'a str) -> BoxFuture<'a, CodedResult<JsonMap>> {
        Box::pin(async move {
            let id = campaign_id.to_owned();
            let plan = self
                .store()
                .call(move |store| store.accepted_campaign_plan(&id))
                .await
                .map_err(store_error)?;
            match plan {
                Some(plan) => Ok(plan.content().clone()),
                None => Err(CodedError::rejected("Accepted Campaign Plan is unavailable")
                    .with_error_type("CommandRejected")),
            }
        })
    }

    fn execute_action<'a>(
        &'a self,
        command: &'a ActionCommand,
    ) -> BoxFuture<'a, Result<ActionExecution, AuthorityFailure>> {
        Box::pin(async move {
            let Some(owner_id) = self.owner_id() else {
                return Err(CodedError::new(
                    "internal_error",
                    "The GM turn's owner reads were built without the owner's identity.",
                )
                .with_error_type("RuntimeError")
                .into());
            };
            actions::execute_action(self.store(), owner_id, command).await.map_err(|error| match error {
                ActionError::Domain(error) => AuthorityFailure::Domain(error),
                ActionError::Store(error) => AuthorityFailure::Coded(store_error(error)),
            })
        })
    }

    fn campaign_narrative_options<'a>(&'a self, session_id: &'a str) -> BoxFuture<'a, CodedResult<JsonMap>> {
        Box::pin(async move {
            turn_reads::campaign_narrative_options(self.store(), session_id).await.map_err(store_error)
        })
    }

    fn current_open_turn<'a>(&'a self, campaign_id: &'a str) -> BoxFuture<'a, CodedResult<Option<String>>> {
        Box::pin(
            async move { turns::current_open_turn(self.store(), campaign_id).await.map_err(store_error) },
        )
    }

    fn update_campaign_file<'a>(
        &'a self,
        session_id: &'a str,
        update_id: &'a str,
        updates: Vec<JsonMap>,
        basis_message_ids: Vec<String>,
        turn_id: Option<&'a str>,
    ) -> BoxFuture<'a, Result<JsonMap, AuthorityFailure>> {
        Box::pin(async move {
            let Some(owner_id) = self.owner_id() else {
                return Err(CodedError::new(
                    "internal_error",
                    "The GM turn's owner reads were built without the owner's identity.",
                )
                .with_error_type("RuntimeError")
                .into());
            };
            let update = campaign_file::CampaignFileUpdate {
                session_id: session_id.to_owned(),
                update_id: update_id.to_owned(),
                updates,
                basis_message_ids,
                turn_id: turn_id.map(str::to_owned),
            };
            campaign_file::update_campaign_file(self.store(), owner_id, &update)
                .await
                .map_err(authority_failure)
        })
    }

    fn search_campaign_knowledge<'a>(
        &'a self,
        campaign_id: &'a str,
        query: &'a Json,
        sources: Option<&'a Json>,
        limit: &'a Json,
        offset: &'a Json,
    ) -> BoxFuture<'a, Result<JsonMap, AuthorityFailure>> {
        Box::pin(async move {
            let request = knowledge::KnowledgeSearch {
                query: query.clone(),
                sources: sources.cloned(),
                limit: limit.clone(),
                offset: offset.clone(),
            };
            knowledge::search_campaign_knowledge(self.store(), campaign_id, &request)
                .await
                .map_err(authority_failure)
        })
    }

    fn read_campaign_reference<'a>(
        &'a self,
        campaign_id: &'a str,
        source: &'a Json,
        path: &'a Json,
    ) -> BoxFuture<'a, Result<JsonMap, AuthorityFailure>> {
        // The MCP server's read passes no `maximum_bytes`: the owner's default.
        Box::pin(async move {
            knowledge::read_campaign_reference(self.store(), campaign_id, source, path, None)
                .await
                .map_err(authority_failure)
        })
    }

    fn actor_decision_request<'a>(
        &'a self,
        campaign_id: &'a str,
        _turn_id: &'a str,
    ) -> BoxFuture<'a, CodedResult<Option<ActorDecisionRequest>>> {
        Box::pin(async move {
            // `AuthorityOwner.actor_decision_request`'s opening: a package that declares no
            // isolated participant has no actor decision. The rest (the turn checks and the
            // projection) is T6's, with `run_required_actor_decision`.
            let id = campaign_id.to_owned();
            let isolated = self
                .store()
                .call(move |store| {
                    let snapshot = store.display_snapshot(&id, &DisplayQuery::default())?;
                    let declared = content_reads::decision_scope_declaration(store, &snapshot.state)?;
                    Ok(declared.is_some_and(|declared| declared.contains_key("isolated_participant")))
                })
                .await
                .map_err(store_error)?;
            if isolated {
                return Err(pending("actor_decision_request"));
            }
            Ok(None)
        })
    }

    fn agent_session_work<'a>(&'a self, session_id: &'a str) -> BoxFuture<'a, Result<HeldWork, AuthorityFailure>> {
        Box::pin(async move {
            let held = self.store().agent_session_work(session_id).await.map_err(store_failure)?;
            let context = held.context.clone();
            Ok(HeldWork { context, work: Box::new(held) })
        })
    }

    fn campaign_ending_choice_text<'a>(
        &'a self,
        _session_id: &'a str,
        _message_id: &'a str,
        _choice: &'a JsonMap,
    ) -> BoxFuture<'a, Result<String, AuthorityFailure>> {
        // Lane 3's N2b (#483).
        Box::pin(async { Err(pending("campaign_ending_choice_text").into()) })
    }

    fn player_action_response_basis<'a>(
        &'a self,
        session_id: &'a str,
        action_id: &'a str,
        automatic: bool,
    ) -> BoxFuture<'a, Result<JsonMap, AuthorityFailure>> {
        Box::pin(async move {
            player_reads::player_action_response_basis(self.store(), session_id, action_id, automatic)
                .await
                .map_err(store_failure)
        })
    }

    fn unresolved_session_interruption<'a>(
        &'a self,
        context: &'a SessionContext,
    ) -> BoxFuture<'a, Result<Option<(NarrationInterruption, bool)>, AuthorityFailure>> {
        Box::pin(async move {
            narration::unresolved_session_interruption(self.store(), context).await.map_err(authority_failure)
        })
    }

    fn narration_interruption<'a>(
        &'a self,
        campaign_id: &'a str,
        interruption_id: &'a str,
    ) -> BoxFuture<'a, Result<NarrationInterruption, AuthorityFailure>> {
        Box::pin(async move {
            narration::narration_interruption(self.store(), campaign_id, interruption_id)
                .await
                .map_err(authority_failure)
        })
    }

    fn redirect_narration<'a>(
        &'a self,
        _session_id: &'a str,
        _message_id: &'a str,
        _content: &'a JsonMap,
        _interruption_id: &'a str,
    ) -> BoxFuture<'a, Result<NarrationInterruption, AuthorityFailure>> {
        // Lane 3's N2b (#483).
        Box::pin(async { Err(pending("redirect_narration").into()) })
    }

    fn saved_action_receipt<'a>(
        &'a self,
        campaign_id: &'a str,
        action_id: &'a str,
    ) -> BoxFuture<'a, Result<ActionReceipt, AuthorityFailure>> {
        Box::pin(async move {
            narration::saved_action_receipt(self.store(), campaign_id, action_id).await.map_err(authority_failure)
        })
    }

    fn start_narration_attempt(&self, start: NarrationAttemptStart) -> BoxFuture<'_, Result<(), AuthorityFailure>> {
        Box::pin(async move {
            let cancel = start.cancel;
            let start = NarrationStart {
                campaign_id: start.campaign_id,
                attempt_id: start.attempt_id,
                turn_id: start.turn_id,
                cancel: Box::new(move || cancel()),
                last_action_id: start.last_action_id,
                interruption_id: start.interruption_id,
            };
            narration::start_narration_attempt(self.store(), start).await.map_err(authority_failure)
        })
    }

    fn emit_narration_token<'a>(
        &'a self,
        campaign_id: &'a str,
        attempt_id: &'a str,
        channel: ProseChannel,
        token: &'a str,
        response_blocks: Option<&'a Json>,
    ) -> BoxFuture<'a, Result<(), AuthorityFailure>> {
        Box::pin(async move {
            narration::emit_narration_token(self.store(), campaign_id, attempt_id, channel, token, response_blocks)
                .await
                .map_err(authority_failure)
        })
    }

    fn flush_narration<'a>(
        &'a self,
        campaign_id: &'a str,
        attempt_id: &'a str,
    ) -> BoxFuture<'a, Result<(), AuthorityFailure>> {
        Box::pin(async move {
            narration::flush_narration(self.store(), campaign_id, attempt_id).await.map_err(authority_failure)
        })
    }

    fn bind_narration_action<'a>(
        &'a self,
        campaign_id: &'a str,
        attempt_id: &'a str,
        action_id: &'a str,
    ) -> BoxFuture<'a, Result<(), AuthorityFailure>> {
        Box::pin(async move {
            narration::bind_narration_action(self.store(), campaign_id, attempt_id, action_id)
                .await
                .map_err(authority_failure)
        })
    }

    fn complete_narration_attempt<'a>(
        &'a self,
        campaign_id: &'a str,
        attempt_id: &'a str,
    ) -> BoxFuture<'a, Result<Vec<CanonicalEvent>, AuthorityFailure>> {
        Box::pin(async move {
            narration::complete_narration_attempt(self.store(), campaign_id, attempt_id)
                .await
                .map_err(authority_failure)
        })
    }

    fn interrupt_narration<'a>(
        &'a self,
        campaign_id: &'a str,
        attempt_id: &'a str,
    ) -> BoxFuture<'a, Result<NarrationInterruption, AuthorityFailure>> {
        Box::pin(async move {
            narration::interrupt_narration(self.store(), campaign_id, attempt_id).await.map_err(authority_failure)
        })
    }

    fn disconnect_narration<'a>(
        &'a self,
        campaign_id: &'a str,
        attempt_id: &'a str,
        now: f64,
    ) -> BoxFuture<'a, Result<f64, AuthorityFailure>> {
        Box::pin(async move {
            narration::disconnect_narration(self.store(), campaign_id, attempt_id, now)
                .await
                .map_err(authority_failure)
        })
    }

    fn reconnect_narration<'a>(
        &'a self,
        campaign_id: &'a str,
        attempt_id: &'a str,
        now: f64,
        acknowledged_sequence: i64,
    ) -> BoxFuture<'a, Result<CanonicalReplay, AuthorityFailure>> {
        Box::pin(async move {
            narration::reconnect_narration(self.store(), campaign_id, attempt_id, now, acknowledged_sequence)
                .await
                .map_err(authority_failure)
        })
    }

    fn expire_narration<'a>(
        &'a self,
        campaign_id: &'a str,
        attempt_id: &'a str,
        now: f64,
    ) -> BoxFuture<'a, Result<NarrationInterruption, AuthorityFailure>> {
        Box::pin(async move {
            narration::expire_narration(self.store(), campaign_id, attempt_id, now).await.map_err(authority_failure)
        })
    }

    fn append_private_provider_record<'a>(
        &'a self,
        record: &'a ProviderPrivateRecord,
    ) -> BoxFuture<'a, Result<(), AuthorityFailure>> {
        Box::pin(async move { self.store().append_private_provider_record(record).await.map_err(store_failure) })
    }

    fn finish_private_provider_attempt<'a>(
        &'a self,
        session_id: &'a str,
        attempt_id: &'a str,
        completed: bool,
    ) -> BoxFuture<'a, Result<(), AuthorityFailure>> {
        Box::pin(async move {
            self.store().finish_private_provider_attempt(session_id, attempt_id, completed).await.map_err(store_failure)
        })
    }

    fn append_session_canonical_references<'a>(
        &'a self,
        session_id: &'a str,
        events: &'a [CanonicalEvent],
    ) -> BoxFuture<'a, Result<Vec<SessionMessage>, AuthorityFailure>> {
        Box::pin(async move {
            self.store().append_session_canonical_references(session_id, events).await.map_err(store_failure)
        })
    }

    fn append_live_steering_message<'a>(
        &'a self,
        session_id: &'a str,
        message_id: &'a str,
        kind: SessionMessageKind,
        content: &'a JsonMap,
    ) -> BoxFuture<'a, Result<SessionMessage, AuthorityFailure>> {
        Box::pin(async move {
            self.store()
                .append_live_steering_message(session_id, message_id, kind, content)
                .await
                .map_err(store_failure)
        })
    }

    fn stop_agent_session<'a>(&'a self, _session_id: &'a str) -> BoxFuture<'a, Result<SessionRecord, AuthorityFailure>> {
        // Not ported yet by lane 3.
        Box::pin(async { Err(pending("stop_agent_session").into()) })
    }

    fn session_activity_basis<'a>(
        &'a self,
        session_id: &'a str,
        attempt_id: Option<&'a str>,
    ) -> BoxFuture<'a, CodedResult<SessionActivityBasis>> {
        Box::pin(async move {
            let (session_id, attempt_id) = (session_id.to_owned(), attempt_id.map(str::to_owned));
            self.store()
                .call(move |store| store.session_activity_basis(&session_id, attempt_id.as_deref()))
                .await
                .map_err(store_error)
        })
    }
}

#[cfg(test)]
mod tests {
    use crate::dispatch::Failure;
    use sylvia_agent::stateless_mcp::listing::listing_refusal;
    use sylvia_base::CodedError;

    /// A listing the MCP server refuses reaches Python's owner as an `ExceptionGroup`, which
    /// its `except AuthorityError` does not catch: the fixed `internal_error` reply, with
    /// `ExceptionGroup` in local diagnostics.
    #[test]
    fn a_refused_listing_is_an_internal_fault() {
        let refused =
            CodedError::new("probe_code", "Campaign is transitioning").with_error_type("CommandRejected");
        assert_eq!(
            Failure::from(listing_refusal(refused)),
            Failure::Internal { error_type: "ExceptionGroup" }
        );
    }
}
