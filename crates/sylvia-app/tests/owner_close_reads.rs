//! Lane 5's guarded owner reads on a closed owner, against Python's
//! (`vectors/generate_owner_close_reads.py`): each is refused with the closed owner's
//! `RuntimeError`, except a text read of an export-only path, whose check comes first.

use flate2::read::GzDecoder;
use serde_json::json;
use std::io::Read;
use std::path::Path;
use sylvia_app::authority::owner_close::close_owner;
use sylvia_app::authority::package_candidates::{
    compare_package_candidates, inspect_package_candidate, package_candidate_text,
};
use sylvia_app::authority::snapshots::desktop_snapshot;
use sylvia_base::testing::assert_same_json;
use sylvia_base::Json;
use sylvia_store::owner_lifecycle::OWNER_CLOSED;
use sylvia_store::{AuthorityStore, StoreError, StoreHandle, StoreResult};

fn outcome<T>(result: StoreResult<T>) -> Json {
    match result {
        Ok(_) => json!({"ok": true}),
        Err(StoreError::Other(message)) if message == OWNER_CLOSED => {
            json!({"raise": "RuntimeError", "code": null, "message": message})
        }
        Err(error) => match error.rejection() {
            Some(coded) => json!({
                "raise": coded.error_type.unwrap_or("CommandRejected"),
                "code": coded.code,
                "message": coded.message,
            }),
            None => json!({"raise": "RustStoreError", "code": null, "message": error.to_string()}),
        },
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn closed_owner_refuses_lane_five_reads_as_python_does() {
    let here = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/vectors");
    let vectors: Json =
        serde_json::from_str(&std::fs::read_to_string(here.join("owner_close_reads.json")).unwrap()).unwrap();
    let folder = std::env::temp_dir().join(format!("sylvia-owner-close-reads-{}", std::process::id()));
    if folder.exists() {
        std::fs::remove_dir_all(&folder)
            .unwrap_or_else(|error| panic!("could not clear {}: {error}", folder.display()));
    }
    std::fs::create_dir_all(&folder).unwrap();
    let database = folder.join("authority.sqlite3");
    let mut bytes = Vec::new();
    GzDecoder::new(std::fs::File::open(here.join("owner_close/base.sqlite3.gz")).unwrap())
        .read_to_end(&mut bytes)
        .unwrap();
    std::fs::write(&database, bytes).unwrap();
    let path = database.clone();
    let (store, thread) =
        StoreHandle::spawn(move || AuthorityStore::open_for_application_owner(&path, None)).unwrap();
    close_owner(&store).await.unwrap();
    let found = vec![
        outcome(inspect_package_candidate(&store, "candidate-missing").await),
        outcome(package_candidate_text(&store, "candidate-missing", "pkg", "data/archive.bin").await),
        outcome(package_candidate_text(&store, "candidate-missing", "pkg", "notes.md").await),
        outcome(compare_package_candidates(&store, "left", "right").await),
        outcome(desktop_snapshot(&store, "campaign", false).await),
        outcome(desktop_snapshot(&store, "campaign", true).await),
    ];
    let cases = vectors["cases"].as_array().unwrap();
    assert_eq!(cases.len(), found.len());
    for (case, found) in cases.iter().zip(&found) {
        assert_same_json(found, &case["outcome"], case["name"].as_str().unwrap());
    }
    drop(store);
    tokio::task::spawn_blocking(move || thread.join()).await.unwrap();
    let _ = std::fs::remove_dir_all(&folder);
}
