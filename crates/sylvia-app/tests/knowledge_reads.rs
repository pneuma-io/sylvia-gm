//! The GM's campaign knowledge reads against Python (`tests/vectors/knowledge_read/*_runs.json`,
//! written by `generate_knowledge_read_runs.py`): each base built at test time from the bundled
//! Rift closure or SRD Lite package (`installed_base`, every installed table's digest checked),
//! then every search and read replayed through `sylvia_app::authority::knowledge` with the
//! arguments Python's owner took, each answer or failure compared exactly.

mod installed_base;

use serde_json::json;
use std::path::Path;
use sylvia_app::authority::actions::ActionError;
use sylvia_app::authority::knowledge::{read_campaign_reference, search_campaign_knowledge, KnowledgeSearch};
use sylvia_app::bundled_packages::{BundledPackages, BundledSources};
use sylvia_base::Json;
use sylvia_store::{AuthorityStore, StoreError, StoreHandle};

fn record(name: &str) -> Json {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/vectors/knowledge_read").join(name);
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

/// The generator's failure: class, code (none outside the authority errors) and message.
fn failure(error: ActionError) -> Json {
    let (class, code, message) = match error {
        ActionError::Domain(error) => (
            error.python_class().to_owned(),
            error.rejection().map(|coded| coded.code),
            error.message().to_owned(),
        ),
        ActionError::Store(StoreError::Coded(coded)) => {
            (coded.error_type.unwrap_or("CommandRejected").to_owned(), Some(coded.code), coded.message)
        }
        ActionError::Store(other) => ("RustStoreError".to_owned(), None, other.to_string()),
    };
    let authority = !matches!(class.as_str(), "ValueError" | "TypeError" | "KeyError" | "AttributeError");
    json!({"error": class, "code": if authority { code } else { None }, "message": message})
}

fn open(database: &Path) -> (StoreHandle, sylvia_store::handle::StoreThread) {
    let path = database.to_path_buf();
    StoreHandle::spawn(move || AuthorityStore::open_for_application_owner(&path, None)).unwrap()
}

async fn replay(name: &str, archive: fn(&BundledPackages) -> Vec<u8>, steps: usize) {
    let record = record(&format!("{name}_runs.json"));
    let runs = record["runs"].as_array().unwrap();
    assert_eq!(runs.len(), steps, "{name}'s step count");
    let folder = Path::new(env!("CARGO_TARGET_TMPDIR")).join("sylvia-app").join(format!("knowledge-{name}"));
    if let Err(error) = std::fs::remove_dir_all(&folder) {
        assert_eq!(error.kind(), std::io::ErrorKind::NotFound, "cannot clear {}: {error}", folder.display());
    }
    std::fs::create_dir_all(&folder).unwrap();
    let database = folder.join("authority.sqlite3");
    installed_base::seed_copy(&database);
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    let bundled = BundledPackages::new(BundledSources::from_repository_root(&root));
    let (handle, thread) = open(&database);
    installed_base::install(&handle, archive(&bundled), record["install_id"].as_str().unwrap()).await;
    drop(handle);
    tokio::task::spawn_blocking(move || thread.join()).await.unwrap();
    let mismatched =
        installed_base::check_digests(&database, record["installed_table_digests"].as_object().unwrap());
    assert!(mismatched.is_empty(), "the Rust install differs from Python's:\n{}", mismatched.join("\n"));
    installed_base::write_rows(&database, record["setup_tables"].as_object().unwrap());
    let (handle, thread) = open(&database);
    let campaign_id = record["campaign_id"].as_str().unwrap();
    let mut failures = Vec::new();
    for (index, run) in runs.iter().enumerate() {
        let arguments = &run["arguments"];
        let answer = if run["call"] == "search" {
            let request = KnowledgeSearch {
                query: arguments["query"].clone(),
                sources: arguments.get("sources").cloned(),
                limit: arguments["limit"].clone(),
                offset: arguments["offset"].clone(),
            };
            search_campaign_knowledge(&handle, campaign_id, &request).await
        } else {
            read_campaign_reference(
                &handle,
                campaign_id,
                &arguments["source"],
                &arguments["path"],
                arguments.get("maximum_bytes"),
            )
            .await
        };
        let got = match answer {
            Ok(value) => json!({"ok": Json::Object(value)}),
            Err(error) => failure(error),
        };
        let (got, want) = (got.to_string(), run["outcome"].to_string());
        if got != want {
            let cut = |text: &str| text.chars().take(600).collect::<String>();
            failures.push(format!(
                "{name} #{index} {}\n  Rust   {}\n  Python {}",
                run["name"],
                cut(&got),
                cut(&want)
            ));
        }
    }
    drop(handle);
    tokio::task::spawn_blocking(move || thread.join()).await.unwrap();
    assert!(failures.is_empty(), "{} knowledge steps differ:\n{}", failures.len(), failures.join("\n"));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn rift_knowledge_reads_answer_as_pythons_owner() {
    replay("rift", |bundled| bundled.bundled_rift_archive().unwrap(), RIFT_STEPS).await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn srd_knowledge_reads_answer_as_pythons_owner() {
    replay("srd", |bundled| bundled.bundled_srd_archive().unwrap(), SRD_STEPS).await;
}

const RIFT_STEPS: usize = 188;
const SRD_STEPS: usize = 16;
