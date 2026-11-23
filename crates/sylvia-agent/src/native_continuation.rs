//! `agent/_native_continuation.py`: the selected provider's native continuation requirements,
//! apart from inspection policy.

use sylvia_base::{pyjson, pystr, CodedError, CodedResult, Json, JsonMap};
use sylvia_provider::types::ModelInfo;

fn mismatch(message: &str) -> CodedError {
    CodedError::new("provider_native_continuation_mismatch", message).with_error_type("CommandRejected")
}

/// `requires_reasoning_with_tools(model, has_tools=True)`: provider-required reasoning for
/// DeepSeek, Kimi and GLM tool continuations.
pub fn requires_reasoning_with_tools(model: Option<&ModelInfo>, has_tools: bool) -> bool {
    let Some(model) = model.filter(|model| model.transport == "chat_completions") else { return false };
    let folded = pystr::casefold(model.wire_model_id());
    let identity = folded.rsplit('/').next().unwrap_or("");
    if !has_tools {
        return identity.starts_with("kimi-k3") || identity.starts_with("kimi-k2.7-code");
    }
    ["deepseek", "kimi", "glm"].iter().any(|prefix| identity.starts_with(prefix))
}

/// Python `==` of two decoded JSON values: numbers compare by value (`1 == 1.0`, `True == 1`).
fn python_equal(a: &Json, b: &Json) -> bool {
    let number = |value: &Json| match value {
        Json::Bool(flag) => Some(f64::from(u8::from(*flag))),
        Json::Number(number) => number.as_f64(),
        _ => None,
    };
    match (a, b) {
        (Json::Array(a), Json::Array(b)) => {
            a.len() == b.len() && a.iter().zip(b).all(|(a, b)| python_equal(a, b))
        }
        (Json::Object(a), Json::Object(b)) => {
            a.len() == b.len()
                && a.iter().all(|(key, value)| b.get(key).is_some_and(|other| python_equal(value, other)))
        }
        (Json::String(a), Json::String(b)) => a == b,
        (Json::Null, Json::Null) => true,
        _ => match (number(a), number(b)) {
            (Some(a), Some(b)) => a == b,
            _ => false,
        },
    }
}

/// `json.loads(value)` for the native-call comparisons: a non-string or invalid argument text is
/// Python's `TypeError`/`ValueError` (`None` here).
fn loads(value: Option<&Json>, default: &str) -> Option<Json> {
    match value {
        None => pyjson::loads(default).ok(),
        Some(Json::String(text)) => pyjson::loads(text).ok(),
        Some(_) => None,
    }
}

fn function_of(call: &Json) -> JsonMap {
    call.get("function").and_then(Json::as_object).cloned().unwrap_or_default()
}

/// `attach_chat_metadata(message, metadata, required=False)`: private chat extensions attached
/// to the same saved tool identities only.
pub fn attach_chat_metadata(
    message: &JsonMap,
    metadata: Option<&Json>,
    required: bool,
) -> CodedResult<JsonMap> {
    let Some(metadata) = metadata.filter(|metadata| !metadata.is_null()) else {
        if required {
            return Err(CodedError::new(
                "provider_native_continuation_missing",
                "Required provider continuation is unavailable; restore a backup or an export including Thinking.",
            )
            .with_error_type("CommandRejected"));
        }
        return Ok(message.clone());
    };
    let Json::Object(metadata) = metadata else { return Err(mismatch("Native chat metadata is invalid")) };
    let mut result = message.clone();
    if let Some(fields) = metadata.get("reasoning_fields").filter(|fields| !fields.is_null()) {
        let valid = fields.as_object().is_some_and(|fields| {
            fields.keys().all(|key| matches!(key.as_str(), "reasoning" | "reasoning_content"))
                && fields.values().all(Json::is_string)
        });
        if !valid {
            return Err(mismatch("Native reasoning fields are invalid"));
        }
        result.shift_remove("reasoning_content");
        for (key, value) in fields.as_object().into_iter().flatten() {
            result.insert(key.clone(), value.clone());
        }
    }
    if let Some(content) = metadata.get("chat_content").filter(|content| !content.is_null()) {
        if !content.as_array().is_some_and(|blocks| blocks.iter().all(Json::is_object)) {
            return Err(mismatch("Native chat content is invalid"));
        }
        result.insert("content".into(), content.clone());
    }
    let calls = message.get("tool_calls").cloned().unwrap_or_else(|| Json::Array(Vec::new()));
    if let Some(output) = metadata.get("responses_output").filter(|output| !output.is_null()) {
        let (Some(items), Some(calls)) = (output.as_array(), calls.as_array()) else {
            return Err(mismatch("Native Responses metadata is invalid"));
        };
        if !items.iter().all(Json::is_object) {
            return Err(mismatch("Native Responses metadata is invalid"));
        }
        let native: Vec<&Json> = items
            .iter()
            .filter(|item| item.get("type").and_then(Json::as_str) == Some("function_call"))
            .collect();
        let matches = native.len() == calls.len()
            && native.iter().zip(calls).all(|(native, call)| {
                let function = function_of(call);
                let (Some(native_arguments), Some(call_arguments)) =
                    (loads(native.get("arguments"), "{}"), loads(function.get("arguments"), "{}"))
                else {
                    return false;
                };
                native.get("call_id") == call.get("id")
                    && native.get("name") == function.get("name")
                    && python_equal(&native_arguments, &call_arguments)
            });
        if !matches {
            return Err(mismatch("Native Responses calls do not match this exchange"));
        }
        result.insert("responses_output".into(), output.clone());
    }
    if let Some(parts) = metadata.get("gemini_parts").filter(|parts| !parts.is_null()) {
        let ids = metadata.get("gemini_call_ids");
        let call_ids: Option<Vec<Json>> = calls
            .as_array()
            .map(|calls| calls.iter().map(|call| call.get("id").cloned().unwrap_or(Json::Null)).collect());
        let valid = parts.as_array().is_some_and(|parts| parts.iter().all(Json::is_object))
            && ids.and_then(Json::as_array).is_some_and(|ids| call_ids.as_ref() == Some(ids));
        if !valid {
            return Err(mismatch("Native Gemini metadata does not match this exchange"));
        }
        let calls = calls.as_array().cloned().unwrap_or_default();
        let native: Vec<&Json> =
            parts.as_array().into_iter().flatten().filter_map(|part| part.get("functionCall")).collect();
        let differs = native.len() != calls.len()
            || native.iter().zip(&calls).any(|(native, call)| {
                let function = function_of(call);
                let arguments = loads(function.get("arguments"), "{}");
                let native_args = native.get("args").cloned().unwrap_or_else(|| Json::Object(JsonMap::new()));
                native.get("name") != function.get("name")
                    || arguments.is_none_or(|arguments| !python_equal(&native_args, &arguments))
            });
        if differs {
            return Err(mismatch("Native Gemini arguments do not match this exchange"));
        }
        result.insert("gemini_parts".into(), parts.clone());
    }
    if let Some(details) = metadata.get("reasoning_details").filter(|details| !details.is_null()) {
        if !details.is_array() {
            return Err(mismatch("Native reasoning details are invalid"));
        }
        result.insert("reasoning_details".into(), details.clone());
    }
    if let Some(extras) = metadata.get("tool_call_extras").filter(|extras| !extras.is_null()) {
        let (Some(extras), Some(calls)) = (extras.as_object(), calls.as_array()) else {
            return Err(mismatch("Native tool metadata is invalid"));
        };
        let ids: Vec<&Json> = calls.iter().filter_map(|call| call.as_object().and(call.get("id"))).collect();
        if !extras.keys().all(|key| ids.iter().any(|id| id.as_str() == Some(key.as_str()))) {
            return Err(mismatch("Native tool metadata does not match this exchange"));
        }
        let updated: Vec<Json> = calls
            .iter()
            .map(|call| {
                let mut call = call.as_object().cloned().unwrap_or_default();
                if let Some(extra) =
                    call.get("id").and_then(Json::as_str).and_then(|id| extras.get(id)).cloned()
                {
                    call.insert("extra_content".into(), extra);
                }
                Json::Object(call)
            })
            .collect();
        result.insert("tool_calls".into(), Json::Array(updated));
    }
    Ok(result)
}
