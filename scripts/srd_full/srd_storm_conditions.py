"""Storm's later weather is derived from its actual cloud source, never a Weather row."""

from copy import deepcopy

if __package__:
    from .srd_retained_spell_attacks import _lists
    from .srd_scene_relations import _array, _tool_scene
    from .srd_timed_actor_defenses import _call, _load_runtime, _parameter, _set, _tool, _when
    from .srd_timed_senses import _loop, _push, _replace_tools
else:
    from srd_retained_spell_attacks import _lists
    from srd_scene_relations import _array, _tool_scene
    from srd_timed_actor_defenses import _call, _load_runtime, _parameter, _set, _tool, _when
    from srd_timed_senses import _loop, _push, _replace_tools

POINT = "resolve_srd_storm_conditions_point"
SEGMENT = "resolve_srd_storm_conditions_segment"
PUBLIC_TOOLS = set()
POINT_EXPORTS = ("_storm_heavy_obscurement", "_storm_difficult_terrain", "_storm_strong_wind")
SEGMENT_EXPORTS = (
    "_storm_heavy_segment",
    "_storm_difficult_intervals",
    "_storm_strong_wind_segment",
)


def _rows(effects):
    """The same actual turn_effects row owns stage, concentration, clock and geometry."""
    return [
        _set("_storm_condition_rows", []),
        _when(
            "state.has_storm_sources == true and state.turn_effects != null",
            [_set("_storm_condition_rows", "state.turn_effects")],
        ),
        _loop(
            "_storm_condition_rows",
            "_storm_condition_row",
            [
                _when(
                    "_storm_condition_row.kind == 'storm_of_vengeance' and "
                    "_storm_condition_row.plane == plane and "
                    "_storm_condition_row.storm_ordinal >= 5 and "
                    "_storm_condition_row.storm_ordinal <= 10",
                    [
                        _call(
                            "check_srd_storm_of_vengeance_record",
                            {
                                "caster_state": "{_storm_condition_row.caster}",
                                "record": "{_storm_condition_row}",
                            },
                            ("_storm_active",),
                        ),
                        _when("_storm_active == true", effects),
                    ],
                ),
            ],
        ),
    ]


def _point_tool():
    """Only points actually beneath the chosen cloud gain its printed later conditions."""
    return _tool(
        "Read actual current Storm Heavy Obscuration, Difficult Terrain and strong wind.",
        [_array("point"), _parameter("plane")],
        [_set(name, False) for name in POINT_EXPORTS]
        + _rows(
            [
                _when(
                    "point[2] < _storm_condition_row.origin[2] and "
                    "point[2] >= state.spatial_scene.map.bounds.min[2] and "
                    "point[0] >= state.spatial_scene.map.bounds.min[0] and "
                    "point[0] <= state.spatial_scene.map.bounds.max[0] and "
                    "point[1] >= state.spatial_scene.map.bounds.min[1] and "
                    "point[1] <= state.spatial_scene.map.bounds.max[1] and "
                    "(point[0] - _storm_condition_row.origin[0]) ** 2 + "
                    "(point[1] - _storm_condition_row.origin[1]) ** 2 <= 300 ** 2",
                    [_set(name, True) for name in POINT_EXPORTS],
                )
            ]
        ),
    )


def _segment_tool():
    """Scene clipping supplies a lower bound, not an invented cloud altitude or new area."""
    interval = [
        _set(
            "_storm_condition_volume",
            {
                "kind": "cylinder",
                "center": [
                    "_storm_condition_row.origin[0]",
                    "_storm_condition_row.origin[1]",
                    "state.spatial_scene.map.bounds.min[2]",
                ],
                "radius": 300,
                "height": "_storm_condition_row.origin[2] - state.spatial_scene.map.bounds.min[2]",
            },
        ),
        _call(
            "srd_information_segment_interval",
            {
                "start": "{start}",
                "end": "{end}",
                "volume": "{_storm_condition_volume}",
            },
            ("_segment_hit", "_segment_entry", "_segment_exit"),
        ),
        _when(
            "_segment_hit == true and _segment_exit > _segment_entry",
            [
                _set("_storm_condition_fraction", "(_segment_entry + _segment_exit) * 0.5"),
                _set(
                    "_storm_condition_midpoint",
                    [
                        f"start[{i}] + (end[{i}] - start[{i}]) * _storm_condition_fraction"
                        for i in range(3)
                    ],
                ),
                _when(
                    "_storm_condition_midpoint[2] < _storm_condition_row.origin[2] and "
                    "(_storm_condition_midpoint[0] - _storm_condition_row.origin[0]) ** 2 + "
                    "(_storm_condition_midpoint[1] - _storm_condition_row.origin[1]) ** 2 "
                    "< 300 ** 2",
                    [
                        _set("_storm_heavy_segment", True),
                        _set("_storm_strong_wind_segment", True),
                        _push(
                            "_storm_difficult_intervals",
                            {
                                "source_id": "_storm_condition_row.source_id",
                                "entry": "_segment_entry",
                                "exit": "_segment_exit",
                            },
                        ),
                    ],
                ),
            ],
        ),
    ]
    return _tool_scene(
        "Read positive current Storm crossings and source-qualified Difficult Terrain intervals.",
        [_array("start"), _array("end"), _parameter("plane")],
        [
            _set("_storm_heavy_segment", False),
            _set("_storm_strong_wind_segment", False),
            _set("_storm_difficult_intervals", []),
            _when(
                "start[0] == end[0] and start[1] == end[1] and start[2] == end[2]",
                [
                    _call(POINT, {"point": "{start}", "plane": "{plane}"}, POINT_EXPORTS),
                    _set("_storm_heavy_segment", "_storm_heavy_obscurement"),
                    _set("_storm_strong_wind_segment", "_storm_strong_wind"),
                ],
            )
            | {
                "on_false": _rows(
                    [
                        _when(
                            "_storm_condition_row.origin[2] > "
                            "state.spatial_scene.map.bounds.min[2]",
                            interval,
                        )
                    ]
                )
            },
        ],
    )


def bind_runtime_consumers(text):
    """Compose actual Heavy flags before final eye/body/sensory consumers are cloned."""
    tools = _load_runtime(text)["tools"]
    if "check_srd_storm_of_vengeance_record" not in tools:
        raise ValueError("Storm conditions require the authentic paid cloud/source owner.")
    changed = {POINT: _point_tool(), SEGMENT: _segment_tool()}
    for name, query, params, exported, result in (
        (
            "resolve_srd_environment_point",
            POINT,
            {"point": "{point}", "plane": "{plane}"},
            "_storm_heavy_obscurement",
            "_environment_fog",
        ),
        (
            "resolve_srd_environment_segment",
            SEGMENT,
            {"start": "{start}", "end": "{end}", "plane": "{plane}"},
            "_storm_heavy_segment",
            "_environment_sight_obscured",
        ),
    ):
        tool = deepcopy(tools[name])
        if query not in str(tool["effects"]):
            tool["effects"].append(
                _when(
                    "lasting_state.has_storm_sources == true",
                    [
                        _call(
                            query, params | {"lasting_state": "{_lasting_state_id}"}, (exported,)
                        ),
                        _when(exported + " == true", [_set(result, True)]),
                    ],
                )
            )
        changed[name] = tool
    for name, original in tools.items():
        if name in changed:
            continue
        tool = deepcopy(original)
        for sequence in _lists(tool.get("effects", [])):
            for effect in sequence:
                guard = effect.get("condition", "")
                branch = str(effect.get("on_true", []))
                if "has_storm_sources" in guard:
                    continue
                if any(
                    "'call': '" + name + "'" in branch
                    for name in (
                        "resolve_srd_environment_point",
                        "resolve_srd_environment_segment",
                        "resolve_srd_illumination_point_sight",
                        "resolve_srd_illumination_target_sight",
                        "resolve_srd_illumination_object_obscurement",
                    )
                ):
                    for state in ("lasting_state", "state"):
                        existing = state + ".has_spell_environments == true"
                        if existing in guard:
                            effect["condition"] = guard.replace(
                                existing,
                                "(" + existing + " or " + state + ".has_storm_sources == true)",
                            )
                            break
        if tool != original:
            changed[name] = tool
    return _replace_tools(text, {n: t for n, t in changed.items() if tools.get(n) != t})
