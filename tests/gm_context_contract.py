"""Wire-contract helpers for the append-only GM request ledger (SPEC section 5).

``LedgerRecorder`` captures every request the context ledger plans and commits in a real owner
run: the canonical list it reconciled, its epoch and generation, and the wire it produced.
``replay`` feeds the same canonical sequence through a fresh ledger for another transport, and
``serialize`` renders a wire list the way each route sends it, so the byte-for-byte extension
invariant can be checked after transport serialization, without any model call.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sylvia_gm.agent import _context_ledger as ledger_module
from sylvia_gm.agent._context_ledger import (
    LIFTING_TRANSPORTS,
    STATE_LABEL,
    UPDATE_LABEL,
    ContextLedger,
    JournalState,
    host_seal,
    ledger_contract,
    native_inputs,
    seal_prefix,
    state_covers,
)
from sylvia_gm.agent._request_data import strip_data_markers
from sylvia_gm.provider._claude_native import NativeRoute, collect

#: Claude Code session files from offline captures (see fixtures/claude-native/README.md).
NATIVE_FIXTURES = Path(__file__).parent / "fixtures" / "claude-native"

TRANSPORTS = ("anthropic_messages", "claude_code", "codex_app_server", "chat_completions",
              "responses")


@dataclass
class Recorded:
    session_id: str
    context_owner: dict[str, Any] | None
    transport: str | None
    context_id: str
    canonical: list[dict[str, Any]]
    epoch: str
    generation: int
    reset: str | None
    base_count: int
    appended: list[dict[str, Any]]
    wire: list[dict[str, Any]]
    view: dict[str, Any] | None
    observation: dict[str, Any] = field(default_factory=dict)
    #: claude_code only: the native route the provider builds its transcript from.
    native: NativeRoute | None = None


def native_route(state: JournalState, wire: Sequence[dict[str, Any]]) -> NativeRoute:
    """The route the GM adapter hands Claude Code for this committed journal state."""
    boundaries, records = native_inputs(state)
    return NativeRoute(stable_count=len(wire) - len(state.entries),
                       entry_count=len(state.entries), boundaries=boundaries,
                       native_records=records)


def first_step_records() -> list[dict[str, Any]]:
    """The context records Claude Code wrote after a first prompt, as collection copies them."""
    result = collect(NATIVE_FIXTURES / "first-step.jsonl", "SENTINEL_H1_USER hello")
    assert result.status == "collected" and len(result.records) == 3, result
    return [dict(record) for record in result.records]


def simulate_collection(state: JournalState, route: NativeRoute) -> None:
    """What the provider reports after a step: the CLI's records on a generation's first step."""
    if route.first_step:
        state.native[route.entry_count] = {"status": "collected", "records": first_step_records()}


class LedgerRecorder:
    """Patch ``ContextLedger`` so every committed frame is kept with its canonical input."""

    def __init__(self) -> None:
        self.frames: list[Recorded] = []
        self._originals: tuple[Any, Any] | None = None

    def stop(self) -> list[Recorded]:
        """Restore the unpatched ledger (replays must not be recorded) and return the frames."""
        if self._originals is not None:
            ContextLedger.plan, ContextLedger.commit = self._originals  # type: ignore[method-assign]
            self._originals = None
        return list(self.frames)

    def install(self, monkeypatch: Any) -> LedgerRecorder:
        original_plan = ContextLedger.plan
        original_commit = ContextLedger.commit
        recorder = self

        def plan(ledger: ContextLedger, canonical: Any, **options: Any) -> Any:
            frame = original_plan(ledger, canonical, **options)
            frame._contract_canonical = copy.deepcopy(list(canonical))  # type: ignore[attr-defined]
            frame._contract_context = options["request_context_id"]  # type: ignore[attr-defined]
            return frame

        def commit(ledger: ContextLedger, frame: Any) -> None:
            original_commit(ledger, frame)
            recorder.frames.append(Recorded(
                session_id=ledger.session_id,
                context_owner=copy.deepcopy(ledger.context_owner),
                transport=ledger.transport,
                context_id=frame._contract_context,
                canonical=frame._contract_canonical,
                epoch=frame.state.epoch,
                generation=frame.state.generation,
                reset=frame.reset,
                base_count=frame.base_count,
                appended=copy.deepcopy(frame.appended),
                wire=copy.deepcopy(frame.wire),
                view=copy.deepcopy(frame.state.view),
                observation=dict(frame.observation),
                native=(native_route(frame.state, frame.wire)
                        if ledger.transport == "claude_code" else None),
            ))

        self._originals = (original_plan, original_commit)
        monkeypatch.setattr(ContextLedger, "plan", plan)
        monkeypatch.setattr(ContextLedger, "commit", commit)
        return self


def provider_messages(wire: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The provider list without native continuation (scripted runs have none to attach)."""
    return strip_data_markers([
        {key: value for key, value in message.items()
         if key not in {"_native_reference", "_context_owner"}}
        for message in wire
    ])


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _without_cache_control(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _without_cache_control(v) for k, v in value.items() if k != "cache_control"}
    if isinstance(value, list):
        return [_without_cache_control(item) for item in value]
    return value


def serialize(transport: str, wire: Sequence[dict[str, Any]],
              tools: list[dict[str, Any]] | None = None,
              native: NativeRoute | None = None) -> tuple[str, list[str]]:
    """(system prefix, ordered message bytes) exactly as the route would send them.

    For ``claude_code`` with the ledger's native route this is the transcript plus the stdin
    prompt, including Sylvia's fillers and the CLI context records it replays.
    """
    messages = provider_messages(wire)
    if transport == "anthropic_messages":
        from sylvia_gm.provider._anthropic import (
            AnthropicMessagesTransport,
            with_cache_breakpoints,
        )

        request = AnthropicMessagesTransport().format_request("model", messages, tools, True)
        # First-party Anthropic adds breakpoints; they mark the prefix and are not content.
        request = _without_cache_control(with_cache_breakpoints(request))
        return _dump(request.get("system")), [_dump(m) for m in request["messages"]]
    if transport == "claude_code":
        from sylvia_gm.provider._claude import prepare_request

        system, records, prompt, _tools = prepare_request(messages, tools, "model", Path("."),
                                                          native)
        sequence = []
        for record in records:
            if record["type"] == "attachment":
                sequence.append(_dump({k: record[k] for k in ("attachment", "rendered")}))
                continue
            message = dict(record["message"])
            # Per-request native transcript ids are disposable wrappers, not message content.
            message.pop("id", None)
            sequence.append(_dump(message))
        sequence.append(_dump(prompt["message"]))
        return system, sequence
    if transport == "codex_app_server":
        from sylvia_gm.provider._chatgpt import app_server_request

        _dynamic, items, instructions = app_server_request("model", messages, tools)
        return "\n\n".join(instructions), [_dump(item) for item in items]
    if transport == "chat_completions":
        from sylvia_gm.provider._openai_compat import ChatCompletionsTransport

        body = ChatCompletionsTransport().format_request("model", messages, tools, True)
        rows = body["messages"]
    elif transport == "responses":
        from sylvia_gm.provider._openai_compat import ResponsesTransport

        body = ResponsesTransport().format_request("model", messages, tools, True)
        rows = body["input"]
    else:
        raise ValueError(transport)
    lead = 0
    while lead < len(rows) and rows[lead].get("role") in {"system", "developer"}:
        lead += 1
    return _dump(rows[:lead]), [_dump(row) for row in rows[lead:]]


def replay(frames: Iterable[Recorded], transport: str) -> list[Recorded]:
    """Run the recorded canonical sequence through fresh ledgers for *transport*."""
    ledgers: dict[str, ContextLedger] = {}
    replayed = []
    for frame in list(frames):
        ledger = ledgers.setdefault(frame.session_id, ContextLedger(
            session_id=frame.session_id, context_owner=frame.context_owner, transport=transport))
        planned = ledger.plan(frame.canonical, request_context_id=frame.context_id)
        ledger.commit(planned)
        route = None
        if transport == "claude_code":
            route = native_route(planned.state, planned.wire)
            simulate_collection(planned.state, route)
        replayed.append(Recorded(
            session_id=frame.session_id, context_owner=frame.context_owner, transport=transport,
            context_id=frame.context_id, canonical=frame.canonical, epoch=planned.state.epoch,
            generation=planned.state.generation, reset=planned.reset,
            base_count=planned.base_count, appended=planned.appended, wire=planned.wire,
            view=copy.deepcopy(planned.state.view), observation=planned.observation,
            native=route,
        ))
    return replayed


def _note(canonical: Sequence[dict[str, Any]]) -> str | None:
    return next((str(m.get("content")) for m in canonical
                 if str(m.get("content", "")).startswith("SYLVIA_CONTINUATION_NOTE ")), None)


def check_extension(frames: Sequence[Recorded], transport: str) -> dict[str, Any]:
    """Assert the wire invariant on consecutive same-epoch requests; return reset statistics."""
    previous: dict[str, tuple[Recorded, tuple[str, list[str]]]] = {}
    resets: list[tuple[int, str]] = []
    pairs = 0
    for index, frame in enumerate(frames):
        rendered = serialize(transport, frame.wire, native=frame.native)
        before = previous.get(frame.session_id)
        if before is not None:
            last, (system, sequence) = before
            fresh = frame.base_count == 0 and frame.reset is not None
            if (frame.epoch, frame.generation) == (last.epoch, last.generation) and not fresh:
                pairs += 1
                assert rendered[0] == system, (transport, index, "system prefix changed")
                assert rendered[1][: len(sequence)] == sequence, (
                    transport, index, "request does not extend the previous one",
                    _first_difference(sequence, rendered[1]),
                )
            else:
                # Only declared resets: a new checkpoint or context owner starts an epoch;
                # a missing identity or invalid journal starts a generation.
                reason = frame.reset or "unrecorded"
                resets.append((index, reason))
                if frame.epoch != last.epoch:
                    assert (_note(frame.canonical) != _note(last.canonical)
                            or frame.context_id != last.context_id
                            or frame.context_owner != last.context_owner), (
                        transport, index, "undeclared epoch change")
                else:
                    assert reason in {"identity_missing", "journal_invalid",
                                      "journal_started", "journal_rebased"}, (
                        transport, index, reason)
        previous[frame.session_id] = (frame, rendered)
    return {"pairs": pairs, "resets": resets}


def _first_difference(before: list[str], after: list[str]) -> Any:
    for position, (old, new) in enumerate(zip(before, after, strict=False)):
        if old != new:
            return {"position": position, "before": old[:400], "after": new[:400]}
    return {"before_length": len(before), "after_length": len(after)}


def canonical_state(canonical: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    content = next((str(m.get("content")) for m in reversed(canonical)
                    if m.get("role") == "system"
                    and str(m.get("content", "")).startswith(STATE_LABEL + " ")), None)
    if content is None:
        return None
    return json.loads(content.split("\n", 1)[1])


def check_freshness(frames: Sequence[Recorded]) -> int:
    """After every request, keyframe plus updates reconstructs the canonical state."""
    checked = 0
    for frame in frames:
        state = canonical_state(frame.canonical)
        if state is None:
            continue
        assert state_covers(frame.view, state), "the model's state is stale"
        assert frame.view["campaign"]["revision"] == state["campaign"]["revision"]
        seal = seal_prefix(host_seal(frame.session_id))
        last = next(m for m in reversed(frame.wire) if str(m.get("content", "")).startswith(
            (seal + STATE_LABEL, seal + UPDATE_LABEL)))
        body = json.loads(str(last["content"]).split("\n", 1)[1])
        revision = (body["campaign"]["revision"] if "campaign" in body
                    else body["campaign_revision"])
        assert revision == state["campaign"]["revision"], "last state record is not current"
        checked += 1
    return checked


def check_authority(frames: Sequence[Recorded]) -> None:
    """The frozen prefix carries the ledger contract; every user-role host record is sealed."""
    for frame in frames:
        seal = seal_prefix(host_seal(frame.session_id))
        prefix = []
        for message in frame.wire:
            if message.get("role") != "system":
                break
            prefix.append(message.get("content"))
        assert ledger_contract(host_seal(frame.session_id)) in prefix
        for entry in frame.appended:
            if entry["c"] != "conversation" and entry["m"]["role"] == "user":
                assert str(entry["m"]["content"]).startswith(seal)
            if entry["c"] == "conversation" and entry["m"]["role"] == "user":
                assert not str(entry["m"]["content"]).startswith(seal)
            if frame.transport in LIFTING_TRANSPORTS:
                assert entry["c"] == "conversation" or entry["m"]["role"] == "user"


def growth(frames: Sequence[Recorded], transport: str) -> dict[str, Any]:
    """Bytes appended per request and the share that repeats the previous request."""
    rows = []
    previous: dict[str, tuple[Recorded, int, int]] = {}
    for frame in frames:
        system, sequence = serialize(transport, frame.wire, native=frame.native)
        total = len(system.encode()) + sum(len(item.encode()) for item in sequence)
        before = previous.get(frame.session_id)
        repeated = 0
        if before is not None:
            last, last_total, _ = before
            last_system, last_sequence = serialize(transport, last.wire, native=last.native)
            if system == last_system:
                repeated = len(system.encode())
                for old, new in zip(last_sequence, sequence, strict=False):
                    if old != new:
                        break
                    repeated += len(new.encode())
        rows.append({"total_bytes": total, "repeated_bytes": repeated,
                     "appended_bytes": total - repeated,
                     "repeat_share": round(repeated / total, 4) if total else None})
        previous[frame.session_id] = (frame, total, repeated)
    return {"requests": rows,
            "total_wire_bytes": sum(row["total_bytes"] for row in rows),
            "total_appended_bytes": sum(row["appended_bytes"] for row in rows)}


__all__ = [
    "TRANSPORTS", "LedgerRecorder", "Recorded", "check_authority", "check_extension",
    "check_freshness", "first_step_records", "growth", "ledger_module", "native_route",
    "replay", "serialize", "simulate_collection",
]


_SEAL = __import__("re").compile(r"^\[host-seal [0-9a-f]{12}\] ")


def unsealed(content: object) -> str:
    """A host record's text without its wire seal (legacy records have none)."""
    return _SEAL.sub("", str(content or ""), count=1)


def host_records(messages: Sequence[dict[str, Any]], label: str) -> list[dict[str, Any]]:
    """Messages carrying *label*, oldest first, in either request assembly."""
    return [
        {**message, "content": unsealed(message.get("content"))}
        for message in messages
        if unsealed(message.get("content")).startswith(label)
    ]


def current_state(messages: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    """The campaign state a request conveys: the single legacy record, or keyframe + updates."""
    from sylvia_gm.agent._context_ledger import apply_state_update

    view: dict[str, Any] | None = None
    for message in messages:
        content = unsealed(message.get("content"))
        if content.startswith(STATE_LABEL + " "):
            view = json.loads(content.split("\n", 1)[1])
        elif content.startswith(UPDATE_LABEL + " ") and view is not None:
            view = apply_state_update(view, json.loads(content.split("\n", 1)[1]))
    return view


TAGS_FIELD = "__ledger_tags__"


def frame_to_json(frame: Recorded) -> dict[str, Any]:
    """A JSON form of a recorded frame that keeps the canonical messages' attribute tags."""
    import dataclasses

    row = dataclasses.asdict(frame)
    if frame.native is not None:
        row["native"] = {"stable_count": frame.native.stable_count,
                         "entry_count": frame.native.entry_count,
                         "boundaries": list(frame.native.boundaries),
                         "native_records": {str(k): list(v) for k, v in
                                            frame.native.native_records.items()}}
    row["canonical"] = [
        {**dict(message), TAGS_FIELD: dict(getattr(message, "ledger_tags", {}) or {})}
        for message in frame.canonical
    ]
    return row


def frame_from_json(row: dict[str, Any]) -> Recorded:
    from sylvia_gm.agent._context_ledger import tagged

    canonical = []
    for message in row["canonical"]:
        message = dict(message)
        tags = message.pop(TAGS_FIELD, None) or {}
        canonical.append(tagged(message, **tags) if tags else message)
    native = row.get("native")
    if isinstance(native, dict):
        native = NativeRoute(stable_count=native["stable_count"],
                             entry_count=native["entry_count"],
                             boundaries=tuple(native["boundaries"]),
                             native_records={int(k): v for k, v in
                                             native["native_records"].items()})
    return Recorded(**{**row, "canonical": canonical, "native": native})



def narration_facts_record(messages: Sequence[dict[str, Any]]) -> str:
    """The narration-facts record a request conveys, in the canonical (full-list) form.

    Legacy requests carry one record; ledger requests may carry the turn's first list and then
    records that add to it. Raises StopIteration when the request carries none, like the
    ``next(...)`` lookups it replaces.
    """
    from sylvia_gm.agent._context_ledger import (
        _FACTS_HEADER,
        _json,
        split_narration_facts,
    )

    given: list[Any] = []
    tail: str | None = None
    for message in messages:
        raw = str(message.get("content", ""))
        content = unsealed(raw)
        if message.get("role") == "user" and content == raw and not content.startswith(
            "SYLVIA_"
        ):
            given, tail = [], None  # a player message starts a new turn
            continue
        parsed = split_narration_facts(content)
        if parsed is not None:
            added, facts, tail = parsed
            given = [*given, *facts] if added else list(facts)
    if tail is None:
        raise StopIteration
    return _FACTS_HEADER + _json(given) + tail
