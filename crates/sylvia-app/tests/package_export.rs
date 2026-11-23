//! Package candidate exports against Python (`tests/vectors/package_export/package_export_runs.json`,
//! written by `generate_package_export_runs.py`): every installed candidate of four Python-made
//! profiles, and one that does not exist. Each result, or refusal, must be Python's, and the bytes
//! written must be Python's archive, byte for byte.

use flate2::read::GzDecoder;
use serde_json::json;
use std::io::Read;
use std::path::Path;
use sylvia_app::authority::portable;
use sylvia_base::Json;
use sylvia_store::{AuthorityStore, StoreHandle};

fn base(name: &str) -> Vec<u8> {
    let vectors = Path::new(env!("CARGO_MANIFEST_DIR")).join("../sylvia-store/tests/vectors");
    let relative = match name {
        "cast" => "stored_authority/cast.sqlite3.gz",
        "session_pages" => "session_pages/seed.sqlite3.gz",
        "campaigns" => "campaigns/seed.sqlite3.gz",
        "install" => "packages/install.sqlite3.gz",
        other => panic!("unknown base {other}"),
    };
    let mut bytes = Vec::new();
    GzDecoder::new(std::fs::File::open(vectors.join(relative)).unwrap()).read_to_end(&mut bytes).unwrap();
    bytes
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn package_exports_match_python() {
    let record: Json = serde_json::from_str(
        &std::fs::read_to_string(
            Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("tests/vectors/package_export/package_export_runs.json"),
        )
        .unwrap(),
    )
    .unwrap();
    let runs = record["runs"].as_array().unwrap();
    assert_eq!(runs.len(), 10);
    let mut failures = Vec::new();
    for name in ["cast", "session_pages", "campaigns", "install"] {
        let folder =
            Path::new(env!("CARGO_TARGET_TMPDIR")).join("sylvia-app").join(format!("package-export-{name}"));
        if let Err(error) = std::fs::remove_dir_all(&folder) {
            assert_eq!(
                error.kind(),
                std::io::ErrorKind::NotFound,
                "cannot clear the old work folder {}: {error}",
                folder.display()
            );
        }
        std::fs::create_dir_all(&folder).unwrap();
        let database = folder.join("authority.sqlite3");
        std::fs::write(&database, base(name)).unwrap();
        let (handle, thread): (StoreHandle, _) =
            StoreHandle::spawn(move || AuthorityStore::open_for_application_owner(&database, None)).unwrap();
        for run in runs.iter().filter(|run| run["base"] == name) {
            let candidate_id = run["candidate_id"].as_str().unwrap();
            let mut written = Vec::new();
            let outcome = match portable::export_package_candidate(&handle, candidate_id, &mut written).await
            {
                Ok(result) => json!({ "ok": result }),
                Err(error) => {
                    let coded = error.rejection().unwrap_or_else(|| panic!("an unexpected failure: {error}"));
                    json!({"error": coded.error_type.unwrap_or("CommandRejected"), "code": coded.code, "message": coded.message})
                }
            };
            let got = json!({
                "outcome": outcome,
                "written_sha256": sylvia_base::hash::sha256_hex(&written),
                "written_bytes": written.len(),
            });
            let want = json!({
                "outcome": run["outcome"],
                "written_sha256": run["written_sha256"],
                "written_bytes": run["written_bytes"],
            });
            let (got, want) = (got.to_string(), want.to_string());
            if got != want {
                failures.push(format!("{name} {candidate_id}\n  Rust   {got}\n  Python {want}"));
            }
        }
        drop(handle);
        tokio::task::spawn_blocking(move || thread.join()).await.unwrap();
    }
    assert!(failures.is_empty(), "{} exports differ:\n{}", failures.len(), failures.join("\n"));
}
