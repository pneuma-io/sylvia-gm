//! Lanes: the owner's in-process per-key work locks.
//!
//! Python keeps two lane registries, both `dict[str, threading.RLock]` filled by `setdefault`
//! under a plain lock and never pruned:
//! - `AuthorityOwner._lane(key)`, keyed by campaign id and by `builder-chat:<session id>`;
//! - `SessionApplication._lane(session_id)`, keyed by session id.
//!
//! [`LaneRegistry`] is the one Rust type for both, and for lane 4's session loop. A lane is a
//! `tokio::sync::Mutex`, so its [`LaneGuard`] is owned, `Send` and can be held across `.await`.
//! Acquisition is async ([`LaneRegistry::acquire`]), non-blocking ([`LaneRegistry::try_acquire`],
//! Python's `acquire(blocking=False)`) or thread-blocking for callers outside the runtime
//! ([`LaneRegistry::acquire_blocking`]).
//!
//! **Re-entrancy.** Python's lanes are `RLock`s: a thread that holds a lane takes it again
//! without waiting. A tokio mutex has no owning thread, so re-acquiring would deadlock. Where
//! Python re-enters, the Rust caller passes the guard it holds down as proof instead
//! ([`LaneRegistry::acquire_unless_held`], [`LaneGuard::is_for`]). These are the nested
//! acquisitions in the frozen Python, found by following calls from every lane holder:
//!
//! 1. `action_sequences.compose` holds the campaign lane and calls `_execute`, which takes it
//!    again.
//! 2. `action_sequences._execute` holds the campaign lane, and may call `read` (the
//!    recovery path), which takes `owner._lane(queue["campaign_id"])`. That key is checked against
//!    the held campaign only after `read`, so it may differ: `acquire_unless_held` reuses the
//!    guard only when the key matches, and otherwise takes the other lane.
//! 3. `action_sequences._execute` → `AuthorityOwner._player_action_in_owned_work` →
//!    `_execute_action`, which takes `_lane(command.campaign_id)` again.
//! 4. `AuthorityOwner.builder_session_work` holds `builder-chat:<id>` for a campaign-less
//!    Builder chat across its `yield`. Its holders append messages through
//!    `append_agent_message`, whose `_session_campaign_work` takes the same lane again. Those
//!    holders are `SessionLoop.run_builder` (including the `persist_builder_turn` callback),
//!    `SessionLoop.run_compaction` for a Builder session, and
//!    `interfaces.commands.builder.handle_ingest_builder_sources`.
//!
//! The session registry has no nested acquisition: `SessionApplication` methods take their
//! lane and call only the store, and nothing outside `SessionApplication` takes its lanes.
//! `delete_builder_chat` and `set_builder_chat_archived` take `builder-chat:<id>`
//! non-blocking. Under an `RLock` that would succeed on a thread that already holds it, but no
//! Python path calls either while holding that lane, so [`LaneRegistry::try_acquire`] does not
//! re-enter. Ports of 1 to 4 (the action-sequence family, `append_agent_message` and lane 4's
//! session loop) take the held guard as a parameter.

use std::collections::HashMap;
use std::sync::{Arc, Mutex, PoisonError};
use tokio::sync::{Mutex as LaneLock, OwnedMutexGuard};

#[derive(Default)]
struct Lanes {
    lanes: Mutex<HashMap<String, Arc<LaneLock<()>>>>,
}

/// A registry of per-key lanes (Python's `_lanes` with `_lanes_lock` and `_lane`). Clones are
/// cheap and share the same lanes.
#[derive(Clone, Default)]
pub struct LaneRegistry {
    lanes: Arc<Lanes>,
}

impl std::fmt::Debug for LaneRegistry {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let count = self.lanes.lanes.lock().unwrap_or_else(PoisonError::into_inner).len();
        formatter.debug_struct("LaneRegistry").field("lanes", &count).finish()
    }
}

/// A held lane. Dropping it releases the lane (Python's `release()` or the end of the `with`).
pub struct LaneGuard {
    lanes: Arc<Lanes>,
    key: String,
    _held: OwnedMutexGuard<()>,
}

impl std::fmt::Debug for LaneGuard {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.debug_struct("LaneGuard").field("key", &self.key).finish()
    }
}

impl LaneGuard {
    /// The key of the held lane.
    pub fn key(&self) -> &str {
        &self.key
    }

    /// Whether this guard holds `key` in `registry`: the proof a caller re-entering a lane
    /// passes down in place of re-acquiring it.
    pub fn is_for(&self, registry: &LaneRegistry, key: &str) -> bool {
        Arc::ptr_eq(&self.lanes, &registry.lanes) && self.key == key
    }
}

/// A lane a caller holds for one piece of work: either the guard its caller already held
/// (Python's re-entry), or one it took itself.
#[derive(Debug)]
pub enum LaneHold<'held> {
    /// The caller's guard, reused.
    Reused(&'held LaneGuard),
    /// Taken here; released when this is dropped.
    Acquired(LaneGuard),
}

impl LaneHold<'_> {
    /// The guard either way.
    pub fn guard(&self) -> &LaneGuard {
        match self {
            Self::Reused(guard) => guard,
            Self::Acquired(guard) => guard,
        }
    }
}

impl LaneRegistry {
    /// An empty registry.
    pub fn new() -> Self {
        Self::default()
    }

    /// `_lane(key)`: the key's lane, created on first use and kept, as Python keeps it.
    fn lane(&self, key: &str) -> Arc<LaneLock<()>> {
        let mut lanes = self.lanes.lanes.lock().unwrap_or_else(PoisonError::into_inner);
        Arc::clone(lanes.entry(key.to_owned()).or_default())
    }

    fn guard(&self, key: &str, held: OwnedMutexGuard<()>) -> LaneGuard {
        LaneGuard { lanes: Arc::clone(&self.lanes), key: key.to_owned(), _held: held }
    }

    /// `with _lane(key):`, waiting for the lane.
    pub async fn acquire(&self, key: &str) -> LaneGuard {
        let held = self.lane(key).lock_owned().await;
        self.guard(key, held)
    }

    /// `_lane(key).acquire(blocking=False)`: the lane if it is free now, else `None`.
    pub fn try_acquire(&self, key: &str) -> Option<LaneGuard> {
        let held = self.lane(key).try_lock_owned().ok()?;
        Some(self.guard(key, held))
    }

    /// `with _lane(key):` for a caller on a plain thread, blocking it until the lane is free.
    /// It panics if called from within an async runtime, like `blocking_lock`.
    pub fn acquire_blocking(&self, key: &str) -> LaneGuard {
        let held = self.lane(key).blocking_lock_owned();
        self.guard(key, held)
    }

    /// `with _lane(key):` where Python may already hold the lane: reuse `held` when it is
    /// this registry's lane for `key`, else wait for the lane.
    pub async fn acquire_unless_held<'held>(
        &self,
        held: Option<&'held LaneGuard>,
        key: &str,
    ) -> LaneHold<'held> {
        match held {
            Some(guard) if guard.is_for(self, key) => LaneHold::Reused(guard),
            _ => LaneHold::Acquired(self.acquire(key).await),
        }
    }
}

/// The owner's two lane registries, which every clone of the store handle shares.
#[derive(Debug, Clone, Default)]
pub struct OwnerLanes {
    /// `AuthorityOwner._lanes`: campaign ids and `builder-chat:<session id>`.
    pub owner: LaneRegistry,
    /// `SessionApplication._lanes`: session ids.
    pub sessions: LaneRegistry,
}

/// `f"builder-chat:{session_id}"`: the owner lane of a standalone Builder chat.
pub fn builder_chat_lane(session_id: &str) -> String {
    format!("builder-chat:{session_id}")
}
