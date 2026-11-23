"""Generate reference vectors for the working context and the request data (T2a).

Run from the repository root with the snapshot's interpreter:

    .venv/Scripts/python.exe -I crates/sylvia-agent/tests/vectors/generate_working_context_vectors.py

Writes `working_context_vectors.json`, every value read back from the Python module:

- `request_data`: `application_data_after_history` and `strip_data_markers` over message lists
  (control messages with and without values, data records, the current state, history);
- `pure`: `_safe_message`, `durable_tool_result`, `_valid_checkpoint`, `_groups` (the pinned
  messages and each group's keys) and `estimate_tokens`;
- `scenarios`: a `WorkingContextProvider` around a scripted provider, each running one
  operation (`project`, `budget_terms`, `prepare`, `read_history`, `generate` or
  `generate_stream`), with everything the wrapped provider was sent, the checkpoints saved,
  the progress hints, the records after and the outcome (result or error class and message).

Checkpoint identities are numbered (`uuid4` is replaced). Reference tooling only; retired with
the Python source.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.agent import _request_data as request_data  # noqa: E402
from sylvia_gm.agent import _working_context as working  # noqa: E402
from sylvia_gm.provider import _exceptions as exceptions  # noqa: E402
from sylvia_gm.provider._base import (  # noqa: E402
    ModelInfo,
    ProviderResponse,
    ProviderStreamChunk,
    ToolCall,
)
from sylvia_gm.provider._hooks import HookManager  # noqa: E402

OUTPUT = Path(__file__).with_name("working_context_vectors.json")


# ---------------------------------------------------------------------------------------------
# Request data


def request_data_cases() -> list[dict[str, object]]:
    lists = [
        [
            {"role": "system", "content": "SYLVIA_GM_SYSTEM contract"},
            {"role": "user", "content": "I look around."},
            {"role": "system", "content": "SYLVIA_GM_FRESH_CAMPAIGN_STATE (data):\n{\"b\":1,\"a\":[2]}"},
            {"role": "assistant", "content": "You see a road."},
            {"role": "system", "content": "SYLVIA_GM_CAMPAIGN_PREPARATION (application-owned campaign data):\n{}"},
            {"role": "system", "content": "SYLVIA_GM_DECISION_REQUEST (control):\n{\"z\": 1, \"ä\": [1, 2]}\nIt is the GM's decision."},
            {"role": "system", "content": "SYLVIA_GM_OPENING_TASK (start): no values here"},
            {"role": "system", "content": "SYLVIA_GM_PLAYER_BOUNDARY_NARRATION (control):\n  [1, {\"x\": null}] trailing\nMore."},
            {"role": "system", "content": "SYLVIA_GM_CAMPAIGN_FILE_DISPOSITION_REQUIRED (control): text\n{not json"},
            {"role": "user", "content": "SYLVIA_GM_CURRENT_ACTION data", "_application_data": True, "_fresh_application_data": True},
            {"role": "user", "content": "SYLVIA_GM_FRESH_CAMPAIGN_STATE again", "_fresh_application_data": True},
            {"role": "tool", "content": "{\"ok\": true}", "tool_call_id": "c1", "_ledger_key": "k"},
        ],
        [
            {"role": "system", "content": "SYLVIA_GM_CAMPAIGN_PREPARATION (application-owned control, x):\n{\"queue\": [1]}\nResolve."},
            {"role": "system", "content": None},
            {"role": "user", "content": ["parts", 1]},
            {"role": "system", "content": "SYLVIA_GM_LIFECYCLE_EVENT (application-owned):\n{\"event\": \"campaign_start\"}"},
            {"role": "assistant", "tool_calls": [{"id": "c1"}], "_stable_instruction": True},
            {"role": "system", "content": "SYLVIA_GM_REDIRECTED_CONTINUATION x\n　{\"spaced\": \"ideographic\"}"},
            {"role": "system", "content": "SYLVIA_PLAYER_ACTION_RESPONSE_TASK (task):\n{\"action_id\": \"a\", \"automatic\": false} tail"},
            {"content": "no role", "_host_record": True, "_ledger_entry": {"x": 1}},
        ],
        [],
    ]
    cases = []
    for messages in lists:
        cases.append({
            "messages": messages,
            "after_history": request_data.application_data_after_history(messages),
            "stripped": request_data.strip_data_markers(messages),
        })
    return cases


# ---------------------------------------------------------------------------------------------
# Pure helpers


def call(call_id: str, name: str, arguments: object) -> dict[str, object]:
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": arguments}}


def conversation(pairs: int, size: int = 400, with_tools: bool = True) -> list[dict[str, object]]:
    messages: list[dict[str, object]] = [{"role": "system", "content": "SYSTEM " + "s" * 200}]
    for index in range(pairs):
        messages.append({"role": "user", "content": f"Player turn {index} " + "u" * size})
        if with_tools and index % 3 == 1:
            messages.append({"role": "assistant", "content": f"Checking {index}",
                             "tool_calls": [call(f"call-{index}", "read_file",
                                                 json.dumps({"path": f"f{index}.md"}))]})
            messages.append({"role": "tool", "tool_call_id": f"call-{index}",
                             "content": json.dumps({"content": "file " * 30, "path": "x"})})
        messages.append({"role": "assistant", "content": f"GM answer {index} " + "a" * size,
                         "reasoning_details": [{"signed": "x"}]})
    return messages


def pure_cases() -> dict[str, object]:
    messages = conversation(5, 50) + [
        {"role": "user", "content": "SYLVIA_GM_BUILDER_START (start)"},
        {"role": "assistant", "content": None, "tool_calls": [call("u1", "search_files", "{}")]},
        {"role": "tool", "tool_call_id": "other", "content": "{}"},
        {"role": "assistant", "content": "", "tool_calls": [
            call("s1", "search_files", "{\"q\": \"x\"}"), call("s2", "roll", {"already": "parsed"})]},
        {"role": "tool", "tool_call_id": "s2", "content": "not json"},
        {"role": "tool", "tool_call_id": "s1", "content": json.dumps(
            {"matches": [{"path": "a", "content": "c"}, "plain", {"path": "b"}]})},
        {"role": "user", "content": "data", "_application_data": True},
    ]
    pinned, groups = working._groups(messages)  # noqa: SLF001
    results = [
        ("read_file", {"content": "hello", "path": "p"}), ("read_file", {"content": 3}),
        ("read_file", "text"), ("search_files", {"matches": [{"content": "x", "a": 1}, 2]}),
        ("search_files", {"matches": [{"a": 1}]}), ("search_files", {"matches": "no"}),
        ("other", {"content": "kept"}),
    ]
    good = {"kind": working.CHECKPOINT_KIND, "version": 1, "checkpoint_id": "c",
            "summary": "s", "source_archive": "[]", "covered_keys": ["0" * 64]}
    records = [good, {**good, "version": True}, {**good, "version": 1.0}, {**good, "version": 2},
               {**good, "kind": "other"}, {**good, "checkpoint_id": ""}, {**good, "summary": ""},
               {**good, "summary": "x" * 12_000}, {**good, "summary": "x" * 12_001},
               {**good, "source_archive": 3}, {**good, "covered_keys": []},
               {**good, "covered_keys": "0" * 64}, {**good, "covered_keys": ["0" * 63]},
               {**good, "covered_keys": ["é" * 64]}, {k: v for k, v in good.items() if k != "kind"}]
    values = [None, "", "x", {"a": "é"}, [1, 2.5, True], {"nested": {"list": [None]}}]
    return {
        "groups": {"messages": messages, "pinned": pinned,
                   "groups": [{"messages": g.messages, "keys": g.keys} for g in groups]},
        "safe_message": [{"message": m, "safe": working._safe_message(m)}  # noqa: SLF001
                         for m in messages[:6]],
        "durable": [{"tool": tool, "result": result,
                     "durable": working.durable_tool_result(tool, result)}
                    for tool, result in results],
        "valid_checkpoint": [{"record": record, "valid": working._valid_checkpoint(record)}  # noqa: SLF001
                             for record in records],
        "estimate_tokens": [{"value": value, "tokens": working.estimate_tokens(value)}
                            for value in values],
    }


# ---------------------------------------------------------------------------------------------
# Scenarios


ERRORS = {
    "TokenLimitError": exceptions.TokenLimitError,
    "InvalidRequestError": exceptions.InvalidRequestError,
    "ProviderError": exceptions.ProviderError,
}


def error_of(spec: dict[str, object]) -> BaseException:
    return ERRORS[spec["class"]](spec["message"])  # type: ignore[index]


def response_of(spec: dict[str, object]) -> ProviderResponse:
    return ProviderResponse(
        content=spec.get("content"),  # type: ignore[arg-type]
        tool_calls=[ToolCall(**tool) for tool in spec.get("tool_calls", [])],  # type: ignore[arg-type]
        finish_reason=spec.get("finish_reason", "stop"),  # type: ignore[arg-type]
    )


def chunk_of(spec: dict[str, object]) -> ProviderStreamChunk:
    return ProviderStreamChunk(content=spec.get("content", ""),  # type: ignore[arg-type]
                               reasoning_content=spec.get("reasoning_content", ""),  # type: ignore[arg-type]
                               tool_call_delta=spec.get("tool_call_delta"),  # type: ignore[arg-type]
                               finish_reason=spec.get("finish_reason"))  # type: ignore[arg-type]


class Scripted:
    """A provider answering from a script and recording every request it is sent."""

    def __init__(self, model: ModelInfo | None, script: dict[str, list[object]]) -> None:
        self.model_info = model
        self.hooks = HookManager()
        self._generate = list(script.get("generate", []))
        self._streams = list(script.get("streams", []))
        self.calls: list[dict[str, object]] = []

    def _record(self, kind: str, messages, tools, timeout, options) -> None:  # type: ignore[no-untyped-def]
        self.calls.append({"kind": kind, "messages": messages, "tools": tools,
                           "timeout": timeout, "options": dict(options)})

    async def generate(self, messages, tools=None, stream=False, timeout=60.0, **options):  # type: ignore[no-untyped-def]
        self._record("generate", messages, tools, timeout, options)
        if not self._generate:
            raise exceptions.ProviderError("script exhausted")
        step = self._generate.pop(0)
        if "raise" in step:
            raise error_of(step["raise"])
        return response_of(step)

    def generate_stream(self, messages, tools=None, timeout=120.0, **options):  # type: ignore[no-untyped-def]
        self._record("stream", messages, tools, timeout, options)
        steps = self._streams.pop(0) if self._streams else [
            {"raise": {"class": "ProviderError", "message": "script exhausted"}}]

        async def chunks():  # type: ignore[no-untyped-def]
            for step in steps:
                if "raise" in step:
                    raise error_of(step["raise"])
                yield chunk_of(step)
        return chunks()

    async def release_idle(self) -> None:
        self.calls.append({"kind": "release_idle"})


def model_of(spec: dict[str, object] | None) -> ModelInfo | None:
    if spec is None:
        return None
    model = ModelInfo(id=spec["id"], name=spec["id"], provider_name="p",  # type: ignore[arg-type]
                      provider_key=spec.get("provider_key", "openai_compat"),  # type: ignore[arg-type]
                      provider_mode="api", transport=spec.get("transport", "chat_completions"),  # type: ignore[arg-type]
                      context_window=spec.get("context_window"),  # type: ignore[arg-type]
                      max_output_tokens=spec.get("max_output_tokens"))  # type: ignore[arg-type]
    return model


def model_facts(model: ModelInfo | None) -> dict[str, object] | None:
    """What the working context reads of the model, as it is after construction."""
    if model is None:
        return None
    return {"id": model.id, "provider_key": model.provider_key, "transport": model.transport,
            "context_window": model.context_window, "max_output_tokens": model.max_output_tokens,
            "builder_output_tokens": model.builder_output_tokens}


def plain(value: object) -> object:
    if isinstance(value, ProviderResponse):
        data = asdict(value)
        return {"content": data["content"], "finish_reason": data["finish_reason"],
                "tool_calls": data["tool_calls"]}
    if isinstance(value, ProviderStreamChunk):
        return {"content": value.content, "reasoning_content": value.reasoning_content,
                "tool_call_delta": value.tool_call_delta, "finish_reason": value.finish_reason}
    return value


SMALL = {"id": "small", "context_window": 3_000}
TINY = {"id": "tiny", "context_window": 1_200}
UNKNOWN = {"id": "unknown"}
ROUTER = {"id": "router", "provider_key": "openrouter", "context_window": 100_000}
CLAUDE_CODE = {"id": "cc", "transport": "claude_code"}
CODEX = {"id": "codex", "transport": "codex_app_server", "context_window": 5_000}
BIG = {"id": "big", "context_window": 400_000, "max_output_tokens": 8_000}
OWNER = {"campaign_id": "c1", "session_id": "s1"}


def checkpoint(messages: list[dict[str, object]], groups: int, summary: str, ident: str,
               owner: dict[str, object] | None = None) -> dict[str, object]:
    _, all_groups = working._groups(messages)  # noqa: SLF001
    record = {"kind": working.CHECKPOINT_KIND, "version": 1, "checkpoint_id": ident,
              "summary": summary, "source_archive": json.dumps([{"archived": ident}]),
              "covered_keys": [key for group in all_groups[:groups] for key in group.keys]}
    if owner is not None:
        record["context_owner"] = owner
    return record


def note(content: str) -> dict[str, object]:
    return {"content": content, "finish_reason": "stop"}


def notes(prefix: str, count: int = 40) -> list[dict[str, object]]:
    """Enough summarizer replies for every slice of a compaction."""
    return [note(f"{prefix} {index}.") for index in range(count)]


def scenarios() -> list[dict[str, object]]:
    long_play = conversation(12)
    short_play = conversation(2, 40, with_tools=False)
    fresh = conversation(6) + [{"role": "user", "content": "SYLVIA_GM_FRESH_CAMPAIGN_STATE now",
                                "_application_data": True, "_fresh_application_data": True}]
    tools = [{"type": "function", "function": {"name": "roll", "parameters": {}}}]
    history_tools = [{"type": "function", "function": {"name": "read_session_history"}}]
    saved = checkpoint(long_play, 4, "Earlier: the player reached the inn.", "saved-1", OWNER)
    other_owner = checkpoint(long_play, 6, "Other owner.", "saved-2", {"campaign_id": "c2"})
    stale = checkpoint(conversation(12, 401), 3, "Stale.", "saved-3", OWNER)
    builder = [{"role": "system", "content": "B"}, {"role": "user", "content": "Objective " + "o" * 300}]
    builder += [m for m in conversation(6)[1:]]
    builder_saved = checkpoint(builder, 3, "Builder note.", "saved-b")
    cases: list[dict[str, object]] = [
        {"name": "project without a checkpoint", "op": "project", "model": SMALL, "messages": short_play},
        {"name": "project with the owner's checkpoint", "op": "project", "model": SMALL,
         "messages": long_play, "records": [stale, saved, other_owner], "record_owner": OWNER},
        {"name": "project keeps fresh data and the last player turn", "op": "project", "model": SMALL,
         "messages": fresh, "records": [checkpoint(fresh, 5, "Fresh note.", "f1")]},
        {"name": "builder project pins its objective", "op": "project", "model": SMALL,
         "messages": builder, "records": [builder_saved], "pin_first_user": True},
        {"name": "play project does not pin the first turn", "op": "project", "model": SMALL,
         "messages": builder, "records": [builder_saved], "pin_first_user": False},
        {"name": "budget terms, small window", "op": "budget_terms", "model": SMALL,
         "messages": long_play, "tools": tools, "options": {}},
        {"name": "budget terms, max_tokens", "op": "budget_terms", "model": BIG,
         "messages": long_play, "tools": None, "options": {"max_tokens": 1_500}},
        {"name": "budget terms, max_output_tokens and zero max_tokens", "op": "budget_terms",
         "model": BIG, "messages": short_play, "options": {"max_tokens": 0, "max_output_tokens": 900}},
        {"name": "budget terms, unknown window", "op": "budget_terms", "model": UNKNOWN,
         "messages": short_play, "options": {}},
        {"name": "budget terms, Claude Code", "op": "budget_terms", "model": CLAUDE_CODE,
         "messages": short_play, "options": {"max_tokens": 100}},
        {"name": "budget terms, no model", "op": "budget_terms", "model": None,
         "messages": short_play, "options": {}},
        {"name": "prepare fits", "op": "prepare", "model": BIG, "messages": long_play, "tools": tools},
        {"name": "prepare without a model", "op": "prepare", "model": None, "messages": long_play},
        {"name": "prepare compacts", "op": "prepare", "model": SMALL, "messages": long_play,
         "tools": tools, "record_owner": OWNER, "script": {"generate": notes("A short note")}},
        {"name": "prepare compacts from the saved note", "op": "prepare", "model": SMALL,
         "messages": long_play, "records": [saved], "record_owner": OWNER,
         "script": {"generate": notes("The newer note")}},
        {"name": "prepare repairs a long note", "op": "prepare", "model": SMALL, "messages": long_play,
         "script": {"generate": [note("x" * 300), note("Shortened.")] + notes("Then")}},
        {"name": "prepare refuses a long note twice", "op": "prepare", "model": SMALL,
         "messages": long_play, "script": {"generate": [note("x" * 300), note("y" * 299)]}},
        {"name": "prepare refuses a tool call", "op": "prepare", "model": SMALL, "messages": long_play,
         "script": {"generate": [{"content": "n", "tool_calls": [
             {"id": "t", "name": "roll", "arguments": "{}"}]}]}},
        {"name": "prepare refuses an unfinished note", "op": "prepare", "model": SMALL,
         "messages": long_play, "script": {"generate": [{"content": "n", "finish_reason": "length"}]}},
        {"name": "prepare refuses an empty note", "op": "prepare", "model": SMALL,
         "messages": long_play, "script": {"generate": [{"content": ""}]}},
        {"name": "prepare, end_turn note on Codex", "op": "prepare", "model": CODEX,
         "messages": long_play, "script": {"generate": [{"content": "ok", "finish_reason": "end_turn"}] * 40}},
        {"name": "prepare with router context id", "op": "prepare", "model": {**ROUTER, "context_window": 3_000},
         "messages": long_play, "request_context_id": "ctx-1", "force": True,
         "script": {"generate": notes("Router note")}},
        {"name": "prepare too large to fit", "op": "prepare", "model": TINY,
         "messages": short_play, "tools": [{"name": "t", "x": "y" * 4_000}]},
        {"name": "prepare too large for an unknown window", "op": "prepare",
         "model": {"id": "unknown-small", "context_window": None}, "messages": short_play,
         "tools": [{"name": "t", "x": "y" * 200_000}]},
        {"name": "prepare force with nothing to compact", "op": "prepare", "model": BIG,
         "messages": short_play[:2], "force": True},
        {"name": "prepare force, play", "op": "prepare", "model": BIG, "messages": long_play,
         "force": True, "pin_first_user": False, "script": {"generate": [note("Forced.")]}},
        {"name": "prepare force, builder", "op": "prepare", "model": BIG, "messages": long_play,
         "force": True, "script": {"generate": [note("Forced builder.")]}},
        {"name": "prepare cancelled", "op": "prepare", "model": SMALL, "messages": long_play,
         "cancelled": True},
        {"name": "prepare summarizer fails", "op": "prepare", "model": SMALL, "messages": long_play,
         "script": {"generate": [{"raise": {"class": "ProviderError", "message": "down"}}]}},
        {"name": "prepare in slices", "op": "prepare", "model": {"id": "slices", "context_window": 4_000},
         "messages": conversation(14, 500), "force": True,
         "script": {"generate": notes("Slice", 80)}},
        {"name": "read history list", "op": "read_history", "model": BIG, "messages": long_play,
         "records": [saved], "params": [{"source_id": "list"}]},
        {"name": "read history pages", "op": "read_history", "model": BIG, "messages": long_play,
         "records": [saved], "params": [
             {}, {"offset": 100, "limit": 50}, {"source_id": "saved-1"}, {"source_id": "missing"},
             {"offset": -1}, {"limit": 0}, {"limit": 16_001}, {"offset": True}, {"limit": 5.0},
             {"query": "PLAYER TURN 3", "limit": 20}, {"query": "nowhere"}, {"query": 3},
             {"query": "", "limit": 10}, {"offset": 10_000_000}, {"query": "gm answer", "offset": 5_000, "limit": 30},
             {"source_id": "saved-1", "query": "ARCHIVED"}]},
        {"name": "generate offers history after compaction", "op": "generate", "model": BIG,
         "messages": long_play, "tools": tools, "records": [saved], "history": True,
         "script": {"generate": [{"content": "Done."}]}},
        {"name": "generate keeps an offered history tool", "op": "generate", "model": BIG,
         "messages": long_play, "tools": history_tools, "records": [saved], "history": True,
         "script": {"generate": [{"content": "Done."}]}},
        {"name": "generate retries a token limit", "op": "generate", "model": BIG,
         "messages": long_play, "tools": tools, "options": {"tool_choice": "auto"},
         "script": {"generate": [{"raise": {"class": "TokenLimitError", "message": "too big"}},
                                 note("Forced note."), {"content": "Answer.", "finish_reason": "stop"}]}},
        {"name": "generate context_length", "op": "generate", "model": BIG, "messages": short_play,
         "script": {"generate": [{"content": "x", "finish_reason": "context_length"}] + notes("Retry")
                    + [{"content": "After the retry."}]}},
        {"name": "generate output limit", "op": "generate", "model": BIG, "messages": short_play,
         "script": {"generate": [{"content": "x", "finish_reason": "max_tokens"}]}},
        {"name": "generate invalid request", "op": "generate", "model": BIG, "messages": short_play,
         "script": {"generate": [{"raise": {"class": "InvalidRequestError", "message": "bad"}}]}},
        {"name": "generate router id", "op": "generate", "model": ROUTER, "messages": short_play,
         "request_context_id": "ctx-2", "timeout": 33.0, "script": {"generate": [{"content": "ok"}]}},
        {"name": "stream", "op": "generate_stream", "model": BIG, "messages": short_play, "tools": tools,
         "script": {"streams": [[{"content": "Hel"}, {"reasoning_content": "r"}, {"content": "lo",
                                                                                   "finish_reason": "stop"}]]}},
        {"name": "stream retries a token limit before output", "op": "generate_stream", "model": BIG,
         "messages": long_play, "script": {
             "streams": [[{"tool_call_delta": {}}, {"raise": {"class": "TokenLimitError", "message": "big"}}],
                         [{"content": "After."}]],
             "generate": [note("Note for the retry.")]}},
        {"name": "stream does not retry after output", "op": "generate_stream", "model": BIG,
         "messages": long_play, "script": {
             "streams": [[{"content": "Partial"}, {"raise": {"class": "TokenLimitError", "message": "big"}}]]}},
        {"name": "stream context_length chunk", "op": "generate_stream", "model": BIG,
         "messages": long_play, "script": {"streams": [[{"finish_reason": "context_length"}],
                                                        [{"content": "Retried."}]],
                                            "generate": notes("Stream retry")}},
        {"name": "stream cut off with output", "op": "generate_stream", "model": BIG,
         "messages": short_play, "script": {"streams": [[{"content": "a"}, {"content": "b", "finish_reason": "length"}]]}},
        {"name": "stream cut off without output", "op": "generate_stream", "model": BIG,
         "messages": short_play, "script": {"streams": [[{"tool_call_delta": {"x": 1}}, {"finish_reason": "max_tokens"}]]}},
        {"name": "stream invalid request", "op": "generate_stream", "model": BIG,
         "messages": short_play, "script": {"streams": [[{"raise": {"class": "InvalidRequestError", "message": "no"}}]]}},
        {"name": "stream token limit four times", "op": "generate_stream", "model": BIG,
         "messages": long_play, "script": {
             "streams": [[{"raise": {"class": "TokenLimitError", "message": f"big {i}"}}] for i in range(4)],
             "generate": [note(f"Note {i}.") for i in range(3)]}},
    ]
    return cases


async def run(case: dict[str, object]) -> dict[str, object]:
    model = model_of(case.get("model"))  # type: ignore[arg-type]
    provider = Scripted(model, case.get("script", {}))  # type: ignore[arg-type]
    saved: list[dict[str, object]] = []
    progress: list[object] = []
    numbers = iter(range(1, 100))

    async def save(payload: dict[str, object]) -> None:
        saved.append(payload)

    working.uuid4 = lambda: SimpleNamespace(hex=f"checkpoint-{next(numbers)}")  # type: ignore[attr-defined]
    wrapper = working.WorkingContextProvider(
        provider, records=case.get("records", []), save=save,  # type: ignore[arg-type]
        cancelled=lambda: bool(case.get("cancelled")), progress=progress.append,
        record_owner=case.get("record_owner"),  # type: ignore[arg-type]
        request_context_id=case.get("request_context_id"),  # type: ignore[arg-type]
        pin_first_user=case.get("pin_first_user", True))  # type: ignore[arg-type]
    if case.get("history"):
        wrapper.history_retrieval_enabled = True
    messages = case["messages"]
    tools = case.get("tools")
    options = dict(case.get("options", {}))  # type: ignore[call-overload]
    op = case["op"]
    outcome: dict[str, object]
    try:
        if op == "project":
            outcome = {"result": wrapper.project(messages)}  # type: ignore[arg-type]
        elif op == "budget_terms":
            projected = wrapper.project(messages)  # type: ignore[arg-type]
            outcome = {"result": wrapper.budget_terms(projected, tools, options)}
        elif op == "prepare":
            outcome = {"result": await wrapper.prepare(
                messages, tools, force=bool(case.get("force")), **options)}
        elif op == "read_history":
            await wrapper.prepare(messages, tools)
            outcome = {"result": [wrapper.read_history(params) for params in case["params"]]}  # type: ignore[union-attr]
        elif op == "generate":
            response = await wrapper.generate(messages, tools, timeout=case.get("timeout", 60.0),
                                              **options)
            outcome = {"result": plain(response)}
        else:
            chunks = []
            try:
                async for chunk in wrapper.generate_stream(messages, tools, **options):
                    chunks.append(plain(chunk))
            except BaseException as error:  # the chunks before the error are kept
                outcome = {"chunks": chunks, "error": {"class": type(error).__name__,
                                                       "message": str(error)}}
            else:
                outcome = {"chunks": chunks}
    except (exceptions.ProviderError, asyncio.CancelledError) as error:
        outcome = {"error": {"class": type(error).__name__, "message": str(error)}}
    return {**case, "model": model_facts(model), "outcome": outcome, "calls": provider.calls,
            "saved": saved, "progress": progress, "records_after": wrapper.records}


def main() -> None:
    vectors = {
        "request_data": request_data_cases(),
        "pure": pure_cases(),
        "scenarios": [asyncio.run(run(case)) for case in scenarios()],
    }
    text = json.dumps(vectors, ensure_ascii=False, indent=1, allow_nan=False) + "\n"
    OUTPUT.write_bytes(text.encode("utf-8"))
    print(len(vectors["scenarios"]), "scenarios,", len(text), "bytes")
    for scenario in vectors["scenarios"]:
        outcome = scenario["outcome"]
        print(" ", scenario["name"], "->", outcome.get("error", {}).get("class", "ok"),
              len(scenario["calls"]), "calls", len(scenario["saved"]), "saved")


if __name__ == "__main__":
    main()
