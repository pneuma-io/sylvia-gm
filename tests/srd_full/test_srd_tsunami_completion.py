"""Completed paid source ending opens one real falling window without a manual drain."""

from copy import deepcopy
from dataclasses import replace
from functools import lru_cache
from graphlib import TopologicalSorter

import pytest
import yaml
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
from scripts.srd_full.srd_tsunami_completion import DRAIN, RESULT, _effects, bind_runtime

from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_travel_telekinesis_fall as scope
from tests import test_srd_tsunami as tsunami
from tests import test_srd_tsunami_source_adoption as source
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_npc_spell_casting import _prepare_mage
from tests.srd_full.test_srd_spell_travel import SRD, _point, _TravelDice, dice

PUBLIC = frozenset(
    tsunami.PUBLIC_TOOLS
    | {
        "dismiss_srd_spell",
        "advance_srd_lasting_time",
        "cast_srd_reaction_spell",
        "begin_combat_turn",
        "end_combat_turn",
    }
)


@lru_cache
def _runtime():
    _, encoded = source._runtime()
    runtime = _load_runtime(bind_runtime(encoded.decode(), PUBLIC))
    return runtime, yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False).encode()


def test_completion_graph_preserves_receivers_and_actual_owner_bodies():
    before, _ = source._runtime()
    after, encoded = _runtime()
    tools = after["tools"]
    graph = {
        name: {effect["call"] for effect in _effects(tool["effects"]) if "call" in effect}
        for name, tool in tools.items()
    }
    assert len(list(TopologicalSorter(graph).static_order())) >= len(tools)
    for name in (
        "cast_spell",
        "cast_spell_outside_combat",
        "dismiss_srd_spell",
        "swim_srd_tsunami",
        "advance_srd_lasting_time",
        "complete_srd_pending_action",
    ):
        assert tools[name]["effects"][:-1] == before["tools"][name]["effects"]
        completion = tools[name]["effects"][-1]
        assert "has_tsunami_pending_exits == true" in completion["condition"]
        assert "len(lasting_state.pending_srd_action_stack) == 0" in completion["condition"]
        assert completion["on_true"][0]["export_context"] == {RESULT: "_pending_action_id"}
        assert tools[name]["metadata"] == before["tools"][name]["metadata"]
    for name in (
        DRAIN,
        "begin_srd_scene_fall",
        "resolve_srd_scene_fall_event",
        "apply_srd_qualifying_damage",
        "move_srd_scene_creature",
        "move_srd_scene_creature_node",
        "move_srd_travel_subject",
        "carry_srd_tsunami_creature",
    ):
        assert tools[name] == before["tools"][name]
    assert _load_runtime(bind_runtime(encoded.decode(), PUBLIC)) == after


@pytest.fixture
def completion_scope(monkeypatch):
    monkeypatch.setattr(scope, "_runtime", _runtime)
    monkeypatch.setattr(scope, "EXTRA_PUBLIC_TOOLS", PUBLIC)
    yield from scope.actual_fall_scope.__wrapped__(monkeypatch)


@pytest.mark.parametrize("feather", [False, True])
def test_paid_source_ending_automatically_opens_saved_fall_and_feather_window(
    completion_scope, monkeypatch, tmp_path, feather
):
    def elevated_case(_spell="tsunami"):
        state, data, params = source._case()
        scene = state[f"{SRD}.state"]["spatial_scene"]
        next(body for body in scene["entities"] if body["id"] == "guard")["position"] = [
            5,
            0,
            20,
        ]
        scene["geometry"].append(
            {
                "id": "guard-platform",
                "map_id": "information",
                "volume": {"kind": "box", "min": [4.4, -1, 19], "max": [5.6, 1, 20]},
                "active": True,
                "movement": "blocking",
                "occupies_cells": True,
                "visibility": "opaque",
                "metadata": {"plane": "material", "ground": True},
                "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
            }
        )
        mage = deepcopy(next(body for body in scene["entities"] if body["id"] == "hero"))
        mage.update(id="mage", position=[-20, 10, 0])
        mage["metadata"]["participant_ref"] = "mage"
        scene["entities"].append(mage)
        return state, data, params

    monkeypatch.setattr(public, "_spell_case", elevated_case)
    faces = [(10, 1)] * 6 + [(20, 20), (20, 20)]
    if not feather:
        faces += [(6, 2), (6, 3)]
    actual = _TravelDice(faces)
    monkeypatch.setattr(dice, "_rng", actual)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / "tsunami-completed-exit.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(
            owner, "tsunami", target_overrides={"hp": 500, "max_hp": 500}
        )
        base = ActionCommand(
            campaign_id="casting",
            action_id="begin",
            idempotency_key="begin",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="begin_srd_spell_cast",
            parameters=_begin(params),
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

        if feather:
            feather_state, _, _ = tsunami.spell_case("feather_fall")
            saved_materials = feather_state[f"{SRD}.entities"]["hero"]["spell_materials"]
            materials = [
                {
                    key: list(item[key]) if key == "components" else item[key]
                    for key in (
                        "id",
                        "inventory_item",
                        "kind",
                        "components",
                        "quantity",
                        "value_cp",
                    )
                }
                for item in saved_materials
            ]
            prepared, _ = _prepare_mage(
                owner,
                [{"spell": "feather_fall", "uses_per_day": 1}],
                inventory=[item["inventory_item"] for item in materials],
                spell_materials=materials,
            )
            assert prepared.status is ActionStatus.COMMITTED, prepared.result
        act("begin", "begin_srd_spell_cast", _begin(params))
        act(
            "minute",
            "continue_srd_spell_cast",
            {
                "caster_state": "hero",
                "elapsed_rounds": 10,
                "uninterrupted": True,
                "verbal_component_available": True,
                "somatic_component_available": True,
                "material_component_available": True,
            },
        )
        _, cast = act("cast", "cast_spell_outside_combat", params)
        water = deepcopy(tsunami._source(cast.state))
        act(
            "swim-off-platform",
            "swim_srd_tsunami",
            {"actor_state": "guard", "source_id": water["id"], "destination": [10, 0, 20]},
        )
        if feather:
            ending, ended = act(
                "dismiss",
                "dismiss_srd_spell",
                {"caster_state": "hero", "cast_id": water["cast_id"]},
            )
        else:
            ending, ended = act("expiry", "advance_srd_lasting_time", {"elapsed_rounds": 6})
        trigger = ending.result["context"][RESULT]
        assert trigger == ended.state[f"{SRD}.state"]["pending_srd_action_stack"][-1]
        assert ended.state[f"{SRD}.state"]["pending_srd_actions"][trigger]["kind"] == "scene_fall"
        assert not ended.state[f"{SRD}.state"]["has_tsunami_pending_exits"]
        assert not tsunami._source(ended.state)["active"]
        assert ended.state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
        assert _point(ended.state, "guard") == [10, 0, 20]
        assert ended.state[f"{SRD}.entities"]["guard"]["hp"] == 497
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == ended
        if feather:
            _, _, choices = tsunami.spell_case("feather_fall")
            choices.pop("caster")
            choices.pop("slot_resource")
            choices.update(caster_state="mage", targets=["guard"], reaction_trigger_id=trigger)
            _, protected = act("paid-feather", "cast_srd_reaction_spell", choices)
            assert (
                protected.state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"]["feather_fall"]
                == 0
            )
            assert protected.state[f"{SRD}.entities"]["mage"]["reaction_available"] is False
        completed, landed = act(
            "complete-fall", "complete_srd_pending_action", {"pending_action_id": trigger}
        )
        assert completed.result["result_card"]["title"] == "Falling creatures"
        if feather:
            assert landed.state[f"{SRD}.entities"]["guard"]["scene_fall"]["status"] == "descending"
            assert _point(landed.state, "guard") == [10, 0, 20]
            _, landed = act("land", "advance_srd_lasting_time", {"elapsed_rounds": 1})
            assert landed.state[f"{SRD}.entities"]["guard"]["hp"] == 497
        else:
            assert landed.state[f"{SRD}.entities"]["guard"]["hp"] == 492
            assert "prone" in landed.state[f"{SRD}.entities"]["guard"]["conditions"]
        assert _point(landed.state, "guard") == [10, 0, 0]
        assert landed.state[f"{SRD}.state"]["pending_srd_action_stack"] == []
        assert actual.consumed == faces
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == landed
