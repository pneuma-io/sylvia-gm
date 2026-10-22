"""Control Water derives its current effects from one admitted water body and paid source."""

from copy import deepcopy

if __package__:
    from .srd_scene_relations import POSITION_EXPORTS, _array, _snapshot, _spatial
    from .srd_spell_environment import (
        _call,
        _each,
        _effect_lists,
        _entity,
        _environment_tool,
        _optional,
        _param,
        _public,
        _push,
        _require,
        _set,
        _when,
    )
else:
    from srd_scene_relations import POSITION_EXPORTS, _array, _snapshot, _spatial
    from srd_spell_environment import (
        _call,
        _each,
        _effect_lists,
        _entity,
        _environment_tool,
        _optional,
        _param,
        _public,
        _push,
        _require,
        _set,
        _when,
    )

SOURCE_SHA = "b77c82e2d75a8cc5a356ebcf85840ebcfbb47e4051d13d003129c5e0cb85f46e"
CONTINUATION_SHA = "dcc0f13f68071229e96200384441058b9c07b0858f2230a4d335030f6f7bede1"
PROFILE = {
    "family": "spell_environment",
    "casting_time": "action",
    "base_level": 4,
    "range_kind": "distance",
    "range_feet": 300,
    "radius_feet": 0,
    "radius_per_slot_level": 0,
    "duration_rounds": 100,
    "concentration": True,
    "environment_mode": "control_water",
}
POINT_EXPORTS = (
    "_water_present",
    "_water_uncertain",
    "_water_geometry_id",
    "_water_depth",
    "_water_surface",
    "_water_flow",
    "_water_flow_direction",
    "_water_flow_source_id",
    "_water_parted",
    "_water_part_source_id",
    "_water_effect_volume",
)


def _direction(value, output):
    """A direction is a factual or printed choice, never a flow rate or displacement verdict."""
    return [
        _set(
            "_water_direction_length",
            f"({value}[0] ** 2 + {value}[1] ** 2 + {value}[2] ** 2) ** 0.5",
        ),
        _require(
            "_water_direction_length > 0", "Record or choose a genuine nonzero flow direction."
        ),
        _set(output, [f"{value}[{i}] * _water_direction_length ** -1" for i in range(3)]),
    ]


def _vehicle_fact_tool():
    """Vehicle identity/size are fiction; a capsize outcome comes only from the spell's Dice."""
    return _public(
        _environment_tool(
            "Record actual vehicle identity and size on an existing non-creature scene object.",
            [
                _param("object_id"),
                _param(
                    "size",
                    "enum",
                    enum_values=[
                        "tiny",
                        "small",
                        "medium",
                        "large",
                        "huge",
                        "gargantuan",
                    ],
                ),
            ],
            [
                _require(
                    "combat.encounter_active != true and combat.pending_action == null",
                    "Record the vehicle's fictional identity outside pending combat actions.",
                ),
                _call("srd_information_scene_entity", {"subject": "{object_id}"}, POSITION_EXPORTS),
                _require(
                    "_info_scene_entity.id == object_id and "
                    "_info_scene_entity.metadata.participant_ref == null",
                    "Use an actual non-creature scene object as this vehicle.",
                ),
                _set("_water_vehicle_initial_capsized", False),
                _when(
                    "_info_scene_entity.metadata.srd_water_vehicle.kind == 'water_vehicle'",
                    [
                        _set(
                            "_water_vehicle_initial_capsized",
                            "_info_scene_entity.metadata.srd_water_vehicle.capsized",
                        ),
                    ],
                ),
                _set(
                    "_water_vehicle_fact",
                    {
                        "kind": "'water_vehicle'",
                        "size": "size",
                        "capsized": "_water_vehicle_initial_capsized",
                    },
                ),
                _set(
                    "_water_vehicle_fact_patch",
                    {"is_object": True, "srd_water_vehicle": "_water_vehicle_fact"},
                ),
                _set(
                    "_water_vehicle_metadata",
                    "merge(_info_scene_entity.metadata, _water_vehicle_fact_patch)",
                ),
                *_snapshot(),
                {
                    "spatial_mutation": {
                        "scene": "state.spatial_scene",
                        "map_id": "{state.spatial_scene.map.id}",
                        "expected_revision": "{_info_snapshot.geometry_revision}",
                        "target": {"kind": "entity", "id": "{object_id}"},
                        "patch": {"metadata": "{_water_vehicle_metadata}"},
                    },
                    "store_as": "_water_vehicle_recorded",
                },
            ],
            public=True,
        ),
        "exploration",
        "Water vehicle recorded",
        [("Vehicle", "object_id"), ("Size", "size")],
    )


def _wave_destination(body, cube, direction):
    """A fixed side-to-side wave retains the represented vehicle's height and transverse axis."""
    effects = [_set("_water_wave_destination", f"{body}.position")]
    for name, axis, face, sign in (
        ("east", 0, "max", -1),
        ("west", 0, "min", 1),
        ("north", 1, "max", -1),
        ("south", 1, "min", 1),
    ):
        point = [f"{body}.position[{i}]" for i in range(3)]
        point[axis] = f"{cube}.{face}[{axis}] + {sign} * {body}.footprint.half_extents[{axis}]"
        effects.append(
            _when(
                f"{direction} == {name!r}",
                [
                    _set("_water_wave_destination", point),
                ],
            )
        )
    return effects


def _wave_candidates_tool():
    return _environment_tool(
        "Read eligible represented vehicles actually struck by this source's twenty-foot wave.",
        [_param("area_id")],
        [
            _set("_water_wave_source", "state.persistent_spell_areas[area_id]"),
            _set("_water_wave_targets", []),
            _call(
                "qualify_srd_control_water_source",
                {
                    "caster_state": "{_water_wave_source.caster}",
                    "area_id": "{area_id}",
                },
                ("_water_source_current",),
            ),
            _require(
                "_water_source_current == true and _water_wave_source.water_effect == 'flood' "
                "and _water_wave_source.water_body_category == 'large_body' and "
                "_water_wave_source.water_wave_volume != null",
                "Use an actual current large-body Flood wave.",
            ),
            _set("_water_wave_entities", "state.spatial_scene.entities"),
            _set("_wave_bounds", "_water_wave_source.water_wave_volume"),
            _set("_wave_cube", "_water_wave_source.water_cube"),
            _each(
                "_water_wave_entities",
                "_water_wave_vehicle",
                [
                    _when(
                        "_water_wave_vehicle.active == true and "
                        "_water_wave_vehicle.metadata.is_object == true and "
                        "_water_wave_vehicle.metadata.participant_ref == null and "
                        "_water_wave_vehicle.metadata.plane == _water_wave_source.plane and "
                        "_water_wave_vehicle.metadata.srd_water_vehicle.kind == 'water_vehicle' "
                        "and "
                        "_water_wave_vehicle.metadata.srd_water_vehicle.size != 'gargantuan'",
                        [
                            _require(
                                "_water_wave_vehicle.footprint.kind == 'box'",
                                "Use represented Box vehicle contact; other body shapes remain "
                                "GM adjudication.",
                            ),
                            _set("_wave_at", "_water_wave_vehicle.position"),
                            _set("_wave_half", "_water_wave_vehicle.footprint.half_extents"),
                            _when(
                                " and ".join(
                                    f"_wave_at[{i}] - _wave_half[{i}] < _wave_bounds.max[{i}] and "
                                    f"_wave_at[{i}] + _wave_half[{i}] > _wave_bounds.min[{i}]"
                                    for i in range(2)
                                )
                                + " and _wave_at[2] < _wave_bounds.max[2] and "
                                "_wave_at[2] + _water_wave_vehicle.height >= _wave_bounds.min[2]",
                                [
                                    _require(
                                        " and ".join(
                                            f"_wave_at[{i}] - _wave_half[{i}] >= "
                                            f"_wave_cube.min[{i}] and "
                                            f"_wave_at[{i}] + _wave_half[{i}] <= "
                                            f"_wave_cube.max[{i}]"
                                            for i in range(2)
                                        ),
                                        "A partly struck vehicle needs its actual source-side "
                                        "receiving placement.",
                                    ),
                                    _push("_water_wave_targets", "_water_wave_vehicle.id"),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _wave_motion_tool():
    """The final receiver authenticates a source-created receipt before the sole mutation."""
    return _environment_tool(
        "Authenticate only this paid wave's actual unconsumed vehicle displacement.",
        [_param("subject"), _array("destination"), _param("plane")],
        [
            _set("_water_wave_motion", "state.control_water_pending_motion"),
            _require(
                "_water_wave_motion.kind == 'control_water_wave' and "
                "_water_wave_motion.consumed == false and _water_wave_motion.subject == subject "
                "and "
                "_water_wave_motion.plane == plane and _water_wave_motion.tick == "
                "state.lasting_tick",
                "Use this actual current wave's unconsumed vehicle receipt.",
            ),
            _call(
                "qualify_srd_control_water_source",
                {
                    "caster_state": "{_water_wave_motion.caster}",
                    "area_id": "{_water_wave_motion.source_id}",
                },
                ("_water_source_current", "_water_source"),
            ),
            _require(
                "_water_source_current == true and _water_source.water_effect == 'flood' and "
                "_water_source.water_body_category == 'large_body' and "
                "_water_source.sequence == _water_wave_motion.sequence and "
                "_water_source.water_wave_sequence == _water_wave_motion.wave_sequence",
                "The authentic current wave or cast identity changed.",
            ),
            _call("srd_information_scene_entity", {"subject": "{subject}"}, POSITION_EXPORTS),
            _require(
                "_info_scene_entity.id == subject and _info_scene_entity.metadata.is_object == "
                "true "
                "and _info_scene_entity.metadata.srd_water_vehicle.kind == 'water_vehicle' and "
                "_info_scene_entity.metadata.srd_water_vehicle.size != 'gargantuan' and "
                "_info_scene_entity.metadata.plane == plane and _water_source.plane == plane",
                "Only this wave's actual eligible vehicle can receive its displacement.",
            ),
            *_wave_destination(
                "_info_scene_entity",
                "_water_source.water_cube",
                "_water_source.water_wave_direction",
            ),
            *[
                _require(
                    f"_info_scene_entity.position[{i}] == _water_wave_motion.origin[{i}] and "
                    f"destination[{i}] == _water_wave_motion.destination[{i}] and "
                    f"destination[{i}] == _water_wave_destination[{i}]",
                    "The actual vehicle origin or source-derived opposite-side destination "
                    "changed.",
                )
                for i in range(3)
            ],
            _set("_water_wave_motion_allowed", True),
        ],
    )


def _wave_tool():
    """Settle one wave; the next caster start creates a new wave and capsize roll."""
    return _environment_tool(
        "Carry Huge-or-smaller actual vehicles with the source wave and roll its native 25 "
        "percent capsize chance.",
        [_param("area_id"), _optional("timing", default="effect", enum_values=["effect", "start"])],
        [
            _call(
                "collect_srd_control_water_wave_vehicles",
                {"area_id": "{area_id}"},
                ("_water_wave_targets", "_water_wave_source"),
            ),
            _set("_water_wave_due", True),
            _set("_water_wave_turn", 0),
            _when(
                "combat.turn_active == true",
                [
                    _set("_water_wave_turn", "combat.turn_sequence"),
                ],
            ),
            _when(
                "timing == 'start'",
                [
                    _require(
                        "combat.turn_active == true and combat.active_actor == "
                        "_water_wave_source.caster",
                        "Repeat a Flood wave only at its caster's genuine turn start.",
                    ),
                    _set(
                        "_water_wave_due",
                        "_water_wave_source.water_wave_turn != _water_wave_turn",
                    ),
                ],
            ),
            _when(
                "_water_wave_due == true",
                [
                    _set("_water_wave_sequence", 1),
                    _when(
                        "_water_wave_source.water_wave_sequence != null",
                        [
                            _set(
                                "_water_wave_sequence", "_water_wave_source.water_wave_sequence + 1"
                            ),
                        ],
                    ),
                    _set(
                        "state.persistent_spell_areas.{area_id}.water_wave_sequence",
                        "_water_wave_sequence",
                    ),
                    _set(
                        "state.persistent_spell_areas.{area_id}.water_wave_turn",
                        "_water_wave_turn",
                    ),
                    _each(
                        "_water_wave_targets",
                        "_water_wave_subject",
                        [
                            _call(
                                "srd_information_scene_entity",
                                {"subject": "{_water_wave_subject}"},
                                POSITION_EXPORTS,
                            ),
                            *_wave_destination(
                                "_info_scene_entity",
                                "_water_wave_source.water_cube",
                                "_water_wave_source.water_wave_direction",
                            ),
                            _set(
                                "state.control_water_pending_motion",
                                {
                                    "kind": "'control_water_wave'",
                                    "source_id": "area_id",
                                    "caster": "_water_wave_source.caster",
                                    "sequence": "_water_wave_source.sequence",
                                    "wave_sequence": "_water_wave_sequence",
                                    "subject": "_water_wave_subject",
                                    "origin": "_info_scene_entity.position",
                                    "destination": "_water_wave_destination",
                                    "plane": "_water_wave_source.plane",
                                    "tick": "state.lasting_tick",
                                    "consumed": False,
                                },
                            ),
                            _call(
                                "move_srd_control_water_vehicle",
                                {
                                    "subject": "{_water_wave_subject}",
                                    "destination": "{_water_wave_destination}",
                                    "plane": "{_water_wave_source.plane}",
                                },
                            ),
                            _set("state.control_water_pending_motion", None),
                            {"dice": {"expression": "1d100"}, "store_as": "_water_capsize_roll"},
                            _call(
                                "srd_information_scene_entity",
                                {"subject": "{_water_wave_subject}"},
                                POSITION_EXPORTS,
                            ),
                            _set(
                                "_water_vehicle_result",
                                "_info_scene_entity.metadata.srd_water_vehicle",
                            ),
                            _set("_water_vehicle_capsized", "_water_vehicle_result.capsized"),
                            _when(
                                "_water_capsize_roll.total <= 25",
                                [_set("_water_vehicle_capsized", True)],
                            ),
                            _set(
                                "_water_vehicle_wave_update",
                                {
                                    "last_wave_source": "area_id",
                                    "last_wave_sequence": "_water_wave_sequence",
                                    "capsize_roll": "_water_capsize_roll.total",
                                    "capsized": "_water_vehicle_capsized",
                                },
                            ),
                            _set(
                                "_water_vehicle_result",
                                "merge(_water_vehicle_result, _water_vehicle_wave_update)",
                            ),
                            _set(
                                "_water_vehicle_patch",
                                {"srd_water_vehicle": "_water_vehicle_result"},
                            ),
                            _set(
                                "_water_vehicle_metadata",
                                "merge(_info_scene_entity.metadata, _water_vehicle_patch)",
                            ),
                            {
                                "spatial_mutation": {
                                    "scene": "state.spatial_scene",
                                    "map_id": "{state.spatial_scene.map.id}",
                                    "expected_revision": "{state.spatial_scene.map.revision}",
                                    "target": {"kind": "entity", "id": "{_water_wave_subject}"},
                                    "patch": {"metadata": "{_water_vehicle_metadata}"},
                                },
                                "store_as": "_water_vehicle_capsize",
                            },
                        ],
                    ),
                ],
            ),
        ],
    )


def _wave_start_tool():
    return _environment_tool(
        "Repeat only this actual caster's live large-body Flood waves on the native turn clock.",
        [_entity("caster_state")],
        [
            _set("_water_wave_ids", []),
            _when(
                "state.persistent_spell_area_ids != null",
                [
                    _set("_water_wave_ids", "state.persistent_spell_area_ids"),
                ],
            ),
            _each(
                "_water_wave_ids",
                "_water_wave_id",
                [
                    _set("_water_wave_row", "state.persistent_spell_areas[_water_wave_id]"),
                    _when(
                        "_water_wave_row.mode == 'control_water' and "
                        "_water_wave_row.water_effect == 'flood' "
                        "and _water_wave_row.water_body_category == 'large_body' and "
                        "_water_wave_row.caster == _caster_state_id",
                        [
                            _call(
                                "qualify_srd_control_water_source",
                                {
                                    "caster_state": "{_caster_state_id}",
                                    "area_id": "{_water_wave_id}",
                                },
                                ("_water_source_current",),
                            ),
                            _when(
                                "_water_source_current == true",
                                [
                                    _call(
                                        "resolve_srd_control_water_wave",
                                        {"area_id": "{_water_wave_id}", "timing": "start"},
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _lookup():
    return _environment_tool(
        "Read one actual admitted Box water geometry and its standing/flowing fact.",
        [_param("geometry_id"), _optional("require_fact", "boolean", True)],
        [
            *_snapshot(),
            _set("_water_geometry", None),
            _set("_water_geometries", "state.spatial_scene.geometry"),
            _each(
                "_water_geometries",
                "_water_candidate",
                [
                    _when(
                        "_water_candidate.id == geometry_id and _water_candidate.active == true",
                        [
                            _require(
                                "_water_geometry == null", "The actual water body must be unique."
                            ),
                            _set("_water_geometry", "_water_candidate"),
                        ],
                    ),
                ],
            ),
            _require(
                "_water_geometry != null and _water_geometry.volume.kind == 'box'",
                "Select an actual active admitted Box water volume.",
            ),
            _require(
                "_water_geometry.movement == 'passable' and "
                "_water_geometry.visibility == 'clear' and _water_geometry.metadata.plane != null",
                "This selected clear water body needs its actual passable volume and plane.",
            ),
            _when(
                "require_fact == true",
                [
                    _require(
                        "_water_geometry.metadata.srd_water.kind == 'water_body' and "
                        "(_water_geometry.metadata.srd_water.flow == 'standing' or "
                        "_water_geometry.metadata.srd_water.flow == 'flowing')",
                        "Admit the actual water body's standing/flowing fact first.",
                    ),
                ],
            ),
            _set("_water_surface", "_water_geometry.volume.max[2]"),
            _set("_water_depth", "_water_geometry.volume.max[2] - _water_geometry.volume.min[2]"),
        ],
    )


def _fact_tool():
    return _public(
        _environment_tool(
            "Admit water and standing/flowing fiction on one real clear passable Box geometry.",
            [
                _param("geometry_id"),
                _param("flow", "enum", enum_values=["standing", "flowing"]),
                _optional(
                    "body_category",
                    default="unknown",
                    enum_values=["unknown", "bounded", "large_body"],
                ),
                _array("terrain_flow_direction", default=[0, 0, 0]),
            ],
            [
                _require(
                    "combat.encounter_active != true and combat.pending_action == null",
                    "Admit actual water facts outside combat and pending actions.",
                ),
                _call(
                    "lookup_srd_water_body",
                    {"geometry_id": "{geometry_id}", "require_fact": False},
                    ("_water_geometry", "_water_surface", "_water_depth"),
                ),
                _set("_water_terrain_direction", [0, 0, 0]),
                _when(
                    "flow == 'flowing'",
                    [
                        _set("_water_terrain_direction", None),
                        _when(
                            "terrain_flow_direction[0] != 0 or terrain_flow_direction[1] != 0 "
                            "or terrain_flow_direction[2] != 0",
                            _direction("terrain_flow_direction", "_water_terrain_direction"),
                        ),
                    ],
                ),
                _set(
                    "_water_fact",
                    {
                        "kind": "'water_body'",
                        "flow": "flow",
                        "body_category": "body_category",
                        "terrain_flow_direction": "_water_terrain_direction",
                    },
                ),
                _set("_water_fact_patch", {"srd_water": "_water_fact"}),
                _set("_water_fact_metadata", "merge(_water_geometry.metadata, _water_fact_patch)"),
                *_snapshot(),
                {
                    "spatial_mutation": {
                        "scene": "state.spatial_scene",
                        "map_id": "{state.spatial_scene.map.id}",
                        "expected_revision": "{_info_snapshot.geometry_revision}",
                        "target": {"kind": "geometry", "id": "{geometry_id}"},
                        "patch": {"metadata": "{_water_fact_metadata}"},
                    },
                    "store_as": "_water_fact_mutation",
                },
                _set("state.has_srd_water_body", True),
            ],
            public=True,
        ),
        "exploration",
        "Water body admitted",
        [
            ("Body", "geometry_id"),
            ("Flow", "flow"),
            ("Surface", "_water_surface"),
            ("Depth", "_water_depth"),
        ],
    )


def _dimensions():
    return [
        _optional("trench_width", "number", 10, minimum=0, maximum=100),
        _optional("trench_axis", default="east", enum_values=["east", "north"]),
    ]


def _effect_choices():
    if __package__:
        from .srd_water_whirlpool import choice
    else:
        from srd_water_whirlpool import choice
    return [
        _optional(
            "water_effect",
            default="part_water",
            enum_values=["part_water", "flood", "redirect_flow", "whirlpool"],
        ),
        _optional("flood_rise", "number", 20, minimum=0, maximum=20),
        _optional("wave_direction", default="east", enum_values=["east", "west", "north", "south"]),
        _array("redirect_direction", default=[1, 0, 0]),
        choice(),
    ]


def _flood(cube, rise):
    """The real body category selects a level rise or the printed twenty-foot wave."""
    return [
        _require(
            "_water_geometry.metadata.srd_water.body_category == 'bounded' or "
            "_water_geometry.metadata.srd_water.body_category == 'large_body'",
            "Flood needs the actual bounded-body or large-body fact.",
        ),
        _require(
            f"{rise} >= 0 and {rise} <= 20", "Flood raises the actual level by at most 20 feet."
        ),
        _set("_water_flood_volume", None),
        _set("_water_wave_volume", None),
        _when(
            "_water_geometry.metadata.srd_water.body_category == 'bounded'",
            [
                _set(
                    "_water_flood_volume",
                    {
                        "kind": "'box'",
                        "min": [
                            f"{cube}.min[0]",
                            f"{cube}.min[1]",
                            "_water_geometry.volume.min[2]",
                        ],
                        "max": [
                            f"{cube}.max[0]",
                            f"{cube}.max[1]",
                            f"_water_geometry.volume.max[2] + {rise}",
                        ],
                    },
                ),
                _require(
                    f"_water_flood_volume.max[2] <= {cube}.max[2] and "
                    "_water_flood_volume.max[2] <= state.spatial_scene.map.bounds.max[2]",
                    "The actual raised water must fit the selected source Cube and map.",
                ),
            ],
            [
                _set(
                    "_water_wave_volume",
                    {
                        "kind": "'box'",
                        "min": [
                            f"{cube}.min[0]",
                            f"{cube}.min[1]",
                            "_water_geometry.volume.max[2]",
                        ],
                        "max": [
                            f"{cube}.max[0]",
                            f"{cube}.max[1]",
                            "_water_geometry.volume.max[2] + 20",
                        ],
                    },
                ),
                _require(
                    f"_water_wave_volume.max[2] <= {cube}.max[2]",
                    "The complete twenty-foot wave must fit the actual chosen Cube.",
                ),
            ],
        ),
    ]


def _effect_flow():
    return [
        _require(
            "(water_effect == 'redirect_flow' and _water_geometry.metadata.srd_water.flow == "
            "'flowing') "
            "or (water_effect != 'redirect_flow' and _water_geometry.metadata.srd_water.flow == "
            "'standing')",
            "Redirect Flow needs genuine flowing water; the other supported effects need "
            "standing water.",
        ),
        _set("_water_redirect_direction", None),
        _when(
            "water_effect == 'redirect_flow'",
            [
                _require(
                    "_water_geometry.metadata.srd_water.terrain_flow_direction != null",
                    "Record this flowing body's real terrain direction before redirecting it.",
                ),
                *_direction("redirect_direction", "_water_redirect_direction"),
            ],
        ),
    ]


def _trench(cube, width, axis):
    effects = []
    for direction, transverse in (("east", 1), ("north", 0)):
        low = [f"{cube}.min[{i}]" for i in range(3)]
        high = [f"{cube}.max[{i}]" for i in range(3)]
        low[transverse] = (
            f"({cube}.min[{transverse}] + {cube}.max[{transverse}]) * 0.5 - {width} * 0.5"
        )
        high[transverse] = (
            f"({cube}.min[{transverse}] + {cube}.max[{transverse}]) * 0.5 + {width} * 0.5"
        )
        high[2] = "_water_geometry.volume.max[2]"
        effects.append(
            _when(
                f"{axis} == {direction!r}",
                [
                    _set("_water_trench", {"kind": "'box'", "min": low, "max": high}),
                ],
            )
        )
    return effects


def _conflicts():
    """Reject unsupported same-body source/refill overlap before ending or paying a cast."""
    return [
        _set("_water_conflict_ids", []),
        _when(
            "state.persistent_spell_area_ids != null",
            [
                _set("_water_conflict_ids", "state.persistent_spell_area_ids"),
            ],
        ),
        _each(
            "_water_conflict_ids",
            "_water_conflict_id",
            [
                _set("_water_conflict", "state.persistent_spell_areas[_water_conflict_id]"),
                _when(
                    "_water_conflict.mode == 'control_water' and "
                    "_water_conflict.water_geometry_id == water_geometry_id and "
                    f"_water_conflict.source_sha256 == {SOURCE_SHA!r}",
                    [
                        _call(
                            "qualify_srd_control_water_source",
                            {
                                "caster_state": "{_water_conflict.caster}",
                                "area_id": "{_water_conflict_id}",
                            },
                            ("_water_source_current",),
                        ),
                        _require(
                            "_water_source_current != true",
                            "Concurrent Part Water on one body needs an actual source consumer.",
                        ),
                    ],
                ),
            ],
        ),
        _set("_water_conflict_returns", []),
        _when(
            "state.turn_effects != null",
            [
                _set("_water_conflict_returns", "state.turn_effects"),
            ],
        ),
        _each(
            "_water_conflict_returns",
            "_water_conflict_return",
            [
                _when(
                    "_water_conflict_return.kind == 'environment_water_refill' and "
                    "_water_conflict_return.geometry_id == water_geometry_id and "
                    f"_water_conflict_return.source_sha256 == {SOURCE_SHA!r}",
                    [
                        _require(
                            "state.lasting_tick >= _water_conflict_return.ready_tick",
                            "Concurrent Part Water during refill needs an actual source consumer.",
                        ),
                    ],
                ),
            ],
        ),
    ]


def _open_cube():
    """This selected water subset has no solid interior; supporting Box face contact survives."""
    return [
        *_snapshot(),
        _spatial(
            "area",
            {
                "map_id": "{state.spatial_scene.map.id}",
                "shape": {"kind": "box", "min": "{_water_cube.min}", "max": "{_water_cube.max}"},
                "include": ["geometry"],
                "max_results": 4096,
            },
            "_water_cube_candidates",
        ),
        _require(
            "_water_cube_candidates.truncated != true",
            "The real Cube's obstacle candidates must be complete.",
        ),
        _set("_water_cube_candidate_ids", []),
        _set("_water_cube_results", "_water_cube_candidates.results"),
        _each(
            "_water_cube_results",
            "_water_cube_candidate",
            [
                _push("_water_cube_candidate_ids", "_water_cube_candidate.id"),
            ],
        ),
        _set("_water_cube_geometry", "state.spatial_scene.geometry"),
        _each(
            "_water_cube_geometry",
            "_water_cube_solid",
            [
                _when(
                    "_water_cube_solid.id in _water_cube_candidate_ids and "
                    "_water_cube_solid.active == true and "
                    "(_water_cube_solid.movement == 'blocking' or "
                    "_water_cube_solid.visibility == 'opaque') and "
                    "(_water_cube_solid.metadata.plane == _environment_plane or "
                    "(_water_cube_solid.metadata.plane == null and "
                    "_environment_plane == 'material'))",
                    [
                        _require(
                            "_water_cube_solid.volume.kind == 'box'",
                            "Relevant curved solids need the actual water-clipping consumer.",
                        ),
                        _require(
                            "not ("
                            + " and ".join(
                                f"_water_cube.min[{i}] < _water_cube_solid.volume.max[{i}] and "
                                f"_water_cube.max[{i}] > _water_cube_solid.volume.min[{i}]"
                                for i in range(3)
                            )
                            + ")",
                            "Part Water across a real solid interior needs actual effect clipping.",
                        ),
                    ],
                ),
            ],
        ),
    ]


def _source_tool():
    return _environment_tool(
        "Authenticate the exact paid Control Water without accepting caller liquid outcomes.",
        [_entity("caster_state"), _param("area_id")],
        [
            _set("_water_geometry", None),
            _set("_water_depth", 0),
            _set("_water_source", "state.persistent_spell_areas[area_id]"),
            _set(
                "_water_source_current",
                "_water_source != null and "
                "_water_source.profile == 'environment' and _water_source.mode == 'control_water' "
                "and _water_source.spell == 'control_water' and "
                f"_water_source.source_sha256 == {SOURCE_SHA!r}",
            ),
            _set(
                "_water_source_current",
                "_water_source_current == true and "
                "_water_source.active == true and state.lasting_tick < _water_source.expires_tick "
                "and _water_source.caster == _caster_state_id and caster_state.is_alive == true "
                "and caster_state.hp > 0 and caster_state.environment_area_id == area_id",
            ),
            _set(
                "_water_source_current",
                "_water_source_current == true and "
                "caster_state.concentration_spell_id == 'control_water' and "
                "caster_state.concentration_effect_family == 'spell_environment'",
            ),
            _when(
                "_water_source_current == true",
                [
                    _require(
                        "state.has_spell_magic_zones != true",
                        "Load the actual whole-water magic-zone clipping consumer first.",
                    ),
                    _call(
                        "lookup_srd_water_body",
                        {"geometry_id": "{_water_source.water_geometry_id}"},
                        ("_water_geometry", "_water_depth"),
                    ),
                    *_same_body("_water_source.water_body_stamp"),
                    _require(
                        "_water_geometry.metadata.plane == _water_source.plane and "
                        "_water_geometry.map_id == _water_source.water_map_id and "
                        "(_water_geometry.metadata.srd_water.flow == _water_source.water_body_flow "
                        "or (_water_source.water_body_flow == null and "
                        "_water_geometry.metadata.srd_water.flow == 'standing'))",
                        "Changed water body geometry/flow needs its actual source consumer.",
                    ),
                    _when(
                        "_water_source.water_effect == 'flood'",
                        [
                            _require(
                                "_water_geometry.metadata.srd_water.body_category == "
                                "_water_source.water_body_category",
                                "Changed Flood body classification needs its actual consumer.",
                            )
                        ],
                    ),
                ],
            ),
        ],
    )


def _same_body(stamp):
    return [
        _require(
            f"_water_geometry.volume.kind == {stamp}.kind",
            "Changed water body geometry needs its actual source consumer.",
        ),
        *[
            _require(
                f"_water_geometry.volume.{bound}[{i}] == {stamp}.{bound}[{i}]",
                "Changed water body geometry needs its actual source consumer.",
            )
            for bound in ("min", "max")
            for i in range(3)
        ],
    ]


def _return_tool():
    return _environment_tool(
        "Retain one genuine next-round refill even when another spell replaces the caster source.",
        [_param("area_id")],
        [
            _set("_water_ending", "state.persistent_spell_areas[area_id]"),
            _when(
                "_water_ending.active == true and _water_ending.mode == 'control_water' and "
                "_water_ending.water_effect == 'part_water' and "
                f"_water_ending.source_sha256 == {SOURCE_SHA!r} and "
                "_water_ending.water_refill_retained != true",
                [
                    _set("_water_refills", []),
                    _when(
                        "state.turn_effects != null",
                        [
                            _set("_water_refills", "state.turn_effects"),
                        ],
                    ),
                    _push(
                        "_water_refills",
                        {
                            "kind": "'environment_water_refill'",
                            "spell": "'control_water'",
                            "source_sha256": repr(SOURCE_SHA),
                            "source_id": "area_id",
                            "caster": "_water_ending.caster",
                            "instance": "_water_ending.sequence",
                            "geometry_id": "_water_ending.water_geometry_id",
                            "map_id": "_water_ending.water_map_id",
                            "body_stamp": "_water_ending.water_body_stamp",
                            "trench": "_water_ending.water_trench",
                            "plane": "_water_ending.plane",
                            "started_tick": "min(state.lasting_tick, _water_ending.expires_tick)",
                            "ready_tick": "min(state.lasting_tick, _water_ending.expires_tick) + 2",
                            "dispel_ongoing": False,
                        },
                    ),
                    _set("state.turn_effects", "_water_refills"),
                    _set("state.persistent_spell_areas.{area_id}.water_refill_retained", True),
                ],
            ),
        ],
    )


def _inside(point, volume):
    return " and ".join(
        f"{point}[{i}] >= {volume}.min[{i}] and {point}[{i}] < {volume}.max[{i}]" for i in range(3)
    )


def _point_source_tool():
    return _environment_tool(
        "Qualify current Part Water at an actual point using only its exact paid source.",
        [_param("area_id"), _param("geometry_id"), _array("point")],
        [
            _set("_water_point_removed", False),
            _set("_water_point_flow_direction", None),
            _set("_water_point_source", "state.persistent_spell_areas[area_id]"),
            _when(
                "_water_point_source.mode == 'control_water' and "
                "_water_point_source.water_effect == 'part_water' and "
                "_water_point_source.water_geometry_id == geometry_id and "
                f"_water_point_source.source_sha256 == {SOURCE_SHA!r}",
                [
                    _when(
                        _inside("point", "_water_point_source.water_trench"),
                        [
                            _call(
                                "qualify_srd_control_water_source",
                                {
                                    "caster_state": "{_water_point_source.caster}",
                                    "area_id": "{area_id}",
                                },
                                ("_water_source_current",),
                            ),
                            _set("_water_point_removed", "_water_source_current"),
                        ],
                    ),
                ],
            ),
            _when(
                "_water_point_source.mode == 'control_water' and "
                "_water_point_source.water_effect == 'redirect_flow' and "
                "_water_point_source.water_geometry_id == geometry_id and "
                f"_water_point_source.source_sha256 == {SOURCE_SHA!r}",
                [
                    _when(
                        _inside("point", "_water_point_source.water_cube"),
                        [
                            _call(
                                "qualify_srd_control_water_source",
                                {
                                    "caster_state": "{_water_point_source.caster}",
                                    "area_id": "{area_id}",
                                },
                                ("_water_source_current",),
                            ),
                            _when(
                                "_water_source_current == true",
                                [
                                    _set(
                                        "_water_point_flow_direction",
                                        "_water_point_source.water_redirect_direction",
                                    ),
                                ],
                            ),
                        ],
                    )
                ],
            ),
        ],
    )


def _point_body_tool():
    return _environment_tool(
        "Read one genuine Box water point and current source-qualified trench/refill.",
        [_param("geometry_id"), _array("point")],
        [
            _call(
                "lookup_srd_water_body",
                {"geometry_id": "{geometry_id}"},
                ("_water_geometry", "_water_surface", "_water_depth"),
            ),
            _set("_water_present", True),
            _set("_water_uncertain", False),
            _set("_water_parted", False),
            _set("_water_part_source_id", None),
            _set("_water_flow", "_water_geometry.metadata.srd_water.flow"),
            _set(
                "_water_flow_direction", "_water_geometry.metadata.srd_water.terrain_flow_direction"
            ),
            _set("_water_flow_source_id", None),
            _set("_water_effect_volume", "_water_geometry.volume"),
            _set("_water_point_ids", []),
            _when(
                "state.persistent_spell_area_ids != null",
                [
                    _set("_water_point_ids", "state.persistent_spell_area_ids"),
                ],
            ),
            _each(
                "_water_point_ids",
                "_water_point_id",
                [
                    _call(
                        "resolve_srd_control_water_point_source",
                        {
                            "area_id": "{_water_point_id}",
                            "geometry_id": "{geometry_id}",
                            "point": "{point}",
                        },
                        ("_water_point_removed", "_water_point_flow_direction"),
                    ),
                    _when(
                        "_water_point_flow_direction != null",
                        [
                            _require(
                                "_water_flow_source_id == null",
                                "Overlapping redirected sources need their actual flow precedence.",
                            ),
                            _set("_water_flow_direction", "_water_point_flow_direction"),
                            _set("_water_flow_source_id", "_water_point_id"),
                        ],
                    ),
                    _when(
                        "_water_point_removed == true",
                        [
                            _require(
                                "_water_part_source_id == null",
                                "Overlapping Part Water sources need an actual union consumer.",
                            ),
                            _set("_water_present", False),
                            _set("_water_parted", True),
                            _set("_water_part_source_id", "_water_point_id"),
                        ],
                    ),
                ],
            ),
            _set("_water_point_returns", []),
            _when(
                "state.turn_effects != null",
                [
                    _set("_water_point_returns", "state.turn_effects"),
                ],
            ),
            _each(
                "_water_point_returns",
                "_water_point_return",
                [
                    _when(
                        "_water_point_return.kind == 'environment_water_refill' and "
                        "_water_point_return.geometry_id == geometry_id and "
                        f"_water_point_return.source_sha256 == {SOURCE_SHA!r} and "
                        "state.lasting_tick < _water_point_return.ready_tick",
                        [
                            *_same_body("_water_point_return.body_stamp"),
                            _require(
                                "_water_point_return.plane == _water_geometry.metadata.plane and "
                                "_water_point_return.map_id == _water_geometry.map_id",
                                "Changed refilling water plane or map needs its actual consumer.",
                            ),
                            _when(
                                _inside("point", "_water_point_return.trench"),
                                [
                                    _set("_water_uncertain", True),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _flood_point_tool():
    """Read the same paid source above the original level without moving its actual body."""
    return _environment_tool(
        "Qualify current bounded Flood water at an actual same-plane point.",
        [_array("point"), _param("plane")],
        [
            _set("_water_flood_found", False),
            _set("_water_flood_geometry_id", None),
            _set("_water_flood_volume", None),
            _set("_water_flood_ids", []),
            _when(
                "state.persistent_spell_area_ids != null",
                [
                    _set("_water_flood_ids", "state.persistent_spell_area_ids"),
                ],
            ),
            _each(
                "_water_flood_ids",
                "_water_flood_id",
                [
                    _set("_water_flood_source", "state.persistent_spell_areas[_water_flood_id]"),
                    _when(
                        "_water_flood_source.mode == 'control_water' and "
                        "_water_flood_source.water_effect == 'flood' and "
                        "_water_flood_source.water_flood_volume != null and "
                        "_water_flood_source.plane == plane",
                        [
                            _when(
                                _inside("point", "_water_flood_source.water_flood_volume"),
                                [
                                    _call(
                                        "qualify_srd_control_water_source",
                                        {
                                            "caster_state": "{_water_flood_source.caster}",
                                            "area_id": "{_water_flood_id}",
                                        },
                                        ("_water_source_current", "_water_geometry"),
                                    ),
                                    _when(
                                        "_water_source_current == true",
                                        [
                                            _require(
                                                "_water_flood_found != true",
                                                "Overlapping raised water needs "
                                                "its genuine source union.",
                                            ),
                                            *_flood(
                                                "_water_flood_source.water_cube",
                                                "_water_flood_source.water_flood_rise",
                                            ),
                                            _set("_water_flood_found", True),
                                            _set("_water_flood_geometry_id", "_water_geometry.id"),
                                        ],
                                    ),
                                ],
                            )
                        ],
                    ),
                ],
            ),
        ],
    )


def _point_tool():
    return _environment_tool(
        "Read actual Box water minus genuine Part Water; next-round refill stays uncertain.",
        [_array("point"), _param("plane")],
        [
            *_snapshot(),
            _set("_water_present", False),
            _set("_water_uncertain", False),
            _set("_water_geometry_id", None),
            _set("_water_surface", None),
            _set("_water_depth", 0),
            _set("_water_flow", "''"),
            _set("_water_flow_direction", None),
            _set("_water_flow_source_id", None),
            _set("_water_parted", False),
            _set("_water_part_source_id", None),
            _set("_water_effect_volume", None),
            _set("_water_point_geometries", "state.spatial_scene.geometry"),
            _each(
                "_water_point_geometries",
                "_water_point_body",
                [
                    _when(
                        "_water_point_body.active == true and "
                        "_water_point_body.metadata.plane == plane and "
                        "_water_point_body.metadata.srd_water.kind == 'water_body'",
                        [
                            _require(
                                "_water_point_body.volume.kind == 'box'",
                                "Curved actual water bodies need their own consumer.",
                            ),
                            _when(
                                _inside("point", "_water_point_body.volume"),
                                [
                                    _require(
                                        "_water_geometry_id == null",
                                        "Overlapping water bodies need an actual union consumer.",
                                    ),
                                    _set("_water_geometry_id", "_water_point_body.id"),
                                    _call(
                                        "resolve_srd_water_body_point",
                                        {
                                            "geometry_id": "{_water_point_body.id}",
                                            "point": "{point}",
                                        },
                                        tuple(
                                            e for e in POINT_EXPORTS if e != "_water_geometry_id"
                                        ),
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _when(
                "state.has_spell_environments == true",
                [
                    _call(
                        "resolve_srd_water_flood_point",
                        {
                            "point": "{point}",
                            "plane": "{plane}",
                        },
                        ("_water_flood_found", "_water_flood_geometry_id", "_water_flood_volume"),
                    ),
                    _when(
                        "_water_flood_found == true",
                        [
                            _require(
                                "_water_geometry_id == null or "
                                "_water_geometry_id == _water_flood_geometry_id",
                                "Overlapping actual water and Flood need a genuine volume union.",
                            ),
                            _set("_water_geometry_id", "_water_flood_geometry_id"),
                            _set("_water_present", True),
                            _set("_water_parted", False),
                            _set("_water_part_source_id", None),
                            _set("_water_flow", "'standing'"),
                            _set("_water_effect_volume", "_water_flood_volume"),
                            _set("_water_surface", "_water_flood_volume.max[2]"),
                            _set(
                                "_water_depth",
                                "_water_flood_volume.max[2] - _water_flood_volume.min[2]",
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _footprint_bounds(volume):
    return [
        _require(
            f"{point}[{i}] - _water_actor.footprint.half_extents[{i}] >= {volume}.min[{i}] "
            f"and {point}[{i}] + _water_actor.footprint.half_extents[{i}] <= {volume}.max[{i}]",
            "The whole moving Box footprint must remain inside the actual selected volume.",
        )
        for point in ("start", "end")
        for i in range(2)
    ]


def _body_interval(volume):
    """Minkowski expansion tests strict actual Box interiors, preserving surface tangency."""
    return [
        _set(
            "_water_route_box",
            {
                "kind": "'box'",
                "min": [
                    *[
                        f"{volume}.min[{i}] - _water_actor.footprint.half_extents[{i}]"
                        for i in range(2)
                    ],
                    f"{volume}.min[2] - _water_actor.height",
                ],
                "max": [
                    *[
                        f"{volume}.max[{i}] + _water_actor.footprint.half_extents[{i}]"
                        for i in range(2)
                    ],
                    f"{volume}.max[2]",
                ],
            },
        ),
        _call(
            "srd_information_segment_interval",
            {
                "start": "{start}",
                "end": "{end}",
                "volume": "{_water_route_box}",
            },
            ("_segment_hit", "_segment_entry", "_segment_exit"),
        ),
        _set("_water_route_touches", False),
        _when(
            "_segment_hit == true",
            [
                _set(
                    "_water_route_midpoint",
                    [
                        f"start[{i}] + (end[{i}] - start[{i}]) * "
                        "(_segment_entry + _segment_exit) * 0.5"
                        for i in range(3)
                    ],
                ),
                _set(
                    "_water_route_touches",
                    " and ".join(
                        f"_water_route_midpoint[{i}] > _water_route_box.min[{i}] and "
                        f"_water_route_midpoint[{i}] < _water_route_box.max[{i}]"
                        for i in range(3)
                    ),
                ),
            ],
        ),
    ]


def _support():
    return [
        _call(
            "qualify_srd_environment_ground",
            {
                "point": "{start}",
                "plane": "{_water_actor.metadata.plane}",
            },
            ("_environment_ground_id",),
        ),
        _set("_water_support_id", "_environment_ground_id"),
        _call(
            "qualify_srd_environment_ground",
            {
                "point": "{end}",
                "plane": "{_water_actor.metadata.plane}",
            },
            ("_environment_ground_id",),
        ),
        _require(
            "_water_support_id == _environment_ground_id",
            "The complete trench walk needs one actual supported bottom Box.",
        ),
        _set("_water_support_geometries", "state.spatial_scene.geometry"),
        _each(
            "_water_support_geometries",
            "_water_support_body",
            [
                _when(
                    "_water_support_body.id == _water_support_id",
                    [
                        _require(
                            "_water_support_body.volume.kind == 'box'",
                            "This complete route needs a genuine flat supported bottom Box.",
                        ),
                        *_footprint_bounds("_water_support_body.volume"),
                    ],
                ),
            ],
        ),
    ]


def _wet_cuts():
    return [
        _set("_water_wet_ids", []),
        _when(
            "state.persistent_spell_area_ids != null",
            [
                _set("_water_wet_ids", "state.persistent_spell_area_ids"),
            ],
        ),
        _each(
            "_water_wet_ids",
            "_water_wet_id",
            [
                _set("_water_wet_source", "state.persistent_spell_areas[_water_wet_id]"),
                _when(
                    "_water_wet_source.mode == 'control_water' and "
                    "_water_wet_source.water_effect == 'part_water' and "
                    "_water_wet_source.water_geometry_id == _water_geometry_id and "
                    f"_water_wet_source.source_sha256 == {SOURCE_SHA!r}",
                    [
                        _call(
                            "qualify_srd_control_water_source",
                            {
                                "caster_state": "{_water_wet_source.caster}",
                                "area_id": "{_water_wet_id}",
                            },
                            ("_water_source_current",),
                        ),
                        _when(
                            "_water_source_current == true",
                            [
                                *_body_interval("_water_wet_source.water_trench"),
                                _require(
                                    "_water_route_touches != true",
                                    "A whole wet route cannot cross the genuine dry trench.",
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        _set("_water_wet_returns", []),
        _when(
            "state.turn_effects != null",
            [
                _set("_water_wet_returns", "state.turn_effects"),
            ],
        ),
        _each(
            "_water_wet_returns",
            "_water_wet_return",
            [
                _when(
                    "_water_wet_return.kind == 'environment_water_refill' and "
                    "_water_wet_return.geometry_id == _water_geometry_id and "
                    f"_water_wet_return.source_sha256 == {SOURCE_SHA!r} and "
                    "state.lasting_tick < _water_wet_return.ready_tick",
                    [
                        *_same_body("_water_wet_return.body_stamp"),
                        _require(
                            "_water_wet_return.plane == _water_geometry.metadata.plane and "
                            "_water_wet_return.map_id == _water_geometry.map_id",
                            "Changed refilling water plane or map needs its actual consumer.",
                        ),
                        *_body_interval("_water_wet_return.trench"),
                        _require(
                            "_water_route_touches != true",
                            "The whole route touches genuine next-round refill in progress.",
                        ),
                    ],
                ),
            ],
        ),
    ]


def _route_tool():
    """Whole straight Box routes are dry or wet; mixed boundary routes reject explicitly."""
    return _environment_tool(
        "Qualify whole actual Box routes through real water or a genuine supported dry trench.",
        [
            _entity("actor_state"),
            _array("start"),
            _array("end"),
            _param("mode", "enum", enum_values=["walk", "fly", "climb", "swim"]),
        ],
        [
            _call(
                "srd_information_scene_entity", {"subject": "{_actor_state_id}"}, POSITION_EXPORTS
            ),
            _set("_water_actor", "_info_scene_entity"),
            _call(
                "resolve_srd_water_point",
                {
                    "point": "{start}",
                    "plane": "{_water_actor.metadata.plane}",
                },
                POINT_EXPORTS,
            ),
            _set("_water_start_body", "_water_geometry_id"),
            _set("_water_start_wet", "_water_present"),
            _set("_water_start_dry", "_water_parted"),
            _set("_water_start_source", "_water_part_source_id"),
            _set("_water_start_volume", "_water_effect_volume"),
            _require("_water_uncertain != true", "The next-round refill is still in progress."),
            _call(
                "resolve_srd_water_point",
                {
                    "point": "{end}",
                    "plane": "{_water_actor.metadata.plane}",
                },
                POINT_EXPORTS,
            ),
            _require("_water_uncertain != true", "The next-round refill is still in progress."),
            _set("_water_route_bodies", "state.spatial_scene.geometry"),
            _each(
                "_water_route_bodies",
                "_water_route_body",
                [
                    _when(
                        "_water_route_body.active == true and "
                        "_water_route_body.metadata.plane == _water_actor.metadata.plane and "
                        "_water_route_body.metadata.srd_water.kind == 'water_body'",
                        [
                            _require(
                                "_water_actor.footprint.kind == 'box' and "
                                "_water_route_body.volume.kind == 'box'",
                                "Curved water routes need their actual whole-body consumer.",
                            ),
                            *_body_interval("_water_route_body.volume"),
                            _when(
                                "_water_route_touches == true",
                                [
                                    _require(
                                        "_water_start_body == _water_route_body.id and "
                                        "_water_geometry_id == _water_route_body.id",
                                        "Mixed water boundary routes need actual entry splitting.",
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _set("_water_route_source_ids", []),
            _when(
                "state.persistent_spell_area_ids != null",
                [
                    _set("_water_route_source_ids", "state.persistent_spell_area_ids"),
                ],
            ),
            _each(
                "_water_route_source_ids",
                "_water_route_source_id",
                [
                    _set(
                        "_water_route_source",
                        "state.persistent_spell_areas[_water_route_source_id]",
                    ),
                    _when(
                        "_water_route_source.mode == 'control_water' and "
                        "_water_route_source.water_effect == 'flood' and "
                        "_water_route_source.water_flood_volume != null and "
                        "_water_route_source.plane == _water_actor.metadata.plane",
                        [
                            _call(
                                "qualify_srd_control_water_source",
                                {
                                    "caster_state": "{_water_route_source.caster}",
                                    "area_id": "{_water_route_source_id}",
                                },
                                ("_water_source_current",),
                            ),
                            _when(
                                "_water_source_current == true",
                                [
                                    *_body_interval("_water_route_source.water_flood_volume"),
                                    _when(
                                        "_water_route_touches == true",
                                        [
                                            _require(
                                                "_water_start_body == "
                                                "_water_route_source.water_geometry_id and "
                                                "_water_geometry_id == "
                                                "_water_route_source.water_geometry_id",
                                                "Mixed raised-water routes need "
                                                "their actual entry splitting.",
                                            )
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _when(
                "_water_start_body != null or _water_geometry_id != null",
                [
                    _require(
                        "_water_start_body == _water_geometry_id and "
                        "mode != 'fly' and mode != 'climb'",
                        "Mixed or surface water routes need their genuine body consumer.",
                    ),
                    _require(
                        "(_water_start_dry == true and _water_parted == true) or "
                        "(_water_start_wet == true and _water_present == true)",
                        "Split the actual wet/dry route at its qualified boundary.",
                    ),
                    _call(
                        "lookup_srd_water_body",
                        {"geometry_id": "{_water_geometry_id}"},
                        ("_water_geometry",),
                    ),
                    _require(
                        "_water_geometry.metadata.srd_water.flow == 'standing'",
                        "Flowing water needs its actual current consumer.",
                    ),
                    *_footprint_bounds("_water_geometry.volume"),
                    _when(
                        "_water_parted == true",
                        [
                            _require(
                                "_water_start_source == _water_part_source_id",
                                "A whole dry route must remain in one genuine trench.",
                            ),
                            _set(
                                "_water_dry_source",
                                "state.persistent_spell_areas[_water_part_source_id]",
                            ),
                            *_footprint_bounds("_water_dry_source.water_trench"),
                            _require(
                                "mode == 'walk' and start[2] == _water_geometry.volume.min[2] and "
                                "end[2] == _water_geometry.volume.min[2]",
                                "Walk the actual dry trench bottom; swimming needs actual water.",
                            ),
                            *_support(),
                        ],
                        [
                            _require("mode == 'swim'", "Use actual swimming through this water."),
                            *[
                                _require(
                                    f"_water_start_volume.{bound}[{i}] == "
                                    f"_water_effect_volume.{bound}[{i}]",
                                    "Mixed raised-water sections need "
                                    "their actual whole-body union.",
                                )
                                for bound in ("min", "max")
                                for i in range(3)
                            ],
                            *_footprint_bounds("_water_effect_volume"),
                            *[
                                _require(
                                    f"{point}[2] >= _water_effect_volume.min[2] and "
                                    f"{point}[2] + _water_actor.height <= "
                                    "_water_effect_volume.max[2]",
                                    "This complete swimming route needs its actual submerged Box.",
                                )
                                for point in ("start", "end")
                            ],
                            *_wet_cuts(),
                        ],
                    ),
                ],
            ),
        ],
    )


def tools():
    if __package__:
        from .srd_water_whirlpool import bind_route
        from .srd_water_whirlpool import tools as whirlpool_tools
        from .srd_whirlpool_pull import tools as pull_tools
    else:
        from srd_water_whirlpool import bind_route
        from srd_water_whirlpool import tools as whirlpool_tools
        from srd_whirlpool_pull import tools as pull_tools
    result = {
        "lookup_srd_water_body": _lookup(),
        "record_srd_water_body": _fact_tool(),
        "qualify_srd_control_water_source": _source_tool(),
        "retain_srd_control_water_refill": _return_tool(),
        "resolve_srd_water_point": _point_tool(),
        "resolve_srd_water_flood_point": _flood_point_tool(),
        "resolve_srd_control_water_point_source": _point_source_tool(),
        "resolve_srd_water_body_point": _point_body_tool(),
        "qualify_srd_water_route": _route_tool(),
        "record_srd_water_vehicle": _vehicle_fact_tool(),
        "collect_srd_control_water_wave_vehicles": _wave_candidates_tool(),
        "qualify_srd_control_water_wave_motion": _wave_motion_tool(),
        "resolve_srd_control_water_wave": _wave_tool(),
        "repeat_srd_control_water_waves": _wave_start_tool(),
        "resolve_srd_water_flow": _public(
            _environment_tool(
                "Read represented water's current source-scoped direction, without inventing a "
                "flow rate.",
                [_array("point"), _param("plane")],
                [
                    _call(
                        "resolve_srd_water_point",
                        {"point": "{point}", "plane": "{plane}"},
                        POINT_EXPORTS,
                    ),
                    _require(
                        "_water_present == true and _water_uncertain != true",
                        "Query actual present water outside a still-refilling trench.",
                    ),
                    _when(
                        "_water_flow == 'flowing'",
                        [
                            _require(
                                "_water_flow_direction != null",
                                "Record the actual terrain flow direction first.",
                            ),
                        ],
                    ),
                ],
                public=True,
            ),
            "information",
            "Water flow",
            [
                ("Body", "_water_geometry_id"),
                ("Direction", "_water_flow_direction"),
                ("Spell source", "_water_flow_source_id"),
            ],
        ),
        "change_srd_control_water": _public(
            _environment_tool(
                "Take a real later Magic action to repeat or change supported Control Water.",
                [_entity("caster_state"), _param("area_id"), *_dimensions(), *_effect_choices()],
                [
                    _call(
                        "qualify_srd_control_water_source",
                        {"caster_state": "{_caster_state_id}", "area_id": "{area_id}"},
                        (
                            "_water_source_current",
                            "_water_source",
                            "_water_geometry",
                            "_water_depth",
                        ),
                    ),
                    _require(
                        "_water_source_current == true", "Use the exact current Part Water source."
                    ),
                    _require(
                        "combat.turn_active == true and "
                        "combat.active_actor == _caster_state_id and "
                        "(_water_source.water_cast_clock != 'combat' or "
                        "combat.turn_sequence != _water_source.water_cast_turn)",
                        "Use the caster's actual later turn for this Magic action.",
                    ),
                    _set("_water_cube", "_water_source.water_cube"),
                    _set("_environment_plane", "_water_source.plane"),
                    *_effect_flow(),
                    _require(
                        "state.has_spell_magic_zones != true",
                        "Load the actual whole-water magic-zone clipping consumer first.",
                    ),
                    _when("water_effect != 'redirect_flow'", _open_cube()),
                    _when(
                        "water_effect == 'part_water'",
                        [
                            _require(
                                "trench_width > 0 and trench_width < _water_source.water_side",
                                "Leave genuine water to both sides of the chosen trench.",
                            ),
                            *_trench("_water_source.water_cube", "trench_width", "trench_axis"),
                        ],
                    ),
                    _when(
                        "water_effect == 'flood'",
                        [
                            *_flood("_water_source.water_cube", "flood_rise"),
                        ],
                    ),
                    _call("spend_srd_action", {"magic_action": True}),
                    _when(
                        "_water_source.water_effect == 'part_water' "
                        "and water_effect != 'part_water'",
                        [_call("retain_srd_control_water_refill", {"area_id": "{area_id}"})],
                    ),
                    _set("state.persistent_spell_areas.{area_id}.water_effect", "water_effect"),
                    _set(
                        "state.persistent_spell_areas.{area_id}.water_redirect_direction",
                        "_water_redirect_direction",
                    ),
                    _set("state.persistent_spell_areas.{area_id}.water_wave_volume", None),
                    _when(
                        "water_effect == 'part_water'",
                        [
                            _set(
                                "state.persistent_spell_areas.{area_id}.water_trench",
                                "_water_trench",
                            ),
                            _set(
                                "state.persistent_spell_areas.{area_id}.water_trench_width",
                                "trench_width",
                            ),
                            _set(
                                "state.persistent_spell_areas.{area_id}.water_trench_axis",
                                "trench_axis",
                            ),
                            _set(
                                "state.persistent_spell_areas.{area_id}.water_refill_retained",
                                False,
                            ),
                        ],
                    ),
                    _when(
                        "water_effect == 'flood'",
                        [
                            _set(
                                "state.persistent_spell_areas.{area_id}.water_flood_volume",
                                "_water_flood_volume",
                            ),
                            _set(
                                "state.persistent_spell_areas.{area_id}.water_flood_rise",
                                "flood_rise",
                            ),
                            _set(
                                "state.persistent_spell_areas.{area_id}.water_wave_volume",
                                "_water_wave_volume",
                            ),
                            _set(
                                "state.persistent_spell_areas.{area_id}.water_wave_direction",
                                "wave_direction",
                            ),
                        ],
                    ),
                ],
                public=True,
            ),
            "combat",
            "Control Water changed",
            [("Source", "area_id"), ("Effect", "water_effect")],
        ),
    }
    bind_route(result["qualify_srd_water_route"])
    result.update(whirlpool_tools())
    result.update(pull_tools())
    return result


def bind_sources(changed):
    """Supported standing-body effects join the same paid casting and exact retirement."""
    if __package__:
        from .srd_water_whirlpool import bind_sources as bind_whirlpool
        from .srd_water_whirlpool import formation
    else:
        from srd_water_whirlpool import bind_sources as bind_whirlpool
        from srd_water_whirlpool import formation
    cast = changed["cast_spell_environment"]
    choices = [
        _optional("water_geometry_id", default=""),
        _optional("water_side", "number", 100, minimum=0, maximum=100),
        *_dimensions(),
        *_effect_choices(),
    ]
    cast["parameters"] += choices
    boundary = next(
        i
        for i, e in enumerate(cast["effects"])
        if e.get("condition") == "_environment_profile.concentration == true and "
        "caster_state.concentration_spell_id != null"
    )
    cast["effects"].insert(
        boundary,
        _when(
            "_environment_profile.environment_mode == 'control_water'",
            [
                _call(
                    "lookup_srd_water_body",
                    {"geometry_id": "{water_geometry_id}"},
                    ("_water_geometry", "_water_depth"),
                ),
                _require(
                    "_water_geometry.metadata.plane == _environment_plane",
                    "This selected effect needs actual same-plane water.",
                ),
                *_effect_flow(),
                *_conflicts(),
                _require("water_side > 0", "Select a positive actual source Cube."),
                _require(
                    "state.has_spell_magic_zones != true",
                    "Load the actual whole-water magic-zone clipping consumer first.",
                ),
                _set(
                    "_water_cube",
                    {
                        "kind": "'box'",
                        "min": [
                            "_environment_cast_origin[0] - water_side * 0.5",
                            "_environment_cast_origin[1] - water_side * 0.5",
                            "_environment_cast_origin[2]",
                        ],
                        "max": [
                            "_environment_cast_origin[0] + water_side * 0.5",
                            "_environment_cast_origin[1] + water_side * 0.5",
                            "_environment_cast_origin[2] + water_side",
                        ],
                    },
                ),
                _when(
                    "water_effect == 'redirect_flow' or (water_effect == 'flood' and "
                    "_water_geometry.metadata.srd_water.body_category == 'large_body')",
                    [
                        _require(
                            "_water_cube.min[2] < _water_geometry.volume.max[2] and "
                            "_water_cube.max[2] > _water_geometry.volume.min[2]",
                            "The chosen Cube must contain actual represented water.",
                        ),
                    ],
                    [
                        _require(
                            "_water_cube.min[2] == _water_geometry.volume.min[2] and "
                            "_water_cube.max[2] >= _water_geometry.volume.max[2]",
                            "The supported trench Cube must contain this water's complete depth.",
                        ),
                    ],
                ),
                _require(
                    " and ".join(
                        f"_water_cube.min[{i}] >= _water_geometry.volume.min[{i}] and "
                        f"_water_cube.max[{i}] <= _water_geometry.volume.max[{i}]"
                        for i in range(2)
                    ),
                    "The whole source Cube footprint must fit this actual water body.",
                ),
                _require(
                    " and ".join(
                        f"_water_cube.min[{i}] >= state.spatial_scene.map.bounds.min[{i}] and "
                        f"_water_cube.max[{i}] <= state.spatial_scene.map.bounds.max[{i}]"
                        for i in range(3)
                    ),
                    "The whole source Cube must fit the actual admitted map.",
                ),
                _when("water_effect != 'redirect_flow'", _open_cube()),
                _set("_water_trench", None),
                _set("_water_flood_volume", None),
                _set("_water_wave_volume", None),
                _set("_water_whirlpool", None),
                _when(
                    "water_effect == 'part_water'",
                    [
                        _require(
                            "trench_width > 0 and trench_width < water_side",
                            "Leave real water on both sides of a positive trench.",
                        ),
                        *_trench("_water_cube", "trench_width", "trench_axis"),
                    ],
                ),
                _when("water_effect == 'flood'", [*_flood("_water_cube", "flood_rise")]),
                _when("water_effect == 'whirlpool'", formation("_water_cube")),
                _set("_water_cast_clock", "'exploration'"),
                _set("_water_cast_turn", 0),
                _when(
                    "combat.turn_active == true",
                    [
                        _set("_water_cast_clock", "'combat'"),
                        _set("_water_cast_turn", "combat.turn_sequence"),
                    ],
                ),
            ],
        ),
    )
    cast["effects"].append(
        _when(
            "_environment_profile.environment_mode == 'control_water'",
            [
                *[
                    _set("state.persistent_spell_areas.{_environment_cast_id}." + field, value)
                    for field, value in {
                        "source_sha256": repr(SOURCE_SHA),
                        "continuation_sha256": repr(CONTINUATION_SHA),
                        "water_geometry_id": "water_geometry_id",
                        "water_map_id": "_water_geometry.map_id",
                        "water_body_stamp": "_water_geometry.volume",
                        "water_side": "water_side",
                        "water_cube": "_water_cube",
                        "water_trench": "_water_trench",
                        "water_trench_width": "trench_width",
                        "water_trench_axis": "trench_axis",
                        "water_effect": "water_effect",
                        "water_body_flow": "_water_geometry.metadata.srd_water.flow",
                        "water_redirect_direction": "_water_redirect_direction",
                        "water_flood_volume": "_water_flood_volume",
                        "water_flood_rise": "flood_rise",
                        "water_wave_volume": "_water_wave_volume",
                        "water_wave_direction": "wave_direction",
                        "water_whirlpool": "_water_whirlpool",
                        "whirlpool_effect_sequence": 1,
                        "water_body_category": "_water_geometry.metadata.srd_water.body_category",
                        "water_cast_clock": "_water_cast_clock",
                        "water_cast_turn": "_water_cast_turn",
                        "water_refill_retained": False,
                    }.items()
                ],
                _when(
                    "water_effect == 'flood' and "
                    "_water_geometry.metadata.srd_water.body_category == 'large_body'",
                    [
                        _set("state.has_control_water_waves", True),
                        _call(
                            "resolve_srd_control_water_wave", {"area_id": "{_environment_cast_id}"}
                        ),
                    ],
                ),
            ],
        )
    )
    public = changed["cast_spell"]
    names = {"environment_" + parameter["name"] for parameter in choices}
    public["parameters"] = [p for p in public["parameters"] if p["name"] not in names]
    for parameter in choices:
        choice = deepcopy(parameter)
        choice["name"] = "environment_" + parameter["name"]
        public["parameters"].append(choice)
    for effects in _effect_lists(public["effects"]):
        for e in effects:
            if e.get("call") == "cast_spell_environment":
                e["params"].update({p["name"]: "{environment_" + p["name"] + "}" for p in choices})
    changed["retire_srd_environment_ground_source"]["effects"].insert(
        0,
        _when(
            "state.persistent_spell_areas[area_id].mode == 'control_water'",
            [
                _call("retain_srd_control_water_refill", {"area_id": "{area_id}"}),
            ],
        ),
    )
    change = changed["change_srd_control_water"]
    spend = next(i for i, e in enumerate(change["effects"]) if e.get("call") == "spend_srd_action")
    change["effects"][spend:spend] = [
        _when(
            "water_effect == 'whirlpool'",
            [
                _require(
                    "_water_source.water_effect != 'part_water'",
                    "Changing an active dry trench to Whirlpool needs its actual "
                    "next-round refill.",
                ),
                _set("_whirl_pending_refills", []),
                _when(
                    "state.turn_effects != null",
                    [_set("_whirl_pending_refills", "state.turn_effects")],
                ),
                _each(
                    "_whirl_pending_refills",
                    "_whirl_pending_refill",
                    [
                        _when(
                            "_whirl_pending_refill.kind == 'environment_water_refill' and "
                            "_whirl_pending_refill.geometry_id == _water_source.water_geometry_id "
                            "and _whirl_pending_refill.source_sha256 == "
                            "_water_source.source_sha256",
                            [
                                _require(
                                    "state.lasting_tick >= _whirl_pending_refill.ready_tick",
                                    "Whirlpool needs the actual next-round refill to finish first.",
                                ),
                            ],
                        ),
                    ],
                ),
                *formation("_water_source.water_cube"),
            ],
        ),
    ]
    transition = next(
        i
        for i, e in enumerate(change["effects"])
        if e.get("set", {}).get("field", "").endswith(".water_effect")
    )
    change["effects"][transition:transition] = [
        _when(
            "water_effect == 'whirlpool'",
            [
                _set(
                    "state.persistent_spell_areas.{_environment_area_key}.whirlpool_effect_sequence",
                    "_water_source.whirlpool_effect_sequence + 1",
                ),
            ],
        ),
    ]
    change["effects"].append(
        _when(
            "water_effect == 'whirlpool'",
            [
                _set(
                    "state.persistent_spell_areas.{_environment_area_key}.water_whirlpool",
                    "_water_whirlpool",
                ),
            ],
        )
    )
    bind_whirlpool(changed)
    if __package__:
        from .srd_whirlpool_pull import bind_sources as bind_pull
    else:
        from srd_whirlpool_pull import bind_sources as bind_pull
    bind_pull(changed)
    change["effects"].append(
        _when(
            "water_effect == 'flood' and _water_geometry.metadata.srd_water.body_category == "
            "'large_body'",
            [
                _set("state.has_control_water_waves", True),
                _call("resolve_srd_control_water_wave", {"area_id": "{area_id}"}),
            ],
        )
    )


def bind_runtime_consumers(text):
    """The final ordinary movement owner keeps all body, cost and sole mutation effects."""
    import yaml

    if __package__:
        from .srd_timed_senses import _replace_tools
    else:
        from srd_timed_senses import _replace_tools
    runtime = yaml.load(text, Loader=yaml.CSafeLoader)
    changed = {}
    begin = deepcopy(runtime["tools"]["begin_combat_turn"])
    if "repeat_srd_control_water_waves" not in str(begin["effects"]):
        begin["effects"].append(
            _when(
                "lasting_state.has_control_water_waves == true",
                [_call("repeat_srd_control_water_waves", {"caster_state": "{_actor_id}"})],
            )
        )
        changed["begin_combat_turn"] = begin
    process = deepcopy(runtime["tools"]["process_srd_turn_effects"])
    if "environment_water_refill" not in str(process["effects"]):
        loop = next(
            e["for_each"]
            for e in process["effects"]
            if e.get("for_each", {}).get("collection") == "_pending_turn_effects"
        )
        original = loop["do"]
        loop["do"] = [
            {
                **_when(
                    "_effect.kind == 'environment_water_refill'",
                    [
                        _when(
                            "state.lasting_tick < _effect.ready_tick",
                            [_push("_kept_turn_effects", "_effect")],
                        ),
                    ],
                ),
                "on_false": original,
            },
        ]
        changed["process_srd_turn_effects"] = process
    movement = deepcopy(runtime["tools"]["move_srd_scene_creature_environment_segment"])
    if "qualify_srd_water_route" in str(movement["effects"]):
        return _replace_tools(text, changed)
    boundary = next(
        i
        for i, e in enumerate(movement["effects"])
        if e.get("call") == "srd_information_movement_cost"
    )
    movement["effects"].insert(
        boundary,
        _when(
            "lasting_state.has_srd_water_body == true",
            [
                _call(
                    "qualify_srd_water_route",
                    {
                        "actor_state": "{_actor_state_id}",
                        "start": "{_move_origin}",
                        "end": "{destination}",
                        "mode": "{mode}",
                        "lasting_state": "{_lasting_state_id}",
                    },
                ),
            ],
        ),
    )
    changed["move_srd_scene_creature_environment_segment"] = movement
    return _replace_tools(text, changed)


def bind_flood_receivers(text, *, receiver_factory=None):
    """Capture final canonical object relocation; a wave never spends the vehicle's movement."""
    import json
    import re

    import yaml

    if __package__:
        from .srd_timed_senses import _replace_tools
    else:
        from srd_timed_senses import _replace_tools
    if receiver_factory is None:
        if __package__:
            from .srd_reverse_gravity import _receiver
        else:
            from srd_reverse_gravity import _receiver
        receiver_factory = _receiver
    runtime = yaml.load(text, Loader=yaml.CSafeLoader)
    names = {
        "move_srd_reverse_gravity_subject": "move_srd_control_water_vehicle",
        "move_srd_reverse_gravity_subject_node": "move_srd_control_water_vehicle_node",
        "qualify_srd_reverse_gravity_motion": "qualify_srd_control_water_wave_motion",
        "_gravity_motion_allowed": "_water_wave_motion_allowed",
        "_gravity_up_sweep": "_water_wave_sweep",
    }
    value = json.dumps(receiver_factory(runtime["tools"]))
    value = value.replace(
        "state.reverse_gravity_pending_motion.consumed",
        "state.control_water_pending_motion.consumed",
    )
    for old, new in names.items():
        value = re.sub(r"\b" + re.escape(old) + r"\b", new, value)
    changed = json.loads(value)
    for tool in changed.values():
        tool["description"] = "Commit only this live Control Water wave's actual vehicle receipt."
        for effects in _effect_lists(tool["effects"]):
            for effect in effects:
                if effect.get("require", {}).get("condition") == (
                    "_water_wave_sweep.supported == true and _water_wave_sweep.clear == true"
                ):
                    effect["require"]["error"] = (
                        "A real obstacle blocks this vehicle's source-caused wave carriage."
                    )
    return _replace_tools(text, changed)
