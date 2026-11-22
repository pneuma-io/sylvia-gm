//! `application/player_actions.py` above the store. The owner marker checks the store needs
//! are `sylvia_domain::player_actions`; this module has the Ruleset's direct-control policy.

use sylvia_base::pystr::strip;
use sylvia_base::{Json, JsonMap};

/// `PlayerControls`: the Ruleset policy for confirming one direct action and requesting its GM
/// response. `available` is false when the declaration is malformed.
#[derive(Debug, Clone, PartialEq)]
pub struct PlayerControls {
    pub confirmation: String,
    pub response: String,
    pub preview: Option<JsonMap>,
    pub actor_parameter: Option<String>,
    pub checks: Vec<JsonMap>,
    pub queries: Vec<JsonMap>,
    pub calculations: Vec<JsonMap>,
    pub map_preview: Option<JsonMap>,
    pub available: bool,
    pub preflight: Vec<String>,
    pub preflight_path: Option<JsonMap>,
}

impl Default for PlayerControls {
    fn default() -> Self {
        Self {
            confirmation: "none".into(),
            response: "on_request".into(),
            preview: None,
            actor_parameter: None,
            checks: Vec::new(),
            queries: Vec::new(),
            calculations: Vec::new(),
            map_preview: None,
            available: true,
            preflight: Vec::new(),
            preflight_path: None,
        }
    }
}

impl PlayerControls {
    fn unavailable() -> Self {
        Self { available: false, ..Self::default() }
    }
}

fn keys_are(object: &JsonMap, keys: &[&str]) -> bool {
    object.len() == keys.len() && keys.iter().all(|key| object.contains_key(*key))
}

fn ident_start(c: char) -> bool {
    c.is_ascii_alphabetic() || c == '_'
}

fn ident_char(c: char) -> bool {
    c.is_ascii_alphanumeric() || c == '_'
}

/// `_LOCAL_NAME.fullmatch(name)`: `_[A-Za-z][A-Za-z0-9_]{0,63}`.
fn local_name(name: &str) -> bool {
    let mut chars = name.chars();
    chars.next() == Some('_')
        && chars.next().is_some_and(|c| c.is_ascii_alphabetic())
        && name.chars().count() <= 65
        && name.chars().skip(2).all(ident_char)
}

/// `re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', name)`.
fn preflight_name(name: &str) -> bool {
    name.chars().next().is_some_and(ident_start)
        && name.chars().count() <= 128
        && name.chars().all(ident_char)
}

/// `re.fullmatch(r'state\.[A-Za-z_][A-Za-z0-9_.]{0,127}', scene)`.
fn preflight_scene(scene: &str) -> bool {
    let Some(rest) = scene.strip_prefix("state.") else {
        return false;
    };
    rest.chars().next().is_some_and(ident_start)
        && rest.chars().count() <= 128
        && rest.chars().all(|c| ident_char(c) || c == '.')
}

/// `len(text)` in code points.
fn chars(text: &str) -> usize {
    text.chars().count()
}

/// `_valid_control_queries(queries, calculations)`.
fn valid_control_queries(queries: &Json, calculations: &Json) -> bool {
    let (Json::Array(queries), Json::Array(calculations)) = (queries, calculations) else {
        return false;
    };
    if queries.len() > 8 || calculations.len() > 24 {
        return false;
    }
    let mut names: Vec<&str> = Vec::new();
    for item in queries {
        let Json::Object(item) = item else { return false };
        if !keys_are(item, &["spatial_query", "store_as"]) {
            return false;
        }
        let (Json::String(name), Json::Object(body)) = (&item["store_as"], &item["spatial_query"]) else {
            return false;
        };
        if !local_name(name)
            || names.contains(&name.as_str())
            || !keys_are(body, &["scene", "operation", "query"])
        {
            return false;
        }
        let scene_ok = matches!(&body["scene"], Json::String(scene) if scene.starts_with("state."));
        let operation_ok = matches!(&body["operation"], Json::String(op)
            if matches!(op.as_str(), "path" | "ray" | "exposure" | "area" | "crossed_zones"));
        let query_ok = matches!(&body["query"], Json::Object(query) if !query.contains_key("kind"));
        if !(scene_ok && operation_ok && query_ok) {
            return false;
        }
        names.push(name);
    }
    for item in calculations {
        let Json::Object(item) = item else { return false };
        if !item.contains_key("set") || item.keys().any(|key| key != "set" && key != "condition") {
            return false;
        }
        let Json::Object(body) = &item["set"] else { return false };
        if !keys_are(body, &["field", "value"]) {
            return false;
        }
        let condition = item.get("condition").cloned().unwrap_or_else(|| Json::String("true".into()));
        let (Json::String(name), Json::String(expression), Json::String(condition)) =
            (&body["field"], &body["value"], &condition)
        else {
            return false;
        };
        if !local_name(name)
            || names.contains(&name.as_str())
            || !(1..=500).contains(&chars(expression))
            || !(1..=500).contains(&chars(condition))
        {
            return false;
        }
        names.push(name);
    }
    true
}

/// `parse_player_controls(value)`: disable malformed direct controls without withdrawing the
/// underlying chat mechanic. `None` stands for an absent declaration.
pub fn parse_player_controls(value: Option<&Json>) -> PlayerControls {
    let value = match value {
        None | Some(Json::Null) => return PlayerControls::default(),
        Some(Json::Object(value)) => value,
        Some(_) => return PlayerControls::unavailable(),
    };
    const ALLOWED: [&str; 10] = [
        "confirmation",
        "response",
        "preview",
        "actor_parameter",
        "checks",
        "queries",
        "calculations",
        "map_preview",
        "preflight",
        "preflight_path",
    ];
    if value.keys().any(|key| !ALLOWED.contains(&key.as_str())) {
        return PlayerControls::unavailable();
    }
    let field = |name: &str| value.get(name).filter(|item| !item.is_null());
    let empty = Json::Array(Vec::new());
    let confirmation = value.get("confirmation").cloned().unwrap_or_else(|| Json::String("none".into()));
    let response = value.get("response").cloned().unwrap_or_else(|| Json::String("on_request".into()));
    let checks = value.get("checks").unwrap_or(&empty);
    let queries = value.get("queries").unwrap_or(&empty);
    let calculations = value.get("calculations").unwrap_or(&empty);
    if !valid_control_queries(queries, calculations) {
        return PlayerControls::unavailable();
    }
    let map_preview = field("map_preview");
    let preflight = value.get("preflight").unwrap_or(&empty);
    let preflight_path = field("preflight_path");
    let Json::Array(preflight) = preflight else {
        return PlayerControls::unavailable();
    };
    if preflight.len() > 4
        || preflight.iter().any(|name| !matches!(name, Json::String(name) if preflight_name(name)))
    {
        return PlayerControls::unavailable();
    }
    if let Some(path) = preflight_path {
        let valid = !preflight.is_empty()
            && matches!(path, Json::Object(path)
                if keys_are(path, &["scene", "route", "cost"])
                && matches!(&path["scene"], Json::String(scene) if preflight_scene(scene))
                && ["route", "cost"].iter().all(|key| matches!(&path[*key], Json::String(name) if local_name(name))))
            && map_preview.is_none();
        if !valid {
            return PlayerControls::unavailable();
        }
    }
    let queries = queries.as_array().expect("checked above");
    if let Some(map_preview) = map_preview {
        let kind_of = |name: &str| {
            queries
                .iter()
                .rev()
                .find(|item| item["store_as"] == name)
                .map(|item| &item["spatial_query"]["operation"])
        };
        let valid = matches!(map_preview, Json::Object(map) if !map.is_empty()
            && map.keys().all(|key| matches!(key.as_str(), "path" | "ray" | "exposure" | "crossed_zones"))
            && map.iter().all(|(kind, name)| matches!(name, Json::String(name)
                if kind_of(name).is_some_and(|op| op == kind))));
        if !valid {
            return PlayerControls::unavailable();
        }
    }
    let Json::Array(checks) = checks else {
        return PlayerControls::unavailable();
    };
    let check_ok = |check: &Json| {
        matches!(check, Json::Object(check) if keys_are(check, &["condition", "reason"])
            && matches!(&check["condition"], Json::String(text) if !strip(text).is_empty() && chars(text) <= 4096)
            && matches!(&check["reason"], Json::String(text) if !strip(text).is_empty() && chars(text) <= 256))
    };
    if checks.len() > 32 || !checks.iter().all(check_ok) {
        return PlayerControls::unavailable();
    }
    let preview = field("preview");
    let actor_parameter = field("actor_parameter");
    let (Json::String(confirmation), Json::String(response)) = (&confirmation, &response) else {
        return PlayerControls::unavailable();
    };
    if !matches!(confirmation.as_str(), "none" | "required")
        || !matches!(response.as_str(), "on_request" | "automatic")
        || preview.is_some_and(|preview| !preview.is_object())
        || actor_parameter
            .is_some_and(|name| !matches!(name, Json::String(name) if !name.is_empty() && chars(name) <= 128))
    {
        return PlayerControls::unavailable();
    }
    let object = |value: &Json| value.as_object().cloned().unwrap_or_default();
    PlayerControls {
        confirmation: confirmation.clone(),
        response: response.clone(),
        preview: preview.map(object),
        actor_parameter: actor_parameter.and_then(Json::as_str).map(str::to_owned),
        checks: checks.iter().map(object).collect(),
        queries: queries.iter().map(object).collect(),
        calculations: calculations.as_array().expect("checked above").iter().map(object).collect(),
        map_preview: map_preview.map(object),
        available: true,
        preflight: preflight.iter().filter_map(Json::as_str).map(str::to_owned).collect(),
        preflight_path: preflight_path.map(object),
    }
}
