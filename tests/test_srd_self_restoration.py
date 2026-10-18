"""Self-Restoration owns one end-turn removal and its specific Exhaustion immunity."""

from copy import deepcopy
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.test_srd_action_budget import SRD, _run
from tests.test_srd_unarmed_creatures import _case


def _monk(level=10):
    state, data = _case()
    data.update(classes=[{"id": "monk", "level": level}], level=level)
    state[f"{SRD}.entities"]["hero"]["conditions"] = ["charmed", "frightened", "poisoned"]
    return state, data


@pytest.mark.parametrize("condition", ["charmed", "frightened", "poisoned"])
def test_end_turn_removes_only_the_selected_condition_and_records_it(condition):
    state, data = _monk()
    result = _run("end_combat_turn", state, participant_data=data, self_restoration=condition)
    hero = state[f"{SRD}.entities"]["hero"]
    remaining = [c for c in ["charmed", "frightened", "poisoned"] if c != condition]
    assert set(hero["conditions"]) == set(remaining)
    assert len(hero["conditions"]) == len(remaining)
    assert "Self-Restoration" in str(result.result["result_card"])
    assert state[f"{SRD}.combat"]["turn_active"] is False
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("end_combat_turn", state, participant_data=data, self_restoration="poisoned")
    assert state == before


@pytest.mark.parametrize("level,cause,expected", [
    (10, "lack_of_food_or_drink", 0), (9, "lack_of_food_or_drink", 1), (10, "other", 1),
])
def test_only_earned_self_restoration_prevents_food_or_drink_exhaustion(level, cause, expected):
    state, data = _monk(level)
    _run("gain_srd_exhaustion", state, participant_data=data,
         target="hero", levels=1, cause=cause)
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["exhaustion_level"] == expected
    assert ("exhaustion" in hero["conditions"]) is (expected > 0)


@pytest.mark.parametrize("problem", ["unearned", "absent", "npc"])
def test_invalid_self_restoration_choice_does_not_close_or_change_the_turn(problem):
    state, data = _monk(9 if problem == "unearned" else 10)
    if problem == "absent":
        state[f"{SRD}.entities"]["hero"]["conditions"] = []
    elif problem == "npc":
        state[f"{SRD}.combat"].update(active_actor="guard", current_actor="guard")
        state[f"{SRD}.entities"]["guard"]["conditions"] = ["poisoned"]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("end_combat_turn", state, participant_data=data, self_restoration="poisoned")
    assert state == before


def test_end_turn_without_selection_keeps_conditions_and_npc_food_exhaustion_still_applies():
    state, data = _monk()
    _run("end_combat_turn", state, participant_data=data)
    assert state[f"{SRD}.entities"]["hero"]["conditions"] == ["charmed", "frightened", "poisoned"]
    _run("gain_srd_exhaustion", state, participant_data=data,
         target="guard", levels=1, cause="lack_of_food_or_drink")
    assert state[f"{SRD}.entities"]["guard"]["exhaustion_level"] == 1


@pytest.mark.parametrize("seed,recovered,died", [
    (1, False, False), (5, True, False), (31, False, True),
])
def test_automatic_death_save_requests_a_choice_only_when_a_living_turn_ends(
    monkeypatch, seed, recovered, died,
):
    monkeypatch.setattr(dice, "_rng", Random(seed))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data = _monk()
    state[f"{SRD}.entities"]["hero"].update(
        hp=0, stable=False, conditions=["unconscious", "poisoned"],
        death_successes=0, death_failures=1, stable_recovery_hours_remaining=0,
    )
    before = deepcopy(state)
    result = _run("death_saving_throw", state, participant_data=data)
    if not recovered and not died:
        assert result.result["status"] == "awaiting_choice"
        assert [option.option_id for option in result.choice.options] == ["none", "poisoned"]
        assert state == before
        return
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["is_alive"] is not died
    assert state[f"{SRD}.combat"]["turn_active"] is recovered
    assert "poisoned" in hero["conditions"]
    if recovered:
        _run("end_combat_turn", state, participant_data=data, self_restoration="poisoned")
        assert "poisoned" not in state[f"{SRD}.entities"]["hero"]["conditions"]


@pytest.mark.parametrize("selection", ["none", "charmed", "frightened", "poisoned"])
def test_death_save_choice_resumes_same_roll_and_removes_only_selection(monkeypatch, selection):
    from dataclasses import replace

    from sylvia_gm.domain.authority import MechanicContext, MechanicsChoiceResolution, owned_payload
    from tests.test_srd_action_budget import _mechanic

    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data = _monk()
    state[f"{SRD}.entities"]["hero"].update(
        hp=0, stable=False, death_successes=0, death_failures=0,
        stable_recovery_hours_remaining=0,
    )
    context = MechanicContext(
        campaign_id="budget", turn_id="turn", action_id="death",
        state=owned_payload(state), parameters=owned_payload({}),
        participants=owned_payload({"hero": {"participant_id": "hero",
            "kind": "player_character", "validation_status": "validated",
            "package_id": SRD, "data": {"name": "Monk", **data}}}),
    )
    monkeypatch.setattr(dice, "_rng", Random(1))
    waiting = _mechanic("death_saving_throw")(context)
    assert waiting.mutations == ()
    assert [o.option_id for o in waiting.choice.options] == [
        "none", "charmed", "frightened", "poisoned",
    ]
    monkeypatch.setattr(dice, "_rng", Random(1))
    result = _mechanic("death_saving_throw")(replace(context, choice_resolution=
        MechanicsChoiceResolution(waiting.choice.choice_id, selection, waiting.choice)))
    saved = {f"{m.namespace}.{m.key}": m.value for m in result.mutations}
    hero = saved[f"{SRD}.entities"]["hero"]
    assert hero["death_failures"] == 1
    assert hero["hp"] == 0
    remaining = [c for c in ["charmed", "frightened", "poisoned"] if c != selection]
    assert set(hero["conditions"]) == set(remaining)
    assert len(hero["conditions"]) == len(remaining)
    assert saved[f"{SRD}.combat"]["turn_active"] is False
    assert ("Self-Restoration" in str(result.result["result_card"])) is (selection != "none")


def test_ending_encounter_preserves_selected_end_turn_restoration_and_its_result():
    state, data = _monk()
    result = _run("end_combat_encounter", state, participant_data=data,
                  end_reason="fighting_stopped", self_restoration="frightened")
    assert "frightened" not in state[f"{SRD}.entities"]["hero"]["conditions"]
    assert "Self-Restoration" in str(result.result["result_card"])



@pytest.mark.parametrize("death_save,reroll", [(False, False), (True, False), (True, True)])
def test_public_self_restoration_reopens_and_replays_without_another_removal(
    tmp_path, monkeypatch, death_save, reroll,
):
    import io
    from dataclasses import replace

    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import (
        ActionCommand,
        AuthorityOwner,
        CampaignCreate,
        ResolveMechanicsChoiceCommand,
    )
    from sylvia_gm.application.bundled_packages import bundled_srd_archive
    from sylvia_gm.domain.authority import ActionStatus, StateValue, WriterRole
    from tests.test_srd_second_wind import _installed_fighter

    database = tmp_path / "self-restoration.db"
    state, data = _monk(14 if reroll else 10)
    if reroll:
        state[f"{SRD}.entities"]["hero"].update(
            resources={"focus_point": 14}, resource_maxima={"focus_point": 14},
        )
    state[f"{SRD}.combat"].update(active_actor_kind="character", active_actor_can_act=True,
                                 active_actor_at_zero_hp=False)
    if death_save:
        monkeypatch.setattr(dice, "set_seed",
                            lambda seed: setattr(dice, "_rng", Random(2 if reroll else 1)))
        state[f"{SRD}.combat"].update(active_actor_can_act=False,
                                     active_actor_at_zero_hp=True, active_actor_stable=False)
        state[f"{SRD}.entities"]["hero"].update(
            hp=0, stable=False, death_successes=0, death_failures=0,
            stable_recovery_hours_remaining=0,
        )
    with AuthorityOwner.open(database, "test") as owner:
        package = owner.install_package_archive(io.BytesIO(bundled_srd_archive()), install_id="srd")
        actor = _installed_fighter(package.package_lock, fighter=True)
        actor = replace(actor, data={**actor.data, **data})
        owner.create_campaign(CampaignCreate(
            campaign_id="self-restoration", display_name="Synthetic Monk", writer=WriterRole.DM,
            package_lock=package.package_lock, turn_id="turn", participants=(actor,),
            initial_state=tuple(StateValue(SRD, key, state[f"{SRD}.{key}"])
                                for key in ("combat", "entities", "state")),
        ))
        action = ActionCommand(
            campaign_id="self-restoration", action_id="end", idempotency_key="end",
            owner_id="test", writer=WriterRole.DM, expected_revision=0, turn_id="turn",
            package_id=SRD, tool_name="death_saving_throw" if death_save else "end_combat_turn",
            parameters={} if death_save else {"self_restoration": "poisoned"},
        )
        receipt = StatelessMcpMechanics(owner).execute_action(action)
        if death_save:
            assert receipt.status == "awaiting_choice"
            assert owner.snapshot("self-restoration").revision == 0
    if reroll:
        with AuthorityOwner.open(database, "test") as owner:
            assert owner.execute_action(action) == receipt
            baseline = owner.snapshot("self-restoration")
            reroll_choice = ResolveMechanicsChoiceCommand(
                campaign_id="self-restoration", action_id="end", resolution_id="reroll-save",
                owner_id="test", expected_revision=0, choice_id=receipt.choice.choice_id,
                option_id="first",
            )
            receipt = owner.resolve_mechanics_choice(reroll_choice)
            assert receipt.status == "awaiting_choice"
            assert receipt.choice_sequence == 1
            assert owner.snapshot("self-restoration") == baseline
            assert owner.resolve_mechanics_choice(reroll_choice) == receipt
    with AuthorityOwner.open(database, "test") as owner:
        assert owner.execute_action(action) == receipt
        if death_save:
            choice = ResolveMechanicsChoiceCommand(
                campaign_id="self-restoration", action_id="end", resolution_id="remove-poison",
                owner_id="test", expected_revision=0, choice_id=receipt.choice.choice_id,
                option_id="poisoned",
                choice_sequence=1 if reroll else 0,
            )
            receipt = owner.resolve_mechanics_choice(choice)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("self-restoration")
        assert set(saved.state[f"{SRD}.entities"]["hero"]["conditions"]) == {"charmed", "frightened"}
        assert len(saved.state[f"{SRD}.entities"]["hero"]["conditions"]) == 2
        assert "Self-Restoration" in str(receipt.result["result_card"])
        if reroll:
            assert saved.revision == 1
            assert len(saved.actions) == 1
            assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["focus_point"] == 13
            details = {row["label"]: row["value"]
                       for row in receipt.result["result_card"]["details"]}
            assert details["Focus spent on reroll"] == 1
            assert details["Original save total"] == 7
    with AuthorityOwner.open(database, "test") as owner:
        assert owner.snapshot("self-restoration").state == saved.state
        assert owner.execute_action(action) == receipt
        if death_save:
            assert owner.resolve_mechanics_choice(choice) == receipt
            assert saved.state[f"{SRD}.entities"]["hero"]["death_failures"] == 1
        if reroll:
            assert owner.resolve_mechanics_choice(reroll_choice) == receipt
        assert owner.snapshot("self-restoration").state == saved.state
