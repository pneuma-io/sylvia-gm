//! A database the Rust store creates on an empty folder against the Python owner's `startup`
//! recordings: same `sqlite_master`, pragmas, `schema_migrations` and
//! `authority_schema_extensions`, and the same pre-migration copy in the file list.

mod common;

use rusqlite::{Connection, OpenFlags};
use sylvia_base::testing::assert_same_json;
use sylvia_base::{Json, JsonMap};
use sylvia_store::{AuthorityStore, StoreHandle};

fn recording(platform: &str) -> Json {
    common::read_json(&format!("../sylvia-compare/recordings/{platform}/startup.recording.json"))
}

/// Rust's database in the comparison tool's dump shape, for the parts the schema decides.
fn rust_dump(folder: &std::path::Path) -> (Vec<Json>, JsonMap, Json, Json) {
    let connection =
        Connection::open_with_flags(folder.join("authority.sqlite3"), OpenFlags::SQLITE_OPEN_READ_ONLY)
            .unwrap();
    let schema = common::schema_rows(&connection)
        .into_iter()
        .map(|row| {
            let mut entry = JsonMap::new();
            entry.insert("type".into(), row[0].clone());
            entry.insert("name".into(), row[1].clone());
            entry.insert("table".into(), row[2].clone());
            entry.insert("sql".into(), row[3].clone());
            Json::Object(entry)
        })
        .collect();
    let mut pragmas = JsonMap::new();
    for pragma in ["user_version", "application_id", "journal_mode", "page_size", "auto_vacuum", "encoding"] {
        pragmas.insert(pragma.into(), common::rows(&connection, &format!("PRAGMA {pragma}"))[0][0].clone());
    }
    let table = |name: &str, columns: &[&str]| {
        Json::Array(
            common::rows(&connection, &format!("SELECT {} FROM {name} ORDER BY rowid", columns.join(", ")))
                .into_iter()
                .map(|row| {
                    let mut record = JsonMap::new();
                    for (index, column) in columns.iter().enumerate() {
                        record.insert((*column).into(), row[index].clone());
                    }
                    Json::Object(record)
                })
                .collect(),
        )
    };
    let migrations = table("schema_migrations", &["version", "applied_name", "sql_sha256"]);
    let extensions = table(
        "authority_schema_extensions",
        &["extension_name", "extension_sha256", "predecessor_migration_name", "predecessor_sql_sha256"],
    );
    (schema, pragmas, migrations, extensions)
}

#[test]
fn a_fresh_rust_database_matches_both_startup_recordings() {
    let folder = common::work_folder("fresh-vs-recording");
    let database = folder.join("authority.sqlite3");
    let (handle, thread) =
        StoreHandle::spawn(move || AuthorityStore::open_for_application_owner(&database, None))
            .expect("store opens on its thread");
    handle.call_blocking(|store| store.prepare_new_campaign_storage()).expect("fresh migrations finish");
    drop(handle);
    thread.join();

    let (schema, pragmas, migrations, extensions) = rust_dump(&folder);
    for platform in ["windows", "linux"] {
        let recording = recording(platform);
        let database = &recording["database"];
        let want_schema = database["schema"].as_array().unwrap();
        assert_eq!(schema.len(), want_schema.len(), "{platform}: schema entries");
        for (got, want) in schema.iter().zip(want_schema) {
            assert_same_json(got, want, format!("{platform}: schema entry"));
        }
        assert_same_json(
            &Json::Object(pragmas.clone()),
            &database["pragmas"],
            format!("{platform}: pragmas"),
        );
        assert_eq!(
            migrations, database["tables"]["schema_migrations"]["rows"],
            "{platform}: schema_migrations"
        );
        assert_eq!(
            extensions, database["tables"]["authority_schema_extensions"]["rows"],
            "{platform}: authority_schema_extensions"
        );
        let files: Vec<&str> =
            recording["files"].as_array().unwrap().iter().filter_map(|file| file["path"].as_str()).collect();
        assert!(files.contains(&"migration-sources/authority.pre-migration-v3.sqlite3"), "{platform}");
        assert!(files.contains(&"authority.sqlite3.authority-owner.owner.lock"), "{platform}");
    }
    assert!(folder.join("migration-sources/authority.pre-migration-v3.sqlite3").is_file());
    assert_eq!(std::fs::read(folder.join("authority.sqlite3.authority-owner.owner.lock")).unwrap(), b"0");
    let leftovers: Vec<String> = std::fs::read_dir(folder.join("migration-sources"))
        .unwrap()
        .map(|entry| entry.unwrap().file_name().to_string_lossy().into_owned())
        .collect();
    assert_eq!(leftovers, ["authority.pre-migration-v3.sqlite3"], "no temporary or sidecar files remain");
}

#[test]
fn the_store_thread_holds_the_owner_lock_until_it_stops() {
    let folder = common::work_folder("thread-lock");
    let database = folder.join("authority.sqlite3");
    let first = database.clone();
    let (handle, thread) =
        StoreHandle::spawn(move || AuthorityStore::open_for_application_owner(&first, None)).unwrap();
    let second =
        AuthorityStore::open_for_application_owner(&database, None).expect_err("second owner refused");
    assert_eq!(second.to_string(), "The Sylvia GM SQLite authority already has a live owner");
    let runtime = tokio::runtime::Builder::new_current_thread().build().unwrap();
    let active = runtime.block_on(handle.call(|store| store.campaign_schema_is_active())).unwrap();
    assert!(!active, "a fresh database stays at schema 3 until prepared");
    drop(handle);
    thread.join();
    let mut reopened = AuthorityStore::open_for_application_owner(&database, None).expect("lock released");
    assert!(!reopened.created_new_database());
    reopened.close();
}
