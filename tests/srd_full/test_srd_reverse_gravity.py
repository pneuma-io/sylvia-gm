"""Paid gravity uses actual bodies, reachable grabs and the real saved fall opportunity."""

from copy import deepcopy
from dataclasses import replace
from functools import lru_cache

import pytest
import yaml
from scripts.srd_full.srd_reverse_gravity import (
    PROFILES,
    PUBLIC_TOOLS,
    bind_runtime_consumers,
    extend_runtime,
    validate_profiles,
)
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.application import package_runtime
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_gust_of_wind as gust
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_travel_telekinesis_fall as scope
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_spell_travel import SRD, _point, _TravelDice, dice
from tests.srd_full.test_srd_spell_travel import _case as spell_case
from tests.srd_full.test_srd_spell_travel_telekinesis import _tk_case


@lru_cache
def _runtime():
    _, encoded = gust._runtime()
    text = bind_runtime_consumers(extend_runtime(encoded.decode()))
    runtime = _load_runtime(text)
    runtime["definitions"]["spells"].update(deepcopy(PROFILES))
    for name in (
        "cast_spell",
        "cast_spell_node",
        "cast_spell_outside_combat",
        "begin_srd_spell_cast",
        "begin_srd_spell_cast_internal",
        "begin_srd_spell_action",
    ):
        for p in runtime["tools"].get(name, {}).get("parameters", []):
            if p["name"] == "spell" and "enum_values" in p:
                p["enum_values"] = sorted(set(p["enum_values"]) | PROFILES.keys())
    return runtime, yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False).encode()


@pytest.fixture
def gravity_scope(monkeypatch):
    monkeypatch.setattr(scope, "_runtime", _runtime)
    monkeypatch.setattr(scope, "EXTRA_PUBLIC_TOOLS", frozenset(PUBLIC_TOOLS))
    execute = package_runtime.execute_tool

    def evidence(*args, **kwargs):
        result = execute(*args, **kwargs)
        if not result.success:
            print("Actual gravity failure:", result.tool_name, result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", evidence)
    yield from scope.actual_fall_scope.__wrapped__(monkeypatch)


def _case():
    state, data = _tk_case("reverse_gravity")
    _, _, params = spell_case("reverse_gravity")
    params.pop("targets", None)
    params["gravity_origin"] = [50, 0, 0]
    scene = state[f"{SRD}.state"]["spatial_scene"]
    next(b for b in scene["entities"] if b["id"] == "hero")["position"] = [-15, 0, 60]
    next(b for b in scene["entities"] if b["id"] == "orb")["position"] = [8, 0, 0]
    scene["geometry"].append(
        {
            "id": "caster-platform",
            "map_id": "information",
            "volume": {"kind": "box", "min": [-20, -5, 59], "max": [-10, 5, 60]},
            "active": True,
            "movement": "blocking",
            "occupies_cells": True,
            "visibility": "opaque",
            "metadata": {"plane": "material", "ground": True},
            "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
        }
    )
    for grant in data["spellcasting_grants"]:
        if grant["source"] == "wizard":
            grant["spells"].append("feather_fall")
    data["wizard_spellbook"].append("feather_fall")
    state[f"{SRD}.entities"]["hero"]["prepared_spells"].append("feather_fall")
    state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] = 3
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["dexterity"] = 0
    feather_state, _, _ = spell_case("feather_fall")
    for material in feather_state[f"{SRD}.entities"]["hero"]["spell_materials"]:
        material = deepcopy(material)
        material["id"] = "feather_" + material["id"]
        state[f"{SRD}.entities"]["hero"]["spell_materials"].append(material)
        state[f"{SRD}.entities"]["hero"]["inventory"].append(material["inventory_item"])
    return state, data, params


def _admit(state, *, guard=False, orb=True):
    for subject, anchored, grabbable in (
        ("hero", True, False),
        ("guard", guard, False),
        ("orb", orb, orb),
    ):
        _run(
            "record_srd_reverse_gravity_anchoring",
            state,
            subject=subject,
            anchored=anchored,
            grabbable=grabbable,
        )


def _source(state):
    return state[f"{SRD}.state"]["reverse_gravity_sources"][-1]


@pytest.mark.parametrize("face,up", [(1, True), (20, False)])
def test_actual_fixed_object_reach_dexterity_save_and_no_receiver_budget(
    gravity_scope, monkeypatch, face, up
):
    assert validate_profiles()
    state, data, params = _case()
    _admit(state)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="authentic paid gravity"):
        _run(
            "move_srd_reverse_gravity_subject",
            state,
            subject="guard",
            destination=[5, 0, 100],
            plane="material",
        )
    assert state == before
    actual = _TravelDice([(20, face)])
    monkeypatch.setattr(dice, "_rng", actual)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    params.update(gravity_grab_actors=["guard"], gravity_grab_objects=["orb"])
    movement = state[f"{SRD}.entities"]["guard"].get("movement_remaining")
    paid = _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert _point(state, "guard") == [5, 0, 100 if up else 0]
    assert _point(state, "orb") == [8, 0, 0]
    row = next(r for r in _source(state)["subjects"] if r["subject"] == "guard")
    assert row["phase"] == ("hovering" if up else "grabbed")
    assert row["save_results"] == [face] and row["grabbed"] is (not up)
    assert state[f"{SRD}.entities"]["guard"].get("movement_remaining") == movement
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_7"] == 2
    assert actual.consumed == [(20, face)]
    assert "guard" in str(paid.result["result_card"]["details"])
    assert "" not in state[f"{SRD}.combat"]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Concurrent or unsettled"):
        _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert state == before
    if up:
        for name in PUBLIC_TOOLS:
            assert "lasting_state" not in {
                parameter["name"] for parameter in _runtime()[0]["tools"][name]["parameters"]
            }
        _run("end_srd_concentration_node", state, source_state="hero", target_state="hero")
        assert _source(state)["active"] is False
        assert _source(state)["return_pending"] is True
        assert state[f"{SRD}.state"]["has_reverse_gravity_sources"] is False
        assert _point(state, "guard") == [5, 0, 100]
        retired = deepcopy(_source(state))
        with pytest.raises(RulesetPreconditionRejected, match="not concentrating"):
            _run("end_srd_concentration_node", state, source_state="hero", target_state="hero")
        assert _source(state) == retired


def test_real_object_upward_motion_and_unsupported_object_return_are_explicit(gravity_scope):
    state, data, params = _case()
    _admit(state, guard=True, orb=False)
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert _point(state, "orb") == [8, 0, 100]
    object_row = next(r for r in _source(state)["subjects"] if r["subject"] == "orb")
    assert object_row["creature"] is None
    _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    assert _source(state)["active"] is False and _source(state)["return_pending"] is True
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Object downward"):
        _run("settle_srd_reverse_gravity_returns", state, source_id=_source(state)["id"])
    assert state == before


def test_actual_ceiling_collision_rejects_atomically_until_impact_receiver_exists(gravity_scope):
    state, data, params = _case()
    _admit(state)
    state[f"{SRD}.state"]["spatial_scene"]["geometry"].append(
        {
            "id": "ceiling",
            "map_id": "information",
            "active": True,
            "movement": "blocking",
            "visibility": "opaque",
            "occupies_cells": True,
            "volume": {"kind": "box", "min": [0, -5, 40], "max": [10, 5, 41]},
            "metadata": {"plane": "material"},
            "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
        }
    )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="impact receiver"):
        _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert state == before


def _paid_case(_spell):
    state, data, params = _case()
    # These are the actual initial world facts; the source owner later calculates all effects.
    for body in state[f"{SRD}.state"]["spatial_scene"]["entities"]:
        body["metadata"]["reverse_gravity_ground_anchor"] = body["id"] != "guard"
        body["metadata"]["reverse_gravity_grabbable"] = body["id"] == "orb"
    return state, data, params


@pytest.mark.parametrize("feather", [False, True], ids=["unprotected", "paid-feather"])
def test_paid_source_expiry_actual_downward_fall_reopen_retry_and_later_landing(
    gravity_scope, monkeypatch, tmp_path, feather
):
    monkeypatch.setattr(public, "_spell_case", _paid_case)
    actual = _TravelDice([] if feather else [(6, 1)] * 10)
    monkeypatch.setattr(dice, "_rng", actual)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / "gravity.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(
            owner, "reverse_gravity", target_overrides={"hp": 500, "max_hp": 500}
        )
        base = ActionCommand(
            campaign_id="casting",
            action_id="cast",
            idempotency_key="cast",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=params,
        )

        def act(identity, name, selected):
            command = replace(
                base,
                action_id=identity,
                idempotency_key=identity,
                expected_revision=owner.snapshot("casting").revision,
                tool_name=name,
                parameters=selected,
            )
            result = owner.execute_action(command)
            assert result.status is ActionStatus.COMMITTED, result.result
            saved = owner.snapshot("casting")
            assert owner.execute_action(command) == result and owner.snapshot("casting") == saved
            return result, saved

        _, hovering = act("cast", "cast_spell_outside_combat", params)
        assert _point(hovering.state, "guard") == [5, 0, 100]
        act("almost-minute", "advance_srd_lasting_time", {"elapsed_rounds": 9})
        _, ended = act("minute", "advance_srd_lasting_time", {"elapsed_rounds": 1})
        assert _source(ended.state)["return_pending"] is True
        assert ended.state[f"{SRD}.state"]["has_reverse_gravity_sources"] is False
        assert ended.state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
        opened, pending = act(
            "fall", "settle_srd_reverse_gravity_returns", {"source_id": _source(ended.state)["id"]}
        )
        action = opened.result["context"]["_pending_action_id"]
        assert pending.state[f"{SRD}.state"]["has_reverse_gravity_pending_returns"] is False
        assert pending.state[f"{SRD}.state"]["pending_srd_actions"][action]["kind"] == "scene_fall"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == pending
        if feather:
            _, _, feather_params = spell_case("feather_fall")
            feather_params.pop("target", None)
            feather_params["material_item_ids"] = [
                "feather_" + item for item in feather_params["material_item_ids"]
            ]
            feather_params.update(reaction_trigger_id=action, targets=["guard"])
            act("protect", "cast_srd_reaction_spell", feather_params)
        act("complete-fall", "complete_srd_pending_action", {"pending_action_id": action})
        if feather:
            act("descent", "advance_srd_lasting_time", {"elapsed_rounds": 2})
        final = owner.snapshot("casting")
        assert _point(final.state, "guard") == [5, 0, 0]
        assert final.state[f"{SRD}.entities"]["guard"]["hp"] == (500 if feather else 490)
        assert ("prone" in final.state[f"{SRD}.entities"]["guard"]["conditions"]) is (not feather)
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == final
