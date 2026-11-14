"""Real source volumes govern later paid effects, Magic actions and ordinary containment."""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from random import Random

import pytest
from scripts.srd_full.srd_spell_magic_zones import (
    PROFILES,
    bind_effect_consumers,
    bind_runtime_consumers,
    extend_runtime,
    normalize_runtime,
)
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_spell_result_cards import SRD, _spell_case
from tests.srd_full.test_srd_spell_wave3_orders import _admit_scene


def _case(spell="chromatic_orb"):
    state, data, parameters = _spell_case(spell)
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    state[f"{SRD}.state"].setdefault("lasting_tick", 0)
    _admit_scene(state)
    return state, data, parameters


def _position(state, actor):
    return next(e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == actor)


def _cast_zone(state, spell, **choices):
    caster = choices.pop("caster_state", "hero")
    slot_level = choices.pop("slot_level", PROFILES[spell]["base_level"])
    result = _run(
        "cast_srd_spell_magic_zone",
        state,
        caster_state=caster,
        spell=spell,
        slot_level=slot_level,
        concentration_save_modifier=3,
        save_dc=18,
        **choices,
    )
    return result.result["context"]["_new_magic_zone_id"]


def _reject(state, tool, *, error=None, **parameters):
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match=error):
        _run(tool, state, **parameters)
    assert state == before


def test_globe_is_immobile_and_uses_actual_slot_level_and_cast_origin():
    state, _, _ = _case()
    _position(state, "guard")["position"] = [5, 0, 0]
    source_id = _cast_zone(state, "globe_of_invulnerability")
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[30, 0, 0])
    parameters = {"source_subject": "hero", "target_subject": "guard", "effect_kind": "spell"}
    result = _run("check_srd_magic_zone_effect", state, **parameters, actual_spell_level=5)
    assert result.result["context"]["_magic_zone_effect_allowed"] is False
    assert (
        _run("check_srd_magic_zone_effect", state, **parameters, actual_spell_level=6).result[
            "context"
        ]["_magic_zone_effect_allowed"]
        is True
    )
    assert (
        _run(
            "check_srd_magic_zone_effect", state, **(parameters | {"effect_kind": "physical"})
        ).result["context"]["_magic_zone_effect_allowed"]
        is True
    )
    assert state[f"{SRD}.state"]["spell_magic_zone_sources"][source_id]["body"]["position"] == [
        0,
        0,
        0,
    ]
    _run("end_concentration", state, caster_state="hero")
    assert (
        _run("check_srd_magic_zone_effect", state, **parameters, actual_spell_level=1).result[
            "context"
        ]["_magic_zone_effect_allowed"]
        is True
    )


def test_antimagic_moves_with_actual_caster_and_blocks_real_magic_action_only():
    state, _, _ = _case()
    _position(state, "guard")["position"] = [5, 0, 0]
    source = _cast_zone(state, "antimagic_field")
    combat = state[f"{SRD}.combat"]
    combat.update(
        encounter_active=True,
        turn_active=True,
        active_actor="guard",
        current_actor="guard",
        action_available=True,
        normal_action_spent=False,
        action_surge_available=False,
    )
    _reject(state, "spend_srd_action", error="Antimagic", magic_action=True)
    _run("spend_srd_action", state, magic_action=False)
    assert state[f"{SRD}.combat"]["normal_action_spent"] is True
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[30, 0, 0])
    assert (
        _run("check_srd_magic_zone_cast", state, actor_state="guard").result["context"][
            "_magic_zone_can_cast"
        ]
        is True
    )
    assert state[f"{SRD}.state"]["spell_magic_zone_sources"][source]["installed"] is True


def test_forcecage_real_body_containment_and_partial_push_inputs_fail_clearly():
    state, _, _ = _case()
    source = _cast_zone(state, "forcecage", origin=[20, 0, 3])
    assert state[f"{SRD}.state"]["spell_magic_zone_sources"][source]["trapped_state_ids"] == [
        "guard"
    ]
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[22, 0, 0])
    _reject(state, "move_srd_scene_creature", actor_state="guard", destination=[40, 0, 0])
    _run("end_concentration", state, caster_state="hero")
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[40, 0, 0])
    assert _position(state, "guard")["position"] == [40, 0, 0]
    partial, _, _ = _case()
    _reject(
        partial,
        "cast_srd_spell_magic_zone",
        error="partial-body",
        caster_state="hero",
        spell="forcecage",
        slot_level=7,
        concentration_save_modifier=3,
        save_dc=18,
        origin=[20, 0, 0],
        prison_shape="box",
        prison_side=5,
    )


def test_genuine_paid_globe_then_paid_orb_retains_cost_without_attack_or_damage():
    state, data, orb_parameters, globe_parameters = _dual_globe_orb_case()
    _run("cast_spell_outside_combat", state, participant_data=data, **globe_parameters)
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"] == 2
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[30, 0, 0])
    guard_before = deepcopy(state[f"{SRD}.entities"]["guard"])
    result = _run("cast_spell_outside_combat", state, participant_data=data, **orb_parameters)
    context = result.result["context"]
    assert context["_spell_attack_outcome"] == "no_effect"
    assert context["_spell_damage"] == 0
    assert state[f"{SRD}.entities"]["guard"]["hp"] == guard_before["hp"]
    assert state[f"{SRD}.entities"]["guard"]["temp_hp"] == guard_before["temp_hp"]
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 2
    assert not context.get("_spell_attack_roll")


def _dual_globe_orb_case():
    state, data, orb_parameters = _case("chromatic_orb")
    _, _, globe_parameters = _spell_case("globe_of_invulnerability")
    hero = state[f"{SRD}.entities"]["hero"]
    data["spellcasting_grants"][0]["spells"].append("globe_of_invulnerability")
    data["wizard_spellbook"].append("globe_of_invulnerability")
    hero["prepared_spells"].append("globe_of_invulnerability")
    hero["spell_materials"].append(
        {
            "id": "globe_bead",
            "inventory_item": "real_glass_bead",
            "kind": "specific",
            "components": ["globe_of_invulnerability.1"],
            "quantity": 1,
            "value_cp": 0,
        }
    )
    hero["inventory"].append("real_glass_bead")
    _position(state, "guard")["position"] = [5, 0, 0]
    globe_parameters["material_item_ids"] = ["globe_bead"]
    orb_parameters.update(target_distance=25, selected_damage_type="fire")
    return state, data, orb_parameters, globe_parameters


def test_magic_zone_emitter_and_movement_binder_repeat_without_growing_guards():
    root = Path(__file__).parents[1]
    text = (root / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_text(encoding="utf-8")
    first = bind_effect_consumers(bind_runtime_consumers(extend_runtime(text)))
    assert bind_effect_consumers(bind_runtime_consumers(extend_runtime(first))) == first
    normalized = normalize_runtime(first)
    leaf = _load_runtime(normalized)["tools"]["cast_spell_attack_damage"]
    assert any(e.get("set", {}).get("field") == "_spell_critical_hit" for e in leaf["effects"])
    assert bind_effect_consumers(normalized) == first


def test_saved_spell_hit_rechecks_actual_zone_without_reroll_or_second_cost(monkeypatch):
    state, data, orb_parameters, globe_parameters = _dual_globe_orb_case()
    _run("cast_spell_outside_combat", state, participant_data=data, **globe_parameters)
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[30, 0, 0])
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[20, 0, 0])
    orb_parameters["target_distance"] = 10
    monkeypatch.setattr(dice, "_rng", Random(1))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    _run("begin_srd_spell_action", state, participant_data=data, **orb_parameters)
    event_id = state[f"{SRD}.state"]["pending_srd_action_stack"][-1]
    _run("complete_srd_pending_action", state, participant_data=data, pending_action_id=event_id)
    event = deepcopy(state[f"{SRD}.state"]["pending_srd_actions"][event_id])
    assert event["stage"] == "spell_attack_reaction"
    hero_before = deepcopy(state[f"{SRD}.entities"]["hero"])
    guard_before = deepcopy(state[f"{SRD}.entities"]["guard"])
    _run(
        "move_srd_travel_subject",
        state,
        subject="guard",
        destination=[5, 0, 0],
        plane="material",
        teleport=False,
    )
    result = _run(
        "complete_srd_pending_action",
        state,
        participant_data=data,
        pending_action_id=event_id,
    ).result["context"]
    assert result["_spell_attack_outcome"] == "no_effect"
    assert result["_spell_attack"] == event["spell_attack_context"]["_spell_attack"]
    assert state[f"{SRD}.entities"]["hero"]["resources"] == hero_before["resources"]
    assert state[f"{SRD}.entities"]["guard"]["hp"] == guard_before["hp"]
    assert state[f"{SRD}.entities"]["guard"]["temp_hp"] == guard_before["temp_hp"]
    assert event_id not in state[f"{SRD}.state"]["pending_srd_actions"]
    assert not state[f"{SRD}.state"]["pending_srd_action_stack"]


def test_higher_slot_globe_changes_actual_threshold_without_blocking_nonspell_magic():
    state, _, _ = _case()
    _position(state, "guard")["position"] = [5, 0, 0]
    _cast_zone(state, "globe_of_invulnerability", slot_level=7)
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[30, 0, 0])
    params = {"source_subject": "hero", "target_subject": "guard", "effect_kind": "spell"}
    assert not _run("check_srd_magic_zone_effect", state, **params, actual_spell_level=6).result[
        "context"
    ]["_magic_zone_effect_allowed"]
    assert _run("check_srd_magic_zone_effect", state, **params, actual_spell_level=7).result[
        "context"
    ]["_magic_zone_effect_allowed"]
    assert _run(
        "check_srd_magic_zone_effect", state, **(params | {"effect_kind": "other_magic"})
    ).result["context"]["_magic_zone_effect_allowed"]


def test_real_paid_harm_is_excluded_without_save_damage_or_hp_maximum_reduction():
    state, data, parameters = _case("harm")
    _position(state, "other")["position"] = [0, 0, 0]
    _position(state, "guard")["position"] = [5, 0, 0]
    _position(state, "hero")["position"] = [30, 0, 0]
    _cast_zone(state, "antimagic_field", caster_state="other")
    before = deepcopy(state[f"{SRD}.entities"]["guard"])
    parameters["target_distance"] = 25
    result = _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    context = result.result["context"]
    assert context["_spell_save_outcome"] == "no_effect"
    assert context["_spell_save"] is None and context["_spell_damage"] == 0
    target = state[f"{SRD}.entities"]["guard"]
    assert (target["hp"], target["temp_hp"], target["max_hp"]) == (
        before["hp"],
        before["temp_hp"],
        before["max_hp"],
    )
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"] == 2


def test_public_paid_globe_reopen_retry_keeps_immobile_source_and_blocks_paid_orb(
    monkeypatch,
    tmp_path,
):
    import tests.srd_full.test_srd_public_spell_casting as public

    state, data, orb_parameters, globe_parameters = _dual_globe_orb_case()
    monkeypatch.setattr(public, "_spell_case", lambda _: (state, data, globe_parameters))
    database = tmp_path / "globe.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        public._create_spell_campaign(owner, "globe_of_invulnerability")
        mechanics = StatelessMcpMechanics(owner)
        cast = ActionCommand(
            campaign_id="casting",
            action_id="globe",
            idempotency_key="globe",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=globe_parameters,
        )
        receipt = mechanics.execute_action(cast)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting")
        assert mechanics.execute_action(cast) == receipt
        assert owner.snapshot("casting").state == saved.state
        source_id, source = next(
            iter(saved.state[f"{SRD}.state"]["spell_magic_zone_sources"].items())
        )
        assert source["installed"] and source["spell_level"] == 6
        assert source["body"]["position"] == [0, 0, 0]
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"] == 2
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        mechanics = StatelessMcpMechanics(owner)
        time = replace(
            cast,
            action_id="time",
            idempotency_key="time",
            expected_revision=owner.snapshot("casting").revision,
            tool_name="advance_srd_lasting_time",
            parameters={"elapsed_rounds": 1},
        )
        assert mechanics.execute_action(time).status is ActionStatus.COMMITTED
        move = replace(
            cast,
            action_id="move",
            idempotency_key="move",
            expected_revision=owner.snapshot("casting").revision,
            tool_name="move_srd_scene_creature",
            parameters={"actor_state": "hero", "destination": [30, 0, 0]},
        )
        assert mechanics.execute_action(move).status is ActionStatus.COMMITTED
        target_before = deepcopy(owner.snapshot("casting").state[f"{SRD}.entities"]["guard"])
        orb = replace(
            cast,
            action_id="orb",
            idempotency_key="orb",
            expected_revision=owner.snapshot("casting").revision,
            parameters=orb_parameters,
        )
        receipt = mechanics.execute_action(orb)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        final = owner.snapshot("casting")
        assert mechanics.execute_action(orb) == receipt
        assert owner.snapshot("casting").state == final.state
        target = final.state[f"{SRD}.entities"]["guard"]
        assert (target["hp"], target["temp_hp"]) == (target_before["hp"], target_before["temp_hp"])
        assert final.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 2
        assert final.state[f"{SRD}.state"]["spell_magic_zone_sources"][source_id]["body"][
            "position"
        ] == [0, 0, 0]
