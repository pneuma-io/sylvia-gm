"""A printed Fire ring is a saved Cylinder shell, with no fire in its inner hole."""

from copy import deepcopy

if __package__:
    from .srd_scene_relations import POSITION_EXPORTS, _snapshot, _spatial
    from .srd_spell_environment import (
        _array,
        _call,
        _effect_lists,
        _entity,
        _environment_tool,
        _optional,
        _param,
        _push,
        _require,
        _set,
        _when,
    )
else:
    from srd_scene_relations import POSITION_EXPORTS, _snapshot, _spatial
    from srd_spell_environment import (
        _array,
        _call,
        _effect_lists,
        _entity,
        _environment_tool,
        _optional,
        _param,
        _push,
        _require,
        _set,
        _when,
    )

SOURCE_SHA256 = "f47aa77fef781cc935dd1f3983da93a43c07d667bd51dfd8b4d874425c50c489"
BODY_EXPORTS = ("_body_hit", "_body_entry", "_body_exit", "_body_bounds")
SEGMENT_EXPORTS = ("_segment_hit", "_segment_entry", "_segment_exit")


def _difference():
    """Subtract one contained interval using the same clipped-interval rule as Fog."""
    full = [_push("_ring_intervals", {"entry": "_ring_outer_entry", "exit": "_ring_outer_exit"})]
    return [
        _set("_ring_intervals", []),
        _when(
            "_ring_inner_all != true or _ring_inner_entry > _ring_inner_exit or "
            "_ring_inner_exit < _ring_outer_entry or _ring_inner_entry > _ring_outer_exit",
            full,
            [
                _set("_ring_inner_entry", "max(_ring_inner_entry, _ring_outer_entry)"),
                _set("_ring_inner_exit", "min(_ring_inner_exit, _ring_outer_exit)"),
                _when(
                    "_ring_inner_entry > _ring_outer_entry",
                    [
                        _push(
                            "_ring_intervals",
                            {"entry": "_ring_outer_entry", "exit": "_ring_inner_entry"},
                        )
                    ],
                ),
                _when(
                    "_ring_inner_exit < _ring_outer_exit",
                    [
                        _push(
                            "_ring_intervals",
                            {"entry": "_ring_inner_exit", "exit": "_ring_outer_exit"},
                        )
                    ],
                ),
            ],
        ),
    ]


def _source_shape_requirements(source):
    """Read only the genuine saved one-foot shell selected by the actual Fire cast."""
    capture = _set("_ring_shape", source)
    source = "_ring_shape"
    return [
        capture,
        _require(
            f"{source}.profile == 'environment' and {source}.mode == 'wall_fire' and "
            f"{source}.volume.kind == 'annulus' and {source}.source_sha256 == {SOURCE_SHA256!r}",
            "Use the actual pinned Wall of Fire ring source.",
        ),
        _require(
            f"{source}.volume.outer.kind == 'cylinder' and {source}.volume.inner.kind == "
            f"'cylinder' and {source}.volume.outer.height == 20 and "
            f"{source}.volume.inner.height == 20 and {source}.volume.outer.radius <= 10 "
            f"and {source}.volume.inner.radius > 0 and abs({source}.volume.outer.radius - "
            f"{source}.volume.inner.radius - 1) < 0.000000001",
            "The selected printed ring must retain its actual diameter, height and thickness.",
        ),
        _require(
            " and ".join(
                f"{source}.volume.outer.center[{i}] == {source}.origin[{i}] and "
                f"{source}.volume.inner.center[{i}] == {source}.origin[{i}]"
                for i in range(3)
            ),
            "Both saved Cylinders must retain the same actual cast origin.",
        ),
    ]


def _cylinder_body(existing):
    """Bind only a qualified local outer-Cylinder projection before unchanged body math."""
    tool = deepcopy(existing["resolve_srd_environment_cylinder_body_segment"])
    requirement = next(
        i
        for i, effect in enumerate(tool["effects"])
        if "_sphere_geometry_source.profile" in effect.get("require", {}).get("condition", "")
    )
    tool["effects"][requirement] = _when(
        "_sphere_geometry_source.mode == 'wall_fire' and "
        "_sphere_geometry_source.volume.kind == 'annulus'",
        [
            *_source_shape_requirements("_sphere_geometry_source"),
            _require(
                "_body_moving.footprint.kind == 'box'",
                "Ring entry needs the admitted Box creature body.",
            ),
            _set(
                "_sphere_geometry_source",
                {
                    "origin": "_ring_shape.origin",
                    "plane": "_ring_shape.plane",
                    "radius": "_ring_shape.volume.outer.radius",
                    "cylinder_min": "_ring_shape.volume.outer.center[2]",
                    "cylinder_max": "_ring_shape.volume.outer.center[2] + 20",
                },
            ),
        ],
        [tool["effects"][requirement]],
    )
    return tool


def _body_tool():
    """Remove only path intervals where all four body corners remain inside the hole."""
    inner = []
    for x, y in ((-1, -1), (-1, 1), (1, -1), (1, 1)):
        points = {}
        for name in ("start", "end"):
            points[name] = [
                f"{name}[0] + _ring_moving.footprint.half_extents[0] * {x}",
                f"{name}[1] + _ring_moving.footprint.half_extents[1] * {y}",
                "_ring_body_source.origin[2] + 10",
            ]
        inner += [
            _set("_ring_corner_start", points["start"]),
            _set("_ring_corner_end", points["end"]),
            _call(
                "srd_information_segment_interval",
                {
                    "start": "{_ring_corner_start}",
                    "end": "{_ring_corner_end}",
                    "volume": "{_ring_body_source.volume.inner}",
                },
                SEGMENT_EXPORTS,
            ),
            _set("_ring_inner_all", "_ring_inner_all == true and _segment_hit == true"),
            _when(
                "_segment_hit == true",
                [
                    _set("_ring_inner_entry", "max(_ring_inner_entry, _segment_entry)"),
                    _set("_ring_inner_exit", "min(_ring_inner_exit, _segment_exit)"),
                ],
            ),
        ]
    return _environment_tool(
        "Resolve actual creature-space shell intervals without filling the ring's inner hole.",
        [_param("moving_subject"), _param("area_id"), _array("start"), _array("end")],
        [
            _call(
                "resolve_srd_environment_cylinder_body_segment",
                {
                    "moving_subject": "{moving_subject}",
                    "area_id": "{area_id}",
                    "start": "{start}",
                    "end": "{end}",
                },
                BODY_EXPORTS,
            ),
            _set("_ring_body_source", "state.persistent_spell_areas[area_id]"),
            _set("_ring_intervals", []),
            _when(
                "_body_hit == true",
                [
                    _set("_ring_outer_entry", "_body_entry"),
                    _set("_ring_outer_exit", "_body_exit"),
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{moving_subject}"},
                        POSITION_EXPORTS,
                    ),
                    _set("_ring_moving", "_info_scene_entity"),
                    _set(
                        "_body_bounds",
                        {
                            "min": [
                                f"_ring_body_source.origin[{i}] - "
                                f"_ring_body_source.volume.outer.radius - "
                                f"_ring_moving.footprint.half_extents[{i}]"
                                for i in range(2)
                            ]
                            + ["_ring_body_source.origin[2] - _ring_moving.height"],
                            "max": [
                                f"_ring_body_source.origin[{i}] + "
                                f"_ring_body_source.volume.outer.radius + "
                                f"_ring_moving.footprint.half_extents[{i}]"
                                for i in range(2)
                            ]
                            + ["_ring_body_source.origin[2] + 20"],
                        },
                    ),
                    _set("_ring_inner_all", True),
                    _set("_ring_inner_entry", 0),
                    _set("_ring_inner_exit", 1),
                    *inner,
                    *_difference(),
                ],
            ),
            _set("_body_hit", "len(_ring_intervals) > 0"),
            _set("_body_entry", 0),
            _set("_body_exit", 0),
            _when(
                "_body_hit == true",
                [
                    _set("_body_entry", "_ring_intervals[0].entry"),
                    _set("_body_exit", "_ring_intervals[-1].exit"),
                ],
            ),
        ],
    )


def _opacity_tool():
    """A ray crosses opaque Fire only through the positive strict interior of the shell."""
    midpoint = [
        f"start[{i}] + (end[{i}] - start[{i}]) * (_ring_part.entry + _ring_part.exit) * 0.5"
        for i in range(3)
    ]
    return _environment_tool(
        "Read actual live ring opacity, preserving inner-hole rays and Cylinder tangency.",
        [_param("area_id"), _array("start"), _array("end"), _param("plane")],
        [
            _set("_ring_fire_opaque", False),
            _set("_ring_ray_source", "state.persistent_spell_areas[area_id]"),
            *_source_shape_requirements("_ring_ray_source"),
            _call(
                "qualify_srd_wall_of_fire_source",
                {"caster_state": "{_ring_ray_source.caster}", "area_id": "{area_id}"},
                ("_sphere_source_current",),
            ),
            _when(
                "_sphere_source_current == true and plane == _ring_ray_source.plane",
                [
                    _call(
                        "srd_information_segment_interval",
                        {
                            "start": "{start}",
                            "end": "{end}",
                            "volume": "{_ring_ray_source.volume.outer}",
                        },
                        SEGMENT_EXPORTS,
                    ),
                    _when(
                        "_segment_hit == true",
                        [
                            _set("_ring_outer_entry", "_segment_entry"),
                            _set("_ring_outer_exit", "_segment_exit"),
                            _call(
                                "srd_information_segment_interval",
                                {
                                    "start": "{start}",
                                    "end": "{end}",
                                    "volume": "{_ring_ray_source.volume.inner}",
                                },
                                SEGMENT_EXPORTS,
                            ),
                            _set("_ring_inner_all", "_segment_hit"),
                            _set("_ring_inner_entry", 0),
                            _set("_ring_inner_exit", 0),
                            _when(
                                "_segment_hit == true",
                                [
                                    _set("_ring_inner_entry", "_segment_entry"),
                                    _set("_ring_inner_exit", "_segment_exit"),
                                ],
                            ),
                            *_difference(),
                            {
                                "for_each": {
                                    "collection": "_ring_intervals",
                                    "as": "_ring_part",
                                    "do": [
                                        _set("_ring_ray_midpoint", midpoint),
                                        _set(
                                            "_ring_ray_distance2",
                                            "(_ring_ray_midpoint[0] - "
                                            "_ring_ray_source.origin[0]) ** 2 + "
                                            "(_ring_ray_midpoint[1] - "
                                            "_ring_ray_source.origin[1]) ** 2",
                                        ),
                                        _when(
                                            "_ring_ray_distance2 < "
                                            "_ring_ray_source.volume.outer.radius ** 2 and "
                                            "_ring_ray_distance2 > "
                                            "_ring_ray_source.volume.inner.radius ** 2 and "
                                            "_ring_ray_midpoint[2] > "
                                            "_ring_ray_source.origin[2] and "
                                            "_ring_ray_midpoint[2] < "
                                            "_ring_ray_source.origin[2] + 20",
                                            [_set("_ring_fire_opaque", True)],
                                        ),
                                    ],
                                }
                            },
                        ],
                    ),
                ],
            ),
        ],
    )


def _hot_side_tool():
    """Bind the selected inner/outer face to actual Box distance and physical exposure."""
    return _environment_tool(
        "Qualify the printed ring hot side from actual body bounds and physical exposure.",
        [_entity("target_state"), _param("area_id")],
        [
            _set("_fire_hot_side_inside", False),
            _set("_ring_hot_source", "state.persistent_spell_areas[area_id]"),
            *_source_shape_requirements("_ring_hot_source"),
            _call(
                "qualify_srd_wall_of_fire_source",
                {"caster_state": "{_ring_hot_source.caster}", "area_id": "{area_id}"},
                ("_sphere_source_current",),
            ),
            _when(
                "_sphere_source_current == true and target_state.is_alive == true",
                [
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_target_state_id}"},
                        POSITION_EXPORTS,
                    ),
                    _require(
                        "_info_scene_entity.footprint.kind == 'box'",
                        "Ring face distance needs the actual admitted Box body.",
                    ),
                    _when(
                        "_info_scene_entity.metadata.plane == _ring_hot_source.plane",
                        [
                            _set("_ring_hot_body", "_info_scene_entity"),
                            _set(
                                "_ring_hot_xy",
                                [
                                    f"max(_info_position[{i}] - "
                                    f"_ring_hot_body.footprint.half_extents[{i}], "
                                    f"min(_ring_hot_source.origin[{i}], _info_position[{i}] + "
                                    f"_ring_hot_body.footprint.half_extents[{i}]))"
                                    for i in range(2)
                                ],
                            ),
                            _set("_ring_hot_face_radius", "_ring_hot_source.volume.outer.radius"),
                            _when(
                                "_ring_hot_source.wall_hot_side == 'negative'",
                                [
                                    _set(
                                        "_ring_hot_face_radius",
                                        "_ring_hot_source.volume.inner.radius",
                                    ),
                                    _set(
                                        "_ring_hot_xy",
                                        [
                                            f"_info_position[{i}] - "
                                            f"_ring_hot_body.footprint.half_extents[{i}]"
                                            for i in range(2)
                                        ],
                                    ),
                                    *[
                                        _when(
                                            f"_info_position[{i}] >= _ring_hot_source.origin[{i}]",
                                            [
                                                _set(
                                                    "_ring_hot_xy",
                                                    [
                                                        f"_info_position[{j}] + "
                                                        f"_ring_hot_body.footprint.half_extents[{j}]"
                                                        if j == i
                                                        else f"_ring_hot_xy[{j}]"
                                                        for j in range(2)
                                                    ],
                                                )
                                            ],
                                        )
                                        for i in range(2)
                                    ],
                                ],
                            ),
                            _set(
                                "_ring_hot_offset",
                                [
                                    f"_ring_hot_xy[{i}] - _ring_hot_source.origin[{i}]"
                                    for i in range(2)
                                ],
                            ),
                            _set(
                                "_ring_hot_distance",
                                "(_ring_hot_offset[0] ** 2 + _ring_hot_offset[1] ** 2) ** 0.5",
                            ),
                            _set(
                                "_ring_hot_selected", "_ring_hot_distance >= _ring_hot_face_radius"
                            ),
                            _when(
                                "_ring_hot_source.wall_hot_side == 'negative'",
                                [
                                    _set(
                                        "_ring_hot_selected",
                                        "_ring_hot_distance <= _ring_hot_face_radius",
                                    )
                                ],
                            ),
                            _when(
                                "_ring_hot_selected == true and _ring_hot_distance > 0",
                                [
                                    _set(
                                        "_ring_hot_face_z",
                                        "max(_ring_hot_source.origin[2], min(_info_position[2], "
                                        "_ring_hot_source.origin[2] + 20))",
                                    ),
                                    _set(
                                        "_ring_hot_face_point",
                                        [
                                            f"_ring_hot_source.origin[{i}] + "
                                            f"_ring_hot_offset[{i}] * "
                                            "_ring_hot_face_radius * _ring_hot_distance ** -1"
                                            for i in range(2)
                                        ]
                                        + ["_ring_hot_face_z"],
                                    ),
                                    _set(
                                        "_ring_hot_body_point",
                                        [
                                            "_ring_hot_xy[0]",
                                            "_ring_hot_xy[1]",
                                            "max(_info_position[2], min(_ring_hot_face_z, "
                                            "_info_position[2] + _ring_hot_body.height))",
                                        ],
                                    ),
                                    _call(
                                        "measure_srd_information_distance",
                                        {
                                            "start": "{_ring_hot_face_point}",
                                            "end": "{_ring_hot_body_point}",
                                        },
                                        ("_scene_distance",),
                                    ),
                                    _when(
                                        "_scene_distance <= 10",
                                        [
                                            *_snapshot(),
                                            _spatial(
                                                "exposure",
                                                {
                                                    "map_id": "{state.spatial_scene.map.id}",
                                                    "observer": {"point": "{_ring_hot_face_point}"},
                                                    "target": {"entity_id": "{_ring_hot_body.id}"},
                                                    "samples": {"horizontal": 3, "vertical": 3},
                                                    "include": ["geometry"],
                                                },
                                                "_ring_hot_exposure",
                                            ),
                                            _set(
                                                "_fire_hot_side_inside",
                                                "_ring_hot_exposure.visible_samples > 0",
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


def tools(existing):
    """Emit shell queries using existing body, point interval and exposure primitives."""
    return {
        "resolve_srd_environment_cylinder_body_segment": _cylinder_body(existing),
        "resolve_srd_wall_of_fire_ring_body_segment": _body_tool(),
        "resolve_srd_wall_of_fire_ring_opacity": _opacity_tool(),
        "qualify_srd_wall_of_fire_ring_hot_side": _hot_side_tool(),
        "qualify_srd_wall_of_fire_ring_point": _environment_tool(
            "Qualify an actual selected effect point in the saved Fire shell for source removal.",
            [_param("area_id"), _array("point"), _param("plane")],
            [
                _set("_ring_point_source", "state.persistent_spell_areas[area_id]"),
                *_source_shape_requirements("_ring_point_source"),
                _set(
                    "_ring_point_distance2",
                    "(point[0] - _ring_point_source.origin[0]) ** 2 + "
                    "(point[1] - _ring_point_source.origin[1]) ** 2",
                ),
                _set(
                    "_ring_fire_point_inside",
                    "plane == _ring_point_source.plane and "
                    "_ring_point_distance2 <= _ring_point_source.volume.outer.radius ** 2 and "
                    "_ring_point_distance2 >= _ring_point_source.volume.inner.radius ** 2 and "
                    "point[2] >= _ring_point_source.origin[2] and "
                    "point[2] <= _ring_point_source.origin[2] + 20",
                ),
            ],
        ),
    }


def bind_sources(changed):
    """Select actual shell placement before costs, then use it at existing Fire consumers."""
    cast = changed["cast_spell_environment"]
    params = [
        _optional("fire_wall_shape", "string", "straight", enum_values=["straight", "ring"]),
        _optional("fire_ring_diameter", "number", 20, minimum=2.000001, maximum=20),
    ]
    cast["parameters"] += params
    placement = next(
        e["on_true"]
        for e in cast["effects"]
        if e.get("condition") == "_environment_profile.environment_mode == 'wall_fire'"
        and "_fire_support_volume" in str(e)
    )
    before = next(
        i
        for i, e in enumerate(placement)
        if "_fire_cast_volume.min[" in e.get("require", {}).get("condition", "")
    )
    placement[before:before] = [
        _set("_fire_cast_bounds", "_fire_cast_volume"),
        _when(
            "fire_wall_shape == 'ring'",
            [
                _set("_fire_ring_radius", "fire_ring_diameter * 0.5"),
                _set(
                    "_fire_ring_outer",
                    {
                        "kind": "'cylinder'",
                        "center": "_environment_cast_origin",
                        "radius": "_fire_ring_radius",
                        "height": 20,
                    },
                ),
                _set(
                    "_fire_ring_inner",
                    {
                        "kind": "'cylinder'",
                        "center": "_environment_cast_origin",
                        "radius": "_fire_ring_radius - 1",
                        "height": 20,
                    },
                ),
                _set(
                    "_fire_cast_volume",
                    {"kind": "'annulus'", "outer": "_fire_ring_outer", "inner": "_fire_ring_inner"},
                ),
                _set(
                    "_fire_cast_bounds",
                    {
                        "min": [
                            f"_environment_cast_origin[{i}] - _fire_ring_radius" for i in range(2)
                        ]
                        + ["_environment_cast_origin[2]"],
                        "max": [
                            f"_environment_cast_origin[{i}] + _fire_ring_radius" for i in range(2)
                        ]
                        + ["_environment_cast_origin[2] + 20"],
                    },
                ),
            ],
        ),
    ]
    for effect in placement:
        if isinstance(effect.get("require", {}).get("condition"), str):
            effect["require"]["condition"] = effect["require"]["condition"].replace(
                "_fire_cast_volume.", "_fire_cast_bounds."
            )
    install = next(
        e["on_true"]
        for e in cast["effects"]
        if e.get("condition") == "_environment_profile.environment_mode == 'wall_fire'"
        and "wall_hot_side" in str(e)
    )
    install += [
        _set("state.persistent_spell_areas.{_environment_cast_id}.wall_shape", "fire_wall_shape"),
        _set(
            "state.persistent_spell_areas.{_environment_cast_id}.source_sha256", repr(SOURCE_SHA256)
        ),
    ]
    public = changed["cast_spell"]
    for parameter in params:
        parameter = deepcopy(parameter)
        parameter["name"] = "environment_" + parameter["name"]
        if not any(p["name"] == parameter["name"] for p in public["parameters"]):
            public["parameters"].append(parameter)
    for effects in _effect_lists(public["effects"]):
        for effect in effects:
            if effect.get("call") == "cast_spell_environment":
                for parameter in params:
                    effect["params"][parameter["name"]] = "{environment_" + parameter["name"] + "}"
    body = changed["resolve_srd_wall_of_fire_body_segment"]
    body_capture, *body_effects = body["effects"]
    assert "_environment_area_key" in body_capture.get("export_context", {})
    body["effects"] = [
        body_capture,
        _set("_ring_body_dispatch", "state.persistent_spell_areas[_environment_area_key]"),
        _when(
            "_ring_body_dispatch.volume.kind == 'annulus'",
            [
                _call(
                    "resolve_srd_wall_of_fire_ring_body_segment",
                    {
                        "moving_subject": "{moving_subject}",
                        "area_id": "{_environment_area_key}",
                        "start": "{start}",
                        "end": "{end}",
                    },
                    BODY_EXPORTS,
                ),
            ],
            body_effects,
        ),
    ]
    hot = changed["qualify_srd_wall_of_fire_hot_side"]
    hot_capture, *hot_effects = hot["effects"]
    assert "_environment_area_key" in hot_capture.get("export_context", {})
    hot["effects"] = [
        hot_capture,
        _set("_ring_hot_dispatch", "state.persistent_spell_areas[_environment_area_key]"),
        _when(
            "_ring_hot_dispatch.volume.kind == 'annulus'",
            [
                _call(
                    "qualify_srd_wall_of_fire_ring_hot_side",
                    {"target_state": "{_target_state_id}", "area_id": "{_environment_area_key}"},
                    ("_fire_hot_side_inside",),
                ),
            ],
            hot_effects,
        ),
    ]
    opacity = changed["resolve_srd_wall_of_fire_opacity"]
    for effects in _effect_lists(opacity["effects"]):
        for effect in effects:
            if effect.get("condition") == "_sphere_source_current == true":
                effect["on_true"] = [
                    _when(
                        "_environment_area.volume.kind == 'annulus'",
                        [
                            _call(
                                "resolve_srd_wall_of_fire_ring_opacity",
                                {
                                    "area_id": "{_environment_id}",
                                    "start": "{start}",
                                    "end": "{end}",
                                    "plane": "{plane}",
                                },
                                ("_ring_fire_opaque",),
                            ),
                            _when(
                                "_ring_fire_opaque == true",
                                [_set("_environment_fire_opaque", True)],
                            ),
                        ],
                        effect["on_true"],
                    )
                ]
    dispel = changed["dispel_srd_environment_source"]
    volume = next(
        e["on_true"]
        for e in dispel["effects"]
        if e.get("condition") == "_environment_dispel_source.volume != null"
    )
    volume[0] = _when(
        "_environment_dispel_source.volume.kind == 'annulus'",
        [
            _call(
                "qualify_srd_wall_of_fire_ring_point",
                {
                    "area_id": "{_environment_area_key}",
                    "point": "{_environment_dispel_point}",
                    "plane": "{_environment_dispel_plane}",
                },
                {"_segment_hit": "_ring_fire_point_inside"},
            )
        ],
        [volume[0]],
    )
