"""Builder widget answers retain their canonical question/tool identity and genuine input."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from sylvia_gm.agent._builder_question_answers import builder_question_answer_content
from sylvia_gm.agent._session_loop import CampaignSessionLoop, ProviderEvent, SessionRunRequest
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.session import SessionMessageKind
from sylvia_gm.infrastructure import sqlite_session
from sylvia_gm.provider._base import FakeProvider, ProviderResponse
from sylvia_gm.tools._builder_handlers import tool_ask_question
from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness


def seed_question(owner, *, session_id="builder", identity="play-question", **overrides):
    arguments = {
        "id": "player_concept",
        "type": "text",
        "question": "Who would you like to play?",
        **overrides,
    }
    return owner.append_agent_message(
        session_id=session_id,
        message_id=identity,
        kind=SessionMessageKind.EVIDENCE,
        content={
            "kind": "builder_tool_result",
            "tool_name": "ask_question",
            "tool_call_id": identity + "-call",
            "arguments": json.dumps(arguments),
            "result": tool_ask_question(arguments, None),
        },
    )


@pytest.mark.parametrize("attached", [False, True])
@pytest.mark.parametrize("straddling", [False, True])
def test_reopened_page_exposes_complete_active_form_without_reading_unrelated_history(
    tmp_path,
    monkeypatch,
    attached,
    straddling,
):
    database = tmp_path / "paged-form.sqlite3"
    with AuthorityOwner.open(database, "seed") as owner:
        if attached:
            owner.create_guided_campaign_draft(
                operation_id="campaign",
                display_name="Paged form fixture",
                builder_session_id="builder",
                builder_provider_id="test",
                builder_model_id="test",
            )
        else:
            owner.create_builder_chat(session_id="builder", provider_id="test", model_id="test")
        seed_question(owner, identity="closed-question")
        for index in range(80):
            owner.append_agent_message(
                session_id="builder",
                message_id=f"old-{index}",
                kind=SessionMessageKind.TALK,
                content={"text": "Unrelated older prose"},
            )
        owner.append_agent_message(
            session_id="builder",
            message_id="latest-input",
            kind=SessionMessageKind.USER,
            content={"text": "Create my character"},
        )
        first = seed_question(owner, identity="offpage-question")
        if not straddling:
            second = seed_question(
                owner,
                identity="second-question",
                id="background",
                question="What is their background?",
            )
        for index in range(55):
            owner.append_agent_message(
                session_id="builder",
                message_id=f"recent-{index}",
                kind=SessionMessageKind.TALK,
                content={"text": "Related Builder progress"},
            )
        if straddling:
            second = seed_question(
                owner,
                identity="second-question",
                id="background",
                question="What is their background?",
            )
    with AuthorityOwner.open(database, "reopen") as owner:
        decoded = []
        original = sqlite_session.message_from_row

        def capture(row):
            decoded.append(row["message_id"])
            return original(row)

        monkeypatch.setattr(sqlite_session, "message_from_row", capture)
        page = owner.session_history_page("builder")
        assert len(page.messages) == 50
        assert first.message_id not in {row.message_id for row in page.messages}
        assert getattr(page, "builder_question_messages", ()) == (first, second)
        assert "closed-question" not in decoded
        assert not any(identity.startswith("old-") for identity in decoded)
        # Only the bounded page, current form and two existing launch projections are decoded.
        assert len(decoded) <= 54
        context = owner.session_context("builder")
        text = (
            "Here are my answers:\n\n1. Who would you like to play?\nAnswer: Rowan"
            "\n\n2. What is their background?\nAnswer: Courier"
        )
        answers = [
            {"question_message_id": row.message_id, "value": value, "custom": False}
            for row, value in zip(page.builder_question_messages, ("Rowan", "Courier"), strict=True)
        ]
        assert len(builder_question_answer_content(context, answers, text)["answers"]) == 2
        with pytest.raises(CommandRejected, match="Answer every current question"):
            builder_question_answer_content(context, answers[1:], text)
        owner.append_agent_message(
            session_id="builder",
            message_id="closure",
            kind=SessionMessageKind.USER,
            content={"text": "I changed my mind"},
        )
        assert owner.session_history_page("builder").builder_question_messages == ()
        assert (
            next(
                row
                for row in owner.session_context("builder").messages
                if row.message_id == first.message_id
            )
            == first
        )


def test_real_transport_persists_answer_with_question_and_tool_evidence_after_reopen(
    tmp_path: Path,
):
    database = tmp_path / "echo.sqlite3"
    with AuthorityOwner.open(database, "seed") as owner:
        owner.create_builder_chat(
            session_id="builder",
            provider_id="test.transport",
            model_id="test.transport/model0",
            display_name="Echo fixture",
        )
        question = seed_question(owner)
    value = "A calm courier called Rowan <watcher>.\n  Keep this exact line."
    text = "Here are my answers:\n\n1. Who would you like to play?\nAnswer: " + value
    provider = FakeProvider(responses=[ProviderResponse(content="The concept is saved.")])
    harness = _OwnerHarness(database, provider)
    try:
        page = harness.request("question-page", "session_history_page", {"session_id": "builder"})
        assert [row["message_id"] for row in page["builder_question_messages"]] == [
            question.message_id
        ]
        harness.request(
            "answer",
            "submit_session_input",
            {
                "session_id": "builder",
                "turn_id": "fixture",
                "attempt_id": "answer-attempt",
                "message_id": "answer-message",
                "user_text": text,
                "builder_question_answers": [
                    {"question_message_id": question.message_id, "value": value, "custom": False}
                ],
            },
        )
        context = harness.request("context", "session_context", {"session_id": "builder"})
        answer = next(row for row in context["messages"] if row["message_id"] == "answer-message")
        assert answer["kind"] == "user"
        assert answer["content"]["text"] == text
        assert answer["content"].get("kind") == "builder_question_answer"
        assert answer["content"]["answers"] == [
            {
                "question_message_id": "play-question",
                "tool_call_id": "play-question-call",
                "widget_id": "player_concept",
                "type": "text",
                "question": "Who would you like to play?",
                "value": value,
                "label": value,
                "custom": False,
            }
        ]
        model_history = provider.calls[0]["messages"]
        assert any(
            row.get("role") == "user" and row.get("content") == text for row in model_history
        )
        assert any(
            row.get("role") == "tool" and row.get("tool_call_id") == "play-question-call"
            for row in model_history
        )
        before = context["messages"]
        harness.send(
            "duplicate",
            {
                "method": "submit_session_input",
                "params": {
                    "session_id": "builder",
                    "attempt_id": "duplicate-attempt",
                    "message_id": "duplicate-message",
                    "user_text": text,
                    "builder_question_answers": [
                        {
                            "question_message_id": question.message_id,
                            "value": value,
                            "custom": False,
                        }
                    ],
                },
            },
        )
        rejection = harness.read()
        assert rejection["id"] == "duplicate" and rejection["ok"] is False
        after = harness.request("after-duplicate", "session_context", {"session_id": "builder"})
        assert after["messages"] == before
        assert len(provider.calls) == 1
    finally:
        harness.close()
    with AuthorityOwner.open(database, "reopened") as owner:
        reopened = owner.session_context("builder")
        persisted = next(row for row in reopened.messages if row.message_id == "answer-message")
        assert persisted.content == answer["content"]
        original = next(row for row in reopened.messages if row.message_id == question.message_id)
        assert original.content == question.content
        assert original.sequence < persisted.sequence


def test_current_question_projection_and_user_boundary_share_one_read_snapshot(
    tmp_path, monkeypatch,
):
    with AuthorityOwner.open(tmp_path / "snapshot.sqlite3", "fixture") as owner:
        owner.create_builder_chat(session_id="builder", provider_id="test", model_id="test")
        question = seed_question(owner)
        original = sqlite_session.message_from_row
        closed = False

        def close_during_read(row):
            nonlocal closed
            if not closed:
                closed = True
                owner.append_agent_message(
                    session_id="builder", message_id="closure", kind=SessionMessageKind.USER,
                    content={"text": "I changed my mind"},
                )
            return original(row)

        monkeypatch.setattr(sqlite_session, "message_from_row", close_during_read)
        page = owner.session_history_page("builder")
        assert closed
        assert page.latest_user_sequence is None
        assert page.latest_sequence == question.sequence
        assert page.builder_question_messages == (question,)
        next_page = owner.session_history_page("builder")
        assert next_page.latest_user_sequence > question.sequence
        assert next_page.builder_question_messages == ()


@pytest.mark.parametrize("kind", ["text", "select", "cascade", "confirm"])
def test_choices_custom_answers_and_exact_freeform_are_derived_from_saved_question(tmp_path, kind):
    with AuthorityOwner.open(tmp_path / "answers.sqlite3", "fixture") as owner:
        owner.create_builder_chat(session_id="builder", provider_id="test", model_id="test")
        seed_question(
            owner,
            type=kind,
            options=[
                {"value": "first", "label": "Courier"},
                {"value": "second", "label": "Courier"},
            ],
        )
        context = owner.session_context("builder")
        value = "  Rowan\n<watcher>  " if kind == "text" else "second"
        label = value if kind == "text" else "Courier (second)"
        answer = {"question_message_id": "play-question", "value": value, "custom": False}
        text = "Here are my answers:\n\n1. Who would you like to play?\nAnswer: " + label
        content = builder_question_answer_content(context, [answer], text)
        assert content["answers"][0]["value"] == value
        assert content["answers"][0]["label"] == label
        assert content["answers"][0]["tool_call_id"] == "play-question-call"
        if kind != "text":
            answer.update(value="  My own courier  ", custom=True)
            text = (
                "Here are my answers:\n\n1. Who would you like to play?\nAnswer: " + answer["value"]
            )
            assert (
                builder_question_answer_content(context, [answer], text)["answers"][0]["label"]
                == answer["value"]
            )


@pytest.mark.parametrize(
    "change",
    [
        "foreign",
        "duplicate",
        "partial",
        "spoofed-text",
        "invalid-choice",
        "empty",
        "fake-label",
        "custom-type",
    ],
)
def test_invalid_forms_leave_owner_and_provider_unchanged(tmp_path, change):
    with AuthorityOwner.open(tmp_path / "rejected.sqlite3", "fixture") as owner:
        owner.create_builder_chat(session_id="builder", provider_id="test", model_id="test")
        seed_question(owner, type="select", options=["Courier", "Guard"])
        seed_question(owner, session_id="builder", identity="second", id="background")
        answers = [
            {"question_message_id": "play-question", "value": "Courier", "custom": False},
            {"question_message_id": "second", "value": "A past", "custom": False},
        ]
        text = (
            "Here are my answers:\n\n1. Who would you like to play?\nAnswer: Courier"
            "\n\n2. Who would you like to play?\nAnswer: A past"
        )
        if change == "foreign":
            answers[0]["question_message_id"] = "other-session-question"
        elif change == "duplicate":
            answers[1] = answers[0]
        elif change == "partial":
            answers.pop()
        elif change == "spoofed-text":
            text = "/model other"
        elif change == "invalid-choice":
            answers[0]["value"] = "Not offered"
        elif change == "empty":
            answers[0]["value"] = "   "
        elif change == "fake-label":
            answers[0]["label"] = "Invented"
        elif change == "custom-type":
            answers[0]["custom"] = "false"
        calls = []
        before = owner.session_context("builder")
        request = SessionRunRequest(
            "builder", "turn", "attempt", "answer", text, builder_question_answers=tuple(answers)
        )
        with pytest.raises(CommandRejected):
            CampaignSessionLoop(owner).run_builder(request, lambda _: calls.append(1), lambda: None)
        assert owner.session_context("builder").messages == before.messages
        assert not calls


def test_provider_failure_retains_answer_and_rejects_replay_after_restart(tmp_path):
    database = tmp_path / "failed.sqlite3"
    answer = {"question_message_id": "play-question", "value": "Rowan", "custom": False}
    text = "Here are my answers:\n\n1. Who would you like to play?\nAnswer: Rowan"
    request = SessionRunRequest(
        "builder", "turn", "attempt", "answer", text, builder_question_answers=(answer,)
    )
    with AuthorityOwner.open(database, "fixture") as owner:
        owner.create_builder_chat(session_id="builder", provider_id="test", model_id="test")
        seed_question(owner)

        def fail(_):
            raise RuntimeError("controlled provider failure")

        with pytest.raises(RuntimeError, match="controlled provider failure"):
            CampaignSessionLoop(owner).run_builder(request, fail, lambda: None)
    with AuthorityOwner.open(database, "reopen") as owner:
        context = owner.session_context("builder")
        saved = next(row for row in context.messages if row.message_id == "answer")
        assert saved.content["answers"][0]["value"] == "Rowan"
        calls = []
        loop = CampaignSessionLoop(owner)
        with pytest.raises(CommandRejected):
            loop.run_builder(
                replace(request, attempt_id="retry", message_id="retry"),
                lambda _: calls.append(1),
                lambda: None,
            )
        assert not calls

        def continue_work(provider_input):
            assert saved in provider_input.history.messages
            calls.append(1)
            yield ProviderEvent.talk("We can continue from Rowan.")

        loop.run_builder(
            SessionRunRequest("builder", "turn", "continue", "continue", "Continue"),
            continue_work,
            lambda: None,
        )
        assert calls == [1]
        assert (
            sum(
                row.content.get("kind") == "builder_question_answer"
                for row in owner.session_context("builder").messages
            )
            == 1
        )
