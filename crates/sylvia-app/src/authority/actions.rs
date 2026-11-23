//! Executing one Game Master tool action, the application half: `AuthorityOwner.execute_action`
//! with `_execute_action`, `_admit`, `_admitted_tool`, `_locked_package`, `_semantic_hash`,
//! `_participant_context_payload`, `_owned_mechanic_outcome` and `_safe_tool_failure_outcome`.
//!
//! This is the plain commit path (lane 4's T3, step 1): a mechanic that commits, fails, or is
//! refused by its Ruleset. Not ported yet, so refused as `rust_port_pending`: an answer that is an
//! awaiting-choice receipt (a mechanic asking for a choice, or a replay of one), which comes with
//! choice resolution; a mechanic's participant mutations; a player action's origin and the
//! Campaign File batches, which come with their own families.
//!
//! Errors keep Python's classes for lane 4's MCP surface: what `_execute_action` and lane 2's
//! mechanic raise come back as [`ActionError::Domain`], unchanged (a `MechanicsRejected` keeps its
//! details, a `RulesetPreconditionRejected` its safe reason), and what the store raises as
//! [`ActionError::Store`], whose coded refusals carry Python's class.

use sylvia_base::{CodedError, Json, JsonMap};
use sylvia_domain::authority::{
    canonical_payload_is_safe, mechanic_outcome_is_safe, validate_authority_identity, ActionCommand,
    ActionReceipt, ActionStatus, AwaitingChoiceReceipt, CampaignRecord, LifecycleState, MechanicContext,
    MechanicOutcome, PackageLock, PackageLockEntry, Participant, PlayerProjection, PlayerProjectionKind,
    TurnStatus,
};
use sylvia_domain::DomainError;
use sylvia_store::{AuthorityStore, StoreError, StoreHandle};

/// The owner's answer to `rust_port_pending`.
const PORT_PENDING: &str = "rust_port_pending";

/// What `execute_action` answers: `ActionReceipt | AwaitingChoiceReceipt`.
#[derive(Debug, Clone, PartialEq)]
pub enum ActionExecution {
    Receipt(ActionReceipt),
    AwaitingChoice(AwaitingChoiceReceipt),
}

/// What `execute_action` raises: Python's own exception unchanged, or a store failure.
#[derive(Debug)]
pub enum ActionError {
    /// What `_execute_action` and lane 2's mechanic raise, in Python's class.
    Domain(DomainError),
    /// What the store raises; its coded refusals carry Python's class.
    Store(StoreError),
}

impl From<DomainError> for ActionError {
    fn from(error: DomainError) -> Self {
        Self::Domain(error)
    }
}

impl From<StoreError> for ActionError {
    fn from(error: StoreError) -> Self {
        Self::Store(error)
    }
}

impl ActionError {
    /// The owner's reply for this error: its code, message and Python class. A failure Python
    /// does not catch is the owner's `internal_error`.
    pub fn into_coded(self) -> CodedError {
        match self {
            Self::Domain(error) => error.rejection().unwrap_or_else(|| {
                CodedError::new("internal_error", error.message()).with_error_type(error.python_class())
            }),
            Self::Store(StoreError::NotPorted(what)) => {
                CodedError::new(PORT_PENDING, format!("not yet ported: {what}"))
            }
            Self::Store(error) => {
                error.rejection().unwrap_or_else(|| CodedError::new("internal_error", error.to_string()))
            }
        }
    }
}

fn rejected(error_type: &'static str, code: &str, message: &str) -> DomainError {
    DomainError::Rejected {
        error_type,
        code: code.to_owned(),
        message: message.to_owned(),
        diagnostics: Vec::new(),
        mechanics: None,
    }
}

fn command_rejected(message: &str, code: &str) -> DomainError {
    rejected("CommandRejected", code, message)
}

/// `_locked_package(package_id, package_lock)`.
fn locked_package(package_id: &str, lock: &PackageLock) -> Result<PackageLockEntry, DomainError> {
    lock.entries()
        .iter()
        .find(|entry| entry.package_id() == package_id)
        .cloned()
        .ok_or_else(|| command_rejected("Package tool is inactive or unavailable", "tool_unavailable"))
}

/// `_semantic_hash(command, parameters, package_version=..., package_sha256=...)`.
fn semantic_hash(command: &ActionCommand, parameters: &JsonMap, package: &PackageLockEntry) -> String {
    let mut payload = JsonMap::new();
    payload.insert("campaign_id".into(), command.campaign_id.as_str().into());
    payload.insert("action_id".into(), command.action_id.as_str().into());
    payload.insert("writer".into(), command.writer.as_str().into());
    payload.insert("expected_revision".into(), command.expected_revision.into());
    payload.insert("turn_id".into(), command.turn_id.as_str().into());
    payload.insert("package_id".into(), command.package_id.as_str().into());
    payload.insert("package_version".into(), package.version().into());
    payload.insert("package_sha256".into(), package.sha256().into());
    payload.insert("tool_name".into(), command.tool_name.as_str().into());
    payload.insert("parameters".into(), Json::Object(parameters.clone()));
    let encoded = sylvia_base::pyjson::dumps(&Json::Object(payload), sylvia_base::pyjson::Dump::CANONICAL);
    sylvia_base::hash::sha256_hex(encoded.as_bytes())
}

/// `_admit(command, campaign, turn_status)`.
fn admit(
    command: &ActionCommand,
    campaign: &CampaignRecord,
    turn_status: TurnStatus,
) -> Result<(), DomainError> {
    if campaign.lifecycle != LifecycleState::Active {
        return Err(command_rejected("Campaign lifecycle blocks mutation", "lifecycle_inactive"));
    }
    if campaign.writer != command.writer {
        return Err(rejected("WrongWriter", "wrong_writer", "Command writer is not authoritative"));
    }
    if campaign.revision != command.expected_revision {
        return Err(rejected("StaleRevision", "stale_revision", "Expected campaign revision is stale"));
    }
    if turn_status != TurnStatus::Open {
        return Err(command_rejected("TURN does not admit actions", "command_rejected"));
    }
    Ok(())
}

/// `_participant_context_payload(participant)`.
pub(super) fn participant_context(participant: &Participant) -> Json {
    let fields = participant.fields();
    let text = |value: &Option<String>| value.as_deref().map_or(Json::Null, Json::from);
    let mut payload = JsonMap::new();
    payload.insert("participant_id".into(), fields.participant_id.as_str().into());
    payload.insert("kind".into(), fields.kind.as_str().into());
    payload.insert("package_id".into(), fields.package_id.as_str().into());
    payload.insert("package_version".into(), fields.package_version.as_str().into());
    payload.insert("package_sha256".into(), text(&fields.package_sha256));
    payload.insert("schema_id".into(), text(&fields.schema_id));
    payload.insert("schema_sha256".into(), text(&fields.schema_sha256));
    payload.insert("validation_status".into(), fields.validation_status.as_str().into());
    payload.insert("adaptation_id".into(), text(&fields.adaptation_id));
    payload.insert("data".into(), Json::Object(fields.data.clone()));
    Json::Object(payload)
}

/// `_owned_mechanic_outcome(outcome)`'s checks on a typed outcome: identities of the state
/// mutations, participant mutations and consequence kinds, and no authority-owned provenance.
/// Each failure is the defect path's message.
fn owned_outcome(outcome: &MechanicOutcome) -> Result<(), String> {
    for mutation in &outcome.mutations {
        validate_authority_identity(&mutation.namespace, "Mechanic mutation namespace", false)
            .map_err(|error| error.message().to_owned())?;
        validate_authority_identity(&mutation.key, "Mechanic mutation key", false)
            .map_err(|error| error.message().to_owned())?;
    }
    for mutation in &outcome.participant_mutations {
        validate_authority_identity(&mutation.participant_id, "Participant mutation identity", false)
            .map_err(|error| error.message().to_owned())?;
    }
    for consequence in &outcome.consequences {
        match consequence.get("kind") {
            None | Some(Json::Null) => {}
            Some(Json::String(kind))
                if kind == sylvia_domain::player_actions::PLAYER_ACTION_ORIGIN
                    || kind == "participant_updated" =>
            {
                return Err("Mechanic cannot declare authority-owned provenance".into());
            }
            Some(Json::String(kind)) => {
                validate_authority_identity(kind, "Mechanic consequence kind", false)
                    .map_err(|error| error.message().to_owned())?;
            }
            // `validate_authority_identity` of a non-string is Python's `TypeError`.
            Some(_) => return Err("Mechanic consequence kind must be text".into()),
        }
    }
    Ok(())
}

/// `_safe_tool_failure_outcome()`.
fn safe_tool_failure_outcome() -> MechanicOutcome {
    let mut result = JsonMap::new();
    result.insert("status".into(), "failed".into());
    result.insert("code".into(), "tool_execution_failed".into());
    result.insert(
        "reason".into(),
        "The Ruleset could not complete this operation. Check its declared inputs; if they are valid, report a \
         Ruleset defect."
            .into(),
    );
    MechanicOutcome {
        result,
        mutations: Vec::new(),
        consequences: Vec::new(),
        choice: None,
        participant_mutations: Vec::new(),
    }
}

/// `str(value)[:2000]`.
fn clipped(text: &str) -> String {
    text.chars().take(2000).collect()
}

/// `AuthorityOwner.execute_action(command)`: one Game Master tool action through the campaign's
/// work and owner lane. `owner_id` is this owner's own identity (`self._owner_id`), which the
/// command must name.
pub async fn execute_action(
    store: &StoreHandle,
    owner_id: &str,
    command: &ActionCommand,
) -> Result<ActionExecution, ActionError> {
    let _work = super::guided::campaign_work(store, &command.campaign_id).await?;
    // The identities, as `validate_authority_identity` checks them.
    let identities = [
        (&command.campaign_id, "Campaign identity"),
        (&command.action_id, "Action identity"),
        (&command.idempotency_key, "Idempotency identity"),
        (&command.owner_id, "Application owner identity"),
        (&command.turn_id, "TURN identity"),
        (&command.package_id, "Package identity"),
        (&command.tool_name, "Tool name"),
    ];
    let invalid = || command_rejected("Command authority identity is invalid", "invalid_authority_identity");
    for (value, label) in identities {
        validate_authority_identity(value, label, false).map_err(|_| invalid())?;
    }
    if let Some(session) = &command.initiating_session_id {
        validate_authority_identity(session, "Initiating agent session identity", false)
            .map_err(|_| invalid())?;
    }
    let parameters = command.parameters.clone();
    if !canonical_payload_is_safe(&Json::Object(parameters.clone())) {
        return Err(command_rejected(
            "Tool parameters contain a reserved canonical channel",
            "unsafe_canonical_payload",
        )
        .into());
    }
    let _lane = store.lanes().owner.acquire(&command.campaign_id).await;
    if command.owner_id != owner_id {
        return Err(rejected(
            "WrongOwner",
            "wrong_owner",
            "Command owner does not hold this application authority",
        )
        .into());
    }
    let owned = command.clone();
    let basis = {
        let (campaign_id, turn_id) = (owned.campaign_id.clone(), owned.turn_id.clone());
        store.call(move |store| store.action_basis(&campaign_id, &turn_id)).await?
    };
    let package = locked_package(&command.package_id, &basis.package_lock)?;
    let hash = semantic_hash(command, &parameters, &package);
    // The replays and the pending choice, in Python's order.
    let replay = {
        let (command, hash) = (owned.clone(), hash.clone());
        store
            .call(move |store| -> sylvia_store::StoreResult<Option<Result<ActionReceipt, &'static str>>> {
                if let Some(existing) =
                    store.idempotency_hit(&command.campaign_id, &command.idempotency_key)?
                {
                    if existing.payload_hash != hash {
                        return Ok(Some(Err("Idempotency key payload does not match")));
                    }
                    return Ok(Some(Ok(existing.receipt)));
                }
                store.pending_mechanics_alias(&command.campaign_id, &command.idempotency_key, &hash)?;
                match store.pending_choice_for(&command.campaign_id, &command.action_id, &hash)? {
                    sylvia_store::action_execution::PendingChoice::None => {}
                    sylvia_store::action_execution::PendingChoice::SameAction => {
                        return Err(StoreError::NotPorted(
                            "an awaiting mechanics choice (choice resolution, T3 step 2)",
                        ));
                    }
                    sylvia_store::action_execution::PendingChoice::Other => {
                        return Err(StoreError::from(DomainError::Rejected {
                            error_type: "CommandRejected",
                            code: "mechanics_choice_pending".into(),
                            message: "Campaign is awaiting a mechanics choice".into(),
                            diagnostics: Vec::new(),
                            mechanics: None,
                        }));
                    }
                }
                if let Some(existing) = store.action_hit(&command.campaign_id, &command.action_id)? {
                    if existing.payload_hash != hash {
                        return Ok(Some(Err("Action identity payload does not match")));
                    }
                    return store
                        .bind_transport_alias(
                            &command.campaign_id,
                            &command.action_id,
                            &command.idempotency_key,
                            &hash,
                        )
                        .map(|receipt| Some(Ok(receipt)));
                }
                Ok(None)
            })
            .await?
    };
    match replay {
        Some(Ok(receipt)) => return Ok(ActionExecution::Receipt(receipt)),
        Some(Err(message)) => {
            return Err(rejected("IdempotencyConflict", "idempotency_conflict", message).into())
        }
        None => {}
    }
    // A campaign whose Ruleset queues starting choices admits only their event first.
    if let Some(boundary) =
        super::turn_reads::campaign_preparation_boundary(store, &command.campaign_id).await?
    {
        if let Some(preparation) = &boundary.preparation {
            let text = |key: &str| preparation.get(key).cloned().unwrap_or(Json::Null);
            let actor_parameter = sylvia_domain::pyops::py_str(&text("actor_parameter"));
            let actor = parameters.get(&actor_parameter).cloned().unwrap_or(Json::Null);
            let normal_event =
                sylvia_domain::pyops::py_eq(&Json::from(command.package_id.as_str()), &text("package_id"))
                    && sylvia_domain::pyops::py_eq(&Json::from(command.tool_name.as_str()), &text("intent"))
                    && sylvia_domain::pyops::py_eq(
                        &actor,
                        &boundary.actor_id.clone().map_or(Json::Null, Json::from),
                    );
            if !normal_event {
                return Err(command_rejected(
                    "Complete the queued starting character choices before play.",
                    "campaign_preparation_pending",
                )
                .into());
            }
        }
    }
    admit(command, &basis.campaign, basis.turn_status)?;
    let decided = {
        let (command, hash, package, basis, parameters) =
            (owned.clone(), hash.clone(), package.clone(), basis, parameters);
        let handle = store.clone();
        store
            .call(move |store| {
                Ok(execute_on(store, handle.narration(), &command, &hash, &package, &basis, parameters))
            })
            .await?
    };
    let commit = match decided {
        Ok(commit) => commit,
        Err(ExecuteFailure::Domain(error)) => return Err(error.into()),
        Err(ExecuteFailure::Store(error)) => return Err(error.into()),
    };
    let receipt = store.call(move |store| store.commit_action(&commit)).await?;
    Ok(ActionExecution::Receipt(receipt))
}

/// A failure inside the store call that runs the mechanic.
enum ExecuteFailure {
    Domain(DomainError),
    Store(StoreError),
}

impl From<StoreError> for ExecuteFailure {
    fn from(error: StoreError) -> Self {
        Self::Store(error)
    }
}

impl From<DomainError> for ExecuteFailure {
    fn from(error: DomainError) -> Self {
        Self::Domain(error)
    }
}

/// `_admitted_tool`, the parameter checks, the mechanic and its outcome checks, on the store
/// thread, with the tool's pending projection held for the run.
fn execute_on(
    store: &AuthorityStore,
    narration: &sylvia_store::narration::NarrationState,
    command: &ActionCommand,
    hash: &str,
    package: &PackageLockEntry,
    basis: &sylvia_store::action_execution::ActionBasis,
    parameters: JsonMap,
) -> Result<Box<sylvia_store::actions::CommitAction>, ExecuteFailure> {
    let unavailable = || command_rejected("Package tool is inactive or unavailable", "tool_unavailable");
    let declaration = store.declared_package_tool(
        package.package_id(),
        package.version(),
        package.sha256(),
        &command.tool_name,
    )?;
    let tool = match &declaration {
        Some(declaration) => super::content_reads::package_tool(store, package, declaration)?,
        None => None,
    };
    let (Some(tool), Some(declaration)) = (tool, declaration) else { return Err(unavailable().into()) };
    let capabilities: Vec<String> = package.capabilities().to_vec();
    if !capabilities.contains(&tool.capability)
        || !crate::package_library::declaration_admitted(
            &declaration,
            &basis.state,
            basis.turn_status.as_str(),
            basis.campaign.writer.as_str(),
            &capabilities,
        )
    {
        return Err(unavailable().into());
    }
    sylvia_domain::schema_admission::validate_parameters(&parameters, &tool.input_schema)?;
    let participant_ids: Vec<&str> =
        basis.participants.iter().map(|participant| participant.fields().participant_id.as_str()).collect();
    sylvia_domain::schema_admission::validate_references(&parameters, &tool.input_schema, &participant_ids)?;
    let participants: JsonMap = basis
        .participants
        .iter()
        .map(|participant| (participant.fields().participant_id.clone(), participant_context(participant)))
        .collect();
    let context = MechanicContext {
        campaign_id: command.campaign_id.clone(),
        turn_id: command.turn_id.clone(),
        action_id: command.action_id.clone(),
        state: basis.state.clone(),
        parameters: parameters.clone(),
        participants,
        choice_resolution: None,
    };
    let key = (command.campaign_id.clone(), command.action_id.clone());
    let mut pending_payload = JsonMap::new();
    pending_payload.insert("package_id".into(), command.package_id.as_str().into());
    pending_payload.insert("package_version".into(), package.version().into());
    pending_payload.insert("package_sha256".into(), package.sha256().into());
    pending_payload.insert("tool_name".into(), command.tool_name.as_str().into());
    pending_payload.insert("parameters".into(), Json::Object(parameters.clone()));
    let projection = PlayerProjection {
        kind: PlayerProjectionKind::PendingTool,
        sequence: None,
        text: None,
        safe_payload: pending_payload,
        collapsed_by_default: true,
        provisional: true,
        action_id: Some(command.action_id.clone()),
        attempt_id: None,
    };
    narration.with(|inner| {
        inner.pending_tools.retain(|(known, _)| *known != key);
        inner.pending_tools.push((key.clone(), projection));
    });
    let outcome = run_mechanic(store, narration, command, package, &tool, &context);
    narration.with(|inner| inner.pending_tools.retain(|(known, _)| *known != key));
    let (outcome, status) = outcome?;
    if outcome.choice.is_some() {
        return Err(ExecuteFailure::Store(StoreError::NotPorted(
            "a mechanic asking for a choice (choice resolution, T3 step 2)",
        )));
    }
    Ok(Box::new(sylvia_store::actions::CommitAction {
        campaign_id: command.campaign_id.clone(),
        action_id: command.action_id.clone(),
        idempotency_key: command.idempotency_key.clone(),
        payload_hash: hash.to_owned(),
        expected_revision: command.expected_revision,
        writer: command.writer,
        turn_id: command.turn_id.clone(),
        package_id: command.package_id.clone(),
        package_version: package.version().to_owned(),
        package_sha256: package.sha256().to_owned(),
        tool_name: command.tool_name.clone(),
        parameters,
        outcome,
        status,
    }))
}

/// The mechanic run inside Python's `try`: a `RulesetPreconditionRejected` is raised; any other
/// failure, an unsafe outcome or one that writes outside the package's state is a Ruleset defect,
/// recorded in the admin audit, and commits the safe failure outcome.
fn run_mechanic(
    store: &AuthorityStore,
    narration: &sylvia_store::narration::NarrationState,
    command: &ActionCommand,
    package: &PackageLockEntry,
    tool: &super::content_reads::CatalogTool,
    context: &MechanicContext,
) -> Result<(MechanicOutcome, ActionStatus), ExecuteFailure> {
    use crate::package_mechanic::MechanicFailure;
    let defect =
        |error: String, cause: Option<String>| -> Result<(MechanicOutcome, ActionStatus), ExecuteFailure> {
            let mut audit = JsonMap::new();
            audit.insert("kind".into(), "ruleset_execution_defect".into());
            audit.insert("package_id".into(), command.package_id.as_str().into());
            audit.insert("tool_name".into(), command.tool_name.as_str().into());
            audit.insert("error".into(), clipped(&error).into());
            audit.insert("cause".into(), cause.map_or(Json::Null, |cause| clipped(&cause).into()));
            narration.record_admin_full_audit(Json::Object(audit))?;
            Ok((safe_tool_failure_outcome(), ActionStatus::Failed))
        };
    let outcome = match tool.execute.execute(context) {
        Ok(outcome) => outcome,
        Err(MechanicFailure::Rejected(error)) => return Err(error.into()),
        Err(MechanicFailure::PortPending(what)) => {
            return Err(ExecuteFailure::Store(StoreError::Coded(CodedError::new(PORT_PENDING, what))))
        }
        Err(MechanicFailure::Failed { message, cause, .. }) => return defect(message, cause),
    };
    if let Err(message) = owned_outcome(&outcome) {
        return defect(message, None);
    }
    if !mechanic_outcome_is_safe(&outcome) {
        return defect("Mechanic outcome is not canonical-safe".into(), None);
    }
    match store.mechanic_mutations_are_owned(
        &command.campaign_id,
        &command.package_id,
        package.version(),
        package.sha256(),
        &outcome,
    ) {
        Ok(true) => Ok((outcome, ActionStatus::Committed)),
        Ok(false) => defect("Mechanic outcome violates state ownership".into(), None),
        Err(StoreError::NotPorted(what)) => Err(ExecuteFailure::Store(StoreError::NotPorted(what))),
        // `except Exception`: a store failure here is a defect too, with its text.
        Err(error) => defect(error.to_string(), None),
    }
}
