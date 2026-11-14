"""Time Stop suspends an actual turn and runs extra caster budgets without shared elapsed time."""

import re
from copy import deepcopy

import yaml

if __package__:
    from .srd_divination_spells import _normalize
    from .srd_illusion_spells import PENDING, _distance
    from .srd_scene_relations import POSITION_EXPORTS, _tool_scene
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _RuntimeDumper,
        _set,
        _when,
    )
    from .srd_timed_senses import _loop, _public, _replace_tools
else:
    from srd_divination_spells import _normalize
    from srd_illusion_spells import PENDING, _distance
    from srd_scene_relations import POSITION_EXPORTS, _tool_scene
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _RuntimeDumper,
        _set,
        _when,
    )
    from srd_timed_senses import _loop, _public, _replace_tools

PROFILES = {
    "time_stop": {
        "family": "time_stop",
        "base_level": 9,
        "casting_time": "action",
        "range_kind": "self",
        "range_feet": 0,
        "effect_rounds": 0,
        "concentration": False,
    }
}
PUBLIC_TOOLS = {"begin_srd_time_stop_turn", "end_srd_time_stop_turn"}
ACTIVE = "state.time_stop_source != null and state.time_stop_source.active == true"
ENDING = "state.time_stop_source != null and state.time_stop_source.ending == true"
ACTOR_BUDGET = (
    "bonus_action_available",
    "spell_slot_cast_this_turn",
    "light_extra_attack_used",
    "light_attack_source_weapon",
    "light_attack_source_weapons",
    "light_unrestricted_source_weapons",
    "flurry_attacks_remaining",
)


def validate_profiles(definitions):
    """Keep the Instantaneous level-nine self spell bound to the pinned p169 source."""
    for spell, definition in definitions.items():
        if definition.get("family") == "time_stop" and definition != PROFILES.get(spell):
            raise SystemExit(f"Time Stop source profile differs: {spell}")


def _finish(reason):
    return [
        _set("state.time_stop_source.active", False),
        _set("state.time_stop_source.ending", True),
        _set("state.time_stop_source.end_reason", "'" + reason + "'"),
    ]


def _source_tools(combat_fields):
    """Preserve suspended budgets without rewinding resources or actual extra turn counts."""
    budget = {}
    install = _tool_scene(
        "Install the paid Time Stop and suspend the original remaining budget.",
        [_entity("caster_state")],
        [
            _require("not (" + ACTIVE + ")", "Nested Time Stop sources are not yet supported."),
            _when("state.lasting_tick == null", [_set("state.lasting_tick", 0)]),
            _call(
                "srd_information_scene_entity", {"subject": "{_caster_state_id}"}, POSITION_EXPORTS
            ),
            {"dice": {"expression": "d4", "modifier": 1}, "store_as": "_time_stop_roll"},
            _set("_time_stop_total", "_time_stop_roll.total + _time_stop_roll.modifier"),
            _set(
                "state.time_stop_source",
                {
                    "active": True,
                    "ending": False,
                    "caster": "_caster_state_id",
                    "scene_id": "_info_scene_entity.id",
                    "origin": "_info_position",
                    "plane": "_info_scene_entity.metadata.plane",
                    "started_tick": "state.lasting_tick",
                    "extra_turns": "_time_stop_total",
                    "roll": "_time_stop_roll",
                    "remaining": "_time_stop_total",
                    "turn_running": False,
                    "spell_action_active": False,
                    "turns_started": 0,
                    "end_reason": "''",
                    "suspended_combat": "combat",
                    "suspended_actor_budget": budget,
                },
            ),
            *[
                {
                    "condition": "caster_state." + field + " != null",
                    "on_true": [
                        _set(
                            "state.time_stop_source.suspended_actor_budget." + field,
                            "caster_state." + field,
                        )
                    ],
                    "on_false": [
                        _set("state.time_stop_source.suspended_actor_budget." + field, None)
                    ],
                }
                for field in ACTOR_BUDGET
            ],
            _set("combat.current_actor", "_caster_state_id"),
            _set("combat.active_actor", None),
            _set("combat.turn_active", False),
            _set("combat.action_available", False),
            _set("combat.attack_available", False),
            _set("combat.movement_remaining", 0),
            _set("combat.phase", "'time_stop_awaiting_turn'"),
            _set(
                "_time_stop_summary_rows",
                [
                    {"label": "'Extra caster turns'", "value": "_time_stop_total"},
                    {"label": "'Other creatures'", "value": "'Time frozen'"},
                ],
            ),
            _set(
                "_time_stop_detail_rows",
                [
                    {"label": "'Time Stop roll'", "value": "_time_stop_roll"},
                    {"label": "'Cast origin'", "value": "state.time_stop_source.origin"},
                ],
            ),
        ],
    )
    restore = []
    for field in sorted(combat_fields - {"turn_sequence"}):
        restore.append(
            {
                "condition": "state.time_stop_source.suspended_combat." + field + " != null",
                "on_true": [
                    _set("combat." + field, "state.time_stop_source.suspended_combat." + field)
                ],
                "on_false": [_set("combat." + field, None)],
            }
        )
    for field in ACTOR_BUDGET:
        restore.append(
            {
                "condition": "state.time_stop_source.suspended_actor_budget." + field + " != null",
                "on_true": [
                    _set(
                        "caster_state." + field,
                        "state.time_stop_source.suspended_actor_budget." + field,
                    )
                ],
                "on_false": [_set("caster_state." + field, None)],
            }
        )
    restore += [
        _when(
            "state.time_stop_source.suspended_combat.turn_active == true",
            [
                _set("combat.active_actor_at_zero_hp", "caster_state.hp == 0"),
                _set("combat.active_actor_stable", "caster_state.stable == true"),
                _set("combat.active_actor_spell_slow", "caster_state.spell_slow == true"),
                _set(
                    "combat.active_actor_form_wind_only_actions",
                    "caster_state.form_wind_only_actions == true",
                ),
                _set(
                    "combat.active_actor_form_restricted",
                    "caster_state.form_statistics_replaced == true and "
                    "caster_state.form_can_cast != true",
                ),
                _when(
                    "caster_state.spell_haste != true",
                    [
                        _set("combat.haste_action_available", False),
                        _set("combat.haste_action_selected", False),
                        _set("combat.haste_action_granted", False),
                    ],
                ),
                _set(
                    "combat.active_actor_can_act",
                    "caster_state.is_alive == true and "
                    "caster_state.hp > 0 and not ('incapacitated' in caster_state.conditions or "
                    "'paralyzed' in caster_state.conditions or "
                    "'petrified' in caster_state.conditions "
                    "or 'stunned' in caster_state.conditions or "
                    "'unconscious' in caster_state.conditions)",
                ),
                _call("refresh_srd_movement_budget", {"actor_state": "{_caster_state_id}"}),
                _call("refresh_srd_attack_availability", {}),
            ],
        ),
        _set("state.time_stop_source.ending", False),
        _set("state.time_stop_source.turn_running", False),
    ]
    restore_tool = _tool_scene(
        "Resume the saved original budget after the final actual extra action completes.",
        [_entity("caster_state")],
        [_when(ENDING, restore)],
    )
    finalize = _tool_scene(
        "Finalize source retirement after the enclosing operation, never mid-packet.",
        [],
        [
            _when(
                ENDING,
                [
                    _call(
                        "restore_srd_time_stop_budget",
                        {"caster_state": "{state.time_stop_source.caster}"},
                    )
                ],
            )
        ],
    )
    qualify_actor = _tool_scene(
        "Qualify actual reaction/action candidates before a frozen creature gets an offer.",
        [_entity("actor_state")],
        [
            _set("_time_stop_actor_can_act", True),
            _when(
                ACTIVE,
                [
                    _set(
                        "_time_stop_actor_can_act",
                        "_actor_state_id == state.time_stop_source.caster",
                    )
                ],
            ),
        ],
    )
    actor = _tool_scene(
        "Only the exact caster can act while other creatures' time is stopped.",
        [_entity("actor_state")],
        [
            _call(
                "qualify_srd_time_stop_actor",
                {"actor_state": "{_actor_state_id}"},
                ["_time_stop_actor_can_act"],
            ),
            _require(
                "_time_stop_actor_can_act == true",
                "Time Stop freezes this other creature's actions, movement and reactions.",
            ),
        ],
    )
    movement = _tool_scene(
        "Observe the actual caster's saved scene position against the cast origin.",
        [_parameter("subject_id")],
        [
            _set("_time_stop_move_ended", False),
            _when(
                ACTIVE + " and (subject_id == state.time_stop_source.caster or "
                "subject_id == state.time_stop_source.scene_id)",
                [
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{subject_id}"},
                        POSITION_EXPORTS,
                    ),
                    _distance(
                        "{state.time_stop_source.origin}", "{_info_position}", "_time_stop_distance"
                    ),
                    _when(
                        "_info_scene_entity.metadata.plane != state.time_stop_source.plane or "
                        "_time_stop_distance > 1000",
                        _finish("departure") + [_set("_time_stop_move_ended", True)],
                    ),
                ],
            ),
        ],
    )
    creature = _tool_scene(
        "End only for an actual caster-owned effect on a different creature.",
        [_entity("target_state"), _entity("source_state")],
        [
            _set("_time_stop_effect_ended", False),
            _when(
                ACTIVE + " and _source_state_id == state.time_stop_source.caster and "
                "_target_state_id != state.time_stop_source.caster",
                _finish("other_creature") + [_set("_time_stop_effect_ended", True)],
            ),
        ],
    )
    obj = _tool_scene(
        "Qualify an affected worn/carried object through its actual admitted holder.",
        [_parameter("subject_id"), _entity("source_state")],
        [
            _set("_time_stop_effect_ended", False),
            _when(
                ACTIVE + " and _source_state_id == state.time_stop_source.caster",
                [
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{subject_id}"},
                        POSITION_EXPORTS,
                    ),
                    _set("_time_stop_object", "_info_scene_entity"),
                    _set("_time_stop_holders", []),
                    *[
                        _when(
                            "_time_stop_object.metadata." + field + " != null",
                            [
                                {
                                    "list_push": {
                                        "list": "_time_stop_holders",
                                        "value": "_time_stop_object.metadata." + field,
                                    },
                                }
                            ],
                        )
                        for field in ("worn_by", "carried_by")
                    ],
                    _loop(
                        "_time_stop_holders",
                        "_time_stop_holder",
                        [
                            _when(
                                "_time_stop_holder != null and _time_stop_holder != ''",
                                [
                                    _call(
                                        "srd_information_scene_entity",
                                        {"subject": "{_time_stop_holder}"},
                                        POSITION_EXPORTS,
                                    ),
                                    _when(
                                        "_info_scene_entity.id != state.time_stop_source.scene_id",
                                        _finish("other_held_object")
                                        + [_set("_time_stop_effect_ended", True)],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    begin_spell = _tool_scene(
        "Attribute only an actual paid caster spell resolution to the extra turn action.",
        [_entity("caster_state")],
        [_call("begin_srd_time_stop_action_effect", {"actor_state": "{_caster_state_id}"})],
    )
    begin_action = _tool_scene(
        "Attribute an authentic action resolution to the actual Time Stop caster.",
        [_entity("actor_state")],
        [
            _when(
                ACTIVE + " and _actor_state_id == state.time_stop_source.caster",
                [_set("state.time_stop_source.spell_action_active", True)],
            )
        ],
    )
    return {
        "cast_srd_time_stop": install,
        "restore_srd_time_stop_budget": restore_tool,
        "finalize_srd_time_stop": finalize,
        "check_srd_time_stop_actor": actor,
        "qualify_srd_time_stop_actor": qualify_actor,
        "observe_srd_time_stop_movement": movement,
        "observe_srd_time_stop_creature_effect": creature,
        "observe_srd_time_stop_object_effect": obj,
        "begin_srd_time_stop_spell_effect": begin_spell,
        "begin_srd_time_stop_action_effect": begin_action,
    }


def _turn_tools():
    """Use genuine start/end lifecycle owners while leaving global time and initiative fixed."""
    actor = _entity("caster_state")
    actor["from_state"] = "state.time_stop_source.caster"
    begin = _tool_scene(
        "Begin the next real extra caster turn; other creatures and shared time stay frozen.",
        [actor],
        [
            _require(PENDING, "Resolve the pending action before starting a Time Stop turn."),
            _require(
                ACTIVE + " and state.time_stop_source.turn_running != true",
                "No extra Time Stop turn is awaiting its start.",
            ),
            _require(
                "state.time_stop_source.remaining > 0 and combat.turn_active != true",
                "End the current extra turn first.",
            ),
            _set("state.time_stop_source.turn_running", True),
            _set(
                "state.time_stop_source.turns_started", "state.time_stop_source.turns_started + 1"
            ),
            _call("begin_combat_turn", {"actor": "{_caster_state_id}"}),
            _call("finalize_srd_time_stop", {}),
            _set("_time_stop_turns_left", "state.time_stop_source.remaining"),
        ],
    )
    ending = _tool_scene(
        "End the active extra caster turn through its genuine end-turn lifecycle.",
        [deepcopy(actor)],
        [
            _require(
                ACTIVE + " and state.time_stop_source.turn_running == true",
                "No extra Time Stop turn is active.",
            ),
            _call("end_combat_turn", {"actor": "{_caster_state_id}"}),
            _set("_time_stop_turns_left", "state.time_stop_source.remaining"),
            _set("_time_stop_active", "state.time_stop_source.active"),
        ],
    )
    return {
        "begin_srd_time_stop_turn": _public(
            begin,
            "combat",
            "Time Stop turn begins",
            [("Extra turns remaining", "_time_stop_turns_left")],
        ),
        "end_srd_time_stop_turn": _public(
            ending,
            "combat",
            "Time Stop turn ends",
            [
                ("Extra turns remaining", "_time_stop_turns_left"),
                ("Time stopped", "_time_stop_active"),
            ],
        ),
    }


def _walk(effects):
    for effect in effects:
        yield effect
        for key in ("on_true", "on_false"):
            yield from _walk(effect.get(key, []))
        if "for_each" in effect:
            yield from _walk(effect["for_each"].get("do", []))


def _bind_cast(tools, changed):
    """Replace one owned dispatch, preserving existing final Slow and pending-loss guards."""
    cast = deepcopy(tools["cast_spell"])
    branch = _when(
        "_spell_definition.family == 'time_stop'",
        [
            _call(
                "cast_srd_time_stop",
                {"caster_state": "{_caster_state_id}"},
                ["_time_stop_summary_rows", "_time_stop_detail_rows"],
            ),
            _loop(
                "_time_stop_summary_rows",
                "_time_stop_row",
                [{"list_push": {"list": "_spell_summary_rows", "value": "_time_stop_row"}}],
            ),
            _loop(
                "_time_stop_detail_rows",
                "_time_stop_row",
                [{"list_push": {"list": "_spell_detail_rows", "value": "_time_stop_row"}}],
            ),
        ],
    )
    found = False
    for effect in _walk(cast["effects"]):
        if "_spell_definition.family == 'time_stop'" in effect.get("condition", ""):
            branch["condition"] = effect["condition"]
            effect.clear()
            effect.update(deepcopy(branch))
            found = True

    def insert(effects):
        positions = [
            i
            for i, e in enumerate(effects)
            if "_spell_definition.family ==" in e.get("condition", "")
        ]
        if positions:
            effects.insert(positions[-1] + 1, deepcopy(branch))
            return True
        return any(insert(e[k]) for e in effects for k in ("on_true", "on_false") if k in e)

    if not found and not insert(cast["effects"]):
        raise ValueError("Actual paid spell dispatch missing")
    changed["cast_spell"] = cast


def _append_owned(tool, marker, effect):
    """Regenerate only one owned hook in place, leaving peer effects and order intact."""
    tool["effects"] = [e for e in tool["effects"] if marker not in str(e)]
    tool["effects"].append(effect)


def _bind_spell_attribution(tools, changed):
    """Mark the genuine paid resolution, not an older source's unrelated later packet."""
    cast = deepcopy(changed.get("cast_spell", tools["cast_spell"]))

    def insert(effects):
        effects[:] = [e for e in effects if e.get("call") != "begin_srd_time_stop_spell_effect"]
        positions = [
            i
            for i, e in enumerate(effects)
            if "_spell_definition.family ==" in e.get("condition", "")
        ]
        if positions:
            effects.insert(
                positions[0],
                _call("begin_srd_time_stop_spell_effect", {"caster_state": "{_caster_state_id}"}),
            )
            return True
        return any(insert(e[k]) for e in effects for k in ("on_true", "on_false") if k in e)

    if not insert(cast["effects"]):
        raise ValueError("Actual paid spell dispatch missing for Time Stop attribution")
    _append_owned(
        cast,
        "state.time_stop_source.spell_action_active",
        _when(
            "state.time_stop_source != null",
            [_set("state.time_stop_source.spell_action_active", False)],
        ),
    )
    changed["cast_spell"] = cast


def _bind_consumers(tools, changed):
    """Gate actual time/action owners and observe actual effect producer boundaries."""
    _bind_spell_attribution(tools, changed)
    clock = deepcopy(tools["advance_srd_lasting_clock"])
    guard = _require(
        "not (" + ACTIVE + ")", "Other creatures' time cannot advance during Time Stop."
    )
    clock["effects"] = [
        e for e in clock["effects"] if "time cannot advance during Time Stop" not in str(e)
    ]
    clock["effects"].insert(0, guard)
    changed["advance_srd_lasting_clock"] = clock
    for name in (
        "advance_combat_encounter_turn",
        "advance_group_combat_encounter_turn",
        "advance_srd_lasting_time",
        "sync_srd_group_lasting_clock",
    ):
        tool = deepcopy(tools[name])
        tool["pre_conditions"] = [
            p for p in tool.get("pre_conditions", []) if "Time Stop" not in p.get("error", "")
        ]
        tool["pre_conditions"].append(
            {
                "condition": "not (" + ACTIVE + ")",
                "error": "Finish Time Stop turns before ordinary initiative or time advances.",
            }
        )
        changed[name] = tool
    for name, parameter in (
        ("check_srd_active_control_action", "actor_state"),
        ("consume_srd_bonus_action", "actor_state"),
        ("consume_srd_reaction", "actor_state"),
    ):
        tool = deepcopy(tools[name])
        hook = _when(
            ACTIVE, [_call("check_srd_time_stop_actor", {"actor_state": "{_" + parameter + "_id}"})]
        )
        tool["effects"] = [e for e in tool["effects"] if "check_srd_time_stop_actor" not in str(e)]
        tool["effects"].insert(0, hook)
        changed[name] = tool
    start = deepcopy(tools["begin_combat_turn"])
    start["pre_conditions"] = [
        p for p in start["pre_conditions"] if "Time Stop" not in p.get("error", "")
    ]
    start["pre_conditions"].append(
        {
            "condition": "not (" + ACTIVE + ") or "
            "(state.time_stop_source.turn_running == true and "
            "_actor_id == state.time_stop_source.caster)",
            "error": "Only the bound Time Stop caster can begin an extra turn.",
        }
    )
    changed["begin_combat_turn"] = start
    end = deepcopy(tools["end_combat_turn"])
    _append_owned(
        end,
        "state.time_stop_source.remaining",
        _when(
            ACTIVE + " and state.time_stop_source.turn_running == true and "
            "_actor_id == state.time_stop_source.caster",
            [
                _set("state.time_stop_source.turn_running", False),
                _set("state.time_stop_source.remaining", "state.time_stop_source.remaining - 1"),
                _when("state.time_stop_source.remaining == 0", _finish("extra_turns_complete")),
                _when(
                    "state.time_stop_source.active == true",
                    [_set("combat.phase", "'time_stop_awaiting_turn'")],
                ),
            ],
        ),
    )
    _append_owned(end, "finalize_srd_time_stop", _call("finalize_srd_time_stop", {}))
    changed["end_combat_turn"] = end
    # An exploration cast inside extra turns must use the genuine active-turn cast owner.
    outside = deepcopy(tools["cast_spell_outside_combat"])
    outside["pre_conditions"] = [
        p for p in outside["pre_conditions"] if "Time Stop" not in p.get("error", "")
    ]
    outside["pre_conditions"].append(
        {
            "condition": "not (" + ACTIVE + ")",
            "error": "Use the active caster-turn spell action during Time Stop.",
        }
    )
    changed["cast_spell_outside_combat"] = outside
    for name, actor_parameter in (
        ("resolve_combat_attack", "actor_state"),
        ("resolve_combat_attack_followup", "actor_state"),
        ("resolve_adjudicated_creature_attack", "actor"),
        ("attack_srd_transformed_form", "actor_state"),
        ("attack_srd_altered_natural_weapon", "actor_state"),
        ("resolve_opportunity_attack", "actor_state"),
        ("resolve_unarmed_opportunity_attack", "actor_state"),
    ):
        if name not in tools:
            continue
        tool = deepcopy(changed.get(name, tools[name]))
        if not any(
            p["name"] == actor_parameter and p.get("resolve") == "entity"
            for p in tool["parameters"]
        ):
            raise ValueError("Time Stop attack actor contract changed: " + name)
        tool["effects"] = [
            e for e in tool["effects"] if e.get("call") != "begin_srd_time_stop_action_effect"
        ]
        tool["effects"].insert(
            0,
            _call(
                "begin_srd_time_stop_action_effect",
                {"actor_state": "{_" + actor_parameter + "_id}"},
            ),
        )
        changed[name] = tool
    for name in ("move_srd_scene_creature_node", "move_srd_travel_subject"):
        tool = deepcopy(tools[name])
        subject = "{_actor_state_id}" if name == "move_srd_scene_creature_node" else "{subject}"
        if name == "move_srd_scene_creature_node":
            tool["effects"] = [
                e for e in tool["effects"] if "check_srd_time_stop_actor" not in str(e)
            ]
            tool["effects"].insert(
                0,
                _when(
                    ACTIVE,
                    [
                        _call("check_srd_time_stop_actor", {"actor_state": "{_actor_state_id}"}),
                    ],
                ),
            )
        _append_owned(
            tool,
            "observe_srd_time_stop_movement",
            _when(ACTIVE, [_call("observe_srd_time_stop_movement", {"subject_id": subject})]),
        )
        changed[name] = tool
    for name in (
        "apply_srd_damage",
        "apply_srd_composite_damage",
        "apply_srd_qualifying_damage",
        "apply_srd_qualifying_composite_damage",
        "apply_srd_condition",
    ):
        if name not in tools:
            continue
        tool = deepcopy(tools[name])
        params = {p["name"] for p in tool["parameters"]}
        if "source_state_id" not in params:
            continue
        consequence = "" if name == "apply_srd_condition" else " and _effective_damage > 0"
        _append_owned(
            tool,
            "observe_srd_time_stop_creature_effect",
            _when(
                ACTIVE + " and state.time_stop_source.spell_action_active == true and "
                "source_state_id != null and len(source_state_id) > 0" + consequence,
                [
                    _call(
                        "observe_srd_time_stop_creature_effect",
                        {"target_state": "{target}", "source_state": "{source_state_id}"},
                    )
                ],
            ),
        )
        changed[name] = tool
    # These exact installer contracts create an actual grant on the bound creature.
    for name in (
        "install_srd_guidance_concentration",
        "install_srd_concentration_armor_class_bonus",
        "install_srd_concentration_damage_resistance",
        "install_srd_turn_effect",
        "install_srd_timed_condition_source",
        "install_srd_timed_hit_point_bonus",
        "install_srd_timed_armor_class_floor",
        "install_srd_timed_actor_mobility",
        "install_srd_timed_speed_bonus",
        "install_srd_timed_death_prevention",
        "install_srd_timed_actor_defense",
        "install_srd_timed_senses",
        "install_srd_control_behavior_source",
        "install_srd_actor_spell_modifier",
        "install_srd_transformation_source",
        "install_srd_befuddlement_source",
        "install_srd_environment_deafened",
        "install_srd_illumination_invisible",
        "install_srd_environment_restrained",
        "install_srd_prismatic_consequence",
    ):
        if name not in tools:
            continue
        tool = deepcopy(changed.get(name, tools[name]))
        if not any(
            p["name"] == "target_state" and p.get("resolve") == "entity" for p in tool["parameters"]
        ):
            raise ValueError("Time Stop target installer contract changed: " + name)
        _append_owned(
            tool,
            "observe_srd_time_stop_creature_effect",
            _when(
                ACTIVE + " and state.time_stop_source.spell_action_active == true",
                [
                    _call(
                        "observe_srd_time_stop_creature_effect",
                        {
                            "target_state": "{_target_state_id}",
                            "source_state": "{state.time_stop_source.caster}",
                        },
                    )
                ],
            ),
        )
        changed[name] = tool
    for name, target_parameter in (
        ("restore_hit_points", "target"),
        ("cast_spell_healing", "target"),
        ("restore_shared_spell_healing_target", "target"),
        ("restore_srd_healing_pool_target", "target"),
        ("restore_srd_returned_hit_points", "target_state"),
    ):
        if name not in tools:
            continue
        heal = deepcopy(tools[name])
        if not any(
            p["name"] == target_parameter
            and (
                p.get("resolve") == "entity"
                or (name == "restore_hit_points" and p.get("resolve") is None)
            )
            for p in heal["parameters"]
        ):
            raise ValueError("Time Stop healing target contract changed: " + name)
        heal["effects"] = [
            e
            for e in heal["effects"]
            if "_time_stop_healing_before" not in str(e)
            and "observe_srd_time_stop_creature_effect" not in str(e)
        ]
        heal["effects"].insert(0, _set("_time_stop_healing_before", target_parameter + ".hp"))
        heal["effects"].append(
            _when(
                ACTIVE
                + " and state.time_stop_source.spell_action_active == true and "
                + target_parameter
                + ".hp > _time_stop_healing_before",
                [
                    _call(
                        "observe_srd_time_stop_creature_effect",
                        {
                            "target_state": "{" + target_parameter + "}",
                            "source_state": "{state.time_stop_source.caster}",
                        },
                    )
                ],
            )
        )
        changed[name] = heal
    # Do not restore suspended budgets inside nested damage/condition/movement packets.
    for name, original in tools.items():
        acting_operation = any(
            p["name"] in {"actor", "actor_state", "caster_state"}
            and p.get("resolve") in {"entity", "participant"}
            for p in original["parameters"]
        )
        if (
            original.get("metadata", {}).get("category") in {None, "internal"}
            or name in PUBLIC_TOOLS
            or not acting_operation
        ):
            if (
                original.get("metadata", {}).get("category") not in {None, "internal"}
                and not acting_operation
            ):
                tool = changed.get(name, deepcopy(original))
                tool["effects"] = [
                    e
                    for e in tool["effects"]
                    if e.get("call") != "finalize_srd_time_stop"
                    and not (
                        len(e.get("on_true", [])) == 1
                        and e["on_true"][0].get("call") == "finalize_srd_time_stop"
                    )
                ]
                changed[name] = tool
            continue
        tool = changed.get(name, deepcopy(original))
        tool["effects"] = [
            e
            for e in tool["effects"]
            if not any(
                child.get("set", {}).get("field")
                in {
                    "state.time_stop_source.spell_action_active",
                    "time_stop_state.time_stop_source.spell_action_active",
                }
                for child in e.get("on_true", [])
            )
        ]
        tool["effects"].append(
            _when(
                "state.time_stop_source != null",
                [_set("state.time_stop_source.spell_action_active", False)],
            )
        )
        if name not in {
            "advance_srd_lasting_time",
            "advance_combat_encounter_turn",
            "advance_group_combat_encounter_turn",
        }:
            _append_owned(
                tool, "finalize_srd_time_stop", _when(ENDING, [_call("finalize_srd_time_stop", {})])
            )
            changed[name] = tool
    # Existing Action support helpers already know their bound actor; include stopped-time turns
    # when they choose whether to spend a genuine action outside an ordinary encounter.
    for name, original in tools.items():
        tool = changed.get(name, deepcopy(original))
        altered = False
        for effect in _walk(tool["effects"]):
            if effect.get("condition") == "combat.encounter_active == true" and any(
                e.get("call") == "spend_srd_action" for e in _walk(effect.get("on_true", []))
            ):
                effect["condition"] = "combat.encounter_active == true or (" + ACTIVE + ")"
                altered = True
        if altered:
            changed[name] = tool


def extend_runtime(text):
    """Emit the owned temporal family and narrow turn/clock/actual consequence hooks."""
    tools = _load_runtime(text)["tools"]
    fields = {
        e["set"]["field"][7:]
        for t in tools.values()
        for e in _walk(t.get("effects", []))
        if e.get("set", {}).get("field", "").startswith("combat.") and "{" not in e["set"]["field"]
    }
    fields |= {"encounter_active", "turn_active", "current_actor", "active_actor", "phase"}
    changed = {**_source_tools(fields), **_turn_tools()}
    _bind_cast(tools, changed)
    _bind_consumers(tools, changed)
    _bind_state(changed)
    _normalize(changed)
    for name in PUBLIC_TOOLS:
        tool = changed[name]
        node = deepcopy(tool)
        node["metadata"] = {
            "category": "internal",
            "visible_to_player": False,
            "costs_action": False,
        }
        changed[name + "_node"] = node
        tool["parameters"] = [p for p in tool["parameters"] if p["name"] != "lasting_state"]
        exports = [
            row["value"]["path"].removeprefix("context.")
            for row in tool["metadata"]["result_card"]["summary"]
        ]
        tool["effects"] = [
            _call(
                name + "_node",
                {
                    "caster_state": "{_caster_state_id}",
                    "lasting_state": "state",
                },
                exports,
            )
        ]
    text = _replace_tools(text, changed)
    if "      time_stop:" not in text:
        profile = PROFILES["time_stop"]
        properties = {
            key: {
                "type": "boolean"
                if type(value) is bool
                else "integer"
                if type(value) is int
                else "string"
            }
            for key, value in profile.items()
            if key != "family"
        }
        schema = yaml.dump(
            {
                "time_stop": {
                    "type": "object",
                    "properties": properties,
                    "required": list(properties),
                    "additionalProperties": False,
                }
            },
            Dumper=_RuntimeDumper,
            sort_keys=False,
        )
        text = text.replace(
            "      illusion_spell:",
            "".join("      " + line for line in schema.splitlines(True))
            + "\n      illusion_spell:",
            1,
        )
    return text


def bind_runtime_consumers(text):
    """Bind final public cast/action and effect owners before Root regenerates reaction clones."""
    changed = {}
    _bind_consumers(_load_runtime(text)["tools"], changed)
    _bind_state(changed)
    _normalize(changed)
    return _replace_tools(text, changed)


def _bind_state(changed):
    """Existing owners need an explicit hidden state binding for the new source guard."""
    for tool in changed.values():
        used = False

        def bind(value):
            nonlocal used
            if isinstance(value, str):
                updated = re.sub(r"\bstate(?=\.time_stop_source)", "time_stop_state", value)
                used |= updated != value
                return updated
            if isinstance(value, list):
                return [bind(item) for item in value]
            if isinstance(value, dict):
                return {key: bind(item) for key, item in value.items()}
            return value

        tool["effects"] = bind(tool["effects"])
        tool["pre_conditions"] = bind(tool.get("pre_conditions", []))
        if used and not any(p["name"] == "time_stop_state" for p in tool["parameters"]):
            tool["parameters"].append(
                {
                    "name": "time_stop_state",
                    "type": "string",
                    "resolve": "entity",
                    "required": False,
                    "default": "state",
                    "model_hidden": True,
                }
            )
