//! `application/action_sequences.py`: durable player intentions around declared controls, whose
//! native transactions stay authoritative.
//!
//! - **The readers a GM response's context needs** (`response_results`, `response_reservations`,
//!   with `_bound_queues` and `_committed_ids`): ported by lane 3, reviewed by lane 2.
//! - **Reading, composing and executing a queue** (`read` as [`action_sequence`],
//!   [`compose_action_sequence`], [`execute_action_sequence`], [`last_attempt_commit`]): lane 2's,
//!   generic over [`SequenceOwner`], the owner operations Python calls, which lane 3's authority
//!   implements. Every status move, consent hash, projection, recovery, staleness and pause rule
//!   is Python's.
//!
//! A saved queue is the content of an `action_sequence` evidence message, as Python's own
//! sequence code wrote it. A field Python indexes that is missing is its `KeyError`, an internal
//! failure here too.
//!
//! One divergence is left, reachable only through a hand-corrupted queue: Python's
//! `latest[content["sequence_id"]]`, `bases = {...}` and `... in bases` raise `TypeError` for an
//! unhashable value (a list or object as a sequence or basis identity); here those are compared
//! by Python's `==` instead.

use crate::player_actions::{player_action_origins, player_action_request_matches, validate_player_control};
use std::collections::BTreeSet;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::WriterRole;
use sylvia_domain::authority::{validate_authority_identity, validate_authority_identity_value};
use sylvia_domain::package_library::canonical_json_bytes;
use sylvia_domain::player_actions::{player_control_origin, PLAYER_ACTION_ORIGIN};
use sylvia_domain::pyops::{py_eq, py_truthy, py_type_name};
use sylvia_domain::session::{SessionContext, SessionMessageKind};
use sylvia_store::{AuthorityStore, StoreError, StoreResult};

/// `QueueStatus.COMPLETED`.
const QUEUE_COMPLETED: &str = "completed";
/// `StepStatus.COMMITTED`.
const STEP_COMMITTED: &str = "committed";

fn key_error(key: &str) -> StoreError {
    StoreError::Other(format!("KeyError: '{key}'"))
}

fn item<'a>(map: &'a JsonMap, key: &str) -> StoreResult<&'a Json> {
    map.get(key).ok_or_else(|| key_error(key))
}

/// Python's truthiness of a JSON value.
fn truthy(value: Option<&Json>) -> bool {
    match value {
        None | Some(Json::Null) => false,
        Some(Json::Bool(flag)) => *flag,
        Some(Json::Number(number)) => number.as_f64().is_some_and(|value| value != 0.0),
        Some(Json::String(text)) => !text.is_empty(),
        Some(Json::Array(items)) => !items.is_empty(),
        Some(Json::Object(map)) => !map.is_empty(),
    }
}

/// `_bound_queues(context, basis_message_id=..., attempt_id=...)`: the latest saved version of
/// every queue bound to a narration basis, by `sequence_id`, in first-saved order.
pub(crate) fn bound_queues(
    context: &SessionContext,
    basis_message_id: Option<&str>,
    attempt_id: Option<&str>,
) -> StoreResult<Vec<JsonMap>> {
    let mut latest: Vec<(Json, JsonMap)> = Vec::new();
    for message in &context.messages {
        let content = message.content();
        if message.kind() != SessionMessageKind::Evidence
            || content.get("kind") != Some(&Json::from("action_sequence"))
            || !truthy(content.get("narration_basis_message_id"))
        {
            continue;
        }
        if basis_message_id
            .is_some_and(|basis| content.get("narration_basis_message_id") != Some(&Json::from(basis)))
        {
            continue;
        }
        if attempt_id.is_some_and(|attempt| content.get("narration_attempt_id") != Some(&Json::from(attempt)))
        {
            continue;
        }
        let sequence_id = item(content, "sequence_id")?.clone();
        match latest.iter_mut().find(|(known, _)| sylvia_domain::pyops::py_eq(known, &sequence_id)) {
            Some(entry) => entry.1 = content.clone(),
            None => latest.push((sequence_id, content.clone())),
        }
    }
    Ok(latest.into_iter().map(|(_, queue)| queue).collect())
}

/// `_committed_ids(owner, queue)`: the queue's steps whose saved player action committed with
/// exactly the origin the step's execution envelope asked for.
fn committed_ids(store: &AuthorityStore, queue: &JsonMap) -> StoreResult<Vec<String>> {
    let (campaign_id, operation_ids) = marker_request(queue)?;
    let ids: Vec<&str> = operation_ids.iter().map(String::as_str).collect();
    let markers = store.player_action_markers(&campaign_id, &ids, PLAYER_ACTION_ORIGIN)?;
    committed_from_markers(queue, &markers)
}

/// The campaign and operation identities `_committed_ids` asks the store's markers for, in
/// Python's order of evaluation.
fn marker_request(queue: &JsonMap) -> StoreResult<(String, Vec<String>)> {
    let steps = queue_steps(queue)?;
    let operation_ids: Vec<String> = steps
        .iter()
        .map(|step| Ok(sylvia_domain::pyops::py_str(item(step, "operation_id")?)))
        .collect::<StoreResult<_>>()?;
    let campaign_id = sylvia_domain::pyops::py_str(item(queue, "campaign_id")?);
    Ok((campaign_id, operation_ids))
}

fn queue_steps(queue: &JsonMap) -> StoreResult<Vec<&JsonMap>> {
    let Json::Array(steps) = item(queue, "steps")? else {
        return Err(StoreError::Other("TypeError: steps is not a list".into()));
    };
    steps
        .iter()
        .map(|step| {
            step.as_object().ok_or_else(|| StoreError::Other("TypeError: step is not an object".into()))
        })
        .collect()
}

/// `_committed_ids`' check of the markers the store answered for [`marker_request`].
fn committed_from_markers(queue: &JsonMap, markers: &[JsonMap]) -> StoreResult<Vec<String>> {
    let steps = queue_steps(queue)?;
    let (_, operation_ids) = marker_request(queue)?;
    let origins: Vec<(String, Json)> = markers
        .iter()
        .filter(|marker| marker.get("status") == Some(&Json::from(STEP_COMMITTED)))
        .map(|marker| (sylvia_domain::pyops::py_str(&marker["action_id"]), marker["origin"].clone()))
        .collect();
    let mut identities = Vec::new();
    for (step, operation_id) in steps.iter().zip(&operation_ids) {
        let Some(execution) = step.get("execution").filter(|value| truthy(Some(value))) else {
            continue;
        };
        // `origins` is a dict: of two committed markers for one action, the later one counts.
        let Some((_, stored)) = origins.iter().rev().find(|(action_id, _)| action_id == operation_id) else {
            continue;
        };
        // `envelope["source"]` on a truthy value that is not a mapping is Python's `TypeError`.
        let Json::Object(envelope) = execution else {
            return Err(StoreError::Other("TypeError: the step's execution is not a mapping".into()));
        };
        // Python's arguments, in its order, each as saved: the domain function validates the
        // source and supplies the control fields; the origin's first three items are then the raw
        // saved values (their keys already exist, so they keep their places).
        let source = item(envelope, "source")?;
        let expected_revision = item(envelope, "expected_revision")?.clone();
        let selection = envelope.get("selection").cloned().filter(|value| truthy(Some(value)));
        let session_id = item(queue, "session_id")?.clone();
        let mut request = player_control_origin(source, 0, &Json::Null, None).map_err(StoreError::from)?;
        request.insert("expected_revision".into(), expected_revision);
        request.insert("selection".into(), selection.unwrap_or_else(|| Json::Object(JsonMap::new())));
        request.insert("session_id".into(), session_id);
        if !player_action_request_matches(stored.as_object(), &request) {
            return Err(sylvia_domain::DomainError::command_rejected(
                "Saved sequence action identity is inconsistent",
            )
            .into());
        }
        identities.push(operation_id.clone());
    }
    Ok(identities)
}

/// `response_results(owner, session_id=..., basis_message_id=...)`: every queue bound to the
/// basis that committed actions, with those identities and whether the provider stopped. A
/// session that does not exist (a Start preflight's unsaved one) has none.
pub fn response_results(
    store: &AuthorityStore,
    session_id: &str,
    basis_message_id: Option<&str>,
) -> StoreResult<Vec<JsonMap>> {
    let context = match store.session_context(session_id) {
        Ok(context) => context,
        Err(error) if error.rejection().is_some_and(|coded| coded.code == "session_unavailable") => {
            return Ok(Vec::new())
        }
        Err(error) => return Err(error),
    };
    if context.session.role() != WriterRole::Dm || context.session.campaign_id().is_none() {
        return Ok(Vec::new());
    }
    let mut results = Vec::new();
    for queue in bound_queues(&context, basis_message_id, None)? {
        let identities = committed_ids(store, &queue)?;
        if !identities.is_empty() {
            let stopped = item(&queue, "status")? != &Json::from(QUEUE_COMPLETED);
            let mut result = queue.clone();
            result.insert(
                "committed_action_ids".into(),
                Json::Array(identities.into_iter().map(Json::from).collect()),
            );
            result.insert("provider_execution_stopped".into(), stopped.into());
            results.push(result);
        }
    }
    Ok(results)
}

/// `response_reservations(owner, contexts)`: the actions bound queues committed, and the
/// narration attempts that answer them (their own, and retries or turn activity naming a bound
/// basis).
pub fn response_reservations(
    store: &AuthorityStore,
    contexts: &[SessionContext],
) -> StoreResult<(BTreeSet<String>, BTreeSet<String>)> {
    let (mut reserved, mut attempts) = (BTreeSet::new(), BTreeSet::new());
    for context in contexts {
        let queues = bound_queues(context, None, None)?;
        let bases: Vec<Json> = queues
            .iter()
            .map(|queue| item(queue, "narration_basis_message_id").cloned())
            .collect::<StoreResult<_>>()?;
        for queue in &queues {
            reserved.extend(committed_ids(store, queue)?);
            // Membership is only ever tested with attempt identities, which are strings.
            if let Json::String(attempt) = item(queue, "narration_attempt_id")? {
                attempts.insert(attempt.clone());
            }
        }
        for message in &context.messages {
            let content = message.content();
            let retry_or_activity = matches!(
                content.get("kind").and_then(Json::as_str),
                Some("gm_turn_activity" | "player_action_response_retry")
            );
            if message.kind() == SessionMessageKind::Evidence
                && retry_or_activity
                && content.get("basis_message_id").is_some_and(|basis| bases.contains(basis))
            {
                if let Some(Json::String(attempt)) = content.get("attempt_id") {
                    attempts.insert(attempt.clone());
                }
            }
        }
    }
    Ok((reserved, attempts))
}

// ── The owner a sequence works through ─────────────────────────────────────────────────────

use crate::authority::actions::ActionError;
use crate::authority::content_reads::CatalogTool;
use std::future::Future;
use sylvia_domain::authority::{ActionReceipt, AwaitingChoiceReceipt, CampaignSnapshot, CanonicalEventType};
use sylvia_domain::session::SessionStatus;
use sylvia_domain::DomainError;
use sylvia_store::actions::IdempotencyHit;

/// A result whose error is the owner's own ([`ActionError`]).
pub type SequenceResult<T> = Result<T, ActionError>;

/// `_resolve_panel_control(...)` / `_resolve_card_control(...)`: the bound tool, its parameters,
/// its label and its declared actor parameter.
#[derive(Debug, Clone, PartialEq)]
pub struct ResolvedControl {
    pub tool: CatalogTool,
    pub parameters: JsonMap,
    pub label: String,
    pub actor_parameter: Option<String>,
}

/// `_player_action_in_owned_work(**envelope, preview_only=...)`'s arguments. `selection` is
/// the raw JSON a step saved (`Json::Null` for Python's `None`).
#[derive(Debug, Clone, PartialEq)]
pub struct PlayerActionRequest {
    pub campaign_id: String,
    pub source: Json,
    pub operation_id: String,
    pub expected_revision: i64,
    pub selection: Json,
    pub session_id: Option<String>,
    pub confirmation: Option<String>,
    pub preview_only: bool,
}

/// What `_player_action_in_owned_work` answers: a receipt, a saved choice, or (with
/// `preview_only`) the preview.
#[derive(Debug, Clone, PartialEq)]
pub enum PlayerActionAnswer {
    Receipt(ActionReceipt),
    AwaitingChoice(AwaitingChoiceReceipt),
    Preview(JsonMap),
}

/// The owner operations `application/action_sequences.py` calls, with Python's names. Lane 3's
/// authority implements it; the sequence functions below are generic over it.
///
/// The four guards hold across `body`, as Python's `with` blocks do: what runs inside the held
/// region, and where an error leaves it, are Python's. `with_lane` and `with_narration_lock` are
/// reentrant on one task (Python's `RLock`s): `read` runs inside `_execute`'s held lane, and
/// `bind_narration_action` takes the narration lock `_execute` already holds.
pub trait SequenceOwner: Sync {
    /// `with owner._operation():`.
    fn with_operation<'a, R, F, Fut>(&'a self, body: F) -> impl Future<Output = SequenceResult<R>> + Send + 'a
    where
        R: Send + 'a,
        F: FnOnce() -> Fut + Send + 'a,
        Fut: Future<Output = SequenceResult<R>> + Send + 'a;

    /// `with owner._lane(campaign_id):`.
    fn with_lane<'a, R, F, Fut>(
        &'a self,
        campaign_id: &'a str,
        body: F,
    ) -> impl Future<Output = SequenceResult<R>> + Send + 'a
    where
        R: Send + 'a,
        F: FnOnce() -> Fut + Send + 'a,
        Fut: Future<Output = SequenceResult<R>> + Send + 'a;

    /// `with owner._direct_action_work(campaign_id):`.
    fn with_direct_action_work<'a, R, F, Fut>(
        &'a self,
        campaign_id: &'a str,
        body: F,
    ) -> impl Future<Output = SequenceResult<R>> + Send + 'a
    where
        R: Send + 'a,
        F: FnOnce() -> Fut + Send + 'a,
        Fut: Future<Output = SequenceResult<R>> + Send + 'a;

    /// `with owner._narration_lock:`.
    fn with_narration_lock<'a, R, F, Fut>(&'a self, body: F) -> impl Future<Output = SequenceResult<R>> + Send + 'a
    where
        R: Send + 'a,
        F: FnOnce() -> Fut + Send + 'a,
        Fut: Future<Output = SequenceResult<R>> + Send + 'a;

    /// `owner._active_dm_context(session_id)`.
    fn active_dm_context<'a>(&'a self, session_id: &'a str)
        -> impl Future<Output = SequenceResult<SessionContext>> + Send + 'a;

    /// `owner._sessions.context(session_id)`.
    fn session_context<'a>(&'a self, session_id: &'a str)
        -> impl Future<Output = SequenceResult<SessionContext>> + Send + 'a;

    /// `owner._sessions.append_message(session_id=..., message_id=..., kind=..., content=...)`.
    fn append_session_message<'a>(
        &'a self,
        session_id: &'a str,
        message_id: &'a str,
        kind: SessionMessageKind,
        content: &'a JsonMap,
    ) -> impl Future<Output = SequenceResult<()>> + Send + 'a;

    /// `owner._store.snapshot(campaign_id)`.
    fn snapshot<'a>(&'a self, campaign_id: &'a str) -> impl Future<Output = SequenceResult<CampaignSnapshot>> + Send + 'a;

    /// `owner._store.action_hit(campaign_id, action_id)`.
    fn action_hit<'a>(
        &'a self,
        campaign_id: &'a str,
        action_id: &'a str,
    ) -> impl Future<Output = SequenceResult<Option<IdempotencyHit>>> + Send + 'a;

    /// `owner._store.pending_mechanics_action(campaign_id) is not None`.
    fn has_pending_mechanics_action<'a>(&'a self, campaign_id: &'a str)
        -> impl Future<Output = SequenceResult<bool>> + Send + 'a;

    /// `owner._store.current_open_turn(campaign_id)`.
    fn current_open_turn<'a>(&'a self, campaign_id: &'a str)
        -> impl Future<Output = SequenceResult<Option<String>>> + Send + 'a;

    /// `owner._campaign_work_counts.get(campaign_id)`, `0` for `None`.
    fn campaign_work_count<'a>(&'a self, campaign_id: &'a str) -> impl Future<Output = SequenceResult<usize>> + Send + 'a;

    /// `owner._store.campaign_session_contexts(campaign_id)`.
    fn campaign_session_contexts<'a>(
        &'a self,
        campaign_id: &'a str,
    ) -> impl Future<Output = SequenceResult<Vec<SessionContext>>> + Send + 'a;

    /// `owner._store.player_action_markers(campaign_id, action_ids, kind)`.
    fn player_action_markers<'a>(
        &'a self,
        campaign_id: &'a str,
        action_ids: &'a [&'a str],
        kind: &'a str,
    ) -> impl Future<Output = SequenceResult<Vec<JsonMap>>> + Send + 'a;

    /// `owner._player_control_scope(snapshot)`.
    fn player_control_scope<'a>(
        &'a self,
        snapshot: &'a CampaignSnapshot,
    ) -> impl Future<Output = SequenceResult<Option<JsonMap>>> + Send + 'a;

    /// `owner._resolve_panel_control(snapshot, source, selection)`.
    fn resolve_panel_control<'a>(
        &'a self,
        snapshot: &'a CampaignSnapshot,
        source: &'a Json,
        selection: &'a Json,
    ) -> impl Future<Output = SequenceResult<ResolvedControl>> + Send + 'a;

    /// `owner._resolve_card_control(snapshot, source, selection)`.
    fn resolve_card_control<'a>(
        &'a self,
        snapshot: &'a CampaignSnapshot,
        source: &'a Json,
        selection: &'a Json,
    ) -> impl Future<Output = SequenceResult<ResolvedControl>> + Send + 'a;

    /// `owner._check_player_action(tool, parameters, snapshot)`.
    fn check_player_action<'a>(
        &'a self,
        tool: &'a CatalogTool,
        parameters: &'a JsonMap,
        snapshot: &'a CampaignSnapshot,
    ) -> impl Future<Output = SequenceResult<()>> + Send + 'a;

    /// `owner._player_action_preview(tool, parameters, snapshot, source, selection)`.
    fn player_action_preview<'a>(
        &'a self,
        tool: &'a CatalogTool,
        parameters: &'a JsonMap,
        snapshot: &'a CampaignSnapshot,
        source: &'a Json,
        selection: &'a Json,
    ) -> impl Future<Output = SequenceResult<Option<JsonMap>>> + Send + 'a;

    /// `owner.campaign_panels(campaign_id)`.
    fn campaign_panels<'a>(&'a self, campaign_id: &'a str) -> impl Future<Output = SequenceResult<JsonMap>> + Send + 'a;

    /// `owner._player_action_in_owned_work(**envelope, preview_only=...)`.
    fn player_action_in_owned_work<'a>(
        &'a self,
        request: &'a PlayerActionRequest,
    ) -> impl Future<Output = SequenceResult<PlayerActionAnswer>> + Send + 'a;

    /// `owner._append_player_action_references(session_id, receipt)`.
    fn append_player_action_references<'a>(
        &'a self,
        session_id: &'a str,
        receipt: &'a ActionReceipt,
    ) -> impl Future<Output = SequenceResult<()>> + Send + 'a;

    /// `owner._catalog(campaign_id)`.
    fn catalog<'a>(&'a self, campaign_id: &'a str) -> impl Future<Output = SequenceResult<Vec<CatalogTool>>> + Send + 'a;

    /// The `attempt_id`s of `owner._attempts.values()` for this campaign that are not terminal and
    /// whose `turn_id` equals `turn_id` (`None` matches none), in `_attempts` order. Called only
    /// inside [`SequenceOwner::with_narration_lock`].
    fn open_narration_attempts<'a>(
        &'a self,
        campaign_id: &'a str,
        turn_id: Option<&'a str>,
    ) -> impl Future<Output = SequenceResult<Vec<String>>> + Send + 'a;

    /// `owner.bind_narration_action(campaign_id, attempt_id, action_id)`.
    fn bind_narration_action<'a>(
        &'a self,
        campaign_id: &'a str,
        attempt_id: &'a str,
        action_id: &'a str,
    ) -> impl Future<Output = SequenceResult<()>> + Send + 'a;
}

// ── Python's statuses and errors ───────────────────────────────────────────────────────────

const READY: &str = "ready";
const RUNNING: &str = "running";
const PAUSED: &str = "paused";
const FAILED: &str = "failed";
const STALE: &str = "stale";
const PENDING: &str = "pending";
const EXECUTING: &str = "executing";
const AWAITING_CHOICE: &str = "awaiting_choice";

/// The classes Python's `except AuthorityError` catches.
const AUTHORITY_CLASSES: [&str; 13] = [
    "AuthorityError",
    "CommandRejected",
    "IdempotencyConflict",
    "MechanicsRejected",
    "OwnerConflict",
    "PortableRecoveryFailure",
    "RulesetPreconditionRejected",
    "SchemaIdentityError",
    "StaleRevision",
    "WrongOwner",
    "WrongWriter",
    "_BackupFailure",
    "_ExportFailure",
];

/// Whether Python's `except AuthorityError` catches `error`. A coded store refusal without a
/// class is the owner's `CommandRejected`.
fn is_authority_error(error: &ActionError) -> bool {
    match error {
        ActionError::Domain(error) => AUTHORITY_CLASSES.contains(&error.python_class()),
        ActionError::Store(error) => error
            .rejection()
            .is_some_and(|coded| AUTHORITY_CLASSES.contains(&coded.error_type.unwrap_or("CommandRejected"))),
    }
}

/// `str(error)`.
fn error_text(error: &ActionError) -> String {
    match error {
        ActionError::Domain(error) => error.message().to_owned(),
        ActionError::Store(error) => error.rejection().map_or_else(|| error.to_string(), |coded| coded.message),
    }
}

fn reject(message: &str) -> ActionError {
    DomainError::command_rejected(message).into()
}

fn stale(message: &str) -> ActionError {
    DomainError::Rejected {
        error_type: "StaleRevision",
        code: "stale_revision".into(),
        message: message.into(),
        diagnostics: Vec::new(),
        mechanics: None,
    }
    .into()
}

fn uncaught(class: &'static str, message: impl Into<String>) -> ActionError {
    DomainError::uncaught(class, message).into()
}

/// `map[key]`, Python's `KeyError` when it is missing.
fn field<'a>(map: &'a JsonMap, key: &str) -> SequenceResult<&'a Json> {
    map.get(key).ok_or_else(|| uncaught("KeyError", format!("'{key}'")))
}

/// A saved object field (a saved queue is the sequence code's own evidence; another JSON type
/// is possible only by editing the database, and fails as a `TypeError`).
fn object(value: &Json) -> SequenceResult<&JsonMap> {
    value.as_object().ok_or_else(|| uncaught("TypeError", format!("'{}' object is not subscriptable", py_type_name(value))))
}

fn text(value: &Json) -> SequenceResult<&str> {
    value.as_str().ok_or_else(|| uncaught("TypeError", format!("expected str, got {}", py_type_name(value))))
}

/// The saved steps of a queue.
fn steps_of(queue: &JsonMap) -> SequenceResult<&Vec<Json>> {
    match field(queue, "steps")? {
        Json::Array(steps) => Ok(steps),
        other => Err(uncaught("TypeError", format!("'{}' object is not a list of steps", py_type_name(other)))),
    }
}

fn step_at(queue: &JsonMap, index: usize) -> SequenceResult<&JsonMap> {
    object(&steps_of(queue)?[index])
}

fn step_mut(queue: &mut JsonMap, index: usize) -> SequenceResult<&mut JsonMap> {
    match queue.get_mut("steps") {
        Some(Json::Array(steps)) => steps[index]
            .as_object_mut()
            .ok_or_else(|| uncaught("TypeError", "a saved step is not an object")),
        _ => Err(uncaught("KeyError", "'steps'")),
    }
}

fn is(value: &Json, status: &str) -> bool {
    value.as_str() == Some(status)
}

fn set(map: &mut JsonMap, key: &str, value: impl Into<Json>) {
    map.insert(key.into(), value.into());
}

/// `value + 1` for a saved version, as Python adds to a `bool`, `int` or `float`.
fn plus_one(value: &Json) -> SequenceResult<Json> {
    match value {
        Json::Bool(flag) => Ok(Json::from(i64::from(*flag) + 1)),
        Json::Number(number) => Ok(match number.as_i64() {
            Some(int) => Json::from(int + 1),
            None => Json::from(number.as_f64().expect("a JSON number") + 1.0),
        }),
        other => Err(uncaught(
            "TypeError",
            format!("unsupported operand type(s) for +: '{}' and 'int'", py_type_name(other)),
        )),
    }
}

// ── The queue's records ────────────────────────────────────────────────────────────────────

/// `_context(owner, session_id, campaign_id, require_active=...)`.
async fn context<O: SequenceOwner>(
    owner: &O,
    session_id: &str,
    campaign_id: Option<&Json>,
    require_active: bool,
) -> SequenceResult<SessionContext> {
    let context = if require_active {
        owner.active_dm_context(session_id).await?
    } else {
        owner.session_context(session_id).await?
    };
    if context.session.role() != WriterRole::Dm || context.session.campaign_id().is_none() {
        return Err(reject("Action sequence requires a campaign GM session"));
    }
    if let Some(campaign_id) = campaign_id {
        if Some(campaign_id) != context.session.campaign_id().map(Json::from).as_ref() {
            return Err(reject("Action sequence belongs to another campaign"));
        }
    }
    Ok(context)
}

/// `_load(owner, session_id, sequence_id)`: the latest saved version of the queue.
async fn load<O: SequenceOwner>(
    owner: &O,
    session_id: &str,
    sequence_id: Option<&str>,
) -> SequenceResult<Option<JsonMap>> {
    let context = context(owner, session_id, None, false).await?;
    for message in context.messages.iter().rev() {
        let content = message.content();
        if message.kind() == SessionMessageKind::Evidence
            && content.get("kind").and_then(Json::as_str) == Some("action_sequence")
            && sequence_id.is_none_or(|id| content.get("sequence_id") == Some(&Json::from(id)))
        {
            return Ok(Some(content.clone()));
        }
    }
    Ok(None)
}

/// `_consent(queue)`: the SHA-256 of the ordered intentions the player confirms.
fn consent(queue: &JsonMap) -> SequenceResult<String> {
    let mut steps = Vec::new();
    for step in steps_of(queue)? {
        let step = object(step)?;
        let mut kept = JsonMap::new();
        for key in ["step_id", "source", "selection", "operation_id"] {
            kept.insert(key.into(), step.get(key).cloned().unwrap_or(Json::Null));
        }
        steps.push(Json::Object(kept));
    }
    let mut payload = JsonMap::new();
    payload.insert("sequence_id".into(), field(queue, "sequence_id")?.clone());
    payload.insert("version".into(), field(queue, "version")?.clone());
    payload.insert("steps".into(), Json::Array(steps));
    Ok(sylvia_base::hash::sha256_hex(&canonical_json_bytes(&Json::Object(payload))))
}

/// `_project(queue)`: the saved queue with its confirmation and committed actions.
fn project(queue: &JsonMap) -> SequenceResult<JsonMap> {
    let confirmation = if is(field(queue, "status")?, RUNNING) {
        queue.get("submission_confirmation").cloned().unwrap_or(Json::Null)
    } else {
        Json::from(consent(queue)?)
    };
    let mut committed = Vec::new();
    for step in steps_of(queue)? {
        let step = object(step)?;
        if is(field(step, "status")?, STEP_COMMITTED) {
            committed.push(field(step, "operation_id")?.clone());
        }
    }
    let mut projected = queue.clone();
    projected.insert("confirmation".into(), confirmation);
    projected.insert("committed_action_ids".into(), Json::Array(committed));
    Ok(projected)
}

/// `_save(owner, queue)`: the next version, saved as the session's evidence.
async fn save<O: SequenceOwner>(owner: &O, queue: &mut JsonMap) -> SequenceResult<JsonMap> {
    let version = plus_one(field(queue, "version")?)?;
    queue.insert("version".into(), version.clone());
    let session_id = text(field(queue, "session_id")?)?.to_owned();
    let message_id = format!(
        "sequence-{}-{}",
        sylvia_domain::pyops::py_str(field(queue, "sequence_id")?),
        sylvia_domain::pyops::py_str(&version)
    );
    owner.append_session_message(&session_id, &message_id, SessionMessageKind::Evidence, queue).await?;
    project(queue)
}

/// `_receipt(receipt)`.
fn receipt_record(answer: &PlayerActionAnswer) -> SequenceResult<Json> {
    match answer {
        PlayerActionAnswer::Receipt(receipt) => Ok(action_receipt_record(receipt)),
        PlayerActionAnswer::AwaitingChoice(receipt) => Ok(serde_json::json!({
            "action_id": receipt.action_id, "status": AWAITING_CHOICE,
            "basis_revision": receipt.basis_revision, "choice_id": receipt.choice.choice_id,
        })),
        PlayerActionAnswer::Preview(_) => {
            Err(uncaught("AttributeError", "'FrozenJsonDict' object has no attribute 'action_id'"))
        }
    }
}

fn action_receipt_record(receipt: &ActionReceipt) -> Json {
    serde_json::json!({
        "action_id": receipt.action_id, "status": receipt.status.as_str(),
        "result": Json::Object(receipt.result.clone()), "resulting_revision": receipt.resulting_revision,
    })
}

/// `_watermark(snapshot)`.
fn watermark(snapshot: &CampaignSnapshot) -> i64 {
    snapshot.events.iter().map(|event| event.sequence).max().unwrap_or(0)
}

/// `_commit_watermark(snapshot, action_id)`.
fn commit_watermark(snapshot: &CampaignSnapshot, action_id: &str) -> i64 {
    snapshot
        .events
        .iter()
        .filter(|event| {
            event.action_id.as_deref() == Some(action_id) && event.event_type == CanonicalEventType::ActionCommitted
        })
        .map(|event| event.sequence)
        .max()
        .unwrap_or(0)
}

/// `_scope(owner, snapshot)`: the control scope's availability and sorted actors.
async fn scope<O: SequenceOwner>(owner: &O, snapshot: &CampaignSnapshot) -> SequenceResult<Json> {
    let Some(value) = owner.player_control_scope(snapshot).await? else { return Ok(Json::Null) };
    let mut actors = Vec::new();
    for actor in value.get("actors").and_then(Json::as_array).into_iter().flatten() {
        actors.push(text(field(object(actor)?, "id")?)?.to_owned());
    }
    actors.sort();
    Ok(serde_json::json!({"available": value.get("available").cloned().unwrap_or(Json::Null), "actors": actors}))
}

/// `player_control_origin(envelope["source"], expected_revision=envelope["expected_revision"],
/// selection=envelope.get("selection"), session_id=...)`, with the saved values as they are.
fn envelope_origin(envelope: &JsonMap, session_id: &Json) -> SequenceResult<JsonMap> {
    let source = field(envelope, "source")?;
    let expected_revision = field(envelope, "expected_revision")?.clone();
    let selection = envelope.get("selection").unwrap_or(&Json::Null);
    let mut origin = player_control_origin(source, 0, selection, None)?;
    origin.insert("expected_revision".into(), expected_revision);
    origin.insert("session_id".into(), session_id.clone());
    Ok(origin)
}

/// `_needs_recovery(owner, queue)`: a step whose saved envelope's native action committed.
async fn needs_recovery<O: SequenceOwner>(owner: &O, queue: &JsonMap) -> SequenceResult<bool> {
    for index in 0..steps_of(queue)?.len() {
        let step = step_at(queue, index)?;
        if is(field(step, "status")?, STEP_COMMITTED) || !py_truthy(step.get("execution").unwrap_or(&Json::Null)) {
            continue;
        }
        let campaign_id = text(field(queue, "campaign_id")?)?;
        let operation_id = text(field(step, "operation_id")?)?;
        let hit = owner.action_hit(campaign_id, operation_id).await?;
        if hit.is_some_and(|hit| hit.receipt.status.as_str() == STEP_COMMITTED) {
            return Ok(true);
        }
    }
    Ok(false)
}

/// `_resolve(owner, snapshot, source, selection)`: the control, after the source is checked.
async fn resolve<O: SequenceOwner>(
    owner: &O,
    snapshot: &CampaignSnapshot,
    source: &Json,
    selection: &Json,
) -> SequenceResult<ResolvedControl> {
    player_control_origin(source, snapshot.state.revision, selection, None)?;
    if source.get("kind").and_then(Json::as_str) == Some("panel") {
        owner.resolve_panel_control(snapshot, source, selection).await
    } else {
        owner.resolve_card_control(snapshot, source, selection).await
    }
}

// ── read ───────────────────────────────────────────────────────────────────────────────────

/// `read(owner, session_id=..., sequence_id=...)` (the owner's `action_sequence`): the latest
/// saved queue, after recovering native commitments its saved envelopes made.
pub async fn action_sequence<O: SequenceOwner>(
    owner: &O,
    session_id: &str,
    sequence_id: Option<&str>,
) -> SequenceResult<Option<JsonMap>> {
    owner.with_operation(|| read_held(owner, session_id, sequence_id)).await
}

async fn read_held<O: SequenceOwner>(
    owner: &O,
    session_id: &str,
    sequence_id: Option<&str>,
) -> SequenceResult<Option<JsonMap>> {
    let Some(mut queue) = load(owner, session_id, sequence_id).await? else { return Ok(None) };
    let active = !queue.is_empty() && {
        let status = field(&queue, "status")?;
        [RUNNING, PAUSED, FAILED, STALE].iter().any(|known| is(status, known))
    };
    if active {
        let campaign_id = text(field(&queue, "campaign_id")?)?.to_owned();
        let queue_ref = &mut queue;
        let recovered = owner.with_lane(&campaign_id, || recover(owner, session_id, queue_ref)).await?;
        if let Some(projected) = recovered {
            return Ok(Some(projected));
        }
    }
    project(&queue).map(Some)
}

/// `read`'s recovery inside the campaign lane; `Some` when it changed the queue.
async fn recover<O: SequenceOwner>(
    owner: &O,
    session_id: &str,
    queue: &mut JsonMap,
) -> SequenceResult<Option<JsonMap>> {
    let campaign_id = text(field(queue, "campaign_id")?)?.to_owned();
    let mut changed = false;
    let snapshot = owner.snapshot(&campaign_id).await?;
    let session_active = owner.session_context(session_id).await?.session.fields().status == SessionStatus::Active;
    for index in 0..steps_of(queue)?.len() {
        let step = step_at(queue, index)?;
        if is(field(step, "status")?, STEP_COMMITTED) || !py_truthy(step.get("execution").unwrap_or(&Json::Null)) {
            continue;
        }
        let operation_id = text(field(step, "operation_id")?)?.to_owned();
        let Some(hit) = owner.action_hit(&campaign_id, &operation_id).await? else { continue };
        let step = step_at(queue, index)?;
        let origin = envelope_origin(object(field(step, "execution")?)?, field(queue, "session_id")?)?;
        let origins = player_action_origins(&snapshot);
        let saved = origins.iter().find(|(action_id, _)| *action_id == operation_id).map(|(_, origin)| origin);
        if !player_action_request_matches(saved, &origin) {
            return Err(reject("Saved sequence action identity is inconsistent"));
        }
        let receipt = hit.receipt;
        if session_active {
            let queue_session = text(field(queue, "session_id")?)?.to_owned();
            owner.append_player_action_references(&queue_session, &receipt).await?;
        }
        let step = step_mut(queue, index)?;
        set(step, "receipt", action_receipt_record(&receipt));
        if receipt.status.as_str() == STEP_COMMITTED {
            set(step, "status", STEP_COMMITTED);
            set(step, "error", Json::Null);
            set(queue, "error", Json::Null);
            set(queue, "expected_revision", receipt.resulting_revision);
            set(queue, "event_watermark", watermark(&snapshot));
            set(queue, "status", PAUSED);
            set(queue, "pause_reason", "Saved commitment recovered; inspect the remaining intentions");
            if snapshot.state.revision != receipt.resulting_revision
                || watermark(&snapshot) != commit_watermark(&snapshot, &receipt.action_id)
            {
                set(queue, "status", STALE);
                set(queue, "error", "Campaign changed after the saved commitment");
            }
        } else {
            set(step, "status", PENDING);
            set(step, "error", "Native action did not commit");
            set(queue, "status", FAILED);
            set(queue, "error", "Native action did not commit");
        }
        changed = true;
    }
    if !changed {
        return Ok(None);
    }
    if !session_active {
        // Stopped history stays immutable: project the real receipt without resuming the session.
        return project(queue).map(Some);
    }
    save(owner, queue).await.map(Some)
}

// ── compose ────────────────────────────────────────────────────────────────────────────────

/// `compose(...)`'s arguments. `expected_revision`, `steps` and `expected_version` are as given,
/// because Python compares and stores them as they are.
#[derive(Debug, Clone, PartialEq)]
pub struct ComposeRequest {
    pub campaign_id: String,
    pub session_id: String,
    pub sequence_id: String,
    pub expected_revision: Json,
    pub steps: Json,
    pub expected_version: Json,
}

/// `compose(owner, ..., from_agent=...)` (the owner's `compose_action_sequence`, and with
/// `from_agent` its agent seam): a new ready version of the queue, binding each intention to its
/// declared control without simulating resources, rolls or future legality.
pub async fn compose_action_sequence<O: SequenceOwner>(
    owner: &O,
    request: &ComposeRequest,
    from_agent: bool,
) -> SequenceResult<JsonMap> {
    validate_authority_identity(&request.sequence_id, "Action sequence identity", false)?;
    let campaign = Json::from(request.campaign_id.as_str());
    context(owner, &request.session_id, Some(&campaign), true).await?;
    let steps = match &request.steps {
        Json::Array(steps) if steps.len() <= 32 => steps,
        _ => return Err(reject("An action sequence supports at most 32 declared steps")),
    };
    owner
        .with_operation(|| owner.with_lane(&request.campaign_id, || compose_held(owner, request, steps, from_agent)))
        .await
}

async fn compose_held<O: SequenceOwner>(
    owner: &O,
    request: &ComposeRequest,
    steps: &[Json],
    from_agent: bool,
) -> SequenceResult<JsonMap> {
    let campaign_id = request.campaign_id.as_str();
    if !from_agent && owner.campaign_work_count(campaign_id).await? != 0 {
        return Err(DomainError::command_rejected_with(
            "Finish the active campaign operation first",
            "campaign_busy",
            Vec::new(),
        )
        .into());
    }
    let snapshot = owner.snapshot(campaign_id).await?;
    if !py_eq(&Json::from(snapshot.state.revision), &request.expected_revision) {
        return Err(stale("Action sequence expected an obsolete campaign revision"));
    }
    let old = load(owner, &request.session_id, Some(&request.sequence_id)).await?;
    let old = old.filter(|old| !old.is_empty());
    if let Some(old) = &old {
        if needs_recovery(owner, old).await? {
            action_sequence(owner, &request.session_id, Some(&request.sequence_id)).await?;
            return Err(stale("Saved commitments were recovered; inspect the current sequence before editing"));
        }
    }
    let old_version = match &old {
        Some(old) => field(old, "version")?.clone(),
        None => Json::from(0),
    };
    if !py_eq(&old_version, &request.expected_version) {
        return Err(stale("Action sequence version changed"));
    }
    let pending_action = owner.has_pending_mechanics_action(campaign_id).await?;
    let mut old_steps: Vec<(Json, JsonMap)> = Vec::new();
    if let Some(old) = &old {
        let mut busy = false;
        for step in steps_of(old)? {
            let status = field(object(step)?, "status")?;
            if is(status, EXECUTING) || is(status, AWAITING_CHOICE) {
                busy = true;
                break;
            }
        }
        if busy {
            return Err(reject("Recover the saved sequence execution or resolve its choice before editing"));
        }
    }
    if pending_action {
        return Err(reject("Resolve the pending mechanics choice before composing"));
    }
    if let Some(old) = &old {
        for step in steps_of(old)? {
            let step = object(step)?;
            let step_id = field(step, "step_id")?.clone();
            match old_steps.iter_mut().find(|(known, _)| py_eq(known, &step_id)) {
                Some(entry) => entry.1 = step.clone(),
                None => old_steps.push((step_id, step.clone())),
            }
        }
    }
    let mut committed: Vec<Json> = Vec::new();
    for (step_id, step) in &old_steps {
        if is(field(step, "status")?, STEP_COMMITTED) {
            committed.push(step_id.clone());
        }
    }
    let mut prefix = Vec::new();
    for raw in steps.iter().take(committed.len()) {
        match raw {
            Json::Object(raw) => prefix.push(raw.get("step_id").cloned().unwrap_or(Json::Null)),
            other => {
                return Err(uncaught(
                    "AttributeError",
                    format!("'{}' object has no attribute 'get'", py_type_name(other)),
                ))
            }
        }
    }
    if !py_eq(&Json::Array(prefix), &Json::Array(committed)) {
        return Err(reject("Committed steps cannot be removed or reordered"));
    }
    let control_scope = owner.player_control_scope(&snapshot).await?;
    let mut actor_names: Vec<(Json, Json)> = Vec::new();
    if let Some(scope) = &control_scope {
        for actor in scope.get("actors").and_then(Json::as_array).into_iter().flatten() {
            let actor = object(actor)?;
            let id = field(actor, "id")?.clone();
            let name = actor.get("name").cloned().unwrap_or_else(|| id.clone());
            match actor_names.iter_mut().find(|(known, _)| py_eq(known, &id)) {
                Some(entry) => entry.1 = name,
                None => actor_names.push((id, name)),
            }
        }
    }
    let mut result: Vec<Json> = Vec::new();
    let mut ids: Vec<Json> = Vec::new();
    for raw in steps {
        let allowed = ["step_id", "source", "selection"];
        let Json::Object(raw) = raw else { return Err(reject("Action sequence step is invalid")) };
        if raw.keys().any(|key| !allowed.contains(&key.as_str())) {
            return Err(reject("Action sequence step is invalid"));
        }
        let step_id = raw.get("step_id").cloned().unwrap_or(Json::Null);
        let source = raw.get("source").cloned().unwrap_or(Json::Null);
        let selection = raw.get("selection").cloned().unwrap_or(Json::Null);
        validate_authority_identity_value(&step_id, "Action sequence step identity", false)?;
        if ids.iter().any(|known| py_eq(known, &step_id)) {
            return Err(reject("Action sequence step identities must be unique"));
        }
        ids.push(step_id.clone());
        let previous = old_steps.iter().find(|(known, _)| py_eq(known, &step_id)).map(|(_, step)| step);
        let previous = match previous {
            Some(previous) if is(field(previous, "status")?, STEP_COMMITTED) => Some(previous),
            _ => None,
        };
        if let Some(previous) = previous {
            if !py_eq(field(previous, "source")?, &source) || !py_eq(field(previous, "selection")?, &selection) {
                return Err(reject("Committed steps cannot be edited"));
            }
            result.push(Json::Object(previous.clone()));
            continue;
        }
        let control = resolve(owner, &snapshot, &source, &selection).await?;
        if !control.tool.player_controls.available {
            return Err(reject("This declared player control is unavailable"));
        }
        let scope_now = owner.player_control_scope(&snapshot).await?;
        validate_player_control(scope_now.as_ref(), &control.parameters, control.actor_parameter.as_deref())?;
        // Composition binds identities without simulating resources, rolls or future legality.
        let warning = match owner.check_player_action(&control.tool, &control.parameters, &snapshot).await {
            Ok(()) => Json::Null,
            Err(error) if is_authority_error(&error) => Json::from(error_text(&error)),
            Err(error) => return Err(error),
        };
        // A later intention can become legal after an earlier native commit.
        let presentation =
            match owner.player_action_preview(&control.tool, &control.parameters, &snapshot, &source, &selection).await {
                Ok(presentation) => presentation,
                Err(error) if is_authority_error(&error) => None,
                Err(error) => return Err(error),
            };
        let actor_id = match control.actor_parameter.as_deref().filter(|parameter| !parameter.is_empty()) {
            Some(parameter) => control.parameters.get(parameter).cloned().unwrap_or(Json::Null),
            None => Json::Null,
        };
        let edit = plus_one(&request.expected_version)?;
        let operation = format!(
            "sequence-{}",
            sylvia_base::hash::sha256_hex(&canonical_json_bytes(&serde_json::json!({
                "sequence": request.sequence_id, "step": step_id, "edit": edit, "session": request.session_id,
                "source": source, "selection": selection,
            })))
        );
        let actor_name = actor_names
            .iter()
            .find(|(known, _)| py_eq(known, &actor_id))
            .map_or_else(|| actor_id.clone(), |(_, name)| name.clone());
        let preview = presentation.and_then(|presentation| presentation.get("preview").cloned()).unwrap_or(Json::Null);
        let mut queued = JsonMap::new();
        set(&mut queued, "step_id", step_id);
        set(&mut queued, "source", source);
        set(&mut queued, "selection", selection);
        set(&mut queued, "label", control.label.as_str());
        set(&mut queued, "status", PENDING);
        set(&mut queued, "operation_id", operation);
        set(&mut queued, "receipt", Json::Null);
        set(&mut queued, "error", Json::Null);
        set(&mut queued, "warning", warning);
        set(&mut queued, "package_sha256", control.tool.package_sha256.as_str());
        set(&mut queued, "tool", control.tool.name.as_str());
        set(&mut queued, "response_policy", control.tool.player_controls.response.as_str());
        set(&mut queued, "actor", actor_id);
        set(&mut queued, "actor_name", actor_name);
        set(&mut queued, "preview", preview);
        result.push(Json::Object(queued));
    }
    let mut queue = JsonMap::new();
    set(&mut queue, "kind", "action_sequence");
    set(&mut queue, "campaign_id", campaign_id);
    set(&mut queue, "session_id", request.session_id.as_str());
    set(&mut queue, "sequence_id", request.sequence_id.as_str());
    set(&mut queue, "version", request.expected_version.clone());
    set(&mut queue, "status", READY);
    set(&mut queue, "expected_revision", snapshot.state.revision);
    set(&mut queue, "event_watermark", watermark(&snapshot));
    set(&mut queue, "scope", scope(owner, &snapshot).await?);
    set(&mut queue, "turn_id", owner.current_open_turn(campaign_id).await?);
    set(&mut queue, "steps", Json::Array(result));
    set(&mut queue, "error", Json::Null);
    set(&mut queue, "pause_reason", Json::Null);
    save(owner, &mut queue).await
}

// ── execute ────────────────────────────────────────────────────────────────────────────────

/// `execute(...)`'s arguments. `expected_version` is as given.
#[derive(Debug, Clone, PartialEq)]
pub struct ExecuteRequest {
    pub campaign_id: String,
    pub session_id: String,
    pub sequence_id: String,
    pub expected_version: Json,
    pub confirmation: Option<String>,
}

/// `execute(owner, ..., from_agent=...)` (the owner's `execute_action_sequence`, and with
/// `from_agent` its agent seam, which already owns the GM campaign-work lease): run the
/// confirmed intentions in order through the native player-action transaction, stopping at the
/// first refusal, saved choice, staleness or control, turn or Ruleset-decision boundary.
pub async fn execute_action_sequence<O: SequenceOwner>(
    owner: &O,
    request: &ExecuteRequest,
    from_agent: bool,
) -> SequenceResult<JsonMap> {
    let campaign = Json::from(request.campaign_id.as_str());
    context(owner, &request.session_id, Some(&campaign), true).await?;
    if from_agent {
        if owner.campaign_work_count(&request.campaign_id).await? == 0 {
            return Err(reject("Agent sequence execution requires active campaign work"));
        }
        return execute_held(owner, request).await;
    }
    owner
        .with_operation(|| owner.with_direct_action_work(&request.campaign_id, || execute_held(owner, request)))
        .await
}

async fn execute_held<O: SequenceOwner>(owner: &O, request: &ExecuteRequest) -> SequenceResult<JsonMap> {
    owner.with_lane(&request.campaign_id, || execute_in_lane(owner, request)).await
}

/// The envelope a step saved, as `_player_action_in_owned_work`'s keyword arguments.
fn envelope_request(envelope: &JsonMap, preview_only: bool) -> SequenceResult<PlayerActionRequest> {
    let optional_text = |key: &str| -> SequenceResult<Option<String>> {
        match envelope.get(key) {
            None | Some(Json::Null) => Ok(None),
            Some(value) => Ok(Some(text(value)?.to_owned())),
        }
    };
    let revision = field(envelope, "expected_revision")?;
    Ok(PlayerActionRequest {
        campaign_id: text(field(envelope, "campaign_id")?)?.to_owned(),
        source: field(envelope, "source")?.clone(),
        operation_id: text(field(envelope, "operation_id")?)?.to_owned(),
        expected_revision: revision
            .as_i64()
            .ok_or_else(|| uncaught("TypeError", format!("expected int, got {}", py_type_name(revision))))?,
        selection: envelope.get("selection").cloned().unwrap_or(Json::Null),
        session_id: optional_text("session_id")?,
        confirmation: optional_text("confirmation")?,
        preview_only,
    })
}

fn answer_action_id(answer: &PlayerActionAnswer) -> SequenceResult<&str> {
    match answer {
        PlayerActionAnswer::Receipt(receipt) => Ok(&receipt.action_id),
        PlayerActionAnswer::AwaitingChoice(receipt) => Ok(&receipt.action_id),
        PlayerActionAnswer::Preview(_) => {
            Err(uncaught("AttributeError", "'FrozenJsonDict' object has no attribute 'action_id'"))
        }
    }
}

/// `_rebind_map(owner, snapshot, step)`: a queued map selection moved to the current revision.
async fn rebind_map<O: SequenceOwner>(owner: &O, snapshot: &CampaignSnapshot, step: &JsonMap) -> SequenceResult<Json> {
    let mut selection = field(step, "selection")?.clone();
    if let Json::Object(map) = &mut selection {
        if map.contains_key("map_revision") && is(field(object(field(step, "source")?)?, "kind")?, "panel") {
            let panels = owner.campaign_panels(&snapshot.state.campaign_id).await?;
            let surface_id = field(object(field(step, "source")?)?, "surface_id")?;
            let mut surface = None;
            for item in field(&panels, "surfaces")?.as_array().into_iter().flatten() {
                if py_eq(field(object(item)?, "id")?, surface_id) {
                    surface = Some(object(item)?);
                    break;
                }
            }
            let Some(surface) = surface else {
                return Err(reject("The queued map control is no longer available"));
            };
            let revision = field(object(field(surface, "data")?)?, "map_revision")?.clone();
            map.insert("map_revision".into(), revision);
        }
    }
    Ok(selection)
}

/// `_bind_execution_basis(owner, queue)`: the one live narration attempt for this turn, and the
/// input it narrates.
async fn bind_execution_basis<O: SequenceOwner>(owner: &O, queue: &mut JsonMap) -> SequenceResult<()> {
    owner.with_narration_lock(|| bind_execution_basis_held(owner, queue)).await
}

async fn bind_execution_basis_held<O: SequenceOwner>(owner: &O, queue: &mut JsonMap) -> SequenceResult<()> {
    let campaign_id = text(field(queue, "campaign_id")?)?.to_owned();
    let turn_id = field(queue, "turn_id")?.as_str().map(str::to_owned);
    let attempts = owner.open_narration_attempts(&campaign_id, turn_id.as_deref()).await?;
    if attempts.is_empty() {
        return Ok(());
    }
    if attempts.len() != 1 {
        return Err(reject("Action sequence has ambiguous narration ownership"));
    }
    let attempt = &attempts[0];
    let session_id = text(field(queue, "session_id")?)?.to_owned();
    let context = context(owner, &session_id, Some(field(queue, "campaign_id")?), true).await?;
    let basis = context.messages.iter().rev().find(|message| {
        let content = message.content();
        content.get("attempt_id") == Some(&Json::from(attempt.as_str()))
            && (message.kind() == SessionMessageKind::User
                || (message.kind() == SessionMessageKind::Evidence
                    && matches!(
                        content.get("kind").and_then(Json::as_str),
                        Some("campaign_start" | "player_action_response")
                    )))
    });
    let Some(basis) = basis else { return Err(reject("Action sequence narration input is unavailable")) };
    match queue.get("narration_basis_message_id") {
        None | Some(Json::Null) => {}
        Some(known) if known == &Json::from(basis.message_id()) => {}
        Some(_) => return Err(reject("The sequence belongs to an earlier narration input")),
    }
    set(queue, "narration_basis_message_id", basis.message_id());
    set(queue, "narration_attempt_id", attempt.as_str());
    Ok(())
}

/// The model behaviors that stop a queue for a required Ruleset decision.
const REQUIRED_BEHAVIORS: [&str; 2] = ["required_engine_transition", "required_ruleset_decision"];

async fn execute_in_lane<O: SequenceOwner>(owner: &O, request: &ExecuteRequest) -> SequenceResult<JsonMap> {
    let campaign_id = request.campaign_id.as_str();
    let mut queue = load(owner, &request.session_id, Some(&request.sequence_id)).await?;
    if let Some(saved) = queue.as_ref().filter(|saved| !saved.is_empty()) {
        if needs_recovery(owner, saved).await? {
            action_sequence(owner, &request.session_id, Some(&request.sequence_id)).await?;
            queue = load(owner, &request.session_id, Some(&request.sequence_id)).await?;
        }
    }
    let Some(mut queue) = queue else { return Err(reject("Action sequence does not exist")) };
    if !py_eq(field(&queue, "campaign_id")?, &Json::from(campaign_id)) {
        return Err(reject("Action sequence belongs to another campaign"));
    }
    let status = field(&queue, "status")?.clone();
    // Duplicate or uncertain submissions return the durable settled result, never re-execute it.
    if [QUEUE_COMPLETED, FAILED, STALE, PAUSED].iter().any(|settled| is(&status, settled)) {
        return project(&queue);
    }
    let running = is(&status, RUNNING);
    let resubmission =
        running && py_eq(queue.get("submission_version").unwrap_or(&Json::Null), &request.expected_version);
    if !py_eq(field(&queue, "version")?, &request.expected_version) && !resubmission {
        return Err(stale("Action sequence version changed"));
    }
    let expected = if running {
        queue.get("submission_confirmation").cloned().unwrap_or(Json::Null)
    } else {
        Json::from(consent(&queue)?)
    };
    let confirmation = request.confirmation.as_deref().map_or(Json::Null, Json::from);
    if confirmation != expected {
        return Err(DomainError::command_rejected_with(
            "Inspect and confirm the ordered actions first",
            "action_confirmation_required",
            Vec::new(),
        )
        .into());
    }
    if !running {
        set(&mut queue, "submission_version", request.expected_version.clone());
        set(&mut queue, "submission_confirmation", confirmation);
    }
    bind_execution_basis(owner, &mut queue).await?;
    set(&mut queue, "status", RUNNING);
    for index in 0..steps_of(&queue)?.len() {
        if is(field(step_at(&queue, index)?, "status")?, STEP_COMMITTED) {
            continue;
        }
        let snapshot = owner.snapshot(campaign_id).await?;
        let envelope = step_at(&queue, index)?.get("execution").filter(|value| !value.is_null()).cloned();
        let operation_id = text(field(step_at(&queue, index)?, "operation_id")?)?.to_owned();
        let hit = owner.action_hit(campaign_id, &operation_id).await?;
        let receipt = match (&hit, &envelope) {
            // Recover an uncertain native commit before comparing its advanced revision.
            (Some(_), Some(envelope)) => {
                let receipt = owner.player_action_in_owned_work(&envelope_request(object(envelope)?, false)?).await?;
                let resulting = match &receipt {
                    PlayerActionAnswer::Receipt(receipt) => receipt.resulting_revision,
                    PlayerActionAnswer::AwaitingChoice(_) => {
                        return Err(uncaught(
                            "AttributeError",
                            "'AwaitingChoiceReceipt' object has no attribute 'resulting_revision'",
                        ))
                    }
                    PlayerActionAnswer::Preview(_) => {
                        return Err(uncaught(
                            "AttributeError",
                            "'FrozenJsonDict' object has no attribute 'resulting_revision'",
                        ))
                    }
                };
                if snapshot.state.revision != resulting
                    || watermark(&snapshot) != commit_watermark(&snapshot, answer_action_id(&receipt)?)
                {
                    let record = receipt_record(&receipt)?;
                    let step = step_mut(&mut queue, index)?;
                    set(step, "receipt", record);
                    set(step, "status", STEP_COMMITTED);
                    set(&mut queue, "status", STALE);
                    set(&mut queue, "error", "Campaign changed after the saved commitment");
                    return save(owner, &mut queue).await;
                }
                receipt
            }
            _ => {
                if !py_eq(&Json::from(snapshot.state.revision), field(&queue, "expected_revision")?)
                    || !py_eq(&Json::from(watermark(&snapshot)), field(&queue, "event_watermark")?)
                {
                    set(&mut queue, "status", STALE);
                    set(&mut queue, "error", "Campaign changed outside this sequence");
                    return save(owner, &mut queue).await;
                }
                let changed = !py_eq(&scope(owner, &snapshot).await?, field(&queue, "scope")?)
                    || !py_eq(
                        &owner.current_open_turn(campaign_id).await?.map_or(Json::Null, Json::from),
                        field(&queue, "turn_id")?,
                    );
                if changed {
                    set(&mut queue, "status", PAUSED);
                    set(&mut queue, "pause_reason", "Control or turn changed");
                    return save(owner, &mut queue).await;
                }
                match run_step(owner, request, &snapshot, &mut queue, index, envelope).await {
                    Ok(receipt) => receipt,
                    Err(error) if is_authority_error(&error) => {
                        let message = error_text(&error);
                        let step = step_mut(&mut queue, index)?;
                        set(step, "status", PENDING);
                        set(step, "error", message.as_str());
                        set(&mut queue, "status", FAILED);
                        set(&mut queue, "error", message);
                        return save(owner, &mut queue).await;
                    }
                    Err(error) => return Err(error),
                }
            }
        };
        let record = receipt_record(&receipt)?;
        set(step_mut(&mut queue, index)?, "receipt", record);
        if let PlayerActionAnswer::AwaitingChoice(_) = receipt {
            set(step_mut(&mut queue, index)?, "status", AWAITING_CHOICE);
            set(&mut queue, "status", PAUSED);
            set(&mut queue, "pause_reason", "Resolve the saved mechanics choice");
            return save(owner, &mut queue).await;
        }
        let PlayerActionAnswer::Receipt(receipt) = receipt else {
            return Err(uncaught("AttributeError", "'FrozenJsonDict' object has no attribute 'status'"));
        };
        if receipt.status.as_str() != STEP_COMMITTED {
            let step = step_mut(&mut queue, index)?;
            set(step, "status", PENDING);
            set(step, "error", "Native action did not commit");
            set(&mut queue, "status", FAILED);
            set(&mut queue, "error", "Native action did not commit");
            return save(owner, &mut queue).await;
        }
        set(step_mut(&mut queue, index)?, "status", STEP_COMMITTED);
        let turn_id = field(&queue, "turn_id")?.as_str().map(str::to_owned);
        let action_id = receipt.action_id.clone();
        owner
            .with_narration_lock(|| async {
                let attempts = owner.open_narration_attempts(campaign_id, turn_id.as_deref()).await?;
                if attempts.len() == 1 {
                    owner.bind_narration_action(campaign_id, &attempts[0], &action_id).await?;
                }
                Ok(())
            })
            .await?;
        let after = owner.snapshot(campaign_id).await?;
        set(&mut queue, "expected_revision", after.state.revision);
        set(&mut queue, "event_watermark", watermark(&after));
        let boundary = !py_eq(&scope(owner, &after).await?, field(&queue, "scope")?)
            || !py_eq(
                &owner.current_open_turn(campaign_id).await?.map_or(Json::Null, Json::from),
                field(&queue, "turn_id")?,
            );
        if boundary {
            set(&mut queue, "status", PAUSED);
            set(&mut queue, "pause_reason", "Native action reached a control or turn boundary");
            return save(owner, &mut queue).await;
        }
        // The package's authoritative fresh catalog declares required engine transitions; required
        // reactions and lifecycle stop the queue through the native catalog.
        let required: Vec<CatalogTool> = owner
            .catalog(campaign_id)
            .await?
            .into_iter()
            .filter(|tool| REQUIRED_BEHAVIORS.contains(&tool.model_behavior.as_str()))
            .collect();
        if !required.is_empty() {
            set(&mut queue, "status", PAUSED);
            let names: Vec<&str> = required.iter().map(|tool| tool.name.as_str()).collect();
            set(&mut queue, "pause_reason", format!("Required Ruleset decision: {}", names.join(", ")));
            let mut titles: Vec<&str> = Vec::new();
            for tool in &required {
                if !tool.decision_title.is_empty() && !titles.contains(&tool.decision_title.as_str()) {
                    titles.push(&tool.decision_title);
                }
            }
            let summary = if titles.is_empty() {
                Json::Null
            } else {
                Json::from(format!("Resolve {} before continuing queued actions.", titles.join("; ")))
            };
            set(&mut queue, "pause_summary", summary);
            return save(owner, &mut queue).await;
        }
        save(owner, &mut queue).await?;
    }
    set(&mut queue, "status", QUEUE_COMPLETED);
    save(owner, &mut queue).await
}

/// The `try` of one step without a saved native commit: bind and persist its envelope, then run
/// it.
async fn run_step<O: SequenceOwner>(
    owner: &O,
    request: &ExecuteRequest,
    snapshot: &CampaignSnapshot,
    queue: &mut JsonMap,
    index: usize,
    envelope: Option<Json>,
) -> SequenceResult<PlayerActionAnswer> {
    let envelope = match envelope {
        Some(envelope) => envelope,
        None => {
            let step = step_at(queue, index)?.clone();
            let selection = rebind_map(owner, snapshot, &step).await?;
            let control = resolve(owner, snapshot, field(&step, "source")?, &selection).await?;
            if !py_eq(&Json::from(control.tool.package_sha256.as_str()), field(&step, "package_sha256")?)
                || !py_eq(&Json::from(control.tool.name.as_str()), field(&step, "tool")?)
            {
                return Err(reject("Queued package or declared mechanic changed"));
            }
            let mut envelope = JsonMap::new();
            set(&mut envelope, "campaign_id", request.campaign_id.as_str());
            set(&mut envelope, "source", field(&step, "source")?.clone());
            set(&mut envelope, "operation_id", field(&step, "operation_id")?.clone());
            set(&mut envelope, "expected_revision", snapshot.state.revision);
            set(&mut envelope, "selection", selection);
            set(&mut envelope, "session_id", request.session_id.as_str());
            let preview = owner.player_action_in_owned_work(&envelope_request(&envelope, true)?).await?;
            let confirmation = match &preview {
                PlayerActionAnswer::Preview(preview) => field(preview, "confirmation")?.clone(),
                _ => return Err(uncaught("TypeError", "the receipt is not subscriptable")),
            };
            set(&mut envelope, "confirmation", confirmation);
            let step = step_mut(queue, index)?;
            set(step, "execution", Json::Object(envelope.clone()));
            set(step, "status", EXECUTING);
            // Persist the exact envelope before any native transaction.
            save(owner, queue).await?;
            Json::Object(envelope)
        }
    };
    owner.player_action_in_owned_work(&envelope_request(object(&envelope)?, false)?).await
}

// ── last_attempt_commit ────────────────────────────────────────────────────────────────────

/// `last_attempt_commit(owner, campaign_id, attempt_id)`: the latest action a queue bound to this
/// narration attempt committed.
pub async fn last_attempt_commit<O: SequenceOwner>(
    owner: &O,
    campaign_id: &str,
    attempt_id: &str,
) -> SequenceResult<Option<String>> {
    let snapshot = owner.snapshot(campaign_id).await?;
    let mut identities: Vec<String> = Vec::new();
    for context in owner.campaign_session_contexts(campaign_id).await? {
        for queue in bound_queues(&context, None, Some(attempt_id)).map_err(ActionError::Store)? {
            let (queue_campaign, operation_ids) = marker_request(&queue).map_err(ActionError::Store)?;
            let ids: Vec<&str> = operation_ids.iter().map(String::as_str).collect();
            let markers = owner.player_action_markers(&queue_campaign, &ids, PLAYER_ACTION_ORIGIN).await?;
            identities.extend(committed_from_markers(&queue, &markers).map_err(ActionError::Store)?);
        }
    }
    Ok(snapshot.actions.iter().rev().find(|action| identities.contains(&action.action_id)).map(|action| action.action_id.clone()))
}
