"""Generate reference vectors for sylvia_app's first lane 1 modules from the frozen Python.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_app_leaves_vectors.py

Modules: `setting_character_requirements`, `campaign_starting_world`,
`srd_lite_weapon_set_migration` and `network_graph`. No public package ships character
requirements, so those inputs are synthetic. `campaign_starting_world` runs on a shipped Plan
(read by the Rust test from its path) given authored mechanics and party placements, validated
by `validate_campaign_plan` first. Every input is mutated (stored as edits).
For each call it records Python's outcome: the result as `json.dumps` writes it (or that text's
SHA-256 above 1,000 characters), or the exception class, message and diagnostics.
The output is deterministic (fixed seed) and checked in, so the Rust tests need no Python.
This script is reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.application import network_graph as graph  # noqa: E402
from sylvia_gm.application.campaign_starting_world import materialize_starting_world  # noqa: E402
from sylvia_gm.application.setting_character_requirements import (  # noqa: E402
    parse_setting_character_requirements,
    validate_setting_character_data,
)
from sylvia_gm.application.srd_lite_weapon_set_migration import migrate_state  # noqa: E402
from sylvia_gm.domain.authority import thaw_json_value  # noqa: E402
from sylvia_gm.domain.campaign_plan import PackageBinding, validate_campaign_plan  # noqa: E402

OUTPUT = Path(__file__).with_name("app_leaves_vectors.json")
PLAN = "campaign-plans/riichi-quick-test/plan.json"
rng = random.Random(20261126)

JUNK = [None, True, False, 0, 1, -1, 2.5, "", " ", "x", "x ", "object", "string", "character", "ranged", "melee",
        "two_handed", "directed", "bidirectional", "tactical_grid", "network_graph", "actor", "node", [], [1], ["x"],
        [[1]], {}, {"k": 1}, {"id": "x"}, {"$participant": "id"}]


def plain(value):
    return thaw_json_value(value)


def outcome(call, project=plain):
    try:
        result = call()
    except Exception as error:  # every class is behaviour
        diagnostics = getattr(error, "diagnostics", None)
        if diagnostics:
            return [type(error).__name__, str(error), getattr(error, "code", None),
                    [item.as_payload() for item in diagnostics]]
        return [type(error).__name__, str(error), getattr(error, "code", None), []]
    text = json.dumps(project(result))
    return ["ok", text] if len(text) <= 1000 else ["ok-sha256", hashlib.sha256(text.encode("utf-8")).hexdigest()]


def leaves(value, path=()):
    yield path, value
    if isinstance(value, dict):
        for key, item in value.items():
            yield from leaves(item, (*path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from leaves(item, (*path, index))


def apply(document, edits):
    document = copy.deepcopy(document)
    for op, path, value in edits:
        if not path:
            document = copy.deepcopy(value)
            continue
        parent = document
        for part in path[:-1]:
            parent = parent[part]
        if op == "delete":
            del parent[path[-1]]
        else:
            parent[path[-1]] = copy.deepcopy(value)
    return document


def variants(document, count):
    paths = [(path, value) for path, value in leaves(document) if path]
    yield [], document
    for _ in range(count):
        edits = []
        for _ in range(1 if rng.random() < 0.8 else 2):
            path, value = rng.choice(paths)
            if isinstance(value, dict) and value and rng.random() < 0.3:
                edits.append(["delete", [*path, rng.choice(list(value))], None])
            elif isinstance(value, dict) and rng.random() < 0.15:
                edits.append(["set", [*path, rng.choice(["extra", "visible", "id", "format"])], rng.choice(JUNK)])
            else:
                edits.append(["set", list(path), rng.choice(JUNK)])
        try:
            yield edits, apply(document, edits)
        except (KeyError, IndexError, TypeError):
            continue


# --- setting_character_requirements ---------------------------------------------------------

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["origin"], "properties": {
    "origin": {"type": "string", "enum": ["harbor", "hills", "court"]},
    "debts": {"type": "integer", "minimum": 0, "maximum": 9},
    "ties": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                        "properties": {"name": {"type": "string"}}}},
    "notes": {"type": "object", "additionalProperties": {"type": "string"}},
}}
ENVELOPE = {"format_id": "sylvia-gm-setting-character-requirements", "format_version": 1, "schema": SCHEMA}
DATA = {"origin": "harbor", "debts": 2, "ties": [{"name": "Ana"}], "notes": {"a": "b"}}


def requirements_cases():
    cases = []
    for edits, document in variants(ENVELOPE, 260):
        text = json.dumps(document)
        cases.append({"fn": "parse_setting_character_requirements", "args": [{"base": "envelope", "edits": edits}],
                      "outcome": outcome(lambda: parse_setting_character_requirements(text))})
    for raw in ["", "{", "[]", " " * (256 * 1024 + 1), json.dumps(ENVELOPE).replace('"format_version": 1',
                '"format_version": 1, "format_version": 1')]:
        cases.append({"fn": "parse_setting_character_requirements", "args": [{"raw": raw}],
                      "outcome": outcome(lambda: parse_setting_character_requirements(raw))})
    schema = parse_setting_character_requirements(json.dumps(ENVELOPE))
    for edits, document in variants(DATA, 200):
        for with_schema in (True, False):
            cases.append({"fn": "validate_setting_character_data",
                          "args": [{"base": "data", "edits": edits}, {"inline": with_schema}],
                          "outcome": outcome(lambda: validate_setting_character_data(
                              document, schema=schema if with_schema else None))})
    for value in ({}, [], "x", None, {"origin": "x" * (300 * 1024)}):
        for with_schema in (True, False):
            cases.append({"fn": "validate_setting_character_data", "args": [{"inline": value}, {"inline": with_schema}],
                          "outcome": outcome(lambda: validate_setting_character_data(
                              value, schema=schema if with_schema else None))})
    bad = {**SCHEMA, "properties": {"origin": {"type": "string"}, "deep": {"type": "object"}}}
    cases.append({"fn": "validate_setting_character_data", "args": [{"inline": DATA}, {"schema": bad}],
                  "outcome": outcome(lambda: validate_setting_character_data(DATA, schema=bad))})
    return cases


# --- campaign_starting_world -------------------------------------------------------------------

RULESET = "pneuma.riichi-mahjong"
MECHANICS = [{"namespace": RULESET, "key": "table", "value": {"seats": [], "z": 1, "a": {"b": 2}}},
             {"namespace": RULESET + ".world", "key": "actors", "value": {"npc-1": {"name": "Rin"}}}]
PLACEMENTS = [{"entity_state": {"seat": "east", "label": {"$participant": "name"}},
               "append": [{"namespace": RULESET, "key": "table.seats", "value": {"$participant": "id"}}]},
              {"entity_state": {"seat": "south"},
               "append": [{"namespace": RULESET, "key": "table.seats", "value": {"id": {"$participant": "id"}}}]}]
PARTY = {"you": {"hp": 3, "b": 1, "a": 2}, "ally": {"hp": 2}}
NAMES = {"you": "You", "ally": "Ally"}
REFERENCE = {"namespace": RULESET + ".world", "key": "actors"}


def plan_expectation(plan):
    paths = {plan["starting_location"]["path"], *(r["path"] for v in plan["setting_references"].values() for r in v)}
    party = plan["starting_party"]
    if isinstance(party["player_character"].get("character"), dict):
        paths.add(party["player_character"]["character"]["path"])
    paths.update(item["path"] for item in party["required_companions"])
    paths.update(item["source_ref"]["path"] for item in plan["initial_state"]["entities"])
    for act in plan["structure"]:
        paths.update(act["setting_refs"])
    bindings = plan["bindings"]
    return {"ruleset": bindings["ruleset"], "setting": bindings["campaign_setting"],
            "closure": bindings["package_closure_sha256"], "paths": sorted(paths), "knowledge": []}


def validated(plan, expect):
    def binding(value):
        return PackageBinding(value["id"], value["version"], value["content_sha256"])
    return validate_campaign_plan(plan, readable_markdown="# Plan", expected_ruleset=binding(expect["ruleset"]),
                                  expected_setting=binding(expect["setting"]),
                                  expected_closure_sha256=expect["closure"], setting_paths=frozenset(expect["paths"]))


def world_cases(plan):
    cases = []
    expect = plan_expectation(plan)
    project = lambda states: [[s.namespace, s.key, s.value] for s in states]  # noqa: E731
    bases = [
        [["set", ["initial_state", "mechanics"], MECHANICS]],
        [["set", ["initial_state", "mechanics"], MECHANICS], ["set", ["initial_state", "party_placements"], PLACEMENTS]],
        [["set", ["initial_state", "mechanics"], MECHANICS],
         ["set", ["initial_state", "party_placements"], PLACEMENTS[:1]]],
        [],
    ]
    for plan_edits in bases:
        try:
            document = validated(apply(plan, plan_edits), expect)
        except Exception as error:  # the base must validate; a failure here is a generator bug
            raise SystemExit(f"base plan did not validate: {error}")
        inputs = {"party": PARTY, "names": NAMES, "reference": REFERENCE}
        for edits, args in variants(inputs, 120):
            party, names, reference = args.get("party", {}), args.get("names"), args.get("reference")
            cases.append({"fn": "materialize_starting_world",
                          "args": [{"plan": PLAN, "edits": plan_edits}, {"base": "world_inputs", "edits": edits}],
                          "outcome": outcome(lambda: materialize_starting_world(
                              document, entity_reference=reference, party_entities=party,
                              party_names=names), project)})
    return cases, expect


# --- srd_lite_weapon_set_migration -------------------------------------------------------------

WEAPONS = {"sword": {"kind": "melee", "properties": ["versatile"]}, "bow": {"kind": "ranged", "properties": ["two_handed"]},
           "maul": {"kind": "melee", "properties": ["two_handed", "heavy"]}, "dagger": {"kind": "melee", "properties": ["light"]}}
STATE = {
    "sylvia.srd-lite.bg3.state": {"round": 1},
    "sylvia.srd-lite.bg3.entities": {
        "a": {"creature_kind": "character", "inventory": ["sword", "shield"], "held_items": ["sword"],
              "worn_shield": {"inventory_item": "shield"}, "light_attack_source_weapons": ["dagger", "rock"]},
        "b": {"creature_kind": "character", "inventory": ["bow"], "held_items": ["bow"]},
        "c": {"creature_kind": "character", "inventory": ["maul", "shield"], "held_items": ["maul"],
              "worn_shield": {"inventory_item": "shield"}},
        "d": {"creature_kind": "character", "weapon_sets": {"format": 1}},
        "e": {"creature_kind": "monster", "held_items": ["sword"]},
        "f": {"creature_kind": "character", "inventory": ["sword", "dagger"], "held_items": ["sword", "dagger"],
              "light_attack_source_items": ["kept"]},
    },
    "other": [1, 2],
}


def migration_cases():
    cases = []
    for edits, state in variants(STATE, 320):
        cases.append({"fn": "migrate_state", "args": [{"base": "state", "edits": edits}, {"base": "weapons", "edits": []}],
                      "outcome": outcome(lambda: migrate_state(state, WEAPONS))})
    for edits, weapons in variants(WEAPONS, 80):
        cases.append({"fn": "migrate_state", "args": [{"base": "state", "edits": []}, {"base": "weapons", "edits": edits}],
                      "outcome": outcome(lambda: migrate_state(STATE, weapons))})
    pending = {**STATE, "sylvia.srd-lite.bg3.state": {"pending_srd_action_stack": [1]}}
    cases.append({"fn": "migrate_state", "args": [{"inline": pending}, {"base": "weapons", "edits": []}],
                  "outcome": outcome(lambda: migrate_state(pending, WEAPONS))})
    return cases


# --- network_graph -----------------------------------------------------------------------------

GRAPH = {
    "graph_id": "net", "revision": 3,
    "groups": {"g1": {"label": "Core", "visible": True}, "g0": {"label": "Hidden", "visible": False}},
    "nodes": {
        "n2": {"label": "Gate", "kind": "door", "group_id": "g1", "visible": True, "status": "locked",
               "functions": {"open": {"label": "Open", "enabled": True, "visible": True, "state": "ready"},
                             "zap": {"label": "Zap", "enabled": False, "visible": True},
                             "hid": {"label": "Hid", "enabled": True, "visible": False}},
               "physical_binding": {"surface_id": "map", "entity_id": "door-1"}},
        "n1": {"label": "Hub", "kind": "router", "visible": True, "group_id": "g0", "access": "user"},
        "n3": {"label": "Secret", "kind": "vault", "visible": False},
    },
    "links": {"l1": {"source": "n1", "target": "n2", "visible": True, "direction": "directed", "label": "Wire"},
              "l2": {"source": "n2", "target": "n3", "visible": True},
              "l0": {"source": "n2", "target": "n1", "visible": True}},
    "selection": {"node": "n1", "link": "l9", "route_step": ["n1", "n2", "l1"], "remote_function": "open",
                  "actor": "hero"},
}
VISIBLE = [["map", "door-1"]]
ACTION = {"id": "hack", "label": "Hack", "intent": "hack_node", "params": {"mode": "quiet"},
          "bindings": {"target": "node", "program": "remote_function", "who": "actor", "step": "route_step",
                       "act": "declared_action"}}
SELECTION = {"graph_revision": 3, "node": "n2", "remote_function": "open", "actor": "hero",
             "route_step": ["n2", "n1", "l0"], "declared_action": "hack"}
SURFACES = [
    {"id": "map", "renderer": "tactical_grid", "data": {"entities": [{"id": "door-1", "active": True},
                                                                       {"id": "door-2", "active": False}]}},
    {"id": "net", "renderer": "network_graph", "data": {"nodes": [
        {"id": "n2", "physical_binding": {"surface_id": "map", "entity_id": "door-1"}},
        {"id": "n4", "physical_binding": {"surface_id": "map", "entity_id": "door-2"}},
        {"id": "n5"}]}},
    {"id": "coords", "renderer": "coordinate_map", "data": {"entities": [{"id": "door-1", "active": True,
                                                                          "network_bindings": [{"x": 1}]}]}},
]


def graph_cases():
    cases = []
    visible = frozenset(tuple(item) for item in VISIBLE)
    for edits, source in variants(GRAPH, 400):
        cases.append({"fn": "project_network_graph", "args": [{"base": "graph", "edits": edits}],
                      "outcome": outcome(lambda: graph.project_network_graph(source, visible_physical_entities=visible))})
    projection = graph.project_network_graph(GRAPH, visible_physical_entities=visible)
    for edits, inputs in variants({"action": ACTION, "selection": SELECTION}, 300):
        action, selection = inputs.get("action", {}), inputs.get("selection")
        cases.append({"fn": "resolve_network_action_parameters",
                      "args": [{"base": "resolve_inputs", "edits": edits}],
                      "outcome": outcome(lambda: graph.resolve_network_action_parameters(
                          action, selection, projection, controlled_actor_ids=frozenset({"hero"})))})
    for edits, surfaces in variants(SURFACES, 250):
        cases.append({"fn": "visible_physical_entity_ids", "args": [{"base": "surfaces", "edits": edits}],
                      "outcome": outcome(lambda: graph.visible_physical_entity_ids(surfaces), sorted)})
        cases.append({"fn": "bind_physical_network_surfaces", "args": [{"base": "surfaces", "edits": edits}],
                      "outcome": outcome(lambda: graph.bind_physical_network_surfaces(surfaces),
                                         lambda value: [plain(item) for item in value])})
    return cases


def main() -> None:
    plan = json.loads((ROOT / PLAN).read_text(encoding="utf-8"))
    world, expect = world_cases(plan)
    cases = requirements_cases() + world + migration_cases() + graph_cases()
    bases = {"envelope": ENVELOPE, "data": DATA, "world_inputs": {"party": PARTY, "names": NAMES, "reference": REFERENCE},
             "state": STATE, "weapons": WEAPONS, "graph": GRAPH, "resolve_inputs": {"action": ACTION, "selection": SELECTION},
             "surfaces": SURFACES}
    vectors = {"python": sys.version.split()[0], "bases": bases, "plan_expectation": expect, "visible": VISIBLE,
               "graph_projection": plain(graph.project_network_graph(
                   GRAPH, visible_physical_entities=frozenset(tuple(item) for item in VISIBLE))),
               "cases": cases}
    OUTPUT.write_text(json.dumps(vectors, ensure_ascii=True, separators=(",", ":")) + "\n", encoding="utf-8",
                      newline="\n")
    kinds = {}
    for case in cases:
        key = (case["fn"], case["outcome"][0])
        kinds[key] = kinds.get(key, 0) + 1
    print(len(cases), "cases")
    for key in sorted(kinds):
        print(" ", key, kinds[key])


if __name__ == "__main__":
    main()
