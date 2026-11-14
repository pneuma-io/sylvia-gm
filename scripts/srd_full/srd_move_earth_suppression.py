"""Qualify current whole-area earth work against genuine Antimagic Field bodies."""

from copy import deepcopy

if __package__:
    from .srd_timed_actor_defenses import (
        _call,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _tool,
        _when,
    )
    from .srd_timed_senses import _loop, _replace_tools
else:
    from srd_timed_actor_defenses import (
        _call,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _tool,
        _when,
    )
    from srd_timed_senses import _loop, _replace_tools

QUERY = "check_srd_move_earth_current_antimagic"
BODY_EXPORTS = ("_magic_zone_body", "_magic_zone_plane", "_magic_zone_radius")


def _query():
    """Use exact AABB distance to the source Emanation; partial overlap cannot shape a whole Box."""
    body_min = [
        f"_magic_zone_body.position[{i}] - _magic_zone_body.footprint.half_extents[{i}]"
        for i in (0, 1)
    ] + ["_magic_zone_body.position[2]"]
    body_max = [
        f"_magic_zone_body.position[{i}] + _magic_zone_body.footprint.half_extents[{i}]"
        for i in (0, 1)
    ] + ["_magic_zone_body.position[2] + _magic_zone_body.height"]
    distances = [
        _set(
            f"_earth_field_gap_{i}",
            f"max(0, volume.min[{i}] - ({body_max[i]}), ({body_min[i]}) - volume.max[{i}])",
        )
        for i in range(3)
    ]
    return _tool(
        "Qualify only the current supported earth volume against actual active Antimagic Fields.",
        [
            _parameter("volume", "object", from_state="state.turn_effects", model_hidden=True),
            _parameter("plane", model_hidden=True),
        ],
        [
            _require(
                "volume.kind == 'box' and "
                "volume.min[0] < volume.max[0] and volume.min[1] < volume.max[1] and "
                "volume.min[2] < volume.max[2]",
                "Current earth suppression requires the genuine positive whole Box volume.",
            ),
            _call("refresh_srd_spell_magic_zones", {}),
            _set("_earth_field_ids", "state.spell_magic_zone_source_ids"),
            _loop(
                "_earth_field_ids",
                "_earth_field_id",
                [
                    _set("_earth_field", "state.spell_magic_zone_sources[_earth_field_id]"),
                    _when(
                        "_earth_field.installed == true and _earth_field.mode == 'antimagic'",
                        [
                            _call(
                                "read_srd_magic_zone_body",
                                {"source_id": "{_earth_field_id}"},
                                BODY_EXPORTS,
                            ),
                            _when(
                                "_magic_zone_plane == plane",
                                [
                                    *distances,
                                    _require(
                                        "_earth_field_gap_0 ** 2 + _earth_field_gap_1 ** 2 + "
                                        "_earth_field_gap_2 ** 2 > _magic_zone_radius ** 2",
                                        "An actual Antimagic Field overlaps the shaping volume; "
                                        "partial suppressed earth work remains unsupported.",
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _guard(volume, plane):
    return _when(
        "state.has_spell_magic_zones == true",
        [_call(QUERY, {"volume": "{" + volume + "}", "plane": "{" + plane + "}"})],
    )


def bind_runtime_consumers(text):
    """Join current start/pre-mutation checks; field entry/exit chronology remains separate."""
    tools = _load_runtime(text)["tools"]
    required = {
        "prepare_srd_move_earth_change",
        "complete_srd_move_earth_change",
        "refresh_srd_spell_magic_zones",
        "read_srd_magic_zone_body",
    }
    if not required <= tools.keys():
        raise ValueError("Emit the actual earth source and magic-zone owners before suppression.")
    changed = {QUERY: _query()}
    for name, volume, plane in (
        ("prepare_srd_move_earth_change", "_earth_swept", "_earth_geometry.metadata.plane"),
        ("complete_srd_move_earth_change", "_earth_work.swept_volume", "_earth_work.plane"),
    ):
        owner = deepcopy(tools[name])
        guard = _guard(volume, plane)
        prior = [e for e in owner["effects"] if QUERY in str(e)]
        if prior:
            if prior != [guard]:
                raise ValueError(f"The current earth suppression owner changed in {name}.")
        else:
            indices = [
                i
                for i, effect in enumerate(owner["effects"])
                if effect.get("call") == "check_srd_move_earth_clear"
            ]
            if len(indices) != 1:
                raise ValueError(f"Expected exactly one genuine swept-volume check in {name}.")
            owner["effects"].insert(indices[0] + 1, guard)
        changed[name] = owner
    changed = {name: tool for name, tool in changed.items() if tools.get(name) != tool}
    return _replace_tools(text, changed) if changed else text
