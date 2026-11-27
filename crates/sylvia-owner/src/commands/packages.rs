//! `interfaces/commands/packages.py`: the package commands.

use super::campaigns::{required, required_artifact_id, resolve_strict, store_error};
use crate::dispatch::DispatchContext;
use sylvia_base::{CodedError, CodedResult, Json, JsonMap};

/// An exception Python's handler does not catch: the owner's internal-error reply, with its class.
fn os_error(error: &std::io::Error) -> CodedError {
    CodedError::new("internal_error", error.to_string()).with_error_type("OSError")
}

/// `handle_package_candidates`: every installed candidate, newest first
/// (`AuthorityOwner.package_candidates`).
pub async fn handle_package_candidates(ctx: &DispatchContext, _params: &JsonMap) -> CodedResult<Json> {
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
    sylvia_app::authority::package_candidates::package_candidate_text(&ctx.store, &candidate_id, &package_id, &path)
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
const PLAN_BUNDLE_BYTE_LIMIT: u64 = 68_419_584;

/// `OwnerProtocolError(message)`.
fn protocol(message: &str) -> CodedError {
    CodedError::rejected(message).with_error_type("OwnerProtocolError")
}

/// A branch whose Python function the port has not reached: `rust_port_pending`, naming it.
fn port_pending(what: &str) -> CodedError {
    CodedError::new(crate::dispatch::PORT_PENDING, format!("not yet ported: {what}"))
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

/// `handle_install_package_file`: the selected file, checked and size-bounded, installed under
/// `install_id`. A `.sylvia-gm-plan` bundle and the import-repair offer after a failed install
/// answer `rust_port_pending` until lane 1 ports `plan_bundle.import_plan_bundle` and
/// `import_repair_sessions.offer_import_repair`.
pub async fn handle_install_package_file(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let unavailable = || CodedError::rejected("Selected package file is unavailable");
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
    if is_plan {
        // `source.open("rb")`, then `import_plan_bundle(...)`.
        std::fs::File::open(&source).map_err(|_| unavailable())?;
        return Err(port_pending("plan_bundle.import_plan_bundle"));
    }
    let archive = std::fs::read(&source).map_err(|_| unavailable())?;
    // A missing `install_id` is an `OwnerProtocolError` (a `ValueError`) inside the install's
    // `try`; the repair offer reads it again, and that second error reaches the outer
    // `except ValueError`.
    let Ok(install_id) = required(params, "install_id") else {
        return Err(CodedError::rejected(
            "Content archive could not be admitted. Check its format and content, \
             then export a new copy using Store or Deflate ZIP compression.",
        ));
    };
    match install(ctx, archive, install_id).await {
        Ok(candidate) => Ok(candidate),
        // `except (AuthorityError, ValueError)`: Python offers an import repair first, and only
        // a source it cannot inspect re-raises the error.
        Err(sylvia_store::StoreError::Coded(_) | sylvia_store::StoreError::Other(_)) => {
            Err(port_pending("import_repair_sessions.offer_import_repair"))
        }
        Err(error) => Err(store_error(error)),
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

    /// The two branches lane 1 has not ported answer `rust_port_pending`, naming the Python
    /// function: a `.sylvia-gm-plan` file, and the import-repair offer after a failed file
    /// install (here the same install identity again). The `package-install` session covers
    /// everything Python and Rust both answer.
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn plan_bundles_and_repair_offers_answer_port_pending() {
        let folder = std::env::temp_dir().join(format!("sgp-pending-{}", std::process::id()));
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
        let (store, thread) = sylvia_store::StoreHandle::spawn(move || {
            sylvia_store::AuthorityStore::open_for_application_owner(&database, None)
        })
        .unwrap();
        let context = crate::dispatch::DispatchContext {
            owner_id: "package-install-test".into(),
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
        let plan = folder.join("starter.sylvia-gm-plan");
        std::fs::write(&plan, b"not read before the pending answer").unwrap();
        let archive = folder.join("riichi.sylvia-gm-package");
        std::fs::copy(workspace("rulesets/riichi-mahjong/riichi-mahjong-0.1.0.sylvia-gm-package"), &archive)
            .unwrap();
        let params = |path: &std::path::Path| {
            let mut params = JsonMap::new();
            params.insert("source_path".into(), path.to_string_lossy().into_owned().into());
            params.insert("install_id".into(), "install-riichi".into());
            params
        };
        let pending = |result: CodedResult<Json>| match result {
            Err(error) if error.code == crate::dispatch::PORT_PENDING => error.message,
            other => panic!("expected rust_port_pending, got {other:?}"),
        };
        assert_eq!(
            pending(handle_install_package_file(&context, &params(&plan)).await),
            "not yet ported: plan_bundle.import_plan_bundle"
        );
        let installed = handle_install_package_file(&context, &params(&archive)).await.unwrap();
        assert_eq!(installed["candidate_id"], "candidate-install-riichi");
        assert_eq!(
            pending(handle_install_package_file(&context, &params(&archive)).await),
            "not yet ported: import_repair_sessions.offer_import_repair"
        );
        drop(context);
        tokio::task::spawn_blocking(move || thread.join()).await.unwrap();
        let _ = std::fs::remove_dir_all(folder);
    }
}
