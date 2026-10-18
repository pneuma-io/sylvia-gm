"""Representative spell families retain actual per-target outcomes in the shared presentation."""

import json
import re
import subprocess
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from random import Random

import pytest
import yaml

from sylvia_gm import dice
from tests.test_srd_action_budget import _run
from tests.test_srd_attack_budget import SRD, _combat_case


@lru_cache
def _spell_definitions():
    root = Path(__file__).parents[1]
    runtime = yaml.load(
        (root / "rulesets/srd-lite/runtime/ruleset.yaml").read_text(encoding="utf-8"), Loader=yaml.CSafeLoader,
    )
    return runtime["definitions"]["spells"]


@lru_cache
def _casting_headers():
    inventory = json.loads(
        (Path(__file__).parents[1] / "rulesets/srd-lite/content/spell-status-inventory.json")
        .read_text(encoding="utf-8")
    )
    headers = {row.get("identity", row.get("id")): row["casting_requirements"] for row in inventory["spells"]}
    # The final roster preserves 21 implementations with their exact imported
    # SRD headers. Admit only names present in the actual active runtime.
    preserved = json.loads((Path(__file__).parents[1] / "rulesets/srd-lite/provenance/full-srd/spell-status-inventory.json").read_text())
    active = _spell_definitions()
    for row in preserved['spells']:
        identity = row.get('identity', row.get('id'))
        if identity in active and identity not in headers:
            headers[identity] = row['casting_requirements']
    return headers


def _spell_case(spell):
    """Supply admitted casting inputs; exercise actual public package effects and projection."""
    header = _casting_headers()[spell]
    source = next(name for name in (
        "wizard", "cleric", "druid", "bard", "sorcerer", "warlock", "paladin", "ranger",
    ) if name in header["classes"])
    ability = {"wizard": "intelligence", "cleric": "wisdom", "druid": "wisdom",
               "ranger": "wisdom"}.get(source, "charisma")
    state, data = _combat_case([{"id": source, "level": 17}])
    data["name"] = "Mage"
    data["abilities"][ability] = 18
    data["spellcasting_grants"] = [{"source": source, "ability": ability, "spells": [spell]}]
    if source == "wizard" and header["level"] > 0:
        data["wizard_spellbook"] = [spell]
    entities = state[f"{SRD}.entities"]
    entities["hero"].update(
        name="Mage", prepared_spells=[spell], spell_slot_cast_this_turn=False,
        dodging=False,
        resources={f"spell_slot_{level}": 3 for level in range(1, 10)},
    )
    for target_id, name in [("guard", "Guard"), ("other", "Other")]:
        entities[target_id] = deepcopy(entities["guard"])
        entities[target_id].update(
            name=name, hp=19, max_hp=20, temp_hp=1, armor_class=1, creature_type="humanoid",
            speed=30, dodging=False, reaction_available=True, stable=False,
            death_successes=0, death_failures=0, stable_recovery_hours_remaining=0,
            saving_throw_modifiers=dict.fromkeys(data["abilities"], 0),
        )
    entities["guard"]["saving_throw_modifiers"]["dexterity"] = 30
    entities["other"]["saving_throw_modifiers"]["dexterity"] = -30
    level = _spell_definitions()[spell]["base_level"]
    materials = [
        {"id": f"material_{index}", "inventory_item": f"{spell}_material_{index}",
         "kind": "specific", "components": [part["id"]],
         "quantity": part["quantity"] * 3, "value_cp": part["minimum_value_cp"]}
        for index, part in enumerate(_casting_headers()[spell]["material_parts"], 1)
    ]
    entities["hero"]["spell_materials"] = materials
    entities["hero"]["inventory"] += [item["inventory_item"] for item in materials]
    parameters = {
        "spell": spell, "caster": "hero", "caster_state": "hero", "target": "guard",
        "slot_resource": f"spell_slot_{level}" if level else "cantrip",
        "target_distance": 5, "caster_can_see_target": True, "caster_can_hear_target": True,
        "caster_can_touch_target": True, "target_willing": True, "cover": "none",
        "targets_max_distance": 5, "caster_can_see_all_targets": True,
        "all_targets_have_clear_path": True, "all_targets_willing": True,
        "caster_can_touch_all_targets": True, "origin_distance": 0,
        "caster_can_see_origin": True, "origin_has_clear_path": True,
        "all_attack_targets_have_clear_path": True,
        "verbal_component_available": True, "somatic_component_available": True,
        "material_component_available": True,
        "material_item_ids": [item["id"] for item in materials],
    }
    if spell == "ice_storm":
        parameters["environment_origin"] = [23, 0, 0]
    if _spell_definitions()[spell]["family"] == "multi_spell_attack_damage":
        for key in ("target", "target_distance", "caster_can_see_target"):
            parameters.pop(key)
    from tests.srd_fixture_scene import fixture_scene
    state[f"{SRD}.state"]["spatial_scene"] = fixture_scene(entities, {"other": [5, 2, 0]})
    return state, data, parameters


CASES = [
    ("magic_missile", {}, ["Guard"]),
    ("fire_bolt", {}, ["Guard"]),
    ("ice_knife", {"targets": ["guard", "other"]}, ["Guard", "Guard", "Other"]),
    ("scorching_ray", {"attack_targets": ["guard", "other", "guard"]},
     ["Guard", "Other", "Guard"]),
    ("cure_wounds", {}, ["Guard"]),
    ("false_life", {}, ["Mage"]),
    ("aid", {"targets": ["guard", "other"]}, ["Guard", "Other"]),
    ("sacred_flame", {}, ["Guard"]),
    ("burning_hands", {"targets": ["guard", "other"]}, ["Guard", "Other"]),
    ("ice_storm", {"targets": ["guard", "other"]}, ["Guard", "Other"]),
    ("mass_healing_word", {"targets": ["guard", "other"]}, ["Guard", "Other"]),
    ("lesser_restoration", {"selected_restoration_condition": "poisoned"}, ["Guard"]),
    ("power_word_kill", {}, ["Guard"]),
    ("spare_the_dying", {}, ["Guard"]),
    ("shield_of_faith", {}, ["Guard"]),
    ("protection_from_energy", {"selected_damage_type": "fire"}, ["Guard"]),
    ("stoneskin", {}, ["Guard"]),
    ("blindness_deafness", {"targets": ["guard"], "blinded_targets": ["guard"]}, ["Guard"]),
    ("enhance_ability", {"targets": ["guard"], "strength_targets": ["guard"]}, ["Guard"]),
    ("bless", {"targets": ["guard", "other"]}, ["Guard", "Other"]),
    ("bane", {"targets": ["guard", "other"], "forego_save_targets": ["guard"]},
     ["Guard", "Other"]),
    ("guidance", {"selected_skill": "athletics"}, ["Guard"]),
]


@pytest.mark.parametrize("save_modifier,outcome", [(30, "success"), (-30, "failure")])
def test_area_cast_preserves_all_64_actual_saves_and_damage_within_operation_budget(
    save_modifier, outcome,
):
    state, data, parameters = _spell_case("ice_knife")
    entities = state[f"{SRD}.entities"]
    entities["guard"].update(hp=1000, max_hp=1000)
    entities["guard"]["saving_throw_modifiers"]["dexterity"] = save_modifier
    targets = ["guard"] + [f"area-target-{i}" for i in range(63)]
    for index, identity in enumerate(targets[1:]):
        entities[identity] = deepcopy(entities["guard"])
        entities[identity]["name"] = f"Area target {index}"
    result = _run("cast_spell", state, participant_data=data, **(parameters | {
        "targets": targets,
    })).result
    rows = result["result_card"]["details"]
    assert len(rows) == 65
    assert all(outcome in row["value"] for row in rows[1:])
    for identity, row in zip(targets[1:], rows[2:], strict=True):
        target = state[f"{SRD}.entities"][identity]
        applied = 1001 - target["hp"] - target["temp_hp"]
        assert f"Damage {applied} (" in row["value"]


@pytest.mark.parametrize("spell,choices,names", CASES, ids=[c[0] for c in CASES])
def test_spell_families_record_each_target_and_the_actual_resource_change(
    monkeypatch, spell, choices, names,
):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, parameters = _spell_case(spell)
    if spell == "spare_the_dying":
        state[f"{SRD}.entities"]["guard"].update(hp=0, conditions=["unconscious"])
    if spell == "lesser_restoration":
        state[f"{SRD}.entities"]["guard"]["conditions"] = ["poisoned"]
    before = deepcopy(state)
    result = _run("cast_spell", state, participant_data=data, **(parameters | choices)).result
    card = result.get("result_card")
    assert card is not None
    assert card["title"] == _casting_headers()[spell]["name"]
    summary = {row["label"]: row["value"] for row in card["summary"]}
    assert summary["Caster"] == "Mage"
    resource = parameters["slot_resource"]
    expected_spent = 0 if resource == "cantrip" else (
        before[f"{SRD}.entities"]["hero"]["resources"][resource]
        - state[f"{SRD}.entities"]["hero"]["resources"][resource]
    )
    assert expected_spent == (0 if resource == "cantrip" else 1)
    assert summary["Spell slots spent"] == expected_spent
    if resource != "cantrip":
        assert summary["Slots remaining"] == state[f"{SRD}.entities"]["hero"]["resources"][resource]
    calculations = [row for row in card["details"] if "equation" not in row or spell == "ice_knife"]
    assert [row["label"] for row in calculations] == names
    equations = [row for row in card["details"] if "equation" in row]
    if spell in {"fire_bolt", "scorching_ray", "sacred_flame", "burning_hands", "blindness_deafness"}:
        assert equations, "Every actual roll must retain its saved numeric equation"
        # Standalone mechanic metadata supports scalar rolls; the normal owner
        # projection additionally preserves full face arrays (compiled UI proof).
        assert all(row["equation"].get("rolls") or
                   isinstance(row["equation"].get("roll"), (int, float)) for row in equations)
        assert all(row["equation"]["total"] ==
                   row["equation"]["roll"] + row["equation"].get("modifier", 0)
                   for row in equations)
    assert all(row["value"] for row in card["details"])
    assert all("{" not in row["value"] for row in card["details"])
    saved = deepcopy(card)
    state[f"{SRD}.entities"]["guard"].update(name="Changed", hp=999)
    assert card == saved


@pytest.mark.parametrize("outcome", ["hit", "miss", "critical", "resisted", "immune"])
def test_spell_attack_card_matches_actual_damage_and_adjusted_roll(monkeypatch, outcome):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, parameters = _spell_case("fire_bolt")
    entities = state[f"{SRD}.entities"]
    entities["hero"]["exhaustion_level"] = 2
    target = entities["guard"]
    target.update(hp=1000, max_hp=1000, armor_class=99 if outcome == "miss" else 1,
                  conditions=["unconscious"] if outcome == "critical" else [])
    target["damage_resistances"] = ["fire"] if outcome == "resisted" else []
    target["damage_immunities"] = ["fire"] if outcome == "immune" else []
    before = deepcopy(target)
    result = _run("cast_spell", state, participant_data=data, **parameters).result
    after = state[f"{SRD}.entities"]["guard"]
    text = result["result_card"]["details"][0]["value"]
    damage = before["hp"] - after["hp"] + before["temp_hp"] - after["temp_hp"]
    assert f"Damage {damage} (" in text
    assert "Armor Class" not in text
    roll = result["context"]["_spell_attack"]
    assert f"selected {roll['total']} + (6) = {roll['total'] + 6}" in text
    assert {"miss": "Miss", "critical": "Critical hit"}.get(outcome, "Hit") in text
    if outcome == "miss":
        assert "Damage dice" not in text


@pytest.mark.parametrize("spell", ["burning_hands", "ice_storm", "scorching_ray"])
def test_multi_target_damage_cards_keep_distinct_actual_outcomes(monkeypatch, spell):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, parameters = _spell_case(spell)
    for entity in state[f"{SRD}.entities"].values():
        entity.update(hp=1000, max_hp=1000)
    state[f"{SRD}.entities"]["other"]["damage_immunities"] = ["fire", "cold", "bludgeoning"]
    choices = next(c[1] for c in CASES if c[0] == spell)
    before = deepcopy(state[f"{SRD}.entities"])
    result = _run("cast_spell", state, participant_data=data, **(parameters | choices)).result
    rows = [row for row in result["result_card"]["details"] if "equation" not in row]
    for identity, name in [("guard", "Guard"), ("other", "Other")]:
        after = state[f"{SRD}.entities"][identity]
        applied = before[identity]["hp"] - after["hp"]
        applied += before[identity]["temp_hp"] - after["temp_hp"]
        recorded = sum(int(re.search(r"Damage (\d+) \(", row["value"])[1])
                       for row in rows if row["label"] == name)
        assert recorded == applied
    if spell != "scorching_ray":
        assert "success" in rows[0]["value"]
        assert "failure" in rows[1]["value"]


@pytest.mark.parametrize("save_modifier", [30, -30], ids=["saved", "failed"])
def test_large_attack_and_area_cast_keeps_primary_and_every_area_result(save_modifier):
    state, data, parameters = _spell_case("ice_knife")
    entities = state[f"{SRD}.entities"]
    targets = ["guard"] + [f"target-{index}" for index in range(63)]
    entities["guard"].update(hp=1000, max_hp=1000)
    entities["guard"]["saving_throw_modifiers"]["dexterity"] = save_modifier
    for index, identity in enumerate(targets[1:]):
        entities[identity] = deepcopy(entities["guard"])
        entities[identity]["name"] = f"Target {index}"
    result = _run("cast_spell", state, participant_data=data,
                  **(parameters | {"targets": targets})).result
    card = result.get("result_card")
    assert card is not None
    assert len(card["details"]) == 65
    assert card["details"][0]["label"] == card["details"][1]["label"] == "Guard"
    assert card["details"][-1]["label"] == "Target 62"
    assert len(card["summary"]) <= 16
    source = Path(__file__).parents[1] / "src-tauri/frontend/app.js"
    script = """
const fs = require('fs'), assert = require('node:assert/strict');
global.window = {}; global.document = {addEventListener(){}};
eval(fs.readFileSync(process.argv[1], 'utf8'));
const html = window.SylviaGMFrontend.toolResultMarkup({
  tool_name: 'cast_spell', result: {status: 'committed', result_card: JSON.parse(process.argv[2])}
}, '');
assert(html.includes('Target 62'));
assert(html.includes('result-card-compact'));
assert(!html.includes('Operation completed'));
"""
    subprocess.run(["node", "-e", script, str(source), json.dumps(card)], check=True,
                   capture_output=True, text=True)


@pytest.mark.parametrize("spell", ["cure_wounds", "mass_healing_word"])
def test_healing_cards_keep_each_actual_capped_gain(spell):
    state, data, parameters = _spell_case(spell)
    state[f"{SRD}.entities"]["other"]["hp"] = 20
    choices = {"targets": ["guard", "other"]} if spell == "mass_healing_word" else {}
    result = _run("cast_spell", state, participant_data=data, **(parameters | choices)).result
    rows = result["result_card"]["details"]
    assert "HP restored 1." in rows[0]["value"]
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 20
    if len(rows) == 2:
        assert "HP restored 0." in rows[1]["value"]
        assert state[f"{SRD}.entities"]["other"]["hp"] == 20


def test_bane_card_distinguishes_foregone_and_successful_saves_without_inventing_dice():
    state, data, parameters = _spell_case("bane")
    state[f"{SRD}.entities"]["other"]["saving_throw_modifiers"]["charisma"] = 30
    result = _run("cast_spell", state, participant_data=data,
                  **(parameters | {"targets": ["guard", "other"],
                                   "forego_save_targets": ["guard"]})).result
    rows = result["result_card"]["details"]
    assert "No roll." in rows[0]["value"]
    assert "Subtract D4" in rows[0]["value"]
    assert "success" in rows[1]["value"]
    assert "No penalty applied" in rows[1]["value"]
    assert state[f"{SRD}.entities"]["hero"]["concentration_target_state_ids"] == ["guard"]
