"""Pinned illumination spells share source-owned light, Invisible and later-action consumers."""

import re
from copy import deepcopy

import yaml

if __package__:
    from .srd_scene_relations import POSITION_EXPORTS, _array, _snapshot, _spatial, _tool_scene
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
    from srd_scene_relations import POSITION_EXPORTS, _array, _snapshot, _spatial, _tool_scene
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


PUBLIC_TOOLS = {
    "move_srd_dancing_lights",
    "set_srd_illumination_cover",
    "dismiss_srd_illumination_spell",
}
SOURCE = {
    "continual_flame": (119, "Continual Flame", "Control Water"),
    "dancing_lights": ([121, 122], "Dancing Lights", "Darkness"),
    "darkness": (122, "Darkness", "Darkvision"),
    "daylight": (122, "Daylight", "Death Ward"),
    "faerie_fire": (129, "Faerie Fire", "Faithful Hound"),
    "greater_invisibility": (137, "Greater Invisibility", "Greater Restoration"),
    "invisibility": (143, "Invisibility", "Jump"),
    "light": (144, "Light", "Lightning Bolt"),
    "produce_flame": (156, "Produce Flame", "Programmed Illusion"),
}
PROFILES = {}
for _name, _level, _time, _range, _rounds, _conc, _mode, _bright, _dim, _dark in (
    ("light", 0, "action", 0, 600, False, "object", 20, 40, 0),
    ("continual_flame", 2, "action", 0, 0, False, "object", 20, 40, 0),
    ("daylight", 3, "action", 60, 600, False, "point_or_object", 60, 120, 0),
    ("darkness", 2, "action", 60, 100, True, "point_or_object", 0, 0, 15),
    ("dancing_lights", 0, "action", 120, 10, True, "dancing", 0, 10, 0),
    ("produce_flame", 0, "bonus_action", 0, 100, False, "self", 20, 40, 0),
    ("faerie_fire", 1, "action", 60, 10, True, "cube", 0, 10, 0),
    ("invisibility", 2, "action", 0, 600, True, "invisible", 0, 0, 0),
    ("greater_invisibility", 4, "action", 0, 10, True, "invisible", 0, 0, 0),
):
    PROFILES[_name] = {
        "family": "illumination_spell",
        "base_level": _level,
        "casting_time": _time,
        "range_feet": _range,
        "effect_rounds": _rounds,
        "concentration": _conc,
        "illumination_mode": _mode,
        "bright_radius": _bright,
        "dim_radius": _dim,
        "dark_radius": _dark,
    }
    if _rounds:
        PROFILES[_name]["duration_rounds"] = _rounds


def validate_profiles(definitions):
    """Keep every promoted leaf equal to its reviewed full-column SRD source profile."""
    for spell, definition in definitions.items():
        if definition.get("family") == "illumination_spell" and definition != PROFILES.get(spell):
            raise SystemExit(f"Illumination source profile differs: {spell}")


def _schema():
    properties = {}
    for profile in PROFILES.values():
        for name, value in profile.items():
            if name != "family":
                properties[name] = {
                    "type": "boolean"
                    if type(value) is bool
                    else "integer"
                    if type(value) is int
                    else "string"
                }
    return {
        "type": "object",
        "properties": properties,
        "required": [name for name in properties if name != "duration_rounds"],
        "additionalProperties": False,
    }


CAST_CHOICES = [
    _optional("illumination_object", default=""),
    _array("illumination_position", default=[0, 0, 0]),
    _optional(
        "illumination_points",
        "array",
        [],
        items={"type": "array", "items": {"type": "number"}},
        max_items=4,
    ),
    _optional("illumination_combined", "boolean", False),
    _optional("illumination_color", default="white"),
]
SUBJECT_EXPORTS = ("_illumination_invisible", "_illumination_suppressed", "_illumination_faerie")
RECORD_EXPORTS = (
    "_illumination_active",
    "_illumination_origin",
    "_illumination_plane",
    "_illumination_covered",
)


def _distance(start, end, local):
    result = _call("measure_srd_information_distance", {"start": start, "end": end})
    result["export_context"] = {local: "_scene_distance"}
    return result


def _record_tools():
    """Resolve current anchors, opaque covering and exact concentration before using a source."""
    check = _tool_scene(
        (
            "Resolve a saved illumination source against its exact clock, caster and curren"
            "t scene anchor."
        ),
        [_parameter("record", "object", from_state="state.turn_effects"), _entity("caster_state")],
        [
            _set(
                "_illumination_active",
                "record.expires_tick == null or state.lasting_tick < record.expires_tick",
            ),
            _set("_illumination_origin", "record.origin"),
            _set("_illumination_plane", "record.plane"),
            _set("_illumination_covered", False),
            _when(
                "record.concentration == true",
                [
                    _set(
                        "_illumination_active",
                        (
                            "_illumination_active == true and caster_state.is_alive == true"
                            " and caster_state.concentration_spell_id == record.spell and "
                            "caster_state.concentration_effect_family == "
                            "'illumination_spell' and "
                            "caster_state.illumination_concentration_instance == "
                            "record.instance and not ('incapacitated' in "
                            "caster_state.conditions or 'paralyzed' in "
                            "caster_state.conditions or 'petrified' in "
                            "caster_state.conditions or 'stunned' in "
                            "caster_state.conditions or 'unconscious' in "
                            "caster_state.conditions)"
                        ),
                    )
                ],
            ),
            _when(
                "_illumination_active == true and record.source != ''",
                [
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{record.source}"},
                        POSITION_EXPORTS,
                    ),
                    _set("_illumination_origin", "_info_position"),
                    _set("_illumination_plane", "_info_scene_entity.metadata.plane"),
                    _set(
                        "_illumination_covered", "_info_scene_entity.metadata.opaque_cover == true"
                    ),
                    _set("_illumination_holder", None),
                    _when(
                        "_info_scene_entity.metadata.carried_by != null",
                        [_set("_illumination_holder", "_info_scene_entity.metadata.carried_by")],
                    ),
                    _when(
                        (
                            "(_illumination_holder == null or _illumination_holder == '') a"
                            "nd _info_scene_entity.metadata.worn_by != null"
                        ),
                        [_set("_illumination_holder", "_info_scene_entity.metadata.worn_by")],
                    ),
                    _when(
                        "_illumination_holder != null and _illumination_holder != ''",
                        [
                            _call(
                                "srd_information_scene_entity",
                                {"subject": "{_illumination_holder}"},
                                POSITION_EXPORTS,
                            ),
                            _set("_illumination_origin", "_info_position"),
                            _set("_illumination_plane", "_info_scene_entity.metadata.plane"),
                        ],
                    ),
                ],
            ),
            _when(
                "_illumination_active == true and record.spell == 'dancing_lights'",
                [
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{record.caster}"},
                        POSITION_EXPORTS,
                    ),
                    _distance("{_info_position}", "{_illumination_origin}", "_dance_range"),
                    _set(
                        "_illumination_active",
                        (
                            "_dance_range <= 120 and _info_scene_entity.metadata.plane == _"
                            "illumination_plane"
                        ),
                    ),
                ],
            ),
        ],
    )
    subject = _tool(
        (
            "Read active Invisible and outlining benefits for a subject; native invisibilit"
            "y remains distinct."
        ),
        [_parameter("subject")],
        [
            _set("_illumination_invisible", False),
            _set("_illumination_suppressed", False),
            _set("_illumination_faerie", False),
            _set("_subject_records", []),
            _when(
                "state.has_illumination_spells == true and state.turn_effects != null",
                [_set("_subject_records", "state.turn_effects")],
            ),
            _loop(
                "_subject_records",
                "_subject_record",
                [
                    _when(
                        (
                            "_subject_record.kind == 'illumination_spell' and "
                            "_subject_record.source == subject and (_subject_record.mode =="
                            " 'invisible' or _subject_record.spell == 'faerie_fire' or "
                            "_subject_record.suppress_invisible == true)"
                        ),
                        [
                            _call(
                                "check_srd_illumination_record",
                                {
                                    "record": "{_subject_record}",
                                    "caster_state": "{_subject_record.caster}",
                                },
                                RECORD_EXPORTS,
                            ),
                            _when(
                                "_illumination_active == true",
                                [
                                    _when(
                                        "_subject_record.mode == 'invisible'",
                                        [_set("_illumination_invisible", True)],
                                    ),
                                    _when(
                                        (
                                            "_subject_record.spell == 'faerie_fire' or _sub"
                                            "ject_record.suppress_invisible == true"
                                        ),
                                        [_set("_illumination_suppressed", True)],
                                    ),
                                    _when(
                                        "_subject_record.spell == 'faerie_fire'",
                                        [_set("_illumination_faerie", True)],
                                    ),
                                ],
                            ),
                        ],
                    )
                ],
            ),
        ],
    )
    sync = _tool(
        "Recompute the Invisible condition while preserving independent and preexisting grants.",
        [_entity("actor_state")],
        [
            _call(
                "check_srd_illumination_subject", {"subject": "{_actor_state_id}"}, SUBJECT_EXPORTS
            ),
            _set("actor_state.illumination_has_invisibility", "_illumination_invisible"),
            _when(
                "actor_state.illumination_invisible_managed == true",
                [
                    {
                        ("condition"): (
                            "_illumination_invisible == true or actor_state.illumination_in"
                            "visible_native == true"
                        ),
                        "on_true": [
                            _when(
                                "not ('invisible' in actor_state.conditions)",
                                [
                                    {
                                        "apply_condition": {
                                            "target": "actor_state",
                                            "condition": "invisible",
                                        }
                                    }
                                ],
                            )
                        ],
                        "on_false": [
                            {
                                "remove_condition": {
                                    "target": "actor_state",
                                    "condition": "invisible",
                                }
                            }
                        ],
                    },
                ],
            ),
        ],
    )
    remove = _tool(
        (
            "Remove one exact source instance, preserving unrelated sources and target Invi"
            "sible grants."
        ),
        [
            _entity("caster_state"),
            _parameter("instance", "integer"),
            _optional("clear_concentration", "boolean", True),
        ],
        [
            _set("_light_keep", []),
            _set("_light_removed_targets", []),
            _set("_light_remove_records", "state.turn_effects"),
            _loop(
                "_light_remove_records",
                "_light_remove_record",
                [
                    {
                        "condition": (
                            "_light_remove_record.kind == 'illumination_spell' and "
                            "_light_remove_record.caster == _caster_state_id and "
                            "_light_remove_record.instance == instance"
                        ),
                        "on_true": [
                            _when(
                                "_light_remove_record.mode == 'invisible' and "
                                "_light_remove_record.source not in _light_removed_targets",
                                [_push("_light_removed_targets", "_light_remove_record.source")],
                            )
                        ],
                        "on_false": [_push("_light_keep", "_light_remove_record")],
                    }
                ],
            ),
            _set("state.turn_effects", "_light_keep"),
            _loop(
                "_light_removed_targets",
                "_light_removed_target",
                [
                    _call(
                        "sync_srd_illumination_invisible",
                        {"actor_state": "{_light_removed_target}"},
                    )
                ],
            ),
            _when(
                "clear_concentration == true",
                [
                    _call(
                        "finish_srd_illumination_concentration",
                        {"caster_state": "{_caster_state_id}", "instance": "{instance}"},
                    )
                ],
            ),
            _call("refresh_srd_illumination_registry", {}),
        ],
    )
    refresh = _tool(
        "Prune elapsed or invalid source records and remove only their owned Invisible conditions.",
        [],
        [
            _when(
                "state.has_illumination_spells == true",
                [
                    _set("_light_refresh_records", "state.turn_effects"),
                    _set("_light_refresh_keep", []),
                    _set("_light_refresh_targets", []),
                    _loop(
                        "_light_refresh_records",
                        "_light_refresh_record",
                        [
                            _set("_light_refresh_active", True),
                            _when(
                                "_light_refresh_record.kind == 'illumination_spell'",
                                [
                                    _call(
                                        "check_srd_illumination_record",
                                        {
                                            "record": "{_light_refresh_record}",
                                            "caster_state": "{_light_refresh_record.caster}",
                                        },
                                        RECORD_EXPORTS,
                                    ),
                                    _set("_light_refresh_active", "_illumination_active"),
                                    _when(
                                        (
                                            "_light_refresh_record.mode == 'invisible' and "
                                            "_light_refresh_record.source not in "
                                            "_light_refresh_targets"
                                        ),
                                        [
                                            _push(
                                                "_light_refresh_targets",
                                                "_light_refresh_record.source",
                                            )
                                        ],
                                    ),
                                ],
                            ),
                            _when(
                                "_light_refresh_active == true",
                                [_push("_light_refresh_keep", "_light_refresh_record")],
                            ),
                        ],
                    ),
                    _set("state.turn_effects", "_light_refresh_keep"),
                    _loop(
                        "_light_refresh_targets",
                        "_light_refresh_target",
                        [
                            _call(
                                "sync_srd_illumination_invisible",
                                {"actor_state": "{_light_refresh_target}"},
                            )
                        ],
                    ),
                    _call("refresh_srd_illumination_registry", {}),
                ],
            )
        ],
    )
    end_actor = _tool(
        (
            "End ordinary Invisibility immediately after its target's actual attack, damage"
            " or spell event."
        ),
        [
            _entity("actor_state"),
            _parameter("event", "enum", enum_values=["attack_roll", "damage", "spell"]),
        ],
        [
            _when(
                (
                    "actor_state.illumination_has_invisibility == true and state.has_illumi"
                    "nation_spells == true"
                ),
                [
                    _set("_invis_event_records", "state.turn_effects"),
                    _set("_invis_event_keep", []),
                    _loop(
                        "_invis_event_records",
                        "_invis_event_record",
                        [
                            _when(
                                "not (_invis_event_record.kind == 'illumination_spell' and "
                                "_invis_event_record.spell == 'invisibility' and "
                                "_invis_event_record.source == _actor_state_id)",
                                [_push("_invis_event_keep", "_invis_event_record")],
                            )
                        ],
                    ),
                    _set("state.turn_effects", "_invis_event_keep"),
                    _call("sync_srd_illumination_invisible", {"actor_state": "{_actor_state_id}"}),
                    _loop(
                        "_invis_event_records",
                        "_invis_ended_record",
                        [
                            _when(
                                (
                                    "_invis_ended_record.kind == 'illumination_spell' and "
                                    "_invis_ended_record.spell == 'invisibility' and "
                                    "_invis_ended_record.source == _actor_state_id"
                                ),
                                [
                                    _call(
                                        "finish_srd_illumination_concentration",
                                        {
                                            "caster_state": "{_invis_ended_record.caster}",
                                            "instance": "{_invis_ended_record.instance}",
                                        },
                                    )
                                ],
                            )
                        ],
                    ),
                    _call("refresh_srd_illumination_registry", {}),
                ],
            )
        ],
    )
    finish = _tool(
        "Clear concentration only when the exact source instance has no remaining grants.",
        [_entity("caster_state"), _parameter("instance", "integer")],
        [
            _when(
                "caster_state.concentration_effect_family == 'illumination_spell' and "
                "caster_state.illumination_concentration_instance == instance",
                [
                    _set("_concentrated_light_remaining", False),
                    _set("_concentrated_light_records", "state.turn_effects"),
                    _loop(
                        "_concentrated_light_records",
                        "_concentrated_light_record",
                        [
                            _when(
                                (
                                    "_concentrated_light_record.kind == "
                                    "'illumination_spell' and "
                                    "_concentrated_light_record.caster == _caster_state_id "
                                    "and _concentrated_light_record.instance == instance"
                                ),
                                [_set("_concentrated_light_remaining", True)],
                            )
                        ],
                    ),
                    _when(
                        "_concentrated_light_remaining != true",
                        [
                            *[
                                _set("caster_state." + field, None)
                                for field in (
                                    "concentration_spell_id",
                                    "concentration_effect_family",
                                    "concentration_target_state_id",
                                    "concentration_started_tick",
                                    "concentration_expires_tick",
                                    "illumination_concentration_instance",
                                )
                            ],
                            _set("caster_state.concentration_target_state_ids", []),
                        ],
                    ),
                ],
            )
        ],
    )
    registry = _tool(
        "Maintain the cheap inactive guard from the actual retained source registry.",
        [],
        [
            _set("state.has_illumination_spells", False),
            _set("_light_registry_records", "state.turn_effects"),
            _loop(
                "_light_registry_records",
                "_light_registry_record",
                [
                    _when(
                        "_light_registry_record.kind == 'illumination_spell'",
                        [_set("state.has_illumination_spells", True)],
                    )
                ],
            ),
        ],
    )
    initiative = _tool(
        "Read the actual Invisible initiative benefit after source-owned suppression.",
        [_entity("actor_state")],
        [
            _set("_illumination_initiative_advantage", "'invisible' in actor_state.conditions"),
            _when(
                (
                    "_illumination_initiative_advantage == true and state.has_illumination_"
                    "spells == true"
                ),
                [
                    _call(
                        "check_srd_illumination_subject",
                        {"subject": "{_actor_state_id}"},
                        SUBJECT_EXPORTS,
                    ),
                    _when(
                        "_illumination_suppressed == true",
                        [_set("_illumination_initiative_advantage", False)],
                    ),
                ],
            ),
        ],
    )
    return {
        "check_srd_illumination_record": check,
        "check_srd_illumination_subject": subject,
        "sync_srd_illumination_invisible": sync,
        "remove_srd_illumination_instance": remove,
        "refresh_srd_illumination_spells": refresh,
        "end_srd_invisibility_for_actor": end_actor,
        "finish_srd_illumination_concentration": finish,
        "refresh_srd_illumination_registry": registry,
        "check_srd_illumination_initiative": initiative,
    }


def _light_tools(prior):
    """Compose magical and nonmagical source light; overlap dispels use exact saved spell levels."""
    light = deepcopy(prior)
    light["effects"] += [
        _set("_information_sunlight", False),
        _set("_light_magic_dark", False),
        _set("_light_magic_bright", False),
        _set("_light_magic_dim", False),
        _set("_light_query_records", []),
        _when(
            "state.has_illumination_spells == true",
            [_set("_light_query_records", "state.turn_effects")],
        ),
        _loop(
            "_light_query_records",
            "_light_query_record",
            [
                _when(
                    (
                        "_light_query_record.kind == 'actor_spell_modifier' and "
                        "(_light_query_record.mode == 'fire_shield_warm' or "
                        "_light_query_record.mode == 'fire_shield_chill')"
                    ),
                    [
                        _call(
                            "check_srd_actor_spell_light",
                            {
                                "actor_state": "{_light_query_record.target}",
                                "lasting_state": "state",
                            },
                            ["_actor_spell_bright_light_feet", "_actor_spell_dim_light_feet"],
                        ),
                        _when(
                            "_actor_spell_dim_light_feet > 0",
                            [
                                _call(
                                    "srd_information_scene_entity",
                                    {"subject": "{_light_query_record.target}"},
                                    POSITION_EXPORTS,
                                ),
                                _set("_existing_light_origin", "_info_position"),
                                _set("_existing_light_plane", "_info_scene_entity.metadata.plane"),
                                _distance(
                                    "{_existing_light_origin}",
                                    "{point}",
                                    "_existing_light_distance",
                                ),
                                _call(
                                    "srd_information_barriers",
                                    {
                                        "start": "{_existing_light_origin}",
                                        "end": "{point}",
                                        "rule": "light",
                                    },
                                    ["_information_blocked"],
                                ),
                                _when(
                                    (
                                        "_existing_light_plane == plane and _information_bl"
                                        "ocked != true"
                                    ),
                                    [
                                        _when(
                                            (
                                                "_existing_light_distance <= _actor_spell_d"
                                                "im_light_feet"
                                            ),
                                            [_set("_light_magic_dim", True)],
                                        ),
                                        _when(
                                            (
                                                "_existing_light_distance <= _actor_spell_b"
                                                "right_light_feet"
                                            ),
                                            [_set("_light_magic_bright", True)],
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
                _when(
                    "_light_query_record.kind == 'illumination_spell' and "
                    "(_light_query_record.dim_radius > 0 or _light_query_record.dark_radius > 0)",
                    [
                        _call(
                            "check_srd_illumination_record",
                            {
                                "record": "{_light_query_record}",
                                "caster_state": "{_light_query_record.caster}",
                            },
                            RECORD_EXPORTS,
                        ),
                        _when(
                            (
                                "_illumination_active == true and _illumination_covered != "
                                "true and _illumination_plane == plane"
                            ),
                            [
                                _distance(
                                    "{_illumination_origin}", "{point}", "_light_query_distance"
                                ),
                                _call(
                                    "srd_information_barriers",
                                    {
                                        "start": "{_illumination_origin}",
                                        "end": "{point}",
                                        "rule": "light",
                                    },
                                    ["_information_blocked"],
                                ),
                                _when(
                                    "_information_blocked != true",
                                    [
                                        _when(
                                            (
                                                "_light_query_record.dark_radius > 0 and _l"
                                                "ight_query_distance <= _light_query_record"
                                                ".dark_radius"
                                            ),
                                            [_set("_light_magic_dark", True)],
                                        ),
                                        _when(
                                            (
                                                "_light_query_record.dim_radius > 0 and _li"
                                                "ght_query_distance <= _light_query_record."
                                                "dim_radius"
                                            ),
                                            [_set("_light_magic_dim", True)],
                                        ),
                                        _when(
                                            (
                                                "_light_query_record.bright_radius > 0 and "
                                                "_light_query_distance <= _light_query_reco"
                                                "rd.bright_radius"
                                            ),
                                            [
                                                _set("_light_magic_bright", True),
                                                _when(
                                                    "_light_query_record.spell == 'daylight'",
                                                    [_set("_information_sunlight", True)],
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
        ),
        _when("_light_magic_dark == true", [_set("_information_light", "'magical_darkness'")]),
        _when(
            (
                "_light_magic_dim == true and (_information_light == 'darkness' or _informa"
                "tion_light == 'magical_darkness')"
            ),
            [_set("_information_light", "'dim'")],
        ),
        _when("_light_magic_bright == true", [_set("_information_light", "'bright'")]),
    ]
    # Rebind this existing owner after appending state effects, preserving its scene snapshot path.
    light = _tool_scene(
        light["description"],
        light["parameters"],
        light["effects"],
    )
    overlap = _tool(
        (
            "Dispel complete light/darkness instances whose actual active areas overlap at "
            "source-level thresholds."
        ),
        [],
        [
            _when(
                "state.has_illumination_spells == true",
                [
                    _set("_overlap_records", "state.turn_effects"),
                    _set("_overlap_dispelled", []),
                    _loop(
                        "_overlap_records",
                        "_overlap_source",
                        [
                            _when(
                                (
                                    "_overlap_source.kind == 'illumination_spell' and "
                                    "(_overlap_source.spell == 'darkness' or "
                                    "_overlap_source.spell == 'daylight')"
                                ),
                                [
                                    _call(
                                        "check_srd_illumination_record",
                                        {
                                            "record": "{_overlap_source}",
                                            "caster_state": "{_overlap_source.caster}",
                                        },
                                        RECORD_EXPORTS,
                                    ),
                                    _set("_overlap_origin", "_illumination_origin"),
                                    _set("_overlap_plane", "_illumination_plane"),
                                    _set("_overlap_radius", "_overlap_source.dark_radius"),
                                    _when(
                                        "_overlap_source.spell == 'daylight'",
                                        [_set("_overlap_radius", "_overlap_source.bright_radius")],
                                    ),
                                    _when(
                                        (
                                            "_illumination_active == true and _illumination"
                                            "_covered != true"
                                        ),
                                        [
                                            _loop(
                                                "_overlap_records",
                                                "_overlap_other",
                                                [
                                                    _when(
                                                        (
                                                            "_overlap_other.kind == "
                                                            "'illumination_spell' and "
                                                            "((_overlap_source.spell == "
                                                            "'darkness' and "
                                                            "_overlap_other.dim_radius > 0 "
                                                            "and _overlap_other.slot_level "
                                                            "<= 2) or "
                                                            "(_overlap_source.spell == "
                                                            "'daylight' and "
                                                            "_overlap_other.dark_radius > 0"
                                                            " and _overlap_other.slot_level"
                                                            " <= 3))"
                                                        ),
                                                        [
                                                            _call(
                                                                "check_srd_illumination_record",
                                                                {
                                                                    "record": "{_overlap_other}",
                                                                    ("caster_state"): (
                                                                        "{_overlap_other.caster}"
                                                                    ),
                                                                },
                                                                RECORD_EXPORTS,
                                                            ),
                                                            _when(
                                                                (
                                                                    "_illumination_active ="
                                                                    "= true and _illuminati"
                                                                    "on_covered != true and"
                                                                    " _illumination_plane ="
                                                                    "= _overlap_plane"
                                                                ),
                                                                [
                                                                    _distance(
                                                                        "{_overlap_origin}",
                                                                        "{_illumination_origin}",
                                                                        "_overlap_distance",
                                                                    ),
                                                                    _set(
                                                                        "_overlap_other_radius",
                                                                        (
                                                                            "max(_overlap_o"
                                                                            "ther.dim_radiu"
                                                                            "s, _overlap_ot"
                                                                            "her.dark_radiu"
                                                                            "s)"
                                                                        ),
                                                                    ),
                                                                    _when(
                                                                        (
                                                                            "_overlap_dista"
                                                                            "nce <= _overla"
                                                                            "p_radius + _ov"
                                                                            "erlap_other_ra"
                                                                            "dius"
                                                                        ),
                                                                        [
                                                                            _when(
                                                                                (
                                                                                    "_overlap"
                                                                                    "_other.i"
                                                                                    "nstance "
                                                                                    "not in _"
                                                                                    "overlap_"
                                                                                    "dispelle"
                                                                                    "d"
                                                                                ),
                                                                                [
                                                                                    _push(
                                                                                        (
                                                                                            "_overlap"
                                                                                            "_dispell"
                                                                                            "ed"
                                                                                        ),
                                                                                        (
                                                                                            "_overlap"
                                                                                            "_other.i"
                                                                                            "nstance"
                                                                                        ),
                                                                                    )
                                                                                ],
                                                                            ),
                                                                        ],
                                                                    ),
                                                                ],
                                                            ),
                                                        ],
                                                    )
                                                ],
                                            )
                                        ],
                                    ),
                                ],
                            )
                        ],
                    ),
                    _loop(
                        "_overlap_records",
                        "_dispel_record",
                        [
                            _when(
                                (
                                    "_dispel_record.kind == 'illumination_spell' and _dispe"
                                    "l_record.instance in _overlap_dispelled"
                                ),
                                [
                                    _call(
                                        "remove_srd_illumination_instance",
                                        {
                                            "caster_state": "{_dispel_record.caster}",
                                            "instance": "{_dispel_record.instance}",
                                        },
                                    )
                                ],
                            )
                        ],
                    ),
                ],
            )
        ],
    )
    dispel_area = _tool(
        "Retire every spell-created darkness instance overlapping an owning spell's actual area.",
        [_array("origin"), _parameter("plane"), _parameter("radius", "integer")],
        [
            _when(
                "state.has_illumination_spells == true",
                [
                    _require("radius >= 0", "An area radius cannot be negative."),
                    _set("_area_dark_records", "state.turn_effects"),
                    _set("_area_dark_instances", []),
                    _loop(
                        "_area_dark_records",
                        "_area_dark_record",
                        [
                            _when(
                                (
                                    "_area_dark_record.kind == 'illumination_spell' and _ar"
                                    "ea_dark_record.dark_radius > 0"
                                ),
                                [
                                    _call(
                                        "check_srd_illumination_record",
                                        {
                                            "record": "{_area_dark_record}",
                                            "caster_state": "{_area_dark_record.caster}",
                                        },
                                        RECORD_EXPORTS,
                                    ),
                                    _when(
                                        (
                                            "_illumination_active == true and _illumination"
                                            "_covered != true and _illumination_plane == pl"
                                            "ane"
                                        ),
                                        [
                                            _distance(
                                                "{origin}",
                                                "{_illumination_origin}",
                                                "_area_dark_distance",
                                            ),
                                            _when(
                                                (
                                                    "_area_dark_distance <= radius + "
                                                    "_area_dark_record.dark_radius and "
                                                    "_area_dark_record.instance not in "
                                                    "_area_dark_instances"
                                                ),
                                                [
                                                    _push(
                                                        "_area_dark_instances",
                                                        "_area_dark_record.instance",
                                                    )
                                                ],
                                            ),
                                        ],
                                    ),
                                ],
                            )
                        ],
                    ),
                    _loop(
                        "_area_dark_records",
                        "_area_dark_record",
                        [
                            _when(
                                (
                                    "_area_dark_record.kind == 'illumination_spell' and _ar"
                                    "ea_dark_record.instance in _area_dark_instances"
                                ),
                                [
                                    _call(
                                        "remove_srd_illumination_instance",
                                        {
                                            "caster_state": "{_area_dark_record.caster}",
                                            "instance": "{_area_dark_record.instance}",
                                        },
                                    )
                                ],
                            )
                        ],
                    ),
                ],
            )
        ],
    )
    return {
        "resolve_srd_information_light": light,
        "resolve_srd_illumination_overlaps": overlap,
        "dispel_srd_illumination_darkness_area": dispel_area,
    }


def _object_visibility_tools():
    """Equipment concealment follows its actual Invisible carrier and source-owned suppression."""
    carrier = _tool(
        "Read a carrier's actual Invisible concealment after active source suppression.",
        [_entity("actor_state")],
        [
            _call(
                "check_srd_illumination_subject", {"subject": "{_actor_state_id}"}, SUBJECT_EXPORTS
            ),
            _set("_illumination_carrier_invisible", "'invisible' in actor_state.conditions"),
            _when(
                (
                    "actor_state.illumination_invisible_managed == true and actor_state.ill"
                    "umination_invisible_native != true"
                ),
                [_set("_illumination_carrier_invisible", "_illumination_invisible")],
            ),
            _when(
                "_illumination_suppressed == true", [_set("_illumination_carrier_invisible", False)]
            ),
        ],
    )
    object_query = _tool_scene(
        (
            "Derive an object's Invisible equipment benefit from its actual holder and outl"
            "ining sources."
        ),
        [_parameter("object_id"), _optional("observer_state", resolve="entity")],
        [
            _call("check_srd_illumination_subject", {"subject": "{object_id}"}, SUBJECT_EXPORTS),
            _set("_illumination_object_outlined", "_illumination_faerie"),
            _set("_illumination_object_suppressed", "_illumination_suppressed"),
            _call("srd_information_scene_entity", {"subject": "{object_id}"}, POSITION_EXPORTS),
            _require(
                "_info_scene_entity.metadata.is_object == true",
                "Object visibility needs an admitted scene object.",
            ),
            _set("_illumination_object_invisible", "_info_scene_entity.metadata.invisible == true"),
            _set("_illumination_object_origin", "_info_position"),
            _set("_illumination_object_plane", "_info_scene_entity.metadata.plane"),
            _set("_illumination_object_lighting", None),
            _when(
                "_info_scene_entity.metadata.lighting != null",
                [_set("_illumination_object_lighting", "_info_scene_entity.metadata.lighting")],
            ),
            _set("_illumination_object_holder_scene_id", "''"),
            _set("_object_visibility_holder", None),
            _when(
                (
                    "_info_scene_entity.metadata.carried_by != null and _info_scene_entity."
                    "metadata.carried_by != ''"
                ),
                [_set("_object_visibility_holder", "_info_scene_entity.metadata.carried_by")],
            ),
            _when(
                (
                    "_object_visibility_holder == null and _info_scene_entity.metadata.worn"
                    "_by != null and _info_scene_entity.metadata.worn_by != ''"
                ),
                [_set("_object_visibility_holder", "_info_scene_entity.metadata.worn_by")],
            ),
            _when(
                "_object_visibility_holder != null",
                [
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_object_visibility_holder}"},
                        POSITION_EXPORTS,
                    ),
                    _set("_illumination_object_origin", "_info_position"),
                    _set("_illumination_object_plane", "_info_scene_entity.metadata.plane"),
                    _set("_illumination_object_holder_scene_id", "_info_scene_entity.id"),
                    _set("_illumination_object_lighting", None),
                    _when(
                        "_info_scene_entity.metadata.lighting != null",
                        [
                            _set(
                                "_illumination_object_lighting",
                                "_info_scene_entity.metadata.lighting",
                            )
                        ],
                    ),
                    _call(
                        "check_srd_illumination_carrier",
                        {"actor_state": "{_object_visibility_holder}"},
                        ["_illumination_carrier_invisible"],
                    ),
                    _when(
                        "observer_state != null and state.has_divination_spells == true",
                        [
                            _call(
                                "check_srd_mind_spike_tracking",
                                {
                                    "caster_state": "{_observer_state_id}",
                                    "target_state": "{_object_visibility_holder}",
                                },
                                ["_mind_spike_tracking"],
                            ),
                            _when(
                                "_mind_spike_tracking == true",
                                [_set("_illumination_carrier_invisible", False)],
                            ),
                        ],
                    ),
                    _set(
                        "_illumination_object_invisible",
                        (
                            "_illumination_object_invisible == true or _illumination_carrie"
                            "r_invisible == true"
                        ),
                    ),
                ],
            ),
            _when(
                "_illumination_object_suppressed == true",
                [_set("_illumination_object_invisible", False)],
            ),
        ],
    )
    return {
        "check_srd_illumination_carrier": carrier,
        "check_srd_illumination_object_visibility": object_query,
    }


def _install_tools():
    install = _tool(
        "Persist one source-owned light or Invisible grant from the admitted spell profile.",
        [
            _entity("caster_state"),
            _parameter("spell"),
            _optional("source", "array", [""], items={"type": "string"}, min_items=1, max_items=1),
            _array("origin"),
            _parameter("plane"),
            _parameter("slot_level", "integer"),
            _parameter("instance", "integer"),
            _parameter("color"),
            _optional("combined", "boolean", False),
        ],
        [
            _lookup("spell", "_light_install_profile"),
            _set("_light_expiry", None),
            _when(
                "_light_install_profile.effect_rounds > 0",
                [
                    _set(
                        "_light_expiry",
                        "state.lasting_tick + _light_install_profile.effect_rounds * 2",
                    )
                ],
            ),
            _set("_light_install_records", "state.turn_effects"),
            _require(
                "len(_light_install_records) < 128",
                "Resolve existing lasting effects before adding another.",
            ),
            _set(
                "_light_install_record",
                {
                    "kind": "'illumination_spell'",
                    "spell": "spell",
                    "label": "spell",
                    "caster": "_caster_state_id",
                    "source": "source[0]",
                    "target": "_caster_state_id",
                    "origin": "origin",
                    "plane": "plane",
                    "instance": "instance",
                    "slot_level": "slot_level",
                    "started_tick": "state.lasting_tick",
                    "expires_tick": None,
                    "concentration": "_light_install_profile.concentration",
                    "mode": "_light_install_profile.illumination_mode",
                    "bright_radius": "_light_install_profile.bright_radius",
                    "dim_radius": "_light_install_profile.dim_radius",
                    "dark_radius": "_light_install_profile.dark_radius",
                    "color": "color",
                    "combined": "combined",
                    "owner": "'__time_only__'",
                    "phase": "'time'",
                    "turn": 1000000000,
                    "magnitude": 0,
                    "dice_count": 0,
                    "damage_die": "''",
                    "damage_type": "''",
                },
            ),
            _when(
                "_light_install_profile.effect_rounds > 0",
                [
                    _set("_light_expiry_patch", {"expires_tick": "_light_expiry"}),
                    _set(
                        "_light_install_record", "merge(_light_install_record, _light_expiry_patch)"
                    ),
                ],
            ),
            _push("_light_install_records", "_light_install_record"),
            _set("state.turn_effects", "_light_install_records"),
            _set("state.has_illumination_spells", True),
        ],
    )
    invis_target = _tool(
        "Validate actual touch and install one creature's source-owned Invisible condition.",
        [
            _entity("caster_state"),
            _entity("target_state"),
            _parameter("spell"),
            _parameter("slot_level", "integer"),
            _parameter("instance", "integer"),
            _parameter("plane"),
        ],
        [
            _require(
                "target_state.is_alive == true and target_state.conditions != null",
                "Invisible needs an admitted creature.",
            ),
            _call(
                "srd_information_scene_entity", {"subject": "{_target_state_id}"}, POSITION_EXPORTS
            ),
            _set("_invis_target_origin", "_info_position"),
            _call(
                "resolve_srd_scene_geometry_relation",
                {"actor_state": "{_caster_state_id}", "target_state": "{_target_state_id}"},
                ["_scene_distance", "_scene_same_plane", "_scene_line_of_sight"],
            ),
            _set("_invis_touch_reach", 5),
            _when(
                "caster_state.melee_reach != null",
                [_set("_invis_touch_reach", "caster_state.melee_reach")],
            ),
            _require(
                (
                    "_scene_distance <= _invis_touch_reach and _scene_same_plane == true an"
                    "d _scene_line_of_sight == true"
                ),
                "Invisibility requires actual same-plane contact within the caster's reach.",
            ),
            _when(
                "target_state.illumination_invisible_managed != true",
                [
                    _set(
                        "target_state.illumination_invisible_native",
                        "'invisible' in target_state.conditions",
                    ),
                    _set("target_state.illumination_invisible_managed", True),
                ],
            ),
            _call(
                "install_srd_illumination_source",
                {
                    "caster_state": "{_caster_state_id}",
                    "spell": "{spell}",
                    "source": "{_target_state_id}",
                    "origin": "{_invis_target_origin}",
                    "plane": "{plane}",
                    "slot_level": "{slot_level}",
                    "instance": "{instance}",
                    "color": "none",
                },
            ),
            _call("sync_srd_illumination_invisible", {"actor_state": "{_target_state_id}"}),
        ],
    )
    return {
        "install_srd_illumination_source": install,
        "install_srd_illumination_invisible": invis_target,
    }


def _casting_tool():
    """Derive casts from saved facts, resolving optional creature IDs only in their installer."""
    effects = [
        _lookup("spell", "_light_profile"),
        _when("state.lasting_tick == null", [_set("state.lasting_tick", 0)]),
        _when("state.turn_effects == null", [_set("state.turn_effects", [])]),
        _when(
            "state.illumination_cast_serial == null", [_set("state.illumination_cast_serial", 0)]
        ),
        _set("state.illumination_cast_serial", "state.illumination_cast_serial + 1"),
        _set("_light_cast_instance", "state.illumination_cast_serial"),
        _call("srd_information_scene_entity", {"subject": "{_caster_state_id}"}, POSITION_EXPORTS),
        _set("_light_cast_origin", "_info_position"),
        _set("_light_cast_plane", "_info_scene_entity.metadata.plane"),
        _require("_light_cast_plane != null", "The caster needs its actual scene plane."),
        _when(
            "_light_profile.concentration == true and caster_state.concentration_spell_id != null",
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
        _when(
            "spell == 'light' or spell == 'produce_flame'",
            [
                _set("_light_recast_records", "state.turn_effects"),
                _loop(
                    "_light_recast_records",
                    "_light_recast_record",
                    [
                        _when(
                            (
                                "_light_recast_record.kind == 'illumination_spell' and "
                                "_light_recast_record.caster == _caster_state_id and "
                                "_light_recast_record.spell == spell"
                            ),
                            [
                                _call(
                                    "remove_srd_illumination_instance",
                                    {
                                        "caster_state": "{_caster_state_id}",
                                        "instance": "{_light_recast_record.instance}",
                                    },
                                )
                            ],
                        )
                    ],
                ),
            ],
        ),
        _when(
            "_light_profile.concentration == true",
            [
                _set("caster_state.concentration_spell_id", "spell"),
                _set("caster_state.concentration_effect_family", "'illumination_spell'"),
                _set("caster_state.concentration_target_state_id", "_caster_state_id"),
                _set("caster_state.concentration_target_state_ids", []),
                _set("caster_state.concentration_started_tick", "state.lasting_tick"),
                _set(
                    "caster_state.concentration_expires_tick",
                    "state.lasting_tick + _light_profile.effect_rounds * 2",
                ),
                _set("caster_state.concentration_save_modifier", "concentration_modifier"),
                _set("caster_state.illumination_concentration_instance", "_light_cast_instance"),
            ],
        ),
        _set("_light_source", "illumination_object"),
        _set("_light_origin", "illumination_position"),
        _when(
            "_light_profile.illumination_mode == 'object' or "
            "(_light_profile.illumination_mode == 'point_or_object' and illumination_object != '')",
            [
                _require("illumination_object != ''", "Choose the touched scene object."),
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{illumination_object}"},
                    POSITION_EXPORTS,
                ),
                _require(
                    "_info_scene_entity.metadata.is_object == true",
                    "This spell targets an admitted object.",
                ),
                _set("_light_origin", "_info_position"),
                _when(
                    "spell == 'light'",
                    [
                        _require(
                            (
                                "_info_scene_entity.metadata.size_rank != null and _info_sc"
                                "ene_entity.metadata.size_rank <= 3"
                            ),
                            "Light targets one Large or smaller object.",
                        ),
                        _require(
                            (
                                "(_info_scene_entity.metadata.carried_by == null or "
                                "_info_scene_entity.metadata.carried_by == '' or "
                                "_info_scene_entity.metadata.carried_by == "
                                "_caster_state_id) and (_info_scene_entity.metadata.worn_by"
                                " == null or _info_scene_entity.metadata.worn_by == '' or "
                                "_info_scene_entity.metadata.worn_by == _caster_state_id)"
                            ),
                            "Light cannot target an object worn or carried by someone else.",
                        ),
                    ],
                ),
                _when(
                    "spell == 'daylight' or spell == 'darkness'",
                    [
                        _require(
                            (
                                "(_info_scene_entity.metadata.carried_by == null or "
                                "_info_scene_entity.metadata.carried_by == '') and "
                                "(_info_scene_entity.metadata.worn_by == null or "
                                "_info_scene_entity.metadata.worn_by == '')"
                            ),
                            "Daylight and Darkness need an object that is not worn or carried.",
                        )
                    ],
                ),
                _set("_light_cast_holder", None),
                _when(
                    (
                        "_info_scene_entity.metadata.carried_by != null and _info_scene_ent"
                        "ity.metadata.carried_by != ''"
                    ),
                    [_set("_light_cast_holder", "_info_scene_entity.metadata.carried_by")],
                ),
                _when(
                    (
                        "_light_cast_holder == null and _info_scene_entity.metadata.worn_by"
                        " != null and _info_scene_entity.metadata.worn_by != ''"
                    ),
                    [_set("_light_cast_holder", "_info_scene_entity.metadata.worn_by")],
                ),
                _when(
                    "_light_cast_holder != null",
                    [
                        _call(
                            "srd_information_scene_entity",
                            {"subject": "{_light_cast_holder}"},
                            POSITION_EXPORTS,
                        ),
                        _set("_light_origin", "_info_position"),
                    ],
                ),
                _require(
                    "_info_scene_entity.metadata.plane == _light_cast_plane",
                    "The source object must share the caster's plane.",
                ),
            ],
        ),
        _when(
            (
                "_light_profile.illumination_mode == 'object' or _light_profile.illuminatio"
                "n_mode == 'point_or_object'"
            ),
            [
                _distance("{_light_cast_origin}", "{_light_origin}", "_light_cast_distance"),
                _set("_light_cast_range", "_light_profile.range_feet"),
                _when(
                    "_light_profile.range_feet == 0",
                    [
                        _set("_light_cast_range", 5),
                        _when(
                            "caster_state.melee_reach != null",
                            [_set("_light_cast_range", "caster_state.melee_reach")],
                        ),
                    ],
                ),
                _require(
                    "_light_cast_distance <= _light_cast_range",
                    "The actual source is outside this spell's range.",
                ),
                _call(
                    "srd_information_barriers",
                    {"start": "{_light_cast_origin}", "end": "{_light_origin}", "rule": "solid"},
                    ["_information_blocked"],
                ),
                _require(
                    "_information_blocked != true", "A solid barrier blocks the spell's source."
                ),
                _call(
                    "install_srd_illumination_source",
                    {
                        "caster_state": "{_caster_state_id}",
                        "spell": "{spell}",
                        "source": "{_light_source}",
                        "origin": "{_light_origin}",
                        "plane": "{_light_cast_plane}",
                        "slot_level": "{slot_level}",
                        "instance": "{_light_cast_instance}",
                        "color": "{illumination_color}",
                    },
                ),
            ],
        ),
        _when(
            "spell == 'produce_flame'",
            [
                _call(
                    "install_srd_illumination_source",
                    {
                        "caster_state": "{_caster_state_id}",
                        "spell": "{spell}",
                        "source": "{_caster_state_id}",
                        "origin": "{_light_cast_origin}",
                        "plane": "{_light_cast_plane}",
                        "slot_level": "{slot_level}",
                        "instance": "{_light_cast_instance}",
                        "color": "flame",
                    },
                ),
                _set("caster_state.produce_flame_source_instance", "_light_cast_instance"),
                _set("caster_state.produce_flame_casting_ability", "casting_ability"),
            ],
        ),
        _when(
            "_light_profile.illumination_mode == 'invisible'",
            [
                _set("_invis_targets", []),
                _when("targets != null", [_set("_invis_targets", "targets")]),
                _when(
                    "len(_invis_targets) == 0",
                    [
                        _require(
                            "target_state != null and target_state != ''",
                            "Select the actual creature target or creatures for Invisibility.",
                        ),
                        _push("_invis_targets", "target_state"),
                    ],
                ),
                _set("_invis_max_targets", 1),
                _when(
                    "spell == 'invisibility'", [_set("_invis_max_targets", "1 + slot_level - 2")]
                ),
                _require(
                    "len(_invis_targets) <= _invis_max_targets",
                    "The slot does not permit this Invisible target count.",
                ),
                _set("_invis_seen", []),
                _loop(
                    "_invis_targets",
                    "_invis_target",
                    [
                        _require(
                            "_invis_target not in _invis_seen",
                            "Invisible targets must be distinct.",
                        ),
                        _push("_invis_seen", "_invis_target"),
                        _call(
                            "install_srd_illumination_invisible",
                            {
                                "caster_state": "{_caster_state_id}",
                                "target_state": "{_invis_target}",
                                "spell": "{spell}",
                                "slot_level": "{slot_level}",
                                "instance": "{_light_cast_instance}",
                                "plane": "{_light_cast_plane}",
                            },
                        ),
                    ],
                ),
            ],
        ),
        _when(
            "spell == 'dancing_lights'",
            [
                _require(
                    "len(illumination_points) >= 1 and len(illumination_points) <= 4",
                    "Dancing Lights creates up to four sources.",
                ),
                _require(
                    "illumination_combined != true or len(illumination_points) == 1",
                    "A combined Medium form uses one position.",
                ),
                _call(
                    "validate_srd_dancing_light_points",
                    {
                        "caster_state": "{_caster_state_id}",
                        "points": "{illumination_points}",
                        "combined": "{illumination_combined}",
                    },
                ),
                _loop(
                    "illumination_points",
                    "_dance_cast_point",
                    [
                        _call(
                            "install_srd_illumination_source",
                            {
                                "caster_state": "{_caster_state_id}",
                                "spell": "{spell}",
                                "source": "",
                                "origin": "{_dance_cast_point}",
                                "plane": "{_light_cast_plane}",
                                "slot_level": "{slot_level}",
                                "instance": "{_light_cast_instance}",
                                "color": "{illumination_color}",
                                "combined": "{illumination_combined}",
                            },
                        )
                    ],
                ),
            ],
        ),
        _when(
            "spell == 'faerie_fire'",
            [
                _call(
                    "cast_srd_faerie_fire_cube",
                    {
                        "caster_state": "{_caster_state_id}",
                        "origin": "{illumination_position}",
                        "plane": "{_light_cast_plane}",
                        "slot_level": "{slot_level}",
                        "instance": "{_light_cast_instance}",
                        "color": "{illumination_color}",
                        "save_dc": "{save_dc}",
                    },
                )
            ],
        ),
        _call("resolve_srd_illumination_overlaps", {}),
        _set(
            "_spell_summary_rows",
            [{"label": "'Effect'", "value": "'Source-owned illumination active'"}],
        ),
        _set(
            "_spell_detail_rows", [{"label": "'Spell instance'", "value": "_light_cast_instance"}]
        ),
    ]
    return _tool_scene(
        "Resolve the reviewed illumination spell's complete initial fixed rules.",
        [
            _entity("caster_state"),
            _optional("target_state", default=""),
            _parameter("spell"),
            _parameter("slot_level", "integer"),
            _parameter("concentration_modifier", "integer"),
            _parameter("casting_ability"),
            _parameter("save_dc", "integer"),
            _parameter("caster_can_touch_target", "boolean"),
            _optional("targets", "array", [], items={"type": "string"}),
            *deepcopy(CAST_CHOICES),
        ],
        effects,
    )


def _produce_flame_source_tool():
    """Retained Magic actions derive their statistics from the exact active flame source."""
    return _tool(
        "Prepare the retained Produce Flame source's actual Magic action spell attack profile.",
        [_entity("caster_state"), _optional("caster", resolve="participant")],
        [
            _require(
                "caster_state.is_alive == true",
                "Produce Flame needs its living source caster.",
            ),
            _set("_produce_flame_source_active", False),
            _set("_produce_flame_source_records", []),
            _when(
                "state.has_illumination_spells == true",
                [_set("_produce_flame_source_records", "state.turn_effects")],
            ),
            _loop(
                "_produce_flame_source_records",
                "_produce_flame_record",
                [
                    _when(
                        "_produce_flame_record.kind == 'illumination_spell' and "
                        "_produce_flame_record.spell == 'produce_flame' and "
                        "_produce_flame_record.caster == _caster_state_id and "
                        "_produce_flame_record.instance == "
                        "caster_state.produce_flame_source_instance",
                        [
                            _call(
                                "check_srd_illumination_record",
                                {
                                    "record": "{_produce_flame_record}",
                                    "caster_state": "{_caster_state_id}",
                                },
                                RECORD_EXPORTS,
                            ),
                            _when(
                                "_illumination_active == true",
                                [
                                    _set("_produce_flame_source_active", True),
                                    _set(
                                        "_produce_flame_source_instance",
                                        "_produce_flame_record.instance",
                                    ),
                                    _set("_produce_flame_origin", "_illumination_origin"),
                                    _set("_produce_flame_plane", "_illumination_plane"),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _require(
                "_produce_flame_source_active == true",
                "Summon Produce Flame before using its retained Magic action.",
            ),
            _set("_produce_flame_casting_ability", "caster_state.produce_flame_casting_ability"),
            _require(
                "_produce_flame_casting_ability == 'strength' or "
                "_produce_flame_casting_ability == 'dexterity' or "
                "_produce_flame_casting_ability == 'constitution' or "
                "_produce_flame_casting_ability == 'intelligence' or "
                "_produce_flame_casting_ability == 'wisdom' or "
                "_produce_flame_casting_ability == 'charisma'",
                "The active flame source lacks its original casting ability.",
            ),
            {
                "condition": "caster != null",
                "on_true": [
                    _require(
                        "caster.validation_status == 'validated' and "
                        "caster.package_id == 'sylvia.srd-lite.bg3' and "
                        "caster.participant_id == _caster_state_id",
                        "The flame source needs its own validated Character File.",
                    ),
                    _require(
                        "caster.data.abilities[_produce_flame_casting_ability] >= 3 and "
                        "caster.data.abilities[_produce_flame_casting_ability] <= 20",
                        "Produce Flame needs the source's actual casting ability.",
                    ),
                    _set("_produce_flame_scaling_level", "caster.data.level"),
                    _set(
                        "_produce_flame_casting_modifier",
                        "(caster.data.abilities[_produce_flame_casting_ability] - 10) // 2",
                    ),
                    _set(
                        "_produce_flame_attack_bonus",
                        "_produce_flame_casting_modifier + 2 + ((caster.data.level - 1) // 4)",
                    ),
                ],
                "on_false": [
                    _require(
                        "caster_state.creature_kind == 'monster' and "
                        "caster_state.spellcasting_profile.attack_bonus != null and "
                        "caster_state.spellcasting_profile.scaling_level != null",
                        "A creature flame source needs its saved stat-block spellcasting profile.",
                    ),
                    _set(
                        "_produce_flame_scaling_level",
                        "caster_state.spellcasting_profile.scaling_level",
                    ),
                    _set(
                        "_produce_flame_casting_modifier",
                        "caster_state.spellcasting_profile.modifier",
                    ),
                    _set(
                        "_produce_flame_attack_bonus",
                        "caster_state.spellcasting_profile.attack_bonus",
                    ),
                ],
            },
            _require(
                "_produce_flame_scaling_level >= 1 and _produce_flame_scaling_level <= 20",
                "Produce Flame needs the actual source caster level from 1 through 20.",
            ),
            _set("_produce_flame_dice_count", 1),
            _when("_produce_flame_scaling_level >= 5", [_set("_produce_flame_dice_count", 2)]),
            _when("_produce_flame_scaling_level >= 11", [_set("_produce_flame_dice_count", 3)]),
            _when("_produce_flame_scaling_level >= 17", [_set("_produce_flame_dice_count", 4)]),
            _set("_produce_flame_damage_die", "'d8'"),
            _set("_produce_flame_damage_type", "'fire'"),
            _set("_produce_flame_max_range", 60),
        ],
    )


def _point_sight_tool(*, projected=False, environment=False, subject=False):
    """Read an unoccupied point from saved ambient zones and actual source-qualified sight."""
    effects = []
    if subject:
        if projected:
            effects += [
                _call("refresh_srd_etherealness_target", {"target_state": "{_observer_state_id}"}),
                _call("refresh_srd_etherealness_target", {"target_state": "{_target_state_id}"}),
            ]
        effects += [
            _call(
                "srd_information_scene_entity", {"subject": "{_target_state_id}"}, POSITION_EXPORTS
            ),
            _require(
                "_info_scene_entity.metadata.plane != null and "
                "_info_scene_entity.metadata.lighting != null",
                "Target sight needs the target's actual admitted scene plane and ambient lighting.",
            ),
            _set("_illumination_target_entity", "_info_scene_entity"),
            _set("point", "_info_position"),
            _set("perception_plane", "''"),
        ]
    effects += [
        _call(
            "srd_information_scene_entity",
            {"subject": "{_observer_state_id}"},
            POSITION_EXPORTS,
        ),
        _require(
            "_info_scene_entity.metadata.plane != null",
            "Point sight needs the observer's actual admitted scene plane.",
        ),
        _set("_illumination_point_origin", "_info_position"),
        _set("_illumination_point_plane", "_info_scene_entity.metadata.plane"),
    ]
    if subject:
        effects += [
            _set("_illumination_target_observer_plane", "_illumination_point_plane"),
            _set("_illumination_target_plane", "_illumination_target_entity.metadata.plane"),
            _set(
                "_illumination_target_same_plane",
                "_illumination_point_plane == _illumination_target_plane",
            ),
            _set("_lit_target_invisible", "_illumination_target_entity.metadata.invisible == true"),
            _set(
                "_lit_target_ethereal",
                "_illumination_target_same_plane != true and "
                "_illumination_target_plane == 'ethereal'",
            ),
            _set("_illumination_target_projected", False),
            _set(
                "_illumination_target_permitted",
                "_illumination_target_same_plane == true or "
                "(_illumination_point_plane == 'material' and "
                "_illumination_target_plane == 'ethereal')",
            ),
        ]
        if projected:
            effects += [
                _call(
                    "measure_srd_information_distance",
                    {"start": "{_illumination_point_origin}", "end": "{point}"},
                    ["_scene_distance"],
                ),
                _when(
                    "_illumination_point_plane == 'ethereal' and "
                    "observer_state.travel_ethereal != null and "
                    "observer_state.travel_ethereal.spell == 'etherealness' and "
                    "observer_state.travel_ethereal.return_plane == _illumination_target_plane and "
                    "_scene_distance <= 60",
                    [
                        _set("perception_plane", "_illumination_target_plane"),
                        _set("_illumination_target_projected", True),
                        _set("_illumination_target_permitted", True),
                    ],
                ),
            ]
    if projected:
        effects += [
            _call(
                "resolve_srd_travel_perception",
                {
                    "observer_state": "{_observer_state_id}",
                    "point": "{point}",
                    "perception_plane": "{perception_plane}",
                },
                [
                    "_travel_perception_plane",
                    "_travel_perception_origin",
                    "_travel_perception_distance",
                ],
            ),
            _set("_illumination_point_origin", "_travel_perception_origin"),
            _set("_illumination_point_plane", "_travel_perception_plane"),
            _set("_illumination_point_distance", "_travel_perception_distance"),
        ]
    else:
        effects += [
            _require(
                "perception_plane == '' or perception_plane == _illumination_point_plane",
                "Projected point sight needs an active source-owned travel perception guard.",
            ),
            _call(
                "measure_srd_information_distance",
                {"start": "{_illumination_point_origin}", "end": "{point}"},
                ["_scene_distance"],
            ),
            _set("_illumination_point_distance", "_scene_distance"),
        ]
    if subject:
        effects += [_set("_illumination_point_plane", "_illumination_target_plane")]
    effects += [
        _require(
            " and ".join(
                f"point[{i}] >= state.spatial_scene.map.bounds.min[{i}] and "
                f"point[{i}] <= state.spatial_scene.map.bounds.max[{i}]"
                for i in range(3)
            ),
            "The observed point must be inside the actual admitted map bounds.",
        ),
        _set(
            "_illumination_point_ambient",
            "_illumination_target_entity.metadata.lighting" if subject else None,
        ),
        _set("_illumination_point_obscured", False),
        _set("_illumination_point_zones", "state.spatial_scene.zones"),
        _loop(
            "_illumination_point_zones",
            "_illumination_point_zone",
            [
                _when(
                    "_illumination_point_zone.metadata.active != false and "
                    "_illumination_point_zone.metadata.plane == _illumination_point_plane",
                    [
                        _when(
                            "_illumination_point_zone.metadata.lighting != null",
                            [
                                _call(
                                    "srd_information_segment_interval",
                                    {
                                        "start": "{point}",
                                        "end": "{point}",
                                        "volume": "{_illumination_point_zone.shape}",
                                    },
                                    ["_segment_hit"],
                                ),
                                _when(
                                    "_segment_hit == true",
                                    [
                                        _require(
                                            "_illumination_point_zone.metadata.lighting == "
                                            "'bright' or _illumination_point_zone.metadata."
                                            "lighting == 'dim' or _illumination_point_zone."
                                            "metadata.lighting == 'darkness' or "
                                            "_illumination_point_zone.metadata.lighting == "
                                            "'magical_darkness'",
                                            "The containing zone needs an actual supported "
                                            "ambient lighting value.",
                                        ),
                                        _require(
                                            "_illumination_point_ambient == null or "
                                            "_illumination_point_ambient == "
                                            "_illumination_point_zone.metadata.lighting",
                                            "Containing zones disagree on this point's "
                                            "admitted ambient lighting.",
                                        ),
                                        _set(
                                            "_illumination_point_ambient",
                                            "_illumination_point_zone.metadata.lighting",
                                        ),
                                    ],
                                ),
                            ],
                        ),
                        _when(
                            "_illumination_point_zone.metadata.obstruction == 'heavy_obscurement'",
                            [
                                _call(
                                    "srd_information_segment_interval",
                                    {
                                        "start": "{_illumination_point_origin}",
                                        "end": "{point}",
                                        "volume": "{_illumination_point_zone.shape}",
                                    },
                                    ["_segment_hit"],
                                ),
                                _when(
                                    "_segment_hit == true",
                                    [_set("_illumination_point_obscured", True)],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        _require(
            "_illumination_point_ambient != null"
            if not subject
            else "_illumination_point_ambient == 'bright' or "
            "_illumination_point_ambient == 'dim' or "
            "_illumination_point_ambient == 'darkness' or "
            "_illumination_point_ambient == 'magical_darkness'",
            "The unoccupied point needs actual ambient lighting in a containing scene zone."
            if not subject
            else "The target needs a supported actual ambient lighting value.",
        ),
        _call(
            "resolve_srd_information_light",
            {
                "point": "{point}",
                "plane": "{_illumination_point_plane}",
                "ambient": "{_illumination_point_ambient}",
            },
            ["_information_light", "_information_sunlight"],
        ),
        _set("_illumination_point_lighting", "_information_light"),
        _set("_illumination_point_opaque", False),
        _set("_illumination_point_total_cover", False),
        _set("_illumination_point_geometry", "state.spatial_scene.geometry"),
        _loop(
            "_illumination_point_geometry",
            "_illumination_point_barrier",
            [
                _when(
                    "_illumination_point_barrier.active == true and "
                    "(_illumination_point_barrier.metadata.plane == _illumination_point_plane "
                    "or (_illumination_point_barrier.metadata.plane == null and "
                    "_illumination_point_plane == 'material')) and "
                    "(_illumination_point_barrier.visibility == 'opaque' or "
                    "_illumination_point_barrier.movement == 'blocking')",
                    [
                        _call(
                            "srd_information_segment_interval",
                            {
                                "start": "{_illumination_point_origin}",
                                "end": "{point}",
                                "volume": "{_illumination_point_barrier.volume}",
                            },
                            [
                                "_segment_hit",
                                "_segment_thickness",
                                "_segment_entry",
                                "_segment_exit",
                            ],
                        ),
                        _when(
                            "_segment_hit == true and _segment_thickness > 0.000000000001",
                            [
                                _require(
                                    "_illumination_point_barrier.volume.kind == 'box' or "
                                    "_illumination_point_barrier.volume.kind == 'cylinder'",
                                    "Point sight tangency needs supported admitted "
                                    "Box or Cylinder geometry.",
                                ),
                                _set(
                                    "_lb",
                                    {
                                        "v": "_illumination_point_barrier.volume",
                                        "p": [
                                            f"_illumination_point_origin[{i}] + "
                                            f"(point[{i}] - _illumination_point_origin[{i}]) * "
                                            "(_segment_entry + _segment_exit) * 0.5"
                                            for i in range(3)
                                        ],
                                    },
                                ),
                                _when(
                                    "(_lb.v.kind == 'box' and "
                                    + " and ".join(
                                        f"_lb.p[{i}] > _lb.v.min[{i}] and "
                                        f"_lb.p[{i}] < _lb.v.max[{i}]"
                                        for i in range(3)
                                    )
                                    + ") or (_lb.v.kind == 'cylinder' and "
                                    "(_lb.p[0] - _lb.v.center[0]) ** 2 + "
                                    "(_lb.p[1] - _lb.v.center[1]) ** 2 < _lb.v.radius ** 2 and "
                                    "_lb.p[2] > _lb.v.center[2] and "
                                    "_lb.p[2] < _lb.v.center[2] + _lb.v.height)",
                                    [
                                        _when(
                                            "_illumination_point_barrier.visibility == 'opaque'",
                                            [_set("_illumination_point_opaque", True)],
                                        ),
                                        _when(
                                            "_illumination_point_barrier.movement == 'blocking'",
                                            [_set("_illumination_point_total_cover", True)],
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
    ]
    if environment:
        effects += [
            _call(
                "resolve_srd_environment_segment",
                {
                    "start": "{_illumination_point_origin}",
                    "end": "{point}",
                    "plane": "{_illumination_point_plane}",
                },
                ["_environment_sight_obscured"],
            ),
            _when(
                "_environment_sight_obscured == true",
                [_set("_illumination_point_obscured", True)],
            ),
        ]
    effects += [
        _set("_illumination_point_obstruction", "'clear'"),
        _when(
            "_illumination_point_obscured == true",
            [_set("_illumination_point_obstruction", "'heavy_obscurement'")],
        ),
        _when(
            "_illumination_point_opaque == true",
            [_set("_illumination_point_obstruction", "'solid'")],
        ),
        _set("_illumination_point_sight_distance", "ceil(_illumination_point_distance)"),
        _call(
            "resolve_srd_sight",
            {
                "observer_state": "{_observer_state_id}",
                "distance": "{_illumination_point_sight_distance}",
                "lighting": "{_illumination_point_lighting}",
                "obstruction": "{_illumination_point_obstruction}",
                **(
                    {
                        "subject_state": "{_target_state_id}",
                        "subject_invisible": "{_lit_target_invisible}",
                        "subject_ethereal": "{_lit_target_ethereal}",
                    }
                    if subject
                    else {"subject_invisible": False}
                ),
            },
            ["_sight_can_see"],
        ),
        _set("_illumination_point_can_see", "_sight_can_see"),
        _when(
            "observer_state.blindsight_feet != null and observer_state.blindsight_feet >= "
            "_illumination_point_distance and _illumination_point_total_cover != true"
            + (
                " and (_illumination_target_same_plane == true or "
                "_illumination_target_projected == true)"
                if subject
                else ""
            ),
            [_set("_illumination_point_can_see", True)],
        ),
    ]
    if subject:
        zones = next(
            effect["for_each"]["do"][0]["on_true"]
            for effect in effects
            if effect.get("for_each", {}).get("as") == "_illumination_point_zone"
        )
        zones[:] = [
            effect
            for effect in zones
            if effect.get("condition") != "_illumination_point_zone.metadata.lighting != null"
        ]
        effects += [
            _set(
                "_illumination_target_can_see",
                "_illumination_point_can_see == true and _illumination_target_permitted == true",
            ),
        ]

        def target_locals(value, key=""):
            if isinstance(value, str) and key != "error":
                value = re.sub(r"\bpoint\b", "_lit_target_point", value)
                return re.sub(
                    r"\bperception_plane\b", "_illumination_target_perception_plane", value
                )
            if isinstance(value, list):
                return [target_locals(child) for child in value]
            if isinstance(value, dict):
                return {name: target_locals(child, name) for name, child in value.items()}
            return value

        effects = target_locals(effects)
        effects += [_set("_illumination_target_position", "_lit_target_point")]
    return _tool_scene(
        "Resolve actual same-plane or source-qualified projected sight of an unoccupied point."
        if not subject
        else "Resolve an actual saved target through source light, geometry and owning senses.",
        [_entity("observer_state"), _array("point"), _optional("perception_plane", default="")]
        if not subject
        else [_entity("observer_state"), _entity("target_state")],
        effects,
    )


def _target_sight_tools(*, projected=False, environment=False):
    """Share the actual target sight owner with the private opportunity reaction adapter."""
    target = _point_sight_tool(projected=projected, environment=environment, subject=True)
    exports = [
        "_illumination_target_can_see",
        "_illumination_target_same_plane",
        "_illumination_target_observer_plane",
        "_illumination_target_plane",
        "_illumination_target_projected",
        "_illumination_target_position",
        "_illumination_point_origin",
        "_illumination_point_distance",
        "_illumination_point_lighting",
        "_illumination_point_obscured",
    ]
    opportunity = _tool(
        "Derive whether the actual opportunity observer can see the saved moving creature.",
        [_entity("observer_state"), _entity("target_state")],
        [
            _call(
                "resolve_srd_illumination_target_sight",
                {"observer_state": "{_observer_state_id}", "target_state": "{_target_state_id}"},
                exports,
            ),
            _set("_observer_can_see_target", "_illumination_target_can_see"),
        ],
    )
    return {
        "resolve_srd_illumination_target_sight": target,
        "resolve_srd_opportunity_perception": opportunity,
    }


def _dancing_tools():
    validate = _tool_scene(
        "Enforce actual casting range and each light's source-defined companion separation.",
        [
            _entity("caster_state"),
            _parameter(
                "points",
                "array",
                items={"type": "array", "items": {"type": "number"}},
                min_items=1,
                max_items=4,
            ),
            _parameter("combined", "boolean"),
        ],
        [
            _call(
                "srd_information_scene_entity", {"subject": "{_caster_state_id}"}, POSITION_EXPORTS
            ),
            _set("_dance_caster_origin", "_info_position"),
            _set("_dance_point_index", 0),
            _loop(
                "points",
                "_dance_point",
                [
                    _require("len(_dance_point) == 3", "A light position needs three coordinates."),
                    _distance("{_dance_caster_origin}", "{_dance_point}", "_dance_point_range"),
                    _require(
                        "_dance_point_range <= 120",
                        "A Dancing Light must be within 120 feet of its caster.",
                    ),
                    _set("_dance_neighbor", "combined == true"),
                    _set("_dance_neighbor_index", 0),
                    _loop(
                        "points",
                        "_dance_other_point",
                        [
                            _when(
                                "_dance_neighbor_index != _dance_point_index",
                                [
                                    _distance(
                                        "{_dance_point}",
                                        "{_dance_other_point}",
                                        "_dance_separation",
                                    ),
                                    _when(
                                        "_dance_separation <= 20", [_set("_dance_neighbor", True)]
                                    ),
                                ],
                            ),
                            _set("_dance_neighbor_index", "_dance_neighbor_index + 1"),
                        ],
                    ),
                    _require(
                        "_dance_neighbor == true",
                        (
                            "Each Dancing Light must be within 20 feet of another light; us"
                            "e a combined form for one source."
                        ),
                    ),
                    _set("_dance_point_index", "_dance_point_index + 1"),
                ],
            ),
        ],
    )
    move = _public(
        _tool_scene(
            "Use a Bonus Action to move each existing Dancing Light at most 60 feet within range.",
            [
                _entity("caster_state"),
                _parameter(
                    "points",
                    "array",
                    items={"type": "array", "items": {"type": "number"}},
                    min_items=1,
                    max_items=4,
                ),
            ],
            [
                _call("refresh_srd_illumination_spells", {}),
                _set("_dance_move_records", "state.turn_effects"),
                _set("_dance_move_count", 0),
                _set("_dance_move_combined", False),
                _loop(
                    "_dance_move_records",
                    "_dance_move_record",
                    [
                        _when(
                            (
                                "_dance_move_record.kind == 'illumination_spell' and "
                                "_dance_move_record.caster == _caster_state_id and "
                                "_dance_move_record.spell == 'dancing_lights'"
                            ),
                            [
                                _set("_dance_move_count", "_dance_move_count + 1"),
                                _set("_dance_move_combined", "_dance_move_record.combined"),
                            ],
                        )
                    ],
                ),
                _require(
                    "_dance_move_count > 0 and _dance_move_count == len(points)",
                    "Supply one destination for each active Dancing Light.",
                ),
                _call(
                    "validate_srd_dancing_light_points",
                    {
                        "caster_state": "{_caster_state_id}",
                        "points": "{points}",
                        "combined": "{_dance_move_combined}",
                    },
                ),
                _call(
                    "spend_spell_casting_budget",
                    {
                        "caster_state": "{_caster_state_id}",
                        "casting_time": "bonus_action",
                        "slot_resource": "cantrip",
                    },
                ),
                _set("_dance_move_keep", []),
                _set("_dance_move_index", 0),
                _loop(
                    "_dance_move_records",
                    "_dance_move_record",
                    [
                        _when(
                            (
                                "_dance_move_record.kind == 'illumination_spell' and "
                                "_dance_move_record.caster == _caster_state_id and "
                                "_dance_move_record.spell == 'dancing_lights'"
                            ),
                            [
                                _set("_dance_destination", "points[_dance_move_index]"),
                                _distance(
                                    "{_dance_move_record.origin}",
                                    "{_dance_destination}",
                                    "_dance_move_distance",
                                ),
                                _require(
                                    "_dance_move_distance <= 60",
                                    "Dancing Lights can move at most 60 feet per Bonus Action.",
                                ),
                                _set("_dance_move_patch", {"origin": "_dance_destination"}),
                                _set(
                                    "_dance_move_record",
                                    "merge(_dance_move_record, _dance_move_patch)",
                                ),
                                _set("_dance_move_index", "_dance_move_index + 1"),
                            ],
                        ),
                        _push("_dance_move_keep", "_dance_move_record"),
                    ],
                ),
                _set("state.turn_effects", "_dance_move_keep"),
                _call("resolve_srd_illumination_overlaps", {}),
            ],
        ),
        "exploration",
        "Dancing Lights moved",
        [("Lights", "_dance_move_count")],
    )
    return {"validate_srd_dancing_light_points": validate, "move_srd_dancing_lights": move}


def _faerie_tools():
    target = _tool(
        "Apply the cube's actual Dexterity save before outlining an admitted creature.",
        [
            _entity("caster_state"),
            _entity("target_state"),
            _array("origin"),
            _parameter("plane"),
            _parameter("slot_level", "integer"),
            _parameter("instance", "integer"),
            _parameter("color"),
            _parameter("save_dc", "integer"),
        ],
        [
            _require(
                "target_state.saving_throw_modifiers.dexterity != null",
                "Faerie Fire needs the creature's saved Dexterity save modifier.",
            ),
            _call(
                "d20_test",
                {
                    "actor_state": "{_target_state_id}",
                    "ability": "dexterity",
                    "kind": "saving_throw",
                    "ability_modifier": "{target_state.saving_throw_modifiers.dexterity}",
                    "proficiency_bonus": 0,
                    "proficiency_factor": "none",
                    "circumstantial_modifier": 0,
                    "target_number": "{save_dc}",
                    "advantage": False,
                    "disadvantage": False,
                },
                ["_outcome"],
            ),
            _when(
                "_outcome == 'failure'",
                [
                    _call(
                        "install_srd_illumination_source",
                        {
                            "caster_state": "{_caster_state_id}",
                            "spell": "faerie_fire",
                            "source": "{_target_state_id}",
                            "origin": "{origin}",
                            "plane": "{plane}",
                            "slot_level": "{slot_level}",
                            "instance": "{instance}",
                            "color": "{color}",
                        },
                    )
                ],
            ),
        ],
    )
    cast = _tool_scene(
        (
            "Derive objects and creatures in the admitted 20-foot Cube, with actual saves f"
            "or creatures."
        ),
        [
            _entity("caster_state"),
            _array("origin"),
            _parameter("plane"),
            _parameter("slot_level", "integer"),
            _parameter("instance", "integer"),
            _parameter("color"),
            _parameter("save_dc", "integer"),
        ],
        _snapshot()
        + [
            _require(
                "color == 'blue' or color == 'green' or color == 'violet'",
                "Faerie Fire uses blue, green or violet light.",
            ),
            _call(
                "srd_information_scene_entity", {"subject": "{_caster_state_id}"}, POSITION_EXPORTS
            ),
            _distance("{_info_position}", "{origin}", "_faerie_range"),
            _require("_faerie_range <= 60", "The Faerie Fire cube origin must be within 60 feet."),
            _set("_faerie_cube_max", [f"origin[{axis}] + 20" for axis in range(3)]),
            _spatial(
                "area",
                {
                    "map_id": "{state.spatial_scene.map.id}",
                    "shape": {"kind": "box", "min": "{origin}", "max": "{_faerie_cube_max}"},
                    "include": ["entities"],
                    "max_results": 64,
                },
                "_faerie_area",
            ),
            _require(
                "_faerie_area.truncated != true",
                "The admitted Faerie Fire cube contains more than 64 subjects.",
            ),
            _set("_faerie_scene_entries", "_faerie_area.results"),
            _loop(
                "_faerie_scene_entries",
                "_faerie_reference",
                [
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_faerie_reference.id}"},
                        POSITION_EXPORTS,
                    ),
                    _set("_faerie_entry", "_info_scene_entity"),
                    _when(
                        "_faerie_entry.metadata.plane == plane",
                        [
                            _call(
                                "srd_information_barriers",
                                {
                                    "start": "{origin}",
                                    "end": "{_faerie_entry.position}",
                                    "rule": "solid",
                                },
                                ["_information_blocked"],
                            ),
                            _when(
                                "_information_blocked != true",
                                [
                                    _when(
                                        "_faerie_entry.metadata.is_object == true",
                                        [
                                            _call(
                                                "install_srd_illumination_source",
                                                {
                                                    "caster_state": "{_caster_state_id}",
                                                    "spell": "faerie_fire",
                                                    "source": "{_faerie_entry.id}",
                                                    "origin": "{_faerie_entry.position}",
                                                    "plane": "{plane}",
                                                    "slot_level": "{slot_level}",
                                                    "instance": "{instance}",
                                                    "color": "{color}",
                                                },
                                            )
                                        ],
                                    ),
                                    _when(
                                        "_faerie_entry.metadata.participant_ref != null",
                                        [
                                            _call(
                                                "save_srd_faerie_fire_target",
                                                {
                                                    "caster_state": "{_caster_state_id}",
                                                    ("target_state"): (
                                                        "{_faerie_entry.metadata.participant_ref}"
                                                    ),
                                                    "origin": "{_faerie_entry.position}",
                                                    "plane": "{plane}",
                                                    "slot_level": "{slot_level}",
                                                    "instance": "{instance}",
                                                    "color": "{color}",
                                                    "save_dc": "{save_dc}",
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
    )
    return {"save_srd_faerie_fire_target": target, "cast_srd_faerie_fire_cube": cast}


def _public_tools():
    cover = _public(
        _tool_scene(
            (
                "Record actual opaque covering of an admitted light-source object; covering"
                " does not quench Continual Flame."
            ),
            [_parameter("object_id"), _parameter("covered", "boolean")],
            [
                _call("srd_information_scene_entity", {"subject": "{object_id}"}, POSITION_EXPORTS),
                _require(
                    "_info_scene_entity.metadata.is_object == true",
                    "Covering requires an admitted source object.",
                ),
                _set("_cover_patch", {"opaque_cover": "covered"}),
                _set("_cover_metadata", "merge(_info_scene_entity.metadata, _cover_patch)"),
                {
                    "spatial_mutation": {
                        "scene": "state.spatial_scene",
                        "map_id": "{state.spatial_scene.map.id}",
                        "expected_revision": "{state.spatial_scene.map.revision}",
                        "target": {"kind": "entity", "id": "{_info_scene_entity.id}"},
                        "patch": {"metadata": "{_cover_metadata}"},
                    },
                    "store_as": "_cover_mutation",
                },
                _call("resolve_srd_illumination_overlaps", {}),
                _set("_source_covered", "covered"),
            ],
        ),
        "exploration",
        "Light source covering",
        [("Covered", "_source_covered")],
    )
    dismiss = _public(
        _tool(
            (
                "Dismiss this caster's selected illumination effect, preserving every indep"
                "endent source."
            ),
            [_entity("caster_state"), _parameter("spell", "enum", enum_values=sorted(PROFILES))],
            [
                _require(
                    (
                        "caster_state.is_alive == true and not ('incapacitated' in "
                        "caster_state.conditions or 'paralyzed' in caster_state.conditions "
                        "or 'petrified' in caster_state.conditions or 'stunned' in "
                        "caster_state.conditions or 'unconscious' in "
                        "caster_state.conditions)"
                    ),
                    "An Incapacitated caster cannot dismiss a spell.",
                ),
                _set("_dismiss_light_count", 0),
                _set("_dismiss_light_records", "state.turn_effects"),
                _loop(
                    "_dismiss_light_records",
                    "_dismiss_light_record",
                    [
                        _when(
                            (
                                "_dismiss_light_record.kind == 'illumination_spell' and "
                                "_dismiss_light_record.caster == _caster_state_id and "
                                "_dismiss_light_record.spell == spell"
                            ),
                            [
                                _set("_dismiss_light_count", "_dismiss_light_count + 1"),
                                _call(
                                    "remove_srd_illumination_instance",
                                    {
                                        "caster_state": "{_caster_state_id}",
                                        "instance": "{_dismiss_light_record.instance}",
                                    },
                                ),
                            ],
                        )
                    ],
                ),
                _require(
                    "_dismiss_light_count > 0",
                    "This caster has no selected illumination spell to dismiss.",
                ),
            ],
        ),
        "exploration",
        "Illumination dismissed",
        [("Sources released", "_dismiss_light_count")],
    )
    return {"set_srd_illumination_cover": cover, "dismiss_srd_illumination_spell": dismiss}


def _normalize_calls(effects):
    """Stage expression arguments as locals for the closed call-template grammar."""
    result = []
    for effect in effects:
        for key in ("on_true", "on_false"):
            if key in effect:
                effect[key] = _normalize_calls(effect[key])
        if "for_each" in effect:
            effect["for_each"]["do"] = _normalize_calls(effect["for_each"]["do"])
        if "call" in effect:
            if effect["call"] == "install_srd_illumination_source" and isinstance(
                effect["params"].get("source"), str
            ):
                source = effect["params"]["source"]
                if source:
                    local = "_illumination_anchor_" + str(len(result))
                    result.append(_set(local, [source[1:-1]]))
                    effect["params"]["source"] = "{" + local + "}"
                else:
                    effect["params"]["source"] = [""]
            for name, value in effect["params"].items():
                if (
                    isinstance(value, str)
                    and re.fullmatch(r"\{[^{}]+\}", value)
                    and not re.fullmatch(r"\{[A-Za-z_][A-Za-z0-9_.]*\}", value)
                ):
                    local = "_illumination_argument_" + str(len(result)) + "_" + name
                    result.append(_set(local, value[1:-1]))
                    effect["params"][name] = "{" + local + "}"
        result.append(effect)
    return result


def _compose_invisibility_roll(tool):
    """Query actual Invisible attackers and end their source after the existing Dice branch."""
    d20 = deepcopy(tool)
    query = _when(
        "actor_state != null and kind == 'attack_roll' and "
        "'invisible' in actor_state.conditions and state.has_illumination_spells == true",
        [
            _call(
                "check_srd_illumination_subject",
                {"subject": "{_actor_state_id}"},
                SUBJECT_EXPORTS,
            ),
            _set("_roll_invisible_suppressed", "_illumination_suppressed"),
        ],
    )
    ending = _when(
        "actor_state != null and state.has_illumination_spells == true",
        [
            _call(
                "end_srd_invisibility_for_actor",
                {"actor_state": "{_actor_state_id}", "event": "attack_roll"},
            )
        ],
    )

    def owns_query(effect):
        return any(
            child.get("set", {}).get("field") == "_roll_invisible_suppressed"
            for child in effect.get("on_true", [])
        )

    def owns_ending(effect):
        children = effect.get("on_true", [])
        return len(children) == 1 and children[0].get("call") == "end_srd_invisibility_for_actor"

    # Normalize previously emitted hooks too: another owner may have retained their calls.
    d20["effects"] = [
        effect
        for effect in d20["effects"]
        if effect.get("set", {}).get("field") != "_roll_invisible_suppressed"
        and not owns_query(effect)
        and not owns_ending(effect)
    ]
    d20["effects"].insert(0, query)
    attack_branches = []

    def compose(effects):
        for effect in effects:
            condition = effect.get("condition", "")
            if (
                "'invisible' in actor_state.conditions" in condition
                and "_roll_invisible_suppressed != true" not in condition
                and not owns_query(effect)
            ):
                effect["condition"] = "_roll_invisible_suppressed != true and (" + condition + ")"
            if condition == "kind == 'attack_roll'" and any(
                child.get("set", {}).get("field") == "_attack_outcome"
                for child in effect.get("on_true", [])
            ):
                attack_branches.append(effect)
                effect["on_true"] = [
                    child for child in effect["on_true"] if not owns_ending(child)
                ] + [deepcopy(ending)]
            for key in ("on_true", "on_false"):
                if key in effect:
                    compose(effect[key])

    compose(d20["effects"])
    if len(attack_branches) != 1:
        raise SystemExit("Invisible requires the actual post-Dice attack outcome branch")
    bound = _tool(d20["description"], d20["parameters"], d20["effects"], d20["pre_conditions"])
    bound["metadata"] = d20["metadata"]
    return bound


def _compose_consumers(tools, changed):
    """Preserve existing Divination sight while adding exact source-owned later consequences."""
    sight_name = (
        "resolve_srd_sight_node" if "resolve_srd_sight_node" in tools else "resolve_srd_sight"
    )
    sight = deepcopy(tools[sight_name])
    if "check_srd_illumination_subject" not in str(sight["effects"]):
        index = next(
            i
            for i, e in enumerate(sight["effects"])
            if e.get("set", {}).get("field") == "_sight_light"
        )
        sight["effects"].insert(
            index,
            _when(
                "subject_state != null and state.has_illumination_spells == true",
                [
                    _call(
                        "check_srd_illumination_subject",
                        {"subject": "{_subject_state_id}"},
                        SUBJECT_EXPORTS,
                    ),
                    _when("_illumination_suppressed == true", [_set("_sight_invisible", False)]),
                ],
            ),
        )
    bound = _tool(
        sight["description"], sight["parameters"], sight["effects"], sight["pre_conditions"]
    )
    bound["metadata"] = sight["metadata"]
    changed[sight_name] = bound
    attack = deepcopy(tools["resolve_srd_attack_conditions"])
    if "check_srd_illumination_subject" not in str(attack["effects"]):

        def visibility_locals(value):
            if isinstance(value, str):
                value = re.sub(
                    r"\bactor_can_see_target\b", "_illumination_actor_can_see_target", value
                )
                return re.sub(
                    r"\btarget_can_see_actor\b", "_illumination_target_can_see_actor", value
                )
            if isinstance(value, list):
                return [visibility_locals(child) for child in value]
            if isinstance(value, dict):
                return {key: visibility_locals(child) for key, child in value.items()}
            return value

        attack["effects"] = visibility_locals(attack["effects"])
        attack["effects"].insert(
            0, _set("_illumination_actor_can_see_target", "actor_can_see_target")
        )
        attack["effects"].insert(
            1, _set("_illumination_target_can_see_actor", "target_can_see_actor")
        )
        attack["effects"].insert(
            2,
            _when(
                "state.has_illumination_spells == true",
                [
                    _call(
                        "resolve_srd_scene_relation",
                        {"actor_state": "{_actor_state_id}", "target_state": "{_target_state_id}"},
                        ["_scene_can_see"],
                    ),
                    _set("_illumination_actor_can_see_target", "_scene_can_see"),
                    _call(
                        "resolve_srd_scene_relation",
                        {"actor_state": "{_target_state_id}", "target_state": "{_actor_state_id}"},
                        ["_scene_can_see"],
                    ),
                    _set("_illumination_target_can_see_actor", "_scene_can_see"),
                ],
            ),
        )
        attack["effects"].append(
            _when(
                "state.has_illumination_spells == true",
                [
                    _call(
                        "check_srd_illumination_subject",
                        {"subject": "{_target_state_id}"},
                        SUBJECT_EXPORTS,
                    ),
                    _when(
                        (
                            "_illumination_faerie == true and _illumination_actor_can_see_t"
                            "arget == true"
                        ),
                        [_set("_condition_advantage", True)],
                    ),
                ],
            )
        )
    changed["resolve_srd_attack_conditions"] = _tool(
        attack["description"], attack["parameters"], attack["effects"], attack["pre_conditions"]
    )
    changed["d20_test"] = _compose_invisibility_roll(tools["d20_test"])
    clock = deepcopy(tools["advance_srd_lasting_clock"])
    if "refresh_srd_illumination_spells" not in str(clock["effects"]):
        index = next(
            i
            for i, e in enumerate(clock["effects"])
            if e.get("set", {}).get("field") == "lasting_state.lasting_tick"
        )
        clock["effects"].insert(index + 1, _call("refresh_srd_illumination_spells", {}))
    changed["advance_srd_lasting_clock"] = clock
    process = deepcopy(tools["process_srd_turn_effects"])

    def preserve_permanent(effects):
        for effect in effects:
            condition = effect.get("condition", "")
            if (
                "_effect.expires_tick" in condition
                and "_effect.expires_tick != null" not in condition
            ):
                effect["condition"] = "_effect.expires_tick != null and (" + condition + ")"
            for key in ("on_true", "on_false"):
                if key in effect:
                    preserve_permanent(effect[key])
            if "for_each" in effect:
                preserve_permanent(effect["for_each"]["do"])

    preserve_permanent(process["effects"])
    changed["process_srd_turn_effects"] = process
    end = deepcopy(tools["end_srd_concentration_node"])
    if "remove_srd_illumination_instance" not in str(end["effects"]):
        end["effects"].insert(
            0,
            _when(
                "source_state.concentration_effect_family == 'illumination_spell'",
                [
                    _call(
                        "remove_srd_illumination_instance",
                        {
                            "caster_state": "{_source_state_id}",
                            "instance": "{source_state.illumination_concentration_instance}",
                            "clear_concentration": False,
                        },
                    )
                ],
            ),
        )
    changed["end_srd_concentration_node"] = end
    movement = deepcopy(tools["move_srd_scene_creature_node"])
    if "resolve_srd_illumination_overlaps" not in str(movement["effects"]):
        movement["effects"] += [
            _call("refresh_srd_illumination_spells", {}),
            _call("resolve_srd_illumination_overlaps", {}),
        ]
    changed["move_srd_scene_creature_node"] = movement
    information_invisible = deepcopy(tools["resolve_srd_information_subject_invisibility"])
    if "check_srd_illumination_subject" not in str(information_invisible["effects"]):
        information_invisible["effects"].append(
            _when(
                "state.has_illumination_spells == true",
                [
                    _call(
                        "check_srd_illumination_subject",
                        {"subject": "{_subject_state_id}"},
                        SUBJECT_EXPORTS,
                    ),
                    _when(
                        "_illumination_suppressed == true",
                        [_set("_information_subject_invisible", False)],
                    ),
                ],
            )
        )
    changed["resolve_srd_information_subject_invisibility"] = information_invisible
    initiative = deepcopy(tools["start_combat_encounter_entities"])
    for actor in ("first_actor", "second_actor"):
        short = actor.removesuffix("_actor")
        local = "_illumination_" + short + "_initiative_advantage"
        call = _call("check_srd_illumination_initiative", {"actor_state": "{_" + actor + "_id}"})
        call["export_context"] = {local: "_illumination_initiative_advantage"}
        index = next(
            i
            for i, e in enumerate(initiative["effects"])
            if e.get("dice", {}).get("modifier") == actor + ".initiative_modifier"
        )
        if not any(
            effect.get("call") == call["call"] and effect.get("params") == call["params"]
            for effect in initiative["effects"]
        ):
            initiative["effects"].insert(index, call)
            index += 1
        die = initiative["effects"][index]["dice"]
        benefit = "(" + actor + ".spell_foresight == true or " + local + " == true)"
        die["advantage"] = benefit + " and " + short + "_surprised != true"
        die["disadvantage"] = short + "_surprised == true and not " + benefit
    changed["start_combat_encounter_entities"] = initiative
    group = deepcopy(tools["roll_srd_actor_group_initiative"])
    index = next(i for i, e in enumerate(group["effects"]) if "dice" in e)
    if "check_srd_illumination_initiative" not in str(group["effects"]):
        group["effects"].insert(
            index,
            _call(
                "check_srd_illumination_initiative",
                {"actor_state": "{_actor_state_id}"},
                ["_illumination_initiative_advantage"],
            ),
        )
        index += 1
    group["effects"][index]["dice"]["advantage"] = (
        "actor_state.spell_foresight == true or _illumination_initiative_advantage == true"
    )
    changed["roll_srd_actor_group_initiative"] = group


def extend_runtime(text):
    """Emit illumination owners after Divination and before reaction/outer guard composition."""
    tools = _load_runtime(text)["tools"]
    changed = (
        _record_tools()
        | _light_tools(tools["resolve_srd_information_light"])
        | _object_visibility_tools()
        | _install_tools()
        | _dancing_tools()
        | _faerie_tools()
        | _public_tools()
    )
    changed["cast_spell_illumination_spell"] = _casting_tool()
    changed["prepare_srd_produce_flame_source"] = _produce_flame_source_tool()
    changed["resolve_srd_illumination_point_sight"] = _point_sight_tool(
        projected="resolve_srd_travel_perception" in tools,
        environment="resolve_srd_environment_segment" in tools,
    )
    changed.update(
        _target_sight_tools(
            projected="resolve_srd_travel_perception" in tools,
            environment="resolve_srd_environment_segment" in tools,
        )
    )
    _compose_consumers(tools, changed)
    cast = deepcopy(tools["cast_spell"])
    known = {p["name"] for p in cast["parameters"]}
    cast["parameters"] += [deepcopy(p) for p in CAST_CHOICES if p["name"] not in known]
    params = {p["name"]: "{" + p["name"] + "}" for p in CAST_CHOICES}
    params.update(
        caster_state="{_caster_state_id}",
        target_state="{_scalar_target_id}",
        spell="{spell}",
        slot_level="{_slot_level}",
        concentration_modifier="{_casting_profile.concentration_save_modifier}",
        casting_ability="{_casting_profile.ability}",
        save_dc="{_casting_profile.save_dc}",
        caster_can_touch_target="{_caster_can_touch_target}",
        targets="{targets}",
    )
    branch = _when(
        "_spell_definition.family == 'illumination_spell'",
        [_call("cast_spell_illumination_spell", params)],
    )
    branch["on_true"][0]["export_context"] = {
        "_nested_illumination_summary": "_spell_summary_rows",
        "_nested_illumination_details": "_spell_detail_rows",
    }
    branch["on_true"] += [
        _loop(
            "_nested_illumination_summary",
            "_illumination_row",
            [_push("_spell_summary_rows", "_illumination_row")],
        ),
        _loop(
            "_nested_illumination_details",
            "_illumination_row",
            [_push("_spell_detail_rows", "_illumination_row")],
        ),
    ]

    def add_branch(effects):
        indices = [
            i
            for i, e in enumerate(effects)
            if "_spell_definition.family ==" in e.get("condition", "")
        ]
        if indices:
            effects[:] = [
                e
                for e in effects
                if e.get("condition") != "_spell_definition.family == 'illumination_spell'"
            ]
            last = max(
                i
                for i, e in enumerate(effects)
                if "_spell_definition.family ==" in e.get("condition", "")
            )
            effects.insert(last + 1, branch)
            return True
        for e in effects:
            for key in ("on_true", "on_false"):
                if key in e and add_branch(e[key]):
                    return True
        return False

    if not add_branch(cast["effects"]):
        raise ValueError("No spell family dispatcher found")
    changed["cast_spell"] = cast
    for name, tool in changed.items():
        tool["effects"] = _normalize_calls(tool["effects"])
        if name not in {
            "resolve_srd_information_light",
            "cast_spell_illumination_spell",
            "check_srd_illumination_record",
            "check_srd_illumination_object_visibility",
            "cast_srd_faerie_fire_cube",
            "set_srd_illumination_cover",
            "validate_srd_dancing_light_points",
            "move_srd_dancing_lights",
            "move_srd_scene_creature_node",
        }:
            rebound = _tool(
                tool["description"],
                tool["parameters"],
                tool["effects"],
                tool.get("pre_conditions", []),
            )
            rebound["metadata"] = tool["metadata"]
            changed[name] = rebound
    # The existing wrapper helper expects its own public name set; these leaves bind explicitly.
    for name in PUBLIC_TOOLS:
        tool = changed[name]
        if any(p["name"] == "lasting_state" for p in tool["parameters"]):
            node = deepcopy(tool)
            node["metadata"] = {
                "category": "internal",
                "visible_to_player": False,
                "costs_action": False,
            }
            changed[name + "_node"] = node
            tool["parameters"] = [p for p in tool["parameters"] if p["name"] != "lasting_state"]
            args = {
                p["name"]: "{"
                + ("_" + p["name"] + "_id" if p.get("resolve") == "entity" else p["name"])
                + "}"
                for p in tool["parameters"]
            }
            args["lasting_state"] = "state"
            exports = [
                row["value"]["path"].removeprefix("context.")
                for row in tool["metadata"]["result_card"]["summary"]
            ]
            tool["effects"] = [_call(name + "_node", args, exports)]
    text = _replace_tools(text, changed)
    if "*d20_result_card" in text and "&d20_result_card" not in text:
        start = text.index("  d20_test:")
        index = text.index("      result_card:", start)
        text = text[:index] + text[index:].replace(
            "      result_card:", "      result_card: &d20_result_card", 1
        )
    if "      illumination_spell:" not in text:
        schema = yaml.dump(
            {"illumination_spell": _schema()}, Dumper=_RuntimeDumper, sort_keys=False
        )
        text = text.replace(
            "      information_spell:",
            "".join("      " + line for line in schema.splitlines(True))
            + "\n      information_spell:",
            1,
        )
    return text


def _moonbeam_light_tools(runtime):
    """Join source-owned Moonbeam light and retirement at existing light/overlap boundaries."""
    tools = runtime["tools"]
    required = {
        "resolve_srd_environment_light",
        "refresh_srd_moonbeam_darkness",
        "refresh_srd_environment_mode_indices",
    }
    if not required.issubset(tools):
        raise ValueError("Moonbeam light needs its exact source-owned environment helpers.")
    name = "resolve_srd_information_light"
    if "resolve_srd_information_light_node" in tools:
        name += "_node"
    light = deepcopy(tools[name])
    overlap = deepcopy(tools["resolve_srd_illumination_overlaps"])
    if not all(
        any(p["name"] == "lasting_state" for p in t["parameters"]) for t in (light, overlap)
    ):
        raise ValueError("Moonbeam light needs the owning light tools' actual lasting state.")
    if "resolve_srd_environment_light" not in str(light["effects"]):
        final_dark = [
            i
            for i, e in enumerate(light["effects"])
            if e.get("condition") == "_light_magic_dark == true"
        ]
        if len(final_dark) != 1 or not any(
            e.get("set", {}).get("field") == "_light_magic_dim" for e in light["effects"]
        ):
            raise ValueError("Moonbeam light needs the actual final magical Dim/Darkness flags.")
        light["effects"].insert(
            final_dark[0],
            _when(
                "lasting_state.has_moonbeam == true",
                [
                    _call(
                        "resolve_srd_environment_light",
                        {
                            "point": "{point}",
                            "plane": "{plane}",
                            "lasting_state": "{_lasting_state_id}",
                        },
                        ["_environment_magic_dim"],
                    ),
                    _when("_environment_magic_dim == true", [_set("_light_magic_dim", True)]),
                ],
            ),
        )
    if "refresh_srd_moonbeam_darkness" not in str(overlap["effects"]):
        overlap["effects"].append(
            _when(
                "lasting_state.has_moonbeam == true",
                [_call("refresh_srd_moonbeam_darkness", {"lasting_state": "{_lasting_state_id}"})],
            )
        )
    return {name: light, "resolve_srd_illumination_overlaps": overlap}


def bind_moonbeam_runtime(text):
    """Bind saved Cylinder light after Moonbeam emission and before final sight cloning."""
    return _replace_tools(text, _moonbeam_light_tools(_load_runtime(text)))
