"""One source-derived admission catalogue serves ordinary and exceptional spell headers."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from scripts.build_srd_5_2_1_spells import _spell_rows

from sylvia_gm.application.package_runtime import PackageRuntimeError
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.test_srd_action_budget import _run
from tests.test_srd_spell_result_cards import _spell_case

ROOT = Path(__file__).resolve().parents[1]


def _rows():
    corpus = json.loads(
        (ROOT / "rulesets/srd-lite/content/corpus/spells.json").read_text(encoding="utf-8")
    )
    return {row["id"]: row for row in _spell_rows(corpus, set())}


def test_entire_spell_catalogue_has_complete_source_derived_casting_headers():
    rows = _rows()
    assert len(rows) == 339
    classes = {"bard", "cleric", "druid", "paladin", "ranger", "sorcerer", "warlock", "wizard"}
    for row in rows.values():
        requirements = row["casting_requirements"]
        assert 0 <= requirements["level"] <= 9
        assert set(requirements["classes"]) <= classes
        assert requirements["classes"]
        assert requirements["casting_time"] and requirements["range"] and requirements["duration"]
        assert set(requirements["components"]) <= {"verbal", "somatic", "material"}
        assert bool(requirements["material"]) == ("material" in requirements["components"])
        assert requirements["focus_substitution"] == (
            bool(requirements["material"])
            and not requirements["material_has_cost"]
            and not requirements["material_consumed"]
        )


def test_wrapped_headers_and_material_exceptions_keep_the_actual_requirements():
    rows = _rows()
    aid = rows["aid"]["casting_requirements"]
    assert aid["classes"] == ["bard", "cleric", "druid", "paladin", "ranger"]
    assert aid["focus_substitution"] is True
    word = rows["healing_word"]["casting_requirements"]
    assert word["components"] == ["verbal"]
    assert word["material"] == ""
    assert word["casting_time"] == "Bonus Action"
    assert rows["alarm"]["casting_requirements"]["ritual"] is True
    assert rows["chromatic_orb"]["casting_requirements"]["material_has_cost"] is True
    assert rows["chromatic_orb"]["casting_requirements"]["material_consumed"] is False
    clone = rows["clone"]["casting_requirements"]
    assert clone["material_consumed"] is True
    assert "1,000+ GP" in clone["material"] and "2,000+ GP" in clone["material"]
    assert clone["focus_substitution"] is False
    assert [(p["minimum_value_cp"], p["consumed"]) for p in clone["material_parts"]] == [
        (100000, True), (200000, False),
    ]
    assert rows["warding_bond"]["casting_requirements"]["material_parts"][0]["quantity"] == 2
    assert rows["gentle_repose"]["casting_requirements"]["material_parts"] == [{
        "id": "gentle_repose.1", "quantity": 2, "scale": "fixed",
        "minimum_value_cp": 1, "consumed": True,
    }]
    # Copper prices, consumed coins and per-target/per-piece requirements cannot
    # accidentally enter the ordinary focus/pouch substitution path.
    for spell in ("true_strike", "gentle_repose", "astral_projection", "warding_bond"):
        assert rows[spell]["casting_requirements"]["focus_substitution"] is False


def test_missing_component_header_fails_generation_instead_of_implying_no_components():
    corpus = json.loads(
        (ROOT / "rulesets/srd-lite/content/corpus/spells.json").read_text(encoding="utf-8")
    )
    header = "Components: V, S, M (powdered rhubarb leaf)"
    page = next(p for p in corpus["page_records"] if header in p["text"])
    page["text"] = page["text"].replace(header, "Missing header")
    with pytest.raises(ValueError, match="acid_arrow.*Components"):
        _spell_rows(corpus, set())


@pytest.mark.parametrize("parameter", ["verbal_component_available", "somatic_component_available"])
def test_unavailable_required_component_rejects_without_spending_or_mutating(parameter):
    state, data, parameters = _spell_case("fire_bolt")
    parameters.update(verbal_component_available=True, somatic_component_available=True)
    parameters[parameter] = False
    before = deepcopy(state)
    with pytest.raises((RulesetPreconditionRejected, PackageRuntimeError), match="component"):
        _run("cast_spell", state, participant_data=data, **parameters)
    assert state == before


def test_verbal_only_spell_does_not_require_a_gesture():
    state, data, parameters = _spell_case("healing_word")
    parameters.update(verbal_component_available=True, somatic_component_available=False)
    result = _run("cast_spell", state, participant_data=data, **parameters)
    assert result.result["status"] == "committed"


def test_unknown_required_component_does_not_default_to_available():
    state, data, parameters = _spell_case("fire_bolt")
    parameters.pop("verbal_component_available", None)
    parameters.update(somatic_component_available=True)
    before = deepcopy(state)
    with pytest.raises(
        (RulesetPreconditionRejected, PackageRuntimeError), match="Verbal component",
    ):
        _run("cast_spell", state, participant_data=data, **parameters)
    assert state == before


def test_combat_cast_defaults_somatic_available_without_supplying_it():
    state, data, parameters = _spell_case("fire_bolt")
    parameters.pop("somatic_component_available", None)
    parameters["verbal_component_available"] = True
    result = _run("cast_spell", state, participant_data=data, **parameters)
    assert result.result["status"] == "committed"


def test_long_cast_defaults_somatic_at_start_and_continuation_but_preserves_false():
    state, data, parameters = _spell_case("mending")
    state["sylvia.srd-lite.bg3.combat"].update(
        encounter_active=False, turn_active=False, active_actor=None, phase="exploration",
    )
    begin = {key: parameters[key] for key in (
        "spell", "caster", "caster_state", "slot_resource", "verbal_component_available",
        "material_component_available", "material_item_ids",
    )}
    _run("begin_srd_spell_cast", state, participant_data=data, **begin)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Somatic component"):
        _run(
            "continue_srd_spell_cast", state, caster_state="hero", uninterrupted=True,
            verbal_component_available=True, somatic_component_available=False,
            material_component_available=True,
        )
    assert state == before
    _run(
        "continue_srd_spell_cast", state, caster_state="hero", uninterrupted=True,
        elapsed_rounds=1, verbal_component_available=True, material_component_available=True,
    )
    hero = state["sylvia.srd-lite.bg3.entities"]["hero"]
    previous = before["sylvia.srd-lite.bg3.entities"]["hero"]
    assert (
        hero["extended_casting"]["progress_rounds"]
        == previous["extended_casting"]["progress_rounds"] + 1
    )
    assert hero["resources"] == previous["resources"]


def test_missing_spell_grant_has_an_actionable_rejection_without_dynamic_key_failure():
    state, data, parameters = _spell_case("fire_bolt")
    data.pop("spellcasting_grants")
    before = deepcopy(state)
    with pytest.raises(
        (RulesetPreconditionRejected, PackageRuntimeError), match="spellcasting grants",
    ):
        _run("cast_spell", state, participant_data=data, **parameters)
    assert state == before
