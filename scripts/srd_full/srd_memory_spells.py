"""Memory and dialogue spells retain source-owned speech, corpse and area consequences."""

from copy import deepcopy

import yaml

if __package__:
    from .srd_control_behaviors import (
        _call,
        _each,
        _entity,
        _lasting,
        _param,
        _push,
        _require,
        _set,
        _tool,
        _walk,
        _when,
    )
    from .srd_timed_senses import _public as _emit_public
    from .srd_timed_senses import _replace_tools
else:
    from srd_control_behaviors import (
        _call,
        _each,
        _entity,
        _lasting,
        _param,
        _push,
        _require,
        _set,
        _tool,
        _walk,
        _when,
    )
    from srd_timed_senses import _public as _emit_public
    from srd_timed_senses import _replace_tools


PROFILES = {}
for spell, level, distance, rounds, concentration, mode, save in (
    ("modify_memory", 5, 30, 10, True, "memory", "wisdom"),
    ("speak_with_dead", 3, 10, 100, False, "corpse", "none"),
    ("zone_of_truth", 2, 60, 100, False, "truth_zone", "charisma"),
):
    PROFILES[spell] = {
        "family": "memory_dialogue_spell",
        "casting_time": "action",
        "base_level": level,
        "range_kind": "distance",
        "range_feet": distance,
        "duration_rounds": rounds,
        "concentration": concentration,
        "dialogue_mode": mode,
        "save_ability": save,
    }

PUBLIC_TOOLS = {
    "record_srd_life_event",
    "recall_srd_life_event",
    "describe_srd_modified_memory",
    "ask_srd_dead_question",
    "speak_srd_truth_zone",
    "read_srd_truth_zone_outcomes",
}
CAST_CHOICES = [
    _param("memory_event_id", required=False, default=""),
    _param(
        "memory_mode",
        "enum",
        required=False,
        default="alter",
        enum_values=["erase", "recall", "alter", "create"],
    ),
    _param(
        "dialogue_position",
        "array",
        items={"type": "number"},
        min_items=3,
        max_items=3,
        required=False,
        default=[0, 0, 0],
    ),
]
POSITION_EXPORTS = ("_info_scene_entity", "_info_position")


def _public(tool, fields=None):
    parameter = tool["parameters"][0]
    variable = (
        "_" + parameter["name"] + "_id"
        if parameter.get("resolve") == "entity"
        else parameter["name"]
    )
    return _emit_public(
        tool, "exploration", "Memory and dialogue", [("Subject", variable), *(fields or [])]
    )


def validate_profiles(definitions):
    """Only the exact pinned fixed profiles enter this family."""
    for spell, definition in definitions.items():
        if definition.get("family") == "memory_dialogue_spell" and definition != PROFILES.get(
            spell
        ):
            raise SystemExit(f"Memory/dialogue profile differs from pinned source: {spell}")


def _schema():
    fields = {
        key: {
            "type": "boolean"
            if type(value) is bool
            else "integer"
            if type(value) is int
            else "string"
        }
        for profile in PROFILES.values()
        for key, value in profile.items()
        if key != "family"
    }
    return {
        "type": "object",
        "properties": fields,
        "required": list(fields),
        "additionalProperties": False,
    }


def _refresh(target="_target_state_id"):
    return _call(
        "refresh_srd_timed_condition_target",
        {
            "target_state": "{" + target + "}",
            "lasting_state": "{_lasting_state_id}",
        },
    )


def _source_ids():
    return [
        _set("_dialogue_ids", []),
        _when(
            "target_state.timed_condition_source_ids != null",
            [
                _set("_dialogue_ids", "target_state.timed_condition_source_ids"),
            ],
        ),
    ]


def _source_loop(effects):
    return _source_ids() + [
        _each(
            "_dialogue_ids",
            "_dialogue_id",
            [
                _set("_dialogue_source", "lasting_state.timed_condition_sources[_dialogue_id]"),
                *effects,
            ],
        )
    ]


def _record_tools():
    record = _tool(
        "Admit one actual life event or empty historical window; preserve its original truth.",
        [
            _entity("target_state"),
            _lasting(),
            _param("event_id"),
            _param("start_tick", "number", minimum=0),
            _param("end_tick", "number", minimum=0),
            _param("description", required=False, default=""),
            _param("window_only", "boolean", required=False, default=False),
        ],
        [
            _require(
                "start_tick <= end_tick and end_tick <= lasting_state.lasting_tick",
                "Life events must be a coherent interval in the actual past.",
            ),
            _require(
                "window_only == true or len(description) > 0",
                "An actual event needs its admitted true description.",
            ),
            _require(
                "target_state.is_alive == true or (target_state.death_tick != null and "
                "end_tick <= target_state.death_tick)",
                "A corpse cannot acquire knowledge after its actual death.",
            ),
            _when("target_state.life_events == null", [_set("target_state.life_events", {})]),
            _when("target_state.life_event_ids == null", [_set("target_state.life_event_ids", [])]),
            _require(
                "event_id not in target_state.life_event_ids",
                "An admitted original life event cannot be rewritten.",
            ),
            _set(
                "target_state.life_events.{event_id}",
                {
                    "id": "event_id",
                    "start_tick": "start_tick",
                    "end_tick": "end_tick",
                    "true_description": "description",
                    "perceived_description": "description",
                    "window_only": "window_only",
                    "modified": False,
                },
            ),
            _set("_life_event_ids", "target_state.life_event_ids"),
            _push("_life_event_ids", "event_id"),
            _set("target_state.life_event_ids", "_life_event_ids"),
        ],
        public=True,
    )
    recall = _tool(
        "Read the subject's actual saved perception of one admitted life event.",
        [_entity("target_state"), _param("event_id"), _lasting()],
        [
            _refresh(),
            _require(
                "target_state.life_event_ids != null and event_id in target_state.life_event_ids",
                "Select this subject's admitted life event.",
            ),
            _set("_recalled_memory", "target_state.life_events[event_id].perceived_description"),
        ],
        public=True,
    )
    return {
        "record_srd_life_event": _public(record),
        "recall_srd_life_event": _public(recall, [("Memory", "_recalled_memory")]),
    }


def _memory_tools():
    spell_targets = _tool(
        "Abort original memory work on the actual selected targets of another committed spell.",
        [
            _param("target_state", resolve="entity", required=False, default=None),
            _param("targets", "entity_id_list", resolve="entity_list", required=False, default=[]),
            _param(
                "attack_targets",
                "entity_id_list",
                resolve="entity_list",
                required=False,
                default=[],
            ),
            _param(
                "projectile_targets",
                "entity_id_list",
                resolve="entity_list",
                required=False,
                default=[],
            ),
            _lasting(),
        ],
        [
            _when(
                "target_state != null and target_state.has_memory_sources == true",
                [
                    _call("abort_srd_modified_memory", {"target_state": "{_target_state_id}"}),
                ],
            ),
            *[
                _each(
                    collection,
                    "_memory_spell_target_id",
                    [
                        _call(
                            "abort_srd_modified_memory_if_active",
                            {"target_state": "{_memory_spell_target_id}"},
                        ),
                    ],
                )
                for collection in ("targets", "attack_targets", "projectile_targets")
            ],
        ],
    )
    apply = _tool(
        "Apply one completed description at its actual source ending, once only.",
        [_entity("target_state"), _lasting(), _param("source_id")],
        [
            _set("_memory_ending", "lasting_state.timed_condition_sources[source_id]"),
            _when(
                "_memory_ending.memory_finished == true and "
                "_memory_ending.memory_aborted != true and "
                "_memory_ending.memory_applied != true",
                [
                    _set(
                        "_memory_event", "target_state.life_events[_memory_ending.memory_event_id]"
                    ),
                    _require(
                        "_memory_event != null", "The original admitted event must remain saved."
                    ),
                    _set(
                        "target_state.life_events.{_memory_ending.memory_event_id}.modified", True
                    ),
                    _set(
                        "target_state.life_events.{_memory_ending.memory_event_id}."
                        "perceived_description",
                        "_memory_ending.memory_description",
                    ),
                    _set(
                        "target_state.life_events.{_memory_ending.memory_event_id}.memory_source",
                        "_memory_ending.cast_id",
                    ),
                    _set("lasting_state.timed_condition_sources.{source_id}.memory_applied", True),
                    _set("target_state.has_modified_memories", True),
                ],
            ),
        ],
    )
    settle = _tool(
        "Settle only this memory or truth-area aspect before exact timed-source retirement.",
        [_lasting(), _param("source_id")],
        [
            _set("_memory_retiring", "lasting_state.timed_condition_sources[source_id]"),
            _when(
                "_memory_retiring.installed == true and "
                "_memory_retiring.dialogue_profile == 'memory'",
                [
                    _when(
                        "lasting_state.active_memory_sources != null",
                        [
                            {
                                "increment": {
                                    "field": "lasting_state.active_memory_sources",
                                    "amount": -1,
                                }
                            },
                            _set(
                                "lasting_state.has_memory_spells",
                                "lasting_state.active_memory_sources > 0",
                            ),
                        ],
                    ),
                    _call(
                        "apply_srd_completed_memory",
                        {
                            "target_state": "{_memory_retiring.target_state_id}",
                            "source_id": "{source_id}",
                            "lasting_state": "{_lasting_state_id}",
                        },
                    ),
                ],
            ),
            _when(
                "_memory_retiring.dialogue_profile == 'truth_zone'",
                [
                    _set(
                        "lasting_state.persistent_spell_areas."
                        "{_memory_retiring.truth_area_id}.active",
                        False,
                    ),
                    _set("_truth_retained_areas", []),
                    _set("_truth_still_active", False),
                    _set("_truth_retiring_areas", "lasting_state.persistent_spell_area_ids"),
                    _each(
                        "_truth_retiring_areas",
                        "_truth_retiring_area",
                        [
                            _when(
                                "_truth_retiring_area != _memory_retiring.truth_area_id",
                                [
                                    _push("_truth_retained_areas", "_truth_retiring_area"),
                                    _when(
                                        "lasting_state.persistent_spell_areas[_truth_retiring_area]."
                                        "profile == 'truth_zone' and "
                                        "lasting_state.persistent_spell_areas["
                                        "_truth_retiring_area].active == true",
                                        [_set("_truth_still_active", True)],
                                    ),
                                ],
                            )
                        ],
                    ),
                    _set("lasting_state.persistent_spell_area_ids", "_truth_retained_areas"),
                    _set("lasting_state.has_truth_zones", "_truth_still_active"),
                ],
            ),
            _set("lasting_state.timed_condition_sources.{source_id}.dialogue_profile", None),
        ],
    )
    projection = _tool(
        "Project Incapacitated and unawareness only from an effective Modify Memory charm.",
        [_entity("target_state"), _lasting()],
        [
            _set("_memory_link_active", False),
            _set("_memory_retained", False),
            _set(
                "_memory_other_incap", "target_state.timed_condition_unowned_incapacitated == true"
            ),
            *_source_loop(
                [
                    _when(
                        "_dialogue_source.installed == true and "
                        "_dialogue_source.dialogue_profile == 'memory'",
                        [_set("_memory_retained", True)],
                    ),
                    _when(
                        "_dialogue_source.installed == true and _dialogue_source."
                        "suppressed != true",
                        [
                            _when(
                                "_dialogue_source.dialogue_profile == 'memory' and "
                                "'charmed' in target_state.conditions",
                                [_set("_memory_link_active", True)],
                            ),
                            _when(
                                "_dialogue_source.condition == 'incapacitated' or "
                                "_dialogue_source.control_profile == 'hypnotic' or "
                                "_dialogue_source.control_profile == 'laughter'",
                                [
                                    _set("_memory_other_incap", True),
                                ],
                            ),
                        ],
                    ),
                ]
            ),
            _when(
                "_memory_link_active == true",
                [
                    {"apply_condition": {"target": "target_state", "condition": "incapacitated"}},
                ],
                [
                    _when(
                        "target_state.memory_link_incapacitated == true and "
                        "_memory_other_incap != true",
                        [
                            {
                                "remove_condition": {
                                    "target": "target_state",
                                    "condition": "incapacitated",
                                }
                            },
                        ],
                    ),
                ],
            ),
            _set("target_state.memory_link_incapacitated", "_memory_link_active"),
            _set("target_state.memory_unaware", "_memory_link_active"),
            _set("target_state.has_memory_sources", "_memory_retained"),
        ],
    )
    abort = _tool(
        "Damage or being targeted by another committed spell discards unfinished memory work.",
        [_entity("target_state"), _lasting()],
        _source_loop(
            [
                _when(
                    "_dialogue_source.installed == true and _dialogue_source."
                    "dialogue_profile == 'memory'",
                    [
                        _set(
                            "lasting_state.timed_condition_sources.{_dialogue_id}.memory_aborted",
                            True,
                        ),
                        _call(
                            "retire_srd_timed_condition_source",
                            {
                                "source_id": "{_dialogue_id}",
                                "lasting_state": "{_lasting_state_id}",
                            },
                        ),
                    ],
                ),
            ]
        )
        + [_refresh()],
    )
    describe = _tool(
        "Speak the complete changed event to the effective charmed listener in an actual language.",
        [
            _entity("caster_state"),
            _entity("target_state"),
            _lasting(),
            _param("source_id"),
            _param("description"),
            _param("language"),
        ],
        [
            _refresh(),
            _set("_describing_source", "lasting_state.timed_condition_sources[source_id]"),
            _require(
                "_describing_source.installed == true and "
                "_describing_source.dialogue_profile == 'memory' and "
                "_describing_source.target_state_id == _target_state_id and "
                "_describing_source.caster_state_id == _caster_state_id and "
                "_describing_source.suppressed != true and target_state.memory_unaware == true",
                "Select this caster's actual effective memory source.",
            ),
            _require(
                "caster_state.is_alive == true and 'unconscious' not in caster_state.conditions",
                "The caster must actually be able to speak.",
            ),
            _call(
                "resolve_srd_spell_language",
                {
                    "listener_state": "{_target_state_id}",
                    "speaker_state": "{_caster_state_id}",
                    "channel": "speech",
                    "perceived": True,
                    "language": "{language}",
                },
                ("_language_understands",),
            ),
            _require(
                "_language_understands == true",
                "The target must actually hear and understand the description.",
            ),
            _set("_memory_description", "description"),
            _when("_describing_source.memory_mode == 'erase'", [_set("_memory_description", "''")]),
            _when(
                "_describing_source.memory_mode == 'recall'",
                [
                    _set(
                        "_memory_description",
                        "target_state.life_events[_describing_source.memory_event_id]."
                        "true_description",
                    ),
                ],
            ),
            _set(
                "lasting_state.timed_condition_sources.{source_id}.memory_description",
                "_memory_description",
            ),
            _set("lasting_state.timed_condition_sources.{source_id}.memory_finished", True),
        ],
        public=True,
    )
    cure = _tool(
        "Restore original admitted memories and stop every ongoing "
        "memory alteration on this subject.",
        [_entity("target_state"), _lasting()],
        _source_loop(
            [
                _when(
                    "_dialogue_source.installed == true and _dialogue_source."
                    "dialogue_profile == 'memory'",
                    [
                        _set(
                            "lasting_state.timed_condition_sources.{_dialogue_id}.memory_aborted",
                            True,
                        ),
                        _call(
                            "retire_srd_timed_condition_source",
                            {
                                "source_id": "{_dialogue_id}",
                                "lasting_state": "{_lasting_state_id}",
                            },
                        ),
                    ],
                ),
            ]
        )
        + [
            _when(
                "target_state.life_events != null",
                [
                    _set("_memory_events", "target_state.life_event_ids"),
                    _each(
                        "_memory_events",
                        "_memory_event_id",
                        [
                            _when(
                                "target_state.life_events[_memory_event_id].modified == true",
                                [
                                    _set(
                                        "target_state.life_events.{_memory_event_id}."
                                        "perceived_description",
                                        "target_state.life_events[_memory_event_id].true_description",
                                    ),
                                    _set(
                                        "target_state.life_events.{_memory_event_id}.modified",
                                        False,
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _set("target_state.has_modified_memories", False),
            _refresh(),
        ],
    )
    return {
        "apply_srd_completed_memory": apply,
        "settle_srd_dialogue_source": settle,
        "resolve_srd_memory_projection": projection,
        "abort_srd_modified_memory": abort,
        "describe_srd_modified_memory": _public(describe),
        "restore_srd_true_memories": cure,
        "abort_srd_memory_cast_targets": spell_targets,
        "abort_srd_modified_memory_if_active": _tool(
            "Skip untouched actors at a selected spell target boundary.",
            [_entity("target_state"), _lasting()],
            [
                _when(
                    "target_state.has_memory_sources == true",
                    [
                        _call("abort_srd_modified_memory", {"target_state": "{_target_state_id}"}),
                    ],
                )
            ],
        ),
    }


def _corpse_tools():
    question = _tool(
        "Ask one of five questions of the actual saved corpse; it learns nothing after death.",
        [
            _entity("caster_state"),
            _entity("target_state"),
            _lasting(),
            _param("source_id"),
            _param("question"),
            _param("answer"),
            _param("language"),
            _param("answer_kind", "enum", enum_values=["truth", "lie", "evasion", "refusal"]),
            _param(
                "knowledge_event_ids",
                "array",
                items={"type": "string"},
                required=False,
                default=[],
                unique_items=True,
            ),
        ],
        [
            _refresh(),
            _set("_corpse_source", "lasting_state.timed_condition_sources[source_id]"),
            _require(
                "_corpse_source.installed == true and _corpse_source.dialogue_profile == 'corpse' "
                "and _corpse_source.caster_state_id == _caster_state_id and "
                "_corpse_source.target_state_id == _target_state_id and "
                "target_state.is_alive == false",
                "Select the actual still-active animating spirit; no soul is returned.",
            ),
            _require(
                "_corpse_source.questions_asked < 5", "The corpse has answered all five questions."
            ),
            _require(
                "target_state.body_mouth_present == true",
                "The actual corpse must still have a mouth.",
            ),
            _require(
                "language in target_state.languages", "The corpse knows only its life languages."
            ),
            _set("_corpse_antagonistic", "target_state.corpse_antagonistic == true"),
            _when(
                "target_state.fighting_actor_ids != null",
                [
                    _set(
                        "_corpse_antagonistic",
                        "_corpse_antagonistic == true or "
                        "_caster_state_id in target_state.fighting_actor_ids",
                    ),
                ],
            ),
            _require(
                "answer_kind != 'lie' or _corpse_antagonistic == true",
                "An admitted antagonistic corpse may lie; this spell does not compel enemy truth.",
            ),
            _require(
                "answer_kind != 'truth' or len(knowledge_event_ids) > 0",
                "A factual answer needs admitted knowledge from this creature's lifetime.",
            ),
            _each(
                "knowledge_event_ids",
                "_dead_event_id",
                [
                    _require(
                        "_dead_event_id in target_state.life_event_ids",
                        "The answer needs this creature's admitted lifetime knowledge.",
                    ),
                    _set("_dead_event", "target_state.life_events[_dead_event_id]"),
                    _require(
                        "_dead_event != null and _dead_event.window_only != true and "
                        "_dead_event.end_tick <= target_state.death_tick",
                        "A corpse cannot answer from post-death or invented knowledge.",
                    ),
                ],
            ),
            {
                "increment": {
                    "field": "lasting_state.timed_condition_sources.{source_id}.questions_asked",
                    "amount": 1,
                }
            },
            _set(
                "_corpse_dialogue",
                {
                    "question": "question",
                    "answer": "answer",
                    "kind": "answer_kind",
                    "language": "language",
                    "knowledge": "knowledge_event_ids",
                    "tick": "lasting_state.lasting_tick",
                },
            ),
            _set("_corpse_questions", "_corpse_source.questions"),
            _push("_corpse_questions", "_corpse_dialogue"),
            _set(
                "lasting_state.timed_condition_sources.{source_id}.questions", "_corpse_questions"
            ),
            _set(
                "_corpse_questions_asked",
                "lasting_state.timed_condition_sources[source_id].questions_asked",
            ),
        ],
        public=True,
    )
    return {
        "ask_srd_dead_question": _public(
            question,
            [
                ("Answer", "answer"),
                ("Kind", "answer_kind"),
                ("Asked", "_corpse_questions_asked"),
            ],
        )
    }


def _truth_tools():
    geometry = _tool(
        "Read actual actor footprint and plane against this source's fixed fifteen-foot Sphere.",
        [_entity("target_state"), _lasting(), _param("source_id")],
        [
            _set("_truth_geometry_source", "lasting_state.timed_condition_sources[source_id]"),
            _set(
                "_truth_area",
                "lasting_state.persistent_spell_areas[_truth_geometry_source.truth_area_id]",
            ),
            _set("_truth_inside", False),
            _call(
                "srd_information_scene_entity", {"subject": "{_target_state_id}"}, POSITION_EXPORTS
            ),
            _when(
                "_info_scene_entity.metadata.plane == _truth_area.plane",
                [
                    _call(
                        "resolve_srd_environment_body_point",
                        {
                            "anchor_subject": "{_target_state_id}",
                            "point": "{_truth_area.origin}",
                            "radius": 15,
                        },
                        ("_body_point_inside",),
                    ),
                    _set("_truth_inside", "_body_point_inside == true"),
                ],
            ),
        ],
    )
    event = _tool(
        "Settle the actual first entry or own turn start once, recording every real Charisma save.",
        [
            _entity("target_state"),
            _lasting(),
            _param("source_id"),
            _param("event", "enum", enum_values=["entry", "start"]),
            _param("crossed", "boolean", required=False, default=False, model_hidden=True),
        ],
        [
            _set("_truth_source", "lasting_state.timed_condition_sources[source_id]"),
            _when(
                "_truth_source.installed == true and "
                "_truth_source.dialogue_profile == 'truth_zone' and target_state.is_alive == true",
                [
                    _call(
                        "resolve_srd_truth_zone_geometry",
                        {
                            "target_state": "{_target_state_id}",
                            "source_id": "{source_id}",
                        },
                        ("_truth_inside",),
                    ),
                    _when(
                        "_truth_inside == true or (event == 'entry' and crossed == true)",
                        [
                            _set("_truth_clock", "'exploration'"),
                            _set("_truth_turn", "lasting_state.lasting_tick // 2"),
                            _when(
                                "combat.turn_active == true",
                                [
                                    _set("_truth_clock", "'combat'"),
                                    _set("_truth_turn", "combat.turn_sequence"),
                                ],
                            ),
                            _set("_truth_previous", None),
                            _when(
                                "_target_state_id in _truth_source.truth_receipts[event]",
                                [
                                    _set(
                                        "_truth_previous",
                                        "_truth_source.truth_receipts[event][_target_state_id]",
                                    ),
                                ],
                            ),
                            _when(
                                "_truth_previous == null or _truth_previous.clock != "
                                "_truth_clock or "
                                "_truth_previous.turn != _truth_turn",
                                [
                                    _set(
                                        "lasting_state.timed_condition_sources.{source_id}."
                                        "truth_receipts.{event}.{_target_state_id}",
                                        {"clock": "_truth_clock", "turn": "_truth_turn"},
                                    ),
                                    _call(
                                        "d20_test",
                                        {
                                            "actor_state": "{_target_state_id}",
                                            "ability": "charisma",
                                            "kind": "saving_throw",
                                            "ability_modifier": "{target_state."
                                            "saving_throw_modifiers.charisma}",
                                            "proficiency_bonus": 0,
                                            "proficiency_factor": "none",
                                            "circumstantial_modifier": 0,
                                            "target_number": "{_truth_source.save_dc}",
                                            "advantage": False,
                                            "disadvantage": False,
                                        },
                                        ("_d20_test", "_outcome"),
                                    ),
                                    _set("_truth_failed", "_truth_source.truth_failed_ids"),
                                    _when(
                                        "_outcome != 'success' and _target_state_id not in "
                                        "_truth_failed",
                                        [
                                            _push("_truth_failed", "_target_state_id"),
                                            _set(
                                                "lasting_state.timed_condition_sources.{source_id}."
                                                "truth_failed_ids",
                                                "_truth_failed",
                                            ),
                                        ],
                                    ),
                                    _set("_truth_outcomes", "_truth_source.truth_outcomes"),
                                    _push(
                                        "_truth_outcomes",
                                        {
                                            "target": "_target_state_id",
                                            "roll": "_d20_test",
                                            "outcome": "_outcome",
                                            "event": "event",
                                            "clock": "_truth_clock",
                                            "turn": "_truth_turn",
                                            "tick": "lasting_state.lasting_tick",
                                        },
                                    ),
                                    _set(
                                        "lasting_state.timed_condition_sources.{source_id}."
                                        "truth_outcomes",
                                        "_truth_outcomes",
                                    ),
                                    _set(
                                        "lasting_state.persistent_spell_areas."
                                        "{_truth_source.truth_area_id}.truth_outcomes",
                                        "_truth_outcomes",
                                    ),
                                    _set("target_state.truth_zone_aware", True),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    collect = _tool(
        "Discover this subject's actual truth-zone start or movement "
        "entry from existing area records.",
        [_entity("target_state"), _lasting(), _param("event")],
        [
            _set("_truth_area_ids", "lasting_state.persistent_spell_area_ids"),
            _each(
                "_truth_area_ids",
                "_truth_id",
                [
                    _set("_truth_area", "lasting_state.persistent_spell_areas[_truth_id]"),
                    _when(
                        "_truth_area.profile == 'truth_zone' and _truth_area.active == true",
                        [
                            _set(
                                "_truth_source",
                                "lasting_state.timed_condition_sources[_truth_area.source_id]",
                            ),
                            _when(
                                "_truth_source.installed == true and "
                                "_truth_source.cast_id == _truth_area.cast_id and "
                                "_truth_source.dialogue_profile == 'truth_zone' and lasting_state."
                                "lasting_tick < _truth_source.expires_tick",
                                [
                                    _call(
                                        "settle_srd_truth_zone_event",
                                        {
                                            "target_state": "{_target_state_id}",
                                            "source_id": "{_truth_area.source_id}",
                                            "event": "{event}",
                                        },
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    speech = _tool(
        "Speak with an admitted fiction classification; exact failed "
        "zones prevent deliberate lies only inside.",
        [
            _entity("target_state"),
            _lasting(),
            _param("statement"),
            _param(
                "statement_kind",
                "enum",
                enum_values=["truth", "evasion", "deliberate_lie", "silence"],
            ),
        ],
        [
            _set("_truth_blocks_lie", False),
            _when(
                "lasting_state.has_truth_zones == true",
                [
                    _set("_speech_area_ids", "lasting_state.persistent_spell_area_ids"),
                    _each(
                        "_speech_area_ids",
                        "_speech_area_id",
                        [
                            _set(
                                "_speech_area",
                                "lasting_state.persistent_spell_areas[_speech_area_id]",
                            ),
                            _when(
                                "_speech_area.profile == 'truth_zone' and _speech_area.active "
                                "== true",
                                [
                                    _set(
                                        "_speech_source",
                                        "lasting_state.timed_condition_sources[_speech_area.source_id]",
                                    ),
                                    _when(
                                        "_speech_source.installed == true and "
                                        "_speech_source.cast_id == _speech_area.cast_id and "
                                        "_speech_source.dialogue_profile == 'truth_zone' and "
                                        "lasting_state.lasting_tick < _speech_source.expires_tick "
                                        "and _target_state_id in _speech_source.truth_failed_ids",
                                        [
                                            _call(
                                                "resolve_srd_truth_zone_geometry",
                                                {
                                                    "target_state": "{_target_state_id}",
                                                    "source_id": "{_speech_area.source_id}",
                                                },
                                                ("_truth_inside",),
                                            ),
                                            _when(
                                                "_truth_inside == true",
                                                [_set("_truth_blocks_lie", True)],
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _require(
                "statement_kind != 'deliberate_lie' or _truth_blocks_lie != true",
                "This actor cannot speak a deliberate lie while inside its failed truth zone.",
            ),
            _set(
                "target_state.last_truth_zone_statement",
                {
                    "statement": "statement",
                    "kind": "statement_kind",
                    "tick": "lasting_state.lasting_tick",
                },
            ),
        ],
        public=True,
    )
    read = _tool(
        "Return only this caster's saved actual truth-zone outcomes, "
        "including later successful saves.",
        [
            _entity("caster_state"),
            _lasting(),
            _param("source_id"),
            _param("outcome_offset", "integer", required=False, default=0, minimum=0),
        ],
        [
            _set("_known_truth_source", "lasting_state.persistent_spell_areas[source_id]"),
            _require(
                "_known_truth_source.profile == 'truth_zone' and "
                "_known_truth_source.caster_state_id == _caster_state_id",
                "Only the actual caster receives this zone's save outcomes.",
            ),
            _set("_truth_known_outcomes", "_known_truth_source.truth_outcomes"),
            _set("_truth_known_rows", []),
            _set("_truth_known_count", 0),
            _set("_truth_page_slots", list(range(32))),
            _each(
                "_truth_page_slots",
                "_truth_page_slot",
                [
                    _set("_truth_outcome_index", "outcome_offset + _truth_page_slot"),
                    _when(
                        "_truth_outcome_index < len(_truth_known_outcomes)",
                        [
                            _set("_known_outcome", "_truth_known_outcomes[_truth_outcome_index]"),
                            _push(
                                "_truth_known_rows",
                                {
                                    "label": "_known_outcome.target",
                                    "value": "_known_outcome.outcome",
                                },
                            ),
                            _push(
                                "_truth_known_rows",
                                {"label": "'Roll'", "value": "_known_outcome.roll.total"},
                            ),
                            _push(
                                "_truth_known_rows",
                                {"label": "'Modifier'", "value": "_known_outcome.roll.modifier"},
                            ),
                            _push(
                                "_truth_known_rows",
                                {"label": "'Event'", "value": "_known_outcome.event"},
                            ),
                            _set("_truth_known_count", "_truth_known_count + 1"),
                        ],
                    ),
                ],
            ),
        ],
        public=True,
    )
    read = _public(read, [("Outcomes returned", "_truth_known_count")])
    read["metadata"]["result_card"]["details"] = {"path": "context._truth_known_rows"}
    return {
        "resolve_srd_truth_zone_geometry": geometry,
        "settle_srd_truth_zone_event": event,
        "collect_srd_truth_zone_events": collect,
        "speak_srd_truth_zone": _public(speech),
        "read_srd_truth_zone_outcomes": read,
    }


def _casting_tool():
    """Ordinary cast owner has already committed cost; invalid corpse targets have no effect."""
    parameters = [
        _entity("caster_state"),
        _entity("target_state"),
        _lasting(),
        _param("spell"),
        _param("slot_level", "integer"),
        _param("save_dc", "integer"),
        _param("concentration_save_modifier", "integer"),
        *deepcopy(CAST_CHOICES),
    ]

    def install(target, condition, caster):
        return _call(
            "install_srd_timed_condition_source",
            {
                "target_state": "{" + target + "}",
                "lasting_state": "{_lasting_state_id}",
                "spell": "{spell}",
                "condition": condition,
                "save_dc": "{save_dc}",
                "duration_rounds": "{_dialogue_profile.duration_rounds}",
                "repeat_save_timing": "none",
                "save_ability": "{_dialogue_save_ability}",
                "caster_state": "{" + caster + "}",
                "concentration_sequence": "{_dialogue_concentration}",
                "control_profile": "none",
                "actual_spell_level": "{slot_level}",
            },
            ("_timed_condition_source_id",),
        )

    effects = [
        _set("_dialogue_profile", "definitions.spells[spell]"),
        _set("_dialogue_save_ability", "_dialogue_profile.save_ability"),
        _when("_dialogue_save_ability == 'none'", [_set("_dialogue_save_ability", "'wisdom'")]),
        _set("_dialogue_concentration", 0),
        _set("_dialogue_installed", False),
        _set("_dialogue_result", "'No effect: invalid spell target.'"),
        _set("_dialogue_roll", None),
        _set("_dialogue_save_outcome", "''"),
        _set("_memory_targets", ["_target_state_id"]),
        _when(
            "spell == 'zone_of_truth'",
            [
                _call(
                    "resolve_srd_object_content_point",
                    {
                        "caster_state": "{_caster_state_id}",
                        "point": "{dialogue_position}",
                        "range_feet": 60,
                    },
                    ("_world_plane",),
                ),
                install("_caster_state_id", "none", "_caster_state_id"),
                _set("_dialogue_installed", True),
                _set(
                    "_truth_area_id",
                    "lasting_state.timed_condition_sources[_timed_condition_source_id].cast_id",
                ),
                _require(
                    "_truth_area_id != ''", "A truth zone needs its actual committed cast identity."
                ),
                _when(
                    "lasting_state.persistent_spell_area_ids == null",
                    [
                        _set("lasting_state.persistent_spell_area_ids", []),
                        _set("lasting_state.persistent_spell_areas", {}),
                    ],
                ),
                _set(
                    "lasting_state.persistent_spell_areas.{_truth_area_id}",
                    {
                        "profile": "'truth_zone'",
                        "active": True,
                        "origin": "dialogue_position",
                        "plane": "_world_plane",
                        "source_id": "_timed_condition_source_id",
                        "cast_id": "_truth_area_id",
                        "caster_state_id": "_caster_state_id",
                        "truth_outcomes": [],
                        "expires_tick": "lasting_state.timed_condition_sources["
                        "_timed_condition_source_id].expires_tick",
                    },
                ),
                _set("_truth_registered", "lasting_state.persistent_spell_area_ids"),
                _when(
                    "_truth_area_id not in _truth_registered",
                    [
                        _push("_truth_registered", "_truth_area_id"),
                        _set("lasting_state.persistent_spell_area_ids", "_truth_registered"),
                    ],
                ),
                _set("lasting_state.has_persistent_spell_areas", True),
                _set("lasting_state.has_truth_zones", True),
                _set(
                    "lasting_state.timed_condition_sources.{_timed_condition_source_id}.truth_area_id",
                    "_truth_area_id",
                ),
                _set(
                    "lasting_state.timed_condition_sources."
                    "{_timed_condition_source_id}.truth_failed_ids",
                    [],
                ),
                _set(
                    "lasting_state.timed_condition_sources."
                    "{_timed_condition_source_id}.truth_receipts",
                    {"entry": {}, "start": {}},
                ),
                _set(
                    "lasting_state.timed_condition_sources."
                    "{_timed_condition_source_id}.truth_outcomes",
                    [],
                ),
            ],
            [
                _call(
                    "resolve_srd_scene_geometry_relation",
                    {
                        "actor_state": "{_caster_state_id}",
                        "target_state": "{_target_state_id}",
                    },
                    ("_scene_distance", "_scene_same_plane", "_scene_line_of_sight"),
                ),
                _require(
                    "_scene_same_plane == true and _scene_distance <= _dialogue_profile.range_feet "
                    "and _scene_line_of_sight == true",
                    "Target must be in actual range with a clear path.",
                ),
            ],
        ),
        _when(
            "spell == 'speak_with_dead'",
            [
                _set("_corpse_recent", False),
                _when(
                    "target_state.last_speak_dead_tick != null",
                    [
                        _set(
                            "_corpse_recent",
                            "lasting_state.lasting_tick - "
                            "target_state.last_speak_dead_tick < 288000",
                        ),
                    ],
                ),
                _when(
                    "target_state.is_alive == false and target_state.death_tick != null",
                    [_set("target_state.last_speak_dead_tick", "lasting_state.lasting_tick")],
                ),
                _when(
                    "target_state.is_alive == false and target_state.body_mouth_present == true "
                    "and target_state.death_was_undead == false and target_state."
                    "death_tick != null "
                    "and _corpse_recent != true",
                    [
                        install("_target_state_id", "none", "_caster_state_id"),
                        _set("_dialogue_installed", True),
                        _set(
                            "lasting_state.timed_condition_sources."
                            "{_timed_condition_source_id}.questions_asked",
                            0,
                        ),
                        _set(
                            "lasting_state.timed_condition_sources."
                            "{_timed_condition_source_id}.questions",
                            [],
                        ),
                    ],
                ),
            ],
        ),
        _when(
            "spell == 'modify_memory'",
            [
                _call(
                    "validate_srd_charmer_harm",
                    {
                        "actor_state": "{_caster_state_id}",
                        "target_state": "{_target_state_id}",
                    },
                ),
                _require(
                    "target_state.life_event_ids != null and "
                    "memory_event_id in target_state.life_event_ids",
                    "Select the subject's admitted historical event or window.",
                ),
                _set("_memory_selected", "target_state.life_events[memory_event_id]"),
                _require(
                    "_memory_selected != null",
                    "Select the subject's admitted event or historical window.",
                ),
                _require(
                    "_memory_selected.end_tick - _memory_selected.start_tick <= 200",
                    "The selected event may last at most ten minutes.",
                ),
                _require(
                    "memory_mode == 'create' or _memory_selected.window_only != true",
                    "An empty historical window supports creating a memory only.",
                ),
                _set("_memory_lookback", 28800),
                _when("slot_level == 6", [_set("_memory_lookback", 201600)]),
                _when("slot_level == 7", [_set("_memory_lookback", 864000)]),
                _when("slot_level == 8", [_set("_memory_lookback", 10512000)]),
                _require(
                    "slot_level == 9 or lasting_state.lasting_tick - "
                    "_memory_selected.start_tick <= _memory_lookback",
                    "The selected event falls outside the actual slot's lookback.",
                ),
                _call(
                    "resolve_srd_scene_relation",
                    {
                        "actor_state": "{_caster_state_id}",
                        "target_state": "{_target_state_id}",
                    },
                    ("_scene_can_see",),
                ),
                _require(
                    "_scene_can_see == true", "The caster must actually see the memory target."
                ),
                _call(
                    "start_srd_condition_concentration",
                    {
                        "caster_state": "{_caster_state_id}",
                        "lasting_state": "{_lasting_state_id}",
                        "targets": "{_memory_targets}",
                        "spell": "{spell}",
                        "duration_rounds": 10,
                        "save_modifier": "{concentration_save_modifier}",
                    },
                    ("_condition_concentration_sequence",),
                ),
                _set("_dialogue_concentration", "_condition_concentration_sequence"),
                _call(
                    "check_srd_target_condition_immunity",
                    {
                        "target_state": "{_target_state_id}",
                        "condition": "charmed",
                        "source_state_id": "{_caster_state_id}",
                    },
                    ("_source_condition_immune", "_source_condition_save_advantage"),
                ),
                _when(
                    "_source_condition_immune == true",
                    [_set("_dialogue_result", "'Target immune to Charmed.'")],
                ),
                _when(
                    "_source_condition_immune != true",
                    [
                        _set("_memory_advantage", "_source_condition_save_advantage == true"),
                        _when(
                            "target_state.fighting_actor_ids != null",
                            [
                                _set(
                                    "_memory_advantage",
                                    "_memory_advantage == true or "
                                    "_caster_state_id in target_state.fighting_actor_ids",
                                ),
                            ],
                        ),
                        _call(
                            "d20_test",
                            {
                                "actor_state": "{_target_state_id}",
                                "ability": "wisdom",
                                "kind": "saving_throw",
                                "ability_modifier": "{target_state.saving_throw_modifiers.wisdom}",
                                "proficiency_bonus": 0,
                                "proficiency_factor": "none",
                                "circumstantial_modifier": 0,
                                "target_number": "{save_dc}",
                                "advantage": "{_memory_advantage}",
                                "disadvantage": False,
                                "save_avoids_condition": "charmed",
                            },
                            ("_d20_test", "_outcome"),
                        ),
                        _set("_dialogue_roll", "_d20_test"),
                        _set("_dialogue_save_outcome", "_outcome"),
                        _set("_dialogue_result", "'Saving throw succeeded.'"),
                        _when(
                            "_outcome != 'success'",
                            [
                                install("_target_state_id", "charmed", "_caster_state_id"),
                                _set("_dialogue_installed", True),
                                _set(
                                    "lasting_state.timed_condition_sources."
                                    "{_timed_condition_source_id}.memory_event_id",
                                    "memory_event_id",
                                ),
                                _set(
                                    "lasting_state.timed_condition_sources."
                                    "{_timed_condition_source_id}.memory_mode",
                                    "memory_mode",
                                ),
                                _set(
                                    "lasting_state.timed_condition_sources."
                                    "{_timed_condition_source_id}.memory_finished",
                                    False,
                                ),
                                _set(
                                    "lasting_state.timed_condition_sources."
                                    "{_timed_condition_source_id}.memory_aborted",
                                    False,
                                ),
                                _set(
                                    "lasting_state.timed_condition_sources."
                                    "{_timed_condition_source_id}.memory_applied",
                                    False,
                                ),
                                _set("target_state.has_memory_sources", True),
                                _when(
                                    "lasting_state.active_memory_sources == null",
                                    [_set("lasting_state.active_memory_sources", 0)],
                                ),
                                {
                                    "increment": {
                                        "field": "lasting_state.active_memory_sources",
                                        "amount": 1,
                                    }
                                },
                                _set("lasting_state.has_memory_spells", True),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        _when(
            "_dialogue_installed == true",
            [
                _set(
                    "lasting_state.timed_condition_sources."
                    "{_timed_condition_source_id}.dialogue_profile",
                    "_dialogue_profile.dialogue_mode",
                ),
                _set(
                    "lasting_state.timed_condition_sources."
                    "{_timed_condition_source_id}.dispel_ongoing",
                    True,
                ),
                _set("_dialogue_result", "'Source installed.'"),
                _when(
                    "spell == 'modify_memory'",
                    [
                        _refresh(),
                        _when(
                            "target_state.memory_link_incapacitated == true and "
                            "target_state.concentration_spell_id != null",
                            [
                                _call(
                                    "end_srd_concentration_node",
                                    {
                                        "source_state": "{_target_state_id}",
                                        "target_state": "{target_state."
                                        "concentration_target_state_id}",
                                    },
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        _set("_spell_summary_rows", [{"label": "spell", "value": "_dialogue_result"}]),
        _set("_spell_detail_rows", [{"label": "'Dialogue source'", "value": "_dialogue_result"}]),
        _when(
            "_dialogue_roll != null",
            [
                _push(
                    "_spell_detail_rows", {"label": "'Save roll'", "value": "_dialogue_roll.total"}
                ),
                _push(
                    "_spell_detail_rows",
                    {"label": "'Save modifier'", "value": "_dialogue_roll.modifier"},
                ),
                _push(
                    "_spell_detail_rows",
                    {"label": "'Save outcome'", "value": "_dialogue_save_outcome"},
                ),
            ],
        ),
        _when(
            "_dialogue_installed == true",
            [
                _push(
                    "_spell_detail_rows",
                    {"label": "'Source reference'", "value": "_timed_condition_source_id"},
                ),
                _when(
                    "spell == 'zone_of_truth'",
                    [
                        _push(
                            "_spell_detail_rows",
                            {"label": "'Zone reference'", "value": "_truth_area_id"},
                        )
                    ],
                ),
            ],
        ),
    ]
    # A corpse is an invalid creature target, but a legal committed casting still
    # spends its cost and starts Concentration; it never rolls or alters an event.
    memory = next(e for e in effects if e.get("condition") == "spell == 'modify_memory'")
    subject_effects = memory["on_true"]
    sight_at = next(
        i for i, e in enumerate(subject_effects) if e.get("call") == "resolve_srd_scene_relation"
    )
    sight_and_concentration = subject_effects[sight_at : sight_at + 4]
    del subject_effects[sight_at : sight_at + 4]
    memory["on_true"] = [
        *sight_and_concentration,
        _when("target_state.is_alive == true", subject_effects),
    ]
    return _tool(
        "Resolve actual saved memory, corpse or truth-area effects after named cast costs.",
        parameters,
        effects,
    )


def _effect_lists(effects):
    yield effects
    for effect in effects:
        for branch in ("on_true", "on_false"):
            if branch in effect:
                yield from _effect_lists(effect[branch])


def _strip(effects, names):
    def owned(effect):
        calls = {node["call"] for node in _walk(effect) if "call" in node}
        return bool(calls) and calls <= names

    for values in _effect_lists(effects):
        values[:] = [e for e in values if not owned(e)]


def _movement_tools(tools):
    """Reuse the proven box/sphere sweep, replacing only its fixed anchor with the saved area."""
    segment = deepcopy(tools["resolve_srd_environment_body_segment"])
    segment["description"] = (
        "Test this actual committed box-creature segment against the saved truth Sphere."
    )
    segment["parameters"] = [p for p in segment["parameters"] if p["name"] != "fixed_subject"]
    segment["parameters"] += [_param("source_id"), _lasting()]
    for values in _effect_lists(segment["effects"]):
        for index, effect in enumerate(values):
            if (
                effect.get("call") == "srd_information_scene_entity"
                and effect.get("params", {}).get("subject") == "{fixed_subject}"
            ):
                values[index] = _set(
                    "_info_scene_entity",
                    {
                        "position": "_truth_segment_area.origin",
                        "footprint": {"kind": "'box'", "half_extents": [0, 0]},
                        "height": 0,
                        "metadata": {"plane": "_truth_segment_area.plane"},
                    },
                )
            requirement = effect.get("require", {})
            if requirement.get("error") == "The segment must start at the actual moving creature.":
                requirement["condition"] = requirement["condition"].replace(
                    "_body_moving.position", "_body_moving.metadata.previous_position"
                )
                requirement["error"] = (
                    "The segment must start at this actor's actual committed previous position."
                )
    segment["effects"][:0] = [
        _set("_truth_segment_area", "lasting_state.persistent_spell_areas[source_id]"),
        _require(
            "_truth_segment_area.profile == 'truth_zone' and _truth_segment_area.active == true",
            "Select the actual saved truth area.",
        ),
    ]
    entry = _tool(
        "Discover real entries along the just-committed scene movement, including passing through.",
        [_entity("target_state"), _lasting()],
        [
            _call(
                "srd_information_scene_entity", {"subject": "{_target_state_id}"}, POSITION_EXPORTS
            ),
            _set("_truth_move_start", "_info_scene_entity.metadata.previous_position"),
            _set("_truth_move_end", "_info_position"),
            _require("_truth_move_start != null", "Entry needs the actual saved movement segment."),
            _set("_truth_move_areas", "lasting_state.persistent_spell_area_ids"),
            _each(
                "_truth_move_areas",
                "_truth_move_id",
                [
                    _set(
                        "_truth_move_area", "lasting_state.persistent_spell_areas[_truth_move_id]"
                    ),
                    _when(
                        "_truth_move_area.profile == 'truth_zone' and "
                        "_truth_move_area.active == true",
                        [
                            _set(
                                "_truth_move_source",
                                "lasting_state.timed_condition_sources[_truth_move_area.source_id]",
                            ),
                            _when(
                                "_truth_move_source.installed == true and "
                                "_truth_move_source.cast_id == _truth_move_area.cast_id and "
                                "_truth_move_source.dialogue_profile == 'truth_zone' and "
                                "lasting_state.lasting_tick < _truth_move_source.expires_tick",
                                [
                                    _call(
                                        "resolve_srd_truth_zone_segment",
                                        {
                                            "moving_subject": "{_target_state_id}",
                                            "source_id": "{_truth_move_id}",
                                            "start": "{_truth_move_start}",
                                            "end": "{_truth_move_end}",
                                            "radius": 15,
                                        },
                                        ("_body_hit", "_body_entry"),
                                    ),
                                    _when(
                                        "_body_hit == true and _body_entry > 0.000000000001 and "
                                        "_body_entry <= 1",
                                        [
                                            _call(
                                                "settle_srd_truth_zone_event",
                                                {
                                                    "target_state": "{_target_state_id}",
                                                    "source_id": "{_truth_move_area.source_id}",
                                                    "event": "entry",
                                                    "crossed": True,
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
    return {"resolve_srd_truth_zone_segment": segment, "collect_srd_truth_zone_movement": entry}


def extend_runtime(text):
    """Compose after environment geometry and before common source, damage and reactions."""
    runtime = yaml.load(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
    tools = runtime["tools"]
    changed = (
        _record_tools()
        | _memory_tools()
        | _corpse_tools()
        | _truth_tools()
        | _movement_tools(tools)
    )
    changed["cast_spell_memory_dialogue_spell"] = _casting_tool()

    installer = deepcopy(tools["install_srd_timed_condition_source"])
    for values in _effect_lists(installer["effects"]):
        values[:] = [
            effect
            for effect in values
            if effect.get("set", {}).get("field") != "lasting_state.timed_condition_sources."
            "{_allocated_timed_condition_source_id}.dialogue_profile"
        ]
    if installer != tools["install_srd_timed_condition_source"]:
        changed["install_srd_timed_condition_source"] = installer

    retire = deepcopy(tools["retire_srd_timed_condition_source"])
    _strip(retire["effects"], {"settle_srd_dialogue_source"})
    for values in _effect_lists(retire["effects"]):
        for index, effect in enumerate(list(values)):
            if effect.get("set", {}).get("field", "").endswith(".{source_id}.installed"):
                values.insert(
                    index,
                    _when(
                        "_retiring_condition_source.dialogue_profile != null",
                        [
                            _call(
                                "settle_srd_dialogue_source",
                                {
                                    "source_id": "{source_id}",
                                    "lasting_state": "{_lasting_state_id}",
                                },
                            ),
                        ],
                    ),
                )
    changed["retire_srd_timed_condition_source"] = retire
    projection = deepcopy(tools["resolve_srd_control_behavior_projection"])
    _strip(projection["effects"], {"resolve_srd_memory_projection"})
    projection["effects"].append(
        _when(
            "target_state.has_memory_sources == true",
            [
                _call("resolve_srd_memory_projection", {"target_state": "{_target_state_id}"}),
            ],
        )
    )
    changed["resolve_srd_control_behavior_projection"] = projection

    sight_name = (
        "resolve_srd_sight_node" if "resolve_srd_sight_node" in tools else "resolve_srd_sight"
    )
    sight = deepcopy(tools[sight_name])
    for node in _walk(sight["effects"]):
        setter = node.get("set", {})
        if setter.get("field") in {"_sight_can_see", "_sight_illusion_save_success"}:
            guard = "observer_state.memory_unaware != true"
            if guard not in setter["value"]:
                setter["value"] = "(" + setter["value"] + ") and " + guard
    changed[sight_name] = sight
    d20 = deepcopy(tools["d20_test"])
    awareness_guard = (
        "actor_state == null or actor_state.memory_unaware != true or "
        "kind != 'ability_check' or skill != 'perception'"
    )
    if not any(p["condition"] == awareness_guard for p in d20["pre_conditions"]):
        d20["pre_conditions"].append(
            {
                "condition": awareness_guard,
                "error": "Modify Memory leaves this subject unaware of surrounding stimuli.",
            }
        )
    changed["d20_test"] = d20
    passive = deepcopy(tools["read_srd_passive_perception"])
    passive_guard = "target_state.memory_unaware != true"
    if not any(p["condition"] == passive_guard for p in passive["pre_conditions"]):
        passive["pre_conditions"].append(
            {
                "condition": passive_guard,
                "error": "An unaware memory subject cannot notice surrounding stimuli.",
            }
        )
    changed["read_srd_passive_perception"] = passive

    damage = deepcopy(tools["resolve_srd_control_after_damage"])
    _strip(damage["effects"], {"abort_srd_modified_memory"})
    damage["effects"][:0] = [
        _when(
            "target_state.has_memory_sources == true",
            [
                _call("abort_srd_modified_memory", {"target_state": "{_target_state_id}"}),
            ],
        )
    ]
    changed["resolve_srd_control_after_damage"] = damage
    body = deepcopy(tools["record_srd_deceased_body"])
    if not any(p["name"] == "mouth_present" for p in body["parameters"]):
        body["parameters"].append(_param("mouth_present", "boolean", required=False, default=False))
        body["effects"].append(_set("target_state.body_mouth_present", "mouth_present"))
    changed["record_srd_deceased_body"] = body

    turn = deepcopy(tools["begin_combat_turn"])
    _strip(turn["effects"], {"collect_srd_truth_zone_events"})
    turn["effects"].append(
        _when(
            "lasting_state.has_truth_zones == true",
            [
                _call(
                    "collect_srd_truth_zone_events",
                    {"target_state": "{_actor_id}", "event": "start"},
                ),
            ],
        )
    )
    changed["begin_combat_turn"] = turn
    movement_name = (
        "move_srd_scene_creature_environment_segment"
        if "move_srd_scene_creature_environment_segment" in tools
        else "move_srd_scene_creature_node"
    )
    movement = deepcopy(tools[movement_name])
    _strip(movement["effects"], {"collect_srd_truth_zone_movement"})
    movement["effects"].append(
        _when(
            "lasting_state.has_truth_zones == true and _scene_movement_distance > 0",
            [
                _call("collect_srd_truth_zone_movement", {"target_state": "{_actor_state_id}"}),
            ],
        )
    )
    changed[movement_name] = movement

    recovery = deepcopy(tools["cast_srd_recovery"])
    _strip(recovery["effects"], {"restore_srd_true_memories"})
    recovery["effects"].append(
        _when(
            "(spell == 'greater_restoration' or "
            "(spell == 'remove_curse' and recovery_item == '')) and "
            "(target_state.has_memory_sources == true or "
            "target_state.has_modified_memories == true)",
            [
                _call("restore_srd_true_memories", {"target_state": "{_target_state_id}"}),
            ],
        )
    )
    changed["cast_srd_recovery"] = recovery
    for node in _walk(recovery["effects"]):
        requirement = node.get("require", {})
        if (
            "Select the Greater Restoration effect" in requirement.get("error", "")
            and "'modified_memory'" not in requirement["condition"]
        ):
            requirement["condition"] += " or recovery_choice == 'modified_memory'"

    cast = deepcopy(tools["cast_spell"])
    cast["parameters"] += [
        deepcopy(p)
        for p in CAST_CHOICES
        if not any(old["name"] == p["name"] for old in cast["parameters"])
    ]
    for values in _effect_lists(cast["effects"]):
        values[:] = [
            e
            for e in values
            if "_spell_definition.family == 'memory_dialogue_spell'" not in e.get("condition", "")
        ]
    _strip(cast["effects"], {"abort_srd_memory_cast_targets"})
    resolution = cast["effects"]
    # All casting facts and costs precede this proven family-dispatch boundary.
    # Inserting at the whole list's start would read a stale or absent definition.
    dispatch_at = next(
        i
        for i, effect in enumerate(resolution)
        if any(
            node.get("call") == "cast_spell_multi_target_timed_speed_bonus"
            for node in _walk(effect)
        )
    )
    memory_primary = [
        _set(
            "_memory_target_list_families",
            [
                "'multi_target_timed_speed_bonus'",
                "'multi_target_timed_hit_point_bonus'",
                "'instantaneous_attack_area_save_damage'",
                "'instantaneous_area_save_damage'",
                "'instantaneous_area_save_composite_damage'",
                "'instantaneous_multi_target_healing'",
                "'instantaneous_healing_pool'",
                "'timed_control_affliction'",
                "'timed_control_orders'",
                "'timed_control_behavior'",
                "'multi_target_timed_control'",
                "'multi_target_timed_repeating_save_condition'",
                "'multi_target_concentration_ability_advantage'",
                "'multi_target_concentration_d20_modifier'",
                "'divination_spell'",
                "'travel_spell'",
                "'illumination_spell'",
            ],
        ),
        _set("_memory_cast_targets", []),
        _set("_memory_cast_attacks", []),
        _set("_memory_cast_projectiles", []),
        _when(
            "targets != null and _spell_definition.family in _memory_target_list_families",
            [_set("_memory_cast_targets", "targets")],
        ),
        _when(
            "attack_targets != null and _spell_definition.family == 'multi_spell_attack_damage'",
            [_set("_memory_cast_attacks", "attack_targets")],
        ),
        _when(
            "projectile_targets != null and "
            "_spell_definition.family == 'automatic_force_projectiles'",
            [_set("_memory_cast_projectiles", "projectile_targets")],
        ),
        _set(
            "_memory_point_families",
            [
                "'instantaneous_area_save_damage'",
                "'instantaneous_area_save_composite_damage'",
                "'spell_environment'",
                "'created_spell_force'",
                "'created_spell_guardian'",
                "'created_spell_hound'",
                "'illumination_spell'",
            ],
        ),
        _set("_memory_effect_target", None),
        _when(
            "target != null and _spell_definition.range_kind != 'self'",
            [_set("_memory_effect_target", "_target_id")],
        ),
        _when(
            "_spell_definition.range_kind == 'self'",
            [_set("_memory_effect_target", "_caster_state_id")],
        ),
        _when(
            "_spell_definition.family in _memory_point_families or "
            "(_spell_definition.family == 'information_spell' and spell != 'tongues') or "
            "(_spell_definition.family == 'divination_spell' and spell != 'mind_spike')",
            [_set("_memory_effect_target", None)],
        ),
    ]
    resolution.insert(
        dispatch_at,
        _when(
            "_spell_cast_failed != true and lasting_state.has_memory_spells == true "
            "and spell != 'zone_of_truth' and _spell_definition.family != 'illusion_spell' "
            "and _spell_definition.family != 'object_content_spell' "
            "and _spell_definition.family != 'minor_world_spell'",
            [
                *memory_primary,
                _call(
                    "abort_srd_memory_cast_targets",
                    {
                        "target_state": "{_memory_effect_target}",
                        "targets": "{_memory_cast_targets}",
                        "attack_targets": "{_memory_cast_attacks}",
                        "projectile_targets": "{_memory_cast_projectiles}",
                    },
                ),
            ],
        ),
    )
    resolution.insert(
        dispatch_at + 1,
        _when(
            "_spell_cast_failed != true and _spell_definition.family == 'memory_dialogue_spell'",
            [
                _call(
                    "cast_spell_memory_dialogue_spell",
                    {
                        "caster_state": "{_caster_state_id}",
                        "target_state": "{_target_id}",
                        "spell": "{spell}",
                        "slot_level": "{_slot_level}",
                        "save_dc": "{_casting_profile.save_dc}",
                        "concentration_save_modifier": "{_casting_profile."
                        "concentration_save_modifier}",
                        **{p["name"]: "{" + p["name"] + "}" for p in CAST_CHOICES},
                    },
                    ("_spell_summary_rows", "_spell_detail_rows"),
                ),
            ],
        ),
    )
    changed["cast_spell"] = cast
    text = _replace_tools(text, changed)
    if "*d20_result_card" in text and "&d20_result_card" not in text:
        start = text.index("  d20_test:")
        index = text.index("      result_card:", start)
        text = text[:index] + text[index:].replace(
            "      result_card:", "      result_card: &d20_result_card", 1
        )
    if "      memory_dialogue_spell:" not in text:
        schema = yaml.safe_dump({"memory_dialogue_spell": _schema()}, sort_keys=False)
        text = text.replace(
            "      timed_senses:",
            "".join("      " + line for line in schema.splitlines(True)) + "\n      timed_senses:",
            1,
        )
    return text
