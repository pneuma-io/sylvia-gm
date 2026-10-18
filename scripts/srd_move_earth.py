"""Reshape unoccupied admitted earth boxes through the existing source clock and scene."""

from copy import deepcopy

if __package__:
    from .srd_divination_spells import ACTIVE, PENDING
    from .srd_scene_relations import POSITION_EXPORTS, _snapshot, _spatial, _tool_scene
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _tool,
        _when,
    )
    from .srd_timed_senses import _loop, _optional, _public, _push, _replace_tools
else:
    from srd_divination_spells import ACTIVE, PENDING
    from srd_scene_relations import POSITION_EXPORTS, _snapshot, _spatial, _tool_scene
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _tool,
        _when,
    )
    from srd_timed_senses import _loop, _optional, _public, _push, _replace_tools

PROFILE = {
    "family": "move_earth",
    "base_level": 6,
    "casting_time": "action",
    "range_kind": "distance",
    "range_feet": 120,
    "concentration": True,
    "duration_rounds": 1200,
}
SOURCE = (
    "sylvia.srd-lite.bg3:spells:p151:187c299763fc8bbff873446caed1f78671ea222645d65bf3dd067d7cf25365ca"
)
PUBLIC_TOOLS = {
    "record_srd_earth_material",
    "read_srd_move_earth",
    "choose_srd_move_earth_area",
}
CAST_CHOICES = [
    _optional("earth_geometry_id", default=""),
    _optional("earth_elevation_change", "number", 0),
]
RESULTS = ("_spell_summary_rows", "_spell_detail_rows")


def schema():
    properties = {
        k: {
            "type": "boolean" if type(v) is bool else "integer" if type(v) is int else "string",
            "enum": [v],
        }
        for k, v in PROFILE.items()
        if k != "family"
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def validate_profiles(definitions):
    for spell, row in definitions.items():
        if row.get("family") == "move_earth" and (spell != "move_earth" or row != PROFILE):
            raise ValueError("Move Earth must retain its exact printed profile.")


def _geometry(geometry="geometry_id"):
    return [
        *_snapshot(),
        _set("_earth_geometry", None),
        _set("_earth_geometry_rows", "state.spatial_scene.geometry"),
        _loop(
            "_earth_geometry_rows",
            "_earth_geometry_row",
            [
                _when(
                    "_earth_geometry_row.id == " + geometry,
                    [
                        _set("_earth_geometry", "_earth_geometry_row"),
                    ],
                ),
            ],
        ),
        _require(
            "_earth_geometry != null and _earth_geometry.active == true",
            "Select actual active admitted terrain geometry.",
        ),
        _require(
            "_earth_geometry.volume.kind == 'box'",
            "This terrain reshaping supports a whole admitted Box only.",
        ),
    ]


def _earth_facts():
    return [
        _require(
            "_earth_geometry.metadata.natural_terrain == true and "
            "(_earth_geometry.metadata.material == 'dirt' or "
            "_earth_geometry.metadata.material == 'sand' or "
            "_earth_geometry.metadata.material == 'clay')",
            "Move Earth needs actual admitted dirt, sand or clay terrain.",
        ),
        _require(
            "_earth_geometry.metadata.structure != true and "
            "_earth_geometry.metadata.plants != true and "
            "_earth_geometry.metadata.plant_growth != true and "
            "_earth_geometry.metadata.rocks != true and "
            "_earth_geometry.metadata.magical_effect != true and "
            "_earth_geometry.metadata.source_environment_id == null and "
            "_earth_geometry.metadata.mirage_base_volume == null",
            "Plant, rock, structure and replacement-terrain movement remains unsupported.",
        ),
        _require("_earth_geometry.metadata.plane != null", "Record this actual terrain's plane."),
        _require(
            "_earth_geometry.movement == 'blocking'",
            "This supported box must be actual solid ground.",
        ),
    ]


def _mutation(patch):
    return [
        *_snapshot(),
        {
            "spatial_mutation": {
                "scene": "state.spatial_scene",
                "map_id": "{state.spatial_scene.map.id}",
                "expected_revision": "{_info_snapshot.geometry_revision}",
                "target": {"kind": "geometry", "id": "{geometry_id}"},
                "patch": patch,
            },
            "store_as": "_earth_mutation",
        },
    ]


def _material():
    return _public(
        _tool_scene(
            "Record actual natural dirt, sand or clay for one existing terrain identity.",
            [
                _parameter("geometry_id"),
                _parameter("material", "enum", enum_values=["dirt", "sand", "clay"]),
            ],
            _geometry()
            + [
                _require(
                    "_earth_geometry.metadata.natural_terrain != false and "
                    "_earth_geometry.metadata.structure != true and "
                    "_earth_geometry.metadata.magical_effect != true",
                    "Do not contradict a recorded nonnatural structure or magical effect.",
                ),
                _require(
                    "_earth_geometry.metadata.material == null",
                    "Actual terrain material is already recorded; never reclassify stone.",
                ),
                _set("_earth_material_patch", {"material": "material", "natural_terrain": True}),
                _set("_earth_metadata", "merge(_earth_geometry.metadata, _earth_material_patch)"),
                *_mutation({"metadata": "{_earth_metadata}"}),
            ],
        ),
        "exploration",
        "Terrain material",
        [("Material", "material")],
    )


def _clear():
    """Check the entire swept solid box; nearby supported bodies are never carried or hurt."""
    return _tool_scene(
        "Reject occupied, planted or constructed terrain before an actual earth mutation.",
        [
            _parameter("geometry_id"),
            _parameter("candidate", "object", from_state="state.turn_effects"),
            _parameter("plane"),
        ],
        [
            *_snapshot(),
            _spatial(
                "area",
                {
                    "map_id": "{state.spatial_scene.map.id}",
                    "shape": {
                        "kind": "box",
                        "min": "{candidate.min}",
                        "max": "{candidate.max}",
                    },
                    "include": ["geometry", "entities"],
                    "max_results": 4096,
                },
                "_earth_occupants",
            ),
            _require(
                "_earth_occupants.truncated != true",
                "Actual terrain occupancy results must be complete.",
            ),
            _set("_earth_occupant_rows", "_earth_occupants.results"),
            _loop(
                "_earth_occupant_rows",
                "_earth_occupant",
                [
                    _require(
                        "(_earth_occupant.kind == 'geometry' and "
                        "_earth_occupant.id == geometry_id) or "
                        "(_earth_occupant.metadata.plane != null and "
                        "_earth_occupant.metadata.plane != plane)",
                        "Occupied terrain or shifting plants, rocks and structures "
                        "remains unsupported; no trap or injury is applied.",
                    ),
                ],
            ),
        ],
    )


def _prepare():
    effects = (
        _geometry()
        + _earth_facts()
        + [
            _call(
                "srd_information_scene_entity", {"subject": "{_caster_state_id}"}, POSITION_EXPORTS
            ),
            _set("_earth_caster", "_info_scene_entity"),
            _require(
                "_earth_caster.footprint.kind == 'box' and "
                "_earth_caster.metadata.plane == _earth_geometry.metadata.plane",
                "The selected terrain and actual caster must share a supported plane/body.",
            ),
            _set("_earth_width", "_earth_geometry.volume.max[0] - _earth_geometry.volume.min[0]"),
            _set("_earth_length", "_earth_geometry.volume.max[1] - _earth_geometry.volume.min[1]"),
            _require(
                "_earth_width <= 40 and _earth_length <= 40",
                "Choose an actual whole area no larger than 40 feet on a side.",
            ),
            _require(
                "elevation_change != 0 and "
                "abs(elevation_change) <= max(_earth_width, _earth_length) * 0.5",
                "The actual change must not exceed half the area's largest side.",
            ),
            _set("_earth_new_top", "_earth_geometry.volume.max[2] + elevation_change"),
            _require(
                "_earth_new_top > _earth_geometry.volume.min[2] and "
                "_earth_new_top <= state.spatial_scene.map.bounds.max[2]",
                "Lowering must retain actual positive ground depth inside the admitted map.",
            ),
            _set(
                "_earth_candidate",
                {
                    "kind": "'box'",
                    "min": "_earth_geometry.volume.min",
                    "max": [
                        "_earth_geometry.volume.max[0]",
                        "_earth_geometry.volume.max[1]",
                        "_earth_new_top",
                    ],
                },
            ),
            _set(
                "_earth_swept",
                {
                    "kind": "'box'",
                    "min": "_earth_geometry.volume.min",
                    "max": [
                        "_earth_geometry.volume.max[0]",
                        "_earth_geometry.volume.max[1]",
                        "max(_earth_new_top, _earth_geometry.volume.max[2])",
                    ],
                },
            ),
        ]
    )
    for x in ("min", "max"):
        for y in ("min", "max"):
            effects += [
                _set(
                    "_earth_corner",
                    [
                        f"_earth_geometry.volume.{x}[0]",
                        f"_earth_geometry.volume.{y}[1]",
                        "_earth_geometry.volume.max[2]",
                    ],
                ),
                _set(
                    "_earth_nearest",
                    [
                        f"max(_earth_caster.position[{i}] - "
                        f"_earth_caster.footprint.half_extents[{i}], min(_earth_corner[{i}], "
                        f"_earth_caster.position[{i}] + _earth_caster.footprint.half_extents[{i}]))"
                        for i in (0, 1)
                    ]
                    + [
                        "max(_earth_caster.position[2], min(_earth_corner[2], "
                        "_earth_caster.position[2] + _earth_caster.height))"
                    ],
                ),
                _require(
                    "(_earth_corner[0] - _earth_nearest[0]) ** 2 + "
                    "(_earth_corner[1] - _earth_nearest[1]) ** 2 + "
                    "(_earth_corner[2] - _earth_nearest[2]) ** 2 <= 120 ** 2",
                    "This supported whole terrain area must be within 120 feet.",
                ),
            ]
    effects += [
        _set(
            "_earth_aim",
            [
                "max(_earth_geometry.volume.min[0], "
                "min(_earth_geometry.volume.max[0], _earth_caster.position[0]))",
                "max(_earth_geometry.volume.min[1], "
                "min(_earth_geometry.volume.max[1], _earth_caster.position[1]))",
                "_earth_geometry.volume.max[2]",
            ],
        ),
        _call(
            "srd_information_barriers",
            {
                "start": "{_earth_caster.position}",
                "end": "{_earth_aim}",
                "rule": "solid",
                "plane": "{_earth_geometry.metadata.plane}",
            },
            ("_information_blocked",),
        ),
        _require("_information_blocked != true", "A solid barrier blocks the actual earth area."),
        _call(
            "check_srd_move_earth_clear",
            {
                "geometry_id": "{geometry_id}",
                "candidate": "{_earth_swept}",
                "plane": "{_earth_geometry.metadata.plane}",
            },
        ),
    ]
    return _tool_scene(
        "Prepare a whole source-qualified earth box and real elevation intent.",
        [
            _entity("caster_state"),
            _parameter("geometry_id"),
            _parameter("elevation_change", "number"),
        ],
        effects,
    )


def _pending(geometry="geometry_id"):
    return {
        "geometry_id": geometry,
        "original_volume": "_earth_geometry.volume",
        "candidate_volume": "_earth_candidate",
        "swept_volume": "_earth_swept",
        "plane": "_earth_geometry.metadata.plane",
        "material": "_earth_geometry.metadata.material",
        "due_tick": "state.lasting_tick + 200",
    }


def _complete():
    return _tool_scene(
        "Apply one earned ten-minute earth change only after fresh terrain/body checks.",
        [_parameter("source_id"), _parameter("geometry_id")],
        [
            _set("_earth_record", "state.move_earth_sources[source_id]"),
            _set("_earth_work", "_earth_record.pending"),
            _require(
                "_earth_record.installed == true and _earth_work != null and "
                "_earth_work.geometry_id == geometry_id and "
                "state.lasting_tick >= _earth_work.due_tick and "
                "_earth_work.due_tick <= _earth_record.expires_tick",
                "Only authentic completed source work can reshape the terrain.",
            ),
            *_geometry(),
            *_earth_facts(),
            _require(
                " and ".join(
                    f"_earth_geometry.volume.{side}[{axis}] == "
                    f"_earth_work.original_volume.{side}[{axis}]"
                    for side in ("min", "max")
                    for axis in range(3)
                ),
                "The actual source terrain changed while this work was pending.",
            ),
            _require(
                "_earth_geometry.metadata.plane == _earth_work.plane and "
                "_earth_geometry.metadata.material == _earth_work.material",
                "The actual source terrain changed while this work was pending.",
            ),
            _call(
                "check_srd_move_earth_clear",
                {
                    "geometry_id": "{geometry_id}",
                    "candidate": "{_earth_work.swept_volume}",
                    "plane": "{_earth_work.plane}",
                },
            ),
            *_mutation({"volume": "{_earth_work.candidate_volume}"}),
            _set("state.move_earth_sources.{source_id}.pending", None),
            _set("state.move_earth_sources.{source_id}.last_completed_tick", "state.lasting_tick"),
            _set(
                "state.move_earth_sources.{source_id}.completed_changes",
                "_earth_record.completed_changes + 1",
            ),
        ],
    )


def _retire():
    return _tool(
        "Retire the exact earth source; completed physical changes are not rewound.",
        [_entity("caster_state"), _parameter("source_id")],
        [
            _set("_earth_retiring", "state.move_earth_sources[source_id]"),
            _require(
                "_earth_retiring.caster_state_id == _caster_state_id",
                "Retire only this actual caster's earth source.",
            ),
            _set("state.move_earth_sources.{source_id}.installed", False),
            _set("state.move_earth_sources.{source_id}.pending", None),
        ],
    )


def _refresh():
    check = _tool(
        "Complete authentic ten-minute work before retiring its elapsed source.",
        [_entity("caster_state"), _parameter("source_id")],
        [
            _set("_earth_live_record", "state.move_earth_sources[source_id]"),
            _when(
                "_earth_live_record.installed == true",
                [
                    {
                        "condition": "caster_state.concentration_spell_id != 'move_earth' or "
                        "caster_state.concentration_cast_id != source_id or "
                        "caster_state.is_alive != true",
                        "on_true": [
                            _call(
                                "retire_srd_move_earth",
                                {"caster_state": "{_caster_state_id}", "source_id": "{source_id}"},
                            ),
                        ],
                        "on_false": [
                            _when(
                                "_earth_live_record.pending != null and "
                                "state.lasting_tick >= _earth_live_record.pending.due_tick and "
                                "_earth_live_record.pending.due_tick <= "
                                "_earth_live_record.expires_tick",
                                [
                                    _call(
                                        "complete_srd_move_earth_change",
                                        {
                                            "source_id": "{source_id}",
                                            "geometry_id": (
                                                "{_earth_live_record.pending.geometry_id}"
                                            ),
                                        },
                                    ),
                                ],
                            ),
                            _when(
                                "state.lasting_tick >= _earth_live_record.expires_tick",
                                [
                                    _call(
                                        "end_srd_concentration_node",
                                        {
                                            "source_state": "{_caster_state_id}",
                                            "target_state": "{_caster_state_id}",
                                        },
                                    ),
                                ],
                            ),
                        ],
                    },
                ],
            ),
        ],
    )
    all_sources = _tool(
        "Refresh only installed authentic Move Earth sources.",
        [],
        [
            _set("_earth_refresh_ids", "state.move_earth_source_ids"),
            _loop(
                "_earth_refresh_ids",
                "_earth_refresh_id",
                [
                    _set("_earth_refresh_record", "state.move_earth_sources[_earth_refresh_id]"),
                    _when(
                        "_earth_refresh_record.installed == true",
                        [
                            _call(
                                "refresh_srd_move_earth_source",
                                {
                                    "source_id": "{_earth_refresh_id}",
                                    "caster_state": "{_earth_refresh_record.caster_state_id}",
                                },
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    return {"refresh_srd_move_earth_source": check, "refresh_srd_move_earth": all_sources}


def _read():
    return _tool(
        "Read the exact actual caster's active earth source and pending work.",
        [_entity("caster_state")],
        [
            _set("_earth_source_id", "caster_state.concentration_cast_id"),
            _require(
                "caster_state.concentration_spell_id == 'move_earth' and _earth_source_id != null",
                "This caster has no current Move Earth source.",
            ),
            _call(
                "refresh_srd_move_earth_source",
                {
                    "caster_state": "{_caster_state_id}",
                    "source_id": "{_earth_source_id}",
                },
            ),
            _set("_earth_record", "state.move_earth_sources[_earth_source_id]"),
            _require(
                "_earth_record.installed == true and "
                "_earth_record.caster_state_id == _caster_state_id",
                "Read only this actual installed earth source.",
            ),
        ],
    )


def _cast():
    return _tool_scene(
        "Install the actual paid two-hour source and first ten-minute work.",
        [
            _entity("caster_state"),
            _parameter("spell_cast_id"),
            _parameter("slot_level", "integer"),
            _parameter("concentration_modifier", "integer"),
            *deepcopy(CAST_CHOICES),
        ],
        [
            _require(
                "spell_cast_id != '' and "
                "caster_state.last_spell_source_cast_id == spell_cast_id and "
                "caster_state.last_spell_source_spell == 'move_earth'",
                "Move Earth requires its authentic paid casting source.",
            ),
            _call(
                "prepare_srd_move_earth_change",
                {
                    "caster_state": "{_caster_state_id}",
                    "geometry_id": "{earth_geometry_id}",
                    "elevation_change": "{earth_elevation_change}",
                },
                ("_earth_geometry", "_earth_candidate", "_earth_swept"),
            ),
            _when(
                "caster_state.concentration_spell_id != null",
                [
                    _call(
                        "end_srd_concentration_node",
                        {
                            "source_state": "{_caster_state_id}",
                            "target_state": "{caster_state.concentration_target_state_id}",
                        },
                    ),
                ],
            ),
            _when(
                "state.move_earth_source_ids == null",
                [
                    _set("state.move_earth_source_ids", []),
                    _set("state.move_earth_sources", {}),
                ],
            ),
            _set("_earth_cast_ids", "state.move_earth_source_ids"),
            _require("spell_cast_id not in _earth_cast_ids", "This source is already installed."),
            _push("_earth_cast_ids", "spell_cast_id"),
            _set("state.move_earth_source_ids", "_earth_cast_ids"),
            _set("_earth_first_work", _pending("earth_geometry_id")),
            _set(
                "state.move_earth_sources.{spell_cast_id}",
                {
                    "installed": True,
                    "cast_id": "spell_cast_id",
                    "spell_id": "'move_earth'",
                    "spell_level": "slot_level",
                    "caster_state_id": "_caster_state_id",
                    "source_caster_id": "_caster_state_id",
                    "started_tick": "state.lasting_tick",
                    "expires_tick": "state.lasting_tick + 2400",
                    "ongoing_duration": True,
                    "anchor_geometry_id": "earth_geometry_id",
                    "pending": "_earth_first_work",
                    "completed_changes": 0,
                    "last_completed_tick": None,
                },
            ),
            _set("state.has_move_earth", True),
            _set("caster_state.concentration_spell_id", "'move_earth'"),
            _set("caster_state.concentration_cast_id", "spell_cast_id"),
            _set("caster_state.concentration_effect_family", "'move_earth'"),
            _set("caster_state.concentration_target_state_id", "_caster_state_id"),
            _set("caster_state.concentration_target_state_ids", []),
            _set("caster_state.concentration_started_tick", "state.lasting_tick"),
            _set("caster_state.concentration_expires_tick", "state.lasting_tick + 2400"),
            _set("caster_state.concentration_save_modifier", "concentration_modifier"),
            _set(
                "_spell_summary_rows",
                [{"label": "'Move Earth'", "value": "'Actual terrain reshaping takes 10 minutes'"}],
            ),
            _set("_spell_detail_rows", []),
        ],
    )


def _choose():
    return _public(
        _tool_scene(
            "Choose new actual terrain at an earned ten-minute Concentration boundary.",
            [
                _entity("caster_state"),
                _parameter("geometry_id"),
                _parameter("elevation_change", "number"),
            ],
            [
                _require(PENDING, "Resolve the pending action before choosing new terrain."),
                _require(ACTIVE, "An Incapacitated caster cannot reshape another area."),
                _call(
                    "read_srd_move_earth_source",
                    {"caster_state": "{_caster_state_id}"},
                    ("_earth_source_id", "_earth_record"),
                ),
                _require(
                    "_earth_record.pending == null and "
                    "state.lasting_tick >= _earth_record.started_tick + 200 and "
                    "(state.lasting_tick - _earth_record.started_tick) % 200 == 0 and "
                    "state.lasting_tick + 200 <= _earth_record.expires_tick",
                    "Choose a new area only at a real ten-minute boundary with time to finish.",
                ),
                _call(
                    "prepare_srd_move_earth_change",
                    {
                        "caster_state": "{_caster_state_id}",
                        "geometry_id": "{geometry_id}",
                        "elevation_change": "{elevation_change}",
                    },
                    ("_earth_geometry", "_earth_candidate", "_earth_swept"),
                ),
                _set("state.move_earth_sources.{_earth_source_id}.pending", _pending()),
                _set(
                    "state.move_earth_sources.{_earth_source_id}.anchor_geometry_id", "geometry_id"
                ),
            ],
        ),
        "spells",
        "Move Earth",
        [("Terrain", "geometry_id")],
    )


def _dispel():
    """Select the retained shaping area, not already completed physical terrain changes."""
    return _tool_scene(
        "Dispel one actual ongoing earth-shaping source at its current chosen area.",
        [
            _entity("caster_state"),
            {"name": "caster", "type": "string", "required": False, "resolve": "participant"},
            _parameter("source_id"),
            _parameter("slot_level", "integer"),
            _parameter("casting_modifier", "integer"),
            _parameter("casting_ability"),
        ],
        [
            _set("_earth_selected_record", "state.move_earth_sources[source_id]"),
            _require(
                "_earth_selected_record != null and _earth_selected_record.installed == true "
                "and _earth_selected_record.spell_id == 'move_earth' and "
                "_earth_selected_record.cast_id == source_id",
                "Select an actual installed Move Earth casting source.",
            ),
            _call(
                "read_srd_move_earth_source",
                {"caster_state": "{_earth_selected_record.caster_state_id}"},
                ("_earth_record", "_earth_source_id"),
            ),
            _require(
                "_earth_source_id == source_id and _earth_record.ongoing_duration == true",
                "The selected source must be this caster's actual ongoing shaping spell.",
            ),
            _set("_earth_anchor_geometry_id", "_earth_record.anchor_geometry_id"),
            *_geometry("_earth_anchor_geometry_id"),
            _call(
                "srd_information_scene_entity",
                {"subject": "{_caster_state_id}"},
                POSITION_EXPORTS,
            ),
            _set("_earth_dispeller", "_info_scene_entity"),
            _require(
                "_earth_dispeller.metadata.plane == _earth_geometry.metadata.plane and "
                "_earth_geometry.metadata.plane != null",
                "The selected shaping area and actual dispeller must share a recorded plane.",
            ),
            _set(
                "_earth_dispel_point",
                [
                    "max(_earth_geometry.volume.min[0], "
                    "min(_earth_geometry.volume.max[0], _earth_dispeller.position[0]))",
                    "max(_earth_geometry.volume.min[1], "
                    "min(_earth_geometry.volume.max[1], _earth_dispeller.position[1]))",
                    "_earth_geometry.volume.max[2]",
                ],
            ),
            _call(
                "measure_srd_information_distance",
                {"start": "{_earth_dispeller.position}", "end": "{_earth_dispel_point}"},
                ("_scene_distance",),
            ),
            _require("_scene_distance <= 120", "The actual shaping effect is beyond Dispel range."),
            _call(
                "srd_information_barriers",
                {
                    "start": "{_earth_dispeller.position}",
                    "end": "{_earth_dispel_point}",
                    "plane": "{_earth_geometry.metadata.plane}",
                    "rule": "solid",
                },
                ("_information_blocked",),
            ),
            _require("_information_blocked != true", "A solid barrier blocks this Dispel path."),
            _call(
                "resolve_srd_dispel_source_check",
                {
                    "caster_state": "{_caster_state_id}",
                    "caster": "{_caster_id}",
                    "slot_level": "{slot_level}",
                    "source_level": "{_earth_record.spell_level}",
                    "casting_modifier": "{casting_modifier}",
                    "casting_ability": "{casting_ability}",
                },
                ("_dispel_succeeded", "_dispel_roll"),
            ),
            _set(
                "_earth_dispel_check",
                {
                    "spell": "'move_earth'",
                    "source_id": "source_id",
                    "ended": "_dispel_succeeded",
                    "roll": None,
                    "dc": "10 + _earth_record.spell_level",
                },
            ),
            _when(
                "_dispel_roll != null",
                [
                    _set("_earth_dispel_roll_patch", {"roll": "_dispel_roll"}),
                    _set(
                        "_earth_dispel_check",
                        "merge(_earth_dispel_check, _earth_dispel_roll_patch)",
                    ),
                ],
            ),
            _when(
                "_dispel_succeeded == true",
                [
                    _call(
                        "end_srd_concentration_node",
                        {
                            "source_state": "{_earth_record.caster_state_id}",
                            "target_state": "{_earth_record.caster_state_id}",
                        },
                    ),
                ],
            ),
        ],
    )


def _bind_dispel(recovery):
    """Join only this selected-source choice after the established recovery source owners."""
    recovery = deepcopy(recovery)
    condition = "spell == 'dispel_magic' and recovery_choice == 'move_earth'"
    recovery["effects"] = [e for e in recovery["effects"] if e.get("condition") != condition]
    arrays = {
        e.get("set", {}).get("field"): e["set"]["value"]
        for e in recovery["effects"]
        if e.get("set", {}).get("field") in {"_recovery_source_intents", "_recovery_source_owners"}
    }
    if arrays:
        if len(arrays) != 2 or any(not isinstance(value, list) for value in arrays.values()):
            raise ValueError("The actual Recovery selected-source boundary has changed.")
        for values in arrays.values():
            if "'move_earth'" not in values:
                values.append("'move_earth'")
    else:
        marker = "spell != 'dispel_magic' or recovery_choice != 'move_earth'"
        if not (
            len(recovery["effects"]) == 1 and recovery["effects"][0].get("condition") == marker
        ):
            recovery["effects"] = [_when(marker, recovery["effects"])]
    recovery["effects"].append(
        _when(
            condition,
            [
                _require("recovery_source_id != ''", "Select the authentic ongoing earth source."),
                _call(
                    "dispel_srd_move_earth_owned_source",
                    {
                        "caster_state": "{_caster_state_id}",
                        "caster": "{_caster_id}",
                        "source_id": "{recovery_source_id}",
                        "slot_level": "{slot_level}",
                        "casting_modifier": "{casting_modifier}",
                        "casting_ability": "{casting_ability}",
                    },
                    ("_earth_dispel_check",),
                ),
                _set("_dispel_checks", ["_earth_dispel_check"]),
                _set("_revival_succeeded", False),
                _set(
                    "_spell_summary_rows",
                    [{"label": "'Selected source ended'", "value": "_earth_dispel_check.ended"}],
                ),
                _set("_spell_detail_rows", []),
                _when(
                    "_earth_dispel_check.roll != null",
                    [
                        _call(
                            "format_srd_information_projection",
                            {
                                "message": "d20 {_earth_dispel_check.roll.results}; "
                                "selected {_earth_dispel_check.roll.total}; "
                                "modifier {_earth_dispel_check.roll.modifier}; "
                                "DC {_earth_dispel_check.dc}; "
                                "effect ended: {_earth_dispel_check.ended}",
                            },
                            ("_info_projection_message",),
                        ),
                        _set(
                            "_spell_detail_rows",
                            [
                                {"label": "'Dispel check'", "value": "_info_projection_message"},
                                {"label": "'Dispel DC'", "value": "_earth_dispel_check.dc"},
                            ],
                        ),
                    ],
                ),
            ],
        )
    )
    return recovery


def extend_runtime(text):
    tools = _load_runtime(text)["tools"]
    changed = {
        "record_srd_earth_material": _material(),
        "check_srd_move_earth_clear": _clear(),
        "prepare_srd_move_earth_change": _prepare(),
        "complete_srd_move_earth_change": _complete(),
        "retire_srd_move_earth": _retire(),
        "read_srd_move_earth_source": _read(),
        "cast_srd_move_earth": _cast(),
        "choose_srd_move_earth_area": _choose(),
        "dispel_srd_move_earth_owned_source": _dispel(),
        "read_srd_move_earth": _public(
            _read(),
            "exploration",
            "Move Earth",
            [
                ("Completed changes", "_earth_record.completed_changes"),
                ("Pending work", "_earth_record.pending"),
            ],
        ),
        **_refresh(),
    }
    changed = {name: tool for name, tool in changed.items() if tools.get(name) != tool}
    return _replace_tools(text, changed) if changed else text


def bind_runtime_consumers(text):
    """Join genuine paid casts, exact Concentration retirement and the authoritative clock."""
    text = extend_runtime(text)
    tools = _load_runtime(text)["tools"]
    changed = {}
    for name in ("cast_spell", "cast_spell_outside_combat"):
        cast = deepcopy(tools[name])
        if "cast_srd_move_earth" in str(cast["effects"]) or any(
            e.get("call") == "cast_spell" for e in cast["effects"]
        ):
            continue
        present = {p["name"] for p in cast["parameters"]}
        cast["parameters"] += [deepcopy(p) for p in CAST_CHOICES if p["name"] not in present]
        index = next(
            i
            for i, e in enumerate(cast["effects"])
            if e.get("set", {}).get("field") == "_card_slots_after"
        )
        cast["effects"].insert(
            index,
            _when(
                "_spell_cast_failed != true and _pending_spell_operation_lost != true and "
                "_spell_definition.family == 'move_earth'",
                [
                    _require(
                        "_scalar_target_supplied != true or _scalar_target_id == _caster_state_id",
                        "Move Earth selects actual terrain, not another creature.",
                    ),
                    _require(
                        "targets == null or len(targets) == 0",
                        "Move Earth selects actual terrain, not creature recipients.",
                    ),
                    _when(
                        "_spell_source_cast_id == null or _spell_source_cast_id == ''",
                        [
                            _call(
                                "begin_srd_spell_source_instance",
                                {
                                    "caster_state": "{_caster_state_id}",
                                    "spell": "move_earth",
                                    "slot_level": "{_slot_level}",
                                },
                                ("_spell_source_cast_id",),
                            ),
                        ],
                    ),
                    _call(
                        "cast_srd_move_earth",
                        {
                            "caster_state": "{_caster_state_id}",
                            "spell_cast_id": "{_spell_source_cast_id}",
                            "slot_level": "{_slot_level}",
                            "concentration_modifier": (
                                "{_casting_profile.concentration_save_modifier}"
                            ),
                            "earth_geometry_id": "{earth_geometry_id}",
                            "earth_elevation_change": "{earth_elevation_change}",
                        },
                        RESULTS,
                    ),
                ],
            ),
        )
        changed[name] = cast
    end = deepcopy(tools["end_srd_concentration_node"])
    if "retire_srd_move_earth" not in str(end["effects"]):
        end["effects"].insert(
            0,
            _when(
                "source_state.concentration_effect_family == 'move_earth'",
                [
                    _call(
                        "retire_srd_move_earth",
                        {
                            "caster_state": "{_source_state_id}",
                            "source_id": "{source_state.concentration_cast_id}",
                        },
                    ),
                ],
            ),
        )
        changed["end_srd_concentration_node"] = end
    clock = deepcopy(tools["advance_srd_lasting_clock"])
    if "refresh_srd_move_earth" not in str(clock["effects"]):
        index = next(
            i
            for i, e in enumerate(clock["effects"])
            if e.get("set", {}).get("field") == "lasting_state.lasting_tick"
        )
        clock["effects"].insert(
            index + 1,
            _when(
                "lasting_state.has_move_earth == true",
                [_call("refresh_srd_move_earth", {})],
            ),
        )
        changed["advance_srd_lasting_clock"] = clock
    return _replace_tools(text, changed) if changed else text


def bind_removal_consumers(text):
    """Join selected Dispel after the existing removal-adoption family has rebuilt its owners."""
    tools = _load_runtime(text)["tools"]
    if "dispel_srd_move_earth_owned_source" not in tools:
        raise ValueError("Emit the actual Move Earth source before binding its removal consumer.")
    recovery = _bind_dispel(tools["cast_srd_recovery"])
    return (
        _replace_tools(text, {"cast_srd_recovery": recovery})
        if recovery != tools["cast_srd_recovery"]
        else text
    )
