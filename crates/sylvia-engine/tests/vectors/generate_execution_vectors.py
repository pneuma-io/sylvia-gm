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

from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge
from sylvia_gm.runtime._runtime import RulesetRuntime
from sylvia_gm.runtime._tool_executor import execute_tool

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "execution_vectors.json"
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


def run(document: dict, tool: str, params: dict, state=STATE, participants=PARTICIPANTS) -> dict:
    try:
        runtime = RulesetRuntime(None, copy.deepcopy(document))
    except Exception as error:  # noqa: BLE001
        return {"load_error": [type(error).__name__, str(error)]}
    tools = runtime.tools
    bridge = AuthorityMemoryStateBridge(NAMESPACE, copy.deepcopy(state), copy.deepcopy(participants))

    def nested(name, nested_params, parent):
        return execute_tool(name, nested_params, tools, bridge, runtime.ruleset_config, runtime.definitions,
                            bridge.combat_state, tool_executor=nested, budget=parent.budget)

    try:
        result = execute_tool(tool, copy.deepcopy(params), tools, bridge, runtime.ruleset_config,
                              runtime.definitions, bridge.combat_state, tool_executor=nested)
    except Exception as error:  # noqa: BLE001 - every exception class is behaviour here
        return {"raise": [type(error).__name__, str(error)]}
    record = {"result": plain(result)}
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


def main() -> int:
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
