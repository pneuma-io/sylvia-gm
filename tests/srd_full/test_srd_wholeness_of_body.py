"""Open Hand healing owns a Wisdom-derived pool and the ordinary recovery boundaries."""

from copy import deepcopy
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_unarmed_creatures import _case


def _monk(level=6, wisdom=16):
    state, data = _case()
    data.update(classes=[{"id": "monk", "level": level}], level=level,
                subclasses={"monk": "warrior_of_the_open_hand"}, fighting_styles=[])
    data["abilities"]["wisdom"] = wisdom
    maximum = max(1, (wisdom - 10) // 2)
    state[f"{SRD}.entities"]["hero"].update(
        hp=1, max_hp=50, resources={"wholeness_of_body": maximum},
        resource_maxima={"wholeness_of_body": maximum},
    )
    return state, data


def _heal(state, data):
    return _run("use_monk_wholeness_of_body", state, participant_data=data,
                actor="hero", actor_state="hero")


@pytest.mark.parametrize("level,die", [(6, 8), (11, 10), (17, 12)])
def test_wholeness_uses_earned_die_wisdom_and_only_bonus_action(level, die):
    state, data = _monk(level)
    result = _heal(state, data).result
    context = result["context"]
    rolled = context["_wholeness_roll"]["total"]
    assert 1 <= rolled <= die
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["hp"] == 1 + rolled + 3
    assert hero["resources"]["wholeness_of_body"] == 2
    assert hero["bonus_action_available"] is False
    assert state[f"{SRD}.combat"]["action_available"] is True
    card = result["result_card"]
    assert card["title"] == "Wholeness of Body"
    assert card["equation"] == {
        "die_sides": die, "roll": rolled, "modifier": 3, "total": rolled + 3,
    }


@pytest.mark.parametrize("problem", ["level", "subclass", "empty", "maximum", "bonus", "stunned"])
def test_invalid_wholeness_use_preserves_hp_resources_and_action_budget(problem):
    state, data = _monk()
    hero = state[f"{SRD}.entities"]["hero"]
    if problem == "level":
        data["classes"][0]["level"] = 5
    elif problem == "subclass":
        data["subclasses"] = {"monk": "another_subclass"}
    elif problem == "empty":
        hero["resources"]["wholeness_of_body"] = 0
    elif problem == "maximum":
        hero["resource_maxima"]["wholeness_of_body"] = 99
    elif problem == "bonus":
        hero["bonus_action_available"] = False
    else:
        hero["conditions"] = ["stunned"]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _heal(state, data)
    assert state == before


@pytest.mark.parametrize("mode", ["minimum", "capped", "blocked", "armored"])
def test_wholeness_respects_healing_rules_and_does_not_require_unarmored_state(monkeypatch, mode):
    monkeypatch.setattr(dice, "_rng", Random(2))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data = _monk(wisdom=8 if mode == "minimum" else 16)
    hero = state[f"{SRD}.entities"]["hero"]
    if mode == "capped":
        hero["hp"] = 49
    elif mode == "blocked":
        hero["hit_point_recovery_blocked"] = True
    elif mode == "armored":
        hero["worn_armor"] = {"inventory_item": "plate_armor"}
    before_hp, before_uses = hero["hp"], hero["resources"]["wholeness_of_body"]
    result = _heal(state, data).result
    hero = state[f"{SRD}.entities"]["hero"]
    expected = 0 if mode == "blocked" else (4 if mode == "armored" else 1)
    assert hero["hp"] - before_hp == expected
    assert hero["resources"]["wholeness_of_body"] == before_uses - 1
    assert result["context"]["_wholeness_hp_restored"] == expected


@pytest.mark.parametrize("kind,remaining", [("short", 1), ("long", 3)])
def test_only_long_rest_restores_wholeness_uses(kind, remaining):
    state, _ = _monk()
    state[f"{SRD}.entities"]["hero"]["resources"]["wholeness_of_body"] = 1
    _run("recover_srd_rest_resources", state, target_state="hero", kind=kind)
    assert state[f"{SRD}.entities"]["hero"]["resources"]["wholeness_of_body"] == remaining


@pytest.mark.parametrize("level,wisdom,expected", [(5, 16, 0), (6, 8, 1), (6, 16, 3)])
def test_creation_derives_wholeness_pool_from_feature_and_wisdom(level, wisdom, expected):
    from sylvia_gm.application.initial_character_state import materialize_initial_character_state
    from tests.test_reusable_participants import (
        _participant,
        _participant_authority,
        _starting_state,
    )

    _, declaration = _participant_authority()
    profile = _participant("Open Hand Monk")
    profile.update(level=level, classes=[{"id": "monk", "level": level}], fighting_styles=[],
                   subclasses={"monk": "warrior_of_the_open_hand"})
    profile["abilities"]["wisdom"] = wisdom
    authored = _starting_state()
    authored.update(worn_armor=None, worn_shield=None)
    completed = materialize_initial_character_state(
        profile, authored, entity_state_schema=declaration.entity_state_schema,
        initial_state_calculations=declaration.initial_state_calculations,
        initial_state_validation=declaration.initial_state_validation,
    )
    assert completed["resources"].get("wholeness_of_body", 0) == expected
    assert completed["resource_maxima"].get("wholeness_of_body", 0) == expected


@pytest.mark.parametrize("level,old_maximum,next_maximum", [(5, 0, 3), (7, 3, 4)])
def test_level_up_grants_or_expands_wholeness_without_restoring_spent_uses(
    tmp_path, level, old_maximum, next_maximum,
):
    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus
    from sylvia_gm.domain.errors import CommandRejected
    from tests.srd_full.test_srd_advancement import award, build, command, create, resolve

    database = tmp_path / "wholeness-level.db"
    with AuthorityOwner.open(database, "test") as owner:
        create(owner, level=level, participant_overrides={
            "classes": [{"id": "monk", "level": level}], "fighting_styles": [],
            "subclasses": {"monk": "warrior_of_the_open_hand"},
            "abilities": {"strength": 14, "dexterity": 14, "constitution": 14,
                          "intelligence": 10, "wisdom": 16, "charisma": 10},
        }, entity_overrides={
            "resources": {"hit_die": 0, "focus_point": 0, "wholeness_of_body": 0},
            "resource_maxima": {"hit_die": level, "focus_point": level,
                                "wholeness_of_body": old_maximum},
        })
        award(owner)
        proposal = build(owner)
        proposal["class_id"] = "monk"
        proposal["build"][0]["armor_class"] = 15
        if level == 7:
            proposal["build"][0]["abilities"]["wisdom"] = 18
            proposal["build"][0]["armor_class"] += 1
        proposal["build"][0]["resources"] = [
            {"id": "wholeness_of_body", "maximum": 99, "source": "Open Hand"},
        ]
        before = owner.snapshot("advancement")
        with pytest.raises(CommandRejected, match="Wholeness of Body capacity"):
            owner.execute_action(command(owner, "level_up_srd_character", proposal, "invented"))
        assert owner.snapshot("advancement").state == before.state
        proposal["build"][0]["resources"] = []
        waiting = owner.execute_action(command(owner, "level_up_srd_character", proposal, "level"))
        assert resolve(owner, waiting).status is ActionStatus.COMMITTED
        saved = owner.snapshot("advancement")
        hero = saved.state[f"{SRD}.entities"]["hero"]
        assert hero["resource_maxima"]["wholeness_of_body"] == next_maximum
        assert hero["resources"]["wholeness_of_body"] == next_maximum - old_maximum
    with AuthorityOwner.open(database, "test") as owner:
        assert owner.snapshot("advancement").state == saved.state


def test_public_wholeness_reopens_replays_once_and_requires_completed_long_rest(tmp_path):
    from dataclasses import replace

    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus
    from tests.srd_full.test_srd_advancement import command, create

    database = tmp_path / "wholeness.db"
    with AuthorityOwner.open(database, "test") as owner:
        create(owner, level=6, participant_overrides={
            "classes": [{"id": "monk", "level": 6}], "fighting_styles": [],
            "subclasses": {"monk": "warrior_of_the_open_hand"},
        }, entity_overrides={
            "hp": 5, "max_hp": 50, "speed": 30,
            "resources": {"hit_die": 6, "focus_point": 6, "wholeness_of_body": 1},
            "resource_maxima": {"hit_die": 6, "focus_point": 6, "wholeness_of_body": 1},
        })
        action = command(owner, "use_monk_wholeness_of_body",
                         {"actor": "hero", "actor_state": "hero"}, "heal")
        action = replace(action, parameters={"actor": "hero", "actor_state": "hero"})
        receipt = StatelessMcpMechanics(owner).execute_action(action)
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("advancement")
        assert 5 < saved.state[f"{SRD}.entities"]["hero"]["hp"] <= 13
        assert receipt.result["result_card"]["title"] == "Wholeness of Body"
    with AuthorityOwner.open(database, "test") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("advancement").state == saved.state
        assert mechanics.execute_action(action) == receipt
        for kind, rounds, remaining in [("short", 600, 0), ("long", 4800, 1)]:
            for name, parameters, suffix in [
                ("begin_srd_rest", {"target_state": "hero", "kind": kind,
                                    "activity": "sleep" if kind == "long" else "light"}, "begin"),
                ("advance_srd_lasting_time", {"elapsed_rounds": rounds}, "time"),
                ("complete_srd_rest", {"target_state": "hero"}, "complete"),
            ]:
                rest_action = replace(command(owner, name, parameters, kind + suffix),
                                      parameters=parameters)
                result = mechanics.execute_action(rest_action)
                assert result.status is ActionStatus.COMMITTED, result.result
            hero = owner.snapshot("advancement").state[f"{SRD}.entities"]["hero"]
            assert hero["resources"]["wholeness_of_body"] == remaining
