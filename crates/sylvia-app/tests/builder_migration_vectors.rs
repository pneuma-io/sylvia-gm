//! Compare sylvia_app::builder_session_migration with the frozen Python, on lane 5's legacy
//! Builder bases (`sylvia-store/tests/vectors/legacy_builder`) and on the open base with stored
//! message contents changed. Each case compares the migrated ids or the error, the store calls
//! the migration made (retain, then detach with the full launch context) and the storage tree.
//!
//! Regenerate with `tests/vectors/generate_builder_migration_vectors.py` (needs the snapshot's Python).

use flate2::read::GzDecoder;
use serde_json::json;
use std::cell::RefCell;
use std::io::Read;
use std::path::{Path, PathBuf};
use sylvia_app::builder_references::{OwnerError, ReferenceOwner};
use sylvia_app::builder_session_migration::{migrate_legacy_builder_chats, MigrationOwner};
use sylvia_app::bundled_packages::{BundledPackages, BundledSources};
use sylvia_app::library_sources::LibraryOwner;
use sylvia_base::{pyjson, Json, JsonMap};
use sylvia_domain::package_library::PackageCandidate;
use sylvia_domain::session::SessionContext;
use sylvia_domain::{DomainError, DomainResult};
use sylvia_store::AuthorityStore;

fn vectors() -> Json {
    pyjson::loads(include_str!("vectors/builder_migration_vectors.json")).expect("vector file is valid JSON")
}

fn repository() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
}

/// The generator's `skipped`, as lane 5's store vectors skip.
fn skipped(relative: &str) -> bool {
    ["authority.sqlite3", "migration-sources", "bundled-srd-starters"]
        .iter()
        .any(|prefix| relative.starts_with(prefix))
}

/// The generator's `tree`: every entry under `root`, sorted part by part, files by SHA-256.
fn tree(root: &Path) -> Json {
    fn walk(root: &Path, folder: &Path, entries: &mut Vec<(String, Json)>) {
        for entry in std::fs::read_dir(folder).unwrap() {
            let path = entry.unwrap().path();
            let relative = path.strip_prefix(root).unwrap().to_string_lossy().replace('\\', "/");
            if skipped(&relative) {
                continue;
            }
            if path.is_dir() {
                entries.push((relative.clone(), json!([relative, "dir", null])));
                walk(root, &path, entries);
            } else {
                let digest = sylvia_base::hash::sha256_hex(std::fs::read(&path).unwrap());
                entries.push((relative.clone(), json!([relative, "file", digest])));
            }
        }
    }
    let mut entries = Vec::new();
    walk(root, root, &mut entries);
    entries.sort_by(|left, right| left.0.split('/').cmp(right.0.split('/')));
    Json::Array(entries.into_iter().map(|(_, entry)| entry).collect())
}

/// The generator's `prepare` and `mutate`: a fresh copy of a base, with the recorded contents.
fn prepare(root: &Path, fixtures: &Path, bases: &Json, case: &Json) {
    std::fs::create_dir_all(root).unwrap();
    let name = case["base"].as_str().unwrap();
    let mut bytes = Vec::new();
    GzDecoder::new(std::fs::File::open(fixtures.join(format!("base-{name}.sqlite3.gz"))).unwrap())
        .read_to_end(&mut bytes)
        .unwrap();
    let database = root.join("authority.sqlite3");
    std::fs::write(&database, bytes).unwrap();
    for (relative, text) in bases[name]["files"].as_object().unwrap() {
        let path = root.join(relative);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text.as_str().unwrap()).unwrap();
    }
    if let Some(Json::Array(edits)) = case.get("edits") {
        let connection = rusqlite::Connection::open(&database).unwrap();
        for edit in edits {
            let text = |index: usize| edit[index].as_str().unwrap();
            let changed = connection
                .execute(
                    "UPDATE agent_session_messages SET content_json = ?1 WHERE session_id = ?2 AND message_id = ?3",
                    (text(2), text(0), text(1)),
                )
                .unwrap();
            assert_eq!(changed, 1);
        }
    }
}

/// `AuthorityStore` as the generator's recording owner: retain and detach are recorded before
/// they run, and the store's errors are read back as the domain errors they stand for, so a
/// fault keeps Python's class.
struct Recording<'a> {
    store: &'a AuthorityStore,
    calls: RefCell<Vec<Json>>,
}

fn domain<T>(result: Result<T, sylvia_store::StoreError>) -> DomainResult<T> {
    result.map_err(|error| error.as_domain())
}

impl LibraryOwner for Recording<'_> {
    type Error = DomainError;
    fn package_candidates(&self) -> DomainResult<Vec<PackageCandidate>> {
        domain(self.store.package_candidates())
    }
    fn package_candidate(&self, candidate_id: &str) -> DomainResult<PackageCandidate> {
        domain(LibraryOwner::package_candidate(self.store, candidate_id))
    }
    fn package_file_paths(
        &self,
        id: &str,
        version: &str,
        sha256: &str,
        maximum: i64,
    ) -> DomainResult<Vec<String>> {
        domain(LibraryOwner::package_file_paths(self.store, id, version, sha256, maximum))
    }
    fn package_text_file(
        &self,
        id: &str,
        version: &str,
        sha256: &str,
        path: &str,
        maximum: i64,
    ) -> DomainResult<String> {
        domain(LibraryOwner::package_text_file(self.store, id, version, sha256, path, maximum))
    }
    fn package_record(&self, id: &str, version: &str, sha256: &str) -> DomainResult<JsonMap> {
        domain(LibraryOwner::package_record(self.store, id, version, sha256))
    }
    fn package_label(&self, id: &str, version: &str, sha256: &str) -> DomainResult<String> {
        domain(LibraryOwner::package_label(self.store, id, version, sha256))
    }
}

impl ReferenceOwner for Recording<'_> {
    fn install_package_archive(&self, archive: &[u8], install_id: &str) -> DomainResult<PackageCandidate> {
        domain(ReferenceOwner::install_package_archive(self.store, archive, install_id))
    }
}

impl MigrationOwner for Recording<'_> {
    fn legacy_builder_contexts(&self) -> DomainResult<Vec<SessionContext>> {
        domain(MigrationOwner::legacy_builder_contexts(self.store))
    }
    fn session_context(&self, session_id: &str) -> DomainResult<SessionContext> {
        domain(MigrationOwner::session_context(self.store, session_id))
    }
    fn retain_legacy_workspace(
        &self,
        storage_root: &Path,
        campaign_id: Option<&str>,
        session_id: &str,
    ) -> DomainResult<()> {
        self.calls.borrow_mut().push(json!(["retain", campaign_id, session_id]));
        domain(MigrationOwner::retain_legacy_workspace(self.store, storage_root, campaign_id, session_id))
    }
    fn detach_legacy_builder_chat(
        &self,
        session_id: &str,
        expected_message_count: usize,
        launch_context: &JsonMap,
        display_name: &str,
    ) -> DomainResult<()> {
        self.calls.borrow_mut().push(json!([
            "detach",
            session_id,
            expected_message_count,
            launch_context,
            display_name
        ]));
        domain(MigrationOwner::detach_legacy_builder_chat(
            self.store,
            session_id,
            expected_message_count,
            launch_context,
            display_name,
        ))
    }
}

fn result(outcome: DomainResult<Vec<String>>) -> Json {
    match outcome {
        Ok(ids) => json!(["ok", ids]),
        Err(error) => json!([error.python_class(), error.message(), error.code()]),
    }
}

#[test]
fn builder_migration_matches_python() {
    let vectors = vectors();
    let root = repository();
    let fixtures = root.join(vectors["fixtures"].as_str().unwrap());
    let bases = pyjson::loads(&std::fs::read_to_string(fixtures.join("store_runs.json")).unwrap()).unwrap()
        ["bases"]
        .clone();
    let bundled = BundledPackages::new(BundledSources::from_repository_root(&root));
    let work = Path::new(env!("CARGO_TARGET_TMPDIR")).join("builder-migration-vectors");
    let _ = std::fs::remove_dir_all(&work);
    let mut failures = Vec::new();
    let cases = vectors["cases"].as_array().unwrap();
    for (index, case) in cases.iter().enumerate() {
        let storage = work.join(format!("case-{index}"));
        prepare(&storage, &fixtures, &bases, case);
        let catalog: Vec<JsonMap> = vectors["catalogs"][case["catalog"].as_str().unwrap()]
            .as_array()
            .unwrap()
            .iter()
            .map(|row| row.as_object().unwrap().clone())
            .collect();
        let store =
            AuthorityStore::open_for_application_owner(&storage.join("authority.sqlite3"), None).unwrap();
        let owner = Recording { store: &store, calls: RefCell::new(Vec::new()) };
        let outcome = result(migrate_legacy_builder_chats(&owner, &bundled, &storage, &catalog));
        let calls = owner.calls.into_inner();
        drop(store);
        let actual = json!({"result": outcome, "calls": calls, "tree": tree(&storage)});
        let expected = json!({"result": case["result"], "calls": case["calls"], "tree": case["tree"]});
        if actual != expected {
            let part =
                ["result", "calls", "tree"].into_iter().find(|key| actual[key] != expected[key]).unwrap();
            failures.push(format!(
                "{} {} {}: {part}: python {}, rust {}",
                case["base"], case["catalog"], case["mutation"], expected[part], actual[part]
            ));
        }
    }
    let _ = std::fs::remove_dir_all(&work);
    assert!(cases.len() >= 60, "{} cases", cases.len());
    let shown: Vec<String> = failures.iter().take(6).map(|f| f.chars().take(1500).collect()).collect();
    assert!(failures.is_empty(), "{} of {} differ: {shown:#?}", failures.len(), cases.len());
}

/// The real owner's errors: what `AuthorityStore` itself returns for a refusal and a fault.
#[test]
fn authority_store_owner_reports_python_errors() {
    let vectors = vectors();
    let root = repository();
    let fixtures = root.join(vectors["fixtures"].as_str().unwrap());
    let bases = pyjson::loads(&std::fs::read_to_string(fixtures.join("store_runs.json")).unwrap()).unwrap()
        ["bases"]
        .clone();
    let bundled = BundledPackages::new(BundledSources::from_repository_root(&root));
    let work = Path::new(env!("CARGO_TARGET_TMPDIR")).join("builder-migration-store-errors");
    let _ = std::fs::remove_dir_all(&work);
    for (index, mutation) in
        ["transcript_ghost", "parent_unknown", "state_answer_ghost"].into_iter().enumerate()
    {
        let case =
            vectors["cases"].as_array().unwrap().iter().find(|case| case["mutation"] == mutation).unwrap();
        let storage = work.join(format!("case-{index}"));
        prepare(&storage, &fixtures, &bases, case);
        let store =
            AuthorityStore::open_for_application_owner(&storage.join("authority.sqlite3"), None).unwrap();
        let error = migrate_legacy_builder_chats(&store, &bundled, &storage, &[]).unwrap_err();
        let expected = &case["result"];
        match error.rejection() {
            // A rejection keeps Python's class, code and message.
            Some(coded) => {
                assert_eq!(json!([coded.error_type, coded.message, coded.code]), *expected, "{mutation}");
            }
            // A fault is the owner's internal error: only its text survives, as in Python's reply.
            None => {
                assert_eq!(expected[0], "KeyError", "{mutation}");
                assert_eq!(error.to_string(), expected[1].as_str().unwrap(), "{mutation}");
            }
        }
    }
    let _ = std::fs::remove_dir_all(&work);
}
