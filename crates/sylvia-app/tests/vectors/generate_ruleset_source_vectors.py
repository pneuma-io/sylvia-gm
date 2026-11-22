"""Generate reference vectors for sylvia_app::ruleset_source (builder/_ruleset_source.py).

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_ruleset_source_vectors.py

Sources come in four kinds (the Rust test rebuilds each one's bytes the same way):
- `riichi`: the shipped `rulesets/riichi-mahjong/ruleset-source.yaml`, read from the repository;
- `riichi_edits`: that source loaded, edited, and written back as `json.dumps` text (JSON is
  YAML), so each edit reaches a different compile step;
- `small_edits`: the same for a small source stored here, which compiles, and random and
  targeted edits of it;
- `text`: YAML written here: anchors and aliases that reach the dumped runtime, merge keys,
  duplicate and non-text keys, YAML-only values, nesting and expansion limits, encodings.
A success records the record's canonical hash and key order, each payload's path, hash and
length in order, and the required engine features; `show` cases also keep the dumped
`runtime/ruleset.yaml` text. A failure records the class, text, diagnostics and code.
This script is reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.builder._ruleset_source import compile_ruleset_source  # noqa: E402
from sylvia_gm.domain.package_library import canonical_json_bytes  # noqa: E402

OUTPUT = Path(__file__).with_name("ruleset_source_vectors.json")
RIICHI = "rulesets/riichi-mahjong/ruleset-source.yaml"
NEWLINE = chr(10)
rng = random.Random(20261122)
JUNK = [None, True, 0, 1, 1.5, "", "x", "{amount}", "_x", [], ["x"], {}, {"k": 1}, "engine_family", "state",
        "choice", "enum", "entity_list", "path", ["open"]]


def apply(document, edits):
    document = copy.deepcopy(document)
    for op, path, value in edits:
        parent = document
        for part in path[:-1]:
            parent = parent[part]
        if op == "delete":
            del parent[path[-1]]
        elif op == "append":
            parent[path[-1]].append(copy.deepcopy(value))
        else:
            parent[path[-1]] = copy.deepcopy(value)
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


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


RIICHI_SOURCE = yaml.safe_load((ROOT / RIICHI).read_text(encoding="utf-8"))
SMALL = {
    "format": 1, "id": "pneuma.small", "version": "1.0.0", "label": "Small", "publisher": "QA",
    "mechanic_families": {
        "roll": {
            "parameters": [{"name": "target", "type": "string", "source": "choice", "description": "Target"}],
            "pre_conditions": [], "effects": [{"set": {"field": "_x", "value": "{amount}"}}],
            "narrative": {"success": "Rolled {amount} for {label}."},
        },
        "pick": {
            "parameters": [
                {"name": "choice", "type": "enum", "source": "choice", "enum_values": ["a", "b"], "required": False},
                {"name": "who", "type": "entity_id", "source": "gm_judgment", "resolve": "participant"},
                {"name": "many", "type": "entity_id_list", "source": "choice", "resolve": "entity_list",
                 "min_items": 1, "max_items": 3, "unique_items": True},
                {"name": "level", "type": "integer", "source": "state", "from_state": "participant.level"},
                {"name": "count", "type": "integer", "source": "choice", "minimum": 0, "maximum": 9, "default": 1},
            ],
            "pre_conditions": ["{guard}"], "effects": [{"publish": {"value": "joined(x)"}}],
            "narrative": {"success": "Picked."}, "metadata": {"tier": "{tier}"},
        },
    },
    "public_actions": [
        {"name": "roll_once", "family": "roll", "description": "Roll.", "configure": {"amount": 2, "label": "Ash"},
         "capability": "pneuma.small.play", "state_predicates": [], "turn_statuses": ["open"], "writers": ["dm"],
         "reference_availability": "included", "gameplay_resolution": "engine_family", "deterministic_part": None,
         "gm_part": None},
        {"name": "pick_one", "family": "pick", "description": "Pick.",
         "configure": {"guard": "true", "tier": 1.5}, "capability": "pneuma.small.pick", "state_predicates": [],
         "turn_statuses": ["open"], "writers": ["dm"], "reference_availability": "included",
         "gameplay_resolution": "hybrid", "deterministic_part": "Rolls.", "gm_part": "Judge it."},
    ],
    "private_mechanics": [{"name": "_helper", "family": "roll", "description": "Helper.",
                           "configure": {"amount": 1, "label": True}}],
    "definitions": {"table": {"a": 1}},
    "participant_artifacts": RIICHI_SOURCE["participant_artifacts"],
    "state_schemas": RIICHI_SOURCE["state_schemas"],
    "ui_surfaces": [{"id": "board", "capability": "pneuma.small.board", "renderer": "tactical_grid", "order": 7,
                     "source": {"kind": "state", "namespace": "pneuma.small", "key": "state.board"}}],
    "procedures": {"tick": {"trigger": "on_turn_end", "steps": [{"spatial_query": {"operation": "path",
                                                                                   "query": {"moving_entity_id": "a"}}}]}},
    "model_decision_boundary": RIICHI_SOURCE["model_decision_boundary"],
    "operating_guide": "Run the small game.",
}


def source_bytes(spec) -> bytes:
    if "riichi" in spec:
        return (ROOT / RIICHI).read_bytes()
    if "riichi_edits" in spec:
        return json.dumps(apply(RIICHI_SOURCE, spec["riichi_edits"])).encode("utf-8")
    if "small_edits" in spec:
        return json.dumps(apply(SMALL, spec["small_edits"])).encode("utf-8")
    if "latin1" in spec:
        return spec["latin1"].encode("latin-1")
    return spec["text"].encode("utf-8")


def outcome(spec):
    data = source_bytes(spec)
    try:
        compiled = compile_ruleset_source(data)
    except Exception as error:  # every class is behaviour
        diagnostics = getattr(error, "diagnostics", None) or ()
        return [type(error).__name__, str(error), [item.as_payload() for item in diagnostics],
                getattr(error, "code", None)]
    draft = compiled.draft
    result = {
        "record": digest(canonical_json_bytes(draft.record)),
        "record_order": digest(canonical_json_bytes(key_order(draft.record))),
        "payloads": [[path, digest(value), len(value)] for path, value in draft.payloads.items()],
        "features": list(compiled.required_engine_features),
        "format_version": draft.format_version,
    }
    if spec.get("show"):
        result["runtime_yaml"] = draft.payloads["runtime/ruleset.yaml"].decode("utf-8")
    return ["ok", result]


def anchored_texts() -> list:
    """YAML whose aliases survive `deepcopy` into the dumped runtime, written by PyYAML itself.

    Each must fail in at most one member that `additionalProperties` visits: Python visits those
    in `set` order (differences row 6), so two failing members would order by hash seed.
    """
    texts = []
    shared = {"cost": 2, "tags": ["a", "b"]}
    document = copy.deepcopy(SMALL)
    document["definitions"] = {"left": shared, "right": shared, "list": [shared, shared]}
    texts.append(yaml.safe_dump(document, sort_keys=False))
    document = copy.deepcopy(SMALL)
    step = {"set": {"field": "_y", "value": 1}}
    document["procedures"] = {"one": {"trigger": "on_turn_end", "steps": [step, step]},
                              "two": {"trigger": "on_turn_start", "steps": [step]}}
    texts.append(yaml.safe_dump(document, sort_keys=False))
    document = copy.deepcopy(SMALL)
    effect = {"set": {"field": "_z", "value": 3}}
    document["public_actions"][0]["configure"] = {"amount": [effect, effect], "label": "x"}
    document["mechanic_families"]["roll"]["effects"] = "{amount}"
    document["mechanic_families"]["roll"]["narrative"] = {"success": "Rolled for {label}."}
    texts.append(yaml.safe_dump(document, sort_keys=False))
    document = copy.deepcopy(SMALL)
    boundary = {"x": 1}
    document["model_decision_boundary"] = {"a": boundary, "b": boundary}
    document["definitions"] = {"a": boundary}
    texts.append(yaml.safe_dump(document, sort_keys=False))
    document = copy.deepcopy(SMALL)
    items = {"type": "string"}
    document["mechanic_families"]["pick"]["parameters"][2]["items"] = {"pair": [items, items]}
    texts.append(yaml.safe_dump(document, sort_keys=False))
    return texts


def raw_texts() -> list:
    base = yaml.safe_dump(SMALL, sort_keys=False)
    deep = "definitions:" + NEWLINE + "".join("  " * i + f"  k{i}:" + NEWLINE for i in range(70)) + "  " * 71 + "v: 1"
    laughs = "a: &a [x, x, x, x, x, x, x, x, x, x]" + NEWLINE + "".join(
        f"{chr(98 + i)}: &{chr(98 + i)} [*{chr(97 + i)}, *{chr(97 + i)}, *{chr(97 + i)}, *{chr(97 + i)}, *{chr(97 + i)}, "
        f"*{chr(97 + i)}, *{chr(97 + i)}, *{chr(97 + i)}, *{chr(97 + i)}, *{chr(97 + i)}]" + NEWLINE for i in range(5))
    return [
        base, base.replace("format: 1", "format: 1" + NEWLINE + "format: 1"), base.replace("label: Small", "1: Small"),
        base.replace("label: Small", "label: Small" + NEWLINE + "<<: {extra_merge: 1}"),
        base.replace("definitions:" + NEWLINE, "definitions:" + NEWLINE + "  <<: {merged: 1}" + NEWLINE),
        base.replace("label: Small", "label: !!set {a, b}"), base.replace("label: Small", "label: 2026-11-22"),
        base.replace("label: Small", "label: !!binary aGk="), base.replace("table:", "nan: .nan" + NEWLINE + "  table:"),
        base.replace("table:", "inf: -.inf" + NEWLINE + "  table:"), base.replace("label: Small", "label: [a, b]"),
        base.replace("format: 1", "format: 1.0"), base.replace("format: 1", "format: true"),
        base.replace("format: 1", "format: 2"), base.replace("id: pneuma.small", "id: 7"),
        base.replace("version: 1.0.0", "version: '   '"), base.replace("publisher: QA", "publisher: null"),
        base.replace("operating_guide: Run the small game.", "operating_guide: '  '"),
        base.replace("operating_guide: Run the small game.", "operating_guide: 5"),
        base.replace("label: Small", "label: !!python/name:os.system"), "", "---" + NEWLINE + "...", "[]", "x", "{",
        "a: [", deep, laughs, chr(0xfeff) + base, base.replace("label: Small", "label: " + chr(0x1f600)),
        base.replace("label: Small", "label: 'tab" + chr(9) + "here'"), "a: 1" + NEWLINE + "---" + NEWLINE + "b: 2",
        base.replace("label: Small", "label: &x Small" + NEWLINE + "extra: *x"),
        base.replace("table:", "self: &s [*s]" + NEWLINE + "  table:"),
        base.replace("format: 1", "format: 123456789012345678901234567890"),
    ]


def main() -> None:
    cases = []

    def add(spec, row=None):
        case = {"source": spec, "outcome": outcome(spec)}
        if row:
            case["row"] = row
        cases.append(case)

    add({"riichi": True})
    add({"riichi_edits": []})
    add({"small_edits": [], "show": True})
    paths = [(path, value) for path, value in leaves(RIICHI_SOURCE) if path and path[0] != "mechanic_families"]
    family_paths = [(path, value) for path, value in leaves(RIICHI_SOURCE["mechanic_families"]) if len(path) <= 3]
    for _ in range(60):
        path, value = rng.choice(paths)
        add({"riichi_edits": [["set", list(path), rng.choice(JUNK)]]})
    for _ in range(30):
        path, value = rng.choice(family_paths)
        add({"riichi_edits": [["set", ["mechanic_families", *path], rng.choice(JUNK)]]})
    small_paths = [(path, value) for path, value in leaves(SMALL) if path]
    for _ in range(320):
        path, value = rng.choice(small_paths)
        if isinstance(value, dict) and value and rng.random() < 0.3:
            edits = [["delete", [*path, rng.choice(list(value))], None]]
        else:
            edits = [["set", list(path), rng.choice(JUNK)]]
        add({"small_edits": edits})
    targeted = [
        [["set", ["extra"], 1]], [["delete", ["format"], None]], [["delete", ["id"], None]],
        [["set", ["mechanic_families", "roll", "extra"], 1]], [["delete", ["mechanic_families", "roll", "effects"], None]],
        [["set", ["public_actions", 0, "family"], "missing"]], [["set", ["public_actions", 1, "name"], "roll_once"]],
        [["set", ["private_mechanics", 0, "name"], "roll_once"]], [["append", ["public_actions"], SMALL["public_actions"][0]]],
        [["set", ["public_actions", 0, "gameplay_resolution"], ["engine_family"]]],
        [["set", ["public_actions", 0, "capability"], 5], ["set", ["public_actions", 0, "gameplay_resolution"], ["x"]]],
        [["set", ["public_actions", 0, "gameplay_resolution"], "hybrid"]],
        [["set", ["public_actions", 0, "configure"], {"amount": {"nested": 1}, "label": "x"}]],
        [["set", ["public_actions", 0, "configure"], {"amount": [1], "label": "x"}]],
        [["set", ["mechanic_families", "roll", "narrative"], "{amount}"]],
        [["set", ["mechanic_families", "roll", "parameters", 0, "source"], "model"]],
        [["set", ["mechanic_families", "roll", "parameters", 0, "source"], ["choice"]]],
        [["set", ["mechanic_families", "roll", "parameters", 0, "extra"], 1]],
        [["set", ["mechanic_families", "roll", "parameters", 0, "from_state"], "x"]],
        [["delete", ["mechanic_families", "pick", "parameters", 3, "from_state"], None]],
        [["set", ["mechanic_families", "pick", "parameters", 0, "enum_values"], "ab"]],
        [["set", ["mechanic_families", "pick", "parameters", 0, "enum_values"], {"a": 1}]],
        [["set", ["mechanic_families", "pick", "parameters", 0, "enum_values"], 5]],
        [["delete", ["mechanic_families", "pick", "parameters", 0, "enum_values"], None]],
        [["set", ["mechanic_families", "pick", "parameters", 1, "resolve"], ["entity"]]],
        [["set", ["mechanic_families", "pick", "parameters", 1, "resolve"], "entity"]],
        [["set", ["mechanic_families", "pick", "parameters", 2, "type"], "string"]],
        [["set", ["mechanic_families", "pick", "parameters", 2, "items"], {"type": "integer"}]],
        [["set", ["mechanic_families", "pick", "parameters", 0, "required"], 1]],
        [["set", ["mechanic_families", "pick", "parameters", 0, "name"], "who"]],
        [["set", ["mechanic_families", "roll", "parameters"], "x"]],
        [["set", ["ui_surfaces"], None]], [["set", ["ui_surfaces", 0, "id"], 5]], [["delete", ["ui_surfaces"], None]],
        [["set", ["ui_surfaces", 0, "icon"], "x"]], [["delete", ["procedures"], None]], [["set", ["procedures"], []]],
        [["delete", ["state_schemas"], None]], [["delete", ["model_decision_boundary"], None]],
        [["set", ["operating_guide"], None]], [["set", ["panel_layout"], {"format_version": 1}]],
        [["set", ["tests"], []]], [["set", ["tests"], {"format_id": "x"}]],
        [["set", ["procedures", "tick", "steps", 0], {"shot_effect": {}, "occupies_cells": 1, "spatial_mutation": 1}]],
        [["set", ["procedures", "tick", "steps", 0], {"isolated_participant": 1, "shuffle": 1}]],
        [["set", ["procedures", "tick", "steps", 0], {"spatial_query": {"operation": "line"}}]],
        [["set", ["procedures", "tick", "steps", 0], {"value": "total(x) + ordered(y)"}]],
        [["set", ["mechanic_families", "roll", "effects"], [{"x": "partitions(a)"}]]],
        [["set", ["participant_artifacts"], []]], [["set", ["definitions"], []]],
    ]
    for edits in targeted:
        add({"small_edits": edits})
    for text in anchored_texts():
        add({"text": text, "show": True})
    for index, text in enumerate(raw_texts()):
        # Row 5: an integer beyond i64 in the YAML.
        add({"text": text}, row=5 if "123456789012345678901234567890" in text else None)
    add({"latin1": "format: 1" + NEWLINE + "label: " + chr(0xff)})
    vectors = {"python": ".".join(sys.version.split()[0].split(".")[:2]), "riichi": RIICHI, "small": SMALL,
               "cases": cases}
    OUTPUT.write_text(json.dumps(vectors, ensure_ascii=True, separators=(",", ":")) + NEWLINE, encoding="utf-8",
                      newline=NEWLINE)
    kinds = {}
    for case in cases:
        result = case["outcome"]
        key = "ok" if result[0] == "ok" else (result[2][0]["code"] if result[2] else result[0])
        kinds[key] = kinds.get(key, 0) + 1
    print(len(cases), "cases", OUTPUT.stat().st_size, "bytes")
    for key in sorted(kinds):
        print(" ", kinds[key], key)


if __name__ == "__main__":
    main()
