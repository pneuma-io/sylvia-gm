"""Actual ward children stop providing door benefits without retiring independent locks."""

import os
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.srd_guards_wards_doors import bind_door_consumers

from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import MechanicsRejected, RulesetPreconditionRejected
from tests import test_srd_guards_wards_source_adoption as ward
from tests import test_srd_public_spell_casting as public
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_shared_source_adoption import _scene_row

budget = ward.budget
SRD = ward.SRD


@pytest.fixture(autouse=True)
def _compiled_owner(monkeypatch):
    yield from ward.lifetime._compiled_owner.__wrapped__(monkeypatch)


def _case(*, independent=False):
    state, data, params = ward._case()
    _scene_row(state, "guard")["position"] = [0, 1, 0]
    if independent:
        materials = ward._add_spell(state, data, "arcane_lock")
        budget._run(
            "cast_spell_outside_combat",
            state,
            participant_data=data,
            **(
                params
                | {
                    "spell": "arcane_lock",
                    "slot_resource": "spell_slot_2",
                    "material_item_ids": materials,
                    "object_target_id": "door",
                    "object_password": "independent",
                }
            ),
        )
    return state, data, params


def _operate(state, operation="open", *, password=""):
    return budget._run(
        "operate_srd_locked_object",
        state,
        actor_state="guard",
        object_id="door",
        operation=operation,
        password=password,
    )


def _anti(state, data, params):
    materials = ward._add_spell(state, data, "antimagic_field")
    return budget._run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **(
            params
            | {
                "spell": "antimagic_field",
                "target": "hero",
                "slot_resource": "spell_slot_8",
                "material_item_ids": materials,
            }
        ),
    )


def test_retained_parent_suppression_stops_only_its_child_and_restores_without_new_lifetime():
    state, data, params = _case()
    ward._cast(state, data, params)
    source_id, record = ward._ward(state)
    deadline = record["expires_tick"]
    original_locks = deepcopy(ward._locks(state))
    with pytest.raises(RulesetPreconditionRejected, match="Arcane Lock prevents"):
        _operate(state)
    # Exercise the existing retained-parent suppression contract at its owning reader.
    ward._ward(state)[1]["suppressed"] = True
    _operate(state)
    assert ward._locks(state) == original_locks
    assert ward._ward(state)[1]["installed"] is True
    ward._ward(state)[1]["suppressed"] = False
    with pytest.raises(RulesetPreconditionRejected, match="Arcane Lock prevents"):
        _operate(state, "close")
    assert ward._ward(state)[0] == source_id and ward._ward(state)[1]["expires_tick"] == deadline


def test_suppressed_newer_child_preserves_independent_lock_and_password_updates_exact_source():
    state, data, params = _case(independent=True)
    independent = deepcopy(ward._locks(state)[0])
    ward._cast(state, data, params)
    original_child = deepcopy(ward._locks(state)[1])
    ward._ward(state)[1]["suppressed"] = True
    with pytest.raises(RulesetPreconditionRejected, match="Arcane Lock prevents"):
        _operate(state)
    _operate(state, "speak_password", password="independent")
    locks = ward._locks(state)
    assert locks[0]["source_id"] == independent["source_id"]
    assert locks[0]["password_until_tick"] == state[f"{SRD}.state"]["lasting_tick"] + 20
    assert locks[1] == original_child
    _operate(state)
    ward._ward(state)[1]["suppressed"] = False
    with pytest.raises(RulesetPreconditionRejected, match="Arcane Lock prevents"):
        _operate(state, "close")


def test_actual_paid_antimagic_at_door_suppresses_both_real_locks_then_restores_them():
    state, data, params = _case(independent=True)
    ward._cast(state, data, params)
    original = deepcopy(ward._locks(state))
    deadline = ward._ward(state)[1]["expires_tick"]
    _anti(state, data, params)
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_8"] == 2
    _operate(state)
    _operate(state, "close")
    assert ward._locks(state) == original
    budget._run("end_srd_concentration_voluntarily", state, caster_state="hero")
    with pytest.raises(RulesetPreconditionRejected, match="Arcane Lock prevents"):
        _operate(state)
    assert ward._locks(state) == original and ward._ward(state)[1]["expires_tick"] == deadline


def test_real_expiry_during_parent_suppression_keeps_independent_lock_and_never_restores_child():
    state, data, params = _case(independent=True)
    independent = deepcopy(ward._locks(state)[0])
    ward._cast(state, data, params)
    source_id, source = ward._ward(state)
    source["suppressed"] = True
    remaining = ward._ward(state)[1]["expires_tick"] - state[f"{SRD}.state"]["lasting_tick"]
    budget._run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=remaining)
    source = state[f"{SRD}.state"]["spell_guards_wards_sources"][source_id]
    assert source["installed"] is False
    source["suppressed"] = False
    assert ward._locks(state) == [independent]
    with pytest.raises(RulesetPreconditionRejected, match="Arcane Lock prevents"):
        _operate(state)


def test_door_binding_is_semantically_idempotent():
    text = (budget.ROOT / "runtime/ruleset.yaml").read_text()
    first = bind_door_consumers(text)
    assert yaml.load(first, Loader=yaml.CSafeLoader) == yaml.load(
        bind_door_consumers(first), Loader=yaml.CSafeLoader
    )


def test_public_paid_antimagic_reopen_door_access_and_restoration_retry(tmp_path, monkeypatch):
    parent = os.environ.get("FC14_SOURCE_PACKAGE_ROOT")
    if os.environ.get("FC14_SOURCE_RUNTIME_ROOT"):
        assert parent
        monkeypatch.setattr(bundled_packages, "_bundled_srd_root", lambda: Path(parent))
        monkeypatch.setattr(public, "bundled_srd_archive", ward.lifetime._composed_archive)

    def prepared_case(_spell):
        state, data, params = _case()
        materials = ward._add_spell(state, data, "antimagic_field")
        params["door_antimagic_materials"] = materials
        return state, data, params

    monkeypatch.setattr(public, "_spell_case", prepared_case)
    path = tmp_path / "doors.db"
    with AuthorityOwner.open(path, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(owner, "guards_and_wards")
        anti_materials = params.pop("door_antimagic_materials")
        begin, _ = _act(owner, "begin_srd_spell_cast", **ward._begin(params))
        assert begin.status is ActionStatus.COMMITTED, begin.result
        assert (
            owner.snapshot("casting").state[f"{SRD}.entities"]["hero"]["extended_casting"][
                "materials"
            ]
            == params["material_item_ids"]
        )
        continued, _ = _act(
            owner,
            "continue_srd_spell_cast",
            caster_state="hero",
            elapsed_rounds=600,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        assert continued.status is ActionStatus.COMMITTED, continued.result
        cast, _ = _act(owner, "cast_spell_outside_combat", **params)
        assert cast.status is ActionStatus.COMMITTED, cast.result
        anti, anti_command = _act(
            owner,
            "cast_spell_outside_combat",
            **(
                params
                | {
                    "spell": "antimagic_field",
                    "target": "hero",
                    "slot_resource": "spell_slot_8",
                    "material_item_ids": anti_materials,
                }
            ),
        )
        assert anti.status is ActionStatus.COMMITTED, anti.result
        before_reopen = owner.snapshot("casting")
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == before_reopen
        opened, open_command = _act(
            owner,
            "operate_srd_locked_object",
            actor_state="guard",
            object_id="door",
            operation="open",
            password="",
        )
        assert opened.status is ActionStatus.COMMITTED, opened.result
        closed, _ = _act(
            owner,
            "operate_srd_locked_object",
            actor_state="guard",
            object_id="door",
            operation="close",
            password="",
        )
        assert closed.status is ActionStatus.COMMITTED, closed.result
        ended, end_command = _act(owner, "end_concentration", caster_state="hero")
        assert ended.status is ActionStatus.COMMITTED, ended.result
        stopped = owner.snapshot("casting")
        with pytest.raises(MechanicsRejected, match="Arcane Lock prevents"):
            _act(
                owner,
                "operate_srd_locked_object",
                actor_state="guard",
                object_id="door",
                operation="open",
                password="",
            )
        assert owner.snapshot("casting") == stopped
        assert owner.execute_action(anti_command) == anti
        assert owner.execute_action(open_command) == opened
        assert owner.execute_action(end_command) == ended
        assert owner.snapshot("casting") == stopped
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == stopped
