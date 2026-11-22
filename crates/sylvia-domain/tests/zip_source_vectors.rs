//! The file-backed `zipfile` reader against the in-memory one: every repository archive and every
//! hostile archive of `vectors/archive_vectors.json` (truncated, patched, prefixed and rebuilt)
//! opened both ways must give the same directory, the same archive comment and the same bytes
//! or the same failure, member by member.

use serde_json::Value as Json;
use sylvia_base::hash::sha256_hex;
use sylvia_domain::package_archive::zipfile::{ReadError, ZipArchive, ZipError};

fn vectors() -> Json {
    serde_json::from_str(include_str!("vectors/archive_vectors.json")).expect("vector file is valid JSON")
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

/// The archive generator's `patch(data, edits)`.
fn patch(base: &[u8], edits: &[Json]) -> Vec<u8> {
    let mut data = base.to_vec();
    for edit in edits {
        match edit[0].as_str().unwrap() {
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

/// Everything one reader says about an archive: the open's outcome, the comment, each
/// member's directory entry, and each member's bytes (its SHA-256) or read failure.
fn reading(archive: Result<ZipArchive<'_>, ZipError>) -> Vec<String> {
    let archive = match archive {
        Ok(archive) => archive,
        Err(error) => return vec![format!("open {}: {}", error.python_class(), error.message())],
    };
    let mut lines = vec![format!("comment {}", hex::encode(archive.comment()))];
    for info in archive.infos() {
        lines.push(format!("{info:?}"));
        let mut data = Vec::new();
        let outcome = archive.read_chunks(info, 65_536, |chunk| {
            data.extend_from_slice(chunk);
            Ok::<(), ()>(())
        });
        lines.push(match outcome {
            Ok(()) => format!("read {}", sha256_hex(&data)),
            Err(ReadError::Zip(error)) => format!("read {}: {}", error.python_class(), error.message()),
            Err(ReadError::Caller(())) => unreachable!("the sink never refuses"),
        });
    }
    lines
}

#[test]
fn file_and_slice_readers_agree() {
    let vectors = vectors();
    let folder = std::env::temp_dir().join(format!("sylvia-domain-zip-source-{}", std::process::id()));
    std::fs::create_dir_all(&folder).unwrap();
    let mut archives: Vec<(String, Vec<u8>)> = vectors["repository"]
        .as_array()
        .unwrap()
        .iter()
        .map(|case| {
            let path = case["path"].as_str().unwrap();
            (path.to_owned(), repository_file(path))
        })
        .collect();
    for (index, case) in vectors["hostile"].as_array().unwrap().iter().enumerate() {
        let data = match case.get("archive").and_then(Json::as_str) {
            Some(archive) => base64_decode(archive),
            None => {
                patch(&repository_file(case["base"].as_str().unwrap()), case["edits"].as_array().unwrap())
            }
        };
        archives.push((format!("hostile #{index}"), data));
    }
    let mut failures = Vec::new();
    for (index, (label, data)) in archives.iter().enumerate() {
        let path = folder.join(format!("archive-{index}.zip"));
        std::fs::write(&path, data).unwrap();
        let file = std::fs::File::open(&path).unwrap();
        let from_slice = reading(ZipArchive::open(data));
        let from_file = reading(ZipArchive::open_file(&file));
        if from_slice != from_file {
            failures.push(format!("{label}:\n  slice {from_slice:?}\n  file  {from_file:?}"));
        }
    }
    let _ = std::fs::remove_dir_all(&folder);
    assert!(archives.len() > 190, "every repository and hostile archive is read");
    assert!(failures.is_empty(), "{} archives differ:\n{}", failures.len(), failures.join("\n"));
}
