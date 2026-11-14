"""Executable SRD spells must agree with their admitted headers and runtime consumers."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_spells import (
    _validate_definition_profiles,
    _validate_spell_composition,
)

ROOT = Path(__file__).parents[1] / "rulesets/dnd-srd-5.2.1"


@pytest.fixture(scope="module")
def catalogue():
    runtime_text = (ROOT / "runtime/ruleset.yaml").read_text(encoding="utf-8")
    runtime = yaml.safe_load(runtime_text)
    headers = {
        row["id"]: row["casting_requirements"]
        for row in json.loads((ROOT / "content/spell-status-inventory.json").read_text(
            encoding="utf-8"
        ))["spells"]
    }
    return runtime["definitions"]["spells"], headers, runtime, runtime_text


@pytest.mark.parametrize(
    "spell,field,bad,reason",
    [
        ("harm", "family", "unknown_family", "no admitted executable family"),
        ("harm", "casting_time", "bonus_action", "casting time disagrees"),
        ("bless", "duration_rounds", 600, "duration disagrees"),
        ("bless", "concentration", False, "concentration disagrees"),
        ("harm", "range_feet", 120, "range disagrees"),
    ],
)
def test_catalogue_rejects_incoherent_record(catalogue, spell, field, bad, reason):
    definitions, headers, runtime, text = catalogue
    changed = deepcopy(definitions)
    changed[spell][field] = bad
    with pytest.raises(SystemExit, match=reason):
        _validate_spell_composition(changed, headers, runtime, text)


def test_every_executable_record_has_coherent_header_and_effect_consumer(catalogue):
    definitions, headers, runtime, text = catalogue
    _validate_spell_composition(definitions, headers, runtime, text)
    inventory = json.loads((ROOT / "content/spell-status-inventory.json").read_text(
        encoding="utf-8"
    ))
    assert len(inventory["spells"]) == inventory["spell_count"] == len(headers)
    assert runtime["definitions"]["spell_casting"] == headers
    assert {row["id"] for row in inventory["spells"]
            if row["mechanics_status"] == "implemented"} == set(definitions)
    assert {row["id"] for row in inventory["spells"]
            if row["mechanics_status"] == "pending"}.isdisjoint(definitions)
    sources = json.loads((ROOT / "content/spell-mechanics-source.json").read_text(
        encoding="utf-8"
    ))["spells"]
    assert set(sources) == set(definitions)
    for spell, source in sources.items():
        expected = (
            "PARTIAL_MECHANICAL_MAPPING" if source["not_yet_mapped"]
            else "COMPLETE_MECHANICAL_MAPPING"
        )
        assert source["claim_status"] == expected, spell


def test_summon_damage_choice_must_be_a_saved_resistance(catalogue):
    definitions, _, _, _ = catalogue
    changed = deepcopy(definitions)
    changed["summon_dragon"]["selectable_damage_types"] = ["thunder"]
    with pytest.raises(SystemExit, match="Summoned creature definition"):
        _validate_definition_profiles(changed)


def test_timed_death_prevention_must_preserve_its_replacement(catalogue):
    definitions, _, _, _ = catalogue
    changed = deepcopy(definitions)
    changed["death_ward"]["replacement_hit_points"] = 2
    with pytest.raises(SystemExit, match="Timed death-prevention definition"):
        _validate_definition_profiles(changed)


def test_healing_pool_requires_positive_bounded_allocation(catalogue):
    definitions, _, _, _ = catalogue
    changed = deepcopy(definitions)
    changed["mass_heal"]["healing_pool"] = 0
    with pytest.raises(SystemExit, match="Healing-pool definition"):
        _validate_definition_profiles(changed)


def test_threshold_condition_requires_saved_repeat_save_profile(catalogue):
    definitions, _, _, _ = catalogue
    assert definitions["power_word_stun"]["hit_point_threshold"] == 150
    changed = deepcopy(definitions)
    changed["power_word_stun"]["repeat_save_timing"] = "none"
    with pytest.raises(SystemExit, match="Threshold-condition definition"):
        _validate_definition_profiles(changed)


def test_area_condition_has_no_damage_or_repeated_save(catalogue):
    definitions, _, _, _ = catalogue
    assert definitions["color_spray"]["failed_save_condition_expiry"] == "caster_next_turn_end"
    changed = deepcopy(definitions)
    changed["color_spray"]["base_dice"] = 2
    with pytest.raises(SystemExit, match="damage/condition composition"):
        _validate_definition_profiles(changed)
