//! Compare sylvia_app's package_materialization and bundled_packages with the frozen Python,
//! byte for byte on every bundled archive.
//!
//! Regenerate with `tests/vectors/generate_bundled_vectors.py` (needs the snapshot's Python).

use serde_json::json;
use std::path::PathBuf;
use sylvia_app::bundled_packages::{BundledPackages, BundledSources};
use sylvia_app::package_materialization::{
    materialize_package_archive, snapshot_record, NormalizedPackageDraft,
};
use sylvia_base::hash::sha256_hex;
use sylvia_base::{pyjson, Json, JsonMap};
use sylvia_domain::package_archive::zipfile::ZipArchive;
use sylvia_domain::package_library::manifest_from_record;
use sylvia_domain::DomainResult;

fn vectors() -> Json {
    pyjson::loads(include_str!("vectors/bundled_vectors.json")).expect("vector file is valid JSON")
}

fn repository_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
}

fn outcome<T>(result: DomainResult<T>, project: impl Fn(T) -> Json) -> Json {
    match result {
        Ok(value) => json!(["ok", project(value)]),
        Err(error) => json!([error.python_class(), error.message()]),
    }
}

#[test]
fn bundled_archives_and_facts_match_python() {
    let vectors = vectors();
    let expected = &vectors["bundled"];
    let packages = BundledPackages::new(BundledSources::from_repository_root(&repository_root()));
    assert_eq!(
        sha256_hex(packages.bundled_srd_archive().unwrap()),
        expected["srd_archive"],
        "bundled_srd_archive"
    );
    assert_eq!(
        sha256_hex(packages.bundled_rift_archive().unwrap()),
        expected["rift_archive"],
        "bundled_rift_archive"
    );
    assert_eq!(
        sha256_hex(packages.bundled_riichi_archive().unwrap()),
        expected["riichi_archive"],
        "bundled_riichi_archive"
    );
    for (package_id, archive) in expected["update_archives"].as_object().unwrap() {
        let actual = packages.bundled_ruleset_update_archive(package_id).unwrap().map(sha256_hex);
        assert_eq!(json!(actual), *archive, "bundled_ruleset_update_archive({package_id})");
    }
    assert_eq!(packages.bundled_srd_version().unwrap(), expected["srd_version"]);
    let authority = packages.bundled_rift_authority().unwrap();
    assert_eq!(
        json!({
            "ruleset": authority.ruleset.as_payload(),
            "setting": authority.setting.as_payload(),
            "closure_sha256": authority.closure_sha256,
            "setting_paths": authority.setting_paths,
            "setting_knowledge_ids": authority.setting_knowledge_ids,
        }),
        expected["authority"]
    );
    let declaration = packages.bundled_rift_participant_declaration().unwrap();
    assert_eq!(
        json!({"kind": declaration.kind, "schema_id": declaration.schema_id, "schema_sha256": declaration.schema_sha256}),
        expected["participant_declaration"]
    );
    let contribution =
        packages.bundled_rift_interview_contribution().unwrap().map(|c| Json::Object(c.as_payload()));
    assert_eq!(contribution.unwrap_or(Json::Null), expected["interview_contribution"]);
    for (query, paths) in expected["source_catalog"].as_object().unwrap() {
        assert_eq!(json!(packages.bundled_rift_source_catalog(query).unwrap()), *paths, "catalog {query:?}");
    }
    for (path, read) in expected["source_reads"].as_object().unwrap() {
        let actual =
            outcome(packages.read_bundled_rift_source(path), |text| json!(sha256_hex(text.as_bytes())));
        assert_eq!(actual, *read, "read {path}");
    }
}

/// The Riichi archive's records and payloads: the generator's materialization base.
fn riichi_base(vectors: &Json) -> Json {
    let bytes = std::fs::read(repository_root().join(vectors["riichi"].as_str().unwrap())).unwrap();
    let archive = ZipArchive::open(&bytes).unwrap();
    let read = |name: &str| -> Vec<u8> {
        let info = archive.infos().iter().find(|info| info.filename == name).unwrap();
        let mut data = Vec::new();
        archive
            .read_chunks(info, 64 * 1024, |chunk| -> Result<(), ()> {
                data.extend_from_slice(chunk);
                Ok(())
            })
            .unwrap();
        data
    };
    let manifest = pyjson::loads(&String::from_utf8(read("manifest.json")).unwrap()).unwrap();
    let mut payloads = JsonMap::new();
    for record in manifest["packages"].as_array().unwrap() {
        let id = record["id"].as_str().unwrap();
        let prefix = format!("packages/{id}/");
        let mut files = JsonMap::new();
        for info in archive.infos() {
            if let Some(path) = info.filename.strip_prefix(&prefix) {
                files.insert(path.to_owned(), Json::String(String::from_utf8(read(&info.filename)).unwrap()));
            }
        }
        payloads.insert(id.to_owned(), Json::Object(files));
    }
    json!({"records": manifest["packages"], "payloads": payloads, "version": manifest["format"]})
}

fn step<'a>(document: &'a mut Json, path: &[Json]) -> &'a mut Json {
    path.iter().fold(document, |node, part| match part {
        Json::String(key) => &mut node[key.as_str()],
        index => &mut node[index.as_u64().unwrap() as usize],
    })
}

/// The generator's `apply(document, edits)`.
fn apply(base: &Json, edits: &[Json]) -> Json {
    let mut document = base.clone();
    for edit in edits {
        let path = edit[1].as_array().unwrap();
        let (last, parents) = path.split_last().unwrap();
        match edit[0].as_str().unwrap() {
            "set" => match last {
                Json::String(key) => {
                    step(&mut document, parents)
                        .as_object_mut()
                        .unwrap()
                        .insert(key.clone(), edit[2].clone());
                }
                index => step(&mut document, parents)[index.as_u64().unwrap() as usize] = edit[2].clone(),
            },
            "delete" => match last {
                Json::String(key) => {
                    step(&mut document, parents).as_object_mut().unwrap().shift_remove(key.as_str());
                }
                index => {
                    step(&mut document, parents)
                        .as_array_mut()
                        .unwrap()
                        .remove(index.as_u64().unwrap() as usize);
                }
            },
            "reverse" => step(&mut document, path).as_array_mut().unwrap().reverse(),
            "copy" => {
                let value = step(&mut document, path).clone();
                *step(&mut document, edit[2].as_array().unwrap()) = value;
            }
            other => panic!("unknown edit {other}"),
        }
    }
    document
}

#[test]
fn materialization_matches_python() {
    let vectors = vectors();
    let base = riichi_base(&vectors);
    let mut failures = Vec::new();
    for case in vectors["materialize"].as_array().unwrap() {
        let inputs = apply(&base, case["edits"].as_array().unwrap());
        let version = inputs["version"].as_i64().unwrap();
        let drafts: Vec<NormalizedPackageDraft> = inputs["records"]
            .as_array()
            .unwrap()
            .iter()
            .map(|record| {
                let payloads = record
                    .get("id")
                    .and_then(Json::as_str)
                    .and_then(|id| inputs["payloads"].get(id))
                    .and_then(Json::as_object)
                    .map(|files| {
                        files
                            .iter()
                            .map(|(path, text)| (path.clone(), text.as_str().unwrap().as_bytes().to_vec()))
                            .collect()
                    })
                    .unwrap_or_default();
                NormalizedPackageDraft::new(record.as_object().unwrap().clone(), payloads, version)
            })
            .collect();
        let actual = outcome(materialize_package_archive(&drafts), |m| {
            json!({
                "archive": sha256_hex(&m.archive),
                "closure": m.closure_sha256,
                "manifests": m.manifests.iter().map(|item| item.package_id.clone()).collect::<Vec<_>>(),
            })
        });
        if actual != case["outcome"] {
            failures.push(format!("{}: python {}, rust {actual}", case["edits"], case["outcome"]));
        }
    }
    let records = base["records"].as_array().unwrap();
    let version = Some(base["version"].as_i64().unwrap());
    let primary = manifest_from_record(&records[0], version).unwrap();
    let setting = manifest_from_record(&records[1], version).unwrap();
    for case in vectors["snapshot"].as_array().unwrap() {
        let record = case["record"].as_object().unwrap();
        let actual =
            json!(["ok", Json::Object(snapshot_record(record, &primary, std::slice::from_ref(&setting)))]);
        if actual != case["outcome"] {
            failures.push(format!("snapshot: python {}, rust {actual}", case["outcome"]));
        }
    }
    let shown: Vec<String> = failures.iter().take(6).map(|f| f.chars().take(900).collect()).collect();
    assert!(failures.is_empty(), "{} differ: {:#?}", failures.len(), shown);
}
