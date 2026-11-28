//! `private_records::decode` against Python's `private_records_archive.decode`
//! (`tests/vectors/private_records_decode.json`, written by
//! `generate_private_records_decode_vectors.py`): each hand-made member decodes to Python's rows,
//! in Python's key order, or is refused with Python's code and message.

use serde_json::json;
use std::collections::HashSet;
use std::path::Path;
use sylvia_base::testing::json_difference;
use sylvia_base::Json;
use sylvia_store::private_records::decode;

#[test]
fn members_decode_as_pythons_archive_reader() {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/vectors/private_records_decode.json");
    let record: Json = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    let sessions: HashSet<String> =
        record["session_ids"].as_array().unwrap().iter().map(|id| id.as_str().unwrap().to_owned()).collect();
    let cases = record["cases"].as_array().unwrap();
    assert_eq!(cases.len(), 20);
    let mut failures = Vec::new();
    for case in cases {
        let data = hex::decode(case["hex"].as_str().unwrap()).unwrap();
        let got = match decode(&data, &sessions) {
            Ok(rows) => json!({"rows": rows}),
            Err(error) => {
                let coded = error.rejection().expect("a rejection");
                json!({
                    "error": coded.error_type.unwrap_or("CommandRejected"),
                    "code": coded.code,
                    "message": coded.message,
                })
            }
        };
        if let Some(difference) = json_difference(&got, &case["outcome"]) {
            failures.push(format!("{}: {difference}", case["name"]));
        }
    }
    assert!(failures.is_empty(), "{} members decode differently:\n{}", failures.len(), failures.join("\n"));
}
