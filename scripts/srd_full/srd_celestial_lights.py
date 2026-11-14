"""Earn later Celestial lights from actual caster movement and creature turn endings."""

from copy import deepcopy

from .srd_conjured_presences import (
    _bounds,
    _call,
    _cylinder_faces,
    _distance,
    _lists,
    _lookup,
    _magic,
    _subject,
)
from .srd_scene_relations import _array, _tool_scene
from .srd_spell_travel import _normalize
from .srd_timed_actor_defenses import (
    _entity,
    _load_runtime,
    _parameter,
    _require,
    _set,
    _tool,
    _when,
)
from .srd_timed_senses import _loop, _public, _push, _replace_tools

PUBLIC_TOOLS = {"move_srd_celestial_cylinder"}


def _clock():
    return [
        _set("_celestial_clock", "'exploration'"),
        _set("_celestial_turn", "state.lasting_tick"),
        _when(
            "combat.encounter_active == true",
            [
                _set("_celestial_clock", "'combat'"),
                _set("_celestial_turn", "combat.turn_sequence"),
            ],
        ),
    ]


def _claim():
    """Write only the current affected-target receipt before native rolls or consequences."""
    return _tool(
        "Claim this actual source's once-per-turn light before its native packet.",
        [_parameter("source_id"), _entity("target_state")],
        _lookup(False)
        + [
            _require(
                "_presence.mode == 'celestial'", "Only this actual Celestial has light claims."
            ),
            *_clock(),
            _when(
                "_presence.event_clock != _celestial_clock or "
                "_presence.event_turn != _celestial_turn",
                [
                    _set(
                        "state.persistent_spell_areas.{source_id}.event_clock", "_celestial_clock"
                    ),
                    _set("state.persistent_spell_areas.{source_id}.event_turn", "_celestial_turn"),
                    _set("state.persistent_spell_areas.{source_id}.event_target_ids", []),
                ],
            ),
            _set("_celestial_claimed", "state.persistent_spell_areas[source_id].event_target_ids"),
            _require(
                "_target_state_id not in _celestial_claimed",
                "This creature has already been affected by this Celestial this turn.",
            ),
            _push("_celestial_claimed", "_target_state_id"),
            _set("state.persistent_spell_areas.{source_id}.event_target_ids", "_celestial_claimed"),
        ],
    )


def _available(target):
    return [
        *_clock(),
        _set("_celestial_available", True),
        _when(
            "_presence.event_clock == _celestial_clock and "
            "_presence.event_turn == _celestial_turn and "
            f"{target} in _presence.event_target_ids",
            [_set("_celestial_available", False)],
        ),
    ]


def _sight(target):
    return [
        _call(
            "resolve_srd_conjured_subject_sight",
            {"observer_state": "{_presence.caster}", "subject": target},
            ["_illumination_point_can_see", "_illumination_point_plane"],
        ),
    ]


def _record_movement():
    """One positive committed own-turn movement earns one source-qualified continuation."""
    return _tool_scene(
        "Record a genuine own-turn positive caster movement after the sole scene owner.",
        [_entity("actor_state"), _parameter("source_id")],
        [
            _when(
                "actor_state.conjured_presence_source_id == source_id and "
                "actor_state.concentration_spell_id == 'conjure_celestial' and "
                "combat.encounter_active == true and combat.turn_active == true and "
                "combat.active_actor == _actor_state_id",
                [
                    _set("_presence", "state.persistent_spell_areas[source_id]"),
                    _when(
                        "_presence != null and _presence.profile == 'conjured_presence' and "
                        "_presence.mode == 'celestial' and _presence.active == true and "
                        "state.lasting_tick < _presence.expires_tick and "
                        "_presence.caster == _actor_state_id",
                        [
                            _call(
                                "qualify_srd_conjured_presence_focus",
                                {"source_id": "{source_id}"},
                                ["_presence_focus_live"],
                            ),
                            _when("_presence_focus_live == true", _movement_receipt()),
                        ],
                    ),
                ],
            ),
        ],
    )


def _movement_receipt():
    return [
        *_subject("{_actor_state_id}", "_celestial_caster"),
        _set("_celestial_movement_serial", 0),
        _when(
            "_presence.celestial_movement_serial != null",
            [_set("_celestial_movement_serial", "_presence.celestial_movement_serial")],
        ),
        _set("_celestial_movement_serial", "_celestial_movement_serial + 1"),
        _set(
            "_celestial_movement_receipt",
            {
                "source_id": "source_id",
                "caster": "_actor_state_id",
                "cast_id": "_presence.cast_id",
                "sequence": "_presence.sequence",
                "serial": "_celestial_movement_serial",
                "turn": "combat.turn_sequence",
                "map_id": "state.spatial_scene.map.id",
                "plane": "_celestial_caster.metadata.plane",
                "position": "_celestial_caster.position",
            },
        ),
        _set(
            "state.persistent_spell_areas.{source_id}.celestial_movement_serial",
            "_celestial_movement_serial",
        ),
        _set(
            "state.persistent_spell_areas.{source_id}.celestial_movement_receipt",
            "_celestial_movement_receipt",
        ),
    ]


def _moving_targets():
    """Translate actual bodies oppositely to the Cylinder's actual movement for exact entry."""
    return _tool_scene(
        "Derive actual Box creatures newly entered by this exact moving Cylinder.",
        [_parameter("source_id"), _array("point")],
        _lookup()
        + [
            _require("_presence.mode == 'celestial'", "Move the actual Celestial Cylinder."),
            _set("_celestial_event_targets", []),
            _set("_celestial_bodies", "state.spatial_scene.entities"),
            _loop(
                "_celestial_bodies",
                "_celestial_body",
                [
                    _when(
                        "_celestial_body.active == true and "
                        "_celestial_body.metadata.participant_ref != null and "
                        "_celestial_body.metadata.is_object != true and "
                        "_celestial_body.metadata.plane == _presence.plane",
                        [
                            *_available("_celestial_body.metadata.participant_ref"),
                            _when(
                                "_celestial_available == true",
                                [
                                    _call(
                                        "resolve_srd_celestial_body_segment",
                                        {
                                            "moving_subject": "{_celestial_body.id}",
                                            "area_id": "{source_id}",
                                            "start": "{_celestial_body.position}",
                                            "end": "{_celestial_body.position}",
                                        },
                                        {"_celestial_started_inside": "_body_hit"},
                                    ),
                                    _when(
                                        "_celestial_started_inside != true",
                                        [
                                            _set(
                                                "_celestial_relative_end",
                                                [
                                                    f"_celestial_body.position[{i}] - point[{i}] + "
                                                    f"_presence.origin[{i}]"
                                                    for i in range(3)
                                                ],
                                            ),
                                            _call(
                                                "resolve_srd_celestial_body_segment",
                                                {
                                                    "moving_subject": "{_celestial_body.id}",
                                                    "area_id": "{source_id}",
                                                    "start": "{_celestial_body.position}",
                                                    "end": "{_celestial_relative_end}",
                                                },
                                                ["_body_hit"],
                                            ),
                                            _when(
                                                "_body_hit == true",
                                                [
                                                    *_sight("{_celestial_body.id}"),
                                                    _when(
                                                        "_illumination_point_can_see == true and "
                                                        "_illumination_point_plane == "
                                                        "_presence.plane",
                                                        [
                                                            _push(
                                                                "_celestial_event_targets",
                                                                "_celestial_body.metadata.participant_ref",
                                                            ),
                                                        ],
                                                    ),
                                                ],
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _validate_choices():
    return [
        _loop(
            "healing_targets",
            "_celestial_choice",
            [
                _require(
                    "_celestial_choice in _celestial_event_targets and "
                    "_celestial_choice not in searing_targets",
                    "Healing must select an actual eligible creature only once.",
                )
            ],
        ),
        _loop(
            "searing_targets",
            "_celestial_choice",
            [
                _require(
                    "_celestial_choice in _celestial_event_targets",
                    "Searing must select an actual eligible visible creature not yet affected.",
                )
            ],
        ),
    ]


def _event_batch(initial):
    """Reuse native initial dice/healing/save/typed packets for earned optional event targets."""
    tool = deepcopy(initial)
    tool["parameters"].append(
        _parameter("event_targets", "entity_id_list", resolve="entity_list", unique_items=True)
    )
    tool["description"] = (
        "Resolve optional earned Celestial light choices with native shared save damage."
    )
    effects = tool["effects"]
    receipt = next(
        i for i, e in enumerate(effects) if e.get("set", {}).get("field") == "_presence_event_clock"
    )
    result = next(
        i for i, e in enumerate(effects) if e.get("set", {}).get("field") == "_presence_results"
    )
    del effects[receipt:result]
    for e in effects:
        if e.get("call") == "collect_srd_celestial_visible_creatures":
            e.clear()
            e.update(_set("_presence_visible", "event_targets"))
        require = e.get("require", {})
        if (
            require.get("condition")
            == "len(healing_targets) + len(searing_targets) == len(_presence_visible)"
        ):
            require["condition"] = (
                "len(healing_targets) + len(searing_targets) <= len(_presence_visible)"
            )
            require["error"] = "Choose only actual earned light recipients."
    _insert_claims(tool)
    return tool


def _insert_claims(tool):
    matches = 0
    for sequence in _lists(tool["effects"]):
        for effect in sequence:
            if effect.get("condition") == "_presence_magic_allowed == true":
                if not any(e.get("call") == "claim_srd_celestial_light" for e in effect["on_true"]):
                    effect["on_true"].insert(
                        0,
                        _call(
                            "claim_srd_celestial_light",
                            {"source_id": "{source_id}", "target_state": "{_presence_recipient}"},
                        ),
                    )
                matches += 1
    if matches != 2:
        raise ValueError("Retain both genuine Healing and Searing magic-qualified packet branches.")


def _move():
    return _public(
        _tool_scene(
            "After actual own-turn caster movement, move its Cylinder at most 30 feet and "
            "choose lights for newly entered visible creatures. No extra Action is spent.",
            [_entity("caster_state"), _parameter("source_id"), _array("point")]
            + [
                _parameter(
                    name,
                    "entity_id_list",
                    required=False,
                    default=[],
                    resolve="entity_list",
                    unique_items=True,
                    max_items=64,
                )
                for name in ("healing_targets", "searing_targets")
            ],
            _lookup()
            + [
                _require(
                    "_presence.mode == 'celestial' and _presence.caster == _caster_state_id and "
                    "combat.encounter_active == true and combat.turn_active == true and "
                    "combat.active_actor == _caster_state_id",
                    "The actual caster must move this Cylinder on its own active turn.",
                ),
                _require(
                    "_presence.celestial_movement_receipt != null",
                    "Move the actual caster before moving its Cylinder.",
                ),
                _set("_celestial_receipt", "_presence.celestial_movement_receipt"),
                *_subject("{_caster_state_id}", "_celestial_caster"),
                *_magic("{_caster_state_id}", "{_caster_state_id}", "{_presence.spell_level}"),
                _require(
                    "_presence_magic_allowed == true",
                    "Antimagic suppresses this actual caster's magical Cylinder control.",
                ),
                _require(
                    "_celestial_receipt.source_id == source_id and "
                    "_celestial_receipt.caster == _caster_state_id and "
                    "_celestial_receipt.cast_id == _presence.cast_id and "
                    "_celestial_receipt.sequence == _presence.sequence and "
                    "_celestial_receipt.serial == _presence.celestial_movement_serial",
                    "Use this exact source's unspent actual caster movement receipt.",
                ),
                _require(
                    "_celestial_receipt.turn == combat.turn_sequence and "
                    "_celestial_receipt.map_id == state.spatial_scene.map.id and "
                    "_celestial_receipt.plane == _presence.plane and "
                    "_celestial_caster.metadata.plane == _presence.plane",
                    "Use this exact source's unspent actual caster movement receipt.",
                ),
                *[
                    _require(
                        f"_celestial_receipt.position[{i}] == _celestial_caster.position[{i}]",
                        "The caster must remain at this actual movement's committed endpoint.",
                    )
                    for i in range(3)
                ],
                *_distance("{_presence.origin}", "{point}", "_celestial_distance"),
                _require(
                    "_celestial_distance <= 30",
                    "The actual Cylinder movement cannot exceed30 feet.",
                ),
                _require(
                    "_presence.cylinder_origin_face == 'top' or "
                    "_presence.cylinder_origin_face == 'bottom'",
                    "Move only a source with an authentic retained circular origin face.",
                ),
                *_bounds("point", radius=10),
                *_cylinder_faces("point", "_presence.cylinder_origin_face"),
                _require(
                    "_presence_cylinder_min >= state.spatial_scene.map.bounds.min[2] and "
                    "_presence_cylinder_max <= state.spatial_scene.map.bounds.max[2]",
                    "The complete actual Cylinder must remain within the admitted map.",
                ),
                _call(
                    "collect_srd_celestial_moving_targets",
                    {"source_id": "{source_id}", "point": "{point}"},
                    ["_celestial_event_targets"],
                ),
                *_validate_choices(),
                _set("state.persistent_spell_areas.{source_id}.celestial_movement_receipt", None),
                _set("state.persistent_spell_areas.{source_id}.origin", "point"),
                _set(
                    "state.persistent_spell_areas.{source_id}.cylinder_min",
                    "_presence_cylinder_min",
                ),
                _set(
                    "state.persistent_spell_areas.{source_id}.cylinder_max",
                    "_presence_cylinder_max",
                ),
                _call(
                    "resolve_srd_celestial_event_lights",
                    {
                        "caster_state": "{_caster_state_id}",
                        "source_id": "{source_id}",
                        "healing_targets": "{healing_targets}",
                        "searing_targets": "{searing_targets}",
                        "event_targets": "{_celestial_event_targets}",
                    },
                    ["_presence_results"],
                ),
            ],
        ),
        "magic",
        "Move conjured Celestial",
        [("Light recipients", "_presence_results")],
    )


def _end_qualify():
    return _tool_scene(
        "Qualify a real creature ending its own active turn inside this visible Cylinder.",
        [_entity("target_state"), _parameter("source_id")],
        _lookup()
        + [
            _require("_presence.mode == 'celestial'", "Use an actual Celestial source."),
            _require(
                "combat.turn_active == true and combat.active_actor == _target_state_id",
                "Only the actual ending creature earns this end-turn light.",
            ),
            *_subject("{_target_state_id}", "_celestial_ending_body"),
            *_available("_target_state_id"),
            _require(
                "_celestial_available == true",
                "This creature has already had this light this turn.",
            ),
            _call(
                "resolve_srd_celestial_body_segment",
                {
                    "moving_subject": "{_celestial_ending_body.id}",
                    "area_id": "{source_id}",
                    "start": "{_celestial_ending_body.position}",
                    "end": "{_celestial_ending_body.position}",
                },
                ["_body_hit"],
            ),
            _require("_body_hit == true", "The actual ending body must intersect this Cylinder."),
            *_sight("{_celestial_ending_body.id}"),
            _require(
                "_illumination_point_can_see == true and "
                "_illumination_point_plane == _presence.plane",
                "The actual source caster must see this ending creature.",
            ),
            _set("_celestial_event_targets", ["_target_state_id"]),
        ],
    )


def _end_choices():
    parameter = _parameter(
        "celestial_light_choices",
        "array",
        required=False,
        default=[],
        max_items=8,
        items={
            "type": "object",
            "required": ["source_id", "light"],
            "additionalProperties": False,
            "properties": {
                "source_id": {"type": "string", "minLength": 1},
                "light": {"type": "string", "enum": ["healing", "searing"]},
            },
        },
        description="Optional source caster choices for this actual creature ending its turn; "
        "membership, visibility and once-per-turn eligibility are derived.",
    )
    qualify = _call(
        "qualify_srd_celestial_end_turn",
        {"target_state": "{_target_state_id}", "source_id": "{_celestial_end_choice.source_id}"},
        ["_presence", "_celestial_event_targets"],
    )
    return _tool_scene(
        "Validate all optional actual end-turn light intentions before any native packet.",
        [_entity("target_state"), deepcopy(parameter)],
        [
            _set("_celestial_end_rows", []),
            _set("_celestial_choice_sources", []),
            _loop(
                "celestial_light_choices",
                "_celestial_end_choice",
                [
                    _require(
                        "_celestial_end_choice.source_id not in _celestial_choice_sources",
                        "Choose at most one light from each actual source for this creature.",
                    ),
                    _push("_celestial_choice_sources", "_celestial_end_choice.source_id"),
                    deepcopy(qualify),
                ],
            ),
            _loop(
                "celestial_light_choices",
                "_celestial_end_choice",
                [
                    deepcopy(qualify),
                    _set("_celestial_healing", []),
                    _set("_celestial_searing", []),
                    {
                        "condition": "_celestial_end_choice.light == 'healing'",
                        "on_true": [_push("_celestial_healing", "_target_state_id")],
                        "on_false": [_push("_celestial_searing", "_target_state_id")],
                    },
                    _call(
                        "resolve_srd_celestial_event_lights",
                        {
                            "caster_state": "{_presence.caster}",
                            "source_id": "{_celestial_end_choice.source_id}",
                            "healing_targets": "{_celestial_healing}",
                            "searing_targets": "{_celestial_searing}",
                            "event_targets": "{_celestial_event_targets}",
                        },
                        ["_presence_results"],
                    ),
                    _loop(
                        "_presence_results",
                        "_celestial_light_result",
                        [
                            {
                                "condition": "_celestial_light_result.light == 'searing'",
                                "on_true": [
                                    _push(
                                        "_celestial_end_rows",
                                        {
                                            "label": "'Conjured Celestial damage'",
                                            "value": "_celestial_light_result.damage",
                                        },
                                    ),
                                ],
                                "on_false": [
                                    _push(
                                        "_celestial_end_rows",
                                        {
                                            "label": "'Conjured Celestial'",
                                            "value": "'Received Healing Light'",
                                        },
                                    ),
                                ],
                            },
                        ],
                    ),
                ],
            ),
        ],
    ), parameter


def bind_runtime_consumers(text):
    """Bind after the initial family; preserve native turn, position and source owners."""
    runtime = _load_runtime(text)
    tools = runtime["tools"]
    initial = deepcopy(tools["resolve_srd_celestial_initial_lights"])
    receipt = next(
        e for e in initial["effects"] if e.get("set", {}).get("field") == "_presence_event_patch"
    )
    receipt["set"]["value"]["event_target_ids"] = []
    _insert_claims(initial)
    end_choices, parameter = _end_choices()
    emitted = {
        "claim_srd_celestial_light": _claim(),
        "resolve_srd_celestial_initial_lights": initial,
        "resolve_srd_celestial_event_lights": _event_batch(initial),
        "record_srd_celestial_caster_movement": _record_movement(),
        "collect_srd_celestial_moving_targets": _moving_targets(),
        "move_srd_celestial_cylinder": _move(),
        "qualify_srd_celestial_end_turn": _end_qualify(),
        "resolve_srd_celestial_end_turn": end_choices,
    }
    for private_parameter in emitted["move_srd_celestial_cylinder"]["parameters"]:
        if private_parameter["name"] == "lasting_state":
            private_parameter["model_hidden"] = True
    if "record_srd_celestial_caster_movement" not in tools:
        movement = deepcopy(tools["move_srd_scene_creature_node"])
        movement["effects"].append(
            _when(
                "lasting_state.has_conjured_presences == true and _scene_movement_distance > 0 "
                "and actor_state.conjured_presence_source_id != null and "
                "actor_state.concentration_spell_id == 'conjure_celestial'",
                [
                    _call(
                        "record_srd_celestial_caster_movement",
                        {
                            "actor_state": "{_actor_state_id}",
                            "source_id": "{actor_state.conjured_presence_source_id}",
                        },
                    )
                ],
            )
        )
        emitted["move_srd_scene_creature_node"] = movement
        end = deepcopy(tools["end_combat_turn"])
        end["parameters"].append(parameter)
        boundary = next(
            i for i, e in enumerate(end["effects"]) if e.get("call") == "end_srd_blink_turn"
        )
        end["effects"].insert(
            boundary,
            _when(
                "len(celestial_light_choices) > 0",
                [
                    _call(
                        "resolve_srd_celestial_end_turn",
                        {
                            "target_state": "{_actor_id}",
                            "celestial_light_choices": "{celestial_light_choices}",
                        },
                        ["_celestial_end_rows"],
                    ),
                    _loop(
                        "_celestial_end_rows",
                        "_celestial_end_row",
                        [_push("_end_turn_rows", "_celestial_end_row")],
                    ),
                ],
            ),
        )
        emitted["end_combat_turn"] = end
    _normalize(emitted)
    return _replace_tools(text, emitted)
