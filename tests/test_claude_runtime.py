"""Native-runtime admission, canonical history and process-owned failure checks."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from sylvia_gm.agent._provider_resolver import ConfiguredProviderResolver
from sylvia_gm.config import claude_settings
from sylvia_gm.config._resolver import ConfigError
from sylvia_gm.config.provider_settings import (
    discover_provider_models,
    provider_settings,
    validate_provider_model,
)
from sylvia_gm.domain.authority import WriterRole
from sylvia_gm.domain.session import SessionRecord
from sylvia_gm.provider import _claude, _claude_runtime
from sylvia_gm.provider._base import ModelInfo, ProviderConfig
from sylvia_gm.provider._claude import ClaudeProvider, prepare_request
from sylvia_gm.provider._claude_runtime import ClaudeConnection
from sylvia_gm.provider._exceptions import (
    AuthenticationError,
    IncompleteProviderStreamError,
    InvalidResponseError,
    ModelNotFoundError,
    ProviderConnectionError,
    ProviderError,
    ProviderTimeoutError,
    RateLimitError,
    TokenLimitError,
)
from sylvia_gm.provider._factory import ProviderFactory
from sylvia_gm.provider._output_policy import output_allowance

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "inspect",
            "description": "Inspect a door",
            "parameters": {
                "type": "object",
                "properties": {"target": {"type": "string"}},
                "required": ["target"],
            },
        },
    }
]


def model() -> ModelInfo:
    return ModelInfo(
        id="claude-runtime/opus",
        name="Claude",
        provider_name="Claude",
        provider_key="claude_runtime",
        provider_mode="subscription",
        transport="claude_code",
        provider_model_id="opus",
        max_output_tokens=None,
    )


def tool_history() -> list[dict]:
    return [
        {"role": "system", "content": "Only saved campaign facts are authoritative."},
        {"role": "user", "content": "Inspect the door."},
        {
            "role": "assistant",
            "content": "I check it.",
            "tool_calls": [
                {
                    "id": "call_saved",
                    "type": "function",
                    "function": {"name": "inspect", "arguments": '{"target":"door"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_saved", "content": "The door is open."},
    ]


class Connection:
    """SDK events at the native process boundary; game operations never run here."""

    def __init__(self, events: list[dict | Exception]) -> None:
        self.events = iter(events)
        self.writes = []
        self.closed = False
        self.arguments = []

    def start(self):
        pass

    def write(self, event):
        self.writes.append(event)

    def next_event(self, timeout):
        try:
            event = next(self.events)
        except StopIteration:
            raise ProviderConnectionError("native EOF") from None
        if isinstance(event, Exception):
            raise event
        return event

    def close(self):
        self.closed = True


def provider(tmp_path, monkeypatch, events):
    connection = Connection(events)
    monkeypatch.setattr(_claude, "find_runtime", lambda _: Path(sys.executable))
    monkeypatch.setattr(_claude, "runtime_version", lambda *_: "2.1.322")

    def factory(executable, workspace, arguments, environment):
        connection.arguments = arguments
        connection.workspace = workspace
        connection.environment = environment
        return connection

    return ClaudeProvider(tmp_path / "runtime", model(), connection_factory=factory), connection


def native_call(name="inspect", arguments=None):
    return {
        "type": "control_request",
        "request_id": "rpc-call",
        "request": {
            "subtype": "mcp_message",
            "server_name": "sylvia",
            "message": {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {"target": "door"}},
            },
        },
    }


def native_message(*blocks):
    """A real Messages stream, including completed native signatures and tool inputs."""
    events = []
    for index, block in enumerate(blocks):
        kind = block["type"]
        initial = {**block}
        field = {"text": "text", "thinking": "thinking"}.get(kind)
        if field:
            initial[field] = ""
        elif kind == "tool_use":
            initial["input"] = {}
        events.append({"type": "content_block_start", "index": index, "content_block": initial})
        if field:
            events.append(
                {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {"type": field + "_delta", field: block[field]},
                }
            )
        elif kind == "tool_use":
            events.append(
                {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": json.dumps(block["input"]),
                    },
                }
            )
        events.append({"type": "content_block_stop", "index": index})
    events.append({"type": "message_stop"})
    return [{"type": "stream_event", "event": event} for event in events]


def test_native_history_keeps_roles_pairs_and_does_not_mutate_source(tmp_path):
    source = tool_history()
    before = json.dumps(source)
    system, records, prompt, tools = prepare_request(source, TOOLS, "opus", tmp_path)
    assert system == source[0]["content"]
    assert [r["message"]["role"] for r in records] == ["user", "assistant", "user"]
    assert records[1]["message"]["content"][1] == {
        "type": "tool_use",
        "id": "call_saved",
        "name": "mcp__sylvia__inspect",
        "input": {"target": "door"},
    }
    result = records[2]["message"]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "call_saved"
    assert result["content"] == "The door is open."
    assert prompt["isSynthetic"] is True
    assert "not player speech" in prompt["message"]["content"]
    assert tools[0]["name"] == "inspect"
    assert json.dumps(source) == before
    _, history, player, _ = prepare_request(
        source + [{"role": "user", "content": "/fast"}], TOOLS, "opus", tmp_path
    )
    assert len(history) == 3 and player["message"] == {"role": "user", "content": "/fast"}


def test_unknown_roles_are_rejected(tmp_path):
    with pytest.raises(InvalidResponseError):
        prepare_request([{"role": "surprise", "content": "private"}], None, "opus", tmp_path)


def test_application_owned_input_uses_native_non_user_marker(tmp_path):
    _, _, prompt, _ = prepare_request(
        [
            {"role": "user", "_application_data": True, "content": "Application-owned source"},
        ],
        None,
        "opus",
        tmp_path,
    )
    assert prompt["isSynthetic"] is True
    assert prompt["message"]["content"] == "Application-owned source"


def test_stream_returns_one_admitted_tool_only_after_child_collection(tmp_path, monkeypatch):
    events = native_message(
        {"type": "text", "text": "Checking."},
        {
            "type": "tool_use",
            "id": "call_owned",
            "name": "mcp__sylvia__inspect",
            "input": {"target": "door"},
        },
    ) + [native_call()]
    adapter, connection = provider(tmp_path, monkeypatch, events)

    async def run():
        chunks = []
        async for chunk in adapter.generate_stream([{"role": "user", "content": "Check."}], TOOLS):
            if chunk.tool_call_delta:
                assert connection.closed
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(run())
    assert chunks[0].content == "Checking."
    assert next(c for c in chunks if c.tool_call_delta).tool_call_delta["id"] == "call_owned"
    assert chunks[-1].finish_reason == "tool_calls"
    assert all(w.get("response", {}).get("request_id") != "rpc-call" for w in connection.writes)
    assert (
        "--safe-mode" in connection.arguments and "--no-session-persistence" in connection.arguments
    )
    assert connection.arguments[connection.arguments.index("--tools") + 1] == ""
    assert not connection.workspace.exists()


@pytest.mark.parametrize("name,arguments", [("Bash", {}), ("inspect", {"target": "wrong"})])
def test_unadvertised_or_uncorrelated_calls_never_escape(tmp_path, monkeypatch, name, arguments):
    events = [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "owned",
                        "name": "mcp__sylvia__inspect",
                        "input": {"target": "door"},
                    }
                ]
            },
        },
        native_call(name, arguments),
    ]
    adapter, connection = provider(tmp_path, monkeypatch, events)
    with pytest.raises(InvalidResponseError):
        asyncio.run(adapter.generate([{"role": "user", "content": "Check"}], TOOLS))
    assert connection.closed


@pytest.mark.parametrize(
    "event,error",
    [
        ({"api_error_status": 401}, AuthenticationError),
        ({"api_error_status": 429}, RateLimitError),
        ({"type": "rate_limit_event", "rate_limit_info": {"status": "rejected"}}, RateLimitError),
        ({"api_error_status": 404}, ModelNotFoundError),
        ({"errors": ["prompt is too long"]}, TokenLimitError),
        ({"errors": ["private data and sk-secret"]}, ProviderError),
    ],
)
def test_native_failures_are_safe_and_never_resubmitted(tmp_path, monkeypatch, event, error):
    adapter, connection = provider(
        tmp_path,
        monkeypatch,
        [
            {"type": "result", "subtype": "error_during_execution", "is_error": True, **event},
        ],
    )
    with pytest.raises(error) as caught:
        asyncio.run(adapter.generate([{"role": "user", "content": "Check"}]))
    assert "sk-secret" not in str(caught.value)
    assert len(connection.writes) == 2 and connection.closed


def test_text_and_reported_usage_have_no_estimated_bill(tmp_path, monkeypatch):
    adapter, connection = provider(
        tmp_path,
        monkeypatch,
        native_message({"type": "text", "text": "Open."})
        + [
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Open.",
                "total_cost_usd": 300,
                "usage": {
                    "input_tokens": 10,
                    "cache_read_input_tokens": 20,
                    "cache_creation_input_tokens": 3,
                    "output_tokens": 4,
                },
            },
        ],
    )
    response = asyncio.run(adapter.generate(tool_history()))
    assert response.content == "Open."
    assert response.usage.prompt_tokens == 33 and response.usage.total_tokens == 37
    assert response.usage_reported is True
    assert not hasattr(response, "total_cost_usd") and connection.closed


def test_signed_thinking_stays_private_and_survives_tool_continuation(tmp_path, monkeypatch):
    events = native_message(
        {"type": "thinking", "thinking": "private native reasoning", "signature": "signed-value"},
        {
            "type": "tool_use",
            "id": "signed-call",
            "name": "mcp__sylvia__inspect",
            "input": {"target": "door"},
        },
    )
    # Native MCP work can be admitted while the final message event is still in flight.
    events.insert(-1, native_call())
    adapter, _ = provider(tmp_path, monkeypatch, events)
    response = asyncio.run(adapter.generate([{"role": "user", "content": "Check"}], TOOLS))
    assert response.anthropic_content[0] == {
        "type": "thinking",
        "thinking": "private native reasoning",
        "signature": "signed-value",
    }
    assert response.anthropic_content[1]["name"] == "inspect"
    assert response.content is None
    history = [
        {"role": "assistant", "content": None, "anthropic_content": response.anthropic_content},
        {"role": "tool", "tool_call_id": "signed-call", "content": "Open"},
    ]
    _, records, _, _ = prepare_request(history, TOOLS, "opus", tmp_path)
    assert records[0]["message"]["content"][0]["signature"] == "signed-value"
    assert records[0]["message"]["content"][1]["name"] == "mcp__sylvia__inspect"


def test_runtime_eof_and_timeout_close_ephemeral_state(tmp_path, monkeypatch):
    for events, error in [
        ([], IncompleteProviderStreamError),
        ([ProviderTimeoutError("silent")], ProviderTimeoutError),
    ]:
        adapter, connection = provider(tmp_path, monkeypatch, events)
        with pytest.raises(error):
            asyncio.run(adapter.generate([{"role": "user", "content": "Check"}]))
        assert connection.closed and not connection.workspace.exists()


def test_environment_selects_sylvias_profile_and_no_tokens_or_harness_overrides(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "do-not-forward")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "do-not-forward")
    monkeypatch.setenv("ANTHROPIC_PROFILE", "do-not-forward")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "the-persons-own-profile")
    monkeypatch.setenv("ANTHROPIC_CONFIG_DIR", "the-persons-own-console-profile")
    monkeypatch.setenv("CLAUDE_CODE_SIMPLE", "1")
    environment = _claude_runtime.native_environment(tmp_path)
    assert environment["CLAUDE_CONFIG_DIR"] == str(tmp_path / "profile")
    assert environment["ANTHROPIC_CONFIG_DIR"] == str(tmp_path / "anthropic")
    assert environment["CLAUDE_CODE_TMPDIR"] == str(tmp_path / "temp")
    # Claude Code's own updater writes to the shared per-user installation.
    assert environment["DISABLE_UPDATES"] == environment["DISABLE_AUTOUPDATER"] == "1"
    assert (
        not {
            "ANTHROPIC_API_KEY",
            "CLAUDE_CODE_OAUTH_TOKEN",
            "ANTHROPIC_PROFILE",
            "CLAUDE_CODE_SIMPLE",
        }
        & environment.keys()
    )


def test_only_sylvias_installed_copy_is_found(tmp_path, monkeypatch):
    name = _claude_runtime.executable_name()
    elsewhere = tmp_path / "person" / ".local" / "bin"
    elsewhere.mkdir(parents=True)
    (elsewhere / name).write_bytes(b"the person's own Claude Code")
    monkeypatch.setenv("PATH", str(elsewhere))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "person"))
    monkeypatch.setenv("HOME", str(tmp_path / "person"))
    home = tmp_path / "claude-runtime"
    assert _claude_runtime.find_runtime(home) is None
    for folder in ["2.1.322", "2.1.1000", "2.1.999.partial", "notes"]:
        (home / "install" / folder).mkdir(parents=True)
        (home / "install" / folder / name).write_bytes(b"copy")
    assert _claude_runtime.find_runtime(home) == home / "install" / "2.1.1000" / name


def test_missing_copy_asks_for_setup_instead_of_using_another(tmp_path):
    adapter = ClaudeProvider(tmp_path / "runtime", model())
    with pytest.raises(ProviderConnectionError, match="Set up Claude Code for Sylvia"):
        asyncio.run(adapter.generate([{"role": "user", "content": "Check"}]))


@pytest.mark.parametrize("version", ["2.1.100 (Claude Code)", "not an official runtime"])
def test_unsupported_runtime_is_not_used(monkeypatch, version):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, version, "")
    )
    with pytest.raises(ProviderConnectionError):
        _claude_runtime.runtime_version(Path(sys.executable), Path("runtime"))


def test_status_discards_private_fields(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            a,
            0,
            json.dumps(
                {
                    "loggedIn": True,
                    "authMethod": "claude.ai",
                    "email": "private@example.invalid",
                    "token": "sk-private",
                    "subscriptionType": "private-plan",
                }
            ),
            "",
        ),
    )
    assert _claude_runtime.authentication_status(Path(sys.executable), Path("runtime")) == {
        "signed_in": True,
        "auth_method": "claude.ai",
        "billing": "subscription",
    }


def test_native_login_cancel_and_logout_stay_in_sylvias_profile(tmp_path, monkeypatch):
    commands = []
    environments = []

    class LoginChild:
        ended = False

        def poll(self):
            return 0 if self.ended else None

        def terminate(self):
            self.ended = True

        def wait(self, timeout):
            return 0

    child = LoginChild()

    def launch(command, **options):
        commands.append(command)
        environments.append(options["env"])
        assert options["stdin"] == options["stdout"] == options["stderr"] == subprocess.DEVNULL
        return child

    def run(command, **options):
        commands.append(command)
        environments.append(options["env"])
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(claude_settings, "find_runtime", lambda _: Path(sys.executable))
    monkeypatch.setattr(claude_settings, "runtime_version", lambda *_: "2.1.322")
    monkeypatch.setattr(
        claude_settings,
        "authentication_status",
        lambda *_: {
            "signed_in": False,
            "auth_method": "none",
            "billing": "unknown",
        },
    )
    monkeypatch.setattr(subprocess, "Popen", launch)
    monkeypatch.setattr(subprocess, "run", run)
    pending = claude_settings.claude_settings_action(tmp_path, "login", {"account_type": "api"})
    assert pending["login_pending"] is True
    assert commands[0][-3:] == ["auth", "login", "--console"]
    assert "--safe-mode" in commands[0]
    cancelled = claude_settings.claude_settings_action(tmp_path, "cancel")
    assert cancelled["login_pending"] is False and child.ended
    assert len(commands) == 1 and not (tmp_path / "config.yaml").exists()
    claude_settings.claude_settings_action(tmp_path, "logout")
    assert commands[1][-2:] == ["auth", "logout"]
    profile = str(tmp_path.resolve() / "claude-runtime" / "profile")
    assert [e["CLAUDE_CONFIG_DIR"] for e in environments] == [profile, profile]
    with pytest.raises(ProviderConnectionError, match="options are invalid"):
        claude_settings.claude_settings_action(tmp_path, "status", {"executable": sys.executable})


def test_setup_is_required_before_sign_in_or_models(tmp_path):
    status = claude_settings.claude_settings_action(tmp_path, "status")
    assert status["installed"] is False and status["install"]["state"] == "idle"
    assert "own copy of Claude Code" in status["message"]
    for action in ["login", "logout", "add_model"]:
        with pytest.raises(ProviderConnectionError, match="own copy of Claude Code"):
            claude_settings.claude_settings_action(tmp_path, action)


def test_cancelling_a_waiting_native_stream_collects_the_child(tmp_path, monkeypatch):
    script = tmp_path / "partial_runtime.py"
    frames = native_message({"type": "text", "text": "Partial"})[:2]
    script.write_text(
        "import json,sys,time\n"
        + "\n".join(f"print({json.dumps(frame)!r}, flush=True)" for frame in frames)
        + "\ntime.sleep(60)\n",
        encoding="utf-8",
    )
    connections = []

    def factory(executable, workspace, arguments, environment):
        connection = ClaudeConnection(Path(sys.executable), workspace, [str(script)], environment)
        connections.append(connection)
        return connection

    monkeypatch.setattr(_claude, "find_runtime", lambda _: Path(sys.executable))
    monkeypatch.setattr(_claude, "runtime_version", lambda *_: "2.1.322")
    adapter = ClaudeProvider(tmp_path / "runtime", model(), connection_factory=factory)

    async def cancel():
        stream = adapter.generate_stream([{"role": "user", "content": "Check"}])
        assert (await anext(stream)).content == "Partial"
        waiting = asyncio.create_task(anext(stream))
        await asyncio.sleep(0.02)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        await stream.aclose()

    asyncio.run(cancel())
    assert connections[0]._process.poll() is not None
    assert not list((tmp_path / "runtime").iterdir())


def test_setup_saves_separate_runtime_and_resolves_after_reopen(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_settings, "find_runtime", lambda _: Path(sys.executable))
    monkeypatch.setattr(claude_settings, "runtime_version", lambda *_: "2.1.322")
    monkeypatch.setattr(
        claude_settings,
        "authentication_status",
        lambda *_: {
            "signed_in": True,
            "auth_method": "claude.ai",
            "billing": "subscription",
        },
    )
    status = claude_settings.claude_settings_action(
        tmp_path,
        "add_model",
        {
            "model": {
                "id": "opus",
                "context_window": None,
                "max_output_tokens": None,
            }
        },
    )
    assert status["saved_model"] == "claude-runtime/opus"
    settings = provider_settings(tmp_path / "config.yaml")
    assert settings["configured"][0]["preset_id"] == "claude_runtime"
    assert settings["configured"][0]["credential_configured"] is False
    assert "executable" not in (tmp_path / "config.yaml").read_text(encoding="utf-8")
    resolver = ConfiguredProviderResolver(tmp_path / "config.yaml")
    choice = resolver.model_choices()[0]
    assert choice["output_budget_mode"] == "runtime_managed"
    assert choice["context_window"] is None
    assert output_allowance(model()) is None
    for role in [WriterRole.DM, WriterRole.BUILDER]:
        record = SessionRecord(
            "session",
            "campaign" if role is WriterRole.DM else None,
            role,
            "claude_runtime",
            status["saved_model"],
        )
        assert resolver(record).model_info.transport == "claude_code"


def test_native_route_cannot_accept_api_keys_or_fallback():
    for kwargs in [
        {"credentials_list": [{"value": "private"}]},
        {"fallback_providers": [ProviderConfig("other", "responses", "https://example.invalid")]},
    ]:
        with pytest.raises(ValueError):
            ProviderFactory.create(
                ProviderConfig(
                    "claude_runtime",
                    "claude_code",
                    "https://claude.ai",
                    runtime_home="runtime",
                    models=[model()],
                    **kwargs,
                )
            )


def test_no_http_discovery_or_probe_for_claude(tmp_path):
    with pytest.raises(ConfigError, match="HTTP discovery"):
        discover_provider_models(
            tmp_path / "config.yaml", provider_id="claude_runtime", base_url=None, credential=None
        )
    with pytest.raises(ConfigError, match="HTTP probe"):
        validate_provider_model(
            tmp_path / "config.yaml",
            provider_id="claude_runtime",
            base_url=None,
            credential=None,
            model_id="opus",
            transport="claude_code",
        )


def test_owned_process_cancellation_does_not_touch_unrelated_process(tmp_path):
    script = tmp_path / "runtime.py"
    script.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    other = subprocess.Popen([sys.executable, str(script)], **_claude_runtime.process_options())
    connection = ClaudeConnection(Path(sys.executable), tmp_path, [str(script)], dict(os.environ))
    try:
        connection.start()
        process = connection._process
        connection.close()
        connection.close()
        assert process.poll() is not None
        assert other.poll() is None
    finally:
        other.terminate()
        other.wait(timeout=5)


def test_malformed_native_process_stream_is_rejected(tmp_path):
    script = tmp_path / "runtime.py"
    script.write_text("print('not JSON', flush=True)\n", encoding="utf-8")
    connection = ClaudeConnection(Path(sys.executable), tmp_path, [str(script)], dict(os.environ))
    try:
        connection.start()
        with pytest.raises(InvalidResponseError):
            connection.next_event(3)
    finally:
        connection.close()


def test_claude_owner_command_preserves_safe_rejection(tmp_path):
    from tests.test_alpha_f6d_owner_process import _launch, _read, _shutdown, _write

    process, input_stream, output_stream = _launch(tmp_path / "owner.sqlite3")
    try:
        _write(
            input_stream,
            {
                "id": "claude-rejection",
                "method": "claude_connection",
                "params": {"action": "unavailable-action"},
            },
        )
        reply = _read(output_stream)
        assert reply["ok"] is False
        assert reply["error"]["message"] == "This Claude connection action is unavailable."
    finally:
        _shutdown(process, input_stream, output_stream)


def test_game_tool_commit_survives_interruption_and_reopen(tmp_path, monkeypatch):
    from sylvia_gm.agent._provider_session import ProviderSessionAdapter
    from sylvia_gm.agent._session_loop import CampaignSessionLoop, SessionRunRequest
    from sylvia_gm.application.authority import AuthorityOwner
    from tests.test_alpha_f4_generic_sessions import _clockwork_campaign

    connections = []

    class ToolConnection(Connection):
        def __init__(self, *args):
            super().__init__(
                [
                    {
                        "type": "control_request",
                        "request_id": "list",
                        "request": {
                            "subtype": "mcp_message",
                            "server_name": "sylvia",
                            "message": {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                        },
                    }
                ]
            )
            self.workspace = args[1]
            history = self.workspace / "history.jsonl"
            self.records = [json.loads(line) for line in history.read_text().splitlines()]
            connections.append(self)

        def write(self, event):
            super().write(event)
            result = (
                event.get("response", {})
                .get("response", {})
                .get("mcp_response", {})
                .get("result", {})
            )
            if "tools" not in result:
                return
            if len(connections) == 1:
                alias = next(t["name"] for t in result["tools"] if t["name"].endswith("_advance"))
                self.events = iter(
                    [
                        {
                            "type": "assistant",
                            "message": {
                                "content": [
                                    {
                                        "type": "tool_use",
                                        "id": "one-advance",
                                        "name": "mcp__sylvia__" + alias,
                                        "input": {"steps": 1},
                                    }
                                ]
                            },
                        },
                        native_call(alias, {"steps": 1}),
                    ]
                )
            else:
                self.events = iter([ProviderTimeoutError("Synthetic narration interruption")])

    monkeypatch.setattr(_claude, "find_runtime", lambda _: Path(sys.executable))
    monkeypatch.setattr(_claude, "runtime_version", lambda *_: "2.1.322")
    database = tmp_path / "campaign.sqlite3"
    calls = []
    with AuthorityOwner.open(database, "owner-claude", disconnect_grace_seconds=1.0) as owner:
        _clockwork_campaign(
            owner,
            campaign_id="claude-campaign",
            writer=WriterRole.DM,
            turn_id="claude-turn",
            calls=calls,
        )
        owner.create_agent_session(
            campaign_id="claude-campaign",
            role=WriterRole.DM,
            provider_id="claude_runtime",
            model_id="claude-runtime/opus",
            session_id="claude-session",
        )
        adapter = ClaudeProvider(tmp_path / "runtime", model(), connection_factory=ToolConnection)
        loop = ProviderSessionAdapter(owner, adapter, owner_id="owner-claude")
        with pytest.raises(ProviderTimeoutError):
            CampaignSessionLoop(owner).run(
                SessionRunRequest(
                    session_id="claude-session",
                    turn_id="claude-turn",
                    attempt_id="claude-attempt",
                    message_id="claude-input",
                    user_text="Advance the clock once.",
                ),
                loop.for_turn("claude-turn"),
            )
        assert calls == [1]
        assert len(connections) == 2 and all(c.closed for c in connections)
        results = [
            record["message"]["content"]
            for record in connections[1].records
            if record["type"] == "user" and isinstance(record["message"]["content"], list)
        ]
        assert any(block.get("type") == "tool_result" for blocks in results for block in blocks)
        saved = owner.session_context("claude-session").messages
        assert "SYLVIA_GM_RUNTIME_CONTINUE" not in str(saved)
        original_events = owner.snapshot("claude-campaign").events
    with AuthorityOwner.open(database, "owner-claude-reopened") as owner:
        assert owner.snapshot("claude-campaign").events == original_events
        assert owner.session_context("claude-session").messages == saved
        assert calls == [1]
