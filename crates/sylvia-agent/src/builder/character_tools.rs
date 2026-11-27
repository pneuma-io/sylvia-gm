//! The Builder's character tools (B5): `_builder_session.py`'s `_participant_entity_state`,
//! `_participant_artifact_tool` and `_participant_draft_check_tool`, `_participant_file_tool.py`,
//! `_character_draft_tool.py` and `_character_reuse_tool.py`.
//!
//! The library and campaign work stays with the owner: each tool takes the callback Python's
//! takes ([`ParticipantPublisher`], [`ParticipantReader`], [`SourceProfileResolver`]), which
//! answers Python's own exception classes as a [`DomainError`]. Each tool catches what Python's
//! catches; anything else is the tool's error.
//!
//! The definitions depend on the chat's declarations, schemas and role, so they are built here
//! as Python builds them, and the vectors compare each one whole.

use super::tools::{ToolHandler, ToolSpec, ToolsetName};
use super::{py_family, PyFamily};
use indexmap::IndexMap;
use std::io::Read;
use std::path::Path;
use std::sync::Arc;
use sylvia_app::path_guardrail::{platform, PathGuardrail};
use sylvia_app::reusable_participants::validate_reusable_participant_draft;
use sylvia_app::workspace_files::{content_version, write_text};
use sylvia_base::pyjson::{self, Dump, HookFailure, HookPolicy};
use sylvia_base::{pystr, CodedError, CodedResult, Json, JsonMap};
use sylvia_domain::package_library::{canonical_json_bytes, ParticipantArtifactDeclaration};
use sylvia_domain::DomainError;

/// The largest entity state, Setting data or workspace draft, 256 KiB.
const MAX_DRAFT_BYTES: usize = 256 * 1024;
/// The deepest nesting a draft's JSON may have.
const MAX_DEPTH: usize = 32;
/// How many diagnostics a refusal reports.
const DIAGNOSTICS: usize = 16;

/// `publisher(role=..., summary=..., ...)`: the inputs of one character publication or check.
#[derive(Debug, Clone, PartialEq)]
pub struct ParticipantInput {
    pub role: String,
    pub summary: String,
    pub authored_profile: String,
    pub participant: JsonMap,
    pub entity_state: JsonMap,
    /// Present only when the model gave one.
    pub schema_id: Option<String>,
    pub setting_data: Option<JsonMap>,
    /// The model's value as given (Python passes it through unchecked).
    pub source_profile: Option<Json>,
}

/// `ParticipantPublisher`: save, or only check, validated content for a character chat.
pub type ParticipantPublisher = Arc<dyn Fn(&ParticipantInput) -> Result<JsonMap, DomainError> + Send + Sync>;

/// `ParticipantDraftReader` and `ParticipantReuser`: `(artifact_id, expected_content_sha256,
/// schema_id)`, the exact saved source for the owner-bound destination and role.
pub type ParticipantReader =
    Arc<dyn Fn(&str, &str, Option<&str>) -> Result<JsonMap, DomainError> + Send + Sync>;

/// `resolve_source_profile(source_profile)`: verify an exact general profile, if one is named.
pub type SourceProfileResolver =
    Arc<dyn Fn(Option<&Json>) -> Result<Option<JsonMap>, DomainError> + Send + Sync>;

fn object(pairs: Vec<(&str, Json)>) -> JsonMap {
    pairs.into_iter().map(|(key, value)| (key.to_owned(), value)).collect()
}

/// `re.fullmatch(r"[0-9a-f]{n}", text)`.
fn lower_hex(text: &str, length: usize) -> bool {
    text.len() == length && text.bytes().all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f'))
}

/// `re.fullmatch(r"participant-[0-9a-f]{24}-[0-9a-f]{16}", text)`.
fn is_participant_id(text: &str) -> bool {
    text.strip_prefix("participant-")
        .and_then(|rest| rest.split_once('-'))
        .is_some_and(|(operation, digest)| lower_hex(operation, 24) && lower_hex(digest, 16))
}

/// `params[key]` as text, when it is text.
fn text<'a>(params: &'a JsonMap, key: &str) -> Option<&'a str> {
    params.get(key).and_then(Json::as_str)
}

/// `error.diagnostics[:16]` as payloads.
fn diagnostics(error: &DomainError) -> Json {
    Json::Array(
        error.diagnostics().iter().take(DIAGNOSTICS).map(|item| Json::Object(item.as_payload())).collect(),
    )
}

/// `{**dict(result)}` after `head`: a key the result also has keeps its place, with the
/// result's value.
fn merged(head: JsonMap, result: JsonMap) -> Json {
    let mut merged = head;
    merged.extend(result);
    Json::Object(merged)
}

/// `str(path.relative_to(guardrail.allowed_root).as_posix())`.
fn workspace_path(guardrail: &PathGuardrail, target: &Path) -> String {
    platform::relative_parts(&target.to_string_lossy(), guardrail.allowed_root())
        .map(|parts| if parts.is_empty() { ".".to_owned() } else { parts.join("/") })
        .unwrap_or_default()
}

/// `json.dumps(dict(draft), ensure_ascii=False, indent=2) + "\n"`.
fn draft_text(draft: &JsonMap) -> String {
    let mut text = pyjson::dumps(&Json::Object(draft.clone()), Dump::INDENT2.ascii(false));
    text.push('\n');
    text
}

/// `_participant_entity_state(value)`: opaque Ruleset state (or Setting data) parsed from JSON
/// object text without losing types: at most 256 KiB, unique members, 32 nested levels and
/// finite values. Python raises a `ValueError` (or `RecursionError`) for anything else, which
/// every caller turns into its own refusal, so the reason is not kept.
pub fn participant_entity_state(value: &Json) -> Option<JsonMap> {
    let Json::String(text) = value else { return None };
    if text.len() > MAX_DRAFT_BYTES {
        return None;
    }
    let policy = HookPolicy { refuse_duplicates: true, refuse_constants: false };
    let parsed = match pyjson::loads_hooked(text, policy) {
        // A `NaN` or overflowing number parses, then the canonical encoder refuses it.
        Ok(hooked) if !hooked.non_finite => hooked.value,
        Ok(_) | Err(HookFailure::Decode(_) | HookFailure::Duplicate(_) | HookFailure::Constant(_)) => {
            return None
        }
        // Text Python reads but Rust cannot hold (differences rows 1, 3 and 4).
        Err(HookFailure::Unsupported(_)) => return None,
    };
    let Json::Object(parsed) = parsed else { return None };
    let mut pending: Vec<(&Json, usize)> = parsed.values().map(|child| (child, 1)).collect();
    while let Some((item, depth)) = pending.pop() {
        if depth > MAX_DEPTH {
            return None;
        }
        match item {
            Json::Object(map) => pending.extend(map.values().map(|child| (child, depth + 1))),
            Json::Array(items) => pending.extend(items.iter().map(|child| (child, depth + 1))),
            _ => {}
        }
    }
    Some(parsed)
}

/// The participant tools' refusal for malformed input.
fn artifact_invalid() -> Json {
    Json::Object(object(vec![
        ("success", Json::Bool(false)),
        ("status", "rejected".into()),
        ("code", "participant_artifact_invalid".into()),
    ]))
}

/// `_participant_artifact_tool`'s definition.
fn artifact_definition(role: Option<&str>, schemas: &IndexMap<String, JsonMap>, checking: bool) -> Json {
    let description = if checking {
        "Check the character draft and compute its declared starting values without saving a library entry, \
         discarding a draft, or changing a campaign."
    } else {
        "Validate and save the completed Player Character or Companion as an exact local reusable library choice. \
         Save the full finished profile and mechanical data."
    };
    let mut definition = serde_json::json!({
        "type": "function",
        "function": {
            "name": if checking { "check_participant_draft" } else { "publish_participant_artifact" },
            "description": description,
            "parameters": {
                "type": "object",
                "additionalProperties": false,
                "required": ["summary", "authored_profile", "participant", "entity_state"],
                "properties": {
                    "summary": {"type": "string", "minLength": 1, "maxLength": 500,
                                "description": "Short selection summary, 1-500 characters."},
                    "authored_profile": {
                        "type": "string",
                        "minLength": 1,
                        "description": "The full authored Character File body: characterization, history, \
                            relationships, goals, and other established details. This is distinct from the short \
                            selector summary and from Ruleset data.",
                    },
                    "participant": {"type": "object", "additionalProperties": true},
                    "source_profile": {
                        "type": "object", "additionalProperties": false,
                        "required": ["artifact_id", "content_sha256"],
                        "properties": {"artifact_id": {"type": "string"}, "content_sha256": {"type": "string"}},
                        "description": "Exact general profile used for this adaptation, if any.",
                    },
                    "setting_data": {
                        "type": "string", "minLength": 2, "maxLength": MAX_DRAFT_BYTES,
                        "description": "Setting-specific character information as JSON object text. Maximum 256 KiB \
                            of UTF-8 text; non-ASCII characters use more bytes. Read the setting_data schema in \
                            get_content_definition character_creation. Supply all required world fields; keep \
                            Ruleset mechanics in participant and entity_state.",
                    },
                    "entity_state": {
                        "type": "string",
                        "minLength": 2,
                        "maxLength": MAX_DRAFT_BYTES,
                        "description": "Authored starting state inputs encoded as JSON object text. Maximum 256 KiB \
                            of UTF-8 text; non-ASCII characters use more bytes. Read get_content_definition \
                            character_creation and omit calculated output fields; Sylvia computes those from the \
                            declared formulas. Preserve JSON numbers, booleans, arrays and objects, including \
                            empty collections. For example: \
                            {\"counter\":3,\"enabled\":true,\"items\":[],\"details\":{}}. Use the selected \
                            Ruleset's actual fields and values.",
                    },
                },
            },
        },
    });
    let parameters = &mut definition["function"]["parameters"];
    if !schemas.is_empty() {
        // `json.loads(canonical_json_bytes(value))`: each schema with its keys sorted.
        let declared: Vec<Json> = schemas
            .values()
            .map(|schema| {
                let bytes = canonical_json_bytes(&Json::Object(schema.clone()));
                pyjson::loads(&String::from_utf8_lossy(&bytes)).unwrap_or(Json::Null)
            })
            .collect();
        parameters["properties"]["participant"] = if declared.len() == 1 {
            declared.into_iter().next().unwrap_or(Json::Null)
        } else {
            serde_json::json!({"type": "object", "anyOf": declared})
        };
    }
    if role.is_none() {
        parameters["properties"]["role"] = serde_json::json!({
            "type": "string", "enum": ["player_character", "companion"],
            "description": "The role assigned by this Plan's starting-party requirement.",
        });
        push_required(parameters, "role");
    }
    if !schemas.is_empty() {
        let ids: Vec<&String> = schemas.keys().collect();
        parameters["properties"]["schema_id"] = serde_json::json!({
            "type": "string", "enum": ids,
            "description": "Exact participant schema from the original Ruleset references.",
        });
        if schemas.len() > 1 {
            push_required(parameters, "schema_id");
        }
    }
    definition
}

fn push_required(parameters: &mut Json, name: &str) {
    if let Some(required) = parameters["required"].as_array_mut() {
        required.push(Json::from(name));
    }
}

/// `_participant_artifact_tool(role=..., publisher=..., schemas=..., checking=...)`: typed
/// character inputs shared by publication and an independently bound draft check.
pub fn participant_artifact_tool(
    role: Option<&str>,
    publisher: ParticipantPublisher,
    schemas: &IndexMap<String, JsonMap>,
    checking: bool,
) -> CodedResult<ToolSpec> {
    if role.is_none() && !checking {
        return Err(CodedError::rejected("Character publication must be bound to this chat's role")
            .with_error_type("ValueError"));
    }
    let definition = artifact_definition(role, schemas, checking);
    let schema_ids: Vec<String> = schemas.keys().cloned().collect();
    let role = role.map(str::to_owned);
    let handler: ToolHandler = Arc::new(move |params: &JsonMap| {
        let mut required = vec!["summary", "authored_profile", "participant", "entity_state"];
        if role.is_none() {
            required.push("role");
        }
        let optional = ["schema_id", "setting_data", "source_profile"];
        let schema_known =
            |value: &Json| value.as_str().is_some_and(|id| schema_ids.iter().any(|known| known == id));
        let given_role = text(params, "role");
        if !required.iter().all(|key| params.contains_key(*key))
            || params.keys().any(|key| !required.contains(&key.as_str()) && !optional.contains(&key.as_str()))
            || params.get("schema_id").is_some_and(|value| !schema_known(value))
            || (schema_ids.len() > 1 && !params.contains_key("schema_id"))
            || (role.is_none() && !matches!(given_role, Some("player_character" | "companion")))
        {
            return Ok(artifact_invalid());
        }
        let summary = text(params, "summary").map(pystr::strip).filter(|value| !value.is_empty());
        let authored_profile =
            text(params, "authored_profile").map(pystr::strip).filter(|value| !value.is_empty());
        let (Some(summary), Some(authored_profile), Some(Json::Object(participant))) =
            (summary, authored_profile, params.get("participant"))
        else {
            return Ok(artifact_invalid());
        };
        let Some(entity_state) = participant_entity_state(params.get("entity_state").unwrap_or(&Json::Null))
        else {
            return Ok(Json::Object(object(vec![
                ("success", Json::Bool(false)),
                ("status", "rejected".into()),
                ("code", "participant_artifact_invalid".into()),
                ("state_changed", Json::Bool(false)),
                (
                    "message",
                    "Correct entity_state and retry: supply one JSON object encoded as text, with unique keys and \
                     finite values, at most 256 KiB and 32 nested levels. Preserve the Ruleset's number, boolean, \
                     array and object types."
                        .into(),
                ),
            ])));
        };
        let default_setting = Json::from("{}");
        let Some(setting_data) =
            participant_entity_state(params.get("setting_data").unwrap_or(&default_setting))
        else {
            return Ok(Json::Object(object(vec![
                ("success", Json::Bool(false)),
                ("status", "rejected".into()),
                ("state_changed", Json::Bool(false)),
                ("code", "participant_artifact_invalid".into()),
                (
                    "message",
                    "Correct setting_data: supply one JSON object encoded as text, at most 256 KiB of UTF-8 and 32 \
                     nested levels."
                        .into(),
                ),
            ])));
        };
        let input = ParticipantInput {
            role: role.clone().or_else(|| given_role.map(str::to_owned)).unwrap_or_default(),
            summary: summary.to_owned(),
            authored_profile: authored_profile.to_owned(),
            participant: participant.clone(),
            entity_state,
            schema_id: text(params, "schema_id").map(str::to_owned),
            setting_data: params.contains_key("setting_data").then_some(setting_data),
            source_profile: params.get("source_profile").cloned(),
        };
        match publisher(&input) {
            Ok(result) => {
                let status = if checking { "valid_draft" } else { "published" };
                Ok(merged(object(vec![("success", Json::Bool(true)), ("status", status.into())]), result))
            }
            Err(DomainError::Issues { error_type: "ParticipantValidationError", issues, .. }) => {
                let issues = issues
                    .iter()
                    .map(|issue| {
                        let mut payload = issue.as_payload();
                        payload.insert("parameter".into(), "participant".into());
                        Json::Object(payload)
                    })
                    .collect();
                Ok(Json::Object(object(vec![
                    ("success", Json::Bool(false)),
                    ("status", "rejected".into()),
                    ("code", "participant_artifact_invalid".into()),
                    ("state_changed", Json::Bool(false)),
                    ("message", "Correct participant against the selected Ruleset schema and retry.".into()),
                    ("validation_issues", Json::Array(issues)),
                ])))
            }
            Err(error) => match py_family(&error) {
                PyFamily::CommandRejected => Ok(Json::Object(object(vec![
                    ("success", Json::Bool(false)),
                    ("status", "rejected".into()),
                    ("code", error.code().unwrap_or("command_rejected").into()),
                    ("state_changed", Json::Bool(false)),
                    ("message", error.message().into()),
                    ("validation_errors", diagnostics(&error)),
                ]))),
                PyFamily::Authority | PyFamily::Os | PyFamily::Unicode | PyFamily::Value | PyFamily::Type => {
                    Ok(artifact_invalid())
                }
                PyFamily::Other => Err(error.message().to_owned()),
            },
        }
    });
    ToolSpec::from_openai_schema(&definition, handler, &[ToolsetName::BUILDER_TOOLS])
}

/// `CommandRejected(message)`, as the draft check raises it.
fn command_rejected(message: &str) -> DomainError {
    DomainError::command_rejected_with(message, "command_rejected", Vec::new())
}

/// `_participant_draft_check_tool(role, declarations, setting_character_schema,
/// resolve_source_profile)`: the shared validation path, with no storage or publication.
pub fn participant_draft_check_tool(
    role: Option<&str>,
    declarations: &[ParticipantArtifactDeclaration],
    setting_character_schema: Option<JsonMap>,
    resolve_source_profile: Option<SourceProfileResolver>,
) -> CodedResult<ToolSpec> {
    let owned = declarations.to_vec();
    let check: ParticipantPublisher = Arc::new(move |input: &ParticipantInput| {
        match &resolve_source_profile {
            Some(resolve) => {
                resolve(input.source_profile.as_ref())?;
            }
            None if input.source_profile.is_some() => {
                return Err(command_rejected("Source profile verification is unavailable"));
            }
            None => {}
        }
        let selected: Vec<&ParticipantArtifactDeclaration> = owned
            .iter()
            .filter(|declaration| input.schema_id.as_deref().is_none_or(|id| declaration.schema_id == id))
            .collect();
        let [declaration] = selected.as_slice() else {
            return Err(command_rejected("Choose one of this Ruleset's participant schema IDs"));
        };
        let setting_data = input.setting_data.clone().map(Json::Object);
        let (_profile, _participant, state, world_data) = validate_reusable_participant_draft(
            &input.role,
            &input.summary,
            &Json::from(input.authored_profile.as_str()),
            &Json::Object(input.participant.clone()),
            &Json::Object(input.entity_state.clone()),
            declaration,
            setting_data.as_ref(),
            setting_character_schema.as_ref(),
        )?;
        Ok(object(vec![
            ("published", Json::Bool(false)),
            ("state_changed", Json::Bool(false)),
            ("complete_entity_state", Json::Object(state)),
            ("setting_data", Json::Object(world_data)),
        ]))
    });
    // `{declaration.schema_id: declaration.schema for declaration in declarations}`.
    let mut schemas: IndexMap<String, JsonMap> = IndexMap::new();
    for declaration in declarations {
        schemas.insert(declaration.schema_id.clone(), declaration.schema.clone());
    }
    participant_artifact_tool(role, check, &schemas, true)
}

/// Read at most `limit + 1` bytes of `path`.
fn read_capped(path: &Path, limit: usize) -> std::io::Result<Vec<u8>> {
    let mut data = Vec::new();
    std::fs::File::open(path)?.take(limit as u64 + 1).read_to_end(&mut data)?;
    Ok(data)
}

/// `participant_file_tool(inline_tool, workspace, decode)`: validate or publish the exact
/// workspace draft through the inline tool, without the model retransmitting its body.
pub fn participant_file_tool(inline: &ToolSpec, workspace: &Path) -> CodedResult<ToolSpec> {
    let checking = inline.name == "check_participant_draft";
    let name = if checking { "check_participant_draft_file" } else { "publish_participant_artifact_file" };
    let guardrail = PathGuardrail::new(workspace);
    let inline_handler = Arc::clone(&inline.handler);
    let handler: ToolHandler = Arc::new(move |params: &JsonMap| {
        let rejected = |code: &str, message: Option<&str>| {
            let mut answer = object(vec![
                ("success", Json::Bool(false)),
                ("status", "rejected".into()),
                ("state_changed", Json::Bool(false)),
                ("code", code.into()),
            ]);
            if let Some(message) = message {
                answer.insert("message".into(), message.into());
            }
            Json::Object(answer)
        };
        let keys_ok =
            params.len() == 2 && params.contains_key("path") && params.contains_key("expected_version");
        let path = text(params, "path").filter(|path| !pystr::strip(path).is_empty());
        let expected = text(params, "expected_version").filter(|version| lower_hex(version, 64));
        let (true, Some(path), Some(expected)) = (keys_ok, path, expected) else {
            return Ok(rejected("participant_file_parameters_invalid", None));
        };
        let Ok(target) = guardrail.validate(path, false) else {
            return Ok(rejected("participant_file_path_denied", None));
        };
        let invalid = || {
            rejected(
                "participant_file_invalid",
                Some(
                    "Supply one UTF-8 JSON draft object, with unique keys and finite values, at most 256 KiB and 32 \
                     nested levels.",
                ),
            )
        };
        let Ok(data) = read_capped(&target, MAX_DRAFT_BYTES) else { return Ok(invalid()) };
        if data.len() > MAX_DRAFT_BYTES {
            return Ok(invalid());
        }
        let version = content_version(&data);
        if version != expected {
            return Ok(rejected(
                "stale_file",
                Some("Draft changed. Read and reconcile it before checking or publishing again."),
            ));
        }
        let Ok(decoded) = String::from_utf8(data) else { return Ok(invalid()) };
        let Some(mut draft) = participant_entity_state(&Json::String(decoded)) else { return Ok(invalid()) };
        for key in ["entity_state", "setting_data"] {
            if let Some(Json::Object(_)) = draft.get(key) {
                let encoded = pyjson::dumps(&draft[key], Dump::DEFAULT.ascii(false));
                draft.insert(key.into(), Json::String(encoded));
            }
        }
        let result = inline_handler(&draft)?;
        let Json::Object(mut result) = result else { return Ok(result) };
        result.insert("draft_path".into(), workspace_path(&guardrail, &target).into());
        result.insert("draft_version".into(), version.into());
        Ok(Json::Object(result))
    });
    let action = if checking { "Check" } else { "Validate and save" };
    let definition = serde_json::json!({"type": "function", "function": {
        "name": name,
        "description": format!(
            "{action} the exact JSON character draft in this Builder workspace using its observed \
             read_file/write_file/edit_file/copy_builder_reference version. Same validation and role as the inline \
             character tool; no need to repeat the full draft. The file contains summary, authored_profile, \
             participant, entity_state, optional setting_data/schema_id/source_profile. State and Setting data may \
             be objects. A check does not save or change a campaign. Maximum 256 KiB."
        ),
        "parameters": {"type": "object", "additionalProperties": false,
                       "required": ["path", "expected_version"], "properties": {
                           "path": {"type": "string"},
                           "expected_version": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                       }},
    }});
    let toolsets: Vec<&'static str> = inline.toolsets.iter().copied().collect();
    ToolSpec::from_openai_schema(&definition, handler, &toolsets)
}

/// The `schema_id` property of the draft and reuse tools, required when there are several.
fn schema_property(properties: &mut JsonMap, required: &mut Vec<&'static str>, schema_ids: &[String]) {
    if !schema_ids.is_empty() {
        properties.insert("schema_id".into(), serde_json::json!({"type": "string", "enum": schema_ids}));
        if schema_ids.len() > 1 {
            required.push("schema_id");
        }
    }
}

/// The common checks of the draft and reuse tools' parameters.
fn exact_source_params(
    params: &JsonMap,
    required: &[&str],
    properties: &JsonMap,
    schema_ids: &[String],
) -> bool {
    required.iter().all(|key| params.contains_key(*key))
        && params.keys().all(|key| properties.contains_key(key))
        && text(params, "artifact_id").is_some_and(is_participant_id)
        && text(params, "expected_content_sha256").is_some_and(|digest| lower_hex(digest, 64))
        && params
            .get("schema_id")
            .is_none_or(|value| value.as_str().is_some_and(|id| schema_ids.iter().any(|known| known == id)))
}

/// `character_draft_tool(read, workspace, schema_ids)`: open an exact saved character as an
/// editable, unpublished workspace draft, never overwriting an unobserved local file.
pub fn character_draft_tool(
    read: ParticipantReader,
    workspace: &Path,
    schema_ids: &[String],
) -> CodedResult<ToolSpec> {
    let guardrail = PathGuardrail::new(workspace);
    let mut properties = object(vec![
        ("artifact_id", serde_json::json!({"type": "string"})),
        ("expected_content_sha256", serde_json::json!({"type": "string"})),
        ("destination_path", serde_json::json!({"type": "string"})),
        ("expected_version", serde_json::json!({"type": "string"})),
    ]);
    let mut required = vec!["artifact_id", "expected_content_sha256", "destination_path"];
    schema_property(&mut properties, &mut required, schema_ids);
    let definition = serde_json::json!({"type": "function", "function": {
        "name": "open_character_draft",
        "description": "Open a saved character for an explicitly requested repair or adaptation. Get its exact \
            artifact ID and content_sha256 from discover_campaign_content. Copies the full character into a new JSON \
            workspace file; originals remain unchanged. Existing destinations require their observed read_file \
            version. Use read_file and edit_file to preserve other choices. Calculated state is omitted for \
            recalculation. This does not validate or save a library character: check the draft against this \
            chat's selected Setting and Ruleset, then use check_participant_draft_file and \
            publish_participant_artifact_file with the draft path and observed version. Inline character tools \
            remain available. A different Setting may require explicit adaptation; never invent missing choices.",
        "parameters": {"type": "object", "properties": properties.clone(), "required": required.clone(), "additionalProperties": false},
    }});
    let schema_ids = schema_ids.to_vec();
    let handler: ToolHandler = Arc::new(move |params: &JsonMap| {
        let refused =
            |code: &str| Json::Object(object(vec![("success", Json::Bool(false)), ("code", code.into())]));
        let all_text = params.values().all(Json::is_string);
        if !all_text
            || !exact_source_params(params, &required, &properties, &schema_ids)
            || text(params, "destination_path").is_none_or(|path| pystr::strip(path).is_empty())
        {
            return Ok(refused("character_draft_parameters_invalid"));
        }
        let destination = text(params, "destination_path").unwrap_or_default();
        let Ok(target) = guardrail.validate(destination, true) else {
            return Ok(refused("character_draft_path_denied"));
        };
        let outcome = read(
            text(params, "artifact_id").unwrap_or_default(),
            text(params, "expected_content_sha256").unwrap_or_default(),
            text(params, "schema_id"),
        )
        .and_then(|draft| {
            let content = draft_text(&draft);
            let version = write_text(&target, &content, text(params, "expected_version"))?;
            Ok((content, version))
        });
        let (content, version) = match outcome {
            Ok(written) => written,
            Err(error) if error.python_class() == "StaleWorkspaceFileError" => {
                return Ok(Json::Object(object(vec![
                    ("success", Json::Bool(false)),
                    ("error_code", "stale_file".into()),
                    ("error", error.message().into()),
                ])));
            }
            Err(error) => match py_family(&error) {
                PyFamily::CommandRejected => {
                    return Ok(Json::Object(object(vec![
                        ("success", Json::Bool(false)),
                        ("code", error.code().unwrap_or("command_rejected").into()),
                        ("message", error.message().into()),
                    ])));
                }
                PyFamily::Os | PyFamily::Unicode | PyFamily::Type | PyFamily::Value => {
                    return Ok(refused("character_draft_unavailable"));
                }
                PyFamily::Authority | PyFamily::Other => return Err(error.message().to_owned()),
            },
        };
        Ok(Json::Object(object(vec![
            ("success", Json::Bool(true)),
            ("published", Json::Bool(false)),
            ("source_artifact_id", params["artifact_id"].clone()),
            ("source_content_sha256", params["expected_content_sha256"].clone()),
            ("path", workspace_path(&guardrail, &target).into()),
            ("version", version.into()),
            ("bytes_written", content.len().into()),
        ])))
    });
    ToolSpec::from_openai_schema(&definition, handler, &[ToolsetName::BUILDER_TOOLS])
}

/// `reuse_character_copy_tool(reuse, schema_ids)`: an explicit compatible copy into this chat's
/// saved Setting, the destination bound outside model input.
pub fn reuse_character_copy_tool(reuse: ParticipantReader, schema_ids: &[String]) -> CodedResult<ToolSpec> {
    let mut properties = object(vec![
        (
            "artifact_id",
            serde_json::json!({"type": "string", "description": "Existing character ID from the local content catalog."}),
        ),
        (
            "expected_content_sha256",
            serde_json::json!({"type": "string",
                "description": "The exact content_sha256 returned for that catalog character."}),
        ),
    ]);
    let mut required = vec!["artifact_id", "expected_content_sha256"];
    schema_property(&mut properties, &mut required, schema_ids);
    let definition = serde_json::json!({"type": "function", "function": {
        "name": "reuse_character_copy",
        "description": "On the user's explicit request, reuse one saved character unchanged in this chat's selected \
            Setting. Call discover_campaign_content with the character's name to obtain its ID and content_sha256 \
            from the local catalog; chat history is not the saved-character catalog. Checks the new exact Setting \
            and contained Ruleset and saves a compatible copy only when every requirement already holds. Original \
            files and campaign characters remain unchanged. If adaptation is required, explain the missing choices \
            and ask before changing them in a new Builder draft. Never use this to rebind all characters \
            automatically after a Setting update.",
        "parameters": {"type": "object", "properties": properties.clone(), "required": required.clone(), "additionalProperties": false},
    }});
    let schema_ids = schema_ids.to_vec();
    let handler: ToolHandler = Arc::new(move |params: &JsonMap| {
        if !exact_source_params(params, &required, &properties, &schema_ids) {
            return Ok(Json::Object(object(vec![
                ("success", Json::Bool(false)),
                ("code", "character_reuse_parameters_invalid".into()),
            ])));
        }
        let outcome = reuse(
            text(params, "artifact_id").unwrap_or_default(),
            text(params, "expected_content_sha256").unwrap_or_default(),
            text(params, "schema_id"),
        );
        match outcome {
            Ok(result) => {
                Ok(merged(object(vec![("success", Json::Bool(true)), ("status", "copied".into())]), result))
            }
            Err(error) => match py_family(&error) {
                PyFamily::CommandRejected => Ok(Json::Object(object(vec![
                    ("success", Json::Bool(false)),
                    ("status", "rejected".into()),
                    ("state_changed", Json::Bool(false)),
                    ("code", error.code().unwrap_or("command_rejected").into()),
                    ("message", error.message().into()),
                    ("validation_errors", diagnostics(&error)),
                ]))),
                PyFamily::Os | PyFamily::Unicode | PyFamily::Type | PyFamily::Value => {
                    Ok(Json::Object(object(vec![
                        ("success", Json::Bool(false)),
                        ("status", "rejected".into()),
                        ("state_changed", Json::Bool(false)),
                        ("code", "character_reuse_unavailable".into()),
                        ("message", "Refresh the source character and correct its file before reuse.".into()),
                    ])))
                }
                PyFamily::Authority | PyFamily::Other => Err(error.message().to_owned()),
            },
        }
    });
    ToolSpec::from_openai_schema(&definition, handler, &[ToolsetName::BUILDER_TOOLS])
}
