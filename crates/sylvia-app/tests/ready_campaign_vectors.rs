//! Compare `sylvia_app::ready_campaigns::ready_campaign_catalog` with the frozen Python on the
//! shipped prepared-campaign inventory and on damaged copies of it.
//!
//! Regenerate with `tests/vectors/generate_ready_campaign_vectors.py` (needs the snapshot's
//! Python). The owner commands are proven by the `ready-campaigns` comparison session.

use std::path::PathBuf;
use sylvia_app::bundled_packages::{BundledPackages, BundledSources};
use sylvia_base::hash::sha256_hex;
use sylvia_base::pyjson;
use sylvia_base::testing::json_difference;
use sylvia_base::Json;
use sylvia_domain::package_library::canonical_json_bytes;

fn repository() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
}

/// The generator's `edited`: each `set` or `delete` at a JSON pointer, in order.
fn edited(shipped: &Json, edits: &[Json]) -> Json {
    let mut value = shipped.clone();
    for edit in edits {
        let (action, pointer) = (edit[0].as_str().unwrap(), edit[1].as_str().unwrap());
        let (parent, last) = pointer.rsplit_once('/').unwrap();
        let container = value.pointer_mut(parent).unwrap();
        match (action, container) {
            ("set", Json::Array(items)) => items[last.parse::<usize>().unwrap()] = edit[2].clone(),
            ("set", Json::Object(map)) => {
                map.insert(last.to_owned(), edit[2].clone());
            }
            ("delete", Json::Array(items)) => {
                items.remove(last.parse::<usize>().unwrap());
            }
            ("delete", Json::Object(map)) => {
                map.shift_remove(last);
            }
            other => panic!("unexpected edit {other:?}"),
        }
    }
    value
}

#[test]
fn the_catalog_reads_the_inventory_as_python_does() {
    let record: Json = pyjson::loads(include_str!("vectors/ready_campaigns/catalog_vectors.json"))
        .expect("vector file is valid JSON");
    let raw = std::fs::read(repository().join("src/sylvia_gm/campaign/ready-campaigns.json")).unwrap();
    let shipped: Json = pyjson::loads_bytes(&raw).unwrap();
    let folder = std::env::temp_dir().join(format!("sgrc-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&folder);
    std::fs::create_dir_all(&folder).unwrap();
    let inventory = folder.join("ready-campaigns.json");
    let mut sources = BundledSources::from_repository_root(&repository());
    sources.ready_campaigns = inventory.clone();
    let bundled = BundledPackages::new(sources);
    let mut failures = Vec::new();
    for case in record["cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let data = if let Some(text) = case.get("raw").and_then(Json::as_str) {
            text.as_bytes().to_vec()
        } else if let Some(pad) = case.get("raw_pad").and_then(Json::as_u64) {
            let mut data = raw.clone();
            data.extend(std::iter::repeat_n(b' ', pad as usize));
            data
        } else {
            serde_json::to_vec(&edited(&shipped, case["edits"].as_array().unwrap())).unwrap()
        };
        std::fs::write(&inventory, data).unwrap();
        let got = match sylvia_app::ready_campaigns::ready_campaign_catalog(&bundled) {
            Ok(catalog) => {
                let catalog = Json::Array(catalog.into_iter().map(Json::Object).collect());
                if name == "shipped" {
                    serde_json::json!({ "ok": catalog })
                } else {
                    serde_json::json!({ "ok_sha256": sha256_hex(canonical_json_bytes(&catalog)) })
                }
            }
            Err(error) => serde_json::json!({"error": error.python_class(), "message": error.message()}),
        };
        if let Some(difference) = json_difference(&got, &case["outcome"]) {
            failures.push(format!("{name}: {difference}"));
        }
    }
    let _ = std::fs::remove_dir_all(&folder);
    assert!(failures.is_empty(), "{} catalog cases differ:\n{}", failures.len(), failures.join("\n"));
}
