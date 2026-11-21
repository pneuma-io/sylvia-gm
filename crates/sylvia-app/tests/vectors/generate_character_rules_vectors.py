"""Generate reference vectors for sylvia_app::character_rules (application/character_rules.py).

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_character_rules_vectors.py

Two groups of cases:
- `validate`: `validate_character_rules` on the shipped SRD Lite player-character program
  (read by the Rust test from the package record), random leaf edits of it, edits of each
  admission rule, and small programs that reach each rejection, the size limit and the runtime
  validator. Cases marked `row` meet a declared limit of docs/rust-port/differences.md.
- `campaign`: `campaign_character_state` on every shipped Campaign Plan (read by the Rust test
  from the repository) and on edits of their starting mechanics.
Long strings are stored as `{"$repeat": [text, n]}`. A success records the canonical hash and
every object's key order, since Python's result is re-read from canonical (sorted) bytes.
This script is reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import copy
import glob
import hashlib
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.application.character_rules import (  # noqa: E402
    campaign_character_state,
    validate_character_rules,
)
from sylvia_gm.domain.package_library import canonical_json_bytes  # noqa: E402

OUTPUT = Path(__file__).with_name("character_rules_vectors.json")
SRD = "rulesets/srd-lite/package-record.json"
NAMESPACE = "sylvia.srd-lite.bg3"
NEWLINE = chr(10)
rng = random.Random(20261122)
JUNK = [None, True, 0, 2, 1.5, -1, "", "x", "_x", "definitions.t", [], ["x"], {}, {"k": 1}, [{"set": {}}]]


def expand(value):
    if isinstance(value, dict):
        if set(value) == {"$repeat"}:
            text, count = value["$repeat"]
            return text * count
        return {key: expand(item) for key, item in value.items()}
    if isinstance(value, list):
        return [expand(item) for item in value]
    return value


def apply(document, edits):
    document = copy.deepcopy(document)
    for op, path, value in edits:
        parent = document
        for part in path[:-1]:
            parent = parent[part]
        if op == "delete":
            del parent[path[-1]]
        else:
            parent[path[-1]] = expand(value)
    return document


def leaves(value, path=()):
    yield path, value
    if isinstance(value, dict):
        for key, item in value.items():
            yield from leaves(item, (*path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from leaves(item, (*path, index))


def key_order(value) -> list:
    """Every object's keys, depth first in iteration order."""
    order = []
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            order.append(list(item))
            pending.extend(reversed(list(item.values())))
        elif isinstance(item, list):
            pending.extend(reversed(item))
    return order


def outcome(call):
    try:
        value = call()
    except Exception as error:  # every class is behaviour
        diagnostics = getattr(error, "diagnostics", None) or ()
        return [type(error).__name__, str(error), [item.as_payload() for item in diagnostics],
                getattr(error, "code", None)]
    if value is None:
        return ["ok", None]
    order = hashlib.sha256(canonical_json_bytes(key_order(value))).hexdigest()
    return ["ok", hashlib.sha256(canonical_json_bytes(value)).hexdigest(), order]


def effect(handler="set", **body):
    return {handler: body or {"field": "_x", "value": 1}}


def validate_cases(base) -> list:
    cases = []

    def add(spec, row=None):
        if spec is None:
            program = None
        elif "base" in spec:
            program = apply(base, spec.get("edits", []))
        else:
            program = expand(spec["program"])
        case = {"program": spec, "outcome": outcome(lambda: validate_character_rules(program))}
        if row:
            case["row"] = row
        cases.append(case)

    add(None)
    add({"base": True})
    paths = [(path, value) for path, value in leaves(base) if path]
    for _ in range(320):
        path, value = rng.choice(paths)
        if isinstance(value, dict) and value and rng.random() < 0.3:
            edits = [["delete", [*path, rng.choice(list(value))], None]]
        else:
            edits = [["set", list(path), rng.choice(JUNK)]]
        add({"base": True, "edits": edits})
    for key in ("description", "definitions", "effects"):
        add({"base": True, "edits": [["delete", [key], None]]})
    for edits in (
        [["set", ["extra"], 1]], [["set", ["description"], ""]], [["set", ["description"], "a"]],
        [["set", ["description"], {"$repeat": ["a", 2000]}]], [["set", ["description"], {"$repeat": ["a", 2001]}]],
        [["set", ["description"], {"$repeat": [chr(0x1f600), 2000]}]], [["set", ["description"], 5]],
        [["set", ["definitions"], []]], [["set", ["effects"], []]], [["set", ["effects"], {}]],
        [["set", ["definitions", "big"], {"$repeat": ["a", 1100000]}]],
        [["set", ["definitions", "big"], {"$repeat": ["a", 900000]}]],
    ):
        add({"base": True, "edits": edits})
    for value in ([], "x", 5, {}):
        add({"program": value})

    def program(*effects, definitions=None):
        return {"program": {"description": "Synthetic.", "definitions": definitions or {"t": {"a": 1}},
                            "effects": list(effects)}}

    deep = effect()
    for _ in range(33):
        deep = {"condition": "true", "on_true": [deep]}
    shallow = effect()
    for _ in range(32):
        shallow = {"condition": "true", "on_true": [shallow]}
    synthetic = [
        program(effect()), program(effect(field="x", value=1)), program(effect(field="_", value=1)),
        program(effect(field="_ok9", value=1)), program(effect(field="_é", value=1)),
        program(effect(field=5, value=1)), program({"set": "x"}), program({"set": {"field": "_x"}}),
        program(effect("list_push", list="_items", value=1)), program(effect("list_push", list="items", value=1)),
        program(effect("require", condition="true", error="No.")), program(effect("require", nothing=1)),
        program({"for_each": {"in": "participant.items", "as": "_item", "do": [effect()]}}),
        program({"for_each": {"in": "participant.items", "as": "_item", "do": effect()}}),
        program({"for_each": {"in": "participant.items", "as": "item", "do": []}}),
        program({"for_each": {"in": "participant.items", "as": "_item"}}),
        program({"table_lookup": {"table": "definitions.t", "key": "'a'"}, "store_as": "_found"}),
        program({"table_lookup": {"table": "tables.t", "key": "'a'"}, "store_as": "_found"}),
        program({"table_lookup": {"table": 5, "key": "'a'"}, "store_as": "_found"}),
        program({"table_lookup": {"table": "definitions.t", "key": "'a'"}}),
        program({"table_lookup": {"table": "definitions.t", "key": "'a'"}, "store_as": "found"}),
        program({"condition": "true"}), program({"condition": "true", "on_true": [effect()], "on_false": [effect()]}),
        program({"condition": "true", "on_true": effect()}), program({"condition": 1}),
        program({"condition": "true", "set": {"field": "_x", "value": 1}}),
        program({"condition": "true", "set": {"field": "_x", "value": 1}, "require": {"condition": "true"}}),
        program({"condition": 1, "set": {"field": "_x", "value": 1}}),
        program({"set": {"field": "_x", "value": 1}, "require": {"condition": "true"}}),
        program({"set": {"field": "_x", "value": 1}, "note": "x"}), program({"damage": {"amount": 1}}),
        program({}), program(5), program(effect(), "x"), program(deep), program(shallow),
        program(*[effect()] * 1024), program(*[effect()] * 1025),
        program(effect(field="_x", value="1 +")), program(effect("require", condition="1 +", error="No.")),
        program({"for_each": {"in": "participant.items", "as": "_item", "do": [effect()] * 1023}}),
        program({"for_each": {"in": "participant.items", "as": "_item", "do": [effect()] * 1024}}),
        program({"condition": "1 +", "set": {"field": "_x", "value": 1}}),
        program({"condition": "1 +", "on_true": [effect()]}),
        program({"condition": "participant.level >= 1", "on_true": [effect()], "on_false": []}),
        program({"condition": "true", "on_true": [{"condition": "(", "set": {"field": "_x", "value": 1}}]}),
        program({"condition": "true", "on_false": [{"condition": "x ==", "require": {"condition": "true"}}]}),
        program({"for_each": {"in": "participant.items", "as": "_item",
                              "do": [{"condition": "_item.", "set": {"field": "_x", "value": 1}}]}}),
        program({"condition": "", "set": {"field": "_x", "value": 1}}),
        program({"condition": "__import__('os')", "set": {"field": "_x", "value": 1}}),
    ]
    for spec in synthetic:
        add(spec)
    # Row 5: an integer beyond i64 in the effects reaches the runtime validator.
    add(program(effect(field="_x", value=18446744073709551615)), row=5)
    return cases


def campaign_cases() -> list:
    cases = []
    plans = sorted(glob.glob(str(ROOT / "campaign-plans/*/plan.json")))
    for path in plans:
        plan = json.loads(Path(path).read_text(encoding="utf-8"))
        relative = str(Path(path).relative_to(ROOT)).replace(chr(92), "/")
        variants = [[]]
        mechanics = plan.get("initial_state", {}).get("mechanics", [])
        if mechanics and len(cases) < 200:
            paths = [(p, v) for p, v in leaves(plan) if p[:2] == ("initial_state", "mechanics") and len(p) > 2]
            for _ in range(25):
                p, v = rng.choice(paths)
                if isinstance(v, dict) and v and rng.random() < 0.3:
                    variants.append([["delete", [*p, rng.choice(list(v))], None]])
                else:
                    variants.append([["set", list(p), rng.choice(JUNK + [NAMESPACE])]])
        for edits in variants:
            for namespace in (NAMESPACE, "other"):
                document = apply(plan, edits)
                cases.append({"plan": relative, "edits": edits, "namespace": namespace,
                              "outcome": campaign_outcome(document, namespace)})
    first = plans[0]
    plan = json.loads(Path(first).read_text(encoding="utf-8"))
    relative = str(Path(first).relative_to(ROOT)).replace(chr(92), "/")
    item = {"namespace": NAMESPACE, "key": "k", "value": {"b": 1, "a": [2, {"d": 1, "c": 2}]}}
    targeted = [
        [["delete", ["initial_state"], None]], [["set", ["initial_state"], []]], [["set", ["initial_state"], "x"]],
        [["set", ["initial_state"], {}]], [["set", ["initial_state", "mechanics"], {"a": 1}]],
        [["set", ["initial_state", "mechanics"], "ab"]], [["set", ["initial_state", "mechanics"], 5]],
        [["set", ["initial_state", "mechanics"], None]], [["set", ["initial_state", "mechanics"], []]],
        [["set", ["initial_state", "mechanics"], [item, {**item, "value": 2}]]],
        [["set", ["initial_state", "mechanics"], [{**item, "key": key} for key in
                                                   (1, None, True, 1.5, [1, "a"], {"a": 1}, "z", "A", "é")]]],
        [["set", ["initial_state", "mechanics"], [{"key": "k", "value": 1}]]],
        [["set", ["initial_state", "mechanics"], [{"namespace": NAMESPACE, "value": 1}]]],
        [["set", ["initial_state", "mechanics"], [{"namespace": NAMESPACE, "key": "k"}]]],
        [["set", ["initial_state", "mechanics"], [{"namespace": "other", "key": "k"}]]],
        [["set", ["initial_state", "mechanics"], ["x"]]], [["set", ["initial_state", "mechanics"], [5]]],
        [["set", ["initial_state", "mechanics"], [[1]]]],
    ]
    for edits in targeted:
        cases.append({"plan": relative, "edits": edits, "namespace": NAMESPACE,
                      "outcome": campaign_outcome(apply(plan, edits), NAMESPACE)})
    return cases


def campaign_outcome(plan, namespace):
    try:
        result = campaign_character_state(plan, namespace)
    except Exception as error:  # every class is behaviour
        return [type(error).__name__, str(error), [], None]
    return ["ok", list(result), result]


def main() -> None:
    record = json.loads((ROOT / SRD).read_text(encoding="utf-8"))
    base = record["participant_artifacts"][0]["initial_state_validation"]
    vectors = {"python": ".".join(sys.version.split()[0].split(".")[:2]), "srd": SRD,
               "validate": validate_cases(base), "campaign": campaign_cases()}
    text = json.dumps(vectors, ensure_ascii=True, separators=(",", ":")) + NEWLINE
    OUTPUT.write_text(text, encoding="utf-8", newline=NEWLINE)
    kinds = {}
    for case in vectors["validate"]:
        result = case["outcome"]
        key = "ok" if result[0] == "ok" else (result[1][:50] if result[0] == "CommandRejected" else result[0])
        kinds[key] = kinds.get(key, 0) + 1
    print(len(vectors["validate"]), "validate,", len(vectors["campaign"]), "campaign;", len(text), "bytes")
    for key in sorted(kinds):
        print(" ", kinds[key], key)


if __name__ == "__main__":
    main()
