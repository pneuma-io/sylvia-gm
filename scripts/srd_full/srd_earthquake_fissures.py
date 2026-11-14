"""Open source-owned cardinal fissures over an exactly represented deep Box floor.

The complete admitted map footprint must lie inside the printed circle. Its curved
boundary is outside the scene, so each represented ten-foot strip is an exact Box
aperture. Remaining slabs use the existing Passwall subtraction recipe; no new
position, terrain, time or cast authority is introduced.
"""

from copy import deepcopy

if __package__:
    from .srd_control_behaviors import _walk, _when
    from .srd_earthquake import FAMILY_GUARD, MAX_TARGETS, SOURCE_SHA, _source
    from .srd_scene_relations import POSITION_EXPORTS, _array, _snapshot, _tool_scene
    from .srd_spell_object_content import _new_scene_object
    from .srd_spell_travel import _move, _placement
    from .srd_spell_travel_passwall import SEGMENTS
    from .srd_timed_actor_defenses import _call, _entity, _load_runtime, _parameter, _require, _set
    from .srd_timed_senses import _loop, _optional, _push, _replace_tools
else:
    from srd_control_behaviors import _walk, _when
    from srd_earthquake import FAMILY_GUARD, MAX_TARGETS, SOURCE_SHA, _source
    from srd_scene_relations import POSITION_EXPORTS, _array, _snapshot, _tool_scene
    from srd_spell_object_content import _new_scene_object
    from srd_spell_travel import _move, _placement
    from srd_spell_travel_passwall import SEGMENTS
    from srd_timed_actor_defenses import _call, _entity, _load_runtime, _parameter, _require, _set
    from srd_timed_senses import _loop, _optional, _push, _replace_tools

PUBLIC_TOOLS = set()
CAST_CHOICES = [
    _optional("earthquake_fissure_axis", "enum", "x", enum_values=["x", "y"]),
    _optional(
        "earthquake_fissure_offsets",
        "array",
        [],
        items={"type": "number"},
        min_items=0,
        max_items=6,
    ),
    _optional(
        "earthquake_fissure_edges",
        "array",
        [],
        items={"type": "string"},
        min_items=0,
        max_items=6,
    ),
]


def _represented_floor():
    """Prove exact represented coverage and a real bottom for every possible d10 depth."""
    return [
        _require(
            "combat.encounter_active == true and combat.turn_active == true and "
            "combat.active_actor == _caster_state_id and caster_state.turns_started != null",
            "Fissures require the creating caster's real combat turn end.",
        ),
        *[
            _require(
                f"_quake_floor.volume.{edge}[{axis}] == "
                f"state.spatial_scene.map.bounds.{edge}[{axis}]",
                "The single admitted floor must cover the entire represented map footprint.",
            )
            for edge in ("min", "max")
            for axis in range(2)
        ],
        *[
            _require(
                f"(_quake_floor.volume.{x}[0] - point[0]) ** 2 + "
                f"(_quake_floor.volume.{y}[1] - point[1]) ** 2 <= 10000",
                "Every actual map corner must lie within the printed Earthquake circle.",
            )
            for x in ("min", "max")
            for y in ("min", "max")
        ],
        _require(
            "_quake_floor.volume.min[2] < point[2] - 100 and "
            "state.spatial_scene.map.bounds.min[2] <= _quake_floor.volume.min[2]",
            "The actual admitted floor must extend below every possible hundred-foot depth.",
        ),
    ]


def _floor_lookup():
    return [
        _set("_fissure_floor", None),
        _set("_fissure_geometry", "state.spatial_scene.geometry"),
        _loop(
            "_fissure_geometry",
            "_fissure_piece",
            [_when("_fissure_piece.id == ground_id", [_set("_fissure_floor", "_fissure_piece")])],
        ),
        _require(
            "_fissure_floor != null and _fissure_floor.active == true and "
            "_fissure_floor.volume.kind == 'box' and _fissure_floor.metadata.ground == true",
            "Select the unchanged actual deep floor before opening its fissures.",
        ),
    ]


def _plan():
    """Validate all six contingent GM locations before any random count or depth is drawn."""
    overlap = (
        "_fissure_obstacle.volume.min[_fissure_perp] < _fissure_offset + 5 and "
        "_fissure_obstacle.volume.max[_fissure_perp] > _fissure_offset - 5"
    )
    return _tool_scene(
        "Validate six legal predeclared parallel fissures against actual floor, bodies and "
        "structures.",
        [
            _entity("caster_state"),
            _array("point"),
            _parameter("plane"),
            _parameter("ground_id"),
            _parameter("axis", "enum", enum_values=["x", "y"]),
            _parameter("offsets", "array", items={"type": "number"}, min_items=6, max_items=6),
            _parameter("edges", "array", items={"type": "string"}, min_items=6, max_items=6),
        ],
        _floor_lookup()
        + [_set("_quake_floor", "_fissure_floor")]
        + _represented_floor()[1:]
        + [
            _require(
                "_fissure_floor.metadata.plane == plane and "
                "_fissure_floor.volume.max[2] == point[2]",
                "The actual represented floor changed plane or surface height before opening.",
            ),
            _set("_fissure_perp", 1),
            _when("axis == 'y'", [_set("_fissure_perp", 0)]),
            _set("_fissure_previous", None),
            _set("_fissure_plan_index", 0),
            _call(
                "collect_srd_earthquake_targets",
                {"point": "{point}", "plane": "{plane}", "ground_id": "{ground_id}"},
                ("_quake_targets",),
            ),
            _set("_fissure_plan_bodies", []),
            _loop(
                "_quake_targets",
                "_fissure_target",
                [
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_fissure_target}"},
                        POSITION_EXPORTS,
                    ),
                    _push(
                        "_fissure_plan_bodies",
                        {"target": "_fissure_target", "body": "_info_scene_entity"},
                    ),
                ],
            ),
            _loop(
                "offsets",
                "_fissure_offset",
                [
                    _require(
                        "edges[_fissure_plan_index] == 'negative' or "
                        "edges[_fissure_plan_index] == 'positive'",
                        "The GM must choose an actual opening edge for each contingent strip.",
                    ),
                    _set("_fissure_selected_edge", "edges[_fissure_plan_index]"),
                    _require(
                        "_fissure_previous == null or _fissure_offset - _fissure_previous >= 20",
                        "This bounded plan needs six increasing parallel strips at least twenty "
                        "feet apart.",
                    ),
                    _require(
                        "_fissure_offset - 5 > _fissure_floor.volume.min[_fissure_perp] and "
                        "_fissure_offset + 5 < _fissure_floor.volume.max[_fissure_perp]",
                        "Both opening edges must remain inside the represented map.",
                    ),
                    _loop(
                        "_fissure_geometry",
                        "_fissure_obstacle",
                        [
                            _when(
                                "_fissure_obstacle.active == true and _fissure_obstacle.id != "
                                "ground_id "
                                "and _fissure_obstacle.metadata.plane == plane",
                                [
                                    _require(
                                        "_fissure_obstacle.volume.kind == 'box'",
                                        "Additional curved solids need their actual aperture "
                                        "consumer.",
                                    ),
                                    _when(
                                        overlap,
                                        [
                                            _require(
                                                "_fissure_obstacle.metadata.structure != true and "
                                                "_fissure_obstacle.metadata.structure_id == null",
                                                "A fissure cannot be placed under an actual "
                                                "structure.",
                                            ),
                                            _require(
                                                "_fissure_obstacle.movement != 'blocking' or "
                                                "_fissure_obstacle.volume.min[2] >= point[2] or "
                                                "_fissure_obstacle.volume.max[2] <= point[2] - 100",
                                                "The potential aperture intersects another "
                                                "actual underground solid.",
                                            ),
                                        ],
                                    ),
                                ],
                            )
                        ],
                    ),
                    _set("_fissure_entities", "state.spatial_scene.entities"),
                    _loop(
                        "_fissure_entities",
                        "_fissure_entity",
                        [
                            _when(
                                "_fissure_entity.active == true and "
                                "_fissure_entity.metadata.plane == plane "
                                "and _fissure_entity.metadata.structure == true",
                                [
                                    _require(
                                        "_fissure_entity.footprint.kind == 'box'",
                                        "A curved structure needs genuine strip-intersection "
                                        "qualification.",
                                    ),
                                    _require(
                                        "_fissure_entity.position[_fissure_perp] + "
                                        "_fissure_entity.footprint.half_extents[_fissure_perp] <= "
                                        "_fissure_offset - 5 or "
                                        "_fissure_entity.position[_fissure_perp] - "
                                        "_fissure_entity.footprint.half_extents[_fissure_perp] "
                                        ">= _fissure_offset + 5",
                                        "The legal GM plan cannot place fissures under actual "
                                        "structures.",
                                    ),
                                ],
                            )
                        ],
                    ),
                    _loop(
                        "_fissure_plan_bodies",
                        "_fissure_plan_body",
                        [
                            _call(
                                "qualify_srd_earthquake_fissure_plan_body",
                                {
                                    "target_state": "{_fissure_plan_body.target}",
                                    "candidate": "{_fissure_plan_body.body}",
                                    "offset": "{_fissure_offset}",
                                    "perp": "{_fissure_perp}",
                                    "edge": "{_fissure_selected_edge}",
                                },
                                ("_fissure_body_inside",),
                            ),
                            _when(
                                "_fissure_body_inside == true",
                                [
                                    _call(
                                        "validate_srd_charmer_harm",
                                        {
                                            "actor_state": "{_caster_state_id}",
                                            "target_state": "{_fissure_plan_body.target}",
                                        },
                                    )
                                ],
                            ),
                        ],
                    ),
                    _set("_fissure_previous", "_fissure_offset"),
                    _set("_fissure_plan_index", "_fissure_plan_index + 1"),
                ],
            ),
        ],
    )


def _body():
    return _tool_scene(
        "Qualify this actual grounded Box body and its chosen clear opening edge.",
        [
            _entity("target_state"),
            _parameter("offset", "number"),
            _parameter("perp", "integer", minimum=0, maximum=1),
            _parameter("edge", "enum", enum_values=["negative", "positive"]),
        ],
        [
            _call(
                "srd_information_scene_entity", {"subject": "{_target_state_id}"}, POSITION_EXPORTS
            ),
            _set("_fissure_body", "_info_scene_entity"),
            _set("_fissure_body_inside", False),
            _set("_fissure_edge_position", None),
            _require(
                "_fissure_body.footprint.kind == 'box'", "Fissures require an actual Box body."
            ),
            _when(
                "_fissure_body.position[perp] + _fissure_body.footprint.half_extents[perp] > "
                "offset - 5 "
                "and _fissure_body.position[perp] - _fissure_body.footprint.half_extents[perp] < "
                "offset + 5",
                [
                    _require(
                        "_fissure_body.position[perp] - "
                        "_fissure_body.footprint.half_extents[perp] >= "
                        "offset - 5 and _fissure_body.position[perp] + "
                        "_fissure_body.footprint.half_extents[perp] <= offset + 5",
                        "Boundary-straddling bodies need their actual opening-edge geometry.",
                    ),
                    _require(
                        "target_state.magic_jar_source_id == null and "
                        "target_state.form_statistics_replaced "
                        "!= true and (target_state.current_body_source_id == null or "
                        "target_state.current_body_source_id == _target_state_id)",
                        "Fissure saves and falls require the actual native physical receiving "
                        "body.",
                    ),
                    _require(
                        "target_state.creature_kind == 'character' or "
                        "target_state.saving_throw_modifiers.dexterity != null",
                        "Use the actual creature's Dexterity saving throw statistics.",
                    ),
                    _set(
                        "_fissure_edge_coordinate",
                        "offset - 5 - _fissure_body.footprint.half_extents[perp]",
                    ),
                    _when(
                        "edge == 'positive'",
                        [
                            _set(
                                "_fissure_edge_coordinate",
                                "offset + 5 + _fissure_body.footprint.half_extents[perp]",
                            )
                        ],
                    ),
                    _when(
                        "perp == 0",
                        [
                            _set(
                                "_fissure_edge_position",
                                [
                                    "_fissure_edge_coordinate",
                                    "_fissure_body.position[1]",
                                    "_fissure_body.position[2]",
                                ],
                            )
                        ],
                        [
                            _set(
                                "_fissure_edge_position",
                                [
                                    "_fissure_body.position[0]",
                                    "_fissure_edge_coordinate",
                                    "_fissure_body.position[2]",
                                ],
                            )
                        ],
                    ),
                    *_placement(
                        "{_target_state_id}",
                        "{_fissure_edge_position}",
                        "{_fissure_body.metadata.plane}",
                        exclusions=[],
                    ),
                    *[
                        _require(
                            f"_travel_placement.position[{axis}] == _fissure_edge_position[{axis}]",
                            "The chosen opening edge must be an actual clear placement, without "
                            "a guessed fallback.",
                        )
                        for axis in range(3)
                    ],
                    _set("_fissure_body_inside", True),
                ],
            ),
        ],
    )


def _plan_body():
    """Reuse one actual body read across the pure strip tests in this plan pass."""
    tool = deepcopy(_body())
    tool["parameters"].append(
        _parameter("candidate", "object", from_state="state.spatial_scene")
    )
    tool["effects"][:2] = [
        _require(
            "candidate.active == true and (candidate.id == _target_state_id or "
            "candidate.metadata.participant_ref == _target_state_id)",
            "Reuse only this target's actual admitted scene body during the current plan.",
        ),
        _set("_fissure_body", "candidate"),
    ]
    return tool


def _fragment():
    """Apply the existing six disjoint slab recipe to this actual saved physical cut."""
    segments = []
    for index, segment in enumerate(SEGMENTS):
        axis = index // 2
        lower = [
            f"_fissure_cut.original.volume.min[{i}]" if i >= axis else f"_fissure_cut.open_min[{i}]"
            for i in range(3)
        ]
        upper = [
            f"_fissure_cut.original.volume.max[{i}]" if i >= axis else f"_fissure_cut.open_max[{i}]"
            for i in range(3)
        ]
        if index % 2:
            lower[axis] = f"_fissure_cut.open_max[{axis}]"
        else:
            upper[axis] = f"_fissure_cut.open_min[{axis}]"
        segments.append(
            _when(
                f"segment == '{segment}'",
                [_set("_fissure_fragment_min", lower), _set("_fissure_fragment_max", upper)],
            )
        )
    return _tool_scene(
        "Create a genuine remaining floor slab from the saved exact fissure aperture.",
        [
            _parameter("area_id"),
            _parameter("fragment_id"),
            _parameter("segment", "enum", enum_values=SEGMENTS),
        ],
        _source()
        + [
            _set("_fissure_cut", "_quake_source.fissure_current_cut"),
            _require(
                "_fissure_cut != null and _quake_source.event_in_progress == true",
                "A remaining slab belongs to this actual end-turn physical cut.",
            ),
            *segments,
            _set("_fissure_created_fragment", None),
            _when(
                " and ".join(
                    f"_fissure_fragment_max[{i}] > _fissure_fragment_min[{i}]" for i in range(3)
                ),
                [
                    _set(
                        "_fissure_fragment_extent",
                        [
                            f"_fissure_fragment_max[{i}] - _fissure_fragment_min[{i}]"
                            for i in range(3)
                        ],
                    ),
                    _set(
                        "_world_origin",
                        [
                            "(_fissure_fragment_min[0] + _fissure_fragment_max[0]) * 0.5",
                            "(_fissure_fragment_min[1] + _fissure_fragment_max[1]) * 0.5",
                            "_fissure_fragment_min[2]",
                        ],
                    ),
                    _set(
                        "_fissure_fragment_tag",
                        {
                            "earthquake_floor_origin": "_quake_source.ground_id",
                            "earthquake_source_id": "area_id",
                            "fissure_index": "_fissure_cut.index",
                            "plane": "_quake_source.plane",
                            "magical_effect": False,
                        },
                    ),
                    _set(
                        "_fissure_fragment_metadata",
                        "merge(_fissure_cut.original.metadata, _fissure_fragment_tag)",
                    ),
                    *_new_scene_object(
                        "_fissure_fragment_metadata",
                        identity="fragment_id",
                        extent="_fissure_fragment_extent",
                        scene_kind="geometry",
                        movement="_fissure_cut.original.movement",
                        visibility="_fissure_cut.original.visibility",
                    ),
                    _set("_fissure_created_fragment", "_world_created_geometry"),
                ],
            ),
        ],
    )


def _cut():
    return _tool_scene(
        "Subtract one exact cardinal ten-foot Box strip using its genuinely rolled depth.",
        [_parameter("area_id"), _parameter("index", "integer", minimum=0, maximum=5)],
        _source()
        + [
            _set("_fissure_current_floor", None),
            _set("_fissure_geometry", "state.spatial_scene.geometry"),
            _loop(
                "_fissure_geometry",
                "_fissure_piece",
                [
                    _when(
                        "_fissure_piece.id == _quake_source.fissure_remaining_floor",
                        [_set("_fissure_current_floor", "_fissure_piece")],
                    )
                ],
            ),
            _require(
                "_fissure_current_floor != null and _fissure_current_floor.active == true and "
                "_fissure_current_floor.volume.kind == 'box'",
                "The next exact remaining floor slab must still exist.",
            ),
            _set("_fissure_perp", 1),
            _when("_quake_source.fissure_axis == 'y'", [_set("_fissure_perp", 0)]),
            _set("_fissure_offset", "_quake_source.fissure_offsets[index]"),
            _when(
                "_fissure_perp == 0",
                [
                    _set(
                        "_fissure_open_min",
                        [
                            "_fissure_offset - 5",
                            "_fissure_current_floor.volume.min[1]",
                            "_quake_source.origin[2] - _quake_source.fissure_depths[index]",
                        ],
                    ),
                    _set(
                        "_fissure_open_max",
                        [
                            "_fissure_offset + 5",
                            "_fissure_current_floor.volume.max[1]",
                            "_quake_source.origin[2]",
                        ],
                    ),
                ],
                [
                    _set(
                        "_fissure_open_min",
                        [
                            "_fissure_current_floor.volume.min[0]",
                            "_fissure_offset - 5",
                            "_quake_source.origin[2] - _quake_source.fissure_depths[index]",
                        ],
                    ),
                    _set(
                        "_fissure_open_max",
                        [
                            "_fissure_current_floor.volume.max[0]",
                            "_fissure_offset + 5",
                            "_quake_source.origin[2]",
                        ],
                    ),
                ],
            ),
            _set(
                "state.persistent_spell_areas.{area_id}.fissure_current_cut",
                {
                    "original": "_fissure_current_floor",
                    "index": "index",
                    "open_min": "_fissure_open_min",
                    "open_max": "_fissure_open_max",
                },
            ),
            *_snapshot(),
            {
                "spatial_mutation": {
                    "scene": "state.spatial_scene",
                    "map_id": "{state.spatial_scene.map.id}",
                    "expected_revision": "{_info_snapshot.geometry_revision}",
                    "target": {"kind": "geometry", "id": "{_fissure_current_floor.id}"},
                    "patch": {"active": False, "movement": "passable", "visibility": "clear"},
                },
                "store_as": "_fissure_cut_mutation",
            },
            _set("_fissure_fragments", "_quake_source.fissure_floor_ids"),
            _set("_fissure_next_floor", None),
            _set("_fissure_bottom_floor", None),
            *[
                _when(
                    "_fissure_perp == 0"
                    if segment in {"west", "east"}
                    else "_fissure_perp == 1"
                    if segment in {"south", "north"}
                    else "true",
                    [
                        _call(
                            "format_srd_information_projection",
                            {"message": "{area_id}:fissure:{index}:" + segment},
                            ("_info_projection_message",),
                        ),
                        _call(
                            "create_srd_earthquake_fissure_fragment",
                            {
                                "area_id": "{area_id}",
                                "fragment_id": "{_info_projection_message}",
                                "segment": segment,
                            },
                            ("_fissure_created_fragment",),
                        ),
                        _when(
                            "_fissure_created_fragment != null",
                            [
                                _push("_fissure_fragments", "_fissure_created_fragment.id"),
                                *(
                                    [
                                        _when(
                                            "_fissure_perp == 1",
                                            [
                                                _set(
                                                    "_fissure_next_floor",
                                                    "_fissure_created_fragment.id",
                                                )
                                            ],
                                        )
                                    ]
                                    if segment == "north"
                                    else []
                                ),
                                *(
                                    [
                                        _when(
                                            "_fissure_perp == 0",
                                            [
                                                _set(
                                                    "_fissure_next_floor",
                                                    "_fissure_created_fragment.id",
                                                )
                                            ],
                                        )
                                    ]
                                    if segment == "east"
                                    else []
                                ),
                                *(
                                    [_set("_fissure_bottom_floor", "_fissure_created_fragment.id")]
                                    if segment == "floor"
                                    else []
                                ),
                            ],
                        ),
                    ],
                )
                for segment in ("west", "east", "south", "north", "floor")
            ],
            _require(
                "_fissure_next_floor != null",
                "The actual next increasing strip needs its remaining upper floor.",
            ),
            _set("state.persistent_spell_areas.{area_id}.fissure_floor_ids", "_fissure_fragments"),
            _set(
                "state.persistent_spell_areas.{area_id}.fissure_remaining_floor",
                "_fissure_next_floor",
            ),
            _require(
                "_fissure_bottom_floor != null", "The genuine rolled depth needs its real bottom."
            ),
            _call(
                "install_srd_earthquake_fissure_bottom_zone",
                {"area_id": "{area_id}", "floor_id": "{_fissure_bottom_floor}"},
            ),
        ],
    )


def _bottom_zone():
    """Continue the source's Difficult Terrain on the actual newly lowered ground face."""
    return _tool_scene(
        "Admit ongoing Earthquake terrain on the actual physical bottom without creating ground.",
        [_parameter("area_id"), _parameter("floor_id")],
        _source()
        + [
            _set("_fissure_bottom", None),
            _set("_fissure_geometry", "state.spatial_scene.geometry"),
            _loop(
                "_fissure_geometry",
                "_fissure_piece",
                [
                    _when(
                        "_fissure_piece.id == floor_id", [_set("_fissure_bottom", "_fissure_piece")]
                    )
                ],
            ),
            _require(
                "_fissure_bottom != null and _fissure_bottom.active == true and "
                "_fissure_bottom.metadata.earthquake_source_id == area_id and "
                "floor_id in _quake_source.fissure_floor_ids",
                "This source must already own the independently admitted physical bottom slab.",
            ),
            _when(
                "_quake_source.active == true",
                [
                    _call(
                        "format_srd_information_projection",
                        {"message": "{floor_id}:quake-terrain"},
                        ("_info_projection_message",),
                    ),
                    _set(
                        "_fissure_bottom_zone",
                        {
                            "id": "_info_projection_message",
                            "map_id": "state.spatial_scene.map.id",
                            "shape": {
                                "kind": "'box'",
                                "min": [
                                    "_fissure_bottom.volume.min[0]",
                                    "_fissure_bottom.volume.min[1]",
                                    "_fissure_bottom.volume.max[2]",
                                ],
                                "max": [
                                    "_fissure_bottom.volume.max[0]",
                                    "_fissure_bottom.volume.max[1]",
                                    "_fissure_bottom.volume.max[2] + 0.000001",
                                ],
                            },
                            "metadata": {
                                "source_environment_id": "area_id",
                                "source_spell": "'earthquake'",
                                "plane": "_quake_source.plane",
                                "difficult": True,
                                "ground_environment": True,
                                "magical_effect": True,
                            },
                        },
                    ),
                    _set("_fissure_zones", "state.spatial_scene.zones"),
                    _push("_fissure_zones", "_fissure_bottom_zone"),
                    _set("state.spatial_scene.zones", "_fissure_zones"),
                    {"increment": {"field": "state.spatial_scene.map.revision", "amount": 1}},
                    *_snapshot(),
                ],
            ),
        ],
    )


def _save():
    params = {
        "actor_state": "{_target_state_id}",
        "ability": "dexterity",
        "kind": "saving_throw",
        "proficiency_factor": "none",
        "target_number": "{_quake_source.save_dc}",
        "save_avoids_condition": "none",
        "circumstantial_modifier": 0,
    }
    exports = ("_outcome", "_d20_test", "_adjusted_total")
    return _tool_scene(
        "Resolve actual fissure Dexterity; successful bodies move with the chosen opening edge.",
        [
            _entity("target_state"),
            _parameter("area_id"),
            _parameter("index", "integer", minimum=0, maximum=5),
        ],
        _source()
        + [
            _require(
                "_quake_source.event_in_progress == true",
                "The real caster-end event owns fissure saves.",
            ),
            _set("_fissure_perp", 1),
            _when("_quake_source.fissure_axis == 'y'", [_set("_fissure_perp", 0)]),
            _set("_fissure_selected_offset", "_quake_source.fissure_offsets[index]"),
            _set("_fissure_selected_edge", "_quake_source.fissure_edges[index]"),
            _call(
                "qualify_srd_earthquake_fissure_body",
                {
                    "target_state": "{_target_state_id}",
                    "offset": "{_fissure_selected_offset}",
                    "perp": "{_fissure_perp}",
                    "edge": "{_fissure_selected_edge}",
                },
                ("_fissure_body_inside", "_fissure_body", "_fissure_edge_position"),
            ),
            _set("_fissure_save_row", None),
            _when(
                "_fissure_body_inside == true",
                [
                    _when(
                        "target_state.creature_kind == 'character'",
                        [_call("d20_test", params | {"actor": "{_target_state_id}"}, exports)],
                        [
                            _call(
                                "d20_test",
                                params
                                | {
                                    "ability_modifier": (
                                        "{target_state.saving_throw_modifiers.dexterity}"
                                    ),
                                    "proficiency_bonus": 0,
                                },
                                exports,
                            )
                        ],
                    ),
                    _set(
                        "_fissure_save_row",
                        {
                            "target": "_target_state_id",
                            "body_id": "_fissure_body.id",
                            "index": "index",
                            "roll": "_d20_test.total",
                            "total": "_adjusted_total",
                            "dc": "_quake_source.save_dc",
                            "outcome": "_outcome",
                            "edge_position": "_fissure_edge_position",
                        },
                    ),
                    _when(
                        "_outcome == 'success'",
                        [
                            *_move(
                                "{_target_state_id}",
                                "{_fissure_edge_position}",
                                "{_quake_source.plane}",
                                teleport=False,
                            )
                        ],
                    ),
                ],
            ),
        ],
    )


def _open():
    """Commit count/depth only at the original caster turn end, never on later tremors."""
    return _tool_scene(
        "Open the actual saved GM plan once at the casting turn end, with real count and depth "
        "dice.",
        [_parameter("area_id"), _parameter("event_turn", "integer")],
        _source()
        + [
            _when(
                "_quake_source.fissures == true and _quake_source.fissures_opened != true and "
                "event_turn == _quake_source.fissure_casting_turn and _quake_source.active == true",
                [
                    _require(
                        "_quake_source.event_in_progress == true",
                        "Use the actual end-turn tremor receipt.",
                    ),
                    _call(
                        "validate_srd_earthquake_fissure_plan",
                        {
                            "caster_state": "{_quake_source.caster}",
                            "point": "{_quake_source.origin}",
                            "plane": "{_quake_source.plane}",
                            "ground_id": "{_quake_source.ground_id}",
                            "axis": "{_quake_source.fissure_axis}",
                            "offsets": "{_quake_source.fissure_offsets}",
                            "edges": "{_quake_source.fissure_edges}",
                        },
                        ("_quake_targets",),
                    ),
                    {"dice": {"expression": "1d6"}, "store_as": "_fissure_count_roll"},
                    _set("_fissure_depths", []),
                    _set("_fissure_depth_rolls", []),
                    _set("_fissure_open_offsets", "_quake_source.fissure_offsets"),
                    _set("_fissure_index", 0),
                    _loop(
                        "_fissure_open_offsets",
                        "_fissure_offset",
                        [
                            _when(
                                "_fissure_index < _fissure_count_roll.total",
                                [
                                    {
                                        "dice": {"expression": "1d10"},
                                        "store_as": "_fissure_depth_roll",
                                    },
                                    _push("_fissure_depths", "_fissure_depth_roll.total * 10"),
                                    _push("_fissure_depth_rolls", "_fissure_depth_roll"),
                                ],
                            ),
                            _set("_fissure_index", "_fissure_index + 1"),
                        ],
                    ),
                    _set("state.persistent_spell_areas.{area_id}.fissures_opened", True),
                    _set(
                        "state.persistent_spell_areas.{area_id}.fissure_count_roll",
                        "_fissure_count_roll",
                    ),
                    _set(
                        "state.persistent_spell_areas.{area_id}.fissure_depth_rolls",
                        "_fissure_depth_rolls",
                    ),
                    _set(
                        "state.persistent_spell_areas.{area_id}.fissure_depths", "_fissure_depths"
                    ),
                    _set(
                        "state.persistent_spell_areas.{area_id}.fissure_opened_turn", "event_turn"
                    ),
                    *_source(),
                    _set("_fissure_index", 0),
                    _set("_fissure_save_rows", []),
                    _loop(
                        "_fissure_depths",
                        "_fissure_depth",
                        [
                            _loop(
                                "_quake_targets",
                                "_fissure_target",
                                [
                                    _call(
                                        "resolve_srd_earthquake_fissure_save",
                                        {
                                            "target_state": "{_fissure_target}",
                                            "area_id": "{area_id}",
                                            "index": "{_fissure_index}",
                                        },
                                        ("_fissure_save_row",),
                                    ),
                                    _when(
                                        "_fissure_save_row != null",
                                        [_push("_fissure_save_rows", "_fissure_save_row")],
                                    ),
                                ],
                            ),
                            _call(
                                "cut_srd_earthquake_fissure_floor",
                                {"area_id": "{area_id}", "index": "{_fissure_index}"},
                            ),
                            _set("_fissure_index", "_fissure_index + 1"),
                        ],
                    ),
                    _set(
                        "state.persistent_spell_areas.{area_id}.fissure_save_rows",
                        "_fissure_save_rows",
                    ),
                    _set("_fissure_effects", "state.turn_effects"),
                    _loop(
                        "_fissure_save_rows",
                        "_fissure_save_row",
                        [
                            _when(
                                "_fissure_save_row.outcome == 'failure'",
                                [
                                    _call(
                                        "format_srd_information_projection",
                                        {
                                            "message": (
                                                "{area_id}:fissure:{_fissure_save_row.index}:"
                                                "{_fissure_save_row.target}:fall"
                                            )
                                        },
                                        ("_info_projection_message",),
                                    ),
                                    _push(
                                        "_fissure_effects",
                                        {
                                            "kind": "'earthquake_fissure_fall'",
                                            "spell": "'earthquake'",
                                            "source_sha256": repr(SOURCE_SHA),
                                            "source_id": "_info_projection_message",
                                            "area_id": "area_id",
                                            "caster": "_quake_source.caster",
                                            "cast_id": "_quake_source.cast_id",
                                            "target": "_fissure_save_row.target",
                                            "owner": "_fissure_save_row.target",
                                            "body_id": "_fissure_save_row.body_id",
                                            "phase": "'never'",
                                            "turn": 0,
                                            "active": True,
                                            "ongoing_duration": False,
                                            "dispel_ongoing": False,
                                        },
                                    ),
                                    _set("state.has_earthquake_fissure_falls", True),
                                ],
                            ),
                        ],
                    ),
                    _set("state.turn_effects", "_fissure_effects"),
                ],
            ),
        ],
    )


def _support():
    """Read actual remaining floor ancestry, including physical slabs after spell ending."""
    return _tool_scene(
        "Qualify the actual source-created floor slab against its exact retained physical parent.",
        [_parameter("ground_id"), _parameter("original_ground_id")],
        [
            _set("_fissure_support_owned", "ground_id == original_ground_id"),
            _when(
                "_fissure_support_owned != true",
                [
                    _set("_fissure_support_geometry", "state.spatial_scene.geometry"),
                    _loop(
                        "_fissure_support_geometry",
                        "_fissure_support",
                        [
                            _when(
                                "_fissure_support.id == ground_id and _fissure_support.active == "
                                "true and "
                                "_fissure_support.metadata.ground == true and "
                                "_fissure_support.metadata.earthquake_floor_origin == "
                                "original_ground_id",
                                [
                                    _set(
                                        "_fissure_support_source_id",
                                        "_fissure_support.metadata.earthquake_source_id",
                                    ),
                                    _require(
                                        "_fissure_support_source_id != null and "
                                        "state.persistent_spell_areas != null "
                                        "and _fissure_support_source_id in "
                                        "state.persistent_spell_areas",
                                        "A source-created floor needs its actual retained "
                                        "physical source.",
                                    ),
                                    _set(
                                        "_fissure_support_source",
                                        "state.persistent_spell_areas[_fissure_support_source_id]",
                                    ),
                                    _require(
                                        f"_fissure_support_source.profile == 'earthquake' and "
                                        f"_fissure_support_source.source_sha256 == "
                                        f"'{SOURCE_SHA}' and "
                                        "_fissure_support_source.ground_id == original_ground_id "
                                        "and "
                                        "ground_id in _fissure_support_source.fissure_floor_ids",
                                        "Use only this actual source's independently retained "
                                        "floor slabs.",
                                    ),
                                    _set("_fissure_support_owned", True),
                                ],
                            )
                        ],
                    ),
                ],
            ),
        ],
    )


def _handoff():
    """Allocate a genuine fall only from completed outer actions, outside raw retirement."""
    return _tool_scene(
        "Hand earned fissure falls to the existing real scene-fall Reaction owner once.",
        [],
        [
            _when(
                "state.has_earthquake_fissure_falls == true and "
                "(state.pending_srd_action_stack == null or len(state.pending_srd_action_stack) "
                "== 0)",
                [
                    _set("_fissure_effects", "state.turn_effects"),
                    _set("_fissure_fall_targets", []),
                    _set("_fissure_fall_receipts", []),
                    _set("_fissure_retained_effects", []),
                    _loop(
                        "_fissure_effects",
                        "_fissure_effect",
                        [
                            _when(
                                f"_fissure_effect.kind == 'earthquake_fissure_fall' and "
                                f"_fissure_effect.source_sha256 == '{SOURCE_SHA}' and "
                                f"_fissure_effect.active == true",
                                [
                                    _require(
                                        f"len(_fissure_fall_targets) < {MAX_TARGETS} and "
                                        "_fissure_effect.target not in _fissure_fall_targets",
                                        "Use the source's bounded, unambiguous actual falling "
                                        "bodies.",
                                    ),
                                    _push("_fissure_fall_targets", "_fissure_effect.target"),
                                    _push("_fissure_fall_receipts", "_fissure_effect"),
                                ],
                                [_push("_fissure_retained_effects", "_fissure_effect")],
                            )
                        ],
                    ),
                    _when(
                        "len(_fissure_fall_targets) > 0",
                        [
                            _call(
                                "begin_srd_scene_fall",
                                {
                                    "target_states": "{_fissure_fall_targets}",
                                    "fall_cause": "support_lost",
                                },
                                ("_pending_action_id",),
                            ),
                            _require(
                                "state.pending_srd_actions[_pending_action_id].owner_kind == "
                                "'scene_fall' and "
                                "state.pending_srd_actions[_pending_action_id].status == 'open'",
                                "Retain earned source receipts only after the actual fall "
                                "allocator accepts them.",
                            ),
                            _require(
                                "combat.encounter_active == true and "
                                "combat.turn_active != true and "
                                "state.pending_srd_actions[_pending_action_id].turn_actor == "
                                "combat.active_actor",
                                "Hand this earned fall off only after its actual turn closes.",
                            ),
                            _set(
                                "state.pending_srd_actions.{_pending_action_id}.context."
                                "earthquake_fissure_completed_turn",
                                {
                                    "actor": "combat.active_actor",
                                    "sequence": "combat.turn_sequence",
                                },
                            ),
                            _set(
                                "state.pending_srd_actions.{_pending_action_id}.turn_actor", None
                            ),
                            _set(
                                "state.pending_srd_actions.{_pending_action_id}.context.earthquake_fissure_receipts",
                                "_fissure_fall_receipts",
                            ),
                        ],
                    ),
                    _set("state.turn_effects", "_fissure_retained_effects"),
                    _set("state.has_earthquake_fissure_falls", False),
                ],
            )
        ],
    )


def extend_runtime(text):
    """Preserve ordinary admission; add the bounded physical mode before cast capture."""
    tools = _load_runtime(text)["tools"]
    cast = deepcopy(tools["cast_spell"])
    names = {p["name"] for p in cast["parameters"]}
    cast["parameters"].extend(deepcopy(p) for p in CAST_CHOICES if p["name"] not in names)
    blocks = [
        n
        for n in _walk(cast["effects"])
        if n.get("condition") == FAMILY_GUARD + " and _spell_cast_failed != true"
    ]
    owned_leaf = "open_srd_earthquake_fissures"
    if (
        len(blocks) == 1
        and owned_leaf in tools
        and (
            blocks[0]["on_true"][0].get("require", {}).get("condition")
            != "earthquake_fissures != true"
        )
    ):
        owned_calls = [
            n
            for n in _walk(tools["resolve_srd_earthquake_event"]["effects"])
            if n.get("call") == owned_leaf
        ]
        paid_calls = [n for n in _walk(cast["effects"]) if n.get("call") == "cast_srd_earthquake"]
        if (
            len(owned_calls) != 1
            or len(paid_calls) != 1
            or (paid_calls[0]["params"].get("fissures") != "{earthquake_fissures}")
        ):
            raise ValueError("The existing actual fissure producer contract changed")
        return text
    if (
        len(blocks) != 1
        or blocks[0]["on_true"][0].get("require", {}).get("condition")
        != "earthquake_fissures != true"
    ):
        raise ValueError("Fissures require the actual settled structure-enabled admission prefix")
    blocks[0]["on_true"].pop(0)
    geometry_calls = [
        n
        for n in _walk(blocks[0]["on_true"])
        if n.get("call") == "validate_srd_earthquake_geometry"
    ]
    if len(geometry_calls) != 1:
        raise ValueError("Fissures require the unique actual paid ground admission")
    geometry_calls[0]["params"]["fissures"] = "{earthquake_fissures}"
    geometry_calls[0].setdefault("export_context", {}).update(
        _quake_plane="_quake_plane", _quake_ground_id="_quake_ground_id"
    )
    blocks[0]["on_true"].append(
        _when(
            "earthquake_fissures == true",
            [
                _require(
                    "len(earthquake_fissure_offsets) == 6 and len(earthquake_fissure_edges) == 6",
                    "Predeclare all six contingent GM locations and opening edges.",
                ),
                _call(
                    "validate_srd_earthquake_fissure_plan",
                    {
                        "caster_state": "{_caster_state_id}",
                        "point": "{earthquake_point}",
                        "plane": "{_quake_plane}",
                        "ground_id": "{_quake_ground_id}",
                        "axis": "{earthquake_fissure_axis}",
                        "offsets": "{earthquake_fissure_offsets}",
                        "edges": "{earthquake_fissure_edges}",
                    },
                ),
            ],
        )
    )
    install_calls = [n for n in _walk(cast["effects"]) if n.get("call") == "cast_srd_earthquake"]
    if len(install_calls) != 1:
        raise ValueError("Fissures require the unique genuine committed installer")
    install_calls[0]["params"].update(
        fissures="{earthquake_fissures}",
        fissure_axis="{earthquake_fissure_axis}",
        fissure_offsets="{earthquake_fissure_offsets}",
        fissure_edges="{earthquake_fissure_edges}",
    )
    geometry = deepcopy(tools["validate_srd_earthquake_geometry"])
    geometry["parameters"].append(_optional("fissures", "boolean", False))
    fit = [
        e
        for e in geometry["effects"]
        if e.get("require", {}).get("error")
        == "The full Earthquake disk must fit on the actual admitted flat floor."
    ]
    if len(fit) != 2:
        raise ValueError("The original whole-disk admission floor proof changed")
    first = geometry["effects"].index(fit[0])
    geometry["effects"] = [e for e in geometry["effects"] if e not in fit]
    geometry["effects"].insert(first, _when("fissures == true", _represented_floor(), fit))
    install = deepcopy(tools["cast_srd_earthquake"])
    install["parameters"].extend(
        [
            _optional("fissures", "boolean", False),
            _optional("fissure_axis", "enum", "x", enum_values=["x", "y"]),
            _optional(
                "fissure_offsets", "array", [], items={"type": "number"}, min_items=0, max_items=6
            ),
            _optional(
                "fissure_edges", "array", [], items={"type": "string"}, min_items=0, max_items=6
            ),
        ]
    )
    checks = [
        n for n in _walk(install["effects"]) if n.get("call") == "validate_srd_earthquake_geometry"
    ]
    if len(checks) != 1:
        raise ValueError("The actual committed floor check changed")
    checks[0]["params"]["fissures"] = "{fissures}"
    records = [
        n["set"]["value"]
        for n in _walk(install["effects"])
        if n.get("set", {}).get("field") == "lasting_state.persistent_spell_areas.{_quake_id}"
    ]
    if len(records) != 1:
        raise ValueError("The actual retained Earthquake source changed")
    records[0].update(
        fissures="fissures == true",
        fissure_axis="fissure_axis",
        fissure_offsets="fissure_offsets",
        fissure_edges="fissure_edges",
        fissure_casting_turn="caster_state.turns_started",
        fissures_opened=False,
        fissure_floor_ids=[],
        fissure_remaining_floor="_quake_ground_id",
    )
    zone_calls = [
        n
        for n in _walk(install["effects"])
        if n.get("call") == "install_srd_environment_ground_zone"
    ]
    if len(zone_calls) != 1 or zone_calls[0] not in install["effects"]:
        raise ValueError("The actual initial ground-zone installation changed")
    zone_at = install["effects"].index(zone_calls[0])
    install["effects"][zone_at] = _when(
        "fissures == true",
        [_call("install_srd_earthquake_represented_ground_zone", {"area_id": "{_quake_id}"})],
        [zone_calls[0]],
    )
    represented_zone = deepcopy(tools["install_srd_environment_ground_zone"])
    zone_records = [
        n["set"]["value"]
        for n in _walk(represented_zone["effects"])
        if n.get("set", {}).get("field") == "_terrain_zone" and isinstance(n["set"]["value"], dict)
    ]
    if not zone_records:
        raise ValueError("The actual source-owned ground-zone receiver changed")
    for zone in zone_records:
        zone["shape"] = {
            "kind": "'box'",
            "min": [
                "state.spatial_scene.map.bounds.min[0]",
                "state.spatial_scene.map.bounds.min[1]",
                "_terrain_source.origin[2]",
            ],
            "max": [
                "state.spatial_scene.map.bounds.max[0]",
                "state.spatial_scene.map.bounds.max[1]",
                "_terrain_source.origin[2] + 0.000001",
            ],
        }
    event = deepcopy(tools["resolve_srd_earthquake_event"])
    owners = [
        n
        for n in _walk(event["effects"])
        if n.get("condition") == "event_turn != _quake_source.last_end_turn"
    ]
    if len(owners) != 1:
        raise ValueError("Fissures require the unique saved caster-end event receipt")
    endings = [
        i
        for i, n in enumerate(owners[0]["on_true"])
        if n.get("set", {}).get("field")
        == "lasting_state.persistent_spell_areas.{area_id}.event_in_progress"
        and n["set"]["value"] is False
    ]
    if len(endings) != 1:
        raise ValueError("The actual end-turn event ownership changed")
    owners[0]["on_true"].insert(
        endings[0],
        _call(
            "open_srd_earthquake_fissures", {"area_id": "{area_id}", "event_turn": "{event_turn}"}
        ),
    )
    body = deepcopy(tools["qualify_srd_earthquake_body"])
    conditions = [
        n
        for n in _walk(body["effects"])
        if "_quake_body_ground == ground_id" in n.get("condition", "")
    ]
    if len(conditions) != 1:
        raise ValueError("The actual supporting-face target qualifier changed")
    conditions[0]["condition"] = conditions[0]["condition"].replace(
        "_quake_body_ground == ground_id", "_fissure_support_owned == true"
    )
    locate = [
        n for n in _walk(body["effects"]) if n.get("call") == "query_srd_earthquake_body_ground"
    ]
    if len(locate) != 1:
        raise ValueError("The actual body support query changed")
    branches = [
        n for n in _walk(body["effects"]) if n.get("condition") == "target_state.is_alive == true"
    ]
    if len(branches) != 1:
        raise ValueError("The actual living-body support owner changed")
    at = branches[0]["on_true"].index(locate[0])
    branches[0]["on_true"].insert(
        at + 1,
        _call(
            "qualify_srd_earthquake_fissure_support",
            {"ground_id": "{_quake_body_ground}", "original_ground_id": "{ground_id}"},
            ("_fissure_support_owned",),
        ),
    )
    return _replace_tools(
        text,
        {
            "cast_spell": cast,
            "cast_srd_earthquake": install,
            "validate_srd_earthquake_geometry": geometry,
            "resolve_srd_earthquake_event": event,
            "validate_srd_earthquake_fissure_plan": _plan(),
            "qualify_srd_earthquake_fissure_body": _body(),
            "qualify_srd_earthquake_fissure_plan_body": _plan_body(),
            "create_srd_earthquake_fissure_fragment": _fragment(),
            "cut_srd_earthquake_fissure_floor": _cut(),
            "resolve_srd_earthquake_fissure_save": _save(),
            "open_srd_earthquake_fissures": _open(),
            "install_srd_earthquake_fissure_bottom_zone": _bottom_zone(),
            "install_srd_earthquake_represented_ground_zone": represented_zone,
            "qualify_srd_earthquake_body": body,
            "qualify_srd_earthquake_fissure_support": _support(),
            "handoff_srd_earthquake_fissure_falls": _handoff(),
        },
    )


def bind_movement_consumers(text):
    """Retain current upper and lowered floor qualification after the ordinary late binder."""
    tools = _load_runtime(text)["tools"]
    tool = deepcopy(tools["qualify_srd_earthquake_route_ground"])
    bounds = [
        n
        for n in _walk(tool["effects"])
        if "abs(point[2] - _quake_source.origin[2])" in n.get("condition", "")
    ]
    if not bounds and any(
        "_quake_route_floor.id in _quake_source.fissure_floor_ids" in n.get("condition", "")
        for n in _walk(tool["effects"])
    ):
        return text
    if len(bounds) != 1:
        raise ValueError("The actual source's route-ground boundary changed")
    old = bounds[0]["condition"]
    suffix = " and abs(point[2] - _quake_source.origin[2]) < 0.000000001"
    if not old.endswith(suffix):
        raise ValueError("The actual original-ground route qualifier changed")
    bounds[0]["condition"] = old.removesuffix(suffix)
    floors = [
        n
        for n in _walk(tool["effects"])
        if n.get("condition", "").startswith(
            "_quake_route_floor.id == _quake_source.ground_id and "
        )
    ]
    if len(floors) != 1:
        raise ValueError("The actual supporting-floor route branch changed")
    floors[0]["condition"] = floors[0]["condition"].replace(
        "_quake_route_floor.id == _quake_source.ground_id",
        "(_quake_route_floor.id == _quake_source.ground_id or "
        "(_quake_source.fissure_floor_ids != null and "
        "_quake_route_floor.id in _quake_source.fissure_floor_ids))",
        1,
    )
    return _replace_tools(text, {"qualify_srd_earthquake_route_ground": tool})


def bind_completed_turn_consumer(text):
    """Hand support loss off after the actual end-turn owner completes all its effects."""
    tools = _load_runtime(text)["tools"]
    if "begin_srd_scene_fall" not in tools:
        raise ValueError("Fissure completion requires the actual scene-fall allocator")
    tool = deepcopy(tools["end_combat_turn"])
    owned = _when(
        "lasting_state.has_earthquake_fissure_falls == true",
        [_call("handoff_srd_earthquake_fissure_falls", {})],
    )
    existing = [
        effect for effect in tool["effects"] if effect.get("condition") == owned["condition"]
    ]
    if existing:
        if existing != [owned] or tool["effects"][-1] != owned:
            raise ValueError("The exact completed-turn fissure handoff changed")
        return text
    actual_ends = [
        node
        for node in _walk(tool["effects"])
        if node.get("call") == "process_srd_turn_effects"
        and node.get("params", {}).get("boundary_phase") == "end"
    ]
    closed_turn = [
        node
        for node in _walk(tool["effects"])
        if node.get("set", {}).get("field") == "combat.turn_active"
        and node["set"]["value"] is False
    ]
    if len(actual_ends) != 1 or len(closed_turn) != 1:
        raise ValueError("The actual completed combat-turn boundary changed")
    tool["effects"].append(owned)
    return _replace_tools(text, {"end_combat_turn": tool})
