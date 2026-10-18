"""First-turn titles share the real Builder request, history and metadata owners."""

import asyncio
import threading

import pytest

from sylvia_gm.agent._builder_title import TITLE_PREFIX, FirstReplyTitle, opening_title
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.provider._base import FakeProvider, ProviderResponse, ProviderStreamChunk
from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness

OPENING = (
    "Help me design a campaign about a lighthouse keeper who discovers that "
    "the ships arriving in the harbour have travelled backwards through time."
)
REPLY = "<!-- sylvia-chat-title: The Lighthouse Beyond Time -->\nLet's build the harbour."


def create_chat(database):
    with AuthorityOwner.open(database, "seed-title-chat") as owner:
        owner.create_builder_chat(session_id="chat", provider_id="test", model_id="test")


def submit(harness, text=OPENING):
    harness.send("reply", {"method": "submit_session_input", "params": {
        "session_id": "chat", "attempt_id": "first-reply", "message_id": "opening",
        "user_text": text,
    }})


@pytest.mark.parametrize("opening,response,expected", [
    (OPENING, REPLY, "The Lighthouse Beyond Time"),
    (OPENING, "Let's build the harbour.", opening_title(OPENING)),
    ("  Plan a lighthouse mystery.  ", "Let's build it.", "Plan a lighthouse mystery."),
])
def test_first_reply_title_persists_without_an_extra_request(tmp_path, opening, response, expected):
    database = tmp_path / "titles.sqlite3"
    create_chat(database)
    provider = FakeProvider(stream_chunks=[ProviderStreamChunk(content=char) for char in response])
    harness = _OwnerHarness(database, provider)
    try:
        submit(harness, opening)
        assert harness.read()["ok"]
        assert len(provider.calls) == 1
        assert (TITLE_PREFIX in str(provider.calls[0]["messages"])) == (opening == OPENING)
        context = harness.request("history", "session_context", {"session_id": "chat"})
        talk = "".join(m["content"]["text"] for m in context["messages"] if m["kind"] == "talk")
        assert talk == response.split("-->\n", 1)[-1]
        assert TITLE_PREFIX not in talk
    finally:
        harness.close()
    with AuthorityOwner.open(database, "reopen-title-chat") as owner:
        assert owner.builder_chat_index()[0].display_name == expected


class HeldTitleProvider(FakeProvider):
    """Pause a title-bearing response so user rename, Stop and failure can win."""

    def __init__(self, *, fail=False):
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()
        self.fail = fail

    async def generate_stream(self, messages, tools=None, **kwargs):
        self.calls.append({"messages": messages, "tools": tools})
        yield ProviderStreamChunk(content=REPLY.split("\n")[0])
        self.started.set()
        while not self.release.is_set():
            await asyncio.sleep(0.01)
        if self.fail:
            raise RuntimeError("Synthetic provider failure")
        yield ProviderStreamChunk(content="\nLet's build the harbour.", finish_reason="stop")


@pytest.mark.parametrize("action", ["rename", "stop", "failure"])
def test_first_reply_title_respects_manual_names_and_incomplete_turns(tmp_path, action):
    database = tmp_path / "titles.sqlite3"
    create_chat(database)
    provider = HeldTitleProvider(fail=action == "failure")
    harness = _OwnerHarness(database, provider)
    try:
        submit(harness)
        assert provider.started.wait(5)
        initial = harness.request("before-title", "builder_chat_index")
        assert initial[0]["display_name"] == opening_title(OPENING)
        if action == "rename":
            harness.request("rename", "rename_builder_chat", {
                "session_id": "chat", "display_name": "My harbour notes",
            })
        elif action == "stop":
            harness.request("stop", "interrupt_provider_stream", {
                "campaign_id": "chat", "attempt_id": "first-reply",
            })
        provider.release.set()
        reply = harness.read()
        assert reply["id"] == "reply"
        assert reply["ok"] == (action == "rename")
        assert len(provider.calls) == 1
    finally:
        provider.release.set()
        harness.close()
    with AuthorityOwner.open(database, "reopen-title-chat") as owner:
        assert owner.builder_chat_index()[0].display_name == (
            "My harbour notes" if action == "rename" else opening_title(OPENING)
        )


@pytest.mark.parametrize("response,expected", [
    ("<!-- sylvia-chat-title: " + "word " * 30 + "-->\nAnswer", "Answer"),
    ("<!-- sylvia-chat-title: unfinished\nAnswer", "Answer"),
    ("<ordinary prose", "<ordinary prose"),
])
def test_missing_or_malformed_title_keeps_the_answer(response, expected):
    title = FirstReplyTitle()
    answer = title.feed(response) + title.feed("", final=True)
    assert answer == expected
    assert title.title is None


def test_later_turn_does_not_request_a_title_for_existing_history(tmp_path):
    database = tmp_path / "titles.sqlite3"
    create_chat(database)
    provider = FakeProvider(responses=[ProviderResponse(content="Here is the plan.")])
    harness = _OwnerHarness(database, provider)
    try:
        submit(harness)
        assert harness.read()["ok"]
        harness.request("second", "submit_session_input", {
            "session_id": "chat", "attempt_id": "second", "message_id": "second",
            "user_text": "Expand the mystery.",
        })
        assert len(provider.calls) == 2
        assert TITLE_PREFIX not in str(provider.calls[1]["messages"])
    finally:
        harness.close()
