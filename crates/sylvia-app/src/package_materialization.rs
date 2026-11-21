//! `application/package_materialization.py`: deterministically materialize normalized package
//! drafts through one admitted seam.
//!
//! The archive is written with CPython `zipfile`'s exact bytes
//! (`sylvia_domain::package_archive::zipfile_write`): its SHA-256 identifies bundled installs
//! and is stored with every staged package.

use serde_json::json;
use std::collections::BTreeSet;
use sylvia_base::hash::sha256_hex;
use sylvia_base::pystr;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::PackageLock;
use sylvia_domain::error::{DomainError, DomainResult};
use sylvia_domain::package_archive::admit_package_archive;
use sylvia_domain::package_archive::zipfile::ZIP_DEFLATED;
use sylvia_domain::package_archive::zipfile_write::{ZipMember, ZipWriter};
use sylvia_domain::package_library::{
    canonical_json_bytes, closure_sha256, content_root_for_record, manifest_from_record,
    resolve_exact_closure, validate_portable_path, PackageManifest,
};
use sylvia_domain::pyops::{py_eq, py_iter, py_truthy};

/// A draft's payload files, `(portable path, bytes)` in order.
pub type Payloads = Vec<(String, Vec<u8>)>;

/// `_ZIP_TIMESTAMP`.
const ZIP_TIMESTAMP: (u16, u16, u16, u16, u16, u16) = (1980, 1, 1, 0, 0, 0);

/// `NormalizedPackageDraft`: one draft record and its already-ingested, portable payload files.
#[derive(Debug, Clone, PartialEq)]
pub struct NormalizedPackageDraft {
    pub record: JsonMap,
    /// `payloads`, in the draft's order.
    pub payloads: Payloads,
    pub format_version: i64,
}

impl NormalizedPackageDraft {
    /// `NormalizedPackageDraft(record, payloads)` with Python's default `format_version=3`.
    pub fn new(record: JsonMap, payloads: Payloads, format_version: i64) -> Self {
        Self { record, payloads, format_version }
    }
}

/// `MaterializedPackageArchive`: one deterministic, pre-admitted archive.
#[derive(Debug, Clone, PartialEq)]
pub struct MaterializedPackageArchive {
    pub archive: Vec<u8>,
    pub manifests: Vec<PackageManifest>,
    pub package_lock: PackageLock,
    pub closure_sha256: String,
}

impl MaterializedPackageArchive {
    /// `manifest`: the only manifest when the caller supplied one package draft.
    pub fn manifest(&self) -> DomainResult<&PackageManifest> {
        match self.manifests.as_slice() {
            [only] => Ok(only),
            _ => Err(DomainError::value("A multi-package archive has no singular manifest")),
        }
    }
}

fn value(message: &str) -> DomainError {
    DomainError::value(message)
}

/// `snapshot_record(record, primary=..., settings=...)`: remove derived claims and normalize
/// legacy ownership for an editable owned copy.
pub fn snapshot_record(record: &JsonMap, primary: &PackageManifest, settings: &[PackageManifest]) -> JsonMap {
    let strip_hash = |dependency: &Json| -> Json {
        Json::Object(
            dependency
                .as_object()
                .into_iter()
                .flatten()
                .filter(|(key, _)| key.as_str() != "content_sha256")
                .map(|(key, value)| (key.clone(), value.clone()))
                .collect(),
        )
    };
    let mut result = record.clone();
    result.shift_remove("content_sha256");
    result.shift_remove("files");
    if record.contains_key("content_kind") {
        let dependencies: Vec<Json> =
            record["dependencies"].as_array().into_iter().flatten().map(strip_hash).collect();
        result.insert("dependencies".into(), Json::Array(dependencies));
        return result;
    }
    let identity = result["id"].as_str().unwrap_or_default().to_owned();
    let setting_ids: Vec<&str> = settings.iter().map(|item| item.package_id.as_str()).collect();
    let kind = if identity == primary.package_id {
        "ruleset"
    } else if setting_ids.contains(&identity.as_str()) {
        "campaign_setting"
    } else {
        "support"
    };
    result.insert("content_kind".into(), Json::from(kind));
    let mut dependencies: Vec<Json> = result["dependencies"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|dependency| !dependency["id"].as_str().is_some_and(|id| setting_ids.contains(&id)))
        .map(strip_hash)
        .collect();
    if setting_ids.contains(&identity.as_str()) {
        dependencies.retain(|dependency| !py_eq(&dependency["id"], &Json::from(primary.package_id.as_str())));
        dependencies.push(json!({
            "namespace": "ruleset_snapshot",
            "kind": "ruleset_snapshot",
            "id": primary.package_id,
            "version": primary.version,
        }));
    }
    result.insert("dependencies".into(), Json::Array(dependencies));
    result
}

/// `participant_schema_sha256(...)` on the draft's own values, as Python passes them.
fn participant_schema_hash(raw: &JsonMap, schema: &Json) -> DomainResult<String> {
    let entity_state_schema = raw.get("entity_state_schema").filter(|value| !value.is_null());
    let empty = Json::Array(Vec::new());
    let calculations = raw.get("initial_state_calculations").unwrap_or(&empty);
    let validation = raw.get("initial_state_validation").filter(|value| !value.is_null());
    let payload = match entity_state_schema {
        None => {
            if py_truthy(calculations) || validation.is_some() {
                return Err(value("Character calculations require a complete entity-state schema"));
            }
            schema.clone()
        }
        Some(entity_state_schema) => {
            let mut payload = JsonMap::new();
            payload.insert("schema".into(), schema.clone());
            payload.insert("entity_state_schema".into(), entity_state_schema.clone());
            payload.insert("initial_state_calculations".into(), Json::Array(py_iter(calculations)?));
            if let Some(validation) = validation {
                payload.insert("initial_state_validation".into(), validation.clone());
            }
            Json::Object(payload)
        }
    };
    Ok(sha256_hex(canonical_json_bytes(&payload)))
}

/// `_participant_artifact_hashes(record)`: derive the identity of every declared participant and
/// starting-state requirement.
fn participant_artifact_hashes(record: &mut JsonMap) -> DomainResult<()> {
    let raw_artifacts = match record.get("participant_artifacts") {
        None | Some(Json::Null) => return Ok(()),
        Some(Json::Array(items)) => items.clone(),
        Some(_) => return Err(value("Package participant artifacts must be an array")),
    };
    let mut projected = Vec::with_capacity(raw_artifacts.len());
    for raw in raw_artifacts {
        let Json::Object(raw) = raw else {
            return Err(value("Package participant artifact schema is invalid"));
        };
        let Some(schema @ Json::Object(_)) = raw.get("schema") else {
            return Err(value("Package participant artifact schema is invalid"));
        };
        let expected = participant_schema_hash(&raw, schema)?;
        if raw
            .get("schema_sha256")
            .is_some_and(|supplied| !supplied.is_null() && !py_eq(supplied, &Json::from(expected.as_str())))
        {
            return Err(value("Package participant artifact schema hash is invalid"));
        }
        let mut item = raw.clone();
        item.insert("schema_sha256".into(), Json::String(expected));
        projected.push(Json::Object(item));
    }
    record.insert("participant_artifacts".into(), Json::Array(projected));
    Ok(())
}

/// `_zip_member(name, value)`: one deterministic regular-file ZIP member.
fn zip_member(name: String) -> ZipMember {
    ZipMember {
        filename: name,
        date_time: ZIP_TIMESTAMP,
        compress_type: ZIP_DEFLATED,
        create_system: 3,
        external_attr: 0o100644 << 16,
    }
}

struct Completing<'a> {
    records: &'a mut [(JsonMap, Payloads)],
    completing: BTreeSet<String>,
    completed: BTreeSet<String>,
}

impl Completing<'_> {
    fn position(&self, package_id: &str) -> Option<usize> {
        // `{record["id"]: record for record, _ in completed}`: the last with an id wins, but
        // identities are unique here.
        self.records.iter().rposition(|(record, _)| record["id"].as_str() == Some(package_id))
    }

    /// `complete_record(package_id)`: resolve local exact dependencies before hashing.
    fn complete(&mut self, package_id: &str) -> DomainResult<JsonMap> {
        let at = self.position(package_id).expect("a local draft");
        if self.completed.contains(package_id) {
            return Ok(self.records[at].0.clone());
        }
        if !self.completing.insert(package_id.to_owned()) {
            return Err(value("Package draft dependencies contain a cycle"));
        }
        let dependencies = self.records[at].0.get("dependencies").cloned().unwrap_or_else(|| json!([]));
        let Json::Array(dependencies) = dependencies else {
            return Err(value("Package dependencies must be an array"));
        };
        let mut updated = Vec::with_capacity(dependencies.len());
        for dependency in dependencies {
            let Json::Object(mut dependency) = dependency else {
                return Err(value("Package dependency is invalid"));
            };
            let Some(Json::String(dependency_id)) = dependency.get("id").cloned() else {
                return Err(value("Package dependency identity is invalid"));
            };
            if self.position(&dependency_id).is_some() {
                let dependency_record = self.complete(&dependency_id)?;
                let null = Json::Null;
                if !py_eq(
                    dependency.get("version").unwrap_or(&null),
                    dependency_record.get("version").unwrap_or(&null),
                ) {
                    return Err(value("Local package dependency version does not match its draft"));
                }
                let expected = dependency_record["content_sha256"].clone();
                if dependency
                    .get("content_sha256")
                    .is_some_and(|supplied| !supplied.is_null() && !py_eq(supplied, &expected))
                {
                    return Err(value("Local package dependency hash does not match its draft"));
                }
                dependency.insert("content_sha256".into(), expected);
            }
            updated.push(Json::Object(dependency));
        }
        let record = &mut self.records[at].0;
        if record.contains_key("dependencies") {
            record.insert("dependencies".into(), Json::Array(updated));
        }
        let root = content_root_for_record(record);
        record.insert("content_sha256".into(), Json::String(root));
        self.completing.remove(package_id);
        self.completed.insert(package_id.to_owned());
        Ok(record.clone())
    }
}

/// `materialize_package_archive(drafts)`: complete, archive, and pre-admit one normalized exact
/// package closure.
pub fn materialize_package_archive(
    drafts: &[NormalizedPackageDraft],
) -> DomainResult<MaterializedPackageArchive> {
    if drafts.is_empty() {
        return Err(value("Package materialization requires at least one draft"));
    }
    let versions: BTreeSet<i64> = drafts.iter().map(|draft| draft.format_version).collect();
    if versions.len() != 1 || !versions.iter().all(|version| (1..=4).contains(version)) {
        return Err(value("Package drafts require one supported archive format"));
    }
    let format_version = *versions.iter().next().expect("one version");
    let mut completed: Vec<(JsonMap, Payloads)> = Vec::with_capacity(drafts.len());
    let mut package_ids: BTreeSet<String> = BTreeSet::new();
    for draft in drafts {
        let mut record = draft.record.clone();
        let package_id = match record.get("id") {
            Some(Json::String(id)) if !id.is_empty() && !package_ids.contains(id) => id.clone(),
            _ => return Err(value("Package materialization requires unique package identities")),
        };
        package_ids.insert(package_id);
        let mut sorted: Vec<&(String, Vec<u8>)> = draft.payloads.iter().collect();
        sorted.sort_by(|a, b| a.0.cmp(&b.0));
        let mut payloads: Payloads = Vec::with_capacity(sorted.len());
        let mut folded_paths: BTreeSet<String> = BTreeSet::new();
        for (path, data) in sorted {
            validate_portable_path(path)?;
            if !folded_paths.insert(pystr::casefold(path)) {
                return Err(value("Package payload files are invalid"));
            }
            payloads.push((path.clone(), data.clone()));
        }
        if payloads.is_empty() {
            return Err(value("Package payload files are empty"));
        }
        participant_artifact_hashes(&mut record)?;
        let files: Vec<Json> = payloads
            .iter()
            .map(|(path, data)| json!({"path": path, "bytes": data.len(), "sha256": sha256_hex(data)}))
            .collect();
        record.insert("files".into(), Json::Array(files));
        completed.push((record, payloads));
    }
    let ids: Vec<String> =
        completed.iter().map(|(record, _)| record["id"].as_str().unwrap_or_default().to_owned()).collect();
    let mut completing =
        Completing { records: &mut completed, completing: BTreeSet::new(), completed: BTreeSet::new() };
    for id in &ids {
        completing.complete(id)?;
    }
    let mut manifests = Vec::with_capacity(completed.len());
    for (record, _) in &completed {
        manifests.push(manifest_from_record(&Json::Object(record.clone()), Some(format_version))?);
    }
    let package_lock = resolve_exact_closure(&manifests)?;
    let order = |id: &str| {
        package_lock.entries().iter().find(|entry| entry.package_id() == id).map(|entry| entry.load_order())
    };
    manifests.sort_by_key(|manifest| order(&manifest.package_id));
    completed.sort_by_key(|(record, _)| order(record["id"].as_str().unwrap_or_default()));
    let closure = closure_sha256(&package_lock);
    let records: Vec<Json> = completed.iter().map(|(record, _)| Json::Object(record.clone())).collect();
    let manifest_bytes = canonical_json_bytes(&json!({"format": format_version, "packages": records}));
    let mut writer = ZipWriter::new();
    writer.writestr(zip_member("manifest.json".into()), &manifest_bytes)?;
    for (record, payloads) in &completed {
        let package_id = record["id"].as_str().unwrap_or_default();
        for (path, data) in payloads {
            writer.writestr(zip_member(format!("packages/{package_id}/{path}")), data)?;
        }
    }
    let archive = writer.finish();
    let admitted = admit_package_archive(&archive)?;
    if admitted.package_lock != package_lock {
        return Err(value("Materialized package lock changed during pre-admission"));
    }
    Ok(MaterializedPackageArchive { archive, manifests, package_lock, closure_sha256: closure })
}
