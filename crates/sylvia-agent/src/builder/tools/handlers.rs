//! `tools/_builder_handlers.py` and the runner's `_wrap_builder_handler`: the Builder's
//! workspace file tools and its question widget.
//!
//! Paths in results and errors are Python's `str(Path)` on this platform (through
//! `sylvia_app::path_guardrail::platform`), because the model reads them. The wrapper then
//! projects a result's `path` relative to the workspace and writes `[workspace]` for the
//! workspace root in an error, as Python does.
//!
//! `web_search` checks its query and limit as Python does; its request is not ported yet (B2c),
//! so it answers `rust_port_pending` in the tool result.

use super::glob::rglob;
use super::pattern::SearchPattern;
use super::spec::{ToolHandler, ToolSpec, ToolsetName};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use sylvia_app::builder_tools::{
    builder_tool_definitions, interview_tool_definitions, public_web_tool_definition,
};
use sylvia_app::path_guardrail::{platform, PathGuardrail};
use sylvia_app::workspace_files;
use sylvia_base::pystr::{repr, splitlines, splitlines_keepends};
use sylvia_base::{CodedResult, Json, JsonMap};
use sylvia_domain::pyops::py_truthy;

/// The paths the wrapper gives a handler: `params["_resolved_path"]` and `params["_project_root"]`.
#[derive(Debug, Clone)]
pub struct HandlerPaths {
    pub resolved: Option<String>,
    pub project_root: String,
}

type Inner = fn(&JsonMap, &HandlerPaths) -> Result<Json, String>;

/// The Builder's workspace: its resolved root and guardrail.
#[derive(Debug, Clone)]
pub struct Workspace {
    project_root: String,
    guardrail: Option<PathGuardrail>,
}

impl Workspace {
    /// `Path(project_root).resolve()`, with the guardrail when it is enabled.
    pub fn new(project_root: &Path, guardrail_enabled: bool) -> Self {
        let guardrail = PathGuardrail::new(project_root);
        let project_root = guardrail.allowed_root().to_owned();
        Self { project_root, guardrail: guardrail_enabled.then_some(guardrail) }
    }

    /// `str(self._project_root)`.
    pub fn root(&self) -> &str {
        &self.project_root
    }
}

fn map(pairs: Vec<(&str, Json)>) -> Json {
    Json::Object(pairs.into_iter().map(|(key, value)| (key.to_owned(), value)).collect())
}

fn failure(error: String) -> Json {
    map(vec![("success", Json::Bool(false)), ("error", Json::from(error))])
}

fn coded(code: &str, error: String) -> Json {
    map(vec![("success", Json::Bool(false)), ("error_code", Json::from(code)), ("error", Json::from(error))])
}

/// `type(value).__name__` for a tool argument.
fn type_name(value: &Json) -> &'static str {
    match value {
        Json::Null => "NoneType",
        Json::Bool(_) => "bool",
        Json::Number(number) if number.is_f64() => "float",
        Json::Number(_) => "int",
        Json::String(_) => "str",
        Json::Array(_) => "list",
        Json::Object(_) => "dict",
    }
}

/// The OSError text Python prints for a failed file operation: `[Errno N] text: 'path'`.
fn os_error(error: &std::io::Error, path: &str) -> String {
    use std::io::ErrorKind;
    let (errno, text) = match error.kind() {
        ErrorKind::NotFound => (2, "No such file or directory".to_owned()),
        ErrorKind::PermissionDenied => (13, "Permission denied".to_owned()),
        ErrorKind::AlreadyExists => (17, "File exists".to_owned()),
        ErrorKind::NotADirectory => (20, "Not a directory".to_owned()),
        ErrorKind::IsADirectory => (21, "Is a directory".to_owned()),
        _ => return format!("{error}: {}", repr(path)),
    };
    format!("[Errno {errno}] {text}: {}", repr(path))
}

/// A Python number from a tool argument: an `int` (a `bool` counts) or a `float`.
#[derive(Debug, Clone, Copy, PartialEq)]
enum Number {
    Int(i128),
    Float(f64),
}

impl Number {
    fn of(value: &Json) -> Option<Self> {
        match value {
            Json::Bool(flag) => Some(Self::Int(i128::from(*flag))),
            Json::Number(number) => match number.as_i64() {
                Some(int) => Some(Self::Int(i128::from(int))),
                None => match number.as_u64() {
                    Some(int) => Some(Self::Int(i128::from(int))),
                    None => number.as_f64().map(Self::Float),
                },
            },
            _ => None,
        }
    }

    fn float(self) -> f64 {
        match self {
            Self::Int(int) => int as f64,
            Self::Float(float) => float,
        }
    }

    fn less(self, other: Self) -> bool {
        match (self, other) {
            (Self::Int(a), Self::Int(b)) => a < b,
            (a, b) => a.float() < b.float(),
        }
    }

    fn add(self, other: Self) -> Self {
        match (self, other) {
            (Self::Int(a), Self::Int(b)) => Self::Int(a + b),
            (a, b) => Self::Float(a.float() + b.float()),
        }
    }
}

/// `min(value, bound)` with Python's comparison error for a non-number.
fn py_min(value: &Json, bound: i128) -> Result<Number, String> {
    let number = Number::of(value)
        .ok_or_else(|| format!("'<' not supported between instances of 'int' and '{}'", type_name(value)))?;
    Ok(if Number::Int(bound).less(number) { Number::Int(bound) } else { number })
}

/// `max(0, value - 1)` with Python's arithmetic error for a non-number.
fn start_of(value: &Json) -> Result<Number, String> {
    let number = Number::of(value)
        .ok_or_else(|| format!("unsupported operand type(s) for -: '{}' and 'int'", type_name(value)))?;
    let shifted = number.add(Number::Int(-1));
    Ok(if Number::Int(0).less(shifted) { shifted } else { Number::Int(0) })
}

/// `list[start:end]` bounds, for integer `start >= 0` and any integer `end`.
fn slice_bounds(length: usize, start: i128, end: i128) -> (usize, usize) {
    let length = length as i128;
    let end = if end < 0 { (end + length).max(0) } else { end.min(length) };
    let start = start.min(length);
    (start as usize, end.max(start) as usize)
}

const SLICE_ERROR: &str = "slice indices must be integers or None or have an __index__ method";

fn resolved(paths: &HandlerPaths) -> Result<&str, String> {
    paths.resolved.as_deref().ok_or_else(|| "'_resolved_path'".to_owned())
}

/// `tool_read_file`: a file's lines, numbered.
pub fn tool_read_file(params: &JsonMap, paths: &HandlerPaths) -> Result<Json, String> {
    let file = resolved(paths)?;
    let offset = params.get("offset").cloned().unwrap_or(Json::from(1));
    let limit = py_min(&params.get("limit").cloned().unwrap_or(Json::from(500)), 2000)?;
    let data = match std::fs::read(file) {
        Ok(data) => data,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return Ok(map(vec![
                ("success", Json::Bool(false)),
                ("error", Json::from(format!("File not found: {file}"))),
                ("version", Json::from(workspace_files::MISSING_VERSION)),
            ]))
        }
        Err(error) => return Ok(failure(format!("Error reading file: {}", os_error(&error, file)))),
    };
    let binary = || failure(format!("Cannot read binary file: {file}"));
    if data[..data.len().min(1024)].contains(&0) {
        return Ok(binary());
    }
    let Ok(text) = std::str::from_utf8(&data) else { return Ok(binary()) };
    let lines = splitlines_keepends(text);
    let total = lines.len();
    let start = match start_of(&offset) {
        Ok(start) => start,
        Err(error) => return Ok(failure(format!("Error reading file: {error}"))),
    };
    let sum = start.add(limit);
    let end = if sum.less(Number::Int(total as i128)) { sum } else { Number::Int(total as i128) };
    let (Number::Int(start), Number::Int(end)) = (start, end) else {
        return Ok(failure(format!("Error reading file: {SLICE_ERROR}")));
    };
    let (from, to) = slice_bounds(total, start, end);
    let selected = &lines[from..to];
    let content: String = selected.concat();
    let length = content.chars().count();
    if length > 100_000 {
        return Ok(failure(format!(
            "File too large ({length} chars). Use offset/limit to read smaller chunks. Total lines: {total}."
        )));
    }
    let numbered: String =
        selected.iter().enumerate().map(|(index, line)| format!("{:6}|{line}", from + index + 1)).collect();
    Ok(map(vec![
        ("success", Json::Bool(true)),
        ("content", Json::from(numbered)),
        ("path", Json::from(file)),
        ("lines", Json::from(selected.len())),
        ("total_lines", Json::from(total)),
        ("version", Json::from(workspace_files::content_version(&data))),
    ]))
}

/// `expected_version` as `_check_version` reads it: a falsy value is "missing", any other
/// non-text value matches no version.
fn expected_version(params: &JsonMap) -> Option<String> {
    match params.get("expected_version") {
        Some(Json::String(version)) => Some(version.clone()),
        Some(value) if py_truthy(value) => Some("\u{0}not a version".to_owned()),
        _ => None,
    }
}

fn workspace_error(error: &sylvia_domain::DomainError, prefix: &str) -> Json {
    match error.python_class() {
        "StaleWorkspaceFileError" => coded("stale_file", error.message().to_owned()),
        "LiteralMatchError" => {
            let code = if error.message().starts_with("old_text was not found") {
                "text_not_found"
            } else {
                "text_not_unique"
            };
            coded(code, error.message().to_owned())
        }
        _ => failure(format!("{prefix}: {}", error.message())),
    }
}

fn is_dir(path: &str) -> bool {
    std::fs::metadata(path).is_ok_and(|metadata| metadata.is_dir())
}

fn is_file(path: &Path) -> bool {
    std::fs::metadata(path).is_ok_and(|metadata| metadata.is_file())
}

fn exists(path: &str) -> bool {
    std::fs::metadata(path).is_ok()
}

/// `tool_write_file`: an atomic save, refused over an outdated file version.
pub fn tool_write_file(params: &JsonMap, paths: &HandlerPaths) -> Result<Json, String> {
    let file = resolved(paths)?;
    let content = match params.get("content") {
        None | Some(Json::Null) => return Ok(failure("content must be a non-empty string, got null".into())),
        Some(Json::String(content)) if !content.is_empty() => content,
        Some(_) => return Ok(failure("content must be a non-empty string".into())),
    };
    if exists(file) && is_dir(file) {
        return Ok(failure(format!("Cannot write to a directory: {file}")));
    }
    match workspace_files::write_text(Path::new(file), content, expected_version(params).as_deref()) {
        Ok(version) => Ok(map(vec![
            ("success", Json::Bool(true)),
            ("bytes_written", Json::from(content.len())),
            ("path", Json::from(file)),
            ("version", Json::from(version)),
        ])),
        Err(error) => Ok(workspace_error(&error, "Error writing file")),
    }
}

/// `tool_edit_file`: one unique literal replaced in an observed file.
pub fn tool_edit_file(params: &JsonMap, paths: &HandlerPaths) -> Result<Json, String> {
    let file = resolved(paths)?;
    let Some(Json::String(old_text)) =
        params.get("old_text").filter(|text| text.as_str().is_some_and(|t| !t.is_empty()))
    else {
        return Ok(coded("invalid_edit", "old_text must be a non-empty string".into()));
    };
    let Some(Json::String(new_text)) = params.get("new_text") else {
        return Ok(coded("invalid_edit", "new_text must be a string".into()));
    };
    let Some(Json::String(expected)) =
        params.get("expected_version").filter(|v| v.as_str().is_some_and(|v| !v.is_empty()))
    else {
        return Ok(coded(
            "stale_file",
            "Read the file first and pass its version as expected_version.".into(),
        ));
    };
    match workspace_files::edit_text(Path::new(file), old_text, new_text, Some(expected)) {
        Ok((version, written)) => Ok(map(vec![
            ("success", Json::Bool(true)),
            ("bytes_written", Json::from(written)),
            ("path", Json::from(file)),
            ("version", Json::from(version)),
        ])),
        Err(error) => Ok(workspace_error(&error, "Error editing file")),
    }
}

/// `tool_delete_file`: only the observed version is deleted.
pub fn tool_delete_file(params: &JsonMap, paths: &HandlerPaths) -> Result<Json, String> {
    let file = resolved(paths)?;
    if !exists(file) {
        return Ok(failure(format!("File not found: {file}")));
    }
    if is_dir(file) {
        return Ok(failure(format!("Cannot delete a directory: {file}")));
    }
    match workspace_files::delete_file(Path::new(file), expected_version(params).as_deref()) {
        Ok(()) => Ok(map(vec![("success", Json::Bool(true)), ("path", Json::from(file))])),
        Err(error) => Ok(workspace_error(&error, "Error deleting file")),
    }
}

/// `str(path.relative_to(base))`, lexically, with the platform's separator.
fn relative(path: &Path, base: &Path) -> String {
    let separator = if cfg!(windows) { "\\" } else { "/" };
    let parts: Vec<String> = path
        .strip_prefix(base)
        .map(|rest| rest.components().map(|part| part.as_os_str().to_string_lossy().into_owned()).collect())
        .unwrap_or_default();
    if parts.is_empty() {
        ".".to_owned()
    } else {
        parts.join(separator)
    }
}

const PURGE_PENDING: &str = ".builder-sources-purge-pending";

/// `tool_search_files`: files by glob, or lines by regular expression.
pub fn tool_search_files(params: &JsonMap, paths: &HandlerPaths) -> Result<Json, String> {
    let search = paths.resolved.clone().unwrap_or_else(|| paths.project_root.clone());
    let root = paths.project_root.clone();
    let star = Json::from("*");
    let pattern = params.get("pattern").unwrap_or(&star);
    let files_target = Json::from("files");
    let files = params.get("target").unwrap_or(&files_target) == &files_target;
    let owner_internal = |path: &Path| {
        let text = path.to_string_lossy();
        platform::relative_parts(&text, &root)
            .and_then(|parts| parts.first().map(|first| sylvia_base::pystr::casefold(first) == PURGE_PENDING))
            .unwrap_or(false)
    };
    let search_path = PathBuf::from(&search);
    let error = |message: String| Ok(failure(format!("Error searching files: {message}")));
    if !exists(&search) {
        return Ok(failure(format!("Search path not found: {search}")));
    }
    if files {
        let pattern = match pattern {
            Json::String(pattern) => pattern.as_str(),
            // `_parse_path` reads any falsy pattern as no parts.
            other if !py_truthy(other) => "",
            other if cfg!(windows) => {
                return error(format!("'{}' object has no attribute 'replace'", type_name(other)))
            }
            other => {
                return error(format!("expected str, bytes or os.PathLike object, not {}", type_name(other)))
            }
        };
        let found = match rglob(&search_path, pattern) {
            Ok(found) => found,
            Err(failure) => return error(failure.to_string()),
        };
        let mut matches: Vec<String> = found
            .iter()
            .filter(|path| is_file(path) && !owner_internal(path))
            .map(|path| relative(path, &search_path))
            .collect();
        matches.sort();
        let count = matches.len();
        matches.truncate(50);
        return Ok(map(vec![
            ("success", Json::Bool(true)),
            ("matches", Json::Array(matches.into_iter().map(Json::from).collect())),
            ("count", Json::from(count)),
        ]));
    }
    let Json::String(pattern) = pattern else {
        return error("first argument must be string or compiled pattern".into());
    };
    let regex = match SearchPattern::new(pattern) {
        Ok(regex) => regex,
        Err(failure) => return Ok(self::failure(format!("Invalid regex pattern: {failure}"))),
    };
    let single = is_file(&search_path);
    let candidates = if single {
        vec![search_path.clone()]
    } else {
        match rglob(&search_path, "*") {
            Ok(found) => found,
            Err(failure) => return error(failure.to_string()),
        }
    };
    let result_root = if single { PathBuf::from(&root) } else { search_path.clone() };
    let mut results: Vec<Json> = Vec::new();
    let mut skipped = 0usize;
    for file in candidates {
        if !is_file(&file) || owner_internal(&file) {
            continue;
        }
        let size = std::fs::metadata(&file).map(|metadata| metadata.len()).unwrap_or(0);
        if size > sylvia_domain::package_archive::PACKAGE_MEMBER_BYTE_LIMIT {
            if single {
                return Ok(coded(
                    "file_oversized",
                    "The selected file exceeds the supported package file size.".into(),
                ));
            }
            skipped += 1;
            continue;
        }
        let Ok(data) = std::fs::read(&file) else { continue };
        let Ok(text) = String::from_utf8(data) else { continue };
        for (index, line) in splitlines(&text).into_iter().enumerate() {
            if regex.search(line) {
                let length = line.chars().count();
                let content: String = line.chars().take(200).collect();
                results.push(map(vec![
                    ("file", Json::from(relative(&file, &result_root))),
                    ("line", Json::from(index + 1)),
                    ("content", Json::from(content)),
                    ("content_complete", Json::Bool(length <= 200)),
                ]));
                if results.len() >= 100 {
                    break;
                }
            }
        }
        if results.len() >= 100 {
            break;
        }
    }
    let count = results.len();
    Ok(map(vec![
        ("success", Json::Bool(true)),
        ("matches", Json::Array(results)),
        ("count", Json::from(count)),
        ("skipped_oversized_files", Json::from(skipped)),
    ]))
}

fn get<'a>(params: &'a JsonMap, key: &str, default: &'a Json) -> &'a Json {
    params.get(key).unwrap_or(default)
}

/// `tool_ask_question`: a serializable question awaiting the player's answer.
pub fn tool_ask_question(params: &JsonMap, _paths: &HandlerPaths) -> Result<Json, String> {
    let (select, empty, empty_list) = (Json::from("select"), Json::from(""), Json::Array(Vec::new()));
    let kind = get(params, "type", &select).clone();
    let id = get(params, "id", &empty).clone();
    let awaiting = |kind: Json, data: Json| {
        map(vec![
            ("success", Json::Bool(true)),
            ("status", Json::from("awaiting_answer")),
            ("widget", map(vec![("type", kind), ("data", data)])),
        ])
    };
    if kind == "cascade" {
        let question = get(params, "question", &empty).clone();
        let options = get(params, "options", &empty_list).clone();
        let label = get(params, "action_label", &Json::from("Choose")).clone();
        let style = get(params, "action_style", &Json::from("primary")).clone();
        if !py_truthy(&question) {
            return Ok(failure("question is required for cascade widget type".into()));
        }
        let options = if py_truthy(&options) { options } else { Json::Array(Vec::new()) };
        let data = map(vec![
            ("question", question),
            ("id", id),
            ("options", options),
            ("action_label", label),
            ("action_style", style),
        ]);
        return Ok(awaiting(Json::from("cascade"), data));
    }
    for (name, summary) in
        [("complete", "Your campaign is ready!"), ("plan_complete", "Campaign plan written!")]
    {
        if kind == name {
            let data = map(vec![
                ("summary", get(params, "summary", &Json::from(summary)).clone()),
                ("campaign_id", get(params, "campaign_id", &empty).clone()),
                ("id", id),
            ]);
            return Ok(awaiting(Json::from(name), data));
        }
    }
    let question = get(params, "question", &empty).clone();
    if !py_truthy(&question) {
        return Ok(failure("question is required for select/text/confirm widget types".into()));
    }
    let mut data = vec![("question", question), ("id", id)];
    if kind == "select" || kind == "confirm" {
        let options = get(params, "options", &empty_list).clone();
        if !py_truthy(&options) {
            let name = kind.as_str().unwrap_or_default();
            return Ok(failure(format!("options list is required for '{name}' widget type")));
        }
        data.push(("options", options));
    }
    Ok(awaiting(kind, map(data)))
}

/// `tool_web_search`'s argument checks; the request itself is not ported yet (B2c).
pub fn tool_web_search(params: &JsonMap, _paths: &HandlerPaths) -> Result<Json, String> {
    let query = params.get("query");
    let limit = params.get("limit").cloned().unwrap_or(Json::from(5));
    if !matches!(query, Some(Json::String(text)) if !sylvia_base::pystr::strip(text).is_empty()) {
        return Ok(failure("Web search query must be nonempty text".into()));
    }
    if !matches!(&limit, Json::Number(number) if number.as_i64().is_some_and(|limit| (1..=10).contains(&limit)))
    {
        return Ok(failure("Web search limit must be an integer from 1 to 10".into()));
    }
    Ok(coded("rust_port_pending", "not yet ported: web_search".into()))
}

const FILE_TOOLS: [&str; 5] = ["read_file", "write_file", "edit_file", "search_files", "delete_file"];

/// `_wrap_builder_handler(handler, tool_name)`: the guardrail and path resolution before the
/// handler, and the workspace projection of its result.
fn wrap(workspace: Arc<Workspace>, name: &'static str, inner: Inner) -> ToolHandler {
    Arc::new(move |params: &JsonMap| {
        let raw =
            if FILE_TOOLS.contains(&name) { params.get("path").filter(|path| py_truthy(path)) } else { None };
        let root = workspace.root();
        let mut resolved = None;
        if let Some(raw) = raw {
            let Json::String(raw) = raw else {
                return Err(format!(
                    "argument should be a str or an os.PathLike object where __fspath__ returns a str, not '{}'",
                    type_name(raw)
                ));
            };
            match &workspace.guardrail {
                Some(guardrail) => {
                    let for_write = matches!(name, "write_file" | "edit_file" | "delete_file");
                    match guardrail.validate(raw, for_write) {
                        Ok(path) => resolved = Some(path.to_string_lossy().into_owned()),
                        Err(error) => return Ok(failure(error.to_string())),
                    }
                }
                None => {
                    let joined =
                        if platform::is_absolute(raw) { raw.clone() } else { platform::join(root, raw) };
                    resolved = Some(platform::resolve(&joined));
                }
            }
        } else if name == "search_files" {
            resolved = Some(root.to_owned());
        }
        let paths = HandlerPaths { resolved, project_root: root.to_owned() };
        let result = inner(params, &paths)?;
        let Json::Object(mut projected) = result else { return Ok(result) };
        if let Some(Json::String(path)) = projected.get("path").cloned() {
            let absolute =
                if platform::is_absolute(&path) { path } else { platform::join(&platform::cwd(), &path) };
            match platform::relative_parts(&platform::resolve(&absolute), root) {
                Some(parts) if parts.is_empty() => {
                    projected.insert("path".into(), Json::from("."));
                }
                Some(parts) => {
                    projected.insert("path".into(), Json::from(parts.join("/")));
                }
                None => {
                    projected.shift_remove("path");
                }
            }
        }
        if let Some(Json::String(error)) = projected.get("error").cloned() {
            projected.insert("error".into(), Json::from(error.replace(root, "[workspace]")));
        }
        Ok(Json::Object(projected))
    })
}

fn inner(name: &str) -> CodedResult<Inner> {
    Ok(match name {
        "read_file" => tool_read_file,
        "write_file" => tool_write_file,
        "edit_file" => tool_edit_file,
        "search_files" => tool_search_files,
        "delete_file" => tool_delete_file,
        "ask_question" => tool_ask_question,
        "web_search" => tool_web_search,
        other => {
            return Err(sylvia_base::CodedError::rejected(format!("'{other}'")).with_error_type("KeyError"))
        }
    })
}

fn static_name(name: &str) -> &'static str {
    ["read_file", "write_file", "edit_file", "search_files", "delete_file", "ask_question", "web_search"]
        .into_iter()
        .find(|known| *known == name)
        .unwrap_or("")
}

/// The constructor's Builder tools: `BUILDER_TOOL_DEFINITIONS` (and the public web search when
/// allowed), wrapped, then `INTERVIEW_TOOL_DEFINITIONS` unwrapped, in Python's order.
pub fn builder_workspace_tools(
    workspace: &Arc<Workspace>,
    allow_public_web: bool,
) -> CodedResult<Vec<ToolSpec>> {
    let mut definitions: Vec<&Json> = builder_tool_definitions().iter().collect();
    if allow_public_web {
        definitions.push(public_web_tool_definition());
    }
    let name_of = |definition: &Json| definition["function"]["name"].as_str().unwrap_or_default().to_owned();
    let mut specs = Vec::new();
    for definition in definitions {
        let name = name_of(definition);
        let handler = wrap(Arc::clone(workspace), static_name(&name), inner(&name)?);
        specs.push(ToolSpec::from_openai_schema(definition, handler, &[ToolsetName::BUILDER_TOOLS])?);
    }
    for definition in interview_tool_definitions() {
        let name = name_of(definition);
        let handler = inner(&name)?;
        let root = workspace.root().to_owned();
        let plain: ToolHandler = Arc::new(move |params: &JsonMap| {
            handler(params, &HandlerPaths { resolved: None, project_root: root.clone() })
        });
        specs.push(ToolSpec::from_openai_schema(definition, plain, &[ToolsetName::INTERVIEW_TOOLS])?);
    }
    Ok(specs)
}
