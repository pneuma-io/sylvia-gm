"""Creature Dispel retires the authentic Steed source through its one-minute fade."""

from copy import deepcopy

if __package__:
    from .srd_control_behaviors import _lasting, _param, _tool
    from .srd_courier_chronology import _emit
    from .srd_timed_actor_defenses import _call, _entity, _load_runtime, _require, _set, _when
    from .srd_timed_senses import _loop, _push
else:
    from srd_control_behaviors import _lasting, _param, _tool
    from srd_courier_chronology import _emit
    from srd_timed_actor_defenses import _call, _entity, _load_runtime, _require, _set, _when
    from srd_timed_senses import _loop, _push


def _dispel_tool():
    """Use actual horse/source identity and actual casting level, independent of caster life."""
    return _tool(
        "Dispel the selected actual Phantom Steed source, preserving its printed fade.",
        [
            _entity("target_state"),
            _entity("caster_state"),
            _lasting(),
            _param("slot_level", "integer"),
            _param("casting_modifier", "integer"),
            _param("casting_ability"),
            _param("caster", resolve="participant", required=False),
        ],
        [
            _set("_phantom_dispel_checks", []),
            _when(
                "target_state.phantom_steed_source_id != null",
                [
                    _set("_phantom_dispel_source", None),
                    _set("_phantom_dispel_sources", []),
                    _when(
                        "lasting_state.phantom_steed_sources != null",
                        [_set("_phantom_dispel_sources", "lasting_state.phantom_steed_sources")],
                    ),
                    _loop(
                        "_phantom_dispel_sources",
                        "_phantom_dispel_candidate",
                        [
                            _when(
                                "_phantom_dispel_candidate.id == "
                                "target_state.phantom_steed_source_id",
                                [
                                    _require(
                                        "_phantom_dispel_source == null",
                                        "The source horse has an ambiguous retained identity.",
                                    ),
                                    _set("_phantom_dispel_source", "_phantom_dispel_candidate"),
                                ],
                            )
                        ],
                    ),
                    _require(
                        "_phantom_dispel_source != null and "
                        "_phantom_dispel_source.spell == 'phantom_steed' and "
                        "_phantom_dispel_source.steed == _target_state_id and "
                        "_phantom_dispel_source.caster == target_state.phantom_steed_caster",
                        "Select the source's actual horse, not rider or equipment bookkeeping.",
                    ),
                    _when(
                        "_phantom_dispel_source.status == 'active' and "
                        "_phantom_dispel_source.dispel_ongoing == true and "
                        "lasting_state.lasting_tick < _phantom_dispel_source.expires_tick",
                        [
                            _call(
                                "resolve_srd_dispel_source_check",
                                {
                                    "caster_state": "{_caster_state_id}",
                                    "caster": "{_caster_id}",
                                    "slot_level": "{slot_level}",
                                    "source_level": "{_phantom_dispel_source.spell_level}",
                                    "casting_modifier": "{casting_modifier}",
                                    "casting_ability": "{casting_ability}",
                                },
                                ["_dispel_succeeded", "_dispel_roll"],
                            ),
                            _set(
                                "_phantom_dispel_row",
                                {
                                    "source": "_phantom_dispel_source.id",
                                    "roll": None,
                                    "ended": "_dispel_succeeded",
                                },
                            ),
                            _when(
                                "_dispel_roll != null",
                                [
                                    _set(
                                        "_phantom_dispel_row",
                                        {
                                            "source": "_phantom_dispel_source.id",
                                            "roll": "_dispel_roll",
                                            "ended": "_dispel_succeeded",
                                        },
                                    )
                                ],
                            ),
                            _push("_phantom_dispel_checks", "_phantom_dispel_row"),
                            _when(
                                "_dispel_succeeded == true",
                                [
                                    _call(
                                        "retire_srd_phantom_steed_source",
                                        {
                                            "source_id": "{_phantom_dispel_source.id}",
                                            "reason": "dispel",
                                            "ended_tick": "{lasting_state.lasting_tick}",
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


def extend_runtime(text):
    """Emit the private leaf without rewriting shared source/removal owners."""
    return _emit(text, {"dispel_srd_phantom_steed_target": _dispel_tool()})


def bind_target_dispel(text):
    """Append only the actual creature-source callback to the existing paid Dispel consumer."""
    runtime = _load_runtime(text)
    owner = deepcopy(runtime["tools"]["dispel_srd_target_sources"])
    name = "dispel_srd_phantom_steed_target"
    marker = "target_state.phantom_steed_source_id != null"
    owner["effects"] = [
        e for e in owner["effects"] if not (e.get("condition") == marker and name in str(e))
    ]
    owner["effects"].append(
        _when(
            marker,
            [
                _call(
                    name,
                    {
                        "target_state": "{_target_state_id}",
                        "caster_state": "{_caster_state_id}",
                        "lasting_state": "{_lasting_state_id}",
                        "caster": "{_caster_id}",
                        "slot_level": "{slot_level}",
                        "casting_modifier": "{casting_modifier}",
                        "casting_ability": "{casting_ability}",
                    },
                    ["_phantom_dispel_checks"],
                ),
                _loop(
                    "_phantom_dispel_checks",
                    "_phantom_dispel_check",
                    [_push("_dispel_checks", "_phantom_dispel_check")],
                ),
            ],
        )
    )
    return _emit(text, {"dispel_srd_target_sources": owner})
