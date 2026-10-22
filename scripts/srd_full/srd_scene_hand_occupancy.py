"""Qualify factual hand anatomy and grasps against existing body/equipment facts."""

if __package__:
    from .srd_scene_relations import POSITION_EXPORTS, _snapshot, _tool_scene
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from .srd_timed_senses import _loop, _optional, _public, _replace_tools
else:
    from srd_scene_relations import POSITION_EXPORTS, _snapshot, _tool_scene
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from srd_timed_senses import _loop, _optional, _public, _replace_tools


PUBLIC_TOOLS = {"record_srd_scene_hand_occupancy"}
EXPORTS = (
    "_scene_hand_capacity",
    "_scene_hands_used",
    "_scene_free_hand_count",
    "_scene_hand_record",
)
HOLDING_SCHEMA = {
    "type": "object",
    "properties": {
        "inventory_item": {"type": "string", "minLength": 1},
        "hand_indices": {
            "type": "array",
            "items": {"type": "integer", "minimum": 0, "maximum": 7},
            "uniqueItems": True,
        },
    },
    "required": ["inventory_item", "hand_indices"],
    "additionalProperties": False,
}


def _holdings(*, private=False):
    return _parameter(
        "holdings",
        "array",
        required=not private,
        **({"default": None, "model_hidden": True} if private else {}),
        items=HOLDING_SCHEMA,
        max_items=9,
    )


def _qualifier():
    """Stored assignments never override the sole held-item, inventory, shield or body owners."""
    effects = [
        _call(
            "srd_information_scene_entity",
            {"subject": "{_actor_state_id}"},
            POSITION_EXPORTS,
        ),
        _set("_hands_body", "_info_scene_entity"),
        _require(
            "actor_state.creature_kind != null and _hands_body.metadata.is_object != true "
            "and _hands_body.metadata.plane != null",
            "Hand anatomy needs an actual admitted creature body and plane.",
        ),
        _require(
            "actor_state.held_items != null and actor_state.inventory != null",
            "Record the actual held items through their existing inventory owner first.",
        ),
        _set("_hands_held_actual", "actor_state.held_items"),
        _require("len(_hands_held_actual) <= 8", "At most eight held items are admitted here."),
        _set("_hands_form", None),
        _when(
            "actor_state.form_current_source_id != null",
            [_set("_hands_form", "actor_state.form_current_source_id")],
        ),
        _set("_hands_shield_item", None),
        _when(
            "actor_state.worn_shield != null",
            [
                _require(
                    "actor_state.worn_shield.inventory_item != null and "
                    "actor_state.worn_shield.inventory_item in actor_state.inventory",
                    "The actual wielded shield must remain in its owner's inventory.",
                ),
                _set("_hands_shield_item", "actor_state.worn_shield.inventory_item"),
            ],
        ),
        _set("_scene_hand_record", None),
        {
            "condition": "hand_capacity != null",
            "on_true": [
                _require("holdings != null", "Supply the current factual grasp assignments."),
                _set(
                    "_scene_hand_record",
                    {
                        "capacity": "hand_capacity",
                        "holdings": "holdings",
                        "footprint": "_hands_body.footprint",
                        "height": "_hands_body.height",
                        "form_source_id": None,
                        "held_items": "_hands_held_actual",
                        "worn_shield_inventory_item": None,
                    },
                ),
                *[
                    _when(
                        variable + " != null",
                        [
                            _set("_hands_record_patch", {field: variable}),
                            _set(
                                "_scene_hand_record",
                                "merge(_scene_hand_record, _hands_record_patch)",
                            ),
                        ],
                    )
                    for field, variable in (
                        ("form_source_id", "_hands_form"),
                        ("worn_shield_inventory_item", "_hands_shield_item"),
                    )
                ],
            ],
            "on_false": [
                _require("holdings == null", "Hand assignments require their anatomical capacity."),
                _when(
                    "_hands_body.metadata.srd_hand_occupancy != null",
                    [_set("_scene_hand_record", "_hands_body.metadata.srd_hand_occupancy")],
                ),
            ],
        },
        _require(
            "_scene_hand_record != null",
            "Record current anatomical hand capacity and grasps before requiring a free hand.",
        ),
        _require(
            "len(_scene_hand_record) == 7 and "
            "_scene_hand_record.capacity >= 0 and _scene_hand_record.capacity <= 8 and "
            "_scene_hand_record.height == _hands_body.height and "
            "_scene_hand_record.footprint.kind == _hands_body.footprint.kind and "
            "_scene_hand_record.form_source_id == _hands_form and "
            "_scene_hand_record.worn_shield_inventory_item == _hands_shield_item",
            "Hand anatomy or wielded shield changed; record the current facts again.",
        ),
        {
            "condition": "_hands_body.footprint.kind == 'box'",
            "on_true": [
                _require(
                    " and ".join(
                        f"_scene_hand_record.footprint.half_extents[{i}] == "
                        f"_hands_body.footprint.half_extents[{i}]"
                        for i in range(2)
                    ),
                    "The recorded hands need the exact current Box body.",
                ),
            ],
            "on_false": [
                _require(
                    "_hands_body.footprint.kind == 'circle' and "
                    "_scene_hand_record.footprint.radius == _hands_body.footprint.radius",
                    "The recorded hands need the exact current circular body.",
                ),
            ],
        },
        _require(
            "len(_scene_hand_record.held_items) == len(_hands_held_actual)",
            "Held items changed; record the current hand assignments again.",
        ),
        _set("_hands_accounted", []),
        _set("_scene_hands_used", []),
        _set("_hands_assignments", "_scene_hand_record.holdings"),
        _require("len(_hands_assignments) <= 9", "Too many grasp assignments."),
        _loop(
            "_hands_assignments",
            "_hands_assignment",
            [
                _require(
                    "len(_hands_assignment) == 2 and "
                    "len(_hands_assignment.inventory_item) <= 300 and "
                    "_hands_assignment.inventory_item in actor_state.inventory and "
                    "(_hands_assignment.inventory_item in _hands_held_actual or "
                    "_hands_assignment.inventory_item == _hands_shield_item) and "
                    "_hands_assignment.inventory_item not in _hands_accounted",
                    "Each grasp must name one actual held inventory item or wielded shield.",
                ),
                {
                    "list_push": {
                        "list": "_hands_accounted",
                        "value": "_hands_assignment.inventory_item",
                    }
                },
                _set("_hands_indices", "_hands_assignment.hand_indices"),
                _require(
                    "len(_hands_indices) >= 1 and len(_hands_indices) <= 8",
                    "A held item needs its actual bounded grasp indices.",
                ),
                _set("_hands_assignment_seen", []),
                _loop(
                    "_hands_indices",
                    "_hands_index",
                    [
                        _require(
                            "_hands_index >= 0 and _hands_index < _scene_hand_record.capacity "
                            "and _hands_index not in _hands_assignment_seen",
                            "A grasp index must be a distinct hand in the current anatomy.",
                        ),
                        {"list_push": {"list": "_hands_assignment_seen", "value": "_hands_index"}},
                        _when(
                            "_hands_index not in _scene_hands_used",
                            [{"list_push": {"list": "_scene_hands_used", "value": "_hands_index"}}],
                        ),
                    ],
                ),
            ],
        ),
        _loop(
            "_hands_held_actual",
            "_hands_held_item",
            [
                _require(
                    "_hands_held_item in _hands_accounted and "
                    "_hands_held_item in _scene_hand_record.held_items",
                    "All current held items need current factual hand assignments.",
                )
            ],
        ),
        _when(
            "_hands_shield_item != null",
            [
                _require(
                    "_hands_shield_item in _hands_accounted",
                    "Account for the actual wielded shield's occupied hand.",
                )
            ],
        ),
        _set("_scene_hand_capacity", "_scene_hand_record.capacity"),
        _set("_scene_free_hand_count", "_scene_hand_capacity - len(_scene_hands_used)"),
    ]
    return _tool_scene(
        "Qualify recorded anatomical capacity and grasps against current owners.",
        [
            _entity("actor_state"),
            _optional(
                "hand_capacity", "integer", default=None, minimum=0, maximum=8, model_hidden=True
            ),
            _holdings(private=True),
        ],
        effects,
    )


def _recorder():
    """Write only anatomy metadata after the current holding/equipment authorities qualify it."""
    tool = _tool_scene(
        "Record actual hands and grasps for this body without changing equipment or Somatic facts.",
        [
            _entity("actor_state"),
            _parameter("hand_capacity", "integer", minimum=0, maximum=8),
            _holdings(),
        ],
        [
            _call(
                "qualify_srd_scene_hand_occupancy",
                {
                    "actor_state": "{_actor_state_id}",
                    "hand_capacity": "{hand_capacity}",
                    "holdings": "{holdings}",
                },
                EXPORTS,
            ),
            _call(
                "srd_information_scene_entity",
                {"subject": "{_actor_state_id}"},
                POSITION_EXPORTS,
            ),
            _set("_hands_metadata_patch", {"srd_hand_occupancy": "_scene_hand_record"}),
            _set("_hands_metadata", "merge(_info_scene_entity.metadata, _hands_metadata_patch)"),
            *_snapshot(),
            {
                "spatial_mutation": {
                    "scene": "state.spatial_scene",
                    "map_id": "{state.spatial_scene.map.id}",
                    "expected_revision": "{_info_snapshot.geometry_revision}",
                    "target": {"kind": "entity", "id": "{_info_scene_entity.id}"},
                    "patch": {"metadata": "{_hands_metadata}"},
                },
                "store_as": "_hands_recording",
            },
        ],
    )
    tool = _public(
        tool,
        "exploration",
        "Hand occupancy",
        [
            ("Anatomical hands", "_scene_hand_capacity"),
            ("Occupied hands", "_scene_hands_used"),
            ("Free hands", "_scene_free_hand_count"),
        ],
    )
    tool["metadata"].update(visible_to_player=False, costs_action=False)
    return tool


def extend_runtime(text):
    """Emit factual tools only; Root applies the existing final ordinary pending-window fence."""
    tools = _load_runtime(text)["tools"]
    changed = {
        "qualify_srd_scene_hand_occupancy": _qualifier(),
        "record_srd_scene_hand_occupancy": _recorder(),
    }
    changed = {name: tool for name, tool in changed.items() if tools.get(name) != tool}
    return _replace_tools(text, changed) if changed else text
