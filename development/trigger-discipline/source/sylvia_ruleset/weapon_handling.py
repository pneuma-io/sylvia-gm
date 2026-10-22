"""GM-adjudicated handling of the firearm already held by a TD actor."""

from collections.abc import Callable
from typing import Any


def end_suppression(actor_id: str = "_actor_id") -> list[dict[str, Any]]:
    """End only this actor's continuing fire after a committed incompatible action."""
    return [{
        "condition": f"'suppression' in prepared and prepared.suppression.owner == {actor_id}",
        "on_true": [
            {"set": {"field": "state.prepared.suppression.active", "value": False}},
            {"set": {"field": "state.prepared.suppression.capacity", "value": 0}},
        ],
        "on_false": [],
    }]


def break_prepared_fire() -> list[dict[str, Any]]:
    """Handling breaks maintained aim and the acting character's prepared firing solution."""
    changes = [
        {"set": {"field": "{_actor_id}.aim.ap", "value": "0"}},
        {"set": {"field": "{_actor_id}.aim.target", "value": "null"}},
        {"set": {"field": "{_actor_id}.aim.point", "value": "null"}},
    ]
    for kind in ("overwatch", "suppression"):
        changes.append({
            "condition": f"'{kind}' in prepared and prepared.{kind}.owner == _actor_id",
            "set": {"field": f"state.prepared.{kind}.active", "value": "false"},
        })
    return changes


def specification(actor: dict[str, Any], param: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    """Commit an explicit handling cost without inventing a retrieval schedule."""
    effects: list[dict[str, Any]] = [
        {"set": {"field": "state.last_equipment", "value": {}}},
        {"set": {"field": "state.last_equipment.status", "value": "'HANDLING_REJECTED'"}},
    ]
    changes: list[dict[str, Any]] = [
        {"set": {"field": "state.last_equipment.actor", "value": "_actor_id"}},
        {"set": {"field": "state.last_equipment.weapon", "value": "weapon_id"}},
        {"set": {"field": "state.last_equipment.previous_location", "value": "actor.weapon.location"}},
        {"set": {"field": "state.last_equipment.location", "value": "location"}},
        {"set": {"field": "state.last_equipment.ap_spent", "value": "ap_cost"}},
        {"decrement": {"field": "{_actor_id}.ap", "amount": "ap_cost"}},
        {"set": {"field": "{_actor_id}.weapon.location", "value": "location"}},
        *break_prepared_fire(),
    ]
    changes.append({"set": {"field": "state.last_equipment.status", "value": "'HANDLED'"}})
    effects.append({
        "condition": (
            "actor.active == true and actor.downed == false and actor.ap >= ap_cost and actor.weapon.id == weapon_id "
            "and actor.weapon.controlled_by == null and actor.weapon.location != location "
            "and (actor.weapon.location == 'READY' or actor.weapon.location == 'QUICK_ACCESS' "
            "or actor.weapon.location == 'STOWED')"
        ),
        "on_true": changes,
        "on_false": [],
    })
    return {
        "capability": "trigger-discipline.equipment",
        "description": (
            "GM-adjudicated ready/stow handling of this actor's carried firearm. Supply the "
            "actual weapon ID, destination and explicit AP cost warranted by its physical "
            "storage and situation; no universal cost is defined. Does not pick up, transfer, "
            "replace or reload a weapon. Breaks maintained aim and this actor's prepared fire."
        ),
        "parameters": [
            actor, param("weapon_id", "string"),
            param("location", "enum", enum=("READY", "QUICK_ACCESS", "STOWED")),
            param("ap_cost", "integer", minimum=0),
            param("prepared", "object", from_state="state.prepared"),
        ],
        "effects": effects,
        "costs_action": True,
    }


def switch_specification(actor: dict[str, Any], param: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    """Exchange the actor's actual current and carried reserve weapons in one transaction."""
    return {
        "capability": "trigger-discipline.equipment",
        "description": (
            "GM-adjudicated switch to a firearm in this actor's reserve_weapons. Supply exact "
            "current/next physical weapon IDs (omit current when the slot is empty), storage for the current weapon, and the "
            "explicit total handling AP cost. Moves both existing objects with their ammunition "
            "and recoil intact; creates no equipment or ammunition."
        ),
        "parameters": [
            actor, param("current_weapon_id", "string", required=False), param("next_weapon_id", "string"),
            param("store_location", "enum", enum=("QUICK_ACCESS", "STOWED")),
            param("ap_cost", "integer", minimum=0),
            param("prepared", "object", from_state="state.prepared"),
        ],
        "effects": [
            {"set": {"field": "state.last_equipment", "value": {}}},
            {"set": {"field": "state.last_equipment.status", "value": "'SWITCH_REJECTED'"}},
            {"condition": (
                "actor.active == true and actor.downed == false and actor.ap >= ap_cost and 'reserve_weapons' in actor "
                "and len(actor.reserve_weapons) <= 16 and current_weapon_id != next_weapon_id "
                "and ((actor.weapon == null and current_weapon_id == null) or (actor.weapon != null "
                "and actor.weapon.id == current_weapon_id and actor.weapon.controlled_by == null))"
            ), "on_true": [
                {"set": {"field": "_current_valid", "value": "actor.weapon == null or actor.weapon.location == 'READY' or actor.weapon.location == 'QUICK_ACCESS' or actor.weapon.location == 'STOWED'"}},
                {"set": {"field": "_remaining_weapons", "value": []}},
                {"set": {"field": "_weapon_ids", "value": []}},
                {"set": {"field": "_stored_weapon_id", "value": None}},
                {"condition": "actor.weapon != null", "on_true": [
                    {"list_push": {"list": "_weapon_ids", "value": "current_weapon_id"}},
                    {"set": {"field": "_stored_weapon_id", "value": "current_weapon_id"}},
                ]},
                {"set": {"field": "_next_weapon", "value": None}},
                {"set": {"field": "_weapons_unique", "value": True}},
                {"set": {"field": "_carried_weapons", "value": "actor.reserve_weapons"}},
                {"for_each": {"collection": "_carried_weapons", "as": "_reserve_weapon", "do": [
                    {"set": {"field": "_weapons_unique", "value": False},
                     "condition": "_reserve_weapon.id == null or _reserve_weapon.id in _weapon_ids"},
                    {"list_push": {"list": "_weapon_ids", "value": "_reserve_weapon.id"}},
                    {"condition": "_reserve_weapon.id == next_weapon_id", "on_true": [
                        {"set": {"field": "_next_weapon", "value": "_reserve_weapon"}},
                    ], "on_false": [
                        {"list_push": {"list": "_remaining_weapons", "value": "_reserve_weapon"}},
                    ]},
                ]}},
                {"condition": (
                    "_current_valid == true and _weapons_unique == true and _next_weapon != null and "
                    "_next_weapon.controlled_by == null and "
                    "(_next_weapon.location == 'QUICK_ACCESS' or _next_weapon.location == 'STOWED')"
                ), "on_true": [
                    {"condition": "actor.weapon != null", "on_true": [
                        {"set": {"field": "{_actor_id}.weapon.location", "value": "store_location"}},
                        {"set": {"field": "_previous_weapon", "value": "actor.weapon"}},
                        {"list_push": {"list": "_remaining_weapons", "value": "_previous_weapon"}},
                    ]},
                    {"set": {"field": "{_actor_id}.reserve_weapons", "value": "_remaining_weapons"}},
                    {"set": {"field": "{_actor_id}.weapon", "value": "_next_weapon"}},
                    {"set": {"field": "{_actor_id}.weapon.location", "value": "'READY'"}},
                    {"decrement": {"field": "{_actor_id}.ap", "amount": "ap_cost"}},
                    *break_prepared_fire(),
                    {"set": {"field": "state.last_equipment", "value": {
                        "actor": "_actor_id", "weapon": "next_weapon_id",
                        "ap_spent": "ap_cost",
                        "status": "'SWITCHED'",
                    }}},
                    {"set": {"field": "state.last_equipment.stored_weapon", "value": None}},
                    {"set": {"field": "state.last_equipment.stored_weapon", "value": "_stored_weapon_id"},
                     "condition": "_stored_weapon_id != null"},
                ], "on_false": []},
            ], "on_false": []},
        ],
        "costs_action": True,
    }


def transfer_specification(actor: dict[str, Any], param: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    """Hand one existing reserve firearm to an adjacent actor without changing its ownership."""
    return {
        "capability": "trigger-discipline.equipment",
        "description": (
            "Hand a carried reserve firearm to a nearby active recipient with a clear short "
            "physical path. Store any current firearms first; empty slots are allowed. Supply its exact physical "
            "ID and explicit handoff AP cost. This changes possession, preserves recorded ownership "
            "and all ammunition, and does not equip, purchase or create a weapon."
        ),
        "parameters": [
            actor, param("recipient", "string", resolve="entity"), param("weapon_id", "string"),
            param("ap_cost", "integer", minimum=0),
            param("map_id", "string", from_state="state.spatial_scene.map.id"),
        ],
        "effects": [
            {"set": {"field": "state.last_equipment", "value": {}}},
            {"set": {"field": "state.last_equipment.status", "value": "'TRANSFER_REJECTED'"}},
            {"condition": (
                "_actor_id != _recipient_id and actor.active == true and actor.downed == false and recipient.active == true and recipient.downed == false "
                "and actor.ap >= ap_cost and 'reserve_weapons' in actor and len(actor.reserve_weapons) <= 16 "
                "and (actor.weapon == null or actor.weapon.location == 'STOWED' or actor.weapon.location == 'QUICK_ACCESS') "
                "and (recipient.weapon == null or recipient.weapon.location == 'STOWED' or recipient.weapon.location == 'QUICK_ACCESS')"
            ), "on_true": [
                {"set": {"field": "_recipient_weapons", "value": []}},
                {"set": {"field": "_recipient_weapons", "value": "recipient.reserve_weapons"},
                 "condition": "'reserve_weapons' in recipient"},
                {"set": {"field": "_remaining_weapons", "value": []}},
                {"set": {"field": "_handoff_weapon", "value": None}},
                {"set": {"field": "_handoff_count", "value": 0}},
                {"set": {"field": "_handoff_valid", "value": "len(_recipient_weapons) < 16"}},
                {"set": {"field": "_weapon_ids", "value": []}},
                {"list_push": {"list": "_weapon_ids", "value": "actor.weapon.id"},
                 "condition": "actor.weapon != null"},
                {"list_push": {"list": "_weapon_ids", "value": "recipient.weapon.id"},
                 "condition": "recipient.weapon != null"},
                {"set": {"field": "_source_weapons", "value": "actor.reserve_weapons"}},
                {"for_each": {"collection": "_source_weapons", "as": "_held_weapon", "do": [
                    {"set": {"field": "_handoff_valid", "value": False},
                     "condition": "_held_weapon.id == null or _held_weapon.id in _weapon_ids"},
                    {"list_push": {"list": "_weapon_ids", "value": "_held_weapon.id"}},
                    {"condition": "_held_weapon.id == weapon_id", "on_true": [
                        {"set": {"field": "_handoff_weapon", "value": "_held_weapon"}},
                        {"set": {"field": "_handoff_count", "value": "_handoff_count + 1"}},
                    ], "on_false": [
                        {"list_push": {"list": "_remaining_weapons", "value": "_held_weapon"}},
                    ]},
                ]}},
                {"for_each": {"collection": "_recipient_weapons", "as": "_held_weapon", "do": [
                    {"set": {"field": "_handoff_valid", "value": False},
                     "condition": "_held_weapon.id == null or _held_weapon.id in _weapon_ids"},
                    {"list_push": {"list": "_weapon_ids", "value": "_held_weapon.id"}},
                ]}},
                {"condition": (
                    "_handoff_valid == true and _handoff_count == 1 and "
                    "_handoff_weapon.controlled_by == null and "
                    "(_handoff_weapon.location == 'STOWED' or _handoff_weapon.location == 'QUICK_ACCESS')"
                ), "on_true": [
                    {"spatial_query": {"scene": "state.spatial_scene", "operation": "snapshot"}, "store_as": "_handoff_scene"},
                    {"spatial_query": {"scene": "state.spatial_scene", "operation": "path", "snapshot": "{_handoff_scene}",
                        "query": {"map_id": "{map_id}", "start": "{actor.position_cell}", "goal": "{recipient.position_cell}",
                                  "allow_diagonal": True, "corner_policy": "forbid_cutting", "include_entities": False,
                                  "max_steps": 2}}, "store_as": "_handoff_path"},
                    {"condition": "_handoff_path.found == true", "on_true": [
                        {"list_push": {"list": "_recipient_weapons", "value": "_handoff_weapon"}},
                        {"set": {"field": "{_actor_id}.reserve_weapons", "value": "_remaining_weapons"}},
                        {"set": {"field": "{_recipient_id}.reserve_weapons", "value": "_recipient_weapons"}},
                        {"decrement": {"field": "{_actor_id}.ap", "amount": "ap_cost"}},
                        {"set": {"field": "state.last_equipment", "value": {
                            "actor": "_actor_id", "recipient": "_recipient_id", "weapon": "weapon_id",
                            "ap_spent": "ap_cost", "status": "'TRANSFERRED'",
                        }}},
                    ], "on_false": []},
                ], "on_false": []},
            ], "on_false": []},
        ],
        "costs_action": True,
    }
