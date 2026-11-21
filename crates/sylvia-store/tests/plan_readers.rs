//! Function-level vectors for the Plan readers (`tests/vectors/plan_readers`, written by
//! `generate_plan_reader_vectors.py`): the adventure lineage, chapter markers, endings, the
//! accepted initial state and migration 9's seeding. Each case applies its SQL to a copy of its
//! base database and calls the Rust function Python's module function was called as; every
//! outcome, and every set of continuity records a write leaves, must be Python's.

mod common;

use flate2::read::GzDecoder;
use rusqlite::types::Value;
use rusqlite::Connection;
use std::io::Read;
use std::path::Path;
use sylvia_base::Json;
use sylvia_store::{continuity, schema, sequel, StoreError, StoreResult};

fn gunzip(relative: &str) -> Vec<u8> {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/vectors").join(relative);
    let mut bytes = Vec::new();
    GzDecoder::new(std::fs::File::open(&path).unwrap()).read_to_end(&mut bytes).unwrap();
    bytes
}

const DM: &str = "dm-1";

fn value_json(value: Value) -> Json {
    match value {
        Value::Null => Json::Null,
        Value::Integer(number) => number.into(),
        Value::Real(number) => number.into(),
        Value::Text(text) => text.into(),
        Value::Blob(_) => panic!("no blobs in continuity records"),
    }
}

fn records(connection: &Connection) -> Json {
    let mut statement = connection
        .prepare(
            "SELECT campaign_id, sequence, entry_id, record_id, kind, status, payload_json, \
             basis_message_ids_json, operation_id, admitted_revision FROM campaign_continuity_records \
             ORDER BY campaign_id, sequence",
        )
        .unwrap();
    let rows = statement
        .query_map([], |row| (0..10).map(|index| row.get::<_, Value>(index)).collect::<Result<Vec<_>, _>>())
        .unwrap();
    Json::Array(rows.map(|row| Json::Array(row.unwrap().into_iter().map(value_json).collect())).collect())
}

fn maps(items: Vec<sylvia_base::JsonMap>) -> Json {
    Json::Array(items.into_iter().map(Json::Object).collect())
}

fn call(connection: &Connection, item: &Json) -> StoreResult<Json> {
    let campaign = item["campaign_id"].as_str().unwrap_or("campaign-story");
    match item["fn"].as_str().unwrap() {
        "adventure_rows" => Ok(Json::Array(
            sequel::adventure_rows(connection, campaign)?
                .iter()
                .map(|row| row.plan_id().map(Json::String))
                .collect::<StoreResult<_>>()?,
        )),
        "read_act_markers" => continuity::read_act_markers(connection, DM, campaign).map(maps),
        "read_campaign_ending" => continuity::read_campaign_ending(connection, DM, campaign)
            .map(|ending| ending.map_or(Json::Null, Json::Object)),
        "read_campaign_endings" => continuity::read_campaign_endings(connection, DM, campaign).map(maps),
        "seed_initial_state" => {
            continuity::seed_initial_state(
                connection,
                campaign,
                item["plan"].as_object().unwrap(),
                item["admitted_revision"].as_i64().unwrap(),
            )?;
            Ok(records(connection))
        }
        "apply_migration" => {
            schema::apply_continuity_migration(connection)?;
            Ok(records(connection))
        }
        other => panic!("unknown function {other}"),
    }
}

/// Python exceptions whose text the port reproduces; for SQLite's own errors only the class of
/// failure (an internal fault) is compared.
const UNCAUGHT_WITH_TEXT: [&str; 4] = ["KeyError", "AttributeError", "TypeError", "StopIteration"];

fn check(label: &str, connection: &Connection, got: StoreResult<Json>, want: &Json) {
    let Some(kind) = want.get("error").and_then(Json::as_str) else {
        assert_eq!(
            got.unwrap_or_else(|error| panic!("{label}: Rust {error:?}, Python {want}")),
            want["ok"],
            "{label}"
        );
        return;
    };
    let error = got.err().unwrap_or_else(|| panic!("{label}: Rust succeeded, Python {want}"));
    let message = want["message"].as_str().unwrap();
    match kind {
        "ValueError" => {
            let coded =
                error.rejection().unwrap_or_else(|| panic!("{label}: Rust fault {error:?}, Python {want}"));
            assert_eq!((coded.error_type, coded.message.as_str()), (Some("ValueError"), message), "{label}");
        }
        // `json.JSONDecodeError`, a `ValueError`, with CPython's text (`sylvia_base::pyjson`).
        "JSONDecodeError" => {
            let coded =
                error.rejection().unwrap_or_else(|| panic!("{label}: Rust fault {error:?}, Python {want}"));
            assert_eq!(
                (coded.error_type, coded.message.as_str()),
                (Some("JSONDecodeError"), message),
                "{label}"
            );
        }
        _ => {
            assert!(error.rejection().is_none(), "{label}: Rust rejection {error:?}, Python {want}");
            if UNCAUGHT_WITH_TEXT.contains(&kind) {
                assert!(
                    matches!(&error, StoreError::Other(text) if text == message),
                    "{label}: {error:?}, Python {want}"
                );
            }
        }
    }
    // A failed write leaves the records written before it, in both.
    if let Some(expected) = want.get("records") {
        assert_eq!(&records(connection), expected, "{label}: records after the failure");
    }
}

#[test]
fn every_plan_reader_case_matches_python() {
    let record = common::read_json("tests/vectors/plan_readers/plan_readers.json");
    let story = gunzip("session_pages/seed.sqlite3.gz");
    let v8 = gunzip("plan_readers/v8.sqlite3.gz");
    for case in record["cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let folder = common::work_folder(&format!("plan-readers-{name}"));
        let database = folder.join("authority.sqlite3");
        std::fs::write(&database, if case["base"] == "v8" { &v8 } else { &story }).unwrap();
        let connection = Connection::open(&database).unwrap();
        connection.execute_batch("PRAGMA foreign_keys = OFF").unwrap();
        for statement in case["sql"].as_array().unwrap() {
            connection.execute_batch(statement.as_str().unwrap()).unwrap();
        }
        for (index, (item, want)) in
            case["calls"].as_array().unwrap().iter().zip(case["outcomes"].as_array().unwrap()).enumerate()
        {
            let label = format!("{name} #{index} {}", item["fn"]);
            check(&label, &connection, call(&connection, item), want);
        }
    }
}
