"""Casting derives permissions from saved SRD grants, not an arbitrary per-spell ability."""

from copy import deepcopy

import pytest
from scripts.srd_spell_grants import grant_policies

from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.test_srd_action_budget import _run
from tests.test_srd_spell_result_cards import _spell_case


@pytest.mark.parametrize("problem", ["class", "ability", "spell_level", "spellbook", "split"])
def test_public_cast_rejects_illegal_source_without_spending_any_resource(problem):
    state, data, parameters = _spell_case("acid_arrow")
    grant = data["spellcasting_grants"][0]
    if problem == "class":
        data["classes"] = [{"id": "fighter", "level": 17}]
    elif problem == "ability":
        grant["ability"] = "strength"
    elif problem == "spell_level":
        data["classes"] = [{"id": "wizard", "level": 1}, {"id": "cleric", "level": 16}]
    elif problem == "spellbook":
        data["wizard_spellbook"] = []
    else:
        data["spellcasting_grants"].append(deepcopy(grant))
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("cast_spell", state, participant_data=data, **parameters)
    assert state == before


def _validate(data, spell, source="", *, complete=False):
    state, _, _ = _spell_case("fire_bolt")
    return _run(
        "validate_srd_spell_grants", state, participant_data=data,
        caster="hero", spell=spell, selected_source=source, complete=complete,
    ).result["context"]


@pytest.mark.parametrize("source", ["magic_initiate_wizard", "tiefling_abyssal", "druidic"])
def test_complete_casting_requires_feat_lineage_and_automatic_class_spells(source):
    _, data, _ = _spell_case("fire_bolt")
    data.update(level=1, classes=[{"id": "fighter", "level": 1}], species="human",
                origin_feats=[], wizard_spellbook=[])
    grant = {"source": source, "ability": "wisdom", "spells": []}
    data["spellcasting_grants"] = [grant]
    if source == "magic_initiate_wizard":
        data["origin_feats"] = ["magic_initiate"]
        grant["spells"] = ["fire_bolt", "ray_of_frost", "magic_missile"]
    elif source == "tiefling_abyssal":
        data.update(level=3, classes=[{"id": "fighter", "level": 3}], species="tiefling")
        grant["spells"] = ["poison_spray", "thaumaturgy", "ray_of_sickness"]
    else:
        data["classes"] = [{"id": "druid", "level": 1}]
        grant["spells"] = ["speak_with_animals"]
        data["spellcasting_grants"].append({
            "source": "druid", "ability": "wisdom",
            "spells": ["starry_wisp", "guidance", "cure_wounds", "healing_word",
                       "longstrider", "thunderwave"],
        })
    _validate(data, "", complete=True)
    incomplete = deepcopy(data)
    incomplete["spellcasting_grants"][0]["spells"].pop()
    with pytest.raises(RulesetPreconditionRejected, match="Complete"):
        _validate(incomplete, "", complete=True)
    missing = deepcopy(data)
    missing["spellcasting_grants"].pop(0)
    with pytest.raises(RulesetPreconditionRejected):
        _validate(missing, "", complete=True)


def test_multiclass_duplicate_spell_requires_a_source_and_keeps_its_ability_and_focus():
    _, data, _ = _spell_case("cure_wounds")
    data.update(level=4, classes=[{"id": "cleric", "level": 2}, {"id": "bard", "level": 2}])
    data["spellcasting_grants"] = [
        {"source": "cleric", "ability": "wisdom", "spells": ["cure_wounds"]},
        {"source": "bard", "ability": "charisma", "spells": ["cure_wounds"]},
    ]
    with pytest.raises(RulesetPreconditionRejected, match="multiple sources"):
        _validate(data, "cure_wounds")
    for source, ability, focus in [("cleric", "wisdom", "holy_symbol"),
                                   ("bard", "charisma", "musical_instrument")]:
        result = _validate(data, "cure_wounds", source)
        assert result["_grant_ability"] == ability
        assert result["_grant_focus"] == [focus]


def test_magic_initiate_uses_one_list_and_one_ability_without_class_focus_permission():
    _, data, _ = _spell_case("fire_bolt")
    data.update(level=1, classes=[{"id": "fighter", "level": 1}], origin_feats=["magic_initiate"])
    data["spellcasting_grants"] = [{
        "source": "magic_initiate_wizard", "ability": "wisdom",
        "spells": ["fire_bolt", "ray_of_frost", "magic_missile"],
    }]
    result = _validate(data, "magic_missile")
    assert result["_grant_ability"] == "wisdom" and result["_grant_focus"] == []
    for extra in ("cure_wounds", "acid_arrow", "burning_hands"):
        invalid = deepcopy(data)
        invalid["spellcasting_grants"][0]["spells"].append(extra)
        with pytest.raises(RulesetPreconditionRejected):
            _validate(invalid, extra)
    data["origin_feats"] = []
    with pytest.raises(RulesetPreconditionRejected, match="Magic Initiate"):
        _validate(data, "magic_missile")


def test_source_preparation_counts_cannot_be_exceeded_with_otherwise_legal_spells():
    _, data, _ = _spell_case("magic_missile")
    data.update(level=1, classes=[{"id": "wizard", "level": 1}])
    spells = ["magic_missile", "burning_hands", "shield", "longstrider", "sleep"]
    data["wizard_spellbook"] = spells
    data["spellcasting_grants"][0]["spells"] = spells
    with pytest.raises(RulesetPreconditionRejected, match="prepared-spell limit"):
        _validate(data, "magic_missile")
    data["spellcasting_grants"][0]["spells"] = spells[:4]
    assert _validate(data, "magic_missile")["_grant_ability"] == "intelligence"


def test_species_grants_respect_lineage_level_and_do_not_require_a_caster_class():
    _, data, _ = _spell_case("fire_bolt")
    data.update(level=3, classes=[{"id": "fighter", "level": 3}], species="tiefling")
    data["spellcasting_grants"] = [{
        "source": "tiefling_abyssal", "ability": "intelligence",
        "spells": ["poison_spray", "thaumaturgy", "ray_of_sickness"],
    }]
    assert _validate(data, "ray_of_sickness")["_grant_ability"] == "intelligence"
    data["spellcasting_grants"][0]["spells"].append("hold_person")
    with pytest.raises(RulesetPreconditionRejected, match="actual class or lineage level"):
        _validate(data, "hold_person")
    data["level"] = data["classes"][0]["level"] = 5
    _validate(data, "hold_person")
    data["species"] = "human"
    with pytest.raises(RulesetPreconditionRejected, match="species"):
        _validate(data, "hold_person")


def test_bard_magical_secrets_and_lore_are_distinct_grants_with_real_prerequisites():
    _, data, _ = _spell_case("fireball")
    data.update(level=6, classes=[{"id": "bard", "level": 6}])
    data["spellcasting_grants"] = [
        {"source": "magical_discoveries", "ability": "charisma", "spells": ["fireball"]},
    ]
    with pytest.raises(RulesetPreconditionRejected, match="subclass"):
        _validate(data, "fireball")
    data["subclasses"] = {"bard": "college_of_lore"}
    _validate(data, "fireball")
    data["spellcasting_grants"][0]["source"] = "bard"
    with pytest.raises(RulesetPreconditionRejected, match="actual class"):
        _validate(data, "fireball")
    data["level"] = data["classes"][0]["level"] = 10
    _validate(data, "fireball")
    data["spellcasting_grants"][0]["spells"].append("fire_bolt")
    with pytest.raises(RulesetPreconditionRejected, match="actual class"):
        _validate(data, "fire_bolt")


def test_all_class_tables_preserve_twenty_source_rows_and_warlock_spell_level_cap():
    policies = grant_policies()
    for name in ("bard", "cleric", "druid", "paladin", "ranger", "sorcerer", "warlock", "wizard"):
        row = policies[name]
        assert all(len(row[key]) == 20 for key in ("cantrips", "prepared", "max_level"))
    assert policies["wizard"]["prepared"][-1] == 25
    assert policies["sorcerer"]["prepared"][0] == 2
    assert policies["paladin"]["max_level"][3:5] == [1, 2]
    assert policies["warlock"]["max_level"][-1] == 5


@pytest.mark.parametrize("source,spells", [("tiefling_infernal", ["fire_bolt", "thaumaturgy", "hellish_rebuke"]), ("elf_high", ["fire_bolt", "detect_magic"])])
def test_archived_lineage_choices_are_flagged_without_substitution(source, spells):
    _, data, _ = _spell_case("fire_bolt")
    data.update(level=3, classes=[{"id":"fighter", "level":3}], species="tiefling" if source.startswith("tiefling") else "elf", spellcasting_grants=[{"source":source,"ability":"wisdom","spells":spells}])
    before=deepcopy(data)
    with pytest.raises(RulesetPreconditionRejected, match="unavailable.*locked117"):
        _validate(data,"",complete=True)
    assert data==before
