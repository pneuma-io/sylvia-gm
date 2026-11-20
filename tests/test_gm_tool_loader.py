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
    READER_NAME,
    READER_TOOL,
    MechanicNames,
    compact_definition,
    definitions_record,
    project_conversation,
    read_result,
    text_call,
    unread_refusal,
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


@pytest.mark.parametrize(("text", "expected"), [
    ('{"name": "call_mechanic", "arguments": {"name": "cast", "arguments": {}}}',
     ("call_mechanic", '{"name":"cast","arguments":{}}')),
    ('```json\n{"name": "cast", "arguments": {"spell": "aid"}}\n```', ("cast", '{"spell":"aid"}')),
    ('{"name": "cast", "parameters": "{\\"spell\\": \\"aid\\"}"}', ("cast", '{"spell":"aid"}')),
    ('{"name": "rest"}', ("rest", "{}")),
    ('{"name": "narration", "text": "The clearing is quiet."}', None),
    ('{"kind": "narration", "text": "The clearing is quiet."}', None),
    ("The clearing is quiet.", None),
    ('{"name": "cast", "arguments": ["aid"]}', None),
], ids=["generic", "fenced", "string-parameters", "no-arguments", "unknown-name", "no-name",
        "prose", "list-arguments"])
def test_a_reply_that_is_one_call_written_as_text_is_read_as_that_call(text, expected):
    assert text_call(text, {"call_mechanic", "cast", "rest"}) == expected


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
    assert _names(declared) == [READ, READER_NAME, MECHANIC_TOOL_NAME]
    assert declared[0] == _tool(READ)
    assert declared[1] == READER_TOOL and declared[2] == MECHANIC_TOOL
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


def test_a_listed_mechanic_is_read_once_or_refused_with_its_definition():
    ledger = _ledger()
    _run(ledger, [[_tool(ATTACK, required=["note"]), BIG_TOOL]])
    assert not ledger.needs_definition(ATTACK)  # defined in full
    assert not ledger.needs_definition(READ)  # declared
    assert ledger.needs_definition(BIG)
    assert ledger.mechanic_tool(BIG, [BIG_TOOL]) == BIG_TOOL
    refusal = unread_refusal(BIG_TOOL, "begin_spell")
    assert refusal["status"] == "rejected" and refusal["state_changed"] is False
    assert refusal["code"] == "mechanic_arguments_unread"
    assert refusal["definition"].startswith("## begin_spell\nBegin a spell.")
    assert "field_59?: boolean" in refusal["definition"]
    read = read_result(BIG_TOOL, "begin_spell")
    assert read["status"] == "ok" and read["definition"] == refusal["definition"]
    # Once either result is in the journal, the mechanic runs.
    for result in (refusal, read):
        dialogue = [_user("u1", "Look around"), {
            "role": "assistant", "content": None, "_ledger_key": "calls:r1", "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": BIG, "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "name": BIG, "_ledger_key": "result:c1",
             "content": json.dumps(result, sort_keys=True, separators=(",", ":"))}]
        fresh = _ledger()
        _run(fresh, [[BIG_TOOL]])
        assert fresh.needs_definition(BIG)
        frame = fresh.plan(_canonical(*dialogue), request_context_id=CONTEXT, tools=[BIG_TOOL])
        fresh.commit(frame)
        call = [m for m in frame.wire if m.get("tool_calls")][-1]["tool_calls"][0]["function"]
        assert call == {"name": MECHANIC_TOOL_NAME,
                        "arguments": '{"name":"begin_spell","arguments":{}}'}
        assert not fresh.needs_definition(BIG)


def test_inline_limit_keeps_small_mechanics_defined_in_full():
    names = MechanicNames([ATTACK, BIG], reserved=set())
    content, defined = definitions_record([CATALOG[0], BIG_TOOL], names)
    assert defined == [ATTACK]
    assert len(compact_definition(BIG_TOOL, "begin_spell")) > INLINE_LIMIT
    assert re.search(r"^## begin_spell \(read its arguments first\)$", content, re.M)


# ── The scripted D&D session through the loader ───────────────────────────────────────────


def _session_gm():
    """The shared scripted GM on Chat Completions, plus ``("raw", (name, arguments))`` steps
    that send one call exactly as written (an invalid envelope, a mechanic not offered)."""
    import copy

    from sylvia_gm.provider._base import ProviderStreamChunk
    from sylvia_gm.provider._openai_compat import ChatCompletionsTransport
    from tests.test_gm_deferred_tool_list import _route_gm

    gm = _route_gm("chat")
    scripted = type(gm).generate_stream

    async def generate_stream(self, messages, tools=None, **kwargs):
        last = next((m for m in reversed(messages or [])
                     if m.get("role") in {"tool", "assistant"}), {})
        retry = last.get("role") == "tool" and "mechanic_arguments_unread" in str(
            last.get("content"))  # the scripted GM repeats the refused call
        if (not retry and self.position < len(self.steps)
                and self.steps[self.position][0] == "raw"):
            name, arguments = self.steps[self.position][1]
            self.position += 1
            self.calls.append({"messages": messages, "tools": tools})
            self.requests.append(ChatCompletionsTransport().format_request(
                "qwen3-32b", copy.deepcopy(messages), copy.deepcopy(tools), True))
            yield ProviderStreamChunk(tool_call_delta={
                "index": 0, "id": f"raw-{self.position}",
                "function": {"name": name, "arguments": arguments}}, finish_reason="tool_calls")
            return
        async for chunk in scripted(self, messages, tools, **kwargs):
            yield chunk

    gm.generate_stream = generate_stream.__get__(gm)
    return gm


def _operation_of(name: str) -> str:
    return name.split("_", 2)[-1] if re.match(r"tool_[0-9a-f]{12}_", name) else name


def test_loader_session_reads_refuses_and_runs_mechanics(loader_seed, tmp_path, monkeypatch):
    import shutil

    from sylvia_gm.agent._provider_session import ProviderSessionAdapter, _plain_json
    from sylvia_gm.agent._session_loop import CampaignSessionLoop, SessionRunRequest
    from sylvia_gm.application.authority import AuthorityOwner
    from tests.test_gm_context_contract import settle
    from tests.test_srd_public_spell_casting import SRD

    monkeypatch.setenv("SYLVIA_GM_CONTEXT_MODE", "ledger")
    monkeypatch.setenv("SYLVIA_GM_TOOL_LIST", "deferred")
    database = tmp_path / "session.db"
    shutil.copy2(loader_seed[0], database)
    gm = _session_gm()
    owner = AuthorityOwner.open(database, "fixed-owner", disconnect_grace_seconds=1.0)
    outcomes: list[tuple[str, dict]] = []
    refusals: list[str] = []
    record = AuthorityOwner.record_admin_full_audit

    def audit(self, event):
        if event.get("kind") == "gm_fixed_tool_refusal":
            refusals.append(event["reason"])
        return record(self, event)
    monkeypatch.setattr(AuthorityOwner, "record_admin_full_audit", audit)

    def turn(text, steps):
        gm.steps.extend(steps)
        identity = f"turn-{len(gm.calls)}"
        result = CampaignSessionLoop(owner).run(SessionRunRequest(
            session_id="fixed-session", turn_id="casting-turn", attempt_id=identity,
            message_id=identity, user_text=text,
        ), ProviderSessionAdapter(owner, gm, owner_id="fixed-owner").for_turn("casting-turn"))
        assert result.interruption is None, (result.interruption, gm.failure)
        assert gm.position == len(gm.steps), (gm.position, gm.steps)
        for message in owner.session_context("fixed-session").messages:
            evidence = message.content
            if (evidence.get("kind") == "provider_tool_results"
                    and evidence.get("basis_message_id") == identity):
                outcomes.extend((row["name"], json.loads(row["content"]))
                                for row in evidence["results"])

    try:
        turn("We look around the clearing.", [
            # An envelope without a name, then a mechanic exploration does not offer.
            ("raw", (MECHANIC_TOOL_NAME, '{"arguments": {}}')),
            ("raw", (MECHANIC_TOOL_NAME, json.dumps({"name": "end_combat_encounter",
                                                     "arguments": {"end_reason": "x"}}))),
            settle("The clearing is quiet.")])
        scene = _plain_json(owner.snapshot("casting").state[f"{SRD}.state"]["spatial_scene"])
        scene["zones"] = [{"id": "daylight", "map_id": "fixture",
                           "shape": {"kind": "box", "min": [-100, -100, -10],
                                     "max": [100, 100, 20]},
                           "metadata": {"lighting": "bright", "plane": "material"}}]
        summon = dict(loader_seed[1])
        summon.pop("target")
        summon.update(summon_id="dragon_one", summon_distance=30,
                      caster_can_see_summon_space=True, summon_space_unoccupied=True,
                      selected_damage_type="fire", created_actor_position=[0, 30, 0],
                      created_actor_height=6, created_actor_lighting="bright")

        def completion():
            pending = owner.snapshot("casting").state[f"{SRD}.state"]["pending_srd_action_stack"]
            return {"pending_action_id": pending[-1]}
        turn("Begin a practice fight, summon a dragon, then stop the fight.", [
            # Called before its arguments were read: refused with them, then called again.
            ("start_group_combat_encounter",
             {"combatants": ["hero", "guard", "other"], "combat_scenes": [scene]}),
            # Read first, with the spell: the definition and the spell's own inputs at once.
            ("raw", ("read_tool_arguments", json.dumps(
                {"tool": "begin_srd_spell_action", "value": "summon_dragon"}))),
            ("begin_srd_spell_action", summon),
            ("complete_srd_pending_action", completion),
            ("end_combat_encounter", {"end_reason": "fighting_stopped"}),
            settle("The summoned dragon remains as the practice fight ends."),
        ])
        owner.close()
        owner = AuthorityOwner.open(database, "fixed-owner", disconnect_grace_seconds=1.0)
        turn("Look around again.", [settle("The clearing is quiet again.")])
    finally:
        owner.close()
    assert gm.failure is None
    by_code: dict = {}
    for _name, outcome in outcomes:
        by_code.setdefault(outcome.get("code"), outcome)  # the first of each
    invalid = by_code["mechanic_call_invalid"]
    assert invalid["requested_provider_tool_name"] == MECHANIC_TOOL_NAME
    assert "start_group_combat_encounter" in invalid["mechanics_usable_now"]
    unavailable = by_code["unavailable_catalog_tool"]
    assert unavailable["requested_provider_tool_name"] == "end_combat_encounter"
    assert all(not name.startswith("tool_") for name in unavailable["mechanics_usable_now"])
    unread = by_code["mechanic_arguments_unread"]
    assert unread["mechanic_definition"] == "start_group_combat_encounter"
    assert unread["definition"].startswith("## start_group_combat_encounter\n")
    read = next(outcome for name, outcome in outcomes if name == "read_tool_arguments")
    assert read["status"] == "ok" and read["mechanic_definition"] == "begin_srd_spell_action"
    assert "summon_distance?: " in read["definition"]
    statuses = [(_operation_of(name), outcome.get("status")) for name, outcome in outcomes]
    assert ("start_group_combat_encounter", "action_committed") in statuses
    # The cast runs on its first call: its arguments were read.
    assert [s for op, s in statuses if op == "begin_srd_spell_action"] == ["action_committed"]
    assert refusals.count("mechanic_call_invalid") == 1
    assert "not_callable" in refusals and "arguments_unread" in refusals
    # Evidence names the mechanic itself; only the unreadable envelope keeps the generic name.
    assert [name for name, _o in outcomes if name == MECHANIC_TOOL_NAME] == [MECHANIC_TOOL_NAME]
    requests = gm.requests
    assert len({json.dumps(request["tools"]) for request in requests}) == 1
    assert len(requests[0]["tools"]) < 16
    for previous, current in zip(requests[:-1], requests[1:], strict=True):
        assert current["messages"][:len(previous["messages"])] == previous["messages"]
    # While a listed mechanic is unread, the reader can be called.
    callable_text = [message["content"] for request in requests
                     for message in request["messages"]
                     if isinstance(message.get("content"), str)
                     and CALLABLE_LABEL in message["content"][:200]]
    assert any("begin_srd_spell_action" in text
               and "cannot be called now: read_tool_arguments" not in text
               for text in callable_text)
    # The model's calls go back to it as the call_mechanic calls it made.
    declared = {tool["function"]["name"] for tool in requests[0]["tools"]}
    calls = [call["function"] for message in requests[-1]["messages"]
             for call in message.get("tool_calls") or ()]
    assert {call["name"] for call in calls} <= declared
    assert any(call["arguments"].startswith('{"name":"begin_srd_spell_action","arguments":')
               for call in calls)


@pytest.fixture(scope="module")
def loader_seed(tmp_path_factory):
    """The SRD spell-casting campaign with a summon, saved once for the module."""
    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.domain.authority import WriterRole
    from tests.test_srd_public_spell_casting import _create_spell_campaign

    database = tmp_path_factory.mktemp("loader-seed") / "seed.db"
    with AuthorityOwner.open(database, "loader-seed") as owner:
        _state, _data, parameters = _create_spell_campaign(owner, "summon_dragon")
        owner.create_agent_session(campaign_id="casting", session_id="fixed-session",
                                   role=WriterRole.DM, provider_id="openai_compatible",
                                   model_id="openai_compatible/qwen3-32b")
    return database, parameters
