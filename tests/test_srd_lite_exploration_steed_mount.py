"""Run selected genuine Steed owners without loading a package or its scene closure."""

import json
import os
import re
from copy import deepcopy
from pathlib import Path

import pytest
from scripts.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_lite_exploration_steed_mount import (
    CHOICES,
    COST,
    DISMOUNT,
    IDS,
    MOUNT,
    MOUNT_FIELD,
    OWNER,
    PLACEMENTS,
    RIDER_FIELD,
    SCOPE,
    SPEED,
    bind_exploration_steed_mount,
)
from scripts.srd_timed_actor_defenses import _require

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import (
    AuthorityMemoryStateBridge,
    _runtime_parameter_schema,
)
from sylvia_gm.application.result_cards import project_result_card
from sylvia_gm.runtime import RulesetRuntime
from sylvia_gm.runtime._tool_executor import execute_tool
from sylvia_gm.runtime._types import ExecutionBudget

NAMESPACE = "test.exploration.steed.mount"


def _lists(rows):
    if isinstance(rows, dict):
        rows = [rows]
    yield rows
    for row in rows:
        for key in ("on_true", "on_false"):
            if key in row:
                yield from _lists(row[key])
        if "for_each" in row:
            yield from _lists(row["for_each"]["do"])


def _calls(rows):
    return {row["call"] for group in _lists(rows) for row in group if "call" in row}


def _fragments(variable):
    """Read only an explicitly selected bounded export of public synthetic owners."""
    path = Path(os.environ[variable])
    selected = json.loads(path.read_text(encoding="utf-8-sig"))["tools"]
    assert len(selected) <= 128, "Use a bounded owner export, never the whole runtime."
    return selected


@pytest.fixture(scope="module")
def owners():
    selected = _fragments("SRD_LITE_STEED_MOUNT_OWNERS")
    selected.update(_fragments("SRD_LITE_STEED_MOUNT_SPEED"))
    selected.update(_fragments("SRD_LITE_STEED_MOUNT_PROJECTIONS"))
    return selected


@pytest.fixture(scope="module")
def bound(owners):
    data = {"tools": deepcopy(owners)}
    record = {
        "tools": [
            {
                "name": name,
                "input_schema": _tool_schema(data["tools"][name], tool_name=name),
            }
            for name in (MOUNT, DISMOUNT)
        ]
    }
    unchanged = {name: deepcopy(data["tools"][name]) for name in (MOUNT, DISMOUNT)}
    assert bind_exploration_steed_mount(data, record) == [MOUNT, DISMOUNT, COST, SPEED]
    for name in (MOUNT, DISMOUNT):
        original = unchanged[name]
        effect = data["tools"][name]["effects"][0]
        assert effect["condition"] == SCOPE
        expected = original["effects"]
        if name == DISMOUNT:
            expected = [
                _require("destination != null", "Combat dismounting needs its actual destination."),
                *expected,
            ]
        assert effect["on_false"] == expected
        assert data["tools"][name].get("pre_conditions") == original.get("pre_conditions")
        assert data["tools"][name].get("outputs") == original.get("outputs")
        assert data["tools"][name].get("ui") == original.get("ui")
        assert OWNER in _calls(effect["on_true"])
        assert "srd_information_scene_entity" not in _calls(effect["on_true"])
        assert "move_srd_phantom_steed_rider" not in _calls(effect["on_true"])
        for parameter in original["parameters"]:
            actual = next(
                p for p in data["tools"][name]["parameters"] if p["name"] == parameter["name"]
            )
            if name == DISMOUNT and parameter["name"] == "destination":
                parameter = {**parameter, "required": False, "default": None}
            assert actual == parameter
    with pytest.raises(ValueError, match="already bound"):
        bind_exploration_steed_mount(data, record)
    return data, record


def _runtime(data):
    """Select new exploration arms; unchanged native combat arms retain existing paid proofs."""
    closure, pending = {}, [MOUNT, DISMOUNT]
    while pending:
        name = pending.pop()
        if name in closure:
            continue
        closure[name] = deepcopy(data["tools"][name])
        if name in (MOUNT, DISMOUNT):
            closure[name]["effects"][0]["on_false"] = [
                _require("false", "Use the existing native combat mount proofs.")
            ]
        if name == "end_concentration":
            # No selected case ends concentration. Fail if this closed native owner is reached;
            # its existing lifecycle proof is retained rather than reloading its whole closure.
            closure[name]["effects"] = [
                _require("false", "Use the existing native concentration lifecycle proofs.")
            ]
        if name == "refresh_srd_movement_budget":
            # Native projection calls this only for an active combat turn. Preserve that gate
            # and fail if these exploration probes reach the previously qualified combat arm.
            effect = closure[name]["effects"]
            if isinstance(effect, list):
                assert len(effect) == 1
                effect = effect[0]
            assert effect["condition"] == (
                "combat.turn_active == true and combat.active_actor == _actor_state_id"
            )
            closure[name]["effects"] = [
                {
                    "condition": effect["condition"],
                    "on_true": [_require("false", "Use the existing combat Speed budget proofs.")],
                }
            ]
        pending.extend(_calls(closure[name]["effects"]))
    return RulesetRuntime(ruleset_dict={"tools": closure})


@pytest.fixture(scope="module")
def parsed(bound):
    return _runtime(bound[0])


def _bridge(*, cached_scene=None, mounted=False, ally=False):
    hero = {
        "name": "Caster",
        "hp": 49,
        "is_alive": True,
        "size_rank": 2,
        "speed": 30,
        "exhaustion_level": 0,
        "conditions": [],
        "created_steed_id": "paid-summon",
        "resources": {"spell_slot_2": 1},
        MOUNT_FIELD: "paid-summon" if mounted and not ally else None,
        "travel_location": {
            "place": "beside the established oak",
            "plane": "material",
            "native_anchor": False,
            "via_spell": "misty_step",
        },
    }
    steed = {
        "name": "Otherworldly Steed",
        "hp": 25,
        "is_alive": True,
        "size_rank": 3,
        "created_spell_id": "find_steed",
        "created_spell_level": 2,
        "created_active": True,
        "created_caster_state_id": "hero",
        RIDER_FIELD: ("ally" if ally else "hero") if mounted else None,
        "inventory": [],
        "conditions": [],
        "resources": {"healing_touch": 1},
    }
    friend = {
        **deepcopy(hero),
        "name": "Friendly ally",
        "created_steed_id": None,
        MOUNT_FIELD: "paid-summon" if mounted and ally else None,
        "attitudes": {"hero": "friendly"},
    }
    lasting = {"lasting_tick": 10, "pending_srd_action_stack": None}
    if cached_scene is not None:
        lasting["spatial_scene"] = deepcopy(cached_scene)
    return AuthorityMemoryStateBridge(
        NAMESPACE,
        {
            NAMESPACE + ".state": lasting,
            NAMESPACE + ".combat": {
                "encounter_active": False,
                "turn_active": False,
                "pending_action": None,
            },
            NAMESPACE + ".entities": {"hero": hero, "paid-summon": steed, "ally": friend},
        },
    )


def _facts(**changes):
    return {
        "rider_state": "hero",
        "steed_state": "paid-summon",
        "mount_placement": "beside the established oak",
        "mount_current_plane": "material",
        "mount_same_plane": True,
        "mount_distance_feet": 5,
        "mount_clear_path": True,
        "mount_space_unoccupied": True,
        **changes,
    }


def _run(parsed, bridge, name, parameters, calls=None):
    def nested(child, params, parent):
        if calls is not None:
            calls.append(child)
        return execute_tool(
            child,
            params,
            parsed.tools,
            bridge,
            parsed.ruleset_config,
            parsed.definitions,
            combat_state=parent.combat_state,
            tool_executor=nested,
            budget=parent.budget,
        )

    return execute_tool(
        name,
        parameters,
        parsed.tools,
        bridge,
        parsed.ruleset_config,
        parsed.definitions,
        combat_state=bridge.combat_state,
        tool_executor=nested,
        budget=ExecutionBudget(),
    )


def _card(data, name, result, parameters):
    card = project_result_card(
        data["tools"][name]["metadata"]["result_card"],
        parameters=parameters,
        public_context=result.context,
    )
    assert card
    return {row["label"]: row["value"] for row in card["summary"]}


def _unpaid_unchanged(bridge):
    assert bridge.get_entity("hero")["resources"] == {"spell_slot_2": 1}
    assert bridge.get_entity("paid-summon")["resources"] == {"healing_touch": 1}
    for actor in ("hero", "paid-summon", "ally"):
        assert not any(key.startswith("scene_movement_") for key in bridge.get_entity(actor))
        assert "scene_dashes_taken" not in bridge.get_entity(actor)
    assert "movement_spent" not in bridge.combat_state
    assert "movement_remaining" not in bridge.combat_state


def test_schema_and_new_expression_limits(bound, parsed):
    data, record = bound
    for declaration in record["tools"]:
        name = declaration["name"]
        assert _runtime_parameter_schema(parsed.tools[name]) == declaration["input_schema"]
        assert {choice[0] for choice in CHOICES} <= set(declaration["input_schema"]["properties"])
        if name == DISMOUNT:
            assert "destination" not in declaration["input_schema"]["required"]
    for name in (MOUNT, DISMOUNT, COST, SPEED):
        effects = data["tools"][name]["effects"]
        if name in (MOUNT, DISMOUNT):
            effects = effects[0]["on_true"]
        for group in _lists(effects):
            for row in group:
                for expression in (
                    row.get("condition"),
                    row.get("require", {}).get("condition"),
                    row.get("set", {}).get("value"),
                ):
                    if isinstance(expression, str):
                        assert len(expression) <= 500
                        assert all(
                            len(value) <= 200 for value in re.findall("'([^']*)'", expression)
                        )
                if "for_each" in row:
                    assert "." not in row["for_each"]["collection"]


@pytest.mark.parametrize("cached_scene", [None, {"stale_coordinate_fixture": True}])
def test_mount_and_dismount_without_map_have_durable_truthful_feedback(bound, parsed, cached_scene):
    data, _ = bound
    bridge = _bridge(cached_scene=cached_scene)
    rng = dice._rng.getstate()
    calls = []
    parameters = _facts()
    mounted = _run(parsed, bridge, MOUNT, parameters, calls)
    assert mounted.success, mounted.error
    assert bridge.get_entity("hero")[MOUNT_FIELD] == "paid-summon"
    assert bridge.get_entity("paid-summon")[RIDER_FIELD] == "hero"
    saved = bridge.get_entity("state")[PLACEMENTS]["paid-summon"]
    assert saved == {
        "steed": "paid-summon",
        "caster": "hero",
        "rider": "hero",
        "steed_placement": parameters["mount_placement"],
        "rider_placement": parameters["mount_placement"],
        "plane": "material",
        "mounted": True,
        "pending": True,
        "tick": 10,
    }
    assert bridge.get_entity("state")[IDS] == ["paid-summon"]
    card = _card(data, MOUNT, mounted, parameters)
    assert card == {
        "Rider": "Caster",
        "Steed": "Otherworldly Steed",
        "Mounted": True,
        "Movement cost (ft)": 15,
        "Destination": parameters["mount_placement"],
    }
    parameters = _facts(
        dismount_placement="on the clear ground beside the oak",
        dismount_ground_supported=True,
    )
    dismounted = _run(parsed, bridge, DISMOUNT, parameters, calls)
    assert dismounted.success, dismounted.error
    assert bridge.get_entity("hero")[MOUNT_FIELD] is None
    assert bridge.get_entity("paid-summon")[RIDER_FIELD] is None
    saved = bridge.get_entity("state")[PLACEMENTS]["paid-summon"]
    assert saved["rider"] == "hero" and saved["mounted"] is False and saved["pending"] is True
    assert saved["rider_placement"] == parameters["dismount_placement"]
    assert bridge.get_entity("state")[IDS] == ["paid-summon"]
    card = _card(data, DISMOUNT, dismounted, parameters)
    assert card["Mounted"] is False and card["Movement cost (ft)"] == 15
    assert card["Destination"] == parameters["dismount_placement"]
    assert bridge.get_entity("state").get("spatial_scene") == cached_scene
    assert dice._rng.getstate() == rng
    assert "resolve_srd_spirit_guardians_speed" not in calls
    assert "refresh_srd_movement_budget" not in calls
    _unpaid_unchanged(bridge)


def test_actual_friendly_ally_mounts_without_gaining_caster_turn_control(parsed):
    bridge = _bridge(ally=True)
    result = _run(parsed, bridge, MOUNT, _facts(rider_state="ally"))
    assert result.success, result.error
    assert bridge.get_entity("ally")[MOUNT_FIELD] == "paid-summon"
    assert bridge.get_entity("paid-summon")[RIDER_FIELD] == "ally"
    assert bridge.get_entity("state")[PLACEMENTS]["paid-summon"]["caster"] == "hero"
    assert bridge.combat_state["turn_active"] is False
    _unpaid_unchanged(bridge)


@pytest.mark.parametrize(
    "change,error",
    [
        ({"mount_distance_feet": 5.01}, "within five feet"),
        ({"mount_same_plane": False}, "same-plane"),
        ({"mount_current_plane": "astral"}, "saved current plane"),
        ({"mount_clear_path": False}, "clear"),
        ({"mount_space_unoccupied": False}, "unoccupied"),
        ({"mount_placement": ""}, "placement and plane"),
    ],
)
def test_incomplete_or_wrong_current_facts_fail_before_mount_links(parsed, change, error):
    bridge = _bridge()
    result = _run(parsed, bridge, MOUNT, _facts(**change))
    assert not result.success and error in result.error
    assert bridge.get_entity("hero")[MOUNT_FIELD] is None
    assert bridge.get_entity("paid-summon")[RIDER_FIELD] is None
    assert PLACEMENTS not in bridge.get_entity("state")
    _unpaid_unchanged(bridge)


@pytest.mark.parametrize(
    "actor,field,value,error",
    [
        ("hero", "created_steed_id", "different-source", "actual active Otherworldly Steed"),
        ("paid-summon", "created_active", False, "actual active Otherworldly Steed"),
        ("paid-summon", "created_spell_level", 1, "actual saved spell level"),
        ("hero", "size_rank", 3, "smaller"),
        ("hero", "conditions", ["incapacitated"], "Incapacitated"),
        ("hero", "meld_stone_source_id", "actual-stone", "merged stone"),
        ("hero", "earthquake_buried", True, "actual rubble"),
        ("hero", "phantom_steed_mount", "another-horse", "both be unmounted"),
        ("paid-summon", RIDER_FIELD, "someone-else", "both be unmounted"),
    ],
)
def test_actual_source_rider_and_mobility_restrictions_remain(parsed, actor, field, value, error):
    bridge = _bridge()
    bridge.write_field(actor, field, value)
    result = _run(parsed, bridge, MOUNT, _facts())
    assert not result.success and error in result.error
    assert bridge.get_entity("hero")[MOUNT_FIELD] is None
    assert PLACEMENTS not in bridge.get_entity("state")
    _unpaid_unchanged(bridge)


def test_unfriendly_ally_and_nonreciprocal_dismount_fail(parsed):
    bridge = _bridge(ally=True)
    bridge.write_field("ally", "attitudes.hero", "hostile")
    result = _run(parsed, bridge, MOUNT, _facts(rider_state="ally"))
    assert not result.success and "actual friendly ally" in result.error
    bridge = _bridge(mounted=True)
    bridge.write_field("paid-summon", RIDER_FIELD, "ally")
    result = _run(
        parsed,
        bridge,
        DISMOUNT,
        _facts(dismount_placement="clear ground", dismount_ground_supported=True),
    )
    assert not result.success and "reciprocal saved rider" in result.error
    assert bridge.get_entity("hero")[MOUNT_FIELD] == "paid-summon"


def test_dismount_requires_supported_fictional_ground(parsed):
    bridge = _bridge(mounted=True)
    result = _run(parsed, bridge, DISMOUNT, _facts(dismount_placement="open air"))
    assert not result.success and "airborne dismounts remain unsupported" in result.error
    assert bridge.get_entity("hero")[MOUNT_FIELD] == "paid-summon"
    assert bridge.get_entity("paid-summon")[RIDER_FIELD] == "hero"
    assert PLACEMENTS not in bridge.get_entity("state")


@pytest.mark.parametrize("inside,cost", [(False, 15), (True, 7)])
def test_guardian_current_rider_fact_honors_unrelated_source(parsed, inside, cost):
    bridge = _bridge()
    bridge.write_field("state", "has_spirit_guardians", True)
    result = _run(parsed, bridge, MOUNT, _facts(mount_guardian_speed_halved=inside))
    assert result.success, result.error
    assert result.context["_ps_mount_cost"] == cost
    _unpaid_unchanged(bridge)


def test_freedom_of_movement_retains_native_guardian_speed_exception(parsed):
    bridge = _bridge()
    bridge.write_field("state", "has_spirit_guardians", True)
    bridge.write_field("hero", "spell_freedom_of_movement", True)
    result = _run(parsed, bridge, MOUNT, _facts(mount_guardian_speed_halved=True))
    assert result.success, result.error
    assert result.context["_ps_mount_cost"] == 15
    _unpaid_unchanged(bridge)


@pytest.mark.parametrize(
    "kind,magnitude,cost",
    [("speed_bonus", 10, 20), ("speed_reduction", 10, 10), ("speed_halved", 0, 7)],
)
def test_native_current_modifier_projection_sets_actual_mount_cost(parsed, kind, magnitude, cost):
    bridge = _bridge()
    bridge.write_field(
        "state", "turn_effects", [{"kind": kind, "target": "hero", "magnitude": magnitude}]
    )
    result = _run(parsed, bridge, MOUNT, _facts())
    assert result.success, result.error
    assert result.context["_ps_mount_cost"] == cost
    _unpaid_unchanged(bridge)


def test_fresh_projected_stun_cannot_create_mount_links(parsed):
    bridge = _bridge()
    bridge.write_field("state", "turn_effects", [{"kind": "stunned", "target": "hero"}])
    result = _run(parsed, bridge, MOUNT, _facts())
    assert not result.success and "Incapacitated" in result.error
    assert "stunned" in bridge.get_entity("hero")["conditions"]
    assert bridge.get_entity("hero")[MOUNT_FIELD] is None
    assert PLACEMENTS not in bridge.get_entity("state")
    _unpaid_unchanged(bridge)


def test_cached_exploration_budget_neither_blocks_nor_changes_mounting(parsed):
    bridge = _bridge()
    counters = {
        "scene_movement_tick": -1,
        "scene_movement_spent": 999,
        "scene_dashes_taken": 17,
    }
    for field, value in counters.items():
        bridge.write_field("hero", field, value)
    result = _run(parsed, bridge, MOUNT, _facts())
    assert result.success, result.error
    assert result.context["_ps_mount_cost"] == 15
    assert {field: bridge.get_entity("hero")[field] for field in counters} == counters
    assert "movement_remaining" not in bridge.combat_state


def test_missing_guardian_current_rider_fact_fails_clearly(parsed):
    bridge = _bridge()
    bridge.write_field("state", "has_spirit_guardians", True)
    result = _run(parsed, bridge, MOUNT, _facts())
    assert not result.success and "Spirit Guardians" in result.error
    assert bridge.get_entity("hero")[MOUNT_FIELD] is None
    assert PLACEMENTS not in bridge.get_entity("state")
    _unpaid_unchanged(bridge)


def test_current_speed_and_zero_speed_use_original_half_speed_rule(parsed):
    bridge = _bridge()
    bridge.write_field("hero", "exhaustion_level", 2)
    result = _run(parsed, bridge, MOUNT, _facts())
    assert result.success, result.error
    assert result.context["_ps_mount_cost"] == 10
    bridge = _bridge()
    bridge.write_field("hero", "speed", 0)
    result = _run(parsed, bridge, MOUNT, _facts())
    assert not result.success and "enough Speed" in result.error
    assert bridge.get_entity("hero")[MOUNT_FIELD] is None


def test_existing_pending_plane_and_pending_window_cannot_be_overridden(parsed):
    bridge = _bridge()
    bridge.write_field("state", PLACEMENTS, {"paid-summon": {"pending": True, "plane": "astral"}})
    result = _run(parsed, bridge, MOUNT, _facts())
    assert not result.success and "pending plane" in result.error
    bridge = _bridge()
    bridge.write_field("state", "pending_srd_action_stack", ["actual-pending-id"])
    result = _run(parsed, bridge, MOUNT, _facts())
    assert not result.success and "pending action" in result.error
    assert bridge.get_entity("hero")[MOUNT_FIELD] is None
    bridge = _bridge()
    bridge.combat_state["pending_action"] = {"id": "actual-pending-action"}
    result = _run(parsed, bridge, MOUNT, _facts())
    assert not result.success and "pending action" in result.error


def test_historical_plane_does_not_pin_current_native_arrivals(parsed):
    bridge = _bridge()
    bridge.write_field(
        "state", PLACEMENTS, {"paid-summon": {"pending": False, "plane": "astral", "rider": "hero"}}
    )
    result = _run(parsed, bridge, MOUNT, _facts())
    assert result.success, result.error
    assert bridge.get_entity("state")[PLACEMENTS]["paid-summon"]["plane"] == "material"
    bridge = _bridge()
    bridge.write_field(
        "state", PLACEMENTS, {"paid-summon": {"pending": True, "plane": "astral", "rider": "hero"}}
    )
    bridge.write_field("paid-summon", "find_path_current_plane", "material")
    result = _run(parsed, bridge, MOUNT, _facts())
    assert result.success, result.error
    assert bridge.get_entity("state")[PLACEMENTS]["paid-summon"]["plane"] == "material"
    bridge = _bridge()
    bridge.write_field("paid-summon", "find_path_current_plane", "astral")
    result = _run(parsed, bridge, MOUNT, _facts())
    assert not result.success and "saved current planes" in result.error
    assert bridge.get_entity("hero")[MOUNT_FIELD] is None


def test_pending_bound_only_blocks_new_exact_steed(parsed):
    bridge = _bridge()
    bridge.write_field("state", PLACEMENTS, {})
    bridge.write_field("state", IDS, ["pending-" + str(index) for index in range(128)])
    result = _run(parsed, bridge, MOUNT, _facts())
    assert not result.success and "128 pending Steeds" in result.error
    assert bridge.get_entity("hero")[MOUNT_FIELD] is None
    prior = bridge.get_entity("state")[IDS]
    prior[-1] = "paid-summon"
    bridge.write_field("state", IDS, prior)
    result = _run(parsed, bridge, MOUNT, _facts())
    assert result.success, result.error
    assert len(bridge.get_entity("state")[IDS]) == 128
