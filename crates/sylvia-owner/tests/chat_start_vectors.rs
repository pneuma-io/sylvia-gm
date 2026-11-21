//! Compare the chat-start helpers ported with `set_chat_defaults` (`catalog_model_info`,
//! `explicit_effort`, `explicit_speed`) with vectors written by the frozen Python reference.
//!
//! Regenerate with `tests/vectors/generate_chat_start_vectors.py` (needs the snapshot's Python).

use serde_json::{json, Map, Value as J};
use sylvia_base::CodedError;
use sylvia_owner::commands::providers::{catalog_model_info, explicit_effort, explicit_speed};
use sylvia_provider::types::{ModelInfo, SpeedOption};

fn vectors() -> J {
    serde_json::from_str(include_str!("vectors/chat_start_vectors.json")).expect("vector file is valid JSON")
}

fn same(a: &J, b: &J) -> bool {
    serde_json::to_string(a).unwrap() == serde_json::to_string(b).unwrap()
}

fn opt_text(value: &Option<String>) -> J {
    value.clone().map_or(J::Null, J::String)
}

/// One `ModelInfo` field as Python's `getattr` shows it.
fn model_field(model: &ModelInfo, name: &str) -> J {
    let int = |value: Option<i64>| value.map_or(J::Null, J::from);
    let flag = |value: Option<bool>| value.map_or(J::Null, J::Bool);
    match name {
        "id" => json!(model.id),
        "name" => json!(model.name),
        "provider_name" => json!(model.provider_name),
        "provider_key" => json!(model.provider_key),
        "provider_mode" => json!(model.provider_mode),
        "recommended" => json!(model.recommended),
        "premium" => json!(model.premium),
        "context_window" => int(model.context_window),
        "declared_context_window" => int(model.declared_context_window),
        "declared_context_source" => json!(model.declared_context_source),
        "runtime_context_window" => int(model.runtime_context_window),
        "runtime_context_source" => json!(model.runtime_context_source),
        "configured_context_window" => int(model.configured_context_window),
        "context_window_source" => json!(model.context_window_source),
        "declared_context_observed_at" => opt_text(&model.declared_context_observed_at),
        "runtime_context_observed_at" => opt_text(&model.runtime_context_observed_at),
        "configured_context_observed_at" => opt_text(&model.configured_context_observed_at),
        "context_observed_at" => opt_text(&model.context_observed_at),
        "configured_runtime_mismatch" => json!(model.configured_runtime_mismatch),
        "configured_declared_mismatch" => json!(model.configured_declared_mismatch),
        "max_output_tokens" => int(model.max_output_tokens),
        "builder_output_tokens" => json!(model.builder_output_tokens),
        "cost_per_1k_tokens" => json!(model.cost_per_1k_tokens),
        "transport" => json!(model.transport),
        "provider_model_id" => json!(model.provider_model_id),
        "supports_streaming" => flag(model.supports_streaming),
        "supports_tools" => flag(model.supports_tools),
        "supported_reasoning_efforts" => {
            model.supported_reasoning_efforts.as_ref().map_or(J::Null, |values| json!(values))
        }
        "default_reasoning_effort" => opt_text(&model.default_reasoning_effort),
        "reasoning_control" => opt_text(&model.reasoning_control),
        "reasoning_source" => json!(model.reasoning_source),
        "speed_options" => model
            .speed_options
            .as_ref()
            .map_or(J::Null, |options| J::Array(options.iter().map(SpeedOption::projection).collect())),
        "speed_control" => opt_text(&model.speed_control),
        "speed_source" => json!(model.speed_source),
        other => panic!("ModelInfo has no field {other}"),
    }
}

fn record(model: Option<&ModelInfo>, expected: &J) -> J {
    match (model, expected.as_object()) {
        (Some(model), Some(fields)) => {
            J::Object(fields.keys().map(|key| (key.clone(), model_field(model, key))).collect::<Map<_, _>>())
        }
        (Some(_), None) => json!("a model where Python has none"),
        (None, _) => J::Null,
    }
}

fn failure(error: CodedError) -> J {
    let class = error.error_type.unwrap_or("CommandRejected");
    let mut detail = json!({"class": class, "message": error.message});
    if class == "CommandRejected" {
        detail = json!({"class": class, "code": error.code, "message": detail["message"]});
    }
    json!({"error": detail})
}

#[test]
fn catalog_model_info_projects_choices_back_as_python_does() {
    let vectors = vectors();
    let mut models: Vec<Option<ModelInfo>> = Vec::new();
    for case in vectors["projections"].as_array().unwrap() {
        let row = &case["row"];
        let (provider_id, model_id) =
            (row["provider_id"].as_str().unwrap(), row["model_id"].as_str().unwrap());
        let actual = match catalog_model_info(provider_id, model_id, std::slice::from_ref(row)) {
            Ok(model) => {
                let value = record(model.as_ref(), &case["value"]);
                models.push(model);
                json!({"row": row, "value": value})
            }
            Err(error) => {
                let mut value = failure(error);
                value["row"] = row.clone();
                json!({"row": row, "error": value["error"]})
            }
        };
        assert!(same(&actual, case), "expected {case}\n  got      {actual}");
        if case.get("value").is_some() {
            assert_eq!(
                case["value"].as_object().unwrap().len(),
                34,
                "every Python field has a Rust counterpart"
            );
        }
    }
    let rows: Vec<J> =
        vectors["projections"].as_array().unwrap().iter().map(|case| case["row"].clone()).collect();
    assert!(catalog_model_info("p", "p/none", &rows).unwrap().is_none());
    assert_eq!(vectors["missing"], json!({"value": null}));
}

#[test]
fn explicit_effort_and_speed_match_python() {
    let vectors = vectors();
    let projections = vectors["projections"].as_array().unwrap();
    for case in vectors["choices"].as_array().unwrap() {
        let model = case["row"].as_u64().map(|index| {
            let row = &projections[index as usize]["row"];
            catalog_model_info(
                row["provider_id"].as_str().unwrap(),
                row["model_id"].as_str().unwrap(),
                std::slice::from_ref(row),
            )
            .unwrap()
            .unwrap()
        });
        let requested = &case["requested"];
        let result = match case["kind"].as_str().unwrap() {
            "effort" => explicit_effort(model.as_ref(), case["role"].as_str().unwrap(), requested),
            _ => explicit_speed(model.as_ref(), requested),
        };
        let mut actual = case.clone();
        let object = actual.as_object_mut().unwrap();
        object.remove("value");
        object.remove("error");
        match result {
            Ok(value) => object.insert("value".into(), json!(value)),
            Err(error) => object.insert("error".into(), failure(error)["error"].clone()),
        };
        assert!(same(&actual, case), "expected {case}\n  got      {actual}");
    }
}
