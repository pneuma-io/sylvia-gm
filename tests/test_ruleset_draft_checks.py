"""Focused contract tests for the Builder Ruleset draft checker."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest

from sylvia_gm.agent._builder_session import (
    _ruleset_candidate_tool,
    run_builder_turn,
    stream_builder_turn,
)
from sylvia_gm.agent._ruleset_draft_check_tool import ruleset_draft_check_tool
from sylvia_gm.agent._session_loop import ProviderControl, ProviderEventKind
from sylvia_gm.application.package_library import install_package_archive
from sylvia_gm.builder._package_materializer import materialize_ruleset_draft
from sylvia_gm.domain.authority import WriterRole
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.session import SessionContext, SessionRecord
from sylvia_gm.provider._base import Provider, ProviderResponse, ProviderStreamChunk, ToolCall

FIXTURE = Path(__file__).parent / "fixtures" / "rulesets" / "signal-and-static"


def _stage_fixture(workspace: Path, *, invalid_runtime: bool = False) -> None:
    draft = workspace / "ruleset-draft"
    files = draft / "files"
    (files / "runtime").mkdir(parents=True)
    (files / "content").mkdir(parents=True)
    (draft / "package.json").write_bytes((FIXTURE / "package-record.json").read_bytes())
    runtime = (FIXTURE / "runtime" / "ruleset.yaml").read_text(encoding="utf-8")
    if invalid_runtime:
        runtime = runtime.replace("  resolve_intrusion:\n", "  unresolved_intrusion:\n", 1)
    (files / "runtime" / "ruleset.yaml").write_text(runtime, encoding="utf-8")
    (files / "content" / "content-declarations.json").write_bytes(
        (FIXTURE / "content" / "content-declarations.json").read_bytes()
    )


def _tree(root: Path) -> dict[str, bytes | None]:
    return {
        path.relative_to(root).as_posix(): None if path.is_dir() else path.read_bytes()
        for path in root.rglob("*")
    }


def _context() -> SessionContext:
    return SessionContext(
        session=SessionRecord(
            session_id="ruleset-check-session",
            campaign_id="campaign-ruleset-check",
            role=WriterRole.BUILDER,
            provider_id="test.builder",
            model_id="test.builder/model",
            selected_skill="BUILD RULESET",
        ),
        messages=(),
    )


def test_valid_draft_check_is_read_only_and_does_not_publish(tmp_path: Path) -> None:
    _stage_fixture(tmp_path)
    before = _tree(tmp_path)

    result = ruleset_draft_check_tool(tmp_path).handler({}, None)

    assert result["success"] is True
    assert result["status"] == "valid_draft"
    assert result["published"] is False
    assert result["checked"] == ["source", "package", "references", "runtime"]
    assert _tree(tmp_path) == before
    assert not (tmp_path / "ruleset-draft" / "output").exists()


def test_invalid_runtime_matches_install_rejection_before_store_writes(tmp_path: Path) -> None:
    _stage_fixture(tmp_path, invalid_runtime=True)
    checked = ruleset_draft_check_tool(tmp_path).handler({}, None)
    with materialize_ruleset_draft(tmp_path) as materialized:
        archive = materialized.archive.read()

    class Store:
        def __init__(self) -> None:
            self.writes: list[str] = []

        def stage_package_archive(self, _admitted: object, _install_id: str) -> None:
            self.writes.append("stage")

        def publish_package_archive(self, _admitted: object, _install_id: str) -> object:
            self.writes.append("publish")
            raise AssertionError("publication must remain unreachable")

    store = Store()
    with pytest.raises(CommandRejected) as rejected:
        install_package_archive(
            store, BytesIO(archive), install_id="invalid-runtime",  # type: ignore[arg-type]
        )

    assert store.writes == []
    assert checked["code"] == rejected.value.code == "package_runtime_invalid"
    assert checked["message"] == str(rejected.value)
    assert checked["validation_errors"] == [
        item.as_payload() for item in rejected.value.diagnostics
    ]


def test_source_diagnostics_survive_check_and_publication_tool(tmp_path: Path) -> None:
    source = tmp_path / "ruleset-draft" / "ruleset-source.yaml"
    source.parent.mkdir(parents=True)
    source.write_text("format: 1\nid: example.incomplete\n", encoding="utf-8")
    published = False

    def publish(*_args: object, **_kwargs: object) -> dict[str, object]:
        nonlocal published
        published = True
        return {}

    checked = ruleset_draft_check_tool(tmp_path).handler({}, None)
    publication = _ruleset_candidate_tool(
        workspace=tmp_path,
        session_id="ruleset-check-session",
        publisher=publish,  # type: ignore[arg-type]
    ).handler({}, None)

    assert published is False
    assert checked["code"] == publication["code"] == "builder_ruleset_source_invalid"
    assert checked["message"] == publication["message"]
    assert checked["validation_errors"] == publication["validation_errors"]
    assert "$.version" in {item["path"] for item in checked["validation_errors"]}


class _CheckingProvider(Provider):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[list[dict[str, Any]], list[dict[str, Any]] | None]] = []

    async def generate(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
        **_kwargs: object,
    ) -> ProviderResponse:
        self.calls.append((messages.copy(), tools))
        if len(self.calls) == 1:
            names = {tool["function"]["name"] for tool in tools or []}
            assert "check_ruleset_draft" in names
            assert "test_ruleset_draft" in names
            assert "publish_ruleset_candidate" not in names
            return ProviderResponse(tool_calls=[ToolCall(
                id="check-draft", name="check_ruleset_draft", arguments="{}",
            )])
        results = [
            json.loads(message["content"]) for message in messages if message["role"] == "tool"
        ]
        assert results[-1]["status"] == "valid_draft"
        assert results[-1]["published"] is False
        return ProviderResponse(content="The draft is structurally valid.")

    async def generate_stream(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
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
def test_builder_roundtrip_checks_without_publisher_authority(
    tmp_path: Path, streaming: bool,
) -> None:
    _stage_fixture(tmp_path)
    provider = _CheckingProvider()
    kwargs = {
        "context": _context(),
        "message_id": f"check-{'stream' if streaming else 'sync'}",
        "user_text": "Check this Ruleset draft.",
        "workspace": tmp_path,
        "control": ProviderControl(),
    }
    if streaming:
        completed = []
        events = list(stream_builder_turn(
            provider, **kwargs, complete=completed.append,  # type: ignore[arg-type]
        ))
        evidence = [event.evidence for event in events if event.kind is ProviderEventKind.EVIDENCE]
        assert completed[0].tool_calls_made == ("check_ruleset_draft",)
        result = next(
            item["result"] for item in evidence if item["tool_name"] == "check_ruleset_draft"
        )
    else:
        turn = run_builder_turn(provider, **kwargs)  # type: ignore[arg-type]
        assert turn.tool_calls_made == ("check_ruleset_draft",)
        result = next(
            message.content["result"] for message in turn.messages
            if message.content.get("tool_name") == "check_ruleset_draft"
        )
    assert result["status"] == "valid_draft"
    assert len(provider.calls) == 2
