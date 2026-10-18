"""Developer-only model steering experiments use the production GM session path."""

from __future__ import annotations

import asyncio
import json
import os
import threading
from collections.abc import AsyncIterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from scripts.run_model_steering_lab import (
    _ProtocolOwnerClient,
    _provider_preflight,
    _SubprocessOwnerClient,
    load_experiment,
    main,
    run_experiment,
)

import sylvia_gm.interfaces.owner_process as owner_process
from sylvia_gm import __version__
from sylvia_gm.interfaces.local_owner import LocalOwnerSupervisor
from sylvia_gm.provider._base import (
    ModelInfo,
    Provider,
    ProviderResponse,
    ProviderStreamChunk,
)


class _SteeringProvider(Provider):
    """Return one valid Campaign File disposition for each opening or player action."""

    def __init__(self) -> None:
        super().__init__()
        self.model_info = ModelInfo(
            id="test-provider/minimax-m3",
            name="MiniMax M3 test double",
            provider_name="Test Provider",
            provider_key="test_provider",
            provider_mode="byok",
            context_window=524_288,
            max_output_tokens=4_096,
        )

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        timeout: float = 60.0,
        **kwargs: object,
    ) -> ProviderResponse:
        raise NotImplementedError

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        del timeout, kwargs
        rendered = json.dumps(messages, ensure_ascii=False)
        opening = "I look around" not in rendered
        narration = (
            "You stand at the edge of the Anchor Clearing."
            if opening
            else "You take in the clearing and the dark tree line beyond it."
        )
        disposition: dict[str, object] = {
            "state_change": "changed" if opening else "unchanged",
            "state_change_summary": (
                "Established the party at the Anchor Clearing."
                if opening
                else "The response establishes no new lasting campaign truth."
            ),
            "revised_player_facing_response": [{"kind": "narration", "text": narration}],
        }
        if opening:
            disposition["scenes"] = [{"summary": "The party is at the Anchor Clearing."}]
        if not tools:
            yield ProviderStreamChunk(content=narration, finish_reason="stop")
            return
        update_tool = next(
            tool
            for tool in tools or []
            if str(tool.get("function", {}).get("name", "")).endswith("_update_campaign_file")
        )
        tool_name = str(update_tool["function"]["name"])
        yield ProviderStreamChunk(
            tool_call_delta={
                "index": 0,
                "id": "steering-test-campaign-file",
                "function": {
                    "name": tool_name,
                    "arguments": json.dumps(
                        disposition,
                        separators=(",", ":"),
                    ),
                },
            },
            finish_reason="tool_calls",
        )

    async def close(self) -> None:
        return None


class _SteeringResolver:
    def __init__(self) -> None:
        self.provider = _SteeringProvider()

    def __call__(self, _session: object) -> Provider:
        return self.provider

    def model_choices(self) -> tuple[dict[str, object], ...]:
        return (
            {
                "provider_id": "test_provider",
                "provider_name": "Test Provider",
                "model_id": "test-provider/minimax-m3",
                "model_name": "MiniMax M3 test double",
                "context_window": 524_288,
                "max_output_tokens": 4_096,
            },
        )


class _SlowSteeringProvider(_SteeringProvider):
    """Delay the first token long enough to exercise renderer attachment renewal."""

    def __init__(self) -> None:
        super().__init__()
        self.request_count = 0

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        self.request_count += 1
        if self.request_count == 1:
            yield ProviderStreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": "steering-test-reference",
                    "function": {
                        "name": next(
                            str(tool["function"]["name"])
                            for tool in tools or []
                            if str(tool.get("function", {}).get("name", "")).endswith(
                                "_read_campaign_reference"
                            )
                        ),
                        "arguments": json.dumps(
                            {
                                "source": "campaign_setting",
                                "path": "locations/verdant-margin.md",
                            },
                            separators=(",", ":"),
                        ),
                    },
                },
                finish_reason="tool_calls",
            )
            return
        if self.request_count == 2:
            await asyncio.sleep(1.2)
        async for chunk in super().generate_stream(
            messages,
            tools,
            timeout=timeout,
            **kwargs,
        ):
            yield chunk


class _SlowSteeringResolver(_SteeringResolver):
    def __init__(self) -> None:
        self.provider = _SlowSteeringProvider()


class _OwnerProcessProviderHandler(BaseHTTPRequestHandler):
    """Serve deterministic OpenAI-compatible tool calls to the child owner."""

    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 - inherited HTTP handler name
        body = json.dumps({"data": [{"id": "minimax-m3"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 - inherited HTTP handler name
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        tools = payload.get("tools", [])
        server = self.server
        protocol_headers = list(getattr(server, "opencode_protocol_headers", []))
        protocol_headers.append(
            {
                "x-opencode-session": self.headers.get("x-opencode-session"),
                "x-opencode-client": self.headers.get("x-opencode-client"),
                "user-agent": self.headers.get("user-agent"),
            }
        )
        server.opencode_protocol_headers = protocol_headers  # type: ignore[attr-defined]
        request_count = int(getattr(server, "provider_request_count", 0)) + 1
        server.provider_request_count = request_count  # type: ignore[attr-defined]
        rendered_messages = json.dumps(payload.get("messages", []), ensure_ascii=False)
        opening = "I look around" not in rendered_messages
        narration = (
            "You stand at the edge of the Anchor Clearing."
            if opening
            else "You take in the clearing and the dark tree line beyond it."
        )
        draft_first_action = bool(getattr(server, "draft_first_action", False))
        draft_emitted = bool(getattr(server, "draft_emitted", False))
        read_location_first = bool(getattr(server, "read_location_first", False))
        if read_location_first and request_count == 1:
            reference_tool = next(
                tool
                for tool in tools
                if str(tool.get("function", {}).get("name", "")).endswith(
                    "_read_campaign_reference"
                )
            )
            delta = {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "owner-process-location-reference",
                        "function": {
                            "name": reference_tool["function"]["name"],
                            "arguments": json.dumps(
                                {
                                    "source": "campaign_setting",
                                    "path": "locations/verdant-margin.md",
                                },
                                separators=(",", ":"),
                            ),
                        },
                    }
                ]
            }
            finish_reason = "tool_calls"
        elif not opening and draft_first_action and not draft_emitted and tools:
            server.draft_emitted = True  # type: ignore[attr-defined]
            delta = {"content": "PRIVATE DRAFT THAT MUST NOT BE RELEASED"}
            finish_reason = "stop"
        elif tools:
            update_tool = next(
                tool
                for tool in tools
                if str(tool.get("function", {}).get("name", "")).endswith("_update_campaign_file")
            )
            parameters = update_tool["function"]["parameters"]
            properties = parameters.get("properties", {})
            disposition: dict[str, object] = {
                "state_change": "changed" if opening else "unchanged",
                "state_change_summary": (
                    "Established the party at the Anchor Clearing."
                    if opening
                    else "The response establishes no new lasting campaign truth."
                ),
            }
            if "revised_player_facing_response" in properties:
                disposition["revised_player_facing_response"] = [
                    {"kind": "narration", "text": narration}
                ]
            if opening:
                disposition["scenes"] = [{"summary": "The party is at the Anchor Clearing."}]
            delta = {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": f"owner-process-call-{request_count}",
                        "function": {
                            "name": update_tool["function"]["name"],
                            "arguments": json.dumps(disposition, separators=(",", ":")),
                        },
                    }
                ]
            }
            finish_reason = "tool_calls"
        else:
            delta = {"content": narration}
            finish_reason = "stop"
        call = {"choices": [{"delta": delta, "finish_reason": None}]}
        finished = {"choices": [{"delta": {}, "finish_reason": finish_reason}]}
        body = (
            f"data: {json.dumps(call, separators=(',', ':'))}\n\n"
            f"data: {json.dumps(finished, separators=(',', ':'))}\n\n"
            "data: [DONE]\n\n"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *args: object) -> None:
        del args


def _owner_process_manifest(
    tmp_path: Path,
    base_url: str,
    credential: str,
    *,
    finalization_mode: str = "production",
    reference_scope_mode: str = "production",
    restart_before_actions: list[int] | None = None,
    customization_notes: str = "",
    output_limit: int = 4_096,
) -> Path:
    config = tmp_path / "owner-process-config.yaml"
    config.write_text(
        json.dumps(
            {
                "providers": {
                    "test_provider": {
                        "enabled": True,
                        "preset_id": "opencode_go",
                        "mode": "byok",
                        "name": "Owner process test provider",
                        "base_url": base_url,
                        "api_key": credential,
                        "models": [
                            {
                                "id": "minimax-m3",
                                "name": "MiniMax M3 process test double",
                                "context_window": 524_288,
                                "max_output_tokens": output_limit,
                                "supports_streaming": True,
                                "supports_tools": True,
                            }
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    candidate_prompt = tmp_path / "headless-candidate-prompt.txt"
    candidate_prompt.write_text("HEADLESS CUSTOM STEERING CONTRACT", encoding="utf-8")
    manifest = tmp_path / "owner-process-experiment.json"
    manifest.write_text(
        json.dumps(
            {
                "format": "sylvia.model-steering-experiment.v1",
                "name": "Headless owner process test",
                "provider_id": "test_provider",
                "model_id": "test-provider/minimax-m3",
                "config_path": str(config),
                "output_root": str(tmp_path / "owner-process-output"),
                "samples_per_variant": 1,
                "scenario": {
                    "kind": "rift",
                    "player_character_id": "open-adventurer",
                    "companion_ids": [],
                    "actions": ["I look around"],
                    "restart_before_actions": restart_before_actions or [],
                    "customization_notes": customization_notes,
                },
                "variants": [
                    {
                        "name": "headless candidate",
                        "gm_system_prompt_path": str(candidate_prompt),
                        "finalization_mode": finalization_mode,
                        "reference_scope_mode": reference_scope_mode,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return manifest


def _manifest(tmp_path: Path) -> Path:
    config = tmp_path / "config.yaml"
    config.write_text("providers: {}\n", encoding="utf-8")
    candidate_prompt = tmp_path / "candidate-prompt.txt"
    candidate_prompt.write_text("CUSTOM STEERING CONTRACT", encoding="utf-8")
    manifest = tmp_path / "experiment.json"
    manifest.write_text(
        json.dumps(
            {
                "format": "sylvia.model-steering-experiment.v1",
                "name": "Rift steering test",
                "provider_id": "test_provider",
                "model_id": "test-provider/minimax-m3",
                "config_path": str(config),
                "output_root": str(tmp_path / "output"),
                "samples_per_variant": 1,
                "scenario": {
                    "kind": "rift",
                    "player_character_id": "open-adventurer",
                    "companion_ids": [],
                    "actions": ["I look around"],
                },
                "variants": [
                    {"name": "production"},
                    {
                        "name": "candidate",
                        "gm_system_prompt_path": str(candidate_prompt),
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return manifest


@pytest.mark.parametrize("output_limit", [8_192, 131_072])
def test_preflight_reports_the_configured_output_allowance(
    tmp_path: Path, output_limit: int
) -> None:
    manifest = _owner_process_manifest(
        tmp_path, "http://127.0.0.1:1/v1", "synthetic-test-token", output_limit=output_limit
    )

    preflight = _provider_preflight(load_experiment(manifest))

    assert preflight["safe_model_choice"]["max_output_tokens"] == output_limit
    assert preflight["opening_request_options"] == {
        "max_tokens": output_limit,
        "tool_choice": "required",
    }


def test_validate_only_makes_no_provider_calls(tmp_path: Path, capsys: Any) -> None:
    manifest = _manifest(tmp_path)

    assert main([str(manifest), "--validate-only"]) == 0
    assert "2 variants × 1 sample(s)" in capsys.readouterr().out
    assert not (tmp_path / "output").exists()


def test_comparison_runs_every_variant_through_the_real_owner_loop(tmp_path: Path) -> None:
    experiment = load_experiment(_manifest(tmp_path))

    result = run_experiment(experiment, resolver_factory=_SteeringResolver)

    summary = json.loads((result / "summary.json").read_text(encoding="utf-8"))
    assert [sample["variant"] for sample in summary["samples"]] == [
        "production",
        "candidate",
    ]
    assert [sample["status"] for sample in summary["samples"]] == [
        "completed",
        "completed",
    ]
    for sample in summary["samples"]:
        assert sample["responses"] == [
            {
                "stage": "opening",
                "input": None,
                "outputs": ["You stand at the edge of the Anchor Clearing."],
            },
            {
                "stage": "action_1",
                "input": "I look around",
                "outputs": ["You take in the clearing and the dark tree line beyond it."],
            },
        ]
        sample_dir = result / sample["artifact_directory"]
        audit = json.loads((sample_dir / "full-audit.json").read_text(encoding="utf-8"))
        assert audit["enabled"] is True
        assert sample["mechanics"]["provider_request_count"] == 2
        assert sample["mechanics"]["tool_call_round_count"] == 2
        assert (sample_dir / "authority.sqlite3").is_file()
        assert (sample_dir / "campaign-file.diff").is_file()

    production_audit = json.loads(
        (result / "production-sample-01" / "full-audit.json").read_text(encoding="utf-8")
    )
    candidate_audit = json.loads(
        (result / "candidate-sample-01" / "full-audit.json").read_text(encoding="utf-8")
    )
    production_request = next(
        trace for trace in production_audit["traces"] if trace["kind"] == "provider_request"
    )
    candidate_request = next(
        trace for trace in candidate_audit["traces"] if trace["kind"] == "provider_request"
    )
    assert "You are Sylvia, the Game Master" in production_request["messages"][0]["content"]
    assert candidate_request["messages"][0]["content"] == "CUSTOM STEERING CONTRACT"

    report = (result / "report.html").read_text(encoding="utf-8")
    assert "production · sample 1" in report
    assert "candidate · sample 1" in report
    assert "You take in the clearing" in report
    assert "Every provider exchange" in report
    assert "Exact Full Audit JSON" in report
    assert "Prompt difference from production" in report
    assert "Write review notes" in report
    assert (result / "production-gm-system-prompt.txt").is_file()
    assert (
        (result / "prompt-diffs" / "production.diff")
        .read_text(encoding="utf-8")
        .startswith("This variant uses the production")
    )
    assert "CUSTOM STEERING CONTRACT" in (result / "prompt-diffs" / "candidate.diff").read_text(
        encoding="utf-8"
    )
    review = (result / "review.md").read_text(encoding="utf-8")
    assert "production · sample 1" in review
    assert "candidate · sample 1" in review


def test_in_process_lab_keeps_narration_attached_during_slow_provider_turn(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """A slow but healthy model turn must not lose its headless renderer attachment."""

    manifest_path = _manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["variants"] = [{"name": "production"}]
    manifest["scenario"]["actions"] = ["I look around"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    supervisors: list[LocalOwnerSupervisor] = []

    def short_attachment_supervisor() -> LocalOwnerSupervisor:
        supervisor = LocalOwnerSupervisor(
            disconnect_grace_seconds=2.0,
            attachment_lease_seconds=0.5,
            initial_attachment_lease_seconds=0.5,
        )
        supervisors.append(supervisor)
        return supervisor

    monkeypatch.setattr(
        owner_process,
        "LocalOwnerSupervisor",
        short_attachment_supervisor,
    )
    monkeypatch.setattr(_ProtocolOwnerClient, "_NARRATION_HEARTBEAT_SECONDS", 0.1)

    result = run_experiment(
        load_experiment(manifest_path),
        resolver_factory=_SlowSteeringResolver,
    )

    sample = json.loads((result / "summary.json").read_text(encoding="utf-8"))["samples"][0]
    assert len(supervisors) == 1
    assert sample["status"] == "completed", sample.get("error")
    assert sample["responses"][0]["outputs"] == ["You stand at the edge of the Anchor Clearing."]


def test_subprocess_client_uses_real_owner_protocol_and_releases_database(tmp_path: Path) -> None:
    database = tmp_path / "authority.sqlite3"
    client = _SubprocessOwnerClient(database, None)
    process_id = client.process_id

    assert isinstance(process_id, int)
    assert process_id != os.getpid()
    assert client.request("index", "campaign_index") == []
    client.close()

    reopened = _SubprocessOwnerClient(database, None)
    try:
        assert reopened.request("reopened-index", "campaign_index") == []
    finally:
        reopened.close()


def test_subprocess_owner_uses_the_runners_checkout_over_an_inherited_pythonpath(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    conflicting_source = tmp_path / "conflicting-source"
    conflicting_package = conflicting_source / "sylvia_gm"
    conflicting_package.mkdir(parents=True)
    (conflicting_package / "__init__.py").write_text(
        "raise RuntimeError('wrong Sylvia source')\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", str(conflicting_source))

    client = _SubprocessOwnerClient(tmp_path / "authority.sqlite3", None)
    try:
        assert client.request("index", "campaign_index") == []
    finally:
        client.close()


def test_headless_mode_keeps_owner_data_ephemeral_and_reports_complete_journey(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    credential = "PRIVATE-OWNER-PROCESS-CREDENTIAL"
    normal_data_root = tmp_path / "normal-app-data"
    normal_database = normal_data_root / "com.pneuma-io.sylvia-gm" / "sylvia-gm.db"
    normal_database.parent.mkdir(parents=True)
    normal_database.write_bytes(b"NORMAL APP DATABASE MUST REMAIN UNTOUCHED")
    monkeypatch.setenv("LOCALAPPDATA", str(normal_data_root))
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OwnerProcessProviderHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    host, port = server.server_address
    manifest = _owner_process_manifest(tmp_path, f"http://{host}:{port}/v1", credential)
    config = tmp_path / "owner-process-config.yaml"
    config_before = config.read_bytes()
    try:
        result = run_experiment(load_experiment(manifest), execution_mode="owner_process")
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=10)

    assert config.read_bytes() == config_before
    assert normal_database.read_bytes() == b"NORMAL APP DATABASE MUST REMAIN UNTOUCHED"
    summary = json.loads((result / "summary.json").read_text(encoding="utf-8"))
    assert summary["execution_mode"] == "owner_process"
    sample = summary["samples"][0]
    assert sample["status"] == "completed", sample.get("error")
    assert sample["responses"] == [
        {
            "stage": "opening",
            "input": None,
            "outputs": ["You stand at the edge of the Anchor Clearing."],
        },
        {
            "stage": "action_1",
            "input": "I look around",
            "outputs": ["You take in the clearing and the dark tree line beyond it."],
        },
    ]
    runtime = sample["mechanics"]["owner_runtime"]
    assert runtime["execution_mode"] == "owner_process"
    assert isinstance(runtime["owner_process_id"], int)
    assert runtime["owner_process_id"] != os.getpid()
    assert runtime["owner_storage_retained"] is False
    assert runtime["source_provider_config_unchanged"] is True
    sample_dir = result / sample["artifact_directory"]
    assert not (sample_dir / "authority.sqlite3").exists()
    assert not (sample_dir / "config.yaml").exists()
    audit = json.loads((sample_dir / "full-audit.json").read_text(encoding="utf-8"))
    assert audit["enabled"] is True
    provider_request = next(
        trace for trace in audit["traces"] if trace["kind"] == "provider_request"
    )
    assert provider_request["messages"][0]["content"] == "HEADLESS CUSTOM STEERING CONTRACT"
    protocol_headers = server.opencode_protocol_headers  # type: ignore[attr-defined]
    assert len(protocol_headers) >= 2
    assert len({headers["x-opencode-session"] for headers in protocol_headers}) == 1
    assert all(headers["x-opencode-client"] == "sylvia-gm" for headers in protocol_headers)
    assert all(headers["user-agent"] == f"Sylvia-GM/{__version__}" for headers in protocol_headers)
    wire_requests = [trace for trace in audit["traces"] if trace["kind"] == "provider_wire_request"]
    assert wire_requests
    assert all(
        wire["request"]["extra_headers"]["x-opencode-session"]
        == protocol_headers[0]["x-opencode-session"]
        for wire in wire_requests
    )
    assert "extra_headers" not in provider_request["options"]
    raw_session_header = protocol_headers[0]["x-opencode-session"].encode()
    for artifact in sample_dir.rglob("*"):
        if artifact.is_file() and artifact.name != "full-audit.json":
            assert raw_session_header not in artifact.read_bytes()
    assert "owner_process" in (result / "report.md").read_text(encoding="utf-8")
    for artifact in result.rglob("*"):
        if artifact.is_file():
            assert credential.encode() not in artifact.read_bytes()


def test_headless_required_tool_round_discards_a_premature_draft(
    tmp_path: Path,
) -> None:
    credential = "PRIVATE-DRAFT-FIRST-CREDENTIAL"
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OwnerProcessProviderHandler)
    server.draft_first_action = True  # type: ignore[attr-defined]
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    host, port = server.server_address
    manifest = _owner_process_manifest(
        tmp_path,
        f"http://{host}:{port}/v1",
        credential,
        restart_before_actions=[1],
        customization_notes="Begin beside a rain-swollen crossing.",
    )
    try:
        result = run_experiment(load_experiment(manifest), execution_mode="owner_process")
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=10)

    summary = json.loads((result / "summary.json").read_text(encoding="utf-8"))
    sample = summary["samples"][0]
    assert sample["status"] == "completed", sample.get("error")
    assert sample["responses"][1]["outputs"] == [
        "You take in the clearing and the dark tree line beyond it."
    ]
    assert sample["mechanics"]["provider_request_count"] == 3
    assert sample["restarts"][0]["before_action"] == 1
    assert (
        sample["restarts"][0]["previous_owner_process_id"]
        != sample["restarts"][0]["reopened_owner_process_id"]
    )
    sample_dir = result / sample["artifact_directory"]
    assert (sample_dir / "full-audit-before-action-1.json").is_file()
    audit_text = (sample_dir / "full-audit.json").read_text(encoding="utf-8")
    assert "PRIVATE DRAFT THAT MUST NOT BE RELEASED" in audit_text
    projection_text = (sample_dir / "player-projection.json").read_text(encoding="utf-8")
    assert "PRIVATE DRAFT THAT MUST NOT BE RELEASED" not in projection_text


def test_headless_reference_prototype_returns_location_overview_without_hooks(
    tmp_path: Path,
) -> None:
    credential = "PRIVATE-REFERENCE-SCOPE-CREDENTIAL"
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OwnerProcessProviderHandler)
    server.read_location_first = True  # type: ignore[attr-defined]
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    host, port = server.server_address
    manifest = _owner_process_manifest(
        tmp_path,
        f"http://{host}:{port}/v1",
        credential,
        reference_scope_mode="location_overview_only",
    )
    try:
        result = run_experiment(load_experiment(manifest), execution_mode="owner_process")
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=10)

    summary = json.loads((result / "summary.json").read_text(encoding="utf-8"))
    sample = summary["samples"][0]
    assert sample["status"] == "completed"
    assert sample["reference_scope_mode"] == "location_overview_only"
    sample_dir = result / sample["artifact_directory"]
    audit = json.loads((sample_dir / "full-audit.json").read_text(encoding="utf-8"))
    reference = next(
        trace
        for trace in audit["traces"]
        if trace["kind"] == "mcp_read_campaign_reference"
        and trace.get("source") == "campaign_setting"
        and trace.get("path") == "locations/verdant-margin.md"
    )
    content = reference["result"]["content"]
    assert content.startswith("## Overview")
    assert "## Hooks" not in content
    assert "supply cache" not in content.casefold()
    for artifact in result.rglob("*"):
        if artifact.is_file():
            assert credential.encode() not in artifact.read_bytes()
