"""Write ledger_state_vectors.json: the context ledger's wire and state functions (L2a).

Run from the repository root with the snapshot's interpreter, after `cargo build -p
sylvia-compare`:

    .venv/Scripts/python.exe -I crates/sylvia-agent/tests/vectors/generate_ledger_state_vectors.py

Two sources of cases, every answer Python's own from `agent/_context_ledger.py`:

- `traced`: the inputs real GM requests give these functions. The `ruleless-turn`,
  `srd-mechanics-turn` and `fail-retry` sessions are recorded with the Python owner in forced
  ledger mode (`SYLVIA_GM_CONTEXT_MODE=ledger`) under `trace_ledger_functions.py`, which logs
  each call's arguments. Each run's own identities (uuids, host seals, timestamps) are then
  renamed to stable stand-ins in order of first appearance, the same value always to the same
  stand-in, and each distinct call is made again here on the renamed inputs, so the file is
  byte-identical on every run.
- `synthetic`: hand-made inputs for the rules those sessions do not reach (fact lists and
  retired records, additive maps, replacement depths, removed keys, malformed updates, seal-like
  player text, tool exchanges).

Reference tooling for the port; retired with the Python source.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.agent import _context_ledger as ledger  # noqa: E402

FUNCTIONS = [
    "request_values", "slim_narration_facts", "comparable_facts", "slim_transition_result",
    "split_narration_facts", "narration_facts_given", "conversation_keys", "state_target",
    "state_update", "apply_state_update", "state_covers", "update_record_content",
    "reconstruct_state", "_wire_message", "_label", "is_host_record", "_is_data_record",
    "fact_key", "_parse_state",
]
SESSIONS = ["ruleless-turn", "srd-mechanics-turn", "fail-retry"]
VOLATILE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"|(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])"
    r"|(?<![0-9a-f])[0-9a-f]{12}(?![0-9a-f])"
    r"|\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?"
)


class Renamer:
    """Each volatile value to a stand-in of the same shape, in order of first appearance."""

    def __init__(self) -> None:
        self.names: dict[str, str] = {}

    def text(self, text: str) -> str:
        return VOLATILE.sub(lambda match: self.name(match.group(0)), text)

    def name(self, value: str) -> str:
        if value not in self.names:
            number = len(self.names) + 1
            if "T" in value:
                stand_in = f"2026-01-01T00:00:{number % 60:02d}+00:00"
            elif "-" in value:
                digits = f"{number:032x}"
                stand_in = f"{digits[:8]}-{digits[8:12]}-4{digits[13:16]}-8{digits[17:20]}-{digits[20:]}"
            else:
                stand_in = f"{number:0{len(value)}x}"
            self.names[value] = stand_in
        return self.names[value]

    def value(self, value: object) -> object:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.value(item) for item in value]
        if isinstance(value, dict):
            return {self.text(key): self.value(item) for key, item in value.items()}
        return value


def decode(value: object) -> object:
    """A traced argument as the Python value it was: tagged messages and sets restored."""
    if isinstance(value, dict):
        if set(value) == {"__tagged__"}:
            tagged = value["__tagged__"]
            return ledger.Tagged(decode(tagged["message"]), decode(tagged["tags"]))
        if set(value) == {"__set__"}:
            return set(decode(value["__set__"]))
        return {key: decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [decode(item) for item in value]
    return value


def encode(value: object) -> object:
    """A value as the vector file holds it (the tracer's form)."""
    if isinstance(value, ledger.Tagged):
        return {"__tagged__": {"message": encode(dict(value)), "tags": encode(value.ledger_tags)}}
    if isinstance(value, dict):
        return {str(key): encode(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return {"__set__": sorted((encode(item) for item in value), key=repr)}
    if isinstance(value, (list, tuple)):
        return [encode(item) for item in value]
    return value


def call(function: str, args: list[object], kwargs: dict[str, object]) -> dict[str, object]:
    case: dict[str, object] = {"function": function, "args": args, "kwargs": kwargs}
    try:
        result = getattr(ledger, function)(*decode(args), **decode(kwargs))
    except Exception as error:  # noqa: BLE001 - the class is the vector
        case["raised"] = {"class": type(error).__name__, "message": str(error)}
    else:
        case["result"] = encode(result)
    return case


def traced() -> list[dict[str, object]]:
    compare = ROOT / "target" / "debug" / ("sylvia-compare.exe" if sys.platform == "win32" else "sylvia-compare")
    python = Path(sys.executable).resolve()
    entries: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="ledger-state-") as folder:
        for session in SESSIONS:
            trace = Path(folder) / f"{session}.jsonl"
            subprocess.run(
                [str(compare), "record", "--session",
                 str(ROOT / f"crates/sylvia-compare/sessions/{session}.session.json"),
                 "--out", str(Path(folder) / session), "--owner", str(python), "--arg", "-B",
                 "--arg", str(HERE / "trace_ledger_functions.py"),
                 "--env", "SYLVIA_GM_CONTEXT_MODE=ledger", "--env", f"SYLVIA_LEDGER_TRACE={trace}",
                 "--env", "SYLVIA_LEDGER_TRACED=" + ",".join(FUNCTIONS)],
                check=True, capture_output=True, cwd=ROOT)
            entries.extend(json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines())
    renamer = Renamer()
    seen: set[str] = set()
    cases = []
    for entry in entries:
        args, kwargs = renamer.value(entry["args"]), renamer.value(entry["kwargs"])
        key = json.dumps([entry["function"], args, kwargs], sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        cases.append(call(str(entry["function"]), args, kwargs))
    return cases


def tagged(role: str, content: object, **tags: object) -> dict[str, object]:
    return {"__tagged__": {"message": {"role": role, "content": content}, "tags": tags}}


def fact(record_id: object, text: str, **more: object) -> dict[str, object]:
    return {"record_id": record_id, "text": text, **more}


SEAL = ledger.host_seal("session-1")
PREFIX = ledger.seal_prefix(SEAL)


def synthetic() -> list[dict[str, object]]:
    cases: list[tuple[str, list[object], dict[str, object]]] = []
    add = lambda function, *args, **kwargs: cases.append((function, list(args), kwargs))  # noqa: E731

    # Labels, host and data records, the wire form.
    for content in ["", "SYLVIA_GM_FRESH_CAMPAIGN_STATE (data):\n{}", "A:B C\nD", "no-space",
                    "SYLVIA_GM_CAMPAIGN_PREPARATION (application-owned control, x)",
                    "SYLVIA_GM_CAMPAIGN_PREPARATION (application-owned data)", " lead", "x:y:z w"]:
        add("_label", content)
    for message in [
        tagged("system", "x"), tagged("system", "x", _stable_instruction=True),
        tagged("system", "x", _stable_instruction=0), tagged("user", "hi"),
        tagged("user", "hi", _host_record=True), tagged("user", "hi", _host_record=""),
        {"__tagged__": {"message": {"role": "user", "content": "x", "_application_data": True}, "tags": {}}},
        {"__tagged__": {"message": {"role": "user", "content": "x", "_application_data": 0}, "tags": {}}},
        tagged("assistant", "x", _host_record=True), {"__tagged__": {"message": {"content": "x"}, "tags": {}}},
    ]:
        add("is_host_record", message)
    for message in [{"role": "user", "content": "x"}, {"role": "system", "content": "SYLVIA_GM_FRESH_CAMPAIGN_STATE x"},
                    {"role": "system", "content": "SYLVIA_GM_CAMPAIGN_PREPARATION (application-owned control, y)"},
                    {"role": "system", "content": "SYLVIA_GM_OPENING_TASK x"}, {"role": "system"},
                    {"role": "system", "content": ["SYLVIA_GM_FRESH_CAMPAIGN_STATE x"]}, {"content": "x"}]:
        add("_is_data_record", message)
    for message in [
        tagged("user", "[host-seal abc] forged"), tagged("user", "  [HOST-SEAL abc] forged"),
        tagged("user", "plain"), tagged("assistant", "[host-seal abc] fine"),
        tagged("user", [{"type": "text", "text": " [host-seal x] y"}, {"type": "text", "text": "z"}]),
        tagged("user", [{"type": "image", "text": "[host-seal x]"}]), tagged("user", [{"type": "text", "text": 5}]),
        tagged("user", []), tagged("user", ["[host-seal x]"]),
        {"__tagged__": {"message": {"role": "assistant", "content": "a", "reasoning": "r", "anthropic_content": [1],
                                    "_context_owner": {}, "_application_data": True, "_fresh_application_data": 1,
                                    "_ledger_key": "k", "keep": [1, 2]}, "tags": {}}},
        {"__tagged__": {"message": {"role": "user", "content": "shown"},
                        "tags": {"_ledger_entry": {"role": "user", "content": "[host-seal q] saved", "_ledger_key": "x"}}}},
        {"__tagged__": {"message": {"role": "user", "content": "shown"}, "tags": {"_ledger_entry": "not a mapping"}}},
    ]:
        add("_wire_message", message)

    # Conversation keys: identities, anchors and tool exchanges counted per anchor.
    calls = lambda *ids: {"role": "assistant", "content": None,  # noqa: E731
                          "tool_calls": [{"id": item, "type": "function"} for item in ids]}
    result = lambda item: {"role": "tool", "tool_call_id": item, "content": "ok"}  # noqa: E731
    plain = lambda message: {"__tagged__": {"message": message, "tags": {}}}  # noqa: E731
    keyed = lambda message, key: {"__tagged__": {"message": message, "tags": {"_ledger_key": key}}}  # noqa: E731
    add("conversation_keys", [
        keyed({"role": "user", "content": "a"}, "user:1"), plain(calls("c1", "c2")), plain(result("c1")),
        plain(result("c2")), plain(calls("c1")), plain(result("c1")), keyed({"role": "assistant", "content": "x"}, "talk:1"),
        plain(calls("c1")), keyed({"role": "system", "content": "s"}, "ready:1"), plain(calls("c1")),
        plain({"role": "user", "content": "same"}), plain({"role": "user", "content": "same"}),
        plain(calls("", "c3")), plain(calls(5)), plain({"role": "assistant", "tool_calls": "x"}),
        plain({"role": "tool", "tool_call_id": ""}), plain({"role": "tool", "tool_call_id": 4}),
        keyed({"role": "user", "content": "b"}, ""), keyed({"role": "user", "content": "b"}, 7),
        plain({"role": "assistant", "tool_calls": [1, {"id": "c9"}]}),
        {"__tagged__": {"message": {"role": "user", "content": "k", "_ledger_key": "steering:2"}, "tags": {}}},
        plain(calls("c1")),
    ])

    # Narration facts.
    entities = {"e1": {"hp": 5}, "e2": {"hp": 1.0}}
    add("request_values", None)
    add("request_values", {})
    add("request_values", {"campaign_state": {"ruleset_owned_values": {
        "pkg.entities": {"e1": {"hp": 5}, "e2": 1}, "pkg.state": {"round": 2}, "other.entities": "x",
        "late.entities": {"e1": {"hp": 6}}}}})
    add("request_values", {"campaign_state": None})
    add("request_values", {"campaign_state": []})
    add("request_values", {"campaign_state": [1]})
    add("request_values", {"campaign_state": {"ruleset_owned_values": [1]}})
    add("slim_narration_facts", [
        {"outcome": 1, "current_entities": {"e1": {"hp": 5}, "e2": {"hp": 1}, "e3": {}}},
        {"current_entities": {"e1": {"hp": 5}}}, {"current_entities": {"e1": {"hp": 4}}},
        {"current_entities": [1]}, "text", 3, {"current_entities": {}},
        {"a": 1, "current_entities": {"e2": {"hp": True}}, "z": 2}], entities)
    add("comparable_facts", [{"a": 1, "current_entities": {}, "b": 2}, "x", [1], {"current_entities": 1}])
    namespaces = {"pkg.state": {"round": 2}, "pkg.time": {"t": 1}, "state": {"round": 2}}
    for payload in [
        {"current_entities": {"e1": {"hp": 5}}, "result": {"context": {
            "actor": {"hp": 5}, "_actor_id": "e1", "completed_actor": {"hp": 2}, "_completed_actor_id": "e2",
            "lasting_state": {"round": 2}, "_lasting_state_id": "state", "time_stop_state": {"t": 2},
            "_time_stop_state_id": "time", "keep": 1}, "rows": [1]}},
        {"result": {"context": {"actor": {"hp": 5}, "_actor_id": 7}}},
        {"result": {"context": {"_actor_id": "e1"}}}, {"result": {"context": "x"}}, {"result": [1]},
        {"current_entities": {"e9": {}}}, {"x": 1},
        {"result": {"context": {"lasting_state": {"round": 2.0}, "_lasting_state_id": "pkg.state"}}},
        {"result": {"context": {"lasting_state": {"round": 2}, "_lasting_state_id": "nope"}}},
    ]:
        add("slim_transition_result", payload, entities, namespaces)
    full, added = ledger._FACTS_HEADER, ledger._FACTS_ADDED_HEADER
    for content in [full + '[{"a":1}] then narrate', added + "[1, 2]\nrest", full + '{"a":1}', full + "[1",
                    full + " [1]", "SYLVIA_GM_EXACT_NARRATION_FACTS other", added + "[]", full + "[1] [2]",
                    full + '["é\U0001f3b2"]tailé']:
        add("split_narration_facts", content)
    record = lambda header, facts, sealed=True: {"role": "user", "content": (  # noqa: E731
        PREFIX if sealed else "") + header + json.dumps(facts)}
    add("narration_facts_given", [
        {"role": "user", "content": "earlier player"}, record(full, [1]), record(added, [2]),
        {"role": "system", "content": "unrelated"}, record(added, [3]), {"role": "assistant", "content": "x"},
        record(full, [9], sealed=False)], SEAL)
    add("narration_facts_given", [record(full, [1]), record(added, [2]), {"role": "user", "content": "player"},
                                  record(added, [3]), record(added, [4])], SEAL)
    add("narration_facts_given", [record(full, [1]), {"role": "user", "content": PREFIX + "x" * 100
                                  + "SYLVIA_GM_EXACT_NARRATION_FACTS"}, record(added, [2])], SEAL)
    add("narration_facts_given", [], SEAL)
    add("narration_facts_given", [{"role": "system", "content": None}, {"role": "system"}], SEAL)

    # Campaign state.
    def state(**sections: object) -> dict[str, object]:
        return {"campaign": {"revision": sections.pop("revision", 1)}, **sections}

    held = state(PLAYER_KNOWN={"current_facts": [fact("r1", "a"), fact("r2", "b"), {"text": "anon"}, fact("r1", "dup")]},
                 GM_PRIVATE_CURRENT={"current_facts": [fact("g1", "x")], "character_knowledge": {"c1": [1, 2], "c2": "s"}},
                 campaign_state={"ruleset_owned_values": {"pkg.entities": {"e1": {"hp": 5, "ac": 12}}},
                                 "current_scene": {"name": "Gate", "light": {"level": 1}}, "turn": 3})
    canonical = state(revision=2,
                      PLAYER_KNOWN={"current_facts": [fact("r2", "b2"), fact("r3", "c"), {"text": "anon"}]},
                      GM_PRIVATE_CURRENT={"current_facts": [fact("g2", "y")], "character_knowledge": {"c1": [2, 3, 3], "c3": [4]}},
                      campaign_state={"ruleset_owned_values": {"pkg.entities": {"e1": {"hp": 4, "ac": 12}, "e2": {"hp": 1}}},
                                      "current_scene": {"name": "Gate", "light": {"level": 2}}, "turn": 3.0})
    active = {"PLAYER_KNOWN.current_facts": ["r2", "r3"], "GM_PRIVATE_CURRENT.current_facts": "x"}
    for view in [None, held, {}, {"PLAYER_KNOWN": {"current_facts": "x"}}]:
        for given_active in [None, active, {"PLAYER_KNOWN.current_facts": ["r1", "r2", "r3", 5]}]:
            add("state_target", view, canonical, given_active)
            add("state_covers", view, canonical, given_active)
            if view is not None:
                add("state_update", view, canonical, given_active)
    add("state_update", canonical, canonical)
    add("state_update", held, held)
    add("state_update", {"a": 1, "b": {"c": 1}}, {"b": {"c": 1}, "a": True})
    add("state_update", {"x": {"y": {"z": 1}}, "gone": 1}, {"x": {"y": {"z": 2, "w": 3}}})
    add("state_update", {"campaign_state": {"current_scene": {"a": {"b": 1}}}},
        {"campaign_state": {"current_scene": {"a": {"b": 2}}, "new": 1}, "campaign": {"revision": 4}})
    add("state_update", {"campaign_state": {"ruleset_owned_values": {"p": {"e": {"f": {"g": 1}, "h": 1}}}}},
        {"campaign_state": {"ruleset_owned_values": {"p": {"e": {"f": {"g": 2}}}}}})
    add("state_update", {"PLAYER_KNOWN": {"current_facts": [fact("r1", "a")]}},
        {"PLAYER_KNOWN": {"current_facts": [fact("r1", "a"), None]}})
    update = ledger.state_update(held, canonical, active)
    assert update is not None
    for view, given in [
        (held, update), (held, {}), ({"a": 1}, {"set": [{"path": [], "value": {"b": 2}}]}),
        ({"a": 1}, {"set": [{"path": [], "value": 3}]}),
        ({"a": {"b": 1}}, {"set": [{"path": ["a", "b", "c"], "value": 1}], "remove": [["a", "x"], [], ["z", "q"]]}),
        ({"a": 1}, {"set": [{"path": "ab", "value": 1}]}), ({"a": 1}, {"set": [{"path": [1, True, None], "value": 1}]}),
        ({"a": 1}, {"set": [{"value": 1}]}), ({"a": 1}, {"set": [["path"]]}), ({"a": 1}, {"set": 5}),
        ({"a": 1}, {"facts": [{"path": ["F", "l"], "upsert": [fact("x", "1"), fact("x", "2")], "remove": ["y"]}]}),
        ({"F": {"l": [fact("y", "1"), fact("z", "2")]}}, {"facts": [{"path": ["F", "l"], "remove": ["y", "q"]}]}),
        ({"a": 1}, {"facts": [{"path": ["F"], "remove": [[1]]}]}), ({"a": 1}, {"facts": [{"path": []}]}),
        ({"a": 1}, {"remove": "ab"}),
    ]:
        add("apply_state_update", view, given)
    for item in [fact("r1", "a"), {"record_id": ""}, {"record_id": 5, "t": 1}, "text", [1, {"b": 1, "a": 2}],
                 None, {"z": 1, "a": "é"}, 1.5]:
        add("fact_key", item)
    for content in ["SYLVIA_GM_FRESH_CAMPAIGN_STATE (x):\n{\"a\": 1}", "no newline", "L\n[1]", "L\nnot json",
                    "L\n {\"a\": 1} ", "L\n{\"a\": 1}\nmore"]:
        add("_parse_state", content)
    add("update_record_content", update)
    add("update_record_content", {"campaign_revision": None, "set": [{"path": ["é"], "value": "\U0001f3b2"}]})
    keyframe = ledger.STATE_LABEL + " (application-owned current data):\n" + json.dumps(held)
    entry = lambda content, c="state": {"c": c, "m": {"role": "system", "content": content}}  # noqa: E731
    for entries in [
        [entry(PREFIX + keyframe), entry(PREFIX + ledger.update_record_content(update))],
        [entry(keyframe), entry(ledger.update_record_content(update), c="event")],
        [entry(PREFIX + ledger.update_record_content(update))],
        [entry(PREFIX + keyframe), entry(PREFIX + ledger.STATE_LABEL + " broken\nnot json"),
         entry(PREFIX + ledger.update_record_content(update))],
        [entry(PREFIX + keyframe), entry(PREFIX + ledger.UPDATE_LABEL + " x\n[1]")],
        [entry(PREFIX + keyframe), {"c": "state", "m": {"role": "system"}}], [{"c": "state"}],
        [{"c": "state", "m": "text"}], [], [entry(ledger.STATE_LABEL + "X\n{}")],
    ]:
        add("reconstruct_state", entries, SEAL)
    return [call(function, args, kwargs) for function, args, kwargs in cases]


def main() -> None:
    vectors = {"traced": traced(), "synthetic": synthetic()}
    out = HERE / "ledger_state_vectors.json"
    out.write_bytes((json.dumps(vectors, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
    counts: dict[str, int] = {}
    for case in vectors["traced"]:
        counts[str(case["function"])] = counts.get(str(case["function"]), 0) + 1
    print("traced", counts, "synthetic", len(vectors["synthetic"]), file=sys.stderr)


if __name__ == "__main__":
    main()
