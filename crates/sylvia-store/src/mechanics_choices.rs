//! Pending mechanics choices, from `sqlite_authority.py`: `_pending_from_row`,
//! `pending_mechanics_action`, `pending_mechanics_hit`, `reserve_mechanics_choice`,
//! `advance_mechanics_choice` and `mechanics_choice_resolution_receipt`. Committing an answered
//! choice is `commit_action`'s resolution branch ([`crate::actions`]).
//!
//! Every descriptor and resolution payload is hashed over Python's `_dump` (sorted keys, compact,
//! not ASCII-escaped), so the stored SHA-256 values are Python's.

use crate::actions::{load_payload, rejected};
use crate::campaigns::{named_rows, Named};
use crate::error::{authority_coded, schema_identity, StoreError, StoreResult};
use crate::pytext::{py_int, py_str};
use crate::store::AuthorityStore;
use rusqlite::types::Value;
use rusqlite::Connection;
use sylvia_base::pyjson::{dumps, Dump};
use sylvia_base::{hash::sha256_hex, Json, JsonMap};
use sylvia_domain::authority::{
    ActionReceipt, ActionStatus, AwaitingChoiceReceipt, LifecycleState, MechanicsChoiceAnswer,
    MechanicsChoiceRequest, PendingMechanicsAction, TurnStatus, WriterRole,
};
use sylvia_domain::mechanics_choices::{
    answer_payload, answers_from_payload, choice_from_payload, choice_payload,
};

/// `_dump(value)`.
pub(crate) fn dump(value: &Json) -> String {
    dumps(value, Dump::CANONICAL)
}

fn idempotency_conflict(message: &str) -> StoreError {
    StoreError::Coded(authority_coded("IdempotencyConflict", message))
}

fn stale(message: &str) -> StoreError {
    StoreError::Coded(authority_coded("StaleRevision", message))
}

fn int(value: &Value) -> StoreResult<i64> {
    py_int(value).ok_or_else(|| crate::error::value_error("invalid literal for int()"))
}

fn optional_text(value: &Value) -> Option<String> {
    match value {
        Value::Null => None,
        other => Some(py_str(other)),
    }
}

/// The descriptor of one request: its identity, prompt and options.
pub(crate) fn request_descriptor(choice: &MechanicsChoiceRequest) -> JsonMap {
    choice_payload(choice)
}

/// `sha256(_dump(owned_payload({"choice_id", "prompt", "options"})))` of one request.
pub(crate) fn request_descriptor_sha256(choice: &MechanicsChoiceRequest) -> String {
    sha256_hex(dump(&Json::Object(request_descriptor(choice))).as_bytes())
}

/// `_pending_from_row(row)`.
pub(crate) fn pending_from_row(row: &Named<'_>) -> StoreResult<PendingMechanicsAction> {
    let descriptor_json = row.text("descriptor_json")?;
    if sha256_hex(descriptor_json.as_bytes()) != row.text("descriptor_sha256")? {
        return Err(schema_identity("Stored mechanics choice descriptor hash differs"));
    }
    let descriptor = load_payload(&descriptor_json)?;
    if descriptor.get("player_action_origin").is_some_and(|origin| !origin.is_object()) {
        return Err(schema_identity("Stored player action origin is invalid"));
    }
    let invalid = || schema_identity("Stored mechanics choice descriptor is invalid");
    let session = optional_text(row.get("initiating_session_id")?);
    let parsed = (|| -> Option<(MechanicsChoiceRequest, Vec<MechanicsChoiceAnswer>)> {
        let mut request = JsonMap::new();
        for key in ["choice_id", "prompt", "options"] {
            request.insert(key.into(), descriptor.get(key)?.clone());
        }
        let choice = choice_from_payload(&Json::Object(request)).ok()?;
        let answers = answers_from_payload(descriptor.get("prior_answers").unwrap_or(&Json::Null)).ok()?;
        Some((choice, answers))
    })();
    let Some((choice, prior_answers)) = parsed else { return Err(invalid()) };
    if prior_answers.iter().any(|answer| answer.session_id != session) {
        return Err(invalid());
    }
    if choice.choice_id != row.text("choice_id")? {
        return Err(invalid());
    }
    let writer = row.text("writer")?;
    Ok(PendingMechanicsAction {
        campaign_id: row.text("campaign_id")?,
        action_id: row.text("action_id")?,
        turn_id: row.text("turn_id")?,
        basis_revision: int(row.get("basis_revision")?)?,
        writer: WriterRole::parse(&writer)
            .ok_or_else(|| crate::error::value_error(format!("'{writer}' is not a valid WriterRole")))?,
        package_id: row.text("package_id")?,
        package_version: row.text("package_version")?,
        package_sha256: row.text("package_sha256")?,
        lock_id: row.text("lock_id")?,
        tool_name: row.text("tool_name")?,
        parameters: load_payload(&row.text("parameters_json")?)?,
        payload_hash: row.text("payload_hash")?,
        initiating_session_id: session,
        choice,
        descriptor_sha256: row.text("descriptor_sha256")?,
        player_action_origin: match descriptor.get("player_action_origin") {
            Some(Json::Object(origin)) => Some(origin.clone()),
            _ => None,
        },
        prior_answers,
    })
}

fn table_exists(connection: &Connection, name: &str) -> StoreResult<bool> {
    Ok(!named_rows(connection, "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", &[&name])?
        .1
        .is_empty())
}

fn pending_on(connection: &Connection, campaign_id: &str) -> StoreResult<Option<PendingMechanicsAction>> {
    let (columns, rows) = named_rows(
        connection,
        "SELECT * FROM pending_mechanics_actions WHERE campaign_id = ?",
        &[&campaign_id],
    )?;
    rows.into_iter().next().map(|values| pending_from_row(&Named { columns: &columns, values })).transpose()
}

/// `AwaitingChoiceReceipt` for a pending choice (`_awaiting_choice_receipt`).
pub fn awaiting_receipt(pending: &PendingMechanicsAction) -> AwaitingChoiceReceipt {
    AwaitingChoiceReceipt {
        campaign_id: pending.campaign_id.clone(),
        action_id: pending.action_id.clone(),
        basis_revision: pending.basis_revision,
        turn_id: pending.turn_id.clone(),
        choice: pending.choice.clone(),
        choice_sequence: pending.prior_answers.len() as i64,
    }
}

/// What a resolution identity already answered (`mechanics_choice_resolution_receipt`).
#[derive(Debug, Clone, PartialEq)]
pub enum ResolutionReplay {
    Receipt(ActionReceipt),
    AwaitingChoice(AwaitingChoiceReceipt),
}

/// The identity of one answer to a pending choice.
#[derive(Debug, Clone, Copy)]
pub struct ResolutionIdentity<'a> {
    pub campaign_id: &'a str,
    pub resolution_id: &'a str,
    pub action_id: &'a str,
    pub choice_id: &'a str,
    pub option_id: &'a str,
    pub session_id: Option<&'a str>,
    pub choice_sequence: i64,
}

/// The resolution payload whose SHA-256 a committed resolution keeps.
pub(crate) fn resolution_sha256(identity: &ResolutionIdentity<'_>) -> String {
    let mut payload = JsonMap::new();
    payload.insert("campaign_id".into(), identity.campaign_id.into());
    payload.insert("action_id".into(), identity.action_id.into());
    payload.insert("resolution_id".into(), identity.resolution_id.into());
    payload.insert("choice_id".into(), identity.choice_id.into());
    payload.insert("option_id".into(), identity.option_id.into());
    payload.insert("session_id".into(), identity.session_id.map_or(Json::Null, Json::from));
    if identity.choice_sequence != 0 {
        payload.insert("choice_sequence".into(), identity.choice_sequence.into());
    }
    sha256_hex(dump(&Json::Object(payload)).as_bytes())
}

fn answer_matches(
    answer: &MechanicsChoiceAnswer,
    index: usize,
    identity: &ResolutionIdentity<'_>,
    action_id: &str,
) -> bool {
    index as i64 == identity.choice_sequence
        && action_id == identity.action_id
        && answer.request.choice_id == identity.choice_id
        && answer.option_id == identity.option_id
        && answer.session_id.as_deref() == identity.session_id
}

impl AuthorityStore {
    /// `pending_mechanics_action(campaign_id)`.
    pub fn pending_mechanics_action(&self, campaign_id: &str) -> StoreResult<Option<PendingMechanicsAction>> {
        let connection = self.connect_read_only()?;
        if !table_exists(&connection, "pending_mechanics_actions")? {
            return Ok(None);
        }
        pending_on(&connection, campaign_id)
    }

    /// `pending_mechanics_hit(campaign_id, idempotency_key)`: the alias's payload hash and the
    /// pending choice it names.
    pub fn pending_mechanics_hit(
        &self,
        campaign_id: &str,
        idempotency_key: &str,
    ) -> StoreResult<Option<(String, PendingMechanicsAction)>> {
        let connection = self.connect_read_only()?;
        if !table_exists(&connection, "pending_mechanics_action_aliases")? {
            return Ok(None);
        }
        let (columns, rows) = named_rows(
            &connection,
            "SELECT pending.*, alias.payload_hash AS alias_payload_hash \
             FROM pending_mechanics_action_aliases AS alias \
             JOIN pending_mechanics_actions AS pending \
             ON pending.campaign_id = alias.campaign_id AND pending.action_id = alias.action_id \
             WHERE alias.campaign_id = ? AND alias.idempotency_key = ?",
            &[&campaign_id, &idempotency_key],
        )?;
        let Some(values) = rows.into_iter().next() else { return Ok(None) };
        let row = Named { columns: &columns, values };
        Ok(Some((row.text("alias_payload_hash")?, pending_from_row(&row)?)))
    }

    /// `reserve_mechanics_choice(...)`: one pending choice and its transport alias, or the
    /// pending choice an exact alias replay names.
    pub fn reserve_mechanics_choice(
        &self,
        reservation: &ChoiceReservation<'_>,
    ) -> StoreResult<PendingMechanicsAction> {
        let mut descriptor = request_descriptor(reservation.choice);
        if let Some(origin) = reservation.player_action_origin {
            descriptor.insert("player_action_origin".into(), Json::Object(origin.clone()));
        }
        let descriptor_json = dump(&Json::Object(descriptor));
        let descriptor_sha256 = sha256_hex(descriptor_json.as_bytes());
        let connection = self.connect()?;
        let outcome = (|| -> StoreResult<PendingMechanicsAction> {
            connection.execute_batch("BEGIN IMMEDIATE")?;
            self.ensure_campaign_schema(&connection)?;
            let campaign_id = reservation.campaign_id;
            let alias = named_rows(
                &connection,
                "SELECT payload_hash, action_id FROM pending_mechanics_action_aliases \
                 WHERE campaign_id = ? AND idempotency_key = ?",
                &[&campaign_id, &reservation.idempotency_key],
            )?
            .1;
            if let Some(alias) = alias.first() {
                if py_str(&alias[0]) != reservation.payload_hash || py_str(&alias[1]) != reservation.action_id
                {
                    return Err(idempotency_conflict("Idempotency key payload does not match"));
                }
                let (columns, rows) = named_rows(
                    &connection,
                    "SELECT * FROM pending_mechanics_actions WHERE campaign_id = ? AND action_id = ?",
                    &[&campaign_id, &reservation.action_id],
                )?;
                let Some(values) = rows.into_iter().next() else {
                    return Err(schema_identity("Stored mechanics choice alias is orphaned"));
                };
                connection.execute_batch("ROLLBACK")?;
                return pending_from_row(&Named { columns: &columns, values });
            }
            let campaign = named_rows(
                &connection,
                "SELECT lifecycle, writer, revision, current_lock_id FROM campaigns WHERE campaign_id = ?",
                &[&campaign_id],
            )?
            .1;
            let turn = named_rows(
                &connection,
                "SELECT status FROM turns WHERE campaign_id = ? AND turn_id = ?",
                &[&campaign_id, &reservation.turn_id],
            )?
            .1;
            let admitted = match (campaign.first(), turn.first()) {
                (Some(campaign), Some(turn)) => {
                    py_str(&campaign[0]) == LifecycleState::Active.as_str()
                        && py_str(&campaign[1]) == reservation.writer.as_str()
                        && py_int(&campaign[2]) == Some(reservation.expected_revision)
                        && py_str(&turn[0]) == TurnStatus::Open.as_str()
                }
                _ => false,
            };
            if !admitted {
                return Err(stale("Mechanics choice basis no longer admits reservation"));
            }
            let lock_id = py_str(&campaign[0][3]);
            if table_exists(&connection, "pending_mechanics_actions")?
                && !named_rows(
                    &connection,
                    "SELECT 1 FROM pending_mechanics_actions WHERE campaign_id = ?",
                    &[&campaign_id],
                )?
                .1
                .is_empty()
            {
                return Err(rejected("Campaign is awaiting a mechanics choice", "mechanics_choice_pending"));
            }
            if !named_rows(
                &connection,
                "SELECT 1 FROM actions WHERE campaign_id = ? AND action_id = ?",
                &[&campaign_id, &reservation.action_id],
            )?
            .1
            .is_empty()
            {
                return Err(idempotency_conflict("Action identity is already terminal"));
            }
            connection.execute(
                "INSERT INTO pending_mechanics_actions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rusqlite::params![
                    campaign_id,
                    reservation.action_id,
                    reservation.turn_id,
                    reservation.expected_revision,
                    reservation.writer.as_str(),
                    reservation.package_id,
                    reservation.package_version,
                    reservation.package_sha256,
                    lock_id,
                    reservation.tool_name,
                    dump(&Json::Object(reservation.parameters.clone())),
                    reservation.payload_hash,
                    reservation.initiating_session_id,
                    reservation.choice.choice_id,
                    descriptor_json,
                    descriptor_sha256,
                ],
            )?;
            connection.execute(
                "INSERT INTO pending_mechanics_action_aliases VALUES (?, ?, ?, ?)",
                rusqlite::params![
                    campaign_id,
                    reservation.idempotency_key,
                    reservation.action_id,
                    reservation.payload_hash
                ],
            )?;
            connection.execute_batch("COMMIT")?;
            pending_on(&connection, campaign_id)?
                .ok_or_else(|| schema_identity("Stored mechanics choice reservation is unavailable"))
        })();
        if !connection.is_autocommit() {
            let _ = connection.execute_batch("ROLLBACK");
        }
        outcome
    }

    /// `advance_mechanics_choice(pending, answer, next_choice)`: one answer and the next request
    /// saved, without committing any mechanical effect.
    pub fn advance_mechanics_choice(
        &self,
        pending: &PendingMechanicsAction,
        answer: &MechanicsChoiceAnswer,
        next_choice: &MechanicsChoiceRequest,
    ) -> StoreResult<AwaitingChoiceReceipt> {
        if pending.prior_answers.len() >= 63
            || answer.request != pending.choice
            || answer.session_id != pending.initiating_session_id
            || pending.prior_answers.iter().any(|known| known.resolution_id == answer.resolution_id)
        {
            return Err(idempotency_conflict("Mechanics choice sequence does not match its reservation"));
        }
        let mut descriptor = choice_payload(next_choice);
        let answers: Vec<Json> = pending
            .prior_answers
            .iter()
            .chain([answer])
            .map(|known| Json::Object(answer_payload(known)))
            .collect();
        descriptor.insert("prior_answers".into(), Json::Array(answers));
        if let Some(origin) = &pending.player_action_origin {
            descriptor.insert("player_action_origin".into(), Json::Object(origin.clone()));
        }
        let encoded = dump(&Json::Object(descriptor));
        let digest = sha256_hex(encoded.as_bytes());
        let connection = self.connect()?;
        let outcome = (|| -> StoreResult<()> {
            connection.execute_batch("BEGIN IMMEDIATE")?;
            let stored = pending_on(&connection, &pending.campaign_id)?;
            let campaign = named_rows(
                &connection,
                "SELECT revision, current_lock_id FROM campaigns WHERE campaign_id = ?",
                &[&pending.campaign_id],
            )?
            .1;
            let current = stored.as_ref() == Some(pending)
                && campaign.first().is_some_and(|row| {
                    py_int(&row[0]) == Some(pending.basis_revision) && py_str(&row[1]) == pending.lock_id
                });
            if !current {
                return Err(stale("Mechanics choice basis has drifted"));
            }
            connection.execute(
                "UPDATE pending_mechanics_actions SET choice_id = ?, descriptor_json = ?, descriptor_sha256 = ? \
                 WHERE campaign_id = ? AND action_id = ?",
                rusqlite::params![next_choice.choice_id, encoded, digest, pending.campaign_id, pending.action_id],
            )?;
            connection.execute_batch("COMMIT")?;
            Ok(())
        })();
        if !connection.is_autocommit() {
            let _ = connection.execute_batch("ROLLBACK");
        }
        outcome?;
        Ok(AwaitingChoiceReceipt {
            campaign_id: pending.campaign_id.clone(),
            action_id: pending.action_id.clone(),
            basis_revision: pending.basis_revision,
            turn_id: pending.turn_id.clone(),
            choice: next_choice.clone(),
            choice_sequence: pending.prior_answers.len() as i64 + 1,
        })
    }

    /// `mechanics_choice_resolution_receipt(...)`: what this resolution identity already answered,
    /// from its committed resolution, the pending choice's saved answers, or a completed action's
    /// saved answers; a different payload under the same identity is a conflict.
    pub fn mechanics_choice_resolution_receipt(
        &self,
        identity: &ResolutionIdentity<'_>,
    ) -> StoreResult<Option<ResolutionReplay>> {
        let connection = self.connect_read_only()?;
        if !table_exists(&connection, "mechanics_choice_resolutions")? {
            return Ok(None);
        }
        let campaign_id = identity.campaign_id;
        let (columns, rows) = named_rows(
            &connection,
            "SELECT resolution.*, action.status, action.result_json \
             FROM mechanics_choice_resolutions AS resolution \
             JOIN actions AS action ON action.campaign_id = resolution.campaign_id \
             AND action.action_id = resolution.action_id \
             WHERE resolution.campaign_id = ? AND resolution.resolution_id = ?",
            &[&campaign_id, &identity.resolution_id],
        )?;
        let Some(values) = rows.into_iter().next() else {
            if let Some(pending) = pending_on(&connection, campaign_id)? {
                for (index, answer) in pending.prior_answers.iter().enumerate() {
                    if answer.resolution_id != identity.resolution_id {
                        continue;
                    }
                    if !answer_matches(answer, index, identity, &pending.action_id) {
                        return Err(idempotency_conflict("Mechanics choice resolution payload differs"));
                    }
                    let following = pending
                        .prior_answers
                        .get(index + 1)
                        .map_or(&pending.choice, |next| &next.request)
                        .clone();
                    return Ok(Some(ResolutionReplay::AwaitingChoice(AwaitingChoiceReceipt {
                        campaign_id: campaign_id.to_owned(),
                        action_id: identity.action_id.to_owned(),
                        basis_revision: pending.basis_revision,
                        turn_id: pending.turn_id.clone(),
                        choice: following,
                        choice_sequence: index as i64 + 1,
                    })));
                }
            }
            let completed = named_rows(
                &connection,
                "SELECT action_id, status, result_json, resulting_revision FROM actions \
                 WHERE campaign_id = ? AND json_type(result_json, '$.mechanics_choice_answers') = 'array'",
                &[&campaign_id],
            )?
            .1;
            for row in completed {
                let result = load_payload(&py_str(&row[2]))?;
                let answers =
                    answers_from_payload(result.get("mechanics_choice_answers").unwrap_or(&Json::Null))?;
                for (index, answer) in answers.iter().enumerate() {
                    if answer.resolution_id != identity.resolution_id {
                        continue;
                    }
                    if !answer_matches(answer, index, identity, &py_str(&row[0])) {
                        return Err(idempotency_conflict("Mechanics choice resolution payload differs"));
                    }
                    let status = py_str(&row[1]);
                    return Ok(Some(ResolutionReplay::Receipt(ActionReceipt {
                        campaign_id: campaign_id.to_owned(),
                        action_id: identity.action_id.to_owned(),
                        status: ActionStatus::parse(&status).ok_or_else(|| {
                            crate::error::value_error(format!("'{status}' is not a valid ActionStatus"))
                        })?,
                        result,
                        resulting_revision: int(&row[3])?,
                    })));
                }
            }
            return Ok(None);
        };
        let row = Named { columns: &columns, values };
        let session = optional_text(row.get("session_id")?);
        if row.text("action_id")? != identity.action_id
            || row.text("choice_id")? != identity.choice_id
            || row.text("option_id")? != identity.option_id
            || session.as_deref() != identity.session_id
            || row.text("resolution_sha256")? != resolution_sha256(identity)
        {
            return Err(idempotency_conflict("Mechanics choice resolution identity payload does not match"));
        }
        let status = row.text("status")?;
        Ok(Some(ResolutionReplay::Receipt(ActionReceipt {
            campaign_id: campaign_id.to_owned(),
            action_id: identity.action_id.to_owned(),
            status: ActionStatus::parse(&status).ok_or_else(|| {
                crate::error::value_error(format!("'{status}' is not a valid ActionStatus"))
            })?,
            result: load_payload(&row.text("result_json")?)?,
            resulting_revision: int(row.get("resulting_revision")?)?,
        })))
    }
}

/// `reserve_mechanics_choice`'s keyword arguments.
#[derive(Debug, Clone, Copy)]
pub struct ChoiceReservation<'a> {
    pub campaign_id: &'a str,
    pub action_id: &'a str,
    pub idempotency_key: &'a str,
    pub payload_hash: &'a str,
    pub expected_revision: i64,
    pub writer: WriterRole,
    pub turn_id: &'a str,
    pub package_id: &'a str,
    pub package_version: &'a str,
    pub package_sha256: &'a str,
    pub tool_name: &'a str,
    pub parameters: &'a JsonMap,
    pub initiating_session_id: Option<&'a str>,
    pub choice: &'a MechanicsChoiceRequest,
    pub player_action_origin: Option<&'a JsonMap>,
}
