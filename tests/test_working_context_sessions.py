"""Real SQLite/session-loop composition for compaction, without live inference."""

import json
import threading

import pytest

from sylvia_gm.agent._builder_session import _provider_history, stream_builder_turn
from sylvia_gm.agent._session_loop import CampaignSessionLoop, SessionRunRequest
from sylvia_gm.agent._working_context import (
    CHECKPOINT_KIND,
    WorkingContextProvider,
    compact_saved_messages,
)
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.session import SessionMessageKind
from sylvia_gm.interfaces.owner_process import _dispatch, _json_value
from sylvia_gm.provider._base import ProviderResponse, ProviderStreamChunk
from tests.test_working_context import Model


def saved_chat(owner):
    owner.create_builder_chat(provider_id="test", model_id="test", session_id="builder")
    for index in range(60):
        owner.append_agent_message(
            session_id="builder",
            message_id=f"original-{index}",
            kind=SessionMessageKind.USER if index % 2 == 0 else SessionMessageKind.TALK,
            content={"text": f"Synthetic decision {index}"},
        )


def test_manual_command_survives_restart_and_never_generates_a_builder_reply(tmp_path):
    database = tmp_path / "authority.sqlite3"
    provider = Model(responses=[ProviderResponse(content="Keep all accepted decisions.")] * 10)
    with AuthorityOwner.open(database, "owner") as owner:
        saved_chat(owner)
        loop = CampaignSessionLoop(owner)
        originals = owner.session_context("builder").messages
        result = _dispatch(
            None,
            "submit_session_input",
            {
                "session_id": "builder",
                "user_text": "/compact",
                "attempt_id": "manual",
                "message_id": "command",
            },
            application_owner=owner,
            owner_id="owner",
            provider_resolver=lambda _: provider,
            model_choices=lambda: (),
            session_loop=loop,
            storage_root=tmp_path,
        )
        assert result["status"] == "context_compacted"
        after = owner.session_context("builder")
        assert after.messages[:60] == originals
        records = [m.content for m in after.messages if m.content.get("kind") == CHECKPOINT_KIND]
        assert len(records) == 1
        assert loop.builder_activity("builder")["status"] == "completed"
        assert not any(m.kind is SessionMessageKind.TALK for m in after.messages[60:])
        assert all(call["tools"] is None for call in provider.calls)
        public = json.dumps(_json_value(after))
        assert "Keep all accepted" not in public and "source_archive" not in public
    with AuthorityOwner.open(database, "reopened") as owner:
        context = owner.session_context("builder")
        wrapper = WorkingContextProvider(
            provider, records=[m.content for m in context.messages], save=lambda _: None
        )
        history = _provider_history(context)
        assert len(history) == 60
        assert len(wrapper.project(history)) < 60
        assert wrapper.read_history({"source_id": records[0]["checkpoint_id"]})["content"]


def test_streaming_compaction_commits_checkpoint_before_next_request_and_keeps_tail(tmp_path):
    class OverflowOnce(Model):
        requests = 0

        async def generate_stream(self, messages, **kwargs):
            from sylvia_gm.provider._exceptions import TokenLimitError

            self.requests += 1
            if self.requests == 1:
                raise TokenLimitError("synthetic context limit")
            assert committed.is_set()
            assert messages[-1]["content"] == "Continue with the saved decisions"
            yield ProviderStreamChunk(content="Continued", finish_reason="stop")

    committed = threading.Event()
    provider = OverflowOnce(responses=[ProviderResponse(content="Saved decisions 0–59.")] * 10)
    with AuthorityOwner.open(tmp_path / "stream.db", "owner") as owner:
        saved_chat(owner)
        original_append = owner.append_agent_message

        def append(**kwargs):
            message = original_append(**kwargs)
            if kwargs["content"].get("kind") == CHECKPOINT_KIND:
                committed.set()
            return message

        owner.append_agent_message = append
        loop = CampaignSessionLoop(owner)

        def run(provider_input):
            yield from stream_builder_turn(
                provider,
                context=provider_input.history,
                message_id="new-input",
                user_text="Continue with the saved decisions",
                workspace=tmp_path / "draft",
                control=provider_input.control,
                complete=lambda _: None,
            )

        loop.run_builder(
            SessionRunRequest(
                "builder", "turn", "attempt", "new-input", "Continue with the saved decisions"
            ),
            run,
            lambda: (),
        )
        context = owner.session_context("builder")
        assert context.messages[:60][0].content["text"] == "Synthetic decision 0"
        assert len([m for m in context.messages if m.kind is SessionMessageKind.TALK]) == 31
        assert provider.requests == 2


def test_cancellation_during_summary_keeps_old_history_and_session_can_continue(tmp_path):
    async def wait_forever(*_args, **_kwargs):
        entered.set()
        import asyncio

        await asyncio.Event().wait()

    entered = threading.Event()
    provider = Model()
    provider.generate = wait_forever
    cancelled = []
    with AuthorityOwner.open(tmp_path / "cancel.db", "owner") as owner:
        saved_chat(owner)
        loop = CampaignSessionLoop(
            owner, on_provider_work_started=lambda _s, _a, c: cancelled.append(c)
        )

        def stop_summary():
            assert entered.wait(5)
            cancelled[0]()

        stopper = threading.Thread(target=stop_summary)
        stopper.start()

        def compact(provider_input):
            yield from compact_saved_messages(
                provider,
                provider_input.history,
                _provider_history(provider_input.history),
                provider_input.control,
            )

        with pytest.raises(CommandRejected, match="cancelled"):
            loop.run_compaction("builder", "cancelled", compact)
        stopper.join(timeout=5)
        assert not any(
            m.content.get("kind") == CHECKPOINT_KIND
            for m in owner.session_context("builder").messages
        )
        loop.assert_idle("builder")
        assert len(_provider_history(owner.session_context("builder"))) == 60


def test_gm_history_read_projection_does_not_expose_secrets_to_renderer():
    source = {
        "kind": "provider_tool_results",
        "results": [
            {"role": "tool", "name": "read_session_history", "content": "Secret villain identity"},
            {"role": "tool", "name": "safe_other", "content": "Keep this ordinary result"},
        ],
    }
    result = _json_value(source)
    assert "Secret villain" not in json.dumps(result)
    assert "ordinary result" in json.dumps(result)
    assert source["results"][0]["content"] == "Secret villain identity"


def test_definition_section_reread_is_smaller_and_versioned():
    from sylvia_gm.agent._content_definition_tool import content_definition_tool

    tool = content_definition_tool()
    complete = tool.handler({"name": "campaign_plan"}, None)
    selected = tool.handler({"name": "campaign_plan", "section": "/example"}, None)
    assert selected["value"] == complete["definition"]["example"]
    assert len(selected["definition_sha256"]) == 64
    assert len(json.dumps(selected)) < len(json.dumps(complete))
    assert not tool.handler({"name": "campaign_plan", "section": "/not-a-field"}, None)["success"]


def test_same_batch_prose_and_tool_results_restore_checkpoint_without_repeating_write(
    tmp_path,
    monkeypatch,
):
    from sylvia_gm.provider._exceptions import TokenLimitError
    from sylvia_gm.tools._spec import ToolSpec
    from sylvia_gm.tools._toolsets import ToolsetName

    monkeypatch.setattr(WorkingContextProvider, "_budgets", lambda *_: (128_000, 1_000_000, 1))
    writes = []

    def write(_params, _context):
        writes.append("saved")
        (tmp_path / "draft.md").write_text("Ada", encoding="utf-8")
        return {"success": True, "path": "draft.md"}

    tool = ToolSpec.from_openai_schema(
        {
            "type": "function",
            "function": {
                "name": "save_draft",
                "description": "Save the test draft",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        handler=write,
        toolsets=frozenset({ToolsetName.BUILDER_TOOLS}),
    )

    class Burst(Model):
        count = 0

        async def generate_stream(self, messages, **options):
            self.count += 1
            if self.count == 3:
                raise TokenLimitError("context is full")
            if self.count <= 2:
                name = "save_draft" if self.count == 1 else "read_file"
                arguments = "{}" if self.count == 1 else '{"path":"draft.md"}'
                yield ProviderStreamChunk(
                    content="I will save the draft. " if self.count == 1 else ""
                )
                yield ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": f"call-{self.count}",
                        "function": {"name": name, "arguments": arguments},
                    },
                    finish_reason="tool_calls",
                )
            else:
                yield ProviderStreamChunk(content="Done", finish_reason="stop")

    provider = Burst(responses=[ProviderResponse(content="Saved draft.md containing Ada.")] * 30)
    with AuthorityOwner.open(tmp_path / "batched.db", "owner") as owner:
        saved_chat(owner)
        loop = CampaignSessionLoop(owner)

        def run(provider_input):
            yield from stream_builder_turn(
                provider,
                context=provider_input.history,
                message_id="new",
                user_text="Save Ada",
                workspace=tmp_path,
                control=provider_input.control,
                complete=lambda _: None,
                source_tools=(tool,),
            )

        loop.run_builder(
            SessionRunRequest("builder", "turn", "a", "new", "Save Ada"), run, lambda: ()
        )
        context = owner.session_context("builder")
        wrapper = WorkingContextProvider(
            provider, records=[m.content for m in context.messages], save=lambda _: None
        )
        history = _provider_history(context)
        assert wrapper.records and wrapper.project(history) != history
        assert writes == ["saved"]
        assert (tmp_path / "draft.md").read_text(encoding="utf-8") == "Ada"
        assert (
            len(
                [
                    m
                    for m in context.messages
                    if m.content.get("kind") == "builder_tool_result"
                    and m.content.get("tool_name") == "save_draft"
                ]
            )
            == 1
        )


def test_gm_compaction_preserves_current_state_and_enforced_output(tmp_path):
    from sylvia_gm.agent._provider_session import ProviderSessionAdapter
    from sylvia_gm.agent._session_loop import ProviderControl, SessionProviderInput
    from sylvia_gm.application.authority import CampaignCreate
    from sylvia_gm.domain.authority import PackageLock, WriterRole
    from sylvia_gm.provider._exceptions import TokenLimitError
    from tests.test_working_context import history

    class GM(Model):
        requests = []

        async def generate_stream(self, messages, tools=None, **options):
            self.requests.append((messages, tools, options))
            if len(self.requests) == 1:
                raise TokenLimitError("context limit")
            yield ProviderStreamChunk(content="Narration", finish_reason="stop")

    provider = GM(
        responses=[ProviderResponse(content="Historical guess AP=100; villain is secret")] * 10
    )
    with AuthorityOwner.open(tmp_path / "gm.db", "owner") as owner:
        owner.create_campaign(
            CampaignCreate(
                display_name="Synthetic GM",
                writer=WriterRole.DM,
                package_lock=PackageLock(entries=()),
                turn_id="turn",
                campaign_id="campaign",
            )
        )
        owner.create_agent_session(
            campaign_id="campaign",
            role=WriterRole.DM,
            provider_id="test",
            model_id="test",
            session_id="gm",
        )
        context = owner.session_context("gm")
        provider_input = SessionProviderInput(context.session, context, (), ProviderControl())
        adapter = ProviderSessionAdapter(owner, provider, owner_id="owner")
        before = owner.snapshot("campaign")
        messages = history()
        with owner.agent_session_work("gm"):
            list(
                adapter._stream_round(
                    messages,
                    [],
                    control=provider_input.control,
                    provider_input=provider_input,
                    history_allowed=True,
                )
            )
            messages[0] = {"role": "system", "content": "FRESH STATE: authoritative AP=2"}
            list(
                adapter._stream_round(
                    messages,
                    [],
                    control=provider_input.control,
                    provider_input=provider_input,
                    history_allowed=True,
                )
            )
        assert owner.snapshot("campaign") == before
        assert provider.requests[-1][0][0] == messages[0]
        assert not any("AP=4" in str(m) for m in provider.requests[-1][0])
        assert provider.requests[-1][2]["max_tokens"] == 4096
        assert provider.requests[-1][1][0]["function"]["name"] == "read_session_history"
        saved = owner.session_context("gm")
        assert "villain is secret" not in json.dumps(_json_value(saved))
        assert any(m.content.get("kind") == CHECKPOINT_KIND for m in saved.messages)
