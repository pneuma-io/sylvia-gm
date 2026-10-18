import json
from pathlib import Path

import pytest

from sylvia_gm.agent._builder_session import (
    BuilderMessage,
    _builder_prompt,
    _provider_history,
    _streamed_builder_messages,
    run_builder_turn,
    stream_builder_turn,
)
from sylvia_gm.agent._session_loop import ProviderControl, ProviderEventKind
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import PackageLock
from sylvia_gm.domain.errors import CommandRejected, IdempotencyConflict, StaleRevision
from sylvia_gm.domain.session import SessionMessageKind
from sylvia_gm.provider._anthropic import AnthropicMessagesTransport
from sylvia_gm.provider._base import FakeProvider, ProviderResponse, ProviderStreamChunk, ToolCall


def test_delete_builder_chat_is_exact_independent_and_durable(tmp_path: Path) -> None:
    database = tmp_path / "delete-builder.sqlite3"
    with AuthorityOwner.open(database, "owner-delete-builder") as owner:
        campaign = owner.create_guided_campaign_draft(
            operation_id="keep-campaign",
            display_name="Kept campaign",
            builder_session_id="campaign-interview",
            builder_provider_id="test.provider",
            builder_model_id="test.model",
        )
        owner.create_builder_chat(
            provider_id="test.provider", model_id="test.model", session_id="delete-me"
        )
        owner.create_builder_chat(
            provider_id="test.provider", model_id="test.model", session_id="keep-me"
        )
        owner.append_agent_message(
            session_id="delete-me",
            message_id="unfinished-message",
            kind=SessionMessageKind.USER,
            content={"text": "Synthetic unfinished work"},
        )
        with pytest.raises(CommandRejected, match="confirmation is not exact"):
            owner.delete_builder_chat("delete-me", confirmation="delete-me")
        with pytest.raises(CommandRejected, match="Builder chat is unavailable"):
            owner.delete_builder_chat(
                campaign.builder_session_id,
                confirmation=f"permanently delete {campaign.builder_session_id}",
            )
        deleted = owner.delete_builder_chat(
            "delete-me", confirmation="permanently delete delete-me"
        )
        assert deleted.session.session_id == "delete-me"
        assert [chat.session.session_id for chat in owner.builder_chat_index()] == ["keep-me"]
        assert owner.session_context("keep-me").session.session_id == "keep-me"
        assert owner.session_context(campaign.builder_session_id).session.campaign_id == (
            campaign.campaign_id
        )

    with AuthorityOwner.open(database, "owner-delete-builder-restarted") as owner:
        assert [chat.session.session_id for chat in owner.builder_chat_index()] == ["keep-me"]
        with pytest.raises(CommandRejected):
            owner.session_context("delete-me")
        assert owner.session_context(campaign.builder_session_id).session.campaign_id == (
            campaign.campaign_id
        )


def test_builder_delete_rejects_windows_alias_without_touching_another_workspace(tmp_path):
    """A trailing dot can alias a different Windows directory, despite a distinct SQLite ID."""
    with AuthorityOwner.open(tmp_path / "alias.db", "alias-owner") as owner:
        for session_id in ("ordinary", "ordinary."):
            owner.create_builder_chat(provider_id="test", model_id="test", session_id=session_id)
        workspace = tmp_path / "builder-chats" / "ordinary"
        workspace.mkdir(parents=True)
        retained = workspace / "draft.md"
        retained.write_text("Keep this other chat's draft", encoding="utf-8")
        with pytest.raises(CommandRejected, match="directory name"):
            owner.delete_builder_chat("ordinary.", confirmation="permanently delete ordinary.")
        assert retained.read_text(encoding="utf-8") == "Keep this other chat's draft"
        assert len(owner.builder_chat_index()) == 2


def test_builder_launch_preserves_interview_and_survives_campaign_purge(tmp_path: Path) -> None:
    database = tmp_path / "saved-builders.sqlite3"
    with AuthorityOwner.open(database, "owner-saved-builders") as owner:
        draft = owner.create_guided_campaign_draft(
            operation_id="create-interview",
            display_name="Original campaign",
            builder_session_id="session-interview",
            builder_provider_id="test.provider",
            builder_model_id="test.model",
        )
        owner.advance_guided_campaign_draft(
            operation_id="choose-original-ruleset",
            campaign_id=draft.campaign_id,
            builder_session_id=draft.builder_session_id,
            expected_construction_revision=0,
            current_step="ruleset",
            visible_answer="No Ruleset",
            answer={"candidate_id": None},
        )
        original_interview = owner.session_context(draft.builder_session_id)
        request = {
            "operation_id": "launch-setting",
            "campaign_id": draft.campaign_id,
            "parent_session_id": draft.builder_session_id,
            "expected_construction_revision": 1,
            "purpose": "setting",
            "visible_answer": "New Campaign Setting",
            "child_session_id": "session-new-setting",
        }
        launched = owner.start_interview_builder_chat(**request)
        assert launched.status == "independent"
        assert launched.construction_revision == 1
        assert owner.session_context(draft.builder_session_id) == original_interview
        child = owner.session_context(launched.child_session_id)
        assert child.session.campaign_id is None
        assert child.session.parent_session_id is None
        assert child.messages[0].content["selections"]["ruleset"]["text"] == "No Ruleset"
        assert all(message.kind is not SessionMessageKind.USER for message in child.messages)
        assert owner.builder_chat_index()[0].display_name == "New setting · No Ruleset"

        owner.advance_guided_campaign_draft(
            operation_id="change-ruleset-while-builder-is-open",
            campaign_id=draft.campaign_id,
            builder_session_id=draft.builder_session_id,
            expected_construction_revision=1,
            current_step="ruleset",
            visible_answer="A different Ruleset",
            answer={"candidate_id": "different-ruleset"},
        )
        assert owner.session_context(launched.child_session_id) == child
        assert owner.start_interview_builder_chat(**request) == launched
        assert len(owner.builder_chat_index()) == 1
        with pytest.raises(IdempotencyConflict):
            owner.start_interview_builder_chat(**{**request, "visible_answer": "Changed request"})
        with pytest.raises(StaleRevision):
            owner.start_interview_builder_chat(
                **{**request, "operation_id": "stale-launch", "child_session_id": "stale-chat"}
            )
        assert len(owner.builder_chat_index()) == 1

        # Complete the originating interview through the existing campaign binding path.
        remaining = (
            ("ruleset", "No Ruleset", {"candidate_id": None}),
            ("setting", "A quiet town", {"setting_id": "quiet-town"}),
            ("plan", "A town mystery", {"plan_id": "town-mystery"}),
            ("name", "Original campaign", {"display_name": "Original campaign"}),
            ("participants", "One investigator", {"player_name": "Investigator"}),
            ("boundaries_preferences", "No additions", {"boundaries": "", "preferences": ""}),
            ("specific_requests", "No additional requests", {"no_additional_requests": True}),
        )
        for revision, (step, text, value) in enumerate(remaining, start=2):
            owner.advance_guided_campaign_draft(
                operation_id=f"finish-{step}",
                campaign_id=draft.campaign_id,
                builder_session_id=draft.builder_session_id,
                expected_construction_revision=revision,
                current_step=step,
                visible_answer=text,
                answer=value,
            )
        owner.append_agent_message(
            session_id=draft.builder_session_id,
            message_id="ready-to-start",
            kind=SessionMessageKind.USER,
            content={
                "text": "Ready to Begin",
                "interview_step": "ready",
                "interview_value": {"action": "ready_to_begin"},
            },
        )
        owner.bind_guided_campaign_basis(
            operation_id="bind-original-campaign",
            campaign_id=draft.campaign_id,
            display_name="Original campaign",
            package_lock=PackageLock(entries=()),
            turn_id="original-construction",
            builder_session_id=draft.builder_session_id,
        )
        owner.trash_campaign(
            operation_id="trash-origin",
            campaign_id=draft.campaign_id,
            expected_revision=owner.snapshot(draft.campaign_id).revision,
            designated_session_id=draft.builder_session_id,
        )
        owner.purge_campaign(
            operation_id="purge-origin",
            campaign_id=draft.campaign_id,
            expected_revision=owner.snapshot(draft.campaign_id).revision,
            confirmation=f"permanently purge {draft.campaign_id}",
        )
        assert owner.session_context(launched.child_session_id) == child
        owner.append_agent_message(
            session_id=launched.child_session_id,
            message_id="continue-after-origin-deletion",
            kind=SessionMessageKind.USER,
            content={"text": "Continue building this setting."},
        )

    with AuthorityOwner.open(database, "owner-reopened-builders") as reopened:
        restored = reopened.session_context("session-new-setting")
        assert restored.messages[0] == child.messages[0]
        assert restored.messages[-1].content["text"] == "Continue building this setting."
        assert restored.session.status.value == "active"
        history = _provider_history(restored)
        assert history[0]["role"] == "system"
        assert "No Ruleset" in history[0]["content"]
        assert [item["content"] for item in history if item["role"] == "user"] == [
            "Continue building this setting."
        ]


@pytest.mark.parametrize(
    ("path", "exists"),
    (("reference.txt", True), ("missing.txt", False)),
)
@pytest.mark.parametrize("streaming", [False, True])
def test_saved_builder_tool_pair_survives_reopen_as_native_provider_history(
    tmp_path: Path,
    path: str,
    exists: bool,
    streaming: bool,
) -> None:
    """A resumed Builder retains the exact call that produced durable tool evidence."""
    database = tmp_path / "tool-history.sqlite3"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    if exists:
        (workspace / path).write_text("Transient reference body", encoding="utf-8")
    arguments = f'{{ "path": "{path}" }}'

    class StreamingProvider(FakeProvider):
        async def generate_stream(self, messages, tools=None, **kwargs):
            response = await self.generate(messages, tools, **kwargs)
            yield ProviderStreamChunk(reasoning_content="Private synthetic reasoning")
            for index, call in enumerate(response.tool_calls):
                yield ProviderStreamChunk(
                    tool_call_delta={
                        "index": index,
                        "id": call.id,
                        "function": {"name": call.name, "arguments": call.arguments},
                    }
                )
            yield ProviderStreamChunk(content=response.content or "", finish_reason="stop")

    with AuthorityOwner.open(database, "owner-first-turn") as owner:
        owner.create_builder_chat(
            provider_id="test.provider", model_id="test.model", session_id="saved-builder"
        )
        owner.append_agent_message(
            session_id="saved-builder",
            message_id="first-user",
            kind=SessionMessageKind.USER,
            content={"text": f"Read {path}."},
        )
        provider = StreamingProvider(
            responses=[
                ProviderResponse(
                    tool_calls=[ToolCall(id="read-call-1", name="read_file", arguments=arguments)]
                ),
                ProviderResponse(content="The read attempt is complete."),
            ]
        )
        turn_kwargs = {
            "context": owner.session_context("saved-builder"),
            "message_id": "first-user",
            "user_text": f"Read {path}.",
            "workspace": workspace,
            "control": ProviderControl(),
        }
        if streaming:
            events = list(stream_builder_turn(provider, **turn_kwargs, complete=lambda _: None))
            prepared = tuple(
                BuilderMessage(event.message_id, SessionMessageKind.EVIDENCE, event.evidence)
                for event in events
                if event.kind is ProviderEventKind.EVIDENCE
            )
        else:
            prepared = run_builder_turn(provider, **turn_kwargs).messages
        for message in prepared:
            owner.append_agent_message(
                session_id="saved-builder",
                message_id=message.message_id,
                kind=message.kind,
                content=message.content,
            )
        evidence = next(
            message for message in prepared if message.kind is SessionMessageKind.EVIDENCE
        )
        assert evidence.content["tool_name"] == "read_file"
        assert evidence.content["tool_call_id"] == "read-call-1"
        assert evidence.content["arguments"] == arguments
        assert evidence.content["result"]["success"] is exists
        if exists:
            assert "content" not in evidence.content["result"]
            assert evidence.content["result"]["content_omitted_from_history"] is True
            assert evidence.content["result"]["content_sha256"]

    with AuthorityOwner.open(database, "owner-reopened-turn") as owner:
        owner.append_agent_message(
            session_id="saved-builder",
            message_id="second-user",
            kind=SessionMessageKind.USER,
            content={"text": "Continue from that result without repeating the read."},
        )
        resumed_provider = FakeProvider(
            responses=[ProviderResponse(content="Continuing from the saved result.")]
        )
        resumed = run_builder_turn(
            resumed_provider,
            context=owner.session_context("saved-builder"),
            message_id="second-user",
            user_text="Continue from that result without repeating the read.",
            workspace=workspace,
            control=ProviderControl(),
        )

    sent = resumed_provider.calls[0]["messages"]
    assert "Private synthetic reasoning" not in json.dumps(sent)
    saved_call = next(
        call
        for message in sent
        for call in message.get("tool_calls", [])
        if call.get("id") == "read-call-1"
    )
    assert saved_call["function"] == {"name": "read_file", "arguments": arguments}
    saved_result = next(message for message in sent if message.get("tool_call_id") == "read-call-1")
    assert saved_result["role"] == "tool"
    assert bool(json.loads(saved_result["content"])["success"]) is exists
    request = AnthropicMessagesTransport().format_request("test.model", sent, None, False)
    tool_use = next(
        block
        for message in request["messages"]
        if message["role"] == "assistant"
        for block in message["content"]
        if block["type"] == "tool_use" and block["id"] == "read-call-1"
    )
    tool_result = next(
        block
        for message in request["messages"]
        if message["role"] == "user" and isinstance(message["content"], list)
        for block in message["content"]
        if block["type"] == "tool_result" and block["tool_use_id"] == "read-call-1"
    )
    assert tool_use == {
        "type": "tool_use",
        "id": "read-call-1",
        "name": "read_file",
        "input": {"path": path},
    }
    assert tool_result["content"] == saved_result["content"]
    assert resumed.tool_calls_made == ()


def test_streamed_tool_result_keeps_call_from_an_earlier_batch() -> None:
    """A slow second tool can finish after its assistant call was already consumed."""
    history = [
        {
            "role": "assistant",
            "tool_calls": [
                {"id": "first", "function": {"name": "read_file", "arguments": '{"path":"a"}'}},
                {"id": "second", "function": {"name": "read_file", "arguments": '{"path":"b"}'}},
            ],
        },
        {"role": "tool", "tool_call_id": "first", "content": '{"success":false}'},
        {"role": "tool", "tool_call_id": "second", "content": '{"success":false}'},
    ]
    messages = _streamed_builder_messages(
        "turn",
        history[2:],
        history_offset=2,
        call_history=history,
    )
    assert len(messages) == 1
    assert messages[0].content["tool_name"] == "read_file"
    assert messages[0].content["arguments"] == '{"path":"b"}'


def test_legacy_builder_tool_evidence_without_arguments_remains_application_data(
    tmp_path: Path,
) -> None:
    """Legacy evidence cannot truthfully be reconstructed as a native tool call."""
    with AuthorityOwner.open(tmp_path / "legacy.sqlite3", "owner-legacy") as owner:
        owner.create_builder_chat(
            provider_id="test.provider", model_id="test.model", session_id="legacy-builder"
        )
        owner.append_agent_message(
            session_id="legacy-builder",
            message_id="legacy-tool-result",
            kind=SessionMessageKind.EVIDENCE,
            content={
                "tool_name": "read_file",
                "tool_call_id": "legacy-read-1",
                "result": {"success": True, "content_omitted_from_history": True},
            },
        )
        history = _provider_history(owner.session_context("legacy-builder"))

    assert [item["role"] for item in history] == ["system"]
    assert "SYLVIA_GM_APPLICATION_DATA" in history[0]["content"]
    assert "legacy-read-1" in history[0]["content"]


@pytest.mark.parametrize("purpose", ["player_character", "companion"])
def test_character_launch_snapshots_campaign_selections(tmp_path: Path, purpose: str) -> None:
    with AuthorityOwner.open(tmp_path / "characters.sqlite3", "owner-characters") as owner:
        draft = owner.create_guided_campaign_draft(
            operation_id="create-character-interview",
            display_name="Character campaign",
            builder_session_id="character-interview",
            builder_provider_id="test.provider",
            builder_model_id="test.model",
        )
        answers = (
            ("ruleset", "SRD", {"candidate_id": "sylvia.srd-lite.bg3"}),
            ("setting", "Severed Lands", {"setting_id": "pneuma.severed-lands"}),
            ("plan", "Rift Awakening", {"plan_id": "the-rift-awakening"}),
            ("name", "Character campaign", {"display_name": "Character campaign"}),
        )
        for revision, (step, text, value) in enumerate(answers):
            owner.advance_guided_campaign_draft(
                operation_id=f"answer-{step}",
                campaign_id=draft.campaign_id,
                builder_session_id=draft.builder_session_id,
                expected_construction_revision=revision,
                current_step=step,
                visible_answer=text,
                answer=value,
            )
        for index in range(2):
            launched = owner.start_interview_builder_chat(
                operation_id=f"launch-character-{index}",
                campaign_id=draft.campaign_id,
                parent_session_id=draft.builder_session_id,
                expected_construction_revision=4,
                purpose=purpose,
                visible_answer="New Character"
                if purpose == "player_character"
                else "New Companion",
                child_session_id=f"character-chat-{index}",
            )
        assert len(owner.builder_chat_index()) == 2
        context = owner.session_context(launched.child_session_id)
        brief = context.messages[0].content
        assert brief["purpose"] == purpose
        assert brief["selections"]["ruleset"]["value"]["candidate_id"] == "sylvia.srd-lite.bg3"
        assert brief["selections"]["setting"]["text"] == "Severed Lands"
        assert brief["selections"]["plan"]["text"] == "Rift Awakening"
        assert "load_builder_skill" in _builder_prompt(context)
