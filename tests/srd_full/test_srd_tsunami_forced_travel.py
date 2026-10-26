"""Actual paid sources preserve forced water exits without ending a different hover."""

import hashlib
from copy import deepcopy
from dataclasses import replace
from functools import lru_cache
from graphlib import TopologicalSorter
from pathlib import Path

import pytest
import yaml
from scripts import srd_reverse_gravity as gravity
from scripts import srd_tsunami as water_wall
from scripts import srd_tsunami_source_adoption as source_identity
from scripts import srd_whirlpool_pull as whirlpool
from scripts.srd_full.srd_courier_chronology import _emit
from scripts.srd_full.srd_divination_spells import _normalize
from scripts.srd_full.srd_source_relocation_node import bind_runtime_consumers as split_travel
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
from scripts.srd_full.srd_tsunami_completion import _effects
from scripts.srd_full.srd_tsunami_completion import bind_runtime as complete_exits
from scripts.srd_full.srd_tsunami_forced_travel import (
    HELD,
    LIVE,
    RECEIVERS,
    _strip_observation,
    bind_runtime_consumers,
)
from scripts.srd_full.srd_tsunami_travel import bind_runtime_consumers as observe_travel

from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_travel_telekinesis_fall as scope
from tests import test_srd_tsunami_source_adoption as source
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_npc_spell_casting import _prepare_mage
from tests.srd_full.test_srd_spell_result_cards import _casting_headers
from tests.srd_full.test_srd_spell_travel import SRD, _point, _TravelDice, dice

BASE = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-tsunami-forced-travel/fc14-environment-whirlpool-pull-runtime-v1.yaml")
)
BASE_SHA256 = "142335f1c3dae15551cd3830a82e0bc8c39c4fc9d87df2f49500b0aba48247f5"
FALL = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-tsunami-forced-travel/tsunami-travel-paid-parent.yaml")
)
FALL_SHA256 = "a3995448d28d96fa4e63a6806eab49f139832f4628713b4a61246ca9f616373c"


def _actual_fall_owners(text):
    """Borrow the true saved fall completion and only missing named descendants, unchanged."""
    encoded = FALL.read_bytes()
    assert hashlib.sha256(encoded).hexdigest() == FALL_SHA256
    available = _load_runtime(encoded)["tools"]
    existing = _load_runtime(text)["tools"]
    changed = {
        name: deepcopy(available[name])
        for name in ("begin_srd_scene_fall", "complete_srd_pending_action")
    }
    pending = list(changed)
    while pending:
        name = pending.pop()
        for effect in _effects(changed[name]["effects"]):
            child = effect.get("call")
            if child and child not in existing and child not in changed:
                changed[child] = deepcopy(available[child])
                pending.append(child)
    return _emit(text, changed)


@lru_cache
def _owners():
    """Compose actual paid leaves/receivers over the immutable proved Whirlpool graph."""
    encoded = BASE.read_bytes()
    assert hashlib.sha256(encoded).hexdigest() == BASE_SHA256
    text = _actual_fall_owners(encoded.decode().replace("\r\n", "\n"))
    text = split_travel(text)
    text = water_wall.extend_runtime(text)
    # Reuse actual Source factories needed for these paid/ending paths. The immutable
    # Water scope predates selected-effect Recovery; no Dispel path is claimed here.
    tools = _load_runtime(text)["tools"]
    changed = {
        name: owner
        for name, owner in source_identity._source_tools().items()
        if name != "dispel_srd_tsunami_owned_source"
    }
    source_identity._producer(tools, changed)
    _normalize(changed)
    text = _emit(text, changed)
    text = gravity.extend_runtime(text)
    text = water_wall.bind_runtime_consumers(text)
    text = gravity.bind_runtime_consumers(text)
    text = whirlpool.bind_runtime_consumers(text)
    text = observe_travel(text)
    return text, _load_runtime(text)


@lru_cache
def _runtime():
    text, _ = _owners()
    text = bind_runtime_consumers(text)
    text = complete_exits(text, water_wall.PUBLIC_TOOLS | gravity.PUBLIC_TOOLS)
    runtime = _load_runtime(text)
    runtime["definitions"]["spells"].update(deepcopy(water_wall.PROFILES | gravity.PROFILES))
    for name in (
        "cast_spell",
        "cast_spell_node",
        "cast_spell_outside_combat",
        "begin_srd_spell_cast",
        "begin_srd_spell_cast_internal",
        "begin_srd_spell_action",
    ):
        for parameter in runtime["tools"].get(name, {}).get("parameters", []):
            if parameter["name"] == "spell" and "enum_values" in parameter:
                parameter["enum_values"] = sorted(
                    set(parameter["enum_values"])
                    | water_wall.PROFILES.keys()
                    | gravity.PROFILES.keys()
                )
    # Select exact transitive owners only; every retained body is unchanged. The
    # two public casts still retain all branches/callees, not just this fixture's path.
    roots = {
        "prepare_srd_creature",
        "record_srd_water_body",
        "record_srd_reverse_gravity_anchoring",
        "begin_srd_spell_cast",
        "continue_srd_spell_cast",
        "cast_spell_outside_combat",
        "cast_spell",
        "end_concentration",
        "settle_srd_reverse_gravity_returns",
        "complete_srd_pending_action",
    }
    roots |= water_wall.PUBLIC_TOOLS | gravity.PUBLIC_TOOLS
    retained = set(roots)
    pending = list(roots)
    while pending:
        name = pending.pop()
        for effect in _effects(runtime["tools"][name]["effects"]):
            child = effect.get("call")
            if child and child not in retained:
                retained.add(child)
                pending.append(child)
    runtime["tools"] = {name: tool for name, tool in runtime["tools"].items() if name in retained}
    print("Actual forced-exit selected transitive owners", len(retained))
    return runtime, yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False).encode()


def test_final_source_receivers_keep_every_guard_native_node_and_receiving_suffix():
    text, original = _owners()
    encoded = bind_runtime_consumers(text)
    observed = _load_runtime(encoded)
    for name, (node_name, _qualifier, _receipt) in RECEIVERS.items():
        owner = deepcopy(observed["tools"][name])
        owner["effects"] = _strip_observation(owner["effects"])
        assert owner == original["tools"][name]
        assert observed["tools"][node_name] == original["tools"][node_name]
    for name, owner in original["tools"].items():
        if name not in RECEIVERS and name != "settle_srd_tsunami_exits_node":
            assert observed["tools"][name] == owner
    assert bind_runtime_consumers(encoded) == encoded
    graph = {
        name: {effect["call"] for effect in _effects(tool["effects"]) if "call" in effect}
        for name, tool in observed["tools"].items()
    }
    assert len(list(TopologicalSorter(graph).static_order())) >= len(observed["tools"])
    assert all(
        parameter["model_hidden"]
        for name in (HELD, LIVE)
        for parameter in observed["tools"][name]["parameters"]
    )


@pytest.fixture
def forced_scope(monkeypatch):
    monkeypatch.setattr(scope, "_runtime", _runtime)
    monkeypatch.setattr(
        scope,
        "EXTRA_PUBLIC_TOOLS",
        frozenset(water_wall.PUBLIC_TOOLS | gravity.PUBLIC_TOOLS | {"record_srd_water_body"}),
    )
    yield from scope.actual_fall_scope.__wrapped__(monkeypatch)


def _case(kind):
    state, data, parameters = source._case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    hero = next(body for body in scene["entities"] if body["id"] == "hero")
    hero["position"] = [-50, 0, 0]
    guard = next(body for body in scene["entities"] if body["id"] == "guard")
    # The inherited shared fixture changes its unrelated guard's HP. This body's
    # actual living actor is instead admitted through public creature preparation.
    guard["metadata"]["participant_ref"] = "water_guard"
    mage = deepcopy(guard)
    mage.update(id="mage", position=[70, 0, 0])
    mage["metadata"]["participant_ref"] = "mage"
    scene["entities"].append(mage)
    if kind == "whirlpool":
        guard["position"] = [22, 0, 5]
        parameters["tsunami_origin"] = [0, 0, 0]
        scene["geometry"].append(
            {
                "id": "pool",
                "map_id": scene["map"]["id"],
                "volume": {"kind": "box", "min": [20, -30, 0], "max": [80, 30, 40]},
                "active": True,
                "movement": "passable",
                "visibility": "clear",
                "occupies_cells": False,
                "metadata": {"plane": "material"},
                "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
            }
        )
    else:
        parameters["tsunami_height"] = 50
    return state, data, parameters


@pytest.mark.parametrize("kind", ["whirlpool", "gravity"])
def test_paid_forced_water_exit_reopen_and_retry_preserves_gravity_hover_or_real_fall(
    forced_scope, tmp_path, monkeypatch, kind
):
    monkeypatch.setattr(public, "_spell_case", lambda _spell: _case(kind))
    faces = [(10, 1)] * 6 + [(20, 20)]
    if kind == "gravity":
        faces += [(6, 2)] * 10
    actual = _TravelDice(faces)
    monkeypatch.setattr(dice, "_rng", actual)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / (kind + "-tsunami.db")
    receipts = []
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public._create_spell_campaign(owner, "tsunami")
        template = ActionCommand(
            campaign_id="casting",
            action_id="begin",
            idempotency_key="begin",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="begin_srd_spell_cast",
            parameters=_begin(parameters),
        )

        def act(label, tool, parameters):
            command = replace(
                template,
                action_id=label,
                idempotency_key=label,
                expected_revision=owner.snapshot("casting").revision,
                tool_name=tool,
                parameters=parameters,
            )
            result = owner.execute_action(command)
            assert result.status is ActionStatus.COMMITTED, result.result
            snapshot = owner.snapshot("casting")
            assert owner.execute_action(command) == result
            assert owner.snapshot("casting") == snapshot
            receipts.append((command, result))
            return result, snapshot

        act(
            "prepare-guard",
            "prepare_srd_creature",
            {
                "creature_id": "water_guard",
                "name": "Guard",
                "source": "Synthetic stat block: 100 HP, AC 12, Speed 30, STR save +0.",
                "armor_class": 12,
                "hit_points": 100,
                "speed": 30,
                "initiative_modifier": 0,
                "size_rank": 2,
                "strength_save": 0,
            },
        )
        spell = "control_water" if kind == "whirlpool" else "reverse_gravity"
        materials = [
            {
                "id": f"mage_material_{index}",
                "inventory_item": f"mage_material_item_{index}",
                "kind": "specific",
                "components": [part["id"]],
                "quantity": part["quantity"],
                "value_cp": part["minimum_value_cp"],
            }
            for index, part in enumerate(_casting_headers()[spell]["material_parts"])
        ]
        prepared, _ = _prepare_mage(
            owner,
            [{"spell": spell, "uses_per_day": 1}],
            strength_save=0,
            inventory=[material["inventory_item"] for material in materials],
            spell_materials=materials,
        )
        assert prepared.status is ActionStatus.COMMITTED, prepared.result
        if kind == "whirlpool":
            act("water-fact", "record_srd_water_body", {"geometry_id": "pool", "flow": "standing"})
        else:
            for subject, anchored in (("hero", True), ("water_guard", False), ("orb", True)):
                act(
                    "anchored-" + subject,
                    "record_srd_reverse_gravity_anchoring",
                    {
                        "subject": subject,
                        "anchored": anchored,
                        "grabbable": False,
                    },
                )
        act("begin", "begin_srd_spell_cast", _begin(parameters))
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
        _, cast = act("tsunami", "cast_spell_outside_combat", parameters)
        wave = cast.state[f"{SRD}.state"]["tsunami_sources"][-1]
        initial_hp = cast.state[f"{SRD}.entities"]["water_guard"]["hp"]
        choices = {
            "caster_state": "mage",
            "spell": spell,
            "all_targets_willing": True,
            "material_item_ids": [material["id"] for material in materials],
            "verbal_component_available": True,
            "somatic_component_available": True,
            "material_component_available": True,
        }
        if kind == "whirlpool":
            choices.update(
                environment_origin=[50, 0, 0],
                environment_water_geometry_id="pool",
                environment_water_side=50,
                environment_water_effect="whirlpool",
                environment_whirlpool_top_diameter=50,
            )
        else:
            choices.update(
                gravity_origin=[5, 0, 0], gravity_grab_actors=[], gravity_grab_objects=[]
            )
        moved, saved = act("forced", "cast_spell_outside_combat", choices)
        world = saved.state[f"{SRD}.state"]
        assert world.get("tsunami_exits"), {
            "position": _point(saved.state, "guard"),
            "wave": world["tsunami_sources"],
            "motion": world.get("whirlpool_pending_motion"),
        }
        receipt = next(row for row in world["tsunami_exits"] if row["body_id"] == "guard")
        assert receipt["source_id"] == wave["id"] and receipt["sequence"] == wave["sequence"]
        assert receipt["position"] == _point(saved.state, "guard")
        assert saved.state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"][spell] == 0
        assert saved.state[f"{SRD}.entities"]["water_guard"]["hp"] == initial_hp
        if kind == "gravity":
            assert _point(saved.state, "guard") == [5, 0, 100]
            assert receipt["pending"] is False
            assert not world.get("pending_srd_action_stack"), world.get("pending_srd_actions")
            current = world["reverse_gravity_sources"][-1]
            assert current["active"]
            assert (
                next(row for row in current["subjects"] if row["body_id"] == "guard")["phase"]
                == "hovering"
            )
        else:
            assert _point(saved.state, "guard") == [32, 0, 5]
            trigger = world["pending_srd_action_stack"][-1]
            assert world["pending_srd_actions"][trigger]["kind"] == "scene_fall"
            assert moved.result["context"]["_tsunami_exit_action_id"] == trigger
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        if kind == "gravity":
            # Retire the separate wave first; return-through-active-water physics is not claimed.
            act("end-wave", "end_concentration", {"caster_state": "hero"})
            act("end-gravity", "end_concentration", {"caster_state": "mage"})
            ended = owner.snapshot("casting")
            assert not ended.state[f"{SRD}.state"]["reverse_gravity_sources"][-1]["active"]
            assert _point(ended.state, "guard") == [5, 0, 100]
            _, falling = act(
                "gravity-return",
                "settle_srd_reverse_gravity_returns",
                {"source_id": current["id"]},
            )
            trigger = falling.state[f"{SRD}.state"]["pending_srd_action_stack"][-1]
        _, landed = act("fall", "complete_srd_pending_action", {"pending_action_id": trigger})
        assert _point(landed.state, "guard") == ([5, 0, 0] if kind == "gravity" else [32, 0, 0])
        assert landed.state[f"{SRD}.entities"]["water_guard"]["hp"] == initial_hp - (
            20 if kind == "gravity" else 0
        )
        assert landed.state[f"{SRD}.state"]["pending_srd_action_stack"] == []
        for command, result in receipts:
            assert owner.execute_action(command) == result
            assert owner.snapshot("casting") == landed
        assert actual.consumed == faces
