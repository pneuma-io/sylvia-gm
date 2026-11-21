"""Generate reference vectors for sylvia_domain::content from the frozen Python reference.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-domain/tests/vectors/generate_content_vectors.py

For every installed content definition it validates the definition's example, its invalid
example, a few non-object documents, and several hundred mutations of the example (each stored
as edits to the example, which the Rust test applies the same way). It also records Python's
`re.search` verdicts for every pattern the definitions use, and hashes of the catalogue output.
Those go in `content_vectors.json`. `schema_vectors.json` holds the engine's own errors
(keyword, `message`, paths, union context) for synthetic schemas covering every keyword, the
shipped packages' tool parameter schemas, the state schema and the parameter descriptor schema.

`jsonschema` collects a schema-valued `additionalProperties`'s extra members in a `set`, so
Python's own error order then depends on hash randomisation. The generator pins that one `set`
to the instance's member order (one of the orders Python can produce) and counts the cases
where the unpinned run in this process ordered the diagnostics differently (printed only).
The output is deterministic (fixed seed) and checked in, so the Rust tests need no Python.
This script is reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
import re
import sys
from pathlib import Path

import jsonschema._keywords
import jsonschema.validators
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.domain.content_definitions import (  # noqa: E402
    _DEFINITIONS,
    get_content_definition,
    list_content_definitions,
)
from sylvia_gm.domain.content_validation import (  # noqa: E402
    ContentShapeError,
    _validator,
    validate_content_shape,
)

OUTPUT = Path(__file__).with_name("content_vectors.json")
SCHEMA_OUTPUT = Path(__file__).with_name("schema_vectors.json")
rng = random.Random(20261121)
PYTHON_SET = set
# Cases where this run's unpinned set order gave different corrections. It follows the hash
# seed, so it is printed, not stored: the vector file stays byte-identical between runs.
ORDER_SENSITIVE = [0]


def ordered_set(iterable):
    return list(dict.fromkeys(iterable))


def diagnostics(name, document, file):
    try:
        validate_content_shape(name, document, file=file)
    except ContentShapeError as error:
        return [item.as_payload() for item in error.diagnostics]
    return []


def both_orders(name, document, file):
    """Diagnostics with the pinned order, and whether the unpinned run differed."""
    jsonschema._keywords.set = PYTHON_SET
    free = diagnostics(name, document, file)
    jsonschema._keywords.set = ordered_set
    pinned = diagnostics(name, document, file)
    return pinned, free != pinned


def leaves(value, path=()):
    yield path, value
    if isinstance(value, dict):
        for key, item in value.items():
            yield from leaves(item, (*path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from leaves(item, (*path, index))


REPLACEMENTS = [
    1, -1, 0, 1.5, -0.5, 10**9, 2**40, "", " ", "x", "\x1c", "line\n", "a\x00b", "\u00e9" * 3,
    "y" * 600, "z" * 5000, True, False, None, [], [1], {}, {"k": 1}, ["a", "a"],
]


def apply(example, edits):
    document = copy.deepcopy(example)
    for op, path, value in edits:
        if not path:
            document = copy.deepcopy(value)
            continue
        parent = document
        for part in path[:-1]:
            parent = parent[part]
        last = path[-1]
        if op == "set":
            parent[last] = copy.deepcopy(value)
        elif op == "delete":
            del parent[last]
        elif op == "add":
            parent[last] = copy.deepcopy(value)
        elif op == "repeat":
            # Separate copies: a later edit to element 0 must not change the others.
            parent[last] = [copy.deepcopy(parent[last][0]) for _ in range(value)]
    return document


def edits_for(example):
    paths = list(leaves(example))
    single = []
    for path, value in paths:
        if isinstance(value, dict):
            for key in value:
                single.append([["delete", [*path, key], None]])
            single.append([["add", [*path, "zz_extra"], 1]])
            single.append([["add", [*path, "Bad Name!"], "x"]])
        if isinstance(value, list) and value:
            single.append([["set", list(path), []]])
            single.append([["repeat", list(path), 2]])
            single.append([["repeat", list(path), 300]])
    for path, value in paths:
        if path:
            for replacement in rng.sample(REPLACEMENTS, 6):
                single.append([["set", list(path), replacement]])
    chosen = single if len(single) <= 170 else rng.sample(single, 170)
    combined = []
    for _ in range(40):
        picks = rng.sample(single, min(len(single), rng.randint(2, 6)))
        combined.append([edit for group in picks for edit in group])
    return chosen + combined


def all_string_keys(value):
    if isinstance(value, dict):
        return all(isinstance(key, str) and all_string_keys(item) for key, item in value.items())
    if isinstance(value, list):
        return all(all_string_keys(item) for item in value)
    return True


def document_cases(name, example):
    cases = []
    roots = [[["set", [], value]] for value in ([], "x", None, 1, {}, {"zz_extra": 1})]
    for edits in [[]] + roots + edits_for(example):
        try:
            document = apply(example, edits)
        except (KeyError, IndexError, TypeError):
            continue
        if json.loads(json.dumps(document)) != document or not all_string_keys(document):
            continue  # an edit gave a dict an integer key: no longer a JSON document
        file = rng.choice([None, "package.json", "C:\\Users\\author\\pkg\\file.json"])
        pinned, order_sensitive = both_orders(name, document, file)
        ORDER_SENSITIVE[0] += order_sensitive
        cases.append({"edits": edits, "file": file, "diagnostics": pinned})
    return cases


def patterns():
    found = []

    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "pattern" and isinstance(item, str) and item not in found:
                    found.append(item)
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for schema, _example, _notes, _file in _DEFINITIONS.values():
        walk(schema)
    pool = ["a", "Z", "0", "9", "_", "-", ".", ":", "/", "\\", " ", "\t", "\n", "\r", "\x0b", "\x0c",
            "\x1c", "\x1f", "\x85", "\xa0", "\u2028", "\u3000", "\u200b", "\x00", "{", "}", "\u00e9",
            "\U0001f600", "f", "e", "C"]
    strings = ["", "\n", "a", "a\n", "a\n\n", "\na", "abc", "{x}", "{x}\n", "C:/x", "/x", "a//b", "./a",
               "a/../b", "a/b", " a", "a ", "a\\b", "https://x", "card://y", "asset:sha256:" + "0" * 64,
               "0" * 64, "0" * 64 + "\n", "campaign-plan-reusable-" + "a" * 24 + "-" + "b" * 16,
               "on_x", "on_", "x.y.z", "x..y", "\u3000a", "a\x1c"]
    for _ in range(400):
        strings.append("".join(rng.choice(pool) for _ in range(rng.randint(0, 10))))
    return [{"pattern": pattern, "matches": [bool(re.search(pattern, text)) for text in strings]}
            for pattern in found], strings


ROOT_ID = "urn:test:root"
SYNTHETIC = [
    {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": ["string", "null"]}},
     "required": ["a", "c"], "additionalProperties": False},
    {"type": "object", "dependentRequired": {"a": ["b", "c"], "x": ["y"]}, "maxProperties": 2,
     "minProperties": 1},
    {"type": "object", "propertyNames": {"pattern": "^[a-z]+$", "maxLength": 3}},
    {"type": "object", "properties": {"no": False, "yes": True}, "additionalProperties": {"type": "string"}},
    {"type": "object", "if": {"properties": {"kind": {"const": "a"}}, "required": ["kind"]},
     "then": {"required": ["alpha"]}, "else": {"required": ["beta"], "properties": {"beta": {"minimum": 3}}}},
    {"type": "object", "if": {"required": ["kind"]}, "then": {"properties": {"kind": {"enum": ["x", 1]}}}},
    {"type": "array", "contains": {"type": "string"}, "uniqueItems": True, "minItems": 2, "maxItems": 4,
     "items": {"type": ["string", "integer", "boolean", "object", "array"]}},
    {"type": "array", "items": False},
    {"oneOf": [{"type": "string", "minLength": 2}, {"type": "object", "properties": {"n": {"type": "integer"}},
                                                     "required": ["n"]}, {"type": "array"}]},
    {"oneOf": [{"type": "integer"}, {"type": "number"}]},
    {"anyOf": [{"type": "object", "required": ["p"]}, {"type": "object", "required": ["q"]}]},
    {"type": "object", "properties": {"v": {"anyOf": [{"type": "string"}, {"oneOf": [
        {"type": "object", "required": ["deep"], "properties": {"deep": {"type": "integer"}}},
        {"type": "array", "maxItems": 1}]}]}}},
    {"not": {"type": "object", "required": ["forbidden"]}, "type": "object"},
    {"allOf": [{"required": ["a"]}, {"properties": {"a": {"exclusiveMinimum": 0, "maximum": 10}}},
               {"properties": {"a": {"type": "integer"}}}]},
    {"$id": ROOT_ID, "$defs": {
        "name": {"type": "string", "pattern": "^\\S(?:[\\s\\S]*\\S)?$(?![\\s\\S])", "maxLength": 5},
        "inner": {"$id": "urn:test:inner", "$defs": {"leaf": {"type": "integer"}},
                  "type": "object", "properties": {"leaf": {"$ref": "#/$defs/leaf"}}}},
     "type": "object", "properties": {
        "name": {"$ref": "#/$defs/name"}, "inner": {"$ref": "urn:test:inner"},
        "list": {"type": "array", "items": {"$ref": "#/$defs/name"}}}},
    {"type": "object", "properties": {"const": {"const": {"a": [1, True]}}, "e": {"enum": [[1], {"k": 1.0}]}}},
    {"type": "object", "properties": {"s": {"type": "string", "minLength": 1, "maxLength": 3}}},
    {"type": "array", "contains": {"type": "integer"}, "minContains": 2, "maxContains": 3},
    {"type": "array", "contains": {"type": "string"}, "minContains": 0},
    {"type": "object", "properties": {"x": {"exclusiveMaximum": 3, "minimum": -2.5}}},
]
SYNTHETIC_DOCUMENTS = [
    {}, [], "x", None, 0, 1.0, True, {"a": 1}, {"a": 1.0, "b": 2, "c": 3, "d": 4}, {"a": "x", "b": None},
    {"a": 1, "b": "x", "c": 1}, {"x": 1}, {"x": 1, "y": 2, "z": 3}, {"abcd": 1, "B": 2, "ok": 3},
    {"no": 1, "yes": 2, "other": 3, "more": "s"}, {"kind": "a"}, {"kind": "b", "beta": 1}, {"kind": "a", "alpha": 1},
    {"beta": 5}, {"kind": 2}, {"kind": 1.0}, ["a", "a"], ["a", 1, True], [1, True], [1, 1.0], [[1], [True]],
    [1, 2, 3, 4, 5], ["a", "b", "c", "d", "e"], [{"a": 1}, {"a": 1.0}], [3], "s", "ss", {"n": 1}, {"n": "x"},
    {"m": 1}, 5, 5.5, {"p": 1}, {"q": 1}, {"v": "s"}, {"v": {"deep": "x"}}, {"v": {"deep": 1}}, {"v": [1, 2]},
    {"v": {}}, {"v": 3}, {"forbidden": 1}, {"a": 0}, {"a": 11}, {"a": 5.5}, {"a": 10},
    {"name": "ok", "inner": {"leaf": 1}, "list": ["a", " b", "ccccccc", "d\n"]},
    {"name": " x", "inner": {"leaf": "no"}, "list": "x"}, {"name": "x\u001c", "inner": []},
    {"const": {"a": [1, True]}}, {"const": {"a": [1.0, True]}}, {"const": {"a": [1, 1]}},
    {"e": [1.0]}, {"e": {"k": 1}}, {"e": [True]}, {"s": ""}, {"s": "\u00e9\u00e9\u00e9\u00e9"},
    {"s": "\U0001f600"}, {"Bad Name!": 1, "a": 1},
    [1, 2, 3, 4], [1, "a"], [1, 2], ["a", 1, 2, 3, 4], {"x": 3}, {"x": 2.9}, {"x": -2.5}, {"x": -3},
    {"q": "it's", "r": "'" + '"', "u": "\u200b\x1c\u00e9\U0001f600", "f": -0.0, "g": 1e16, "h": [[], {}]},
]


def error_tree(error):
    return {
        "keyword": error.validator,
        "message": error.message,
        "path": list(error.absolute_path),
        "schema_path": list(error.relative_schema_path),
        "context": [error_tree(child) for child in error.context],
    }


def synthetic_cases():
    from sylvia_gm.domain import content_validation

    cases = []
    original = content_validation._validator
    try:
        for index, schema in enumerate(SYNTHETIC):
            validator = content_validation._ContentValidator(schema)
            content_validation._validator = lambda _name, validator=validator: validator
            draft = Draft202012Validator(schema)
            for document in SYNTHETIC_DOCUMENTS:
                cases.append({
                    "schema": index,
                    "document": document,
                    "errors": [error_tree(error) for error in validator.iter_errors(document)],
                    "draft_errors": [error_tree(error) for error in draft.iter_errors(document)],
                    "diagnostics": diagnostics("synthetic", document, None),
                })
    finally:
        content_validation._validator = original
    return cases


JUNK = [None, True, 0, -1, 1.0, 2.5, 10**9, "", "x", "\u00e9", "line\n", [], [1, 1], ["a"], {}, {"k": "v"}]


def junk_documents(schema, count, singles=None):
    """Documents for a schema without an example: empty, extra members, declared properties
    set to junk alone (all of them, or `singles` sampled), and random combinations."""
    documents = [{}, {"zz_extra": 1}, [], "x", None]
    properties = list(schema.get("properties", {})) if isinstance(schema, dict) else []
    pairs = [(name, value) for name in properties for value in rng.sample(JUNK, 3)]
    if singles is not None and len(pairs) > singles:
        pairs = rng.sample(pairs, singles)
    for name, value in pairs:
        documents.append({name: value})
    for _ in range(count):
        chosen = rng.sample(properties, min(len(properties), rng.randint(1, 5))) if properties else []
        documents.append({name: rng.choice(JUNK) for name in chosen})
    return documents


def tool_cases():
    """The shipped packages' tool parameter schemas (what argument contracts validate), read
    from the Python owner's startup recording, under jsonschema's default checker."""
    recording = json.loads((ROOT / "crates/sylvia-compare/recordings/windows/startup.recording.json")
                           .read_text(encoding="utf-8"))
    cases = []
    for row in recording["database"]["tables"]["package_tools"]["rows"]:
        schema = row["input_schema_json"]["$json"]
        draft = Draft202012Validator(schema)
        for document in junk_documents(schema, 3, singles=6):
            cases.append({
                "package_id": row["package_id"], "tool_name": row["tool_name"], "document": document,
                "errors": [error_tree(error) for error in draft.iter_errors(document)],
            })
    return cases


def state_cases():
    """`state/state_schema.json` (`validator_for` picks Draft 2020-12) and the runtime parameter
    descriptor schema, under jsonschema's default checker."""
    from sylvia_gm.runtime._parameter_declarations import PARAMETER_DESCRIPTOR_SCHEMA

    state = json.loads((ROOT / "src/sylvia_gm/state/state_schema.json").read_text(encoding="utf-8"))
    cases = []
    for name, schema in (("state", state), ("parameter_descriptor", PARAMETER_DESCRIPTOR_SCHEMA)):
        validator = jsonschema.validators.validator_for(schema)(schema)
        documents = junk_documents(schema, 30)
        defs = schema.get("$defs", {})
        for definition in defs.values():
            for document in junk_documents(definition, 10):
                documents.append({key: [document] for key in list(schema.get("properties", {}))[:3]})
        for document in documents:
            cases.append({"schema": name, "document": document,
                          "errors": [error_tree(error) for error in validator.iter_errors(document)],
                          "valid": validator.is_valid(document)})
    return cases, PARAMETER_DESCRIPTOR_SCHEMA


def main() -> None:
    jsonschema._keywords.set = ordered_set
    definitions = []
    for name, (_schema, example, _notes, _file) in _DEFINITIONS.items():
        _validator(name)  # compile and check_schema first, as Python does
        definitions.append({"name": name, "cases": document_cases(name, example)})
    found, strings = patterns()
    catalogue = {
        "list_sha256": hashlib.sha256(json.dumps(list_content_definitions()).encode()).hexdigest(),
        "definition_sha256": {
            name: hashlib.sha256(json.dumps(get_content_definition(name)).encode()).hexdigest()
            for name in _DEFINITIONS
        },
    }
    state, parameter_descriptor_schema = state_cases()
    content = {
        "python": sys.version.split()[0],
        "definitions": definitions,
        "patterns": found,
        "pattern_strings": strings,
        "catalogue": catalogue,
    }
    engine = {
        "python": sys.version.split()[0],
        "synthetic_schemas": SYNTHETIC,
        "synthetic": synthetic_cases(),
        "tools": tool_cases(),
        "state": state,
        "parameter_descriptor_schema": parameter_descriptor_schema,
    }
    print(f"cases whose corrections followed this run's set order: {ORDER_SENSITIVE[0]}")
    for path, vectors in ((OUTPUT, content), (SCHEMA_OUTPUT, engine)):
        path.write_text(json.dumps(vectors, indent=None, ensure_ascii=True, separators=(",", ":")) + "\n",
                        encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
