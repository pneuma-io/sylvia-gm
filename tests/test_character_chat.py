"""Direct character chat: card selection, in-character requests, saving and reopening."""

from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path

import pytest
from sylviabench.owner import OwnerClient, OwnerError

from sylvia_gm.application import character_chat as chat_module
from sylvia_gm.application.character_chat import (
    chat_catalog,
    chat_projection,
    collect_reply,
    create_chat,
    load_chat,
    persona_prompt,
    request_messages,
    send_message,
)
from sylvia_gm.application.character_profiles import save_character_profile
from sylvia_gm.domain._content_schema_character_profile import EXAMPLE as V1_EXAMPLE
from sylvia_gm.domain._content_schema_character_profile_v2 import EXAMPLE as V2_EXAMPLE
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.interfaces import owner_process
from sylvia_gm.provider._base import FakeProvider, ProviderStreamChunk

PROVIDER, MODEL = "synthetic-provider", "synthetic-model"
V2_CARD = {
    **V2_EXAMPLE,
    "greetings": {"default": "*looks up from a ledger* Hello, {{user}}. I am {{char}}."},
    "biography": {**V2_EXAMPLE["biography"], "personality": "Inquisitive; answers {{user}} dryly."},
}


def _card(root: Path, card: dict | None = None, operation: str = "card") -> tuple[str, str]:
    saved = save_character_profile(
        root, operation_id=operation, source=canonical_json_bytes(card or V2_CARD),
    )
    return saved.artifact_id, saved.content_sha256


def _chat(root: Path, card: dict | None = None, **changes):
    artifact_id, digest = _card(root, card)
    return create_chat(
        root, operation_id=changes.pop("operation_id", "start-1"), artifact_id=artifact_id,
        expected_content_sha256=digest, provider_id=PROVIDER, model_id=MODEL, **changes,
    )


def test_card_persona_is_reference_data_with_card_placeholders_filled():
    prompt = persona_prompt(V2_CARD, user_name="Mira")
    assert prompt.startswith("You are Vela. You are talking one-to-one with Mira")
    for expected in ("Carries a worn notebook.", "answers Mira dryly", "Grey coat.",
                     "Dry and measured.", "The Lantern", "Elian - Former mentor.",
                     "Ask before touching fragile books.", "Vela: A story needs evidence."):
        assert expected in prompt
    # Foreign prompt overrides and inactive lore stay inert.
    assert "Ignore prior rules" not in prompt
    assert "An old archive." not in prompt
    assert "{{" not in prompt
    v1 = persona_prompt(V1_EXAMPLE, user_name="You")
    assert "A loyal archivist with a dry wit" in v1
    assert "An inquisitive travelling archivist." in v1


def test_new_chat_opens_with_the_card_greeting_and_repeats_idempotently(tmp_path):
    chat = _chat(tmp_path, user_name="Mira")
    assert [(m.role, m.text) for m in chat.messages] == [
        ("character", "*looks up from a ledger* Hello, Mira. I am Vela."),
    ]
    assert _chat(tmp_path, user_name="Mira") == chat
    v1_chat = _chat(tmp_path / "v1", V1_EXAMPLE)
    assert v1_chat.messages == () and v1_chat.user_name == "You"
    catalog = chat_catalog(tmp_path)
    assert [card["name"] for card in catalog["cards"]] == ["Vela"]
    assert catalog["cards"][0]["has_greeting"] is True
    assert [row["chat_id"] for row in catalog["chats"]] == [chat.chat_id]
    with pytest.raises(CommandRejected) as stale:
        create_chat(tmp_path, operation_id="start-2", artifact_id=chat.artifact_id,
                    expected_content_sha256="0" * 64, provider_id=PROVIDER, model_id=MODEL)
    assert stale.value.code == "stale_character_profile"


def test_send_saves_both_messages_reopens_and_retries_safely(tmp_path):
    chat = _chat(tmp_path)
    seen = []

    def reply(_chat, _profile, messages):
        seen.append(messages)
        return "Vela: Evidence first, {{user}}. What brings you here?"

    updated = send_message(tmp_path, chat_id=chat.chat_id, operation_id="send-1",
                           text="  Hi Vela!  ", expected_message_count=1, generate=reply)
    roles = [message["role"] for message in seen[0]]
    assert roles == ["system", "user", "assistant", "user"]
    assert seen[0][1]["content"] == "(The conversation begins.)"
    assert seen[0][-1]["content"] == "Hi Vela!"
    assert [(m.sequence, m.role, m.text) for m in updated.messages][1:] == [
        (2, "user", "Hi Vela!"),
        (3, "character", "Evidence first, {{user}}. What brings you here?"),
    ]
    # Reopen reads the same chat back from disk.
    assert load_chat(tmp_path, chat.chat_id) == updated
    # A repeated send (lost reply) returns the saved chat without asking the model again.
    assert send_message(tmp_path, chat_id=chat.chat_id, operation_id="send-1", text="Hi Vela!",
                        expected_message_count=1, generate=reply) == updated
    assert len(seen) == 1
    with pytest.raises(CommandRejected) as stale:
        send_message(tmp_path, chat_id=chat.chat_id, operation_id="send-2", text="Again",
                     expected_message_count=1, generate=reply)
    assert stale.value.code == "character_chat_stale"


def test_failed_or_empty_reply_saves_nothing(tmp_path):
    chat = _chat(tmp_path)

    def fail(*_):
        raise RuntimeError("provider down")

    with pytest.raises(RuntimeError):
        send_message(tmp_path, chat_id=chat.chat_id, operation_id="send-1", text="Hello",
                     expected_message_count=1, generate=fail)
    with pytest.raises(CommandRejected) as empty:
        send_message(tmp_path, chat_id=chat.chat_id, operation_id="send-1", text="Hello",
                     expected_message_count=1, generate=lambda *_: "Vela:   ")
    assert empty.value.code == "character_chat_empty_reply"
    assert load_chat(tmp_path, chat.chat_id) == chat
    for bad in ("", "   ", "x" * 8001, None):
        with pytest.raises(CommandRejected):
            send_message(tmp_path, chat_id=chat.chat_id, operation_id="send-2", text=bad,
                         expected_message_count=1, generate=lambda *_: "ok")


def test_one_reply_at_a_time_per_chat(tmp_path):
    chat = _chat(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def slow(*_):
        entered.set()
        release.wait(5)
        return "Done."

    worker = threading.Thread(target=send_message, args=(tmp_path,), kwargs={
        "chat_id": chat.chat_id, "operation_id": "a", "text": "One",
        "expected_message_count": 1, "generate": slow,
    })
    worker.start()
    assert entered.wait(5)
    try:
        with pytest.raises(CommandRejected) as busy:
            send_message(tmp_path, chat_id=chat.chat_id, operation_id="b", text="Two",
                         expected_message_count=1, generate=lambda *_: "x")
        assert busy.value.code == "character_chat_busy"
    finally:
        release.set()
        worker.join(5)
    assert len(load_chat(tmp_path, chat.chat_id).messages) == 3


def test_removed_card_leaves_a_readable_read_only_chat(tmp_path):
    chat = _chat(tmp_path)
    shutil.rmtree(tmp_path / "character-profiles" / chat.artifact_id)
    projection = chat_projection(tmp_path, load_chat(tmp_path, chat.chat_id))
    assert projection["card_available"] is False
    assert projection["messages"][0]["role"] == "character"
    with pytest.raises(CommandRejected) as unavailable:
        send_message(tmp_path, chat_id=chat.chat_id, operation_id="send", text="Hi",
                     expected_message_count=1, generate=lambda *_: "x")
    assert unavailable.value.code == "character_card_unavailable"


def test_history_keeps_recent_messages_within_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_module, "HISTORY_BUDGET_CHARS", 50)
    chat = _chat(tmp_path)
    for index in range(4):
        chat = send_message(tmp_path, chat_id=chat.chat_id, operation_id=f"s{index}",
                            text=f"question {index} " + "x" * 10,
                            expected_message_count=len(chat.messages),
                            generate=lambda *_: "answer " + "y" * 10)
    messages = request_messages(chat, V2_CARD, "latest")
    contents = [message["content"] for message in messages[1:]]
    assert contents[-1] == "latest"
    assert len(contents) < len(chat.messages) + 1
    assert messages[1]["role"] == "user"
    assert "question 3" in contents[-3]


def test_damaged_chat_files_are_counted_not_fatal(tmp_path):
    chat = _chat(tmp_path)
    (tmp_path / "character-chats" / ("character-chat-" + "0" * 24 + ".json")).write_text("{")
    catalog = chat_catalog(tmp_path)
    assert [row["chat_id"] for row in catalog["chats"]] == [chat.chat_id]
    assert catalog["unreadable_count"] == 1
    with pytest.raises(CommandRejected):
        load_chat(tmp_path, "../escape")


def test_collect_reply_joins_streamed_text_without_reasoning():
    import asyncio

    provider = FakeProvider(stream_chunks=[
        ProviderStreamChunk(reasoning_content="private"),
        ProviderStreamChunk(content="Hello "),
        ProviderStreamChunk(content="there."),
    ])
    text = asyncio.run(collect_reply(provider, [{"role": "user", "content": "Hi"}],
                                     request_options={}))
    assert text == "Hello there."
    assert provider.calls[0]["tools"] is None


class _Resolver:
    """A scripted model: replies in character and records exactly what it was sent."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    def model_choices(self):
        return [{"provider_id": PROVIDER, "model_id": MODEL, "name": "Synthetic"}]

    def __call__(self, session):
        assert (session.provider_id, session.model_id) == (PROVIDER, MODEL)
        resolver = self

        class Scripted(FakeProvider):
            async def generate_stream(self, messages, tools=None, timeout=120.0, **kwargs):
                resolver.requests.append({"messages": messages, "tools": tools, **kwargs})
                yield ProviderStreamChunk(content=resolver.replies.pop(0))
                yield ProviderStreamChunk(finish_reason="stop")

        return Scripted()


def test_owner_protocol_select_converse_restart_and_reopen(tmp_path, monkeypatch):
    # Character chat needs no Ruleset, so the bundled package installs are skipped.
    monkeypatch.setattr(owner_process, "_preinstall_bundled_srd", lambda _bus: None)
    monkeypatch.setattr(owner_process, "_preinstall_bundled_riichi", lambda _bus: None)
    database = tmp_path / "owner" / "campaign.sqlite3"
    database.parent.mkdir()
    artifact_id, digest = _card(database.parent)
    resolver = _Resolver(["*smiles* Welcome to the archive.", "We keep every story here."])
    owner = OwnerClient(database, resolver, timeout=60)
    try:
        catalog = owner.request("character_chat", {"action": "catalog"})
        assert [card["artifact_id"] for card in catalog["cards"]] == [artifact_id]
        with pytest.raises(OwnerError) as no_model:
            owner.request("character_chat", {
                "action": "create", "operation_id": "op", "artifact_id": artifact_id,
                "expected_content_sha256": digest, "provider_id": PROVIDER, "model_id": "other",
            })
        assert no_model.value.error["code"] == "character_chat_model_unavailable"
        chat = owner.request("character_chat", {
            "action": "create", "operation_id": "op", "artifact_id": artifact_id,
            "expected_content_sha256": digest, "provider_id": PROVIDER, "model_id": MODEL,
            "user_name": "Mira",
        })
        assert chat["card_available"] is True
        first = owner.request("send_character_chat_message", {
            "chat_id": chat["chat_id"], "operation_id": "m1", "text": "Hello?",
            "expected_message_count": 1,
        })
        second = owner.request("send_character_chat_message", {
            "chat_id": chat["chat_id"], "operation_id": "m2", "text": "What is this place?",
            "expected_message_count": len(first["messages"]),
        })
    finally:
        owner.close()
    assert [m["text"] for m in second["messages"]] == [
        "*looks up from a ledger* Hello, Mira. I am Vela.", "Hello?",
        "*smiles* Welcome to the archive.", "What is this place?",
        "We keep every story here.",
    ]
    request = resolver.requests[1]
    assert request["tools"] is None
    assert request["messages"][0]["role"] == "system"
    assert "You are Vela" in request["messages"][0]["content"]
    assert [m["content"] for m in request["messages"][1:]] == [
        "(The conversation begins.)", "*looks up from a ledger* Hello, Mira. I am Vela.",
        "Hello?", "*smiles* Welcome to the archive.", "What is this place?",
    ]
    # A fresh owner process reopens the saved chat from disk.
    reopened_owner = OwnerClient(database, _Resolver([]), timeout=60)
    try:
        listed = reopened_owner.request("character_chat", {"action": "catalog"})
        assert [row["chat_id"] for row in listed["chats"]] == [chat["chat_id"]]
        assert listed["chats"][0]["message_count"] == 5
        reopened = reopened_owner.request(
            "character_chat", {"action": "open", "chat_id": chat["chat_id"]},
        )
    finally:
        reopened_owner.close()
    assert reopened["messages"] == second["messages"]
    saved = json.loads((database.parent / "character-chats" / f"{chat['chat_id']}.json")
                       .read_text(encoding="utf-8"))
    assert saved["format_id"] == "sylvia-gm-character-chat"
    assert saved["character"]["artifact_id"] == artifact_id


def test_a_chat_too_large_to_read_back_is_never_written(tmp_path, monkeypatch):
    chat = _chat(tmp_path)
    monkeypatch.setattr(chat_module, "MAX_FILE_BYTES", len(
        (tmp_path / "character-chats" / f"{chat.chat_id}.json").read_bytes()) + 50)
    with pytest.raises(CommandRejected) as large:
        send_message(tmp_path, chat_id=chat.chat_id, operation_id="big", text="Hello",
                     expected_message_count=1, generate=lambda *_: "y" * 500)
    assert large.value.code == "character_chat_limit"
    assert load_chat(tmp_path, chat.chat_id) == chat
