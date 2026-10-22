"""Genuine Storm strong wind qualifies covered Fog sources at the existing retirement owner."""

from copy import deepcopy

if __package__:
    from .srd_storm_conditions import _rows
    from .srd_storm_of_vengeance import START
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _set,
        _tool,
        _when,
    )
    from .srd_timed_senses import _replace_tools
else:
    from srd_storm_conditions import _rows
    from srd_storm_of_vengeance import START
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _set,
        _tool,
        _when,
    )
    from srd_timed_senses import _replace_tools

QUERY = "qualify_srd_storm_fog_wind"


def _tool_source():
    """Full original Sphere coverage is supported; partial/residual Fog remains explicit."""
    return _tool(
        "Read whether actual active Storm wind entirely contains the current saved Fog Sphere.",
        [_parameter("area_id"), _entity("caster_state"), _parameter("plane")],
        [
            _set("_storm_fog_full_wind", False),
            _call(
                "format_srd_information_projection",
                {"message": "{area_id}"},
                ("_info_projection_message",),
            ),
            _set("_storm_fog_area_key", "_info_projection_message"),
            _set("_storm_wind_fog", "state.persistent_spell_areas[_storm_fog_area_key]"),
            _when(
                "_storm_wind_fog.profile == 'environment' and _storm_wind_fog.active == true and "
                "state.lasting_tick < _storm_wind_fog.expires_tick and "
                "(_storm_wind_fog.mode == 'fog' or _storm_wind_fog.mode == 'cloudkill' or "
                "_storm_wind_fog.mode == 'stinking_cloud') and "
                "_storm_wind_fog.plane == plane and _storm_wind_fog.caster == _caster_state_id",
                [
                    _when(
                        "caster_state.concentration_effect_family == 'spell_environment' and "
                        "caster_state.concentration_spell_id == _storm_wind_fog.spell and "
                        "caster_state.environment_area_id == _storm_fog_area_key",
                        [
                            _set(
                                "_storm_fog_scene_covered",
                                " and ".join(
                                    f"_storm_wind_fog.origin[{axis}] - _storm_wind_fog.radius >= "
                                    f"state.spatial_scene.map.bounds.min[{axis}] and "
                                    f"_storm_wind_fog.origin[{axis}] + _storm_wind_fog.radius <= "
                                    f"state.spatial_scene.map.bounds.max[{axis}]"
                                    for axis in range(2)
                                ),
                            ),
                            _when(
                                "_storm_fog_scene_covered == true",
                                _rows(
                                    [
                                        _set(
                                            "_storm_fog_horizontal",
                                            "((_storm_wind_fog.origin[0] - "
                                            "_storm_condition_row.origin[0]) ** 2 + "
                                            "(_storm_wind_fog.origin[1] - "
                                            "_storm_condition_row.origin[1]) ** 2) ** 0.5",
                                        ),
                                        _when(
                                            "_storm_fog_horizontal + "
                                            "_storm_wind_fog.radius <= 300 and "
                                            "_storm_wind_fog.origin[2] + _storm_wind_fog.radius < "
                                            "_storm_condition_row.origin[2] and "
                                            "_storm_wind_fog.origin[2] - _storm_wind_fog.radius >= "
                                            "state.spatial_scene.map.bounds.min[2]",
                                            [_set("_storm_fog_full_wind", True)],
                                        ),
                                    ]
                                ),
                            ),
                        ],
                    )
                ],
            ),
        ],
    )


def bind_runtime_consumers(text):
    """Preserve native Fog retirement and refresh real wind after actual Storm advancement."""
    tools = _load_runtime(text)["tools"]
    if "check_srd_storm_of_vengeance_record" not in tools:
        raise ValueError("Storm wind requires the authentic original cloud source.")
    changed = {QUERY: _tool_source()}
    wind = deepcopy(tools["qualify_srd_environment_wind"])
    if QUERY not in str(wind["effects"]):
        wind["effects"].append(
            _when(
                "lasting_state.has_storm_sources == true",
                [
                    _call(
                        QUERY,
                        {
                            "area_id": "{area_id}",
                            "caster_state": "{_environment_area.caster}",
                            "plane": "{_environment_area.plane}",
                            "lasting_state": "{_lasting_state_id}",
                        },
                        ("_storm_fog_full_wind",),
                    ),
                    _when("_storm_fog_full_wind == true", [_set("_environment_wind_found", True)]),
                ],
            )
        )
    changed["qualify_srd_environment_wind"] = wind
    start = deepcopy(tools[START])
    if "refresh_srd_environment_wind" not in str(start["effects"]):
        start["effects"].append(
            _when(
                "lasting_state.has_spell_environments == true",
                [_call("refresh_srd_environment_wind", {"lasting_state": "{_lasting_state_id}"})],
            )
        )
    changed[START] = start
    return _replace_tools(
        text, {name: tool for name, tool in changed.items() if tools.get(name) != tool}
    )
