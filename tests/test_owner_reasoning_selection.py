"""Reasoning selection through the real owner command and saved-session boundary."""

import json
from dataclasses import replace
from threading import Event

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import WriterRole
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.session import SessionRecord
from sylvia_gm.interfaces import owner_process
from sylvia_gm.interfaces._session_reasoning import reasoning_state
from sylvia_gm.provider._base import ProviderStreamChunk
from tests.test_alpha_f6e_owner_session_transport import (
    _CampaignDiscoveryProvider,
    _configure_ollama_models,
    _ControlledBlockingBuilderProvider,
    _create_campaign,
    _FailThenRetryProvider,
    _HarnessResolver,
    _OwnerHarness,
)
from tests.test_reasoning_effort import model


def catalog():
    """Supply synthetic advertised capabilities independently of real provider connections."""
    return tuple(
        {
            "provider_id": "ollama",
            "model_id": f"ollama/model{index}",
            "model_name": "Synthetic",
            "supported_reasoning_efforts": levels,
            "default_reasoning_effort": "low",
            "reasoning_control": "codex",
            "reasoning_source": "model/list",
            "transport": "codex_app_server",
        }
        for index, levels in enumerate((["low", "high", "xhigh"], ["low", "high"]))
    )


@pytest.fixture
def owner_commands(tmp_path):
    _configure_ollama_models(tmp_path / "config.yaml")
    with AuthorityOwner.open(tmp_path / "owner.db", "owner") as owner:
        owner.create_builder_chat(provider_id="ollama", model_id="ollama/model0", session_id="chat")
        loop = owner_process.CampaignSessionLoop(owner)

        def command(method, **params):
            return owner_process._dispatch(
                None,
                method,
                {"session_id": "chat", **params},
                application_owner=owner,
                owner_id="owner",
                provider_resolver=lambda _session: pytest.fail("Unexpected provider"),
                model_choices=catalog,
                session_loop=loop,
                storage_root=tmp_path,
            )

        yield owner, command


def test_defaults_saved_and_next_reply_are_distinct_owner_selections(owner_commands):
    owner, command = owner_commands
    before = owner.session_context("chat")
    state = command("session_context")["reasoning_state"]
    assert state["effective_request"] == "xhigh"
    assert state["scope"] == "mode_default"
    assert state["provider_reported_effective"] is None
    saved = command("switch_session_reasoning", effort="low", mode="current")
    assert saved["reasoning_state"]["effective_request"] == "low"
    once = command("switch_session_reasoning", effort="mode_default", mode="once")
    assert once["session"].reasoning_effort == "low"
    assert once["session"].reasoning_effort_once == "mode_default"
    assert once["reasoning_state"]["scope"] == "once"
    assert once["reasoning_state"]["effective_request"] == "xhigh"
    cleared = command("switch_session_reasoning", effort=None, mode="once")
    assert cleared["reasoning_state"]["effective_request"] == "low"
    assert cleared["reasoning_state"]["scope"] == "session"
    assert owner.session_context("chat").messages == before.messages


def test_invalid_selection_and_model_switch_preserve_the_saved_chat(owner_commands):
    owner, command = owner_commands
    command("switch_session_reasoning", effort="xhigh", mode="current")
    before = owner.session_context("chat")
    with pytest.raises(CommandRejected, match="does not support"):
        command("switch_session_reasoning", effort="ultra", mode="once")
    with pytest.raises(CommandRejected, match="destination model"):
        command("switch_session_model", provider_id="ollama", model_id="ollama/model1")
    assert owner.session_context("chat") == before
    command("switch_session_reasoning", effort="high", mode="current")
    command("switch_session_reasoning", effort="low", mode="once")
    switched = command("switch_session_model", provider_id="ollama", model_id="ollama/model1")
    assert switched["session"].reasoning_effort == "high"
    assert switched["session"].reasoning_effort_once == "low"


@pytest.mark.parametrize("first", ["current", "once"])
def test_stale_saved_and_once_preferences_can_be_cleared_independently(owner_commands, first):
    owner, command = owner_commands
    # Simulate a saved capability that the provider no longer advertises.
    owner.update_agent_session_reasoning("chat", effort="ultra", once="ultra")
    changed = command("switch_session_reasoning", effort=None, mode=first)
    assert not changed["reasoning_state"]["valid"]
    changed = command("switch_session_reasoning", effort=None,
                      mode="once" if first == "current" else "current")
    assert changed["reasoning_state"]["valid"]
    assert changed["reasoning_state"]["effective_request"] == "xhigh"


def test_one_reply_model_validates_its_effective_effort_and_preserves_saved_defaults(
    owner_commands,
):
    _, command = owner_commands
    command("switch_session_reasoning", effort="xhigh", mode="current")
    with pytest.raises(CommandRejected, match="destination model"):
        command("switch_session_model", provider_id="ollama", model_id="ollama/model1", mode="once")
    command("switch_session_reasoning", effort="high", mode="once")
    command("switch_session_model", provider_id="ollama", model_id="ollama/model1", mode="once")
    state = command("session_context")["reasoning_state"]
    assert state["model_id"] == "ollama/model1"
    assert state["current"] == "xhigh"
    assert state["effective_request"] == "high"
    with pytest.raises(CommandRejected, match="does not support"):
        command("switch_session_reasoning", effort="xhigh", mode="once")


def test_mode_default_and_unknown_capabilities_have_honest_projection():
    session = SessionRecord("session", "campaign", WriterRole.DM, "ollama", "ollama/model0")
    assert reasoning_state(session, catalog())["effective_request"] == "high"
    state = reasoning_state(session, ())
    assert state["supported"] is None
    assert state["effective_request"] is None
    assert state["native_options"] == {}
    invalid = reasoning_state(replace(session, reasoning_effort="high"), ())
    assert invalid["valid"] is False
    assert invalid["effective_request"] is None


def test_effort_change_is_rejected_during_active_generation(tmp_path):
    provider = _ControlledBlockingBuilderProvider()
    provider.model_info = native_info()
    harness = _OwnerHarness(tmp_path / "owner.db", provider)
    try:
        advertise(harness)
        chat = harness.request(
            "create",
            "create_builder_chat",
            {
                "operation_id": "create",
                "provider_id": "test.transport",
                "model_id": "test.transport/model0",
            },
        )
        session_id = chat["session"]["session_id"]
        # Prepare the ordinary catalog before testing active-generation rejection.
        harness.request("catalog-ready", "campaign_creation_catalog", {})
        harness.request(
            "saved",
            "switch_session_reasoning",
            {
                "session_id": session_id,
                "effort": "low",
                "mode": "current",
            },
        )
        harness.request(
            "once",
            "switch_session_reasoning",
            {
                "session_id": session_id,
                "effort": "high",
                "mode": "once",
            },
        )
        harness.send(
            "submit",
            {
                "method": "submit_session_input",
                "params": {
                    "session_id": session_id,
                    "attempt_id": "attempt",
                    "message_id": "input",
                    "user_text": "Synthetic blocking reply",
                },
            },
        )
        assert provider.started.wait(2)
        harness.send(
            "change",
            {
                "method": "switch_session_reasoning",
                "params": {
                    "session_id": session_id,
                    "effort": "provider_default",
                    "mode": "current",
                },
            },
        )
        rejected = harness.read()
        assert rejected["id"] == "change"
        assert rejected["ok"] is False
        assert "active provider work" in rejected["error"]["message"]
        harness.request(
            "interrupt",
            "interrupt_provider_stream",
            {
                "campaign_id": session_id,
                "attempt_id": "attempt",
            },
        )
        submitted = harness.read()
        assert submitted["id"] == "submit" and submitted["ok"] is False
        context = harness.request("context", "session_context", {"session_id": session_id})
        assert context["session"]["reasoning_effort"] == "low"
        assert context["session"]["reasoning_effort_once"] is None
    finally:
        provider.release.set()
        harness.close()


def native_info():
    """Give synthetic request capture the same capability object as a configured GPT transport."""
    return replace(
        model(),
        id="test.transport/model0",
        provider_key="test.transport",
        context_window=1_000_000,
        declared_context_window=1_000_000,
    )


def advertise(harness):
    info = native_info()
    harness.resolver.model_choices = lambda: (
        {
            "provider_id": info.provider_key,
            "model_id": info.id,
            "supported_reasoning_efforts": list(info.supported_reasoning_efforts),
            "default_reasoning_effort": info.default_reasoning_effort,
            "reasoning_control": info.reasoning_control,
            "reasoning_source": info.reasoning_source,
            "transport": info.transport,
        },
    )


class EffortBuilder(_CampaignDiscoveryProvider):
    def __init__(self):
        super().__init__()
        self.model_info = native_info()
        self.options = []

    async def generate_stream(self, messages, tools=None, timeout=120, **kwargs):
        self.options.append(kwargs)
        async for chunk in super().generate_stream(messages, tools, timeout, **kwargs):
            yield chunk


@pytest.mark.parametrize("override", [None, "high"])
def test_builder_turn_keeps_effort_through_real_tools_and_consumes_override(tmp_path, override):
    provider = EffortBuilder()
    harness = _OwnerHarness(tmp_path / "owner.db", provider)
    try:
        advertise(harness)
        harness.request("audit", "set_admin_full_audit", {"enabled": True})
        chat = harness.request(
            "create",
            "create_builder_chat",
            {
                "operation_id": "create",
                "provider_id": "test.transport",
                "model_id": "test.transport/model0",
            },
        )
        session_id = chat["session"]["session_id"]
        if override is not None:
            harness.request(
                "saved",
                "switch_session_reasoning",
                {
                    "session_id": session_id,
                    "effort": "low",
                    "mode": "current",
                },
            )
            harness.request(
                "once",
                "switch_session_reasoning",
                {
                    "session_id": session_id,
                    "effort": override,
                    "mode": "once",
                },
            )
        harness.request(
            "submit",
            "submit_session_input",
            {
                "session_id": session_id,
                "attempt_id": "attempt",
                "message_id": "input",
                "user_text": "Find The Rift Awakening, but do not start playing.",
            },
        )
        expected = override or "xhigh"
        assert len(provider.options) == 3
        assert all(options["reasoning"] == {"effort": expected} for options in provider.options)
        assert all(options["max_tokens"] == 4096 for options in provider.options)
        count = harness.request("audit-count", "admin_full_audit_status")["trace_count"]
        requests = [
            harness.request(f"trace-{index}", "admin_full_audit_record", {"index": index})["trace"]
            for index in range(count)
        ]
        requests = [record for record in requests if record["kind"] == "provider_request"]
        assert len(requests) == 3
        for record in requests:
            assert record["options"]["reasoning"] == {"effort": expected}
            assert record["correlation"]["reasoning_selection"]["effective_request"] == expected
            assert (
                record["correlation"]["reasoning_selection"]["provider_reported_effective"] is None
            )
        context = harness.request("context", "session_context", {"session_id": session_id})
        assert context["reasoning_state"]["once"] is None
        harness.request(
            "second",
            "submit_session_input",
            {
                "session_id": session_id,
                "attempt_id": "second",
                "message_id": "second-input",
                "user_text": "Thank you.",
            },
        )
        assert provider.options[-1]["reasoning"] == {"effort": "low" if override else "xhigh"}
    finally:
        harness.close()


class EffortRetry(_FailThenRetryProvider):
    def __init__(self):
        super().__init__()
        self.model_info = native_info()
        self.options = []

    async def generate_stream(self, messages, tools=None, timeout=120, **kwargs):
        self.options.append(kwargs)
        update = next(
            (
                tool["function"]["name"]
                for tool in tools or []
                if tool["function"]["name"].endswith("_update_campaign_file")
            ),
            None,
        )
        if (
            self.calls
            and kwargs.get("tool_choice") == "required"
            and update is not None
        ):
            self.calls.append((messages.copy(), tools))
            yield ProviderStreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": "settle-effort-retry",
                    "function": {
                        "name": update,
                        "arguments": json.dumps(
                            {
                                "state_change": "unchanged",
                                "state_change_summary": "No synthetic state change.",
                                "revised_player_facing_response": [
                                    {"kind": "narration", "text": "Recovered narration."},
                                ],
                            }
                        ),
                    },
                },
                finish_reason="tool_calls",
            )
            return
        async for chunk in super().generate_stream(messages, tools, timeout, **kwargs):
            yield chunk


def test_gm_failed_once_is_consumed_and_manual_retry_uses_current_selection(tmp_path):
    provider = EffortRetry()
    harness = _OwnerHarness(tmp_path / "owner.db", provider)
    try:
        advertise(harness)
        campaign_id, turn_id, session_id = _create_campaign(harness, "effort-retry")
        state = harness.request("default", "session_context", {"session_id": session_id})
        assert state["reasoning_state"]["effective_request"] == "high"
        harness.request(
            "once",
            "switch_session_reasoning",
            {
                "session_id": session_id,
                "effort": "low",
                "mode": "once",
            },
        )
        harness.send(
            "submit",
            {
                "method": "submit_session_input",
                "params": {
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "attempt_id": "failed",
                    "message_id": "input",
                    "user_text": "Continue.",
                },
            },
        )
        reply = harness.read()
        assert reply["id"] == "submit" and reply["ok"] is False
        assert provider.options[0]["reasoning"] == {"effort": "low"}
        state = harness.request("state", "session_context", {"session_id": session_id})
        assert state["reasoning_state"]["once"] is None
        harness.request(
            "change",
            "switch_session_reasoning",
            {
                "session_id": session_id,
                "effort": "xhigh",
                "mode": "current",
            },
        )
        interruption = harness.request(
            "interruption",
            "retryable_interruption",
            {
                "campaign_id": campaign_id,
                "failed_attempt_id": "failed",
            },
        )
        harness.request(
            "retry",
            "manual_retry_session",
            {
                "session_id": session_id,
                "interruption_id": interruption["interruption_id"],
                "retry_id": "retry",
            },
        )
        assert len(provider.options) == 2
        assert all(options["reasoning"] == {"effort": "xhigh"} for options in provider.options[1:])
        assert all("service_tier" not in options for options in provider.options)
    finally:
        harness.close()


@pytest.mark.parametrize(
    "stage,resolution_fails", [("resolve", False), ("resolve", True), ("queued", False)],
)
def test_preparing_request_rejects_setting_changes_and_cleans_up_once(
    tmp_path,
    monkeypatch,
    stage,
    resolution_fails,
):
    entered, release = Event(), Event()
    original = _HarnessResolver.__call__

    def resolve(self, session):
        entered.set()
        assert release.wait(5)
        if resolution_fails:
            raise CommandRejected("Synthetic unavailable provider")
        return original(self, session)

    if stage == "resolve":
        monkeypatch.setattr(_HarnessResolver, "__call__", resolve)
    else:
        original_dispatch = owner_process._dispatch

        def queued(bus, method, params, **kwargs):
            if method == "submit_session_input":
                entered.set()
                assert release.wait(5)
            return original_dispatch(bus, method, params, **kwargs)

        monkeypatch.setattr(owner_process, "_dispatch", queued)
    provider = EffortBuilder()
    harness = _OwnerHarness(tmp_path / "owner.db", provider)
    try:
        advertise(harness)
        chat = harness.request(
            "create",
            "create_builder_chat",
            {
                "operation_id": "create",
                "provider_id": "test.transport",
                "model_id": "test.transport/model0",
            },
        )
        session_id = chat["session"]["session_id"]
        harness.request(
            "once",
            "switch_session_reasoning",
            {
                "session_id": session_id,
                "effort": "low",
                "mode": "once",
            },
        )
        harness.send(
            "submit",
            {
                "method": "submit_session_input",
                "params": {
                    "session_id": session_id,
                    "attempt_id": "attempt",
                    "message_id": "input",
                    "user_text": "Find The Rift Awakening, but do not start playing.",
                },
            },
        )
        assert entered.wait(2)
        harness.send(
            "change",
            {
                "method": "switch_session_reasoning",
                "params": {
                    "session_id": session_id,
                    "effort": "high",
                    "mode": "current",
                },
            },
        )
        rejected = harness.read()
        assert rejected["id"] == "change" and rejected["ok"] is False
        assert "pending or active" in rejected["error"]["message"]
        if stage == "queued":
            harness.send("duplicate", {"method": "submit_session_input", "params": {
                "session_id": session_id, "attempt_id": "duplicate", "message_id": "duplicate",
                "user_text": "Duplicate synthetic input.",
            }})
            duplicate = harness.read()
            assert duplicate["id"] == "duplicate" and duplicate["ok"] is False
            assert "pending or active" in duplicate["error"]["message"]
        release.set()
        submitted = harness.read()
        assert submitted["id"] == "submit"
        assert submitted["ok"] is not resolution_fails
        context = harness.request("context", "session_context", {"session_id": session_id})
        assert context["session"]["reasoning_effort_once"] is None
        assert context["session"]["reasoning_effort"] is None
        if resolution_fails:
            assert provider.options == []
        else:
            assert all(options["reasoning"] == {"effort": "low"} for options in provider.options)
    finally:
        release.set()
        harness.close()
