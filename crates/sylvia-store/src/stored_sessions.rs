//! `_verify_session_identities`: the session, plan and handoff authority graph, reconciled
//! at startup and on backups (part 2 of `_verify_stored_authority`).
//!
//! Python: `SQLiteAuthorityStore._verify_session_identities` with `_handoff_candidate_matches_lock`.
//!
//! Python catches only `KeyError`, `TypeError`, `ValueError` and `JSONDecodeError` here and
//! turns them into its `SchemaIdentityError`; a `CommandRejected` from a package or lock read,
//! a SQLite error or anything else escapes, and startup words it as `_migrate` does.
//!
//! Not yet checked, only making this less strict until it lands: a reviewed sequel Plan's
//! `verify_sequel` and its adoption evidence, which need lane 1's pending
//! `domain/campaign_sequel.py`. Its predecessor must still exist.

use crate::actions::{dump, load_payload};
use crate::campaigns::{handoff_from_row, named_rows, read_lock_record, session_from_row, Named};
use crate::error::{schema_identity, StoreError, StoreResult};
use crate::pytext::{py_int, py_str};
use crate::sessions::{message_from_row, plan_from_row, read_builder_chats};
use rusqlite::types::Value;
use rusqlite::Connection;
use std::collections::{BTreeMap, HashMap, HashSet};
use sylvia_base::hash::sha256_hex;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{owned_payload, WriterRole};
use sylvia_domain::model_capability::model_guidance_payload_sha256;
use sylvia_domain::package_library::{
    CandidateStatus, PackageCandidate, PackageLockAvailability, PackageLockRecord,
};
use sylvia_domain::pyops::py_eq;
use sylvia_domain::session::{
    builder_dm_handoff_payload_sha256, campaign_plan_payload_sha256, BuilderDMHandoff,
    BuilderDMHandoffRequest, CampaignPlan, CampaignPlanRequest, SessionMessage, SessionMessageKind,
    SessionRecord, SessionStatus,
};
use sylvia_domain::DomainError;

const INVALID: &str = "Saved campaign records failed an integrity check";

/// One step's failure: an exception Python turns into its `SchemaIdentityError`, or one that
/// escapes.
enum Flow {
    Invalid,
    Escape(StoreError),
}

type Checked<T> = Result<T, Flow>;

impl From<StoreError> for Flow {
    fn from(error: StoreError) -> Self {
        match &error {
            // Lane 1's uncaught `KeyError` and `TypeError` reach the store as `Other`.
            StoreError::Other(_) => Flow::Invalid,
            StoreError::Coded(coded)
                if matches!(coded.error_type, Some("ValueError" | "TypeError" | "JSONDecodeError")) =>
            {
                Flow::Invalid
            }
            _ => Flow::Escape(error),
        }
    }
}

impl From<DomainError> for Flow {
    fn from(error: DomainError) -> Self {
        Flow::from(StoreError::from(error))
    }
}

impl From<rusqlite::Error> for Flow {
    fn from(error: rusqlite::Error) -> Self {
        Flow::Escape(error.into())
    }
}

fn invalid<T>() -> Checked<T> {
    Err(Flow::Invalid)
}

fn rows(
    connection: &Connection,
    query: &str,
    values: &[&dyn rusqlite::ToSql],
    columns: &mut Vec<String>,
) -> Checked<Vec<Vec<Value>>> {
    let (names, rows) = named_rows(connection, query, values)?;
    *columns = names;
    Ok(rows)
}

fn first(
    connection: &Connection,
    query: &str,
    values: &[&dyn rusqlite::ToSql],
) -> Checked<Option<(Vec<String>, Vec<Value>)>> {
    let (columns, mut found) = named_rows(connection, query, values)?;
    Ok(if found.is_empty() { None } else { Some((columns, found.swap_remove(0))) })
}

fn text_of(value: &Value) -> Option<&str> {
    match value {
        Value::Text(text) => Some(text),
        _ => None,
    }
}

/// `_handoff_candidate_matches_lock`.
fn handoff_candidate_matches_lock(lock: &PackageLockRecord, candidate: Option<&PackageCandidate>) -> bool {
    if lock.availability != PackageLockAvailability::Available {
        return false;
    }
    if lock.package_lock.entries().is_empty() {
        return candidate.is_none();
    }
    candidate.is_some_and(|candidate| {
        matches!(candidate.status(), CandidateStatus::Validated | CandidateStatus::Activated)
            && candidate.package_lock() == &lock.package_lock
            && candidate.closure_sha256() == lock.closure_sha256
    })
}

fn messages_of(connection: &Connection, table: &str, session_id: &str) -> Checked<Vec<SessionMessage>> {
    let (columns, found) = named_rows(
        connection,
        &format!("SELECT * FROM {table} WHERE session_id = ? ORDER BY sequence"),
        &[&session_id],
    )?;
    Ok(found
        .into_iter()
        .map(|values| message_from_row(&Named { columns: &columns, values }))
        .collect::<StoreResult<_>>()?)
}

fn chronology_is_valid(messages: &[SessionMessage]) -> bool {
    messages.iter().enumerate().all(|(index, message)| message.sequence() == index as i64 + 1)
}

fn identities_are_unique(messages: &[SessionMessage]) -> bool {
    messages.iter().map(SessionMessage::message_id).collect::<HashSet<_>>().len() == messages.len()
}

/// `_verify_session_identities`.
pub(crate) fn verify_session_identities(connection: &Connection) -> StoreResult<()> {
    let present: Vec<String> =
        named_rows(connection, "SELECT name FROM sqlite_master WHERE type = 'table'", &[])?
            .1
            .iter()
            .map(|row| py_str(&row[0]))
            .collect();
    if !present.iter().any(|name| name == "agent_sessions") {
        return Ok(());
    }
    reconcile(connection, &present).map_err(|flow| match flow {
        Flow::Invalid => schema_identity(INVALID),
        Flow::Escape(error) => error,
    })
}

fn reconcile(connection: &Connection, present: &[String]) -> Checked<()> {
    let mut columns = Vec::new();
    let mut sessions: BTreeMap<String, SessionRecord> = BTreeMap::new();
    for values in rows(connection, "SELECT * FROM agent_sessions ORDER BY session_id", &[], &mut columns)? {
        let record = session_from_row(&Named { columns: &columns, values })?;
        sessions.insert(record.session_id().to_owned(), record);
    }
    let mut messages_by_session: HashMap<String, Vec<SessionMessage>> = HashMap::new();
    for session in sessions.values() {
        let fields = session.fields();
        let campaign_id = session.campaign_id().unwrap_or_default();
        let Some((campaign_columns, campaign)) = first(
            connection,
            "SELECT lifecycle, writer FROM campaigns WHERE campaign_id = ?",
            &[&campaign_id],
        )?
        else {
            return invalid();
        };
        let campaign = Named { columns: &campaign_columns, values: campaign };
        if fields.status == SessionStatus::Active {
            if campaign.text("lifecycle")? != "active" {
                return invalid();
            }
            let session_is_writer = campaign.text("writer")? == session.role().as_str();
            let persistent_builder = session.role() == WriterRole::Builder
                && first(
                    connection,
                    "SELECT 1 FROM builder_dm_handoffs WHERE campaign_id = ? AND builder_session_id = ? LIMIT 1",
                    &[&campaign_id, &fields.session_id],
                )?
                .is_some();
            if !session_is_writer && !persistent_builder {
                return invalid();
            }
        }
        if let Some(parent_id) = &fields.parent_session_id {
            if parent_id == &fields.session_id {
                return invalid();
            }
            // `sessions[...]`: a missing parent is a `KeyError`.
            let Some(parent) = sessions.get(parent_id) else { return invalid() };
            if parent.fields().parent_session_id.is_some()
                || parent.campaign_id() != session.campaign_id()
                || parent.role() != WriterRole::Builder
                || session.role() != WriterRole::Builder
            {
                return invalid();
            }
        }
        let messages = messages_of(connection, "agent_session_messages", &fields.session_id)?;
        if !chronology_is_valid(&messages) || !identities_are_unique(&messages) {
            return invalid();
        }
        for message in &messages {
            if message.kind() != SessionMessageKind::CanonicalReference {
                continue;
            }
            let Some((event_columns, event)) = first(
                connection,
                "SELECT event_type, visibility, safe_payload_json FROM canonical_events \
                 WHERE campaign_id = ? AND sequence = ?",
                &[&campaign_id, &message.canonical_sequence()],
            )?
            else {
                return invalid();
            };
            let event = Named { columns: &event_columns, values: event };
            let payload = load_payload(&event.text("safe_payload_json")?)?;
            let event_type = event.text("event_type")?;
            let mut expected = JsonMap::new();
            expected.insert("event_type".into(), event_type.clone().into());
            expected.insert("visibility".into(), event.text("visibility")?.into());
            expected.insert("safe_payload".into(), Json::Object(payload.clone()));
            let mut expected = owned_payload(&Json::Object(expected))?;
            let content = Json::Object(message.content().clone());
            if !py_eq(&content, &Json::Object(expected.clone())) && event_type == "tool_call" {
                let identity: Vec<Option<&str>> = ["package_id", "package_version", "package_sha256"]
                    .iter()
                    .map(|key| payload.get(*key).and_then(Json::as_str))
                    .collect();
                if let [Some(package_id), Some(version), Some(sha256)] = identity[..] {
                    let manifest =
                        crate::packages::exact_manifest(connection, (package_id, version, sha256))?;
                    // Canonical replay omits private engine inputs: check that projection.
                    if manifest.state_schemas.iter().any(|item| item.visibility == "engine") {
                        let mut projected = payload.clone();
                        projected.insert("parameters".into(), Json::Object(JsonMap::new()));
                        expected.insert("safe_payload".into(), Json::Object(projected));
                        expected = owned_payload(&Json::Object(expected))?;
                    }
                }
            }
            if !py_eq(&content, &Json::Object(expected)) {
                return invalid();
            }
        }
        if fields.status == SessionStatus::ReviewRequired
            && !messages.iter().any(|message| message.kind() == SessionMessageKind::HumanReview)
        {
            return invalid();
        }
        messages_by_session.insert(fields.session_id.clone(), messages);
    }

    if present.iter().any(|name| name == "standalone_builder_chats") {
        for chat in read_builder_chats(connection)? {
            let session = chat.session();
            if sessions.contains_key(session.session_id()) {
                return invalid();
            }
            let messages = messages_of(connection, "standalone_builder_chat_messages", session.session_id())?;
            if !chronology_is_valid(&messages)
                || !identities_are_unique(&messages)
                || messages.iter().any(|message| {
                    message.kind() == SessionMessageKind::CanonicalReference
                        || message.canonical_sequence().is_some()
                })
                || (session.fields().status == SessionStatus::ReviewRequired
                    && !messages.iter().any(|message| message.kind() == SessionMessageKind::HumanReview))
            {
                return invalid();
            }
        }
    }

    let mut plans: HashMap<String, CampaignPlan> = HashMap::new();
    let campaign_ids: Vec<String> =
        named_rows(connection, "SELECT campaign_id FROM campaigns ORDER BY campaign_id", &[])?
            .1
            .iter()
            .map(|row| py_str(&row[0]))
            .collect();
    for campaign_id in &campaign_ids {
        let mut plan_columns = Vec::new();
        let plan_rows = rows(
            connection,
            "SELECT * FROM campaign_plans WHERE campaign_id = ? ORDER BY plan_revision",
            &[campaign_id],
            &mut plan_columns,
        )?;
        let plan_rows: Vec<Named<'_>> =
            plan_rows.into_iter().map(|values| Named { columns: &plan_columns, values }).collect();
        for (index, row) in plan_rows.iter().enumerate() {
            if row.int("plan_revision")? != index as i64 + 1 {
                return invalid();
            }
        }
        for row in &plan_rows {
            let plan = plan_from_row(row)?;
            let f = plan.fields();
            if plans.contains_key(&f.plan_id) {
                return invalid();
            }
            plans.insert(f.plan_id.clone(), plan.clone());
            let source = sessions.get(&f.source_session_id);
            let campaign = first(
                connection,
                "SELECT lifecycle, writer FROM campaigns WHERE campaign_id = ?",
                &[&f.campaign_id],
            )?;
            let campaign =
                campaign.as_ref().map(|(columns, values)| Named { columns, values: values.clone() });
            let handoff = first(
                connection,
                "SELECT builder_session_id, dm_session_id FROM builder_dm_handoffs WHERE campaign_id = ?",
                &[&f.campaign_id],
            )?;
            let handoff = handoff.as_ref().map(|(columns, values)| Named { columns, values: values.clone() });
            let mut stopped_source_is_valid = false;
            if let Some(source) = source.filter(|source| source.fields().status == SessionStatus::Stopped) {
                if let Some(campaign) = &campaign {
                    let lifecycle = campaign.text("lifecycle")?;
                    let writer = campaign.text("writer")?;
                    if lifecycle == "active" {
                        stopped_source_is_valid = (writer == "builder" && handoff.is_none())
                            || (writer == "dm" && handoff.is_some());
                    } else if let Some((state_columns, state)) = first(
                        connection,
                        "SELECT designated_session_id, tombstone_prior_session_id \
                         FROM campaign_lifecycle_state WHERE campaign_id = ?",
                        &[&f.campaign_id],
                    )? {
                        let state = Named { columns: &state_columns, values: state };
                        // After handoff, archive keeps the GM as writer while stopping the
                        // Interview that supplied the saved Plan.
                        let mut designated = source.session_id().to_owned();
                        if let Some(handoff) = &handoff {
                            if handoff.text("builder_session_id")? == source.session_id() {
                                designated = handoff.text("dm_session_id")?;
                            }
                        }
                        if lifecycle == "archived" {
                            stopped_source_is_valid =
                                text_of(state.get("designated_session_id")?) == Some(&designated);
                        } else if lifecycle == "tombstoned" {
                            stopped_source_is_valid =
                                text_of(state.get("tombstone_prior_session_id")?) == Some(&designated);
                        }
                    }
                }
            }
            let sequel = plan.content().get("sequel").filter(|value| !value.is_null());
            let source_role_valid = source.is_some_and(|source| {
                (source.role() == WriterRole::Builder
                    && source.fields().selected_skill.as_deref() == Some("BUILD CAMPAIGN PLAN")
                    && sequel.is_none())
                    || (source.role() == WriterRole::Dm
                        && matches!(sequel, Some(Json::Object(_)))
                        && handoff.as_ref().is_some_and(|handoff| {
                            handoff.text("dm_session_id").is_ok_and(|dm| dm == source.session_id())
                        }))
            });
            let Some(source) = source else { return invalid() };
            if source.campaign_id() != Some(f.campaign_id.as_str())
                || source.fields().parent_session_id.is_some()
                || !source_role_valid
                || (!matches!(source.fields().status, SessionStatus::Active | SessionStatus::HandoffClosed)
                    && !stopped_source_is_valid)
            {
                return invalid();
            }
            let source_messages: HashMap<&str, &SessionMessage> = messages_by_session
                .get(source.session_id())
                .map(|messages| messages.iter().map(|message| (message.message_id(), message)).collect())
                .unwrap_or_default();
            if f.provenance_evidence_ids.iter().any(|id| {
                source_messages
                    .get(id.as_str())
                    .is_none_or(|message| message.kind() != SessionMessageKind::Evidence)
            }) {
                return invalid();
            }
            if sha256_hex(dump(&Json::Object(plan.content().clone())).as_bytes()) != f.content_sha256 {
                return invalid();
            }
            if let Some(Json::Object(sequel)) = sequel {
                // `verify_sequel` and the adoption evidence wait for lane 1's `campaign_sequel`.
                let previous = sequel
                    .get("previous_plan_id")
                    .map_or_else(|| "None".to_owned(), sylvia_domain::pyops::py_str);
                if !plans.contains_key(&previous) {
                    return invalid();
                }
            }
            let expected_payload = campaign_plan_payload_sha256(&CampaignPlanRequest {
                plan_id: f.plan_id.clone(),
                campaign_id: f.campaign_id.clone(),
                source_session_id: f.source_session_id.clone(),
                expected_revision: f.resulting_campaign_revision - 1,
                expected_package_lock_id: f.package_lock_id.clone(),
                content_sha256: f.content_sha256.clone(),
                content: Json::Object(plan.content().clone()),
                provenance_evidence_ids: f.provenance_evidence_ids.clone(),
                validation_outcome: f.validation_outcome,
                acceptance_status: f.acceptance_status,
            })?;
            let revision = first(
                connection,
                "SELECT plan_id FROM campaign_revisions WHERE campaign_id = ? AND revision = ?",
                &[&f.campaign_id, &f.resulting_campaign_revision],
            )?;
            let lock = first(
                connection,
                "SELECT campaign_id FROM campaign_locks WHERE lock_id = ?",
                &[&f.package_lock_id],
            )?;
            if expected_payload != f.payload_sha256
                || f.resulting_campaign_revision < f.plan_revision
                || revision.is_none_or(|(_, row)| py_str(&row[0]) != f.plan_id)
                || lock.is_none_or(|(_, row)| py_str(&row[0]) != f.campaign_id)
            {
                return invalid();
            }
        }
    }

    let mut handoffs: HashMap<String, BuilderDMHandoff> = HashMap::new();
    let mut handoff_columns = Vec::new();
    for values in rows(
        connection,
        "SELECT * FROM builder_dm_handoffs ORDER BY campaign_id, handoff_id",
        &[],
        &mut handoff_columns,
    )? {
        let row = Named { columns: &handoff_columns, values };
        let handoff = handoff_from_row(&row)?;
        let h = handoff.fields();
        if handoffs.contains_key(&h.handoff_id) {
            return invalid();
        }
        handoffs.insert(h.handoff_id.clone(), handoff.clone());
        let builder = sessions.get(&h.builder_session_id);
        let dm = sessions.get(&h.dm_session_id);
        let plan = plans.get(&h.plan_id).map(CampaignPlan::fields);
        let campaign = first(
            connection,
            "SELECT lifecycle, writer, revision, current_lock_id FROM campaigns WHERE campaign_id = ?",
            &[&h.campaign_id],
        )?;
        let campaign = campaign.as_ref().map(|(columns, values)| Named { columns, values: values.clone() });
        let turn = first(
            connection,
            "SELECT status FROM turns WHERE campaign_id = ? AND turn_id = ?",
            &[&h.campaign_id, &h.completed_turn_id],
        )?;
        let revision = first(
            connection,
            "SELECT handoff_id FROM campaign_revisions WHERE campaign_id = ? AND revision = ?",
            &[&h.campaign_id, &h.resulting_campaign_revision],
        )?;
        let mut dm_projection_is_valid = false;
        if let (Some(campaign), Some(dm)) = (&campaign, dm) {
            let lifecycle = campaign.text("lifecycle")?;
            let writer = campaign.text("writer")?;
            if lifecycle == "active" {
                dm_projection_is_valid = writer == "dm"
                    && matches!(dm.fields().status, SessionStatus::Active | SessionStatus::Stopped);
            } else if writer == "none" {
                if let Some((state_columns, state)) = first(
                    connection,
                    "SELECT designated_session_id, tombstone_prior_session_id \
                     FROM campaign_lifecycle_state WHERE campaign_id = ?",
                    &[&h.campaign_id],
                )? {
                    let state = Named { columns: &state_columns, values: state };
                    if dm.fields().status == SessionStatus::Stopped {
                        if lifecycle == "archived" {
                            dm_projection_is_valid =
                                text_of(state.get("designated_session_id")?) == Some(&h.dm_session_id);
                        } else if lifecycle == "tombstoned" {
                            dm_projection_is_valid =
                                text_of(state.get("tombstone_prior_session_id")?) == Some(&h.dm_session_id);
                        }
                    }
                }
            }
        }
        let builder_ok = builder.is_some_and(|builder| {
            builder.campaign_id() == Some(h.campaign_id.as_str())
                && builder.fields().parent_session_id.is_none()
                && builder.role() == WriterRole::Builder
                && matches!(builder.fields().status, SessionStatus::Active | SessionStatus::Stopped)
                && builder.fields().selected_skill.as_deref() == Some("BUILD CAMPAIGN PLAN")
        });
        let dm_ok = dm.is_some_and(|dm| {
            dm.campaign_id() == Some(h.campaign_id.as_str())
                && dm.fields().parent_session_id.is_none()
                && dm.role() == WriterRole::Dm
        }) && dm_projection_is_valid;
        let plan_ok = plan.is_some_and(|plan| {
            plan.campaign_id == h.campaign_id
                && plan.source_session_id == h.builder_session_id
                && plan.package_lock_id == h.package_lock_id
                && plan.plan_revision == h.plan_revision
                && plan.content_sha256 == h.plan_content_sha256
                && plan.resulting_campaign_revision <= h.expected_revision
        });
        // Python evaluates these in order and stops at the first failure, so the lock walk
        // (which can raise) only runs once everything before it holds.
        if !builder_ok || !dm_ok || !plan_ok {
            return invalid();
        }
        let Some(campaign) = &campaign else { return invalid() };
        if py_int(campaign.get("revision")?).ok_or(Flow::Invalid)? < h.resulting_campaign_revision
            || !crate::packages::lock_is_ancestor(connection, &h.campaign_id, &h.package_lock_id)?
            || turn.is_none_or(|(_, row)| py_str(&row[0]) != "completed")
            || revision.is_none_or(|(_, row)| py_str(&row[0]) != h.handoff_id)
        {
            return invalid();
        }
        // The immutable handoff certifies its original package basis; later supported
        // activation keeps it as an ancestor.
        let handoff_lock = read_lock_record(connection, &h.package_lock_id)?;
        let candidate = match &h.package_candidate_id {
            None => None,
            Some(id) => Some(crate::packages::read_candidate(connection, id)?),
        };
        if handoff_lock.lock_id != h.package_lock_id
            || !handoff_candidate_matches_lock(&handoff_lock, candidate.as_ref())
        {
            return invalid();
        }
        let primary = crate::packages::primary_manifest_for_lock(connection, &h.package_lock_id)?;
        let exact_minimum = primary.map(|manifest| manifest.minimum_model_capability_tier);
        let guidance = &h.model_guidance;
        if guidance.fields().minimum_tier_id != exact_minimum || !guidance.may_continue() {
            return invalid();
        }
        // The opening records its original model, not today's session choice.
        let chronology = load_payload(&row.text("safe_payload_json")?)?;
        let dm = dm.expect("checked above");
        let opening_provider = match chronology.get("dm_provider_id") {
            None => dm.fields().provider_id.clone(),
            Some(Json::String(provider)) => provider.clone(),
            Some(_) => return invalid(),
        };
        let expected = builder_dm_handoff_payload_sha256(&BuilderDMHandoffRequest {
            handoff_id: h.handoff_id.clone(),
            campaign_id: h.campaign_id.clone(),
            builder_session_id: h.builder_session_id.clone(),
            dm_session_id: h.dm_session_id.clone(),
            dm_provider_id: opening_provider,
            dm_model_id: guidance.model_id().to_owned(),
            expected_writer: WriterRole::Builder,
            expected_revision: h.expected_revision,
            expected_package_lock_id: h.package_lock_id.clone(),
            expected_package_candidate_id: h.package_candidate_id.clone(),
            plan_id: h.plan_id.clone(),
            expected_plan_revision: h.plan_revision,
            expected_plan_content_sha256: h.plan_content_sha256.clone(),
            completed_turn_id: h.completed_turn_id.clone(),
            model_guidance_sha256: model_guidance_payload_sha256(guidance),
        });
        if expected != h.payload_sha256 {
            return invalid();
        }
    }

    let handoff_builders: HashSet<&str> =
        handoffs.values().map(|handoff| handoff.fields().builder_session_id.as_str()).collect();
    for session in sessions.values() {
        let fields = session.fields();
        if fields.status == SessionStatus::HandoffClosed
            && !handoff_builders.contains(fields.session_id.as_str())
        {
            return invalid();
        }
        if let (Some(parent_id), SessionStatus::Completed) = (&fields.parent_session_id, fields.status) {
            // `messages_by_session[...]`: a missing parent is a `KeyError`.
            let Some(parent_messages) = messages_by_session.get(parent_id) else { return invalid() };
            if !parent_messages.iter().any(|message| {
                message.kind() == SessionMessageKind::Evidence
                    && message.content().get("child_session_id").and_then(Json::as_str)
                        == Some(fields.session_id.as_str())
                    && message.content().get("authoritative") == Some(&Json::Bool(false))
            }) {
                return invalid();
            }
        }
    }
    Ok(())
}
