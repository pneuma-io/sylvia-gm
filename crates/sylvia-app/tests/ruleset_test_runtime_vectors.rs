//! `ruleset_test_runtime_facts` against the frozen Python: what `ruleset_test_worker.run_archive`
//! reads of a runtime through `_parse_runtime` (`vectors/generate_ruleset_test_runtime_vectors.py`).

use serde_json::Value;
use sylvia_app::package_runtime::{ruleset_test_runtime_facts, PackageRuntimeFailure};

#[test]
fn ruleset_test_runtime_facts_match_python() {
    let vectors: Value =
        serde_json::from_str(include_str!("vectors/ruleset_test_runtime_vectors.json")).expect("valid JSON");
    let cases = vectors["cases"].as_array().expect("cases");
    assert_eq!(cases.len(), 36);
    let mut handoffs = 0;
    for case in cases {
        let label = case["label"].as_str().expect("label");
        let runtime = match case.get("runtime_ref").and_then(Value::as_str) {
            Some(name) => vectors["runtimes"][name].as_str().expect("kept runtime"),
            None => case["runtime"].as_str().expect("runtime"),
        };
        let required: Vec<&str> = case["required"]
            .as_array()
            .expect("required")
            .iter()
            .map(|name| name.as_str().expect("name"))
            .collect();
        let package_id = case["package_id"].as_str().expect("package id");
        let actual = match ruleset_test_runtime_facts(package_id, runtime.as_bytes(), &required) {
            Ok(facts) => {
                let mut names = facts.required_preconditions;
                names.sort();
                handoffs += usize::from(facts.requires_handoff);
                serde_json::json!({"required_preconditions": names, "requires_handoff": facts.requires_handoff})
            }
            Err(PackageRuntimeFailure::Rejected(_)) => serde_json::json!({"raise": "PackageRuntimeError"}),
            Err(PackageRuntimeFailure::Uncaught { class, message }) => {
                if class == "KeyError" {
                    assert_eq!(message, "'not_a_declared_tool'", "{label}");
                }
                serde_json::json!({"raise": class})
            }
        };
        sylvia_base::testing::assert_same_json(&actual, &case["expected"], label);
    }
    assert!(handoffs > 0, "a case declares a decision scope");
}
