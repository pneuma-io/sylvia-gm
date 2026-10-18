"""Route SRD area membership through the ruleset-neutral occupied-volume query."""

from copy import deepcopy

from .srd_actor_spell_modifiers import _call, _entity, _parameter, _require, _set, _tool, _when
from .srd_cone_qualification import _each, _push, _shape_parameters


def _frame(source_index=None, target_index=None):
    """Choose a 3D source surface point, retaining the existing default facing."""
    effects = [
        _require(
            "state.spatial_scene != null and state.spatial_scene.map.grid.unit == 'ft'",
            "Cone qualification needs the admitted scene measured in feet.",
        )
    ]
    for name, index in (("source", source_index), ("target", target_index)):
        if index is not None:
            effects += [
                _require(
                    f"{index} >= 0 and {index} < len(state.spatial_scene.entities)",
                    "Cone source and target must be admitted scene entries.",
                ),
                _set(f"_cone_{name}", f"state.spatial_scene.entities[{index}]"),
            ]
    effects += [
        _require(
            "_cone_source.active == true and _cone_source.metadata.plane != null and "
            "(_cone_source.footprint.kind == 'box' or _cone_source.footprint.kind == 'circle')",
            "Cone source needs its actual active body and plane.",
        ),
        _set("_cone_heading", ["_cone_source.facing[0]", "_cone_source.facing[1]", 0]),
        _when(
            "len(direction) != 0",
            [
                _require(
                    "len(direction) == 2 or len(direction) == 3",
                    "Choose a 2D or 3D Cone direction.",
                ),
                _set("_cone_heading", ["direction[0]", "direction[1]", 0]),
                _when("len(direction) == 3", [_set("_cone_heading", "direction")]),
            ],
        ),
        _set(
            "_cone_direction_norm",
            "(_cone_heading[0] ** 2 + _cone_heading[1] ** 2 + _cone_heading[2] ** 2) ** 0.5",
        ),
        _require("_cone_direction_norm > 0", "Cone direction must be nonzero."),
        _set("_cone_axis3", [f"_cone_heading[{i}] * _cone_direction_norm ** -1" for i in range(3)]),
        _set("_cone_surface_distance", 1000000000),
        _when(
            "_cone_source.footprint.kind == 'box'",
            [
                _when(
                    f"_cone_axis3[{i}] != 0",
                    [
                        _set(
                            "_cone_surface_distance",
                            f"min(_cone_surface_distance, "
                            f"_cone_source.footprint.half_extents[{i}] * abs(_cone_axis3[{i}]) "
                            f"** -1)",
                        )
                    ],
                )
                for i in range(2)
            ],
            [
                _set("_cone_xy_norm", "(_cone_axis3[0] ** 2 + _cone_axis3[1] ** 2) ** 0.5"),
                _when(
                    "_cone_xy_norm > 0",
                    [
                        _set(
                            "_cone_surface_distance",
                            "_cone_source.footprint.radius * _cone_xy_norm ** -1",
                        )
                    ],
                ),
            ],
        ),
        _when(
            "_cone_axis3[2] != 0",
            [
                _set(
                    "_cone_surface_distance",
                    "min(_cone_surface_distance, _cone_source.height * 0.5 * abs(_cone_axis3[2]) "
                    "** -1)",
                )
            ],
        ),
        _set(
            "_srd_cone_origin",
            [
                "_cone_source.position[0] + _cone_axis3[0] * _cone_surface_distance",
                "_cone_source.position[1] + _cone_axis3[1] * _cone_surface_distance",
                "_cone_source.position[2] + _cone_source.height * 0.5 + _cone_axis3[2] * "
                "_cone_surface_distance",
            ],
        ),
        _when(
            "_cone_axis3[2] != 0",
            [
                _set("_cone_tilt_x", "_cone_source.position[0]"),
                _set("_cone_tilt_y", "_cone_source.position[1]"),
                _set(
                    "_cone_tilt_z",
                    "_cone_source.position[2] + _cone_source.height * 0.5 * (1 + _cone_axis3[2] "
                    "* abs(_cone_axis3[2]) ** -1)",
                ),
                _when(
                    "_cone_source.footprint.kind == 'box'",
                    [
                        _when(
                            f"_cone_axis3[{i}] != 0",
                            [
                                _set(
                                    f"_cone_tilt_{'xy'[i]}",
                                    f"_cone_source.position[{i}] + "
                                    f"_cone_source.footprint.half_extents[{i}] * "
                                    f"_cone_axis3[{i}] * abs(_cone_axis3[{i}]) ** -1",
                                )
                            ],
                        )
                        for i in range(2)
                    ],
                    [
                        _set("_cone_xy_norm", "(_cone_axis3[0] ** 2 + _cone_axis3[1] ** 2) ** 0.5"),
                        _when(
                            "_cone_xy_norm > 0",
                            [
                                _set(
                                    f"_cone_tilt_{'xy'[i]}",
                                    f"_cone_source.position[{i}] + _cone_source.footprint.radius "
                                    f"* _cone_axis3[{i}] * _cone_xy_norm ** -1",
                                )
                                for i in range(2)
                            ],
                        ),
                    ],
                ),
                _set("_srd_cone_origin", ["_cone_tilt_x", "_cone_tilt_y", "_cone_tilt_z"]),
            ],
        ),
        _when(
            "len(apex) != 0",
            [
                _require("len(apex) == 3", "Choose a three-dimensional Cone apex."),
                _require(
                    "apex[2] >= _cone_source.position[2] and apex[2] <= _cone_source.position[2] "
                    "+ _cone_source.height",
                    "Choose the Cone apex within its actual source volume.",
                ),
                _when(
                    "_cone_source.footprint.kind == 'box'",
                    [
                        _require(
                            "abs(apex[0] - _cone_source.position[0]) <= "
                            "_cone_source.footprint.half_extents[0] and "
                            "abs(apex[1] - _cone_source.position[1]) <= "
                            "_cone_source.footprint.half_extents[1]",
                            "Choose the Cone apex within its actual source volume.",
                        ),
                    ],
                    [
                        _require(
                            "(apex[0] - _cone_source.position[0]) ** 2 + (apex[1] - "
                            "_cone_source.position[1]) ** 2 <= _cone_source.footprint.radius ** 2",
                            "Choose the Cone apex within its actual source volume.",
                        ),
                    ],
                ),
                _set("_srd_cone_origin", "apex"),
            ],
        ),
        _set("_cone_width", "width_feet"),
        _when("_cone_width == 0", [_set("_cone_width", "length_feet")]),
        _set("_srd_cone_axis", []),
        _when(
            "_cone_axis3[2] == 0", [_set("_srd_cone_axis", ["_cone_axis3[0]", "_cone_axis3[1]"])]
        ),
        _set("_srd_cone_source", "_cone_source"),
    ]
    return effects


def _query():
    """Resolve one committed-scene query and retain its immutable provenance."""
    return [
        _set("_cone_radius", "_cone_width * 0.5"),
        {
            "spatial_query": {
                "scene": "state.spatial_scene",
                "operation": "area",
                "query": {
                    "map_id": "{state.spatial_scene.map.id}",
                    "shape": {
                        "kind": "cone",
                        "apex": "{_srd_cone_origin}",
                        "direction": "{_cone_axis3}",
                        "length": "{length_feet}",
                        "radius": "{_cone_radius}",
                    },
                    "include": ["entities"],
                    "max_results": 4096,
                },
            },
            "store_as": "_cone_area",
        },
        _require(
            "_cone_area.truncated != true", "Cone exceeds the supported scene recipient bound."
        ),
        _set("_cone_hit_ids", []),
        _set("_cone_hit_rows", "_cone_area.results"),
        _each("_cone_hit_rows", "_cone_area_hit", [_push("_cone_hit_ids", "_cone_area_hit.id")]),
    ]


def _intersection():
    """Exclude pure apex contact unless the spell explicitly includes its origin."""
    return [
        _set("_srd_cone_intersects", False),
        _when(
            "_cone_target.active == true and _cone_source.metadata.plane == "
            "_cone_target.metadata.plane and _cone_target.id in _cone_hit_ids",
            [
                _set(
                    "_cone_forward_extent",
                    "(_cone_target.position[0] - _srd_cone_origin[0]) * _cone_axis3[0] + "
                    "(_cone_target.position[1] - _srd_cone_origin[1]) * _cone_axis3[1] + "
                    "(_cone_target.position[2] + _cone_target.height * 0.5 - "
                    "_srd_cone_origin[2]) * _cone_axis3[2] + "
                    "_cone_target.height * 0.5 * abs(_cone_axis3[2])",
                ),
                _when(
                    "_cone_target.footprint.kind == 'box'",
                    [
                        _set(
                            "_cone_forward_extent",
                            "_cone_forward_extent + _cone_target.footprint.half_extents[0] * "
                            "abs(_cone_axis3[0]) + _cone_target.footprint.half_extents[1] * "
                            "abs(_cone_axis3[1])",
                        ),
                    ],
                    [
                        _set(
                            "_cone_forward_extent",
                            "_cone_forward_extent + _cone_target.footprint.radius * "
                            "(_cone_axis3[0] ** 2 + _cone_axis3[1] ** 2) ** 0.5",
                        ),
                    ],
                ),
                _set(
                    "_srd_cone_intersects",
                    "include_origin == true or _cone_forward_extent > 0.000000001",
                ),
            ],
        ),
    ]


def bind_coordinate_cones(runtime):
    """Replace private geometry helpers without altering SRD spell profiles or paid owners."""
    tools = runtime["tools"]
    tools["qualify_srd_cone_admitted_box"] = _tool(
        "Qualify Cone intersection with the actual Box or circular body at any 3D direction.",
        [
            _parameter("source_index", "integer", minimum=0),
            _parameter("target_index", "integer", minimum=0),
            *_shape_parameters(),
        ],
        _frame("source_index", "target_index") + _query() + _intersection(),
    )
    tools["collect_srd_cone_creature_targets"] = _tool(
        "Derive actual living creature recipients from the committed 3D Cone.",
        [_entity("source_state"), *_shape_parameters()],
        [
            _require("state.spatial_scene != null", "Cone selection needs the admitted scene."),
            _set("_cone_source", None),
            _set("_cone_scene_entities", "state.spatial_scene.entities"),
            _each(
                "_cone_scene_entities",
                "_cone_scene_entry",
                [
                    _when(
                        "_cone_scene_entry.active == true and (_cone_scene_entry.id == "
                        "_source_state_id or "
                        "_cone_scene_entry.metadata.participant_ref == _source_state_id)",
                        [
                            _require(
                                "_cone_source == null", "Cone source has ambiguous scene proxies."
                            ),
                            _set("_cone_source", "_cone_scene_entry"),
                        ],
                    ),
                ],
            ),
            _require("_cone_source != null", "Cone source needs its actual admitted proxy."),
            *_frame(),
            *_query(),
            _set("_srd_cone_targets", []),
            _each(
                "_cone_scene_entities",
                "_cone_candidate",
                [
                    _when(
                        "_cone_candidate.id in _cone_hit_ids and _cone_candidate.active == true "
                        "and "
                        "_cone_candidate.metadata.participant_ref != null and "
                        "_cone_candidate.metadata.is_object != true and "
                        "_cone_candidate.metadata.creature_type != 'spell_force'",
                        [
                            _call(
                                "check_srd_cone_creature_actor",
                                {"target_state": "{_cone_candidate.metadata.participant_ref}"},
                                ("_cone_actual_creature",),
                            ),
                            _when(
                                "_cone_actual_creature == true",
                                [
                                    _set("_cone_target", "_cone_candidate"),
                                    _require(
                                        "_cone_target.metadata.plane != null",
                                        "Cone creatures need their actual admitted planes.",
                                    ),
                                    *_intersection(),
                                    _when(
                                        "_srd_cone_intersects == true",
                                        [
                                            _require(
                                                "_cone_candidate.metadata.participant_ref not in "
                                                "_srd_cone_targets",
                                                "Cone creature has ambiguous admitted scene "
                                                "proxies.",
                                            ),
                                            _require(
                                                "len(_srd_cone_targets) < 64",
                                                "Resolve at most 64 affected creatures.",
                                            ),
                                            _push(
                                                "_srd_cone_targets",
                                                "_cone_candidate.metadata.participant_ref",
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
    pool = runtime.get("parameter_declarations", {})
    parameters = [
        deepcopy(pool[p]) if isinstance(p, str) else deepcopy(p)
        for p in tools["collect_srd_spell_area_cover_targets"]["parameters"]
    ]
    tools["collect_srd_spell_area_cover_targets"] = _tool(
        "Apply existing physical point-area Cover to the chosen 3D origin and actual creature "
        "bodies.",
        parameters,
        [
            _call(
                "collect_srd_sunburst_cover_targets",
                {
                    "source_state": "{_source_state_id}",
                    "targets": "{targets}",
                    "origin": "{origin}",
                },
                ("_srd_area_targets", "_srd_area_half_cover", "_srd_area_three_quarters"),
            )
        ],
    )
    return {
        "qualify_srd_cone_admitted_box",
        "collect_srd_cone_creature_targets",
        "collect_srd_spell_area_cover_targets",
    }
