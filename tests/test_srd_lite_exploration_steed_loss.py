"""Probe selected exploration loss with the root's already admitted native owners."""

import re
from copy import deepcopy
from random import Random

import pytest
from scripts.srd_lite_exploration_fall import (
    ARRIVE,
    OBSERVE,
    QUALIFY,
    SCENE_FALL_SCHEMA,
    SETTLE_TARGET,
    TARGET,
    VALIDATE,
)
from scripts.srd_lite_exploration_steed_loss import (
    ADMIT_RIDER,
    EMPTY_GUARD,
    FACTS,
    IDS,
    MOUNT_FIELD,
    RECORDS,
    RELEASE,
    RETIRE,
    RIDER_FIELD,
    bind_exploration_steed_loss,
)
from scripts.srd_lite_steed_disappearance import ONE
from scripts.srd_timed_actor_defenses import _require
from tests.test_srd_lite_combat_scene_reentry import admission_source as _admission_source
from tests.test_srd_lite_mass_cure_geometry import _scene_entity, _wall

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import (
    AuthorityMemoryStateBridge,
    _runtime_parameter_schema,
)
from sylvia_gm.application.schema_admission import validate_json_schema
from sylvia_gm.domain.package_library import participant_schema_sha256
from sylvia_gm.runtime import RulesetRuntime
from sylvia_gm.runtime._tool_executor import execute_tool
from sylvia_gm.runtime._types import ExecutionBudget

NAMESPACE = "test.exploration.steed.loss"
DEATH = "record_srd_actual_death"
RESOLVE = "resolve_srd_scene_fall_event"
OUTER_SETTLE = "settle_srd_phantom_steed_fades"
SHARED = "finish_srd_shared_action_settlements"
native_owner_source = _admission_source


def _nodes(value):
    if isinstance(value, dict):
        return 1 + sum(_nodes(item) for item in value.values())
    if isinstance(value, list):
        return 1 + sum(_nodes(item) for item in value)
    return 1


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


@pytest.fixture(scope="module")
def bound(native_owner_source):
    data, record = deepcopy(native_owner_source)
    original = deepcopy(data)
    old_record = deepcopy(record)
    changed = bind_exploration_steed_loss(data, record)
    assert data["definitions"] == original["definitions"]
    assert len(data["definitions"]["spells"]) == 96
    for name, owner in original["tools"].items():
        if name not in changed:
            assert data["tools"][name] == owner, name
    for name in (ONE, QUALIFY, OBSERVE, TARGET, SETTLE_TARGET):
        assert data["tools"][name]["effects"][0]["on_false"] == original["tools"][name]["effects"]
    before = original["tools"][RETIRE]["effects"][-1]["on_false"]
    after = data["tools"][RETIRE]["effects"][-1]["on_false"]
    assert after == before
    assert data["tools"][RETIRE]["parameters"] == original["tools"][RETIRE]["parameters"]
    assert any(
        row.get("require", {}).get("condition") == EMPTY_GUARD
        for row in data["tools"][RETIRE]["effects"][-1]["on_true"]
    )
    with pytest.raises(ValueError, match="already bound"):
        bind_exploration_steed_loss(data, record)
    growth = _nodes(data) - _nodes(original)
    print(f"1082 expanded runtime node growth: {growth}")
    print(f"1082 participant/contract node growth: {_nodes(record) - _nodes(old_record)}")
    return data, record, old_record, changed


@pytest.fixture(scope="module")
def parsed(bound):
    data = deepcopy(bound[0])
    # Keep already qualified native combat geometry closed; fail rather than fake it.
    data["tools"][RETIRE]["effects"][-1]["on_false"] = [
        _require("false", "Use the existing native physical Steed disappearance proof.")
    ]
    for name in (ONE, QUALIFY, OBSERVE, TARGET, SETTLE_TARGET):
        data["tools"][name]["effects"][0]["on_false"] = [
            _require("false", "Use the existing qualified native combat fall/Feather proof.")
        ]
    closure, pending = (
        {},
        [
            FACTS, RETIRE, DEATH, OUTER_SETTLE, SHARED, ADMIT_RIDER, VALIDATE, RESOLVE, ARRIVE,
            "install_srd_feather_fall",
        ],
    )
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = data["tools"][name]
            pending.extend(_calls(closure[name]["effects"]))
    return RulesetRuntime(
        ruleset_dict={
            **{key: value for key, value in data.items() if key not in {"tools", "procedures"}},
            "tools": closure,
        }
    )


def _bridge(cached=False, cargo=True, mounted=True):
    hero = {
        "name": "Actual caster",
        "hp": 40,
        "is_alive": True,
        "size_rank": 2,
        "creature_type": "humanoid",
        "creature_kind": "monster",
        "conditions": [],
        "condition_immunities": [],
        "temp_hp": 0,
        "max_hp": 40,
        "speed": 30,
        "abilities": {
            "strength": 16,
            "dexterity": 12,
            "constitution": 14,
            "intelligence": 10,
            "wisdom": 10,
            "charisma": 10,
        },
        "created_steed_id": "steed",
        MOUNT_FIELD: "steed" if mounted else None,
        "resources": {"spell_slot_2": 1},
        "travel_location": {
            "place": "the sheltered courtyard",
            "plane": "material",
            "native_anchor": False,
            "via_spell": "misty_step",
        },
    }
    steed = {
        "name": "Actual Otherworldly Steed",
        "hp": 25,
        "is_alive": True,
        "size_rank": 3,
        "creature_type": "celestial",
        "creature_kind": "monster",
        "conditions": [],
        "created_spell_id": "find_steed",
        "created_active": True,
        "created_spell_level": 2,
        "created_caster_state_id": "hero",
        RIDER_FIELD: "hero" if mounted else None,
        "inventory": ["saddle", "bag"] if cargo else [],
        "held_items": ["saddle", "held-rope"] if cargo else [],
        "resources": {"healing_touch": 1},
    }
    lasting = {
        "lasting_tick": 10,
        "created_actor_ids": ["steed"],
        "created_actor_records": {
            "steed": {
                "individual": "steed",
                "spell": "find_steed",
                "caster": "hero",
                "active": True,
                "disappearance": "",
                "spell_level": 2,
                "source_page": 131,
            }
        },
        "created_actor_dropped_items": [
            {
                "item_id": "earlier-real-item",
                "position": [0, 0, 0],
                "plane": "material",
                "former_carrier": "earlier",
            }
        ],
    }
    if cached:
        # Keep obsolete geometry, with the admitted zone-list shape used by shared map feedback.
        lasting["spatial_scene"] = {
            "id": "obsolete", "entities": [], "map": {"revision": 6}, "zones": []
        }
    return AuthorityMemoryStateBridge(
        NAMESPACE,
        {
            NAMESPACE + ".state": lasting,
            NAMESPACE + ".combat": {"encounter_active": False, "turn_active": False},
            NAMESPACE + ".entities": {
                "hero": hero,
                "steed": steed,
                "bag": {"contents": ["actual-food"]},
                "saddle": {"kind": "real saddle"},
                "held-rope": {"kind": "real rope"},
            },
        },
    )


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


def _facts(**changes):
    return {
        "steed_state": "steed",
        "established_steed_placement": "the sheltered courtyard",
        "established_plane": "material",
        "established_landing_supported": True,
        "established_rider_landing": "the supported courtyard stones",
        "established_cargo_drop": "on the courtyard stones beside the actual rider",
        "established_rider_fall_distance": 0,
        "established_feather_relations": [],
        "established_clear_descent": True,
        "established_rider_current_placement": "falling above the courtyard stones",
        **changes,
    }


def _snapshot(bridge):
    return deepcopy(
        {
            name: bridge.get_entity(name)
            for name in ("state", "hero", "steed", "bag", "saddle", "held-rope")
        }
    )


def _assert_retired(bridge, reason, old):
    actor, hero, lasting = [bridge.get_entity(name) for name in ("steed", "hero", "state")]
    assert actor["created_active"] is False and actor["created_disappearance"] == reason
    assert actor["inventory"] == [] and actor["held_items"] == []
    assert actor[RIDER_FIELD] is None and hero[MOUNT_FIELD] is None
    source = lasting["created_actor_records"]["steed"]
    assert source["active"] is False and source["disappearance"] == reason
    assert source["individual"] == "steed" and source["caster"] == "hero"
    pending = lasting[RECORDS]["steed"]
    assert pending["steed"] == "steed" and pending["rider"] == "hero"
    assert pending["mounted"] is False and pending["pending"] is True
    assert pending["released_by_disappearance"] is True and pending["disappearance"] == reason
    assert pending["loss_facts"] is None and pending["plane"] == "material"
    assert pending["rider_placement"] == _facts()["established_rider_landing"]
    assert (
        lasting["created_actor_dropped_items"][0] == old["state"]["created_actor_dropped_items"][0]
    )
    drops = lasting["created_actor_dropped_items"][1:]
    assert [row["item_id"] for row in drops] == ["saddle", "bag", "held-rope"]
    assert all(
        row["placement"] == _facts()["established_cargo_drop"]
        and row["plane"] == "material"
        and row["former_carrier"] == "steed"
        and "position" not in row
        for row in drops
    )
    for name in ("bag", "saddle", "held-rope"):
        assert bridge.get_entity(name) == old[name]
    assert actor["resources"] == old["steed"]["resources"]
    assert hero["resources"] == old["hero"]["resources"]
    assert lasting.get("spatial_scene") == old["state"].get("spatial_scene")


def test_schema_and_selected_expression_limits(bound, parsed):
    data, record, _, changed = bound
    declaration = next(row for row in record["tools"] if row["name"] == FACTS)
    assert declaration["name"] == FACTS
    assert _runtime_parameter_schema(parsed.tools[FACTS]) == declaration["input_schema"]
    for name in changed:
        effects = data["tools"][name]["effects"]
        if name == RETIRE:
            effects = [row for row in effects if row is not effects[-1]] + effects[-1]["on_true"]
        elif name in (ONE, QUALIFY, OBSERVE, TARGET, SETTLE_TARGET):
            effects = effects[0]["on_true"]
        for group in _lists(effects):
            for row in group:
                for expression in (
                    row.get("condition"),
                    row.get("require", {}).get("condition"),
                    row.get("set", {}).get("value"),
                ):
                    if isinstance(expression, str):
                        assert len(expression) <= 500, expression
                        assert all(
                            len(value) <= 200 for value in re.findall("'([^']*)'", expression)
                        )
                if "for_each" in row:
                    assert re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", row["for_each"]["collection"])


def test_optional_pc_fall_contract_preserves_native_form_and_canonical_fingerprint(bound):
    _, record, old_record, _ = bound
    artifact = next(
        row for row in record["participant_artifacts"] if row["kind"] == "player_character"
    )
    before = next(
        row for row in old_record["participant_artifacts"] if row["kind"] == "player_character"
    )
    entity = artifact["entity_state_schema"]
    assert entity["additionalProperties"] is False
    assert entity.get("required", []) == before["entity_state_schema"].get("required", [])
    assert entity["properties"]["scene_fall"] == SCENE_FALL_SCHEMA
    validate_json_schema(entity, label="PC actual fall state")
    for field in before["entity_state_schema"]["properties"]:
        assert entity["properties"][field] == before["entity_state_schema"]["properties"][field]
    assert artifact["schema"] == before["schema"]
    for field in ("initial_state_calculations", "initial_state_validation"):
        assert artifact.get(field) == before.get(field)
    if "schema_sha256" in before:
        assert artifact["schema_sha256"] == participant_schema_sha256(
            artifact["schema"],
            entity_state_schema=entity,
            initial_state_calculations=artifact.get("initial_state_calculations", ()),
            initial_state_validation=artifact.get("initial_state_validation"),
        )


@pytest.mark.parametrize("cached", [False, True])
def test_actual_steed_death_callback_retires_releases_and_keeps_genuine_cargo(parsed, cached):
    bridge = _bridge(cached)
    before = _snapshot(bridge)
    assert _run(parsed, bridge, FACTS, _facts()).success
    bridge.write_field("steed", "hp", 0)
    bridge.write_field("steed", "is_alive", False)
    rng, calls = dice._rng.getstate(), []
    result = _run(parsed, bridge, DEATH, {"target_state": "steed"}, calls)
    assert result.success, result.error
    _assert_retired(bridge, "zero_hit_points", before)
    assert RETIRE in calls and RELEASE in calls
    assert not any(
        name in calls
        for name in (
            ONE,
            QUALIFY,
            VALIDATE,
            RESOLVE,
            "begin_srd_scene_fall",
            "srd_information_scene_entity",
            "resolve_srd_fall",
        )
    )
    assert bridge.get_entity("hero")["hp"] == 40 and bridge.get_entity("hero")["is_alive"] is True
    assert dice._rng.getstate() == rng


@pytest.mark.parametrize("cached", [False, True])
def test_actual_caster_death_clears_dead_rider_and_admits_it_without_resurrection(parsed, cached):
    bridge = _bridge(cached)
    before = _snapshot(bridge)
    assert _run(parsed, bridge, FACTS, _facts()).success
    bridge.write_field("hero", "hp", 0)
    bridge.write_field("hero", "is_alive", False)
    result = _run(parsed, bridge, DEATH, {"target_state": "hero"})
    assert result.success, result.error
    _assert_retired(bridge, "caster_died", before)
    dead = _snapshot(bridge)
    result = _run(parsed, bridge, ADMIT_RIDER, {"steed_state": "steed", "rider_state": "hero"})
    assert result.success, result.error
    assert _snapshot(bridge) == dead


def test_replacement_keeps_new_caster_source_link_and_exact_old_retirement(parsed):
    bridge = _bridge()
    before = _snapshot(bridge)
    assert _run(parsed, bridge, FACTS, _facts()).success
    bridge.write_field("hero", "created_steed_id", "genuine-new-source")
    result = _run(parsed, bridge, RETIRE, {"actor_state": "steed", "reason": "replaced"})
    assert result.success, result.error
    _assert_retired(bridge, "replaced", before)
    assert bridge.get_entity("hero")["created_steed_id"] == "genuine-new-source"


@pytest.mark.parametrize(
    "field,value",
    [
        ("state.lasting_tick", 11),
        ("steed.inventory", ["different-real-item"]),
        ("hero.otherworldly_steed_mount", "different-steed"),
        (
            "steed.travel_location",
            {
                "place": "later genuine native arrival",
                "plane": "material",
                "native_anchor": False,
                "via_spell": "misty_step",
            },
        ),
    ],
)
def test_current_facts_expire_on_actual_tick_items_pair_or_native_arrival_change(
    parsed, field, value
):
    bridge = _bridge()
    assert _run(parsed, bridge, FACTS, _facts()).success
    identity, path = field.split(".", 1)
    bridge.write_field(identity, path, value)
    before = _snapshot(bridge)
    result = _run(parsed, bridge, RETIRE, {"actor_state": "steed", "reason": "replaced"})
    assert not result.success
    assert _snapshot(bridge) == before


def _positive(parsed, distance=80, relations=(), *, cached=True, bridge=None):
    bridge = bridge or _bridge(cached=cached)
    assert _run(
        parsed,
        bridge,
        FACTS,
        _facts(
            established_rider_fall_distance=distance,
            established_feather_relations=list(relations),
        ),
    ).success
    bridge.write_field("steed", "hp", 0)
    bridge.write_field("steed", "is_alive", False)
    result = _run(parsed, bridge, DEATH, {"target_state": "steed"})
    assert result.success, result.error
    result = _run(parsed, bridge, SHARED, {})
    assert result.success, result.error
    state = bridge.get_entity("state")
    event_id = state["pending_srd_action_stack"][-1]
    event = state["pending_srd_actions"][event_id]
    assert event["kind"] == "scene_fall" and event["stage"] == "reaction"
    assert event["status"] == "open"
    assert bridge.get_entity("hero")["scene_fall"]["event_id"] == event_id
    return bridge, event_id


def test_private_loss_release_defers_until_the_actual_native_outer_boundary(parsed):
    bridge = _bridge(cached=True)
    assert _run(parsed, bridge, FACTS, _facts(established_rider_fall_distance=80)).success
    bridge.write_field("steed", "hp", 0)
    bridge.write_field("steed", "is_alive", False)
    before_rng, before_combat = dice._rng.getstate(), deepcopy(bridge.combat_state)
    result = _run(parsed, bridge, DEATH, {"target_state": "steed"})
    assert result.success, result.error
    state, hero = bridge.get_entity("state"), bridge.get_entity("hero")
    assert state["created_actor_records"]["steed"]["active"] is False
    assert state["otherworldly_steed_released_riders"] == [{"rider": "hero", "steed": "steed"}]
    assert hero["scene_fall"]["status"] == "ready"
    assert not state.get("pending_srd_action_stack")
    bridge.write_field("state", "pending_srd_action_stack", ["original-action"])
    result = _run(parsed, bridge, SHARED, {})
    assert result.success, result.error
    assert bridge.get_entity("hero")["scene_fall"]["status"] == "ready"
    assert bridge.get_entity("state")["pending_srd_action_stack"] == ["original-action"]
    bridge.write_field("state", "pending_srd_action_stack", [])
    result = _run(parsed, bridge, SHARED, {})
    assert result.success, result.error
    state, hero = bridge.get_entity("state"), bridge.get_entity("hero")
    event_id = state["pending_srd_action_stack"][-1]
    assert state["pending_srd_actions"][event_id]["kind"] == "scene_fall"
    assert hero["scene_fall"]["status"] == "pending"
    assert state["otherworldly_steed_released_riders"] == []
    assert hero["hp"] == 40 and hero["is_alive"] is True
    assert hero["resources"]["spell_slot_2"] == 1
    assert bridge.combat_state == before_combat and dice._rng.getstate() == before_rng


@pytest.mark.parametrize("distance", [5, 80])
def test_positive_drop_opens_actual_native_window_then_uses_original_remaining_fall_damage(
    parsed, distance
):
    bridge, event_id = _positive(parsed, distance)
    assert bridge.get_entity("hero")["hp"] == 40
    before_rng, old_rng = dice._rng.getstate(), dice._rng
    dice._rng = Random(107)
    try:
        result = _run(parsed, bridge, RESOLVE, {"pending_action_id": event_id})
    finally:
        dice._rng = old_rng
    assert result.success, result.error
    hero = bridge.get_entity("hero")
    state = bridge.get_entity("state")
    assert hero["scene_fall"]["status"] == "landed"
    assert hero["scene_fall"]["remaining_distance"] == 0
    assert hero["scene_fall"]["ended_reason"] == "ordinary_fall"
    assert hero[MOUNT_FIELD] is None
    assert state[RECORDS]["steed"]["rider_placement"] == _facts()["established_rider_landing"]
    assert state["created_actor_records"]["steed"]["active"] is False
    assert state["spatial_scene"]["id"] == "obsolete"
    assert result.context["_scene_fall_actor_name"] == "Actual caster"
    location = result.context["_scene_fall_detail_rows"][-1]
    assert location["label"] == "Current location"
    assert _facts()["established_rider_landing"] in location["value"]
    assert "remaining height 0" in location["value"]
    if distance < 10:
        assert hero["hp"] == 40 and "prone" not in hero["conditions"]
        assert result.context["_scene_fall_roll"] is None
    else:
        roll = result.context["_scene_fall_roll"]
        assert len(roll.results) == distance // 10 and roll.sides == 6
        assert hero["hp"] == 40 - result.context["_fall_hp_lost"]
        assert "prone" in hero["conditions"]
    assert dice._rng.getstate() == before_rng


@pytest.mark.parametrize(
    "facts",
    [
        {"established_clear_descent": False},
        {"established_rider_current_placement": ""},
        {"established_landing_supported": False},
    ],
)
def test_positive_drop_rejects_missing_actual_current_support_or_descent_before_mutation(
    parsed, facts
):
    bridge = _bridge()
    before = _snapshot(bridge)
    result = _run(parsed, bridge, FACTS, _facts(established_rider_fall_distance=80, **facts))
    assert not result.success
    assert _snapshot(bridge) == before


def test_genuine_feather_source_uses_native_duration_clock_and_safe_landing(parsed):
    """This direct paid-leaf probe proves source/clock integration, not slot/reaction payment."""
    bridge, event_id = _positive(parsed, 80)
    result = _run(
        parsed,
        bridge,
        "install_srd_feather_fall",
        {
            "caster_state": "hero",
            "targets": ["hero"],
            "reaction_trigger_id": event_id,
            "actual_spell_level": 1,
            "spell_cast_id": "genuine-native-cast",
        },
    )
    assert result.success, result.error
    source = bridge.get_entity("state")["turn_effects"][-1]
    assert source["spell"] == "feather_fall" and source["target"] == "hero"
    assert source["caster"] == "hero" and source["trigger_id"] == event_id
    assert source["expires_tick"] == source["started_tick"] + 20
    assert source["concentration"] is False
    assert _run(parsed, bridge, RESOLVE, {"pending_action_id": event_id}).success
    assert bridge.get_entity("hero")["scene_fall"]["status"] == "descending"
    bridge.write_field("state", "lasting_tick", source["started_tick"] + 2)
    result = _run(parsed, bridge, SETTLE_TARGET, {"target_state": "hero"})
    assert result.success, result.error
    assert bridge.get_entity("hero")["scene_fall"]["remaining_distance"] == 20
    assert bridge.get_entity("hero")["hp"] == 40
    bridge.write_field("state", "lasting_tick", source["started_tick"] + 3)
    assert _run(parsed, bridge, SETTLE_TARGET, {"target_state": "hero"}).success
    hero = bridge.get_entity("hero")
    assert hero["scene_fall"]["status"] == "landed"
    assert hero["scene_fall"]["ended_reason"] == "feather_landing"
    assert hero["hp"] == 40 and "prone" not in hero["conditions"]
    assert bridge.get_entity("state")["turn_effects"][-1]["active"] is False


def test_feather_expiry_uses_native_capped_damage_for_only_remaining_height(parsed):
    bridge, event_id = _positive(parsed, 1000)
    result = _run(
        parsed,
        bridge,
        "install_srd_feather_fall",
        {
            "caster_state": "hero",
            "targets": ["hero"],
            "reaction_trigger_id": event_id,
            "actual_spell_level": 1,
            "spell_cast_id": "genuine-native-cast",
        },
    )
    assert result.success, result.error
    source = bridge.get_entity("state")["turn_effects"][-1]
    assert _run(parsed, bridge, RESOLVE, {"pending_action_id": event_id}).success
    bridge.write_field("state", "lasting_tick", source["expires_tick"])
    result = _run(parsed, bridge, SETTLE_TARGET, {"target_state": "hero"})
    assert result.success, result.error
    assert len(result.context["_scene_fall_roll"].results) == 20
    hero = bridge.get_entity("hero")
    assert hero["scene_fall"]["status"] == "landed"
    assert hero["scene_fall"]["ended_reason"] == "feather_expired"
    assert hero["scene_fall"]["remaining_distance"] == 0
    assert hero["hp"] == max(0, 40 - result.context["_fall_effective_damage"])
    assert bridge.get_entity("state")["turn_effects"][-1]["active"] is False


def _arrival_scene(height, ground_height=20):
    body = _scene_entity("hero", [0, 0, height + ground_height])
    body["metadata"]["retained_mechanic"] = "Preserve this exact admitted body metadata"
    ground = _wall(height=ground_height)
    ground["id"] = "actual-ground"
    ground["volume"]["min"] = [-100, -100, ground_height - 1]
    ground["volume"]["max"] = [100, 100, ground_height]
    ground["metadata"]["ground"] = True
    return {
        "format": 1,
        "map": {
            "id": "mass-cure-fixture",
            "revision": 4,
            "bounds": {"min": [-200, -200, -200], "max": [200, 200, 200]},
            "grid": {"cell_size": 5, "unit": "ft", "origin": [-200, -200]},
            "layers": [{"id": "world", "min_elevation": -200, "max_elevation": 200}],
        },
        "entities": [body],
        "geometry": [ground],
        "terrain": [],
        "zones": [],
    }


@pytest.mark.parametrize("height,success", [(80, True), (79, False)])
def test_current_admitted_body_and_ground_convert_the_exact_fall_without_resurrecting_steed(
    parsed, height, success
):
    bridge, event_id = _positive(parsed)
    supplied = _arrival_scene(height)
    bridge.write_field("state", "spatial_scene", supplied)
    before = _snapshot(bridge)
    result = _run(parsed, bridge, ARRIVE, {"target_state": "hero"})
    assert result.success is success, result.error
    if not success:
        assert "ground-relative height" in result.error
        assert _snapshot(bridge) == before
        return
    hero, state = bridge.get_entity("hero"), bridge.get_entity("state")
    fall = hero["scene_fall"]
    assert "mode" not in fall and "remaining_distance" not in fall
    assert fall["scene_id"] == "hero" and fall["plane"] == "material"
    assert fall["origin"] == supplied["entities"][0]["position"]
    assert fall["event_id"] == event_id and fall["started_tick"] == 10
    assert state["spatial_scene"] == supplied
    assert state["created_actor_records"]["steed"]["active"] is False
    assert bridge.get_entity("steed")["created_active"] is False
    assert all(row["id"] != "steed" for row in state["spatial_scene"]["entities"])
    event_row = state["pending_srd_actions"][event_id]["parameters"]["falling_events"][0]
    assert "mode" not in event_row and "steed_id" not in event_row
    assert event_row["actor_id"] == "hero" and event_row["scene_id"] == "hero"
    assert event_row["distance"] == 80 and event_row["ground_position"] == [0, 0, 20]
    assert event_row["origin"] == fall["origin"]


@pytest.mark.parametrize(
    "distance,visible,same_plane,success",
    [
        (60, True, True, True),
        (60.01, True, True, False),
        (20, False, True, False),
        (20, True, False, False),
    ],
)
def test_other_actual_feather_caster_needs_current_native_range_and_sight(
    parsed, distance, visible, same_plane, success
):
    bridge = _bridge()
    bridge.create_entity(
        "helper", {"name": "Actual other caster", "is_alive": True, "conditions": []}
    )
    relations = [
        {
            "caster": "helper",
            "target": "hero",
            "distance": distance,
            "same_plane": same_plane,
            "visible": visible,
        }
    ]
    bridge, event_id = _positive(parsed, relations=relations, bridge=bridge)
    before = _snapshot(bridge)
    result = _run(
        parsed,
        bridge,
        VALIDATE,
        {"caster_state": "helper", "reaction_trigger_id": event_id, "targets": ["hero"]},
    )
    assert result.success is success, result.error
    assert _snapshot(bridge) == before


def test_no_current_facts_rejects_mounted_loss_without_cached_scene_lookup(parsed):
    bridge = _bridge(cached=True)
    before = _snapshot(bridge)
    result = _run(parsed, bridge, RETIRE, {"actor_state": "steed", "reason": "replaced"})
    assert not result.success and "current exploration disappearance facts" in result.error
    assert _snapshot(bridge) == before


def test_empty_unridden_native_retirement_stays_available_without_new_fact_requirements(parsed):
    bridge = _bridge(cached=True, cargo=False, mounted=False)
    result = _run(parsed, bridge, RETIRE, {"actor_state": "steed", "reason": "replaced"})
    assert result.success, result.error
    assert bridge.get_entity("steed")["created_active"] is False
    assert bridge.get_entity("hero")["created_steed_id"] is None


def test_dead_rider_exception_requires_actual_inactive_native_source_and_matching_cause(parsed):
    bridge = _bridge()
    assert _run(parsed, bridge, FACTS, _facts()).success
    bridge.write_field("hero", "hp", 0)
    bridge.write_field("hero", "is_alive", False)
    assert _run(parsed, bridge, DEATH, {"target_state": "hero"}).success
    bridge.write_field("state", "created_actor_records.steed.disappearance", "contradictory")
    before = _snapshot(bridge)
    result = _run(parsed, bridge, ADMIT_RIDER, {"steed_state": "steed", "rider_state": "hero"})
    assert not result.success and "genuine native disappearance cause" in result.error
    assert _snapshot(bridge) == before


def test_current_fact_bound_preserves_existing_index_and_only_caps_new_exact_ids(parsed):
    bridge = _bridge()
    bridge.write_field("state", IDS, ["earlier-" + str(index) for index in range(128)])
    before = _snapshot(bridge)
    result = _run(parsed, bridge, FACTS, _facts())
    assert not result.success and "bound is full" in result.error
    assert _snapshot(bridge) == before
    bridge.write_field("state", IDS, ["steed", *before["state"][IDS][1:]])
    result = _run(parsed, bridge, FACTS, _facts())
    assert result.success, result.error
    assert len(bridge.get_entity("state")[IDS]) == 128


def test_optional_native_arrival_and_item_fields_are_real_nulls_and_arrays(parsed):
    bridge = _bridge(cargo=False)
    for identity, field in (
        ("hero", "travel_location"),
        ("steed", "inventory"),
        ("steed", "held_items"),
    ):
        bridge.write_field(identity, field, None)
    facts = _run(parsed, bridge, FACTS, _facts())
    assert facts.success, facts.error
    packet = bridge.get_entity("state")[RECORDS]["steed"]["loss_facts"]
    absent = {"has_travel": False, "place": None, "plane": None, "path_plane": None}
    assert packet["steed_arrival"] == absent and packet["rider_arrival"] == absent
    assert packet["inventory"] == [] and packet["held_items"] == []
    bridge.write_field("hero", "find_path_current_plane", "material")
    facts = _run(parsed, bridge, FACTS, _facts())
    assert facts.success, facts.error
    packet = bridge.get_entity("state")[RECORDS]["steed"]["loss_facts"]
    assert packet["steed_arrival"] == absent
    assert packet["rider_arrival"] == {**absent, "path_plane": "material"}
    result = _run(parsed, bridge, RETIRE, {"actor_state": "steed", "reason": "replaced"})
    assert result.success, result.error
    assert bridge.get_entity("steed")["created_active"] is False


def test_current_rider_native_arrival_change_invalidates_loss_before_unlink(parsed):
    bridge = _bridge()
    facts = _run(parsed, bridge, FACTS, _facts())
    assert facts.success, facts.error
    arrival = bridge.get_entity("hero")["travel_location"]
    arrival["place"] = "a genuinely later courtyard arrival"
    bridge.write_field("hero", "travel_location", arrival)
    before = _snapshot(bridge)
    result = _run(parsed, bridge, RETIRE, {"actor_state": "steed", "reason": "replaced"})
    assert not result.success and "rider's genuine later native travel" in result.error
    assert _snapshot(bridge) == before
