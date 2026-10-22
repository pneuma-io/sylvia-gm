"""The composition validator retains printed instantaneous extended spell headers."""

import json
from copy import deepcopy

import pytest
from scripts import build_srd_5_2_1_spells as builder
from scripts.srd_full.srd_awaken import PROFILE as AWAKEN_PROFILE
from scripts.srd_full.srd_plant_growth import PROFILE


@pytest.mark.parametrize(
    ("spell", "profile", "casting_time"),
    [
        ("plant_growth", PROFILE, "Action (Overgrowth) or 8 hours (Enrichment)"),
        ("awaken", AWAKEN_PROFILE, "8 hours"),
    ],
)
def test_printed_modes_and_instantaneous_duration_compose(spell, profile, casting_time):
    corpus = json.loads(builder.CORPUS_PATH.read_text(encoding="utf-8"))
    row = next(r for r in builder._spell_rows(corpus, {spell}) if r["id"] == spell)
    header = row["casting_requirements"]
    assert header["casting_time"] == casting_time
    assert header["duration"] == "Instantaneous"
    family = profile["family"]
    runtime = {"definition_row_schemas": {"definitions.spells": {"variants": {family: {}}}}}
    branch = f"_spell_definition.family == '{family}'"
    builder._validate_spell_composition({spell: profile}, {spell: header}, runtime, branch)
    for field, value, message in (
        ("casting_time", "bonus_action", "casting time"),
        ("duration_rounds", 10, "duration"),
        ("concentration", True, "concentration"),
    ):
        changed = deepcopy(profile)
        changed[field] = value
        with pytest.raises(SystemExit, match=message):
            builder._validate_spell_composition({spell: changed}, {spell: header}, runtime, branch)
