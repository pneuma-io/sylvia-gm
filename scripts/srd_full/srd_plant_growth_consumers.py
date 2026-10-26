"""Plant Growth composes the final segment cost and authentic eight-hour casting owners."""

from copy import deepcopy

if __package__:
    from .srd_magic_jar_consumers import _walk
    from .srd_plant_growth import CAST_CHOICES, SOURCE_SHA
    from .srd_timed_actor_defenses import _call, _load_runtime, _require, _set, _when
    from .srd_timed_senses import _loop, _push, _replace_tools
else:
    from srd_magic_jar_consumers import _walk
    from srd_plant_growth import CAST_CHOICES, SOURCE_SHA
    from srd_timed_actor_defenses import _call, _load_runtime, _require, _set, _when
    from srd_timed_senses import _loop, _push, _replace_tools

CUTS = "_growth_route_records"
PROFILE_GUARD = "_spell_definition.family == 'plant_growth_spell'"


def _insert(nodes, predicate, additions):
    """Insert once beside the actual owner node, including its genuine nested continuation."""
    count = 0
    for node in list(nodes):
        for key in ("on_true", "on_false"):
            if key in node:
                count += _insert(node[key], predicate, additions)
        if "for_each" in node:
            count += _insert(node["for_each"]["do"], predicate, additions)
        if predicate(node):
            index = nodes.index(node)
            nodes[index + 1 : index + 1] = deepcopy(additions)
            count += 1
    return count


def _growth_route_cuts():
    return [
        _set(CUTS, []),
        _when(
            "lasting_state.has_plant_growth_terrain == true",
            [
                _loop(
                    "_route_zones",
                    "_growth_route_zone",
                    [
                        _when(
                            "_growth_route_zone.metadata.source_spell == 'plant_growth' and "
                            f"_growth_route_zone.metadata.source_sha256 == '{SOURCE_SHA}' and "
                            "_growth_route_zone.metadata.plane == plane",
                            [
                                _push(CUTS, "_growth_route_zone.metadata"),
                                _call(
                                    "srd_plant_growth_sphere_interval",
                                    {
                                        "start": "{start}",
                                        "end": "{end}",
                                        "origin": "{_growth_route_zone.metadata.origin}",
                                        "radius": 100,
                                    },
                                    ["_sphere_hit", "_sphere_entry", "_sphere_exit"],
                                ),
                                _when(
                                    "_sphere_hit == true",
                                    [
                                        _push("_route_cuts", {"t": "_sphere_entry"}),
                                        _push("_route_cuts", {"t": "_sphere_exit"}),
                                    ],
                                ),
                                _set(
                                    "_growth_route_exclusions",
                                    "_growth_route_zone.metadata.exclusions",
                                ),
                                _loop(
                                    "_growth_route_exclusions",
                                    "_growth_route_exclusion",
                                    [
                                        _call(
                                            "srd_information_segment_interval",
                                            {
                                                "start": "{start}",
                                                "end": "{end}",
                                                "volume": "{_growth_route_exclusion}",
                                            },
                                            ["_segment_hit", "_segment_entry", "_segment_exit"],
                                        ),
                                        _when(
                                            "_segment_hit == true",
                                            [
                                                _push("_route_cuts", {"t": "_segment_entry"}),
                                                _push("_route_cuts", {"t": "_segment_exit"}),
                                            ],
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ],
                )
            ],
        ),
    ]


def _growth_piece():
    return [
        _set("_growth_route_extra", 0),
        _when(
            "lasting_state.has_plant_growth_terrain == true",
            [
                _loop(
                    CUTS,
                    "_growth_route_record",
                    [
                        _set(
                            "_growth_route_inside",
                            " + ".join(
                                f"(_route_point[{i}] - _growth_route_record.origin[{i}]) ** 2"
                                for i in range(3)
                            )
                            + " <= 10000",
                        ),
                        _set("_growth_route_exclusions", "_growth_route_record.exclusions"),
                        _loop(
                            "_growth_route_exclusions",
                            "_growth_route_exclusion",
                            [
                                _call(
                                    "srd_information_segment_interval",
                                    {
                                        "start": "{_route_point}",
                                        "end": "{_route_point}",
                                        "volume": "{_growth_route_exclusion}",
                                    },
                                    ["_segment_hit"],
                                ),
                                _when(
                                    "_segment_hit == true", [_set("_growth_route_inside", False)]
                                ),
                            ],
                        ),
                        _when("_growth_route_inside == true", [_set("_growth_route_extra", 3)]),
                    ],
                ),
            ],
        ),
    ]


def bind_movement_consumers(text):
    """Compose ordered segment costs after the final Environment/Mirage movement owners."""
    tools = _load_runtime(text)["tools"]
    if "cast_srd_plant_growth" not in tools:
        raise ValueError("Plant Growth must precede its consumers")
    changed = {}
    route = deepcopy(tools["srd_information_movement_cost"])
    if "_growth_route_records" not in str(route):
        index = next(i for i, e in enumerate(route["effects"]) if "sort" in e)
        route["effects"][index:index] = _growth_route_cuts()
        assert (
            _insert(
                route["effects"],
                lambda n: (
                    n.get("set", {}).get("field") == "_route_plants"
                    and n["set"].get("value") is False
                ),
                _growth_piece(),
            )
            == 1
        )

        def clear_growth(node):
            if node.get("condition") == "_scene_distance <= 30":
                node["on_true"].append(
                    _when(
                        "_route_plant.terrain == 'ordinary_terrain'",
                        [_set("_growth_route_extra", 0)],
                    )
                )

        _walk(route["effects"], clear_growth)
        assert (
            _insert(
                route["effects"],
                lambda n: n.get("condition") == "_route_piece_difficult == true",
                [
                    _when(
                        "_growth_route_extra > 0",
                        [
                            _set(
                                "_route_piece_cost",
                                "_route_piece_cost + "
                                "(_route_cut.t - _route_previous) * _route_distance * "
                                "_growth_route_extra",
                            )
                        ],
                    )
                ],
            )
            == 1
        )
    changed["srd_information_movement_cost"] = route
    return _replace_tools(text, changed)


def bind_casting_consumers(text):
    """Bind actual eight-hour casting before reaction continuations capture cast owners."""
    tools = _load_runtime(text)["tools"]
    if "cast_srd_plant_growth" not in tools:
        raise ValueError("Plant Growth must precede its consumers")
    changed = {}
    begin_owner = (
        "begin_srd_spell_cast_internal"
        if "begin_srd_spell_cast_internal" in tools
        else "begin_srd_spell_cast"
    )
    if begin_owner != "begin_srd_spell_cast":
        public_begin = deepcopy(tools["begin_srd_spell_cast"])
        names = {p["name"] for p in public_begin["parameters"]}
        public_begin["parameters"].extend(
            deepcopy(p) for p in CAST_CHOICES if p["name"] not in names
        )

        def forward(node):
            if node.get("call") == begin_owner:
                node["params"].update({p["name"]: "{" + p["name"] + "}" for p in CAST_CHOICES})

        _walk(public_begin["effects"], forward)
        changed["begin_srd_spell_cast"] = public_begin
    for name in ("cast_spell", begin_owner):
        tool = deepcopy(tools[name])
        names = {p["name"] for p in tool["parameters"]}
        tool["parameters"].extend(deepcopy(p) for p in CAST_CHOICES if p["name"] not in names)
        if "_growth_cast_timing" not in str(tool):
            adjustment = [
                _when(
                    PROFILE_GUARD,
                    [
                        _set("_growth_cast_timing", "'action'"),
                        _when(
                            "plant_growth_mode == 'enrichment'",
                            [
                                _set("_growth_cast_timing", "'8_hours'"),
                            ],
                        ),
                        _set(
                            "_growth_cast_definition_patch", {"casting_time": "_growth_cast_timing"}
                        ),
                        _set(
                            "_spell_definition",
                            "merge(_spell_definition, _growth_cast_definition_patch)",
                        ),
                    ],
                )
            ]
            assert (
                _insert(
                    tool["effects"], lambda n: n.get("store_as") == "_spell_definition", adjustment
                )
                == 1
            )
            if name == "cast_spell":
                guard = _when(
                    PROFILE_GUARD + " and plant_growth_mode == 'enrichment'",
                    [
                        _require(
                            "caster_state.extended_casting != null and "
                            "caster_state.extended_casting.plant_growth_mode == 'enrichment'",
                            "Enrichment must finish its genuine eight-hour casting.",
                        ),
                    ],
                )
                # The authentic completion owner subsequently checks 4800 elapsed rounds.
                assert (
                    _insert(
                        tool["effects"], lambda n: n.get("store_as") == "_spell_definition", [guard]
                    )
                    == 1
                )
            else:
                assert (
                    _insert(
                        tool["effects"],
                        lambda n: n.get("set", {}).get("field") == "caster_state.extended_casting",
                        [
                            _when(
                                PROFILE_GUARD,
                                [
                                    _set(
                                        "caster_state.extended_casting.plant_growth_mode",
                                        "plant_growth_mode",
                                    )
                                ],
                            )
                        ],
                    )
                    == 1
                )
        changed[name] = tool
    return _replace_tools(text, changed)


def bind_consumers(text):
    """Compose both hooks together for an already selected local owning fixture."""
    return bind_movement_consumers(bind_casting_consumers(text))
