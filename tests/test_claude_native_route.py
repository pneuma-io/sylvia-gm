"""Claude runtime route under the context ledger: native transcript, CLI records, cleanup.

No CLI and no model call: the transcript is checked on scripted ledger journals, collection on
session files copied from offline captures, and the provider against a scripted process that
writes its session file the way Claude Code 2.1.322 does.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import sys
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from sylvia_gm.agent._context_ledger import (
    CONTINUE_LABEL,
    JOURNAL_KIND,
    LEDGER_KEY,
    NATIVE_KIND,
    STABLE_KEY,
    ContextLedger,
    native_gap,
    native_record,
    tagged,
)
from sylvia_gm.agent._provider_session import ProviderSessionAdapter
from sylvia_gm.provider import _claude
from sylvia_gm.provider._base import ModelInfo
from sylvia_gm.provider._claude import ClaudeProvider, prepare_request
from sylvia_gm.provider._claude_native import (
    CURRENT_NATIVE_ROUTE,
    FILLER_TEXT,
    TRANSIENT_SESSION,
    NativeCollection,
    NativeRoute,
    collect,
    sweep,
    wait_for_flush,
)
from sylvia_gm.provider._exceptions import ProviderError, ProviderTimeoutError
from tests.gm_context_contract import (
    NATIVE_FIXTURES,
    first_step_records,
    provider_messages,
    serialize,
)

OWNER = {"kind": "gm", "campaign_id": "c", "session_id": "s"}
MODEL = "claude-opus-5-5"
TOOLS = [{"type": "function", "function": {
    "name": "roll", "description": "Roll dice with Sylvia's engine.",
    "parameters": {"type": "object", "properties": {"n": {"type": "integer"}}}}}]
STABLE = [
    tagged({"role": "system", "content": "GM CONTRACT\nNarrate in second person.\n"},
           **{STABLE_KEY: True}),
    tagged({"role": "system", "content": "SYLVIA_GM_RULESET_OPERATING_GUIDE\nRoll for risk."},
           **{STABLE_KEY: True}),
]
PREP = {"role": "system", "content": "SYLVIA_GM_CAMPAIGN_PREPARATION (data):\n"
        + json.dumps({"campaign": "Glasswater Hollow"})}
FACTS_HEAD = ("SYLVIA_GM_EXACT_NARRATION_FACTS (committed mechanics and application-owned "
              "continuation control):\n")


def state(revision: int, hp: int = 7) -> dict[str, Any]:
    body = {"campaign": {"revision": revision},
            "campaign_state": {"ruleset_owned_values": {"entities": {"hero": {"hp": hp}}}},
            "PLAYER_KNOWN": {"current_facts": [], "revealed_facts": []},
            "GM_PRIVATE_CURRENT": {"character_knowledge": {}, "current_facts": []}}
    return {"role": "system", "content": "SYLVIA_GM_FRESH_CAMPAIGN_STATE (data):\n"
            + json.dumps(body, sort_keys=True, separators=(",", ":"))}


def facts(*items: str) -> dict[str, Any]:
    rows = [{"tool": "roll", "detail": item} for item in items]
    return {"role": "system", "content": FACTS_HEAD + json.dumps(rows, separators=(",", ":"))
            + "\nContinue this same GM turn from these committed results."}


def player(n: int, text: str) -> dict[str, Any]:
    return tagged({"role": "user", "content": text}, **{LEDGER_KEY: f"user:u{n}"})


def talk(n: int, text: str) -> dict[str, Any]:
    return tagged({"role": "assistant", "content": text}, **{LEDGER_KEY: f"talk:t{n}"})


def call(call_id: str, n: int) -> dict[str, Any]:
    return {"role": "assistant", "content": None, "tool_calls": [{
        "id": call_id, "type": "function",
        "function": {"name": "roll", "arguments": json.dumps({"n": n})}}]}


def result(call_id: str, text: str) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": call_id, "content": text}


def chain_canonicals() -> list[tuple[str, list[dict[str, Any]]]]:
    """One GM session in steps, each the canonical list for one provider request."""
    one = [player(1, "I open the cellar door.")]
    two = [*one, talk(1, "The door creaks."), player(2, "I go down the stairs carefully.")]
    three = [*two, call("c1", 1), result("c1", "Rolled 14.")]
    four = [*three, call("c2", 2), result("c2", "Rolled 3.")]
    five = [*four, talk(2, "You reach the bottom."), player(3, "I light my lantern.")]
    six = [*five, talk(3, "Light floods the room."), player(4, "We move on.")]
    return [
        ("fresh", [*STABLE, PREP, state(1), *one]),
        ("player", [*STABLE, PREP, state(1), *two]),
        ("tool_result_with_records", [*STABLE, PREP, state(2, 6), *three, facts("14")]),
        ("tool_result_no_records", [*STABLE, PREP, state(2, 6), *four, facts("14")]),
        ("consecutive_user_entries", [*STABLE, PREP, state(3, 5), *five]),
        ("after_date_change", [*STABLE, PREP, state(3, 5), *six]),
    ]


DATE_CHANGE = {"attachment": {"type": "date", "date": "2026-11-15", "changed": True},
               "rendered": [{"content": "<system-reminder>\nThe date has changed. Today's date "
                             "is now 2026-11-15.\n</system-reminder>"}],
               "renderedRole": "system"}


class Chain:
    """Plan, commit and project like the GM adapter does on claude_code; no provider."""

    def __init__(self, transport: str = "claude_code", session: str = "s") -> None:
        self.ledger = ContextLedger(session_id=session, context_owner=OWNER, transport=transport)
        self.frames: list[Any] = []
        self.routes: list[NativeRoute | None] = []

    def step(self, canonical, collected: NativeCollection | None = None, records=()):
        frame = self.ledger.plan(canonical, request_context_id="ctx",
                                 history_records=lambda: list(records), tools=TOOLS,
                                 check_saved_tip=False)
        self.ledger.commit(frame)
        self.frames.append(frame)
        route = None
        if self.ledger.transport == "claude_code":
            # The adapter's own route builder (persistent=False: no owner to save to).
            route = ProviderSessionAdapter._claude_native_route(self.ledger, frame, None, False)
            if collected is None and route.first_step:
                collected = NativeCollection("collected", tuple(first_step_records()))
            if collected is not None:
                asyncio.run(route.on_collected(collected))
        self.routes.append(route)
        return frame, route


def project(frame, route, workspace=Path("C:/runtime/workspace")):
    return prepare_request(provider_messages(frame.wire), TOOLS, MODEL, workspace, route)


def view(frame, route) -> list[str]:
    return serialize("claude_code", frame.wire, TOOLS, native=route)[1]


def texts(content) -> list[str]:
    return [block.get("text") for block in content]


def test_native_transcript_extends_every_step_with_fillers_records_and_list_content():
    chain = Chain()
    steps = chain_canonicals()
    views, transcripts = [], []
    for name, canonical in steps:
        collected = (NativeCollection("collected", (DATE_CHANGE,))
                     if name == "consecutive_user_entries" else None)
        frame, route = chain.step(canonical, collected)
        assert frame.reset in (None, "journal_started"), (name, frame.reset)
        system, records, prompt, native_tools = project(frame, route)
        # Every user record and the prompt use list content; nothing is flagged synthetic.
        assert "isSynthetic" not in prompt and prompt["type"] == "user"
        assert isinstance(prompt["message"]["content"], list)
        for record in records:
            if record["type"] == "user":
                assert isinstance(record["message"]["content"], list), (name, record)
            assert "isSynthetic" not in record and record["cwd"] == str(
                Path("C:/runtime/workspace"))
        assert "SYLVIA_GM_RUNTIME_CONTINUE" not in json.dumps([records, prompt])
        views.append(view(frame, route))
        transcripts.append(records)
    # Transcript N is a prefix of transcript-plus-prompt N+1, and so is N's own view.
    for index in range(1, len(views)):
        before, after = views[index - 1], views[index]
        assert after[: len(before)] == before, steps[index][0]
        assert after[: len(views[index - 1]) - 1] == before[:-1]
    names = [name for name, _ in steps]
    by = dict(zip(names, zip(transcripts, views, strict=True), strict=True))

    # Fresh step: no transcript, the whole first group is the stdin prompt.
    fresh_records, fresh_view = by["fresh"]
    assert fresh_records == []
    first_prompt = json.loads(fresh_view[-1])["content"]
    assert "SYLVIA_GM_CAMPAIGN_PREPARATION" in first_prompt[0]["text"]
    assert any(t["text"] == "I open the cellar door." for t in first_prompt)

    # Player prompt: step 1's prompt is one user record followed by the CLI's own records.
    player_records, _ = by["player"]
    assert player_records[0]["type"] == "user"
    assert player_records[0]["message"]["content"] == first_prompt
    assert [r["attachment"]["type"] for r in player_records[1:4]] == [
        "environment", "model", "date"]
    assert player_records[4]["type"] == "assistant"

    # Continuation after a tool result with records: filler between result and the group.
    tool_records, tool_view = by["tool_result_with_records"]
    kinds = [(r["type"], r["message"]["content"][0].get("type")) if r["type"] != "attachment"
             else ("attachment", None) for r in tool_records]
    assert kinds[-3:] == [("assistant", "tool_use"), ("user", "tool_result"),
                          ("assistant", "text")]
    assert tool_records[-1]["message"]["content"] == [{"type": "text", "text": FILLER_TEXT}]
    assert tool_records[-2]["message"]["content"][0]["tool_use_id"] == "c1"
    group = texts(json.loads(tool_view[-1])["content"])
    assert any("SYLVIA_GM_EXACT_NARRATION_FACTS" in t for t in group)

    # Continuation with no records: the ledger's journaled continue record is the prompt.
    _records, no_records_view = by["tool_result_no_records"]
    group = texts(json.loads(no_records_view[-1])["content"])
    assert len(group) == 1 and CONTINUE_LABEL in group[0]
    assert json.loads(no_records_view[-2])["content"] == [{"type": "text", "text": FILLER_TEXT}]

    # Consecutive user-role entries form one prompt and later one record with one block each.
    _records, consecutive_view = by["consecutive_user_entries"]
    group = texts(json.loads(consecutive_view[-1])["content"])
    assert len(group) >= 2 and group[0] == "I light my lantern."
    last_records, last_view = by["after_date_change"]
    grouped = [r for r in last_records if r["type"] == "user"
               and texts(r["message"]["content"]) == group]
    assert len(grouped) == 1
    after_group = last_records[last_records.index(grouped[0]) + 1]
    assert after_group["type"] == "attachment" and after_group["attachment"]["changed"] is True

    # Collected attachments replay unchanged right after their prompt in every later step.
    def copied(records):
        return [{k: r[k] for k in ("attachment", "rendered", "renderedRole")}
                for r in records if r["type"] == "attachment"]

    assert copied(transcripts[1]) == first_step_records()
    for records in transcripts[2:]:
        assert copied(records)[:3] == first_step_records()


def test_a_failed_step_is_resent_unchanged_and_later_work_follows_with_a_filler():
    chain = Chain()
    steps = chain_canonicals()
    frame, route = chain.step(steps[0][1])
    frame2, route2 = chain.step(steps[1][1])
    # The step failed before any reply was committed: the same canonical comes back.
    frame3, route3 = chain.step(steps[1][1])
    assert frame3.appended == [] and route3.boundaries == route2.boundaries
    assert view(frame3, route3) == view(frame2, route2)
    # Then the player adds more without a reply in between: the unanswered prompt stays a
    # record and Sylvia's filler separates it from the new prompt.
    later = [*steps[1][1], player(9, "Hello?")]
    frame4, route4 = chain.step(later)
    records, prompt = project(frame4, route4)[1:3]
    assert records[-1]["message"]["content"] == [{"type": "text", "text": FILLER_TEXT}]
    assert texts(records[-2]["message"]["content"])[-1] == "I go down the stairs carefully."
    assert texts(prompt["message"]["content"]) == ["Hello?"]
    assert view(frame4, route4)[: len(view(frame3, route3))] == view(frame3, route3)


def test_a_session_that_moves_between_routes_stays_correct_on_each():
    steps = chain_canonicals()
    chat = Chain(transport="chat_completions")
    for _name, canonical in steps[:3]:
        frame, route = chat.step(canonical)
        assert route is None
        wire = json.dumps(provider_messages(frame.wire))
        assert FILLER_TEXT not in wire and "attachment" not in wire
    # The adapter starts a new ledger when the transport changes and keeps the old state
    # only so the reset is visible: a new epoch, a full send.
    claude = Chain()
    claude.ledger.state = chat.ledger.state
    frame, route = claude.step(steps[3][1])
    assert frame.reset == "epoch_changed" and frame.base_count == 0 and route.first_step
    records, prompt = project(frame, route)[1:3]
    assert not any(r["type"] == "attachment" for r in records)
    assert texts(prompt["message"]["content"])[0].startswith("[host-seal")
    frame2, route2 = claude.step(steps[4][1])
    assert frame2.reset is None
    assert view(frame2, route2)[: len(view(frame, route))] == view(frame, route)
    # And back: the other route never sees Claude Code's records or fillers.
    back = Chain(transport="chat_completions")
    back.ledger.state = claude.ledger.state
    frame3, _ = back.step(steps[5][1])
    wire = json.dumps(provider_messages(frame3.wire))
    assert frame3.reset == "epoch_changed"
    assert FILLER_TEXT not in wire and "environment" not in wire


def test_unknown_native_records_start_a_new_generation_on_the_next_request():
    steps = chain_canonicals()
    chain = Chain()
    chain.step(steps[0][1])
    frame, _ = chain.step(steps[1][1], NativeCollection("unknown", reason="cli_inserted_turn"))
    assert frame.reset is None
    frame2, route2 = chain.step(steps[2][1])
    assert frame2.reset == "native_records_unknown"
    assert frame2.state.generation == frame.state.generation + 1 and frame2.base_count == 0
    assert route2.first_step
    # A resend of the same request does not replay the unknown records, so it is not reset.
    retry = Chain()
    retry.step(steps[0][1])
    first, _ = retry.step(steps[1][1], NativeCollection("unknown", reason="session_file_missing"))
    again, _ = retry.step(steps[1][1])
    assert again.reset is None and again.state.generation == first.state.generation


def test_saved_native_records_rebuild_after_restart_and_lost_ones_reset():
    steps = chain_canonicals()
    chain = Chain()
    saved: list[dict[str, Any]] = []
    for _name, canonical in steps[:3]:
        frame, route = chain.step(canonical)
        if frame.record is not None:
            saved.append(frame.record)
        if route.first_step:
            saved.append(native_record(frame.state, context_owner=OWNER,
                                       boundary=len(frame.state.entries), status="collected",
                                       reason=None, records=first_step_records()))
    assert [r["kind"] for r in saved][:2] == [JOURNAL_KIND, NATIVE_KIND]
    before = view(chain.frames[-1], chain.routes[-1])
    # A new owner: the journal and the CLI's records come back from saved evidence.
    restarted = Chain()
    frame, route = restarted.step(steps[3][1], records=saved)
    assert frame.reset is None and frame.state.generation == chain.frames[-1].state.generation
    assert view(frame, route)[: len(before)] == before
    # Without the native evidence (lost in a crash) the next request starts a new generation.
    lost = Chain()
    frame, route = lost.step(steps[3][1],
                             records=[r for r in saved if r["kind"] == JOURNAL_KIND])
    assert frame.reset == "native_records_unknown" and frame.base_count == 0
    assert native_gap(chain.frames[-1].state, len(chain.frames[-1].state.entries) + 1) is False


def test_native_route_must_match_the_wire():
    chain = Chain()
    frame, route = chain.step(chain_canonicals()[0][1])
    wrong = NativeRoute(stable_count=route.stable_count, entry_count=route.entry_count + 1)
    with pytest.raises(ProviderError):
        project(frame, wrong)


# ---------------------------------------------------------------- collection (acceptance 2)


def test_collection_copies_rendered_context_records_after_the_prompt():
    first = collect(NATIVE_FIXTURES / "first-step.jsonl", "SENTINEL_H1_USER hello")
    assert first.status == "collected"
    assert [r["attachment"]["type"] for r in first.records] == ["environment", "model", "date"]
    environment = first.records[0]
    assert environment["renderedRole"] == "system"
    assert set(environment["attachment"]["snapshot"]) == {
        "workingDirectory", "isWorktree", "isGitRepo", "additionalWorkingDirectories",
        "platform", "shell", "osVersion"}
    assert "# Environment" in environment["rendered"][0]["content"]
    assert first.records[1]["attachment"]["identity"]["modelId"] == "claude-opus-5-5"
    # Bookkeeping (session_context, prompt_snapshot, api-request copies) is never copied.
    assert all(r["attachment"]["type"] not in {"session_context", "prompt_snapshot"}
               for r in first.records)
    ordinary = collect(NATIVE_FIXTURES / "ordinary-step.jsonl", "SENTINEL_H2_USER next")
    assert ordinary == NativeCollection("collected", ())
    changed = collect(NATIVE_FIXTURES / "date-change.jsonl",
                      [{"type": "text", "text": "SENTINEL_DC2 today"}])
    assert changed.status == "collected" and len(changed.records) == 1
    assert changed.records[0]["attachment"] == {"type": "date", "date": "2026-11-14",
                                                "changed": True}


def test_collection_reports_unknown_instead_of_failing(tmp_path):
    inserted = collect(NATIVE_FIXTURES / "cli-inserted-turn.jsonl", "SENTINEL_O4_USER what now")
    assert inserted == NativeCollection("unknown", reason="cli_inserted_turn")
    assert collect(None, "x").reason == "session_file_missing"
    assert collect(tmp_path / "absent.jsonl", "x").reason == "session_file_missing"
    broken = tmp_path / "broken.jsonl"
    broken.write_bytes(b'{"type":"user"\n')
    assert collect(broken, "x").reason == "session_file_unreadable"
    assert collect(NATIVE_FIXTURES / "first-step.jsonl", "never sent").reason == (
        "prompt_record_missing")


def test_the_cli_may_finish_writing_its_records_before_it_is_closed(tmp_path):
    """Closing at a tool call must not lose the prompt and context records still queued."""
    import time

    config = tmp_path / "profile"
    session = "cccccccc-0000-4000-8000-000000000003"
    folder = config / "projects" / "C--runtime-workspace"
    folder.mkdir(parents=True)
    path = folder / f"{session}.jsonl"
    prompt = [{"type": "text", "text": "SENTINEL roll"}]
    rows = [{"type": "user", "message": {"role": "user", "content": prompt}},
            {"type": "attachment", "attachment": {"type": "date", "date": "2026-11-15"},
             "rendered": [{"content": "date"}]},
            {"type": "api-request", "id": "r1"}]

    def writer():
        time.sleep(0.2)
        path.write_text(json.dumps(rows[0]) + "\n" + '{"type": "attach', encoding="utf-8")
        time.sleep(0.2)
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    thread = threading.Thread(target=writer)
    thread.start()
    assert wait_for_flush(config, session, prompt, limit=5.0) is True
    thread.join()
    assert collect(path, prompt).records[0]["attachment"]["type"] == "date"
    # Nothing written at all: give up within the limit; collection then reports unknown.
    started = time.monotonic()
    assert wait_for_flush(config, "dddddddd-0000-4000-8000-000000000004", prompt,
                          limit=0.2) is False
    assert time.monotonic() - started < 2


def _age(*paths: Path, minutes: float = 20) -> None:
    import os
    import time

    old = time.time() - minutes * 60
    for path in paths:
        for item in [path, *(path.rglob("*") if path.is_dir() else ())]:
            os.utime(item, (old, old))


def test_sweep_removes_leftover_session_files_and_step_folders_only(tmp_path):
    config, steps = tmp_path / "profile", tmp_path / "steps"
    project_dir = config / "projects" / "C--runtime-workspace"
    project_dir.mkdir(parents=True)
    stale = "aaaaaaaa-0000-4000-8000-000000000001"
    running = "bbbbbbbb-0000-4000-8000-000000000002"
    (project_dir / f"{stale}.jsonl").write_bytes(b"private")
    (project_dir / stale).mkdir()
    (project_dir / stale / "tool-results.txt").write_bytes(b"private")
    (project_dir / f"{running}.jsonl").write_bytes(b"in use")
    (project_dir / "notes.txt").write_bytes(b"not a session")
    (steps / stale).mkdir(parents=True)
    (steps / stale / "system.txt").write_bytes(b"private")
    (steps / running).mkdir()
    # C2: another process's step that is still fresh (its file and folder) is left alone.
    foreign = "eeeeeeee-0000-4000-8000-000000000005"
    (project_dir / f"{foreign}.jsonl").write_bytes(b"another process, mid-step")
    (steps / foreign).mkdir()
    (steps / foreign / "system.txt").write_bytes(b"another process, mid-step")
    _age(project_dir / f"{stale}.jsonl", project_dir / stale, steps / stale,
         project_dir / f"{running}.jsonl", steps / running, project_dir / "notes.txt")
    assert sweep(config, steps, {running}, tmp_path) == 3
    assert sorted(p.name for p in project_dir.iterdir()) == [
        f"{running}.jsonl", f"{foreign}.jsonl", "notes.txt"]
    assert sorted(p.name for p in steps.iterdir()) == sorted([running, foreign])
    # Once the foreign step has been quiet for more than 15 minutes it is a leftover too.
    _age(project_dir / f"{foreign}.jsonl", steps / foreign, minutes=16)
    assert sweep(config, steps, {running}, tmp_path) == 2


def test_sweep_and_delete_never_touch_a_config_directory_outside_the_runtime_home(tmp_path):
    """C1: a person's own Claude Code profile keeps its saved sessions."""
    from sylvia_gm.provider._claude_native import delete_session

    home = tmp_path / "runtime"
    (home / "steps").mkdir(parents=True)
    person = tmp_path / "person" / ".claude"
    session = "ffffffff-0000-4000-8000-000000000006"
    saved = person / "projects" / "C--work" / f"{session}.jsonl"
    saved.parent.mkdir(parents=True)
    saved.write_bytes(b"the person's own session")
    _age(saved, minutes=60)
    assert sweep(person, home / "steps", set(), home) == 0
    assert delete_session(person, session, home) is False
    assert saved.read_bytes() == b"the person's own session"
    # A config path that only looks inside (via ..) is resolved first.
    assert sweep(home / ".." / "person" / ".claude", home / "steps", set(), home) == 0
    assert saved.exists()


# ---------------------------------------------------------------- provider with a scripted CLI


def claude_model() -> ModelInfo:
    return ModelInfo(id=f"claude-runtime/{MODEL}", name="Claude", provider_name="Claude",
                     provider_key="claude_runtime", provider_mode="subscription",
                     transport="claude_code", provider_model_id=MODEL, max_output_tokens=None)


def text_reply(text: str) -> list[dict[str, Any]]:
    events = [
        {"type": "message_start", "message": {"model": MODEL, "usage": {
            "input_tokens": 10, "output_tokens": 0}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text",
                                                                       "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta",
                                                              "text": text}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
         "usage": {"output_tokens": 3}},
        {"type": "message_stop"},
    ]
    return [*({"type": "stream_event", "event": e} for e in events),
            {"type": "result", "subtype": "success", "is_error": False, "result": text,
             "usage": {"input_tokens": 10, "output_tokens": 3}}]


def tool_reply(call_id: str, n: int) -> list[dict[str, Any]]:
    events = [
        {"type": "message_start", "message": {"model": MODEL, "usage": {
            "input_tokens": 10, "output_tokens": 0}}},
        {"type": "content_block_start", "index": 0, "content_block": {
            "type": "tool_use", "id": call_id, "name": "mcp__sylvia__roll", "input": {}}},
        {"type": "content_block_delta", "index": 0, "delta": {
            "type": "input_json_delta", "partial_json": json.dumps({"n": n})}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_stop"},
    ]
    return [*({"type": "stream_event", "event": e} for e in events),
            {"type": "control_request", "request_id": "call", "request": {
                "subtype": "mcp_message", "server_name": "sylvia", "message": {
                    "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"name": "roll", "arguments": {"n": n}}}}}]


class ScriptedCLI:
    """Claude Code at the process boundary: persists its session file like 2.1.322.

    With ``--session-id`` it writes ``<config>/projects/<cwd>/<id>.jsonl`` holding the copied
    transcript, the stdin prompt, its context records when the transcript has none (or a
    scripted date change), and a ``<synthetic>`` filler when the transcript ends on a user
    record. Events are scripted per step.
    """

    scripts: list[list[Any]] = []
    instances: list[ScriptedCLI] = []
    date_change = False

    def __init__(self, executable, workspace, arguments, environment):
        self.workspace, self.arguments, self.environment = workspace, arguments, environment
        self.events = iter(ScriptedCLI.scripts.pop(0))
        self.writes: list[dict[str, Any]] = []
        self.closed = threading.Event()
        self.session_file: Path | None = None
        ScriptedCLI.instances.append(self)

    def argument(self, flag):
        return self.arguments[self.arguments.index(flag) + 1] if flag in self.arguments else None

    def start(self):
        self.system_bytes = Path(self.argument("--system-prompt-file")).read_bytes()
        resume = self.argument("--resume")
        self.transcript_bytes = Path(resume).read_bytes() if resume else b""
        self.cwd_files = list(self.workspace.iterdir())
        self.config_files = sorted(p.name for p in (Path(
            self.environment["CLAUDE_CONFIG_DIR"]) / "projects").glob("*/*"))
        self.step_files = sorted(p.name for p in Path(
            self.argument("--system-prompt-file")).parent.parent.iterdir())

    def write(self, event):
        self.writes.append(event)
        if event.get("type") != "user":
            return
        session = self.argument("--session-id")
        if session is None:
            return
        history = [json.loads(line) for line in self.transcript_bytes.decode().splitlines()]
        rows = list(history)
        if history and history[-1]["type"] == "user":
            rows.append({"type": "assistant", "message": {
                "role": "assistant", "model": "<synthetic>",
                "content": [{"type": "text", "text": FILLER_TEXT}]}})
        rows.append({"type": "user", "message": event["message"], "sessionId": session})
        rows.append({"type": "attachment", "attachment": {"type": "session_context"}})
        if not any(r["type"] == "attachment" for r in history):
            rows.extend({"type": "attachment", **r} for r in first_step_records())
        elif ScriptedCLI.date_change:
            rows.append({"type": "attachment", **DATE_CHANGE})
        rows.append({"type": "api-request-blob", "message": "full private request copy"})
        cwd = re.sub(r"[^A-Za-z0-9]", "-", str(self.workspace))
        self.session_file = (Path(self.environment["CLAUDE_CONFIG_DIR"]) / "projects" / cwd
                             / f"{session}.jsonl")
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        self.session_file.write_text("".join(json.dumps(r) + "\n" for r in rows),
                                     encoding="utf-8")

    def next_event(self, timeout):
        event = next(self.events)
        if event == "block":
            self.closed.wait(30)
            raise ProviderTimeoutError("closed while waiting")
        if isinstance(event, Exception):
            raise event
        return event

    def close(self):
        self.closed.set()


class Hook:
    def __init__(self):
        self.requests = []

    async def on_request(self, context):
        self.requests.append(context.request_json)

    async def on_response(self, context):
        pass

    async def on_error(self, context):
        pass


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(_claude, "find_runtime", lambda _: Path(sys.executable))
    monkeypatch.setattr(_claude, "runtime_version", lambda *_: "2.1.322")
    ScriptedCLI.scripts, ScriptedCLI.instances, ScriptedCLI.date_change = [], [], False
    home = tmp_path / "runtime"
    provider = ClaudeProvider(home, claude_model(), connection_factory=ScriptedCLI)
    hook = Hook()
    provider.hooks.register(hook)
    return provider, home, hook


def session_files_left(home: Path) -> list[Path]:
    return [p for p in (home / "profile" / "projects").rglob("*") if p.is_file()]


def assert_nothing_private_left(home: Path) -> None:
    assert session_files_left(home) == []
    assert list((home / "steps").iterdir()) == []
    assert list((home / "workspace").iterdir()) == []


async def drain(provider, messages, tools=TOOLS):
    return [chunk async for chunk in provider.generate_stream(messages, tools)]


def run_step(provider, chain, canonical, script):
    """One ledger-mode request: plan, set the route as the adapter does, run the provider."""
    frame = chain.ledger.plan(canonical, request_context_id="ctx", tools=TOOLS,
                              check_saved_tip=False)
    chain.ledger.commit(frame)
    chain.frames.append(frame)
    route = ProviderSessionAdapter._claude_native_route(chain.ledger, frame, None, False)
    chain.routes.append(route)
    ScriptedCLI.scripts.append(script)

    async def go():
        CURRENT_NATIVE_ROUTE.set(route)
        return await drain(provider, provider_messages(frame.wire))

    return frame, route, asyncio.run(go())


def test_provider_runs_the_ledger_chain_with_fixed_cwd_lf_system_and_cleanup(runtime):
    provider, home, hook = runtime
    chain = Chain()
    steps = chain_canonicals()
    scripts = {"fresh": text_reply("The door creaks."), "player": tool_reply("c1", 1),
               "tool_result_with_records": tool_reply("c2", 2),
               "tool_result_no_records": text_reply("You reach the bottom."),
               "consecutive_user_entries": text_reply("Light floods the room."),
               "after_date_change": text_reply("Onward.")}
    for name, canonical in steps:
        ScriptedCLI.date_change = name == "consecutive_user_entries"
        frame, route, chunks = run_step(provider, chain, canonical, scripts[name])
        cli = ScriptedCLI.instances[-1]
        assert frame.reset in (None, "journal_started"), name
        # Flags: persistence on with a fresh session id, never --no-session-persistence.
        assert "--no-session-persistence" not in cli.arguments
        assert re.fullmatch(r"[0-9a-f-]{36}", cli.argument("--session-id"))
        assert ("--resume" in cli.arguments) == (name != "fresh")
        assert "--fork-session" in cli.arguments or name == "fresh"
        assert cli.environment["ENABLE_TOOL_SEARCH"] == "false"
        # One fixed, empty cwd; the system prompt bytes are exactly the UTF-8 text, LF only.
        assert cli.workspace == home / "workspace" and cli.cwd_files == []
        assert b"\r" not in cli.system_bytes and b"\r" not in cli.transcript_bytes
        system = prepare_request(provider_messages(frame.wire), TOOLS, MODEL,
                                 home / "workspace", route)[0]
        assert cli.system_bytes == system.encode("utf-8")
        # The stdin prompt: list content, no isSynthetic flag.
        prompt = next(w for w in cli.writes if w.get("type") == "user")
        assert "isSynthetic" not in prompt
        assert all(b["type"] == "text" for b in prompt["message"]["content"])
        assert hook.requests[-1]["controls"]["persist_session"] == TRANSIENT_SESSION
        assert hook.requests[-1]["input"] == prompt
        # No step ever needed the CLI's own filler, and every step's private files are gone.
        assert cli.session_file is not None and not cli.session_file.exists()
        assert_nothing_private_left(home)
        if name in {"player", "tool_result_with_records"}:
            assert chunks[-1].finish_reason == "tool_calls"
    # Records were saved for the first step and the date change only.
    native = {k: v["records"] for k, v in chain.ledger.state.native.items()}
    assert len(native) == 2
    first_boundary, date_boundary = sorted(native)
    assert [r["attachment"]["type"] for r in native[first_boundary]] == [
        "environment", "model", "date"]
    assert native[date_boundary] == [DATE_CHANGE]
    # The last transcript the CLI received replays both, each right after its prompt.
    transcript = [json.loads(line) for line in
                  ScriptedCLI.instances[-1].transcript_bytes.decode().splitlines()]
    attachments = [i for i, r in enumerate(transcript) if r["type"] == "attachment"]
    assert len(attachments) == 4
    assert transcript[attachments[0] - 1]["type"] == "user"
    assert transcript[attachments[3] - 1]["type"] == "user"
    assert not any(r["message"].get("model") == "<synthetic>" for r in transcript
                   if r["type"] != "attachment")


def test_session_file_is_deleted_on_error_and_records_are_not_kept(runtime):
    provider, home, _hook = runtime
    chain = Chain()
    failure = [{"type": "result", "subtype": "error_during_execution", "is_error": True,
                "api_error_status": 500}]
    with pytest.raises(ProviderError):
        run_step(provider, chain, chain_canonicals()[0][1], failure)
    assert ScriptedCLI.instances[-1].session_file is not None
    assert_nothing_private_left(home)
    assert chain.ledger.state.native == {}


def test_session_file_is_deleted_on_timeout(runtime):
    provider, home, _hook = runtime
    chain = Chain()
    with pytest.raises(ProviderTimeoutError):
        run_step(provider, chain, chain_canonicals()[0][1], [ProviderTimeoutError("silent")])
    assert ScriptedCLI.instances[-1].session_file is not None
    assert_nothing_private_left(home)


def test_session_file_is_deleted_when_the_player_cancels(runtime):
    provider, home, _hook = runtime
    chain = Chain()
    frame = chain.ledger.plan(chain_canonicals()[0][1], request_context_id="ctx", tools=TOOLS,
                              check_saved_tip=False)
    chain.ledger.commit(frame)
    route = ProviderSessionAdapter._claude_native_route(chain.ledger, frame, None, False)
    ScriptedCLI.scripts.append(["block"])

    async def cancel():
        CURRENT_NATIVE_ROUTE.set(route)
        stream = provider.generate_stream(provider_messages(frame.wire), TOOLS)
        waiting = asyncio.create_task(anext(stream))
        while not ScriptedCLI.instances or ScriptedCLI.instances[-1].session_file is None:
            await asyncio.sleep(0.01)
        assert ScriptedCLI.instances[-1].session_file.exists()
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        await stream.aclose()

    asyncio.run(cancel())
    assert ScriptedCLI.instances[-1].closed.is_set()
    assert_nothing_private_left(home)
    assert chain.ledger.state.native == {}


def test_leftover_session_files_are_swept_before_each_step(runtime):
    provider, home, _hook = runtime
    stale = home / "profile" / "projects" / "old-cwd" / "aaaaaaaa-0000-4000-8000-00000000000a"
    stale.mkdir(parents=True)
    stale.with_suffix(".jsonl").write_bytes(b"an earlier request")
    (stale / "subagent.jsonl").write_bytes(b"an earlier request")
    leftover = home / "steps" / "aaaaaaaa-0000-4000-8000-00000000000b"
    leftover.mkdir(parents=True)
    (leftover / "history.jsonl").write_bytes(b"an earlier transcript")
    _age(stale.with_suffix(".jsonl"), stale, leftover)
    run_step(provider, Chain(), chain_canonicals()[0][1], text_reply("Hello."))
    cli = ScriptedCLI.instances[-1]
    assert cli.config_files == []
    assert len(cli.step_files) == 1  # only this step's own folder
    assert_nothing_private_left(home)


def test_missing_session_file_is_reported_unknown_and_resets_the_next_request(runtime):
    provider, home, _hook = runtime
    chain = Chain()
    steps = chain_canonicals()
    run_step(provider, chain, steps[0][1], text_reply("The door creaks."))
    real_write = ScriptedCLI.write

    def forgetful(self, event):
        real_write(self, event)
        if self.session_file is not None:
            self.session_file.unlink()

    ScriptedCLI.write = forgetful
    try:
        frame, _route, chunks = run_step(provider, chain, steps[1][1], tool_reply("c1", 1))
    finally:
        ScriptedCLI.write = real_write
    assert chunks[-1].finish_reason == "tool_calls"  # the step's output is still used
    boundary = len(frame.state.entries)
    assert chain.ledger.state.native[boundary] == {"status": "unknown", "records": []}
    frame2, route2, _ = run_step(provider, chain, steps[2][1], text_reply("Done."))
    assert frame2.reset == "native_records_unknown" and route2.first_step
    assert "--resume" not in ScriptedCLI.instances[-1].arguments or (
        frame2.base_count == 0)
    assert_nothing_private_left(home)


def test_legacy_mode_is_mains_route_with_fixed_cwd_and_lf_system(runtime):
    provider, home, hook = runtime
    history = [
        {"role": "system", "content": "Line one.\nLine two.\n"},
        {"role": "user", "content": "Roll."},
        call("c1", 1),
        result("c1", "Rolled 14."),
    ]
    ScriptedCLI.scripts.append(text_reply("Done."))
    asyncio.run(drain(provider, history))
    cli = ScriptedCLI.instances[-1]
    assert "--no-session-persistence" in cli.arguments and "--session-id" not in cli.arguments
    assert cli.system_bytes == b"Line one.\nLine two."
    assert cli.workspace == home / "workspace" and cli.cwd_files == []
    prompt = next(w for w in cli.writes if w.get("type") == "user")
    # Main's continuation prompt and form, unchanged.
    assert prompt["isSynthetic"] is True
    assert prompt["message"]["content"].startswith("SYLVIA_GM_RUNTIME_CONTINUE")
    assert hook.requests[-1]["controls"]["persist_session"] is False
    assert cli.session_file is None
    assert_nothing_private_left(home)


def test_native_records_are_redacted_from_the_owner_protocol():
    from sylvia_gm.interfaces.owner_process import _json_value

    chain = Chain()
    frame, _route = chain.step(chain_canonicals()[0][1])
    payload = native_record(frame.state, context_owner=OWNER, boundary=len(frame.state.entries),
                            status="collected", reason=None, records=first_step_records())
    projected = _json_value(payload)
    assert projected["kind"] == NATIVE_KIND and projected["record_count"] == 3
    assert "records" not in projected and "Environment" not in json.dumps(projected)


def test_gm_turn_saves_cli_records_once_and_replays_them(tmp_path, monkeypatch):
    """A real owner and GM adapter on claude_code with the scripted CLI."""
    from sylvia_gm.agent._session_loop import CampaignSessionLoop, SessionRunRequest
    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.domain.authority import WriterRole
    from sylvia_gm.domain.session import SessionMessageKind
    from tests.test_alpha_f4_generic_sessions import _clockwork_campaign

    monkeypatch.setenv("SYLVIA_GM_CONTEXT_MODE", "ledger")
    monkeypatch.setattr(_claude, "find_runtime", lambda _: Path(sys.executable))
    monkeypatch.setattr(_claude, "runtime_version", lambda *_: "2.1.322")
    ScriptedCLI.instances, ScriptedCLI.date_change = [], False

    class AdvanceCLI(ScriptedCLI):
        """First step calls the engine's advance tool; the second times out."""

        def __init__(self, *args):
            ScriptedCLI.scripts = [[{"type": "control_request", "request_id": "list",
                                     "request": {"subtype": "mcp_message",
                                                 "server_name": "sylvia", "message": {
                                                     "jsonrpc": "2.0", "id": 1,
                                                     "method": "tools/list"}}}]]
            super().__init__(*args)

        def write(self, event):
            super().write(event)
            tools = (event.get("response", {}).get("response", {}).get("mcp_response", {})
                     .get("result", {}).get("tools"))
            if tools is None:
                return
            if len(ScriptedCLI.instances) == 1:
                alias = next(t["name"] for t in tools if t["name"].endswith("_advance"))
                self.events = iter([
                    {"type": "assistant", "message": {"content": [{
                        "type": "tool_use", "id": "one-advance",
                        "name": "mcp__sylvia__" + alias, "input": {"steps": 1}}]}},
                    {"type": "control_request", "request_id": "call", "request": {
                        "subtype": "mcp_message", "server_name": "sylvia", "message": {
                            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                            "params": {"name": alias, "arguments": {"steps": 1}}}}},
                ])
            else:
                self.events = iter([ProviderTimeoutError("Synthetic narration interruption")])

    database = tmp_path / "campaign.sqlite3"
    calls: list[int] = []
    home = tmp_path / "runtime"
    with AuthorityOwner.open(database, "owner-claude", disconnect_grace_seconds=1.0) as owner:
        _clockwork_campaign(owner, campaign_id="claude-campaign", writer=WriterRole.DM,
                            turn_id="claude-turn", calls=calls)
        owner.create_agent_session(campaign_id="claude-campaign", role=WriterRole.DM,
                                   provider_id="claude_runtime",
                                   model_id="claude-runtime/opus", session_id="claude-session")
        provider = ClaudeProvider(home, claude_model(), connection_factory=AdvanceCLI)
        loop = ProviderSessionAdapter(owner, provider, owner_id="owner-claude")
        with pytest.raises(ProviderTimeoutError):
            CampaignSessionLoop(owner).run(
                SessionRunRequest(session_id="claude-session", turn_id="claude-turn",
                                  attempt_id="claude-attempt", message_id="claude-input",
                                  user_text="Advance the clock once."),
                loop.for_turn("claude-turn"),
            )
        assert calls == [1] and len(ScriptedCLI.instances) == 2
        saved = owner.session_context("claude-session").messages
    native = [m for m in saved if m.kind is SessionMessageKind.EVIDENCE
              and isinstance(m.content, Mapping) and m.content.get("kind") == NATIVE_KIND]
    # The first step's records, saved once; the failed second step saves nothing.
    assert len(native) == 1 and native[0].content["status"] == "collected"
    assert native[0].message_id == f"claude-native-{native[0].content['native_record_id']}"
    assert [r["attachment"]["type"] for r in native[0].content["records"]] == [
        "environment", "model", "date"]
    first, second = ScriptedCLI.instances
    prompt = next(w for w in first.writes if w.get("type") == "user")
    transcript = [json.loads(line) for line in second.transcript_bytes.decode().splitlines()]
    assert transcript[0]["message"]["content"] == prompt["message"]["content"]
    assert [r["type"] for r in transcript[1:4]] == ["attachment"] * 3
    kinds = [r["type"] for r in transcript]
    assert kinds[-3:] == ["assistant", "user", "assistant"]
    assert transcript[-1]["message"]["content"] == [{"type": "text", "text": FILLER_TEXT}]
    assert "isSynthetic" not in next(w for w in second.writes if w.get("type") == "user")
    assert_nothing_private_left(home)
    shutil.rmtree(home / "profile", ignore_errors=True)


def test_a_failed_deletion_is_retried_next_step_without_waiting(runtime, monkeypatch):
    """This process's own leftovers are retried at once, whatever their age."""
    from sylvia_gm.provider import _claude_native

    provider, home, _hook = runtime
    real_remove = _claude_native._remove
    failing = {"left": 2}

    def flaky(path):
        if failing["left"] and path.suffix == ".jsonl":
            failing["left"] -= 1
            return False  # still locked by the closing process for the rest of this step
        return real_remove(path)

    monkeypatch.setattr(_claude_native, "_remove", flaky)
    run_step(provider, Chain(), chain_canonicals()[0][1], text_reply("Hello."))
    left = session_files_left(home)
    assert len(left) == 1  # one fresh copy of the request survived this step
    run_step(provider, Chain(), chain_canonicals()[0][1], text_reply("Hello again."))
    # Retried at the start of the next step, though it is seconds old, not 15 minutes.
    assert not left[0].exists()
    assert ScriptedCLI.instances[-1].config_files == []
    assert_nothing_private_left(home)


def test_an_incomplete_flush_reports_the_records_unknown(runtime, monkeypatch):
    """A flush wait that did not complete never reports "collected"."""
    provider, home, _hook = runtime
    monkeypatch.setattr(_claude, "wait_for_flush", lambda *args, **kwargs: False)
    chain = Chain()
    frame, _route, chunks = run_step(provider, chain, chain_canonicals()[0][1],
                                     text_reply("Hello."))
    assert chunks[-1].finish_reason == "stop"  # the step's output is still used
    assert chain.ledger.state.native[len(frame.state.entries)] == {
        "status": "unknown", "records": []}
    assert_nothing_private_left(home)


def test_exports_stub_cli_records_and_the_wire_journal(tmp_path, monkeypatch):
    """A portable or private export keeps both machine-local evidence kinds only as
    stubs; recovery opens and the next GM turn starts a journal and collects the CLI's records
    again."""
    import dataclasses
    import re
    import zipfile

    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.domain.session import SessionMessageKind
    from tests.test_fc_campaign_journey import _start_campaign
    from tests.test_gm_context_contract import Table

    monkeypatch.setenv("SYLVIA_GM_CONTEXT_MODE", "ledger")
    monkeypatch.setattr(_claude, "find_runtime", lambda _: Path(sys.executable))
    monkeypatch.setattr(_claude, "runtime_version", lambda *_: "2.1.322")
    ScriptedCLI.instances, ScriptedCLI.date_change = [], False

    class ToolListCLI(ScriptedCLI):
        """Answers the tool listing, then replies."""

        def __init__(self, *args):
            ScriptedCLI.scripts = [[{"type": "control_request", "request_id": "list",
                                     "request": {"subtype": "mcp_message",
                                                 "server_name": "sylvia", "message": {
                                                     "jsonrpc": "2.0", "id": 1,
                                                     "method": "tools/list"}}}]]
            super().__init__(*args)

        def write(self, event):
            super().write(event)
            tools = (event.get("response", {}).get("response", {}).get("mcp_response", {})
                     .get("result", {}).get("tools"))
            if tools is not None:
                self.events = iter(text_reply("The road is quiet."))

    def evidence(database: Path, session: str) -> list:
        with AuthorityOwner.open(database, "inspect") as owner:
            messages = owner.session_context(session).messages
        return [m.content for m in messages if m.kind is SessionMessageKind.EVIDENCE
                and isinstance(m.content, Mapping)
                and m.content.get("kind") in {NATIVE_KIND, JOURNAL_KIND}]

    database, recovered = tmp_path / "campaign.sqlite3", tmp_path / "recovered.sqlite3"
    home = tmp_path / "runtime"
    # A real bundled campaign's instructions and tools need a realistic context window.
    model = dataclasses.replace(claude_model(), context_window=1_000_000)
    campaign, session, _turn = _start_campaign(database, "the-long-way-round")
    table = Table(database, ClaudeProvider(home, model, connection_factory=ToolListCLI),
                  campaign, session)
    try:
        table.submit("I look around.")
    finally:
        table.close()
    saved = evidence(database, session)
    assert {NATIVE_KIND, JOURNAL_KIND} <= {r["kind"] for r in saved}
    private_heads = [r["head"].encode() for r in saved if r["kind"] == JOURNAL_KIND]
    with AuthorityOwner.open(database, "exporter") as owner:
        # Exports need the current schema; a no-op rename migrates it, as the export tests do.
        snap = owner.snapshot(campaign)
        owner.rename_campaign(operation_id="schema", campaign_id=campaign,
                              display_name=snap.display_name, expected_revision=snap.revision)
        for name, private in (("portable.zip", False), ("private.zip", True)):
            owner.export_campaign(operation_id="export-" + name, campaign_id=campaign,
                                  target=tmp_path / name, include_thinking=private)
    # The local database is never touched.
    assert evidence(database, session) == saved
    for name in ("portable.zip", "private.zip"):
        with zipfile.ZipFile(tmp_path / name) as archive:
            data = b"".join(archive.read(member) for member in archive.namelist())
        assert b"journal_record_id" not in data and b"native_record_id" not in data, name
        assert not any(head in data for head in private_heads), name
        # The CLI's environment record names its working directory and the OS version.
        assert b"SylviaRuntime" not in data and b"osVersion" not in data, name
        assert b"I look around." in data  # the session itself travels
    with AuthorityOwner.open(recovered, "recovery") as owner:
        owner.recover_campaign(recovery_id="recover", source=tmp_path / "portable.zip")
    stubs = evidence(recovered, session)
    # Every row is kept, in order, as a stub.
    assert [dict(r) for r in stubs] == [
        {"kind": r["kind"], "omitted_from_export": True} for r in saved]

    def next_turn(path: Path) -> tuple[str, int]:
        """Play the same next turn; its outcome and how many CLI steps it took."""
        ScriptedCLI.instances = []
        table = Table(path, ClaudeProvider(home, model, connection_factory=ToolListCLI),
                      campaign, session)
        try:
            reply, _identity = table.submit("I keep looking.")
        finally:
            table.close()
        error = str((reply or {}).get("interruption", {}).get("error", ""))
        code = re.search(r"'code': '([a-z_]+)'", error)
        return (code.group(1) if code else "completed"), len(ScriptedCLI.instances)

    # The recovered campaign plays its next turn exactly as the never-exported one does. (With
    # a prose-only CLI this campaign's second turn ends on its required-tool rule, in both.)
    outcome = next_turn(recovered)
    assert outcome == next_turn(database) and outcome[1] >= 1
    after = evidence(recovered, session)[len(stubs):]
    journals = [r for r in after if r["kind"] == JOURNAL_KIND]
    assert journals and journals[0]["reset"] == "journal_started"
    # The CLI's records were collected again on the recovered campaign's first step.
    assert any(r["kind"] == NATIVE_KIND and r["status"] == "collected" for r in after)
    assert_nothing_private_left(home)


def test_mcp_debug_logs_inside_the_runtime_home_go_with_each_step(runtime, monkeypatch, tmp_path):
    """Off Windows Claude Code logs each step's tool names under the runtime's cache folder."""
    from sylvia_gm.provider._claude_native import remove_debug_logs

    provider, home, _hook = runtime
    real_environment = _claude.native_environment

    def off_windows(runtime_home):
        return {**real_environment(runtime_home), "XDG_CACHE_HOME": str(runtime_home / "cache")}

    monkeypatch.setattr(_claude, "native_environment", off_windows)

    def plant(cache: Path) -> Path:
        folder = cache / "claude-cli-nodejs" / "-runtime-workspace" / "mcp-logs-sylvia"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "2026-11-16T00-00-00.jsonl").write_text(
            '{"debug":"Calling MCP tool: roll","cwd":"/home/someone/runtime/workspace"}\n',
            encoding="utf-8")
        return folder

    chain = Chain()
    # A completed step, then a failed one: the log is gone after both.
    log = plant(home / "cache")
    kept = home / "cache" / "claude-cli-nodejs" / "-runtime-workspace" / "other.txt"
    kept.write_text("not a debug log", encoding="utf-8")
    run_step(provider, chain, chain_canonicals()[0][1], text_reply("The door creaks."))
    assert not log.exists() and kept.exists()
    log = plant(home / "cache")
    failure = [{"type": "result", "subtype": "error_during_execution", "is_error": True,
                "api_error_status": 500}]
    with pytest.raises(ProviderError):
        run_step(provider, chain, chain_canonicals()[1][1], failure)
    assert not log.exists() and kept.exists()
    # A cache folder outside the runtime home (a person's own) is never touched.
    outside = plant(tmp_path / "person" / "cache")
    assert remove_debug_logs(tmp_path / "person" / "cache", home) == 0 and outside.exists()
    assert remove_debug_logs(None, home) == 0
