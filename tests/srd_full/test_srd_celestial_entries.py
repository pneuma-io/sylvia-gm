"""Chosen Celestial entry lights follow committed physical movement and native packets."""

from copy import deepcopy
from pathlib import Path

import pytest
from scripts.srd_full.srd_celestial_entries import (
    BUDGET,
    ROUTE,
    SEGMENT,
    bind_runtime_consumers,
    emit_late_qualification,
)
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import MechanicsRejected, RulesetPreconditionRejected
from tests import test_srd_action_budget as budget
from tests import test_srd_celestial_lights as light
from tests import test_srd_conjured_presences as presence
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_travel_dwelling as dwelling
from tests import test_srd_spell_travel_telekinesis_fall as fall
from tests.srd_full.test_srd_spell_travel import SRD, _point

actual_fall_scope = light.actual_fall_scope
conjured_scope = light.conjured_scope
celestial_scope = light.celestial_scope


@pytest.fixture
def entry_scope(celestial_scope, monkeypatch):
    monkeypatch.setattr(
        fall, "RUNTIME_PATH", Path(".scratch/celestial-entries-composition/runtime.yaml")
    )


def _case(*, guard_hp=500, begin=True):
    state, data, params = light._case(begin=begin)
    state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["position"] = [18, 0, 0]
    state[f"{SRD}.entities"]["guard"]["hp"] = guard_hp
    return state, data, params


def _guard_turn(state, data):
    budget._run("end_combat_turn", state, actor="hero")
    state[f"{SRD}.combat"]["current_actor"] = "guard"
    budget._run("begin_combat_turn", state, participant_data=data, actor="guard")


def _walk(state, data, destination=(-10, 0, 0), choices=None):
    params = {"actor_state": "guard", "destination": list(destination)}
    if choices is not None:
        params["celestial_entry_choices"] = choices
    return budget._run("move_srd_scene_creature", state, participant_data=data, **params)


def _choice():
    return [{"source_id": "presence", "light": "searing"}]


def test_entry_binding_preserves_original_segment_and_unrelated_tools_exactly():
    text = Path(".scratch/celestial-lights-composition/runtime.yaml").read_text()
    original = _load_runtime(text)
    joined = _load_runtime(bind_runtime_consumers(text))
    expected = deepcopy(original["tools"]["move_srd_scene_creature_node"])
    expected["metadata"].update(category="internal", visible_to_player=False)
    assert joined["tools"][SEGMENT] == expected
    for name, tool in original["tools"].items():
        if name not in {"move_srd_scene_creature", "move_srd_scene_creature_node"}:
            assert joined["tools"][name] == tool
    assert _load_runtime(bind_runtime_consumers(bind_runtime_consumers(text))) == joined
    assert _load_runtime(emit_late_qualification(bind_runtime_consumers(text))) == joined
    budget = original["tools"]["spend_combat_movement"]
    stop = next(
        i
        for i, effect in enumerate(budget["effects"])
        if effect.get("set", {}).get("value") == "combat.movement_remaining - _movement_cost"
    )
    assert joined["tools"][BUDGET]["effects"] == budget["effects"][:stop]
    assert joined["tools"][BUDGET]["pre_conditions"] == budget["pre_conditions"]
    assert "spatial_mutation" not in str(joined["tools"][ROUTE]["effects"])
    assert "spend_combat_movement" not in str(joined["tools"][ROUTE]["effects"])
    assert "actor_state.scene_movement_spent" not in str(joined["tools"][ROUTE]["effects"])


def test_paid_source_entry_commits_boundary_rolls_native_light_then_finishes_route(
    entry_scope, actual_fall_scope, monkeypatch
):
    state, data, params = _case()
    game = light._rolls(monkeypatch, [(12, 3)] * 4 + [(12, 2)] * 6 + [(20, 10)])
    light._cast(state, data, params)
    _guard_turn(state, data)
    resources = deepcopy(state[f"{SRD}.entities"]["hero"]["resources"])
    revision = state[f"{SRD}.state"]["spatial_scene"]["map"]["revision"]
    _walk(state, data, choices=_choice())
    assert _point(state, "guard") == [-10, 0, 0]
    assert state[f"{SRD}.state"]["spatial_scene"]["map"]["revision"] == revision + 2
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 494
    assert state[f"{SRD}.combat"]["movement_spent"] == 28
    assert presence._source(state)["event_target_ids"] == ["guard"]
    assert presence._source(state)["cast_id"] == "srd_cast:1"
    assert state[f"{SRD}.entities"]["hero"]["resources"] == resources
    assert not game.rolls


def test_actual_lethal_native_packet_stops_at_entry_without_spending_remaining_route(
    entry_scope, actual_fall_scope, monkeypatch
):
    state, data, params = _case(guard_hp=5)
    game = light._rolls(monkeypatch, [(12, 3)] * 4 + [(12, 2)] * 6 + [(20, 10)])
    light._cast(state, data, params)
    _guard_turn(state, data)
    _walk(state, data, choices=_choice())
    assert _point(state, "guard") == [15.4, 0, 0]
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 0
    assert state[f"{SRD}.entities"]["guard"]["is_alive"] is False
    assert state[f"{SRD}.combat"]["movement_spent"] == pytest.approx(2.6)
    assert presence._source(state)["event_target_ids"] == ["guard"]
    assert not game.rolls


def test_upcast_entry_healing_uses_native_dice_modifier_and_no_extra_action(
    entry_scope, actual_fall_scope, monkeypatch
):
    state, data, params = _case(guard_hp=470)
    params["slot_resource"] = "spell_slot_9"
    game = light._rolls(monkeypatch, [(12, 3)] * 6 + [(12, 2)] * 6)
    light._cast(state, data, params)
    _guard_turn(state, data)
    _walk(state, data, choices=[{"source_id": "presence", "light": "healing"}])
    assert _point(state, "guard") == [-10, 0, 0]
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 486
    assert state[f"{SRD}.combat"]["action_available"] is True
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_9"] == 2
    assert presence._source(state)["upcast"] == 2
    assert presence._source(state)["event_target_ids"] == ["guard"]
    assert not game.rolls


@pytest.mark.parametrize("defect", ["duplicate", "unknown", "miss", "plane"])
def test_invalid_actual_entry_intent_is_atomic_before_movement_or_native_roll(
    entry_scope, actual_fall_scope, monkeypatch, defect
):
    state, data, params = _case()
    game = light._rolls(monkeypatch, [(12, 3)] * 4)
    light._cast(state, data, params)
    _guard_turn(state, data)
    choices, destination = _choice(), [-10, 0, 0]
    if defect == "duplicate":
        choices += [{"source_id": "presence", "light": "healing"}]
    elif defect == "unknown":
        choices[0]["source_id"] = "missing"
    elif defect == "miss":
        destination = [18, 20, 0]
    else:
        state[f"{SRD}.state"]["spatial_scene"]["entities"][1]["metadata"]["plane"] = "ethereal"
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _walk(state, data, destination, choices)
    assert state == before and not game.rolls


def test_actual_unseeing_caster_cannot_light_entry_and_ordinary_route_still_completes(
    entry_scope, actual_fall_scope, monkeypatch
):
    state, data, params = _case()
    game = light._rolls(monkeypatch, [(12, 3)] * 4)
    light._cast(state, data, params)
    _guard_turn(state, data)
    state[f"{SRD}.entities"]["hero"]["conditions"].append("blinded")
    _walk(state, data, choices=_choice())
    assert _point(state, "guard") == [-10, 0, 0]
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 500
    assert presence._source(state)["event_target_ids"] == ["hero"]
    assert state[f"{SRD}.combat"]["movement_spent"] == 28
    assert not game.rolls


def test_initially_over_budget_entry_route_rejects_before_any_native_entry_roll(
    entry_scope, actual_fall_scope, monkeypatch
):
    state, data, params = _case()
    game = light._rolls(monkeypatch, [(12, 3)] * 4 + [(12, 2)] * 6 + [(20, 10)])
    light._cast(state, data, params)
    _guard_turn(state, data)
    before = deepcopy(state)
    remaining_rolls = list(game.rolls)
    with pytest.raises(RulesetPreconditionRejected, match="enough movement"):
        _walk(state, data, [-20, 0, 0], _choice())
    assert state == before
    assert game.rolls == remaining_rolls


def test_no_choice_has_exact_original_ordinary_movement_state(
    entry_scope, actual_fall_scope, monkeypatch
):
    state, data, params = _case()
    game = light._rolls(monkeypatch, [(12, 3)] * 4)
    light._cast(state, data, params)
    _guard_turn(state, data)
    original_state = deepcopy(state)
    _walk(state, data)
    with monkeypatch.context() as comparison:
        comparison.setattr(
            fall, "RUNTIME_PATH", Path(".scratch/celestial-lights-composition/runtime.yaml")
        )
        fall._runtime.cache_clear()
        fall._mechanic.cache_clear()
        _walk(original_state, data)
    fall._runtime.cache_clear()
    fall._mechanic.cache_clear()
    assert state == original_state and not game.rolls


def test_real_paid_source_entry_native_card_reopens_and_retries_once(
    entry_scope, actual_fall_scope, monkeypatch, tmp_path
):
    monkeypatch.setattr(public, "_spell_case", lambda _spell: _case(begin=False))
    game = light._rolls(
        monkeypatch,
        [(20, 20), (20, 10), (20, 1)] + [(12, 3)] * 4 + [(12, 2)] * 6 + [(20, 10)],
    )
    database = tmp_path / "celestial-entry.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(
            owner, "conjure_celestial", target_overrides={"hp": 500, "max_hp": 500}
        )
        mechanics = StatelessMcpMechanics(owner)

        def commit(name, intent, action):
            command = dwelling._command(intent, owner.snapshot("casting").revision, name, action)
            result = mechanics.execute_action(command)
            assert result.status is ActionStatus.COMMITTED, result.result
            return command, result

        commit("start_group_combat_encounter", {"combatants": ["hero", "guard", "other"]}, "start")
        commit("begin_combat_turn", {}, "begin-hero")
        paid_command, paid = commit("cast_spell", params, "paid-celestial")
        cast = owner.snapshot("casting")
        assert presence._source(cast.state)["cast_id"] == "srd_cast:1"
        assert cast.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_7"] == 2
        assert mechanics.execute_action(paid_command) == paid
        commit("end_combat_turn", {}, "end-hero")
        commit("advance_group_combat_encounter_turn", {}, "advance")
        commit("begin_combat_turn", {}, "begin-guard")
        ready = owner.snapshot("casting")
        assert ready.state[f"{SRD}.combat"]["active_actor"] == "guard"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == ready
        mechanics = StatelessMcpMechanics(owner)
        intent = {
            "actor_state": "guard",
            "destination": [-10, 0, 0],
            "celestial_entry_choices": _choice(),
        }
        invalid = dwelling._command(
            intent | {"celestial_entry_choices": _choice() * 2},
            ready.revision,
            "move_srd_scene_creature",
            "invalid-entry",
        )
        with pytest.raises(MechanicsRejected, match="at most one light"):
            mechanics.execute_action(invalid)
        assert owner.snapshot("casting").state == ready.state
        command = dwelling._command(
            intent, owner.snapshot("casting").revision, "move_srd_scene_creature", "entry"
        )
        result = mechanics.execute_action(command)
        assert result.status is ActionStatus.COMMITTED, result.result
        rows = {row["label"]: row["value"] for row in result.result["result_card"]["summary"]}
        assert rows["Celestial lights"] == 1
        assert rows["Celestial Radiant damage"] == 6
        saved = owner.snapshot("casting")
        assert _point(saved.state, "guard") == [-10, 0, 0]
        assert saved.state[f"{SRD}.entities"]["guard"]["hp"] == 494
        assert saved.state[f"{SRD}.combat"]["movement_spent"] == 28
        assert saved.state[f"{SRD}.combat"]["action_available"] is True
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_7"] == 2
        assert presence._source(saved.state)["event_target_ids"] == ["guard"]
        assert mechanics.execute_action(command) == result and owner.snapshot("casting") == saved
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        mechanics = StatelessMcpMechanics(owner)
        assert mechanics.execute_action(command) == result and owner.snapshot("casting") == saved
    assert not game.rolls
