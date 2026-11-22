//! Campaign rename, archive, restore and trash through the store handle, against Python's
//! `AuthorityOwner`.
//!
//! The paired run (`tests/vectors/lifecycle`, written by `generate_lifecycle_runs.py`) replays
//! the five lifecycle operations on the session-page profile plus three guided Drafts: identity
//! refusals, a missing campaign, a stale revision, busy campaign work, exact and conflicting
//! replays, every transition with its session handling, both commit seams, and the Drafts' unbound
//! basis, ambiguous sessions and active child work. Each outcome must be Python's, and the
//! database afterwards must match Python's.

use flate2::read::GzDecoder;
use serde_json::json;
use std::io::Read;
use std::path::Path;
use std::sync::{Arc, Mutex};
use sylvia_app::authority::lifecycle;
use sylvia_base::Json;
use sylvia_domain::authority::CampaignLifecycleResult;
use sylvia_store::store::Faults;
use sylvia_store::{AuthorityStore, StoreError, StoreHandle, StoreResult};

/// A fresh, empty folder under Cargo's per-test temporary root.
fn work_folder(name: &str) -> std::path::PathBuf {
    let folder = Path::new(env!("CARGO_TARGET_TMPDIR")).join("sylvia-app").join(name);
    // A folder left by an earlier run must go, or its files would look like this run's.
    if let Err(error) = std::fs::remove_dir_all(&folder) {
        assert_eq!(
            error.kind(),
            std::io::ErrorKind::NotFound,
            "cannot clear the old work folder {}: {error}",
            folder.display()
        );
    }
    std::fs::create_dir_all(&folder).expect("work folder");
    folder
}

fn read_json(relative: &str) -> Json {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join(relative);
    serde_json::from_str(&std::fs::read_to_string(&path).expect("vector file")).expect("valid JSON")
}

fn gunzip(name: &str) -> Vec<u8> {
    let path =
        Path::new(env!("CARGO_MANIFEST_DIR")).join(format!("tests/vectors/lifecycle/{name}.sqlite3.gz"));
    let mut bytes = Vec::new();
    GzDecoder::new(std::fs::File::open(&path).unwrap()).read_to_end(&mut bytes).unwrap();
    bytes
}

fn text<'a>(op: &'a Json, key: &str) -> &'a str {
    op[key].as_str().unwrap_or_else(|| panic!("{key} in {op}"))
}

fn result_json(result: &CampaignLifecycleResult) -> Json {
    json!({
        "campaign_id": result.campaign_id, "lifecycle": result.lifecycle.as_str(),
        "writer": result.writer.as_str(), "resulting_revision": result.resulting_revision,
        "session_id": result.session_id,
    })
}

async fn run(handle: &StoreHandle, op: &Json) -> StoreResult<Json> {
    let (operation_id, campaign_id) = (text(op, "operation_id"), text(op, "campaign_id"));
    let revision = op["expected_revision"].as_i64().unwrap();
    let session = |key: &str| op[key].as_str();
    let result = match text(op, "op") {
        "rename_campaign" => {
            lifecycle::rename_campaign(handle, operation_id, campaign_id, text(op, "display_name"), revision)
                .await
        }
        "archive_campaign" => {
            lifecycle::archive_campaign(
                handle,
                operation_id,
                campaign_id,
                revision,
                session("designated_session_id"),
            )
            .await
        }
        "restore_archived_campaign" => {
            lifecycle::restore_archived_campaign(
                handle,
                operation_id,
                campaign_id,
                revision,
                session("session_id"),
            )
            .await
        }
        "trash_campaign" => {
            lifecycle::trash_campaign(
                handle,
                operation_id,
                campaign_id,
                revision,
                session("designated_session_id"),
            )
            .await
        }
        "restore_trashed_campaign" => {
            lifecycle::restore_trashed_campaign(handle, operation_id, campaign_id, revision).await
        }
        other => panic!("unknown operation {other}"),
    }?;
    Ok(result_json(&result))
}

fn check(label: &str, got: StoreResult<Json>, want: &Json) {
    match (got, want.get("error")) {
        (Ok(value), None) => assert_eq!(value, want["ok"], "{label}"),
        // The generator's own fault class, raised from a commit seam.
        (Err(StoreError::Other(message)), Some(kind)) if kind == "Fault" => {
            assert_eq!(message, want["message"].as_str().unwrap(), "{label}: fault");
        }
        (Err(error), Some(kind)) => {
            let Some(coded) = error.rejection() else {
                panic!("{label}: Rust fault {error:?}, Python {want}")
            };
            assert_eq!(coded.error_type, kind.as_str(), "{label}: type");
            assert_eq!(coded.code, want["code"].as_str().unwrap_or("command_rejected"), "{label}: code");
            assert_eq!(coded.message, want["message"].as_str().unwrap(), "{label}: message");
        }
        (got, _) => panic!("{label}: Rust {got:?}, Python {want}"),
    }
}

/// Faults that raise from the seam `armed` names (`"before"` or `"after"`), as the generator's do.
fn faults(armed: &Arc<Mutex<Option<String>>>) -> Faults {
    let seam = |name: &'static str| {
        let armed = Arc::clone(armed);
        Box::new(move || -> StoreResult<()> {
            if armed.lock().unwrap().as_deref() == Some(name) {
                return Err(StoreError::Other(format!("{name} lifecycle commit fault")));
            }
            Ok(())
        }) as Box<dyn Fn() -> StoreResult<()> + Send>
    };
    Faults {
        before_lifecycle_commit: Some(seam("before")),
        after_lifecycle_commit: Some(seam("after")),
        ..Faults::default()
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn lifecycle_operations_match_python() {
    let record = read_json("tests/vectors/lifecycle/lifecycle_runs.json");
    let folder = work_folder("lifecycle-runs");
    let database = folder.join("authority.sqlite3");
    std::fs::write(&database, gunzip("seed")).unwrap();
    let armed: Arc<Mutex<Option<String>>> = Arc::new(Mutex::new(None));
    let (path, seams) = (database.clone(), faults(&armed));
    let (handle, thread) = StoreHandle::spawn(move || {
        let mut store = AuthorityStore::open_for_application_owner(&path, None)?;
        store.set_faults(seams);
        Ok(store)
    })
    .unwrap();
    for (index, op) in record["runs"].as_array().unwrap().iter().enumerate() {
        let label = format!("#{index} {} {}", op["op"], op["operation_id"]);
        *armed.lock().unwrap() = op["fault"].as_str().map(str::to_owned);
        // Running campaign work, as `_begin_campaign_work` registers it for a turn.
        let busy = if op["busy"].as_bool() == Some(true) {
            let campaign_id = text(op, "campaign_id").to_owned();
            let handle = &handle;
            Some(
                handle
                    .lanes()
                    .work
                    .begin(&campaign_id.clone(), || async move {
                        handle.call(move |store| store.campaign_lifecycle(&campaign_id)).await
                    })
                    .await
                    .unwrap(),
            )
        } else {
            None
        };
        let outcome = run(&handle, op).await;
        drop(busy);
        *armed.lock().unwrap() = None;
        check(&label, outcome, &op["outcome"]);
    }
    drop(handle);
    tokio::task::spawn_blocking(move || thread.join()).await.unwrap();
    let rules = sylvia_compare::diff::Rules::defaults();
    let python = folder.join("python.sqlite3");
    std::fs::write(&python, gunzip("lifecycle_runs")).unwrap();
    let dump = |path: &Path| {
        sylvia_compare::diff::normalise(&sylvia_compare::dbdump::dump(path).unwrap(), &rules).unwrap()
    };
    let differences = sylvia_compare::diff::compare(&dump(&python), &dump(&database), 20);
    assert!(differences.is_empty(), "database differs from Python's: {differences:#?}");
}
