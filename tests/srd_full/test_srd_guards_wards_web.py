"""Actual ward-owned stair saves, exact retirement and source-preserving suppression."""

import os
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_guards_wards import extend_runtime as parent_runtime
from scripts.srd_full.srd_guards_wards_web import bind_runtime_consumers, extend_runtime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.runtime._schema_validator import validate_ruleset_data
from tests import test_srd_guards_wards as wards
from tests import test_srd_spell_object_content as objects

SRD = objects.SRD


@lru_cache
def _runtime():
    base = Path(os.environ.get("FC14_MATERIAL_RUNTIME", objects.ROOT / "runtime/ruleset.yaml"))
    text = bind_runtime_consumers(extend_runtime(parent_runtime(base.read_text())))
    assert extend_runtime(text) == text
    assert bind_runtime_consumers(text) == text
    runtime = _load_runtime(text)
    assert not (errors := validate_ruleset_data(runtime)), errors
    return text.encode(), runtime


@lru_cache
def _mechanic(name):
    raw, runtime = _runtime()
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="5.2.1",
        package_sha256="d" * 64,
        runtime_bytes=raw,
        declaration=ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.spells",
            input_schema=owned_payload(_tool_schema(runtime["tools"][name], tool_name=name)),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


@pytest.fixture(autouse=True)
def _owners(monkeypatch):
    monkeypatch.setattr(objects, "_mechanic", _mechanic)


def _case(stairs=None):
    state = objects._state()
    objects._access(state)
    sid = wards._cast_ward(
        state, save_dc=40, stairs=stairs or [{"min": [2, 0, 0], "max": [12, 8, 15]}]
    )
    target = state[f"{SRD}.entities"]["guard"]
    target.setdefault("saving_throw_modifiers", {})["dexterity"] = 0
    target.setdefault("skill_check_modifiers", {})["athletics"] = 100
    area = state[f"{SRD}.state"]["spell_guards_wards_sources"][sid]["web_section_ids"][0]
    return state, sid, area


def _save(state, area, **kwargs):
    return objects._run(
        "resolve_srd_guards_wards_web_event", state, target_state="guard", area_id=area, **kwargs
    )


def test_parent_dc_and_body_are_retained_and_failed_dexterity_is_a_real_restraint():
    state, sid, area = _case()
    _save(state, area)
    target = state[f"{SRD}.entities"]["guard"]
    source = state[f"{SRD}.state"]["spell_guards_wards_sources"][sid]
    receipt = target["last_guards_wards_web_save"]
    assert receipt["outcome"] == "failure" and max(receipt["roll"]["results"]) <= 20
    assert "restrained" in target["conditions"]
    cid = receipt["condition_source_id"]
    condition = state[f"{SRD}.state"]["timed_condition_sources"][cid]
    assert condition["spell_id"] == "guards_and_wards" and condition["save_dc"] == 40
    assert condition["guarded_web_parent_serial"] == source["serial"]
    assert condition["expires_tick"] == source["expires_tick"]
    assert condition["concentration_sequence"] == 0
    before = deepcopy(receipt)
    _save(state, area)
    assert state[f"{SRD}.entities"]["guard"]["last_guards_wards_web_save"] == before
    assert state[f"{SRD}.state"]["spell_guarded_web_sections"][area]["condition_source_ids"] == [
        cid
    ]


def test_parent_group_retirement_preserves_a_genuine_independent_restraint():
    state, sid, area = _case()
    _save(state, area)
    cid = state[f"{SRD}.entities"]["guard"]["last_guards_wards_web_save"]["condition_source_id"]
    independent = objects._run(
        "install_srd_environment_restrained",
        state,
        target_state="guard",
        lasting_state="state",
        spell="entangle",
        condition="restrained",
        save_dc=15,
        duration_rounds=10,
        repeat_save_timing="none",
    )
    other = independent["_timed_condition_source_id"]
    objects._run("end_srd_guards_wards_effect", state, source_id=sid, group="stairs")
    pool = state[f"{SRD}.state"]["timed_condition_sources"]
    assert not pool[cid]["installed"] and pool[other]["installed"]
    assert "restrained" in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert state[f"{SRD}.state"]["spell_guards_wards_sources"][sid]["installed"]


def test_password_exemption_and_other_plane_do_not_produce_a_false_stair_save():
    state, sid, area = _case()
    objects._run(
        "speak_srd_guards_wards_password",
        state,
        actor_state="guard",
        source_id=sid,
        password="silver threshold",
    )
    _save(state, area)
    assert "last_guards_wards_web_save" not in state[f"{SRD}.entities"]["guard"]
    state, _, area = _case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    next(e for e in scene["entities"] if e["id"] == "guard")["metadata"]["plane"] = "ethereal"
    _save(state, area)
    assert "last_guards_wards_web_save" not in state[f"{SRD}.entities"]["guard"]


def test_actual_athletics_escape_spends_one_action_and_retires_only_its_source():
    state, _, area = _case()
    _save(state, area)
    cid = state[f"{SRD}.entities"]["guard"]["last_guards_wards_web_save"]["condition_source_id"]
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=True,
        active_actor="guard",
        action_available=True,
        actions_remaining=1,
    )
    result = objects._run("escape_srd_guards_wards_web", state, actor_state="guard", source_id=cid)
    assert result["_escape_success"] is True
    assert not state[f"{SRD}.state"]["timed_condition_sources"][cid]["installed"]
    assert "restrained" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert state[f"{SRD}.combat"]["action_taken"] == "escape_web"
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        objects._run("escape_srd_guards_wards_web", state, actor_state="guard", source_id=cid)
    assert state == before


def test_temporary_parent_suppression_retains_and_restores_the_original_source():
    state, sid, area = _case()
    _save(state, area)
    target = state[f"{SRD}.entities"]["guard"]
    cid = target["last_guards_wards_web_save"]["condition_source_id"]
    source = state[f"{SRD}.state"]["spell_guards_wards_sources"][sid]
    source["suppressed"] = True
    objects._run(
        "refresh_srd_timed_condition_target", state, target_state="guard", lasting_state="state"
    )
    pool = state[f"{SRD}.state"]["timed_condition_sources"]
    assert pool[cid]["installed"] and pool[cid]["suppressed"]
    assert "restrained" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    state[f"{SRD}.state"]["spell_guards_wards_sources"][sid]["suppressed"] = False
    objects._run(
        "refresh_srd_timed_condition_target", state, target_state="guard", lasting_state="state"
    )
    pool = state[f"{SRD}.state"]["timed_condition_sources"]
    assert pool[cid]["installed"] and not pool[cid]["suppressed"]
    assert "restrained" in state[f"{SRD}.entities"]["guard"]["conditions"]


def test_caster_death_and_real_clock_expiry_follow_parent_stair_lifetime():
    state, _, area = _case()
    _save(state, area)
    state[f"{SRD}.entities"]["hero"].update(is_alive=False, hp=0)
    objects._run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert "restrained" in state[f"{SRD}.entities"]["guard"]["conditions"]
    cid = state[f"{SRD}.entities"]["guard"]["last_guards_wards_web_save"]["condition_source_id"]
    assert state[f"{SRD}.state"]["timed_condition_sources"][cid]["installed"]
    objects._run("advance_srd_lasting_time", state, elapsed_rounds=14399)
    assert not state[f"{SRD}.state"]["timed_condition_sources"][cid]["installed"]
    assert "restrained" not in state[f"{SRD}.entities"]["guard"]["conditions"]


def test_actual_movement_stops_at_failed_stair_entry_and_pays_real_partial_distance():
    state, _, area = _case(stairs=[{"min": [7, 0, 0], "max": [12, 8, 15]}])
    result = objects._run(
        "move_srd_scene_creature", state, actor_state="guard", destination=[10, 2, 0]
    )
    scene_target = next(
        e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "guard"
    )
    assert 3 < scene_target["position"][0] < 7
    target = state[f"{SRD}.entities"]["guard"]
    assert "restrained" in target["conditions"]
    assert target["last_guards_wards_web_save"]["area_id"] == area
    assert result["_scene_movement_distance"] == pytest.approx(scene_target["position"][0] - 3)
    assert target["scene_movement_spent"] == pytest.approx(result["_scene_movement_cost"])


def test_actual_start_turn_is_distinct_from_entry_and_failure_reuses_exact_source():
    state, _, area = _case()
    _save(state, area)
    target = state[f"{SRD}.entities"]["guard"]
    cid = target["last_guards_wards_web_save"]["condition_source_id"]
    state[f"{SRD}.combat"].update(
        encounter_active=True, turn_active=True, active_actor="guard", turn_sequence=1
    )
    objects._run("resolve_srd_environment_hazard_start_turn", state, target_state="guard")
    target = state[f"{SRD}.entities"]["guard"]
    assert target["last_guards_wards_web_save"]["timing"] == "start"
    assert target["last_guards_wards_web_save"]["condition_source_id"] == cid
    assert state[f"{SRD}.state"]["spell_guarded_web_sections"][area]["condition_source_ids"] == [
        cid
    ]
    before = deepcopy(target["last_guards_wards_web_save"])
    objects._run("resolve_srd_environment_hazard_start_turn", state, target_state="guard")
    assert state[f"{SRD}.entities"]["guard"]["last_guards_wards_web_save"] == before


def test_actual_password_after_restraint_masks_source_and_actual_teleport_exit_retires_it():
    state, sid, area = _case()
    _save(state, area)
    cid = state[f"{SRD}.entities"]["guard"]["last_guards_wards_web_save"]["condition_source_id"]
    objects._run(
        "speak_srd_guards_wards_password",
        state,
        actor_state="guard",
        source_id=sid,
        password="silver threshold",
    )
    source = state[f"{SRD}.state"]["timed_condition_sources"][cid]
    assert source["installed"] and source["suppressed"]
    assert "restrained" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    objects._run(
        "move_srd_travel_subject",
        state,
        subject="guard",
        destination=[25, 2, 0],
        plane="material",
        teleport=True,
    )
    assert not state[f"{SRD}.state"]["timed_condition_sources"][cid]["installed"]


def test_real_freedom_source_temporarily_suppresses_stair_restraint_and_expiry_restores_it():
    state, _, area = _case()
    _save(state, area)
    cid = state[f"{SRD}.entities"]["guard"]["last_guards_wards_web_save"]["condition_source_id"]
    objects._run(
        "install_srd_actor_spell_modifier",
        state,
        target_state="guard",
        caster_state="hero",
        lasting_state="state",
        spell="freedom_of_movement",
        mode="freedom",
        save_dc=15,
        duration_rounds=1,
        concentration=False,
    )
    source = state[f"{SRD}.state"]["timed_condition_sources"][cid]
    assert source["installed"] and source["suppressed"]
    assert "restrained" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    objects._run("advance_srd_lasting_time", state, elapsed_rounds=1)
    source = state[f"{SRD}.state"]["timed_condition_sources"][cid]
    assert source["installed"] and not source["suppressed"]
    assert "restrained" in state[f"{SRD}.entities"]["guard"]["conditions"]


def test_first_entry_receipt_is_one_parent_effect_across_two_actual_stair_sections():
    state, _, first = _case(
        stairs=[{"min": [2, 0, 0], "max": [12, 8, 15]}, {"min": [20, 0, 0], "max": [30, 8, 15]}]
    )
    _save(state, first)
    receipt = deepcopy(state[f"{SRD}.entities"]["guard"]["last_guards_wards_web_save"])
    objects._run(
        "move_srd_travel_subject",
        state,
        subject="guard",
        destination=[25, 2, 0],
        plane="material",
        teleport=True,
    )
    second = state[f"{SRD}.state"]["guarded_web_section_ids"][1]
    _save(state, second)
    assert state[f"{SRD}.entities"]["guard"]["last_guards_wards_web_save"] == receipt
    assert "restrained" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    state[f"{SRD}.combat"].update(
        encounter_active=True, turn_active=True, active_actor="guard", turn_sequence=2
    )
    _save(state, second, timing="start")
    assert state[f"{SRD}.entities"]["guard"]["last_guards_wards_web_save"]["area_id"] == second


def test_later_overlapping_same_spell_masks_old_source_then_exact_end_restores_it():
    state, _, first = _case()
    _save(state, first)
    cid = state[f"{SRD}.entities"]["guard"]["last_guards_wards_web_save"]["condition_source_id"]
    second_parent = wards._cast_ward(
        state, save_dc=40, stairs=[{"min": [2, 0, 0], "max": [12, 8, 15]}]
    )
    objects._run(
        "refresh_srd_timed_condition_target", state, target_state="guard", lasting_state="state"
    )
    assert state[f"{SRD}.state"]["timed_condition_sources"][cid]["suppressed"]
    assert "restrained" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    objects._run("end_srd_guards_wards_effect", state, source_id=second_parent, group="stairs")
    objects._run(
        "refresh_srd_timed_condition_target", state, target_state="guard", lasting_state="state"
    )
    assert state[f"{SRD}.state"]["timed_condition_sources"][cid]["installed"]
    assert not state[f"{SRD}.state"]["timed_condition_sources"][cid]["suppressed"]
    assert "restrained" in state[f"{SRD}.entities"]["guard"]["conditions"]


def test_actual_unrestrained_move_inside_web_pays_double_distance_without_another_entry():
    state, _, area = _case()
    _save(state, area)
    cid = state[f"{SRD}.entities"]["guard"]["last_guards_wards_web_save"]["condition_source_id"]
    objects._run("escape_srd_guards_wards_web", state, actor_state="guard", source_id=cid)
    result = objects._run(
        "move_srd_scene_creature", state, actor_state="guard", destination=[8, 2, 0]
    )
    assert result["_scene_movement_distance"] == 5
    assert result["_scene_movement_cost"] == 10
    assert "restrained" not in state[f"{SRD}.entities"]["guard"]["conditions"]


def test_public_paid_hour_stairs_use_derived_dc_then_saved_movement_and_retry(
    monkeypatch, tmp_path
):
    from dataclasses import replace

    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus, WriterRole

    original_case = wards.cards._spell_case

    def source_case(spell):
        state, data, parameters = original_case(spell)
        parameters["guards_wards_stairs"] = [{"min": [7, 0, 0], "max": [12, 8, 15]}]
        state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["dexterity"] = -30
        return state, data, parameters

    monkeypatch.setattr(wards.cards, "_spell_case", source_case)
    monkeypatch.setattr(wards, "extend_runtime", lambda text: extend_runtime(parent_runtime(text)))
    original_paid = wards._paid_runtime.__wrapped__

    @lru_cache
    def paid():
        raw, _ = original_paid()
        text = bind_runtime_consumers(raw.decode())
        return text.encode(), _load_runtime(text)

    monkeypatch.setattr(wards, "_paid_runtime", paid)
    wards.test_public_paid_hour_cast_reopens_and_retry_retains_real_ward_and_reusable_silver_rod(
        monkeypatch, tmp_path
    )
    database = tmp_path / "guards-wards.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        snapshot = owner.snapshot("casting")
        ward = next(iter(snapshot.state[f"{SRD}.state"]["spell_guards_wards_sources"].values()))
        assert ward["save_dc"] == 18  # Actual level17 Wizard, Intelligence18 and PB6.
        assert len(ward["web_section_ids"]) == 1
        command = ActionCommand(
            campaign_id="casting",
            action_id="enter-stairs",
            idempotency_key="enter-stairs",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=snapshot.revision,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="move_srd_scene_creature",
            parameters={"actor_state": "guard", "destination": [10, 2, 0]},
        )
        receipt = StatelessMcpMechanics(owner).execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        finished = owner.snapshot("casting")
        target = finished.state[f"{SRD}.entities"]["guard"]
        assert "restrained" in target["conditions"]
        cid = target["last_guards_wards_web_save"]["condition_source_id"]
        assert finished.state[f"{SRD}.state"]["timed_condition_sources"][cid]["save_dc"] == 18
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == finished.state
        assert StatelessMcpMechanics(owner).execute_action(replace(command)) == receipt
        assert owner.snapshot("casting").state == finished.state
