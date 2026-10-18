"""Freezing Sphere retains one paid globe and source-linked ice using native owners."""

from copy import deepcopy

if __package__:
    from .srd_control_behaviors import _each, _push
    from .srd_reaction_spells import _allocate
    from .srd_scene_relations import POSITION_EXPORTS
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _tool,
    )
    from .srd_timed_senses import _optional, _public, _replace_tools
else:
    from srd_control_behaviors import _each, _push
    from srd_reaction_spells import _allocate
    from srd_scene_relations import POSITION_EXPORTS
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _tool,
    )
    from srd_timed_senses import _optional, _public, _replace_tools


SPELL = "freezing_sphere"
AREA = "cast_spell_instantaneous_area_save_damage"
NATIVE_AREA = "resolve_srd_freezing_sphere_native_area"
LOOKUP = "find_srd_freezing_sphere_globe"
EXPLODE = "explode_srd_freezing_sphere_globe"
REFRESH = "refresh_srd_freezing_sphere_sources"
WATER = "install_srd_freezing_sphere_ice"
PUBLIC_TOOLS = {
    "handle_srd_freezing_sphere_globe",
    "resolve_srd_freezing_sphere_globe",
    "escape_srd_freezing_sphere_ice",
}
INTENTS = (
    _optional("freezing_sphere_hold", "boolean", False),
    _optional(
        "freezing_globe_point", "array", None, items={"type": "number"}, min_items=3, max_items=3
    ),
    _optional("freezing_water", "string", ""),
    _optional(
        "freezing_surface_swimmers",
        "entity_id_list",
        [],
        resolve="entity_list",
        max_items=64,
        unique_items=True,
    ),
)


def _lists(effects):
    yield effects
    for effect in effects:
        for key in ("on_true", "on_false"):
            if isinstance(effect.get(key), list):
                yield from _lists(effect[key])
        if "for_each" in effect:
            yield from _lists(effect["for_each"]["do"])


def _branch(condition, yes, no):
    return {"condition": condition, "on_true": yes, "on_false": no}


def _when(condition, effects):
    return {"condition": condition, "on_true": effects}


def _source():
    """Reuse an allocated paid cast, allocating only if the ordinary family has none."""
    return [
        _set("_freezing_cast_id", "spell_cast_id"),
        _when(
            "spell_cast_id == ''",
            [
                _call(
                    "begin_srd_spell_source_instance",
                    {
                        "caster_state": "{_caster_state_id}",
                        "spell": SPELL,
                        "slot_level": "{slot_level}",
                    },
                    ["_spell_source_cast_id"],
                ),
                _set("_freezing_cast_id", "_spell_source_cast_id"),
            ],
        ),
        _require(
            "caster_state.last_spell_source_cast_id == _freezing_cast_id and "
            "caster_state.last_spell_source_spell == 'freezing_sphere'",
            "Use this actual paid Freezing Sphere casting.",
        ),
    ]


def _retain(area):
    """The original native formals retain DC/scaling; release never recasts or revalidates cost."""
    saved = {
        p["name"]: (
            "_caster_state_id"
            if p["name"] == "caster_state"
            else "_caster_state_id"
            if p["name"] == "spell_cast_owner"
            else "_freezing_cast_id"
            if p["name"] == "spell_cast_id"
            else "slot_level"
            if p["name"] == "actual_spell_level"
            else "_freezing_witnesses"
            if p["name"] == "witness_state_ids"
            else None
            if p["name"] == "primary_target_id"
            else p["name"]
        )
        for p in area["parameters"]
        if p["name"]
        not in {"lasting_state", "targets", "half_cover_targets", "three_quarters_cover_targets"}
    }
    return [
        *_source(),
        _set("_freezing_witnesses", []),
        _when("witness_state_ids != null", [_set("_freezing_witnesses", "witness_state_ids")]),
        _set("_spell_casting_ability", "casting_ability"),
        _set("_spell_casting_modifier", "casting_modifier"),
        _set("_spell_proficiency_bonus", "2 + ((casting_scaling_level - 1) // 4)"),
        _set("_spell_save_dc", "casting_save_dc"),
        _set("_spell_damage", None),
        _set("_spell_damage_total", 0),
        _set("_spell_damage_dice", 0),
        _set("_spell_area_target_count", 0),
        _set("_spell_area_last_damage_amount", 0),
        _set("_spell_area_last_save", None),
        _set("_spell_area_last_save_outcome", None),
        _set("_spell_area_last_save_advantage", False),
        _set("_spell_area_last_save_disadvantage", False),
        _when("state.lasting_tick == null", [_set("state.lasting_tick", 0)]),
        _when("state.turn_effects == null", [_set("state.turn_effects", [])]),
        _require("len(state.turn_effects) < 128", "Resolve existing lasting effects first."),
        _set("_freezing_origin", None),
        _set("_freezing_plane", None),
        _set("_freezing_offset", None),
        _when(
            "state.spatial_scene != null",
            [
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{_caster_state_id}"},
                    POSITION_EXPORTS,
                ),
                _require(
                    "freezing_globe_point != null and _info_scene_entity.footprint.kind == 'box'",
                    "Record the globe's actual hand point inside the admitted caster body.",
                ),
                *_require_contains(
                    "_info_scene_entity",
                    "freezing_globe_point",
                    "The held globe point lies outside its actual holder body.",
                ),
                _set("_freezing_origin", "freezing_globe_point"),
                _set("_freezing_plane", "'material'"),
                _when(
                    "_info_scene_entity.metadata.plane != null",
                    [_set("_freezing_plane", "_info_scene_entity.metadata.plane")],
                ),
                _set(
                    "_freezing_offset",
                    [f"freezing_globe_point[{i}] - _info_position[{i}]" for i in range(3)],
                ),
            ],
        ),
        _set(
            "_freezing_globe",
            {
                "kind": "'freezing_sphere_globe'",
                "spell": "'freezing_sphere'",
                "cast_id": "_freezing_cast_id",
                "source_caster_id": "_caster_state_id",
                "caster": "_caster_state_id",
                "target": "_caster_state_id",
                "holder": "_caster_state_id",
                "location": "'In caster hand'",
                "origin": "_freezing_origin",
                "plane": "_freezing_plane",
                "held_offset": "_freezing_offset",
                "created_tick": "state.lasting_tick",
                "globe_expires_tick": "state.lasting_tick + 20",
                "expires_tick": None,
                "owner": "_caster_state_id",
                "phase": "'time'",
                "turn": 0,
                "magnitude": 0,
                "dice_count": 0,
                "damage_die": "''",
                "damage_type": "'cold'",
                "label": "'Freezing Sphere globe'",
                "native": saved,
            },
        ),
        _set("_freezing_rows", "state.turn_effects"),
        _push("_freezing_rows", "_freezing_globe"),
        _set("state.turn_effects", "_freezing_rows"),
        _set("state.has_freezing_sphere_sources", True),
        _set("_spell_summary_rows", [{"label": "'Globe'", "value": "_freezing_cast_id"}]),
        _set(
            "_spell_detail_rows",
            [
                {
                    "label": "'Effect'",
                    "value": "'Globe retained for 1 minute: throw, sling, give away or set down.'",
                }
            ],
        ),
    ]


def _lookup():
    return _tool(
        "Find one genuine still-unshattered paid globe.",
        [_parameter("source_id")],
        [
            _set("_freezing_globe", None),
            _set("_freezing_rows", []),
            _when("state.turn_effects != null", [_set("_freezing_rows", "state.turn_effects")]),
            _each(
                "_freezing_rows",
                "_freezing_candidate",
                [
                    _when(
                        "_freezing_candidate.kind == 'freezing_sphere_globe' and "
                        "_freezing_candidate.cast_id == source_id",
                        [
                            _require("_freezing_globe == null", "The globe source is ambiguous."),
                            _set("_freezing_globe", "_freezing_candidate"),
                        ],
                    ),
                ],
            ),
            _require("_freezing_globe != null", "No unshattered globe has this cast ID."),
        ],
    )


def _contains(body, point):
    return " and ".join(
        [
            f"{point}[0] >= {body}.position[0] - {body}.footprint.half_extents[0]",
            f"{point}[0] <= {body}.position[0] + {body}.footprint.half_extents[0]",
            f"{point}[1] >= {body}.position[1] - {body}.footprint.half_extents[1]",
            f"{point}[1] <= {body}.position[1] + {body}.footprint.half_extents[1]",
            f"{point}[2] >= {body}.position[2]",
            f"{point}[2] <= {body}.position[2] + {body}.height",
        ]
    )


def _require_contains(body, point, error):
    """Keep the same ordered XY and Z bounds within the engine's expression limit."""
    parts = _contains(body, point).split(" and ")
    return [
        _require(" and ".join(parts[:4]), error),
        _require(" and ".join(parts[4:]), error),
    ]


def _replace(value=None):
    effects = [_set("_freezing_kept", []), _set("_freezing_rows", "state.turn_effects")]
    effects.append(
        _each(
            "_freezing_rows",
            "_freezing_old",
            [
                _branch(
                    "_freezing_old.kind == 'freezing_sphere_globe' and "
                    "_freezing_old.cast_id == source_id",
                    [] if value is None else [_push("_freezing_kept", value)],
                    [_push("_freezing_kept", "_freezing_old")],
                ),
            ],
        )
    )
    return effects + [_set("state.turn_effects", "_freezing_kept")]


def _explode():
    """Save a genuine explosion as an existing pending choice before asking for area facts."""
    return _tool(
        "Retain the original globe explosion once, without a damage or casting verdict.",
        [_parameter("source_id"), _parameter("reason")],
        [
            _call(LOOKUP, {"source_id": "{source_id}"}, ["_freezing_globe"]),
            _when(
                "state.spatial_scene != null and _freezing_globe.holder != null",
                [
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_freezing_globe.holder}"},
                        POSITION_EXPORTS,
                    ),
                    _require(
                        "_freezing_globe.held_offset != null and "
                        "_info_scene_entity.footprint.kind == 'box'",
                        "The held globe needs its actual recorded point in the current body.",
                    ),
                    _set(
                        "_freezing_origin",
                        [
                            f"_info_position[{i}] + _freezing_globe.held_offset[{i}]"
                            for i in range(3)
                        ],
                    ),
                    *_require_contains(
                        "_info_scene_entity",
                        "_freezing_origin",
                        "Requalify the actual globe point after a body change.",
                    ),
                    _set("_freezing_plane", "'material'"),
                    _when(
                        "_info_scene_entity.metadata.plane != null",
                        [_set("_freezing_plane", "_info_scene_entity.metadata.plane")],
                    ),
                    _set(
                        "_freezing_location_patch",
                        {
                            "origin": "_freezing_origin",
                            "plane": "_freezing_plane",
                        },
                    ),
                    _set("_freezing_globe", "merge(_freezing_globe, _freezing_location_patch)"),
                ],
            ),
            *_allocate("freezing_sphere", "_freezing_globe.caster", "_freezing_globe.caster"),
            _set("_freezing_explosion_tick", "state.lasting_tick"),
            _when(
                "reason == 'expiry'",
                [_set("_freezing_explosion_tick", "_freezing_globe.globe_expires_tick")],
            ),
            _set("state.pending_srd_actions.{_pending_action_id}.stage", "'freezing_sphere_area'"),
            _set("state.pending_srd_actions.{_pending_action_id}.context.globe", "_freezing_globe"),
            _set(
                "state.pending_srd_actions.{_pending_action_id}.context.explosion_tick",
                "_freezing_explosion_tick",
            ),
            _set("state.pending_srd_actions.{_pending_action_id}.context.reason", "reason"),
            *_replace(),
            _set("_freezing_pending_id", "_pending_action_id"),
        ],
    )


def _handle():
    """Physical handoff/location facts qualify the saved holder and actual range."""
    return _public(
        _tool(
            "Handle the actual paid globe before its one-minute deadline.",
            [
                _entity("actor_state"),
                _parameter("source_id"),
                _parameter("operation", "enum", enum_values=["give", "set_down", "throw", "sling"]),
                _optional("recipient_state", "string", None, resolve="entity"),
                _optional("distance", "number", 0, minimum=0),
                _optional("location", "string", ""),
                _optional(
                    "point", "array", None, items={"type": "number"}, min_items=3, max_items=3
                ),
            ],
            [
                _call(LOOKUP, {"source_id": "{source_id}"}, ["_freezing_globe"]),
                _require(
                    "state.lasting_tick < _freezing_globe.globe_expires_tick",
                    "This globe has reached its one-minute explosion; resolve the clock.",
                ),
                _require(
                    "_freezing_globe.holder == _actor_state_id and "
                    "actor_state.is_alive == true and "
                    "not ('incapacitated' in actor_state.conditions)",
                    "Only the actual capable holder may handle this globe.",
                ),
                _set("_freezing_patch", {}),
                _set("_freezing_distance", "distance"),
                _when(
                    "state.spatial_scene != null and operation != 'give'",
                    [
                        _require("point != null", "Record the actual globe destination point."),
                        _call(
                            "srd_information_scene_entity",
                            {"subject": "{_actor_state_id}"},
                            POSITION_EXPORTS,
                        ),
                        _call(
                            "measure_srd_information_distance",
                            {"start": "{_info_position}", "end": "{point}"},
                            ["_scene_distance"],
                        ),
                        _set("_freezing_distance", "_scene_distance"),
                    ],
                ),
                _when(
                    "operation == 'give'",
                    [
                        _require(
                            "recipient_state != null and recipient_state.is_object != true and "
                            "distance <= 5",
                            "Give the globe to an actual creature within reach.",
                        ),
                        _when(
                            "state.spatial_scene != null",
                            [
                                _call(
                                    "resolve_srd_scene_relation",
                                    {
                                        "actor_state": "{_actor_state_id}",
                                        "target_state": "{_recipient_state_id}",
                                    },
                                    [
                                        "_scene_distance",
                                        "_scene_same_plane",
                                        "_scene_line_of_sight",
                                    ],
                                ),
                                _require(
                                    "_scene_same_plane == true and _scene_distance <= 5 and "
                                    "_scene_line_of_sight == true",
                                    "The actual recipient is out of reach.",
                                ),
                                _call(
                                    "srd_information_scene_entity",
                                    {"subject": "{_recipient_state_id}"},
                                    POSITION_EXPORTS,
                                ),
                                _require(
                                    "point != null and _info_scene_entity.footprint.kind == 'box'",
                                    "Record the receiving hand point inside its body.",
                                ),
                                *_require_contains(
                                    "_info_scene_entity",
                                    "point",
                                    "Record the receiving hand point inside its body.",
                                ),
                            ],
                        ),
                        _set(
                            "_freezing_patch",
                            {
                                "holder": "_recipient_state_id",
                                "held_offset": None,
                                "location": "'In holder hand'",
                            },
                        ),
                        _when(
                            "state.spatial_scene != null",
                            [
                                _set("_freezing_plane", "'material'"),
                                _when(
                                    "_info_scene_entity.metadata.plane != null",
                                    [_set("_freezing_plane", "_info_scene_entity.metadata.plane")],
                                ),
                                _set(
                                    "_freezing_patch",
                                    {
                                        "holder": "_recipient_state_id",
                                        "location": "'In holder hand'",
                                        "origin": "point",
                                        "plane": "_freezing_plane",
                                        "held_offset": [
                                            f"point[{i}] - _info_position[{i}]" for i in range(3)
                                        ],
                                    },
                                ),
                            ],
                        ),
                    ],
                ),
                _when(
                    "operation == 'set_down'",
                    [
                        _require("location != ''", "Describe the actual set-down location."),
                        _require(
                            "_freezing_distance <= 5", "Set the globe down within actual reach."
                        ),
                        _set(
                            "_freezing_patch",
                            {"holder": None, "location": "location", "origin": "point"},
                        ),
                    ],
                ),
                _when(
                    "operation == 'throw'",
                    [
                        _require(
                            "_freezing_distance <= 40 and location != ''",
                            "Throw to an actual impact within 40 feet.",
                        ),
                        _set(
                            "_freezing_patch",
                            {"holder": None, "location": "location", "origin": "point"},
                        ),
                    ],
                ),
                _when(
                    "operation == 'sling'",
                    [
                        _require(
                            "'sling' in actor_state.inventory and "
                            "'sling' in actor_state.held_items and "
                            "_freezing_distance <= "
                            "definitions.weapons.sling.normal_range and location != ''",
                            "Hurl with an actual Sling to its printed normal range.",
                        ),
                        _set(
                            "_freezing_patch",
                            {"holder": None, "location": "location", "origin": "point"},
                        ),
                    ],
                ),
                _set("_freezing_updated", "merge(_freezing_globe, _freezing_patch)"),
                *_replace("_freezing_updated"),
                _when(
                    "operation == 'throw' or operation == 'sling'",
                    [
                        _call(
                            EXPLODE,
                            {"source_id": "{source_id}", "reason": "impact"},
                            ["_freezing_pending_id"],
                        ),
                    ],
                ),
            ],
        ),
        "spells",
        "Freezing Sphere globe",
        [("Source", "source_id"), ("Handling", "operation"), ("Location", "location")],
    )


def _water():
    """Factual surface swimmers receive exact timed restraint; no save or terrain is invented."""
    return _tool(
        "Freeze admitted water30ft square/6in deep and trap its actual surface swimmers.",
        [
            _entity("caster_state"),
            _parameter("spell_cast_id"),
            _parameter("slot_level", "integer"),
            _parameter("save_dc", "integer"),
            _optional("water", "string", ""),
            _optional("effect_tick", "number", -1, model_hidden=True),
            _parameter(
                "swimmers", "entity_id_list", resolve="entity_list", max_items=64, unique_items=True
            ),
        ],
        [
            _require("water != '' or len(swimmers) == 0", "Surface swimmers require actual water."),
            _set("_freezing_water_rows", []),
            _set("_freezing_ice_tick", "state.lasting_tick"),
            _when("effect_tick >= 0", [_set("_freezing_ice_tick", "effect_tick")]),
            _set("_freezing_ice_live", "state.lasting_tick < _freezing_ice_tick + 20"),
            _when(
                "water != ''",
                [
                    _set("_freezing_ice_links", []),
                    _each(
                        "swimmers",
                        "_freezing_swimmer",
                        [
                            _when(
                                "_freezing_ice_live == true",
                                [
                                    _call(
                                        "install_srd_timed_condition_source",
                                        {
                                            "target_state": "{_freezing_swimmer}",
                                            "lasting_state": "{_lasting_state_id}",
                                            "spell": SPELL,
                                            "condition": "restrained",
                                            "save_dc": "{save_dc}",
                                            "duration_rounds": 10,
                                            "repeat_save_timing": "none",
                                            "save_ability": "strength",
                                            "caster_state": "{_caster_state_id}",
                                            "spell_cast_id": "{spell_cast_id}",
                                            "actual_spell_level": "{slot_level}",
                                            "spell_cast_owner": "{_caster_state_id}",
                                        },
                                        ["_timed_condition_source_id"],
                                    ),
                                    _set(
                                        "state.timed_condition_sources.{_timed_condition_source_id}.expires_tick",
                                        "_freezing_ice_tick + 20",
                                    ),
                                    _push(
                                        "_freezing_ice_links",
                                        {
                                            "actor": "_freezing_swimmer",
                                            "condition_id": "_timed_condition_source_id",
                                        },
                                    ),
                                ],
                            )
                        ],
                    ),
                    _when(
                        "state.freezing_sphere_ice == null", [_set("state.freezing_sphere_ice", {})]
                    ),
                    _when(
                        "state.freezing_sphere_ice_ids == null",
                        [_set("state.freezing_sphere_ice_ids", [])],
                    ),
                    _set("_freezing_ice_ids", "state.freezing_sphere_ice_ids"),
                    _when(
                        "spell_cast_id not in _freezing_ice_ids",
                        [_push("_freezing_ice_ids", "spell_cast_id")],
                    ),
                    _set("state.freezing_sphere_ice_ids", "_freezing_ice_ids"),
                    _set(
                        "state.freezing_sphere_ice.{spell_cast_id}",
                        {
                            "cast_id": "spell_cast_id",
                            "caster": "_caster_state_id",
                            "water": "water",
                            "square_feet": 30,
                            "depth_inches": 6,
                            "created_tick": "_freezing_ice_tick",
                            "expires_tick": "_freezing_ice_tick + 20",
                            "save_dc": "save_dc",
                            "links": "_freezing_ice_links",
                            "active": "_freezing_ice_live",
                        },
                    ),
                    _when(
                        "_freezing_ice_live == true",
                        [
                            _set("state.has_freezing_sphere_sources", True),
                        ],
                    ),
                    _push(
                        "_freezing_water_rows",
                        {
                            "label": "'Ice'",
                            "value": "'Ice30ft square/6in deep,1minute; swimmers Restrained.'",
                        },
                    ),
                    _when(
                        "_freezing_ice_live != true",
                        [
                            _set(
                                "_freezing_water_rows",
                                [
                                    {
                                        "label": "'Ice'",
                                        "value": "'The30ft square of6in-deep ice has thawed.'",
                                    }
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _refresh():
    """Clock expiry creates a fenced saved choice, and exact ice expiry removes only its sources."""
    return _tool(
        "Settle real globe/ice clocks before ordinary turn-effect retirement.",
        [],
        [
            _set("state.has_freezing_sphere_sources", False),
            _set("_freezing_clock_rows", []),
            _when(
                "state.turn_effects != null", [_set("_freezing_clock_rows", "state.turn_effects")]
            ),
            _each(
                "_freezing_clock_rows",
                "_freezing_clock_row",
                [
                    _when(
                        "_freezing_clock_row.kind == 'freezing_sphere_globe' and "
                        "state.lasting_tick < _freezing_clock_row.globe_expires_tick",
                        [_set("state.has_freezing_sphere_sources", True)],
                    ),
                    _when(
                        "_freezing_clock_row.kind == 'freezing_sphere_globe' and "
                        "state.lasting_tick >= _freezing_clock_row.globe_expires_tick",
                        [
                            _call(
                                EXPLODE,
                                {"source_id": "{_freezing_clock_row.cast_id}", "reason": "expiry"},
                            ),
                        ],
                    ),
                ],
            ),
            _when(
                "state.freezing_sphere_ice != null",
                [
                    _set("_freezing_live_ice_ids", []),
                    _set("_freezing_ice_ids", "state.freezing_sphere_ice_ids"),
                    _each(
                        "_freezing_ice_ids",
                        "_freezing_ice_id",
                        [
                            _set("_freezing_ice", "state.freezing_sphere_ice[_freezing_ice_id]"),
                            _when(
                                "_freezing_ice.active == true and state.lasting_tick >= "
                                "_freezing_ice.expires_tick",
                                [
                                    _set("_freezing_links", "_freezing_ice.links"),
                                    _each("_freezing_links", "_freezing_link", _retire_link()),
                                    _set(
                                        "state.freezing_sphere_ice.{_freezing_ice_id}.active", False
                                    ),
                                ],
                            ),
                            _when(
                                "state.freezing_sphere_ice[_freezing_ice_id].active == true",
                                [
                                    _set("state.has_freezing_sphere_sources", True),
                                    _push("_freezing_live_ice_ids", "_freezing_ice_id"),
                                ],
                            ),
                        ],
                    ),
                    _set("state.freezing_sphere_ice_ids", "_freezing_live_ice_ids"),
                ],
            ),
        ],
    )


def _retire_link():
    return [
        _call(
            "retire_srd_timed_condition_source",
            {"lasting_state": "{_lasting_state_id}", "source_id": "{_freezing_link.condition_id}"},
        ),
        _call(
            "refresh_srd_timed_condition_target",
            {"lasting_state": "{_lasting_state_id}", "target_state": "{_freezing_link.actor}"},
        ),
    ]


def _resolve(area):
    native_params = {
        p["name"]: "{_freezing_event.context.globe.native." + p["name"] + "}"
        for p in area["parameters"]
        if p["name"] != "lasting_state"
    }
    native_params.update(
        targets="{targets}",
        half_cover_targets="{half_cover_targets}",
        three_quarters_cover_targets="{three_quarters_cover_targets}",
    )
    return _public(
        _tool(
            "Resolve the one saved explosion with native area recipients, saves and Cold.",
            [
                _parameter("pending_action_id"),
                _parameter(
                    "targets",
                    "entity_id_list",
                    resolve="entity_list",
                    min_items=0,
                    max_items=64,
                    unique_items=True,
                ),
                _optional(
                    "half_cover_targets",
                    "entity_id_list",
                    [],
                    resolve="entity_list",
                    max_items=64,
                    unique_items=True,
                ),
                _optional(
                    "three_quarters_cover_targets",
                    "entity_id_list",
                    [],
                    resolve="entity_list",
                    max_items=64,
                    unique_items=True,
                ),
                *deepcopy(INTENTS[2:]),
            ],
            [
                _set("_freezing_event", "state.pending_srd_actions[pending_action_id]"),
                _require(
                    "_freezing_event.owner_kind == 'freezing_sphere' and "
                    "_freezing_event.status == 'open' and "
                    "_freezing_event.stage == 'freezing_sphere_area'",
                    "Choose the genuine pending globe explosion.",
                ),
                _set("_freezing_stack", "state.pending_srd_action_stack"),
                _require(
                    "len(_freezing_stack) > 0 and "
                    "_freezing_stack[len(_freezing_stack) - 1] == pending_action_id",
                    "Resolve the innermost saved choice first.",
                ),
                _call(
                    NATIVE_AREA,
                    native_params,
                    [
                        "_spell_summary_rows",
                        "_spell_detail_rows",
                        "_spell_damage",
                        "_spell_damage_total",
                    ],
                ),
                _call(
                    WATER,
                    {
                        "caster_state": "{_freezing_event.context.globe.caster}",
                        "spell_cast_id": "{_freezing_event.context.globe.cast_id}",
                        "slot_level": "{_freezing_event.context.globe.native.slot_level}",
                        "save_dc": "{_freezing_event.context.globe.native.casting_save_dc}",
                        "water": "{freezing_water}",
                        "effect_tick": "{_freezing_event.context.explosion_tick}",
                        "swimmers": "{freezing_surface_swimmers}",
                    },
                    ["_freezing_water_rows"],
                ),
                _each(
                    "_freezing_water_rows",
                    "_freezing_water_row",
                    [_push("_spell_detail_rows", "_freezing_water_row")],
                ),
                _set("state.pending_srd_actions.{pending_action_id}.status", "'resolved'"),
                _set(
                    "state.pending_srd_actions.{pending_action_id}.context.raw_damage",
                    "_spell_damage",
                ),
                _set("_freezing_remaining", []),
                _each(
                    "_freezing_stack",
                    "_freezing_stack_id",
                    [
                        _when(
                            "_freezing_stack_id != pending_action_id",
                            [_push("_freezing_remaining", "_freezing_stack_id")],
                        ),
                    ],
                ),
                _set("state.pending_srd_action_stack", "_freezing_remaining"),
            ],
        ),
        "spells",
        "Freezing Sphere explosion",
        [("Damage", "_spell_damage_total")],
    )


def _escape():
    effects = [
        _call(REFRESH, {}),
        _require(
            "actor_state.is_alive == true and not ('incapacitated' in actor_state.conditions)",
            "The trapped creature must be capable of taking the escape Action.",
        ),
        _set("_freezing_ice", "state.freezing_sphere_ice[source_id]"),
        _require(
            "_freezing_ice.active == true and state.lasting_tick < _freezing_ice.expires_tick",
            "This exact ice has already thawed.",
        ),
        _set("_freezing_link", None),
        _set("_freezing_links", "_freezing_ice.links"),
        _each(
            "_freezing_links",
            "_freezing_candidate_link",
            [
                _when(
                    "_freezing_candidate_link.actor == _actor_state_id",
                    [_set("_freezing_link", "_freezing_candidate_link")],
                ),
            ],
        ),
        _require(
            "_freezing_link != null and state.timed_condition_sources["
            "_freezing_link.condition_id].installed == true",
            "The acting creature is not trapped by this ice.",
        ),
        _set("_freezing_proficiency", "'none'"),
        _branch(
            "actor != null",
            [
                _require(
                    "_actor_id == _actor_state_id", "Use the acting character's own statistics."
                ),
                _when(
                    "'athletics' in actor.data.skill_proficiencies",
                    [_set("_freezing_proficiency", "'normal'")],
                ),
            ],
            [
                _require(
                    "actor_state.skill_check_modifiers.athletics != null",
                    "Record this creature's actual Athletics modifier.",
                )
            ],
        ),
        _when(
            "combat.encounter_active == true",
            [
                _require(
                    "combat.turn_active == true and combat.active_actor == _actor_state_id",
                    "Escape on the trapped creature's own turn.",
                ),
                _call("spend_srd_action", {"magic_action": False}),
            ],
        ),
        _branch(
            "actor != null",
            [
                _call(
                    "d20_test",
                    {
                        "actor": "{_actor_id}",
                        "actor_state": "{_actor_state_id}",
                        "ability": "strength",
                        "skill": "athletics",
                        "kind": "ability_check",
                        "proficiency_factor": "{_freezing_proficiency}",
                        "circumstantial_modifier": 0,
                        "target_number": "{_freezing_ice.save_dc}",
                    },
                    ["_d20_test", "_outcome"],
                )
            ],
            [
                _call(
                    "d20_test",
                    {
                        "actor_state": "{_actor_state_id}",
                        "ability": "strength",
                        "skill": "athletics",
                        "kind": "ability_check",
                        "ability_modifier": "{actor_state.skill_check_modifiers.athletics}",
                        "proficiency_bonus": 0,
                        "proficiency_factor": "none",
                        "circumstantial_modifier": 0,
                        "target_number": "{_freezing_ice.save_dc}",
                    },
                    ["_d20_test", "_outcome"],
                )
            ],
        ),
        _when("_outcome == 'success'", _retire_link()),
    ]
    return _public(
        _tool(
            "Spend an Action for actual Strength(Athletics) against the original DC.",
            [
                _entity("actor_state"),
                _optional("actor", "string", None, resolve="participant"),
                _parameter("source_id"),
            ],
            effects,
        ),
        "spells",
        "Escape Freezing Sphere ice",
        [("Outcome", "_outcome")],
    )


def bind_runtime_consumers(text):
    """Bind after final native area/source emission, before saved casting/reaction copies."""
    tools = _load_runtime(text)["tools"]
    area = deepcopy(tools[AREA])
    if area["effects"] and area["effects"][0].get("condition") == (
        "spell == 'freezing_sphere' and freezing_sphere_hold == true"
    ):
        area["effects"] = deepcopy(area["effects"][0]["on_false"])
    area["effects"] = [
        e
        for e in area["effects"]
        if e.get("condition") != "spell == 'freezing_sphere' and freezing_water != ''"
    ]
    area["parameters"] = [
        p for p in area["parameters"] if p["name"] not in {x["name"] for x in INTENTS}
    ]
    if not any(p["name"] == "lasting_state" for p in area["parameters"]):
        area["parameters"].append(_optional("lasting_state", "string", "state", resolve="entity"))
    native = deepcopy(area)
    native["pre_conditions"] = []
    next(p for p in native["parameters"] if p["name"] == "targets")["min_items"] = 0
    native["metadata"]["category"] = "internal"
    changed = {
        NATIVE_AREA: native,
        LOOKUP: _lookup(),
        EXPLODE: _explode(),
        REFRESH: _refresh(),
        WATER: _water(),
        "handle_srd_freezing_sphere_globe": _handle(),
        "resolve_srd_freezing_sphere_globe": _resolve(native),
        "escape_srd_freezing_sphere_ice": _escape(),
    }
    normal = deepcopy(area["effects"])
    normal.append(
        _when(
            "spell == 'freezing_sphere' and freezing_water != ''",
            [
                *_source(),
                _call(
                    WATER,
                    {
                        "caster_state": "{_caster_state_id}",
                        "spell_cast_id": "{_freezing_cast_id}",
                        "slot_level": "{slot_level}",
                        "save_dc": "{casting_save_dc}",
                        "water": "{freezing_water}",
                        "swimmers": "{freezing_surface_swimmers}",
                    },
                    ["_freezing_water_rows"],
                ),
                _each(
                    "_freezing_water_rows",
                    "_freezing_water_row",
                    [_push("_spell_detail_rows", "_freezing_water_row")],
                ),
            ],
        )
    )
    area["parameters"] += deepcopy(INTENTS)
    area["effects"] = [
        _branch(
            "spell == 'freezing_sphere' and freezing_sphere_hold == true", _retain(native), normal
        )
    ]
    bound = _tool("", deepcopy(area["parameters"]), area["effects"])
    area["parameters"] = bound["parameters"]
    area["effects"] = bound["effects"]
    changed[AREA] = area
    for name in ("cast_spell", "cast_spell_outside_combat"):
        if name not in tools:
            continue
        owner = deepcopy(tools[name])
        names = {p["name"] for p in owner["parameters"]}
        owner["parameters"] += [deepcopy(p) for p in INTENTS if p["name"] not in names]
        for effects in _lists(owner["effects"]):
            for effect in effects:
                if effect.get("call") in {AREA, "cast_spell"}:
                    effect["params"].update({p["name"]: "{" + p["name"] + "}" for p in INTENTS})
        changed[name] = owner
    processor = deepcopy(tools["process_srd_turn_effects"])
    marker = "lasting_state.has_freezing_sphere_sources == true"
    if marker not in str(processor["effects"]):
        processor["effects"].insert(0, _when(marker, [_call(REFRESH, {})]))
    changed["process_srd_turn_effects"] = processor
    completion = deepcopy(tools["complete_srd_pending_action"])
    guard = _require(
        "lasting_state.pending_srd_actions[pending_action_id].owner_kind != 'freezing_sphere'",
        "Supply the pending Freezing Sphere area facts first.",
    )
    if guard not in completion["effects"]:
        completion["effects"].insert(0, guard)
    changed["complete_srd_pending_action"] = completion
    return _replace_tools(_replace_tools(text, changed), changed)
