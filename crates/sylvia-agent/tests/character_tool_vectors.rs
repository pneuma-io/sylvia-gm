//! The Builder's character tools (B5) against Python: each case builds the tools Python built,
//! with stand-in owner callbacks answering the same scripted outcomes, and compares every
//! definition, answer, callback call and the workspace files afterwards.
//!
//! Regenerate with `tests/vectors/generate_character_tool_vectors.py`. Values are compared with
//! `assert_same_json`, so key order counts. Long test strings are stored as `$repeat` and `$json`
//! markers, expanded here as the generator expands them.

use indexmap::IndexMap;
use serde_json::{json, Value as J};
use std::collections::VecDeque;
use std::path::Path;
use std::sync::{Arc, Mutex, OnceLock};
use sylvia_agent::builder::character_profile_tools::character_profile_tools;
use sylvia_agent::builder::character_tools::{
    character_draft_tool, participant_artifact_tool, participant_draft_check_tool, participant_file_tool,
    reuse_character_copy_tool, ParticipantInput, ParticipantPublisher, ParticipantReader,
    SourceProfileResolver,
};
use sylvia_agent::builder::content_definition::content_definition_tool;
use sylvia_agent::builder::tools::{tool_spec_to_openai, ToolSpec};
use sylvia_app::character_profiles::CharacterProfile;
use sylvia_base::hash::sha256_hex;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::testing::assert_same_json;
use sylvia_base::JsonMap;
use sylvia_domain::error::MechanicsValidationIssue;
use sylvia_domain::error::RulesetDiagnostic;
use sylvia_domain::package_library::ParticipantArtifactDeclaration;
use sylvia_domain::DomainError;

fn vectors() -> &'static J {
    static VECTORS: OnceLock<J> = OnceLock::new();
    VECTORS.get_or_init(|| {
        serde_json::from_str(include_str!("vectors/character_tool_vectors.json"))
            .expect("the recorded vectors")
    })
}

/// The value a `$repeat` or `$json` marker stands for.
fn expand(value: &J) -> J {
    match value {
        J::Object(map) if map.len() == 1 && map.contains_key("$repeat") => {
            let pair = &map["$repeat"];
            J::String(pair[0].as_str().unwrap().repeat(usize::try_from(pair[1].as_u64().unwrap()).unwrap()))
        }
        J::Object(map) if map.len() == 1 && map.contains_key("$json") => {
            J::String(pyjson::dumps(&expand(&map["$json"]), Dump::DEFAULT))
        }
        J::Object(map) => J::Object(map.iter().map(|(key, item)| (key.clone(), expand(item))).collect()),
        J::Array(items) => J::Array(items.iter().map(expand).collect()),
        other => other.clone(),
    }
}

fn map(value: &J) -> JsonMap {
    value.as_object().cloned().unwrap_or_default()
}

fn declaration(fields: &J) -> ParticipantArtifactDeclaration {
    ParticipantArtifactDeclaration {
        kind: fields["kind"].as_str().unwrap().into(),
        schema_id: fields["schema_id"].as_str().unwrap().into(),
        schema_sha256: fields["schema_sha256"].as_str().unwrap().into(),
        schema: map(&fields["schema"]),
        entity_state_schema: fields["entity_state_schema"].as_object().cloned(),
        initial_state_calculations: fields["initial_state_calculations"]
            .as_array()
            .unwrap()
            .iter()
            .map(map)
            .collect(),
        initial_state_validation: fields["initial_state_validation"].as_object().cloned(),
    }
}

fn declarations(names: &J) -> Vec<ParticipantArtifactDeclaration> {
    let all = &vectors()["declarations"];
    names.as_array().into_iter().flatten().map(|name| declaration(&all[name.as_str().unwrap()])).collect()
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
                .map(|item| RulesetDiagnostic {
                    code: item["code"].as_str().unwrap().into(),
                    phase: item["phase"].as_str().unwrap().into(),
                    message: item["message"].as_str().unwrap().into(),
                    ..RulesetDiagnostic::default()
                })
                .collect();
            DomainError::command_rejected_with(
                message,
                spec["code"].as_str().unwrap_or("command_rejected"),
                diagnostics,
            )
        }
        "ParticipantValidationError" => DomainError::Issues {
            error_type: "ParticipantValidationError",
            message: "Reusable participant does not match its exact schema".into(),
            issues: spec["issues"]
                .as_array()
                .unwrap()
                .iter()
                .map(|issue| MechanicsValidationIssue {
                    path: issue["path"].as_str().unwrap().into(),
                    kind: issue["kind"].as_str().unwrap().into(),
                    expected_type: issue["expected_type"].as_str().map(Into::into),
                    actual_type: issue["actual_type"].as_str().map(Into::into),
                    parent_path: None,
                    allowed_values: Vec::new(),
                    minimum: None,
                    maximum: None,
                    actual_length: None,
                })
                .collect(),
        },
        "StaleRevision" => DomainError::authority("StaleRevision", message),
        "ValueError" => DomainError::value(message),
        "TypeError" => DomainError::type_error(message),
        "OSError" => DomainError::uncaught("OSError", message),
        // `str(KeyError(message))` is its repr.
        "KeyError" => DomainError::uncaught("KeyError", format!("'{message}'")),
        "RuntimeError" => DomainError::uncaught("RuntimeError", message),
        other => panic!("no scripted class {other}"),
    }
}

/// The scripted outcomes and the calls the callbacks received.
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

    fn answer(&self, call: J) -> Result<J, DomainError> {
        self.calls.lock().unwrap().push(call);
        let outcome =
            self.outcomes.lock().unwrap().pop_front().unwrap_or(json!({"return": {"note": "default"}}));
        match outcome.get("raise") {
            Some(spec) => Err(raised(spec)),
            None => Ok(outcome["return"].clone()),
        }
    }

    fn publisher(&self) -> ParticipantPublisher {
        let script = self.clone();
        Arc::new(move |input: &ParticipantInput| {
            let mut call = JsonMap::new();
            call.insert("role".into(), input.role.as_str().into());
            call.insert("summary".into(), input.summary.as_str().into());
            call.insert("authored_profile".into(), input.authored_profile.as_str().into());
            call.insert("participant".into(), J::Object(input.participant.clone()));
            call.insert("entity_state".into(), J::Object(input.entity_state.clone()));
            if let Some(schema_id) = &input.schema_id {
                call.insert("schema_id".into(), schema_id.as_str().into());
            }
            if let Some(source_profile) = &input.source_profile {
                call.insert("source_profile".into(), source_profile.clone());
            }
            if let Some(setting_data) = &input.setting_data {
                call.insert("setting_data".into(), J::Object(setting_data.clone()));
            }
            Ok(map(&script.answer(json!({"publish": call}))?))
        })
    }

    fn reader(&self, kind: &'static str) -> ParticipantReader {
        let script = self.clone();
        Arc::new(move |artifact_id: &str, digest: &str, schema_id: Option<&str>| {
            let call = json!({kind: {"artifact_id": artifact_id, "expected_content_sha256": digest, "schema_id": schema_id}});
            Ok(map(&script.answer(call)?))
        })
    }

    fn resolver(&self) -> SourceProfileResolver {
        let script = self.clone();
        Arc::new(move |source_profile: Option<&J>| {
            let answer = script
                .answer(json!({"resolve_source_profile": source_profile.cloned().unwrap_or(J::Null)}))?;
            Ok(answer.as_object().cloned())
        })
    }
}

/// A fresh workspace holding a case's files, removed when dropped.
struct Workspace(std::path::PathBuf);

impl Workspace {
    fn new(index: usize, files: &J) -> Self {
        let path =
            std::env::temp_dir().join(format!("sylvia-character-tools-{}-{index}", std::process::id()));
        let _ = std::fs::remove_dir_all(&path);
        std::fs::create_dir_all(&path).unwrap();
        for (relative, content) in files.as_object().into_iter().flatten() {
            let file = path.join(relative);
            std::fs::create_dir_all(file.parent().unwrap()).unwrap();
            let bytes = match content.get("base64") {
                Some(encoded) => {
                    sylvia_base::pybase64::b64decode_validate(encoded.as_str().unwrap()).unwrap()
                }
                None => content.as_str().unwrap().as_bytes().to_vec(),
            };
            std::fs::write(file, bytes).unwrap();
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
        // `sorted(workspace.rglob("*"))`: by path, part by part.
        found.sort_by(|a, b| a.0.split('/').cmp(b.0.split('/')));
        let mut files = JsonMap::new();
        for (relative, data) in found {
            let value = if data.len() > 4096 {
                json!({"sha256": sha256_hex(&data), "length": data.len()})
            } else {
                match String::from_utf8(data.clone()) {
                    Ok(text) => J::String(text),
                    Err(_) => json!({"base64": sylvia_base::pybase64::b64encode(&data)}),
                }
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

fn build(case: &J, script: &Script, workspace: &Path) -> Result<Vec<ToolSpec>, String> {
    let role = case["role"].as_str();
    let named = declarations(&case["declarations"]);
    let mut schemas: IndexMap<String, JsonMap> = IndexMap::new();
    let all = &vectors()["declarations"];
    for name in case["schemas"].as_array().into_iter().flatten() {
        let name = name.as_str().unwrap();
        schemas.insert(name.into(), map(&all[name]["schema"]));
    }
    let strings = |key: &str| -> Vec<String> {
        case[key].as_array().into_iter().flatten().map(|item| item.as_str().unwrap().to_owned()).collect()
    };
    let built = match case["tool"].as_str().unwrap() {
        "artifact" => participant_artifact_tool(role, script.publisher(), &schemas, case["checking"] == true)
            .map(|tool| vec![tool]),
        "draft_check" => participant_draft_check_tool(
            role,
            &named,
            case["setting_character_schema"].as_object().cloned(),
            (case["resolver"] == true).then(|| script.resolver()),
        )
        .map(|tool| vec![tool]),
        "file" => {
            let inline = if case["checking"] == true {
                participant_draft_check_tool(role, &named, None, None)
            } else {
                participant_artifact_tool(role, script.publisher(), &schemas, false)
            };
            inline.and_then(|inline| participant_file_tool(&inline, workspace)).map(|tool| vec![tool])
        }
        "character_draft" => character_draft_tool(script.reader("read"), workspace, &strings("schema_ids"))
            .map(|tool| vec![tool]),
        "reuse" => {
            reuse_character_copy_tool(script.reader("reuse"), &strings("schema_ids")).map(|tool| vec![tool])
        }
        "profile" => {
            let (save, read) = (script.clone(), script.clone());
            character_profile_tools(
                workspace,
                Arc::new(move |data: &[u8]| Ok(map(&save.answer(json!({"save": sha256_hex(data)}))?))),
                Arc::new(move |artifact_id: &str, digest: &str| {
                    let profile = read.answer(json!({"read_profile": [artifact_id, digest]}))?;
                    Ok(CharacterProfile {
                        artifact_id: artifact_id.into(),
                        content_sha256: digest.into(),
                        profile,
                    })
                }),
                None,
            )
        }
        "card" => {
            let (save, read, import) = (script.clone(), script.clone(), script.clone());
            let import_source: Option<sylvia_agent::builder::character_profile_tools::ProfileImport> =
                if case.get("no_import").is_some() {
                    None
                } else {
                    Some(Arc::new(move |data: &[u8]| {
                        Ok(map(&import.answer(json!({"import": sha256_hex(data)}))?))
                    }))
                };
            character_profile_tools(
                workspace,
                Arc::new(move |data: &[u8]| Ok(map(&save.answer(json!({"save": sha256_hex(data)}))?))),
                Arc::new(move |artifact_id: &str, digest: &str| {
                    let profile = read.answer(json!({"read_profile": [artifact_id, digest]}))?;
                    Ok(CharacterProfile {
                        artifact_id: artifact_id.into(),
                        content_sha256: digest.into(),
                        profile,
                    })
                }),
                import_source,
            )
        }
        _ => content_definition_tool(&named, case["setting_character_schema"].as_object().cloned())
            .map(|tool| vec![tool]),
    };
    built.map_err(|error| error.message)
}

#[test]
fn character_tools_match_python() {
    for (index, compact) in vectors()["cases"].as_array().unwrap().iter().enumerate() {
        let case = expand(compact);
        let label = format!("case {index} ({})", case["tool"]);
        let script = Script::new(&case["outcomes"]);
        let workspace = Workspace::new(index, &case["files"]);
        let tools = match build(&case, &script, &workspace.0) {
            Ok(tools) => tools,
            Err(message) => {
                assert_same_json(
                    &json!({"message": message}),
                    &json!({"message": case["construction"]["message"]}),
                    &label,
                );
                continue;
            }
        };
        let definitions: Vec<J> = tools.iter().map(tool_spec_to_openai).collect();
        assert_same_json(&J::Array(definitions), &compact["definitions"], format!("{label} definitions"));
        for (call, recorded) in
            case["calls"].as_array().unwrap().iter().zip(compact["answers"].as_array().unwrap())
        {
            let found = if case["tool"] == "profile" || case["tool"] == "card" {
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
            &compact["callback_calls"],
            format!("{label} callbacks"),
        );
        assert_same_json(&workspace.files(), &compact["files_after"], format!("{label} files"));
    }
}
