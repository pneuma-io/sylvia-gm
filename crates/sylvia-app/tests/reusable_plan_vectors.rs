//! Replay the frozen Python's reusable-plan scenario against sylvia_app::reusable_plans.
//!
//! Regenerate with `tests/vectors/generate_reusable_plan_vectors.py` (needs the snapshot's Python).

use serde_json::json;
use std::path::{Path, PathBuf};
use sylvia_app::bundled_packages::{BundledPackages, BundledSources};
use sylvia_app::plan_snapshot::validate_plan_snapshot;
use sylvia_app::reusable_plans::{
    delete_reusable_campaign_plan, load_reusable_campaign_plan, reusable_campaign_plan_catalog,
    save_reusable_campaign_plan, save_reusable_rift_plan, validate_rift_plan, InvalidHandler, PlanValidator,
    ReusableCampaignPlan,
};
use sylvia_base::hash::sha256_hex;
use sylvia_base::{pyjson, Json, JsonMap};
use sylvia_domain::campaign_plan::{validate_campaign_plan, CampaignPlanExpectations, ValidatedCampaignPlan};
use sylvia_domain::package_library::canonical_json_bytes;
use sylvia_domain::{DomainError, DomainResult};

fn vectors() -> Json {
    pyjson::loads(include_str!("vectors/reusable_plan_vectors.json")).expect("vector file is valid JSON")
}

fn repository_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
}

#[cfg(windows)]
fn make_link(path: &Path, target: &Path) {
    let status = std::process::Command::new("cmd")
        .args(["/C", "mklink", "/J"])
        .arg(path)
        .arg(target)
        .stdout(std::process::Stdio::null())
        .status()
        .unwrap();
    assert!(status.success(), "mklink /J failed");
}

#[cfg(not(windows))]
fn make_link(path: &Path, target: &Path) {
    std::os::unix::fs::symlink(target, path).unwrap();
}

fn remove_link(path: &Path) {
    if cfg!(windows) {
        std::fs::remove_dir(path).unwrap();
    } else {
        std::fs::remove_file(path).unwrap();
    }
}

fn copy_dir(from: &Path, to: &Path) {
    std::fs::create_dir_all(to).unwrap();
    for entry in std::fs::read_dir(from).unwrap() {
        let entry = entry.unwrap();
        let target = to.join(entry.file_name());
        if entry.file_type().unwrap().is_dir() {
            copy_dir(&entry.path(), &target);
        } else {
            std::fs::copy(entry.path(), target).unwrap();
        }
    }
}

fn unhex(text: &str) -> Vec<u8> {
    (0..text.len()).step_by(2).map(|index| u8::from_str_radix(&text[index..index + 2], 16).unwrap()).collect()
}

struct Scenario {
    storage: PathBuf,
    bundled: BundledPackages,
    retained: Vec<u8>,
    rift: ValidatedCampaignPlan,
    contribution: JsonMap,
    linked_id: String,
}

impl Scenario {
    /// The generator's `mask`: the storage root as <ROOT>, separators inside it as "/".
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

    fn entry(&self, artifact_id: &str) -> PathBuf {
        self.storage.join("campaign-plans").join(artifact_id)
    }

    fn markdown(&self, variant: &str) -> String {
        let text = &self.rift.readable_markdown;
        match variant {
            "rift" => text.clone(),
            "sections" => format!(
                "{text}\n\n## Selected campaign party\nAlice\n\n## Player customization brief  \n\nBe kind.\n## Accepted Campaign Construction\n"
            ),
            "crlf" => text.replace('\n', "\r\n"),
            "untitled" => text.split_once('\n').unwrap().1.to_owned(),
            "indented" => format!("  # Indented\n{text}"),
            other => panic!("{other}"),
        }
    }

    fn error(&self, error: &DomainError) -> Json {
        json!([error.python_class(), self.mask(error.message())])
    }

    fn shape(&self, plan: &ReusableCampaignPlan, files: bool) -> Json {
        let mut shape = json!({
            "artifact_id": plan.artifact_id,
            "title": plan.title,
            "summary": plan.summary,
            "content_sha256": plan.content_sha256,
            "plan_content_sha256": plan.plan.content_sha256,
            "plan_artifact_id": plan.plan.artifact_id,
            "markdown_sha256": sha256_hex(plan.plan.readable_markdown.as_bytes()),
            "interview_contribution": plan.interview_contribution.clone().map(Json::Object),
            "setup_id": plan.setup_id,
            "snapshot_sha256": plan.package_snapshot.as_deref().map(sha256_hex),
            "catalog": Json::Object(plan.catalog_payload()),
        });
        if files {
            let mut entries: Vec<(String, String)> = std::fs::read_dir(self.entry(&plan.artifact_id))
                .unwrap()
                .map(|entry| {
                    let entry = entry.unwrap();
                    (
                        entry.file_name().to_string_lossy().into_owned(),
                        sha256_hex(std::fs::read(entry.path()).unwrap()),
                    )
                })
                .collect();
            entries.sort();
            let files: JsonMap =
                entries.into_iter().map(|(name, digest)| (name, Json::from(digest))).collect();
            shape["files"] = Json::Object(files);
            let mut storage: Vec<String> = std::fs::read_dir(&self.storage)
                .unwrap()
                .map(|entry| entry.unwrap().file_name().to_string_lossy().into_owned())
                .collect();
            storage.sort();
            shape["storage"] = json!(storage);
        }
        shape
    }

    fn outcome(&self, result: DomainResult<ReusableCampaignPlan>, files: bool) -> Json {
        match result {
            Ok(plan) => json!(["ok", self.shape(&plan, files)]),
            Err(error) => self.error(&error),
        }
    }

    fn run(&self, step: &Json) -> Json {
        let text = |key: &str| step[key].as_str().unwrap();
        let authority = self.bundled.bundled_rift_authority().unwrap();
        let rift = |value: &Json, markdown: &str| validate_rift_plan(&self.bundled, value, markdown);
        let snapshot = |value: &Json, markdown: &str| validate_plan_snapshot(&self.retained, value, markdown);
        let current = |value: &Json, markdown: &str| {
            validate_campaign_plan(
                value,
                markdown,
                CampaignPlanExpectations {
                    expected_ruleset: Some(&authority.ruleset),
                    expected_setting: Some(&authority.setting),
                    expected_closure_sha256: &authority.closure_sha256,
                    setting_paths: &authority.setting_paths,
                    setting_knowledge_ids: &authority.setting_knowledge_ids,
                },
            )
        };
        let validator = |name: &str| -> Option<PlanValidator<'_>> {
            match name {
                "default" => None,
                "rift" => Some(&rift),
                "snapshot" => Some(&snapshot),
                "current" => Some(&current),
                other => panic!("{other}"),
            }
        };
        let package_snapshot = || step["snapshot"].as_bool().unwrap().then_some(self.retained.as_slice());
        match text("op") {
            "save_rift" => self.outcome(
                save_reusable_rift_plan(
                    &self.storage,
                    &self.bundled,
                    text("operation_id"),
                    text("title"),
                    &self.rift.payload,
                    &self.markdown(text("markdown")),
                    package_snapshot(),
                ),
                true,
            ),
            "save_plan" => {
                let invalid: JsonMap = [("format_id".to_owned(), Json::from("x"))].into_iter().collect();
                let contribution = match text("contribution") {
                    "none" => None,
                    "invalid" => Some(&invalid),
                    "valid" => Some(&self.contribution),
                    other => panic!("{other}"),
                };
                self.outcome(
                    save_reusable_campaign_plan(
                        &self.storage,
                        &self.bundled,
                        text("operation_id"),
                        &Json::Object(self.rift.payload.clone()),
                        &self.markdown(text("markdown")),
                        validator(text("validator")).unwrap(),
                        package_snapshot(),
                        step["setup_id"].as_str(),
                        contribution,
                    ),
                    true,
                )
            }
            "load" => self.outcome(
                load_reusable_campaign_plan(
                    &self.storage,
                    text("artifact"),
                    validator(text("validator")),
                    &self.bundled,
                ),
                false,
            ),
            "catalog" => {
                let mut invalid = Vec::new();
                let mut handler = |name: &str, error: &DomainError| {
                    invalid.push(json!([name, error.python_class(), self.mask(error.message())]));
                };
                let on_invalid: Option<InvalidHandler<'_>> =
                    if step["handler"].as_bool().unwrap() { Some(&mut handler) } else { None };
                let result = reusable_campaign_plan_catalog(
                    &self.storage,
                    validator(text("validator")),
                    on_invalid,
                    &self.bundled,
                );
                match result {
                    Ok(plans) => {
                        let shapes: Vec<Json> = plans.iter().map(|plan| self.shape(plan, false)).collect();
                        json!(["ok", shapes, invalid])
                    }
                    Err(error) => self.error(&error),
                }
            }
            "delete" => {
                match delete_reusable_campaign_plan(
                    &self.storage,
                    text("artifact"),
                    text("hash"),
                    text("confirmation"),
                ) {
                    Ok(deleted) => json!(["ok", deleted]),
                    Err(error) => self.error(&error),
                }
            }
            _ => {
                self.tamper(step);
                json!(["ok", null])
            }
        }
    }

    fn tamper(&self, step: &Json) {
        let text = |key: &str| step[key].as_str().unwrap();
        let entry = self.entry(step.get("artifact").and_then(Json::as_str).unwrap_or(""));
        let backup = |label: &str| self.storage.parent().unwrap().join(format!("backup-{label}"));
        match text("op") {
            "backup" => copy_dir(&entry, &backup(text("label"))),
            "restore" => {
                std::fs::remove_dir_all(&entry).unwrap();
                copy_dir(&backup(text("label")), &entry);
            }
            "edit_record" => {
                let path = entry.join("record.json");
                let mut record = pyjson::loads(&std::fs::read_to_string(&path).unwrap()).unwrap();
                let record_map = record.as_object_mut().unwrap();
                for edit in step["edits"].as_array().unwrap() {
                    let key = edit[1].as_str().unwrap();
                    if edit[0] == "delete" {
                        record_map.shift_remove(key);
                    } else {
                        record_map.insert(key.to_owned(), edit[2].clone());
                    }
                }
                std::fs::write(path, canonical_json_bytes(&record)).unwrap();
            }
            "write" => std::fs::write(entry.join(text("name")), unhex(text("hex"))).unwrap(),
            "transform" => {
                let path = entry.join(text("name"));
                let data = std::fs::read(&path).unwrap();
                let utf16le = || -> Vec<u8> {
                    std::str::from_utf8(&data).unwrap().encode_utf16().flat_map(u16::to_le_bytes).collect()
                };
                let changed: Vec<u8> = match text("transform") {
                    "bom" => [&[0xEF, 0xBB, 0xBF][..], &data].concat(),
                    "double_bom" => [&[0xEF, 0xBB, 0xBF, 0xEF, 0xBB, 0xBF][..], &data].concat(),
                    "utf-16" => [vec![0xFF, 0xFE], utf16le()].concat(),
                    "utf-16-le" => utf16le(),
                    "utf-32-be" => std::str::from_utf8(&data)
                        .unwrap()
                        .chars()
                        .flat_map(|c| u32::from(c).to_be_bytes())
                        .collect(),
                    "truncate" => data[..data.len() - 1].to_vec(),
                    "garbage" => [&data[..], &[0xFF]].concat(),
                    "crlf" => {
                        let mut out: Vec<u8> = Vec::new();
                        for byte in &data {
                            if *byte == b'\n' {
                                out.push(b'\r');
                            }
                            out.push(*byte);
                        }
                        out.extend_from_slice(b"\r\n");
                        out
                    }
                    other => panic!("{other}"),
                };
                std::fs::write(path, changed).unwrap();
            }
            "remove" => std::fs::remove_file(entry.join(text("name"))).unwrap(),
            "mkdir" => std::fs::create_dir(entry.join(text("name"))).unwrap(),
            "big_snapshot" => {
                std::fs::write(entry.join("setting.sylvia-gm-package"), vec![0; 64 * 1024 * 1024 + 1])
                    .unwrap()
            }
            "link" => make_link(&self.entry(&self.linked_id), &entry),
            "unlink" => remove_link(&self.entry(&self.linked_id)),
            "many_dirs" => {
                for index in 0..step["count"].as_u64().unwrap() {
                    std::fs::create_dir(
                        self.storage.join("campaign-plans").join(format!("filler-{index:03}")),
                    )
                    .unwrap();
                }
            }
            "clear_dirs" => {
                for entry in std::fs::read_dir(self.storage.join("campaign-plans")).unwrap() {
                    let entry = entry.unwrap();
                    if entry.file_name().to_string_lossy().starts_with("filler-") {
                        std::fs::remove_dir(entry.path()).unwrap();
                    }
                }
            }
            other => panic!("{other}"),
        }
    }
}

#[test]
fn reusable_plans_match_python() {
    let vectors = vectors();
    let root = repository_root();
    let work = Path::new(env!("CARGO_TARGET_TMPDIR")).join("reusable-plan-vectors");
    let _ = std::fs::remove_dir_all(&work);
    let storage = work.join("storage");
    std::fs::create_dir_all(&storage).unwrap();
    let bundled = BundledPackages::new(BundledSources::from_repository_root(&root));
    let rift = bundled.bundled_rift_campaign_plan().unwrap();
    let contribution = pyjson::loads(
        &std::fs::read_to_string(root.join(vectors["contribution"].as_str().unwrap()))
            .unwrap()
            .replace("\r\n", "\n"),
    )
    .unwrap();
    let scenario = Scenario {
        storage,
        retained: std::fs::read(root.join(vectors["retained"].as_str().unwrap())).unwrap(),
        bundled,
        rift,
        contribution: contribution.as_object().unwrap().clone(),
        linked_id: vectors["linked_id"].as_str().unwrap().to_owned(),
    };
    let steps = vectors["steps"].as_array().unwrap();
    assert!(steps.len() > 400);
    let mut failures = Vec::new();
    for (index, step) in steps.iter().enumerate() {
        let actual = scenario.run(step);
        if actual != step["outcome"] {
            let mut label = step.clone();
            label.as_object_mut().unwrap().shift_remove("outcome");
            failures.push(format!("step {index} {label}: python {}, rust {actual}", step["outcome"]));
        }
    }
    let _ = std::fs::remove_dir_all(&work);
    // The whole text of each difference, for comparing long outcomes.
    let report = Path::new(env!("CARGO_TARGET_TMPDIR")).join("reusable-plan-differences.txt");
    let _ = std::fs::write(&report, failures.join("\n"));
    let shown: Vec<String> = failures.iter().take(6).map(|text| text.chars().take(1200).collect()).collect();
    assert!(failures.is_empty(), "{} of {} differ: {shown:#?}", failures.len(), steps.len());
}
