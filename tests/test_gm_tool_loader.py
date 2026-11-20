"""Sylvia's own tool loader (``SYLVIA_GM_TOOL_LIST=deferred`` on a route without native
deferral): a fixed declared list with one generic mechanic tool, compact appended
definitions, and a record of what the engine accepts now."""
from __future__ import annotations

import json
import re

import pytest

from sylvia_gm.agent._context_ledger import (
    CALLABLE_LABEL,
    LOADER_DEFERRAL,
    TOOL_CHANGE_CLASS,
    ContextLedger,
    deferred_tool_list,
)
from sylvia_gm.agent._tool_loader import (
    CHOICES_LABEL,
    DEFINITIONS_LABEL,
    INLINE_LIMIT,
    LISTED_NOTE,
    MECHANIC_TOOL,
    MECHANIC_TOOL_NAME,
    MechanicNames,
    compact_definition,
    definitions_record,
    project_conversation,
    unwrap_mechanic_call,
    wrap_mechanic_arguments,
)
from tests.test_gm_context_ledger import CONTEXT, OWNER, _canonical, _history, _talk, _user


def _tool(name: str, *, properties: dict | None = None, description: str | None = None,
          required: list[str] | None = None) -> dict:
    parameters: dict = {"type": "object", "properties": properties or {"note": {"type": "string"}}}
    if required:
        parameters["required"] = required
    return {"type": "function", "function": {
        "name": name, "description": description if description is not None else name.title(),
        "parameters": parameters}}


ATTACK = "tool_0123456789ab_attack"
CAST = "tool_0123456789ab_cast"
REST = "tool_aaaaaaaaaaaa_rest"
READ = "tool_bbbbbbbbbbbb_read_reference"
#: A mechanic too large to define up front: many optional fields.
BIG = "tool_cccccccccccc_begin_spell"
BIG_TOOL = _tool(BIG, properties={f"field_{i}": {"type": "boolean", "description": f"Fact {i}."}
                                  for i in range(60)}, description="Begin a spell. " * 30)
CATALOG = [_tool(ATTACK, required=["note"]),
           _tool(CAST, properties={"spell": {"type": "string", "enum": ["aid", "light"]}}),
           BIG_TOOL, _tool(READ), _tool(REST)]
CORE = frozenset({READ})


def _ledger(transport: str = "chat_completions") -> ContextLedger:
    return ContextLedger(session_id="s1", context_owner=OWNER, transport=transport,
                         fixed_tools=CATALOG, core_tools=CORE, deferral=LOADER_DEFERRAL)


def _names(tools) -> list[str]:
    return [tool["function"]["name"] for tool in tools]


def test_other_routes_use_the_loader_only_when_deferred_is_set():
    env = {"SYLVIA_GM_TOOL_LIST": "deferred"}
    for transport in ("chat_completions", "codex_app_server", "gemini_generate_content",
                      "responses"):
        assert deferred_tool_list(env, transport=transport, model_id="m") == LOADER_DEFERRAL
        assert deferred_tool_list({}, transport=transport, model_id="m") is None


def test_compact_definition_shows_the_argument_shape_tersely():
    schema = {
        "type": "object", "required": ["actor", "mode"],
        "$defs": {"point": {"type": "array", "items": {"type": "number"},
                            "minItems": 3, "maxItems": 3}},
        "properties": {
            "actor": {"type": "string", "description": "Exact participant id.",
                      "x-sylvia-gm-reference": "participant"},
            "mode": {"enum": ["near", "far"]},
            "at": {"$ref": "#/$defs/point"},
            "count": {"type": "integer", "minimum": 1, "maximum": 4},
            "items": {"type": "array", "items": {"type": "object", "required": ["id"],
                                                 "properties": {"id": {"type": "string"}}}},
            "spell": {"type": "string", "x-sylvia-gm-castable-spells": True},
            "tags": {"type": "object", "additionalProperties": {"type": "string"}},
        },
    }
    text = compact_definition({"type": "function", "function": {
        "name": "tool_0123456789ab_move", "description": "Move  one\nactor.",
        "parameters": schema}}, "move")
    assert text == "\n".join([
        "## move",
        "Move one actor.",
        "arguments: {",
        " // Exact participant id.",
        " actor: string // a participant id",
        ' mode: "near" | "far"',
        " at?: number[] // min items 3; max items 3",
        " count?: integer // >= 1; <= 4",
        " items?: {",
        "  id: string",
        " }[]",
        " spell?: string // a castable spell",
        " tags?: {[key: string]: string}",
        "}",
    ])
    # Far smaller than the JSON Schema it stands for.
    assert len(text) < len(json.dumps(schema)) * 0.6


def test_short_names_drop_the_alias_digest_unless_ambiguous():
    names = MechanicNames([ATTACK, "tool_ffffffffffff_attack", CAST, "tool_eeeeeeeeeeee_x"],
                          reserved={"x", READ})
    assert names.short(CAST) == "cast"
    # Two catalog mechanics share an operation, or it clashes with a declared tool.
    assert names.short(ATTACK) == ATTACK
    assert names.short("tool_eeeeeeeeeeee_x") == "tool_eeeeeeeeeeee_x"
    assert names.short(MECHANIC_TOOL_NAME) == MECHANIC_TOOL_NAME
    # A tool outside the catalog keeps its operation unless that is taken.
    assert names.short("tool_999999999999_breath") == "breath"
    assert names.short("tool_999999999999_cast") == "tool_999999999999_cast"
    offered = [CAST, ATTACK, "tool_999999999999_breath"]
    assert names.alias("cast", offered) == CAST
    assert names.alias("breath", offered) == "tool_999999999999_breath"
    assert names.alias(ATTACK, offered) == ATTACK
    assert names.alias("unknown", offered) == "unknown"


@pytest.mark.parametrize(("arguments", "expected"), [
    ('{"name": "cast", "arguments": {"spell": "aid"}}', ("cast", '{"spell":"aid"}')),
    ('{"name": " cast ", "arguments": "{\\"spell\\": \\"aid\\"}"}', ("cast", '{"spell":"aid"}')),
    ('{"name": "rest"}', ("rest", "{}")),
    ('{"name": "cast", "arguments": ["aid"]}', None),
    ('{"arguments": {}}', None),
    ('{"name": ""}', None),
    ("not json", None),
    ('["cast"]', None),
], ids=["object", "string-arguments", "no-arguments", "list-arguments", "no-name",
        "empty-name", "not-json", "not-object"])
def test_unwrap_reads_the_mechanic_and_its_arguments(arguments, expected):
    assert unwrap_mechanic_call(arguments) == expected


def test_projection_wraps_mechanic_calls_as_the_model_made_them():
    names = MechanicNames([ATTACK, CAST], reserved={READ})
    declared = frozenset({READ, MECHANIC_TOOL_NAME})
    assistant = {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": CAST,
                                                      "arguments": '{"spell":"aid"}'}},
        {"id": "c2", "type": "function", "function": {"name": READ, "arguments": "{}"}},
    ]}
    projected = project_conversation(assistant, declared, names)
    assert projected["tool_calls"][0]["function"] == {
        "name": MECHANIC_TOOL_NAME, "arguments": '{"name":"cast","arguments":{"spell":"aid"}}'}
    assert projected["tool_calls"][1] == assistant["tool_calls"][1]
    assert assistant["tool_calls"][0]["function"]["name"] == CAST  # canonical untouched
    assert project_conversation({"role": "tool", "tool_call_id": "c1", "name": CAST,
                                 "content": "{}"}, declared, names)["name"] == MECHANIC_TOOL_NAME
    assert project_conversation({"role": "tool", "tool_call_id": "c2", "name": READ,
                                 "content": "{}"}, declared, names)["name"] == READ
    # Unwrapping the projection gives the canonical call back.
    assert unwrap_mechanic_call(projected["tool_calls"][0]["function"]["arguments"]) == (
        "cast", '{"spell":"aid"}')
    assert wrap_mechanic_arguments("x", "not json") == '{"name":"x","arguments":"not json"}'


def _run(ledger: ContextLedger, rounds: list[list[dict]]):
    dialogue = [_user("u1", "Look around")]
    frames, records = [], []
    for offered in rounds:
        dialogue.append(_talk(f"t{len(dialogue)}", "ok"))
        frame = ledger.plan(_canonical(*dialogue), request_context_id=CONTEXT, tools=offered)
        ledger.commit(frame)
        frames.append(frame)
        records.append(frame.record)
    return dialogue, frames, records


def _labelled(frame, label: str) -> list[dict]:
    return [entry for entry in frame.appended if entry.get("l") == label]


def test_loader_declares_a_fixed_small_list_and_defines_each_mechanic_once():
    ledger = _ledger()
    outside = _tool("tool_999999999999_breath")
    rounds = [
        [_tool(READ), _tool(REST)],
        [_tool(READ), _tool(REST)],
        [_tool(ATTACK, required=["note"]),
         _tool(CAST, properties={"spell": {"type": "string", "enum": ["light"]}}), BIG_TOOL],
        [_tool(CAST, properties={"spell": {"type": "string", "enum": ["aid"]}}), outside],
        [_tool(READ)],
    ]
    dialogue, frames, records = _run(ledger, rounds)
    declared = frames[0].tool_list
    assert _names(declared) == [READ, MECHANIC_TOOL_NAME]
    assert declared[0] == _tool(READ) and declared[1] == MECHANIC_TOOL
    assert all(frame.tool_list == declared for frame in frames)
    assert all(not (frame.record or {}).get("tools_added") for frame in frames[1:])
    assert ledger.sent_tools(rounds[-1]) == declared
    # The wire never changes what it already sent.
    for previous, current in zip(frames[:-1], frames[1:], strict=True):
        assert current.wire[:len(previous.wire)] == previous.wire
    definitions = [_labelled(frame, DEFINITIONS_LABEL) for frame in frames]
    assert [[entry["n"] for entry in found] for found in definitions] == [
        [[REST]], [], [[ATTACK, CAST, BIG]], [["tool_999999999999_breath"]], []]
    assert all(entry["c"] == TOOL_CHANGE_CLASS for found in definitions for entry in found)
    fight = definitions[2][0]
    assert fight["f"] == [ATTACK, CAST]  # the big one is listed, defined on first use
    text = fight["m"]["content"]
    assert fight["m"]["role"] == "user" and DEFINITIONS_LABEL in text
    assert "## attack\nAttack\narguments: {\n // " not in text
    assert "## attack\n" in text and "## cast\n" in text and "note: string" in text
    # The catalog's state-independent definition, never the round's narrowed enum.
    assert '"aid" | "light"' in text
    assert f"## begin_spell {LISTED_NOTE}\nBegin a spell. Begin" in text
    assert "field_0" not in text
    assert "## breath\n" in definitions[3][0]["m"]["content"]
    # What can be used now, by short name; current choices in their own record.
    callable_text = [entry["m"]["content"] for frame in frames
                     for entry in _labelled(frame, CALLABLE_LABEL)]
    assert "with call_mechanic: rest." in callable_text[0]
    assert any("with call_mechanic: attack, begin_spell, cast. These listed tools cannot be "
               f"called now: {READ}." in text for text in callable_text)
    assert not _labelled(frames[1], CALLABLE_LABEL)
    choices = [entry["m"]["content"] for frame in frames
               for entry in _labelled(frame, CHOICES_LABEL)]
    assert len(choices) == 3 and CHOICES_LABEL in choices[0]
    assert '"cast":{"$.spell":{"allowed_values":["light"]}}' in choices[0]
    assert '"cast":{"$.spell":{"allowed_values":["aid"]}}' in choices[1]
    assert choices[2].endswith(":\n{}")
    # After a restart the same journal rebuilds the same request and defines nothing again.
    restarted = _ledger()
    rebuilt = restarted.plan(_canonical(*dialogue), request_context_id=CONTEXT,
                             tools=rounds[-1], history_records=lambda: _history(records))
    assert rebuilt.wire == frames[-1].wire and rebuilt.record is None
    sorted_records = [json.loads(json.dumps(r, sort_keys=True)) for r in records]
    resent = _ledger().sent_tools(rounds[-1], history_records=lambda: sorted_records)
    assert json.dumps(resent) == json.dumps(declared)


def test_a_listed_mechanic_returns_its_definition_once():
    ledger = _ledger()
    _run(ledger, [[_tool(ATTACK, required=["note"]), BIG_TOOL]])
    assert not ledger.needs_definition(ATTACK)  # defined in full
    assert not ledger.needs_definition(READ)  # declared
    assert ledger.needs_definition(BIG)
    result = ledger.definition_result(BIG, [BIG_TOOL])
    assert result["status"] == "definition_given" and result["state_changed"] is False
    assert result["definition"].startswith("## begin_spell\nBegin a spell.")
    assert "field_59?: boolean" in result["definition"]
    # Once that result is in the journal, the mechanic runs.
    dialogue = [_user("u1", "Look around"), {
        "role": "assistant", "content": None, "_ledger_key": "calls:r1", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": BIG, "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "name": BIG, "_ledger_key": "result:c1",
         "content": json.dumps(result, sort_keys=True, separators=(",", ":"))}]
    frame = ledger.plan(_canonical(*dialogue), request_context_id=CONTEXT, tools=[BIG_TOOL])
    ledger.commit(frame)
    call = [m for m in frame.wire if m.get("tool_calls")][-1]["tool_calls"][0]["function"]
    assert call == {"name": MECHANIC_TOOL_NAME,
                    "arguments": '{"name":"begin_spell","arguments":{}}'}
    assert not ledger.needs_definition(BIG)


def test_inline_limit_keeps_small_mechanics_defined_in_full():
    names = MechanicNames([ATTACK, BIG], reserved=set())
    content, defined = definitions_record([CATALOG[0], BIG_TOOL], names)
    assert defined == [ATTACK]
    assert len(compact_definition(BIG_TOOL, "begin_spell")) > INLINE_LIMIT
    assert re.search(r"^## begin_spell \(arguments given on first use\)$", content, re.M)
