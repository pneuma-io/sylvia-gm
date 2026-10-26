"""Actual spell schemas may retain multiline or compact inline variant rows."""

import re

import pytest
import yaml
from scripts.srd_full.srd_solid_spell_walls import FAMILY, extend_runtime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from tests.srd_full.test_srd_solid_spell_walls import BASE


@pytest.mark.parametrize("inline", [False, True])
def test_force_family_inserts_once_before_actual_multiline_or_inline_variant(inline):
    text = BASE.read_text()
    runtime = _load_runtime(text)
    variants = runtime["definition_row_schemas"]["definitions.spells"]["variants"]
    assert FAMILY not in variants
    first = next(iter(variants))
    if inline:
        compact = yaml.safe_dump(variants[first], default_flow_style=True, width=100000).strip()
        pattern = rf"(?ms)^      {re.escape(first)}:.*?(?=^      [a-z]|^  [a-z])"
        assert len(re.findall(pattern, text)) == 1
        text = re.sub(pattern, f"      {first}: {compact}\n", text, count=1)
    emitted = extend_runtime(text)
    actual = _load_runtime(emitted)["definition_row_schemas"]["definitions.spells"]["variants"]
    assert actual[first] == variants[first]
    assert actual[FAMILY]["properties"]["base_level"] == {"type": "integer", "enum": [5]}
    assert len(re.findall(rf"(?m)^      {FAMILY}:", emitted)) == 1
    assert extend_runtime(emitted) == emitted
