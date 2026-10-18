"""Scripted choices execute through real installed-package, MCP and session boundaries."""

from __future__ import annotations

from copy import deepcopy

import pytest
import yaml

from sylvia_gm.agent._provider_session import ProviderSessionAdapter
from sylvia_gm.agent._ruleset_test_provider import RulesetTestProvider, ScenarioScriptError
from sylvia_gm.agent._session_loop import CampaignSessionLoop, SessionRunRequest
from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.application.package_materialization import materialize_package_archive
from sylvia_gm.builder._ruleset_source import compile_ruleset_source
from sylvia_gm.domain._content_schema_ruleset import EXAMPLE
from sylvia_gm.domain.authority import ActionStatus, WriterRole


def _run(tmp_path, replies):
    source = deepcopy(EXAMPLE)
    compiled = compile_ruleset_source(yaml.safe_dump(source).encode())
    with AuthorityOwner.open(
        tmp_path / "scenario.sqlite3",
        "scenario-owner",
        disconnect_grace_seconds=1.0,
    ) as owner:
        with materialize_package_archive((compiled.draft,)) as package:
            candidate = owner.install_package_archive(package.archive, install_id="scenario")
        owner.create_campaign(
            CampaignCreate(
                campaign_id="scenario-campaign",
                display_name="Scenario",
                writer=WriterRole.DM,
                package_lock=candidate.package_lock,
                turn_id="scenario-turn",
            )
        )
        owner.create_agent_session(
            campaign_id="scenario-campaign",
            role=WriterRole.DM,
            session_id="scenario-session",
            provider_id="ruleset-test",
            model_id="ruleset-test/scripted",
        )
        provider = RulesetTestProvider(replies, decision_owner=lambda: "none")
        adapter = ProviderSessionAdapter(owner, provider, owner_id="scenario-owner")
        try:
            result = CampaignSessionLoop(owner).run(
                SessionRunRequest(
                    session_id="scenario-session",
                    turn_id="scenario-turn",
                    attempt_id="scenario-attempt",
                    message_id="scenario-message",
                    user_text="Flip the coin.",
                ),
                provider.observe(adapter.for_turn("scenario-turn")),
            )
        except ScenarioScriptError as error:
            result = error
        return provider, result, owner.snapshot("scenario-campaign")


def test_scripted_provider_runs_the_real_mcp_action_and_narration_path(tmp_path):
    provider, result, snapshot = _run(
        tmp_path,
        [
            {
                "kind": "tool",
                "tool": "flip_coin",
                "arguments": {},
                "expect_request": {"available_tools": ["flip_coin"]},
            },
            {"kind": "narration", "text": "The coin has landed."},
        ],
    )
    provider.assert_finished()
    assert result.interruption is None
    assert [receipt.status for receipt in result.receipts] == [ActionStatus.COMMITTED]
    assert snapshot.revision >= 1
    assert len(provider.requests) == 2
    assert all("flip_coin" in request["available_tools"] for request in provider.requests)


def test_script_failure_keeps_its_type_through_the_real_adapter(tmp_path):
    provider, result, snapshot = _run(
        tmp_path,
        [
            {"kind": "tool", "tool": "flip_coin", "arguments": {}},
        ],
    )
    with pytest.raises(ScenarioScriptError, match="after the saved script ended"):
        provider.assert_finished()
    assert isinstance(result, ScenarioScriptError)
    assert snapshot.revision == 1


def test_rejected_arguments_are_observed_before_a_corrected_action(tmp_path):
    provider, result, snapshot = _run(
        tmp_path,
        [
            {"kind": "tool", "tool": "flip_coin", "arguments": {"invented": True}},
            {"kind": "tool", "tool": "flip_coin", "arguments": {}},
            {"kind": "narration", "text": "The coin has landed."},
        ],
    )
    provider.assert_finished()
    assert result.interruption is None
    assert provider.tool_results[0]["tool"] == "flip_coin"
    assert provider.tool_results[0]["result"]["status"] == "rejected"
    assert len(result.receipts) == 1
    assert snapshot.revision == 1


def test_available_operation_expected_to_be_withheld_fails_before_execution(tmp_path):
    provider, result, snapshot = _run(
        tmp_path,
        [
            {
                "kind": "tool",
                "tool": "flip_coin",
                "arguments": {},
                "expect_request": {"unavailable_tools": ["flip_coin"]},
            },
        ],
    )
    with pytest.raises(ScenarioScriptError, match="withheld"):
        provider.assert_finished()
    assert isinstance(result, ScenarioScriptError)
    assert snapshot.revision == 0


def test_unused_script_does_not_pass_when_the_real_turn_finishes_early(tmp_path):
    provider, result, _snapshot = _run(
        tmp_path,
        [
            {"kind": "tool", "tool": "flip_coin", "arguments": {}},
            {"kind": "narration", "text": "The coin has landed."},
            {"kind": "tool", "tool": "flip_coin", "arguments": {}},
        ],
    )
    assert result.interruption is None
    assert len(result.receipts) == 1
    with pytest.raises(ScenarioScriptError, match="unused scripted replies"):
        provider.assert_finished()
