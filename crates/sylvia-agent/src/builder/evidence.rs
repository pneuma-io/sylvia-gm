//! `_tool_call_details`, `_durable_tool_result` and `_builder_tool_evidence` from
//! `agent/_builder_session.py`: a completed tool result saved with the original call needed to
//! resume its typed pair.

use super::rejected;
use crate::working_context::durable_tool_result;
use indexmap::IndexMap;
use sylvia_base::{pyjson, CodedResult, Json, JsonMap};

/// One original call: `name`, `arguments` and, when the response had one, `provider_response_id`.
pub type CallDetails = IndexMap<String, JsonMap>;

/// `_tool_call_details(messages)`: the original call arguments indexed by call id, without the
/// assistant's private reasoning.
pub fn tool_call_details(messages: &[JsonMap]) -> CallDetails {
    let mut calls = CallDetails::new();
    for message in messages {
        if message.get("role") != Some(&Json::from("assistant")) {
            continue;
        }
        let Some(Json::Array(tool_calls)) = message.get("tool_calls") else { continue };
        for tool_call in tool_calls {
            let Json::Object(tool_call) = tool_call else { continue };
            let (Some(Json::String(call_id)), Some(Json::Object(function))) =
                (tool_call.get("id"), tool_call.get("function"))
            else {
                continue;
            };
            if let (Some(Json::String(name)), Some(Json::String(arguments))) =
                (function.get("name"), function.get("arguments"))
            {
                let mut call = JsonMap::new();
                call.insert("name".into(), Json::from(name.as_str()));
                call.insert("arguments".into(), Json::from(arguments.as_str()));
                if let Some(Json::String(response_id)) = message.get("provider_response_id") {
                    if !response_id.is_empty() {
                        call.insert("provider_response_id".into(), Json::from(response_id.as_str()));
                    }
                }
                calls.insert(call_id.clone(), call);
            }
        }
    }
    calls
}

/// `_durable_tool_result(tool_name, result)`: transient file contents removed, resumable
/// evidence kept.
pub fn builder_durable_tool_result(tool_name: &str, result: &Json) -> Json {
    durable_tool_result(tool_name, result)
}

/// `_builder_tool_evidence(message, calls)`.
pub fn builder_tool_evidence(message: &JsonMap, calls: &CallDetails) -> CodedResult<JsonMap> {
    let call = match message.get("tool_call_id") {
        Some(Json::String(call_id)) => calls.get(call_id).map(|call| (call_id, call)),
        _ => None,
    };
    let Some((call_id, call)) = call else {
        return Err(rejected("Builder tool result has no matching original call"));
    };
    let raw = message.get("content").cloned().unwrap_or_else(|| Json::from(""));
    let result = match &raw {
        Json::String(text) => pyjson::loads(text).unwrap_or_else(|_| {
            let mut fallback = JsonMap::new();
            fallback.insert("text".into(), Json::from(text.as_str()));
            Json::Object(fallback)
        }),
        other => other.clone(),
    };
    let name = call["name"].as_str().unwrap_or_default();
    let mut evidence = JsonMap::new();
    evidence.insert("kind".into(), Json::from("builder_tool_result"));
    evidence.insert("tool_name".into(), Json::from(name));
    evidence.insert("tool_call_id".into(), Json::from(call_id.as_str()));
    evidence.insert("arguments".into(), call["arguments"].clone());
    if let Some(response_id) = call.get("provider_response_id") {
        evidence.insert("provider_response_id".into(), response_id.clone());
    }
    evidence.insert("result".into(), builder_durable_tool_result(name, &result));
    Ok(evidence)
}
