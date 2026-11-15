"""Consume Courier movement and its consequences on the one actual lasting clock."""

from copy import deepcopy

import yaml

if __package__:
    from .srd_animal_messenger import _pace_tool, _route_pieces_tool
    from .srd_courier_body_movement import bind_runtime_consumers as bind_body
    from .srd_courier_interval_pace import bind_runtime_consumers as bind_pace
    from .srd_scene_relations import _tool_scene
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _RuntimeDumper,
        _set,
        _tool,
        _when,
    )
    from .srd_timed_senses import _loop, _replace_tools
else:
    from srd_animal_messenger import _pace_tool, _route_pieces_tool
    from srd_courier_body_movement import bind_runtime_consumers as bind_body
    from srd_courier_interval_pace import bind_runtime_consumers as bind_pace
    from srd_scene_relations import _tool_scene
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _RuntimeDumper,
        _set,
        _tool,
        _when,
    )
    from srd_timed_senses import _loop, _replace_tools


OWNED_CLOCK_BRANCH = (
    "lasting_state.has_animal_messengers == true and combat.encounter_active != true"
)


def _emit(text, changed):
    """Insert private leaves before the actual procedures block, whether empty or populated."""
    tools = _load_runtime(text)["tools"]
    additions = {name: tool for name, tool in changed.items() if name not in tools}
    text = _replace_tools(text, {name: tool for name, tool in changed.items() if name in tools})
    if additions:
        rendered = yaml.dump(additions, Dumper=_RuntimeDumper, sort_keys=False, width=105)
        rendered = "".join(
            "  " + line if line.strip() else line for line in rendered.splitlines(True)
        )
        marker = "\nprocedures:"
        assert marker in text
        text = text.replace(marker, "\n" + rendered + marker, 1)
    return text


def _clock(tools):
    """Keep every final clock callback, omitting only its recursive Courier prefix."""
    tool = deepcopy(tools["advance_srd_lasting_clock"])
    for parameter in tool["parameters"]:
        if parameter["name"] == "elapsed_ticks":
            parameter.update(type="number", minimum=0)
    tool["effects"] = [
        effect
        for effect in tool["effects"]
        if effect.get("call")
        not in ("progress_all_srd_animal_messengers", "advance_srd_courier_chronology")
        and effect.get("set", {}).get("field") != "_courier_clock_end"
    ]
    tool["effects"].insert(
        0,
        _require(
            "elapsed_ticks > 0 and elapsed_ticks <= 200000",
            "Private elapsed time must be positive and within the existing clock bound.",
        ),
    )
    return tool


def _move(tools):
    """Use final physical guards and aftermath; replace only the per-round budget debit."""
    tool = deepcopy(tools["move_srd_scene_creature_environment_segment"])
    mutation = next(i for i, effect in enumerate(tool["effects"]) if "spatial_mutation" in effect)
    debit = next(
        i
        for i, effect in enumerate(tool["effects"])
        if effect.get("condition") == "combat.encounter_active == true"
    )
    tool["effects"][debit : debit + 1] = [
        _require(
            "combat.encounter_active != true", "Courier elapsed travel cannot spend combat turns."
        ),
        _set("_courier_motion_receipt", "actor_state.animal_messenger.chronology_motion"),
        _require(
            "_courier_motion_receipt.instance == actor_state.animal_messenger.instance_id and "
            "_courier_motion_receipt.started_tick == lasting_state.lasting_tick and "
            "_courier_motion_receipt.revision == lasting_state.spatial_scene.map.revision",
            "Use only the immediately derived actual source, clock and geometry receipt.",
        ),
        _require(
            "destination[0] == _courier_motion_receipt.destination[0] and "
            "destination[1] == _courier_motion_receipt.destination[1] and "
            "destination[2] == _courier_motion_receipt.destination[2]",
            "Move only to the actual checked event boundary.",
        ),
        _set("_courier_pace", "_courier_motion_receipt.pace"),
        _set("_courier_next_pace_tick", "_courier_motion_receipt.next_tick"),
        _set("_courier_mode", "_courier_motion_receipt.mode"),
        _require(
            "_courier_pace > 0 and mode == _courier_mode",
            "This actual source has no usable Courier movement in the selected mode.",
        ),
        _set("_courier_motion_elapsed", "_route_cost * _courier_pace ** -1"),
        _require(
            "_courier_motion_elapsed > 0 and lasting_state.lasting_tick + "
            "_courier_motion_elapsed <= _courier_next_pace_tick + 0.000000001",
            "Movement must stop at the next genuine source or clock boundary.",
        ),
        _set("_courier_motion_end", "lasting_state.lasting_tick + _courier_motion_elapsed"),
        _when(
            "abs(_courier_motion_end - _courier_next_pace_tick) < 0.000000001",
            [
                _set("_courier_motion_end", "_courier_next_pace_tick"),
                _set("_courier_motion_elapsed", "_courier_motion_end - lasting_state.lasting_tick"),
            ],
        ),
    ]
    # Locate again after the narrow debit replacement.
    mutation = next(i for i, effect in enumerate(tool["effects"]) if "spatial_mutation" in effect)
    for effect in tool["effects"][:mutation]:
        if effect.get("set", {}).get("field") == "_move_patch":
            effect["set"]["value"]["motion_tick"] = "_courier_motion_end"
    tool["effects"][mutation + 1 : mutation + 1] = [
        _call(
            "advance_srd_courier_clock_boundary",
            {
                "lasting_state": "{_lasting_state_id}",
                "elapsed_ticks": "{_courier_motion_elapsed}",
                "witness_state_ids": "{witness_state_ids}",
            },
        ),
        _set("actor_state.animal_messenger.last_tick", "lasting_state.lasting_tick"),
        _call(
            "project_srd_turn_modifiers",
            {
                "target_state": "{_actor_state_id}",
                "lasting_state": "{_lasting_state_id}",
            },
        ),
    ]
    tool["effects"].insert(
        0,
        _require(
            "actor_state.animal_messenger != null and "
            "actor_state.animal_messenger.last_tick == lasting_state.lasting_tick",
            "Move only the authentic directive on its current actual clock.",
        ),
    )
    return tool


def _step():
    """Calculate the earliest actual route, lifetime, terrain or hazard crossing."""
    boundary_calls = [
        (
            "resolve_srd_environment_ground_entry",
            ["_ground_entry_t", "_ground_entry_ids"],
            "_chronology_has_grease == true",
        ),
        (
            "resolve_srd_environment_hazard_entry",
            ["_hazard_entry_t", "_hazard_entry_events"],
            "state.has_spell_environments == true",
        ),
        (
            "resolve_srd_spirit_guardians_speed_boundary",
            ["_guardian_speed_boundary_t"],
            "_guardian_speed_halved == true",
        ),
        (
            "resolve_srd_spike_growth_boundary",
            ["_spike_boundary_t"],
            "state.has_spike_growth == true",
        ),
    ]
    return _tool_scene(
        "Progress one source-qualified Courier event boundary on the actual clock.",
        [_entity("target_state"), _parameter("interval_end", "number")],
        [
            _set("target_state.animal_messenger.chronology_end", "interval_end"),
            _set("_chronology_start", "state.lasting_tick"),
            _set("_chronology_end", "interval_end"),
            _set("_chronology_area_ids", []),
            _set("_chronology_has_grease", False),
            _when(
                "state.has_spell_environments == true",
                [
                    _set("_chronology_area_ids", "state.persistent_spell_area_ids"),
                ],
            ),
            _loop(
                "_chronology_area_ids",
                "_chronology_area_id",
                [
                    _set("_chronology_area", "state.persistent_spell_areas[_chronology_area_id]"),
                    _when(
                        "_chronology_area.profile == 'environment' and "
                        "_chronology_area.active == true and "
                        "_chronology_area.expires_tick > _chronology_start",
                        [
                            _set(
                                "_chronology_end",
                                "min(_chronology_end, _chronology_area.expires_tick)",
                            ),
                            _when(
                                "_chronology_area.mode == 'grease'",
                                [
                                    _set("_chronology_has_grease", True),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _set("_chronology_goal", "target_state.animal_messenger.destination"),
            _when(
                "target_state.animal_messenger.phase == 'returning'",
                [
                    _set("_chronology_goal", "target_state.animal_messenger.origin"),
                ],
            ),
            _when(
                "target_state.animal_messenger.phase == 'outbound'",
                [
                    _set(
                        "_chronology_end",
                        "min(_chronology_end, target_state.animal_messenger.expires_tick)",
                    ),
                ],
            ),
            _call(
                "project_srd_turn_modifiers",
                {
                    "target_state": "{_target_state_id}",
                    "lasting_state": "{_lasting_state_id}",
                },
            ),
            _call(
                "project_srd_animal_messenger_pace",
                {
                    "target_state": "{_target_state_id}",
                    "at_tick": "{_chronology_start}",
                    "interval_end": "{_chronology_end}",
                },
                [
                    "_courier_pace",
                    "_courier_next_pace_tick",
                    "_courier_mode",
                    "_guardian_speed_halved",
                ],
            ),
            _set("_chronology_end", "_courier_next_pace_tick"),
            _set("_chronology_moved", False),
            _when(
                "_courier_pace > 0 and _chronology_end > _chronology_start and "
                "(target_state.animal_messenger.phase == 'outbound' or "
                "target_state.animal_messenger.phase == 'returning')",
                [
                    _call(
                        "plan_srd_animal_messenger_route",
                        {
                            "target_state": "{_target_state_id}",
                            "goal": "{_chronology_goal}",
                        },
                        ["_courier_route", "_courier_route_reason", "_courier_route_origin"],
                    ),
                    _set("target_state.animal_messenger.route_reason", "_courier_route_reason"),
                    _when(
                        "len(_courier_route) > 0",
                        [
                            _set("_chronology_origin", "_courier_route_origin"),
                            _set("_chronology_waypoint", "_courier_route[0]"),
                            _call(
                                "srd_information_scene_entity",
                                {"subject": "{_target_state_id}"},
                                ["_info_scene_entity"],
                            ),
                            _set("_chronology_plane", "_info_scene_entity.metadata.plane"),
                            _call(
                                "measure_srd_courier_chronology_route_pieces",
                                {
                                    "start": "{_chronology_origin}",
                                    "end": "{_chronology_waypoint}",
                                    "base_difficult": False,
                                    "plane": "{_chronology_plane}",
                                    "at_tick": "{_chronology_start}",
                                    "actor_subject": "{_target_state_id}",
                                    "movement_mode": "{_courier_mode}",
                                },
                                ["_courier_route_pieces"],
                            ),
                            _set(
                                "_chronology_credit",
                                "(_chronology_end - _chronology_start) * _courier_pace",
                            ),
                            _set("_chronology_fraction", 0),
                            _loop(
                                "_courier_route_pieces",
                                "_chronology_piece",
                                [
                                    _when(
                                        "_chronology_credit > 0 and _chronology_piece.cost > 0",
                                        [
                                            _set(
                                                "_chronology_paid",
                                                "min(_chronology_credit, _chronology_piece.cost)",
                                            ),
                                            _set(
                                                "_chronology_fraction",
                                                "_chronology_piece.start + "
                                                "(_chronology_piece.end - "
                                                "_chronology_piece.start) * "
                                                "_chronology_paid * _chronology_piece.cost ** -1",
                                            ),
                                            _set(
                                                "_chronology_credit",
                                                "max(0, _chronology_credit - _chronology_paid)",
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                            _set(
                                "_chronology_destination",
                                [
                                    f"_chronology_origin[{i}] + (_chronology_waypoint[{i}] - "
                                    f"_chronology_origin[{i}]) * _chronology_fraction"
                                    for i in range(3)
                                ],
                            ),
                            _set("_ground_entry_t", 2),
                            _set("_ground_entry_ids", []),
                            _set("_hazard_entry_t", 2),
                            _set("_hazard_entry_events", []),
                            _set("_guardian_speed_boundary_t", 2),
                            _set("_spike_boundary_t", 2),
                            *[
                                _when(
                                    condition,
                                    [
                                        _call(
                                            name,
                                            {
                                                "actor_state": "{_target_state_id}",
                                                "destination": "{_chronology_destination}",
                                                **(
                                                    {"mode": "{_courier_mode}"}
                                                    if "ground" in name or "spike" in name
                                                    else {}
                                                ),
                                            },
                                            exports,
                                        )
                                    ],
                                )
                                for name, exports, condition in boundary_calls
                            ],
                            _set(
                                "_chronology_boundary",
                                "min(1, _ground_entry_t, _hazard_entry_t, "
                                "_guardian_speed_boundary_t, _spike_boundary_t)",
                            ),
                            _set(
                                "_chronology_destination",
                                [
                                    f"_chronology_origin[{i}] + (_chronology_destination[{i}] - "
                                    f"_chronology_origin[{i}]) * _chronology_boundary"
                                    for i in range(3)
                                ],
                            ),
                            _set(
                                "target_state.animal_messenger.chronology_motion",
                                {
                                    "instance": "target_state.animal_messenger.instance_id",
                                    "started_tick": "_chronology_start",
                                    "revision": "state.spatial_scene.map.revision",
                                    "destination": "_chronology_destination",
                                    "pace": "_courier_pace",
                                    "next_tick": "_courier_next_pace_tick",
                                    "mode": "_courier_mode",
                                },
                            ),
                            _call(
                                "move_srd_courier_chronological_segment",
                                {
                                    "actor_state": "{_target_state_id}",
                                    "destination": "{_chronology_destination}",
                                    "mode": "{_courier_mode}",
                                },
                                ["_scene_actor_position", "_scene_movement_distance"],
                            ),
                            _set("_chronology_moved", True),
                            _set("target_state.animal_messenger.chronology_motion", None),
                            _when(
                                "abs(_chronology_boundary - _hazard_entry_t) < 0.000000001",
                                [
                                    _loop(
                                        "_hazard_entry_events",
                                        "_chronology_hazard",
                                        [
                                            _call(
                                                "resolve_srd_environment_hazard_event",
                                                {
                                                    "target_state": (
                                                        "{_chronology_hazard.target_id}"
                                                    ),
                                                    "area_id": "{_chronology_hazard.area_id}",
                                                },
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                            _when(
                                "abs(_chronology_boundary - _ground_entry_t) < 0.000000001",
                                [
                                    _loop(
                                        "_ground_entry_ids",
                                        "_chronology_ground",
                                        [
                                            _call(
                                                "apply_srd_grease_save",
                                                {
                                                    "target_state": "{_target_state_id}",
                                                    "area_id": "{_chronology_ground}",
                                                },
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                            _when(
                                "state.has_spike_growth == true and "
                                "_scene_movement_distance > 0.000000001",
                                [
                                    _call(
                                        "commit_srd_spike_growth_travel",
                                        {
                                            "actor_state": "{_target_state_id}",
                                            "start": "{_chronology_origin}",
                                            "end": "{_scene_actor_position}",
                                            "mode": "{_courier_mode}",
                                        },
                                    ),
                                ],
                            ),
                            _call(
                                "observe_srd_courier_movement_aftermath",
                                {
                                    "actor_state": "{_target_state_id}",
                                    "destination": "{_scene_actor_position}",
                                    "mode": "{_courier_mode}",
                                    "movement_distance": "{_scene_movement_distance}",
                                },
                            ),
                            _call(
                                "measure_srd_information_distance",
                                {
                                    "start": "{_scene_actor_position}",
                                    "end": "{_chronology_goal}",
                                },
                                ["_scene_distance"],
                            ),
                            _when(
                                "_scene_distance < 0.000000001",
                                [
                                    _when(
                                        "target_state.animal_messenger.phase == 'outbound'",
                                        [
                                            _set(
                                                "target_state.animal_messenger.phase", "'arrived'"
                                            ),
                                            _set(
                                                "target_state.animal_messenger.arrived_tick",
                                                "state.lasting_tick",
                                            ),
                                        ],
                                    ),
                                    _when(
                                        "target_state.animal_messenger.phase == 'returning'",
                                        [
                                            _set(
                                                "target_state.animal_messenger.phase", "'returned'"
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _when(
                "_chronology_moved != true and _chronology_end > _chronology_start",
                [
                    _set("_chronology_wait_elapsed", "_chronology_end - _chronology_start"),
                    _call(
                        "advance_srd_courier_clock_boundary",
                        {
                            "lasting_state": "{_lasting_state_id}",
                            "elapsed_ticks": "{_chronology_wait_elapsed}",
                        },
                    ),
                    _set("target_state.animal_messenger.last_tick", "state.lasting_tick"),
                ],
            ),
            _when(
                "target_state.animal_messenger.phase == 'outbound' and "
                "state.lasting_tick >= target_state.animal_messenger.expires_tick",
                [
                    _set("target_state.animal_messenger.phase", "'returning'"),
                    _set("target_state.animal_messenger.message_words", []),
                    _set("target_state.animal_messenger.message_lost", True),
                ],
            ),
        ],
    )


def _controller():
    """Bound the sequence and fail atomically instead of claiming skipped crossings."""
    return _tool(
        "Consume one actual Courier through chronological source and movement boundaries.",
        [_parameter("interval_end", "number")],
        [
            _require(
                "combat.encounter_active != true", "Use actual combat turns for Courier movement."
            ),
            _require(
                "len(state.animal_messenger_targets) == 1",
                "Simultaneous Couriers require the selected shared chronological scheduler.",
            ),
            _set("_chronology_target", "state.animal_messenger_targets[0]"),
            _set("_chronology_steps", list(range(32))),
            _loop(
                "_chronology_steps",
                "_chronology_step_index",
                [
                    _when(
                        "state.lasting_tick + 0.000000001 < interval_end",
                        [
                            _call(
                                "advance_srd_courier_chronology_step",
                                {
                                    "target_state": "{_chronology_target}",
                                    "interval_end": "{interval_end}",
                                },
                            ),
                        ],
                    ),
                ],
            ),
            _require(
                "abs(state.lasting_tick - interval_end) < 0.000000001",
                "This action exceeds the bounded chronological movement event count.",
            ),
            _call(
                "progress_srd_animal_messenger_journey",
                {
                    "target_state": "{_chronology_target}",
                    "interval_end": "{state.lasting_tick}",
                },
                ["_courier_unresolved"],
            ),
            _when(
                "_courier_unresolved != true",
                [
                    _set("state.animal_messenger_targets", []),
                    _set("state.has_animal_messengers", False),
                ],
            ),
        ],
    )


def _movement_aftermath(tools):
    """Keep final observers after the physical route branch, including expanded ward guards."""
    node = deepcopy(tools["move_srd_scene_creature_node"])
    routes = [
        i
        for i, effect in enumerate(node["effects"])
        if any(
            item.get("set", {}).get("field") == "_ground_route_ids"
            for item in effect.get("on_true", [])
        )
    ]
    if len(routes) != 1:
        raise ValueError("Courier chronology requires the final owning environment route branch.")
    node["effects"] = [
        _set("_scene_movement_distance", "movement_distance"),
        *node["effects"][routes[0] + 1 :],
    ]
    node["parameters"].append(_parameter("movement_distance", "number"))
    return node


def bind_runtime_consumers(text):
    """Bind final owners without changing ordinary movement or the public integer clock ABI."""
    text = text.replace("\r\n", "\n")
    tools = _load_runtime(text)["tools"]
    original_clock = deepcopy(tools["advance_srd_lasting_clock"])
    for index, effect in enumerate(original_clock["effects"]):
        if effect.get("condition") == OWNED_CLOCK_BRANCH:
            original_clock["effects"][index : index + 1] = effect["on_false"]
            if original_clock["effects"][-1] == _set("_lasting_tick", "lasting_state.lasting_tick"):
                original_clock["effects"].pop()
            text = _replace_tools(text, {"advance_srd_lasting_clock": original_clock})
            tools["advance_srd_lasting_clock"] = original_clock
            break
    # Reuse the final Speed owner, including later source-qualified environment hooks.
    pace = _pace_tool(tools)
    pace["effects"].insert(0, _set("_guardian_speed_halved", False))

    def target_alias(value):
        if isinstance(value, str):
            return value.replace("_actor_state_id", "_target_state_id")
        if isinstance(value, list):
            return [target_alias(item) for item in value]
        if isinstance(value, dict):
            return {key: target_alias(item) for key, item in value.items()}
        return value

    text = _replace_tools(text, {"project_srd_animal_messenger_pace": target_alias(pace)})
    text = bind_pace(bind_body(text))
    tools = _load_runtime(text)["tools"]
    node = _movement_aftermath(tools)
    changed = {
        "advance_srd_courier_clock_boundary": _clock(tools),
        "move_srd_courier_chronological_segment": _move(tools),
        "advance_srd_courier_chronology_step": _step(),
        "advance_srd_courier_chronology": _controller(),
        "measure_srd_courier_chronology_route_pieces": _route_pieces_tool(tools),
        "observe_srd_courier_movement_aftermath": node,
    }
    clock = deepcopy(tools["advance_srd_lasting_clock"])
    effects = clock["effects"]
    prefix = next(
        i
        for i, effect in enumerate(effects)
        if effect.get("set", {}).get("field") == "_courier_clock_end"
    )
    original = deepcopy(effects[prefix + 1 :])
    clock["effects"][prefix + 1 :] = [
        {
            "condition": OWNED_CLOCK_BRANCH,
            "on_true": [
                _call("advance_srd_courier_chronology", {"interval_end": "{_courier_clock_end}"})
            ],
            "on_false": original,
        },
        _set("_lasting_tick", "lasting_state.lasting_tick"),
    ]
    changed["advance_srd_lasting_clock"] = clock
    return _emit(text, changed)
