"""Earthquake removal selects its actual finite cast and preserves earned Prone."""

from copy import deepcopy

if __package__:
    from .srd_control_behaviors import _each, _push, _walk, _when
    from .srd_earthquake import _source
    from .srd_scene_relations import _array
    from .srd_spell_removal_adoption import _actual_path, _source_check
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _tool,
    )
    from .srd_timed_senses import _optional, _replace_tools
else:
    from srd_control_behaviors import _each, _push, _walk, _when
    from srd_earthquake import _source
    from srd_scene_relations import _array
    from srd_spell_removal_adoption import _actual_path, _source_check
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _tool,
    )
    from srd_timed_senses import _optional, _replace_tools

SOURCE_FAMILY = "earthquake_spell"
SOURCE_INSTALLER = "cast_srd_earthquake"


def _current():
    return (
        "_quake_source.active == true and state.lasting_tick < _quake_source.expires_tick "
        "and _quake_source.cast_id != null and _quake_source.cast_id != '' "
        "and _quake_source.source_caster_id == _quake_source.caster "
        "and _quake_source.ongoing_duration == true and _quake_source.dispel_ongoing == true "
        "and _quake_source.spell_level >= 8 and _quake_source.spell_level <= 9"
    )


def _read():
    return [
        *_source(),
        _require(_current(), "Select an active Earthquake with its actual paid cast identity."),
        _call(
            "qualify_srd_earthquake_owned_focus",
            {"creating_caster": "{_quake_source.caster}", "area_id": "{area_id}"},
            ("_quake_focus_current",),
        ),
        _require("_quake_focus_current == true", "Earthquake's exact concentration has ended."),
    ]


def _end():
    return [
        _call(
            "end_srd_concentration_node",
            {"source_state": "{_quake_source.caster}", "target_state": "{_quake_source.caster}"},
        ),
    ]


def _tools():
    focus = _tool(
        "Qualify this saved tremor against its creator's exact current concentration sequence.",
        [_entity("creating_caster"), _parameter("area_id")],
        [
            *_source(),
            _set(
                "_quake_focus_current",
                "_quake_source.caster == _creating_caster_id and "
                "creating_caster.concentration_spell_id == 'earthquake' and "
                "creating_caster.concentration_effect_family == 'earthquake_spell' and "
                "creating_caster.earthquake_area_id == area_id and "
                "creating_caster.concentration_sequence == _quake_source.sequence and "
                "creating_caster.concentration_cast_id == _quake_source.cast_id",
            ),
        ],
    )
    read = _tool(
        "Read one actual current Earthquake source without grouping legacy casts.",
        [_parameter("area_id")],
        _read(),
    )
    collect = _tool(
        "Collect this creator's exact finite tremor cast from the existing source index.",
        [_entity("caster_state"), _parameter("cast_id")],
        [
            _set("_earthquake_dismiss_ids", []),
            _when(
                "state.has_earthquakes == true and cast_id != ''",
                [
                    _set("_earthquake_owned_ids", "state.earthquake_area_ids"),
                    _each(
                        "_earthquake_owned_ids",
                        "_earthquake_owned_id",
                        [
                            _set(
                                "_quake_source",
                                "state.persistent_spell_areas[_earthquake_owned_id]",
                            ),
                            _when(
                                _current() + " and _quake_source.cast_id == cast_id and "
                                "_quake_source.source_caster_id == _caster_state_id",
                                [
                                    _call(
                                        "read_srd_earthquake_owned_source",
                                        {"area_id": "{_earthquake_owned_id}"},
                                    ),
                                    _push("_earthquake_dismiss_ids", "_earthquake_owned_id"),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    retire = _tool(
        "End the selected exact owned tremor through its real concentration cleanup.",
        [_entity("caster_state"), _parameter("cast_id"), _parameter("area_id")],
        [
            *_read(),
            _require(
                "_quake_source.cast_id == cast_id and "
                "_quake_source.source_caster_id == _caster_state_id",
                "Select this caster's exact Earthquake cast.",
            ),
            *_end(),
        ],
    )
    dispel = _tool(
        "Dispel the selected actual ground tremor at its saved paid level, with a real check.",
        [
            _entity("caster_state"),
            _optional("caster", default=None, resolve="participant"),
            _parameter("source_id"),
            _array("point"),
            _parameter("slot_level", "integer"),
            _parameter("casting_modifier", "integer"),
            _parameter("casting_ability"),
        ],
        [
            _call(
                "read_srd_earthquake_owned_source",
                {"area_id": "{source_id}"},
                ("_quake_source",),
            ),
            _require(
                "abs(point[2] - _quake_source.origin[2]) < 0.000000001 and "
                "(point[0] - _quake_source.origin[0]) ** 2 + "
                "(point[1] - _quake_source.origin[1]) ** 2 <= 10000",
                "Choose an actual point within this tremor's saved ground disk.",
            ),
            *_actual_path("point", "_quake_source.plane"),
            *_source_check("_quake_source"),
            _when("_dispel_succeeded == true", _end()),
        ],
    )
    return {
        "qualify_srd_earthquake_owned_focus": focus,
        "read_srd_earthquake_owned_source": read,
        "collect_srd_earthquake_owned_cast": collect,
        "retire_srd_earthquake_owned_cast": retire,
        "dispel_srd_earthquake_owned_source": dispel,
    }


def _bind_dismissal(tool):
    """Collect before admission and retire before generic concentration shutdown."""
    marker = "lasting_state.has_earthquakes == true"
    tool["effects"] = [
        e
        for e in tool["effects"]
        if e.get("condition") != marker
        and e.get("set", {}).get("field") != "_earthquake_dismiss_ids"
        and e.get("for_each", {}).get("collection") != "_earthquake_dismiss_ids"
    ]
    admission = [
        i
        for i, e in enumerate(tool["effects"])
        if e.get("require", {}).get("condition")
        == "_dismiss_count > 0 or _dismiss_concentration_owned == true"
    ]
    shutdown = [
        e
        for e in tool["effects"]
        if e.get("condition", "").startswith("_dismiss_concentration_owned == true")
    ]
    if len(admission) != 1 or len(shutdown) != 1:
        raise ValueError("Earthquake requires the actual shared dismissal admission and shutdown.")
    tool["effects"][admission[0] : admission[0]] = [
        _set("_earthquake_dismiss_ids", []),
        _when(
            marker,
            [
                _call(
                    "collect_srd_earthquake_owned_cast",
                    {"caster_state": "{_caster_state_id}", "cast_id": "{cast_id}"},
                    ("_earthquake_dismiss_ids",),
                ),
                _set("_dismiss_count", "_dismiss_count + len(_earthquake_dismiss_ids)"),
            ],
        ),
    ]
    index = tool["effects"].index(shutdown[0])
    tool["effects"].insert(
        index,
        _each(
            "_earthquake_dismiss_ids",
            "_earthquake_dismiss_id",
            [
                _call(
                    "retire_srd_earthquake_owned_cast",
                    {
                        "caster_state": "{_caster_state_id}",
                        "cast_id": "{cast_id}",
                        "area_id": "{_earthquake_dismiss_id}",
                    },
                ),
            ],
        ),
    )
    # The exact source cleanup may already have ended concentration.
    shutdown[0]["condition"] = (
        "_dismiss_concentration_owned == true and caster_state.concentration_spell_id != null "
        "and caster_state.concentration_cast_id == cast_id"
    )


def _bind_recovery(tool):
    """Join only this area choice to the genuine selected-source Dispel owner."""
    fields = {"_recovery_source_intents", "_recovery_source_owners"}
    found = set()
    for e in _walk(tool["effects"]):
        if e.get("set", {}).get("field") in fields:
            found.add(e["set"]["field"])
            if repr("earthquake") not in e["set"]["value"]:
                e["set"]["value"].append(repr("earthquake"))
    if found != fields:
        raise ValueError("Earthquake requires the actual selected-source Recovery owner.")
    marker = "spell == 'dispel_magic' and recovery_choice == 'earthquake'"
    tool["effects"] = [e for e in tool["effects"] if e.get("condition") != marker]
    tool["effects"].append(
        _when(
            marker,
            [
                _call(
                    "dispel_srd_earthquake_owned_source",
                    {
                        "caster_state": "{_caster_state_id}",
                        "caster": "{_caster_id}",
                        "source_id": "{recovery_source_id}",
                        "point": "{recovery_position}",
                        "slot_level": "{slot_level}",
                        "casting_modifier": "{casting_modifier}",
                        "casting_ability": "{casting_ability}",
                    },
                    ("_adoption_dispel_check",),
                ),
                _set("_dispel_checks", ["_adoption_dispel_check"]),
                _set("_earthquake_dispel_detail", "'Automatic at this slot level'"),
                _when(
                    "_adoption_dispel_check.roll != null",
                    [
                        _call(
                            "format_srd_information_projection",
                            {
                                "message": "d20 {_adoption_dispel_check.roll.results}; "
                                "modifier {_adoption_dispel_check.roll.modifier}; "
                                "DC {_adoption_dispel_check.dc}; "
                                "effect ended: {_adoption_dispel_check.ended}"
                            },
                            ("_info_projection_message",),
                        ),
                        _set("_earthquake_dispel_detail", "_info_projection_message"),
                    ],
                ),
                _set(
                    "_spell_summary_rows",
                    [{"label": "'Selected tremor ended'", "value": "_adoption_dispel_check.ended"}],
                ),
                _set(
                    "_spell_detail_rows",
                    [
                        {"label": "'Earthquake source'", "value": "recovery_source_id"},
                        {"label": "'Actual Dispel check'", "value": "_earthquake_dispel_detail"},
                    ],
                ),
            ],
        ),
    )


def bind_runtime(text):
    """Compose after family installation and shared Source/removal, before reaction capture."""
    tools = _load_runtime(text)["tools"]
    if SOURCE_INSTALLER not in tools:
        raise ValueError("Earthquake removal requires its genuine installed family.")
    changed = _tools()
    cast = deepcopy(tools["cast_spell"])
    intents = [
        e["set"]["value"]
        for e in _walk(cast["effects"])
        if e.get("set", {}).get("field") == "_recovery_source_intents"
    ]
    if len(intents) != 1:
        raise ValueError("Earthquake requires the actual targetless Recovery cast dispatcher.")
    if repr("earthquake") not in intents[0]:
        intents[0].append(repr("earthquake"))
    changed["cast_spell"] = cast
    for name, binder in (
        ("dismiss_srd_spell", _bind_dismissal),
        ("cast_srd_recovery", _bind_recovery),
    ):
        changed[name] = deepcopy(tools[name])
        binder(changed[name])
    return _replace_tools(text, changed)
