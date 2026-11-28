"""Write ledger_tools_vectors.json: the ledger's tool lists, the tool loader and the fixed
projection (L2b).

Run from the repository root with the snapshot's interpreter, after `cargo build -p
sylvia-compare`:

    .venv/Scripts/python.exe -I crates/sylvia-agent/tests/vectors/generate_ledger_tools_vectors.py

As `generate_ledger_state_vectors.py` (whose renaming and encoding this reuses), two sources of
cases, every answer Python's own:

- `traced`: each distinct call real GM requests make to these functions, with the Python owner
  in forced ledger mode and each tool list: `deferred` (Sylvia's loader, on the stand-in's
  Chat Completions route), `fixed` and `stable`, over the `ruleless-turn` and
  `srd-mechanics-turn` sessions (SRD Lite's whole catalog). Identities are renamed and each
  call is made again here; a `MechanicNames` argument is rebuilt from its catalog and reserved
  names. `schema_choices` is reached through `argument_choices_record`, since its own traced
  calls would be its recursion.

Each distinct tool definition is stored once, in `tools`, and a case refers to it as
`{"__tool__": index}`: SRD Lite's catalog would otherwise repeat in most cases.
- `synthetic`: hand-made inputs for the rules those runs do not reach (schema shapes the
  renderer draws, short-name collisions, malformed calls, the callable records' forms, choices
  and unavailable values, compact references).

Reference tooling for the port; retired with the Python source.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))

import generate_ledger_state_vectors as state  # noqa: E402
from sylvia_gm.agent import _context_ledger as ledger  # noqa: E402
from sylvia_gm.agent import _fixed_tool_projection as projection  # noqa: E402
from sylvia_gm.agent import _tool_loader as loader  # noqa: E402

LEDGER = ["_prefix_fingerprint", "_tools_fingerprint", "toolset_fingerprint", "merged_tool_list",
          "_canonical_tool", "saved_tool_list", "callable_record", "deferred_definitions",
          "is_tool_change", "_surfaced", "_addition_block"]
LOADER = ["compact_arguments", "compact_definition", "definitions_record", "read_result",
          "unread_refusal", "defined_in_result", "callable_record", "choices_record",
          "unwrap_mechanic_call", "text_call", "wrap_mechanic_arguments", "project_conversation"]
# `schema_choices` recurses, so its traced calls are its own sub-calls; it is reached through
# `argument_choices_record` and the synthetic cases.
PROJECTION = ["argument_choices_record", "unavailable_values", "compact_schema"]
TRACED = LEDGER + [f"loader:{name}" for name in LOADER] + [f"projection:{name}" for name in PROJECTION]
RUNS = [(session, tool_list) for tool_list in ("deferred", "fixed", "stable")
        for session in ("ruleless-turn", "srd-mechanics-turn")]
MODULES = {"loader": loader, "projection": projection}


def decode(value: object) -> object:
    if isinstance(value, dict) and set(value) == {"__names__"}:
        names = value["__names__"]
        return loader.MechanicNames(names["catalog"], names["reserved"])
    if isinstance(value, dict) and set(value) not in ({"__tagged__"}, {"__set__"}):
        return {key: decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [decode(item) for item in value]
    return state.decode(value)


def call(function: str, args: list[object], kwargs: dict[str, object]) -> dict[str, object]:
    module_name, _, name = function.rpartition(":")
    module = MODULES[module_name] if module_name else ledger
    case: dict[str, object] = {"function": function, "args": args, "kwargs": kwargs}
    try:
        result = getattr(module, name)(*decode(args), **decode(kwargs))
    except Exception as error:  # noqa: BLE001 - the class is the vector
        case["raised"] = {"class": type(error).__name__, "message": str(error)}
    else:
        case["result"] = state.encode(result)
    return case


def traced() -> list[dict[str, object]]:
    compare = ROOT / "target" / "debug" / ("sylvia-compare.exe" if sys.platform == "win32" else "sylvia-compare")
    python = Path(sys.executable).resolve()
    entries: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="ledger-tools-") as folder:
        for session, tool_list in RUNS:
            trace = Path(folder) / f"{session}-{tool_list}.jsonl"
            subprocess.run(
                [str(compare), "record", "--session",
                 str(ROOT / f"crates/sylvia-compare/sessions/{session}.session.json"),
                 "--out", str(Path(folder) / f"{session}-{tool_list}"), "--owner", str(python), "--arg", "-B",
                 "--arg", str(HERE / "trace_ledger_functions.py"),
                 "--env", "SYLVIA_GM_CONTEXT_MODE=ledger", "--env", f"SYLVIA_GM_TOOL_LIST={tool_list}",
                 "--env", f"SYLVIA_LEDGER_TRACE={trace}", "--env", "SYLVIA_LEDGER_TRACED=" + ",".join(TRACED)],
                capture_output=True, cwd=ROOT)
            if trace.exists():
                entries.extend(json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines())
    renamer = state.Renamer()
    seen: set[str] = set()
    cases = []
    for entry in entries:
        args, kwargs = renamer.value(entry["args"]), renamer.value(entry["kwargs"])
        key = json.dumps([entry["function"], args, kwargs], sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        cases.append(call(str(entry["function"]), args, kwargs))
    return cases


def tool(name: str, description: object = "", parameters: object = None, **more: object) -> dict[str, object]:
    function: dict[str, object] = {"name": name, "description": description}
    if parameters is not None:
        function["parameters"] = parameters
    return {"type": "function", "function": function, **more}


def names(catalog: list[str], reserved: list[str]) -> dict[str, object]:
    return {"__names__": {"catalog": catalog, "reserved": reserved}}


ALIAS_A = "tool_0123456789ab_attack"
ALIAS_B = "tool_ba9876543210_attack"
ALIAS_C = "tool_aaaaaaaaaaaa_dash"
SCHEMAS = [
    {"type": "object"},
    {"type": "object", "properties": {}},
    {"type": "object", "properties": {}, "additionalProperties": False},
    {"type": "object", "additionalProperties": {"type": "integer"}},
    {"type": "object", "required": ["a"], "properties": {
        "a": {"type": "string", "description": "  The   target\nid ", "minLength": 1, "maxLength": 64,
              "pattern": "^[a-z]+$", "x-sylvia-gm-reference": "participant"},
        "b": {"type": "integer", "minimum": 0, "maximum": 20, "default": 3},
        "c": {"type": "number", "exclusiveMinimum": 0.5, "exclusiveMaximum": 1e20},
        "d": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 4, "uniqueItems": True},
        "e": {"enum": ["fire", "cold", 3, None, True]}, "f": {"enum": []}, "g": {"const": "xé"},
        "h": {"oneOf": [{"type": "string"}, {"type": "integer"}]},
        "i": {"anyOf": [{"type": "array", "items": {"type": "string"}}, {"type": "null"}]},
        "j": {"type": ["string", "null"]}, "k": {"type": "array", "items": {"enum": ["a", "b"]}},
        "l": {"items": {"type": "boolean"}}, "m": {"properties": {"n": {"type": "string"}}},
        "o": {"type": "object", "additionalProperties": {"type": "string"},
              "properties": {"p": {"$ref": "#/$defs/Point"}}},
        "q": {"$ref": "#/$defs/Point", "description": "overrides"}, "r": {"$ref": "#/$defs/Missing"},
        "y": {"$ref": "#/$defs/Point", "type": "string", "required": []},
        "s": {"$ref": "#/$defs/Loop"}, "t": {}, "u": {"type": 5},
        "v": {"type": "string", "x-sylvia-gm-castable-spells": True,
              "x-sylvia-gm-value-aliases": {"z": "zed", "a": 1}, "uniqueItems": 1},
        "w": "not a schema", "x": {"type": "string", "description": 7},
    }, "$defs": {"Point": {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]},
                 "Loop": {"$ref": "#/$defs/Loop", "type": "string"}}},
    {"type": "object", "properties": {"a": {"type": "string"}}, "required": "ab"},
    {"type": "object", "properties": {"a": {"type": "string"}}, "required": 5},
    {"type": "object", "properties": {"a": {"type": "string"}}, "required": [["a"]]},
]


def synthetic() -> list[dict[str, object]]:
    cases: list[tuple[str, list[object], dict[str, object]]] = []
    add = lambda function, *args, **kwargs: cases.append((function, list(args), kwargs))  # noqa: E731

    # The loader: compact shapes and definitions.
    for schema in SCHEMAS:
        add("loader:compact_arguments", schema)
        add("loader:compact_definition", tool("tool_0123456789ab_cast", "Cast a spell.", schema))
    add("loader:compact_definition", tool("x", None), "")
    add("loader:compact_definition", {"name": "bare", "description": "Bare tool.", "parameters": "no"})
    add("loader:compact_definition", tool("x", "d"), "shown")
    add("loader:compact_definition", {"function": {"name": 5}})
    long_description = ("word, " * 60) + "end"
    big = {"type": "object", "properties": {f"field_{i}": {"type": "string", "description": "x" * 30}
                                            for i in range(30)}}
    catalog = [ALIAS_A, ALIAS_B, ALIAS_C, "tool_cccccccccccc_call_mechanic", "plain"]
    the_names = names(catalog, ["read_tool_arguments", "plain"])
    add("loader:definitions_record", [], the_names)
    add("loader:definitions_record", [tool(ALIAS_A, "Attack."), tool(ALIAS_C, long_description, big),
                                      tool("tool_dddddddddddd_new", "Short.", big),
                                      tool(ALIAS_B, "x" * 239 + " y" * 3, big)], the_names)
    add("loader:definitions_record", [tool(ALIAS_C, "a" * 300, big)], the_names)
    # Under the limit in characters, over it in bytes: defined in full, as Python counts.
    add("loader:definitions_record", [tool(ALIAS_C, "é" * 640)], the_names)
    # Python's `$` also matches before a final newline: the alias still has its short name.
    newline_alias = "tool_ffffffffffff_jump\n"
    add("loader:definitions_record", [tool(newline_alias, "Jump.")], names([newline_alias], []))
    add("loader:definitions_record", [tool(newline_alias, "Jump.")], names([], []))
    for short in ["attack", "dash"]:
        add("loader:read_result", tool(ALIAS_C, "Dash.", SCHEMAS[4]), short)
        add("loader:unread_refusal", tool(ALIAS_C, "Dash.", SCHEMAS[4]), short)
    for message in [
        {"role": "tool", "content": json.dumps({"status": "ok", "mechanic_definition": "dash"})},
        {"role": "tool", "content": json.dumps({"mechanic_definition": 5})},
        {"role": "tool", "content": "mechanic_definition not json"},
        {"role": "tool", "content": json.dumps(["mechanic_definition"])},
        {"role": "assistant", "content": json.dumps({"mechanic_definition": "dash"})},
        {"role": "tool", "content": {"mechanic_definition": "dash"}}, {"role": "tool"},
        {"role": "tool", "content": json.dumps({"status": "ok"})},
    ]:
        add("loader:defined_in_result", message)

    # The loader's callable and choices records.
    declared = [tool("read_tool_arguments"), tool("call_mechanic"), tool("narrate"), tool("search")]
    for offered, reading in [
        (None, False), ([], False), ([tool("narrate")], False), ([tool(ALIAS_A), tool(ALIAS_C)], False),
        ([tool(ALIAS_A), tool("narrate"), tool("search")], False), ([tool(ALIAS_A)], True),
        ([tool("read_tool_arguments")], False), ([tool("unknown_one")], False), ([], True),
    ]:
        add("loader:callable_record", declared, offered, the_names, reading=reading)
    add("loader:callable_record", [tool("call_mechanic")], [], the_names)
    choice_schema = {"type": "object", "properties": {
        "target": {"type": "string", "enum": ["a", "b"], "x-sylvia-gm-reference": "participant"},
        "spell": {"type": "string", "enum": ["fireball", "light"]},
        "options": {"type": "array", "items": {"type": "object", "properties": {"k": {"enum": [1, 2]}}}}}}
    fixed_schema = {"type": "object", "properties": {
        "target": {"type": "string"}, "spell": {"type": "string"},
        "options": {"type": "array", "items": {"type": "object", "properties": {"k": {}, "z": {}}}},
        "gone": {"type": "string"}}}
    add("loader:choices_record", [tool(ALIAS_A, "A.", fixed_schema)], [tool(ALIAS_A, "A.", choice_schema)], the_names)
    add("loader:choices_record", [tool(ALIAS_A, "A.", fixed_schema)], None, the_names)
    add("loader:choices_record", [tool(ALIAS_A, "A.", fixed_schema)], [tool("tool_eeeeeeeeeeee_other", "O.", choice_schema)],
        the_names)
    add("loader:choices_record", [{"function": {"name": "x"}}], [], the_names)

    # The call itself.
    for arguments in ["", "{}", '{"name": " dash ", "arguments": {"to": 3}}', '{"name": "dash"}',
                      '{"name": "dash", "arguments": "{\\"to\\": 1}"}', '{"name": "dash", "arguments": ""}',
                      '{"name": "dash", "arguments": "not json"}', '{"name": "dash", "arguments": [1]}',
                      '{"name": "  ", "arguments": {}}', '{"name": 5}', "[1]", "not json",
                      '{"name": "dé", "arguments": {"t": "é"}}']:
        add("loader:unwrap_mechanic_call", arguments)
    callable_names = ["call_mechanic", "dash", "narrate"]
    for text in ['{"name": "dash", "arguments": {"to": 1}}', ' ```json\n{"name": "dash"}\n``` ',
                 '```{"name": "narrate", "parameters": {"t": 1}}```', '{"name": "dash", "arguments": "{}"}',
                 '{"name": "dash", "arguments": null}', '{"name": "other", "arguments": {}}',
                 '{"name": " dash ", "arguments": {}, "parameters": 5}', "Plain prose.", '{"name": 3}',
                 '{"name": "dash", "arguments": "bad"}', "```JSON\n{}\n```", "``json{\"name\": \"dash\"}``"]:
        add("loader:text_call", text, callable_names)
    for name, arguments in [("dash", '{"to": 1}'), ("dash", ""), ("dash", "not json"), ("dé", '{"b":1, "a":2}'),
                            ("dash", "[1, 2]")]:
        add("loader:wrap_mechanic_arguments", name, arguments)
    declared_set = {"__set__": ["narrate", "read_tool_arguments"]}
    for message in [
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": ALIAS_A, "arguments": '{"t": 1}'}},
            {"id": "c2", "type": "function", "function": {"name": "narrate", "arguments": "{}"}},
            {"id": "c3", "function": {"name": ALIAS_C, "arguments": ""}}, {"id": "c4", "function": "x"},
            "not a call", {"id": "c5"}, {"id": "c6", "function": {"name": 5}},
            {"id": "c7", "function": {"name": ALIAS_C, "arguments": {"t": 1}}}]},
        {"role": "assistant", "tool_calls": "x"}, {"role": "tool", "name": ALIAS_A, "content": "r"},
        {"role": "tool", "name": "narrate", "content": "r"}, {"role": "tool", "content": "r"},
        {"role": "user", "name": ALIAS_A, "content": "u"},
    ]:
        add("loader:project_conversation", message, declared_set, the_names)

    # The ledger's tool lists.
    sent = [tool("a", "A."), tool("b", "B."), {"name": "c", "description": "C."}]
    for offered in [None, [], [tool("b", "B.")], [tool("b", "B2."), tool("d", "D."), tool("d", "D2."), tool("", "x"),
                                                    {"name": "a", "description": "A."}],
                    [tool("a", "A.", None, extra=1)], [tool("e", "E.")]]:
        add("merged_tool_list", sent, offered)
    add("merged_tool_list", [tool("a", "1"), tool("a", "2")], [tool("a", "3")])
    add("_canonical_tool", {"z": 1, "a": {"y": [{"b": 1, "a": 2}]}, "é": 1})
    for tools in [None, [], sent, list(reversed(sent)), [{"function": "x", "name": "n"}]]:
        add("_tools_fingerprint", tools)
        add("toolset_fingerprint", tools)
    add("toolset_fingerprint", [tool("a", "2"), tool("a", "1")])
    add("_prefix_fingerprint", [{"role": "system", "content": "x"}, {"content": "y"}])
    owner = {"owner": "gm", "id": 1}
    journal = lambda **fields: {"kind": "gm_wire_journal", **fields}  # noqa: E731
    records = [
        journal(context_owner=owner, tools_added=[tool("a", "A."), tool("b", "B.")]),
        {"kind": "gm_wire_journal", "omitted_from_export": True, "tools_added": [tool("z", "Z.")]},
        {"kind": "other", "tools_added": [tool("y", "Y.")]}, "not a record",
        journal(context_owner={"owner": "other"}, tools_added=[tool("q", "Q.")]),
        journal(context_owner=owner, tools_replaced=[tool("a", "A2.")], tools_added="x"),
        journal(context_owner=owner, tools_added=[tool("c", "C."), "x"]),
        journal(context_owner={"id": 1.0, "owner": "gm"}, tools_added=[tool("d", "D.")]),
    ]
    add("saved_tool_list", records, owner)
    add("saved_tool_list", records, None)
    add("saved_tool_list", [], owner)
    for offered in [None, [], [tool("a"), tool("b"), tool("c")], [tool("a")], [tool("a"), tool("b")], [tool("z")]]:
        add("callable_record", [tool("a"), tool("b"), tool("c")], offered)
    add("callable_record", [tool("b"), tool("a"), tool("b")], [tool("b")])
    fixed = [tool("narrate", "N."), tool(ALIAS_A, "A."), tool("read_tool_arguments", "R."),
             tool("x", "X.", None, defer_loading=False)]
    for core in [{"__set__": ["narrate"]}, {"__set__": []}, {"__set__": ["narrate", "read_tool_arguments"]}]:
        for deferral in ["native", "provider", "loader", "other"]:
            add("deferred_definitions", fixed, core, deferral)
    for message in [
        {"role": "system", "content": [{"type": "tool_addition"}, {"type": "tool_removal", "x": 1}]},
        {"role": "system", "content": []}, {"role": "system", "content": [{"type": "text"}]},
        {"role": "user", "content": [{"type": "tool_addition"}]}, {"role": "system", "content": ["tool_addition"]},
        {"role": "system", "content": "tool_addition"}, {"role": "system", "content": [{"type": "tool_addition"}, 5]},
    ]:
        add("is_tool_change", message)
    add("_surfaced", [{"c": "tools", "n": ["a", "b"]}, {"c": "event", "n": ["z"]}, {"c": "tools", "n": None},
                      {"c": "tools", "n": "cd"}, {"c": "tools", "n": [1, "a"]}, {"c": "tools"}])
    add("_surfaced", [{"c": "tools", "n": 5}])
    for given in [tool("t", "T.", {"type": "object", "properties": {}}), {"name": "bare"}, {"function": {}},
                  tool("t", None)]:
        add("_addition_block", given, True)
        add("_addition_block", given, False)

    # The fixed projection.
    add("projection:schema_choices", choice_schema, fixed_schema)
    add("projection:schema_choices", choice_schema, {})
    add("projection:schema_choices", {"enum": [1]}, {"enum": [1.0]})
    add("projection:schema_choices", {"enum": [1]}, {"enum": [2]}, "$.x")
    add("projection:schema_choices", {"properties": {"a": {"enum": [1]}}}, {"properties": {"a": "bad"}})
    add("projection:schema_choices", {"items": {"enum": [1]}}, {"items": "bad"})
    add("projection:schema_choices", {"properties": "x"}, "not a mapping")
    add("projection:argument_choices_record", [tool("t", "T.", fixed_schema), tool("t", "T.", {})],
        [tool("t", "T.", choice_schema), tool("u", "U.", {"type": "object"})])
    add("projection:argument_choices_record", [], None)
    add("projection:argument_choices_record", [{"function": {"name": "t"}}], [])
    add("projection:argument_choices_record", [], [tool("t", "T.", [1])])
    current = {"type": "object", "properties": {
        "spell": {"enum": ["fireball", "light"]}, "level": {"enum": [1, 2]},
        "list": {"type": "array", "items": {"enum": ["a"]}}, "nested": {"properties": {"k": {"enum": [True]}}}}}
    stable = {"type": "object", "properties": {"spell": {}, "level": {"enum": [1, 2]}, "list": {"items": {}},
                                               "nested": {"properties": {}}}}
    for arguments in [{"spell": "light"}, {"spell": "wish", "level": 3, "list": ["a", "b"], "nested": {"k": 1}},
                      {"nested": {"k": False}}, [], "text", {"list": []}]:
        add("projection:unavailable_values", arguments, current, stable)
    add("projection:unavailable_values", "x", {"enum": "xyz"}, {})
    add("projection:unavailable_values", 5, {"enum": "xyz"}, {})
    add("projection:unavailable_values", {"a": 1}, {"properties": [1]}, {})
    add("projection:unavailable_values", [1], {"items": {"enum": [1]}}, "bad")
    add("projection:unavailable_values", [], {"items": {"enum": [1]}}, "bad")
    repeated = {"type": "string", "description": "An entity id of the current scene, as listed.", "minLength": 1}
    add("projection:compact_schema", {"type": "object", "properties": {f"p{i}": dict(repeated) for i in range(4)}})
    add("projection:compact_schema", {"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "string"}}})
    add("projection:compact_schema", {"type": "object", "properties": {
        **{f"r{i}": {"type": "string", "x-sylvia-gm-reference": "participant", "description": "x" * 60} for i in range(4)},
        **{f"s{i}": {"enum": ["a", "b"], "description": "y" * 60} for i in range(4)},
        **{f"t{i}": {"type": "array", "items": {"type": "string"}, "description": "z" * 60} for i in range(4)},
        **{f"u{i}": {"type": "integer", "description": "w" * 60} for i in range(2)},
    }})
    add("projection:compact_schema", {"type": "object", "properties": "x"})
    add("projection:compact_schema", {"type": "object", "$defs": {}, "properties": {f"p{i}": dict(repeated) for i in range(4)}})
    return [call(function, args, kwargs) for function, args, kwargs in cases]


class Interned:
    """Each distinct tool definition (`{"type": "function", "function": ...}`) stored once, in
    `tools`, and referred to as `{"__tool__": index}` wherever a case holds it."""

    def __init__(self) -> None:
        self.tools: list[object] = []
        self.index: dict[str, int] = {}

    def value(self, value: object) -> object:
        if isinstance(value, dict):
            if value.get("type") == "function" and isinstance(value.get("function"), dict):
                key = json.dumps(value, ensure_ascii=False)
                if key not in self.index:
                    self.index[key] = len(self.tools)
                    self.tools.append(value)
                return {"__tool__": self.index[key]}
            return {key: self.value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.value(item) for item in value]
        return value


def main() -> None:
    interned = Interned()
    groups = {"traced": traced(), "synthetic": synthetic()}
    vectors: dict[str, object] = {name: interned.value(cases) for name, cases in groups.items()}
    vectors = {"tools": interned.tools, **vectors}
    out = HERE / "ledger_tools_vectors.json"
    out.write_bytes((json.dumps(vectors, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
    counts: dict[str, int] = {}
    for case in groups["traced"]:
        counts[str(case["function"])] = counts.get(str(case["function"]), 0) + 1
    print("traced", counts, "synthetic", len(vectors["synthetic"]), file=sys.stderr)


if __name__ == "__main__":
    main()
