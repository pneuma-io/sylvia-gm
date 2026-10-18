"""Permanent Hallow sources qualify real bodies, entry, speech and typed defenses."""

import re
from copy import deepcopy

import yaml

if __package__:
    from .srd_control_behaviors import _each, _push
    from .srd_scene_relations import _array
    from .srd_spell_barriers import _effect_lists, _scene, _tool
    from .srd_spell_magic_zones import _when
    from .srd_timed_actor_defenses import _call, _entity, _load_runtime, _parameter, _require, _set
    from .srd_timed_senses import _optional, _replace_tools
else:
    from srd_control_behaviors import _each, _push
    from srd_scene_relations import _array
    from srd_spell_barriers import _effect_lists, _scene, _tool
    from srd_spell_magic_zones import _when
    from srd_timed_actor_defenses import _call, _entity, _load_runtime, _parameter, _require, _set
    from srd_timed_senses import _optional, _replace_tools


PROFILES = {
    "hallow": {
        "family": "hallowed_area",
        "base_level": 5,
        "casting_time": "24_hours",
        "range_kind": "touch",
        "range_feet": 0,
        "concentration": False,
        "persistent_until": "dispelled",
    }
}
PUBLIC_TOOLS = set()
WARD_TYPES = {"aberration", "celestial", "elemental", "fey", "fiend", "undead"}
CREATURE_TYPES = WARD_TYPES | {
    "beast",
    "construct",
    "dragon",
    "giant",
    "humanoid",
    "monstrosity",
    "ooze",
    "plant",
}
EXTRAS = {
    "courage",
    "darkness",
    "daylight",
    "peaceful_rest",
    "extraplanar",
    "fear",
    "resistance",
    "silence",
    "tongues",
    "vulnerability",
}
DAMAGE_TYPES = {
    "acid",
    "bludgeoning",
    "cold",
    "fire",
    "force",
    "lightning",
    "necrotic",
    "piercing",
    "poison",
    "psychic",
    "radiant",
    "slashing",
    "thunder",
}
BENEFIT_EXPORTS = (
    "_hallow_courage",
    "_hallow_frightened",
    "_hallow_resistances",
    "_hallow_vulnerabilities",
    "_hallow_peaceful_rest",
    "_hallow_tongues_source_ids",
)


def validate_profiles(definitions):
    """Keep every admitted fixed Hallow header tied to its pinned spell source."""
    for spell, profile in definitions.items():
        if profile.get("family") == "hallowed_area" and profile != PROFILES.get(spell):
            raise SystemExit(f"Hallow profile differs from its printed source: {spell}")


def _read_source():
    return [
        _require(
            "state.spell_hallow_sources != null and source_id in state.spell_hallow_sources",
            "Use the actual installed Hallow source.",
        ),
        _set("_hallow_source", "state.spell_hallow_sources[source_id]"),
    ]


def _sources(effects, *, include_suppressed=False):
    return [
        _when(
            "state.has_hallow_sources == true",
            [
                _set("_hallow_ids", "state.spell_hallow_source_ids"),
                _each(
                    "_hallow_ids",
                    "_hallow_id",
                    [
                        _set("_hallow_source", "state.spell_hallow_sources[_hallow_id]"),
                        _when(
                            "_hallow_source.installed == true"
                            + (
                                ""
                                if include_suppressed
                                else " and _hallow_source.suppressed != true"
                            ),
                            effects,
                        ),
                    ],
                ),
            ],
        )
    ]


def _point_membership(point, plane, output):
    return [
        _set("_hallow_dx", f"{point}[0] - _hallow_source.origin[0]"),
        _set("_hallow_dy", f"{point}[1] - _hallow_source.origin[1]"),
        _set("_hallow_dz", f"{point}[2] - _hallow_source.origin[2]"),
        _set(
            output,
            f"{plane} == _hallow_source.plane and _hallow_dx * _hallow_dx + "
            "_hallow_dy * _hallow_dy + _hallow_dz * _hallow_dz <= "
            "_hallow_source.radius * _hallow_source.radius",
        ),
    ]


def _body_tools():
    point = _tool(
        "Qualify a private point against the real permanent source Sphere and plane.",
        [_parameter("source_id"), _array("point"), _parameter("plane")],
        [*_read_source(), *_point_membership("point", "plane", "_hallow_point_inside")],
    )
    body = _tool(
        "Qualify the actual admitted body at its current or privately planned position.",
        [
            _parameter("source_id"),
            _parameter("subject"),
            {**_array("position"), "required": False, "default": None},
            _optional("plane", default=None),
        ],
        [
            *_read_source(),
            *_scene("subject", "_hallow_body"),
            _set("_hallow_body_position", "_hallow_body.position"),
            _set("_hallow_body_plane", "_hallow_body.metadata.plane"),
            _when("position != null", [_set("_hallow_body_position", "position")]),
            _when("plane != null", [_set("_hallow_body_plane", "plane")]),
            _set("_hallow_half_x", 0),
            _set("_hallow_half_y", 0),
            _when(
                "_hallow_body.footprint.kind == 'box'",
                [
                    _set("_hallow_half_x", "_hallow_body.footprint.half_extents[0]"),
                    _set("_hallow_half_y", "_hallow_body.footprint.half_extents[1]"),
                ],
                [
                    _set("_hallow_half_x", "_hallow_body.footprint.radius"),
                    _set("_hallow_half_y", "_hallow_body.footprint.radius"),
                ],
            ),
            _set(
                "_hallow_dx",
                "max(0, abs(_hallow_source.origin[0] - _hallow_body_position[0]) - _hallow_half_x)",
            ),
            _set(
                "_hallow_dy",
                "max(0, abs(_hallow_source.origin[1] - _hallow_body_position[1]) - _hallow_half_y)",
            ),
            _set(
                "_hallow_dz",
                "max(0, _hallow_body_position[2] - _hallow_source.origin[2], "
                "_hallow_source.origin[2] - _hallow_body_position[2] - _hallow_body.height)",
            ),
            _when(
                "_hallow_body.footprint.kind == 'circle'",
                [
                    _set("_hallow_dx", "_hallow_source.origin[0] - _hallow_body_position[0]"),
                    _set("_hallow_dy", "_hallow_source.origin[1] - _hallow_body_position[1]"),
                    _set(
                        "_hallow_horizontal_squared",
                        "_hallow_dx * _hallow_dx + _hallow_dy * _hallow_dy",
                    ),
                    _set(
                        "_hallow_dx",
                        "max(0, _hallow_horizontal_squared ** 0.5 - _hallow_body.footprint.radius)",
                    ),
                    _set("_hallow_dy", 0),
                ],
            ),
            _set(
                "_hallow_body_inside",
                "_hallow_body_plane == _hallow_source.plane and "
                "_hallow_dx * _hallow_dx + _hallow_dy * _hallow_dy + _hallow_dz * _hallow_dz <= "
                "_hallow_source.radius * _hallow_source.radius",
            ),
        ],
    )
    return {"qualify_srd_hallow_point": point, "qualify_srd_hallow_body": body}


def _membership(subject, *, position=None, plane=None, output="_hallow_body_inside"):
    params = {"source_id": "{_hallow_id}", "subject": subject}
    if position is not None:
        params["position"] = position
    if plane is not None:
        params["plane"] = plane
    call = _call("qualify_srd_hallow_body", params)
    call["export_context"] = {output: "_hallow_body_inside"}
    return [call]


def _cast_parameters():
    return [
        _entity("caster_state"),
        _parameter("slot_level", "integer"),
        _array("origin"),
        _optional("radius", "number", 60),
        _optional("ward_types", "array", sorted(WARD_TYPES), items={"type": "string"}, max_items=6),
        _optional("extra", default="courage", enum_values=sorted(EXTRAS)),
        _optional(
            "effect_types", "array", sorted(CREATURE_TYPES), items={"type": "string"}, max_items=14
        ),
        _optional("damage_type", default="radiant", enum_values=sorted(DAMAGE_TYPES)),
        _optional("caster_can_touch_origin", "boolean", False),
    ]


def _choice_validation(name, allowed):
    return [
        _set("_hallow_allowed_choices", sorted(allowed)),
        _set("_hallow_unique_choices", []),
        _each(
            name,
            "_hallow_choice",
            [
                _require(
                    "_hallow_choice in _hallow_allowed_choices and "
                    "_hallow_choice not in _hallow_unique_choices",
                    "Choose each actual printed creature type once.",
                ),
                _push("_hallow_unique_choices", "_hallow_choice"),
            ],
        ),
    ]


def _casting_tools():
    prepare = _tool(
        "Derive touch placement and the printed paid overlap failure from real sources.",
        _cast_parameters(),
        [
            _require(
                "slot_level >= 5 and slot_level <= 9 and radius > 0 and radius <= 60",
                "Use the actual Hallow slot and a radius no greater than 60 feet.",
            ),
            *_scene("_caster_state_id", "_hallow_caster_body"),
            _set("_hallow_cast_plane", "_hallow_caster_body.metadata.plane"),
            _call(
                "measure_srd_information_distance",
                {
                    "start": "{_hallow_caster_body.position}",
                    "end": "{origin}",
                },
                ("_scene_distance",),
            ),
            _require(
                "caster_can_touch_origin == true and _scene_distance <= 5",
                "Touch this actual admitted point to cast Hallow.",
            ),
            *_choice_validation("ward_types", WARD_TYPES),
            *_choice_validation("effect_types", CREATURE_TYPES),
            _set("_hallow_extra_choices", sorted(EXTRAS)),
            _set("_hallow_damage_choices", sorted(DAMAGE_TYPES)),
            _require(
                "extra in _hallow_extra_choices and damage_type in _hallow_damage_choices",
                "Choose one printed Hallow extra and a real damage type.",
            ),
            _set("_hallow_cast_failed", False),
            *_sources(
                [
                    _when(
                        "_hallow_source.plane == _hallow_cast_plane",
                        [
                            _set("_hallow_overlap_dx", "origin[0] - _hallow_source.origin[0]"),
                            _set("_hallow_overlap_dy", "origin[1] - _hallow_source.origin[1]"),
                            _set("_hallow_overlap_dz", "origin[2] - _hallow_source.origin[2]"),
                            _set("_hallow_overlap_radius", "radius + _hallow_source.radius"),
                            _when(
                                "_hallow_overlap_dx * _hallow_overlap_dx + _hallow_overlap_dy * "
                                "_hallow_overlap_dy + _hallow_overlap_dz * _hallow_overlap_dz <= "
                                "_hallow_overlap_radius * _hallow_overlap_radius",
                                [_set("_hallow_cast_failed", True)],
                            ),
                        ],
                    ),
                ],
                include_suppressed=True,
            ),
        ],
    )
    forward = {p["name"]: "{" + p["name"] + "}" for p in _cast_parameters()}
    forward["caster_state"] = "{_caster_state_id}"
    install = _tool(
        "Install permanent source facts after the ordinary paid 24-hour cast owner.",
        _cast_parameters() + [_optional("spell_cast_id", default="", model_hidden=True)],
        [
            _call(
                "prepare_srd_hallow_cast", forward, ("_hallow_cast_plane", "_hallow_cast_failed")
            ),
            _set("_new_hallow_source_id", None),
            _when(
                "_hallow_cast_failed != true",
                [
                    _when(
                        "state.spell_hallow_sources == null",
                        [
                            _set("state.spell_hallow_sources", {}),
                            _set("state.spell_hallow_source_ids", []),
                        ],
                    ),
                    _when("state.lasting_serial == null", [_set("state.lasting_serial", 0)]),
                    {"increment": {"field": "state.lasting_serial", "amount": 1}},
                    _call(
                        "format_srd_information_projection",
                        {
                            "message": "{_caster_state_id}:hallow:{state.lasting_serial}",
                        },
                        ("_info_projection_message",),
                    ),
                    _set("_new_hallow_source_id", "_info_projection_message"),
                    _set(
                        "state.spell_hallow_sources.{_new_hallow_source_id}",
                        {
                            "installed": True,
                            "spell_id": "'hallow'",
                            "spell_level": "slot_level",
                            "caster_state_id": "_caster_state_id",
                            "source_caster_id": "_caster_state_id",
                            "cast_id": "spell_cast_id",
                            "dispel_ongoing": True,
                            "ongoing_duration": False,
                            "started_tick": "state.lasting_tick",
                            "serial": "state.lasting_serial",
                            "permanent": True,
                            "suppressed": False,
                            "origin": "origin",
                            "radius": "radius",
                            "plane": "_hallow_cast_plane",
                            "ward_types": "ward_types",
                            "extra": "extra",
                            "effect_types": "effect_types",
                            "damage_type": "damage_type",
                        },
                    ),
                    _set("_hallow_installed_ids", "state.spell_hallow_source_ids"),
                    _push("_hallow_installed_ids", "_new_hallow_source_id"),
                    _set("state.spell_hallow_source_ids", "_hallow_installed_ids"),
                    _set("state.has_hallow_sources", True),
                ],
            ),
            _set(
                "_spell_summary_rows",
                [
                    {"label": "Spell", "value": "'hallow'"},
                    {"label": "Overlap failure", "value": "_hallow_cast_failed"},
                ],
            ),
            _set(
                "_spell_detail_rows",
                [
                    {"label": "Source", "value": "_new_hallow_source_id"},
                    {"label": "Extra effect", "value": "extra"},
                ],
            ),
        ],
    )
    end = _tool(
        "Retire one actual Hallow source without erasing other wards or concentration.",
        [_parameter("source_id"), _optional("reason", default="removed")],
        [
            *_read_source(),
            _set("state.spell_hallow_sources.{source_id}.installed", False),
            _set("state.spell_hallow_sources.{source_id}.ended_tick", "state.lasting_tick"),
            _set("state.spell_hallow_sources.{source_id}.end_reason", "reason"),
        ],
    )
    return {
        "prepare_srd_hallow_cast": prepare,
        "cast_srd_hallow": install,
        "end_srd_hallow_source": end,
    }


def _benefit_tools():
    benefits = _tool(
        "Read chosen Hallow benefits only for the actual body/type inside its source.",
        [_entity("target_state")],
        [
            _set("_hallow_courage", False),
            _set("_hallow_frightened", False),
            _set("_hallow_resistances", []),
            _set("_hallow_vulnerabilities", []),
            _set("_hallow_peaceful_rest", False),
            _set("_hallow_tongues_source_ids", []),
            *_sources(
                [
                    *_membership("{_target_state_id}"),
                    _when(
                        "_hallow_body_inside == true",
                        [
                            _when(
                                "_hallow_source.extra == 'peaceful_rest' and "
                                "target_state.is_alive == false "
                                "and target_state.body_interred == true",
                                [_set("_hallow_peaceful_rest", True)],
                            ),
                            _when(
                                "target_state.creature_type in _hallow_source.effect_types",
                                [
                                    _when(
                                        "_hallow_source.extra == 'courage'",
                                        [_set("_hallow_courage", True)],
                                    ),
                                    _when(
                                        "_hallow_source.extra == 'fear'",
                                        [_set("_hallow_frightened", True)],
                                    ),
                                    _when(
                                        "_hallow_source.extra == 'resistance'",
                                        [
                                            _push(
                                                "_hallow_resistances", "_hallow_source.damage_type"
                                            )
                                        ],
                                    ),
                                    _when(
                                        "_hallow_source.extra == 'vulnerability'",
                                        [
                                            _push(
                                                "_hallow_vulnerabilities",
                                                "_hallow_source.damage_type",
                                            )
                                        ],
                                    ),
                                    _when(
                                        "_hallow_source.extra == 'tongues'",
                                        [_push("_hallow_tongues_source_ids", "_hallow_id")],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ]
            ),
        ],
    )
    condition = _tool(
        "Derive causing-creature-qualified ward suppression and Courage immunity.",
        [
            _entity("target_state"),
            _parameter("condition"),
            _optional("source_state_id", default=None),
        ],
        [
            _set("_hallow_condition_suppressed", False),
            _set("_hallow_condition_immune", False),
            _set("_hallow_condition_needs_source", False),
            _call(
                "resolve_srd_hallow_benefits",
                {"target_state": "{_target_state_id}"},
                ("_hallow_courage",),
            ),
            _when(
                "condition == 'frightened' and _hallow_courage == true",
                [_set("_hallow_condition_immune", True)],
            ),
            _when(
                "condition == 'charmed' or condition == 'frightened' or condition == 'possession'",
                [
                    *_sources(
                        [
                            *_membership("{_target_state_id}"),
                            _when(
                                "_hallow_body_inside == true and "
                                "len(_hallow_source.ward_types) > 0",
                                [
                                    _when(
                                        "source_state_id == null",
                                        [_set("_hallow_condition_needs_source", True)],
                                        [
                                            _call(
                                                "read_srd_secure_area_creature_type",
                                                {"actor_state": "{source_state_id}"},
                                                ("_secure_creature_type",),
                                            ),
                                            _when(
                                                "_secure_creature_type in "
                                                "_hallow_source.ward_types",
                                                [_set("_hallow_condition_suppressed", True)],
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ]
                    ),
                ],
            ),
        ],
    )
    movement = _tool(
        "Qualify actual voluntary entry and selected magical/planar boundaries.",
        [
            _parameter("subject"),
            _array("destination"),
            _parameter("plane"),
            _optional("teleport", "boolean", False),
            _optional("voluntary", "boolean", True),
        ],
        [
            *_scene("subject", "_hallow_mover"),
            _set("_hallow_movement_allowed", True),
            _set("_hallow_movement_blocking_ids", []),
            *_sources(
                [
                    *_membership("{subject}", output="_hallow_origin_inside"),
                    *_membership(
                        "{subject}",
                        position="{destination}",
                        plane="{plane}",
                        output="_hallow_destination_inside",
                    ),
                    _when(
                        "_hallow_mover.metadata.participant_ref != null",
                        [
                            _call(
                                "read_srd_secure_area_creature_type",
                                {
                                    "actor_state": "{_hallow_mover.metadata.participant_ref}",
                                },
                                ("_secure_creature_type",),
                            ),
                            _set(
                                "_hallow_entry_blocked",
                                "voluntary == true and _hallow_origin_inside != true "
                                "and _hallow_destination_inside == true and "
                                "_secure_creature_type in "
                                "_hallow_source.ward_types",
                            ),
                            _set(
                                "_hallow_planar_blocked",
                                "_hallow_source.extra == 'extraplanar' and "
                                "_secure_creature_type in _hallow_source.effect_types and "
                                "(_hallow_origin_inside != _hallow_destination_inside) and "
                                "(teleport == true or plane != _hallow_mover.metadata.plane)",
                            ),
                            _when(
                                "_hallow_entry_blocked == true or _hallow_planar_blocked == true",
                                [
                                    _set("_hallow_movement_allowed", False),
                                    _push("_hallow_movement_blocking_ids", "_hallow_id"),
                                ],
                            ),
                        ],
                    ),
                ]
            ),
        ],
    )
    return {
        "resolve_srd_hallow_benefits": benefits,
        "check_srd_hallow_condition": condition,
        "check_srd_hallow_movement": movement,
    }


def _communication_tools():
    sound = _tool(
        "Silence prevents sound leaving or reaching an actual Hallow body.",
        [_array("start"), _array("end"), _parameter("plane")],
        [
            _set("_hallow_sound_allowed", True),
            *_sources(
                [
                    _when(
                        "_hallow_source.extra == 'silence'",
                        [
                            *_point_membership("start", "plane", "_hallow_start_inside"),
                            *_point_membership("end", "plane", "_hallow_end_inside"),
                            _when(
                                "_hallow_start_inside == true or _hallow_end_inside == true",
                                [_set("_hallow_sound_allowed", False)],
                            ),
                        ],
                    )
                ]
            ),
        ],
    )
    language = _tool(
        "A chosen creature can communicate with any creature in the same Hallow.",
        [_entity("speaker_state"), _entity("listener_state")],
        [
            _set("_hallow_language_understands", False),
            _set("_hallow_language_can_reply", False),
            *_sources(
                [
                    _when(
                        "_hallow_source.extra == 'tongues'",
                        [
                            *_membership("{_speaker_state_id}", output="_hallow_speaker_inside"),
                            *_membership("{_listener_state_id}", output="_hallow_listener_inside"),
                            _when(
                                "_hallow_speaker_inside == true and "
                                "_hallow_listener_inside == true and "
                                "(speaker_state.creature_type in _hallow_source.effect_types or "
                                "listener_state.creature_type in _hallow_source.effect_types)",
                                [
                                    _set("_hallow_language_understands", True),
                                    _set("_hallow_language_can_reply", True),
                                ],
                            ),
                        ],
                    )
                ]
            ),
        ],
    )
    return {"check_srd_hallow_sound": sound, "resolve_srd_hallow_language": language}


def _light_tool():
    return _tool(
        "Read actual Hallow point/plane light and spell level for final source precedence.",
        [_array("point"), _parameter("plane")],
        [
            _set("_hallow_light", None),
            _set("_hallow_light_spell_level", -1),
            _set("_hallow_light_source_id", None),
            *_sources(
                [
                    *_point_membership("point", "plane", "_hallow_light_inside"),
                    _when(
                        "_hallow_light_inside == true and (_hallow_source.extra == 'darkness' or "
                        "_hallow_source.extra == 'daylight')",
                        [
                            _set("_hallow_light", "'bright'"),
                            _when(
                                "_hallow_source.extra == 'darkness'",
                                [
                                    _set("_hallow_light", "'magical_darkness'"),
                                ],
                            ),
                            _set("_hallow_light_spell_level", "_hallow_source.spell_level"),
                            _set("_hallow_light_source_id", "_hallow_id"),
                        ],
                    ),
                ]
            ),
        ],
    )


def _remove_callback(tool, name):
    tool["effects"] = [e for e in tool["effects"] if name not in str(e)]


def _compose_consumers(tools, changed):
    """Use cheap inactive guards at actual typed defense, speech and voluntary movement owners."""
    immunity = deepcopy(tools["check_srd_target_condition_immunity"])
    _remove_callback(immunity, "check_srd_hallow_condition")
    immunity["effects"].append(
        _when(
            "lasting_state.has_hallow_sources == true and condition == 'frightened'",
            [
                _call(
                    "check_srd_hallow_condition",
                    {
                        "target_state": "{_target_state_id}",
                        "condition": "{condition}",
                        "source_state_id": "{source_state_id}",
                    },
                    ("_hallow_condition_immune",),
                ),
                _when("_hallow_condition_immune == true", [_set("_source_condition_immune", True)]),
            ],
        )
    )
    changed["check_srd_target_condition_immunity"] = immunity
    language = deepcopy(tools["resolve_srd_spell_language"])
    _remove_callback(language, "resolve_srd_hallow_language")
    index = next(
        i
        for i, e in enumerate(language["effects"])
        if e.get("set", {}).get("field") == "_language_understands"
        and e["set"]["value"] == "_language_understands == true and _language_perceived == true"
    )
    # The final perception projection still masks both results when actual speech is blocked.
    language["effects"].insert(
        index,
        _when(
            "lasting_state.has_hallow_sources == true",
            [
                _call(
                    "resolve_srd_hallow_language",
                    {
                        "speaker_state": "{_speaker_state_id}",
                        "listener_state": "{_listener_state_id}",
                    },
                    ("_hallow_language_understands", "_hallow_language_can_reply"),
                ),
                _when(
                    "_hallow_language_understands == true",
                    [_set("_language_understands", True), _set("_language_can_reply", True)],
                ),
                _when(
                    "channel == 'speech'",
                    [
                        *_scene("_speaker_state_id", "_hallow_speaker"),
                        *_scene("_listener_state_id", "_hallow_listener"),
                        _call(
                            "check_srd_hallow_sound",
                            {
                                "start": "{_hallow_speaker.position}",
                                "end": "{_hallow_listener.position}",
                                "plane": "{_hallow_speaker.metadata.plane}",
                            },
                            ("_hallow_sound_allowed",),
                        ),
                        _when(
                            "_hallow_sound_allowed != true", [_set("_language_perceived", False)]
                        ),
                    ],
                ),
            ],
        ),
    )
    changed["resolve_srd_spell_language"] = language
    _compose_damage_consumers(tools, changed)


def _compose_damage_consumers(tools, changed):
    """Join existing resistance groups, preserving zero guards and every real typed component."""
    marker = "lasting_state.has_hallow_sources == true"
    suffix = " or " + marker
    for name, original in tools.items():
        if not any(p["name"] == "target" for p in original.get("parameters", [])):
            continue
        if " in target.damage_resistances" not in str(original["effects"]):
            continue
        tool = deepcopy(original)
        # Remove only this owner's nested lookup; keep Ward/Concentration consumers.
        for effects in _effect_lists(tool["effects"]):
            for effect in effects:
                children = effect.get("on_true", [])
                if any(
                    c.get("condition") == marker and "resolve_srd_hallow_benefits" in str(c)
                    for c in children
                ):
                    effect["on_true"] = [
                        c
                        for c in children
                        if not (
                            c.get("condition") == marker and "resolve_srd_hallow_benefits" in str(c)
                        )
                    ]
                    clause = effect.get("condition", "")
                    if clause.startswith("(") and clause.endswith(suffix):
                        effect["condition"] = clause[1 : -len(suffix) - 1]
            effects[:] = [
                e
                for e in effects
                if not (e.get("condition") == marker and "resolve_srd_hallow_benefits" in str(e))
            ]
        for effects in list(_effect_lists(tool["effects"])):
            for effect in list(effects):
                index = effects.index(effect)
                clause = effect.get("condition", "")
                match = re.search(
                    r"([A-Za-z_][A-Za-z_0-9]*|'[a-z]+') in "
                    r"target.damage_resistances",
                    clause,
                )
                if match:
                    kind = match[1]
                    result = re.search(
                        r"([A-Za-z_][A-Za-z_0-9]*lasting_damage_resistance) "
                        r"== true",
                        clause,
                    )
                    if result is None:
                        raise ValueError("Hallow requires the genuine lasting-resistance owner.")
                    field = result[1]
                    lookup = _when(
                        marker,
                        [
                            _call(
                                "resolve_srd_hallow_benefits",
                                {"target_state": "{target}"},
                                ("_hallow_resistances", "_hallow_vulnerabilities"),
                            ),
                            _when(f"{kind} in _hallow_resistances", [_set(field, True)]),
                        ],
                    )
                    previous = next(
                        (
                            e
                            for e in reversed(effects[:index])
                            if "on_true" in e and field in str(e)
                        ),
                        None,
                    )
                    if previous is not None:
                        previous["condition"] = "(" + previous["condition"] + ")" + suffix
                        previous["on_true"].append(lookup)
                    else:
                        effects.insert(effects.index(effect), lookup)
                elif " in target.damage_vulnerabilities" in clause:
                    match = re.search(
                        r"([A-Za-z_][A-Za-z_0-9]*|'[a-z]+') in "
                        r"target.damage_vulnerabilities",
                        clause,
                    )
                    if match:
                        kind = match[1]
                        term = f"({marker} and {kind} in _hallow_vulnerabilities)"
                        if term not in clause:
                            token = f"{kind} in target.damage_vulnerabilities"
                            effect["condition"] = clause.replace(
                                token, "(" + token + " or " + term + ")", 1
                            )
        changed[name] = tool


def extend_runtime(text):
    """Publish source leaves before extended-casting capture and shared source/removal adoption."""
    tools = _load_runtime(text)["tools"]
    changed = {
        **_body_tools(),
        **_casting_tools(),
        **_benefit_tools(),
        **_communication_tools(),
        "check_srd_hallow_light": _light_tool(),
    }
    cast = deepcopy(tools["cast_spell"])
    for parameter in [
        _array("hallow_origin", default=[0, 0, 0]),
        _optional("hallow_radius", "number", 60),
        _optional(
            "hallow_ward_types", "array", sorted(WARD_TYPES), items={"type": "string"}, max_items=6
        ),
        _optional("hallow_extra", default="courage", enum_values=sorted(EXTRAS)),
        _optional(
            "hallow_effect_types",
            "array",
            sorted(CREATURE_TYPES),
            items={"type": "string"},
            max_items=14,
        ),
    ]:
        if not any(p["name"] == parameter["name"] for p in cast["parameters"]):
            cast["parameters"].append(parameter)
    for effects in _effect_lists(cast["effects"]):
        effects[:] = [
            e
            for e in effects
            if "_spell_definition.family == 'hallowed_area'" not in e.get("condition", "")
        ]
    index = max(
        i
        for i, e in enumerate(cast["effects"])
        if "_spell_definition.family ==" in e.get("condition", "")
    )
    cast["effects"].insert(
        index + 1,
        _when(
            "_spell_definition.family == 'hallowed_area'",
            [
                _call(
                    "cast_srd_hallow",
                    {
                        "caster_state": "{_caster_state_id}",
                        "slot_level": "{_slot_level}",
                        "origin": "{hallow_origin}",
                        "radius": "{hallow_radius}",
                        "ward_types": "{hallow_ward_types}",
                        "extra": "{hallow_extra}",
                        "effect_types": "{hallow_effect_types}",
                        "damage_type": "{selected_damage_type}",
                        "caster_can_touch_origin": "{caster_can_touch_target}",
                        "spell_cast_id": "{_spell_source_cast_id}",
                    },
                    ("_spell_summary_rows", "_spell_detail_rows"),
                ),
            ],
        ),
    )
    changed["cast_spell"] = cast
    text = _replace_tools(text, changed)
    text = _replace_tools(text, {k: v for k, v in changed.items() if k not in tools})
    props = {
        k: {"type": "boolean" if type(v) is bool else "integer" if type(v) is int else "string"}
        for k, v in PROFILES["hallow"].items()
        if k != "family"
    }
    schema = yaml.safe_dump(
        {
            "hallowed_area": {
                "type": "object",
                "properties": props,
                "required": list(props),
                "additionalProperties": False,
            }
        },
        sort_keys=False,
    )
    schema = "".join("      " + line for line in schema.splitlines(True))
    if "      hallowed_area:" in text:
        text = re.sub(
            r"(?ms)^      hallowed_area:\n.*?(?=^      [a-z])",
            lambda _: schema + "\n",
            text,
            count=1,
        )
    else:
        text = text.replace("      spell_secure_area:", schema + "\n      spell_secure_area:", 1)
    return text


def bind_runtime_consumers(text):
    """Compose final actual packet/language owners and the real ordinary movement leaf."""
    tools = _load_runtime(text)["tools"]
    changed = {}
    _compose_consumers(tools, changed)
    name = "move_srd_scene_creature_environment_segment"
    if name in tools:
        movement = deepcopy(tools[name])
        _remove_callback(movement, "check_srd_hallow_movement")
        movement["effects"].insert(
            0,
            _when(
                "lasting_state.has_hallow_sources == true",
                [
                    *_scene("_actor_state_id", "_hallow_moving_body"),
                    _call(
                        "check_srd_hallow_movement",
                        {
                            "subject": "{_actor_state_id}",
                            "destination": "{destination}",
                            "plane": "{_hallow_moving_body.metadata.plane}",
                        },
                        ("_hallow_movement_allowed",),
                    ),
                    _require(
                        "_hallow_movement_allowed == true", "Hallow prevents this voluntary entry."
                    ),
                ],
            ),
        )
        changed[name] = movement
    if "create_srd_animated_zombie_body" in tools:
        corpse = deepcopy(tools["create_srd_animated_zombie_body"])
        _remove_callback(corpse, "resolve_srd_hallow_benefits")
        corpse["effects"].insert(
            0,
            _when(
                "lasting_state.has_hallow_sources == true",
                [
                    _call(
                        "resolve_srd_hallow_benefits",
                        {"target_state": "{_corpse_state_id}"},
                        ("_hallow_peaceful_rest",),
                    ),
                    _require(
                        "_hallow_peaceful_rest != true",
                        "This actually interred body is protected by Hallow's Peaceful Rest.",
                    ),
                ],
            ),
        )
        changed["create_srd_animated_zombie_body"] = corpse
    return _replace_tools(text, changed)
