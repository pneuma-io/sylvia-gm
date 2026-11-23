//! Shared helpers: a work folder per test and the database state the fixtures record.

#![allow(dead_code)]

use rusqlite::types::ValueRef;
use rusqlite::{Connection, OpenFlags};
use std::path::{Path, PathBuf};
use sylvia_base::hash::sha256_hex;
use sylvia_base::pyjson::{dumps, Dump};
use sylvia_base::{Json, JsonMap};

/// A fresh, empty folder under Cargo's per-test temporary root.
pub fn work_folder(name: &str) -> PathBuf {
    let folder = Path::new(env!("CARGO_TARGET_TMPDIR")).join("sylvia-store").join(name);
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

pub fn read_json(relative: &str) -> Json {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join(relative);
    let text = std::fs::read_to_string(&path).unwrap_or_else(|error| panic!("{}: {error}", path.display()));
    serde_json::from_str(&text).expect("valid JSON")
}

fn value(value: ValueRef<'_>) -> Json {
    match value {
        ValueRef::Null => Json::Null,
        ValueRef::Integer(number) => number.into(),
        ValueRef::Real(number) => serde_json::Number::from_f64(number).map_or(Json::Null, Json::Number),
        ValueRef::Text(text) => Json::String(String::from_utf8_lossy(text).into_owned()),
        ValueRef::Blob(bytes) => Json::String(format!("<blob {}>", bytes.len())),
    }
}

pub fn rows(connection: &Connection, query: &str) -> Vec<Json> {
    let mut statement = connection.prepare(query).expect(query);
    let width = statement.column_count();
    let mut out = Vec::new();
    let mut rows = statement.query([]).expect(query);
    while let Some(row) = rows.next().expect(query) {
        out.push(Json::Array((0..width).map(|index| value(row.get_ref(index).unwrap())).collect()));
    }
    out
}

/// `sqlite_master` rows `[type, name, tbl_name, sql]` in `type, name` order.
pub fn schema_rows(connection: &Connection) -> Vec<Json> {
    rows(connection, "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name")
}

/// SHA-256 of Python's `json.dumps(rows)`, the generator's schema digest.
pub fn schema_digest(rows: &[Json]) -> String {
    sha256_hex(dumps(&Json::Array(rows.to_vec()), Dump::DEFAULT).as_bytes())
}

/// The state `generate_schema_fixtures.py` records for the database in `folder`.
pub fn state(folder: &Path) -> Json {
    let database = folder.join("authority.sqlite3");
    let connection =
        Connection::open_with_flags(&database, OpenFlags::SQLITE_OPEN_READ_ONLY).expect("open state");
    let names: Vec<String> = rows(&connection, "SELECT name FROM sqlite_master")
        .into_iter()
        .map(|row| row[0].as_str().unwrap_or_default().to_owned())
        .collect();
    let mut result = JsonMap::new();
    result.insert("user_version".into(), rows(&connection, "PRAGMA user_version")[0][0].clone());
    result.insert("journal_mode".into(), rows(&connection, "PRAGMA journal_mode")[0][0].clone());
    result.insert("schema".into(), Json::String(schema_digest(&schema_rows(&connection))));
    result.insert(
        "schema_migrations".into(),
        Json::Array(rows(&connection, "SELECT * FROM schema_migrations ORDER BY rowid")),
    );
    result.insert(
        "authority_schema_extensions".into(),
        if names.iter().any(|name| name == "authority_schema_extensions") {
            Json::Array(rows(&connection, "SELECT * FROM authority_schema_extensions ORDER BY rowid"))
        } else {
            Json::Null
        },
    );
    drop(connection);
    let sources = folder.join("migration-sources");
    let mut listed: Vec<String> = if sources.exists() {
        std::fs::read_dir(&sources)
            .expect("list migration sources")
            .map(|entry| entry.expect("entry").file_name().to_string_lossy().into_owned())
            .collect()
    } else {
        Vec::new()
    };
    listed.sort();
    result.insert("migration_sources".into(), Json::Array(listed.into_iter().map(Json::String).collect()));
    Json::Object(result)
}
