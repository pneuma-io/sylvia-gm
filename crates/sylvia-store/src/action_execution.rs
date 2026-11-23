//! The store reads and writes an action's execution makes before its commit, from
//! `sqlite_authority.py`: `action_basis`, `declared_package_tool`, `mechanic_mutations_are_owned`
//! (with `_validate_participant_mutations` and `sqlite_packages.participant_owner_is_current`, which
//! `commit_action` checks again), and `bind_transport_alias`. The pending mechanics choices are
//! [`crate::mechanics_choices`].

use crate::actions::{load_payload, owner_for_mutation, rejected, MutationActor};
use crate::campaigns::{named_rows, Named};
use crate::error::{authority_coded, StoreError, StoreResult};
use crate::pytext::{py_int, py_str};
use crate::schema::CAMPAIGN_SCHEMA_VERSION;
use crate::store::AuthorityStore;
use rusqlite::types::Value;
use sylvia_base::Json;
use sylvia_domain::authority::{
    ActionReceipt, ActionStatus, CampaignRecord, MechanicOutcome, PackageLock, Participant, TurnStatus,
};
use sylvia_domain::package_library::ToolDeclaration;

/// `ActionBasis`: what an action is admitted against.
#[derive(Debug, Clone, PartialEq)]
pub struct ActionBasis {
    pub campaign: CampaignRecord,
    pub turn_status: TurnStatus,
    pub state: sylvia_base::JsonMap,
    pub package_lock: PackageLock,
    pub participants: Vec<Participant>,
}

fn idempotency_conflict(message: &str) -> StoreError {
    StoreError::Coded(authority_coded("IdempotencyConflict", message))
}

fn receipt(campaign_id: &str, action_id: &str, row: &Named<'_>) -> StoreResult<ActionReceipt> {
    let status = row.text("status")?;
    Ok(ActionReceipt {
        campaign_id: campaign_id.to_owned(),
        action_id: action_id.to_owned(),
        status: ActionStatus::parse(&status)
            .ok_or_else(|| crate::error::value_error(format!("'{status}' is not a valid ActionStatus")))?,
        result: load_payload(&row.text("result_json")?)?,
        resulting_revision: py_int(row.get("resulting_revision")?)
            .ok_or_else(|| crate::error::value_error("invalid literal for int()"))?,
    })
}

impl AuthorityStore {
    /// `action_basis(campaign_id, turn_id)`: the campaign, its TURN's status, its state, its
    /// lock and its participants.
    pub fn action_basis(&self, campaign_id: &str, turn_id: &str) -> StoreResult<ActionBasis> {
        let connection = self.connect()?;
        let (columns, campaigns) =
            named_rows(&connection, "SELECT * FROM campaigns WHERE campaign_id = ?", &[&campaign_id])?;
        let Some(values) = campaigns.into_iter().next() else {
            return Err(rejected("Campaign is unavailable", "command_rejected"));
        };
        let campaign = crate::guided::campaign_record(&Named { columns: &columns, values })?;
        let turn = named_rows(
            &connection,
            "SELECT status FROM turns WHERE campaign_id = ? AND turn_id = ?",
            &[&campaign_id, &turn_id],
        )?
        .1;
        let Some(turn) = turn.into_iter().next() else {
            return Err(rejected("TURN is unavailable", "command_rejected"));
        };
        let status = py_str(&turn[0]);
        let turn_status = TurnStatus::parse(&status)
            .ok_or_else(|| crate::error::value_error(format!("'{status}' is not a valid TurnStatus")))?;
        let state = crate::snapshots::read_state(&connection, campaign_id)?;
        let version: i64 = connection.query_row("PRAGMA user_version", [], |row| row.get(0))?;
        let has_f5 = version == CAMPAIGN_SCHEMA_VERSION;
        let package_lock = crate::stored::read_lock(&connection, campaign_id)?;
        let (columns, rows) = named_rows(
            &connection,
            "SELECT * FROM participants WHERE campaign_id = ? ORDER BY participant_id",
            &[&campaign_id],
        )?;
        let participants = rows
            .into_iter()
            .map(|values| crate::snapshots::participant(&Named { columns: &columns, values }, has_f5))
            .collect::<StoreResult<Vec<_>>>()?;
        Ok(ActionBasis { campaign, turn_status, state, package_lock, participants })
    }

    /// `declared_package_tool(package_id, version, sha256, tool_name)`.
    pub fn declared_package_tool(
        &self,
        package_id: &str,
        version: &str,
        sha256: &str,
        tool_name: &str,
    ) -> StoreResult<Option<ToolDeclaration>> {
        crate::catalog_reads::declared_tool(&self.connect()?, (package_id, version, sha256, tool_name))
    }

    /// `mechanic_mutations_are_owned(...)`: whether every captured state mutation's namespace is
    /// the acting package's to write, and every live character replacement keeps its owning exact
    /// Ruleset and schema (`_validate_participant_mutations`). A campaign schema behind the current
    /// one is advanced first when there are either, as Python does.
    pub fn mechanic_mutations_are_owned(
        &self,
        campaign_id: &str,
        package_id: &str,
        package_version: &str,
        package_sha256: &str,
        outcome: &MechanicOutcome,
    ) -> StoreResult<bool> {
        if !outcome.mutations.is_empty() || !outcome.participant_mutations.is_empty() {
            let connection = self.connect()?;
            connection.execute_batch("BEGIN IMMEDIATE")?;
            let advanced = self.ensure_campaign_schema(&connection).and_then(|()| {
                connection.execute_batch("COMMIT")?;
                Ok(())
            });
            if advanced.is_err() && !connection.is_autocommit() {
                let _ = connection.execute_batch("ROLLBACK");
            }
            advanced?;
        }
        let connection = self.connect_read_only()?;
        let actor = MutationActor { campaign_id, package_id, package_version, package_sha256 };
        let checked = outcome
            .mutations
            .iter()
            .try_for_each(|mutation| owner_for_mutation(&connection, &actor, &mutation.namespace).map(drop))
            .and_then(|()| validate_participant_mutations(&connection, &actor, outcome));
        match checked {
            Ok(()) => Ok(true),
            // `except CommandRejected: return False`, which takes its subclasses too.
            Err(StoreError::Coded(error)) if is_command_rejected(error.error_type) => Ok(false),
            Err(error) => Err(error),
        }
    }

    /// `bind_transport_alias(...)`: a new transport key reserved for a durable action's replay.
    pub fn bind_transport_alias(
        &self,
        campaign_id: &str,
        action_id: &str,
        idempotency_key: &str,
        payload_hash: &str,
    ) -> StoreResult<ActionReceipt> {
        let connection = self.connect()?;
        let outcome = (|| -> StoreResult<ActionReceipt> {
            connection.execute_batch("BEGIN IMMEDIATE")?;
            crate::actions::require_active_campaign(&connection, campaign_id)?;
            let (columns, existing) = named_rows(
                &connection,
                "SELECT payload_hash, action_id, status, result_json, resulting_revision \
                 FROM idempotency_records WHERE campaign_id = ? AND idempotency_key = ?",
                &[&campaign_id, &idempotency_key],
            )?;
            if let Some(values) = existing.into_iter().next() {
                let row = Named { columns: &columns, values };
                if row.text("payload_hash")? != payload_hash || row.text("action_id")? != action_id {
                    return Err(idempotency_conflict("Idempotency key payload does not match"));
                }
                connection.execute_batch("ROLLBACK")?;
                return receipt(campaign_id, action_id, &row);
            }
            let (columns, actions) = named_rows(
                &connection,
                "SELECT payload_hash, status, result_json, resulting_revision FROM actions \
                 WHERE campaign_id = ? AND action_id = ?",
                &[&campaign_id, &action_id],
            )?;
            let Some(values) = actions.into_iter().next() else {
                return Err(idempotency_conflict("Action identity payload does not match"));
            };
            let row = Named { columns: &columns, values };
            if row.text("payload_hash")? != payload_hash {
                return Err(idempotency_conflict("Action identity payload does not match"));
            }
            let receipt = receipt(campaign_id, action_id, &row)?;
            let resulting: Value = row.get("resulting_revision")?.clone();
            connection.execute(
                "INSERT INTO idempotency_records VALUES (?, ?, ?, ?, ?, ?, ?)",
                rusqlite::params![
                    campaign_id,
                    idempotency_key,
                    payload_hash,
                    action_id,
                    row.text("status")?,
                    row.text("result_json")?,
                    resulting,
                ],
            )?;
            connection.execute_batch("COMMIT")?;
            Ok(receipt)
        })();
        if !connection.is_autocommit() {
            let _ = connection.execute_batch("ROLLBACK");
        }
        outcome
    }
}

/// Whether a Python class is `CommandRejected` or one of its subclasses (`errors.py`). Every
/// store rejection carries its class (`DomainError::rejection` always sets it), so an untyped
/// coded error is not one, and propagates.
fn is_command_rejected(error_type: Option<&str>) -> bool {
    matches!(error_type, Some("CommandRejected" | "MechanicsRejected" | "RulesetPreconditionRejected"))
}

/// `_validate_participant_mutations(connection, campaign_id, package_id, package_version,
/// package_sha256, outcome)`: each live replacement's participant must be validated, still owned
/// by the acting exact Ruleset through its schema's lineage, and the new data must match the
/// participant's exact schema.
pub(crate) fn validate_participant_mutations(
    connection: &rusqlite::Connection,
    actor: &MutationActor<'_>,
    outcome: &MechanicOutcome,
) -> StoreResult<()> {
    for mutation in &outcome.participant_mutations {
        let (columns, rows) = named_rows(
            connection,
            "SELECT p.*, d.schema_json FROM participants p              JOIN package_participant_artifacts d ON d.package_id = p.package_id              AND d.version = p.package_version AND d.content_sha256 = p.package_sha256              AND d.kind = p.kind AND d.schema_id = p.schema_id              AND d.schema_sha256 = p.schema_sha256              WHERE p.campaign_id = ? AND p.participant_id = ?",
            &[&actor.campaign_id, &mutation.participant_id],
        )?;
        let row = rows.into_iter().next().map(|values| Named { columns: &columns, values });
        let owned = match &row {
            None => false,
            Some(row) => {
                matches!(row.get("validation_status")?, Value::Text(status) if status == "validated")
                    && participant_owner_is_current(
                        connection,
                        actor.campaign_id,
                        (
                            &row.text("package_id")?,
                            &row.text("package_version")?,
                            &row.text("package_sha256")?,
                        ),
                        (actor.package_id, actor.package_version, actor.package_sha256),
                        (&row.text("kind")?, &row.text("schema_id")?, &row.text("schema_sha256")?),
                    )?
            }
        };
        let Some(row) = row.filter(|_| owned) else {
            return Err(rejected(
                "Live character updates require the owning exact Ruleset and schema",
                "participant_mutation_unowned",
            ));
        };
        let schema = load_payload(&row.text("schema_json")?)?;
        sylvia_domain::schema_admission::validate_json_value(
            &Json::Object(mutation.data.clone()),
            &schema,
            &format!("Participant {}", mutation.participant_id),
        )?;
    }
    Ok(())
}

/// `sqlite_packages.participant_owner_is_current(connection, campaign_id=..., source_identity=...,
/// actor_identity=..., kind=..., schema_id=..., schema_sha256=...)`: the acting package is the
/// current exact authority along the participant's committed lineage, and every package on that
/// lineage still declares the participant's unchanged character contract.
fn participant_owner_is_current(
    connection: &rusqlite::Connection,
    campaign_id: &str,
    source: (&str, &str, &str),
    actor: (&str, &str, &str),
    (kind, schema_id, schema_sha256): (&str, &str, &str),
) -> StoreResult<bool> {
    let entries = crate::catalog_reads::compatible_binding_entries(connection, campaign_id, source)?;
    let Some(first) = entries.first() else { return Ok(false) };
    if (first.package_id(), first.version(), first.sha256()) != actor {
        return Ok(false);
    }
    let Some(declaration) = crate::packages::exact_participant_declarations(connection, source)?
        .into_iter()
        .find(|item| item.kind == kind && item.schema_id == schema_id && item.schema_sha256 == schema_sha256)
    else {
        return Ok(false);
    };
    for entry in &entries {
        let declared = crate::packages::exact_participant_declarations(
            connection,
            (entry.package_id(), entry.version(), entry.sha256()),
        )?;
        if !declared.contains(&declaration) {
            return Ok(false);
        }
    }
    Ok(true)
}
