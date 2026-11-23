//! Package installation: bytes-first staging, one publication transaction, the candidate and
//! manifest reads, and the stored-package verifier run at startup and on backups.
//!
//! Python: `sqlite_packages.py` (`begin_staging`, `append_staging_chunk`, `_exact_rows`,
//! `_insert_or_compare_blob`, `_insert_or_compare_manifest`, `publish_archive`, `exact_manifest`,
//! `candidate_participant_declarations`, `exact_participant_declarations`, `read_candidate`,
//! `list_candidates`, `read_staging`, `verify_published_lock`, `verify_blob`,
//! `_activation_result_from_row`, `_compatible_migration_identity`, `verify_package_authority`),
//! `sqlite_campaign.insert_or_compare_declarations`, `sqlite_draft_history.py`, and the
//! package operations of `SQLiteAuthorityStore` (`stage_package_archive`,
//! `publish_package_archive` with its `_before_package_publication` seam, `package_candidate(s)`,
//! `package_staging`, `exact_package_manifest`, `package_participant_declarations`,
//! `exact_participant_declarations`).
//!
//! The archive comes admitted by lane 1's `package_archive::admit_package_archive`. Python
//! streams each file in 64 KiB reads (`iter_file_chunks`); a zip member's `read(n)` returns
//! exactly `n` bytes until the end, so splitting lane 1's `read_file` into 64 KiB pieces gives
//! the same chunks. It holds one member (at most 64 MiB) in memory at a time.

use crate::actions::{dump, rejected};
use crate::campaigns::{
    manifest_from_identity, named_rows, package_lock_from_memberships, parse_canonical, read_lock_record,
    Named,
};
use crate::error::{schema_identity, StoreError, StoreResult};
use crate::pytext::{py_int, py_str};
use crate::store::AuthorityStore;
use rusqlite::types::Value;
use rusqlite::{params, Connection, OptionalExtension};
use std::collections::{BTreeMap, BTreeSet, HashSet};
use sylvia_base::hash::sha256_hex;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{owned_payload, validate_authority_identity, PackageLock};
use sylvia_domain::package_archive::{AdmittedPackageArchive, PACKAGE_CHUNK_LIMIT};
use sylvia_domain::package_library::{
    activation_payload_sha256, canonical_json_bytes, closure_sha256, manifest_from_record,
    parse_campaign_setting_knowledge_bytes, parse_content_declarations_bytes, resolve_exact_closure,
    validate_campaign_setting_knowledge_links, validate_content_declaration_links, ActivationCommand,
    ActivationResult, ActivationStatus, CandidateStatus, PackageCandidate, PackageLockAvailability,
    PackageManifest, PackageStagingRecord, ParticipantArtifactDeclaration, CAMPAIGN_SETTING_KNOWLEDGE_PATH,
};
use sylvia_domain::pyops::py_eq;
use sylvia_domain::DomainError;

/// `sqlite_packages._dump`: canonical JSON text.
fn canonical_dump(value: &Json) -> String {
    String::from_utf8(canonical_json_bytes(value)).expect("canonical JSON is UTF-8")
}

fn sha(bytes: &[u8]) -> String {
    sha256_hex(bytes)
}

fn idempotency_conflict(message: &str) -> StoreError {
    StoreError::Coded(crate::error::authority_coded("IdempotencyConflict", message))
}

/// The bytes of one admitted file in Python's 64 KiB reads.
fn file_chunks(admitted: &AdmittedPackageArchive, index: usize) -> StoreResult<Vec<Vec<u8>>> {
    let bytes = admitted.read_file(&admitted.files[index])?;
    Ok(bytes.chunks(PACKAGE_CHUNK_LIMIT).map(<[u8]>::to_vec).collect())
}

/// `begin_staging`: a new install, or the retry of an unfinished one with its chunks cleared.
fn begin_staging(connection: &Connection, install_id: &str, manifest_json: &str) -> StoreResult<()> {
    let existing: Option<(Value, Value)> = connection
        .query_row(
            "SELECT status, manifest_json FROM package_staging WHERE install_id = ?",
            [install_id],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .optional()?;
    if let Some((status, stored)) = existing {
        if py_str(&stored) != manifest_json {
            return Err(idempotency_conflict("Install identity payload does not match"));
        }
        if py_str(&status) != "staging" {
            return Err(idempotency_conflict("Install identity is already terminal"));
        }
        connection.execute("DELETE FROM package_staging_chunks WHERE install_id = ?", [install_id])?;
        return Ok(());
    }
    let mut report = JsonMap::new();
    report.insert("code".into(), "staging".into());
    report.insert("label".into(), "Package staging".into());
    connection.execute(
        "INSERT INTO package_staging VALUES (?, 'staging', ?, ?)",
        params![install_id, manifest_json, canonical_dump(&Json::Object(report))],
    )?;
    Ok(())
}

/// `_exact_rows`: every row as a tuple of values.
fn exact_rows(
    connection: &Connection,
    query: &str,
    values: &[&dyn rusqlite::ToSql],
) -> StoreResult<Vec<Vec<Value>>> {
    Ok(named_rows(connection, query, values)?.1)
}

/// `_insert_or_compare_blob`: content-addressed bytes, inserted once and compared after.
fn insert_or_compare_blob(connection: &Connection, blob_sha256: &str, chunks: &[Vec<u8>]) -> StoreResult<()> {
    let byte_count = i64::try_from(chunks.iter().map(Vec::len).sum::<usize>())
        .map_err(|_| StoreError::Other("package blob byte count overflow".into()))?;
    if sha(&chunks.concat()) != blob_sha256 {
        return Err(rejected("Staged package blob hash does not match", "blob_hash_mismatch"));
    }
    let existing: Option<(Value, Value)> = connection
        .query_row(
            "SELECT byte_count, chunk_count FROM package_blobs WHERE blob_sha256 = ?",
            [blob_sha256],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .optional()?;
    let expected: Vec<Vec<Value>> = chunks
        .iter()
        .enumerate()
        .map(|(order, chunk)| {
            vec![
                Value::Integer(order as i64),
                Value::Integer(chunk.len() as i64),
                Value::Text(sha(chunk)),
                Value::Blob(chunk.clone()),
            ]
        })
        .collect();
    if let Some((stored_bytes, stored_chunks)) = existing {
        let stored = exact_rows(
            connection,
            "SELECT chunk_order, byte_count, chunk_sha256, data FROM package_blob_chunks \
             WHERE blob_sha256 = ? ORDER BY chunk_order",
            &[&blob_sha256],
        )?;
        if py_int(&stored_bytes) != Some(byte_count)
            || py_int(&stored_chunks) != Some(chunks.len() as i64)
            || stored != expected
        {
            return Err(rejected(
                "Immutable package blob conflicts with stored bytes",
                "package_identity_conflict",
            ));
        }
        return Ok(());
    }
    connection.execute(
        "INSERT INTO package_blobs VALUES (?, ?, ?)",
        params![blob_sha256, byte_count, chunks.len() as i64],
    )?;
    for (order, chunk) in chunks.iter().enumerate() {
        connection.execute(
            "INSERT INTO package_blob_chunks VALUES (?, ?, ?, ?, ?)",
            params![blob_sha256, order as i64, chunk.len() as i64, sha(chunk), chunk],
        )?;
    }
    Ok(())
}

fn identity_values(manifest: &PackageManifest) -> [Value; 3] {
    let (package_id, version, sha256) = manifest.identity();
    [Value::Text(package_id.into()), Value::Text(version.into()), Value::Text(sha256.into())]
}

/// The `packages` row of one manifest, inserted once and compared after.
fn insert_or_compare_package(connection: &Connection, manifest: &PackageManifest) -> StoreResult<()> {
    let (package_id, version, sha256) = manifest.identity();
    let capabilities_json =
        canonical_dump(&Json::Array(manifest.capabilities.iter().cloned().map(Json::String).collect()));
    connection.execute(
        "INSERT OR IGNORE INTO packages(package_id, version, sha256, capabilities_json) VALUES (?, ?, ?, ?)",
        params![package_id, version, sha256, capabilities_json],
    )?;
    let stored: Option<Value> = connection
        .query_row(
            "SELECT capabilities_json FROM packages WHERE package_id = ? AND version = ? AND sha256 = ?",
            params![package_id, version, sha256],
            |row| row.get(0),
        )
        .optional()?;
    if stored.map(|value| py_str(&value)) != Some(capabilities_json) {
        return Err(rejected(
            "Package identity conflicts with immutable metadata",
            "package_identity_conflict",
        ));
    }
    Ok(())
}

/// `sqlite_campaign.insert_or_compare_declarations`: immutable participant declarations, when
/// migration 5 is present.
pub(crate) fn insert_or_compare_declarations(
    connection: &Connection,
    manifest: &PackageManifest,
) -> StoreResult<()> {
    let present = connection
        .query_row(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'package_participant_artifacts'",
            [],
            |_| Ok(()),
        )
        .optional()?
        .is_some();
    if !present {
        return Ok(());
    }
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
                Value::Text(canonical_dump(&Json::Object(declaration.schema.clone()))),
            ]
        })
        .collect();
    let identity = identity_values(manifest);
    let stored = exact_rows(
        connection,
        "SELECT artifact_order, kind, schema_id, schema_sha256, schema_json \
         FROM package_participant_artifacts WHERE package_id = ? AND version = ? \
         AND content_sha256 = ? ORDER BY artifact_order",
        &[&identity[0], &identity[1], &identity[2]],
    )?;
    if !stored.is_empty() {
        if stored != expected {
            return Err(crate::error::value_error("Immutable participant declarations conflict"));
        }
        return Ok(());
    }
    for row in &expected {
        connection.execute(
            "INSERT INTO package_participant_artifacts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            params![identity[0], identity[1], identity[2], row[0], row[1], row[2], row[3], row[4]],
        )?;
    }
    Ok(())
}

/// `_insert_or_compare_manifest`: the manifest and its immutable children, inserted once and
/// compared after.
fn insert_or_compare_manifest(connection: &Connection, manifest: &PackageManifest) -> StoreResult<()> {
    insert_or_compare_package(connection, manifest)?;
    let identity = identity_values(manifest);
    let [id, version, sha256] = &identity;
    for (order, dependency) in manifest.dependencies.iter().enumerate() {
        connection.execute(
            "INSERT OR IGNORE INTO package_dependencies \
             (package_id, version, sha256, dependency_order, dependency_id, \
             dependency_version, dependency_sha256, dependency_kind, dependency_namespace) \
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            params![
                id,
                version,
                sha256,
                order as i64,
                dependency.package_id(),
                dependency.version(),
                dependency.content_sha256(),
                dependency.kind(),
                dependency.namespace()
            ],
        )?;
    }
    let stored_dependencies = exact_rows(
        connection,
        "SELECT dependency_order, dependency_id, dependency_version, dependency_sha256, \
         dependency_kind, dependency_namespace FROM package_dependencies \
         WHERE package_id = ? AND version = ? AND sha256 = ? ORDER BY dependency_order",
        &[id, version, sha256],
    )?;
    let expected_dependencies: Vec<Vec<Value>> = manifest
        .dependencies
        .iter()
        .enumerate()
        .map(|(order, dependency)| {
            vec![
                Value::Integer(order as i64),
                Value::Text(dependency.package_id().into()),
                Value::Text(dependency.version().into()),
                Value::Text(dependency.content_sha256().into()),
                Value::Text(dependency.kind().into()),
                Value::Text(dependency.namespace().into()),
            ]
        })
        .collect();
    if stored_dependencies != expected_dependencies {
        return Err(rejected("Package dependency metadata conflicts", "package_identity_conflict"));
    }
    let existing = exact_rows(
        connection,
        "SELECT role, canonical_json, metadata_json, provenance_json, compatibility_json, status \
         FROM package_manifests WHERE package_id = ? AND version = ? AND content_sha256 = ?",
        &[id, version, sha256],
    )?;
    let expected = vec![
        Value::Text(manifest.role.as_str().into()),
        Value::Text(manifest.canonical_json.clone()),
        Value::Text(canonical_dump(&Json::Object(manifest.metadata.clone()))),
        Value::Text(canonical_dump(&Json::Object(manifest.provenance.clone()))),
        Value::Text(canonical_dump(&Json::Object(manifest.compatibility.clone()))),
        Value::Text("published".into()),
    ];
    if let Some(existing) = existing.first() {
        if existing != &expected {
            return Err(rejected("Package manifest identity conflicts", "package_identity_conflict"));
        }
        return insert_or_compare_declarations(connection, manifest);
    }
    connection.execute(
        "INSERT INTO package_manifests VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'published')",
        params![id, version, sha256, expected[0], expected[1], expected[2], expected[3], expected[4]],
    )?;
    for (order, capability) in manifest.capabilities.iter().enumerate() {
        connection.execute(
            "INSERT INTO package_capabilities VALUES (?, ?, ?, ?, ?)",
            params![id, version, sha256, order as i64, capability],
        )?;
    }
    for (order, tool) in manifest.tools.iter().enumerate() {
        let predicates: Vec<Json> = tool
            .state_predicates
            .iter()
            .map(|item| {
                let mut predicate = JsonMap::new();
                predicate.insert("namespace".into(), item.namespace.clone().into());
                predicate.insert("key".into(), item.key.clone().into());
                predicate.insert("equals".into(), item.equals.clone());
                if item.negated {
                    predicate.insert("negated".into(), Json::Bool(true));
                }
                Json::Object(predicate)
            })
            .collect();
        let strings = |items: &[String]| {
            canonical_dump(&Json::Array(items.iter().cloned().map(Json::String).collect()))
        };
        connection.execute(
            "INSERT INTO package_tools VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            params![
                id,
                version,
                sha256,
                order as i64,
                tool.name,
                tool.capability,
                canonical_dump(&Json::Object(tool.input_schema.clone())),
                canonical_dump(&Json::Array(predicates)),
                strings(&tool.turn_statuses),
                strings(&tool.writers)
            ],
        )?;
    }
    for (order, surface) in manifest.prompt_fragments.iter().enumerate() {
        connection.execute(
            "INSERT INTO package_prompt_fragments VALUES (?, ?, ?, ?, ?, ?)",
            params![id, version, sha256, order as i64, surface.identity, surface.capability],
        )?;
    }
    for (order, surface) in manifest.ui_surfaces.iter().enumerate() {
        connection.execute(
            "INSERT INTO package_ui_surfaces VALUES (?, ?, ?, ?, ?, ?)",
            params![id, version, sha256, order as i64, surface.identity, surface.capability],
        )?;
    }
    insert_or_compare_declarations(connection, manifest)
}

/// `publish_archive`: validate the staged bytes against the admitted archive and publish the
/// candidate, inside the caller's transaction.
fn publish_archive(
    connection: &Connection,
    admitted: &AdmittedPackageArchive,
    install_id: &str,
) -> StoreResult<PackageCandidate> {
    let status: Option<Value> = connection
        .query_row("SELECT status FROM package_staging WHERE install_id = ?", [install_id], |row| row.get(0))
        .optional()?;
    if status.map(|value| py_str(&value)).as_deref() != Some("staging") {
        return Err(rejected("Package staging is unavailable", "staging_unavailable"));
    }
    let manifests = &admitted.manifests;
    for manifest in manifests {
        insert_or_compare_package(connection, manifest)?;
    }
    for manifest in manifests {
        insert_or_compare_manifest(connection, manifest)?;
    }
    let mut file_order: i64 = 0;
    let mut expected_chunked: BTreeSet<i64> = BTreeSet::new();
    for manifest in manifests {
        let identity = identity_values(manifest);
        for (index, manifest_file) in manifest.files.iter().enumerate() {
            let rows = exact_rows(
                connection,
                "SELECT chunk_order, chunk_sha256, data FROM package_staging_chunks \
                 WHERE install_id = ? AND file_order = ? ORDER BY chunk_order",
                &[&install_id, &file_order],
            )?;
            if rows.iter().enumerate().any(|(order, row)| py_int(&row[0]) != Some(order as i64)) {
                return Err(rejected("Staged chunk order is incomplete", "staging_invalid"));
            }
            let chunks: Vec<Vec<u8>> =
                rows.iter().map(|row| blob_bytes(&row[2])).collect::<StoreResult<_>>()?;
            if rows.iter().zip(&chunks).any(|(row, chunk)| sha(chunk) != py_str(&row[1])) {
                return Err(rejected("Staged chunk hash is invalid", "staging_invalid"));
            }
            if chunks.iter().map(Vec::len).sum::<usize>() as i64 != manifest_file.byte_count() {
                return Err(rejected("Staged file byte count is invalid", "staging_invalid"));
            }
            if manifest_file.byte_count() != 0 {
                expected_chunked.insert(file_order);
            }
            insert_or_compare_blob(connection, manifest_file.sha256(), &chunks)?;
            let existing = exact_rows(
                connection,
                "SELECT portable_path, byte_count, blob_sha256 FROM package_manifest_files \
                 WHERE package_id = ? AND version = ? AND content_sha256 = ? AND file_order = ?",
                &[&identity[0], &identity[1], &identity[2], &(index as i64)],
            )?;
            let expected = vec![
                Value::Text(manifest_file.path().into()),
                Value::Integer(manifest_file.byte_count()),
                Value::Text(manifest_file.sha256().into()),
            ];
            match existing.first() {
                None => {
                    connection.execute(
                        "INSERT INTO package_manifest_files VALUES (?, ?, ?, ?, ?, ?, ?)",
                        params![
                            identity[0],
                            identity[1],
                            identity[2],
                            index as i64,
                            expected[0],
                            expected[1],
                            expected[2]
                        ],
                    )?;
                }
                Some(row) if row != &expected => {
                    return Err(rejected("Package file metadata conflicts", "package_identity_conflict"))
                }
                Some(_) => {}
            }
            file_order += 1;
        }
        let expected_dependencies: Vec<Vec<Value>> = manifest
            .dependencies
            .iter()
            .enumerate()
            .map(|(order, dependency)| {
                vec![
                    Value::Integer(order as i64),
                    Value::Text(dependency.namespace().into()),
                    Value::Text(dependency.kind().into()),
                    Value::Text(dependency.package_id().into()),
                    Value::Text(dependency.version().into()),
                    Value::Text(dependency.content_sha256().into()),
                ]
            })
            .collect();
        let existing_dependencies = exact_rows(
            connection,
            "SELECT dependency_order, dependency_namespace, dependency_kind, dependency_id, \
             dependency_version, dependency_content_sha256 \
             FROM package_exact_dependencies WHERE package_id = ? AND version = ? \
             AND content_sha256 = ? ORDER BY dependency_order",
            &[&identity[0], &identity[1], &identity[2]],
        )?;
        if !existing_dependencies.is_empty() {
            if existing_dependencies != expected_dependencies {
                return Err(rejected("Package dependency metadata conflicts", "package_identity_conflict"));
            }
        } else {
            for dependency in &expected_dependencies {
                connection.execute(
                    "INSERT INTO package_exact_dependencies VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    params![
                        identity[0],
                        identity[1],
                        identity[2],
                        dependency[0],
                        dependency[1],
                        dependency[2],
                        dependency[3],
                        dependency[4],
                        dependency[5]
                    ],
                )?;
            }
        }
    }
    let staged: BTreeSet<i64> = exact_rows(
        connection,
        "SELECT DISTINCT file_order FROM package_staging_chunks WHERE install_id = ? ORDER BY file_order",
        &[&install_id],
    )?
    .iter()
    .map(|row| {
        py_int(&row[0]).ok_or_else(|| crate::error::value_error("invalid literal for int() with base 10"))
    })
    .collect::<StoreResult<_>>()?;
    if staged != expected_chunked {
        return Err(rejected("Staged file set is incomplete", "staging_invalid"));
    }
    let package_lock = &admitted.package_lock;
    let closure = closure_sha256(package_lock);
    let candidate_id = format!("candidate-{install_id}");
    let mut report = JsonMap::new();
    report.insert("code".into(), "validated".into());
    report.insert("label".into(), "Package candidate validated".into());
    let report = owned_payload(&Json::Object(report))?;
    let report_json = canonical_dump(&Json::Object(report.clone()));
    connection.execute(
        "INSERT INTO package_candidates VALUES (?, ?, 'validated', ?, ?)",
        params![candidate_id, install_id, closure, report_json],
    )?;
    let published: HashSet<(&str, &str, &str)> = manifests.iter().map(PackageManifest::identity).collect();
    for entry in package_lock.entries() {
        if !published.contains(&(entry.package_id(), entry.version(), entry.sha256())) {
            return Err(rejected("Candidate closure is incomplete", "closure_invalid"));
        }
        connection.execute(
            "INSERT INTO package_candidate_memberships VALUES (?, ?, ?, ?, ?)",
            params![candidate_id, entry.load_order(), entry.package_id(), entry.version(), entry.sha256()],
        )?;
    }
    connection.execute(
        "UPDATE package_staging SET status = 'published', report_json = ? WHERE install_id = ?",
        params![report_json, install_id],
    )?;
    Ok(PackageCandidate::new(
        candidate_id,
        install_id,
        CandidateStatus::Validated,
        closure,
        package_lock.clone(),
        report,
    )?)
}

/// `bytes(row["data"])` for a stored chunk: a `BLOB` as it is; Python's `bytes(n)` of an
/// integer is `n` zero bytes; anything else is a `TypeError`.
fn blob_bytes(value: &Value) -> StoreResult<Vec<u8>> {
    match value {
        Value::Blob(bytes) => Ok(bytes.clone()),
        Value::Integer(count) if *count >= 0 => Ok(vec![0; usize::try_from(*count).unwrap_or(usize::MAX)]),
        Value::Integer(_) => Err(crate::error::value_error("negative count")),
        Value::Text(_) => Err(StoreError::Other("string argument without an encoding".into())),
        Value::Real(_) => Err(StoreError::Other("cannot convert 'float' object to bytes".into())),
        Value::Null => Err(StoreError::Other("cannot convert 'NoneType' object to bytes".into())),
    }
}

/// `exact_manifest`.
pub fn exact_manifest(connection: &Connection, identity: (&str, &str, &str)) -> StoreResult<PackageManifest> {
    manifest_from_identity(connection, identity)
}

fn membership_identities(
    connection: &Connection,
    candidate_id: &str,
) -> StoreResult<Vec<(String, String, String)>> {
    Ok(exact_rows(
        connection,
        "SELECT package_id, version, content_sha256 FROM package_candidate_memberships \
         WHERE candidate_id = ? ORDER BY closure_order",
        &[&candidate_id],
    )?
    .iter()
    .map(|row| (py_str(&row[0]), py_str(&row[1]), py_str(&row[2])))
    .collect())
}

/// `candidate_participant_declarations`.
pub fn candidate_participant_declarations(
    connection: &Connection,
    candidate_id: &str,
) -> StoreResult<Vec<ParticipantArtifactDeclaration>> {
    let rows = membership_identities(connection, candidate_id)?;
    if rows.is_empty() {
        return Err(rejected("Package candidate is unavailable", "candidate_unavailable"));
    }
    let mut declarations = Vec::new();
    for (package_id, version, sha256) in &rows {
        declarations
            .extend(manifest_from_identity(connection, (package_id, version, sha256))?.participant_artifacts);
    }
    Ok(declarations)
}

/// `exact_participant_declarations`.
pub fn exact_participant_declarations(
    connection: &Connection,
    identity: (&str, &str, &str),
) -> StoreResult<Vec<ParticipantArtifactDeclaration>> {
    Ok(manifest_from_identity(connection, identity)?.participant_artifacts)
}

/// `read_candidate`.
pub fn read_candidate(connection: &Connection, candidate_id: &str) -> StoreResult<PackageCandidate> {
    let (columns, mut rows) =
        named_rows(connection, "SELECT * FROM package_candidates WHERE candidate_id = ?", &[&candidate_id])?;
    if rows.is_empty() {
        return Err(rejected("Package candidate is unavailable", "candidate_unavailable"));
    }
    let row = Named { columns: &columns, values: rows.swap_remove(0) };
    let memberships = exact_rows(
        connection,
        "SELECT closure_order, package_id, version, content_sha256, \
         CASE WHEN closure_order = 0 THEN 'primary' ELSE 'dependency' END AS role \
         FROM package_candidate_memberships WHERE candidate_id = ? ORDER BY closure_order",
        &[&candidate_id],
    )?;
    let lock = package_lock_from_memberships(connection, &memberships)?;
    let status = crate::campaigns::parse(&row.text("status")?, CandidateStatus::parse, "CandidateStatus")?;
    let report = match parse_canonical(&row.text("report_json")?)? {
        Json::Object(map) => map,
        _ => return Err(StoreError::Other("Stored candidate report is not an object".into())),
    };
    Ok(PackageCandidate::new(
        row.text("candidate_id")?,
        row.text("install_id")?,
        status,
        row.text("closure_sha256")?,
        lock,
        report,
    )?)
}

/// `list_candidates`: newest first.
pub fn list_candidates(connection: &Connection) -> StoreResult<Vec<PackageCandidate>> {
    exact_rows(connection, "SELECT candidate_id FROM package_candidates ORDER BY rowid DESC", &[])?
        .iter()
        .map(|row| read_candidate(connection, &py_str(&row[0])))
        .collect()
}

/// `read_staging`.
pub fn read_staging(connection: &Connection, install_id: &str) -> StoreResult<PackageStagingRecord> {
    let status: Option<Value> = connection
        .query_row("SELECT status FROM package_staging WHERE install_id = ?", [install_id], |row| row.get(0))
        .optional()?;
    let Some(status) = status else {
        return Err(rejected("Package staging is unavailable", "staging_unavailable"));
    };
    let count: i64 = connection.query_row(
        "SELECT COUNT(*) FROM package_staging_chunks WHERE install_id = ?",
        [install_id],
        |row| row.get(0),
    )?;
    Ok(PackageStagingRecord {
        install_id: install_id.to_owned(),
        status: py_str(&status),
        chunk_count: count,
    })
}

/// `verify_blob`: the stored chunks of one blob, if they are whole and match its hash.
pub fn verify_blob(connection: &Connection, blob_sha256: &str) -> StoreResult<Vec<Vec<u8>>> {
    let row: Option<(Value, Value)> = connection
        .query_row(
            "SELECT byte_count, chunk_count FROM package_blobs WHERE blob_sha256 = ?",
            [blob_sha256],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .optional()?;
    let Some((byte_count, chunk_count)) = row else {
        return Err(rejected("Exact package blob is unavailable", "package_unavailable"));
    };
    let (columns, rows) = named_rows(
        connection,
        "SELECT * FROM package_blob_chunks WHERE blob_sha256 = ? ORDER BY chunk_order",
        &[&blob_sha256],
    )?;
    let chunks = rows
        .into_iter()
        .map(|values| blob_bytes(Named { columns: &columns, values }.get("data")?))
        .collect::<StoreResult<Vec<_>>>()?;
    let int = |value: &Value| {
        py_int(value).ok_or_else(|| crate::error::value_error("invalid literal for int() with base 10"))
    };
    if chunks.len() as i64 != int(&chunk_count)?
        || chunks.iter().map(Vec::len).sum::<usize>() as i64 != int(&byte_count)?
        || sha(&chunks.concat()) != blob_sha256
    {
        return Err(rejected("Exact package blob is corrupt", "package_corrupt"));
    }
    Ok(chunks)
}

/// `verify_published_lock`: the lock is the exact closure of published manifests whose bytes
/// are whole.
pub fn verify_published_lock(connection: &Connection, package_lock: &PackageLock) -> StoreResult<()> {
    if package_lock.entries().is_empty() {
        return Ok(());
    }
    let manifests = package_lock
        .entries()
        .iter()
        .map(|entry| {
            manifest_from_identity(connection, (entry.package_id(), entry.version(), entry.sha256()))
        })
        .collect::<StoreResult<Vec<_>>>()?;
    if &resolve_exact_closure(&manifests)? != package_lock {
        return Err(rejected("Package lock does not match published exact closure", "package_unavailable"));
    }
    for manifest in &manifests {
        for file in &manifest.files {
            verify_blob(connection, file.sha256())?;
        }
    }
    Ok(())
}

/// `primary_manifest_for_lock`: the exact primary manifest of a lock, `None` for no rules.
pub fn primary_manifest_for_lock(
    connection: &Connection,
    lock_id: &str,
) -> StoreResult<Option<PackageManifest>> {
    read_lock_record(connection, lock_id)?;
    let rows = exact_rows(
        connection,
        "SELECT package_id, version, content_sha256 FROM campaign_lock_memberships          WHERE lock_id = ? AND role = 'primary' ORDER BY closure_order",
        &[&lock_id],
    )?;
    let members: i64 = connection.query_row(
        "SELECT COUNT(*) FROM campaign_lock_memberships WHERE lock_id = ?",
        [lock_id],
        |row| row.get(0),
    )?;
    if rows.is_empty() && members == 0 {
        return Ok(None);
    }
    if rows.len() != 1 {
        return Err(rejected(
            "Campaign package lock does not have one exact primary package",
            "package_lock_invalid",
        ));
    }
    let (package_id, version, sha256) = (py_str(&rows[0][0]), py_str(&rows[0][1]), py_str(&rows[0][2]));
    let manifest = manifest_from_identity(connection, (&package_id, &version, &sha256))?;
    if manifest.role != sylvia_domain::authority::PackageRole::Primary {
        return Err(rejected(
            "Campaign package lock primary role conflicts with its manifest",
            "package_lock_invalid",
        ));
    }
    Ok(Some(manifest))
}

/// `lock_is_ancestor`: follow the current lock's authenticated predecessor chain.
pub fn lock_is_ancestor(connection: &Connection, campaign_id: &str, lock_id: &str) -> StoreResult<bool> {
    let mut current = crate::campaigns::current_lock_record(connection, campaign_id)?;
    let mut seen: HashSet<String> = HashSet::new();
    while seen.insert(current.lock_id.clone()) {
        if current.campaign_id != campaign_id {
            return Ok(false);
        }
        if current.lock_id == lock_id {
            return Ok(true);
        }
        let Some(predecessor) = current.predecessor_lock_id.clone() else { return Ok(false) };
        let previous = read_lock_record(connection, &predecessor)?;
        if previous.created_revision >= current.created_revision {
            return Ok(false);
        }
        current = previous;
    }
    Ok(false)
}

/// `COMPATIBLE_UPDATE_AUTHORITY`.
pub const COMPATIBLE_UPDATE_AUTHORITY: &str = "campaign-compatible-update.v1";

/// `_compatible_migration_identity`.
pub(crate) fn compatible_migration_identity(migration_id: &str) -> bool {
    match migration_id.strip_prefix("compatible-update-") {
        Some(token) => token.chars().count() == 64 && token.chars().all(|c| "0123456789abcdef".contains(c)),
        None => false,
    }
}

/// `_activation_result_from_row`.
pub(crate) fn activation_result_from_row(row: &Named<'_>) -> StoreResult<ActivationResult> {
    let campaign_id = row.text("campaign_id")?;
    let activation_id = row.text("activation_id")?;
    let status = crate::campaigns::parse(&row.text("status")?, ActivationStatus::parse, "ActivationStatus")?;
    let old_lock_id = row.text("old_lock_id")?;
    let new_lock_id = row.text("new_lock_id")?;
    let resulting_revision = row.int("resulting_revision")?;
    let payload_sha256 = row.text("payload_sha256")?;
    let mut expected = JsonMap::new();
    expected.insert("campaign_id".into(), campaign_id.clone().into());
    expected.insert("activation_id".into(), activation_id.clone().into());
    expected.insert("status".into(), status.as_str().into());
    expected.insert("old_lock_id".into(), old_lock_id.clone().into());
    expected.insert("new_lock_id".into(), new_lock_id.clone().into());
    expected.insert("resulting_revision".into(), resulting_revision.into());
    expected.insert("payload_sha256".into(), payload_sha256.clone().into());
    let report = parse_canonical(&row.text("result_json")?)?;
    if let Json::Object(map) = &report {
        if let Some(authority) = map.get("authority") {
            if !py_eq(authority, &Json::from(COMPATIBLE_UPDATE_AUTHORITY))
                || !compatible_migration_identity(&row.text("migration_id")?)
            {
                return Err(rejected("Stored activation authority is invalid", "activation_result_corrupt"));
            }
            expected.insert("authority".into(), COMPATIBLE_UPDATE_AUTHORITY.into());
        }
    }
    if !py_eq(&report, &Json::Object(expected)) {
        return Err(rejected("Stored activation result is inconsistent", "activation_result_corrupt"));
    }
    Ok(ActivationResult {
        campaign_id,
        activation_id,
        status,
        old_lock_id,
        new_lock_id,
        resulting_revision,
        payload_sha256,
        replayed: true,
        resolution: "durable_replay".into(),
    })
}

/// `sqlite_packages.reclaim`: delete every package blob no root holds. Roots are the campaign
/// lock memberships, validated or activated candidates, read leases and recovery roots; the
/// counts of each root kind are returned in lane 1's `ReclamationResult`.
pub(crate) fn reclaim(
    connection: &Connection,
) -> StoreResult<sylvia_domain::package_library::ReclamationResult> {
    let count = |query: &str| -> StoreResult<i64> {
        let value: Value = connection.query_row(query, [], |row| row.get(0))?;
        py_int(&value).ok_or_else(|| crate::error::value_error("invalid literal for int() with base 10"))
    };
    let campaign_lock_roots = count("SELECT COUNT(*) FROM campaign_locks")?;
    let candidate_roots =
        count("SELECT COUNT(*) FROM package_candidates WHERE status IN ('validated','activated')")?;
    let activation_roots = count("SELECT COUNT(*) FROM package_activations")?;
    let staging_roots = count("SELECT COUNT(*) FROM package_staging WHERE status = 'staging'")?;
    let read_roots = count("SELECT COUNT(*) FROM package_read_leases")?;
    let recovery_roots = count("SELECT COUNT(*) FROM package_recovery_roots")?;
    let text_rows = |query: &str, values: &[&dyn rusqlite::ToSql]| -> StoreResult<Vec<Vec<String>>> {
        Ok(exact_rows(connection, query, values)?
            .iter()
            .map(|row| row.iter().map(py_str).collect())
            .collect())
    };
    let mut rooted_packages = BTreeSet::new();
    for query in [
        "SELECT package_id, version, content_sha256 FROM campaign_lock_memberships",
        "SELECT package_id, version, content_sha256 FROM package_candidate_memberships          WHERE candidate_id IN (SELECT candidate_id FROM package_candidates          WHERE status IN ('validated','activated'))",
    ] {
        rooted_packages.extend(text_rows(query, &[])?);
    }
    let mut rooted_blobs = HashSet::new();
    for identity in &rooted_packages {
        for row in text_rows(
            "SELECT blob_sha256 FROM package_manifest_files WHERE package_id = ? AND version = ?              AND content_sha256 = ?",
            &[&identity[0], &identity[1], &identity[2]],
        )? {
            rooted_blobs.insert(row[0].clone());
        }
    }
    for query in
        ["SELECT blob_sha256 FROM package_read_leases", "SELECT blob_sha256 FROM package_recovery_roots"]
    {
        rooted_blobs.extend(text_rows(query, &[])?.into_iter().map(|row| row[0].clone()));
    }
    let deletable: BTreeSet<String> = text_rows("SELECT blob_sha256 FROM package_blobs", &[])?
        .into_iter()
        .map(|row| row[0].clone())
        .filter(|blob| !rooted_blobs.contains(blob))
        .collect();
    for blob in &deletable {
        connection.execute("DELETE FROM package_blobs WHERE blob_sha256 = ?", [blob])?;
    }
    let deleted =
        i64::try_from(deletable.len()).map_err(|_| StoreError::Other("blob count overflow".into()))?;
    Ok(sylvia_domain::package_library::ReclamationResult {
        deleted_blobs: deleted,
        campaign_lock_roots,
        candidate_roots,
        activation_roots,
        staging_roots,
        read_lease_roots: read_roots,
        recovery_roots,
    })
}

/// `sqlite_draft_history.DRAFT_LIFECYCLE_KINDS`.
pub(crate) const DRAFT_LIFECYCLE_KINDS: [&str; 4] =
    ["rename_campaign", "archive_campaign", "restore_campaign", "trash_campaign"];

/// `sqlite_draft_history.has_only_draft_lifecycle_history`: every pre-binding revision has its
/// own lifecycle receipt and no game basis.
pub(crate) fn has_only_draft_lifecycle_history(
    connection: &Connection,
    campaign_id: &str,
    revision: i64,
) -> StoreResult<bool> {
    if revision == 0 {
        return Ok(true);
    }
    let rows = exact_rows(
        connection,
        "SELECT revision.revision, operation.operation_kind, operation.basis_revision, \
         operation.resulting_revision, operation.basis_turn_id, operation.basis_lock_id \
         FROM campaign_revisions AS revision LEFT JOIN campaign_operations AS operation \
         ON operation.operation_id = revision.lifecycle_operation_id \
         AND operation.campaign_id = revision.campaign_id \
         WHERE revision.campaign_id = ? AND revision.revision BETWEEN 1 AND ? \
         ORDER BY revision.revision",
        &[&campaign_id, &revision],
    )?;
    let integer =
        |value: &Value, expected: i64| matches!(value, Value::Integer(number) if *number == expected);
    if rows.len() as i64 != revision
        || rows.iter().enumerate().any(|(index, row)| {
            let number = index as i64 + 1;
            !integer(&row[0], number)
                || !matches!(&row[1], Value::Text(kind) if DRAFT_LIFECYCLE_KINDS.contains(&kind.as_str()))
                || !integer(&row[2], number - 1)
                || !integer(&row[3], number)
                || row[4] != Value::Null
                || row[5] != Value::Null
        })
    {
        return Ok(false);
    }
    for query in [
        "SELECT 1 FROM campaign_locks WHERE campaign_id = ? AND created_revision < ? LIMIT 1",
        "SELECT 1 FROM turns WHERE campaign_id = ? AND created_revision < ? LIMIT 1",
    ] {
        if connection.query_row(query, params![campaign_id, revision], |_| Ok(())).optional()?.is_some() {
            return Ok(false);
        }
    }
    Ok(true)
}

const PACKAGE_RECORDS_INVALID: &str = "Saved package records failed an integrity check";

/// How one step of `verify_package_authority` failed: an exception it catches (`CommandRejected`,
/// `KeyError`, `TypeError`, `ValueError` and subclasses, every `sqlite3.Error`), which becomes its
/// `SchemaIdentityError`, or any other exception, which escapes as it is.
enum Check {
    Invalid,
    Escape(StoreError),
}

type Checked<T> = Result<T, Check>;

impl From<StoreError> for Check {
    fn from(error: StoreError) -> Self {
        match &error {
            StoreError::Sqlite(_) | StoreError::Other(_) => Check::Invalid,
            StoreError::Coded(coded) => match coded.error_type {
                Some("CommandRejected" | "ValueError" | "TypeError") | None => Check::Invalid,
                _ => Check::Escape(error),
            },
            StoreError::Backup(_) | StoreError::NotPorted(_) => Check::Escape(error),
        }
    }
}

impl From<DomainError> for Check {
    fn from(error: DomainError) -> Self {
        match &error {
            DomainError::Uncaught { error_type, message }
                if !matches!(*error_type, "KeyError" | "TypeError" | "ValueError" | "UnicodeDecodeError") =>
            {
                Check::Escape(StoreError::Other(format!("{error_type}: {message}")))
            }
            // Other `AuthorityError` classes are not `CommandRejected`.
            DomainError::Rejected { error_type, .. } if *error_type != "CommandRejected" => {
                Check::Escape(StoreError::from(error))
            }
            _ => Check::Invalid,
        }
    }
}

impl From<rusqlite::Error> for Check {
    fn from(_: rusqlite::Error) -> Self {
        Check::Invalid
    }
}

/// `verify_package_authority`: reconcile every immutable byte, closure, lineage and safe record
/// invariant of the package library.
pub fn verify_package_authority(connection: &Connection) -> StoreResult<()> {
    verify_packages(connection).map_err(|check| match check {
        Check::Invalid => schema_identity(PACKAGE_RECORDS_INVALID),
        Check::Escape(error) => error,
    })
}

fn int_of(value: &Value) -> Checked<i64> {
    py_int(value).ok_or(Check::Invalid)
}

fn invalid<T>() -> Checked<T> {
    Err(Check::Invalid)
}

/// One staged file of the manifest set: `(identity, path, byte count, SHA-256)`.
type LedgerEntry = ((String, String, String), String, i64, String);

fn verify_packages(connection: &Connection) -> Checked<()> {
    let (staging_columns, staging_rows) = named_rows(connection, "SELECT * FROM package_staging", &[])?;
    for values in staging_rows {
        let row = Named { columns: &staging_columns, values };
        let parsed = parse_canonical(&row.text("manifest_json")?)?;
        let Json::Object(parsed) = parsed else { return invalid() };
        if parsed.len() != 2 || !parsed.contains_key("format") || !parsed.contains_key("packages") {
            return invalid();
        }
        let format_version = match &parsed["format"] {
            Json::Number(number) => number.as_i64().filter(|value| (1..=4).contains(value)),
            _ => None,
        };
        let (Some(format_version), Json::Array(packages)) = (format_version, &parsed["packages"]) else {
            return invalid();
        };
        let manifests = packages
            .iter()
            .map(|item| manifest_from_record(item, Some(format_version)))
            .collect::<Result<Vec<_>, _>>()?;
        let ledger: Vec<LedgerEntry> = manifests
            .iter()
            .flat_map(|manifest| {
                let (package_id, version, sha256) = manifest.identity();
                manifest.files.iter().map(move |file| {
                    (
                        (package_id.to_owned(), version.to_owned(), sha256.to_owned()),
                        file.path().to_owned(),
                        file.byte_count(),
                        file.sha256().to_owned(),
                    )
                })
            })
            .collect();
        let expected_chunked: BTreeSet<usize> =
            ledger.iter().enumerate().filter(|(_, entry)| entry.2 > 0).map(|(order, _)| order).collect();
        let report = parse_canonical(&row.text("report_json")?)?;
        let Json::Object(report) = report else { return invalid() };
        if report.len() != 2 || !report.contains_key("code") || !report.contains_key("label") {
            return invalid();
        }
        let mut observed: BTreeSet<usize> = BTreeSet::new();
        let mut next_chunk: BTreeMap<usize, i64> = BTreeMap::new();
        let mut staged_bytes: BTreeMap<usize, i64> = BTreeMap::new();
        let mut staged_data: BTreeMap<usize, Vec<u8>> = BTreeMap::new();
        let install_id = row.get("install_id")?.clone();
        let (chunk_columns, chunk_rows) = named_rows(
            connection,
            "SELECT file_order, chunk_order, package_id, version, content_sha256, \
             portable_path, chunk_sha256, data FROM package_staging_chunks \
             WHERE install_id = ? ORDER BY file_order, chunk_order",
            &[&install_id],
        )?;
        for values in chunk_rows {
            let chunk = Named { columns: &chunk_columns, values };
            let file_order = int_of(chunk.get("file_order")?)?;
            let Some(index) = usize::try_from(file_order).ok().filter(|index| *index < ledger.len()) else {
                return invalid();
            };
            let (identity, path, byte_count, _) = &ledger[index];
            if *byte_count == 0
                || (chunk.text("package_id")?, chunk.text("version")?, chunk.text("content_sha256")?)
                    != *identity
                || chunk.text("portable_path")? != *path
                || int_of(chunk.get("chunk_order")?)? != *next_chunk.get(&index).unwrap_or(&0)
            {
                return invalid();
            }
            let data = blob_bytes(chunk.get("data")?)?;
            if data.is_empty() || sha(&data) != chunk.text("chunk_sha256")? {
                return invalid();
            }
            staged_data.entry(index).or_default().extend_from_slice(&data);
            observed.insert(index);
            *next_chunk.entry(index).or_insert(0) += 1;
            *staged_bytes.entry(index).or_insert(0) += data.len() as i64;
        }
        let unfinished = row.text("status")? == "staging";
        if unfinished {
            // Staging commits each verified chunk separately, so an interrupted import owns
            // only a prefix and must stay reopenable for retry.
            let prefix: BTreeSet<usize> = expected_chunked.iter().copied().take(observed.len()).collect();
            if observed != prefix || !py_eq(&report["code"], &Json::from("staging")) {
                return invalid();
            }
        } else if observed != expected_chunked {
            return invalid();
        }
        let last = observed.iter().next_back().copied();
        for index in &observed {
            let (_, _, byte_count, file_sha256) = &ledger[*index];
            if unfinished && Some(*index) == last && staged_bytes[index] < *byte_count {
                continue;
            }
            if staged_bytes[index] != *byte_count || sha(&staged_data[index]) != *file_sha256 {
                return invalid();
            }
        }
    }
    for row in exact_rows(connection, "SELECT blob_sha256 FROM package_blobs", &[])? {
        verify_blob(connection, &py_str(&row[0]))?;
    }
    let (manifest_columns, manifest_rows) =
        named_rows(connection, "SELECT * FROM package_manifests WHERE status = 'published'", &[])?;
    for values in manifest_rows {
        let row = Named { columns: &manifest_columns, values };
        let manifest = manifest_from_record(&parse_canonical(&row.text("canonical_json")?)?, None)?;
        let (package_id, version, sha256) = manifest.identity();
        if (package_id, version, sha256)
            != (
                row.text("package_id")?.as_str(),
                row.text("version")?.as_str(),
                row.text("content_sha256")?.as_str(),
            )
        {
            return invalid();
        }
        for (column, expected) in [
            ("metadata_json", &manifest.metadata),
            ("provenance_json", &manifest.provenance),
            ("compatibility_json", &manifest.compatibility),
        ] {
            if !py_eq(&parse_canonical(&row.text(column)?)?, &Json::Object(expected.clone())) {
                return invalid();
            }
        }
        let (file_columns, file_rows) = named_rows(
            connection,
            "SELECT * FROM package_manifest_files WHERE package_id = ? AND version = ? \
             AND content_sha256 = ? ORDER BY file_order",
            &[&package_id, &version, &sha256],
        )?;
        let files: Vec<Named<'_>> =
            file_rows.into_iter().map(|values| Named { columns: &file_columns, values }).collect();
        for (order, file) in files.iter().enumerate() {
            if int_of(file.get("file_order")?)? != order as i64 {
                return invalid();
            }
        }
        let stored = files
            .iter()
            .map(|file| {
                Ok((file.text("portable_path")?, int_of(file.get("byte_count")?)?, file.text("blob_sha256")?))
            })
            .collect::<Checked<Vec<_>>>()?;
        let declared: Vec<(String, i64, String)> = manifest
            .files
            .iter()
            .map(|file| (file.path().to_owned(), file.byte_count(), file.sha256().to_owned()))
            .collect();
        if stored != declared {
            return invalid();
        }
        for file in &manifest.files {
            let chunks = verify_blob(connection, file.sha256())?;
            if manifest.content_declarations_path.as_deref() == Some(file.path()) {
                let declarations = parse_content_declarations_bytes(&chunks.concat())?;
                validate_content_declaration_links(&manifest, &declarations)?;
            }
            if file.path() == CAMPAIGN_SETTING_KNOWLEDGE_PATH {
                let knowledge = parse_campaign_setting_knowledge_bytes(&chunks.concat())?;
                validate_campaign_setting_knowledge_links(&manifest, &knowledge)?;
            }
        }
    }
    for row in exact_rows(connection, "SELECT candidate_id FROM package_candidates", &[])? {
        let candidate = read_candidate(connection, &py_str(&row[0]))?;
        if closure_sha256(candidate.package_lock()) != candidate.closure_sha256() {
            return invalid();
        }
        if matches!(candidate.status(), CandidateStatus::Validated | CandidateStatus::Activated) {
            verify_published_lock(connection, candidate.package_lock())?;
        }
    }
    for row in exact_rows(connection, "SELECT lock_id FROM campaign_locks", &[])? {
        let lock = read_lock_record(connection, &py_str(&row[0]))?;
        if closure_sha256(&lock.package_lock) != lock.closure_sha256 {
            return invalid();
        }
        if lock.availability == PackageLockAvailability::Available {
            verify_published_lock(connection, &lock.package_lock)?;
        }
        if lock.predecessor_lock_id.as_deref() == Some(lock.lock_id.as_str()) {
            return invalid();
        }
    }
    let (campaign_columns, campaign_rows) = named_rows(
        connection,
        "SELECT campaign_id, current_lock_id, revision, lifecycle, writer, lock_hash FROM campaigns",
        &[],
    )?;
    for values in campaign_rows {
        let campaign = Named { columns: &campaign_columns, values };
        let campaign_id = campaign.text("campaign_id")?;
        if campaign.get("current_lock_id")? == &Value::Null {
            let lifecycle = (campaign.text("lifecycle")?, campaign.text("writer")?);
            let draft = has_only_draft_lifecycle_history(
                connection,
                &campaign_id,
                int_of(campaign.get("revision")?)?,
            )?;
            let has_lock = connection
                .query_row(
                    "SELECT 1 FROM campaign_locks WHERE campaign_id = ? LIMIT 1",
                    [&campaign_id],
                    |_| Ok(()),
                )
                .optional()?
                .is_some();
            let has_turn = connection
                .query_row("SELECT 1 FROM turns WHERE campaign_id = ? LIMIT 1", [&campaign_id], |_| Ok(()))
                .optional()?
                .is_some();
            if !draft
                || !matches!(
                    (lifecycle.0.as_str(), lifecycle.1.as_str()),
                    ("active", "builder") | ("archived", "none") | ("tombstoned", "none")
                )
                || campaign.text("lock_hash")? != "unbound"
                || has_lock
                || has_turn
            {
                return invalid();
            }
            continue;
        }
        let current = read_lock_record(connection, &campaign.text("current_lock_id")?)?;
        if current.campaign_id != campaign_id {
            return invalid();
        }
    }
    let (activation_columns, activation_rows) =
        named_rows(connection, "SELECT * FROM package_activations", &[])?;
    for values in activation_rows {
        let activation = Named { columns: &activation_columns, values };
        let result = activation_result_from_row(&activation)?;
        let old = read_lock_record(connection, &result.old_lock_id)?;
        let new = read_lock_record(connection, &result.new_lock_id)?;
        let candidate = read_candidate(connection, &activation.text("candidate_id")?)?;
        if result.resulting_revision < 1 {
            return invalid();
        }
        let request = ActivationCommand::new(
            result.campaign_id.clone(),
            activation.text("candidate_id")?,
            result.activation_id.clone(),
            result.resulting_revision - 1,
            result.old_lock_id.clone(),
            activation.text("migration_id")?,
        )?;
        let revision: Option<(Value, Value)> = connection
            .query_row(
                "SELECT action_id, activation_id FROM campaign_revisions WHERE campaign_id = ? AND revision = ?",
                params![result.campaign_id, result.resulting_revision],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .optional()
            ?;
        let campaign_revision: Option<Value> = connection
            .query_row("SELECT revision FROM campaigns WHERE campaign_id = ?", [&result.campaign_id], |row| {
                row.get(0)
            })
            .optional()?;
        let revision_ok = matches!(&revision, Some((action, activation_id))
            if *action == Value::Null && py_str(activation_id) == result.activation_id);
        let campaign_ok = match &campaign_revision {
            Some(value) => int_of(value)? >= result.resulting_revision,
            None => false,
        };
        if result.status != ActivationStatus::Committed
            || activation_payload_sha256(&request) != result.payload_sha256
            || new.predecessor_lock_id.as_deref() != Some(old.lock_id.as_str())
            || old.campaign_id != result.campaign_id
            || new.campaign_id != result.campaign_id
            || new.created_revision != result.resulting_revision
            || candidate.package_lock() != &new.package_lock
            || candidate.closure_sha256() != new.closure_sha256
            || !revision_ok
            || !campaign_ok
        {
            return invalid();
        }
    }
    let violation = connection.query_row("PRAGMA foreign_key_check", [], |_| Ok(())).optional()?;
    if violation.is_some() {
        return invalid();
    }
    Ok(())
}

impl AuthorityStore {
    /// `stage_package_archive`: the manifest set, then each file's chunks, each in its own
    /// committed transaction, so an interrupted import keeps a retryable prefix.
    pub fn stage_package_archive(
        &self,
        admitted: &AdmittedPackageArchive,
        install_id: &str,
    ) -> StoreResult<()> {
        validate_authority_identity(install_id, "Package install identity", false)?;
        let packages = admitted
            .manifests
            .iter()
            .map(|manifest| crate::sequel::json_loads(&manifest.canonical_json))
            .collect::<StoreResult<Vec<_>>>()?;
        let mut envelope = JsonMap::new();
        envelope.insert("format".into(), admitted.format_version.into());
        envelope.insert("packages".into(), Json::Array(packages));
        let manifest_json = dump(&Json::Object(envelope));
        let connection = self.connect()?;
        let result = (|| -> StoreResult<()> {
            connection.execute_batch("BEGIN IMMEDIATE")?;
            begin_staging(&connection, install_id, &manifest_json)?;
            connection.execute_batch("COMMIT")?;
            for file_order in 0..admitted.files.len() {
                let archive_file = &admitted.files[file_order];
                let manifest = &admitted.manifests[archive_file.manifest];
                for (chunk_order, chunk) in file_chunks(admitted, file_order)?.iter().enumerate() {
                    connection.execute_batch("BEGIN IMMEDIATE")?;
                    connection.execute(
                        "INSERT INTO package_staging_chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        params![
                            install_id,
                            file_order as i64,
                            chunk_order as i64,
                            manifest.package_id,
                            manifest.version,
                            manifest.content_sha256,
                            archive_file.file.path(),
                            sha(chunk),
                            chunk
                        ],
                    )?;
                    connection.execute_batch("COMMIT")?;
                }
            }
            Ok(())
        })();
        if result.is_err() && !connection.is_autocommit() {
            let _ = connection.execute_batch("ROLLBACK");
        }
        result
    }

    /// `publish_package_archive`: the staged bytes published as one candidate, or nothing.
    pub fn publish_package_archive(
        &self,
        admitted: &AdmittedPackageArchive,
        install_id: &str,
    ) -> StoreResult<PackageCandidate> {
        let connection = self.connect()?;
        let result = (|| {
            connection.execute_batch("BEGIN IMMEDIATE")?;
            if let Some(fault) = &self.faults.before_package_publication {
                fault()?;
            }
            let candidate = publish_archive(&connection, admitted, install_id)?;
            connection.execute_batch("COMMIT")?;
            Ok(candidate)
        })();
        if !connection.is_autocommit() {
            let _ = connection.execute_batch("ROLLBACK");
        }
        result
    }

    /// `package_candidate`.
    pub fn package_candidate(&self, candidate_id: &str) -> StoreResult<PackageCandidate> {
        read_candidate(&self.connect()?, candidate_id)
    }

    /// `package_candidates`: newest first.
    pub fn package_candidates(&self) -> StoreResult<Vec<PackageCandidate>> {
        list_candidates(&self.connect()?)
    }

    /// `package_staging`.
    pub fn package_staging(&self, install_id: &str) -> StoreResult<PackageStagingRecord> {
        read_staging(&self.connect()?, install_id)
    }

    /// `exact_package_manifest`: one exact installed manifest.
    pub fn exact_package_manifest(
        &self,
        package_id: &str,
        version: &str,
        content_sha256: &str,
    ) -> StoreResult<PackageManifest> {
        exact_manifest(&self.connect()?, (package_id, version, content_sha256))
    }

    /// `package_participant_declarations`.
    pub fn package_participant_declarations(
        &self,
        candidate_id: &str,
    ) -> StoreResult<Vec<ParticipantArtifactDeclaration>> {
        candidate_participant_declarations(&self.connect()?, candidate_id)
    }

    /// `exact_participant_declarations`.
    pub fn exact_participant_declarations(
        &self,
        package_id: &str,
        version: &str,
        content_sha256: &str,
    ) -> StoreResult<Vec<ParticipantArtifactDeclaration>> {
        exact_participant_declarations(&self.connect()?, (package_id, version, content_sha256))
    }
}
