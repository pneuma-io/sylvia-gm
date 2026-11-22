"""Generate tool-execution vectors for sylvia_engine::exec from the frozen Python.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-engine/tests/vectors/generate_execution_vectors.py

(`.venv/bin/python` on Linux.) Deterministic and checked in. Reference tooling for the port,
retired with the Python source.

Each case is a Ruleset document (its tools, features and definitions), an authority state, the
participants, a tool name and its parameters. Python loads the tools with `RulesetRuntime(None,
document)`, builds `AuthorityMemoryStateBridge(namespace, state, participants)` and runs
`execute_tool(...)` with the bridge's combat state and a nested executor on the same bridge (as
`materialize_package_mechanic` does, without its state-bound parameter step). It records the
whole `ToolResult`, the bridge's state and participant mutations, or the exception.

A case whose recorded result or mutations hold an integer outside the signed 64-bit range is
marked `wide_int`: Rust refuses such an integer (`differences.md` row 5).

The corpus covers the handlers this pull request ports (set, increment, decrement, require,
condition, log, narrate, publish, create_entity, set_participant, call): targeted cases for each
parameter check, pre-condition and handler path, then a seeded random corpus of tools built from
those handlers with expressions, templates, fields, parameters and states drawn from the same
pools.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import math
import random
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import sylvia_gm.runtime._effect_executor as effect_executor
from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge
from sylvia_gm.dice import seeded_rolls
import uuid

from sylvia_gm.runtime._exceptions import MechanicsChoiceSuspended
from sylvia_gm.runtime._procedure_executor import trigger_procedure
from sylvia_gm.runtime._runtime import RulesetRuntime
from sylvia_gm.runtime._tool_executor import execute_tool
from sylvia_gm.runtime._types import ChoiceResolutionControl, RuntimeChoiceOption, RuntimeChoiceRequest

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "execution_vectors.json"
HANDLERS_OUTPUT = HERE / "execution_handlers_vectors.json"
PROCEDURES_OUTPUT = HERE / "execution_procedures_vectors.json"


class _Counter:
    """`uuid.uuid4()` for the vectors: `defer` names each call with a random id, which the Rust
    test compares by shape (eight hex digits) instead of value."""

    value = 0

    def __call__(self):
        _Counter.value += 1
        return uuid.UUID(int=_Counter.value)


uuid.uuid4 = _Counter()


class _InOrder:
    """`secrets.SystemRandom()` for the vectors: OS randomness cannot be compared, so the
    generator keeps the original order and the Rust test compares shuffled lists as multisets."""

    def shuffle(self, values):
        return None


effect_executor.secrets.SystemRandom = _InOrder
NAMESPACE = "pkg.test"

STATE = {
    f"{NAMESPACE}.state": {"counter": 1, "nested": {"deep": 2}, "flag": True, "items": ["a", "b"]},
    f"{NAMESPACE}.entities": {
        "orc": {"hp": 5, "max_hp": 9, "name": "Orc", "conditions": ["prone"], "position": {"band": "close"},
                "abilities": {"str": {"score": 12}}, "id": "orc"},
        "elf": {"hp": 7, "max_hp": 7, "name": "Elf", "conditions": [], "speed": 2.5},
        "box": {"name": "Box"},
    },
    f"{NAMESPACE}.combat": {"round": 1, "turn_index": 0},
}
PARTICIPANTS = {
    "pc": {"package_id": NAMESPACE, "data": {"hp": 3, "name": "Pc", "level": 2}},
    "guest": {"package_id": "other.pkg", "data": {"hp": 4}},
    "orc": {"package_id": NAMESPACE, "data": {"hp": 1}},
}


def plain(value):
    """A JSON form of an execution value: dataclasses carry their class name."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {"__class__": type(value).__name__,
                **{field.name: plain(getattr(value, field.name)) for field in dataclasses.fields(value)}}
    if isinstance(value, Mapping):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [plain(item) for item in value]
    if isinstance(value, set):
        return {"__set__": sorted(plain(item) for item in value)}
    if isinstance(value, float) and not math.isfinite(value):
        return {"__float__": repr(value)}
    return value


def choice_request(value: dict) -> RuntimeChoiceRequest:
    return RuntimeChoiceRequest(value["choice_id"], value["prompt"], tuple(
        RuntimeChoiceOption(option["option_id"], option["label"]) for option in value["options"]))


def run(document: dict, tool: str, params: dict, state=STATE, participants=PARTICIPANTS, seed=None,
        choice=None, procedures=False) -> dict:
    try:
        runtime = RulesetRuntime(None, copy.deepcopy(document))
    except Exception as error:  # noqa: BLE001
        return {"load_error": [type(error).__name__, str(error)]}
    tools = runtime.tools
    bridge = AuthorityMemoryStateBridge(NAMESPACE, copy.deepcopy(state), copy.deepcopy(participants))
    control = None
    if choice is not None:
        control = ChoiceResolutionControl(
            choice_id=choice["choice_id"], option_id=choice["option_id"],
            expected_request=choice_request(choice["expected_request"]),
            prior_answers=tuple((choice_request(request), option) for request, option in choice["prior_answers"]))
    extras = {} if control is None else {"mechanics_choice_resolution": control}

    def procedure(trigger_name, trigger_context, parent):
        # As `materialize_package_mechanic`'s `procedure`.
        return trigger_procedure(trigger_name, runtime.procedures, tools, bridge, runtime.ruleset_config,
                                 runtime.definitions, context=trigger_context, combat_state=bridge.combat_state,
                                 budget=parent.budget)

    procedure_executor = procedure if procedures else None

    def nested(name, nested_params, parent):
        return execute_tool(name, nested_params, tools, bridge, runtime.ruleset_config, runtime.definitions,
                            bridge.combat_state, tool_executor=nested, extras=extras, budget=parent.budget,
                            procedure_executor=procedure_executor)

    try:
        with seeded_rolls(seed):
            result = execute_tool(tool, copy.deepcopy(params), tools, bridge, runtime.ruleset_config,
                                  runtime.definitions, bridge.combat_state, tool_executor=nested, extras=extras,
                                  procedure_executor=procedure_executor)
    except MechanicsChoiceSuspended as suspended:
        return {"raise": [type(suspended).__name__, str(suspended)], "request": plain(suspended.request),
                **({} if control is None else {"control": [control.consumed, control.replayed_answers]})}
    except Exception as error:  # noqa: BLE001 - every exception class is behaviour here
        return {"raise": [type(error).__name__, str(error)]}
    record = {"result": plain(result)}
    if control is not None:
        record["control"] = [control.consumed, control.replayed_answers]
    try:
        record["mutations"] = [[m.namespace, m.key, plain(m.value)] for m in bridge.mutations()]
        record["participant_mutations"] = [[m.participant_id, plain(m.data)] for m in bridge.participant_mutations()]
    except Exception as error:  # noqa: BLE001
        record["mutations_raise"] = [type(error).__name__, str(error)]
    return record


ENTITY = {"name": "target", "type": "string", "resolve": "entity"}
ACTOR = {"name": "actor", "type": "string", "resolve": "participant", "required": False}
AMOUNT = {"name": "amount", "type": "integer", "default": 2}


def tools(effects, *, parameters=(ENTITY, ACTOR, AMOUNT), pre=None, extra=None, rng_range=None) -> dict:
    main = {"parameters": [dict(p) for p in parameters], "effects": effects}
    if pre is not None:
        main["pre_conditions"] = pre
    if rng_range is not None:
        main["range"] = rng_range
    document = {"tools": {"main": main, "helper": {
        "parameters": [{"name": "target", "type": "string", "resolve": "entity"}, {"name": "n", "type": "integer",
                                                                                     "required": False}],
        "effects": [{"set": {"field": "_helped", "value": "n"}}, {"set": {"field": "{target}.hp", "value": "target.hp + 1"}},
                    {"log": {"message": "helped {target.name}"}}]},
        "failing": {"parameters": [], "effects": [{"require": {"condition": "false", "error": "no way"}}]},
        "broken": {"parameters": [], "effects": [{"bogus": 1}]},
        "picky": {"parameters": [{"name": "n", "type": "integer"}], "effects": []}},
        "features": {"crit": True}, "definitions": {"weapons": {"axe": {"damage": "1d8"}}, "table": {"a": 1}}}
    if extra:
        document["tools"].update(extra)
    return document


BASE_PARAMS = {"target": "orc", "actor": "pc"}


def targeted() -> list[dict]:
    cases = []

    def case(effects, params=None, **options):
        cases.append({"document": tools(effects, **{k: v for k, v in options.items() if k in
                                                    ("parameters", "pre", "extra", "rng_range")}),
                      "tool": options.get("tool", "main"), "params": BASE_PARAMS if params is None else params})

    # Parameter validation, one check at a time.
    kinds = [
        ({"name": "p", "type": "string"}, ["x", 1, None, "", True]),
        ({"name": "p", "type": "integer"}, [1, 1.0, 1.5, True, "1", None]),
        ({"name": "p", "type": "number"}, [1, 2.5, True, "x"]),
        ({"name": "p", "type": "boolean"}, [True, 0, "true"]),
        ({"name": "p", "type": "object"}, [{}, [], "x"]),
        ({"name": "p", "type": "enum", "enum_values": ["a", "b"]}, ["a", "c", 1, True]),
        ({"name": "p", "type": "enum", "enum_values": ["a", 1]}, ["z", 1, True, 1.0, False]),
        ({"name": "p", "type": "enum"}, ["a"]),
        ({"name": "p", "type": "entity_id_list", "min_items": 1, "max_items": 2, "unique_items": True,
          "resolve": "entity_list"}, [[], ["orc"], ["orc", "orc"], ["orc", "elf", "box"], ["ghost"], ["orc", 3], "orc"]),
        ({"name": "p", "type": "array", "min_items": 1, "max_items": 2, "unique_items": True},
         [[], [1], [1, 1], [1, "1"], [1, 2, 3], "x"]),
        ({"name": "p", "type": "integer", "minimum": 0, "maximum": 5}, [-1, 0, 5, 6, 2.0]),
        ({"name": "p", "type": "number", "minimum": 0.5}, [0, 1]),
        ({"name": "p", "type": "integer", "minimum": "x"}, [1]),
        ({"name": "p", "type": "string", "resolve": "entity"}, ["orc", "ghost", "state", 5]),
        ({"name": "p", "type": "string", "resolve": "participant"}, ["pc", "ghost", 5]),
        ({"name": "p", "type": "string", "required": False, "default": "dflt"}, [None]),
        ({"name": "p", "type": "array", "required": False}, [None]),
    ]
    for parameter, values in kinds:
        for value in values:
            params = {} if value is None else {"p": value}
            case([{"set": {"field": "_p", "value": "p"}}], params, parameters=[parameter])
    case([], {}, tool="nope")
    case([], {"target": "ghost"})
    case([], {"target": "orc", "actor": "ghost"})
    # Pre-conditions.
    for pre in (["target.hp > 0"], ["target.hp > 100"], [{"condition": "target.hp > 100", "error": "{target.name} too weak"}],
                [{"condition": "1 +", "error": "x"}], ["1 +", "target.hp > 100"], ["target.hp > 100", "1 +"],
                ["false", "false"], [{"condition": "false"}], [{"error": "no condition"}], [True], [3],
                ["amount == 2", "actor.data.hp == 3"]):
        case([{"set": {"field": "_ok", "value": "true"}}], pre=pre)
    case([], pre=[{"condition": f"amount > {i}", "error": f"e{i}"} for i in range(70)])
    case([], rng_range={"type": "band", "value": "close"})
    case([], {"target": "elf"}, rng_range={"type": "grid", "value": 30})
    case([], {"target": "orc", "weapon": "axe", "offhand_weapon": "axe"},
         parameters=[ENTITY, {"name": "weapon", "type": "string"}, {"name": "offhand_weapon", "type": "string"}])
    case([], {"target": "orc", "weapon": "club"}, parameters=[ENTITY, {"name": "weapon", "type": "string"}])
    # set: context, entity, combat, typed literals, null, templates.
    for field, value in (("_x", "1 + 2"), ("_x", 3), ("_x", 2.5), ("_x", True), ("_x", None), ("_x", "null"),
                         ("_x", "true"), ("_x", "'text'"), ("_x", "unknown_name"), ("_x", "target.missing"),
                         ("_x", "state.missing"), ("_x", "state"), ("_x", "1 +"), ("_x", {"a": "amount", "b": [1, "2"]}),
                         ("_x", ["amount", "x y"]), ("target.hp", "target.hp - amount"), ("{target}.conditions", "[]"),
                         ("state.counter", "state.counter + 10"), ("state.nested.deep.deeper", 1),
                         ("state.new.path", "amount"), ("orc.name", "'Big Orc'"), ("elf.hp", "1"), ("round", 7),
                         ("combat.round", 9), ("ghost.hp", 1), ("", 1), ("_n", "1 / 0"), ("target", 1),
                         ("target.speed", "elf.speed"), ("state", 1)):
        case([{"set": {"field": field, "value": value}}])
    case([{"set": {"value": 1}}])
    case([{"set": "x"}])
    # increment / decrement.
    for effect in ({"increment": {"field": "target.hp"}}, {"decrement": {"field": "target.hp", "amount": "amount"}},
                   {"increment": {"field": "state.counter", "amount": 2.7}}, {"increment": {"field": "elf.speed", "amount": 1}},
                   {"decrement": {"field": "target.name"}}, {"increment": {"field": "state.fresh", "amount": "3"}},
                   {"increment": {"field": "ghost.hp"}}, {"increment": {"field": "", "amount": "1 +"}},
                   {"increment": {"field": "target.hp", "amount": "'x'"}}, {"increment": {"field": "target.hp", "amount": "missing"}},
                   {"increment": {"field": "target.hp", "amount": "[1]"}}, {"increment": "x"},
                   {"increment": {"field": "target.hp", "amount": "9223372036854775807"}}):
        case([effect])
    # require, condition (nested and flat), gates.
    for effect in ({"require": {"condition": "target.hp > 0"}}, {"require": {"condition": "target.hp > 9", "error": "weak"}},
                   {"require": {"condition": 1}}, {"require": "x"}, {"require": {"condition": "1 +"}},
                   {"condition": {"condition": "target.hp > 3", "on_true": [{"set": {"field": "_a", "value": 1}}],
                                  "on_false": [{"set": {"field": "_a", "value": 2}}]}},
                   {"condition": "target.hp > 3", "on_true": [{"set": {"field": "_b", "value": 1}}]},
                   {"condition": "target.hp > 30", "on_false": [{"log": {"message": "low"}}]},
                   {"condition": {"condition": "1 +"}}, {"condition": "x"},
                   {"set": {"field": "_g", "value": 1}, "condition": "false"},
                   {"set": {"field": "_g", "value": 1}, "condition": "1 +"},
                   {"set": {"field": "_g", "value": 1}, "condition": True},
                   {"set": {"field": "_g", "value": 1}, "log": {"message": "two"}}, {"bogus": 1}, {},
                   {"condition": "true", "set": {"field": "_c", "value": 1}, "on_true": []}):
        case([effect])
    case(["not a mapping"])
    case([None])
    case({"set": {"field": "_x", "value": 1}})
    # log, narrate, publish.
    for effect in ({"log": {"message": "{target.name} has {target.hp} hp, {missing.x} {state.items}"}},
                   {"log": {"message": "lvl", "level": 3}}, {"log": "ignored"}, {"log": {"message": "{target.conditions}"}},
                   {"narrate": "The {target.name} snarls"}, {"narrate": {"text": "t", "style": 1, "priority": None}},
                   {"narrate": {"narrate": "alt"}}, {"narrate": 5},
                   {"publish": {"fields": {"hp": "target.hp", "lit": 3}}}, {"publish": {"fields": []}},
                   {"publish": {"fields": {f"k{i}": 1 for i in range(33)}}}, {"publish": {"fields": {}, "x": 1}}):
        case([effect, {"narrate": "after", "style": "system", "priority": "high"}])
    case([{"set": {"field": "_logs", "value": "'not a list'"}}, {"log": {"message": "x"}}])
    case([{"log": {"message": "{target.conditions} / {state.items}"}}], {"target": "elf", "actor": "pc"})
    case([{"set": {"field": "_narrative_hit", "value": "'boom'"}}, {"set": {"field": "_narrative_miss", "value": 0}}])
    # create_entity, set_participant.
    for effect in ({"create_entity": {"id": "'goblin'", "value": {"hp": 3}}}, {"create_entity": {"id": "'orc'", "value": {}}},
                   {"create_entity": {"id": "'pc'", "value": {}}}, {"create_entity": {"id": "'state'", "value": {}}},
                   {"create_entity": {"id": "'bad id!'", "value": {}}}, {"create_entity": {"id": 5, "value": {}}},
                   {"create_entity": {"id": "'g2'", "value": "x"}}, {"create_entity": "x"},
                   {"set_participant": {"participant": "actor", "field": "hp", "value": "actor.data.hp + 1"}},
                   {"set_participant": {"participant": "'guest'", "field": "hp", "value": 1}},
                   {"set_participant": {"participant": "'pc'", "field": "", "value": 1}},
                   {"set_participant": {"participant": 3, "field": "x"}},
                   {"set_participant": {"participant": "'orc'", "field": "mood", "value": "'grim'"}}):
        case([effect, {"set": {"field": "_after", "value": "actor"}}])
    # Nested calls.
    for effect in ({"call": "helper", "params": {"target": "{target}", "n": 4}},
                   {"call": {"call": "helper", "params": {"target": "{target}"}}, "store_as": "_r"},
                   {"call": {"name": "helper", "params": {"target": "orc"}}, "export_context": "*"},
                   {"call": "helper", "params": {"target": "{target}"}, "export_context": {"_h": "_helped"}},
                   {"call": "helper", "params": {"target": "{target}"}, "export_context": {"_h": "_missing"}},
                   {"call": "helper", "params": {"target": "{target}"}, "export_context": {"h": "_helped"}},
                   {"call": "helper", "params": {"target": "{target}"}, "export_context": 3},
                   {"call": "failing"}, {"call": "broken"}, {"call": "picky", "params": {"n": "x"}}, {"call": "absent"},
                   {"call": ""}, {"call": 5}, {"call": {"call": 5}}, {"call": "helper", "params": []},
                   {"call": "helper", "params": {"target": "{nothing}"}},
                   {"call": "helper", "params": {"target": "{target}", "n": "{amount} and more"}}):
        case([effect, {"set": {"field": "_seen", "value": "target.hp"}}])
    case([{"call": "main"}], tool="main", params={"target": "orc"})
    return cases


EXPRESSIONS = ["1", "2 + 3", "amount", "amount * 2", "target.hp", "target.hp - amount", "_x", "_x + 1", "state.counter",
               "combat.round", "'word'", "true", "false", "null", "missing", "target.missing", "state.items", "1 +",
               "actor.data.hp", "target.name", "len(state.items)", "max(1, amount)", "_y", "target.conditions",
               "definitions.table.a", "ruleset.crit", "1 / 0", "amount > 1", "state.nested"]
LITERALS = [0, 3, -2, 2.5, True, False, None, {"k": "amount"}, ["amount", 1]]
FIELDS = ["_x", "_y", "_z", "_logs", "_narrative_note", "target.hp", "target.conditions", "{target}.hp",
          "state.counter", "state.nested.deep", "elf.hp", "box.weight", "orc.name", "turn", "ghost.hp", "",
          "{actor}.hp", "state.deeper.path.x"]
TEMPLATES = ["{target.name} acts", "{_x}", "{amount} {missing}", "{state.items}", "plain", "{target.hp}/{target.max_hp}",
             "{actor.data.name}", "{_logs}"]


def random_value(rng):
    return rng.choice(EXPRESSIONS) if rng.random() < 0.75 else copy.deepcopy(rng.choice(LITERALS))


def random_effect(rng, depth=0):
    kind = rng.choice(["set", "set", "set", "increment", "decrement", "require", "condition", "flat", "log",
                       "narrate", "publish", "create", "participant", "call", "gate"])
    if kind == "set":
        return {"set": {"field": rng.choice(FIELDS), "value": random_value(rng)}}
    if kind in ("increment", "decrement"):
        body = {"field": rng.choice(FIELDS)}
        if rng.random() < 0.7:
            body["amount"] = random_value(rng)
        return {kind: body}
    if kind == "require":
        return {"require": {"condition": rng.choice(EXPRESSIONS), "error": rng.choice(TEMPLATES)}}
    if kind in ("condition", "flat") and depth < 2:
        on_true = [random_effect(rng, depth + 1) for _ in range(rng.randint(0, 2))]
        on_false = [random_effect(rng, depth + 1) for _ in range(rng.randint(0, 2))]
        if kind == "flat":
            return {"condition": rng.choice(EXPRESSIONS), "on_true": on_true, "on_false": on_false}
        return {"condition": {"condition": rng.choice(EXPRESSIONS), "on_true": on_true, "on_false": on_false}}
    if kind == "log":
        return {"log": {"message": rng.choice(TEMPLATES), "level": rng.choice(["combat", "debug", 2])}}
    if kind == "narrate":
        return {"narrate": rng.choice(TEMPLATES)} if rng.random() < 0.5 else {"narrate": {"text": rng.choice(TEMPLATES)}}
    if kind == "publish":
        return {"publish": {"fields": {"a": random_value(rng), "b": random_value(rng)}}}
    if kind == "create":
        return {"create_entity": {"id": rng.choice(["'gob'", "'orc'", "_x", "'g-1'"]), "value": {"hp": random_value(rng)}}}
    if kind == "participant":
        return {"set_participant": {"participant": rng.choice(["actor", "'pc'", "'guest'"]), "field": rng.choice(["hp", "x.y", ""]),
                                    "value": random_value(rng)}}
    if kind == "call":
        effect = {"call": rng.choice(["helper", "failing", "picky"]), "params": {"target": rng.choice(["{target}", "elf", "ghost"])}}
        if rng.random() < 0.4:
            effect["store_as"] = "_r"
        if rng.random() < 0.3:
            effect["export_context"] = "*"
        return effect
    return {"set": {"field": rng.choice(FIELDS), "value": random_value(rng)}, "condition": rng.choice(EXPRESSIONS)}


def random_cases(rng, count):
    cases = []
    for _ in range(count):
        effects = [random_effect(rng) for _ in range(rng.randint(1, 6))]
        pre = [rng.choice(EXPRESSIONS) for _ in range(rng.randint(0, 2))] or None
        params = {"target": rng.choice(["orc", "orc", "elf", "box"]), "actor": rng.choice(["pc", "pc", "guest", None])}
        if params["actor"] is None:
            del params["actor"]
        if rng.random() < 0.5:
            params["amount"] = rng.choice([1, 3, 0, -1, 2.0])
        cases.append({"document": tools(effects, pre=pre), "tool": "main", "params": params})
    return cases


def wide(value) -> bool:
    """Whether a recorded value holds an integer outside i64 (`differences.md` row 5)."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return not -(2**63) <= value < 2**63
    if isinstance(value, dict):
        return any(wide(item) for item in value.values())
    if isinstance(value, list):
        return any(wide(item) for item in value)
    return False


ROLL_STATE = copy.deepcopy(STATE)
ROLL_STATE[f"{NAMESPACE}.entities"]["orc"]["abilities"] = {"dex": {"modifier": 3}, "str": {"score": 12, "modifier": -1},
                                                           "wis": "odd"}
ROLL_STATE[f"{NAMESPACE}.state"]["rolls"] = [{"name": "b", "init": 3}, {"name": "a", "init": 9}, {"name": "c", "init": 3}]
DEFINITIONS = {"weapons": {"axe": {"damage": "1d8"}}, "table": {"1": "one", "2": "two", "x": None},
               "encounters": {"wolf": {"weight": 3, "hp": 5}, "bear": {"weight": 1, "_key": "custom-bear"}, "ghost": "boo"},
               "flat": {"a": 1, "b": 2, "c": 3}, "empty": {}, "nested": {"inner": {"k": "v"}}}

CHOICE_OPTIONS = [{"id": "attack", "label": "Attack {target.name}"}, {"id": "flee", "label": "Flee"}]
REQUEST = {"choice_id": "c1", "prompt": "Choose for Orc",
           "options": [{"option_id": "attack", "label": "Attack Orc"}, {"option_id": "flee", "label": "Flee"}]}


def handler_tools(effects, **options) -> dict:
    document = tools(effects, **options)
    document["definitions"] = copy.deepcopy(DEFINITIONS)
    return document


def handler_targeted() -> list[dict]:
    cases = []

    def case(effects, params=None, choice=None, **options):
        item = {"document": handler_tools(effects, **options), "tool": "main",
                "params": BASE_PARAMS if params is None else params}
        if choice is not None:
            item["choice"] = choice
        cases.append(item)

    for body in ({"expression": "1d20"}, {"expression": "2d6+3"}, {"expression": "{amount}d4"}, {"expression": "d8", "modifier": "amount"},
                 {"expression": "d20", "advantage": "true"}, {"expression": "d20", "disadvantage": "true"},
                 {"expression": "d20", "advantage": "true", "disadvantage": "true"}, {"expression": "1d6", "critical": "true"},
                 {"expression": "3d6", "explode": True, "explode_on": 6, "max_explosions": 2}, {"expression": "2d6", "explode": "amount > 1"},
                 {"expression": "bogus"}, {"expression": "0d6"}, {"expression": "1d1"}, {"modifier": "'x'"}, {"modifier": "missing"},
                 {"expression": "1d6", "explode": True, "max_explosions": "'x'"}, "x"):
        case([{"dice": body, "store_as": "_roll"}, {"set": {"field": "_t", "value": "_roll.total"}}])
    case([{"dice": {"expression": "1d20"}}])
    for body in ({"target": "target", "ability": "dex", "dc": 12}, {"target": "target", "ability": "str", "dc": "amount + 10"},
                 {"target": "target", "ability": "wis"}, {"target": "target", "ability": "cha"}, {"target": "elf"}, {"target": 3},
                 {"target": "target", "dc": "'x'"}):
        case([{"save_throw": body, "store_as": "_save"}, {"set": {"field": "_ok", "value": "_save.success"}}])
    for effect in ({"apply_damage": {"target": "target", "amount": "amount + 1"}}, {"apply_damage": {"target": "elf", "amount": 100}},
                   {"apply_damage": {"target": "box", "amount": 1}}, {"apply_damage": {"target": "ghost", "amount": 1}},
                   {"apply_damage": {"target": "target", "amount": -3}}, {"heal": {"target": "target", "amount": 99}},
                   {"heal": {"target": "elf", "amount": 1}}, {"heal": {"target": "box"}},
                   {"apply_condition": {"target": "target", "condition": "stunned"}}, {"apply_condition": {"target": "target", "condition": "prone"}},
                   {"remove_condition": {"target": "target", "condition": "prone"}}, {"remove_condition": {"target": "elf", "condition": "x"}},
                   {"apply_condition": {"target": "target", "condition": "{target.name}-marked"}},
                   {"apply_ability_damage": {"target": "target", "ability": "str", "amount": 5}},
                   {"apply_ability_damage": {"target": "elf", "ability": "dex", "amount": 1}}, {"apply_damage": "x"},
                   {"apply_damage": {"target": ["x"], "amount": 1}}):
        case([effect, {"set": {"field": "_hp", "value": "target.hp"}}])
    for body in ({"pool": "5d6", "threshold": 5}, {"pool": "{amount}d10", "threshold": "amount + 5", "modifier": 1}, {"pool": "x"}, {}):
        case([{"count_successes": body, "store_as": "_pool"}])
    for effects in ([{"dice": {"expression": "4d6"}, "store_as": "_r"}, {"reroll": {"source": "_r", "predicate": "ones"}, "store_as": "_rr"}],
                    [{"dice": {"expression": "4d6"}, "store_as": "_r"}, {"reroll": {"source": "_r", "predicate": "failures", "threshold": 4,
                                                                                    "consequence": [{"log": {"message": "pushed"}}]}}],
                    [{"set": {"field": "_r", "value": {"sides": 6, "count": 2, "results": [1, 5], "total": 6, "modifier": 0}}},
                     {"reroll": {"source": "_r", "predicate": "all", "modifier": "amount"}}],
                    [{"reroll": {"source": "_none"}}], [{"set": {"field": "_r", "value": 3}}, {"reroll": {"source": "_r"}}],
                    [{"dice": {"expression": "2d6"}, "store_as": "_r"}, {"reroll": {"source": "_r", "predicate": "bogus"}}]):
        case(effects)
    for body in ({"pool": 6, "hunger": 2}, {"pool": "amount + 3", "hunger": "amount", "sides": 10, "threshold": 6, "modifier": 1},
                 {"pool": 3, "hunger": 5}, {"pool": "'x'"}):
        case([{"hunger_dice": body, "store_as": "_h"}])
    for body, store in (({"table": "definitions.table", "key": "'1'"}, "_v"), ({"table": "definitions.table", "key": "1"}, "_v"),
                        ({"table": "definitions.table", "key": "2.0", "default": "dflt"}, "_v"),
                        ({"table": "definitions.table", "key": "'x'", "default": "dflt"}, "_v"),
                        ({"table": "definitions.table", "key": "'zz'", "required": True}, "_v"),
                        ({"table": "definitions.missing", "key": "'1'", "required": True}, "_v"),
                        ({"table": "definitions.missing", "key": "'1'", "default": 5}, "_v"),
                        ({"table": "definitions.table", "key": "'1'", "required": "yes"}, "_v"),
                        ({"table": "definitions.table", "key": "'1'", "required": True, "default": 1}, "_v"),
                        ({"table": "definitions.table", "key": "'1'"}, "v"), ({"table": "other.table", "key": "'1'"}, "_v"),
                        ({"table": "definitions.nested.inner", "key": "'k'"}, "_v"), ({"table": "definitions.table", "key": "1 +"}, "_v"),
                        ({"table": "definitions.table", "key": "state.items"}, "_v"), ({"table": 5, "key": "1"}, "_v")):
        case([{"table_lookup": body, "store_as": store}])
    for body in ({"table": "definitions.encounters"}, {"table": "definitions.encounters", "roll": "1d4"},
                 {"table": "definitions.flat", "roll": "1d6+1"}, {"table": "definitions.empty", "default": "none"},
                 {"table": "definitions.missing", "default": "nothing"}, {"table": "definitions.flat", "weight_field": "w"},
                 {"table": "definitions.encounters", "roll": "1d100"}):
        case([{"random_table": body, "store_as": "_pick"}])
    for effects in ([{"for_each": {"collection": "_xs", "as": "_x", "do": [{"log": {"message": "{_x}"}}]}}],
                    [{"set": {"field": "_xs", "value": "state.items"}},
                     {"for_each": {"collection": "_xs", "as": "_x", "do": [{"log": {"message": "{_x}"}}]}}],
                    [{"set": {"field": "_xs", "value": "state.rolls"}}, {"for_each": {"collection": "_xs", "do": [{"set": {"field": "_last", "value": "_item.name"}}]}}],
                    [{"for_each": {"collection": "missing", "do": [{"log": {"message": "never"}}]}}],
                    [{"set": {"field": "_xs", "value": "state.rolls"}}, {"sort": {"collection": "_xs", "by": "init"}}],
                    [{"set": {"field": "_xs", "value": "state.rolls"}}, {"sort": {"collection": "_xs", "by": "init", "direction": "asc"}}],
                    [{"set": {"field": "_xs", "value": "state.rolls"}}, {"sort": {"collection": "_xs", "by": "name", "direction": "DESC"}}],
                    [{"set": {"field": "_xs", "value": "state.rolls"}}, {"set": {"field": "_alias", "value": "_xs"}},
                     {"sort": {"collection": "_xs", "by": "init"}}],
                    [{"set": {"field": "_xs", "value": "state.items"}}, {"sort": {"collection": "_xs", "by": "x"}}],
                    [{"set": {"field": "_xs", "value": [3, "a"]}}, {"sort": {"collection": "_xs", "by": "real"}}],
                    [{"sort": "x"}], [{"set": {"field": "_xs", "value": "state.rolls"}}, {"sort": {"collection": "_xs", "by": "init", "direction": 1}}],
                    [{"list_push": {"list": "_q", "value": "'a'"}}, {"list_push": {"list": "_q", "value": "amount"}}, {"list_pop": {"list": "_q"}, "store_as": "_p"}],
                    [{"list_push": {"list": "_q", "value": "'a'"}, "store_as": "_r"}, {"list_push": {"list": "_q", "value": "'b'"}}],
                    [{"set": {"field": "_q", "value": "state.items"}}, {"set": {"field": "_alias", "value": "_q"}}, {"list_push": {"list": "_q", "value": "'z'"}}],
                    [{"list_pop": {"list": "_none"}}], [{"set": {"field": "_q", "value": 3}}, {"list_push": {"list": "_q", "value": 1}}],
                    [{"list_push": {"list": "", "value": 1}}], [{"list_push": "x"}],
                    [{"shuffle": {"collection": "state.items"}, "store_as": "_deck"}], [{"shuffle": {"collection": "state.items"}, "store_as": "deck"}],
                    [{"shuffle": {"collection": "indices(5)"}, "store_as": "_deck"}, {"set": {"field": "_n", "value": "len(_deck)"}}],
                    [{"shuffle": {"x": 1}, "store_as": "_deck"}],
                    # Python iterates the list object by index: items the body adds are visited too.
                    [{"set": {"field": "_q", "value": "state.items"}}, {"for_each": {"collection": "_q", "as": "_x", "do": [
                        {"condition": "len(_q) < 5", "on_true": [{"list_push": {"list": "_q", "value": "_x"}}]}]}}],
                    [{"set": {"field": "_q", "value": "state.items"}},
                     {"for_each": {"collection": "_q", "as": "_x", "do": [{"list_push": {"list": "_q", "value": 1}}]}}],
                    [{"set": {"field": "_xs", "value": "state.rolls"}}, {"for_each": {"collection": "_xs", "as": "_x", "do": [
                        {"sort": {"collection": "_xs", "by": "init", "direction": "asc"}}, {"log": {"message": "{_x.name}"}}]}}],
                    [{"set": {"field": "_q", "value": "state.items"}}, {"set": {"field": "_alias", "value": "_q"}},
                     {"list_pop": {"list": "_alias"}, "store_as": "_p"}, {"set": {"field": "_left", "value": "state.items"}}],
                    [{"set": {"field": "_x", "value": "_logs"}}, {"log": {"message": "one"}}, {"log": {"message": "two"}}],
                    [{"log": {"message": "zero"}}, {"set": {"field": "_x", "value": "_logs"}}, {"log": {"message": "one"}}],
                    [{"narrate": "zero"}, {"set": {"field": "_n", "value": "_narrative_directives"}}, {"narrate": "one"}],
                    # A snapshot kept from before a refresh is a separate copy of the entity.
                    [{"set": {"field": "_c", "value": "target.conditions"}}, {"set": {"field": "target.hp", "value": 1}},
                     {"list_push": {"list": "_c", "value": "'x'"}}, {"set": {"field": "_after", "value": "target.conditions"}}],
                    # One literal makes a fresh list each time it runs.
                    [{"for_each": {"collection": "_xs", "as": "_x", "do": [{"set": {"field": "_v_{_x}", "value": [0]}}]}},
                     {"list_push": {"list": "_v_1", "value": "'x'"}}],
                    [{"set": {"field": "_xs", "value": [1, 2]}},
                     {"for_each": {"collection": "_xs", "as": "_x", "do": [{"set": {"field": "_v_{_x}", "value": [0]}}]}},
                     {"list_push": {"list": "_v_1", "value": "'x'"}}],
                    [{"set": {"field": "_r", "value": {"count": 2, "results": [1, 5], "total": 6}}}, {"reroll": {"source": "_r"}}],
                    [{"random_table": {"table": "definitions.flat", "roll": "1d2+4"}, "store_as": "_pick"}],
                    [{"random_table": {"table": "definitions.flat", "roll": "1d2+1"}, "store_as": "_pick"}],
                    [{"random_table": {"table": "definitions.encounters", "roll": "1d2+3"}, "store_as": "_pick"}]):
        case(effects)
    choice_effect = {"request_choice": {"choice_id": "c1", "prompt": "Choose for {target.name}", "options": CHOICE_OPTIONS},
                     "store_as": "_choice"}
    after = {"set": {"field": "_chosen", "value": "_choice"}}
    case([choice_effect, after])
    case([choice_effect, after], choice={"choice_id": "c1", "option_id": "flee", "expected_request": REQUEST, "prior_answers": []})
    case([choice_effect, after], choice={"choice_id": "c1", "option_id": "nope", "expected_request": REQUEST, "prior_answers": []})
    case([choice_effect, after], choice={"choice_id": "c2", "option_id": "flee", "expected_request": REQUEST, "prior_answers": []})
    case([choice_effect, choice_effect, after],
         choice={"choice_id": "c1", "option_id": "flee", "expected_request": REQUEST, "prior_answers": []})
    case([choice_effect, choice_effect, after],
         choice={"choice_id": "c1", "option_id": "attack", "expected_request": REQUEST, "prior_answers": [[REQUEST, "flee"]]})
    case([choice_effect, after],
         choice={"choice_id": "c1", "option_id": "attack", "expected_request": REQUEST, "prior_answers": [[REQUEST, "gone"]]})
    for body in ({"choice_id": "", "prompt": "p", "options": CHOICE_OPTIONS}, {"choice_id": "c", "prompt": "p", "options": CHOICE_OPTIONS[:1]},
                 {"choice_id": "c", "prompt": "p", "options": [{"id": "a", "label": "A"}, {"id": "a", "label": "B"}]},
                 {"choice_id": "c", "prompt": "p", "options": [{"id": "-a", "label": "A"}, {"id": "b", "label": "B"}]},
                 {"choice_id": "c", "prompt": "p", "options": [{"id": "a", "label": "{missing}"}, {"id": "b", "label": ""}]},
                 {"choice_id": "c", "prompt": "x" * 4097, "options": CHOICE_OPTIONS},
                 {"choice_id": "c", "prompt": "p", "options": [{"id": f"o{i}", "label": "L"} for i in range(17)]},
                 {"choice_id": "c", "prompt": "p", "options": "_opts"}):
        case([{"set": {"field": "_opts", "value": CHOICE_OPTIONS}}, {"request_choice": body, "store_as": "_c"}])
    case([{"shuffle": {"collection": "state.items"}, "store_as": "_deck"}, choice_effect])
    return cases


ROLL_EXPRESSIONS = ["1d6", "2d6", "1d20", "3d4+1", "{amount}d6", "d8", "4d10"]


def random_handler_effect(rng):
    kind = rng.choice(["dice", "save", "damage", "heal", "condition", "pool", "reroll", "hunger", "table", "random_table",
                       "for_each", "sort", "push", "pop", "set"])
    if kind == "dice":
        body = {"expression": rng.choice(ROLL_EXPRESSIONS)}
        for key in ("advantage", "disadvantage", "critical"):
            if rng.random() < 0.2:
                body[key] = rng.choice(["true", "false", "amount > 1"])
        if rng.random() < 0.3:
            body["modifier"] = rng.choice(["amount", "1", "target.hp"])
        if rng.random() < 0.2:
            body["explode"] = True
        return {"dice": body, "store_as": rng.choice(["_roll", "_r2"])}
    if kind == "save":
        return {"save_throw": {"target": rng.choice(["target", "elf"]), "ability": rng.choice(["dex", "str", "con"]),
                               "dc": rng.choice([8, 12, "amount + 9"])}, "store_as": "_save"}
    if kind == "damage":
        return {"apply_damage": {"target": rng.choice(["target", "elf"]), "amount": rng.choice([1, 3, "amount", "_roll.total"])}}
    if kind == "heal":
        return {"heal": {"target": rng.choice(["target", "elf"]), "amount": rng.choice([1, 5, "amount"])}}
    if kind == "condition":
        return {rng.choice(["apply_condition", "remove_condition"]): {"target": "target", "condition": rng.choice(["prone", "dazed"])}}
    if kind == "pool":
        return {"count_successes": {"pool": rng.choice(["5d6", "{amount}d10"]), "threshold": rng.choice([4, 6])}, "store_as": "_pool"}
    if kind == "reroll":
        return {"reroll": {"source": rng.choice(["_roll", "_r2"]), "predicate": rng.choice(["all", "ones"])}, "store_as": "_rr"}
    if kind == "hunger":
        return {"hunger_dice": {"pool": rng.choice([4, "amount + 2"]), "hunger": rng.choice([0, 1, 2])}, "store_as": "_h"}
    if kind == "table":
        return {"table_lookup": {"table": "definitions.table", "key": rng.choice(["'1'", "'2'", "'x'", "_roll.total"]),
                                 "default": rng.choice([None, "d"])}, "store_as": "_v"}
    if kind == "random_table":
        return {"random_table": {"table": rng.choice(["definitions.encounters", "definitions.flat"]),
                                 **({"roll": "1d4"} if rng.random() < 0.5 else {})}, "store_as": "_pick"}
    if kind == "for_each":
        return {"for_each": {"collection": rng.choice(["_xs", "_q"]), "as": "_x",
                             "do": [{"log": {"message": "{_x}"}}, {"list_push": {"list": "_seen", "value": "_x"}}]}}
    if kind == "sort":
        return {"sort": {"collection": "_xs", "by": rng.choice(["init", "name"]), "direction": rng.choice(["asc", "desc"])}}
    if kind == "push":
        return {"list_push": {"list": rng.choice(["_q", "_xs"]), "value": rng.choice(["amount", "'v'", "_roll.total"])}}
    if kind == "pop":
        return {"list_pop": {"list": rng.choice(["_q", "_xs"])}, "store_as": "_p"}
    return {"set": {"field": rng.choice(["_xs", "_q"]), "value": rng.choice(["state.rolls", "state.items", "_q", "_xs"])}}


def random_handler_cases(rng, count):
    cases = []
    for _ in range(count):
        effects = [random_handler_effect(rng) for _ in range(rng.randint(2, 7))]
        params = {"target": rng.choice(["orc", "orc", "elf"]), "actor": "pc"}
        if rng.random() < 0.5:
            params["amount"] = rng.choice([1, 2, 3])
        cases.append({"document": handler_tools(effects), "tool": "main", "params": params})
    return cases


def shuffled_vars(document) -> list[str]:
    names = []
    for effect in document["tools"]["main"]["effects"]:
        if isinstance(effect, dict) and "shuffle" in effect and isinstance(effect.get("store_as"), str):
            names.append(effect["store_as"])
    return names


COMBAT_STATE = copy.deepcopy(ROLL_STATE)
COMBAT_STATE[f"{NAMESPACE}.combat"] = {"round": 2, "turn_index": 0, "order": [
    {"entity_id": "orc", "initiative_result": 15}, {"entity_id": "elf", "initiative_result": 9}, "box"]}
COMBAT_STATE[f"{NAMESPACE}.entities"]["orc"]["init_mod"] = 2
COMBAT_STATE[f"{NAMESPACE}.entities"]["elf"]["init_mod"] = 7
COMBAT_STATE[f"{NAMESPACE}.entities"]["elf"]["down"] = True
COMBAT_STATE[f"{NAMESPACE}.entities"]["orc"]["long"] = "x" * 200
COMBAT_STATE[f"{NAMESPACE}.entities"]["elf"]["long"] = "\u00e9" * 201
COMBAT_STATE[f"{NAMESPACE}.entities"]["box"]["long"] = "   "
COMBAT_STATE[f"{NAMESPACE}.entities"]["_campaign"] = {"flags": {"lit": True, "off": 0}}
COMBAT_STATE[f"{NAMESPACE}.state"]["party"] = ["orc", "elf", {"entity_id": "box"}, {"id": "ghost"}, 5]


BARE_STATE = copy.deepcopy(ROLL_STATE)
del BARE_STATE[f"{NAMESPACE}.combat"]
STATES = {"combat": COMBAT_STATE, "bare": BARE_STATE}


def procedure_tools(effects, procedures=None) -> dict:
    choose = {"request_choice": {"choice_id": "c1", "prompt": "Choose for {target.name}", "options": CHOICE_OPTIONS},
              "store_as": "_choice"}
    document = handler_tools(effects, extra={
        "ticker": {"parameters": [], "effects": [{"advance_time": "1 round"}, {"advance_initiative": {}, "store_as": "_a"}]},
        "reader": {"parameters": [], "effects": [{"require": {"condition": "combat.round >= 3", "error": "reader saw a stale combat"}},
                                                  {"trigger": "on_inner"}]},
        "chooser": {"parameters": [{"name": "target", "type": "string", "resolve": "entity"}], "effects": [choose]}})
    document["procedures"] = procedures if procedures is not None else {
        "on_outer": {"trigger": "on_outer", "steps": [{"advance_time": "2 rounds"}, {"trigger": "on_inner"}, {"call": "reader"},
                                                       {"log": {"message": "outer {combat}"}}]},
        "on_inner": {"trigger": "on_inner", "steps": [{"require": {"condition": "combat.round >= 2", "error": "inner saw a stale combat"}},
                                                       {"advance_time": "1 round"}, {"log": {"message": "inner {combat}"}}]},
        "on_peek": {"trigger": "on_peek", "steps": [{"set": {"field": "_narrative_combat", "value": "combat"}}]},
        "on_quiet": {"trigger": "on_quiet", "steps": [{"set": {"field": "_narrative_zero", "value": "0"}},
                                                       {"set": {"field": "_narrative_empty", "value": "''"}},
                                                       {"set": {"field": "_narrative_off", "value": "false"}},
                                                       {"set": {"field": "_narrative_on", "value": "1.5"}}]},
        "on_fail_empty": {"trigger": "on_fail_empty", "steps": [{"require": {"condition": "false", "error": ""}}]},
        "on_target_id": {"trigger": "on_target_id", "steps": [{"log": {"message": "{target.name} {target.hp}"}},
                                                               {"set": {"field": "_narrative_hp", "value": "target.hp"}}]},
        "on_tick": {"trigger": "on_tick", "steps": [{"call": "ticker"}, {"advance_time": "1 round"}]},
        "on_choose": {"trigger": "on_choose", "steps": [choose]},
        "on_choose_call": {"trigger": "on_choose_call", "steps": [{"call": "chooser", "params": {"target": "{target}"}}]},
        "on_flag": {"trigger": "on_flag", "condition": "_flag_lit", "steps": [{"log": {"message": "lit"}}]},
        "on_flag_off": {"trigger": "on_flag", "condition": "_flag_off == true", "steps": [{"log": {"message": "off"}}]},
        "on_hit": {"trigger": "on_hit", "steps": [{"log": {"message": "hit {target}"}}, {"set": {"field": "_seen", "value": "target"}},
                                                   {"narrate": "proc narration"}, {"set": {"field": "_narrative_proc", "value": "'boom'"}}]},
        "on_hit_second": {"trigger": "on_hit", "condition": "amount > 1", "steps": [{"log": {"message": "second"}}]},
        "on_fail": {"trigger": "on_fail", "steps": [{"require": {"condition": "false", "error": "procedure refused"}}]},
        "on_loop": {"trigger": "on_loop", "steps": [{"trigger": "on_loop"}]},
        "on_call": {"trigger": "on_call", "steps": [{"call": "helper", "params": {"target": "{target}"}},
                                                     {"set": {"field": "combat_note", "value": "'from procedure'"}}]},
        "on_missing": {"trigger": "on_missing", "steps": [{"apply_damage": {"target": "ghost", "amount": 1}},
                                                           {"log": {"message": "after missing"}}]},
        "on_cond_error": {"trigger": "on_cond_error", "condition": "1 +", "steps": []},
        "on_chain": {"trigger": "on_chain", "steps": [{"trigger": {"name": "on_hit", "context": {"target": "{target}", "amount": 3}}}]},
    }
    return document


def procedure_targeted() -> list[dict]:
    cases = []

    def case(effects, params=None, procedures=None, state=None, choice=None):
        item = {"document": procedure_tools(effects, procedures), "tool": "main",
                "params": BASE_PARAMS if params is None else params}
        if state is not None:
            item["state"] = state
        if choice is not None:
            item["choice"] = choice
        cases.append(item)

    control = {"choice_id": "c1", "option_id": "flee", "expected_request": REQUEST, "prior_answers": []}
    for state in (None, "combat", "bare"):
        for name, context in (("on_peek", {}), ("on_quiet", {}), ("on_fail_empty", {}), ("on_missing", {}),
                              ("on_target_id", {"target": "orc"}), ("on_target_id", {"target": "ghost"}),
                              ("on_target_id", {"target": "elf", "actor": "orc"}), ("on_target_id", {"entity": "orc"})):
            case([{"trigger": name, "context": context, "store_as": "_r"}], state=state)
    for party, body in ((["orc"], {"modifier_field": "init_mod", "modifier_minimum": 2, "modifier_maximum": 2}),
                        (["elf"], {"modifier_field": "init_mod", "modifier_minimum": 2, "modifier_maximum": 2}),
                        (["elf"], {"required_truthy_field": "conditions"}), (["orc"], {"required_truthy_field": "conditions"}),
                        (["elf"], {"required_truthy_field": "speed"}), (["box"], {"required_truthy_field": "name"}),
                        (["elf"], {"modifier_field": "speed"}), (["orc", "elf"], {"modifier_field": "position.band"}),
                        (["orc"], {"label_field": "long"}), (["elf"], {"label_field": "long"}), (["box"], {"label_field": "long"}),
                        (["orc", "elf"], {"modifier_field": "hp", "modifier_minimum": 5, "modifier_maximum": 7})):
        case([{"set": {"field": "_p", "value": party}}, {"initiative": {"combatants": "_p", **body}, "store_as": "_order"}],
             state="combat")
    for body in ({"skip_field": "max_hp", "skip_equals": "7.0"}, {"skip_field": "max_hp", "skip_equals": "9.0"},
                 {"skip_field": "init_mod", "skip_equals": "true"}, {"skip_field": "position.band", "skip_equals": "'close'"},
                 {"skip_field": "missing"}, {"skip_field": "missing", "skip_equals": "none"}):
        case([{"advance_initiative": body, "store_as": "_actor"}], state="combat")
    for body in ("1 rounds\n", "1 rounds\n\n", "1 r\u00e9und", "1\u00a0round", "\u00a02 rounds\u2003", "1.5 rounds", "1. rounds",
                 ".5 rounds", "1.25 minutes", "1_0 rounds", "\u0661\u0662 rounds", "1 \u0661rounds"):
        case([{"advance_time": body}], state="combat")
    for state in (None, "combat", "bare"):
        case([{"trigger": "on_outer", "store_as": "_r"}, {"set": {"field": "_combat", "value": "combat"}}], state=state)
        case([{"trigger": "on_tick", "store_as": "_r"}, {"set": {"field": "_combat", "value": "combat"}}], state=state)
    for name in ("on_tick", "on_choose", "on_choose_call", "on_flag"):
        for state in (None, "combat"):
            case([{"trigger": name, "context": {"target": "{_target_id}"}, "store_as": "_r"}], state=state)
            if name.startswith("on_choose"):
                case([{"trigger": name, "context": {"target": "{_target_id}"}}], state=state, choice=control)
    case([{"request_choice": {"choice_id": "c1", "prompt": "Choose for {target.name}", "options": CHOICE_OPTIONS}, "store_as": "_c"},
          {"trigger": "on_choose", "context": {"target": "{target}"}}], choice=control)
    case([{"call": "chooser", "params": {"target": "{_target_id}"}}], choice=control)

    for effect in ({"trigger": "on_hit", "context": {"target": "{target}"}, "store_as": "_r"},
                   {"trigger": {"name": "on_hit", "context": {"target": "orc", "amount": "{amount}"}}, "store_as": "_r"},
                   {"trigger": {"trigger": "on_hit"}}, {"trigger": "on_nothing", "store_as": "_r"}, {"trigger": "on_fail"},
                   {"trigger": "on_loop"}, {"trigger": "on_call", "context": {"target": "orc"}}, {"trigger": "on_missing"},
                   {"trigger": "on_cond_error"}, {"trigger": "on_chain", "context": {"target": "elf"}, "store_as": "_r"},
                   {"trigger": ""}, {"trigger": 5}, {"trigger": {"name": "on_hit", "context": []}}, {"trigger": ["x"]}):
        case([effect, {"set": {"field": "_after", "value": "1"}}])
        case([effect, {"set": {"field": "_after", "value": "1"}}], state="combat")
    for effect in ({"set_flag": "dragon_defeated"}, {"set_flag": "door", "value": False},
                   {"set_flag": {"flag": "lit", "value": 3}}, {"set_flag": {"set_flag": "x"}}, {"set_flag": ""}, {"set_flag": 3}):
        case([effect])
    for body in ("1 round", "10 minutes", "2 hours", "1.5 hours", "1 day", " 3  rounds ", "1 fortnight", "x rounds", "rounds",
                 "1round", "\u0663 rounds", "1 Rounds", {"duration": "2 rounds"}, {"advance_time": "1 minute"}, {}, 5):
        case([{"advance_time": body}])
        case([{"advance_time": body}], state="combat")
    case([{"set": {"field": "round", "value": "'7'"}}, {"advance_time": "1 round"}])
    case([{"set": {"field": "round", "value": "[1]"}}, {"advance_time": "1 round"}])
    for body in ({"combatants": "_party"}, {"combatants": "_party", "expression": "d20 + 2"}, {"combatants": "_party", "roll": "5"},
                 {"combatants": "_party", "modifier_field": "init_mod"}, {"combatants": "_party", "modifier_field": "missing"},
                 {"combatants": "_party", "modifier_field": "init_mod", "modifier_minimum": 3},
                 {"combatants": "_party", "modifier_field": "init_mod", "modifier_maximum": 5},
                 {"combatants": "_party", "required_truthy_field": "down"}, {"combatants": "_party", "label_field": "name"},
                 {"combatants": "_party", "modifier_field": " "}, {"combatants": "_party", "modifier_minimum": 1.5},
                 {"combatants": "_party", "modifier_minimum": 5, "modifier_maximum": 2}, {"combatants": "_empty"}, "x"):
        case([{"set": {"field": "_party", "value": ["orc", "elf"]}}, {"set": {"field": "_empty", "value": []}},
              {"initiative": body, "store_as": "_order"}], state="combat")
    case([{"set": {"field": "_party", "value": "state.party"}}, {"initiative": {"combatants": "_party"}}], state="combat")
    for body in ({}, {"skip_field": "down", "skip_equals": True}, {"skip_field": "down", "skip_equals": "false"}, {"skip_field": " "},
                 {"skip_field": "hp", "skip_equals": 5}, "x"):
        case([{"advance_initiative": body, "store_as": "_actor"}], state="combat")
        case([{"advance_initiative": body, "store_as": "_actor"}])
    case([{"set": {"field": "order", "value": 3}}, {"advance_initiative": {}}])
    case([{"set": {"field": "turn_index", "value": 5}}, {"advance_initiative": {}}], state="combat")
    case([{"set": {"field": "turn_index", "value": -9}}, {"advance_initiative": {}}], state="combat")
    for effect in ({"transition_scene": "tavern"}, {"transition_scene": {"scene": "road"}}, {"transition_scene": ""}, {"transition_scene": 1},
                   {"defer": {"call": "helper", "params": {"target": "orc"}}}, {"defer": {"tool": "helper", "reason": "now", "priority": 1}, "store_as": "_d"},
                   {"defer": {"params": {}}}, {"defer": "x"},
                   {"register_initiative_trigger": {"trigger_id": "t1", "count": "3", "procedure": "on_hit"}},
                   {"register_initiative_trigger": {"id": "t2", "count": "x", "procedure_name": "p", "repeats": 0, "source": 5}},
                   {"register_initiative_trigger": {"procedure": "p"}}, {"register_initiative_trigger": {"trigger_id": "t"}},
                   {"clock_create": {"clock_id": "doom", "max_segments": 6, "thresholds": {"3": "{target.name} stirs", "x": "no"},
                                     "metadata": {"m": "{amount}", "n": 1}}},
                   {"clock_create": {"id": "c2", "max_segments": "x"}}, {"clock_create": {}}):
        case([effect, {"set": {"field": "_after", "value": "1"}}])
    case([{"defer": {"call": "helper"}}, {"defer": {"call": "helper"}, "store_as": "_d2"}])
    for effects in ([{"clock_create": {"clock_id": "doom", "max_segments": 4}}, {"clock_tick": "doom"}, {"clock_tick": {"clock_id": "doom", "segments": 9}}],
                    [{"clock_create": {"clock_id": "doom"}}, {"set": {"field": "_alias", "value": "_clock_doom"}}, {"clock_tick": {"id": "doom", "segments": "2"}}],
                    [{"clock_tick": "unknown"}], [{"set": {"field": "_clock_x", "value": 3}}, {"clock_tick": "x"}], [{"clock_tick": ""}],
                    [{"clock_tick": {"clock_id": "doom", "segments": "x"}}], [{"clock_tick": 7}]):
        case(effects)
    return cases


def random_procedure_effect(rng):
    kind = rng.choice(["trigger", "trigger", "flag", "time", "init", "advance", "scene", "defer", "register", "clock", "tick", "set"])
    if kind == "trigger":
        return {"trigger": rng.choice(["on_hit", "on_call", "on_chain", "on_nothing", "on_fail"]),
                "context": {"target": rng.choice(["{target}", "orc", "elf"]), "amount": rng.choice([1, 3])}, "store_as": "_r"}
    if kind == "flag":
        return {"set_flag": rng.choice(["a", "b"]), "value": rng.choice([True, False, 1])}
    if kind == "time":
        return {"advance_time": rng.choice(["1 round", "2 minutes", "1 hour", "x"])}
    if kind == "init":
        return {"initiative": {"combatants": "_party", **rng.choice([{}, {"modifier_field": "init_mod"}, {"label_field": "name"}])},
                "store_as": "_order"}
    if kind == "advance":
        return {"advance_initiative": rng.choice([{}, {"skip_field": "down", "skip_equals": True}]), "store_as": "_actor"}
    if kind == "scene":
        return {"transition_scene": rng.choice(["a", "b"])}
    if kind == "defer":
        return {"defer": {"call": "helper", "params": {"target": "{target}"}}}
    if kind == "register":
        return {"register_initiative_trigger": {"trigger_id": rng.choice(["t", "u"]), "procedure": "on_hit", "count": rng.choice([1, "2"])}}
    if kind == "clock":
        return {"clock_create": {"clock_id": rng.choice(["c", "d"]), "max_segments": rng.choice([3, 4])}}
    if kind == "tick":
        return {"clock_tick": {"clock_id": rng.choice(["c", "d"]), "segments": rng.choice([1, 2])}}
    return {"set": {"field": "_party", "value": rng.choice([["orc", "elf"], ["elf"], "state.party"])}}


def random_procedure_cases(rng, count):
    cases = []
    for _ in range(count):
        effects = [{"set": {"field": "_party", "value": ["orc", "elf"]}}] + [random_procedure_effect(rng)
                                                                           for _ in range(rng.randint(1, 6))]
        item = {"document": procedure_tools(effects), "tool": "main",
                "params": {"target": rng.choice(["orc", "elf"]), "actor": "pc", "amount": rng.choice([1, 2, 3])}}
        if rng.random() < 0.6:
            item["state"] = "combat"
        cases.append(item)
    return cases


def deferred_vars(document) -> list[str]:
    names = []
    for effect in document["tools"]["main"]["effects"]:
        if isinstance(effect, dict) and "defer" in effect:
            names.append(effect.get("store_as", "_deferred_id") if isinstance(effect.get("store_as", ""), str) else "_deferred_id")
    return names


def main() -> int:
    procedure_cases = procedure_targeted() + random_procedure_cases(random.Random(6262), 2000)
    for index, item in enumerate(procedure_cases):
        item["seed"] = index
        state = STATES.get(item.get("state"), ROLL_STATE)
        item.update(run(item["document"], item["tool"], item["params"], state=state, seed=index, procedures=True,
                        choice=item.get("choice")))
        if deferred_vars(item["document"]):
            item["deferred"] = deferred_vars(item["document"])
        if wide({key: item[key] for key in ("result", "mutations") if key in item}):
            item["wide_int"] = True
    PROCEDURES_OUTPUT.write_bytes((json.dumps({"python": sys.version.split()[0], "namespace": NAMESPACE, "state": ROLL_STATE,
                                               "states": STATES, "participants": PARTICIPANTS, "procedures": True,
                                               "cases": procedure_cases}, ensure_ascii=True, allow_nan=False) + "\n").encode("ascii"))
    print(f"{len(procedure_cases)} procedure cases")
    handler_cases = handler_targeted() + random_handler_cases(random.Random(5151), 2500)
    for index, item in enumerate(handler_cases):
        item["seed"] = index
        item.update(run(item["document"], item["tool"], item["params"], state=ROLL_STATE, seed=index,
                        choice=item.get("choice")))
        if shuffled_vars(item["document"]):
            item["shuffled"] = shuffled_vars(item["document"])
        if wide({key: item[key] for key in ("result", "mutations") if key in item}):
            item["wide_int"] = True
    HANDLERS_OUTPUT.write_bytes((json.dumps({"python": sys.version.split()[0], "namespace": NAMESPACE, "state": ROLL_STATE,
                                             "participants": PARTICIPANTS, "cases": handler_cases},
                                            ensure_ascii=True, allow_nan=False) + "\n").encode("ascii"))
    print(f"{len(handler_cases)} handler cases")
    cases = targeted() + random_cases(random.Random(4242), 2500)
    for item in cases:
        item.update(run(item["document"], item["tool"], item["params"]))
        if wide({key: item[key] for key in ("result", "mutations") if key in item}):
            # Rust refuses the integer where Python stores it; the test expects that refusal.
            item["wide_int"] = True
    document = {"python": sys.version.split()[0], "namespace": NAMESPACE, "state": STATE, "participants": PARTICIPANTS,
                "cases": cases}
    OUTPUT.write_bytes((json.dumps(document, ensure_ascii=True, allow_nan=False) + "\n").encode("ascii"))
    kinds = {}
    for item in cases:
        if "raise" in item:
            key = "raise " + item["raise"][0]
        elif "load_error" in item:
            key = "load_error"
        else:
            result = item["result"]
            key = "ok" if result["success"] else f"fail {result['failure_kind']}"
        kinds[key] = kinds.get(key, 0) + 1
    print(f"{len(cases)} cases", dict(sorted(kinds.items(), key=lambda kv: -kv[1])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
