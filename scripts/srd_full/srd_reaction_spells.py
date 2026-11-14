"""Saved action windows retain source-derived rolls while another creature reacts."""

import re
from copy import deepcopy

import yaml

if __package__:
    from .srd_extended_casting import _render_tool
    from .srd_hellish_rebuke import PROFILE as HELLISH_REBUKE_PROFILE
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
else:
    from srd_extended_casting import _render_tool
    from srd_hellish_rebuke import PROFILE as HELLISH_REBUKE_PROFILE
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


PUBLIC_TOOLS = {
    "begin_srd_hellish_rebuke",
    "begin_srd_spell_action",
    "begin_srd_character_attack",
    "begin_srd_creature_attack",
    "complete_srd_pending_action",
    "cast_srd_reaction_spell",
    "resume_srd_sanctuary_attack",
    "resume_srd_sanctuary_spell",
    "begin_srd_extended_spell_action",
    "continue_srd_extended_spell_action",
    "begin_srd_scene_fall",
}
PROFILES = {
    "hellish_rebuke": HELLISH_REBUKE_PROFILE,
    "counterspell": {
        "family": "reaction_spell",
        "casting_time": "reaction",
        "base_level": 3,
        "range_kind": "distance",
        "range_feet": 60,
        "effect_kind": "counterspell",
        "effect_rounds": 0,
    },
    "shield": {
        "family": "reaction_spell",
        "casting_time": "reaction",
        "base_level": 1,
        "range_kind": "self",
        "range_feet": 0,
        "effect_kind": "shield",
        "effect_rounds": 1,
    },
    "feather_fall": {
        "family": "reaction_spell",
        "casting_time": "reaction",
        "base_level": 1,
        "range_kind": "distance",
        "range_feet": 60,
        "effect_kind": "feather_fall",
        "effect_rounds": 10,
    },
}
SLOT_TURN_AVAILABLE = (
    "caster_state.spell_slot_cast_this_turn != true or "
    "(combat.turn_sequence != null and "
    "caster_state.spell_slot_turn_sequence != combat.turn_sequence)"
)
ATTACK_OWNERS = {
    "resolve_combat_attack": ("begin_srd_character_attack", "actor_state", "character_attack"),
    "resolve_adjudicated_creature_attack": (
        "begin_srd_creature_attack",
        "actor",
        "creature_attack",
    ),
}
LATE_SUPPORT_TOOLS = {
    "handle_srd_freezing_sphere_globe",
    "escape_srd_freezing_sphere_ice",
    "check_srd_simulacrum_character_ability",
    "learn_srd_initial_circle_sigils",
    "record_srd_information_world_facts",
    "prepare_srd_true_polymorph_object",
    "command_srd_true_polymorph_creature",
    "admit_srd_native_wight_rise_language",
    "record_srd_native_wight_body_destruction",
    "escape_srd_earthquake_rubble",
    "choose_srd_storm_lightning_targets",
    "use_srd_conjured_fey",
    "move_srd_celestial_cylinder",
    "swim_srd_tsunami",
    "settle_srd_tsunami_exits",
    "record_srd_earth_material",
    "read_srd_move_earth",
    "choose_srd_move_earth_area",
    "begin_srd_simulacrum_repair",
    "begin_srd_simulacrum_turn",
    "end_srd_simulacrum_turn",
    "read_srd_astral_projection",
    "dismiss_srd_astral_projection",
    "record_srd_reverse_gravity_anchoring",
    "settle_srd_reverse_gravity_returns",
    "choose_srd_control_weather",
    "record_srd_water_body",
    "change_srd_control_water",
    "escape_srd_whirlpool",
    "record_srd_object_attachment",
    "command_srd_animated_objects",
    "begin_srd_animated_object_slam",
    "traverse_srd_gate",
    "look_through_srd_gate",
    "touch_srd_delayed_blast_fireball",
    "throw_srd_delayed_blast_fireball",
    "command_srd_arcane_hand",
    "detonate_srd_delayed_blast_fireball",
    "inspect_srd_gate",
    "test_srd_awakened_intelligence",
    "record_srd_find_path_location",
    "query_srd_find_path",
    "roll_srd_flaming_sphere",
    "use_srd_sunbeam",
    "harvest_srd_enriched_crop",
    "change_srd_gust_direction",
    "record_srd_wind_flame_protection",
    "command_srd_mansion_servant",
    "transfer_srd_attack_mark",
    "record_srd_secret_chest_components",
    "use_srd_secret_chest",
    "record_srd_rope_length",
    "climb_srd_rope_trick",
    "traverse_srd_rope_trick",
    "manipulate_srd_rope_trick_rope",
    "call_srd_lightning_source",
    "use_srd_dragon_breath",
    "operate_srd_mansion_door",
    "mount_srd_phantom_steed",
    "dismount_srd_phantom_steed",
    "dismiss_srd_phantom_steed",
    "handle_srd_phantom_steed_equipment",
    "eat_srd_mansion_banquet",
    "leave_srd_meld_into_stone",
    "read_srd_meld_into_stone",
    "traverse_srd_dwelling_door",
    "use_srd_flame_blade_object",
    "pour_srd_telekinesis_vial",
    "use_srd_flame_blade",
    "evoke_srd_flame_blade",
    "release_srd_flame_blade",
    "record_srd_scene_hand_occupancy",
    "operate_srd_telekinesis_object",
    "record_srd_scene_sight_origin",
    "record_srd_telekinesis_object_size",
    "use_srd_telekinesis",
}
ORDINARY_SUPPORT_TOOLS = LATE_SUPPORT_TOOLS | {
    "escape_srd_guards_wards_web",
    "record_srd_map_time_of_day",
    "use_srd_vampiric_touch",
    "begin_srd_spell_cast",
    "continue_srd_spell_cast",
    "move_srd_scene_creature",
    "operate_srd_information_proxy",
    "communicate_srd_spell",
    "command_srd_plants",
    "dismiss_srd_information_spell",
    "designate_srd_compulsion_direction",
    "issue_srd_dominated_order",
    "resolve_srd_control_order",
    "resolve_srd_control_activity",
    "violate_srd_geas",
    "retarget_srd_eyebite",
    "resolve_srd_affliction_movement",
    "resolve_srd_fear_turn",
    "resolve_srd_confused_turn",
    "record_srd_control_held_items",
    "drop_srd_control_held_items",
    "escape_srd_grapple",
    "escape_srd_magical_restriction",
    "change_srd_transformation",
    "end_srd_animal_shape",
    "attack_srd_transformed_form",
    "record_srd_deceased_body",
    "record_srd_cursed_item",
    "apply_srd_ability_score_reduction",
    "begin_srd_feast_consumption",
    "finish_srd_feast_consumption",
    "use_srd_dispel_evil_and_good",
    "reroll_srd_reincarnate_species",
    "complete_srd_reincarnation",
    "traverse_srd_plant_link",
    "traverse_srd_tree_stride",
    "enter_srd_teleportation_circle",
    "study_srd_circle_sigil",
    "move_srd_dancing_lights",
    "set_srd_illumination_cover",
    "dismiss_srd_illumination_spell",
    "command_srd_spell_force",
    "disperse_srd_fog",
    "prepare_srd_spell_object",
    "hurl_srd_produce_flame",
    "escape_srd_entangle",
    "record_srd_imprisonment_statuette",
    "observe_srd_imprisonment_trigger",
    "dismiss_srd_etherealness",
    "dismiss_srd_spell",
    "attack_srd_altered_natural_weapon",
    "notice_srd_spell_glyph",
    "resolve_srd_spell_trigger",
    "choose_srd_blink_return_point",
    "study_srd_maze_escape",
    "traverse_srd_demiplane_door",
    "choose_srd_demiplane_shunt",
    "record_srd_demiplane_room_knowledge",
    "dodge_srd_familiar",
    "communicate_srd_familiar",
    "issue_srd_bound_order",
    "resolve_srd_binding_return",
    "control_srd_mage_hand",
    "begin_srd_time_stop_turn",
    "end_srd_time_stop_turn",
    "record_srd_life_event",
    "recall_srd_life_event",
    "describe_srd_modified_memory",
    "ask_srd_dead_question",
    "speak_srd_truth_zone",
    "read_srd_truth_zone_outcomes",
    "record_srd_banishment_plane",
    "reheat_srd_metal",
    "command_srd_tiny_hut",
    "roll_srd_resilient_sphere",
    "observe_srd_phantasm",
    "study_srd_phantasm",
    "study_srd_phantasm_creature",
    "damage_srd_phantasm",
    "control_srd_project_image",
    "perceive_srd_project_image",
    "record_srd_object_utility_facts",
    "record_srd_object_holder",
    "record_srd_spell_material_object",
    "use_srd_instant_summons",
    "record_srd_food_and_drink",
    "eat_srd_goodberry",
    "transfer_srd_goodberries",
    "observe_srd_appearance",
    "study_srd_appearance",
    "study_srd_appearance_creature",
    "inspect_srd_appearance",
    "read_srd_illusory_script",
    "dismiss_srd_appearance_spell",
}


def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def validate_profiles(definitions):
    """Check every admitted reaction profile against its pinned source constants."""
    for spell, definition in definitions.items():
        if definition.get("family") == "reaction_spell" and definition != PROFILES.get(spell):
            raise SystemExit(f"Reaction source profile differs: {spell}")


def _casting_budget():
    """Spend a Reaction off turn and enforce slot expenditure on the actual current turn."""
    slot = "slot_resource != 'cantrip' and slot_resource != 'no_slot'"
    return _tool(
        "Spend the source casting action and at most one spell slot on the current turn.",
        [
            _entity("caster_state"),
            _parameter("casting_time", "enum", enum_values=["action", "bonus_action", "reaction"]),
            _parameter(
                "slot_resource",
                "enum",
                enum_values=[
                    "cantrip",
                    *[f"spell_slot_{level}" for level in range(1, 10)],
                    "no_slot",
                ],
            ),
        ],
        [
            _when(
                "combat.turn_sequence != null and "
                "caster_state.spell_slot_turn_sequence != combat.turn_sequence",
                [
                    _set("caster_state.spell_slot_cast_this_turn", False),
                    _set("caster_state.spell_slot_turn_sequence", "combat.turn_sequence"),
                ],
            ),
            _when(
                "casting_time == 'reaction'",
                [
                    _call(
                        "consume_srd_reaction",
                        {
                            "actor_state": "{_caster_state_id}",
                            "lasting_state": "state",
                            "reaction": "spell",
                        },
                    ),
                ],
            ),
            _when(
                "combat.turn_active == true and casting_time == 'action'",
                [
                    _call("spend_srd_action", {"magic_action": True}),
                ],
            ),
            _when(
                "combat.turn_active == true and casting_time == 'bonus_action'",
                [
                    _call(
                        "consume_srd_bonus_action",
                        {
                            "actor_state": "{_caster_state_id}",
                            "bonus_action": "spell",
                        },
                    ),
                ],
            ),
            _when(
                "combat.turn_active == true and " + slot,
                [
                    _set("caster_state.spell_slot_cast_this_turn", True),
                ],
            ),
        ],
        [
            {
                "condition": "casting_time == 'reaction' or combat.encounter_active != true "
                "or combat.turn_active == true",
                "error": "Casting needs an active combat turn.",
            },
            {
                "condition": "casting_time == 'reaction' or combat.turn_active != true "
                "or combat.active_actor == _caster_state_id",
                "error": "Only the active actor can spend a casting action.",
            },
            {
                "condition": "not ('incapacitated' in caster_state.conditions or "
                "'paralyzed' in caster_state.conditions or "
                "'petrified' in caster_state.conditions or "
                "'stunned' in caster_state.conditions or 'unconscious' in caster_state.conditions)",
                "error": "The spellcaster cannot take the action required to cast this spell.",
            },
            {
                "condition": "casting_time == 'reaction' or combat.turn_active != true or "
                "(casting_time == 'action' and combat.action_available == true) or "
                "(casting_time == 'bonus_action' and caster_state.bonus_action_available == true)",
                "error": "The spellcaster has already spent the required combat action budget.",
            },
            {
                "condition": "combat.turn_active != true or not ("
                + slot
                + ") or ("
                + SLOT_TURN_AVAILABLE
                + ")",
                "error": "Only one spell slot can be expended to cast a spell on a turn.",
            },
        ],
    )


def _copy_value(field, expression):
    """Retain actual nulls; the expression runtime treats unresolved string values as literals."""
    return [
        _set(field, None),
        _when(f"{expression} != null", [_set(field, expression)]),
    ]


def _allocate(kind, actor_expression, target_expression):
    return [
        _when("state.pending_srd_actions == null", [_set("state.pending_srd_actions", {})]),
        _when(
            "state.pending_srd_action_stack == null",
            [_set("state.pending_srd_action_stack", [])],
        ),
        _set("_pending_stack", "state.pending_srd_action_stack"),
        _require("len(_pending_stack) < 16", "Complete an existing reaction window first."),
        _when(
            "state.pending_srd_action_sequence == null",
            [
                _set("state.pending_srd_action_sequence", 0),
            ],
        ),
        _set("state.pending_srd_action_sequence", "state.pending_srd_action_sequence + 1"),
        _call(
            "record_srd_result_text",
            {"text": "action-{state.pending_srd_action_sequence}"},
            [
                "_recorded_result_text",
            ],
        ),
        _set("_pending_action_id", "_recorded_result_text"),
        _set(
            "state.pending_srd_actions.{_pending_action_id}",
            {
                "kind": repr(
                    "weapon_attack"
                    if kind
                    in {
                        "character_attack",
                        "creature_attack",
                    }
                    else kind
                ),
                "owner_kind": repr(kind),
                "status": "'open'",
                "stage": "'reaction'",
                "actor_state_id": actor_expression,
                "target_state_id": target_expression,
                "parameters": {},
                "context": {},
                "sanctuary_saves": [],
            },
        ),
        _set("state.pending_srd_actions.{_pending_action_id}.turn_actor", None),
        _when(
            "combat.encounter_active == true",
            [
                _set(
                    "state.pending_srd_actions.{_pending_action_id}.turn_actor",
                    "combat.active_actor",
                ),
            ],
        ),
        {"list_push": {"list": "_pending_stack", "value": "_pending_action_id"}},
        _set("state.pending_srd_action_stack", "_pending_stack"),
    ]


def _save_parameters(parameters):
    effects = []
    for parameter in parameters:
        name = parameter["name"]
        if name == "lasting_state":
            continue
        expression = (
            f"_{name}_id"
            if parameter.get("resolve")
            in {
                "entity",
                "participant",
            }
            else name
        )
        effects += _copy_value(
            f"state.pending_srd_actions.{{_pending_action_id}}.parameters.{name}",
            expression,
        )
    return effects


def _scene_fall_window():
    """Save actual qualified falling bodies in the sole pending Reaction stack."""
    return _tool(
        "Open a Feather Fall Reaction window for actual airborne scene creatures.",
        [
            _parameter(
                "target_states", "array", items={"type": "string"}, min_items=1, max_items=64
            ),
            _parameter(
                "fall_cause", "enum", enum_values=["departure", "support_lost", "flight_ended"]
            ),
        ],
        [
            _require(
                "state.pending_srd_action_stack == null or "
                "len(state.pending_srd_action_stack) == 0",
                "Complete the pending Reaction before another scene fall.",
            ),
            _call(
                "qualify_srd_scene_fall",
                {
                    "target_states": "{target_states}",
                    "fall_cause": "{fall_cause}",
                },
                ["_falling_events", "_falling_targets", "_fall_first_actor"],
            ),
            *_allocate("scene_fall", "_fall_first_actor", "_fall_first_actor"),
            _set(
                "state.pending_srd_actions.{_pending_action_id}.parameters.falling_events",
                "_falling_events",
            ),
            _call("bind_srd_scene_fall_event", {"pending_action_id": "{_pending_action_id}"}),
        ],
    )


def _capture_names(prefix, suffix, metadata):
    """Capture the existing owner's private values needed after its roll boundary."""
    used = set(re.findall(r"\b_[A-Za-z][A-Za-z0-9_]*\b", str(suffix) + str(metadata)))
    used.add("_condition_critical_hit")
    assigned = set()
    for item in _walk(prefix):
        if isinstance(item.get("set"), dict):
            assigned.add(item["set"]["field"].split(".", 1)[0])
        if "store_as" in item:
            assigned.add(item["store_as"])
        if isinstance(item.get("export_context"), dict):
            assigned.update(item["export_context"])
    return sorted(name for name in assigned & used if name.startswith("_"))


def _typed_attack_feedback(owner):
    """Retain actual typed native dice and receiving feedback through saved completion."""
    calls = {effect.get("call") for effect in _walk(owner["effects"])}
    names = []
    if "apply_srd_native_wight_weapon_damage" in calls:
        names.extend(
            [
                "_wight_weapon_necrotic_roll",
                "_wight_weapon_necrotic_amount",
                "_component_1_effective_damage",
                "_component_2_effective_damage",
            ]
        )
    if "apply_srd_native_mummy_fist_damage" in calls:
        names.extend(
            [
                "_mummy_fist_necrotic_roll",
                "_mummy_fist_necrotic_amount",
                "_mummy_rot_source_id",
                "_mummy_fist_dust",
                "_component_1_effective_damage",
                "_component_2_effective_damage",
            ]
        )
    if "apply_srd_insect_poison_jab_damage" in calls:
        names.extend(
            [
                "_insect_poison_damage_roll",
                "_insect_poison_damage_amount",
                "_component_1_effective_damage",
                "_component_2_effective_damage",
            ]
        )
    if "apply_srd_created_ghoul_bite_damage" in calls:
        names.extend(
            [
                "_ghoul_necrotic_damage_roll",
                "_ghoul_necrotic_damage_amount",
                "_ghoul_piercing_damage_amount",
                "_component_1_effective_damage",
                "_component_2_effective_damage",
            ]
        )
    if "resolve_srd_created_ghoul_claw" in calls:
        names.extend(
            [
                "_ghoul_claw_exempt",
                "_ghoul_claw_save_outcome",
                "_ghoul_claw_save_total",
                "_ghoul_claw_save_roll",
                "_ghoul_claw_source_id",
            ]
        )
    return list(dict.fromkeys(names))


def _attack_window(owner, begin_name, actor_name, kind):
    split = (
        next(
            index
            for index, effect in enumerate(owner["effects"])
            if effect.get("set", {}).get("field") == "_critical_hit"
        )
        + 1
    )
    prefix, suffix = deepcopy(owner["effects"][:split]), deepcopy(owner["effects"][split:])
    names = _capture_names(prefix, suffix, owner.get("metadata", {}))
    target_index = next(
        i
        for i, effect in enumerate(prefix)
        if effect.get("call") == "resolve_srd_attack_conditions"
    )
    setup, roll = prefix[:target_index], prefix[target_index:]
    setup_names = _capture_names(setup, [*roll, *suffix], owner.get("metadata", {}))
    setup_names = [
        name for name in setup_names if name not in {"_target_id", "_actor_id", "_actor_state_id"}
    ]
    stage_name = "continue_srd_" + kind + "_targeting"
    save_roll = [
        _set("state.pending_srd_actions.{_pending_action_id}.attack_roll", "_attack_roll"),
        _set(
            "state.pending_srd_actions.{_pending_action_id}.attack_total", "_attack_adjusted_total"
        ),
        _set("state.pending_srd_actions.{_pending_action_id}.attack_outcome", "_attack_outcome"),
        _set("state.pending_srd_actions.{_pending_action_id}.stage", "'reaction'"),
    ]
    for name in names:
        save_roll += _copy_value(
            f"state.pending_srd_actions.{{_pending_action_id}}.context.{name}", name
        )
    stage = deepcopy(owner)
    stage["description"] = "Resolve saved Sanctuary targeting before the owned attack roll."
    stage["parameters"].append(_parameter("pending_action_id"))
    # Costs were already reserved by the initial owner; all other constraints remain live.
    stage["pre_conditions"] = [
        condition
        for condition in owner.get("pre_conditions", [])
        if not any(
            marker in condition["condition"]
            for marker in (
                "action_available",
                "attacks_remaining",
                "bonus_action_available",
                "light_extra_attack",
                "ammunition",
                "resources.darts",
            )
        )
    ]
    stage["effects"] = [
        _set("_pending_action_id", "pending_action_id"),
        _set("_targeting_action", "state.pending_srd_actions[pending_action_id]"),
        _set("_attack_roll", None),
        _set("_attack_adjusted_total", 0),
        _set("_attack_outcome", "'unrolled'"),
    ]
    for name in setup_names:
        stage["effects"] += _copy_value(name, f"_targeting_action.context.{name}")
    if actor_name == "actor_state":
        stage["effects"].append(
            _when(
                "target.has_actor_spell_modifiers == true",
                [
                    _when(
                        "actor_state.saving_throw_modifiers == null",
                        [_set("actor_state.saving_throw_modifiers", {})],
                    ),
                    _set("_pending_wisdom_save", "(actor.data.abilities.wisdom - 10) // 2"),
                    _when(
                        "actor.data.saving_throw_proficiencies != null and "
                        "'wisdom' in actor.data.saving_throw_proficiencies",
                        [
                            _set(
                                "_pending_wisdom_save",
                                "_pending_wisdom_save + 2 + ((actor.data.level - 1) // 4)",
                            )
                        ],
                    ),
                    _set("actor_state.saving_throw_modifiers.wisdom", "_pending_wisdom_save"),
                ],
            )
        )
    actor_id = f"_{actor_name}_id"
    stage["effects"] += [
        _call(
            "check_srd_sanctuary_targeting",
            {
                "actor_state": "{" + actor_id + "}",
                "target_state": "{_target_id}",
                "lasting_state": "state",
                "pending_action_id": "{pending_action_id}",
                "operation_kind": "weapon_attack",
            },
            ["_sanctuary_target_allowed", "_sanctuary_choice_required", "_sanctuary_save"],
        ),
        _when(
            "_sanctuary_target_allowed == true",
            [
                _set(f"{actor_name}.pending_srd_targeting_id", "pending_action_id"),
                *roll,
                _set(f"{actor_name}.pending_srd_targeting_id", None),
                *save_roll,
            ],
        ),
    ]
    stage["metadata"] = {"category": "internal", "visible_to_player": False, "costs_action": False}
    begin = deepcopy(owner)
    begin["description"] = (
        "Reserve the attack cost, resolve targeting, then retain its real roll for reactions."
    )
    begin["effects"] = [
        *setup,
        *_allocate(kind, f"_{actor_name}_id", "_target_id"),
        *_save_parameters(owner["parameters"]),
    ]
    for name in setup_names:
        begin["effects"] += _copy_value(
            f"state.pending_srd_actions.{{_pending_action_id}}.context.{name}",
            name,
        )
    begin["effects"].append(
        _set("_pending_targeting_action", "state.pending_srd_actions[_pending_action_id]")
    )
    begin["effects"].append(
        _call(
            stage_name,
            {
                **{
                    p["name"]: "{_pending_targeting_action.parameters." + p["name"] + "}"
                    for p in owner["parameters"]
                    if p["name"] != "lasting_state"
                },
                "pending_action_id": "{_pending_action_id}",
            },
            ["_sanctuary_target_allowed", "_sanctuary_choice_required"],
        )
    )
    begin["metadata"] = {"category": "combat", "costs_action": True}
    complete = deepcopy(owner)
    complete["parameters"].append(_parameter("pending_action_id"))
    complete["pre_conditions"] = []
    complete["effects"] = [
        _set("_pending_action", "state.pending_srd_actions[pending_action_id]"),
        _set("_damage_roll", None),
        _set("_damage_incoming", 0),
        _set("_damage_absorbed", 0),
        _set("_damage_hp_lost", 0),
    ]
    complete["effects"].extend(_set(name, None) for name in _typed_attack_feedback(owner))
    for name in names:
        complete["effects"] += _copy_value(name, f"_pending_action.context.{name}")
    complete["effects"] += [
        deepcopy(
            next(
                effect
                for effect in prefix
                if effect.get("call") == "resolve_srd_effective_armor_class"
            )
        ),
        _set("_attack_outcome", "'miss'"),
        _when("_attack_roll.total == 20", [_set("_attack_outcome", "'critical_hit'")]),
        _when(
            "_attack_roll.total != 1 and _attack_adjusted_total >= "
            "_effective_target_armor_class and _attack_outcome != 'critical_hit'",
            [_set("_attack_outcome", "'hit'")],
        ),
        _when(
            "_attack_outcome == 'hit' and _condition_critical_hit == true",
            [
                _set("_attack_outcome", "'critical_hit'"),
            ],
        ),
        _when("target.is_alive != true", [_set("_attack_outcome", "'miss'")]),
        _set("_critical_hit", "_attack_outcome == 'critical_hit'"),
        *suffix,
    ]
    complete["metadata"]["category"] = "internal"
    complete["metadata"]["costs_action"] = False
    return {begin_name: begin, stage_name: stage, "complete_srd_" + kind: complete}


def _resume_sanctuary_tool(tools):
    """Validate a chosen replacement against the saved scene without paying for another attack."""
    effects = [
        _require(
            "state.pending_srd_actions != null and pending_action_id in state.pending_srd_actions",
            "The saved attack does not exist.",
        ),
        _set("_retarget_action", "state.pending_srd_actions[pending_action_id]"),
        _set("_retarget_stack", "state.pending_srd_action_stack"),
        _require(
            "len(_retarget_stack) > 0 and "
            "_retarget_stack[len(_retarget_stack) - 1] == pending_action_id",
            "Resolve the innermost pending action first.",
        ),
        _require(
            "_retarget_action.status == 'open' and "
            "_retarget_action.stage == 'sanctuary_retarget_validation' and "
            "(_retarget_action.owner_kind == 'character_attack' or "
            "_retarget_action.owner_kind == 'creature_attack')",
            "This saved attack is not awaiting a replacement Sanctuary target.",
        ),
        _require(
            "_retarget_action.turn_actor == null or (combat.turn_active == true and "
            "combat.active_actor == _retarget_action.turn_actor)",
            "Retarget the attack on its original turn.",
        ),
        _set("_retarget_id", "_retarget_action.sanctuary_retarget_state_id"),
        _call(
            "resolve_srd_scene_relation",
            {
                "actor_state": "{_retarget_action.actor_state_id}",
                "target_state": "{_retarget_id}",
            },
            ["_scene_distance", "_scene_same_plane", "_scene_can_see", "_scene_line_of_sight"],
        ),
        _require(
            "_scene_same_plane == true and _scene_line_of_sight == true",
            "The replacement target must share the attacker's plane and have a clear path.",
        ),
        _set("_retarget_distance", "_scene_distance"),
        _set("_retarget_actor_sees_target", "_scene_can_see"),
        _call(
            "resolve_srd_scene_relation",
            {
                "actor_state": "{_retarget_id}",
                "target_state": "{_retarget_action.actor_state_id}",
            },
            ["_scene_can_see"],
        ),
        _set("_retarget_target_sees_actor", "_scene_can_see"),
        _set("state.pending_srd_actions.{pending_action_id}.parameters.target", "_retarget_id"),
        _set("state.pending_srd_actions.{pending_action_id}.target_state_id", "_retarget_id"),
        _set(
            "state.pending_srd_actions.{pending_action_id}.parameters.attack_distance",
            "_retarget_distance",
        ),
        _set(
            "state.pending_srd_actions.{pending_action_id}.parameters.actor_can_see_target",
            "_retarget_actor_sees_target",
        ),
        _set(
            "state.pending_srd_actions.{pending_action_id}.parameters.target_can_see_actor",
            "_retarget_target_sees_actor",
        ),
        _set("state.pending_srd_actions.{pending_action_id}.parameters.target_is_charmer", False),
        _set("state.pending_srd_actions.{pending_action_id}.stage", "'targeting'"),
        _set("_retarget_action", "state.pending_srd_actions[pending_action_id]"),
    ]
    for _, (_, _, kind) in ATTACK_OWNERS.items():
        name = "continue_srd_" + kind + "_targeting"
        parameters = {
            p["name"]: "{_retarget_action.parameters." + p["name"] + "}"
            for p in tools[name]["parameters"]
            if p["name"] not in {"lasting_state", "pending_action_id"}
        }
        parameters["pending_action_id"] = "{pending_action_id}"
        effects.append(
            _when(
                "_retarget_action.owner_kind == '" + kind + "'",
                [
                    _call(
                        name,
                        parameters,
                        ["_sanctuary_target_allowed", "_sanctuary_choice_required"],
                    )
                ],
            )
        )
    result = _tool(
        "Resume a Sanctuary-blocked attack against its scene-validated replacement.",
        [_parameter("pending_action_id")],
        effects,
    )
    result["metadata"] = {"category": "combat", "costs_action": False}
    return result


def _sanctuary_decision_tools(tools):
    """Bind the saved state privately for the public targeting choice."""
    node = deepcopy(tools["decide_srd_sanctuary_target"])
    if not any(p["name"] == "lasting_state" for p in node["parameters"]):
        node = deepcopy(tools["decide_srd_sanctuary_target_node"])
    node["metadata"] = {"category": "internal", "visible_to_player": False, "costs_action": False}
    public = deepcopy(node)
    public["parameters"] = [p for p in node["parameters"] if p["name"] != "lasting_state"]
    public["effects"] = [
        _call(
            "decide_srd_sanctuary_target_node",
            {
                **{p["name"]: "{" + p["name"] + "}" for p in public["parameters"]},
                "lasting_state": "state",
            },
            ["_sanctuary_retarget_required", "_sanctuary_operation_lost"],
        ),
    ]
    public["metadata"] = {"category": "spells", "costs_action": False}
    return {"decide_srd_sanctuary_target": public, "decide_srd_sanctuary_target_node": node}


def _complete_window(tools):
    """Return the actual saved owner's result with its public calculation feedback."""
    effects = [
        _require(
            "state.pending_srd_actions != null and pending_action_id in state.pending_srd_actions",
            "This pending action does not exist.",
        ),
        _set("_pending_action", "state.pending_srd_actions[pending_action_id]"),
        _set("_pending_stack", "state.pending_srd_action_stack"),
        _require(
            "len(_pending_stack) > 0 and "
            "_pending_stack[len(_pending_stack) - 1] == pending_action_id",
            "Complete the innermost reaction before its triggering action.",
        ),
        _require(
            "_pending_action.status == 'open' or _pending_action.status == 'lost'",
            "This action has already resolved.",
        ),
        _require(
            "_pending_action.turn_actor == null or "
            "(combat.turn_active == true and combat.active_actor == _pending_action.turn_actor)",
            "Complete the reaction window on its original turn.",
        ),
        _require(
            "_pending_action.stage != 'awaiting_sanctuary_choice' and "
            "_pending_action.stage != 'sanctuary_retarget_validation'",
            "Resolve the saved Sanctuary choice before completing the attack.",
        ),
        _set("_pending_card_title", "'Attack'"),
        _set("_pending_card_summary", []),
        _set("_pending_card_details", []),
        _set("_spell_card_summary", []),
        _set("_spell_detail_rows", []),
        _when(
            "_pending_action.spell != null",
            [
                _set("_pending_card_spell", "_pending_action.spell"),
                {
                    "table_lookup": {
                        "table": "definitions.spell_casting",
                        "key": "_pending_card_spell",
                        "required": True,
                    },
                    "store_as": "_pending_card_requirements",
                },
                _set("_pending_card_title", "_pending_card_requirements.name"),
            ],
        ),
    ]
    for _, (_, _, kind) in ATTACK_OWNERS.items():
        complete_name = "complete_srd_" + kind
        typed_feedback = _typed_attack_feedback(tools[complete_name])
        params = {
            parameter["name"]: "{_pending_action.parameters." + parameter["name"] + "}"
            for parameter in tools[complete_name]["parameters"]
            if parameter["name"] not in {"lasting_state", "pending_action_id"}
        }
        params["pending_action_id"] = "{pending_action_id}"
        effects += [
            _when(
                f"_pending_action.owner_kind == '{kind}' and _pending_action.status == 'open' and "
                "_pending_action.stage == 'reaction'",
                [
                    _call(
                        complete_name,
                        params,
                        [
                            "_attack_roll",
                            "_attack_adjusted_total",
                            "_attack_outcome",
                            "_damage_applied",
                            "_attack_card_modifier",
                            "_damage_roll",
                            "_damage_incoming",
                            "_damage_absorbed",
                            "_damage_hp_lost",
                            "_weapon_damage_type",
                            *typed_feedback,
                        ],
                    ),
                    _set("_pending_attack_outcome", "'Miss'"),
                    _when("_attack_outcome == 'hit'", [_set("_pending_attack_outcome", "'Hit'")]),
                    _when(
                        "_attack_outcome == 'critical_hit'",
                        [_set("_pending_attack_outcome", "'Critical hit'")],
                    ),
                    _call(
                        "record_srd_result_text",
                        {
                            "text": "Damage {_damage_applied}.",
                        },
                        ["_recorded_result_text"],
                    ),
                    _set(
                        "_pending_card_summary",
                        [
                            {"label": "'Outcome'", "value": "_pending_attack_outcome"},
                            {"label": "'Damage'", "value": "_recorded_result_text"},
                        ],
                    ),
                    _call(
                        "record_srd_result_text",
                        {
                            "text": "Attack dice {_attack_roll.results}; "
                            "selected {_attack_roll.total} "
                            "+ ({_attack_card_modifier}) = {_attack_adjusted_total}; "
                            "{_pending_attack_outcome}.",
                        },
                        ["_recorded_result_text"],
                    ),
                    _set(
                        "_pending_card_details",
                        [
                            {"label": "'Attack roll'", "value": "_recorded_result_text"},
                        ],
                    ),
                    _when(
                        "_damage_roll != null",
                        [
                            _call(
                                "record_srd_result_text",
                                {
                                    "text": "Damage dice {_damage_roll.results}; "
                                    "incoming {_damage_incoming} {_weapon_damage_type}; "
                                    "Temporary HP absorbed {_damage_absorbed}; "
                                    "HP lost {_damage_hp_lost}.",
                                },
                                ["_recorded_result_text"],
                            ),
                            {
                                "list_push": {
                                    "list": "_pending_card_details",
                                    "value": {
                                        "label": "'Damage'",
                                        "value": "_recorded_result_text",
                                    },
                                }
                            },
                        ],
                    ),
                ],
            )
        ]
        if "_wight_weapon_necrotic_roll" in typed_feedback:
            effects[-1]["on_true"].append(
                _when(
                    "_wight_weapon_necrotic_roll != null",
                    [
                        _call(
                            "record_srd_result_text",
                            {
                                "text": "Necrotic dice {_wight_weapon_necrotic_roll.results}; "
                                "incoming {_wight_weapon_necrotic_amount} Necrotic.",
                            },
                            ["_recorded_result_text"],
                        ),
                        {
                            "list_push": {
                                "list": "_pending_card_details",
                                "value": {
                                    "label": "'Wight weapon Necrotic'",
                                    "value": "_recorded_result_text",
                                },
                            }
                        },
                        _call(
                            "record_srd_result_text",
                            {
                                "text": "After defenses: {_component_1_effective_damage} "
                                "{_weapon_damage_type} and {_component_2_effective_damage} "
                                "Necrotic.",
                            },
                            ["_recorded_result_text"],
                        ),
                        {
                            "list_push": {
                                "list": "_pending_card_details",
                                "value": {
                                    "label": "'Wight weapon components'",
                                    "value": "_recorded_result_text",
                                },
                            }
                        },
                    ],
                )
            )
        if "_mummy_fist_necrotic_roll" in typed_feedback:
            effects[-1]["on_true"].append(
                _when(
                    "_mummy_fist_necrotic_roll != null",
                    [
                        _call(
                            "record_srd_result_text",
                            {
                                "text": "Necrotic dice {_mummy_fist_necrotic_roll.results}; "
                                "incoming {_mummy_fist_necrotic_amount} Necrotic.",
                            },
                            ["_recorded_result_text"],
                        ),
                        {
                            "list_push": {
                                "list": "_pending_card_details",
                                "value": {
                                    "label": "'Mummy Fist Necrotic'",
                                    "value": "_recorded_result_text",
                                },
                            }
                        },
                        _call(
                            "record_srd_result_text",
                            {
                                "text": "After defenses: {_component_1_effective_damage} "
                                "Bludgeoning and {_component_2_effective_damage} Necrotic.",
                            },
                            ["_recorded_result_text"],
                        ),
                        {
                            "list_push": {
                                "list": "_pending_card_details",
                                "value": {
                                    "label": "'Mummy Fist components'",
                                    "value": "_recorded_result_text",
                                },
                            }
                        },
                        _when(
                            "_mummy_rot_source_id != null",
                            [
                                _call(
                                    "record_srd_result_text",
                                    {"text": "Applied Mummy Rot."},
                                    ["_recorded_result_text"],
                                ),
                                {
                                    "list_push": {
                                        "list": "_pending_card_details",
                                        "value": {
                                            "label": "'Mummy Rot'",
                                            "value": "_recorded_result_text",
                                        },
                                    }
                                },
                            ],
                        ),
                        _when(
                            "_mummy_fist_dust == true",
                            [
                                {
                                    "list_push": {
                                        "list": "_pending_card_details",
                                        "value": {
                                            "label": "'Mummy Fist death'",
                                            "value": "'Reduced to dust.'",
                                        },
                                    }
                                }
                            ],
                        ),
                    ],
                )
            )
        if "_insect_poison_damage_roll" in typed_feedback:
            effects[-1]["on_true"].append(
                _when(
                    "_insect_poison_damage_roll != null",
                    [
                        _call(
                            "record_srd_result_text",
                            {
                                "text": "Poison dice {_insect_poison_damage_roll.results}; "
                                "incoming {_insect_poison_damage_amount} Poison.",
                            },
                            ["_recorded_result_text"],
                        ),
                        {
                            "list_push": {
                                "list": "_pending_card_details",
                                "value": {
                                    "label": "'Poison Jab Poison'",
                                    "value": "_recorded_result_text",
                                },
                            }
                        },
                        _call(
                            "record_srd_result_text",
                            {
                                "text": "After defenses: {_component_1_effective_damage} "
                                "Piercing and {_component_2_effective_damage} Poison.",
                            },
                            ["_recorded_result_text"],
                        ),
                        {
                            "list_push": {
                                "list": "_pending_card_details",
                                "value": {
                                    "label": "'Poison Jab components'",
                                    "value": "_recorded_result_text",
                                },
                            }
                        },
                    ],
                )
            )
        if "_ghoul_necrotic_damage_roll" in typed_feedback:
            effects[-1]["on_true"].append(
                _when(
                    "_ghoul_necrotic_damage_roll != null",
                    [
                        _call(
                            "record_srd_result_text",
                            {
                                "text": "Necrotic dice {_ghoul_necrotic_damage_roll.results}; "
                                "incoming {_ghoul_necrotic_damage_amount} Necrotic.",
                            },
                            ["_recorded_result_text"],
                        ),
                        {
                            "list_push": {
                                "list": "_pending_card_details",
                                "value": {
                                    "label": "'Ghoul Bite Necrotic'",
                                    "value": "_recorded_result_text",
                                },
                            }
                        },
                        _call(
                            "record_srd_result_text",
                            {
                                "text": "After defenses: {_component_1_effective_damage} "
                                "Piercing and {_component_2_effective_damage} Necrotic.",
                            },
                            ["_recorded_result_text"],
                        ),
                        {
                            "list_push": {
                                "list": "_pending_card_details",
                                "value": {
                                    "label": "'Ghoul Bite components'",
                                    "value": "_recorded_result_text",
                                },
                            }
                        },
                    ],
                )
            )
        if "_ghoul_claw_save_roll" in typed_feedback:
            effects[-1]["on_true"].append(
                _when(
                    "_ghoul_claw_save_roll != null",
                    [
                        _call(
                            "record_srd_result_text",
                            {
                                "text": "Constitution dice {_ghoul_claw_save_roll.results}; "
                                "total {_ghoul_claw_save_total} against DC 10: "
                                "{_ghoul_claw_save_outcome}.",
                            },
                            ["_recorded_result_text"],
                        ),
                        {
                            "list_push": {
                                "list": "_pending_card_details",
                                "value": {
                                    "label": "'Ghoul Claw save'",
                                    "value": "_recorded_result_text",
                                },
                            }
                        },
                    ],
                )
            )
            effects[-1]["on_true"].append(
                _when(
                    "_ghoul_claw_source_id != null",
                    [
                        {
                            "list_push": {
                                "list": "_pending_card_details",
                                "value": {
                                    "label": "'Ghoul Claw paralysis source'",
                                    "value": "_ghoul_claw_source_id",
                                },
                            }
                        },
                    ],
                )
            )
    effects += [
        _when(
            "_pending_action.kind == 'scene_fall' and _pending_action.status == 'open'",
            [
                _call(
                    "resolve_srd_scene_fall_event",
                    {"pending_action_id": "{pending_action_id}"},
                    ["_scene_fall_summary_rows", "_scene_fall_detail_rows"],
                ),
                _set("_pending_card_title", "'Falling creatures'"),
                _set("_pending_card_summary", "_scene_fall_summary_rows"),
                _set("_pending_card_details", "_scene_fall_detail_rows"),
            ],
        ),
        _set("_remaining_pending_stack", []),
        {
            "for_each": {
                "collection": "_pending_stack",
                "as": "_pending_stack_id",
                "do": [
                    _when(
                        "_pending_stack_id != pending_action_id",
                        [
                            {
                                "list_push": {
                                    "list": "_remaining_pending_stack",
                                    "value": "_pending_stack_id",
                                }
                            }
                        ],
                    ),
                ],
            }
        },
        _set("state.pending_srd_action_stack", "_remaining_pending_stack"),
        _set("state.pending_srd_actions.{pending_action_id}.status", "'completed'"),
        _when(
            "len(_remaining_pending_stack) == 0",
            [
                _set("state.pending_srd_actions", {}),
            ],
        ),
    ]
    result = _tool(
        "Complete the saved action after its reaction window without another roll or cost.",
        [_parameter("pending_action_id")],
        effects,
    )
    result["metadata"] = {
        "category": "combat",
        "costs_action": False,
        "result_card": {
            "version": 1,
            "footprint": "compact",
            "title": {"path": "context._pending_card_title"},
            "summary": {"path": "context._pending_card_summary"},
            "details_label": "Targets and calculations",
            "details": {"path": "context._pending_card_details"},
        },
    }
    return result


def _cast_window(owner):
    """Reserve the actual casting action while leaving materials and spell uses for resolution."""
    material_index = next(
        i
        for i, effect in enumerate(owner["effects"])
        if effect.get("call") == "resolve_srd_spell_materials"
    )
    prefix = deepcopy(owner["effects"][: material_index + 1])
    prefix[-1]["params"]["validate_only"] = True
    # Ongoing source identity belongs to resolved casting, after its reaction window.
    prefix = [
        effect
        for effect in prefix
        if not any(
            child.get("call") == "begin_srd_spell_source_instance" for child in _walk([effect])
        )
    ]
    level_effects = [
        deepcopy(effect)
        for effect in owner["effects"]
        if effect.get("set", {}).get("field") == "_slot_level"
        or (
            effect.get("condition")
            and effect.get("on_true")
            and all(
                child.get("set", {}).get("field") == "_slot_level" for child in effect["on_true"]
            )
        )
    ]
    begin = deepcopy(owner)
    begin["description"] = "Begin a source-validated casting action before its spell resolves."
    begin["effects"] = [
        *prefix,
        *level_effects,
        _require(
            "_slot_level >= _spell_definition.base_level",
            "The selected spell slot is below the spell's source level.",
        ),
        _require(
            "_spell_definition.casting_time == 'reaction' or "
            "state.pending_srd_action_stack == null or len(state.pending_srd_action_stack) == 0",
            "Complete the existing reaction window before another ordinary casting action.",
        ),
        _require(
            "casting_mode == 'ordinary' and "
            "(_spell_definition.casting_time == 'action' or "
            "_spell_definition.casting_time == 'bonus_action' or "
            "_spell_definition.casting_time == 'reaction')",
            "This window begins an Action, Bonus Action or Reaction spell.",
        ),
        _when(
            "_spell_definition.casting_time == 'reaction'",
            [
                _call(
                    "validate_srd_reaction_spell_trigger",
                    {
                        "caster_state": "{_caster_state_id}",
                        "spell": "{spell}",
                        "reaction_trigger_id": "{reaction_trigger_id}",
                        "targets": "{targets}",
                    },
                ),
            ],
        ),
        _require(
            "_cast_resource == 'cantrip' or _cast_resource == 'no_slot' or "
            "combat.turn_active != true or (" + SLOT_TURN_AVAILABLE + ")",
            "Only one spell slot can be expended to cast a spell on a turn.",
        ),
        _call(
            "spend_spell_casting_budget",
            {
                "caster_state": "{_caster_state_id}",
                "casting_time": "{_spell_definition.casting_time}",
                "slot_resource": "no_slot",
            },
        ),
        *_allocate("spell_cast", "_caster_state_id", "_caster_state_id"),
        *_save_parameters(owner["parameters"]),
        _set("state.pending_srd_actions.{_pending_action_id}.spell", "spell"),
        _set("_pending_actual_components", []),
        _set("_pending_components", "_casting_requirements.components"),
        {
            "for_each": {
                "collection": "_pending_components",
                "as": "_pending_component",
                "do": [
                    _when(
                        "_pending_component not in _component_exemptions",
                        [
                            {
                                "list_push": {
                                    "list": "_pending_actual_components",
                                    "value": "_pending_component",
                                }
                            },
                        ],
                    ),
                ],
            }
        },
        _set(
            "state.pending_srd_actions.{_pending_action_id}.components",
            "_pending_actual_components",
        ),
        *_copy_value(
            "state.pending_srd_actions.{_pending_action_id}.constitution_save_modifier",
            "_casting_profile.concentration_save_modifier",
        ),
        _set(
            "state.pending_srd_actions.{_pending_action_id}.casting_time",
            "_spell_definition.casting_time",
        ),
        _set("state.pending_srd_actions.{_pending_action_id}.limited_use_kind", "'none'"),
        _when(
            "caster == null and _npc_spell.uses_per_day > 0",
            [_set("state.pending_srd_actions.{_pending_action_id}.limited_use_kind", "'npc'")],
        ),
        _when(
            "_feature_cast == true",
            [
                _set(
                    "state.pending_srd_actions.{_pending_action_id}.limited_use_kind", "'feature'"
                ),
                _set(
                    "state.pending_srd_actions.{_pending_action_id}.limited_use_pool",
                    "_grant_free_pool",
                ),
                _set(
                    "state.pending_srd_actions.{_pending_action_id}.limited_use_maximum",
                    "_grant_free_limit",
                ),
            ],
        ),
    ]
    begin["metadata"] = {"category": "spells", "costs_action": True}
    begin["effects"].append(
        _when(
            "target != null and ("
            "_spell_definition.family == 'spell_attack_damage' or "
            "_spell_definition.family == 'single_target_save_damage' or "
            "_spell_definition.family == 'instantaneous_threshold_death_damage' or "
            "_spell_definition.family == 'instantaneous_attack_area_save_damage' or "
            "(_spell_definition.family == 'automatic_force_projectiles' and "
            "projectile_targets == null))",
            [
                _set(
                    "state.pending_srd_actions.{_pending_action_id}.target_state_id", "_target_id"
                ),
                _when(
                    "caster != null and target.has_actor_spell_modifiers == true",
                    [
                        _when(
                            "caster_state.saving_throw_modifiers == null",
                            [_set("caster_state.saving_throw_modifiers", {})],
                        ),
                        _set("_casting_wisdom_save", "(caster.data.abilities.wisdom - 10) // 2"),
                        _when(
                            "caster.data.saving_throw_proficiencies != null and "
                            "'wisdom' in caster.data.saving_throw_proficiencies",
                            [
                                _set(
                                    "_casting_wisdom_save",
                                    "_casting_wisdom_save + 2 + ((caster.data.level - 1) // 4)",
                                )
                            ],
                        ),
                        _set("caster_state.saving_throw_modifiers.wisdom", "_casting_wisdom_save"),
                    ],
                ),
                _call(
                    "continue_srd_spell_targeting",
                    {
                        "caster_state": "{_caster_state_id}",
                        "target_state": "{_target_id}",
                        "pending_action_id": "{_pending_action_id}",
                    },
                    ["_sanctuary_target_allowed", "_sanctuary_choice_required"],
                ),
            ],
        )
    )
    cast = deepcopy(owner)
    budget_index = next(
        i
        for i, effect in enumerate(cast["effects"])
        if i > material_index and "spend_spell_casting_budget" in str(effect)
    )
    if "Complete the source-owned casting" not in str(cast["effects"][budget_index]):
        assert "casting_mode == 'ritual'" in cast["effects"][budget_index].get("condition", "")
        budget = cast["effects"][budget_index]
        cast["effects"][budget_index] = {
            "condition": "caster_state.pending_srd_completion_id != null",
            "on_true": [
                _set("_pending_completion_id", "caster_state.pending_srd_completion_id"),
                _require(
                    "state.pending_srd_actions != null and "
                    "_pending_completion_id in state.pending_srd_actions and "
                    "state.pending_srd_actions[_pending_completion_id].kind == 'spell_cast' and "
                    "state.pending_srd_actions[_pending_completion_id].actor_state_id == "
                    "_caster_state_id and "
                    "state.pending_srd_actions[_pending_completion_id].spell == spell and "
                    "state.pending_srd_actions[_pending_completion_id].status == 'open'",
                    "Complete the source-owned casting action through its saved continuation.",
                ),
                _require(
                    "_cast_resource == 'cantrip' or _cast_resource == 'no_slot' or "
                    "combat.turn_active != true or (" + SLOT_TURN_AVAILABLE + ")",
                    "Only one spell slot can be expended to cast a spell on a turn.",
                ),
                _when(
                    "combat.turn_active == true and "
                    "_cast_resource != 'cantrip' and _cast_resource != 'no_slot'",
                    [
                        _set("caster_state.spell_slot_cast_this_turn", True),
                        *_copy_value(
                            "caster_state.spell_slot_turn_sequence", "combat.turn_sequence"
                        ),
                    ],
                ),
            ],
            "on_false": [budget],
        }
    return {"begin_srd_spell_action": begin, "cast_spell": cast}


def _spell_sanctuary_tools():
    """Retain scalar damaging-spell targeting before any spell roll or paid completion."""
    targeting = _tool(
        "Resolve and retain Sanctuary's targeting save for the saved damaging spell.",
        [_entity("caster_state"), _entity("target_state"), _parameter("pending_action_id")],
        [
            _set("_targeting_cast", "state.pending_srd_actions[pending_action_id]"),
            _require(
                "_targeting_cast.kind == 'spell_cast' and "
                "_targeting_cast.actor_state_id == _caster_state_id and "
                "_targeting_cast.target_state_id == _target_state_id and "
                "_targeting_cast.status == 'open'",
                "The targeting save requires its actual saved spell and target.",
            ),
            _call(
                "check_srd_sanctuary_targeting",
                {
                    "actor_state": "{_caster_state_id}",
                    "target_state": "{_target_state_id}",
                    "lasting_state": "state",
                    "pending_action_id": "{pending_action_id}",
                    "operation_kind": "targeted_damage_spell",
                },
                ["_sanctuary_target_allowed", "_sanctuary_choice_required"],
            ),
            _when(
                "_sanctuary_target_allowed == true",
                [
                    _set(
                        "state.pending_srd_actions.{pending_action_id}.spell_targeting_checked",
                        True,
                    ),
                    _set("state.pending_srd_actions.{pending_action_id}.stage", "'reaction'"),
                ],
            ),
        ],
    )
    resume = _tool(
        "Resume the saved damaging spell against its scene-validated replacement target.",
        [_parameter("pending_action_id")],
        [
            _require(
                "state.pending_srd_actions != null and "
                "pending_action_id in state.pending_srd_actions",
                "The saved spell does not exist.",
            ),
            _set("_retarget_cast", "state.pending_srd_actions[pending_action_id]"),
            _require(
                "state.pending_srd_action_stack[-1] == pending_action_id and "
                "_retarget_cast.kind == 'spell_cast' and _retarget_cast.status == 'open' and "
                "_retarget_cast.stage == 'sanctuary_retarget_validation'",
                "This saved spell is not awaiting a replacement Sanctuary target.",
            ),
            _require(
                "_retarget_cast.turn_actor == null or (combat.turn_active == true and "
                "combat.active_actor == _retarget_cast.turn_actor)",
                "Retarget the spell on its original turn.",
            ),
            {
                "table_lookup": {
                    "table": "definitions.spells",
                    "key": "_retarget_cast.spell",
                    "required": True,
                },
                "store_as": "_retarget_definition",
            },
            _set("_retarget_id", "_retarget_cast.sanctuary_retarget_state_id"),
            _call(
                "resolve_srd_scene_relation",
                {
                    "actor_state": "{_retarget_cast.actor_state_id}",
                    "target_state": "{_retarget_id}",
                },
                ["_scene_distance", "_scene_same_plane", "_scene_can_see", "_scene_line_of_sight"],
            ),
            _require(
                "_scene_same_plane == true and _scene_line_of_sight == true and "
                "_scene_distance <= _retarget_definition.range_feet",
                "The replacement spell target must have a clear path on the same plane "
                "and be within the source range.",
            ),
            _require(
                "(_retarget_definition.targeting_sense != 'sight' and "
                "_retarget_definition.requires_sight != true) or _scene_can_see == true",
                "The source spell requires seeing its replacement target.",
            ),
            _set("state.pending_srd_actions.{pending_action_id}.parameters.target", "_retarget_id"),
            _set("state.pending_srd_actions.{pending_action_id}.target_state_id", "_retarget_id"),
            _set(
                "state.pending_srd_actions.{pending_action_id}.parameters.target_distance",
                "_scene_distance",
            ),
            _set(
                "state.pending_srd_actions.{pending_action_id}.parameters.caster_can_see_target",
                "_scene_can_see",
            ),
            _set("state.pending_srd_actions.{pending_action_id}.stage", "'targeting'"),
            _call(
                "continue_srd_spell_targeting",
                {
                    "caster_state": "{_retarget_cast.actor_state_id}",
                    "target_state": "{_retarget_id}",
                    "pending_action_id": "{pending_action_id}",
                },
                ["_sanctuary_target_allowed", "_sanctuary_choice_required"],
            ),
        ],
    )
    resume["metadata"] = {"category": "spells", "costs_action": False}
    return {"continue_srd_spell_targeting": targeting, "resume_srd_sanctuary_spell": resume}


def _strip_spell_hit_guard(clause):
    """Remove this emitter's guard even when the later Slow owner wraps it."""
    marker = "_spell_hit_window_deferred != true and ("
    while marker in clause:
        start = clause.index(marker)
        opening = start + len(marker) - 1
        depth = 1
        closing = opening + 1
        while depth and closing < len(clause):
            depth += (clause[closing] == "(") - (clause[closing] == ")")
            closing += 1
        if depth:
            raise ValueError("Unbalanced saved spell-hit guard")
        clause = clause[:start] + clause[opening + 1 : closing - 1] + clause[closing:]
    return clause


def normalize_pending_spell_guards(text):
    """Remove final owned wrappers before generators locate their exact family anchors."""
    runtime = _load_runtime(text)

    def normalize(effects):
        result = []
        for effect in effects:
            clause = effect.get("condition", "")
            if "_spell_hit_window_deferred != true and (" in clause:
                effect["condition"] = _strip_spell_hit_guard(clause)
            if clause == "_spell_hit_window_deferred == true":
                continue
            if effect.get("condition") == "_pending_spell_operation_lost != true":
                result.extend(normalize(effect["on_true"]))
            elif effect.get("condition") == "_pending_spell_operation_lost == true":
                continue
            elif effect.get("set", {}).get("field") not in {
                "_pending_spell_operation_lost",
                "_spell_hit_window_deferred",
            }:
                for branch in ("on_true", "on_false"):
                    if branch in effect:
                        effect[branch] = normalize(effect[branch])
                result.append(effect)
        return result

    for name in ("cast_spell", "cast_spell_outside_combat"):
        tool = runtime["tools"][name]
        tool["effects"] = normalize(tool["effects"])
        text = _render_tool(text, name, tool)
    leaf = runtime["tools"]["cast_spell_attack_damage"]
    restored = []
    for effect in leaf["effects"]:
        if effect.get("condition") == "_spell_hit_window_deferred != true":
            restored.extend(effect["on_true"])
        elif effect.get("condition") == (
            "caster_state.pending_srd_completion_id != null and _spell_attack_outcome == 'success'"
        ):
            continue
        elif effect.get("set", {}).get("field") != "_spell_hit_window_deferred":
            restored.append(effect)
    leaf["effects"] = restored
    text = _render_tool(text, "cast_spell_attack_damage", leaf)
    return text


def _spell_attack_hit_window(tools, changed):
    """Retain an actual spell hit before its unchanged damage and consequence owners run."""
    owner = deepcopy(tools["cast_spell_attack_damage"])
    split = (
        next(
            i
            for i, e in enumerate(owner["effects"])
            if e.get("set", {}).get("field") == "_spell_critical_hit"
        )
        + 1
    )
    prefix, suffix = deepcopy(owner["effects"][:split]), deepcopy(owner["effects"][split:])
    names = sorted(
        set(_capture_names(prefix, suffix, owner.get("metadata", {})))
        | {
            "_spell_attack",
            "_spell_condition_critical_hit",
        }
    )
    save_parameters = deepcopy(_save_parameters(owner["parameters"]))
    for effect in _walk(save_parameters):
        field = effect.get("set", {}).get("field", "")
        if ".parameters." in field:
            effect["set"]["field"] = field.replace(".parameters.", ".spell_attack_parameters.")
    saved = [
        _set("_pending_action_id", "caster_state.pending_srd_completion_id"),
        _require(
            "state.pending_srd_actions[_pending_action_id].kind == 'spell_cast' and "
            "state.pending_srd_actions[_pending_action_id].actor_state_id == _caster_state_id",
            "This spell hit needs its original casting action.",
        ),
        _set("state.pending_srd_actions.{_pending_action_id}.kind", "'spell_attack'"),
        _set("state.pending_srd_actions.{_pending_action_id}.stage", "'spell_attack_reaction'"),
        _set("state.pending_srd_actions.{_pending_action_id}.target_state_id", "_target_id"),
        _set("state.pending_srd_actions.{_pending_action_id}.attack_outcome", "'hit'"),
        _when(
            "_spell_critical_hit == true",
            [
                _set(
                    "state.pending_srd_actions.{_pending_action_id}.attack_outcome",
                    "'critical_hit'",
                ),
            ],
        ),
        _set("state.pending_srd_actions.{_pending_action_id}.spell_attack_parameters", {}),
        _set("state.pending_srd_actions.{_pending_action_id}.spell_attack_context", {}),
        *save_parameters,
    ]
    for name in names:
        saved += _copy_value(
            "state.pending_srd_actions.{_pending_action_id}.spell_attack_context." + name, name
        )
    saved += [
        _set("_spell_hit_window_deferred", True),
        _set("_spell_damage", None),
        _set("_spell_damage_amount", 0),
        _call(
            "record_srd_spell_target_result",
            {
                "target": "{_target_id}",
                "brief": "Attack total {_card_d20_total}; awaiting defense",
                "detail": "Saved D20 {_card_d20_roll.results}; attack total {_card_d20_total}. "
                "Resolve the defensive Reaction window before damage or later effects.",
            },
            ["_spell_target_summary", "_spell_target_detail"],
        ),
        {"list_push": {"list": "_spell_summary_rows", "value": "_spell_target_summary"}},
        {"list_push": {"list": "_spell_detail_rows", "value": "_spell_target_detail"}},
    ]
    owner["effects"] = [
        _set("_spell_hit_window_deferred", False),
        *prefix,
        _when(
            "caster_state.pending_srd_completion_id != null and _spell_attack_outcome == 'success'",
            saved,
        ),
        _when("_spell_hit_window_deferred != true", suffix),
    ]
    changed["cast_spell_attack_damage"] = owner
    finish = deepcopy(owner)
    finish["parameters"].append(_parameter("pending_action_id"))
    finish["pre_conditions"] = []
    finish["effects"] = [
        _set("_spell_hit_event", "state.pending_srd_actions[pending_action_id]"),
        _require(
            "_spell_hit_event.kind == 'spell_attack' and "
            "_spell_hit_event.owner_kind == 'spell_cast' and "
            "_spell_hit_event.stage == 'spell_attack_reaction' and "
            "_spell_hit_event.status == 'open'",
            "Complete this spell hit through its original saved window.",
        ),
    ]
    for name in names:
        finish["effects"] += _copy_value(name, "_spell_hit_event.spell_attack_context." + name)
    armor_start = next(
        i
        for i, e in enumerate(prefix)
        if e.get("set", {}).get("field") == "_spell_target_armor_class"
    )
    armor_end = next(
        i for i, e in enumerate(prefix) if e.get("call") == "consume_srd_next_attack_advantage"
    )
    finish["effects"] += [
        *deepcopy(prefix[armor_start:armor_end]),
        _set("_spell_attack_outcome", "'failure'"),
        _set("_spell_attack_kind", "'miss'"),
        _when(
            "_card_d20_roll.total == 20",
            [
                _set("_spell_attack_outcome", "'success'"),
                _set("_spell_attack_kind", "'critical_hit'"),
            ],
        ),
        _when(
            "_card_d20_roll.total != 1 and _card_d20_total >= _spell_target_armor_class "
            "and _spell_attack_kind != 'critical_hit'",
            [
                _set("_spell_attack_outcome", "'success'"),
                _set("_spell_attack_kind", "'hit'"),
            ],
        ),
        _when(
            "_spell_attack_outcome == 'success' and _spell_condition_critical_hit == true",
            [
                _set("_spell_attack_kind", "'critical_hit'"),
            ],
        ),
        _set("_spell_critical_hit", "_spell_attack_kind == 'critical_hit'"),
        *suffix,
        _set("state.pending_srd_actions.{pending_action_id}.stage", "'spell_attack_resolved'"),
    ]
    finish["metadata"] = {"category": "internal", "visible_to_player": False, "costs_action": False}
    changed["complete_srd_spell_attack_hit"] = finish
    cast = changed["cast_spell"]
    dispatch = next(
        e for e in _walk(cast["effects"]) if e.get("call") == "cast_spell_attack_damage"
    )
    dispatch["export_context"]["_spell_hit_window_deferred"] = "_spell_hit_window_deferred"
    consequences = [
        deepcopy(e)
        for e in _walk(cast["effects"])
        if "_spell_attack_outcome" in e.get("condition", "")
        and any(
            key in e["condition"]
            for key in (
                "_spell_definition.turn_effect",
                "_spell_definition.prevents_opportunity_attacks",
            )
        )
    ]
    context_names = set(_capture_names(cast["effects"], consequences, {}))
    context_names -= {
        "_spell_attack_outcome",
        "_spell_detail_rows",
        "_turn_effect_dice",
        "_spell_hit_window_deferred",
    }
    context_names.add("_spell_card_summary")
    if "begin_srd_spell_source_instance" in tools:
        context_names.add("_spell_source_cast_id")
    capture = [
        _set("_pending_action_id", "caster_state.pending_srd_completion_id"),
        _set("state.pending_srd_actions.{_pending_action_id}.spell_consequence_context", {}),
    ]
    for name in sorted(context_names):
        capture += _copy_value(
            "state.pending_srd_actions.{_pending_action_id}.spell_consequence_context." + name, name
        )
    for e in _walk(cast["effects"]):
        clause = e.get("condition", "")
        if "_spell_attack_outcome" in clause and any(
            key in clause
            for key in (
                "_spell_definition.turn_effect",
                "_spell_definition.prevents_opportunity_attacks",
            )
        ):
            e["condition"] = "_spell_hit_window_deferred != true and (" + clause + ")"
    cast["effects"] = [
        _set("_spell_hit_window_deferred", False),
        *cast["effects"],
        _when("_spell_hit_window_deferred == true", capture),
    ]
    continuation = deepcopy(cast)
    continuation["parameters"].append(_parameter("pending_action_id"))
    continuation["pre_conditions"] = []
    continuation["effects"] = [
        _set("_spell_hit_event", "state.pending_srd_actions[pending_action_id]"),
        _set("_spell_cast_failed", False),
    ]
    for name in sorted(context_names):
        continuation["effects"] += _copy_value(
            name, "_spell_hit_event.spell_consequence_context." + name
        )
    continuation["effects"] += [
        _call(
            "complete_srd_spell_attack_hit",
            {
                **{
                    p["name"]: "{_spell_hit_event.spell_attack_parameters." + p["name"] + "}"
                    for p in owner["parameters"]
                    if p["name"] != "lasting_state"
                },
                "pending_action_id": "{pending_action_id}",
            },
            [
                "_spell_attack",
                "_spell_attack_outcome",
                "_spell_attack_kind",
                "_spell_summary_rows",
                "_spell_detail_rows",
                "_spell_damage_amount",
            ]
            + (
                ["_mirror_redirected", "_mirror_dice", "_mirror_source_id", "_mirror_remaining"]
                if "_mirror_redirected" in str(owner["effects"])
                else []
            ),
        ),
        *consequences,
    ]
    continuation["metadata"] = {
        "category": "internal",
        "visible_to_player": False,
        "costs_action": False,
    }
    changed["complete_srd_spell_attack_consequences"] = continuation


def finalize_pending_spell_guards(text):
    """A lost spell pays ordinary costs and resolves no family effects or damage rolls."""
    runtime = _load_runtime(text)
    cast = runtime["tools"]["cast_spell"]

    def guard(effects):
        result, group = [], []
        for effect in effects:
            clause = effect.get("condition", "")
            dispatch = clause.startswith(
                ("_spell_definition.family", "_spell_definition.turn_effect")
            ) or (
                clause.startswith("_spell_cast_failed != true and (")
                and (
                    "_spell_definition.family" in clause
                    or "_spell_definition.turn_effect" in clause
                )
            )
            if dispatch:
                group.append(effect)
            else:
                if group:
                    result.append(_when("_pending_spell_operation_lost != true", group))
                    group = []
                for branch in ("on_true", "on_false"):
                    if branch in effect:
                        effect[branch] = guard(effect[branch])
                result.append(effect)
        if group:
            result.append(_when("_pending_spell_operation_lost != true", group))
        return result

    cast["effects"] = [
        _set(
            "_pending_spell_operation_lost",
            "caster_state.pending_srd_completion_id != null and "
            "lasting_state.pending_srd_actions[caster_state.pending_srd_completion_id].stage == "
            "'sanctuary_lost'",
        ),
        *guard(cast["effects"]),
        _when(
            "_pending_spell_operation_lost == true",
            [
                _call(
                    "record_srd_result_text",
                    {
                        "text": "{caster_state.name}'s {spell} was lost after failed Sanctuary "
                        "targeting; its casting costs were spent.",
                    },
                    ["_recorded_result_text"],
                ),
                {
                    "list_push": {
                        "list": "_spell_card_summary",
                        "value": {
                            "label": "'Casting'",
                            "value": "_recorded_result_text",
                        },
                    }
                },
                _set(
                    "_spell_summary_rows",
                    [{"label": "'Casting'", "value": "_recorded_result_text"}],
                ),
                _set(
                    "_spell_detail_rows", [{"label": "'Casting'", "value": "_recorded_result_text"}]
                ),
            ],
        ),
    ]
    text = _render_tool(text, "cast_spell", cast)
    exploration = deepcopy(cast)
    return _render_tool(text, "cast_spell_outside_combat", exploration)


def _complete_cast_window(complete, cast):
    effects = complete["effects"]
    insert_at = next(
        i
        for i, effect in enumerate(effects)
        if effect.get("set", {}).get("field") == "_remaining_pending_stack"
    )
    parameters = {
        parameter["name"]: "{_pending_action.parameters." + parameter["name"] + "}"
        for parameter in cast["parameters"]
        if parameter["name"] != "lasting_state"
    }
    effects.insert(
        insert_at,
        _when(
            "_pending_action.kind == 'spell_cast' and _pending_action.status == 'open' and "
            "_pending_action.owner_kind != 'extended_casting'",
            [
                _call(
                    "set_srd_pending_cast_completion",
                    {
                        "caster_state": "{_pending_action.actor_state_id}",
                        "pending_action_id": "{pending_action_id}",
                    },
                ),
                _call(
                    "cast_spell",
                    parameters,
                    [
                        "_spell_card_summary",
                        "_spell_summary_rows",
                        "_spell_detail_rows",
                    ],
                ),
                _call(
                    "set_srd_pending_cast_completion",
                    {
                        "caster_state": "{_pending_action.actor_state_id}",
                        "pending_action_id": "",
                    },
                ),
            ],
        ),
    )
    effects.insert(
        insert_at,
        _when(
            "_pending_action.kind == 'spell_cast' and _pending_action.status == 'lost'",
            [
                _call(
                    "settle_srd_countered_cast_use",
                    {
                        "caster_state": "{_pending_action.actor_state_id}",
                        "pending_action_id": "{pending_action_id}",
                    },
                ),
            ],
        ),
    )
    effects.insert(
        insert_at,
        _when(
            "_pending_action.owner_kind == 'extended_casting'",
            [
                _call(
                    "finish_srd_extended_spell_window",
                    {
                        "caster_state": "{_pending_action.actor_state_id}",
                        "pending_action_id": "{pending_action_id}",
                    },
                ),
            ],
        ),
    )
    effects.insert(
        insert_at,
        _when(
            "_pending_action.kind == 'spell_attack' and _pending_action.status == 'open'",
            [
                _call(
                    "complete_srd_spell_attack_consequences",
                    {
                        **parameters,
                        "pending_action_id": "{pending_action_id}",
                    },
                    [
                        "_spell_card_summary",
                        "_spell_summary_rows",
                        "_spell_detail_rows",
                        "_spell_attack",
                        "_spell_attack_outcome",
                        "_spell_attack_kind",
                    ],
                )
            ],
        ),
    )
    cleanup = next(
        i
        for i, e in enumerate(effects)
        if e.get("set", {}).get("field") == "_remaining_pending_stack"
    )
    effects.insert(
        cleanup,
        _when(
            "_pending_action.spell != null",
            [
                _set("_pending_card_summary", "_spell_card_summary"),
                _set("_pending_card_details", "_spell_detail_rows"),
                _when(
                    "_pending_action.status == 'lost' or "
                    "_pending_action.owner_kind == 'extended_casting'",
                    [
                        _call(
                            "record_srd_result_text",
                            {
                                "text": "{_pending_card_title}: "
                                "casting window {_pending_action.status}.",
                            },
                            ["_recorded_result_text"],
                        ),
                        _set(
                            "_pending_card_summary",
                            [
                                {"label": "'Casting'", "value": "_recorded_result_text"},
                            ],
                        ),
                    ],
                ),
            ],
        ),
    )
    cleanup += 1
    tail = effects[cleanup:]
    effects[cleanup:] = [
        _set(
            "_keep_spell_hit_open",
            "state.pending_srd_actions[pending_action_id].kind == "
            "'spell_attack' and state.pending_srd_actions[pending_action_id].stage == "
            "'spell_attack_reaction' or "
            "state.pending_srd_actions[pending_action_id].kind == 'teleport_resolution'",
        ),
        _when("_keep_spell_hit_open != true", tail),
    ]
    return _tool(
        "Bind the saved casting action only for its private completion callback.",
        [_entity("caster_state"), _parameter("pending_action_id", required=False, default="")],
        [
            _set("caster_state.pending_srd_completion_id", None),
            _when(
                "pending_action_id != ''",
                [
                    _set("caster_state.pending_srd_completion_id", "pending_action_id"),
                ],
            ),
        ],
    )


def _extended_casting_windows(tools):
    """Open an actual longer-casting action without resolving or recosting its spell."""
    changed = {}
    changed["open_srd_extended_spell_window"] = _tool(
        "Bind the ongoing casting channel to its actual reaction trigger.",
        [_entity("caster_state")],
        [
            _require(
                "caster_state.concentration_effect_family == 'extended_casting' and "
                "caster_state.extended_casting != null",
                "A longer-casting reaction needs its actual ongoing channel.",
            ),
            _set("_extended_attempt", "caster_state.extended_casting"),
            _require(
                "_extended_attempt.components != null and _extended_attempt.limited_use != null",
                "Restart this older channel to bind its actual source and components.",
            ),
            *_allocate("extended_casting", "_caster_state_id", "_caster_state_id"),
            _set("state.pending_srd_actions.{_pending_action_id}.kind", "'spell_cast'"),
            _set("state.pending_srd_actions.{_pending_action_id}.spell", "_extended_attempt.spell"),
            _set(
                "state.pending_srd_actions.{_pending_action_id}.parameters",
                {"spell": "_extended_attempt.spell"},
            ),
            _set(
                "state.pending_srd_actions.{_pending_action_id}.components",
                "_extended_attempt.components",
            ),
            *_copy_value(
                "state.pending_srd_actions.{_pending_action_id}.constitution_save_modifier",
                "caster_state.concentration_save_modifier",
            ),
            _set(
                "state.pending_srd_actions.{_pending_action_id}.limited_use_kind",
                "_extended_attempt.limited_use.kind",
            ),
            _set(
                "state.pending_srd_actions.{_pending_action_id}.limited_use_pool",
                "_extended_attempt.limited_use.pool",
            ),
            _set(
                "state.pending_srd_actions.{_pending_action_id}.limited_use_maximum",
                "_extended_attempt.limited_use.maximum",
            ),
            _set("caster_state.extended_casting.window_action_id", "_pending_action_id"),
        ],
    )
    changed["finish_srd_extended_spell_window"] = _tool(
        "Close only this saved channel's reaction, interrupting it when Counterspell succeeded.",
        [_entity("caster_state"), _parameter("pending_action_id")],
        [
            _set("_extended_window", "state.pending_srd_actions[pending_action_id]"),
            _require(
                "_extended_window.kind == 'spell_cast' and "
                "_extended_window.owner_kind == 'extended_casting' and "
                "_extended_window.actor_state_id == _caster_state_id",
                "This saved action does not own the selected casting channel.",
            ),
            _when(
                "_extended_window.status == 'lost'",
                [
                    _call("interrupt_srd_extended_casting", {"caster_state": "{_caster_state_id}"}),
                ],
            ),
            _when(
                "_extended_window.status == 'open'",
                [
                    _require(
                        "caster_state.extended_casting != null and "
                        "caster_state.extended_casting.window_action_id == pending_action_id",
                        "The saved reaction belongs to another casting attempt.",
                    ),
                    _set("caster_state.extended_casting.window_action_id", None),
                ],
            ),
        ],
    )
    for base, name in (
        ("begin_srd_spell_cast", "begin_srd_extended_spell_action"),
        ("continue_srd_spell_cast", "continue_srd_extended_spell_action"),
    ):
        source = tools[base]
        body = deepcopy(source)
        checks = [
            _require(
                "state.pending_srd_action_stack == null or "
                "len(state.pending_srd_action_stack) == 0",
                "Complete the pending reaction window before this casting action.",
            )
        ]
        if base.startswith("continue"):
            checks.append(
                _require(
                    "elapsed_rounds == 1", "Open a reaction for one actual casting round at a time."
                )
            )
        body["effects"] = (
            checks
            + body["effects"]
            + [
                _set("_pending_action_id", None),
                _when(
                    "caster_state.extended_casting != null",
                    [
                        _call(
                            "open_srd_extended_spell_window",
                            {"caster_state": "{_caster_state_id}"},
                            ["_pending_action_id"],
                        ),
                    ],
                ),
            ]
        )
        body["description"] = (
            "Perform this longer casting or Ritual action, then leave its actual Counterspell "
            "window open. Complete the saved window before further casting progress."
        )
        body["metadata"] = deepcopy(source["metadata"])
        body["metadata"]["category"] = "spells"
        body["metadata"]["costs_action"] = True
        changed[name] = body
    return changed


def _hostility_tools():
    effects = []
    for name, other in (("actor_state", "target_state"), ("target_state", "actor_state")):
        effects += [
            _when(f"{name}.fighting_actor_ids == null", [_set(f"{name}.fighting_actor_ids", [])]),
            _set("_fighting_ids", f"{name}.fighting_actor_ids"),
            _when(
                f"_{other}_id not in _fighting_ids",
                [
                    {"list_push": {"list": "_fighting_ids", "value": f"_{other}_id"}},
                    _set(f"{name}.fighting_actor_ids", "_fighting_ids"),
                ],
            ),
        ]
    return {
        "record_srd_hostile_actor_pair": _tool(
            "Record actual encounter opponents without treating carried inventory as held objects.",
            [_entity("actor_state"), _entity("target_state")],
            effects,
        )
    }


def _countered_use_tool():
    """Spend the interrupted limited use while retaining the slot that Counterspell exempts."""
    return _tool(
        "Settle an interrupted spell's source-owned daily or feature use once.",
        [_entity("caster_state"), _parameter("pending_action_id")],
        [
            _set("_countered_cast", "state.pending_srd_actions[pending_action_id]"),
            _require(
                "_countered_cast.actor_state_id == _caster_state_id and "
                "_countered_cast.kind == 'spell_cast' and "
                "_countered_cast.status == 'lost' and _countered_cast.countered_by != null",
                "Only the actual countered casting action can settle this use.",
            ),
            _set("_countered_spell", "_countered_cast.spell"),
            _when(
                "_countered_cast.limited_use_kind == 'npc'",
                [
                    _require(
                        "caster_state.spell_uses_remaining[_countered_spell] >= 1",
                        "The interrupted spell's reserved daily use is unavailable.",
                    ),
                    {
                        "decrement": {
                            "field": "caster_state.spell_uses_remaining.{_countered_spell}",
                            "amount": 1,
                        }
                    },
                ],
            ),
            _when(
                "_countered_cast.limited_use_kind == 'feature'",
                [
                    _set("_countered_pool", "_countered_cast.limited_use_pool"),
                    _set("_countered_uses", 0),
                    _when(
                        "caster_state.spell_grant_uses == null",
                        [_set("caster_state.spell_grant_uses", {})],
                    ),
                    _when(
                        "caster_state.spell_grant_uses != null and "
                        "_countered_pool in caster_state.spell_grant_uses",
                        [_set("_countered_uses", "caster_state.spell_grant_uses[_countered_pool]")],
                    ),
                    _require(
                        "_countered_uses < _countered_cast.limited_use_maximum",
                        "The interrupted spell's reserved feature use is unavailable.",
                    ),
                    _set("caster_state.spell_grant_uses.{_countered_pool}", "_countered_uses + 1"),
                ],
            ),
            _when(
                "caster_state.has_actor_spell_modifiers == true",
                [
                    _call(
                        "end_srd_sanctuary_for_actor",
                        {"actor_state": "{_caster_state_id}", "lasting_state": "state"},
                    )
                ],
            ),
            _set("caster_state.spell_preparation_open", False),
            _call("interrupt_srd_rest", {"target_state": "{_caster_state_id}", "reason": "spell"}),
        ],
    )


def _reaction_tools():
    validate = _tool(
        "Validate the exact saved triggering action before spending a Reaction spell.",
        [
            _entity("caster_state"),
            _parameter("spell"),
            _parameter("reaction_trigger_id"),
            _parameter("targets", "array", items={"type": "string"}, required=False),
        ],
        [
            _require(
                "state.pending_srd_actions != null and "
                "reaction_trigger_id in state.pending_srd_actions",
                "A Reaction spell requires an actual saved trigger.",
            ),
            _set("_reaction_trigger", "state.pending_srd_actions[reaction_trigger_id]"),
            _set("_reaction_stack", "state.pending_srd_action_stack"),
            _require("len(_reaction_stack) > 0", "The triggering action is no longer pending."),
            _set("_reaction_top", "_reaction_stack[len(_reaction_stack) - 1]"),
            _require(
                "_reaction_top == reaction_trigger_id or "
                "(caster_state.pending_srd_completion_id == _reaction_top and "
                "state.pending_srd_actions[_reaction_top].parameters.reaction_trigger_id "
                "== reaction_trigger_id)",
                "Resolve the innermost reaction first.",
            ),
            _require(
                "_reaction_trigger.status == 'open' and (_reaction_trigger.stage == 'reaction' "
                "or (_reaction_trigger.kind == 'spell_attack' and "
                "_reaction_trigger.stage == 'spell_attack_reaction'))",
                "The saved action is not an eligible Reaction trigger.",
            ),
            _require(
                "_reaction_trigger.turn_actor == null or (combat.turn_active == true and "
                "combat.active_actor == _reaction_trigger.turn_actor)",
                "The Reaction trigger has left its original turn.",
            ),
            _when(
                "spell == 'counterspell'",
                [
                    _require(
                        "_reaction_trigger.kind == 'spell_cast' and "
                        "_reaction_trigger.actor_state_id != _caster_state_id and "
                        "len(_reaction_trigger.components) > 0",
                        "Counterspell needs another creature casting with components.",
                    ),
                    _call(
                        "resolve_srd_scene_relation",
                        {
                            "actor_state": "{_caster_state_id}",
                            "target_state": "{_reaction_trigger.actor_state_id}",
                        },
                        ["_scene_distance", "_scene_same_plane", "_scene_can_see"],
                    ),
                    _require(
                        "_scene_same_plane == true and _scene_distance <= 60 and "
                        "_scene_can_see == true",
                        "Counterspell needs a visible caster within 60 feet.",
                    ),
                    _require(
                        "_reaction_trigger.constitution_save_modifier != null",
                        "The triggering cast needs its actual Constitution save modifier.",
                    ),
                ],
            ),
            _when(
                "spell == 'shield'",
                [
                    _set(
                        "_shield_attack_trigger",
                        "(_reaction_trigger.kind == 'weapon_attack' or "
                        "_reaction_trigger.kind == 'spell_attack') and "
                        "_reaction_trigger.target_state_id == _caster_state_id and "
                        "(_reaction_trigger.attack_outcome == 'hit' or "
                        "_reaction_trigger.attack_outcome == 'critical_hit')",
                    ),
                    _require(
                        "_shield_attack_trigger == true or "
                        "(_reaction_trigger.kind == 'spell_cast' and "
                        "_reaction_trigger.spell == 'magic_missile' and "
                        "(_reaction_trigger.parameters.target == _caster_state_id or "
                        "(_reaction_trigger.parameters.projectile_targets != null and "
                        "_caster_state_id in _reaction_trigger.parameters.projectile_targets)))",
                        "Shield needs an actual attack hit or Magic Missile targeting this caster.",
                    ),
                ],
            ),
            _when(
                "spell == 'feather_fall'",
                [
                    _require(
                        "targets != null and len(targets) >= 1 and len(targets) <= 5",
                        "Choose one to five actual falling creatures.",
                    ),
                    _call(
                        "validate_srd_feather_fall_trigger",
                        {
                            "caster_state": "{_caster_state_id}",
                            "reaction_trigger_id": "{reaction_trigger_id}",
                            "targets": "{targets}",
                        },
                    ),
                ],
            ),
        ],
    )
    counter = _tool(
        "Counterspell the saved cast using the interrupted caster's actual Constitution save.",
        [
            _entity("target_state"),
            _entity("caster_state"),
            _parameter("reaction_trigger_id"),
            _parameter("save_dc", "integer"),
        ],
        [
            _set("_counter_trigger", "state.pending_srd_actions[reaction_trigger_id]"),
            _call(
                "d20_test",
                {
                    "actor_state": "{_target_state_id}",
                    "ability": "constitution",
                    "kind": "saving_throw",
                    "ability_modifier": "{_counter_trigger.constitution_save_modifier}",
                    "proficiency_bonus": 0,
                    "proficiency_factor": "none",
                    "target_number": "{save_dc}",
                    "circumstantial_modifier": 0,
                    "advantage": False,
                    "disadvantage": False,
                },
                ["_d20_test", "_adjusted_total", "_outcome"],
            ),
            _when(
                "_outcome == 'failure'",
                [
                    _set("state.pending_srd_actions.{reaction_trigger_id}.status", "'lost'"),
                    _set(
                        "state.pending_srd_actions.{reaction_trigger_id}.countered_by",
                        "_caster_state_id",
                    ),
                ],
            ),
        ],
    )
    shield = _tool(
        "Project a single Shield until the caster's next turn, or six seconds outside combat.",
        [_entity("target_state")],
        [
            _set("_shield_active", False),
            _when(
                "target_state.reaction_shield != null",
                [
                    _call("read_srd_rest_clock", {"lasting_state": "state"}, ["_rest_now"]),
                    _set(
                        "_shield_active",
                        "(combat.encounter_active == true and "
                        "(target_state.turns_started == null or target_state.turns_started < "
                        "target_state.reaction_shield.expires_turn)) or "
                        "(combat.encounter_active != true and _rest_now + 0.000000001 < "
                        "target_state.reaction_shield.expires_tick)",
                    ),
                    _when("_shield_active != true", [_set("target_state.reaction_shield", None)]),
                ],
            ),
        ],
    )
    cast = _tool(
        "Resolve the exact reaction profile against its already validated saved trigger.",
        [
            _entity("caster_state"),
            _parameter("spell"),
            _parameter("reaction_trigger_id"),
            _parameter("save_dc", "integer"),
            _parameter("targets", "array", items={"type": "string"}, required=False),
            _parameter("actual_spell_level", "integer", required=False, default=1),
            _parameter("spell_cast_id", required=False, default=""),
        ],
        [
            _set("_reaction_trigger", "state.pending_srd_actions[reaction_trigger_id]"),
            _set("_spell_summary_rows", []),
            _set("_spell_detail_rows", []),
            _set("_reaction_outcome_text", "''"),
            _set("_reaction_result_target", "_caster_state_id"),
            _when(
                "spell == 'counterspell'",
                [
                    _call(
                        "resolve_srd_counterspell",
                        {
                            "target_state": "{_reaction_trigger.actor_state_id}",
                            "caster_state": "{_caster_state_id}",
                            "reaction_trigger_id": "{reaction_trigger_id}",
                            "save_dc": "{save_dc}",
                        },
                        ["_d20_test", "_adjusted_total", "_outcome"],
                    ),
                    _set("_reaction_result_target", "_reaction_trigger.actor_state_id"),
                    _call(
                        "record_srd_result_text",
                        {
                            "text": "Constitution save {_outcome}; "
                            "roll {_d20_test.results}; total {_adjusted_total}; DC {save_dc}."
                        },
                        ["_recorded_result_text"],
                    ),
                    _set("_reaction_outcome_text", "_recorded_result_text"),
                ],
            ),
            _when(
                "spell == 'shield'",
                [
                    _call("read_srd_rest_clock", {"lasting_state": "state"}, ["_rest_now"]),
                    _set("_shield_turns", 0),
                    _when(
                        "caster_state.turns_started != null",
                        [_set("_shield_turns", "caster_state.turns_started")],
                    ),
                    _set(
                        "caster_state.reaction_shield",
                        {
                            "expires_turn": "_shield_turns + 1",
                            "expires_tick": "_rest_now + 2",
                            "trigger_id": "reaction_trigger_id",
                        },
                    ),
                    _set(
                        "_reaction_outcome_text",
                        "'+5 AC until the next caster turn; no Magic Missile damage.'",
                    ),
                ],
            ),
            _when(
                "spell != 'feather_fall'",
                [
                    _call(
                        "record_srd_spell_target_result",
                        {
                            "target": "{_reaction_result_target}",
                            "brief": "{_reaction_outcome_text}",
                            "detail": "{_reaction_outcome_text}",
                        },
                        ["_spell_target_summary", "_spell_target_detail"],
                    ),
                    {
                        "list_push": {
                            "list": "_spell_summary_rows",
                            "value": "_spell_target_summary",
                        }
                    },
                    {"list_push": {"list": "_spell_detail_rows", "value": "_spell_target_detail"}},
                ],
            ),
            _when(
                "spell == 'feather_fall'",
                [
                    _call(
                        "install_srd_feather_fall",
                        {
                            "caster_state": "{_caster_state_id}",
                            "reaction_trigger_id": "{reaction_trigger_id}",
                            "targets": "{targets}",
                            "actual_spell_level": "{actual_spell_level}",
                            "spell_cast_id": "{spell_cast_id}",
                        },
                        ["_spell_summary_rows", "_spell_detail_rows"],
                    ),
                ],
            ),
        ],
    )
    return {
        "validate_srd_reaction_spell_trigger": validate,
        "resolve_srd_counterspell": counter,
        "resolve_srd_reaction_shield": shield,
        "cast_spell_reaction": cast,
    }


def _compose_reaction_consumers(tools, changed):
    """Use the shared cast, Armor Class and damage owners rather than duplicate their rules."""
    cast = deepcopy(tools["cast_spell"])
    if not any(p["name"] == "reaction_trigger_id" for p in cast["parameters"]):
        cast["parameters"].append(_parameter("reaction_trigger_id", required=False, default=""))
    if "Resolve the pending action before another ordinary spell" not in str(cast["effects"]):
        lookup_index = next(
            i
            for i, effect in enumerate(cast["effects"])
            if effect.get("store_as") == "_spell_definition"
        )
        cast["effects"].insert(
            lookup_index + 1,
            _require(
                "state.pending_srd_action_stack == null or "
                "len(state.pending_srd_action_stack) == 0 or "
                "_spell_definition.casting_time == 'reaction' or "
                "caster_state.pending_srd_completion_id != null",
                "Resolve the pending action before another ordinary spell.",
            ),
        )
    for effect in _walk(cast["effects"]):
        if effect.get("condition") == (
            "casting_mode == 'ritual' or "
            "(_spell_definition.casting_time != 'action' and "
            "_spell_definition.casting_time != 'bonus_action')"
        ):
            effect["condition"] = (
                "casting_mode == 'ritual' or "
                "(_spell_definition.casting_time != 'action' and "
                "_spell_definition.casting_time != 'bonus_action' and "
                "_spell_definition.casting_time != 'reaction')"
            )

    def remove_reaction_dispatch(effects):
        result = []
        for effect in effects:
            clause = effect.get("condition", "")
            if (
                "_spell_definition.family == 'reaction_spell'" in clause
                and any(
                    child.get("call") == "cast_spell_reaction"
                    for child in effect.get("on_true", [])
                )
            ) or clause == "_spell_definition.casting_time == 'reaction'":
                continue
            for branch in ("on_true", "on_false"):
                if branch in effect:
                    effect[branch] = remove_reaction_dispatch(effect[branch])
            result.append(effect)
        return result

    cast["effects"] = remove_reaction_dispatch(cast["effects"])
    budget_index = next(
        i for i, e in enumerate(cast["effects"]) if "spend_spell_casting_budget" in str(e)
    )
    cast["effects"].insert(
        budget_index,
        _when(
            "_spell_definition.casting_time == 'reaction'",
            [
                _call(
                    "validate_srd_reaction_spell_trigger",
                    {
                        "caster_state": "{_caster_state_id}",
                        "spell": "{spell}",
                        "reaction_trigger_id": "{reaction_trigger_id}",
                        "targets": "{targets}",
                    },
                ),
            ],
        ),
    )
    dispatch_index = next(
        i
        for i, e in enumerate(cast["effects"])
        if "_spell_definition.family ==" in e.get("condition", "")
    )
    source_indices = [
        i
        for i, e in enumerate(cast["effects"])
        if any(n.get("call") == "begin_srd_spell_source_instance" for n in _walk([e]))
    ]
    if source_indices:
        dispatch_index = max(dispatch_index, source_indices[-1] + 1)
    cast["effects"].insert(
        dispatch_index,
        _when(
            "_spell_definition.family == 'reaction_spell' and _spell_cast_failed != true",
            [
                _call(
                    "cast_spell_reaction",
                    {
                        "caster_state": "{_caster_state_id}",
                        "spell": "{spell}",
                        "reaction_trigger_id": "{reaction_trigger_id}",
                        "save_dc": "{_casting_profile.save_dc}",
                        "targets": "{targets}",
                        "actual_spell_level": "{_slot_level}",
                        "spell_cast_id": "{_spell_source_cast_id}",
                    },
                    ["_spell_summary_rows", "_spell_detail_rows"],
                ),
            ],
        ),
    )
    changed["cast_spell"] = cast
    armor = deepcopy(tools["resolve_srd_effective_armor_class"])
    if "resolve_srd_reaction_shield" not in str(armor["effects"]):
        armor["effects"].append(
            _when(
                "target_state.reaction_shield != null",
                [
                    _call(
                        "resolve_srd_reaction_shield",
                        {"target_state": "{_target_state_id}"},
                        ["_shield_active"],
                    ),
                    _when(
                        "_shield_active == true",
                        [_set("_effective_armor_class", "_effective_armor_class + 5")],
                    ),
                ],
            )
        )
    changed["resolve_srd_effective_armor_class"] = armor
    missile = deepcopy(tools["cast_spell_automatic_force_projectiles"])
    for effect in _walk(missile["effects"]):
        if effect.get("call") == "apply_srd_damage":
            effect["call"] = "apply_srd_magic_missile_damage"
    changed["cast_spell_automatic_force_projectiles"] = missile
    damage = deepcopy(tools["apply_srd_damage"])
    damage["parameters"][0].update(resolve="entity")
    delegated_parameters = {
        p["name"]: "{"
        + ("_" + p["name"] + "_id" if p.get("resolve") in {"entity", "participant"} else p["name"])
        + "}"
        for p in damage["parameters"]
    }
    delegated_parameters.update(
        target="{_target_id}", amount="{_missile_amount}", adjustment="{_missile_adjustment}"
    )
    damage["effects"] = [
        _set("_shield_active", False),
        _when(
            "target.reaction_shield != null",
            [
                _call(
                    "resolve_srd_reaction_shield",
                    {"target_state": "{_target_id}"},
                    ["_shield_active"],
                ),
            ],
        ),
        _set("_missile_amount", "amount"),
        _set("_missile_adjustment", "adjustment"),
        _when(
            "_shield_active == true",
            [
                _set("_missile_amount", 0),
                _set("_missile_adjustment", 0),
            ],
        ),
        _call(
            "apply_srd_damage",
            delegated_parameters,
            ["_hp_lost", "_temp_hp_absorbed", "_effective_damage", "_is_alive_after"],
        ),
    ]
    changed["apply_srd_magic_missile_damage"] = damage


def _public_reaction_cast(cast):
    """Expose off-turn reactions through a catalog entry validated against its saved trigger."""
    result = deepcopy(cast)
    result["description"] = "Cast a prepared Reaction spell against an actual saved trigger."
    next(p for p in result["parameters"] if p["name"] == "spell")["enum_values"] = sorted(PROFILES)
    result["pre_conditions"] = []
    result["effects"] = [
        _when(p["name"] + " == null", [_set("_" + p["name"] + "_id", None)])
        for p in result["parameters"]
        if p.get("resolve") in {"entity", "participant"} and p.get("required") is False
    ]
    parameters = {
        p["name"]: "{"
        + ("_" + p["name"] + "_id" if p.get("resolve") in {"entity", "participant"} else p["name"])
        + "}"
        for p in result["parameters"]
    }
    result["effects"].append(
        _call(
            "cast_spell",
            parameters,
            [
                "_spell_card_summary",
                "_spell_detail_rows",
                "_spell_summary_rows",
            ],
        )
    )
    return result


def bind_pending_support_fences(text):
    """Fence late emitted ordinary tools through the existing saved-window owner."""
    tools = _load_runtime(text)["tools"]
    changed = {}
    _ordinary_support_fences(tools, changed, LATE_SUPPORT_TOOLS)
    for name, tool in changed.items():
        if tools.get(name) != tool:
            text = _render_tool(text, name, tool)
    return text


def _ordinary_support_fences(tools, changed, public_tools=None):
    """Finish a saved reaction before another ordinary action changes its admitted facts."""
    message = "Complete the pending reaction window before another ordinary spell action."
    changed["require_srd_pending_window_closed"] = _tool(
        "Require the saved reaction window to finish before an ordinary support action.",
        [],
        [
            _require(
                "state.pending_srd_action_stack == null or "
                "len(state.pending_srd_action_stack) == 0",
                message,
            )
        ],
    )
    for name in ORDINARY_SUPPORT_TOOLS if public_tools is None else public_tools:
        if name not in tools:
            continue
        tool = deepcopy(changed.get(name, tools[name]))
        tool["effects"] = [
            e for e in tool["effects"] if e.get("require", {}).get("error") != message
        ]
        if "require_srd_pending_window_closed" not in str(tool["effects"]):
            tool["effects"].insert(0, _call("require_srd_pending_window_closed", {}))
        # Public wrappers retain their fixed source binding instead of exposing another selector.
        if name in {
            "designate_srd_compulsion_direction",
            "issue_srd_dominated_order",
            "resolve_srd_control_order",
            "resolve_srd_control_activity",
            "violate_srd_geas",
            "retarget_srd_eyebite",
            "resolve_srd_affliction_movement",
            "resolve_srd_fear_turn",
            "resolve_srd_confused_turn",
        }:
            tool["parameters"] = [p for p in tool["parameters"] if p["name"] != "lasting_state"]
        changed[name] = tool


def _commanded_reaction_consumer(tools, changed):
    """Consume Dominate's authorization only on its actual parent's target Reaction."""
    changed["consume_srd_dominated_reaction_authorization"] = _tool(
        "Validate and consume the commanded creature's saved parent authorization once.",
        [_entity("actor_state"), _entity("lasting_state")],
        [
            _when(
                "lasting_state.pending_srd_action_stack != null and "
                "len(lasting_state.pending_srd_action_stack) > 0",
                [
                    _set("_command_parent_id", "lasting_state.pending_srd_action_stack[-1]"),
                    _set(
                        "_command_parent", "lasting_state.pending_srd_actions[_command_parent_id]"
                    ),
                    _when(
                        "_command_parent.dominated_reaction_authorizations != null and "
                        "_actor_state_id in _command_parent.dominated_reaction_authorizations",
                        [
                            _set(
                                "_command_authorization",
                                "_command_parent.dominated_reaction_authorizations[_actor_state_id]",
                            ),
                            _require(
                                "_command_authorization.consumed != true",
                                "The commanded Reaction already consumed this authorization.",
                            ),
                            _set("_command_source_id", "_command_authorization.source_id"),
                            _set(
                                "_command_source",
                                "lasting_state.timed_condition_sources[_command_source_id]",
                            ),
                            _require(
                                "_command_source != null and _command_source.installed == true and "
                                "_command_source.suppressed != true and "
                                "_command_source.control_profile == 'dominate' and "
                                "_command_source.target_state_id == _actor_state_id and "
                                "_command_source.caster_state_id == "
                                "_command_authorization.caster_state_id and "
                                "'charmed' in actor_state.conditions",
                                "The commanded Reaction requires its active Dominate source.",
                            ),
                            _call(
                                "check_srd_selected_order_source",
                                {
                                    "target_state": "{_actor_state_id}",
                                    "source_id": "{_command_source_id}",
                                },
                            ),
                            _call(
                                "resolve_srd_scene_geometry_relation",
                                {
                                    "actor_state": "{_command_authorization.caster_state_id}",
                                    "target_state": "{_actor_state_id}",
                                },
                                ["_scene_same_plane"],
                            ),
                            _require(
                                "_scene_same_plane == true",
                                "Dominate's commanded Reaction requires the same plane.",
                            ),
                            _set(
                                "lasting_state.pending_srd_actions.{_command_parent_id}."
                                "dominated_reaction_authorizations.{_actor_state_id}.consumed",
                                True,
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    reaction = deepcopy(changed.get("consume_srd_reaction", tools["consume_srd_reaction"]))
    if "consume_srd_dominated_reaction_authorization" not in str(reaction["effects"]):
        reaction["effects"].insert(
            0,
            _call(
                "consume_srd_dominated_reaction_authorization",
                {"actor_state": "{_actor_state_id}", "lasting_state": "{_lasting_state_id}"},
            ),
        )
    changed["consume_srd_reaction"] = reaction


def _compose_opportunity_perception(tools, changed):
    """Retain actual two-way scene sight before an opportunity trigger is saved."""
    if "resolve_srd_opportunity_perception" not in tools:
        return
    trigger = deepcopy(tools["trigger_opportunity_attack"])
    if "_opportunity_actor_can_see_target" in str(trigger["effects"]):
        return
    trigger["pre_conditions"] = [
        condition
        for condition in trigger["pre_conditions"]
        if condition["condition"] != "actor_can_see_target == true"
    ]
    for effect in _walk(trigger["effects"]):
        pending = effect.get("set", {}).get("value")
        if isinstance(pending, dict) and pending.get("kind") == "opportunity_attack":
            pending["actor_can_see_target"] = "_opportunity_actor_can_see_target"
            pending["target_can_see_actor"] = "_opportunity_target_can_see_actor"
    trigger["effects"][:0] = [
        _set("_opportunity_actor_can_see_target", "actor_can_see_target"),
        _set("_opportunity_target_can_see_actor", "target_can_see_actor"),
        _when(
            "state.spatial_scene != null",
            [
                _call(
                    "resolve_srd_opportunity_perception",
                    {
                        "observer_state": "{_reactor_state_id}",
                        "target_state": "{_provoking_state_id}",
                    },
                    ["_observer_can_see_target"],
                ),
                _set("_opportunity_actor_can_see_target", "_observer_can_see_target"),
                _call(
                    "resolve_srd_opportunity_perception",
                    {
                        "observer_state": "{_provoking_state_id}",
                        "target_state": "{_reactor_state_id}",
                    },
                    ["_observer_can_see_target"],
                ),
                _set("_opportunity_target_can_see_actor", "_observer_can_see_target"),
            ],
        ),
        _require(
            "_opportunity_actor_can_see_target == true",
            "An Opportunity Attack requires seeing the provoking creature.",
        ),
    ]
    changed["trigger_opportunity_attack"] = trigger


def _compose_teleport_resolution(tools, changed):
    """Keep one paid instantaneous journey fenced through all saved damage and arrival stages."""
    if "start_srd_teleport_resolution" not in tools:
        return
    changed["hold_srd_teleport_resolution"] = _tool(
        "Bind only the actual paid Teleport to its saved resolution action.",
        [_entity("caster_state")],
        [
            _require(
                "caster_state.teleport_resolution != null and "
                "caster_state.teleport_resolution.paid == true and "
                "caster_state.teleport_resolution.status == 'pending'",
                "Only a paid pending Teleport owns a resolution window.",
            ),
            _set("_teleport_parent_id", None),
            _when(
                "caster_state.teleport_resolution.reaction_action_id != null",
                [
                    _set(
                        "_teleport_parent_id", "caster_state.teleport_resolution.reaction_action_id"
                    ),
                ],
            ),
            _when(
                "_teleport_parent_id == null",
                [
                    _require(
                        "state.pending_srd_action_stack == null or "
                        "len(state.pending_srd_action_stack) == 0",
                        "Finish the previous pending action before starting Teleport.",
                    ),
                    *_allocate("teleport_resolution", "_caster_state_id", "_caster_state_id"),
                    _set("_teleport_parent_id", "_pending_action_id"),
                    _set(
                        "caster_state.teleport_resolution.reaction_action_id", "_teleport_parent_id"
                    ),
                ],
            ),
            _require(
                "state.pending_srd_actions != null and "
                "_teleport_parent_id in state.pending_srd_actions",
                "Teleport must retain its actual saved casting action.",
            ),
            _set("_teleport_parent", "state.pending_srd_actions[_teleport_parent_id]"),
            _require(
                "_teleport_parent.status == 'open' and "
                "_teleport_parent.actor_state_id == _caster_state_id and "
                "(_teleport_parent.kind == 'spell_cast' or "
                "_teleport_parent.kind == 'teleport_resolution')",
                "This action does not own the paid Teleport.",
            ),
            _set("state.pending_srd_actions.{_teleport_parent_id}.kind", "'teleport_resolution'"),
            _set("state.pending_srd_actions.{_teleport_parent_id}.stage", "'paid_resolution'"),
        ],
    )
    start = deepcopy(tools["start_srd_teleport_resolution"])
    if "hold_srd_teleport_resolution" not in str(start["effects"]):
        start["effects"].append(
            _when(
                "caster_state.teleport_resolution.status == 'pending'",
                [
                    _call("hold_srd_teleport_resolution", {"caster_state": "{_caster_state_id}"}),
                ],
            )
        )
    changed["start_srd_teleport_resolution"] = start
    complete = changed["complete_srd_pending_action"]
    guard = _require(
        "_pending_action.kind != 'teleport_resolution'",
        "Continue the paid Teleport journey before closing its action.",
    )
    if guard not in complete["effects"]:
        index = next(
            i
            for i, effect in enumerate(complete["effects"])
            if effect.get("set", {}).get("field") == "_pending_action"
        )
        complete["effects"].insert(index + 1, guard)
    continuation = deepcopy(tools["continue_srd_teleport_resolution"])
    if "_teleport_resolution_window" in str(continuation["effects"]):
        return
    index = next(
        i
        for i, effect in enumerate(continuation["effects"])
        if effect.get("call") == "step_srd_teleport_resolution"
    )
    continuation["effects"][index:index] = [
        _set("_teleport_resolution_window", "caster_state.teleport_resolution.reaction_action_id"),
        _require(
            "_teleport_resolution_window != null and state.pending_srd_actions != null and "
            "_teleport_resolution_window in state.pending_srd_actions and "
            "state.pending_srd_actions[_teleport_resolution_window].kind == 'teleport_resolution' "
            "and state.pending_srd_actions[_teleport_resolution_window].status == 'open' and "
            "state.pending_srd_actions[_teleport_resolution_window].actor_state_id == "
            "_caster_state_id",
            "Continue only this caster's actual paid pending Teleport resolution window.",
        ),
        _set("_teleport_pending_stack", "state.pending_srd_action_stack"),
        _require(
            "len(_teleport_pending_stack) > 0 and "
            "_teleport_pending_stack[len(_teleport_pending_stack) - 1] == "
            "_teleport_resolution_window",
            "Finish the innermost pending action before continuing Teleport.",
        ),
    ]
    continuation["effects"].append(
        _when(
            "caster_state.teleport_resolution.status == 'completed'",
            [
                _set("_teleport_remaining_stack", []),
                {
                    "for_each": {
                        "collection": "_teleport_pending_stack",
                        "as": "_teleport_pending_id",
                        "do": [
                            _when(
                                "_teleport_pending_id != _teleport_resolution_window",
                                [
                                    {
                                        "list_push": {
                                            "list": "_teleport_remaining_stack",
                                            "value": "_teleport_pending_id",
                                        }
                                    },
                                ],
                            ),
                        ],
                    }
                },
                _set("state.pending_srd_action_stack", "_teleport_remaining_stack"),
                _set(
                    "state.pending_srd_actions.{_teleport_resolution_window}.status", "'completed'"
                ),
                _when(
                    "len(_teleport_remaining_stack) == 0", [_set("state.pending_srd_actions", {})]
                ),
            ],
        )
    )
    changed["continue_srd_teleport_resolution"] = continuation


def _compose_time_stop_reaction_permission(tools, changed):
    """Qualify the real saved reaction actor before its trigger is accepted."""
    if "qualify_srd_time_stop_actor" not in tools:
        return
    validate = changed["validate_srd_reaction_spell_trigger"]
    validate["effects"].insert(
        0,
        _when(
            "state.time_stop_source != null and state.time_stop_source.active == true",
            [
                _call(
                    "qualify_srd_time_stop_actor",
                    {"actor_state": "{_caster_state_id}"},
                    ["_time_stop_actor_can_act"],
                ),
                _require(
                    "_time_stop_actor_can_act == true",
                    "Only the Time Stop caster can react during its extra turns.",
                ),
            ],
        ),
    )


def extend_runtime(text):
    """Compose the action owner once, retaining its final live modifiers in saved continuations."""
    runtime = _load_runtime(text)
    tools = runtime["tools"]
    changed = {
        **_hostility_tools(),
        **_reaction_tools(),
        **_spell_sanctuary_tools(),
        **_sanctuary_decision_tools(tools),
        "settle_srd_countered_cast_use": _countered_use_tool(),
        "spend_spell_casting_budget": _casting_budget(),
        "begin_srd_scene_fall": _scene_fall_window(),
    }
    _compose_reaction_consumers(tools, changed)
    _compose_opportunity_perception(tools, changed)
    sanctuary = deepcopy(
        changed.get("check_srd_sanctuary_target", tools["check_srd_sanctuary_target"])
    )
    for branch in sanctuary["effects"]:
        if branch.get("condition") == "actor_state.pending_srd_completion_id != null":
            for effect in branch["on_true"]:
                if effect.get("set", {}).get("field") == "_sanctuary_bypass":
                    effect["set"]["value"] = (
                        "_sanctuary_bypass_event.spell_targeting_checked == true and "
                        "_sanctuary_bypass_event.target_state_id == _target_state_id"
                    )
    changed["check_srd_sanctuary_target"] = sanctuary
    clear = deepcopy(tools["clear_srd_encounter_transients"])
    if "creature.fighting_actor_ids" not in str(clear["effects"]):
        clear["effects"].append(_set("creature.fighting_actor_ids", []))
    changed["clear_srd_encounter_transients"] = clear
    for boundary in ("end_combat_turn", "advance_srd_lasting_time"):
        tool = deepcopy(tools[boundary])
        if "Complete the pending reaction" not in str(tool["effects"]):
            tool["effects"].insert(
                0,
                _require(
                    "state.pending_srd_action_stack == null or "
                    "len(state.pending_srd_action_stack) == 0",
                    "Complete the pending reaction window before advancing its turn or time.",
                ),
            )
        changed[boundary] = tool
    for owner_name, (begin_name, actor_name, kind) in ATTACK_OWNERS.items():
        owner = deepcopy(tools[owner_name])
        if "Complete the previous attack" not in str(owner["effects"]):
            owner["effects"].insert(
                0,
                _require(
                    "state.pending_srd_action_stack == null or "
                    "len(state.pending_srd_action_stack) == 0",
                    "Complete the previous attack's reaction window first.",
                ),
            )
        for item in _walk(owner["effects"]):
            if item.get("call") == "apply_srd_damage":
                item["params"].setdefault("source_state_id", "{_" + actor_name + "_id}")
        if "record_srd_hostile_actor_pair" not in str(owner["effects"]):
            owner["effects"].append(
                _call(
                    "record_srd_hostile_actor_pair",
                    {
                        "actor_state": "{_" + actor_name + "_id}",
                        "target_state": "{_target_id}",
                    },
                )
            )
        changed[owner_name] = owner
        changed.update(_attack_window(owner, begin_name, actor_name, kind))
    changed["resume_srd_sanctuary_attack"] = _resume_sanctuary_tool(changed)
    _spell_attack_hit_window(tools, changed)
    changed.update(_cast_window(changed["cast_spell"]))
    changed["cast_srd_reaction_spell"] = _public_reaction_cast(changed["cast_spell"])
    changed["complete_srd_pending_action"] = _complete_window(changed)
    changed["set_srd_pending_cast_completion"] = _complete_cast_window(
        changed["complete_srd_pending_action"],
        changed["cast_spell"],
    )
    _compose_teleport_resolution(tools, changed)
    changed.update(_extended_casting_windows(tools))
    _ordinary_support_fences(tools, changed)
    _commanded_reaction_consumer(tools, changed)
    _compose_time_stop_reaction_permission(tools, changed)
    schema = {
        "type": "object",
        "properties": {
            key: {"type": "integer" if type(value) is int else "string"}
            for key, value in PROFILES["shield"].items()
            if key != "family"
        },
        "required": [key for key in PROFILES["shield"] if key != "family"],
        "additionalProperties": False,
    }
    schema_text = yaml.dump(
        {"reaction_spell": schema}, Dumper=_RuntimeDumper, sort_keys=False, width=105
    )
    schema_text = "".join("      " + line for line in schema_text.splitlines(True))
    if "      reaction_spell:" not in text:
        text = text.replace(
            "      timed_actor_defense:", schema_text + "\n      timed_actor_defense:", 1
        )
    for name, tool in changed.items():
        normalized = _tool("", tool["parameters"], tool["effects"])
        tool["effects"] = normalized["effects"]
        # Spatial effects keep the package-owned scene path when state expressions are rebound.
        for effect in _walk(tool["effects"]):
            for kind in ("spatial_query", "spatial_mutation"):
                if kind in effect:
                    body = effect[kind]
                    body["scene"] = re.sub(r"^lasting_state\.", "state.", body["scene"])
        for parameter in tool["parameters"]:
            if parameter["name"] == "lasting_state":
                parameter["model_hidden"] = True
        text = _render_tool(text, name, tool)
    return text
