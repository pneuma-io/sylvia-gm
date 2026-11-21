//! Databases the Python store made, opened by the Rust store: each must reach exactly the
//! state, or the refusal, that `tests/vectors/schema_fixtures.json` records for Python.

mod common;

use flate2::read::GzDecoder;
use std::io::Read;
use std::path::Path;
use sylvia_base::Json;
use sylvia_store::{AuthorityStore, StoreResult};

fn fixture_bytes(name: &str) -> Vec<u8> {
    let path =
        Path::new(env!("CARGO_MANIFEST_DIR")).join(format!("tests/vectors/fixtures/{name}.sqlite3.gz"));
    let mut bytes = Vec::new();
    GzDecoder::new(std::fs::File::open(&path).expect("fixture")).read_to_end(&mut bytes).expect("gzip");
    bytes
}

/// Python's recorded outcome against what Rust did, with the full schema on a mismatch.
fn compare(label: &str, expected: &Json, folder: &Path, result: StoreResult<()>, schemas: &Json) {
    match (expected.get("error"), result) {
        (Some(error), Err(got)) => {
            let coded = match got {
                sylvia_store::StoreError::Coded(coded) => coded,
                other => panic!("{label}: expected {error}, Rust failed with {other:?}"),
            };
            assert_eq!(error["type"], "SchemaIdentityError", "{label}");
            assert_eq!(coded.error_type, Some("SchemaIdentityError"), "{label}");
            assert_eq!(coded.code, "schema_identity_mismatch", "{label}");
            assert_eq!(coded.message, error["message"].as_str().unwrap(), "{label}");
        }
        (Some(error), Ok(())) => panic!("{label}: Python refused with {error}, Rust accepted"),
        (None, Err(got)) => panic!("{label}: Python succeeded, Rust failed: {got:?}"),
        (None, Ok(())) => {
            let got = common::state(folder);
            let want = &expected["state"];
            if got["schema"] != want["schema"] {
                let connection = rusqlite::Connection::open(folder.join("authority.sqlite3")).unwrap();
                let rows = common::schema_rows(&connection);
                let want_rows = schemas[want["schema"].as_str().unwrap()].as_array().unwrap();
                for (index, row) in rows.iter().enumerate() {
                    if want_rows.get(index) != Some(row) {
                        panic!(
                            "{label}: schema row {index} differs\n rust:   {row}\n python: {:?}",
                            want_rows.get(index)
                        );
                    }
                }
                panic!("{label}: schema has {} rows, Python {}", rows.len(), want_rows.len());
            }
            assert_eq!(&got, want, "{label}");
        }
    }
}

fn place(name: &str, stage: &str) -> std::path::PathBuf {
    let folder = common::work_folder(&format!("fixture-{}-{stage}", name.replace('/', "-")));
    std::fs::write(folder.join("authority.sqlite3"), fixture_bytes(name)).expect("write fixture");
    folder
}

fn open(folder: &Path) -> StoreResult<()> {
    let mut store = AuthorityStore::open_for_application_owner(&folder.join("authority.sqlite3"), None)?;
    store.close();
    Ok(())
}

fn ensure_campaign(folder: &Path) -> StoreResult<()> {
    let mut store = AuthorityStore::open_for_application_owner(&folder.join("authority.sqlite3"), None)?;
    let connection = store.connect()?;
    connection.execute_batch("BEGIN IMMEDIATE")?;
    let result = store.ensure_campaign_schema(&connection);
    if result.is_ok() {
        connection.execute_batch("COMMIT")?;
    } else {
        connection.execute_batch("ROLLBACK")?;
    }
    drop(connection);
    store.close();
    result
}

#[test]
fn every_python_fixture_opens_and_advances_as_in_python() {
    let record = common::read_json("tests/vectors/schema_fixtures.json");
    let schemas = &record["schemas"];
    let fixtures = record["fixtures"].as_object().unwrap();
    assert_eq!(fixtures.len(), 19);
    for (name, entry) in fixtures {
        // The fixture bytes are what the generator recorded as `made`.
        let made = place(name, "made");
        assert_eq!(common::state(&made), entry["made"], "{name}: fixture bytes");

        let opened = place(name, "open");
        compare(&format!("{name} open"), &entry["open"], &opened, open(&opened), schemas);

        let advanced = place(name, "campaign");
        compare(
            &format!("{name} campaign"),
            &entry["campaign"],
            &advanced,
            ensure_campaign(&advanced),
            schemas,
        );
    }
}

/// The SQLite version that last wrote a database: bytes 96 to 99 of the header.
fn written_by(bytes: &[u8]) -> u32 {
    u32::from_be_bytes([bytes[96], bytes[97], bytes[98], bytes[99]])
}

#[test]
fn databases_written_by_linux_python_sqlite_3_45_1_open_with_the_bundled_library() {
    // The Linux reference environment (Ubuntu 24.04's CPython 3.12.3) ships SQLite 3.45.1; the
    // Rust build bundles 3.49.1. These two fixtures were made on Linux by the same generator,
    // and must reach the same states as Python recorded for the Windows-made ones.
    assert_eq!(written_by(&fixture_bytes("v3")), 3_049_001, "Windows fixtures are 3.49.1");
    let record = common::read_json("tests/vectors/schema_fixtures.json");
    let schemas = &record["schemas"];
    for name in ["v3", "v12"] {
        let entry = &record["fixtures"][name];
        let linux = format!("sqlite-3.45.1/{name}");
        assert_eq!(written_by(&fixture_bytes(&linux)), 3_045_001, "{linux} was written by SQLite 3.45.1");

        let made = place(&linux, "made");
        assert_eq!(common::state(&made), entry["made"], "{linux}: fixture bytes");
        let opened = place(&linux, "open");
        compare(&format!("{linux} open"), &entry["open"], &opened, open(&opened), schemas);
        let advanced = place(&linux, "campaign");
        compare(
            &format!("{linux} campaign"),
            &entry["campaign"],
            &advanced,
            ensure_campaign(&advanced),
            schemas,
        );
    }
}

#[test]
fn a_fresh_folder_reaches_the_python_fresh_state() {
    let record = common::read_json("tests/vectors/schema_fixtures.json");
    let folder = common::work_folder("fresh-vs-fixture");
    let result = (|| {
        let mut store = AuthorityStore::open_for_application_owner(&folder.join("authority.sqlite3"), None)?;
        assert!(store.created_new_database());
        store.prepare_new_campaign_storage()?;
        assert!(!store.created_new_database());
        store.close();
        Ok(())
    })();
    compare("fresh", &record["fresh"], &folder, result, &record["schemas"]);
}
