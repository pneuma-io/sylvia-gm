"""Generate Ruleset validation vectors for sylvia_engine::ruleset from the frozen Python reference.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-engine/tests/vectors/generate_ruleset_validation_vectors.py

(`.venv/bin/python` on Linux.) Deterministic and checked in. Reference tooling for the port,
retired with the Python source.

Each case is YAML text. Python loads it with `CSafeLoader` (libyaml, as the package runtime loads
its Ruleset; the values are those of `safe_load`) and records what
`validate_ruleset_data`, `runtime_authoring_shape_errors` and, for a case's effect list,
`_validate_effects("character_validation", effects)` return, or the exception they raise. The
Rust test loads the same text with `sylvia_base::pyyaml` and must agree.

The corpus is the shipped SRD Lite and Riichi Mahjong runtimes and the signal-and-static test
fixture, then a seeded mutation corpus built from their real tools, procedures and sections:
deleted and unknown keys, values replaced by every YAML type (null, booleans, numbers, odd
strings, lists, mappings, dates, bytes, sets, integer keys), and targeted edits of each
validated section.

Two spots of the Python follow per-process string hashing: `_suggest` takes the first of tied
suggestions in set order, and `_iter_effects` visits handler bodies in frozenset order. For
the vectors both are made to follow the fixed order Rust declares (`differences.md`): ties by
sorted `str()`, handlers in sorted order. Nothing else is changed.
"""

from __future__ import annotations

import copy
import datetime
import hashlib
import json
import random
import sys
import zipfile
from pathlib import Path

import yaml

import sylvia_gm.runtime._schema_validator as sv
from sylvia_gm.runtime._authoring_declarations import (
    COMPATIBILITY_EFFECT_HANDLERS,
    EFFECT_BODY_FIELDS,
    NEUTRAL_EFFECT_HANDLERS,
    SCALAR_EFFECT_BODIES,
)
from sylvia_gm.runtime._authoring_shape import runtime_authoring_shape_errors
from sylvia_gm.runtime._parameter_declarations import resolve_parameter_declarations

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
OUTPUT = HERE / "ruleset_validation_vectors.json"
SRD_PATH = "rulesets/srd-lite/runtime/ruleset.yaml"
FIXTURE_PATH = "tests/fixtures/rulesets/signal-and-static/runtime/ruleset.yaml"

# The fixed orders Rust declares for Python's hash-ordered iteration.
sv.VALID_EFFECT_KEYS = tuple(sorted(sv.VALID_EFFECT_KEYS))
_original_suggest = sv._suggest
sv._suggest = lambda target, options: _original_suggest(target, sorted(options, key=str))  # noqa: E731


def outcome(call) -> dict:
    try:
        return {"ok": call()}
    except Exception as error:  # noqa: BLE001 - every exception class is behaviour here
        return {"raise": type(error).__name__, "message": str(error)}


def record(text: str) -> dict:
    data = load(text)
    case = {
        "validate": outcome(lambda: sv.validate_ruleset_data(data)),
        "shape": outcome(lambda: [list(pair) for pair in runtime_authoring_shape_errors(data)]),
    }
    effects = None
    if isinstance(data, dict) and isinstance(data.get("tools"), dict):
        for tool in data["tools"].values():
            if isinstance(tool, dict) and isinstance(tool.get("effects"), list):
                effects = tool["effects"]
                break
    if effects is not None:
        case["character_effects"] = outcome(lambda: sv._validate_effects("character_validation", effects))
    if isinstance(data, dict) and "parameter_declarations" in data:
        # Key order and contents of the expansion, which validation alone does not show, as the
        # SHA-256 of its repr() to keep the vectors small.
        case["resolved"] = outcome(lambda: hashlib.sha256(
            repr(resolve_parameter_declarations(data)).encode("utf-8")).hexdigest())
    if len(text) <= TEXT_LIMIT and not cyclic(data):
        # The YAML text entry point, which parses with the pure-Python SafeLoader.
        case["validate_text"] = outcome(lambda: sv.validate_ruleset(text))
    return case


TEXT_LIMIT = 10_000


def cyclic(node, seen=()) -> bool:
    """Whether aliases make the loaded value contain itself (`differences.md` row 25)."""
    if isinstance(node, (dict, list)):
        if any(node is ancestor for ancestor in seen):
            return True
        children = [*node.keys(), *node.values()] if isinstance(node, dict) else node
        return any(cyclic(child, (*seen, node)) for child in children)
    return False


# `validate_ruleset` on text the loader refuses: syntax errors become a finding, while the
# exceptions PyYAML lets through (`ValueError` from `int()`, a bad date) propagate.
YAML_TEXT_CASES = [
    "a: [", "\t", "{a: 1", "a: b: c", "- a\nb: c", "a: !!int x", "a: !!float x", "a: !!timestamp 2024-13-45",
    "a: !!python/name:os.system x", "a: !custom 1", "a: *missing", "a: &x 1\nb: &x 2\nc: *x",
    "%YAML 1.1\n%YAML 1.1\n--- a", "--- a\n--- b", "a: '\\x00'", "a: \"\\xzz\"", "\ufeffmeta: {id: ok}",
    "", "# nothing", "null", "[]", "7", "meta: {id: ok}\n...\n", "\x85", "a: \x07",
    "? [a, b]\n: c", "{[a]: 1}", "a: !!set {x, y}", "a: !!binary aGk=", "a: !!binary '!'",
    "meta: {id: ok}\ntools: {t: {effects: [{log: x}]}}\n",
]


def boundary_cases() -> list[str]:
    """Each length limit at its bound and one past it, plus suggestions and the shape cap."""
    texts = []

    def tool_doc(effects: list) -> str:
        return dump({"meta": {"id": "ok"}, "tools": {"t": {"effects": effects}}})

    def spatial(operation: str, **query) -> str:
        fields = {name: "x" for name in sv._SPATIAL_QUERY_FIELDS[operation]}
        fields.update(query)
        body = {"scene": "state.map", "operation": operation, "query": fields}
        return tool_doc([{"spatial_query": body, "store_as": "_q"}])

    circle = {"entity_id": "a", "radius": 1, "blocking": True}
    edge = {"start": "a", "end": "b", "cost": 1}
    point = [0, 0, 0]
    for n in (64, 65):
        texts.append(spatial("circle_route", circles=[dict(circle) for _ in range(n)]))
        texts.append(spatial("sweep", exclude_entity_ids=["e"] * n))
        texts.append(spatial("placement", exclude_entity_ids=["e"] * n))
    for n in (16384, 16385):
        # One aliased row keeps the text small; PyYAML loads every alias as the same mapping.
        texts.append(spatial("circle_route", edge_costs=[edge] * n))
    for n in (1, 2, 256, 257):
        texts.append(spatial("crossed_zones", path=[list(point) for _ in range(n)]))

    def choice(prompt="Pick", options=None, label="L") -> str:
        options = options if options is not None else [{"id": "a", "label": label}, {"id": "b", "label": "B"}]
        return tool_doc([{"request_choice": {"choice_id": "c", "prompt": prompt, "options": options},
                          "store_as": "_c"}])

    for n in (1, 2, 16, 17):
        texts.append(choice(options=[{"id": f"o{i}", "label": "L"} for i in range(n)]))
    # Byte limits: "é" is two UTF-8 bytes, so a character count would differ.
    for size in (4096, 4097):
        texts.append(choice(prompt="a" * size))
    texts.append(choice(prompt="é" * 2048))
    texts.append(choice(prompt="é" * 2049))
    for size in (1024, 1025):
        texts.append(choice(label="a" * size))
    texts.append(choice(label="é" * 512))
    texts.append(choice(label="é" * 513))

    owner = {"active_actor": {"namespace": "s", "key": "a"}, "actor_entities": {"namespace": "s", "key": "e"},
             "participant_control": {"namespace": "s", "key": "p"}, "default_owner": "gm"}

    def boundary(**extra) -> str:
        return dump({"meta": {"id": "ok"}, "model_decision_boundary": {**owner, **extra}})

    def preparation(prompt: str, intent: str = "prepare") -> dict:
        return {"preparation": {"queue_field": "queue", "intent": intent, "actor_parameter": "actor",
                                "prompt": prompt}}

    # A character limit: 2000 "é" pass even though they are 4000 bytes.
    for prompt in ("", "a" * 2000, "a" * 2001, "é" * 2000, "é" * 2001):
        texts.append(boundary(**preparation(prompt)))
    texts.append(boundary(**preparation("p", intent="pré_pare")))
    texts.append(boundary(**preparation("p", intent="pre-pare")))

    def isolated(instructions="Play.", path=("a",)) -> str:
        return boundary(isolated_participant={
            "instructions": instructions, "observation_path": list(path), "actions_path": ["b"],
            "public_observation": {"namespace": "s", "key": "o"}})

    for size in (8192, 8193):
        texts.append(isolated(instructions="a" * size))
    texts.append(isolated(instructions="é" * 4096))
    texts.append(isolated(instructions="é" * 4097))
    for n in (8, 9):
        texts.append(isolated(path=["p"] * n))
    for size in (64, 65):
        texts.append(isolated(path=["a" * size]))

    # Suggestions: distance 3 is offered, 4 is not; ties go to the smaller str().
    def calls(tool_names: list, call: str) -> str:
        tools = {name: {"effects": [{"log": "x"}]} for name in tool_names}
        return dump({"meta": {"id": "ok"}, "tools": tools,
                     "procedures": {"p": {"trigger": "on_turn_start", "steps": [{"call": call}]}}})

    texts.append(calls(["abcdefgh"], "abcdexyz"))
    texts.append(calls(["abcdefgh"], "abcdwxyz"))
    texts.append(calls(["aba", "aab"], "aaa"))
    texts.append(calls(["zeta", "alpha"], "alpah"))
    for trigger in ("On_turn_start", "turn_strat", "xyzzy"):
        texts.append(dump({"meta": {"id": "ok"}, "procedures": {"p": {"trigger": trigger, "steps": []}}}))

    # `$` under re.match accepts one final newline; re.fullmatch does not.
    for name in ("ok\n", "ok\n\n", "Ok\n"):
        texts.append(dump({"meta": {"id": name}}))
        texts.append(dump({"meta": {"id": "ok"}, "tools": {name.lower(): {"effects": [{"log": "x"}]}}}))
        texts.append(dump({"meta": {"id": "ok"}, "parameter_declarations": {name.lower(): {"name": "x"}},
                           "tools": {"t": {"parameters": [name.lower()], "effects": [{"log": "x"}]}}}))

    # More than sixteen authoring-shape findings, cut to the first sixteen.
    texts.append(tool_doc([{"log": "x", f"extra_{index:02}": 1} for index in range(20)]))
    texts.append(tool_doc([{"log": "x", f"extra_{index:02}": 1} for index in range(16)]))
    return texts


def load(text: str):
    return yaml.load(text, Loader=yaml.CSafeLoader)


def dump(document) -> str:
    return yaml.dump(document, Dumper=yaml.CSafeDumper, sort_keys=False, allow_unicode=True)


ODD_VALUES = [
    None, True, False, 0, 1, -1, 7, 2.5, float("inf"), "", "x", "Bad Name", "on_Bad", "state.", "{a}",
    "a\n", "é", "_private", "state.map", "*", [], {}, [1], ["a", "a"], {"k": 1}, {1: "a"},
    datetime.date(2024, 1, 2), datetime.datetime(2024, 1, 2, 3, 4, 5), b"\x00bin", {1, 2},
]
ODD_KEYS = ["bogus", 7, "", "_x", "condition", "on_true", "do", "store_as", "type", "name"]


def paths(node, prefix=()):
    """Every (path, value) inside a nested structure, containers included."""
    yield prefix, node
    if isinstance(node, dict):
        for key, value in node.items():
            yield from paths(value, (*prefix, key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from paths(value, (*prefix, index))


def parent_of(node, path):
    for step in path[:-1]:
        node = node[step]
    return node


def mutate(node, rng: random.Random):
    node = copy.deepcopy(node)
    candidates = list(paths(node))
    for _ in range(rng.choice((1, 1, 1, 2, 3))):
        path, value = rng.choice(candidates)
        action = rng.randrange(6)
        if not path:
            if isinstance(node, dict) and action == 0:
                node[rng.choice(ODD_KEYS)] = rng.choice(ODD_VALUES)
            continue
        parent = parent_of(node, path)
        last = path[-1]
        try:
            if action in (0, 1):
                parent[last] = copy.deepcopy(rng.choice(ODD_VALUES))
            elif action == 2 and isinstance(parent, dict):
                del parent[last]
            elif action == 3 and isinstance(value, dict):
                value[rng.choice(ODD_KEYS)] = copy.deepcopy(rng.choice(ODD_VALUES))
            elif action == 4 and isinstance(parent, dict):
                parent[rng.choice(ODD_KEYS)] = parent.pop(last)
            elif action == 5 and isinstance(value, list) and value:
                value.append(copy.deepcopy(rng.choice(value)))
        except (KeyError, IndexError, TypeError):
            pass
        candidates = list(paths(node))
    return node


def referenced_declarations(tools: dict, pool: dict) -> dict:
    names = {p for tool in tools.values() if isinstance(tool, dict)
             for p in tool.get("parameters") or [] if isinstance(p, str)}
    return {name: pool[name] for name in sorted(names) if name in pool}


MAX_TOOL_TEXT = 4000
MAX_CASE_TEXT = 8000


def mutation_cases(srd: dict, riichi: dict, rng: random.Random) -> list[str]:
    """Small documents (at most 8 KB of YAML each) around one or two real tools."""
    texts: list[str] = []
    sources = [srd, riichi]
    tool_items = [(name, tool) for src in sources for name, tool in src.get("tools", {}).items()
                  if len(dump({name: tool})) <= MAX_TOOL_TEXT]
    procedure_items = [(name, proc) for src in sources for name, proc in (src.get("procedures") or {}).items()
                       if len(dump({name: proc})) <= MAX_TOOL_TEXT]
    pool = srd.get("parameter_declarations", {})
    row_schemas = srd.get("definition_row_schemas") or {}
    small_definitions = {}
    for key in row_schemas:
        table = key.split(".")[1] if key.startswith("definitions.") else None
        rows = srd.get("definitions", {}).get(table)
        if isinstance(rows, dict):
            small_definitions[table] = dict(list(rows.items())[:3])
    boundary = srd.get("model_decision_boundary") or riichi.get("model_decision_boundary")
    while len(texts) < 3200:
        index = len(texts)
        name, tool = rng.choice(tool_items)
        tools = {name: tool}
        if rng.random() < 0.3:
            other_name, other_tool = rng.choice(tool_items)
            tools[other_name] = other_tool
        document: dict = {}
        if rng.random() < 0.5:
            document["sylvia_gm_runtime"] = {"format": 1, "state_namespace": "pkg"}
        else:
            document["meta"] = {"id": rng.choice(["mutation-case", "Bad_ID", "a1-b2", "x"])}
        document["tools"] = tools
        declarations = referenced_declarations(tools, pool)
        if declarations:
            document["parameter_declarations"] = declarations
        elif rng.random() < 0.08:
            document["parameter_declarations"] = dict(list(pool.items())[:4])
        if procedure_items and rng.random() < 0.3:
            proc_name, proc = rng.choice(procedure_items)
            document["procedures"] = {proc_name: proc}
        if boundary is not None and rng.random() < 0.08:
            document["model_decision_boundary"] = boundary
        if row_schemas and rng.random() < 0.12:
            table_path = rng.choice(sorted(row_schemas))
            table = table_path.split(".")[1]
            document["definition_row_schemas"] = {table_path: row_schemas[table_path]}
            document["definitions"] = {table: small_definitions.get(table, {})}
        text = dump(mutate(document, rng) if index % 7 else document)
        if len(text) <= MAX_CASE_TEXT or ("definition_row_schemas" in document and len(text) <= 4 * MAX_CASE_TEXT):
            texts.append(text)
    return texts


TARGETED = [
    "[]\n", "null\n", "a: 1\n", "meta: {}\n", "meta: {id: Bad}\n", "meta: {id: ok}\ntools: []\n",
    "meta: {id: ok}\ntools: {1: {}}\n", "meta: {id: ok}\ntools: {Bad: {}}\n",
    "meta: {id: ok}\ntools: {t: {parameters: null, effects: null}}\ndefinition_row_schemas: {}\n",
    "meta: {id: ok}\ntools: {t: {parameters: 3}}\n", "meta: {id: ok}\nprocedures: []\n",
    "meta: {id: ok}\ntools: {t: {}}\nprocedures: {p: {trigger: on_turn_strat}}\n",
    "meta: {id: ok}\ntools: {roll_dice: {}}\nprocedures: {p: {trigger: on_turn_start, steps: [{call: roll_dcie}]}}\n",
    "meta: {id: ok}\ntools: {7: {}}\nprocedures: {p: {trigger: on_turn_start, steps: [{call: nope}]}}\n",
    "meta: {id: ok}\ndefinitions: 3\n", "meta: {id: ok}\nnarrative: 3\n", "meta: {id: ok}\nmodel_tool_availability: 3\n",
    "meta: {id: ok}\nparameter_declarations: []\n", "meta: {id: ok}\nparameter_declarations: {Bad: {name: x}}\n",
    "meta: {id: ok}\nparameter_declarations: {a: {name: x, type: [1]}}\n",
    "meta: {id: ok}\nparameter_declarations: {a: {name: x, type: bogus}}\n",
    "meta: {id: ok}\ntools: {t: {parameters: [nope]}}\n",
    "sylvia_gm_runtime: {}\nmeta: {}\nmodel_tool_availability: {}\n",
    "meta: {id: ok}\nmodel_decision_boundary: {}\nmodel_decision_scope: {}\n",
    "meta: {id: ok}\nmodel_decision_boundary: {active_actor: {namespace: a, key: b}, actor_entities: [], "
    "participant_control: {field: ' ', player_values: [a, a]}, default_owner: [x]}\n",
]


ROW_SCHEMA_CASES = [
    # Definition row schemas, rows and exact table lookups.
    "meta: {id: ok}\ndefinition_row_schemas: []\n",
    "meta: {id: ok}\ndefinition_row_schemas: {tables.x: {}}\n",
    "meta: {id: ok}\ndefinition_row_schemas: {definitions.x: 3}\n",
    "meta: {id: ok}\ndefinition_row_schemas: {definitions.x: {type: object, additionalProperties: false}}\n",
    "meta: {id: ok}\ndefinitions: {x: {a: {n: 1}, b: {n: 'two'}, c: 3}}\n"
    "definition_row_schemas: {definitions.x: {type: object, additionalProperties: false, required: [n],"
    " properties: {n: {type: integer, minimum: 0, maximum: 2}}}}\n",
    "meta: {id: ok}\ndefinitions: {x: {a: {n: 1, extra: 2}, b: {}}}\n"
    "definition_row_schemas: {definitions.x: {type: object, additionalProperties: false, required: [n],"
    " properties: {n: {type: number, enum: [1, 2]}}}}\n",
    "meta: {id: ok}\ndefinitions: {x: {a: {kind: one, v: 's'}, b: {kind: two}, c: {v: 1}, d: {kind: 3}}}\n"
    "definition_row_schemas: {definitions.x: {discriminator: kind, variants: {one: {type: object,"
    " additionalProperties: false, properties: {v: {type: string, minLength: 2}}}}}}\n",
    "meta: {id: ok}\ndefinitions: {x: {}}\ndefinition_row_schemas: {definitions.x: {discriminator: 1, variants: {},"
    " extra: 1}}\n",
    "meta: {id: ok}\ndefinitions: {x: {}}\ndefinition_row_schemas: {definitions.x: {type: string, items: {},"
    " uniqueItems: 1, enum: [], minimum: x, minLength: -1, properties: {}}}\n",
    "meta: {id: ok}\ndefinitions: {x: {}}\ndefinition_row_schemas: {definitions.x: {type: array, items: {type: bogus},"
    " uniqueItems: true}}\n",
    "meta: {id: ok}\ndefinitions: {x: {a: {l: [1, 1]}}}\ndefinition_row_schemas: {definitions.x: {type: object,"
    " additionalProperties: false, properties: {l: {type: array, uniqueItems: true, items: {type: integer}}}}}\n",
    "meta: {id: ok}\ndefinitions: {x: {a: {n: 1}}}\ndefinition_row_schemas: {definitions.x: {type: object,"
    " additionalProperties: false, properties: {n: {type: integer}}, required: [n, n]}}\n",
    "meta: {id: ok}\ndefinitions: {x: {a: {}, b: {}}}\n"
    "definition_row_schemas: {definitions.x: {type: object, additionalProperties: false}}\n"
    "tools: {t: {parameters: [{name: k, type: enum, enum_values: [a, b]}], effects: ["
    "{table_lookup: {table: definitions.x, key: k, required: true, exact_selector: true}, store_as: _r}]}}\n",
    "meta: {id: ok}\ndefinitions: {x: {a: {}, c: 3}}\n"
    "tools: {t: {parameters: [{name: k, type: enum, enum_values: [a, a]}], effects: ["
    "{table_lookup: {table: definitions.x, key: k, required: true, exact_selector: true, bogus: 1}, store_as: bad},"
    "{table_lookup: {table: definitions.y, required: 'yes'}}, {table_lookup: {table: definitions.x, required: true,"
    " default: 1}}, {table_lookup: {table: definitions.x, exact_selector: true}}, {table_lookup: {table: definitions.x,"
    " key: [1], required: true, exact_selector: true}}]}}\ndefinition_row_schemas: {}\n",
    "meta: {id: ok}\ndefinitions: {x: {a: {}}}\nprocedures: {p: {trigger: on_turn_start, steps: [{condition: 'a > 1',"
    " on_true: [{table_lookup: {table: definitions.x, key: k, required: true, exact_selector: true}}]}]}}\n"
    "definition_row_schemas: {}\n",
]


def main() -> int:
    srd_text = (ROOT / SRD_PATH).read_text(encoding="utf-8")
    riichi_text = zipfile.ZipFile(ROOT / "rulesets/riichi-mahjong/riichi-mahjong-0.1.1.sylvia-gm-package").read(
        "packages/pneuma.riichi-mahjong/runtime/ruleset.yaml").decode("utf-8")
    fixture_text = (ROOT / FIXTURE_PATH).read_text(encoding="utf-8")
    srd, riichi = load(srd_text), load(riichi_text)
    shipped = [
        {"source": SRD_PATH, **record(srd_text)},
        {"source": FIXTURE_PATH, **record(fixture_text)},
        {"text": riichi_text, **record(riichi_text)},
    ]
    rng = random.Random(9090)
    cases = [{"text": text, **record(text)} for text in TARGETED + ROW_SCHEMA_CASES + boundary_cases() + mutation_cases(srd, riichi, rng)]
    texts = [{"text": text, "validate_text": outcome(lambda text=text: sv.validate_ruleset(text))}
             for text in YAML_TEXT_CASES]
    declarations = {
        "VALID_PARAM_TYPES": sorted(sv.VALID_PARAM_TYPES),
        "VALID_EFFECT_KEYS": sorted(sv.VALID_EFFECT_KEYS),
        "VALID_TRIGGERS": sorted(sv.VALID_TRIGGERS),
        "EFFECT_BODY_FIELDS": {name: list(fields) for name, fields in EFFECT_BODY_FIELDS.items()},
        "SCALAR_EFFECT_BODIES": sorted(SCALAR_EFFECT_BODIES),
        "NEUTRAL_EFFECT_HANDLERS": list(NEUTRAL_EFFECT_HANDLERS),
        "COMPATIBILITY_EFFECT_HANDLERS": list(COMPATIBILITY_EFFECT_HANDLERS),
    }
    document = {"python": sys.version.split()[0], "declarations": declarations, "shipped": shipped,
                "texts": texts, "cases": cases}
    OUTPUT.write_bytes((json.dumps(document, ensure_ascii=True) + "\n").encode("ascii"))
    kinds = {}
    for case in cases:
        kind = "raise" if "raise" in case["validate"] else ("errors" if case["validate"]["ok"] else "valid")
        kinds[kind] = kinds.get(kind, 0) + 1
    print(f"{len(cases)} cases {kinds}, shipped {[bool(c['validate'].get('ok')) for c in shipped]} -> {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
