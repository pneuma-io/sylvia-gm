"""Generate vectors for the catalog-facing package tool helpers of sylvia-app::package_runtime
from the frozen Python.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_catalog_tool_vectors.py

(`.venv/bin/python` on Linux.) Deterministic and checked in. Reference tooling for the port,
retired with the Python source.

Each case is a runtime, a package id and one tool declaration. The record is what each helper
returns (`package_tool_description`, `package_tool_model_behavior`, `package_tool_decision_title`,
`package_tool_player_controls`, `package_tool_permanently_refused`) or the exception's class and
message. Every declared tool of SRD Lite (read from its source file), Riichi and the conformance
fixture is a case, as are fixture edits of each tool's metadata and declarations that do not
match their runtime tool.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import os
import random
import sys
import zipfile
from pathlib import Path

import jsonschema._keywords
import yaml

import sylvia_gm.application.package_runtime as pr
from sylvia_gm.domain.authority import owned_payload, thaw_json_value
from sylvia_gm.domain.package_library import ToolDeclaration

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
OUTPUT = HERE / "catalog_tool_vectors.json"
FIXTURE = "tests/fixtures/rulesets/signal-and-static"
SRD = "rulesets/srd-lite"
RIICHI = "rulesets/riichi-mahjong/riichi-mahjong-0.1.1.sylvia-gm-package"

os.environ.pop("SYLVIA_RUNTIME_CACHE_DIR", None)


class _SortedSet(set):
    def __iter__(self):
        items = list(super().__iter__())
        try:
            return iter(sorted(items))
        except TypeError:
            return iter(items)


pr.set = _SortedSet
jsonschema._keywords.set = lambda iterable: list(dict.fromkeys(iterable))  # row 6

HELPERS = {
    "description": pr.package_tool_description,
    "model_behavior": pr.package_tool_model_behavior,
    "decision_title": pr.package_tool_decision_title,
    "player_controls": pr.package_tool_player_controls,
    "permanently_refused": pr.package_tool_permanently_refused,
}


def plain(value: object) -> object:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, tuple) and len(value) == 2 and hasattr(value[0], "value"):
        return [value[0].value, [plain(item) for item in value[1]]]
    if hasattr(value, "value") and not isinstance(value, (str, int, float, bool)):
        return value.value
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    if isinstance(value, dict) or hasattr(value, "items"):
        return {str(key): plain(item) for key, item in value.items()}
    return thaw_json_value(value) if value is not None and not isinstance(value, (str, int, float, bool)) else value


def declaration_json(declaration: ToolDeclaration) -> dict:
    return {"name": declaration.name, "capability": declaration.capability,
            "input_schema": thaw_json_value(declaration.input_schema),
            "turn_statuses": list(declaration.turn_statuses), "writers": list(declaration.writers)}


def record_tools(record: dict) -> list[ToolDeclaration]:
    return [ToolDeclaration(name=tool["name"], capability=tool["capability"],
                            input_schema=owned_payload(tool["input_schema"]), state_predicates=(),
                            turn_statuses=tuple(tool["turn_statuses"]), writers=tuple(tool["writers"]))
            for tool in record["tools"]]


LAST_RUNTIME = None


def outcome(package_id: str, declaration: ToolDeclaration, runtime_bytes: bytes) -> dict:
    # Python's parse cache cannot change a result; keeping it across one runtime's calls keeps
    # the 14.6 MB SRD Lite runtime to one parse.
    global LAST_RUNTIME
    if runtime_bytes != LAST_RUNTIME:
        pr._PARSED_RUNTIMES.clear()
        LAST_RUNTIME = runtime_bytes
    record = {}
    for name, helper in HELPERS.items():
        try:
            record[name] = {"result": plain(helper(package_id=package_id, declaration=declaration, runtime_bytes=runtime_bytes))}
        except Exception as error:  # noqa: BLE001 - every exception Python lets through
            record[name] = {"error": [type(error).__name__, str(error)]}
    return record


METADATA = [
    {}, {"model_behavior": "required_engine_transition"}, {"model_behavior": "nonsense"}, {"model_behavior": None},
    {"model_behavior": {"kind": "required_ruleset_decision"}},
    {"model_behavior": {"kind": "resolves_player_intent",
                        "state_predicates": [{"namespace": "x", "key": "y", "equals": 1},
                                             {"namespace": "x", "key": "z", "equals": [1, {"a": None}], "negated": True},
                                             "skip-me", {"namespace": 5, "key": True, "equals": "e", "negated": 0}]}},
    {"model_behavior": {"kind": "supports_player_intent",
                        "state_predicates": [{"namespace": "pkg.x", "key": "state.mode", "equals": "duel"},
                                             {"namespace": "pkg.x", "key": "state.flags", "equals": [1, {"a": None}],
                                              "negated": True}]}},
    {"model_behavior": {"kind": "optional", "state_predicates": "x"}},
    {"model_behavior": {"kind": "optional", "state_predicates": [{"namespace": "x"}]}},
    {"model_behavior": {"state_predicates": []}},
    {"result_card": {"title": "  Draw a tile  "}}, {"result_card": {"title": ""}}, {"result_card": {"title": "x" * 256}},
    {"result_card": {"title": "é" * 257}}, {"result_card": {"title": 5}}, {"result_card": "Title"},
    {"player_controls": {"kind": "panel"}}, {"player_controls": None}, {"player_controls": "x"},
]


def fixture_edits(text: str, declarations: list[ToolDeclaration], package_id: str) -> list[dict]:
    data = yaml.safe_load(text)
    cases = []
    names = [item.name for item in declarations if item.name in data["tools"]]
    for metadata in METADATA:
        for name in names[:2]:
            edited = copy.deepcopy(data)
            edited["tools"][name]["metadata"] = {**edited["tools"][name].get("metadata", {}), **metadata}
            runtime = yaml.safe_dump(edited, sort_keys=False, allow_unicode=True).encode("utf-8")
            declaration = next(item for item in declarations if item.name == name)
            cases.append({"name": f"{name} {json.dumps(metadata)[:60]}", "runtime_hex": runtime.hex(),
                          "package_id": package_id, "declaration": declaration_json(declaration),
                          **outcome(package_id, declaration, runtime)})
    for description in ("  Spaced out.  ", "", None, 7, ["a"]):
        edited = copy.deepcopy(data)
        edited["tools"][names[0]]["description"] = description
        runtime = yaml.safe_dump(edited, sort_keys=False).encode("utf-8")
        declaration = next(item for item in declarations if item.name == names[0])
        cases.append({"name": f"description {description!r}", "runtime_hex": runtime.hex(), "package_id": package_id,
                      "declaration": declaration_json(declaration), **outcome(package_id, declaration, runtime)})
    # A literal-false first `require` makes a tool permanently refused.
    edited = copy.deepcopy(data)
    edited["tools"][names[0]]["effects"] = [{"require": {"condition": "false", "error": "never"}}] + list(
        edited["tools"][names[0]].get("effects", []))
    runtime = yaml.safe_dump(edited, sort_keys=False).encode("utf-8")
    declaration = next(item for item in declarations if item.name == names[0])
    cases.append({"name": "permanently refused", "runtime_hex": runtime.hex(), "package_id": package_id,
                  "declaration": declaration_json(declaration), **outcome(package_id, declaration, runtime)})
    # Declarations that do not match their runtime tool, or name no tool at all.
    base = text.encode("utf-8")
    first = declarations[0]
    mismatched = [
        dataclasses.replace(first, name="not_a_runtime_tool"),
        dataclasses.replace(first, input_schema=owned_payload({"type": "object", "properties": {}})),
        dataclasses.replace(first, capability="other.capability"),
    ]
    for declaration in mismatched:
        cases.append({"name": f"mismatch {declaration.name} {declaration.capability}", "runtime_hex": base.hex(),
                      "package_id": package_id, "declaration": declaration_json(declaration),
                      **outcome(package_id, declaration, base)})
    cases.append({"name": "invalid runtime", "runtime_hex": b"tools: [".hex(), "package_id": package_id,
                  "declaration": declaration_json(first), **outcome(package_id, first, b"tools: [")})
    return cases


def main() -> int:
    record = json.loads((ROOT / FIXTURE / "package-record.json").read_text(encoding="utf-8"))
    text = (ROOT / FIXTURE / "runtime/ruleset.yaml").read_text(encoding="utf-8")
    declarations = record_tools(record)
    cases = fixture_edits(text, declarations, record["id"])
    for declaration in declarations:
        cases.append({"name": f"fixture {declaration.name}", "runtime_hex": text.encode().hex(), "package_id": record["id"],
                      "declaration": declaration_json(declaration), **outcome(record["id"], declaration, text.encode())})
    srd = json.loads((ROOT / SRD / "package-record.json").read_text(encoding="utf-8"))
    srd_bytes = (ROOT / SRD / "runtime/ruleset.yaml").read_bytes()
    shipped = []
    for declaration in record_tools(srd):
        shipped.append({"name": f"srd {declaration.name}", "source": f"{SRD}/runtime/ruleset.yaml", "package_id": srd["id"],
                        "declaration": declaration_json(declaration), **outcome(srd["id"], declaration, srd_bytes)})
    archive = zipfile.ZipFile(ROOT / RIICHI)
    riichi = json.loads(archive.read("manifest.json"))["packages"][0]
    riichi_bytes = archive.read(f"packages/{riichi['id']}/runtime/ruleset.yaml")
    riichi_cases = [{"name": f"riichi {declaration.name}", "declaration": declaration_json(declaration),
                     **outcome(riichi["id"], declaration, riichi_bytes)} for declaration in record_tools(riichi)]
    OUTPUT.write_bytes((json.dumps({"python": sys.version.split()[0], "cases": cases, "shipped": shipped,
                                    "riichi": {"package_id": riichi["id"], "runtime_hex": riichi_bytes.hex(),
                                               "cases": riichi_cases}},
                                   ensure_ascii=True, allow_nan=False) + "\n").encode("ascii"))
    print(f"{len(cases)} fixture cases, {len(shipped)} SRD Lite tools, {len(riichi_cases)} Riichi tools")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
