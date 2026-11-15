"""Actual Telekinesis fine control preserves ordinary reach, locks and canonical object access."""

import os
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from sylvia_gm.domain.errors import MechanicsRejected, RulesetPreconditionRejected
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_travel_telekinesis_fall as fall
from tests.srd_full.test_srd_spell_travel import SRD, _point, _TravelDice, dice
from tests.srd_full.test_srd_spell_travel_telekinesis import _install, _tk_case


@pytest.fixture
def fine_scope(monkeypatch):
    monkeypatch.setattr(
        fall,
        "RUNTIME_PATH",
        Path(os.environ.get("FC14_TK_FINE_RUNTIME", str(fall.RUNTIME_PATH))),
    )
    yield from fall.actual_fall_scope.__wrapped__(monkeypatch)


def _object(state):
    return next(
        row for row in state[f"{SRD}.state"]["spatial_scene"]["entities"] if row["id"] == "orb"
    )


def _ready(monkeypatch, *, locks=(), stuck=False, barred=False, size="small", rolls=(1,)):
    state, _ = _tk_case()
    actual = _TravelDice([(20, value) for value in rolls])
    monkeypatch.setattr(dice, "_rng", actual)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    budget._run("record_srd_telekinesis_object_size", state, object_id="orb", size=size)
    budget._run(
        "record_srd_object_access",
        state,
        object_id="orb",
        object_kind="door",
        closed=True,
        locks=list(locks),
        stuck=stuck,
        barred=barred,
    )
    _install(state, destination=[25, 5, 0])
    budget._run("advance_srd_lasting_time", state, elapsed_rounds=1)
    source = state[f"{SRD}.state"]["telekinesis_sources"][-1]
    return state, source, actual


def _operate(state, source, operation="open"):
    return budget._run(
        "operate_srd_telekinesis_object",
        state,
        caster_state="hero",
        source_id=source["id"],
        object_id="orb",
        operation=operation,
    )


def test_fine_control_uses_actual_sixty_foot_source_and_preserves_ordinary_reach(
    fine_scope, monkeypatch
):
    state, source, actual = _ready(monkeypatch)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        budget._run(
            "operate_srd_locked_object",
            state,
            actor_state="hero",
            object_id="orb",
            operation="open",
        )
    assert state == before
    origin = _point(state, "orb")
    _operate(state, source)
    assert _object(state)["metadata"]["srd_object_access"]["closed"] is False
    assert _object(state)["movement"] == "passable" and _object(state)["visibility"] == "clear"
    assert _point(state, "orb") == origin
    _operate(state, source, "close")
    assert _object(state)["metadata"]["srd_object_access"]["closed"] is True
    assert _object(state)["movement"] == "blocking" and _object(state)["visibility"] == "opaque"
    assert _point(state, "orb") == origin
    budget._run(
        "operate_srd_locked_object",
        state,
        actor_state="guard",
        object_id="orb",
        operation="open",
    )
    assert _object(state)["metadata"]["srd_object_access"]["closed"] is False
    assert actual.consumed == [(20, 1)]


@pytest.mark.parametrize("restriction", ["locks", "stuck", "barred"])
def test_fine_control_retains_actual_mundane_access_restrictions(
    fine_scope, monkeypatch, restriction
):
    arguments = {restriction: ["padlock"] if restriction == "locks" else True}
    state, source, actual = _ready(monkeypatch, **arguments)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="mundane access restriction"):
        _operate(state, source)
    assert state == before
    assert actual.consumed == [(20, 1)]


def test_fine_control_retains_actual_arcane_lock_and_real_knock_suppression(
    fine_scope, monkeypatch
):
    state, source, actual = _ready(monkeypatch, rolls=(1, 1))
    budget._run(
        "cast_spell_object_content_spell",
        state,
        caster_state="guard",
        object_id="orb",
        spell="arcane_lock",
        slot_level=2,
    )
    lock = _object(state)["metadata"]["srd_arcane_locks"][-1]
    assert lock["caster"] == "guard" and lock["source_id"]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Arcane Lock prevents"):
        _operate(state, source)
    assert state == before
    budget._run(
        "cast_spell_object_content_spell",
        state,
        caster_state="hero",
        object_id="orb",
        spell="knock",
        slot_level=2,
    )
    _operate(state, source)
    assert _object(state)["metadata"]["srd_object_access"]["closed"] is False
    budget._run("advance_srd_lasting_time", state, elapsed_rounds=100)
    _install(state, destination=[25, 5, 0])
    budget._run("advance_srd_lasting_time", state, elapsed_rounds=1)
    source = state[f"{SRD}.state"]["telekinesis_sources"][-1]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Arcane Lock prevents"):
        _operate(state, source, "close")
    assert state == before
    assert actual.consumed == [(20, 1), (20, 1)]


def test_fine_control_rejects_actual_gargantuan_object_without_action_or_mutation(
    fine_scope, monkeypatch
):
    state, source, actual = _ready(monkeypatch, size="gargantuan")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Huge-or-smaller"):
        _operate(state, source)
    assert state == before
    assert actual.consumed == [(20, 1)]


def test_paid_public_fine_control_admits_facts_reopens_and_retries_without_another_slot(
    fine_scope, tmp_path, monkeypatch
):
    def actual_case(spell):
        state, data, parameters = fall._cast_case(spell)
        parameters["telekinesis_destination"] = [25, 5, 0]
        return state, data, parameters

    monkeypatch.setattr(public, "_spell_case", actual_case)
    actual = _TravelDice([(20, 1)])
    monkeypatch.setattr(dice, "_rng", actual)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / "telekinesis-fine.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(owner, "telekinesis")
        command = ActionCommand(
            campaign_id="casting",
            action_id="cast",
            idempotency_key="cast",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        mechanics = StatelessMcpMechanics(owner)
        for name, facts in (
            ("record_srd_telekinesis_object_size", {"object_id": "orb", "size": "small"}),
            (
                "record_srd_object_access",
                {
                    "object_id": "orb",
                    "object_kind": "door",
                    "closed": True,
                    "locks": [],
                    "stuck": False,
                    "barred": False,
                },
            ),
        ):
            registration = replace(
                command,
                action_id=name,
                idempotency_key=name,
                expected_revision=owner.snapshot("casting").revision,
                tool_name=name,
                parameters=facts,
            )
            admitted = mechanics.execute_action(registration)
            assert admitted.status is ActionStatus.COMMITTED, admitted.result
        command = replace(command, expected_revision=owner.snapshot("casting").revision)
        paid = mechanics.execute_action(command)
        assert paid.status is ActionStatus.COMMITTED, paid.result
        saved = owner.snapshot("casting")
        source_id = saved.state[f"{SRD}.state"]["telekinesis_sources"][-1]["id"]
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 2
        clock = replace(
            command,
            action_id="later",
            idempotency_key="later",
            expected_revision=saved.revision,
            tool_name="advance_srd_lasting_time",
            parameters={"elapsed_rounds": 1},
        )
        elapsed = mechanics.execute_action(clock)
        assert elapsed.status is ActionStatus.COMMITTED, elapsed.result
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        mechanics = StatelessMcpMechanics(owner)
        ordinary = replace(
            command,
            action_id="ordinary",
            idempotency_key="ordinary",
            expected_revision=saved.revision,
            tool_name="operate_srd_locked_object",
            parameters={"actor_state": "hero", "object_id": "orb", "operation": "open"},
        )
        with pytest.raises(MechanicsRejected):
            mechanics.execute_action(ordinary)
        assert owner.snapshot("casting") == saved
        remote = replace(
            ordinary,
            action_id="remote-open",
            idempotency_key="remote-open",
            tool_name="operate_srd_telekinesis_object",
            parameters={
                "caster_state": "hero",
                "source_id": source_id,
                "object_id": "orb",
                "operation": "open",
            },
        )
        opened = mechanics.execute_action(remote)
        assert opened.status is ActionStatus.COMMITTED, opened.result
        saved = owner.snapshot("casting")
        assert _object(saved.state)["metadata"]["srd_object_access"]["closed"] is False
        assert _object(saved.state)["visibility"] == "clear"
        assert mechanics.execute_action(remote) == opened
        assert owner.snapshot("casting") == saved
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        mechanics = StatelessMcpMechanics(owner)
        close = replace(
            remote,
            action_id="remote-close",
            idempotency_key="remote-close",
            expected_revision=saved.revision,
            parameters={**remote.parameters, "operation": "close"},
        )
        closed = mechanics.execute_action(close)
        assert closed.status is ActionStatus.COMMITTED, closed.result
        saved = owner.snapshot("casting")
        assert _object(saved.state)["metadata"]["srd_object_access"]["closed"] is True
        assert _object(saved.state)["movement"] == "blocking"
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 2
        assert mechanics.execute_action(close) == closed
        assert owner.snapshot("casting") == saved
        assert actual.consumed == [(20, 1)]
