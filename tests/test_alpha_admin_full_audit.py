"""Admin Full Audit exposes exact current-process model I/O during testing."""

from __future__ import annotations

import asyncio
import errno
import json
from collections.abc import Mapping
from pathlib import Path

import pytest
from scripts.generate_model_input_audit_examples import generate_examples

from sylvia_gm.agent._provider_session import _dm_system_prompt
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.schema_admission import validate_parameters
from sylvia_gm.domain.authority import thaw_json_value
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.interfaces.owner_process import _dispatch
from sylvia_gm.provider._admin_audit import AdminAuditedProvider
from sylvia_gm.provider._base import (
    CacheUsage,
    FakeProvider,
    ModelInfo,
    ProviderResponse,
    ProviderStreamChunk,
    TokenUsage,
)
from sylvia_gm.provider._context_fit import ContextFittingProvider
from sylvia_gm.provider._exceptions import TokenLimitError
from sylvia_gm.provider._hooks import HookContext


@pytest.fixture(scope="module")
def public_audit_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Generate current synthetic examples without relying on ignored local captures."""
    output = tmp_path_factory.mktemp("public-model-input-audit")
    generate_examples(output)
    return output


class _UsageStreamingProvider(FakeProvider):
    """Emit a provider completion hook after the configured stream."""

    async def generate_stream(self, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        async for chunk in super().generate_stream(*args, **kwargs):
            yield chunk
        await self.hooks.fire_on_response(
            HookContext(
                provider_key="local-test",
                model_id="model-test",
                response=ProviderResponse(
                    usage=TokenUsage(101, 12, 113, reasoning_tokens=3),
                    cache_usage=CacheUsage(cache_read_tokens=40, total_prompt_tokens=101),
                ),
                retry_attempt=1,
                stream=True,
            )
        )


class _WireStreamingProvider(FakeProvider):
    """Expose the exact transport-shaped request through the normal provider hook."""

    async def generate_stream(self, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        await self.hooks.fire_on_request(
            HookContext(
                provider_key="local-test",
                model_id="model-test",
                request_json={
                    "model": "wire-model-test",
                    "messages": [
                        {"role": "user", "content": "application input"},
                        {"role": "assistant", "content": "<think>\n\n</think>\n\n"},
                    ],
                    "stream": True,
                },
                stream=True,
            )
        )
        async for chunk in super().generate_stream(*args, **kwargs):
            yield chunk


def test_admin_audited_provider_captures_complete_request_and_stream() -> None:
    traces: list[object] = []
    base_provider = FakeProvider(
        stream_chunks=[
            ProviderStreamChunk(reasoning_content="private reasoning"),
            ProviderStreamChunk(content="player-safe answer"),
            ProviderStreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": "call-1",
                    "function": {"name": "do_thing", "arguments": '{"value":2}'},
                }
            ),
            ProviderStreamChunk(
                anthropic_content=[
                    {
                        "type": "thinking",
                        "thinking": "private reasoning",
                        "signature": "opaque-signature",
                    },
                    {
                        "type": "tool_use",
                        "id": "call-1",
                        "name": "do_thing",
                        "input": {"value": 2},
                    },
                ]
            ),
        ]
    )
    base_provider._base_url = (  # type: ignore[attr-defined]
        "http://user:secret@127.0.0.1:1234/v1?key=hidden"
    )
    provider = AdminAuditedProvider(
        base_provider,
        sink=traces.append,
        session_id="session-audit",
        role="builder",
        provider_id="local-test",
        model_id="model-test",
        selection_mode="once",
    )
    messages = [
        {"role": "system", "content": "complete system input"},
        {"role": "user", "content": "complete user input"},
    ]
    tools = [{"type": "function", "function": {"name": "do_thing"}}]
    provider.set_audit_context(
        {
            "inference_id": "inference-audit",
            "mcp_discovery_id": "mcp-discovery-audit",
        }
    )

    async def consume() -> None:
        async for _chunk in provider.generate_stream(messages, tools=tools, max_tokens=99):
            pass

    asyncio.run(consume())

    request = traces[0]
    response = traces[1]
    assert isinstance(request, dict)
    assert request["kind"] == "provider_request"
    assert request["input_stage"] == "application_provider_request"
    assert request["messages"] == messages
    assert request["tools"] == tools
    assert request["options"]["max_tokens"] == 99
    assert request["correlation"] == {
        "inference_id": "inference-audit",
        "mcp_discovery_id": "mcp-discovery-audit",
    }
    assert request["provider"]["provider_id"] == "local-test"
    assert request["provider"]["model_id"] == "model-test"
    assert request["provider"]["selection_mode"] == "once"
    assert request["provider"]["endpoint"] == "http://127.0.0.1:1234/v1"
    assert "secret" not in str(request)
    assert "hidden" not in str(request)
    assert isinstance(request["started_at_utc"], str)
    assert request["serialization"]["layer"] == "neutral_application_request"
    assert request["serialization"]["bytes"] > 0
    assert len(request["serialization"]["sha256"]) == 64
    assert isinstance(response, dict)
    assert response["kind"] == "provider_stream_response"
    serialized = str(response)
    assert "private reasoning" in serialized
    assert "player-safe answer" in serialized
    assert "do_thing" in serialized
    native_chunk = next(
        chunk for chunk in response["chunks"] if chunk["anthropic_content"] is not None
    )
    assert native_chunk["anthropic_content"][0]["signature"] == "opaque-signature"
    assert response["assembled_response"]["content"] == "player-safe answer"
    assert response["assembled_response"]["reasoning_content"] == "private reasoning"
    assert response["usage"]["availability"] == "not_returned"
    assert isinstance(response["duration_ms"], float)


def test_admin_audit_distinguishes_application_input_from_provider_wire_request() -> None:
    traces: list[object] = []
    provider = AdminAuditedProvider(
        _WireStreamingProvider(stream_chunks=[ProviderStreamChunk(content="hello")]),
        sink=traces.append,
        session_id="session-wire",
        role="dm",
    )
    provider.set_audit_context({"inference_id": "inference-wire"})

    async def consume() -> None:
        async for _chunk in provider.generate_stream(
            [{"role": "user", "content": "application input"}]
        ):
            pass

    asyncio.run(consume())

    application = traces[0]
    wire = traces[1]
    assert isinstance(application, dict) and isinstance(wire, dict)
    assert application["kind"] == "provider_request"
    assert application["input_stage"] == "application_provider_request"
    assert application["messages"] == [{"role": "user", "content": "application input"}]
    assert wire["kind"] == "provider_wire_request"
    assert wire["input_stage"] == "provider_specific_wire_request"
    assert wire["exchange_id"] == application["exchange_id"]
    assert wire["session_id"] == "session-wire"
    assert wire["role"] == "dm"
    assert wire["retry_attempt"] == 0
    assert wire["request"] == {
        "model": "wire-model-test",
        "messages": [
            {"role": "user", "content": "application input"},
            {"role": "assistant", "content": "<think>\n\n</think>\n\n"},
        ],
        "stream": True,
    }
    assert wire["correlation"] == {"inference_id": "inference-wire"}
    assert wire["serialization"]["layer"] == "provider_client_request_representation"
    assert len(wire["serialization"]["sha256"]) == 64


def test_admin_audit_does_not_retain_wire_observers_across_wrappers() -> None:
    base_provider = _WireStreamingProvider(stream_chunks=[ProviderStreamChunk(content="hello")])
    first_traces: list[object] = []
    second_traces: list[object] = []

    async def consume(provider: AdminAuditedProvider, content: str) -> None:
        async for _chunk in provider.generate_stream([{"role": "user", "content": content}]):
            pass

    first = AdminAuditedProvider(
        base_provider,
        sink=first_traces.append,
        session_id="session-first",
        role="dm",
    )
    asyncio.run(consume(first, "first request"))

    second = AdminAuditedProvider(
        base_provider,
        sink=second_traces.append,
        session_id="session-second",
        role="dm",
    )
    asyncio.run(consume(second, "second request"))

    assert [trace["kind"] for trace in first_traces] == [
        "provider_request",
        "provider_wire_request",
        "provider_stream_response",
    ]
    assert [trace["kind"] for trace in second_traces] == [
        "provider_request",
        "provider_wire_request",
        "provider_stream_response",
    ]
    for traces in (first_traces, second_traces):
        request = traces[0]
        wire = traces[1]
        assert wire["exchange_id"] == request["exchange_id"]


def test_admin_audit_isolates_concurrent_wrappers_on_one_provider() -> None:
    base_provider = _WireStreamingProvider(stream_chunks=[ProviderStreamChunk(content="hello")])
    first_traces: list[object] = []
    second_traces: list[object] = []
    first = AdminAuditedProvider(
        base_provider,
        sink=first_traces.append,
        session_id="session-first",
        role="dm",
    )
    second = AdminAuditedProvider(
        base_provider,
        sink=second_traces.append,
        session_id="session-second",
        role="dm",
    )

    async def consume(provider: AdminAuditedProvider, content: str) -> None:
        async for _chunk in provider.generate_stream([{"role": "user", "content": content}]):
            await asyncio.sleep(0)

    async def consume_both() -> None:
        await asyncio.gather(
            consume(first, "first request"),
            consume(second, "second request"),
        )

    asyncio.run(consume_both())

    for traces, session_id in (
        (first_traces, "session-first"),
        (second_traces, "session-second"),
    ):
        wires = [trace for trace in traces if trace["kind"] == "provider_wire_request"]
        assert len(wires) == 1
        assert wires[0]["session_id"] == session_id
        request = next(trace for trace in traces if trace["kind"] == "provider_request")
        assert wires[0]["exchange_id"] == request["exchange_id"]


def test_admin_audit_preserves_retried_wire_attempts_for_one_exchange() -> None:
    class RetryingWireProvider(_WireStreamingProvider):
        async def generate_stream(  # type: ignore[no-untyped-def]
            self, *args: object, **kwargs: object
        ):
            for retry_attempt in (0, 1):
                await self.hooks.fire_on_request(
                    HookContext(
                        provider_key="local-test",
                        model_id="model-test",
                        request_json={"model": "wire-model-test", "attempt": retry_attempt},
                        retry_attempt=retry_attempt,
                        stream=True,
                    )
                )
            async for chunk in FakeProvider.generate_stream(self, *args, **kwargs):
                yield chunk

    traces: list[object] = []
    provider = AdminAuditedProvider(
        RetryingWireProvider(stream_chunks=[ProviderStreamChunk(content="hello")]),
        sink=traces.append,
        session_id="session-retry",
        role="dm",
    )

    async def consume() -> None:
        async for _chunk in provider.generate_stream([{"role": "user", "content": "hello"}]):
            pass

    asyncio.run(consume())

    request = next(trace for trace in traces if trace["kind"] == "provider_request")
    wires = [trace for trace in traces if trace["kind"] == "provider_wire_request"]
    assert [wire["retry_attempt"] for wire in wires] == [0, 1]
    assert {wire["exchange_id"] for wire in wires} == {request["exchange_id"]}


def test_admin_audited_provider_includes_provider_returned_stream_usage() -> None:
    traces: list[object] = []
    provider = AdminAuditedProvider(
        _UsageStreamingProvider(stream_chunks=[ProviderStreamChunk(content="hello")]),
        sink=traces.append,
        session_id="session-usage",
        role="builder",
    )

    async def consume() -> None:
        async for _chunk in provider.generate_stream([{"role": "user", "content": "hi"}]):
            pass

    asyncio.run(consume())
    response = traces[-1]
    assert isinstance(response, dict)
    assert response["usage"] == {
        "availability": "provider_returned",
        "precision": "authoritative_count",
        "source": "provider_reported_completed_request",
        "scope": "completed_request_only",
        "input_tokens": 101,
        "output_tokens": 12,
        "total_tokens": 113,
        "cached_tokens": 40,
        "reasoning_tokens": 3,
    }
    assert response["retry_count"] == 1


def test_context_fit_blocks_before_provider_send_and_preserves_honest_basis() -> None:
    base = FakeProvider(stream_chunks=[ProviderStreamChunk(content="should not send")])
    base.model_info = ModelInfo(  # type: ignore[attr-defined]
        id="test/tiny",
        name="Tiny",
        provider_name="Test",
        provider_key="test",
        provider_mode="byok",
        context_window=512,
        max_output_tokens=128,
    )
    provider = ContextFittingProvider(base)

    async def consume() -> None:
        async for _chunk in provider.generate_stream([{"role": "user", "content": "x" * 900}]):
            pass

    try:
        asyncio.run(consume())
    except TokenLimitError as error:
        assert "token upper bound including reply reserve" in str(error)
        assert "Campaign Draft was preserved" in str(error)
    else:  # pragma: no cover - the provider must not receive an oversized request
        raise AssertionError("oversized request was sent")
    assert base.calls == []


def test_admin_audit_records_capacity_source_precision_reserve_and_admission() -> None:
    traces: list[object] = []
    base = FakeProvider(stream_chunks=[ProviderStreamChunk(content="ok")])
    base.model_info = ModelInfo(  # type: ignore[attr-defined]
        id="test/audited-fit",
        name="Audited Fit",
        provider_name="Test",
        provider_key="test",
        provider_mode="byok",
        context_window=8_192,
        runtime_context_window=8_192,
        runtime_context_source="runtime_reported",
        context_window_source="runtime_reported",
        max_output_tokens=1_024,
    )
    audited = AdminAuditedProvider(
        base,
        sink=traces.append,
        session_id="session-audited-fit",
        role="builder",
    )
    provider = ContextFittingProvider(audited)

    async def consume() -> None:
        async for _chunk in provider.generate_stream(
            [{"role": "user", "content": "hello"}],
            max_tokens=700,
        ):
            pass

    asyncio.run(consume())
    fit = next(trace for trace in traces if trace.get("kind") == "provider_context_fit")
    report = fit["correlation"]["context_fit"]
    assert report["precision"] == "conservative_bound"
    assert report["input_tokens"] is None
    assert report["input_token_bound"] > 0
    assert report["output_token_reserve"] == 700
    assert report["effective_context_capacity"] == 8_192
    assert report["capacity_source"] == "runtime_reported"
    assert report["admission_decision"] == "admitted"
    assert report["request_layer"] == "neutral_application_request"
    assert report["request_bytes"] > 0
    assert len(report["request_sha256"]) == 64


def test_admin_audit_records_rejected_preflight_without_provider_request() -> None:
    traces: list[object] = []
    base = FakeProvider(stream_chunks=[ProviderStreamChunk(content="must not send")])
    base.model_info = ModelInfo(  # type: ignore[attr-defined]
        id="test/rejected-fit",
        name="Rejected Fit",
        provider_name="Test",
        provider_key="test",
        provider_mode="byok",
        context_window=512,
        runtime_context_window=512,
        runtime_context_source="runtime_reported",
        context_window_source="runtime_reported",
        max_output_tokens=128,
    )
    provider = ContextFittingProvider(
        AdminAuditedProvider(
            base,
            sink=traces.append,
            session_id="session-rejected-fit",
            role="dm",
        )
    )

    with pytest.raises(TokenLimitError):
        provider.admit_request(
            [{"role": "user", "content": "x" * 1_000}],
            max_tokens=128,
        )
    assert [trace["kind"] for trace in traces] == ["provider_context_fit"]
    assert traces[0]["correlation"]["context_fit"]["admission_decision"] == "rejected"
    assert base.calls == []


def test_unknown_capacity_is_audited_as_unknown_and_blocked_before_send() -> None:
    traces: list[object] = []
    base = FakeProvider(stream_chunks=[ProviderStreamChunk(content="must not send")])
    base.model_info = ModelInfo(  # type: ignore[attr-defined]
        id="test/unknown-fit",
        name="Unknown Fit",
        provider_name="Test",
        provider_key="test",
        provider_mode="byok",
        context_window=None,
        max_output_tokens=128,
    )
    provider = ContextFittingProvider(
        AdminAuditedProvider(
            base,
            sink=traces.append,
            session_id="session-unknown-fit",
            role="builder",
        )
    )

    with pytest.raises(TokenLimitError, match="no trustworthy effective context capacity"):
        provider.admit_request([{"role": "user", "content": "hello"}], max_tokens=128)
    report = traces[0]["correlation"]["context_fit"]
    assert report["precision"] == "conservative_bound"
    assert report["effective_context_capacity"] is None
    assert report["capacity_source"] in {"unknown", "legacy_unverified"}
    assert report["admission_decision"] == "rejected"
    assert base.calls == []


def test_admin_full_audit_is_off_by_default_for_ordinary_use_and_clears_on_restart(
    tmp_path: Path,
) -> None:
    database = tmp_path / "audit.db"
    with AuthorityOwner.open(database, "owner-audit-a") as owner:
        owner.record_admin_full_audit({"ignored": True})
        assert owner.admin_full_audit_projection()["traces"] == []
        enabled = owner.set_admin_full_audit(True)
        assert enabled["enabled"] is True
        owner.record_admin_full_audit({"kind": "provider_request", "messages": ["secret"]})
        projection = owner.admin_full_audit_projection()
        assert projection["trace_count"] == 1
        assert projection["traces"][0]["messages"] == ["secret"]
        assert owner.admin_full_audit_status() == {
            "enabled": True,
            "trace_count": 1,
            "safe_mechanics_diagnostics": [],
        }
        assert owner.admin_full_audit_record(0) == {
            "index": 0,
            "trace": {"kind": "provider_request", "messages": ["secret"]},
        }
        export_path = tmp_path / "audit-export.json"
        export = owner.export_admin_full_audit(export_path)
        assert export["status"] == "exported"
        assert export["trace_count"] == 1
        assert export["bytes"] == export_path.stat().st_size
        assert json.loads(export_path.read_text(encoding="utf-8")) == projection
        assert not list(tmp_path.glob(".audit-export.json.*.tmp"))
        disabled = owner.set_admin_full_audit(False)
        assert disabled == {"enabled": False, "trace_count": 0}

    with AuthorityOwner.open(database, "owner-audit-b") as reopened:
        projection = reopened.admin_full_audit_projection()
        assert projection == {
            "enabled": False,
            "trace_count": 0,
            "traces": [],
            "safe_mechanics_diagnostics": [],
        }


@pytest.mark.parametrize(
    ("error_number", "windows_error", "reason"),
    [
        (errno.ENOSPC, None, "not enough space"),
        (errno.EACCES, None, "not writable"),
        (errno.ENOENT, None, "unavailable"),
        (errno.ENOTDIR, None, "unavailable"),
        (errno.EIO, None, "Try another destination"),
        (errno.EIO, 112, "not enough space"),
        (errno.EIO, 39, "not enough space"),
        (errno.EIO, 5, "not writable"),
        (errno.EIO, 3, "unavailable"),
    ],
)
def test_audit_save_errors_are_actionable_and_retry_keeps_exact_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_number: int,
    windows_error: int | None,
    reason: str,
) -> None:
    destination = tmp_path / "audit.json"
    destination.write_bytes(b"previous export")
    with AuthorityOwner.open(tmp_path / "audit.db", "owner-audit-errors") as owner:
        owner.set_admin_full_audit(True)
        owner.record_admin_full_audit({"kind": "synthetic", "text": "private fixture"})
        projection = owner.admin_full_audit_projection()
        error = OSError(error_number, "private exception payload")
        if windows_error is not None:
            monkeypatch.setattr(error, "winerror", windows_error, raising=False)

        def fail_sync(_descriptor: int) -> None:
            raise error

        with monkeypatch.context() as patch:
            patch.setattr("sylvia_gm.application.authority.os.fsync", fail_sync)
            with pytest.raises(CommandRejected) as failure:
                owner.export_admin_full_audit(destination)
        assert reason in str(failure.value)
        assert "private" not in str(failure.value)
        assert destination.read_bytes() == b"previous export"
        assert owner.admin_full_audit_projection() == projection
        assert not list(tmp_path.glob(".audit.json.*.tmp"))
        result = owner.export_admin_full_audit(destination)
        assert result["status"] == "exported"
        assert json.loads(destination.read_text(encoding="utf-8")) == projection


@pytest.mark.parametrize("failure_kind", ["replace", "serialization", "cleanup"])
def test_audit_save_failure_preserves_destination_and_original_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
) -> None:
    destination = tmp_path / "audit.json"
    destination.write_bytes(b"previous export")
    with AuthorityOwner.open(tmp_path / "audit.db", "owner-audit-failures") as owner:
        owner.set_admin_full_audit(True)
        owner.record_admin_full_audit({"kind": "synthetic"})
        projection = owner.admin_full_audit_projection()

        def fail(*_args: object, **_kwargs: object) -> None:
            if failure_kind == "serialization":
                raise TypeError("private serialization payload")
            raise OSError(errno.ENOSPC, "private filesystem payload")

        def fail_cleanup(*_args: object, **_kwargs: object) -> None:
            raise OSError(errno.EACCES, "private cleanup payload")

        with monkeypatch.context() as patch:
            patch.setattr(
                "sylvia_gm.application.authority."
                + {
                    "replace": "os.replace",
                    "serialization": "json.dumps",
                    "cleanup": "os.fsync",
                }[failure_kind],
                fail,
            )
            if failure_kind == "cleanup":
                patch.setattr(Path, "unlink", fail_cleanup)
            with pytest.raises(CommandRejected) as failure:
                owner.export_admin_full_audit(destination)
        expected = (
            "could not serialize exact records"
            if failure_kind == "serialization"
            else "not enough space"
        )
        assert expected in str(failure.value)
        assert "private" not in str(failure.value)
        assert destination.read_bytes() == b"previous export"
        assert owner.admin_full_audit_projection() == projection
        assert bool(list(tmp_path.glob(".audit.json.*.tmp"))) is (failure_kind == "cleanup")
        assert owner.export_admin_full_audit(destination)["status"] == "exported"
        assert json.loads(destination.read_text(encoding="utf-8")) == projection


def test_audit_export_dispatch_reports_unavailable_destination_and_allows_retry(
    tmp_path: Path,
) -> None:
    with AuthorityOwner.open(tmp_path / "audit.db", "owner-audit-dispatch") as owner:
        owner.set_admin_full_audit(True)
        owner.record_admin_full_audit({"kind": "synthetic"})

        def dispatch(destination: Path) -> object:
            return _dispatch(
                None,  # type: ignore[arg-type]
                "export_admin_full_audit",
                {"destination_path": str(destination)},
                application_owner=owner,
                owner_id="owner-audit-dispatch",
                provider_resolver=None,  # type: ignore[arg-type]
                model_choices=lambda: (),
                session_loop=None,  # type: ignore[arg-type]
                storage_root=tmp_path,
            )

        with pytest.raises(CommandRejected, match="Choose an available folder or drive"):
            dispatch(tmp_path / "unavailable" / "audit.json")
        assert owner.admin_full_audit_status()["trace_count"] == 1
        result = dispatch(tmp_path / "retry.json")
        assert isinstance(result, Mapping)
        assert result["status"] == "exported"
        assert json.loads((tmp_path / "retry.json").read_text(encoding="utf-8")) == (
            owner.admin_full_audit_projection()
        )


def test_admin_full_audit_exports_more_than_the_desktop_protocol_limit(tmp_path: Path) -> None:
    database = tmp_path / "large-audit.db"
    transport_limit = 67_108_864
    synthetic_segment = "x" * (4 * 1024 * 1024)
    export_path = tmp_path / "large-audit-export.json"

    with AuthorityOwner.open(database, "owner-large-audit") as owner:
        owner.set_admin_full_audit(True)
        for index in range(17):
            owner.record_admin_full_audit(
                {"kind": "synthetic_large_record", "index": index, "payload": synthetic_segment}
            )

        def dispatch(method: str, params: dict[str, object]) -> object:
            return _dispatch(
                None,  # type: ignore[arg-type]
                method,
                params,
                application_owner=owner,
                owner_id="owner-large-audit",
                provider_resolver=None,  # type: ignore[arg-type]
                model_choices=lambda: (),
                session_loop=None,  # type: ignore[arg-type]
                storage_root=tmp_path,
            )

        status = dispatch("admin_full_audit_status", {})
        assert isinstance(status, Mapping)
        assert status == {
            "enabled": True,
            "trace_count": 17,
            "safe_mechanics_diagnostics": [],
        }
        assert len(json.dumps(thaw_json_value(status)).encode("utf-8")) < 1_024
        record = dispatch("admin_full_audit_record", {"index": 16})
        assert isinstance(record, Mapping)
        assert record["trace"] is None
        assert record["unavailable_reason"] == "record_too_large_for_live_view"
        assert record["bytes"] > 4 * 1024 * 1024
        assert owner.admin_full_audit_record(16)["trace"]["payload"] == synthetic_segment

        exported = dispatch(
            "export_admin_full_audit", {"destination_path": str(export_path.resolve())}
        )

    assert isinstance(exported, Mapping)
    assert exported["status"] == "exported"
    assert exported["trace_count"] == 17
    assert exported["bytes"] == export_path.stat().st_size
    assert export_path.stat().st_size > transport_limit
    with export_path.open("rb") as stream:
        assert stream.read(24).startswith(b'{\n  "enabled": true')
        stream.seek(-3, 2)
        assert stream.read() == b"]\n}\n"[-3:]
    assert not list(tmp_path.glob(".large-audit-export.json.*.tmp"))


def test_public_exact_input_examples_are_complete_and_correlated(public_audit_root: Path) -> None:
    ruleless = json.loads(
        (public_audit_root / "ruleless-opening-and-history.json").read_text(encoding="utf-8")
    )
    srd = json.loads(
        (public_audit_root / "srd-rift-opening-combat.json").read_text(encoding="utf-8")
    )
    prompt = (
        (public_audit_root / "current-dm-system-prompt.txt")
        .read_text(encoding="utf-8")
        .rstrip("\n")
    )
    assert prompt == _dm_system_prompt(
        campaign_has_mechanics=False,
        tools_available=False,
    )

    ruleless_requests = [
        trace for trace in ruleless["traces"] if trace["kind"] == "provider_request"
    ]
    ruleless_wires = [
        trace for trace in ruleless["traces"] if trace["kind"] == "provider_wire_request"
    ]
    ruleless_discoveries = {
        trace["discovery_id"]: trace
        for trace in ruleless["traces"]
        if trace["kind"] == "mcp_list_tools"
    }
    assert len(ruleless_requests) == len(ruleless_wires) == len(ruleless_discoveries) == 2
    ruleless_surfaces = [
        {
            (
                tool["function"]["name"].split("_", 2)[-1]
                if tool["function"]["name"].startswith("tool_")
                else tool["function"]["name"]
            )
            for tool in request["tools"]
        }
        for request in ruleless_requests
    ]
    assert ruleless_surfaces == [
        {
            "search_campaign_knowledge",
            "read_campaign_reference",
            "update_campaign_file",
        },
        {
            "search_campaign_knowledge",
            "read_campaign_reference",
            "update_campaign_file",
        },
    ]
    assert [request["options"].get("tool_choice") for request in ruleless_requests] == [
        "required",
        "required",
    ]
    assert all(
        request["correlation"]["mcp_discovery_id"] in ruleless_discoveries
        for request in ruleless_requests
    )
    assert str(ruleless_requests[0]["messages"][-1]["content"]).startswith("SYLVIA_GM_OPENING_TASK")
    assert (
        "application-owned start signal, not player speech"
        in (ruleless_requests[0]["messages"][-1]["content"])
    )
    assert any(
        message["role"] == "user"
        and message["content"] == "I wait beneath the nearest tree and call out for Elian."
        for message in ruleless_requests[1]["messages"]
    )
    assert ruleless_requests[1]["messages"][-1] == {
        "role": "user",
        "content": "I wait beneath the nearest tree and call out for Elian.",
    }
    assert "SYLVIA_GM_CURRENT_TURN" not in json.dumps(
        ruleless_requests[1]["messages"], ensure_ascii=False
    )
    assert str(ruleless_requests[1]["messages"][-2]["content"]).startswith(
        "SYLVIA_GM_CURRENT_PLAYER_MESSAGE"
    )
    final_arguments = [
        json.loads(chunk["tool_call_delta"]["function"]["arguments"])
        for trace in ruleless["traces"]
        if trace["kind"] == "provider_stream_response"
        for chunk in trace["chunks"]
        if isinstance(chunk.get("tool_call_delta"), Mapping)
        and str(chunk["tool_call_delta"]["function"]["name"]).endswith("_update_campaign_file")
    ]
    assert len(final_arguments) == 2
    assert all("revised_player_facing_response" in arguments for arguments in final_arguments)
    assert all(
        isinstance(arguments["revised_player_facing_response"], list)
        and arguments["revised_player_facing_response"][0]["kind"] == "narration"
        for arguments in final_arguments
    )
    assert all("player_action_preparation_id" not in arguments for arguments in final_arguments)
    ruleless_campaign_file_results = [
        trace["result"]["status"]
        for trace in ruleless["traces"]
        if trace["kind"] == "mcp_update_campaign_file"
    ]
    assert ruleless_campaign_file_results == [
        "campaign_file_updated",
        "campaign_file_unchanged",
    ]
    assert "Begin the story" not in json.dumps(ruleless_requests, ensure_ascii=False)
    assert all(
        wire["request"]["messages"] == request["messages"]
        for request, wire in zip(ruleless_requests, ruleless_wires, strict=True)
    )
    assert "<think>" not in json.dumps(ruleless_wires, ensure_ascii=False)

    srd_requests = [trace for trace in srd["traces"] if trace["kind"] == "provider_request"]
    srd_wires = [trace for trace in srd["traces"] if trace["kind"] == "provider_wire_request"]
    srd_discoveries = {
        trace["discovery_id"]: trace
        for trace in srd["traces"]
        if trace["kind"] == "mcp_list_tools" and trace["purpose"] == "provider_inference"
    }
    assert len(srd_requests) == len(srd_wires)
    assert len(srd_discoveries) >= len(srd_requests)
    srd_calls = [trace["tool_name"] for trace in srd["traces"] if trace["kind"] == "mcp_call_tool"]
    player_first_calls = [
        "start_combat_encounter_entities",
        "begin_combat_turn",
        "resolve_combat_attack",
        "end_combat_turn",
        "advance_combat_encounter_turn",
        "begin_combat_turn",
        "resolve_adjudicated_creature_attack",
        "end_combat_turn",
        "advance_combat_encounter_turn",
        "begin_combat_turn",
    ]
    creature_first_calls = [
        "start_combat_encounter_entities",
        "begin_combat_turn",
        "resolve_adjudicated_creature_attack",
        "end_combat_turn",
        "advance_combat_encounter_turn",
        "begin_combat_turn",
        *player_first_calls[2:],
    ]
    assert srd_calls in (player_first_calls, creature_first_calls)
    assert srd_calls.count("resolve_combat_attack") == 1
    assert len(srd_requests) >= 7
    assert all(
        request["correlation"]["mcp_discovery_id"] in srd_discoveries for request in srd_requests
    )
    surfaces = [
        {
            (
                tool["function"]["name"].split("_", 2)[-1]
                if tool["function"]["name"].startswith("tool_")
                else tool["function"]["name"]
            )
            for tool in request["tools"]
        }
        for request in srd_requests
    ]
    assert surfaces[0] == {
        "d20_test",
        "search_campaign_knowledge",
        "read_campaign_reference",
        "start_combat_encounter_entities",
        "start_group_combat_encounter",
        "take_srd_short_rest_hit_die",
        "use_srd_second_wind",
        "unbound_d20_test",
        "update_campaign_file",
    }
    start_request = next(
        request
        for request, surface in zip(srd_requests, surfaces, strict=True)
        if "start_combat_encounter_entities" in surface
    )
    assert not any(
        name.startswith("resolve_combat_attack")
        for name in {tool["function"]["name"].split("_", 2)[-1] for tool in start_request["tools"]}
    )
    start_discovery = srd_discoveries[start_request["correlation"]["mcp_discovery_id"]]
    start_tool = next(
        tool
        for tool in start_discovery["tools"]
        if tool["name"] == "start_combat_encounter_entities"
    )
    surprise_properties = start_tool["input_schema"]["properties"]
    assert (
        "unaware that combat was starting" in surprise_properties["first_surprised"]["description"]
    )
    assert (
        "already watching, threatening, or ready to act"
        in surprise_properties["second_surprised"]["description"]
    )
    assert any("resolve_combat_attack" in surface for surface in surfaces)
    assert any("resolve_adjudicated_creature_attack" in surface for surface in surfaces)
    assert all("begin_combat_turn" not in surface for surface in surfaces)
    assert all("advance_combat_encounter_turn" not in surface for surface in surfaces)
    assert not any(
        message["role"] in {"assistant", "tool"} for message in srd_requests[0]["messages"]
    )
    assert any(
        str(message.get("content", "")).startswith("SYLVIA_GM_CAMPAIGN_PREPARATION")
        and "private story preparation and desired direction" in str(message["content"])
        for message in srd_requests[0]["messages"]
    )
    assert (
        "immediately call the currently exposed start-combat mechanic"
        not in json.dumps(srd_requests[0], ensure_ascii=False).casefold()
    )
    assert "watches without advancing" in json.dumps(srd["traces"], ensure_ascii=False)
    assert any("resolve_adjudicated_creature_attack" in surface for surface in surfaces)
    assert any(
        "end_combat_turn" in surface
        and "resolve_adjudicated_creature_attack" not in surface
        and "resolve_combat_attack" not in surface
        for surface in surfaces
    )
    assert "begin_combat_turn" in srd_calls
    assert "advance_combat_encounter_turn" in srd_calls
    for surface in surfaces:
        if "resolve_combat_attack" not in surface:
            continue
        assert surface.isdisjoint(
            {
                "cast_spell",
                "death_saving_throw",
                "resolve_opportunity_attack",
                "spend_srd_reaction",
                "stabilize_character",
            }
        )
    # The fighter now has Second Wind; its granted Bonus Action is discoverable on its turn.
    assert any(
        {"resolve_combat_attack", "spend_srd_bonus_action", "use_srd_second_wind"} <= surface
        for surface in surfaces
    )
    post_player_attack = next(
        request
        for request in srd_requests
        if any(
            str(message.get("content", "")).startswith("SYLVIA_GM_EXACT_NARRATION_FACTS")
            and '"tool":"resolve_combat_attack"' in str(message.get("content", ""))
            for message in request["messages"]
        )
    )
    post_attack_tools = {
        (
            str(tool["function"]["name"]).split("_", 2)[-1]
            if str(tool["function"]["name"]).startswith("tool_")
            else str(tool["function"]["name"])
        )
        for tool in post_player_attack["tools"]
    }
    assert {
        "search_campaign_knowledge",
        "read_campaign_reference",
        "update_campaign_file",
    } <= post_attack_tools
    assert "resolve_combat_attack" not in post_attack_tools
    assert str(post_player_attack["messages"][-1]["content"]).startswith(
        "SYLVIA_GM_PLAYER_BOUNDARY_NARRATION"
    )
    assert any(
        str(message.get("content", "")).startswith("SYLVIA_GM_EXACT_NARRATION_FACTS")
        for request in srd_requests
        for message in request["messages"]
    )
    assert any(
        str(message.get("content", "")).startswith("SYLVIA_GM_AUTOMATIC_ENGINE_TRANSITION_RESULT")
        for request in srd_requests
        for message in request["messages"]
    )
    guide_message = next(
        message["content"]
        for message in srd_requests[0]["messages"]
        if str(message.get("content", "")).startswith("SYLVIA_GM_RULESET_OPERATING_GUIDE")
    )
    assert "Out of combat" in guide_message
    assert "start_combat_encounter_entities" in guide_message
    assert "Do not start combat solely" in guide_message
    preparation_message = next(
        message["content"]
        for message in srd_requests[0]["messages"]
        if str(message.get("content", "")).startswith("SYLVIA_GM_CAMPAIGN_PREPARATION")
    )
    preparation = json.loads(str(preparation_message).split("\n", 1)[1])
    plan_preparation = preparation["campaign_plan_preparation"]
    prepared_act = plan_preparation["prepared_acts"][0]
    setting_sources = preparation["campaign_setting_references"]
    assert plan_preparation["starting_location"] == {
        "id": "verdant-margin",
        "path": "locations/verdant-margin.md",
    }
    assert "situations" not in prepared_act
    assert "planned_encounters" not in prepared_act
    assert "Read the structured Campaign Plan" in prepared_act["detail_handling"]
    assert len(setting_sources) == 1
    assert setting_sources[0]["selection"] == {
        "kind": "markdown_section",
        "heading": "Overview",
        "complete": True,
    }
    assert setting_sources[0]["purpose"] == "starting-location overview for opening texture"
    assert "## Hooks" not in setting_sources[0]["content"]
    assert any(
        message.get("role") == "assistant"
        for request in srd_requests[1:]
        for message in request["messages"]
    )
    assert "I strike the Riftwolf with my quarterstaff." in {
        message["content"]
        for request in srd_requests
        for message in request["messages"]
        if message["role"] == "user"
    }
    assert "I end my turn and brace for the Riftwolf." in {
        message["content"]
        for request in srd_requests
        for message in request["messages"]
        if message["role"] == "user"
    }
    for request in srd_requests:
        assert request["options"].get("tool_choice") == "required"
    assert any("update_campaign_file" in surface for surface in surfaces)
    assert any("update_campaign_file" not in surface for surface in surfaces)
    for request, surface in zip(srd_requests, surfaces, strict=True):
        if "update_campaign_file" in surface:
            continue
        assert str(request["messages"][-1]["content"]).startswith(
            (
                "SYLVIA_GM_DECISION_REQUEST",
                "SYLVIA_GM_PLAYER_BOUNDARY_NARRATION",
            )
        )
        if str(request["messages"][-1]["content"]).startswith(
            "SYLVIA_GM_PLAYER_BOUNDARY_NARRATION"
        ):
            assert '"declared_player_intent_pending":true' in str(
                request["messages"][-1]["content"]
            )
    assert not any(
        str(tool["function"]["description"]).startswith("REQUIRED ENGINE TRANSITION:")
        for request in srd_requests
        for tool in request["tools"]
    )
    assert all(
        response["usage"]
        == {
            "availability": "not_returned",
            "precision": "unavailable",
            "source": "unavailable",
            "scope": "unavailable",
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
            "cached_tokens": None,
            "reasoning_tokens": None,
        }
        for response in srd["traces"]
        if response["kind"] == "provider_stream_response"
    )


def test_public_exact_input_examples_regenerate_with_current_tool_schemas(
    tmp_path: Path,
    public_audit_root: Path,
) -> None:
    generated_root = tmp_path / "model-input-audit"
    generate_examples(generated_root)

    generated = json.loads(
        (generated_root / "srd-rift-opening-combat.json").read_text(encoding="utf-8")
    )
    baseline = json.loads(
        (public_audit_root / "srd-rift-opening-combat.json").read_text(encoding="utf-8")
    )
    generated_traces = generated["traces"]
    baseline_traces = baseline["traces"]
    generated_calls = [trace for trace in generated_traces if trace.get("kind") == "mcp_call_tool"]
    baseline_calls = [trace for trace in baseline_traces if trace.get("kind") == "mcp_call_tool"]

    def call_signature(trace: Mapping[str, object]) -> tuple[str, str, bool]:
        return (
            str(trace["tool_name"]),
            json.dumps(trace["arguments"], sort_keys=True, separators=(",", ":")),
            trace["is_error"] is True,
        )

    stochastic_tool = "resolve_adjudicated_creature_attack"
    assert {
        call_signature(trace) for trace in generated_calls if trace["tool_name"] != stochastic_tool
    } == {
        call_signature(trace) for trace in baseline_calls if trace["tool_name"] != stochastic_tool
    }
    assert {str(trace["tool_name"]) for trace in generated_calls}.symmetric_difference(
        {str(trace["tool_name"]) for trace in baseline_calls}
    ) <= {stochastic_tool}

    schemas: dict[str, Mapping[str, object]] = {}
    for trace in generated_traces:
        if trace.get("kind") != "mcp_list_tools":
            continue
        for tool in trace["tools"]:
            name = tool["name"]
            schema = tool["input_schema"]
            if name in schemas:
                assert schemas[name] == schema
            else:
                schemas[name] = schema
    for call in generated_calls:
        assert call["is_error"] is False
        validate_parameters(call["arguments"], schemas[call["tool_name"]])

    assert (generated_root / "current-dm-system-prompt.txt").read_text(encoding="utf-8") == (
        public_audit_root / "current-dm-system-prompt.txt"
    ).read_text(encoding="utf-8")
