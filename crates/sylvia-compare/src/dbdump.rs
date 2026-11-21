//! Dump a finished SQLite database to JSON, table by table.

use rusqlite::types::ValueRef;
use rusqlite::{Connection, OpenFlags};
use sha2::{Digest, Sha256};
use std::path::Path;
use sylvia_base::{Json, JsonMap};

/// Blobs up to this size are kept whole; larger ones are recorded by hash and length.
const INLINE_BLOB_BYTES: usize = 256;

/// Dump every table of the database at `path`.
///
/// The result has `schema` (every `sqlite_master` entry in name order), `pragmas` and `tables`
/// (name to `{columns, rows}`; rows in rowid order, or in whole-row order for tables without a
/// rowid). Text that is itself a JSON object or array is parsed, so the comparison can look
/// inside stored payloads.
pub fn dump(path: &Path) -> Result<Json, String> {
    let connection = Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY)
        .map_err(|error| format!("could not open {}: {error}", path.display()))?;
    let fail = |error: rusqlite::Error| format!("could not read {}: {error}", path.display());

    let mut schema = Vec::new();
    let mut tables = Vec::new();
    {
        let mut statement = connection
            .prepare("SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name")
            .map_err(fail)?;
        let mut rows = statement.query([]).map_err(fail)?;
        while let Some(row) = rows.next().map_err(fail)? {
            let kind: String = row.get(0).map_err(fail)?;
            let name: String = row.get(1).map_err(fail)?;
            let table: String = row.get(2).map_err(fail)?;
            let sql: Option<String> = row.get(3).map_err(fail)?;
            if kind == "table" {
                tables.push(name.clone());
            }
            let mut entry = JsonMap::new();
            entry.insert("type".into(), kind.into());
            entry.insert("name".into(), name.into());
            entry.insert("table".into(), table.into());
            entry.insert("sql".into(), sql.map_or(Json::Null, Json::String));
            schema.push(Json::Object(entry));
        }
    }

    let mut pragmas = JsonMap::new();
    for pragma in ["user_version", "application_id", "journal_mode", "page_size", "auto_vacuum", "encoding"] {
        let value: rusqlite::Result<rusqlite::types::Value> =
            connection.query_row(&format!("PRAGMA {pragma}"), [], |row| row.get(0));
        pragmas.insert(pragma.into(), value.map_or(Json::Null, |value| owned_value(&value)));
    }

    let mut dumped = JsonMap::new();
    for table in tables {
        let quoted = table.replace('"', "\"\"");
        let has_rowid = connection.prepare(&format!("SELECT rowid FROM \"{quoted}\" LIMIT 0")).is_ok();
        let order = if has_rowid { "rowid" } else { "1" };
        let mut statement =
            connection.prepare(&format!("SELECT * FROM \"{quoted}\" ORDER BY {order}")).map_err(fail)?;
        let columns: Vec<String> = statement.column_names().iter().map(|name| (*name).to_owned()).collect();
        let mut rows_out = Vec::new();
        let mut rows = statement.query([]).map_err(fail)?;
        while let Some(row) = rows.next().map_err(fail)? {
            let mut record = JsonMap::new();
            for (index, column) in columns.iter().enumerate() {
                record.insert(column.clone(), value(row.get_ref(index).map_err(fail)?));
            }
            rows_out.push(Json::Object(record));
        }
        if !has_rowid {
            rows_out.sort_by_key(Json::to_string);
        }
        let mut entry = JsonMap::new();
        entry.insert("columns".into(), columns.into_iter().map(Json::String).collect());
        entry.insert("row_count".into(), rows_out.len().into());
        entry.insert("rows".into(), Json::Array(rows_out));
        dumped.insert(table, Json::Object(entry));
    }

    let mut document = JsonMap::new();
    document.insert("pragmas".into(), Json::Object(pragmas));
    document.insert("schema".into(), Json::Array(schema));
    document.insert("tables".into(), Json::Object(dumped));
    Ok(Json::Object(document))
}

fn owned_value(value: &rusqlite::types::Value) -> Json {
    use rusqlite::types::Value;
    match value {
        Value::Null => Json::Null,
        Value::Integer(number) => (*number).into(),
        Value::Real(number) => serde_json::Number::from_f64(*number).map_or(Json::Null, Json::Number),
        Value::Text(text) => text_value(text),
        Value::Blob(bytes) => blob_value(bytes),
    }
}

fn value(value: ValueRef<'_>) -> Json {
    match value {
        ValueRef::Null => Json::Null,
        ValueRef::Integer(number) => number.into(),
        ValueRef::Real(number) => serde_json::Number::from_f64(number).map_or(Json::Null, Json::Number),
        ValueRef::Text(bytes) => match std::str::from_utf8(bytes) {
            Ok(text) => text_value(text),
            Err(_) => blob_value(bytes),
        },
        ValueRef::Blob(bytes) => blob_value(bytes),
    }
}

fn text_value(text: &str) -> Json {
    let trimmed = text.trim_start();
    if trimmed.starts_with('{') || trimmed.starts_with('[') {
        if let Ok(parsed) = serde_json::from_str::<Json>(text) {
            // Equal values stored with different encodings are a real difference for anything
            // hashed later. Record which Python encoding reproduces the stored text exactly;
            // that survives run-specific identifiers inside the payload, which a hash of the
            // text would not.
            let mut wrapped = JsonMap::new();
            wrapped.insert("$encoding".into(), encoding_of(text, &parsed).into());
            wrapped.insert("$json".into(), parsed);
            return Json::Object(wrapped);
        }
    }
    Json::String(text.to_owned())
}

/// Name the `json.dumps` options that reproduce `text` from its parsed value, or `other`.
fn encoding_of(text: &str, parsed: &Json) -> &'static str {
    use sylvia_base::pyjson::{dumps, Dump};
    let candidates = [
        ("canonical", Dump::CANONICAL),
        ("wire", Dump::WIRE),
        ("wire_sorted", Dump::WIRE.sorted(true)),
        ("compact_unicode", Dump::WIRE.ascii(false)),
        ("default", Dump::DEFAULT),
        ("default_sorted", Dump::DEFAULT.sorted(true)),
        ("unicode", Dump::DEFAULT.ascii(false)),
        ("unicode_sorted", Dump::DEFAULT.ascii(false).sorted(true)),
        ("indent2", Dump::INDENT2),
        ("indent2_sorted", Dump::INDENT2.sorted(true)),
        ("indent2_unicode", Dump::INDENT2.ascii(false)),
        ("indent2_sorted_unicode", Dump::INDENT2.sorted(true).ascii(false)),
    ];
    candidates
        .into_iter()
        .find(|(_, options)| dumps(parsed, *options) == text)
        .map_or("other", |(name, _)| name)
}

fn blob_value(bytes: &[u8]) -> Json {
    let mut wrapped = JsonMap::new();
    if bytes.len() <= INLINE_BLOB_BYTES {
        wrapped.insert("$blob_hex".into(), hex::encode(bytes).into());
    } else {
        wrapped.insert("$blob_sha256".into(), hex::encode(Sha256::digest(bytes)).into());
        wrapped.insert("$blob_len".into(), bytes.len().into());
    }
    Json::Object(wrapped)
}
