"""Write contract_vectors.json: Python's argument-contract reads and call flattening.

Run from the checkout root with the snapshot's interpreter:
    PYTHONHASHSEED=0 .venv/Scripts/python.exe -I crates/sylvia-agent/tests/vectors/generate_contract_vectors.py

Cases run `selected_schema`, `unwrap_arguments` and `read_tool_arguments`
(`agent/_argument_contracts.py`) on the SRD Lite package's two spell tools, whose input schemas
carry contracts, and on a small schema with `$defs`. The Rust test reads the same package
record, so only the cases and Python's answers are written here. Reference tooling only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.agent._argument_contracts import (  # noqa: E402
    argument_contract,
    read_tool_arguments,
    selected_schema,
    unwrap_arguments,
)

RECORD = ROOT / "rulesets" / "srd-lite" / "package-record.json"
TOOLS = {"begin_srd_spell_action": 16, "cast_spell_outside_combat": 19}

SMALL = {
    "type": "object",
    "properties": {
        "mode": {"type": "string", "x-sylvia-gm-value-aliases": {"quick": "fast"}},
        "actor": {"type": "string"},
        "speed": {"$ref": "#/$defs/speed"},
        "label": {"$ref": "#/$defs/label", "description": "kept as written"},
        "count": {"type": "integer", "minimum": 1},
    },
    "required": ["mode", "speed", "count"],
    "$defs": {"speed": {"$ref": "#/$defs/number"}, "number": {"type": "number", "maximum": 30},
              "label": {"type": "string"}},
    "x-sylvia-gm-argument-contracts": {
        "version": 1, "selector": "mode", "field": "mode_arguments", "common": ["actor"],
        "by_value": {"fast": ["speed", "label", "count"], "slow": ["count"], "none": []},
    },
}


def schemas() -> dict[str, dict]:
    record = json.loads(RECORD.read_text(encoding="utf-8"))
    found = {"small": SMALL}
    for name, index in TOOLS.items():
        tool = record["tools"][index]
        assert tool["name"] == name, tool["name"]
        found[name] = tool["input_schema"]
    return found


def run(function, *arguments):
    try:
        return {"ok": function(*arguments)}
    except Exception as error:  # noqa: BLE001 - the error is the vector
        return {"error": {"type": type(error).__name__, "message": str(error)}}


def main() -> None:
    found = schemas()
    contracts = {name: argument_contract(schema) for name, schema in found.items()}
    cases: list[dict] = []

    for name, value in (
        ("small", "fast"), ("small", "quick"), ("small", "slow"), ("small", "none"),
        ("small", "nope"), ("small", None), ("small", 3),
        ("begin_srd_spell_action", "acid_arrow"), ("begin_srd_spell_action", "alter_self"),
        ("begin_srd_spell_action", "mage_armour"), ("cast_spell_outside_combat", "acid_splash"),
        ("cast_spell_outside_combat", "hunter_s_mark"),
    ):
        cases.append({"function": "selected_schema", "schema": name, "value": value,
                      "expected": run(selected_schema, found[name], contracts[name], value)})

    spell = "begin_srd_spell_action"
    for name, arguments in (
        ("small", {"mode": "fast", "actor": "a"}),
        ("small", {"mode": "fast", "mode_arguments": None, "count": 2}),
        ("small", {"mode": "fast", "mode_arguments": "x"}),
        ("small", {"mode": "fast", "mode_arguments": {"speed": 10, "count": 2}}),
        ("small", {"mode": "quick", "mode_arguments": {"speed": 40, "count": 0}}),
        ("small", {"mode": "fast", "count": 1, "mode_arguments": {"count": 2, "actor": "a",
                                                                    "mode": "slow", "zz": 1}}),
        ("small", {"mode": "slow", "mode_arguments": {"speed": "fast", "count": "two"}}),
        ("small", {"mode": "none", "mode_arguments": {"speed": "fast"}}),
        ("small", {"mode": "nope", "mode_arguments": {"speed": "fast"}}),
        ("small", {"mode_arguments": {"count": "x"}}),
        (spell, {"spell": "acid_arrow", "caster": "p-1",
                 "spell_arguments": {"material_component_available": True,
                                     "witness_state_ids": ["w-1"]}}),
        (spell, {"spell": "acid_arrow", "caster": "p-1",
                 "spell_arguments": {"material_component_available": "yes",
                                     "witness_state_ids": "w-1", "selected_damage_type": 7}}),
        (spell, {"spell": "acid_arrow", "spell_arguments": {"transformation_form": "x",
                                                            "advantage": True}}),
        (spell, {"spell": "mage_armour", "spell_arguments": {"material_component_available": 1}}),
        (spell, {"spell": "alter_self", "spell_arguments": {"transformation_same_size": "no",
                                                            "target_willing": None}}),
        ("cast_spell_outside_combat", {"spell": "acid_splash", "caster_state": "p-1",
                                       "spell_arguments": {"environment_origin": 3,
                                                           "not_an_input": 1}}),
    ):
        cases.append({"function": "unwrap_arguments", "schema": name, "arguments": arguments,
                      "expected": run(lambda a, s, c: list(unwrap_arguments(dict(a), s, c)),
                                      arguments, found[name], contracts[name])})

    listed = [("tool_a1_begin_srd_spell_action", spell),
              ("tool_b2_cast_spell_outside_combat", "cast_spell_outside_combat"),
              ("tool_c3_small", "small")]
    tools = {alias: (found[name], contracts[name]) for alias, name in listed}
    for parameters in (
        [], "x", {"tool": "nope", "value": "aid"}, {"tool": 5, "value": "aid"},
        {"tool": "mcp__sylvia__begin_srd_spell_action", "value": "aid"},
        {"tool": "tool_b2_cast_spell_outside_combat", "value": "mage_armour"},
        {"tool": "cast_spell_outside_combat", "value": "nope"},
        {"tool": "begin_srd_spell_action"},
        {"tool": "small", "value": "quick"},
        {"tool": "mcp__x__small", "value": "slow"},
    ):
        cases.append({"function": "read_tool_arguments", "tools": listed, "parameters": parameters,
                      "expected": run(read_tool_arguments, parameters, tools)})

    out = Path(__file__).with_name("contract_vectors.json")
    out.write_bytes((json.dumps({"cases": cases}, indent=1, ensure_ascii=False) + "\n").encode())
    print(f"wrote {out.name}: {len(cases)} cases")


if __name__ == "__main__":
    main()
