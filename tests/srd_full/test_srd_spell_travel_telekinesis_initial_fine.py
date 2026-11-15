"""Initial fine-control intent resolves in the actual paid Telekinesis casting action."""

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
from tests import test_srd_reaction_spells as reactions
from tests import test_srd_spell_travel_telekinesis_fall as fall
from tests import test_srd_spell_travel_telekinesis_fine as fine
from tests.srd_full.test_srd_spell_travel import SRD, _point, _TravelDice, dice
from tests.srd_full.test_srd_spell_travel_telekinesis import _tk_case
from tests.srd_full.test_srd_spell_wave3_information import _entity


@pytest.fixture
def initial_fine_scope(monkeypatch):
    monkeypatch.setattr(fall, "RUNTIME_PATH", Path(os.environ.get(
        "FC14_TK_INITIAL_FINE_RUNTIME", str(fall.RUNTIME_PATH))))
    yield from fall.actual_fall_scope.__wrapped__(monkeypatch)


def _dice(monkeypatch, rolls=()):
    actual = _TravelDice([(20, value) for value in rolls])
    monkeypatch.setattr(dice, "_rng", actual)
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    return actual


def _admit(state, *, closed=True, locks=(), size="small"):
    budget._run("record_srd_telekinesis_object_size", state, object_id="orb", size=size)
    budget._run("record_srd_object_access", state, object_id="orb", object_kind="container",
                closed=closed, locks=list(locks), stuck=False, barred=False)


def _initial(state, operation="open", **intent):
    parameters = {"caster_state": "hero", "subject": "orb", "operation": operation,
                  "destination": [0, 0, 0], "slot_level": 5, "save_dc": 15,
                  "concentration_modifier": 3}
    return budget._run("install_srd_telekinesis", state, **(parameters | intent))


@pytest.mark.parametrize("operation", ["open", "close"])
def test_initial_fine_uses_same_source_clock_without_motion_save_or_later_action(
    initial_fine_scope, monkeypatch, operation
):
    state, _ = _tk_case()
    actual = _dice(monkeypatch)
    _admit(state, closed=operation == "open")
    positions = {row["id"]: row["position"] for row in
                 state[f"{SRD}.state"]["spatial_scene"]["entities"]}
    tick = state[f"{SRD}.state"]["lasting_tick"]
    _initial(state, operation)
    source = state[f"{SRD}.state"]["telekinesis_sources"][-1]
    assert source["active"] is True and source["subject"] is None
    assert source["creature_id"] is None and source["restraint_id"] is None
    assert source["suspended"] is False and source["created_tick"] == tick
    assert source["expires_tick"] == tick + 200
    assert source["created_action_turn"] == source["last_action_turn"]
    assert source["created_action_mode"] == source["last_action_mode"]
    assert fine._object(state)["metadata"]["srd_object_access"]["closed"] is (
        operation == "close")
    assert {row["id"]: row["position"] for row in
            state[f"{SRD}.state"]["spatial_scene"]["entities"]} == positions
    assert not state[f"{SRD}.state"].get("telekinesis_releases")
    assert actual.consumed == []


@pytest.mark.parametrize("defect", ["missing", "destination", "huge", "held", "lock"])
def test_initial_invalid_fine_intent_rejects_atomically_without_a_source(
    initial_fine_scope, monkeypatch, defect
):
    state, _ = _tk_case()
    actual = _dice(monkeypatch)
    _admit(state, size="gargantuan" if defect == "huge" else "small",
           locks=["padlock"] if defect == "lock" else [])
    if defect == "held":
        fine._object(state)["metadata"]["carried_by"] = "guard"
    before = deepcopy(state)
    intent = {"subject": ""} if defect == "missing" else (
        {"destination": [1, 0, 0]} if defect == "destination" else {})
    with pytest.raises(RulesetPreconditionRejected):
        _initial(state, **intent)
    assert state == before and actual.consumed == []


def test_initial_access_preserves_real_arcane_lock_and_knock_suppression(
    initial_fine_scope, monkeypatch
):
    state, _ = _tk_case()
    next(row for row in state[f"{SRD}.state"]["spatial_scene"]["entities"]
         if row["id"] == "guard")["position"] = [25, 5, 0]
    actual = _dice(monkeypatch)
    _admit(state)
    budget._run("cast_spell_object_content_spell", state, caster_state="guard",
                spell="arcane_lock", object_id="orb", slot_level=2)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Arcane Lock prevents"):
        _initial(state)
    assert state == before
    budget._run("cast_spell_object_content_spell", state, caster_state="hero",
                spell="knock", object_id="orb", slot_level=2)
    _initial(state)
    assert fine._object(state)["metadata"]["srd_object_access"]["closed"] is False
    assert fine._object(state)["metadata"]["srd_arcane_locks"][-1]["caster"] == "guard"
    assert actual.consumed == []


def _command(parameters, revision=0, name="cast_spell_outside_combat", action_id="cast"):
    return ActionCommand(
        campaign_id="casting", action_id=action_id, idempotency_key=action_id,
        owner_id="casting-owner", writer=WriterRole.DM, expected_revision=revision,
        turn_id="casting-turn", package_id=SRD, tool_name=name, parameters=parameters)


def _facts(owner, mechanics, command, operation):
    for name, facts in (
        ("record_srd_telekinesis_object_size", {"object_id": "orb", "size": "huge"}),
        ("record_srd_object_access", {"object_id": "orb", "object_kind": "container",
         "closed": operation == "open", "locks": [], "stuck": False, "barred": False}),
    ):
        registration = replace(command, action_id=name, idempotency_key=name,
                               expected_revision=owner.snapshot("casting").revision,
                               tool_name=name, parameters=facts)
        receipt = mechanics.execute_action(registration)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result


@pytest.mark.parametrize("defect", ["lock", "gargantuan"])
def test_real_paid_cast_entry_rejects_actual_initial_access_facts_without_spending(
    initial_fine_scope, tmp_path, monkeypatch, defect
):
    def case(spell):
        state, data, parameters = fall._cast_case(spell)
        parameters.update(telekinesis_subject="orb", telekinesis_destination=[0, 0, 0],
                          telekinesis_operation="open")
        return state, data, parameters

    monkeypatch.setattr(public, "_spell_case", case)
    actual = _dice(monkeypatch)
    with AuthorityOwner.open(tmp_path / "rejected-initial.db", "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(owner, "telekinesis")
        mechanics = StatelessMcpMechanics(owner)
        command = _command(parameters)
        for name, facts in (
            ("record_srd_telekinesis_object_size", {"object_id": "orb", "size":
             "gargantuan" if defect == "gargantuan" else "huge"}),
            ("record_srd_object_access", {"object_id": "orb", "object_kind": "container",
             "closed": True, "locks": ["padlock"] if defect == "lock" else [],
             "stuck": False, "barred": False}),
        ):
            facts_command = replace(command, action_id=name, idempotency_key=name, tool_name=name,
                                    expected_revision=owner.snapshot("casting").revision,
                                    parameters=facts)
            receipt = mechanics.execute_action(facts_command)
            assert receipt.status is ActionStatus.COMMITTED, receipt.result
        initial = owner.snapshot("casting")
        command = replace(command, expected_revision=initial.revision)
        with pytest.raises(MechanicsRejected):
            mechanics.execute_action(command)
        assert owner.snapshot("casting") == initial
        assert initial.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 3
        assert actual.consumed == []


@pytest.mark.parametrize("operation", ["open", "close"])
def test_paid_initial_fine_reopens_and_retries_same_ability_without_extra_action(
    initial_fine_scope, tmp_path, monkeypatch, operation
):
    def case(spell):
        state, data, parameters = fall._cast_case(spell)
        parameters.update(telekinesis_subject="orb", telekinesis_destination=[0, 0, 0],
                          telekinesis_operation=operation)
        return state, data, parameters

    monkeypatch.setattr(public, "_spell_case", case)
    actual = _dice(monkeypatch)
    path = tmp_path / "initial-fine.db"
    with AuthorityOwner.open(path, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(owner, "telekinesis")
        mechanics = StatelessMcpMechanics(owner)
        command = _command(parameters)
        _facts(owner, mechanics, command, operation)
        initial = owner.snapshot("casting")
        command = replace(command, expected_revision=initial.revision)
        bad = replace(command, action_id="invalid", idempotency_key="invalid",
                      parameters=parameters | {"telekinesis_destination": [5, 0, 0]})
        with pytest.raises(MechanicsRejected):
            mechanics.execute_action(bad)
        assert owner.snapshot("casting") == initial
        paid = mechanics.execute_action(command)
        assert paid.status is ActionStatus.COMMITTED, paid.result
        saved = owner.snapshot("casting")
        source = saved.state[f"{SRD}.state"]["telekinesis_sources"][-1]
        assert source["subject"] is None and source["restraint_id"] is None
        assert source["expires_tick"] == source["created_tick"] + 200
        assert source["created_action_turn"] == source["last_action_turn"]
        assert fine._object(saved.state)["metadata"]["srd_object_access"]["closed"] is (
            operation == "close")
        assert _point(saved.state, "orb") == _point(initial.state, "orb")
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 2
        assert not saved.state[f"{SRD}.state"].get("telekinesis_releases")
        assert actual.consumed == []
        assert mechanics.execute_action(command) == paid
        assert owner.snapshot("casting") == saved
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        mechanics = StatelessMcpMechanics(owner)
        later = _command({"caster_state": "hero", "source_id": source["id"],
                          "object_id": "orb", "operation": "close" if operation == "open"
                          else "open"}, saved.revision, "operate_srd_telekinesis_object", "later")
        with pytest.raises(MechanicsRejected, match="later turn"):
            mechanics.execute_action(later)
        assert owner.snapshot("casting") == saved
        clock = replace(later, action_id="clock", idempotency_key="clock",
                        tool_name="advance_srd_lasting_time", parameters={"elapsed_rounds": 1})
        elapsed = mechanics.execute_action(clock)
        assert elapsed.status is ActionStatus.COMMITTED, elapsed.result
        later = replace(later, expected_revision=owner.snapshot("casting").revision)
        changed = mechanics.execute_action(later)
        assert changed.status is ActionStatus.COMMITTED, changed.result
        final = owner.snapshot("casting")
        current = final.state[f"{SRD}.state"]["telekinesis_sources"][-1]
        assert current["id"] == source["id"] and current["cast_id"] == source["cast_id"]
        assert current["created_tick"] == source["created_tick"]
        assert current["expires_tick"] == source["expires_tick"]
        assert final.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 2
        assert mechanics.execute_action(later) == changed
        assert owner.snapshot("casting") == final
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == final
        assert StatelessMcpMechanics(owner).execute_action(command) == paid
        assert owner.snapshot("casting") == final and actual.consumed == []


@pytest.mark.parametrize("roll,countered", [(1, True), (20, False)])
def test_saved_initial_fine_counterspell_controls_same_paid_cast_and_survives_reopen(
    initial_fine_scope, tmp_path, monkeypatch, roll, countered
):
    def case(spell):
        state, data, parameters = fall._cast_case(spell)
        reactions._mage(state, "counterspell")
        state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
            _entity("mage", [0, 5, 0], participant_ref="mage"))
        parameters.update(telekinesis_subject="orb", telekinesis_destination=[0, 0, 0],
                          telekinesis_operation="open")
        return state, data, parameters

    monkeypatch.setattr(public, "_spell_case", case)
    actual = _dice(monkeypatch, [roll])
    path = tmp_path / "countered-initial.db"
    with AuthorityOwner.open(path, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(owner, "telekinesis")
        mechanics = StatelessMcpMechanics(owner)
        command = _command(parameters, name="begin_srd_spell_action", action_id="begin")
        _facts(owner, mechanics, command, "open")
        command = replace(command, expected_revision=owner.snapshot("casting").revision)
        begun = mechanics.execute_action(command)
        assert begun.status is ActionStatus.COMMITTED, begun.result
        saved = owner.snapshot("casting")
        action = saved.state[f"{SRD}.state"]["pending_srd_action_stack"][-1]
        assert saved.state[f"{SRD}.state"]["pending_srd_actions"][action]["parameters"][
            "telekinesis_operation"] == "open"
        assert fine._object(saved.state)["metadata"]["srd_object_access"]["closed"] is True
        assert not saved.state[f"{SRD}.state"].get("telekinesis_sources")
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 3
        assert mechanics.execute_action(command) == begun
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        mechanics = StatelessMcpMechanics(owner)
        counter = _command(reactions._cast_parameters("counterspell", action), saved.revision,
                           "cast_spell_outside_combat", "counter")
        receipt = mechanics.execute_action(counter)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        complete = _command({"pending_action_id": action}, owner.snapshot("casting").revision,
                            "complete_srd_pending_action", "complete")
        finished = mechanics.execute_action(complete)
        assert finished.status is ActionStatus.COMMITTED, finished.result
        final = owner.snapshot("casting")
        assert not final.state[f"{SRD}.state"]["pending_srd_action_stack"]
        assert final.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == (
            3 if countered else 2)
        assert fine._object(final.state)["metadata"]["srd_object_access"]["closed"] is countered
        assert bool(final.state[f"{SRD}.state"].get("telekinesis_sources")) is (not countered)
        assert final.state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"]["counterspell"] == 1
        assert actual.consumed == [(20, roll)]
        for sent, result in ((command, begun), (counter, receipt), (complete, finished)):
            assert mechanics.execute_action(sent) == result
        assert owner.snapshot("casting") == final
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == final


def test_initial_fine_spends_one_genuine_combat_casting_action_and_slot(
    initial_fine_scope, tmp_path, monkeypatch
):
    def case(spell):
        state, data, parameters = fall._cast_case(spell)
        parameters.update(telekinesis_subject="orb", telekinesis_destination=[0, 0, 0],
                          telekinesis_operation="open")
        return state, data, parameters

    monkeypatch.setattr(public, "_spell_case", case)
    actual = _dice(monkeypatch, [20, 1, 1])
    path = tmp_path / "combat-initial.db"
    with AuthorityOwner.open(path, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(owner, "telekinesis")
        mechanics = StatelessMcpMechanics(owner)
        command = _command(parameters, name="cast_spell")
        _facts(owner, mechanics, command, "open")
        for name, intent in (
            ("start_group_combat_encounter", {"combatants": ["hero", "guard", "other"]}),
            ("begin_combat_turn", {}),
        ):
            transition = replace(
                command, action_id=name, idempotency_key=name, tool_name=name,
                parameters=intent, expected_revision=owner.snapshot("casting").revision)
            receipt = mechanics.execute_action(transition)
            assert receipt.status is ActionStatus.COMMITTED, receipt.result
        before = owner.snapshot("casting")
        assert before.state[f"{SRD}.combat"]["action_available"] is True
        command = replace(command, expected_revision=before.revision)
        paid = mechanics.execute_action(command)
        assert paid.status is ActionStatus.COMMITTED, paid.result
        final = owner.snapshot("casting")
        assert final.state[f"{SRD}.combat"]["action_available"] is False
        assert final.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 2
        source = final.state[f"{SRD}.state"]["telekinesis_sources"][-1]
        assert source["created_action_mode"] == "combat"
        assert source["created_action_turn"] == source["last_action_turn"]
        assert fine._object(final.state)["metadata"]["srd_object_access"]["closed"] is False
        assert mechanics.execute_action(command) == paid
        assert owner.snapshot("casting") == final
        assert actual.consumed == [(20, 20), (20, 1), (20, 1)]
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == final
