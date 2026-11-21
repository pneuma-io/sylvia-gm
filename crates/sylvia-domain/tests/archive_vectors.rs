//! Compare sylvia_domain::package_archive with the frozen Python's archive admission.
//!
//! Every repository archive is read from its path; hostile archives are rebuilt from the
//! vectors. Regenerate with `tests/vectors/generate_archive_vectors.py` (needs the snapshot's
//! Python). Cases that depend on Windows path handling run only on Windows.

use serde_json::json;
use sylvia_base::hash::sha256_hex;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::Json;
use sylvia_domain::package_archive::admit_package_archive;
use sylvia_domain::package_library::closure_sha256;
use sylvia_domain::DomainError;

fn vectors() -> Json {
    pyjson::loads(include_str!("vectors/archive_vectors.json")).expect("vector file is valid JSON")
}

fn repository_file(path: &str) -> Vec<u8> {
    let full = format!("{}/../../{path}", env!("CARGO_MANIFEST_DIR"));
    std::fs::read(&full).unwrap_or_else(|error| panic!("{full}: {error}"))
}

/// Standard base64, as Python's `base64.b64encode` writes it.
fn base64_decode(text: &str) -> Vec<u8> {
    let value = |c: u8| -> u32 {
        match c {
            b'A'..=b'Z' => u32::from(c - b'A'),
            b'a'..=b'z' => u32::from(c - b'a') + 26,
            b'0'..=b'9' => u32::from(c - b'0') + 52,
            b'+' => 62,
            b'/' => 63,
            other => panic!("not base64: {other}"),
        }
    };
    let mut out = Vec::with_capacity(text.len() * 3 / 4);
    for chunk in text.as_bytes().chunks(4) {
        let digits: Vec<u8> = chunk.iter().copied().filter(|c| *c != b'=').collect();
        let bits = digits.iter().fold(0u32, |acc, c| (acc << 6) | value(*c)) << (6 * (4 - digits.len()));
        let bytes = bits.to_be_bytes();
        out.extend_from_slice(&bytes[1..digits.len()]);
    }
    out
}

/// The generator's `patch(data, edits)`.
fn patch(base: &[u8], edits: &[Json]) -> Vec<u8> {
    let mut data = base.to_vec();
    for edit in edits {
        let op = edit[0].as_str().unwrap();
        match op {
            "patch" => {
                let offset = edit[1].as_u64().unwrap() as usize;
                let bytes = hex::decode(edit[2].as_str().unwrap()).unwrap();
                let end = (offset + bytes.len()).min(data.len());
                data.splice(offset..end, bytes);
            }
            "truncate" => data.truncate(edit[1].as_u64().unwrap() as usize),
            "append" => data.extend(hex::decode(edit[1].as_str().unwrap()).unwrap()),
            "prepend" => {
                let bytes = hex::decode(edit[1].as_str().unwrap()).unwrap();
                data.splice(0..0, bytes);
            }
            other => panic!("unknown edit {other}"),
        }
    }
    data
}

/// Rust's outcome in the vector's form.
fn outcome(data: &[u8]) -> Json {
    match admit_package_archive(data) {
        Ok(admitted) => {
            let first_file = admitted.files.first().map(|file| sha256_hex(admitted.read_file(file).unwrap()));
            let projection = json!({
                "format_version": admitted.format_version,
                "manifests": admitted.manifests.iter().map(|m| m.canonical_json.clone()).collect::<Vec<_>>(),
                "files": admitted.files.iter().map(|f| json!([f.manifest, f.file.path(), f.file.byte_count(),
                    f.file.sha256(), f.member_name])).collect::<Vec<_>>(),
                "closure": closure_sha256(&admitted.package_lock),
                "first_file": first_file,
            });
            json!(["ok", sha256_hex(pyjson::dumps(&projection, Dump::DEFAULT))])
        }
        Err(DomainError::Uncaught { error_type, .. }) => json!(["uncaught", error_type]),
        Err(error @ DomainError::Rejected { .. }) => json!([
            error.python_class(),
            error.code(),
            error.message(),
            error.diagnostics().iter().map(|d| Json::Object(d.as_payload())).collect::<Vec<_>>(),
        ]),
        Err(other) => json!(["unexpected", other.python_class(), other.message()]),
    }
}

/// docs/rust-port/differences.md row 9: a file byte count beyond 64 bits is refused when the
/// manifest is read, before Python's later size comparison.
fn row_nine() -> Json {
    json!(["CommandRejected", "archive_invalid", "Package archive admission failed", [{
        "code": "archive_invalid", "phase": "archive", "message": "Package file byte count is too large",
        "file": "manifest.json"}]])
}

/// docs/rust-port/differences.md row 1: an integer beyond 64 bits is refused when the
/// manifest JSON is read.
fn row_one() -> Json {
    json!(["CommandRejected", "archive_invalid", "Package archive admission failed", [{
        "code": "archive_invalid", "phase": "archive", "message": "Package manifest is not strict canonical JSON",
        "file": "manifest.json"}]])
}

#[test]
fn repository_archives_admit_like_python() {
    let vectors = vectors();
    let cases = vectors["repository"].as_array().unwrap();
    assert_eq!(cases.len(), 30);
    let mut failures = Vec::new();
    for case in cases {
        let path = case["path"].as_str().unwrap();
        let actual = outcome(&repository_file(path));
        if actual != case["outcome"] {
            failures.push(format!("{path}: python {}, rust {actual}", case["outcome"]));
        }
    }
    assert!(failures.is_empty(), "{} differ: {:#?}", failures.len(), failures);
}

#[test]
fn hostile_archives_fail_like_python() {
    let vectors = vectors();
    let cases = vectors["hostile"].as_array().unwrap();
    assert!(cases.len() > 150);
    let mut failures = Vec::new();
    let mut checked = 0;
    for case in cases {
        if case["windows_only"] == Json::Bool(true) && !cfg!(windows) {
            continue;
        }
        let data = if let Some(archive) = case.get("archive").and_then(Json::as_str) {
            base64_decode(archive)
        } else {
            patch(&repository_file(case["base"].as_str().unwrap()), case["edits"].as_array().unwrap())
        };
        let expected = match case["label"].as_str() {
            Some("record file huge") => row_nine(),
            Some("record file beyond 64 bits") => row_one(),
            _ => case["outcome"].clone(),
        };
        let actual = outcome(&data);
        checked += 1;
        if actual != expected {
            failures.push(format!(
                "{} {}: python {expected}, rust {actual}",
                case["base"],
                case.get("label").or(case.get("edits")).unwrap()
            ));
        }
    }
    assert!(checked > 150);
    assert!(
        failures.is_empty(),
        "{} of {checked} differ, first: {:#?}",
        failures.len(),
        &failures[..failures.len().min(4)]
    );
}

#[test]
fn base64_decoder_round_trips() {
    assert_eq!(base64_decode("aGVsbG8="), b"hello");
    assert_eq!(base64_decode("aGVsbG8hIQ=="), b"hello!!");
    assert_eq!(base64_decode("YWJj"), b"abc");
}

/// `unicodedata` in CPython 3.12.11 is Unicode 15.0.0; `unicode-normalization` is pinned to the
/// release with the same tables, so NFC checks of member names agree.
#[test]
fn nfc_tables_match_python_unicode_version() {
    assert_eq!(unicode_normalization::UNICODE_VERSION, (15, 0, 0));
}
