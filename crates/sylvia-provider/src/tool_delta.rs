//! `provider/_tool_delta.py`: read one tool-call fragment from any transport's shape.

use sylvia_base::Json;

/// `_delta_value(delta, name)`: a key of a JSON object; any other value has no fields.
pub fn delta_value<'a>(delta: &'a Json, name: &str) -> Option<&'a Json> {
    delta.as_object().and_then(|map| map.get(name))
}

/// Python's `isinstance(value, int)` for a JSON value, where `bool` counts as an `int`.
pub fn python_int(value: &Json) -> Option<i128> {
    match value {
        Json::Bool(flag) => Some(i128::from(*flag)),
        Json::Number(number) if !number.is_f64() => {
            number.as_i64().map(i128::from).or_else(|| number.as_u64().map(i128::from))
        }
        _ => None,
    }
}

/// One parsed fragment: `(index, call_id, name, arguments)`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ToolCallFragment {
    pub index: usize,
    pub call_id: String,
    pub name: String,
    pub arguments: String,
}

fn text(value: Option<&Json>) -> String {
    value.and_then(Json::as_str).unwrap_or_default().to_owned()
}

/// `parse_tool_call_delta(delta)`.
///
/// A missing or invalid index is 0; a non-text identity, name or argument fragment is empty.
/// `function` (Chat Completions) wins over top-level `name` / `arguments` / `partial_json`.
pub fn parse_tool_call_delta(delta: &Json) -> ToolCallFragment {
    let index = delta_value(delta, "index")
        .and_then(python_int)
        .filter(|index| *index >= 0)
        .map_or(0, |index| usize::try_from(index).unwrap_or(usize::MAX));
    let call_id = text(delta_value(delta, "id"));
    let (name, arguments) = match delta_value(delta, "function").filter(|value| !value.is_null()) {
        Some(function) => (text(delta_value(function, "name")), text(delta_value(function, "arguments"))),
        None => {
            let arguments = match delta_value(delta, "arguments") {
                Some(value) => text(Some(value)),
                None => text(delta_value(delta, "partial_json")),
            };
            (text(delta_value(delta, "name")), arguments)
        }
    };
    ToolCallFragment { index, call_id, name, arguments }
}
