"""GM-adjudicated tasking of an existing autonomous drone over its actual signal link."""

from collections.abc import Callable
from typing import Any

RECEIPT_LIMIT = 256


def condition_calculations():
    """Read the same actual sensing prerequisite for the player preview and execution."""
    return [{"set": {"field": "_order_condition", "value": (
        "actor.standing_order.condition_entity_id == null or "
        "actor.standing_order.condition_entity_id == '' or "
        "actor.standing_order.condition_entity_id in actor.sensor_confirmed_targets"
    )}}]


def _status(value):
    return {"set": {"field": "state.last_drone_order.status", "value": repr(value)}}


def _receipt_effects(commit):
    """Keep bounded durable receipts separately from whichever order is currently active."""
    return [
        {"set": {"field": "_order_receipts", "value": []}},
        {"set": {"field": "_order_receipts", "value": "drone.order_receipts"},
         "condition": "drone.order_receipts != null"},
        {"set": {"field": "_matched_order", "value": "null"}},
        {"set": {"field": "_matched_order_count", "value": 0}},
        {"set": {"field": "_current_order_recorded", "value": False}},
        {"for_each": {"collection": "_order_receipts", "as": "_order_receipt", "do": [
            {"set": {"field": "_matched_order", "value": "_order_receipt"},
             "condition": "_order_receipt.id == order_id"},
            {"set": {"field": "_matched_order_count", "value": "_matched_order_count + 1"},
             "condition": "_order_receipt.id == order_id"},
            {"set": {"field": "_current_order_recorded", "value": True},
             "condition": "_order_receipt.id == drone.standing_order.id"},
        ]}},
        {"set": {"field": "_legacy_current", "value": (
            "drone.standing_order.id != null and _current_order_recorded == false"
        )}},
        {"set": {"field": "_receipt_slots", "value": 1}},
        {"set": {"field": "_receipt_slots", "value": 2},
         "condition": "_legacy_current == true"},
        {"condition": "_matched_order != null", "on_true": [
            {"condition": "_matched_order.verified == true and _matched_order_count == 1",
             "on_true": [
                {"condition": (
                    "_matched_order.operator_id == _actor_id and "
                    "_matched_order.target_id == _target_id and "
                    "_matched_order.mode == mode and _matched_order.ap_cost == ap_cost and "
                    "(_matched_order.condition_entity_id == condition_entity_id or "
                    "(_matched_order.condition_entity_id == null and condition_entity_id == ''))"
                ), "on_true": [_status("ALREADY_ASSIGNED")],
                 "on_false": [_status("ORDER_ID_CONFLICT")]},
            ], "on_false": [_status("ORDER_HISTORY_UNAVAILABLE")]},
        ], "on_false": [
            {"condition": "drone.standing_order.id == order_id", "on_true": [
                _status("ORDER_HISTORY_UNAVAILABLE"),
            ], "on_false": [
                {"condition": (
                    "actor.active == true and actor.downed == false and actor.ap >= ap_cost "
                    "and drone.active == true and drone.kind == 'AUTONOMOUS_DRONE' "
                    "and drone.controlled_by == _actor_id and drone.signal_connected == true "
                    "and len(order_id) >= 1 and len(order_id) <= 128 "
                    "and _condition_permitted == true"
                ), "on_true": [
                    {"condition": f"len(_order_receipts) + _receipt_slots <= {RECEIPT_LIMIT}",
                     "on_true": commit, "on_false": [_status("ORDER_RECEIPT_LIMIT")]},
                ], "on_false": []},
            ]},
        ]},
    ]


def _remember_order():
    return [
        {"condition": "_legacy_current == true", "on_true": [
            {"list_push": {"list": "_order_receipts", "value": {
                "id": "drone.standing_order.id", "verified": False,
            }}},
        ], "on_false": []},
        {"list_push": {"list": "_order_receipts", "value": {
            "id": "order_id", "verified": True, "operator_id": "_actor_id",
            "target_id": "_target_id", "mode": "mode", "ap_cost": "ap_cost",
            "condition_entity_id": "condition_entity_id",
        }}},
        {"set": {"field": "{_drone_id}.order_receipts", "value": "_order_receipts"}},
    ]


def specification(actor: dict, param: Callable[..., dict]) -> dict[str, Any]:
    """Commit each physical assignment and its durable identity receipt together."""
    commit = [
        *_remember_order(),
        {"decrement": {"field": "{_actor_id}.ap", "amount": "ap_cost"}},
        {"set": {"field": "{_drone_id}.standing_order", "value": {
            "id": "order_id", "operator_id": "_actor_id", "active": True,
            "authorized_fire": "mode == 'ENGAGE_CONFIRMED_TARGET'", "target": "_target_id",
            "collateral_limit": "'NO_UNCONFIRMED_TARGETS'",
            "condition_entity_id": "condition_entity_id",
        }}},
        _status("ASSIGNED"),
        {"set": {"field": "state.last_drone_order.ap_spent", "value": "ap_cost"}},
    ]
    return {
        "capability": "trigger-discipline.drones",
        "description": (
            "Assign or replace an existing autonomous drone's watch/confirmed-target fire order. "
            "Requires its actual controlling operator, live signal and explicit operator AP cost. "
            "This grants no sensor knowledge, attack, movement, new drone or ownership. "
            "An optional condition_entity_id requires CONDITIONAL ORDER: engage the confirmed "
            "target only while that additional actual entity is also sensor-confirmed; otherwise "
            "continue WATCH. It creates no detections or knowledge. Other AUTODRONES contingencies "
            "and numerical discounts remain GM-adjudicated."
            " Order IDs persist per drone across replacements/reopen, including operator, target, "
            "mode and AP cost. A matching replay spends nothing and never restores an old order; "
            "changed contents conflict. At 256 retained IDs new orders refuse without eviction. "
            "An identified legacy order lacking its receipt is reserved and cannot execute again."
        ),
        "parameters": [
            actor, param("drone", "string", resolve="entity"),
            param("target", "string", resolve="entity"), param("order_id", "string"),
            param("mode", "enum", enum=("WATCH", "ENGAGE_CONFIRMED_TARGET")),
            param("ap_cost", "integer", minimum=0),
            param("condition_entity_id", "string", required=False, default=""),
            param("order_entities", "array", items="object",
                  from_state="state.spatial_scene.entities"),
        ],
        "effects": [
            {"set": {"field": "state.last_drone_order", "value": {}}},
            _status("ORDER_REJECTED"),
            {"set": {"field": "state.last_drone_order.ap_spent", "value": 0}},
            {"set": {"field": "_condition_permitted", "value": "condition_entity_id == ''"}},
            {"for_each": {"collection": "order_entities", "as": "_order_entity", "do": [
                {"set": {"field": "_condition_permitted", "value": True},
                 "condition": "_order_entity.id == condition_entity_id"},
            ]}, "condition": (
                "condition_entity_id != '' and len(condition_entity_id) <= 128 and "
                "mode == 'ENGAGE_CONFIRMED_TARGET' and actor.specialties != null and "
                "'specialty.autodrones.entry.tactical_autonomy' in actor.specialties and "
                "'specialty.autodrones.tier_1.conditional_order' in actor.specialties"
            )},
            *_receipt_effects(commit),
        ],
        "costs_action": True,
    }


def state_schemas():
    """Validate optional per-drone receipt history using the existing package state contract."""
    text = {"type": "string", "minLength": 1}
    fields = {
        "id": {**text, "maxLength": 128}, "verified": {"type": "boolean"},
        "operator_id": text, "target_id": text,
        "mode": {"type": "string", "enum": ["WATCH", "ENGAGE_CONFIRMED_TARGET"]},
        "ap_cost": {"type": "integer", "minimum": 0},
        "condition_entity_id": {"type": "string", "maxLength": 128},
    }
    required = ["operator_id", "target_id", "mode", "ap_cost"]
    receipt = {
        "type": "object", "properties": fields, "required": ["id", "verified"],
        "additionalProperties": False,
        "allOf": [{
            "type": "object",
            "if": {"type": "object", "properties": {"verified": {
                "type": "boolean", "enum": [True]}}, "required": ["verified"]},
            "then": {"type": "object", "required": required,
                     "properties": {name: fields[name] for name in required}},
        }],
    }
    return [{"key": "entities", "references": [], "schema": {
        "type": "object", "additionalProperties": {
            "type": "object", "properties": {"order_receipts": {
                "type": "array", "items": receipt, "maxItems": RECEIPT_LIMIT, "uniqueItems": True,
            }},
        },
    }}]
