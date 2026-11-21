"""Generate reference vectors for sylvia_app::plan_state_admission from the frozen Python.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_plan_state_vectors.py

Inputs:
- every shipped Campaign Plan that binds the SRD Ruleset, against the SRD record's own
  `state_schemas` (read by the Rust test from `rulesets/srd-lite/package-record.json`), with
  mutations of its starting mechanics;
- a synthetic Plan state with declarations whose references use `*`, `$name` bindings, `$key`,
  list indices and an uncaptured binding, with mutations.
Outcomes are "ok" or the exception class, message and diagnostics.
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

from sylvia_gm.application.plan_state_admission import validate_starting_declarations  # noqa: E402
from sylvia_gm.domain.package_library import manifest_from_record  # noqa: E402
from sylvia_gm.domain.starting_state import StateReference, StateSchema  # noqa: E402

OUTPUT = Path(__file__).with_name("plan_state_vectors.json")
SRD = "rulesets/srd-lite/package-record.json"
rng = random.Random(20261128)
JUNK = [None, True, 0, 1, 1.0, -1, "", "x", "loc1", "i1", "a1", [], ["x"], {}, {"k": 1}, [0], [5]]


def outcome(call):
    try:
        call()
    except Exception as error:  # every class is behaviour
        diagnostics = getattr(error, "diagnostics", None) or ()
        return [type(error).__name__, str(error), [item.as_payload() for item in diagnostics]]
    return ["ok"]


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


def variants(document, count, prefix):
    paths = [(path, value) for path, value in leaves(document) if path[:len(prefix)] == prefix and len(path) > len(prefix)]
    yield [], document
    for _ in range(count):
        path, value = rng.choice(paths)
        if isinstance(value, dict) and value and rng.random() < 0.3:
            edits = [["delete", [*path, rng.choice(list(value))], None]]
        elif isinstance(value, list) and value and rng.random() < 0.2:
            edits = [["delete", [*path, len(value) - 1], None]]
        else:
            edits = [["set", list(path), rng.choice(JUNK)]]
        try:
            yield edits, apply(document, edits)
        except (KeyError, IndexError, TypeError):
            continue


NAMESPACE = "pneuma.synthetic"
SYNTHETIC_PLAN = {"initial_state": {"mechanics": [
    {"namespace": NAMESPACE, "key": "world", "value": {
        "actors": {"a1": {"location": "loc1", "items": ["i1", "i2"]}, "a2": {"location": "loc2", "items": []}},
        "slots": [{"ref": 0}, {"ref": 1}, {"ref": None}],
    }},
    {"namespace": NAMESPACE, "key": "map", "value": {
        "locations": {"loc1": {"name": "Hall"}, "loc2": {"name": "Yard"}},
        "items": [{"id": "i1"}, {"id": "i2"}],
        "per_actor": {"a1": ["i1", "i2"], "a2": []},
    }},
    {"namespace": "other.package", "key": "ignored", "value": 1},
    "not a mapping",
    {"namespace": NAMESPACE, "key": "no_value"},
]}}
OPEN = {"type": "object", "additionalProperties": True}
SYNTHETIC_DECLARATIONS = [
    {"key": "world", "schema": OPEN, "required": True, "references": [
        [["actors", "*", "location"], "map", ["locations", "*", "$key"]],
        [["actors", "$actor", "items", "*"], "map", ["per_actor", "$actor", "*"]],
        [["slots", "*", "ref"], "map", ["items", "*", "$key"]],
    ]},
    {"key": "map", "schema": OPEN, "required": True, "references": []},
    {"key": "optional", "schema": OPEN, "required": False, "references": []},
]
EXTRA_DECLARATIONS = {
    "uncaptured": [{"key": "world", "schema": OPEN, "required": True,
                    "references": [[["actors", "*", "location"], "map", ["per_actor", "$who", "*"]]]}],
    "missing_target": [{"key": "world", "schema": OPEN, "required": True,
                        "references": [[["actors", "*", "location"], "absent", ["*"]]]}],
    "required_missing": [{"key": "absent", "schema": OPEN, "required": True, "references": []}],
    "typed": [{"key": "world", "schema": {"type": "object", "required": ["actors"],
                                          "properties": {"actors": {"type": "object"}, "slots": {"type": "array"}},
                                          "additionalProperties": False}, "required": True, "references": []}],
}


def declarations_of(specs):
    return [StateSchema(item["key"], item["schema"], tuple(StateReference(tuple(src), target_key, tuple(target))
                                                          for src, target_key, target in item["references"]),
                        item["required"]) for item in specs]


def main() -> None:
    cases = []
    srd = manifest_from_record(json.loads((ROOT / SRD).read_text(encoding="utf-8")), format_version=4)
    for path in sorted(glob.glob(str(ROOT / "campaign-plans/*/plan.json"))):
        plan = json.loads(Path(path).read_text(encoding="utf-8"))
        if (plan["bindings"].get("ruleset") or {}).get("id") != srd.package_id:
            continue
        relative = str(Path(path).relative_to(ROOT)).replace("\\", "/")
        count = 60 if "riichi" not in relative and len(cases) < 400 else 0
        for edits, document in variants(plan, count, ("initial_state",)):
            cases.append({"plan": relative, "edits": edits, "declarations": "srd",
                          "outcome": outcome(lambda: validate_starting_declarations(
                              document, srd.package_id, srd.version, srd.state_schemas))})
    for name, specs in [("synthetic", SYNTHETIC_DECLARATIONS), *EXTRA_DECLARATIONS.items()]:
        declarations = declarations_of(specs)
        for edits, document in variants(SYNTHETIC_PLAN, 250 if name == "synthetic" else 40, ("initial_state",)):
            cases.append({"plan": None, "edits": edits, "declarations": name,
                          "outcome": outcome(lambda: validate_starting_declarations(
                              document, NAMESPACE, "1.0.0", declarations))})
    wide = copy.deepcopy(SYNTHETIC_PLAN)
    wide["initial_state"]["mechanics"][0]["value"]["slots"] = [{"ref": 0}] * 8193
    cases.append({"plan": None, "edits": [["set", ["initial_state", "mechanics", 0, "value", "slots"],
                                           {"$repeat": [{"ref": 0}, 8193]}]], "declarations": "synthetic",
                  "outcome": outcome(lambda: validate_starting_declarations(
                      wide, NAMESPACE, "1.0.0", declarations_of(SYNTHETIC_DECLARATIONS)))})
    vectors = {"python": sys.version.split()[0], "srd": SRD, "namespace": NAMESPACE, "synthetic_plan": SYNTHETIC_PLAN,
               "declarations": {"synthetic": SYNTHETIC_DECLARATIONS, **EXTRA_DECLARATIONS}, "cases": cases}
    OUTPUT.write_text(json.dumps(vectors, ensure_ascii=True, separators=(",", ":")) + "\n", encoding="utf-8",
                      newline="\n")
    kinds = {}
    for case in cases:
        key = case["outcome"][0] if case["outcome"][0] == "ok" else case["outcome"][1][:60]
        kinds[key] = kinds.get(key, 0) + 1
    print(len(cases), "cases")
    for key in sorted(kinds):
        print(" ", kinds[key], key)


if __name__ == "__main__":
    main()
