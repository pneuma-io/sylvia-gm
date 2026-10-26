"""Retained extradimensional dwellings use actual doors, rooms and source returns."""

from copy import deepcopy

if __package__:
    from .srd_scene_relations import _array, _snapshot, _tool_scene
    from .srd_spell_object_content import _metadata_patch, _new_scene_object
    from .srd_spell_travel_room_geometry import (
        _body_clearance,
        panel_space_effects,
        physical_bounds,
    )
    from .srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _when
    from .srd_timed_senses import _loop, _optional, _public, _push
else:
    from srd_scene_relations import _array, _snapshot, _tool_scene
    from srd_spell_object_content import _metadata_patch, _new_scene_object
    from srd_spell_travel_room_geometry import _body_clearance, panel_space_effects, physical_bounds
    from srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _when
    from srd_timed_senses import _loop, _optional, _public, _push

PUBLIC_TOOLS = {"operate_srd_mansion_door", "traverse_srd_dwelling_door"}


def dwelling_intent():
    """Door placement and unprinted construction thickness are creation intent, never verdicts."""
    if __package__:
        from .srd_spell_travel_banquet import banquet_intent
        from .srd_spell_travel_furnishings import furnishing_intent
        from .srd_spell_travel_servants import servant_intent
    else:
        from srd_spell_travel_banquet import banquet_intent
        from srd_spell_travel_furnishings import furnishing_intent
        from srd_spell_travel_servants import servant_intent

    return [
        _parameter("dwelling_id"),
        _array("point"),
        _optional("designated", "array", [], items={"type": "string"}, max_items=100),
        _optional(
            "floor_cells",
            "array",
            [[0, 0, 0]],
            items={"type": "array", "items": {"type": "integer"}},
            min_items=1,
            max_items=50,
        ),
        _optional("wall_thickness", "number", 0.25, minimum=0.001, maximum=1),
        _optional("door_depth", "number", 0.25, minimum=0.001, maximum=1),
        _optional("lighting", "enum", "bright", enum_values=["bright", "dim", "darkness"]),
        furnishing_intent(),
        banquet_intent(),
        servant_intent(),
    ]


def _lookup(identity="source_id"):
    """Read one retained actual source; retirement deliberately remains readable."""
    return [
        _set("_xd_source", None),
        _set("_xd_rows", []),
        _when(
            "state.extradimensional_sources != null",
            [
                _set("_xd_rows", "state.extradimensional_sources"),
            ],
        ),
        _loop(
            "_xd_rows",
            "_xd_row",
            [
                _when(f"_xd_row.id == {identity}", [_set("_xd_source", "_xd_row")]),
            ],
        ),
        _require("_xd_source != null", "Choose an actual retained extradimensional source."),
    ]


def _save():
    """Replace only the exact source record in its existing family pool."""
    return [
        _set("_xd_saved", []),
        _set("_xd_current", "state.extradimensional_sources"),
        _loop(
            "_xd_current",
            "_xd_current_source",
            [
                {
                    "condition": "_xd_current_source.id == _xd_source.id",
                    "on_true": [_push("_xd_saved", "_xd_source")],
                    "on_false": [_push("_xd_saved", "_xd_current_source")],
                }
            ],
        ),
        _set("state.extradimensional_sources", "_xd_saved"),
    ]


def dwelling_tools(tools):
    """Emit the Mansion access vertical without another spatial or inventory authority."""
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

    magic_effects = [
        _require(
            "state.has_spell_magic_zones != true",
            "The actual dwelling needs its magic-zone consumer while such a source is active.",
        )
    ]
    if "check_srd_magic_zone_effect" in tools:
        magic_effects = [
            _call(
                "check_srd_magic_zone_effect",
                {
                    "source_subject": "{subject}",
                    "target_subject": "{subject}",
                    "effect_kind": "spell",
                    "actual_spell_level": "{slot_level}",
                },
                ["_magic_zone_effect_allowed"],
            ),
            _require(
                "_magic_zone_effect_allowed == true",
                "The actual magic zone suppresses this dwelling doorway or ability.",
            ),
        ]
    magic = _tool_scene(
        "Qualify an actual doorway or caster body against the saved magic-zone owner.",
        [_parameter("subject"), _parameter("slot_level", "integer", minimum=7, maximum=9)],
        magic_effects,
    )

    identities = _tool_scene(
        "Bind deterministic source-derived scene identities through actual call parameters.",
        [
            _parameter("room_id"),
            _parameter("outer_id"),
            _parameter("inner_id"),
            _parameter("face_ids", "array", items={"type": "string"}),
        ],
        [
            _set("_xd_plane", "room_id"),
            _set("_xd_outer_id", "outer_id"),
            _set("_xd_inner_id", "inner_id"),
            _set("_xd_face_ids", "face_ids"),
        ],
    )
    preflight = _tool_scene(
        "Validate actual Mansion door construction and initial guests before payment.",
        [_entity("caster_state"), *dwelling_intent()],
        [
            *_snapshot(),
            *_scene_subject("{_caster_state_id}", "_xd_caster"),
            _require(
                "_xd_caster.metadata.plane != null and dwelling_id != ''",
                "Use the actual caster plane and a unique dwelling identity.",
            ),
            *_measure("{_xd_caster.position}", "{point}", "_xd_range"),
            _require("_xd_range <= 300", "The actual Mansion door must be within 300 feet."),
            _require(
                "len(floor_cells[0]) == 3", "Each floor cell needs three integer coordinates."
            ),
            _set("_xd_seen_cells", []),
            _set("_xd_first_cell", "floor_cells[0]"),
            *[
                effect
                for axis in range(3)
                for effect in [
                    _set(f"_xd_cell_min_{axis}", f"_xd_first_cell[{axis}]"),
                    _set(f"_xd_cell_max_{axis}", f"_xd_first_cell[{axis}]"),
                ]
            ],
            _loop(
                "floor_cells",
                "_xd_cell",
                [
                    _require(
                        "len(_xd_cell) == 3", "Each floor cell needs three integer coordinates."
                    ),
                    _require("_xd_cell not in _xd_seen_cells", "Floor plan cells must be unique."),
                    _push("_xd_seen_cells", "_xd_cell"),
                    *[
                        effect
                        for axis in range(3)
                        for effect in [
                            _when(
                                f"_xd_cell[{axis}] < _xd_cell_min_{axis}",
                                [_set(f"_xd_cell_min_{axis}", f"_xd_cell[{axis}]")],
                            ),
                            _when(
                                f"_xd_cell[{axis}] > _xd_cell_max_{axis}",
                                [_set(f"_xd_cell_max_{axis}", f"_xd_cell[{axis}]")],
                            ),
                        ]
                    ],
                ],
            ),
            _set(
                "_xd_room_extent",
                [f"(_xd_cell_max_{axis} - _xd_cell_min_{axis} + 1) * 10" for axis in range(3)],
            ),
            _require(
                "len(floor_cells) * 1000 == _xd_room_extent[0] * "
                "_xd_room_extent[1] * _xd_room_extent[2]",
                "The first physical Mansion mode supports contiguous rectangular cube plans; "
                "nonrectangular plans remain selected.",
            ),
            _call(
                "bind_srd_dwelling_scene_ids",
                {
                    "room_id": "{dwelling_id}:room",
                    "outer_id": "{dwelling_id}:door",
                    "inner_id": "{dwelling_id}:inside",
                    "face_ids": [
                        "{dwelling_id}:room:" + face
                        for face in ("west", "east", "south", "north", "floor", "ceiling")
                    ],
                },
                ["_xd_plane", "_xd_outer_id", "_xd_inner_id", "_xd_face_ids"],
            ),
            *[
                effect
                for collection in ("entities", "geometry", "zones")
                for effect in [
                    _set("_xd_existing", f"state.spatial_scene.{collection}"),
                    _loop(
                        "_xd_existing",
                        "_xd_existing_row",
                        [
                            _require(
                                "_xd_existing_row.id != _xd_plane and "
                                "_xd_existing_row.id != _xd_outer_id and "
                                "_xd_existing_row.id != _xd_inner_id and "
                                "_xd_existing_row.id not in _xd_face_ids",
                                "Dwelling construction cannot replace an admitted scene identity.",
                            ),
                        ],
                    ),
                ]
            ],
            _when(
                "state.extradimensional_sources != null",
                [
                    _set("_xd_existing_sources", "state.extradimensional_sources"),
                    _loop(
                        "_xd_existing_sources",
                        "_xd_existing_source",
                        [
                            _require(
                                "_xd_existing_source.id != dwelling_id",
                                "An actual dwelling identity cannot be reused.",
                            ),
                        ],
                    ),
                ],
            ),
            _set("_xd_guests", []),
            _loop(
                "designated",
                "_xd_designated",
                [
                    *_scene_subject("{_xd_designated}", "_xd_guest"),
                    _require(
                        "_xd_guest.metadata.participant_ref != null",
                        "Designate actual admitted creatures, not invented guest verdicts.",
                    ),
                    _require(
                        "_xd_guest.metadata.participant_ref not in _xd_guests",
                        "A creature can be designated only once.",
                    ),
                    _push("_xd_guests", "_xd_guest.metadata.participant_ref"),
                ],
            ),
            _set(
                "_xd_room_center",
                [
                    f"(state.spatial_scene.map.bounds.min[{axis}] + "
                    f"state.spatial_scene.map.bounds.max[{axis}]) * 0.5"
                    for axis in range(3)
                ],
            ),
            *[
                _require(
                    f"_xd_room_center[{axis}] - _xd_room_extent[{axis}] * 0.5 - wall_thickness >= "
                    f"state.spatial_scene.map.bounds.min[{axis}] and "
                    f"_xd_room_center[{axis}] + _xd_room_extent[{axis}] * 0.5 + wall_thickness <= "
                    f"state.spatial_scene.map.bounds.max[{axis}]",
                    "The admitted map must fit the actual foyer and physical walls.",
                )
                for axis in range(3)
            ],
            _set("_dp_physical_origin", "point"),
            _set(
                "_mf_room_min",
                [f"_xd_room_center[{axis}] - _xd_room_extent[{axis}] * 0.5" for axis in range(3)],
            ),
            _set(
                "_mf_room_max",
                [f"_xd_room_center[{axis}] + _xd_room_extent[{axis}] * 0.5" for axis in range(3)],
            ),
            _set("_mf_reserved", []),
            *[
                _push("_mf_reserved", identity)
                for identity in ("_xd_plane", "_xd_outer_id", "_xd_inner_id")
            ],
            *[
                effect
                for face in ("west", "east", "south", "north", "floor", "ceiling")
                for effect in [
                    _call(
                        "bind_srd_mansion_reserved_identity",
                        {"identity": "{_xd_plane}:" + face},
                        ["_mf_reserved_id"],
                    ),
                    _push("_mf_reserved", "_mf_reserved_id"),
                ]
            ],
            _set("_mf_combined", []),
            _loop("furnishings", "_mf_room_intent", [_push("_mf_combined", "_mf_room_intent")]),
            _loop("banquet", "_mf_food_intent", [_push("_mf_combined", "_mf_food_intent")]),
            _loop(
                "servants",
                "_ms_body_intent",
                [
                    _require(
                        "_ms_body_intent.index >= 1 and _ms_body_intent.index <= 100 and "
                        "_ms_body_intent.attire != ''",
                        "Choose one of the actual hundred staff members and its attire.",
                    ),
                    _set(
                        "_ms_physical_intent",
                        {
                            "id": "_ms_body_intent.id",
                            "offset": "_ms_body_intent.offset",
                            "extent": "_ms_body_intent.extent",
                            "material": "'source-created servant'",
                            "appearance": "_ms_body_intent.appearance",
                        },
                    ),
                    _push("_mf_combined", "_ms_physical_intent"),
                ],
            ),
            _call(
                "validate_srd_mansion_furnishings",
                {
                    "furnishings": "{_mf_combined}",
                    "dwelling_id": "{dwelling_id}",
                    "room_min": "{_mf_room_min}",
                    "room_max": "{_mf_room_max}",
                    "reserved_ids": "{_mf_reserved}",
                },
            ),
            _set("_dp_physical_extent", ["door_depth", 5, 10]),
            *physical_bounds("_dp_physical_origin", "_dp_physical_extent"),
            _set("_dp_panel_plane", "_xd_caster.metadata.plane"),
            _set("surface_id", "''"),
            *panel_space_effects(),
        ],
    )
    face = _tool_scene(
        "Create one actual room face through the existing canonical scenery constructor.",
        [
            _parameter("room_id"),
            _parameter("face_id"),
            _parameter(
                "face", "enum", enum_values=["west", "east", "south", "north", "floor", "ceiling"]
            ),
        ],
        [
            _set("_xd_room", None),
            _set("_xd_zones", "state.spatial_scene.zones"),
            _loop(
                "_xd_zones",
                "_xd_zone",
                [
                    _when("_xd_zone.id == room_id", [_set("_xd_room", "_xd_zone")]),
                ],
            ),
            _require(
                "_xd_room != null and _xd_room.metadata.kind == 'srd_dwelling_room'",
                "Create faces only for the actual source-owned room.",
            ),
            _set("_xd_thickness", "_xd_room.metadata.wall_thickness"),
            *[
                _when(
                    f"face == '{name}'",
                    [
                        _set(
                            "_world_origin",
                            [
                                (
                                    f"_xd_room.shape.{edge}[{axis}] + _xd_thickness * {sign * 0.5}"
                                    if other == axis
                                    else (
                                        "_xd_room.shape.min[2]"
                                        if other == 2
                                        else (
                                            f"(_xd_room.shape.min[{other}] + "
                                            f"_xd_room.shape.max[{other}]) * 0.5"
                                        )
                                    )
                                )
                                for other in range(3)
                            ],
                        ),
                        _set(
                            "_xd_extent",
                            [
                                "_xd_thickness"
                                if other == axis
                                else f"_xd_room.shape.max[{other}] - _xd_room.shape.min[{other}]"
                                for other in range(3)
                            ],
                        ),
                    ],
                )
                for name, axis, edge, sign in (
                    ("west", 0, "min", -1),
                    ("east", 0, "max", 1),
                    ("south", 1, "min", -1),
                    ("north", 1, "max", 1),
                    ("floor", 2, "min", -1),
                    ("ceiling", 2, "max", 1),
                )
            ],
            _when(
                "face == 'floor'",
                [
                    _set(
                        "_world_origin",
                        [
                            "_world_origin[0]",
                            "_world_origin[1]",
                            "_xd_room.shape.min[2] - _xd_thickness",
                        ],
                    ),
                ],
            ),
            _when(
                "face == 'ceiling'",
                [
                    _set(
                        "_world_origin",
                        ["_world_origin[0]", "_world_origin[1]", "_xd_room.shape.max[2]"],
                    )
                ],
            ),
            *_new_scene_object(
                {
                    "plane": "room_id",
                    "kind": "'srd_dwelling_face'",
                    "dwelling_source_id": "_xd_room.metadata.source_id",
                    "ground": "face == 'floor'",
                    "material": "'stone'",
                },
                identity="face_id",
                extent="_xd_extent",
                movement="'blocking'",
                visibility="'opaque'",
                scene_kind="geometry",
            ),
        ],
    )
    install = _tool_scene(
        "Create the paid Mansion's foyer and paired doors with the retained cast identity.",
        [
            _entity("caster_state"),
            _parameter("slot_level", "integer", minimum=7, maximum=9),
            *dwelling_intent(),
            _optional("spell_cast_id", default="", model_hidden=True),
            _optional("spell_cast_owner", default=None, resolve="entity", model_hidden=True),
            _optional(
                "actual_spell_level", "integer", -1, minimum=-1, maximum=9, model_hidden=True
            ),
        ],
        [
            _call(
                "validate_srd_magnificent_mansion",
                {
                    "caster_state": "{_caster_state_id}",
                    **{p["name"]: "{" + p["name"] + "}" for p in dwelling_intent()},
                },
                [
                    "_xd_caster",
                    "_xd_room_center",
                    "_xd_room_extent",
                    "_xd_guests",
                    "_xd_plane",
                    "_xd_outer_id",
                    "_xd_inner_id",
                ],
            ),
            _when("state.lasting_tick == null", [_set("state.lasting_tick", 0)]),
            _set(
                "_xd_source",
                {
                    "id": "dwelling_id",
                    "spell": "'magnificent_mansion'",
                    "caster": "_caster_state_id",
                    "slot_level": "slot_level",
                    "spell_level": "slot_level",
                    "active": True,
                    "cast_id": "spell_cast_id",
                    "source_caster_id": None,
                    "ongoing_duration": "spell_cast_id != ''",
                    "created_tick": "state.lasting_tick",
                    "expires_tick": "state.lasting_tick + 28800",
                    "room_id": "_xd_plane",
                    "outer_id": "_xd_outer_id",
                    "inner_id": "_xd_inner_id",
                    "origin_plane": "_xd_caster.metadata.plane",
                    "origin": "point",
                    "door_depth": "door_depth",
                    "designated": "_xd_guests",
                    "is_open": True,
                    "return_pending": False,
                    "furnishings": "furnishings",
                    "banquet": "banquet",
                    "servants": "servants",
                    "servant_indices": list(range(1, 101)),
                },
            ),
            _when(
                "spell_cast_id != ''",
                [
                    _require(
                        "spell_cast_owner != null and _spell_cast_owner_id == _caster_state_id and "
                        "caster_state.last_spell_source_cast_id == spell_cast_id and "
                        "caster_state.last_spell_source_spell == 'magnificent_mansion'",
                        "Mansion must retain its authentic committed source identity.",
                    ),
                    _set("_xd_identity", {"source_caster_id": "_spell_cast_owner_id"}),
                    _set("_xd_source", "merge(_xd_source, _xd_identity)"),
                ],
            ),
            _when(
                "state.extradimensional_sources == null",
                [
                    _set("state.extradimensional_sources", []),
                ],
            ),
            _set("_xd_sources", "state.extradimensional_sources"),
            _push("_xd_sources", "_xd_source"),
            _set("state.extradimensional_sources", "_xd_sources"),
            _set("state.has_extradimensional_sources", True),
            _set(
                "_xd_room",
                {
                    "id": "_xd_plane",
                    "map_id": "state.spatial_scene.map.id",
                    "shape": {
                        "kind": "'box'",
                        "min": [
                            f"_xd_room_center[{axis}] - _xd_room_extent[{axis}] * 0.5"
                            for axis in range(3)
                        ],
                        "max": [
                            f"_xd_room_center[{axis}] + _xd_room_extent[{axis}] * 0.5"
                            for axis in range(3)
                        ],
                    },
                    "metadata": {
                        "kind": "'srd_dwelling_room'",
                        "plane": "_xd_plane",
                        "source_id": "dwelling_id",
                        "creator": "_caster_state_id",
                        "lighting": "lighting",
                        "wall_thickness": "wall_thickness",
                        "atmosphere": "'clean, fresh, warm'",
                        "floor_cells": "floor_cells",
                        "active": True,
                    },
                },
            ),
            _set("_xd_zones", "state.spatial_scene.zones"),
            _push("_xd_zones", "_xd_room"),
            _set("state.spatial_scene.zones", "_xd_zones"),
            _set("state.spatial_scene.map.revision", "state.spatial_scene.map.revision + 1"),
            *_snapshot(),
            *[
                _call(
                    "create_srd_dwelling_room_face",
                    {
                        "room_id": "{_xd_plane}",
                        "face_id": "{_xd_plane}:" + name,
                        "face": name,
                    },
                )
                for name in ("west", "east", "south", "north", "floor", "ceiling")
            ],
            _set(
                "_xd_inner_point",
                [
                    "_xd_room.shape.min[0] + door_depth * 0.5",
                    "_xd_room_center[1]",
                    "_xd_room.shape.min[2]",
                ],
            ),
            *[
                effect
                for inner in (False, True)
                for effect in [
                    _set("_world_origin", "_xd_inner_point" if inner else "point"),
                    _set("_xd_panel_extent", ["door_depth", 5, 10]),
                    *_new_scene_object(
                        {
                            "plane": "_xd_plane" if inner else "_xd_caster.metadata.plane",
                            "kind": "'door'",
                            "dwelling_source_id": "dwelling_id",
                            "dwelling_peer": "_xd_source.outer_id"
                            if inner
                            else "_xd_source.inner_id",
                            "source_ref": "'magnificent_mansion'",
                            "magical_effect": True,
                            "spell_school": "'conjuration'",
                            "source_caster": "_caster_state_id",
                            "aperture_face": "'east'",
                            "aperture_width": 5,
                            "aperture_height": 10,
                            "aperture_depth": "door_depth",
                            "imperceptible": False,
                            "srd_object_access": {
                                "kind": "'door'",
                                "closed": False,
                                "locks": [],
                                "stuck": False,
                                "barred": False,
                            },
                        },
                        identity="_xd_source.inner_id" if inner else "_xd_source.outer_id",
                        extent="_xd_panel_extent",
                        movement="'passable'",
                        visibility="'clear'",
                    ),
                ]
            ],
            _loop(
                "furnishings",
                "_xd_furnishing_intent",
                [
                    _call(
                        "create_srd_mansion_furnishing",
                        {"source_id": "{dwelling_id}", "object_id": "{_xd_furnishing_intent.id}"},
                    )
                ],
            ),
            _loop(
                "banquet",
                "_xd_banquet_intent",
                [
                    _call(
                        "create_srd_mansion_banquet",
                        {"source_id": "{dwelling_id}", "object_id": "{_xd_banquet_intent.id}"},
                    )
                ],
            ),
            _loop(
                "servants",
                "_xd_servant_intent",
                [
                    _call(
                        "create_srd_mansion_servant",
                        {"source_id": "{dwelling_id}", "object_id": "{_xd_servant_intent.id}"},
                    ),
                ],
            ),
            _set("_dwelling_created_source_id", "dwelling_id"),
        ],
    )
    project_door = _tool_scene(
        "Patch one actual retained doorway through the canonical scenery metadata owner.",
        [_parameter("object_id"), _parameter("is_open", "boolean")],
        [
            *_scene_subject("{object_id}", "_object_scene"),
            _set("_xd_door_movement", "'blocking'"),
            _when("is_open == true", [_set("_xd_door_movement", "'passable'")]),
            _set("_xd_access", "_object_scene.metadata.srd_object_access"),
            _set("_xd_closed", {"closed": "is_open != true"}),
            _set("_xd_access", "merge(_xd_access, _xd_closed)"),
            *_metadata_patch(
                {"srd_object_access": "_xd_access", "imperceptible": "is_open != true"},
                movement="{_xd_door_movement}",
                visibility="clear",
            ),
        ],
    )
    door_state = _tool_scene(
        "Project the exact retained source's open or closed state onto its actual paired doors.",
        [_parameter("source_id"), _parameter("is_open", "boolean")],
        [
            *_lookup(),
            *[
                _call("project_srd_dwelling_door", {"object_id": identity, "is_open": "{is_open}"})
                for identity in ("{_xd_source.outer_id}", "{_xd_source.inner_id}")
            ],
            _set("_xd_open_patch", {"is_open": "is_open"}),
            _set("_xd_source", "merge(_xd_source, _xd_open_patch)"),
            *_save(),
        ],
    )
    operate = _public(
        _tool_scene(
            "Open or close this caster's Mansion door within thirty feet without another action.",
            [_entity("caster_state"), _parameter("source_id"), _parameter("is_open", "boolean")],
            [
                _require(
                    ACTIVE.replace("actor_state", "caster_state"),
                    "A capable living caster must control this doorway.",
                ),
                _call("refresh_srd_extradimensional_sources", {}),
                *_lookup(),
                _require(
                    "_xd_source.active == true and _xd_source.spell == 'magnificent_mansion' and "
                    "_xd_source.caster == _caster_state_id",
                    "Only the actual Mansion caster can operate this retained door.",
                ),
                *_scene_subject("{_caster_state_id}", "_xd_actor"),
                _set("_xd_operated_door", "_xd_source.outer_id"),
                _when(
                    "_xd_actor.metadata.plane == _xd_source.room_id",
                    [
                        _set("_xd_operated_door", "_xd_source.inner_id"),
                    ],
                ),
                *_scene_subject("{_xd_operated_door}", "_xd_door"),
                _require(
                    "_xd_actor.metadata.plane == _xd_door.metadata.plane",
                    "Operate the actual door on the caster's current plane.",
                ),
                *_measure("{_xd_actor.position}", "{_xd_door.position}", "_xd_operate_distance"),
                _require(
                    "_xd_operate_distance <= 30", "The actual caster must be within thirty feet."
                ),
                *[
                    _call(
                        "check_srd_dwelling_magic",
                        {
                            "subject": subject,
                            "slot_level": "{_xd_source.slot_level}",
                        },
                    )
                    for subject in (
                        "{_xd_actor.id}",
                        "{_xd_source.outer_id}",
                        "{_xd_source.inner_id}",
                    )
                ],
                _call(
                    "set_srd_dwelling_door_state",
                    {"source_id": "{source_id}", "is_open": "{is_open}"},
                ),
                _set("_dwelling_door_open", "is_open"),
            ],
        ),
        "exploration",
        "Mansion door",
        [("Open", "_dwelling_door_open")],
    )
    traverse = _public(
        _tool_scene(
            "Walk through an open source doorway using existing movement and relocation owners.",
            [
                _entity("actor_state"),
                _parameter("source_id"),
                _parameter("door_subject"),
                _array("destination"),
            ],
            [
                _require(ACTIVE, "A capable living creature must traverse this doorway."),
                _call("refresh_srd_extradimensional_sources", {}),
                *_lookup(),
                _require(
                    "_xd_source.active == true and _xd_source.is_open == true",
                    "This actual Mansion doorway is closed or has ended.",
                ),
                _require(
                    "door_subject == _xd_source.outer_id or door_subject == _xd_source.inner_id",
                    "Choose one of this actual source's paired doors.",
                ),
                *_scene_subject("{_actor_state_id}", "_xd_actor"),
                *_scene_subject("{door_subject}", "_xd_entry"),
                _require(
                    "_xd_actor.metadata.plane == _xd_entry.metadata.plane",
                    "Reach the actual doorway on the creature's current plane.",
                ),
                _when(
                    "door_subject == _xd_source.outer_id",
                    [
                        _require(
                            "_actor_state_id == _xd_source.caster or _actor_state_id in "
                            "_xd_source.designated",
                            "Only the actual caster and initial guests can enter.",
                        ),
                    ],
                ),
                *_body_clearance("_xd_actor", "'east'", "5", "10"),
                *_measure("{_xd_actor.position}", "{_xd_entry.position}", "_xd_approach"),
                _require("_xd_approach <= 5", "Reach the actual portal before walking through it."),
                _call(
                    "srd_information_barriers",
                    {
                        "start": "{_xd_actor.position}",
                        "end": "{_xd_entry.position}",
                        "plane": "{_xd_actor.metadata.plane}",
                        "rule": "solid",
                    },
                    ["_information_blocked"],
                ),
                _require(
                    "_information_blocked != true", "A real solid blocks approach to the doorway."
                ),
                *_scene_subject("{_xd_entry.metadata.dwelling_peer}", "_xd_exit"),
                *[
                    _call(
                        "check_srd_dwelling_magic",
                        {
                            "subject": subject,
                            "slot_level": "{_xd_source.slot_level}",
                        },
                    )
                    for subject in ("{_xd_entry.id}", "{_xd_exit.id}")
                ],
                *_measure("{_xd_exit.position}", "{destination}", "_xd_departure"),
                _require(
                    "_xd_departure <= 5", "Choose an actual space just beyond the counterpart door."
                ),
                _when(
                    "door_subject == _xd_source.outer_id",
                    [
                        _call(
                            "check_srd_dwelling_room_bounds",
                            {
                                "subject": "{_actor_state_id}",
                                "destination": "{destination}",
                                "plane": "{_xd_source.room_id}",
                            },
                        ),
                    ],
                ),
                _set("_travel_excluded", []),
                *_placement("{_actor_state_id}", "{destination}", "{_xd_exit.metadata.plane}"),
                _require(
                    "_travel_placement.distance == 0",
                    "The actual arrival space must be unoccupied.",
                ),
                _set("_xd_movement", "ceil(_xd_approach + _xd_departure)"),
                _when("_xd_movement > 0", _movement_cost("{_xd_movement}")),
                *_move("{_actor_state_id}", "{destination}", "{_xd_exit.metadata.plane}", False),
                _set("_dwelling_entered_source_id", "source_id"),
            ],
        ),
        "exploration",
        "Traverse dwelling door",
        [("Dwelling", "_dwelling_entered_source_id")],
    )
    bounds = _tool_scene(
        "Keep an actual box body within its source-created dwelling's physical interior.",
        [_parameter("subject"), _array("destination"), _parameter("plane")],
        [
            _set("_xd_bound_room", None),
            _set("_xd_bound_zones", "state.spatial_scene.zones"),
            _loop(
                "_xd_bound_zones",
                "_xd_bound_zone",
                [
                    _when(
                        "_xd_bound_zone.metadata.kind == 'srd_dwelling_room' and "
                        "_xd_bound_zone.metadata.plane == plane",
                        [
                            _require(
                                "_xd_bound_room == null", "The actual dwelling plane is ambiguous."
                            ),
                            _set("_xd_bound_room", "_xd_bound_zone"),
                        ],
                    ),
                ],
            ),
            _when(
                "_xd_bound_room != null",
                [
                    *_scene_subject("{subject}", "_xd_bound_body"),
                    _require(
                        "_xd_bound_body.footprint.kind == 'box'",
                        "Exact dwelling body containment currently needs a box footprint.",
                    ),
                    *[
                        _require(
                            f"destination[{axis}] - _xd_bound_body.footprint.half_extents[{axis}] "
                            f">= _xd_bound_room.shape.min[{axis}] and destination[{axis}] + "
                            f"_xd_bound_body.footprint.half_extents[{axis}] <= "
                            f"_xd_bound_room.shape.max[{axis}]",
                            "The actual body must fit within the source's room bounds.",
                        )
                        for axis in range(2)
                    ],
                    _require(
                        "destination[2] >= _xd_bound_room.shape.min[2] and destination[2] + "
                        "_xd_bound_body.height <= _xd_bound_room.shape.max[2]",
                        "The actual body must fit the room's floor and ceiling.",
                    ),
                ],
            ),
        ],
    )
    end = _tool_scene(
        "Retire the exact dwelling source and queue its return outside raw source removal.",
        [_parameter("source_id")],
        [
            *_lookup(),
            _when(
                "_xd_source.active == true",
                [
                    _set("_xd_end_patch", {"active": False, "return_pending": True}),
                    _set("_xd_source", "merge(_xd_source, _xd_end_patch)"),
                    *_save(),
                    _set("state.has_extradimensional_pending_returns", True),
                ],
            ),
        ],
    )
    refresh = _tool_scene(
        "Retire expired sources against the shared clock without allocating or moving inline.",
        [],
        [
            _when(
                "state.extradimensional_sources != null",
                [
                    _set("_xd_expiring", "state.extradimensional_sources"),
                    _loop(
                        "_xd_expiring",
                        "_xd_expiring_source",
                        [
                            _when(
                                "_xd_expiring_source.active == true and state.lasting_tick >= "
                                "_xd_expiring_source.expires_tick",
                                [
                                    _call(
                                        "end_srd_extradimensional_source",
                                        {"source_id": "{_xd_expiring_source.id}"},
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    settle = _tool_scene(
        "Settle queued Mansion occupants through actual nearest placement and sole relocation.",
        [],
        [
            _require(
                "state.pending_srd_action_stack == null or "
                "len(state.pending_srd_action_stack) == 0",
                "Complete the actual saved window before settling dwelling returns.",
            ),
            _when(
                "state.has_extradimensional_pending_returns == true",
                [
                    _set("_xd_return_sources", "state.extradimensional_sources"),
                    _loop(
                        "_xd_return_sources",
                        "_xd_return_source",
                        [
                            _when(
                                "_xd_return_source.return_pending == true",
                                [
                                    *_lookup("_xd_return_source.id"),
                                    _require(
                                        "_xd_source.spell == 'magnificent_mansion'",
                                        "Other dwelling modes need their actual "
                                        "drop or retrieval owner.",
                                    ),
                                    _set("_xd_return_entities", "state.spatial_scene.entities"),
                                    _loop(
                                        "_xd_return_entities",
                                        "_xd_return_body",
                                        [
                                            _when(
                                                "_xd_return_body.active == true and "
                                                "_xd_return_body.metadata.plane == "
                                                "_xd_source.room_id and "
                                                "_xd_return_body.id != _xd_source.inner_id",
                                                [
                                                    _set("_travel_excluded", []),
                                                    _push(
                                                        "_travel_excluded", "_xd_source.outer_id"
                                                    ),
                                                    *_placement(
                                                        "{_xd_return_body.id}",
                                                        "{_xd_source.origin}",
                                                        "{_xd_source.origin_plane}",
                                                        reference=(
                                                            "entity",
                                                            "{_xd_source.outer_id}",
                                                            "west",
                                                        ),
                                                    ),
                                                    *_move(
                                                        "{_xd_return_body.id}",
                                                        "{_travel_placement.position}",
                                                        "{_xd_source.origin_plane}",
                                                        False,
                                                    ),
                                                ],
                                            ),
                                        ],
                                    ),
                                    *[
                                        effect
                                        for identity in (
                                            "{_xd_source.outer_id}",
                                            "{_xd_source.inner_id}",
                                        )
                                        for effect in [
                                            *_snapshot(),
                                            {
                                                "spatial_mutation": {
                                                    "scene": "state.spatial_scene",
                                                    "map_id": "{state.spatial_scene.map.id}",
                                                    "expected_revision": (
                                                        "{_info_snapshot.geometry_revision}"
                                                    ),
                                                    "target": {"kind": "entity", "id": identity},
                                                    "patch": {
                                                        "active": False,
                                                        "movement": "passable",
                                                        "visibility": "clear",
                                                    },
                                                },
                                                "store_as": "_xd_retired_portal",
                                            },
                                        ]
                                    ],
                                    *[
                                        effect
                                        for face_name in (
                                            "west",
                                            "east",
                                            "south",
                                            "north",
                                            "floor",
                                            "ceiling",
                                        )
                                        for effect in [
                                            *_snapshot(),
                                            {
                                                "spatial_mutation": {
                                                    "scene": "state.spatial_scene",
                                                    "map_id": "{state.spatial_scene.map.id}",
                                                    "expected_revision": (
                                                        "{_info_snapshot.geometry_revision}"
                                                    ),
                                                    "target": {
                                                        "kind": "geometry",
                                                        "id": "{_xd_source.room_id}:" + face_name,
                                                    },
                                                    "patch": {
                                                        "active": False,
                                                        "movement": "passable",
                                                        "visibility": "clear",
                                                    },
                                                },
                                                "store_as": "_xd_retired_wall",
                                            },
                                        ]
                                    ],
                                    _set("_xd_retained_zones", []),
                                    _set("_xd_retiring_zones", "state.spatial_scene.zones"),
                                    _loop(
                                        "_xd_retiring_zones",
                                        "_xd_retiring_zone",
                                        [
                                            _when(
                                                "_xd_retiring_zone.id == _xd_source.room_id",
                                                [
                                                    _set("_xd_inactive", {"active": False}),
                                                    _set(
                                                        "_xd_zone_metadata",
                                                        "merge("
                                                        "_xd_retiring_zone.metadata, _xd_inactive)",
                                                    ),
                                                    _set(
                                                        "_xd_zone_change",
                                                        {"metadata": "_xd_zone_metadata"},
                                                    ),
                                                    _set(
                                                        "_xd_retiring_zone",
                                                        "merge(_xd_retiring_zone, _xd_zone_change)",
                                                    ),
                                                ],
                                            ),
                                            _push("_xd_retained_zones", "_xd_retiring_zone"),
                                        ],
                                    ),
                                    _set("state.spatial_scene.zones", "_xd_retained_zones"),
                                    _set(
                                        "state.spatial_scene.map.revision",
                                        "state.spatial_scene.map.revision + 1",
                                    ),
                                    *_snapshot(),
                                    _set("_xd_settled_patch", {"return_pending": False}),
                                    _set("_xd_source", "merge(_xd_source, _xd_settled_patch)"),
                                    *_save(),
                                ],
                            ),
                        ],
                    ),
                    _set("state.has_extradimensional_pending_returns", False),
                ],
            ),
        ],
    )
    return {
        "bind_srd_dwelling_scene_ids": identities,
        "check_srd_dwelling_magic": magic,
        "validate_srd_magnificent_mansion": preflight,
        "create_srd_dwelling_room_face": face,
        "install_srd_magnificent_mansion": install,
        "set_srd_dwelling_door_state": door_state,
        "project_srd_dwelling_door": project_door,
        "operate_srd_mansion_door": operate,
        "traverse_srd_dwelling_door": traverse,
        "check_srd_dwelling_room_bounds": bounds,
        "end_srd_extradimensional_source": end,
        "refresh_srd_extradimensional_sources": refresh,
        "settle_srd_extradimensional_returns": settle,
    }


def bind_dwelling_consumers(tools, changed):
    """Bind actual room containment and expiry at their existing movement and clock owners."""
    geometry_name = "resolve_srd_object_content_geometry"
    geometry = changed.get(geometry_name, tools.get(geometry_name))
    if geometry is not None and "_xd_exposure_missing_plane" not in str(geometry["effects"]):
        geometry = deepcopy(geometry)
        effects = []
        for effect in geometry["effects"]:
            query = effect.get("spatial_query", {})
            if query.get("operation") == "exposure":
                effects.append(
                    _set(
                        "_xd_exposure_missing_plane",
                        "_world_caster_scene.metadata.plane == 'material'",
                    )
                )
                query["query"]["metadata_filter"] = {
                    "key": "plane",
                    "value": "{_world_caster_scene.metadata.plane}",
                    "missing_matches": "{_xd_exposure_missing_plane}",
                }
            effects.append(effect)
        geometry["effects"] = effects
        changed[geometry_name] = geometry
    clock_name = "advance_srd_lasting_clock"
    if clock_name in tools:
        clock = deepcopy(changed.get(clock_name, tools[clock_name]))
        if "refresh_srd_extradimensional_sources" not in str(clock["effects"]):
            clock["effects"].append(
                _when(
                    "state.has_extradimensional_sources == true",
                    [
                        _call("refresh_srd_extradimensional_sources", {}),
                    ],
                )
            )
        changed[clock_name] = clock
    operation = "operate_srd_locked_object_node"
    if operation not in tools:
        operation = "operate_srd_locked_object"
    facts = "record_srd_object_access_node"
    if facts not in tools:
        facts = "record_srd_object_access"
    for name in (operation, facts):
        original = changed.get(name, tools.get(name))
        if original is None:
            continue
        access = deepcopy(original)
        if "A source-owned dwelling doorway" in str(access["effects"]):
            continue
        # The canonical qualifier supplies the actual object before any access mutation.
        boundary = next(
            (
                i
                for i, effect in enumerate(access["effects"])
                if effect.get("call") == "resolve_srd_object_content_geometry"
                or effect.get("set", {}).get("field") == "_object_scene"
            ),
            None,
        )
        if boundary is None:
            continue
        access["effects"].insert(
            boundary + 1,
            _require(
                "_object_scene.metadata.dwelling_source_id == null",
                "A source-owned dwelling doorway uses its caster's exact source control; "
                "generic access changes remain selected.",
            ),
        )
        changed[name] = access
    for name in (
        "resolve_srd_travel_placement",
        "resolve_srd_travel_geometry_face_placement",
        "resolve_srd_travel_entity_face_placement",
    ):
        original = changed.get(name, tools.get(name))
        if original is None or "_xd_placement_zone" in str(original["effects"]):
            continue
        placement = deepcopy(original)
        placement["effects"] = [
            _set("_xd_placement_zone", None),
            _set("_xd_placement_zones", "state.spatial_scene.zones"),
            _loop(
                "_xd_placement_zones",
                "_xd_placement_room",
                [
                    _when(
                        "_xd_placement_room.metadata.kind == 'srd_dwelling_room' and "
                        "_xd_placement_room.metadata.plane == plane",
                        [
                            _require(
                                "_xd_placement_zone == null and "
                                "_xd_placement_room.metadata.active == true",
                                "Placement needs one actual active dwelling room.",
                            ),
                            _set("_xd_placement_zone", "_xd_placement_room.id"),
                        ],
                    ),
                ],
            ),
            *placement["effects"],
        ]
        effects = []
        for effect in placement["effects"]:
            query = effect.get("spatial_query", {})
            if query.get("operation") != "placement":
                effects.append(effect)
                continue
            ordinary, bounded = deepcopy(effect), deepcopy(effect)
            prior = effect.get("condition")
            prefix = "(" + prior + ") and " if prior else ""
            ordinary["condition"] = prefix + "_xd_placement_zone == null"
            bounded["condition"] = prefix + "_xd_placement_zone != null"
            bounded["spatial_query"]["query"]["containing_zone_id"] = "{_xd_placement_zone}"
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
            e for e in movement["effects"] if e.get("call") != "check_srd_dwelling_room_bounds"
        ]
        boundary = next(i for i, e in enumerate(movement["effects"]) if "spatial_mutation" in e)
        travel = name in {"move_srd_travel_subject", "move_srd_travel_subject_node"}
        movement["effects"].insert(
            boundary,
            _call(
                "check_srd_dwelling_room_bounds",
                {
                    "subject": "{subject}" if travel else "{_actor_state_id}",
                    "destination": "{destination}",
                    "plane": "{plane}" if travel else "{_travel_movement_plane}",
                },
            ),
        )
        changed[name] = movement
