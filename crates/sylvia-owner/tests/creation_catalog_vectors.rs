//! The owner's campaign creation catalog and the launch's legacy Builder chat migration against
//! the frozen Python, on lane 5's legacy Builder bases (`sylvia-store/tests/vectors/legacy_builder`).
//!
//! The catalog cases are `vectors/generate_creation_catalog_vectors.py`'s (each base as is and
//! with junk in each saved library, with and without participants). The launch cases are lane
//! 1's (`sylvia-app/tests/vectors/builder_migration_vectors.json`): the catalog Python built on
//! each base, then the migration's result and the storage tree it left.

use flate2::read::GzDecoder;
use serde_json::{json, Value};
use std::io::Read;
use std::path::{Path, PathBuf};
use sylvia_app::bundled_packages::{BundledPackages, BundledSources};
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{Json, JsonMap};
use sylvia_owner::creation_catalog::{campaign_creation_catalog, migrate_legacy_builder_chats};
use sylvia_store::AuthorityStore;

fn repository() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
}

fn catalog_vectors() -> Value {
    serde_json::from_str(include_str!("vectors/creation_catalog_vectors.json"))
        .expect("creation catalog vectors")
}

fn migration_vectors() -> Value {
    serde_json::from_str(include_str!("../../sylvia-app/tests/vectors/builder_migration_vectors.json"))
        .expect("builder migration vectors")
}

fn fixtures() -> PathBuf {
    repository().join("crates/sylvia-store/tests/vectors/legacy_builder")
}

fn bases() -> Value {
    serde_json::from_str::<Value>(&std::fs::read_to_string(fixtures().join("store_runs.json")).unwrap())
        .unwrap()["bases"]
        .clone()
}

/// A fresh work folder: one left by a killed run must not show up later as an unrelated error.
fn fresh_work(name: &str) -> PathBuf {
    let work = Path::new(env!("CARGO_TARGET_TMPDIR")).join(name);
    match std::fs::remove_dir_all(&work) {
        Ok(()) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => panic!("cannot clear the old work folder {}: {error}", work.display()),
    }
    work
}

/// A fresh copy of a base: its database, its saved files, then the case's extra files.
fn prepare(root: &Path, base: &str, bases: &Value, extra: &[(String, Vec<u8>)]) {
    std::fs::create_dir_all(root).unwrap();
    let mut bytes = Vec::new();
    GzDecoder::new(std::fs::File::open(fixtures().join(format!("base-{base}.sqlite3.gz"))).unwrap())
        .read_to_end(&mut bytes)
        .unwrap();
    std::fs::write(root.join("authority.sqlite3"), bytes).unwrap();
    let files = bases[base]["files"]
        .as_object()
        .unwrap()
        .iter()
        .map(|(path, text)| (path.clone(), text.as_str().unwrap().as_bytes().to_vec()));
    for (relative, content) in files.chain(extra.iter().cloned()) {
        let path = root.join(&relative);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, content).unwrap();
    }
}

fn hex(text: &str) -> Vec<u8> {
    (0..text.len()).step_by(2).map(|i| u8::from_str_radix(&text[i..i + 2], 16).unwrap()).collect()
}

fn dumps(value: &Json) -> String {
    pyjson::dumps(value, Dump::DEFAULT)
}

fn rows(catalog: Vec<JsonMap>) -> Json {
    Json::Array(catalog.into_iter().map(Json::Object).collect())
}

#[test]
fn creation_catalog_matches_python() {
    let vectors = catalog_vectors();
    let bases = bases();
    let bundled = BundledPackages::new(BundledSources::from_repository_root(&repository()));
    let work = fresh_work("creation-catalog-vectors");
    let binary: Vec<&str> =
        vectors["binary"].as_array().unwrap().iter().map(|v| v.as_str().unwrap()).collect();
    let cases = vectors["cases"].as_array().unwrap();
    assert!(cases.len() >= 30, "{} cases", cases.len());
    for (index, case) in cases.iter().enumerate() {
        let (base, junk) = (case["base"].as_str().unwrap(), case["junk"].as_str().unwrap());
        let extra: Vec<(String, Vec<u8>)> = vectors["junk"][junk]
            .as_object()
            .unwrap()
            .iter()
            .map(|(path, content)| {
                let content = content.as_str().unwrap();
                (
                    path.clone(),
                    if binary.contains(&junk) { hex(content) } else { content.as_bytes().to_vec() },
                )
            })
            .collect();
        let root = sylvia_provider::codex_rpc::python_resolve(&{
            let root = work.join(format!("case-{index}"));
            std::fs::create_dir_all(&root).unwrap();
            root
        });
        prepare(&root, base, &bases, &extra);
        let store =
            AuthorityStore::open_for_application_owner(&root.join("authority.sqlite3"), None).unwrap();
        if junk == "saved-participant" {
            // The generator's: the first built-in starter, copied into the saved library.
            campaign_creation_catalog(&root, &store, &bundled, true).unwrap();
            let starters = root.join("bundled-srd-starters").join("participants");
            let mut names: Vec<PathBuf> =
                std::fs::read_dir(&starters).unwrap().map(|entry| entry.unwrap().path()).collect();
            names.sort();
            let target = root.join("participants").join(names[0].file_name().unwrap());
            std::fs::create_dir_all(&target).unwrap();
            for file in std::fs::read_dir(&names[0]).unwrap() {
                let file = file.unwrap().path();
                std::fs::copy(&file, target.join(file.file_name().unwrap())).unwrap();
            }
        }
        let participants = case["include_participants"].as_bool().unwrap();
        let outcome = match campaign_creation_catalog(&root, &store, &bundled, participants) {
            Ok(catalog) => json!({"catalog": rows(catalog)}),
            Err(error) => match error.rejection() {
                Some(coded) => json!({"error": [coded.error_type, coded.code, coded.message]}),
                None => json!({"error": ["internal", null, error.to_string()]}),
            },
        };
        drop(store);
        let expected = match case.get("catalog") {
            Some(catalog) => json!({"catalog": catalog}),
            None => json!({"error": case["error"]}),
        };
        assert_eq!(dumps(&outcome), dumps(&expected), "{base} {junk} participants={participants}");
    }
    let _ = std::fs::remove_dir_all(&work);
}

/// The generator's `tree`: every entry under `root` (but the database, the pre-migration copies
/// and the catalog's starters), sorted part by part, files by SHA-256.
fn tree(root: &Path) -> Json {
    fn walk(root: &Path, folder: &Path, entries: &mut Vec<(String, Json)>) {
        for entry in std::fs::read_dir(folder).unwrap() {
            let path = entry.unwrap().path();
            let relative = path.strip_prefix(root).unwrap().to_string_lossy().replace('\\', "/");
            if ["authority.sqlite3", "migration-sources", "bundled-srd-starters"]
                .iter()
                .any(|prefix| relative.starts_with(prefix))
            {
                continue;
            }
            if path.is_dir() {
                entries.push((relative.clone(), json!([relative, "dir", null])));
                walk(root, &path, entries);
            } else {
                let digest = sylvia_base::hash::sha256_hex(std::fs::read(&path).unwrap());
                entries.push((relative.clone(), json!([relative, "file", digest])));
            }
        }
    }
    let mut entries = Vec::new();
    walk(root, root, &mut entries);
    entries.sort_by(|left, right| left.0.split('/').cmp(right.0.split('/')));
    Json::Array(entries.into_iter().map(|(_, entry)| entry).collect())
}

#[test]
fn launch_migration_matches_python() {
    let vectors = migration_vectors();
    let bases = bases();
    let bundled = BundledPackages::new(BundledSources::from_repository_root(&repository()));
    let work = fresh_work("launch-migration-vectors");
    let mut checked = 0;
    for base in ["open", "returned", "stopped"] {
        let label = format!("own-{base}");
        let case = vectors["cases"]
            .as_array()
            .unwrap()
            .iter()
            .find(|case| {
                case["base"] == base
                    && case["catalog"] == label.as_str()
                    && case.get("mutation").is_none_or(Value::is_null)
            })
            .unwrap_or_else(|| panic!("lane 1's {label} case"));
        // The catalog the launch builds is the one Python built on this base.
        let root = sylvia_provider::codex_rpc::python_resolve(&{
            let root = work.join(base);
            std::fs::create_dir_all(&root).unwrap();
            root
        });
        prepare(&root, base, &bases, &[]);
        let store =
            AuthorityStore::open_for_application_owner(&root.join("authority.sqlite3"), None).unwrap();
        let catalog = campaign_creation_catalog(&root, &store, &bundled, true).unwrap();
        // Lane 1 stored these catalogs with sorted keys; the key order itself is checked by the
        // catalog cases above.
        let sorted = |value: &Json| pyjson::dumps(value, Dump { sort_keys: true, ..Dump::DEFAULT });
        assert_eq!(sorted(&rows(catalog)), sorted(&vectors["catalogs"][&label]), "catalog {base}");
        drop(store);
        // Then the launch's migration on a fresh copy: its result and the tree it leaves.
        std::fs::remove_dir_all(&root).unwrap();
        prepare(&root, base, &bases, &[]);
        let store =
            AuthorityStore::open_for_application_owner(&root.join("authority.sqlite3"), None).unwrap();
        let outcome = match migrate_legacy_builder_chats(&store, &bundled, &root) {
            Ok(ids) => json!(["ok", ids]),
            Err(error) => json!(["error", error.to_string()]),
        };
        drop(store);
        assert_eq!(dumps(&outcome), dumps(&case["result"]), "migration {base}");
        assert_eq!(dumps(&tree(&root)), dumps(&case["tree"]), "tree {base}");
        checked += 1;
    }
    assert_eq!(checked, 3);
    let _ = std::fs::remove_dir_all(&work);
}

#[test]
fn a_profile_without_legacy_chats_is_left_alone() {
    let work = fresh_work("launch-migration-fresh");
    std::fs::create_dir_all(&work).unwrap();
    let bundled = BundledPackages::new(BundledSources::from_repository_root(&repository()));
    let store = AuthorityStore::open_for_application_owner(&work.join("authority.sqlite3"), None).unwrap();
    assert_eq!(migrate_legacy_builder_chats(&store, &bundled, &work).unwrap(), Vec::<String>::new());
    drop(store);
    let _ = std::fs::remove_dir_all(&work);
}
