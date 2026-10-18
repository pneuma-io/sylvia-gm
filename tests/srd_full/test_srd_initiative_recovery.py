"""Monk Initiative recovery uses saved class levels, Focus, healing and rest state."""

from copy import deepcopy

import pytest

from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_unarmed_creatures import _case


def _monk(level=15, focus=1):
    state, data = _case()
    data.update(classes=[{"id": "monk", "level": level}], level=level, fighting_styles=[])
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    entities = state[f"{SRD}.entities"]
    entities["hero"].update(name="Monk", hp=1, max_hp=50, initiative_modifier=2,
                             resources={"focus_point": focus},
                             resource_maxima={"focus_point": level})
    entities["guard"].update(initiative_modifier=1)
    entities["ally"] = {**deepcopy(entities["guard"]), "name": "Ally"}
    return state, data


def _start(state, data, *, group=False, users=()):
    parameters = {"combatants": ["hero", "guard", "ally"]} if group else {
        "first_actor": "hero", "second_actor": "guard", "tie_winner": "first",
    }
    return _run("start_group_combat_encounter" if group else "start_combat_encounter_entities",
                state, participant_data=data, uncanny_metabolism_users=list(users), **parameters)


@pytest.mark.parametrize("group", [False, True])
def test_uncanny_metabolism_restores_focus_and_heals_once_on_initiative(group):
    state, data = _monk()
    result = _start(state, data, group=group, users=("hero",))
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["resources"]["focus_point"] == 15
    assert 17 <= hero["hp"] <= 26
    assert hero["uncanny_metabolism_used"] is True
    assert "Uncanny Metabolism" in str(result.result["result_card"])
    assert "Perfect Focus" not in str(result.result["result_card"])
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _start(state, data, group=group, users=("hero",))
    assert state == before


@pytest.mark.parametrize("level,focus,expected", [(14, 1, 1), (15, 0, 4), (15, 3, 4), (15, 6, 6)])
def test_perfect_focus_restores_only_to_four_without_metabolism(level, focus, expected):
    state, data = _monk(level, focus)
    result = _start(state, data)
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["resources"]["focus_point"] == expected
    assert hero["hp"] == 1
    assert hero.get("uncanny_metabolism_used") is not True
    assert ("Perfect Focus" in str(result.result["result_card"])) is (expected != focus)


@pytest.mark.parametrize("problem", ["level", "spent", "maximum", "npc", "outsider"])
def test_invalid_metabolism_selection_rejects_entire_encounter_start(problem):
    state, data = _monk(1 if problem == "level" else 15)
    hero = state[f"{SRD}.entities"]["hero"]
    if problem == "spent":
        hero["uncanny_metabolism_used"] = True
    if problem == "maximum":
        hero["resource_maxima"]["focus_point"] = 99
    users = ("guard",) if problem == "npc" else ("ally",) if problem == "outsider" else ("hero",)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _start(state, data, users=users)
    assert state == before


@pytest.mark.parametrize("case", ["blocked", "full", "unconscious", "armored"])
def test_metabolism_uses_shared_healing_rules_without_armor_restrictions(case):
    state, data = _monk(2, 0)
    hero = state[f"{SRD}.entities"]["hero"]
    if case == "blocked":
        hero["hit_point_recovery_blocked"] = True
    elif case == "full":
        hero["hp"] = hero["max_hp"]
    elif case == "unconscious":
        hero.update(hp=0, conditions=["unconscious"], death_successes=1, death_failures=2,
                    stable=False, stable_recovery_hours_remaining=0)
    else:
        hero.update(worn_armor="chain_mail", worn_shield="shield")
    before_hp = hero["hp"]
    _start(state, data, users=("hero",))
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["uncanny_metabolism_used"] is True
    assert hero["resources"]["focus_point"] == 2
    if case in ("blocked", "full"):
        assert hero["hp"] == before_hp
    else:
        assert 3 <= hero["hp"] - before_hp <= 8
    if case == "unconscious":
        assert hero["conditions"] == []
        assert hero["death_failures"] == 0


@pytest.mark.parametrize("kind,used", [("short", True), ("long", False)])
def test_metabolism_recovers_only_on_long_rest(kind, used):
    state, _ = _monk()
    state[f"{SRD}.entities"]["hero"]["uncanny_metabolism_used"] = True
    result = _run("recover_srd_rest_resources", state, target_state="hero", kind=kind)
    assert state[f"{SRD}.entities"]["hero"]["uncanny_metabolism_used"] is used
    assert ("Uncanny Metabolism" in str(result.result)) is (kind == "long")


def test_public_initiative_recovery_replays_and_requires_completed_long_rest(tmp_path):
    import io
    from dataclasses import replace

    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner, CampaignCreate
    from tests.srd_full.full_package_fixture import bundled_srd_archive
    from sylvia_gm.domain.authority import ActionStatus, StateValue, WriterRole
    from tests.srd_full.test_srd_second_wind import _installed_fighter

    database = tmp_path / "initiative-recovery.db"
    state, data = _monk()
    state[f"{SRD}.entities"]["ally"].update(
        creature_kind="character", hp=1, max_hp=50,
        resources={"focus_point": 0}, resource_maxima={"focus_point": 2},
    )

    def command(owner, tool, **parameters):
        revision = owner.snapshot("initiative-recovery").revision
        return ActionCommand(
            campaign_id="initiative-recovery", action_id=f"action-{revision}",
            idempotency_key=f"action-{revision}", owner_id="test", writer=WriterRole.DM,
            expected_revision=revision, turn_id="turn", package_id=SRD,
            tool_name=tool, parameters=parameters,
        )

    def act(owner, tool, **parameters):
        receipt = StatelessMcpMechanics(owner).execute_action(command(owner, tool, **parameters))
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        return receipt

    with AuthorityOwner.open(database, "test") as owner:
        package = owner.install_package_archive(io.BytesIO(bundled_srd_archive()), install_id="srd")
        actor = _installed_fighter(package.package_lock, fighter=True)
        actor = replace(actor, data={**actor.data, **data})
        companion = replace(actor, participant_id="ally", data={**actor.data,
            "name": "Ally", "level": 2, "classes": [{"id": "monk", "level": 2}]})
        owner.create_campaign(CampaignCreate(
            campaign_id="initiative-recovery", display_name="Synthetic Monk", writer=WriterRole.DM,
            package_lock=package.package_lock, turn_id="turn", participants=(actor, companion),
            initial_state=tuple(StateValue(SRD, key, state[f"{SRD}.{key}"])
                                for key in ("combat", "entities", "state")),
        ))
        start = command(owner, "start_group_combat_encounter",
                        combatants=["hero", "guard", "ally"],
                        uncanny_metabolism_users=["hero", "ally"])
        receipt = StatelessMcpMechanics(owner).execute_action(start)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("initiative-recovery")
        assert saved.state[f"{SRD}.entities"]["hero"]["uncanny_metabolism_used"] is True
        assert saved.state[f"{SRD}.entities"]["ally"]["resources"]["focus_point"] == 2
        assert saved.state[f"{SRD}.entities"]["ally"]["uncanny_metabolism_used"] is True
    with AuthorityOwner.open(database, "test") as owner:
        assert owner.snapshot("initiative-recovery").state == saved.state
        assert owner.execute_action(start) == receipt
        assert owner.snapshot("initiative-recovery").state == saved.state
        act(owner, "end_combat_encounter", end_reason="fighting_stopped")
        for kind, activity, rounds, used in [("short", "light", 600, True),
                                             ("long", "sleep", 4800, False)]:
            act(owner, "begin_srd_rest", target_state="hero", kind=kind, activity=activity)
            act(owner, "advance_srd_lasting_time", elapsed_rounds=rounds)
            rest = act(owner, "complete_srd_rest", target_state="hero")
            hero = owner.snapshot("initiative-recovery").state[f"{SRD}.entities"]["hero"]
            assert hero["uncanny_metabolism_used"] is used
            assert ("Uncanny Metabolism" in str(rest.result["result_card"])) is (kind == "long")
        again = act(owner, "start_combat_encounter_entities", first_actor="hero",
                    second_actor="guard", first_surprised=False, second_surprised=False,
                    tie_winner="first", uncanny_metabolism_users=["hero"])
        assert "Uncanny Metabolism" in str(again.result["result_card"])
        assert owner.snapshot("initiative-recovery").state[
            f"{SRD}.entities"]["hero"]["uncanny_metabolism_used"] is True
        assert owner.snapshot("initiative-recovery").state[
            f"{SRD}.entities"]["ally"]["uncanny_metabolism_used"] is True
