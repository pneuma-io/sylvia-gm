//! `builder/_tools.py`: the Builder agent's hard-coded tool definitions, in OpenAI
//! function-calling JSON Schema form.
//!
//! The definitions are model-read, so they are data here: `builder_tools.json`, exported from
//! the Python values by `generate_builder_tools.py` with every object's keys in Python's order.

use std::sync::OnceLock;
use sylvia_base::{pyjson, Json};

fn definitions() -> &'static Json {
    static DEFINITIONS: OnceLock<Json> = OnceLock::new();
    DEFINITIONS.get_or_init(|| pyjson::loads(include_str!("builder_tools.json")).expect("exported JSON"))
}

fn definition(name: &str) -> &'static Json {
    &definitions()[name]
}

/// `BUILDER_TOOL_DEFINITIONS`: the project-file tools.
pub fn builder_tool_definitions() -> &'static [Json] {
    definition("BUILDER_TOOL_DEFINITIONS").as_array().expect("a list")
}

/// `PUBLIC_WEB_TOOL_DEFINITION`: the per-turn public web search.
pub fn public_web_tool_definition() -> &'static Json {
    definition("PUBLIC_WEB_TOOL_DEFINITION")
}

/// `INTERVIEW_TOOL_DEFINITIONS`.
pub fn interview_tool_definitions() -> &'static [Json] {
    definition("INTERVIEW_TOOL_DEFINITIONS").as_array().expect("a list")
}

/// `RULESET_CANDIDATE_TOOL_DEFINITION`.
pub fn ruleset_candidate_tool_definition() -> &'static Json {
    definition("RULESET_CANDIDATE_TOOL_DEFINITION")
}

/// `CAMPAIGN_SOURCE_TOOL_DEFINITION`.
pub fn campaign_source_tool_definition() -> &'static Json {
    definition("CAMPAIGN_SOURCE_TOOL_DEFINITION")
}

/// `CREATE_CAMPAIGN_PLAN_DRAFT_TOOL_DEFINITION`.
pub fn create_campaign_plan_draft_tool_definition() -> &'static Json {
    definition("CREATE_CAMPAIGN_PLAN_DRAFT_TOOL_DEFINITION")
}

/// `CAMPAIGN_PLAN_TOOL_DEFINITION`.
pub fn campaign_plan_tool_definition() -> &'static Json {
    definition("CAMPAIGN_PLAN_TOOL_DEFINITION")
}

/// `DISCOVER_CAMPAIGN_CONTENT_TOOL_DEFINITION`.
pub fn discover_campaign_content_tool_definition() -> &'static Json {
    definition("DISCOVER_CAMPAIGN_CONTENT_TOOL_DEFINITION")
}

/// `PREPARE_CAMPAIGN_SETUP_TOOL_DEFINITION`.
pub fn prepare_campaign_setup_tool_definition() -> &'static Json {
    definition("PREPARE_CAMPAIGN_SETUP_TOOL_DEFINITION")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn name(tool: &Json) -> &str {
        tool["function"]["name"].as_str().unwrap()
    }

    #[test]
    fn every_python_definition_is_exported_in_order() {
        let exported: Vec<&String> = definitions().as_object().unwrap().keys().collect();
        assert_eq!(
            exported,
            [
                "BUILDER_TOOL_DEFINITIONS",
                "PUBLIC_WEB_TOOL_DEFINITION",
                "INTERVIEW_TOOL_DEFINITIONS",
                "RULESET_CANDIDATE_TOOL_DEFINITION",
                "CAMPAIGN_SOURCE_TOOL_DEFINITION",
                "CREATE_CAMPAIGN_PLAN_DRAFT_TOOL_DEFINITION",
                "CAMPAIGN_PLAN_TOOL_DEFINITION",
                "DISCOVER_CAMPAIGN_CONTENT_TOOL_DEFINITION",
                "PREPARE_CAMPAIGN_SETUP_TOOL_DEFINITION",
            ]
        );
        let builder: Vec<&str> = builder_tool_definitions().iter().map(name).collect();
        assert_eq!(builder, ["read_file", "write_file", "edit_file", "search_files", "delete_file"]);
        let singles: Vec<&str> = singles_json().into_iter().map(name).collect();
        assert_eq!(
            singles,
            [
                "web_search",
                "publish_ruleset_candidate",
                "inspect_campaign_sources",
                "create_campaign_plan_draft",
                "publish_campaign_plan",
                "discover_campaign_content",
                "prepare_campaign_setup",
            ]
        );
        assert_eq!(interview_tool_definitions().iter().map(name).collect::<Vec<_>>(), ["ask_question"]);
        // Each is a function tool with an object schema, as Python declares it.
        for tool in
            builder_tool_definitions().iter().chain(interview_tool_definitions()).chain(singles_json())
        {
            assert_eq!(tool["type"], "function");
            assert_eq!(tool["function"]["parameters"]["type"], "object");
        }
    }

    fn singles_json() -> Vec<&'static Json> {
        vec![
            public_web_tool_definition(),
            ruleset_candidate_tool_definition(),
            campaign_source_tool_definition(),
            create_campaign_plan_draft_tool_definition(),
            campaign_plan_tool_definition(),
            discover_campaign_content_tool_definition(),
            prepare_campaign_setup_tool_definition(),
        ]
    }
}
