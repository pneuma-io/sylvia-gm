"""Reaction spells intervene on source-owned saved actions and spend real budgets."""

from copy import deepcopy
from random import Random

import pytest
from scripts.srd_reaction_spells import PROFILES, validate_profiles

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.test_srd_action_budget import SRD, _run
from tests.test_srd_attack_budget import _combat_case
from tests.test_srd_npc_spell_casting import _act, _prepare_mage
from tests.test_srd_public_spell_casting import _create_spell_campaign
from tests.test_srd_reaction_windows import _parameters
from tests.test_srd_scene_relations import _state as _scene_state
from tests.test_srd_spell_result_cards import _spell_case


def _mage(state, spell, constitution_save=0):
    original_combat = deepcopy(state[f"{SRD}.combat"])
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    _run(
        "prepare_srd_creature",
        state,
        creature_id="mage",
        name="Reaction Mage",
        source="Synthetic NPC: AC 13, Constitution save supplied, spell DC 15.",
        armor_class=13,
        hit_points=1000,
        speed=30,
        initiative_modifier=0,
        size_rank=2,
        constitution_save=constitution_save,
        spellcasting_ability="intelligence",
        spellcasting_modifier=3,
        spell_attack_bonus=7,
        spell_save_dc=15,
        spells=[{"spell": spell, "uses_per_day": 2}],
    )
    state[f"{SRD}.combat"] = original_combat
    from tests.srd_fixture_scene import fixture_scene
    state[f"{SRD}.state"]["spatial_scene"] = fixture_scene(
        state[f"{SRD}.entities"], {"mage": [10, 0, 0]},
    )


def _cast_parameters(spell, trigger):
    return {
        "spell": spell,
        "caster_state": "mage",
        "reaction_trigger_id": trigger,
        "verbal_component_available": True,
        "somatic_component_available": True,
    }


def _counterspell_case(constitution_save=0):
    state, data, parameters = _spell_case("counterspell")
    _mage(state, "magic_missile", constitution_save)
    scene = deepcopy(_scene_state()[f"{SRD}.state"])
    scene["spatial_scene"]["entities"][1].update(id="mage", position=[10, 0, 0])
    scene["spatial_scene"]["entities"][1]["metadata"]["participant_ref"] = "mage"
    # The source relation fixture contains only hero/guard; this cast targets other.
    target = deepcopy(scene["spatial_scene"]["entities"][1])
    target.update(id="other", position=[15, 0, 0])
    target["metadata"]["participant_ref"] = "other"
    scene["spatial_scene"]["entities"].append(target)
    from tests.srd_fixture_scene import fixture_scene
    scene["spatial_scene"] = fixture_scene(
        state[f"{SRD}.entities"], {"hero": [0, 0, 0], "mage": [10, 0, 0], "other": [15, 0, 0]},
    )
    state[f"{SRD}.state"] = scene
    state[f"{SRD}.combat"].update(active_actor="mage", current_actor="mage")
    begun = _run(
        "begin_srd_spell_action",
        state,
        spell="magic_missile",
        caster_state="mage",
        target="other",
        target_distance=5,
        caster_can_see_target=True,
        verbal_component_available=True,
        somatic_component_available=True,
    )
    trigger = begun.result["context"]["_pending_action_id"]
    parameters["reaction_trigger_id"] = trigger
    return state, data, parameters, trigger


@pytest.mark.parametrize("tool,parameters", [
    ("dismiss_srd_information_spell", {"caster_state": "hero", "spell": "arcane_eye"}),
    ("move_srd_scene_creature", {"actor_state": "hero", "destination": [15, 0, 0]}),
])
def test_pending_spell_fences_ordinary_support_actions_without_changing_saved_trigger(
    tool, parameters,
):
    state, _, _, _ = _counterspell_case()
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="pending reaction window"):
        _run(tool, state, **parameters)
    assert state == before


@pytest.mark.parametrize("spell", PROFILES)
def test_reaction_profiles_reject_changed_source_constants(spell):
    validate_profiles({spell: PROFILES[spell]})
    wrong = PROFILES[spell] | {"range_feet": 999}
    with pytest.raises(SystemExit, match="source profile"):
        validate_profiles({spell: wrong})


@pytest.mark.parametrize("constitution_save,countered", [(0, True), (30, False)])
def test_counterspell_wastes_the_casting_action_and_only_successful_cast_reaches_damage(
    monkeypatch,
    constitution_save,
    countered,
):
    monkeypatch.setattr(dice, "_rng", Random(8))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, parameters, trigger = _counterspell_case(constitution_save)
    before_hp = state[f"{SRD}.entities"]["other"]["hp"]
    _run("cast_spell", state, participant_data=data, **parameters)
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["reaction_available"] is False
    assert hero["resources"]["spell_slot_3"] == 2
    assert state[f"{SRD}.combat"]["action_available"] is False
    _run("complete_srd_pending_action", state, participant_data=data, pending_action_id=trigger)
    assert state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"]["magic_missile"] == 1
    assert (state[f"{SRD}.entities"]["other"]["hp"] == before_hp) is countered


def test_counterspell_keeps_the_actual_interrupted_character_slot(monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(8))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, parameters = _spell_case("magic_missile")
    _mage(state, "counterspell")
    scene = deepcopy(_scene_state()[f"{SRD}.state"])
    scene["spatial_scene"]["entities"][1].update(id="mage", position=[10, 0, 0])
    scene["spatial_scene"]["entities"][1]["metadata"]["participant_ref"] = "mage"
    from tests.srd_fixture_scene import fixture_scene
    scene["spatial_scene"] = fixture_scene(
        state[f"{SRD}.entities"], {"hero": [0, 0, 0], "mage": [10, 0, 0], "other": [15, 0, 0]},
    )
    state[f"{SRD}.state"] = scene
    begun = _run("begin_srd_spell_action", state, participant_data=data, **parameters)
    trigger = begun.result["context"]["_pending_action_id"]
    before_hp = state[f"{SRD}.entities"]["guard"]["hp"]
    _run("cast_spell", state, **_cast_parameters("counterspell", trigger))
    _run("complete_srd_pending_action", state, participant_data=data, pending_action_id=trigger)
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 3
    assert state[f"{SRD}.entities"]["guard"]["hp"] == before_hp
    assert state[f"{SRD}.combat"]["action_available"] is False


def test_pending_cast_rejects_an_underlevel_slot_before_spending_the_reaction():
    state, data, parameters, _ = _counterspell_case()
    parameters["slot_resource"] = "spell_slot_1"
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="below the spell's source level"):
        _run("begin_srd_spell_action", state, participant_data=data, **parameters)
    assert state == before


def test_pending_cast_fences_an_ordinary_direct_cast_before_any_resource_changes():
    state, data, parameters = _spell_case("magic_missile")
    _run("begin_srd_spell_action", state, participant_data=data, **parameters)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="pending action before another"):
        _run("cast_spell", state, participant_data=data, **parameters)
    assert state == before


def test_counterspell_spends_the_interrupted_feature_use_without_spending_a_slot(monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(8))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, parameters = _spell_case("magic_missile")
    data["origin_feats"] = ["magic_initiate"]
    data["spellcasting_grants"] = [
        {"source": "magic_initiate_wizard", "ability": "intelligence", "spells": ["magic_missile"]}
    ]
    parameters.update(slot_resource="feature", spell_grant="magic_initiate_wizard")
    _mage(state, "counterspell")
    scene = deepcopy(_scene_state()[f"{SRD}.state"])
    scene["spatial_scene"]["entities"][1].update(id="mage", position=[10, 0, 0])
    scene["spatial_scene"]["entities"][1]["metadata"]["participant_ref"] = "mage"
    from tests.srd_fixture_scene import fixture_scene
    scene["spatial_scene"] = fixture_scene(
        state[f"{SRD}.entities"], {"hero": [0, 0, 0], "mage": [10, 0, 0], "other": [15, 0, 0]},
    )
    state[f"{SRD}.state"] = scene
    begun = _run("begin_srd_spell_action", state, participant_data=data, **parameters)
    trigger = begun.result["context"]["_pending_action_id"]
    _run("cast_spell", state, **_cast_parameters("counterspell", trigger))
    _run("complete_srd_pending_action", state, participant_data=data, pending_action_id=trigger)
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["spell_grant_uses"]["magic_initiate_wizard"] == 1
    assert hero["resources"]["spell_slot_1"] == 3
    assert hero["spell_slot_cast_this_turn"] is False


@pytest.mark.parametrize("fact", ["range", "invisible", "plane"])
def test_counterspell_rejects_saved_scene_ineligibility_without_spending(fact):
    state, data, parameters, _ = _counterspell_case()
    mage = next(row for row in state[f"{SRD}.state"]["spatial_scene"]["entities"] if row["id"] == "mage")
    if fact == "range":
        mage["position"] = [61, 0, 0]
    elif fact == "invisible":
        mage["metadata"]["invisible"] = True
    else:
        mage["metadata"]["plane"] = "ethereal"
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="visible caster"):
        _run("cast_spell", state, participant_data=data, **parameters)
    assert state == before


def test_shield_changes_the_triggering_weapon_hit_and_expires_next_caster_turn(monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(8))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data = _combat_case()
    _mage(state, "shield")
    begun = _run(
        "begin_srd_character_attack",
        state,
        participant_data=data,
        **(_parameters() | {"target": "mage"}),
    )
    trigger = begun.result["context"]["_pending_action_id"]
    event = state[f"{SRD}.state"]["pending_srd_actions"][trigger]
    assert event["attack_outcome"] == "hit"
    assert event["attack_total"] < 18
    _run("cast_spell", state, **_cast_parameters("shield", trigger))
    finished = _run(
        "complete_srd_pending_action", state, participant_data=data, pending_action_id=trigger
    )
    assert finished.result["context"]["_attack_roll"] == event["attack_roll"]
    assert finished.result["context"]["_attack_outcome"] == "miss"
    assert state[f"{SRD}.entities"]["mage"]["hp"] == 1000
    active = _run(
        "resolve_srd_effective_armor_class",
        state,
        target_state="mage",
        lasting_state="state",
        base_armor_class=13,
    )
    assert active.result["context"]["_effective_armor_class"] == 18
    _run("end_combat_turn", state, actor="hero")
    state[f"{SRD}.combat"]["current_actor"] = "mage"
    _run("begin_combat_turn", state, actor="mage")
    expired = _run(
        "resolve_srd_effective_armor_class",
        state,
        target_state="mage",
        lasting_state="state",
        base_armor_class=13,
    )
    assert expired.result["context"]["_effective_armor_class"] == 13


def test_shield_blocks_only_its_magic_missile_allocation():
    state, data, parameters = _spell_case("magic_missile")
    _mage(state, "shield")
    parameters.update(target="mage", projectile_targets=["mage", "mage", "other"])
    begun = _run("begin_srd_spell_action", state, participant_data=data, **parameters)
    trigger = begun.result["context"]["_pending_action_id"]
    before_other = state[f"{SRD}.entities"]["other"]["hp"]
    _run("cast_spell", state, **_cast_parameters("shield", trigger))
    _run("complete_srd_pending_action", state, participant_data=data, pending_action_id=trigger)
    assert state[f"{SRD}.entities"]["mage"]["hp"] == 1000
    assert state[f"{SRD}.entities"]["other"]["hp"] < before_other


def test_reaction_slot_limit_follows_each_actual_turn_without_resetting_reaction():
    state, data, parameters, trigger = _counterspell_case(30)
    hero = state[f"{SRD}.entities"]["hero"]
    combat = state[f"{SRD}.combat"]
    combat["turn_sequence"] = 2
    hero.update(spell_slot_turn_sequence=1, spell_slot_cast_this_turn=True)
    _run("cast_spell", state, participant_data=data, **parameters)
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["spell_slot_cast_this_turn"] is True
    assert hero["spell_slot_turn_sequence"] == 2
    assert hero["reaction_available"] is False
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="one spell slot"):
        _run("cast_spell", state, participant_data=data, **parameters)
    assert state == before


def test_public_shield_reopens_and_retries_before_the_triggering_magic_missile_finishes(tmp_path):
    database = tmp_path / "shield.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(owner, "shield")
        prepared, _ = _prepare_mage(owner, [{"spell": "magic_missile", "uses_per_day": 2}])
        assert prepared.status is ActionStatus.COMMITTED
        begun, _ = _act(
            owner,
            "begin_srd_spell_action",
            spell="magic_missile",
            caster_state="mage",
            target="hero",
            target_distance=5,
            caster_can_see_target=True,
            verbal_component_available=True,
            somatic_component_available=True,
        )
        assert begun.status is ActionStatus.COMMITTED
        before = owner.snapshot("casting")
        trigger = before.state[f"{SRD}.state"]["pending_srd_action_stack"][-1]
        shielded, shield_command = _act(
            owner,
            "cast_srd_reaction_spell",
            **(parameters | {"reaction_trigger_id": trigger}),
        )
        assert shielded.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 2
        assert owner.execute_action(shield_command) == shielded
        assert owner.snapshot("casting").state == saved.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        assert owner.execute_action(shield_command) == shielded
        finished, finish_command = _act(
            owner, "complete_srd_pending_action", pending_action_id=trigger
        )
        assert finished.status is ActionStatus.COMMITTED
        after = owner.snapshot("casting")
        assert (
            after.state[f"{SRD}.entities"]["hero"]["hp"]
            == before.state[f"{SRD}.entities"]["hero"]["hp"]
        )
        assert after.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 2
        assert owner.execute_action(finish_command) == finished
        assert owner.snapshot("casting").state == after.state
