"""Ordinary Builder draft-to-candidate-to-neutral-play acceptance."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import zipfile
from collections.abc import AsyncIterator
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest

from sylvia_gm.agent._builder_session import run_builder_turn
from sylvia_gm.agent._session_loop import CampaignSessionLoop, ProviderControl
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner, CampaignCreate
from sylvia_gm.application.package_materialization import (
    NormalizedPackageDraft,
    materialize_package_archive,
)
from sylvia_gm.builder import _package_materializer as package_materializer
from sylvia_gm.builder._package_materializer import materialize_ruleset_draft
from sylvia_gm.builder._tools import (
    BUILDER_TOOL_DEFINITIONS,
    RULESET_CANDIDATE_TOOL_DEFINITION,
)
from sylvia_gm.domain.authority import ActionStatus, PackageLock, StateValue, WriterRole
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import canonical_json_bytes, closure_sha256
from sylvia_gm.domain.session import SessionContext, SessionRecord
from sylvia_gm.infrastructure.launch_handshake import LaunchHandshake
from sylvia_gm.infrastructure.package_archive import admit_package_archive
from sylvia_gm.interfaces.owner_process import (
    _dispatch,
    _json_value,
    _publish_builder_ruleset_candidate,
    run,
)
from sylvia_gm.provider._base import Provider, ProviderResponse, ProviderStreamChunk, ToolCall

_FIXTURE = Path(__file__).parent / "fixtures" / "rulesets" / "signal-and-static"
_SETTING_FIXTURE = Path(__file__).parent / "fixtures" / "campaign-settings" / "signal-and-static"
_SCENARIOS = Path(__file__).parent / "fixtures" / "ruleset-scenarios" / "signal-and-static.json"
_PACKAGE_ID = "sylvia-gm.conformance.signal-static"
_PRIVATE_SENTINEL = "PRIVATE-CAMPAIGN-MATERIAL-MUST-NOT-ENTER-PACKAGE"


def _stage_ruleset_draft(workspace: Path) -> None:
    draft = workspace / "ruleset-draft"
    files = draft / "files"
    (files / "runtime").mkdir(parents=True)
    (files / "content").mkdir(parents=True)
    (draft / "package.json").write_bytes((_FIXTURE / "package-record.json").read_bytes())
    (files / "runtime" / "ruleset.yaml").write_bytes(
        (_FIXTURE / "runtime" / "ruleset.yaml").read_bytes()
    )
    (files / "content" / "content-declarations.json").write_bytes(
        (_FIXTURE / "content" / "content-declarations.json").read_bytes()
    )


def test_normalized_package_materializer_is_one_deterministic_admitted_seam() -> None:
    """Trusted and guarded adapters receive the same deep materialization behavior."""

    record = json.loads((_FIXTURE / "package-record.json").read_text(encoding="utf-8"))
    payloads = {
        "content/content-declarations.json": (
            _FIXTURE / "content" / "content-declarations.json"
        ).read_bytes(),
        "runtime/ruleset.yaml": (_FIXTURE / "runtime" / "ruleset.yaml").read_bytes(),
    }
    draft = NormalizedPackageDraft(record=record, payloads=payloads)

    with materialize_package_archive((draft,)) as first:
        first_bytes = first.archive.read()
        first_identity = first.manifests[0].identity
        first_closure = first.closure_sha256
    with materialize_package_archive((draft,)) as second:
        assert second.archive.read() == first_bytes
        assert second.manifests[0].identity == first_identity
        assert second.closure_sha256 == first_closure

    with zipfile.ZipFile(BytesIO(first_bytes), "r") as archive:
        assert archive.namelist() == [
            "manifest.json",
            f"packages/{_PACKAGE_ID}/content/content-declarations.json",
            f"packages/{_PACKAGE_ID}/runtime/ruleset.yaml",
        ]
    with admit_package_archive(BytesIO(first_bytes)) as admitted:
        entry = admitted.package_lock.entries[0]
        assert (entry.package_id, entry.version, entry.sha256) == first_identity


def test_normalized_materializer_resolves_a_local_draft_dependency() -> None:
    """A complete draft set needs no adapter-specific dependency hash calculation."""

    ruleset_record = json.loads((_FIXTURE / "package-record.json").read_text(encoding="utf-8"))
    setting_record = json.loads(
        (_SETTING_FIXTURE / "package-record.json").read_text(encoding="utf-8")
    )
    ruleset_record["dependencies"] = [
        {
            "namespace": "campaign_setting",
            "kind": "campaign_setting",
            "id": setting_record["id"],
            "version": setting_record["version"],
        }
    ]
    ruleset = NormalizedPackageDraft(
        record=ruleset_record,
        payloads={
            "content/content-declarations.json": (
                _FIXTURE / "content" / "content-declarations.json"
            ).read_bytes(),
            "runtime/ruleset.yaml": (_FIXTURE / "runtime" / "ruleset.yaml").read_bytes(),
        },
    )
    setting = NormalizedPackageDraft(
        record=setting_record,
        payloads={
            path.relative_to(_SETTING_FIXTURE).as_posix(): path.read_bytes()
            for path in _SETTING_FIXTURE.rglob("*")
            if path.is_file() and path.name != "package-record.json"
        },
    )

    with materialize_package_archive((ruleset, setting)) as materialized:
        primary = next(item for item in materialized.manifests if item.role.value == "primary")
        dependency = next(
            item for item in materialized.manifests if item.role.value == "dependency"
        )
        assert primary.dependencies[0].content_sha256 == dependency.content_sha256
        assert len(materialized.package_lock.entries) == 2


def test_builder_materializer_is_deterministic_bounded_and_workspace_private(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "campaign-workspace"
    workspace.mkdir()
    (workspace / "campaign-plan.md").write_text(_PRIVATE_SENTINEL, encoding="utf-8")
    attached = workspace / ".builder-sources" / "source-private.txt"
    attached.parent.mkdir()
    attached.write_text(_PRIVATE_SENTINEL, encoding="utf-8")
    _stage_ruleset_draft(workspace)

    with materialize_ruleset_draft(workspace) as first:
        first_bytes = first.archive.read()
        first_identity = first.manifest.identity
        first_closure = first.closure_sha256
    with materialize_ruleset_draft(workspace) as second:
        second_bytes = second.archive.read()
        assert second.manifest.identity == first_identity
        assert second.closure_sha256 == first_closure

    assert first_bytes == second_bytes
    assert _PRIVATE_SENTINEL.encode() not in first_bytes
    with zipfile.ZipFile(BytesIO(first_bytes), "r") as archive:
        assert archive.namelist() == [
            "manifest.json",
            f"packages/{_PACKAGE_ID}/content/content-declarations.json",
            f"packages/{_PACKAGE_ID}/runtime/ruleset.yaml",
        ]
    with admit_package_archive(BytesIO(first_bytes)) as admitted:
        assert admitted.format_version == 4
        assert json.loads(admitted.manifests[0].canonical_json)["content_kind"] == "ruleset"
        assert admitted.manifests[0].identity == first_identity
        assert admitted.package_lock.entries[0].package_id == _PACKAGE_ID


@pytest.mark.parametrize("failure", ("duplicate_descriptor_key", "missing_runtime"))
def test_builder_materializer_fails_closed_before_publication(
    tmp_path: Path,
    failure: str,
) -> None:
    workspace = tmp_path / failure
    workspace.mkdir()
    _stage_ruleset_draft(workspace)
    if failure == "duplicate_descriptor_key":
        descriptor = workspace / "ruleset-draft" / "package.json"
        original = descriptor.read_text(encoding="utf-8")
        descriptor.write_text(
            '{"id":"duplicate",' + original.lstrip()[1:],
            encoding="utf-8",
        )
        expected = "builder_descriptor_invalid"
    else:
        (workspace / "ruleset-draft" / "files" / "runtime" / "ruleset.yaml").unlink()
        expected = "builder_payload_invalid"

    with pytest.raises(CommandRejected) as rejected:
        materialize_ruleset_draft(workspace)
    assert rejected.value.code == expected


def test_builder_materializer_bounds_descriptor_reads(tmp_path: Path) -> None:
    workspace = tmp_path / "oversized-descriptor"
    workspace.mkdir()
    _stage_ruleset_draft(workspace)
    descriptor = workspace / "ruleset-draft" / "package.json"
    descriptor.write_bytes(b"{" + b" " * (2 * 1024 * 1024) + b"}")

    with pytest.raises(CommandRejected) as rejected:
        materialize_ruleset_draft(workspace)
    assert rejected.value.code == "builder_descriptor_invalid"


def test_builder_materializer_bounds_tree_before_sorting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "oversized-tree"
    workspace.mkdir()
    _stage_ruleset_draft(workspace)
    files = workspace / "ruleset-draft" / "files"
    for index in range(4):
        (files / f"empty-{index}").mkdir()
    monkeypatch.setattr(package_materializer, "_PAYLOAD_ENTRY_LIMIT", 3)

    with pytest.raises(CommandRejected) as rejected:
        materialize_ruleset_draft(workspace)
    assert rejected.value.code == "builder_payload_invalid"


class _RulesetBuilderProvider(Provider):
    def __init__(self, *, runtime_text: str | None = None) -> None:
        super().__init__()
        self.calls: list[tuple[list[dict[str, Any]], list[dict[str, Any]] | None]] = []
        self.runtime_text = runtime_text

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        timeout: float = 60.0,
        **kwargs: object,
    ) -> ProviderResponse:
        del stream, timeout, kwargs
        self.calls.append((messages.copy(), tools))
        call_number = len(self.calls)
        if call_number == 1:
            declarations = json.loads(
                (_FIXTURE / "content" / "content-declarations.json").read_text(encoding="utf-8")
            )
            declarations["inclusions"][0]["links"]["artifacts"].append("tests/scenarios.json")
            payloads = (
                (
                    "builder-write-descriptor",
                    "ruleset-draft/package.json",
                    (_FIXTURE / "package-record.json").read_text(encoding="utf-8"),
                ),
                (
                    "builder-write-runtime",
                    "ruleset-draft/files/runtime/ruleset.yaml",
                    self.runtime_text
                    or (_FIXTURE / "runtime" / "ruleset.yaml").read_text(encoding="utf-8"),
                ),
                (
                    "builder-write-declarations",
                    "ruleset-draft/files/content/content-declarations.json",
                    canonical_json_bytes(declarations).decode(),
                ),
                (
                    "builder-write-tests",
                    "ruleset-draft/files/tests/scenarios.json",
                    _SCENARIOS.read_text(encoding="utf-8"),
                ),
            )
            calls = [
                ToolCall(
                    id=call_id,
                    name="write_file",
                    arguments=json.dumps({"path": path, "content": content}),
                )
                for call_id, path, content in payloads
            ]
            calls.append(
                ToolCall(
                    id="builder-publish-candidate",
                    name="publish_ruleset_candidate",
                    arguments="{}",
                )
            )
            return ProviderResponse(tool_calls=calls)
        if call_number == 2:
            return ProviderResponse(content="The neutral ruleset candidate passed admission.")
        if call_number == 3:
            return ProviderResponse(
                tool_calls=[
                    ToolCall(
                        id="builder-republish-candidate",
                        name="publish_ruleset_candidate",
                        arguments="{}",
                    )
                ]
            )
        return ProviderResponse(content="The existing candidate was returned safely.")

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        del timeout, kwargs
        response = await self.generate(messages, tools)
        if response.tool_calls:
            for index, call in enumerate(response.tool_calls):
                yield ProviderStreamChunk(
                    tool_call_delta={
                        "index": index,
                        "id": call.id,
                        "function": {"name": call.name, "arguments": call.arguments},
                    },
                    finish_reason=("tool_calls" if index == len(response.tool_calls) - 1 else None),
                )
            return
        yield ProviderStreamChunk(
            content=response.content or "",
            finish_reason=response.finish_reason,
        )

    async def close(self) -> None:
        return None


class _RulesetBuilderPublishThenBlockProvider(_RulesetBuilderProvider):
    def __init__(self) -> None:
        super().__init__()
        self.second_round_started = threading.Event()
        self.cancelled = threading.Event()
        self.release = threading.Event()
        self.tool_results: list[dict] = []

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        if self.calls:
            self.tool_results = [
                json.loads(message["content"]) for message in messages
                if message.get("role") == "tool"
            ]
            self.second_round_started.set()
            try:
                while not self.release.is_set():
                    await asyncio.sleep(0.01)
                yield ProviderStreamChunk(content="CANCELLED TALK MUST NOT PERSIST")
            finally:
                self.cancelled.set()
            return
        async for chunk in super().generate_stream(
            messages,
            tools,
            timeout=timeout,
            **kwargs,
        ):
            yield chunk


class _MissingCapabilityProvider(Provider):
    def __init__(self, requirement: dict[str, str]) -> None:
        super().__init__()
        self.requirement = requirement
        self.calls: list[tuple[list[dict[str, Any]], list[dict[str, Any]] | None]] = []

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        timeout: float = 60.0,
        **kwargs: object,
    ) -> ProviderResponse:
        del stream, timeout, kwargs
        self.calls.append((messages.copy(), tools))
        return ProviderResponse(
            content=json.dumps(
                {
                    "mechanic_id": self.requirement["mechanic_id"],
                    "requirement": self.requirement["requirement"],
                    "status": self.requirement["expected_status"],
                    "implemented": False,
                    "publication": "not_performed",
                },
                sort_keys=True,
            )
        )

    async def close(self) -> None:
        return None

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        response = await self.generate(messages, tools, timeout=timeout, **kwargs)
        yield ProviderStreamChunk(content=response.content or "", finish_reason="stop")


class _Resolver:
    def __init__(self, provider: Provider) -> None:
        self.provider = provider

    def __call__(self, _session: object) -> Provider:
        return self.provider

    def model_choices(self) -> tuple[dict[str, object], ...]:
        return (
            {
                "provider_id": "test.builder",
                "provider_name": "Builder Test",
                "model_id": "test.builder/model",
                "model_name": "Builder Model",
                "context_window": 16_384,
                "capability_registry_version": "sylvia.capability.registry.v1",
                "capability_tier_id": None,
                "capability_evidence_sha256": None,
            },
        )


def test_ruleset_publication_tool_is_injected_only_by_the_canonical_builder_session(
    tmp_path: Path,
) -> None:
    provider = _RulesetBuilderProvider()
    context = SessionContext(
        session=SessionRecord(
            session_id="session-ruleset-tool",
            campaign_id="campaign-ruleset-tool",
            role=WriterRole.BUILDER,
            provider_id="test.builder",
            model_id="test.builder/model",
            selected_skill="BUILD RULESET",
        ),
        messages=(),
    )
    published: list[str] = []

    def publish(
        _archive: object,
        *,
        install_id: str,
        expected_closure_sha256: str,
    ) -> dict[str, object]:
        published.append(expected_closure_sha256)
        return {
            "candidate_id": f"candidate-{install_id}",
            "status": "validated",
            "closure_sha256": expected_closure_sha256,
            "packages": [],
            "validation_report": {"code": "validated"},
            "activation": "not_performed",
        }

    result = run_builder_turn(
        provider,
        context=context,
        message_id="message-ruleset-tool",
        user_text="Build the ruleset.",
        workspace=tmp_path,
        control=ProviderControl(),
        publish_ruleset_candidate=publish,  # type: ignore[arg-type]
    )

    assert result.tool_calls_made[-1] == "publish_ruleset_candidate"
    assert len(published) == 1
    publication = next(
        message.content["result"]
        for message in result.messages
        if message.content.get("tool_name") == "publish_ruleset_candidate"
    )
    artifact_path = publication["artifact_path"]
    assert artifact_path == (f"ruleset-draft/output/candidate-{published[0]}.sylvia-gm-package")
    artifact = tmp_path / artifact_path
    assert artifact.is_file()
    with artifact.open("rb") as archive, admit_package_archive(archive) as admitted:
        assert closure_sha256(admitted.package_lock) == published[0]


def test_builder_reports_missing_engine_capability_without_inventing_or_publishing(
    tmp_path: Path,
) -> None:
    requirement = json.loads(
        (_FIXTURE.parent / "missing-capability" / "request.json").read_text(encoding="utf-8")
    )
    provider = _MissingCapabilityProvider(requirement)
    context = SessionContext(
        session=SessionRecord(
            session_id="session-missing-capability",
            campaign_id="campaign-missing-capability",
            role=WriterRole.BUILDER,
            provider_id="test.builder",
            model_id="test.builder/model",
            selected_skill="BUILD RULESET",
        ),
        messages=(),
    )
    published = False

    def publish(
        _archive: object,
        *,
        install_id: str,
        expected_closure_sha256: str,
    ) -> dict[str, object]:
        del install_id, expected_closure_sha256
        nonlocal published
        published = True
        return {}

    result = run_builder_turn(
        provider,
        context=context,
        message_id="message-missing-capability",
        user_text="Build this ruleset without changing its continuous-surface mechanic.",
        workspace=tmp_path,
        control=ProviderControl(),
        publish_ruleset_candidate=publish,  # type: ignore[arg-type]
    )

    assert result.tool_calls_made == ()
    assert published is False
    assert not (tmp_path / "ruleset-draft").exists()
    report = json.loads(result.messages[-1].content["text"])
    assert report == {
        "implemented": False,
        "mechanic_id": requirement["mechanic_id"],
        "publication": "not_performed",
        "requirement": requirement["requirement"],
        "status": "missing_engine_capability",
    }
    assert "missing_engine_capability" in str(provider.calls[0][0][0]["content"])


def test_owner_publication_callback_returns_same_exact_candidate_on_replay(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _stage_ruleset_draft(workspace)
    with AuthorityOwner.open(tmp_path / "callback.sqlite3", "owner-callback") as owner:
        owner.create_campaign(
            CampaignCreate(
                campaign_id="campaign-callback",
                display_name="Callback",
                writer=WriterRole.BUILDER,
                package_lock=PackageLock(entries=()),
                turn_id="turn-callback",
            )
        )
        owner.create_agent_session(
            campaign_id="campaign-callback",
            role=WriterRole.BUILDER,
            provider_id="test.builder",
            model_id="test.builder/model",
            session_id="session-callback",
            selected_skill="BUILD RULESET",
        )
        with materialize_ruleset_draft(workspace) as materialized:
            first = _publish_builder_ruleset_candidate(
                owner,
                "session-callback",
                materialized.archive,
                install_id="builder-callback",
                expected_closure_sha256=materialized.closure_sha256,
            )
        with materialize_ruleset_draft(workspace) as materialized:
            replay = _publish_builder_ruleset_candidate(
                owner,
                "session-callback",
                materialized.archive,
                install_id="builder-callback",
                expected_closure_sha256=materialized.closure_sha256,
            )
        assert replay == first
        assert len(owner.package_candidates()) == 1


def test_owner_dispatch_composes_ruleset_builder_tool_without_test_bypass(
    tmp_path: Path,
) -> None:
    provider = _RulesetBuilderProvider()
    with AuthorityOwner.open(tmp_path / "dispatch.sqlite3", "owner-dispatch") as owner:
        confirmation = owner.confirm_new_campaign(
            operation_id="confirm-dispatch",
            display_name="Dispatch",
            package_lock=PackageLock(entries=()),
            turn_id="turn-dispatch",
            builder_session_id="session-dispatch",
            builder_provider_id="test.builder",
            builder_model_id="test.builder/model",
            builder_selected_skill="BUILD RULESET",
        )
        result = _dispatch(
            None,  # type: ignore[arg-type]
            "submit_session_input",
            {
                "session_id": "session-dispatch",
                "turn_id": "turn-dispatch-submit",
                "attempt_id": "attempt-dispatch-submit",
                "message_id": "message-dispatch-submit",
                "user_text": "Build the ruleset.",
            },
            application_owner=owner,
            owner_id="owner-dispatch",
            provider_resolver=_Resolver(provider),
            model_choices=lambda: (),
            session_loop=CampaignSessionLoop(owner),
            storage_root=tmp_path,
        )
        projected = _json_value(result)
        assert projected["tool_calls_made"][-1] == "publish_ruleset_candidate"
        assert owner.snapshot(confirmation.campaign_id).revision == 0


def test_owner_dispatch_rejects_invalid_builder_runtime_without_candidate_or_mutation(
    tmp_path: Path,
) -> None:
    invalid_runtime = (
        (_FIXTURE / "runtime" / "ruleset.yaml")
        .read_text(encoding="utf-8")
        .replace("call: commit_program", "call: missing_program")
    )
    provider = _RulesetBuilderProvider(runtime_text=invalid_runtime)
    with AuthorityOwner.open(tmp_path / "rejected.sqlite3", "owner-rejected") as owner:
        confirmation = owner.confirm_new_campaign(
            operation_id="confirm-rejected",
            display_name="Rejected",
            package_lock=PackageLock(entries=()),
            turn_id="turn-rejected",
            builder_session_id="session-rejected",
            builder_provider_id="test.builder",
            builder_model_id="test.builder/model",
            builder_selected_skill="BUILD RULESET",
        )
        result = _dispatch(
            None,  # type: ignore[arg-type]
            "submit_session_input",
            {
                "session_id": "session-rejected",
                "turn_id": "turn-rejected-submit",
                "attempt_id": "attempt-rejected-submit",
                "message_id": "message-rejected-submit",
                "user_text": "Validate this invalid runtime.",
            },
            application_owner=owner,
            owner_id="owner-rejected",
            provider_resolver=_Resolver(provider),
            model_choices=lambda: (),
            session_loop=CampaignSessionLoop(owner),
            storage_root=tmp_path,
        )
        projected = _json_value(result)
        publication = next(
            message
            for message in projected["messages"]
            if message["content"].get("tool_name") == "publish_ruleset_candidate"
        )
        rejection = publication["content"]["result"]
        assert rejection["success"] is False
        assert rejection["status"] == "rejected"
        assert rejection["code"] == "package_runtime_invalid"
        assert rejection["validation_errors"][0]["file"] == "runtime/ruleset.yaml"
        assert "missing_program" in rejection["validation_errors"][0]["message"]
        assert owner.package_candidates() == ()
        snapshot = owner.snapshot(confirmation.campaign_id)
        assert snapshot.revision == 0
        assert snapshot.package_lock.entries == ()


def test_campaign_confirmation_rejects_unknown_builder_skill_atomically(tmp_path: Path) -> None:
    with AuthorityOwner.open(tmp_path / "skill.sqlite3", "owner-skill") as owner:
        with pytest.raises(CommandRejected, match="authority is invalid"):
            owner.confirm_new_campaign(
                operation_id="confirm-invalid-skill",
                display_name="Invalid Skill",
                package_lock=PackageLock(entries=()),
                turn_id="turn-invalid-skill",
                builder_session_id="session-invalid-skill",
                builder_provider_id="test.builder",
                builder_model_id="test.builder/model",
                builder_selected_skill="BUILD EVERYTHING",
            )
        assert owner.campaign_index() == ()


def test_published_candidate_and_tool_evidence_survive_later_stream_cancellation(
    tmp_path: Path,
) -> None:
    provider = _RulesetBuilderPublishThenBlockProvider()
    harness = _OwnerHarness(tmp_path / "cancel-after-publish.sqlite3", provider)
    try:
        confirmation = harness.request(
            "confirm-cancel-after-publish",
            "confirm_new_campaign",
            {
                "operation_id": "confirm-cancel-after-publish",
                "display_name": "Cancel After Publish",
                "candidate_id": None,
                "turn_id": "turn-cancel-after-publish",
                "provider_id": "test.builder",
                "model_id": "test.builder/model",
                "builder_skill": "BUILD RULESET",
            },
        )
        session_id = confirmation["builder_session_id"]
        harness._send(
            {
                "id": "submit-cancel-after-publish",
                "method": "submit_session_input",
                "params": {
                    "session_id": session_id,
                    "turn_id": "turn-submit-cancel-after-publish",
                    "attempt_id": "attempt-cancel-after-publish",
                    "message_id": "message-cancel-after-publish",
                    "user_text": "Publish the ruleset, then wait.",
                },
            }
        )
        # Publication now runs the saved scenarios before the provider's next round.
        assert provider.second_round_started.wait(30)

        candidates = harness.request("candidate-before-cancel", "package_candidates", {})
        builder_candidates = [
            candidate
            for candidate in candidates
            if any(
                entry["package_id"] == _PACKAGE_ID for entry in candidate["package_lock"]["entries"]
            )
        ]
        assert len(builder_candidates) == 1, provider.tool_results
        publication: dict[str, Any] | None = None
        for poll in range(50):
            before = harness.request(
                f"context-before-cancel-{poll}",
                "session_context",
                {"session_id": session_id},
            )
            publication = next(
                (
                    message
                    for message in before["messages"]
                    if message["kind"] == "evidence"
                    and message["content"].get("tool_name") == "publish_ruleset_candidate"
                ),
                None,
            )
            if publication is not None:
                break
            provider.release.wait(0.02)
        assert publication is not None
        assert publication["content"]["result"]["success"] is True

        interrupted = harness.request(
            "interrupt-after-publish",
            "interrupt_provider_stream",
            {
                "campaign_id": session_id,
                "attempt_id": "attempt-cancel-after-publish",
            },
        )
        assert interrupted == {"status": "interrupted"}
        submit = harness._read()
        assert submit["id"] == "submit-cancel-after-publish"
        assert submit["ok"] is False
        assert provider.cancelled.wait(2)

        after = harness.request(
            "context-after-cancel",
            "session_context",
            {"session_id": session_id},
        )
        assert publication in after["messages"]
        assert "CANCELLED TALK MUST NOT PERSIST" not in json.dumps(after)
        assert after["session"]["status"] == "active"
        after_candidates = harness.request("candidate-after-cancel", "package_candidates", {})
        assert (
            sum(
                any(
                    entry["package_id"] == _PACKAGE_ID
                    for entry in candidate["package_lock"]["entries"]
                )
                for candidate in after_candidates
            )
            == 1
        )
    finally:
        provider.release.set()
        harness.close()


class _OwnerHarness:
    def __init__(self, database: Path, provider: Provider) -> None:
        self.storage_root = database.parent
        owner_read, client_write = os.pipe()
        client_read, owner_write = os.pipe()
        self._owner_input = os.fdopen(owner_read, "r", encoding="utf-8", newline="\n")
        self._client_output = os.fdopen(
            client_write, "w", encoding="utf-8", newline="\n", buffering=1
        )
        self._client_input = os.fdopen(client_read, "r", encoding="utf-8", newline="\n")
        self._owner_output = os.fdopen(
            owner_write, "w", encoding="utf-8", newline="\n", buffering=1
        )
        resolver = _Resolver(provider)
        self._thread = threading.Thread(
            target=run,
            args=(self._owner_input, self._owner_output),
            kwargs={"provider_resolver": resolver},
            daemon=True,
        )
        self._thread.start()
        handshake = LaunchHandshake()
        credential = handshake.issue()
        self._send(
            {
                "type": "launch",
                "secret_hex": credential.secret_hex,
                "database_path": str(database),
                "owner_id": "owner-builder-journey",
            }
        )
        ready = self._read()
        assert ready["ok"] is True
        handshake.verify(ready["response_hex"])

    def _send(self, value: dict[str, object]) -> None:
        self._client_output.write(json.dumps(value, separators=(",", ":")) + "\n")
        self._client_output.flush()

    def _read(self) -> dict[str, Any]:
        line = self._client_input.readline()
        assert line
        value = json.loads(line)
        assert isinstance(value, dict)
        return value

    def request(self, request_id: str, method: str, params: dict[str, object]) -> Any:
        self._send({"id": request_id, "method": method, "params": params})
        response = self._read()
        assert response["id"] == request_id
        assert response["ok"] is True, response
        return response["result"]

    def close(self) -> None:
        if self._thread.is_alive():
            self._send({"id": "shutdown", "method": "shutdown", "params": {}})
            response = self._read()
            assert response["id"] == "shutdown"
        self._thread.join(timeout=10)
        assert not self._thread.is_alive()
        for stream in (
            self._client_output,
            self._client_input,
            self._owner_input,
            self._owner_output,
        ):
            stream.close()


def _action(owner_id: str, *, revision: int) -> ActionCommand:
    return ActionCommand(
        campaign_id="campaign-builder-validation",
        action_id="action-builder-validation",
        idempotency_key="idempotency-builder-validation",
        owner_id=owner_id,
        writer=WriterRole.DM,
        expected_revision=revision,
        turn_id="turn-builder-validation",
        package_id=_PACKAGE_ID,
        tool_name="apply_operation_cost",
        parameters={"stress": 2, "wounds": 1, "charge_spent": 2, "progress": 3},
    )


def test_ordinary_builder_publishes_candidate_then_neutral_runtime_plays_and_reopens(
    tmp_path: Path,
) -> None:
    database = tmp_path / "authority.sqlite3"
    provider = _RulesetBuilderProvider()
    harness = _OwnerHarness(database, provider)
    try:
        confirmation = harness.request(
            "confirm-builder-ruleset",
            "confirm_new_campaign",
            {
                "operation_id": "confirm-builder-ruleset",
                "display_name": "Ruleset Builder",
                "candidate_id": None,
                "turn_id": "turn-builder-ruleset",
                "provider_id": "test.builder",
                "model_id": "test.builder/model",
                "builder_skill": "BUILD RULESET",
            },
        )
        campaign_id = confirmation["campaign_id"]
        session_id = confirmation["builder_session_id"]
        workspace = tmp_path / "campaigns" / campaign_id
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "campaign-plan.md").write_text(_PRIVATE_SENTINEL, encoding="utf-8")
        before = harness.request("snapshot-before", "snapshot", {"campaign_id": campaign_id})
        context = harness.request("context-before", "session_context", {"session_id": session_id})
        assert context["session"]["selected_skill"] == "BUILD RULESET"

        first = harness.request(
            "builder-publish",
            "submit_session_input",
            {
                "session_id": session_id,
                "turn_id": "turn-builder-publish",
                "attempt_id": "attempt-builder-publish",
                "message_id": "message-builder-publish",
                "user_text": "Build and validate the ability-free ruleset draft.",
            },
        )
        assert first["tool_calls_made"] == [
            "write_file",
            "write_file",
            "write_file",
            "write_file",
            "publish_ruleset_candidate",
        ]
        candidates = harness.request("candidates-first", "package_candidates", {})
        candidate = next(
            item
            for item in candidates
            if any(entry["package_id"] == _PACKAGE_ID for entry in item["package_lock"]["entries"])
        )
        assert candidate["status"] == "validated"
        assert candidate["package_lock"]["entries"][0]["package_id"] == _PACKAGE_ID

        replay = harness.request(
            "builder-republish",
            "submit_session_input",
            {
                "session_id": session_id,
                "turn_id": "turn-builder-republish",
                "attempt_id": "attempt-builder-republish",
                "message_id": "message-builder-republish",
                "user_text": "Recheck the exact same candidate after reconnect.",
            },
        )
        assert replay["tool_calls_made"] == ["publish_ruleset_candidate"]
        replay_candidates = harness.request("candidates-replay", "package_candidates", {})
        assert (
            sum(
                any(entry["package_id"] == _PACKAGE_ID for entry in item["package_lock"]["entries"])
                for item in replay_candidates
            )
            == 1
        )
        after = harness.request("snapshot-after", "snapshot", {"campaign_id": campaign_id})
        assert after["revision"] == before["revision"] == 0
        assert after["package_lock"] == before["package_lock"]
        assert after["state"] == before["state"]

        resumed = harness.request("context-after", "session_context", {"session_id": session_id})
        serialized = json.dumps(resumed, sort_keys=True)
        assert str(tmp_path) not in serialized
        assert _PRIVATE_SENTINEL not in serialized
        evidence = [
            message
            for message in resumed["messages"]
            if message["kind"] == "evidence"
            and message["content"].get("tool_name") == "publish_ruleset_candidate"
        ]
        assert len(evidence) == 2
        assert all(item["content"]["result"]["success"] is True for item in evidence)
        artifact_paths = {item["content"]["result"]["artifact_path"] for item in evidence}
        assert artifact_paths == {
            f"ruleset-draft/output/candidate-{candidate['closure_sha256']}.sylvia-gm-package"
        }
        artifact = workspace / artifact_paths.pop()
        with artifact.open("rb") as archive, admit_package_archive(archive) as admitted:
            assert closure_sha256(admitted.package_lock) == candidate["closure_sha256"]

        advertised = {tool["function"]["name"] for tool in (provider.calls[0][1] or [])}
        assert "publish_ruleset_candidate" in advertised
        system_prompt = str(provider.calls[0][0][0]["content"])
        assert "campaign-plan.md" not in system_prompt
        assert "D&D" not in system_prompt
        assert "Cairn" not in system_prompt
        assert str(tmp_path) not in json.dumps(provider.calls, sort_keys=True, default=str)
    finally:
        harness.close()

    database_bytes = database.read_bytes()
    assert _PRIVATE_SENTINEL.encode() not in database_bytes

    with AuthorityOwner.open(database, "owner-builder-validation") as owner:
        durable_candidate = next(
            candidate
            for candidate in owner.package_candidates()
            if any(entry.package_id == _PACKAGE_ID for entry in candidate.package_lock.entries)
        )
        owner.create_campaign(
            CampaignCreate(
                campaign_id="campaign-builder-validation",
                display_name="Builder Candidate Validation",
                writer=WriterRole.DM,
                package_lock=durable_candidate.package_lock,
                turn_id="turn-builder-validation",
                initial_state=(
                    StateValue(
                        _PACKAGE_ID,
                        "state",
                        {
                            "stress": 0,
                            "wounds": 0,
                            "resources": {"charge": 5},
                            "conditions": {"interference": False},
                            "advancement": {"intrusion": 0},
                            "last_check": {"skill": None, "total": 0, "outcome": None},
                            "last_program": {"name": None, "result": 0},
                        },
                    ),
                ),
            )
        )
        assert tuple(tool.name for tool in owner.catalog("campaign-builder-validation")) == (
            "apply_operation_cost",
            "resolve_intrusion",
            "run_program",
        )
        command = _action("owner-builder-validation", revision=0)
        committed = owner.execute_action(command)
        replayed = owner.execute_action(command)
        assert committed.status is ActionStatus.COMMITTED
        assert replayed.resulting_revision == committed.resulting_revision == 1

    with AuthorityOwner.open(database, "owner-builder-validation-reopen") as reopened:
        state = reopened.snapshot("campaign-builder-validation").state[f"{_PACKAGE_ID}.state"]
        assert state["stress"] == 2
        assert state["wounds"] == 1
        assert state["resources"]["charge"] == 3
        assert state["conditions"]["interference"] is True
        assert state["advancement"]["intrusion"] == 3

    static_tools = {item["function"]["name"] for item in BUILDER_TOOL_DEFINITIONS}
    assert RULESET_CANDIDATE_TOOL_DEFINITION["function"]["name"] not in static_tools
    print(
        "F6G BUILDER PACKAGE",
        candidate["candidate_id"],
        "origin revision=0",
        "validation revision=1",
        "replay=stable",
    )
