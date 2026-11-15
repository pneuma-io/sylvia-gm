"""Repeat generation keeps the actual retained public declaration singular."""

from copy import deepcopy
from pathlib import Path

import yaml
from scripts.srd_full.srd_environment_retained_damage import _bind_public_lightning_point


def test_actual_public_lightning_choice_rebind_is_singular_and_preserves_other_fields():
    root = Path(__file__).parents[1]
    runtime = yaml.load(
        (root / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_bytes(), Loader=yaml.CSafeLoader
    )
    public = deepcopy(runtime["tools"]["cast_spell"])
    other = [p for p in public["parameters"] if p["name"] != "environment_lightning_point"]
    _bind_public_lightning_point(public)
    first = deepcopy(public)
    _bind_public_lightning_point(public)
    assert public == first
    assert [p for p in public["parameters"] if p["name"] != "environment_lightning_point"] == other
    assert sum(p["name"] == "environment_lightning_point" for p in public["parameters"]) == 1
