"""Actual barrier sources constrain later scene movement and owning effect eligibility."""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_spell_result_cards import SRD, _spell_case
from tests.srd_full.test_srd_spell_wave3_orders import _admit_scene


def _position(state, subject):
    return next(e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == subject)


def _setup(spell):
    state, data, parameters = _spell_case(spell)
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    _admit_scene(state)
    state[f"{SRD}.state"].setdefault("lasting_tick", 0)
    for entity in state[f"{SRD}.entities"].values():
        entity.setdefault("size_rank", 2)
    return state, data, parameters


def _cast_leaf(state, spell, **choices):
    from scripts.srd_full.srd_spell_barriers import PROFILES

    result = _run(
        "cast_srd_spell_barrier",
        state,
        caster_state="hero",
        spell=spell,
        slot_level=PROFILES[spell]["base_level"],
        concentration_save_modifier=3,
        save_dc=18,
        **choices,
    )
    return result.result["context"]


def _record(state, source_id):
    return state[f"{SRD}.state"]["spell_barrier_sources"][source_id]


def _reject(state, tool, **parameters):
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run(tool, state, **parameters)
    assert state == before


def test_antilife_actual_body_movement_exclusions_and_concentration_end():
    state, _, _ = _setup("antilife_shell")
    source = _cast_leaf(state, "antilife_shell")["_new_barrier_id"]
    _reject(state, "move_srd_scene_creature", actor_state="guard", destination=[5, 0, 0])
    state[f"{SRD}.entities"]["guard"]["creature_type"] = "construct"
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[5, 0, 0])
    assert _position(state, "guard")["position"] == [5, 0, 0]
    _run("end_concentration", state, caster_state="hero")
    assert _record(state, source)["installed"] is False


def test_antilife_moving_caster_retires_only_shell_forcing_real_crossing():
    state, _, _ = _setup("antilife_shell")
    source = _cast_leaf(state, "antilife_shell")["_new_barrier_id"]
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[15, 0, 0])
    assert _position(state, "hero")["position"] == [15, 0, 0]
    assert _record(state, source)["installed"] is False
    assert _record(state, source)["ended_reason"] == "forced_crossing"
    assert state[f"{SRD}.entities"]["hero"].get("concentration_spell_id") is None


def test_antilife_actual_weapon_exceptions_and_spell_path():
    state, _, _ = _setup("antilife_shell")
    _cast_leaf(state, "antilife_shell")
    call = {"source_subject": "guard", "target_subject": "hero", "effect_kind": "physical"}
    blocked = _run("check_srd_barrier_effect", state, **call).result["context"]
    assert not blocked["_barrier_effect_allowed"]
    for choice in ({"reach_weapon": True}, {"ranged_weapon": True}):
        assert _run("check_srd_barrier_effect", state, **(call | choice)).result["context"][
            "_barrier_effect_allowed"
        ]
    assert _run("check_srd_barrier_effect", state, **(call | {"effect_kind": "spell"})).result[
        "context"
    ]["_barrier_effect_allowed"]


def test_hut_initial_membership_stationary_boundary_and_caster_leaving():
    state, _, _ = _setup("tiny_hut")
    _position(state, "guard")["position"] = [5, 0, 0]
    source = _cast_leaf(state, "tiny_hut")["_new_barrier_id"]
    assert _record(state, source)["allowed_subject_ids"] == ["hero", "guard"]
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[25, 0, 0])
    assert _record(state, source)["installed"] is True
    _reject(state, "move_srd_scene_creature", actor_state="other", destination=[5, 0, 0])
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[5, 0, 0])
    assert _record(state, source)["body"]["position"] == [0, 0, 0]
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[15, 0, 0])
    assert _record(state, source)["installed"] is False
    assert _record(state, source)["ended_reason"] == "caster_left"


def test_hut_partial_creature_fails_actual_cast_and_recast_retires_old_hut():
    state, _, _ = _setup("tiny_hut")
    first = _cast_leaf(state, "tiny_hut")["_new_barrier_id"]
    _position(state, "guard")["position"] = [10.4, 0, 0]
    result = _cast_leaf(state, "tiny_hut")
    assert result["_barrier_cast_failed"] is True
    assert _record(state, first)["installed"] is False
    assert _record(state, first)["ended_reason"] == "recast"
    assert _record(state, result["_new_barrier_id"])["installed"] is False


def test_hut_real_spell_level_and_expiry_change_later_effect_eligibility():
    state, _, _ = _setup("tiny_hut")
    source = _cast_leaf(state, "tiny_hut")["_new_barrier_id"]
    call = {"source_subject": "guard", "target_subject": "hero", "effect_kind": "spell"}
    _reject(state, "check_srd_barrier_effect", **call)
    for level in (0, 1, 3, 4, 9):
        result = _run("check_srd_barrier_effect", state, actual_spell_level=level, **call).result[
            "context"
        ]
        assert result["_barrier_effect_allowed"] is (level >= 4)
    _run("advance_srd_lasting_time", state, elapsed_rounds=4800)
    result = _run("check_srd_barrier_effect", state, actual_spell_level=1, **call).result["context"]
    assert result["_barrier_effect_allowed"] is True
    assert _record(state, source)["installed"] is False


@pytest.mark.parametrize("spell", ["tiny_hut", "antilife_shell"])
def test_ordinary_nonspell_energy_is_not_a_printed_barrier_exclusion(spell):
    state, _, _ = _setup(spell)
    _cast_leaf(state, spell)
    result = _run(
        "check_srd_barrier_effect",
        state,
        source_subject="guard",
        target_subject="hero",
        effect_kind="energy",
    ).result["context"]
    assert result["_barrier_effect_allowed"] is True
    assert result["_barrier_blocking_source_ids"] == []


def test_hut_light_command_changes_actual_later_lighting_and_one_way_observation():
    state, _, _ = _setup("tiny_hut")
    source = _cast_leaf(state, "tiny_hut")["_new_barrier_id"]
    light = {"point": [0, 0, 3], "plane": "material", "ambient": "bright"}
    assert (
        _run("resolve_srd_information_light", state, **light).result["context"][
            "_information_light"
        ]
        == "dim"
    )
    _run(
        "command_srd_tiny_hut",
        state,
        caster_state="hero",
        source_id=source,
        interior_light="darkness",
        exterior_color="blue",
    )
    assert (
        _run("resolve_srd_information_light", state, **light).result["context"][
            "_information_light"
        ]
        == "darkness"
    )
    assert _record(state, source)["exterior_color"] == "blue"
    for start, end, blocked in (([20, 0, 3], [0, 0, 3], True), ([0, 0, 3], [20, 0, 3], False)):
        query = _run(
            "check_srd_barrier_observation", state, start=start, end=end, plane="material"
        ).result["context"]
        assert query["_barrier_observation_blocked"] is blocked
    elsewhere = _run("resolve_srd_information_light", state, **(light | {"point": [20, 0, 3]}))
    assert elsewhere.result["context"]["_information_light"] == "bright"


def test_sphere_subject_cannot_bypass_the_validated_cast_target():
    state, _, _ = _setup("resilient_sphere")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="actual validated target"):
        _cast_leaf(
            state,
            "resilient_sphere",
            subject="other",
            target_willing=True,
            expected_target_state="guard",
            validate_target_binding=True,
        )
    assert state == before


def test_resilient_actual_target_save_and_later_in_out_boundary():
    state, _, _ = _setup("resilient_sphere")
    saved = _cast_leaf(state, "resilient_sphere", subject="guard", target_willing=False)
    assert saved["_sphere_target_saved"] is True
    assert saved["_barrier_cast_failed"] is False
    assert saved["_barrier_save"]["total"] >= 30
    source = _cast_leaf(state, "resilient_sphere", subject="guard", target_willing=True)[
        "_new_barrier_id"
    ]
    _reject(state, "move_srd_scene_creature", actor_state="guard", destination=[25, 0, 0])
    for origin, target in (("hero", "guard"), ("guard", "hero")):
        result = _run(
            "check_srd_barrier_effect",
            state,
            source_subject=origin,
            target_subject=target,
            effect_kind="energy",
        ).result["context"]
        assert not result["_barrier_effect_allowed"]
    _run("end_srd_spell_barrier_source", state, source_id=source)
    assert _record(state, source)["installed"] is False
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[25, 0, 0])


def test_resilient_successful_target_save_still_ends_prior_concentration():
    state, _, _ = _setup("resilient_sphere")
    old = _cast_leaf(state, "antilife_shell")["_new_barrier_id"]
    result = _cast_leaf(state, "resilient_sphere", subject="guard", target_willing=False)
    assert result["_sphere_target_saved"] is True
    assert result["_barrier_cast_failed"] is False
    assert result["_new_barrier_id"] is None
    assert _record(state, old)["installed"] is False
    assert state[f"{SRD}.entities"]["hero"].get("concentration_spell_id") is None


def test_resilient_real_half_speed_roll_spends_action_but_no_ordinary_movement():
    state, _, _ = _setup("resilient_sphere")
    source = _cast_leaf(state, "resilient_sphere", subject="guard", target_willing=True)[
        "_new_barrier_id"
    ]
    state[f"{SRD}.combat"].update(encounter_active=True, turn_active=False, current_actor="guard")
    _run("begin_combat_turn", state, actor="guard")
    before_movement = state[f"{SRD}.combat"].get("movement_spent", 0)
    _reject(
        state,
        "roll_srd_resilient_sphere",
        actor_state="guard",
        source_id=source,
        destination=[36, 0, 0],
    )
    _run(
        "roll_srd_resilient_sphere",
        state,
        actor_state="guard",
        source_id=source,
        destination=[35, 0, 0],
    )
    assert _position(state, "guard")["position"] == [35, 0, 0]
    assert state[f"{SRD}.combat"]["action_available"] is False
    assert state[f"{SRD}.combat"].get("movement_spent", 0) == before_movement


def test_barrier_source_profiles_exact_continuation_and_emission_repeat():
    import json

    from scripts.srd_full.build_srd_5_2_1_spells import _spell_declaration, _spell_mechanics_source
    from scripts.srd_full.srd_spell_barriers import bind_runtime_consumers, extend_runtime
    from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

    root = Path(__file__).parents[1]
    content = root / "rulesets/dnd-srd-5.2.1/content"
    declaration = _spell_declaration((content / "spell-mechanics.yaml").read_text())
    corpora = {
        name: json.loads((content / f"corpus/{name}.json").read_text())
        for name in ("spells", "playing_the_game", "rules_glossary")
    }
    source = _spell_mechanics_source(corpora, declaration)
    text = str(source)
    assert "Ranged or Reach" in text and "affected creature is forced" in text
    runtime = (root / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_text()
    once = bind_runtime_consumers(extend_runtime(runtime))
    twice = bind_runtime_consumers(extend_runtime(once))
    first, second = _load_runtime(once), _load_runtime(twice)
    for name, tool in first["tools"].items():
        if "barrier" in name or name in {
            "cast_spell",
            "end_srd_concentration_node",
            "move_srd_travel_subject",
            "move_srd_scene_creature_environment_segment",
        }:
            assert second["tools"][name] == tool


def test_public_tiny_hut_ritual_reopens_retries_and_original_occupant_moves(monkeypatch, tmp_path):
    import tests.srd_full.test_srd_public_spell_casting as public
    from tests.srd_full.test_srd_extended_casting import _begin

    state, data, parameters = _setup("tiny_hut")
    _position(state, "guard")["position"] = [5, 0, 0]
    parameters.update(casting_mode="ritual", slot_resource="ritual", spellbook_readable=True)
    monkeypatch.setattr(public, "_spell_case", lambda _: (state, data, parameters))
    database = tmp_path / "hut.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(owner, "tiny_hut")
        command = ActionCommand(
            campaign_id="casting",
            action_id="begin-hut",
            idempotency_key="begin-hut",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="begin_srd_spell_cast",
            parameters=_begin(parameters),
        )
        mechanics = StatelessMcpMechanics(owner)
        begin = mechanics.execute_action(command)
        assert begin.status is ActionStatus.COMMITTED, begin.result
        assert mechanics.execute_action(command) == begin
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        mechanics = StatelessMcpMechanics(owner)
        continuation = replace(
            command,
            action_id="continue-hut",
            idempotency_key="continue-hut",
            expected_revision=saved.revision,
            tool_name="continue_srd_spell_cast",
            parameters={
                "caster_state": "hero",
                "elapsed_rounds": 110,
                "uninterrupted": True,
                "spellbook_readable": True,
                "verbal_component_available": True,
                "somatic_component_available": True,
                "material_component_available": True,
            },
        )
        progress = mechanics.execute_action(continuation)
        assert progress.status is ActionStatus.COMMITTED, progress.result
        assert mechanics.execute_action(continuation) == progress
        ready = owner.snapshot("casting")
        assert ready.state[f"{SRD}.state"]["lasting_tick"] == 220
        finish = replace(
            command,
            action_id="finish-hut",
            idempotency_key="finish-hut",
            expected_revision=ready.revision,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        receipt = mechanics.execute_action(finish)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        finished = owner.snapshot("casting")
        assert mechanics.execute_action(finish) == receipt
        assert owner.snapshot("casting").state == finished.state
        records = finished.state[f"{SRD}.state"]["spell_barrier_sources"]
        assert len(records) == 1
        source_id, source = next(iter(records.items()))
        assert source["installed"] is True
        assert source["started_tick"] == 220 and source["expires_tick"] == 220 + 9600
        assert source["allowed_subject_ids"] == ["hero", "guard"]
        assert finished.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 3
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == finished.state
        mechanics = StatelessMcpMechanics(owner)
        move = replace(
            command,
            action_id="leave-hut",
            idempotency_key="leave-hut",
            expected_revision=finished.revision,
            tool_name="move_srd_scene_creature",
            parameters={"actor_state": "guard", "destination": [25, 0, 0]},
        )
        moved = mechanics.execute_action(move)
        assert moved.status is ActionStatus.COMMITTED, moved.result
        after = owner.snapshot("casting")
        assert _position(after.state, "guard")["position"] == [25, 0, 0]
        assert _record(after.state, source_id)["installed"] is True
        assert mechanics.execute_action(move) == moved
        assert owner.snapshot("casting").state == after.state
