//! Compare sylvia_app::reusable_participants with the frozen Python: Character File parses, and
//! one save/load/list/delete scenario replayed in a fresh storage root.
//!
//! Regenerate with `tests/vectors/generate_reusable_participant_vectors.py` (needs the snapshot's Python).

use serde_json::json;
use std::path::{Path, PathBuf};
use sylvia_app::reusable_participants::{
    compatible_reusable_participants, delete_reusable_participant, load_reusable_participant,
    parse_setting_character, save_reusable_participant, validate_reusable_participant_draft, InvalidHandler,
    ReusableParticipant,
};
use sylvia_base::hash::sha256_hex;
use sylvia_base::{pyjson, Json, JsonMap};
use sylvia_domain::authority::{PackageLockEntry, PackageRole};
use sylvia_domain::campaign_plan::PackageBinding;
use sylvia_domain::package_archive::zipfile::ZipArchive;
use sylvia_domain::package_library::{
    canonical_json_bytes, manifest_from_record, ParticipantArtifactDeclaration,
};
use sylvia_domain::{DomainError, DomainResult};

fn vectors() -> Json {
    pyjson::loads(include_str!("vectors/reusable_participant_vectors.json"))
        .expect("vector file is valid JSON")
}

fn repository() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
}

fn repository_json(path: &str) -> Json {
    pyjson::loads(&std::fs::read_to_string(repository().join(path)).unwrap().replace("\r\n", "\n")).unwrap()
}

type Context = (PackageLockEntry, ParticipantArtifactDeclaration, PackageBinding);

fn lock_entry(id: &str, version: &str, sha256: &str) -> PackageLockEntry {
    PackageLockEntry::new(id, version, sha256, PackageRole::Primary, 0, Vec::new(), Vec::new()).unwrap()
}

fn binding(spec: &Json) -> PackageBinding {
    PackageBinding {
        package_id: spec[0].as_str().unwrap().into(),
        version: spec[1].as_str().unwrap().into(),
        content_sha256: spec[2].as_str().unwrap().into(),
    }
}

fn contexts(vectors: &Json) -> (Context, Context) {
    let record = repository_json(vectors["srd"].as_str().unwrap());
    let manifest = manifest_from_record(&record, Some(4)).unwrap();
    let declaration =
        manifest.participant_artifacts.iter().find(|item| item.kind == "player_character").unwrap();
    let current = (
        lock_entry(&manifest.package_id, &manifest.version, &manifest.content_sha256),
        declaration.clone(),
        binding(&vectors["setting"]),
    );
    let archive_bytes =
        std::fs::read(repository().join(vectors["legacy_archive"].as_str().unwrap())).unwrap();
    let archive = ZipArchive::open(&archive_bytes).unwrap();
    let info = archive.infos().iter().find(|info| info.filename == "manifest.json").unwrap().clone();
    let mut manifest_bytes = Vec::new();
    archive
        .read_chunks(&info, 65536, |chunk| {
            manifest_bytes.extend_from_slice(chunk);
            Ok::<(), ()>(())
        })
        .unwrap();
    let packages = pyjson::loads(std::str::from_utf8(&manifest_bytes).unwrap()).unwrap();
    let primary =
        packages["packages"].as_array().unwrap().iter().find(|p| p["id"] == "sylvia.srd-lite.bg3").unwrap();
    let artifact = &primary["participant_artifacts"][0];
    let legacy_declaration = ParticipantArtifactDeclaration::new(
        &artifact["kind"],
        &artifact["schema_id"],
        &artifact["schema_sha256"],
        artifact["schema"].as_object().unwrap().clone(),
        artifact["entity_state_schema"].as_object().cloned(),
        artifact["initial_state_calculations"]
            .as_array()
            .map(|items| items.iter().map(|item| item.as_object().unwrap().clone()).collect())
            .unwrap_or_default(),
        artifact["initial_state_validation"].as_object().cloned(),
    )
    .unwrap();
    let identity = repository_json(vectors["legacy_identity"].as_str().unwrap());
    let setting = &identity["campaign_setting"];
    let legacy = (
        lock_entry(
            primary["id"].as_str().unwrap(),
            primary["version"].as_str().unwrap(),
            primary["content_sha256"].as_str().unwrap(),
        ),
        legacy_declaration,
        PackageBinding {
            package_id: setting["id"].as_str().unwrap().into(),
            version: setting["version"].as_str().unwrap().into(),
            content_sha256: setting["content_sha256"].as_str().unwrap().into(),
        },
    );
    for (name, context) in [("current", &current), ("legacy", &legacy)] {
        let spec = json!({
            "package": [context.0.package_id(), context.0.version(), context.0.sha256()],
            "setting": [context.2.package_id, context.2.version, context.2.content_sha256],
        });
        assert_eq!(spec, vectors["contexts"][name], "the {name} context");
    }
    (current, legacy)
}

fn digest(value: &JsonMap) -> Json {
    Json::from(sha256_hex(canonical_json_bytes(&Json::Object(value.clone()))))
}

fn shape(item: &ReusableParticipant) -> Json {
    json!({
        "artifact_id": item.artifact_id,
        "role": item.role,
        "name": item.name,
        "summary": item.summary,
        "authored_profile_sha256": item.authored_profile.as_ref().map(|text| sha256_hex(text.as_bytes())),
        "content_sha256": item.content_sha256,
        "participant_sha256": digest(&item.participant),
        "entity_state_sha256": digest(&item.entity_state),
        "setting_data_sha256": digest(&item.setting_data),
        "source_profile": item.source_profile.clone().map(Json::Object),
        "source_migration": item.source_migration.clone().map(Json::Object),
        "catalog": Json::Object(item.catalog_payload()),
        "rift_source_sha256": sha256_hex(canonical_json_bytes(&Json::Object(item.rift_source()))),
    })
}

struct Scenario {
    storage: PathBuf,
    current: Context,
    legacy: Context,
    starters: Json,
}

impl Scenario {
    /// The generator's `mask`.
    fn mask(&self, text: &str) -> String {
        let root = self.storage.display().to_string();
        let mut text = text.to_owned();
        for form in [root.replace('\\', "\\\\"), root.clone(), root.replace('\\', "/")] {
            text = text.replace(&form, "<ROOT>");
        }
        let mut parts: Vec<String> = text.split("<ROOT>").map(str::to_owned).collect();
        for part in parts.iter_mut().skip(1) {
            let (path, rest) = match part.find('\'') {
                Some(index) => part.split_at(index),
                None => (part.as_str(), ""),
            };
            *part = format!("{}{rest}", path.replace("\\\\", "/").replace('\\', "/"));
        }
        parts.join("<ROOT>")
    }

    fn error(&self, error: &DomainError) -> Json {
        let diagnostics: Vec<Json> =
            error.diagnostics().iter().map(|d| Json::Object(d.as_payload())).collect();
        let issues: Vec<Json> = error.issues().iter().map(|i| Json::Object(i.as_payload())).collect();
        json!([error.python_class(), self.mask(error.message()), error.code(), diagnostics, issues])
    }

    fn outcome<T>(&self, result: DomainResult<T>, shape: impl FnOnce(T) -> Json) -> Json {
        match result {
            Ok(value) => json!(["ok", shape(value)]),
            Err(error) => self.error(&error),
        }
    }

    fn context(&self, name: &str) -> Context {
        let (mut package, declaration, mut setting) =
            if name == "legacy" { self.legacy.clone() } else { self.current.clone() };
        if name == "other_setting" {
            setting.version = "9.9.9".into();
        }
        if name == "other_package" {
            package = lock_entry(package.package_id(), "0.0.1", package.sha256());
        }
        (package, declaration, setting)
    }

    fn entry(&self, step: &Json) -> PathBuf {
        self.storage.join("participants").join(step.get("artifact").and_then(Json::as_str).unwrap_or(""))
    }

    fn run(&self, step: &Json) -> Json {
        let text = |key: &str| step[key].as_str().unwrap();
        match text("op") {
            op @ ("draft" | "save") => {
                let row = &self.starters[step["starter"].as_u64().unwrap() as usize];
                let mut participant = row["participant"].clone();
                let mut authored = row["entity_state"].clone();
                for edit in step.get("edits").and_then(Json::as_array).into_iter().flatten() {
                    let target = if edit[1][0] == "participant" { &mut participant } else { &mut authored };
                    let key = edit[1][1].as_str().unwrap();
                    if edit[0] == "delete" {
                        target.as_object_mut().unwrap().shift_remove(key);
                    } else {
                        target.as_object_mut().unwrap().insert(key.into(), edit[2].clone());
                    }
                }
                let (package, declaration, setting) = self.context(text("context"));
                let summary = step.get("summary").unwrap_or(&row["summary"]).as_str().unwrap().to_owned();
                let profile = step.get("authored_profile").unwrap_or(&row["authored_profile"]).clone();
                let role = text("role");
                if op == "draft" {
                    let result = validate_reusable_participant_draft(
                        role,
                        &summary,
                        &profile,
                        &participant,
                        &authored,
                        &declaration,
                        None,
                        None,
                    );
                    return self.outcome(result, |(profile, participant, state, setting)| {
                        json!([
                            sha256_hex(profile.as_bytes()),
                            digest(&participant),
                            digest(&state),
                            digest(&setting)
                        ])
                    });
                }
                let source_profile = step.get("source_profile").filter(|value| !value.is_null());
                let result = save_reusable_participant(
                    &self.storage,
                    text("operation_id"),
                    role,
                    &summary,
                    &profile,
                    &participant,
                    &authored,
                    &package,
                    &declaration,
                    &setting,
                    None,
                    None,
                    source_profile,
                );
                self.outcome(result, |item| {
                    let mut value = shape(&item);
                    let entry = self.storage.join("participants").join(&item.artifact_id);
                    let mut files: Vec<(String, String)> = std::fs::read_dir(&entry)
                        .unwrap()
                        .map(|e| {
                            let e = e.unwrap();
                            (
                                e.file_name().to_string_lossy().into_owned(),
                                sha256_hex(std::fs::read(e.path()).unwrap()),
                            )
                        })
                        .collect();
                    files.sort();
                    value["files"] =
                        Json::Object(files.into_iter().map(|(n, d)| (n, Json::from(d))).collect());
                    let mut storage: Vec<String> = std::fs::read_dir(&self.storage)
                        .unwrap()
                        .map(|e| e.unwrap().file_name().to_string_lossy().into_owned())
                        .collect();
                    storage.sort();
                    value["storage"] = json!(storage);
                    value
                })
            }
            "load" => {
                let (package, declaration, setting) = self.context(text("context"));
                let legacy = step.get("legacy").filter(|value| !value.is_null()).map(binding);
                let result = load_reusable_participant(
                    &self.storage,
                    text("artifact"),
                    &package,
                    &declaration,
                    &setting,
                    legacy.as_ref(),
                    None,
                );
                self.outcome(result, |item| shape(&item))
            }
            "list" => {
                let (package, declaration, setting) = self.context(text("context"));
                let mut invalid = Vec::new();
                let mut handler = |name: &str, error: &DomainError| {
                    invalid.push(json!([name, error.python_class(), self.mask(error.message())]));
                };
                let on_invalid: Option<InvalidHandler<'_>> =
                    if step["handler"].as_bool().unwrap() { Some(&mut handler) } else { None };
                let result = compatible_reusable_participants(
                    &self.storage,
                    &package,
                    &declaration,
                    &setting,
                    None,
                    None,
                    on_invalid,
                );
                match result {
                    Ok(items) => json!(["ok", items.iter().map(shape).collect::<Vec<_>>(), invalid]),
                    Err(error) => self.error(&error),
                }
            }
            "delete" => {
                let result = delete_reusable_participant(
                    &self.storage,
                    text("artifact"),
                    text("hash"),
                    text("confirmation"),
                );
                self.outcome(result, Json::from)
            }
            _ => {
                self.tamper(step);
                json!(["ok", null])
            }
        }
    }

    fn tamper(&self, step: &Json) {
        let text = |key: &str| step[key].as_str().unwrap();
        let entry = self.entry(step);
        let backup = |label: &str| self.storage.parent().unwrap().join(format!("backup-{label}"));
        match text("op") {
            "backup" => copy_dir(&entry, &backup(text("label"))),
            "restore" => {
                std::fs::remove_dir_all(&entry).unwrap();
                copy_dir(&backup(text("label")), &entry);
            }
            "edit_json" => {
                let path = entry.join(text("name"));
                let mut value = pyjson::loads(&std::fs::read_to_string(&path).unwrap()).unwrap();
                for edit in step["edits"].as_array().unwrap() {
                    let keys = edit[1].as_array().unwrap();
                    let (last, parents) = keys.split_last().unwrap();
                    let parent =
                        parents.iter().fold(&mut value, |node, key| &mut node[key.as_str().unwrap()]);
                    let last = last.as_str().unwrap();
                    if edit[0] == "delete" {
                        parent.as_object_mut().unwrap().shift_remove(last);
                    } else {
                        parent.as_object_mut().unwrap().insert(last.into(), edit[2].clone());
                    }
                }
                std::fs::write(path, canonical_json_bytes(&value)).unwrap();
            }
            "write" => std::fs::write(entry.join(text("name")), unhex(text("hex"))).unwrap(),
            "remove" => std::fs::remove_file(entry.join(text("name"))).unwrap(),
            "mkdir_entry" => {
                std::fs::create_dir(self.storage.join("participants").join(text("name"))).unwrap()
            }
            other => panic!("{other}"),
        }
    }
}

fn copy_dir(from: &Path, to: &Path) {
    std::fs::create_dir_all(to).unwrap();
    for entry in std::fs::read_dir(from).unwrap() {
        let entry = entry.unwrap();
        std::fs::copy(entry.path(), to.join(entry.file_name())).unwrap();
    }
}

fn unhex(text: &str) -> Vec<u8> {
    (0..text.len()).step_by(2).map(|index| u8::from_str_radix(&text[index..index + 2], 16).unwrap()).collect()
}

#[test]
fn reusable_participants_match_python() {
    let vectors = vectors();
    let (current, legacy) = contexts(&vectors);
    let work = Path::new(env!("CARGO_TARGET_TMPDIR")).join("reusable-participant-vectors");
    let _ = std::fs::remove_dir_all(&work);
    let storage = work.join("storage");
    std::fs::create_dir_all(&storage).unwrap();
    let scenario = Scenario {
        storage,
        current,
        legacy,
        starters: repository_json(vectors["starters"].as_str().unwrap()),
    };
    let mut failures = Vec::new();
    for case in vectors["parses"].as_array().unwrap() {
        let (package, declaration, setting) = scenario.context(case["context"].as_str().unwrap());
        let result = parse_setting_character(
            case["source"].as_str().unwrap(),
            case["character_id"].as_str().unwrap(),
            case["role"].as_str().unwrap(),
            &package,
            &declaration,
            &setting,
            None,
        );
        let actual = scenario.outcome(result, |item| shape(&item));
        if case.get("row").is_some() {
            // Differences row 2: Python reads NaN; the port refuses the file as invalid JSON.
            if actual[1] != "Setting Character File is invalid JSON" {
                failures.push(format!("parse {} (row 2): rust {actual}", case["name"]));
            }
            continue;
        }
        if actual != case["outcome"] {
            failures.push(format!(
                "parse {} {} {}: python {}, rust {actual}",
                case["name"], case["context"], case["role"], case["outcome"]
            ));
        }
    }
    for (index, step) in vectors["steps"].as_array().unwrap().iter().enumerate() {
        let actual = scenario.run(step);
        if actual != step["outcome"] {
            let mut label = step.clone();
            label.as_object_mut().unwrap().shift_remove("outcome");
            failures.push(format!("step {index} {label}: python {}, rust {actual}", step["outcome"]));
        }
    }
    let _ = std::fs::remove_dir_all(&work);
    let report = Path::new(env!("CARGO_TARGET_TMPDIR")).join("reusable-participant-differences.txt");
    let _ = std::fs::write(&report, failures.join("\n"));
    let shown: Vec<String> = failures.iter().take(6).map(|f| f.chars().take(1200).collect()).collect();
    assert!(failures.is_empty(), "{} differ: {shown:#?}", failures.len());
}
