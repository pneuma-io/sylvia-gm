//! The Builder's Plan, import repair and subflow tools (B7): `_builder_session.py`'s
//! `_campaign_plan_example`, `_campaign_source_tool`, `_campaign_plan_diagnostic`,
//! `_campaign_plan_tool`, `_campaign_plan_draft_tool`, `_campaign_plan_tools` and
//! `_creation_subflow_completion_tool`, `_plan_draft_check_tool.py` and `_import_repair_tools.py`.
//!
//! The Plan's exact package closure and Setting reader come from the owner as a [`PlanAuthority`]
//! (Python's `CampaignPlanToolAuthority`), fixed or chosen later ([`PlanAuthoritySource`]). The
//! publication, check and repair callbacks answer Python's classes as a [`DomainError`]. Every
//! definition is Python's: the Plan tools' from `sylvia_app::builder_tools`, the others from
//! `builder_tool_specs.json`.

use super::skills::load_builder_skill;
use super::tools::{ToolHandler, ToolSpec, ToolsetName};
use super::turn::exported_definition;
use super::{py_family, PyFamily};
use std::collections::BTreeSet;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use sylvia_app::builder_tools::{
    campaign_plan_tool_definition, campaign_source_tool_definition,
    create_campaign_plan_draft_tool_definition,
};
use sylvia_app::campaign_plan_materializer::{
    publish_campaign_plan_draft, CampaignPlanDraft, PlanDraftExpectations,
};
use sylvia_base::hash::sha256_hex;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{pystr, CodedResult, Json, JsonMap};
use sylvia_domain::campaign_plan::PackageBinding;
use sylvia_domain::error::RulesetDiagnostic;
use sylvia_domain::package_library::canonical_json_bytes;
use sylvia_domain::DomainError;

/// `authority.read_setting(path)`: one Setting file's text.
pub type SettingReader = Arc<dyn Fn(&str) -> Result<String, DomainError> + Send + Sync>;

/// `CampaignPlanToolAuthority`: the exact package closure and bounded Setting reader of one
/// campaign Builder chat.
#[derive(Clone)]
pub struct PlanAuthority {
    pub ruleset: PackageBinding,
    pub setting: PackageBinding,
    pub closure_sha256: String,
    pub setting_paths: BTreeSet<String>,
    pub read_setting: SettingReader,
    pub setting_knowledge_ids: BTreeSet<String>,
    pub participant_schemas: Vec<JsonMap>,
    pub setting_character_schema: Option<JsonMap>,
    pub state_schemas: Vec<JsonMap>,
}

/// `PlanAuthoritySource`: the authority itself, or a callable that has one only once a Setting
/// is selected.
#[derive(Clone)]
pub enum PlanAuthoritySource {
    Fixed(Box<PlanAuthority>),
    Deferred(Arc<dyn Fn() -> Result<Option<PlanAuthority>, DomainError> + Send + Sync>),
}

/// `publisher()`: the owner's Plan publication, the draft and its library artifact.
pub type PlanPublisher = Arc<dyn Fn() -> Result<(CampaignPlanDraft, Json), DomainError> + Send + Sync>;

/// One Plan tool's work, given the selected authority.
type PlanOperation = Arc<dyn Fn(&PlanAuthority, &JsonMap) -> Result<Json, String> + Send + Sync>;

/// `checker()` and the import repair `checker()`/`publisher()`: one owner operation's result.
pub type OwnerCheck = Arc<dyn Fn() -> Result<JsonMap, DomainError> + Send + Sync>;

fn object(pairs: Vec<(&str, Json)>) -> JsonMap {
    pairs.into_iter().map(|(key, value)| (key.to_owned(), value)).collect()
}

fn rejected(code: &str) -> Json {
    Json::Object(object(vec![
        ("success", Json::Bool(false)),
        ("status", "rejected".into()),
        ("code", code.into()),
    ]))
}

/// `json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`.
fn canonical_text(value: &Json) -> String {
    pyjson::dumps(value, Dump::CANONICAL)
}

/// `text[:count]` in code points.
fn prefix(text: &str, count: usize) -> String {
    text.chars().take(count).collect()
}

/// Which `except` clause of a callback tool catches `error` (`AuthorityError` or `ValueError`).
fn authority_or_value(error: &DomainError) -> bool {
    matches!(
        py_family(error),
        PyFamily::CommandRejected | PyFamily::Authority | PyFamily::Value | PyFamily::Unicode
    )
}

/// `{"success": False, "status": "rejected", "code": getattr(error, "code", default), ...}` with
/// up to 16 diagnostics, as the Plan check and the import repair tools report a refusal.
fn owner_refusal(error: &DomainError, default: &str, state_changed: bool) -> Json {
    let mut answer = object(vec![("success", Json::Bool(false)), ("status", "rejected".into())]);
    if state_changed {
        answer.insert("state_changed".into(), Json::Bool(false));
    }
    answer.insert("code".into(), error.code().unwrap_or(default).into());
    answer.insert("message".into(), error.message().into());
    let diagnostics =
        error.diagnostics().iter().take(16).map(|item| Json::Object(item.as_payload())).collect();
    answer.insert("validation_errors".into(), Json::Array(diagnostics));
    Json::Object(answer)
}

// ── Plan check, import repair and subflow completion ───────────────────────────────────────

/// `plan_draft_check_tool(checker)`: `check_campaign_plan_draft`, read-only.
pub fn plan_draft_check_tool(checker: OwnerCheck) -> CodedResult<ToolSpec> {
    let handler: ToolHandler = Arc::new(move |params: &JsonMap| {
        if !params.is_empty() {
            return Ok(Json::Object(object(vec![
                ("success", Json::Bool(false)),
                ("code", "builder_tool_parameters_invalid".into()),
            ])));
        }
        match checker() {
            Ok(result) => {
                let mut answer =
                    object(vec![("success", Json::Bool(true)), ("status", "valid_draft".into())]);
                answer.extend(result);
                Ok(Json::Object(answer))
            }
            Err(error) if authority_or_value(&error) => {
                Ok(owner_refusal(&error, "campaign_plan_invalid", true))
            }
            Err(error) => Err(error.message().to_owned()),
        }
    });
    ToolSpec::from_openai_schema(
        exported_definition("check_campaign_plan_draft"),
        handler,
        &[ToolsetName::BUILDER_TOOLS],
    )
}

/// `import_repair_tools(checker, publisher)`: `check_import_repair` and
/// `publish_import_repair`, both with the same repaired-content checks.
pub fn import_repair_tools(checker: OwnerCheck, publisher: OwnerCheck) -> CodedResult<Vec<ToolSpec>> {
    let tool = |name: &str, operation: OwnerCheck| {
        let handler: ToolHandler = Arc::new(move |params: &JsonMap| {
            if !params.is_empty() {
                return Ok(Json::Object(object(vec![
                    ("success", Json::Bool(false)),
                    ("code", "builder_tool_parameters_invalid".into()),
                ])));
            }
            match operation() {
                Ok(result) => {
                    let mut answer = object(vec![("success", Json::Bool(true))]);
                    answer.extend(result);
                    Ok(Json::Object(answer))
                }
                Err(error) if authority_or_value(&error) => {
                    Ok(owner_refusal(&error, "import_repair_invalid", false))
                }
                Err(error) => Err(error.message().to_owned()),
            }
        });
        ToolSpec::from_openai_schema(exported_definition(name), handler, &[ToolsetName::BUILDER_TOOLS])
    };
    Ok(vec![tool("check_import_repair", checker)?, tool("publish_import_repair", publisher)?])
}

/// `_creation_subflow_completion_tool()`: `complete_creation_subflow`, semantic readiness with
/// no campaign authority.
pub fn creation_subflow_completion_tool() -> CodedResult<ToolSpec> {
    let handler: ToolHandler = Arc::new(|params: &JsonMap| {
        let keys =
            params.len() == 2 && params.contains_key("summary") && params.contains_key("boundary_conflicts");
        let summary = params
            .get("summary")
            .and_then(Json::as_str)
            .filter(|text| !pystr::strip(text).is_empty() && pystr::len(text) <= 2000);
        let conflicts = params.get("boundary_conflicts").and_then(Json::as_array).filter(|items| {
            items.len() <= 16
                && items.iter().all(|item| {
                    item.as_str()
                        .is_some_and(|text| !pystr::strip(text).is_empty() && pystr::len(text) <= 500)
                })
        });
        let (true, Some(summary), Some(conflicts)) = (keys, summary, conflicts) else {
            return Ok(rejected("completion_invalid"));
        };
        let stripped: Vec<Json> = conflicts
            .iter()
            .map(|item| Json::from(pystr::strip(item.as_str().unwrap_or_default())))
            .collect();
        Ok(Json::Object(object(vec![
            ("success", Json::Bool(stripped.is_empty())),
            ("status", if stripped.is_empty() { "ready" } else { "blocked" }.into()),
            ("summary", pystr::strip(summary).into()),
            ("boundary_conflicts", Json::Array(stripped)),
        ])))
    });
    ToolSpec::from_openai_schema(
        exported_definition("complete_creation_subflow"),
        handler,
        &[ToolsetName::BUILDER_TOOLS],
    )
}

// ── The Plan tools ─────────────────────────────────────────────────────────────────────────

/// `_campaign_plan_example(authority)`: one small package-bound example that satisfies the
/// closed Plan schema, as canonical JSON text.
///
/// An authority with no Setting paths is Python's `ValueError` from `min()`.
pub fn campaign_plan_example(authority: &PlanAuthority) -> Result<String, DomainError> {
    let source_path = authority
        .setting_paths
        .iter()
        .next()
        .ok_or_else(|| DomainError::value("min() iterable argument is empty"))?
        .clone();
    let mut initial_state = serde_json::json!({
        "entities": [],
        "facts": ["Replace this example fact with the campaign's actual starting state."],
    });
    let required: BTreeSet<&str> = authority
        .state_schemas
        .iter()
        .filter(|item| item.get("required").is_some_and(truthy))
        .filter_map(|item| item.get("key").and_then(Json::as_str))
        .collect();
    if !required.is_empty() {
        let worked = load_builder_skill("BUILD CAMPAIGN PLAN", Some("references/combat-plan.json"))
            .map_err(|error| DomainError::value(error.message))?;
        let reference = pyjson::loads(&worked.text).map_err(|error| DomainError::value(error.message))?;
        let mechanics: Vec<Json> = reference["initial_state"]
            .get("mechanics")
            .and_then(Json::as_array)
            .into_iter()
            .flatten()
            .filter(|row| {
                row["namespace"].as_str() == Some(authority.ruleset.package_id.as_str())
                    && row["key"].as_str().is_some_and(|key| required.contains(key))
            })
            .cloned()
            .collect();
        if !mechanics.is_empty() {
            initial_state["mechanics"] = Json::Array(mechanics);
        }
    }
    let payload = serde_json::json!({
        "bindings": {
            "campaign_setting": authority.setting.as_payload(),
            "package_closure_sha256": authority.closure_sha256,
            "ruleset": authority.ruleset.as_payload(),
        },
        "boundaries": ["Respect the player's stated boundaries."],
        "completion_conditions": ["Replace with the final act's exact closing event."],
        "continuation_conditions": ["Unresolved consequences may continue in later play."],
        "ending": {
            "mode": "finite",
            "outcomes": [{
                "id": "story-concluded",
                "conditions": ["Replace with the actual final act closing event."],
                "outcome": "Replace with the concise intended outcome.",
                "immediate_aftermath": ["Name the immediate loose end and its resolution."],
                "open_world": true,
                "after_story": null,
            }],
        },
        "failure_conditions": [],
        "format_id": "sylvia-gm-campaign-plan",
        "format_version": 2,
        "gm_guidance": {
            "instructions": "Use the exact Ruleset for encoded mechanics and the Setting as the world source.",
            "player_freedom": "Let player decisions determine the route and consequences.",
            "preparation_detail": "Read the current act and its required objectives before framing its main problem \
                or judging completion. All linked objectives and their conditions must hold; optional work does not \
                replace the named closing event. Retrieve Setting entries when needed for the current situation.",
            "railroading": "Preserve the premise without forcing one solution.",
        },
        "id": "replace-with-campaign-id",
        "initial_state": initial_state,
        "intended_player_experience": "Replace with the intended player experience.",
        "objectives": [{
            "completion_conditions": [
                "Replace with the required main-story discoveries/events.",
                "Replace with the exact closing event and any explicit alternative outcome.",
            ],
            "description": "Replace with the campaign's first objective.",
            "id": "primary-objective",
            "title": "Primary objective",
        }],
        "premise": "Replace with the campaign premise from campaign_construction_brief.",
        "revision": 1,
        "secrets": [],
        "setting_references": {"creatures": [], "factions": [], "items": [], "locations": [], "lore": [], "npcs": []},
        "side_content": [],
        "starting_location": {"id": "replace-with-location-id", "path": source_path},
        "starting_party": {
            "player_character": {"mode": "compatible"},
            "required_companions": [],
            "custom_companions": {"minimum": 0, "maximum": 0},
        },
        "structure": [{
            "branches": ["Allow another approach when the player changes direction."],
            "consequences": ["Carry committed outcomes into the next situation."],
            "dynamic_encounter_guidance": ["Use only mechanics exposed by the Ruleset."],
            "id": "act-1",
            "objectives": ["primary-objective"],
            "planned_encounters": [],
            "setting_refs": [source_path],
            "situations": ["Replace with the opening playable situation."],
            "summary": "Replace with a concise act summary.",
            "title": "Opening",
        }],
        "title": "Replace with campaign title",
        "tone": "Replace with the requested tone.",
        "unsupported_mechanics": [],
    });
    Ok(canonical_text(&payload))
}

/// Python truthiness of a JSON value.
fn truthy(value: &Json) -> bool {
    match value {
        Json::Null => false,
        Json::Bool(flag) => *flag,
        Json::Number(number) => number.as_f64().is_some_and(|value| value != 0.0),
        Json::String(text) => !text.is_empty(),
        Json::Array(items) => !items.is_empty(),
        Json::Object(map) => !map.is_empty(),
    }
}

/// `params.get(key, default)` as a page bound: an integer (never a boolean).
fn integer(params: &JsonMap, key: &str, default: i64) -> Option<i64> {
    match params.get(key) {
        None => Some(default),
        Some(Json::Number(number)) if number.is_i64() || number.is_u64() => {
            Some(number.as_i64().unwrap_or(i64::MAX))
        }
        Some(_) => None,
    }
}

/// One `read` of the Plan source tool, or `None` where Python's `OSError`, `UnicodeError` or
/// `ValueError` makes the source unavailable.
fn read_source(authority: &PlanAuthority, path: &str) -> Result<Option<String>, String> {
    let content = match path {
        "campaign-plan/example/plan.json" => campaign_plan_example(authority),
        "campaign-plan/example/plan.md" => Ok(
            "# Replace with campaign title\n\nWrite the readable GM rendering of the structured plan here."
                .into(),
        ),
        "ruleset/identity.json" => Ok(canonical_text(&serde_json::json!({
            "binding": authority.ruleset.as_payload(),
            "campaign_setting": authority.setting.as_payload(),
            "package_closure_sha256": authority.closure_sha256,
            "participant_artifacts": authority.participant_schemas,
        }))),
        _ => match path.strip_prefix("setting/") {
            Some(relative) => (authority.read_setting)(relative),
            None => Err(DomainError::value("Campaign source path is unavailable")),
        },
    };
    match content {
        Ok(content) => Ok(Some(content)),
        Err(error) if matches!(py_family(&error), PyFamily::Os | PyFamily::Unicode | PyFamily::Value) => {
            Ok(None)
        }
        Err(error) => Err(error.message().to_owned()),
    }
}

/// `_campaign_source_tool(authority)`'s answer to one call.
fn campaign_source(authority: &PlanAuthority, params: &JsonMap) -> Result<Json, String> {
    let action = params.get("action").and_then(Json::as_str);
    let path = params.get("path");
    let query = params.get("query");
    if action == Some("list") {
        let query = match query {
            None => "",
            Some(Json::String(text)) if pystr::len(text) <= 120 => text.as_str(),
            Some(_) => return Ok(rejected("source_query_invalid")),
        };
        if path.is_some_and(|path| !path.is_null()) {
            return Ok(rejected("source_query_invalid"));
        }
        let (Some(offset), Some(limit)) = (integer(params, "offset", 0), integer(params, "limit", 512))
        else {
            return Ok(rejected("source_page_invalid"));
        };
        if offset < 0 || !(1..=512).contains(&limit) {
            return Ok(rejected("source_page_invalid"));
        }
        let mut paths: Vec<String> =
            ["campaign-plan/example/plan.json", "campaign-plan/example/plan.md", "ruleset/identity.json"]
                .into_iter()
                .map(str::to_owned)
                .collect();
        paths.extend(authority.setting_paths.iter().map(|item| format!("setting/{item}")));
        let needle = pystr::casefold(pystr::strip(query));
        let matches: Vec<String> = paths
            .into_iter()
            .filter(|item| needle.is_empty() || pystr::casefold(item).contains(&needle))
            .collect();
        let offset = usize::try_from(offset).unwrap_or(usize::MAX);
        if offset > matches.len() {
            return Ok(rejected("source_page_invalid"));
        }
        let end = matches.len().min(offset.saturating_add(usize::try_from(limit).unwrap_or(512)));
        return Ok(Json::Object(object(vec![
            ("success", Json::Bool(true)),
            ("status", "available".into()),
            ("paths", matches[offset..end].iter().cloned().map(Json::String).collect()),
            ("total", matches.len().into()),
            ("next_offset", if end < matches.len() { end.into() } else { Json::Null }),
        ])));
    }
    if action == Some("read") {
        let query_ok = matches!(query, None | Some(Json::Null)) || query.and_then(Json::as_str) == Some("");
        let Some(Json::String(path)) = path else { return Ok(rejected("source_path_invalid")) };
        if !query_ok || params.contains_key("offset") || params.contains_key("limit") {
            return Ok(rejected("source_path_invalid"));
        }
        let Some(content) = read_source(authority, path)? else { return Ok(rejected("source_unavailable")) };
        return Ok(Json::Object(object(vec![
            ("success", Json::Bool(true)),
            ("status", "available".into()),
            ("path", path.as_str().into()),
            ("content", content.into()),
        ])));
    }
    Ok(rejected("source_action_invalid"))
}

/// `_campaign_plan_diagnostic(diagnostic, authority)`: publication errors pointed at the Plan's
/// editable files, keeping package identity.
fn plan_diagnostic(diagnostic: &RulesetDiagnostic, authority: &PlanAuthority) -> Json {
    let keys = ["code", "message", "file", "path", "guidance", "package_id"];
    let mut payload: JsonMap =
        diagnostic.as_payload().into_iter().filter(|(key, _)| keys.contains(&key.as_str())).collect();
    if matches!(diagnostic.code.as_str(), "campaign_plan_fields_invalid" | "campaign_plan_reference_invalid")
    {
        payload.insert("file".into(), "campaign-plan-draft/plan.json".into());
    } else if diagnostic.code == "package_content_invalid" {
        if let Some(file) = &diagnostic.file {
            let package = diagnostic.package_id.as_deref();
            if package == Some(authority.setting.package_id.as_str()) {
                payload.insert("file".into(), format!("campaign-plan-draft/setting-files/{file}").into());
            } else if package == Some(authority.ruleset.package_id.as_str()) {
                payload.insert("file".into(), format!("campaign-plan-draft/ruleset-files/{file}").into());
            }
        }
    }
    Json::Object(payload)
}

/// The draft entries an inherited-package edit leaves, which a Plan chat cannot publish.
const INHERITED_EDITS: [&str; 6] = [
    "setting-files",
    "ruleset-files",
    "removed-files.json",
    "package-records",
    "ruleset-source.json",
    "setting-source.json",
];

/// `_campaign_plan_tool(workspace, authority, publisher)`'s answer to one call.
fn publish_plan(
    workspace: &Path,
    authority: &PlanAuthority,
    publisher: Option<&PlanPublisher>,
    skill_sha256: &str,
    params: &JsonMap,
) -> Result<Json, String> {
    if !params.is_empty() {
        return Ok(rejected("builder_tool_parameters_invalid"));
    }
    let outcome = match publisher {
        Some(publisher) => publisher().map(|(draft, artifact)| (draft, Some(artifact))),
        None => {
            let draft_directory = workspace.join("campaign-plan-draft");
            if INHERITED_EDITS.iter().any(|name| draft_directory.join(name).exists()) {
                Err(DomainError::command_rejected_with(
                    "Inherited package edits require an independent Campaign Plan Builder chat",
                    "command_rejected",
                    Vec::new(),
                ))
            } else {
                let expectations = PlanDraftExpectations {
                    expected_ruleset: &authority.ruleset,
                    expected_setting: &authority.setting,
                    expected_closure_sha256: &authority.closure_sha256,
                    setting_paths: &authority.setting_paths,
                    setting_knowledge_ids: &authority.setting_knowledge_ids,
                };
                publish_campaign_plan_draft(workspace, expectations, &authority.state_schemas)
                    .map(|draft| (draft, None))
            }
        }
    };
    let (draft, library_artifact) = match outcome {
        Ok(published) => published,
        Err(error) => {
            return match py_family(&error) {
                // `except (ValueError, CommandRejected)`: a `UnicodeError` is a `ValueError`.
                PyFamily::CommandRejected | PyFamily::Value | PyFamily::Unicode => {
                    let diagnostics = error
                        .diagnostics()
                        .iter()
                        .take(8)
                        .map(|item| plan_diagnostic(item, authority))
                        .collect();
                    Ok(Json::Object(object(vec![
                        ("success", Json::Bool(false)),
                        ("status", "rejected".into()),
                        ("code", "campaign_plan_invalid".into()),
                        ("message", prefix(error.message(), 240).into()),
                        ("validation_errors", Json::Array(diagnostics)),
                    ])))
                }
                PyFamily::Os => Ok(rejected("campaign_plan_unavailable")),
                PyFamily::Authority | PyFamily::Type | PyFamily::Other => Err(error.message().to_owned()),
            };
        }
    };
    let bindings = draft.plan.payload.get("bindings").cloned().unwrap_or(Json::Null);
    let mut answer = object(vec![
        ("success", Json::Bool(true)),
        ("status", "ready".into()),
        ("artifact_kind", "campaign_plan".into()),
        ("player_character_required", Json::Bool(true)),
        (
            "message",
            "Campaign Plan saved. Starting play still requires a Player Character and completed campaign setup.".into(),
        ),
        ("artifact_id", draft.plan.artifact_id.as_str().into()),
        ("title", draft.plan.title.as_str().into()),
        ("content_sha256", draft.plan.content_sha256.as_str().into()),
        ("plan_path", draft.plan_path.as_str().into()),
        ("readable_path", draft.readable_path.as_str().into()),
        ("readable_preview", prefix(&draft.plan.readable_markdown, 6000).into()),
        ("ruleset", bindings["ruleset"].clone()),
        ("campaign_setting", bindings["campaign_setting"].clone()),
        ("package_closure_sha256", bindings["package_closure_sha256"].clone()),
    ]);
    if let Some(artifact) = library_artifact.filter(|artifact| !artifact.is_null()) {
        answer.insert("library_artifact".into(), artifact);
    }
    answer.insert(
        "tracked_skill".into(),
        serde_json::json!({"name": "BUILD CAMPAIGN PLAN", "sha256": skill_sha256}),
    );
    Ok(Json::Object(answer))
}

/// `text.removeprefix("setting/")`.
fn without_setting_prefix(text: &str) -> String {
    text.strip_prefix("setting/").unwrap_or(text).to_owned()
}

/// A JSON integer (never a boolean), Python's `int`.
fn py_int(value: Option<&Json>) -> Option<i128> {
    match value {
        Some(Json::Number(number)) if number.is_i64() => number.as_i64().map(i128::from),
        Some(Json::Number(number)) if number.is_u64() => number.as_u64().map(i128::from),
        _ => None,
    }
}

/// `_campaign_plan_draft_tool(workspace, authority)`'s answer to one call: a bound Plan scaffold
/// with explicitly authored objective endings.
fn create_draft(workspace: &Path, authority: &PlanAuthority, params: &JsonMap) -> Result<Json, String> {
    const REQUIRED: [&str; 10] = [
        "title",
        "premise",
        "tone",
        "player_character_mode",
        "starting_location_path",
        "opening_situation",
        "primary_objective",
        "completion_conditions",
        "custom_companion_minimum",
        "custom_companion_maximum",
    ];
    const OPTIONAL: [&str; 3] = ["opening_direction", "player_character_path", "required_companion_paths"];
    const TEXT: [&str; 7] = [
        "title",
        "premise",
        "tone",
        "player_character_mode",
        "starting_location_path",
        "opening_situation",
        "primary_objective",
    ];
    let text = |key: &str| params.get(key).and_then(Json::as_str).map(pystr::strip).unwrap_or_default();
    let keys_ok = REQUIRED.iter().all(|key| params.contains_key(*key))
        && params.keys().all(|key| REQUIRED.contains(&key.as_str()) || OPTIONAL.contains(&key.as_str()));
    let (minimum, maximum) =
        (py_int(params.get("custom_companion_minimum")), py_int(params.get("custom_companion_maximum")));
    if !keys_ok || TEXT.iter().any(|key| text(key).is_empty()) || minimum.is_none() || maximum.is_none() {
        return Ok(rejected("campaign_plan_concept_invalid"));
    }
    let conditions = match params.get("completion_conditions") {
        Some(Json::Array(items))
            if !items.is_empty()
                && items.iter().all(|item| item.as_str().is_some_and(|t| !pystr::strip(t).is_empty())) =>
        {
            items
                .iter()
                .map(|item| pystr::strip(item.as_str().unwrap_or_default()).to_owned())
                .collect::<Vec<_>>()
        }
        _ => return Ok(rejected("campaign_plan_concept_invalid")),
    };
    let player_mode = text("player_character_mode");
    let raw_player_path = params.get("player_character_path");
    let fixed_without_path = player_mode == "fixed"
        && !raw_player_path.and_then(Json::as_str).is_some_and(|path| !pystr::strip(path).is_empty());
    if !matches!(player_mode, "compatible" | "fixed") || fixed_without_path {
        return Ok(rejected("campaign_plan_party_policy_invalid"));
    }
    let player_path =
        raw_player_path.and_then(Json::as_str).map(|path| without_setting_prefix(pystr::strip(path)));
    let companions = match params.get("required_companion_paths") {
        None => Vec::new(),
        Some(Json::Array(items))
            if items.iter().all(|item| item.as_str().is_some_and(|t| !pystr::strip(t).is_empty())) =>
        {
            items
                .iter()
                .map(|item| without_setting_prefix(pystr::strip(item.as_str().unwrap_or_default())))
                .collect()
        }
        Some(_) => return Ok(rejected("campaign_plan_party_policy_invalid")),
    };
    let (minimum, maximum) = (minimum.unwrap_or_default(), maximum.unwrap_or_default());
    if !(0 <= minimum && minimum <= maximum && maximum <= 64) {
        return Ok(rejected("campaign_plan_party_policy_invalid"));
    }
    let location_path = without_setting_prefix(text("starting_location_path"));
    let known = |path: &String| authority.setting_paths.contains(path);
    if player_path.as_ref().is_some_and(|path| !known(path))
        || !companions.iter().all(known)
        || !known(&location_path)
    {
        return Ok(rejected("campaign_plan_source_missing"));
    }
    let (title, premise, tone) = (text("title"), text("premise"), text("tone"));
    let (opening, objective) = (text("opening_situation"), text("primary_objective"));
    let example = campaign_plan_example(authority).map_err(|error| error.message().to_owned())?;
    let Ok(Json::Object(mut payload)) = pyjson::loads(&example) else {
        return Err("the example Plan is a JSON object".into());
    };
    let suffix = sha256_hex(canonical_json_bytes(&serde_json::json!({"premise": premise, "title": title})));
    payload.insert("id".into(), format!("campaign-{}", &suffix[..16]).into());
    payload.insert("title".into(), title.into());
    payload.insert("premise".into(), premise.into());
    payload.insert("tone".into(), tone.into());
    payload.insert("intended_player_experience".into(), premise.into());
    payload.insert("initial_state".into(), serde_json::json!({"entities": [], "facts": [premise]}));
    let mut player_character = object(vec![("mode", player_mode.into())]);
    if let (true, Some(path)) = (player_mode == "fixed", &player_path) {
        player_character
            .insert("character".into(), serde_json::json!({"id": "fixed-player-character", "path": path}));
    }
    let required_companions: Vec<Json> = companions
        .iter()
        .enumerate()
        .map(|(index, path)| serde_json::json!({"id": format!("required-companion-{}", index + 1), "path": path}))
        .collect();
    payload.insert(
        "starting_party".into(),
        serde_json::json!({
            "player_character": player_character,
            "required_companions": required_companions,
            "custom_companions": {"minimum": minimum as i64, "maximum": maximum as i64},
        }),
    );
    let opening_direction = match params.get("opening_direction") {
        None => None,
        Some(Json::String(direction)) if !pystr::strip(direction).is_empty() => {
            Some(pystr::strip(direction).to_owned())
        }
        Some(_) => return Ok(rejected("campaign_plan_opening_direction_invalid")),
    };
    if let Some(direction) = &opening_direction {
        payload.insert("opening_direction".into(), direction.as_str().into());
    }
    payload.insert(
        "starting_location".into(),
        serde_json::json!({"id": "starting-location", "path": location_path}),
    );
    payload.insert(
        "objectives".into(),
        serde_json::json!([{
            "completion_conditions": conditions,
            "description": objective,
            "id": "primary-objective",
            "title": "Primary objective",
        }]),
    );
    payload.insert("completion_conditions".into(), serde_json::json!(conditions));
    if let Some(outcome) = payload.get_mut("ending").and_then(|ending| ending["outcomes"].get_mut(0)) {
        outcome["conditions"] = serde_json::json!(conditions);
        outcome["outcome"] = objective.into();
        outcome["immediate_aftermath"] = serde_json::json!([]);
    }
    // `list(dict.fromkeys([...]))`: the references in order, each once.
    let mut references: Vec<String> = Vec::new();
    for path in player_path.iter().chain(companions.iter()).chain(std::iter::once(&location_path)) {
        if !references.contains(path) {
            references.push(path.clone());
        }
    }
    if let Some(act) = payload.get_mut("structure").and_then(|structure| structure.get_mut(0)) {
        act["setting_refs"] = serde_json::json!(references);
        act["situations"] = serde_json::json!([opening]);
        act["summary"] = opening.into();
    }
    let mut readable = format!("# {title}\n\n{premise}\n\n## Tone\n\n{tone}\n\n");
    if let Some(direction) = &opening_direction {
        readable.push_str(&format!("## Opening direction\n\n{direction}\n\n"));
    }
    readable
        .push_str(&format!("## Opening situation\n\n{opening}\n\n## Primary objective\n\n{objective}\n\n"));
    readable.push_str("## Completion and aftermath\n\n");
    readable.push_str(
        &conditions.iter().map(|condition| format!("- {condition}")).collect::<Vec<_>>().join("\n"),
    );
    readable.push('\n');
    // `write_text(..., encoding="utf-8")` is text mode: Windows writes each newline as CRLF.
    if cfg!(windows) {
        readable = readable.replace('\n', "\r\n");
    }
    let draft_directory = workspace.join("campaign-plan-draft");
    let written = std::fs::create_dir_all(&draft_directory)
        .and_then(|()| {
            std::fs::write(draft_directory.join("plan.json"), canonical_json_bytes(&Json::Object(payload)))
        })
        .and_then(|()| std::fs::write(draft_directory.join("plan.md"), readable));
    if written.is_err() {
        return Ok(rejected("campaign_plan_draft_unavailable"));
    }
    Ok(Json::Object(object(vec![
        ("success", Json::Bool(true)),
        ("status", "draft_ready".into()),
        ("title", title.into()),
        ("plan_path", "campaign-plan-draft/plan.json".into()),
        ("readable_path", "campaign-plan-draft/plan.md".into()),
    ])))
}

/// The Plan tools' authority for one call: the fixed one, or the selected one.
fn selected(source: &PlanAuthoritySource) -> Result<Option<PlanAuthority>, String> {
    match source {
        PlanAuthoritySource::Fixed(authority) => Ok(Some(PlanAuthority::clone(authority))),
        PlanAuthoritySource::Deferred(select) => select().map_err(|error| error.message().to_owned()),
    }
}

/// `_campaign_plan_tools(workspace, authority, publisher, checker)`: the Plan tools, for a chat
/// with an exact Setting or one that selects it later (`builder_setting_selection_required`
/// until then).
pub fn campaign_plan_tools(
    workspace: &Path,
    authority: PlanAuthoritySource,
    publisher: Option<PlanPublisher>,
    checker: Option<OwnerCheck>,
) -> CodedResult<Vec<ToolSpec>> {
    let skill = load_builder_skill("BUILD CAMPAIGN PLAN", None)?;
    let skill_sha256 = sha256_hex(skill.text.as_bytes());
    let workspace: PathBuf = workspace.to_path_buf();
    let wrap = |definition: &Json, operation: PlanOperation| {
        let source = authority.clone();
        let handler: ToolHandler = Arc::new(move |params: &JsonMap| match selected(&source)? {
            Some(authority) => operation(&authority, params),
            None => Ok(serde_json::json!({
                "success": false, "code": "builder_setting_selection_required",
                "message": "Use select_builder_setting with the exact saved Setting first.",
            })),
        });
        ToolSpec::from_openai_schema(definition, handler, &[ToolsetName::BUILDER_TOOLS])
    };
    let draft_workspace = workspace.clone();
    let mut tools = vec![
        wrap(campaign_source_tool_definition(), Arc::new(campaign_source))?,
        wrap(
            create_campaign_plan_draft_tool_definition(),
            Arc::new(move |authority: &PlanAuthority, params: &JsonMap| {
                create_draft(&draft_workspace, authority, params)
            }),
        )?,
        wrap(
            campaign_plan_tool_definition(),
            Arc::new(move |authority: &PlanAuthority, params: &JsonMap| {
                publish_plan(&workspace, authority, publisher.as_ref(), &skill_sha256, params)
            }),
        )?,
    ];
    if let Some(checker) = checker {
        tools.push(plan_draft_check_tool(checker)?);
    }
    Ok(tools)
}
