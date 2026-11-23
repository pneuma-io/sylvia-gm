//! The owner's live narration state: `AuthorityOwner._attempts` and `_pending_tools`, under
//! `_narration_lock`, and `_provisional_projection`, which projects them for
//! `canonical_replay`; and, under the same lock as in Python, the admin full audit state
//! (`_admin_full_audit_enabled`, `_inspector_trace`, `_pending_audit_usage`) with
//! `admin_full_audit_enabled`, `set_admin_full_audit`, `record_admin_full_audit` and
//! `_credit_audit_usage`; and `_mechanics_diagnostics`, the bounded safe metadata the turn keeps
//! for each mechanics rejection, with `record_mechanics_diagnostic` and
//! `admin_full_audit_status`. One instance per owner, shared by
//! [`crate::StoreHandle::narration`].
//!
//! **Narration is not ported yet.** When it is, its commands write here: each narration attempt
//! into [`NarrationInner::attempts`] (`begin_narration_attempt`, then its prose buffer and
//! terminal state) and each tool awaiting its result into [`NarrationInner::pending_tools`]
//! (`_execute_mechanic` before its commit, removed after). Until then no Rust owner has a live
//! attempt or pending tool, so the projection's true answer is empty, as Python's is with none.
//! `sylvia-app`'s `tests/player_reads.rs` proves the projection from a state that holds both.

use crate::error::{StoreError, StoreResult};
use std::sync::Mutex;
use sylvia_base::{Json, JsonMap};
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
    /// `_admin_full_audit_enabled`.
    pub admin_full_audit_enabled: bool,
    /// `_inspector_trace`.
    pub inspector_trace: Vec<Json>,
    /// `_pending_audit_usage`, by exchange identity, in insertion order.
    pub pending_audit_usage: Vec<(String, Json)>,
    /// `_mechanics_diagnostics`: the last [`MECHANICS_DIAGNOSTICS_KEPT`], oldest first.
    pub mechanics_diagnostics: Vec<Json>,
}

/// How many safe mechanics diagnostics the owner keeps (`del ...[:-200]`).
pub const MECHANICS_DIAGNOSTICS_KEPT: usize = 200;

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

    /// `admin_full_audit_enabled()`.
    pub fn admin_full_audit_enabled(&self) -> bool {
        self.with(|inner| inner.admin_full_audit_enabled)
    }

    /// `set_admin_full_audit(enabled)`: a change clears what was captured.
    pub fn set_admin_full_audit(&self, enabled: bool) -> JsonMap {
        self.with(|inner| {
            if inner.admin_full_audit_enabled != enabled {
                inner.inspector_trace.clear();
                inner.pending_audit_usage.clear();
            }
            inner.admin_full_audit_enabled = enabled;
            let mut reply = JsonMap::new();
            reply.insert("enabled".into(), enabled.into());
            reply.insert("trace_count".into(), inner.inspector_trace.len().into());
            reply
        })
    }

    /// `record_admin_full_audit(payload)`: capture a private exchange or defect only while
    /// explicit admin mode is on. Late usage is credited to its saved response, or held until
    /// the response is saved; a response takes held usage with it.
    pub fn record_admin_full_audit(&self, payload: Json) -> StoreResult<()> {
        self.with(|inner| {
            if !inner.admin_full_audit_enabled {
                return Ok(());
            }
            let mut payload = payload;
            if let Json::Object(map) = &payload {
                if let Some(Json::String(exchange_id)) = map.get("exchange_id") {
                    let exchange_id = exchange_id.clone();
                    match map.get("kind").and_then(Json::as_str) {
                        Some("provider_late_usage") => {
                            let usage = map
                                .get("usage")
                                .cloned()
                                .ok_or_else(|| StoreError::Other("KeyError: 'usage'".into()))?;
                            credit_audit_usage(inner, &exchange_id, usage);
                            return Ok(());
                        }
                        Some("provider_response" | "provider_stream_response") => {
                            if let Some(index) =
                                inner.pending_audit_usage.iter().position(|(id, _)| *id == exchange_id)
                            {
                                let (_, usage) = inner.pending_audit_usage.remove(index);
                                // `{**payload, "usage": usage}`: an existing key keeps its place.
                                let mut merged = map.clone();
                                merged.insert("usage".into(), usage);
                                payload = Json::Object(merged);
                            }
                        }
                        _ => {}
                    }
                }
            }
            inner.inspector_trace.push(payload);
            Ok(())
        })
    }

    /// `record_mechanics_diagnostic(...)`, after its caller built the payload: keep it, and only
    /// the last [`MECHANICS_DIAGNOSTICS_KEPT`]. Kept whether or not Full Audit is on, and a Full
    /// Audit change leaves them.
    pub fn record_mechanics_diagnostic(&self, diagnostic: JsonMap) {
        self.with(|inner| {
            inner.mechanics_diagnostics.push(Json::Object(diagnostic));
            let kept = inner.mechanics_diagnostics.len();
            if kept > MECHANICS_DIAGNOSTICS_KEPT {
                inner.mechanics_diagnostics.drain(..kept - MECHANICS_DIAGNOSTICS_KEPT);
            }
        })
    }

    /// The kept diagnostics, oldest first (`tuple(self._mechanics_diagnostics)`).
    pub fn mechanics_diagnostics(&self) -> Vec<Json> {
        self.with(|inner| inner.mechanics_diagnostics.clone())
    }

    /// `admin_full_audit_status()`: whether Full Audit is on, how many traces it holds, and the
    /// kept diagnostics, read under one lock.
    pub fn admin_full_audit_status(&self) -> JsonMap {
        self.with(|inner| {
            let mut status = JsonMap::new();
            status.insert("enabled".into(), inner.admin_full_audit_enabled.into());
            status.insert("trace_count".into(), inner.inspector_trace.len().into());
            status.insert(
                "safe_mechanics_diagnostics".into(),
                Json::Array(inner.mechanics_diagnostics.clone()),
            );
            status
        })
    }

    /// The captured traces, oldest first (`tuple(self._inspector_trace)`).
    pub fn inspector_traces(&self) -> Vec<Json> {
        self.with(|inner| inner.inspector_trace.clone())
    }
}

/// `_credit_audit_usage(exchange_id, usage)`: replace only the usage of the exchange's latest
/// saved response, keeping its place; with only its request saved, hold the usage instead.
fn credit_audit_usage(inner: &mut NarrationInner, exchange_id: &str, usage: Json) {
    for index in (0..inner.inspector_trace.len()).rev() {
        let Json::Object(trace) = &inner.inspector_trace[index] else { continue };
        if trace.get("exchange_id") != Some(&Json::from(exchange_id)) {
            continue;
        }
        match trace.get("kind").and_then(Json::as_str) {
            Some("provider_response" | "provider_stream_response") => {
                let mut credited = trace.clone();
                credited.insert("usage".into(), usage);
                inner.inspector_trace[index] = Json::Object(credited);
                return;
            }
            Some("provider_request") => {
                // A background close can report before the stream wrapper saves its response.
                match inner.pending_audit_usage.iter_mut().find(|(id, _)| id == exchange_id) {
                    Some(entry) => entry.1 = usage,
                    None => inner.pending_audit_usage.push((exchange_id.to_owned(), usage)),
                }
                return;
            }
            _ => {}
        }
    }
}
