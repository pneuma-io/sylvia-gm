//! Compare the MCP tool listing (T3m-1) with Python, on real turns.
//!
//! Regenerate with `tests/vectors/generate_listing_vectors.py`: every
//! `StatelessMcpMechanics._list_tools` call of the `ruleless-turn` and `srd-mechanics-turn`
//! turns, and the first listing with a session repeated with variants (probe catalog tools for
//! the schema projection and exposure, castable spells, no session, readable and structured
//! Plans, every Campaign File note type, refusals). Rust is answered every owner call Python's
//! listing made, in order, the Full Audit record among them; its tools must be Python's, byte
//! for byte, and a refusal Python's.

mod common;

use common::Recorded;
use serde_json::{json, Value as J};
use sylvia_agent::stateless_mcp::listing::list_tools_with_discovery;
use sylvia_agent::stateless_mcp::ModelMechanicsTool;

/// `{"__shared__": key}` written back as the shared value, recursively.
fn expand(value: &J, shared: &J) -> J {
    match value {
        J::Object(map) => match map.get("__shared__") {
            Some(J::String(key)) if map.len() == 1 => expand(&shared[key.as_str()], shared),
            _ => J::Object(map.iter().map(|(key, item)| (key.clone(), expand(item, shared))).collect()),
        },
        J::Array(items) => J::Array(items.iter().map(|item| expand(item, shared)).collect()),
        other => other.clone(),
    }
}

/// `plain(tool)`, in the dataclass's field order.
fn tool_json(tool: &ModelMechanicsTool) -> J {
    json!({
        "__class__": "ModelMechanicsTool",
        "package_id": tool.package_id,
        "package_version": tool.package_version,
        "package_sha256": tool.package_sha256,
        "name": tool.name,
        "capability": tool.capability,
        "input_schema": tool.input_schema,
        "parameter_derivations": tool.parameter_derivations,
        "description": tool.description,
        "category": tool.category,
        "model_behavior": tool.model_behavior.as_str(),
    })
}

#[tokio::test]
async fn tool_listings_match_python() {
    let vectors: J = serde_json::from_str(include_str!("vectors/listing_vectors.json"))
        .expect("vector file is valid JSON");
    let cases = expand(&vectors["cases"], &vectors["shared"]);
    let cases = cases.as_array().expect("cases");
    let mut seen = std::collections::BTreeSet::new();
    for (index, case) in cases.iter().enumerate() {
        let variant = case["variant"].as_str().unwrap_or("real");
        seen.insert(variant.to_owned());
        let name = format!("{index} {} {} {variant}", case["session"].as_str().unwrap(), case["purpose"]);
        let owner = Recorded::new(case["calls"].as_array().unwrap());
        let result = list_tools_with_discovery(
            &owner,
            case["campaign_id"].as_str().unwrap(),
            case["purpose"].as_str().unwrap(),
            case["discovery_id"].as_str().unwrap(),
            case["session_id"].as_str(),
            case["fixed"].as_bool().unwrap(),
        )
        .await;
        if case["fixed"] == true {
            // The fixed list is the context ledger's (L4): not ported yet.
            let error = result.expect_err(&name);
            assert_eq!(error.code, "rust_port_pending", "{name}");
            continue;
        }
        if let Some(raised) = case.get("raised") {
            let error = result.expect_err(&format!("{name}: Python raised {raised}"));
            assert_eq!(error.message, raised["message"].as_str().unwrap(), "{name}");
            // Python's refusal is an `MCPError` inside two exception groups: not an
            // `AuthorityError`, so the owner answers it as an internal fault.
            let mut inner = raised;
            while let Some(J::Array(held)) = inner.get("exceptions") {
                assert_eq!(held.len(), 1, "{name}");
                inner = &held[0];
            }
            let python_internal = raised["class"] == "ExceptionGroup" && inner["class"] == "MCPError";
            if python_internal {
                assert_eq!(error.code, "internal_error", "{name}");
            } else {
                assert_eq!(J::from(error.code.as_str()), raised["code"], "{name}");
            }
            assert_eq!(
                error.error_type.unwrap_or("CommandRejected"),
                raised["class"].as_str().unwrap(),
                "{name}"
            );
        } else {
            let tools = result.unwrap_or_else(|error| panic!("{name}: {error:?}"));
            let python = case["tools"].as_array().unwrap();
            assert_eq!(tools.len(), python.len(), "{name}: tool count");
            for (tool, expected) in tools.iter().zip(python) {
                // Byte for byte: the schemas' key order is what the model reads.
                assert_eq!(
                    serde_json::to_string(&tool_json(tool)).unwrap(),
                    serde_json::to_string(expected).unwrap(),
                    "{name}: {}",
                    tool.name
                );
            }
        }
        owner.check(&name);
    }
    for expected in [
        "real",
        "probe_tools",
        "probe_exposure",
        "no_session_no_plan",
        "readable_plan",
        "structured_plan",
        "every_note",
        "no_notes",
        "invalid_notes",
        "catalog_refused",
        "invalid_properties",
        "invalid_property",
        "invalid_derivation",
        "fixed",
    ] {
        assert!(seen.contains(expected), "no {expected} case");
    }
}
