"""Source-directed courier travel consumes the actual clock, movement and admitted scene."""

import re
from copy import deepcopy

if __package__:
    from .srd_scene_relations import _array, _snapshot, _spatial, _tool_scene
    from .srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _tool, _when
    from .srd_timed_senses import _loop, _optional, _public, _push
else:
    from srd_scene_relations import _array, _snapshot, _spatial, _tool_scene
    from srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _tool, _when
    from srd_timed_senses import _loop, _optional, _public, _push


def _pace_tool(tools):
    """Reuse the actual per-mode Speed owner and its movement restrictions."""
    budget = deepcopy(tools["derive_srd_scene_movement_budget"])
    guards = [effect["require"]["condition"] for effect in budget["effects"] if "require" in effect]
    guards = [condition.replace("actor_state.", "target_state.") for condition in guards]
    projection_start = next(
        i
        for i, e in enumerate(budget["effects"])
        if e.get("set", {}).get("field") == "_movement_dashes"
    )
    projection = budget["effects"][projection_start:-1]

    def bind(value):
        if isinstance(value, str):
            return re.sub(
                r"\bmode\b",
                "_courier_mode",
                value.replace("actor_state.spell_flying_speed", "_courier_magical_flying").replace(
                    "actor_state.", "target_state."
                ),
            )
        if isinstance(value, list):
            return [bind(item) for item in value]
        if isinstance(value, dict):
            return {key: bind(item) for key, item in value.items()}
        return value

    return _tool(
        "Derive a living courier's native/magical flight and actually usable movement pace.",
        [
            _entity("target_state"),
            _parameter("at_tick", "number"),
            _parameter("interval_end", "number"),
        ],
        [
            _set("_courier_pace", 0),
            _set("_courier_mode", "'walk'"),
            _set("_courier_base_speed", 0),
            _set("_courier_next_pace_tick", "interval_end"),
            _when(
                "target_state.speed != null", [_set("_courier_base_speed", "target_state.speed")]
            ),
            _set("_courier_native_flying", 0),
            _when(
                "target_state.flying_speed != null",
                [_set("_courier_native_flying", "target_state.flying_speed")],
            ),
            _set("_courier_magical_flying", 0),
            _set("_courier_pace_records", []),
            _when(
                "state.turn_effects != null", [_set("_courier_pace_records", "state.turn_effects")]
            ),
            _loop(
                "_courier_pace_records",
                "_courier_pace_record",
                [
                    _when(
                        "_courier_pace_record.kind == 'information_spell' and "
                        "_courier_pace_record.spell == 'speak_with_plants' and "
                        "at_tick + 0.000000001 < _courier_pace_record.expires_tick",
                        [
                            _set(
                                "_courier_next_pace_tick",
                                "min(_courier_next_pace_tick, _courier_pace_record.expires_tick)",
                            ),
                        ],
                    ),
                    _when(
                        "_courier_pace_record.kind == 'actor_mobility' and "
                        "_courier_pace_record.target == _target_state_id and "
                        "_courier_pace_record.mobility == 'flight' and "
                        "(_courier_pace_record.started_tick == null or "
                        "_courier_pace_record.started_tick <= at_tick) and "
                        "at_tick + 0.000000001 < _courier_pace_record.expires_tick",
                        [
                            _set("_courier_flight_active", True),
                            _when(
                                "_courier_pace_record.concentration == true",
                                [
                                    _call(
                                        "check_srd_timed_actor_mobility_source",
                                        {
                                            "caster_state": "{_courier_pace_record.caster}",
                                            "spell": "{_courier_pace_record.spell}",
                                        },
                                        ["_mobility_source_active"],
                                    ),
                                    _set("_courier_flight_active", "_mobility_source_active"),
                                ],
                            ),
                            _when(
                                "_courier_flight_active == true",
                                [
                                    _set(
                                        "_courier_magical_flying",
                                        "max(_courier_magical_flying, "
                                        "_courier_pace_record.magnitude)",
                                    ),
                                    _set(
                                        "_courier_next_pace_tick",
                                        "min(_courier_next_pace_tick, "
                                        "_courier_pace_record.expires_tick)",
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _when(
                "max(_courier_native_flying, _courier_magical_flying) > 0",
                [
                    _set("_courier_mode", "'fly'"),
                    _set(
                        "_courier_base_speed",
                        "max(_courier_native_flying, _courier_magical_flying)",
                    ),
                ],
            ),
            _when(
                "target_state.is_alive == true and _courier_base_speed > 0",
                [
                    _set("_courier_movement_allowed", True),
                    *[
                        _when(f"not ({guard})", [_set("_courier_movement_allowed", False)])
                        for guard in guards
                    ],
                    _when(
                        "_courier_movement_allowed == true",
                        [
                            *bind(projection),
                            _when(
                                "_courier_mode == 'fly' and _movement_speed <= 0 and "
                                "target_state.speed > 0",
                                [
                                    _set("_courier_mode", "'walk'"),
                                    _set("_courier_base_speed", "target_state.speed"),
                                    *bind(projection),
                                ],
                            ),
                            _set("_courier_daily_distance", 132000),
                            _when(
                                "_courier_mode == 'fly'", [_set("_courier_daily_distance", 264000)]
                            ),
                            _set(
                                "_courier_pace",
                                "_courier_daily_distance * 28800 ** -1 * "
                                "min(1, max(0, _movement_speed) * _courier_base_speed ** -1)",
                            ),
                        ],
                    ),
                ],
            ),
            _when("combat.encounter_active == true", [_set("_courier_pace", 0)]),
        ],
    )


def _route_tool():
    """Choose an actual clear direct route or the admitted grid's calculated detour."""
    return _tool_scene(
        "Plan a goal-directed courier route from actual geometry, never supplied route verdicts.",
        [_entity("target_state"), _array("goal")],
        _snapshot()
        + [
            _call(
                "srd_information_scene_entity",
                {"subject": "{_target_state_id}"},
                ["_info_scene_entity", "_info_position"],
            ),
            _set("_courier_route", []),
            _set("_courier_route_reason", "'clear'"),
            _set("_courier_route_origin", "_info_position"),
            _set("_courier_route_entity", "_info_scene_entity"),
            _call(
                "srd_information_barriers",
                {"start": "{_info_position}", "end": "{goal}", "rule": "solid"},
                ["_information_blocked"],
            ),
            {
                "condition": "_information_blocked != true",
                "on_true": [_push("_courier_route", "goal")],
                "on_false": [
                    _set("_courier_grid", "state.spatial_scene.map.grid"),
                    _set(
                        "_courier_start_cell",
                        [
                            f"floor((_courier_route_origin[{i}] - _courier_grid.origin[{i}]) * "
                            "_courier_grid.cell_size ** -1)"
                            for i in range(2)
                        ],
                    ),
                    _set(
                        "_courier_goal_cell",
                        [
                            f"floor((goal[{i}] - _courier_grid.origin[{i}]) * "
                            "_courier_grid.cell_size ** -1)"
                            for i in range(2)
                        ],
                    ),
                    _spatial(
                        "path",
                        {
                            "map_id": "{state.spatial_scene.map.id}",
                            "start": "{_courier_start_cell}",
                            "goal": "{_courier_goal_cell}",
                            "allow_diagonal": True,
                            "corner_policy": "forbid_cutting",
                            "include_entities": True,
                            "moving_entity_id": "{_info_scene_entity.id}",
                            "max_steps": 10000,
                        },
                        "_courier_path",
                    ),
                    _set("_courier_route_reason", "'blocked'"),
                    _when(
                        "_courier_path.truncated == true",
                        [_set("_courier_route_reason", "'route_search_limit'")],
                    ),
                    _when(
                        "_courier_path.found == true",
                        [
                            _set("_courier_route_reason", "'calculated_detour'"),
                            _set("_courier_cells", "_courier_path.cells"),
                            _set("_courier_cell_index", 0),
                            _loop(
                                "_courier_cells",
                                "_courier_cell",
                                [
                                    _when(
                                        "_courier_cell_index > 0",
                                        [
                                            _set(
                                                "_courier_waypoint",
                                                [
                                                    "_courier_grid.origin[0] + "
                                                    "(_courier_cell[0] + 0.5)"
                                                    " * _courier_grid.cell_size",
                                                    "_courier_grid.origin[1] + "
                                                    "(_courier_cell[1] + 0.5)"
                                                    " * _courier_grid.cell_size",
                                                    "_courier_route_origin[2]",
                                                ],
                                            ),
                                            _push("_courier_route", "_courier_waypoint"),
                                        ],
                                    ),
                                    _set("_courier_cell_index", "_courier_cell_index + 1"),
                                ],
                            ),
                            _push("_courier_route", "goal"),
                        ],
                    ),
                ],
            },
        ],
    )


def _route_pieces_tool(tools):
    """Reuse the terrain owner's exact cuts rather than averaging partial route costs."""
    tool = deepcopy(tools["srd_information_movement_cost"])
    tool["parameters"].append(_parameter("at_tick", "number"))
    tool["effects"].insert(0, _set("_courier_route_pieces", []))

    def visit(effects):
        for effect in effects:
            for key in ("on_true", "on_false"):
                if key in effect:
                    visit(effect[key])
            if "for_each" in effect:
                visit(effect["for_each"]["do"])
        for index, effect in enumerate(effects):
            if effect.get("set", {}).get("field") == "_route_cost" and "_route_piece_cost" in str(
                effect
            ):
                effects.insert(
                    index,
                    _push(
                        "_courier_route_pieces",
                        {
                            "start": "_route_previous",
                            "end": "_route_cut.t",
                            "cost": "_route_piece_cost",
                        },
                    ),
                )
                break

    visit(tool["effects"])

    def at_tick(value):
        if isinstance(value, str):
            return value.replace("lasting_state.lasting_tick", "at_tick")
        if isinstance(value, list):
            return [at_tick(item) for item in value]
        if isinstance(value, dict):
            return {key: at_tick(item) for key, item in value.items()}
        return value

    tool["effects"] = at_tick(tool["effects"])
    return tool


def _step_effects():
    """Pay actual terrain pieces, then make one admitted position mutation per waypoint."""
    return [
        _call(
            "measure_srd_animal_messenger_route_pieces",
            {
                "start": "{_courier_position}",
                "end": "{_courier_waypoint}",
                "base_difficult": False,
                "plane": "{_courier_plane}",
                "at_tick": "{interval_start}",
            },
            ["_route_cost", "_route_distance", "_courier_route_pieces"],
        ),
        _when(
            "_route_cost > 0",
            [
                _set("_courier_fraction", 0),
                _set("_courier_segment_paid", 0),
                _loop(
                    "_courier_route_pieces",
                    "_courier_route_piece",
                    [
                        _when(
                            "_courier_credit > 0 and _courier_route_piece.cost > 0",
                            [
                                _set(
                                    "_courier_piece_paid",
                                    "min(_courier_credit, _courier_route_piece.cost)",
                                ),
                                _set(
                                    "_courier_fraction",
                                    "_courier_route_piece.start + "
                                    "(_courier_route_piece.end - _courier_route_piece.start) * "
                                    "_courier_piece_paid * _courier_route_piece.cost ** -1",
                                ),
                                _set(
                                    "_courier_credit",
                                    "max(0, _courier_credit - _courier_piece_paid)",
                                ),
                                _set(
                                    "_courier_segment_paid",
                                    "_courier_segment_paid + _courier_piece_paid",
                                ),
                            ],
                        ),
                    ],
                ),
                _set(
                    "_courier_next",
                    [
                        f"_courier_position[{i}] + (_courier_waypoint[{i}] - "
                        f"_courier_position[{i}]) * _courier_fraction"
                        for i in range(3)
                    ],
                ),
                _set(
                    "_courier_motion",
                    {
                        "previous_position": "_courier_position",
                        "moving": True,
                        "motion_tick": "interval_start + "
                        "(_courier_used_cost + _courier_segment_paid) * pace ** -1",
                    },
                ),
                _set("_courier_metadata", "merge(_courier_entity_metadata, _courier_motion)"),
                {
                    "spatial_mutation": {
                        "scene": "state.spatial_scene",
                        "map_id": "{state.spatial_scene.map.id}",
                        "expected_revision": "{state.spatial_scene.map.revision}",
                        "target": {"kind": "entity", "id": "{_courier_scene_id}"},
                        "patch": {"position": "{_courier_next}", "metadata": "{_courier_metadata}"},
                    },
                    "store_as": "_courier_scene_move",
                },
                _set("_courier_used_cost", "_courier_used_cost + _courier_segment_paid"),
                _set("_courier_position", "_courier_next"),
                _call(
                    "interrupt_srd_identify_movement", {"scene_entity_id": "{_courier_scene_id}"}
                ),
            ],
        ),
    ]


def _interval_tool():
    """Travel only as far as earned source pace permits along actual clear route segments."""
    return _tool_scene(
        "Progress an exact courier directive toward its saved goal over one actual interval.",
        [
            _entity("target_state"),
            _parameter("interval_end", "number"),
            _parameter("interval_start", "number"),
            _parameter("pace", "number"),
        ],
        [
            _set("_courier", "target_state.animal_messenger"),
            _set("_courier_credit", "max(0, interval_end - interval_start) * pace"),
            _set("_courier_used_cost", 0),
            _set("_courier_goal", "_courier.destination"),
            _when("_courier.phase == 'returning'", [_set("_courier_goal", "_courier.origin")]),
            _call(
                "srd_information_scene_entity",
                {"subject": "{_target_state_id}"},
                ["_info_scene_entity", "_info_position"],
            ),
            _set("_courier_position", "_info_position"),
            _set("_courier_scene_id", "_info_scene_entity.id"),
            _set("_courier_entity_metadata", "_info_scene_entity.metadata"),
            _set("_courier_plane", "_info_scene_entity.metadata.plane"),
            _set("target_state.animal_messenger.last_tick", "interval_end"),
            _set("target_state.animal_messenger.travel_credit", 0),
            _when(
                "_courier_plane == _courier.plane and _courier_credit > 0",
                [
                    _call(
                        "plan_srd_animal_messenger_route",
                        {"target_state": "{_target_state_id}", "goal": "{_courier_goal}"},
                        ["_courier_route", "_courier_route_reason"],
                    ),
                    _set("target_state.animal_messenger.route_reason", "_courier_route_reason"),
                    _loop(
                        "_courier_route",
                        "_courier_waypoint",
                        [
                            _when(
                                "_courier_credit > 0",
                                [
                                    _call(
                                        "srd_information_barriers",
                                        {
                                            "start": "{_courier_position}",
                                            "end": "{_courier_waypoint}",
                                            "rule": "solid",
                                        },
                                        ["_information_blocked"],
                                    ),
                                    _when("_information_blocked != true", _step_effects()),
                                    _when(
                                        "_information_blocked == true", [_set("_courier_credit", 0)]
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _call(
                "measure_srd_information_distance",
                {"start": "{_courier_position}", "end": "{_courier_goal}"},
                ["_scene_distance"],
            ),
            _when(
                "_scene_distance < 0.000000001",
                [
                    _when(
                        "_courier.phase == 'outbound'",
                        [
                            _set("target_state.animal_messenger.phase", "'arrived'"),
                            _set("target_state.animal_messenger.arrived_tick", "interval_start"),
                            _when(
                                "pace > 0",
                                [
                                    _set(
                                        "target_state.animal_messenger.arrived_tick",
                                        "interval_start + _courier_used_cost * pace ** -1",
                                    )
                                ],
                            ),
                        ],
                    ),
                    _when(
                        "_courier.phase == 'returning'",
                        [_set("target_state.animal_messenger.phase", "'returned'")],
                    ),
                ],
            ),
        ],
    )


def courier_tools(tools):
    """Emit helpers for source install, the real clock and later delivery."""
    return {
        "project_srd_animal_messenger_pace": _pace_tool(tools),
        "plan_srd_animal_messenger_route": _route_tool(),
        "progress_srd_animal_messenger_interval": _interval_tool(),
        "measure_srd_animal_messenger_route_pieces": _route_pieces_tool(tools),
        "progress_srd_animal_messenger_journey": _journey_tool(),
        "progress_all_srd_animal_messengers": _all_tool(),
    }


def _journey_tool():
    """Split real elapsed travel at source flight and delivery-expiry boundaries."""
    expire = [
        _set("target_state.animal_messenger.phase", "'returning'"),
        _set("target_state.animal_messenger.message_words", []),
        _set("target_state.animal_messenger.message_lost", True),
        _set("target_state.animal_messenger.last_tick", "_courier_cursor"),
    ]
    segment = [
        _when(
            "_courier_cursor < interval_end and "
            "(target_state.animal_messenger.phase == 'outbound' or "
            "target_state.animal_messenger.phase == 'returning')",
            [
                _set("_courier_segment_end", "interval_end"),
                _when(
                    "target_state.animal_messenger.phase == 'outbound'",
                    [
                        _set(
                            "_courier_segment_end",
                            "min(interval_end, target_state.animal_messenger.expires_tick)",
                        ),
                    ],
                ),
                _call(
                    "project_srd_animal_messenger_pace",
                    {
                        "target_state": "{_target_state_id}",
                        "at_tick": "{_courier_cursor}",
                        "interval_end": "{_courier_segment_end}",
                    },
                    ["_courier_pace", "_courier_next_pace_tick"],
                ),
                _set("_courier_segment_end", "_courier_next_pace_tick"),
                _when(
                    "_courier_segment_end > _courier_cursor",
                    [
                        _call(
                            "progress_srd_animal_messenger_interval",
                            {
                                "target_state": "{_target_state_id}",
                                "interval_start": "{_courier_cursor}",
                                "interval_end": "{_courier_segment_end}",
                                "pace": "{_courier_pace}",
                            },
                        ),
                        _set("_courier_cursor", "_courier_segment_end"),
                    ],
                ),
                _when(
                    "target_state.animal_messenger.phase == 'arrived' and "
                    "target_state.animal_messenger.arrived_tick + 0.000000001 >= "
                    "target_state.animal_messenger.expires_tick",
                    expire,
                ),
                _when(
                    "target_state.animal_messenger.phase == 'outbound' and "
                    "_courier_cursor >= target_state.animal_messenger.expires_tick",
                    expire,
                ),
            ],
        ),
    ]
    return _tool(
        "Consume source-directed travel, including return after missed expiry.",
        [_entity("target_state"), _parameter("interval_end", "number")],
        [
            _when(
                "target_state.animal_messenger != null and "
                "target_state.animal_messenger.last_tick < interval_end",
                [
                    _set("_courier_cursor", "target_state.animal_messenger.last_tick"),
                    _set("_courier_segment_records", []),
                    _set("_courier_all_records", []),
                    _when(
                        "state.turn_effects != null",
                        [_set("_courier_all_records", "state.turn_effects")],
                    ),
                    _loop(
                        "_courier_all_records",
                        "_courier_segment_record",
                        [
                            _when(
                                "((_courier_segment_record.kind == 'actor_mobility' and "
                                "_courier_segment_record.target == _target_state_id and "
                                "_courier_segment_record.mobility == 'flight') or "
                                "(_courier_segment_record.kind == 'information_spell' and "
                                "_courier_segment_record.spell == 'speak_with_plants')) and "
                                "_courier_segment_record.expires_tick > _courier_cursor and "
                                "_courier_segment_record.expires_tick < interval_end",
                                [
                                    _push("_courier_segment_records", "_courier_segment_record"),
                                ],
                            ),
                        ],
                    ),
                    _set("_courier_extra_segment", 0),
                    _push("_courier_segment_records", "_courier_extra_segment"),
                    _push("_courier_segment_records", "_courier_extra_segment"),
                    _loop("_courier_segment_records", "_courier_unused_segment", segment),
                ],
            ),
            _set(
                "_courier_unresolved",
                "target_state.animal_messenger != null and "
                "target_state.is_alive == true and "
                "(target_state.animal_messenger.phase == 'outbound' or "
                "target_state.animal_messenger.phase == 'returning' or "
                "target_state.animal_messenger.phase == 'arrived')",
            ),
        ],
    )


def _all_tool():
    """Retain only unresolved courier identities, including returns after source expiration."""
    return _tool(
        "Progress indexed actual couriers only when the saved source index is nonempty.",
        [_parameter("interval_end", "number")],
        [
            _when(
                "state.has_animal_messengers == true",
                [
                    _set("_courier_targets", "state.animal_messenger_targets"),
                    _set("_courier_remaining_targets", []),
                    _loop(
                        "_courier_targets",
                        "_courier_target",
                        [
                            _call(
                                "progress_srd_animal_messenger_journey",
                                {
                                    "target_state": "{_courier_target}",
                                    "interval_end": "{interval_end}",
                                },
                                ["_courier_unresolved"],
                            ),
                            _when(
                                "_courier_unresolved == true",
                                [_push("_courier_remaining_targets", "_courier_target")],
                            ),
                        ],
                    ),
                    _set("state.animal_messenger_targets", "_courier_remaining_targets"),
                    _set("state.has_animal_messengers", "len(_courier_remaining_targets) > 0"),
                ],
            ),
        ],
    )


def deepen_courier_tools(changed, tools):
    """Bind courier source install and the genuine clock; preserve the existing public fence."""
    changed.update(courier_tools(tools))
    cast = changed["cast_srd_animal_messenger"]

    def install(effects):
        for effect in effects:
            if effect.get("set", {}).get("field") == "target_state.animal_messenger":
                effect["set"]["value"].pop("rate_per_tick", None)
                effect["set"]["value"].update(route_reason="'not_started'")
            for branch in ("on_true", "on_false"):
                if branch in effect:
                    install(effect[branch])
        for index, effect in enumerate(effects):
            if effect.get("set", {}).get("field") == "target_state.animal_messenger":
                effects[index + 1 : index + 1] = [
                    _when(
                        "state.animal_messenger_targets == null",
                        [_set("state.animal_messenger_targets", [])],
                    ),
                    _set("_courier_install_targets", "state.animal_messenger_targets"),
                    _when(
                        "_target_state_id not in _courier_install_targets",
                        [_push("_courier_install_targets", "_target_state_id")],
                    ),
                    _set("state.animal_messenger_targets", "_courier_install_targets"),
                    _set("state.has_animal_messengers", True),
                ]
                break

    install(cast["effects"])
    original = changed["advance_srd_animal_messenger"]
    delivery = next(
        e for e in original["effects"] if "recipient_state != null" in e.get("condition", "")
    )
    delivery = deepcopy(delivery)
    delivery["condition"] = delivery["condition"].replace(
        "_courier.phase == 'outbound'", "_courier.phase == 'arrived'"
    )
    changed["advance_srd_animal_messenger"] = _public(
        _tool_scene(
            "Read automatic progress and deliver at the saved destination "
            "to the qualified recipient.",
            deepcopy(original["parameters"][:-1]),
            [
                deepcopy(original["effects"][0]),
                _require(
                    "target_state.animal_messenger != null and target_state.is_alive == true",
                    "Select the actual living source-owned messenger.",
                ),
                _call(
                    "progress_srd_animal_messenger_journey",
                    {"target_state": "{_target_state_id}", "interval_end": "{state.lasting_tick}"},
                ),
                _set("_courier", "target_state.animal_messenger"),
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{_target_state_id}"},
                    ["_info_scene_entity", "_info_position"],
                ),
                _require(
                    "_info_position[0] == destination[0] and _info_position[1] == destination[1] "
                    "and _info_position[2] == destination[2]",
                    "The clock and saved route determine the messenger's actual position.",
                ),
                _set("_courier_position", "_info_position"),
                _set("_courier_delivered", "_courier.phase == 'delivered'"),
                _set("_courier_returned", "_courier.phase == 'returned'"),
                delivery,
            ],
        ),
        "movement",
        "Animal messenger",
        [
            ("Position", "_courier_position"),
            ("Delivered", "_courier_delivered"),
            ("Returned", "_courier_returned"),
        ],
    )
    clock = deepcopy(changed.get("advance_srd_lasting_clock", tools["advance_srd_lasting_clock"]))
    clock["effects"] = [
        e
        for e in clock["effects"]
        if e.get("call") != "progress_all_srd_animal_messengers"
        and e.get("set", {}).get("field") != "_courier_clock_end"
    ]
    index = next(
        i
        for i, e in enumerate(clock["effects"])
        if e.get("set", {}).get("field") == "lasting_state.lasting_tick"
    )
    clock["effects"][index:index] = [
        _set("_courier_clock_end", "lasting_state.lasting_tick + elapsed_ticks"),
        _call("progress_all_srd_animal_messengers", {"interval_end": "{_courier_clock_end}"}),
    ]
    changed["advance_srd_lasting_clock"] = clock
    prep = deepcopy(changed.get("prepare_srd_creature", tools["prepare_srd_creature"]))
    if not any(p["name"] == "flying_speed" for p in prep["parameters"]):
        prep["parameters"].append(
            _optional(
                "flying_speed",
                "integer",
                0,
                minimum=0,
                maximum=1000,
                description="Copy actual native Flying Speed from the source; zero if none.",
            )
        )
    for effect in prep["effects"]:
        if "create_entity" in effect:
            effect["create_entity"]["value"]["flying_speed"] = "flying_speed"
    changed["prepare_srd_creature"] = prep
