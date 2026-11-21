//! `backup_database` against what the Python store did for the same targets
//! (`tests/vectors/backup_scenarios.<platform>.json`, written by
//! `generate_backup_scenarios.py` on each platform).

mod common;

use rusqlite::{Connection, OpenFlags};
use std::path::{Path, PathBuf};
use sylvia_base::{Json, JsonMap};
use sylvia_store::{AuthorityStore, StoreError};

const PLATFORM: &str = if cfg!(windows) { "windows" } else { "linux" };

#[cfg(windows)]
fn unused_drive() -> Option<PathBuf> {
    "ZYXWVUTSRQPONMLKJIHGFE"
        .chars()
        .map(|letter| PathBuf::from(format!("{letter}:\\")))
        .find(|root| !root.exists())
}

/// The generator's `scenarios`, in the same order.
fn scenarios(data: &Path) -> Vec<(&'static str, PathBuf)> {
    let backups = data.join("backups");
    let mut cases = vec![
        ("new_target", backups.join("copy.sqlite3")),
        ("existing_target_replaced", backups.join("existing.sqlite3")),
        ("parent_missing", data.join("missing").join("copy.sqlite3")),
        ("target_is_directory", backups.join("directory.sqlite3")),
        ("target_is_source", data.join("authority.sqlite3")),
        ("target_is_source_wal", data.join("authority.sqlite3-wal")),
        ("target_is_source_lock", data.join("authority.sqlite3.authority-owner.owner.lock")),
        ("destination_owner_lock", backups.join("locked.sqlite3")),
        ("parent_is_file", backups.join("plain.txt").join("copy.sqlite3")),
    ];
    #[cfg(windows)]
    {
        cases.push(("name_with_colon", backups.join("copy:stream.sqlite3")));
        cases.push(("unc_admin_share", PathBuf::from(r"\\localhost\c$\sylvia-backup-scenario.sqlite3")));
        if let Some(drive) = unused_drive() {
            cases.push(("unmapped_drive", drive.join("copy.sqlite3")));
        }
    }
    #[cfg(not(windows))]
    {
        cases.push(("target_is_symlink", backups.join("link.sqlite3")));
        cases.push(("parent_is_symlink", data.join("linked").join("copy.sqlite3")));
        cases.push(("tmpfs_volume", PathBuf::from("/dev/shm/sylvia-backup-scenario.sqlite3")));
    }
    cases
}

/// The generator's `prepare`.
fn prepare(data: &Path, name: &str, target: &Path) {
    let backups = data.join("backups");
    std::fs::create_dir_all(&backups).unwrap();
    match name {
        "existing_target_replaced" => std::fs::write(target, b"old bytes").unwrap(),
        "target_is_directory" => std::fs::create_dir(target).unwrap(),
        "destination_owner_lock" => {
            let mut lock = target.file_name().unwrap().to_os_string();
            lock.push(".owner.lock");
            std::fs::write(target.with_file_name(lock), b"0").unwrap();
        }
        "parent_is_file" => std::fs::write(target.parent().unwrap(), b"plain").unwrap(),
        #[cfg(unix)]
        "target_is_symlink" => {
            std::fs::write(backups.join("real.sqlite3"), b"real").unwrap();
            std::os::unix::fs::symlink(backups.join("real.sqlite3"), target).unwrap();
        }
        #[cfg(unix)]
        "parent_is_symlink" => std::os::unix::fs::symlink(&backups, data.join("linked")).unwrap(),
        _ => {}
    }
}

fn copy_state(path: &Path) -> Json {
    let connection = Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY).unwrap();
    let mut state = JsonMap::new();
    let one =
        |query: &str| -> Json { connection.query_row(query, [], |row| row.get::<_, i64>(0)).unwrap().into() };
    state.insert("user_version".into(), one("PRAGMA user_version"));
    state.insert("tables".into(), one("SELECT count(*) FROM sqlite_master WHERE type = 'table'"));
    state.insert("migrations".into(), one("SELECT count(*) FROM schema_migrations"));
    Json::Object(state)
}

#[test]
fn every_backup_scenario_ends_as_it_does_in_python() {
    let record = common::read_json(&format!("tests/vectors/backup_scenarios.{PLATFORM}.json"));
    let expected = record["scenarios"].as_object().unwrap();
    let cases = scenarios(Path::new("probe"));
    assert_eq!(cases.len(), expected.len(), "same scenario list as the generator");
    for (name, _) in cases {
        let data = common::work_folder(&format!("backup-{name}"));
        let mut store =
            AuthorityStore::open_for_application_owner(&data.join("authority.sqlite3"), None).unwrap();
        store.prepare_new_campaign_storage().unwrap();
        let target = scenarios(&data).into_iter().find(|(case, _)| *case == name).unwrap().1;
        prepare(&data, name, &target);
        let outcome = store.backup_database(&target);
        store.close();
        let want = &expected[name];
        match outcome {
            Ok(()) => {
                assert_eq!(want["ok"], true, "{name}: Python failed with {want}");
                // List before opening the copy: reading a WAL database creates its sidecars.
                let mut siblings: Vec<String> = std::fs::read_dir(target.parent().unwrap())
                    .unwrap()
                    .map(|entry| entry.unwrap().file_name().to_string_lossy().into_owned())
                    .filter(|sibling| sibling.as_str() != target.file_name().unwrap().to_string_lossy())
                    .collect();
                siblings.sort();
                assert_eq!(serde_json::to_value(siblings).unwrap(), want["siblings"], "{name}: no leftovers");
                assert_eq!(copy_state(&target), want["copy"], "{name}: copy");
            }
            Err(StoreError::Backup(failure)) => {
                let coded = failure.to_coded();
                assert_eq!(want["ok"], false, "{name}: Python succeeded, Rust failed with {}", coded.code);
                assert_eq!(coded.code, want["code"].as_str().unwrap(), "{name}: code");
                assert_eq!(coded.message, want["message"].as_str().unwrap(), "{name}: message");
                assert_eq!(coded.error_type, Some("_BackupFailure"), "{name}: type");
            }
            Err(other) => panic!("{name}: not a backup failure: {other:?}"),
        }
    }
}
