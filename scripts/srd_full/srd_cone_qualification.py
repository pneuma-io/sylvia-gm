"""Exact finite circular Cone intersection with admitted vertical box creatures."""

if __package__:
    from .srd_actor_spell_modifiers import _call, _entity, _parameter, _require, _set, _tool, _when
    from .srd_timed_senses import _replace_tools
else:
    from srd_actor_spell_modifiers import _call, _entity, _parameter, _require, _set, _tool, _when
    from srd_timed_senses import _replace_tools


def _push(field, value):
    return {"list_push": {"list": field, "value": value}}


def _each(collection, item, effects):
    return {"for_each": {"collection": collection, "as": item, "do": effects}}


def _shape_parameters():
    return [
        _parameter("length_feet", "number", minimum=0.000001),
        _parameter("width_feet", "number", required=False, default=0, minimum=0),
        _parameter(
            "apex",
            "array",
            required=False,
            default=[],
            items={"type": "number"},
            min_items=0,
            max_items=3,
        ),
        _parameter(
            "direction",
            "array",
            required=False,
            default=[],
            items={"type": "number"},
            min_items=0,
            max_items=3,
        ),
        _parameter("include_origin", "boolean", required=False, default=False),
    ]


def _frame(source_index="source_index", target_index="target_index"):
    """Derive the actual source-facing surface point; selected origins stay in its volume."""
    effects = [
        _require(
            "state.spatial_scene != null and state.spatial_scene.map.grid.unit == 'ft'",
            "Cone qualification needs an admitted scene measured in feet.",
        ),
        _require(
            (
                "source_index >= 0 and target_index >= 0 and source_index < "
                "len(state.spatial_scene.entities) and target_index < "
                "len(state.spatial_scene.entities)"
            ),
            "Cone source and target must be admitted scene entries.",
        ),
        _set("_cone_source", "state.spatial_scene.entities[source_index]"),
        _set("_cone_target", "state.spatial_scene.entities[target_index]"),
        _require(
            "_cone_source.active == true and _cone_source.footprint.kind == 'box'",
            "Cone origin currently requires an active admitted box footprint.",
        ),
        _require(
            "_cone_source.metadata.plane != null and _cone_target.metadata.plane != null",
            "Cone entries need their actual planes.",
        ),
        _set("_cone_heading", "_cone_source.facing"),
        _when(
            "len(direction) != 0",
            [
                _require(
                    "len(direction) == 2 or (len(direction) == 3 and direction[2] == 0)",
                    "Cone qualification currently supports horizontal chosen directions.",
                ),
                _set("_cone_heading", ["direction[0]", "direction[1]"]),
            ],
        ),
        _set(
            "_cone_direction_norm",
            "(_cone_heading[0] ** 2 + _cone_heading[1] ** 2) ** 0.5",
        ),
        _require("_cone_direction_norm > 0", "Cone source needs a nonzero admitted facing."),
        _set(
            "_cone_axis",
            [
                "_cone_heading[0] * _cone_direction_norm ** -1",
                "_cone_heading[1] * _cone_direction_norm ** -1",
            ],
        ),
        _set("_cone_surface_distance", 1000000000),
        _when(
            "_cone_axis[0] != 0",
            [
                _set(
                    "_cone_surface_distance",
                    (
                        "min(_cone_surface_distance, _cone_source.footprint.half_extents[0] * "
                        "abs(_cone_axis[0]) ** -1)"
                    ),
                )
            ],
        ),
        _when(
            "_cone_axis[1] != 0",
            [
                _set(
                    "_cone_surface_distance",
                    (
                        "min(_cone_surface_distance, _cone_source.footprint.half_extents[1] * "
                        "abs(_cone_axis[1]) ** -1)"
                    ),
                )
            ],
        ),
        _set(
            "_srd_cone_origin",
            [
                "_cone_source.position[0] + _cone_axis[0] * _cone_surface_distance",
                "_cone_source.position[1] + _cone_axis[1] * _cone_surface_distance",
                "_cone_source.position[2] + _cone_source.height * 0.5",
            ],
        ),
        _when(
            "len(apex) != 0",
            [
                _require(
                    (
                        "len(apex) == 3 and abs(apex[0] - _cone_source.position[0]) <= "
                        "_cone_source.footprint.half_extents[0] and abs(apex[1] - "
                        "_cone_source.position[1]) <= _cone_source.footprint.half_extents[1] "
                        "and apex[2] >= _cone_source.position[2] and apex[2] <= "
                        "_cone_source.position[2] + _cone_source.height"
                    ),
                    "Choose the Cone apex only within its actual source volume.",
                ),
                _set("_srd_cone_origin", "apex"),
            ],
        ),
        _set("_cone_width", "width_feet"),
        _when("_cone_width == 0", [_set("_cone_width", "length_feet")]),
        _set("_cone_slope_squared", "(_cone_width * 0.5 * length_feet ** -1) ** 2"),
        _set("_srd_cone_intersects", False),
    ]

    if target_index is None:
        effects = [e for e in effects if e.get("set", {}).get("field") != "_cone_target"]
        if source_index is None:
            del effects[1]
        else:
            effects[1]["require"]["condition"] = (
                f"{source_index} >= 0 and {source_index} < len(state.spatial_scene.entities)"
            )
        plane = next(e for e in effects if "Cone entries" in e.get("require", {}).get("error", ""))
        plane["require"]["condition"] = "_cone_source.metadata.plane != null"
    source = next(e for e in effects if e.get("set", {}).get("field") == "_cone_source")
    if source_index is None:
        effects.remove(source)
    else:
        source["set"]["value"] = f"state.spatial_scene.entities[{source_index}]"
    return effects


def _box_bounds():
    return [
        _require(
            "_cone_target.footprint.kind == 'box'",
            "Exact Cone target qualification currently supports box footprints.",
        ),
        _set(
            "_cone_box",
            {
                "x0": (
                    "_cone_target.position[0] - _cone_target.footprint.half_extents[0] - "
                    "_srd_cone_origin[0]"
                ),
                "x1": (
                    "_cone_target.position[0] + _cone_target.footprint.half_extents[0] - "
                    "_srd_cone_origin[0]"
                ),
                "y0": (
                    "_cone_target.position[1] - _cone_target.footprint.half_extents[1] - "
                    "_srd_cone_origin[1]"
                ),
                "y1": (
                    "_cone_target.position[1] + _cone_target.footprint.half_extents[1] - "
                    "_srd_cone_origin[1]"
                ),
                "z": (
                    "max(_cone_target.position[2] - _srd_cone_origin[2], 0, "
                    "_srd_cone_origin[2] - _cone_target.position[2] - _cone_target.height)"
                ),
            },
        ),
    ]


def _cardinal():
    """A cardinal cone grows monotonically across the constant transverse box section."""
    return [
        _set(
            "_cone_cardinal",
            {
                "minimum": (
                    "min(_cone_box.x0 * _cone_axis[0], _cone_box.x1 * _cone_axis[0]) + "
                    "min(_cone_box.y0 * _cone_axis[1], _cone_box.y1 * _cone_axis[1])"
                ),
                "maximum": (
                    "min(length_feet, max(_cone_box.x0 * _cone_axis[0], "
                    "_cone_box.x1 * _cone_axis[0]) + max(_cone_box.y0 * _cone_axis[1], "
                    "_cone_box.y1 * _cone_axis[1]))"
                ),
                "transverse_squared": (
                    "(max(_cone_box.y0, 0, -_cone_box.y1) * _cone_axis[0]) ** 2 + "
                    "(max(_cone_box.x0, 0, -_cone_box.x1) * _cone_axis[1]) ** 2 + "
                    "_cone_box.z ** 2"
                ),
            },
        ),
        _set(
            "_srd_cone_intersects",
            (
                "_cone_cardinal.maximum > 0 and _cone_cardinal.maximum >= "
                "max(0, _cone_cardinal.minimum) and _cone_cardinal.transverse_squared <= "
                "_cone_slope_squared * _cone_cardinal.maximum ** 2"
            ),
        ),
    ]


def _edge():
    """Minimize the exact cone quadratic over each rectangle edge clipped to its axial slab."""
    return [
        _set(
            "_cone_e",
            {
                "t0": "_cone_edge.x0 * _cone_axis[0] + _cone_edge.y0 * _cone_axis[1]",
                "t1": "_cone_edge.x1 * _cone_axis[0] + _cone_edge.y1 * _cone_axis[1]",
                "v0": "-_cone_edge.x0 * _cone_axis[1] + _cone_edge.y0 * _cone_axis[0]",
                "v1": "-_cone_edge.x1 * _cone_axis[1] + _cone_edge.y1 * _cone_axis[0]",
            },
        ),
        _set(
            "_cone_d",
            {
                "t": "_cone_e.t1 - _cone_e.t0",
                "v": "_cone_e.v1 - _cone_e.v0",
                "a": (
                    "(_cone_e.v1 - _cone_e.v0) ** 2 - _cone_slope_squared * (_cone_e.t1 - "
                    "_cone_e.t0) ** 2"
                ),
                "b": (
                    "2 * (_cone_e.v0 * (_cone_e.v1 - _cone_e.v0) - _cone_slope_squared * "
                    "_cone_e.t0 * (_cone_e.t1 - _cone_e.t0))"
                ),
            },
        ),
        _set("_cone_u0", 0),
        _set("_cone_u1", 1),
        _when(
            "_cone_d.t > 0",
            [
                _set("_cone_u0", "max(0, -_cone_e.t0 * _cone_d.t ** -1)"),
                _set("_cone_u1", "min(1, (length_feet - _cone_e.t0) * _cone_d.t ** -1)"),
            ],
        ),
        _when(
            "_cone_d.t < 0",
            [
                _set("_cone_u0", "max(0, (length_feet - _cone_e.t0) * _cone_d.t ** -1)"),
                _set("_cone_u1", "min(1, -_cone_e.t0 * _cone_d.t ** -1)"),
            ],
        ),
        _when(
            "_cone_d.t == 0 and (_cone_e.t0 < 0 or _cone_e.t0 > length_feet)", [_set("_cone_u0", 2)]
        ),
        _when(
            "_cone_u0 <= _cone_u1",
            [
                _set("_cone_u_min", "_cone_u0"),
                _when(
                    "_cone_d.a > 0",
                    [
                        _set(
                            "_cone_u_min",
                            "max(_cone_u0, min(_cone_u1, -_cone_d.b * 0.5 * _cone_d.a ** -1))",
                        )
                    ],
                ),
                _set("_cone_candidates", ["_cone_u0", "_cone_u1", "_cone_u_min"]),
                _each(
                    "_cone_candidates",
                    "_cone_u",
                    [
                        _when(
                            (
                                "_cone_e.t0 + _cone_d.t * _cone_u > 0 and (_cone_e.v0 + "
                                "_cone_d.v * _cone_u) ** 2 + _cone_box.z ** 2 <= "
                                "_cone_slope_squared * (_cone_e.t0 + _cone_d.t * _cone_u) ** 2"
                            ),
                            [_set("_srd_cone_intersects", True)],
                        ),
                    ],
                ),
            ],
        ),
        _when(
            "_cone_d.t != 0",
            [
                _set("_cone_cap_u", "(length_feet - _cone_e.t0) * _cone_d.t ** -1"),
                _when(
                    "_cone_cap_u >= 0 and _cone_cap_u <= 1",
                    [_push("_cone_cap_values", "_cone_e.v0 + _cone_d.v * _cone_cap_u")],
                ),
            ],
            [
                _when(
                    "_cone_e.t0 == length_feet",
                    [
                        _push("_cone_cap_values", "_cone_e.v0"),
                        _push("_cone_cap_values", "_cone_e.v1"),
                    ],
                ),
            ],
        ),
    ]


def _arbitrary_horizontal():
    """The quadratic has no interior negative minimum; edges and the far cap suffice."""
    return [
        _set(
            "_cone_edges",
            [
                {
                    "x0": "_cone_box.x0",
                    "y0": "_cone_box.y0",
                    "x1": "_cone_box.x1",
                    "y1": "_cone_box.y0",
                },
                {
                    "x0": "_cone_box.x1",
                    "y0": "_cone_box.y0",
                    "x1": "_cone_box.x1",
                    "y1": "_cone_box.y1",
                },
                {
                    "x0": "_cone_box.x1",
                    "y0": "_cone_box.y1",
                    "x1": "_cone_box.x0",
                    "y1": "_cone_box.y1",
                },
                {
                    "x0": "_cone_box.x0",
                    "y0": "_cone_box.y1",
                    "x1": "_cone_box.x0",
                    "y1": "_cone_box.y0",
                },
            ],
        ),
        _set("_cone_cap_values", []),
        _each("_cone_edges", "_cone_edge", _edge()),
        _when(
            "len(_cone_cap_values) > 0",
            [
                _set("_cone_cap_nearest", "max(min(_cone_cap_values), 0, -max(_cone_cap_values))"),
                _when(
                    (
                        "_cone_cap_nearest ** 2 + _cone_box.z ** 2 <= _cone_slope_squared * "
                        "length_feet ** 2"
                    ),
                    [_set("_srd_cone_intersects", True)],
                ),
            ],
        ),
    ]


def _intersection():
    """Use the same exact target intersection with this action's freshly read source frame."""
    return [
        _set("_srd_cone_intersects", False),
        _when(
            (
                "_cone_target.active == true and _cone_source.metadata.plane == "
                "_cone_target.metadata.plane"
            ),
            _box_bounds()
            + [
                _when(
                    "_cone_axis[0] == 0 or _cone_axis[1] == 0",
                    _cardinal(),
                    _arbitrary_horizontal(),
                ),
                _when(
                    (
                        "include_origin == true and _cone_box.x0 <= 0 and _cone_box.x1 >= "
                        "0 and _cone_box.y0 <= 0 and _cone_box.y1 >= 0 and _cone_box.z == 0"
                    ),
                    [_set("_srd_cone_intersects", True)],
                ),
            ],
        ),
    ]


def cone_tools():
    shape = _shape_parameters()
    qualify = _tool(
        "Qualify exact Cone intersection with admitted source and target volumes by scene index.",
        [
            _parameter("source_index", "integer", minimum=0),
            _parameter("target_index", "integer", minimum=0),
            *shape,
        ],
        _frame() + _intersection(),
    )
    collect = _tool(
        (
            "Derive every admitted actor creature intersecting this source's Cone without "
            "caller membership."
        ),
        [_entity("source_state"), *_shape_parameters()],
        [
            _require("state.spatial_scene != null", "Cone selection needs the admitted scene."),
            _set("_cone_scene_entities", "state.spatial_scene.entities"),
            _set("_cone_source", None),
            _set("_srd_cone_targets", []),
            _each(
                "_cone_scene_entities",
                "_cone_scene_entry",
                [
                    _when(
                        (
                            "_cone_scene_entry.active == true and (_cone_scene_entry.id == "
                            "_source_state_id or _cone_scene_entry.metadata.participant_ref == "
                            "_source_state_id)"
                        ),
                        [
                            _require(
                                "_cone_source == null",
                                "Cone source has ambiguous scene proxies.",
                            ),
                            _set("_cone_source", "_cone_scene_entry"),
                        ],
                    ),
                ],
            ),
            _require("_cone_source != null", "Cone source needs its actual admitted proxy."),
            *_frame(None, None),
            _set("_srd_cone_axis", "_cone_axis"),
            _set("_srd_cone_source", "_cone_source"),
            _each(
                "_cone_scene_entities",
                "_cone_candidate",
                [
                    _when(
                        (
                            "_cone_candidate.active == true and "
                            "_cone_candidate.metadata.participant_ref != null and "
                            "_cone_candidate.metadata.is_object != true and "
                            "_cone_candidate.metadata.creature_type != 'spell_force'"
                        ),
                        [
                            _call(
                                "check_srd_cone_creature_actor",
                                {
                                    "target_state": "{_cone_candidate.metadata.participant_ref}",
                                },
                                ("_cone_actual_creature",),
                            ),
                            _when(
                                "_cone_actual_creature == true",
                                [
                                    _set("_cone_target", "_cone_candidate"),
                                    _require(
                                        "_cone_target.metadata.plane != null",
                                        "Cone entries need their actual planes.",
                                    ),
                                    *_intersection(),
                                    _when(
                                        "_srd_cone_intersects == true",
                                        [
                                            _require(
                                                (
                                                    "_cone_candidate.metadata.participant_ref "
                                                    "not in _srd_cone_targets"
                                                ),
                                                (
                                                    "Cone creature has ambiguous admitted "
                                                    "scene proxies."
                                                ),
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
    actor = _tool(
        "Read the selected proxy's actual mechanics actor before treating it as a creature.",
        [_entity("target_state")],
        [
            _set(
                "_cone_actual_creature",
                (
                    "target_state.is_alive == true and target_state.creature_type != 'object' "
                    "and target_state.creature_type != "
                    "'spell_force'"
                ),
            ),
        ],
    )
    actor["pre_conditions"] = [
        {
            "condition": "target_state.creature_type != null",
            "error": "Cone creature proxy needs its actual typed mechanics actor.",
        }
    ]
    return {
        "qualify_srd_cone_admitted_box": qualify,
        "check_srd_cone_creature_actor": actor,
        "collect_srd_cone_creature_targets": collect,
    }


def extend_runtime(text):
    """Emit private package geometry owners without changing neutral spatial primitives."""
    changed = cone_tools()
    # The first insertion adds a separator; replacement normalizes the same owned fragments.
    return _replace_tools(_replace_tools(text, changed), changed)
