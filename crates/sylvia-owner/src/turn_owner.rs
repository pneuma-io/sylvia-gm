//! `TurnOwner` for the owner's reads: the GM turn's owner calls, as delegation.
//!
//! Each method is the owner's whole operation (lane 3's: `sylvia_app::authority::turn_reads`,
//! #414 and #420, and the store's own operations). A call added later that lane 3 has not
//! ported yet answers `rust_port_pending`, naming the `AuthorityOwner` method.

use crate::commands::campaigns::store_error;
use crate::owner_reads::OwnerReads;
use sylvia_agent::provider_session::reads::TurnOwner;
use sylvia_agent::stateless_mcp::listing::CatalogEntry;
use sylvia_app::authority::{content_reads, turn_reads};
use sylvia_base::{CodedError, CodedResult, Json, JsonMap};
use sylvia_domain::authority::ModelDecisionBoundary;
use sylvia_domain::package_library::PackageLockRecord;
use sylvia_domain::session::{SessionActivityBasis, SessionMessage, SessionMessageKind};
use sylvia_provider::BoxFuture;

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

    fn campaign_narrative_options<'a>(&'a self, session_id: &'a str) -> BoxFuture<'a, CodedResult<JsonMap>> {
        Box::pin(async move {
            turn_reads::campaign_narrative_options(self.store(), session_id).await.map_err(store_error)
        })
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
