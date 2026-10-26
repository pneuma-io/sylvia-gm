"""Non-Beast bodies reuse real statistics, type gates, attacks and saved source lifetimes."""

from copy import deepcopy
from dataclasses import replace
from random import Random

import pytest
from scripts.srd_full.build_srd_beast_forms import build_index as beasts
from scripts.srd_full.build_srd_non_beast_forms import build_index

from sylvia_gm import dice
from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner, CampaignCreate
from sylvia_gm.domain.authority import ActionStatus, StateValue, WriterRole, thaw_json_value
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_public_spell_casting import _create_spell_campaign
from tests.srd_full.test_srd_spell_result_cards import SRD
from tests.srd_full.test_srd_spell_wave6_transformations import _cast, _entity, _setup, _source_attack


def test_non_beast_catalogue_preserves_source_and_refuses_unowned_clauses():
    index = build_index()
    assert index["form_count"] == len(index["forms"]) == 240
    forms = index["forms"]
    assert {name for name, row in forms.items() if row["execution_supported"]} == {
        "grimlock",
        "owlbear",
        "violet_fungus",
    }
    assert set(forms).isdisjoint(beasts()["forms"])
    assert forms["owlbear"]["challenge_rating_eighths"] == 24
    assert forms["owlbear"]["other_speeds"] == {"climb": 40}
    assert forms["grimlock"]["attacks"]["Bone Cudgel"]["extra_damage_type"] == "psychic"
    for row in forms.values():
        assert row["execution_supported"] is (not row["unsupported_clauses"])
        assert row["source_stat_block"].startswith(row["name"] + "\n")
        assert row["source"]["page_text_sha256"]
    for name in ("gargoyle", "giant_eagle", "awakened_shrub", "bandit"):
        assert forms[name]["unsupported_clauses"]
    assert "Flyby" in " ".join(forms["gargoyle"]["unsupported_clauses"])
    assert "Gear" in " ".join(forms["bandit"]["unsupported_clauses"])
    assert forms["violet_fungus"]["condition_immunities"] == [
        "blinded",
        "charmed",
        "deafened",
        "frightened",
    ]


@pytest.mark.parametrize("spell", ["polymorph", "animal_shapes"])
def test_beast_only_spells_reject_non_beast_before_any_cost(spell):
    state, data, parameters = _setup(spell, transformation_form="owlbear")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="requires a Beast"):
        _cast(state, data, parameters)
    assert state == before


def test_shapechange_preserves_person_and_communicates_while_using_owlbear_body():
    state, data, parameters = _setup(
        "shapechange", transformation_form="owlbear", transformation_seen_before=True
    )
    _entity(state, "hero").update(creature_type="humanoid", languages=["common"])
    hero = deepcopy(_entity(state, "hero"))
    _cast(state, data, parameters)
    body = _entity(state, "hero")
    assert (body["speed"], body["climb_speed"], body["darkvision_feet"], body["temp_hp"]) == (
        40,
        40,
        60,
        59,
    )
    assert body["form_ability_scores"]["strength"] == 20
    assert body["form_ability_scores"]["intelligence"] == data["abilities"]["intelligence"]
    assert body["creature_type"] == hero["creature_type"]
    assert body["languages"] == hero["languages"]
    assert body["form_can_speak"] is True and body["form_can_cast"] is True
    _run(
        "change_srd_transformation",
        state,
        participant_data=data,
        actor_state="hero",
        target_character="hero",
        form_id="grimlock",
        seen_before=True,
    )
    body = _entity(state, "hero")
    assert body["speed"] == 30 and body["blindsight_feet"] == 30
    assert body["climb_speed"] == 30
    assert body["temp_hp"] == 59
    _run("end_concentration", state, caster_state="hero")
    assert _entity(state, "hero")["speed"] == hero["speed"]
    assert _entity(state, "hero")["temp_hp"] == 0


@pytest.mark.parametrize(
    "form,name,kind",
    [
        ("owlbear", "Rend", "monstrosity"),
        ("grimlock", "Bone Cudgel", "aberration"),
    ],
)
def test_true_polymorph_non_beast_uses_actual_type_attack_and_restores_after_removal(
    monkeypatch, form, name, kind
):
    state, data, parameters = _setup(
        "true_polymorph", transformation_form=form, forego_save_targets=["guard"]
    )
    original = deepcopy(_entity(state))
    _cast(state, data, parameters)
    body = _entity(state)
    source_id = body["form_current_source_id"]
    assert body["creature_type"] == kind
    assert body["languages"] == [] and body["form_can_speak"] is False
    _entity(state, "other").update(hp=100, max_hp=100, temp_hp=0, armor_class=1)
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=True,
        active_actor="guard",
        current_actor="guard",
        action_available=True,
        normal_action_spent=False,
        attacks_remaining=0,
    )
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    result = _source_attack(state, name)
    profile = build_index()["forms"][form]["attacks"][name]
    assert result["_attack_roll"]["modifier"] == profile["attack_modifier"]
    assert result["_effective_damage"] > 0
    assert _entity(state, "other")["hp"] == 100 - result["_effective_damage"]
    if form == "grimlock":
        assert result["_form_extra_damage_roll"]["sides"] == 4
    _run("end_srd_transformation_source", state, target_state="guard", source_id=source_id)
    assert _entity(state)["creature_type"] == original["creature_type"]
    assert _entity(state)["speed"] == original["speed"]


def test_non_beast_body_suppresses_preexisting_immune_conditions():
    state, data, parameters = _setup(
        "true_polymorph", transformation_form="violet_fungus", forego_save_targets=["guard"]
    )
    _entity(state)["conditions"] = ["charmed", "frightened", "blinded", "deafened"]
    _cast(state, data, parameters)
    source_id = _entity(state)["form_current_source_id"]
    assert _entity(state)["conditions"] == []
    _run("end_srd_transformation_source", state, target_state="guard", source_id=source_id)
    assert set(_entity(state)["conditions"]) == {"charmed", "frightened", "blinded", "deafened"}


def test_public_true_polymorph_owlbear_reopens_and_named_attack_retry_keeps_cost(
    tmp_path,
):
    from tests.srd_full.test_srd_scene_relations import _state

    database = tmp_path / "owlbear-body.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(
            owner,
            "true_polymorph",
            target_overrides={
                "hp": 19,
                "max_hp": 20,
                "challenge_rating_eighths": 32,
                "ability_scores": dict.fromkeys(
                    ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"),
                    12,
                ),
                "size_rank": 2,
            },
        )
        parameters.update(
            target="guard", transformation_form="owlbear", forego_save_targets=["guard"]
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
                display_name="Saved Source Owlbear Attack",
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
                "attack_name": "Rend",
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
        assert context["_attack_roll"]["modifier"] == 7
        assert context["_damage_roll"]["sides"] == 8
        assert context["_damage_roll"]["count"] in (2, 4)
        assert context["_effective_damage"] == context["_damage_roll"]["total"] + 5
        actual_body = reopened.snapshot("pack").state[f"{SRD}.entities"]["guard"]
        assert actual_body["creature_type"] == "monstrosity" and actual_body["climb_speed"] == 40
        assert reopened.snapshot("pack").state[f"{SRD}.combat"]["action_available"] is False
        after = reopened.snapshot("pack")
        assert bridge.execute_action(attack) == result
        assert reopened.snapshot("pack").state == after.state
