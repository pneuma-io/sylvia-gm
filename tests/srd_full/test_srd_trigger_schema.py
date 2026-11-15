"""An existing ward package must admit the current source-owned Symbol profile."""

from pathlib import Path

import yaml
from scripts.srd_full.srd_spell_triggers import PROFILES, _schema, extend_runtime


def test_existing_ward_schema_is_refreshed_without_changing_other_families():
    path = Path(__file__).resolve().parents[2] / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml"
    text = path.read_text()
    before = yaml.load(text, Loader=yaml.CSafeLoader)
    variants = before["definition_row_schemas"]["definitions.spells"]["variants"]
    stale = dict(_schema())
    stale["properties"] = dict(stale["properties"])
    stale["properties"]["base_level"] = {"type": "integer", "minimum": 1, "maximum": 3}
    old = yaml.safe_dump({"trigger_spell": variants["trigger_spell"]}, sort_keys=False)
    old = "".join("      " + line for line in old.splitlines(True))
    rendered = yaml.safe_dump({"trigger_spell": stale}, sort_keys=False)
    rendered = "".join("      " + line for line in rendered.splitlines(True))
    assert text.count(old) == 1
    text = text.replace(old, rendered, 1)
    updated = extend_runtime(text)
    after = yaml.load(updated, Loader=yaml.CSafeLoader)
    actual = after["definition_row_schemas"]["definitions.spells"]["variants"]
    assert actual["trigger_spell"] == _schema()
    levels = actual["trigger_spell"]["properties"]["base_level"]["enum"]
    assert PROFILES["symbol"]["base_level"] in levels
    assert {k: v for k, v in actual.items() if k != "trigger_spell"} == {
        k: v for k, v in variants.items() if k != "trigger_spell"
    }
    repeated = yaml.load(extend_runtime(updated), Loader=yaml.CSafeLoader)
    assert repeated["definition_row_schemas"]["definitions.spells"]["variants"] == actual
