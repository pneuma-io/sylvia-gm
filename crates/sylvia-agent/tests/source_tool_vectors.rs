//! The Builder's source, reference, library, Setting and content tools (B6) against Python: each
//! case builds the tools Python built, over scripted stand-ins for the owner's objects, and
//! compares every answer, callback call and the workspace files afterwards.
//!
//! Regenerate with `tests/vectors/generate_source_tool_vectors.py`. Values are compared with
//! `assert_same_json`, so key order counts.

use serde_json::{json, Value as J};
use std::collections::VecDeque;
use std::sync::{Arc, Mutex, OnceLock};
use sylvia_agent::builder::source_tools::{
    builder_reference_copy_tool, builder_reference_tool, builder_setting_source_tool, builder_source_tool,
    campaign_content_tools, campaign_setting_tool, library_source_copy_tool, library_source_tool,
    CampaignContent, LibrarySources, ReferenceSource, References, SourceSelection,
};
use sylvia_agent::builder::tools::ToolSpec;
use sylvia_base::hash::sha256_hex;
use sylvia_base::testing::assert_same_json;
use sylvia_base::JsonMap;
use sylvia_domain::error::RulesetDiagnostic;
use sylvia_domain::DomainError;

fn vectors() -> &'static J {
    static VECTORS: OnceLock<J> = OnceLock::new();
    VECTORS.get_or_init(|| {
        serde_json::from_str(include_str!("vectors/source_tool_vectors.json")).expect("the recorded vectors")
    })
}

fn map(value: &J) -> JsonMap {
    value.as_object().cloned().unwrap_or_default()
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
        "TypeError" => DomainError::type_error(message),
        "OSError" => DomainError::uncaught("OSError", message),
        "KeyError" => DomainError::uncaught("KeyError", format!("'{message}'")),
        other => panic!("no scripted class {other}"),
    }
}

/// The scripted outcomes and the calls the stand-ins received.
#[derive(Clone)]
struct Script {
    outcomes: Arc<Mutex<VecDeque<J>>>,
    calls: Arc<Mutex<Vec<J>>>,
}

impl Script {
    fn new(outcomes: &J) -> Self {
        Self {
            outcomes: Arc::new(Mutex::new(outcomes.as_array().into_iter().flatten().cloned().collect())),
            calls: Arc::new(Mutex::new(Vec::new())),
        }
    }

    fn record(&self, call: J) {
        self.calls.lock().unwrap().push(call);
    }

    fn answer(&self, call: J) -> Result<J, DomainError> {
        self.record(call);
        let outcome =
            self.outcomes.lock().unwrap().pop_front().unwrap_or(json!({"return": {"note": "default"}}));
        match outcome.get("raise") {
            Some(spec) => Err(raised(spec)),
            None => Ok(outcome["return"].clone()),
        }
    }
}

impl SourceSelection for Script {
    fn select(&self, ruleset_id: &str) -> Result<J, DomainError> {
        self.answer(json!({"select": ruleset_id}))
    }
    fn select_setting(&self, candidate_id: Option<&str>, setting_id: &str) -> Result<J, DomainError> {
        self.answer(json!({"select_setting": {"candidate_id": candidate_id, "setting_id": setting_id}}))
    }
}

impl LibrarySources for Script {
    fn candidates(&self, query: &str) -> Result<Vec<JsonMap>, DomainError> {
        Ok(self.answer(json!({"candidates": query}))?.as_array().unwrap().iter().map(map).collect())
    }
    fn files(&self, candidate_id: &str) -> Result<JsonMap, DomainError> {
        Ok(map(&self.answer(json!({"files": candidate_id}))?))
    }
    fn read(&self, candidate_id: &str, path: &str) -> Result<JsonMap, DomainError> {
        Ok(map(&self.answer(json!({"read": [candidate_id, path]}))?))
    }
    fn record(&self, candidate_id: &str, prefix: &str) -> Result<JsonMap, DomainError> {
        Ok(map(&self.answer(json!({"record": [candidate_id, prefix]}))?))
    }
}

/// The chat's reference source over the case's fixed table.
fn references(spec: &J, script: &Script) -> ReferenceSource {
    let (spec, script) = (spec.clone(), script.clone());
    Arc::new(move || {
        script.record(json!({"references": null}));
        if spec.is_null() {
            return Ok(None);
        }
        let paths =
            spec["paths"].as_array().unwrap().iter().map(|path| path.as_str().unwrap().to_owned()).collect();
        let (contents, reads) = (spec["contents"].clone(), script.clone());
        Ok(Some(References {
            paths,
            read: Arc::new(move |path: &str| {
                reads.record(json!({"read_reference": path}));
                match &contents[path] {
                    J::String(text) => Ok(text.clone()),
                    other => Err(raised(&other["raise"])),
                }
            }),
        }))
    })
}

/// A fresh workspace holding a case's files, removed when dropped.
struct Workspace(std::path::PathBuf);

impl Workspace {
    fn new(index: usize, files: &J) -> Self {
        let path = std::env::temp_dir().join(format!("sylvia-source-tools-{}-{index}", std::process::id()));
        let _ = std::fs::remove_dir_all(&path);
        std::fs::create_dir_all(&path).unwrap();
        for (relative, content) in files.as_object().into_iter().flatten() {
            let file = path.join(relative);
            std::fs::create_dir_all(file.parent().unwrap()).unwrap();
            std::fs::write(file, content.as_str().unwrap()).unwrap();
        }
        Self(path)
    }

    /// The files under the workspace, as the generator records them.
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

fn answer(tool: &ToolSpec, params: &J) -> J {
    match (tool.handler)(params.as_object().unwrap()) {
        Ok(result) => json!({"result": result}),
        Err(message) => json!({"message": message}),
    }
}

/// Python's answer, with an escaped exception's class dropped: a handler's error keeps only its
/// text.
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

fn build(case: &J, script: &Script, workspace: &std::path::Path) -> Vec<ToolSpec> {
    let selection = || Arc::new(script.clone()) as Arc<dyn SourceSelection>;
    let library = || Arc::new(script.clone()) as Arc<dyn LibrarySources>;
    match case["tool"].as_str().unwrap() {
        "select_ruleset" => vec![builder_source_tool(selection()).unwrap()],
        "select_setting" => vec![builder_setting_source_tool(selection()).unwrap()],
        "references" => vec![builder_reference_tool(references(&case["references"], script)).unwrap()],
        "reference_copy" => {
            vec![builder_reference_copy_tool(references(&case["references"], script), workspace).unwrap()]
        }
        "library" => vec![library_source_tool(library()).unwrap()],
        "library_copy" => vec![library_source_copy_tool(library(), workspace).unwrap()],
        "setting" => {
            let script = script.clone();
            let publisher = Arc::new(move |package_id: &str, version: &str, title: &str| {
                Ok(map(&script.answer(
                    json!({"publish": {"package_id": package_id, "version": version, "title": title}}),
                )?))
            });
            vec![campaign_setting_tool(publisher, case["checking"] == true).unwrap()]
        }
        _ => {
            let refreshing = script.clone();
            let refresh = (case["refresh"] == true).then(|| {
                Arc::new(move || {
                    Ok(refreshing
                        .answer(json!({"refresh": null}))?
                        .as_array()
                        .unwrap()
                        .iter()
                        .map(map)
                        .collect())
                }) as Arc<dyn Fn() -> Result<Vec<JsonMap>, DomainError> + Send + Sync>
            });
            let items = case["items"].as_array().unwrap().iter().map(map).collect();
            campaign_content_tools(CampaignContent { items, refresh }).unwrap()
        }
    }
}

#[test]
fn source_tools_match_python() {
    for (index, case) in vectors()["cases"].as_array().unwrap().iter().enumerate() {
        let label = format!("case {index} ({})", case["tool"]);
        let script = Script::new(&case["outcomes"]);
        let workspace = Workspace::new(index, &case["files"]);
        let tools = build(case, &script, &workspace.0);
        for (call, recorded) in
            case["calls"].as_array().unwrap().iter().zip(case["answers"].as_array().unwrap())
        {
            let found = if case["tool"] == "content" {
                let name = call[0].as_str().unwrap();
                let tool = tools.iter().find(|tool| tool.name == name).unwrap();
                let mut found = map(&answer(tool, &call[1]));
                found.shift_insert(0, "tool".into(), name.into());
                J::Object(found)
            } else {
                answer(&tools[0], call)
            };
            assert_same_json(&found, &expected_answer(recorded), format!("{label} call {call}"));
        }
        assert_same_json(
            &J::Array(script.calls.lock().unwrap().clone()),
            &case["callback_calls"],
            format!("{label} callbacks"),
        );
        assert_same_json(&workspace.files(), &case["files_after"], format!("{label} files"));
    }
}
