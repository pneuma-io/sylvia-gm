"""Illusion sources own actual proxies, observer recognition and later Action consumers."""

from copy import deepcopy

import yaml

if __package__:
    from .srd_scene_relations import POSITION_EXPORTS, _array, _spatial, _tool_scene
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _RuntimeDumper,
        _set,
        _tool,
        _when,
    )
    from .srd_timed_senses import _lookup, _loop, _optional, _public, _push, _replace_tools
else:
    from srd_scene_relations import POSITION_EXPORTS, _array, _spatial, _tool_scene
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _RuntimeDumper,
        _set,
        _tool,
        _when,
    )
    from srd_timed_senses import _lookup, _loop, _optional, _public, _push, _replace_tools


SOURCE = {
    "silent_image": (162, "Silent Image", "Simulacrum"),
    "major_image": (147, "Major Image", "Mass Cure Wounds"),
    "minor_illusion": (149, "Minor Illusion", "Mirage Arcane"),
    "programmed_illusion": (156, "Programmed Illusion", "Project Image"),
    "mislead": (150, "Mislead", "Misty Step"),
}
PUBLIC_TOOLS = {
    "observe_srd_illusion",
    "study_srd_illusion",
    "touch_srd_illusion",
    "study_srd_illusion_creature",
    "control_srd_illusion",
    "trigger_srd_programmed_illusion",
    "dismiss_srd_illusion_spell",
    "perceive_srd_mislead_double",
}
PROFILES = {}
for _spell, _level, _range, _rounds, _concentration, _cube, _mode in (
    ("silent_image", 1, 60, 100, True, 15, "visual"),
    ("major_image", 3, 120, 100, True, 20, "sensory"),
    ("minor_illusion", 0, 30, 10, False, 5, "minor"),
    ("programmed_illusion", 6, 120, 0, False, 30, "programmed"),
    ("mislead", 5, 0, 600, True, 0, "double"),
):
    PROFILES[_spell] = {
        "family": "illusion_spell",
        "base_level": _level,
        "casting_time": "action",
        "range_feet": _range,
        "effect_rounds": _rounds,
        "concentration": _concentration,
        "cube_feet": _cube,
        "illusion_mode": _mode,
    }
    if _rounds:
        PROFILES[_spell]["duration_rounds"] = _rounds

ACTIVE = (
    "caster_state.is_alive == true and not ('incapacitated' in caster_state.conditions or "
    "'paralyzed' in caster_state.conditions or 'petrified' in caster_state.conditions or "
    "'stunned' in caster_state.conditions or 'unconscious' in caster_state.conditions)"
)
PENDING = "state.pending_srd_action_stack == null or len(state.pending_srd_action_stack) == 0"
RECORD_EXPORTS = ("_illusion_record", "_illusion_active", "_illusion_perceptible")


def validate_profiles(definitions):
    """Bind all promoted records to the complete pinned source rather than supplied quantities."""
    for spell, definition in definitions.items():
        if definition.get("family") == "illusion_spell" and definition != PROFILES.get(spell):
            raise SystemExit(f"Illusion source profile differs: {spell}")


def _schema():
    properties = {}
    for profile in PROFILES.values():
        for key, value in profile.items():
            if key != "family":
                properties[key] = {
                    "type": "boolean"
                    if type(value) is bool
                    else "integer"
                    if type(value) is int
                    else "string"
                }
    return {
        "type": "object",
        "properties": properties,
        "required": [key for key in properties if key != "duration_rounds"],
        "additionalProperties": False,
    }


def _record_tools():
    """Keep finite concentration, independent permanent sources and performance clocks separate."""
    check = _tool(
        "Check an exact installed illusion and calculate its current performance state.",
        [
            _parameter("record", "object", from_state="state.illusion_spell_sources"),
            _entity("caster_state"),
        ],
        [
            _set(
                "_illusion_active",
                "record.installed == true and "
                "(record.expires_tick == null or state.lasting_tick < record.expires_tick)",
            ),
            _when(
                "record.concentration == true",
                [
                    _set(
                        "_illusion_active",
                        "_illusion_active == true and " + ACTIVE + " and "
                        "caster_state.concentration_spell_id == record.spell and "
                        "caster_state.concentration_effect_family == 'illusion_spell' and "
                        "caster_state.illusion_concentration_id == record.id",
                    )
                ],
            ),
            _set("_illusion_perceptible", "_illusion_active"),
            _when(
                "record.spell == 'programmed_illusion'",
                [
                    _set(
                        "_illusion_perceptible",
                        "_illusion_active == true and record.activated_tick != null "
                        "and state.lasting_tick >= record.activated_tick and "
                        "state.lasting_tick < record.activated_tick + "
                        "record.performance_rounds * 2",
                    )
                ],
            ),
        ],
    )
    collect = _tool(
        "Select only the actual saved source and its exact caster.",
        [_parameter("illusion_id")],
        [
            _require(
                "state.illusion_spell_sources != null and "
                "illusion_id in state.illusion_spell_sources",
                "Select a saved illusion source.",
            ),
            _set("_illusion_record", "state.illusion_spell_sources[illusion_id]"),
            _call(
                "check_srd_illusion_record",
                {
                    "record": "{_illusion_record}",
                    "caster_state": "{_illusion_record.caster}",
                },
                ["_illusion_active", "_illusion_perceptible"],
            ),
        ],
    )
    retire = _tool_scene(
        "Retire only this source's proxy and Invisible grant, preserving independent sources.",
        [_parameter("illusion_id"), _optional("clear_concentration", "boolean", True)],
        [
            _call("collect_srd_illusion_source", {"illusion_id": "{illusion_id}"}, RECORD_EXPORTS),
            _set("state.illusion_spell_sources.{illusion_id}.installed", False),
            _set("state.illusion_spell_sources.{illusion_id}.invisible_active", False),
            _call("deactivate_srd_information_proxy", {"proxy_id": "{_illusion_record.proxy_id}"}),
            _when(
                "_illusion_record.spell == 'mislead'",
                [
                    _call(
                        "sync_srd_illumination_invisible",
                        {"actor_state": "{_illusion_record.caster}"},
                    ),
                ],
            ),
            _when(
                "clear_concentration == true and _illusion_record.concentration == true",
                [
                    _call(
                        "clear_srd_illusion_concentration",
                        {
                            "caster_state": "{_illusion_record.caster}",
                            "illusion_id": "{illusion_id}",
                        },
                    ),
                ],
            ),
        ],
    )
    clear = _tool(
        "Clear concentration only when it still belongs to this exact illusion source.",
        [_entity("caster_state"), _parameter("illusion_id")],
        [
            _when(
                "caster_state.concentration_effect_family == 'illusion_spell' and "
                "caster_state.illusion_concentration_id == illusion_id",
                [
                    *[
                        _set("caster_state." + field, None)
                        for field in (
                            "concentration_spell_id",
                            "concentration_effect_family",
                            "concentration_target_state_id",
                            "concentration_started_tick",
                            "concentration_expires_tick",
                            "illusion_concentration_id",
                        )
                    ],
                    _set("caster_state.concentration_target_state_ids", []),
                ],
            )
        ],
    )
    refresh = _tool(
        "Expire exact sources and retain the cheap guard from actual remaining installed sources.",
        [],
        [
            _when(
                "state.has_illusion_spells == true",
                [
                    _set("_illusion_refresh_ids", "state.illusion_spell_source_ids"),
                    _set("_illusion_remaining_ids", []),
                    _loop(
                        "_illusion_refresh_ids",
                        "_illusion_refresh_id",
                        [
                            _call(
                                "collect_srd_illusion_source",
                                {
                                    "illusion_id": "{_illusion_refresh_id}",
                                },
                                RECORD_EXPORTS,
                            ),
                            {
                                "condition": "_illusion_active == true",
                                "on_true": [
                                    _push("_illusion_remaining_ids", "_illusion_refresh_id")
                                ],
                                "on_false": [
                                    _call(
                                        "retire_srd_illusion_source",
                                        {"illusion_id": "{_illusion_refresh_id}"},
                                    )
                                ],
                            },
                        ],
                    ),
                    _set("state.illusion_spell_source_ids", "_illusion_remaining_ids"),
                    _set("state.has_illusion_spells", "len(_illusion_remaining_ids) > 0"),
                ],
            )
        ],
    )
    return {
        "check_srd_illusion_record": check,
        "collect_srd_illusion_source": collect,
        "retire_srd_illusion_source": retire,
        "clear_srd_illusion_concentration": clear,
        "refresh_srd_illusion_spells": refresh,
    }


def _invisibility_tools():
    """End the caster's Invisible grant without ending its independently lasting double."""
    check = _tool(
        "Read exact active Mislead Invisible sources without inferring them from generic immunity.",
        [_parameter("subject")],
        [
            _set("_mislead_invisible", False),
            _when(
                "state.has_illusion_spells == true",
                [
                    _set("_mislead_source_ids", "state.illusion_spell_source_ids"),
                    _loop(
                        "_mislead_source_ids",
                        "_mislead_source_id",
                        [
                            _set(
                                "_mislead_source",
                                "state.illusion_spell_sources[_mislead_source_id]",
                            ),
                            _when(
                                "_mislead_source.spell == 'mislead' and _mislead_source.caster "
                                "== subject and "
                                "_mislead_source.invisible_active == true",
                                [
                                    _call(
                                        "check_srd_illusion_record",
                                        {
                                            "record": "{_mislead_source}",
                                            "caster_state": "{_mislead_source.caster}",
                                        },
                                        ["_illusion_active"],
                                    ),
                                    _when(
                                        "_illusion_active == true",
                                        [_set("_mislead_invisible", True)],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    end = _tool(
        "Retire only this caster's actual Invisible grant after its qualifying event.",
        [_entity("caster_state")],
        [
            _when(
                "state.has_illusion_spells == true",
                [
                    _set("_mislead_event_ids", "state.illusion_spell_source_ids"),
                    _set("_mislead_event_ended", False),
                    _loop(
                        "_mislead_event_ids",
                        "_mislead_event_id",
                        [
                            _set(
                                "_mislead_event_source",
                                "state.illusion_spell_sources[_mislead_event_id]",
                            ),
                            _when(
                                "_mislead_event_source.spell == 'mislead' and "
                                "_mislead_event_source.caster == _caster_state_id and "
                                "_mislead_event_source.invisible_active == true",
                                [
                                    _set(
                                        "state.illusion_spell_sources.{_mislead_event_id}.invis"
                                        "ible_active",
                                        False,
                                    ),
                                    _set("_mislead_event_ended", True),
                                ],
                            ),
                        ],
                    ),
                    _when(
                        "_mislead_event_ended == true",
                        [
                            _call(
                                "sync_srd_illumination_invisible",
                                {"actor_state": "{_caster_state_id}"},
                            ),
                        ],
                    ),
                ],
            )
        ],
    )
    return {
        "check_srd_mislead_invisible": check,
        "end_srd_mislead_damage_source": end,
        "end_srd_mislead_attack_source": deepcopy(end),
        "end_srd_mislead_cast_source": deepcopy(end),
    }


CAST_CHOICES = [
    _array("illusion_position", default=[0, 0, 0]),
    _array("illusion_size", default=[5, 5, 5]),
    _optional("illusion_lighting", "enum", "bright", enum_values=["bright", "dim", "darkness"]),
    _optional("illusion_description", default=""),
    _optional("illusion_minor_mode", "enum", "image", enum_values=["image", "sound"]),
    _optional(
        "illusion_subject_kind",
        "enum",
        "unqualified",
        enum_values=["unqualified", "object", "creature", "phenomenon"],
    ),
    _optional("illusion_opaque", "boolean", True),
    _optional("illusion_sound", default=""),
    _optional(
        "illusion_sound_volume", "enum", "normal", enum_values=["whisper", "normal", "scream"]
    ),
    _optional("illusion_smell", default=""),
    _optional("illusion_temperature", default=""),
    _optional("illusion_trigger", default=""),
    _optional("illusion_trigger_modality", "enum", "visual", enum_values=["visual", "auditory"]),
    _optional("illusion_performance_seconds", "integer", 300, minimum=1, maximum=300),
]


def _distance(start, end, name):
    call = _call("measure_srd_information_distance", {"start": start, "end": end})
    call["export_context"] = {name: "_scene_distance"}
    return call


def _action(actor="caster_state", magic=True):
    """Spend a real Action on the bound actor and fence pending ordinary operations."""
    return [
        _require(PENDING, "Resolve the pending action before another ordinary action."),
        _require(ACTIVE.replace("caster_state", actor), "An Incapacitated creature cannot act."),
        _call(
            "check_srd_active_control_action",
            {"actor_state": "{_" + actor + "_id}", "action_kind": "action", "magic_action": magic},
        ),
        _when(
            "combat.encounter_active == true",
            [
                _require(
                    f"combat.turn_active == true and combat.active_actor == _{actor}_id",
                    "Act on this creature's current turn.",
                ),
                _call("spend_srd_action", {"magic_action": magic}),
            ],
        ),
    ]


def _proxy_tools():
    """Illusory volumes are passable entities in the existing scene, with no physical effects."""
    create = _tool_scene(
        "Install one source's intangible scene volume.",
        [
            _parameter("illusion_id"),
            _entity("caster_state"),
            _array("position"),
            _array("size"),
            _parameter("plane"),
            _parameter("spell"),
        ],
        [
            _set("_illusion_proxy_id", "illusion_id"),
            _set(
                "_illusion_proxy",
                {
                    "id": "illusion_id",
                    "map_id": "state.spatial_scene.map.id",
                    "position": "position",
                    "footprint": {
                        "kind": "'box'",
                        "half_extents": ["size[0] * 0.5", "size[1] * 0.5"],
                    },
                    "height": "size[2]",
                    "facing": [1, 0],
                    "active": True,
                    "movement": "'passable'",
                    "visibility": "'clear'",
                    "metadata": {
                        "illusion_proxy": "illusion_id",
                        "source_ref": "spell",
                        "source_caster": "_caster_state_id",
                        "plane": "plane",
                        "lighting": "'bright'",
                        "invulnerable": True,
                        "intangible": True,
                    },
                },
            ),
            _set("_illusion_scene_entities", "state.spatial_scene.entities"),
            _push("_illusion_scene_entities", "_illusion_proxy"),
            _set("state.spatial_scene.entities", "_illusion_scene_entities"),
            _set("state.spatial_scene.map.revision", "state.spatial_scene.map.revision + 1"),
        ],
    )
    volume = _tool_scene(
        "Derive the illusion volume only from its actual admitted proxy.",
        [_parameter("illusion_id")],
        [
            _call("srd_information_scene_entity", {"subject": "{illusion_id}"}, POSITION_EXPORTS),
            _require(
                "_info_scene_entity.metadata.illusion_proxy == illusion_id",
                "Select an actual illusion proxy.",
            ),
            _set("_illusion_position", "_info_position"),
            _set("_illusion_plane", "_info_scene_entity.metadata.plane"),
            _set(
                "_illusion_volume",
                {
                    "kind": "'box'",
                    "min": [
                        "_info_position[0] - _info_scene_entity.footprint.half_extents[0] * 2 "
                        "* 0.5",
                        "_info_position[1] - _info_scene_entity.footprint.half_extents[1] * 2 "
                        "* 0.5",
                        "_info_position[2]",
                    ],
                    "max": [
                        "_info_position[0] + _info_scene_entity.footprint.half_extents[0] * 2 "
                        "* 0.5",
                        "_info_position[1] + _info_scene_entity.footprint.half_extents[1] * 2 "
                        "* 0.5",
                        "_info_position[2] + _info_scene_entity.height",
                    ],
                },
            ),
        ],
    )
    return {"create_srd_illusion_proxy": create, "resolve_srd_illusion_volume": volume}


def _casting_tool(tools):
    """Install qualified image geometry and save the actual casting DC and exact lifetime."""
    major_sight = [
        _call(
            "resolve_srd_information_light",
            {
                "point": "{illusion_position}",
                "plane": "{_illusion_cast_plane}",
                "ambient": "{illusion_lighting}",
            },
            ["_information_light"],
        ),
        _call(
            "resolve_srd_sight",
            {
                "observer_state": "{_caster_state_id}",
                "distance": "{ceil(_illusion_cast_distance)}",
                "lighting": "{_information_light}",
                "obstruction": "clear",
            },
            ["_sight_can_see"],
        ),
        _require("_sight_can_see == true", "Major Image's origin must be seen."),
    ]
    if "resolve_srd_illumination_point_sight" in tools:
        major_sight = [
            _call(
                "resolve_srd_illumination_point_sight",
                {"observer_state": "{_caster_state_id}", "point": "{illusion_position}"},
                ["_illumination_point_can_see"],
            ),
            _require("_illumination_point_can_see == true", "Major Image's origin must be seen."),
        ]
    effects = [
        _lookup("spell", "_illusion_profile"),
        _require(
            "illusion_description != ''",
            "Describe the selected illusion's fictional appearance or sound.",
        ),
        _when("state.lasting_tick == null", [_set("state.lasting_tick", 0)]),
        _when(
            "state.illusion_spell_sources == null",
            [_set("state.illusion_spell_sources", {}), _set("state.illusion_spell_source_ids", [])],
        ),
        _require(
            "len(state.illusion_spell_source_ids) < 64",
            "Retire old illusion sources before creating another.",
        ),
        _when("state.illusion_cast_serial == null", [_set("state.illusion_cast_serial", 0)]),
        _set("state.illusion_cast_serial", "state.illusion_cast_serial + 1"),
        _call(
            "record_srd_result_text",
            {"text": "{_caster_state_id}:{spell}:{state.illusion_cast_serial}"},
            ["_recorded_result_text"],
        ),
        _set("_illusion_id", "_recorded_result_text"),
        _call("srd_information_scene_entity", {"subject": "{_caster_state_id}"}, POSITION_EXPORTS),
        _set("_illusion_cast_origin", "_info_position"),
        _set("_illusion_cast_plane", "_info_scene_entity.metadata.plane"),
        _require("_illusion_cast_plane != null", "The caster needs its actual scene plane."),
        _set("_illusion_cast_position", "illusion_position"),
        _set("_illusion_cast_size", "illusion_size"),
        _set("_illusion_concentration", "_illusion_profile.concentration"),
        _set("_illusion_expires", None),
        _when(
            "_illusion_profile.effect_rounds > 0",
            [_set("_illusion_expires", "state.lasting_tick + _illusion_profile.effect_rounds * 2")],
        ),
        _when(
            "spell == 'major_image' and slot_level >= 4",
            [_set("_illusion_concentration", False), _set("_illusion_expires", None)],
        ),
        _when(
            "spell == 'mislead'",
            [
                _set("_illusion_cast_position", "_illusion_cast_origin"),
                {
                    "condition": "_info_scene_entity.footprint.kind == 'box'",
                    "on_true": [
                        _set(
                            "_illusion_cast_size",
                            [
                                "_info_scene_entity.footprint.half_extents[0] * 2",
                                "_info_scene_entity.footprint.half_extents[1] * 2",
                                "_info_scene_entity.height",
                            ],
                        ),
                    ],
                    "on_false": [
                        _set(
                            "_illusion_cast_size",
                            [
                                "_info_scene_entity.footprint.radius * 2",
                                "_info_scene_entity.footprint.radius * 2",
                                "_info_scene_entity.height",
                            ],
                        ),
                    ],
                },
            ],
        ),
        _when(
            "spell != 'mislead'",
            [
                _require(
                    "illusion_size[0] > 0 and illusion_size[1] > 0 and illusion_size[2] > 0 "
                    "and illusion_size[0] <= _illusion_profile.cube_feet and illusion_size[1] "
                    "<= _illusion_profile.cube_feet and illusion_size[2] <= "
                    "_illusion_profile.cube_feet",
                    "The illusion must fit its source-defined Cube.",
                ),
                _distance(
                    "{_illusion_cast_origin}", "{illusion_position}", "_illusion_cast_distance"
                ),
                _require(
                    "_illusion_cast_distance <= _illusion_profile.range_feet",
                    "The chosen actual origin is outside spell range.",
                ),
                _call(
                    "srd_information_barriers",
                    {
                        "start": "{_illusion_cast_origin}",
                        "end": "{illusion_position}",
                        "rule": "solid",
                    },
                    ["_information_blocked"],
                ),
                _require(
                    "_information_blocked != true",
                    "A solid barrier blocks this spell's point of origin.",
                ),
            ],
        ),
        _when(
            "spell == 'major_image'",
            major_sight,
        ),
        _when(
            "spell == 'minor_illusion' and illusion_minor_mode == 'image'",
            [
                _require(
                    "illusion_subject_kind == 'object'",
                    "Minor Illusion's image must be a source-qualified object.",
                ),
            ],
        ),
        _when(
            "spell == 'silent_image' or (spell == 'minor_illusion' and illusion_minor_mode == "
            "'image')",
            [
                _require(
                    "illusion_sound == '' and illusion_smell == '' and illusion_temperature == ''",
                    "This image cannot create the other sensory effects.",
                ),
            ],
        ),
        _when(
            "spell == 'minor_illusion' and illusion_minor_mode == 'sound'",
            [
                _require(
                    "illusion_sound != '' and illusion_smell == '' and illusion_temperature == "
                    "'' and illusion_opaque == false",
                    "A sound illusion needs its sound and has no visual or other sensory effects.",
                ),
            ],
        ),
        _when(
            "spell == 'programmed_illusion'",
            [
                _require(
                    "illusion_trigger != '' and illusion_smell == '' and illusion_temperature "
                    "== ''",
                    "Programmed Illusion needs a visual or auditory trigger and only "
                    "visual/auditory performance.",
                ),
            ],
        ),
        _when(
            "spell == 'minor_illusion'",
            [
                _set("_illusion_recast_ids", "state.illusion_spell_source_ids"),
                _loop(
                    "_illusion_recast_ids",
                    "_illusion_recast_id",
                    [
                        _set(
                            "_illusion_recast_source",
                            "state.illusion_spell_sources[_illusion_recast_id]",
                        ),
                        _when(
                            "_illusion_recast_source.installed == true and "
                            "_illusion_recast_source.spell == 'minor_illusion' and "
                            "_illusion_recast_source.caster == _caster_state_id",
                            [
                                _call(
                                    "retire_srd_illusion_source",
                                    {"illusion_id": "{_illusion_recast_id}"},
                                )
                            ],
                        ),
                    ],
                ),
            ],
        ),
        _when(
            "_illusion_concentration == true and caster_state.concentration_spell_id != null",
            [
                _call(
                    "end_srd_concentration_node",
                    {
                        "source_state": "{_caster_state_id}",
                        "target_state": "{caster_state.concentration_target_state_id}",
                    },
                )
            ],
        ),
        _set(
            "_illusion_record",
            {
                "id": "_illusion_id",
                "spell": "spell",
                "caster": "_caster_state_id",
                "installed": True,
                "started_tick": "state.lasting_tick",
                "expires_tick": None,
                "concentration": "_illusion_concentration",
                "proxy_id": "_illusion_id",
                "save_dc": "save_dc",
                "recognized": [],
                "mode": "illusion_minor_mode",
                "subject_kind": "illusion_subject_kind",
                "opaque": "illusion_opaque",
                "description": "illusion_description",
                "sound": "illusion_sound",
                "sound_volume": "illusion_sound_volume",
                "smell": "illusion_smell",
                "temperature": "illusion_temperature",
                "trigger": "illusion_trigger",
                "trigger_modality": "illusion_trigger_modality",
                "activated_tick": None,
                "performance_rounds": "illusion_performance_seconds * 6 ** -1",
                "invisible_active": "spell == 'mislead'",
                "lighting": "illusion_lighting",
            },
        ),
        _when(
            "_illusion_profile.effect_rounds > 0 and not (spell == 'major_image' and "
            "slot_level >= 4)",
            [
                _set("_illusion_expiry_patch", {"expires_tick": "_illusion_expires"}),
                _set("_illusion_record", "merge(_illusion_record, _illusion_expiry_patch)"),
            ],
        ),
        _set("state.illusion_spell_sources.{_illusion_id}", "_illusion_record"),
        _set("_illusion_installed_ids", "state.illusion_spell_source_ids"),
        _push("_illusion_installed_ids", "_illusion_id"),
        _set("state.illusion_spell_source_ids", "_illusion_installed_ids"),
        _set("state.has_illusion_spells", True),
        _when(
            "_illusion_concentration == true",
            [
                _set("caster_state.concentration_spell_id", "spell"),
                _set("caster_state.concentration_effect_family", "'illusion_spell'"),
                _set("caster_state.concentration_target_state_id", "_caster_state_id"),
                _set("caster_state.concentration_target_state_ids", []),
                _set("caster_state.concentration_started_tick", "state.lasting_tick"),
                _set("caster_state.concentration_expires_tick", "_illusion_expires"),
                _set("caster_state.concentration_save_modifier", "concentration_modifier"),
                _set("caster_state.illusion_concentration_id", "_illusion_id"),
            ],
        ),
        _call(
            "create_srd_illusion_proxy",
            {
                "illusion_id": "{_illusion_id}",
                "caster_state": "{_caster_state_id}",
                "position": "{_illusion_cast_position}",
                "size": "{_illusion_cast_size}",
                "plane": "{_illusion_cast_plane}",
                "spell": "{spell}",
            },
        ),
        _when(
            "spell == 'mislead'",
            [
                _when(
                    "caster_state.illumination_invisible_managed != true",
                    [
                        _set(
                            "caster_state.illumination_invisible_native",
                            "'invisible' in caster_state.conditions",
                        ),
                        _set("caster_state.illumination_invisible_managed", True),
                    ],
                ),
                _call("sync_srd_illumination_invisible", {"actor_state": "{_caster_state_id}"}),
            ],
        ),
        _set(
            "_spell_summary_rows",
            [{"label": "'Effect'", "value": "'Source-owned illusion installed'"}],
        ),
        _set("_spell_detail_rows", [{"label": "'Illusion source'", "value": "_illusion_id"}]),
    ]
    return _tool_scene(
        "Install the reviewed illusion's actual scene source.",
        [
            _entity("caster_state"),
            _parameter("spell"),
            _parameter("slot_level", "integer"),
            _parameter("save_dc", "integer"),
            _parameter("concentration_modifier", "integer"),
            *deepcopy(CAST_CHOICES),
        ],
        effects,
    )


def _recognition_tools():
    """An observer recognizes a particular source through actual Study, touch or Truesight."""
    recognize = _tool(
        "Retain recognition for this observer and this exact installed source.",
        [
            _parameter("illusion_id"),
            _parameter("observer"),
        ],
        [
            _call("collect_srd_illusion_source", {"illusion_id": "{illusion_id}"}, RECORD_EXPORTS),
            _set("_illusion_known", "_illusion_record.recognized"),
            _when(
                "observer not in _illusion_known",
                [
                    _push("_illusion_known", "observer"),
                    _set(
                        "state.illusion_spell_sources.{illusion_id}.recognized", "_illusion_known"
                    ),
                ],
            ),
        ],
    )
    perceive = _tool_scene(
        "Resolve actual visual and admitted auditory perception of a source.",
        [
            _entity("observer_state"),
            _parameter("illusion_id"),
        ],
        [
            _call("collect_srd_illusion_source", {"illusion_id": "{illusion_id}"}, RECORD_EXPORTS),
            _call(
                "resolve_srd_illusion_volume",
                {"illusion_id": "{illusion_id}"},
                ["_illusion_position", "_illusion_plane"],
            ),
            _call(
                "srd_information_scene_entity",
                {"subject": "{_observer_state_id}"},
                POSITION_EXPORTS,
            ),
            _set("_illusion_observer_position", "_info_position"),
            _set("_illusion_observer_entity", "_info_scene_entity"),
            _distance("{_info_position}", "{_illusion_position}", "_illusion_distance"),
            _call(
                "srd_information_barriers",
                {
                    "start": "{_illusion_observer_position}",
                    "end": "{_illusion_position}",
                    "rule": "solid",
                },
                ["_information_blocked"],
            ),
            _set(
                "_illusion_physical_clear",
                "_information_blocked != true and _illusion_plane == "
                "_illusion_observer_entity.metadata.plane",
            ),
            _call(
                "resolve_srd_information_light",
                {
                    "point": "{_illusion_position}",
                    "plane": "{_illusion_plane}",
                    "ambient": "{_illusion_record.lighting}",
                },
                ["_information_light"],
            ),
            _call(
                "resolve_srd_sight",
                {
                    "observer_state": "{_observer_state_id}",
                    "distance": "{ceil(_illusion_distance)}",
                    "lighting": "{_information_light}",
                    "obstruction": "clear",
                    "visual_illusion": True,
                },
                ["_sight_can_see", "_sight_illusion_transparent"],
            ),
            _set(
                "_illusion_can_see",
                "_illusion_perceptible == true and _illusion_physical_clear == true and "
                "_sight_can_see == true and not (_illusion_record.spell == 'minor_illusion' "
                "and _illusion_record.mode == 'sound')",
            ),
            _set(
                "_illusion_can_hear",
                "_illusion_perceptible == true and _illusion_plane == "
                "_illusion_observer_entity.metadata.plane and _illusion_record.sound != '' and "
                "not ('deafened' in observer_state.conditions) and "
                "_illusion_observer_entity.metadata.audible_sources != null and illusion_id in "
                "_illusion_observer_entity.metadata.audible_sources",
            ),
            _when(
                "_illusion_can_see == true and _sight_illusion_transparent == true",
                [
                    _call(
                        "recognize_srd_illusion_source",
                        {"illusion_id": "{illusion_id}", "observer": "{_observer_state_id}"},
                    ),
                    _set("_illusion_record", "state.illusion_spell_sources[illusion_id]"),
                ],
            ),
            _set("_illusion_recognized", "_observer_state_id in _illusion_record.recognized"),
            _set("_illusion_appearance", "''"),
            _set("_illusion_sound", "''"),
            _set("_illusion_other_senses", "''"),
            _when(
                "_illusion_can_see == true",
                [_set("_illusion_appearance", "_illusion_record.description")],
            ),
            _when(
                "_illusion_can_hear == true", [_set("_illusion_sound", "_illusion_record.sound")]
            ),
            _set("_illusion_visual_transparent", "_illusion_recognized == true"),
            _set("_illusion_sensory_quality", "'normal'"),
            _when(
                "_illusion_recognized == true and _illusion_record.spell == 'minor_illusion'",
                [_set("_illusion_sensory_quality", "'faint'")],
            ),
            _when(
                "_illusion_recognized == true and _illusion_record.spell == 'major_image'",
                [_set("_illusion_sensory_quality", "'faint'")],
            ),
            _when(
                "_illusion_recognized == true and _illusion_record.spell == 'programmed_illusion'",
                [_set("_illusion_sensory_quality", "'hollow'")],
            ),
            _when(
                "_illusion_perceptible == true and _illusion_record.spell == 'major_image' and "
                "_illusion_physical_clear == true",
                [
                    _call(
                        "record_srd_result_text",
                        {
                            "text": "Smell: {_illusion_record.smell}; temperature: "
                            "{_illusion_record.temperature}"
                        },
                        ["_recorded_result_text"],
                    ),
                    _set("_illusion_other_senses", "_recorded_result_text"),
                ],
            ),
        ],
    )
    visibility = _tool_scene(
        "Calculate observer-specific illusory opacity from actual scene ray intersections.",
        [
            _entity("observer_state"),
            _array("start"),
            _array("end"),
            _parameter("plane"),
            _optional("use_observer_senses", "boolean", True),
            _optional("subject_illusion_id", default=""),
        ],
        [
            _set("_illusion_visual_blocked", False),
            _when(
                "state.has_illusion_spells == true",
                [
                    _set("_sense_truesight", 0),
                    _when(
                        "use_observer_senses == true",
                        [
                            _call(
                                "resolve_srd_timed_senses",
                                {"target_state": "{_observer_state_id}"},
                                ["_sense_truesight"],
                            ),
                        ],
                    ),
                    _spatial(
                        "ray",
                        {
                            "map_id": "{state.spatial_scene.map.id}",
                            "origin": "{start}",
                            "destination": "{end}",
                            "include": ["entities"],
                            "max_intersections": 1024,
                        },
                        "_illusion_sight_ray",
                    ),
                    _require(
                        "_illusion_sight_ray.truncated != true",
                        "The scene ray exceeds the admitted intersection bound.",
                    ),
                    _distance("{start}", "{end}", "_illusion_sight_target_distance"),
                    _set(
                        "_illusion_blindsight",
                        "use_observer_senses == true and "
                        "observer_state.blindsight_feet != null and "
                        "observer_state.blindsight_feet >= _illusion_sight_target_distance",
                    ),
                    _set("_illusion_sight_hits", "_illusion_sight_ray.intersections"),
                    _loop(
                        "_illusion_sight_hits",
                        "_illusion_sight_hit",
                        [
                            _when(
                                "_illusion_sight_hit.metadata.illusion_proxy != null and "
                                "_illusion_sight_hit.metadata.illusion_proxy != "
                                "subject_illusion_id and "
                                "_illusion_sight_hit.metadata.plane == plane and "
                                "_illusion_sight_hit.metadata.illusion_proxy in "
                                "state.illusion_spell_sources",
                                [
                                    _set(
                                        "_illusion_sight_id",
                                        "_illusion_sight_hit.metadata.illusion_proxy",
                                    ),
                                    _call(
                                        "collect_srd_illusion_source",
                                        {"illusion_id": "{_illusion_sight_id}"},
                                        RECORD_EXPORTS,
                                    ),
                                    _when(
                                        "_illusion_blindsight != true and "
                                        "_illusion_perceptible == true and "
                                        "_illusion_record.opaque == true and "
                                        "_illusion_record.spell != 'mislead' and not "
                                        "(_illusion_record.spell == 'minor_illusion' and "
                                        "_illusion_record.mode == 'sound')",
                                        [
                                            _distance(
                                                "{start}",
                                                "{_illusion_sight_hit.point}",
                                                "_illusion_hit_distance",
                                            ),
                                            _when(
                                                "_sense_truesight > 0 and "
                                                "_sense_truesight >= _illusion_hit_distance "
                                                "and not ('blinded' in "
                                                "observer_state.conditions)",
                                                [
                                                    _call(
                                                        "recognize_srd_illusion_source",
                                                        {
                                                            "illusion_id": "{_illusion_sight_id}",
                                                            "observer": "{_observer_state_id}",
                                                        },
                                                    ),
                                                    _set(
                                                        "_illusion_record",
                                                        "state.illusion_spell_sources[_illusion"
                                                        "_sight_id]",
                                                    ),
                                                ],
                                            ),
                                            _when(
                                                "_observer_state_id not in "
                                                "_illusion_record.recognized",
                                                [_set("_illusion_visual_blocked", True)],
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
    touch = _tool_scene(
        "Physical interaction with an intangible image reveals it to the actual interactor.",
        [
            _entity("observer_state"),
            _parameter("illusion_id"),
        ],
        [
            _require(PENDING, "Resolve the pending action before ordinary interaction."),
            _require(
                ACTIVE.replace("caster_state", "observer_state"),
                "An Incapacitated creature cannot interact.",
            ),
            _call("collect_srd_illusion_source", {"illusion_id": "{illusion_id}"}, RECORD_EXPORTS),
            _require(
                "_illusion_perceptible == true and _illusion_record.spell != 'mislead' and not "
                "(_illusion_record.spell == 'minor_illusion' and _illusion_record.mode == "
                "'sound')",
                "This source has no physical-interaction reveal rule.",
            ),
            _call(
                "resolve_srd_illusion_volume",
                {"illusion_id": "{illusion_id}"},
                ["_illusion_volume", "_illusion_plane"],
            ),
            _call(
                "srd_information_scene_entity",
                {"subject": "{_observer_state_id}"},
                POSITION_EXPORTS,
            ),
            _set("_illusion_touch_origin", "_info_position"),
            _require(
                "_info_scene_entity.metadata.plane == _illusion_plane",
                "The image is on another plane.",
            ),
            _call(
                "srd_information_volume_point",
                {"origin": "{_illusion_touch_origin}", "volume": "{_illusion_volume}"},
                ["_volume_point"],
            ),
            _distance("{_illusion_touch_origin}", "{_volume_point}", "_illusion_touch_distance"),
            _set("_illusion_touch_reach", 5),
            _when(
                "observer_state.melee_reach != null",
                [_set("_illusion_touch_reach", "observer_state.melee_reach")],
            ),
            _require(
                "_illusion_touch_distance <= _illusion_touch_reach",
                "Move within actual reach to interact with the image.",
            ),
            _call(
                "srd_information_barriers",
                {"start": "{_illusion_touch_origin}", "end": "{_volume_point}", "rule": "solid"},
                ["_information_blocked"],
            ),
            _require(
                "_information_blocked != true", "A solid barrier prevents physical interaction."
            ),
            _call(
                "recognize_srd_illusion_source",
                {"illusion_id": "{illusion_id}", "observer": "{_observer_state_id}"},
            ),
            _set("_illusion_recognized", True),
        ],
    )
    return {
        "recognize_srd_illusion_source": recognize,
        "resolve_srd_illusion_perception": perceive,
        "check_srd_illusion_visibility": visibility,
        "touch_srd_illusion": _public(
            touch, "exploration", "Illusion interaction", [("Recognized", "_illusion_recognized")]
        ),
        "observe_srd_illusion": _public(
            deepcopy(perceive),
            "exploration",
            "Illusion observed",
            [
                ("Can see", "_illusion_can_see"),
                ("Can hear", "_illusion_can_hear"),
                ("Recognized", "_illusion_recognized"),
                ("Appearance", "_illusion_appearance"),
                ("Sound", "_illusion_sound"),
                ("Other senses", "_illusion_other_senses"),
                ("Visual transparent", "_illusion_visual_transparent"),
                ("Sensory quality", "_illusion_sensory_quality"),
            ],
        ),
    }


def _study_tool(tools):
    """Reuse the authentic Intelligence Investigation D20 contract and its saved spell DC."""
    study = deepcopy(tools["study_srd_action"])
    roll = next(deepcopy(e) for e in study["effects"] if e.get("call") == "d20_test")
    roll["params"].update(
        actor="{_observer_id}",
        actor_state="{_observer_state_id}",
        target_number="{_illusion_record.save_dc}",
        skill="investigation",
        proficiency_factor="{illusion_proficiency_factor}",
        circumstantial_modifier=0,
        advantage=False,
        disadvantage=False,
    )
    roll["export_context"] = {
        "_illusion_study_total": "_adjusted_total",
        "_illusion_study_outcome": "_outcome",
    }
    # The shared D20 owner retains native participant ability and proficiency calculation.
    parameters = [
        _parameter("observer", resolve="participant"),
        _entity("observer_state"),
        _parameter("illusion_id"),
        _parameter(
            "illusion_proficiency_factor",
            "enum",
            enum_values=["none", "half", "normal", "double"],
            model_derive={
                "kind": "participant_membership",
                "participant": "observer",
                "source": "illusion_skill",
                "field": "skill_proficiencies",
                "present": "normal",
                "absent": "none",
            },
        ),
        _parameter(
            "illusion_skill",
            "enum",
            enum_values=["investigation"],
            default="investigation",
            required=False,
            model_derive={"kind": "constant", "value": "investigation"},
        ),
    ]
    # Resolve the constant skill before its participant-membership derivation.
    parameters = parameters[:3] + [parameters[4], parameters[3]]
    parameters[1]["model_derive"] = {"kind": "copy", "source": "observer"}
    npc = _call(
        "d20_test",
        {
            "actor_state": "{_observer_state_id}",
            "kind": "ability_check",
            "ability": "intelligence",
            "skill": "investigation",
            "ability_modifier": "{observer_state.skill_check_modifiers.investigation}",
            "proficiency_bonus": 0,
            "proficiency_factor": "none",
            "circumstantial_modifier": 0,
            "target_number": "{_illusion_record.save_dc}",
            "advantage": False,
            "disadvantage": False,
        },
    )
    npc["export_context"] = {
        "_illusion_study_total": "_adjusted_total",
        "_illusion_study_outcome": "_outcome",
    }
    tool = _tool_scene(
        "Study a perceived image or sound with Intelligence Investigation against its actual "
        "saved DC.",
        parameters,
        [
            _call(
                "resolve_srd_illusion_perception",
                {"observer_state": "{_observer_state_id}", "illusion_id": "{illusion_id}"},
                ["_illusion_record", "_illusion_can_see", "_illusion_can_hear"],
            ),
            _require(
                "_illusion_record.spell != 'mislead' and (_illusion_can_see == true or "
                "_illusion_can_hear == true)",
                "Study requires a perceived source with the printed Investigation rule.",
            ),
            *_action("observer_state", False),
            {
                "condition": "observer != null",
                "on_true": [roll],
                "on_false": [
                    _require(
                        "observer_state.skill_check_modifiers.investigation != null",
                        "Record this creature's actual Investigation modifier before its Study "
                        "action.",
                    ),
                    npc,
                ],
            },
            _set("_illusion_study_success", "_illusion_study_outcome == 'success'"),
            _when(
                "_illusion_study_success == true",
                [
                    _call(
                        "recognize_srd_illusion_source",
                        {"illusion_id": "{illusion_id}", "observer": "{_observer_state_id}"},
                    )
                ],
            ),
        ],
    )
    return _public(
        tool,
        "exploration",
        "Illusion Study",
        [("Recognized", "_illusion_study_success"), ("Total", "_illusion_study_total")],
    )


def _control_tools(tools):
    """Move only controllable images using the actual caster's Action and current Speed."""
    speed_effects = deepcopy(tools["refresh_srd_movement_budget"]["effects"][0]["on_true"])
    speed_effects = [e for e in speed_effects if "combat." not in str(e)]
    speed = _tool(
        "Read current walking Speed from the shared movement projection, excluding Dash or "
        "spent movement.",
        [_entity("actor_state")],
        [
            _call(
                "project_srd_turn_modifiers",
                {"target_state": "{_actor_state_id}", "lasting_state": "state"},
            ),
            _call(
                "project_srd_timed_actor_mobility",
                {"target_state": "{_actor_state_id}", "lasting_state": "state"},
            ),
            *speed_effects,
            _set("_illusion_caster_speed", "_movement_speed"),
        ],
    )
    control = _public(
        _tool_scene(
            "Spend a Magic action to move and animate the selected actual image.",
            [
                _entity("caster_state"),
                _parameter("illusion_id"),
                _array("destination"),
                _optional("description", default=""),
                _optional("sound", default=""),
            ],
            [
                _call(
                    "collect_srd_illusion_source", {"illusion_id": "{illusion_id}"}, RECORD_EXPORTS
                ),
                _require(
                    "_illusion_active == true and _illusion_record.caster == _caster_state_id "
                    "and (_illusion_record.spell == 'silent_image' or _illusion_record.spell "
                    "== 'major_image' or _illusion_record.spell == 'mislead')",
                    "Control only this caster's active controllable illusion.",
                ),
                _call(
                    "resolve_srd_illusion_volume",
                    {"illusion_id": "{illusion_id}"},
                    ["_illusion_position", "_illusion_plane"],
                ),
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{_caster_state_id}"},
                    POSITION_EXPORTS,
                ),
                _require(
                    "_info_scene_entity.metadata.plane == _illusion_plane",
                    "The caster and image must share a plane.",
                ),
                _set("_illusion_control_caster_position", "_info_position"),
                _lookup("_illusion_record.spell", "_illusion_control_profile"),
                _distance(
                    "{_illusion_control_caster_position}",
                    "{destination}",
                    "_illusion_control_destination_range",
                ),
                _when(
                    "_illusion_record.spell != 'mislead'",
                    [
                        _require(
                            "_illusion_control_destination_range <= "
                            "_illusion_control_profile.range_feet",
                            "Move the image to a point within its caster's spell range.",
                        ),
                    ],
                ),
                _when(
                    "_illusion_record.spell == 'major_image'",
                    [
                        _distance(
                            "{_illusion_control_caster_position}",
                            "{_illusion_position}",
                            "_illusion_control_current_range",
                        ),
                        _require(
                            "_illusion_control_current_range <= 120",
                            "The caster must be within range of the current Major Image.",
                        ),
                    ],
                ),
                _when(
                    "_illusion_record.spell == 'mislead'",
                    [
                        _call(
                            "read_srd_illusion_caster_speed",
                            {"actor_state": "{_caster_state_id}"},
                            ["_illusion_caster_speed"],
                        ),
                        _distance(
                            "{_illusion_position}", "{destination}", "_illusion_control_moved"
                        ),
                        _require(
                            "_illusion_control_moved <= 2 * _illusion_caster_speed",
                            "The double moves up to twice the caster's current Speed per Magic "
                            "action.",
                        ),
                    ],
                ),
                _require(
                    "_illusion_record.spell != 'silent_image' or sound == ''",
                    "Silent Image cannot make sound.",
                ),
                *_action(),
                {
                    "spatial_mutation": {
                        "scene": "state.spatial_scene",
                        "request": {
                            "map_id": "{state.spatial_scene.map.id}",
                            "expected_revision": "{state.spatial_scene.map.revision}",
                            "target": {"kind": "entity", "id": "{illusion_id}"},
                            "patch": {"position": "{destination}"},
                        },
                    },
                    "store_as": "_illusion_moved",
                },
                _when(
                    "description != ''",
                    [_set("state.illusion_spell_sources.{illusion_id}.description", "description")],
                ),
                _when(
                    "sound != ''",
                    [_set("state.illusion_spell_sources.{illusion_id}.sound", "sound")],
                ),
                _set("_illusion_control_position", "destination"),
            ],
        ),
        "exploration",
        "Illusion controlled",
        [("Position", "_illusion_control_position")],
    )
    dismiss = _public(
        _tool(
            "Dismiss one finite time-span source without an Action when its caster can act.",
            [
                _entity("caster_state"),
                _parameter("illusion_id"),
            ],
            [
                _require(PENDING, "Resolve the pending action before ordinary dismissal."),
                _require(ACTIVE, "An Incapacitated caster cannot dismiss a spell."),
                _call(
                    "collect_srd_illusion_source", {"illusion_id": "{illusion_id}"}, RECORD_EXPORTS
                ),
                _require(
                    "_illusion_active == true and _illusion_record.caster == _caster_state_id "
                    "and _illusion_record.expires_tick != null",
                    "Select this caster's finite time-span effect; permanent illusions require "
                    "an actual ending effect.",
                ),
                _call("retire_srd_illusion_source", {"illusion_id": "{illusion_id}"}),
                _set("_illusion_dismissed", True),
            ],
        ),
        "exploration",
        "Illusion dismissed",
        [("Dismissed", "_illusion_dismissed")],
    )
    return {
        "read_srd_illusion_caster_speed": speed,
        "control_srd_illusion": control,
        "dismiss_srd_illusion_spell": dismiss,
    }


def _programmed_tools():
    """A saved real visual/auditory phenomenon triggers only within the calculated area distance."""
    trigger = _public(
        _tool_scene(
            "Activate the saved performance from an admitted source-qualified scene phenomenon.",
            [
                _parameter("illusion_id"),
                _parameter("event_subject"),
            ],
            [
                _require(PENDING, "Resolve the pending action before an ordinary event operation."),
                _call(
                    "collect_srd_illusion_source", {"illusion_id": "{illusion_id}"}, RECORD_EXPORTS
                ),
                _require(
                    "_illusion_active == true and _illusion_record.spell == 'programmed_illusion'",
                    "Select an installed Programmed Illusion.",
                ),
                _require(
                    "_illusion_record.activated_tick == null or state.lasting_tick >= "
                    "_illusion_record.activated_tick + _illusion_record.performance_rounds * 2 "
                    "+ 200",
                    "This performance is active or in its ten-minute dormancy.",
                ),
                _call(
                    "resolve_srd_illusion_volume",
                    {"illusion_id": "{illusion_id}"},
                    ["_illusion_volume", "_illusion_plane"],
                ),
                _call(
                    "srd_information_scene_entity", {"subject": "{event_subject}"}, POSITION_EXPORTS
                ),
                _require(
                    "_info_scene_entity.metadata.plane == _illusion_plane",
                    "The triggering phenomenon must share the image's plane.",
                ),
                _set("_illusion_event_entity", "_info_scene_entity"),
                _call(
                    "srd_information_volume_point",
                    {"origin": "{_info_position}", "volume": "{_illusion_volume}"},
                    ["_volume_point"],
                ),
                _distance(
                    "{_illusion_event_entity.position}",
                    "{_volume_point}",
                    "_illusion_event_distance",
                ),
                _require(
                    "_illusion_event_distance <= 30",
                    "The visual or auditory phenomenon is outside thirty feet of the actual area.",
                ),
                _set("_illusion_event_facts", None),
                _when(
                    "_illusion_record.trigger_modality == 'visual'",
                    [
                        _set(
                            "_illusion_event_facts",
                            "_illusion_event_entity.metadata.visual_phenomena",
                        )
                    ],
                ),
                _when(
                    "_illusion_record.trigger_modality == 'auditory'",
                    [
                        _set(
                            "_illusion_event_facts",
                            "_illusion_event_entity.metadata.auditory_phenomena",
                        )
                    ],
                ),
                _require(
                    "_illusion_event_facts != null and _illusion_record.trigger in "
                    "_illusion_event_facts",
                    "The admitted phenomenon does not match this source's saved "
                    "visual/auditory trigger.",
                ),
                _set(
                    "state.illusion_spell_sources.{illusion_id}.activated_tick",
                    "state.lasting_tick",
                ),
                _set("_illusion_activated", True),
            ],
        ),
        "exploration",
        "Programmed illusion triggered",
        [("Activated", "_illusion_activated")],
    )
    return {"trigger_srd_programmed_illusion": trigger}


def _movement_interaction_tool():
    """Actual movement through an image reveals it only to that actor."""
    return _tool_scene(
        "Recognize every actual image crossed by this actor's successful movement.",
        [
            _entity("actor_state"),
            _array("start"),
            _array("end"),
            _parameter("plane"),
        ],
        [
            _when(
                "state.has_illusion_spells == true",
                [
                    _set("_illusion_cross_ids", "state.illusion_spell_source_ids"),
                    _loop(
                        "_illusion_cross_ids",
                        "_illusion_cross_id",
                        [
                            _call(
                                "collect_srd_illusion_source",
                                {"illusion_id": "{_illusion_cross_id}"},
                                RECORD_EXPORTS,
                            ),
                            _when(
                                "_illusion_perceptible == true and _illusion_record.spell != "
                                "'mislead' and not (_illusion_record.spell == 'minor_illusion' "
                                "and _illusion_record.mode == 'sound')",
                                [
                                    _call(
                                        "resolve_srd_illusion_volume",
                                        {"illusion_id": "{_illusion_cross_id}"},
                                        ["_illusion_volume", "_illusion_plane"],
                                    ),
                                    _when(
                                        "_illusion_plane == plane",
                                        [
                                            _call(
                                                "srd_information_segment_interval",
                                                {
                                                    "start": "{start}",
                                                    "end": "{end}",
                                                    "volume": "{_illusion_volume}",
                                                },
                                                ["_segment_hit"],
                                            ),
                                            _when(
                                                "_segment_hit == true",
                                                [
                                                    _call(
                                                        "recognize_srd_illusion_source",
                                                        {
                                                            "illusion_id": "{_illusion_cross_id}",
                                                            "observer": "{_actor_state_id}",
                                                        },
                                                    )
                                                ],
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


def _proxy_image_visibility_tool(tools):
    """Sensor sight respects images without lending the Arcane Eye its caster's own senses."""
    proxy = deepcopy(tools["perceive_srd_information_proxy"])
    if "check_srd_illusion_visibility" in str(proxy["effects"]):
        return proxy
    hook = _when(
        "_remote_visible == true and record.modality != 'hearing' and "
        "state.has_illusion_spells == true",
        [
            _set("_illusion_remote_subject", "''"),
            _when(
                "_remote_entity.metadata.illusion_proxy != null",
                [
                    _set("_illusion_remote_subject", "_remote_entity.metadata.illusion_proxy"),
                    _call(
                        "collect_srd_illusion_source",
                        {"illusion_id": "{_illusion_remote_subject}"},
                        RECORD_EXPORTS,
                    ),
                    _set(
                        "_remote_visible",
                        "_illusion_perceptible == true and not "
                        "(_illusion_record.spell == 'minor_illusion' and "
                        "_illusion_record.mode == 'sound')",
                    ),
                ],
            ),
            _when(
                "_remote_visible == true",
                [
                    _set("_illusion_remote_use_caster_senses", "record.spell != 'arcane_eye'"),
                    _call(
                        "check_srd_illusion_visibility",
                        {
                            "observer_state": "{_caster_state_id}",
                            "start": "{_remote_origin}",
                            "end": "{_remote_entity.position}",
                            "plane": "{_remote_entity.metadata.plane}",
                            "use_observer_senses": "{_illusion_remote_use_caster_senses}",
                            "subject_illusion_id": "{_illusion_remote_subject}",
                        },
                        ["_illusion_visual_blocked"],
                    ),
                    _when("_illusion_visual_blocked == true", [_set("_remote_visible", False)]),
                ],
            ),
        ],
    )

    def bind(effects):
        bound = []
        for effect in effects:
            condition = effect.get("condition", "")
            if (
                "_remote_visible == true" in condition
                and "_remote_entity.metadata.participant_ref != null" in condition
                and "check_srd_mind_blank_information" in str(effect.get("on_true", []))
            ):
                bound.append(deepcopy(hook))
            for branch in ("on_true", "on_false"):
                if branch in effect:
                    effect[branch] = bind(effect[branch])
            if "for_each" in effect:
                effect["for_each"]["do"] = bind(effect["for_each"]["do"])
            bound.append(effect)
        return bound

    proxy["effects"] = bind(proxy["effects"])
    if "check_srd_illusion_visibility" not in str(proxy["effects"]):
        raise ValueError("The actual sensor subject-perception boundary is absent")
    return proxy


def _mislead_perception_tool(tools):
    """Use the double as eyes and ears with current caster senses and protected-subject guards."""
    perceive = deepcopy(tools["perceive_srd_information_proxy"])

    # Its existing geometry, current source sight, lighting and Mind Blank consumers remain intact.
    def adapt(value):
        if isinstance(value, str) and value.startswith("record.modality == 'hearing' and"):
            return "record.modality == 'hearing' and record.spell == 'mislead'"
        if isinstance(value, list):
            return [adapt(item) for item in value]
        if isinstance(value, dict):
            return {key: adapt(item) for key, item in value.items()}
        return value

    perceive = adapt(perceive)
    public = _public(
        _tool_scene(
            "Perceive through the double's actual eyes and ears using the caster's current senses.",
            [
                _entity("caster_state"),
                _parameter("illusion_id"),
            ],
            [
                _call(
                    "collect_srd_illusion_source", {"illusion_id": "{illusion_id}"}, RECORD_EXPORTS
                ),
                _require(
                    "_illusion_active == true and _illusion_record.spell == 'mislead' and "
                    "_illusion_record.caster == _caster_state_id",
                    "Select this caster's active Mislead double.",
                ),
                _set(
                    "_illusion_sensor_patch",
                    {"origin": [0, 0, 0], "plane": "''", "modality": "'seeing'"},
                ),
                _set("_illusion_sensor_record", "merge(_illusion_record, _illusion_sensor_patch)"),
                {
                    "call": "perceive_srd_mislead_proxy",
                    "params": {
                        "caster_state": "{_caster_state_id}",
                        "record": "{_illusion_sensor_record}",
                    },
                    "export_context": {"_info_visible": "_info_results"},
                },
                _set("_illusion_sensor_patch", {"modality": "'hearing'"}),
                _set(
                    "_illusion_sensor_record",
                    "merge(_illusion_sensor_record, _illusion_sensor_patch)",
                ),
                {
                    "call": "perceive_srd_mislead_proxy",
                    "params": {
                        "caster_state": "{_caster_state_id}",
                        "record": "{_illusion_sensor_record}",
                    },
                    "export_context": {"_info_heard": "_info_results"},
                },
            ],
        ),
        "exploration",
        "Mislead senses",
        [("Visible subjects", "_info_visible"), ("Heard subjects", "_info_heard")],
    )
    return {"perceive_srd_mislead_proxy": perceive, "perceive_srd_mislead_double": public}


def _compose_consumers(tools, changed):
    """Hook actual sight, motion, rolls, casting and concentration; retain pure geometry."""
    subject = deepcopy(tools["check_srd_illumination_subject"])
    if "check_srd_mislead_invisible" not in str(subject["effects"]):
        subject["effects"].append(
            _when(
                "state.has_illusion_spells == true",
                [
                    _call(
                        "check_srd_mislead_invisible",
                        {"subject": "{subject}"},
                        ["_mislead_invisible"],
                    ),
                    _set(
                        "_illumination_invisible",
                        "_illumination_invisible == true or _mislead_invisible == true",
                    ),
                ],
            )
        )
    changed["check_srd_illumination_subject"] = subject
    relation = deepcopy(tools["resolve_srd_scene_relation_node"])
    if "check_srd_illusion_visibility" not in str(relation["effects"]):
        relation["effects"].append(
            _when(
                "_scene_can_see == true and state.has_illusion_spells == true",
                [
                    _call(
                        "check_srd_illusion_visibility",
                        {
                            "observer_state": "{_actor_state_id}",
                            "start": "{_scene_actor_position}",
                            "end": "{_scene_target_position}",
                            "plane": "{_scene_actor_entity.metadata.plane}",
                        },
                        ["_illusion_visual_blocked"],
                    ),
                    _when("_illusion_visual_blocked == true", [_set("_scene_can_see", False)]),
                ],
            )
        )
    changed["resolve_srd_scene_relation_node"] = relation
    for name, visible, endpoint in (
        (
            "resolve_srd_illumination_target_sight",
            "_illumination_target_can_see",
            "_illumination_target_position",
        ),
        ("resolve_srd_illumination_point_sight", "_illumination_point_can_see", "point"),
    ):
        if name not in tools:
            continue
        sight = deepcopy(tools[name])
        if "check_srd_illusion_visibility" not in str(sight["effects"]):
            sight["effects"].append(
                _when(
                    f"{visible} == true and state.has_illusion_spells == true",
                    [
                        _call(
                            "check_srd_illusion_visibility",
                            {
                                "observer_state": "{_observer_state_id}",
                                "start": "{_illumination_point_origin}",
                                "end": "{" + endpoint + "}",
                                "plane": "{_illumination_point_plane}",
                            },
                            ["_illusion_visual_blocked"],
                        ),
                        _when("_illusion_visual_blocked == true", [_set(visible, False)]),
                    ],
                )
            )
        changed[name] = sight
    movement_name = (
        "move_srd_scene_creature_environment_segment"
        if "move_srd_scene_creature_environment_segment" in tools
        else "move_srd_scene_creature_node"
    )
    movement = deepcopy(tools[movement_name])
    if "interact_srd_illusions_during_movement" not in str(movement["effects"]):
        movement["effects"].append(
            _when(
                "state.has_illusion_spells == true",
                [
                    _call(
                        "interact_srd_illusions_during_movement",
                        {
                            "actor_state": "{_actor_state_id}",
                            "start": "{_move_origin}",
                            "end": "{destination}",
                            "plane": "{_move_scene_entity.metadata.plane}",
                        },
                    ),
                ],
            )
        )
    changed[movement_name] = movement
    clock = deepcopy(tools["advance_srd_lasting_clock"])
    if "refresh_srd_illusion_spells" not in str(clock["effects"]):
        index = next(
            i
            for i, e in enumerate(clock["effects"])
            if e.get("set", {}).get("field") == "lasting_state.lasting_tick"
        )
        clock["effects"].insert(index + 1, _call("refresh_srd_illusion_spells", {}))
    changed["advance_srd_lasting_clock"] = clock
    end = deepcopy(tools["end_srd_concentration_node"])
    if "retire_srd_illusion_source" not in str(end["effects"]):
        end["effects"].insert(
            0,
            _when(
                "source_state.concentration_effect_family == 'illusion_spell'",
                [
                    _call(
                        "retire_srd_illusion_source",
                        {
                            "illusion_id": "{source_state.illusion_concentration_id}",
                            "clear_concentration": False,
                        },
                    ),
                ],
            ),
        )
    changed["end_srd_concentration_node"] = end
    d20 = deepcopy(tools["d20_test"])
    if "end_srd_mislead_attack_source" not in str(d20["effects"]):
        d20["effects"].append(
            _when(
                "actor_state != null and kind == 'attack_roll' and state.has_illusion_spells "
                "== true",
                [
                    _call("end_srd_mislead_attack_source", {"caster_state": "{_actor_state_id}"}),
                ],
            )
        )
    changed["d20_test"] = d20


def _public_wrappers(changed):
    """Hide the internal lasting-state binding while keeping actual actor resolution."""
    for name in PUBLIC_TOOLS:
        tool = changed[name]
        if not any(p["name"] == "lasting_state" for p in tool["parameters"]):
            continue
        node = deepcopy(tool)
        node["metadata"] = {
            "category": "internal",
            "visible_to_player": False,
            "costs_action": False,
        }
        changed[name + "_node"] = node
        tool["parameters"] = [p for p in tool["parameters"] if p["name"] != "lasting_state"]
        parameters = {
            p["name"]: "{"
            + (
                "_" + p["name"] + "_id"
                if p.get("resolve") in {"entity", "participant"}
                else p["name"]
            )
            + "}"
            for p in tool["parameters"]
        }
        parameters["lasting_state"] = "state"
        initializers = [
            _when(p["name"] + " == null", [_set("_" + p["name"] + "_id", None)])
            for p in tool["parameters"]
            if p.get("resolve") in {"entity", "participant"} and p.get("required") is False
        ]
        exports = [
            row["value"]["path"].removeprefix("context.")
            for row in tool["metadata"]["result_card"]["summary"]
        ]
        tool["effects"] = [*initializers, _call(name + "_node", parameters, exports)]


def extend_runtime(text):
    """Emit images and later consumers after illumination and before final damage/reactions."""
    if __package__:
        from .srd_divination_spells import _normalize
    else:
        from srd_divination_spells import _normalize
    tools = _load_runtime(text)["tools"]
    proxy = _proxy_image_visibility_tool(tools)
    proxy_tools = dict(tools, perceive_srd_information_proxy=proxy)
    changed = (
        _record_tools()
        | _invisibility_tools()
        | _proxy_tools()
        | _recognition_tools()
        | _control_tools(tools)
        | _programmed_tools()
        | {"perceive_srd_information_proxy": proxy}
        | _mislead_perception_tool(proxy_tools)
    )
    changed["cast_spell_illusion_spell"] = _casting_tool(tools)
    changed["study_srd_illusion"] = _study_tool(tools)
    npc_study = deepcopy(changed["study_srd_illusion"])
    npc_study["parameters"] = [
        p
        for p in npc_study["parameters"]
        if p["name"] not in {"observer", "illusion_proficiency_factor", "illusion_skill"}
    ]
    for p in npc_study["parameters"]:
        p.pop("model_derive", None)
    npc_study["effects"] = [
        _set("observer", None),
        _set("_observer_id", None),
        *npc_study["effects"],
    ]
    changed["study_srd_illusion_creature"] = npc_study
    changed["interact_srd_illusions_during_movement"] = _movement_interaction_tool()
    _compose_consumers(tools, changed)
    cast = deepcopy(tools["cast_spell"])
    known = {p["name"] for p in cast["parameters"]}
    cast["parameters"] += [deepcopy(p) for p in CAST_CHOICES if p["name"] not in known]
    if "end_srd_mislead_cast_source" not in str(cast["effects"]):
        cast["effects"].insert(
            0,
            _when(
                "state.has_illusion_spells == true",
                [_call("end_srd_mislead_cast_source", {"caster_state": "{_caster_state_id}"})],
            ),
        )
    args = {p["name"]: "{" + p["name"] + "}" for p in CAST_CHOICES}
    args.update(
        caster_state="{_caster_state_id}",
        spell="{spell}",
        slot_level="{_slot_level}",
        save_dc="{_casting_profile.save_dc}",
        concentration_modifier="{_casting_profile.concentration_save_modifier}",
    )
    branch = _when(
        "_spell_definition.family == 'illusion_spell'",
        [
            {
                "call": "cast_spell_illusion_spell",
                "params": args,
                "export_context": {
                    "_illusion_summary": "_spell_summary_rows",
                    "_illusion_details": "_spell_detail_rows",
                },
            },
            _loop(
                "_illusion_summary",
                "_illusion_card_row",
                [_push("_spell_summary_rows", "_illusion_card_row")],
            ),
            _loop(
                "_illusion_details",
                "_illusion_card_row",
                [_push("_spell_detail_rows", "_illusion_card_row")],
            ),
        ],
    )

    def dispatch(items):
        if any("_spell_definition.family ==" in e.get("condition", "") for e in items):
            items[:] = [
                e
                for e in items
                if "_spell_definition.family == 'illusion_spell'" not in e.get("condition", "")
            ]
            last = max(
                i
                for i, e in enumerate(items)
                if "_spell_definition.family ==" in e.get("condition", "")
            )
            items.insert(last + 1, deepcopy(branch))
            return True
        for effect in items:
            for key in ("on_true", "on_false"):
                if key in effect and dispatch(effect[key]):
                    return True
        return False

    def remove_prior_dispatch(items):
        kept = []
        for effect in items:
            if "_spell_definition.family == 'illusion_spell'" in effect.get("condition", ""):
                continue
            for key in ("on_true", "on_false"):
                if key in effect:
                    effect[key] = remove_prior_dispatch(effect[key])
            if "for_each" in effect:
                effect["for_each"]["do"] = remove_prior_dispatch(effect["for_each"]["do"])
            kept.append(effect)
        return kept

    cast["effects"] = remove_prior_dispatch(cast["effects"])
    if not dispatch(cast["effects"]):
        raise ValueError("The admitted spell family dispatcher is absent")
    changed["cast_spell"] = cast
    _normalize(changed)

    def flatten_mutations(effects):
        for effect in effects:
            if "spatial_mutation" in effect and "request" in effect["spatial_mutation"]:
                mutation = effect["spatial_mutation"]
                effect["spatial_mutation"] = {"scene": mutation["scene"], **mutation["request"]}
            for key in ("on_true", "on_false"):
                if key in effect:
                    flatten_mutations(effect[key])
            if "for_each" in effect:
                flatten_mutations(effect["for_each"]["do"])

    for tool in changed.values():
        flatten_mutations(tool["effects"])
    # Newly appended shared hooks contain canonical state expressions and must be rebound.
    for name, tool in list(changed.items()):
        if "state." in str(tool["effects"]):
            rebound = _tool_scene(tool["description"], tool["parameters"], tool["effects"])
            rebound["pre_conditions"] = tool.get("pre_conditions", [])
            rebound["metadata"] = tool["metadata"]
            changed[name] = rebound
    _public_wrappers(changed)
    text = _replace_tools(text, changed)
    if "*d20_result_card" in text and "&d20_result_card" not in text:
        start = text.index("  d20_test:")
        index = text.index("      result_card:", start)
        text = text[:index] + text[index:].replace(
            "      result_card:", "      result_card: &d20_result_card", 1
        )
    if "      illusion_spell:" not in text:
        schema = yaml.dump({"illusion_spell": _schema()}, Dumper=_RuntimeDumper, sort_keys=False)
        text = text.replace(
            "      information_spell:",
            "".join("      " + line for line in schema.splitlines(True))
            + "\n      information_spell:",
            1,
        )
    return text
