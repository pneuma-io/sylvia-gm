//! `domain/package_library.py`: package, closure, catalog, activation and reference contracts.
//!
//! It holds the identities every other lane builds on (canonical bytes and the hashes over
//! them, exact package files and dependencies, portable paths, the strict JSON readers, and the
//! candidate, lock-record and activation records), then manifests ([`manifest_from_record`]),
//! content declarations, gameplay support, Campaign Setting knowledge and exact closures.

mod declarations;
mod graph;
mod manifest;
mod record;
mod strict_json;

pub use declarations::*;
pub use graph::{
    campaign_setting_entries, manifest_index, resolve_exact_closure, resolve_package_graph, PackageGraphNode,
    PackageIdentity,
};

pub use manifest::{
    manifest_from_record, PackageManifest, ParticipantArtifactDeclaration, StatePredicate,
    SurfaceDeclaration, ToolDeclaration, ENGINE_FEATURE_PANEL_WORKSPACES, ENGINE_FEATURE_SHOT_EFFECTS,
    ENGINE_FEATURE_SPATIAL_CLEARANCE, ENGINE_FEATURE_SPATIAL_MUTATION,
    ENGINE_FEATURE_SPATIAL_PATH_MOVING_ENTITY, ENGINE_FEATURE_TACTICAL_GRID, NETWORK_UI_DYNAMIC_SELECTIONS,
    PACKAGE_HOST_CONTRACT, PACKAGE_RUNTIME_FORMAT, SUPPORTED_ENGINE_FEATURES, TACTICAL_UI_DYNAMIC_SELECTIONS,
    UI_DYNAMIC_SELECTIONS,
};

pub use strict_json::{
    parse_canonical_json_bytes, parse_content_json_bytes, preflight_json_shape, JsonShapeLimits,
    DEFAULT_LABEL,
};

use crate::authority::{
    str_enum, validate_authority_identity, validate_authority_identity_value, PackageLock,
};
use crate::error::{DomainError, DomainResult};
use sylvia_base::hash::sha256_hex;
use sylvia_base::{pyjson, pystr, Json, JsonMap};

str_enum! {
    /// Package candidate lifecycle.
    CandidateStatus {
        Staging = "staging",
        Validated = "validated",
        Activated = "activated",
        Failed = "failed",
    }
}

str_enum! {
    /// Whether a stored campaign lock can still be loaded.
    PackageLockAvailability {
        Available = "available",
        LegacyUnavailable = "legacy_unavailable",
    }
}

str_enum! {
    /// Terminal activation outcomes.
    ActivationStatus {
        Committed = "committed",
        Failed = "failed",
    }
}

/// `canonical_json_bytes(value)`: the exact F3 canonical bytes (sorted keys, `,`/`:`
/// separators, UTF-8, no NaN).
///
/// Python first runs `thaw_json_value`, which refuses non-finite floats and non-JSON leaves and
/// turns mapping keys into `str`. A [`Json`] cannot hold any of those, so nothing is left to
/// refuse and the encoding is `sylvia_base::pyjson::canonical_bytes`.
pub fn canonical_json_bytes(value: &Json) -> Vec<u8> {
    pyjson::canonical_bytes(value)
}

/// [`canonical_json_bytes`] of an object.
pub fn canonical_object_bytes(value: &JsonMap) -> Vec<u8> {
    // Encoding a borrowed map needs an owned `Json::Object`; the clone is the price of
    // `serde_json::Value` owning its children.
    canonical_json_bytes(&Json::Object(value.clone()))
}

/// `participant_schema_sha256(schema, entity_state_schema=..., initial_state_calculations=...,
/// initial_state_validation=...)`: bind the complete creation contract while preserving older
/// participant-only identities.
pub fn participant_schema_sha256(
    schema: &JsonMap,
    entity_state_schema: Option<&JsonMap>,
    initial_state_calculations: &[Json],
    initial_state_validation: Option<&JsonMap>,
) -> DomainResult<String> {
    let payload = match entity_state_schema {
        None => {
            if !initial_state_calculations.is_empty() || initial_state_validation.is_some() {
                return Err(DomainError::value(
                    "Character calculations require a complete entity-state schema",
                ));
            }
            Json::Object(schema.clone())
        }
        Some(entity_state_schema) => {
            let mut payload = JsonMap::new();
            payload.insert("schema".into(), Json::Object(schema.clone()));
            payload.insert("entity_state_schema".into(), Json::Object(entity_state_schema.clone()));
            payload.insert(
                "initial_state_calculations".into(),
                Json::Array(initial_state_calculations.to_vec()),
            );
            if let Some(validation) = initial_state_validation {
                payload.insert("initial_state_validation".into(), Json::Object(validation.clone()));
            }
            Json::Object(payload)
        }
    };
    Ok(sha256_hex(canonical_json_bytes(&payload)))
}

/// `content_root_for_record(record)`: hash the canonical package record with its
/// `content_sha256` claim left out.
pub fn content_root_for_record(record: &JsonMap) -> String {
    let without_claim: JsonMap = record
        .iter()
        .filter(|(key, _)| key.as_str() != "content_sha256")
        .map(|(k, v)| (k.clone(), v.clone()))
        .collect();
    sha256_hex(canonical_json_bytes(&Json::Object(without_claim)))
}

/// `closure_sha256(package_lock)`: the identity of an exact closure, over each member's id,
/// version, content hash, role and load order.
pub fn closure_sha256(package_lock: &PackageLock) -> String {
    let membership: Vec<Json> = package_lock
        .entries()
        .iter()
        .map(|entry| {
            let mut member = JsonMap::new();
            member.insert("id".into(), Json::from(entry.package_id()));
            member.insert("version".into(), Json::from(entry.version()));
            member.insert("content_sha256".into(), Json::from(entry.sha256()));
            member.insert("role".into(), Json::from(entry.role().as_str()));
            member.insert("load_order".into(), Json::from(entry.load_order()));
            Json::Object(member)
        })
        .collect();
    sha256_hex(canonical_json_bytes(&Json::Array(membership)))
}

/// One file of a package archive (`PackageFile`).
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct PackageFile {
    path: String,
    byte_count: i64,
    sha256: String,
}

impl PackageFile {
    /// `PackageFile(path, byte_count, sha256)`.
    pub fn new(path: impl Into<String>, byte_count: i64, sha256: impl Into<String>) -> DomainResult<Self> {
        let file = Self { path: path.into(), byte_count, sha256: sha256.into() };
        validate_portable_path(&file.path)?;
        if file.byte_count < 0 {
            return Err(DomainError::value("Package file byte count cannot be negative"));
        }
        validate_authority_identity(&file.sha256, "Package file SHA-256", true)?;
        Ok(file)
    }
    /// `PackageFile(path, byte_count, sha256)` from record values of any JSON type, checked in
    /// Python's order: the path, then the byte count, then the SHA-256.
    ///
    /// A byte count beyond `i64` is refused here. Python accepts it at this point and refuses
    /// the archive later, when the member's real size is compared.
    pub fn from_record_values(path: &Json, byte_count: i128, sha256: &Json) -> DomainResult<Self> {
        let path = validate_portable_path_value(path)?;
        if byte_count < 0 {
            return Err(DomainError::value("Package file byte count cannot be negative"));
        }
        let byte_count = i64::try_from(byte_count)
            .map_err(|_| DomainError::value("Package file byte count is too large"))?;
        let sha256 = validate_authority_identity_value(sha256, "Package file SHA-256", true)?;
        Self::new(path, byte_count, sha256)
    }

    pub fn path(&self) -> &str {
        &self.path
    }
    pub fn byte_count(&self) -> i64 {
        self.byte_count
    }
    pub fn sha256(&self) -> &str {
        &self.sha256
    }
}

/// One exact dependency declared by a package manifest (`ExactPackageDependency`).
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct ExactPackageDependency {
    namespace: String,
    kind: String,
    package_id: String,
    version: String,
    content_sha256: String,
}

impl ExactPackageDependency {
    /// `ExactPackageDependency(namespace, kind, package_id, version, content_sha256)`.
    pub fn new(
        namespace: impl Into<String>,
        kind: impl Into<String>,
        package_id: impl Into<String>,
        version: impl Into<String>,
        content_sha256: impl Into<String>,
    ) -> DomainResult<Self> {
        let value = Self {
            namespace: namespace.into(),
            kind: kind.into(),
            package_id: package_id.into(),
            version: version.into(),
            content_sha256: content_sha256.into(),
        };
        validate_authority_identity(&value.namespace, "Dependency namespace", false)?;
        validate_authority_identity(&value.kind, "Dependency kind", false)?;
        validate_authority_identity(&value.package_id, "Dependency package identity", false)?;
        validate_authority_identity(&value.version, "Dependency version", false)?;
        validate_authority_identity(&value.content_sha256, "Dependency content SHA-256", true)?;
        Ok(value)
    }
    pub fn namespace(&self) -> &str {
        &self.namespace
    }
    pub fn kind(&self) -> &str {
        &self.kind
    }
    pub fn package_id(&self) -> &str {
        &self.package_id
    }
    pub fn version(&self) -> &str {
        &self.version
    }
    pub fn content_sha256(&self) -> &str {
        &self.content_sha256
    }
    /// `(package_id, version, content_sha256)`.
    pub fn identity(&self) -> (&str, &str, &str) {
        (&self.package_id, &self.version, &self.content_sha256)
    }
}

/// The names `PureWindowsPath.is_reserved()` refuses in CPython 3.12.
const WINDOWS_RESERVED_NAMES: &[&str] = &[
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "CONIN$",
    "CONOUT$",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "COM\u{b9}",
    "COM\u{b2}",
    "COM\u{b3}",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
    "LPT\u{b9}",
    "LPT\u{b2}",
    "LPT\u{b3}",
];

/// `PureWindowsPath(piece).is_reserved()` for one path piece that has already passed the
/// separator, drive and character checks of [`validate_portable_path`].
fn windows_reserved_piece(piece: &str) -> bool {
    let name = piece.split('.').next().unwrap_or_default();
    let name = name.split(':').next().unwrap_or_default().trim_end_matches(' ');
    WINDOWS_RESERVED_NAMES.contains(&pystr::upper(name).as_str())
}

/// `validate_portable_path(path)`: ordinary relative file names, also on Windows.
pub fn validate_portable_path(path: &str) -> DomainResult<&str> {
    if path.is_empty() {
        return Err(DomainError::value("Portable package path must be nonempty text"));
    }
    if path.contains(['\0', '\\']) || path.starts_with('/') {
        return Err(DomainError::value("Portable package path is unsafe"));
    }
    let mut characters = path.chars();
    if characters.next().is_some_and(|c| c.is_ascii_alphabetic()) && characters.next() == Some(':') {
        return Err(DomainError::value("Portable package path cannot contain a drive"));
    }
    let pieces: Vec<&str> = path.split('/').collect();
    if pieces.iter().any(|piece| matches!(*piece, "" | "." | "..")) {
        return Err(DomainError::value("Portable package path contains an alias"));
    }
    if pieces.iter().any(|piece| *piece != pystr::strip(piece)) {
        return Err(DomainError::value("Portable package path contains ambiguous whitespace"));
    }
    if path.chars().any(|c| (c as u32) < 32 || "<>:\"|?*".contains(c)) {
        return Err(DomainError::value("Portable package path contains non-portable characters"));
    }
    if pieces.iter().any(|piece| piece.ends_with('.') || windows_reserved_piece(piece)) {
        return Err(DomainError::value("Portable package path contains a reserved name or alias"));
    }
    Ok(path)
}

/// `validate_portable_path(path)` for any JSON value: a non-string is not text.
pub fn validate_portable_path_value(path: &Json) -> DomainResult<&str> {
    match path {
        Json::String(text) => validate_portable_path(text),
        _ => Err(DomainError::value("Portable package path must be nonempty text")),
    }
}

/// One validated install candidate (`PackageCandidate`).
#[derive(Debug, Clone, PartialEq)]
pub struct PackageCandidate {
    candidate_id: String,
    install_id: String,
    status: CandidateStatus,
    closure_sha256: String,
    package_lock: PackageLock,
    validation_report: JsonMap,
}

impl PackageCandidate {
    /// `PackageCandidate(...)`: the lock must be nonempty.
    pub fn new(
        candidate_id: impl Into<String>,
        install_id: impl Into<String>,
        status: CandidateStatus,
        closure_sha256: impl Into<String>,
        package_lock: PackageLock,
        validation_report: JsonMap,
    ) -> DomainResult<Self> {
        if package_lock.entries().is_empty() {
            return Err(DomainError::value("Package candidate lock must be nonempty"));
        }
        Ok(Self {
            candidate_id: candidate_id.into(),
            install_id: install_id.into(),
            status,
            closure_sha256: closure_sha256.into(),
            package_lock,
            validation_report,
        })
    }
    pub fn candidate_id(&self) -> &str {
        &self.candidate_id
    }
    pub fn install_id(&self) -> &str {
        &self.install_id
    }
    pub fn status(&self) -> CandidateStatus {
        self.status
    }
    pub fn closure_sha256(&self) -> &str {
        &self.closure_sha256
    }
    pub fn package_lock(&self) -> &PackageLock {
        &self.package_lock
    }
    pub fn validation_report(&self) -> &JsonMap {
        &self.validation_report
    }
}

/// One stored campaign lock.
#[derive(Debug, Clone, PartialEq)]
pub struct PackageLockRecord {
    pub lock_id: String,
    pub campaign_id: String,
    pub closure_sha256: String,
    pub package_lock: PackageLock,
    pub availability: PackageLockAvailability,
    pub predecessor_lock_id: Option<String>,
    pub created_revision: i64,
}

/// The prompt fragments and UI surfaces a lock exposes.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct PackageSurfaceProjection {
    pub prompt_fragment_ids: Vec<String>,
    pub ui_surface_ids: Vec<String>,
    pub ui_surfaces: Vec<JsonMap>,
}

/// A leased read of one stored package blob.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PackageBlobReadReceipt {
    pub sha256: String,
    pub byte_count: i64,
    pub maximum_chunk_size: i64,
    pub lease_id: String,
}

/// One package staging upload.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PackageStagingRecord {
    pub install_id: String,
    pub status: String,
    pub chunk_count: i64,
}

/// Counts from one package storage reclamation.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct ReclamationResult {
    pub deleted_blobs: i64,
    pub campaign_lock_roots: i64,
    pub candidate_roots: i64,
    pub activation_roots: i64,
    pub staging_roots: i64,
    pub read_lease_roots: i64,
    pub recovery_roots: i64,
}

/// A request to activate a validated candidate for a campaign (`ActivationCommand`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ActivationCommand {
    campaign_id: String,
    candidate_id: String,
    activation_id: String,
    expected_revision: i64,
    expected_lock_id: String,
    migration_id: String,
}

impl ActivationCommand {
    /// `ActivationCommand(...)` with Python's checks, in Python's order.
    pub fn new(
        campaign_id: impl Into<String>,
        candidate_id: impl Into<String>,
        activation_id: impl Into<String>,
        expected_revision: i64,
        expected_lock_id: impl Into<String>,
        migration_id: impl Into<String>,
    ) -> DomainResult<Self> {
        let command = Self {
            campaign_id: campaign_id.into(),
            candidate_id: candidate_id.into(),
            activation_id: activation_id.into(),
            expected_revision,
            expected_lock_id: expected_lock_id.into(),
            migration_id: migration_id.into(),
        };
        for (value, label) in [
            (&command.campaign_id, "Activation campaign identity"),
            (&command.candidate_id, "Activation candidate identity"),
            (&command.activation_id, "Activation identity"),
            (&command.expected_lock_id, "Activation expected lock identity"),
            (&command.migration_id, "Activation migration identity"),
        ] {
            validate_authority_identity(value, label, false)?;
        }
        if command.expected_revision < 0 {
            return Err(DomainError::value("Activation expected revision cannot be negative"));
        }
        Ok(command)
    }
    pub fn campaign_id(&self) -> &str {
        &self.campaign_id
    }
    pub fn candidate_id(&self) -> &str {
        &self.candidate_id
    }
    pub fn activation_id(&self) -> &str {
        &self.activation_id
    }
    pub fn expected_revision(&self) -> i64 {
        self.expected_revision
    }
    pub fn expected_lock_id(&self) -> &str {
        &self.expected_lock_id
    }
    pub fn migration_id(&self) -> &str {
        &self.migration_id
    }
}

/// The outcome of one activation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ActivationResult {
    pub campaign_id: String,
    pub activation_id: String,
    pub status: ActivationStatus,
    pub old_lock_id: String,
    pub new_lock_id: String,
    pub resulting_revision: i64,
    pub payload_sha256: String,
    /// Python's default is `false`.
    pub replayed: bool,
    /// Python's default is `"confirmed_commit"`.
    pub resolution: String,
}

/// `activation_payload_sha256(command)`: hash only the caller-owned request fields.
pub fn activation_payload_sha256(command: &ActivationCommand) -> String {
    let mut payload = JsonMap::new();
    payload.insert("campaign_id".into(), Json::from(command.campaign_id()));
    payload.insert("candidate_id".into(), Json::from(command.candidate_id()));
    payload.insert("expected_revision".into(), Json::from(command.expected_revision()));
    payload.insert("expected_lock_id".into(), Json::from(command.expected_lock_id()));
    payload.insert("migration_id".into(), Json::from(command.migration_id()));
    sha256_hex(canonical_json_bytes(&Json::Object(payload)))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reserved_names_follow_python() {
        for path in ["CON", "con.txt", "a/Nul", "com\u{b9}", "CON .md", "conın$", "lpt9.x.y"] {
            assert!(validate_portable_path(path).is_err(), "{path}");
        }
        for path in ["CONS", "a/console.txt", "com0", "x.con"] {
            assert!(validate_portable_path(path).is_ok(), "{path}");
        }
    }
}
