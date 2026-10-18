"""Source-reviewed defense records must change later play and survive saved authority."""

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from scripts.srd_full.build_srd_5_2_1_spells import _validate_spell_composition

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from sylvia_gm.domain.errors import MechanicsRejected, RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_public_spell_casting import _create_spell_campaign
from tests.srd_full.test_srd_spell_composition import catalogue as catalogue
from tests.srd_full.test_srd_spell_result_cards import SRD, _spell_case

SPELLS = ["mage_armor", "blur", "heroism", "mind_blank", "protection_from_evil_and_good"]
ROOT = Path(__file__).parents[1] / "rulesets/dnd-srd-5.2.1"


def _case(spell, **choices):
    state, data, parameters = _spell_case(spell)
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False, active_actor=None)
    return state, data, parameters | choices


def _attack_flags(state, target="guard", distance=5):
    return _run(
        "resolve_srd_attack_conditions",
        state,
        actor_state="other",
        target_state=target,
        attack_distance=distance,
        actor_can_see_target=True,
        target_can_see_actor=True,
        fear_source_in_sight=False,
    ).result["context"]


def _cast(state, data, parameters):
    return _run("cast_spell_outside_combat", state, participant_data=data, **parameters).result


def test_source_reviewed_records_have_coherent_headers_and_honest_fixed_gaps(catalogue):
    definitions, headers, runtime, text = catalogue
    _validate_spell_composition(definitions, headers, runtime, text)
    sources = json.loads((ROOT / "content/spell-mechanics-source.json").read_text())["spells"]
    for spell in SPELLS:
        assert definitions[spell]["family"] == "timed_actor_defense"
        assert sources[spell]["spell_id"] == spell
    for spell in ["mage_armor", "blur"]:
        assert sources[spell]["not_yet_mapped"] == []
    assert sources["heroism"]["not_yet_mapped"] == []
    assert "already present Charmed" not in " ".join(sources["mind_blank"]["not_yet_mapped"])
    assert sources["mind_blank"]["not_yet_mapped"]
    assert sources["protection_from_evil_and_good"]["not_yet_mapped"]
    assert headers["protection_from_evil_and_good"]["duration"] == "Concentration up to 10 minutes"


@pytest.mark.parametrize(
    "sense,range_feet,distance,expected",
    [
        (None, 0, 5, True),
        ("blindsight_feet", 10, 5, False),
        ("blindsight_feet", 10, 15, True),
        ("truesight_feet", 60, 30, False),
    ],
)
def test_blur_changes_later_attacks_and_respects_perception_range(
    sense, range_feet, distance, expected
):
    state, data, parameters = _case("blur")
    _cast(state, data, parameters)
    if sense:
        state[f"{SRD}.entities"]["other"][sense] = range_feet
    assert (
        _attack_flags(state, target="hero", distance=distance)["_condition_disadvantage"]
        is expected
    )
    _run("end_concentration", state, caster_state="hero")
    assert (
        _attack_flags(state, target="hero", distance=distance)["_condition_disadvantage"] is False
    )


def test_mage_armor_uses_actual_dexterity_and_preserves_better_existing_ac():
    state, data, parameters = _case("mage_armor", target="hero")
    data["abilities"]["dexterity"] = 16
    _cast(state, data, parameters)
    value = _run(
        "resolve_srd_effective_armor_class",
        state,
        participant_data=data,
        target_state="hero",
        lasting_state="state",
        base_armor_class=12,
    ).result["context"]
    assert value["_effective_armor_class"] == 16
    data["abilities"]["dexterity"] = 18
    value = _run(
        "resolve_srd_effective_armor_class",
        state,
        participant_data=data,
        target_state="hero",
        lasting_state="state",
        base_armor_class=12,
    ).result["context"]
    assert value["_effective_armor_class"] == 17
    value = _run(
        "resolve_srd_effective_armor_class",
        state,
        participant_data=data,
        target_state="hero",
        lasting_state="state",
        base_armor_class=20,
    ).result["context"]
    assert value["_effective_armor_class"] == 20


def test_blur_truesight_bypass_requires_perception_while_blindsight_survives_blindness():
    state, data, parameters = _case("blur")
    _cast(state, data, parameters)
    other = state[f"{SRD}.entities"]["other"]
    other["conditions"].append("blinded")
    other["truesight_feet"] = 60
    result = _run(
        "resolve_srd_timed_actor_attack_defenses",
        state,
        actor_state="other",
        target_state="hero",
        attack_distance=5,
    ).result["context"]
    assert result["_actor_defense_attack_disadvantage"] is True
    other["blindsight_feet"] = 10
    result = _run(
        "resolve_srd_timed_actor_attack_defenses",
        state,
        actor_state="other",
        target_state="hero",
        attack_distance=5,
    ).result["context"]
    assert result["_actor_defense_attack_disadvantage"] is False


def test_mage_armor_rejects_armor_and_unowned_target_facts_atomically():
    state, data, parameters = _case("mage_armor", target="hero")
    state[f"{SRD}.entities"]["hero"]["worn_armor"] = {
        "inventory_item": "leather",
        "armor_type": "leather_armor",
        "magic_bonus": 0,
    }
    state[f"{SRD}.entities"]["hero"]["inventory"].append("leather")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="armor"):
        _cast(state, data, parameters)
    assert state == before
    state[f"{SRD}.entities"]["hero"]["worn_armor"] = None
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Character File"):
        _cast(state, data, parameters | {"target": "guard"})
    assert state == before


def test_mage_armor_preserves_trained_magic_shield_bonus():
    state, data, parameters = _case("mage_armor", target="hero")
    data["classes"] = [{"id": "cleric", "level": 1}, {"id": "wizard", "level": 16}]
    data["abilities"]["dexterity"] = 16
    hero = state[f"{SRD}.entities"]["hero"]
    hero["inventory"].append("magic shield")
    hero["worn_shield"] = {
        "inventory_item": "magic shield",
        "armor_type": "shield",
        "magic_bonus": 1,
    }
    _cast(state, data, parameters)
    value = _run(
        "resolve_srd_effective_armor_class",
        state,
        participant_data=data,
        target_state="hero",
        lasting_state="state",
        base_armor_class=16,
    ).result["context"]
    assert value["_effective_armor_class"] == 19


def test_mage_armor_ends_on_actual_armor_don_and_does_not_return_after_doff():
    state, data, parameters = _case("mage_armor", target="hero")
    hero = state[f"{SRD}.entities"]["hero"]
    hero["inventory"].append("leather")
    _cast(state, data, parameters)
    assert state[f"{SRD}.state"]["turn_effects"]
    _run(
        "change_srd_equipment",
        state,
        participant_data=data,
        actor="hero",
        actor_state="hero",
        slot="armor",
        item=[{"inventory_item": "leather", "armor_type": "leather_armor", "magic_bonus": 0}],
    )
    assert state[f"{SRD}.state"]["turn_effects"] == []
    _run(
        "change_srd_equipment",
        state,
        participant_data=data,
        actor="hero",
        actor_state="hero",
        slot="armor",
        item=[],
    )
    ac = _run(
        "resolve_srd_effective_armor_class",
        state,
        target_state="hero",
        lasting_state="state",
        base_armor_class=10,
    ).result["context"]["_effective_armor_class"]
    assert ac == 10


def test_mind_blank_blocks_later_psychic_damage_and_charm_without_replacing_inherent_defenses():
    state, data, parameters = _case("mind_blank")
    _cast(state, data, parameters)
    guard = state[f"{SRD}.entities"]["guard"]
    hp = guard["hp"]
    _run("apply_srd_damage", state, target="guard", amount=20, adjustment=0, damage_type="psychic")
    assert state[f"{SRD}.entities"]["guard"]["hp"] == hp
    with pytest.raises(RulesetPreconditionRejected, match="immune"):
        _run("apply_srd_condition", state, target="guard", condition="charmed")
    _run(
        "apply_srd_composite_damage",
        state,
        target="guard",
        component_1_amount=15,
        component_1_damage_type="psychic",
        component_2_amount=2,
        component_2_damage_type="fire",
    )
    assert state[f"{SRD}.entities"]["guard"]["hp"] == hp - 1
    _run("advance_srd_lasting_time", state, elapsed_rounds=14400)
    _run("apply_srd_condition", state, target="guard", condition="charmed")
    assert "charmed" in state[f"{SRD}.entities"]["guard"]["conditions"]


def test_protection_rejects_a_missing_causing_creature_type_instead_of_bypassing_defense():
    state, data, parameters = _case("protection_from_evil_and_good")
    state[f"{SRD}.entities"]["other"].pop("creature_type", None)
    _cast(state, data, parameters)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="creature's type"):
        _attack_flags(state)
    with pytest.raises(RulesetPreconditionRejected, match="creature's type"):
        _run(
            "apply_srd_condition",
            state,
            target="guard",
            condition="charmed",
            source_state_id="other",
        )
    assert state == before


@pytest.mark.parametrize("creature_type,protected", [("fiend", True), ("humanoid", False)])
def test_protection_derives_attack_and_condition_defense_from_causing_creature(
    creature_type, protected
):
    state, data, parameters = _case("protection_from_evil_and_good")
    state[f"{SRD}.entities"]["other"]["creature_type"] = creature_type
    _cast(state, data, parameters)
    assert _attack_flags(state)["_condition_disadvantage"] is protected
    before = deepcopy(state)
    if protected:
        with pytest.raises(RulesetPreconditionRejected, match="immune"):
            _run(
                "apply_srd_condition",
                state,
                target="guard",
                condition="charmed",
                source_state_id="other",
            )
        assert state == before
    else:
        _run(
            "apply_srd_condition",
            state,
            target="guard",
            condition="charmed",
            source_state_id="other",
        )
        assert "charmed" in state[f"{SRD}.entities"]["guard"]["conditions"]
    with pytest.raises(RulesetPreconditionRejected, match="causing creature"):
        _run("apply_srd_condition", state, target="guard", condition="frightened")


def test_protection_consumes_actual_holy_water_and_retains_existing_source_save_advantage():
    state, data, parameters = _case("protection_from_evil_and_good")
    entities = state[f"{SRD}.entities"]
    entities["other"]["creature_type"] = "fiend"
    _run(
        "install_srd_timed_condition_source", state,
        target_state="guard", lasting_state="state", caster_state="other",
        spell="charm_person", condition="charmed", save_dc=15,
        duration_rounds=600, repeat_save_timing="none", save_ability="wisdom",
        concentration_sequence=0, control_profile="friendly_charm", caster_allies=[],
    )
    before = entities["hero"]["spell_materials"][0]["quantity"]
    _cast(state, data, parameters)
    assert state[f"{SRD}.entities"]["hero"]["spell_materials"][0]["quantity"] == before - 1
    result = _run(
        "check_srd_target_condition_immunity",
        state,
        target_state="guard",
        condition="charmed",
        source_state_id="other",
    ).result["context"]
    assert result["_source_condition_immune"] is True
    assert result["_source_condition_save_advantage"] is True
    assert "charmed" in state[f"{SRD}.entities"]["guard"]["conditions"]


def test_heroism_scaling_grants_temporary_hp_only_at_start_and_preserves_other_hp():
    state, data, parameters = _case(
        "heroism", targets=["guard", "other"], slot_resource="spell_slot_2"
    )
    _cast(state, data, parameters)
    entities = state[f"{SRD}.entities"]
    assert entities["guard"]["temp_hp"] == 1
    _run(
        "process_srd_turn_effects",
        state,
        lasting_state="state",
        boundary_actor="guard",
        boundary_phase="start",
        boundary_turn=0,
    )
    assert state[f"{SRD}.entities"]["guard"]["temp_hp"] == 4
    with pytest.raises(RulesetPreconditionRejected, match="immune"):
        _run("apply_srd_condition", state, target="guard", condition="frightened")
    _run("grant_temporary_hit_points", state, target="guard", amount=9, accept_new=True)
    _run(
        "process_srd_turn_effects",
        state,
        lasting_state="state",
        boundary_actor="guard",
        boundary_phase="start",
        boundary_turn=1,
    )
    assert state[f"{SRD}.entities"]["guard"]["temp_hp"] == 9
    _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    _run(
        "process_srd_turn_effects",
        state,
        lasting_state="state",
        boundary_actor="guard",
        boundary_phase="start",
        boundary_turn=2,
    )
    assert state[f"{SRD}.entities"]["guard"]["temp_hp"] == 9
    _run("apply_srd_condition", state, target="guard", condition="frightened")


def test_public_blur_replay_reopen_later_attack_and_shutdown(tmp_path):
    path = tmp_path / "defenses.db"
    with AuthorityOwner.open(path, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(owner, "blur")
        command = ActionCommand(
            campaign_id="casting",
            action_id="blur",
            idempotency_key="blur",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        mechanics = StatelessMcpMechanics(owner)
        receipt = mechanics.execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED
        assert mechanics.execute_action(command) == receipt
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        state = deepcopy(dict(owner.snapshot("casting").state))
        assert _attack_flags(state, target="hero")["_condition_disadvantage"] is True
        ended = StatelessMcpMechanics(owner).execute_action(
            replace(
                command,
                action_id="end",
                idempotency_key="end",
                expected_revision=saved.revision,
                tool_name="end_concentration",
                parameters={"caster_state": "hero"},
            )
        )
        assert ended.status is ActionStatus.COMMITTED
        state = deepcopy(dict(owner.snapshot("casting").state))
        assert _attack_flags(state, target="hero")["_condition_disadvantage"] is False


def test_public_mage_armor_rejects_mismatched_target_character_without_spending(tmp_path):
    with AuthorityOwner.open(tmp_path / "mage-armor.db", "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(owner, "mage_armor")
        before = owner.snapshot("casting")
        command = ActionCommand(
            campaign_id="casting",
            action_id="bad-identity",
            idempotency_key="bad-identity",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters | {"target_character": "hero"},
        )
        with pytest.raises(MechanicsRejected, match="Character File"):
            StatelessMcpMechanics(owner).execute_action(command)
        assert owner.snapshot("casting").revision == before.revision
        assert owner.snapshot("casting").state == before.state
