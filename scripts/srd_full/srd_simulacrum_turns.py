"""A source-owned Simulacrum subphase reuses actual turn budgets without advancing Initiative."""

from copy import deepcopy

if __package__:
    from .srd_control_behaviors import _when
    from .srd_scene_relations import _tool_scene
    from .srd_timed_actor_defenses import _call, _entity, _load_runtime, _parameter, _require, _set
    from .srd_timed_senses import _public, _replace_tools
else:
    from srd_control_behaviors import _when
    from srd_scene_relations import _tool_scene
    from srd_timed_actor_defenses import _call, _entity, _load_runtime, _parameter, _require, _set
    from srd_timed_senses import _public, _replace_tools

PUBLIC_TOOLS = {"begin_srd_simulacrum_turn", "end_srd_simulacrum_turn"}

# Only acting-creature budgets are saved; Initiative, clocks and pending source state stay live.
TURN_FIELDS = (
    "current_actor",
    "active_actor",
    "active_actor_kind",
    "active_actor_at_zero_hp",
    "active_actor_stable",
    "active_actor_can_act",
    "active_actor_prepared_spells",
    "active_actor_available_bonus_actions",
    "active_actor_spell_slow",
    "active_actor_form_wind_only_actions",
    "active_actor_form_restricted",
    "movement_remaining",
    "movement_spent",
    "movement_mode",
    "dashes_taken",
    "action_available",
    "normal_action_spent",
    "action_surge_available",
    "action_surge_used",
    "attacks_remaining",
    "attack_available",
    "multiattack_remaining",
    "light_attack_followup_available",
    "disengaged_actor",
    "turn_active",
    "phase",
    "step_wind_companion",
    "haste_action_selected",
    "haste_action_available",
    "haste_action_granted",
    "action_used_this_turn",
    "bonus_action_used_this_turn",
    "monk_bonus_strike_available",
    "monk_focus_action_available",
)


def bind_turn_consumers(text):
    """Extract final genuine start/end owners after their source hooks, before public Rx clones."""
    tools = _load_runtime(text)["tools"]
    begin = deepcopy(tools["begin_combat_turn"])
    end = deepcopy(tools["end_combat_turn"])
    for tool in (begin, end):
        actor = next(p for p in tool["parameters"] if p["name"] == "actor")
        actor.pop("from_state", None)
        actor.update(required=True, resolve="entity")
        tool["metadata"] = {
            "category": "internal",
            "visible_to_player": False,
            "costs_action": False,
        }
    # The actual actor starts once on the caster's turn; the shared turn identity stays unchanged.
    begin["effects"] = [
        e
        for e in begin["effects"]
        if e.get("set", {}).get("field") != "combat.turn_sequence"
        and e.get("condition") != "combat.turn_sequence == null"
    ]
    begin["pre_conditions"] = [
        p
        for p in begin.get("pre_conditions", [])
        if p.get("error") != "Use the creator's source-owned Simulacrum subphase."
    ]
    end["pre_conditions"] = [
        p
        for p in end.get("pre_conditions", [])
        if p.get("error") != "Finish the Simulacrum subphase before ending the creator's turn."
    ]
    changed = {
        "begin_srd_simulacrum_native_turn": begin,
        "end_srd_simulacrum_native_turn": end,
    }
    saved = [_set("caster_state.simulacrum_turn_budget", {})]
    restored = []
    for field in TURN_FIELDS:
        saved += [
            _set("caster_state.simulacrum_turn_budget." + field, None),
            _when(
                "combat." + field + " != null",
                [
                    _set("caster_state.simulacrum_turn_budget." + field, "combat." + field),
                ],
            ),
        ]
        restored += [
            _set("combat." + field, None),
            _when(
                "caster_state.simulacrum_turn_budget." + field + " != null",
                [
                    _set("combat." + field, "caster_state.simulacrum_turn_budget." + field),
                ],
            ),
        ]
    changed["begin_srd_simulacrum_turn"] = _public(
        _tool_scene(
            "Let the commanded duplicate act once during its creator's current turn, "
            "preserving spent creator budgets.",
            [_entity("caster_state"), _entity("duplicate_state"), _parameter("command")],
            [
                _call("require_srd_pending_window_closed", {}),
                _require(
                    "combat.encounter_active == true and combat.turn_active == true and "
                    "combat.active_actor == _caster_state_id and "
                    "combat.simulacrum_turn_source_id == null",
                    "A Simulacrum acts during its actual creator's active turn.",
                ),
                _require(
                    "state.time_stop_source == null or state.time_stop_source.active != true",
                    "A separate Simulacrum cannot act while Time Stop freezes other creatures.",
                ),
                _require(
                    "len(command) > 0",
                    "Retain the actual creator command for this source-owned subphase.",
                ),
                _call(
                    "read_srd_simulacrum_source",
                    {"target_state": "{_duplicate_state_id}"},
                    ["_sim_record", "_sim_active"],
                ),
                _require(
                    "_sim_active == true and _sim_record.caster == _caster_state_id and "
                    "duplicate_state.is_alive == true and duplicate_state.hp > 0",
                    "Choose the actual living duplicate of this creator.",
                ),
                _require(
                    "duplicate_state.simulacrum_last_caster_turn != caster_state.turns_started",
                    "This Simulacrum already acted on this creator turn.",
                ),
                *saved,
                _set("duplicate_state.simulacrum_last_caster_turn", "caster_state.turns_started"),
                _set("duplicate_state.simulacrum_command", "command"),
                _set("combat.simulacrum_turn_source_id", "_duplicate_state_id"),
                _set("combat.simulacrum_turn_caster_id", "_caster_state_id"),
                _set("combat.current_actor", "_duplicate_state_id"),
                _set("combat.turn_active", False),
                _call("begin_srd_simulacrum_native_turn", {"actor": "{_duplicate_state_id}"}),
            ],
        ),
        "combat",
        "Simulacrum acting",
        [("Command", "command")],
    )
    changed["end_srd_simulacrum_turn"] = _public(
        _tool_scene(
            "Resolve the duplicate's actual turn-end conditions, then restore its creator's "
            "existing budgets.",
            [_entity("caster_state"), _entity("duplicate_state")],
            [
                _call("require_srd_pending_window_closed", {}),
                _require(
                    "combat.simulacrum_turn_source_id == _duplicate_state_id and "
                    "combat.simulacrum_turn_caster_id == _caster_state_id and "
                    "combat.active_actor == _duplicate_state_id and "
                    "caster_state.simulacrum_turn_budget != null",
                    "End the exact actual Simulacrum subphase of this creator.",
                ),
                _call("end_srd_simulacrum_native_turn", {"actor": "{_duplicate_state_id}"}),
                *restored,
                _call(
                    "refresh_srd_timed_condition_target",
                    {"target_state": "{_caster_state_id}", "lasting_state": "state"},
                ),
                _set("combat.active_actor_at_zero_hp", "caster_state.hp == 0"),
                _set("combat.active_actor_stable", "caster_state.stable == true"),
                _set(
                    "combat.active_actor_can_act",
                    "caster_state.is_alive == true and caster_state.hp > 0 and "
                    "not ('incapacitated' in caster_state.conditions or 'paralyzed' in "
                    "caster_state.conditions or "
                    "'petrified' in caster_state.conditions or 'stunned' in "
                    "caster_state.conditions or "
                    "'unconscious' in caster_state.conditions)",
                ),
                _when(
                    "combat.active_actor_can_act != true", [_set("combat.action_available", False)]
                ),
                _set("caster_state.simulacrum_turn_budget", None),
                _set("combat.simulacrum_turn_source_id", None),
                _set("combat.simulacrum_turn_caster_id", None),
            ],
        ),
        "combat",
        "Creator turn resumed",
        [("Creator", "caster_state.name")],
    )
    ordinary_begin = deepcopy(tools["begin_combat_turn"])
    ordinary_end = deepcopy(tools["end_combat_turn"])
    for tool, condition, error in (
        (
            ordinary_begin,
            "actor.created_spell_id != 'simulacrum'",
            "Use the creator's source-owned Simulacrum subphase.",
        ),
        (
            ordinary_end,
            "combat.simulacrum_turn_source_id == null",
            "Finish the Simulacrum subphase before ending the creator's turn.",
        ),
    ):
        tool["pre_conditions"] = [
            p for p in tool.get("pre_conditions", []) if p.get("error") != error
        ]
        tool["pre_conditions"].append({"condition": condition, "error": error})
    changed["begin_combat_turn"] = ordinary_begin
    changed["end_combat_turn"] = ordinary_end
    return _replace_tools(text, changed)
