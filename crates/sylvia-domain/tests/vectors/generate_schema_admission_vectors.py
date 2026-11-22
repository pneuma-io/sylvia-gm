"""Generate reference vectors for sylvia_domain::schema_admission from the frozen Python.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-domain/tests/vectors/generate_schema_admission_vectors.py

Schemas: every tool parameter, participant artifact and starting-state schema of the shipped
packages (read from the Python owner's startup recording), mutations of them, and synthetic
schemas for nullable types, allOf, if/then and contains. For each it records Python's
validate_json_schema outcome, and for admitted schemas the outcomes of validate_parameters,
validate_json_value and validate_references on generated values. An outcome is ["ok"] or the
exception class, code, message, diagnostics and MechanicsRejected fields.
The output is deterministic (fixed seed) and checked in. This script is reference tooling for
the port and is retired with the Python source.
"""

from __future__ import annotations

import copy
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.application.schema_admission import (  # noqa: E402
    validate_json_schema,
    validate_json_value,
    validate_parameters,
    validate_references,
)
from sylvia_gm.domain.authority import thaw_json_value  # noqa: E402
from sylvia_gm.domain.errors import _NAMED_INPUT, MechanicsRejected, RulesetPreconditionRejected  # noqa: E402

OUTPUT = Path(__file__).with_name("schema_admission_vectors.json")
RECORDING = ROOT / "crates/sylvia-compare/recordings/windows/startup.recording.json"
rng = random.Random(20261121)


def outcome(call):
    try:
        call()
        return ["ok"]
    except Exception as error:  # the class, code and fields are behaviour
        result = [type(error).__name__, getattr(error, "code", None), str(error),
                  [item.as_payload() for item in getattr(error, "diagnostics", ())]]
        if isinstance(error, MechanicsRejected):
            result.append({
                "field_paths": list(error.field_paths),
                "validation_issues": [issue.as_payload() for issue in error.validation_issues],
                "state_changed": error.state_changed,
                "refusal_reasons": list(error.refusal_reasons),
            })
        return result


def shipped_schemas():
    recording = json.loads(RECORDING.read_text(encoding="utf-8"))
    tables = recording["database"]["tables"]
    schemas = []
    for row in tables["package_tools"]["rows"]:
        schemas.append(("tool", row["input_schema_json"]["$json"]))
    for row in tables["package_manifests"]["rows"]:
        record = row["canonical_json"]["$json"]
        for artifact in record.get("participant_artifacts", []):
            schemas.append(("participant", artifact["schema"]))
        for state in record.get("state_schemas", []):
            schemas.append(("state", state["schema"]))
    return schemas


SYNTHETIC = [
    {"type": "object", "properties": {"a": {"type": ["string", "null"], "minLength": 2, "enum": ["xx", None]}},
     "required": ["a"], "additionalProperties": False},
    {"type": "object", "properties": {"n": {"type": ["integer", "null"], "minimum": 0, "maximum": 5}}},
    {"type": ["null", "array"], "items": {"type": "string"}, "minItems": 1},
    {"type": "array", "items": {"type": "integer"}, "contains": {"type": "integer", "enum": [3, 4]},
     "minContains": 2, "uniqueItems": True, "maxItems": 4},
    {"type": "array", "items": {"type": "integer"}, "contains": {"type": "integer"}, "minContains": True},
    {"type": "object", "properties": {"kind": {"type": "string"}, "x": {"type": "integer"}},
     "if": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["move"]}}, "required": ["kind"]},
     "then": {"type": "object", "required": ["x"]},
     "allOf": [{"type": "object", "properties": {"x": {"type": "integer", "maximum": 9}}}]},
    {"type": "object", "properties": {"target": {"type": "string", "x-sylvia-gm-reference": "participant"},
                                      "targets": {"type": "array", "items": {"type": "string",
                                                  "x-sylvia-gm-reference": "participant"}}}},
    {"type": "object", "additionalProperties": {"type": "number", "minimum": -1.5}},
    {"type": "object", "properties": {"e": {"type": "string", "enum": ["a", 1, 1.5, True, None, [1], {"k": "v"}]}}},
    {"type": "object", "properties": {"s": {"type": "string", "maxLength": 3}}, "required": ["s", "t"]},
    {"type": "integer", "minimum": 2**53, "maximum": 2**63},
    {"type": "number", "minimum": 0.5},
    {"type": "object", "properties": {"x": {"type": "string"}}, "minItems": 1},
    {"type": "object", "properties": {"x": {"type": "frob"}}},
    {"type": ["string", "integer"]},
    {"type": ["null", "null"]},
    {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 2},
    {"type": "array"},
    {"type": "object", "extra": 1, "zzz": 2},
    {"type": "object", "if": {"type": "object"}},
    {"type": "object", "contains": {"type": "string"}},
    {"type": "array", "items": {"type": "string"}, "minContains": 1},
    {"type": "string", "$id": ""},
    {"type": "string", "enum": "x"},
    {"type": "string", "minLength": -1},
    {"type": "object", "properties": {"x": 1}},
    {"type": "object", "required": ["x"]},
    {"type": "object", "required": ["x", "x"], "properties": {"x": {"type": "string"}}},
    {"type": "object", "additionalProperties": 3},
    {"type": "string", "description": 3},
    {"type": "object", "allOf": []},
]

JUNK = [None, True, False, 0, 1, -1, 2, 3, 4, 5, 9, 10, 1.0, 2.5, -1.5, 2**53, 2**63, "", "x", "xx", "xxxx", "\u00e9\u00e9",
        "p-1", "p-2", "stranger", [], [3], [3, 3], [3, 4], [1, 2, 3, 4, 5], ["a"], ["p-1", "stranger"], {}, {"k": 1}]


def mutate(schema):
    paths = []

    def walk(value, path):
        paths.append(path)
        if isinstance(value, dict):
            for key, item in value.items():
                walk(item, (*path, key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, (*path, index))

    walk(schema, ())
    result = copy.deepcopy(schema)
    path = rng.choice(paths)
    if not path:
        replacement = copy.deepcopy(rng.choice(JUNK))
        return [["set", [], replacement]], replacement
    parent = result
    for part in path[:-1]:
        parent = parent[part]
    choice = rng.random()
    if isinstance(parent, dict) and choice < 0.3:
        del parent[path[-1]]
        return [["delete", list(path), None]], result
    value = rng.choice(JUNK + ["object", "array", "string", "integer", ["string", "null"]])
    parent[path[-1]] = value
    return [["set", list(path), value]], result


def values_for(schema):
    values = [{}, {"zz_extra": 1}]
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    names = list(properties) if isinstance(properties, dict) else []
    for _ in range(6):
        chosen = rng.sample(names, min(len(names), rng.randint(0, 4))) if names else []
        values.append({name: copy.deepcopy(rng.choice(JUNK)) for name in chosen})
    return values


def main() -> None:
    sources = [(kind, schema) for kind, schema in shipped_schemas()] + [("synthetic", s) for s in SYNTHETIC]
    cases = []
    for index, (kind, schema) in enumerate(sources):
        variants = [([], schema)] + [mutate(schema) for _ in range(3)]
        for edits, candidate in variants:
            if not isinstance(candidate, dict):
                continue
            admitted = outcome(lambda: validate_json_schema(candidate, label="Schema"))
            case = {"source": index, "edits": edits, "admission": admitted}
            if admitted == ["ok"]:
                checks = []
                for value in values_for(candidate):
                    checks.append({
                        "value": value,
                        "parameters": outcome(lambda: validate_parameters(value, candidate)),
                        "json_value": outcome(lambda: validate_json_value(value, candidate, label="Value")),
                        "references": outcome(lambda: validate_references(value, candidate,
                                                                          participant_ids={"p-1", "p-2"})),
                    })
                case["checks"] = checks
            cases.append(case)
    reasons = [[], ["ok"], ["Range is too far"], ["dup", "dup"], [" padded"], [""], ["x" * 500], ["x" * 501],
               ["line\nbreak"], ["\u00e9" * 500]]
    rejections = []
    for safe in ["Target is out of range", " padded", "", "x" * 501, "tab\there", "\u00e9"]:
        for extra in reasons[:6]:
            rejections.append({"safe_reason": safe, "reasons": extra, "outcome": outcome(
                lambda: (_ for _ in ()).throw(RulesetPreconditionRejected(safe, reasons=tuple(extra))))})
    for extra in reasons:
        rejections.append({"refusal_reasons": extra, "outcome": outcome(
            lambda: (_ for _ in ()).throw(MechanicsRejected("m", code="c", refusal_reasons=tuple(extra))))})
    # field_paths (0.4.1, #279): the inputs a refusal names, through RulesetPreconditionRejected.
    named = [
        ["Missing input 'target_distance'"], ["Missing required parameter 'target_willing'"],
        ["Input 'a' and input 'b'", "input 'a' again"], ["noinput 'x'", "_input 'x'", "1input 'x'"],
        ["input 'x", "input '1x'", "input  'x'", "required  parameter 'x'", "Required parameter 'x'"],
        ["INPUT 'x'", "input 'x-y'", "input 'x_9'", "input '" + "a" * 80 + "'", "input '" + "a" * 81 + "'"],
        [chr(0xe9) + "input 'x'", chr(0xbd) + "input 'x'", chr(0x903) + "input 'x'", "(input 'p')"],
        ["input 'ab'input 'cd'", "required parameter 'r' and input 'r'"],
    ]
    for extra in named:
        rejections.append({"safe_reason": "Target is out of range", "reasons": extra, "outcome": outcome(
            lambda: (_ for _ in ()).throw(RulesetPreconditionRejected("Target is out of range",
                                                                     reasons=tuple(extra))))})
    rejections.append({"safe_reason": "Missing input 'first'", "reasons": ["input 'second'", "input 'first'"],
                       "outcome": outcome(lambda: (_ for _ in ()).throw(RulesetPreconditionRejected(
                           "Missing input 'first'", reasons=("input 'second'", "input 'first'"))))})
    texts = [text for group in named for text in group] + ["", "input", "input ''", chr(9) + "input 'x'",
                                                           "input 'x'" + chr(10)]
    named_inputs = [[text, _NAMED_INPUT.findall(text)] for text in texts]
    blockers, start = [], None
    for code in range(sys.maxunicode + 2):
        surrogate = 0xD800 <= code <= 0xDFFF
        blocked = code <= sys.maxunicode and not surrogate and not _NAMED_INPUT.findall(chr(code) + "input 'x'")
        if blocked and start is None:
            start = code
        elif not blocked and start is not None:
            blockers.append([start, code - 1])
            start = None
    vectors = {
        "python": sys.version.split()[0],
        "synthetic": SYNTHETIC,
        "cases": cases,
        "rejections": rejections,
        "named_inputs": named_inputs,
        "named_input_blockers": blockers,
    }
    text = json.dumps(vectors, ensure_ascii=True, separators=(",", ":")) + "\n"
    OUTPUT.write_text(text, encoding="utf-8", newline="\n")
    counts = {}
    for case in cases:
        counts[case["admission"][0]] = counts.get(case["admission"][0], 0) + 1
        for check in case.get("checks", []):
            for field in ("parameters", "json_value", "references"):
                key = field + ":" + check[field][0]
                counts[key] = counts.get(key, 0) + 1
    print(len(text) // 1000, "KB", len(cases), "schemas", counts)


if __name__ == "__main__":
    main()
