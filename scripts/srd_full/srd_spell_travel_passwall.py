"""Passwall opens actual box solids and returns actual occupants through the sole mover."""

from copy import deepcopy

if __package__:
    from .srd_scene_relations import _array, _snapshot, _tool_scene
    from .srd_spell_object_content import _new_scene_object
    from .srd_spell_travel_demiplane import FACES
    from .srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _when
    from .srd_timed_senses import _loop, _optional, _push
else:
    from srd_scene_relations import _array, _snapshot, _tool_scene
    from srd_spell_object_content import _new_scene_object
    from srd_spell_travel_demiplane import FACES
    from srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _when
    from srd_timed_senses import _loop, _optional, _push


SEGMENTS = ["west", "east", "south", "north", "floor", "ceiling"]


def _choices():
    """Chosen opening dimensions are positive and bounded by the printed source maxima."""
    return [
        _parameter("surface_id"),
        _array("point"),
        _parameter("face", "enum", enum_values=FACES),
        _optional("width", "number", 5, minimum=0, maximum=5),
        _optional("height", "number", 8, minimum=0, maximum=8),
        _optional("depth", "number", 20, minimum=0, maximum=20),
    ]


def _lookup_source():
    return [
        _set("_pw_source", None),
        _require("state.passwall_sources != null", "There is no actual Passwall source."),
        _set("_pw_sources", "state.passwall_sources"),
        _loop(
            "_pw_sources",
            "_pw_candidate",
            [
                _when("_pw_candidate.id == source_id", [_set("_pw_source", "_pw_candidate")]),
            ],
        ),
        _require("_pw_source != null", "Select an actual retained Passwall source."),
    ]


def _geometry_lookup(identity, local):
    return [
        _set(local, None),
        _set("_pw_geometry_rows", "state.spatial_scene.geometry"),
        _loop(
            "_pw_geometry_rows",
            "_pw_geometry",
            [
                _when(f"_pw_geometry.id == {identity}", [_set(local, "_pw_geometry")]),
            ],
        ),
        _require(f"{local} != null", "The source's actual saved geometry is unavailable."),
    ]


def _mutate_geometry(identity, patch):
    """Each sequential patch binds the latest admitted revision from the existing snapshot owner."""
    return [
        *_snapshot(),
        {
            "spatial_mutation": {
                "scene": "state.spatial_scene",
                "map_id": "{_info_snapshot.map_id}",
                "expected_revision": "{_info_snapshot.geometry_revision}",
                "target": {"kind": "geometry", "id": identity},
                "patch": patch,
            },
            "store_as": "_pw_geometry_mutation",
        }
    ]


def _opening_bounds():
    """Derive the inward opening from the real face, clipping only its removed solid volume."""
    effects = []
    for face, axis, positive in (
        ("west", 0, True),
        ("east", 0, False),
        ("south", 1, True),
        ("north", 1, False),
        ("down", 2, True),
        ("up", 2, False),
    ):
        lower, upper = [], []
        for index in range(3):
            if index == axis:
                lower.append(f"point[{index}]" if positive else f"point[{index}] - depth")
                upper.append(f"point[{index}] + depth" if positive else f"point[{index}]")
            elif axis == 2 or index != 2:
                dimension = "width" if index == (0 if axis == 2 else 1 - axis) else "height"
                lower.append(f"point[{index}] - {dimension} * 0.5")
                upper.append(f"point[{index}] + {dimension} * 0.5")
            else:
                lower.append("point[2]")
                upper.append("point[2] + height")
        effects.append(
            _when(
                f"face == '{face}'",
                [
                    _set("_pw_intent_min", lower),
                    _set("_pw_intent_max", upper),
                    *[
                        _require(
                            f"_pw_intent_min[{i}] >= _pw_surface.volume.min[{i}] and "
                            f"_pw_intent_max[{i}] <= _pw_surface.volume.max[{i}]",
                            "The full chosen opening must fit its actual supporting surface face.",
                        )
                        for i in range(3)
                        if i != axis
                    ],
                ],
            )
        )
    return [
        *effects,
        _set(
            "_pw_open_min",
            [f"max(_pw_intent_min[{i}], _pw_surface.volume.min[{i}])" for i in range(3)],
        ),
        _set(
            "_pw_open_max",
            [f"min(_pw_intent_max[{i}], _pw_surface.volume.max[{i}])" for i in range(3)],
        ),
        *[
            _require(
                f"_pw_open_min[{i}] >= state.spatial_scene.map.bounds.min[{i}] and "
                f"_pw_open_max[{i}] <= state.spatial_scene.map.bounds.max[{i}]",
                "The actual created passage must fit the admitted map bounds.",
            )
            for i in range(3)
        ],
        _require(
            " and ".join(f"_pw_open_max[{i}] > _pw_open_min[{i}]" for i in range(3)),
            "The actual source surface must contain a positive removed solid volume.",
        ),
    ]


def passwall_tools(tools):
    """Own prepayment surface qualification, physical box subtraction, lifetime and ejection."""
    if __package__:
        from .srd_spell_travel import _move, _placement
    else:
        from srd_spell_travel import _move, _placement

    selected_solid = (
        "_pw_solid.id != surface_id and _pw_solid.active == true and "
        "_pw_solid.movement == 'blocking' and "
        "(_pw_solid.metadata.plane == _pw_caster.metadata.plane or "
        "(_pw_solid.metadata.plane == null and _pw_caster.metadata.plane == 'material'))"
    )
    unchanged_original = (
        "_pw_original.active == false and "
        "_pw_original.volume in _pw_expected_volumes and "
        "_pw_original.metadata in _pw_expected_metadata_rows and "
        "_pw_original.movement == 'passable' and _pw_original.visibility == 'clear'"
    )
    unchanged_fragment = (
        "_pw_retiring_fragment.active == true and "
        "_pw_retiring_fragment.volume in _pw_expected_volumes and "
        "_pw_retiring_fragment.metadata in _pw_expected_metadata_rows and "
        "_pw_retiring_fragment.movement == _pw_fragment.movement and "
        "_pw_retiring_fragment.visibility == _pw_fragment.visibility"
    )
    same_plane_body = (
        "_pw_body.active == true and (_pw_body.metadata.plane == _pw_source.plane or "
        "(_pw_body.metadata.plane == null and _pw_source.plane == 'material'))"
    )
    body_overlap = " and ".join(
        [
            *[
                f"_pw_body.position[{i}] - _pw_body.footprint.half_extents[{i}] "
                f"< _pw_source.open_max[{i}] and "
                f"_pw_body.position[{i}] + _pw_body.footprint.half_extents[{i}] "
                f"> _pw_source.open_min[{i}]"
                for i in range(2)
            ],
            "_pw_body.position[2] < _pw_source.open_max[2] and "
            "_pw_body.position[2] + _pw_body.height > _pw_source.open_min[2]",
        ]
    )
    source_expired = "state.lasting_tick >= _pw_clock_source.expires_tick"
    validate = _tool_scene(
        "Qualify Passwall's actual visible thirty-foot wooden, plaster or stone surface.",
        [
            _entity("caster_state"),
            _optional("targets", "array", None, items={"type": "string"}),
            *_choices(),
        ],
        [
            _require(
                "targets == null or len(targets) == 0", "Passwall selects a surface, not targets."
            ),
            _require(
                "width > 0 and height > 0 and depth > 0",
                "Choose positive opening dimensions within five by eight by twenty feet.",
            ),
            _call(
                "qualify_srd_demiplane_surface",
                {
                    "caster_state": "{_caster_state_id}",
                    "surface_id": "{surface_id}",
                    "point": "{point}",
                    "face": "{face}",
                },
                ["_demiplane_surface", "_demiplane_caster", "_demiplane_surface_distance"],
            ),
            _set("_pw_surface", "_demiplane_surface"),
            _set("_pw_caster", "_demiplane_caster"),
            _require(
                "_demiplane_surface_distance <= 30",
                "The actual Passwall surface is beyond thirty feet.",
            ),
            _require(
                "_pw_surface.metadata.material == 'wood' or "
                "_pw_surface.metadata.material == 'plaster' or "
                "_pw_surface.metadata.material == 'stone'",
                "Passwall needs an actual wooden, plaster or stone surface.",
            ),
            *_opening_bounds(),
            _set("_pw_solids", "state.spatial_scene.geometry"),
            _loop(
                "_pw_solids",
                "_pw_solid",
                [
                    _when(
                        selected_solid,
                        [
                            _require(
                                "_pw_solid.volume.kind == 'box'",
                                "Composite-surface clearance currently needs admitted boxes.",
                            ),
                            _require(
                                "not ("
                                + " and ".join(
                                    f"_pw_open_min[{i}] < _pw_solid.volume.max[{i}] and "
                                    f"_pw_open_max[{i}] > _pw_solid.volume.min[{i}]"
                                    for i in range(3)
                                )
                                + ")",
                                "Composite overlapping source walls remain unsupported.",
                            ),
                        ],
                    ),
                ],
            ),
            _set("_pw_next_serial", 1),
            _when(
                "state.passwall_serial != null",
                [_set("_pw_next_serial", "state.passwall_serial + 1")],
            ),
            *[
                _call(
                    "qualify_srd_demiplane_creation_identity",
                    {
                        "identity": "passwall:{_pw_next_serial}:" + segment,
                    },
                )
                for segment in SEGMENTS
            ],
        ],
    )

    segments = []
    # Disjoint axis slabs reconstruct the original solid without the removed box.
    for index, segment in enumerate(SEGMENTS):
        axis = index // 2
        lower = [
            f"_pw_source.original.volume.min[{i}]" if i >= axis else f"_pw_source.open_min[{i}]"
            for i in range(3)
        ]
        upper = [
            f"_pw_source.original.volume.max[{i}]" if i >= axis else f"_pw_source.open_max[{i}]"
            for i in range(3)
        ]
        if index % 2 == 0:
            upper[axis] = f"_pw_source.open_min[{axis}]"
        else:
            lower[axis] = f"_pw_source.open_max[{axis}]"
        segments.append(
            _when(
                f"segment == '{segment}'",
                [
                    _set("_pw_fragment_min", lower),
                    _set("_pw_fragment_max", upper),
                ],
            )
        )
    fragment = _tool_scene(
        "Create one real remaining solid slab from the registered source's exact removed box.",
        [
            _parameter("source_id"),
            _parameter("fragment_id"),
            _parameter("segment", "enum", enum_values=SEGMENTS),
        ],
        [
            *_lookup_source(),
            _require("_pw_source.active == true", "The actual source has already ended."),
            *segments,
            _set("_pw_created_fragment", None),
            _when(
                " and ".join(f"_pw_fragment_max[{i}] > _pw_fragment_min[{i}]" for i in range(3)),
                [
                    _set(
                        "_pw_fragment_extent",
                        [f"_pw_fragment_max[{i}] - _pw_fragment_min[{i}]" for i in range(3)],
                    ),
                    _set(
                        "_world_origin",
                        [
                            "(_pw_fragment_min[0] + _pw_fragment_max[0]) * 0.5",
                            "(_pw_fragment_min[1] + _pw_fragment_max[1]) * 0.5",
                            "_pw_fragment_min[2]",
                        ],
                    ),
                    _set(
                        "_pw_fragment_tag",
                        {
                            "passwall_source_id": "source_id",
                            "source_ref": "'passwall'",
                            "plane": "_pw_source.plane",
                            "magical_effect": True,
                        },
                    ),
                    _set(
                        "_pw_fragment_metadata",
                        "merge(_pw_source.original.metadata, _pw_fragment_tag)",
                    ),
                    *_new_scene_object(
                        "_pw_fragment_metadata",
                        identity="fragment_id",
                        extent="_pw_fragment_extent",
                        scene_kind="geometry",
                        movement="_pw_source.original.movement",
                        visibility="_pw_source.original.visibility",
                    ),
                    _set("_pw_created_fragment", "_world_created_geometry"),
                ],
            ),
        ],
    )

    install = _tool_scene(
        "Save one actual Passwall source, replace its selected solid by real remaining slabs.",
        [
            _entity("caster_state"),
            _parameter("slot_level", "integer", minimum=5, maximum=9),
            *_choices(),
        ],
        [
            _call(
                "validate_srd_passwall_cast",
                {
                    "caster_state": "{_caster_state_id}",
                    "surface_id": "{surface_id}",
                    "point": "{point}",
                    "face": "{face}",
                    "width": "{width}",
                    "height": "{height}",
                    "depth": "{depth}",
                },
                ["_pw_surface", "_pw_caster", "_pw_open_min", "_pw_open_max", "_pw_next_serial"],
            ),
            _when("state.lasting_tick == null", [_set("state.lasting_tick", 0)]),
            _when("state.passwall_sources == null", [_set("state.passwall_sources", [])]),
            _set("state.passwall_serial", "_pw_next_serial"),
            _call(
                "create_srd_passwall_source",
                {
                    "caster_state": "{_caster_state_id}",
                    "source_id": "passwall:{state.passwall_serial}",
                    "surface_id": "{surface_id}",
                    "point": "{point}",
                    "face": "{face}",
                    "slot_level": "{slot_level}",
                    "open_min": "{_pw_open_min}",
                    "open_max": "{_pw_open_max}",
                },
                ["_pw_installed_id"],
            ),
            _set("caster_state.last_passwall_source", "_pw_installed_id"),
        ],
    )
    create_source = _tool_scene(
        "Record the actual paid surface and exact physical opening before source slab creation.",
        [
            _entity("caster_state"),
            _parameter("source_id"),
            _parameter("surface_id"),
            _array("point"),
            _parameter("face", "enum", enum_values=FACES),
            _parameter("slot_level", "integer"),
            _array("open_min"),
            _array("open_max"),
        ],
        [
            *_geometry_lookup("surface_id", "_pw_original"),
            _set(
                "_pw_source",
                {
                    "id": "source_id",
                    "caster": "_caster_state_id",
                    "spell": "'passwall'",
                    "surface_id": "surface_id",
                    "face": "face",
                    "point": "point",
                    "original": "_pw_original",
                    "open_min": "open_min",
                    "open_max": "open_max",
                    "slot_level": "slot_level",
                    "created_tick": "state.lasting_tick",
                    "expires_tick": "state.lasting_tick + 1200",
                    "active": True,
                    "fragments": [],
                    "plane": "'material'",
                },
            ),
            _when(
                "_pw_original.metadata.plane != null",
                [
                    _set("_pw_plane_patch", {"plane": "_pw_original.metadata.plane"}),
                    _set("_pw_source", "merge(_pw_source, _pw_plane_patch)"),
                ],
            ),
            _set("_pw_new_sources", "state.passwall_sources"),
            _push("_pw_new_sources", "_pw_source"),
            _set("state.passwall_sources", "_pw_new_sources"),
            _set("state.has_passwall_sources", True),
            _set("_pw_tag", {"passwall_source_id": "source_id"}),
            _set("_pw_open_metadata", "merge(_pw_original.metadata, _pw_tag)"),
            *_mutate_geometry(
                "{surface_id}",
                {
                    "active": False,
                    "movement": "passable",
                    "visibility": "clear",
                    "metadata": "{_pw_open_metadata}",
                },
            ),
            _set("_pw_new_fragments", []),
            *[
                effect
                for segment in SEGMENTS
                for effect in [
                    _call(
                        "create_srd_passwall_fragment",
                        {
                            "source_id": "{source_id}",
                            "fragment_id": "{source_id}:" + segment,
                            "segment": segment,
                        },
                        ["_pw_created_fragment"],
                    ),
                    _when(
                        "_pw_created_fragment != null",
                        [_push("_pw_new_fragments", "_pw_created_fragment")],
                    ),
                ]
            ],
            _set("_pw_fragment_patch", {"fragments": "_pw_new_fragments"}),
            _set("_pw_source", "merge(_pw_source, _pw_fragment_patch)"),
            _set("_pw_source_retained", []),
            _set("_pw_current_sources", "state.passwall_sources"),
            _loop(
                "_pw_current_sources",
                "_pw_current_source",
                [
                    {
                        "condition": "_pw_current_source.id == source_id",
                        "on_true": [_push("_pw_source_retained", "_pw_source")],
                        "on_false": [_push("_pw_source_retained", "_pw_current_source")],
                    },
                ],
            ),
            _set("state.passwall_sources", "_pw_source_retained"),
            _set("_pw_installed_id", "source_id"),
        ],
    )

    end = _tool_scene(
        "Restore the exact source surface and safely eject its occupants nearest that face.",
        [_parameter("source_id")],
        [
            *_lookup_source(),
            _when(
                "_pw_source.active == true",
                [
                    *_geometry_lookup("_pw_source.surface_id", "_pw_original"),
                    _set("_pw_expected_tag", {"passwall_source_id": "source_id"}),
                    _set(
                        "_pw_expected_metadata",
                        "merge(_pw_source.original.metadata, _pw_expected_tag)",
                    ),
                    _set("_pw_expected_volumes", ["_pw_source.original.volume"]),
                    _set("_pw_expected_metadata_rows", ["_pw_expected_metadata"]),
                    _require(
                        unchanged_original,
                        "Later source surface changes need owning reconciliation.",
                    ),
                    _set("_pw_fragments", "_pw_source.fragments"),
                    _loop(
                        "_pw_fragments",
                        "_pw_fragment",
                        [
                            *_geometry_lookup("_pw_fragment.id", "_pw_retiring_fragment"),
                            _set("_pw_expected_volumes", ["_pw_fragment.volume"]),
                            _set("_pw_expected_metadata_rows", ["_pw_fragment.metadata"]),
                            _require(
                                unchanged_fragment,
                                "Later source slab changes need owning reconciliation.",
                            ),
                        ],
                    ),
                    *_mutate_geometry(
                        "{_pw_source.surface_id}",
                        {
                            "active": "{_pw_source.original.active}",
                            "movement": "{_pw_source.original.movement}",
                            "visibility": "{_pw_source.original.visibility}",
                            "metadata": "{_pw_source.original.metadata}",
                        },
                    ),
                    _loop(
                        "_pw_fragments",
                        "_pw_fragment",
                        [
                            *_mutate_geometry(
                                "{_pw_fragment.id}",
                                {"active": False, "movement": "passable", "visibility": "clear"},
                            ),
                        ],
                    ),
                    _set("_pw_bodies", "state.spatial_scene.entities"),
                    _loop(
                        "_pw_bodies",
                        "_pw_body",
                        [
                            _when(
                                same_plane_body,
                                [
                                    _require(
                                        "_pw_body.footprint.kind == 'box'",
                                        "Exact ejection currently needs actual box bodies.",
                                    ),
                                    _when(
                                        body_overlap,
                                        [
                                            _set("_travel_excluded", []),
                                            *_placement(
                                                "{_pw_body.id}",
                                                "{_pw_body.position}",
                                                "{_pw_source.plane}",
                                                reference=(
                                                    "geometry",
                                                    "{_pw_source.surface_id}",
                                                    "{_pw_source.face}",
                                                ),
                                            ),
                                            *_move(
                                                "{_pw_body.id}",
                                                "{_travel_placement.position}",
                                                "{_pw_source.plane}",
                                                False,
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                    _set("_pw_end_patch", {"active": False}),
                    _set("_pw_source", "merge(_pw_source, _pw_end_patch)"),
                    _set("_pw_source_retained", []),
                    _set("_pw_current_sources", "state.passwall_sources"),
                    _loop(
                        "_pw_current_sources",
                        "_pw_current_source",
                        [
                            {
                                "condition": "_pw_current_source.id == source_id",
                                "on_true": [_push("_pw_source_retained", "_pw_source")],
                                "on_false": [_push("_pw_source_retained", "_pw_current_source")],
                            },
                        ],
                    ),
                    _set("state.passwall_sources", "_pw_source_retained"),
                ],
            ),
        ],
    )
    refresh = _tool_scene(
        "End actual expired Passwall sources once through their physical return owner.",
        [],
        [
            _set("state.has_passwall_sources", False),
            _when(
                "state.passwall_sources != null",
                [
                    _set("_pw_clock_sources", "state.passwall_sources"),
                    _loop(
                        "_pw_clock_sources",
                        "_pw_clock_source",
                        [
                            _when(
                                "_pw_clock_source.active == true",
                                [
                                    {
                                        "condition": source_expired,
                                        "on_true": [
                                            _call(
                                                "end_srd_passwall_source",
                                                {"source_id": "{_pw_clock_source.id}"},
                                            )
                                        ],
                                        "on_false": [_set("state.has_passwall_sources", True)],
                                    },
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    return {
        "validate_srd_passwall_cast": validate,
        "install_srd_passwall": install,
        "create_srd_passwall_source": create_source,
        "create_srd_passwall_fragment": fragment,
        "end_srd_passwall_source": end,
        "refresh_srd_passwall_sources": refresh,
    }


def bind_passwall_lifecycle(tools, changed):
    """Attach the source lifetime to the existing shared clock after other travel owners."""
    clock = deepcopy(changed.get("advance_srd_lasting_clock", tools["advance_srd_lasting_clock"]))
    if "refresh_srd_passwall_sources" not in str(clock["effects"]):
        clock["effects"].append(
            _when(
                "state.has_passwall_sources == true",
                [
                    _call("refresh_srd_passwall_sources", {}),
                ],
            )
        )
    changed["advance_srd_lasting_clock"] = clock
