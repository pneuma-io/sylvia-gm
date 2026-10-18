"""Focused tests for shared content definitions and their Builder retrieval boundary."""

from __future__ import annotations

import copy
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from sylvia_gm.agent._builder_session import run_builder_turn, stream_builder_turn
from sylvia_gm.agent._ruleset_draft_check_tool import ruleset_draft_check_tool
from sylvia_gm.agent._session_loop import ProviderControl, ProviderEventKind
from sylvia_gm.builder._ruleset_source import compile_ruleset_source
from sylvia_gm.domain.authority import WriterRole, canonical_payload_is_safe
from sylvia_gm.domain.campaign_plan import PackageBinding, validate_campaign_plan
from sylvia_gm.domain.content_definitions import (
    get_content_definition,
    list_content_definitions,
)
from sylvia_gm.domain.content_validation import validate_content_shape
from sylvia_gm.domain.errors import CommandRejected, RulesetDiagnostic
from sylvia_gm.domain.interview_contribution import parse_interview_contribution_bytes
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.domain.session import SessionContext, SessionRecord
from sylvia_gm.provider._base import Provider, ProviderResponse, ProviderStreamChunk, ToolCall


def _context() -> SessionContext:
    return SessionContext(
        session=SessionRecord(
            session_id="content-definition-session",
            campaign_id="campaign-content-definition",
            role=WriterRole.BUILDER,
            provider_id="test.builder",
            model_id="test.builder/model",
            selected_skill="BUILD CAMPAIGN PLAN",
        ),
        messages=(),
    )


def test_content_definitions_return_owned_valid_schemas_and_examples() -> None:
    catalog = list_content_definitions()
    assert {item["name"] for item in catalog} >= {
        "campaign_plan",
        "interview_contribution",
        "panel_layout",
        "ruleset_source",
        "panel_declaration",
        "setting_character_requirements",
        "character_file",
        "spatial_scene",
        "result_card",
    }

    for item in catalog:
        first = get_content_definition(item["name"])
        validate_content_shape(item["name"], first["example"], file=first["file"])
        assert canonical_payload_is_safe(first)
        with pytest.raises(ValueError):
            validate_content_shape(item["name"], first["invalid_example"], file=first["file"])

        # Returned authoring data must not expose the process-global declarations to mutation.
        first["schema"]["title"] = "mutated"
        first["example"]["untrusted_field"] = True
        first["requirements"].append("mutated")
        second = get_content_definition(item["name"])

        assert second["schema"]["title"] == item["title"]
        assert "untrusted_field" not in second["example"]
        assert "mutated" not in second["requirements"]


def test_neutral_campaign_plan_example_passes_contextual_plan_validation() -> None:
    definition = get_content_definition("campaign_plan")
    plan = definition["example"]
    bindings = plan["bindings"]

    validated = validate_campaign_plan(
        plan,
        readable_markdown="# Signal Relay\n\nA small neutral example.",
        expected_ruleset=PackageBinding(**{
            "package_id": bindings["ruleset"]["id"],
            "version": bindings["ruleset"]["version"],
            "content_sha256": bindings["ruleset"]["content_sha256"],
        }),
        expected_setting=PackageBinding(**{
            "package_id": bindings["campaign_setting"]["id"],
            "version": bindings["campaign_setting"]["version"],
            "content_sha256": bindings["campaign_setting"]["content_sha256"],
        }),
        expected_closure_sha256=bindings["package_closure_sha256"],
        setting_paths=frozenset({"locations/relay.md"}),
    )

    assert validated.title == "Signal Relay"
    assert validated.referenced_setting_paths == ("locations/relay.md",)


@pytest.mark.parametrize("with_tokens", [False, True])
def test_ruleset_example_and_tokens_pass_actual_draft_validation(
    tmp_path: Path, with_tokens: bool,
) -> None:
    source = get_content_definition("ruleset_source")["example"]
    if with_tokens:
        source["mechanic_families"]["flip"]["parameters"] = "{inputs}"
        source["mechanic_families"]["flip"]["effects"] = ["{effect}"]
        source["public_actions"][0]["configure"] = {
            "inputs": [], "effect": {"dice": {"expression": "1d2"}, "store_as": "_result"},
        }
    source["ui_surfaces"] = [get_content_definition("panel_declaration")["example"]]
    source["panel_layout"] = get_content_definition("panel_layout")["example"]
    directory = tmp_path / "ruleset-draft"
    directory.mkdir()
    path = directory / "ruleset-source.yaml"
    original = yaml.safe_dump(source).encode()
    path.write_bytes(original)
    checked = ruleset_draft_check_tool(tmp_path).handler({}, None)
    assert checked["success"] is True, checked
    assert path.read_bytes() == original
    assert not (directory / "output").exists()

    source["panel_layout"]["sidebar"]["script"] = "undeclared"
    with pytest.raises(CommandRejected) as rejected:
        compile_ruleset_source(yaml.safe_dump(source).encode())
    assert rejected.value.diagnostics
    assert all(item.file == "ruleset-source.yaml" for item in rejected.value.diagnostics)


@pytest.mark.parametrize("source", [
    b"format: 1\nformat: 1\n",
    b"loop: &loop\n  child: *loop\n",
    b"1: value\n",
    b"value: .nan\n",
])
def test_ruleset_source_rejects_ambiguous_or_non_json_yaml(source: bytes) -> None:
    with pytest.raises(CommandRejected) as rejected:
        compile_ruleset_source(source)
    assert rejected.value.diagnostics[0].code == "ruleset_source_yaml_invalid"


@pytest.mark.parametrize(
    ("mutate", "expected_path"),
    [
        (
            lambda plan: plan["initial_state"]["entities"].append(
                {
                    "id": "operator",
                    "kind": "npc",
                    "source_ref": {
                        "id": "operator",
                        "path": "characters/operator.json",
                        "label": "undeclared",
                    },
                    "state": {},
                }
            ),
            "$.initial_state.entities[0].source_ref.label",
        ),
        (
            lambda plan: plan["objectives"][0].pop("description"),
            "$.objectives[0].description",
        ),
    ],
)
def test_malformed_nested_plan_records_report_a_useful_path(
    mutate: Any, expected_path: str,
) -> None:
    plan = copy.deepcopy(get_content_definition("campaign_plan")["example"])
    mutate(plan)

    with pytest.raises(ValueError) as rejected:
        validate_content_shape("campaign_plan", plan, file="plan.json")

    diagnostics = getattr(rejected.value, "diagnostics", ())
    assert diagnostics
    assert all(isinstance(item, RulesetDiagnostic) for item in diagnostics)
    assert expected_path in {item.path for item in diagnostics}
    assert all(item.file == "plan.json" for item in diagnostics)


def test_interview_example_is_accepted_and_closed() -> None:
    example = get_content_definition("interview_contribution")["example"]
    parsed = parse_interview_contribution_bytes(
        json.dumps(example, indent=2).encode(), expected_owner_kind="campaign_setting",
    )
    assert parsed.contribution_id == "journey-tone"

    malformed = copy.deepcopy(example)
    malformed["questions"][0]["system_prompt"] = "undeclared"
    with pytest.raises(ValueError, match="fields") as rejected:
        parse_interview_contribution_bytes(
            canonical_json_bytes(malformed), expected_owner_kind="campaign_setting",
        )
    diagnostics = getattr(rejected.value, "diagnostics", ())
    assert "$.questions[0].system_prompt" in {item.path for item in diagnostics}


def test_interview_json_rejects_duplicate_members() -> None:
    duplicate = (
        b'{"format_id":"sylvia-gm-interview-contribution",'
        b'"format_id":"sylvia-gm-interview-contribution",'
        b'"format_version":1,"owner_kind":"campaign_setting",'
        b'"id":"duplicate","title":"Duplicate","questions":[]}'
    )

    with pytest.raises(ValueError, match="strict JSON"):
        parse_interview_contribution_bytes(
            duplicate, expected_owner_kind="campaign_setting",
        )


def test_interview_condition_cannot_reference_a_later_question() -> None:
    contribution = copy.deepcopy(get_content_definition("interview_contribution")["example"])
    contribution["questions"] = [
        {
            "id": "detail",
            "prompt": "What detail matters?",
            "type": "free_text",
            "required": False,
            "adaptive": False,
            "options": [],
            "condition": {"question_id": "mood", "equals": "tense"},
        },
        contribution["questions"][0],
    ]

    with pytest.raises(ValueError, match="only prior questions"):
        parse_interview_contribution_bytes(
            canonical_json_bytes(contribution), expected_owner_kind="campaign_setting",
        )


@pytest.mark.parametrize("field", ["id", "title"])
def test_interview_identity_and_text_reject_trailing_newlines(field: str) -> None:
    example = get_content_definition("interview_contribution")["example"]
    example[field] += "\n"
    with pytest.raises(ValueError) as rejected:
        parse_interview_contribution_bytes(
            json.dumps(example, indent=2).encode(), expected_owner_kind="campaign_setting",
        )
    assert f"$.{field}" in {item.path for item in rejected.value.diagnostics}


class _DefinitionProvider(Provider):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[list[dict[str, Any]], list[dict[str, Any]] | None]] = []

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **_kwargs: object,
    ) -> ProviderResponse:
        self.calls.append((messages.copy(), tools))
        if len(self.calls) == 1:
            names = {tool["function"]["name"] for tool in tools or []}
            assert "get_content_definition" in names
            assert "publish_campaign_plan" not in names
            assert "publish_ruleset_candidate" not in names
            return ProviderResponse(
                tool_calls=[ToolCall(
                    id="read-definition",
                    name="get_content_definition",
                    arguments=json.dumps({"name": "campaign_plan"}),
                )]
            )

        results = [
            json.loads(message["content"])
            for message in messages
            if message["role"] == "tool"
        ]
        definition = results[-1]["definition"]
        assert definition["name"] == "campaign_plan"
        assert definition["example"]["format_version"] == 2
        assert definition["schema"]["additionalProperties"] is False
        return ProviderResponse(content="The Campaign Plan definition is available.")

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        response = await self.generate(messages, tools, **kwargs)
        for index, call in enumerate(response.tool_calls):
            yield ProviderStreamChunk(tool_call_delta={
                "index": index,
                "id": call.id,
                "function": {"name": call.name, "arguments": call.arguments},
            })
        yield ProviderStreamChunk(
            content=response.content or "",
            finish_reason="tool_calls" if response.tool_calls else "stop",
        )

    async def close(self) -> None:
        return None


@pytest.mark.parametrize("streaming", [False, True])
def test_builder_history_retains_definition_without_publisher_authority(
    tmp_path: Path, streaming: bool,
) -> None:
    provider = _DefinitionProvider()
    kwargs = {
        "context": _context(),
        "message_id": f"definition-{'stream' if streaming else 'sync'}",
        "user_text": "Read the Campaign Plan definition.",
        "workspace": tmp_path,
        "control": ProviderControl(),
    }

    if streaming:
        completed = []
        events = list(stream_builder_turn(
            provider, **kwargs, complete=completed.append,  # type: ignore[arg-type]
        ))
        evidence = [event.evidence for event in events if event.kind is ProviderEventKind.EVIDENCE]
        result = next(
            item["result"] for item in evidence if item["tool_name"] == "get_content_definition"
        )
        assert completed[0].tool_calls_made == ("get_content_definition",)
    else:
        turn = run_builder_turn(provider, **kwargs)  # type: ignore[arg-type]
        result = next(
            message.content["result"]
            for message in turn.messages
            if message.content.get("tool_name") == "get_content_definition"
        )
        assert turn.tool_calls_made == ("get_content_definition",)

    assert result["success"] is True
    assert result["definition"]["name"] == "campaign_plan"
    assert len(provider.calls) == 2
