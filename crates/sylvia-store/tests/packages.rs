//! Paired runs for package installation (`tests/vectors/packages`, written by
//! `generate_package_runs.py`): staging, publication, the candidate and manifest reads, damage
//! between operations and the startup verifier on reopen. From Python's starting database, the
//! Rust store runs each run's operations on archives admitted by lane 1's
//! `admit_package_archive`, and must give Python's outcomes (and, where marked, a database that
//! dumps the same).

mod common;

use flate2::read::GzDecoder;
use serde_json::json;
use std::io::Read;
use std::path::Path;
use sylvia_base::Json;
use sylvia_domain::package_archive::admit_package_archive;
use sylvia_domain::package_library::{PackageCandidate, ParticipantArtifactDeclaration};
use sylvia_store::store::Faults;
use sylvia_store::{AuthorityStore, StoreError, StoreResult};

fn vector(relative: &str) -> Vec<u8> {
    std::fs::read(Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/vectors/packages").join(relative))
        .unwrap()
}

fn gunzip(name: &str) -> Vec<u8> {
    let mut bytes = Vec::new();
    GzDecoder::new(&vector(&format!("{name}.sqlite3.gz"))[..]).read_to_end(&mut bytes).unwrap();
    bytes
}

fn text(op: &Json, key: &str) -> String {
    op[key].as_str().unwrap_or_else(|| panic!("{key} in {op}")).to_owned()
}

fn candidate_json(candidate: &PackageCandidate) -> Json {
    json!({
        "candidate_id": candidate.candidate_id(), "install_id": candidate.install_id(),
        "status": candidate.status().as_str(), "closure_sha256": candidate.closure_sha256(),
        "validation_report": Json::Object(candidate.validation_report().clone()),
        "entries": candidate.package_lock().entries().iter().map(|entry| json!({
            "package_id": entry.package_id(), "version": entry.version(), "sha256": entry.sha256(),
            "role": entry.role().as_str(), "load_order": entry.load_order(), "capabilities": entry.capabilities(),
            "dependencies": entry.dependencies().iter().map(|d| json!([
                d.package_id(), d.version(), d.sha256(), d.kind(), d.namespace(),
            ])).collect::<Vec<_>>(),
        })).collect::<Vec<_>>(),
    })
}

fn declarations_json(items: &[ParticipantArtifactDeclaration]) -> Json {
    Json::Array(
        items
            .iter()
            .map(|item| {
                json!({
                    "kind": item.kind, "schema_id": item.schema_id, "schema_sha256": item.schema_sha256,
                    "schema": Json::Object(item.schema.clone()),
                })
            })
            .collect(),
    )
}

/// The installed identity of one package, read as the generator reads it.
fn identity(database: &Path, package_id: &str) -> (String, String, String) {
    rusqlite::Connection::open(database)
        .unwrap()
        .query_row(
            "SELECT package_id, version, content_sha256 FROM package_manifests WHERE package_id = ?",
            [package_id],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
        )
        .unwrap()
}

struct Run {
    database: std::path::PathBuf,
    store: Option<AuthorityStore>,
}

fn run(state: &mut Run, op: &Json) -> StoreResult<Json> {
    let kind = op["op"].as_str().unwrap();
    match kind {
        "sql" => {
            let connection = rusqlite::Connection::open(&state.database).unwrap();
            // Python's `sqlite3` leaves foreign keys off; rusqlite's bundled SQLite turns them on.
            connection.execute_batch("PRAGMA foreign_keys = OFF").unwrap();
            connection.execute_batch(&text(op, "statement")).unwrap();
            return Ok(Json::Null);
        }
        "reopen" => {
            if let Some(mut store) = state.store.take() {
                store.close();
            }
            state.store = Some(AuthorityStore::open_for_application_owner(&state.database, None)?);
            return Ok(Json::Null);
        }
        _ => {}
    }
    let store = state.store.as_mut().unwrap();
    match kind {
        "stage_package_archive" | "publish_package_archive" => {
            let admitted = admit_package_archive(&vector(&format!("archives/{}.archive", text(op, "archive"))))?;
            let install_id = text(op, "install_id");
            if kind == "stage_package_archive" {
                return store.stage_package_archive(&admitted, &install_id).map(|()| Json::Null);
            }
            if op["fault"].as_bool().unwrap() {
                store.set_faults(Faults {
                    before_package_publication: Some(Box::new(|| {
                        Err(StoreError::Other("fault before publication".into()))
                    })),
                    ..Faults::default()
                });
            }
            let result = store.publish_package_archive(&admitted, &install_id);
            store.set_faults(Faults::default());
            result.map(|candidate| candidate_json(&candidate))
        }
        "package_candidates" => {
            store.package_candidates().map(|items| Json::Array(items.iter().map(candidate_json).collect()))
        }
        "package_candidate" => store.package_candidate(&text(op, "candidate_id")).map(|item| candidate_json(&item)),
        "package_staging" => store.package_staging(&text(op, "install_id")).map(|record| {
            json!({"install_id": record.install_id, "status": record.status, "chunk_count": record.chunk_count})
        }),
        "exact_package_manifest" => {
            let (id, version, sha256) = identity(&state.database, &text(op, "package"));
            let store = state.store.as_ref().unwrap();
            store.exact_package_manifest(&id, &version, &sha256).map(|manifest| {
                let (a, b, c) = manifest.identity();
                json!({"identity": [a, b, c], "canonical_json": manifest.canonical_json})
            })
        }
        "exact_package_manifest_missing" => store
            .exact_package_manifest("pneuma.riichi-mahjong", "0.0.0", &"3".repeat(64))
            .map(|_| Json::Null),
        "package_participant_declarations" => store
            .package_participant_declarations(&text(op, "candidate_id"))
            .map(|items| declarations_json(&items)),
        "exact_participant_declarations" => {
            let (id, version, sha256) = identity(&state.database, &text(op, "package"));
            let store = state.store.as_ref().unwrap();
            store.exact_participant_declarations(&id, &version, &sha256).map(|items| declarations_json(&items))
        }
        other => panic!("unknown operation {other}"),
    }
}

fn check(label: &str, got: StoreResult<Json>, want: &Json) {
    match (got, want.get("error")) {
        (Ok(value), None) => assert_eq!(value, want["ok"], "{label}"),
        (Err(StoreError::Other(message)), Some(kind)) if kind == "_Fault" => {
            assert_eq!(message, want["message"].as_str().unwrap(), "{label}: fault");
        }
        (Err(error), Some(kind)) => {
            let Some(coded) = error.rejection() else {
                panic!("{label}: Rust fault {error:?}, Python {want}")
            };
            assert_eq!(coded.error_type, kind.as_str(), "{label}: type");
            // Python's ValueError carries no code; its owner replies `command_rejected`.
            let code = want["code"].as_str().unwrap_or("command_rejected");
            assert_eq!(coded.code, code, "{label}: code");
            assert_eq!(coded.message, want["message"].as_str().unwrap(), "{label}: message");
        }
        (got, _) => panic!("{label}: Rust {got:?}, Python {want}"),
    }
}

#[test]
fn every_package_run_matches_python() {
    let record = common::read_json("tests/vectors/packages/package_runs.json");
    let rules = sylvia_compare::diff::Rules::defaults();
    for spec in record["runs"].as_array().unwrap() {
        let name = spec["name"].as_str().unwrap();
        let folder = common::work_folder(&format!("packages-{name}"));
        let database = folder.join("authority.sqlite3");
        std::fs::write(&database, gunzip(&format!("seed_{}", spec["seed"].as_str().unwrap()))).unwrap();
        let store = AuthorityStore::open_for_application_owner(&database, None).unwrap();
        let mut state = Run { database: database.clone(), store: Some(store) };
        let outcomes = spec["outcomes"].as_array().unwrap();
        for (index, (op, want)) in spec["operations"].as_array().unwrap().iter().zip(outcomes).enumerate() {
            let outcome = run(&mut state, op);
            check(&format!("{name} #{index} {}", op["op"]), outcome, want);
            if state.store.is_none() {
                assert_eq!(index + 1, outcomes.len(), "{name}: Python stopped where the reopen failed");
                break;
            }
        }
        if let Some(mut store) = state.store.take() {
            store.close();
        }
        if spec["compare_db"].as_bool() == Some(true) {
            let python = folder.join("python.sqlite3");
            std::fs::write(&python, gunzip(name)).unwrap();
            let dump = |path: &Path| {
                sylvia_compare::diff::normalise(&sylvia_compare::dbdump::dump(path).unwrap(), &rules).unwrap()
            };
            let differences = sylvia_compare::diff::compare(&dump(&python), &dump(&database), 20);
            assert!(differences.is_empty(), "{name}: database differs from Python's: {differences:#?}");
        }
    }
}

fn row_values(connection: &rusqlite::Connection, query: &str) -> Json {
    let mut statement = connection.prepare(query).unwrap();
    let width = statement.column_count();
    let rows = statement
        .query_map([], |row| {
            (0..width)
                .map(|index| {
                    Ok(match row.get::<_, rusqlite::types::Value>(index)? {
                        rusqlite::types::Value::Null => Json::Null,
                        rusqlite::types::Value::Integer(number) => number.into(),
                        rusqlite::types::Value::Real(number) => number.into(),
                        rusqlite::types::Value::Text(text) => text.into(),
                        rusqlite::types::Value::Blob(_) => panic!("no blobs here"),
                    })
                })
                .collect::<Result<Vec<_>, rusqlite::Error>>()
        })
        .unwrap();
    Json::Array(rows.map(|row| Json::Array(row.unwrap())).collect())
}

/// Migration 5's participant backfill, function-level: Python's `migrate_v4_participants` and
/// the Rust one on the same schema-4 database with the Riichi closure and legacy rows.
#[test]
fn participant_backfill_matches_python() {
    let record = common::read_json("tests/vectors/packages/package_runs.json");
    let base = gunzip("participants_v4");
    for case in record["participant_cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let folder = common::work_folder(&format!("participants-{name}"));
        let database = folder.join("authority.sqlite3");
        std::fs::write(&database, &base).unwrap();
        let connection = rusqlite::Connection::open(&database).unwrap();
        // Python's `sqlite3` leaves foreign keys off; rusqlite's bundled SQLite turns them on.
        connection.execute_batch("PRAGMA foreign_keys = OFF").unwrap();
        for statement in case["sql"].as_array().unwrap() {
            connection.execute_batch(statement.as_str().unwrap()).unwrap();
        }
        let want = &case["outcome"];
        match (sylvia_store::schema::migrate_v4_participants(&connection), want.get("error")) {
            (Ok(()), None) => {}
            (Err(error), Some(_)) => {
                let coded = error.rejection().unwrap_or_else(|| panic!("{name}: Rust fault {error:?}"));
                assert_eq!(coded.error_type, want["error"].as_str(), "{name}: type");
                assert_eq!(coded.message, want["message"].as_str().unwrap(), "{name}: message");
            }
            (got, _) => panic!("{name}: Rust {got:?}, Python {want}"),
        }
        let rows = json!({
            "participants": row_values(&connection, "SELECT * FROM participants ORDER BY campaign_id, participant_id"),
            "quarantines": row_values(&connection, "SELECT * FROM participant_quarantines ORDER BY campaign_id, participant_id"),
            "declarations": row_values(
                &connection,
                "SELECT package_id, artifact_order, kind, schema_id, schema_sha256, schema_json \
                 FROM package_participant_artifacts ORDER BY package_id, artifact_order",
            ),
        });
        assert_eq!(rows, want["rows"], "{name}: rows");
    }
}
