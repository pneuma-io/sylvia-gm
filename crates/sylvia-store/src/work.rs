//! `AuthorityOwner`'s campaign work registry: `_campaign_work_counts`, `_campaign_transitions`
//! and `_direct_action_campaigns`, kept under `_work_condition`.
//!
//! Python's `_begin_campaign_work` holds `_work_condition` while it reads the campaign, so
//! admission is atomic against a package transition being reserved. Here an async admission
//! lock gives the same: [`CampaignWork::begin`] holds it across the read, and the reservation
//! takes it too. Counts themselves sit behind a plain lock, so a guard can finish its work on
//! drop.
//!
//! Python reads the whole campaign snapshot (`self._store.snapshot(campaign_id)`) only to test its
//! lifecycle. Until the snapshot family is ported, the read here is the campaign row
//! ([`crate::store::AuthorityStore::campaign_lifecycle`]): the same refusal for a missing
//! campaign and the same lifecycle, without the snapshot's other reads.

use crate::error::{StoreError, StoreResult};
use std::collections::{HashMap, HashSet};
use std::sync::{Arc, Mutex, PoisonError};
use sylvia_domain::authority::{validate_authority_identity, LifecycleState};

#[derive(Debug, Default)]
struct State {
    counts: HashMap<String, usize>,
    transitions: HashSet<String>,
    direct_actions: HashSet<String>,
}

/// The registry; clones share it, as every caller of Python's one `AuthorityOwner` does.
#[derive(Debug, Clone, Default)]
pub struct CampaignWork {
    state: Arc<Mutex<State>>,
    admission: Arc<tokio::sync::Mutex<()>>,
}

/// One registered piece of campaign work: `_finish_campaign_work` runs when it drops.
#[derive(Debug)]
pub struct CampaignWorkGuard {
    state: Arc<Mutex<State>>,
    campaign_id: String,
}

impl Drop for CampaignWorkGuard {
    fn drop(&mut self) {
        let mut state = self.state.lock().unwrap_or_else(PoisonError::into_inner);
        match state.counts.get(&self.campaign_id).copied() {
            Some(count) if count > 1 => {
                state.counts.insert(self.campaign_id.clone(), count - 1);
            }
            _ => {
                state.counts.remove(&self.campaign_id);
            }
        }
    }
}

/// One player action's direct work (`_direct_action_work`): when it drops, the campaign stops
/// resolving a player action, then its campaign work finishes, in Python's order.
#[derive(Debug)]
pub struct DirectActionGuard {
    _work: CampaignWorkGuard,
    state: Arc<Mutex<State>>,
    campaign_id: String,
}

impl Drop for DirectActionGuard {
    fn drop(&mut self) {
        // `_work` drops after this, finishing the campaign work.
        self.state.lock().unwrap_or_else(PoisonError::into_inner).direct_actions.remove(&self.campaign_id);
    }
}

/// One reserved campaign transition (`_reserve_campaign_transition`):
/// `_release_campaign_transition` runs when it drops.
#[derive(Debug)]
pub struct TransitionGuard {
    state: Arc<Mutex<State>>,
    campaign_id: String,
}

impl Drop for TransitionGuard {
    fn drop(&mut self) {
        let mut state = self.state.lock().unwrap_or_else(PoisonError::into_inner);
        state.transitions.remove(&self.campaign_id);
    }
}

fn rejected(message: &str, code: &str) -> StoreError {
    crate::actions::rejected(message, code)
}

impl CampaignWork {
    /// `_begin_campaign_work(campaign_id)`: refused while a player action resolves, while a
    /// package transition is reserved or when the campaign is not active; `lifecycle` reads the
    /// campaign's lifecycle.
    pub async fn begin<F, Fut>(&self, campaign_id: &str, lifecycle: F) -> StoreResult<CampaignWorkGuard>
    where
        F: FnOnce() -> Fut,
        Fut: std::future::Future<Output = StoreResult<LifecycleState>>,
    {
        let _admission = self.admission.lock().await;
        self.begin_admitted(campaign_id, lifecycle).await
    }

    /// `_begin_campaign_work`'s body, with the admission lock (`_work_condition`) already held.
    async fn begin_admitted<F, Fut>(&self, campaign_id: &str, lifecycle: F) -> StoreResult<CampaignWorkGuard>
    where
        F: FnOnce() -> Fut,
        Fut: std::future::Future<Output = StoreResult<LifecycleState>>,
    {
        if validate_authority_identity(campaign_id, "Campaign identity", false).is_err() {
            return Err(rejected("Campaign authority identity is invalid", "invalid_authority_identity"));
        }
        {
            let state = self.state.lock().unwrap_or_else(PoisonError::into_inner);
            if state.direct_actions.contains(campaign_id) {
                return Err(rejected("A player action is still resolving", "direct_action_in_progress"));
            }
            if state.transitions.contains(campaign_id) {
                return Err(rejected(
                    "Campaign package transition blocks new work",
                    "package_transition_in_progress",
                ));
            }
        }
        if lifecycle().await? != LifecycleState::Active {
            return Err(rejected("Campaign lifecycle blocks new work", "lifecycle_inactive"));
        }
        let mut state = self.state.lock().unwrap_or_else(PoisonError::into_inner);
        *state.counts.entry(campaign_id.to_owned()).or_insert(0) += 1;
        Ok(CampaignWorkGuard { state: Arc::clone(&self.state), campaign_id: campaign_id.to_owned() })
    }

    /// `_direct_action_work(campaign_id)`: a player click is admitted only while the campaign is
    /// idle (no queueing behind GM work and no interrupting it), as campaign work that also marks
    /// the campaign as resolving a player action, so new campaign work is refused until it ends.
    pub async fn begin_direct_action<F, Fut>(
        &self,
        campaign_id: &str,
        lifecycle: F,
    ) -> StoreResult<DirectActionGuard>
    where
        F: FnOnce() -> Fut,
        Fut: std::future::Future<Output = StoreResult<LifecycleState>>,
    {
        // Python holds `_work_condition` across the idle check and `_begin_campaign_work`.
        let _admission = self.admission.lock().await;
        if self.count(campaign_id) != 0 {
            return Err(rejected(
                "Player actions are unavailable while campaign work is running",
                "campaign_work_in_progress",
            ));
        }
        let work = self.begin_admitted(campaign_id, lifecycle).await?;
        self.state
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .direct_actions
            .insert(campaign_id.to_owned());
        Ok(DirectActionGuard {
            _work: work,
            state: Arc::clone(&self.state),
            campaign_id: campaign_id.to_owned(),
        })
    }

    /// `_reserve_campaign_transition(campaign_id)`: refused while another transition is reserved
    /// or while any work is registered on the campaign; it is never queued. It takes the
    /// admission lock, as [`Self::begin`] does, so work cannot be admitted halfway through.
    pub async fn reserve_transition(&self, campaign_id: &str) -> StoreResult<TransitionGuard> {
        let _admission = self.admission.lock().await;
        let mut state = self.state.lock().unwrap_or_else(PoisonError::into_inner);
        if state.transitions.contains(campaign_id) {
            return Err(rejected("Campaign package transition is already in progress", "command_rejected"));
        }
        if state.counts.get(campaign_id).copied().unwrap_or(0) != 0 {
            return Err(rejected(
                "Campaign has active work; activation is not queued",
                "campaign_work_in_progress",
            ));
        }
        state.transitions.insert(campaign_id.to_owned());
        Ok(TransitionGuard { state: Arc::clone(&self.state), campaign_id: campaign_id.to_owned() })
    }

    /// `_ensure_campaign_not_transitioning(campaign_id)`: refused while a package transition is
    /// reserved for the campaign.
    pub fn ensure_not_transitioning(&self, campaign_id: &str) -> StoreResult<()> {
        if self.state.lock().unwrap_or_else(PoisonError::into_inner).transitions.contains(campaign_id) {
            return Err(rejected(
                "Campaign package transition blocks admission",
                "package_transition_in_progress",
            ));
        }
        Ok(())
    }

    /// How much work is registered on a campaign (`_campaign_work_counts.get(id, 0)`).
    pub fn count(&self, campaign_id: &str) -> usize {
        self.state
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .counts
            .get(campaign_id)
            .copied()
            .unwrap_or(0)
    }
}
