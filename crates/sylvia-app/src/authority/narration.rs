//! The owner's live narration state: `AuthorityOwner._attempts` and `_pending_tools`, under
//! `_narration_lock`, and `_provisional_projection`, which projects them for
//! `canonical_replay`.
//!
//! **Narration is not ported yet.** When it is, its commands write here: each narration attempt
//! into [`NarrationInner::attempts`] (`begin_narration_attempt`, then its prose buffer and
//! terminal state) and each tool awaiting its result into [`NarrationInner::pending_tools`]
//! (`_execute_mechanic` before its commit, removed after). Until then no Rust owner has a live
//! attempt or pending tool, so the projection's true answer is empty, as Python's is with none.
//! `tests/player_reads.rs` proves the projection from a state that holds both.

use std::sync::Mutex;
use sylvia_domain::authority::{PlayerProjection, PlayerProjectionKind, ProseChannel};

/// `_NarrationAttempt`: the fields `_provisional_projection` reads. The narration port adds the
/// rest (its cancel hook, response blocks, ordinal, deadlines, interruption and retry links).
#[derive(Debug, Clone, PartialEq)]
pub struct NarrationAttempt {
    pub campaign_id: String,
    pub attempt_id: String,
    pub turn_id: String,
    pub last_action_id: Option<String>,
    pub channel: Option<ProseChannel>,
    pub buffer: String,
    pub terminal: bool,
}

/// What `_narration_lock` guards, each in insertion order as Python's dicts keep it.
#[derive(Debug, Default)]
pub struct NarrationInner {
    /// `_attempts`, keyed by `(campaign_id, session_id)`.
    pub attempts: Vec<((String, String), NarrationAttempt)>,
    /// `_pending_tools`, keyed by `(campaign_id, action_id)`.
    pub pending_tools: Vec<((String, String), PlayerProjection)>,
}

/// The owner's narration state, one per owner.
#[derive(Debug, Default)]
pub struct NarrationState {
    inner: Mutex<NarrationInner>,
}

impl NarrationState {
    pub fn new() -> Self {
        Self::default()
    }

    /// Run `f` under the narration lock.
    pub fn with<R>(&self, f: impl FnOnce(&mut NarrationInner) -> R) -> R {
        let mut inner = self.inner.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
        f(&mut inner)
    }

    /// `_provisional_projection(campaign_id)`: the campaign's pending tools, then the prose of its
    /// live attempts that have a channel and a buffer.
    pub fn provisional_projection(&self, campaign_id: &str) -> Vec<PlayerProjection> {
        self.with(|inner| {
            let pending = inner
                .pending_tools
                .iter()
                .filter(|((item_campaign_id, _), _)| item_campaign_id == campaign_id)
                .map(|(_, item)| item.clone());
            let prose = inner
                .attempts
                .iter()
                .map(|(_, attempt)| attempt)
                .filter(|attempt| {
                    attempt.campaign_id == campaign_id
                        && !attempt.terminal
                        && !attempt.buffer.is_empty()
                        && attempt.channel.is_some()
                })
                .map(|attempt| {
                    let think = attempt.channel == Some(ProseChannel::SafeThink);
                    let mut payload = sylvia_base::JsonMap::new();
                    payload.insert("text".into(), attempt.buffer.as_str().into());
                    PlayerProjection {
                        kind: if think { PlayerProjectionKind::Think } else { PlayerProjectionKind::Talk },
                        sequence: None,
                        text: Some(attempt.buffer.clone()),
                        safe_payload: payload,
                        collapsed_by_default: think,
                        provisional: true,
                        action_id: None,
                        attempt_id: Some(attempt.attempt_id.clone()),
                    }
                });
            pending.chain(prose).collect()
        })
    }
}
