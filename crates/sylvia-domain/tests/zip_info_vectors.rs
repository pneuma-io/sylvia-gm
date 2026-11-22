//! The central-directory fields of sylvia_domain's `zipfile` reader against the frozen Python
//! (`tests/vectors/zip_info_vectors.json`, written by `generate_zip_info_vectors.py`): each
//! member's `date_time`, `create_system`, `internal_attr`, `external_attr`, `extra` and `comment`,
//! and the archive's `comment`.

use serde_json::{json, Value};
use sylvia_domain::package_archive::zipfile::ZipArchive;

#[test]
fn central_directory_fields_match_python() {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/vectors/zip_info_vectors.json");
    let record: Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    for case in record["cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let bytes = hex::decode(case["archive"].as_str().unwrap()).unwrap();
        let archive = ZipArchive::open(&bytes).unwrap_or_else(|error| panic!("{name}: {error:?}"));
        assert_eq!(hex::encode(archive.comment()), case["comment"].as_str().unwrap(), "{name}: comment");
        let members: Vec<Value> = archive
            .infos()
            .iter()
            .map(|info| {
                let (year, month, day, hour, minute, second) = info.date_time;
                json!({
                    "filename": info.filename,
                    "date_time": [year, month, day, hour, minute, second],
                    "create_system": info.create_system,
                    "internal_attr": info.internal_attr,
                    "external_attr": info.external_attr,
                    "extra": hex::encode(&info.extra),
                    "comment": hex::encode(&info.comment),
                    "flag_bits": info.flag_bits,
                    "compress_type": info.compress_type,
                    "file_size": info.file_size,
                })
            })
            .collect();
        assert_eq!(Value::Array(members), case["members"], "{name}: members");
    }
}
