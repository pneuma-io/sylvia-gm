"""Retained text limits reach callers and reject whole writes without echoing their content."""

import pytest

from sylvia_gm.agent._provider_session import _model_tool_schema
from sylvia_gm.agent._stateless_mcp import (
    ModelMechanicsTool,
    _campaign_file_update_schema,
    _campaign_file_updates,
    _validation_issues_from_payload,
)
from sylvia_gm.application.schema_admission import (
    validate_json_schema,
    validate_json_value,
    validate_parameters,
)
from sylvia_gm.domain.errors import CommandRejected, MechanicsRejected

OPTIONS = {"operations": ["record_memory", "record_entity", "update_scene", "update_plan_progress"]}


def test_package_text_limits_are_enforced_by_both_authority_validation_paths():
    from jsonschema import Draft202012Validator

    from sylvia_gm.domain._content_schema_runtime import SCHEMA_ADMISSION_SUBSET_SCHEMA

    schema = {"type": "object", "properties": {"note": {
        "type": ["string", "null"], "minLength": 1, "maxLength": 3,
    }}, "required": ["note"], "additionalProperties": False}
    Draft202012Validator(SCHEMA_ADMISSION_SUBSET_SCHEMA).validate(schema)
    validate_json_schema(schema)
    for text in (None, "abc", "🦊🦊🦊"):
        validate_parameters({"note": text}, schema)
        validate_json_value({"note": text}, schema)
    with pytest.raises(MechanicsRejected) as failure:
        validate_parameters({"note": "abcd"}, schema)
    issue = failure.value.validation_issues[0]
    assert (issue.kind, issue.maximum, issue.actual_length) == ("string_too_long", 3, 4)
    with pytest.raises(CommandRejected, match="maximum length"):
        validate_json_value({"note": "abcd"}, schema)
    for invalid in (-1, True, "three"):
        with pytest.raises(CommandRejected, match="maximum length"):
            validate_json_schema({"type": "string", "maxLength": invalid})


def test_campaign_limits_survive_model_tool_projection():
    schema = _model_tool_schema(ModelMechanicsTool(
        package_id="sylvia.campaign", package_version="1", package_sha256="a" * 64,
        name="update_campaign_file", capability="narrative_state.update_campaign_file",
        category="narrative_state", input_schema=_campaign_file_update_schema(OPTIONS),
    ))
    summary = schema["properties"]["state_change_summary"]
    assert summary["maxLength"] == 1000
    assert "1000 characters" in summary["description"]
    assert "well below" in summary["description"]
    for collection, field, maximum in (
        ("facts", "text", 2000), ("entities", "name", 300),
        ("scenes", "summary", 2000), ("plan_progress", "progress_summary", 2000),
    ):
        definition = schema["properties"][collection]["items"]["properties"][field]
        assert definition["minLength"] == 1
        assert definition["maxLength"] == maximum
        assert str(maximum) in definition["description"]


@pytest.mark.parametrize("text", ["a" * 999, "a" * 1000, "🦊" * 1000, "x\n" * 500])
def test_summary_limit_counts_unicode_codepoints_and_keeps_valid_text(text):
    assert _campaign_file_updates({
        "state_change": "unchanged", "state_change_summary": text,
    }, OPTIONS) == ()


def test_oversized_summary_reports_bound_and_length_without_echoing_value():
    secret = "PRIVATE" + "🦊" * 1000
    with pytest.raises(MechanicsRejected) as rejected:
        _campaign_file_updates({
            "state_change": "changed", "state_change_summary": secret,
            "scenes": [{"summary": "A valid new scene."}],
        }, OPTIONS)
    assert rejected.value.state_changed is False
    issue = rejected.value.validation_issues[0].as_payload()
    assert issue["path"] == "state_change_summary"
    assert issue["maximum"] == 1000
    assert issue["actual_length"] == 1007
    assert "PRIVATE" not in str(issue)


def test_oversized_fact_is_rejected_before_any_updates_are_returned():
    with pytest.raises(MechanicsRejected) as rejected:
        _campaign_file_updates({
            "state_change": "changed", "state_change_summary": "Save the new facts.",
            "scenes": [{"summary": "A valid new scene."}],
            "facts": [{"fact_id": "test", "category": "continuity", "text": "x" * 2001,
                       "visibility": "player_known", "known_by": ["player"]}],
        }, OPTIONS)
    issue = rejected.value.validation_issues[0].as_payload()
    assert issue["path"] == "facts[0].text"
    assert issue["maximum"] == 2000
    assert issue["actual_length"] == 2001


def test_length_correction_survives_mcp_return_path():
    raw = {"path": "state_change_summary", "kind": "string_too_long",
           "maximum": 1000, "actual_length": 1007}
    assert _validation_issues_from_payload([raw])[0].as_payload() == raw
    for unsafe in (-1, True, "PRIVATE"):
        assert "actual_length" not in _validation_issues_from_payload([
            {**raw, "actual_length": unsafe}
        ])[0].as_payload()


@pytest.mark.parametrize("transport_name", ["chat", "responses", "anthropic"])
def test_limit_guidance_survives_actual_transport_serialization(transport_name):
    from sylvia_gm.provider._anthropic import AnthropicMessagesTransport
    from sylvia_gm.provider._openai_compat import ChatCompletionsTransport, ResponsesTransport

    schema = _campaign_file_update_schema(OPTIONS)
    tool = {"type": "function", "function": {
        "name": "update_campaign_file", "description": "Save campaign facts.", "parameters": schema,
    }}
    transport = {"chat": ChatCompletionsTransport, "responses": ResponsesTransport,
                 "anthropic": AnthropicMessagesTransport}[transport_name]()
    request = transport.format_request("synthetic", [{"role": "user", "content": "Test"}],
                                       [tool], False)
    wire = request["tools"][0]
    actual = (wire["input_schema"] if transport_name == "anthropic" else
              wire["parameters"] if transport_name == "responses" else
              wire["function"]["parameters"])
    assert actual == schema
    assert "1000 characters" in actual["properties"]["state_change_summary"]["description"]


def test_builder_completion_bounds_and_utf8_state_are_explained_and_enforced():
    from sylvia_gm.agent._builder_session import (
        _creation_subflow_completion_tool,
        _participant_artifact_tool,
        _participant_entity_state,
    )

    tool = _creation_subflow_completion_tool()
    schema = tool.parameters_schema["properties"]
    assert "2000 characters" in schema["summary"]["description"]
    assert "500 characters" in schema["boundary_conflicts"]["items"]["description"]
    assert tool.handler({"summary": "🦊" * 2000, "boundary_conflicts": []}, None)["success"]
    assert not tool.handler({"summary": "🦊" * 2001, "boundary_conflicts": []}, None)["success"]
    participant = _participant_artifact_tool(role="companion", publisher=lambda **kwargs: {})
    fields = participant.parameters_schema["properties"]
    for key in ("entity_state", "setting_data"):
        assert fields[key]["maxLength"] == 256 * 1024
        assert "UTF-8" in fields[key]["description"]
    import json
    at_limit = json.dumps({"x": "🦊" * 65534}, ensure_ascii=False, separators=(",", ":"))
    assert len(at_limit.encode("utf-8")) == 256 * 1024
    assert _participant_entity_state(at_limit)["x"] == "🦊" * 65534
    with pytest.raises(ValueError, match="256 KiB"):
        _participant_entity_state(at_limit + " ")


def test_content_format_length_error_is_actionable_without_echoing_text():
    from sylvia_gm.domain.content_definitions import get_content_definition
    from sylvia_gm.domain.content_validation import ContentShapeError, validate_content_shape

    definition = get_content_definition("character_profile")
    value = dict(definition["example"])
    value["summary"] = "PRIVATE" + "🦊" * 500
    with pytest.raises(ContentShapeError) as rejected:
        validate_content_shape("character_profile", value)
    issue = next(item for item in rejected.value.diagnostics if item.path == "$.summary")
    assert "at most 500 characters" in issue.message
    assert "507 characters" in issue.message
    assert "PRIVATE" not in issue.message


def test_private_header_length_error_does_not_echo_header_or_value():
    from sylvia_gm.config._resolver import ConfigError
    from sylvia_gm.config.provider_settings import _private_headers

    assert _private_headers({"X-Synthetic": "a" * 1024}) == {"X-Synthetic": "a" * 1024}
    with pytest.raises(ConfigError, match="1–1024 characters") as rejected:
        _private_headers({"X-PRIVATE": "PRIVATE" + "a" * 1024})
    assert "PRIVATE" not in str(rejected.value)
