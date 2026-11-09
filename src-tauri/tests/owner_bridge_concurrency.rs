use serde_json::json;
use std::path::PathBuf;
use std::sync::{Arc, Barrier};
use std::time::{SystemTime, UNIX_EPOCH};
use support::await_clean_shutdown;
use sylvia_gm::OwnerBridge;

mod support;

#[test]
fn rust_bridge_demultiplexes_concurrent_owner_replies() {
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock before epoch")
        .as_nanos();
    let root = std::env::temp_dir().join(format!(
        "sylvia-gm-owner-concurrency-{}-{nonce}",
        std::process::id()
    ));
    std::fs::create_dir_all(&root).expect("create test directory");
    let database = root.join("authority.sqlite3");
    let retained = root.join("retained-synthetic-data.txt");
    std::fs::write(&retained, "keep this synthetic data").expect("write retained data");
    let python = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("repository root")
        .join(".venv")
        .join("Scripts")
        .join("python.exe");

    let bridge = Arc::new(
        OwnerBridge::spawn_python(&python, &database)
            .expect("Rust host launches verified Python owner"),
    );
    let packages = bridge
        .request("package_candidates", json!({}))
        .expect("actual bundled package catalog");
    assert!(packages.to_string().contains("sylvia.srd-lite.bg3"));
    let barrier = Arc::new(Barrier::new(9));
    let mut workers = Vec::new();
    for _ in 0..8 {
        let worker_bridge = Arc::clone(&bridge);
        let worker_barrier = Arc::clone(&barrier);
        workers.push(std::thread::spawn(move || {
            worker_barrier.wait();
            worker_bridge
                .request("campaign_index", json!({}))
                .expect("correlated concurrent reply")
        }));
    }
    barrier.wait();
    for worker in workers {
        assert_eq!(worker.join().expect("request worker panicked"), json!([]));
    }
    await_clean_shutdown(&bridge);
    bridge.shutdown().expect("clean concurrent bridge shutdown");
    drop(bridge);

    let reopened = OwnerBridge::spawn_python(&python, &database)
        .expect("collected owner releases the same database for reopening");
    assert_eq!(
        reopened.request("campaign_index", json!({})).unwrap(),
        json!([])
    );
    assert_eq!(
        reopened.request("package_candidates", json!({})).unwrap(),
        packages,
        "Lite package state survives close and reopen"
    );
    await_clean_shutdown(&reopened);
    drop(reopened);
    assert_eq!(
        std::fs::read_to_string(&retained).unwrap(),
        "keep this synthetic data"
    );
    assert_eq!(
        root.canonicalize().unwrap().parent(),
        Some(std::env::temp_dir().canonicalize().unwrap().as_path()),
        "remove only this run's contained synthetic directory"
    );
    std::fs::remove_dir_all(&root).expect("remove concurrency test directory");
}
