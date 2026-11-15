"""Printed solid spell panels use the admitted scene as their only physical body authority."""

import re
from copy import deepcopy

import yaml

if __package__:
    from .srd_actor_spell_modifiers import _call, _emit, _when
    from .srd_control_behaviors import _each, _push
    from .srd_scene_relations import POSITION_EXPORTS, _array, _snapshot, _spatial
    from .srd_spell_barriers import _effect_lists, _tool
    from .srd_timed_actor_defenses import _entity, _load_runtime, _parameter, _require, _set
    from .srd_timed_senses import _optional
else:
    from srd_actor_spell_modifiers import _call, _emit, _when
    from srd_control_behaviors import _each, _push
    from srd_scene_relations import POSITION_EXPORTS, _array, _snapshot, _spatial
    from srd_spell_barriers import _effect_lists, _tool
    from srd_timed_actor_defenses import _entity, _load_runtime, _parameter, _require, _set
    from srd_timed_senses import _optional

FAMILY = "solid_spell_wall"
POOL = "solid_spell_wall_sources"
INDEX = "solid_spell_wall_source_ids"
SOURCE_SHA256 = "f47aa77fef781cc935dd1f3983da93a43c07d667bd51dfd8b4d874425c50c489"
PROFILES = {
    "wall_of_force": {
        "family": FAMILY,
        "base_level": 5,
        "casting_time": "action",
        "range_kind": "distance",
        "range_feet": 120,
        "duration_rounds": 100,
        "concentration": True,
    }
}


def validate_profiles(definitions):
    """Admit only source-reviewed rows owned by this actual panel implementation."""
    for spell, profile in definitions.items():
        if profile.get("family") == FAMILY and profile != PROFILES.get(spell):
            raise SystemExit(f"Solid spell Wall profile differs from its source: {spell}")


def _source():
    return [
        _require(
            f"state.{POOL} != null and source_id in state.{POOL}",
            "Select an actual saved solid spell Wall source.",
        ),
        _set("_solid_wall_source", f"state.{POOL}[source_id]"),
        _require(
            f"_solid_wall_source.source_sha256 == {SOURCE_SHA256!r} and "
            "_solid_wall_source.spell_id == 'wall_of_force'",
            "Use the exact source-owned Force panels.",
        ),
    ]


def _scene(subject, output):
    return [
        _call("srd_information_scene_entity", {"subject": "{" + subject + "}"}, POSITION_EXPORTS),
        _set(output, "_info_scene_entity"),
    ]


def _frame(body, output):
    return _set(
        output,
        {
            "min": [
                f"{body}.position[0] - {body}.footprint.half_extents[0]",
                f"{body}.position[1] - {body}.footprint.half_extents[1]",
                f"{body}.position[2]",
            ],
            "max": [
                f"{body}.position[0] + {body}.footprint.half_extents[0]",
                f"{body}.position[1] + {body}.footprint.half_extents[1]",
                f"{body}.position[2] + {body}.height",
            ],
        },
    )


def _overlap(left, right):
    return " and ".join(
        f"{left}.min[{a}] < {right}.max[{a}] and {left}.max[{a}] > {right}.min[{a}]"
        for a in range(3)
    )


def _shape():
    return [
        _set("_solid_wall_extents", [50, 1 / 96]),
        _when("axis == 'north_south'", [_set("_solid_wall_extents", [1 / 96, 50])]),
        _set(
            "_solid_wall_min",
            [
                "origin[0] - _solid_wall_extents[0]",
                "origin[1] - _solid_wall_extents[1]",
                "origin[2] - 5",
            ],
        ),
        _set(
            "_solid_wall_max",
            [
                "origin[0] + _solid_wall_extents[0]",
                "origin[1] + _solid_wall_extents[1]",
                "origin[2] + 5",
            ],
        ),
        _set("_solid_wall_volume", {"min": "_solid_wall_min", "max": "_solid_wall_max"}),
        _require(
            " and ".join(
                f"_solid_wall_min[{a}] >= state.spatial_scene.map.bounds.min[{a}] and "
                f"_solid_wall_max[{a}] <= state.spatial_scene.map.bounds.max[{a}]"
                for a in range(3)
            ),
            "All ten actual Force panels must fit the admitted map.",
        ),
    ]


def _prepare():
    """Reject unsupported forming pushes before installing any paid source body."""
    return _tool(
        "Validate a genuine free-floating straight vertical ten-panel Force layout.",
        [
            _entity("caster_state"),
            _array("origin"),
            _optional("axis", "enum", "east_west", enum_values=["east_west", "north_south"]),
        ],
        [
            *_snapshot(),
            *_scene("_caster_state_id", "_solid_wall_caster_body"),
            _require(
                "_solid_wall_caster_body.metadata.plane != null",
                "The actual caster needs a source plane.",
            ),
            _set("_solid_wall_plane", "_solid_wall_caster_body.metadata.plane"),
            _call(
                "measure_srd_information_distance",
                {"start": "{_solid_wall_caster_body.position}", "end": "{origin}"},
                {"_solid_wall_range": "_scene_distance"},
            ),
            _require("_solid_wall_range <= 120", "The actual source point is beyond 120 feet."),
            _call(
                "srd_information_barriers",
                {
                    "start": "{_solid_wall_caster_body.position}",
                    "end": "{origin}",
                    "plane": "{_solid_wall_plane}",
                    "rule": "solid",
                },
                ("_information_blocked",),
            ),
            _require("_information_blocked != true", "Total Cover blocks this source point."),
            *_shape(),
            _set("_solid_wall_existing_bodies", "state.spatial_scene.entities"),
            _each(
                "_solid_wall_existing_bodies",
                "_solid_wall_body",
                [
                    _when(
                        "_solid_wall_body.active == true and "
                        "(_solid_wall_body.metadata.plane == _solid_wall_plane or "
                        "_solid_wall_body.metadata.plane == 'ethereal')",
                        [
                            _frame("_solid_wall_body", "_solid_wall_body_frame"),
                            _require(
                                f"not ({_overlap('_solid_wall_body_frame', '_solid_wall_volume')})",
                                "Formation crossing an actual body needs the printed push owner.",
                            ),
                        ],
                    ),
                ],
            ),
            _set("_solid_wall_existing_geometry", "state.spatial_scene.geometry"),
            _each(
                "_solid_wall_existing_geometry",
                "_solid_wall_geometry",
                [
                    _when(
                        "_solid_wall_geometry.active == true and "
                        "_solid_wall_geometry.movement == 'blocking' and "
                        "(_solid_wall_geometry.metadata.plane == _solid_wall_plane or "
                        "(_solid_wall_geometry.metadata.plane == null and "
                        "_solid_wall_plane == 'material'))",
                        [
                            _require(
                                "_solid_wall_geometry.volume.kind == 'box'",
                                "Intersecting curved scenery needs a genuine formation owner.",
                            ),
                            _require(
                                "not ("
                                + _overlap("_solid_wall_geometry.volume", "_solid_wall_volume")
                                + ")",
                                "The selected flat source cannot overlap existing solid scenery.",
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _caster_check():
    return _tool(
        "Derive this exact installed Wall's lifetime from its caster's actual Concentration.",
        [_entity("caster_state"), _parameter("source_id")],
        [
            *_source(),
            _require(
                "_solid_wall_source.caster_state_id == _caster_state_id",
                "Use the actual installed source caster.",
            ),
            _set(
                "_solid_wall_current",
                "_solid_wall_source.installed == true and "
                "state.lasting_tick < _solid_wall_source.expires_tick and "
                "caster_state.is_alive == true and caster_state.hp > 0 and "
                "caster_state.concentration_spell_id == _solid_wall_source.spell_id and "
                "caster_state.concentration_effect_family == 'solid_spell_wall' and "
                "caster_state.solid_wall_source_id == source_id",
            ),
        ],
    )


def _retire():
    """Only the exact source's panels vanish; the ordinary Concentration owner stays separate."""
    return _tool(
        "Retire one real Wall source and only its authenticated physical panels.",
        [_parameter("source_id"), _optional("reason", default="source ended")],
        [
            *_source(),
            _when(
                "_solid_wall_source.installed == true",
                [
                    _set("_solid_wall_scene", "state.spatial_scene.entities"),
                    _set("_solid_wall_retained_scene", []),
                    _each(
                        "_solid_wall_scene",
                        "_solid_wall_panel",
                        [
                            _when(
                                "_solid_wall_panel.metadata.solid_wall_source_id == source_id and "
                                "_solid_wall_panel.metadata.solid_wall_serial == "
                                "_solid_wall_source.serial",
                                [
                                    _set("_solid_wall_panel_end", {"active": False}),
                                    _set(
                                        "_solid_wall_panel",
                                        "merge(_solid_wall_panel, _solid_wall_panel_end)",
                                    ),
                                ],
                            ),
                            _push("_solid_wall_retained_scene", "_solid_wall_panel"),
                        ],
                    ),
                    _set("state.spatial_scene.entities", "_solid_wall_retained_scene"),
                    _set(
                        "state.spatial_scene.map.revision", "state.spatial_scene.map.revision + 1"
                    ),
                    _set(f"state.{POOL}.{{source_id}}.installed", False),
                    _set(f"state.{POOL}.{{source_id}}.ended_tick", "state.lasting_tick"),
                    _set(f"state.{POOL}.{{source_id}}.ended_reason", "reason"),
                    *_snapshot(),
                ],
            ),
        ],
    )


def _end():
    return _tool(
        "End the selected source through ordinary caster Concentration, preserving other casters.",
        [_parameter("source_id"), _optional("reason", default="removed")],
        [
            *_source(),
            _call(
                "retire_srd_solid_spell_wall_record",
                {"source_id": "{source_id}", "reason": "{reason}"},
            ),
            _call(
                "finish_srd_solid_spell_wall_concentration",
                {
                    "caster_state": "{_solid_wall_source.caster_state_id}",
                    "source_id": "{source_id}",
                },
            ),
        ],
    )


def _finish():
    return _tool(
        "Finish Concentration only when this exact real source still owns it.",
        [_entity("caster_state"), _parameter("source_id")],
        [
            *_source(),
            _when(
                "caster_state.concentration_effect_family == 'solid_spell_wall' and "
                "caster_state.solid_wall_source_id == source_id",
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
    )


def _refresh():
    return _tool(
        "Expire or remove only genuine registered Wall panels; no-source subjects take one guard.",
        [],
        [
            _set("_solid_walls_live", False),
            _when(
                "state.has_solid_spell_walls == true",
                [
                    _set("_solid_wall_ids", f"state.{INDEX}"),
                    _each(
                        "_solid_wall_ids",
                        "_solid_wall_id",
                        [
                            _set("_solid_wall_candidate", f"state.{POOL}[_solid_wall_id]"),
                            _when(
                                "_solid_wall_candidate.installed == true",
                                [
                                    _call(
                                        "qualify_srd_solid_spell_wall_source",
                                        {
                                            "caster_state": (
                                                "{_solid_wall_candidate.caster_state_id}"
                                            ),
                                            "source_id": "{_solid_wall_id}",
                                        },
                                        ("_solid_wall_current",),
                                    ),
                                    _when(
                                        "_solid_wall_current == true",
                                        [_set("_solid_walls_live", True)],
                                        [
                                            _call(
                                                "end_srd_solid_spell_wall_source",
                                                {
                                                    "source_id": "{_solid_wall_id}",
                                                    "reason": "lifetime",
                                                },
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _set("state.has_solid_spell_walls", "_solid_walls_live"),
        ],
    )


def _install():
    """The saved scene rows own physical bodies; records retain identity and lifetime."""
    return _tool(
        "Install printed invisible, damage-immune Force panels from an actual paid caster profile.",
        [
            _entity("caster_state"),
            _parameter("spell", "enum", enum_values=list(PROFILES)),
            _parameter("slot_level", "integer", minimum=5, maximum=9),
            _parameter("concentration_save_modifier", "integer"),
            _array("origin"),
            _optional("axis", "enum", "east_west", enum_values=["east_west", "north_south"]),
            _optional("spell_cast_id", default="", model_hidden=True),
            _optional("spell_cast_owner", default=None, resolve="entity", model_hidden=True),
            _optional(
                "actual_spell_level", "integer", -1, minimum=-1, maximum=9, model_hidden=True
            ),
        ],
        [
            _require(
                "caster_state.is_alive == true and caster_state.hp > 0",
                "Use a living actual spellcaster.",
            ),
            _when(
                "spell_cast_id != ''",
                [
                    _require(
                        "spell_cast_owner != null and _spell_cast_owner_id == _caster_state_id and "
                        "caster_state.last_spell_source_cast_id == spell_cast_id and "
                        "caster_state.last_spell_source_spell == spell and "
                        "actual_spell_level == slot_level",
                        "Use the actual allocated cast owner, source spell and paid slot.",
                    ),
                ],
            ),
            _call(
                "prepare_srd_solid_spell_wall",
                {
                    "caster_state": "{_caster_state_id}",
                    "origin": "{origin}",
                    "axis": "{axis}",
                },
                ("_solid_wall_plane",),
            ),
            _when("state.lasting_tick == null", [_set("state.lasting_tick", 0)]),
            _when("state.lasting_serial == null", [_set("state.lasting_serial", 0)]),
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
            {"increment": {"field": "state.lasting_serial", "amount": 1}},
            _call(
                "record_srd_result_text",
                {
                    "text": "solid-wall:{_caster_state_id}:{state.lasting_serial}",
                },
                {"_new_solid_wall_id": "_recorded_result_text"},
            ),
            _when(f"state.{POOL} == null", [_set(f"state.{POOL}", {}), _set(f"state.{INDEX}", [])]),
            _set(
                f"state.{POOL}.{{_new_solid_wall_id}}",
                {
                    "spell_id": "spell",
                    "spell_level": "slot_level",
                    "caster_state_id": "_caster_state_id",
                    "source_caster_id": None,
                    "cast_id": "spell_cast_id",
                    "ongoing_duration": "spell_cast_id != ''",
                    "dispel_ongoing": False,
                    "source_sha256": repr(SOURCE_SHA256),
                    "serial": "state.lasting_serial",
                    "installed": True,
                    "started_tick": "state.lasting_tick",
                    "expires_tick": "state.lasting_tick + 200",
                    "plane": "_solid_wall_plane",
                    "axis": "axis",
                    "panel_ids": [],
                },
            ),
            _when(
                "spell_cast_id != ''",
                [
                    _set(
                        f"state.{POOL}.{{_new_solid_wall_id}}.source_caster_id",
                        "_spell_cast_owner_id",
                    ),
                ],
            ),
            _set("_solid_wall_panel_numbers", list(range(10))),
            _set("_solid_wall_panel_ids", []),
            _set("_solid_wall_scene", "state.spatial_scene.entities"),
            _each(
                "_solid_wall_panel_numbers",
                "_solid_wall_number",
                [
                    _call(
                        "record_srd_result_text",
                        {
                            "text": "{_new_solid_wall_id}:panel:{_solid_wall_number}",
                        },
                        {"_solid_wall_panel_id": "_recorded_result_text"},
                    ),
                    _set(
                        "_solid_wall_position",
                        ["origin[0] - 45 + _solid_wall_number * 10", "origin[1]", "origin[2] - 5"],
                    ),
                    _set("_solid_wall_half_extents", [5, 1 / 96]),
                    _when(
                        "axis == 'north_south'",
                        [
                            _set(
                                "_solid_wall_position",
                                [
                                    "origin[0]",
                                    "origin[1] - 45 + _solid_wall_number * 10",
                                    "origin[2] - 5",
                                ],
                            ),
                            _set("_solid_wall_half_extents", [1 / 96, 5]),
                        ],
                    ),
                    _set(
                        "_solid_wall_new_panel",
                        {
                            "id": "_solid_wall_panel_id",
                            "map_id": "state.spatial_scene.map.id",
                            "position": "_solid_wall_position",
                            "footprint": {
                                "kind": "'box'",
                                "half_extents": "_solid_wall_half_extents",
                            },
                            "height": 10,
                            "facing": [1, 0],
                            "active": True,
                            "movement": "'blocking'",
                            "visibility": "'clear'",
                            "metadata": {
                                "plane": "_solid_wall_plane",
                                "kind": "'spell_force'",
                                "invisible": True,
                                "solid_wall_source_id": "_new_solid_wall_id",
                                "solid_wall_serial": "state.lasting_serial",
                                "srd_spell_object": {
                                    "kind": "'solid_force_panel'",
                                    "source_sha256": repr(SOURCE_SHA256),
                                },
                            },
                        },
                    ),
                    _push("_solid_wall_scene", "_solid_wall_new_panel"),
                    _push("_solid_wall_panel_ids", "_solid_wall_panel_id"),
                ],
            ),
            _set("state.spatial_scene.entities", "_solid_wall_scene"),
            _set("state.spatial_scene.map.revision", "state.spatial_scene.map.revision + 1"),
            _set(f"state.{POOL}.{{_new_solid_wall_id}}.panel_ids", "_solid_wall_panel_ids"),
            _set("_solid_wall_ids", f"state.{INDEX}"),
            _push("_solid_wall_ids", "_new_solid_wall_id"),
            _set(f"state.{INDEX}", "_solid_wall_ids"),
            _set("state.has_solid_spell_walls", True),
            _set("caster_state.solid_wall_source_id", "_new_solid_wall_id"),
            _set("caster_state.concentration_spell_id", "spell"),
            _set("caster_state.concentration_effect_family", repr(FAMILY)),
            _set("caster_state.concentration_target_state_id", "_caster_state_id"),
            _set("caster_state.concentration_target_state_ids", []),
            _set("caster_state.concentration_save_modifier", "concentration_save_modifier"),
            _set("caster_state.concentration_started_tick", "state.lasting_tick"),
            _set("caster_state.concentration_expires_tick", "state.lasting_tick + 200"),
            _set("caster_state.concentration_started_round", "combat.round"),
            _set(
                "_spell_summary_rows", [{"label": "'Wall of Force'", "value": "_new_solid_wall_id"}]
            ),
            _set(
                "_spell_detail_rows",
                [{"label": "'Actual panels'", "value": "_solid_wall_panel_ids"}],
            ),
            *_snapshot(),
        ],
    )


def _movement():
    """Use the neutral body sweep and the actual source's Force plane extension."""
    return _tool(
        "Reject physical passage through current source panels, including actual Ethereal travel.",
        [
            _parameter("subject"),
            _array("destination"),
            _parameter("plane"),
            _optional("teleport", "boolean", False),
        ],
        [
            _call("refresh_srd_solid_spell_walls", {}),
            *_scene("subject", "_solid_wall_mover"),
            _when(
                "teleport != true",
                [
                    _set("_solid_wall_ids", f"state.{INDEX}"),
                    _each(
                        "_solid_wall_ids",
                        "_solid_wall_id",
                        [
                            _set("_solid_wall_source", f"state.{POOL}[_solid_wall_id]"),
                            _when(
                                "_solid_wall_source.installed == true and "
                                "(_solid_wall_source.plane == _solid_wall_mover.metadata.plane or "
                                "_solid_wall_mover.metadata.plane == 'ethereal')",
                                [
                                    _require(
                                        "plane == _solid_wall_mover.metadata.plane",
                                        "Cross-plane Wall travel needs its actual source owner.",
                                    ),
                                    *_snapshot(),
                                    _spatial(
                                        "sweep",
                                        {
                                            "map_id": "{state.spatial_scene.map.id}",
                                            "moving_entity_id": "{_solid_wall_mover.id}",
                                            "destination": "{destination}",
                                            "include_entities": True,
                                            "metadata_filter": {
                                                "key": "solid_wall_source_id",
                                                "value": "{_solid_wall_id}",
                                                "missing_matches": False,
                                            },
                                        },
                                        "_solid_wall_sweep",
                                    ),
                                    _require(
                                        "_solid_wall_sweep.supported == true",
                                        "Wall passage needs a supported actual physical body.",
                                    ),
                                    _require(
                                        "_solid_wall_sweep.clear == true",
                                        "An intact Force panel blocks physical passage.",
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _tools():
    return {
        "prepare_srd_solid_spell_wall": _prepare(),
        "cast_srd_solid_spell_wall": _install(),
        "qualify_srd_solid_spell_wall_source": _caster_check(),
        "retire_srd_solid_spell_wall_record": _retire(),
        "end_srd_solid_spell_wall_source": _end(),
        "finish_srd_solid_spell_wall_concentration": _finish(),
        "refresh_srd_solid_spell_walls": _refresh(),
        "check_srd_solid_wall_movement": _movement(),
        **_owned_cast_tools(),
    }


def _owned_cast_tools():
    """Finite dismissal reads the same authenticated source index as physical movement."""
    match = (
        "_solid_owned.cast_id == cast_id and cast_id != '' and "
        "_solid_owned.source_caster_id == _caster_state_id and "
        "_solid_owned.ongoing_duration == true"
    )
    collector = _tool(
        "Collect this caster's exact finite solid Wall casting from its saved source index.",
        [_entity("caster_state"), _parameter("cast_id")],
        [
            _set("_solid_wall_dismiss_ids", []),
            _when(
                "state.has_solid_spell_walls == true",
                [
                    _set("_solid_owned_ids", f"state.{INDEX}"),
                    _each(
                        "_solid_owned_ids",
                        "_solid_owned_id",
                        [
                            _set("_solid_owned", f"state.{POOL}[_solid_owned_id]"),
                            _when(
                                match + " and _solid_owned.installed == true",
                                [
                                    _call(
                                        "qualify_srd_solid_spell_wall_source",
                                        {
                                            "caster_state": "{_caster_state_id}",
                                            "source_id": "{_solid_owned_id}",
                                        },
                                        ("_solid_wall_current",),
                                    ),
                                    _when(
                                        "_solid_wall_current == true",
                                        [_push("_solid_wall_dismiss_ids", "_solid_owned_id")],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    retire = _tool(
        "Retire only this caster's actual finite solid Wall cast through its real end owner.",
        [_entity("caster_state"), _parameter("cast_id"), _parameter("source_id")],
        [
            *_source(),
            _set("_solid_owned", "_solid_wall_source"),
            _require(match, "Choose this caster's actual finite solid Wall cast."),
            _when(
                "_solid_owned.installed == true",
                [
                    _call(
                        "qualify_srd_solid_spell_wall_source",
                        {"caster_state": "{_caster_state_id}", "source_id": "{source_id}"},
                        ("_solid_wall_current",),
                    ),
                    _require(
                        "_solid_wall_current == true", "This exact Wall is no longer current."
                    ),
                    _call(
                        "end_srd_solid_spell_wall_source",
                        {"source_id": "{source_id}", "reason": "dismissed"},
                    ),
                ],
            ),
        ],
    )
    return {
        "collect_srd_solid_wall_owned_cast": collector,
        "retire_srd_solid_wall_owned_cast": retire,
    }


def _bind_dismissal(tool):
    """Join actual finite-source selection before the shared dismissal admission check."""
    marker = "lasting_state.has_solid_spell_walls == true"
    tool["effects"] = [
        e
        for e in tool["effects"]
        if not (e.get("condition") == marker and "_solid_wall_dismiss_ids" in str(e))
        and e.get("for_each", {}).get("collection") != "_solid_wall_dismiss_ids"
        and e.get("set", {}).get("field") != "_solid_wall_dismiss_ids"
    ]
    anchor = next(i for i, e in enumerate(tool["effects"]) if "require" in e)
    tool["effects"][anchor:anchor] = [
        _set("_solid_wall_dismiss_ids", []),
        _when(
            marker,
            [
                _call(
                    "collect_srd_solid_wall_owned_cast",
                    {"caster_state": "{_caster_state_id}", "cast_id": "{cast_id}"},
                    ("_solid_wall_dismiss_ids",),
                ),
                _set("_dismiss_count", "_dismiss_count + len(_solid_wall_dismiss_ids)"),
            ],
        ),
    ]
    tool["effects"].append(
        _each(
            "_solid_wall_dismiss_ids",
            "_solid_wall_dismiss_id",
            [
                _call(
                    "retire_srd_solid_wall_owned_cast",
                    {
                        "caster_state": "{_caster_state_id}",
                        "cast_id": "{cast_id}",
                        "source_id": "{_solid_wall_dismiss_id}",
                    },
                ),
            ],
        )
    )


def _cast_dispatch(tool):
    names = {p["name"] for p in tool["parameters"]}
    for parameter in (
        _array("solid_wall_origin", default=[0, 0, 5]),
        _optional("solid_wall_axis", "enum", "east_west", enum_values=["east_west", "north_south"]),
    ):
        if parameter["name"] not in names:
            tool["parameters"].append(parameter)
    tool["effects"] = [
        e
        for e in tool["effects"]
        if e.get("condition") != f"_spell_definition.family == '{FAMILY}'"
    ]
    anchor = max(
        i
        for i, e in enumerate(tool["effects"])
        if "_spell_definition.family ==" in e.get("condition", "")
    )
    tool["effects"].insert(
        anchor + 1,
        _when(
            f"_spell_definition.family == '{FAMILY}'",
            [
                _call(
                    "cast_srd_solid_spell_wall",
                    {
                        "caster_state": "{_caster_state_id}",
                        "spell": "{spell}",
                        "slot_level": "{_slot_level}",
                        "concentration_save_modifier": (
                            "{_casting_profile.concentration_save_modifier}"
                        ),
                        "spell_cast_id": "{_spell_source_cast_id}",
                        "origin": "{solid_wall_origin}",
                        "axis": "{solid_wall_axis}",
                    },
                    ("_spell_summary_rows", "_spell_detail_rows"),
                ),
            ],
        ),
    )


def _normalize_scene(tool):
    for effects in _effect_lists(tool["effects"]):
        for effect in effects:
            for kind in ("spatial_query", "spatial_mutation"):
                if kind in effect:
                    effect[kind]["scene"] = "state.spatial_scene"


def extend_runtime(text):
    """Emit before paid Source/Reaction composition with the Root-registered installer."""
    tools = _load_runtime(text)["tools"]
    changed = _tools()
    caster = deepcopy(tools["cast_spell"])
    _cast_dispatch(caster)
    changed["cast_spell"] = caster
    end = deepcopy(tools["end_srd_concentration_node"])
    marker = "source_state.concentration_effect_family == 'solid_spell_wall' and "
    end["effects"] = [e for e in end["effects"] if not e.get("condition", "").startswith(marker)]
    end["effects"].insert(
        0,
        _when(
            marker + "source_state.solid_wall_source_id != null",
            [
                _call(
                    "retire_srd_solid_spell_wall_record",
                    {
                        "source_id": "{source_state.solid_wall_source_id}",
                        "reason": "concentration ended",
                    },
                ),
            ],
        ),
    )
    changed["end_srd_concentration_node"] = end
    clock = deepcopy(tools["advance_srd_lasting_time"])
    clock["effects"] = [
        e for e in clock["effects"] if "refresh_srd_solid_spell_walls" not in str(e)
    ]
    clock["effects"].append(
        _when(
            "state.has_solid_spell_walls == true",
            [
                _call("refresh_srd_solid_spell_walls", {}),
            ],
        )
    )
    changed["advance_srd_lasting_time"] = clock
    for tool in changed.values():
        _normalize_scene(tool)
    text = _emit(text, changed)
    # Family rows contain exactly the source header; no optional mode receipts.
    properties = {
        key: {
            "type": "boolean"
            if isinstance(value, bool)
            else "integer"
            if isinstance(value, int)
            else "string",
            "enum": [value],
        }
        for key, value in PROFILES["wall_of_force"].items()
        if key != "family"
    }
    row = {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }
    schema = yaml.safe_dump({FAMILY: row}, sort_keys=False)
    schema = "".join("      " + line if line.strip() else line for line in schema.splitlines(True))
    runtime = _load_runtime(text)
    variants = runtime["definition_row_schemas"]["definitions.spells"]["variants"]
    if FAMILY in variants:
        if variants[FAMILY] == row:
            return text
        pattern = rf"(?ms)^      {FAMILY}:.*?(?=^      [a-z]|^  [a-z])"
        if len(re.findall(pattern, text)) != 1:
            raise ValueError("Solid Wall schema needs its unique actual variant anchor.")
        return re.sub(pattern, lambda _: schema, text, count=1)
    first = next(iter(variants))
    anchors = list(re.finditer(rf"(?m)^      {re.escape(first)}:", text))
    if len(anchors) != 1:
        raise ValueError("Solid Wall schema needs an existing unique spell variant anchor.")
    index = anchors[0].start()
    return text[:index] + schema + text[index:]


def bind_runtime_consumers(text):
    """Bind immediately before actual ordinary movement and final Travel relocation mutations."""
    tools = _load_runtime(text)["tools"]
    changed = {}
    if "dismiss_srd_spell" in tools:
        changed["dismiss_srd_spell"] = deepcopy(tools["dismiss_srd_spell"])
        _bind_dismissal(changed["dismiss_srd_spell"])
    for name in (
        "move_srd_scene_creature_environment_segment",
        "move_srd_travel_subject_node",
        "move_srd_travel_subject",
    ):
        if name not in tools:
            continue
        tool = deepcopy(tools[name])
        mutations = [i for i, e in enumerate(tool["effects"]) if "spatial_mutation" in e]
        if not mutations:
            continue
        if "check_srd_solid_wall_movement" in str(tool["effects"]):
            continue
        ordinary = name == "move_srd_scene_creature_environment_segment"
        tool["effects"].insert(
            mutations[0],
            _when(
                "state.has_solid_spell_walls == true",
                [
                    _call(
                        "check_srd_solid_wall_movement",
                        {
                            "subject": "{_actor_state_id}" if ordinary else "{subject}",
                            "destination": "{destination}",
                            "plane": "{_move_scene_entity.metadata.plane}"
                            if ordinary
                            else "{plane}",
                            "teleport": False if ordinary else "{teleport}",
                        },
                    ),
                ],
            ),
        )
        _normalize_scene(tool)
        changed[name] = tool
    return _emit(text, changed)
