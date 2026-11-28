//! `PackageFileReader` (one connection for many package file reads) answers exactly as
//! `AuthorityStore::package_file_bytes` (a connection for each read, as Python's): on the bundled
//! Rift closure installed through `installed_base`, every installed file is read both ways, and
//! then the refusals are compared too: a missing path, a file over its byte bound, a negative
//! bound, a corrupted chunk and a missing chunk.

mod installed_base;

use std::path::Path;
use sylvia_app::bundled_packages::{BundledPackages, BundledSources};
use sylvia_store::{AuthorityStore, StoreError, StoreHandle, StoreResult};

type File = (String, String, String, String);

fn answer(result: StoreResult<Option<Vec<u8>>>) -> String {
    match result {
        Ok(None) => "none".to_owned(),
        Ok(Some(bytes)) => format!("{} bytes {}", bytes.len(), sylvia_base::hash::sha256_hex(&bytes)),
        Err(StoreError::Coded(coded)) => format!("refused {} {}", coded.code, coded.message),
        Err(other) => format!("error {other}"),
    }
}

/// Each file both ways, with `maximum_bytes`.
fn both(store: &AuthorityStore, files: &[File], maximum_bytes: i64) -> Vec<(String, String)> {
    let reader = store.package_file_reader().unwrap();
    files
        .iter()
        .map(|(id, version, sha, path)| {
            (
                answer(store.package_file_bytes(id, version, sha, path, maximum_bytes)),
                answer(reader.file_bytes(id, version, sha, path, maximum_bytes)),
            )
        })
        .collect()
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn one_reader_answers_as_a_connection_for_each_read() {
    let folder = Path::new(env!("CARGO_TARGET_TMPDIR")).join("sylvia-app").join("package-file-reader");
    if let Err(error) = std::fs::remove_dir_all(&folder) {
        assert_eq!(error.kind(), std::io::ErrorKind::NotFound, "cannot clear {}: {error}", folder.display());
    }
    std::fs::create_dir_all(&folder).unwrap();
    let database = folder.join("authority.sqlite3");
    installed_base::seed_copy(&database);
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    let bundled = BundledPackages::new(BundledSources::from_repository_root(&root));
    let path = database.clone();
    let (handle, thread) =
        StoreHandle::spawn(move || AuthorityStore::open_for_application_owner(&path, None)).unwrap();
    installed_base::install(&handle, bundled.bundled_rift_archive().unwrap(), "rift").await;
    drop(handle);
    tokio::task::spawn_blocking(move || thread.join()).await.unwrap();

    let raw = rusqlite::Connection::open(&database).unwrap();
    let mut files: Vec<File> = raw
        .prepare("SELECT package_id, version, content_sha256, portable_path FROM package_manifest_files ORDER BY 1, 2, 3, 4")
        .unwrap()
        .query_map([], |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)))
        .unwrap()
        .collect::<Result<_, _>>()
        .unwrap();
    // A file of several chunks and one of a single chunk, to corrupt below.
    let chunked = |single: bool| -> (String, File) {
        let (blob, file): (String, File) = raw
            .query_row(
                &format!(
                    "SELECT f.blob_sha256, f.package_id, f.version, f.content_sha256, f.portable_path \
                     FROM package_manifest_files f JOIN package_blobs b ON b.blob_sha256 = f.blob_sha256 \
                     WHERE b.chunk_count {} 1 ORDER BY b.byte_count DESC, f.portable_path LIMIT 1",
                    if single { "=" } else { ">" }
                ),
                [],
                |row| Ok((row.get(0)?, (row.get(1)?, row.get(2)?, row.get(3)?, row.get(4)?))),
            )
            .unwrap();
        (blob, file)
    };
    let (several_blob, several) = chunked(false);
    let (single_blob, single) = chunked(true);
    let first = files[0].clone();
    files.push((first.0.clone(), first.1.clone(), first.2.clone(), "no/such/file.md".to_owned()));
    files.push(("no.such.package".to_owned(), first.1.clone(), first.2.clone(), first.3.clone()));

    let store = AuthorityStore::open_for_application_owner(&database, None).unwrap();
    let mut failures = Vec::new();
    let mut check = |label: &str, answers: Vec<(String, String)>| {
        for (index, (each, shared)) in answers.into_iter().enumerate() {
            if each != shared {
                failures.push(format!("{label} #{index}: a connection each {each}, one reader {shared}"));
            }
        }
    };
    let whole = both(&store, &files, 16 * 1024 * 1024);
    assert!(
        whole.iter().filter(|(each, _)| each.contains(" bytes ")).count() > 300,
        "the Rift files were read"
    );
    assert_eq!(whole.iter().filter(|(each, _)| each == "none").count(), 2);
    check("whole", whole);
    let bounded = both(&store, &files, 4096);
    assert!(bounded.iter().any(|(each, _)| each.contains("package_file_oversized")));
    check("bounded", bounded);
    let negative = both(&store, &files[..1], -1);
    assert!(negative[0].0.contains("cannot be negative"), "{}", negative[0].0);
    check("negative", negative);

    // A changed byte in a chunk of each, then a chunk removed from the several-chunk file.
    for blob in [&several_blob, &single_blob] {
        raw.execute(
            "UPDATE package_blob_chunks SET data = CAST((CASE WHEN substr(data, 1, 1) = X'00'              THEN X'01' ELSE X'00' END) || substr(data, 2) AS BLOB) \
             WHERE blob_sha256 = ? AND chunk_order = 0",
            [blob],
        )
        .unwrap();
    }
    let corrupted = both(&store, &[several.clone(), single.clone()], 16 * 1024 * 1024);
    assert!(corrupted.iter().all(|(each, _)| each.contains("package_corrupt")), "{corrupted:?}");
    check("corrupted", corrupted);
    raw.execute(
        "DELETE FROM package_blob_chunks WHERE blob_sha256 = ? AND chunk_order = \
         (SELECT max(chunk_order) FROM package_blob_chunks WHERE blob_sha256 = ?)",
        [&several_blob, &several_blob],
    )
    .unwrap();
    let missing = both(&store, &[several], 16 * 1024 * 1024);
    assert!(missing[0].0.contains("package_corrupt"), "{}", missing[0].0);
    check("missing chunk", missing);
    assert!(failures.is_empty(), "{} reads differ:\n{}", failures.len(), failures.join("\n"));
}
