//! The launch handshake against `tests/vectors/handshake_vectors.json` and lane 5's two vector
//! files, the second-instance probe and the store half of `acquire_owner`.

mod common;

use sylvia_base::testing::assert_same_json;
use sylvia_base::Json;
use sylvia_store::launch::{self, LaunchHandshake};
use sylvia_store::owner_lock::{authority_lock_identity, OwnerLock};

fn assert_outcome(label: &str, got: Result<Json, sylvia_base::CodedError>, want: &Json) {
    match (got, want.get("error")) {
        (Ok(value), None) => assert_same_json(&value, &want["ok"], label),
        (Err(error), Some(kind)) => {
            assert_eq!(error.error_type, kind.as_str(), "{label}: error type");
            assert_eq!(error.message, want["message"].as_str().unwrap(), "{label}: message");
        }
        (got, _) => panic!("{label}: Rust gave {got:?}, Python {want}"),
    }
}

#[test]
fn derive_owner_response_matches_python() {
    let vectors = common::read_json("tests/vectors/handshake_vectors.json");
    assert_eq!(vectors["transcript"].as_str().unwrap().as_bytes(), launch::HANDSHAKE_TRANSCRIPT);
    for case in vectors["derive"].as_array().unwrap() {
        let secret = case["secret"].as_str().unwrap();
        let got = launch::derive_owner_response(secret).map(Json::String);
        assert_outcome(&format!("derive {secret:?}"), got, case);
    }
}

#[test]
fn lane_5_vectors_still_hold() {
    let read = |name: &str| {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join(format!("../sylvia-owner/tests/vectors/{name}"));
        std::fs::read_to_string(path).unwrap().trim().to_owned()
    };
    assert_eq!(launch::derive_owner_response(&"00".repeat(32)).unwrap(), read("handshake_zero.txt"));
    assert_eq!(
        launch::derive_owner_response(&"0123456789abcdef".repeat(4)).unwrap(),
        read("handshake_pattern.txt")
    );
}

#[test]
fn verify_matches_python() {
    let vectors = common::read_json("tests/vectors/handshake_vectors.json");
    let secret: [u8; 32] =
        hex::decode(vectors["verify_secret"].as_str().unwrap()).unwrap().try_into().unwrap();
    for case in vectors["verify"].as_array().unwrap() {
        let response = case["response"].as_str().unwrap();
        let mut handshake = LaunchHandshake::sealed_for_vectors(secret);
        let got = handshake.verify(response).map(|()| Json::Null);
        assert_outcome(&format!("verify {response:?}"), got, case);
        assert!(handshake.consumed(), "erased after any outcome");
    }
}

#[test]
fn lifecycle_misuse_matches_python() {
    let vectors = common::read_json("tests/vectors/handshake_vectors.json");
    let lifecycle = &vectors["lifecycle"];
    let unit = |result: Result<(), sylvia_base::CodedError>| result.map(|()| Json::Null);

    let mut issued_twice = LaunchHandshake::new();
    let first = issued_twice.issue().unwrap();
    assert!(issued_twice.issued());
    assert_eq!(first.secret_hex.len(), 64);
    assert_eq!(launch::derive_owner_response(&first.secret_hex).unwrap(), first.expected_response_hex);
    assert_outcome(
        "issue after issue",
        issued_twice.issue().map(|_| Json::Null),
        &lifecycle["issue_after_issue"],
    );

    let mut consumed = LaunchHandshake::new();
    let credential = consumed.issue().unwrap();
    consumed.verify(&credential.expected_response_hex).unwrap();
    assert!(consumed.consumed());
    assert_outcome(
        "issue after verify",
        consumed.issue().map(|_| Json::Null),
        &lifecycle["issue_after_verify"],
    );
    assert_outcome(
        "verify after verify",
        unit(consumed.verify(&credential.expected_response_hex)),
        &lifecycle["verify_after_verify"],
    );
    assert_outcome(
        "verify without issue",
        unit(LaunchHandshake::new().verify("00")),
        &lifecycle["verify_without_issue"],
    );
    assert_eq!(format!("{:?}", LaunchHandshake::new()), lifecycle["repr"].as_str().unwrap());
}

#[test]
fn the_probe_sees_a_held_lock_and_never_keeps_one() {
    let folder = common::work_folder("launch-probe");
    let database = folder.join("authority.sqlite3");
    let free = launch::probe_owner_live(&database).unwrap();
    assert!(!free.owner_live);
    assert_eq!(free.reason, "owner lock is free");
    assert_eq!(free.lock_path.file_name().unwrap(), "authority.sqlite3.authority-owner.owner.lock");
    assert_eq!(std::fs::read(&free.lock_path).unwrap(), b"0", "an empty lock file gets Python's 0");
    assert!(!database.exists(), "the probe never opens the database");

    let lock = OwnerLock::acquire(&authority_lock_identity(&database)).unwrap();
    let held = launch::probe_owner_live(&database).unwrap();
    assert!(held.owner_live);
    assert_eq!(held.reason, "owner lock is held by a live process");
    drop(lock);
    assert!(!launch::probe_owner_live(&database).unwrap().owner_live, "free again after release");
    let _again = OwnerLock::acquire(&authority_lock_identity(&database)).expect("the probe kept no lock");
}

#[test]
fn the_recover_hint_is_written_beside_the_lock() {
    let folder = common::work_folder("launch-hint");
    let path =
        launch::write_recover_hint(&folder.join("nested").join("authority.sqlite3"), "Restart Sylvia GM.")
            .unwrap();
    assert_eq!(path.file_name().unwrap(), "authority.sqlite3.authority-owner.recover.txt");
    assert_eq!(std::fs::read_to_string(path).unwrap(), "Restart Sylvia GM.");
}

#[test]
fn acquire_owner_locks_before_it_issues() {
    let folder = common::work_folder("launch-acquire");
    let database = folder.join("authority.sqlite3");
    let mut acquired = launch::acquire_owner(&database, None).unwrap();
    assert!(launch::probe_owner_live(&database).unwrap().owner_live, "the lock is held");
    let second = launch::acquire_owner(&database, None).expect_err("a second owner is refused");
    assert_eq!(second.to_string(), "The Sylvia GM SQLite authority already has a live owner");
    let response = launch::derive_owner_response(&acquired.credential.secret_hex).unwrap();
    assert_eq!(response, acquired.credential.expected_response_hex);
    acquired.handshake.verify(&response).unwrap();
    let created = acquired.store.call_blocking(|store| Ok(store.created_new_database())).unwrap();
    assert!(created, "a fresh profile awaits prepare_new_campaign_storage");
    drop(acquired.store);
    acquired.thread.join();
    assert!(!launch::probe_owner_live(&database).unwrap().owner_live, "released when the store stops");
}
