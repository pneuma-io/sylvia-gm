"""The real saved fall window fences later Telekinesis actions without repeated costs or rolls."""

import os
from dataclasses import replace
from pathlib import Path

import pytest

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from sylvia_gm.domain.errors import MechanicsRejected
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_travel_telekinesis_fall as fall
from tests.srd_full.test_srd_spell_travel import SRD, _point, _TravelDice, dice


@pytest.fixture
def pending_scope(monkeypatch):
    monkeypatch.setattr(
        fall,
        "RUNTIME_PATH",
        Path(os.environ.get("FC14_TK_PENDING_RUNTIME", str(fall.RUNTIME_PATH))),
    )
    yield from fall.actual_fall_scope.__wrapped__(monkeypatch)


def test_real_paid_release_fences_later_tools_reopens_then_allows_after_landing(
    pending_scope, tmp_path, monkeypatch
):
    monkeypatch.setattr(public, "_spell_case", fall._cast_case)
    actual = _TravelDice([(20, 1), (6, 3), (6, 4), (6, 5), (20, 20)])
    monkeypatch.setattr(dice, "_rng", actual)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / "telekinesis-pending.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(
            owner, "telekinesis", target_overrides={"hp": 1000, "max_hp": 1000}
        )
        command = ActionCommand(
            campaign_id="casting",
            action_id="lift",
            idempotency_key="lift",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=params,
        )
        mechanics = StatelessMcpMechanics(owner)
        paid = mechanics.execute_action(command)
        assert paid.status is ActionStatus.COMMITTED, paid.result
        saved = owner.snapshot("casting")
        source_id = saved.state[f"{SRD}.state"]["telekinesis_sources"][-1]["id"]
        clock = replace(
            command,
            action_id="release-clock",
            idempotency_key="release-clock",
            expected_revision=saved.revision,
            tool_name="advance_srd_lasting_time",
            parameters={"elapsed_rounds": 1},
        )
        released = mechanics.execute_action(clock)
        assert released.status is ActionStatus.COMMITTED, released.result
        waiting = owner.snapshot("casting")
        assert _point(waiting.state, "guard")[2] == 30
        assert waiting.state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] == "telekinesis"
        trigger = waiting.state[f"{SRD}.state"]["pending_srd_action_stack"][-1]
        assert mechanics.execute_action(clock) == released
        assert owner.snapshot("casting") == waiting
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == waiting
        mechanics = StatelessMcpMechanics(owner)
        for name, parameters in (
            (
                "use_srd_telekinesis",
                {
                    "caster_state": "hero",
                    "source_id": source_id,
                    "subject": "guard",
                    "destination": [5, 10, 0],
                },
            ),
            ("record_srd_telekinesis_object_size", {"object_id": "orb", "size": "small"}),
        ):
            blocked = replace(
                command,
                action_id=name,
                idempotency_key=name,
                expected_revision=waiting.revision,
                tool_name=name,
                parameters=parameters,
            )
            with pytest.raises(MechanicsRejected, match="Complete the pending reaction window"):
                mechanics.execute_action(blocked)
            assert owner.snapshot("casting") == waiting
            assert actual.consumed == [(20, 1)]
        completion = replace(
            command,
            action_id="fall",
            idempotency_key="fall",
            expected_revision=waiting.revision,
            tool_name="complete_srd_pending_action",
            parameters={"pending_action_id": trigger},
        )
        landed = mechanics.execute_action(completion)
        assert landed.status is ActionStatus.COMMITTED, landed.result
        saved = owner.snapshot("casting")
        assert _point(saved.state, "guard") == [5, 0, 0]
        assert saved.state[f"{SRD}.entities"]["guard"]["hp"] == 988
        assert saved.state[f"{SRD}.state"]["pending_srd_action_stack"] == []
        assert mechanics.execute_action(completion) == landed
        assert owner.snapshot("casting") == saved
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        mechanics = StatelessMcpMechanics(owner)
        for name, parameters in (
            ("record_srd_telekinesis_object_size", {"object_id": "orb", "size": "small"}),
            (
                "use_srd_telekinesis",
                {
                    "caster_state": "hero",
                    "source_id": source_id,
                    "subject": "guard",
                    "destination": [5, 10, 0],
                },
            ),
        ):
            current = owner.snapshot("casting")
            later = replace(
                command,
                action_id="after-" + name,
                idempotency_key="after-" + name,
                expected_revision=current.revision,
                tool_name=name,
                parameters=parameters,
            )
            receipt = mechanics.execute_action(later)
            assert receipt.status is ActionStatus.COMMITTED, receipt.result
            current = owner.snapshot("casting")
            assert mechanics.execute_action(later) == receipt
            assert owner.snapshot("casting") == current
        assert current.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 2
        assert _point(current.state, "guard") == [5, 0, 0]
        assert actual.consumed == [(20, 1), (6, 3), (6, 4), (6, 5), (20, 20)]
