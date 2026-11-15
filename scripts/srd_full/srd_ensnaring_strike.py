"""Ensnaring Strike keeps its saved weapon hit separate from its retained vines."""

from copy import deepcopy

if __package__:
    from .srd_scene_relations import _spatial, _tool_scene
    from .srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _tool, _when
    from .srd_timed_senses import _loop, _optional, _public
else:
    from srd_scene_relations import _spatial, _tool_scene
    from srd_timed_actor_defenses import _call, _entity, _parameter, _require, _set, _tool, _when
    from srd_timed_senses import _loop, _optional, _public

PROFILE = {
    "base_level": 1,
    "range_kind": "self",
    "range_feet": 0,
    "concentration": True,
    "duration_rounds": 10,
    "rider_die": "d6",
    "rider_type": "piercing",
    "rider_base_dice": 0,
    "family": "attack_rider_spell",
    "casting_time": "bonus_action",
}
PUBLIC_TOOLS = {"escape_srd_ensnaring_strike"}
EXPORTS = ("_rider_source_id", "_rider_source", "_rider_live")


def _get():
    return _call("get_srd_attack_rider_source", {"source_id": "{source_id}"}, EXPORTS)


def _finish(caster="{_ensnaring_source.caster}"):
    return _call(
        "finish_srd_attack_rider_source", {"caster_state": caster, "source_id": "{source_id}"}
    )


def _immunity(target="{_target_state_id}", caster="{_ensnaring_source.caster}"):
    return _call(
        "check_srd_target_condition_immunity",
        {
            "target_state": target,
            "condition": "restrained",
            "source_state_id": caster,
            "source_is_magical": True,
        },
        ("_source_condition_immune", "_source_condition_save_advantage"),
    )


def _initial():
    return _tool(
        "Resolve the real hit creature's initial Strength save; Large or larger has Advantage.",
        [_entity("target_state"), _parameter("source_id")],
        [
            _get(),
            _require(
                "_rider_live == true and _rider_source.spell == 'ensnaring_strike' and "
                "_rider_source.target == _target_state_id and "
                "(target_state.creature_kind == 'character' or "
                "target_state.creature_kind == 'monster') and "
                "_rider_source.ensnaring_initial_resolved != true",
                "Resolve only this hit's exact unused paid Ensnaring Strike source.",
            ),
            _set("_ensnaring_source", "_rider_source"),
            _require(
                "target_state.size_rank != null and target_state.size_rank >= 0 and "
                "target_state.size_rank <= 5 and "
                "target_state.saving_throw_modifiers.strength != null",
                "Record the actual creature size category and Strength save modifier.",
            ),
            _immunity(),
            _set(
                "_ensnaring_save_advantage",
                "target_state.size_rank >= 3 or _source_condition_save_advantage == true",
            ),
            _call(
                "d20_test",
                {
                    "actor_state": "{_target_state_id}",
                    "kind": "saving_throw",
                    "ability": "strength",
                    "ability_modifier": "{target_state.saving_throw_modifiers.strength}",
                    "proficiency_bonus": 0,
                    "proficiency_factor": "none",
                    "circumstantial_modifier": 0,
                    "target_number": "{_ensnaring_source.save_dc}",
                    "advantage": "{_ensnaring_save_advantage}",
                    "disadvantage": False,
                    "forego_save": False,
                },
                ("_d20_test", "_outcome"),
            ),
            _set("_ensnaring_save", "_d20_test"),
            _set("_ensnaring_save_outcome", "_outcome"),
            _set("state.attack_rider_sources.{source_id}.ensnaring_initial_resolved", True),
            _set("state.attack_rider_sources.{source_id}.ensnaring_save", "_d20_test"),
            _set("state.attack_rider_sources.{source_id}.ensnaring_save_outcome", "_outcome"),
            {
                "condition": "_outcome == 'success'",
                "on_true": [_finish()],
                "on_false": [
                    _call(
                        "install_srd_timed_condition_source",
                        {
                            "target_state": "{_target_state_id}",
                            "lasting_state": "state",
                            "spell": "ensnaring_strike",
                            "condition": "restrained",
                            "save_dc": "{_ensnaring_source.save_dc}",
                            "duration_rounds": 10,
                            "repeat_save_timing": "none",
                            "save_ability": "strength",
                            "caster_state": "{_ensnaring_source.caster}",
                            "actual_spell_level": "{_ensnaring_source.slot_level}",
                            "spell_cast_id": "{source_id}",
                            "spell_cast_owner": "{_ensnaring_source.caster}",
                        },
                        ("_timed_condition_source_id",),
                    ),
                    _set(
                        "state.attack_rider_sources.{source_id}.condition_source_id",
                        "_timed_condition_source_id",
                    ),
                    _set(
                        "state.timed_condition_sources.{_timed_condition_source_id}.ensnaring_source_id",
                        "source_id",
                    ),
                    _call(
                        "refresh_srd_timed_condition_target",
                        {"target_state": "{_target_state_id}", "lasting_state": "state"},
                    ),
                ],
            },
        ],
    )


def _retire():
    """Retire only the linked condition; Concentration termination belongs to the rider owner."""
    return _tool(
        "Remove this exact vine restraint while preserving independent Restrained sources.",
        [_parameter("source_id")],
        [
            _set("_ensnaring_retire", "state.attack_rider_sources[source_id]"),
            _when(
                "_ensnaring_retire != null and _ensnaring_retire.spell == 'ensnaring_strike' and "
                "_ensnaring_retire.condition_source_id != null",
                [
                    _set(
                        "_ensnaring_condition",
                        "state.timed_condition_sources[_ensnaring_retire.condition_source_id]",
                    ),
                    _when(
                        "_ensnaring_condition.installed == true and "
                        "_ensnaring_condition.spell_id == 'ensnaring_strike' and "
                        "_ensnaring_condition.cast_id == source_id and "
                        "_ensnaring_condition.caster_state_id == _ensnaring_retire.caster and "
                        "_ensnaring_condition.ensnaring_source_id == source_id and "
                        "_ensnaring_condition.target_state_id == _ensnaring_retire.target",
                        [
                            _call(
                                "retire_srd_timed_condition_source",
                                {
                                    "source_id": "{_ensnaring_retire.condition_source_id}",
                                    "lasting_state": "state",
                                },
                            ),
                            _call(
                                "refresh_srd_timed_condition_target",
                                {
                                    "target_state": "{_ensnaring_retire.target}",
                                    "lasting_state": "state",
                                },
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _turn():
    return _tool(
        "Deal the saved slot's noncritical Piercing damage once at the target's start turn.",
        [_entity("target_state"), _parameter("source_id")],
        [
            _get(),
            _require(
                "_rider_live == true and _rider_source.spell == 'ensnaring_strike' and "
                "_rider_source.target == _target_state_id and combat.turn_active == true and "
                "combat.active_actor == _target_state_id and "
                "target_state.turns_started != null and "
                "target_state.turns_started > _rider_source.last_target_turn",
                "Use the actual target's new start turn and its exact live vines.",
            ),
            _set("_ensnaring_source", "_rider_source"),
            _set(
                "state.attack_rider_sources.{source_id}.last_target_turn",
                "target_state.turns_started",
            ),
            _set("_ensnaring_damage_roll", None),
            _set("_ensnaring_damage_taken", 0),
            _set(
                "_ensnaring_condition",
                "state.timed_condition_sources[_rider_source.condition_source_id]",
            ),
            _immunity(),
            _when(
                "_ensnaring_condition.installed == true and "
                "_ensnaring_condition.spell_id == 'ensnaring_strike' and "
                "_ensnaring_condition.condition == 'restrained' and "
                "_ensnaring_condition.cast_id == source_id and "
                "_ensnaring_condition.caster_state_id == _ensnaring_source.caster and "
                "_ensnaring_condition.ensnaring_source_id == source_id and "
                "_ensnaring_condition.target_state_id == _target_state_id and "
                "_source_condition_immune != true",
                [
                    _call(
                        "refresh_srd_timed_condition_target",
                        {"target_state": "{_target_state_id}", "lasting_state": "state"},
                    ),
                    _set("_ensnaring_before", "target_state.hp + target_state.temp_hp"),
                    {
                        "dice": {"expression": "{_ensnaring_source.slot_level}d6"},
                        "store_as": "_ensnaring_damage_roll",
                    },
                    _call(
                        "refresh_srd_timed_condition_target",
                        {"target_state": "{_ensnaring_source.caster}", "lasting_state": "state"},
                    ),
                    _call(
                        "modify_srd_actor_raw_damage_roll",
                        {
                            "actor_state": "{_ensnaring_source.caster}",
                            "amount": "{_ensnaring_damage_roll.total}",
                            "lasting_state": "state",
                        },
                        ("_ray_raw_damage",),
                    ),
                    _set("_ensnaring_amount", "max(0, _ray_raw_damage)"),
                    _set("_ensnaring_unallocated", "max(0, -_ray_raw_damage)"),
                    _call(
                        "apply_srd_qualifying_damage",
                        {
                            "target": "{_target_state_id}",
                            "amount": "{_ensnaring_amount}",
                            "damage_type": "piercing",
                            "adjustment": 0,
                            "source_state_id": "{_ensnaring_source.caster}",
                            "game_damage_roll": True,
                            "unallocated_roll_penalty": "{_ensnaring_unallocated}",
                        },
                    ),
                    _set(
                        "_ensnaring_damage_taken",
                        "max(0, _ensnaring_before - target_state.hp - target_state.temp_hp)",
                    ),
                ],
            ),
        ],
    )


def _escape():
    return _public(
        _tool_scene(
            "Spend an Action on Strength (Athletics) against the vine DC; a helper may assist.",
            [
                _entity("actor_state"),
                _parameter("source_id"),
                _optional("actor", "string", None, resolve="participant"),
            ],
            [
                _get(),
                _require(
                    "_rider_live == true and _rider_source.spell == 'ensnaring_strike' and "
                    "actor_state.is_alive == true and "
                    "'incapacitated' not in actor_state.conditions",
                    "Use an acting creature and the exact live vine source.",
                ),
                _set("_ensnaring_source", "_rider_source"),
                _set(
                    "_ensnaring_condition",
                    "state.timed_condition_sources[_rider_source.condition_source_id]",
                ),
                _require(
                    "_ensnaring_condition.installed == true and "
                    "_ensnaring_condition.spell_id == 'ensnaring_strike' and "
                    "_ensnaring_condition.condition == 'restrained' and "
                    "_ensnaring_condition.cast_id == source_id and "
                    "_ensnaring_condition.caster_state_id == _ensnaring_source.caster and "
                    "_ensnaring_condition.target_state_id == _ensnaring_source.target and "
                    "_ensnaring_condition.ensnaring_source_id == source_id and "
                    "_ensnaring_condition.suppressed != true",
                    "The selected vines must still actually restrain their hit recipient.",
                ),
                _immunity("{_ensnaring_source.target}"),
                _require(
                    "_source_condition_immune != true",
                    "Suppressed restraint cannot supply an escape check.",
                ),
                _when(
                    "_actor_state_id != _ensnaring_source.target",
                    [
                        _call(
                            "qualify_srd_ensnaring_help_reach",
                            {
                                "actor_state": "{_actor_state_id}",
                                "target_state": "{_ensnaring_source.target}",
                            },
                        ),
                    ],
                ),
                _require(
                    "combat.encounter_active == true and combat.turn_active == true and "
                    "combat.active_actor == _actor_state_id",
                    "Use this creature's actual combat Action for the escape attempt.",
                ),
                _set("_ensnaring_proficiency", "'none'"),
                {
                    "condition": "actor != null",
                    "on_true": [
                        _require(
                            "_actor_id == _actor_state_id",
                            "Use this acting character's own statistics.",
                        ),
                        _when(
                            "'athletics' in actor.data.skill_proficiencies",
                            [_set("_ensnaring_proficiency", "'normal'")],
                        ),
                    ],
                    "on_false": [
                        _require(
                            "actor_state.skill_check_modifiers.athletics != null",
                            "Record the actual creature Athletics modifier.",
                        ),
                    ],
                },
                _call("spend_srd_action", {"magic_action": False}),
                _set("combat.action_taken", "'escape_ensnaring_strike'"),
                {
                    "condition": "actor != null",
                    "on_true": [
                        _call(
                            "d20_test",
                            {
                                "actor": "{_actor_id}",
                                "actor_state": "{_actor_state_id}",
                                "ability": "strength",
                                "skill": "athletics",
                                "kind": "ability_check",
                                "proficiency_factor": "{_ensnaring_proficiency}",
                                "circumstantial_modifier": 0,
                                "target_number": "{_ensnaring_source.save_dc}",
                            },
                            ("_d20_test", "_outcome", "_adjusted_total"),
                        ),
                    ],
                    "on_false": [
                        _call(
                            "d20_test",
                            {
                                "actor_state": "{_actor_state_id}",
                                "ability": "strength",
                                "skill": "athletics",
                                "kind": "ability_check",
                                "ability_modifier": "{actor_state.skill_check_modifiers.athletics}",
                                "proficiency_bonus": 0,
                                "proficiency_factor": "none",
                                "circumstantial_modifier": 0,
                                "target_number": "{_ensnaring_source.save_dc}",
                            },
                            ("_d20_test", "_outcome", "_adjusted_total"),
                        ),
                    ],
                },
                _set("_ensnaring_escape_roll", "_d20_test"),
                _set("_ensnaring_escape_outcome", "_outcome"),
                _when("_outcome == 'success'", [_finish()]),
            ],
        ),
        "spell",
        "Escape Ensnaring Strike",
        [("Source", "source_id"), ("Outcome", "_ensnaring_escape_outcome")],
    )


def tools():
    return {
        "resolve_srd_ensnaring_strike_hit": _initial(),
        "retire_srd_ensnaring_condition": _retire(),
        "resolve_srd_ensnaring_strike_turn": _turn(),
        "escape_srd_ensnaring_strike": _escape(),
        "qualify_srd_ensnaring_help_reach": _help_reach(),
    }


def _help_reach():
    """Use exact nearest box points and an actual physical path, independently of sight."""
    effects = []
    for subject, alias in (
        ("_actor_state_id", "_ensnaring_helper"),
        ("_target_state_id", "_ensnaring_victim"),
    ):
        effects += [
            _call(
                "srd_information_scene_entity",
                {"subject": "{" + subject + "}"},
                ("_info_scene_entity",),
            ),
            _set(alias, "_info_scene_entity"),
            _require(
                alias + ".footprint.kind == 'box' and " + alias + ".metadata.plane != null",
                "Vine-help reach supports actual Box creature spaces with recorded planes.",
            ),
            _set(
                alias + "_min",
                [
                    alias + ".position[0] - " + alias + ".footprint.half_extents[0]",
                    alias + ".position[1] - " + alias + ".footprint.half_extents[1]",
                    alias + ".position[2]",
                ],
            ),
            _set(
                alias + "_max",
                [
                    alias + ".position[0] + " + alias + ".footprint.half_extents[0]",
                    alias + ".position[1] + " + alias + ".footprint.half_extents[1]",
                    alias + ".position[2] + " + alias + ".height",
                ],
            ),
        ]
    effects += [
        _require(
            "_ensnaring_helper.metadata.plane == _ensnaring_victim.metadata.plane",
            "The helper must reach vines on its actual plane.",
        ),
        _set("_ensnaring_reach", 5),
        _when(
            "actor_state.melee_reach != null", [_set("_ensnaring_reach", "actor_state.melee_reach")]
        ),
        _set(
            "_ensnaring_reach_from",
            [
                "min(_ensnaring_helper_max["
                + str(i)
                + "], max(_ensnaring_helper_min["
                + str(i)
                + "], _ensnaring_victim_min["
                + str(i)
                + "]))"
                for i in range(3)
            ],
        ),
        *[
            _when(
                "max(_ensnaring_helper_min["
                + str(i)
                + "], _ensnaring_victim_min["
                + str(i)
                + "]) <= min(_ensnaring_helper_max["
                + str(i)
                + "], _ensnaring_victim_max["
                + str(i)
                + "])",
                [
                    _set(
                        "_ensnaring_reach_from",
                        [
                            "(max(_ensnaring_helper_min["
                            + str(i)
                            + "], _ensnaring_victim_min["
                            + str(i)
                            + "]) + min(_ensnaring_helper_max["
                            + str(i)
                            + "], _ensnaring_victim_max["
                            + str(i)
                            + "])) * 0.5"
                            if j == i
                            else "_ensnaring_reach_from[" + str(j) + "]"
                            for j in range(3)
                        ],
                    )
                ],
            )
            for i in range(3)
        ],
        _set(
            "_ensnaring_reach_to",
            [
                "min(_ensnaring_victim_max["
                + str(i)
                + "], max(_ensnaring_victim_min["
                + str(i)
                + "], _ensnaring_reach_from["
                + str(i)
                + "]))"
                for i in range(3)
            ],
        ),
        _call(
            "measure_srd_information_distance",
            {"start": "{_ensnaring_reach_from}", "end": "{_ensnaring_reach_to}"},
            ("_scene_distance",),
        ),
        _require(
            "_ensnaring_reach > 0 and _scene_distance <= _ensnaring_reach",
            "The actual creature spaces must be within this helper's reach.",
        ),
        _spatial(
            "ray",
            {
                "map_id": "{state.spatial_scene.map.id}",
                "origin": "{_ensnaring_reach_from}",
                "destination": "{_ensnaring_reach_to}",
                "include": ["geometry", "entities"],
                "max_intersections": 256,
            },
            "_ensnaring_reach_ray",
        ),
        _require(
            "_ensnaring_reach_ray.truncated != true",
            "Vine-help path exceeds the admitted physical ray bound.",
        ),
        _loop(
            "_ensnaring_reach_ray.intersections",
            "_ensnaring_hit",
            [
                _when(
                    "_ensnaring_hit.id != _ensnaring_helper.id and "
                    "_ensnaring_hit.id != _ensnaring_victim.id",
                    [
                        _loop(
                            "state.spatial_scene.geometry",
                            "_ensnaring_blocker",
                            [
                                _require(
                                    "_ensnaring_hit.id != _ensnaring_blocker.id or "
                                    "_ensnaring_blocker.active != true or "
                                    "_ensnaring_blocker.movement != 'blocking' or "
                                    "(_ensnaring_blocker.metadata.plane != null and "
                                    "_ensnaring_blocker.metadata.plane != "
                                    "_ensnaring_helper.metadata.plane)",
                                    "The nearest reach path is blocked; reposition before helping.",
                                )
                            ],
                        ),
                        _loop(
                            "state.spatial_scene.entities",
                            "_ensnaring_blocker",
                            [
                                _require(
                                    "_ensnaring_hit.id != _ensnaring_blocker.id or "
                                    "_ensnaring_blocker.active != true or "
                                    "_ensnaring_blocker.movement != 'blocking' or "
                                    "_ensnaring_blocker.metadata.plane != "
                                    "_ensnaring_helper.metadata.plane",
                                    "Another body blocks the supported nearest reach path.",
                                )
                            ],
                        ),
                    ],
                ),
            ],
        ),
    ]
    return _tool_scene(
        "Qualify actual Box-space helper reach and its nearest physical path.",
        [_entity("actor_state"), _entity("target_state")],
        effects,
    )


def bind_condition_projection(original):
    """Join only this spell's exact Restrained source at the existing condition projector."""
    tool = deepcopy(original)
    marker = "ensnaring_strike_projection_v1"
    if tool.get("metadata", {}).get(marker):
        return tool
    found = 0

    def visit(value):
        nonlocal found
        if isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, dict):
            if value.get("condition") == "_timed_condition_source.condition == 'restrained'":
                found += 1
                previous = deepcopy(value["on_true"])
                value["on_true"] = [
                    {
                        "condition": "_timed_condition_source.spell_id == 'ensnaring_strike'",
                        "on_true": [
                            _when(
                                "_timed_condition_source.ensnaring_source_id != null",
                                [
                                    _call(
                                        "get_srd_attack_rider_source",
                                        {
                                            "source_id": (
                                                "{_timed_condition_source.ensnaring_source_id}"
                                            )
                                        },
                                        EXPORTS,
                                    ),
                                    _immunity(
                                        "{_target_state_id}",
                                        "{_timed_condition_source.caster_state_id}",
                                    ),
                                    _when(
                                        "_rider_live == true and "
                                        "_rider_source.target == _target_state_id and "
                                        "_timed_condition_source.cast_id == "
                                        "_rider_source.cast_id and "
                                        "_timed_condition_source.caster_state_id == "
                                        "_rider_source.caster and "
                                        "_source_condition_immune != true and "
                                        "_timed_condition_source.suppressed != true",
                                        [_set("_timed_restrained", True)],
                                    ),
                                ],
                            )
                        ],
                        "on_false": previous,
                    }
                ]
                return
            for child in value.values():
                visit(child)

    visit(tool["effects"])
    if found != 1:
        raise ValueError("Ensnaring Strike requires the actual single Restrained projection owner.")
    tool.setdefault("metadata", {})[marker] = True
    return tool
