//! The Builder's source, reference, library, Setting and campaign content tools (B6):
//! `_builder_source_tool.py`, `_builder_reference_copy_tool.py`, `_library_source_tool.py`,
//! `_library_source_copy_tool.py`, and `_builder_session.py`'s `_builder_reference_tool`,
//! `_campaign_setting_tool` and `_campaign_content_tools`.
//!
//! Every definition is Python's, exported into `builder_tool_specs.json`. The library and
//! selection work stays with the owner behind the types here ([`SourceSelection`],
//! [`References`], [`LibrarySources`], [`SettingPublisher`], [`CampaignContent`]); each answers
//! Python's own exception classes as a [`DomainError`], and each tool catches what Python's
//! catches. Paging and searches count code points, as Python's `str` does.

use super::tools::{ToolHandler, ToolSpec, ToolsetName};
use super::turn::exported_definition;
use super::{py_family, PyFamily};
use std::path::Path;
use std::sync::Arc;
use sylvia_app::path_guardrail::{platform, PathGuardrail};
use sylvia_app::workspace_files::write_text;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{pystr, CodedResult, Json, JsonMap};
use sylvia_domain::DomainError;

/// `BuilderSourceSelection`: binds this chat to an exact discovered Ruleset or Setting.
pub trait SourceSelection: Send + Sync {
    /// `selection.select(ruleset_id)`.
    fn select(&self, ruleset_id: &str) -> Result<Json, DomainError>;
    /// `selection.select_setting(candidate_id=..., setting_id=...)`.
    fn select_setting(&self, candidate_id: Option<&str>, setting_id: &str) -> Result<Json, DomainError>;
}

/// `references.read(path)`: one reference's text.
pub type ReferenceReader = Arc<dyn Fn(&str) -> Result<String, DomainError> + Send + Sync>;

/// `BuilderReferences`: the chat's fixed source index and its reader.
#[derive(Clone)]
pub struct References {
    pub paths: Vec<String>,
    pub read: ReferenceReader,
}

/// `ReferenceSource`: the chat's references when it has them (Python's fixed value or callable).
pub type ReferenceSource = Arc<dyn Fn() -> Result<Option<References>, DomainError> + Send + Sync>;

/// `LibrarySourceReader`: installed sources the user asked to inspect.
pub trait LibrarySources: Send + Sync {
    fn candidates(&self, query: &str) -> Result<Vec<JsonMap>, DomainError>;
    fn files(&self, candidate_id: &str) -> Result<JsonMap, DomainError>;
    fn read(&self, candidate_id: &str, path: &str) -> Result<JsonMap, DomainError>;
    fn record(&self, candidate_id: &str, prefix: &str) -> Result<JsonMap, DomainError>;
}

/// `SettingPublisher(package_id=..., version=..., title=...)`: publish, or only check, a Setting.
pub type SettingPublisher = Arc<dyn Fn(&str, &str, &str) -> Result<JsonMap, DomainError> + Send + Sync>;

/// `authority.refresh()`: the current catalog, when the owner can refresh it during a turn.
pub type ContentRefresh = Arc<dyn Fn() -> Result<Vec<JsonMap>, DomainError> + Send + Sync>;

/// `CampaignContentToolAuthority(items, refresh)`: the player-safe content catalog.
#[derive(Clone, Default)]
pub struct CampaignContent {
    pub items: Vec<JsonMap>,
    pub refresh: Option<ContentRefresh>,
}

impl CampaignContent {
    /// `current_items()`.
    fn current_items(&self) -> Result<Vec<JsonMap>, DomainError> {
        match &self.refresh {
            Some(refresh) => refresh(),
            None => Ok(self.items.clone()),
        }
    }
}

/// The package reasons a reference failure reports.
const REFERENCE_REASONS: [&str; 4] =
    ["package_file_oversized", "package_unavailable", "package_file_unavailable", "package_corrupt"];

fn object(pairs: Vec<(&str, Json)>) -> JsonMap {
    pairs.into_iter().map(|(key, value)| (key.to_owned(), value)).collect()
}

fn failure(code: &str) -> Json {
    Json::Object(object(vec![("success", Json::Bool(false)), ("code", code.into())]))
}

fn tool(name: &str, handler: ToolHandler) -> CodedResult<ToolSpec> {
    ToolSpec::from_openai_schema(exported_definition(name), handler, &[ToolsetName::BUILDER_TOOLS])
}

/// `params.get(key, default)` as a page bound: an integer (never a boolean) of at least
/// `minimum`.
fn bound(params: &JsonMap, key: &str, default: i64, minimum: i64) -> Option<usize> {
    let value = match params.get(key) {
        None => default,
        Some(Json::Number(number)) if number.is_i64() || number.is_u64() => {
            number.as_i64().unwrap_or(i64::MAX)
        }
        Some(_) => return None,
    };
    (value >= minimum).then(|| usize::try_from(value).unwrap_or(usize::MAX))
}

/// `sequence[offset:end]` for a page whose `end` is `min(len, offset + limit)`.
fn page<T: Clone>(items: &[T], offset: usize, limit: usize) -> (Vec<T>, usize) {
    let end = items.len().min(offset.saturating_add(limit));
    (items[offset.min(end)..end].to_vec(), end)
}

fn next_offset(end: usize, total: usize) -> Json {
    if end < total {
        end.into()
    } else {
        Json::Null
    }
}

/// `str.casefold()`.
fn casefold(text: &str) -> String {
    pystr::casefold(text)
}

/// `str.find(needle, start)` in code points: the first index at or after `start`.
fn find(content: &[char], needle: &[char], start: usize) -> Option<usize> {
    if start > content.len() || needle.len() > content.len() {
        return None;
    }
    (start..=content.len() - needle.len()).find(|&index| content[index..index + needle.len()] == *needle)
}

fn chars_to_string(chars: &[char]) -> String {
    chars.iter().collect()
}

/// `str(path.relative_to(guardrail.allowed_root).as_posix())`.
fn workspace_path(guardrail: &PathGuardrail, target: &Path) -> String {
    platform::relative_parts(&target.to_string_lossy(), guardrail.allowed_root())
        .map(|parts| if parts.is_empty() { ".".to_owned() } else { parts.join("/") })
        .unwrap_or_default()
}

// ── Source selection ───────────────────────────────────────────────────────────────────────

/// The selection refusal (`except CommandRejected`); anything else escapes the handler.
fn selection_refused(error: DomainError) -> Result<Json, String> {
    if py_family(&error) == PyFamily::CommandRejected {
        Ok(Json::Object(object(vec![
            ("success", Json::Bool(false)),
            ("code", "builder_source_selection_rejected".into()),
            ("error", error.message().into()),
        ])))
    } else {
        Err(error.message().to_owned())
    }
}

/// `builder_source_tool(selection)`: `select_builder_ruleset`.
pub fn builder_source_tool(selection: Arc<dyn SourceSelection>) -> CodedResult<ToolSpec> {
    tool(
        "select_builder_ruleset",
        Arc::new(move |params: &JsonMap| {
            let ruleset_id = params.get("ruleset_id").and_then(Json::as_str).filter(|id| !id.is_empty());
            let Some(ruleset_id) = ruleset_id.filter(|_| params.len() == 1) else {
                return Ok(failure("builder_source_parameters_invalid"));
            };
            selection.select(ruleset_id).or_else(selection_refused)
        }),
    )
}

/// `builder_setting_source_tool(selection)`: `select_builder_setting`.
pub fn builder_setting_source_tool(selection: Arc<dyn SourceSelection>) -> CodedResult<ToolSpec> {
    tool(
        "select_builder_setting",
        Arc::new(move |params: &JsonMap| {
            let keys =
                params.len() == 2 && params.contains_key("candidate_id") && params.contains_key("setting_id");
            let setting_id =
                params.get("setting_id").and_then(Json::as_str).filter(|id| !pystr::strip(id).is_empty());
            let candidate = match params.get("candidate_id") {
                Some(Json::Null) | None => Some(None),
                Some(Json::String(id)) if !pystr::strip(id).is_empty() => Some(Some(id.as_str())),
                Some(_) => None,
            };
            let (true, Some(setting_id), Some(candidate_id)) = (keys, setting_id, candidate) else {
                return Ok(failure("builder_source_parameters_invalid"));
            };
            selection.select_setting(candidate_id, setting_id).or_else(selection_refused)
        }),
    )
}

// ── References ─────────────────────────────────────────────────────────────────────────────

/// A failed reference read: `reference_unavailable` (or the copy's code), with the package
/// reason of an `AuthorityError`; `None` when Python lets the error escape.
fn reference_failure(code: &str, error: &DomainError) -> Option<Json> {
    match py_family(error) {
        PyFamily::CommandRejected | PyFamily::Authority => {
            let mut answer = object(vec![("success", Json::Bool(false)), ("code", code.into())]);
            if let Some(reason) = error.code().filter(|reason| REFERENCE_REASONS.contains(reason)) {
                answer.insert("reason".into(), reason.into());
            }
            Some(Json::Object(answer))
        }
        PyFamily::Os | PyFamily::Unicode | PyFamily::Value => Some(failure(code)),
        PyFamily::Type | PyFamily::Other => None,
    }
}

/// `inspect_page(params, references)`.
fn inspect_page(params: &JsonMap, references: &References) -> Result<Json, String> {
    let action = params.get("action").and_then(Json::as_str);
    let default = match action {
        Some("list") => 100,
        Some("find") => 20,
        _ => 8000,
    };
    let (Some(offset), Some(limit)) = (bound(params, "offset", 0, 0), bound(params, "limit", default, 1))
    else {
        return Ok(failure("reference_page_invalid"));
    };
    if action == Some("list") {
        let query = match params.get("query") {
            None => String::new(),
            Some(Json::String(query)) if pystr::len(query) <= 120 => query.clone(),
            Some(_) => return Ok(failure("reference_query_invalid")),
        };
        let folded = casefold(&query);
        let paths: Vec<String> =
            references.paths.iter().filter(|path| casefold(path).contains(&folded)).cloned().collect();
        let (shown, end) = page(&paths, offset, limit.min(100));
        return Ok(Json::Object(object(vec![
            ("success", Json::Bool(true)),
            ("paths", shown.into_iter().map(Json::String).collect()),
            ("total", paths.len().into()),
            ("next_offset", next_offset(end, paths.len())),
        ])));
    }
    let path = params
        .get("path")
        .and_then(Json::as_str)
        .filter(|path| references.paths.iter().any(|known| known == path));
    let (Some(action @ ("read" | "find")), Some(path)) = (action, path) else {
        return Ok(failure("reference_path_unavailable"));
    };
    let query = params.get("query");
    if action == "find"
        && !matches!(query, Some(Json::String(text)) if !text.is_empty() && pystr::len(text) <= 120)
    {
        return Ok(failure("reference_query_invalid"));
    }
    let content: Vec<char> = match (references.read)(path) {
        Ok(text) => text.chars().collect(),
        Err(error) => {
            return reference_failure("reference_unavailable", &error)
                .ok_or_else(|| error.message().to_owned())
        }
    };
    if action == "find" {
        // Literal matches keep the character offsets paged reads use.
        let needle: Vec<char> = query.and_then(Json::as_str).unwrap_or_default().chars().collect();
        let mut matches = Vec::new();
        let mut cursor = offset;
        for _ in 0..limit.min(50) {
            let Some(found) = find(&content, &needle, cursor) else { break };
            let start = found.saturating_sub(160);
            let end = content.len().min(found + needle.len() + 160);
            matches.push(serde_json::json!({
                "offset": found, "excerpt_offset": start, "excerpt": chars_to_string(&content[start..end]),
            }));
            cursor = found + needle.len();
        }
        let next = if find(&content, &needle, cursor).is_some() { cursor.into() } else { Json::Null };
        return Ok(Json::Object(object(vec![
            ("success", Json::Bool(true)),
            ("path", path.into()),
            ("matches", Json::Array(matches)),
            ("next_offset", next),
        ])));
    }
    let (shown, end) = page(&content, offset, limit.min(32000));
    Ok(Json::Object(object(vec![
        ("success", Json::Bool(true)),
        ("path", path.into()),
        ("content", chars_to_string(&shown).into()),
        ("total_characters", content.len().into()),
        ("next_offset", next_offset(end, content.len())),
    ])))
}

/// `read_many`'s pages, each checked before any is read.
fn read_many(params: &JsonMap, references: &References) -> Result<Json, String> {
    let Some(Json::Array(pages)) = params.get("pages") else { return Ok(failure("reference_batch_invalid")) };
    if !(1..=4).contains(&pages.len()) {
        return Ok(failure("reference_batch_invalid"));
    }
    let mut budget = 0;
    let mut checked = Vec::new();
    for page in pages {
        let Json::Object(page) = page else { return Ok(failure("reference_batch_invalid")) };
        if page.keys().any(|key| !matches!(key.as_str(), "path" | "offset" | "limit")) {
            return Ok(failure("reference_batch_invalid"));
        }
        let known = page
            .get("path")
            .and_then(Json::as_str)
            .is_some_and(|path| references.paths.iter().any(|p| p == path));
        let limit = bound(page, "limit", 8000, 1).filter(|limit| *limit <= 32000);
        let (true, Some(_), Some(limit)) = (known, bound(page, "offset", 0, 0), limit) else {
            return Ok(failure("reference_batch_invalid"));
        };
        budget += limit;
        checked.push(page);
    }
    if budget > 32000 {
        return Ok(failure("reference_batch_invalid"));
    }
    let mut results = Vec::new();
    for page in checked {
        let mut request = page.clone();
        request.insert("action".into(), "read".into());
        let result = inspect_page(&request, references)?;
        let succeeded = result.get("success").and_then(Json::as_bool).unwrap_or(false);
        results.push(result.clone());
        if !succeeded {
            let Json::Object(mut answer) = result else { return Ok(result) };
            answer.insert("pages".into(), Json::Array(results));
            return Ok(Json::Object(answer));
        }
    }
    Ok(Json::Object(object(vec![("success", Json::Bool(true)), ("pages", Json::Array(results))])))
}

/// `_builder_reference_tool(source_reader)`: `inspect_builder_references`, paging through the
/// selected snapshots without machine paths or live parent state.
pub fn builder_reference_tool(source: ReferenceSource) -> CodedResult<ToolSpec> {
    tool(
        "inspect_builder_references",
        Arc::new(move |params: &JsonMap| {
            let references = source().map_err(|error| error.message().to_owned())?;
            let Some(references) = references else {
                return Ok(failure("builder_ruleset_selection_required"));
            };
            if params.get("action").and_then(Json::as_str) == Some("read_many") {
                read_many(params, &references)
            } else {
                inspect_page(params, &references)
            }
        }),
    )
}

/// A workspace write's stale refusal, as the copy tools report it.
fn stale(error: &DomainError) -> Option<Json> {
    (error.python_class() == "StaleWorkspaceFileError").then(|| {
        Json::Object(object(vec![
            ("success", Json::Bool(false)),
            ("error_code", "stale_file".into()),
            ("error", error.message().into()),
        ]))
    })
}

/// `builder_reference_copy_tool(source_reader, workspace)`: `copy_builder_reference`, one
/// complete selected reference into an editable workspace file.
pub fn builder_reference_copy_tool(source: ReferenceSource, workspace: &Path) -> CodedResult<ToolSpec> {
    let guardrail = PathGuardrail::new(workspace);
    tool(
        "copy_builder_reference",
        Arc::new(move |params: &JsonMap| {
            let references = source().map_err(|error| error.message().to_owned())?;
            let Some(references) = references else {
                return Ok(failure("builder_ruleset_selection_required"));
            };
            let keys_ok = params
                .keys()
                .all(|key| matches!(key.as_str(), "source_path" | "destination_path" | "expected_version"));
            let source_path = params
                .get("source_path")
                .and_then(Json::as_str)
                .filter(|path| references.paths.iter().any(|p| p == path));
            let destination = params
                .get("destination_path")
                .and_then(Json::as_str)
                .filter(|path| !pystr::strip(path).is_empty());
            let expected = params.get("expected_version");
            let expected_ok = expected.is_none_or(|value| value.is_null() || value.is_string());
            let (true, Some(source_path), Some(destination), true) =
                (keys_ok, source_path, destination, expected_ok)
            else {
                return Ok(failure("reference_copy_parameters_invalid"));
            };
            let Ok(target) = guardrail.validate(destination, true) else {
                return Ok(failure("reference_copy_path_denied"));
            };
            let outcome = (references.read)(source_path).and_then(|content| {
                let version = write_text(&target, &content, expected.and_then(Json::as_str))?;
                Ok((content, version))
            });
            let (content, version) = match outcome {
                Ok(written) => written,
                Err(error) => {
                    if let Some(answer) = stale(&error) {
                        return Ok(answer);
                    }
                    return reference_failure("reference_copy_unavailable", &error)
                        .ok_or_else(|| error.message().to_owned());
                }
            };
            Ok(Json::Object(object(vec![
                ("success", Json::Bool(true)),
                ("source_path", source_path.into()),
                ("path", workspace_path(&guardrail, &target).into()),
                ("bytes_written", content.len().into()),
                ("version", version.into()),
            ])))
        }),
    )
}

// ── Library sources ────────────────────────────────────────────────────────────────────────

/// `except (AuthorityError, OSError, UnicodeError, ValueError)`.
fn library_caught(error: &DomainError) -> bool {
    matches!(
        py_family(error),
        PyFamily::CommandRejected | PyFamily::Authority | PyFamily::Os | PyFamily::Unicode | PyFamily::Value
    )
}

/// `source[key]` of a reader's answer: Python's `KeyError` without it.
fn required<'a>(source: &'a JsonMap, key: &str) -> Result<&'a Json, String> {
    source.get(key).ok_or_else(|| format!("'{key}'"))
}

/// One `inspect_library_sources` call past its parameter checks.
fn inspect_library(
    reader: &dyn LibrarySources,
    params: &JsonMap,
    action: Option<&str>,
    offset: usize,
    limit: usize,
) -> Result<Result<Json, DomainError>, String> {
    if action == Some("candidates") {
        let query = match params.get("query") {
            None => String::new(),
            Some(Json::String(query)) if pystr::len(query) <= 120 => query.clone(),
            Some(_) => return Ok(Ok(failure("library_source_query_invalid"))),
        };
        let candidates = match reader.candidates(&query) {
            Ok(candidates) => candidates,
            Err(error) => return Ok(Err(error)),
        };
        let (shown, end) = page(&candidates, offset, limit.min(20));
        return Ok(Ok(Json::Object(object(vec![
            ("success", Json::Bool(true)),
            ("candidates", shown.into_iter().map(Json::Object).collect()),
            ("total", candidates.len().into()),
            ("next_offset", next_offset(end, candidates.len())),
        ]))));
    }
    let Some(candidate_id) = params.get("candidate_id").and_then(Json::as_str).filter(|id| !id.is_empty())
    else {
        return Ok(Ok(failure("library_source_candidate_required")));
    };
    if action == Some("files") {
        let source = match reader.files(candidate_id) {
            Ok(source) => source,
            Err(error) => return Ok(Err(error)),
        };
        let paths = required(&source, "paths")?.as_array().cloned().unwrap_or_default();
        let (shown, end) = page(&paths, offset, limit.min(100));
        let mut answer = object(vec![("success", Json::Bool(true))]);
        answer.extend(source.iter().filter(|(key, _)| *key != "paths").map(|(k, v)| (k.clone(), v.clone())));
        answer.insert("paths".into(), Json::Array(shown));
        answer.insert("total".into(), paths.len().into());
        answer.insert("next_offset".into(), next_offset(end, paths.len()));
        return Ok(Ok(Json::Object(answer)));
    }
    let path = params.get("path").and_then(Json::as_str);
    let (Some(action @ ("read" | "record")), Some(path)) = (action, path) else {
        return Ok(Ok(failure("library_source_path_unavailable")));
    };
    let source = match if action == "read" {
        reader.read(candidate_id, path)
    } else {
        reader.record(candidate_id, path)
    } {
        Ok(source) => source,
        Err(error) => return Ok(Err(error)),
    };
    let content: Vec<char> = match required(&source, "content")? {
        Json::String(text) => text.chars().collect(),
        other => return Err(format!("object of type '{}' has no len()", type_name(other))),
    };
    let (shown, end) = page(&content, offset, limit.min(32000));
    let mut answer = object(vec![("success", Json::Bool(true))]);
    answer.extend(source.iter().filter(|(key, _)| *key != "content").map(|(k, v)| (k.clone(), v.clone())));
    answer.insert("content".into(), chars_to_string(&shown).into());
    answer.insert("total_characters".into(), content.len().into());
    answer.insert("next_offset".into(), next_offset(end, content.len()));
    Ok(Ok(Json::Object(answer)))
}

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

/// `library_source_tool(reader)`: `inspect_library_sources`, read-only.
pub fn library_source_tool(reader: Arc<dyn LibrarySources>) -> CodedResult<ToolSpec> {
    tool(
        "inspect_library_sources",
        Arc::new(move |params: &JsonMap| {
            let declared = ["action", "candidate_id", "path", "query", "offset", "limit"];
            if params.keys().any(|key| !declared.contains(&key.as_str())) {
                return Ok(failure("library_source_parameters_invalid"));
            }
            let action = params.get("action").and_then(Json::as_str);
            if params.contains_key("query") && action != Some("candidates") {
                return Ok(Json::Object(object(vec![
                    ("success", Json::Bool(false)),
                    ("code", "library_source_query_unsupported".into()),
                    (
                        "message",
                        "query filters candidates only; it does not search source file contents. Use files to list \
                         paths, then read with character offset and limit. For package declarations, use record with \
                         a package prefix."
                            .into(),
                    ),
                ])));
            }
            let default = if matches!(action, Some("candidates" | "files")) { 20 } else { 8000 };
            let (Some(offset), Some(limit)) =
                (bound(params, "offset", 0, 0), bound(params, "limit", default, 1))
            else {
                return Ok(failure("library_source_page_invalid"));
            };
            match inspect_library(reader.as_ref(), params, action, offset, limit)? {
                Ok(answer) => Ok(answer),
                Err(error) if library_caught(&error) => Ok(Json::Object(object(vec![
                    ("success", Json::Bool(false)),
                    ("code", "library_source_unavailable".into()),
                    (
                        "message",
                        "Confirm the source with candidates. Use read with an exact path returned by files, or record \
                         with the ruleset, setting, or dependencies/<id> prefix. Both read and record support \
                         character offset and limit."
                            .into(),
                    ),
                ]))),
                Err(error) => Err(error.message().to_owned()),
            }
        }),
    )
}

/// `library_source_copy_tool(reader, workspace)`: `copy_library_source`, one complete compatible
/// source copied without passing its content through model context.
pub fn library_source_copy_tool(reader: Arc<dyn LibrarySources>, workspace: &Path) -> CodedResult<ToolSpec> {
    let guardrail = PathGuardrail::new(workspace);
    tool(
        "copy_library_source",
        Arc::new(move |params: &JsonMap| {
            let declared = [
                "candidate_id",
                "expected_closure_sha256",
                "source_type",
                "source_path",
                "destination_path",
                "expected_version",
            ];
            let text = |key: &str| params.get(key).and_then(Json::as_str).filter(|value| !value.is_empty());
            let source_type = text("source_type").filter(|kind| matches!(*kind, "file" | "editable_record"));
            let destination = params
                .get("destination_path")
                .and_then(Json::as_str)
                .filter(|path| !pystr::strip(path).is_empty());
            let expected = params.get("expected_version");
            let ok = params.keys().all(|key| declared.contains(&key.as_str()))
                && expected.is_none_or(|value| value.is_null() || value.is_string());
            let (
                true,
                Some(candidate_id),
                Some(expected_closure),
                Some(source_type),
                Some(source_path),
                Some(destination),
            ) = (
                ok,
                text("candidate_id"),
                text("expected_closure_sha256"),
                source_type,
                text("source_path"),
                destination,
            )
            else {
                return Ok(failure("library_source_copy_parameters_invalid"));
            };
            let Ok(target) = guardrail.validate(destination, true) else {
                return Ok(failure("library_source_copy_path_denied"));
            };
            let unavailable = || Ok(failure("library_source_copy_unavailable"));
            let source = match if source_type == "file" {
                reader.read(candidate_id, source_path)
            } else {
                reader.record(candidate_id, source_path)
            } {
                Ok(source) => source,
                Err(error) if library_caught(&error) => return unavailable(),
                Err(error) => return Err(error.message().to_owned()),
            };
            let actual_closure = source.get("closure_sha256").cloned().unwrap_or(Json::Null);
            if actual_closure.as_str() != Some(expected_closure) {
                return Ok(Json::Object(object(vec![
                    ("success", Json::Bool(false)),
                    ("code", "library_source_copy_changed".into()),
                    ("candidate_id", candidate_id.into()),
                    ("closure_sha256", actual_closure),
                ])));
            }
            let (Some(Json::String(content)), Some(Json::Object(package))) =
                (source.get("content"), source.get("package"))
            else {
                return unavailable();
            };
            let content = if source_type == "editable_record" {
                // Local search and read tools address lines: keep record data in an editable layout.
                let Ok(parsed) = pyjson::loads(content) else { return unavailable() };
                let mut text = pyjson::dumps(&parsed, Dump::INDENT2.ascii(false));
                text.push('\n');
                text
            } else {
                content.clone()
            };
            let version = match write_text(&target, &content, expected.and_then(Json::as_str)) {
                Ok(version) => version,
                Err(error) => {
                    if let Some(answer) = stale(&error) {
                        return Ok(answer);
                    }
                    return if library_caught(&error) {
                        unavailable()
                    } else {
                        Err(error.message().to_owned())
                    };
                }
            };
            Ok(Json::Object(object(vec![
                ("success", Json::Bool(true)),
                ("candidate_id", candidate_id.into()),
                ("closure_sha256", actual_closure),
                ("source_type", source_type.into()),
                ("source_path", source_path.into()),
                ("package", Json::Object(package.clone())),
                ("path", workspace_path(&guardrail, &target).into()),
                ("bytes_written", content.len().into()),
                ("version", version.into()),
            ])))
        }),
    )
}

// ── Setting and campaign content ───────────────────────────────────────────────────────────

/// The diagnostic fields a Setting refusal reports.
const SETTING_DIAGNOSTIC_KEYS: [&str; 6] = ["code", "phase", "message", "file", "path", "guidance"];

/// `_campaign_setting_tool(publisher, checking=...)`: `publish_campaign_setting`, or
/// `check_campaign_setting_draft`, sharing the Setting's parameters.
pub fn campaign_setting_tool(publisher: SettingPublisher, checking: bool) -> CodedResult<ToolSpec> {
    let name = if checking { "check_campaign_setting_draft" } else { "publish_campaign_setting" };
    tool(
        name,
        Arc::new(move |params: &JsonMap| {
            let invalid = || {
                Ok(Json::Object(object(vec![
                    ("success", Json::Bool(false)),
                    ("status", "rejected".into()),
                    ("code", "campaign_setting_invalid".into()),
                ])))
            };
            let keys = params.len() == 3
                && ["package_id", "version", "title"].iter().all(|key| params.contains_key(*key));
            if !keys {
                return invalid();
            }
            let field = |key: &str| {
                params.get(key).and_then(Json::as_str).map(pystr::strip).filter(|value| !value.is_empty())
            };
            let (Some(package_id), Some(version), Some(title)) =
                (field("package_id"), field("version"), field("title"))
            else {
                return invalid();
            };
            match publisher(package_id, version, title) {
                Ok(result) => {
                    let mut answer = object(vec![("success", Json::Bool(true))]);
                    if checking {
                        answer.insert("status".into(), "valid_draft".into());
                    }
                    answer.extend(result);
                    Ok(Json::Object(answer))
                }
                Err(error)
                    if matches!(
                        py_family(&error),
                        PyFamily::CommandRejected | PyFamily::Value | PyFamily::Unicode
                    ) =>
                {
                    let diagnostics = error
                        .diagnostics()
                        .iter()
                        .take(8)
                        .map(|diagnostic| {
                            Json::Object(
                                diagnostic
                                    .as_payload()
                                    .into_iter()
                                    .filter(|(key, _)| SETTING_DIAGNOSTIC_KEYS.contains(&key.as_str()))
                                    .collect(),
                            )
                        })
                        .collect();
                    let message: String = error.message().chars().take(240).collect();
                    Ok(Json::Object(object(vec![
                        ("success", Json::Bool(false)),
                        ("status", "rejected".into()),
                        ("code", error.code().unwrap_or("campaign_setting_invalid").into()),
                        ("message", message.into()),
                        ("validation_errors", Json::Array(diagnostics)),
                    ])))
                }
                Err(error) => Err(error.message().to_owned()),
            }
        }),
    )
}

/// `_campaign_content_tools(authority)`: `discover_campaign_content` and the non-mutating
/// `prepare_campaign_setup` handoff.
pub fn campaign_content_tools(authority: CampaignContent) -> CodedResult<Vec<ToolSpec>> {
    let discover_authority = authority.clone();
    let discover = tool(
        "discover_campaign_content",
        Arc::new(move |params: &JsonMap| {
            let rejected = || {
                Ok(Json::Object(object(vec![
                    ("success", Json::Bool(false)),
                    ("status", "rejected".into()),
                    ("code", "campaign_content_query_invalid".into()),
                ])))
            };
            if params.keys().any(|key| key != "query") {
                return rejected();
            }
            let query = match params.get("query") {
                None => String::new(),
                Some(Json::String(query)) if pystr::len(query) <= 160 => query.clone(),
                Some(_) => return rejected(),
            };
            // `query.casefold().split()`: runs of Unicode whitespace separate the terms.
            let folded = casefold(&query);
            let terms: Vec<&str> = folded.split(pystr::is_space).filter(|term| !term.is_empty()).collect();
            let items = discover_authority.current_items().map_err(|error| error.message().to_owned())?;
            let matches: Vec<Json> = items
                .into_iter()
                .filter(|item| {
                    let searchable = casefold(&pyjson::dumps(
                        &Json::Object(item.clone()),
                        Dump::DEFAULT.ascii(false).sorted(true),
                    ));
                    terms.iter().all(|term| searchable.contains(term))
                })
                .map(Json::Object)
                .collect();
            Ok(Json::Object(object(vec![
                ("success", Json::Bool(true)),
                ("status", if matches.is_empty() { "no_matches" } else { "available" }.into()),
                ("query", pystr::strip(&query).into()),
                ("items", Json::Array(matches)),
            ])))
        }),
    )?;
    let prepare = tool(
        "prepare_campaign_setup",
        Arc::new(move |params: &JsonMap| {
            let rejected = |code: &str| {
                Ok(Json::Object(object(vec![
                    ("success", Json::Bool(false)),
                    ("status", "rejected".into()),
                    ("code", code.into()),
                ])))
            };
            let plan_id = params.get("campaign_plan_id").and_then(Json::as_str).filter(|id| !id.is_empty());
            let Some(plan_id) = plan_id.filter(|_| params.len() == 1) else {
                return rejected("campaign_setup_selection_invalid");
            };
            let items = authority.current_items().map_err(|error| error.message().to_owned())?;
            let plan = items.into_iter().find(|item| {
                item.get("kind").and_then(Json::as_str) == Some("campaign_plan")
                    && item.get("id").and_then(Json::as_str) == Some(plan_id)
            });
            let Some(plan) = plan else { return rejected("campaign_plan_unavailable") };
            let mut prepared = object(vec![
                ("success", Json::Bool(true)),
                ("status", "setup_ready".into()),
                ("campaign_plan_id", plan_id.into()),
                ("title", plan.get("title").cloned().unwrap_or_else(|| plan_id.into())),
                ("player_character_required", Json::Bool(true)),
                (
                    "message",
                    "Use New Campaign to confirm the required Player Character and other player choices. Gameplay has \
                     not started."
                        .into(),
                ),
            ]);
            if let Some(setup_id) = plan.get("setup_id").and_then(Json::as_str).filter(|id| !id.is_empty()) {
                prepared.insert("setup_id".into(), setup_id.into());
            }
            Ok(Json::Object(prepared))
        }),
    )?;
    Ok(vec![discover, prepare])
}
