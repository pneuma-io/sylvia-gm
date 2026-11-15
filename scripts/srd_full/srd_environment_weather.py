"""Control Weather changes admitted weather stages after its real source-owned delay."""

from copy import deepcopy

if __package__:
    from .srd_scene_relations import POSITION_EXPORTS, _array
    from .srd_spell_environment import (
        _call,
        _each,
        _effect_lists,
        _entity,
        _environment_tool,
        _optional,
        _param,
        _public,
        _push,
        _require,
        _set,
        _when,
        _wind_fog_mode,
    )
else:
    from srd_scene_relations import POSITION_EXPORTS, _array
    from srd_spell_environment import (
        _call,
        _each,
        _effect_lists,
        _entity,
        _environment_tool,
        _optional,
        _param,
        _public,
        _push,
        _require,
        _set,
        _when,
        _wind_fog_mode,
    )

SOURCE_SHA = "dcc0f13f68071229e96200384441058b9c07b0858f2230a4d335030f6f7bede1"
PROFILE = {
    "family": "spell_environment",
    "casting_time": "10_minutes",
    "base_level": 8,
    "range_kind": "self",
    "range_feet": 0,
    "radius_feet": 26400,
    "radius_per_slot_level": 0,
    "duration_rounds": 4800,
    "concentration": True,
    "environment_mode": "control_weather",
}
DIRECTIONS = ["north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest"]
STAGES = {"precipitation": 5, "temperature": 6, "wind": 5}
WEATHER_EXPORTS = ("_weather_known", "_weather_conditions", "_weather_phase", "_weather_source_id")


def _facts(field):
    return [
        _set("_weather_fact", field),
        _require("_weather_fact != null", "Admit the actual current weather stages and direction."),
        *[
            _require(
                f"_weather_fact.{key}_stage != null and _weather_fact.{key}_stage >= 1 and "
                f"_weather_fact.{key}_stage <= {maximum} and "
                f"floor(_weather_fact.{key}_stage) == _weather_fact.{key}_stage",
                "Use the actual printed weather stage table.",
            )
            for key, maximum in STAGES.items()
        ],
        _require(
            " or ".join(f"_weather_fact.direction == {direction!r}" for direction in DIRECTIONS),
            "Admit the actual supported cardinal or diagonal wind direction.",
        ),
    ]


def _setting_tool():
    """Explicit current-room facts qualify setting; missing roofs never establish outdoors."""
    return _environment_tool(
        "Read actual same-plane containing setting zones, otherwise the admitted entity fact.",
        [_array("point"), _param("plane"), _optional("fallback", default="")],
        [
            _set("_weather_setting", "fallback"),
            _set("_weather_setting_zone", False),
            _set("_weather_setting_zones", "state.spatial_scene.zones"),
            _each(
                "_weather_setting_zones",
                "_weather_setting_entry",
                [
                    _when(
                        "_weather_setting_entry.metadata.plane == plane and "
                        "(_weather_setting_entry.metadata.setting == 'indoors' or "
                        "_weather_setting_entry.metadata.setting == 'outdoors')",
                        [
                            _call(
                                "srd_information_segment_interval",
                                {
                                    "start": "{point}",
                                    "end": "{point}",
                                    "volume": "{_weather_setting_entry.shape}",
                                },
                                ("_segment_hit",),
                            ),
                            _when(
                                "_segment_hit == true",
                                [
                                    _require(
                                        "_weather_setting_zone != true or _weather_setting == "
                                        "_weather_setting_entry.metadata.setting",
                                        "Admit unambiguous current indoor/outdoor scene facts.",
                                    ),
                                    _set(
                                        "_weather_setting",
                                        "_weather_setting_entry.metadata.setting",
                                    ),
                                    _set("_weather_setting_zone", True),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _outdoors():
    return [
        _call("srd_information_scene_entity", {"subject": "{_caster_state_id}"}, POSITION_EXPORTS),
        _set("_weather_cast_scene", "_info_scene_entity"),
        _set("_weather_cast_origin", "_info_position"),
        _require("_weather_cast_scene.metadata.plane != null", "Admit this caster's actual plane."),
        _call(
            "resolve_srd_weather_setting",
            {
                "point": "{_weather_cast_origin}",
                "plane": "{_weather_cast_scene.metadata.plane}",
                "fallback": "{_weather_cast_scene.metadata.setting}",
            },
            ("_weather_setting",),
        ),
        _require("_weather_setting == 'outdoors'", "Control Weather must be cast outdoors."),
    ]


def _source_tool():
    return _environment_tool(
        "Authenticate this paid Control Weather and its actual current outdoor caster.",
        [_entity("caster_state"), _param("area_id")],
        [
            _set("_weather_source", "state.persistent_spell_areas[area_id]"),
            _set(
                "_weather_live",
                "_weather_source != null and "
                "_weather_source.profile == 'environment' and "
                "_weather_source.spell == 'control_weather' and "
                "_weather_source.mode == 'control_weather' and "
                f"_weather_source.source_sha256 == {SOURCE_SHA!r}",
            ),
            _set(
                "_weather_live",
                "_weather_live == true and _weather_source.active == true and "
                "state.lasting_tick < _weather_source.expires_tick and "
                "caster_state.is_alive == true and caster_state.hp > 0 and "
                "_weather_source.caster == _caster_state_id and "
                "caster_state.environment_area_id == area_id and "
                "caster_state.concentration_spell_id == 'control_weather' and "
                "caster_state.concentration_effect_family == 'spell_environment'",
            ),
            _when(
                "_weather_live == true",
                [
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_caster_state_id}"},
                        POSITION_EXPORTS,
                    ),
                    _set("_weather_caster_scene", "_info_scene_entity"),
                    _set("_weather_caster_origin", "_info_position"),
                    _require(
                        "_weather_caster_scene.metadata.plane == _weather_source.plane",
                        "Control Weather's cross-plane movement needs its actual source consumer.",
                    ),
                    _call(
                        "resolve_srd_weather_setting",
                        {
                            "point": "{_weather_caster_origin}",
                            "plane": "{_weather_source.plane}",
                            "fallback": "{_weather_caster_scene.metadata.setting}",
                        },
                        ("_weather_setting",),
                    ),
                    _require(
                        "_weather_setting == 'indoors' or _weather_setting == 'outdoors'",
                        "Admit this caster's actual current indoor/outdoor setting.",
                    ),
                    _set("state.persistent_spell_areas.{area_id}.origin", "_weather_caster_origin"),
                    _when(
                        "_weather_setting == 'indoors'",
                        [
                            _call(
                                "end_srd_concentration_node",
                                {
                                    "source_state": "{_caster_state_id}",
                                    "target_state": "{_caster_state_id}",
                                    "lasting_state": "state",
                                },
                            ),
                            _set("_weather_live", False),
                        ],
                    ),
                ],
            ),
        ],
    )


def _settle_tool():
    return _environment_tool(
        "Commit chosen weather stages only at their actual rolled transition deadline.",
        [_param("area_id")],
        [
            _set("_weather_settle_source", "state.persistent_spell_areas[area_id]"),
            _call(
                "qualify_srd_control_weather_source",
                {
                    "caster_state": "{_weather_settle_source.caster}",
                    "area_id": "{area_id}",
                },
                ("_weather_live",),
            ),
            _when(
                "_weather_live == true and _weather_settle_source.weather_pending == true and "
                "state.lasting_tick >= _weather_settle_source.weather_ready_tick",
                [
                    _set(
                        "state.persistent_spell_areas.{area_id}.weather_current",
                        "_weather_settle_source.weather_chosen",
                    ),
                    _set("state.persistent_spell_areas.{area_id}.weather_pending", False),
                    _set(
                        "state.persistent_spell_areas.{area_id}.weather_settled_tick",
                        "state.lasting_tick",
                    ),
                ],
            ),
        ],
    )


def _choices():
    return [
        *[_optional(key + "_change", "integer", 0, minimum=-1, maximum=1) for key in STAGES],
        _optional("direction", default="unchanged", enum_values=["unchanged", *DIRECTIONS]),
    ]


def _change_tool():
    return _public(
        _environment_tool(
            "Choose a printed one-stage weather change after the previous transition settles.",
            [_entity("caster_state"), _param("area_id"), *_choices()],
            [
                _call("settle_srd_control_weather", {"area_id": "{area_id}"}),
                _call(
                    "qualify_srd_control_weather_source",
                    {
                        "caster_state": "{_caster_state_id}",
                        "area_id": "{area_id}",
                    },
                    ("_weather_live",),
                ),
                _require(
                    "_weather_live == true", "Choose this actual caster's current Control Weather."
                ),
                _set("_weather_change_source", "state.persistent_spell_areas[area_id]"),
                _require(
                    "_weather_change_source.weather_pending != true",
                    "The previous weather change has not yet taken effect.",
                ),
                _require(
                    "precipitation_change != 0 or temperature_change != 0 or wind_change != 0",
                    "Choose an actual one-stage weather change.",
                ),
                _require(
                    "direction == 'unchanged' or direction == "
                    "_weather_change_source.weather_current.direction or wind_change != 0",
                    "Change wind direction while changing its printed stage.",
                ),
                _set(
                    "_weather_choice_direction", "_weather_change_source.weather_current.direction"
                ),
                _when("direction != 'unchanged'", [_set("_weather_choice_direction", "direction")]),
                _set(
                    "_weather_chosen",
                    {
                        **{
                            key + "_stage": f"_weather_change_source.weather_current.{key}_stage "
                            f"+ {key}_change"
                            for key in STAGES
                        },
                        "direction": "_weather_choice_direction",
                    },
                ),
                *_facts("_weather_chosen"),
                {"dice": {"expression": "1d4"}, "store_as": "_weather_delay_roll"},
                _set("state.persistent_spell_areas.{area_id}.weather_chosen", "_weather_chosen"),
                _set(
                    "state.persistent_spell_areas.{area_id}.weather_delay_roll",
                    "_weather_delay_roll",
                ),
                _set(
                    "state.persistent_spell_areas.{area_id}.weather_ready_tick",
                    "state.lasting_tick + _weather_delay_roll.total * 200",
                ),
                _set("state.persistent_spell_areas.{area_id}.weather_pending", True),
                _set(
                    "state.persistent_spell_areas.{area_id}.weather_choice_sequence",
                    "_weather_change_source.weather_choice_sequence + 1",
                ),
            ],
            public=True,
        ),
        "exploration",
        "Weather change chosen",
        [
            ("Source", "area_id"),
            ("Chosen conditions", "_weather_chosen"),
            ("Delay roll", "_weather_delay_roll"),
        ],
    )


def _query_tool():
    """Weather query preserves admitted ambient facts and overlays the actual current source."""
    return _environment_tool(
        "Read source-qualified current weather stages at one actual same-plane scene point.",
        [_array("point"), _param("plane")],
        [
            _set("_weather_known", False),
            _set("_weather_conditions", None),
            _set("_weather_phase", "'unknown'"),
            _set("_weather_source_id", None),
            _set("_weather_query_ids", "state.persistent_spell_area_ids"),
            _each(
                "_weather_query_ids",
                "_weather_query_id",
                [
                    _set(
                        "_weather_query_source", "state.persistent_spell_areas[_weather_query_id]"
                    ),
                    _when(
                        "_weather_query_source.mode == 'control_weather' and "
                        "_weather_query_source.plane == plane",
                        [
                            _call("settle_srd_control_weather", {"area_id": "{_weather_query_id}"}),
                            _call(
                                "qualify_srd_control_weather_source",
                                {
                                    "caster_state": "{_weather_query_source.caster}",
                                    "area_id": "{_weather_query_id}",
                                },
                                ("_weather_live",),
                            ),
                            _when(
                                "_weather_live == true",
                                [
                                    _set(
                                        "_weather_query_source",
                                        "state.persistent_spell_areas[_weather_query_id]",
                                    ),
                                    _call(
                                        "measure_srd_information_distance",
                                        {
                                            "start": "{point}",
                                            "end": "{_weather_query_source.origin}",
                                        },
                                        ("_scene_distance",),
                                    ),
                                    _when(
                                        "_scene_distance <= 26400",
                                        [
                                            _require(
                                                "_weather_source_id == null",
                                                "Overlapping controlled weather needs "
                                                "a conflict ruling.",
                                            ),
                                            _set("_weather_known", True),
                                            _set(
                                                "_weather_conditions",
                                                "_weather_query_source.weather_current",
                                            ),
                                            _set("_weather_phase", "'controlled'"),
                                            _set("_weather_source_id", "_weather_query_id"),
                                            _when(
                                                "_weather_query_source.weather_pending == true",
                                                [
                                                    _set("_weather_phase", "'changing'"),
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


def _retire_tool():
    """Gradual return is an actual retained consequence, with no invented fixed decay rate."""
    return _environment_tool(
        "Retain the exact ended weather's current/normal stages and gradual-return consequence.",
        [_param("area_id")],
        [
            _set("_weather_return_source", "state.persistent_spell_areas[area_id]"),
            _when(
                "_weather_return_source.active == true and "
                "_weather_return_source.mode == 'control_weather' and "
                "_weather_return_source.weather_return_recorded != true",
                [
                    _when(
                        "_weather_return_source.weather_pending == true and "
                        "state.lasting_tick >= _weather_return_source.weather_ready_tick and "
                        "_weather_return_source.weather_ready_tick < "
                        "_weather_return_source.expires_tick",
                        [
                            _set(
                                "state.persistent_spell_areas.{area_id}.weather_current",
                                "_weather_return_source.weather_chosen",
                            ),
                            _set("state.persistent_spell_areas.{area_id}.weather_pending", False),
                            _set("_weather_return_source", "state.persistent_spell_areas[area_id]"),
                        ],
                    ),
                    _set("_weather_return_records", "state.turn_effects"),
                    _when("_weather_return_records == null", [_set("_weather_return_records", [])]),
                    _push(
                        "_weather_return_records",
                        {
                            "kind": "'environment_weather_return'",
                            "spell": "'control_weather'",
                            "source_sha256": repr(SOURCE_SHA),
                            "source_id": "area_id",
                            "sequence": "_weather_return_source.sequence",
                            "caster": "_weather_return_source.caster",
                            "origin": "_weather_return_source.origin",
                            "plane": "_weather_return_source.plane",
                            "radius": 26400,
                            "current": "_weather_return_source.weather_current",
                            "normal": "_weather_return_source.weather_normal",
                            "started_tick": "state.lasting_tick",
                            "phase": "'gradual_return'",
                            "dispel_ongoing": False,
                        },
                    ),
                    _set("state.turn_effects", "_weather_return_records"),
                    _set("state.persistent_spell_areas.{area_id}.weather_return_recorded", True),
                ],
            ),
        ],
    )


def _refresh_tool():
    return _environment_tool(
        "Refresh controlled-weather clocks and indoor entry at committed scene boundaries.",
        [],
        [
            _set("_weather_refresh_ids", []),
            _when(
                "state.persistent_spell_area_ids != null",
                [
                    _set("_weather_refresh_ids", "state.persistent_spell_area_ids"),
                ],
            ),
            _each(
                "_weather_refresh_ids",
                "_weather_refresh_id",
                [
                    _when(
                        "state.persistent_spell_areas[_weather_refresh_id].mode "
                        "== 'control_weather'",
                        [
                            _call(
                                "settle_srd_control_weather", {"area_id": "{_weather_refresh_id}"}
                            ),
                        ],
                    ),
                ],
            ),
            _when(
                "state.extended_casting_sources != null",
                [
                    _set("_weather_channel_ids", "state.extended_casting_sources"),
                    _each(
                        "_weather_channel_ids",
                        "_weather_channel_id",
                        [
                            _call(
                                "observe_srd_control_weather_channel",
                                {
                                    "caster_state": "{_weather_channel_id}",
                                },
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _fog_tool():
    return _environment_tool(
        "Disperse outdoor spell fog in current same-plane source-qualified strong weather wind.",
        [_param("area_id")],
        [
            _set("_weather_fog", "state.persistent_spell_areas[area_id]"),
            _when(
                "_weather_fog.active == true and state.lasting_tick "
                "< _weather_fog.expires_tick and " + _wind_fog_mode("_weather_fog"),
                [
                    _call(
                        "resolve_srd_weather_setting",
                        {
                            "point": "{_weather_fog.origin}",
                            "plane": "{_weather_fog.plane}",
                            "fallback": "",
                        },
                        ("_weather_setting",),
                    ),
                    _when(
                        "_weather_setting == 'outdoors'",
                        [
                            _call(
                                "resolve_srd_control_weather_point",
                                {
                                    "point": "{_weather_fog.origin}",
                                    "plane": "{_weather_fog.plane}",
                                },
                                WEATHER_EXPORTS,
                            ),
                            _when(
                                "_weather_known == true and _weather_conditions.wind_stage >= 3",
                                [
                                    _call(
                                        "qualify_srd_environment_concentration",
                                        {
                                            "source_state": "{_weather_fog.caster}",
                                            "area_id": "{area_id}",
                                        },
                                    ),
                                    _call(
                                        "end_srd_concentration_node",
                                        {
                                            "source_state": "{_weather_fog.caster}",
                                            "target_state": "{_weather_fog.caster}",
                                            "lasting_state": "state",
                                        },
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def tools():
    return {
        "resolve_srd_weather_setting": _setting_tool(),
        "qualify_srd_control_weather_source": _source_tool(),
        "settle_srd_control_weather": _settle_tool(),
        "choose_srd_control_weather": _change_tool(),
        "resolve_srd_control_weather_point": _query_tool(),
        "retain_srd_control_weather_return": _retire_tool(),
        "refresh_srd_control_weather": _refresh_tool(),
        "disperse_srd_control_weather_fog": _fog_tool(),
        "observe_srd_control_weather_channel": _environment_tool(
            "Interrupt only this actual outdoor weather casting if its caster goes indoors.",
            [_entity("caster_state")],
            [
                _when(
                    "caster_state.concentration_effect_family == 'extended_casting' and "
                    "caster_state.concentration_spell_id == 'control_weather'",
                    [
                        _call(
                            "srd_information_scene_entity",
                            {"subject": "{_caster_state_id}"},
                            POSITION_EXPORTS,
                        ),
                        _call(
                            "resolve_srd_weather_setting",
                            {
                                "point": "{_info_position}",
                                "plane": "{_info_scene_entity.metadata.plane}",
                                "fallback": "{_info_scene_entity.metadata.setting}",
                            },
                            ("_weather_setting",),
                        ),
                        _require(
                            "_weather_setting == 'indoors' or _weather_setting == 'outdoors'",
                            "Admit the weather caster's actual current setting.",
                        ),
                        _when(
                            "_weather_setting == 'indoors'",
                            [
                                _call(
                                    "interrupt_srd_extended_casting",
                                    {"caster_state": "{_caster_state_id}"},
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
    }


def bind_sources(changed):
    """Bind only the new weather profile to genuine cost, concentration, source and wind owners."""
    cast = changed["cast_spell_environment"]
    cast["parameters"] += _choices()
    origin = next(
        i
        for i, e in enumerate(cast["effects"])
        if e.get("set", {}).get("field") == "_environment_cast_origin"
    )
    cast["effects"].insert(
        origin + 1,
        _when(
            "_environment_profile.environment_mode == 'control_weather'",
            [
                *_outdoors(),
                *_facts("_weather_cast_scene.metadata.srd_weather"),
                _set("_environment_cast_origin", "_weather_cast_origin"),
            ],
        ),
    )
    cast["effects"].append(
        _when(
            "_environment_profile.environment_mode == 'control_weather'",
            [
                _set(
                    "state.persistent_spell_areas.{_environment_cast_id}.source_sha256",
                    repr(SOURCE_SHA),
                ),
                _set(
                    "state.persistent_spell_areas.{_environment_cast_id}.weather_normal",
                    "_weather_cast_scene.metadata.srd_weather",
                ),
                _set(
                    "state.persistent_spell_areas.{_environment_cast_id}.weather_current",
                    "_weather_cast_scene.metadata.srd_weather",
                ),
                _set("state.persistent_spell_areas.{_environment_cast_id}.weather_pending", False),
                _set(
                    "state.persistent_spell_areas.{_environment_cast_id}.weather_choice_sequence", 0
                ),
                _call(
                    "choose_srd_control_weather",
                    {
                        "caster_state": "{_caster_state_id}",
                        "area_id": "{_environment_cast_id}",
                        **{key + "_change": "{" + key + "_change}" for key in STAGES},
                        "direction": "{direction}",
                    },
                ),
            ],
        )
    )
    public = changed["cast_spell"]
    for parameter in _choices():
        choice = deepcopy(parameter)
        choice["name"] = "environment_weather_" + parameter["name"]
        public["parameters"].append(choice)
    for effects in _effect_lists(public["effects"]):
        for effect in effects:
            if effect.get("call") == "cast_spell_environment":
                for key in (*[key + "_change" for key in STAGES], "direction"):
                    effect["params"][key] = "{environment_weather_" + key + "}"
    index = changed["refresh_srd_environment_mode_indices"]
    index["effects"].insert(0, _set("state.has_control_weather", False))
    loop = next(e["for_each"] for e in index["effects"] if "for_each" in e)
    loop["do"][-1]["on_true"].append(
        _when(
            "_environment_area.mode == 'control_weather'",
            [
                _set("state.has_control_weather", True),
            ],
        )
    )
    changed["retire_srd_environment_ground_source"]["effects"].insert(
        0,
        _when(
            "state.persistent_spell_areas[area_id].mode == 'control_weather'",
            [
                _call("retain_srd_control_weather_return", {"area_id": "{area_id}"}),
            ],
        ),
    )
    clock = changed["advance_srd_lasting_clock"]
    if "refresh_srd_control_weather" not in str(clock["effects"]):
        wind = next(
            i
            for i, e in enumerate(clock["effects"])
            if e.get("call") == "refresh_srd_environment_wind"
        )
        clock["effects"].insert(
            wind,
            _when(
                "state.has_control_weather == true",
                [_call("refresh_srd_control_weather", {})],
            ),
        )
    changed["refresh_srd_environment_wind"]["effects"].append(
        _when(
            "state.has_control_weather == true",
            [
                _set("_weather_wind_ids", "state.persistent_spell_area_ids"),
                _each(
                    "_weather_wind_ids",
                    "_weather_wind_id",
                    [
                        _when(
                            _wind_fog_mode("state.persistent_spell_areas[_weather_wind_id]"),
                            [
                                _call(
                                    "disperse_srd_control_weather_fog",
                                    {"area_id": "{_weather_wind_id}"},
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        )
    )


def bind_runtime_consumers(text):
    """Capture final movement and longer-casting boundaries; no independent position mutation."""
    import yaml

    if __package__:
        from .srd_timed_senses import _replace_tools
    else:
        from srd_timed_senses import _replace_tools
    runtime = yaml.load(text, Loader=yaml.CSafeLoader)
    changed = {}
    process = deepcopy(runtime["tools"]["process_srd_turn_effects"])
    if "environment_weather_return" not in str(process["effects"]):
        loop = next(
            e["for_each"]
            for e in process["effects"]
            if e.get("for_each", {}).get("collection") == "_pending_turn_effects"
        )
        original = loop["do"]
        loop["do"] = [
            {
                **_when(
                    "_effect.kind == 'environment_weather_return'",
                    [_push("_kept_turn_effects", "_effect")],
                ),
                "on_false": original,
            },
        ]
        changed["process_srd_turn_effects"] = process
    for name in ("move_srd_scene_creature_environment_segment", "move_srd_travel_subject_node"):
        if name not in runtime["tools"]:
            continue
        tool = deepcopy(runtime["tools"][name])
        if "refresh_srd_control_weather" not in str(tool["effects"]):
            tool["effects"].append(
                _when(
                    "lasting_state.has_control_weather == true or "
                    "(lasting_state.extended_casting_sources != null and "
                    "len(lasting_state.extended_casting_sources) > 0)",
                    [
                        _call(
                            "refresh_srd_control_weather", {"lasting_state": "{_lasting_state_id}"}
                        ),
                    ],
                )
            )
            changed[name] = tool
    for name in ("begin_srd_spell_cast", "continue_srd_spell_cast_internal"):
        if name not in runtime["tools"]:
            continue
        tool = deepcopy(runtime["tools"][name])
        if "resolve_srd_weather_setting" in str(tool["effects"]):
            continue
        condition = (
            "spell == 'control_weather'"
            if name.startswith("begin")
            else "caster_state.extended_casting.spell == 'control_weather'"
        )
        facts = _outdoors()
        if name.startswith("begin"):
            facts += _facts("_weather_cast_scene.metadata.srd_weather")
        tool["effects"].insert(0, _when(condition, facts))
        changed[name] = tool
    return _replace_tools(text, changed)
