"""Mirage Arcane owns tangible Square terrain through the existing scene and route cost."""

from copy import deepcopy

import yaml

if __package__:
    from .srd_divination_spells import _normalize
    from .srd_illusion_appearance import HIDDEN_SOURCE
    from .srd_illusion_spells import PENDING
    from .srd_scene_relations import POSITION_EXPORTS, _array, _tool_scene
    from .srd_spell_object_content import _new_scene_object
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _RuntimeDumper,
        _set,
        _when,
    )
    from .srd_timed_senses import _loop, _optional, _public, _push, _replace_tools
else:
    from srd_divination_spells import _normalize
    from srd_illusion_appearance import HIDDEN_SOURCE
    from srd_illusion_spells import PENDING
    from srd_scene_relations import POSITION_EXPORTS, _array, _tool_scene
    from srd_spell_object_content import _new_scene_object
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _RuntimeDumper,
        _set,
        _when,
    )
    from srd_timed_senses import _loop, _optional, _public, _push, _replace_tools

SOURCE = {"mirage_arcane": ([149, 150], "Mirage Arcane", "Mirror Image")}
PROFILES = {
    "mirage_arcane": {
        "family": "mirage_spell",
        "base_level": 7,
        "casting_time": "10_minutes",
        "range_kind": "sight",
        "duration_rounds": 144000,
        "concentration": False,
    }
}
PUBLIC_TOOLS = {"perceive_srd_mirage_terrain", "dismiss_srd_mirage_arcane"}
CAST_CHOICES = [
    _optional(
        "mirage_corner", "array", [0, 0, 0], items={"type": "number"}, min_items=3, max_items=3
    ),
    _optional("mirage_side_feet", "number", 40, minimum=0.001, maximum=5280),
    _optional("mirage_description", default=""),
    _optional("mirage_terrain_kind", default=""),
    _optional("mirage_tactile_kind", default=""),
    _optional("mirage_sound", default=""),
    _optional("mirage_smell", default=""),
    _optional(
        "mirage_ground_mode", "enum", "unchanged", enum_values=["unchanged", "clear", "difficult"]
    ),
    _optional(
        "mirage_structures",
        "array",
        [],
        items={
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "position": {
                    "type": "array",
                    "items": {"type": "number"},
                },
                "extent": {
                    "type": "array",
                    "items": {"type": "number"},
                },
                "description": {"type": "string"},
                "tactile_kind": {"type": "string"},
                "blocks_movement": {"type": "boolean"},
                "ground": {"type": "boolean"},
            },
            "required": [
                "id",
                "position",
                "extent",
                "description",
                "tactile_kind",
                "blocks_movement",
                "ground",
            ],
            "additionalProperties": False,
        },
        max_items=64,
    ),
]


def validate_profiles(definitions):
    """Require the exact printed source profile rather than an unrelated family alias."""
    for spell, profile in PROFILES.items():
        if spell in definitions and definitions[spell] != profile:
            raise SystemExit("Mirage Arcane profile differs from pinned source")


def _record(identity="mirage_id"):
    return [
        _require(f"{identity} in state.mirage_sources", "Select an actual Mirage source."),
        _set("_mirage_record", f"state.mirage_sources[{identity}]"),
    ]


def _inside(point, record="_mirage_record"):
    return " and ".join(
        f"{point}[{axis}] >= {record}.corner[{axis}] and "
        f"{point}[{axis}] <= {record}.corner[{axis}] + {record}.side"
        for axis in range(2)
    )


def _outside_piece():
    return " or ".join(
        f"_mirage_actual_piece.volume.max[{axis}] < _mirage_record.corner[{axis}] or "
        f"_mirage_actual_piece.volume.min[{axis}] > "
        f"_mirage_record.corner[{axis}] + _mirage_record.side"
        for axis in range(2)
    )


def _source_tools():
    check = _tool_scene(
        "Qualify a source's actual ten-day non-concentration lifetime.",
        [_parameter("mirage_id")],
        _record()
        + [
            _set(
                "_mirage_active",
                "_mirage_record.installed == true and "
                "state.lasting_tick < _mirage_record.expires_tick",
            )
        ],
    )
    retire = _tool_scene(
        "Retire the exact illusion and every actual source-owned tangible piece.",
        [_parameter("mirage_id"), _parameter("reason")],
        _record()
        + [
            _set("state.mirage_sources.{mirage_id}.installed", False),
            _set("state.mirage_sources.{mirage_id}.retired_reason", "reason"),
            _set("_mirage_retiring_ids", "_mirage_record.piece_ids"),
            _set("_mirage_existing_pieces", "state.spatial_scene.geometry"),
            _loop(
                "_mirage_existing_pieces",
                "_mirage_piece",
                [
                    _when(
                        "_mirage_piece.id in _mirage_retiring_ids and "
                        "_mirage_piece.metadata.mirage_source_id == mirage_id",
                        [
                            _call(
                                "retire_srd_mirage_piece",
                                {"subject": "{_mirage_piece.id}", "mirage_id": "{mirage_id}"},
                            )
                        ],
                    )
                ],
            ),
        ],
    )
    piece = _tool_scene(
        "Remove an exact tangible illusion piece without replacing a creature or unrelated object.",
        [_parameter("subject"), _parameter("mirage_id")],
        [
            _set("_mirage_piece_rows", "state.spatial_scene.geometry"),
            _set("_mirage_kept_pieces", []),
            _loop(
                "_mirage_piece_rows",
                "_mirage_piece",
                [
                    {
                        "condition": "_mirage_piece.id == subject and "
                        "_mirage_piece.metadata.mirage_source_id == mirage_id",
                        "on_true": [
                            _require(
                                "_mirage_piece.metadata.participant_ref == null",
                                "An illusion piece cannot retire a creature.",
                            ),
                        ],
                        "on_false": [_push("_mirage_kept_pieces", "_mirage_piece")],
                    }
                ],
            ),
            _set("state.spatial_scene.geometry", "_mirage_kept_pieces"),
            _set("state.spatial_scene.map.revision", "state.spatial_scene.map.revision + 1"),
        ],
    )
    registry = _tool_scene(
        "Retire real expired sources and source pieces actually removed from their Square.",
        [],
        [
            _set("_mirage_source_ids", "state.mirage_source_ids"),
            _set("_mirage_any_active", False),
            _loop(
                "_mirage_source_ids",
                "_mirage_check_id",
                [
                    _call(
                        "check_srd_mirage_source",
                        {"mirage_id": "{_mirage_check_id}"},
                        ["_mirage_active", "_mirage_record"],
                    ),
                    _when(
                        "_mirage_active != true and _mirage_record.installed == true",
                        [
                            _call(
                                "retire_srd_mirage_source",
                                {"mirage_id": "{_mirage_check_id}", "reason": "expiry"},
                            )
                        ],
                    ),
                    _when(
                        "_mirage_active == true",
                        [
                            _set("_mirage_any_active", True),
                            _set("_mirage_actual_pieces", "state.spatial_scene.geometry"),
                            _loop(
                                "_mirage_actual_pieces",
                                "_mirage_actual_piece",
                                [
                                    _when(
                                        "_mirage_actual_piece.metadata.mirage_source_id == "
                                        "_mirage_check_id and ("
                                        + "_mirage_actual_piece.metadata.plane != "
                                        "_mirage_record.plane or (" + _outside_piece() + "))",
                                        [
                                            _call(
                                                "retire_srd_mirage_piece",
                                                {
                                                    "subject": "{_mirage_actual_piece.id}",
                                                    "mirage_id": "{_mirage_check_id}",
                                                },
                                            )
                                        ],
                                    )
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _set("state.has_mirage_spells", "_mirage_any_active"),
        ],
    )
    dismiss = _public(
        _tool_scene(
            "Dismiss the caster's own finite Mirage without an action.",
            [_entity("caster_state"), _parameter("mirage_id")],
            [
                _require(PENDING, "Resolve the pending action first."),
                _require(
                    "caster_state.is_alive == true and "
                    "'incapacitated' not in caster_state.conditions",
                    "An Incapacitated caster cannot dismiss a time-span spell.",
                ),
                *_record(),
                _require(
                    "_mirage_record.installed == true and "
                    "_mirage_record.caster == _caster_state_id",
                    "Dismiss only the actual caster's own source.",
                ),
                _call(
                    "retire_srd_mirage_source", {"mirage_id": "{mirage_id}", "reason": "dismissed"}
                ),
                _call("refresh_srd_mirage_sources", {}),
                _set("_mirage_dismissed", True),
            ],
        ),
        "exploration",
        "Mirage ended",
        [("Dismissed", "_mirage_dismissed")],
    )
    return {
        "check_srd_mirage_source": check,
        "retire_srd_mirage_source": retire,
        "retire_srd_mirage_piece": piece,
        "refresh_srd_mirage_sources": registry,
        "dismiss_srd_mirage_arcane": dismiss,
    }


def _piece_tool():
    return _tool_scene(
        "Create an actual tangible source structure without granting it creature HP or AC.",
        [_parameter("mirage_id"), _parameter("spec", "object")],
        _record()
        + [
            _require(
                "len(spec.position) == 3 and len(spec.extent) == 3",
                "Structures need exactly three saved position and dimension coordinates.",
            ),
            _require(
                "spec.description != '' and spec.tactile_kind != ''",
                "Describe the selected tangible structure.",
            ),
            _set("_world_origin", "spec.position"),
            *[
                _require(
                    f"spec.extent[{axis}] > 0", "Physical structures need positive dimensions."
                )
                for axis in range(3)
            ],
            *[
                _require(
                    f"spec.position[{axis}] - spec.extent[{axis}] * 0.5 >= "
                    f"_mirage_record.corner[{axis}] and "
                    f"spec.position[{axis}] + spec.extent[{axis}] * 0.5 <= "
                    f"_mirage_record.corner[{axis}] + _mirage_record.side",
                    "The entire structure must remain inside the source Square.",
                )
                for axis in range(2)
            ],
            _set("_mirage_piece_movement", "'passable'"),
            _when("spec.blocks_movement == true", [_set("_mirage_piece_movement", "'blocking'")]),
            *_new_scene_object(
                {
                    "plane": "_mirage_record.plane",
                    "mirage_source_id": "mirage_id",
                    "description": "spec.description",
                    "tactile_kind": "spec.tactile_kind",
                    "ground": "spec.ground",
                    "structure": True,
                },
                identity="spec.id",
                extent="spec.extent",
                movement="_mirage_piece_movement",
                visibility="'clear'",
                scene_kind="geometry",
            ),
            _set("_mirage_piece_ids", "_mirage_record.piece_ids"),
            _push("_mirage_piece_ids", "spec.id"),
            _set("state.mirage_sources.{mirage_id}.piece_ids", "_mirage_piece_ids"),
        ],
    )


def _casting_tool():
    return _tool_scene(
        "Install the visible actual Square and tangible scenery after the ten-minute paid cast.",
        [
            _entity("caster_state"),
            _parameter("slot_level", "integer"),
            *deepcopy(CAST_CHOICES),
            *deepcopy(HIDDEN_SOURCE),
        ],
        [
            _require(
                "mirage_side_feet > 0 and mirage_side_feet <= 5280",
                "Mirage changes no more than one mile square.",
            ),
            _require(
                "mirage_description != '' and mirage_terrain_kind != '' and "
                "mirage_tactile_kind != ''",
                "Describe the source-selected apparent terrain and its physical feel.",
            ),
            _call(
                "srd_information_scene_entity", {"subject": "{_caster_state_id}"}, POSITION_EXPORTS
            ),
            _set("_mirage_cast_plane", "_info_scene_entity.metadata.plane"),
            _set(
                "_mirage_origin",
                [
                    "mirage_corner[0] + mirage_side_feet * 0.5",
                    "mirage_corner[1] + mirage_side_feet * 0.5",
                    "mirage_corner[2]",
                ],
            ),
            _call(
                "resolve_srd_illumination_point_sight",
                {
                    "observer_state": "{_caster_state_id}",
                    "point": "{_mirage_origin}",
                    "perception_plane": "{_mirage_cast_plane}",
                },
                ["_illumination_point_can_see"],
            ),
            _require(
                "_illumination_point_can_see == true",
                "The terrain's actual casting origin is in sight.",
            ),
            _when(
                "state.mirage_sources == null",
                [
                    _set("state.mirage_sources", {}),
                    _set("state.mirage_source_ids", []),
                    _set("state.mirage_serial", 0),
                ],
            ),
            _set("state.mirage_serial", "state.mirage_serial + 1"),
            _call(
                "record_srd_result_text",
                {"text": "mirage-{state.mirage_serial}"},
                ["_recorded_result_text"],
            ),
            _set("_mirage_id", "_recorded_result_text"),
            _set(
                "state.mirage_sources.{_mirage_id}",
                {
                    "id": "_mirage_id",
                    "spell": "'mirage_arcane'",
                    "installed": True,
                    "caster": "_caster_state_id",
                    "plane": "_mirage_cast_plane",
                    "corner": "mirage_corner",
                    "side": "mirage_side_feet",
                    "origin": "_mirage_origin",
                    "description": "mirage_description",
                    "terrain_kind": "mirage_terrain_kind",
                    "tactile_kind": "mirage_tactile_kind",
                    "sound": "mirage_sound",
                    "smell": "mirage_smell",
                    "ground_mode": "mirage_ground_mode",
                    "piece_ids": [],
                    "started_tick": "state.lasting_tick",
                    "expires_tick": "state.lasting_tick + 288000",
                    "spell_level": "slot_level",
                    "cast_id": "spell_cast_id",
                    "source_caster_id": "_caster_state_id",
                    "ongoing_duration": True,
                    "dispel_ongoing": True,
                },
            ),
            _set("_mirage_ids", "state.mirage_source_ids"),
            _push("_mirage_ids", "_mirage_id"),
            _set("state.mirage_source_ids", "_mirage_ids"),
            _set("state.has_mirage_spells", True),
            _set("_mirage_specs", "mirage_structures"),
            _loop(
                "_mirage_specs",
                "_mirage_spec",
                [
                    _call(
                        "create_srd_mirage_structure",
                        {"mirage_id": "{_mirage_id}", "spec": "{_mirage_spec}"},
                    )
                ],
            ),
            _set("_mirage_summary_rows", [{"label": "'Mirage'", "value": "_mirage_id"}]),
            _set("_mirage_detail_rows", [{"label": "'Square side'", "value": "mirage_side_feet"}]),
        ],
    )


def _perception_tool():
    return _public(
        _tool_scene(
            "Observe actual terrain without disguising, concealing or creating a creature.",
            [_entity("observer_state"), _parameter("subject")],
            [
                _require(PENDING, "Resolve the pending action before observing terrain."),
                _call("refresh_srd_mirage_sources", {}),
                _call("srd_information_scene_entity", {"subject": "{subject}"}, POSITION_EXPORTS),
                _set("_mirage_subject", "_info_scene_entity"),
                _set("_mirage_subject_position", "_info_position"),
                _require(
                    "_mirage_subject.metadata.participant_ref == null",
                    "Mirage never changes a creature's apparent body or presence.",
                ),
                _require(
                    "_mirage_subject.metadata.natural_terrain == true or "
                    "_mirage_subject.metadata.structure == true",
                    "Observe actual admitted terrain or a structure.",
                ),
                _set("_mirage_selected", None),
                _set("_mirage_ids", "state.mirage_source_ids"),
                _loop(
                    "_mirage_ids",
                    "_mirage_test_id",
                    [
                        _call(
                            "check_srd_mirage_source",
                            {"mirage_id": "{_mirage_test_id}"},
                            ["_mirage_active", "_mirage_record"],
                        ),
                        _when(
                            "_mirage_active == true and _mirage_subject.metadata.plane == "
                            "_mirage_record.plane and " + _inside("_mirage_subject_position"),
                            [_set("_mirage_selected", "_mirage_test_id")],
                        ),
                    ],
                ),
                _require(
                    "_mirage_selected != null", "The actual subject is inside a live Mirage Square."
                ),
                *_record("_mirage_selected"),
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{_observer_state_id}"},
                    POSITION_EXPORTS,
                ),
                _set("_mirage_observer_position", "_info_position"),
                _set("_mirage_observer_plane", "_info_scene_entity.metadata.plane"),
                _call(
                    "resolve_srd_illumination_point_sight",
                    {
                        "observer_state": "{_observer_state_id}",
                        "point": "{_mirage_subject_position}",
                        "perception_plane": "{_mirage_record.plane}",
                    },
                    [
                        "_illumination_point_can_see",
                        "_illumination_point_distance",
                        "_illumination_point_total_cover",
                    ],
                ),
                _call(
                    "resolve_srd_timed_senses",
                    {"target_state": "{_observer_state_id}"},
                    ["_sense_truesight"],
                ),
                _set(
                    "_mirage_sees_truth",
                    "_illumination_point_can_see == true and "
                    "_sense_truesight >= _illumination_point_distance and _sense_truesight > 0",
                ),
                _set("_mirage_apparent_terrain", "_mirage_record.terrain_kind"),
                _when(
                    "_mirage_sees_truth == true",
                    [
                        _require(
                            "_mirage_subject.metadata.terrain_kind != null or "
                            "_mirage_subject.metadata.mirage_source_id != null",
                            "Truesight needs the actual terrain's admitted true form.",
                        ),
                        _set("_mirage_apparent_terrain", "_mirage_subject.metadata.terrain_kind"),
                        _when(
                            "_mirage_subject.metadata.mirage_source_id != null",
                            [_set("_mirage_apparent_terrain", "'source-owned illusory structure'")],
                        ),
                    ],
                ),
                _set("_mirage_tactile", "_mirage_record.tactile_kind"),
                _when(
                    "_mirage_subject.metadata.mirage_source_id != null",
                    [_set("_mirage_tactile", "_mirage_subject.metadata.tactile_kind")],
                ),
                _set(
                    "_mirage_touch_reachable",
                    "_mirage_observer_plane == _mirage_record.plane and "
                    "_illumination_point_distance <= 5 and "
                    "_illumination_point_total_cover != true",
                ),
                _when("_mirage_touch_reachable != true", [_set("_mirage_tactile", "''")]),
                _set("_mirage_sound", "''"),
                _set("_mirage_smell", "''"),
                _when(
                    "_mirage_observer_plane == _mirage_record.plane and "
                    + _inside("_mirage_observer_position"),
                    [
                        _when(
                            "'deafened' not in observer_state.conditions",
                            [_set("_mirage_sound", "_mirage_record.sound")],
                        ),
                        _when(
                            "observer_state.can_smell != false",
                            [_set("_mirage_smell", "_mirage_record.smell")],
                        ),
                    ],
                ),
                _set("_mirage_visual", "_illumination_point_can_see"),
                _when("_mirage_visual != true", [_set("_mirage_apparent_terrain", "''")]),
            ],
        ),
        "exploration",
        "Mirage terrain",
        [
            ("Visible", "_mirage_visual"),
            ("Terrain", "_mirage_apparent_terrain"),
            ("True form visible", "_mirage_sees_truth"),
            ("Physical feel", "_mirage_tactile"),
            ("Within tactile reach", "_mirage_touch_reachable"),
            ("Ambient sound", "_mirage_sound"),
            ("Ambient smell", "_mirage_smell"),
        ],
    )


def extend_runtime(text):
    """Emit source/perception/physical leaves; route and relocation binding follows final owners."""
    tools = _load_runtime(text)["tools"]
    changed = {
        **_source_tools(),
        **_route_tools(),
        "create_srd_mirage_structure": _piece_tool(),
        "cast_srd_mirage_arcane": _casting_tool(),
        "perceive_srd_mirage_terrain": _perception_tool(),
    }
    _bind_cast(tools, changed)
    clock = deepcopy(tools["advance_srd_lasting_clock"])
    clock["effects"] = [
        effect
        for effect in clock["effects"]
        if effect.get("condition") != "lasting_state.has_mirage_spells == true"
    ]
    clock["effects"].append(
        _when("lasting_state.has_mirage_spells == true", [_call("refresh_srd_mirage_sources", {})])
    )
    changed["advance_srd_lasting_clock"] = clock
    _normalize(changed)
    text = _replace_tools(text, changed)
    if "      mirage_spell:" not in text:
        properties = {
            k: {"type": "boolean" if type(v) is bool else "integer" if type(v) is int else "string"}
            for k, v in PROFILES["mirage_arcane"].items()
            if k != "family"
        }
        schema = yaml.dump(
            {
                "mirage_spell": {
                    "type": "object",
                    "properties": properties,
                    "required": list(properties),
                    "additionalProperties": False,
                }
            },
            Dumper=_RuntimeDumper,
            sort_keys=False,
        )
        text = text.replace(
            "      illusion_spell:",
            "".join("      " + line for line in schema.splitlines(True))
            + "\n      illusion_spell:",
            1,
        )
    return text


def _bind_cast(tools, changed):
    """Replace only this source family's dispatch, preserving final casting failure guards."""
    cast = deepcopy(tools["cast_spell"])
    names = {p["name"] for p in cast["parameters"]}
    cast["parameters"] += [deepcopy(p) for p in CAST_CHOICES if p["name"] not in names]
    arguments = {p["name"]: "{" + p["name"] + "}" for p in CAST_CHOICES}
    arguments.update(caster_state="{_caster_state_id}", slot_level="{_slot_level}")
    branch = _when(
        "_spell_definition.family == 'mirage_spell'",
        [
            _call(
                "cast_srd_mirage_arcane", arguments, ["_mirage_summary_rows", "_mirage_detail_rows"]
            ),
            _loop(
                "_mirage_summary_rows",
                "_mirage_cast_row",
                [_push("_spell_summary_rows", "_mirage_cast_row")],
            ),
            _loop(
                "_mirage_detail_rows",
                "_mirage_cast_row",
                [_push("_spell_detail_rows", "_mirage_cast_row")],
            ),
        ],
    )
    found = False

    def replace(effects):
        nonlocal found
        for i, effect in enumerate(effects):
            if "_spell_definition.family == 'mirage_spell'" in effect.get("condition", ""):
                owned = deepcopy(branch)
                owned["condition"] = effect["condition"]
                effects[i] = owned
                found = True
            else:
                for key in ("on_true", "on_false"):
                    if key in effect:
                        replace(effect[key])
                if "for_each" in effect:
                    replace(effect["for_each"]["do"])

    replace(cast["effects"])
    if not found:
        cast["effects"].append(branch)
    changed["cast_spell"] = cast


def _effect_lists(effects):
    yield effects
    for effect in effects:
        for key in ("on_true", "on_false"):
            if key in effect:
                yield from _effect_lists(effect[key])
        if "for_each" in effect:
            yield from _effect_lists(effect["for_each"]["do"])


def _route_tools():
    intervals = _tool_scene(
        "Split the actual route at each live same-plane Mirage Square boundary.",
        [_array("start"), _array("end"), _parameter("plane")],
        [
            _set("_mirage_route_intervals", []),
            _set("_mirage_route_ids", "state.mirage_source_ids"),
            _loop(
                "_mirage_route_ids",
                "_mirage_route_id",
                [
                    _call(
                        "check_srd_mirage_source",
                        {"mirage_id": "{_mirage_route_id}"},
                        ["_mirage_active", "_mirage_record"],
                    ),
                    _when(
                        "_mirage_active == true and _mirage_record.plane == plane",
                        [
                            _set(
                                "_mirage_route_volume",
                                {
                                    "kind": "'box'",
                                    "min": [
                                        "_mirage_record.corner[0]",
                                        "_mirage_record.corner[1]",
                                        "state.spatial_scene.map.bounds.min[2]",
                                    ],
                                    "max": [
                                        "_mirage_record.corner[0] + _mirage_record.side",
                                        "_mirage_record.corner[1] + _mirage_record.side",
                                        "state.spatial_scene.map.bounds.max[2]",
                                    ],
                                },
                            ),
                            _call(
                                "srd_information_segment_interval",
                                {
                                    "start": "{start}",
                                    "end": "{end}",
                                    "volume": "{_mirage_route_volume}",
                                },
                                ["_segment_hit", "_segment_entry", "_segment_exit"],
                            ),
                            _when(
                                "_segment_hit == true",
                                [
                                    _push(
                                        "_mirage_route_intervals",
                                        {
                                            "entry": "_segment_entry",
                                            "exit": "_segment_exit",
                                            "id": "_mirage_route_id",
                                        },
                                    )
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    point = _tool_scene(
        "Apply the latest actual Mirage ground choice only to supported ground.",
        [_array("point"), _parameter("plane"), _optional("enabled", "boolean", True)],
        [
            _set("_mirage_ground_clear", False),
            _set("_mirage_ground_difficult", False),
            _set("_mirage_ground_selected", None),
            _when(
                "enabled == true and state.has_mirage_spells == true",
                [
                    _set("_mirage_point_ids", "state.mirage_source_ids"),
                    _loop(
                        "_mirage_point_ids",
                        "_mirage_point_id",
                        [
                            _call(
                                "check_srd_mirage_source",
                                {"mirage_id": "{_mirage_point_id}"},
                                ["_mirage_active", "_mirage_record"],
                            ),
                            _when(
                                "_mirage_active == true and _mirage_record.plane == plane and "
                                + _inside("point"),
                                [_set("_mirage_ground_selected", "_mirage_point_id")],
                            ),
                        ],
                    ),
                    _when(
                        "_mirage_ground_selected != null",
                        [
                            *_record("_mirage_ground_selected"),
                            _when(
                                "_mirage_record.ground_mode != 'unchanged'",
                                [
                                    _call(
                                        "qualify_srd_environment_ground",
                                        {"point": "{point}", "plane": "{plane}"},
                                    ),
                                    _set(
                                        "_mirage_ground_clear",
                                        "_mirage_record.ground_mode == 'clear'",
                                    ),
                                    _set(
                                        "_mirage_ground_difficult",
                                        "_mirage_record.ground_mode == 'difficult'",
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    return {
        "collect_srd_mirage_route_intervals": intervals,
        "resolve_srd_mirage_ground_point": point,
    }


def bind_runtime_consumers(text):
    """Join exact ground choices before magical terrain and leave the one movement spend intact."""
    tools = _load_runtime(text)["tools"]
    changed = {}
    cost = deepcopy(tools["srd_information_movement_cost"])
    if not any(p["name"] == "movement_mode" for p in cost["parameters"]):
        cost["parameters"].append(_optional("movement_mode", default="walk", model_hidden=True))
    cost["effects"][:] = [
        e
        for e in cost["effects"]
        if e.get("condition")
        != (
            "lasting_state.has_mirage_spells == true and movement_mode == 'walk' and "
            "ignore_ground_environments != true"
        )
    ]
    cutoff = next(
        i
        for i, e in enumerate(cost["effects"])
        if e.get("sort", {}).get("collection") == "_route_cuts"
    )
    cost["effects"][cutoff:cutoff] = [
        _when(
            "lasting_state.has_mirage_spells == true and movement_mode == 'walk' and "
            "ignore_ground_environments != true",
            [
                _call(
                    "collect_srd_mirage_route_intervals",
                    {"start": "{start}", "end": "{end}", "plane": "{plane}"},
                    ["_mirage_route_intervals"],
                ),
                _loop(
                    "_mirage_route_intervals",
                    "_mirage_cost_interval",
                    [
                        _push("_route_cuts", {"t": "_mirage_cost_interval.entry"}),
                        _push("_route_cuts", {"t": "_mirage_cost_interval.exit"}),
                    ],
                ),
            ],
        )
    ]
    visited = set()
    for effects in _effect_lists(cost["effects"]):
        if id(effects) in visited:
            continue
        visited.add(id(effects))
        prior_arguments = {
            e["params"].get("enabled", "").strip("{}")
            for e in effects
            if e.get("call") == "resolve_srd_mirage_ground_point"
        }
        effects[:] = [
            e
            for e in effects
            if e.get("call") != "resolve_srd_mirage_ground_point"
            and e.get("set", {}).get("field") != "_mirage_ground_enabled"
            and not (
                e.get("set", {}).get("field") in prior_arguments
                and e.get("set", {}).get("value")
                == "movement_mode == 'walk' and ignore_ground_environments != true"
            )
        ]
        for i, effect in enumerate(list(effects)):
            if effect.get("set", {}).get("field") == "_route_piece_difficult":
                value = effect["set"]["value"]
                if isinstance(value, str) and ("base_difficult" in value):
                    effects[i:i] = [
                        _set(
                            "_mirage_ground_enabled",
                            "movement_mode == 'walk' and ignore_ground_environments != true",
                        ),
                        _call(
                            "resolve_srd_mirage_ground_point",
                            {
                                "point": "{_route_point}",
                                "plane": "{plane}",
                                "enabled": "{_mirage_ground_enabled}",
                            },
                            ["_mirage_ground_clear", "_mirage_ground_difficult"],
                        ),
                    ]
                    effect["set"]["value"] = (
                        "(base_difficult == true and _mirage_ground_clear != true) or "
                        "_mirage_ground_difficult == true"
                    )
            if (
                "_route_zone.metadata.difficult == true" in effect.get("condition", "")
                and "_mirage_ground_clear" not in effect["condition"]
            ):
                effect["condition"] += (
                    " and (_mirage_ground_clear != true or "
                    "_route_zone.metadata.ground_environment == true or "
                    "_route_zone.metadata.source_spell != null or "
                    "_route_zone.metadata.source_environment_id != null or "
                    "_route_zone.metadata.plant_growth == true)"
                )
    changed["srd_information_movement_cost"] = cost
    for name in ("move_srd_scene_creature_node", "move_srd_scene_creature_environment_segment"):
        if name not in tools:
            continue
        tool = deepcopy(tools[name])
        for effects in _effect_lists(tool["effects"]):
            for effect in effects:
                if effect.get("call") == "srd_information_movement_cost":
                    effect["params"]["movement_mode"] = "{mode}"
        changed[name] = tool
    _normalize(changed)
    return _replace_tools(text, changed)
