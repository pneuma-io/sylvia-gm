//! Validating one portable campaign archive, from `campaign_archive.py`:
//! `validate_campaign_archive` with the ZIP envelope checks (`_safe_member_name`,
//! `_validate_zip_metadata`, `_read_member`, `_stream_member_digest`), the manifest
//! (`_decode_json`, `_validate_manifest_shape_and_bytes`), the streamed authority member
//! (`_scan_authority_member`, `_record_authority_summary`, `_record_manifest_summary`,
//! `_iter_manifest_relation_rows`), its closure (`_validate_streamed_authority`,
//! `_validate_owned_memberships`, `_validate_handoff_package_basis`, `_streamed_closure_index`)
//! and the private provider member (`private_records::decode`).
//!
//! The archive is read by position from its file (`ZipArchive::open_file`), member by member in
//! 64 KiB chunks as Python's `iter_bounded_chunks` reads, so only the manifest, the private member
//! and one authority line are ever held whole, as in Python.
//!
//! Python turns every failure that is not a `CommandRejected` into
//! `portable_export_invalid_archive` (`portable_export_invalid_zip` when the archive does not
//! open). Python's values keep its semantics here: set members compare as Python's do
//! (`1 == 1.0 == True`), a list or object in a set is its `TypeError`, and key tuples order as
//! Python's tuples (mixed types are its `TypeError`).
//!
//! Declared difference (`docs/rust-port/differences.md`): a manifest or authority line holding
//! an integer past 64 bits, which Python reads, is refused here with the refusal of its framing.

use crate::campaign_archive::{
    self, authority_columns, canonical_authority_record, checked_archive_ledger, failure, limit, FORMAT_ID,
    FORMAT_VERSION, MANIFEST_FIELDS, PRIVATE_FORMAT_VERSION, PRIVATE_MEMBER_NAME, PRODUCT_GENERATION,
    PRODUCT_ID, SOURCE_AUTHORITY_SCHEMA_VERSIONS, TABLES,
};
use crate::error::StoreError;
use std::collections::{BTreeSet, HashMap, HashSet};
use std::path::Path;
use sylvia_base::hash::sha256_hex;
use sylvia_base::pyjson::{self, HookPolicy};
use sylvia_base::{Json, JsonMap};
use sylvia_domain::package_archive::zipfile::{ReadError, ZipArchive, ZipInfo, ZIP_STORED};
use sylvia_domain::package_library::{
    canonical_json_bytes, manifest_from_record, resolve_package_graph, PackageGraphNode, PackageManifest,
};
use sylvia_domain::pyops::{py_eq, py_number_cmp, py_str};
use sylvia_domain::state_ownership::owner_matches_package_lock;
use unicode_normalization::UnicodeNormalization;

/// `hashlib.sha256()`: `update`, then `hexdigest`.
#[derive(Clone, Default)]
struct Hasher(sha2::Sha256);

impl Hasher {
    fn new() -> Self {
        Self::default()
    }
    fn update(&mut self, data: &[u8]) {
        sha2::Digest::update(&mut self.0, data);
    }
    fn hex(self) -> String {
        hex::encode(sha2::Digest::finalize(self.0))
    }
}

/// `STREAM_CHUNK_BYTES`.
const STREAM_CHUNK_BYTES: u64 = 65_536;

/// `CLOSURE_INDEX_FIELDS`.
const CLOSURE_INDEX_FIELDS: [&str; 8] = [
    "lock_ids",
    "action_ids",
    "candidate_ids",
    "package_identities",
    "blob_sha256s",
    "plan_ids",
    "handoff_ids",
    "session_ids",
];
/// `PACKAGE_IDENTITY_FIELDS`.
const PACKAGE_IDENTITY_FIELDS: [&str; 3] = ["package_id", "version", "content_sha256"];
/// `NON_MANIFEST_MEMBER_FIELDS`.
const NON_MANIFEST_MEMBER_FIELDS: [&str; 3] = ["name", "byte_count", "sha256"];
/// `_PACKAGE_RELATION_TABLES`.
const PACKAGE_RELATION_TABLES: [&str; 10] = [
    "packages",
    "package_dependencies",
    "package_manifests",
    "package_manifest_files",
    "package_exact_dependencies",
    "package_capabilities",
    "package_tools",
    "package_prompt_fragments",
    "package_ui_surfaces",
    "package_participant_artifacts",
];
/// `_new_parent_summary()`'s parents.
const PARENTS: [&str; 13] = [
    "operations",
    "turns",
    "locks",
    "actions",
    "activations",
    "plans",
    "handoffs",
    "sessions",
    "participants",
    "candidates",
    "installs",
    "event_sequences",
    "attempts",
];

/// `_REFERENCE_SUMMARIES`: each table's referring columns and the parent each names.
fn reference_summaries(table: &str) -> &'static [(&'static str, &'static str)] {
    match table {
        "campaigns" => &[("current_lock_id", "locks")],
        "campaign_lifecycle_state" => {
            &[("designated_session_id", "sessions"), ("tombstone_prior_session_id", "sessions")]
        }
        "campaign_lifecycle_transitions" => {
            &[("operation_id", "operations"), ("designated_session_id", "sessions")]
        }
        "campaign_operations" => &[("basis_turn_id", "turns"), ("basis_lock_id", "locks")],
        "campaign_constructions" => &[
            ("confirmation_operation_id", "operations"),
            ("begin_operation_id", "operations"),
            ("completion_operation_id", "operations"),
            ("authority_turn_id", "turns"),
            ("authority_lock_id", "locks"),
        ],
        "campaign_continuity_records" => &[("operation_id", "operations")],
        "campaign_revisions" => &[
            ("action_id", "actions"),
            ("activation_id", "activations"),
            ("plan_id", "plans"),
            ("handoff_id", "handoffs"),
            ("construction_operation_id", "operations"),
            ("lifecycle_operation_id", "operations"),
            ("continuity_operation_id", "operations"),
        ],
        "participant_quarantines" => &[("participant_id", "participants")],
        "actions" => &[("turn_id", "turns"), ("lock_id", "locks")],
        "idempotency_records" => &[("action_id", "actions")],
        "canonical_events" => &[("turn_id", "turns"), ("action_id", "actions")],
        "narration_interruptions" => &[
            ("turn_id", "turns"),
            ("last_action_id", "actions"),
            ("resolving_retry_id", "attempts"),
            ("resolved_sequence", "event_sequences"),
        ],
        "mutations" | "consequences" => &[("action_id", "actions")],
        "campaign_locks" => &[("predecessor_lock_id", "locks")],
        "campaign_lock_memberships" => &[("lock_id", "locks")],
        "package_activations" => {
            &[("candidate_id", "candidates"), ("old_lock_id", "locks"), ("new_lock_id", "locks")]
        }
        "campaign_plans" => &[("package_lock_id", "locks"), ("source_session_id", "sessions")],
        "builder_dm_handoffs" => &[
            ("builder_session_id", "sessions"),
            ("dm_session_id", "sessions"),
            ("package_lock_id", "locks"),
            ("package_candidate_id", "candidates"),
            ("plan_id", "plans"),
            ("completed_turn_id", "turns"),
        ],
        "agent_sessions" => &[("parent_session_id", "sessions")],
        "agent_session_messages" => &[("session_id", "sessions"), ("canonical_sequence", "event_sequences")],
        "package_candidates" => &[("install_id", "installs")],
        "package_candidate_memberships" => &[("candidate_id", "candidates")],
        _ => &[],
    }
}

/// A failure inside validation: a `CommandRejected`, which passes through, or anything else,
/// which Python reports as `portable_export_invalid_archive`.
enum Fail {
    Rejected(StoreError),
    Fault,
}

type Checked<T> = Result<T, Fail>;

fn fail(code: &str) -> Fail {
    Fail::Rejected(failure(code))
}

impl From<StoreError> for Fail {
    fn from(error: StoreError) -> Self {
        match error.rejection() {
            Some(coded) if matches!(coded.error_type, None | Some("CommandRejected")) => {
                Fail::Rejected(error)
            }
            _ => Fail::Fault,
        }
    }
}

/// A value as a member of a Python set: numbers compare across `bool`, `int` and `float`; a
/// list or object is unhashable.
#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord)]
enum Key {
    None,
    Int(i128),
    Float(u64),
    Str(String),
}

fn key(value: &Json) -> Checked<Key> {
    Ok(match value {
        Json::Null => Key::None,
        Json::Bool(flag) => Key::Int(i128::from(*flag)),
        Json::Number(number) => match (number.as_i64(), number.as_u64(), number.as_f64()) {
            (Some(integer), _, _) => Key::Int(i128::from(integer)),
            (None, Some(integer), _) => Key::Int(i128::from(integer)),
            (None, None, Some(float)) if float.fract() == 0.0 && float.abs() < 1e30 => {
                Key::Int(float as i128)
            }
            (None, None, Some(float)) => Key::Float(float.to_bits()),
            _ => return Err(Fail::Fault),
        },
        Json::String(text) => Key::Str(text.clone()),
        Json::Array(_) | Json::Object(_) => return Err(Fail::Fault),
    })
}

/// `a < b` for two unequal values, as Python orders them; anything it cannot order is its
/// `TypeError`.
fn less(a: &Json, b: &Json) -> Checked<bool> {
    match (a, b) {
        (Json::String(left), Json::String(right)) => Ok(left < right),
        (Json::Array(left), Json::Array(right)) => tuple_less_equal(left, right).map(|le| le && !py_eq(a, b)),
        _ => match py_number_cmp(a, b) {
            Some(order) => Ok(order == std::cmp::Ordering::Less),
            None => Err(Fail::Fault),
        },
    }
}

/// `tuple(a) <= tuple(b)`: the first unequal items decide; with none, the shorter is smaller.
fn tuple_less_equal(a: &[Json], b: &[Json]) -> Checked<bool> {
    for (left, right) in a.iter().zip(b) {
        if !py_eq(left, right) {
            return less(left, right);
        }
    }
    Ok(a.len() <= b.len())
}

/// `int(value)` of a parsed JSON value.
fn int_of(value: &Json) -> Checked<i64> {
    match value {
        Json::Bool(flag) => Ok(i64::from(*flag)),
        Json::Number(number) => number
            .as_i64()
            .or_else(|| {
                number
                    .as_f64()
                    .filter(|float| float.is_finite() && float.abs() < 9.2e18)
                    .map(|float| float.trunc() as i64)
            })
            .ok_or(Fail::Fault),
        Json::String(text) => {
            crate::pytext::py_int(&rusqlite::types::Value::Text(text.clone())).ok_or(Fail::Fault)
        }
        _ => Err(Fail::Fault),
    }
}

/// `_decode_json(data, code)`: strict UTF-8 without a byte order mark, no duplicate members, no
/// `NaN`/`Infinity` and no number beyond the float range.
fn decode_json(data: &[u8], code: &str) -> Checked<Json> {
    if data.starts_with(b"\xef\xbb\xbf") {
        return Err(fail(code));
    }
    let Ok(text) = std::str::from_utf8(data) else { return Err(fail(code)) };
    match pyjson::loads_hooked(text, HookPolicy { refuse_duplicates: true, refuse_constants: true }) {
        Ok(read) if !read.non_finite => Ok(read.value),
        _ => Err(fail(code)),
    }
}

/// `_safe_member_name(name)`.
fn safe_member_name(name: &str) -> bool {
    if name.is_empty() || name.contains('\\') || name.contains('\0') || name.starts_with('/') {
        return false;
    }
    // `PurePosixPath(name).parts` drops empty and `.` components; `..` stays.
    if name.split('/').any(|part| part == "..") {
        return false;
    }
    let bytes = name.as_bytes();
    !(bytes.len() >= 2 && bytes[0].is_ascii_alphabetic() && bytes[1] == b':')
}

/// `re.fullmatch(r"blobs/[0-9a-f]{64}", name)`.
fn blob_member_name(name: &str) -> bool {
    name.strip_prefix("blobs/").is_some_and(|digest| {
        digest.len() == 64
            && digest.bytes().all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    })
}

/// `_validate_zip_metadata(info)`.
fn validate_zip_metadata(info: &ZipInfo) -> Checked<()> {
    if info.date_time != (1980, 1, 1, 0, 0, 0)
        || info.compress_type != ZIP_STORED
        || info.create_system != 3
        || info.external_attr != 0o100600 << 16
        || info.internal_attr != 0
        || !info.extra.is_empty()
        || !info.comment.is_empty()
        || info.flag_bits != 0
    {
        return Err(fail("portable_export_invalid_zip_metadata"));
    }
    Ok(())
}

/// One member read as `iter_bounded_chunks(member, limit=info.file_size)` reads it: at most
/// `file_size` bytes go to `sink`, then a further byte is Python's
/// `portable_export_limit_total_uncompressed_bytes`.
fn read_bounded(
    archive: &ZipArchive<'_>,
    info: &ZipInfo,
    mut sink: impl FnMut(&[u8]) -> Checked<()>,
) -> Checked<u64> {
    let mut total = 0u64;
    let outcome = archive.read_chunks(info, STREAM_CHUNK_BYTES as usize, |chunk| {
        let room = (info.file_size - total.min(info.file_size)) as usize;
        let taken = chunk.len().min(room);
        if taken > 0 {
            sink(&chunk[..taken])?;
            total += taken as u64;
        }
        if chunk.len() > taken {
            return Err(fail("portable_export_limit_total_uncompressed_bytes"));
        }
        Ok(())
    });
    match outcome {
        Ok(()) => Ok(total),
        Err(ReadError::Caller(fail)) => Err(fail),
        Err(ReadError::Zip(_)) => Err(Fail::Fault),
    }
}

/// `_read_member(archive, info)`.
fn read_member(archive: &ZipArchive<'_>, info: &ZipInfo) -> Checked<Vec<u8>> {
    let mut buffer = Vec::new();
    read_bounded(archive, info, |chunk| {
        buffer.extend_from_slice(chunk);
        Ok(())
    })?;
    if buffer.len() as u64 != info.file_size {
        return Err(fail("portable_export_invalid_member_size"));
    }
    Ok(buffer)
}

/// `_stream_member_digest(archive, info)`.
fn stream_member_digest(archive: &ZipArchive<'_>, info: &ZipInfo) -> Checked<(u64, String)> {
    let mut digest = Hasher::new();
    let count = read_bounded(archive, info, |chunk| {
        digest.update(chunk);
        Ok(())
    })?;
    if count != info.file_size {
        return Err(fail("portable_export_invalid_member_size"));
    }
    Ok((count, digest.hex()))
}

fn keys_are(object: &JsonMap, fields: &[&str], ordered: bool) -> bool {
    object.len() == fields.len()
        && fields.iter().all(|field| object.contains_key(*field))
        && (!ordered || object.keys().zip(fields).all(|(key, field)| key == field))
}

/// `_validate_manifest_shape_and_bytes(manifest, encoded, enforce_declared_order_bytes=...)`.
fn validate_manifest_shape_and_bytes(manifest: &Json, encoded: &[u8], enforce: bool) -> Checked<JsonMap> {
    let invalid = || fail("portable_export_invalid_manifest");
    let canonical = || fail("portable_export_invalid_manifest_canonical_bytes");
    let Json::Object(manifest) = manifest else { return Err(invalid()) };
    if !keys_are(manifest, &MANIFEST_FIELDS, false) {
        return Err(invalid());
    }
    if enforce && !keys_are(manifest, &MANIFEST_FIELDS, true) {
        return Err(canonical());
    }
    if enforce && campaign_archive::json_bytes(&Json::Object(manifest.clone())) != encoded {
        return Err(canonical());
    }
    campaign_archive::validate_migration_provenance(
        &manifest["migration_provenance"],
        enforce,
        &manifest["source_authority_schema_version"],
    )?;
    let names: Vec<&str> = TABLES.iter().map(|table| table.name).collect();
    let Json::Object(counts) = &manifest["table_row_counts"] else { return Err(invalid()) };
    if !keys_are(counts, &names, false) {
        return Err(invalid());
    }
    if enforce && !keys_are(counts, &names, true) {
        return Err(canonical());
    }
    let Json::Object(closure) = &manifest["closure_index"] else { return Err(invalid()) };
    if !keys_are(closure, &CLOSURE_INDEX_FIELDS, false) {
        return Err(invalid());
    }
    if enforce && !keys_are(closure, &CLOSURE_INDEX_FIELDS, true) {
        return Err(canonical());
    }
    let objects = |value: &Json, fields: &[&str]| -> Checked<()> {
        let Json::Array(items) = value else { return Err(invalid()) };
        if items.iter().any(|item| !matches!(item, Json::Object(object) if keys_are(object, fields, false))) {
            return Err(invalid());
        }
        if enforce
            && items
                .iter()
                .any(|item| !matches!(item, Json::Object(object) if keys_are(object, fields, true)))
        {
            return Err(canonical());
        }
        Ok(())
    };
    objects(&closure["package_identities"], &PACKAGE_IDENTITY_FIELDS)?;
    objects(&manifest["non_manifest_members"], &NON_MANIFEST_MEMBER_FIELDS)?;
    Ok(manifest.clone())
}

/// `(str(row["package_id"]), str(row["version"]), str(row[sha_column]))`.
type Identity = (String, String, String);

fn package_identity(row: &JsonMap, sha_column: &str) -> Identity {
    (py_str(&row["package_id"]), py_str(&row["version"]), py_str(&row[sha_column]))
}

/// `_PackageSummary`.
struct PackageSummary {
    graph: PackageGraphNode,
    tool_names: HashSet<String>,
    participant_artifacts: HashMap<String, (String, String)>,
    canonical_sha256: String,
    requires_state_ownership: bool,
}

/// `_StagingSummary`.
struct StagingSummary {
    status: String,
    report_sha256: String,
    packages: Vec<(Identity, String)>,
}

/// `_AuthorityScanSummary`.
#[derive(Default)]
struct Summary {
    byte_count: u64,
    sha256: String,
    record_count: i64,
    table_counts: Vec<(&'static str, i64)>,
    actual_package_digests: HashMap<&'static str, Hasher>,
    expected_package_digests: HashMap<&'static str, Hasher>,
    expected_package_counts: HashMap<&'static str, i64>,
    last_table_index: i64,
    canonical_bytes_valid: bool,
    last_keys: HashMap<&'static str, Vec<Json>>,
    campaign_id: Option<Json>,
    current_lock_id: Option<String>,
    campaign_revision: Option<i64>,
    maximum_revision: Option<i64>,
    parents: HashMap<&'static str, HashSet<Key>>,
    required: HashMap<&'static str, HashSet<Key>>,
    manifests: HashMap<Identity, PackageSummary>,
    roots: HashSet<Identity>,
    lock_memberships: HashMap<String, Vec<(Identity, String)>>,
    lock_membership_orders: HashMap<String, Vec<i64>>,
    lock_closure_sha256: HashMap<String, String>,
    lock_availability: HashMap<String, String>,
    candidate_memberships: HashMap<String, Vec<Identity>>,
    candidate_membership_orders: HashMap<String, Vec<i64>>,
    candidate_closure_sha256: HashMap<String, String>,
    candidates: HashMap<String, (String, String, String)>,
    staging: HashMap<String, StagingSummary>,
    handoff_package_bases: Vec<(String, Option<String>)>,
    action_bindings: HashSet<(String, Identity, String)>,
    participant_bindings: HashSet<(Identity, String, String, String)>,
    participant_has_adaptation: bool,
    closure_lock_ids: HashSet<String>,
    closure_action_ids: HashSet<String>,
    closure_candidate_ids: HashSet<String>,
    closure_blob_sha256s: HashSet<String>,
    closure_plan_ids: HashSet<String>,
    closure_handoff_ids: HashSet<String>,
    closure_session_ids: HashSet<String>,
    blob_metadata: HashMap<String, (Json, Json)>,
    file_blob_byte_counts: HashMap<String, HashSet<Key>>,
    namespace_owners: HashMap<String, Option<String>>,
    state_namespaces: HashSet<String>,
}

impl Summary {
    fn new() -> Self {
        let mut summary = Summary {
            table_counts: TABLES.iter().map(|table| (table.name, 0)).collect(),
            last_table_index: -1,
            canonical_bytes_valid: true,
            ..Summary::default()
        };
        for table in PACKAGE_RELATION_TABLES {
            summary.actual_package_digests.insert(table, Hasher::new());
            summary.expected_package_digests.insert(table, Hasher::new());
            summary.expected_package_counts.insert(table, 0);
        }
        for parent in PARENTS {
            summary.parents.insert(parent, HashSet::new());
            summary.required.insert(parent, HashSet::new());
        }
        summary
    }

    fn count(&self, table: &str) -> i64 {
        self.table_counts.iter().find(|(name, _)| *name == table).map_or(0, |(_, count)| *count)
    }
}

/// `_canonical_package_json(value)`.
fn canonical_package_json(value: &Json) -> String {
    String::from_utf8(canonical_json_bytes(value)).expect("canonical JSON is UTF-8")
}

fn object(pairs: Vec<(&str, Json)>) -> JsonMap {
    pairs.into_iter().map(|(key, value)| (key.to_owned(), value)).collect()
}

/// `_iter_manifest_relation_rows(manifest)`: every relation row the manifest implies, in
/// Python's order and with Python's members.
fn manifest_relation_rows(manifest: &PackageManifest) -> Vec<(&'static str, JsonMap)> {
    let (package_id, version, content_sha256) = manifest.identity();
    let id = || Json::from(package_id);
    let ver = || Json::from(version);
    let sha = || Json::from(content_sha256);
    let strings = |items: &[String]| Json::Array(items.iter().cloned().map(Json::String).collect());
    let mut rows = vec![
        (
            "packages",
            object(vec![
                ("package_id", id()),
                ("version", ver()),
                ("sha256", sha()),
                ("capabilities_json", Json::from(canonical_package_json(&strings(&manifest.capabilities)))),
            ]),
        ),
        (
            "package_manifests",
            object(vec![
                ("package_id", id()),
                ("version", ver()),
                ("content_sha256", sha()),
                ("role", Json::from(manifest.role.as_str())),
                ("canonical_json", Json::from(manifest.canonical_json.clone())),
                (
                    "metadata_json",
                    Json::from(canonical_package_json(&Json::Object(manifest.metadata.clone()))),
                ),
                (
                    "provenance_json",
                    Json::from(canonical_package_json(&Json::Object(manifest.provenance.clone()))),
                ),
                (
                    "compatibility_json",
                    Json::from(canonical_package_json(&Json::Object(manifest.compatibility.clone()))),
                ),
                ("status", Json::from("published")),
            ]),
        ),
    ];
    for (order, file) in manifest.files.iter().enumerate() {
        rows.push((
            "package_manifest_files",
            object(vec![
                ("package_id", id()),
                ("version", ver()),
                ("content_sha256", sha()),
                ("file_order", Json::from(order)),
                ("portable_path", Json::from(file.path())),
                ("byte_count", Json::from(file.byte_count())),
                ("blob_sha256", Json::from(file.sha256())),
            ]),
        ));
    }
    for (order, dependency) in manifest.dependencies.iter().enumerate() {
        rows.push((
            "package_dependencies",
            object(vec![
                ("package_id", id()),
                ("version", ver()),
                ("sha256", sha()),
                ("dependency_order", Json::from(order)),
                ("dependency_id", Json::from(dependency.package_id())),
                ("dependency_version", Json::from(dependency.version())),
                ("dependency_sha256", Json::from(dependency.content_sha256())),
                ("dependency_kind", Json::from(dependency.kind())),
                ("dependency_namespace", Json::from(dependency.namespace())),
            ]),
        ));
        rows.push((
            "package_exact_dependencies",
            object(vec![
                ("package_id", id()),
                ("version", ver()),
                ("content_sha256", sha()),
                ("dependency_order", Json::from(order)),
                ("dependency_namespace", Json::from(dependency.namespace())),
                ("dependency_kind", Json::from(dependency.kind())),
                ("dependency_id", Json::from(dependency.package_id())),
                ("dependency_version", Json::from(dependency.version())),
                ("dependency_content_sha256", Json::from(dependency.content_sha256())),
            ]),
        ));
    }
    for (order, capability) in manifest.capabilities.iter().enumerate() {
        rows.push((
            "package_capabilities",
            object(vec![
                ("package_id", id()),
                ("version", ver()),
                ("content_sha256", sha()),
                ("capability_order", Json::from(order)),
                ("capability", Json::from(capability.clone())),
            ]),
        ));
    }
    for (order, tool) in manifest.tools.iter().enumerate() {
        let predicates: Vec<Json> = tool
            .state_predicates
            .iter()
            .map(|item| {
                let mut predicate = object(vec![
                    ("namespace", Json::from(item.namespace.clone())),
                    ("key", Json::from(item.key.clone())),
                    ("equals", item.equals.clone()),
                ]);
                if item.negated {
                    predicate.insert("negated".into(), Json::Bool(true));
                }
                Json::Object(predicate)
            })
            .collect();
        rows.push((
            "package_tools",
            object(vec![
                ("package_id", id()),
                ("version", ver()),
                ("content_sha256", sha()),
                ("tool_order", Json::from(order)),
                ("tool_name", Json::from(tool.name.clone())),
                ("capability", Json::from(tool.capability.clone())),
                (
                    "input_schema_json",
                    Json::from(canonical_package_json(&Json::Object(tool.input_schema.clone()))),
                ),
                ("state_predicates_json", Json::from(canonical_package_json(&Json::Array(predicates)))),
                ("turn_statuses_json", Json::from(canonical_package_json(&strings(&tool.turn_statuses)))),
                ("writers_json", Json::from(canonical_package_json(&strings(&tool.writers)))),
            ]),
        ));
    }
    for (table, identity_key, surfaces) in [
        ("package_prompt_fragments", "fragment_id", &manifest.prompt_fragments),
        ("package_ui_surfaces", "surface_id", &manifest.ui_surfaces),
    ] {
        for (order, surface) in surfaces.iter().enumerate() {
            rows.push((
                table,
                object(vec![
                    ("package_id", id()),
                    ("version", ver()),
                    ("content_sha256", sha()),
                    ("surface_order", Json::from(order)),
                    (identity_key, Json::from(surface.identity.clone())),
                    ("capability", Json::from(surface.capability.clone())),
                ]),
            ));
        }
    }
    for (order, artifact) in manifest.participant_artifacts.iter().enumerate() {
        rows.push((
            "package_participant_artifacts",
            object(vec![
                ("package_id", id()),
                ("version", ver()),
                ("content_sha256", sha()),
                ("artifact_order", Json::from(order)),
                ("kind", Json::from(artifact.kind.clone())),
                ("schema_id", Json::from(artifact.schema_id.clone())),
                ("schema_sha256", Json::from(artifact.schema_sha256.clone())),
                ("schema_json", Json::from(canonical_package_json(&Json::Object(artifact.schema.clone())))),
            ]),
        ));
    }
    rows
}

/// `_record_manifest_summary(summary, row)`.
fn record_manifest_summary(summary: &mut Summary, row: &JsonMap) -> Checked<()> {
    let graph_failure = || fail("portable_export_invalid_package_graph");
    let canonical = py_str(&row["canonical_json"]);
    let decoded = match pyjson::loads_hooked(
        &canonical,
        HookPolicy { refuse_duplicates: true, refuse_constants: true },
    ) {
        Ok(read) if !read.non_finite => read.value,
        _ => return Err(graph_failure()),
    };
    if canonical_package_json(&decoded) != canonical {
        return Err(graph_failure());
    }
    let manifest = match manifest_from_record(&decoded, None) {
        Ok(manifest) => manifest,
        // Only `KeyError`, `TypeError`, `ValueError` and `RecursionError` are Python's graph
        // failure; a `CommandRejected` passes through.
        Err(error) => {
            let store: StoreError = error.into();
            return Err(match store.rejection() {
                Some(coded) if coded.error_type == Some("CommandRejected") => Fail::Rejected(store),
                _ => graph_failure(),
            });
        }
    };
    let identity = {
        let (package_id, version, sha256) = manifest.identity();
        (package_id.to_owned(), version.to_owned(), sha256.to_owned())
    };
    if summary.manifests.contains_key(&identity) || identity != package_identity(row, "content_sha256") {
        return Err(graph_failure());
    }
    for (table, expected_row) in manifest_relation_rows(&manifest) {
        *summary.expected_package_counts.get_mut(table).expect("a relation table") += 1;
        summary
            .expected_package_digests
            .get_mut(table)
            .expect("a relation table")
            .update(&canonical_authority_record(table, &expected_row));
    }
    summary.manifests.insert(
        identity,
        PackageSummary {
            graph: PackageGraphNode::from_manifest(&manifest),
            tool_names: manifest.tools.iter().map(|tool| tool.name.clone()).collect(),
            participant_artifacts: manifest
                .participant_artifacts
                .iter()
                .map(|artifact| {
                    (artifact.kind.clone(), (artifact.schema_id.clone(), artifact.schema_sha256.clone()))
                })
                .collect(),
            canonical_sha256: sha256_hex(canonical.as_bytes()),
            requires_state_ownership: manifest.content_declarations_path.is_some(),
        },
    );
    Ok(())
}

/// `str(value)`, or `None` for `null`.
fn optional_text(value: &Json) -> Option<String> {
    (!value.is_null()).then(|| py_str(value))
}

/// `_record_authority_summary(summary, raw_line, expected_campaign_id=..., source_schema_version=...)`.
fn record_authority_summary(
    summary: &mut Summary,
    raw_line: &[u8],
    expected_campaign_id: &str,
    source_schema_version: i64,
) -> Checked<()> {
    if raw_line.is_empty() || raw_line.len() as i64 + 1 > limit("single_authority_row_bytes") {
        return Err(fail("portable_export_invalid_authority_framing"));
    }
    let schema = || fail("portable_export_invalid_authority_schema");
    let Json::Object(item) = decode_json(raw_line, "portable_export_invalid_authority_framing")? else {
        return Err(schema());
    };
    if !keys_are(&item, &["table", "row"], false) {
        return Err(schema());
    }
    if !keys_are(&item, &["table", "row"], true) {
        summary.canonical_bytes_valid = false;
    }
    let (Json::String(table_name), Json::Object(row)) = (&item["table"], &item["row"]) else {
        return Err(schema());
    };
    let Some(table_index) = TABLES.iter().position(|table| table.name == table_name) else {
        return Err(schema());
    };
    let table = &TABLES[table_index];
    let table_name: &'static str = table.name;
    if (table_index as i64) < summary.last_table_index {
        return Err(fail("portable_export_invalid_authority_order"));
    }
    summary.last_table_index = table_index as i64;
    let columns = authority_columns(table, source_schema_version);
    if !keys_are(row, columns, false) {
        return Err(schema());
    }
    if !keys_are(row, columns, true) {
        summary.canonical_bytes_valid = false;
    }
    let materialized: JsonMap =
        columns.iter().map(|column| ((*column).to_owned(), row[*column].clone())).collect();
    let encoded = canonical_authority_record(table_name, &materialized);
    if encoded[..encoded.len() - 1] != *raw_line {
        summary.canonical_bytes_valid = false;
    }
    let key_values: Vec<Json> = table.keys.iter().map(|column| materialized[*column].clone()).collect();
    if let Some(previous) = summary.last_keys.get(table_name) {
        // `key <= previous`; values Python cannot order are its `TypeError`, the same refusal.
        match tuple_less_equal(&key_values, previous) {
            Ok(false) => {}
            _ => return Err(fail("portable_export_invalid_authority_order")),
        }
    }
    summary.last_keys.insert(table_name, key_values);
    summary.record_count += 1;
    let table_count = {
        let entry =
            summary.table_counts.iter_mut().find(|(name, _)| *name == table_name).expect("every table");
        entry.1 += 1;
        entry.1
    };
    if summary.record_count > limit("authority_record_count") {
        return Err(fail("portable_export_limit_authority_record_count"));
    }
    if table_count > limit("single_table_row_count") {
        return Err(fail("portable_export_limit_single_table_row_count"));
    }
    if let Some(digest) = summary.actual_package_digests.get_mut(table_name) {
        digest.update(&encoded);
    }
    if let Some(campaign_id) = materialized.get("campaign_id") {
        key(campaign_id)?;
        if !py_eq(campaign_id, &Json::from(expected_campaign_id)) {
            return Err(fail("portable_export_invalid_authority_closure"));
        }
    }
    let field = |column: &str| &materialized[column];
    let text = |column: &str| py_str(&materialized[column]);
    let mut parent_values: Vec<(&'static str, &Json)> = Vec::new();
    match table_name {
        "campaign_operations" => parent_values.push(("operations", field("operation_id"))),
        "turns" => parent_values.push(("turns", field("turn_id"))),
        "campaign_locks" => {
            parent_values.push(("locks", field("lock_id")));
            let lock_id = text("lock_id");
            summary.closure_lock_ids.insert(lock_id.clone());
            summary.lock_closure_sha256.insert(lock_id.clone(), text("closure_sha256"));
            summary.lock_availability.insert(lock_id, text("availability"));
        }
        "actions" => {
            parent_values.push(("actions", field("action_id")));
            summary.closure_action_ids.insert(text("action_id"));
        }
        "package_activations" => parent_values.push(("activations", field("activation_id"))),
        "campaign_plans" => {
            parent_values.push(("plans", field("plan_id")));
            summary.closure_plan_ids.insert(text("plan_id"));
        }
        "builder_dm_handoffs" => {
            parent_values.push(("handoffs", field("handoff_id")));
            summary.closure_handoff_ids.insert(text("handoff_id"));
            summary
                .handoff_package_bases
                .push((text("package_lock_id"), optional_text(field("package_candidate_id"))));
        }
        "agent_sessions" => {
            parent_values.push(("sessions", field("session_id")));
            summary.closure_session_ids.insert(text("session_id"));
        }
        "participants" => parent_values.push(("participants", field("participant_id"))),
        "package_candidates" => {
            parent_values.push(("candidates", field("candidate_id")));
            summary.closure_candidate_ids.insert(text("candidate_id"));
        }
        "package_staging" => parent_values.push(("installs", field("install_id"))),
        "canonical_events" => {
            parent_values.push(("event_sequences", field("sequence")));
            if !field("attempt_id").is_null() {
                parent_values.push(("attempts", field("attempt_id")));
            }
        }
        // An interrupted attempt can fail before producing any canonical event; its durable
        // interruption row is therefore the root for that attempt identity.
        "narration_interruptions" => parent_values.push(("attempts", field("failed_attempt_id"))),
        _ => {}
    }
    for (parent, value) in parent_values {
        let value = key(value)?;
        summary.parents.get_mut(parent).expect("a parent").insert(value);
    }
    for (column, parent) in reference_summaries(table_name) {
        let value = field(column);
        if !value.is_null() {
            let value = key(value)?;
            summary.required.get_mut(parent).expect("a parent").insert(value);
        }
    }
    match table_name {
        "campaigns" => {
            summary.campaign_id = Some(field("campaign_id").clone());
            summary.current_lock_id = optional_text(field("current_lock_id"));
            summary.campaign_revision = Some(int_of(field("revision"))?);
        }
        "campaign_revisions" => {
            let revision = int_of(field("revision"))?;
            summary.maximum_revision =
                Some(summary.maximum_revision.map_or(revision, |maximum| maximum.max(revision)));
        }
        "package_manifests" => record_manifest_summary(summary, &materialized)?,
        "campaign_package_memberships" => {
            summary.roots.insert(package_identity(&materialized, "sha256"));
        }
        "campaign_lock_memberships" => {
            let identity = package_identity(&materialized, "content_sha256");
            summary.roots.insert(identity.clone());
            let lock_id = text("lock_id");
            summary.lock_memberships.entry(lock_id.clone()).or_default().push((identity, text("role")));
            let order = int_of(field("closure_order"))?;
            summary.lock_membership_orders.entry(lock_id).or_default().push(order);
        }
        "campaign_state_namespace_owners" => {
            summary.namespace_owners.insert(text("namespace"), optional_text(field("owner_package_id")));
        }
        "campaign_state" => {
            summary.state_namespaces.insert(text("namespace"));
        }
        "package_candidate_memberships" => {
            let identity = package_identity(&materialized, "content_sha256");
            summary.roots.insert(identity.clone());
            let candidate_id = text("candidate_id");
            summary.candidate_memberships.entry(candidate_id.clone()).or_default().push(identity);
            let order = int_of(field("closure_order"))?;
            summary.candidate_membership_orders.entry(candidate_id).or_default().push(order);
        }
        "package_candidates" => {
            let graph_failure = || fail("portable_export_invalid_package_graph");
            let (
                Json::String(candidate_id),
                Json::String(install_id),
                Json::String(status),
                Json::String(report_json),
            ) = (field("candidate_id"), field("install_id"), field("status"), field("report_json"))
            else {
                return Err(graph_failure());
            };
            summary.candidates.insert(
                candidate_id.clone(),
                (install_id.clone(), sha256_hex(report_json.as_bytes()), status.clone()),
            );
            summary.candidate_closure_sha256.insert(candidate_id.clone(), text("closure_sha256"));
        }
        "package_staging" => record_staging(summary, &materialized)?,
        "actions" if !field("package_id").is_null() => {
            let identity = (text("package_id"), text("package_version"), text("package_sha256"));
            summary.roots.insert(identity.clone());
            summary.action_bindings.insert((text("lock_id"), identity, text("tool_name")));
        }
        "participants" => {
            if !field("adaptation_id").is_null() {
                summary.participant_has_adaptation = true;
            }
            if !field("package_id").is_null() {
                let identity = (text("package_id"), text("package_version"), text("package_sha256"));
                summary.roots.insert(identity.clone());
                summary.participant_bindings.insert((
                    identity,
                    text("kind"),
                    text("schema_id"),
                    text("schema_sha256"),
                ));
            }
        }
        "package_blobs" => {
            let blob_sha256 = text("blob_sha256");
            summary.closure_blob_sha256s.insert(blob_sha256.clone());
            summary
                .blob_metadata
                .insert(blob_sha256, (field("byte_count").clone(), field("chunk_count").clone()));
        }
        "package_manifest_files" => {
            let byte_count = key(field("byte_count"))?;
            summary.file_blob_byte_counts.entry(text("blob_sha256")).or_default().insert(byte_count);
        }
        _ => {}
    }
    Ok(())
}

/// The `package_staging` branch of `_record_authority_summary`.
fn record_staging(summary: &mut Summary, row: &JsonMap) -> Checked<()> {
    let graph_failure = || fail("portable_export_invalid_package_graph");
    let (
        Json::String(install_id),
        Json::String(status),
        Json::String(manifest_json),
        Json::String(report_json),
    ) = (&row["install_id"], &row["status"], &row["manifest_json"], &row["report_json"])
    else {
        return Err(graph_failure());
    };
    let manifest_value = match pyjson::loads_hooked(
        manifest_json,
        HookPolicy { refuse_duplicates: true, refuse_constants: true },
    ) {
        Ok(read) if !read.non_finite => read.value,
        _ => return Err(graph_failure()),
    };
    let Json::Object(manifest_object) = &manifest_value else { return Err(graph_failure()) };
    let format_known = [1, 2, 3, 4].iter().any(|format| {
        py_eq(&manifest_object.get("format").cloned().unwrap_or(Json::Null), &Json::from(*format))
    });
    let Some(Json::Array(records)) = manifest_object.get("packages") else { return Err(graph_failure()) };
    if !keys_are(manifest_object, &["format", "packages"], true)
        || !format_known
        || canonical_package_json(&manifest_value) != *manifest_json
    {
        return Err(graph_failure());
    }
    let mut packages = Vec::new();
    for record in records {
        let Json::Object(record) = record else { return Err(graph_failure()) };
        let identity = match (record.get("id"), record.get("version"), record.get("content_sha256")) {
            (Some(Json::String(id)), Some(Json::String(version)), Some(Json::String(sha256))) => {
                (id.clone(), version.clone(), sha256.clone())
            }
            _ => return Err(graph_failure()),
        };
        packages.push((identity, sha256_hex(canonical_json_bytes(&Json::Object(record.clone())))));
    }
    summary.staging.insert(
        install_id.clone(),
        StagingSummary {
            status: status.clone(),
            report_sha256: sha256_hex(report_json.as_bytes()),
            packages,
        },
    );
    Ok(())
}

/// `_scan_authority_member(archive, info, expected_campaign_id=..., source_schema_version=...)`.
fn scan_authority_member(
    archive: &ZipArchive<'_>,
    info: &ZipInfo,
    expected_campaign_id: &str,
    source_schema_version: i64,
) -> Checked<Summary> {
    let mut summary = Summary::new();
    let mut digest = Hasher::new();
    let mut current_line: Vec<u8> = Vec::new();
    let framing = || fail("portable_export_invalid_authority_framing");
    read_bounded(archive, info, |chunk| {
        digest.update(chunk);
        summary.byte_count += chunk.len() as u64;
        current_line.extend_from_slice(chunk);
        loop {
            let Some(newline) = current_line.iter().position(|byte| *byte == b'\n') else {
                if current_line.len() as i64 + 1 > limit("single_authority_row_bytes") {
                    return Err(framing());
                }
                break;
            };
            let raw_line: Vec<u8> = current_line.drain(..=newline).take(newline).collect();
            if raw_line.contains(&b'\r') {
                return Err(framing());
            }
            record_authority_summary(&mut summary, &raw_line, expected_campaign_id, source_schema_version)?;
        }
        Ok(())
    })?;
    if summary.byte_count != info.file_size {
        return Err(fail("portable_export_invalid_member_size"));
    }
    if !current_line.is_empty() || summary.record_count == 0 {
        return Err(framing());
    }
    summary.sha256 = digest.hex();
    Ok(summary)
}

/// `_validate_owned_memberships(summary, memberships, roles=...)`.
fn validate_owned_memberships(
    summary: &Summary,
    memberships: &HashMap<String, Vec<Identity>>,
    roles: Option<&HashMap<String, Vec<String>>>,
) -> Checked<()> {
    let graph_failure = || fail("portable_export_invalid_package_graph");
    for (owner, selected) in memberships {
        let mut nodes = Vec::new();
        for identity in selected {
            let Some(manifest) = summary.manifests.get(identity) else { return Err(graph_failure()) };
            nodes.push(manifest.graph.clone());
        }
        if let Err(error) = resolve_package_graph(&nodes) {
            let store: StoreError = error.into();
            return Err(match store.rejection() {
                Some(coded) if coded.error_type == Some("CommandRejected") => Fail::Rejected(store),
                _ => graph_failure(),
            });
        }
        if let Some(roles) = roles {
            let owner_roles = &roles[owner];
            if owner_roles.len() != selected.len() {
                return Err(Fail::Fault);
            }
            if selected
                .iter()
                .zip(owner_roles)
                .any(|(identity, role)| summary.manifests[identity].graph.role.as_str() != role)
            {
                return Err(graph_failure());
            }
        }
    }
    Ok(())
}

/// The `streamed_closure_digest(selected, roles, orders)` of one lock or candidate.
fn streamed_closure_digest(selected: &[Identity], roles: &[String], orders: &[i64]) -> Checked<String> {
    if orders.iter().copied().ne(0..selected.len() as i64) || roles.len() != selected.len() {
        return Err(fail("portable_export_invalid_package_graph"));
    }
    let representation: Vec<Json> = selected
        .iter()
        .zip(roles)
        .enumerate()
        .map(|(order, (identity, role))| {
            Json::Object(object(vec![
                ("id", Json::from(identity.0.clone())),
                ("version", Json::from(identity.1.clone())),
                ("content_sha256", Json::from(identity.2.clone())),
                ("role", Json::from(role.clone())),
                ("load_order", Json::from(order)),
            ]))
        })
        .collect();
    Ok(sha256_hex(canonical_json_bytes(&Json::Array(representation))))
}

/// `_validate_streamed_authority(summary, expected_campaign_id=...)`.
fn validate_streamed_authority(summary: &Summary, expected_campaign_id: &str) -> Checked<()> {
    let closure = || fail("portable_export_invalid_authority_closure");
    let graph_failure = || fail("portable_export_invalid_package_graph");
    if summary.count("campaigns") != 1
        || !summary.campaign_id.as_ref().is_some_and(|id| py_eq(id, &Json::from(expected_campaign_id)))
        || summary.campaign_revision.is_none()
        || summary.maximum_revision != summary.campaign_revision
    {
        return Err(closure());
    }
    if PARENTS.iter().any(|parent| !summary.required[parent].is_subset(&summary.parents[parent])) {
        return Err(closure());
    }
    if summary.participant_has_adaptation {
        return Err(closure());
    }
    for table in PACKAGE_RELATION_TABLES {
        if summary.count(table) != summary.expected_package_counts[table]
            || summary.actual_package_digests[table].clone().hex()
                != summary.expected_package_digests[table].clone().hex()
        {
            return Err(graph_failure());
        }
    }
    let mut reachable: HashSet<Identity> = HashSet::new();
    let mut pending: Vec<Identity> = summary.roots.iter().cloned().collect();
    while let Some(identity) = pending.pop() {
        if reachable.contains(&identity) {
            continue;
        }
        let Some(manifest) = summary.manifests.get(&identity) else { return Err(graph_failure()) };
        pending.extend(manifest.graph.dependencies.iter().map(|dependency| {
            (
                dependency.package_id().to_owned(),
                dependency.version().to_owned(),
                dependency.content_sha256().to_owned(),
            )
        }));
        reachable.insert(identity);
    }
    let identities: HashSet<Identity> = summary.manifests.keys().cloned().collect();
    let keys = |map: &dyn Fn() -> HashSet<String>| map();
    if reachable != identities
        || !keys(&|| summary.lock_memberships.keys().cloned().collect()).is_subset(&summary.closure_lock_ids)
        || keys(&|| summary.lock_closure_sha256.keys().cloned().collect()) != summary.closure_lock_ids
        || keys(&|| summary.lock_availability.keys().cloned().collect()) != summary.closure_lock_ids
        || keys(&|| summary.candidate_memberships.keys().cloned().collect()) != summary.closure_candidate_ids
        || keys(&|| summary.candidate_closure_sha256.keys().cloned().collect())
            != summary.closure_candidate_ids
    {
        return Err(graph_failure());
    }
    let lock_identities: HashMap<String, Vec<Identity>> = summary
        .lock_memberships
        .iter()
        .map(|(owner, memberships)| {
            (owner.clone(), memberships.iter().map(|(identity, _)| identity.clone()).collect())
        })
        .collect();
    let lock_roles: HashMap<String, Vec<String>> = summary
        .lock_memberships
        .iter()
        .map(|(owner, memberships)| {
            (owner.clone(), memberships.iter().map(|(_, role)| role.clone()).collect())
        })
        .collect();
    validate_owned_memberships(summary, &lock_identities, Some(&lock_roles))?;
    validate_owned_memberships(summary, &summary.candidate_memberships, None)?;
    let current_identities: Vec<Identity> = match &summary.current_lock_id {
        None => Vec::new(),
        Some(lock_id) => {
            if !summary.closure_lock_ids.contains(lock_id) {
                return Err(closure());
            }
            lock_identities.get(lock_id).cloned().unwrap_or_default()
        }
    };
    let ownership_required = current_identities.iter().any(|identity| {
        summary
            .manifests
            .get(identity)
            .map(|manifest| manifest.requires_state_ownership)
            .ok_or(())
            .unwrap_or(false)
    });
    if current_identities.iter().any(|identity| !summary.manifests.contains_key(identity)) {
        return Err(Fail::Fault);
    }
    let owner_namespaces: HashSet<String> = summary.namespace_owners.keys().cloned().collect();
    if ownership_required && owner_namespaces != summary.state_namespaces {
        return Err(closure());
    }
    if !summary.namespace_owners.is_empty()
        && (owner_namespaces != summary.state_namespaces
            || summary.namespace_owners.iter().any(|(namespace, owner)| {
                !owner_matches_package_lock(
                    current_identities.iter().map(|identity| identity.0.as_str()),
                    namespace,
                    owner.as_deref(),
                )
            }))
    {
        return Err(closure());
    }
    for lock_id in &summary.closure_lock_ids {
        let selected = lock_identities.get(lock_id).cloned().unwrap_or_default();
        let roles = lock_roles.get(lock_id).cloned().unwrap_or_default();
        let orders = summary.lock_membership_orders.get(lock_id).cloned().unwrap_or_default();
        if summary.lock_closure_sha256[lock_id] != streamed_closure_digest(&selected, &roles, &orders)? {
            return Err(graph_failure());
        }
    }
    for (candidate_id, selected) in &summary.candidate_memberships {
        let roles: Vec<String> = selected
            .iter()
            .map(|identity| summary.manifests[identity].graph.role.as_str().to_owned())
            .collect();
        let orders = summary.candidate_membership_orders.get(candidate_id).cloned().unwrap_or_default();
        if summary.candidate_closure_sha256[candidate_id]
            != streamed_closure_digest(selected, &roles, &orders)?
        {
            return Err(graph_failure());
        }
    }
    // `_validate_handoff_package_basis(handoffs=..., locks=..., candidates=...)`.
    for (lock_id, candidate_id) in &summary.handoff_package_bases {
        if !summary.closure_lock_ids.contains(lock_id) || summary.lock_availability[lock_id] != "available" {
            return Err(graph_failure());
        }
        let lock_closure: Vec<(Identity, String)> =
            summary.lock_memberships.get(lock_id).cloned().unwrap_or_default();
        if lock_closure.is_empty() {
            if candidate_id.is_some() {
                return Err(graph_failure());
            }
            continue;
        }
        let Some(candidate_id) =
            candidate_id.as_ref().filter(|id| summary.closure_candidate_ids.contains(*id))
        else {
            return Err(graph_failure());
        };
        let candidate_closure: Vec<(Identity, String)> = summary.candidate_memberships[candidate_id]
            .iter()
            .map(|identity| (identity.clone(), summary.manifests[identity].graph.role.as_str().to_owned()))
            .collect();
        if summary.candidates[candidate_id].2 != "validated"
            || summary.candidate_closure_sha256[candidate_id] != summary.lock_closure_sha256[lock_id]
            || candidate_closure != lock_closure
        {
            return Err(graph_failure());
        }
    }
    if summary.candidates.len() != summary.staging.len() {
        return Err(graph_failure());
    }
    let mut selected_installs: HashSet<String> = HashSet::new();
    for (candidate_id, (install_id, report_sha256, _status)) in &summary.candidates {
        if !selected_installs.insert(install_id.clone()) {
            return Err(graph_failure());
        }
        let staging = summary.staging.get(install_id);
        let memberships = summary.candidate_memberships.get(candidate_id);
        let orders = summary.candidate_membership_orders.get(candidate_id);
        let valid = match (staging, memberships) {
            (Some(staging), Some(memberships)) => {
                staging.status == "published"
                    && staging.report_sha256 == *report_sha256
                    && orders.is_some_and(|orders| orders.iter().copied().eq(0..memberships.len() as i64))
                    && staging.packages.iter().map(|(identity, _)| identity).eq(memberships.iter())
                    && staging.packages.iter().all(|(identity, digest)| {
                        summary
                            .manifests
                            .get(identity)
                            .is_some_and(|manifest| manifest.canonical_sha256 == *digest)
                    })
            }
            _ => false,
        };
        if !valid {
            return Err(graph_failure());
        }
    }
    if selected_installs != summary.staging.keys().cloned().collect::<HashSet<_>>() {
        return Err(graph_failure());
    }
    for (lock_id, identity, tool_name) in &summary.action_bindings {
        let bound = summary
            .lock_memberships
            .get(lock_id)
            .is_some_and(|memberships| memberships.iter().any(|(member, _)| member == identity))
            && summary
                .manifests
                .get(identity)
                .is_some_and(|manifest| manifest.tool_names.contains(tool_name));
        if !bound {
            return Err(graph_failure());
        }
    }
    for (identity, kind, schema_id, schema_sha256) in &summary.participant_bindings {
        let bound = summary.manifests.get(identity).is_some_and(|manifest| {
            manifest.participant_artifacts.get(kind) == Some(&(schema_id.clone(), schema_sha256.clone()))
        });
        if !bound {
            return Err(graph_failure());
        }
    }
    Ok(())
}

/// `_streamed_closure_index(summary)`.
fn streamed_closure_index(summary: &Summary) -> Json {
    let sorted = |set: &HashSet<String>| {
        Json::Array(set.iter().cloned().collect::<BTreeSet<_>>().into_iter().map(Json::String).collect())
    };
    let packages: BTreeSet<&Identity> = summary.manifests.keys().collect();
    Json::Object(object(vec![
        ("lock_ids", sorted(&summary.closure_lock_ids)),
        ("action_ids", sorted(&summary.closure_action_ids)),
        ("candidate_ids", sorted(&summary.closure_candidate_ids)),
        (
            "package_identities",
            Json::Array(
                packages
                    .into_iter()
                    .map(|(package_id, version, content_sha256)| {
                        Json::Object(object(vec![
                            ("package_id", Json::from(package_id.clone())),
                            ("version", Json::from(version.clone())),
                            ("content_sha256", Json::from(content_sha256.clone())),
                        ]))
                    })
                    .collect(),
            ),
        ),
        ("blob_sha256s", sorted(&summary.closure_blob_sha256s)),
        ("plan_ids", sorted(&summary.closure_plan_ids)),
        ("handoff_ids", sorted(&summary.closure_handoff_ids)),
        ("session_ids", sorted(&summary.closure_session_ids)),
    ]))
}

/// `validate_campaign_archive(path, expected_campaign_id=...)`: the archive's manifest, or the
/// refusal Python gives.
pub fn validate_campaign_archive(path: &Path, expected_campaign_id: &str) -> Result<JsonMap, StoreError> {
    let Ok(file) = std::fs::File::open(path) else { return Err(failure("portable_export_invalid_zip")) };
    validate_campaign_archive_file(&file, expected_campaign_id)
}

/// `validate_campaign_archive(raw, expected_campaign_id=...)` on an open file, as recovery passes
/// its sealed spool.
pub fn validate_campaign_archive_file(
    file: &std::fs::File,
    expected_campaign_id: &str,
) -> Result<JsonMap, StoreError> {
    let Ok(archive) = ZipArchive::open_file(file) else {
        return Err(failure("portable_export_invalid_zip"));
    };
    match validate_open_archive(&archive, expected_campaign_id) {
        Ok(manifest) => Ok(manifest),
        Err(Fail::Rejected(error)) => Err(error),
        Err(Fail::Fault) => Err(failure("portable_export_invalid_archive")),
    }
}

/// Whether `_validate_zip_metadata(info)` accepts the member.
pub(crate) fn zip_metadata_is_exact(info: &ZipInfo) -> bool {
    validate_zip_metadata(info).is_ok()
}

fn validate_open_archive(archive: &ZipArchive<'_>, expected_campaign_id: &str) -> Checked<JsonMap> {
    let infos = archive.infos();
    if infos.len() as i64 > limit("member_count") {
        return Err(fail("portable_export_limit_member_count"));
    }
    let names: Vec<&str> = infos.iter().map(|info| info.filename.as_str()).collect();
    if names.iter().collect::<HashSet<_>>().len() != names.len() {
        return Err(fail("portable_export_invalid_duplicate_member"));
    }
    let casefolded: HashSet<String> = names.iter().map(|name| sylvia_base::pystr::casefold(name)).collect();
    let normalized: HashSet<String> =
        names.iter().map(|name| sylvia_base::pystr::casefold(&name.nfc().collect::<String>())).collect();
    if casefolded.len() != names.len() || normalized.len() != names.len() {
        return Err(fail("portable_export_invalid_member_alias"));
    }
    if names.iter().any(|name| !safe_member_name(name)) {
        return Err(fail("portable_export_invalid_member_path"));
    }
    let total: i128 = infos.iter().map(|info| i128::from(info.file_size)).sum();
    checked_archive_ledger(&[
        ("member_count", infos.len() as i64),
        ("total_uncompressed_bytes", i64::try_from(total).unwrap_or(i64::MAX)),
    ])?;
    if !archive.comment().is_empty() {
        return Err(fail("portable_export_invalid_zip_metadata"));
    }
    let order = || fail("portable_export_invalid_member_order");
    if names.first() != Some(&"authority.jsonl") || names.last() != Some(&"manifest.json") {
        return Err(order());
    }
    let private_index = infos.iter().position(|info| info.filename == PRIVATE_MEMBER_NAME);
    if private_index.is_some_and(|index| index != infos.len() - 2) {
        return Err(order());
    }
    let blob_infos =
        if private_index.is_some() { &infos[1..infos.len() - 2] } else { &infos[1..infos.len() - 1] };
    let blob_names: Vec<&str> = blob_infos.iter().map(|info| info.filename.as_str()).collect();
    if blob_names.windows(2).any(|pair| pair[0] > pair[1]) {
        return Err(order());
    }
    if blob_names.iter().any(|name| !blob_member_name(name)) {
        return Err(fail("portable_export_invalid_undeclared_member"));
    }
    for info in infos {
        validate_zip_metadata(info)?;
    }
    let (first, last) = (&infos[0], &infos[infos.len() - 1]);
    if last.file_size as i64 > limit("manifest_bytes") {
        return Err(fail("portable_export_limit_manifest_bytes"));
    }
    if first.file_size as i64 > limit("authority_jsonl_bytes") {
        return Err(fail("portable_export_limit_authority_jsonl_bytes"));
    }
    if blob_infos.iter().any(|info| info.file_size as i64 > limit("single_blob_bytes")) {
        return Err(fail("portable_export_limit_single_blob_bytes"));
    }
    let encoded_manifest = read_member(archive, last)?;
    let decoded_manifest = decode_json(&encoded_manifest, "portable_export_invalid_manifest")?;
    let manifest = validate_manifest_shape_and_bytes(&decoded_manifest, &encoded_manifest, false)?;
    let source_schema_version = int_of(&manifest["source_authority_schema_version"])?;
    let authority = scan_authority_member(archive, first, expected_campaign_id, source_schema_version)?;
    if let Some(index) = private_index {
        let private_info = &infos[index];
        if private_info.file_size > crate::private_records::MAX_BYTES as u64 {
            return Err(Fail::Rejected(crate::private_records::reject()));
        }
        crate::private_records::decode(&read_member(archive, private_info)?, &authority.closure_session_ids)?;
    }
    let manifest_canonical_bytes_valid =
        match validate_manifest_shape_and_bytes(&decoded_manifest, &encoded_manifest, true) {
            Ok(_) => true,
            Err(Fail::Rejected(error))
                if error.rejection().is_some_and(|coded| {
                    coded.code == "portable_export_invalid_manifest_canonical_bytes"
                }) =>
            {
                false
            }
            Err(other) => return Err(other),
        };
    let Json::Array(declared) = &manifest["non_manifest_members"] else { return Err(Fail::Fault) };
    let declared_names: Vec<String> = declared.iter().map(|item| py_str(&item["name"])).collect();
    if declared_names.iter().map(String::as_str).ne(names[..names.len() - 1].iter().copied()) {
        return Err(fail("portable_export_invalid_undeclared_member"));
    }
    let mut streamed_blobs: HashMap<String, (u64, String)> = HashMap::new();
    for item in declared {
        let name = py_str(&item["name"]);
        let info = infos.iter().find(|info| info.filename == name).ok_or(Fail::Fault)?;
        let (byte_count, digest) = if name == "authority.jsonl" {
            (authority.byte_count, authority.sha256.clone())
        } else {
            let (byte_count, digest) = stream_member_digest(archive, info)?;
            if let Some(blob) = name.strip_prefix("blobs/") {
                streamed_blobs.insert(blob.to_owned(), (byte_count, digest.clone()));
            }
            (byte_count, digest)
        };
        if py_str(&item["sha256"]) != digest {
            return Err(fail("portable_export_invalid_member_hash"));
        }
        if int_of(&item["byte_count"])? as i128 != i128::from(byte_count) {
            return Err(fail("portable_export_invalid_member_size"));
        }
    }
    validate_streamed_authority(&authority, expected_campaign_id)?;
    let blob_closure = || fail("portable_export_invalid_blob_closure");
    let expected_blobs: HashSet<&String> = authority.blob_metadata.keys().collect();
    let member_blobs: HashSet<String> =
        blob_names.iter().map(|name| name.trim_start_matches("blobs/").to_owned()).collect();
    let file_blobs: HashSet<&String> = authority.file_blob_byte_counts.keys().collect();
    if expected_blobs != member_blobs.iter().collect() || expected_blobs != file_blobs {
        return Err(blob_closure());
    }
    for (blob_sha256, (declared_bytes, declared_chunks)) in &authority.blob_metadata {
        let (byte_count, streamed_sha256) = &streamed_blobs[blob_sha256];
        let integer = |value: &Json| match value {
            Json::Number(number) => number.as_i64(),
            _ => None,
        };
        let valid = match (integer(declared_bytes), integer(declared_chunks)) {
            (Some(bytes), Some(chunks)) => {
                bytes as u64 == *byte_count
                    && chunks as u64 == byte_count.div_ceil(STREAM_CHUNK_BYTES)
                    && streamed_sha256 == blob_sha256
                    && authority.file_blob_byte_counts[blob_sha256]
                        == HashSet::from([Key::Int(i128::from(*byte_count))])
            }
            _ => false,
        };
        if !valid {
            return Err(blob_closure());
        }
    }
    if !authority.canonical_bytes_valid {
        return Err(fail("portable_export_invalid_authority_canonical_bytes"));
    }
    if !manifest_canonical_bytes_valid {
        return Err(fail("portable_export_invalid_manifest_canonical_bytes"));
    }
    let invalid_manifest = || fail("portable_export_invalid_manifest");
    let expected_format = if private_index.is_some() { PRIVATE_FORMAT_VERSION } else { FORMAT_VERSION };
    if !py_eq(&manifest["format_id"], &Json::from(FORMAT_ID))
        || !py_eq(&manifest["format_version"], &Json::from(expected_format))
    {
        return Err(invalid_manifest());
    }
    if !py_eq(&manifest["product_id"], &Json::from(PRODUCT_ID))
        || !py_eq(&manifest["product_generation"], &Json::from(PRODUCT_GENERATION))
    {
        return Err(invalid_manifest());
    }
    let version_known = key(&manifest["source_authority_schema_version"]).is_ok_and(|version| {
        SOURCE_AUTHORITY_SCHEMA_VERSIONS.iter().any(|known| version == Key::Int(i128::from(*known)))
    });
    if !version_known
        || !py_eq(
            &manifest["source_authority_schema_identity_sha256"],
            &Json::from(campaign_archive::source_authority_schema_claim_sha256(int_of(
                &manifest["source_authority_schema_version"],
            )?)),
        )
    {
        return Err(fail("portable_export_invalid_source_schema"));
    }
    let closure = || fail("portable_export_invalid_authority_closure");
    if !py_eq(&manifest["campaign_id"], &Json::from(expected_campaign_id)) {
        return Err(closure());
    }
    if Some(int_of(&manifest["captured_revision"])?) != authority.campaign_revision {
        return Err(closure());
    }
    let counts = Json::Object(
        authority
            .table_counts
            .iter()
            .map(|(table, count)| ((*table).to_owned(), Json::from(*count)))
            .collect(),
    );
    if !py_eq(&manifest["table_row_counts"], &counts)
        || !py_eq(&manifest["authority_record_count"], &Json::from(authority.record_count))
    {
        return Err(closure());
    }
    if !py_eq(&manifest["blob_count"], &Json::from(member_blobs.len())) {
        return Err(blob_closure());
    }
    if !py_eq(&manifest["closure_index"], &streamed_closure_index(&authority)) {
        return Err(closure());
    }
    Ok(manifest)
}
