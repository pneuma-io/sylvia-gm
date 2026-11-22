//! `application/library_sources.py`: inspect exact installed package sources for an explicit
//! Builder update.

use crate::package_materialization::snapshot_record;
use sylvia_base::{pystr, Json, JsonMap};
use sylvia_domain::authority::{PackageLockEntry, PackageRole};
use sylvia_domain::error::DomainError;
use sylvia_domain::package_library::{
    campaign_setting_entries, canonical_json_bytes, CandidateStatus, PackageCandidate,
};

const MAX_CANDIDATES: usize = 100;
const MAX_PACKAGES: usize = 64;
const MAX_PATHS_PER_PACKAGE: i64 = 512;
const MAX_TOTAL_PATHS: usize = 2048;
const MAX_TEXT_BYTES: i64 = 8 * 1024 * 1024;

/// The owner reads `LibrarySourceReader` makes (Python's `AuthorityOwner`). Its own refusals
/// convert into the owner's error type, as the owner reports them.
pub trait LibraryOwner {
    type Error: From<DomainError>;

    /// `package_candidates()`, newest first.
    fn package_candidates(&self) -> Result<Vec<PackageCandidate>, Self::Error>;
    /// `package_candidate(candidate_id)`.
    fn package_candidate(&self, candidate_id: &str) -> Result<PackageCandidate, Self::Error>;
    /// `package_file_paths(package_id, version, sha256, maximum_files=...)`.
    fn package_file_paths(
        &self,
        package_id: &str,
        version: &str,
        sha256: &str,
        maximum_files: i64,
    ) -> Result<Vec<String>, Self::Error>;
    /// `package_text_file(package_id, version, sha256, path, maximum_bytes=...)`.
    fn package_text_file(
        &self,
        package_id: &str,
        version: &str,
        sha256: &str,
        path: &str,
        maximum_bytes: i64,
    ) -> Result<String, Self::Error>;
    /// `package_record(package_id, version, sha256)`.
    fn package_record(&self, package_id: &str, version: &str, sha256: &str) -> Result<JsonMap, Self::Error>;
    /// `package_label(package_id, version, sha256)`.
    fn package_label(&self, package_id: &str, version: &str, sha256: &str) -> Result<String, Self::Error>;
}

fn rejected<E: From<DomainError>>(message: &str) -> E {
    E::from(DomainError::command_rejected(message))
}

fn inspectable(candidate: &PackageCandidate) -> bool {
    matches!(candidate.status(), CandidateStatus::Validated | CandidateStatus::Activated)
}

/// `LibrarySourceReader(owner, ruleset_id, setting_id)`: read matching Ruleset sources and the
/// Builder's own Setting when present.
pub struct LibrarySourceReader<'a, O: LibraryOwner> {
    pub owner: &'a O,
    pub ruleset_id: String,
    pub setting_id: Option<String>,
}

impl<O: LibraryOwner> LibrarySourceReader<'_, O> {
    /// `candidates(query=...)`: matching candidates in owner order, without ranking versions.
    pub fn candidates(&self, query: &str) -> Result<Vec<JsonMap>, O::Error> {
        let folded = pystr::casefold(query);
        let mut results = Vec::new();
        for candidate in self.owner.package_candidates()? {
            if !inspectable(&candidate) || !self.matches_source(&candidate) {
                continue;
            }
            let summary = self.candidate_identity(&candidate)?;
            let packages = summary["packages"].as_array().cloned().unwrap_or_default();
            let mut parts = vec![candidate.candidate_id().to_owned(), candidate.closure_sha256().to_owned()];
            for field in ["id", "version", "label"] {
                parts.extend(
                    packages.iter().map(|package| package[field].as_str().unwrap_or_default().to_owned()),
                );
            }
            if pystr::casefold(&parts.join(" ")).contains(&folded) {
                results.push(summary);
            }
            if results.len() > MAX_CANDIDATES {
                return Err(rejected("Too many compatible library sources; narrow the query"));
            }
        }
        Ok(results)
    }

    /// `files(candidate_id)`: the portable paths of one concrete compatible candidate.
    pub fn files(&self, candidate_id: &str) -> Result<JsonMap, O::Error> {
        let candidate = self.candidate(candidate_id)?;
        let mut paths = Vec::new();
        for (prefix, entry) in self.package_index(&candidate)? {
            for path in self.owner.package_file_paths(
                entry.package_id(),
                entry.version(),
                entry.sha256(),
                MAX_PATHS_PER_PACKAGE,
            )? {
                paths.push(format!("{prefix}/{path}"));
                if paths.len() > MAX_TOTAL_PATHS {
                    return Err(rejected("Library source has too many files to inspect"));
                }
            }
        }
        paths.sort();
        let mut result = self.candidate_identity(&candidate)?;
        result.insert("paths".into(), Json::from(paths));
        Ok(result)
    }

    /// `read(candidate_id, path)`: one exact path `files()` exposed.
    pub fn read(&self, candidate_id: &str, path: &str) -> Result<JsonMap, O::Error> {
        let candidate = self.candidate(candidate_id)?;
        for (prefix, entry) in self.package_index(&candidate)? {
            let Some(portable_path) = path.strip_prefix(&format!("{prefix}/")) else { continue };
            let listed = self.owner.package_file_paths(
                entry.package_id(),
                entry.version(),
                entry.sha256(),
                MAX_PATHS_PER_PACKAGE,
            )?;
            if !listed.iter().any(|item| item == portable_path) {
                break;
            }
            let content = self.owner.package_text_file(
                entry.package_id(),
                entry.version(),
                entry.sha256(),
                portable_path,
                MAX_TEXT_BYTES,
            )?;
            return self.located(&candidate, &entry, &prefix, path, content);
        }
        Err(rejected("Library source path is unavailable"))
    }

    /// `record(candidate_id, prefix)`: editable package declarations, without derived hashes and
    /// file indexes.
    pub fn record(&self, candidate_id: &str, prefix: &str) -> Result<JsonMap, O::Error> {
        let candidate = self.candidate(candidate_id)?;
        let lock = candidate.package_lock();
        let primary = lock
            .entries()
            .iter()
            .find(|item| item.role() == PackageRole::Primary)
            .ok_or_else(|| O::Error::from(DomainError::uncaught("StopIteration", "")))?;
        for (package_prefix, entry) in self.package_index(&candidate)? {
            if package_prefix != prefix {
                continue;
            }
            let record = self.owner.package_record(entry.package_id(), entry.version(), entry.sha256())?;
            let snapshot = snapshot_record(&record, primary, &campaign_setting_entries(lock));
            let content = String::from_utf8(canonical_json_bytes(&Json::Object(snapshot)))
                .expect("canonical JSON is UTF-8");
            return self.located(&candidate, &entry, prefix, prefix, content);
        }
        Err(rejected("Library source package is unavailable"))
    }

    fn located(
        &self,
        candidate: &PackageCandidate,
        entry: &PackageLockEntry,
        prefix: &str,
        path: &str,
        content: String,
    ) -> Result<JsonMap, O::Error> {
        let mut result = JsonMap::new();
        result.insert("candidate_id".into(), Json::from(candidate.candidate_id()));
        result.insert("closure_sha256".into(), Json::from(candidate.closure_sha256()));
        result.insert("package".into(), Json::Object(self.package_identity(entry, prefix)?));
        result.insert("path".into(), Json::from(path));
        result.insert("content".into(), Json::from(content));
        Ok(result)
    }

    /// `_candidate(candidate_id)`.
    fn candidate(&self, candidate_id: &str) -> Result<PackageCandidate, O::Error> {
        let candidate = self.owner.package_candidate(candidate_id)?;
        if !inspectable(&candidate) || !self.matches_source(&candidate) {
            return Err(rejected("Library source candidate is unavailable for this Builder"));
        }
        Ok(candidate)
    }

    /// `_matches_source(candidate)`: the candidate's primary is this Builder's Ruleset.
    fn matches_source(&self, candidate: &PackageCandidate) -> bool {
        candidate
            .package_lock()
            .entries()
            .iter()
            .find(|entry| entry.role() == PackageRole::Primary)
            .is_some_and(|primary| primary.package_id() == self.ruleset_id)
    }

    /// `_package_index(candidate)`: the Ruleset's dependencies and the matching Setting's,
    /// excluding other worlds, in lock order.
    fn package_index(
        &self,
        candidate: &PackageCandidate,
    ) -> Result<Vec<(String, PackageLockEntry)>, O::Error> {
        let lock = candidate.package_lock();
        if lock.entries().len() > MAX_PACKAGES {
            return Err(rejected("Library source has too many packages to inspect"));
        }
        let settings = campaign_setting_entries(lock);
        let setting_id = settings
            .iter()
            .map(|entry| entry.package_id())
            .find(|id| self.setting_id.as_deref() == Some(*id))
            .map(str::to_owned);
        let excluded: Vec<&str> = settings
            .iter()
            .map(|entry| entry.package_id())
            .filter(|id| setting_id.as_deref() != Some(*id))
            .collect();
        let mut pending: Vec<String> = vec![self.ruleset_id.clone()];
        pending.extend(setting_id.clone());
        let mut visible: Vec<String> = Vec::new();
        while let Some(identity) = pending.pop() {
            if visible.contains(&identity) || excluded.contains(&identity.as_str()) {
                continue;
            }
            let entry =
                lock.entries().iter().find(|entry| entry.package_id() == identity).ok_or_else(|| {
                    O::Error::from(DomainError::uncaught("KeyError", pystr::repr(&identity)))
                })?;
            visible.push(identity.clone());
            pending.extend(entry.dependencies().iter().map(|dependency| dependency.package_id().to_owned()));
        }
        Ok(lock
            .entries()
            .iter()
            .filter(|entry| visible.iter().any(|id| id == entry.package_id()))
            .map(|entry| {
                let prefix = if entry.role() == PackageRole::Primary {
                    "ruleset".to_owned()
                } else if setting_id.as_deref() == Some(entry.package_id()) {
                    "setting".to_owned()
                } else {
                    format!("dependencies/{}", entry.package_id())
                };
                (prefix, entry.clone())
            })
            .collect())
    }

    /// `_candidate_identity(candidate)`.
    fn candidate_identity(&self, candidate: &PackageCandidate) -> Result<JsonMap, O::Error> {
        let mut packages = Vec::new();
        for (prefix, entry) in self.package_index(candidate)? {
            packages.push(Json::Object(self.package_identity(&entry, &prefix)?));
        }
        let source_kind =
            if packages.iter().any(|package| package["prefix"] == "setting") { "setting" } else { "ruleset" };
        let mut result = JsonMap::new();
        result.insert("candidate_id".into(), Json::from(candidate.candidate_id()));
        result.insert("closure_sha256".into(), Json::from(candidate.closure_sha256()));
        result.insert("status".into(), Json::from(candidate.status().as_str()));
        result.insert("source_kind".into(), Json::from(source_kind));
        result.insert("packages".into(), Json::Array(packages));
        Ok(result)
    }

    /// `_package_identity(entry, prefix)`.
    fn package_identity(&self, entry: &PackageLockEntry, prefix: &str) -> Result<JsonMap, O::Error> {
        let label = self.owner.package_label(entry.package_id(), entry.version(), entry.sha256())?;
        let mut result = JsonMap::new();
        result.insert("prefix".into(), Json::from(prefix));
        result.insert("id".into(), Json::from(entry.package_id()));
        result.insert("version".into(), Json::from(entry.version()));
        result.insert("content_sha256".into(), Json::from(entry.sha256()));
        result.insert("role".into(), Json::from(entry.role().as_str()));
        result.insert("label".into(), Json::from(label));
        Ok(result)
    }
}
