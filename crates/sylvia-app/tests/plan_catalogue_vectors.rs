//! Compare sylvia_app::bundled_campaign_plans and campaign_plan_sources with the frozen Python:
//! the repository's bundled collection, synthetic collections, and Plan sources over lane 3's
//! package-read base with the real `AuthorityStore`.
//!
//! Regenerate with `tests/vectors/generate_plan_catalogue_vectors.py` (needs the snapshot's Python).

use flate2::read::GzDecoder;
use serde_json::json;
use std::io::Read;
use std::path::{Path, PathBuf};
use sylvia_app::bundled_campaign_plans::{bundled_plan_files, read_plan_catalogue};
use sylvia_app::bundled_packages::{BundledPackages, BundledSources};
use sylvia_app::campaign_plan_sources::{
    campaign_plan_sources, validate_retained_campaign_plan, CampaignPlanToolAuthority,
};
use sylvia_app::reusable_plans::ReusableCampaignPlan;
use sylvia_base::hash::sha256_hex;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{Json, JsonMap};
use sylvia_domain::campaign_plan::ValidatedCampaignPlan;
use sylvia_domain::package_library::canonical_json_bytes;
use sylvia_domain::DomainResult;
use sylvia_store::{AuthorityStore, StoreError};

fn vectors() -> Json {
    pyjson::loads(include_str!("vectors/plan_catalogue_vectors.json")).expect("vector file is valid JSON")
}

fn repository() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
}

/// The generator's `masker(*roots)`.
fn mask(text: &str, roots: &[&Path]) -> String {
    let mut text = text.to_owned();
    for root in roots {
        let root = root.display().to_string();
        for form in [root.replace('\\', "\\\\"), root.clone(), root.replace('\\', "/")] {
            text = text.replace(&form, "<ROOT>");
        }
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

fn domain_outcome<T>(result: DomainResult<T>, shape: impl FnOnce(T) -> Json, roots: &[&Path]) -> Json {
    match result {
        Ok(value) => json!(["ok", shape(value)]),
        Err(error) => {
            let diagnostics: Vec<Json> =
                error.diagnostics().iter().map(|d| Json::Object(d.as_payload())).collect();
            json!([error.python_class(), mask(error.message(), roots), error.code(), diagnostics])
        }
    }
}

/// An `AuthorityError` carries `code`; Python's `ValueError`s do not.
fn store_outcome<T>(result: Result<T, StoreError>, shape: impl FnOnce(T) -> Json) -> Json {
    match result {
        Ok(value) => json!(["ok", shape(value)]),
        Err(error) => match error.rejection() {
            Some(coded) => {
                let class = coded.error_type.unwrap_or("CommandRejected");
                let code = if matches!(class, "CommandRejected" | "MechanicsRejected") {
                    json!(coded.code)
                } else {
                    Json::Null
                };
                json!([class, coded.message, code, coded.diagnostics])
            }
            None => json!(["uncoded", error.to_string(), null, []]),
        },
    }
}

fn digest(data: &[u8]) -> Json {
    Json::from(sha256_hex(data))
}

fn validated_shape(plan: &ValidatedCampaignPlan) -> Json {
    json!({
        "content_sha256": plan.content_sha256,
        "artifact_id": plan.artifact_id,
        "title": plan.title,
        "markdown_sha256": sha256_hex(plan.readable_markdown.as_bytes()),
    })
}

fn plan_shape(plan: Option<ReusableCampaignPlan>) -> Json {
    let Some(plan) = plan else { return Json::Null };
    json!({
        "artifact_id": plan.artifact_id,
        "title": plan.title,
        "summary": plan.summary,
        "content_sha256": plan.content_sha256,
        "plan": validated_shape(&plan.plan),
        "interview_contribution": plan.interview_contribution.clone().map(Json::Object),
        "setup_id": plan.setup_id,
        "snapshot_sha256": plan.package_snapshot.as_deref().map(sha256_hex),
        "catalog": Json::Object(plan.catalog_payload()),
    })
}

fn relative(paths: Vec<PathBuf>, root: &Path) -> Json {
    json!(paths
        .iter()
        .map(|path| path
            .strip_prefix(root)
            .unwrap()
            .components()
            .map(|c| c.as_os_str().to_string_lossy())
            .collect::<Vec<_>>()
            .join("/"))
        .collect::<Vec<_>>())
}

fn objects(rows: Vec<JsonMap>) -> Json {
    Json::Array(rows.into_iter().map(Json::Object).collect())
}

fn copy_dir(from: &Path, to: &Path) {
    std::fs::create_dir_all(to).unwrap();
    for entry in std::fs::read_dir(from).unwrap() {
        let entry = entry.unwrap();
        if entry.file_type().unwrap().is_dir() {
            copy_dir(&entry.path(), &to.join(entry.file_name()));
        } else {
            std::fs::copy(entry.path(), to.join(entry.file_name())).unwrap();
        }
    }
}

fn check(failures: &mut Vec<String>, label: String, actual: Json, expected: &Json) {
    if actual != *expected {
        failures.push(format!("{label}: python {expected}, rust {actual}"));
    }
}

fn collection(vectors: &Json, bundled: &BundledPackages, storage: &Path, failures: &mut Vec<String>) {
    let root = repository().join("campaign-plans");
    let roots = [repository(), storage.to_path_buf()];
    let roots: Vec<&Path> = roots.iter().map(PathBuf::as_path).collect();
    let cases = &vectors["collection"];
    check(
        failures,
        "catalogue".into(),
        domain_outcome(read_plan_catalogue(&root), objects, &roots),
        &cases["catalogue"],
    );
    check(
        failures,
        "files".into(),
        domain_outcome(bundled_plan_files(&root), |p| relative(p, &root), &roots),
        &cases["files"],
    );
    for case in cases["plans"].as_array().unwrap() {
        let id = case[0].as_str().unwrap();
        check(
            failures,
            format!("plan {id}"),
            domain_outcome(bundled.bundled_plan(id), plan_shape, &roots),
            &case[1],
        );
    }
    check(
        failures,
        "catalog".into(),
        domain_outcome(bundled.bundled_plan_catalog(), objects, &roots),
        &cases["catalog"],
    );
    for case in cases["settings"].as_array().unwrap() {
        let (ruleset, setting) = (case[0].as_str().unwrap(), case[1].as_str().unwrap());
        let result = bundled.bundled_setting_snapshot(ruleset, setting);
        check(
            failures,
            format!("setting {ruleset} {setting}"),
            domain_outcome(result, |s| s.as_deref().map_or(Json::Null, digest), &roots),
            &case[2],
        );
    }
    for case in cases["summaries"].as_array().unwrap() {
        let (id, change) = (case[0].as_str().unwrap(), case[1].as_str().unwrap());
        let known = bundled.bundled_plan(id).unwrap().unwrap().plan;
        let mut edited = known.clone();
        match change {
            "exact" => {}
            "bindings" => {
                edited.payload["bindings"]["package_closure_sha256"] = json!("0".repeat(64));
            }
            "title" => {
                let title = format!("{} II", edited.payload["title"].as_str().unwrap());
                edited.payload.insert("title".into(), json!(title));
            }
            "markdown" => edited.readable_markdown.push('\n'),
            _ => {
                edited.payload.insert("id".into(), json!("no-such-plan"));
            }
        }
        let result = bundled.known_builtin_public_summary(&edited);
        check(
            failures,
            format!("summary {id} {change}"),
            domain_outcome(result, Json::from, &roots),
            &case[2],
        );
    }
    for case in cases["selected"].as_array().unwrap() {
        let id = case[0].as_str().unwrap();
        let result = bundled.selected_campaign_plan(storage, id, None).map(Some);
        check(failures, format!("selected {id}"), domain_outcome(result, plan_shape, &roots), &case[1]);
    }
}

fn synthetic(vectors: &Json, scratch: &Path, failures: &mut Vec<String>) {
    let real = repository().join("campaign-plans");
    let source_id = vectors["synthetic_source"].as_str().unwrap();
    for case in vectors["synthetic"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let root = scratch.join(name);
        std::fs::create_dir_all(&root).unwrap();
        std::fs::write(root.join("catalogue.json"), pyjson::dumps(&case["catalogue"], Dump::DEFAULT))
            .unwrap();
        copy_dir(&real.join(source_id), &root.join(source_id));
        if let Some(fault) = name.strip_prefix("path_") {
            let path = match fault {
                "escape" => "../x.json",
                "absolute" => "/x.json",
                _ => "a\\b.json",
            };
            let plan_path = root.join(source_id).join("plan.json");
            let mut plan =
                pyjson::loads(&std::fs::read_to_string(&plan_path).unwrap().replace("\r\n", "\n")).unwrap();
            plan["starting_party"]["player_character"]["character"]["path"] = json!(path);
            std::fs::write(&plan_path, pyjson::dumps(&plan, Dump::DEFAULT)).unwrap();
        }
        if name == "missing_markdown" {
            std::fs::remove_file(root.join(source_id).join("plan.md")).unwrap();
        }
        let roots = [root.as_path(), scratch];
        let read = domain_outcome(read_plan_catalogue(&root), |rows| json!(rows.len()), &roots);
        check(failures, format!("synthetic {name} read"), read, &case["read"]);
        let files = if name == "missing_markdown" {
            domain_outcome(bundled_plan_files(&root), |paths| json!(paths.len()), &roots)
        } else {
            domain_outcome(bundled_plan_files(&root), |paths| relative(paths, &root), &roots)
        };
        check(failures, format!("synthetic {name} files"), files, &case["files"]);
    }
}

fn sources_shape(sources: Option<CampaignPlanToolAuthority<'_, AuthorityStore>>) -> Json {
    let Some(sources) = sources else { return Json::Null };
    let declarations: Vec<Json> = sources
        .character_declarations()
        .unwrap()
        .iter()
        .map(|item| json!([item.kind, item.schema_id]))
        .collect();
    json!({
        "ruleset": Json::Object(sources.ruleset.as_payload()),
        "setting": Json::Object(sources.setting.as_payload()),
        "closure_sha256": sources.closure_sha256,
        "setting_paths": sources.setting_paths,
        "setting_knowledge_ids": sources.setting_knowledge_ids,
        "participant_schemas_sha256": digest(&canonical_json_bytes(&Json::Array(sources.participant_schemas.clone()))),
        "setting_character_schema": sources.setting_character_schema.clone().map(Json::Object),
        "state_schemas_sha256": digest(&canonical_json_bytes(&Json::Array(sources.state_schemas.clone()))),
        "declarations": declarations,
    })
}

fn sources(vectors: &Json, bundled: &BundledPackages, scratch: &Path, failures: &mut Vec<String>) {
    let mut base = Vec::new();
    GzDecoder::new(std::fs::File::open(repository().join(vectors["base"].as_str().unwrap())).unwrap())
        .read_to_end(&mut base)
        .unwrap();
    let database = scratch.join("authority.sqlite3");
    std::fs::write(&database, base).unwrap();
    let store = AuthorityStore::open_for_application_owner(&database, None).unwrap();
    let candidates = store.package_candidates().unwrap();
    let read = |path: &str| {
        pyjson::loads(&std::fs::read_to_string(repository().join(path)).unwrap().replace("\r\n", "\n"))
            .unwrap()
    };
    let riichi_plan = read("campaign-plans/riichi-quick-test/plan.json");
    let riichi_markdown =
        std::fs::read_to_string(repository().join("campaign-plans/riichi-quick-test/plan.md"))
            .unwrap()
            .replace("\r\n", "\n");
    for case in vectors["sources"].as_array().unwrap() {
        let candidate = candidates.iter().find(|item| item.candidate_id() == case["candidate"]).unwrap();
        let setting_id = case["setting_id"].as_str();
        let label = format!("sources {} {setting_id:?}", candidate.candidate_id());
        let result = campaign_plan_sources(&store, candidate.package_lock(), setting_id);
        let ok = matches!(result, Ok(Some(_)));
        check(failures, label.clone(), store_outcome(result, sources_shape), &case["sources"]);
        if !ok {
            continue;
        }
        let sources = campaign_plan_sources(&store, candidate.package_lock(), setting_id).unwrap().unwrap();
        for read_case in case["reads"].as_array().unwrap() {
            let path = read_case[0].as_str().unwrap();
            let outcome = store_outcome(sources.read_setting(path), |text| digest(text.as_bytes()));
            check(failures, format!("{label} read {path}"), outcome, &read_case[1]);
        }
        let mut rebound = riichi_plan.clone();
        rebound["bindings"] = case["rebound"].clone();
        for (index, payload) in [&riichi_plan, &rebound].into_iter().enumerate() {
            let outcome =
                domain_outcome(sources.validate(payload, &riichi_markdown), |p| validated_shape(&p), &[]);
            check(failures, format!("{label} validate {index}"), outcome, &case["validate"][index]);
        }
    }
    for case in vectors["retained"].as_array().unwrap() {
        let (name, explicit) = (case[0].as_str().unwrap(), case[1].as_bool().unwrap());
        let (payload, markdown) = match name {
            "riichi" => (riichi_plan.clone(), riichi_markdown.clone()),
            "a-debt-in-iron" | "the-rift-awakening" => (
                read(&format!("campaign-plans/{name}/plan.json")),
                std::fs::read_to_string(repository().join(format!("campaign-plans/{name}/plan.md")))
                    .unwrap()
                    .replace("\r\n", "\n"),
            ),
            "riichi_rebound" => (vectors["edited_plans"][name].clone(), riichi_markdown.clone()),
            "not_object" => (vectors["edited_plans"][name].clone(), String::new()),
            _ => (
                vectors["edited_plans"][name].clone(),
                std::fs::read_to_string(repository().join("campaign-plans/a-debt-in-iron/plan.md"))
                    .unwrap()
                    .replace("\r\n", "\n"),
            ),
        };
        let none: &[sylvia_domain::package_library::PackageCandidate] = &[];
        let result =
            validate_retained_campaign_plan(&store, bundled, &payload, &markdown, explicit.then_some(none));
        check(
            failures,
            format!("retained {name} {explicit}"),
            store_outcome(result, |p| validated_shape(&p)),
            &case[2],
        );
    }
}

#[test]
fn plan_catalogue_matches_python() {
    let vectors = vectors();
    let work = Path::new(env!("CARGO_TARGET_TMPDIR")).join("plan-catalogue-vectors");
    let _ = std::fs::remove_dir_all(&work);
    let storage = work.join("storage");
    std::fs::create_dir_all(&storage).unwrap();
    let bundled = BundledPackages::new(BundledSources::from_repository_root(&repository()));
    let mut failures = Vec::new();
    collection(&vectors, &bundled, &storage, &mut failures);
    let collections = work.join("collections");
    std::fs::create_dir_all(&collections).unwrap();
    synthetic(&vectors, &collections, &mut failures);
    sources(&vectors, &bundled, &work, &mut failures);
    let _ = std::fs::remove_dir_all(&work);
    let report = Path::new(env!("CARGO_TARGET_TMPDIR")).join("plan-catalogue-differences.txt");
    let _ = std::fs::write(&report, failures.join("\n"));
    let shown: Vec<String> = failures.iter().take(6).map(|f| f.chars().take(1000).collect()).collect();
    assert!(failures.is_empty(), "{} differ: {shown:#?}", failures.len());
}
