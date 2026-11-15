"""Completed dismissal observes genuine last-source waking and settles earned Dream damage once."""

import os
from pathlib import Path
from random import Random

import pytest
import yaml
from scripts.srd_full.srd_spell_removal_wakes import bind_runtime_consumers

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from tests import test_srd_lifetime_source_adoption as lifetime
from tests import test_srd_public_spell_casting as public_casting
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_npc_spell_casting import _act, _prepare_mage
from tests.srd_full.test_srd_spell_wave2_conditions import _turn
from tests.srd_full.test_srd_spell_wave3_information import _entity

budget = lifetime.budget
SRD = lifetime.SRD


@pytest.fixture(autouse=True)
def _compiled_owner(monkeypatch):
    yield from lifetime._compiled_owner.__wrapped__(monkeypatch)


def _case(spell="dream"):
    state, data, params = lifetime._case(spell)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"] += [
        _entity("other", [0, 5, 0], participant_ref="other"),
        _entity("mage", [0, -5, 0], participant_ref="mage"),
    ]
    state[f"{SRD}.entities"]["hero"]["prepared_spells"] += ["eyebite", "sleep"]
    data["spellcasting_grants"][0]["spells"] += ["eyebite", "sleep"]
    data["wizard_spellbook"] += ["eyebite", "sleep"]
    for index, part in enumerate(lifetime.cards._casting_headers()["sleep"]["material_parts"]):
        item_id = f"sleep_material_{index}"
        hero = state[f"{SRD}.entities"]["hero"]
        hero["spell_materials"].append({
            "id": item_id, "inventory_item": item_id, "kind": "specific",
            "components": [part["id"]], "quantity": part["quantity"] * 3,
            "value_cp": part["minimum_value_cp"],
        })
        hero["inventory"].append(item_id)
    params.update(
        target="guard",
        dream_messenger="other",
        dream_messenger_willing=True,
        dream_terrifying=True,
        dream_message_words=["Beware"],
    )
    return state, data, params


def _nightmare(state):
    return tuple(state[f"{SRD}.state"]["dream_spell_sources"].values())[-1]


def _eye_params(params, caster="hero"):
    params = params | {
        "spell": "eyebite",
        "slot_resource": "spell_slot_6",
        "material_item_ids": [],
        "targets": ["guard"],
        "targets_max_distance": 10,
        "eyebite_mode": "asleep",
    }
    if caster != "hero":
        params.pop("caster")
        params.pop("slot_resource")
        params["caster_state"] = caster
    return params


def _setup(monkeypatch, *, overlap=False):
    state, data, params = _case()
    lifetime._cast(state, data, params)
    monkeypatch.setattr(dice, "_rng", Random(2))
    budget._run("cast_spell_outside_combat", state, participant_data=data, **_eye_params(params))
    first = state[f"{SRD}.entities"]["hero"]["last_spell_source_cast_id"]
    assert first == "srd_cast:2"
    assert _nightmare(state)["wake_damage_pending"] is True
    assert "unconscious" in state[f"{SRD}.entities"]["guard"]["conditions"]
    second = None
    if overlap:
        budget._run(
            "prepare_srd_creature",
            state,
            creature_id="mage",
            name="NPC Mage",
            source="Synthetic stat block: Wisdom +3, spell attack +7, DC15, CON +6.",
            armor_class=12,
            hit_points=25,
            speed=30,
            initiative_modifier=20,
            size_rank=2,
            constitution_save=6,
            spellcasting_ability="wisdom",
            spellcasting_modifier=3,
            spell_attack_bonus=7,
            spell_save_dc=15,
            spells=[{"spell": "eyebite", "uses_per_day": 3}],
        )
        monkeypatch.setattr(dice, "_rng", Random(2))
        budget._run("cast_spell_outside_combat", state, **_eye_params(params, "mage"))
        second = state[f"{SRD}.entities"]["mage"]["last_spell_source_cast_id"]
        assert second == "srd_cast:3"
    return state, first, second


def _vitals(state):
    target = state[f"{SRD}.entities"]["guard"]
    return target["hp"] + target.get("temp_hp", 0)


def _assert_settled(state, before_vitals):
    source = _nightmare(state)
    assert source["woke"] is True and source["wake_damage_pending"] is False
    assert source["wake_damage_settled"] is True
    assert len(source["wake_damage_roll"]["results"]) == 3
    assert _vitals(state) == before_vitals - source["wake_hp_temp_loss"]
    assert source["wake_hp_temp_loss"] == source["wake_effective_damage"]
    assert source["wake_effective_damage"] > 0
    assert "unconscious" not in state[f"{SRD}.entities"]["guard"]["conditions"]


def test_actual_last_eyebite_dismissal_settles_failed_nightmare_immediately(monkeypatch):
    state, first, _ = _setup(monkeypatch)
    before = _vitals(state)
    budget._run("dismiss_srd_spell", state, caster_state="hero", cast_id=first)
    _assert_settled(state, before)


def test_actual_sleep_needs_its_second_failed_save_before_dismissal_wakes(monkeypatch):
    state, data, params = _case()
    lifetime._cast(state, data, params)
    monkeypatch.setattr(dice, "_rng", Random(2))
    budget._run(
        "cast_spell_outside_combat", state, participant_data=data,
        **(_eye_params(params) | {
            "spell": "sleep", "slot_resource": "spell_slot_1",
            "material_item_ids": [
                item["id"] for item in state[f"{SRD}.entities"]["hero"]["spell_materials"]
                if item["id"].startswith("sleep_material_")
            ],
        }),
    )
    cast_id = state[f"{SRD}.entities"]["hero"]["last_spell_source_cast_id"]
    assert cast_id == "srd_cast:2"
    assert _nightmare(state)["sleep_entered"] is False
    assert "incapacitated" in state[f"{SRD}.entities"]["guard"]["conditions"]
    _turn(state)
    monkeypatch.setattr(dice, "_rng", Random(2))
    budget._run("end_combat_turn", state)
    budget._run("end_combat_encounter", state, end_reason="fighting_stopped")
    budget._run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert _nightmare(state)["wake_damage_pending"] is True
    before = _vitals(state)
    budget._run("dismiss_srd_spell", state, caster_state="hero", cast_id=cast_id)
    _assert_settled(state, before)


def test_other_actual_caster_sleep_preserves_nightmare_until_last_source_ends(monkeypatch):
    state, first, second = _setup(monkeypatch, overlap=True)
    before = _vitals(state)
    budget._run("dismiss_srd_spell", state, caster_state="hero", cast_id=first)
    assert "unconscious" in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert _nightmare(state)["wake_damage_pending"] is True
    assert _vitals(state) == before
    budget._run("dismiss_srd_spell", state, caster_state="mage", cast_id=second)
    _assert_settled(state, before)


def test_binder_repeat_preserves_one_completed_public_boundary():
    text = (budget.ROOT / "runtime/ruleset.yaml").read_text()
    first = bind_runtime_consumers(text)
    second = bind_runtime_consumers(first)
    assert yaml.load(first, Loader=yaml.CSafeLoader) == yaml.load(second, Loader=yaml.CSafeLoader)
    tools = yaml.load(first, Loader=yaml.CSafeLoader)["tools"]
    assert "_dismiss_dream_target" not in str(tools["retire_srd_timed_condition_source"])
    assert "_dismiss_dream_target" not in str(tools["end_srd_concentration_node"])


def test_public_overlap_reopen_dismissal_damage_and_retries(tmp_path, monkeypatch):
    parent = os.environ.get("FC14_SOURCE_PACKAGE_ROOT")
    if os.environ.get("FC14_SOURCE_RUNTIME_ROOT"):
        assert parent
        monkeypatch.setattr(bundled_packages, "_bundled_srd_root", lambda: Path(parent))
        monkeypatch.setattr(public_casting, "bundled_srd_archive", lifetime._composed_archive)
    monkeypatch.setattr(public_casting, "_spell_case", _case)
    path = tmp_path / "dream-dismissal.db"
    with AuthorityOwner.open(path, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(
            owner, "dream", target_overrides={"hp": 1000, "max_hp": 1000}
        )
        begun, _ = _act(owner, "begin_srd_spell_cast", **_begin(params))
        assert begun.status is ActionStatus.COMMITTED, begun.result
        continued, _ = _act(
            owner,
            "continue_srd_spell_cast",
            caster_state="hero",
            elapsed_rounds=10,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        assert continued.status is ActionStatus.COMMITTED, continued.result
        cast, _ = _act(owner, "cast_spell_outside_combat", **params)
        assert cast.status is ActionStatus.COMMITTED, cast.result
        monkeypatch.setattr(dice, "_rng", Random(2))
        eye, _ = _act(owner, "cast_spell_outside_combat", **_eye_params(params))
        assert eye.status is ActionStatus.COMMITTED, eye.result
        prepared, _ = _prepare_mage(owner, [{"spell": "eyebite", "uses_per_day": 3}])
        assert prepared.status is ActionStatus.COMMITTED, prepared.result
        monkeypatch.setattr(dice, "_rng", Random(2))
        second_eye, _ = _act(owner, "cast_spell_outside_combat", **_eye_params(params, "mage"))
        assert second_eye.status is ActionStatus.COMMITTED, second_eye.result
        saved = owner.snapshot("casting")
        assert _nightmare(saved.state)["wake_damage_pending"] is True
        assert saved.state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"]["eyebite"] == 2
        first = saved.state[f"{SRD}.entities"]["hero"]["last_spell_source_cast_id"]
        second = saved.state[f"{SRD}.entities"]["mage"]["last_spell_source_cast_id"]
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        vitals = _vitals(saved.state)
        dismissed, command = _act(owner, "dismiss_srd_spell", caster_state="hero", cast_id=first)
        assert dismissed.status is ActionStatus.COMMITTED, dismissed.result
        middle = owner.snapshot("casting")
        assert _nightmare(middle.state)["wake_damage_pending"] is True
        assert _vitals(middle.state) == vitals
        assert owner.execute_action(command) == dismissed and owner.snapshot("casting") == middle
        final, final_command = _act(owner, "dismiss_srd_spell", caster_state="mage", cast_id=second)
        assert final.status is ActionStatus.COMMITTED, final.result
        ended = owner.snapshot("casting")
        _assert_settled(ended.state, vitals)
        assert owner.execute_action(final_command) == final
        assert owner.snapshot("casting") == ended
        assert owner.execute_action(command) == dismissed and owner.snapshot("casting") == ended
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == ended
