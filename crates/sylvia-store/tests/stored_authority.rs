//! `_verify_stored_authority` against Python (`tests/vectors/stored_authority`, written by
//! `generate_stored_authority_runs.py`): each case damages a Python-made profile as Python's
//! generator did, opens it with the Rust store, and must get Python's startup outcome.

mod common;

use flate2::read::GzDecoder;
use std::io::Read;
use std::path::Path;
use sylvia_store::AuthorityStore;

fn base(name: &str) -> Vec<u8> {
    let relative = match name {
        "riichi" => "tests/vectors/packages/seed_riichi.sqlite3.gz",
        "story" => "tests/vectors/session_pages/seed.sqlite3.gz",
        other => panic!("unknown base {other}"),
    };
    let mut bytes = Vec::new();
    GzDecoder::new(std::fs::File::open(Path::new(env!("CARGO_MANIFEST_DIR")).join(relative)).unwrap())
        .read_to_end(&mut bytes)
        .unwrap();
    bytes
}

#[test]
fn damaged_profiles_open_as_python_opens_them() {
    let record = common::read_json("tests/vectors/stored_authority/stored_authority_runs.json");
    for case in record["cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let folder = common::work_folder(&format!("stored-authority-{name}"));
        let database = folder.join("authority.sqlite3");
        std::fs::write(&database, base(case["base"].as_str().unwrap())).unwrap();
        {
            let connection = rusqlite::Connection::open(&database).unwrap();
            // Python's `sqlite3` leaves foreign keys off; rusqlite's bundled SQLite turns them on.
            connection.execute_batch("PRAGMA foreign_keys = OFF").unwrap();
            for statement in case["sql"].as_array().unwrap() {
                connection.execute_batch(statement.as_str().unwrap()).unwrap();
            }
        }
        let want = &case["outcome"];
        match (AuthorityStore::open_for_application_owner(&database, None), want.get("error")) {
            (Ok(mut store), None) => store.close(),
            (Err(error), Some(kind)) => {
                let coded = error.at_startup();
                assert_eq!(coded.error_type, kind.as_str(), "{name}: type");
                assert_eq!(coded.code, want["code"].as_str().unwrap(), "{name}: code");
                assert_eq!(coded.message, want["message"].as_str().unwrap(), "{name}: message");
            }
            (Ok(mut store), Some(_)) => {
                store.close();
                panic!("{name}: Rust opened the profile, Python {want}")
            }
            (Err(error), None) => panic!("{name}: Rust refused with {error:?}, Python opened it"),
        }
    }
}
