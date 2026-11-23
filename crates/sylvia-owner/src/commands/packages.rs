//! `interfaces/commands/packages.py`: the package commands. Only `handle_export_package_candidate`
//! is ported; installing and inspecting candidates come with their owner operations.

use super::campaigns::{required, required_artifact_id, resolve_strict, store_error};
use crate::dispatch::DispatchContext;
use sylvia_base::{CodedError, CodedResult, Json, JsonMap};

/// An exception Python's handler does not catch: the owner's internal-error reply, with its class.
fn os_error(error: &std::io::Error) -> CodedError {
    CodedError::new("internal_error", error.to_string()).with_error_type("OSError")
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
}
