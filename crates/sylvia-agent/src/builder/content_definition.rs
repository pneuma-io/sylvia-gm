//! `agent/_content_definition_tool.py`: read installed authoring definitions without inspecting
//! user files or changing a session (B5).
//!
//! With the chat's participant declarations, the catalog also offers `character_creation`: the
//! exact requirements of this Setting's contained Ruleset, with each declaration's authored
//! entity-state inputs (`initial_character_input_schema`).

use super::tools::{ToolHandler, ToolSpec, ToolsetName};
use std::sync::Arc;
use sylvia_app::initial_character_state::initial_character_input_schema;
use sylvia_base::hash::sha256_hex;
use sylvia_base::{pyjson, pystr, CodedResult, Json, JsonMap};
use sylvia_domain::content::catalog::{get_content_definition, list_content_definitions};
use sylvia_domain::package_library::{canonical_json_bytes, ParticipantArtifactDeclaration};

fn object(pairs: Vec<(&str, Json)>) -> JsonMap {
    pairs.into_iter().map(|(key, value)| (key.to_owned(), value)).collect()
}

fn refused(code: &str) -> Json {
    Json::Object(object(vec![("success", Json::Bool(false)), ("code", code.into())]))
}

/// `json.loads(canonical_json_bytes(value))`: the value with every object's keys sorted.
fn canonical(value: &Json) -> Json {
    let bytes = canonical_json_bytes(value);
    pyjson::loads(&String::from_utf8_lossy(&bytes)).unwrap_or(Json::Null)
}

/// The `character_creation` definition of `declarations`.
fn character_creation(
    declarations: &[ParticipantArtifactDeclaration],
    setting_character_schema: Option<&JsonMap>,
) -> Result<Json, String> {
    let mut entries = Vec::new();
    for declaration in declarations {
        let complete = declaration.entity_state_schema.is_some();
        let calculations: Vec<Json> =
            declaration.initial_state_calculations.iter().cloned().map(Json::Object).collect();
        let mut entry = object(vec![
            ("kind", declaration.kind.as_str().into()),
            ("schema_id", declaration.schema_id.as_str().into()),
            ("schema_sha256", declaration.schema_sha256.as_str().into()),
            ("participant", Json::Object(declaration.schema.clone())),
            ("entity_state_schema", declaration.entity_state_schema.clone().map_or(Json::Null, Json::Object)),
            ("initial_state_calculations", Json::Array(calculations.clone())),
        ]);
        if let Some(validation) = &declaration.initial_state_validation {
            // `initial_state_validation["description"]`: Python's `KeyError` without one.
            let description = validation.get("description").ok_or_else(|| "'description'".to_owned())?;
            entry.insert("validation_requirements".into(), description.clone());
        }
        entry.insert("creation_available".into(), Json::Bool(complete));
        let authored = match &declaration.entity_state_schema {
            Some(schema) => Json::Object(
                initial_character_input_schema(schema, &calculations)
                    .map_err(|error| error.message().to_owned())?,
            ),
            None => Json::Null,
        };
        entry.insert("authored_entity_state".into(), authored);
        entries.push(Json::Object(entry));
    }
    let setting_data = match setting_character_schema {
        Some(schema) => Json::Object(schema.clone()),
        None => serde_json::json!({"type": "object", "properties": {}, "additionalProperties": false}),
    };
    let requirements = [
        "Requirements come from this chat's saved Setting and contained Ruleset.",
        "Supply authored choices and facts; omit calculated output fields.",
        "Supply Setting-specific fields separately in setting_data; both apply.",
        "Sylvia evaluates the ordered formulas and validates the complete result.",
        "Report missing declarations or choices; do not assume defaults.",
    ];
    Ok(Json::Object(object(vec![
        ("success", Json::Bool(true)),
        (
            "definition",
            Json::Object(object(vec![
                ("name", "character_creation".into()),
                ("declarations", canonical(&Json::Array(entries))),
                ("setting_data", canonical(&setting_data)),
                ("requirements", Json::Array(requirements.iter().map(|item| Json::from(*item)).collect())),
            ])),
        ),
    ])))
}

/// `read_full(params)`: the catalog, or one whole definition.
fn read_full(
    params: &JsonMap,
    declarations: &[ParticipantArtifactDeclaration],
    setting_character_schema: Option<&JsonMap>,
) -> Result<Json, String> {
    if params.keys().any(|key| key != "name") || params.get("name").is_some_and(|name| !name.is_string()) {
        return Ok(refused("content_definition_parameters_invalid"));
    }
    let Some(name) = params.get("name").and_then(Json::as_str) else {
        let mut catalog: Vec<Json> = list_content_definitions().into_iter().map(Json::Object).collect();
        if !declarations.is_empty() {
            catalog.push(serde_json::json!({
                "name": "character_creation",
                "title": "This Setting's exact character creation requirements",
            }));
        }
        return Ok(Json::Object(object(vec![
            ("success", Json::Bool(true)),
            ("definitions", Json::Array(catalog)),
        ])));
    };
    if name == "character_creation" && !declarations.is_empty() {
        return character_creation(declarations, setting_character_schema);
    }
    match get_content_definition(name) {
        Ok(definition) => Ok(Json::Object(object(vec![
            ("success", Json::Bool(true)),
            ("definition", Json::Object(definition)),
        ]))),
        Err(error) if error.is_value_error() => Ok(Json::Object(object(vec![
            ("success", Json::Bool(false)),
            ("code", "content_definition_unknown".into()),
            ("message", "Use a name from the installed content definition catalog.".into()),
        ]))),
        Err(error) => Err(error.message().to_owned()),
    }
}

/// `int(token)` for a token `str.isdecimal()` accepts (any Unicode decimal digits), as an index.
fn decimal_index(token: &str) -> Option<usize> {
    if token.is_empty() || !token.chars().all(|c| pystr::decimal(c).is_some()) {
        return None;
    }
    // Out of `i64` is Python's `IndexError` too: the index cannot exist.
    pystr::parse_int(token).ok().and_then(|index| usize::try_from(index).ok())
}

/// One JSON Pointer `section` of `definition`, or `None` where Python raises `KeyError`,
/// `IndexError` or `ValueError`.
fn pointer<'a>(definition: &'a Json, section: &str) -> Option<&'a Json> {
    let mut value = definition;
    for token in section[1..].split('/') {
        let token = token.replace("~1", "/").replace("~0", "~");
        value = match value {
            Json::Array(items) => items.get(decimal_index(&token)?)?,
            Json::Object(map) => map.get(&token)?,
            _ => return None,
        };
    }
    Some(value)
}

/// `content_definition_tool(participant_declarations, setting_character_schema)`: one
/// on-demand definition, a section of one, or the small catalog of available formats.
pub fn content_definition_tool(
    declarations: &[ParticipantArtifactDeclaration],
    setting_character_schema: Option<JsonMap>,
) -> CodedResult<ToolSpec> {
    let declarations = declarations.to_vec();
    let handler: ToolHandler = Arc::new(move |params: &JsonMap| {
        if params.keys().any(|key| key != "name" && key != "section") {
            return Ok(refused("content_definition_parameters_invalid"));
        }
        let name_only: JsonMap =
            params.iter().filter(|(key, _)| *key == "name").map(|(k, v)| (k.clone(), v.clone())).collect();
        let result = read_full(&name_only, &declarations, setting_character_schema.as_ref())?;
        let succeeded = result.get("success").and_then(Json::as_bool).unwrap_or(false);
        let Some(section) = params.get("section") else { return Ok(result) };
        if !succeeded {
            return Ok(result);
        }
        let definition = result.get("definition");
        let (Json::String(section), Some(definition)) = (section, definition) else {
            return Ok(refused("content_definition_section_invalid"));
        };
        if !section.starts_with('/') {
            return Ok(refused("content_definition_section_invalid"));
        }
        let Some(value) = pointer(definition, section) else {
            return Ok(refused("content_definition_section_unknown"));
        };
        Ok(Json::Object(object(vec![
            ("success", Json::Bool(true)),
            ("name", params["name"].clone()),
            ("section", section.as_str().into()),
            ("value", value.clone()),
            ("definition_sha256", sha256_hex(canonical_json_bytes(definition)).into()),
        ])))
    });
    ToolSpec::from_openai_schema(
        super::turn::exported_definition("get_content_definition"),
        handler,
        &[ToolsetName::BUILDER_TOOLS],
    )
}
