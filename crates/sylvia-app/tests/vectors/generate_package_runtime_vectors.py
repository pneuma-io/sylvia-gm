"""Generate validate_package_runtime vectors for sylvia_app::package_runtime from the frozen Python.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_package_runtime_vectors.py

(`.venv/bin/python` on Linux.) Deterministic and checked in. Reference tooling for the port,
retired with the Python source.

`shipped` records the three shipped package runtimes (SRD Lite, Riichi Mahjong, the
signal-and-static fixture) against their own manifests' tool declarations. `cases` are runtime
texts, each checked against one of the declaration sets in `declarations`:

- the fixture with each manifest declaration changed (dropped, duplicated, renamed, its input
  schema's type, closure, properties, `required` and every compared field altered);
- loader edges (duplicate and non-text keys, merge keys, bad UTF-8, BOM, nesting, member and
  node limits, recursive aliases, non-JSON YAML values);
- descriptor, `meta`, parameter declarations, the `RulesetFile` model (every field type with
  values pydantic converts or refuses), cross-references, trigger links, the static call graph,
  player controls, activity labels and result cards;
- a seeded mutation corpus of the fixture runtime.

`coercion` records pydantic's own answer for each value under each field type the model uses,
so the Rust conversions are checked one value at a time. `models` records
`RulesetFile.model_validate` and `validate()` directly for a seeded corpus of configuration
documents, because the shared content schema refuses most of them before the model is reached.
`controls` records every field of `parse_player_controls(value)` for a seeded corpus, because a
policy's availability shows through `validate_package_runtime` only when its preflight names a
missing tool (which the player-control cases also do).

Each result is `{"ok": true}` or `{"raise": class, "message": str, "diagnostics": [payload]}`.

Two `set`s in the Python follow string hashing, and the generator pins each to the order Rust
uses (`differences.md`): a tool's static call targets iterate sorted, and `jsonschema`'s
schema-valued `additionalProperties` visits extra members in the instance's order (row 6, as
lane 1's content generator pins it). Nothing else is changed, and every `set` the generator
itself iterates is sorted first.

A case whose YAML holds an integer outside the signed 64-bit range is marked `wide_int`:
`sylvia_base::pyyaml` refuses such an integer (row 5), so Rust reports the runtime YAML as
invalid where Python may accept it.
"""

from __future__ import annotations

import copy
import json
import os
import random
import sys
import zipfile
from pathlib import Path
from typing import Any

import jsonschema._keywords
import yaml
from pydantic import BaseModel, Field, ValidationError

import sylvia_gm.application.package_runtime as pr
from sylvia_gm.application.package_runtime import PackageRuntimeError, validate_package_runtime
from sylvia_gm.application.player_actions import parse_player_controls
from sylvia_gm.domain.authority import owned_payload, thaw_json_value
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.ruleset.schema import RulesetFile
from sylvia_gm.runtime._runtime import RulesetRuntime

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
OUTPUT = HERE / "package_runtime_vectors.json"
FIXTURE = "tests/fixtures/rulesets/signal-and-static"
SRD = "rulesets/srd-lite"
RIICHI = "rulesets/riichi-mahjong/riichi-mahjong-0.1.1.sylvia-gm-package"

os.environ.pop("SYLVIA_RUNTIME_CACHE_DIR", None)


class _SortedSet(set):
    """A `set` that iterates in sorted order where its items allow it."""

    def __iter__(self):
        items = list(super().__iter__())
        try:
            return iter(sorted(items))
        except TypeError:
            return iter(items)


pr.set = _SortedSet  # the module-global `set` its static call graph uses
jsonschema._keywords.set = lambda iterable: list(dict.fromkeys(iterable))  # row 6


def wide_int(runtime_bytes: bytes) -> bool:
    """Whether the YAML holds an integer `sylvia_base::pyyaml` refuses (outside i64)."""
    try:
        value = yaml.load(runtime_bytes.decode("utf-8"), Loader=yaml.CSafeLoader)
    except Exception:  # noqa: BLE001 - unloadable text has no integers to compare
        return False
    stack, seen = [value], set()
    while stack:
        item = stack.pop()
        if isinstance(item, bool):
            continue
        if isinstance(item, int) and not -(2**63) <= item < 2**63:
            return True
        if isinstance(item, (dict, list)) and id(item) not in seen:
            seen.add(id(item))
            stack.extend(item.values() if isinstance(item, dict) else item)
            if isinstance(item, dict):
                stack.extend(item.keys())
    return False


def declaration_json(declaration: ToolDeclaration) -> dict:
    return {"name": declaration.name, "capability": declaration.capability,
            "input_schema": thaw_json_value(declaration.input_schema),
            "turn_statuses": list(declaration.turn_statuses), "writers": list(declaration.writers)}


def declaration(name: str, schema: dict, capability: str = "x.y") -> ToolDeclaration:
    return ToolDeclaration(name=name, capability=capability, input_schema=owned_payload(schema),
                           state_predicates=(), turn_statuses=("open",), writers=("dm",))


def outcome(package_id: str, declarations, runtime_bytes: bytes) -> dict:
    pr._clear_runtime_cache()
    try:
        validate_package_runtime(package_id=package_id, declarations=declarations, runtime_bytes=runtime_bytes)
    except PackageRuntimeError as error:
        return {"raise": "PackageRuntimeError", "message": str(error),
                "diagnostics": [item.as_payload() for item in error.diagnostics]}
    except Exception as error:  # noqa: BLE001 - every exception class is behaviour here
        return {"raise": type(error).__name__, "message": str(error), "diagnostics": []}
    return {"ok": True}


def dump(document) -> str:
    return yaml.dump(document, Dumper=yaml.CSafeDumper, sort_keys=False, allow_unicode=True)


# ── pydantic's conversions, value by value ────────────────────────────────────────────────

COERCION_VALUES: list[Any] = [
    None, True, False, 0, 1, 2, -1, 0.0, -0.0, 1.0, 1.5, 3.0, 1e15, 9.2e18, -9.2e18, 2.0**63, -(2.0**63), 1e19,
    1e300, 2**63, 2**64 - 1, "", "a", "1", " 1 ", "1.0", "1.00", "1.5", "1.", ".5", "0", "-0", "+1", "01", "1_000",
    "1__0", "_1", "1_", "+_1", "1_0.0", "1_0.0_0", "1e2", "1e", "1E2", "2e0", "0x10", "inf", "-inf", "infinity",
    "nan", " nan ", "1 ", " 1", "　1", "\x851", "\x1c1", "1\x00", "１", "٣",
    "99999999999999999999", "9" * 25 + ".0", "true", "True", "TRUE", "tRuE", "false", "FALSE", "yes", "Yes", "no",
    "on", "oN", "off", "t", "T", "f", "y", "n", "N", "on ", " true", "true\n", [], ["a"], [1], ["a", 1], {},
    {"a": 1},
]


class _Str(BaseModel):
    v: str


class _Bool(BaseModel):
    v: bool


class _Int(BaseModel):
    v: int


class _Number(BaseModel):
    v: int | float | None = None


class _StrList(BaseModel):
    v: list[str] = Field(default_factory=list)


def coercion() -> dict:
    table = {}
    for name, model in (("str", _Str), ("bool", _Bool), ("int", _Int), ("number", _Number), ("str_list", _StrList)):
        row = []
        for value in COERCION_VALUES:
            try:
                got = model.model_validate({"v": value}).v
                # Floats can be inf or nan, which JSON cannot hold, so "number" records acceptance only.
                row.append({"ok": True} if name == "number" else {"ok": True, "value": str(got) if name == "int" else got})
            except ValidationError:
                row.append({"ok": False})
        table[name] = row
    return {"values": COERCION_VALUES, "fields": table}


MODEL_BASE = {
    "meta": {"id": "ok-1", "name": "N", "version": "1", "author": "A", "license": "L", "license_url": None},
    "features": {"skills_enabled": True, "custom": 1},
    "abilities": {"str": {"name": "Strength", "active": True, "default_min": 3, "default_max": 18,
                          "used_for": ["attack"], "save_label": None},
                  "dex": {"name": "Dexterity", "active": False}},
    "skills": [{"name": "Athletics", "ability": "str", "trained_only": False}, {"name": "Stealth", "ability": "dex"}],
    "modes": {"combat": {"turn_system": {"type": "round", "label": None, "order": "initiative", "actors": [
        {"entity": None, "timing": "during", "resources": {"move": {"refresh": "turn_start", "count": 30,
                                                                    "unit": "feet"}}}]}, "extra": 1}},
    "magic": {}, "equipment": {"a": 1}, "character_creation": {}, "progression": {},
    "conditions": [{"name": "Prone", "description": "d", "mechanical_effect": "m", "stackable": False,
                    "max_stacks": None, "levels": [{"level": 1, "effect": "e"}], "cleared_by": ["long_rest"]}],
    "tools": {}, "procedures": None, "definitions": None, "definition_row_schemas": {}, "narrative": None,
}
MODEL_VALUES = COERCION_VALUES[::3] + [{"name": "X"}, [{"name": "X"}], {"turn_system": {"type": "t"}},
                                       {"refresh": "r"}, {"level": 1, "effect": "e"}]


def table_cases(text: str) -> list[dict]:
    """`RulesetRuntime(None, data).permanently_refused_tool_names` for first effects that do and do
    not refuse from the start; validation does not read it, execution will."""
    base = yaml.safe_load(text)
    tool = next(iter(base["tools"]))
    cases = []
    for condition in (False, "false", True, "true", 0, "0", "False", None, [], "false "):
        document = copy.deepcopy(base)
        document["tools"][tool]["effects"] = [{"require": {"condition": condition}}] + document["tools"][tool]["effects"]
        cases.append(document)
    for first in ({"require": "false"}, {"require": {}}, {"set": {"field": "state.x", "value": "1"}}):
        document = copy.deepcopy(base)
        document["tools"][tool]["effects"] = [first]
        cases.append(document)
    document = copy.deepcopy(base)
    document["tools"][tool]["effects"] = []
    cases.append(document)
    return [{"document": document, "refused": sorted(RulesetRuntime(None, document).permanently_refused_tool_names)}
            for document in cases]


def controls_record(value) -> dict:
    """Every field of `parse_player_controls(value)`, tuples as lists."""
    policy = parse_player_controls(value)
    return {field: thaw_json_value(list(item) if isinstance(item, tuple) else item)
            for field, item in vars(policy).items()}


def controls_cases(rng: random.Random, count: int) -> list[dict]:
    query = {"store_as": "_q", "spatial_query": {"scene": "state.map", "operation": "path", "query": {"a": 1}}}
    full = {
        "confirmation": "required", "response": "automatic", "preview": {"kind": "x"}, "actor_parameter": "actor",
        "checks": [{"condition": "a > 1", "reason": "Too far"}], "queries": [query],
        "calculations": [{"set": {"field": "_c", "value": "1"}, "condition": "true"}],
        "map_preview": {"path": "_q"}, "preflight": ["tool_a"],
    }
    values = [None, {}, [], "x", 1, full, {**full, "map_preview": None},
              {"preflight": ["t"], "preflight_path": {"scene": "state.map", "route": "_r", "cost": "_c"}}]
    odd = [None, True, 0, 1, "", "x", "_x", "state.x", "none", "required", "automatic", [], {}, ["a"], {"k": 1},
           "a" * 129, "_" + "a" * 64]

    def paths(node, prefix=()):
        yield prefix
        if isinstance(node, dict):
            for key, item in node.items():
                yield from paths(item, (*prefix, key))
        elif isinstance(node, list):
            for index, item in enumerate(node):
                yield from paths(item, (*prefix, index))

    seen = set()
    while len(values) < count:
        value = copy.deepcopy(full)
        for _ in range(rng.choice((1, 1, 2))):
            candidates = [p for p in paths(value) if p]
            target = rng.choice(candidates)
            parent = value
            try:
                for step in target[:-1]:
                    parent = parent[step]
                if rng.random() < 0.3:
                    del parent[target[-1]]
                else:
                    parent[target[-1]] = copy.deepcopy(rng.choice(odd))
            except (KeyError, IndexError, TypeError):
                continue
        key = json.dumps(value, sort_keys=True)
        if key not in seen:
            seen.add(key)
            values.append(value)
    return [{"value": value, "result": controls_record(value)} for value in values]


def model_outcome(document) -> str:
    try:
        model = RulesetFile.model_validate(document)
    except ValidationError:
        return "invalid"
    return "cross_reference" if any(issue.severity == "error" for issue in model.validate()) else "ok"


def model_cases(rng: random.Random, count: int) -> list[dict]:
    def paths(node, prefix=()):
        yield prefix
        if isinstance(node, dict):
            for key, value in node.items():
                yield from paths(value, (*prefix, key))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                yield from paths(value, (*prefix, index))

    documents = [MODEL_BASE, {}, {"meta": {"id": "Bad", "name": "N", "version": "1", "author": "A", "license": "L"}},
                 {**MODEL_BASE, "meta": {**MODEL_BASE["meta"], "id": "ok\n"}},
                 {**MODEL_BASE, "meta": {**MODEL_BASE["meta"], "id": "ok-"}},
                 {**MODEL_BASE, "meta": {**MODEL_BASE["meta"], "id": "a--b"}},
                 {**MODEL_BASE, "skills": [{"name": "A", "ability": "missing"}]},
                 {**MODEL_BASE, "skills": [{"name": "Stra\u00dfe"}, {"name": "STRASSE"}]},
                 {**MODEL_BASE, "skills": [{"name": "\u0130"}, {"name": "i\u0307"}]},
                 {**MODEL_BASE, "conditions": MODEL_BASE["conditions"] * 2}]
    # Each numeric bound at its edge and one past it.
    for minimum, maximum in ((1, 100), (0, 100), (1, 101), (99, 100), (100, 100), (None, 100), (None, 101),
                             (1, None), (0, None), ("1", "100"), (True, 100), (1.0, 100.0)):
        ability = {"name": "S", "default_min": minimum, "default_max": maximum}
        documents.append({**MODEL_BASE, "abilities": {"str": ability}, "skills": []})
    for level in (1, 0, -1, True, False, "1", 1.0, 1.5):
        condition = {**MODEL_BASE["conditions"][0], "levels": [{"level": level, "effect": "e"}]}
        documents.append({**MODEL_BASE, "conditions": [condition]})
    seen = set()
    while len(documents) < count:
        document = copy.deepcopy(MODEL_BASE)
        for _ in range(rng.choice((1, 1, 2))):
            candidates = [path for path in paths(document) if path]
            path = rng.choice(candidates)
            parent = document
            try:
                for step in path[:-1]:
                    parent = parent[step]
                if rng.random() < 0.25:
                    del parent[path[-1]]
                else:
                    parent[path[-1]] = copy.deepcopy(rng.choice(MODEL_VALUES))
            except (KeyError, IndexError, TypeError):
                continue
        key = json.dumps(document, sort_keys=True)
        if key not in seen:
            seen.add(key)
            documents.append(document)
    return [{"document": document, "result": model_outcome(document)} for document in documents]


# ── The fixture and its edits ─────────────────────────────────────────────────────────────

def record_tools(record: dict) -> list[ToolDeclaration]:
    """A package record's tool declarations, as the Python tests build them."""
    return [ToolDeclaration(name=tool["name"], capability=tool["capability"],
                            input_schema=owned_payload(tool["input_schema"]), state_predicates=(),
                            turn_statuses=tuple(tool["turn_statuses"]), writers=tuple(tool["writers"]))
            for tool in record["tools"]]


def fixture() -> tuple[str, str, list[ToolDeclaration]]:
    record = json.loads((ROOT / FIXTURE / "package-record.json").read_text(encoding="utf-8"))
    text = (ROOT / FIXTURE / "runtime/ruleset.yaml").read_text(encoding="utf-8")
    return record["id"], text, record_tools(record)


def declaration_sets(base: list[ToolDeclaration]) -> list[list[ToolDeclaration]]:
    sets = [base, [], base[:1], base + base[:1]]
    first = base[0]
    schema = thaw_json_value(first.input_schema)
    sets.append([declaration("not_a_runtime_tool", schema)] + base[1:])

    def with_schema(changed: dict) -> list[ToolDeclaration]:
        return [declaration(first.name, changed, first.capability)] + base[1:]

    for key, value in (("type", "array"), ("additionalProperties", True), ("additionalProperties", 0),
                       ("properties", []), ("required", "skill"), ("required", []), ("required", ["skill"]),
                       ("required", ["pressure", "skill", "skill"]), ("required", [["skill"]])):
        changed = copy.deepcopy(schema)
        changed[key] = value
        sets.append(with_schema(changed))
    for key in ("type", "additionalProperties", "properties", "required"):
        changed = copy.deepcopy(schema)
        changed.pop(key, None)
        sets.append(with_schema(changed))
    names = sorted(schema["properties"])
    changed = copy.deepcopy(schema)
    changed["properties"]["extra"] = {"type": "string"}
    sets.append(with_schema(changed))
    changed = copy.deepcopy(schema)
    del changed["properties"][names[0]]
    sets.append(with_schema(changed))
    for name in names:
        for field, value in (("type", "number"), ("enum", ["x"]), ("items", {"type": "string"}), ("minimum", 0),
                             ("minimum", 0.0), ("maximum", 9), ("minItems", 1), ("maxItems", 2), ("uniqueItems", True),
                             ("x-sylvia-gm-reference", "entity"), ("description", "ignored")):
            changed = copy.deepcopy(schema)
            changed["properties"][name][field] = value
            sets.append(with_schema(changed))
        for field in list(schema["properties"][name]):
            changed = copy.deepcopy(schema)
            del changed["properties"][name][field]
            sets.append(with_schema(changed))
        changed = copy.deepcopy(schema)
        changed["properties"][name] = "string"
        sets.append(with_schema(changed))
    return sets


def edited(document: dict, path: tuple, value=None, delete=False) -> dict:
    result = copy.deepcopy(document)
    node = result
    for step in path[:-1]:
        node = node[step]
    if delete:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return result


def targeted(package_id: str, text: str, declared: str) -> list[str | bytes]:
    base = yaml.safe_load(text)
    tool = next(iter(base["tools"]))
    texts: list[str | bytes] = []
    # Loader edges.
    texts += [
        "", "[]", "7", "null", "a: [", b"\xff\xfe", b"sylvia_gm_runtime: {format: 1}\n\xc3\x28",
        "﻿" + text, text + "\n" + "skills: []\n", "a: 1\na: 2\n", "{a: 1, a: 2}", "? [x]\n: 1\n", "1: a\n",
        "base: &b {x: 1}\nother:\n  <<: *b\n  x: 2\n", "base: &b {x: 1}\nother:\n  <<: *b\n  <<: *b\n",
        "a: &a [*a]\n", "a: &a {b: *a}\n", "d: 2024-01-02\n", "b: !!binary aGk=\n", "s: !!set {x}\n", "f: .inf\n",
        "f: .nan\n", "n: !!int x\n", "t: !!timestamp 2024-13-01\n", "x: !!python/name:os.system\n",
        "x: " + "[" * 65 + "]" * 65 + "\n", "x: " + "[" * 64 + "]" * 64 + "\n",
        "x: {" + ", ".join(f"k{i}: {i}" for i in range(25_001)) + "}\n",
        "a: &a [1, 1, 1, 1, 1, 1, 1, 1, 1, 1]\nb: &b [*a, *a, *a, *a, *a, *a, *a, *a, *a, *a]\n"
        "c: &c [*b, *b, *b, *b, *b, *b, *b, *b, *b, *b]\nd: &d [*c, *c, *c, *c, *c, *c, *c, *c, *c, *c]\n"
        "e: &e [*d, *d, *d, *d, *d, *d, *d, *d, *d, *d]\nf: [*e, *e, *e, *e, *e, *e]\n",
        "x: !!bool maybe\n", "x: !!str [a]\n",
    ]
    # Descriptor and identity.
    for value in (None, {}, {"format": 1}, {"format": 1, "state_namespace": package_id, "extra": 1},
                  {"format": 1.0, "state_namespace": package_id}, {"format": True, "state_namespace": package_id},
                  {"format": "1", "state_namespace": package_id}, {"format": 2, "state_namespace": package_id},
                  {"format": 1, "state_namespace": package_id + "x"}, {"format": 1, "state_namespace": 1}):
        texts.append(dump(edited(base, ("sylvia_gm_runtime",), value)))
    texts.append(dump(edited(base, ("sylvia_gm_runtime",), delete=True)))
    texts.append(dump(edited(base, ("meta",), {"id": "x"})))
    # Parameter declarations.
    for value in ([], {"Bad": {"name": "x"}}, {"p": {"name": "x", "type": "bogus"}},
                  {"p": {"name": "pressure", "type": "integer"}}):
        texts.append(dump(edited(base, ("parameter_declarations",), value)))
    # The RulesetFile model, one field at a time.
    for value in COERCION_VALUES[:40]:
        texts.append(dump(edited(base, ("abilities",), {"str": {"name": "Strength", "active": value}})))
        texts.append(dump(edited(base, ("abilities",), {"str": {"name": "Strength", "default_min": value}})))
        texts.append(dump(edited(base, ("abilities",), {"str": {"name": "Strength", "default_max": value}})))
        texts.append(dump(edited(base, ("conditions",), [{"name": "Hurt", "description": "d", "mechanical_effect": "m",
                                                          "levels": [{"level": value, "effect": "e"}]}])))
        texts.append(dump(edited(base, ("modes",), {"combat": {"turn_system": {"type": "round", "actors": [
            {"timing": "during", "resources": {"move": {"refresh": "turn_start", "count": value}}}]}}})))
        texts.append(dump(edited(base, ("features",), {"skills_enabled": value, "custom_flag": value})))
        texts.append(dump(edited(base, ("skills",), [{"name": value}])))
    for minimum, maximum in ((1, 2), (2, 2), (3, 2), ("5", 100), (1, 101), (0, 5), (None, 1), ("x", 5)):
        texts.append(dump(edited(base, ("abilities",), {"str": {"name": "S", "default_min": minimum,
                                                                "default_max": maximum}})))
    for section, value in (("abilities", []), ("skills", {}), ("modes", []), ("magic", []), ("conditions", {}),
                           ("tools", None), ("procedures", None), ("definitions", []), ("narrative", 1),
                           ("definition_row_schemas", None), ("modes", {"m": {}}), ("modes", {"m": {"turn_system": {}}}),
                           ("skills", [{"name": "A", "ability": "str"}]),
                           ("skills", [{"name": "Intrusion"}, {"name": "intrusion"}]),
                           ("skills", [{"name": "İx"}, {"name": "i̇x"}]),
                           ("conditions", [{"name": "Hurt", "description": "d", "mechanical_effect": "m"},
                                           {"name": "HURT", "description": "d", "mechanical_effect": "m",
                                            "cleared_by": ["long_rest", "nonsense"]}]),
                           ("meta", None)):
        texts.append(dump(edited(base, (section,), value)))
    texts.append(dump(edited(edited(base, ("abilities",), {"str": {"name": "S", "active": False}}), ("skills",),
                             [{"name": "A", "ability": "str"}])))
    # Executable layer, trigger links and the static call graph.
    effects = base["tools"][tool]["effects"]
    for extra in ([{"trigger": "on_missing"}], [{"trigger": {"name": "on_missing"}}], [{"trigger": {"trigger": ""}}],
                  [{"trigger": 7}], [{"call": "nowhere"}], [{"call": {"call": "bad-name"}}], [{"call": {"name": tool}}],
                  [{"condition": "true", "on_true": [{"call": "nowhere"}]}],
                  [{"for_each": {"list": "x", "as": "_i", "do": [{"trigger": "on_missing"}]}}]):
        texts.append(dump(edited(base, ("tools", tool, "effects"), effects + extra)))
    names = list(base["tools"])
    a, b = names[0], names[1]
    texts.append(dump(edited(edited(base, ("tools", a, "effects"), base["tools"][a]["effects"] + [{"call": b}]),
                             ("tools", b, "effects"), base["tools"][b]["effects"] + [{"call": a}])))
    cycle = edited(base, ("tools", a, "effects"), base["tools"][a]["effects"] + [{"call": b}, {"call": names[-1]}])
    cycle = edited(cycle, ("tools", names[-1], "effects"), base["tools"][names[-1]]["effects"] + [{"call": a}])
    texts.append(dump(edited(cycle, ("tools", b, "effects"), base["tools"][b]["effects"] + [{"call": a}])))
    # A cycle between two targets of a third tool: the walk order decides which one is named.
    c = names[-1]
    pair = edited(base, ("tools", a, "effects"), base["tools"][a]["effects"] + [{"call": c}, {"call": b}])
    pair = edited(pair, ("tools", b, "effects"), base["tools"][b]["effects"] + [{"call": c}])
    texts.append(dump(edited(pair, ("tools", c, "effects"), base["tools"][c]["effects"] + [{"call": b}])))
    # A body naming both keys: `trigger` reads `name` before `trigger`, `call` reads `call` before `name`.
    procedure = edited(base, ("procedures",), {"p": {"trigger": "on_turn_start", "steps": []}})
    for body in ({"name": "on_turn_start", "trigger": "on_missing"}, {"name": "on_missing", "trigger": "on_turn_start"}):
        texts.append(dump(edited(procedure, ("tools", tool, "effects"), effects + [{"trigger": body}])))
    for body in ({"call": b, "name": "nowhere"}, {"call": "nowhere", "name": b}):
        texts.append(dump(edited(base, ("tools", tool, "effects"), effects + [{"call": body}])))
    for steps in ([{"call": "nowhere"}], [{"call": "nowhere"}, {"call": "bad-name"}], "abc", [1],
                  [{"trigger": "on_turn_start"}]):
        texts.append(dump(edited(base, ("procedures",), {"p": {"trigger": "on_turn_start", "steps": steps}})))
    # Player controls, activity labels and result cards on a declared tool: only declared tools'
    # metadata is checked.
    tool = declared
    metadata = ("tools", tool, "metadata")
    controls = [
        None, "x", {"bogus": 1}, {"confirmation": "required", "response": "automatic"}, {"confirmation": None},
        {"preflight": [tool]}, {"preflight": ["missing_tool"]}, {"preflight": "x"}, {"preflight": [tool] * 5},
        {"preflight": [tool], "preflight_path": {"scene": "state.map", "route": "_r", "cost": "_c"}},
        {"preflight_path": {"scene": "state.map", "route": "_r", "cost": "_c"}},
        {"checks": [{"condition": "true", "reason": "r"}]}, {"checks": [{"condition": " ", "reason": "r"}]},
        {"queries": [{"store_as": "_q", "spatial_query": {"scene": "state.map", "operation": "path", "query": {}}}],
         "map_preview": {"path": "_q"}},
        {"queries": [], "map_preview": {"path": "_q"}}, {"calculations": [{"set": {"field": "_x", "value": "1"}}]},
        {"actor_parameter": ""}, {"actor_parameter": "a" * 129}, {"preview": []},
    ]
    for value in controls:
        texts.append(dump(edited(base, metadata, {**base["tools"][tool].get("metadata", {}),
                                                  "player_controls": value})))
    for label in ("Roll", " ", "a" * 80, "a" * 81, {"path": "parameters.skill"}, {"path": "other.x"},
                  {"path": "a..b"}, {"path": ".".join(["parameters"] * 17)}, {"path": ""}, {"path": 1}, None, 1):
        texts.append(dump(edited(base, metadata, {**base["tools"][tool].get("metadata", {}), "activity_label": label})))
    cards = [
        {"title": "T"}, {}, {"title": {"path": "other.x"}},
        {"title": "T", "summary": [{"label": "L", "value": {"path": "context.total"}}]},
        {"title": "T", "summary": [{"label": "L", "value": {"path": "bad.total"}}]},
        {"title": "T", "actions": [{"label": "Go", "tool": "bad-tool"}]},
        {"title": "T", "actions": [{"label": "Go", "tool": "undeclared_tool"}]},
        {"title": "T", "actions": [{"label": "Go", "tool": tool, "parameters": {"skill": {"path": "x.y"}}}]},
        {"title": "T", "equation": {"total": {"path": "nope"}}}, {"bogus": 1},
    ]
    # The same cards in the shared schema's valid shape (`version: 1`, actions with `parameters`),
    # so the procedural checks after it are reached; the unversioned originals fail the shape.
    shaped = [{"version": 1, **card, **({"actions": [{"parameters": {}, **action} for action in card["actions"]]}
                                        if "actions" in card else {})} for card in cards]
    for card in cards + shaped:
        texts.append(dump(edited(base, metadata, {**base["tools"][tool].get("metadata", {}), "result_card": card})))
    texts += bounds(base, tool)
    return texts


def bounds(base: dict, tool: str) -> list[str]:
    """Every length limit at its bound and one past it; "é" where characters and bytes differ."""
    metadata = ("tools", tool, "metadata")

    def with_metadata(**fields) -> str:
        return dump(edited(base, metadata, {**base["tools"][tool].get("metadata", {}), **fields}))

    def with_controls(**fields) -> str:
        """Controls whose preflight names a missing tool: refused exactly when they are available."""
        return with_metadata(player_controls={"preflight": ["missing_tool"], **fields})

    def query(name: str, operation: str = "path") -> dict:
        return {"store_as": name, "spatial_query": {"scene": "state.map", "operation": operation, "query": {}}}

    def calculation(name: str, value: str = "1", condition: str | None = None) -> dict:
        item: dict = {"set": {"field": name, "value": value}}
        if condition is not None:
            item["condition"] = condition
        return item

    texts = []
    for n in (8, 9):
        texts.append(with_controls(**{"queries": [query(f"_q{i}") for i in range(n)]}))
    for n in (24, 25):
        texts.append(with_controls(**{"calculations": [calculation(f"_c{i}") for i in range(n)]}))
    for value in ("a" * 500, "a" * 501, "\u00e9" * 500, "\u00e9" * 501, ""):
        texts.append(with_controls(**{"calculations": [calculation("_c", value=value)]}))
        texts.append(with_controls(**{"calculations": [calculation("_c", condition=value)]}))
    for n in (32, 33):
        texts.append(with_controls(**{"checks": [{"condition": "true", "reason": "r"}] * n}))
    for condition in ("a" * 4096, "a" * 4097, "\u00e9" * 4096, "\u00e9" * 4097):
        texts.append(with_controls(**{"checks": [{"condition": condition, "reason": "r"}]}))
    for reason in ("a" * 256, "a" * 257, "\u00e9" * 256, "\u00e9" * 257):
        texts.append(with_controls(**{"checks": [{"condition": "true", "reason": reason}]}))
    for name in ("a" * 128, "a" * 129, "\u00e9" * 128):
        texts.append(with_controls(**{"actor_parameter": name}))
    for name in ("_a" + "b" * 63, "_a" + "b" * 64, "_1", "a_", "_a\n"):
        texts.append(with_controls(**{"queries": [query(name)]}))
    texts.append(with_controls(**{"queries": [query("_q"), query("_q")]}))
    texts.append(with_controls(**{"queries": [query("_q")], "calculations": [calculation("_q")]}))
    for name in ("a" * 128, "a" * 129, "1a"):
        texts.append(with_controls(**{"preflight": [name]}))
    for preflight in ([tool] * 3 + ["missing_tool"], [tool] * 4 + ["missing_tool"]):
        texts.append(with_controls(preflight=preflight))
    for scene in ("state." + "a" * 128, "state." + "a" * 129, "state.a.b", "state.", "other.a"):
        texts.append(with_controls(**{"preflight_path": {
            "scene": scene, "route": "_r", "cost": "_c"}}))
    texts.append(with_controls(**{"queries": [query("_q", "ray")], "map_preview": {"path": "_q"}}))
    for label in ("\u00e9" * 80, "\u00e9" * 81, {"path": "parameters." + "a" * 245}, {"path": "parameters." + "a" * 246}):
        texts.append(with_metadata(activity_label=label))
    for parts in (16, 17):
        texts.append(with_metadata(result_card={"version": 1, "title": {
            "path": ".".join(["parameters"] + ["a"] * (parts - 1))}}))
    # Eight actions with four findings each: only the first sixteen are kept.
    texts.append(with_metadata(result_card={"version": 1, "title": "T", "actions": [
        {"label": "Go", "tool": f"bad-{index}", "parameters": {f"p{n}": {"path": "bad.x"} for n in range(3)}}
        for index in range(8)]}))
    texts.append(with_metadata(result_card={"version": 1, "title": "T", "actions": [
        {"label": "Go", "tool": tool, "parameters": {"p": {"path": "bad.x"}}} for _ in range(4)]}))
    # Parameter declarations are copied into each tool that uses them, two levels deeper and once
    # per use, so the expanded runtime can pass the graph check and still fail `_validate_shape`.
    for levels in (57, 58, 59, 60):
        nested: object = 1
        for _ in range(levels):
            nested = [nested]
        document = copy.deepcopy(base)
        document["parameter_declarations"] = {"deep": {"name": "deep", "type": "array", "items": {"type": "integer"},
                                                       "default": nested}}
        document["tools"][tool]["parameters"] = list(document["tools"][tool].get("parameters", [])) + ["deep"]
        texts.append(dump(document))
    for uses in (40, 60):
        document = copy.deepcopy(base)
        document["parameter_declarations"] = {"wide": {"name": "wide", "type": "array", "items": {"type": "integer"},
                                                       "default": [[1] * 10 for _ in range(1_000)]}}
        for index in range(uses):
            document["tools"][f"copy_{index}"] = {"parameters": ["wide"],
                                                   "effects": copy.deepcopy(base["tools"][tool]["effects"])}
        texts.append(dump(document))
    # A merge can give a mapping more members than its own node holds, past the graph check.
    first = "\n".join(f"  k{i}: {i}" for i in range(13_000))
    second = "\n".join(f"  j{i}: {i}" for i in range(12_001))
    texts.append(f"a: &a\n{first}\nb:\n  <<: *a\n{second}\n")
    texts.append(f"a: &a\n{first}\nb:\n  <<: *a\n" + "\n".join(f"  j{i}: {i}" for i in range(12_000)) + "\n")
    return texts


ODD = [None, True, False, 0, 1, -1, 2.5, "", "x", "on_x", "state.x", [], {}, [1], {"k": 1}, ["a", "a"]]


def mutations(base: dict, rng: random.Random, count: int) -> list[str]:
    def paths(node, prefix=()):
        yield prefix
        if isinstance(node, dict):
            for key, value in node.items():
                yield from paths(value, (*prefix, key))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                yield from paths(value, (*prefix, index))

    all_paths = [path for path in paths(base) if path]
    texts, seen = [], set()
    while len(texts) < count:
        document = copy.deepcopy(base)
        for _ in range(rng.choice((1, 1, 2, 3))):
            candidates = [path for path in paths(document) if path]
            path = rng.choice(candidates or all_paths)
            try:
                parent = document
                for step in path[:-1]:
                    parent = parent[step]
                roll = rng.random()
                if roll < 0.3:
                    del parent[path[-1]]
                elif roll < 0.85:
                    parent[path[-1]] = copy.deepcopy(rng.choice(ODD))
                elif isinstance(parent, dict):
                    parent[rng.choice(["bogus", "on_true", "do", "store_as", "condition", "name"])] = rng.choice(ODD)
            except (KeyError, IndexError, TypeError):
                continue
        text = dump(document)
        if text not in seen:
            seen.add(text)
            texts.append(text)
    return texts


def main() -> int:
    package_id, text, base_declarations = fixture()
    sets = declaration_sets(base_declarations)
    cases = []
    base_bytes = text.encode("utf-8")
    for index in range(len(sets)):
        cases.append({"runtime": text, "declarations": index,
                      "result": outcome(package_id, sets[index], base_bytes)})
    for item in targeted(package_id, text, base_declarations[0].name) + mutations(yaml.safe_load(text), random.Random(2024), 1500):
        runtime_bytes = item if isinstance(item, bytes) else item.encode("utf-8")
        entry = {"runtime_hex": runtime_bytes.hex()} if isinstance(item, bytes) else {"runtime": item}
        case = {**entry, "declarations": 0, "result": outcome(package_id, sets[0], runtime_bytes)}
        if wide_int(runtime_bytes):
            case["wide_int"] = True
        cases.append(case)

    shipped = []
    record = json.loads((ROOT / SRD / "package-record.json").read_text(encoding="utf-8"))
    tools = record_tools(record)
    shipped.append({"source": f"{SRD}/runtime/ruleset.yaml", "package_id": record["id"],
                    "declarations": [declaration_json(item) for item in tools],
                    "result": outcome(record["id"], tools, (ROOT / SRD / "runtime/ruleset.yaml").read_bytes())})
    archive = zipfile.ZipFile(ROOT / RIICHI)
    riichi = json.loads(archive.read("manifest.json"))["packages"][0]
    riichi_tools = record_tools(riichi)
    riichi_text = archive.read(f"packages/{riichi['id']}/runtime/ruleset.yaml")
    shipped.append({"runtime": riichi_text.decode("utf-8"), "package_id": riichi["id"],
                    "declarations": [declaration_json(item) for item in riichi_tools],
                    "result": outcome(riichi["id"], riichi_tools, riichi_text)})
    shipped.append({"source": f"{FIXTURE}/runtime/ruleset.yaml", "package_id": package_id,
                    "declarations": [declaration_json(item) for item in base_declarations],
                    "result": outcome(package_id, base_declarations, base_bytes)})

    document = {
        "python": sys.version.split()[0],
        "package_id": package_id,
        "declarations": [[declaration_json(item) for item in group] for group in sets],
        "coercion": coercion(),
        "models": model_cases(random.Random(77), 1500),
        "controls": controls_cases(random.Random(55), 800),
        "tables": table_cases(text),
        "shipped": shipped,
        "cases": cases,
    }
    OUTPUT.write_bytes((json.dumps(document, ensure_ascii=True, allow_nan=False) + "\n").encode("ascii"))
    kinds: dict[str, int] = {}
    for case in cases:
        key = case["result"].get("raise", "ok") + ": " + case["result"].get("message", "")[:48]
        kinds[key] = kinds.get(key, 0) + 1
    print(f"{len(cases)} cases, shipped {[c['result'] for c in shipped]}")
    for key in sorted(kinds, key=lambda k: -kinds[k]):
        print(f"  {kinds[key]:5}  {key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
