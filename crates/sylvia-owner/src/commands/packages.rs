//! `interfaces/commands/packages.py`: the package commands.

use super::campaigns::{required, required_artifact_id, resolve_strict, store_error};
use crate::dispatch::DispatchContext;
use sylvia_app::import_repair_sessions::{caught_as, domain_caught_as, CaughtAs};
use sylvia_base::{CodedError, CodedResult, Json, JsonMap};

/// An exception Python's handler does not catch: the owner's internal-error reply, with its class.
fn os_error(error: &std::io::Error) -> CodedError {
    CodedError::new("internal_error", error.to_string()).with_error_type("OSError")
}

/// `handle_package_candidates`: every installed candidate, newest first
/// (`AuthorityOwner.package_candidates`).
pub async fn handle_package_candidates(ctx: &DispatchContext, _params: &JsonMap) -> CodedResult<Json> {
    let _operation = super::operation(ctx)?;
    let candidates = ctx.store.call(|store| store.package_candidates()).await.map_err(store_error)?;
    Ok(Json::Array(candidates.iter().map(candidate_value).collect()))
}

/// `handle_inspect_package_candidate`: one candidate's static package facts.
pub async fn handle_inspect_package_candidate(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let candidate_id = required(params, "candidate_id")?;
    sylvia_app::authority::package_candidates::inspect_package_candidate(&ctx.store, &candidate_id)
        .await
        .map(Json::Object)
        .map_err(store_error)
}

/// `handle_package_candidate_text`: one admitted text file of a candidate's package. The three
/// texts are read in order before the owner is asked.
pub async fn handle_package_candidate_text(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let candidate_id = required(params, "candidate_id")?;
    let package_id = required(params, "package_id")?;
    let path = required(params, "path")?;
    sylvia_app::authority::package_candidates::package_candidate_text(
        &ctx.store,
        &candidate_id,
        &package_id,
        &path,
    )
    .await
    .map(Json::Object)
    .map_err(store_error)
}

/// `handle_compare_package_candidates`: two candidates' closures compared by package.
pub async fn handle_compare_package_candidates(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let left = required(params, "left_candidate_id")?;
    let right = required(params, "right_candidate_id")?;
    sylvia_app::authority::package_candidates::compare_package_candidates(&ctx.store, &left, &right)
        .await
        .map(Json::Object)
        .map_err(store_error)
}

/// `handle_export_package_candidate`, for `export_package_candidate` and
/// `export_package_candidate_file`: the candidate's exact installed closure written to
/// `exports/<export_id>.sylvia-gm-package` under the storage root. As in Python, the target is
/// opened (and truncated) before `candidate_id` is read, and a failed export leaves what was
/// written. The file command also answers with the archive's resolved path.
pub async fn handle_export_package_candidate(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let export_id = required_artifact_id(params, "export_id")?;
    let exports = ctx.storage_root.join("exports");
    let target = exports.join(format!("{export_id}.sylvia-gm-package"));
    // `mkdir(parents=True, exist_ok=True)` and `target.open("wb")`: failures are `OSError`s.
    std::fs::create_dir_all(&exports).map_err(|error| os_error(&error))?;
    let mut stream = std::fs::File::create(&target).map_err(|error| os_error(&error))?;
    let candidate_id = required(params, "candidate_id")?;
    let result =
        sylvia_app::authority::portable::export_package_candidate(&ctx.store, &candidate_id, &mut stream)
            .await
            .map_err(store_error)?;
    drop(stream);
    if ctx.method != "export_package_candidate_file" {
        return Ok(Json::Object(result));
    }
    let resolved = resolve_strict(&target).map_err(|error| os_error(&error))?;
    let mut reply = JsonMap::new();
    reply.insert("export".into(), Json::Object(result));
    reply.insert("artifact_path".into(), resolved.to_string_lossy().into_owned().into());
    Ok(Json::Object(reply))
}

/// `_MAX_PACKAGE_BYTES` (`owner_process.py`): the desktop import limit for a package archive.
const MAX_PACKAGE_BYTES: u64 = 48 * 1024 * 1024;

/// `plan_bundle.PLAN_BUNDLE_BYTE_LIMIT`: its members' limits plus 64 KiB.
const PLAN_BUNDLE_BYTE_LIMIT: u64 = sylvia_app::plan_bundle::PLAN_BUNDLE_BYTE_LIMIT as u64;

/// `OwnerProtocolError(message)`.
fn protocol(message: &str) -> CodedError {
    CodedError::rejected(message).with_error_type("OwnerProtocolError")
}

/// `_json_value(PackageCandidate)`: the dataclass fields in declaration order, the lock as
/// `{"entries": [...]}` with each entry's fields (`_json_value(PackageLock)`).
fn candidate_value(candidate: &sylvia_domain::package_library::PackageCandidate) -> Json {
    let mut lock = JsonMap::new();
    lock.insert("entries".into(), sylvia_store::guided_start::package_lock_payload(candidate.package_lock()));
    let mut value = JsonMap::new();
    value.insert("candidate_id".into(), candidate.candidate_id().into());
    value.insert("install_id".into(), candidate.install_id().into());
    value.insert("status".into(), candidate.status().as_str().into());
    value.insert("closure_sha256".into(), candidate.closure_sha256().into());
    value.insert("package_lock".into(), Json::Object(lock));
    value.insert("validation_report".into(), Json::Object(candidate.validation_report().clone()));
    Json::Object(value)
}

/// `ctx.bus.install_package(source, install_id=...)`: `AuthorityOwner.install_package_archive`.
async fn install(
    ctx: &DispatchContext,
    archive: Vec<u8>,
    install_id: String,
) -> sylvia_store::StoreResult<Json> {
    ctx.store
        .call(move |store| {
            sylvia_app::package_library::install_package_archive(&*store, &archive, &install_id)
                .map(|candidate| candidate_value(&candidate))
        })
        .await
}

/// `handle_install_package`: an archive sent as `archive_base64` (`b64decode(validate=True)`),
/// within the desktop import limit, installed under `install_id`.
pub async fn handle_install_package(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let archive_text = required(params, "archive_base64")?;
    let archive = sylvia_base::pybase64::b64decode_validate(&archive_text)
        .map_err(|_| protocol("archive_base64 is invalid"))?;
    if archive.len() as u64 > MAX_PACKAGE_BYTES {
        return Err(protocol("Package archive exceeds the desktop import limit"));
    }
    let install_id = required(params, "install_id")?;
    install(ctx, archive, install_id).await.map_err(store_error)
}

/// `Path(text).expanduser()`: a leading `~` alone or before a separator becomes the home
/// folder (`USERPROFILE` on Windows, `HOME` elsewhere); anything else is unchanged.
fn expand_user(text: &str) -> std::path::PathBuf {
    let home = std::env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" });
    if let (Some(rest), Some(home)) = (text.strip_prefix('~'), home) {
        let separator = rest.starts_with('/') || (cfg!(windows) && rest.starts_with('\\'));
        if rest.is_empty() || separator {
            return std::path::Path::new(&home).join(rest.trim_start_matches(['/', '\\']));
        }
    }
    std::path::PathBuf::from(text)
}

/// The message Python's handler gives a source it cannot read (`except OSError`).
const UNAVAILABLE: &str = "Selected package file is unavailable";

/// The message Python's handler gives a source it cannot admit
/// (`except (ValueError, zipfile.BadZipFile)`).
const NOT_ADMITTED: &str = "Content archive could not be admitted. Check its format and content, \
                            then export a new copy using Store or Deflate ZIP compression.";

/// A failed install or Plan import, as the handler's `except` clauses sort it.
enum Failed {
    /// What the Plan import raised, in Python's class.
    Domain(sylvia_domain::DomainError),
    /// What the package install raised.
    Store(sylvia_store::StoreError),
}

impl Failed {
    /// The `except` clause that catches it. A store refusal carries Python's class; any other
    /// store failure (`sqlite3.Error`, a fault) is caught by none of them.
    fn caught_as(&self) -> CaughtAs {
        match self {
            Failed::Domain(error) => domain_caught_as(error),
            Failed::Store(sylvia_store::StoreError::Coded(error)) => {
                caught_as(error.error_type.unwrap_or("CommandRejected"))
            }
            Failed::Store(sylvia_store::StoreError::Backup(_)) => CaughtAs::Authority,
            Failed::Store(_) => CaughtAs::Other,
        }
    }

    /// `except (AuthorityError, ValueError)`: the failures an import repair is offered for.
    fn repairable(&self) -> bool {
        matches!(self.caught_as(), CaughtAs::Authority | CaughtAs::Value)
    }

    /// `str(error)` and `error.diagnostics`, for the repair receipt.
    fn message_and_diagnostics(&self) -> (String, Vec<Json>) {
        match self {
            Failed::Domain(error) => (
                error.message().to_owned(),
                error.diagnostics().iter().map(|item| Json::Object(item.as_payload())).collect(),
            ),
            Failed::Store(sylvia_store::StoreError::Coded(error)) => {
                (error.message.clone(), error.diagnostics.clone())
            }
            Failed::Store(error) => (error.to_string(), Vec::new()),
        }
    }

    /// The outer handler's answer once no repair is offered: a `ValueError` or `BadZipFile` is
    /// "could not be admitted", an `OSError` is "unavailable", and an `AuthorityError` or
    /// anything else keeps its own reply.
    fn reply(self) -> CodedError {
        let bad_zip = matches!(&self, Failed::Domain(error) if error.python_class() == "BadZipFile");
        match self.caught_as() {
            CaughtAs::Value => return CodedError::rejected(NOT_ADMITTED),
            CaughtAs::Os => return CodedError::rejected(UNAVAILABLE),
            _ if bad_zip => return CodedError::rejected(NOT_ADMITTED),
            _ => {}
        }
        match self {
            Failed::Domain(error) => store_error(sylvia_store::StoreError::from(error)),
            Failed::Store(error) => store_error(error),
        }
    }
}

/// `_install_plan_snapshot(owner, snapshot, expected_closure)` (`owner_process.py`): install a
/// validated Plan's inherited bytes once across repeated imports.
fn install_plan_snapshot(
    store: &sylvia_store::AuthorityStore,
    snapshot: &[u8],
    expected_closure: &str,
) -> sylvia_domain::DomainResult<()> {
    let install_id = format!("plan-snapshot-{}", &sylvia_base::hash::sha256_hex(snapshot)[..32]);
    let to_domain = |error: sylvia_store::StoreError| match error.rejection() {
        Some(coded) => sylvia_domain::DomainError::Rejected {
            error_type: coded.error_type.unwrap_or("CommandRejected"),
            code: coded.code,
            message: coded.message,
            diagnostics: Vec::new(),
            mechanics: None,
        },
        None => sylvia_domain::DomainError::uncaught("RuntimeError", error.to_string()),
    };
    let candidate = match store.package_candidate(&format!("candidate-{install_id}")) {
        Ok(candidate) => candidate,
        Err(sylvia_store::StoreError::Coded(error)) if error.code == "candidate_unavailable" => {
            sylvia_app::package_library::install_package_archive(store, snapshot, &install_id)
                .map_err(to_domain)?
        }
        Err(error) => return Err(to_domain(error)),
    };
    if candidate.closure_sha256() != expected_closure {
        return Err(sylvia_domain::DomainError::authority(
            "IdempotencyConflict",
            "Imported Plan package snapshot does not match its saved closure",
        ));
    }
    Ok(())
}

/// `handle_install_package_file`: the selected file, checked and size-bounded, then installed
/// under `install_id`, or imported as a `.sylvia-gm-plan` bundle with its package snapshot. A
/// refused install or import is offered as an import repair when its source is a safely
/// readable archive (`import_repair_sessions.offer_import_repair`).
pub async fn handle_install_package_file(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let unavailable = || CodedError::rejected(UNAVAILABLE);
    let source = sylvia_provider::codex_rpc::python_resolve(&expand_user(&required(params, "source_path")?));
    // `source.is_file()` is false for anything it cannot stat.
    if !source.is_file() {
        return Err(unavailable());
    }
    let is_plan =
        source.extension().is_some_and(|suffix| suffix.to_string_lossy().to_lowercase() == "sylvia-gm-plan");
    let maximum = if is_plan { PLAN_BUNDLE_BYTE_LIMIT } else { MAX_PACKAGE_BYTES };
    let size = std::fs::metadata(&source).map_err(|_| unavailable())?.len();
    if size > maximum {
        return Err(CodedError::rejected("Package archive exceeds the desktop import limit"));
    }
    let archive = std::fs::read(&source).map_err(|_| unavailable())?;
    let outcome = if is_plan {
        // `import_plan_bundle(ctx.storage_root, stream, install_snapshot=...)`; the snapshot
        // install reads and writes the store, so it holds the owner's operation guard.
        let _operation = super::operation(ctx)?;
        let (storage_root, bundled) = (ctx.storage_root.clone(), std::sync::Arc::clone(&ctx.bundled));
        ctx.store
            .call(move |store| {
                let mut install =
                    |snapshot: &[u8], expected: &str| install_plan_snapshot(store, snapshot, expected);
                Ok(sylvia_app::plan_bundle::import_plan_bundle(
                    &storage_root,
                    &archive,
                    &mut install,
                    &bundled,
                ))
            })
            .await
            .map_err(store_error)?
            .map(|plan| Json::Object(plan.catalog_payload()))
            .map_err(Failed::Domain)
    } else {
        // A missing `install_id` is an `OwnerProtocolError` (a `ValueError`) inside the
        // install's `try`; the repair offer reads it again, and that second error reaches the
        // outer `except ValueError`.
        let Ok(install_id) = required(params, "install_id") else {
            return Err(CodedError::rejected(NOT_ADMITTED));
        };
        install(ctx, archive, install_id).await.map_err(Failed::Store)
    };
    let failed = match outcome {
        Ok(reply) => return Ok(reply),
        Err(failed) => failed,
    };
    if !failed.repairable() {
        return Err(failed.reply());
    }
    // `offer_import_repair(ctx.storage_root, source, install_id=_required_text(...), error=...)`.
    let Ok(install_id) = required(params, "install_id") else {
        return Err(CodedError::rejected(NOT_ADMITTED));
    };
    let (message, diagnostics) = failed.message_and_diagnostics();
    let (storage_root, bundled, offered_source) =
        (ctx.storage_root.clone(), std::sync::Arc::clone(&ctx.bundled), source.clone());
    let offer = tokio::task::spawn_blocking(move || {
        let error =
            sylvia_app::import_repair_sessions::FailedImport { message: &message, diagnostics: &diagnostics };
        sylvia_app::import_repair_sessions::offer_import_repair(
            &storage_root,
            &offered_source,
            &install_id,
            error,
            &bundled,
        )
    })
    .await
    .map_err(|error| CodedError::new("internal_error", error.to_string()))?;
    match offer {
        Ok(Some(repair)) => Ok(Json::Object(repair)),
        // No repair: Python re-raises the install's own failure.
        Ok(None) => Err(failed.reply()),
        // The offer itself failed: a `ValueError` or `BadZipFile` from it reaches the outer
        // handler too.
        Err(error) => Err(Failed::Domain(error).reply()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Read;

    fn workspace(path: &str) -> std::path::PathBuf {
        crate::util::source_root().join(path)
    }

    /// The generator's `tree`: each file under `exports/` and its SHA-256.
    fn exports_tree(root: &std::path::Path) -> Json {
        let exports = root.join("exports");
        if !exports.exists() {
            return Json::Array(Vec::new());
        }
        let mut entries: Vec<(String, Json)> = std::fs::read_dir(&exports)
            .unwrap()
            .map(|entry| {
                let path = entry.unwrap().path();
                let relative = format!("exports/{}", path.file_name().unwrap().to_string_lossy());
                let digest = sylvia_base::hash::sha256_hex(std::fs::read(&path).unwrap());
                (relative.clone(), serde_json::json!([relative, digest]))
            })
            .collect();
        entries.sort_by(|left, right| left.0.cmp(&right.0));
        Json::Array(entries.into_iter().map(|(_, entry)| entry).collect())
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn package_exports_answer_and_publish_as_pythons_handler_does() {
        // Lane 3's Python run of `handle_export_package_candidate` (`generate_package_command_runs.py`)
        // on the package-install profile, through the owner's dispatch: each reply, and the exports
        // folder after each run.
        let record: Json = serde_json::from_str(
            &std::fs::read_to_string(workspace(
                "crates/sylvia-app/tests/vectors/package_export/package_command_runs.json",
            ))
            .unwrap(),
        )
        .unwrap();
        // A short root: the 128-character export identity stays under `MAX_PATH` (a test binary
        // carries no `longPathAware` manifest).
        let folder = std::env::temp_dir().join(format!("sgp-{}", std::process::id()));
        if let Err(error) = std::fs::remove_dir_all(&folder) {
            assert_eq!(
                error.kind(),
                std::io::ErrorKind::NotFound,
                "cannot clear the old work folder {}: {error}",
                folder.display()
            );
        }
        std::fs::create_dir_all(&folder).unwrap();
        let database = folder.join("authority.sqlite3");
        let mut bytes = Vec::new();
        flate2::read::GzDecoder::new(
            std::fs::File::open(workspace("crates/sylvia-store/tests/vectors/packages/install.sqlite3.gz"))
                .unwrap(),
        )
        .read_to_end(&mut bytes)
        .unwrap();
        std::fs::write(&database, bytes).unwrap();
        let path = database.clone();
        let (store, thread) = sylvia_store::StoreHandle::spawn(move || {
            sylvia_store::AuthorityStore::open_for_application_owner(&path, None)
        })
        .unwrap();
        let context = crate::dispatch::DispatchContext {
            owner_id: "package-export-test".into(),
            storage_root: folder.clone(),
            handshake_verified: true,
            method: String::new(),
            gm_system_prompt: None,
            store,
            providers: None,
            session_loop: None,
            campaign_loop: None,
            bundled: std::sync::Arc::new(sylvia_app::bundled_packages::BundledPackages::new(
                sylvia_app::bundled_packages::BundledSources::from_repository_root(
                    &crate::util::source_root(),
                ),
            )),
            record_provider_warning: std::sync::Arc::new(|_: &str| {}),
        };
        let root = resolve_strict(&folder).unwrap();
        let mut failures = Vec::new();
        for (index, run) in record["runs"].as_array().unwrap().iter().enumerate() {
            let method = run["op"].as_str().unwrap();
            let mut asked = context.clone();
            asked.method = method.to_owned();
            let params = run["params"].as_object().unwrap().clone();
            let want = &run["outcome"];
            let got = match crate::dispatch::dispatch(&asked, method, &params).await {
                Ok(mut value) => {
                    if let Some(path) = value.get("artifact_path").and_then(Json::as_str).map(str::to_owned) {
                        let relative = std::path::Path::new(&path).strip_prefix(&root).unwrap().to_path_buf();
                        let parts: Vec<String> = relative
                            .components()
                            .map(|part| part.as_os_str().to_string_lossy().into_owned())
                            .collect();
                        value["artifact_path"] = parts.join("/").into();
                    }
                    serde_json::json!({ "ok": value })
                }
                Err(crate::dispatch::Failure::Rejected(error)) => {
                    // A protocol refusal carries the owner's own code; Python's has none.
                    let code = if error.error_type == Some("OwnerProtocolError") {
                        Json::Null
                    } else {
                        Json::from(error.code)
                    };
                    serde_json::json!({"error": error.error_type, "code": code, "message": error.message})
                }
                Err(other) => serde_json::json!({ "other": format!("{other:?}") }),
            };
            let (got, want_text) = (got.to_string(), want.to_string());
            if got != want_text {
                failures.push(format!("#{index} {method}\n  Rust   {got}\n  Python {want_text}"));
            }
            let (tree, want_tree) = (exports_tree(&folder).to_string(), run["tree"].to_string());
            if tree != want_tree {
                failures.push(format!("#{index} {method} exports\n  Rust   {tree}\n  Python {want_tree}"));
            }
        }
        drop(context);
        tokio::task::spawn_blocking(move || thread.join()).await.unwrap();
        let _ = std::fs::remove_dir_all(folder);
        assert!(
            failures.is_empty(),
            "{} package export runs differ:\n{}",
            failures.len(),
            failures.join("\n")
        );
    }

    /// The generator's `tree`: every file under the storage root but the database, each as its
    /// SHA-256 by path, then each import-repair receipt as its JSON (the selected path made
    /// relative to `files`), by that path.
    fn storage_tree(storage: &std::path::Path, files: &std::path::Path) -> Json {
        fn walk(folder: &std::path::Path, found: &mut Vec<std::path::PathBuf>) {
            for entry in std::fs::read_dir(folder).unwrap() {
                let path = entry.unwrap().path();
                if path.is_dir() {
                    walk(&path, found);
                } else {
                    found.push(path);
                }
            }
        }
        let mut found = Vec::new();
        walk(storage, &mut found);
        let mut entries: Vec<(Vec<String>, Json)> = Vec::new();
        let mut receipts: Vec<(String, Json)> = Vec::new();
        for path in found {
            let parts: Vec<String> = path
                .strip_prefix(storage)
                .unwrap()
                .components()
                .map(|part| part.as_os_str().to_string_lossy().into_owned())
                .collect();
            let relative = parts.join("/");
            if relative.starts_with("authority.sqlite3") {
                continue;
            }
            let data = std::fs::read(&path).unwrap();
            if relative.starts_with("import-repair-sources/") {
                let mut receipt: Json = serde_json::from_slice(&data).unwrap();
                let canonical = sylvia_domain::package_library::canonical_json_bytes(&receipt) == data;
                let source = std::path::PathBuf::from(receipt["source_path"].as_str().unwrap());
                let named = source.strip_prefix(files).unwrap().to_string_lossy().replace('\\', "/");
                receipt["source_path"] = named.clone().into();
                receipts.push((
                    named,
                    serde_json::json!(["import-repair-sources/<repair>.json", {"receipt": receipt, "canonical": canonical}]),
                ));
            } else {
                entries.push((parts, serde_json::json!([relative, sylvia_base::hash::sha256_hex(&data)])));
            }
        }
        entries.sort_by(|left, right| left.0.cmp(&right.0));
        receipts.sort_by(|left, right| left.0.cmp(&right.0));
        Json::Array(
            entries
                .into_iter()
                .map(|(_, entry)| entry)
                .chain(receipts.into_iter().map(|(_, entry)| entry))
                .collect(),
        )
    }

    /// Python's `{"error": type(error).__name__, "code": getattr(error, "code", None), ...}` for
    /// an error a library function raises: a `ValueError` carries no code.
    fn domain_failure(error: &sylvia_domain::DomainError) -> Json {
        let code =
            if error.is_value_error() { Json::Null } else { error.code().map_or(Json::Null, Json::from) };
        serde_json::json!({"error": error.python_class(), "code": code, "message": error.message()})
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn plan_bundles_and_repair_offers_answer_as_pythons_handler_does() {
        // `generate_plan_bundle_runs.py`: Python's `handle_install_package_file` on an empty
        // owner for each selected file (Plans, damaged Plans, packages, repair offers), then
        // `export_plan_bundle` and `inspect_repair_source`.
        let vectors = workspace("crates/sylvia-app/tests/vectors/plan_bundle");
        let record: Json =
            serde_json::from_str(&std::fs::read_to_string(vectors.join("runs.json")).unwrap()).unwrap();
        let plan = std::fs::read(vectors.join("relay.sylvia-gm-plan")).unwrap();
        assert_eq!(sylvia_base::hash::sha256_hex(&plan), record["plan_sha256"]);
        // A short root keeps the Plan library's paths under `MAX_PATH`.
        let folder = std::env::temp_dir().join(format!("sgpb-{}", std::process::id()));
        if let Err(error) = std::fs::remove_dir_all(&folder) {
            assert_eq!(
                error.kind(),
                std::io::ErrorKind::NotFound,
                "cannot clear {}: {error}",
                folder.display()
            );
        }
        let (storage, files) = (folder.join("storage"), folder.join("files"));
        std::fs::create_dir_all(&storage).unwrap();
        std::fs::create_dir_all(&files).unwrap();
        let files = sylvia_provider::codex_rpc::python_resolve(&files);
        for entry in std::fs::read_dir(vectors.join("selected")).unwrap() {
            let path = entry.unwrap().path();
            std::fs::copy(&path, files.join(path.file_name().unwrap())).unwrap();
        }
        std::fs::copy(
            workspace("rulesets/riichi-mahjong/riichi-mahjong-0.1.0.sylvia-gm-package"),
            files.join("riichi.sylvia-gm-package"),
        )
        .unwrap();
        std::fs::create_dir(files.join("folder.sylvia-gm-package")).unwrap();
        let database = storage.join("authority.sqlite3");
        let (store, thread) = sylvia_store::StoreHandle::spawn(move || {
            sylvia_store::AuthorityStore::open_for_application_owner(&database, None)
        })
        .unwrap();
        let bundled = std::sync::Arc::new(sylvia_app::bundled_packages::BundledPackages::new(
            sylvia_app::bundled_packages::BundledSources::from_repository_root(&crate::util::source_root()),
        ));
        let context = crate::dispatch::DispatchContext {
            owner_id: "plan-bundle-test".into(),
            storage_root: storage.clone(),
            handshake_verified: true,
            method: "install_package_file".into(),
            gm_system_prompt: None,
            store,
            providers: None,
            session_loop: None,
            campaign_loop: None,
            bundled: std::sync::Arc::clone(&bundled),
            record_provider_warning: std::sync::Arc::new(|_: &str| {}),
        };
        let mut failures = Vec::new();
        for (index, run) in record["runs"].as_array().unwrap().iter().enumerate() {
            let mut params = run["params"].as_object().unwrap().clone();
            let name = params.remove("source").unwrap();
            let source = files.join(name.as_str().unwrap());
            params.insert("source_path".into(), source.to_string_lossy().into_owned().into());
            if run.get("tamper").is_some() {
                for receipt in std::fs::read_dir(storage.join("import-repair-sources")).unwrap() {
                    let path = receipt.unwrap().path();
                    let mut data = std::fs::read(&path).unwrap();
                    data.push(b' ');
                    std::fs::write(&path, data).unwrap();
                }
            }
            let got = match crate::dispatch::dispatch(&context, "install_package_file", &params).await {
                Ok(mut value) => {
                    if value.get("status").and_then(Json::as_str) == Some("repairable") {
                        // `sha256(canonical([install_id, source_sha256, str(source)]))`.
                        let digest = sylvia_base::hash::sha256_hex(std::fs::read(&source).unwrap());
                        let expected = sylvia_base::hash::sha256_hex(
                            sylvia_domain::package_library::canonical_json_bytes(&serde_json::json!([
                                params["install_id"],
                                digest,
                                source.to_string_lossy()
                            ])),
                        );
                        assert_eq!(value["repair_id"], expected.as_str(), "#{index} repair identity");
                        value["repair_id"] = "<repair>".into();
                    }
                    serde_json::json!({ "ok": value })
                }
                Err(crate::dispatch::Failure::Rejected(error)) => {
                    let code = if error.error_type == Some("OwnerProtocolError") {
                        Json::Null
                    } else {
                        Json::from(error.code)
                    };
                    serde_json::json!({
                        "error": error.error_type.unwrap_or("CommandRejected"),
                        "code": code,
                        "message": error.message,
                    })
                }
                Err(other) => serde_json::json!({ "other": format!("{other:?}") }),
            };
            let label = format!("#{index} {name}");
            if let Some(difference) = sylvia_base::testing::json_difference(&got, &run["outcome"]) {
                failures.push(format!("{label}: {difference}"));
            }
            if let Some(difference) =
                sylvia_base::testing::json_difference(&storage_tree(&storage, &files), &run["tree"])
            {
                failures.push(format!("{label} storage: {difference}"));
            }
            let mut candidates: Vec<(String, String, String)> = context
                .store
                .call(|store| store.package_candidates())
                .await
                .unwrap()
                .iter()
                .map(|item| {
                    (
                        item.candidate_id().to_owned(),
                        item.closure_sha256().to_owned(),
                        item.status().as_str().to_owned(),
                    )
                })
                .collect();
            candidates.sort();
            let candidates = Json::Array(
                candidates
                    .into_iter()
                    .map(|(id, closure, status)| serde_json::json!([id, closure, status]))
                    .collect(),
            );
            if let Some(difference) = sylvia_base::testing::json_difference(&candidates, &run["candidates"]) {
                failures.push(format!("{label} candidates: {difference}"));
            }
        }
        for (index, export) in record["exports"].as_array().unwrap().iter().enumerate() {
            let got = match sylvia_app::plan_bundle::export_plan_bundle(
                &storage,
                export["artifact_id"].as_str().unwrap(),
                export["expected_content_sha256"].as_str().unwrap(),
                &bundled,
            ) {
                Ok((archive, receipt)) => {
                    serde_json::json!({"ok": receipt, "archive_sha256": sylvia_base::hash::sha256_hex(&archive)})
                }
                Err(error) => domain_failure(&error),
            };
            if let Some(difference) = sylvia_base::testing::json_difference(&got, &export["outcome"]) {
                failures.push(format!("export #{index}: {difference}"));
            }
        }
        for inspection in record["inspections"].as_array().unwrap() {
            let name = inspection["source"].as_str().unwrap();
            let got = match sylvia_app::import_repair::inspect_repair_source(&files.join(name), &bundled) {
                Ok(found) => serde_json::json!({"ok": {
                    "kind": found.kind.as_str(),
                    "source_sha256": found.source_sha256,
                    "file_name": found.file_name,
                    "identity": found.identity,
                    "diagnostics": found.diagnostics.iter().map(|item| Json::Object(item.as_payload())).collect::<Vec<_>>(),
                }}),
                Err(error) => domain_failure(&error),
            };
            if let Some(difference) = sylvia_base::testing::json_difference(&got, &inspection["outcome"]) {
                failures.push(format!("inspect {name}: {difference}"));
            }
        }
        drop(context);
        tokio::task::spawn_blocking(move || thread.join()).await.unwrap();
        let _ = std::fs::remove_dir_all(folder);
        assert!(failures.is_empty(), "{} Plan bundle steps differ:\n{}", failures.len(), failures.join("\n"));
    }
}
