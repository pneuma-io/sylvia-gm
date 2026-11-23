//! A base built at test time instead of committed, the Rust half of
//! `tests/vectors/installed_base.py`: `sylvia-store`'s small fresh profile, a bundled package
//! installed through the ported `install_package_archive` (each table's digest checked against
//! the one Python recorded after its own install), then the rows the generator's setup wrote.
//!
//! A test that uses it (`mod installed_base;`):
//! 1. [`seed_copy`] to a work folder;
//! 2. opens the store and runs [`install`] with the generator's archive and install identity;
//! 3. closes the store, then [`check_digests`] against the recorded `installed_table_digests`;
//! 4. [`write_rows`] with the recorded setup tables, and keeps a copy as the run's base;
//! 5. reopens the store and replays its steps;
//! 6. [`changed_tables_differ`] against the recorded final tables and the base.
//!
//! The digest is the module documentation's in `installed_base.py`: per table, the SHA-256 of
//! its name, row count and rows sorted by their encoding (each value a type tag, an 8-byte
//! big-endian length and its bytes).

#![allow(dead_code)]

use flate2::read::GzDecoder;
use rusqlite::types::Value;
use std::collections::BTreeMap;
use std::io::Read;
use std::path::Path;
use sylvia_base::{Json, JsonMap};
use sylvia_store::StoreHandle;

/// The committed seed, `sylvia-store`'s fresh profile.
pub fn seed_path() -> std::path::PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../sylvia-store/tests/vectors/packages/seed_fresh.sqlite3.gz")
}

/// The seed, decompressed to `path`.
pub fn seed_copy(path: &Path) {
    let mut bytes = Vec::new();
    GzDecoder::new(std::fs::File::open(seed_path()).unwrap()).read_to_end(&mut bytes).unwrap();
    std::fs::write(path, bytes).unwrap();
}

/// The archive installed through the ported `install_package_archive`, as the generator's
/// Python owner installed it.
pub async fn install(store: &StoreHandle, archive: Vec<u8>, install_id: &str) {
    let install_id = install_id.to_owned();
    store
        .call(move |store| {
            sylvia_app::package_library::install_package_archive(store, &archive, &install_id).map(drop)
        })
        .await
        .unwrap();
}

fn tables(connection: &rusqlite::Connection) -> Vec<String> {
    connection
        .prepare("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
        .unwrap()
        .query_map([], |row| row.get(0))
        .unwrap()
        .collect::<Result<_, _>>()
        .unwrap()
}

fn rows(connection: &rusqlite::Connection, table: &str) -> Vec<Vec<Value>> {
    let mut statement = connection.prepare(&format!("SELECT * FROM \"{table}\"")).unwrap();
    let width = statement.column_count();
    statement
        .query_map([], |row| (0..width).map(|index| row.get(index)).collect::<Result<Vec<Value>, _>>())
        .unwrap()
        .collect::<Result<_, _>>()
        .unwrap()
}

fn value_bytes(value: &Value, out: &mut Vec<u8>) {
    let (tag, body): (u8, Vec<u8>) = match value {
        Value::Null => (b'n', Vec::new()),
        Value::Integer(value) => (b'i', value.to_string().into_bytes()),
        Value::Real(value) => (b'r', value.to_be_bytes().to_vec()),
        Value::Text(value) => (b't', value.as_bytes().to_vec()),
        Value::Blob(value) => (b'b', value.clone()),
    };
    out.push(tag);
    out.extend_from_slice(&(body.len() as u64).to_be_bytes());
    out.extend_from_slice(&body);
}

/// Each table's digest, by table name.
pub fn table_digests(path: &Path) -> BTreeMap<String, String> {
    let connection = rusqlite::Connection::open(path).unwrap();
    tables(&connection)
        .into_iter()
        .map(|table| {
            let mut encoded: Vec<Vec<u8>> = rows(&connection, &table)
                .iter()
                .map(|row| {
                    let mut out = Vec::new();
                    row.iter().for_each(|value| value_bytes(value, &mut out));
                    out
                })
                .collect();
            encoded.sort();
            let mut buffer = format!("{table}\0{}\0", encoded.len()).into_bytes();
            for row in &encoded {
                buffer.extend_from_slice(&(row.len() as u64).to_be_bytes());
                buffer.extend_from_slice(row);
            }
            (table, sylvia_base::hash::sha256_hex(&buffer))
        })
        .collect()
}

/// The tables whose digest differs from the recorded one (or that one side lacks).
pub fn check_digests(path: &Path, recorded: &JsonMap) -> Vec<String> {
    let ours = table_digests(path);
    let mut names: Vec<&String> = ours.keys().chain(recorded.keys()).collect();
    names.sort();
    names.dedup();
    names
        .into_iter()
        .filter(|name| ours.get(*name).map(String::as_str) != recorded.get(*name).and_then(Json::as_str))
        .map(|name| format!("the installed {name} table differs from Python's"))
        .collect()
}

/// A value as the generator types it for JSON.
pub fn typed(value: &Value) -> Json {
    let hex = |bytes: &[u8]| bytes.iter().map(|byte| format!("{byte:02x}")).collect::<String>();
    match value {
        Value::Null => Json::Null,
        Value::Integer(value) => (*value).into(),
        Value::Real(value) => serde_json::json!({"real": hex(&value.to_be_bytes())}),
        Value::Text(value) => value.as_str().into(),
        Value::Blob(value) => serde_json::json!({"blob": hex(value)}),
    }
}

fn untyped(value: &Json) -> Value {
    let bytes = |text: &str| {
        (0..text.len())
            .step_by(2)
            .map(|index| u8::from_str_radix(&text[index..index + 2], 16).unwrap())
            .collect()
    };
    match value {
        Json::Null => Value::Null,
        Json::Number(number) => Value::Integer(number.as_i64().unwrap()),
        Json::String(text) => Value::Text(text.clone()),
        Json::Object(map) => match (map.get("real"), map.get("blob")) {
            (Some(Json::String(hex)), None) => {
                let raw: Vec<u8> = bytes(hex);
                Value::Real(f64::from_be_bytes(raw.try_into().unwrap()))
            }
            (None, Some(Json::String(hex))) => Value::Blob(bytes(hex)),
            _ => panic!("unexpected typed value {value}"),
        },
        _ => panic!("unexpected typed value {value}"),
    }
}

/// Every table's rows, typed, each table sorted (by its JSON text).
pub fn sorted_rows(path: &Path) -> BTreeMap<String, Vec<Json>> {
    let connection = rusqlite::Connection::open(path).unwrap();
    tables(&connection)
        .into_iter()
        .map(|table| {
            let typed_rows = rows(&connection, &table)
                .iter()
                .map(|row| Json::Array(row.iter().map(typed).collect()))
                .collect();
            (table.clone(), sort(typed_rows))
        })
        .collect()
}

fn sort(mut rows: Vec<Json>) -> Vec<Json> {
    rows.sort_by_cached_key(Json::to_string);
    rows
}

/// Replace each recorded table's rows with the recorded ones, in their order. The authority's
/// triggers check rows against other tables (a state row needs its namespace owner, a namespace
/// owner its campaign's lock), so tables are written in passes until every one is in.
pub fn write_rows(path: &Path, recorded: &JsonMap) {
    let connection = rusqlite::Connection::open(path).unwrap();
    let mut pending: Vec<(&String, &Json)> = recorded.iter().collect();
    while !pending.is_empty() {
        let before = pending.len();
        let mut next = Vec::new();
        for (table, rows) in pending {
            connection.execute_batch("SAVEPOINT installed_base").unwrap();
            let written = (|| -> rusqlite::Result<()> {
                connection.execute(&format!("DELETE FROM \"{table}\""), [])?;
                for row in rows.as_array().unwrap() {
                    let values: Vec<Value> = row.as_array().unwrap().iter().map(untyped).collect();
                    let marks = vec!["?"; values.len()].join(", ");
                    connection.execute(
                        &format!("INSERT INTO \"{table}\" VALUES ({marks})"),
                        rusqlite::params_from_iter(values),
                    )?;
                }
                Ok(())
            })();
            match written {
                Ok(()) => connection.execute_batch("RELEASE installed_base").unwrap(),
                Err(_) => {
                    connection.execute_batch("ROLLBACK TO installed_base; RELEASE installed_base").unwrap();
                    next.push((table, rows));
                }
            }
        }
        assert!(
            next.len() < before,
            "the setup rows of {:?} cannot be written",
            next.iter().map(|item| item.0).collect::<Vec<_>>()
        );
        pending = next;
    }
}

/// Each table that ends differently from Python: a recorded table must hold Python's final rows,
/// and every other table must end as the base has it.
pub fn changed_tables_differ(database: &Path, base: &Path, recorded: &JsonMap) -> Vec<String> {
    let (ours, base_rows) = (sorted_rows(database), sorted_rows(base));
    let mut failures = Vec::new();
    if ours.keys().ne(base_rows.keys()) {
        failures.push("the run's tables differ from the base's".to_owned());
    }
    for (table, rows) in &ours {
        let want = match recorded.get(table) {
            Some(rows) => sort(rows.as_array().unwrap().clone()),
            None => base_rows.get(table).cloned().unwrap_or_default(),
        };
        if *rows != want {
            failures.push(format!("the final {table} rows differ from Python's"));
        }
    }
    failures
}
