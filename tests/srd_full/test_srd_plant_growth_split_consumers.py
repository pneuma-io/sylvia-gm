"""Casting and final route hooks compose independently without changing their owning bodies."""

from pathlib import Path

import yaml
from scripts.srd_full.srd_plant_growth import extend_runtime
from scripts.srd_full.srd_plant_growth_consumers import (
    bind_casting_consumers,
    bind_consumers,
    bind_movement_consumers,
)


def test_split_casting_capture_then_final_movement_preserves_existing_owners():
    parent = (
        Path(__file__).parents[1] / ".scratch/magic-jar-incoming-parent-v1/runtime/ruleset.yaml"
    )
    text = extend_runtime(parent.read_text())
    original = yaml.load(text, Loader=yaml.CSafeLoader)
    casting = bind_casting_consumers(text)
    parsed_casting = yaml.load(casting, Loader=yaml.CSafeLoader)
    captured_cast = parsed_casting["tools"]["cast_spell"]
    assert "_growth_cast_timing" in str(captured_cast)
    assert "plant_growth_mode == 'enrichment'" in str(captured_cast)
    assert (
        parsed_casting["tools"]["srd_information_movement_cost"]
        == original["tools"]["srd_information_movement_cost"]
    )
    final = bind_movement_consumers(casting)
    parsed_final = yaml.load(final, Loader=yaml.CSafeLoader)
    assert parsed_final["tools"]["cast_spell"] == captured_cast
    assert parsed_final == yaml.load(bind_consumers(text), Loader=yaml.CSafeLoader)
    assert parsed_final == yaml.load(
        bind_movement_consumers(bind_casting_consumers(final)), Loader=yaml.CSafeLoader
    )
