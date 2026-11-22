//! Portable export capture against Python (`tests/vectors/export_capture`, written by
//! `generate_export_capture_runs.py`): each case opens a Python-made profile, captures one
//! campaign (with the export fault seam raising at the named stages) and must match Python's
//! capture, its authority and private members, its manifest bytes, or Python's failure with its
//! supplemental codes.

mod common;

use flate2::read::GzDecoder;
use serde_json::json;
use std::io::Read;
use std::path::Path;
use std::sync::{Arc, Mutex};
use sylvia_base::hash::sha256_hex;
use sylvia_base::{Json, JsonMap};
use sylvia_store::campaign_archive::{self, ManifestInput, TABLES};
use sylvia_store::export::ExportCapture;
use sylvia_store::store::Faults;
use sylvia_store::{AuthorityStore, StoreError};

fn base(name: &str) -> Vec<u8> {
    let relative = match name {
        "story" => "tests/vectors/export_capture/story.sqlite3.gz",
        "constructed" => "tests/vectors/campaigns/seed.sqlite3.gz",
        "cast" => "tests/vectors/stored_authority/cast.sqlite3.gz",
        "fresh" => "tests/vectors/packages/seed_fresh.sqlite3.gz",
        "riichi" => "tests/vectors/packages/seed_riichi.sqlite3.gz",
        "v4" => "tests/vectors/packages/participants_v4.sqlite3.gz",
        other => panic!("unknown base {other}"),
    };
    let mut bytes = Vec::new();
    GzDecoder::new(std::fs::File::open(Path::new(env!("CARGO_MANIFEST_DIR")).join(relative)).unwrap())
        .read_to_end(&mut bytes)
        .unwrap();
    bytes
}

fn digest(bytes: Option<&[u8]>) -> Json {
    bytes.map_or(Json::Null, |bytes| json!({"sha256": sha256_hex(bytes), "length": bytes.len()}))
}

fn member(name: &str, bytes: &[u8]) -> JsonMap {
    let mut member = JsonMap::new();
    member.insert("name".into(), name.into());
    member.insert("byte_count".into(), bytes.len().into());
    member.insert("sha256".into(), sha256_hex(bytes).into());
    member
}

fn captured(capture: &ExportCapture) -> Json {
    let mut members = vec![member("authority.jsonl", &capture.authority_bytes)];
    if let Some(private) = &capture.private_bytes {
        members.push(member(campaign_archive::PRIVATE_MEMBER_NAME, private));
    }
    let manifest = campaign_archive::make_manifest(&ManifestInput {
        source_schema_version: capture.source_schema_version,
        source_schema_identity_sha256: &capture.source_schema_identity_sha256,
        migration_provenance: &capture.migration_provenance,
        campaign_id: &capture.campaign_id,
        captured_revision: capture.captured_revision,
        rows: &capture.rows,
        non_manifest_members: &members,
    })
    .unwrap();
    let bytes = campaign_archive::manifest_bytes(&manifest).unwrap();
    let counts: JsonMap = TABLES
        .iter()
        .map(|table| (table.name.to_owned(), capture.rows.get(table.name).len().into()))
        .collect();
    json!({
        "campaign_id": capture.campaign_id,
        "captured_revision": capture.captured_revision,
        "source_schema_version": capture.source_schema_version,
        "source_schema_identity_sha256": capture.source_schema_identity_sha256,
        "migration_provenance": capture.migration_provenance,
        "table_row_counts": counts,
        "authority": digest(Some(&capture.authority_bytes)),
        "private": digest(capture.private_bytes.as_deref()),
        "blob_sha256s": capture.blob_sha256s,
        "members": members,
        "manifest": digest(Some(&bytes)),
    })
}

#[test]
fn captures_match_python() {
    let record = common::read_json("tests/vectors/export_capture/capture_runs.json");
    let mut failures = Vec::new();
    for (index, case) in record["cases"].as_array().unwrap().iter().enumerate() {
        let label = format!("#{index} {} {} {}", case["base"], case["campaign_id"], case["faults"]);
        let folder = common::work_folder(&format!("export-capture-{index}"));
        let database = folder.join("authority.sqlite3");
        std::fs::write(&database, base(case["base"].as_str().unwrap())).unwrap();
        if let Some(statements) = case["sql"].as_array() {
            let connection = rusqlite::Connection::open(&database).unwrap();
            for statement in statements {
                connection.execute_batch(statement.as_str().unwrap()).unwrap();
            }
        }
        let armed: Arc<Mutex<Vec<String>>> =
            Arc::new(Mutex::new(case["faults"].as_array().map_or_else(Vec::new, |faults| {
                faults.iter().map(|stage| stage.as_str().unwrap().to_owned()).collect()
            })));
        let mut store = AuthorityStore::open_for_application_owner(&database, None).unwrap();
        let seam = Arc::clone(&armed);
        store.set_faults(Faults {
            export_stage: Some(Box::new(move |stage: &str| {
                if seam.lock().unwrap().iter().any(|armed| armed == stage) {
                    return Err(StoreError::Other(format!("fault at {stage}")));
                }
                Ok(())
            })),
            ..Faults::default()
        });
        let got = match store.capture_campaign_export(
            case["campaign_id"].as_str().unwrap(),
            case["include_thinking"].as_bool().unwrap(),
        ) {
            Ok(capture) => json!({"ok": captured(&capture)}),
            Err(failure) => json!({
                "error": "_ExportFailure",
                "code": failure.code,
                "message": failure.message(),
                "supplemental_codes": failure.supplemental_codes,
            }),
        };
        store.close();
        if got != case["outcome"] {
            failures.push(format!("{label}:\n  Rust   {got}\n  Python {}", case["outcome"]));
        }
    }
    assert!(failures.is_empty(), "{} cases differ:\n{}", failures.len(), failures.join("\n"));
}
