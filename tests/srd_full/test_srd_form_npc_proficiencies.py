"""Explicit prepared NPC training survives real Shapechange statistics and restoration."""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_public_spell_casting import _create_spell_campaign
from tests.srd_full.test_srd_spell_result_cards import SRD
from tests.srd_full.test_srd_spell_wave6_transformations import _entity, _run, _setup


def _npc_case(**changes):
    state, _, parameters = _setup(
        "shapechange",
        transformation_form="owlbear",
        transformation_seen_before=True,
    )
    hero = _entity(state, "hero")
    prepared = {
        "creature_id": "shaper",
        "name": "Prepared Shaper",
        "source": "Synthetic read stat block: PB6, CR3; explicit ordinary skill/save training "
        "and at-will Shapechange. No inferred level or training.",
        "armor_class": 12,
        "hit_points": 100,
        "speed": 30,
        "size_rank": 2,
        "initiative_modifier": 2,
        "creature_type": "humanoid",
        "proficiency_bonus": 6,
        "challenge_rating_eighths": 24,
        "strength_score": 16,
        "dexterity_score": 14,
        "constitution_score": 14,
        "intelligence_score": 18,
        "wisdom_score": 20,
        "charisma_score": 12,
        "skill_proficiencies": ["perception", "persuasion", "athletics"],
        "saving_throw_proficiencies": ["constitution", "wisdom"],
        "skill_checks": [
            {"skill": "perception", "modifier": 11},
            {"skill": "persuasion", "modifier": 7},
            {"skill": "athletics", "modifier": 9},
        ],
        "constitution_save": 8,
        "wisdom_save": 11,
        "spells": [{"spell": "shapechange", "uses_per_day": 0}],
        "spellcasting_ability": "intelligence",
        "spellcasting_modifier": 4,
        "spell_attack_bonus": 10,
        "spell_save_dc": 18,
        "inventory": deepcopy(hero["inventory"]),
        "spell_materials": deepcopy(hero["spell_materials"]),
    } | changes
    return state, prepared, parameters


def _shape(state, parameters):
    parameters = {
        name: value for name, value in parameters.items() if name not in {"caster", "slot_resource"}
    }
    parameters.update(
        caster_state="shaper", target="shaper", target_distance=0, targets_max_distance=0
    )
    return _run("cast_spell_outside_combat", state, **parameters)


def test_preparation_retains_explicit_training_and_actual_pb():
    state, prepared, _ = _npc_case()
    _run("prepare_srd_creature", state, **prepared)
    npc = _entity(state, "shaper")
    assert npc["proficiency_bonus"] == 6
    assert npc["challenge_rating_eighths"] == 24
    assert npc["skill_proficiencies"] == prepared["skill_proficiencies"]
    assert npc["saving_throw_proficiencies"] == prepared["saving_throw_proficiencies"]


def test_npc_preparation_composition_is_idempotent_and_preserves_nullable_pb_and_cr():
    from scripts.srd_full.srd_form_proficiencies import compose_preparation
    from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

    tools = _load_runtime(
        (Path(__file__).parents[1] / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_text(
            encoding="utf-8"
        )
    )["tools"]
    changed = {}
    compose_preparation(tools, changed)
    once = deepcopy(changed)
    compose_preparation(tools, changed)
    assert changed == once
    parameters = {p["name"]: p for p in changed["prepare_srd_creature"]["parameters"]}
    for name in (
        "proficiency_bonus",
        "challenge_rating_eighths",
        "skill_proficiencies",
        "saving_throw_proficiencies",
    ):
        assert parameters[name]["default"] is None


def test_actual_npc_shape_preserves_training_in_saved_saves_skills_and_reformation():
    state, prepared, parameters = _npc_case()
    _run("prepare_srd_creature", state, **prepared)
    before = deepcopy(_entity(state, "shaper"))
    _shape(state, parameters)
    npc = _entity(state, "shaper")
    assert npc["creature_type"] == "humanoid"
    assert npc["saving_throw_modifiers"]["constitution"] == 9
    assert npc["saving_throw_modifiers"]["wisdom"] == 11
    assert npc["skill_check_modifiers"]["athletics"] == 11
    assert npc["skill_check_modifiers"]["perception"] == 11
    assert npc["skill_check_modifiers"]["persuasion"] == 7
    assert npc["passive_perception"] == 21
    saved = _run(
        "resolve_srd_condition_saving_throw",
        state,
        target="shaper",
        ability="constitution",
        condition="frightened",
        source_save_dc=15,
    )
    assert saved.result["context"]["_combined_modifier"] == 9
    _run(
        "change_srd_transformation",
        state,
        actor_state="shaper",
        form_id="grimlock",
        seen_before=True,
    )
    assert _entity(state, "shaper")["skill_check_modifiers"]["athletics"] == 9
    assert _entity(state, "shaper")["saving_throw_modifiers"]["constitution"] == 7
    _run("end_concentration", state, caster_state="shaper")
    reverted = _entity(state, "shaper")
    assert reverted["skill_check_modifiers"] == before["skill_check_modifiers"]
    assert reverted["saving_throw_modifiers"] == before["saving_throw_modifiers"]
    assert reverted["form_base_npc_proficiencies"] is None


def test_missing_actual_pb_rejects_supplied_training_without_entity_creation():
    state, prepared, _ = _npc_case()
    prepared.pop("proficiency_bonus")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="actual Proficiency Bonus"):
        _run("prepare_srd_creature", state, **prepared)
    assert state == before


@pytest.mark.parametrize("missing", ["skill_proficiencies", "saving_throw_proficiencies"])
def test_unknown_original_training_does_not_claim_npc_shape_support(missing):
    state, prepared, parameters = _npc_case()
    prepared.pop(missing)
    _run("prepare_srd_creature", state, **prepared)
    assert _entity(state, "shaper")[missing] is None
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="explicit skill/save"):
        _shape(state, parameters)
    assert state == before


def test_enhanced_original_training_is_rejected_instead_of_downgraded():
    state, prepared, parameters = _npc_case()
    prepared["skill_checks"][0]["modifier"] = 17
    _run("prepare_srd_creature", state, **prepared)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Enhanced NPC skill"):
        _shape(state, parameters)
    assert state == before


def test_npc_influence_consumes_real_transformed_skill_total(monkeypatch):
    from scripts.srd_full.build_srd_beast_forms import build_index

    state, prepared, parameters = _npc_case(
        spells=[
            {"spell": "shapechange", "uses_per_day": 0},
            {"spell": "speak_with_animals", "uses_per_day": 0},
        ]
    )
    _run("prepare_srd_creature", state, **prepared)
    boar = build_index()["forms"]["boar"]
    _run(
        "prepare_srd_creature",
        state,
        creature_id="boar",
        name="Source Boar",
        source="Pinned SRD Boar printed base statistics; "
        "no unencoded trait is used by this conversation.",
        creature_type="beast",
        armor_class=boar["armor_class"],
        hit_points=boar["hit_points"],
        speed=boar["walk_speed"],
        size_rank=boar["size_rank"],
        initiative_modifier=boar["initiative_modifier"],
    )
    _shape(state, parameters)
    _run(
        "cast_spell_outside_combat",
        state,
        spell="speak_with_animals",
        caster_state="shaper",
        target="shaper",
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    result = _run(
        "communicate_srd_spell",
        state,
        listener_state="shaper",
        speaker_state="boar",
        purpose="influence",
        influence_skill="persuasion",
        target_number=15,
        language="common",
    )
    assert result.result["context"]["_language_npc_modifier"] == 7
    assert result.result["context"]["_language_influence_total"] == 20


def test_public_prepared_npc_shape_training_reopens_retries_and_restores(tmp_path):
    database = tmp_path / "npc-training.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _create_spell_campaign(owner, "shapechange")
        _, prepared, parameters = _npc_case()
        command = ActionCommand(
            campaign_id="casting",
            action_id="prepare-shaper",
            idempotency_key="prepare-shaper",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="prepare_srd_creature",
            parameters=prepared,
        )
        bridge = StatelessMcpMechanics(owner)
        preparation = bridge.execute_action(command)
        assert preparation.status is ActionStatus.COMMITTED
        after_preparation = owner.snapshot("casting")
        npc_parameters = {
            name: value
            for name, value in parameters.items()
            if name not in {"caster", "slot_resource"}
        }
        npc_parameters.update(
            caster_state="shaper", target="shaper", target_distance=0, targets_max_distance=0
        )
        cast = replace(
            command,
            action_id="npc-body",
            idempotency_key="npc-body",
            expected_revision=after_preparation.revision,
            tool_name="cast_spell_outside_combat",
            parameters=npc_parameters,
        )
        receipt = bridge.execute_action(cast)
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        npc = saved.state[f"{SRD}.entities"]["shaper"]
        assert npc["passive_perception"] == 21
        assert npc["skill_check_modifiers"]["athletics"] == 11
        assert npc["saving_throw_modifiers"]["constitution"] == 9
    with AuthorityOwner.open(database, "casting-owner") as owner:
        bridge = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert bridge.execute_action(command) == preparation
        assert bridge.execute_action(cast) == receipt
        assert owner.snapshot("casting").state == saved.state
        check = replace(
            command,
            action_id="saved-npc-save",
            idempotency_key="saved-npc-save",
            expected_revision=saved.revision,
            tool_name="resolve_srd_condition_saving_throw",
            parameters={
                "target": "shaper",
                "ability": "constitution",
                "condition": "frightened",
                "source_save_dc": 15,
            },
        )
        result = bridge.execute_action(check)
        assert result.status is ActionStatus.COMMITTED
        assert result.result["context"]["_combined_modifier"] == 9
        end = replace(
            command,
            action_id="end-npc-body",
            idempotency_key="end-npc-body",
            expected_revision=owner.snapshot("casting").revision,
            tool_name="end_concentration",
            parameters={"caster_state": "shaper"},
        )
        assert bridge.execute_action(end).status is ActionStatus.COMMITTED
        reverted = owner.snapshot("casting").state[f"{SRD}.entities"]["shaper"]
        assert reverted["saving_throw_modifiers"]["constitution"] == 8
        assert reverted["skill_check_modifiers"]["athletics"] == 9
        assert reverted["form_base_npc_proficiencies"] is None
