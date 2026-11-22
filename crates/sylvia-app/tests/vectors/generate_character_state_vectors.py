"""Generate reference vectors for sylvia_app's character rules and initial character state:
`prepare_character_rules`/`verify_character_rules`, `materialize_initial_character_state`,
`verify_initial_character_state`, `initialize_campaign_character_state` and
`verify_placed_character`.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_character_state_vectors.py

Inputs:
- SRD Lite's own player-character declaration (read by the Rust test from the package record)
  with the twelve bundled starters (`src/sylvia_gm/campaign/srd-starters.json`): each is
  materialized, verified, checked by its rules, initialized with real Plans' campaign state,
  verified as placed, and verified after tampering; then mutations of its authored inputs;
- a small synthetic declaration for edges SRD Lite does not reach (missing and null inputs,
  `rules` reads, parents, limits, arithmetic, rejections). One case is differences row 41
  (merge results in creation calculations).
Outcomes are ["ok", value] or [class, message, code, diagnostics].
This script is reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import copy
import glob
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.application.character_rules import campaign_character_state, verify_character_rules  # noqa: E402
from sylvia_gm.application.initial_character_state import (  # noqa: E402
    initialize_campaign_character_state,
    materialize_initial_character_state,
    verify_initial_character_state,
    verify_placed_character,
)
from sylvia_gm.domain.authority import thaw_json_value  # noqa: E402
from sylvia_gm.domain.package_library import ParticipantArtifactDeclaration, manifest_from_record  # noqa: E402

OUTPUT = Path(__file__).with_name("character_state_vectors.json")
SRD = "rulesets/srd-lite/package-record.json"
STARTERS = "src/sylvia_gm/campaign/srd-starters.json"
rng = random.Random(20261123)
JUNK = [None, True, 0, 1, 2.5, -1, 99, "", "x", "fighter", [], ["x"], {}, {"k": 1}]


def outcome(call):
    try:
        value = call()
    except Exception as error:  # every class is behaviour
        diagnostics = getattr(error, "diagnostics", None) or ()
        return [type(error).__name__, str(error), getattr(error, "code", None),
                [item.as_payload() for item in diagnostics]]
    return ["ok", thaw_json_value(value)]


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
        parent = document
        for part in path[:-1]:
            parent = parent[part]
        if op == "delete":
            del parent[path[-1]]
        else:
            parent[path[-1]] = copy.deepcopy(value)
    return document


def mutations(document, count):
    paths = [(path, value) for path, value in leaves(document) if path]
    found = []
    for _ in range(count):
        path, value = rng.choice(paths)
        if isinstance(value, dict) and value and rng.random() < 0.3:
            edits = [["delete", [*path, rng.choice(list(value))], None]]
        elif isinstance(value, list) and value and rng.random() < 0.2:
            edits = [["delete", [*path, len(value) - 1], None]]
        else:
            edits = [["set", list(path), rng.choice(JUNK)]]
        try:
            apply(document, edits)
        except (KeyError, IndexError, TypeError):
            continue
        found.append(edits)
    return found


SYNTHETIC = {
    "kind": "player_character",
    "schema_id": "pneuma.synthetic.character.v1",
    "schema_sha256": "0" * 64,
    "schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"],
               "additionalProperties": False},
    "entity_state_schema": {
        "type": "object",
        "properties": {
            "base": {"type": "integer"},
            "bonus": {"type": ["integer", "null"]},
            "total": {"type": "integer"},
            "label": {"type": "string"},
            "flag": {"type": "boolean"},
            "nested": {"type": "object", "properties": {"value": {"type": "integer"}}},
            "blob": {"type": "string"},
            "ratio": {"type": "number"},
            "merged": {"type": ["integer", "null"]},
            "checked": {"type": ["integer", "null"]},
        },
        "required": ["base", "total"],
        "additionalProperties": False,
    },
    "initial_state_calculations": [
        {"set": {"field": "total", "value": "state.base + participant.level"}},
        {"set": {"field": "label", "value": "participant.name"}, "condition": "participant.level > 1"},
        {"set": {"field": "flag", "value": "state.bonus == null"}},
        {"set": {"field": "nested.value", "value": "state.base * 2"}},
        {"set": {"field": "checked", "value": "rules._double"}, "condition": "rules._double > 0"},
    ],
    "initial_state_validation": {
        "description": "Require a positive base and record its double.",
        "definitions": {"limits": {"max": 20}},
        "effects": [
            {"require": {"condition": "state.base > 0", "message": "Base must be positive."}},
            {"set": {"field": "_double", "value": "state.base * 2"}},
            {"table_lookup": {"table": "definitions.limits", "key": "'max'"}, "store_as": "_max"},
            {"require": {"condition": "state.base <= _max", "message": "Base exceeds the limit."}},
        ],
    },
}
SYNTHETIC_EDITS = {
    "plain": [],
    "missing_input": [["set", ["initial_state_calculations", 0, "set", "value"], "state.base + participant.missing"]],
    "deep_missing": [["set", ["initial_state_calculations", 0, "set", "value"], "participant.stats.str + 1"]],
    "null_input": [["set", ["initial_state_calculations", 0, "set", "value"], "participant.nothing == null"],
                   ["set", ["entity_state_schema", "properties", "total", "type"], "boolean"]],
    "divide": [["set", ["initial_state_calculations", 0, "set", "value"], "state.base / 0"]],
    "infinite": [["set", ["initial_state_calculations", 0, "set", "value"], "1e308 * 10"],
                 ["set", ["entity_state_schema", "properties", "total", "type"], "number"]],
    "type_error": [["set", ["initial_state_calculations", 0, "set", "value"], "participant.name + 1"]],
    "big": [["set", ["initial_state_calculations", 0, "set", "field"], "blob"],
            ["set", ["initial_state_calculations", 0, "set", "value"], "participant.long + participant.long"]],
    "parent_not_object": [["set", ["initial_state_calculations", 3, "set", "field"], "nested.value"]],
    "bad_expression": [["set", ["initial_state_calculations", 0, "set", "value"], "state.base +"]],
    "bad_root": [["set", ["initial_state_calculations", 0, "set", "value"], "definitions.limits"]],
    "rules_in_value": [["set", ["initial_state_calculations", 4], {"set": {"field": "checked", "value": "rules._double + rules._max"}}]],
    "rules_missing": [["set", ["initial_state_calculations", 4], {"set": {"field": "checked", "value": "rules._absent"}}]],
    "no_validation": [["set", ["initial_state_validation"], None]],
    "merge": [["set", ["initial_state_calculations", 4], {"set": {"field": "merged", "value": "merge(participant.stats, participant.extra).absent"}}]],
}
SYNTHETIC_INPUTS = [
    ({"name": "Ada", "level": 3, "stats": {"str": 12}, "extra": {"dex": 1}, "long": "x" * 140000}, {"base": 4}),
    ({"name": "Ada", "level": 1, "stats": {"str": 12}, "extra": {"dex": 1}, "long": "x"}, {"base": 4, "bonus": None}),
    ({"name": "Ada", "level": 2, "stats": {}, "extra": {}, "long": "x"}, {"base": 0}),
    ({"name": "Ada", "level": 2, "stats": {"str": 1}, "extra": {}, "long": "x"}, {"base": 40}),
    ({"name": "Ada", "level": 2, "stats": {"str": 1}, "extra": {}, "long": "x"}, {"base": 5, "nested": 3}),
    ({"name": "Ada", "level": 2}, {"base": 5, "total": 7}),
    ({"name": "Ada", "level": "two"}, {"base": 5}),
]


def declaration_of(spec):
    return ParticipantArtifactDeclaration(
        kind=spec["kind"], schema_id=spec["schema_id"], schema_sha256=spec["schema_sha256"], schema=spec["schema"],
        entity_state_schema=spec["entity_state_schema"],
        initial_state_calculations=tuple(spec["initial_state_calculations"]),
        initial_state_validation=spec["initial_state_validation"])


def run_set(declaration, participant, authored, campaign_states, extra=True):
    schema = thaw_json_value(declaration.entity_state_schema)
    calculations = [thaw_json_value(item) for item in declaration.initial_state_calculations]
    validation = thaw_json_value(declaration.initial_state_validation)
    result = {"materialize": outcome(lambda: materialize_initial_character_state(
        participant, authored, entity_state_schema=schema, initial_state_calculations=calculations,
        initial_state_validation=validation))}
    if not extra or result["materialize"][0] != "ok":
        return result
    complete = result["materialize"][1]
    result["verify"] = outcome(lambda: verify_initial_character_state(
        participant, complete, entity_state_schema=schema, initial_state_calculations=calculations,
        initial_state_validation=validation))
    result["rules"] = outcome(lambda: verify_character_rules(participant, complete, validation))
    result["initialize"] = [outcome(lambda: initialize_campaign_character_state(
        participant, complete, declaration, campaign_state)) for campaign_state in campaign_states]
    placed = {**complete, "token_id": "token-1", "position": [1, 2]}
    result["placed"] = outcome(lambda: verify_placed_character(participant, placed, declaration))
    tampered = []
    for field in [item["set"]["field"] for item in calculations[:3]]:
        top = field.split(".")[0]
        if top in complete:
            changed = copy.deepcopy(complete)
            changed[top] = 999 if not isinstance(changed[top], int) or changed[top] != 999 else 998
            tampered.append(["set", top, outcome(lambda: verify_initial_character_state(
                participant, changed, entity_state_schema=schema, initial_state_calculations=calculations,
                initial_state_validation=validation))])
            removed = {key: value for key, value in complete.items() if key != top}
            tampered.append(["delete", top, outcome(lambda: verify_initial_character_state(
                participant, removed, entity_state_schema=schema, initial_state_calculations=calculations,
                initial_state_validation=validation))])
    result["tampered"] = tampered
    return result


def main() -> None:
    srd = manifest_from_record(json.loads((ROOT / SRD).read_text(encoding="utf-8")), format_version=4)
    declaration = next(item for item in srd.participant_artifacts if item.kind == "player_character")
    campaign_states = []
    for path in sorted(glob.glob(str(ROOT / "campaign-plans/*/plan.json"))):
        plan = json.loads(Path(path).read_text(encoding="utf-8"))
        if (plan["bindings"].get("ruleset") or {}).get("id") == srd.package_id:
            state = campaign_character_state(plan, srd.package_id)
            if state not in campaign_states:
                campaign_states.append(state)
    campaign_states = campaign_states[:3] + [{}]
    starters = json.loads((ROOT / STARTERS).read_text(encoding="utf-8"))
    cases = []
    for row in starters:
        document = {"participant": row["participant"], "entity_state": row["entity_state"]}
        cases.append({"set": "srd", "starter": row["id"], "edits": [],
                      **run_set(declaration, row["participant"], row["entity_state"], campaign_states)})
        for edits in mutations(document, 22):
            varied = apply(document, edits)
            cases.append({"set": "srd", "starter": row["id"], "edits": edits,
                          **run_set(declaration, varied["participant"], varied["entity_state"], [], extra=False)})
    for name, edits in SYNTHETIC_EDITS.items():
        spec = apply(SYNTHETIC, edits)
        synthetic = declaration_of(spec)
        for index, (participant, authored) in enumerate(SYNTHETIC_INPUTS):
            case = {"set": "synthetic", "variant": name, "input": index,
                    **run_set(synthetic, participant, authored, [{"advancement": {"x": 1}}, {}])}
            if name == "merge":
                case["row"] = "NEXT"
            cases.append(case)
    vectors = {"python": ".".join(sys.version.split()[0].split(".")[:2]), "srd": SRD, "starters": STARTERS,
               "campaign_states": campaign_states, "synthetic": SYNTHETIC, "synthetic_edits": SYNTHETIC_EDITS,
               "synthetic_inputs": [[participant, authored] for participant, authored in SYNTHETIC_INPUTS],
               "cases": cases}
    text = json.dumps(vectors, ensure_ascii=True, separators=(",", ":")) + chr(10)
    OUTPUT.write_text(text, encoding="utf-8", newline=chr(10))
    kinds = {}
    for case in cases:
        for key in ("materialize", "verify", "rules", "placed"):
            if key in case:
                result = case[key]
                label = key + ": " + ("ok" if result[0] == "ok" else f"{result[0]} {result[2]}")
                kinds[label] = kinds.get(label, 0) + 1
    print(len(cases), "cases,", len(text), "bytes")
    for key in sorted(kinds):
        print(" ", kinds[key], ascii(key))


if __name__ == "__main__":
    main()
