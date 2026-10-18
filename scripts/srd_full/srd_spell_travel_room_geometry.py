"""Demiplane physical faces and aperture checks use the admitted scene constructor."""

if __package__:
    from .srd_scene_relations import _tool_scene
    from .srd_spell_object_content import _new_scene_object
    from .srd_timed_actor_defenses import _call, _parameter, _require, _set, _when
    from .srd_timed_senses import _loop, _optional
else:
    from srd_scene_relations import _tool_scene
    from srd_spell_object_content import _new_scene_object
    from srd_timed_actor_defenses import _call, _parameter, _require, _set, _when
    from srd_timed_senses import _loop, _optional


def physical_intent():
    """Unprinted construction dimensions are explicit saved fiction intent in feet."""
    return [
        _optional("wall_thickness", "number", 0.25, minimum=0.001),
        _optional("door_width", "number", 5, minimum=0.001),
        _optional("door_height", "number", 8, minimum=0.001),
        _optional("door_depth", "number", 0.25, minimum=0.001),
    ]


def _body_clearance(body, face, width, height):
    """Compare the actual box body cross section with the saved aperture dimensions."""
    return [
        _require(
            f"{body}.footprint.kind == 'box'",
            "Exact Demiplane aperture clearance currently needs an actual box body.",
        ),
        *[
            _when(
                f"{face} == '{first}' or {face} == '{second}'",
                [
                    _require(
                        f"{body}.footprint.half_extents[{axis}] * 2 <= {width} and "
                        f"{body}.height <= {height}",
                        "The actual body does not fit the saved physical door aperture.",
                    ),
                ],
            )
            for first, second, axis in [("east", "west", 1), ("north", "south", 0)]
        ],
        _when(
            f"{face} == 'up' or {face} == 'down'",
            [
                _require(
                    f"{body}.footprint.half_extents[0] * 2 <= {width} and "
                    f"{body}.footprint.half_extents[1] * 2 <= {height}",
                    "The actual body does not fit the saved physical horizontal aperture.",
                ),
            ],
        ),
    ]


def door_body_intent(point, face):
    """Derive the actual door panel outside the chosen supporting face."""
    result = []
    for name, axis, sign in [("west", 0, -1), ("east", 0, 1), ("south", 1, -1), ("north", 1, 1)]:
        lateral = 1 - axis
        origin = [f"{point}[{i}]" for i in range(3)]
        origin[axis] += f" + door_depth * {sign * 0.5}"
        extent = ["door_depth" if i == axis else "door_width" for i in range(2)]
        extent.append("door_height")
        result.append(
            _when(
                f"{face} == '{name}'",
                [
                    _set("_dp_physical_origin", origin),
                    _set("_dp_physical_extent", extent),
                    _require(
                        f"{point}[{lateral}] - door_width * 0.5 >= "
                        f"_demiplane_surface.volume.min[{lateral}] and "
                        f"{point}[{lateral}] + door_width * 0.5 <= "
                        f"_demiplane_surface.volume.max[{lateral}] and "
                        f"{point}[2] >= _demiplane_surface.volume.min[2] and "
                        f"{point}[2] + door_height <= _demiplane_surface.volume.max[2]",
                        "The full physical aperture must fit its actual flat supporting face.",
                    ),
                ],
            )
        )
    for name, sign in [("up", 1), ("down", -1)]:
        result.append(
            _when(
                f"{face} == '{name}'",
                [
                    _set(
                        "_dp_physical_origin",
                        [
                            f"{point}[0]",
                            f"{point}[1]",
                            f"{point}[2]" + (" - door_depth" if sign < 0 else ""),
                        ],
                    ),
                    _set("_dp_physical_extent", ["door_width", "door_height", "door_depth"]),
                    *[
                        _require(
                            f"{point}[{axis}] - {dimension} * 0.5 >= "
                            f"_demiplane_surface.volume.min[{axis}] and "
                            f"{point}[{axis}] + {dimension} * 0.5 <= "
                            f"_demiplane_surface.volume.max[{axis}]",
                            "The full physical aperture must fit its actual flat supporting face.",
                        )
                        for axis, dimension in [(0, "door_width"), (1, "door_height")]
                    ],
                ],
            )
        )
    return result


def physical_bounds(origin, extent):
    """Validate chosen physical facts before the paid creation owner mutates the scene."""
    return [
        *[
            _require(
                f"{origin}[{axis}] - {extent}[{axis}] * 0.5 >= "
                f"state.spatial_scene.map.bounds.min[{axis}] and "
                f"{origin}[{axis}] + {extent}[{axis}] * 0.5 <= "
                f"state.spatial_scene.map.bounds.max[{axis}]",
                "The full physical door must fit the actual admitted map bounds.",
            )
            for axis in range(2)
        ],
        _require(
            f"{origin}[2] >= state.spatial_scene.map.bounds.min[2] and "
            f"{origin}[2] + {extent}[2] <= state.spatial_scene.map.bounds.max[2]",
            "The full physical door must fit the actual admitted map bounds.",
        ),
    ]


def panel_space_effects():
    """Require the actual exterior panel volume to be free of same-plane solids and bodies."""
    effects = []
    for collection, variable in [("entities", "_dp_panel_body"), ("geometry", "_dp_panel_solid")]:
        body = collection == "entities"
        lower = (
            [
                f"{variable}.position[{axis}] - {variable}.footprint.half_extents[{axis}]"
                for axis in range(2)
            ]
            + [f"{variable}.position[2]"]
            if body
            else [f"{variable}.volume.min[{axis}]" for axis in range(3)]
        )
        upper = (
            [
                f"{variable}.position[{axis}] + {variable}.footprint.half_extents[{axis}]"
                for axis in range(2)
            ]
            + [f"{variable}.position[2] + {variable}.height"]
            if body
            else [f"{variable}.volume.max[{axis}]" for axis in range(3)]
        )
        effects.extend(
            [
                _set("_dp_panel_rows", f"state.spatial_scene.{collection}"),
                _loop(
                    "_dp_panel_rows",
                    variable,
                    [
                        _when(
                            f"{variable}.active == true and {variable}.movement == 'blocking' and "
                            f"({variable}.metadata.plane == _dp_panel_plane or "
                            f"({variable}.metadata.plane == null and "
                            "_dp_panel_plane == 'material'))"
                            + ("" if body else f" and {variable}.id != surface_id"),
                            [
                                _require(
                                    f"{variable}.footprint.kind == 'box'"
                                    if body
                                    else f"{variable}.volume.kind == 'box'",
                                    "Exact door construction occupancy needs admitted box "
                                    "geometry and bodies.",
                                ),
                                _require(
                                    "not ("
                                    + " and ".join(
                                        f"_dp_panel_min[{axis}] < {upper[axis]} and "
                                        f"_dp_panel_max[{axis}] > {lower[axis]}"
                                        for axis in range(3)
                                    )
                                    + ")",
                                    "An actual same-plane solid or body occupies "
                                    "the physical door volume.",
                                ),
                            ],
                        ),
                    ],
                ),
            ]
        )
    return [
        _set(
            "_dp_panel_min",
            [
                "_dp_physical_origin[0] - _dp_physical_extent[0] * 0.5",
                "_dp_physical_origin[1] - _dp_physical_extent[1] * 0.5",
                "_dp_physical_origin[2]",
            ],
        ),
        _set(
            "_dp_panel_max",
            [
                "_dp_physical_origin[0] + _dp_physical_extent[0] * 0.5",
                "_dp_physical_origin[1] + _dp_physical_extent[1] * 0.5",
                "_dp_physical_origin[2] + _dp_physical_extent[2]",
            ],
        ),
        *effects,
    ]


def room_geometry_tools():
    """Emit source-bound wall/floor faces through the one shared scenery constructor."""
    planes = [
        ("west", 0, "min"),
        ("east", 0, "max"),
        ("south", 1, "min"),
        ("north", 1, "max"),
        ("floor", 2, "min"),
        ("ceiling", 2, "max"),
    ]
    effects = []
    for name, axis, edge in planes:
        lower, upper = [], []
        for other in range(3):
            if other == axis:
                lower.append(
                    f"_demiplane_room.shape.{edge}[{other}]"
                    + (" - _room_thickness" if edge == "min" else "")
                )
                upper.append(
                    f"_demiplane_room.shape.{edge}[{other}]"
                    + (" + _room_thickness" if edge == "max" else "")
                )
            else:
                lower.append(f"_demiplane_room.shape.min[{other}] - _room_thickness")
                upper.append(f"_demiplane_room.shape.max[{other}] + _room_thickness")
        effects.append(
            _when(
                f"face == '{name}'",
                [
                    _set("_room_face_min", lower),
                    _set("_room_face_max", upper),
                ],
            )
        )
    create = _tool_scene(
        "Create one actual source room face from its registered room and saved thickness.",
        [
            _parameter("room_id"),
            _parameter("face_id"),
            _parameter("face", "enum", enum_values=[p[0] for p in planes]),
        ],
        [
            _call("resolve_srd_demiplane_room", {"room_id": "{room_id}"}, ["_demiplane_room"]),
            _set("_room_thickness", "_demiplane_room.metadata.wall_thickness"),
            _require(
                "_room_thickness != null and _room_thickness > 0",
                "Physical room faces require their actual saved creation thickness.",
            ),
            *effects,
            _set(
                "_world_origin",
                [
                    "(_room_face_min[0] + _room_face_max[0]) * 0.5",
                    "(_room_face_min[1] + _room_face_max[1]) * 0.5",
                    "_room_face_min[2]",
                ],
            ),
            _set(
                "_room_face_extent",
                [f"_room_face_max[{axis}] - _room_face_min[{axis}]" for axis in range(3)],
            ),
            *_new_scene_object(
                {
                    "plane": "room_id",
                    "kind": "'srd_demiplane_face'",
                    "room_id": "room_id",
                    "material": "_demiplane_room.metadata.material",
                    "ground": "face == 'floor'",
                    "source_ref": "'demiplane'",
                    "source_caster": "_demiplane_room.metadata.creator",
                },
                identity="face_id",
                extent="_room_face_extent",
                movement="'blocking'",
                visibility="'opaque'",
                scene_kind="geometry",
            ),
        ],
    )
    identity = _tool_scene(
        "Qualify a source-derived creation identity against all actual admitted scene rows.",
        [_parameter("identity"), _optional("new_plane", default="")],
        [
            *[
                effect
                for collection in ("entities", "geometry", "zones")
                for effect in [
                    _set("_dp_identity_rows", f"state.spatial_scene.{collection}"),
                    _loop(
                        "_dp_identity_rows",
                        "_dp_identity_row",
                        [
                            _require(
                                "_dp_identity_row.id != identity",
                                "A new Demiplane creation identity is already "
                                "admitted in the scene.",
                            ),
                            _require(
                                "new_plane == '' or _dp_identity_row.metadata.plane != new_plane",
                                "The source's new empty room plane already has admitted contents.",
                            ),
                        ],
                    ),
                ]
            ],
        ],
    )
    return {
        "create_srd_demiplane_room_face": create,
        "qualify_srd_demiplane_creation_identity": identity,
    }
