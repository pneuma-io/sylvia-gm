//! Compare the Builder tool layer (B2a) with the frozen Python, on this platform: the registry's
//! definitions, then every scripted workspace tool call in order over the same tree, the
//! registry's execution shapes, parameter-only conversions, and the files left at the end.
//!
//! Regenerate with `tests/vectors/generate_builder_tool_vectors.py` on each platform (needs the
//! snapshot's Python).

use serde_json::json;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use sylvia_agent::builder::mode::AgentMode;
use sylvia_agent::builder::tools::handlers::{builder_workspace_tools, Workspace};
use sylvia_agent::builder::tools::{
    tool_spec_to_openai, ToolHandler, ToolParameter, ToolRegistry, ToolSpec, ToolsetName,
};
use sylvia_base::hash::sha256_hex;
use sylvia_base::{pyjson, Json, JsonMap};

#[cfg(windows)]
const VECTORS: &str = include_str!("vectors/builder_tool_vectors.windows.json");
#[cfg(not(windows))]
const VECTORS: &str = include_str!("vectors/builder_tool_vectors.linux.json");

fn fresh_work(name: &str) -> PathBuf {
    let work = Path::new(env!("CARGO_TARGET_TMPDIR")).join(name);
    match std::fs::remove_dir_all(&work) {
        Ok(()) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => panic!("cannot clear the old work folder {}: {error}", work.display()),
    }
    work
}

fn make_tree(root: &Path, tree: &Json) {
    std::fs::create_dir_all(root).unwrap();
    for entry in tree.as_array().unwrap() {
        let target = root.join(entry[0].as_str().unwrap());
        match &entry[1] {
            Json::Null => std::fs::create_dir(&target).unwrap(),
            Json::String(text) => std::fs::write(&target, text.as_bytes()).unwrap(),
            Json::Object(bytes) => {
                let data = hex::decode(bytes["hex"].as_str().unwrap()).unwrap();
                std::fs::write(&target, data).unwrap();
            }
            other => panic!("tree entry {other}"),
        }
    }
}

/// The generator's `mask`: each root, in its JSON-escaped forms, written `<ROOT>`.
fn mask(value: &Json, roots: &[String]) -> Json {
    let mut text = serde_json::to_string(value).unwrap();
    for root in roots {
        for form in [root.replace('\\', "\\\\\\\\"), root.replace('\\', "\\\\"), root.replace('\\', "/")] {
            text = text.replace(&form, "<ROOT>");
        }
    }
    pyjson::loads(&text).unwrap()
}

fn files(root: &Path) -> Vec<Json> {
    fn walk(base: &Path, dir: &Path, out: &mut Vec<(String, String)>) {
        for entry in std::fs::read_dir(dir).unwrap() {
            let path = entry.unwrap().path();
            if path.is_dir() {
                walk(base, &path, out);
            } else {
                let relative: Vec<String> = path
                    .strip_prefix(base)
                    .unwrap()
                    .components()
                    .map(|part| part.as_os_str().to_string_lossy().into_owned())
                    .collect();
                out.push((relative.join("/"), sha256_hex(std::fs::read(&path).unwrap())));
            }
        }
    }
    let mut out = Vec::new();
    walk(root, root, &mut out);
    out.sort();
    out.into_iter().map(|(path, digest)| json!([path, digest])).collect()
}

fn registry(workspace: &Arc<Workspace>, web: bool) -> ToolRegistry {
    let mut registry = ToolRegistry::new();
    for spec in builder_workspace_tools(workspace, web).unwrap() {
        registry.register(spec);
    }
    registry
}

fn handler(result: Result<Json, String>) -> ToolHandler {
    Arc::new(move |_params: &JsonMap| result.clone())
}

#[test]
fn builder_tools_match_python() {
    let vectors: Json = pyjson::loads(VECTORS).expect("vector file is valid JSON");
    let platform = if cfg!(windows) { "windows" } else { "linux" };
    assert_eq!(vectors["platform"], platform);
    let work = fresh_work("builder-tool-vectors");
    let root = work.join("workspace");
    make_tree(&root, &vectors["tree"]);
    let workspace = Arc::new(Workspace::new(&root, true));
    let roots = vec![
        workspace.root().to_owned(),
        root.display().to_string(),
        sylvia_app::path_guardrail::PathGuardrail::new(&work).allowed_root().to_owned(),
        work.display().to_string(),
    ];
    let mut failures = Vec::new();
    let mut check = |what: String, python: &Json, rust: Json| {
        if &rust != python {
            let (python, rust) = (python.to_string(), rust.to_string());
            failures.push(format!(
                "{what}: python {}, rust {}",
                &python[..python.len().min(700)],
                &rust[..rust.len().min(700)]
            ));
        }
    };

    let web = registry(&workspace, true);
    let quiet = registry(&workspace, false);
    let definitions = &vectors["definitions"];
    for (name, registry, mode) in [
        ("web", &web, AgentMode::BuilderFreeform),
        ("no_web", &quiet, AgentMode::BuilderFreeform),
        ("interview", &web, AgentMode::BuilderInterview),
        ("dm", &web, AgentMode::DmSession),
    ] {
        check(format!("definitions {name}"), &definitions[name], Json::Array(registry.get_definitions(mode)));
    }

    // The differences row for `search_files` patterns: Python accepts the verbose flag, which this
    // port refuses, and `fancy_regex` words its own compile errors. Each is checked as written.
    let known: HashMap<&str, Json> = HashMap::from([
        (
            "(?x) T O D O",
            json!({"success": false, "error": "Invalid regex pattern: The verbose flag is not supported in this search"}),
        ),
        (
            "(?<=T+)O",
            json!({"success": false, "error": "Invalid regex pattern: Error compiling regex: Look-behind assertion without constant size"}),
        ),
    ]);
    let mut shown = 0;
    // A recorded `expected_version` is a content hash, the same on both sides.
    for (index, step) in vectors["steps"].as_array().unwrap().iter().enumerate() {
        let params = step["params"].as_object().unwrap().clone();
        let tool = step["tool"].as_str().unwrap();
        let rust = mask(&Json::Object(web.execute(tool, &params)), &roots);
        if let Some(expected) =
            params.get("pattern").and_then(Json::as_str).and_then(|pattern| known.get(pattern))
        {
            assert_ne!(&step["result"], expected, "step {index}: the difference is real");
            check(format!("step {index} {tool} (differences row)"), expected, rust);
            shown += 1;
            continue;
        }
        check(format!("step {index} {tool} {}", step["params"]), &step["result"], rust);
    }
    check("files after".into(), &vectors["after"], Json::Array(files(&root)));

    let mut fresh = ToolRegistry::new();
    let shape = |name: &str| json!({"type": "function", "function": {"name": name, "parameters": {"type": "object"}}});
    for (name, result) in [
        ("raises", Err("boom".to_owned())),
        ("text", Ok(json!("x"))),
        ("bare", Ok(json!({"a": 1}))),
        ("failed", Ok(json!({"error": "e", "success": false}))),
    ] {
        fresh.register(
            ToolSpec::from_openai_schema(&shape(name), handler(result), &[ToolsetName::BUILDER_TOOLS])
                .unwrap(),
        );
    }
    for pair in vectors["registry"]["executions"].as_array().unwrap() {
        let name = pair[0].as_str().unwrap();
        check(format!("execute {name}"), &pair[1], Json::Object(fresh.execute(name, &JsonMap::new())));
    }
    let raising = handler(Err("boom".to_owned()));
    let parameter = |name: &str,
                     type_: &str,
                     required: bool,
                     default: Json,
                     enum_values: Json,
                     description: Json| {
        ToolParameter { name: name.into(), type_: type_.into(), required, default, enum_values, description }
    };
    let plain = ToolSpec {
        name: "plain".into(),
        description: json!("Plain tool"),
        parameters: vec![
            parameter("path", "string", true, Json::Null, Json::Null, json!("A path")),
            parameter("tags", "array", false, Json::Null, Json::Null, json!("")),
            parameter("mode", "string", true, json!("a"), json!(["a", "b"]), json!("")),
            parameter("count", "integer", true, json!(3), Json::Null, json!("")),
        ],
        handler: Arc::clone(&raising),
        toolsets: [ToolsetName::BUILDER_TOOLS].into_iter().collect(),
        precondition: None,
        metadata: JsonMap::new(),
        result_validator: None,
        parameters_schema: None,
    };
    let bare = json!({"type": "function", "function": {"name": "bare"}});
    let typed = json!({"type": "function", "function": {"name": "typed", "description": "T", "parameters": {
        "type": "object", "properties": {"n": {"type": "number"}, "o": {}, "x": {"type": "weird"}}, "required": ["n"]}}});
    let typed_spec = ToolSpec::from_openai_schema(&typed, Arc::clone(&raising), &[]).unwrap();
    let conversions = vec![
        tool_spec_to_openai(&plain),
        tool_spec_to_openai(&ToolSpec::from_openai_schema(&bare, Arc::clone(&raising), &[]).unwrap()),
        tool_spec_to_openai(&typed_spec),
    ];
    check("conversions".into(), &vectors["registry"]["conversions"], Json::Array(conversions));
    let parsed: Vec<Json> = typed_spec
        .parameters
        .iter()
        .map(|p| json!([p.name, p.type_, p.required, p.default, p.enum_values, p.description]))
        .collect();
    check("parsed".into(), &vectors["registry"]["parsed"], Json::Array(parsed));

    assert_eq!(shown, known.len(), "every differences-row case ran");
    let _ = std::fs::remove_dir_all(&work);
    let total = vectors["steps"].as_array().unwrap().len();
    assert!(
        failures.is_empty(),
        "{} checks differ ({total} steps):\n{}",
        failures.len(),
        failures.join("\n")
    );
}
