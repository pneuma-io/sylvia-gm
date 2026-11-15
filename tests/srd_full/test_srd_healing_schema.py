"""The published healing input admits the actual zero-roll receiving boundary."""

import re
from pathlib import Path

import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema


def test_healing_declaration_retains_actual_receiving_amount_minimum():
    source = (
        Path(__file__).resolve().parents[2] / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml"
    ).read_text(encoding="utf-8")
    match = re.search(r"(?ms)^  restore_hit_points:.*?(?=^  [a-z_]+:)", source)
    assert match is not None
    tool = yaml.safe_load(match.group(0))["restore_hit_points"]
    actual = next(p for p in tool["parameters"] if p["name"] == "amount")
    assert actual["minimum"] == 0
    declaration = _tool_schema(tool, tool_name="restore_hit_points")
    assert declaration["properties"]["amount"]["minimum"] == actual["minimum"]
