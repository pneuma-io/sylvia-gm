//! Starting a guided campaign: binding the finished Interview's content to its Draft and
//! beginning construction, in the store (`SQLiteAuthorityStore`), and the application owner's
//! `append_agent_message`. The other application halves are `sylvia_app::authority::guided`.
//!
//! Python: `bind_guided_campaign_basis`, `campaign_construction_basis` (with `_construction_basis`)
//! and `begin_campaign_construction`, `campaign_operation_hit`, `_campaign_operation_hit`,
//! `_insert_campaign_operation`, `sqlite_packages.insert_campaign_lock` (`_lock_id`) and
//! `AuthorityOwner.append_agent_message`.
//!
//! The binding also writes the saved-library Start's participants and initial state:
//! `legacy_participants_are_admitted`, `participant_adaptation_is_admitted` (with
//! `_participant_adaptation_is_admitted` and `sqlite_campaign.artifact_from_adaptation_row`) and
//! `sqlite_state_ownership.initial_owner` (with `_prefix_matches`).
//!
//! Before committing a binding it runs `sqlite_campaign.verify_f5_campaign_authority` (`f5`), as
//! Python does. What is still not ported is refused in `sylvia_app::authority::guided`.

use crate::actions::{dump, load_payload};
use crate::campaigns::{named_rows, Named};
use crate::error::{StoreError, StoreResult};
use crate::handle::StoreHandle;
use crate::packages::verify_published_lock;
use crate::pytext::py_str;
use crate::sessions::{read_messages, read_session};
use crate::store::AuthorityStore;
use crate::stored_construction::{operation_result, OperationResult};
use rusqlite::types::Value;
use rusqlite::{params, Connection};
use sylvia_base::hash::sha256_hex;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{
    CampaignConfirmation, CampaignConstructionBasis, CampaignConstructionBegun, PackageLock, Participant,
    ParticipantValidationStatus, StateValue, WriterRole,
};
use sylvia_domain::package_library::{closure_sha256, PackageLockAvailability, PackageLockRecord};
use sylvia_domain::pyops::py_eq;
use sylvia_domain::session::{SessionMessage, SessionMessageKind};

/// `_UNBOUND_CAMPAIGN_LOCK_HASH`.
const UNBOUND_CAMPAIGN_LOCK_HASH: &str = "unbound";

fn coded(kind: &'static str, message: &str) -> StoreError {
    StoreError::Coded(crate::error::authority_coded(kind, message))
}

fn rejected(message: &str, code: &str) -> StoreError {
    crate::actions::rejected(message, code)
}

fn first<'a>(columns: &'a [String], rows: Vec<Vec<Value>>) -> Option<Named<'a>> {
    rows.into_iter().next().map(|values| Named { columns, values })
}

/// `_campaign_operation_hit`: the stored result of an operation already committed with this
/// kind and payload, or `None`; any other kind or payload is an `IdempotencyConflict`.
pub(crate) fn campaign_operation_hit_on(
    connection: &Connection,
    operation_id: &str,
    operation_kind: &str,
    payload_sha256: &str,
) -> StoreResult<Option<OperationResult>> {
    let (columns, rows) =
        named_rows(connection, "SELECT * FROM campaign_operations WHERE operation_id = ?", &[&operation_id])?;
    let Some(row) = first(&columns, rows) else { return Ok(None) };
    if row.text("operation_kind")? != operation_kind || row.text("semantic_payload_sha256")? != payload_sha256
    {
        return Err(coded("IdempotencyConflict", "Campaign operation identity payload does not match"));
    }
    operation_result(&row).map(Some)
}

/// `_insert_campaign_operation`'s keyword arguments.
pub(crate) struct NewOperation<'a> {
    pub(crate) operation_id: &'a str,
    pub(crate) operation_kind: &'a str,
    pub(crate) campaign_id: &'a str,
    pub(crate) semantic_payload: &'a JsonMap,
    pub(crate) payload_sha256: &'a str,
    pub(crate) result: &'a JsonMap,
    pub(crate) basis_turn_id: Option<&'a str>,
    pub(crate) basis_lock_id: Option<&'a str>,
    pub(crate) basis_revision: i64,
    pub(crate) resulting_revision: i64,
}

/// `_insert_campaign_operation`.
pub(crate) fn insert_campaign_operation(
    connection: &Connection,
    operation: &NewOperation<'_>,
) -> StoreResult<()> {
    connection.execute(
        "INSERT INTO campaign_operations \
         (operation_id, operation_kind, campaign_id, semantic_payload_json, semantic_payload_sha256, \
         result_json, basis_turn_id, basis_lock_id, basis_revision, resulting_revision) \
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        params![
            operation.operation_id,
            operation.operation_kind,
            operation.campaign_id,
            dump(&Json::Object(operation.semantic_payload.clone())),
            operation.payload_sha256,
            dump(&Json::Object(operation.result.clone())),
            operation.basis_turn_id,
            operation.basis_lock_id,
            operation.basis_revision,
            operation.resulting_revision,
        ],
    )?;
    Ok(())
}

/// `sqlite_packages._lock_id`.
fn lock_id(campaign_id: &str, revision: i64, closure: &str) -> String {
    let mut value = JsonMap::new();
    value.insert("campaign_id".into(), campaign_id.into());
    value.insert("revision".into(), revision.into());
    value.insert("closure".into(), closure.into());
    format!("lock-{}", &sha256_hex(dump(&Json::Object(value)).as_bytes())[..40])
}

/// `sqlite_packages.insert_campaign_lock` for an available lock.
pub(crate) fn insert_campaign_lock(
    connection: &Connection,
    campaign_id: &str,
    package_lock: &PackageLock,
    revision: i64,
    predecessor_lock_id: Option<&str>,
) -> StoreResult<PackageLockRecord> {
    verify_published_lock(connection, package_lock)?;
    let closure = closure_sha256(package_lock);
    let lock_id = lock_id(campaign_id, revision, &closure);
    connection.execute(
        "INSERT INTO campaign_locks VALUES (?, ?, ?, ?, ?, ?)",
        params![
            lock_id,
            campaign_id,
            closure,
            PackageLockAvailability::Available.as_str(),
            predecessor_lock_id,
            revision
        ],
    )?;
    for entry in package_lock.entries() {
        connection.execute(
            "INSERT INTO campaign_lock_memberships VALUES (?, ?, ?, ?, ?, ?)",
            params![
                lock_id,
                entry.load_order(),
                entry.package_id(),
                entry.version(),
                entry.sha256(),
                entry.role().as_str()
            ],
        )?;
    }
    Ok(PackageLockRecord {
        lock_id,
        campaign_id: campaign_id.to_owned(),
        closure_sha256: closure,
        package_lock: package_lock.clone(),
        availability: PackageLockAvailability::Available,
        predecessor_lock_id: predecessor_lock_id.map(str::to_owned),
        created_revision: revision,
    })
}

/// The rows `_construction_basis` reads, with their column names.
pub(crate) struct BasisRows {
    campaign: (Vec<String>, Vec<Value>),
    construction: (Vec<String>, Vec<Value>),
    turn: (Vec<String>, Vec<Value>),
}

impl BasisRows {
    pub(crate) fn campaign(&self) -> Named<'_> {
        Named { columns: &self.campaign.0, values: self.campaign.1.clone() }
    }
    pub(crate) fn construction(&self) -> Named<'_> {
        Named { columns: &self.construction.0, values: self.construction.1.clone() }
    }
    pub(crate) fn turn(&self) -> Named<'_> {
        Named { columns: &self.turn.0, values: self.turn.1.clone() }
    }
}

/// `_construction_basis`: the campaign, its construction and the construction's TURN.
pub(crate) fn construction_basis(connection: &Connection, campaign_id: &str) -> StoreResult<BasisRows> {
    let (campaign_columns, campaign) =
        named_rows(connection, "SELECT * FROM campaigns WHERE campaign_id = ?", &[&campaign_id])?;
    let (construction_columns, construction) = named_rows(
        connection,
        "SELECT * FROM campaign_constructions WHERE campaign_id = ?",
        &[&campaign_id],
    )?;
    let (Some(campaign), Some(construction)) = (campaign.into_iter().next(), construction.into_iter().next())
    else {
        return Err(rejected("Confirmed campaign construction is unavailable", "command_rejected"));
    };
    let turn_id =
        Named { columns: &construction_columns, values: construction.clone() }.text("authority_turn_id")?;
    let (turn_columns, turn) = named_rows(
        connection,
        "SELECT * FROM turns WHERE campaign_id = ? AND turn_id = ?",
        &[&campaign_id, &turn_id],
    )?;
    let Some(turn) = turn.into_iter().next() else {
        return Err(rejected("Campaign construction TURN basis is unavailable", "command_rejected"));
    };
    Ok(BasisRows {
        campaign: (campaign_columns, campaign),
        construction: (construction_columns, construction),
        turn: (turn_columns, turn),
    })
}

/// The store's binding arguments (participants and initial state are not supported yet).
pub struct BindBasis<'a> {
    pub operation_id: &'a str,
    pub semantic_payload: &'a JsonMap,
    pub payload_sha256: &'a str,
    pub campaign_id: &'a str,
    pub display_name: &'a str,
    pub package_lock: &'a PackageLock,
    pub turn_id: &'a str,
    pub builder_session_id: &'a str,
    pub participants: &'a [Participant],
    pub initial_state: &'a [StateValue],
    pub required_interview_steps: &'a [&'a str],
}

/// `sqlite_state_ownership._prefix_matches(package_ids, namespace)`.
fn prefix_matches<'a>(package_ids: &[&'a str], namespace: &str) -> Vec<&'a str> {
    package_ids
        .iter()
        .copied()
        .filter(|package_id| namespace == *package_id || namespace.starts_with(&format!("{package_id}.")))
        .collect()
}

/// `sqlite_state_ownership.initial_owner(package_lock, namespace)`: one initial-state owner,
/// derived without allowing caller-selected ownership.
pub(crate) fn initial_owner(package_lock: &PackageLock, namespace: &str) -> StoreResult<Option<String>> {
    let package_ids: Vec<&str> = package_lock.entries().iter().map(|entry| entry.package_id()).collect();
    let ambiguous =
        || rejected("Campaign state namespace ownership is ambiguous", "state_namespace_ownership_invalid");
    let matches = prefix_matches(&package_ids, namespace);
    match matches.len() {
        1 => return Ok(Some(matches[0].to_owned())),
        0 => {}
        _ => return Err(ambiguous()),
    }
    match package_ids.len() {
        1 => Ok(Some(package_ids[0].to_owned())),
        0 => Ok(None),
        _ => Err(ambiguous()),
    }
}

/// `_participant_adaptation_is_admitted(connection, participant)`: durable exact provenance is
/// required only when an identity crosses schemas.
pub(crate) fn participant_adaptation_admitted_on(
    connection: &Connection,
    participant: &Participant,
) -> StoreResult<bool> {
    let fields = participant.fields();
    let target = (
        fields.kind.clone(),
        fields.package_id.clone(),
        fields.package_version.clone(),
        fields.package_sha256.clone(),
        fields.schema_id.clone(),
        fields.schema_sha256.clone(),
    );
    let (columns, rows) = named_rows(
        connection,
        "SELECT kind, package_id, package_version, package_sha256, schema_id, schema_sha256 \
         FROM participants WHERE participant_id = ?",
        &[&fields.participant_id],
    )?;
    let mut crosses_schema = false;
    for values in rows {
        let row = Named { columns: &columns, values };
        let identity = (
            row.text("kind")?,
            row.text("package_id")?,
            row.text("package_version")?,
            row.optional_text("package_sha256")?,
            row.optional_text("schema_id")?,
            row.optional_text("schema_sha256")?,
        );
        crosses_schema |= identity != target;
    }
    let Some(adaptation_id) = &fields.adaptation_id else {
        return Ok(!crosses_schema);
    };
    let (columns, mut rows) = named_rows(
        connection,
        "SELECT * FROM participant_adaptations WHERE operation_id = ?",
        &[adaptation_id],
    )?;
    if rows.is_empty() {
        return Ok(false);
    }
    // `sqlite_campaign.artifact_from_adaptation_row(row)`.
    let row = Named { columns: &columns, values: rows.swap_remove(0) };
    let adapted_data = Json::Object(load_payload(&row.text("data_json")?)?);
    let data = Json::Object(fields.data.clone());
    // Python builds the whole comparison tuple before `all`, so the data history is read
    // whenever the data differ.
    let data_matches = if py_eq(&data, &adapted_data) {
        true
    } else {
        let (_, sources) = named_rows(
            connection,
            "SELECT campaign_id FROM participants WHERE participant_id = ? AND adaptation_id = ? AND data_json = ?",
            &[&fields.participant_id, adaptation_id, &dump(&data)],
        )?;
        let mut found = false;
        for source in sources {
            let history = crate::participant_history::participant_data_history(
                connection,
                &py_str(&source[0]),
                &fields.participant_id,
                fields.data.clone(),
            )?;
            if history.iter().any(|value| py_eq(value, &adapted_data)) {
                found = true;
                break;
            }
        }
        found
    };
    let text = |column: &str| row.text(column);
    Ok(fields.participant_id == text("participant_id")?
        && fields.kind == text("destination_kind")?
        && fields.package_id == text("destination_package_id")?
        && fields.package_version == text("destination_package_version")?
        && fields.package_sha256.as_deref() == Some(text("destination_package_sha256")?.as_str())
        && fields.schema_id.as_deref() == Some(text("destination_schema_id")?.as_str())
        && fields.schema_sha256.as_deref() == Some(text("destination_schema_sha256")?.as_str())
        && data_matches)
}

impl AuthorityStore {
    /// `participant_adaptation_is_admitted(participant)`.
    pub fn participant_adaptation_is_admitted(&self, participant: &Participant) -> StoreResult<bool> {
        if !self.campaign_schema_is_active()? {
            return Ok(participant.adaptation_id().is_none());
        }
        let connection = self.connect()?;
        participant_adaptation_admitted_on(&connection, participant)
    }

    /// `legacy_participants_are_admitted(package_lock, participants)`: format-1 participant
    /// behaviour preserved without weakening formats 2 and 3.
    pub fn legacy_participants_are_admitted(
        &self,
        package_lock: &PackageLock,
        participants: &[Participant],
    ) -> StoreResult<bool> {
        let connection = self.connect_read_only()?;
        for participant in participants {
            if participant.validation_status() == ParticipantValidationStatus::Validated {
                continue;
            }
            // `{(entry.package_id, entry.version): entry ...}`: a later entry replaces an earlier one.
            let Some(entry) = package_lock.entries().iter().rev().find(|entry| {
                entry.package_id() == participant.package_id()
                    && entry.version() == participant.package_version()
            }) else {
                return Ok(false);
            };
            let (_, rows) = named_rows(
                &connection,
                "SELECT canonical_json FROM package_manifests WHERE package_id = ? AND version = ? \
                 AND content_sha256 = ? AND status = 'published'",
                &[&entry.package_id(), &entry.version(), &entry.sha256()],
            )?;
            let Some(row) = rows.into_iter().next() else { return Ok(false) };
            if load_payload(&py_str(&row[0]))?.contains_key("participant_artifacts") {
                return Ok(false);
            }
        }
        Ok(true)
    }
}

/// The store's begin arguments.
pub struct BeginConstruction<'a> {
    pub operation_id: &'a str,
    pub semantic_payload: &'a JsonMap,
    pub payload_sha256: &'a str,
    pub campaign_id: &'a str,
    pub lane: &'a str,
    pub expected_revision: i64,
    pub canonical_input: &'a JsonMap,
}

impl AuthorityStore {
    /// `campaign_operation_hit`: the application's prelookup, on a read-only connection. Before
    /// the session schema exists there are no operations.
    pub fn campaign_operation_hit(
        &self,
        operation_id: &str,
        operation_kind: &str,
        payload_sha256: &str,
    ) -> StoreResult<Option<OperationResult>> {
        let connection = self.connect_read_only()?;
        let version = crate::verify::verify_exact_schema_identity(&connection, None)?;
        if version == crate::schema::SCHEMA_VERSION || version == crate::schema::SESSION_SCHEMA_VERSION {
            return Ok(None);
        }
        campaign_operation_hit_on(&connection, operation_id, operation_kind, payload_sha256)
    }

    /// `campaign_construction_basis`: the owner-only basis of one begun construction.
    pub fn campaign_construction_basis(&self, campaign_id: &str) -> StoreResult<CampaignConstructionBasis> {
        let connection = self.connect()?;
        let version: i64 = connection.query_row("PRAGMA user_version", [], |row| row.get(0))?;
        if version == crate::schema::SCHEMA_VERSION || version == crate::schema::SESSION_SCHEMA_VERSION {
            return Err(rejected("Confirmed campaign construction is unavailable", "command_rejected"));
        }
        let rows = construction_basis(&connection, campaign_id)?;
        let (campaign, construction) = (rows.campaign(), rows.construction());
        if construction.get("begin_operation_id")? == &Value::Null {
            return Err(rejected("Campaign construction has not begun", "command_rejected"));
        }
        let (columns, confirmation) = named_rows(
            &connection,
            "SELECT semantic_payload_json FROM campaign_operations WHERE operation_id = ?",
            &[&construction.text("confirmation_operation_id")?],
        )?;
        let Some(confirmation) = first(&columns, confirmation) else {
            return Err(rejected("Campaign confirmation receipt is unavailable", "command_rejected"));
        };
        let payload = load_payload(&confirmation.text("semantic_payload_json")?)?;
        let builder_session_id = match payload.get("builder_session") {
            Some(Json::Object(builder)) => match builder.get("session_id") {
                Some(Json::String(session_id)) => session_id.clone(),
                _ => {
                    return Err(rejected("Campaign Builder session basis is unavailable", "command_rejected"))
                }
            },
            _ => return Err(rejected("Campaign Builder session basis is unavailable", "command_rejected")),
        };
        let package_candidate_id = match payload.get("package_candidate_id") {
            None | Some(Json::Null) => None,
            Some(Json::String(candidate)) => Some(candidate.clone()),
            Some(_) => {
                return Err(rejected("Campaign package candidate basis is invalid", "command_rejected"))
            }
        };
        let optional_payload = |column: &str| -> StoreResult<Option<JsonMap>> {
            match construction.get(column)? {
                Value::Null => Ok(None),
                value => load_payload(&py_str(value)).map(Some),
            }
        };
        Ok(CampaignConstructionBasis {
            campaign_id: campaign_id.to_owned(),
            display_name: campaign.text("display_name")?,
            lane: construction.text("lane")?,
            canonical_input: load_payload(&construction.text("canonical_input_json")?)?,
            canonical_plan: optional_payload("canonical_plan_json")?,
            authority_turn_id: construction.text("authority_turn_id")?,
            package_lock_id: construction.text("authority_lock_id")?,
            package_candidate_id,
            builder_session_id,
            begin_revision: construction.int("begin_revision")?,
            completion_revision: match construction.get("completion_revision")? {
                Value::Null => None,
                _ => Some(construction.int("completion_revision")?),
            },
            canonical_representation_sha256: construction.optional_text("canonical_representation_sha256")?,
        })
    }

    /// `bind_guided_campaign_basis`: attach the exact construction basis to one existing
    /// unbound Draft whose Interview is ready.
    pub fn bind_guided_campaign_basis(&self, bind: &BindBasis<'_>) -> StoreResult<CampaignConfirmation> {
        self.in_immediate(|connection| {
            crate::verify::verify_exact_schema_identity(connection, Some(crate::schema::CAMPAIGN_SCHEMA_VERSION))?;
            match campaign_operation_hit_on(connection, bind.operation_id, "confirm_new_campaign", bind.payload_sha256)? {
                Some(OperationResult::Confirmation(replay)) => return Ok(replay),
                Some(_) => return Err(coded("IdempotencyConflict", "Campaign operation result kind does not match")),
                None => {}
            }
            let (campaign_columns, campaign_rows) =
                named_rows(connection, "SELECT * FROM campaigns WHERE campaign_id = ?", &[&bind.campaign_id])?;
            let campaign = first(&campaign_columns, campaign_rows);
            let session = read_session(connection, bind.builder_session_id)?;
            let messages = read_messages(connection, bind.builder_session_id)?;
            let interview_state: Option<&SessionMessage> = messages.iter().rev().find(|message| {
                message.kind() == SessionMessageKind::Evidence
                    && message.content().get("kind") == Some(&Json::String("campaign_interview_state".into()))
            });
            let ready_messages = messages
                .iter()
                .filter(|message| {
                    message.kind() == SessionMessageKind::User
                        && message.content().get("interview_step") == Some(&Json::String("ready".into()))
                        && message.content().get("text") == Some(&Json::String("Ready to Begin".into()))
                })
                .count();
            let unbound = || -> StoreResult<bool> {
                let Some(campaign) = &campaign else { return Ok(false) };
                if !crate::packages::has_only_draft_lifecycle_history(connection, bind.campaign_id, campaign.int("revision")?)?
                    || campaign.text("lifecycle")? != "active"
                    || campaign.text("writer")? != WriterRole::Builder.as_str()
                    || campaign.get("current_lock_id")? != &Value::Null
                    || campaign.text("lock_hash")? != UNBOUND_CAMPAIGN_LOCK_HASH
                    || session.campaign_id() != Some(bind.campaign_id)
                    || session.role() != WriterRole::Builder
                {
                    return Ok(false);
                }
                let Some(state) = interview_state else { return Ok(false) };
                if state.content().get("current_step") != Some(&Json::String("review".into())) {
                    return Ok(false);
                }
                let Some(Json::Object(answers)) = state.content().get("answers") else { return Ok(false) };
                let answered: std::collections::BTreeSet<&str> = answers.keys().map(String::as_str).collect();
                let required: std::collections::BTreeSet<&str> = bind.required_interview_steps.iter().copied().collect();
                if answered != required || ready_messages != 1 {
                    return Ok(false);
                }
                let has = |query: &str| -> StoreResult<bool> {
                    Ok(!named_rows(connection, query, &[&bind.campaign_id])?.1.is_empty())
                };
                Ok(!has("SELECT 1 FROM turns WHERE campaign_id = ? LIMIT 1")?
                    && !has("SELECT 1 FROM campaign_constructions WHERE campaign_id = ?")?)
            };
            if !unbound()? {
                return Err(rejected("Guided Campaign Draft is no longer unbound", "command_rejected"));
            }
            let campaign = campaign.expect("checked above");
            let confirmation_revision = campaign.int("revision")?;
            verify_published_lock(connection, bind.package_lock)?;
            let lock_hash = crate::stored::lock_representation(bind.package_lock);
            let lock_record =
                insert_campaign_lock(connection, bind.campaign_id, bind.package_lock, confirmation_revision, None)?;
            connection.execute(
                "UPDATE campaigns SET display_name = ?, lock_hash = ?, current_lock_id = ? WHERE campaign_id = ?",
                params![bind.display_name, lock_hash, lock_record.lock_id, bind.campaign_id],
            )?;
            for entry in bind.package_lock.entries() {
                connection.execute(
                    "INSERT INTO campaign_package_memberships VALUES (?, ?, ?, ?, ?, ?)",
                    params![
                        bind.campaign_id,
                        entry.package_id(),
                        entry.version(),
                        entry.sha256(),
                        entry.role().as_str(),
                        entry.load_order()
                    ],
                )?;
            }
            if bind.participants.iter().any(|item| item.validation_status() != ParticipantValidationStatus::Validated) {
                return Err(rejected(
                    "Confirmed campaign participants require exact package and schema proof",
                    "participant_exact_proof_required",
                ));
            }
            for participant in bind.participants {
                if !participant_adaptation_admitted_on(connection, participant)? {
                    return Err(rejected(
                        "Cross-schema participant reuse requires a durable adaptation operation",
                        "participant_adaptation_required",
                    ));
                }
            }
            for participant in bind.participants {
                let fields = participant.fields();
                connection.execute(
                    "INSERT INTO participants VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    params![
                        bind.campaign_id,
                        fields.participant_id,
                        fields.kind,
                        fields.package_id,
                        fields.package_version,
                        dump(&Json::Object(fields.data.clone())),
                        fields.package_sha256,
                        fields.schema_id,
                        fields.schema_sha256,
                        fields.validation_status,
                        fields.adaptation_id
                    ],
                )?;
            }
            let mut initial_owners = std::collections::BTreeMap::new();
            for state_value in bind.initial_state {
                let owner = initial_owner(bind.package_lock, state_value.namespace())?;
                initial_owners.insert(state_value.namespace().to_owned(), owner);
            }
            for (namespace, owner_package_id) in &initial_owners {
                connection.execute(
                    "INSERT INTO campaign_state_namespace_owners (campaign_id, namespace, owner_package_id) \
                     VALUES (?, ?, ?)",
                    params![bind.campaign_id, namespace, owner_package_id],
                )?;
            }
            for state_value in bind.initial_state {
                connection.execute(
                    "INSERT INTO campaign_state VALUES (?, ?, ?, ?)",
                    params![bind.campaign_id, state_value.namespace(), state_value.key(), dump(state_value.value())],
                )?;
            }
            connection.execute(
                "INSERT INTO turns VALUES (?, ?, 'open', ?)",
                params![bind.campaign_id, bind.turn_id, confirmation_revision],
            )?;
            let mut result = JsonMap::new();
            result.insert("campaign_id".into(), bind.campaign_id.into());
            result.insert("resulting_revision".into(), confirmation_revision.into());
            insert_campaign_operation(
                connection,
                &NewOperation {
                    operation_id: bind.operation_id,
                    operation_kind: "confirm_new_campaign",
                    campaign_id: bind.campaign_id,
                    semantic_payload: bind.semantic_payload,
                    payload_sha256: bind.payload_sha256,
                    result: &result,
                    basis_turn_id: Some(bind.turn_id),
                    basis_lock_id: Some(&lock_record.lock_id),
                    basis_revision: confirmation_revision,
                    resulting_revision: confirmation_revision,
                },
            )?;
            connection.execute(
                "INSERT INTO campaign_constructions \
                 (campaign_id, confirmation_operation_id, authority_turn_id, authority_lock_id, \
                 confirmation_revision) VALUES (?, ?, ?, ?, ?)",
                params![bind.campaign_id, bind.operation_id, bind.turn_id, lock_record.lock_id, confirmation_revision],
            )?;
            crate::stored::verify_stored_authority(connection)?;
            crate::packages::verify_package_authority(connection)?;
            crate::f5::verify_f5_campaign_authority(connection)?;
            Ok(CampaignConfirmation { campaign_id: bind.campaign_id.to_owned(), resulting_revision: confirmation_revision })
        })
    }

    /// `begin_campaign_construction`: commit one begun lane and its exact basis revision.
    pub fn begin_campaign_construction(
        &self,
        begin: &BeginConstruction<'_>,
    ) -> StoreResult<CampaignConstructionBegun> {
        self.in_immediate(|connection| {
            self.ensure_campaign_schema(connection)?;
            match campaign_operation_hit_on(
                connection,
                begin.operation_id,
                "begin_campaign_construction",
                begin.payload_sha256,
            )? {
                Some(OperationResult::Begun(replay)) => return Ok(replay),
                Some(_) => return Err(coded("IdempotencyConflict", "Campaign operation result kind does not match")),
                None => {}
            }
            let rows = construction_basis(connection, begin.campaign_id)?;
            let (campaign, construction, turn) = (rows.campaign(), rows.construction(), rows.turn());
            let confirmation_revision = construction.int("confirmation_revision")?;
            if construction.get("begin_operation_id")? != &Value::Null {
                return Err(rejected("Campaign construction has already begun", "command_rejected"));
            }
            if campaign.text("lifecycle")? != "active" {
                return Err(rejected("Campaign lifecycle blocks construction", "command_rejected"));
            }
            if campaign.text("writer")? != WriterRole::Builder.as_str() {
                return Err(coded("WrongWriter", "Builder is not the authoritative campaign writer"));
            }
            if campaign.int("revision")? != begin.expected_revision {
                return Err(coded("StaleRevision", "Campaign construction expected revision is stale"));
            }
            let authority_lock_id = construction.text("authority_lock_id")?;
            if begin.expected_revision != confirmation_revision
                || campaign.text("current_lock_id")? != authority_lock_id
                || turn.text("status")? != "open"
                || turn.int("created_revision")? != confirmation_revision
            {
                return Err(rejected("Campaign construction confirmation basis has drifted", "command_rejected"));
            }
            let resulting_revision = confirmation_revision
                .checked_add(1)
                .ok_or_else(|| StoreError::Other("campaign revision overflow".into()))?;
            let mut result = JsonMap::new();
            result.insert("campaign_id".into(), begin.campaign_id.into());
            result.insert("lane".into(), begin.lane.into());
            result.insert("resulting_revision".into(), resulting_revision.into());
            let authority_turn_id = construction.text("authority_turn_id")?;
            insert_campaign_operation(
                connection,
                &NewOperation {
                    operation_id: begin.operation_id,
                    operation_kind: "begin_campaign_construction",
                    campaign_id: begin.campaign_id,
                    semantic_payload: begin.semantic_payload,
                    payload_sha256: begin.payload_sha256,
                    result: &result,
                    basis_turn_id: Some(&authority_turn_id),
                    basis_lock_id: Some(&authority_lock_id),
                    basis_revision: confirmation_revision,
                    resulting_revision,
                },
            )?;
            let updated = connection.execute(
                "UPDATE campaigns SET revision = ? WHERE campaign_id = ? AND revision = ? \
                 AND lifecycle = 'active' AND writer = 'builder' AND current_lock_id = ?",
                params![resulting_revision, begin.campaign_id, confirmation_revision, authority_lock_id],
            )?;
            if updated != 1 {
                return Err(coded("StaleRevision", "Campaign construction revision fence rejected admission"));
            }
            connection.execute(
                "UPDATE campaign_constructions SET lane = ?, canonical_input_json = ?, \
                 begin_operation_id = ?, begin_revision = ? WHERE campaign_id = ? AND begin_operation_id IS NULL",
                params![
                    begin.lane,
                    dump(&Json::Object(begin.canonical_input.clone())),
                    begin.operation_id,
                    resulting_revision,
                    begin.campaign_id
                ],
            )?;
            connection.execute(
                "INSERT INTO campaign_revisions \
                 (campaign_id, revision, action_id, activation_id, plan_id, handoff_id, construction_operation_id) \
                 VALUES (?, ?, NULL, NULL, NULL, NULL, ?)",
                params![begin.campaign_id, resulting_revision, begin.operation_id],
            )?;
            Ok(CampaignConstructionBegun {
                campaign_id: begin.campaign_id.to_owned(),
                lane: begin.lane.to_owned(),
                resulting_revision,
            })
        })
    }
}

/// `_package_lock_payload`.
pub fn package_lock_payload(package_lock: &PackageLock) -> Json {
    crate::stored_construction::operation_lock_payload(package_lock)
}

impl StoreHandle {
    /// `AuthorityOwner.append_agent_message`: the session's campaign work, then
    /// `SessionApplication.append_message` under the session lane.
    pub async fn append_agent_message(
        &self,
        session_id: &str,
        message_id: &str,
        kind: SessionMessageKind,
        content: &JsonMap,
    ) -> StoreResult<SessionMessage> {
        let _work = self.session_campaign_work(session_id).await?;
        let _session = self.lanes().sessions.acquire(session_id).await;
        let (session_id, message_id, content) =
            (session_id.to_owned(), message_id.to_owned(), content.clone());
        self.call(move |store| store.append_agent_message(&session_id, &message_id, kind, &content, None))
            .await
    }
}
