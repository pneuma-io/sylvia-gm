"""Resolve chosen Celestial lights at real ordinary-movement entry boundaries."""

from copy import deepcopy

if __package__:
    from .srd_celestial_lights import _available
    from .srd_conjured_presences import _call, _lists, _lookup, _subject
    from .srd_scene_relations import _array, _tool_scene
    from .srd_spell_travel import _normalize
    from .srd_timed_actor_defenses import _entity, _load_runtime, _parameter, _require, _set, _when
    from .srd_timed_senses import _loop, _push, _replace_tools
else:
    from scripts.srd_celestial_lights import _available
    from scripts.srd_conjured_presences import _call, _lists, _lookup, _subject
    from scripts.srd_scene_relations import _array, _tool_scene
    from scripts.srd_spell_travel import _normalize
    from scripts.srd_timed_actor_defenses import (
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _when,
    )
    from scripts.srd_timed_senses import _loop, _push, _replace_tools

SEGMENT = "move_srd_scene_creature_celestial_segment"
ROUTE = "qualify_srd_celestial_scene_route"
BUDGET = "qualify_srd_celestial_movement_budget"
EXPORTS = (
    "_scene_movement_distance",
    "_scene_movement_cost",
    "_scene_actor_position",
    "_scene_actor_entity_id",
)


def _branch(condition, on_true, on_false):
    return {"condition": condition, "on_true": on_true, "on_false": on_false}


def _choices():
    return _parameter(
        "celestial_entry_choices",
        "array",
        required=False,
        default=[],
        max_items=8,
        items={
            "type": "object",
            "required": ["source_id", "light"],
            "additionalProperties": False,
            "properties": {
                "source_id": {"type": "string"},
                "light": {"type": "string", "enum": ["healing", "searing"]},
            },
        },
    )


def _membership(start, end, exports):
    return _call(
        "resolve_srd_celestial_body_segment",
        {
            "moving_subject": "{_actor_state_id}",
            "area_id": "{source_id}",
            "start": start,
            "end": end,
        },
        exports,
    )


def _validate():
    """Validate actual source and route intent before any original movement or native roll."""
    return _tool_scene(
        "Validate selected real Celestial entry choices before ordinary movement.",
        [_entity("actor_state"), _array("destination"), _choices()],
        [
            *_subject("{_actor_state_id}", "_celestial_entry_body"),
            _set("_celestial_chosen_ids", []),
            _loop(
                "celestial_entry_choices",
                "_celestial_choice",
                [
                    _set("_celestial_source_id", "_celestial_choice.source_id"),
                    _require(
                        "_celestial_source_id not in _celestial_chosen_ids",
                        "Choose at most one light from each actual Celestial source.",
                    ),
                    _push("_celestial_chosen_ids", "_celestial_source_id"),
                    _call(
                        "qualify_srd_celestial_entry_route",
                        {
                            "actor_state": "{_actor_state_id}",
                            "source_id": "{_celestial_source_id}",
                            "destination": "{destination}",
                        },
                    ),
                ],
            ),
        ],
    )


def _qualify_route():
    return _tool_scene(
        "Qualify an actual unclaimed Cylinder entry on the proposed ordinary route.",
        [_entity("actor_state"), _parameter("source_id"), _array("destination")],
        _lookup()
        + [
            _require("_presence.mode == 'celestial'", "Select this real Celestial source."),
            *_subject("{_actor_state_id}", "_celestial_entry_body"),
            *_available("_actor_state_id"),
            _require(
                "_celestial_available == true",
                "This creature has already received this Celestial's light this turn.",
            ),
            _membership(
                "{_celestial_entry_body.position}",
                "{_celestial_entry_body.position}",
                {"_celestial_started_inside": "_body_hit"},
            ),
            _require(
                "_celestial_started_inside != true",
                "Choose entry light only for a creature starting outside this actual Cylinder.",
            ),
            _membership(
                "{_celestial_entry_body.position}", "{destination}", ["_body_hit", "_body_entry"]
            ),
            _require(
                "_body_hit == true and _body_entry > 0 and _body_entry <= 1",
                "The actual route does not enter this same-plane Cylinder.",
            ),
        ],
    )


def _collect():
    """Reuse exact Cylinder/Box intervals; choose the first actual route boundary."""
    return _tool_scene(
        "Collect the next selected real Cylinder entry from the current scene position.",
        [_entity("actor_state"), _array("destination"), _choices()],
        [
            *_subject("{_actor_state_id}", "_celestial_entry_body"),
            _set("_celestial_next_t", 2),
            _set("_celestial_next_point", "destination"),
            _set("_celestial_entry_events", []),
            _loop(
                "celestial_entry_choices",
                "_celestial_choice",
                [
                    _call(
                        "collect_srd_celestial_source_entry",
                        {
                            "actor_state": "{_actor_state_id}",
                            "source_id": "{_celestial_choice.source_id}",
                            "destination": "{destination}",
                        },
                        ["_celestial_candidate_t", "_celestial_candidate_source"],
                    ),
                    _when(
                        "_celestial_candidate_t != null",
                        [
                            _when(
                                "_celestial_candidate_t < _celestial_next_t",
                                [
                                    _set("_celestial_next_t", "_celestial_candidate_t"),
                                    _set("_celestial_entry_events", []),
                                    _set(
                                        "_celestial_next_point",
                                        [
                                            f"_celestial_entry_body.position[{i}] + "
                                            f"(destination[{i}] - "
                                            f"_celestial_entry_body.position[{i}]) "
                                            "* _celestial_next_t"
                                            for i in range(3)
                                        ],
                                    ),
                                ],
                            ),
                            _when(
                                "_celestial_candidate_t == _celestial_next_t",
                                [
                                    _push(
                                        "_celestial_entry_events",
                                        {
                                            "source_id": "_celestial_choice.source_id",
                                            "light": "_celestial_choice.light",
                                            "cast_id": "_celestial_candidate_source.cast_id",
                                            "sequence": "_celestial_candidate_source.sequence",
                                            "position": "_celestial_next_point",
                                        },
                                    )
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _source_entry():
    return _tool_scene(
        "Retain entry only while this exact actual source is still live and unclaimed.",
        [_entity("actor_state"), _parameter("source_id"), _array("destination")],
        _lookup(False)
        + [
            _set("_celestial_candidate_t", None),
            _set("_celestial_candidate_source", "_presence"),
            _when(
                "_presence.mode == 'celestial' and _presence.active == true and "
                "state.lasting_tick < _presence.expires_tick",
                [
                    _call(
                        "qualify_srd_conjured_presence_focus",
                        {"source_id": "{source_id}"},
                        ["_presence_focus_live"],
                    ),
                    *_available("_actor_state_id"),
                    _when(
                        "_presence_focus_live == true and _celestial_available == true",
                        [
                            *_subject("{_actor_state_id}", "_celestial_entry_body"),
                            _membership(
                                "{_celestial_entry_body.position}",
                                "{_celestial_entry_body.position}",
                                {"_celestial_started_inside": "_body_hit"},
                            ),
                            _when(
                                "_celestial_started_inside != true",
                                [
                                    _membership(
                                        "{_celestial_entry_body.position}",
                                        "{destination}",
                                        ["_body_hit", "_body_entry"],
                                    ),
                                    _when(
                                        "_body_hit == true and _body_entry > 0 "
                                        "and _body_entry <= 1",
                                        [_set("_celestial_candidate_t", "_body_entry")],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _resolve():
    """Qualify the committed endpoint and sight before each source's native light packet."""
    return _tool_scene(
        "Resolve chosen source-qualified entry light at the actual committed boundary.",
        [
            _entity("actor_state"),
            _parameter("source_id"),
            _parameter("event_light", "enum", enum_values=["healing", "searing"]),
            _parameter("event_cast_id"),
            _parameter("event_sequence", "integer"),
            _array("event_point"),
        ],
        _lookup(False)
        + [
            _set("_celestial_light_results", []),
            *_subject("{_actor_state_id}", "_celestial_entry_body"),
            _when(
                "_presence.mode == 'celestial' and _presence.active == true and "
                "state.lasting_tick < _presence.expires_tick and "
                "_presence.cast_id == event_cast_id and _presence.sequence == event_sequence and "
                "_celestial_entry_body.metadata.plane == _presence.plane and "
                + " and ".join(
                    f"_celestial_entry_body.position[{i}] == event_point[{i}]" for i in range(3)
                ),
                [
                    _call(
                        "qualify_srd_conjured_presence_focus",
                        {"source_id": "{source_id}"},
                        ["_presence_focus_live"],
                    ),
                    *_available("_actor_state_id"),
                    _when(
                        "_presence_focus_live == true and _celestial_available == true",
                        [
                            _call(
                                "resolve_srd_conjured_subject_sight",
                                {
                                    "observer_state": "{_presence.caster}",
                                    "subject": "{_actor_state_id}",
                                },
                                ["_illumination_point_can_see", "_illumination_point_plane"],
                            ),
                            _when(
                                "_illumination_point_can_see == true and "
                                "_illumination_point_plane == _presence.plane",
                                [
                                    _set("_celestial_healing_targets", []),
                                    _set("_celestial_searing_targets", []),
                                    _branch(
                                        "event_light == 'healing'",
                                        [_push("_celestial_healing_targets", "_actor_state_id")],
                                        [_push("_celestial_searing_targets", "_actor_state_id")],
                                    ),
                                    _set("_celestial_targets", []),
                                    _push("_celestial_targets", "_actor_state_id"),
                                    _call(
                                        "resolve_srd_celestial_event_lights",
                                        {
                                            "caster_state": "{_presence.caster}",
                                            "source_id": "{source_id}",
                                            "healing_targets": "{_celestial_healing_targets}",
                                            "searing_targets": "{_celestial_searing_targets}",
                                            "event_targets": "{_celestial_targets}",
                                        },
                                        {"_celestial_light_results": "_presence_results"},
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _forward(parameters, destination="{destination}"):
    params = {
        p["name"]: "{"
        + ("_" + p["name"] + "_id" if p.get("resolve") == "entity" else p["name"])
        + "}"
        for p in parameters
    }
    params["destination"] = destination
    return params


def _stop():
    """Stop further packets and segments after actual incapacity or a saved pending window."""
    return [
        _when(
            "actor_state.is_alive != true or actor_state.hp <= 0 or "
            "'restrained' in actor_state.conditions or "
            "'incapacitated' in actor_state.conditions or "
            "'unconscious' in actor_state.conditions or "
            "'paralyzed' in actor_state.conditions or "
            "'petrified' in actor_state.conditions or 'stunned' in actor_state.conditions or "
            "actor_state.spell_flight_fall_pending == true",
            [_set("_celestial_route_continue", False)],
        ),
        _when(
            "lasting_state.pending_srd_action_stack != null and "
            "len(lasting_state.pending_srd_action_stack) > 0",
            [_set("_celestial_route_continue", False)],
        ),
    ]


def _entry_packet():
    """Retain native packet evidence, then respect any consequence before the next packet."""
    return [
        _call(
            "resolve_srd_celestial_entry_light",
            {
                "actor_state": "{_actor_state_id}",
                "source_id": "{_celestial_entered.source_id}",
                "event_light": "{_celestial_entered.light}",
                "event_cast_id": "{_celestial_entered.cast_id}",
                "event_sequence": "{_celestial_entered.sequence}",
                "event_point": "{_celestial_entered.position}",
            },
            ["_celestial_light_results"],
        ),
        _when(
            "len(_celestial_light_results) > 0",
            [
                _when("_celestial_entry_count == null", [_set("_celestial_entry_count", 0)]),
                _set(
                    "_celestial_entry_count",
                    "_celestial_entry_count + len(_celestial_light_results)",
                ),
                _loop(
                    "_celestial_light_results",
                    "_celestial_light_result",
                    [
                        _when(
                            "_celestial_light_result.light == 'searing'",
                            [
                                _when(
                                    "_celestial_entry_damage == null",
                                    [_set("_celestial_entry_damage", 0)],
                                ),
                                _set(
                                    "_celestial_entry_damage",
                                    "_celestial_entry_damage + _celestial_light_result.damage",
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        *_stop(),
    ]


def _route_tools(tools):
    """Reuse actual physical route and budget guards before movement expense or mutation."""
    budget = deepcopy(tools["spend_combat_movement"])
    debits = [
        i
        for i, effect in enumerate(budget["effects"])
        if effect.get("set")
        == {
            "field": "combat.movement_remaining",
            "value": "combat.movement_remaining - _movement_cost",
        }
    ]
    if (
        len(debits) != 1
        or budget["effects"][debits[0] - 1].get("require", {}).get("condition")
        != "_movement_cost <= combat.movement_remaining"
    ):
        raise ValueError("Retain the genuine movement-budget guard immediately before its debit.")
    budget["effects"] = budget["effects"][: debits[0]]
    budget["description"] = "Qualify the existing actual movement budget before its expense."
    budget["metadata"].update(category="internal", visible_to_player=False)
    budget["metadata"].pop("result_card", None)
    route = deepcopy(tools["move_srd_scene_creature_environment_segment"])
    mutations = [i for i, effect in enumerate(route["effects"]) if "spatial_mutation" in effect]
    if len(mutations) != 1:
        raise ValueError("Retain the one canonical physical movement implementation.")
    route["effects"] = route["effects"][: mutations[0]]
    route["description"] = (
        "Qualify the existing actual physical route before expense or relocation."
    )
    expense_calls = 0
    exploration_debits = 0
    for sequence in _lists(route["effects"]):
        for effect in list(sequence):
            if effect.get("call") == "spend_combat_movement":
                effect["call"] = BUDGET
                expense_calls += 1
            if effect.get("set", {}).get("field") == "actor_state.scene_movement_spent":
                sequence.remove(effect)
                exploration_debits += 1
    if expense_calls != 1 or exploration_debits != 1:
        raise ValueError("Retain exactly the existing combat and exploration movement guards.")
    route["metadata"].update(category="internal", visible_to_player=False)
    route["metadata"].pop("result_card", None)
    return {ROUTE: route, BUDGET: budget}


def _movement(original):
    """Wrap the same existing movement implementation with a finite entry-boundary sequence."""
    tool = deepcopy(original)
    tool["parameters"].append(_choices())
    call = _call(SEGMENT, _forward(original["parameters"]), EXPORTS)
    segment = _call(SEGMENT, _forward(original["parameters"], "{_celestial_next_point}"), EXPORTS)
    tool["effects"] = [
        _set("_celestial_entry_count", None),
        _set("_celestial_entry_damage", None),
        _branch(
            "len(celestial_entry_choices) > 0",
            [
                _call(
                    "validate_srd_celestial_entry_choices",
                    {
                        "actor_state": "{_actor_state_id}",
                        "destination": "{destination}",
                        "celestial_entry_choices": "{celestial_entry_choices}",
                    },
                ),
                _call(ROUTE, _forward(original["parameters"])),
                _set("_celestial_route_distance", 0),
                _set("_celestial_route_cost", 0),
                _set("_celestial_route_continue", True),
                _set("_celestial_route_steps", []),
                _loop(
                    "celestial_entry_choices",
                    "_celestial_preparation_choice",
                    [_push("_celestial_route_steps", {})],
                ),
                _push("_celestial_route_steps", {}),
                _loop(
                    "_celestial_route_steps",
                    "_celestial_route_step",
                    [
                        _when(
                            "_celestial_route_continue == true",
                            [
                                _call(
                                    "collect_srd_celestial_entries",
                                    {
                                        "actor_state": "{_actor_state_id}",
                                        "destination": "{destination}",
                                        "celestial_entry_choices": "{celestial_entry_choices}",
                                    },
                                    ["_celestial_next_point", "_celestial_entry_events"],
                                ),
                                segment,
                                _set(
                                    "_celestial_route_distance",
                                    "_celestial_route_distance + _scene_movement_distance",
                                ),
                                _set(
                                    "_celestial_route_cost",
                                    "_celestial_route_cost + _scene_movement_cost",
                                ),
                                *_stop(),
                                _loop(
                                    "_celestial_entry_events",
                                    "_celestial_entered",
                                    [
                                        _when(
                                            "_celestial_route_continue == true",
                                            _entry_packet(),
                                        ),
                                    ],
                                ),
                                _when(
                                    "len(_celestial_entry_events) == 0 or "
                                    + " or ".join(
                                        f"_scene_actor_position[{i}] != _celestial_next_point[{i}]"
                                        for i in range(3)
                                    )
                                    + " or "
                                    + " and ".join(
                                        f"_scene_actor_position[{i}] == destination[{i}]"
                                        for i in range(3)
                                    ),
                                    [_set("_celestial_route_continue", False)],
                                ),
                            ],
                        ),
                    ],
                ),
                _set("_scene_movement_distance", "_celestial_route_distance"),
                _set("_scene_movement_cost", "_celestial_route_cost"),
            ],
            [call],
        ),
    ]
    return tool


def bind_runtime_consumers(text):
    """Bind after real Environment and Celestial movement owners, before final reactions."""
    tools = _load_runtime(text)["tools"]
    original = tools.get(SEGMENT, tools["move_srd_scene_creature_node"])
    emitted = {
        **_route_tools(tools),
        "validate_srd_celestial_entry_choices": _validate(),
        "qualify_srd_celestial_entry_route": _qualify_route(),
        "collect_srd_celestial_entries": _collect(),
        "collect_srd_celestial_source_entry": _source_entry(),
        "resolve_srd_celestial_entry_light": _resolve(),
        "move_srd_scene_creature_node": _movement(original),
    }
    if SEGMENT not in tools:
        segment = deepcopy(original)
        segment["metadata"].update(category="internal", visible_to_player=False)
        emitted[SEGMENT] = segment
        public = deepcopy(tools["move_srd_scene_creature"])
        public["parameters"].append(_choices())
        for sequence in _lists(public["effects"]):
            for effect in sequence:
                if effect.get("call") == "move_srd_scene_creature_node":
                    effect["params"]["celestial_entry_choices"] = "{celestial_entry_choices}"
                    effect.setdefault("export_context", {}).update(
                        {
                            name: name
                            for name in ("_celestial_entry_count", "_celestial_entry_damage")
                        }
                    )
        public["metadata"]["result_card"]["summary"].extend(
            [
                {
                    "label": label,
                    "value": {"path": "context." + name},
                    "optional": True,
                }
                for label, name in [
                    ("Celestial lights", "_celestial_entry_count"),
                    ("Celestial Radiant damage", "_celestial_entry_damage"),
                ]
            ]
        )
        emitted["move_srd_scene_creature"] = public
    _normalize(emitted)
    return _replace_tools(text, emitted)


def emit_late_qualification(text):
    """Capture final genuine movement/cost guards without recapturing any observed wrapper."""
    emitted = _route_tools(_load_runtime(text)["tools"])
    _normalize(emitted)
    return _replace_tools(text, emitted)
