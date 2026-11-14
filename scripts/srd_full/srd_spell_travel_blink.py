"""Blink binds its actual phase and chosen return to the existing turn and scene owners."""

from copy import deepcopy

if __package__:
    from .srd_scene_relations import _array, _tool_scene
    from .srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _when
    from .srd_timed_senses import _loop, _optional, _public, _push
else:
    from srd_scene_relations import _array, _tool_scene
    from srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _when
    from srd_timed_senses import _loop, _optional, _public, _push


def blink_tools(tools):
    """Each grant records historical departure facts; only the shared scene leaf relocates."""
    if __package__:
        from .srd_spell_travel import _measure, _move, _placement, _scene_subject
    else:
        from srd_spell_travel import _measure, _move, _placement, _scene_subject

    identity = [
        _optional("spell_cast_id", default="", model_hidden=True),
        _optional("spell_cast_owner", default=None, resolve="entity", model_hidden=True),
        _optional("actual_spell_level", "integer", -1, minimum=-1, maximum=9, model_hidden=True),
    ]

    def sight(point):
        if "resolve_srd_illumination_point_sight" not in tools:
            return [_require("false", "Blink return needs the actual point-sight owner.")]
        return [
            _call(
                "resolve_srd_illumination_point_sight",
                {
                    "observer_state": "{_target_state_id}",
                    "point": point,
                    "perception_plane": "{target_state.travel_blink.return_plane}",
                },
                ["_illumination_point_can_see"],
            ),
            _require(
                "_illumination_point_can_see == true",
                "Blink returns to a point the actual creature can see.",
            ),
        ]

    apply = _tool_scene(
        "Install one actual self-only Blink source without moving before its turn-end roll.",
        [
            _entity("target_state"),
            _entity("caster_state"),
            _parameter("slot_level", "integer", minimum=3, maximum=9),
            *identity,
        ],
        [
            _require("_target_state_id == _caster_state_id", "Blink affects only its caster."),
            _require(
                "target_state.travel_blink == null",
                "Overlapping Blink sources are not yet supported; end the existing source first.",
            ),
            _when(
                "spell_cast_id != ''",
                [
                    _require(
                        "spell_cast_owner != null and _spell_cast_owner_id == _caster_state_id "
                        "and caster_state.last_spell_source_cast_id == spell_cast_id "
                        "and caster_state.last_spell_source_spell == 'blink' "
                        "and actual_spell_level == slot_level",
                        "Blink must retain its actual allocated cast owner and slot.",
                    )
                ],
            ),
            _scene_subject("{_target_state_id}", "_blink_caster"),
            _require("_blink_caster.metadata.plane != null", "Record the actual caster plane."),
            _when("state.lasting_tick == null", [_set("state.lasting_tick", 0)]),
            _set(
                "target_state.travel_blink",
                {
                    "spell": "'blink'",
                    "caster": "_caster_state_id",
                    "slot_level": "slot_level",
                    "cast_id": "spell_cast_id",
                    "source_caster_id": None,
                    "ongoing_duration": "spell_cast_id != ''",
                    "created_tick": "state.lasting_tick",
                    "expires_tick": "state.lasting_tick + 20",
                    "phase": False,
                    "return_plane": "_blink_caster.metadata.plane",
                    "phase_origin": None,
                    "return_position": None,
                    "return_in_progress": False,
                    "last_roll_turn": -1,
                    "last_roll": None,
                },
            ),
            _when(
                "spell_cast_id != ''",
                [_set("target_state.travel_blink.source_caster_id", "_spell_cast_owner_id")],
            ),
            _when("state.travel_blink_targets == null", [_set("state.travel_blink_targets", [])]),
            _set("_blink_registered", "state.travel_blink_targets"),
            _when(
                "_target_state_id not in _blink_registered",
                [_push("_blink_registered", "_target_state_id")],
            ),
            _set("state.travel_blink_targets", "_blink_registered"),
        ],
    )
    return_effects = [
        _scene_subject("{_target_state_id}", "_blink_actual"),
        _when(
            "target_state.travel_blink.phase == true and "
            "_blink_actual.metadata.plane == 'ethereal'",
            [
                _set("target_state.travel_blink.return_in_progress", True),
                *_placement(
                    "{_target_state_id}",
                    "{target_state.travel_blink.phase_origin}",
                    "{target_state.travel_blink.return_plane}",
                    [],
                ),
                {
                    "condition": "_travel_placement.distance > 10",
                    "on_true": [_set("_blink_arrival", "_travel_placement.position")],
                    "on_false": [
                        _set("_blink_arrival", None),
                        _when(
                            "target_state.travel_blink.return_position != null",
                            [
                                _set("_blink_arrival", "target_state.travel_blink.return_position"),
                            ],
                        ),
                        _when(
                            "_blink_arrival == null",
                            [
                                _require(
                                    "_travel_placement.distance <= 0.000000001",
                                    "Choose an actual visible Blink return point within ten feet.",
                                ),
                                _set("_blink_arrival", "target_state.travel_blink.phase_origin"),
                            ],
                        ),
                        *_measure(
                            "{target_state.travel_blink.phase_origin}",
                            "{_blink_arrival}",
                            "_blink_return_distance",
                        ),
                        _require(
                            "_blink_return_distance <= 10",
                            "The chosen Blink point must remain within ten feet of departure.",
                        ),
                        *_placement(
                            "{_target_state_id}",
                            "{_blink_arrival}",
                            "{target_state.travel_blink.return_plane}",
                            [],
                        ),
                        _require(
                            "_travel_placement.distance <= 0.000000001",
                            "The actual chosen Blink return point is occupied.",
                        ),
                        *sight("{_blink_arrival}"),
                    ],
                },
                *_move(
                    "{_target_state_id}",
                    "{_blink_arrival}",
                    "{target_state.travel_blink.return_plane}",
                    False,
                ),
            ],
        ),
        _set("target_state.travel_blink.phase", False),
        _set("target_state.travel_blink.return_position", None),
        _set("target_state.travel_blink.return_in_progress", False),
    ]
    returning = _tool_scene(
        "Return a phased caster to its actual chosen point or proved nearest fallback.",
        [_entity("target_state")],
        [_when("target_state.travel_blink != null", return_effects)],
    )
    ending = _tool_scene(
        "End only this actual Blink source, returning a phased caster through the shared leaf.",
        [_entity("target_state")],
        [
            _when(
                "target_state.travel_blink != null",
                [
                    _call("return_srd_blink_target", {"target_state": "{_target_state_id}"}),
                    _set("target_state.travel_blink", None),
                ],
            )
        ],
    )
    refresh = _tool_scene(
        "Expire a saved Blink source at the actual shared clock boundary.",
        [_entity("target_state")],
        [
            _when(
                "target_state.travel_blink != null and "
                "state.lasting_tick >= target_state.travel_blink.expires_tick",
                [_call("end_srd_blink_target", {"target_state": "{_target_state_id}"})],
            )
        ],
    )
    all_sources = _tool_scene(
        "Refresh only registered actual Blink recipients from the shared lifetime clock.",
        [],
        [
            _when(
                "state.travel_blink_targets != null",
                [
                    _set("_blink_targets", "state.travel_blink_targets"),
                    _loop(
                        "_blink_targets",
                        "_blink_target",
                        [
                            _call("refresh_srd_blink_target", {"target_state": "{_blink_target}"}),
                        ],
                    ),
                ],
            )
        ],
    )
    turn_end = _tool_scene(
        "Roll the printed d6 once per actual caster turn and phase only on four through six.",
        [_entity("target_state"), _parameter("boundary_turn", "integer")],
        [
            _call("refresh_srd_blink_target", {"target_state": "{_target_state_id}"}),
            _when(
                "target_state.travel_blink != null and "
                "target_state.travel_blink.last_roll_turn != boundary_turn",
                [
                    {"dice": {"expression": "1d6"}, "store_as": "_blink_roll"},
                    _set("target_state.travel_blink.last_roll", "_blink_roll"),
                    _set("target_state.travel_blink.last_roll_turn", "boundary_turn"),
                    _when(
                        "_blink_roll.total >= 4",
                        [
                            _scene_subject("{_target_state_id}", "_blink_departure"),
                            {
                                "condition": "_blink_departure.metadata.plane == 'ethereal'",
                                "on_true": [
                                    _call(
                                        "end_srd_blink_target",
                                        {"target_state": "{_target_state_id}"},
                                    ),
                                ],
                                "on_false": [
                                    _set(
                                        "target_state.travel_blink.phase_origin",
                                        "_blink_departure.position",
                                    ),
                                    _set(
                                        "target_state.travel_blink.return_plane",
                                        "_blink_departure.metadata.plane",
                                    ),
                                    _set("target_state.travel_blink.phase", True),
                                    _set("target_state.travel_blink.return_position", None),
                                    *_move(
                                        "{_target_state_id}",
                                        "{_blink_departure.position}",
                                        "ethereal",
                                        False,
                                    ),
                                ],
                            },
                        ],
                    ),
                ],
            ),
        ],
    )
    choosing = _public(
        _tool_scene(
            "Choose an actual visible, unoccupied Blink return point without spending an action.",
            [_entity("target_state"), _array("destination")],
            [
                _require(
                    "target_state.travel_blink != null and target_state.travel_blink.phase == true "
                    "and state.lasting_tick < target_state.travel_blink.expires_tick",
                    "Choose a return point only for an actual live phased Blink source.",
                ),
                _scene_subject("{_target_state_id}", "_blink_actual"),
                _require(
                    "_blink_actual.metadata.plane == 'ethereal'",
                    "Only an actually Ethereal Blink caster can choose its return point.",
                ),
                *_measure(
                    "{target_state.travel_blink.phase_origin}",
                    "{destination}",
                    "_blink_return_distance",
                ),
                _require(
                    "_blink_return_distance <= 10",
                    "The chosen Blink point must be within ten feet of actual departure.",
                ),
                *_placement(
                    "{_target_state_id}",
                    "{destination}",
                    "{target_state.travel_blink.return_plane}",
                    [],
                ),
                _require(
                    "_travel_placement.distance <= 0.000000001",
                    "Choose an actual unoccupied Blink return point.",
                ),
                *sight("{destination}"),
                _set("target_state.travel_blink.return_position", "destination"),
                _set("_blink_return_point", "destination"),
            ],
        ),
        "exploration",
        "Blink return selected",
        [("Actual return point", "_blink_return_point")],
    )
    return {
        "apply_srd_blink_target": apply,
        "return_srd_blink_target": returning,
        "end_srd_blink_target": ending,
        "refresh_srd_blink_target": refresh,
        "refresh_srd_blink_sources": all_sources,
        "end_srd_blink_turn": turn_end,
        "choose_srd_blink_return_point": choosing,
    }


def bind_blink_boundaries(tools, changed):
    """Place phase and return at the actual caster boundaries while preserving other consumers."""
    begin = deepcopy(changed.get("begin_combat_turn", tools["begin_combat_turn"]))
    begin["effects"] = [e for e in begin["effects"] if "return_srd_blink_target" not in str(e)]
    begin["effects"].insert(0, _call("return_srd_blink_target", {"target_state": "{_actor_id}"}))
    changed["begin_combat_turn"] = begin
    end = deepcopy(changed.get("end_combat_turn", tools["end_combat_turn"]))
    end["effects"] = [e for e in end["effects"] if "end_srd_blink_turn" not in str(e)]
    boundary = next(
        i
        for i, effect in enumerate(end["effects"])
        if effect.get("set", {}).get("field") == "combat.action_available"
    )
    end["effects"].insert(
        boundary,
        _call(
            "end_srd_blink_turn",
            {
                "target_state": "{_actor_id}",
                "boundary_turn": "{_ending_turn}",
            },
        ),
    )
    changed["end_combat_turn"] = end
    clock = deepcopy(changed.get("advance_srd_lasting_clock", tools["advance_srd_lasting_clock"]))
    clock["effects"] = [e for e in clock["effects"] if "refresh_srd_blink_sources" not in str(e)]
    clock["effects"].append(_call("refresh_srd_blink_sources", {}))
    changed["advance_srd_lasting_clock"] = clock
