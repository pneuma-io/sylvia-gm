"""Physical firearm drops and pickups move complete objects through Campaign State."""

from collections.abc import Callable
from typing import Any

from .weapon_handling import break_prepared_fire


def drop_helper(param: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    """Share the actual object move between a deliberate drop and a successful DISARM."""
    return {
        "description": "Move the selected held firearm to the current physical scene.",
        "parameters": [param("actor", "entity_id", resolve="entity"),
                       param("world", "entity_id", resolve="entity"), param("map_id", "string")],
        "pre_conditions": [
            {"condition": "actor.weapon != null", "error": "No firearm is held."},
            {"condition": "world.ground_object_sequence == null or world.ground_object_sequence >= 0",
             "error": "Invalid ground object sequence."},
        ],
        "effects": [
            {"set": {"field": "_drop_index", "value": 1}},
            {"set": {"field": "_drop_index", "value": "world.ground_object_sequence + 1"},
             "condition": "world.ground_object_sequence != null"},
            {"call": "td_place_dropped_weapon", "params": {
                "actor": "{_actor_id}", "world": "state", "map_id": "{map_id}",
                "ground_id": "weapon_{_drop_index}",
            }},
            {"set": {"field": "state.ground_object_sequence", "value": "_drop_index"}},
        ],
        "metadata": {"costs_action": False},
    }


def placement_helper(param: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    """Never overwrite an occupied ground slot, even if an authored counter is stale."""
    return {
        "description": "Place the existing firearm in an unoccupied ground slot.",
        "parameters": [param("actor", "entity_id", resolve="entity"),
                       param("world", "entity_id", resolve="entity"),
                       param("map_id", "string"), param("ground_id", "string")],
        "pre_conditions": [{
            "condition": "world.ground_objects == null or ground_id not in world.ground_objects or world.ground_objects[ground_id] == null",
            "error": "Ground object sequence points to an occupied slot; repair the authored counter.",
        }],
        "effects": [
            {"set": {"field": "{_actor_id}.weapon.location", "value": "'GROUND'"}},
            {"set": {"field": "{_actor_id}.weapon.controlled_by", "value": None},
             "condition": "actor.weapon.controlled_by != null"},
            {"set": {"field": "state.ground_objects.{ground_id}", "value": {
                "kind": "'WEAPON'", "map_id": "map_id", "position_cell": "actor.position_cell",
                "dropped_by": "_actor_id", "weapon": "actor.weapon",
            }}},
            {"set": {"field": "{_actor_id}.weapon", "value": None}},
            {"set": {"field": "{_actor_id}.aim.ap", "value": 0}},
            {"set": {"field": "{_actor_id}.aim.target", "value": None}},
            {"set": {"field": "{_actor_id}.aim.point", "value": None}},
            *({"set": {"field": f"state.prepared.{kind}.active", "value": False},
               "condition": f"world.prepared.{kind}.owner == _actor_id"}
              for kind in ("overwatch", "suppression")),
        ],
        "metadata": {"costs_action": False},
    }


def drop_specification(actor: dict[str, Any], param: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    """The GM supplies the actual handling cost; dropping never transfers ownership."""
    return {
        "capability": "trigger-discipline.equipment",
        "description": (
            "Drop this actor's exact carried firearm at their current physical position. "
            "Supply an explicit handling AP cost. The weapon, ammunition and recorded owner "
            "move to a ground object; the actor's current weapon becomes empty. "
            "This does not give the weapon to someone else or automatically ready a reserve."
        ),
        "parameters": [actor, param("weapon_id", "string"), param("ap_cost", "integer", minimum=0),
                       param("map_id", "string", from_state="state.spatial_scene.map.id")],
        "effects": [
            {"set": {"field": "state.last_equipment", "value": {"status": "'DROP_REJECTED'"}}},
            {"condition": (
                "actor.active == true and actor.downed == false and actor.ap >= ap_cost "
                "and actor.weapon != null and actor.weapon.id == weapon_id "
                "and actor.weapon.controlled_by == null and (actor.weapon.location == 'READY' "
                "or actor.weapon.location == 'QUICK_ACCESS' or actor.weapon.location == 'STOWED')"
            ), "on_true": [
                {"call": "td_drop_held_weapon", "params": {
                    "actor": "{_actor_id}", "world": "state", "map_id": "{map_id}",
                }, "export_context": {"_drop_index": "_drop_index"}},
                {"decrement": {"field": "{_actor_id}.ap", "amount": "ap_cost"}},
                {"set": {"field": "state.last_equipment", "value": {
                    "status": "'DROPPED'", "actor": "_actor_id", "weapon": "weapon_id",
                    "ground_slot": "_drop_index", "ap_spent": "ap_cost",
                }}},
            ], "on_false": []},
        ],
        "costs_action": True,
    }


def pickup_specification(actor: dict[str, Any], param: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    """Retrieve the existing object through physical reach, without changing its ammunition."""
    return {
        "capability": "trigger-discipline.equipment",
        "description": (
            "Pick up an actual WEAPON ground object in this scene through a clear adjacent path. "
            "Supply its ground-object ID, exact weapon ID, destination and explicit total AP cost. "
            "An empty current slot may receive it READY; otherwise store the current firearm first "
            "and pick the new one into QUICK_ACCESS or STOWED reserves. Preserves ownership and "
            "ammunition. Creates no weapon, grants no purchase and never copies a taken object."
        ),
        "parameters": [
            actor, param("ground_id", "string"), param("weapon_id", "string"),
            param("location", "enum", enum=("READY", "QUICK_ACCESS", "STOWED")),
            param("ap_cost", "integer", minimum=0),
            param("ground", "object", from_state="state.ground_objects"),
            param("prepared", "object", from_state="state.prepared"),
            param("map_id", "string", from_state="state.spatial_scene.map.id"),
        ],
        "effects": [
            {"set": {"field": "state.last_equipment", "value": {"status": "'PICKUP_REJECTED'"}}},
            {"condition": (
                "actor.active == true and actor.downed == false and actor.ap >= ap_cost "
                "and len(ground_id) > 0 and len(ground_id) <= 128 and '.' not in ground_id "
                "and ground_id in ground and ground[ground_id] != null "
                "and ground[ground_id].kind == 'WEAPON' and ground[ground_id].map_id == map_id"
            ), "on_true": [
                {"set": {"field": "_ground_weapon", "value": "ground[ground_id].weapon"}},
                {"set": {"field": "_pickup_reserves", "value": []}},
                {"set": {"field": "_pickup_reserves", "value": "actor.reserve_weapons"},
                 "condition": "actor.reserve_weapons != null"},
                {"set": {"field": "_held_ids", "value": []}},
                {"list_push": {"list": "_held_ids", "value": "actor.weapon.id"},
                 "condition": "actor.weapon != null"},
                {"set": {"field": "_pickup_valid", "value": "len(_pickup_reserves) < 16 or (actor.weapon == null and len(_pickup_reserves) == 16)"}},
                {"for_each": {"collection": "_pickup_reserves", "as": "_held_weapon", "do": [
                    {"set": {"field": "_pickup_valid", "value": False},
                     "condition": "_held_weapon.id == null or _held_weapon.id in _held_ids"},
                    {"list_push": {"list": "_held_ids", "value": "_held_weapon.id"}},
                ]}},
                {"condition": (
                    "_pickup_valid == true and _ground_weapon != null and _ground_weapon.id == weapon_id "
                    "and weapon_id not in _held_ids and _ground_weapon.location == 'GROUND' "
                    "and _ground_weapon.controlled_by == null and (actor.weapon == null or "
                    "(location != 'READY' and (actor.weapon.location == 'QUICK_ACCESS' "
                    "or actor.weapon.location == 'STOWED')))"
                ), "on_true": [
                    {"set": {"field": "_pickup_cell", "value": "ground[ground_id].position_cell"}},
                    {"spatial_query": {"scene": "state.spatial_scene", "operation": "snapshot"},
                     "store_as": "_pickup_scene"},
                    {"spatial_query": {"scene": "state.spatial_scene", "operation": "path",
                        "snapshot": "{_pickup_scene}", "query": {"map_id": "{map_id}",
                            "start": "{actor.position_cell}", "goal": "{_pickup_cell}",
                            "allow_diagonal": True, "corner_policy": "forbid_cutting",
                            "include_entities": False, "max_steps": 2}}, "store_as": "_pickup_path"},
                    {"condition": "_pickup_path.found == true and _pickup_path.cost <= 1.5",
                     "on_true": [
                        {"set": {"field": "_pickup_location", "value": {"location": "location"}}},
                        {"set": {"field": "_picked_weapon", "value": "merge(_ground_weapon, _pickup_location)"}},
                        {"condition": "actor.weapon == null", "on_true": [
                            {"set": {"field": "{_actor_id}.weapon", "value": "_picked_weapon"}},
                        ], "on_false": [
                            {"list_push": {"list": "_pickup_reserves", "value": "_picked_weapon"}},
                            {"set": {"field": "{_actor_id}.reserve_weapons", "value": "_pickup_reserves"}},
                        ]},
                        {"set": {"field": "state.ground_objects.{ground_id}", "value": None}},
                        {"decrement": {"field": "{_actor_id}.ap", "amount": "ap_cost"}},
                        *break_prepared_fire(),
                        {"set": {"field": "state.last_equipment", "value": {
                            "status": "'PICKED_UP'", "actor": "_actor_id", "weapon": "weapon_id",
                            "ground_object": "ground_id", "location": "location", "ap_spent": "ap_cost",
                        }}},
                    ], "on_false": []},
                ], "on_false": []},
            ], "on_false": []},
        ],
        "costs_action": True,
    }
