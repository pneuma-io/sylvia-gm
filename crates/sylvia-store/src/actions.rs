//! The turns-and-actions family: `commit_action`, the TURN operations and the receipt reads of
//! `SQLiteAuthorityStore`, with their reads, writes, retry and conflict behaviour.
//!
//! Python: `sqlite_authority.py` (`commit_action`, `open_turn`, `current_open_turn`,
//! `complete_turn`, `turn_is_completed`, `action_receipt`, `idempotency_hit`, `action_hit`),
//! `sqlite_session.require_active_campaign` and `sqlite_state_ownership.owner_for_mutation`.
//!
//! Not yet ported here, refused rather than run differently: resolving a mechanics choice,
//! participant mutations (they validate against package schemas, lane 1) and Campaign File
//! command batches. Python's `commit_action` takes them as optional arguments; this port's
//! [`CommitAction`] has no fields for them yet, and an outcome with participant mutations is
//! refused with [`StoreError::NotPorted`].

use crate::error::{value_error, StoreError, StoreResult};
use crate::pytext::{py_int, py_str};
use crate::store::AuthorityStore;
use rusqlite::types::Value;
use rusqlite::{params, Connection, OptionalExtension};
use sylvia_base::pyjson::{dumps, Dump};
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{
    canonical_payload_is_safe, mechanic_outcome_is_safe, ActionReceipt, ActionStatus, CanonicalEventType,
    LifecycleState, MechanicOutcome, TurnStatus, WriterRole,
};
use sylvia_domain::state_ownership::owner_matches_package_lock;

/// `_dump`: the canonical stored text (sorted keys, compact, UTF-8).
pub(crate) fn dump(value: &Json) -> String {
    dumps(value, Dump::CANONICAL)
}

/// `_decode_canonical_json`: the stored text must parse, refusing non-standard constants, and
/// be exactly the canonical text of its value (which also refuses repeated members).
pub(crate) fn load_json(text: &str) -> StoreResult<Json> {
    let parsed = sylvia_base::pyjson::loads(text)
        .map_err(|_| value_error("Stored JSON is not the exact canonical representation"))?;
    if dump(&parsed) != text {
        return Err(value_error("Stored JSON is not the exact canonical representation"));
    }
    Ok(parsed)
}

/// `_load_payload`: a canonical JSON object.
pub(crate) fn load_payload(text: &str) -> StoreResult<JsonMap> {
    match load_json(text)? {
        Json::Object(map) => Ok(map),
        _ => Err(StoreError::Other("Stored payload must be a canonical JSON object".into())),
    }
}

/// `CommandRejected(message, code=...)`.
pub(crate) fn rejected(message: &str, code: &str) -> StoreError {
    sylvia_domain::DomainError::command_rejected_with(message, code, Vec::new()).into()
}

/// One of Python's `AuthorityError` classes, through lane 1's constructor; `code` is the
/// class's code, checked here so a call site cannot drift from it.
fn coded(kind: &'static str, code: &str, message: &str) -> StoreError {
    let error = crate::error::authority_coded(kind, message);
    debug_assert_eq!(error.code, code, "{kind}'s code");
    StoreError::Coded(error)
}

/// One `commit_action` call: Python's keyword arguments without the not-yet-ported ones.
#[derive(Debug, Clone, PartialEq)]
pub struct CommitAction {
    pub campaign_id: String,
    pub action_id: String,
    pub idempotency_key: String,
    pub payload_hash: String,
    pub expected_revision: i64,
    pub writer: WriterRole,
    pub turn_id: String,
    pub package_id: String,
    pub package_version: String,
    pub package_sha256: String,
    pub tool_name: String,
    pub parameters: JsonMap,
    pub outcome: MechanicOutcome,
    pub status: ActionStatus,
    /// `choice_resolution_id`, `choice_resolution` and `choice_resolution_session_id`: the
    /// answer that completes a pending mechanics choice, when this commit resolves one.
    pub choice_resolution: Option<ChoiceCommit>,
}

/// The answer a commit resolves a pending mechanics choice with.
#[derive(Debug, Clone, PartialEq)]
pub struct ChoiceCommit {
    pub resolution_id: String,
    pub resolution: sylvia_domain::authority::MechanicsChoiceResolution,
    pub session_id: Option<String>,
}

/// What `commit_action`'s resolution branch decided before the ordinary commit.
enum Resolution {
    /// The resolution identity was already committed: its receipt.
    Replay(ActionReceipt),
    /// The command to commit (its first transport alias and saved answers), the further aliases,
    /// and the resolution and descriptor digests to record.
    Commit {
        command: Box<CommitAction>,
        aliases: Vec<String>,
        resolution_sha256: String,
        descriptor_sha256: String,
    },
}

/// `IdempotencyHit`.
#[derive(Debug, Clone, PartialEq)]
pub struct IdempotencyHit {
    pub payload_hash: String,
    pub receipt: ActionReceipt,
}

/// `sqlite_session.require_active_campaign`.
pub(crate) fn require_active_campaign(connection: &Connection, campaign_id: &str) -> StoreResult<()> {
    let lifecycle: Option<Value> = connection
        .query_row("SELECT lifecycle FROM campaigns WHERE campaign_id = ?", [campaign_id], |row| row.get(0))
        .optional()?;
    match lifecycle {
        Some(value) if py_str(&value) == LifecycleState::Active.as_str() => Ok(()),
        _ => Err(rejected("Campaign lifecycle blocks mutation", "lifecycle_inactive")),
    }
}

/// `Enum(str(value))`; Python raises `ValueError` for an unknown value.
fn parse_enum<T>(value: &Value, parse: fn(&str) -> Option<T>, name: &str) -> StoreResult<T> {
    let text = py_str(value);
    parse(&text).ok_or_else(|| value_error(format!("'{text}' is not a valid {name}")))
}

fn int(value: &Value) -> StoreResult<i64> {
    py_int(value).ok_or_else(|| value_error("invalid literal for int() with base 10"))
}

/// The receipt Python builds from an `actions` or `idempotency_records` row.
fn receipt_from(campaign_id: &str, row: &[Value]) -> StoreResult<ActionReceipt> {
    Ok(ActionReceipt {
        campaign_id: campaign_id.to_owned(),
        action_id: py_str(&row[0]),
        status: parse_enum(&row[1], ActionStatus::parse, "ActionStatus")?,
        result: load_payload(&py_str(&row[2]))?,
        resulting_revision: int(&row[3])?,
    })
}

fn one_row(
    connection: &Connection,
    query: &str,
    values: &[&dyn rusqlite::ToSql],
) -> StoreResult<Option<Vec<Value>>> {
    let mut statement = connection.prepare(query)?;
    let width = statement.column_count();
    let row = statement
        .query_row(values, |row| {
            (0..width).map(|index| row.get::<_, Value>(index)).collect::<Result<Vec<_>, _>>()
        })
        .optional()?;
    Ok(row)
}

pub(crate) fn current_package_ids(connection: &Connection, campaign_id: &str) -> StoreResult<Vec<String>> {
    Ok(all_rows_with(
        connection,
        "SELECT membership.package_id FROM campaigns AS campaign \
         JOIN campaign_lock_memberships AS membership \
         ON membership.lock_id = campaign.current_lock_id \
         WHERE campaign.campaign_id = ? ORDER BY membership.closure_order",
        &[&campaign_id],
    )?
    .iter()
    .map(|row| py_str(&row[0]))
    .collect())
}

fn all_rows_with(
    connection: &Connection,
    query: &str,
    values: &[&dyn rusqlite::ToSql],
) -> StoreResult<Vec<Vec<Value>>> {
    let mut statement = connection.prepare(query)?;
    let width = statement.column_count();
    let rows =
        statement.query_map(values, |row| (0..width).map(|index| row.get::<_, Value>(index)).collect())?;
    Ok(rows.collect::<Result<Vec<Vec<Value>>, _>>()?)
}

/// `sqlite_state_ownership.package_uses_content_declarations`.
fn package_uses_content_declarations(
    connection: &Connection,
    id: &str,
    version: &str,
    sha256: &str,
) -> StoreResult<bool> {
    let row = one_row(
        connection,
        "SELECT canonical_json FROM package_manifests \
         WHERE package_id = ? AND version = ? AND content_sha256 = ? AND status = 'published'",
        &[&id, &version, &sha256],
    )?;
    let Some(row) = row else {
        return Err(rejected("Action package is unavailable", "command_rejected"));
    };
    let manifest = sylvia_base::pyjson::loads(&py_str(&row[0]))
        .map_err(|_| crate::error::schema_identity("Package manifest identity is invalid"))?;
    Ok(matches!(manifest.get("content_declarations"), Some(Json::String(_))))
}

/// `commit_action`'s resolution branch: a resolution identity already committed is its receipt;
/// otherwise the pending choice must be exactly this command's reservation, and the command
/// commits under its first transport alias with the saved answers in its result.
fn resolution_on(
    connection: &Connection,
    command: &CommitAction,
    choice: &ChoiceCommit,
) -> StoreResult<Resolution> {
    use crate::mechanics_choices::{
        pending_from_row, request_descriptor_sha256, resolution_sha256, ResolutionIdentity,
    };
    let campaign_id = command.campaign_id.as_str();
    let resolution = &choice.resolution;
    let identity = ResolutionIdentity {
        campaign_id,
        resolution_id: &choice.resolution_id,
        action_id: &command.action_id,
        choice_id: &resolution.choice_id,
        option_id: &resolution.option_id,
        session_id: choice.session_id.as_deref(),
        choice_sequence: resolution.prior_answers.len() as i64,
    };
    let digest = resolution_sha256(&identity);
    let descriptor_sha256 = request_descriptor_sha256(&resolution.expected_request);
    let conflict = |message: &str| coded("IdempotencyConflict", "idempotency_conflict", message);
    if let Some(prior) = one_row(
        connection,
        "SELECT action_id, choice_id, option_id, resolution_sha256, descriptor_sha256, session_id, resulting_revision \
         FROM mechanics_choice_resolutions WHERE campaign_id = ? AND resolution_id = ?",
        &[&campaign_id, &choice.resolution_id],
    )? {
        let session = match &prior[5] {
            Value::Null => None,
            value => Some(py_str(value)),
        };
        if py_str(&prior[0]) != command.action_id
            || py_str(&prior[1]) != resolution.choice_id
            || py_str(&prior[2]) != resolution.option_id
            || py_str(&prior[3]) != digest
            || py_str(&prior[4]) != descriptor_sha256
            || session != choice.session_id
        {
            return Err(conflict("Mechanics choice resolution identity payload does not match"));
        }
        let Some(action) = one_row(
            connection,
            "SELECT action_id, status, result_json, resulting_revision FROM actions WHERE campaign_id = ? AND action_id = ?",
            &[&campaign_id, &command.action_id],
        )?
        else {
            return Err(crate::error::schema_identity("Stored mechanics choice resolution is orphaned"));
        };
        return Ok(Resolution::Replay(receipt_from(campaign_id, &action)?));
    }
    let (columns, rows) = crate::campaigns::named_rows(
        connection,
        "SELECT * FROM pending_mechanics_actions WHERE campaign_id = ? AND action_id = ?",
        &[&campaign_id, &command.action_id],
    )?;
    let Some(values) = rows.into_iter().next() else {
        return Err(rejected("Mechanics choice reservation is unavailable", "mechanics_choice_unavailable"));
    };
    let stored = pending_from_row(&crate::campaigns::Named { columns: &columns, values })?;
    let matches = stored.basis_revision == command.expected_revision
        && stored.writer == command.writer
        && stored.turn_id == command.turn_id
        && stored.package_id == command.package_id
        && stored.package_version == command.package_version
        && stored.package_sha256 == command.package_sha256
        && stored.tool_name == command.tool_name
        && sylvia_domain::pyops::py_eq(
            &Json::Object(stored.parameters.clone()),
            &Json::Object(command.parameters.clone()),
        )
        && stored.payload_hash == command.payload_hash
        && stored.choice == resolution.expected_request
        && stored.prior_answers == resolution.prior_answers
        && stored.choice.choice_id == resolution.choice_id
        && stored.choice.options.iter().any(|option| option.option_id == resolution.option_id)
        && stored.initiating_session_id == choice.session_id;
    if !matches {
        return Err(conflict("Mechanics choice resolution does not match its reservation"));
    }
    let aliases: Vec<String> = crate::campaigns::named_rows(
        connection,
        "SELECT idempotency_key FROM pending_mechanics_action_aliases WHERE campaign_id = ? AND action_id = ? \
         ORDER BY idempotency_key",
        &[&campaign_id, &command.action_id],
    )?
    .1
    .iter()
    .map(|row| py_str(&row[0]))
    .collect();
    let Some((first, rest)) = aliases.split_first() else {
        return Err(crate::error::schema_identity(
            "Stored mechanics choice reservation has no transport identity",
        ));
    };
    let mut prepared = command.clone();
    prepared.idempotency_key = first.clone();
    if !stored.prior_answers.is_empty() {
        let answers: Vec<Json> = stored
            .prior_answers
            .iter()
            .map(|answer| Json::Object(sylvia_domain::mechanics_choices::answer_payload(answer)))
            .collect();
        prepared.outcome.result.insert("mechanics_choice_answers".into(), Json::Array(answers));
    }
    Ok(Resolution::Commit {
        command: Box::new(prepared),
        aliases: rest.to_vec(),
        resolution_sha256: digest,
        descriptor_sha256,
    })
}

/// The package a mechanic acts as, for `owner_for_mutation`.
#[derive(Debug, Clone, Copy)]
pub(crate) struct MutationActor<'a> {
    pub campaign_id: &'a str,
    pub package_id: &'a str,
    pub package_version: &'a str,
    pub package_sha256: &'a str,
}

/// `sqlite_state_ownership.owner_for_mutation`: the existing owner, or one new actor claim.
pub(crate) fn owner_for_mutation(
    connection: &Connection,
    actor: &MutationActor<'_>,
    namespace: &str,
) -> StoreResult<(String, bool)> {
    let command = actor;
    let existing = one_row(
        connection,
        "SELECT owner_package_id FROM campaign_state_namespace_owners WHERE campaign_id = ? AND namespace = ?",
        &[&command.campaign_id, &namespace],
    )?;
    let failed = || rejected("Tool execution failed", "tool_execution_failed");
    if let Some(row) = existing {
        let recorded = match &row[0] {
            Value::Null => None,
            value => Some(py_str(value)),
        };
        let ids = current_package_ids(connection, command.campaign_id)?;
        if !owner_matches_package_lock(ids.iter().map(String::as_str), namespace, recorded.as_deref())
            || recorded.as_deref() != Some(command.package_id)
        {
            return Err(failed());
        }
        return Ok((command.package_id.to_owned(), false));
    }
    let ids = current_package_ids(connection, command.campaign_id)?;
    let matches: Vec<&String> = ids
        .iter()
        .filter(|id| namespace == id.as_str() || namespace.starts_with(&format!("{id}.")))
        .collect();
    let single_actor = matches.len() == 1 && matches[0].as_str() == command.package_id;
    if !single_actor
        && (package_uses_content_declarations(
            connection,
            command.package_id,
            command.package_version,
            command.package_sha256,
        )? || !matches.is_empty())
    {
        return Err(failed());
    }
    Ok((command.package_id.to_owned(), true))
}

fn object(pairs: Vec<(&str, Json)>) -> Json {
    let mut map = JsonMap::new();
    for (key, value) in pairs {
        map.insert(key.to_owned(), value);
    }
    Json::Object(map)
}

impl AuthorityStore {
    /// `commit_action`: one atomic action at the expected revision, or the stored receipt of
    /// an idempotent replay.
    pub fn commit_action(&self, command: &CommitAction) -> StoreResult<ActionReceipt> {
        if !canonical_payload_is_safe(&Json::Object(command.parameters.clone()))
            || !mechanic_outcome_is_safe(&command.outcome)
        {
            return Err(rejected(
                "Persistence rejected an unsafe canonical payload",
                "unsafe_canonical_payload",
            ));
        }
        if !command.outcome.participant_mutations.is_empty() {
            return Err(StoreError::NotPorted(
                "participant mutations in commit_action (lane 1 schema validation)",
            ));
        }
        let connection = self.connect()?;
        let result = self.commit_action_in(&connection, command);
        if !connection.is_autocommit() {
            let _ = connection.execute_batch("ROLLBACK");
        }
        result
    }

    fn commit_action_in(
        &self,
        connection: &Connection,
        command: &CommitAction,
    ) -> StoreResult<ActionReceipt> {
        let campaign_id = command.campaign_id.as_str();
        connection.execute_batch("BEGIN IMMEDIATE")?;
        if !command.outcome.mutations.is_empty() || command.choice_resolution.is_some() {
            self.ensure_campaign_schema(connection)?;
        }
        let mut resolving = None;
        if let Some(choice) = &command.choice_resolution {
            match resolution_on(connection, command, choice)? {
                Resolution::Replay(receipt) => {
                    connection.execute_batch("ROLLBACK")?;
                    return Ok(receipt);
                }
                Resolution::Commit { command, aliases, resolution_sha256, descriptor_sha256 } => {
                    resolving = Some((command, aliases, resolution_sha256, descriptor_sha256));
                }
            }
        }
        let command = resolving.as_ref().map_or(command, |(command, ..)| command.as_ref());
        if let Some(existing) = one_row(
            connection,
            "SELECT payload_hash, action_id, status, result_json, resulting_revision \
             FROM idempotency_records WHERE campaign_id = ? AND idempotency_key = ?",
            &[&campaign_id, &command.idempotency_key],
        )? {
            if py_str(&existing[0]) != command.payload_hash {
                return Err(coded(
                    "IdempotencyConflict",
                    "idempotency_conflict",
                    "Idempotency key payload does not match",
                ));
            }
            connection.execute_batch("ROLLBACK")?;
            return receipt_from(campaign_id, &existing[1..]);
        }
        let Some(campaign) = one_row(
            connection,
            "SELECT lifecycle, writer, revision, current_lock_id FROM campaigns WHERE campaign_id = ?",
            &[&campaign_id],
        )?
        else {
            return Err(rejected("Campaign is unavailable", "command_rejected"));
        };
        let lifecycle = parse_enum(&campaign[0], LifecycleState::parse, "LifecycleState")?;
        let writer = parse_enum(&campaign[1], WriterRole::parse, "WriterRole")?;
        let revision = int(&campaign[2])?;
        if lifecycle != LifecycleState::Active {
            return Err(rejected("Campaign lifecycle blocks mutation", "lifecycle_inactive"));
        }
        if writer != command.writer {
            return Err(coded("WrongWriter", "wrong_writer", "Command writer is not authoritative"));
        }
        if revision != command.expected_revision {
            return Err(coded("StaleRevision", "stale_revision", "Expected campaign revision is stale"));
        }
        let turn = one_row(
            connection,
            "SELECT status FROM turns WHERE campaign_id = ? AND turn_id = ?",
            &[&campaign_id, &command.turn_id],
        )?;
        let open = match turn {
            Some(row) => parse_enum(&row[0], TurnStatus::parse, "TurnStatus")? == TurnStatus::Open,
            None => false,
        };
        if !open {
            return Err(rejected("TURN does not admit actions", "command_rejected"));
        }

        for mutation in &command.outcome.mutations {
            let actor = MutationActor {
                campaign_id: &command.campaign_id,
                package_id: &command.package_id,
                package_version: &command.package_version,
                package_sha256: &command.package_sha256,
            };
            let (owner, is_new) = owner_for_mutation(connection, &actor, &mutation.namespace)?;
            if is_new {
                connection.execute(
                    "INSERT INTO campaign_state_namespace_owners (campaign_id, namespace, owner_package_id) \
                     VALUES (?, ?, ?)",
                    params![campaign_id, mutation.namespace, owner],
                )?;
            }
        }
        // `_validate_participant_mutations` has nothing to check: participant mutations are
        // refused above until they are ported.
        let resulting_revision = command
            .expected_revision
            .checked_add(1)
            .ok_or_else(|| StoreError::Other("campaign revision overflow".into()))?;
        let parameters = Json::Object(command.parameters.clone());
        let result = Json::Object(command.outcome.result.clone());
        connection.execute(
            "INSERT INTO actions \
             (campaign_id, action_id, turn_id, expected_revision, resulting_revision, writer, \
             package_id, package_version, package_sha256, tool_name, status, payload_json, \
             payload_hash, result_json, lock_id) \
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            params![
                campaign_id,
                command.action_id,
                command.turn_id,
                command.expected_revision,
                resulting_revision,
                command.writer.as_str(),
                command.package_id,
                command.package_version,
                command.package_sha256,
                command.tool_name,
                command.status.as_str(),
                dump(&parameters),
                command.payload_hash,
                dump(&result),
                py_str(&campaign[3]),
            ],
        )?;
        let mut sequence: i64 = connection.query_row(
            "SELECT COALESCE(MAX(sequence), 0) FROM canonical_events WHERE campaign_id = ?",
            [campaign_id],
            |row| row.get(0),
        )?;
        let mut append = |event_type: CanonicalEventType, payload: &Json| -> StoreResult<()> {
            sequence =
                sequence.checked_add(1).ok_or_else(|| StoreError::Other("event sequence overflow".into()))?;
            let visibility =
                if matches!(event_type, CanonicalEventType::ToolCall | CanonicalEventType::ToolResult) {
                    "player_safe"
                } else {
                    "internal_safe"
                };
            connection.execute(
                "INSERT INTO canonical_events \
                 (campaign_id, sequence, event_type, turn_id, action_id, \
                 safe_payload_json, visibility, attempt_id, attempt_ordinal) \
                 VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
                params![
                    campaign_id,
                    sequence,
                    event_type.as_str(),
                    command.turn_id,
                    command.action_id,
                    dump(payload),
                    visibility
                ],
            )?;
            Ok(())
        };
        append(
            CanonicalEventType::ActionDeclared,
            &object(vec![
                ("action_id", command.action_id.clone().into()),
                ("writer", command.writer.as_str().into()),
            ]),
        )?;
        append(
            CanonicalEventType::ToolCall,
            &object(vec![
                ("package_id", command.package_id.clone().into()),
                ("package_version", command.package_version.clone().into()),
                ("package_sha256", command.package_sha256.clone().into()),
                ("tool_name", command.tool_name.clone().into()),
                ("parameters", parameters.clone()),
            ]),
        )?;
        append(
            CanonicalEventType::ToolResult,
            &object(vec![("status", command.status.as_str().into()), ("result", result.clone())]),
        )?;
        for (ordinal, mutation) in command.outcome.mutations.iter().enumerate() {
            let previous = one_row(
                connection,
                "SELECT value_json FROM campaign_state WHERE campaign_id = ? AND namespace = ? AND state_key = ?",
                &[&campaign_id, &mutation.namespace, &mutation.key],
            )?;
            let before = match previous {
                Some(row) => load_json(&py_str(&row[0]))?,
                None => Json::Null,
            };
            connection.execute(
                "INSERT INTO campaign_state VALUES (?, ?, ?, ?) \
                 ON CONFLICT(campaign_id, namespace, state_key) \
                 DO UPDATE SET value_json = excluded.value_json",
                params![campaign_id, mutation.namespace, mutation.key, dump(&mutation.value)],
            )?;
            connection.execute(
                "INSERT INTO mutations VALUES (?, ?, ?, ?, ?, ?, ?)",
                params![
                    campaign_id,
                    command.action_id,
                    i64::try_from(ordinal).unwrap_or(i64::MAX),
                    mutation.namespace,
                    mutation.key,
                    dump(&before),
                    dump(&mutation.value)
                ],
            )?;
            append(
                CanonicalEventType::StateMutation,
                &object(vec![
                    ("namespace", mutation.namespace.clone().into()),
                    ("key", mutation.key.clone().into()),
                ]),
            )?;
        }
        for (ordinal, consequence) in command.outcome.consequences.iter().enumerate() {
            let consequence = Json::Object(consequence.clone());
            connection.execute(
                "INSERT INTO consequences VALUES (?, ?, ?, ?)",
                params![
                    campaign_id,
                    command.action_id,
                    i64::try_from(ordinal).unwrap_or(i64::MAX),
                    dump(&consequence)
                ],
            )?;
            append(CanonicalEventType::Consequence, &consequence)?;
        }
        let terminal = if command.status == ActionStatus::Committed {
            CanonicalEventType::ActionCommitted
        } else {
            CanonicalEventType::ActionFailed
        };
        append(
            terminal,
            &object(vec![
                ("status", command.status.as_str().into()),
                ("resulting_revision", resulting_revision.into()),
            ]),
        )?;
        let updated = connection.execute(
            "UPDATE campaigns SET revision = ? \
             WHERE campaign_id = ? AND revision = ? AND writer = ? AND lifecycle = 'active'",
            params![resulting_revision, campaign_id, command.expected_revision, command.writer.as_str()],
        )?;
        if updated != 1 {
            return Err(coded(
                "StaleRevision",
                "stale_revision",
                "Campaign revision fence rejected the action",
            ));
        }
        connection.execute(
            "INSERT INTO idempotency_records VALUES (?, ?, ?, ?, ?, ?, ?)",
            params![
                campaign_id,
                command.idempotency_key,
                command.payload_hash,
                command.action_id,
                command.status.as_str(),
                dump(&result),
                resulting_revision
            ],
        )?;
        if let (Some((_, aliases, resolution_sha256, descriptor_sha256)), Some(choice)) =
            (&resolving, &command.choice_resolution)
        {
            for alias in aliases {
                connection.execute(
                    "INSERT INTO idempotency_records VALUES (?, ?, ?, ?, ?, ?, ?)",
                    params![
                        campaign_id,
                        alias,
                        command.payload_hash,
                        command.action_id,
                        command.status.as_str(),
                        dump(&result),
                        resulting_revision
                    ],
                )?;
            }
            connection.execute(
                "INSERT INTO mechanics_choice_resolutions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                params![
                    campaign_id,
                    choice.resolution_id,
                    command.action_id,
                    choice.resolution.choice_id,
                    choice.resolution.option_id,
                    resolution_sha256,
                    descriptor_sha256,
                    choice.session_id,
                    resulting_revision
                ],
            )?;
            connection.execute(
                "DELETE FROM pending_mechanics_actions WHERE campaign_id = ? AND action_id = ?",
                params![campaign_id, command.action_id],
            )?;
        }
        connection.execute(
            "INSERT INTO campaign_revisions (campaign_id, revision, action_id, activation_id) VALUES (?, ?, ?, NULL)",
            params![campaign_id, resulting_revision, command.action_id],
        )?;
        if let Some(fault) = &self.faults.before_commit {
            fault()?;
        }
        connection.execute_batch("COMMIT")?;
        Ok(ActionReceipt {
            campaign_id: campaign_id.to_owned(),
            action_id: command.action_id.clone(),
            status: command.status,
            result: command.outcome.result.clone(),
            resulting_revision,
        })
    }

    /// `open_turn`: one new TURN after the previous one reached a checkpoint.
    pub fn open_turn(&self, campaign_id: &str, turn_id: &str) -> StoreResult<()> {
        self.in_immediate(|connection| {
            require_active_campaign(connection, campaign_id)?;
            if one_row(
                connection,
                "SELECT turn_id FROM turns WHERE campaign_id = ? AND status = 'open'",
                &[&campaign_id],
            )?
            .is_some()
            {
                return Err(rejected("Campaign already has an open TURN", "command_rejected"));
            }
            let Some(revision) =
                one_row(connection, "SELECT revision FROM campaigns WHERE campaign_id = ?", &[&campaign_id])?
            else {
                return Err(rejected("Campaign is unavailable", "command_rejected"));
            };
            match connection.execute(
                "INSERT INTO turns(campaign_id, turn_id, status, created_revision) VALUES (?, ?, 'open', ?)",
                params![campaign_id, turn_id, int(&revision[0])?],
            ) {
                Ok(_) => Ok(()),
                Err(rusqlite::Error::SqliteFailure(error, _))
                    if error.code == rusqlite::ErrorCode::ConstraintViolation =>
                {
                    Err(rejected("TURN identity is unavailable", "command_rejected"))
                }
                Err(error) => Err(error.into()),
            }
        })
    }

    /// `current_open_turn`.
    pub fn current_open_turn(&self, campaign_id: &str) -> StoreResult<Option<String>> {
        let connection = self.connect()?;
        let row = one_row(
            &connection,
            "SELECT turn_id FROM turns WHERE campaign_id = ? AND status = 'open'",
            &[&campaign_id],
        )?;
        Ok(row.map(|row| py_str(&row[0])))
    }

    /// `complete_turn`.
    pub fn complete_turn(&self, campaign_id: &str, turn_id: &str) -> StoreResult<()> {
        self.in_immediate(|connection| {
            require_active_campaign(connection, campaign_id)?;
            let has_pending_table = one_row(
                connection,
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'pending_mechanics_actions'",
                &[],
            )?
            .is_some();
            if has_pending_table
                && one_row(connection, "SELECT 1 FROM pending_mechanics_actions WHERE campaign_id = ?", &[&campaign_id])?
                    .is_some()
            {
                return Err(rejected(
                    "TURN cannot complete while a mechanics choice is pending",
                    "mechanics_choice_pending",
                ));
            }
            let updated = connection.execute(
                "UPDATE turns SET status = 'completed' WHERE campaign_id = ? AND turn_id = ? AND status = 'open'",
                params![campaign_id, turn_id],
            )?;
            if updated != 1 {
                return Err(rejected("TURN cannot be completed", "command_rejected"));
            }
            Ok(())
        })
    }

    /// `turn_is_completed`.
    pub fn turn_is_completed(&self, campaign_id: &str, turn_id: &str) -> StoreResult<bool> {
        let connection = self.connect()?;
        let Some(row) = one_row(
            &connection,
            "SELECT status FROM turns WHERE campaign_id = ? AND turn_id = ?",
            &[&campaign_id, &turn_id],
        )?
        else {
            return Err(rejected("TURN identity is unavailable", "command_rejected"));
        };
        Ok(py_str(&row[0]) == TurnStatus::Completed.as_str())
    }

    /// `idempotency_hit`.
    pub fn idempotency_hit(&self, campaign_id: &str, key: &str) -> StoreResult<Option<IdempotencyHit>> {
        self.hit(
            "SELECT payload_hash, action_id, status, result_json, resulting_revision \
             FROM idempotency_records WHERE campaign_id = ? AND idempotency_key = ?",
            campaign_id,
            key,
        )
    }

    /// `action_hit`.
    pub fn action_hit(&self, campaign_id: &str, action_id: &str) -> StoreResult<Option<IdempotencyHit>> {
        self.hit(
            "SELECT payload_hash, action_id, status, result_json, resulting_revision \
             FROM actions WHERE campaign_id = ? AND action_id = ?",
            campaign_id,
            action_id,
        )
    }

    /// `action_receipt`.
    pub fn action_receipt(&self, campaign_id: &str, action_id: &str) -> StoreResult<ActionReceipt> {
        match self.action_hit(campaign_id, action_id)? {
            Some(hit) => Ok(hit.receipt),
            None => Err(rejected("Stored action result is unavailable", "command_rejected")),
        }
    }

    fn hit(&self, query: &str, campaign_id: &str, key: &str) -> StoreResult<Option<IdempotencyHit>> {
        let connection = self.connect()?;
        let Some(row) = one_row(&connection, query, &[&campaign_id, &key])? else {
            return Ok(None);
        };
        Ok(Some(IdempotencyHit {
            payload_hash: py_str(&row[0]),
            receipt: receipt_from(campaign_id, &row[1..])?,
        }))
    }

    /// `BEGIN IMMEDIATE`, the work, `COMMIT`; rolled back on any failure.
    pub(crate) fn in_immediate<T>(&self, work: impl FnOnce(&Connection) -> StoreResult<T>) -> StoreResult<T> {
        let connection = self.connect()?;
        let result = (|| {
            connection.execute_batch("BEGIN IMMEDIATE")?;
            let value = work(&connection)?;
            connection.execute_batch("COMMIT")?;
            Ok(value)
        })();
        if !connection.is_autocommit() {
            let _ = connection.execute_batch("ROLLBACK");
        }
        result
    }
}
