//! `application/plan_bundle.py`: portable reusable Plans holding their complete Setting and
//! Ruleset snapshot (`.sylvia-gm-plan`).
//!
//! [`import_plan_bundle`] validates the whole Plan in a staged library before installing its
//! package snapshot and exposing the library entry; [`export_plan_bundle`] writes a validated
//! entry's files as a deterministic ZIP. Errors keep Python's classes: a member or record
//! problem is a `ValueError`, a damaged archive the `zipfile` class Python raises
//! (`BadZipFile`), and the snapshot installer's refusal passes through.

use crate::bundled_packages::BundledPackages;
use crate::plan_snapshot::{PLAN_SNAPSHOT_BYTE_LIMIT, PLAN_SNAPSHOT_FILENAME};
use crate::reusable_library_deletion::{validate_owned_files, validated_library_entry};
use crate::reusable_plans::{load_reusable_campaign_plan, ReusableCampaignPlan, LIBRARY_WRITE_LOCK};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use sylvia_base::hash::sha256_hex;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::campaign_plan::CAMPAIGN_PLAN_BYTE_LIMIT;
use sylvia_domain::content::validation::validate_content_shape;
use sylvia_domain::package_archive::zip_bounds::{
    preflight_zip_directory, validate_zip_compression, ZIP_DIRECTORY_BYTE_LIMIT,
};
use sylvia_domain::package_archive::zipfile::{ReadError, ZipArchive, ZipError};
use sylvia_domain::package_archive::zipfile_write::{ZipMember, ZipWriter};
use sylvia_domain::package_library::{parse_canonical_json_bytes, JsonShapeLimits};
use sylvia_domain::{DomainError, DomainResult};

/// `_MEMBER_LIMITS`: each member a bundle may hold, and its byte limit.
pub const MEMBER_LIMITS: [(&str, usize); 5] = [
    ("record.json", 64 * 1024),
    ("plan.json", CAMPAIGN_PLAN_BYTE_LIMIT),
    ("plan.md", CAMPAIGN_PLAN_BYTE_LIMIT),
    ("interview-contribution.json", 128 * 1024),
    (PLAN_SNAPSHOT_FILENAME, PLAN_SNAPSHOT_BYTE_LIMIT),
];

/// `PLAN_BUNDLE_BYTE_LIMIT`: the members' limits plus 64 KiB.
pub const PLAN_BUNDLE_BYTE_LIMIT: usize = 64 * 1024
    + 64 * 1024
    + CAMPAIGN_PLAN_BYTE_LIMIT
    + CAMPAIGN_PLAN_BYTE_LIMIT
    + 128 * 1024
    + PLAN_SNAPSHOT_BYTE_LIMIT;

/// The one member a bundle may leave out.
const OPTIONAL_MEMBER: &str = "interview-contribution.json";

/// A regular file's mode in a ZIP member's external attributes (`S_IFREG`).
const REGULAR_FILE: u32 = 0o100000;

fn member_limit(name: &str) -> Option<usize> {
    MEMBER_LIMITS.iter().find(|(member, _)| *member == name).map(|(_, limit)| *limit)
}

/// A `zipfile` failure as the exception Python raises (a `UnicodeDecodeError` is a `ValueError`).
pub(crate) fn zip_error(error: ZipError) -> DomainError {
    match error {
        ZipError::UnicodeDecode(message) => DomainError::value(message),
        other => DomainError::uncaught(other.python_class(), other.message()),
    }
}

/// `bundle.open(info).read(maximum + 1)`: a member's bytes, stopping after `maximum + 1`.
pub(crate) fn read_member(
    archive: &ZipArchive<'_>,
    info: &sylvia_domain::package_archive::zipfile::ZipInfo,
    maximum: usize,
) -> DomainResult<Vec<u8>> {
    let mut value = Vec::new();
    let result = archive.read_chunks(info, 64 * 1024, |chunk| {
        let room = (maximum + 1).saturating_sub(value.len());
        value.extend_from_slice(&chunk[..chunk.len().min(room)]);
        if value.len() > maximum {
            Err(())
        } else {
            Ok(())
        }
    });
    match result {
        Ok(()) | Err(ReadError::Caller(())) => Ok(value),
        Err(ReadError::Zip(error)) => Err(zip_error(error)),
    }
}

/// `_read_bundle(source)`: a closed set of bounded regular files, never extracted.
pub fn read_bundle(data: &[u8]) -> DomainResult<(String, BTreeMap<String, Vec<u8>>)> {
    if data.len() > PLAN_BUNDLE_BYTE_LIMIT {
        return Err(DomainError::value("Campaign Plan archive is too large"));
    }
    preflight_zip_directory(data, MEMBER_LIMITS.len() as u64, ZIP_DIRECTORY_BYTE_LIMIT)?;
    let archive = ZipArchive::open(data).map_err(zip_error)?;
    let names: Vec<&str> = archive.infos().iter().map(|info| info.filename.as_str()).collect();
    let mut unique = names.clone();
    unique.sort_unstable();
    unique.dedup();
    let complete = MEMBER_LIMITS.iter().all(|(name, _)| *name == OPTIONAL_MEMBER || names.contains(name));
    if unique.len() != names.len() || !complete || names.iter().any(|name| member_limit(name).is_none()) {
        return Err(DomainError::value("Campaign Plan archive members are invalid"));
    }
    let mut files = BTreeMap::new();
    for info in archive.infos() {
        validate_zip_compression(info)?;
        let maximum = member_limit(&info.filename).unwrap_or_default();
        let file_type = (info.external_attr >> 16) & 0o170000;
        if info.is_dir()
            || info.flag_bits & 1 != 0
            || info.file_size > maximum as u64
            || !matches!(file_type, 0 | REGULAR_FILE)
        {
            return Err(DomainError::value("Campaign Plan archive member is unsafe or too large"));
        }
        let value = read_member(&archive, info, maximum)?;
        if value.len() as u64 != info.file_size || value.len() > maximum {
            return Err(DomainError::value("Campaign Plan archive member size is invalid"));
        }
        files.insert(info.filename.clone(), value);
    }
    let record =
        parse_canonical_json_bytes(&files["record.json"], "Package manifest", JsonShapeLimits::NONE)?;
    validate_content_shape("portable_plan_bundle_record", &record, Some("record.json"))?;
    let identity = record.get("artifact_id").and_then(Json::as_str).filter(|id| is_reusable_plan_id(id));
    // `record.get("format_version") != 3`: Python's `==`, so `3.0` is the same version.
    let version_ok = record
        .get("format_version")
        .is_some_and(|version| sylvia_domain::pyops::py_eq(version, &Json::from(3)));
    let (Some(identity), true) = (identity, version_ok) else {
        return Err(DomainError::value("Campaign Plan archive record is invalid"));
    };
    Ok((identity.to_owned(), files))
}

/// `re.fullmatch(r"campaign-plan-reusable-[0-9a-f]{24}-[0-9a-f]{16}", text)`.
pub fn is_reusable_plan_id(text: &str) -> bool {
    let hex = |part: &str, length: usize| {
        part.len() == length && part.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
    };
    text.strip_prefix("campaign-plan-reusable-")
        .and_then(|rest| rest.split_once('-'))
        .is_some_and(|(operation, digest)| hex(operation, 24) && hex(digest, 16))
}

/// `tempfile.TemporaryDirectory(prefix=prefix, dir=parent)`: removed when dropped.
pub(crate) struct TemporaryDirectory {
    pub path: PathBuf,
}

impl TemporaryDirectory {
    pub(crate) fn new(parent: &Path, prefix: &str) -> DomainResult<Self> {
        let mut seed = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|elapsed| elapsed.as_nanos() as u64)
            .unwrap_or_default()
            ^ (u64::from(std::process::id()) << 32);
        const CHARACTERS: &[u8] = b"abcdefghijklmnopqrstuvwxyz0123456789_";
        for _ in 0..100 {
            let name: String = (0..8)
                .map(|_| {
                    seed =
                        seed.wrapping_mul(6_364_136_223_846_793_005).wrapping_add(1_442_695_040_888_963_407);
                    CHARACTERS[(seed >> 33) as usize % CHARACTERS.len()] as char
                })
                .collect();
            let path = parent.join(format!("{prefix}{name}"));
            match std::fs::create_dir(&path) {
                Ok(()) => return Ok(Self { path }),
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(error) => return Err(crate::workspace_files::os_error(&error, &path)),
            }
        }
        Err(DomainError::uncaught("FileExistsError", "No usable temporary directory name found"))
    }
}

impl Drop for TemporaryDirectory {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.path);
    }
}

/// `install_snapshot(snapshot, expected_closure)`: install the Plan's inherited packages.
pub type SnapshotInstaller<'a> = &'a mut dyn FnMut(&[u8], &str) -> DomainResult<()>;

/// `import_plan_bundle(storage_root, source, install_snapshot=...)`: validate the whole Plan before
/// importing its packages and exposing its library entry. A repeated import of the same Plan
/// installs its snapshot again (idempotently, through `install_snapshot`) and keeps the entry.
pub fn import_plan_bundle(
    storage_root: &Path,
    data: &[u8],
    install_snapshot: SnapshotInstaller<'_>,
    bundled: &BundledPackages,
) -> DomainResult<ReusableCampaignPlan> {
    let (artifact_id, files) = read_bundle(data)?;
    std::fs::create_dir_all(storage_root)
        .map_err(|error| crate::workspace_files::os_error(&error, storage_root))?;
    let _guard = LIBRARY_WRITE_LOCK.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    let temporary = TemporaryDirectory::new(storage_root, ".plan-import-")?;
    let staged = temporary.path.join("campaign-plans").join(&artifact_id);
    std::fs::create_dir_all(&staged).map_err(|error| crate::workspace_files::os_error(&error, &staged))?;
    for (name, value) in &files {
        let path = staged.join(name);
        std::fs::write(&path, value).map_err(|error| crate::workspace_files::os_error(&error, &path))?;
    }
    let validated = load_reusable_campaign_plan(&temporary.path, &artifact_id, None, bundled)?;
    let Some(snapshot) = &validated.package_snapshot else {
        return Err(DomainError::value("Campaign Plan archive has no package snapshot"));
    };
    let existing = validated_library_entry(storage_root, "campaign-plans", &artifact_id)?;
    if existing.is_some() {
        let current = load_reusable_campaign_plan(storage_root, &artifact_id, None, bundled)?;
        if current.content_sha256 != validated.content_sha256 {
            return Err(DomainError::value("Campaign Plan archive conflicts with an existing entry"));
        }
    }
    let closure = validated
        .plan
        .payload
        .get("bindings")
        .and_then(|bindings| bindings.get("package_closure_sha256"))
        .map(sylvia_domain::pyops::py_str)
        .unwrap_or_default();
    install_snapshot(snapshot, &closure)?;
    if existing.is_none() {
        let destination = storage_root.join("campaign-plans").join(&artifact_id);
        if let Some(parent) = destination.parent() {
            std::fs::create_dir_all(parent)
                .map_err(|error| crate::workspace_files::os_error(&error, parent))?;
        }
        std::fs::rename(&staged, &destination)
            .map_err(|error| crate::workspace_files::os_error(&error, &staged))?;
    }
    load_reusable_campaign_plan(storage_root, &artifact_id, None, bundled)
}

/// `export_plan_bundle(storage_root, artifact_id, expected_content_sha256=..., target=...)`:
/// the validated entry's files only, never Builder chat or campaign history. Returns the
/// archive and its receipt.
pub fn export_plan_bundle(
    storage_root: &Path,
    artifact_id: &str,
    expected_content_sha256: &str,
    bundled: &BundledPackages,
) -> DomainResult<(Vec<u8>, JsonMap)> {
    let _guard = LIBRARY_WRITE_LOCK.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    let Some(root) = validated_library_entry(storage_root, "campaign-plans", artifact_id)? else {
        return Err(DomainError::value("The selected Campaign Plan is unavailable"));
    };
    let owned: Vec<&str> = MEMBER_LIMITS.iter().map(|(name, _)| *name).collect();
    validate_owned_files(&root, &owned)?;
    let plan = load_reusable_campaign_plan(storage_root, artifact_id, None, bundled)?;
    if plan.content_sha256 != expected_content_sha256 {
        return Err(DomainError::value("The selected Campaign Plan changed; refresh its library entry"));
    }
    if plan.package_snapshot.is_none() {
        return Err(DomainError::value(
            "The Campaign Plan needs its retained package snapshot before export",
        ));
    }
    let mut names = owned;
    names.sort_unstable();
    let mut writer = ZipWriter::new();
    for name in names {
        let path = root.join(name);
        if !path.is_file() {
            continue;
        }
        let data = std::fs::read(&path).map_err(|error| crate::workspace_files::os_error(&error, &path))?;
        let member = ZipMember {
            filename: name.to_owned(),
            date_time: (1980, 1, 1, 0, 0, 0),
            compress_type: 0,
            create_system: 3,
            external_attr: 0o100644 << 16,
        };
        writer.writestr(member, &data)?;
    }
    let data = writer.finish();
    let mut receipt = JsonMap::new();
    receipt.insert("artifact_id".into(), plan.artifact_id.as_str().into());
    receipt.insert("content_sha256".into(), plan.content_sha256.as_str().into());
    receipt.insert("byte_count".into(), data.len().into());
    receipt.insert("archive_sha256".into(), sha256_hex(&data).into());
    Ok((data, receipt))
}
