"""Project light from the real manifested Flame Blade source and exact saved caster anchor."""

from copy import deepcopy

if __package__:
    from .srd_scene_relations import _array, _snapshot, _tool_scene
    from .srd_spell_illumination import RECORD_EXPORTS, _distance
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _tool,
        _when,
    )
    from .srd_timed_senses import _loop, _replace_tools
else:
    from srd_scene_relations import _array, _snapshot, _tool_scene
    from srd_spell_illumination import RECORD_EXPORTS, _distance
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _tool,
        _when,
    )
    from srd_timed_senses import _loop, _replace_tools


LIGHT_EXPORTS = ("_flame_light_active", "_flame_light_origin", "_flame_light_plane")


def _path_tool():
    """A real same-plane strict interior crossing blocks the light; boundary contact does not."""
    interior = [
        _require(
            "_flame_barrier.volume.kind == 'box' or _flame_barrier.volume.kind == 'cylinder'",
            "Flame Blade light obstruction needs admitted Box or Cylinder interior geometry.",
        ),
        _set("_flame_path_fraction", "(_segment_entry + _segment_exit) * 0.5"),
        *[
            _set(f"_flame_path_{i}", f"start[{i}] + (end[{i}] - start[{i}]) * _flame_path_fraction")
            for i in range(3)
        ],
        _set("_flame_path_interior", False),
        _when(
            "_flame_barrier.volume.kind == 'box'",
            [
                _set(
                    "_flame_path_interior",
                    " and ".join(
                        f"_flame_path_{i} > _flame_barrier.volume.min[{i}] and "
                        f"_flame_path_{i} < _flame_barrier.volume.max[{i}]"
                        for i in range(3)
                    ),
                )
            ],
        ),
        _when(
            "_flame_barrier.volume.kind == 'cylinder'",
            [
                _set(
                    "_flame_path_interior",
                    "(_flame_path_0 - _flame_barrier.volume.center[0]) ** 2 + "
                    "(_flame_path_1 - _flame_barrier.volume.center[1]) ** 2 < "
                    "_flame_barrier.volume.radius ** 2 and "
                    "_flame_path_2 > _flame_barrier.volume.center[2] and "
                    "_flame_path_2 < _flame_barrier.volume.center[2] + "
                    "_flame_barrier.volume.height",
                )
            ],
        ),
        _when("_flame_path_interior == true", [_set("_flame_light_blocked", True)]),
    ]
    return _tool_scene(
        "Qualify current opaque light obstruction on the source's actual plane and interior ray.",
        [_array("start"), _array("end"), _parameter("plane")],
        _snapshot()
        + [
            _set("_flame_light_blocked", False),
            _set("_flame_barriers", "state.spatial_scene.geometry"),
            _loop(
                "_flame_barriers",
                "_flame_barrier",
                [
                    _when(
                        "_flame_barrier.active == true and _flame_barrier.visibility == 'opaque'",
                        [
                            _set("_flame_barrier_plane", "'material'"),
                            _when(
                                "_flame_barrier.metadata.plane != null",
                                [_set("_flame_barrier_plane", "_flame_barrier.metadata.plane")],
                            ),
                            _when(
                                "_flame_barrier_plane == plane",
                                [
                                    _call(
                                        "srd_information_segment_interval",
                                        {
                                            "start": "{start}",
                                            "end": "{end}",
                                            "volume": "{_flame_barrier.volume}",
                                        },
                                        ["_segment_hit", "_segment_entry", "_segment_exit"],
                                    ),
                                    _when(
                                        "_segment_hit == true and _segment_exit > _segment_entry",
                                        interior,
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _index_tool():
    """Derive the cheap source-mode index only from the existing retained rows and clock."""
    return _tool(
        "Refresh Flame Blade's possible light-source index from its existing retained records.",
        [],
        [
            _set("state.has_flame_blade_light", False),
            _set("_flame_index_records", []),
            _when(
                "state.turn_effects != null", [_set("_flame_index_records", "state.turn_effects")]
            ),
            _loop(
                "_flame_index_records",
                "_flame_index_record",
                [
                    _when(
                        "_flame_index_record.kind == 'retained_spell_attack' and "
                        "_flame_index_record.spell == 'flame_blade' and "
                        "_flame_index_record.active == true and "
                        "state.lasting_tick < _flame_index_record.expires_tick",
                        [_set("state.has_flame_blade_light", True)],
                    ),
                ],
            ),
        ],
    )


def _record_tool():
    """Current hand, concentration and scene facts qualify the uncached source light."""
    return _tool(
        "Qualify actual manifested Flame Blade light at its current saved caster scene anchor.",
        [_entity("caster_state"), _parameter("record", "object", from_state="state.turn_effects")],
        [
            _set("_flame_light_active", False),
            _set("_flame_light_origin", None),
            _set("_flame_light_plane", None),
            _call(
                "check_srd_retained_spell_attack_record",
                {"caster_state": "{_caster_state_id}", "record": "{record}"},
                ["_retained_active"],
            ),
            _when(
                "_retained_active == true and record.spell == 'flame_blade' and "
                "record.blade_manifested == true",
                [
                    _call(
                        "qualify_srd_scene_hand_occupancy",
                        {"actor_state": "{_caster_state_id}"},
                        ["_scene_hands_used"],
                    ),
                    _require(
                        "record.hand_index in _scene_hands_used",
                        "Flame Blade's manifested hand needs current anatomical qualification.",
                    ),
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_caster_state_id}"},
                        ["_info_position", "_info_scene_entity"],
                    ),
                    _set("_flame_light_origin", "_info_position"),
                    _set("_flame_light_plane", "'material'"),
                    _when(
                        "_info_scene_entity.metadata.plane != null",
                        [_set("_flame_light_plane", "_info_scene_entity.metadata.plane")],
                    ),
                    _set("_flame_light_active", True),
                ],
            ),
        ],
    )


def _point_tool():
    """The printed Bright10/total Dim20 qualifies only a current same-plane unblocked ray."""
    return _tool(
        "Resolve source-qualified Flame Blade Bright10 and additional Dim10 at an actual point.",
        [
            _parameter("point", "array", items={"type": "number"}, min_items=3, max_items=3),
            _parameter("plane"),
        ],
        [
            _set("_flame_magic_bright", False),
            _set("_flame_magic_dim", False),
            _set("_flame_light_records", []),
            _when(
                "state.has_flame_blade_light == true",
                [_set("_flame_light_records", "state.turn_effects")],
            ),
            _loop(
                "_flame_light_records",
                "_flame_light_record",
                [
                    _when(
                        "_flame_light_record.kind == 'retained_spell_attack' and "
                        "_flame_light_record.spell == 'flame_blade'",
                        [
                            _call(
                                "check_srd_flame_blade_light_record",
                                {
                                    "caster_state": "{_flame_light_record.caster}",
                                    "record": "{_flame_light_record}",
                                },
                                LIGHT_EXPORTS,
                            ),
                            _when(
                                "_flame_light_active == true and _flame_light_plane == plane",
                                [
                                    _distance(
                                        "{_flame_light_origin}", "{point}", "_flame_light_distance"
                                    ),
                                    _when(
                                        "_flame_light_distance <= 20",
                                        [
                                            _call(
                                                "resolve_srd_flame_blade_light_path",
                                                {
                                                    "start": "{_flame_light_origin}",
                                                    "end": "{point}",
                                                    "plane": "{plane}",
                                                },
                                                ["_flame_light_blocked"],
                                            ),
                                            _when(
                                                "_flame_light_blocked != true",
                                                [
                                                    _set("_flame_magic_dim", True),
                                                    _when(
                                                        "_flame_light_distance <= 10",
                                                        [_set("_flame_magic_bright", True)],
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
        ],
    )


def _overlap_tool():
    """Existing uncovered Darkness retires only a manifested eligible whole Flame source."""
    candidate = _when(
        "_flame_overlap_other.kind == 'retained_spell_attack' and "
        "_flame_overlap_other.spell == 'flame_blade' and "
        "_flame_overlap_other.slot_level <= 2",
        [
            _call(
                "check_srd_flame_blade_light_record",
                {
                    "caster_state": "{_flame_overlap_other.caster}",
                    "record": "{_flame_overlap_other}",
                },
                LIGHT_EXPORTS,
            ),
            _when(
                "_flame_light_active == true and _flame_light_plane == _flame_dark_plane",
                [
                    _distance(
                        "{_flame_light_origin}", "{_flame_dark_origin}", "_flame_overlap_distance"
                    ),
                    _when(
                        "_flame_overlap_distance <= 20 + _flame_overlap_dark.dark_radius",
                        [
                            _set("_flame_overlap_fraction", 0),
                            _when(
                                "_flame_overlap_distance > 0",
                                [
                                    _set(
                                        "_flame_overlap_fraction",
                                        "max(0, (_flame_overlap_distance - "
                                        "_flame_overlap_dark.dark_radius) * "
                                        "_flame_overlap_distance ** -1)",
                                    )
                                ],
                            ),
                            _set(
                                "_flame_overlap_point",
                                [
                                    f"_flame_light_origin[{i}] + "
                                    f"(_flame_dark_origin[{i}] - _flame_light_origin[{i}]) * "
                                    "_flame_overlap_fraction"
                                    for i in range(3)
                                ],
                            ),
                            _call(
                                "resolve_srd_flame_blade_light_path",
                                {
                                    "start": "{_flame_light_origin}",
                                    "end": "{_flame_overlap_point}",
                                    "plane": "{_flame_light_plane}",
                                },
                                ["_flame_light_blocked"],
                            ),
                            _require(
                                "_flame_light_blocked != true",
                                "Occluded overlap needs admitted clipped light volumes.",
                            ),
                            _call(
                                "finish_srd_retained_spell_attack_removal",
                                {
                                    "caster_state": "{_flame_overlap_other.caster}",
                                    "source_id": "{_flame_overlap_other.source_id}",
                                },
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    return _tool(
        "Retire exact slot2 Flame Blade light sources overlapping actual uncovered Darkness.",
        [],
        [
            _set("_flame_overlap_records", []),
            _when(
                "state.has_flame_blade_light == true and state.has_illumination_spells == true",
                [_set("_flame_overlap_records", "state.turn_effects")],
            ),
            _loop(
                "_flame_overlap_records",
                "_flame_overlap_dark",
                [
                    _when(
                        "_flame_overlap_dark.kind == 'illumination_spell' and "
                        "_flame_overlap_dark.spell == 'darkness'",
                        [
                            _call(
                                "check_srd_illumination_record",
                                {
                                    "caster_state": "{_flame_overlap_dark.caster}",
                                    "record": "{_flame_overlap_dark}",
                                },
                                RECORD_EXPORTS,
                            ),
                            _set("_flame_dark_origin", "_illumination_origin"),
                            _set("_flame_dark_plane", "_illumination_plane"),
                            _when(
                                "_illumination_active == true and _illumination_covered != true",
                                [
                                    _loop(
                                        "_flame_overlap_records",
                                        "_flame_overlap_other",
                                        [candidate],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def bind_runtime_consumers(text):
    """Append source-light at the settled flag owner and actual source/overlap boundaries."""
    runtime = _load_runtime(text)
    tools = runtime["tools"]
    name = (
        "resolve_srd_information_light_node"
        if "resolve_srd_information_light_node" in tools
        else "resolve_srd_information_light"
    )
    changed = {
        "resolve_srd_flame_blade_light_path": _path_tool(),
        "refresh_srd_flame_blade_light_index": _index_tool(),
        "check_srd_flame_blade_light_record": _record_tool(),
        "resolve_srd_flame_blade_light": _point_tool(),
        "refresh_srd_flame_blade_darkness": _overlap_tool(),
    }
    light = deepcopy(tools[name])
    if "resolve_srd_flame_blade_light" not in str(light["effects"]):
        dark = [
            i
            for i, e in enumerate(light["effects"])
            if e.get("condition") == "_light_magic_dark == true"
        ]
        if len(dark) != 1:
            raise ValueError("Flame Blade light needs the final illumination flag owner.")
        light["effects"].insert(
            dark[0],
            _when(
                "lasting_state.has_flame_blade_light == true",
                [
                    _call(
                        "resolve_srd_flame_blade_light",
                        {"point": "{point}", "plane": "{plane}"},
                        ["_flame_magic_bright", "_flame_magic_dim"],
                    ),
                    _when("_flame_magic_dim == true", [_set("_light_magic_dim", True)]),
                    _when("_flame_magic_bright == true", [_set("_light_magic_bright", True)]),
                ],
            ),
        )
        changed[name] = light
    for boundary in ("refresh_srd_retained_spell_attacks", "manifest_srd_flame_blade"):
        tool = deepcopy(tools[boundary])
        if "refresh_srd_flame_blade_light_index" not in str(tool["effects"]):
            tool["effects"].append(
                _call(
                    "refresh_srd_flame_blade_light_index", {"lasting_state": "{_lasting_state_id}"}
                )
            )
            if boundary == "manifest_srd_flame_blade":
                tool["effects"].append(
                    _call(
                        "resolve_srd_illumination_overlaps",
                        {"lasting_state": "{_lasting_state_id}"},
                    )
                )
            changed[boundary] = tool
    overlap = deepcopy(tools["resolve_srd_illumination_overlaps"])
    if "refresh_srd_flame_blade_darkness" not in str(overlap["effects"]):
        overlap["effects"].append(
            _when(
                "lasting_state.has_flame_blade_light == true",
                [
                    _call(
                        "refresh_srd_flame_blade_darkness", {"lasting_state": "{_lasting_state_id}"}
                    ),
                ],
            )
        )
        changed["resolve_srd_illumination_overlaps"] = overlap
    return _replace_tools(text, changed)
