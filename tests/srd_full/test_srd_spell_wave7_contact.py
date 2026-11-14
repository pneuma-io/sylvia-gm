"""A real failed Contact cast is cured through paid Greater Restoration's exact source owner."""

from copy import deepcopy
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_spell_result_cards import _spell_case
from tests.srd_full.test_srd_spell_wave3_information import SRD, _entity
from tests.srd_full.test_srd_spell_wave4_divination import _case
from tests.srd_full.test_srd_spell_wave7_feedback import _card_values


def _healer(state):
    """Prepare the paid recovery owner with its admitted consumed component."""
    supplied, _, cure = _spell_case("greater_restoration")
    supplies = deepcopy(supplied[f"{SRD}.entities"]["hero"]["spell_materials"])
    supplies[0]["quantity"] = 1
    _run(
        "prepare_srd_creature",
        state,
        creature_id="healer",
        name="Public fixture healer",
        source="Synthetic public healer with actual Greater Restoration dust",
        armor_class=12,
        hit_points=25,
        speed=30,
        initiative_modifier=0,
        size_rank=2,
        constitution_save=4,
        spellcasting_ability="wisdom",
        spellcasting_modifier=4,
        spell_attack_bonus=8,
        spell_save_dc=16,
        spells=[{"spell": "greater_restoration", "uses_per_day": 1}],
        inventory=[item["inventory_item"] for item in supplies],
        spell_materials=supplies,
    )
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
        _entity("healer", [0, 5, 0], participant_ref="healer")
    )
    cure.pop("caster")
    cure.pop("slot_resource")
    cure.update(caster_state="healer", target="hero", recovery_choice="contact_other_plane")
    return cure


@pytest.mark.parametrize("temp_hp,resistant", [(0, False), (100, True)])
def test_actual_failed_contact_paid_cure_preserves_unrelated_incapacity(
    monkeypatch, temp_hp, resistant
):
    monkeypatch.setattr(dice, "_rng", Random(2))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, parameters = _case("contact_other_plane")
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(
        hp=500,
        max_hp=500,
        temp_hp=temp_hp,
        damage_resistances=["psychic"] if resistant else [],
    )
    data["saving_throw_proficiencies"] = ["intelligence", "wisdom"]
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(parameters))
    _run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        elapsed_rounds=10,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    result = _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["resources"]["spell_slot_5"] == 2
    loss = 500 + temp_hp - hero["hp"] - hero["temp_hp"]
    assert loss > 0 and "incapacitated" in hero["conditions"]
    assert list(_card_values(result.result["result_card"], "Damage")) == [loss]
    if resistant:
        assert hero["hp"] == 500
    _run(
        "install_srd_timed_condition_source",
        state,
        target_state="hero",
        lasting_state="state",
        spell="synthetic_unrelated",
        condition="incapacitated",
        save_dc=15,
        duration_rounds=0,
        repeat_save_timing="none",
        save_ability="wisdom",
    )
    cure = _healer(state)
    _run("cast_spell_outside_combat", state, **cure)
    sources = state[f"{SRD}.state"]["timed_condition_sources"].values()
    assert any(
        source["installed"] and source["spell_id"] == "synthetic_unrelated" for source in sources
    )
    assert not any(
        source["installed"] and source["spell_id"] == "contact_other_plane" for source in sources
    )
    assert "incapacitated" in state[f"{SRD}.entities"]["hero"]["conditions"]
    healer = state[f"{SRD}.entities"]["healer"]
    assert healer["spell_uses_remaining"]["greater_restoration"] == 0
    assert healer["spell_materials"][0]["quantity"] == 0
    saved = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("cast_spell_outside_combat", state, **cure)
    assert state == saved


def test_public_failed_contact_feedback_reopens_retries_then_paid_cure(tmp_path, monkeypatch):
    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus
    from tests import test_srd_public_spell_casting as public
    from tests.srd_full.test_srd_npc_spell_casting import _act

    monkeypatch.setattr(dice, "_rng", Random(2))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    cure = {}

    def case(spell):
        state, data, parameters = _case(spell)
        cure.update(_healer(state))
        return state, data, parameters

    monkeypatch.setattr(public, "_spell_case", case)
    database = tmp_path / "contact-feedback-cure.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(
            owner,
            "contact_other_plane",
            entity_overrides={
                "hp": 500,
                "max_hp": 500,
                "temp_hp": 100,
                "damage_resistances": ["psychic"],
            },
        )
        mechanics = StatelessMcpMechanics(owner)
        names = {tool.name for tool in mechanics.list_tools("casting")}
        assert {"begin_srd_spell_cast", "continue_srd_spell_cast"} <= names
        begin, begin_command = _act(owner, "begin_srd_spell_cast", **_begin(parameters))
        assert begin.status is ActionStatus.COMMITTED
        completed, completed_command = _act(
            owner,
            "continue_srd_spell_cast",
            caster_state="hero",
            elapsed_rounds=10,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        assert completed.status is ActionStatus.COMMITTED
        failed, failed_command = _act(owner, "cast_spell_outside_combat", **parameters)
        assert failed.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        hero = saved.state[f"{SRD}.entities"]["hero"]
        assert hero["hp"] == 500 and "incapacitated" in hero["conditions"]
        loss = 600 - hero["hp"] - hero["temp_hp"]
        assert loss > 0
        assert list(_card_values(failed.result["result_card"], "Damage")) == [loss]
        assert hero["resources"]["spell_slot_5"] == 2
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        for command, receipt in (
            (begin_command, begin),
            (completed_command, completed),
            (failed_command, failed),
        ):
            assert mechanics.execute_action(command) == receipt
        assert owner.snapshot("casting").state == saved.state
        recovered, recovered_command = _act(owner, "cast_spell_outside_combat", **cure)
        assert recovered.status is ActionStatus.COMMITTED
        cured = owner.snapshot("casting")
        assert "incapacitated" not in cured.state[f"{SRD}.entities"]["hero"]["conditions"]
        healer = cured.state[f"{SRD}.entities"]["healer"]
        assert healer["spell_uses_remaining"]["greater_restoration"] == 0
        assert healer["spell_materials"][0]["quantity"] == 0
        assert mechanics.execute_action(recovered_command) == recovered
        assert owner.snapshot("casting").state == cured.state


def test_contact_feedback_reads_final_enfeeblement_and_self_curse_packet(monkeypatch):
    from tests import test_srd_action_budget as budget
    from tests.srd_full.test_srd_spell_damage_consumers import (
        test_contact_failure_composes_actual_enfeeblement_and_exact_self_curse as actual_case,
    )

    actual_run = budget._run
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    seen = []

    def observe(tool, state, **parameters):
        contact = (
            tool == "cast_spell_outside_combat" and parameters.get("spell") == "contact_other_plane"
        )
        if contact:
            hero = state[f"{SRD}.entities"]["hero"]
            before = hero["hp"] + hero["temp_hp"]
        outcome = actual_run(tool, state, **parameters)
        if contact:
            hero = state[f"{SRD}.entities"]["hero"]
            loss = before - hero["hp"] - hero["temp_hp"]
            assert loss > 0
            assert list(_card_values(outcome.result["result_card"], "Damage")) == [loss]
            seen.append(loss)
        return outcome

    monkeypatch.setattr(budget, "_run", observe)
    actual_case(monkeypatch)
    assert len(seen) == 1
