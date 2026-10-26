"Transformation choices change actual owners and retain source-specific saved lifetimes."

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner, CampaignCreate
from sylvia_gm.domain.authority import ActionStatus, StateValue, WriterRole, thaw_json_value
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_public_spell_casting import _create_spell_campaign
from tests.srd_full.test_srd_spell_composition import catalogue as catalogue
from tests.srd_full.test_srd_spell_result_cards import SRD
from tests.srd_full.test_srd_spell_wave_defenses import _case, _cast

ROOT = Path(__file__).parents[1] / "rulesets/dnd-srd-5.2.1"


def _setup(spell, **choices):
    state, data, parameters = _case(spell, **choices)
    for target in ("guard", "other"):
        state[f"{SRD}.entities"][target].update(
            challenge_rating_eighths=32,
            ability_scores=dict.fromkeys(data["abilities"], 12),
            size_rank=2,
            languages=["common"],
            passive_perception=11,
        )
    return state, data, parameters


def _entity(state, identity="guard"):
    return state[f"{SRD}.entities"][identity]


def _sources(state):
    return state[f"{SRD}.state"]["transformation_sources"]


def _damage(state, amount, identity="guard", kind="force"):
    return _run(
        "apply_srd_damage",
        state,
        target=identity,
        amount=amount,
        adjustment=0,
        damage_type=kind,
        source_state_id="",
    ).result["context"]


def test_transformations_bind_headers_and_only_execute_source_complete_beasts(catalogue):
    from scripts.srd_full.build_srd_5_2_1_spells import _validate_spell_composition
    from scripts.srd_full.build_srd_beast_forms import build_index
    from scripts.srd_full.srd_transformation_spells import PROFILES

    definitions, headers, runtime, text = catalogue
    _validate_spell_composition(definitions, headers, runtime, text)
    index = build_index()
    assert index["form_count"] == 90
    assert runtime["definitions"]["beast_forms"] == index["forms"]
    assert index["forms"]["brown_bear"]["execution_supported"] is True
    assert index["forms"]["ape"]["execution_supported"] is True
    assert index["forms"]["ape"]["attacks"]["Rock"]["recharge_min"] == 6
    for form in index["forms"].values():
        assert form["execution_supported"] is (not bool(form["unsupported_clauses"]))
    source = json.loads((ROOT / "content/spell-mechanics-source.json").read_text())["spells"]
    for spell in PROFILES:
        assert definitions[spell]["family"] == "transformation_spell"
        assert source[spell]["not_yet_mapped"]


def test_polymorph_replaces_real_statistics_preserves_hp_and_reverts_after_temp_hp_damage():
    state, data, parameters = _setup(
        "polymorph", transformation_form="brown_bear", forego_save_targets=["guard"]
    )
    original = deepcopy(_entity(state))
    _cast(state, data, parameters)
    target = _entity(state)
    assert target["hp"] == original["hp"] == 19
    assert target["max_hp"] == original["max_hp"] == 20
    assert (target["temp_hp"], target["armor_class"], target["speed"], target["size_rank"]) == (
        22,
        11,
        40,
        3,
    )
    assert target["creature_type"] == original["creature_type"]
    assert target["form_can_cast"] is False and target["form_equipment_melded"] is True
    context = _damage(state, 24)
    assert context["_temp_hp_absorbed"] == 22 and context["_hp_lost"] == 2
    target = _entity(state)
    assert (
        target["hp"],
        target["temp_hp"],
        target["armor_class"],
        target["speed"],
        target["size_rank"],
    ) == (17, 0, original["armor_class"], 30, 2)
    assert target["form_current_source_id"] is None
    assert _entity(state, "hero")["concentration_spell_id"] is None


def test_transformation_expiry_preserves_later_chosen_temp_hp_and_restores_equipment_access():
    state, data, parameters = _setup(
        "polymorph", transformation_form="camel", forego_save_targets=["guard"]
    )
    _cast(state, data, parameters)
    source_id = _entity(state)["form_current_source_id"]
    _run("grant_temporary_hit_points", state, target="guard", amount=31, accept_new=True)
    _run("end_srd_transformation_source", state, target_state="guard", source_id=source_id)
    assert _entity(state)["temp_hp"] == 31
    assert _entity(state)["form_equipment_melded"] is False
    assert _sources(state)[source_id]["installed"] is False


def test_cast_rejects_unimplemented_form_and_target_character_mismatch_without_costs():
    for choices, reason in [
        ({"transformation_form": "giant_ape"}, "traits or actions"),
        ({"transformation_form": "camel", "target_character": "hero"}, "match"),
    ]:
        state, data, parameters = _setup("polymorph", **choices)
        before = deepcopy(state)
        with pytest.raises(RulesetPreconditionRejected, match=reason):
            _cast(state, data, parameters)
        assert state == before


def test_named_form_attacks_use_real_multiattack_and_fixed_prone_rider(monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    state, data, parameters = _setup(
        "polymorph", transformation_form="brown_bear", forego_save_targets=["guard"]
    )
    _cast(state, data, parameters)
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=True,
        active_actor="guard",
        current_actor="guard",
        action_available=True,
        attacks_remaining=0,
        normal_action_spent=False,
    )
    common = {
        "actor_state": "guard",
        "target_state": "other",
        "attack_distance": 5,
        "actor_can_see_target": True,
        "target_can_see_actor": True,
        "target_is_charmer": False,
        "fear_source_in_sight": False,
        "use_multiattack": True,
    }
    first = _run("attack_srd_transformed_form", state, attack_name="Bite", **common).result[
        "context"
    ]
    assert first["_attack_roll"]["modifier"] == 5
    assert first["_effective_damage"] > 0
    _run("attack_srd_transformed_form", state, attack_name="Claw", **common)
    assert "prone" in _entity(state, "other")["conditions"]
    assert state[f"{SRD}.combat"]["attacks_remaining"] == 0
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("attack_srd_transformed_form", state, attack_name="Bite", **common)
    assert state == before


def test_animal_shapes_retains_mental_scores_and_first_hp_on_later_magic_action():
    state, data, parameters = _setup("animal_shapes", transformation_form="brown_bear")
    _cast(state, data, parameters)
    target = _entity(state)
    source_id = target["form_current_source_id"]
    assert target["form_ability_scores"]["intelligence"] == 12
    assert target["form_ability_scores"]["strength"] == 17
    assert target["languages"] == ["common"]
    _damage(state, 8)
    assert _entity(state)["temp_hp"] == 14
    _run(
        "change_srd_transformation",
        state,
        actor_state="hero",
        source_ids=[source_id],
        form_id="camel",
    )
    assert _entity(state)["temp_hp"] == 14
    assert _entity(state)["speed"] == 50
    assert _entity(state)["form_current_source_id"] == source_id
    _run("end_srd_animal_shape", state, actor_state="guard", source_id=source_id)
    assert _entity(state)["temp_hp"] == 0
    assert _entity(state)["form_ability_scores"] is None


def test_true_polymorph_full_concentration_becomes_permanent_then_exact_disposal_reverts():
    state, data, parameters = _setup("true_polymorph", transformation_form="camel")
    _cast(state, data, parameters)
    source_id = _entity(state)["form_current_source_id"]
    assert _entity(state)["creature_type"] == "beast"
    _run("advance_srd_lasting_time", state, elapsed_rounds=600)
    assert _sources(state)[source_id]["permanent"] is True
    assert _sources(state)[source_id]["spell_level"] == 9
    assert _entity(state)["speed"] == 50
    _run("advance_srd_lasting_time", state, elapsed_rounds=600)
    assert _entity(state)["form_current_source_id"] == source_id
    _run("end_srd_transformation_source", state, target_state="guard", source_id=source_id)
    assert _entity(state)["creature_type"] == "humanoid"
    assert _entity(state)["temp_hp"] == 0


@pytest.mark.parametrize("kind", ["bludgeoning", "piercing", "slashing", "fire"])
def test_gaseous_form_actual_damage_and_prone_immunity(kind):
    state, data, parameters = _setup("gaseous_form")
    _cast(state, data, parameters)
    _entity(state)["temp_hp"] = 0
    result = _damage(state, 8, kind=kind)
    assert result["_effective_damage"] == (8 if kind == "fire" else 4)
    assert (_entity(state)["speed"], _entity(state)["flying_speed"]) == (0, 10)
    with pytest.raises(RulesetPreconditionRejected, match="immune"):
        _run("apply_srd_condition", state, target="guard", condition="prone")


def test_source_beast_resistance_consumes_typed_damage_and_restores_original_resistance():
    state, data, parameters = _setup(
        "polymorph", transformation_form="polar_bear", forego_save_targets=["guard"]
    )
    _entity(state)["damage_resistances"] = ["fire"]
    _cast(state, data, parameters)
    target = _entity(state)
    assert target["damage_resistances"] == ["cold"]
    assert _damage(state, 9, kind="cold")["_effective_damage"] == 4
    assert _damage(state, 9, kind="fire")["_effective_damage"] == 9
    source_id = _entity(state)["form_current_source_id"]
    _run("end_srd_transformation_source", state, target_state="guard", source_id=source_id)
    assert _entity(state)["damage_resistances"] == ["fire"]
    assert _damage(state, 9, kind="fire")["_effective_damage"] == 4


def test_amphibious_form_breathing_expires_at_its_actual_boundary():
    state, data, parameters = _setup(
        "polymorph", transformation_form="crab", forego_save_targets=["guard"]
    )
    _entity(state).update(constitution_score=8, exhaustion_level=2, conditions=["exhaustion"])
    _cast(state, data, parameters)
    _run("set_srd_respiration", state, target_state="guard", medium="water")
    assert _entity(state)["respiration_can_breathe"] is True
    _run("advance_srd_lasting_time", state, elapsed_rounds=606)
    target = _entity(state)
    assert target["form_statistics_replaced"] is False
    assert target["respiration_can_breathe"] is False
    assert target["respiration_capacity_ticks"] == 10
    assert target["respiration_breath_ticks"] == 0
    assert target["respiration_exhaustion"] == 1
    assert target["exhaustion_level"] == 3
    _run("set_srd_respiration", state, target_state="guard", medium="air")
    assert _entity(state)["exhaustion_level"] == 2


def test_hold_breath_consumes_source_duration_without_granting_water_breathing():
    state, data, parameters = _setup(
        "polymorph", transformation_form="hippopotamus", forego_save_targets=["guard"]
    )
    _entity(state).update(constitution_score=12, exhaustion_level=0)
    _run("set_srd_respiration", state, target_state="guard", medium="water")
    _run("advance_srd_lasting_time", state, elapsed_rounds=5)
    assert _entity(state)["respiration_breath_ticks"] == 30
    _cast(state, data, parameters)
    assert _entity(state)["respiration_capacity_ticks"] == 200
    assert _entity(state)["respiration_breath_ticks"] == 190
    assert _entity(state)["respiration_can_breathe"] is False
    _run("advance_srd_lasting_time", state, elapsed_rounds=94)
    assert _entity(state)["respiration_breath_ticks"] == 2
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert _entity(state)["respiration_breath_ticks"] == 0
    assert _entity(state)["exhaustion_level"] == 0
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert _entity(state)["exhaustion_level"] == 1


def test_saved_respiration_without_new_base_field_preserves_its_admitted_original_capacity():
    state, data, parameters = _setup(
        "polymorph", transformation_form="hippopotamus", forego_save_targets=["guard"]
    )
    _entity(state).update(constitution_score=12, exhaustion_level=0)
    _run("set_srd_respiration", state, target_state="guard", medium="water")
    assert _entity(state)["respiration_capacity_ticks"] == 40
    del _entity(state)["respiration_base_capacity_ticks"]
    _cast(state, data, parameters)
    assert _entity(state)["respiration_base_capacity_ticks"] == 40
    _run("advance_srd_lasting_time", state, elapsed_rounds=5)
    source_id = _entity(state)["form_current_source_id"]
    _run("end_srd_transformation_source", state, target_state="guard", source_id=source_id)
    assert _entity(state)["respiration_capacity_ticks"] == 40
    assert _entity(state)["respiration_breath_ticks"] == 30


@pytest.mark.parametrize(
    "distance,conditions,plane,expected",
    [
        (5, [], "material", True),
        (5.01, [], "material", False),
        (5, ["stunned"], "material", False),
        (5, [], "ethereal", False),
        (5, ["blinded"], "material", True),
    ],
)
def test_pack_tactics_uses_saved_ally_scene_distance_and_actual_incapacitation(
    monkeypatch, distance, conditions, plane, expected
):
    from tests.srd_full.test_srd_scene_relations import _state

    state, data, parameters = _setup(
        "polymorph", transformation_form="giant_rat", forego_save_targets=["guard"]
    )
    _cast(state, data, parameters)
    _run(
        "record_srd_creature_relationship",
        state,
        actor_state="guard",
        toward_state="hero",
        attitude="friendly",
        is_ally=True,
    )
    _entity(state, "hero")["conditions"] = conditions
    scene = deepcopy(_state()[f"{SRD}.state"]["spatial_scene"])
    ally = scene["entities"][0]
    ally.update(position=[5, distance, 0])
    ally["metadata"]["plane"] = plane
    del ally["metadata"]["lighting"]
    target = scene["entities"][1]
    target.update(id="other", position=[5, 0, 0])
    target["metadata"]["participant_ref"] = "other"
    state[f"{SRD}.state"]["spatial_scene"] = scene
    _entity(state, "other")["armor_class"] = 1
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=True,
        active_actor="guard",
        current_actor="guard",
        action_available=True,
        attacks_remaining=0,
        normal_action_spent=False,
    )
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    result = _run(
        "attack_srd_transformed_form",
        state,
        actor_state="guard",
        target_state="other",
        attack_name="Bite",
        attack_distance=5,
        actor_can_see_target=True,
        target_can_see_actor=True,
        target_is_charmer=False,
        fear_source_in_sight=False,
    ).result["context"]
    assert result["_form_attack_advantage"] is expected
    assert len(result["_attack_roll"]["results"]) == (2 if expected else 1)


def test_pack_tactics_missing_relationship_facts_reject_before_attack_cost():
    state, data, parameters = _setup(
        "polymorph", transformation_form="giant_rat", forego_save_targets=["guard"]
    )
    _cast(state, data, parameters)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="saved ally relationships"):
        _run(
            "attack_srd_transformed_form",
            state,
            actor_state="guard",
            target_state="other",
            attack_name="Bite",
            attack_distance=5,
            actor_can_see_target=True,
            target_can_see_actor=True,
            target_is_charmer=False,
            fear_source_in_sight=False,
        )
    assert state == before


def test_water_only_body_cannot_breathe_original_actors_air_but_can_breathe_underwater():
    state, data, parameters = _setup(
        "polymorph", transformation_form="reef_shark", forego_save_targets=["guard"]
    )
    _entity(state).update(constitution_score=8, exhaustion_level=0)
    _cast(state, data, parameters)
    _run("set_srd_respiration", state, target_state="guard", medium="air")
    assert _entity(state)["respiration_native_air"] is True
    assert _entity(state)["respiration_can_breathe"] is False
    _run("set_srd_respiration", state, target_state="guard", medium="water")
    assert _entity(state)["respiration_can_breathe"] is True
    _run("advance_srd_lasting_time", state, elapsed_rounds=100)
    assert _entity(state)["respiration_exhaustion"] == 0


def test_public_pack_attack_reopens_with_saved_source_and_scene_and_retries_without_reroll(
    tmp_path,
):
    from tests.srd_full.test_srd_scene_relations import _state

    database = tmp_path / "pack.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(
            owner,
            "polymorph",
            target_overrides={
                "hp": 19,
                "max_hp": 20,
                "challenge_rating_eighths": 8,
                "ability_scores": dict.fromkeys(
                    ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"),
                    12,
                ),
                "size_rank": 2,
            },
        )
        parameters.update(
            target="guard", transformation_form="giant_rat", forego_save_targets=["guard"]
        )
        command = ActionCommand(
            campaign_id="casting",
            action_id="pack-form",
            idempotency_key="pack-form",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        bridge = StatelessMcpMechanics(owner)
        assert bridge.execute_action(command).status is ActionStatus.COMMITTED
        relationship = replace(
            command,
            action_id="pack-ally",
            idempotency_key="pack-ally",
            expected_revision=owner.snapshot("casting").revision,
            tool_name="record_srd_creature_relationship",
            parameters={
                "actor_state": "guard",
                "toward_state": "hero",
                "attitude": "friendly",
                "is_ally": True,
            },
        )
        assert bridge.execute_action(relationship).status is ActionStatus.COMMITTED
        source = owner.snapshot("casting")
        world = thaw_json_value(source.state)
        scene = deepcopy(_state()[f"{SRD}.state"]["spatial_scene"])
        scene["entities"][0]["position"] = [5, 5, 0]
        scene["entities"][1].update(id="other", position=[5, 0, 0])
        scene["entities"][1]["metadata"]["participant_ref"] = "other"
        world[f"{SRD}.state"]["spatial_scene"] = scene
        world[f"{SRD}.combat"].update(
            encounter_active=True,
            turn_active=True,
            active_actor="guard",
            current_actor="guard",
            active_actor_kind="creature",
            active_actor_can_act=True,
            action_available=True,
            attacks_remaining=0,
            normal_action_spent=False,
        )
        owner.create_campaign(
            CampaignCreate(
                campaign_id="pack",
                display_name="Saved Pack Tactics",
                writer=WriterRole.DM,
                package_lock=source.package_lock,
                turn_id="pack-turn",
                participants=source.participants,
                initial_state=tuple(
                    StateValue(SRD, k.removeprefix(f"{SRD}."), v) for k, v in world.items()
                ),
            )
        )
        saved = owner.snapshot("pack")
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("pack").state == saved.state
        attack = replace(
            command,
            campaign_id="pack",
            action_id="pack-bite",
            idempotency_key="pack-bite",
            expected_revision=saved.revision,
            turn_id="pack-turn",
            tool_name="attack_srd_transformed_form",
            parameters={
                "actor_state": "guard",
                "target_state": "other",
                "attack_name": "Bite",
                "attack_distance": 5,
                "actor_can_see_target": True,
                "target_can_see_actor": True,
                "target_is_charmer": False,
                "fear_source_in_sight": False,
            },
        )
        bridge = StatelessMcpMechanics(reopened)
        result = bridge.execute_action(attack)
        assert result.status is ActionStatus.COMMITTED
        assert len(result.result["context"]["_attack_roll"]["results"]) == 2
        after = reopened.snapshot("pack")
        assert bridge.execute_action(attack) == result
        assert reopened.snapshot("pack").state == after.state


def test_overlapping_form_expiry_restores_older_body_breath_capacity_within_elapsed_interval():
    state, data, parameters = _setup(
        "polymorph", transformation_form="crab", forego_save_targets=["guard"]
    )
    _entity(state).update(constitution_score=8, exhaustion_level=0)
    _run("set_srd_respiration", state, target_state="guard", medium="water")
    _run(
        "validate_srd_transformation_form",
        state,
        target_state="guard",
        spell="animal_shapes",
        form_id="camel",
        seen_before=False,
    )
    _run(
        "install_srd_transformation_source",
        state,
        target_state="guard",
        caster_state="hero",
        lasting_state="state",
        spell="animal_shapes",
        form_id="camel",
        actual_spell_level=8,
    )
    older = _entity(state)["form_current_source_id"]
    _cast(state, data, parameters)
    assert _entity(state)["form_appearance"] == "Crab"
    _run("advance_srd_lasting_time", state, elapsed_rounds=610)
    target = _entity(state)
    assert target["form_current_source_id"] == older
    assert target["form_appearance"] == "Camel"
    assert target["respiration_capacity_ticks"] == 80
    assert target["respiration_breath_ticks"] == 60
    assert target["exhaustion_level"] == 0


def test_public_amphibious_form_respiration_reopens_and_expiry_retry_consumes_time_once(tmp_path):
    database = tmp_path / "amphibious.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, data, parameters = _create_spell_campaign(owner, "polymorph")
        parameters.update(
            target="hero",
            target_character="hero",
            transformation_form="crab",
            forego_save_targets=["hero"],
        )
        command = ActionCommand(
            campaign_id="casting",
            action_id="amphibious",
            idempotency_key="amphibious",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        bridge = StatelessMcpMechanics(owner)
        result = bridge.execute_action(command)
        assert result.status is ActionStatus.COMMITTED
        breathe = replace(
            command,
            action_id="underwater",
            idempotency_key="underwater",
            expected_revision=owner.snapshot("casting").revision,
            tool_name="set_srd_respiration",
            parameters={"target_state": "hero", "target_character": "hero", "medium": "water"},
        )
        assert bridge.execute_action(breathe).status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["hero"]["respiration_can_breathe"] is True
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("casting").state == saved.state
        elapsed = replace(
            command,
            action_id="underwater-expiry",
            idempotency_key="underwater-expiry",
            expected_revision=saved.revision,
            tool_name="advance_srd_lasting_time",
            parameters={"elapsed_rounds": 604},
        )
        bridge = StatelessMcpMechanics(reopened)
        result = bridge.execute_action(elapsed)
        assert result.status is ActionStatus.COMMITTED
        assert bridge.execute_action(elapsed) == result
        final = reopened.snapshot("casting").state[f"{SRD}.entities"]["hero"]
        capacity = max(5, (1 + (data["abilities"]["constitution"] - 10) // 2) * 10) * 2
        assert final["form_statistics_replaced"] is False
        assert final["respiration_can_breathe"] is False
        assert final["respiration_breath_ticks"] == capacity - 8


def test_gaseous_movement_objects_magic_action_ending_and_zero_hp():
    state, data, parameters = _setup("gaseous_form")
    _cast(state, data, parameters)
    with pytest.raises(RulesetPreconditionRejected, match="Objects"):
        _run("drop_srd_control_held_items", state, target_state="guard")
    _run("change_srd_transformation", state, actor_state="guard")
    assert _entity(state)["form_cloud"] is False
    _cast(state, data, parameters)
    _damage(state, 100)
    assert _entity(state)["hp"] == 0
    assert _entity(state)["form_cloud"] is False


def _form_permissions(state):
    context = _run("check_srd_form_movement_permissions", state, actor_state="guard").result[
        "context"
    ]
    return tuple(
        context[name]
        for name in ("_form_narrow_openings", "_form_share_creature_space", "_form_liquids_solid")
    )


def test_gaseous_permissions_follow_actual_cast_end_and_expiry():
    state, data, parameters = _setup("gaseous_form")
    assert _form_permissions(state) == (False, False, False)
    _cast(state, data, parameters)
    assert _form_permissions(state) == (True, True, True)
    _run("change_srd_transformation", state, actor_state="guard")
    assert _form_permissions(state) == (False, False, False)
    _cast(state, data, parameters)
    _run("advance_srd_lasting_time", state, elapsed_rounds=600)
    assert _form_permissions(state) == (False, False, False)


def test_gaseous_permissions_end_on_actual_zero_hit_points():
    state, data, parameters = _setup("gaseous_form")
    _cast(state, data, parameters)
    assert _form_permissions(state) == (True, True, True)
    _damage(state, 100)
    assert _entity(state)["hp"] == 0
    assert _form_permissions(state) == (False, False, False)


def test_wind_walk_never_inherits_gaseous_movement_permissions():
    state, _, _ = _setup("wind_walk")
    _run(
        "install_srd_transformation_source",
        state,
        target_state="guard",
        caster_state="hero",
        lasting_state="state",
        spell="wind_walk",
        mode="cloud",
        actual_spell_level=6,
    )
    assert _entity(state)["form_cloud"] is True
    assert _form_permissions(state) == (False, False, False)
    _run("change_srd_transformation", state, actor_state="guard")
    assert _entity(state)["form_cloud"] is True
    assert _form_permissions(state) == (False, False, False)


def test_newer_body_suppresses_gaseous_permissions_until_exact_source_removed():
    state, data, parameters = _setup("gaseous_form")
    _cast(state, data, parameters)
    original_source = _entity(state)["form_current_source_id"]
    _run(
        "install_srd_transformation_source",
        state,
        target_state="guard",
        caster_state="hero",
        lasting_state="state",
        spell="animal_shapes",
        mode="beast",
        form_id="brown_bear",
        actual_spell_level=8,
    )
    newer_source = _entity(state)["form_current_source_id"]
    assert newer_source != original_source
    assert _form_permissions(state) == (False, False, False)
    assert _sources(state)[original_source]["installed"] is True
    _run("end_srd_transformation_source", state, target_state="guard", source_id=newer_source)
    assert _entity(state)["form_current_source_id"] == original_source
    assert _form_permissions(state) == (True, True, True)


def test_alter_self_aquatic_breathing_and_magic_appearance_replacement():
    state, data, parameters = _setup("alter_self")
    _cast(state, data, parameters)
    assert _entity(state, "hero")["swim_speed"] == _entity(state, "hero")["speed"]
    assert (
        _run("resolve_srd_timed_senses", state, target_state="hero").result["context"][
            "_sense_water_breathing"
        ]
        is True
    )
    _run(
        "change_srd_transformation",
        state,
        actor_state="hero",
        option="appearance",
        appearance="a silver-haired traveler",
        same_size=True,
        same_basic_shape=True,
    )
    assert (
        _run("resolve_srd_timed_senses", state, target_state="hero").result["context"][
            "_sense_water_breathing"
        ]
        is False
    )
    view = _run(
        "read_srd_transformed_appearance",
        state,
        observer_state="other",
        target_state="hero",
        distance=5,
    ).result["context"]
    assert view["_transformed_appearance"] == "a silver-haired traveler"
    _run("end_concentration", state, caster_state="hero")
    assert _entity(state, "hero")["form_appearance"] is None


def test_wind_walk_saved_transition_uses_real_one_minute_and_restores_on_expiry():
    state, data, _ = _setup("wind_walk")
    _run(
        "install_srd_transformation_source",
        state,
        target_state="guard",
        caster_state="hero",
        lasting_state="state",
        spell="wind_walk",
        mode="cloud",
        actual_spell_level=6,
    )
    source_id = _entity(state)["form_current_source_id"]
    assert _entity(state)["flying_speed"] == 300
    _run("change_srd_transformation", state, actor_state="guard")
    assert _sources(state)[source_id]["mode"] == "to_normal"
    assert "stunned" in _entity(state)["conditions"]
    _run("advance_srd_lasting_time", state, elapsed_rounds=9)
    assert "stunned" in _entity(state)["conditions"]
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert _sources(state)[source_id]["mode"] == "normal"
    assert "stunned" not in _entity(state)["conditions"]
    assert _entity(state)["speed"] == 30
    _run("change_srd_transformation", state, actor_state="guard")
    _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    assert _entity(state)["flying_speed"] == 300
    _run("advance_srd_lasting_time", state, elapsed_rounds=4780)
    assert _entity(state)["form_cloud"] is False


def test_alter_self_natural_weapon_uses_real_casting_modifier_d6_and_growth_type(monkeypatch):
    from tests.srd_full.test_srd_unarmed_creatures import _strike

    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    state, data, parameters = _setup("alter_self", transformation_option="claws")
    _cast(state, data, parameters)
    source = _sources(state)[_entity(state, "hero")["form_current_source_id"]]
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=True,
        active_actor="hero",
        current_actor="hero",
        action_available=True,
        attacks_remaining=0,
        normal_action_spent=False,
    )
    _entity(state)["armor_class"] = 1
    receipt = _strike(state, data, "damage", use_altered_natural_weapon=True)
    context = receipt.result["context"]
    assert context["_ability_modifier"] == source["casting_modifier"]
    assert context["_unarmed_damage_type"] == "slashing"
    assert context["_unarmed_damage_roll"]["sides"] == 6
    assert (
        context["_unarmed_damage"]
        == context["_unarmed_damage_roll"]["total"] + source["casting_modifier"]
    )


def _npc_alter_case(option="claws", proficiency_bonus=3):
    state, data, parameters = _setup("alter_self", transformation_option=option)
    facts = {} if proficiency_bonus is None else {"proficiency_bonus": proficiency_bonus}
    _run(
        "prepare_srd_creature",
        state,
        creature_id="mage",
        name="NPC Mage",
        source="Synthetic NPC: Wisdom +3, Proficiency +3, spell attack +7, DC 15.",
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
        spells=[{"spell": "alter_self", "uses_per_day": 1}],
        **facts,
    )
    parameters.pop("caster")
    parameters.pop("slot_resource")
    parameters["caster_state"] = "mage"
    _cast(state, None, parameters)
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=True,
        active_actor="mage",
        current_actor="mage",
        action_available=True,
        attacks_remaining=0,
        normal_action_spent=False,
    )
    _entity(state).update(hp=100, max_hp=100, temp_hp=0, armor_class=1)
    return state


def _npc_natural_attack(state):
    return _run(
        "attack_srd_altered_natural_weapon",
        state,
        actor_state="mage",
        target_state="guard",
        attack_distance=5,
        actor_can_see_target=True,
        target_can_see_actor=True,
        target_is_charmer=False,
        fear_source_in_sight=False,
    ).result["context"]


@pytest.mark.parametrize(
    "growth,kind,seed",
    [
        ("claws", "slashing", 0),
        ("fangs", "piercing", 0),
        ("horns", "piercing", 0),
        ("hooves", "bludgeoning", 5),
    ],
)
def test_npc_alter_growth_derives_real_unarmed_proficiency_damage_and_critical(
    monkeypatch, growth, kind, seed
):
    state = _npc_alter_case(growth)
    # An unrelated held weapon's enchantment cannot turn an Unarmed Strike into a weapon attack.
    _entity(state, "mage")["has_actor_weapon_modifiers"] = True
    _entity(state)["damage_resistances"] = [kind]
    monkeypatch.setattr(dice, "_rng", Random(seed))
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    result = _npc_natural_attack(state)
    assert result["_natural_attack_modifier"] == 6
    assert result["_attack_total"] == result["_attack_roll"]["total"] + 6
    assert result["_natural_damage_type"] == kind
    assert result["_damage_roll"]["sides"] == 6
    assert result["_damage_roll"]["count"] == (2 if seed == 5 else 1)
    assert result["_effective_damage"] == (result["_damage_roll"]["total"] + 3) // 2
    assert _entity(state)["hp"] == 100 - result["_effective_damage"]
    assert _entity(state, "mage")["altered_natural_attack_in_progress"] is False
    assert state[f"{SRD}.combat"]["action_available"] is False
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="action.*spent"):
        _npc_natural_attack(state)
    assert state == before


@pytest.mark.parametrize("reason", ["unknown-proficiency", "aquatic", "expiry"])
def test_npc_alter_growth_rejects_missing_actual_facts_or_source_before_spending(reason):
    state = _npc_alter_case(
        "aquatic" if reason == "aquatic" else "claws",
        None if reason == "unknown-proficiency" else 3,
    )
    if reason == "unknown-proficiency":
        assert _entity(state, "mage")["proficiency_bonus"] is None
        assert _entity(state, "mage")["challenge_rating_eighths"] is None
        assert _entity(state, "mage")["spellcasting_profile"]["proficiency_bonus"] is None
    if reason == "expiry":
        state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
        _run("advance_srd_lasting_time", state, elapsed_rounds=600)
        state[f"{SRD}.combat"].update(encounter_active=True, turn_active=True)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _npc_natural_attack(state)
    assert state == before


def test_public_npc_alter_growth_reopens_then_commits_one_real_attack_and_retry(
    tmp_path, monkeypatch
):
    from tests.srd_full.test_srd_npc_spell_casting import _act, _prepare_mage

    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    database = tmp_path / "npc-growth.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(
            owner,
            "alter_self",
            entity_overrides={"initiative_modifier": 0},
            target_overrides={"hp": 100, "max_hp": 100, "armor_class": 1},
        )
        receipt, _ = _prepare_mage(
            owner, [{"spell": "alter_self", "uses_per_day": 1}], proficiency_bonus=3
        )
        assert receipt.status is ActionStatus.COMMITTED
        parameters.pop("caster")
        parameters.pop("slot_resource")
        parameters.update(caster_state="mage", transformation_option="claws")
        receipt, cast = _act(owner, "cast_spell_outside_combat", **parameters)
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        mage = saved.state[f"{SRD}.entities"]["mage"]
        assert mage["spell_uses_remaining"]["alter_self"] == 0
        assert mage["proficiency_bonus"] == 3
        source_id = mage["form_current_source_id"]
        assert mage["alter_self_option"] == "claws"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        bridge = StatelessMcpMechanics(owner)
        assert bridge.execute_action(cast).status is ActionStatus.COMMITTED
        assert owner.snapshot("casting").state == saved.state
        receipt, _ = _act(
            owner, "start_group_combat_encounter", combatants=["mage", "hero", "guard"]
        )
        assert receipt.status is ActionStatus.COMMITTED
        receipt, _ = _act(owner, "begin_combat_turn")
        assert receipt.status is ActionStatus.COMMITTED
        assert owner.snapshot("casting").state[f"{SRD}.combat"]["active_actor"] == "mage"
        receipt, attack = _act(
            owner,
            "attack_srd_altered_natural_weapon",
            actor_state="mage",
            target_state="guard",
            attack_distance=5,
            actor_can_see_target=True,
            target_can_see_actor=True,
            target_is_charmer=False,
            fear_source_in_sight=False,
        )
        assert receipt.status is ActionStatus.COMMITTED
        context = receipt.result["context"]
        assert context["_natural_attack_modifier"] == 6
        assert context["_damage_roll"]["sides"] == 6
        final = owner.snapshot("casting")
        assert final.state[f"{SRD}.entities"]["guard"]["hp"] < 100
        assert final.state[f"{SRD}.combat"]["action_available"] is False
        assert final.state[f"{SRD}.state"]["transformation_sources"][source_id]["installed"]
        assert bridge.execute_action(attack) == receipt
        assert owner.snapshot("casting").state == final.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == final.state
        assert StatelessMcpMechanics(owner).execute_action(attack) == receipt
        assert owner.snapshot("casting").state == final.state


def test_transformed_pc_checks_use_source_saves_and_original_weapons_are_rejected():
    from tests.srd_full.test_srd_unarmed_creatures import _strike

    state, data, parameters = _setup(
        "polymorph",
        target="hero",
        target_character="hero",
        transformation_form="camel",
        forego_save_targets=["hero"],
    )
    _cast(state, data, parameters)
    check = _run(
        "d20_test",
        state,
        participant_data=data,
        actor="hero",
        actor_state="hero",
        ability="constitution",
        kind="saving_throw",
        proficiency_factor="normal",
        circumstantial_modifier=0,
        target_number=10,
        advantage=False,
        disadvantage=False,
    )
    assert check.result["context"]["_combined_modifier"] == 5
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=True,
        active_actor="hero",
        current_actor="hero",
        action_available=True,
        attacks_remaining=0,
        normal_action_spent=False,
    )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="actual transformed form"):
        _strike(state, data, "damage")
    assert state == before


def test_source_ending_preserves_other_subjects_and_animal_shapes_survives_subject_death():
    state, data, parameters = _setup(
        "gaseous_form",
        targets=["guard", "other"],
        slot_resource="spell_slot_4",
        all_targets_willing=True,
        caster_can_touch_all_targets=True,
    )
    _cast(state, data, parameters)
    first = _entity(state)["form_current_source_id"]
    second = _entity(state, "other")["form_current_source_id"]
    _run("end_srd_transformation_source", state, target_state="guard", source_id=first)
    assert _entity(state, "hero")["concentration_spell_id"] == "gaseous_form"
    assert _sources(state)[second]["installed"] is True
    _run("end_srd_transformation_source", state, target_state="other", source_id=second)
    assert _entity(state, "hero")["concentration_spell_id"] is None
    state, data, parameters = _setup("animal_shapes", transformation_form="camel")
    _cast(state, data, parameters)
    _damage(state, 100)
    assert _entity(state)["is_alive"] is False
    assert _entity(state)["form_current_source_id"] is not None
    assert _sources(state)[_entity(state)["form_current_source_id"]]["installed"] is True


def test_public_polymorph_reopens_with_actual_form_and_retry_does_not_grant_hp_twice(tmp_path):
    database = tmp_path / "polymorph.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        initial, _, parameters = _create_spell_campaign(owner, "polymorph")
        parameters.update(
            target="hero",
            target_character="hero",
            transformation_form="camel",
            forego_save_targets=["hero"],
        )
        command = ActionCommand(
            campaign_id="casting",
            action_id="polymorph",
            idempotency_key="polymorph",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        bridge = StatelessMcpMechanics(owner)
        first = bridge.execute_action(command)
        assert first.status is ActionStatus.COMMITTED
        assert bridge.execute_action(command) == first
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("casting").state == saved.state
        hero = saved.state[f"{SRD}.entities"]["hero"]
        assert hero["hp"] == initial[f"{SRD}.entities"]["hero"]["hp"]
        assert hero["form_statistics_replaced"] is True
        assert hero["temp_hp"] == 17
        first_source = hero["form_current_source_id"]
        bridge = StatelessMcpMechanics(reopened)
        elapsed = replace(
            command,
            action_id="form-expiry",
            idempotency_key="form-expiry",
            expected_revision=saved.revision,
            tool_name="advance_srd_lasting_time",
            parameters={"elapsed_rounds": 600},
        )
        result = bridge.execute_action(elapsed)
        assert result.status is ActionStatus.COMMITTED
        assert bridge.execute_action(elapsed) == result
        final = reopened.snapshot("casting").state[f"{SRD}.entities"]["hero"]
        assert final["form_current_source_id"] is None
        assert final["temp_hp"] == 0
        assert final["form_statistics_replaced"] is False
        assert (
            reopened.snapshot("casting").state[f"{SRD}.state"]["transformation_sources"][
                first_source
            ]["installed"]
            is False
        )


def test_shapechange_retains_mental_communication_and_first_hp_on_magic_form_change():
    state, data, parameters = _setup(
        "shapechange", transformation_form="camel", transformation_seen_before=True
    )
    _cast(state, data, parameters)
    hero = _entity(state, "hero")
    assert hero["form_can_cast"] is True
    assert hero["form_can_speak"] is True
    assert hero["form_ability_scores"]["intelligence"] == data["abilities"]["intelligence"]
    assert hero["temp_hp"] == 17
    _run(
        "change_srd_transformation",
        state,
        participant_data=data,
        actor_state="hero",
        target_character="hero",
        form_id="brown_bear",
        seen_before=True,
    )
    assert _entity(state, "hero")["speed"] == 40
    assert _entity(state, "hero")["temp_hp"] == 17
    assert _entity(state, "hero")["inventory"] == hero["inventory"]


def _worn_form_case(spell, **choices):
    state, data, parameters = _setup(spell, **choices)
    data.update(level=18, classes=[{"id": "fighter", "level": 1}, {"id": "wizard", "level": 17}])
    hero = _entity(state, "hero")
    hero["inventory"].extend(["chain_mail", "shield"])
    hero.update(
        worn_armor={"inventory_item": "chain_mail", "armor_type": "chain_mail", "magic_bonus": 0},
        worn_shield={"inventory_item": "shield", "armor_type": "shield", "magic_bonus": 0},
        armor_class=18,
    )
    return state, data, parameters


def test_melded_armor_is_inactive_and_restores_exact_worn_identity_after_reversion():
    state, data, parameters = _worn_form_case(
        "polymorph",
        target="hero",
        target_character="hero",
        transformation_form="brown_bear",
        forego_save_targets=["hero"],
    )
    original_armor = deepcopy(_entity(state, "hero")["worn_armor"])
    _cast(state, data, parameters)
    hero = _entity(state, "hero")
    source = hero["form_current_source_id"]
    assert hero["worn_armor"] is None and hero["worn_shield"] is None
    assert hero["armor_class"] == 11 and hero["speed"] == 40
    assert hero["form_melded_worn_armor"] == original_armor
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Melded"):
        _run(
            "change_srd_equipment",
            state,
            participant_data=data,
            actor="hero",
            actor_state="hero",
            slot="shield",
            item=[],
        )
    assert state == before
    _run(
        "end_srd_transformation_source",
        state,
        participant_data=data,
        target_state="hero",
        source_id=source,
    )
    hero = _entity(state, "hero")
    assert (
        hero["worn_armor"] == original_armor and hero["worn_shield"]["inventory_item"] == "shield"
    )
    assert hero["armor_class"] == 18 and hero["speed"] == 30


def test_shapechange_resize_uses_body_abilities_and_equipment_changes_survive_reversion():
    state, data, parameters = _worn_form_case(
        "shapechange",
        transformation_form="brown_bear",
        transformation_seen_before=True,
    )
    _cast(state, data, parameters)
    hero = _entity(state, "hero")
    source = hero["form_current_source_id"]
    assert hero["armor_class"] == 18 and hero["speed"] == 40
    _run(
        "change_srd_equipment",
        state,
        participant_data=data,
        actor="hero",
        actor_state="hero",
        slot="shield",
        item=[],
    )
    hero = _entity(state, "hero")
    assert hero["armor_class"] == 16 and hero["speed"] == 40
    assert hero["form_ability_scores"]["dexterity"] == 12
    _run(
        "end_srd_transformation_source",
        state,
        participant_data=data,
        target_state="hero",
        source_id=source,
    )
    hero = _entity(state, "hero")
    assert hero["worn_shield"] is None
    assert hero["armor_class"] == 16 and hero["speed"] == 30


def test_shapechange_drop_does_not_restore_worn_or_dropped_items_on_ending():
    state, data, parameters = _worn_form_case(
        "shapechange",
        transformation_form="brown_bear",
        transformation_seen_before=True,
        transformation_equipment="drop",
    )
    _cast(state, data, parameters)
    hero = _entity(state, "hero")
    source = hero["form_current_source_id"]
    assert hero["worn_armor"] is None and hero["worn_shield"] is None
    assert hero["inventory"] == [] and {"chain_mail", "shield"} <= set(hero["dropped_items"])
    assert hero["armor_class"] == 11
    _run(
        "end_srd_transformation_source",
        state,
        participant_data=data,
        target_state="hero",
        source_id=source,
    )
    hero = _entity(state, "hero")
    assert hero["worn_armor"] is None and hero["worn_shield"] is None and hero["inventory"] == []
    assert hero["armor_class"] == 12


@pytest.fixture(scope="module")
def advancement_equipment_runtime(tmp_path_factory):
    "Regenerate the actual final build stage after transformation spell composition."
    import shutil

    from scripts import build_srd_5_2_1_advancement as advancement
    from scripts.srd_full.srd_transformation_spells import extend_equipment_runtime

    root = tmp_path_factory.mktemp("transformation-advancement") / "package"
    shutil.copytree(ROOT, root)
    previous = advancement.ROOT
    try:
        advancement.ROOT = root
        advancement.main()
    finally:
        advancement.ROOT = previous
    text = (root / "runtime/ruleset.yaml").read_text(encoding="utf-8")
    assert extend_equipment_runtime(text) == text
    return root


@pytest.mark.parametrize(
    "journey",
    [
        test_shapechange_retains_mental_communication_and_first_hp_on_magic_form_change,
        test_melded_armor_is_inactive_and_restores_exact_worn_identity_after_reversion,
        test_shapechange_resize_uses_body_abilities_and_equipment_changes_survive_reversion,
        test_shapechange_drop_does_not_restore_worn_or_dropped_items_on_ending,
    ],
    ids=["body-change", "melded", "resize", "drop"],
)
def test_final_advancement_preserves_transformed_equipment(
    advancement_equipment_runtime, monkeypatch, journey
):
    "Actual transformed equipment stays authoritative after the final owning regeneration."
    from tests import test_srd_action_budget as budget

    monkeypatch.setattr(budget, "ROOT", advancement_equipment_runtime)
    budget._mechanic.cache_clear()
    try:
        journey()
    finally:
        budget._mechanic.cache_clear()


def test_mage_armor_uses_current_form_dexterity_and_cannot_use_a_melded_shield():
    state, data, parameters = _worn_form_case(
        "polymorph",
        target="hero",
        target_character="hero",
        transformation_form="brown_bear",
        forego_save_targets=["hero"],
    )
    _entity(state, "hero")["worn_armor"] = None
    _entity(state, "hero")["prepared_spells"].append("mage_armor")
    data["spellcasting_grants"][0]["spells"].append("mage_armor")
    data["wizard_spellbook"].append("mage_armor")
    materials_state, _, _ = _setup("mage_armor")
    material = deepcopy(_entity(materials_state, "hero")["spell_materials"][0])
    material["id"] = "mage_armor_material"
    _entity(state, "hero")["spell_materials"].append(material)
    _entity(state, "hero")["inventory"].append(material["inventory_item"])
    _cast(
        state,
        data,
        parameters
        | {
            "spell": "mage_armor",
            "slot_resource": "spell_slot_1",
            "material_item_ids": [material["id"]],
        },
    )
    _cast(state, data, parameters)
    context = _run(
        "resolve_srd_effective_armor_class",
        state,
        participant_data=data,
        target_state="hero",
        lasting_state="state",
        base_armor_class=18,
    ).result["context"]
    assert context["_effective_armor_class"] == 14


def test_later_form_projection_preserves_then_retires_actual_speed_bonus_source():
    state, data, parameters = _setup(
        "polymorph",
        transformation_form="brown_bear",
        forego_save_targets=["guard"],
    )
    _entity(state, "hero")["prepared_spells"].append("longstrider")
    data["spellcasting_grants"][0]["spells"].append("longstrider")
    data["wizard_spellbook"].append("longstrider")
    materials_state, _, _ = _setup("longstrider")
    material = deepcopy(_entity(materials_state, "hero")["spell_materials"][0])
    material["id"] = "longstrider_material"
    _entity(state, "hero")["spell_materials"].append(material)
    _entity(state, "hero")["inventory"].append(material["inventory_item"])
    _cast(
        state,
        data,
        parameters
        | {
            "spell": "longstrider",
            "slot_resource": "spell_slot_1",
            "material_item_ids": [material["id"]],
            "targets": ["guard"],
        },
    )
    assert _entity(state)["speed"] == 40
    _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    _cast(state, data, parameters)
    assert _entity(state)["speed"] == 50
    _run("advance_srd_lasting_time", state, elapsed_rounds=590)
    assert _entity(state)["form_statistics_replaced"] is True and _entity(state)["speed"] == 40
    _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    assert _entity(state)["form_statistics_replaced"] is False and _entity(state)["speed"] == 30


def test_public_melded_shield_reopens_then_restores_once_on_actual_expiry(tmp_path):
    initial, _, _ = _setup("polymorph")
    shield = {"inventory_item": "shield", "armor_type": "shield", "magic_bonus": 0}
    inventory = _entity(initial, "hero")["inventory"] + ["shield"]
    database = tmp_path / "melded-shield.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(
            owner,
            "polymorph",
            participant_overrides={
                "classes": [{"id": "cleric", "level": 1}, {"id": "wizard", "level": 16}]
            },
            entity_overrides={"inventory": inventory, "worn_shield": shield, "armor_class": 14},
        )
        parameters.update(
            target="hero",
            target_character="hero",
            transformation_form="camel",
            forego_save_targets=["hero"],
        )
        command = ActionCommand(
            campaign_id="casting",
            action_id="meld-shield",
            idempotency_key="meld-shield",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        bridge = StatelessMcpMechanics(owner)
        receipt = bridge.execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED
        assert bridge.execute_action(command) == receipt
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["hero"]["worn_shield"] is None
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("casting").state == saved.state
        expiry = replace(
            command,
            action_id="meld-expiry",
            idempotency_key="meld-expiry",
            expected_revision=saved.revision,
            tool_name="advance_srd_lasting_time",
            parameters={"elapsed_rounds": 600},
        )
        bridge = StatelessMcpMechanics(reopened)
        receipt = bridge.execute_action(expiry)
        assert receipt.status is ActionStatus.COMMITTED
        assert bridge.execute_action(expiry) == receipt
        hero = reopened.snapshot("casting").state[f"{SRD}.entities"]["hero"]
        assert hero["worn_shield"] == shield and hero["armor_class"] == 14
        assert hero["form_melded_worn_shield"] is None


@pytest.mark.parametrize("size,prone", [(4, True), (5, False)])
def test_source_huge_prone_rider_consumes_actual_target_size(monkeypatch, size, prone):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    state, data, parameters = _setup(
        "polymorph",
        transformation_form="ankylosaurus",
        forego_save_targets=["guard"],
    )
    _cast(state, data, parameters)
    assert _entity(state)["initiative_modifier"] == 0
    _entity(state, "other").update(size_rank=size, armor_class=1)
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=True,
        active_actor="guard",
        current_actor="guard",
        action_available=True,
        attacks_remaining=0,
        normal_action_spent=False,
    )
    _run(
        "attack_srd_transformed_form",
        state,
        actor_state="guard",
        target_state="other",
        attack_name="Tail",
        attack_distance=5,
        actor_can_see_target=True,
        target_can_see_actor=True,
        target_is_charmer=False,
        fear_source_in_sight=False,
    )
    assert ("prone" in _entity(state, "other")["conditions"]) is prone


def test_wind_walk_casts_after_actual_minute_then_uses_cloud_speed_and_duration():
    from tests.srd_full.test_srd_extended_casting import _begin

    state, data, parameters = _setup("wind_walk", targets=["hero", "guard"])
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(parameters))
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Start and continue"):
        _cast(state, data, parameters)
    assert state == before
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
    _cast(state, data, parameters)
    assert _entity(state, "hero")["resources"]["spell_slot_6"] == 2
    assert _entity(state, "hero")["flying_speed"] == 300
    assert _entity(state)["flying_speed"] == 300
    source_id = _entity(state)["form_current_source_id"]
    assert _sources(state)[source_id]["started_tick"] == 20
    assert _sources(state)[source_id]["expires_tick"] == 9620


def _beetle_scene():
    from tests.srd_full.test_srd_scene_relations import _state

    state, data, parameters = _setup(
        "polymorph", transformation_form="giant_fire_beetle", forego_save_targets=["guard"]
    )
    _cast(state, data, parameters)
    scene = deepcopy(_state()[f"{SRD}.state"]["spatial_scene"])
    scene["entities"][0]["position"] = [-5, 0, 0]
    scene["entities"][1]["position"] = [0, 0, 0]
    for entity in scene["entities"]:
        entity["metadata"]["lighting"] = "darkness"
    state[f"{SRD}.state"]["spatial_scene"] = scene
    return state


@pytest.mark.parametrize(
    "distance,obstruction,ambient,expected",
    [
        (10, "clear", "darkness", "bright"),
        (10.01, "clear", "darkness", "dim"),
        (20, "clear", "darkness", "dim"),
        (20.01, "clear", "darkness", "darkness"),
        (5, "wall", "darkness", "darkness"),
        (5, "other_plane", "darkness", "darkness"),
        (5, "clear", "magical_darkness", "magical_darkness"),
    ],
)
def test_natural_form_light_uses_real_source_position_and_barriers(
    distance, obstruction, ambient, expected
):
    from tests.test_spatial_engine import _geometry

    state = _beetle_scene()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    if obstruction == "wall":
        wall = _geometry("wall", [1, -50, -50], [2, 50, 50])
        wall["map_id"] = "relation"
        scene["geometry"] = [wall]
    elif obstruction == "other_plane":
        scene["entities"][1]["metadata"]["plane"] = "ethereal"
    result = _run(
        "resolve_srd_information_light",
        state,
        point=[distance, 0, 0],
        plane="material",
        ambient=ambient,
    ).result["context"]
    assert result["_information_light"] == expected
    magical = _run(
        "check_srd_actor_spell_light", state, actor_state="guard", lasting_state="state"
    ).result["context"]
    assert magical["_actor_spell_dim_light_feet"] == 0


def test_body_light_tracks_newest_source_and_restores_older_living_form():
    state = _beetle_scene()
    _run(
        "end_srd_transformation_source",
        state,
        target_state="guard",
        source_id=_entity(state)["form_current_source_id"],
    )
    _run(
        "install_srd_transformation_source",
        state,
        target_state="guard",
        caster_state="hero",
        lasting_state="state",
        spell="animal_shapes",
        form_id="giant_fire_beetle",
        actual_spell_level=8,
    )
    old = _entity(state)["form_current_source_id"]
    _run(
        "install_srd_transformation_source",
        state,
        target_state="guard",
        caster_state="other",
        lasting_state="state",
        spell="animal_shapes",
        form_id="giant_rat",
        mode="beast",
        appearance="",
        equipment_policy="meld",
        actual_spell_level=8,
    )
    light = _run("check_srd_form_light", state, actor_state="guard", lasting_state="state").result[
        "context"
    ]
    assert light["_form_dim_light_feet"] == 0
    newer = _entity(state)["form_current_source_id"]
    _run("end_srd_transformation_source", state, target_state="guard", source_id=newer)
    assert _entity(state)["form_current_source_id"] == old
    light = _run("check_srd_form_light", state, actor_state="guard", lasting_state="state").result[
        "context"
    ]
    assert (light["_form_bright_light_feet"], light["_form_dim_light_feet"]) == (10, 20)
    _run("end_srd_transformation_source", state, target_state="guard", source_id=old)
    light = _run("check_srd_form_light", state, actor_state="guard", lasting_state="state").result[
        "context"
    ]
    assert light["_form_dim_light_feet"] == 0


@pytest.mark.parametrize(
    "armor,resistance,hp_after,outcome",
    [(1, [], 9, "hit"), (1, ["fire"], 10, "hit"), (25, [], 10, "miss")],
)
def test_beetle_bite_applies_source_fire_damage_and_actual_type_resistance(
    monkeypatch, armor, resistance, hp_after, outcome
):
    state = _beetle_scene()
    _entity(state, "other").update(
        armor_class=armor, hp=10, max_hp=10, temp_hp=0, damage_resistances=resistance
    )
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=True,
        active_actor="guard",
        current_actor="guard",
        action_available=True,
        attacks_remaining=0,
        normal_action_spent=False,
    )
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    result = _run(
        "attack_srd_transformed_form",
        state,
        actor_state="guard",
        target_state="other",
        attack_name="Bite",
        attack_distance=5,
        actor_can_see_target=True,
        target_can_see_actor=True,
        target_is_charmer=False,
        fear_source_in_sight=False,
    ).result["context"]
    assert result["_attack_outcome"] == outcome
    assert result["_damage_roll"] is None
    assert _entity(state, "other")["hp"] == hp_after
    assert _entity(state)["damage_resistances"] == ["fire"]


def test_public_natural_body_light_reopens_and_actual_expiry_retry_restores_darkness(
    tmp_path,
):
    from tests.srd_full.test_srd_scene_relations import _state

    database = tmp_path / "body-light.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(
            owner,
            "polymorph",
            target_overrides={
                "hp": 19,
                "max_hp": 20,
                "challenge_rating_eighths": 8,
                "ability_scores": dict.fromkeys(
                    ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"),
                    12,
                ),
                "size_rank": 2,
            },
        )
        parameters.update(
            target="guard", transformation_form="giant_fire_beetle", forego_save_targets=["guard"]
        )
        command = ActionCommand(
            campaign_id="casting",
            action_id="pack-form",
            idempotency_key="pack-form",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        bridge = StatelessMcpMechanics(owner)
        assert bridge.execute_action(command).status is ActionStatus.COMMITTED
        source = owner.snapshot("casting")
        world = thaw_json_value(source.state)
        scene = deepcopy(_state()[f"{SRD}.state"]["spatial_scene"])
        scene["entities"][0]["position"] = [0, 0, 0]
        scene["entities"][1]["position"] = [5, 0, 0]
        for entity in scene["entities"]:
            entity["metadata"]["lighting"] = "darkness"
        world[f"{SRD}.state"]["spatial_scene"] = scene
        owner.create_campaign(
            CampaignCreate(
                campaign_id="pack",
                display_name="Saved Natural Body Light",
                writer=WriterRole.DM,
                package_lock=source.package_lock,
                turn_id="pack-turn",
                participants=source.participants,
                initial_state=tuple(
                    StateValue(SRD, k.removeprefix(f"{SRD}."), v) for k, v in world.items()
                ),
            )
        )
        saved = owner.snapshot("pack")
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("pack").state == saved.state
        attack = replace(
            command,
            campaign_id="pack",
            action_id="pack-bite",
            idempotency_key="pack-bite",
            expected_revision=saved.revision,
            turn_id="pack-turn",
            tool_name="resolve_srd_scene_relation",
            parameters={"actor_state": "hero", "target_state": "guard"},
        )
        bridge = StatelessMcpMechanics(reopened)
        result = bridge.execute_action(attack)
        assert result.status is ActionStatus.COMMITTED
        assert {row["label"]: row["value"] for row in result.result["result_card"]["summary"]}[
            "Can see"
        ] is True
        after = reopened.snapshot("pack")
        assert bridge.execute_action(attack) == result
        assert reopened.snapshot("pack").state == after.state

        expiry = replace(
            attack,
            action_id="body-expiry",
            idempotency_key="body-expiry",
            expected_revision=after.revision,
            tool_name="advance_srd_lasting_time",
            parameters={"elapsed_rounds": 600},
        )
        receipt = bridge.execute_action(expiry)
        assert receipt.status is ActionStatus.COMMITTED
        after_expiry = reopened.snapshot("pack")
        assert bridge.execute_action(expiry) == receipt
        assert reopened.snapshot("pack").state == after_expiry.state
        dark = replace(
            attack,
            action_id="body-dark",
            idempotency_key="body-dark",
            expected_revision=after_expiry.revision,
        )
        result = bridge.execute_action(dark)
        assert result.status is ActionStatus.COMMITTED
        assert {row["label"]: row["value"] for row in result.result["result_card"]["summary"]}[
            "Can see"
        ] is False


def _source_attack_state(form, hp=100, armor=1, temp_hp=0):
    state, data, parameters = _setup(
        "polymorph", transformation_form=form, forego_save_targets=["guard"]
    )
    _entity(state)["challenge_rating_eighths"] = 80
    _cast(state, data, parameters)
    _entity(state)["ally_state_ids"] = []
    _entity(state, "other").update(hp=hp, max_hp=100, armor_class=armor, temp_hp=temp_hp)
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=True,
        active_actor="guard",
        current_actor="guard",
        action_available=True,
        attacks_remaining=0,
        normal_action_spent=False,
    )
    return state


def _source_attack(state, name="Bite"):
    return _run(
        "attack_srd_transformed_form",
        state,
        actor_state="guard",
        target_state="other",
        attack_name=name,
        attack_distance=5,
        actor_can_see_target=True,
        target_can_see_actor=True,
        target_is_charmer=False,
        fear_source_in_sight=False,
    ).result["context"]


@pytest.mark.parametrize("hp,expected_sides", [(51, 4), (50, 8), (49, 8)])
def test_blood_hawk_uses_actual_bloodied_hp_including_temporary_hp(monkeypatch, hp, expected_sides):
    state = _source_attack_state("blood_hawk", hp=hp, temp_hp=50)
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    result = _source_attack(state, "Beak")
    assert result["_attack_outcome"] == "hit"
    assert result["_damage_roll"]["sides"] == expected_sides
    assert _entity(state, "other")["hp"] == hp
    assert _entity(state, "other")["temp_hp"] < 50


@pytest.mark.parametrize("hp,expected", [(100, False), (99, True), (50, True)])
def test_shark_advantage_uses_not_full_hp_instead_of_bloodied(monkeypatch, hp, expected):
    state = _source_attack_state("hunter_shark", hp=hp, temp_hp=50)
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    result = _source_attack(state)
    assert result["_form_attack_advantage"] is expected
    assert len(result["_attack_roll"]["results"]) == (2 if expected else 1)


@pytest.mark.parametrize(
    "seed,defense,temp_hp,armor",
    [
        (0, "none", 0, 1),
        (5, "none", 3, 1),
        (5, "poison_immune", 0, 1),
        (5, "piercing_resistant", 0, 1),
        (0, "none", 0, 30),
    ],
)
def test_source_poison_attack_has_one_damage_event_with_actual_critical_and_type_defenses(
    monkeypatch, seed, defense, temp_hp, armor
):
    state = _source_attack_state("venomous_snake", temp_hp=temp_hp, armor=armor)
    target = _entity(state, "other")
    if defense == "poison_immune":
        target["damage_immunities"] = ["poison"]
    if defense == "piercing_resistant":
        target["damage_resistances"] = ["piercing"]
    _run(
        "install_srd_guidance_concentration",
        state,
        caster_state="other",
        target_state="other",
        lasting_state="state",
        casting_ability="wisdom",
        casting_modifier=1,
        casting_attack_bonus=3,
        casting_save_dc=11,
        casting_scaling_level=1,
        casting_concentration_save_modifier=1,
        selected_skill="athletics",
        duration_rounds=10,
    )
    expressions = []
    from sylvia_gm.runtime import _effect_executor

    original = _effect_executor.dice_roll

    def capture(expression, *args, **kwargs):
        expressions.append(expression)
        return original(expression, *args, **kwargs)

    monkeypatch.setattr(_effect_executor, "dice_roll", capture)
    monkeypatch.setattr(dice, "_rng", Random(seed))
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    result = _source_attack(state)
    target = _entity(state, "other")
    if armor == 30:
        assert result["_attack_outcome"] == "miss"
        assert result["_form_extra_damage_roll"] is None
        assert expressions == [20]
        assert target["hp"] == 100
        return
    critical = seed == 5
    assert result["_attack_outcome"] == ("critical_hit" if critical else "hit")
    assert result["_damage_roll"]["count"] == (2 if critical else 1)
    assert result["_form_extra_damage_roll"]["count"] == (2 if critical else 1)
    primary = result["_form_primary_damage_amount"]
    poison = result["_form_extra_damage_amount"]
    incoming = primary // 2 if defense == "piercing_resistant" else primary
    if defense != "poison_immune":
        incoming += poison
    assert result["_effective_damage"] == incoming
    assert target["hp"] == 100 - max(0, incoming - temp_hp)
    assert target["temp_hp"] == max(0, temp_hp - incoming)
    assert expressions.count(20) == 2
    assert result["_form_concentration_save_dc"] == max(10, incoming // 2)


def test_scorpion_critical_doubles_only_actual_poison_dice(monkeypatch):
    state = _source_attack_state("scorpion")
    monkeypatch.setattr(dice, "_rng", Random(5))
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    result = _source_attack(state, "Sting")
    assert result["_attack_outcome"] == "critical_hit"
    assert result["_damage_roll"] is None
    assert result["_form_primary_damage_amount"] == 1
    assert result["_form_extra_damage_roll"]["count"] == 2
    assert result["_form_extra_damage_roll"]["sides"] == 6
    assert _entity(state, "other")["hp"] == 99 - result["_form_extra_damage_amount"]
    assert _entity(state)["form_attack_in_progress"] is False
    assert _entity(state)["form_attack_has_extra_damage"] is False


def test_public_source_poison_attack_reopens_and_retry_keeps_both_damage_rolls(
    tmp_path,
):
    from tests.srd_full.test_srd_scene_relations import _state

    database = tmp_path / "poison-body.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(
            owner,
            "polymorph",
            target_overrides={
                "hp": 19,
                "max_hp": 20,
                "challenge_rating_eighths": 8,
                "ability_scores": dict.fromkeys(
                    ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"),
                    12,
                ),
                "size_rank": 2,
            },
        )
        parameters.update(
            target="guard", transformation_form="venomous_snake", forego_save_targets=["guard"]
        )
        command = ActionCommand(
            campaign_id="casting",
            action_id="pack-form",
            idempotency_key="pack-form",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        bridge = StatelessMcpMechanics(owner)
        assert bridge.execute_action(command).status is ActionStatus.COMMITTED
        source = owner.snapshot("casting")
        world = thaw_json_value(source.state)
        scene = deepcopy(_state()[f"{SRD}.state"]["spatial_scene"])
        scene["entities"][0]["position"] = [5, 5, 0]
        scene["entities"][1].update(id="other", position=[5, 0, 0])
        scene["entities"][1]["metadata"]["participant_ref"] = "other"
        actor_scene = deepcopy(scene["entities"][1])
        actor_scene.update(id="guard", position=[0, 0, 0])
        actor_scene["metadata"]["participant_ref"] = "guard"
        scene["entities"].append(actor_scene)
        world[f"{SRD}.state"]["spatial_scene"] = scene
        world[f"{SRD}.combat"].update(
            encounter_active=True,
            turn_active=True,
            active_actor="guard",
            current_actor="guard",
            active_actor_kind="creature",
            active_actor_can_act=True,
            action_available=True,
            attacks_remaining=0,
            normal_action_spent=False,
        )
        owner.create_campaign(
            CampaignCreate(
                campaign_id="pack",
                display_name="Saved Source Venom Attack",
                writer=WriterRole.DM,
                package_lock=source.package_lock,
                turn_id="pack-turn",
                participants=source.participants,
                initial_state=tuple(
                    StateValue(SRD, k.removeprefix(f"{SRD}."), v) for k, v in world.items()
                ),
            )
        )
        saved = owner.snapshot("pack")
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("pack").state == saved.state
        attack = replace(
            command,
            campaign_id="pack",
            action_id="pack-bite",
            idempotency_key="pack-bite",
            expected_revision=saved.revision,
            turn_id="pack-turn",
            tool_name="attack_srd_transformed_form",
            parameters={
                "actor_state": "guard",
                "target_state": "other",
                "attack_name": "Bite",
                "attack_distance": 5,
                "actor_can_see_target": True,
                "target_can_see_actor": True,
                "target_is_charmer": False,
                "fear_source_in_sight": False,
            },
        )
        bridge = StatelessMcpMechanics(reopened)
        result = bridge.execute_action(attack)
        assert result.status is ActionStatus.COMMITTED
        context = result.result["context"]
        assert context["_form_extra_damage_roll"]["sides"] == 6
        assert context["_form_extra_damage_amount"] == context["_form_extra_damage_roll"]["total"]
        assert (
            context["_effective_damage"]
            == context["_form_primary_damage_amount"] + context["_form_extra_damage_amount"]
        )
        after = reopened.snapshot("pack")
        assert bridge.execute_action(attack) == result
        assert reopened.snapshot("pack").state == after.state
