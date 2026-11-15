"""An optional first exertion preserves actual paid Telekinesis and later actions."""

import os
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.application.package_runtime import PackageRuntimeError
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from sylvia_gm.domain.errors import MechanicsRejected, RulesetPreconditionRejected
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_travel_telekinesis_fall as fall
from tests.srd_full.test_srd_spell_travel import SRD, _point, _TravelDice, dice
from tests.srd_full.test_srd_spell_travel_telekinesis import _install, _tk_case


@pytest.fixture
def start_scope(monkeypatch):
    monkeypatch.setattr(
        fall, "RUNTIME_PATH",
        Path(os.environ.get("FC14_TK_START_RUNTIME", str(fall.RUNTIME_PATH))),
    )
    yield from fall.actual_fall_scope.__wrapped__(monkeypatch)


def _dice(monkeypatch, rolls=()):
    actual = _TravelDice([(20, value) for value in rolls])
    monkeypatch.setattr(dice, "_rng", actual)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    return actual


def _start(state):
    return budget._run(
        "install_srd_telekinesis", state, caster_state="hero", subject="",
        destination=[0, 0, 0], slot_level=5, save_dc=15, concentration_modifier=3,
    )


@pytest.mark.parametrize("roll, moved", [(1, True), (20, False)])
def test_selected_initial_exertion_still_uses_the_same_actual_save_and_movement(
    start_scope, monkeypatch, roll, moved
):
    state, _ = _tk_case()
    actual = _dice(monkeypatch, [roll])
    origin = _point(state, "guard")
    _install(state, destination=[5, 10, 0])
    source = state[f"{SRD}.state"]["telekinesis_sources"][-1]
    assert actual.consumed == [(20, roll)]
    assert _point(state, "guard") == ([5, 10, 0] if moved else origin)
    assert (source["subject"] == "guard") is moved
    assert ("restrained" in state[f"{SRD}.entities"]["guard"]["conditions"]) is moved
    assert source["active"] is True


def test_optional_first_exertion_creates_only_the_actual_concentration_ability(
    start_scope, monkeypatch
):
    state, _ = _tk_case()
    actual = _dice(monkeypatch)
    scene = deepcopy(state[f"{SRD}.state"]["spatial_scene"])
    _start(state)
    source = state[f"{SRD}.state"]["telekinesis_sources"][-1]
    assert source["active"] is True and source["subject"] is None
    assert source["creature_id"] is None and source["restraint_id"] is None
    assert source["suspended"] is False
    assert source["expires_tick"] - source["created_tick"] == 200
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] == "telekinesis"
    assert state[f"{SRD}.state"]["spatial_scene"] == scene
    assert not state[f"{SRD}.state"].get("telekinesis_releases")
    assert actual.consumed == []
    budget._run("advance_srd_lasting_time", state, elapsed_rounds=100)
    assert state[f"{SRD}.state"]["telekinesis_sources"][-1]["active"] is False
    assert state[f"{SRD}.state"]["spatial_scene"] == scene
    assert not state[f"{SRD}.state"].get("pending_srd_action_stack")


@pytest.mark.parametrize("intent", ["targets", "destination"])
def test_no_first_subject_rejects_conflicting_intent_before_any_source_or_save(
    start_scope, monkeypatch, intent
):
    state, _ = _tk_case()
    actual = _dice(monkeypatch)
    before = deepcopy(state)
    parameters = {
        "caster_state": "hero", "subject": "", "destination": [0, 0, 0],
    }
    parameters.update({"targets": ["guard"]} if intent == "targets" else {
        "destination": [5, 0, 20],
    })
    with pytest.raises(RulesetPreconditionRejected):
        budget._run("validate_srd_telekinesis_start", state, **parameters)
    assert state == before and actual.consumed == []


def test_later_magic_action_still_requires_an_actual_subject_before_spending(
    start_scope, monkeypatch
):
    state, _ = _tk_case()
    actual = _dice(monkeypatch)
    _start(state)
    budget._run("advance_srd_lasting_time", state, elapsed_rounds=1)
    source = state[f"{SRD}.state"]["telekinesis_sources"][-1]
    before = deepcopy(state)
    with pytest.raises(PackageRuntimeError, match="execution failed"):
        budget._run(
            "use_srd_telekinesis", state, caster_state="hero", source_id=source["id"],
            subject="", destination=[0, 0, 0],
        )
    assert state == before and actual.consumed == []


def test_paid_optional_cast_reopens_then_uses_first_actual_target_and_retries(
    start_scope, tmp_path, monkeypatch
):
    def actual_case(spell):
        state, data, parameters = fall._cast_case(spell)
        parameters.pop("telekinesis_subject")
        parameters.pop("telekinesis_destination")
        return state, data, parameters

    monkeypatch.setattr(public, "_spell_case", actual_case)
    actual = _dice(monkeypatch, [1])
    database = tmp_path / "telekinesis-start.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(owner, "telekinesis")
        command = ActionCommand(
            campaign_id="casting", action_id="cast", idempotency_key="cast",
            owner_id="casting-owner", writer=WriterRole.DM, expected_revision=0,
            turn_id="casting-turn", package_id=SRD,
            tool_name="cast_spell_outside_combat", parameters=parameters,
        )
        mechanics = StatelessMcpMechanics(owner)
        scene = deepcopy(owner.snapshot("casting").state[f"{SRD}.state"]["spatial_scene"])
        conflicting = replace(
            command, action_id="conflicting", idempotency_key="conflicting",
            parameters={**parameters, "telekinesis_destination": [5, 0, 20]},
        )
        initial = owner.snapshot("casting")
        with pytest.raises(MechanicsRejected):
            mechanics.execute_action(conflicting)
        assert owner.snapshot("casting") == initial
        paid = mechanics.execute_action(command)
        assert paid.status is ActionStatus.COMMITTED, paid.result
        saved = owner.snapshot("casting")
        source = saved.state[f"{SRD}.state"]["telekinesis_sources"][-1]
        assert source["active"] is True and source["subject"] is None
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 2
        assert saved.state[f"{SRD}.state"]["spatial_scene"] == scene
        assert actual.consumed == []
        assert mechanics.execute_action(command) == paid
        assert owner.snapshot("casting") == saved
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        mechanics = StatelessMcpMechanics(owner)
        first = replace(
            command, action_id="first", idempotency_key="first",
            expected_revision=saved.revision, tool_name="use_srd_telekinesis",
            parameters={
                "caster_state": "hero", "source_id": source["id"], "subject": "guard",
                "destination": [5, 10, 0],
            },
        )
        with pytest.raises(MechanicsRejected):
            mechanics.execute_action(first)
        assert owner.snapshot("casting") == saved and actual.consumed == []
        clock = replace(
            first, action_id="later", idempotency_key="later",
            tool_name="advance_srd_lasting_time", parameters={"elapsed_rounds": 1},
        )
        elapsed = mechanics.execute_action(clock)
        assert elapsed.status is ActionStatus.COMMITTED, elapsed.result
        first = replace(first, expected_revision=owner.snapshot("casting").revision)
        exertion = mechanics.execute_action(first)
        assert exertion.status is ActionStatus.COMMITTED, exertion.result
        saved = owner.snapshot("casting")
        assert _point(saved.state, "guard") == [5, 10, 0]
        current = saved.state[f"{SRD}.state"]["telekinesis_sources"][-1]
        assert current["id"] == source["id"] and current["sequence"] == source["sequence"]
        assert "restrained" in saved.state[f"{SRD}.entities"]["guard"]["conditions"]
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 2
        assert mechanics.execute_action(first) == exertion
        assert owner.snapshot("casting") == saved and actual.consumed == [(20, 1)]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        assert StatelessMcpMechanics(owner).execute_action(first) == exertion
        assert owner.snapshot("casting") == saved and actual.consumed == [(20, 1)]
