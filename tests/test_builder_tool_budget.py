"""Builder works until its request is done, stopping only when it stops making progress."""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import pytest

from sylvia_gm.agent._builder_reference_copy_tool import builder_reference_copy_tool
from sylvia_gm.agent._builder_session import _builder_reference_tool, stream_builder_turn
from sylvia_gm.agent._mode import AgentMode
from sylvia_gm.agent._runner import AgentLoopError, AgentRunner, _BuilderLoopGuard
from sylvia_gm.agent._session_loop import CampaignSessionLoop, SessionRunRequest
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.builder_references import BuilderReferences
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.provider._base import FakeProvider, ProviderResponse, ProviderStreamChunk, ToolCall
from sylvia_gm.provider._exceptions import ProviderOutputLimitError
from sylvia_gm.tools._spec import ToolSpec
from sylvia_gm.tools._toolsets import ToolsetName


class AuthoringProvider(FakeProvider):
    async def generate_stream(self, messages, tools=None, **_kwargs):
        response = await self.generate(messages, tools)
        for index, call in enumerate(response.tool_calls):
            yield ProviderStreamChunk(
                tool_call_delta={
                    "index": index,
                    "id": call.id,
                    "function": {"name": call.name, "arguments": call.arguments},
                }
            )
        yield ProviderStreamChunk(
            content=response.content or "",
            finish_reason="tool_calls" if response.tool_calls else "stop",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_builder_finishes_research_then_saves_without_manual_continuation(
    tmp_path: Path, streaming: bool
):
    (tmp_path / "reference.md").write_text("\n".join(f"Rule {i}" for i in range(36)))
    responses = [
        ProviderResponse(
            tool_calls=[
                ToolCall(
                    id=f"read-{i}",
                    name="read_file",
                    arguments=json.dumps({"path": "reference.md", "offset": i + 1, "limit": 1}),
                )
            ]
        )
        for i in range(36)
    ]
    responses += [
        ProviderResponse(
            tool_calls=[
                ToolCall(
                    id="save",
                    name="write_file",
                    arguments='{"path":"draft.md","content":"Finished draft"}',
                )
            ]
        ),
        ProviderResponse(content="Saved the draft."),
    ]
    provider = AuthoringProvider(responses=responses)
    runner = AgentRunner(AgentMode.BUILDER_FREEFORM, provider, None, project_root=tmp_path)
    if streaming:
        events = asyncio.Queue()
        await runner.turn_stream("Read the references and save the draft.", events)
        observed = [events.get_nowait() for _ in range(events.qsize())]
        assert any(event.type == "done" for event in observed)
    else:
        assert (
            await runner.turn("Read the references and save the draft.")
        ).dm_response == "Saved the draft."
    assert (tmp_path / "draft.md").read_text() == "Finished draft"
    assert len(provider.calls) == 38


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_builder_stops_repeating_the_same_failure(tmp_path: Path, streaming: bool):
    responses = [
        ProviderResponse(
            tool_calls=[
                ToolCall(
                    id=f"read-{i}",
                    name="read_file",
                    arguments='{"path":"missing.md"}',
                )
            ]
        )
        for i in range(5)
    ]
    provider = AuthoringProvider(responses=responses)
    runner = AgentRunner(AgentMode.BUILDER_FREEFORM, provider, None, project_root=tmp_path)
    with pytest.raises(AgentLoopError, match="repeated"):
        if streaming:
            await runner.turn_stream("Read the draft.", asyncio.Queue())
        else:
            await runner.turn("Read the draft.")
    assert len(provider.calls) == 3
    assert (
        len([message for message in runner.history_snapshot() if message["role"] == "tool"])
        == 3
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_builder_stops_one_unreadable_source_across_varied_calls_and_successful_reads(
    tmp_path: Path,
    streaming: bool,
):
    unavailable = "ruleset/runtime/ruleset.yaml"

    def read(path):
        if path == unavailable:
            raise CommandRejected(
                "Selected source cannot be read", code="package_file_unavailable"
            )
        return "A successfully read creation reference"

    references = BuilderReferences((unavailable, "ruleset/creation.md"), read)
    operations = [
        (
            "inspect_builder_references",
            {"action": "find", "path": unavailable, "query": "starting_level_queue"},
        ),
        ("inspect_builder_references", {"action": "read", "path": "ruleset/creation.md"}),
        ("inspect_builder_references", {"action": "read", "path": unavailable, "limit": 1200}),
        ("write_file", {"path": "profile.md", "content": "Preserved authored character profile"}),
        (
            "copy_builder_reference",
            {"source_path": unavailable, "destination_path": "references/runtime.yaml"},
        ),
    ]
    provider = AuthoringProvider(
        responses=[
            ProviderResponse(
                tool_calls=[
                    ToolCall(
                        id=f"call-{i}",
                        name=name,
                        arguments=json.dumps(arguments),
                    )
                ]
            )
            for i, (name, arguments) in enumerate(operations)
        ]
        + [ProviderResponse(content="Incorrectly completed")]
    )
    runner = AgentRunner(
        AgentMode.BUILDER_FREEFORM,
        provider,
        None,
        project_root=tmp_path,
        builder_application_tools=(
            _builder_reference_tool(references),
            builder_reference_copy_tool(references, tmp_path),
        ),
    )
    with pytest.raises(AgentLoopError) as stopped:
        if streaming:
            await runner.turn_stream("Create the character", asyncio.Queue())
        else:
            await runner.turn("Create the character")
    assert stopped.value.reason == "repeated_reference_failure"
    assert len(provider.calls) == 5
    assert len([m for m in runner.history_snapshot() if m["role"] == "tool"]) == 5
    assert (tmp_path / "profile.md").read_text() == "Preserved authored character profile"
    assert not (tmp_path / "references/runtime.yaml").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_listing_an_unreadable_reference_does_not_count_as_recovery(tmp_path, streaming):
    path = "ruleset/runtime/ruleset.yaml"

    def unreadable(_path):
        raise CommandRejected("Missing", code="package_file_unavailable")

    references = BuilderReferences((path,), unreadable)
    provider = AuthoringProvider(
        responses=[
            ProviderResponse(
                tool_calls=[
                    ToolCall(
                        id=f"call-{i}",
                        name="inspect_builder_references",
                        arguments=json.dumps(
                            {"action": "read" if i % 2 == 0 else "list", "path": path}
                        ),
                    )
                ]
            )
            for i in range(12)
        ]
        + [ProviderResponse(content="Must not finish")]
    )
    runner = AgentRunner(
        AgentMode.BUILDER_FREEFORM,
        provider,
        None,
        project_root=tmp_path,
        builder_application_tools=(_builder_reference_tool(references),),
    )
    with pytest.raises(AgentLoopError) as stopped:
        if streaming:
            await runner.turn_stream("Read the source", asyncio.Queue())
        else:
            await runner.turn("Read the source")
    assert stopped.value.reason == "repeated_reference_failure"
    assert len(provider.calls) == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("later", ["ask_question", "publish_participant"])
async def test_structural_reference_failure_blocks_later_batch_tools(tmp_path, streaming, later):
    path = "ruleset/runtime/ruleset.yaml"

    def corrupted(_path):
        raise CommandRejected("Damaged", code="package_corrupt")

    published = []

    def publish(_params, _context):
        published.append(True)
        return {"success": True}

    references = BuilderReferences((path,), corrupted)
    tools = (
        _builder_reference_tool(references),
        ToolSpec(
            name="publish_participant",
            description="Save the character",
            parameters=[],
            handler=publish,
            toolsets=frozenset({ToolsetName.BUILDER_TOOLS}),
        ),
    )
    provider = AuthoringProvider(
        responses=[
            ProviderResponse(
                tool_calls=[
                    ToolCall(
                        id="bad-source",
                        name="inspect_builder_references",
                        arguments=json.dumps({"action": "read", "path": path}),
                    ),
                    ToolCall(
                        id="later",
                        name=later,
                        arguments=json.dumps(
                            {"type": "text", "id": "question", "question": "Choose?"}
                        ),
                    ),
                ]
            ),
            ProviderResponse(content="Must not finish"),
        ]
    )
    runner = AgentRunner(
        AgentMode.BUILDER_FREEFORM,
        provider,
        None,
        project_root=tmp_path,
        builder_application_tools=tools,
    )
    events = asyncio.Queue()
    with pytest.raises(AgentLoopError) as stopped:
        if streaming:
            await runner.turn_stream("Create the character", events)
        else:
            await runner.turn("Create the character")
    assert stopped.value.reason == "required_reference_unavailable"
    assert len(provider.calls) == 1
    assert published == []
    assert not runner._pending_widget
    observed = [events.get_nowait() for _ in range(events.qsize())]
    assert not any((event.meta or {}).get("widget") for event in observed)
    history = runner.history_snapshot()
    requested = {call["id"] for message in history if message["role"] == "assistant"
                 for call in message.get("tool_calls", [])}
    returned = {message["tool_call_id"] for message in history if message["role"] == "tool"}
    assert requested == returned
    if streaming:
        skipped = next(json.loads(m["content"]) for m in history
                       if m.get("tool_call_id") == "later")
        assert skipped == {"success": False, "code": "not_executed",
                           "reason": "required_reference_unavailable"}
    assert any(
        json.loads(m["content"]).get("reason") == "package_corrupt"
        for m in history
        if m["role"] == "tool"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_a_real_successful_reference_read_allows_recovery(tmp_path, streaming):
    path = "ruleset/runtime/ruleset.yaml"
    reads = []

    def recover(_path):
        reads.append(_path)
        if len(reads) != 3:
            raise CommandRejected("Missing", code="package_file_unavailable")
        return "Recovered legal starting choices"

    provider = AuthoringProvider(responses=[ProviderResponse(tool_calls=[ToolCall(
        id=f"read-{i}", name="inspect_builder_references",
        arguments=json.dumps({"action": "read", "path": path}),
    )]) for i in range(5)] + [ProviderResponse(content="Recovered")])
    runner = AgentRunner(AgentMode.BUILDER_FREEFORM, provider, None, project_root=tmp_path,
        builder_application_tools=(_builder_reference_tool(BuilderReferences((path,), recover)),))
    if streaming:
        await runner.turn_stream("Read the source", asyncio.Queue())
    else:
        assert (await runner.turn("Read the source")).dm_response == "Recovered"
    assert len(provider.calls) == 6
    assert len(reads) == 5


def test_saved_builder_stops_oversized_reference_without_another_model_request(tmp_path):
    path = "ruleset/runtime/ruleset.yaml"

    def unavailable(_path):
        raise CommandRejected(
            "Package file exceeds its consumer byte limit", code="package_file_oversized"
        )

    references = BuilderReferences((path,), unavailable)
    provider = AuthoringProvider(
        responses=[
            ProviderResponse(
                tool_calls=[
                    ToolCall(
                        id="runtime",
                        name="inspect_builder_references",
                        arguments=json.dumps(
                            {
                                "action": "find",
                                "path": path,
                                "query": "starting_level_queue",
                            }
                        ),
                    )
                ]
            ),
            ProviderResponse(content="Should never be requested"),
        ]
    )
    workspace = tmp_path / "draft"
    workspace.mkdir()
    (workspace / "profile.md").write_text("Existing character work")
    with AuthorityOwner.open(tmp_path / "owner.db", "builder") as owner:
        owner.create_builder_chat(provider_id="fixture", model_id="fixture", session_id="builder")
        loop = CampaignSessionLoop(owner)
        with pytest.raises(CommandRejected, match="source file is too large or damaged"):
            loop.run_builder(
                SessionRunRequest(
                    "builder", "turn", "source-error", "input", "Create my character"
                ),
                lambda data: stream_builder_turn(
                    provider,
                    context=data.history,
                    message_id="input",
                    user_text="Create my character",
                    workspace=workspace,
                    control=data.control,
                    complete=lambda _: None,
                    references=references,
                ),
                lambda: (),
            )
        loop.assert_idle("builder")
        messages = owner.session_context("builder").messages
        terminal = next(
            m.content for m in messages if m.content.get("kind") == "builder_attempt_terminal"
        )
        assert terminal["status"] == "interrupted"
        assert terminal["reason_code"] == "required_reference_unavailable"
        receipt = next(
            m.content
            for m in messages
            if m.content.get("tool_name") == "inspect_builder_references"
        )
        assert receipt["result"]["reason"] == "package_file_oversized"
    assert len(provider.calls) == 1
    assert (workspace / "profile.md").read_text() == "Existing character work"


def test_saved_builder_works_far_past_the_former_round_allowance_in_one_turn(tmp_path):
    rounds = 100
    workspace = tmp_path / "draft"
    workspace.mkdir()
    (workspace / "reference.md").write_text("\n".join(f"Rule {i}" for i in range(rounds)))
    provider = AuthoringProvider(
        responses=[
            ProviderResponse(tool_calls=[ToolCall(
                id=f"read-{i}", name="read_file",
                arguments=json.dumps({"path": "reference.md", "offset": i + 1, "limit": 1}),
            )])
            for i in range(rounds)
        ]
        + [
            ProviderResponse(tool_calls=[ToolCall(
                id="save", name="write_file",
                arguments='{"path":"draft.md","content":"Finished draft"}',
            )]),
            ProviderResponse(content="Saved the draft."),
        ]
    )
    with AuthorityOwner.open(tmp_path / "authority.sqlite3", "first") as owner:
        owner.create_builder_chat(provider_id="test", model_id="test", session_id="builder")
        loop = CampaignSessionLoop(owner)
        loop.run_builder(
            SessionRunRequest("builder", "turn", "long", "input", "Read it all, then save"),
            lambda data: stream_builder_turn(
                provider, context=data.history, message_id="input",
                user_text="Read it all, then save", workspace=workspace,
                control=data.control, complete=lambda _: None,
            ), lambda: (),
        )
        loop.assert_idle("builder")
        messages = owner.session_context("builder").messages
        terminal = next(m.content for m in messages
                        if m.content.get("kind") == "builder_attempt_terminal")
        assert terminal["status"] == "completed"
        assert len([m for m in messages if m.content.get("tool_name") == "read_file"]) == rounds
    assert (workspace / "draft.md").read_text() == "Finished draft"
    assert len(provider.calls) == rounds + 2


def test_saved_builder_explains_a_repeated_step_and_resumes_without_repeating_write(tmp_path):
    repeats = _BuilderLoopGuard.IDENTICAL_ROUNDS
    provider = AuthoringProvider(
        responses=[
            ProviderResponse(
                tool_calls=[
                    ToolCall(
                        id="save",
                        name="write_file",
                        arguments='{"path":"draft.md","content":"Saved once"}',
                    )
                ]
            )
        ]
        + [
            ProviderResponse(tool_calls=[ToolCall(
                id=f"read-{i}", name="read_file", arguments='{"path":"draft.md"}',
            )])
            for i in range(repeats + 2)
        ]
    )
    database = tmp_path / "authority.sqlite3"
    workspace = tmp_path / "draft"
    with AuthorityOwner.open(database, "first") as owner:
        owner.create_builder_chat(provider_id="test", model_id="test", session_id="builder")
        loop = CampaignSessionLoop(owner)

        def run(provider_input):
            yield from stream_builder_turn(
                provider, context=provider_input.history, message_id="input",
                user_text="Save the draft", workspace=workspace,
                control=provider_input.control, complete=lambda _: None,
            )

        with pytest.raises(CommandRejected, match="repeating the same step"):
            loop.run_builder(
                SessionRunRequest("builder", "turn", "stuck", "input", "Save the draft"),
                run, lambda: (),
            )
        loop.assert_idle("builder")
        assert (workspace / "draft.md").read_text() == "Saved once"
        assert len(provider.calls) == 1 + repeats
    with AuthorityOwner.open(database, "reopened") as owner:
        messages = owner.session_context("builder").messages
        terminal = next(m.content for m in messages
                        if m.content.get("kind") == "builder_attempt_terminal")
        assert terminal["status"] == "interrupted"
        assert terminal["reason_code"] == "repeated_step"
        receipts = [m.content for m in messages if m.content.get("tool_name") == "write_file"]
        assert len(receipts) == 1
        provider = AuthoringProvider(responses=[
            ProviderResponse(content="The saved draft is ready."),
        ])
        CampaignSessionLoop(owner).run_builder(
            SessionRunRequest("builder", "turn-2", "continued", "follow-up", "Continue"),
            lambda data: stream_builder_turn(
                provider, context=data.history, message_id="follow-up", user_text="Continue",
                workspace=workspace, control=data.control, complete=lambda _: None,
            ), lambda: (),
        )
        assert (workspace / "draft.md").read_text() == "Saved once"
        assert len([m for m in owner.session_context("builder").messages
                    if m.content.get("tool_name") == "write_file"]) == 1
        assert len(provider.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_builder_stops_repeating_the_same_successful_step(tmp_path, streaming):
    repeats = _BuilderLoopGuard.IDENTICAL_ROUNDS
    (tmp_path / "reference.md").write_text("A valid reference")
    provider = AuthoringProvider(responses=[ProviderResponse(tool_calls=[ToolCall(
        id=f"read-{index}", name="read_file", arguments='{"path":"reference.md"}',
    )]) for index in range(repeats + 3)])
    runner = AgentRunner(AgentMode.BUILDER_FREEFORM, provider, None, project_root=tmp_path)
    with pytest.raises(AgentLoopError) as stopped:
        if streaming:
            await runner.turn_stream("Read the reference.", asyncio.Queue())
        else:
            await runner.turn("Read the reference.")
    assert stopped.value.reason == "repeated_step"
    assert len(provider.calls) == repeats


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_a_changed_step_between_repeats_is_progress(tmp_path, streaming):
    repeats = _BuilderLoopGuard.IDENTICAL_ROUNDS
    (tmp_path / "reference.md").write_text("First\nSecond")
    provider = AuthoringProvider(
        responses=[
            ProviderResponse(tool_calls=[ToolCall(
                id=f"read-{index}", name="read_file",
                arguments=json.dumps(
                    {"path": "reference.md", "offset": 1 + index % 2, "limit": 1}
                ),
            )])
            for index in range(repeats * 3)
        ]
        + [ProviderResponse(content="Compared both lines.")]
    )
    runner = AgentRunner(AgentMode.BUILDER_FREEFORM, provider, None, project_root=tmp_path)
    if streaming:
        await runner.turn_stream("Compare the lines.", asyncio.Queue())
    else:
        assert (await runner.turn("Compare the lines.")).dm_response == "Compared both lines."
    assert len(provider.calls) == repeats * 3 + 1


def test_saved_builder_automatically_continues_without_repeating_write(tmp_path):
    provider = AuthoringProvider(responses=[
        ProviderResponse(tool_calls=[ToolCall(
            id="save", name="write_file", arguments='{"path":"draft.md","content":"Saved once"}',
        )]),
        ProviderResponse(content="The saved draft is ready."),
    ])
    database = tmp_path / "authority.sqlite3"
    workspace = tmp_path / "draft"
    with AuthorityOwner.open(database, "first") as owner:
        owner.create_builder_chat(provider_id="test", model_id="test", session_id="builder")
        loop = CampaignSessionLoop(owner)
        loop.run_builder(
            SessionRunRequest("builder", "turn", "automatic", "input", "Save the draft"),
            lambda data: stream_builder_turn(
                provider, context=data.history, message_id="input", user_text="Save the draft",
                workspace=workspace, control=data.control, complete=lambda _: None,
            ), lambda: (),
        )
        loop.assert_idle("builder")
    with AuthorityOwner.open(database, "reopened") as owner:
        messages = owner.session_context("builder").messages
        terminal = next(m.content for m in messages
                        if m.content.get("kind") == "builder_attempt_terminal")
        assert terminal["status"] == "completed"
        assert len([m for m in messages if m.content.get("tool_name") == "write_file"]) == 1
        assert (workspace / "draft.md").read_text() == "Saved once"
        assert len(provider.calls) == 2


def test_stop_during_automatic_continuation_preserves_saved_work(tmp_path):
    continued = threading.Event()

    class WaitingProvider(AuthoringProvider):
        async def generate_stream(self, messages, tools=None, **kwargs):
            if self.calls:
                continued.set()
                await asyncio.Event().wait()
            async for chunk in super().generate_stream(messages, tools, **kwargs):
                yield chunk

    provider = WaitingProvider(responses=[ProviderResponse(tool_calls=[ToolCall(
        id="save", name="write_file", arguments='{"path":"draft.md","content":"Saved before Stop"}',
    )])])
    database = tmp_path / "authority.sqlite3"
    workspace = tmp_path / "draft"
    errors = []
    with AuthorityOwner.open(database, "first") as owner:
        owner.create_builder_chat(provider_id="test", model_id="test", session_id="builder")
        loop = CampaignSessionLoop(owner)

        def run():
            try:
                loop.run_builder(
                    SessionRunRequest("builder", "turn", "automatic", "input", "Save the draft"),
                    lambda data: stream_builder_turn(
                        provider, context=data.history, message_id="input",
                        user_text="Save the draft",
                        workspace=workspace, control=data.control, complete=lambda _: None,
                    ), lambda: (),
                )
            except CommandRejected as error:
                errors.append(error)

        worker = threading.Thread(target=run)
        worker.start()
        try:
            assert continued.wait(5), "Builder did not reach its automatic continuation"
            loop.stop_agent_session("builder")
        finally:
            if worker.is_alive():
                loop.stop_agent_session("builder")
            worker.join(5)
        assert not worker.is_alive()
        assert len(errors) == 1 and "cancelled" in str(errors[0])
        loop.assert_idle("builder")
    with AuthorityOwner.open(database, "reopened") as owner:
        assert CampaignSessionLoop(owner).builder_activity("builder")["status"] == "stopped"
        assert (workspace / "draft.md").read_text() == "Saved before Stop"
        assert len([m for m in owner.session_context("builder").messages
                    if m.content.get("tool_name") == "write_file"]) == 1


class CutOffProvider(FakeProvider):
    """Stream one scripted response per request; "length" ends a response the provider cut off."""

    def __init__(self, scripts):
        super().__init__()
        self._scripts = list(scripts)

    async def generate_stream(self, messages, tools=None, **_kwargs):
        self.calls.append({"messages": [dict(message) for message in messages], "tools": tools})
        for chunk in self._scripts.pop(0):
            yield chunk


def _call(call_id, name, arguments, *, finish="tool_calls"):
    return [
        ProviderStreamChunk(tool_call_delta={
            "index": 0, "id": call_id, "function": {"name": name, "arguments": arguments},
        }),
        ProviderStreamChunk(finish_reason=finish),
    ]


def _text(text, *, finish="stop"):
    return [ProviderStreamChunk(content=text), ProviderStreamChunk(finish_reason=finish)]


def _is_continue_signal(message):
    return message["role"] == "user" and message["content"].startswith(
        "SYLVIA_GM_BUILDER_CONTINUE "
    )


@pytest.mark.asyncio
async def test_cut_off_tool_call_never_runs_and_builder_continues_in_smaller_steps(tmp_path):
    provider = CutOffProvider([
        # The allowance ran out inside the file content, so the arguments never closed.
        _call("cut", "write_file", '{"path":"big.md","content":"Part one. Pa', finish="length"),
        _call("first", "write_file", '{"path":"part-one.md","content":"Part one."}'),
        _call("second", "write_file", '{"path":"part-two.md","content":"Part two."}'),
        _text("Saved the work in two smaller files."),
    ])
    runner = AgentRunner(AgentMode.BUILDER_FREEFORM, provider, None, project_root=tmp_path)
    events = asyncio.Queue()
    await runner.turn_stream("Write the big file.", events)

    assert not (tmp_path / "big.md").exists()
    assert (tmp_path / "part-one.md").read_text() == "Part one."
    assert (tmp_path / "part-two.md").read_text() == "Part two."
    assert len(provider.calls) == 4
    # The request after the cut-off ends with the application's signal, not a tool exchange.
    assert _is_continue_signal(provider.calls[1]["messages"][-1])
    history = runner.history_snapshot()
    called = [call["id"] for message in history for call in message.get("tool_calls", [])]
    assert called == ["first", "second"]
    observed = [events.get_nowait() for _ in range(events.qsize())]
    assert [event.data for event in observed if event.type == "tool_call"] == [
        "write_file", "write_file",
    ]
    assert observed[-1].type == "done"


@pytest.mark.asyncio
async def test_a_completed_response_renews_the_cut_off_allowance(tmp_path):
    limit = AgentRunner.MAX_OUTPUT_LIMIT_CONTINUATIONS
    (tmp_path / "notes.md").write_text("Notes")
    cut = _call("cut", "write_file", '{"path":"big.md","content":"Too lo', finish="length")
    provider = CutOffProvider(
        [cut] * limit
        + [_call("read", "read_file", '{"path":"notes.md"}')]
        + [cut] * limit
        + [_text("Finished.")]
    )
    runner = AgentRunner(AgentMode.BUILDER_FREEFORM, provider, None, project_root=tmp_path)
    await runner.turn_stream("Keep going.", asyncio.Queue())
    assert len(provider.calls) == 2 * limit + 2
    assert not (tmp_path / "big.md").exists()


def _run_saved_builder(owner, provider, workspace, *, attempt="attempt"):
    CampaignSessionLoop(owner).run_builder(
        SessionRunRequest("builder", "turn", attempt, "input", "Describe the setting"),
        lambda data: stream_builder_turn(
            provider, context=data.history, message_id="input",
            user_text="Describe the setting", workspace=workspace,
            control=data.control, complete=lambda _: None,
        ), lambda: (),
    )


def test_saved_builder_keeps_cut_off_prose_and_finishes_the_reply(tmp_path):
    provider = CutOffProvider([
        _text("The harbour town sits under", finish="length"),
        _text(" a ring of old watchtowers."),
    ])
    database = tmp_path / "authority.sqlite3"
    with AuthorityOwner.open(database, "first") as owner:
        owner.create_builder_chat(provider_id="test", model_id="test", session_id="builder")
        _run_saved_builder(owner, provider, tmp_path / "draft")
    assert len(provider.calls) == 2
    # The model sees its own cut-off text, then the application's signal to carry on.
    partial, signal = provider.calls[1]["messages"][-2:]
    assert (partial["role"], partial["content"]) == ("assistant", "The harbour town sits under")
    assert _is_continue_signal(signal)
    assert "_application_data" not in signal
    with AuthorityOwner.open(database, "reopened") as owner:
        messages = owner.session_context("builder").messages
        assert [m.content["text"] for m in messages if m.kind.value == "talk"] == [
            "The harbour town sits under", " a ring of old watchtowers.",
        ]
        # The signal is a working instruction for the model, never a saved player message.
        assert [m.content["text"] for m in messages if m.kind.value == "user"] == [
            "Describe the setting",
        ]
        terminal = next(m.content for m in messages
                        if m.content.get("kind") == "builder_attempt_terminal")
        assert terminal["status"] == "completed"


def test_saved_builder_reports_a_model_that_keeps_reaching_its_response_limit(tmp_path):
    limit = AgentRunner.MAX_OUTPUT_LIMIT_CONTINUATIONS
    provider = CutOffProvider(
        [_call("save", "write_file", '{"path":"draft.md","content":"Kept"}')]
        + [_call("cut", "write_file", '{"path":"big.md","content":"Too lo', finish="length")]
        * (limit + 1)
        + [_text("Must not be requested.")]
    )
    database = tmp_path / "authority.sqlite3"
    workspace = tmp_path / "draft"
    with AuthorityOwner.open(database, "first") as owner:
        owner.create_builder_chat(provider_id="test", model_id="test", session_id="builder")
        with pytest.raises(ProviderOutputLimitError):
            _run_saved_builder(owner, provider, workspace)
    assert len(provider.calls) == 1 + limit + 1
    assert (workspace / "draft.md").read_text() == "Kept"
    assert not (workspace / "big.md").exists()
    with AuthorityOwner.open(database, "reopened") as owner:
        messages = owner.session_context("builder").messages
        terminal = next(m.content for m in messages
                        if m.content.get("kind") == "builder_attempt_terminal")
        assert terminal["status"] == "interrupted"
        assert terminal["reason_code"] == "output_limit"
        assert [m.content["tool_name"] for m in messages if "tool_name" in m.content] == [
            "write_file",
        ]
