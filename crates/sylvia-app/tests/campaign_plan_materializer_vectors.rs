//! Compare sylvia_app::campaign_plan_materializer with the frozen Python's
//! `materialize_campaign_plan_draft` and `publish_campaign_plan_draft`.
//!
//! Regenerate with `tests/vectors/generate_campaign_plan_materializer_vectors.py` (needs the
//! snapshot's Python). The shipped Plans and the SRD record are read from the repository. Links
//! are junctions on Windows and symbolic links elsewhere.

use serde_json::json;
use std::collections::BTreeSet;
use std::path::{Path, PathBuf};
use sylvia_app::campaign_plan_materializer::{
    materialize_campaign_plan_draft, publish_campaign_plan_draft, PlanDraftExpectations,
};
use sylvia_base::hash::sha256_hex;
use sylvia_base::{pyjson, Json, JsonMap};
use sylvia_domain::campaign_plan::PackageBinding;
use sylvia_domain::error::DomainError;

const VECTORS: &str = include_str!("vectors/campaign_plan_materializer_vectors.json");

fn repository() -> PathBuf {
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

/// The generator's `edited_text(text, edit)`.
fn edited_text(text: &str, edit: &Json) -> Vec<u8> {
    let Some(edit) = edit.as_array() else { return text.as_bytes().to_vec() };
    let part = |index: usize| edit[index].as_str().unwrap();
    match part(0) {
        "replace" => text.replacen(part(1), part(2), 1).into_bytes(),
        "append" => format!("{text}{}", part(1)).into_bytes(),
        "latin1" => part(1).chars().map(|c| u8::try_from(u32::from(c)).unwrap()).collect(),
        _ => part(1).repeat(edit[2].as_u64().unwrap() as usize).into_bytes(),
    }
}

fn binding(value: &Json) -> PackageBinding {
    PackageBinding {
        package_id: value["id"].as_str().unwrap().into(),
        version: value["version"].as_str().unwrap().into(),
        content_sha256: value["content_sha256"].as_str().unwrap().into(),
    }
}

fn set(value: &Json) -> BTreeSet<String> {
    value.as_array().unwrap().iter().map(|item| item.as_str().unwrap().to_owned()).collect()
}

fn failure(error: &DomainError) -> Json {
    json!([error.python_class(), error.message()])
}

fn run(base: &Path, case: &Json, srd_schemas: &[JsonMap]) -> Json {
    let workspace = base.join("workspace");
    let draft = workspace.join("campaign-plan-draft");
    std::fs::create_dir_all(&draft).unwrap();
    let folder = repository().join(case["plan"].as_str().unwrap());
    let plan_text = std::fs::read_to_string(folder.join("plan.json")).unwrap();
    let markdown = std::fs::read_to_string(folder.join("plan.md")).unwrap();
    let layout = case.get("layout").cloned().unwrap_or(json!({}));
    for (name, text, edit) in [
        ("plan.json", &plan_text, case.get("plan_edit").unwrap_or(&Json::Null)),
        ("plan.md", &markdown, case.get("md_edit").unwrap_or(&Json::Null)),
    ] {
        match layout.get(name).and_then(Json::as_str) {
            Some("missing") => {}
            Some("directory") => std::fs::create_dir(draft.join(name)).unwrap(),
            _ => std::fs::write(draft.join(name), edited_text(text, edit)).unwrap(),
        }
    }
    for name in case.get("linked_outputs").and_then(Json::as_array).into_iter().flatten() {
        std::fs::create_dir_all(base.join("elsewhere")).unwrap();
        make_link(&workspace.join(name.as_str().unwrap()), &base.join("elsewhere"));
    }
    let expect = &case["expect"];
    let (ruleset, setting) = (binding(&expect["ruleset"]), binding(&expect["setting"]));
    let (paths, knowledge) = (set(&expect["paths"]), set(&expect["knowledge"]));
    let closure = case.get("closure").or_else(|| expect.get("closure")).and_then(Json::as_str).unwrap();
    let expected = PlanDraftExpectations {
        expected_ruleset: &ruleset,
        expected_setting: &setting,
        expected_closure_sha256: closure,
        setting_paths: &paths,
        setting_knowledge_ids: &knowledge,
    };
    let materialize = match materialize_campaign_plan_draft(&workspace, expected) {
        Ok(draft) => {
            let plan = &draft.plan;
            let mut referenced = plan.referenced_setting_paths.clone();
            referenced.sort();
            json!([
                "ok",
                plan.content_sha256,
                plan.artifact_id,
                plan.title,
                draft.plan_path,
                draft.readable_path,
                referenced
            ])
        }
        Err(error) => failure(&error),
    };
    let schemas: &[JsonMap] =
        if case.get("state_schemas").and_then(Json::as_str) == Some("srd") { srd_schemas } else { &[] };
    let publish = match publish_campaign_plan_draft(&workspace, expected, schemas) {
        Ok(published) => {
            let read = |name: &str| sha256_hex(std::fs::read(workspace.join(name)).unwrap());
            json!([
                "ok",
                published.plan_path,
                published.readable_path,
                read("campaign-plan.json"),
                read("campaign-plan.md")
            ])
        }
        Err(error) => failure(&error),
    };
    json!({"materialize": materialize, "publish": publish})
}

#[test]
fn campaign_plan_drafts_match_python() {
    let vectors = pyjson::loads(VECTORS).unwrap();
    let record =
        pyjson::loads(&std::fs::read_to_string(repository().join(vectors["srd"].as_str().unwrap())).unwrap())
            .unwrap();
    let srd_schemas: Vec<JsonMap> = record["state_schemas"]
        .as_array()
        .unwrap()
        .iter()
        .map(|item| item.as_object().unwrap().clone())
        .collect();
    let mut failures = Vec::new();
    let cases = vectors["cases"].as_array().unwrap();
    for (index, case) in cases.iter().enumerate() {
        let base =
            std::env::temp_dir().join(format!("sylvia-plan-draft-rust-{}-{index}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        std::fs::create_dir_all(&base).unwrap();
        let actual = run(&base, case, &srd_schemas);
        let _ = std::fs::remove_dir_all(&base);
        let expected = if case.get("row").is_some() {
            // Differences row 2: the overflowing number is refused when the JSON is read.
            let refused = json!(["ValueError", "Campaign Plan JSON is invalid"]);
            json!({"materialize": refused, "publish": refused})
        } else {
            case["outcome"].clone()
        };
        if actual != expected {
            failures.push(format!(
                "{} {:?}: python {expected}, rust {actual}",
                case["plan"],
                case.get("plan_edit")
            ));
        }
    }
    assert!(
        failures.is_empty(),
        "{} of {} differ: {:#?}",
        failures.len(),
        cases.len(),
        &failures[..failures.len().min(6)]
    );
}
