//! `sqlite_campaign.verify_f5_campaign_authority`: every published manifest's participant
//! declarations, every participant adaptation's provenance, and every participant's exact proof or
//! quarantine evidence.
//!
//! Python runs it at startup on a campaign-schema database (before the continuity verifier), on
//! a backup copy from schema 5, and before committing a guided binding or a guided start. With its
//! helpers `_published_manifests`, `artifact_from_adaptation_row` and `adaptation_payload_sha256`.

use crate::campaigns::{named_rows, parse, Named};
use crate::error::{value_error, StoreResult};
use crate::participant_history::participant_data_history;
use crate::pytext::py_str;
use crate::schema::parse_mapping;
use rusqlite::types::Value;
use rusqlite::{Connection, OptionalExtension};
use std::collections::{HashMap, HashSet};
use sylvia_base::hash::sha256_hex;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{
    validate_authority_identity, Participant, ParticipantArtifact, ParticipantFields,
    ParticipantValidationStatus,
};
use sylvia_domain::package_library::{canonical_json_bytes, manifest_from_record, PackageManifest};
use sylvia_domain::pyops::py_eq;
use sylvia_domain::schema_admission::validate_json_value;

/// `_published_manifests`: every published manifest, parsed before any is checked.
fn published_manifests(connection: &Connection) -> StoreResult<Vec<PackageManifest>> {
    let rows = named_rows(
        connection,
        "SELECT canonical_json FROM package_manifests \
         WHERE status = 'published' ORDER BY package_id, version, content_sha256",
        &[],
    )?
    .1;
    rows.iter()
        .map(|row| {
            let record = Json::Object(parse_mapping(&py_str(&row[0]))?);
            Ok(manifest_from_record(&record, None)?)
        })
        .collect()
}

/// `adaptation_payload_sha256`: the SHA-256 of one complete semantic adaptation request.
#[allow(clippy::too_many_arguments)]
pub(crate) fn adaptation_payload_sha256(
    source_campaign_id: &str,
    participant_id: &str,
    destination_package_id: &str,
    destination_package_version: &str,
    destination_package_sha256: &str,
    destination_kind: &str,
    destination_schema_id: &str,
    destination_schema_sha256: &str,
    data: &JsonMap,
) -> String {
    let mut payload = JsonMap::new();
    for (key, value) in [
        ("source_campaign_id", source_campaign_id),
        ("participant_id", participant_id),
        ("destination_package_id", destination_package_id),
        ("destination_package_version", destination_package_version),
        ("destination_package_sha256", destination_package_sha256),
        ("destination_kind", destination_kind),
        ("destination_schema_id", destination_schema_id),
        ("destination_schema_sha256", destination_schema_sha256),
    ] {
        payload.insert(key.to_owned(), Json::String(value.to_owned()));
    }
    payload.insert("data".to_owned(), Json::Object(data.clone()));
    sha256_hex(canonical_json_bytes(&Json::Object(payload)))
}

/// `artifact_from_adaptation_row`: the exact destination artifact a durable adaptation records.
pub(crate) fn artifact_from_adaptation_row(row: &Named<'_>) -> StoreResult<ParticipantArtifact> {
    Ok(Participant::new(ParticipantFields {
        participant_id: row.text("participant_id")?,
        kind: row.text("destination_kind")?,
        package_id: row.text("destination_package_id")?,
        package_version: row.text("destination_package_version")?,
        data: parse_mapping(&row.text("data_json")?)?,
        package_sha256: Some(row.text("destination_package_sha256")?),
        schema_id: Some(row.text("destination_schema_id")?),
        schema_sha256: Some(row.text("destination_schema_sha256")?),
        validation_status: "validated".to_owned(),
        adaptation_id: Some(row.text("operation_id")?),
    })?)
}

fn sha256_of(value: &Json) -> String {
    sha256_hex(canonical_json_bytes(value))
}

/// The declaration row `(schema_id, schema_sha256, schema_json)` of one exact package and kind.
fn declaration(
    connection: &Connection,
    package_id: &str,
    version: &str,
    content_sha256: Option<&str>,
    kind: &str,
) -> StoreResult<Option<(String, String, String)>> {
    Ok(connection
        .query_row(
            "SELECT schema_id, schema_sha256, schema_json FROM package_participant_artifacts \
             WHERE package_id = ? AND version = ? AND content_sha256 = ? AND kind = ?",
            rusqlite::params![package_id, version, content_sha256, kind],
            |row| Ok((row.get::<_, Value>(0)?, row.get::<_, Value>(1)?, row.get::<_, Value>(2)?)),
        )
        .optional()?
        .map(|(id, sha256, schema)| (py_str(&id), py_str(&sha256), py_str(&schema))))
}

/// `verify_f5_campaign_authority`.
pub fn verify_f5_campaign_authority(connection: &Connection) -> StoreResult<()> {
    for manifest in published_manifests(connection)? {
        let expected: Vec<Vec<Value>> = manifest
            .participant_artifacts
            .iter()
            .enumerate()
            .map(|(order, declaration)| {
                vec![
                    Value::Integer(order as i64),
                    Value::Text(declaration.kind.clone()),
                    Value::Text(declaration.schema_id.clone()),
                    Value::Text(declaration.schema_sha256.clone()),
                    Value::Text(
                        String::from_utf8(canonical_json_bytes(&Json::Object(declaration.schema.clone())))
                            .expect("canonical JSON is UTF-8"),
                    ),
                ]
            })
            .collect();
        let (package_id, version, sha256) = manifest.identity();
        let stored = named_rows(
            connection,
            "SELECT artifact_order, kind, schema_id, schema_sha256, schema_json \
             FROM package_participant_artifacts WHERE package_id = ? AND version = ? \
             AND content_sha256 = ? ORDER BY artifact_order",
            &[&package_id, &version, &sha256],
        )?
        .1;
        if stored != expected {
            return Err(value_error("Stored participant declaration authority is invalid"));
        }
    }
    let adaptations = verify_adaptations(connection)?;
    verify_participants(connection, &adaptations)
}

/// The adaptation half: each `participant_adaptations` row's identities, source, payload hash,
/// declaration and data. Returns the artifacts by operation identity.
fn verify_adaptations(connection: &Connection) -> StoreResult<HashMap<String, ParticipantArtifact>> {
    let mut adaptations = HashMap::new();
    let (columns, rows) =
        named_rows(connection, "SELECT * FROM participant_adaptations ORDER BY operation_id", &[])?;
    for values in rows {
        let row = Named { columns: &columns, values };
        let operation_id = row.text("operation_id")?;
        for (column, label) in [
            ("operation_id", "Adaptation operation identity"),
            ("source_campaign_id", "Adaptation source campaign identity"),
            ("participant_id", "Adaptation participant identity"),
            ("source_kind", "Adaptation source kind"),
            ("source_package_id", "Adaptation source package identity"),
            ("source_package_version", "Adaptation source package version"),
            ("destination_kind", "Adaptation destination kind"),
            ("destination_package_id", "Adaptation destination package identity"),
            ("destination_package_version", "Adaptation destination package version"),
            ("destination_schema_id", "Adaptation destination schema identity"),
        ] {
            validate_authority_identity(&row.text(column)?, label, false)?;
        }
        for (column, label) in [
            ("payload_sha256", "Adaptation payload SHA-256"),
            ("source_data_sha256", "Adaptation source data SHA-256"),
            ("destination_package_sha256", "Adaptation destination package SHA-256"),
            ("destination_schema_sha256", "Adaptation destination schema SHA-256"),
        ] {
            validate_authority_identity(&row.text(column)?, label, true)?;
        }
        for (column, label) in [
            ("source_package_sha256", "Adaptation source package SHA-256"),
            ("source_schema_id", "Adaptation source schema identity"),
            ("source_schema_sha256", "Adaptation source schema SHA-256"),
        ] {
            if let Some(value) = row.optional_text(column)? {
                validate_authority_identity(&value, label, column.ends_with("sha256"))?;
            }
        }
        let (source_campaign_id, participant_id) =
            (row.text("source_campaign_id")?, row.text("participant_id")?);
        let (source_columns, mut source_rows) = named_rows(
            connection,
            "SELECT * FROM participants WHERE campaign_id = ? AND participant_id = ?",
            &[&source_campaign_id, &participant_id],
        )?;
        if source_rows.is_empty() {
            return Err(value_error("Stored participant adaptation source is unavailable"));
        }
        let source = Named { columns: &source_columns, values: source_rows.swap_remove(0) };
        let source_data = parse_mapping(&source.text("data_json")?)?;
        // Python also hashes the source data here, for a tuple member it never compares.
        let source_identity = (
            source.text("kind")?,
            source.text("package_id")?,
            source.text("package_version")?,
            source.optional_text("package_sha256")?,
            source.optional_text("schema_id")?,
            source.optional_text("schema_sha256")?,
        );
        let recorded_source_identity = (
            row.text("source_kind")?,
            row.text("source_package_id")?,
            row.text("source_package_version")?,
            row.optional_text("source_package_sha256")?,
            row.optional_text("source_schema_id")?,
            row.optional_text("source_schema_sha256")?,
        );
        let recorded_source_data_sha256 = row.text("source_data_sha256")?;
        let artifact = artifact_from_adaptation_row(&row)?;
        let expected_payload_sha256 = adaptation_payload_sha256(
            &source_campaign_id,
            artifact.participant_id(),
            artifact.package_id(),
            artifact.package_version(),
            artifact.package_sha256().unwrap_or(""),
            artifact.kind(),
            artifact.schema_id().unwrap_or(""),
            artifact.schema_sha256().unwrap_or(""),
            artifact.data(),
        );
        let declaration = declaration(
            connection,
            artifact.package_id(),
            artifact.package_version(),
            artifact.package_sha256(),
            artifact.kind(),
        )?;
        // Python's `or` chain, in order: the history is read only when the identities match.
        let invalid = source_identity != recorded_source_identity
            || !participant_data_history(connection, &source_campaign_id, &participant_id, source_data)?
                .iter()
                .map(sha256_of)
                .collect::<HashSet<_>>()
                .contains(&recorded_source_data_sha256)
            || row.text("payload_sha256")? != expected_payload_sha256
            || match &declaration {
                None => true,
                Some((schema_id, schema_sha256, _)) => {
                    Some(schema_id.as_str()) != artifact.schema_id()
                        || Some(schema_sha256.as_str()) != artifact.schema_sha256()
                }
            };
        let Some((_, _, schema_json)) = declaration.filter(|_| !invalid) else {
            return Err(value_error("Stored participant adaptation authority is invalid"));
        };
        validate_json_value(
            &Json::Object(artifact.data().clone()),
            &parse_mapping(&schema_json)?,
            &format!("Participant {}", artifact.participant_id()),
        )?;
        adaptations.insert(operation_id, artifact);
    }
    Ok(adaptations)
}

/// The participant half: each participant's quarantine evidence or exact validated proof, and its
/// adaptation provenance; then no quarantine without its participant.
fn verify_participants(
    connection: &Connection,
    adaptations: &HashMap<String, ParticipantArtifact>,
) -> StoreResult<()> {
    let mut quarantines: HashMap<(String, String), String> = HashMap::new();
    let (columns, rows) = named_rows(connection, "SELECT * FROM participant_quarantines", &[])?;
    for values in rows {
        let row = Named { columns: &columns, values };
        quarantines.insert((row.text("campaign_id")?, row.text("participant_id")?), row.text("reason_code")?);
    }
    let mut seen_quarantines: HashSet<(String, String)> = HashSet::new();
    let (columns, rows) =
        named_rows(connection, "SELECT * FROM participants ORDER BY campaign_id, participant_id", &[])?;
    for values in rows {
        let row = Named { columns: &columns, values };
        let identity = (row.text("campaign_id")?, row.text("participant_id")?);
        let data = parse_mapping(&row.text("data_json")?)?;
        let fields = ParticipantFields {
            participant_id: identity.1.clone(),
            kind: row.text("kind")?,
            package_id: row.text("package_id")?,
            package_version: row.text("package_version")?,
            data,
            package_sha256: row.optional_text("package_sha256")?,
            schema_id: row.optional_text("schema_id")?,
            schema_sha256: row.optional_text("schema_sha256")?,
            // `ParticipantValidationStatus(...)` is converted before the dataclass checks run.
            validation_status: parse(
                &row.text("validation_status")?,
                ParticipantValidationStatus::parse,
                "ParticipantValidationStatus",
            )?
            .as_str()
            .to_owned(),
            adaptation_id: row.optional_text("adaptation_id")?,
        };
        let participant = Participant::new(fields)?;
        if participant.validation_status() == ParticipantValidationStatus::Quarantined {
            if participant.schema_id().is_some()
                || participant.schema_sha256().is_some()
                || participant.adaptation_id().is_some()
            {
                return Err(value_error("Quarantined participant claims schema proof"));
            }
            if quarantines.get(&identity).is_none_or(String::is_empty) {
                return Err(value_error("Quarantined participant lacks evidence"));
            }
            seen_quarantines.insert(identity);
            continue;
        }
        let declaration = declaration(
            connection,
            participant.package_id(),
            participant.package_version(),
            participant.package_sha256(),
            participant.kind(),
        )?;
        let lock_membership = connection
            .query_row(
                "SELECT 1 FROM campaign_locks AS lock \
                 JOIN campaign_lock_memberships AS membership ON membership.lock_id = lock.lock_id \
                 WHERE lock.campaign_id = ? AND lock.predecessor_lock_id IS NULL \
                 AND membership.package_id = ? AND membership.version = ? \
                 AND membership.content_sha256 = ?",
                rusqlite::params![
                    identity.0,
                    participant.package_id(),
                    participant.package_version(),
                    participant.package_sha256()
                ],
                |_| Ok(()),
            )
            .optional()?;
        let schema_json = match (&declaration, lock_membership) {
            (Some((schema_id, schema_sha256, schema_json)), Some(()))
                if Some(schema_id.as_str()) == participant.schema_id()
                    && Some(schema_sha256.as_str()) == participant.schema_sha256()
                    && !quarantines.contains_key(&identity) =>
            {
                schema_json
            }
            _ => return Err(value_error("Validated participant exact authority is invalid")),
        };
        validate_json_value(
            &Json::Object(participant.data().clone()),
            &parse_mapping(schema_json)?,
            &format!("Participant {}", participant.participant_id()),
        )?;
        if let Some(adaptation_id) = participant.adaptation_id() {
            let Some(adaptation) = adaptations.get(adaptation_id) else {
                return Err(value_error("Participant adaptation provenance does not match"));
            };
            // Python builds every pair before comparing, so the history is always read here.
            let history = participant_data_history(
                connection,
                &identity.0,
                participant.participant_id(),
                participant.data().clone(),
            )?;
            let data = Json::Object(adaptation.data().clone());
            let matches = participant.participant_id() == adaptation.participant_id()
                && participant.kind() == adaptation.kind()
                && participant.package_id() == adaptation.package_id()
                && participant.package_version() == adaptation.package_version()
                && participant.package_sha256() == adaptation.package_sha256()
                && participant.schema_id() == adaptation.schema_id()
                && participant.schema_sha256() == adaptation.schema_sha256()
                && history.iter().any(|value| py_eq(&data, value));
            if !matches {
                return Err(value_error("Participant adaptation provenance does not match"));
            }
        }
    }
    let all: HashSet<(String, String)> = quarantines.into_keys().collect();
    if seen_quarantines != all {
        return Err(value_error("Participant quarantine evidence is orphaned"));
    }
    Ok(())
}
