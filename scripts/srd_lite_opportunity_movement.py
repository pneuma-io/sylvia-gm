"""Bind coordinate movement to the existing native opportunity owners."""

import json
from copy import deepcopy

from .srd_scene_relations import POSITION_EXPORTS, _tool_scene
from .srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _tool, _when

ROOT = "move_srd_scene_creature"
OWNER = "move_srd_scene_creature_before_opportunity"
FIND = "qualify_srd_coordinate_opportunity_move"
REACTOR = "qualify_srd_coordinate_opportunity_reactor"
PREPARE = "prepare_srd_coordinate_opportunity"
COMPLETE = "complete_srd_coordinate_movement"
CHECK = "qualify_srd_coordinate_departure_budget"
FRACTION = "spend_srd_coordinate_fractional_prefix"
ROUTE = "qualify_srd_celestial_scene_route"
BUDGET = "qualify_srd_celestial_movement_budget"
SEGMENT = "move_srd_scene_creature_environment_segment"
GATE = "state.coordinate_fractional_prefix_actor"
PENDING = "state.pending_srd_movement"
VERSION = "0.3.4"
PUBLIC = (PREPARE, COMPLETE)


def _each(collection, item, effects):
    return {"for_each": {"collection": collection, "as": item, "do": effects}}


def _push(field, value):
    return {"list_push": {"list": field, "value": value}}


def _array(name, default=None):
    value = _parameter(name, "array", items={"type": "number"}, min_items=3, max_items=3)
    if default is not None:
        value.update(required=False, default=default)
    return value


def _public(tool, title):
    tool["metadata"] = {
        "category": "combat",
        "model_behavior": "required_engine_transition",
        "player_controls": {"available": False},
        "result_card": {
            "version": 1,
            "footprint": "compact",
            "title": title,
            "summary": [{"label": "Status", "value": {"path": "context._opportunity_status"}}],
        },
    }
    return tool


def _reactor():
    ready = (
        "reactor_state.is_alive == true and reactor_state.reaction_available == true and "
        "reactor_state.available_reactions != null and "
        "'opportunity_attack' in reactor_state.available_reactions and "
        "reactor_state.opportunity_attacks_blocked != true and "
        "reactor_state.melee_reach != null and reactor_state.melee_reach > 0 and "
        "_reactor_state_id != _mover_state_id and _reactor_state_id not in ignored and "
        "combat.disengaged_actor != _mover_state_id"
    )
    capable = (
        "not ('incapacitated' in reactor_state.conditions or 'paralyzed' in "
        "reactor_state.conditions or 'petrified' in reactor_state.conditions or "
        "'stunned' in reactor_state.conditions or 'unconscious' in reactor_state.conditions)"
    )
    hostile = (
        "(reactor_state.attitudes == null or "
        "reactor_state.attitudes[_mover_state_id] != 'friendly') and "
        "((reactor_state.attitudes != null and "
        "reactor_state.attitudes[_mover_state_id] == 'hostile') or "
        "(reactor_state.fighting_actor_ids != null and "
        "_mover_state_id in reactor_state.fighting_actor_ids))"
    )
    return _tool_scene(
        "Locate an actual hostile body's reach exit without spending or rolling a Reaction.",
        [
            _entity("mover_state"),
            _entity("reactor_state"),
            _array("destination"),
            _parameter("ignored", "array", items={"type": "string"}, required=False, default=[]),
        ],
        [
            _set("_coordinate_reactor", None),
            _set("_coordinate_reactor_ready", ready),
            _set("_coordinate_reactor_capable", capable),
            _set("_coordinate_reactor_hostile", hostile),
            _when(
                "_coordinate_reactor_ready and _coordinate_reactor_capable and "
                "_coordinate_reactor_hostile",
                [
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_mover_state_id}"},
                        POSITION_EXPORTS,
                    ),
                    _set("_coordinate_mover_body", "_info_scene_entity"),
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_reactor_state_id}"},
                        POSITION_EXPORTS,
                    ),
                    _set("_coordinate_reactor_body", "_info_scene_entity"),
                    _when(
                        (
                            "_coordinate_mover_body.metadata.plane =="
                            " _coordinate_reactor_body.metadata.plane"
                        ),
                        [
                            {
                                "spatial_query": {
                                    "scene": "state.spatial_scene",
                                    "operation": "body_proximity",
                                    "query": {
                                        "map_id": "{state.spatial_scene.map.id}",
                                        "moving_entity_id": "{_coordinate_mover_body.id}",
                                        "fixed_entity_id": "{_coordinate_reactor_body.id}",
                                        "destination": "{destination}",
                                        "radius": "{reactor_state.melee_reach}",
                                    },
                                },
                                "store_as": "_coordinate_reach",
                            },
                            _when(
                                "_coordinate_reach.hit == true and _coordinate_reach.exit < 1",
                                [
                                    _call(
                                        "resolve_srd_opportunity_perception",
                                        {
                                            "observer_state": "{_reactor_state_id}",
                                            "target_state": "{_mover_state_id}",
                                            "witness_state_ids": None,
                                        },
                                        ("_observer_can_see_target",),
                                    ),
                                    _when(
                                        "_observer_can_see_target == true",
                                        [
                                            _set(
                                                "_coordinate_reactor",
                                                {
                                                    "actor": "_reactor_state_id",
                                                    "exit": "_coordinate_reach.exit",
                                                    "reach": "reactor_state.melee_reach",
                                                },
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


def _finder():
    return _tool_scene(
        "Find the first supported hostile reach boundary in this actual coordinate segment.",
        [_entity("actor_state"), _array("destination")],
        [
            _set("_coordinate_candidates", []),
            _set("_coordinate_first_exit", 1),
            _set("_coordinate_ignored", []),
            _when(
                PENDING + " != null",
                [
                    _require(
                        PENDING
                        + ".stage == 'resuming' and "
                        + PENDING
                        + ".actor == _actor_state_id",
                        "Resolve the saved movement Reaction before another movement.",
                    ),
                    _set("_coordinate_ignored", PENDING + ".ignored"),
                ],
            ),
            _set("_coordinate_frames", "state.spatial_scene.entities"),
            _each(
                "_coordinate_frames",
                "_coordinate_frame",
                [
                    _set("_coordinate_ref", "_coordinate_frame.id"),
                    _when(
                        "_coordinate_frame.metadata.participant_ref != null",
                        [
                            _set("_coordinate_ref", "_coordinate_frame.metadata.participant_ref"),
                        ],
                    ),
                    _when(
                        "_coordinate_frame.active == true and "
                        "_coordinate_frame.metadata.participant_ref != null and "
                        "_coordinate_frame.metadata.creature_type != null",
                        [
                            _call(
                                REACTOR,
                                {
                                    "mover_state": "{_actor_state_id}",
                                    "reactor_state": "{_coordinate_ref}",
                                    "destination": "{destination}",
                                    "ignored": "{_coordinate_ignored}",
                                },
                                ("_coordinate_reactor",),
                            ),
                            _when(
                                "_coordinate_reactor != null",
                                [
                                    _when(
                                        (
                                            "_coordinate_reactor.exit <"
                                            " _coordinate_first_exit - 0.000001"
                                        ),
                                        [
                                            _set(
                                                "_coordinate_first_exit", "_coordinate_reactor.exit"
                                            ),
                                            _set("_coordinate_candidates", []),
                                        ],
                                    ),
                                    _when(
                                        (
                                            "abs(_coordinate_reactor.exit -"
                                            " _coordinate_first_exit) <="
                                            " 0.000001"
                                        ),
                                        [
                                            _push("_coordinate_candidates", "_coordinate_reactor"),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _call(
                "srd_information_scene_entity", {"subject": "{_actor_state_id}"}, POSITION_EXPORTS
            ),
            _set("_coordinate_move_origin", "_info_position"),
            _call(
                "measure_srd_information_distance",
                {
                    "start": "{_coordinate_move_origin}",
                    "end": "{destination}",
                },
                ("_scene_distance",),
            ),
            _set("_coordinate_requested_distance", "_scene_distance"),
            _set(
                "_coordinate_move_destination",
                [
                    (
                        f"_coordinate_move_origin[{axis}] + (destination[{axis}] -"
                        f" _coordinate_move_origin[{axis}]) * _coordinate_first_exit"
                    )
                    for axis in range(3)
                ],
            ),
        ],
    )


def _prepare(original):
    parameters = deepcopy(original["parameters"])
    parameters = [p for p in parameters if p["name"] != "attack_distance"]
    parameters.append(_parameter("take_reaction", "boolean"))
    call_parameters = {
        p["name"]: "{"
        + ("_" + p["name"] + "_id" if p.get("resolve") == "entity" else p["name"])
        + "}"
        for p in original["parameters"]
    }
    call_parameters["attack_distance"] = "{_coordinate_attack_distance}"
    tool = _tool(
        "Choose this saved movement's actual reacting creature and"
        " delegate to the unchanged native trigger. "
        "Declining spends no Reaction; completion preserves the remaining destination.",
        parameters,
        [
            _require(
                PENDING + " != null and " + PENDING + ".stage == 'selection'",
                "No coordinate movement Reaction choice is pending.",
            ),
            _require(
                PENDING
                + ".actor == _provoking_state_id and "
                + PENDING
                + ".turn_sequence == combat.turn_sequence",
                "The saved movement belongs to a different actor or turn.",
            ),
            _set("_coordinate_selected", None),
            _set("_coordinate_choices", PENDING + ".candidates"),
            _each(
                "_coordinate_choices",
                "_coordinate_choice",
                [
                    _when(
                        "_coordinate_choice.actor == _reactor_state_id",
                        [
                            _set("_coordinate_selected", "_coordinate_choice"),
                        ],
                    ),
                ],
            ),
            _require(
                "_coordinate_selected != null",
                "Select one of the saved actual reach-boundary reactors.",
            ),
            _set("_coordinate_attack_distance", "_coordinate_selected.reach"),
            _set(PENDING + ".selected_reactor", "_reactor_state_id"),
            _set("combat.opportunity_attack_pending", False),
            _set("combat.pending_reaction", None),
            _when(
                "take_reaction == true",
                [
                    _set(PENDING + ".stage", "'preparing'"),
                    {
                        "call": "trigger_opportunity_attack",
                        "params": call_parameters,
                        "export_context": "*",
                    },
                    _set(PENDING + ".stage", "'attack'"),
                    _set("_opportunity_status", "'Native Opportunity Attack prepared'"),
                ],
            ),
            _when(
                "take_reaction != true",
                [
                    _set(PENDING + ".stage", "'resume'"),
                    _set(
                        "_opportunity_status",
                        "'Reaction declined; remaining movement awaits completion'",
                    ),
                ],
            ),
        ],
    )
    return _public(tool, "Movement Reaction decision")


def _complete(root):
    call_parameters = {
        p["name"]: "{" + PENDING + ".parameters." + p["name"] + "}" for p in root["parameters"]
    }
    return _public(
        _tool(
            (
                "Continue the saved remaining destination or explicitly stop"
                " after the committed movement prefix."
            ),
            [_parameter("continue_movement", "boolean")],
            [
                _require(
                    PENDING
                    + " != null and "
                    + PENDING
                    + ".stage == 'resume' and combat.opportunity_attack_pending != true",
                    "Resolve the saved native movement Reaction first.",
                ),
                _require(
                    PENDING
                    + ".actor == combat.active_actor and "
                    + PENDING
                    + ".turn_sequence == combat.turn_sequence",
                    "The saved movement belongs to a different actor or turn.",
                ),
                _set("_coordinate_done_ignored", PENDING + ".ignored"),
                _push("_coordinate_done_ignored", PENDING + ".selected_reactor"),
                _set(PENDING + ".ignored", "_coordinate_done_ignored"),
                _set("_coordinate_remaining_reactors", []),
                _set("_coordinate_saved_candidates", PENDING + ".candidates"),
                _each(
                    "_coordinate_saved_candidates",
                    "_coordinate_candidate",
                    [
                        _when(
                            "_coordinate_candidate.actor not in _coordinate_done_ignored",
                            [
                                _push("_coordinate_remaining_reactors", "_coordinate_candidate"),
                            ],
                        ),
                    ],
                ),
                _when(
                    "continue_movement == true and len(_coordinate_remaining_reactors) > 0",
                    [
                        _set(PENDING + ".candidates", "_coordinate_remaining_reactors"),
                        _set(PENDING + ".stage", "'selection'"),
                        _set(PENDING + ".selected_reactor", None),
                        _set("combat.opportunity_attack_pending", True),
                        _set(
                            "combat.pending_reaction",
                            {
                                "kind": "'movement_opportunity_selection'",
                                "provoking_entity": PENDING + ".actor",
                            },
                        ),
                        _set(
                            "_opportunity_status",
                            "'Another actual reach-boundary reactor awaits decision'",
                        ),
                    ],
                ),
                _when(
                    "continue_movement == true and len(_coordinate_remaining_reactors) == 0",
                    [
                        _set(PENDING + ".stage", "'resuming'"),
                        {"call": ROOT, "params": call_parameters, "export_context": "*"},
                        _set(
                            "_opportunity_status",
                            "'Saved movement continued through the native movement owner'",
                        ),
                    ],
                ),
                _when(
                    "continue_movement != true",
                    [
                        _set(PENDING, None),
                        _set(
                            "_opportunity_status",
                            "'Remaining movement cancelled; committed movement retained'",
                        ),
                    ],
                ),
            ],
        ),
        "Saved movement completion",
    )


def _factor_departure_budget(tools):
    original = deepcopy(tools["spend_combat_movement"])
    split = next(
        index
        for index, effect in enumerate(original["effects"])
        if effect.get("set", {}).get("field") == "combat.movement_remaining"
    )
    assert original["effects"][split - 1].get("require", {}).get("condition") == (
        "_movement_cost <= combat.movement_remaining"
    )
    check = deepcopy(original)
    check["description"] = (
        "Use the unchanged native movement eligibility and cost prefix without charging."
    )
    check["effects"] = deepcopy(original["effects"][:split])
    check["metadata"] = {"category": "internal", "visible_to_player": False}
    tools[CHECK] = check
    params = {
        parameter["name"]: "{"
        + (
            "_" + parameter["name"] + "_id"
            if parameter.get("resolve") == "entity"
            else parameter["name"]
        )
        + "}"
        for parameter in original["parameters"]
    }
    charge = deepcopy(original)
    charge["effects"] = [{"call": CHECK, "params": params, "export_context": "*"}]
    charge["effects"].extend(deepcopy(original["effects"][split:]))
    assert check["effects"] + charge["effects"][1:] == original["effects"]
    assert charge["parameters"] == original["parameters"]
    assert charge["pre_conditions"] == original["pre_conditions"]
    tools["spend_combat_movement"] = charge

    # The existing qualifier has this exact body, with the same common defaults.
    # Its public bound stays intact while the shared private body accepts a
    # qualified positive segment. The optional known-spell flag is unused here.
    budget = deepcopy(tools[BUDGET])
    assert budget["effects"] == check["effects"]
    assert budget["pre_conditions"] == check["pre_conditions"]
    checked = {parameter["name"]: parameter for parameter in check["parameters"]}
    qualified = {parameter["name"]: parameter for parameter in budget["parameters"]}
    assert set(checked) - set(qualified) == {"known_spell_effect"}
    assert not set(qualified) - set(checked)
    assert all(checked[name] == parameter for name, parameter in qualified.items())
    assert checked["known_spell_effect"]["default"] is False
    assert "known_spell_effect" not in json.dumps(check["effects"] + check["pre_conditions"])
    budget["effects"] = [{
        "call": CHECK,
        "params": {name: params[name] for name in qualified},
        "export_context": "*",
    }]
    assert budget["parameters"] == tools[BUDGET]["parameters"]
    assert budget["pre_conditions"] == tools[BUDGET]["pre_conditions"]
    tools[BUDGET] = budget
    next(parameter for parameter in check["parameters"]
         if parameter["name"] == "distance")["minimum"] = 0

    fraction_parameters = deepcopy(original["parameters"])
    next(parameter for parameter in fraction_parameters
         if parameter["name"] == "distance")["minimum"] = 0
    fraction = _tool(
        "Charge an actual qualified positive coordinate prefix through the native owner.",
        fraction_parameters,
        [
            _require(
                GATE + " == _actor_state_id and distance > 0 and distance < 0.5",
                "Only this fully qualified actor's positive fractional segment is permitted.",
            ),
            *deepcopy(charge["effects"]),
        ],
    )
    fraction["metadata"] = {"category": "internal", "visible_to_player": False}
    tools[FRACTION] = fraction


def _bind_fractional_segments(tools):
    from .srd_conjured_presences import _lists

    segment = deepcopy(tools[SEGMENT])
    matches = [(sequence, index, effect)
               for sequence in _lists(segment["effects"])
               for index, effect in enumerate(sequence)
               if effect.get("call") == "spend_combat_movement"]
    assert len(matches) == 1, "Keep the actual native segment's one charge owner"
    sequence, index, original = matches[0]
    fractional = deepcopy(original)
    fractional["call"] = FRACTION
    sequence[index] = {
        "condition": "lasting_state.coordinate_fractional_prefix_actor == _actor_state_id "
                     "and _route_cost > 0 and _route_cost < 0.5",
        "on_true": [fractional],
        "on_false": [deepcopy(original)],
    }
    assert fractional["params"] == original["params"]
    tools[SEGMENT] = segment

    route = deepcopy(tools[ROUTE])
    matches = [(sequence, index, effect)
               for sequence in _lists(route["effects"])
               for index, effect in enumerate(sequence)
               if effect.get("call") == BUDGET]
    assert len(matches) == 1
    sequence, index, original = matches[0]
    remaining = deepcopy(original)
    remaining["call"] = CHECK
    sequence[index] = {
        "condition": "lasting_state.pending_srd_movement != null and "
                     "lasting_state.pending_srd_movement.full_intent_validated == true and "
                     "lasting_state.pending_srd_movement.stage == 'resuming' and "
                     "lasting_state.pending_srd_movement.actor == _actor_state_id and "
                     "lasting_state.pending_srd_movement.turn_sequence == combat.turn_sequence",
        "on_true": [remaining],
        "on_false": [deepcopy(original)],
    }
    assert remaining["params"] == original["params"]
    assert route["parameters"] == tools[ROUTE]["parameters"]
    assert route["pre_conditions"] == tools[ROUTE]["pre_conditions"]
    tools[ROUTE] = route



def _bind_opportunity_damage(tool):
    """Resolve the native parameter template once while retaining concrete overrides."""
    matches = []

    def visit(value):
        if isinstance(value, list):
            for index, effect in enumerate(value):
                if (
                    isinstance(effect, dict)
                    and effect.get("store_as") == "_damage_roll"
                    and effect.get("dice", {}).get("expression")
                    == "{_reaction_damage_expression}"
                ):
                    assert effect["dice"]["critical"] == "_attack_outcome == 'critical_hit'"
                    concrete = deepcopy(effect)
                    parameterized = deepcopy(effect)
                    parameterized["dice"]["expression"] = "{damage_dice_count}d{damage_die_size}"
                    value[index] = {
                        "condition": (
                            "_reaction_damage_expression == '{damage_dice_count}d{damage_die_size}'"
                        ),
                        "on_true": [parameterized],
                        "on_false": [concrete],
                    }
                    matches.append(index)
                else:
                    visit(effect)
        elif isinstance(value, dict):
            for child in value.values():
                visit(child)

    visit(tool["effects"])
    assert len(matches) == 1, matches


def bind_opportunity_movement(runtime, record):
    """Keep spell bodies and original reaction eligibility/roll/cost owners intact."""
    tools = runtime["tools"]
    if FIND in tools:
        raise ValueError("Coordinate opportunity movement is already bound")
    original = deepcopy(tools[ROOT])
    owner = deepcopy(original)
    owner["metadata"] = {"category": "internal", "visible_to_player": False}
    tools[OWNER] = owner
    _factor_departure_budget(tools)
    _bind_fractional_segments(tools)
    tools[REACTOR] = _reactor()
    tools[FIND] = _finder()
    root = deepcopy(original)
    params = {
        p["name"]: "{"
        + ("_" + p["name"] + "_id" if p.get("resolve") == "entity" else p["name"])
        + "}"
        for p in original["parameters"]
    }
    params["destination"] = "{_coordinate_move_destination}"
    saved_parameters = {
        p["name"]: ("_" + p["name"] + "_id" if p.get("resolve") == "entity" else p["name"])
        for p in original["parameters"]
    }
    saved_parameters["destination"] = "_coordinate_original_destination"
    root["effects"] = [
        _require(
            "combat.opportunity_attack_pending != true",
            "Resolve the pending native Reaction before moving.",
        ),
        _require(
            PENDING + " == null or " + PENDING + ".stage == 'resuming'",
            "Complete or cancel the saved coordinate movement first.",
        ),
        _set("_coordinate_original_destination", "destination"),
        _set("_coordinate_split_resume", PENDING + " != null"),
        _call(
            FIND,
            {"actor_state": "{_actor_state_id}", "destination": "{destination}"},
            (
                "_coordinate_candidates",
                "_coordinate_move_destination",
                "_coordinate_ignored",
                "_coordinate_first_exit",
                "_coordinate_move_origin",
                "_coordinate_requested_distance",
            ),
        ),
        _when(
            "len(_coordinate_candidates) > 0 or _coordinate_split_resume",
            [_call(ROUTE, {
                parameter["name"]: "{"
                + ("_actor_state_id" if parameter["name"] == "actor_state"
                   else "_lasting_state_id" if parameter["name"] == "lasting_state"
                   else "_coordinate_original_destination" if parameter["name"] == "destination"
                   else parameter["name"]) + "}"
                for parameter in tools[ROUTE]["parameters"]
            })],
        ),
        _set(
            "_coordinate_movement_budget_total",
            "combat.movement_remaining + combat.movement_spent",
        ),
        _set(GATE, None),
        _when(
            "(len(_coordinate_candidates) > 0 or _coordinate_split_resume) and "
            "_coordinate_first_exit > 0 and "
            "_coordinate_requested_distance * _coordinate_first_exit < 0.5",
            [_set(GATE, "_actor_state_id")],
        ),
        _when(
            "_coordinate_first_exit > 0",
            [
                {"call": OWNER, "params": params, "export_context": "*"},
            ],
        ),
        _when(
            "_coordinate_first_exit == 0",
            [
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{_actor_state_id}"},
                    POSITION_EXPORTS,
                ),
                _call(
                    "srd_information_movement_cost",
                    {
                        "start": "{_coordinate_move_origin}",
                        "end": "{_coordinate_original_destination}",
                        "base_difficult": "{difficult_terrain}",
                        "plane": "{_info_scene_entity.metadata.plane}",
                    },
                    ("_route_cost", "_route_distance"),
                ),
                _when(
                    "actor_state.spell_freedom_of_movement == true",
                    [
                        _set("_route_cost", "_route_distance"),
                    ],
                ),
                _call(
                    CHECK,
                    {
                        "actor": "{_actor_state_id}",
                        "actor_state": "{_actor_state_id}",
                        "distance": "{_route_cost}",
                        "difficult_terrain": False,
                        "movement_kind": "{mode}",
                        "known_spell_effect": "{known_spell_effect}",
                    },
                    ("_movement_cost",),
                ),
                _set("_scene_movement_actor_name", "actor_state.name"),
                _set("_scene_movement_distance", 0),
                _set("_scene_movement_cost", 0),
                _set("_scene_actor_position", "_coordinate_move_origin"),
            ],
        ),
        _set(GATE, None),
        # The native owner retains all legality checks, cost and budget refreshes.
        # Derive the split route's remainder from its actual spending only when
        # that owner has retained the exact original total allowance.
        _when(
            "(len(_coordinate_candidates) > 0 or _coordinate_split_resume) and "
            "combat.movement_remaining + combat.movement_spent == "
            "_coordinate_movement_budget_total",
            [
                _set(
                    "combat.movement_remaining",
                    "_coordinate_movement_budget_total - combat.movement_spent",
                ),
            ],
        ),
        _set("_movement_remaining_after", "combat.movement_remaining"),
        _when(
            "len(_coordinate_candidates) > 0",
            [
                _set(
                    PENDING,
                    {
                        "actor": "_actor_state_id",
                        "destination": "_coordinate_original_destination",
                        "parameters": saved_parameters,
                        "candidates": "_coordinate_candidates",
                        "ignored": "_coordinate_ignored",
                        "turn_sequence": "combat.turn_sequence",
                        "movement_budget_total": (
                            "combat.movement_remaining + combat.movement_spent"
                        ),
                        "full_intent_validated": True,
                        "stage": "'selection'",
                        "selected_reactor": None,
                    },
                ),
                _set("combat.opportunity_attack_pending", True),
                _set(
                    "combat.pending_reaction",
                    {
                        "kind": "'movement_opportunity_selection'",
                        "provoking_entity": "_actor_state_id",
                    },
                ),
            ],
        ),
        _when("len(_coordinate_candidates) == 0", [_set(PENDING, None)]),
    ]
    tools[ROOT] = root
    tools[PREPARE] = _prepare(tools["trigger_opportunity_attack"])
    tools[COMPLETE] = _complete(original)
    for name in ("resolve_opportunity_attack", "resolve_unarmed_opportunity_attack"):
        tools[name] = deepcopy(tools[name])
        if name == "resolve_opportunity_attack":
            _bind_opportunity_damage(tools[name])
        tools[name]["effects"].insert(
            0,
            _when(
                PENDING
                + " != null and "
                + PENDING
                + ".stage == 'attack' and "
                + PENDING
                + ".selected_reactor == _actor_state_id",
                [
                    _set(PENDING + ".stage", "'resolving'"),
                ],
            ),
        )
        tools[name]["effects"].append(
            _when(
                PENDING
                + " != null and "
                + PENDING
                + ".stage == 'resolving' and "
                + PENDING
                + ".selected_reactor == _actor_state_id",
                [
                    _set(PENDING + ".stage", "'resume'"),
                ],
            )
        )
        tools[name].setdefault("metadata", {})["model_behavior"] = "required_engine_transition"
        declared = next(row for row in record["tools"] if row["name"] == name)
        declared["state_predicates"].append(
            {
                "namespace": record["id"],
                "key": "combat.pending_reaction.kind",
                "equals": "opportunity_attack",
            }
        )
    tools["require_srd_pending_window_closed"] = deepcopy(
        tools["require_srd_pending_window_closed"]
    )
    tools["require_srd_pending_window_closed"]["effects"].append(
        _require(
            PENDING
            + " == null or "
            + PENDING
            + ".stage == 'resuming' or "
            + PENDING
            + ".stage == 'preparing' or "
            + PENDING
            + ".stage == 'resolving'",
            "Resolve the saved coordinate movement Reaction and completion first.",
        )
    )
    controlled = {
        action["intent"]
        for surface in record["ui_surfaces"]
        for action in surface.get("actions", [])
    }
    for name in controlled - {ROOT}:
        tools[name] = deepcopy(tools[name])
        guard = _tool(
            "Bind the saved movement precondition to its actual state entity.",
            tools[name]["parameters"],
            [_require(PENDING + " == null", "Saved movement is pending.")],
        )
        tools[name].setdefault("pre_conditions", []).append(
            {
                "condition": guard["effects"][0]["require"]["condition"],
                "error": "Complete or cancel the saved movement before another player action.",
            }
        )
    from .build_srd_5_2_1_spells import _tool_schema

    for name in (
        ROOT,
        "resolve_opportunity_attack",
        "resolve_unarmed_opportunity_attack",
        "require_srd_pending_window_closed",
    ):
        tool = tools[name]
        tool["effects"] = _tool(tool.get("description", name), tool["parameters"], tool["effects"])[
            "effects"
        ]
    for declared in record["tools"]:
        if declared["name"] in controlled:
            schema = _tool_schema(tools[declared["name"]])
            if "lasting_state" in schema["properties"]:
                declared["input_schema"]["properties"]["lasting_state"] = schema["properties"][
                    "lasting_state"
                ]

    for name, stage in ((PREPARE, "selection"), (COMPLETE, "resume")):
        record["tools"].append(
            {
                "name": name,
                "capability": "dnd.srd.5.2.1.action-economy",
                "input_schema": _tool_schema(tools[name]),
                "writers": ["dm"],
                "turn_statuses": ["open"],
                "state_predicates": [
                    {
                        "namespace": record["id"],
                        "key": "state.pending_srd_movement.stage",
                        "equals": stage,
                    }
                ],
            }
        )
    from .srd_lite_attack_concentration_receipts import bind_attack_concentration_receipts

    receipt_changed = bind_attack_concentration_receipts(runtime)
    record["version"] = VERSION
    return sorted(
        {
            ROOT,
            OWNER,
            FIND,
            REACTOR,
            CHECK,
            FRACTION,
            ROUTE,
            BUDGET,
            SEGMENT,
            "spend_combat_movement",
            PREPARE,
            COMPLETE,
            "resolve_opportunity_attack",
            "resolve_unarmed_opportunity_attack",
            "require_srd_pending_window_closed",
            *controlled,
            *receipt_changed,
        }
    )


def link_opportunity_movement_content(declarations):
    """Keep the new native continuations under the existing SRD gameplay provenance."""
    owner = next(
        item
        for item in declarations["inclusions"]
        if "trigger_opportunity_attack" in item["links"]["tools"]
    )
    for name in PUBLIC:
        if name not in owner["links"]["tools"]:
            owner["links"]["tools"].append(name)
    owner["links"]["tools"].sort()
