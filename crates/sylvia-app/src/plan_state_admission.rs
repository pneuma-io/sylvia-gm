//! `application/plan_state_admission.py`: check a Plan's package-owned starting state without
//! changing or publishing its values. Also `plan_spatial_admission._plan_state`, which it reads.

use serde_json::json;
use std::collections::HashMap;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::content::ContentShapeError;
use sylvia_domain::error::{DomainError, DomainResult, RulesetDiagnostic};
use sylvia_domain::package_library::PackageManifest;
use sylvia_domain::schema_admission::validate_parameters;
use sylvia_domain::starting_state::StateSchema;

/// The most values one starting-state reference may select.
const MAXIMUM_MATCHES: usize = 8192;

/// A source binding: a member's dict key or list index.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
enum Key {
    Text(String),
    Index(usize),
}

impl Key {
    fn json(&self) -> Json {
        match self {
            Key::Text(text) => Json::String(text.clone()),
            Key::Index(index) => json!(index),
        }
    }
}

/// `{name: binding}` in insertion order, as Python's dict keeps it.
type Bindings = Vec<(String, Key)>;

/// `_plan_state(payload)`: Plan mechanics in the runtime surface state shape, with source paths.
pub fn plan_state(payload: &JsonMap) -> (JsonMap, HashMap<String, String>) {
    let mut state = JsonMap::new();
    let mut paths = HashMap::new();
    let mechanics = match payload.get("initial_state") {
        Some(Json::Object(initial)) => initial.get("mechanics"),
        _ => None,
    };
    let Some(Json::Array(mechanics)) = mechanics else { return (state, paths) };
    for (index, item) in mechanics.iter().enumerate() {
        let Json::Object(item) = item else { continue };
        if let (Some(Json::String(namespace)), Some(Json::String(key)), Some(value)) =
            (item.get("namespace"), item.get("key"), item.get("value"))
        {
            let identity = format!("{namespace}.{key}");
            state.insert(identity.clone(), value.clone());
            paths.insert(identity, format!("$.initial_state.mechanics[{index}].value"));
        }
    }
    (state, paths)
}

/// `_reject(path, message)`: an authoring location and correction, never a private value.
fn reject(path: &str, message: &str) -> DomainError {
    ContentShapeError::new(vec![RulesetDiagnostic {
        code: "plan_starting_state_invalid".into(),
        phase: "content".into(),
        file: Some("plan.json".into()),
        path: Some(path.into()),
        message: message.into(),
        guidance: Some("Correct this field using the Ruleset's state_schemas; no state changed.".into()),
        ..Default::default()
    }])
    .into()
}

struct Matches<'a> {
    found: Vec<(Json, String, Bindings)>,
    path: &'a str,
}

impl Matches<'_> {
    fn push(&mut self, value: Json, path: String, bindings: Bindings) -> DomainResult<()> {
        if self.found.len() >= MAXIMUM_MATCHES {
            return Err(reject(self.path, "A starting-state reference selects more than 8192 values."));
        }
        self.found.push((value, path, bindings));
        Ok(())
    }

    /// `_select(value, parts, path, bindings, source=..., key=...)`: literal fields, collection
    /// members and named source-key bindings only.
    fn select(
        &mut self,
        value: &Json,
        parts: &[String],
        path: &str,
        bindings: &Bindings,
        source: bool,
        key: Option<&Key>,
    ) -> DomainResult<()> {
        let Some((part, rest)) = parts.split_first() else {
            return self.push(value.clone(), path.to_owned(), bindings.clone());
        };
        if part == "$key" {
            if let Some(key) = key {
                self.push(key.json(), path.to_owned(), bindings.clone())?;
            }
            return Ok(());
        }
        let capture = source && part.starts_with('$');
        if part == "*" || capture {
            let members: Vec<(Key, &Json)> = match value {
                Json::Object(map) => map.iter().map(|(k, v)| (Key::Text(k.clone()), v)).collect(),
                Json::Array(items) => items.iter().enumerate().map(|(i, v)| (Key::Index(i), v)).collect(),
                _ => Vec::new(),
            };
            for (member_key, member) in members {
                let mut next = bindings.clone();
                if capture {
                    match next.iter_mut().find(|(name, _)| name == part) {
                        Some(entry) => entry.1 = member_key.clone(),
                        None => next.push((part.clone(), member_key.clone())),
                    }
                }
                let child = match &member_key {
                    Key::Index(index) => format!("{path}[{index}]"),
                    Key::Text(text) => format!("{path}.{text}"),
                };
                self.select(member, rest, &child, &next, source, Some(&member_key))?;
            }
            return Ok(());
        }
        let selected = if part.starts_with('$') {
            // `bindings[part]`: a target selector naming a binding the source did not capture.
            bindings
                .iter()
                .find(|(name, _)| name == part)
                .map(|(_, key)| key.clone())
                .ok_or_else(|| DomainError::uncaught("KeyError", sylvia_base::pystr::repr(part)))?
        } else {
            Key::Text(part.clone())
        };
        match (value, &selected) {
            (Json::Object(map), Key::Text(name)) if map.contains_key(name) => {
                self.select(&map[name], rest, &format!("{path}.{name}"), bindings, source, Some(&selected))
            }
            (Json::Array(items), Key::Index(index)) if *index < items.len() => self.select(
                &items[*index],
                rest,
                &format!("{path}[{index}]"),
                bindings,
                source,
                Some(&selected),
            ),
            _ => Ok(()),
        }
    }
}

/// `_matches(value, selector, path, bindings, source=...)`: bounded even when the package schema
/// leaves a collection's size open.
fn matches(
    value: &Json,
    selector: &[String],
    path: &str,
    bindings: &Bindings,
    source: bool,
) -> DomainResult<Vec<(Json, String, Bindings)>> {
    let mut found = Matches { found: Vec::new(), path };
    found.select(value, selector, path, bindings, source, None)?;
    Ok(found.found)
}

/// `type(selected) is type(candidate) and selected == candidate` for text or integers.
fn same_record(selected: &Json, candidate: &Json) -> bool {
    match (selected, candidate) {
        (Json::String(a), Json::String(b)) => a == b,
        (Json::Number(a), Json::Number(b)) if (a.is_i64() || a.is_u64()) && (b.is_i64() || b.is_u64()) => {
            a == b
        }
        _ => false,
    }
}

/// `validate_plan_state(payload, manifest)`: exact-key schemas and references inside the
/// inherited Ruleset's namespace.
pub fn validate_plan_state(payload: &JsonMap, manifest: &PackageManifest) -> DomainResult<()> {
    validate_starting_declarations(payload, &manifest.package_id, &manifest.version, &manifest.state_schemas)
}

/// `validate_starting_declarations(payload, package_id, version, declarations)`: the retained
/// declaration checks Builder also runs before publication.
pub fn validate_starting_declarations(
    payload: &JsonMap,
    package_id: &str,
    version: &str,
    declarations: &[StateSchema],
) -> DomainResult<()> {
    let (state, paths) = plan_state(payload);
    for declaration in declarations {
        let identity = format!("{package_id}.{}", declaration.key);
        let Some(value) = state.get(&identity) else {
            if declaration.required {
                return Err(reject(
                    "$.initial_state.mechanics",
                    &format!(
                        "Supply required starting key '{}' in namespace '{package_id}' from retained Ruleset {version}. \
                         Read its state_schemas for the required fields.",
                        declaration.key
                    ),
                ));
            }
            continue;
        };
        let path = &paths[&identity];
        let mut parameters = JsonMap::new();
        parameters.insert("value".into(), value.clone());
        let schema = json!({
            "type": "object",
            "properties": {"value": declaration.schema},
            "required": ["value"],
            "additionalProperties": false,
        });
        if let Err(error) = validate_parameters(&parameters, schema.as_object().expect("an object")) {
            let Some(mechanics) = error.mechanics() else { return Err(error) };
            let issue = &mechanics.validation_issues[0];
            let tail = issue.path.strip_prefix("$.").unwrap_or(&issue.path);
            let tail = tail.strip_prefix("value").unwrap_or(tail);
            return Err(reject(
                &format!("{path}{tail}"),
                &format!("Starting field violates its Ruleset schema ({}).", issue.kind.replace('_', " ")),
            ));
        }
        for reference in &declaration.references {
            let target =
                state.get(&format!("{package_id}.{}", reference.target_key)).cloned().unwrap_or(Json::Null);
            let mut cache: Vec<(Bindings, Vec<Json>)> = Vec::new();
            for (selected, selected_path, bindings) in
                matches(value, &reference.source, path, &Vec::new(), true)?
            {
                if selected.is_null() {
                    continue;
                }
                let scope: Bindings =
                    bindings.iter().filter(|(name, _)| reference.target.contains(name)).cloned().collect();
                if !cache.iter().any(|(existing, _)| *existing == scope) {
                    let candidates = matches(&target, &reference.target, path, &bindings, false)?
                        .into_iter()
                        .map(|(item, _, _)| item)
                        .collect();
                    cache.push((scope.clone(), candidates));
                }
                let candidates = &cache.iter().find(|(existing, _)| *existing == scope).expect("cached").1;
                if !candidates.iter().any(|candidate| same_record(&selected, candidate)) {
                    return Err(reject(
                        &selected_path,
                        "Reference must identify an existing record in its Ruleset-declared target collection.",
                    ));
                }
            }
        }
    }
    Ok(())
}
