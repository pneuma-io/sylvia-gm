"""Demiplane portals bind real solid surfaces, scene room bounds and lasting sources."""

from copy import deepcopy

if __package__:
    from .srd_scene_relations import _array, _snapshot, _tool_scene
    from .srd_spell_object_content import _new_scene_object
    from .srd_spell_travel_room_geometry import (
        _body_clearance,
        door_body_intent,
        panel_space_effects,
        physical_bounds,
        physical_intent,
        room_geometry_tools,
    )
    from .srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _when
    from .srd_timed_senses import _loop, _optional, _public, _push
else:
    from srd_scene_relations import _array, _snapshot, _tool_scene
    from srd_spell_object_content import _new_scene_object
    from srd_spell_travel_room_geometry import (
        _body_clearance,
        door_body_intent,
        panel_space_effects,
        physical_bounds,
        physical_intent,
        room_geometry_tools,
    )
    from srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _when
    from srd_timed_senses import _loop, _optional, _public, _push


FACES = ["east", "west", "north", "south", "up", "down"]
FACE_AXES = [
    ("east", 0, "max"),
    ("west", 0, "min"),
    ("north", 1, "max"),
    ("south", 1, "min"),
    ("up", 2, "max"),
    ("down", 2, "min"),
]


def demiplane_tools(tools):
    """Qualify the exact admitted face without caller visibility or solidity verdicts."""
    if __package__:
        from .srd_spell_travel import _measure, _scene_subject
    else:
        from srd_spell_travel import _measure, _scene_subject

    face_effects = []
    for face, axis, edge in FACE_AXES:
        others = [other for other in range(3) if other != axis]
        face_effects.append(
            _when(
                f"face == '{face}'",
                [
                    _require(
                        f"point[{axis}] == _demiplane_surface.volume.{edge}[{axis}] and "
                        + " and ".join(
                            f"point[{other}] >= _demiplane_surface.volume.min[{other}] and "
                            f"point[{other}] <= _demiplane_surface.volume.max[{other}]"
                            for other in others
                        ),
                        "The chosen door must lie on that exact flat surface face.",
                    ),
                ],
            )
        )
    sight = []
    if "resolve_srd_illumination_point_sight" in tools:
        sight = [
            _call(
                "resolve_srd_illumination_point_sight",
                {
                    "observer_state": "{_caster_state_id}",
                    "point": "{point}",
                },
                ["_illumination_point_can_see"],
            ),
            _require(
                "_illumination_point_can_see == true",
                "Demiplane needs a flat solid surface the actual caster can see.",
            ),
        ]
    else:
        sight = [_require("false", "Demiplane needs the admitted point-sight owner.")]
    surface = _tool_scene(
        "Qualify an actual same-plane visible solid box face within Demiplane's 60-foot range.",
        [
            _entity("caster_state"),
            _parameter("surface_id"),
            _array("point"),
            _parameter("face", "enum", enum_values=FACES),
        ],
        [
            *_snapshot(),
            *_scene_subject("{_caster_state_id}", "_demiplane_caster"),
            _require(
                "_demiplane_caster.metadata.plane != null",
                "The caster needs its actual admitted plane.",
            ),
            _set("_demiplane_surface", None),
            _set("_demiplane_geometries", "state.spatial_scene.geometry"),
            _loop(
                "_demiplane_geometries",
                "_demiplane_geometry",
                [
                    _when(
                        "_demiplane_geometry.id == surface_id",
                        [
                            _set("_demiplane_surface", "_demiplane_geometry"),
                        ],
                    ),
                ],
            ),
            _require("_demiplane_surface != null", "Select an actual admitted solid surface."),
            _require(
                "_demiplane_surface.active == true and "
                "_demiplane_surface.movement == 'blocking' and "
                "_demiplane_surface.volume.kind == 'box'",
                "Demiplane's flat-surface mode needs active admitted solid box geometry.",
            ),
            _set("_demiplane_surface_plane", "'material'"),
            _when(
                "_demiplane_surface.metadata.plane != null",
                [
                    _set("_demiplane_surface_plane", "_demiplane_surface.metadata.plane"),
                ],
            ),
            _require(
                "_demiplane_surface_plane == _demiplane_caster.metadata.plane",
                "The actual door surface must be on the caster's current plane.",
            ),
            *face_effects,
            *_measure("{_demiplane_caster.position}", "{point}", "_demiplane_surface_distance"),
            _require(
                "_demiplane_surface_distance <= 60",
                "The actual Demiplane surface is beyond 60 feet.",
            ),
            *sight,
        ],
    )
    room_resolve = _tool_scene(
        "Resolve only an actual persistent source-created thirty-foot room zone.",
        [_parameter("room_id")],
        [
            _require(
                "state.demiplane_room_ids != null and room_id in state.demiplane_room_ids",
                "Choose an actual room created by the Demiplane source owner.",
            ),
            _set("_demiplane_room", None),
            _set("_demiplane_room_zones", "state.spatial_scene.zones"),
            _loop(
                "_demiplane_room_zones",
                "_demiplane_room_zone",
                [
                    _when(
                        "_demiplane_room_zone.id == room_id",
                        [
                            _set("_demiplane_room", "_demiplane_room_zone"),
                        ],
                    ),
                ],
            ),
            _require(
                "_demiplane_room != null and "
                "_demiplane_room.metadata.kind == 'srd_demiplane_room' and "
                "_demiplane_room.metadata.creator != null and "
                "_demiplane_room.metadata.plane == room_id and "
                "_demiplane_room.metadata.permanent == true and "
                "_demiplane_room.shape.kind == 'box' and "
                + " and ".join(
                    f"_demiplane_room.shape.max[{axis}] - _demiplane_room.shape.min[{axis}] == 30"
                    for axis in range(3)
                ),
                "The registered source room needs its actual persistent plane and box bounds.",
            ),
        ],
    )
    room_lookup = _tool_scene(
        "Reuse an actual room created by the caster or known by its nature and contents.",
        [_entity("caster_state"), _parameter("room_id")],
        [
            _call("resolve_srd_demiplane_room", {"room_id": "{room_id}"}, ["_demiplane_room"]),
            _require(
                "_demiplane_room.metadata.creator == _caster_state_id or "
                "(caster_state.demiplane_known_rooms != null and "
                "room_id in caster_state.demiplane_known_rooms)",
                "The caster must know the nature and contents of another creator's room.",
            ),
        ],
    )
    room_knowledge = _public(
        _tool_scene(
            "Record knowledge of the nature and contents of an actual persistent room.",
            [
                _entity("actor_state"),
                _parameter("room_id"),
                _parameter("nature_and_contents_known", "boolean"),
            ],
            [
                _call("resolve_srd_demiplane_room", {"room_id": "{room_id}"}, ["_demiplane_room"]),
                _set("_demiplane_known", []),
                _when(
                    "actor_state.demiplane_known_rooms != null",
                    [
                        _set("_demiplane_old_known", "actor_state.demiplane_known_rooms"),
                        _loop(
                            "_demiplane_old_known",
                            "_demiplane_known_id",
                            [
                                _when(
                                    "_demiplane_known_id != room_id",
                                    [
                                        _push("_demiplane_known", "_demiplane_known_id"),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
                _when(
                    "nature_and_contents_known == true",
                    [
                        _push("_demiplane_known", "room_id"),
                    ],
                ),
                _set("actor_state.demiplane_known_rooms", "_demiplane_known"),
                _set("_demiplane_knowledge_room", "room_id"),
                _set("_demiplane_knowledge_answer", "nature_and_contents_known"),
            ],
        ),
        "information",
        "Demiplane room knowledge",
        [
            ("Actual room", "_demiplane_knowledge_room"),
            ("Nature and contents known", "_demiplane_knowledge_answer"),
        ],
    )
    room_create = _tool_scene(
        "Create the printed persistent thirty-foot room in the sole admitted scene.",
        [
            _entity("caster_state"),
            _parameter("room_id"),
            _parameter("material", "enum", enum_values=["wood", "stone"]),
            _optional("wall_thickness", "number", 0.25, minimum=0.001),
        ],
        [
            *_snapshot(),
            _require(
                "state.demiplane_room_ids == null or room_id not in state.demiplane_room_ids",
                "A persistent Demiplane room identity cannot be created twice.",
            ),
            _require(
                " and ".join(
                    f"state.spatial_scene.map.bounds.max[{axis}] - "
                    f"state.spatial_scene.map.bounds.min[{axis}] >= 30 + wall_thickness * 2"
                    for axis in range(3)
                ),
                "The admitted map must fit this actual thirty-foot room and its portal anchors.",
            ),
            _set(
                "_demiplane_room_center",
                [
                    f"(state.spatial_scene.map.bounds.min[{axis}] + "
                    f"state.spatial_scene.map.bounds.max[{axis}]) * 0.5"
                    for axis in range(3)
                ],
            ),
            _set(
                "_demiplane_room",
                {
                    "id": "room_id",
                    "map_id": "state.spatial_scene.map.id",
                    "shape": {
                        "kind": "'box'",
                        "min": [f"_demiplane_room_center[{axis}] - 15" for axis in range(3)],
                        "max": [f"_demiplane_room_center[{axis}] + 15" for axis in range(3)],
                    },
                    "metadata": {
                        "kind": "'srd_demiplane_room'",
                        "creator": "_caster_state_id",
                        "plane": "room_id",
                        "material": "material",
                        "permanent": True,
                        "wall_thickness": "wall_thickness",
                    },
                },
            ),
            _set("_demiplane_creation_zones", "state.spatial_scene.zones"),
            _push("_demiplane_creation_zones", "_demiplane_room"),
            _set("state.spatial_scene.zones", "_demiplane_creation_zones"),
            _set("state.spatial_scene.map.revision", "state.spatial_scene.map.revision + 1"),
            *_snapshot(),
            _when("state.demiplane_room_ids == null", [_set("state.demiplane_room_ids", [])]),
            _set("_demiplane_saved_room_ids", "state.demiplane_room_ids"),
            _push("_demiplane_saved_room_ids", "room_id"),
            _set("state.demiplane_room_ids", "_demiplane_saved_room_ids"),
            *[
                _call(
                    "create_srd_demiplane_room_face",
                    {
                        "room_id": "{room_id}",
                        "face_id": "{room_id}:" + face,
                        "face": face,
                    },
                )
                for face in ("west", "east", "south", "north", "floor", "ceiling")
            ],
        ],
    )
    bounds = _tool_scene(
        "Keep actual box bodies within the printed persistent room's admitted bounds.",
        [_parameter("subject"), _array("destination"), _parameter("plane")],
        [
            _when(
                "state.demiplane_room_ids != null and plane in state.demiplane_room_ids",
                [
                    *_scene_subject("{subject}", "_demiplane_bound_body"),
                    _require(
                        "_demiplane_bound_body.footprint.kind == 'box'",
                        "Exact Demiplane body containment currently needs a box footprint.",
                    ),
                    _set("_demiplane_bound_room", None),
                    _set("_demiplane_bound_zones", "state.spatial_scene.zones"),
                    _loop(
                        "_demiplane_bound_zones",
                        "_demiplane_bound_zone",
                        [
                            _when(
                                "_demiplane_bound_zone.id == plane",
                                [
                                    _set("_demiplane_bound_room", "_demiplane_bound_zone"),
                                ],
                            ),
                        ],
                    ),
                    _require(
                        "_demiplane_bound_room != null and "
                        "_demiplane_bound_room.metadata.kind == 'srd_demiplane_room' and "
                        "_demiplane_bound_room.metadata.plane == plane and "
                        "_demiplane_bound_room.shape.kind == 'box'",
                        "The persistent Demiplane room needs its actual admitted bounds.",
                    ),
                    *[
                        _require(
                            f"destination[{axis}] - _demiplane_bound_body.footprint.half_extents"
                            f"[{axis}] >= _demiplane_bound_room.shape.min[{axis}] and "
                            f"destination[{axis}] + _demiplane_bound_body.footprint.half_extents"
                            f"[{axis}] <= _demiplane_bound_room.shape.max[{axis}]",
                            "The actual body must remain inside the thirty-foot Demiplane room.",
                        )
                        for axis in range(2)
                    ],
                    _require(
                        "destination[2] >= _demiplane_bound_room.shape.min[2] and "
                        "destination[2] + _demiplane_bound_body.height <= "
                        "_demiplane_bound_room.shape.max[2]",
                        "The actual body must remain inside the thirty-foot Demiplane room.",
                    ),
                ],
            ),
        ],
    )
    return {
        "qualify_srd_demiplane_surface": surface,
        "resolve_srd_demiplane_room": room_resolve,
        "read_srd_demiplane_room": room_lookup,
        "record_srd_demiplane_room_knowledge": room_knowledge,
        "create_srd_demiplane_room": room_create,
        "check_srd_demiplane_body_bounds": bounds,
        **room_geometry_tools(),
        **demiplane_portal_tools(tools),
    }


def _door_lookup(door_id):
    """Resolve either face to its actual retained source door record."""
    return [
        _set("_dp_door", None),
        _set("_dp_doors", "state.demiplane_doors"),
        _loop(
            "_dp_doors",
            "_dp_candidate",
            [
                _when(
                    f"_dp_candidate.id == {door_id} or _dp_candidate.inner_id == {door_id}",
                    [
                        _set("_dp_door", "_dp_candidate"),
                    ],
                ),
            ],
        ),
        _require("_dp_door != null", "Choose an actual Demiplane source door."),
    ]


def demiplane_portal_tools(tools):
    """Persistent rooms and timed door anchors reuse existing access, movement and placement."""
    if __package__:
        from .srd_spell_travel import (
            ACTIVE,
            _measure,
            _move,
            _movement_cost,
            _placement,
            _scene_subject,
        )
    else:
        from srd_spell_travel import (
            ACTIVE,
            _measure,
            _move,
            _movement_cost,
            _placement,
            _scene_subject,
        )

    identity = [
        _optional("spell_cast_id", default="", model_hidden=True),
        _optional("spell_cast_owner", default=None, resolve="entity", model_hidden=True),
        _optional("actual_spell_level", "integer", -1, minimum=-1, maximum=9, model_hidden=True),
    ]
    choices = [
        _optional("surface_id", default=""),
        _array("point"),
        _parameter("face", "enum", enum_values=FACES),
        _optional("room_id", default=""),
        _parameter("material", "enum", enum_values=["wood", "stone"]),
        *physical_intent(),
    ]
    preflight = _tool_scene(
        "Qualify source door surface and actual prior-room ownership before payment.",
        [
            _entity("caster_state"),
            _optional("targets", "array", None, items={"type": "string"}),
            *deepcopy(choices),
        ],
        [
            _require("surface_id != ''", "Select the actual flat solid surface for this door."),
            _require(
                "targets == null or len(targets) == 0", "Demiplane selects a surface, not targets."
            ),
            _call(
                "qualify_srd_demiplane_surface",
                {
                    "caster_state": "{_caster_state_id}",
                    "surface_id": "{surface_id}",
                    "point": "{point}",
                    "face": "{face}",
                },
                ["_demiplane_surface", "_demiplane_caster"],
            ),
            _require(
                "door_width <= 30 and door_height <= 30 and door_depth <= 30",
                "The actual inner door must fit the printed thirty-foot room.",
            ),
            _when(
                "room_id != ''",
                [
                    _call(
                        "read_srd_demiplane_room",
                        {
                            "caster_state": "{_caster_state_id}",
                            "room_id": "{room_id}",
                        },
                        ["_demiplane_room"],
                    ),
                ],
            ),
            *door_body_intent("point", "face"),
            *physical_bounds("_dp_physical_origin", "_dp_physical_extent"),
            _set("_dp_panel_plane", "_demiplane_caster.metadata.plane"),
            *panel_space_effects(),
            _when(
                "caster_state.size_rank != null and caster_state.size_rank <= 2",
                [
                    *_body_clearance("_demiplane_caster", "face", "door_width", "door_height"),
                    *_body_clearance("_demiplane_caster", "'east'", "door_width", "door_height"),
                ],
            ),
            _when(
                "room_id != ''",
                [
                    _set("_dp_panel_plane", "_demiplane_room.metadata.plane"),
                    _set(
                        "_dp_physical_origin",
                        [
                            "_demiplane_room.shape.min[0] + door_depth * 0.5",
                            "(_demiplane_room.shape.min[1] + _demiplane_room.shape.max[1]) * 0.5",
                            "_demiplane_room.shape.min[2]",
                        ],
                    ),
                    _set("_dp_physical_extent", ["door_depth", "door_width", "door_height"]),
                    *physical_bounds("_dp_physical_origin", "_dp_physical_extent"),
                    *panel_space_effects(),
                ],
            ),
            _set("_dp_preflight_serial", 1),
            _when(
                "state.demiplane_serial != null",
                [
                    _set("_dp_preflight_serial", "state.demiplane_serial + 1"),
                ],
            ),
            *[
                _call(
                    "qualify_srd_demiplane_creation_identity",
                    {
                        "identity": prefix + ":{_dp_preflight_serial}",
                    },
                )
                for prefix in ("dp_door", "dp_inside")
            ],
            {
                "condition": "room_id != ''",
                "on_true": [],
                "on_false": [
                    _call(
                        "qualify_srd_demiplane_creation_identity",
                        {
                            "identity": "dp_room:{_dp_preflight_serial}",
                            "new_plane": "dp_room:{_dp_preflight_serial}",
                        },
                    ),
                    *[
                        _call(
                            "qualify_srd_demiplane_creation_identity",
                            {
                                "identity": "dp_room:{_dp_preflight_serial}:" + side,
                            },
                        )
                        for side in ("west", "east", "south", "north", "floor", "ceiling")
                    ],
                    _require(
                        " and ".join(
                            f"state.spatial_scene.map.bounds.max[{i}] - "
                            f"state.spatial_scene.map.bounds.min[{i}] >= 30 + wall_thickness * 2"
                            for i in range(3)
                        ),
                        "The admitted map must fit the thirty-foot room and actual door anchors.",
                    ),
                ],
            },
        ],
    )
    install = _tool_scene(
        "Create or reconnect a persistent room through one authentic timed door pair.",
        [
            _entity("caster_state"),
            _parameter("slot_level", "integer", minimum=8, maximum=9),
            *deepcopy(choices),
            *identity,
        ],
        [
            _set("_dp_source_owner", None),
            _when("spell_cast_owner != null", [_set("_dp_source_owner", "_spell_cast_owner_id")]),
            _when("state.lasting_tick == null", [_set("state.lasting_tick", 0)]),
            _when("state.demiplane_serial == null", [_set("state.demiplane_serial", 0)]),
            _set("state.demiplane_serial", "state.demiplane_serial + 1"),
            {
                "condition": "room_id == ''",
                "on_true": [
                    _call(
                        "create_srd_demiplane_room",
                        {
                            "caster_state": "{_caster_state_id}",
                            "room_id": "dp_room:{state.demiplane_serial}",
                            "material": "{material}",
                            "wall_thickness": "{wall_thickness}",
                        },
                        ["_demiplane_room"],
                    )
                ],
                "on_false": [
                    _call(
                        "read_srd_demiplane_room",
                        {
                            "caster_state": "{_caster_state_id}",
                            "room_id": "{room_id}",
                        },
                        ["_demiplane_room"],
                    )
                ],
            },
            _call(
                "create_srd_demiplane_door_pair",
                {
                    "caster_state": "{_caster_state_id}",
                    "point": "{point}",
                    "surface_id": "{surface_id}",
                    "face": "{face}",
                    "room_id": "{_demiplane_room.id}",
                    "door_id": "dp_door:{state.demiplane_serial}",
                    "inner_id": "dp_inside:{state.demiplane_serial}",
                    "slot_level": "{slot_level}",
                    "door_width": "{door_width}",
                    "door_height": "{door_height}",
                    "door_depth": "{door_depth}",
                    "spell_cast_id": "{spell_cast_id}",
                    "spell_cast_owner": "{_dp_source_owner}",
                    "actual_spell_level": "{actual_spell_level}",
                },
                ["_dp_door"],
            ),
        ],
    )
    pair = _tool_scene(
        "Create the paired physical panels from the saved source construction intent.",
        [
            _entity("caster_state"),
            _array("point"),
            _parameter("surface_id"),
            _parameter("face"),
            _parameter("room_id"),
            _parameter("door_id"),
            _parameter("inner_id"),
            _parameter("slot_level", "integer", minimum=8, maximum=9),
            *physical_intent(),
            *identity,
        ],
        [
            *_snapshot(),
            *_scene_subject("{_caster_state_id}", "_dp_caster"),
            _call(
                "qualify_srd_demiplane_surface",
                {
                    "caster_state": "{_caster_state_id}",
                    "surface_id": "{surface_id}",
                    "point": "{point}",
                    "face": "{face}",
                },
                ["_demiplane_surface"],
            ),
            *door_body_intent("point", "face"),
            *physical_bounds("_dp_physical_origin", "_dp_physical_extent"),
            _call(
                "read_srd_demiplane_room",
                {
                    "caster_state": "{_caster_state_id}",
                    "room_id": "{room_id}",
                },
                ["_demiplane_room"],
            ),
            _set(
                "_dp_inner_point",
                [
                    "_demiplane_room.shape.min[0] + door_depth * 0.5",
                    "(_demiplane_room.shape.min[1] + _demiplane_room.shape.max[1]) * 0.5",
                    "_demiplane_room.shape.min[2]",
                ],
            ),
            *[
                effect
                for inner in (False, True)
                for effect in [
                    _set("_world_origin", "_dp_inner_point" if inner else "_dp_physical_origin"),
                    _set(
                        "_dp_panel_extent",
                        ["door_depth", "door_width", "door_height"]
                        if inner
                        else "_dp_physical_extent",
                    ),
                    *_new_scene_object(
                        {
                            "plane": "room_id" if inner else "_dp_caster.metadata.plane",
                            "kind": "'door'",
                            "source_ref": "'demiplane'",
                            "magical_effect": True,
                            "spell_school": "'conjuration'",
                            "source_caster": "_caster_state_id",
                            "demiplane_door": "door_id",
                            "demiplane_peer": "door_id" if inner else "inner_id",
                            "source_medium_passage": True,
                            "aperture_face": "'east'" if inner else "face",
                            "aperture_width": "door_width",
                            "aperture_height": "door_height",
                            "aperture_depth": "door_depth",
                            "srd_object_access": {
                                "kind": "'door'",
                                "closed": True,
                                "locks": [],
                                "stuck": False,
                                "barred": False,
                            },
                        },
                        identity="inner_id" if inner else "door_id",
                        extent="_dp_panel_extent",
                        movement="'blocking'",
                        visibility="'opaque'",
                    ),
                ]
            ],
            _set(
                "_dp_door",
                {
                    "id": "door_id",
                    "inner_id": "inner_id",
                    "room_id": "room_id",
                    "caster": "_caster_state_id",
                    "surface_id": "surface_id",
                    "face": "face",
                    "active": True,
                    "created_tick": "state.lasting_tick",
                    "expires_tick": "state.lasting_tick + 1200",
                    "spell": "'demiplane'",
                    "cast_id": "spell_cast_id",
                    "source_caster_id": None,
                    "ongoing_duration": "spell_cast_id != ''",
                    "slot_level": "slot_level",
                    "door_width": "door_width",
                    "door_height": "door_height",
                    "door_depth": "door_depth",
                },
            ),
            _when(
                "spell_cast_id != ''",
                [
                    _require(
                        "spell_cast_owner != null and _spell_cast_owner_id == _caster_state_id and "
                        "caster_state.last_spell_source_cast_id == spell_cast_id and "
                        "caster_state.last_spell_source_spell == 'demiplane'",
                        "Demiplane must retain its authentic committed source identity.",
                    ),
                    _set("_dp_identity_patch", {"source_caster_id": "_spell_cast_owner_id"}),
                    _set("_dp_door", "merge(_dp_door, _dp_identity_patch)"),
                ],
            ),
            _when("state.demiplane_doors == null", [_set("state.demiplane_doors", [])]),
            _set("_dp_saved_doors", "state.demiplane_doors"),
            _push("_dp_saved_doors", "_dp_door"),
            _set("state.demiplane_doors", "_dp_saved_doors"),
            _set("caster_state.last_demiplane_door", "door_id"),
            _set("caster_state.last_demiplane_room", "room_id"),
        ],
    )
    sync = _tool_scene(
        "Synchronize the real opposite door face after the actual object-access owner changes it.",
        [_parameter("door_subject")],
        [
            *_scene_subject("{door_subject}", "_dp_operated"),
            _when(
                "_dp_operated.metadata.demiplane_door != null",
                [
                    *_door_lookup("_dp_operated.metadata.demiplane_door"),
                    _require(
                        "_dp_door.active == true and state.lasting_tick < _dp_door.expires_tick",
                        "This source door has vanished.",
                    ),
                    *_scene_subject("{_dp_operated.metadata.demiplane_peer}", "_dp_peer"),
                    _require(
                        "(_dp_operated.metadata.srd_arcane_locks == null or "
                        "len(_dp_operated.metadata.srd_arcane_locks) == 0) and "
                        "(_dp_peer.metadata.srd_arcane_locks == null or "
                        "len(_dp_peer.metadata.srd_arcane_locks) == 0)",
                        "Counterpart magical-lock projection for a planar door remains selected.",
                    ),
                    _when(
                        "_dp_operated.metadata.srd_object_access.closed == false",
                        [
                            _require(
                                "len(_dp_peer.metadata.srd_object_access.locks) == 0 and "
                                "_dp_peer.metadata.srd_object_access.stuck != true and "
                                "_dp_peer.metadata.srd_object_access.barred != true",
                                "A real counterpart restriction prevents opening that planar door.",
                            ),
                        ],
                    ),
                    _set(
                        "_dp_peer_access",
                        {"closed": "_dp_operated.metadata.srd_object_access.closed"},
                    ),
                    _set(
                        "_dp_peer_access",
                        "merge(_dp_peer.metadata.srd_object_access, _dp_peer_access)",
                    ),
                    _set("_dp_peer_patch", {"srd_object_access": "_dp_peer_access"}),
                    _set("_dp_peer_metadata", "merge(_dp_peer.metadata, _dp_peer_patch)"),
                    {
                        "spatial_mutation": {
                            "scene": "state.spatial_scene",
                            "map_id": "{state.spatial_scene.map.id}",
                            "expected_revision": "{state.spatial_scene.map.revision}",
                            "target": {"kind": "entity", "id": "{_dp_peer.id}"},
                            "patch": {
                                "metadata": "{_dp_peer_metadata}",
                                "movement": "{_dp_operated.movement}",
                                "visibility": "{_dp_operated.visibility}",
                            },
                        },
                        "store_as": "_dp_peer_change",
                    },
                ],
            ),
        ],
    )
    traverse = _public(
        _tool_scene(
            "Walk through the open Medium door using the shared movement budget and scene owner.",
            [_entity("actor_state"), _parameter("door_subject"), _array("destination")],
            [
                _require(ACTIVE, "The actual creature must be able to move through the door."),
                _when(
                    "combat.encounter_active == true",
                    [
                        _require(
                            "combat.turn_active == true and combat.active_actor == _actor_state_id",
                            "Walk through the door only on the creature's actual turn.",
                        ),
                    ],
                ),
                _call("refresh_srd_demiplane_doors", {}),
                *_scene_subject("{_actor_state_id}", "_dp_actor"),
                *_scene_subject("{door_subject}", "_dp_entry"),
                *_door_lookup("door_subject"),
                _require(
                    "_dp_door.active == true and state.lasting_tick < _dp_door.expires_tick",
                    "This source door has vanished.",
                ),
                _require(
                    "_dp_entry.metadata.srd_object_access.closed == false",
                    "Open the actual Demiplane door before walking through it.",
                ),
                _require(
                    "_dp_actor.metadata.plane == _dp_entry.metadata.plane",
                    "Reach the actual entrance on the creature's current plane.",
                ),
                _require(
                    "actor_state.size_rank != null and actor_state.size_rank <= 2",
                    "The printed Medium passage currently admits Tiny, Small and Medium creatures; "
                    "larger squeezing remains selected.",
                ),
                *_body_clearance(
                    "_dp_actor",
                    "_dp_entry.metadata.aperture_face",
                    "_dp_entry.metadata.aperture_width",
                    "_dp_entry.metadata.aperture_height",
                ),
                *_measure("{_dp_actor.position}", "{_dp_entry.position}", "_dp_approach"),
                _require("_dp_approach <= 5", "Reach the actual source door before passage."),
                _call(
                    "srd_information_barriers",
                    {
                        "start": "{_dp_actor.position}",
                        "end": "{_dp_entry.position}",
                        "rule": "solid",
                        "plane": "{_dp_actor.metadata.plane}",
                    },
                    ["_information_blocked"],
                ),
                _require(
                    "_information_blocked != true",
                    "A real solid blocks approach to that door face.",
                ),
                *_scene_subject("{_dp_entry.metadata.demiplane_peer}", "_dp_exit"),
                _require(
                    "_dp_exit.metadata.srd_object_access.closed == false",
                    "The actual counterpart face must also be open.",
                ),
                *_body_clearance(
                    "_dp_actor",
                    "_dp_exit.metadata.aperture_face",
                    "_dp_exit.metadata.aperture_width",
                    "_dp_exit.metadata.aperture_height",
                ),
                *_measure("{_dp_exit.position}", "{destination}", "_dp_departure"),
                _require(
                    "_dp_departure <= 5",
                    "Choose a real space immediately beyond the opposite face.",
                ),
                _set("_travel_excluded", []),
                *_placement("{_actor_state_id}", "{destination}", "{_dp_exit.metadata.plane}"),
                _require(
                    "_travel_placement.distance == 0", "The actual destination must be unoccupied."
                ),
                _set("_dp_movement", "ceil(_dp_approach + _dp_departure)"),
                _when("_dp_movement > 0", _movement_cost("{_dp_movement}")),
                *_move("{_actor_state_id}", "{destination}", "{_dp_exit.metadata.plane}", False),
                _set("_dp_traverse_door", "door_subject"),
            ],
        ),
        "exploration",
        "Enter Demiplane door",
        [("Door", "_dp_traverse_door")],
    )
    shunt_choice = _public(
        _tool_scene(
            "Save this actual creature's optional shunt through this vanishing source door.",
            [_entity("actor_state"), _parameter("door_subject"), _parameter("shunt", "boolean")],
            [
                _require(
                    ACTIVE,
                    "An acting creature chooses whether to leave through the vanishing door.",
                ),
                _call("refresh_srd_demiplane_doors", {}),
                *_door_lookup("door_subject"),
                *_scene_subject("{_actor_state_id}", "_dp_chooser"),
                _require(
                    "_dp_chooser.metadata.is_object != true and "
                    "_dp_chooser.metadata.creature_type != 'spell_force'",
                    "Only an actual creature chooses the source shunt; objects remain.",
                ),
                _require(
                    "_dp_door.active == true and state.lasting_tick < _dp_door.expires_tick and "
                    "_dp_chooser.metadata.plane == _dp_door.room_id",
                    "Choose while actually inside the room of this active door.",
                ),
                _set("_dp_choices", []),
                _when(
                    "_dp_chooser.metadata.demiplane_shunt_doors != null",
                    [
                        _set("_dp_old_choices", "_dp_chooser.metadata.demiplane_shunt_doors"),
                        _loop(
                            "_dp_old_choices",
                            "_dp_old_choice",
                            [
                                _when(
                                    "_dp_old_choice != _dp_door.id",
                                    [_push("_dp_choices", "_dp_old_choice")],
                                ),
                            ],
                        ),
                    ],
                ),
                _when("shunt == true", [_push("_dp_choices", "_dp_door.id")]),
                _set("_dp_choice_patch", {"demiplane_shunt_doors": "_dp_choices"}),
                _set("_dp_choice_metadata", "merge(_dp_chooser.metadata, _dp_choice_patch)"),
                {
                    "spatial_mutation": {
                        "scene": "state.spatial_scene",
                        "map_id": "{state.spatial_scene.map.id}",
                        "expected_revision": "{state.spatial_scene.map.revision}",
                        "target": {"kind": "entity", "id": "{_dp_chooser.id}"},
                        "patch": {"metadata": "{_dp_choice_metadata}"},
                    },
                    "store_as": "_dp_choice_change",
                },
                _set("_dp_choice_answer", "shunt"),
            ],
        ),
        "exploration",
        "Choose Demiplane shunt",
        [("Shunt", "_dp_choice_answer")],
    )
    end = _tool_scene(
        "Retire only this timed door; preserve the room, objects and creatures that stay.",
        [_parameter("door_id")],
        [
            *_door_lookup("door_id"),
            _when(
                "_dp_door.active == true",
                [
                    *_scene_subject("{_dp_door.id}", "_dp_former_door"),
                    *[
                        effect
                        for field in ("{_dp_door.id}", "{_dp_door.inner_id}")
                        for effect in [
                            *_scene_subject(field, "_dp_retiring_face"),
                            {
                                "spatial_mutation": {
                                    "scene": "state.spatial_scene",
                                    "map_id": "{state.spatial_scene.map.id}",
                                    "expected_revision": "{state.spatial_scene.map.revision}",
                                    "target": {"kind": "entity", "id": field},
                                    "patch": {
                                        "active": False,
                                        "movement": "passable",
                                        "visibility": "clear",
                                    },
                                },
                                "store_as": "_dp_retired_face",
                            },
                        ]
                    ],
                    _set("_dp_return_bodies", "state.spatial_scene.entities"),
                    _loop(
                        "_dp_return_bodies",
                        "_dp_return_body",
                        [
                            _when(
                                "_dp_return_body.active == true and "
                                "_dp_return_body.metadata.plane == _dp_door.room_id and "
                                "_dp_return_body.metadata.demiplane_shunt_doors != null and "
                                "_dp_door.id in _dp_return_body.metadata.demiplane_shunt_doors",
                                [
                                    _set("_dp_return_actor", "_dp_return_body.id"),
                                    _when(
                                        "_dp_return_body.metadata.participant_ref != null",
                                        [
                                            _set(
                                                "_dp_return_actor",
                                                "_dp_return_body.metadata.participant_ref",
                                            ),
                                        ],
                                    ),
                                    _set("_travel_excluded", []),
                                    _set("_dp_shunt_reference_face", None),
                                    *[
                                        _when(f"_dp_door.face == '{face}'", [
                                            _set("_dp_shunt_reference_face", "'" + opposite + "'"),
                                        ])
                                        for face, opposite in (
                                            ("west", "east"), ("east", "west"),
                                            ("south", "north"), ("north", "south"),
                                            ("down", "up"), ("up", "down"),
                                        )
                                    ],
                                    _require(
                                        "_dp_shunt_reference_face != null",
                                        "The actual former door needs its saved supporting face.",
                                    ),
                                    *_placement(
                                        "{_dp_return_body.id}",
                                        "{_dp_former_door.position}",
                                        "{_dp_former_door.metadata.plane}",
                                        reference=("entity", "{_dp_former_door.id}",
                                                   "{_dp_shunt_reference_face}"),
                                    ),
                                    *_move(
                                        "{_dp_return_body.id}",
                                        "{_travel_placement.position}",
                                        "{_dp_former_door.metadata.plane}",
                                        False,
                                    ),
                                    _call(
                                        "apply_srd_condition",
                                        {
                                            "target": "{_dp_return_actor}",
                                            "condition": "prone",
                                            "source_state_id": "{_dp_door.caster}",
                                            "source_is_magical": True,
                                        },
                                    ),
                                ],
                            ),
                        ],
                    ),
                    _set("_dp_retirement_patch", {"active": False}),
                    _set("_dp_door", "merge(_dp_door, _dp_retirement_patch)"),
                    _set("_dp_retained_doors", []),
                    _set("_dp_current_doors", "state.demiplane_doors"),
                    _loop(
                        "_dp_current_doors",
                        "_dp_current_door",
                        [
                            {
                                "condition": "_dp_current_door.id == _dp_door.id",
                                "on_true": [_push("_dp_retained_doors", "_dp_door")],
                                "on_false": [_push("_dp_retained_doors", "_dp_current_door")],
                            },
                        ],
                    ),
                    _set("state.demiplane_doors", "_dp_retained_doors"),
                ],
            ),
        ],
    )
    refresh = _tool_scene(
        "Expire source doors at the exact shared clock without erasing persistent rooms.",
        [],
        [
            _when(
                "state.demiplane_doors != null",
                [
                    _set("_dp_expiring_doors", "state.demiplane_doors"),
                    _loop(
                        "_dp_expiring_doors",
                        "_dp_expiring_door",
                        [
                            _when(
                                "_dp_expiring_door.active == true and "
                                "state.lasting_tick >= _dp_expiring_door.expires_tick",
                                [
                                    _call(
                                        "end_srd_demiplane_door",
                                        {"door_id": "{_dp_expiring_door.id}"},
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            )
        ],
    )
    return {
        "validate_srd_demiplane_cast": preflight,
        "install_srd_demiplane": install,
        "create_srd_demiplane_door_pair": pair,
        "sync_srd_demiplane_door": sync,
        "traverse_srd_demiplane_door": traverse,
        "choose_srd_demiplane_shunt": shunt_choice,
        "end_srd_demiplane_door": end,
        "refresh_srd_demiplane_doors": refresh,
    }


def bind_demiplane_consumers(tools, changed):
    """Bind room bounds at the existing actual relocation and final movement owners."""
    if "qualify_srd_demiplane_surface" in tools:
        changed.update(deepcopy(demiplane_tools(tools)))
    clock = deepcopy(changed.get("advance_srd_lasting_clock", tools["advance_srd_lasting_clock"]))
    if "refresh_srd_demiplane_doors" not in str(clock["effects"]):
        clock["effects"].append(_call("refresh_srd_demiplane_doors", {}))
    changed["advance_srd_lasting_clock"] = clock
    operation_name = "operate_srd_locked_object"
    if "operate_srd_locked_object_node" in tools:
        wrapper = deepcopy(changed.get(operation_name, tools[operation_name]))
        wrapper["effects"] = [
            effect
            for effect in wrapper["effects"]
            if effect.get("call") != "refresh_srd_demiplane_doors"
            and effect.get("condition") != "_object_scene.metadata.demiplane_door != null"
        ]
        changed[operation_name] = wrapper
        operation_name += "_node"
    operate = deepcopy(changed.get(operation_name, tools[operation_name]))
    if "sync_srd_demiplane_door" not in str(operate["effects"]):
        operate["effects"].insert(0, _call("refresh_srd_demiplane_doors", {}))
        operate["effects"].append(
            _when(
                "_object_scene.metadata.demiplane_door != null",
                [
                    _call("sync_srd_demiplane_door", {"door_subject": "{object_id}"}),
                ],
            )
        )
    changed[operation_name] = operate
    for name in (
        "resolve_srd_travel_placement", "resolve_srd_travel_geometry_face_placement",
        "resolve_srd_travel_entity_face_placement",
    ):
        original = changed.get(name, tools.get(name))
        if original is None:
            continue
        if "_demiplane_placement_zone" not in str(original["effects"]):
            placement = deepcopy(original)
            placement["effects"] = [
                _set("_demiplane_placement_zone", None),
                _when(
                    "state.demiplane_room_ids != null and plane in state.demiplane_room_ids",
                    [
                        _set("_demiplane_placement_zone", "plane"),
                    ],
                ),
                *placement["effects"],
            ]
            effects = []
            for effect in placement["effects"]:
                if effect.get("spatial_query", {}).get("operation") != "placement":
                    effects.append(effect)
                    continue
                ordinary, bounded = deepcopy(effect), deepcopy(effect)
                ordinary["condition"] = "_demiplane_placement_zone == null"
                bounded["condition"] = "_demiplane_placement_zone != null"
                bounded["spatial_query"]["query"]["containing_zone_id"] = (
                    "{_demiplane_placement_zone}"
                )
                effects.extend([ordinary, bounded])
            placement["effects"] = effects
            changed[name] = placement
    for name in (
        "move_srd_travel_subject",
        "move_srd_travel_subject_node",
        "move_srd_scene_creature_environment_segment",
        "move_srd_scene_creature_node",
    ):
        original = changed.get(name, tools.get(name))
        if original is None or not any("spatial_mutation" in e for e in original["effects"]):
            continue
        movement = deepcopy(original)
        movement["effects"] = [
            e for e in movement["effects"] if e.get("call") != "check_srd_demiplane_body_bounds"
        ]
        boundary = next(
            i for i, effect in enumerate(movement["effects"]) if "spatial_mutation" in effect
        )
        travel = name in {"move_srd_travel_subject", "move_srd_travel_subject_node"}
        subject = "{subject}" if travel else "{_actor_state_id}"
        plane = "{plane}" if travel else "{_travel_movement_plane}"
        movement["effects"].insert(
            boundary,
            _call(
                "check_srd_demiplane_body_bounds",
                {
                    "subject": subject,
                    "destination": "{destination}",
                    "plane": plane,
                },
            ),
        )
        changed[name] = movement
