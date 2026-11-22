//! Package reads for library sources through the store handle, against Python's
//! `AuthorityOwner` (`tests/vectors/package_reads`, written by `generate_package_read_runs.py`):
//! `package_file_paths`, `package_text_file`, `package_record` and `package_label` on the
//! installed Riichi closure and a synthetic package with a file that is not UTF-8, with their
//! count and byte bounds and missing paths and packages.

mod common;

use flate2::read::GzDecoder;
use serde_json::json;
use std::io::Read;
use std::path::Path;
use sylvia_base::hash::sha256_hex;
use sylvia_base::pyjson::{dumps, Dump};
use sylvia_base::Json;
use sylvia_store::package_reads::{DEFAULT_MAXIMUM_BYTES, DEFAULT_MAXIMUM_FILES};
use sylvia_store::{AuthorityStore, StoreHandle, StoreResult};

fn base() -> Vec<u8> {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/vectors/package_reads/base.sqlite3.gz");
    let mut bytes = Vec::new();
    GzDecoder::new(std::fs::File::open(&path).unwrap()).read_to_end(&mut bytes).unwrap();
    bytes
}

/// The length (in code points) and SHA-256 of a text, as the generator records it.
fn digest(text: &str) -> Json {
    json!({"length": text.chars().count(), "sha256": sha256_hex(text.as_bytes())})
}

async fn run(handle: &StoreHandle, op: &Json) -> StoreResult<Json> {
    let text = |key: &str| op[key].as_str().unwrap().to_owned();
    let (package_id, version, sha256) = (text("package_id"), text("version"), text("sha256"));
    match op["op"].as_str().unwrap() {
        "package_file_paths" => {
            let limit = op["maximum_files"].as_i64().unwrap_or(DEFAULT_MAXIMUM_FILES);
            Ok(json!(handle.package_file_paths(&package_id, &version, &sha256, limit).await?))
        }
        "package_text_file" => {
            let limit = op["maximum_bytes"].as_i64().unwrap_or(DEFAULT_MAXIMUM_BYTES);
            let value = handle
                .package_text_file(&package_id, &version, &sha256, &text("portable_path"), limit)
                .await?;
            Ok(digest(&value))
        }
        "package_record" => {
            let record = handle.package_record(&package_id, &version, &sha256).await?;
            Ok(digest(&dumps(&Json::Object(record), Dump::CANONICAL)))
        }
        "package_label" => Ok(json!(handle.package_label(&package_id, &version, &sha256).await?)),
        other => panic!("unknown operation {other}"),
    }
}

/// The same read through the synchronous `AuthorityStore` methods lane 1's library sources call.
fn run_sync(store: &AuthorityStore, op: &Json) -> StoreResult<Json> {
    let text = |key: &str| op[key].as_str().unwrap().to_owned();
    let (package_id, version, sha256) = (text("package_id"), text("version"), text("sha256"));
    match op["op"].as_str().unwrap() {
        "package_file_paths" => {
            let limit = op["maximum_files"].as_i64().unwrap_or(DEFAULT_MAXIMUM_FILES);
            Ok(json!(store.package_file_paths(&package_id, &version, &sha256, limit)?))
        }
        "package_text_file" => {
            let limit = op["maximum_bytes"].as_i64().unwrap_or(DEFAULT_MAXIMUM_BYTES);
            Ok(digest(&store.package_text_file(
                &package_id,
                &version,
                &sha256,
                &text("portable_path"),
                limit,
            )?))
        }
        "package_record" => {
            let record = store.package_record(&package_id, &version, &sha256)?;
            Ok(digest(&dumps(&Json::Object(record), Dump::CANONICAL)))
        }
        "package_label" => Ok(json!(store.package_label(&package_id, &version, &sha256)?)),
        other => panic!("unknown operation {other}"),
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn package_reads_match_python() {
    let record = common::read_json("tests/vectors/package_reads/package_read_runs.json");
    let folder = common::work_folder("package-read-runs");
    let database = folder.join("authority.sqlite3");
    std::fs::write(&database, base()).unwrap();
    let path = database.clone();
    let (handle, _thread) =
        StoreHandle::spawn(move || AuthorityStore::open_for_application_owner(&path, None)).unwrap();
    let mut differences = Vec::new();
    for (op, want) in
        record["operations"].as_array().unwrap().iter().zip(record["outcomes"].as_array().unwrap())
    {
        let got = run(&handle, op).await;
        let owned = op.clone();
        let sync = handle.call(move |store| Ok(run_sync(store, &owned))).await.unwrap();
        assert_eq!(format!("{sync:?}"), format!("{got:?}"), "sync and async reads differ for {op}");
        let agrees = match (&got, want.get("error")) {
            (Ok(value), None) => value == &want["ok"],
            (Err(error), Some(kind)) => error.rejection().is_some_and(|coded| {
                coded.error_type == kind.as_str()
                    && coded.code == want["code"].as_str().unwrap_or("command_rejected")
                    && coded.message == want["message"].as_str().unwrap()
            }),
            _ => false,
        };
        if !agrees {
            differences.push(format!("{op}: python {want}, rust {got:?}"));
        }
    }
    assert!(differences.is_empty(), "{} reads differ:\n{}", differences.len(), differences.join("\n"));
}
