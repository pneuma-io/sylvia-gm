"""Default model, effort and speed for new chats, and the choice before a first model call.

Every request asserted here is the actual options object a synthetic provider received
through the real owner command boundary. Nothing contacts a real provider, so these
checks prove what Sylvia sends, not provider compatibility or any speed gain.
"""

from dataclasses import replace

import pytest

from sylvia_gm.config import provider_settings as settings_module
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.interfaces._chat_start import resolve_chat_start
from sylvia_gm.provider._base import ModelInfo, ProviderStreamChunk, SpeedOption
from sylvia_gm.provider._reasoning import TurnReasoningProvider, select_reasoning
from sylvia_gm.provider._speed import (
    SpeedSelection,
    configure_speed,
    select_speed,
    speed_options,
)
from tests.test_alpha_f6e_owner_session_transport import (
    _configure_ollama_models,
    _HarnessResolver,
    _OwnerHarness,
    _ScriptedProvider,
)
from tests.test_reasoning_effort import model

OPENAI = "https://api.openai.com/v1"


def info(index: int, model_id: str = "gpt-5.2", endpoint: str = OPENAI) -> ModelInfo:
    """A configured model whose effort and speed capabilities come from the real tables."""
    return replace(
        configure_speed(model(model_id, endpoint=endpoint), endpoint),
        id=f"ollama/model{index}",
        provider_key="ollama",
        context_window=1_000_000,
        declared_context_window=1_000_000,
    )


def row(model_info: ModelInfo) -> dict[str, object]:
    """Project a model the way the owner catalog does."""
    return {
        "provider_id": model_info.provider_key,
        "model_id": model_info.id,
        "model_name": model_info.name,
        "supported_reasoning_efforts": (
            None if model_info.supported_reasoning_efforts is None
            else list(model_info.supported_reasoning_efforts)
        ),
        "default_reasoning_effort": model_info.default_reasoning_effort,
        "reasoning_control": model_info.reasoning_control,
        "reasoning_source": model_info.reasoning_source,
        "speed_options": (
            None if not model_info.speed_options
            else [option.projection() for option in model_info.speed_options]
        ),
        "speed_control": model_info.speed_control,
        "speed_source": model_info.speed_source,
        "transport": model_info.transport,
    }


class RecordingProvider(_ScriptedProvider):
    """Record the exact options of every provider call, then reply with fixed narration."""

    def __init__(self, model_info: ModelInfo) -> None:
        super().__init__((ProviderStreamChunk(content="Synthetic reply.", finish_reason="stop"),))
        self.model_info = model_info
        self.options: list[dict[str, object]] = []

    async def generate_stream(self, messages, tools=None, timeout=120.0, **kwargs):
        self.options.append(dict(kwargs))
        async for chunk in super().generate_stream(messages, tools, timeout, **kwargs):
            yield chunk


@pytest.fixture
def harness(tmp_path, monkeypatch):
    """Run the real owner with two configured models: one with speed tiers, one without."""
    fast = RecordingProvider(info(0))
    plain = RecordingProvider(info(1, endpoint="https://example.invalid/v1"))
    providers = {fast.model_info.id: fast, plain.model_info.id: plain}
    monkeypatch.setattr(
        _HarnessResolver, "__call__",
        lambda self, session: (self.sessions.append(session), providers[session.model_id])[1],
    )
    _configure_ollama_models(tmp_path / "config.yaml")
    owner = _OwnerHarness(tmp_path / "owner.db", fast)
    owner.resolver.model_choices = lambda: (row(fast.model_info), row(plain.model_info))
    owner.fast, owner.plain = fast, plain
    try:
        yield owner
    finally:
        owner.close()


def builder_chat(harness, name="chat", **params):
    chat = harness.request(
        name, "create_builder_chat",
        {"operation_id": name, "provider_id": "ollama", "model_id": "ollama/model0", **params},
    )
    return chat["session"]["session_id"]


def say(harness, session_id, name="input", **params):
    return harness.request(
        name, "submit_session_input",
        {"session_id": session_id, "attempt_id": f"attempt-{name}", "message_id": name,
         "user_text": "Hello.", **params},
    )


def context(harness, session_id):
    return harness.request(f"context-{session_id}", "session_context", {"session_id": session_id})


def game_master(harness, suffix="gm", **params):
    """Take a campaign through the same commands the app uses up to the Game Master handoff."""
    confirmation = harness.request(
        f"confirm-{suffix}", "confirm_new_campaign",
        {"operation_id": f"confirm-{suffix}", "display_name": "Start Settings",
         "candidate_id": None, "turn_id": f"turn-builder-{suffix}",
         "provider_id": "ollama", "model_id": "ollama/model0"},
    )
    campaign_id = confirmation["campaign_id"]
    harness.request(
        f"begin-{suffix}", "begin_campaign_construction",
        {"operation_id": f"begin-{suffix}", "campaign_id": campaign_id,
         "expected_revision": confirmation["resulting_revision"], "lane": "customize",
         "canonical_input": {"test_fixture": suffix}},
    )
    plan = harness.storage_root / "campaigns" / campaign_id / "campaign-plan.md"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text(f"# Plan {suffix}\n", encoding="utf-8")
    command = {"campaign_id": campaign_id, "dm_provider_id": "ollama",
               "dm_model_id": "ollama/model0", **params}
    return campaign_id, command


# ── Capability data: only documented or advertised tiers are offered ─────────────────────


@pytest.mark.parametrize(
    "model_id,transport,endpoint,expected",
    [
        ("gpt-5.6-sol", "responses", OPENAI, ["fast", "flex"]),
        ("gpt-5.6", "responses", OPENAI, ["fast", "flex"]),
        ("gpt-5.5-pro", "responses", OPENAI, ["flex"]),
        ("gpt-4o", "chat_completions", OPENAI, ["fast"]),
        ("gpt-5.2-pro", "responses", OPENAI, None),
        ("gpt-5.6-sol", "responses", "https://openrouter.ai/api/v1", None),
        ("gpt-5.6-sol", "responses", "http://api.openai.com/v1", None),
        ("claude-opus-5-5", "anthropic_messages", "https://api.anthropic.com", ["fast"]),
        ("claude-fable-5-2", "anthropic_messages", "https://api.anthropic.com", ["fast"]),
        ("claude-opus-4-8", "anthropic_messages", "https://api.anthropic.com", None),
        ("claude-sonnet-5-5", "anthropic_messages", "https://api.anthropic.com", None),
        ("claude-opus-5-5", "anthropic_messages", "https://opencode.ai/zen/v1", None),
        ("claude-opus-5-5", "claude_code", "https://api.anthropic.com", None),
        ("gemini-3-pro", "gemini_generate_content", "https://generativelanguage.googleapis.com",
         None),
    ],
)
def test_speed_tiers_are_bound_to_exact_documented_routes(
    model_id, transport, endpoint, expected,
):
    configured = configure_speed(model(model_id, transport=transport, endpoint=endpoint), endpoint)
    offered = None if not configured.speed_options else [o.id for o in configured.speed_options]
    assert offered == expected
    assert (configured.speed_control is None) == (expected is None)


def test_chatgpt_speed_tiers_come_only_from_the_runtime_model_list():
    advertised = speed_options(
        [{"id": "priority", "name": "Fast", "description": "2x speed, increased usage"}]
    )
    base = model("gpt-6.1-sol", transport="codex_app_server", endpoint="https://chatgpt.com")
    listed = configure_speed(replace(base, speed_options=advertised), "https://chatgpt.com")
    assert [option.projection() for option in listed.speed_options] == [
        {"id": "priority", "name": "Fast", "description": "2x speed, increased usage"}
    ]
    assert (listed.speed_control, listed.speed_source) == ("codex", "model/list")
    unlisted = configure_speed(base, "https://chatgpt.com")
    assert unlisted.speed_options is None and unlisted.speed_control is None


@pytest.mark.parametrize(
    "value", [[{"id": "Fast", "name": "Fast"}], [{"id": "standard", "name": "Standard"}],
              [{"id": "fast"}], ["fast"], "fast"],
)
def test_saved_speed_tiers_reject_anything_that_is_not_a_native_option(value):
    with pytest.raises(ValueError):
        speed_options(value)


@pytest.mark.parametrize(
    "control,expected",
    [("openai", {"service_tier": "fast"}), ("codex", {"service_tier": "fast"}),
     ("anthropic", {"speed": "fast"}), (None, {})],
)
def test_a_speed_selection_emits_only_its_route_native_field(control, expected):
    selection = SpeedSelection("fast", "fast", control, "session")
    assert selection.options() == expected
    assert selection.projection()["provider_reported_effective"] is None
    assert SpeedSelection(None, None, control, "provider_default").options() == {}


def test_unsupported_speed_is_refused_when_chosen_and_unsent_when_merely_saved():
    fast, plain = info(0), info(1, endpoint="https://example.invalid/v1")
    assert select_speed(fast, "flex").effective == "flex"
    assert select_speed(fast, "standard").effective is None
    with pytest.raises(CommandRejected, match="does not offer"):
        select_speed(plain, "fast")
    with pytest.raises(CommandRejected, match="does not offer"):
        select_speed(fast, "priority")
    stale = select_speed(plain, "fast", strict=False)
    assert (stale.requested, stale.effective, stale.options()) == ("fast", None, {})
    assert stale.scope == "unavailable_for_model"


def test_one_turn_selection_replaces_call_local_speed_and_effort_options():
    provider = RecordingProvider(info(0))
    bound = TurnReasoningProvider(
        provider, select_reasoning(provider.model_info, "dm", "low"),
        select_speed(provider.model_info, "fast"),
    )
    merged = bound._options({"service_tier": "flex", "speed": "x", "max_tokens": 9})  # noqa: SLF001
    assert merged == {"reasoning": {"effort": "low"}, "service_tier": "fast", "max_tokens": 9}
    standard = TurnReasoningProvider(provider, select_reasoning(provider.model_info, "dm", "low"))
    assert "service_tier" not in standard._options({"service_tier": "flex"})  # noqa: SLF001


def test_anthropic_fast_mode_is_sent_as_the_top_level_body_field():
    from sylvia_gm.provider._anthropic import AnthropicMessagesTransport

    request = AnthropicMessagesTransport().format_request(
        "claude-opus-5-5", [{"role": "user", "content": "Hi"}], None, False, speed="fast",
    )
    assert request["extra_body"] == {"speed": "fast"}
    plain = AnthropicMessagesTransport().format_request(
        "claude-opus-5-5", [{"role": "user", "content": "Hi"}], None, False,
    )
    assert "extra_body" not in plain


# ── Settings: defaults save, reopen and stay separate from existing chats ────────────────


def test_settings_defaults_save_reopen_and_reject_unsupported_choices(harness):
    legacy = harness.request("legacy", "provider_settings")
    assert legacy["chat_defaults"] == {"reasoning_effort": None, "speed": None}
    saved = harness.request(
        "save", "set_chat_defaults",
        {"provider_id": "ollama", "model_id": "ollama/model0",
         "reasoning_effort": "low", "speed": "fast"},
    )
    assert saved["default_model"] == {"provider_id": "ollama", "model_id": "ollama/model0"}
    reopened = harness.request("reopen", "provider_settings")
    assert reopened["chat_defaults"] == {"reasoning_effort": "low", "speed": "fast"}
    assert reopened["default_model"] == saved["default_model"]
    assert [provider["provider_id"] for provider in reopened["configured"]] == ["ollama"]
    for name, bad in (("effort", {"reasoning_effort": "ultra"}), ("speed", {"speed": "priority"}),
                      ("no-speed", {"provider_id": "ollama", "model_id": "ollama/model1",
                                    "speed": "fast"})):
        harness.send(name, {"method": "set_chat_defaults", "params": {
            "provider_id": "ollama", "model_id": "ollama/model0",
            "reasoning_effort": "low", "speed": "standard", **bad}})
        reply = harness.read()
        assert reply["ok"] is False, reply
    assert harness.request("kept", "provider_settings")["chat_defaults"] == {
        "reasoning_effort": "low", "speed": "fast",
    }
    cleared = harness.request(
        "clear", "set_chat_defaults",
        {"provider_id": "ollama", "model_id": "ollama/model1",
         "reasoning_effort": "mode_default", "speed": "standard"},
    )
    assert cleared["chat_defaults"] == {"reasoning_effort": None, "speed": None}
    assert "chat_defaults" not in settings_module._read_user_config(  # noqa: SLF001
        harness.storage_root / "config.yaml"
    )


def test_changing_only_the_default_model_keeps_saved_effort_and_speed(harness):
    harness.request("save", "set_chat_defaults", {
        "provider_id": "ollama", "model_id": "ollama/model0",
        "reasoning_effort": "low", "speed": "fast"})
    harness.request("model", "set_default_model",
                    {"provider_id": "ollama", "model_id": "ollama/model1"})
    settings = harness.request("read", "provider_settings")
    assert settings["default_model"]["model_id"] == "ollama/model1"
    assert settings["chat_defaults"] == {"reasoning_effort": "low", "speed": "fast"}


def test_a_new_chat_falls_back_when_its_model_lacks_the_saved_defaults():
    choices = (row(info(0)), row(info(1, endpoint="https://example.invalid/v1")))
    defaults = {"reasoning_effort": "xhigh", "speed": "fast"}

    def start(model_index, **params):
        return resolve_chat_start(
            choices, defaults, role="dm", provider_id="ollama",
            model_id=f"ollama/model{model_index}", params=params,
        )

    assert (start(0).reasoning_effort, start(0).speed) == ("xhigh", "fast")
    # The second model advertises neither: mode default effort and standard speed.
    assert (start(1).reasoning_effort, start(1).speed) == (None, None)
    chosen = start(0, reasoning_effort="mode_default", speed="standard")
    assert (chosen.reasoning_effort, chosen.speed) == (None, None)
    assert start(0, reasoning_effort="provider_default").reasoning_effort == "provider_default"
    with pytest.raises(CommandRejected, match="does not support"):
        start(1, reasoning_effort="xhigh")
    with pytest.raises(CommandRejected, match="does not offer"):
        start(1, speed="fast")
    unlisted = resolve_chat_start(
        choices, defaults, role="builder", provider_id="unconfigured.local",
        model_id="unconfigured.model", params={},
    )
    assert (unlisted.reasoning_effort, unlisted.speed) == (None, None)


# ── New Builder chats ────────────────────────────────────────────────────────────────────


def test_new_builder_chat_copies_defaults_and_creating_it_sends_nothing(harness):
    harness.request("save", "set_chat_defaults", {
        "provider_id": "ollama", "model_id": "ollama/model0",
        "reasoning_effort": "low", "speed": "flex"})
    session_id = builder_chat(harness)
    opened = context(harness, session_id)
    assert harness.fast.options == [] and harness.plain.options == []
    assert opened["session"]["reasoning_effort"] == "low"
    assert opened["reasoning_state"]["effective_request"] == "low"
    assert opened["speed_state"]["current"] == "flex"
    assert opened["speed_state"]["effective_request"] == "flex"
    assert opened["speed_state"]["provider_reported_effective"] is None
    assert opened["model_state"]["chat_defaults"] == {"reasoning_effort": "low", "speed": "flex"}
    say(harness, session_id)
    assert harness.fast.options, "the first message must reach the provider"
    assert all(o["reasoning"] == {"effort": "low"} for o in harness.fast.options)
    assert all(o["service_tier"] == "flex" for o in harness.fast.options)


def test_changing_settings_later_does_not_change_an_existing_chat(harness):
    harness.request("save", "set_chat_defaults", {
        "provider_id": "ollama", "model_id": "ollama/model0",
        "reasoning_effort": "low", "speed": "fast"})
    old = builder_chat(harness, "old")
    harness.request("change", "set_chat_defaults", {
        "provider_id": "ollama", "model_id": "ollama/model0",
        "reasoning_effort": "high", "speed": "standard"})
    new = builder_chat(harness, "new")
    say(harness, old, "old-input")
    assert harness.fast.options[-1]["reasoning"] == {"effort": "low"}
    assert harness.fast.options[-1]["service_tier"] == "fast"
    say(harness, new, "new-input")
    assert harness.fast.options[-1]["reasoning"] == {"effort": "high"}
    assert "service_tier" not in harness.fast.options[-1]


def test_changing_a_chat_does_not_change_settings_or_other_chats(harness):
    harness.request("save", "set_chat_defaults", {
        "provider_id": "ollama", "model_id": "ollama/model0",
        "reasoning_effort": "low", "speed": "standard"})
    first, second = builder_chat(harness, "first"), builder_chat(harness, "second")
    harness.request("effort", "switch_session_reasoning",
                    {"session_id": first, "effort": "high", "mode": "current"})
    switched = harness.request("speed", "switch_session_speed",
                               {"session_id": first, "speed": "fast"})
    assert switched["speed_state"]["effective_request"] == "fast"
    assert harness.request("read", "provider_settings")["chat_defaults"] == {
        "reasoning_effort": "low", "speed": None,
    }
    say(harness, second, "second-input")
    assert harness.fast.options[-1]["reasoning"] == {"effort": "low"}
    assert "service_tier" not in harness.fast.options[-1]
    say(harness, first, "first-input")
    assert harness.fast.options[-1]["reasoning"] == {"effort": "high"}
    assert harness.fast.options[-1]["service_tier"] == "fast"


def test_a_chosen_start_overrides_defaults_and_an_unsupported_choice_creates_nothing(harness):
    harness.request("save", "set_chat_defaults", {
        "provider_id": "ollama", "model_id": "ollama/model0",
        "reasoning_effort": "low", "speed": "fast"})
    session_id = builder_chat(harness, "chosen", reasoning_effort="xhigh", speed="standard")
    say(harness, session_id)
    assert harness.fast.options[-1]["reasoning"] == {"effort": "xhigh"}
    assert "service_tier" not in harness.fast.options[-1]
    before = len(harness.request("index", "builder_chat_index"))
    harness.send("bad", {"method": "create_builder_chat", "params": {
        "operation_id": "bad", "provider_id": "ollama", "model_id": "ollama/model1",
        "speed": "fast"}})
    assert harness.read()["ok"] is False
    assert len(harness.request("index-after", "builder_chat_index")) == before


# ── Chat footer: speed belongs to the chat and to its model ──────────────────────────────


def test_chat_speed_survives_a_restart_and_is_refused_for_an_unknown_tier(harness, tmp_path):
    session_id = builder_chat(harness)
    harness.request("fast", "switch_session_speed", {"session_id": session_id, "speed": "fast"})
    harness.send("bad", {"method": "switch_session_speed",
                         "params": {"session_id": session_id, "speed": "priority"}})
    assert harness.read()["ok"] is False
    assert settings_module.session_speed_settings(
        tmp_path / "config.yaml", session_id=session_id,
    ) == "fast"
    harness.request("standard", "switch_session_speed",
                    {"session_id": session_id, "speed": "standard"})
    assert "session_speed" not in settings_module._read_user_config(  # noqa: SLF001
        tmp_path / "config.yaml"
    )


def test_switching_models_clears_a_speed_the_new_model_does_not_offer(harness):
    session_id = builder_chat(harness)
    harness.request("fast", "switch_session_speed", {"session_id": session_id, "speed": "fast"})
    switched = harness.request("switch", "switch_session_model", {
        "session_id": session_id, "provider_id": "ollama", "model_id": "ollama/model1",
        "mode": "current"})
    assert switched["speed_cleared"] is True
    assert switched["speed_state"]["current"] is None
    assert switched["speed_state"]["supported"] is None
    say(harness, session_id)
    assert "service_tier" not in harness.plain.options[-1]


def test_a_one_reply_model_without_the_tier_runs_standard_and_keeps_the_chat_speed(harness):
    session_id = builder_chat(harness)
    harness.request("fast", "switch_session_speed", {"session_id": session_id, "speed": "fast"})
    harness.request("once", "switch_session_model", {
        "session_id": session_id, "provider_id": "ollama", "model_id": "ollama/model1",
        "mode": "once"})
    pending = context(harness, session_id)["speed_state"]
    assert (pending["current"], pending["effective_request"]) == ("fast", None)
    assert pending["scope"] == "unavailable_for_model" and pending["valid"] is False
    say(harness, session_id, "once-input")
    assert "service_tier" not in harness.plain.options[-1]
    say(harness, session_id, "next-input")
    assert harness.fast.options[-1]["service_tier"] == "fast"


# ── Game Master: the opening call uses what was chosen before the handoff ────────────────


def opening(harness, campaign_id, finalized):
    return harness.request("open", "start_dm_session", {
        "campaign_id": campaign_id, "session_id": finalized["handoff"]["dm_session_id"],
        "turn_id": finalized["dm_turn_id"]})


def test_game_master_opening_uses_the_start_chosen_before_handoff(harness):
    harness.request("audit", "set_admin_full_audit", {"enabled": True})
    harness.request("save", "set_chat_defaults", {
        "provider_id": "ollama", "model_id": "ollama/model0",
        "reasoning_effort": "low", "speed": "standard"})
    campaign_id, command = game_master(harness, reasoning_effort="xhigh", speed="fast")
    finalized = harness.request("finalize", "finalize_campaign_construction", command)
    assert harness.fast.options == [], "the handoff itself must not call the model"
    assert finalized["chat_start"] == {
        "provider_id": "ollama", "model_id": "ollama/model0",
        "reasoning_effort": "xhigh", "speed": "fast",
    }
    opening(harness, campaign_id, finalized)
    assert harness.fast.options, "the opening must reach the provider"
    # The first request and every continuation of the opening carry the same selection.
    assert all(o["reasoning"] == {"effort": "xhigh"} for o in harness.fast.options)
    assert all(o["service_tier"] == "fast" for o in harness.fast.options)
    count = harness.request("count", "admin_full_audit_status")["trace_count"]
    records = [
        harness.request(f"trace-{index}", "admin_full_audit_record", {"index": index})["trace"]
        for index in range(count)
    ]
    requests = [record for record in records if record["kind"] == "provider_request"]
    assert requests and all(
        record["correlation"]["speed_selection"] == {
            "requested": "fast", "effective_request": "fast", "scope": "session",
            "native_options": {"service_tier": "fast"}, "provider_reported_effective": None,
        }
        for record in requests
    )
    session_id = finalized["handoff"]["dm_session_id"]
    reopened = context(harness, session_id)
    assert reopened["session"]["reasoning_effort"] == "xhigh"
    assert reopened["speed_state"]["current"] == "fast"
    sent = len(harness.fast.options)
    again = opening(harness, campaign_id, finalized)
    assert again["status"] == "already_started" and len(harness.fast.options) == sent


def test_game_master_inherits_defaults_when_nothing_is_chosen(harness):
    harness.request("save", "set_chat_defaults", {
        "provider_id": "ollama", "model_id": "ollama/model0",
        "reasoning_effort": "medium", "speed": "flex"})
    campaign_id, command = game_master(harness)
    finalized = harness.request("finalize", "finalize_campaign_construction", command)
    opening(harness, campaign_id, finalized)
    assert all(o["reasoning"] == {"effort": "medium"} for o in harness.fast.options)
    assert all(o["service_tier"] == "flex" for o in harness.fast.options)


def test_game_master_without_saved_defaults_keeps_the_mode_default(harness):
    campaign_id, command = game_master(harness)
    finalized = harness.request("finalize", "finalize_campaign_construction", command)
    assert finalized["chat_start"]["reasoning_effort"] == "mode_default"
    assert finalized["chat_start"]["speed"] == "standard"
    opening(harness, campaign_id, finalized)
    assert all(o["reasoning"] == {"effort": "high"} for o in harness.fast.options)
    assert all("service_tier" not in o for o in harness.fast.options)


def test_an_unsupported_game_master_choice_is_refused_before_the_handoff(harness):
    campaign_id, command = game_master(harness, speed="priority")
    harness.send("finalize", {"method": "finalize_campaign_construction", "params": command})
    refused = harness.read()
    assert refused["ok"] is False and "does not offer" in refused["error"]["message"]
    assert harness.fast.options == []
    index = harness.request("index", "campaign_index")
    campaign = next(entry for entry in index if entry["campaign_id"] == campaign_id)
    assert [session["role"] for session in campaign["sessions"]] == ["builder"]
    # The same campaign can still start once a supported speed is chosen.
    finalized = harness.request(
        "retry", "finalize_campaign_construction", {**command, "speed": "standard"},
    )
    opening(harness, campaign_id, finalized)
    assert all("service_tier" not in o for o in harness.fast.options)


def test_speed_option_projection_round_trips_through_saved_settings():
    option = SpeedOption("priority", "Fast", "2x speed")
    assert speed_options([option.projection()]) == (option,)


# ── Replays and the Builder chats the Interview opens ────────────────────────────────────


def test_replaying_a_creation_command_leaves_the_existing_chat_alone(harness):
    harness.request("save", "set_chat_defaults", {
        "provider_id": "ollama", "model_id": "ollama/model0",
        "reasoning_effort": "low", "speed": "fast"})
    session_id = builder_chat(harness, "same")
    harness.request("effort", "switch_session_reasoning",
                    {"session_id": session_id, "effort": "high", "mode": "current"})
    harness.request("speed", "switch_session_speed",
                    {"session_id": session_id, "speed": "standard"})
    harness.request("change", "set_chat_defaults", {
        "provider_id": "ollama", "model_id": "ollama/model0",
        "reasoning_effort": "medium", "speed": "flex"})
    replayed = harness.request("replay", "create_builder_chat", {
        "operation_id": "same", "provider_id": "ollama", "model_id": "ollama/model0",
        "reasoning_effort": "xhigh", "speed": "fast"})
    assert replayed["session"]["session_id"] == session_id
    current = context(harness, session_id)
    assert current["session"]["reasoning_effort"] == "high"
    assert current["speed_state"]["current"] is None


def test_interview_builder_chat_starts_on_the_chosen_model_effort_and_speed(harness):
    harness.request("save", "set_chat_defaults", {
        "provider_id": "ollama", "model_id": "ollama/model0",
        "reasoning_effort": "low", "speed": "standard"})
    draft = harness.request("draft", "create_guided_campaign_draft", {
        "operation_id": "start-draft", "display_name": "Start Settings",
        "provider_id": "ollama", "model_id": "ollama/model1"})
    revision = int(draft["construction_revision"])
    for step, visible, answer in (
        ("ruleset", "D&D SRD", {"candidate_id": "sylvia.srd-lite.bg3"}),
        ("setting", "Severed Lands", {"setting_id": "pneuma.severed-lands"}),
        ("plan", "Rift Awakening", {"plan_id": "rift-awakening"}),
        ("name", "Start Settings", {"display_name": "Start Settings"}),
    ):
        advanced = harness.request(f"answer-{step}", "advance_guided_campaign_draft", {
            "operation_id": f"start-{step}", "campaign_id": draft["campaign_id"],
            "builder_session_id": draft["builder_session_id"],
            "expected_construction_revision": revision, "current_step": step,
            "visible_answer": visible, "answer": answer})
        revision = int(advanced["construction_revision"])
    # The Interview copied only what its own model supports: nothing, on this model.
    interview = context(harness, draft["builder_session_id"])
    assert interview["session"]["reasoning_effort"] is None
    launch = {
        "operation_id": "start-character", "campaign_id": draft["campaign_id"],
        "parent_session_id": draft["builder_session_id"],
        "expected_construction_revision": revision, "purpose": "player_character",
        "visible_answer": "Create New Player Character", "trigger_message_id": None,
        "provider_id": "ollama", "model_id": "ollama/model0",
        "reasoning_effort": "xhigh", "speed": "fast",
    }
    subflow = harness.request("launch", "start_guided_creation_subflow", launch)
    assert subflow["status"] == "independent"
    child = subflow["child_session_id"]
    opened = context(harness, child)
    assert opened["session"]["model_id"] == "ollama/model0"
    assert opened["session"]["reasoning_effort"] == "xhigh"
    assert opened["speed_state"]["current"] == "fast"
    assert harness.fast.options == [] and harness.plain.options == [], "launching sends nothing"
    harness.request("start", "submit_session_input", {
        "session_id": child, "attempt_id": "start", "message_id": "start",
        "builder_start": True})
    assert harness.plain.options == []
    assert harness.fast.options, "the automatic first call must reach the chosen model"
    assert all(o["reasoning"] == {"effort": "xhigh"} for o in harness.fast.options)
    assert all(o["service_tier"] == "fast" for o in harness.fast.options)
    # A replayed launch returns the same chat and leaves its later settings alone.
    harness.request("effort", "switch_session_reasoning",
                    {"session_id": child, "effort": "low", "mode": "current"})
    assert harness.request("replay", "start_guided_creation_subflow", launch) == subflow
    assert context(harness, child)["session"]["reasoning_effort"] == "low"


def test_interview_builder_chat_without_a_choice_inherits_the_parent_model_and_defaults(harness):
    harness.request("save", "set_chat_defaults", {
        "provider_id": "ollama", "model_id": "ollama/model0",
        "reasoning_effort": "medium", "speed": "flex"})
    draft = harness.request("draft", "create_guided_campaign_draft", {
        "operation_id": "inherit-draft", "display_name": "Inherit",
        "provider_id": "ollama", "model_id": "ollama/model0"})
    assert context(harness, draft["builder_session_id"])["session"]["reasoning_effort"] == "medium"
    subflow = harness.request("launch", "start_guided_creation_subflow", {
        "operation_id": "inherit-ruleset", "campaign_id": draft["campaign_id"],
        "parent_session_id": draft["builder_session_id"],
        "expected_construction_revision": int(draft["construction_revision"]),
        "purpose": "ruleset", "visible_answer": "Create New Ruleset",
        "trigger_message_id": None})
    opened = context(harness, subflow["child_session_id"])
    assert opened["session"]["model_id"] == "ollama/model0"
    assert opened["session"]["reasoning_effort"] == "medium"
    assert opened["speed_state"]["current"] == "flex"
    assert harness.fast.options == []
