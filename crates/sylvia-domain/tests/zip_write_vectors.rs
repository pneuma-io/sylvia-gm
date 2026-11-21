//! Compare sylvia_domain's port of `zipfile`'s write path with the frozen Python's bytes.
//!
//! Regenerate with `tests/vectors/generate_zip_write_vectors.py` (needs the snapshot's Python).
//! Inputs are rebuilt from the repository's package archives and from the synthetic recipes.

use sylvia_base::hash::sha256_hex;
use sylvia_base::{pyjson, Json};
use sylvia_domain::package_archive::zipfile::{ReadError, ZipArchive, ZIP_DEFLATED, ZIP_STORED};
use sylvia_domain::package_archive::zipfile_write::{ZipMember, ZipWriter};

/// `(member name, bytes)` in directory order.
type Members = Vec<(String, Vec<u8>)>;

fn vectors() -> Json {
    pyjson::loads(include_str!("vectors/zip_write_vectors.json")).expect("vector file is valid JSON")
}

fn repository_file(path: &str) -> Vec<u8> {
    let full = format!("{}/../../{path}", env!("CARGO_MANIFEST_DIR"));
    std::fs::read(&full).unwrap_or_else(|error| panic!("{full}: {error}"))
}

/// `(name, data)` for every member, in directory order.
fn members(archive_bytes: &[u8]) -> Members {
    let archive = ZipArchive::open(archive_bytes).expect("a repository archive opens");
    archive
        .infos()
        .iter()
        .map(|info| {
            let mut data = Vec::new();
            archive
                .read_chunks(info, 64 * 1024, |chunk| -> Result<(), ()> {
                    data.extend_from_slice(chunk);
                    Ok(())
                })
                .map_err(|error| match error {
                    ReadError::Zip(error) => error.message().to_owned(),
                    ReadError::Caller(()) => String::new(),
                })
                .expect("a repository member reads");
            (info.filename.clone(), data)
        })
        .collect()
}

/// The generator's `zip_bytes(members, compress_type)`.
fn zip_bytes(members: &[(String, Vec<u8>)], compress_type: u16) -> Vec<u8> {
    let mut writer = ZipWriter::new();
    for (name, data) in members {
        let member = ZipMember {
            filename: name.clone(),
            date_time: (1980, 1, 1, 0, 0, 0),
            compress_type,
            create_system: 3,
            external_attr: 0o100644 << 16,
        };
        writer.writestr(member, data).expect("a member writes");
    }
    writer.finish()
}

fn synthetic(name: &str) -> Vec<u8> {
    match name {
        "empty" => Vec::new(),
        "one" => b"x".to_vec(),
        "zeros" => vec![0; 200_000],
        "text" => b"The Lantern Table. ".repeat(5_000),
        "random" => (0..300_000u64).map(|i| ((i * 7919 + 13) % 251) as u8).collect(),
        "non-ascii name" => b"name with a non-ASCII character".to_vec(),
        other => panic!("unknown synthetic input {other}"),
    }
}

#[test]
fn zip_writes_match_python_byte_for_byte() {
    let vectors = vectors();
    let cases = vectors["cases"].as_array().unwrap();
    assert!(cases.len() > 6000);
    let mut cache: Option<(String, Members)> = None;
    let mut failures = Vec::new();
    for case in cases {
        let actual = if let Some(path) = case["archive"].as_str() {
            if cache.as_ref().is_none_or(|(cached, _)| cached != path) {
                cache = Some((path.to_owned(), members(&repository_file(path))));
            }
            let all = &cache.as_ref().unwrap().1;
            match case["member"].as_str() {
                Some(name) => {
                    let one: Vec<_> = all.iter().filter(|(member, _)| member == name).cloned().collect();
                    vec![("deflated", sha256_hex(zip_bytes(&one[..1], ZIP_DEFLATED)))]
                }
                None => vec![
                    ("deflated", sha256_hex(zip_bytes(all, ZIP_DEFLATED))),
                    ("stored", sha256_hex(zip_bytes(all, ZIP_STORED))),
                ],
            }
        } else {
            let member = case["member"].as_str().unwrap().to_owned();
            let data = synthetic(case["synthetic"].as_str().unwrap());
            vec![("deflated", sha256_hex(zip_bytes(&[(member, data)], ZIP_DEFLATED)))]
        };
        for (key, sha) in actual {
            if case[key].as_str() != Some(sha.as_str()) {
                failures.push(format!("{} {} {key}", case["archive"], case["member"]));
            }
        }
    }
    let python_zlib = vectors["zlib_runtime"].as_str().unwrap();
    assert!(
        failures.is_empty(),
        "{} of {} differ (Python zlib {python_zlib}): {:#?}",
        failures.len(),
        cases.len(),
        &failures[..failures.len().min(6)]
    );
}
