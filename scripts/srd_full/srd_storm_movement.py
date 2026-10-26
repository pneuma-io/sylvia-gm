"""Storm Difficult Terrain joins the existing movement integral from genuine source intervals."""

from copy import deepcopy

if __package__:
    from .srd_retained_spell_attacks import _lists
    from .srd_storm_conditions import SEGMENT
    from .srd_timed_actor_defenses import _call, _load_runtime, _set, _when
    from .srd_timed_senses import _loop, _push, _replace_tools
else:
    from srd_retained_spell_attacks import _lists
    from srd_storm_conditions import SEGMENT
    from srd_timed_actor_defenses import _call, _load_runtime, _set, _when
    from srd_timed_senses import _loop, _push, _replace_tools

OWNER = "srd_information_movement_cost"


def bind_runtime_consumers(text):
    """Split actual route pieces once, preserving native cost, budgets and physical movement."""
    tools = _load_runtime(text)["tools"]
    if SEGMENT not in tools:
        raise ValueError("Storm movement requires the authentic same-source interval owner.")
    tool = deepcopy(tools[OWNER])
    if "_storm_route_intervals" in str(tool["effects"]):
        return text
    sorts = [
        i
        for i, effect in enumerate(tool["effects"])
        if effect.get("sort", {}).get("collection") == "_route_cuts"
    ]
    if len(sorts) != 1:
        raise ValueError("Storm movement requires the settled native route partition owner.")
    tool["effects"].insert(
        sorts[0],
        _when(
            "lasting_state.has_storm_sources == true",
            [
                _call(
                    SEGMENT,
                    {
                        "start": "{start}",
                        "end": "{end}",
                        "plane": "{plane}",
                        "lasting_state": "{_lasting_state_id}",
                    },
                    ("_storm_difficult_intervals",),
                ),
                _set("_storm_route_intervals", "_storm_difficult_intervals"),
                _loop(
                    "_storm_route_intervals",
                    "_storm_route_interval",
                    [
                        _push("_route_cuts", {"t": "_storm_route_interval.entry"}),
                        _push("_route_cuts", {"t": "_storm_route_interval.exit"}),
                    ],
                ),
            ],
        ),
    )
    changed = 0
    for sequence in _lists(tool["effects"]):
        for i, effect in enumerate(sequence):
            setter = effect.get("set", {})
            if setter.get("field") == "_route_piece_cost" and setter.get("value") == (
                "(_route_cut.t - _route_previous) * _route_distance"
            ):
                sequence.insert(
                    i,
                    _when(
                        "lasting_state.has_storm_sources == true",
                        [
                            _loop(
                                "_storm_route_intervals",
                                "_storm_route_interval",
                                [
                                    _when(
                                        "_route_mid > _storm_route_interval.entry and "
                                        "_route_mid < _storm_route_interval.exit",
                                        [_set("_route_piece_difficult", True)],
                                    )
                                ],
                            )
                        ],
                    ),
                )
                changed += 1
                break
    if changed != 1:
        raise ValueError("Storm movement needs the single pre-multiplier native route piece.")
    return _replace_tools(text, {OWNER: tool})
