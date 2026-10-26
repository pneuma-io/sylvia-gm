"""Levitate altitude intent consumes real turns, saved bodies and existing action budgets."""

if __package__:
    from .srd_feather_fall import _sweep
    from .srd_scene_relations import _tool_scene
    from .srd_spell_travel_levitate import _body, _save_source, _source
    from .srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _when
    from .srd_timed_senses import _public
else:
    from srd_feather_fall import _sweep
    from srd_scene_relations import _tool_scene
    from srd_spell_travel_levitate import _body, _save_source, _source
    from srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _when
    from srd_timed_senses import _public


PUBLIC_TOOLS = {"adjust_srd_levitate_height"}


def control_tools(tools):
    """Change only actual altitude, with a cumulative twenty-foot allowance on the caster's turn."""
    if __package__:
        from .srd_spell_travel import ACTIVE, _placement, _scene_subject
    else:
        from srd_spell_travel import ACTIVE, _placement, _scene_subject

    control = _public(_tool_scene(
        "Change the exact Levitate subject's actual height on its caster's turn.",
        [_entity("actor_state"), _parameter("source_id"),
         _parameter("altitude_change", "number", minimum=-20, maximum=20)],
        [
            *_source(),
            _require("_levitate_source.active == true and "
                     "_levitate_source.caster == _actor_state_id and "
                     "state.lasting_tick < _levitate_source.expires_tick",
                     "Select this actual caster's current Levitate source."),
            _require(ACTIVE, "An Incapacitated caster cannot adjust Levitate."),
            _require("altitude_change != 0", "Choose an actual nonzero height change."),
            _require("combat.pending_action == null and "
                     "(state.pending_srd_action_stack == null or "
                     "len(state.pending_srd_action_stack) == 0)",
                     "Finish the actual pending action before adjusting Levitate."),
            _call("check_srd_condition_concentration", {
                "caster_state": "{_actor_state_id}", "sequence": "{_levitate_source.sequence}",
            }, ["_condition_concentration_active"]),
            _require("_condition_concentration_active == true and "
                     "actor_state.concentration_spell_id == 'levitate' and "
                     "actor_state.concentration_effect_family == 'travel_levitate'",
                     "Height control needs the exact active Levitate concentration."),
            *_scene_subject("{_actor_state_id}", "_levitate_control_caster"),
            *_body("_levitate_source.subject"),
            _set("_levitate_control_body", "_info_scene_entity"),
            _require("_levitate_control_body.metadata.plane == _levitate_source.plane and "
                     "_levitate_control_caster.metadata.plane == _levitate_source.plane and "
                     "_levitate_control_body.metadata.levitate_source_id == _levitate_source.id",
                     "Use the actual suspended body and caster on their source plane."),
            _call("measure_srd_information_distance", {
                "start": "{_levitate_control_caster.position}", "end": "{_info_position}",
            }, ["_scene_distance"]),
            _require("_levitate_control_body.id == _levitate_control_caster.id or "
                     "_scene_distance <= 60",
                     "Another Levitate target must still be within sixty feet of the caster."),
            _set("_levitate_control_mode", "'exploration'"),
            _set("_levitate_control_turn", "state.lasting_tick // 2"),
            _when("combat.encounter_active == true", [
                _require("combat.turn_active == true and combat.active_actor == _actor_state_id "
                         "and actor_state.turns_started != null",
                         "Height control belongs to the actual caster's active turn."),
                _set("_levitate_control_mode", "'combat'"),
                _set("_levitate_control_turn", "actor_state.turns_started"),
            ]),
            _set("_levitate_control_spent", 0),
            _when("_levitate_source.altitude_mode == _levitate_control_mode and "
                  "_levitate_source.altitude_turn == _levitate_control_turn", [
                _set("_levitate_control_spent", "_levitate_source.altitude_spent"),
            ]),
            _require("_levitate_control_spent + abs(altitude_change) <= 20",
                     "Levitate permits up to twenty feet of altitude control on this turn."),
            _set("_levitate_control_destination", ["_info_position[0]", "_info_position[1]",
                                                    "_info_position[2] + altitude_change"]),
            *_sweep("_levitate_control_destination"),
            _set("_travel_excluded", []),
            *_placement("{_levitate_source.subject}", "{_levitate_control_destination}",
                        "{_levitate_source.plane}"),
            _require("_travel_placement.distance == 0",
                     "Height control needs an actual clear and unoccupied vertical destination."),
            {
                "condition": "_levitate_control_body.id == _levitate_control_caster.id",
                "on_true": [_call("spend_srd_travel_movement", {
                    "actor_state": "{_actor_state_id}", "amount": "{abs(altitude_change)}",
                })],
                "on_false": [
                    _call("check_srd_active_control_action", {
                        "actor_state": "{_actor_state_id}", "action_kind": "action",
                        "magic_action": True,
                    }),
                    _when("combat.encounter_active == true", [
                        _call("spend_srd_action", {"magic_action": True}),
                    ]),
                ],
            },
            _set("_levitate_source_patch", {
                "movement_destination": "_levitate_control_destination",
                "altitude_mode": "_levitate_control_mode",
                "altitude_turn": "_levitate_control_turn",
                "altitude_spent": "_levitate_control_spent + abs(altitude_change)",
            }),
            _set("_levitate_source", "merge(_levitate_source, _levitate_source_patch)"),
            *_save_source(),
            _call("move_srd_travel_subject", {
                "subject": "{_levitate_source.subject}", "plane": "{_levitate_source.plane}",
                "destination": "{_levitate_control_destination}", "teleport": False,
                "levitate_source_id": "{_levitate_source.id}",
            }),
            _set("_levitate_source_patch", {"movement_destination": None}),
            _set("_levitate_source", "merge(_levitate_source, _levitate_source_patch)"),
            *_save_source(),
            _set("_levitate_control_height", "_levitate_control_destination[2]"),
            _set("_levitate_control_change", "altitude_change"),
        ],
    ), "exploration", "Adjust Levitate",
        [("Height", "_levitate_control_height"), ("Changed", "_levitate_control_change")])
    return {"adjust_srd_levitate_height": control}
