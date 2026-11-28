//! The application owner's reads, as lane 4's GM request assembly and preflight call them:
//! `sylvia_agent::provider_session::reads::OpeningReads` over the store handle, with the further
//! reads the guided-start preflight makes (`campaign_index`, the admin full audit state,
//! `campaign_knowledge_catalog_for_plan`, `catalog`).
//!
//! It lives here because `sylvia-app` (the reads) and `sylvia-agent` (the traits) do not depend
//! on each other. A store failure is the owner's coded error, as a handler's is.

use sylvia_agent::provider_session::reads::OpeningReads;
use sylvia_agent::stateless_mcp::ModelMechanicsTool;
use sylvia_app::authority::{content_reads, guided, player_reads, snapshots};
use sylvia_base::{CodedError, CodedResult, Json, JsonMap};
use sylvia_domain::authority::{
    CampaignConstructionBasis, CampaignDisplaySnapshot, CampaignIndexEntry, CampaignSnapshot,
    CanonicalReplay, ModelDecisionBoundary,
};
use sylvia_domain::session::SessionContext;
use sylvia_provider::BoxFuture;
use sylvia_store::{StoreError, StoreHandle};

fn coded(error: StoreError) -> CodedError {
    crate::commands::campaigns::store_error(error)
}

/// The owner's reads over one store handle (and the narration and audit state it carries).
#[derive(Debug, Clone)]
pub struct OwnerReads {
    store: StoreHandle,
    /// This owner's own identity (`self._owner_id`), which `execute_action` checks a command's
    /// owner against; `None` for reads that never execute.
    owner_id: Option<String>,
}

impl OwnerReads {
    pub fn new(store: StoreHandle) -> Self {
        Self { store, owner_id: None }
    }

    /// The reads of the owner `owner_id`, which may also execute its mechanics (the GM turn's).
    pub fn for_owner(store: StoreHandle, owner_id: impl Into<String>) -> Self {
        Self { store, owner_id: Some(owner_id.into()) }
    }

    /// This owner's own identity, when the reads were built with it.
    pub(crate) fn owner_id(&self) -> Option<&str> {
        self.owner_id.as_deref()
    }

    /// The store handle the reads go to (for the GM turn's `TurnOwner` delegation).
    pub(crate) fn store(&self) -> &StoreHandle {
        &self.store
    }

    /// `campaign_index()`.
    pub async fn campaign_index(&self) -> CodedResult<Vec<CampaignIndexEntry>> {
        // `with self._operation():`, refused once the owner is closing.
        let _operation = self.store.operation().map_err(coded)?;
        self.store.call(|store| store.campaign_index_entries()).await.map_err(coded)
    }

    /// `admin_full_audit_enabled()`.
    pub fn admin_full_audit_enabled(&self) -> CodedResult<bool> {
        let _operation = self.store.operation().map_err(coded)?;
        Ok(self.store.narration().admin_full_audit_enabled())
    }

    /// `record_admin_full_audit(payload)`, Python's synchronous in-memory sink.
    pub fn record_admin_full_audit(&self, payload: Json) -> CodedResult<()> {
        let _operation = self.store.operation().map_err(coded)?;
        self.store.narration().record_admin_full_audit(payload).map_err(coded)
    }

    /// `campaign_knowledge_catalog_for_plan(campaign_id, plan)`.
    pub async fn campaign_knowledge_catalog_for_plan(
        &self,
        campaign_id: &str,
        plan: &JsonMap,
    ) -> CodedResult<JsonMap> {
        content_reads::campaign_knowledge_catalog_for_plan(&self.store, campaign_id, plan)
            .await
            .map_err(coded)
    }

    /// `bus.catalog(campaign_id)` as the preflight reads it: Python casts each `CatalogTool` to
    /// `ModelMechanicsTool` without converting, so the fields it reads carry over and the rest
    /// keep their defaults (`category` is `"mechanic"`, no parameter derivations).
    pub async fn catalog(&self, campaign_id: &str) -> CodedResult<Vec<ModelMechanicsTool>> {
        let tools = content_reads::catalog(&self.store, campaign_id, false).await.map_err(coded)?;
        Ok(tools.into_iter().map(model_tool).collect())
    }
}

/// `cast(ModelMechanicsTool, tool)`: the fields the provider projection reads carry over; the
/// rest keep `ModelMechanicsTool`'s defaults, as Python's `getattr` defaults give them.
pub fn model_tool(tool: content_reads::CatalogTool) -> ModelMechanicsTool {
    let mut projected = ModelMechanicsTool::new(
        tool.package_id,
        tool.package_version,
        tool.package_sha256,
        tool.name,
        tool.capability,
        tool.input_schema,
    );
    projected.description = tool.description;
    projected.model_behavior = tool.model_behavior;
    projected
}

impl OpeningReads for OwnerReads {
    fn session_context<'a>(&'a self, session_id: &'a str) -> BoxFuture<'a, CodedResult<SessionContext>> {
        Box::pin(async move { self.store.session_context(session_id).await.map_err(coded) })
    }

    fn model_context_owner<'a>(
        &'a self,
        campaign_id: &'a str,
    ) -> BoxFuture<'a, CodedResult<Option<JsonMap>>> {
        Box::pin(
            async move { content_reads::model_context_owner(&self.store, campaign_id).await.map_err(coded) },
        )
    }

    fn player_action_context<'a>(&'a self, campaign_id: &'a str) -> BoxFuture<'a, CodedResult<Vec<JsonMap>>> {
        Box::pin(
            async move { player_reads::player_action_context(&self.store, campaign_id).await.map_err(coded) },
        )
    }

    fn player_action_response_context<'a>(
        &'a self,
        campaign_id: &'a str,
        response: &'a JsonMap,
    ) -> BoxFuture<'a, CodedResult<Vec<JsonMap>>> {
        Box::pin(async move {
            player_reads::player_action_response_context(&self.store, campaign_id, response)
                .await
                .map_err(coded)
        })
    }

    fn action_sequence_response_results<'a>(
        &'a self,
        session_id: &'a str,
        basis_message_id: &'a str,
    ) -> BoxFuture<'a, CodedResult<Vec<JsonMap>>> {
        Box::pin(async move {
            player_reads::action_sequence_response_results(&self.store, session_id, Some(basis_message_id))
                .await
                .map_err(coded)
        })
    }

    fn canonical_replay<'a>(
        &'a self,
        campaign_id: &'a str,
        acknowledged_sequence: i64,
    ) -> BoxFuture<'a, CodedResult<CanonicalReplay>> {
        Box::pin(async move {
            player_reads::canonical_replay(
                &self.store,
                self.store.narration(),
                campaign_id,
                acknowledged_sequence,
            )
            .await
            .map_err(coded)
        })
    }

    fn ruleset_gm_operating_guide<'a>(
        &'a self,
        campaign_id: &'a str,
    ) -> BoxFuture<'a, CodedResult<Option<String>>> {
        Box::pin(async move {
            content_reads::ruleset_gm_operating_guide(&self.store, campaign_id).await.map_err(coded)
        })
    }

    fn model_context_snapshot<'a>(
        &'a self,
        campaign_id: &'a str,
    ) -> BoxFuture<'a, CodedResult<(CampaignDisplaySnapshot, Option<ModelDecisionBoundary>)>> {
        Box::pin(
            async move { snapshots::model_context_snapshot(&self.store, campaign_id).await.map_err(coded) },
        )
    }

    fn campaign_knowledge_catalog<'a>(&'a self, session_id: &'a str) -> BoxFuture<'a, CodedResult<JsonMap>> {
        Box::pin(async move {
            content_reads::campaign_knowledge_catalog(&self.store, session_id).await.map_err(coded)
        })
    }

    fn package_text_file<'a>(
        &'a self,
        package_id: &'a str,
        version: &'a str,
        sha256: &'a str,
        path: &'a str,
        maximum_bytes: i64,
    ) -> BoxFuture<'a, CodedResult<String>> {
        Box::pin(async move {
            self.store
                .package_text_file(package_id, version, sha256, path, maximum_bytes)
                .await
                .map_err(coded)
        })
    }

    fn snapshot<'a>(&'a self, campaign_id: &'a str) -> BoxFuture<'a, CodedResult<CampaignSnapshot>> {
        Box::pin(async move { snapshots::snapshot(&self.store, campaign_id).await.map_err(coded) })
    }

    fn campaign_construction_basis<'a>(
        &'a self,
        campaign_id: &'a str,
    ) -> BoxFuture<'a, CodedResult<CampaignConstructionBasis>> {
        Box::pin(
            async move { guided::campaign_construction_basis(&self.store, campaign_id).await.map_err(coded) },
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Read;

    /// A real catalog tool (Riichi's, from the participant profile) projected for the model: the
    /// fields the provider projection reads carry over, and the rest keep their defaults.
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn a_catalog_tool_projects_its_model_fields_and_keeps_the_defaults() {
        let folder = std::env::temp_dir().join(format!("sylvia-owner-tool-{}", std::process::id()));
        if let Err(error) = std::fs::remove_dir_all(&folder) {
            assert_eq!(
                error.kind(),
                std::io::ErrorKind::NotFound,
                "cannot clear the old work folder {}: {error}",
                folder.display()
            );
        }
        std::fs::create_dir_all(&folder).unwrap();
        let database = folder.join("authority.sqlite3");
        let source = crate::util::source_root()
            .join("crates/sylvia-store/tests/vectors/stored_authority/cast.sqlite3.gz");
        let mut bytes = Vec::new();
        flate2::read::GzDecoder::new(std::fs::File::open(source).unwrap()).read_to_end(&mut bytes).unwrap();
        std::fs::write(&database, bytes).unwrap();
        let path = database.clone();
        let (store, thread) =
            StoreHandle::spawn(move || sylvia_store::AuthorityStore::open_for_application_owner(&path, None))
                .unwrap();
        let tools =
            sylvia_app::authority::content_reads::catalog(&store, "campaign-table", false).await.unwrap();
        let tool = tools.into_iter().next().expect("Riichi's DM campaign admits its tools");
        let projected = model_tool(tool.clone());
        let mut want = ModelMechanicsTool::new(
            tool.package_id.clone(),
            tool.package_version.clone(),
            tool.package_sha256.clone(),
            tool.name.clone(),
            tool.capability.clone(),
            tool.input_schema.clone(),
        );
        want.description = tool.description.clone();
        want.model_behavior = tool.model_behavior;
        assert_eq!(projected, want);
        assert_eq!(projected.category, "mechanic");
        assert_eq!(projected.parameter_derivations, None);
        drop(store);
        tokio::task::spawn_blocking(move || thread.join()).await.unwrap();
        let _ = std::fs::remove_dir_all(folder);
    }

    /// Every `OpeningReads` method answers exactly what the read it wraps answers (those are proven
    /// against Python by their own vectors), on the session-page profile.
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn the_opening_reads_are_the_owner_reads() {
        let folder = std::env::temp_dir().join(format!("sylvia-owner-reads-{}", std::process::id()));
        // A folder left by an earlier run must go, or its files would look like this run's.
        if let Err(error) = std::fs::remove_dir_all(&folder) {
            assert_eq!(
                error.kind(),
                std::io::ErrorKind::NotFound,
                "cannot clear the old work folder {}: {error}",
                folder.display()
            );
        }
        std::fs::create_dir_all(&folder).unwrap();
        let database = folder.join("authority.sqlite3");
        let source = crate::util::source_root()
            .join("crates/sylvia-store/tests/vectors/session_pages/seed.sqlite3.gz");
        let mut bytes = Vec::new();
        flate2::read::GzDecoder::new(std::fs::File::open(source).unwrap()).read_to_end(&mut bytes).unwrap();
        std::fs::write(&database, bytes).unwrap();
        let path = database.clone();
        let (store, thread) =
            StoreHandle::spawn(move || sylvia_store::AuthorityStore::open_for_application_owner(&path, None))
                .unwrap();
        let reads = OwnerReads::new(store.clone());
        let campaign = "campaign-story";
        let text = |value: &dyn std::fmt::Debug| format!("{value:?}");
        assert_eq!(
            text(&reads.snapshot(campaign).await),
            text(&snapshots::snapshot(&store, campaign).await.map_err(coded))
        );
        assert_eq!(
            text(&reads.model_context_snapshot(campaign).await),
            text(&snapshots::model_context_snapshot(&store, campaign).await.map_err(coded))
        );
        assert_eq!(
            text(&reads.canonical_replay(campaign, 0).await),
            text(
                &player_reads::canonical_replay(&store, store.narration(), campaign, 0).await.map_err(coded)
            )
        );
        assert_eq!(
            text(&reads.player_action_context(campaign).await),
            text(&player_reads::player_action_context(&store, campaign).await.map_err(coded))
        );
        let basis = JsonMap::new();
        assert_eq!(
            text(&reads.player_action_response_context(campaign, &basis).await),
            text(
                &player_reads::player_action_response_context(&store, campaign, &basis).await.map_err(coded)
            )
        );
        assert_eq!(
            text(&reads.action_sequence_response_results("dm-1", "u-1").await),
            text(
                &player_reads::action_sequence_response_results(&store, "dm-1", Some("u-1"))
                    .await
                    .map_err(coded)
            )
        );
        assert_eq!(
            text(&reads.ruleset_gm_operating_guide(campaign).await),
            text(&content_reads::ruleset_gm_operating_guide(&store, campaign).await.map_err(coded))
        );
        assert_eq!(
            text(&reads.model_context_owner(campaign).await),
            text(&content_reads::model_context_owner(&store, campaign).await.map_err(coded))
        );
        assert_eq!(
            text(&reads.campaign_knowledge_catalog("dm-1").await),
            text(&content_reads::campaign_knowledge_catalog(&store, "dm-1").await.map_err(coded))
        );
        assert_eq!(
            text(&reads.session_context("dm-1").await),
            text(&store.session_context("dm-1").await.map_err(coded))
        );
        assert_eq!(
            text(&reads.campaign_construction_basis(campaign).await),
            text(&guided::campaign_construction_basis(&store, campaign).await.map_err(coded))
        );
        let refused =
            reads.package_text_file("synthetic.actions", "1.0.0", &"0".repeat(64), "missing.md", 64).await;
        assert_eq!(
            text(&refused),
            text(
                &store
                    .package_text_file("synthetic.actions", "1.0.0", &"0".repeat(64), "missing.md", 64)
                    .await
                    .map_err(coded)
            )
        );
        assert_eq!(reads.catalog(campaign).await.unwrap(), Vec::<ModelMechanicsTool>::new());
        assert_eq!(
            text(&reads.campaign_index().await),
            text(&store.call(|store| store.campaign_index_entries()).await.map_err(coded))
        );
        // The audit sink and the flag read the handle's one shared state.
        assert!(!reads.admin_full_audit_enabled().unwrap());
        store.narration().set_admin_full_audit(true);
        reads.record_admin_full_audit(serde_json::json!({"kind": "defect"})).unwrap();
        assert!(reads.admin_full_audit_enabled().unwrap());
        assert_eq!(store.narration().inspector_traces(), vec![serde_json::json!({"kind": "defect"})]);
        drop((reads, store));
        tokio::task::spawn_blocking(move || thread.join()).await.unwrap();
        let _ = std::fs::remove_dir_all(folder);
    }
}
