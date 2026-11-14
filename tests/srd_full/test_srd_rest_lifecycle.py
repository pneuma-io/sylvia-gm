"""Rest time, interruptions and recovery persist through ordinary campaign actions."""

from copy import deepcopy

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import MechanicsRejected, RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_attack_budget import _combat_case
from tests.srd_full.test_srd_npc_spell_casting import SRD, _act, _prepare_mage
from tests.srd_full.test_srd_public_spell_casting import _create_spell_campaign


def _cast(owner, parameters):
    parameters = {k: v for k, v in parameters.items() if k not in ("caster", "slot_resource")}
    return _act(owner, "cast_spell_outside_combat", **(parameters | {"caster_state": "mage"}))


def _time(owner, hours):
    receipt, _ = _act(owner, "advance_srd_lasting_time", elapsed_rounds=int(hours * 600))
    assert receipt.status is ActionStatus.COMMITTED, receipt.result


def test_hit_dice_require_earned_rest_and_cannot_be_banked_for_later_healing():
    state, _ = _combat_case()
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    state[f"{SRD}.entities"]["hero"].update(
        hp=1, max_hp=100, hit_die_size=6, constitution_modifier=0,
        resources={"hit_die": 4}, resource_maxima={"hit_die": 4},
    )

    def reject():
        before = deepcopy(state)
        with pytest.raises(RulesetPreconditionRejected, match="Short Rest"):
            _run("take_srd_short_rest_hit_die", state, target_state="hero")
        assert state == before

    reject()
    _run("begin_srd_rest", state, target_state="hero", kind="short", activity="light")
    _run("advance_srd_lasting_time", state, elapsed_rounds=599)
    reject()
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    _run("complete_srd_rest", state, target_state="hero")
    for _ in range(2):
        result = _run("take_srd_short_rest_hit_die", state, target_state="hero").result
        assert result["status"] == "committed"
    assert state[f"{SRD}.entities"]["hero"]["resources"]["hit_die"] == 2
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    reject()

    _run("begin_srd_rest", state, target_state="hero", kind="short", activity="light")
    _run("advance_srd_lasting_time", state, elapsed_rounds=600)
    _run("complete_srd_rest", state, target_state="hero")
    _run("interrupt_srd_rest", state, target_state="hero", reason="damage")
    reject()


def test_npc_daily_casting_recovers_after_real_rest_time_and_survives_reopen(tmp_path):
    database = tmp_path / "rest.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(owner, "cure_wounds")
        receipt, _ = _prepare_mage(owner, [{"spell": "cure_wounds", "uses_per_day": 1}])
        assert receipt.status is ActionStatus.COMMITTED
        receipt, _ = _cast(owner, parameters)
        assert receipt.status is ActionStatus.COMMITTED
        receipt, _ = _act(
            owner, "begin_srd_rest", target_state="mage", kind="long", activity="sleep"
        )
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        assert (
            "unconscious"
            in owner.snapshot("casting").state[f"{SRD}.entities"]["mage"]["conditions"]
        )
        _time(owner, 6)
        receipt, _ = _act(owner, "set_srd_rest_activity", target_state="mage", activity="light")
        assert receipt.status is ActionStatus.COMMITTED
        before = owner.snapshot("casting")
        with pytest.raises(MechanicsRejected, match="eight hours"):
            _act(owner, "complete_srd_rest", target_state="mage")
        assert owner.snapshot("casting").state == before.state
        _time(owner, 2)
        receipt, command = _act(owner, "complete_srd_rest", target_state="mage")
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting")
        mage = saved.state[f"{SRD}.entities"]["mage"]
        assert mage["spell_uses_remaining"]["cure_wounds"] == 1
        assert mage["resources"] == {}
        assert mage["rest_phase"] == "complete"
        assert "unconscious" not in mage["conditions"]
        assert receipt.result["result_card"]["title"] == "Rest completed"
        assert owner.execute_action(command) == receipt
        with pytest.raises(MechanicsRejected, match="sixteen hours"):
            _act(owner, "begin_srd_rest", target_state="mage", kind="long", activity="sleep")
        assert owner.snapshot("casting").state == saved.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        receipt, _ = _cast(owner, parameters)
        assert receipt.status is ActionStatus.COMMITTED
        _time(owner, 16)
        receipt, _ = _act(
            owner, "begin_srd_rest", target_state="mage", kind="long", activity="light"
        )
        assert receipt.status is ActionStatus.COMMITTED


def test_casting_interrupts_rest_and_requires_an_extra_hour_after_resuming(tmp_path):
    with AuthorityOwner.open(tmp_path / "interruption.db", "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(owner, "cure_wounds")
        _prepare_mage(owner, [{"spell": "cure_wounds", "uses_per_day": 1}])
        receipt, _ = _act(
            owner, "begin_srd_rest", target_state="mage", kind="long", activity="light"
        )
        assert receipt.status is ActionStatus.COMMITTED
        _time(owner, 1)
        receipt, _ = _cast(owner, parameters)
        assert receipt.status is ActionStatus.COMMITTED
        mage = owner.snapshot("casting").state[f"{SRD}.entities"]["mage"]
        assert mage["rest_phase"] == "interrupted"
        assert mage["rest_interruptions"] == 1
        # Time spent doing other things cannot silently count toward recovery.
        _time(owner, 3)
        receipt, _ = _act(owner, "set_srd_rest_activity", target_state="mage", activity="sleep")
        assert receipt.status is ActionStatus.COMMITTED
        _time(owner, 7)
        before = owner.snapshot("casting")
        with pytest.raises(MechanicsRejected, match="additional hour"):
            _act(owner, "complete_srd_rest", target_state="mage")
        assert owner.snapshot("casting").state == before.state
        _time(owner, 1)
        receipt, _ = _act(owner, "complete_srd_rest", target_state="mage")
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        assert (
            owner.snapshot("casting").state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"][
                "cure_wounds"
            ]
            == 1
        )


def test_damage_wakes_rest_sleep_ends_concentration_and_does_not_count_twice(tmp_path):
    with AuthorityOwner.open(tmp_path / "damage.db", "casting-owner") as owner:
        _, _, missiles = _create_spell_campaign(owner, "magic_missile")
        _prepare_mage(owner, [{"spell": "guidance", "uses_per_day": 0}])
        receipt, _ = _cast(owner, missiles | {"spell": "guidance", "selected_skill": "athletics"})
        assert receipt.status is ActionStatus.COMMITTED
        assert (
            owner.snapshot("casting").state[f"{SRD}.entities"]["mage"]["concentration_spell_id"]
            == "guidance"
        )
        receipt, _ = _act(
            owner, "begin_srd_rest", target_state="mage", kind="long", activity="sleep"
        )
        assert receipt.status is ActionStatus.COMMITTED
        assert (
            owner.snapshot("casting").state[f"{SRD}.entities"]["mage"]["concentration_spell_id"]
            is None
        )
        _time(owner, 1)
        receipt, command = _act(
            owner, "cast_spell_outside_combat", **(missiles | {"target": "mage"})
        )
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        mage = saved.state[f"{SRD}.entities"]["mage"]
        assert 0 < mage["hp"] < 25
        assert mage["rest_phase"] == "interrupted"
        assert mage["rest_interruptions"] == 1
        assert mage["short_rest_hit_dice_available"] is True
        assert "unconscious" not in mage["conditions"]
        assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting").state == saved.state
        receipt, _ = _act(
            owner, "start_group_combat_encounter", combatants=["hero", "mage", "other"]
        )
        assert receipt.status is ActionStatus.COMMITTED
        assert owner.snapshot("casting").state[f"{SRD}.entities"]["mage"]["rest_interruptions"] == 1


def test_cantrip_preserves_short_rest_but_initiative_and_exertion_stop_it(tmp_path):
    with AuthorityOwner.open(tmp_path / "short.db", "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(owner, "fire_bolt")
        _prepare_mage(owner, [{"spell": "fire_bolt", "uses_per_day": 0}])
        receipt, _ = _act(
            owner, "begin_srd_rest", target_state="mage", kind="short", activity="light"
        )
        assert receipt.status is ActionStatus.COMMITTED
        _time(owner, 0.5)
        receipt, _ = _cast(owner, parameters | {"target": "other"})
        assert receipt.status is ActionStatus.COMMITTED
        assert owner.snapshot("casting").state[f"{SRD}.entities"]["mage"]["rest_phase"] == "active"
        receipt, _ = _act(
            owner, "start_group_combat_encounter", combatants=["hero", "mage", "other"]
        )
        assert receipt.status is ActionStatus.COMMITTED
        assert owner.snapshot("casting").state[f"{SRD}.entities"]["mage"]["rest_phase"] == "failed"
        receipt, _ = _act(owner, "end_combat_encounter", end_reason="fighting_stopped")
        assert receipt.status is ActionStatus.COMMITTED
        with pytest.raises(MechanicsRejected):
            _act(owner, "complete_srd_rest", target_state="mage")
        _act(owner, "begin_srd_rest", target_state="mage", kind="long", activity="light")
        _act(owner, "set_srd_rest_activity", target_state="mage", activity="exertion")
        _time(owner, 0.5)
        assert owner.snapshot("casting").state[f"{SRD}.entities"]["mage"]["rest_phase"] == "active"
        _time(owner, 0.5)
        mage = owner.snapshot("casting").state[f"{SRD}.entities"]["mage"]
        assert mage["rest_phase"] == "interrupted" and mage["rest_interruptions"] == 1
        assert mage["rest_sleep_ticks"] == mage["rest_light_ticks"] == 0


@pytest.mark.parametrize("kind", ["short", "long"])
def test_rest_recovers_known_resources_and_pools_without_replenishing_ammunition(kind):
    state, _ = _combat_case()
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    maxima = {
        "action_surge": 2,
        "second_wind": 4,
        "channel_divinity": 3,
        "focus_point": 5,
        "spell_slot_1": 3,
        "hit_die": 2,
        "darts": 20,
    }
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(
        name="Resting hero",
        hp=19,
        max_hp=20,
        exhaustion_level=2,
        temp_hp=5,
        resource_maxima=maxima,
        resources=dict.fromkeys(maxima, 0),
        hit_dice_pools=[
            {"class_id": "fighter", "size": 10, "maximum": 1, "remaining": 0},
            {"class_id": "wizard", "size": 6, "maximum": 1, "remaining": 0},
        ],
    )
    _run(
        "begin_srd_rest",
        state,
        target_state="hero",
        kind=kind,
        activity="sleep" if kind == "long" else "light",
    )
    _run("advance_srd_lasting_time", state, elapsed_rounds=4800 if kind == "long" else 600)
    result = _run("complete_srd_rest", state, target_state="hero").result
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["resources"]["darts"] == 0
    if kind == "long":
        assert hero["resources"] == maxima | {"darts": 0}
        assert (hero["hp"], hero["exhaustion_level"], hero["temp_hp"]) == (20, 1, 0)
        assert all(pool["remaining"] == pool["maximum"] for pool in hero["hit_dice_pools"])
    else:
        assert hero["resources"] == {
            "action_surge": 2,
            "second_wind": 1,
            "channel_divinity": 1,
            "focus_point": 5,
            "spell_slot_1": 0,
            "hit_die": 0,
            "darts": 0,
        }
        assert (hero["hp"], hero["exhaustion_level"], hero["temp_hp"]) == (19, 2, 5)
    assert result["result_card"]["details"]
