"""Current Storm source geometry blocks native weapons, not spell attacks or completed hits."""

from copy import deepcopy
from functools import lru_cache

import pytest
import yaml
from scripts import srd_storm_weapon_attacks as weapons
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import MechanicsRejected, RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.runtime._types import ExecutionBudget
from tests import test_srd_storm_conditions as base
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_attack_budget import _attack
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_reaction_windows import _parameters
from tests.srd_full.test_srd_sunbeam import Rolls

_BASE_RUNTIME = base._runtime


@lru_cache
def _runtime():
    text = weapons.bind_runtime_consumers(_BASE_RUNTIME()[1].decode())
    return yaml.load(text, Loader=yaml.CSafeLoader), text.encode()


@lru_cache
def _mechanic(name):
    runtime, encoded = _runtime()
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=encoded,
        declaration=ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.spells",
            input_schema=_tool_schema(runtime["tools"][name], tool_name=name),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


@pytest.fixture(autouse=True)
def _owners(monkeypatch):
    monkeypatch.setattr(base.base.base.base, "_runtime", _runtime)
    monkeypatch.setattr(base.base.base.base.budget, "_mechanic", _mechanic)
    monkeypatch.setattr(
        base.base.base.base.cards,
        "_spell_definitions",
        lambda: _runtime()[0]["definitions"]["spells"],
    )
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    monkeypatch.setattr(dice, "_rng", Rolls())


def _case(stage=5):
    state, data, params = base._case(stage)
    state[f"{SRD}.entities"]["hero"].update(
        inventory=["spear", "shortbow"], ammunition={"arrow": 5}
    )
    data["weapon_proficiencies"] = ["simple_weapons"]
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=True,
        phase="turn_active",
        active_actor="hero",
        current_actor="hero",
        action_available=True,
        normal_action_spent=False,
        attacks_remaining=0,
    )
    return state, data, params


def _blocked(state):
    return _run(weapons.QUERY, state, actor_state="hero", target_state="guard").result["context"][
        "_storm_ranged_weapon_blocked"
    ]


def test_genuine_stage_path_plane_and_tangent_qualification_is_pure(monkeypatch):
    state, data, _ = _case(4)
    assert _blocked(state) is False
    base.base._start(state, data, 9)
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    before = deepcopy(state)
    assert _blocked(state) is True and state == before and rolls.sides == []
    rows = {r["id"]: r for r in state[f"{SRD}.state"]["spatial_scene"]["entities"]}
    rows["hero"]["position"], rows["guard"]["position"] = [0, 305, 0], [640, 305, 0]
    assert _blocked(state) is False
    rows["hero"]["position"], rows["guard"]["position"] = [0, 5, 50], [640, 5, 50]
    assert _blocked(state) is False
    rows["hero"]["metadata"]["plane"] = "ethereal"
    rows["guard"]["metadata"]["plane"] = "ethereal"
    rows["hero"]["position"], rows["guard"]["position"] = [0, 0, 0], [30, 0, 0]
    assert _blocked(state) is False
    assert rolls.sides == []


def test_native_ranged_and_thrown_rejection_precedes_action_ammunition_and_dice(monkeypatch):
    state, data, _ = _case()
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    for choices in (
        {
            "weapon": "shortbow",
            "attack_mode": "ranged",
            "attack_ability": "dexterity",
            "wielding_two_handed": True,
        },
        {"weapon": "spear", "attack_mode": "thrown"},
    ):
        before = deepcopy(state)
        with pytest.raises(RulesetPreconditionRejected, match="Ranged weapon attacks"):
            _attack(state, data, attack_distance=30, **choices)
        assert state == before and rolls.sides == []
    _run("end_concentration", state, caster_state="hero")
    result = _attack(
        state,
        data,
        weapon="shortbow",
        attack_mode="ranged",
        attack_ability="dexterity",
        attack_distance=30,
        wielding_two_handed=True,
    ).result
    assert result["context"]["_attack_roll"] and rolls.sides[0] == 20
    assert state[f"{SRD}.entities"]["hero"]["ammunition"]["arrow"] == 4
    assert state[f"{SRD}.combat"]["action_available"] is False


def test_emission_repeat_preserves_roll_packet_cards_and_unrelated_attack_owners():
    runtime, encoded = _runtime()
    assert weapons.bind_runtime_consumers(encoded.decode()) == encoded.decode()
    assert weapons.bind_saved_attack_consumers(encoded.decode()) == encoded.decode()
    parent = _BASE_RUNTIME()[0]
    changed = {n for n in parent["tools"] if runtime["tools"][n] != parent["tools"][n]}
    assert changed == set(weapons.OWNERS)
    for name in weapons.OWNERS:
        actual = deepcopy(runtime["tools"][name])
        assert actual["effects"][0]["condition"].startswith("lasting_state.has_storm_sources")
        actual["effects"].pop(0)
        assert actual == parent["tools"][name]
    for name in ("d20_test", "cast_spell_attack_damage", "complete_srd_character_attack"):
        assert runtime["tools"][name] == parent["tools"][name]


def test_native_melee_is_available_under_storm_and_old_true_delegate_fails_closed(monkeypatch):
    state, data, _ = _case()
    rows = {r["id"]: r for r in state[f"{SRD}.state"]["spatial_scene"]["entities"]}
    rows["hero"]["position"] = [25, 0, 0]
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    result = _attack(state, data, weapon="spear", attack_mode="melee", attack_distance=5).result
    assert result["context"]["_attack_roll"] and rolls.sides[0] == 20
    assert state[f"{SRD}.state"]["has_storm_sources"] is True
    parent = deepcopy(_BASE_RUNTIME()[0])
    parent["tools"]["resolve_combat_attack"]["effects"] = [
        {"call": "resolve_srd_settled_weapon_attack", "params": {}, "export_context": "*"}
    ]
    with pytest.raises(ValueError, match="Restore the ordinary weapon owner"):
        weapons.bind_runtime_consumers(yaml.dump(parent, Dumper=yaml.CSafeDumper))
    settled = deepcopy(_runtime()[0])
    settled["tools"]["resolve_combat_attack"] = parent["tools"]["resolve_combat_attack"]
    settled["tools"][weapons.OWNERS[3]]["effects"].pop(0)
    encoded = yaml.dump(settled, Dumper=yaml.CSafeDumper, sort_keys=False)
    rebound = yaml.load(weapons.bind_saved_attack_consumers(encoded), Loader=yaml.CSafeLoader)
    assert rebound["tools"]["resolve_combat_attack"] == settled["tools"]["resolve_combat_attack"]
    assert rebound["tools"][weapons.OWNERS[3]] == _runtime()[0]["tools"][weapons.OWNERS[3]]


def test_paid_public_storm_blocks_weapon_then_source_end_pending_reopen_and_retry(
    tmp_path, monkeypatch
):
    public = base.base.base.base.public_casting
    monkeypatch.setattr(public, "bundled_srd_archive", base.base.base.base._archive)
    monkeypatch.setattr(public, "_spell_case", lambda _spell: base.base.base._case())
    rolls, budgets = Rolls(), []
    monkeypatch.setattr(dice, "_rng", rolls)
    consume = ExecutionBudget.consume_operation

    def count(budget):
        if not budgets or budgets[-1] is not budget:
            budgets.append(budget)
        consume(budget)

    monkeypatch.setattr(ExecutionBudget, "consume_operation", count)
    database, receipts = tmp_path / "storm-weapons.db", []
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(
            owner,
            base.base.base.storm.SPELL,
            participant_overrides={"weapon_proficiencies": ["simple_weapons"]},
            entity_overrides={"inventory": ["spear", "shortbow"], "ammunition": {"arrow": 5}},
            target_overrides={"hp": 1000, "max_hp": 1000},
        )
        receipts.append(_act(owner, "cast_spell_outside_combat", **params))
        receipts.append(
            _act(
                owner,
                "start_combat_encounter_entities",
                first_actor="hero",
                second_actor="guard",
                first_surprised=False,
                second_surprised=False,
                tie_winner="first",
            )
        )
        for index in range(4):
            receipts.append(_act(owner, "begin_combat_turn"))
            if index < 3:
                for name in (
                    "end_combat_turn",
                    "advance_combat_encounter_turn",
                    "begin_combat_turn",
                    "end_combat_turn",
                    "advance_combat_encounter_turn",
                ):
                    receipts.append(_act(owner, name))
        parameters = _parameters() | {
            "weapon": "shortbow",
            "attack_mode": "ranged",
            "attack_ability": "dexterity",
            "attack_distance": 30,
            "wielding_two_handed": True,
        }
        parameters.pop("actor_state")
        before, faces = owner.snapshot("casting"), deepcopy(rolls.sides)
        with pytest.raises(MechanicsRejected, match="Ranged weapon attacks"):
            _act(owner, "begin_srd_character_attack", **parameters)
        assert owner.snapshot("casting") == before and rolls.sides == faces
        receipts.append(_act(owner, "end_concentration", caster_state="hero"))
        receipts.append(_act(owner, "begin_srd_character_attack", **parameters))
        saved = owner.snapshot("casting")
        event_id = saved.state[f"{SRD}.state"]["pending_srd_action_stack"][-1]
        saved_roll = deepcopy(
            saved.state[f"{SRD}.state"]["pending_srd_actions"][event_id]["attack_roll"]
        )
        assert saved.state[f"{SRD}.entities"]["hero"]["ammunition"]["arrow"] == 4
        assert saved.state[f"{SRD}.combat"]["action_available"] is False
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        receipts.append(_act(owner, "complete_srd_pending_action", pending_action_id=event_id))
        assert receipts[-1][0].result["context"]["_attack_roll"] == saved_roll
        saved, faces = owner.snapshot("casting"), deepcopy(rolls.sides)
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        for result, command in receipts:
            assert result.status is ActionStatus.COMMITTED
            assert owner.execute_action(command) == result
        assert owner.snapshot("casting") == saved and rolls.sides == faces
    assert all(budget.max_operations == 10000 for budget in budgets)
    print("Storm weapon/public max operations:", max(budget.operations for budget in budgets))
