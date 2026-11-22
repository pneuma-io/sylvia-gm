//! Reversible campaign lifecycle transitions in the store: `SQLiteAuthorityStore.
//! commit_lifecycle_operation` (rename, archive, trash and restore, each with its operation
//! receipt, its transition row and its revision) and `_lifecycle_old_authority_is_intact`, which
//! decides an uncertain commit. The application half (`AuthorityOwner._lifecycle_operation`) is
//! `sylvia_app::authority::lifecycle`.
//!
//! The commit seams `_before_lifecycle_commit` and `_after_lifecycle_commit` are
//! [`crate::store::Faults::before_lifecycle_commit`] and `after_lifecycle_commit`.

use crate::campaigns::{named_rows, parse, Named};
use crate::error::{authority_coded, StoreError, StoreResult};
use crate::guided_start::{insert_campaign_operation, NewOperation};
use crate::packages::has_only_draft_lifecycle_history;
use crate::pytext::py_str;
use crate::sessions::{restore_stopped_session, update_session_status};
use crate::store::AuthorityStore;
use crate::stored_construction::OperationResult;
use rusqlite::types::Value;
use rusqlite::{params, Connection};
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{CampaignLifecycleResult, LifecycleState, WriterRole};
use sylvia_domain::session::SessionStatus;

/// `_UNBOUND_CAMPAIGN_LOCK_HASH`.
const UNBOUND_CAMPAIGN_LOCK_HASH: &str = "unbound";

/// `commit_lifecycle_operation`'s keyword arguments.
#[derive(Debug, Clone)]
pub struct LifecycleCommit<'a> {
    pub operation_id: &'a str,
    pub operation_kind: &'a str,
    pub semantic_payload: &'a JsonMap,
    pub payload_sha256: &'a str,
    pub campaign_id: &'a str,
    pub expected_revision: i64,
    pub display_name: Option<&'a str>,
    pub designated_session_id: Option<&'a str>,
}

/// The authority a transition replaces, read before it changes anything: the campaign's
/// columns, its lifecycle state and its sessions.
#[derive(Debug, Clone, PartialEq)]
struct OldAuthority {
    campaign: Vec<Value>,
    state: Vec<Value>,
    sessions: Vec<Vec<Value>>,
}

const CAMPAIGN_COLUMNS: [&str; 5] = ["display_name", "lifecycle", "writer", "revision", "current_lock_id"];
const STATE_COLUMNS: [&str; 5] = [
    "retained_active_writer",
    "designated_session_id",
    "tombstone_prior_lifecycle",
    "tombstone_prior_writer",
    "tombstone_prior_session_id",
];
const SESSIONS_QUERY: &str =
    "SELECT session_id, status, payload_sha256 FROM agent_sessions WHERE campaign_id = ? ORDER BY session_id";

fn rejected(message: &str) -> StoreError {
    crate::actions::rejected(message, "command_rejected")
}

fn stale(message: &str) -> StoreError {
    StoreError::Coded(authority_coded("StaleRevision", message))
}

fn idempotency_conflict() -> StoreError {
    StoreError::Coded(authority_coded("IdempotencyConflict", "Campaign operation result kind does not match"))
}

fn first<'a>(columns: &'a [String], rows: Vec<Vec<Value>>) -> Option<Named<'a>> {
    rows.into_iter().next().map(|values| Named { columns, values })
}

fn values_of(row: &Named<'_>, columns: &[&str]) -> StoreResult<Vec<Value>> {
    columns.iter().map(|column| row.get(column).cloned()).collect()
}

fn lifecycle_of(text: &str) -> StoreResult<LifecycleState> {
    parse(text, LifecycleState::parse, "LifecycleState")
}

fn writer_of(text: &str) -> StoreResult<WriterRole> {
    parse(text, WriterRole::parse, "WriterRole")
}

/// The lifecycle receipt among the receipts `_campaign_operation_hit` can return.
fn lifecycle_hit(hit: Option<OperationResult>) -> StoreResult<Option<CampaignLifecycleResult>> {
    match hit {
        None => Ok(None),
        Some(OperationResult::Lifecycle(result)) => Ok(Some(result)),
        Some(_) => Err(idempotency_conflict()),
    }
}

/// The nested `stopped_session(required_id)`: no active child work, exactly the writer's one
/// top-level session (which must be `required_id`), then every active top-level session stopped.
fn stopped_session(
    connection: &Connection,
    campaign_id: &str,
    writer: WriterRole,
    required_id: Option<&str>,
) -> StoreResult<Option<String>> {
    let children = named_rows(
        connection,
        "SELECT 1 FROM agent_sessions WHERE campaign_id = ? AND parent_session_id IS NOT NULL \
         AND status IN ('active', 'review_required') LIMIT 1",
        &[&campaign_id],
    )?
    .1;
    if !children.is_empty() {
        return Err(rejected("Campaign lifecycle has active child work"));
    }
    let rows = named_rows(
        connection,
        "SELECT session_id, status FROM agent_sessions WHERE campaign_id = ? \
         AND parent_session_id IS NULL AND role = ? ORDER BY session_id",
        &[&campaign_id, &writer.as_str()],
    )?
    .1;
    if rows.len() > 1 {
        return Err(rejected("Campaign lifecycle session authority is ambiguous"));
    }
    let Some(row) = rows.first() else {
        if required_id.is_some() {
            return Err(rejected("Designated campaign session is not exact"));
        }
        return Ok(None);
    };
    let session_id = py_str(&row[0]);
    if required_id != Some(session_id.as_str()) {
        return Err(rejected("Designated campaign session is not exact"));
    }
    let running = named_rows(
        connection,
        "SELECT session_id FROM agent_sessions WHERE campaign_id = ? AND parent_session_id IS NULL \
         AND status IN ('active', 'review_required') ORDER BY session_id",
        &[&campaign_id],
    )?
    .1;
    for row in running {
        update_session_status(connection, &py_str(&row[0]), SessionStatus::Stopped)?;
    }
    Ok(Some(session_id))
}

/// `None if value is None else str(value)`.
fn optional_text(value: &Value) -> Option<String> {
    match value {
        Value::Null => None,
        other => Some(py_str(other)),
    }
}

impl AuthorityStore {
    /// `commit_lifecycle_operation`: one reversible lifecycle transition and its receipt in one
    /// transaction. An exact replay returns the stored receipt.
    pub fn commit_lifecycle_operation(
        &self,
        commit: &LifecycleCommit<'_>,
    ) -> StoreResult<CampaignLifecycleResult> {
        let connection = self.connect()?;
        let mut commit_started = false;
        let mut old_authority: Option<OldAuthority> = None;
        let outcome =
            self.lifecycle_transaction(&connection, commit, &mut commit_started, &mut old_authority);
        let error = match outcome {
            Ok(result) => return Ok(result),
            Err(error) => error,
        };
        if !connection.is_autocommit() {
            let _ = connection.execute_batch("ROLLBACK");
        }
        drop(connection);
        if !commit_started {
            return Err(error);
        }
        if let Some(resolved) = lifecycle_hit(self.campaign_operation_hit(
            commit.operation_id,
            commit.operation_kind,
            commit.payload_sha256,
        )?)? {
            return Ok(resolved);
        }
        if let Some(old) = &old_authority {
            if self.lifecycle_old_authority_is_intact(commit.campaign_id, commit.operation_id, old)? {
                return Err(rejected("Campaign lifecycle commit was confirmed rolled back"));
            }
        }
        Err(rejected("Campaign lifecycle commit outcome is uncertain"))
    }

    fn lifecycle_transaction(
        &self,
        connection: &Connection,
        commit: &LifecycleCommit<'_>,
        commit_started: &mut bool,
        old_authority: &mut Option<OldAuthority>,
    ) -> StoreResult<CampaignLifecycleResult> {
        let campaign_id = commit.campaign_id;
        connection.execute_batch("BEGIN IMMEDIATE")?;
        self.ensure_campaign_schema(connection)?;
        let replay = crate::guided_start::campaign_operation_hit_on(
            connection,
            commit.operation_id,
            commit.operation_kind,
            commit.payload_sha256,
        )?;
        if let Some(replay) = lifecycle_hit(replay)? {
            connection.execute_batch("ROLLBACK")?;
            return Ok(replay);
        }
        let (campaign_columns, campaign_rows) =
            named_rows(connection, "SELECT * FROM campaigns WHERE campaign_id = ?", &[&campaign_id])?;
        let (state_columns, state_rows) = named_rows(
            connection,
            "SELECT * FROM campaign_lifecycle_state WHERE campaign_id = ?",
            &[&campaign_id],
        )?;
        let (Some(campaign), Some(state)) =
            (first(&campaign_columns, campaign_rows), first(&state_columns, state_rows))
        else {
            return Err(rejected("Campaign lifecycle authority is unavailable"));
        };
        let lifecycle = lifecycle_of(&campaign.text("lifecycle")?)?;
        let writer = writer_of(&campaign.text("writer")?)?;
        if campaign.int("revision")? != commit.expected_revision {
            return Err(stale("Campaign lifecycle expected revision is stale"));
        }
        *old_authority = Some(OldAuthority {
            campaign: values_of(&campaign, &CAMPAIGN_COLUMNS)?,
            state: values_of(&state, &STATE_COLUMNS)?,
            sessions: named_rows(connection, SESSIONS_QUERY, &[&campaign_id])?.1,
        });
        let mut session_id: Option<String> = None;
        let mut to_lifecycle = lifecycle;
        let mut to_writer = writer;
        match commit.operation_kind {
            "rename_campaign" => {
                if commit.display_name.is_none() {
                    return Err(rejected("Campaign display name is unavailable"));
                }
            }
            kind @ ("archive_campaign" | "trash_campaign") => {
                let archive = kind == "archive_campaign";
                let allowed = if archive {
                    lifecycle == LifecycleState::Active
                } else {
                    matches!(lifecycle, LifecycleState::Active | LifecycleState::Archived)
                };
                if !allowed {
                    return Err(rejected("Campaign lifecycle transition is not admissible"));
                }
                if lifecycle == LifecycleState::Active {
                    if archive
                        && !named_rows(
                            connection,
                            "SELECT 1 FROM actions WHERE campaign_id = ? \
                             AND status NOT IN ('committed', 'failed') LIMIT 1",
                            &[&campaign_id],
                        )?
                        .1
                        .is_empty()
                    {
                        return Err(rejected("Campaign lifecycle is blocked by incomplete action work"));
                    }
                    // Open turns waiting for the player survive archive/restore unchanged.
                    session_id =
                        stopped_session(connection, campaign_id, writer, commit.designated_session_id)?;
                } else {
                    session_id = optional_text(state.get("designated_session_id")?);
                }
                to_lifecycle = if archive { LifecycleState::Archived } else { LifecycleState::Tombstoned };
                to_writer = WriterRole::None;
                if archive {
                    connection.execute(
                        "UPDATE campaign_lifecycle_state SET retained_active_writer = ?, \
                         designated_session_id = ? WHERE campaign_id = ?",
                        params![writer.as_str(), session_id, campaign_id],
                    )?;
                } else {
                    connection.execute(
                        "UPDATE campaign_lifecycle_state SET tombstone_prior_lifecycle = ?, \
                         tombstone_prior_writer = ?, tombstone_prior_session_id = ? WHERE campaign_id = ?",
                        params![lifecycle.as_str(), writer.as_str(), session_id, campaign_id],
                    )?;
                }
            }
            "restore_campaign" => {
                match lifecycle {
                    LifecycleState::Archived => {
                        let retained = state.get("retained_active_writer")?;
                        if *retained == Value::Null {
                            return Err(rejected("Archived campaign has no restorable writer authority"));
                        }
                        to_lifecycle = LifecycleState::Active;
                        to_writer = writer_of(&py_str(retained))?;
                        session_id = optional_text(state.get("designated_session_id")?);
                        if commit.designated_session_id != session_id.as_deref() {
                            return Err(rejected(
                                "Restore designated session does not match archived campaign",
                            ));
                        }
                    }
                    LifecycleState::Tombstoned => {
                        let prior = state.get("tombstone_prior_lifecycle")?;
                        let prior_writer = state.get("tombstone_prior_writer")?;
                        if *prior == Value::Null || *prior_writer == Value::Null {
                            return Err(rejected("Tombstoned campaign has no restorable prior state"));
                        }
                        to_lifecycle = lifecycle_of(&py_str(prior))?;
                        to_writer = writer_of(&py_str(prior_writer))?;
                        session_id = optional_text(state.get("tombstone_prior_session_id")?);
                        connection.execute(
                            "UPDATE campaign_lifecycle_state SET tombstone_prior_lifecycle = NULL, \
                             tombstone_prior_writer = NULL, tombstone_prior_session_id = NULL \
                             WHERE campaign_id = ?",
                            params![campaign_id],
                        )?;
                    }
                    _ => return Err(rejected("active campaign cannot be restored")),
                }
                if to_lifecycle == LifecycleState::Active {
                    if let Some(id) = &session_id {
                        let restored = restore_stopped_session(connection, id)?;
                        if restored.campaign_id() != Some(campaign_id)
                            || restored.fields().parent_session_id.is_some()
                            || restored.fields().role != to_writer
                        {
                            return Err(rejected("Designated campaign session cannot be restored"));
                        }
                    }
                }
            }
            _ => return Err(rejected("Campaign lifecycle operation kind is invalid")),
        }
        // Python's integers do not overflow; SQLite's binding refuses one past 64 bits.
        let resulting_revision = commit
            .expected_revision
            .checked_add(1)
            .ok_or_else(|| StoreError::Other("Python int too large to convert to SQLite INTEGER".into()))?;
        let display_name = match commit.display_name {
            Some(name) => name.to_owned(),
            None => campaign.text("display_name")?,
        };
        let updated = connection.execute(
            "UPDATE campaigns SET display_name = ?, lifecycle = ?, writer = ?, revision = ? \
             WHERE campaign_id = ? AND revision = ? AND lifecycle = ? AND writer = ?",
            params![
                display_name,
                to_lifecycle.as_str(),
                to_writer.as_str(),
                resulting_revision,
                campaign_id,
                commit.expected_revision,
                lifecycle.as_str(),
                writer.as_str()
            ],
        )?;
        if updated != 1 {
            return Err(stale("Campaign lifecycle revision fence rejected transition"));
        }
        let turn = named_rows(
            connection,
            "SELECT turn_id FROM turns WHERE campaign_id = ? ORDER BY created_revision DESC, turn_id DESC LIMIT 1",
            &[&campaign_id],
        )?
        .1
        .into_iter()
        .next()
        .map(|row| py_str(&row[0]));
        let current_lock = campaign.get("current_lock_id")?;
        let unbound = turn.is_none()
            && *current_lock == Value::Null
            && campaign.text("lock_hash")? == UNBOUND_CAMPAIGN_LOCK_HASH
            && has_only_draft_lifecycle_history(connection, campaign_id, commit.expected_revision)?;
        if !unbound && (turn.is_none() || *current_lock == Value::Null) {
            return Err(rejected("Campaign lifecycle basis is unavailable"));
        }
        let mut result = JsonMap::new();
        result.insert("campaign_id".into(), campaign_id.into());
        result.insert("lifecycle".into(), to_lifecycle.as_str().into());
        result.insert("writer".into(), to_writer.as_str().into());
        result.insert("resulting_revision".into(), resulting_revision.into());
        result.insert("session_id".into(), session_id.clone().map_or(Json::Null, Json::String));
        let lock_id = (!unbound).then(|| py_str(current_lock));
        insert_campaign_operation(
            connection,
            &NewOperation {
                operation_id: commit.operation_id,
                operation_kind: commit.operation_kind,
                campaign_id,
                semantic_payload: commit.semantic_payload,
                payload_sha256: commit.payload_sha256,
                result: &result,
                basis_turn_id: if unbound { None } else { turn.as_deref() },
                basis_lock_id: lock_id.as_deref(),
                basis_revision: commit.expected_revision,
                resulting_revision,
            },
        )?;
        connection.execute(
            "INSERT INTO campaign_lifecycle_transitions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            params![
                commit.operation_id,
                campaign_id,
                commit.operation_kind,
                commit.expected_revision,
                resulting_revision,
                lifecycle.as_str(),
                to_lifecycle.as_str(),
                writer.as_str(),
                to_writer.as_str(),
                session_id
            ],
        )?;
        connection.execute(
            "INSERT INTO campaign_revisions \
             (campaign_id, revision, action_id, activation_id, plan_id, handoff_id, \
             construction_operation_id, lifecycle_operation_id) \
             VALUES (?, ?, NULL, NULL, NULL, NULL, NULL, ?)",
            params![campaign_id, resulting_revision, commit.operation_id],
        )?;
        let lifecycle_result = CampaignLifecycleResult {
            campaign_id: campaign_id.to_owned(),
            lifecycle: to_lifecycle,
            writer: to_writer,
            resulting_revision,
            session_id,
        };
        if let Some(fault) = &self.faults.before_lifecycle_commit {
            fault()?;
        }
        *commit_started = true;
        connection.execute_batch("COMMIT")?;
        if let Some(fault) = &self.faults.after_lifecycle_commit {
            fault()?;
        }
        Ok(lifecycle_result)
    }

    /// `pending_mechanics_action(campaign_id) is not None`, for `_reject_pending_mechanics_choice`.
    /// Declared reduction until the mechanics-choice family lands: the row is not parsed
    /// (`_pending_from_row`), so a malformed row counts as pending here.
    pub fn mechanics_choice_is_pending(&self, campaign_id: &str) -> StoreResult<bool> {
        let connection = self.connect_read_only()?;
        let table = named_rows(
            &connection,
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'pending_mechanics_actions'",
            &[],
        )?
        .1;
        if table.is_empty() {
            return Ok(false);
        }
        Ok(!named_rows(
            &connection,
            "SELECT * FROM pending_mechanics_actions WHERE campaign_id = ?",
            &[&campaign_id],
        )?
        .1
        .is_empty())
    }

    /// `_lifecycle_old_authority_is_intact`: whether nothing of the transition was committed.
    fn lifecycle_old_authority_is_intact(
        &self,
        campaign_id: &str,
        operation_id: &str,
        old: &OldAuthority,
    ) -> StoreResult<bool> {
        let connection = self.connect()?;
        connection.execute_batch("BEGIN")?;
        let exists = |query: &str, values: &[&dyn rusqlite::ToSql]| -> StoreResult<bool> {
            Ok(!named_rows(&connection, query, values)?.1.is_empty())
        };
        let campaign = named_rows(
            &connection,
            "SELECT display_name, lifecycle, writer, revision, current_lock_id FROM campaigns WHERE campaign_id = ?",
            &[&campaign_id],
        )?
        .1;
        let state = named_rows(
            &connection,
            "SELECT retained_active_writer, designated_session_id, tombstone_prior_lifecycle, \
             tombstone_prior_writer, tombstone_prior_session_id FROM campaign_lifecycle_state WHERE campaign_id = ?",
            &[&campaign_id],
        )?
        .1;
        let sessions = named_rows(&connection, SESSIONS_QUERY, &[&campaign_id])?.1;
        let operation = exists("SELECT 1 FROM campaign_operations WHERE operation_id = ?", &[&operation_id])?;
        let transition =
            exists("SELECT 1 FROM campaign_lifecycle_transitions WHERE operation_id = ?", &[&operation_id])?;
        let revision = exists(
            "SELECT 1 FROM campaign_revisions WHERE campaign_id = ? AND lifecycle_operation_id = ?",
            &[&campaign_id, &operation_id],
        )?;
        let intact = campaign.first() == Some(&old.campaign)
            && state.first() == Some(&old.state)
            && sessions == old.sessions
            && !operation
            && !transition
            && !revision;
        if !connection.is_autocommit() {
            let _ = connection.execute_batch("ROLLBACK");
        }
        Ok(intact)
    }
}
