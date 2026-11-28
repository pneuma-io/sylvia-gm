//! The Builder's Plan, import repair and subflow tools (B7) against Python: each case builds the
//! tools Python built, over the same authority and scripted owner callbacks, and compares every
//! answer, callback call and the workspace files afterwards.
//!
//! Regenerate with `tests/vectors/generate_plan_tool_vectors.py`. Values are compared with
//! `assert_same_json`, so key order counts. `plan.md` was recorded with Windows newlines; on
//! another platform the expected text uses that platform's, as Python's text mode writes it.

use serde_json::{json, Value as J};
use std::collections::{BTreeSet, VecDeque};
use std::sync::{Arc, Mutex, OnceLock};
use sylvia_agent::builder::plan_tools::{
    campaign_plan_tools, creation_subflow_completion_tool, import_repair_tools, plan_draft_check_tool,
    OwnerCheck, PlanAuthority, PlanAuthoritySource, PlanPublisher,
};
use sylvia_agent::builder::tools::ToolSpec;
use sylvia_app::campaign_plan_materializer::CampaignPlanDraft;
use sylvia_base::hash::sha256_hex;
use sylvia_base::testing::assert_same_json;
use sylvia_base::JsonMap;
use sylvia_domain::campaign_plan::{PackageBinding, ValidatedCampaignPlan};
use sylvia_domain::error::RulesetDiagnostic;
use sylvia_domain::DomainError;

fn vectors() -> &'static J {
    static VECTORS: OnceLock<J> = OnceLock::new();
    VECTORS.get_or_init(|| {
        serde_json::from_str(include_str!("vectors/plan_tool_vectors.json")).expect("the vectors")
    })
}

fn map(value: &J) -> JsonMap {
    value.as_object().cloned().unwrap_or_default()
}

fn strings(value: &J) -> BTreeSet<String> {
    value.as_array().into_iter().flatten().map(|item| item.as_str().unwrap().to_owned()).collect()
}

/// The exception a scripted outcome raises, as the port carries Python's class.
fn raised(spec: &J) -> DomainError {
    let message = spec["message"].as_str().unwrap_or("scripted refusal").to_owned();
    match spec["class"].as_str().unwrap() {
        "CommandRejected" => {
            let diagnostics = spec["diagnostics"]
                .as_array()
                .into_iter()
                .flatten()
                .map(|item| {
                    let text = |key: &str| item[key].as_str().map(str::to_owned);
                    RulesetDiagnostic {
                        code: text("code").unwrap(),
                        phase: text("phase").unwrap(),
                        message: text("message").unwrap(),
                        file: text("file"),
                        path: text("path"),
                        package_id: text("package_id"),
                        expected: text("expected"),
                        guidance: text("guidance"),
                        ..RulesetDiagnostic::default()
                    }
                })
                .collect();
            DomainError::command_rejected_with(
                message,
                spec["code"].as_str().unwrap_or("command_rejected"),
                diagnostics,
            )
        }
        "StaleRevision" => DomainError::authority("StaleRevision", message),
        "ValueError" => DomainError::value(message),
        "OSError" => DomainError::uncaught("OSError", message),
        "KeyError" => DomainError::uncaught("KeyError", format!("'{message}'")),
        other => panic!("no scripted class {other}"),
    }
}

#[derive(Clone)]
struct Script {
    outcomes: Arc<Mutex<VecDeque<J>>>,
    calls: Arc<Mutex<Vec<J>>>,
}

impl Script {
    fn new(outcomes: &J, calls: Arc<Mutex<Vec<J>>>) -> Self {
        Self {
            outcomes: Arc::new(Mutex::new(outcomes.as_array().into_iter().flatten().cloned().collect())),
            calls,
        }
    }
    fn record(&self, call: J) {
        self.calls.lock().unwrap().push(call);
    }
    fn next(&self) -> J {
        self.outcomes.lock().unwrap().pop_front().unwrap_or(json!({"return": {"note": "default"}}))
    }
    fn answer(&self, call: J) -> Result<JsonMap, DomainError> {
        self.record(call);
        let outcome = self.next();
        match outcome.get("raise") {
            Some(spec) => Err(raised(spec)),
            None => Ok(map(&outcome["return"])),
        }
    }
    fn check(&self, call: J) -> OwnerCheck {
        let script = self.clone();
        Arc::new(move || script.answer(call.clone()))
    }
}

fn binding(value: &J) -> PackageBinding {
    PackageBinding {
        package_id: value["package_id"].as_str().unwrap().into(),
        version: value["version"].as_str().unwrap().into(),
        content_sha256: value["content_sha256"].as_str().unwrap().into(),
    }
}

fn authority(spec: &J, script: &Script) -> PlanAuthority {
    let (files, reads) = (spec["setting_files"].clone(), script.clone());
    PlanAuthority {
        ruleset: binding(&spec["ruleset"]),
        setting: binding(&spec["setting"]),
        closure_sha256: spec["closure_sha256"].as_str().unwrap().into(),
        setting_paths: strings(&spec["setting_paths"]),
        read_setting: Arc::new(move |path: &str| {
            reads.record(json!({"read_setting": path}));
            match &files[path] {
                J::String(text) => Ok(text.clone()),
                other => Err(raised(&other["raise"])),
            }
        }),
        setting_knowledge_ids: strings(&spec["setting_knowledge_ids"]),
        participant_schemas: spec["participant_schemas"].as_array().unwrap().iter().map(map).collect(),
        setting_character_schema: spec["setting_character_schema"].as_object().cloned(),
        state_schemas: spec["state_schemas"].as_array().unwrap().iter().map(map).collect(),
    }
}

fn published() -> CampaignPlanDraft {
    let fields = &vectors()["published"];
    CampaignPlanDraft {
        plan: ValidatedCampaignPlan {
            payload: map(&fields["payload"]),
            content_sha256: fields["content_sha256"].as_str().unwrap().into(),
            artifact_id: fields["artifact_id"].as_str().unwrap().into(),
            title: fields["title"].as_str().unwrap().into(),
            readable_markdown: fields["readable_markdown"].as_str().unwrap().into(),
            referenced_setting_paths: fields["referenced_setting_paths"]
                .as_array()
                .unwrap()
                .iter()
                .map(|item| item.as_str().unwrap().to_owned())
                .collect(),
        },
        plan_path: fields["plan_path"].as_str().unwrap().into(),
        readable_path: fields["readable_path"].as_str().unwrap().into(),
    }
}

fn build(case: &J, script: &Script, workspace: &std::path::Path) -> Vec<ToolSpec> {
    match case["tool"].as_str().unwrap() {
        "plan" => {
            let fixed = authority(&case["authority"], script);
            let source = if case["deferred"] == true {
                let selections: Arc<Mutex<VecDeque<bool>>> = Arc::new(Mutex::new(
                    case["selections"]
                        .as_array()
                        .unwrap()
                        .iter()
                        .map(|item| item.as_bool().unwrap())
                        .collect(),
                ));
                let script = script.clone();
                PlanAuthoritySource::Deferred(Arc::new(move || {
                    script.record(json!({"select_authority": null}));
                    Ok(selections.lock().unwrap().pop_front().unwrap().then(|| fixed.clone()))
                }))
            } else {
                PlanAuthoritySource::Fixed(Box::new(fixed))
            };
            let publisher = case["publisher"].as_array().map(|outcomes| {
                let outcomes: Arc<Mutex<VecDeque<J>>> =
                    Arc::new(Mutex::new(outcomes.iter().cloned().collect()));
                let script = script.clone();
                Arc::new(move || {
                    script.record(json!({"publish_plan": null}));
                    let outcome = outcomes.lock().unwrap().pop_front().unwrap();
                    match outcome.as_str() {
                        Some("published") => Ok((published(), json!({"artifact_id": "library-1"}))),
                        Some("published-without-artifact") => Ok((published(), J::Null)),
                        _ => Err(raised(&outcome["raise"])),
                    }
                }) as PlanPublisher
            });
            let checker = case["checker"].as_array().map(|outcomes| {
                Script::new(&J::Array(outcomes.clone()), Arc::new(Mutex::new(Vec::new())))
                    .check(json!({"check_plan": null}))
            });
            campaign_plan_tools(workspace, source, publisher, checker).unwrap()
        }
        "plan_check" => vec![plan_draft_check_tool(script.check(json!({"check_plan": null}))).unwrap()],
        "import_repair" => import_repair_tools(
            script.check(json!({"check_repair": null})),
            script.check(json!({"publish_repair": null})),
        )
        .unwrap(),
        _ => vec![creation_subflow_completion_tool().unwrap()],
    }
}

struct Workspace(std::path::PathBuf);

impl Workspace {
    fn new(index: usize, files: &J) -> Self {
        let path = std::env::temp_dir().join(format!("sylvia-plan-tools-{}-{index}", std::process::id()));
        let _ = std::fs::remove_dir_all(&path);
        std::fs::create_dir_all(&path).unwrap();
        for (relative, content) in files.as_object().into_iter().flatten() {
            let file = path.join(relative);
            std::fs::create_dir_all(file.parent().unwrap()).unwrap();
            std::fs::write(file, content.as_str().unwrap()).unwrap();
        }
        Self(path)
    }

    fn files(&self) -> J {
        let mut found = Vec::new();
        let mut pending = vec![self.0.clone()];
        while let Some(folder) = pending.pop() {
            for entry in std::fs::read_dir(&folder).unwrap() {
                let path = entry.unwrap().path();
                if path.is_dir() {
                    pending.push(path);
                } else {
                    let relative: Vec<String> = path
                        .strip_prefix(&self.0)
                        .unwrap()
                        .components()
                        .map(|part| part.as_os_str().to_string_lossy().into_owned())
                        .collect();
                    found.push((relative.join("/"), std::fs::read(&path).unwrap()));
                }
            }
        }
        found.sort_by(|a, b| a.0.split('/').cmp(b.0.split('/')));
        let mut files = JsonMap::new();
        for (relative, data) in found {
            let value = if data.len() > 4096 {
                json!({"sha256": sha256_hex(&data), "length": data.len()})
            } else {
                J::String(String::from_utf8(data).unwrap())
            };
            files.insert(relative, value);
        }
        J::Object(files)
    }
}

impl Drop for Workspace {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

/// The recorded files with `plan.md`'s newlines as this platform's text mode writes them.
fn expected_files(recorded: &J) -> J {
    let mut files = map(recorded);
    for (name, value) in &mut files {
        if name.ends_with("plan.md") && !cfg!(windows) {
            if let J::String(text) = value {
                *value = J::String(text.replace("\r\n", "\n"));
            }
        }
    }
    J::Object(files)
}

fn answer(tool: &ToolSpec, params: &J) -> J {
    match (tool.handler)(params.as_object().unwrap()) {
        Ok(result) => json!({"result": result}),
        Err(message) => json!({"message": message}),
    }
}

fn expected_answer(recorded: &J) -> J {
    match recorded.get("raised") {
        Some(_) => {
            let mut expected = JsonMap::new();
            if let Some(tool) = recorded.get("tool") {
                expected.insert("tool".into(), tool.clone());
            }
            expected.insert("message".into(), recorded["message"].clone());
            J::Object(expected)
        }
        None => recorded.clone(),
    }
}

#[test]
fn plan_tools_match_python() {
    for (index, case) in vectors()["cases"].as_array().unwrap().iter().enumerate() {
        let label = format!("case {index} ({})", case["tool"]);
        let script = Script::new(&case["outcomes"], Arc::new(Mutex::new(Vec::new())));
        let workspace = Workspace::new(index, &case["files"]);
        let tools = build(case, &script, &workspace.0);
        let names: Vec<J> = tools.iter().map(|tool| J::String(tool.name.clone())).collect();
        assert_same_json(&J::Array(names), &case["tool_names"], format!("{label} tools"));
        for (call, recorded) in
            case["calls"].as_array().unwrap().iter().zip(case["answers"].as_array().unwrap())
        {
            let found = match call {
                J::Array(pair) => {
                    let name = pair[0].as_str().unwrap();
                    let tool = tools.iter().find(|tool| tool.name == name).unwrap();
                    let mut found = map(&answer(tool, &pair[1]));
                    found.shift_insert(0, "tool".into(), name.into());
                    J::Object(found)
                }
                params => answer(&tools[0], params),
            };
            assert_same_json(&found, &expected_answer(recorded), format!("{label} call {call}"));
        }
        assert_same_json(
            &J::Array(script.calls.lock().unwrap().clone()),
            &case["callback_calls"],
            format!("{label} callbacks"),
        );
        assert_same_json(&workspace.files(), &expected_files(&case["files_after"]), format!("{label} files"));
    }
}
