//! Portable campaign archive validation against Python (`tests/vectors/archive_validation`,
//! written by `generate_archive_validation_runs.py`): four archives Python's owner exported and
//! 72 damaged copies of them, each validated from its file. Every verdict must be Python's: the
//! same manifest, or the same refusal.

mod common;

use flate2::read::GzDecoder;
use serde_json::json;
use std::io::Read;
use std::path::Path;
use sylvia_base::hash::sha256_hex;
use sylvia_base::Json;
use sylvia_store::archive_validation::validate_campaign_archive;
use sylvia_store::campaign_archive::json_bytes;

#[test]
fn archives_validate_as_python_validates_them() {
    let record = common::read_json("tests/vectors/archive_validation/validation_runs.json");
    let mut archives = Vec::new();
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/vectors/archive_validation/archives.bin.gz");
    GzDecoder::new(std::fs::File::open(path).unwrap()).read_to_end(&mut archives).unwrap();
    let folder = common::work_folder("archive-validation");
    let mut failures = Vec::new();
    let cases = record["cases"].as_array().unwrap();
    for case in cases {
        let name = case["name"].as_str().unwrap();
        let offset = case["offset"].as_u64().unwrap() as usize;
        let length = case["length"].as_u64().unwrap() as usize;
        let archive = folder.join(format!("{name}.sylvia-gm-campaign"));
        std::fs::write(&archive, &archives[offset..offset + length]).unwrap();
        let got = match validate_campaign_archive(&archive, case["expected_campaign_id"].as_str().unwrap()) {
            Ok(manifest) => json!({"ok": sha256_hex(json_bytes(&Json::Object(manifest)))}),
            Err(error) => match error.rejection() {
                Some(coded) => {
                    json!({"error": coded.error_type.unwrap_or("CommandRejected"), "code": coded.code, "message": coded.message})
                }
                None => json!({"fault": format!("{error:?}")}),
            },
        };
        if got != case["verdict"] {
            failures.push(format!("{name}: Rust {got}, Python {}", case["verdict"]));
        }
    }
    assert_eq!(cases.len(), 76);
    assert!(failures.is_empty(), "{} cases differ:\n{}", failures.len(), failures.join("\n"));
}
