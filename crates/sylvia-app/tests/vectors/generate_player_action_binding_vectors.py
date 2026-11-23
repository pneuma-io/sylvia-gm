"""Generate the player-control and parameter-binding vectors for sylvia-app from the frozen Python.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_player_action_binding_vectors.py

(`.venv/bin/python` on Linux.) Deterministic and checked in. Reference tooling for the port,
retired with the Python source.

Each case calls one of `validate_player_control`, `resolve_party_action_parameters`,
`resolve_inventory_action_parameters` or `retain_player_action_origin` in
`application/player_actions.py`, with targeted inputs and seeded random ones. The record is the
result, or the exception's class, message and (for `CommandRejected`) code.

The action and scope are frozen, as the owner's always are. The surface data is frozen too, as a
state source is (a literal source is thawed; differences row NEXT).
"""

from __future__ import annotations

import copy
import json
import random
import sys
from pathlib import Path

from sylvia_gm.application.player_actions import (
    resolve_inventory_action_parameters,
    resolve_party_action_parameters,
    retain_player_action_origin,
    validate_player_control,
)
from sylvia_gm.domain.authority import MechanicOutcome, owned_json_value, owned_payload, thaw_json_value
from sylvia_gm.domain.errors import CommandRejected

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "player_action_binding_vectors.json"


def outcome_of(call) -> dict:
    try:
        value = call()
    except Exception as error:  # noqa: BLE001 - every exception Python lets through
        record = {"error": [type(error).__name__, str(error)]}
        if isinstance(error, CommandRejected):
            record["code"] = error.code
        return record
    return {"ok": thaw_json_value(value) if value is not None else None}


# ── Targeted cases ────────────────────────────────────────────────────────────────────────────

SCOPE = {"available": True, "focused_actor_id": "pc.a", "actors": [{"id": "pc.a", "name": "A"},
                                                                      {"id": "pc.b", "name": "B"}]}
PARTICIPANTS = [{"id": "pc.a", "name": "A"}, {"id": "pc.b"}, "pc.c", {"name": "no id"}, {"id": 7}, {"id": None}]


def validate_cases() -> list[dict]:
    cases = []

    def case(label, scope, parameters, actor_parameter):
        cases.append({"label": label, "function": "validate", "scope": scope, "parameters": parameters,
                      "actor_parameter": actor_parameter})

    case("no scope", None, {"actor": "anyone"}, "actor")
    case("allowed actor", SCOPE, {"actor": "pc.b"}, "actor")
    case("other actor", SCOPE, {"actor": "pc.z"}, "actor")
    case("missing actor parameter value", SCOPE, {}, "actor")
    case("non-text actor", SCOPE, {"actor": 1}, "actor")
    case("no actor parameter", SCOPE, {"actor": "pc.z"}, None)
    case("unavailable", {**SCOPE, "available": False}, {"actor": "pc.a"}, "actor")
    case("unavailable without actor parameter", {**SCOPE, "available": False}, {}, None)
    case("available is one", {**SCOPE, "available": 1}, {"actor": "pc.a"}, "actor")
    case("available missing", {"actors": SCOPE["actors"]}, {"actor": "pc.a"}, "actor")
    case("empty actors", {**SCOPE, "actors": []}, {"actor": "pc.a"}, "actor")
    case("actors missing", {"available": True}, {"actor": "pc.a"}, "actor")
    case("actors missing without actor parameter", {"available": True}, {}, None)
    case("actor without id", {**SCOPE, "actors": [{"id": "pc.a"}, {"name": "x"}]}, {"actor": "pc.a"}, "actor")
    case("actor with list id", {**SCOPE, "actors": [{"id": ["pc.a"]}]}, {"actor": "pc.a"}, "actor")
    case("actor with dict id", {**SCOPE, "actors": [{"id": {"x": 1}}]}, {"actor": "pc.a"}, "actor")
    case("actor with number id", {**SCOPE, "actors": [{"id": 3}, {"id": "pc.a"}]}, {"actor": "pc.a"}, "actor")
    for label, actor in (("text", "pc.a"), ("list", ["id"]), ("number", 3), ("float", 1.5), ("null", None),
                         ("bool", True)):
        case(f"actor is {label}", {**SCOPE, "actors": [actor]}, {"actor": "pc.a"}, "actor")
    for label, actors in (("text", "pc.a"), ("empty text", ""), ("mapping", {"id": "pc.a"}), ("empty mapping", {}),
                          ("number", 3), ("float", 2.5), ("null", None), ("bool", False)):
        case(f"actors is {label}", {**SCOPE, "actors": actors}, {"actor": "pc.a"}, "actor")
    return cases


PARTY = {"intent": "attack", "bindings": {"attacker": "actor"}, "params": {"weapon": "sword", "attacker": "x"}}


def party_cases() -> list[dict]:
    cases = []

    def case(label, action, selection, data, allow=False):
        cases.append({"label": label, "function": "party", "action": action, "selection": selection, "data": data,
                      "allow_entity_ids": allow})

    data = {"participants": PARTICIPANTS, "entities": {"npc.x": {}, "npc.y": {}}}
    case("visible member", PARTY, {"actor": "pc.a"}, data)
    case("member without name", PARTY, {"actor": "pc.b"}, data)
    case("text participant", PARTY, {"actor": "pc.c"}, data)
    case("entity without allow", PARTY, {"actor": "npc.x"}, data)
    case("entity with allow", PARTY, {"actor": "npc.x"}, data, True)
    case("entities not a mapping", PARTY, {"actor": "npc.x"}, {"participants": PARTICIPANTS, "entities": ["npc.x"]}, True)
    case("unknown member", PARTY, {"actor": "pc.z"}, data, True)
    case("number actor", PARTY, {"actor": 7}, data)
    case("null actor", PARTY, {"actor": None}, data)
    case("new binding key", {**PARTY, "params": {}}, {"actor": "pc.a"}, data)
    case("two bindings", {**PARTY, "bindings": {"attacker": "actor", "helper": "actor"}}, {"actor": "pc.a"}, data)
    case("empty bindings", {**PARTY, "bindings": {}}, {"actor": "pc.a"}, data)
    case("other binding source", {**PARTY, "bindings": {"attacker": "target"}}, {"actor": "pc.a"}, data)
    case("list binding source", {**PARTY, "bindings": {"attacker": ["actor"]}}, {"actor": "pc.a"}, data)
    case("dict binding source", {**PARTY, "bindings": {"a": "actor", "b": {"x": 1}}}, {"actor": "pc.a"}, data)
    case("bindings not a mapping", {**PARTY, "bindings": ["attacker"]}, {"actor": "pc.a"}, data)
    case("bindings missing", {"params": {}}, {"actor": "pc.a"}, data)
    case("params missing", {"bindings": {"attacker": "actor"}}, {"actor": "pc.a"}, data)
    case("params not a mapping", {**PARTY, "params": []}, {"actor": "pc.a"}, data)
    case("params not a mapping and list binding", {**PARTY, "params": [], "bindings": {"a": ["x"]}}, {"actor": "pc.a"}, data)
    case("selection extra key", PARTY, {"actor": "pc.a", "x": 1}, data)
    case("selection empty", PARTY, {}, data)
    case("selection null", PARTY, None, data)
    case("selection list", PARTY, ["actor"], data)
    case("selection text", PARTY, "actor", data)
    case("data null", PARTY, {"actor": "pc.a"}, None)
    case("data list", PARTY, {"actor": "pc.a"}, [PARTICIPANTS])
    case("participants missing", PARTY, {"actor": "pc.a"}, {})
    case("participants a mapping", PARTY, {"actor": "pc.a"}, {"participants": {"pc.a": {}}})
    case("participants text", PARTY, {"actor": "p"}, {"participants": "pc.a"})
    case("participants number", PARTY, {"actor": "pc.a"}, {"participants": 3})
    case("participant with list id", PARTY, {"actor": "pc.a"}, {"participants": [{"id": "pc.a"}, {"id": ["x"]}]})
    case("participant with dict id", PARTY, {"actor": "nobody"}, {"participants": [{"id": {"x": 1}}]})
    case("participant with list id, number actor", PARTY, {"actor": 3}, {"participants": [{"id": ["x"]}]})
    case("nested fixed params", {**PARTY, "params": {"mods": {"a": [1, 2.5, None]}, "attacker": "x"}},
         {"actor": "pc.a"}, data)
    return cases


INVENTORY = {"intent": "use_item", "bindings": {"owner": "actor", "item": "declared_action"},
             "params": {"mode": "use", "owner": "x"}}


def inventory_cases() -> list[dict]:
    cases = []

    def case(label, action, selection, data):
        cases.append({"label": label, "function": "inventory", "action": action, "selection": selection, "data": data})

    data = {"participants": PARTICIPANTS}
    form = {"actor": "pc.a", "declared_action": {"item_id": "potion", "count": 2}}
    case("owner and form", INVENTORY, form, data)
    case("owner only", {**INVENTORY, "bindings": {"owner": "actor"}}, {"actor": "pc.b"}, data)
    case("two parameters from the actor", {**INVENTORY, "bindings": {"owner": "actor", "user": "actor"}},
         {"actor": "pc.a"}, data)
    case("declared action only", {**INVENTORY, "bindings": {"item": "declared_action"}}, {"declared_action": 1}, data)
    case("empty bindings", {**INVENTORY, "bindings": {}}, {}, data)
    case("empty bindings with actor", {**INVENTORY, "bindings": {}}, {"actor": "pc.a"}, data)
    case("selection extra key", INVENTORY, {**form, "x": 1}, data)
    case("selection missing form", INVENTORY, {"actor": "pc.a"}, data)
    case("other binding source", {**INVENTORY, "bindings": {"owner": "actor", "item": "target"}}, form, data)
    case("number binding source", {**INVENTORY, "bindings": {"owner": "actor", "item": 3}}, form, data)
    case("null binding source", {**INVENTORY, "bindings": {"owner": "actor", "item": None}}, form, data)
    case("list binding source", {**INVENTORY, "bindings": {"owner": "actor", "item": ["x"]}}, form, data)
    case("list binding source, bad participants", {**INVENTORY, "bindings": {"item": ["x"]}}, form, {"participants": 1})
    case("list binding source, no selection", {**INVENTORY, "bindings": {"item": ["x"]}}, None, data)
    case("unknown owner", INVENTORY, {**form, "actor": "pc.z"}, data)
    case("number owner", INVENTORY, {**form, "actor": 7}, data)
    case("number owner with list ids", INVENTORY, {**form, "actor": 7}, {"participants": [{"id": ["x"]}]})
    case("text owner with list ids", INVENTORY, form, {"participants": [{"id": "pc.a"}, {"id": ["x"]}]})
    case("text owner with dict ids", INVENTORY, form, {"participants": [{"id": {"a": 1}}]})
    case("bindings not a mapping", {**INVENTORY, "bindings": None}, form, data)
    case("params not a mapping", {**INVENTORY, "params": "x"}, form, data)
    case("selection null", INVENTORY, None, data)
    case("selection list", INVENTORY, [form], data)
    case("data null", INVENTORY, form, None)
    case("participants text", INVENTORY, {**form, "actor": "p"}, {"participants": "pc.a"})
    case("participants a mapping", INVENTORY, form, {"participants": {"pc.a": {}}})
    case("entities are not owners", INVENTORY, {**form, "actor": "npc.x"},
         {"participants": PARTICIPANTS, "entities": {"npc.x": {}}})
    return cases


def retain_cases() -> list[dict]:
    result = {"ok": True, "total": 12}
    prior = [{"kind": "note", "text": "hit"}]
    origin = {"surface": "party", "action": "attack", "selection": {"actor": "pc.a"}}
    return [
        {"label": "no origin", "function": "retain", "result": result, "consequences": prior, "origin": None},
        {"label": "origin after prior consequences", "function": "retain", "result": result, "consequences": prior,
         "origin": origin},
        {"label": "origin alone", "function": "retain", "result": result, "consequences": [], "origin": origin},
        {"label": "empty origin", "function": "retain", "result": {}, "consequences": prior * 2, "origin": {}},
    ]


# ── Seeded random cases ───────────────────────────────────────────────────────────────────────

IDS = ["pc.a", "pc.b", "pc.c", "npc.x", "zed"]
SOURCES = ["actor", "actor", "actor", "declared_action", "declared_action", "target", 3, None]
NAMES = ["attacker", "owner", "item", "user", "actor"]


def leaf(rng: random.Random) -> object:
    return rng.choice([rng.choice(IDS), rng.randint(-3, 9), rng.random(), None, True, False,
                       [rng.choice(IDS)], {"id": rng.choice(IDS)}])


def random_participants(rng: random.Random) -> object:
    roll = rng.random()
    if roll < 0.05:
        return rng.choice(["pc.a", 3, {"pc.a": {}}, None])
    items = []
    for _ in range(rng.randint(0, 5)):
        pick = rng.random()
        if pick < 0.75:
            items.append({"id": rng.choice(IDS), "name": "N"})
        elif pick < 0.8:
            items.append({"id": leaf(rng)})
        elif pick < 0.9:
            items.append({"name": "x"})
        else:
            items.append(rng.choice(IDS))
    return items


def random_data(rng: random.Random) -> object:
    if rng.random() < 0.04:
        return rng.choice([None, [], "data"])
    data = {}
    if rng.random() < 0.95:
        data["participants"] = random_participants(rng)
    if rng.random() < 0.5:
        data["entities"] = ({rng.choice(IDS): {} for _ in range(rng.randint(0, 3))} if rng.random() < 0.9
                            else [rng.choice(IDS)])
    return data


def random_action(rng: random.Random, inventory: bool) -> dict:
    action = {"intent": "go"}
    if rng.random() < 0.6:
        # A well-formed declaration, so the random cases also reach the bindings themselves.
        names = rng.sample(NAMES, rng.randint(1, 3) if inventory else 1)
        action["bindings"] = {name: ("actor" if index == 0 or not inventory else rng.choice(["actor", "declared_action"]))
                              for index, name in enumerate(names)}
        action["params"] = {name: leaf(rng) for name in rng.sample(NAMES, rng.randint(0, 3))}
        return action
    if rng.random() < 0.96:
        bindings = {}
        for _ in range(rng.choice([1, 1, 1, 2, 2, 0, 3])):
            source = rng.choice(SOURCES) if rng.random() < 0.97 else rng.choice([["actor"], {"a": 1}])
            if not inventory and rng.random() < 0.6:
                source = "actor"
            bindings[rng.choice(NAMES)] = source
        action["bindings"] = bindings if rng.random() < 0.97 else list(bindings)
    if rng.random() < 0.96:
        action["params"] = ({name: leaf(rng) for name in rng.sample(NAMES, rng.randint(0, 3))} if rng.random() < 0.97
                            else "params")
    return action


def random_selection(rng: random.Random, action: dict) -> object:
    if rng.random() < 0.04:
        return rng.choice([None, ["actor"], "actor"])
    bindings = action.get("bindings")
    sources = [s for s in bindings.values() if isinstance(s, str)] if isinstance(bindings, dict) else ["actor"]
    selection = {}
    for source in dict.fromkeys(sources):
        if rng.random() < 0.97:
            selection[source] = rng.choice(IDS) if source == "actor" and rng.random() < 0.9 else leaf(rng)
    if rng.random() < 0.04:
        selection[rng.choice(["actor", "declared_action", "extra"])] = leaf(rng)
    return selection


def random_scope(rng: random.Random) -> object:
    if rng.random() < 0.08:
        return None
    actors = [{"id": rng.choice(IDS), "name": "N"} for _ in range(rng.randint(0, 3))]
    if rng.random() < 0.05:
        actors.append({"id": leaf(rng)})
    scope = {"available": rng.random() < 0.85, "focused_actor_id": None, "actors": actors}
    if rng.random() < 0.03:
        del scope["actors"]
    return scope


def random_cases(seed: int, count: int) -> list[dict]:
    rng = random.Random(seed)
    cases = []
    for index in range(count):
        kind = rng.choice(["validate", "party", "party", "inventory", "inventory"])
        label = f"random {kind} {index}"
        if kind == "validate":
            parameters = {name: (rng.choice(IDS) if rng.random() < 0.8 else leaf(rng))
                          for name in rng.sample(NAMES, rng.randint(0, 2))}
            cases.append({"label": label, "function": "validate", "scope": random_scope(rng), "parameters": parameters,
                          "actor_parameter": rng.choice(NAMES + [None])})
        elif kind == "party":
            action = random_action(rng, False)
            cases.append({"label": label, "function": "party", "action": action,
                          "selection": random_selection(rng, {"bindings": {"x": "actor"}}),
                          "data": random_data(rng), "allow_entity_ids": rng.random() < 0.5})
        else:
            action = random_action(rng, True)
            cases.append({"label": label, "function": "inventory", "action": action,
                          "selection": random_selection(rng, action), "data": random_data(rng)})
    return cases


def run(case: dict) -> dict:
    case = copy.deepcopy(case)
    function = case["function"]
    if function == "validate":
        scope = None if case["scope"] is None else owned_payload(case["scope"])
        return outcome_of(lambda: validate_player_control(scope, owned_payload(case["parameters"]),
                                                          case["actor_parameter"]))
    if function == "party":
        return outcome_of(lambda: resolve_party_action_parameters(
            owned_payload(case["action"]), case["selection"], owned_json_value(case["data"]),
            allow_entity_ids=case["allow_entity_ids"]))
    if function == "inventory":
        return outcome_of(lambda: resolve_inventory_action_parameters(
            owned_payload(case["action"]), case["selection"], owned_json_value(case["data"])))
    outcome = MechanicOutcome(result=owned_payload(case["result"]),
                              consequences=tuple(owned_payload(item) for item in case["consequences"]))
    origin = None if case["origin"] is None else owned_payload(case["origin"])
    kept = retain_player_action_origin(outcome, origin)
    return {"ok": {"result": thaw_json_value(kept.result),
                   "consequences": [thaw_json_value(item) for item in kept.consequences]}}


def main() -> None:
    cases = validate_cases() + party_cases() + inventory_cases() + retain_cases() + random_cases(20261123, 1500)
    for case in cases:
        case["expected"] = run(case)
    OUTPUT.write_bytes((json.dumps({"python": sys.version.split()[0], "cases": cases}, ensure_ascii=True,
                                   allow_nan=False, indent=None, separators=(",", ":")) + "\n").encode("ascii"))
    counts: dict[str, int] = {}
    for case in cases:
        key = case["function"] + (" error " + case["expected"]["error"][0] if "error" in case["expected"] else " ok")
        counts[key] = counts.get(key, 0) + 1
    print(len(cases), dict(sorted(counts.items())))


if __name__ == "__main__":
    main()
