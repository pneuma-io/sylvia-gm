"""Pinned divinations share saved question, communication and remote-observation owners."""

import re
from copy import deepcopy

import yaml

if __package__:
    from .srd_animal_messenger import deepen_courier_tools
    from .srd_divination_continuation import deepen_divination_cast, deepen_divination_tools
    from .srd_identify_object_sources import deepen_identify_object_sources
    from .srd_information_spells import _enum
    from .srd_mind_spike_feedback import deepen_mind_spike_feedback
    from .srd_scene_relations import _array, _snapshot, _tool_scene
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
    from .srd_timed_senses import _lookup, _loop, _optional, _public, _push, _replace_tools
else:
    from srd_animal_messenger import deepen_courier_tools
    from srd_divination_continuation import deepen_divination_cast, deepen_divination_tools
    from srd_identify_object_sources import deepen_identify_object_sources
    from srd_information_spells import _enum
    from srd_mind_spike_feedback import deepen_mind_spike_feedback
    from srd_scene_relations import _array, _snapshot, _tool_scene
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
    from srd_timed_senses import _lookup, _loop, _optional, _public, _push, _replace_tools


SOURCE = {
    "animal_messenger": (108, "Animal Messenger", "Animal Shapes"),
    "augury": (111, "Augury", "Aura of Life"),
    "commune": (116, "Commune", "Commune with Nature"),
    "commune_with_nature": ([116, 117], "Commune with Nature", "Comprehend Languages"),
    "contact_other_plane": (119, "Contact Other Plane", "Contagion"),
    "detect_thoughts": (123, "Detect Thoughts", "Dimension Door"),
    "divination": (125, "Divination", "Divine Favor"),
    "identify": (142, "Identify", "Illusory Script"),
    "legend_lore": (144, "Legend Lore", "Lesser Restoration"),
    "message": ([148, 149], "Message", "Meteor Swarm"),
    "mind_spike": (149, "Mind Spike", "Minor Illusion"),
    "scrying": (160, "Scrying", "Searing Smite"),
    "sending": (161, "Sending", "Sequester"),
    "telepathic_bond": (168, "Telepathic Bond", "Teleport"),
}
PROFILES = {}
for _id, _level, _casting, _range, _feet, _rounds, _concentration, _mode in (
    ("animal_messenger", 2, "action", "distance", 30, 14400, False, "courier"),
    ("augury", 2, "1_minute", "self", 0, 0, False, "omen"),
    ("commune", 5, "1_minute", "self", 0, 10, False, "questions"),
    ("commune_with_nature", 5, "1_minute", "self", 0, 0, False, "nature"),
    ("contact_other_plane", 5, "1_minute", "self", 0, 10, False, "questions"),
    ("detect_thoughts", 2, "action", "self", 0, 10, True, "thoughts"),
    ("divination", 4, "action", "self", 0, 0, False, "prophecy"),
    ("identify", 1, "1_minute", "touch", 0, 0, False, "identify"),
    ("legend_lore", 5, "10_minutes", "self", 0, 0, False, "lore"),
    ("message", 0, "action", "distance", 120, 1, False, "message"),
    ("mind_spike", 2, "action", "distance", 120, 600, True, "track"),
    ("scrying", 5, "10_minutes", "self", 0, 100, True, "scry"),
    ("sending", 3, "action", "unlimited", 0, 0, False, "sending"),
    ("telepathic_bond", 5, "action", "distance", 30, 600, False, "bond"),
):
    PROFILES[_id] = {
        "family": "divination_spell",
        "base_level": _level,
        "casting_time": _casting,
        "range_kind": _range,
        "range_feet": _feet,
        "effect_rounds": _rounds,
        "concentration": _concentration,
        "divination_mode": _mode,
    }
    if _rounds:
        PROFILES[_id]["duration_rounds"] = _rounds

PUBLIC_TOOLS = {
    "ask_srd_divination_question",
    "reply_srd_spell_message",
    "communicate_srd_telepathic_bond",
    "query_srd_nature_divination",
    "read_srd_scrying_sensor",
    "activate_srd_detect_thoughts",
    "contest_srd_detect_thoughts",
    "query_srd_mind_spike",
    "advance_srd_animal_messenger",
    "dismiss_srd_divination_spell",
    "resolve_srd_sight",
}
ACTIVE = (
    "caster_state.is_alive == true and 'incapacitated' not in caster_state.conditions and "
    "'paralyzed' not in caster_state.conditions and 'petrified' not in caster_state.conditions and "
    "'stunned' not in caster_state.conditions and 'unconscious' not in caster_state.conditions"
)
PENDING = "state.pending_srd_action_stack == null or len(state.pending_srd_action_stack) == 0"


def validate_profiles(definitions):
    """The admitted fixed profile must match the reviewed source exactly."""
    for spell, definition in definitions.items():
        if definition.get("family") == "divination_spell" and definition != PROFILES.get(spell):
            raise SystemExit(f"Divination source profile differs: {spell}")


def _words(name="message_words", maximum=25, default=None):
    """Token arrays make the source word cap executable without adding an expression builtin."""
    item = _parameter(name, "array", items={"type": "string"}, min_items=1, max_items=maximum)
    if default is not None:
        item.update(required=False, default=default, min_items=0)
    return item


def _action(actor="caster_state", bonus=False):
    """Later Magic actions use the real combat budget and are ordinary pending-window actions."""
    return [
        _require(PENDING, "Complete the pending action before another ordinary action."),
        _require(ACTIVE.replace("caster_state", actor), "An Incapacitated creature cannot act."),
        _when(
            "combat.encounter_active == true",
            [
                _require(
                    f"combat.active_actor == _{actor}_id and combat.turn_active == true",
                    "Act on this creature's current combat turn.",
                ),
                _call(
                    "consume_srd_bonus_action" if bonus else "spend_srd_action",
                    {"actor_state": "{_" + actor + "_id}", "bonus_action": "divination"}
                    if bonus
                    else {"magic_action": True},
                ),
            ],
        ),
    ]


def _record_tools():
    check = _tool(
        "Validate one exact saved divination source against its clock and concentration.",
        [_entity("caster_state"), _parameter("record", "object", from_state="state.turn_effects")],
        [
            _set(
                "_divination_active",
                "record.kind == 'divination_spell' and state.lasting_tick < record.expires_tick",
            ),
            _when(
                "record.concentration == true",
                [
                    _set(
                        "_divination_active",
                        "_divination_active == true and "
                        + ACTIVE
                        + " and caster_state.concentration_spell_id == record.spell and "
                        "caster_state.concentration_effect_family == 'divination_spell' and "
                        "caster_state.concentration_started_tick == record.started_tick",
                    ),
                ],
            ),
        ],
    )
    collect = _tool(
        "Select the current active caster-owned divination record.",
        [_entity("caster_state"), _parameter("spell"), _optional("instance_id", default="")],
        [
            _set("_divination_record", None),
            _set("_divination_records", []),
            _when(
                "state.turn_effects != null", [_set("_divination_records", "state.turn_effects")]
            ),
            _loop(
                "_divination_records",
                "_divination_candidate",
                [
                    _when(
                        "_divination_candidate.kind == 'divination_spell' and "
                        "_divination_candidate.caster == _caster_state_id and "
                        "_divination_candidate.spell == spell and (instance_id == '' or "
                        "_divination_candidate.instance_id == instance_id)",
                        [
                            _call(
                                "check_srd_divination_record",
                                {
                                    "caster_state": "{_caster_state_id}",
                                    "record": "{_divination_candidate}",
                                },
                                ["_divination_active"],
                            ),
                            _when(
                                "_divination_active == true",
                                [_set("_divination_record", "_divination_candidate")],
                            ),
                        ],
                    ),
                ],
            ),
            _set("_divination_active", "_divination_record != null"),
        ],
    )
    save = _tool(
        "Replace only this exact source instance, preserving other spell owners.",
        [_parameter("record", "object", from_state="state.turn_effects")],
        [
            _set("_divination_updated", []),
            _set("_divination_existing", []),
            _when(
                "state.turn_effects != null", [_set("_divination_existing", "state.turn_effects")]
            ),
            _loop(
                "_divination_existing",
                "_divination_prior",
                [
                    _when(
                        "_divination_prior.kind != 'divination_spell' or "
                        "_divination_prior.instance_id != record.instance_id",
                        [_push("_divination_updated", "_divination_prior")],
                    ),
                ],
            ),
            _require("len(_divination_updated) < 128", "Resolve existing lasting effects first."),
            _push("_divination_updated", "record"),
            _set("state.turn_effects", "_divination_updated"),
            _set("state.has_divination_spells", True),
        ],
    )
    repetition = _tool(
        "Apply each spell's cumulative 25 percent no-answer chance since the actual Long Rest.",
        [_entity("caster_state"), _parameter("spell")],
        [
            _set("_divination_no_answer", False),
            _set("_divination_rest_epoch", -1),
            _when(
                "caster_state.last_long_rest_tick != null",
                [_set("_divination_rest_epoch", "caster_state.last_long_rest_tick")],
            ),
            _when(
                "caster_state.divination_repetition_epoch != _divination_rest_epoch",
                [
                    _set("caster_state.divination_repetition_epoch", "_divination_rest_epoch"),
                    _set(
                        "caster_state.divination_repetitions",
                        {"augury": 0, "commune": 0, "divination": 0},
                    ),
                ],
            ),
            _set("_divination_previous_casts", 0),
            _when(
                "spell in caster_state.divination_repetitions",
                [_set("_divination_previous_casts", "caster_state.divination_repetitions[spell]")],
            ),
            _set("caster_state.divination_repetitions.{spell}", "_divination_previous_casts + 1"),
            _when(
                "_divination_previous_casts > 0",
                [
                    {"dice": {"expression": "1d100"}, "store_as": "_divination_repeat_roll"},
                    _set(
                        "_divination_no_answer",
                        "_divination_repeat_roll.total <= "
                        "min(100, _divination_previous_casts * 25)",
                    ),
                ],
            ),
        ],
    )
    return {
        "check_srd_divination_record": check,
        "collect_srd_divination_record": collect,
        "save_srd_divination_record": save,
        "check_srd_repeated_divination": repetition,
    }


def _save_tool():
    """Use actual saved NPC saves and the shared D20 condition/defense owner."""
    return _tool(
        "Resolve a source-specific saving throw with the actor's admitted modifier.",
        [
            _entity("target_state"),
            _parameter("ability"),
            _parameter("dc", "integer"),
            _optional("actor", "string", None, resolve="participant"),
            _optional("adjustment", "integer", 0),
        ],
        [
            _set("_divination_save_modifier", 0),
            _set("_divination_save_proficiency", "'none'"),
            _when(
                "actor != null and actor.data.saving_throw_proficiencies != null and "
                "ability in actor.data.saving_throw_proficiencies",
                [_set("_divination_save_proficiency", "'normal'")],
            ),
            _when(
                "actor == null",
                [
                    _set("_actor_id", None),
                    _require(
                        "target_state.saving_throw_modifiers != null and "
                        "target_state.saving_throw_modifiers[ability] != null",
                        "Record the actual saving throw modifier.",
                    ),
                    _set(
                        "_divination_save_modifier", "target_state.saving_throw_modifiers[ability]"
                    ),
                ],
            ),
            _call(
                "d20_test",
                {
                    "actor": "{_actor_id}",
                    "actor_state": "{_target_state_id}",
                    "ability": "{ability}",
                    "ability_modifier": "{_divination_save_modifier}",
                    "proficiency_bonus": 0,
                    "kind": "saving_throw",
                    "proficiency_factor": "{_divination_save_proficiency}",
                    "circumstantial_modifier": "{adjustment}",
                    "target_number": "{dc}",
                },
                ["_outcome", "_d20_test", "_adjusted_total"],
            ),
            _set("_divination_save_success", "_outcome == 'success'"),
        ],
    )


def _word_tool():
    """Each array item is one nonempty word; all Unicode whitespace is rejected inside tokens."""
    effects = [_set("_word_tokens", "words")]
    whitespace = (
        " \t\n\r\x0b\x0c\x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007"
        "\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
    )
    checks = [_require("len(_word_token) > 0", "A word token cannot be empty.")]
    for start in range(0, len(whitespace), 7):
        checks.append(
            _require(
                " and ".join(
                    "'" + c + "' not in _word_token" for c in whitespace[start : start + 7]
                ),
                "Provide one word per array item; whitespace cannot conceal extra words.",
            )
        )
    effects.append(_loop("_word_tokens", "_word_token", checks))
    return _tool(
        "Validate the actual word tokens without accepting an arbitrary GM word count.",
        [_parameter("words", "array", items={"type": "string"})],
        effects,
    )


def _question_tools():
    ask = _public(
        _tool(
            (
                "Ask within the source-owned question count and actual one-minute interva"
                "l; the GM supplies the source-authorized answer."
            ),
            [
                _entity("caster_state"),
                _enum("spell", ["commune", "contact_other_plane"]),
                _parameter("question"),
                _words("answer_words", 40),
                _enum(
                    "answer_reason",
                    ["ordinary", "beyond_knowledge", "misleading", "deity_interest"],
                    "ordinary",
                ),
                _optional("answer_is_correct", "boolean", False),
            ],
            [
                _require(PENDING, "Resolve the pending action before asking another question."),
                _require(ACTIVE, "An Incapacitated caster cannot ask a question."),
                _call(
                    "collect_srd_divination_record",
                    {"caster_state": "{_caster_state_id}", "spell": "{spell}"},
                    ["_divination_record", "_divination_active"],
                ),
                _require(
                    "_divination_active == true",
                    "The question interval has ended or was unavailable.",
                ),
                _require("question != ''", "Ask a specific question."),
                _call("validate_srd_spell_words", {"words": "{answer_words}"}),
                _require(
                    "_divination_record.questions_used < _divination_record.question_limit",
                    "This casting's question limit is reached.",
                ),
                _require(
                    (
                        "spell != 'commune' or len(answer_words) != 1 or answer_words[0] "
                        "== 'yes' or answer_words[0] == 'no' or answer_words[0] == 'uncle"
                        "ar'"
                    ),
                    (
                        "Commune uses yes/no, unclear beyond knowledge, or a source-quali"
                        "fied short phrase."
                    ),
                ),
                _require(
                    "len(answer_words) == 1 or answer_reason == 'misleading' or "
                    "(spell == 'commune' and answer_reason == 'deity_interest')",
                    "A phrase needs the source's misleading-answer or divine-interest exception.",
                ),
                _require(
                    "answer_reason != 'beyond_knowledge' or (len(answer_words) == 1 and "
                    "answer_words[0] == 'unclear')",
                    "Information beyond the entity's knowledge is unclear.",
                ),
                _require(
                    "answer_is_correct == true",
                    "The GM must provide a source-qualified correct answer.",
                ),
                _set(
                    "_question_update", {"questions_used": "_divination_record.questions_used + 1"}
                ),
                _set("_divination_record", "merge(_divination_record, _question_update)"),
                _call("save_srd_divination_record", {"record": "{_divination_record}"}),
                _set("_divination_answer", "answer_words"),
                _set(
                    "_divination_questions_remaining",
                    "_divination_record.question_limit - _divination_record.questions_used",
                ),
            ],
        ),
        "exploration",
        "Divination answer",
        [
            ("Answer", "_divination_answer"),
            ("Questions remaining", "_divination_questions_remaining"),
        ],
    )
    clear = _tool(
        (
            "Retire only Contact Other Plane's exact Incapacitated sources, preserving ot"
            "her conditions."
        ),
        [_entity("target_state")],
        [
            _when(
                "target_state.timed_condition_source_ids != null",
                [
                    _set("_contact_sources", "target_state.timed_condition_source_ids"),
                    _loop(
                        "_contact_sources",
                        "_contact_source_id",
                        [
                            _when(
                                (
                                    "state.timed_condition_sources[_contact_source_id].in"
                                    "stalled == true and state.timed_condition_sources[_c"
                                    "ontact_source_id].spell_id == 'contact_other_plane'"
                                ),
                                [
                                    _call(
                                        "retire_srd_timed_condition_source",
                                        {
                                            "lasting_state": "state",
                                            "source_id": "{_contact_source_id}",
                                        },
                                    ),
                                ],
                            ),
                        ],
                    ),
                    _call(
                        "refresh_srd_timed_condition_target",
                        {"target_state": "{_target_state_id}", "lasting_state": "state"},
                    ),
                ],
            ),
        ],
    )
    return {"ask_srd_divination_question": ask, "clear_srd_contact_other_plane": clear}


CAST_CHOICES = [
    _words(maximum=128, default=[]),
    _optional("divination_subject", default=""),
    _optional("divination_recipient_description", default=""),
    _optional("divination_subject_familiar", "boolean", False),
    _optional("divination_subject_met", "boolean", False),
    _optional("divination_described_by_acquaintance", "boolean", False),
    _optional("divination_target_knows_caster", "boolean", False),
    _optional("divination_subject_famous", "boolean", False),
    _optional("divination_existing_knowledge", default=""),
    _optional("divination_gm_answer", default=""),
    _enum("divination_omen", ["weal", "woe", "weal_and_woe", "indifference"], "indifference"),
    _optional("divination_event_tick", "integer", 0),
    _array("divination_destination", default=[0, 0, 0]),
    _optional("divination_visited_location", "boolean", False),
    _optional("divination_known_beyond_barrier", "boolean", False),
    _enum(
        "divination_nature_setting", ["outdoors", "natural_underground", "construction"], "outdoors"
    ),
    _enum("divination_thought_mode", ["sense", "read"], "sense"),
    _enum("divination_scry_kind", ["creature", "location"], "creature"),
    _enum("divination_scry_knowledge", ["secondhand", "firsthand", "extensive"], "secondhand"),
    _enum("divination_scry_connection", ["none", "likeness", "possession", "body_part"], "none"),
    _optional("divination_connection_item_id", default=""),
    _optional("divination_touch_uninterrupted", "boolean", False),
    _optional(
        "divination_nature_facts",
        "array",
        ["settlements", "portals", "water"],
        min_items=3,
        max_items=3,
        items={
            "type": "string",
            "enum": [
                "settlements",
                "portals",
                "powerful_creature",
                "prevalent_plant",
                "prevalent_mineral",
                "prevalent_beast",
                "water",
            ],
        },
    ),
]


def _family_tool(description, effects):
    """Family leaves receive already validated casting values, not independent cast authority."""
    return _tool_scene(
        description,
        [
            _optional("caster", "string", None, resolve="participant"),
            _entity("caster_state"),
            _entity("target_state"),
            _parameter("spell"),
            _parameter("record", "object", from_state="state.turn_effects"),
            _parameter("casting_save_dc", "integer"),
            _parameter("slot_level", "integer"),
            _parameter("caster_can_touch_target", "boolean"),
            _parameter("material_item_ids", "array", items={"type": "string"}),
            _optional("targets", "array", None, items={"type": "string"}),
            _parameter("all_targets_willing", "boolean"),
            *deepcopy(CAST_CHOICES),
        ],
        [
            _when("caster == null", [_set("_caster_id", None)]),
            _set("_divination_record", "record"),
            _set("_divination_install", "record.expires_tick > state.lasting_tick"),
            _set("_divination_answer", "''"),
            *effects,
        ],
    )


def _communication_tools():
    """Messages have source-specific barriers, recipient ownership, later replies and blocking."""
    barrier = _tool_scene(
        "Apply Message's one-foot stone/metal/wood, lead and magical-silence barriers.",
        [_array("start"), _array("end")],
        _snapshot()
        + [
            _set("_message_blocked", False),
            _set("_message_geometry", "state.spatial_scene.geometry"),
            _loop(
                "_message_geometry",
                "_message_wall",
                [
                    _when(
                        "_message_wall.active == true",
                        [
                            _call(
                                "srd_information_segment_interval",
                                {
                                    "start": "{start}",
                                    "end": "{end}",
                                    "volume": "{_message_wall.volume}",
                                },
                                ["_segment_hit", "_segment_thickness"],
                            ),
                            _when(
                                "_segment_hit == true and _segment_thickness > 0.000000000001",
                                [
                                    _when(
                                        "_message_wall.metadata.material == 'lead' or "
                                        "((_message_wall.metadata.material == 'stone' or "
                                        "_message_wall.metadata.material == 'metal' or "
                                        "_message_wall.metadata.material == 'wood') and "
                                        "_segment_thickness + 0.000000000001 >= 1)",
                                        [_set("_message_blocked", True)],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _set("_message_zones", "state.spatial_scene.zones"),
            _loop(
                "_message_zones",
                "_message_zone",
                [
                    _when(
                        (
                            "_message_zone.metadata.active != false and _message_zone.met"
                            "adata.magical_silence == true"
                        ),
                        [
                            _call(
                                "srd_information_segment_interval",
                                {
                                    "start": "{start}",
                                    "end": "{end}",
                                    "volume": "{_message_zone.shape}",
                                },
                                ["_segment_hit", "_segment_thickness"],
                            ),
                            _when("_segment_hit == true", [_set("_message_blocked", True)]),
                        ],
                    ),
                ],
            ),
        ],
    )
    send = _family_tool(
        (
            "Deliver only the selected spell's message and save an actual recipient-owned"
            " reply window."
        ),
        [
            _require("len(message_words) > 0", "Provide the message's actual words."),
            _require(
                "spell != 'sending' or len(message_words) <= 25",
                "Sending permits at most 25 words.",
            ),
            _require("target_state.is_alive == true", "Select a living creature recipient."),
            _call(
                "srd_information_scene_entity",
                {"subject": "{_caster_state_id}"},
                ["_info_scene_entity", "_info_position"],
            ),
            _set("_message_origin", "_info_position"),
            _set("_message_source_plane", "_info_scene_entity.metadata.plane"),
            _call(
                "srd_information_scene_entity",
                {"subject": "{_target_state_id}"},
                ["_info_scene_entity", "_info_position"],
            ),
            _set("_message_target_plane", "_info_scene_entity.metadata.plane"),
            _require(
                "_message_source_plane != null and _message_target_plane != null",
                "Messages need the actual source and recipient planes.",
            ),
            _set("_message_delivered", True),
            _set("_message_block_reason", "''"),
            _when(
                "spell == 'message'",
                [
                    _call(
                        "measure_srd_information_distance",
                        {"start": "{_message_origin}", "end": "{_info_position}"},
                        ["_scene_distance"],
                    ),
                    _require(
                        "_scene_distance <= 120 and _message_source_plane == _message_target_plane",
                        "Message requires its actual 120-foot same-plane range.",
                    ),
                    _call(
                        "srd_information_barriers",
                        {"start": "{_message_origin}", "end": "{_info_position}", "rule": "solid"},
                        ["_information_blocked"],
                    ),
                    _require(
                        "_information_blocked != true or (divination_subject_familiar == true and "
                        "divination_known_beyond_barrier == true)",
                        (
                            "Message through a solid object needs familiarity and knowled"
                            "ge that the recipient is beyond it."
                        ),
                    ),
                    _call(
                        "check_srd_message_barriers",
                        {"start": "{_message_origin}", "end": "{_info_position}"},
                        ["_message_blocked"],
                    ),
                    _set(
                        "_message_delivered",
                        "_message_blocked != true and 'deafened' not in target_state.conditions",
                    ),
                    _when(
                        "_message_delivered != true",
                        [
                            _set(
                                "_message_block_reason",
                                "'The magical whisper is blocked or unheard.'",
                            )
                        ],
                    ),
                ],
            ),
            _when(
                "spell == 'sending'",
                [
                    _require(
                        (
                            "divination_subject_met == true or divination_described_by_ac"
                            "quaintance == true"
                        ),
                        (
                            "Sending needs a creature met by the caster or described by s"
                            "omeone who has met it."
                        ),
                    ),
                    _when(
                        (
                            "target_state.sending_blocks != null and _caster_state_id in "
                            "target_state.sending_blocks and state.lasting_tick < target_"
                            "state.sending_blocks[_caster_state_id]"
                        ),
                        [
                            _set("_message_delivered", False),
                            _set(
                                "_message_block_reason", "'The recipient has blocked this sender.'"
                            ),
                        ],
                    ),
                    _when(
                        (
                            "_message_delivered == true and _message_source_plane != _mes"
                            "sage_target_plane"
                        ),
                        [
                            {"dice": {"expression": "1d100"}, "store_as": "_sending_plane_roll"},
                            _when(
                                "_sending_plane_roll.total <= 5",
                                [
                                    _set("_message_delivered", False),
                                    _set(
                                        "_message_block_reason",
                                        "'The message failed to reach the other plane.'",
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _set("_divination_install", False),
            _when(
                "_message_delivered == true",
                [
                    _when("state.lasting_serial == null", [_set("state.lasting_serial", 0)]),
                    {"increment": {"field": "state.lasting_serial", "amount": 1}},
                    _call(
                        "format_srd_information_projection",
                        {"message": "{_caster_state_id}:{spell}:{state.lasting_serial}"},
                        ["_info_projection_message"],
                    ),
                    _set("_message_id", "_info_projection_message"),
                    _when(
                        "spell == 'message'",
                        [
                            _set("_message_record_patch", {"message_id": "_message_id"}),
                            _set("_divination_record", "merge(record, _message_record_patch)"),
                            _set("_divination_install", True),
                        ],
                    ),
                    _set(
                        "_delivered_message",
                        {
                            "id": "_message_id",
                            "spell": "spell",
                            "sender": "_caster_state_id",
                            "recipient": "_target_state_id",
                            "words": "message_words",
                            "received_tick": "state.lasting_tick",
                            "expires_tick": "state.lasting_tick + 2",
                            "reply_used": False,
                            "recognizes_sender": (
                                "spell == 'message' or divination_target_knows_caster == true"
                            ),
                            "meaning_understood": "spell == 'sending'",
                        },
                    ),
                    _when(
                        "target_state.received_spell_messages == null",
                        [_set("target_state.received_spell_messages", [])],
                    ),
                    _set("_message_inbox", []),
                    _set("_message_old_inbox", "target_state.received_spell_messages"),
                    _loop(
                        "_message_old_inbox",
                        "_message_old_receipt",
                        [
                            _when(
                                (
                                    "_message_old_receipt.reply_used != true and ((_messa"
                                    "ge_old_receipt.spell == 'message' and state.lasting_"
                                    "tick < _message_old_receipt.expires_tick) or (_messa"
                                    "ge_old_receipt.spell == 'sending' and state.lasting_"
                                    "tick == _message_old_receipt.received_tick))"
                                ),
                                [_push("_message_inbox", "_message_old_receipt")],
                            ),
                        ],
                    ),
                    _require(
                        "len(_message_inbox) < 64",
                        "Resolve the recipient's older messages before adding another.",
                    ),
                    _push("_message_inbox", "_delivered_message"),
                    _set("target_state.received_spell_messages", "_message_inbox"),
                    _set("target_state.received_spell_message", "_delivered_message"),
                    _set(
                        "_divination_answer", "'Message delivered; only the recipient receives it.'"
                    ),
                ],
            ),
            _when(
                "_message_delivered != true", [_set("_divination_answer", "_message_block_reason")]
            ),
        ],
    )
    reply = _public(
        _tool(
            (
                "Reply as the actual recipient within Message's one round or Sending's im"
                "mediate same-clock window; optionally block Sending for eight hours."
            ),
            [
                _entity("recipient_state"),
                _entity("sender_state"),
                _parameter("message_id"),
                _words("reply_words", 128, []),
                _optional("block_sending", "boolean", False),
            ],
            [
                _require(PENDING, "Resolve the pending action before replying."),
                _set("_received_message", None),
                _set("_reply_inbox", []),
                _when(
                    "recipient_state.received_spell_messages != null",
                    [_set("_reply_inbox", "recipient_state.received_spell_messages")],
                ),
                _loop(
                    "_reply_inbox",
                    "_reply_message",
                    [
                        _when(
                            (
                                "_reply_message.id == message_id and _reply_message.sende"
                                "r == _sender_state_id"
                            ),
                            [_set("_received_message", "_reply_message")],
                        ),
                    ],
                ),
                _require(
                    "_received_message != null", "Use this recipient's actual received message."
                ),
                _require(
                    "_received_message.reply_used != true",
                    "This message's reply choice was already resolved.",
                ),
                _require(
                    (
                        "(_received_message.spell == 'message' and state.lasting_tick < _"
                        "received_message.expires_tick) or (_received_message.spell == 's"
                        "ending' and state.lasting_tick == _received_message.received_tic"
                        "k)"
                    ),
                    "The source's reply window has ended.",
                ),
                _require(
                    "block_sending != true or _received_message.spell == 'sending'",
                    "Only Sending permits an eight-hour sender block.",
                ),
                _require(
                    "_received_message.spell != 'sending' or len(reply_words) <= 25",
                    "Sending's like reply has at most 25 words.",
                ),
                _require(
                    "len(reply_words) > 0 or block_sending == true",
                    "Reply or select Sending's block.",
                ),
                _call("validate_srd_spell_words", {"words": "{reply_words}"}),
                _require(
                    (
                        "_received_message.spell != 'message' or ('deafened' not in recip"
                        "ient_state.conditions and 'deafened' not in sender_state.conditi"
                        "ons)"
                    ),
                    "Message requires the two creatures to hear its magical whispers.",
                ),
                _when(
                    "_received_message.spell == 'message'",
                    [
                        _call(
                            "resolve_srd_scene_geometry_relation",
                            {
                                "actor_state": "{_recipient_state_id}",
                                "target_state": "{_sender_state_id}",
                            },
                            [
                                "_scene_distance",
                                "_scene_same_plane",
                                "_scene_actor_position",
                                "_scene_target_position",
                            ],
                        ),
                        _require(
                            "_scene_same_plane == true and _scene_distance <= 120",
                            "The reply must remain within Message's actual range.",
                        ),
                        _call(
                            "check_srd_message_barriers",
                            {"start": "{_scene_actor_position}", "end": "{_scene_target_position}"},
                            ["_message_blocked"],
                        ),
                        _require(
                            "_message_blocked != true",
                            "The magical reply is blocked by the current Message barrier.",
                        ),
                    ],
                ),
                _when(
                    "block_sending == true",
                    [
                        _when(
                            "recipient_state.sending_blocks == null",
                            [_set("recipient_state.sending_blocks", {})],
                        ),
                        _set(
                            "recipient_state.sending_blocks.{_sender_state_id}",
                            "state.lasting_tick + 9600",
                        ),
                    ],
                ),
                _when(
                    "len(reply_words) > 0",
                    [
                        _set(
                            "sender_state.received_spell_reply",
                            {
                                "message_id": "message_id",
                                "recipient": "_recipient_state_id",
                                "words": "reply_words",
                                "received_tick": "state.lasting_tick",
                                "meaning_understood": "_received_message.spell == 'sending'",
                            },
                        )
                    ],
                ),
                _set("_updated_inbox", []),
                _loop(
                    "_reply_inbox",
                    "_reply_message",
                    [
                        {
                            "condition": "_reply_message.id == message_id",
                            "on_true": [
                                _set("_reply_update", {"reply_used": True}),
                                _push("_updated_inbox", "merge(_reply_message, _reply_update)"),
                            ],
                            "on_false": [_push("_updated_inbox", "_reply_message")],
                        },
                    ],
                ),
                _set("recipient_state.received_spell_messages", "_updated_inbox"),
                _when(
                    "recipient_state.received_spell_message.id == message_id",
                    [_set("recipient_state.received_spell_message.reply_used", True)],
                ),
                _set("_reply_received", "len(reply_words) > 0"),
                _set("_sender_blocked", "block_sending"),
            ],
        ),
        "exploration",
        "Spell reply",
        [("Reply delivered", "_reply_received"), ("Sender blocked", "_sender_blocked")],
    )
    target = _tool(
        (
            "Validate one willing language-capable Telepathic Bond member against actual "
            "casting range."
        ),
        [_entity("caster_state"), _entity("target_state"), _parameter("willing", "boolean")],
        [
            _require("willing == true", "Telepathic Bond requires willing targets."),
            _require("target_state.is_alive == true", "Choose living bond members."),
            _call(
                "resolve_srd_scene_geometry_relation",
                {"actor_state": "{_caster_state_id}", "target_state": "{_target_state_id}"},
                ["_scene_distance", "_scene_same_plane", "_scene_line_of_sight"],
            ),
            _require(
                (
                    "_scene_distance <= 30 and _scene_same_plane == true and _scene_line_"
                    "of_sight == true"
                ),
                "Bond members need actual same-plane range and a clear path to the target.",
            ),
            _set(
                "_bond_eligible",
                "target_state.languages != null and len(target_state.languages) > 0",
            ),
        ],
    )
    bond = _family_tool(
        (
            "Forge a source-owned bond only among eligible willing creatures in its actua"
            "l thirty-foot range."
        ),
        [
            _require(
                "targets != null and len(targets) >= 1 and len(targets) <= 8",
                "Telepathic Bond selects one to eight willing creatures.",
            ),
            _set("_bond_members", []),
            _set("_bond_targets", "targets"),
            _loop(
                "_bond_targets",
                "_bond_target",
                [
                    _call(
                        "validate_srd_telepathic_bond_member",
                        {
                            "caster_state": "{_caster_state_id}",
                            "target_state": "{_bond_target}",
                            "willing": "{all_targets_willing}",
                        },
                        ["_bond_eligible"],
                    ),
                    _require(
                        "_bond_target not in _bond_members",
                        "A creature cannot occupy two bond positions.",
                    ),
                    _when("_bond_eligible == true", [_push("_bond_members", "_bond_target")]),
                ],
            ),
            _set("_bond_record_update", {"members": "_bond_members"}),
            _set("_divination_record", "merge(record, _bond_record_update)"),
            _set("_divination_install", "len(_bond_members) > 0"),
            _set(
                "_divination_answer",
                (
                    "'Eligible willing creatures are linked; use the later bond communica"
                    "tion consumer.'"
                ),
            ),
        ],
    )
    communicate = _public(
        _tool(
            (
                "Communicate through the exact saved all-to-all bond, independent of lang"
                "uage and distance, while both members are on the same plane."
            ),
            [
                _entity("caster_state"),
                _entity("sender_state"),
                _entity("recipient_state"),
                _parameter("communication"),
                _optional("instance_id", default=""),
            ],
            [
                _require(PENDING, "Resolve the pending action before communicating."),
                _call(
                    "collect_srd_divination_record",
                    {
                        "caster_state": "{_caster_state_id}",
                        "spell": "telepathic_bond",
                        "instance_id": "{instance_id}",
                    },
                    ["_divination_record", "_divination_active"],
                ),
                _require("_divination_active == true", "This source's telepathic bond has ended."),
                _require(
                    (
                        "_sender_state_id in _divination_record.members and _recipient_st"
                        "ate_id in _divination_record.members"
                    ),
                    "Both creatures must be members of this exact bond.",
                ),
                _call(
                    "check_srd_bond_member_source",
                    {"target_state": "{_sender_state_id}", "record": "{_divination_record}"},
                    ["_bond_member_source_active"],
                ),
                _require(
                    "_bond_member_source_active == true",
                    "A more recent overlapping bond applies to this sender.",
                ),
                _call(
                    "check_srd_bond_member_source",
                    {"target_state": "{_recipient_state_id}", "record": "{_divination_record}"},
                    ["_bond_member_source_active"],
                ),
                _require(
                    "_bond_member_source_active == true",
                    "A more recent overlapping bond applies to this recipient.",
                ),
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{_sender_state_id}"},
                    ["_info_scene_entity"],
                ),
                _set("_bond_sender_plane", "_info_scene_entity.metadata.plane"),
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{_recipient_state_id}"},
                    ["_info_scene_entity"],
                ),
                _require(
                    (
                        "_bond_sender_plane != null and _info_scene_entity.metadata.plane"
                        " == _bond_sender_plane"
                    ),
                    "A telepathic bond cannot extend to another plane.",
                ),
                _require("communication != ''", "Supply the actual communication."),
                _set(
                    "recipient_state.received_bond_communication",
                    {
                        "caster": "_caster_state_id",
                        "sender": "_sender_state_id",
                        "communication": "communication",
                        "tick": "state.lasting_tick",
                    },
                ),
                _set("_bond_understood", True),
            ],
        ),
        "exploration",
        "Bond communication",
        [("Understood", "_bond_understood")],
    )
    member_source = _tool(
        (
            "Select the most recent active equal-potency bond for one creature; independe"
            "nt groups do not become a transitive network."
        ),
        [_entity("target_state"), _parameter("record", "object", from_state="state.turn_effects")],
        [
            _set("_bond_member_source_active", True),
            _set("_bond_sources", "state.turn_effects"),
            _loop(
                "_bond_sources",
                "_bond_source",
                [
                    _when(
                        (
                            "_bond_source.kind == 'divination_spell' and _bond_source.spe"
                            "ll == 'telepathic_bond' and _target_state_id in _bond_source"
                            ".members and _bond_source.instance_id != record.instance_id"
                        ),
                        [
                            _call(
                                "check_srd_divination_record",
                                {
                                    "caster_state": "{_bond_source.caster}",
                                    "record": "{_bond_source}",
                                },
                                ["_divination_active"],
                            ),
                            _when(
                                (
                                    "_divination_active == true and (_bond_source.started"
                                    "_tick > record.started_tick or (_bond_source.started"
                                    "_tick == record.started_tick and _bond_source.source"
                                    "_serial > record.source_serial))"
                                ),
                                [_set("_bond_member_source_active", False)],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    return {
        "check_srd_message_barriers": barrier,
        "cast_srd_spell_message": send,
        "reply_srd_spell_message": reply,
        "validate_srd_telepathic_bond_member": target,
        "cast_srd_telepathic_bond": bond,
        "communicate_srd_telepathic_bond": communicate,
        "check_srd_bond_member_source": member_source,
    }


def _nature_tools():
    """Calculate location and range; require source-qualified natural-world knowledge."""
    cast = _family_tool(
        "Learn exactly three selected natural facts within the actual source-defined area.",
        [
            _require(
                "len(divination_nature_facts) == 3",
                "Choose exactly three Commune with Nature facts.",
            ),
            _set("_nature_choices", "divination_nature_facts"),
            _set("_nature_unique", []),
            _set("_nature_prevalence_choices", 0),
            _loop(
                "_nature_choices",
                "_nature_choice",
                [
                    _require(
                        "_nature_choice not in _nature_unique", "Choose three different facts."
                    ),
                    _push("_nature_unique", "_nature_choice"),
                    _when(
                        (
                            "_nature_choice == 'prevalent_plant' or _nature_choice == 'pr"
                            "evalent_mineral' or _nature_choice == 'prevalent_beast'"
                        ),
                        [_set("_nature_prevalence_choices", "_nature_prevalence_choices + 1")],
                    ),
                ],
            ),
            _require(
                "_nature_prevalence_choices <= 1",
                "The prevalent-kind fact selects Plants, minerals, or Beasts.",
            ),
            _set("_divination_install", False),
            {
                "condition": "divination_nature_setting == 'construction'",
                "on_true": [
                    _set(
                        "_divination_answer",
                        "'Nature has been replaced by construction; the spell does not function.'",
                    ),
                ],
                "on_false": [
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_caster_state_id}"},
                        ["_info_scene_entity", "_info_position"],
                    ),
                    _set("_nature_origin", "_info_position"),
                    _set("_nature_plane", "_info_scene_entity.metadata.plane"),
                    _require("_nature_plane != null", "Nature divination needs the actual plane."),
                    _set("_nature_radius", 15840),
                    _when(
                        "divination_nature_setting == 'natural_underground'",
                        [_set("_nature_radius", 300)],
                    ),
                    *[
                        _require(
                            f"_nature_origin[{axis}] - _nature_radius >= "
                            f"state.spatial_scene.map.bounds.min[{axis}] and "
                            f"_nature_origin[{axis}] + _nature_radius <= "
                            f"state.spatial_scene.map.bounds.max[{axis}]",
                            "The admitted world scene must cover the spell's whole natural area.",
                        )
                        for axis in range(3)
                    ],
                    _call(
                        "collect_srd_information_candidates",
                        {"origin": "{_nature_origin}"},
                        ["_query_candidates"],
                    ),
                    _set("_nature_results", []),
                    _loop(
                        "_nature_choices",
                        "_nature_choice",
                        [
                            _set("_nature_fact_found", False),
                            _set("_nature_powerful_found", False),
                            _set("_nature_prevalent_category", "''"),
                            _when(
                                "_nature_choice == 'prevalent_plant'",
                                [_set("_nature_prevalent_category", "'plant'")],
                            ),
                            _when(
                                "_nature_choice == 'prevalent_mineral'",
                                [_set("_nature_prevalent_category", "'mineral'")],
                            ),
                            _when(
                                "_nature_choice == 'prevalent_beast'",
                                [_set("_nature_prevalent_category", "'beast'")],
                            ),
                            _loop(
                                "_query_candidates",
                                "_nature_candidate",
                                [
                                    _when(
                                        (
                                            "_nature_candidate.active == true and _nature"
                                            "_candidate.metadata.plane == _nature_plane"
                                        ),
                                        [
                                            _call(
                                                "measure_srd_information_distance",
                                                {
                                                    "start": "{_nature_origin}",
                                                    "end": "{_nature_candidate.position}",
                                                },
                                                ["_scene_distance", "_scene_direction"],
                                            ),
                                            _when(
                                                "_scene_distance <= _nature_radius",
                                                [
                                                    _set("_nature_selected", False),
                                                    _when(
                                                        (
                                                            "(_nature_choice == 'settleme"
                                                            "nts' and _nature_candidate.m"
                                                            "etadata.settlement == true) "
                                                            "or (_nature_choice == 'porta"
                                                            "ls' and _nature_candidate.me"
                                                            "tadata.planar_portal == true"
                                                            ") or (_nature_choice == 'wat"
                                                            "er' and _nature_candidate.me"
                                                            "tadata.body_of_water == true"
                                                            ")"
                                                        ),
                                                        [_set("_nature_selected", True)],
                                                    ),
                                                    _when(
                                                        (
                                                            "_nature_choice == 'powerful_"
                                                            "creature' and _nature_candid"
                                                            "ate.metadata.challenge_ratin"
                                                            "g != null and _nature_candid"
                                                            "ate.metadata.challenge_ratin"
                                                            "g >= 10 and _nature_powerful"
                                                            "_found != true"
                                                        ),
                                                        [
                                                            _when(
                                                                (
                                                                    "(_nature_candidate.m"
                                                                    "etadata.creature_typ"
                                                                    "e == 'celestial' or "
                                                                    "_nature_candidate.me"
                                                                    "tadata.creature_type"
                                                                    " == 'elemental' or _"
                                                                    "nature_candidate.met"
                                                                    "adata.creature_type "
                                                                    "== 'fey' or _nature_"
                                                                    "candidate.metadata.c"
                                                                    "reature_type == 'fie"
                                                                    "nd' or _nature_candi"
                                                                    "date.metadata.creatu"
                                                                    "re_type == 'undead')"
                                                                    " and (divination_sub"
                                                                    "ject == '' or divina"
                                                                    "tion_subject == _nat"
                                                                    "ure_candidate.id)"
                                                                ),
                                                                [_set("_nature_selected", True)],
                                                            ),
                                                        ],
                                                    ),
                                                    _set("_nature_prevalent_category", "''"),
                                                    _when(
                                                        "_nature_choice == 'prevalent_plant'",
                                                        [
                                                            _set(
                                                                "_nature_prevalent_category",
                                                                "'plant'",
                                                            )
                                                        ],
                                                    ),
                                                    _when(
                                                        "_nature_choice == 'prevalent_mineral'",
                                                        [
                                                            _set(
                                                                "_nature_prevalent_category",
                                                                "'mineral'",
                                                            )
                                                        ],
                                                    ),
                                                    _when(
                                                        "_nature_choice == 'prevalent_beast'",
                                                        [
                                                            _set(
                                                                "_nature_prevalent_category",
                                                                "'beast'",
                                                            )
                                                        ],
                                                    ),
                                                    _when(
                                                        (
                                                            "_nature_prevalent_category !"
                                                            "= '' and _nature_candidate.m"
                                                            "etadata.nature_prevalent_cat"
                                                            "egory == _nature_prevalent_c"
                                                            "ategory and _nature_candidat"
                                                            "e.metadata.nature_prevalent_"
                                                            "kind != null"
                                                        ),
                                                        [
                                                            _require(
                                                                (
                                                                    "_nature_candidate.me"
                                                                    "tadata.nature_region"
                                                                    "_qualified == true"
                                                                ),
                                                                (
                                                                    "Qualify the prevalen"
                                                                    "ce world fact for th"
                                                                    "e selected natural r"
                                                                    "egion and area."
                                                                ),
                                                            ),
                                                            _require(
                                                                "_nature_fact_found != true",
                                                                (
                                                                    "The natural area has"
                                                                    " conflicting prevale"
                                                                    "nce facts."
                                                                ),
                                                            ),
                                                            _call(
                                                                "format_srd_information_projection",
                                                                {
                                                                    "message": (
                                                                        "{_nature_choice}"
                                                                        ": {_nature_candi"
                                                                        "date.metadata.na"
                                                                        "ture_prevalent_k"
                                                                        "ind}"
                                                                    )
                                                                },
                                                                ["_info_projection_message"],
                                                            ),
                                                            _push(
                                                                "_nature_results",
                                                                "_info_projection_message",
                                                            ),
                                                            _set("_nature_fact_found", True),
                                                        ],
                                                    ),
                                                    _when(
                                                        (
                                                            "_nature_selected == true and"
                                                            " _nature_candidate.metadata."
                                                            "participant_ref != null"
                                                        ),
                                                        [
                                                            _call(
                                                                "check_srd_mind_blank_information",
                                                                {
                                                                    "target_state": (
                                                                        "{_nature_candida"
                                                                        "te.metadata.part"
                                                                        "icipant_ref}"
                                                                    ),
                                                                    "lasting_state": "state",
                                                                    "query_kind": (
                                                                        "information_gathering"
                                                                    ),
                                                                },
                                                                ["_mind_blank_information_blocked"],
                                                            ),
                                                            _set(
                                                                "_nature_selected",
                                                                (
                                                                    "_mind_blank_informat"
                                                                    "ion_blocked != true"
                                                                ),
                                                            ),
                                                        ],
                                                    ),
                                                    _when(
                                                        "_nature_selected == true",
                                                        [
                                                            _call(
                                                                "format_srd_information_projection",
                                                                {
                                                                    "message": (
                                                                        "{_nature_choice}"
                                                                        ": {_nature_candi"
                                                                        "date.id}; distan"
                                                                        "ce {_scene_dista"
                                                                        "nce}; direction "
                                                                        "{_scene_directio"
                                                                        "n}"
                                                                    )
                                                                },
                                                                ["_info_projection_message"],
                                                            ),
                                                            _push(
                                                                "_nature_results",
                                                                "_info_projection_message",
                                                            ),
                                                            _set("_nature_fact_found", True),
                                                            _when(
                                                                (
                                                                    "_nature_choice == 'p"
                                                                    "owerful_creature'"
                                                                ),
                                                                [
                                                                    _set(
                                                                        "_nature_powerful_found",
                                                                        True,
                                                                    )
                                                                ],
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
                                "_nature_prevalent_category == '' or _nature_fact_found == true",
                                (
                                    "The requested category needs a qualified qualitative"
                                    " prevalence world fact."
                                ),
                            ),
                            _when(
                                "_nature_fact_found != true",
                                [
                                    _call(
                                        "format_srd_information_projection",
                                        {
                                            "message": (
                                                "{_nature_choice}: none in the admitted n"
                                                "atural area"
                                            )
                                        },
                                        ["_info_projection_message"],
                                    ),
                                    _push("_nature_results", "_info_projection_message"),
                                ],
                            ),
                        ],
                    ),
                    _set(
                        "caster_state.last_nature_divination",
                        {
                            "tick": "state.lasting_tick",
                            "radius": "_nature_radius",
                            "plane": "_nature_plane",
                            "facts": "_nature_results",
                            "choices": "divination_nature_facts",
                        },
                    ),
                    _set("_divination_answer", "_nature_results"),
                ],
            },
        ],
    )
    recall = _public(
        _tool(
            (
                "Recall only the facts actually learned at this caster's last instantaneo"
                "us nature divination; this does not rescan the world."
            ),
            [_entity("caster_state")],
            [
                _require(
                    "caster_state.last_nature_divination != null", "No nature facts were learned."
                ),
                _set("_nature_known_facts", "caster_state.last_nature_divination.facts"),
                _set("_nature_known_tick", "caster_state.last_nature_divination.tick"),
            ],
        ),
        "exploration",
        "Known nature facts",
        [("Facts learned", "_nature_known_facts"), ("Learned at tick", "_nature_known_tick")],
    )
    return {"cast_srd_nature_divination": cast, "query_srd_nature_divination": recall}


def _cleanup_tools():
    remove = _tool(
        "Remove only the chosen divination source instances and their actual sensors.",
        [_entity("caster_state"), _parameter("spell"), _optional("instance_id", default="")],
        [
            _set("_remove_divination_records", []),
            _set("_remove_divination_after", []),
            _set("_remove_divination_remaining", False),
            _when(
                "state.turn_effects != null",
                [_set("_remove_divination_records", "state.turn_effects")],
            ),
            _loop(
                "_remove_divination_records",
                "_remove_divination_record",
                [
                    {
                        "condition": "_remove_divination_record.kind == 'divination_spell' and "
                        "_remove_divination_record.caster == _caster_state_id and "
                        "_remove_divination_record.spell == spell and (instance_id == '' or "
                        "_remove_divination_record.instance_id == instance_id)",
                        "on_true": [
                            _when(
                                "_remove_divination_record.proxy_id != ''",
                                [
                                    _call(
                                        "deactivate_srd_information_proxy",
                                        {"proxy_id": "{_remove_divination_record.proxy_id}"},
                                    ),
                                ],
                            ),
                            _when(
                                "_remove_divination_record.spell == 'message'",
                                [
                                    _call(
                                        "expire_srd_spell_message",
                                        {
                                            "target_state": "{_remove_divination_record.target}",
                                            "message_id": "{_remove_divination_record.message_id}",
                                        },
                                    ),
                                ],
                            ),
                            _when(
                                "_remove_divination_record.spell == 'animal_messenger'",
                                [
                                    _call(
                                        "expire_srd_animal_messenger",
                                        {
                                            "target_state": "{_remove_divination_record.target}",
                                            "instance_id": "{_remove_divination_record."
                                            "instance_id}",
                                        },
                                    ),
                                ],
                            ),
                        ],
                        "on_false": [
                            _push("_remove_divination_after", "_remove_divination_record"),
                            _when(
                                "_remove_divination_record.kind == 'divination_spell'",
                                [_set("_remove_divination_remaining", True)],
                            ),
                        ],
                    },
                ],
            ),
            _set("state.turn_effects", "_remove_divination_after"),
            _set("state.has_divination_spells", "_remove_divination_remaining"),
        ],
    )
    refresh = _tool(
        "Expire inactive source-owned divinations before the common clock removes their records.",
        [],
        [
            _when(
                "state.has_divination_spells == true",
                [
                    _set("_refresh_divinations", "state.turn_effects"),
                    _loop(
                        "_refresh_divinations",
                        "_refresh_divination",
                        [
                            _when(
                                "_refresh_divination.kind == 'divination_spell'",
                                [
                                    _call(
                                        "check_srd_divination_record",
                                        {
                                            "caster_state": "{_refresh_divination.caster}",
                                            "record": "{_refresh_divination}",
                                        },
                                        ["_divination_active"],
                                    ),
                                    _when(
                                        "_divination_active != true",
                                        [
                                            _call(
                                                "remove_srd_divination_record",
                                                {
                                                    "caster_state": "{_refresh_divination.caster}",
                                                    "spell": "{_refresh_divination.spell}",
                                                    "instance_id": (
                                                        "{_refresh_divination.instance_id}"
                                                    ),
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
    dismiss = _public(
        _tool(
            "Dismiss one exact source-owned time-span spell without an action.",
            [
                _entity("caster_state"),
                _enum("spell", [s for s in PROFILES if PROFILES[s]["effect_rounds"]]),
                _optional("instance_id", default=""),
            ],
            [
                _require(PENDING, "Resolve the pending action before dismissing a spell."),
                _require(ACTIVE, "An Incapacitated caster cannot dismiss a spell."),
                _call(
                    "collect_srd_divination_record",
                    {
                        "caster_state": "{_caster_state_id}",
                        "spell": "{spell}",
                        "instance_id": "{instance_id}",
                    },
                    ["_divination_record", "_divination_active"],
                ),
                _require(
                    "_divination_active == true", "There is no active selected source instance."
                ),
                {
                    "condition": "_divination_record.concentration == true",
                    "on_true": [
                        _call(
                            "end_srd_concentration_node",
                            {
                                "source_state": "{_caster_state_id}",
                                "target_state": "{caster_state.concentration_target_state_id}",
                            },
                        ),
                    ],
                    "on_false": [
                        _call(
                            "remove_srd_divination_record",
                            {
                                "caster_state": "{_caster_state_id}",
                                "spell": "{spell}",
                                "instance_id": "{_divination_record.instance_id}",
                            },
                        ),
                    ],
                },
                _set("_divination_dismissed", True),
            ],
        ),
        "exploration",
        "Divination dismissed",
        [("Dismissed", "_divination_dismissed")],
    )
    expire = _tool(
        "Expire only the exact courier source; a lost message becomes a return directive.",
        [_entity("target_state"), _parameter("instance_id")],
        [
            _when(
                (
                    "target_state.animal_messenger != null and target_state.animal_messen"
                    "ger.instance_id == instance_id and target_state.animal_messenger.pha"
                    "se == 'outbound'"
                ),
                [
                    _set("target_state.animal_messenger.phase", "'returning'"),
                    _set("target_state.animal_messenger.message_words", []),
                    _set("target_state.animal_messenger.message_lost", True),
                    _set("target_state.animal_messenger.last_tick", "state.lasting_tick"),
                ],
            ),
        ],
    )
    expire_message = _tool(
        "Close only the selected magical whisper's reply window after dismissal or expiry.",
        [_entity("target_state"), _parameter("message_id")],
        [
            _set("_expiring_message_inbox", []),
            _when(
                "target_state.received_spell_messages != null",
                [_set("_expiring_message_inbox", "target_state.received_spell_messages")],
            ),
            _set("_expired_message_inbox", []),
            _loop(
                "_expiring_message_inbox",
                "_expiring_message",
                [
                    _when(
                        "_expiring_message.id == message_id",
                        [
                            _set("_expired_message_patch", {"reply_used": True}),
                            _set(
                                "_expiring_message",
                                "merge(_expiring_message, _expired_message_patch)",
                            ),
                        ],
                    ),
                    _push("_expired_message_inbox", "_expiring_message"),
                ],
            ),
            _set("target_state.received_spell_messages", "_expired_message_inbox"),
            _when(
                "target_state.received_spell_message != null and "
                "target_state.received_spell_message.id == message_id",
                [_set("target_state.received_spell_message.reply_used", True)],
            ),
        ],
    )
    return {
        "expire_srd_spell_message": expire_message,
        "remove_srd_divination_record": remove,
        "refresh_srd_divination_spells": refresh,
        "dismiss_srd_divination_spell": dismiss,
        "expire_srd_animal_messenger": expire,
    }


def _thought_tools():
    """Thought information follows actual senses, range, barriers and later action ownership."""
    eligibility = _tool(
        "Check a language-capable or telepathic mind without inventing thought content.",
        [_entity("target_state")],
        [
            _set(
                "_thought_eligible",
                "target_state.is_alive == true and "
                "((target_state.languages != null and len(target_state.languages) > 0) or "
                "(target_state.telepathy_feet != null and target_state.telepathy_feet > 0))",
            ),
            _call(
                "check_srd_mind_blank_information",
                {
                    "target_state": "{_target_state_id}",
                    "lasting_state": "state",
                    "query_kind": "reading_thoughts",
                },
                ["_mind_blank_information_blocked"],
            ),
            _set(
                "_thought_eligible",
                "_thought_eligible == true and _mind_blank_information_blocked != true",
            ),
        ],
    )
    select = _tool_scene(
        "Sense or read the actual selected mind through the fixed thirty-foot boundary.",
        [
            _entity("caster_state"),
            _entity("target_state"),
            _parameter("record", "object"),
            _enum("mode", ["sense", "read", "probe"]),
            _optional("content", default=""),
        ],
        [
            _set("_thought_record", "record"),
            _set("_thought_present", False),
            _set("_thought_information", []),
            _set("_thought_ended", False),
            _call(
                "srd_information_scene_entity",
                {"subject": "{_caster_state_id}"},
                ["_info_position", "_info_scene_entity"],
            ),
            _set("_thought_origin", "_info_position"),
            _set("_thought_plane", "_info_scene_entity.metadata.plane"),
            {
                "condition": "mode == 'sense'",
                "on_true": [
                    _set("_thought_detected", []),
                    _call(
                        "collect_srd_information_candidates",
                        {"origin": "{_thought_origin}"},
                        ["_query_candidates"],
                    ),
                    _loop(
                        "_query_candidates",
                        "_thought_candidate",
                        [
                            _when(
                                (
                                    "_thought_candidate.scene_kind == 'entity' and _thoug"
                                    "ht_candidate.active == true and _thought_candidate.m"
                                    "etadata.participant_ref != null and _thought_candida"
                                    "te.metadata.plane == _thought_plane"
                                ),
                                [
                                    _call(
                                        "measure_srd_information_distance",
                                        {
                                            "start": "{_thought_origin}",
                                            "end": "{_thought_candidate.position}",
                                        },
                                        ["_scene_distance"],
                                    ),
                                    _when(
                                        "_scene_distance <= 30",
                                        [
                                            _call(
                                                "check_srd_detect_thoughts_eligible",
                                                {
                                                    "target_state": (
                                                        "{_thought_candidate.metadata.par"
                                                        "ticipant_ref}"
                                                    )
                                                },
                                                ["_thought_eligible"],
                                            ),
                                            _call(
                                                "srd_information_barriers",
                                                {
                                                    "start": "{_thought_origin}",
                                                    "end": "{_thought_candidate.position}",
                                                    "rule": "detect",
                                                },
                                                ["_information_blocked"],
                                            ),
                                            _when(
                                                (
                                                    "_thought_eligible == true and _infor"
                                                    "mation_blocked != true"
                                                ),
                                                [
                                                    _push(
                                                        "_thought_detected",
                                                        (
                                                            "_thought_candidate.metadata."
                                                            "participant_ref"
                                                        ),
                                                    ),
                                                ],
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                    _set("_thought_present", "len(_thought_detected) > 0"),
                    _set("_thought_information", "_thought_detected"),
                    _set(
                        "_thought_patch",
                        {
                            "thought_detected": "_thought_detected",
                            "thought_target": "''",
                            "probe_started": False,
                            "attention_turn": "caster_state.turns_started",
                            "attention_tick": "state.lasting_tick",
                        },
                    ),
                ],
                "on_false": [
                    _call(
                        "resolve_srd_scene_relation",
                        {"actor_state": "{_caster_state_id}", "target_state": "{_target_state_id}"},
                        [
                            "_scene_distance",
                            "_scene_same_plane",
                            "_scene_can_see",
                            "_scene_target_position",
                        ],
                    ),
                    _require(
                        "_scene_same_plane == true and _scene_distance <= 30",
                        "Read a mind within the actual thirty-foot range.",
                    ),
                    _call(
                        "check_srd_detect_thoughts_eligible",
                        {"target_state": "{_target_state_id}"},
                        ["_thought_eligible"],
                    ),
                    _require(
                        "_thought_eligible == true",
                        "This mind is ineligible or protected from thought reading.",
                    ),
                    _call(
                        "srd_information_barriers",
                        {
                            "start": "{_thought_origin}",
                            "end": "{_scene_target_position}",
                            "rule": "detect",
                        },
                        ["_information_blocked"],
                    ),
                    _require(
                        (
                            "_scene_can_see == true or (_target_state_id in record.though"
                            "t_detected and _information_blocked != true)"
                        ),
                        (
                            "Read a visible mind or one actually detected through the per"
                            "mitted barrier."
                        ),
                    ),
                    _set(
                        "_thought_patch",
                        {
                            "thought_target": "_target_state_id",
                            "attention_turn": "caster_state.turns_started",
                            "attention_tick": "state.lasting_tick",
                            "probe_started": False,
                        },
                    ),
                    _when(
                        "mode == 'probe'",
                        [
                            _require(
                                (
                                    "record.thought_target == _target_state_id and ((comb"
                                    "at.encounter_active == true and caster_state.turns_s"
                                    "tarted > record.attention_turn) or (combat.encounter"
                                    "_active != true and state.lasting_tick >= record.att"
                                    "ention_tick + 2))"
                                ),
                                (
                                    "Probe only on a later turn after starting this targe"
                                    "t's surface reading."
                                ),
                            ),
                            _set("target_state.detect_thoughts_probe_known", True),
                            _call(
                                "resolve_srd_divination_save",
                                {
                                    "target_state": "{_target_state_id}",
                                    "ability": "wisdom",
                                    "dc": "{record.save_dc}",
                                },
                                ["_divination_save_success"],
                            ),
                            {
                                "condition": "_divination_save_success == true",
                                "on_true": [_set("_thought_ended", True)],
                                "on_false": [
                                    _set(
                                        "_thought_patch",
                                        {
                                            "thought_target": "_target_state_id",
                                            "attention_turn": "record.attention_turn",
                                            "attention_tick": "record.attention_tick",
                                            "probe_started": True,
                                        },
                                    ),
                                ],
                            },
                        ],
                    ),
                    _when(
                        "_thought_ended != true",
                        [
                            _require(
                                "content != ''",
                                (
                                    "The GM supplies current surface thoughts or the sour"
                                    "ce's deeper reasoning, emotions and looming concern."
                                ),
                            ),
                            _when(
                                "mode == 'probe'",
                                [
                                    _call(
                                        "check_srd_mind_blank_information",
                                        {
                                            "target_state": "{_target_state_id}",
                                            "lasting_state": "state",
                                            "query_kind": "sensing_emotions",
                                        },
                                        ["_mind_blank_information_blocked"],
                                    ),
                                    _require(
                                        "_mind_blank_information_blocked != true",
                                        "Mind Blank blocks sensing emotions.",
                                    ),
                                ],
                            ),
                            _push("_thought_information", "content"),
                            _set("_thought_present", True),
                        ],
                    ),
                ],
            },
            _set("_thought_record", "merge(record, _thought_patch)"),
        ],
    )
    cast = _family_tool(
        (
            "At casting, choose presence sensing or current surface reading; install its "
            "actual concentration source."
        ),
        [
            _call(
                "select_srd_detect_thoughts",
                {
                    "caster_state": "{_caster_state_id}",
                    "target_state": "{_target_state_id}",
                    "record": "{record}",
                    "mode": "{divination_thought_mode}",
                    "content": "{divination_gm_answer}",
                },
                ["_thought_record", "_thought_information"],
            ),
            _set("_divination_record", "_thought_record"),
            _set("_divination_answer", "_thought_information"),
        ],
    )
    activate = _public(
        _tool(
            (
                "Spend a later Magic action to sense, read or probe under the exact Detec"
                "t Thoughts source."
            ),
            [
                _entity("caster_state"),
                _entity("target_state"),
                _enum("mode", ["sense", "read", "probe"]),
                _optional("content", default=""),
            ],
            [
                _call(
                    "collect_srd_divination_record",
                    {"caster_state": "{_caster_state_id}", "spell": "detect_thoughts"},
                    ["_divination_record", "_divination_active"],
                ),
                _require("_divination_active == true", "Detect Thoughts is no longer active."),
                *_action(),
                _call(
                    "select_srd_detect_thoughts",
                    {
                        "caster_state": "{_caster_state_id}",
                        "target_state": "{_target_state_id}",
                        "record": "{_divination_record}",
                        "mode": "{mode}",
                        "content": "{content}",
                    },
                    [
                        "_thought_record",
                        "_thought_information",
                        "_thought_present",
                        "_thought_ended",
                    ],
                ),
                {
                    "condition": "_thought_ended == true",
                    "on_true": [
                        _call(
                            "end_srd_concentration_node",
                            {
                                "source_state": "{_caster_state_id}",
                                "target_state": "{caster_state.concentration_target_state_id}",
                            },
                        ),
                    ],
                    "on_false": [
                        _call("save_srd_divination_record", {"record": "{_thought_record}"})
                    ],
                },
            ],
        ),
        "exploration",
        "Thought information",
        [("Information", "_thought_information"), ("Spell ended", "_thought_ended")],
    )
    contest = _public(
        _tool(
            (
                "The probed creature spends its own Action on an actual Intelligence (Arc"
                "ana) check to end the spell."
            ),
            [
                _entity("caster_state"),
                _entity("target_state"),
                _optional("actor", resolve="participant"),
            ],
            [
                _call(
                    "collect_srd_divination_record",
                    {"caster_state": "{_caster_state_id}", "spell": "detect_thoughts"},
                    ["_divination_record", "_divination_active"],
                ),
                _require(
                    "_divination_active == true and _divination_record.probe_started == true and "
                    "_divination_record.thought_target == _target_state_id",
                    "Only the currently probed creature can contest this spell.",
                ),
                *_action("target_state"),
                _set("_thought_arcana_proficiency", "'none'"),
                _set("_thought_arcana_modifier", 0),
                {
                    "condition": "actor == null",
                    "on_true": [
                        _set("_actor_id", None),
                        _require(
                            "target_state.skill_modifiers.arcana != null",
                            "Record the actual NPC Intelligence (Arcana) modifier.",
                        ),
                        _set("_thought_arcana_modifier", "target_state.skill_modifiers.arcana"),
                    ],
                    "on_false": [
                        _require(
                            "actor.participant_id == _target_state_id",
                            "The Arcana check uses this actual probed creature.",
                        ),
                        _when(
                            (
                                "actor.data.skill_proficiencies != null and 'arcana' in a"
                                "ctor.data.skill_proficiencies"
                            ),
                            [_set("_thought_arcana_proficiency", "'normal'")],
                        ),
                    ],
                },
                _call(
                    "d20_test",
                    {
                        "actor": "{_actor_id}",
                        "actor_state": "{_target_state_id}",
                        "ability": "intelligence",
                        "skill": "arcana",
                        "kind": "ability_check",
                        "ability_modifier": "{_thought_arcana_modifier}",
                        "proficiency_bonus": 0,
                        "proficiency_factor": "{_thought_arcana_proficiency}",
                        "circumstantial_modifier": 0,
                        "target_number": "{_divination_record.save_dc}",
                    },
                    ["_outcome"],
                ),
                _set("_thought_contest_success", "_outcome == 'success'"),
                _when(
                    "_thought_contest_success == true",
                    [
                        _call(
                            "end_srd_concentration_node",
                            {
                                "source_state": "{_caster_state_id}",
                                "target_state": "{caster_state.concentration_target_state_id}",
                            },
                        ),
                    ],
                ),
            ],
        ),
        "exploration",
        "Thought contest",
        [("Spell ended", "_thought_contest_success")],
    )
    # Contest uses an ordinary Action; only the caster's later operations are Magic actions.
    contest["effects"] = _ordinary_action(contest["effects"])
    return {
        "check_srd_detect_thoughts_eligible": eligibility,
        "select_srd_detect_thoughts": select,
        "cast_srd_detect_thoughts": cast,
        "activate_srd_detect_thoughts": activate,
        "contest_srd_detect_thoughts": contest,
    }


def _ordinary_action(value):
    """Convert a generated contest branch to its source-authorized ordinary Action."""
    if isinstance(value, list):
        return [_ordinary_action(v) for v in value]
    if isinstance(value, dict):
        result = {k: _ordinary_action(v) for k, v in value.items()}
        if result.get("call") == "spend_srd_action":
            result["params"]["magic_action"] = False
        return result
    return value


def _mind_spike_tools():
    """Tracking and Invisible exceptions are derived from the caster's active exact source."""
    cast = _family_tool(
        "Resolve the visible target's Wisdom save, real Psychic damage and failed-save tracking.",
        [
            _call(
                "resolve_srd_scene_relation",
                {"actor_state": "{_caster_state_id}", "target_state": "{_target_state_id}"},
                ["_scene_distance", "_scene_same_plane", "_scene_can_see"],
            ),
            _require(
                "_scene_same_plane == true and _scene_distance <= 120 and _scene_can_see == true",
                "Mind Spike selects a visible creature within 120 feet.",
            ),
            _call(
                "resolve_srd_divination_save",
                {
                    "target_state": "{_target_state_id}",
                    "ability": "wisdom",
                    "dc": "{casting_save_dc}",
                },
                ["_divination_save_success"],
            ),
            _set("_mind_spike_dice_count", "3 + max(0, slot_level - 2)"),
            {
                "dice": {"expression": "{_mind_spike_dice_count}d8"},
                "store_as": "_mind_spike_damage",
            },
            _set("_mind_spike_amount", "_mind_spike_damage.total"),
            _when(
                "_divination_save_success == true",
                [_set("_mind_spike_amount", "_mind_spike_amount // 2")],
            ),
            _call(
                "apply_srd_damage",
                {
                    "target": "{_target_state_id}",
                    "amount": "{_mind_spike_amount}",
                    "adjustment": 0,
                    "damage_type": "psychic",
                    "source_state_id": "{_caster_state_id}",
                },
            ),
            _set("_divination_install", "_divination_save_success != true"),
            _set("_divination_answer", "_mind_spike_amount"),
        ],
    )
    check = _tool(
        (
            "Check Mind Spike's actual tracked target and same-plane caster without a sig"
            "ht or D20 edge."
        ),
        [_entity("caster_state"), _entity("target_state")],
        [
            _set("_mind_spike_tracking", False),
            _when(
                "state.has_divination_spells == true",
                [
                    _call(
                        "collect_srd_divination_record",
                        {"caster_state": "{_caster_state_id}", "spell": "mind_spike"},
                        ["_divination_record", "_divination_active"],
                    ),
                    _when(
                        (
                            "_divination_active == true and _divination_record.target == "
                            "_target_state_id"
                        ),
                        [
                            _call(
                                "resolve_srd_scene_geometry_relation",
                                {
                                    "actor_state": "{_caster_state_id}",
                                    "target_state": "{_target_state_id}",
                                },
                                [
                                    "_scene_same_plane",
                                    "_scene_target_position",
                                    "_scene_distance",
                                    "_scene_direction",
                                ],
                            ),
                            _call(
                                "check_srd_mind_blank_information",
                                {
                                    "target_state": "{_target_state_id}",
                                    "lasting_state": "state",
                                    "query_kind": "magical_location",
                                },
                                ["_mind_blank_information_blocked"],
                            ),
                            _set(
                                "_mind_spike_tracking",
                                (
                                    "_scene_same_plane == true and _mind_blank_informatio"
                                    "n_blocked != true"
                                ),
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    query = _public(
        _tool(
            (
                "Know only this source's actual same-plane tracked location; hidden and I"
                "nvisible give no benefits against this caster."
            ),
            [_entity("caster_state"), _entity("target_state")],
            [
                _call(
                    "check_srd_mind_spike_tracking",
                    {"caster_state": "{_caster_state_id}", "target_state": "{_target_state_id}"},
                    [
                        "_mind_spike_tracking",
                        "_scene_target_position",
                        "_scene_distance",
                        "_scene_direction",
                    ],
                ),
                _require(
                    "_mind_spike_tracking == true",
                    (
                        "This caster has no active permitted same-plane tracking source f"
                        "or that creature."
                    ),
                ),
                _set("_mind_spike_position", "_scene_target_position"),
            ],
        ),
        "exploration",
        "Tracked creature",
        [
            ("Position", "_mind_spike_position"),
            ("Distance", "_scene_distance"),
            ("Direction", "_scene_direction"),
        ],
    )
    return {
        "cast_srd_mind_spike": cast,
        "check_srd_mind_spike_tracking": check,
        "query_srd_mind_spike": query,
    }


def _scrying_tools():
    """Remote observation uses an intangible source-owned sensor and actual target save history."""
    cast = _family_tool(
        (
            "Scry on a qualified same-plane creature or previously seen location after th"
            "e actual ten-minute casting."
        ),
        [
            _call(
                "srd_information_scene_entity",
                {"subject": "{_caster_state_id}"},
                ["_info_scene_entity"],
            ),
            _set("_scry_caster_plane", "_info_scene_entity.metadata.plane"),
            _require("_scry_caster_plane != null", "Record the caster's actual plane."),
            _set("_scry_position", "divination_destination"),
            _set("_scry_adjustment", 0),
            _set("_scry_target", "''"),
            _set("_scry_blocked", False),
            {
                "condition": "divination_scry_kind == 'creature'",
                "on_true": [
                    _require(
                        "target_state.is_alive == true",
                        "Scrying requires a living creature subject.",
                    ),
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_target_state_id}"},
                        ["_info_scene_entity", "_info_position"],
                    ),
                    _require(
                        "_info_scene_entity.metadata.plane == _scry_caster_plane",
                        "Scry only on a creature on the caster's plane.",
                    ),
                    _require(
                        (
                            "target_state.scrying_save_blocks == null or _caster_state_id"
                            " not in target_state.scrying_save_blocks or state.lasting_ti"
                            "ck >= target_state.scrying_save_blocks[_caster_state_id]"
                        ),
                        (
                            "A successful save prevents this caster from using Scrying on"
                            " this creature for 24 hours."
                        ),
                    ),
                    _set("_scry_target", "_target_state_id"),
                    _set("_scry_position", "_info_position"),
                    _when(
                        "divination_scry_knowledge == 'secondhand'", [_set("_scry_adjustment", 5)]
                    ),
                    _when(
                        "divination_scry_knowledge == 'extensive'", [_set("_scry_adjustment", -5)]
                    ),
                    _when(
                        "divination_scry_connection != 'none'",
                        [
                            _require(
                                (
                                    "divination_connection_item_id != '' and caster_state"
                                    ".spell_materials != null"
                                ),
                                "Select the actual held connection item.",
                            ),
                            _set("_scry_connection_found", False),
                            _set("_scry_materials", "caster_state.spell_materials"),
                            _loop(
                                "_scry_materials",
                                "_scry_material",
                                [
                                    _when(
                                        (
                                            "_scry_material.id == divination_connection_i"
                                            "tem_id and _scry_material.quantity > 0 and _"
                                            "scry_material.inventory_item in caster_state"
                                            ".inventory and _scry_material.scrying_subjec"
                                            "t == _target_state_id and _scry_material.scr"
                                            "ying_connection == divination_scry_connectio"
                                            "n"
                                        ),
                                        [_set("_scry_connection_found", True)],
                                    ),
                                ],
                            ),
                            _require(
                                "_scry_connection_found == true",
                                (
                                    "The admitted item must be this target's selected sou"
                                    "rce-qualified connection."
                                ),
                            ),
                            _when(
                                "divination_scry_connection == 'likeness'",
                                [_set("_scry_adjustment", "_scry_adjustment - 2")],
                            ),
                            _when(
                                "divination_scry_connection == 'possession'",
                                [_set("_scry_adjustment", "_scry_adjustment - 4")],
                            ),
                            _when(
                                "divination_scry_connection == 'body_part'",
                                [_set("_scry_adjustment", "_scry_adjustment - 10")],
                            ),
                        ],
                    ),
                    _call(
                        "check_srd_mind_blank_information",
                        {
                            "target_state": "{_target_state_id}",
                            "lasting_state": "state",
                            "query_kind": "remote_observation",
                        },
                        ["_mind_blank_information_blocked"],
                    ),
                    _set("_scry_blocked", "_mind_blank_information_blocked == true"),
                    _when(
                        "_scry_blocked != true",
                        [
                            _set("target_state.scrying_uneasy", True),
                            _call(
                                "resolve_srd_divination_save",
                                {
                                    "target_state": "{_target_state_id}",
                                    "ability": "wisdom",
                                    "dc": "{casting_save_dc}",
                                    "adjustment": "{_scry_adjustment}",
                                },
                                ["_divination_save_success"],
                            ),
                            _when(
                                "_divination_save_success == true",
                                [
                                    _when(
                                        "target_state.scrying_save_blocks == null",
                                        [_set("target_state.scrying_save_blocks", {})],
                                    ),
                                    _set(
                                        "target_state.scrying_save_blocks.{_caster_state_id}",
                                        "state.lasting_tick + 28800",
                                    ),
                                    _set("_scry_blocked", True),
                                ],
                            ),
                        ],
                    ),
                ],
                "on_false": [
                    _require(
                        "divination_subject_familiar == true",
                        "The location must have actually been seen before.",
                    ),
                    _require(
                        "divination_scry_connection == 'none'",
                        "Location scrying uses no creature connection modifier.",
                    ),
                ],
            },
            _set("_divination_install", "_scry_blocked != true"),
            _when(
                "_scry_blocked != true",
                [
                    _call(
                        "format_srd_information_projection",
                        {"message": "sensor:{record.instance_id}"},
                        ["_info_projection_message"],
                    ),
                    _set("_scry_proxy_id", "_info_projection_message"),
                    _call(
                        "create_srd_information_proxy",
                        {
                            "caster_state": "{_caster_state_id}",
                            "spell": "scrying",
                            "position": "{_scry_position}",
                            "proxy_id": "{_scry_proxy_id}",
                            "lighting": "bright",
                        },
                    ),
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_scry_proxy_id}"},
                        ["_info_scene_entity"],
                    ),
                    _set(
                        "_scry_sensor_attributes",
                        {
                            "intangible": True,
                            "invulnerable": False,
                            "appearance": "'luminous fist-sized orb'",
                        },
                    ),
                    _set(
                        "_scry_sensor_metadata",
                        "merge(_info_scene_entity.metadata, _scry_sensor_attributes)",
                    ),
                    {
                        "spatial_mutation": {
                            "scene": "state.spatial_scene",
                            "map_id": "{state.spatial_scene.map.id}",
                            "expected_revision": "{state.spatial_scene.map.revision}",
                            "target": {"kind": "entity", "id": "{_scry_proxy_id}"},
                            "patch": {"metadata": "{_scry_sensor_metadata}"},
                        },
                        "store_as": "_scry_sensor_patch",
                    },
                    _set(
                        "_scry_record_patch",
                        {
                            "proxy_id": "_scry_proxy_id",
                            "scry_kind": "divination_scry_kind",
                            "scry_target": "_scry_target",
                            "plane": "_scry_caster_plane",
                            "modality": "'seeing'",
                        },
                    ),
                    _set("_divination_record", "merge(record, _scry_record_patch)"),
                ],
            ),
            _set("_divination_answer", "'No information'"),
            _when(
                "_divination_install == true",
                [
                    _set(
                        "_divination_answer",
                        "'The saved sensor is ready for actual sight and hearing.'",
                    )
                ],
            ),
        ],
    )
    follow = _tool_scene(
        "Keep a creature sensor within ten feet by following the actual admitted subject position.",
        [_parameter("record", "object")],
        [
            _set("_scry_read_allowed", True),
            _call(
                "srd_information_scene_entity",
                {"subject": "{record.caster}"},
                ["_info_scene_entity"],
            ),
            _set("_scry_read_allowed", "_info_scene_entity.metadata.plane == record.plane"),
            _when(
                "record.scry_kind == 'creature'",
                [
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{record.scry_target}"},
                        ["_info_scene_entity", "_info_position"],
                    ),
                    _set(
                        "_scry_read_allowed",
                        (
                            "_scry_read_allowed == true and _info_scene_entity.metadata.p"
                            "lane == record.plane"
                        ),
                    ),
                    _call(
                        "check_srd_mind_blank_information",
                        {
                            "target_state": "{record.scry_target}",
                            "lasting_state": "state",
                            "query_kind": "remote_observation",
                        },
                        ["_mind_blank_information_blocked"],
                    ),
                    _set(
                        "_scry_read_allowed",
                        "_scry_read_allowed == true and _mind_blank_information_blocked != true",
                    ),
                    _when(
                        "_scry_read_allowed == true",
                        [
                            {
                                "spatial_mutation": {
                                    "scene": "state.spatial_scene",
                                    "map_id": "{state.spatial_scene.map.id}",
                                    "expected_revision": "{state.spatial_scene.map.revision}",
                                    "target": {"kind": "entity", "id": "{record.proxy_id}"},
                                    "patch": {"position": "{_info_position}"},
                                },
                                "store_as": "_scry_sensor_follow",
                            },
                        ],
                    ),
                ],
            ),
        ],
    )
    read = _public(
        _tool(
            (
                "Receive sight and hearing from the actual active Scrying sensor, with cu"
                "rrent obstruction, senses and Mind Blank filtering."
            ),
            [_entity("caster_state")],
            [
                _call(
                    "collect_srd_divination_record",
                    {"caster_state": "{_caster_state_id}", "spell": "scrying"},
                    ["_divination_record", "_divination_active"],
                ),
                _require("_divination_active == true", "Scrying is no longer active."),
                _call(
                    "follow_srd_scrying_sensor",
                    {"record": "{_divination_record}"},
                    ["_scry_read_allowed"],
                ),
                _require(
                    "_scry_read_allowed == true",
                    (
                        "The subject or sensor is on another plane or protected from remo"
                        "te observation."
                    ),
                ),
                _call(
                    "perceive_srd_information_proxy",
                    {"caster_state": "{_caster_state_id}", "record": "{_divination_record}"},
                    ["_info_results"],
                ),
                _set("_scry_sight", "_info_results"),
                _set("_scry_hearing_patch", {"modality": "'hearing'"}),
                _set("_scry_hearing_record", "merge(_divination_record, _scry_hearing_patch)"),
                _call(
                    "perceive_srd_information_proxy",
                    {"caster_state": "{_caster_state_id}", "record": "{_scry_hearing_record}"},
                    ["_info_results"],
                ),
                _set("_scry_hearing", "_info_results"),
            ],
        ),
        "exploration",
        "Scrying sensor",
        [("Sight", "_scry_sight"), ("Hearing", "_scry_hearing")],
    )
    refresh = _tool(
        "Refresh active following sensors immediately after authoritative scene movement.",
        [],
        [
            _when(
                "state.has_divination_spells == true",
                [
                    _set("_scry_follow_records", "state.turn_effects"),
                    _loop(
                        "_scry_follow_records",
                        "_scry_follow_record",
                        [
                            _when(
                                (
                                    "_scry_follow_record.kind == 'divination_spell' and _"
                                    "scry_follow_record.spell == 'scrying' and _scry_foll"
                                    "ow_record.scry_kind == 'creature'"
                                ),
                                [
                                    _call(
                                        "check_srd_divination_record",
                                        {
                                            "caster_state": "{_scry_follow_record.caster}",
                                            "record": "{_scry_follow_record}",
                                        },
                                        ["_divination_active"],
                                    ),
                                    _when(
                                        "_divination_active == true",
                                        [
                                            _call(
                                                "follow_srd_scrying_sensor",
                                                {"record": "{_scry_follow_record}"},
                                            )
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
    return {
        "cast_srd_scrying": cast,
        "follow_srd_scrying_sensor": follow,
        "read_srd_scrying_sensor": read,
        "refresh_srd_divination_spatial": refresh,
    }


def _identify_tools():
    """Identify touches an admitted subject throughout its actual casting interval."""
    touch = _tool_scene(
        "Validate actual same-plane touch and the source-qualified continuous contact fact.",
        [_entity("caster_state"), _parameter("subject"), _parameter("touch_confirmed", "boolean")],
        [
            _require(
                "subject != '' and touch_confirmed == true",
                "Identify requires continuous physical touch of its actual subject.",
            ),
            _call(
                "srd_information_scene_entity",
                {"subject": "{_caster_state_id}"},
                ["_info_scene_entity", "_info_position"],
            ),
            _set("_identify_origin", "_info_position"),
            _set("_identify_plane", "_info_scene_entity.metadata.plane"),
            _set("_identify_caster_scene_id", "_info_scene_entity.id"),
            _call(
                "srd_information_scene_entity",
                {"subject": "{subject}"},
                ["_info_scene_entity", "_info_position"],
            ),
            _set("_identify_subject_scene_id", "_info_scene_entity.id"),
            _require(
                "_identify_plane != null and _info_scene_entity.metadata.plane == _identify_plane",
                "Touch cannot extend between planes.",
            ),
            _call(
                "measure_srd_information_distance",
                {"start": "{_identify_origin}", "end": "{_info_position}"},
                ["_scene_distance"],
            ),
            _set("_identify_touch_reach", 5),
            _when(
                "caster_state.melee_reach != null",
                [_set("_identify_touch_reach", "caster_state.melee_reach")],
            ),
            _require(
                "_scene_distance <= _identify_touch_reach",
                "The actual subject is outside the caster's physical touch reach.",
            ),
            _call(
                "srd_information_barriers",
                {"start": "{_identify_origin}", "end": "{_info_position}", "rule": "solid"},
                ["_information_blocked"],
            ),
            _require("_information_blocked != true", "A solid barrier prevents touch."),
        ],
    )
    movement = _tool(
        (
            "An actual movement of the touched subject or caster interrupts continuous Id"
            "entify contact."
        ),
        [_parameter("scene_entity_id")],
        [
            _when(
                "state.extended_casting_sources != null",
                [
                    _set("_identify_cast_sources", "state.extended_casting_sources"),
                    _loop(
                        "_identify_cast_sources",
                        "_identify_cast_source",
                        [
                            _call(
                                "interrupt_srd_identify_source_movement",
                                {
                                    "caster_state": "{_identify_cast_source}",
                                    "scene_entity_id": "{scene_entity_id}",
                                },
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    interrupted = _tool(
        (
            "Retire the actual extended-casting owner when continuous Identify touch is b"
            "roken by movement."
        ),
        [_entity("caster_state"), _parameter("scene_entity_id")],
        [
            _call(
                "format_srd_information_projection",
                {"message": "scene:{scene_entity_id}"},
                ["_info_projection_message"],
            ),
            _set("_identify_moved_scene_token", "_info_projection_message"),
            _call(
                "format_srd_information_projection",
                {"message": "scene:{caster_state.extended_casting.identify_caster_scene_id}"},
                ["_info_projection_message"],
            ),
            _set("_identify_caster_scene_token", "_info_projection_message"),
            _call(
                "format_srd_information_projection",
                {"message": "scene:{caster_state.extended_casting.identify_subject_scene_id}"},
                ["_info_projection_message"],
            ),
            _set("_identify_subject_scene_token", "_info_projection_message"),
            _when(
                (
                    "caster_state.concentration_effect_family == 'extended_casting' and c"
                    "aster_state.extended_casting.spell == 'identify' and (_identify_move"
                    "d_scene_token == _identify_caster_scene_token or _identify_moved_sce"
                    "ne_token == _identify_subject_scene_token)"
                ),
                [
                    _call("interrupt_srd_extended_casting", {"caster_state": "{_caster_state_id}"}),
                ],
            ),
        ],
    )
    cast = _family_tool(
        (
            "Identify the admitted object properties and actual active spells after conti"
            "nuously touching the same subject throughout casting."
        ),
        [
            _require(
                "caster_state.identify_ready_subject == divination_subject",
                "Complete continuous Identify touch of this same subject first.",
            ),
            _set("caster_state.identify_ready_subject", None),
            _set("_divination_install", False),
            _call(
                "srd_information_scene_entity",
                {"subject": "{divination_subject}"},
                ["_info_scene_entity"],
            ),
            _set("_identify_subject", "_info_scene_entity"),
            _set("_identify_affected_subject", "divination_subject"),
            _when(
                "_identify_subject.metadata.participant_ref != null",
                [
                    _call(
                        "format_srd_information_projection",
                        {"message": "{_identify_subject.metadata.participant_ref}"},
                        ["_info_projection_message"],
                    ),
                    _set("_identify_affected_subject", "_info_projection_message"),
                ],
            ),
            _set("_identify_information", []),
            _set("_identify_protected", False),
            _when(
                "_identify_subject.metadata.participant_ref != null",
                [
                    _call(
                        "check_srd_mind_blank_information",
                        {
                            "target_state": "{_identify_subject.metadata.participant_ref}",
                            "lasting_state": "state",
                            "query_kind": "information_gathering",
                        },
                        ["_mind_blank_information_blocked"],
                    ),
                    _set("_identify_protected", "_mind_blank_information_blocked == true"),
                ],
            ),
            _when(
                "_identify_protected != true",
                [
                    _when(
                        "_identify_subject.metadata.is_object == true",
                        [
                            _when(
                                "_identify_subject.metadata.magic_item == true or "
                                "_identify_subject.metadata.magical_object == true or "
                                "_identify_subject.metadata.identify_properties != null",
                                [
                                    _require(
                                        (
                                            "_identify_subject.metadata.identify_properties != nu"
                                            "ll and _identify_subject.metadata.identify_how_to_us"
                                            "e != null and _identify_subject.metadata.identify_re"
                                            "quires_attunement != null and _identify_subject.meta"
                                            "data.identify_charges != null"
                                        ),
                                        (
                                            "Record this object's actual properties, use, attunem"
                                            "ent requirement and current charges as world facts."
                                        ),
                                    ),
                                    _call(
                                        "format_srd_information_projection",
                                        {
                                            "message": (
                                                "Properties: {_identify_subject.metadata.identify"
                                                "_properties}; use: {_identify_subject.metadata.i"
                                                "dentify_how_to_use}; attunement: {_identify_subj"
                                                "ect.metadata.identify_requires_attunement}; char"
                                                "ges: {_identify_subject.metadata.identify_charge"
                                                "s}"
                                            )
                                        },
                                        ["_info_projection_message"],
                                    ),
                                    _push("_identify_information", "_info_projection_message"),
                                ],
                            ),
                            _when(
                                "_identify_subject.metadata.created_by_spell != null",
                                [
                                    _call(
                                        "format_srd_information_projection",
                                        {
                                            "message": (
                                                "Created by: {_identify_subject.metadata."
                                                "created_by_spell}"
                                            )
                                        },
                                        ["_info_projection_message"],
                                    ),
                                    _push("_identify_information", "_info_projection_message"),
                                ],
                            ),
                        ],
                    ),
                    _set("_identify_lasting_sources", []),
                    _when(
                        "state.turn_effects != null",
                        [_set("_identify_lasting_sources", "state.turn_effects")],
                    ),
                    _loop(
                        "_identify_lasting_sources",
                        "_identify_source",
                        [
                            _when(
                                (
                                    "_identify_source.spell != null and _identify_source."
                                    "caster != null and (_identify_source.target == "
                                    "_identify_affected_subject or (_identify_source.members != "
                                    "null and _identify_affected_subject in "
                                    "_identify_source.members))"
                                ),
                                [
                                    _call(
                                        "check_srd_information_source_effect",
                                        {
                                            "source_state": "{_identify_source.caster}",
                                            "record": "{_identify_source}",
                                        },
                                        ["_information_effect_active"],
                                    ),
                                    _when(
                                        (
                                            "_information_effect_active == true and _iden"
                                            "tify_source.spell not in _identify_informati"
                                            "on"
                                        ),
                                        [_push("_identify_information", "_identify_source.spell")],
                                    ),
                                ],
                            ),
                        ],
                    ),
                    _when(
                        "_identify_subject.metadata.participant_ref != null",
                        [
                            _call(
                                "identify_srd_creature_condition_spells",
                                {"target_state": "{_identify_subject.metadata.participant_ref}"},
                                ["_identify_condition_spells"],
                            ),
                            _loop(
                                "_identify_condition_spells",
                                "_identify_condition_spell",
                                [
                                    _when(
                                        "_identify_condition_spell not in _identify_information",
                                        [
                                            _push(
                                                "_identify_information", "_identify_condition_spell"
                                            )
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _set(
                "caster_state.last_identify",
                {
                    "subject": "divination_subject",
                    "information": "_identify_information",
                    "tick": "state.lasting_tick",
                },
            ),
            _set("_divination_answer", "_identify_information"),
        ],
    )
    conditions = _tool(
        "Identify only active installed timed spell conditions affecting this actual creature.",
        [_entity("target_state")],
        [
            _set("_identify_condition_spells", []),
            _call(
                "refresh_srd_timed_condition_target",
                {"target_state": "{_target_state_id}", "lasting_state": "state"},
            ),
            _when(
                "target_state.timed_condition_source_ids != null",
                [
                    _set(
                        "_identify_condition_source_ids", "target_state.timed_condition_source_ids"
                    ),
                    _loop(
                        "_identify_condition_source_ids",
                        "_identify_condition_source_id",
                        [
                            _set(
                                "_identify_condition_source",
                                "state.timed_condition_sources[_identify_condition_source_id]",
                            ),
                            _when(
                                "_identify_condition_source.installed == true",
                                [
                                    _push(
                                        "_identify_condition_spells",
                                        "_identify_condition_source.spell_id",
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    return {
        "validate_srd_identify_touch": touch,
        "interrupt_srd_identify_movement": movement,
        "interrupt_srd_identify_source_movement": interrupted,
        "cast_srd_identify": cast,
        "identify_srd_creature_condition_spells": conditions,
    }


def _courier_tools():
    """The messenger travels in the admitted scene at its source-derived daily rate."""
    cast = _family_tool(
        "Give an actual Tiny CR0 Beast a saved delivery directive after its Charisma save.",
        [
            _require(
                "len(message_words) >= 1 and len(message_words) <= 25",
                "Animal Messenger carries at most 25 words.",
            ),
            _require(
                "divination_visited_location == true and divination_recipient_description != ''",
                "Specify a location visited and a general description of the recipient.",
            ),
            _call(
                "resolve_srd_scene_relation",
                {"actor_state": "{_caster_state_id}", "target_state": "{_target_state_id}"},
                [
                    "_scene_distance",
                    "_scene_same_plane",
                    "_scene_can_see",
                    "_scene_actor_position",
                    "_scene_target_position",
                ],
            ),
            _require(
                "_scene_distance <= 30 and _scene_same_plane == true and _scene_can_see == true",
                "Select an actual visible Beast within thirty feet.",
            ),
            _require(
                (
                    "target_state.creature_type == 'beast' and target_state.size_rank == 0 "
                    "and target_state.challenge_rating_eighths != null"
                ),
                "Animal Messenger requires a Tiny Beast with its actual Challenge Rating.",
            ),
            _set("_divination_install", False),
            _when(
                "target_state.challenge_rating_eighths == 0",
                [
                    _call(
                        "resolve_srd_divination_save",
                        {
                            "target_state": "{_target_state_id}",
                            "ability": "charisma",
                            "dc": "{casting_save_dc}",
                        },
                        ["_divination_save_success"],
                    ),
                    _set("_divination_install", "_divination_save_success != true"),
                ],
            ),
            _when(
                "_divination_install == true",
                [
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_target_state_id}"},
                        ["_info_scene_entity"],
                    ),
                    _set("_courier_rate", "132000 * 28800 ** -1"),
                    _when(
                        "target_state.fly_speed != null and target_state.fly_speed > 0",
                        [_set("_courier_rate", "264000 * 28800 ** -1")],
                    ),
                    _set(
                        "target_state.animal_messenger",
                        {
                            "instance_id": "record.instance_id",
                            "caster": "_caster_state_id",
                            "origin": "_scene_actor_position",
                            "destination": "divination_destination",
                            "plane": "_info_scene_entity.metadata.plane",
                            "recipient_description": "divination_recipient_description",
                            "message_words": "message_words",
                            "phase": "'outbound'",
                            "expires_tick": "record.expires_tick",
                            "last_tick": "state.lasting_tick",
                            "rate_per_tick": "_courier_rate",
                            "travel_credit": 0,
                            "message_lost": False,
                        },
                    ),
                    _set(
                        "_divination_answer",
                        "'The Beast travels under its saved message directive.'",
                    ),
                ],
            ),
            _when(
                "_divination_install != true",
                [
                    _set(
                        "_divination_answer",
                        "'The Beast succeeds on its save; no messenger directive.'",
                    )
                ],
            ),
        ],
    )
    move = _public(
        _tool_scene(
            (
                "Advance an actual messenger along a clear scene segment using only trave"
                "l credit earned from elapsed time; deliver or return under its saved dir"
                "ective."
            ),
            [
                _entity("target_state"),
                _array("destination"),
                _optional("recipient_state", resolve="entity"),
                _optional("recipient_matches_description", "boolean", False),
            ],
            [
                _require(PENDING, "Resolve the pending action before messenger travel."),
                _require(
                    "target_state.animal_messenger != null and target_state.is_alive == true",
                    "Select the actual living source-owned messenger.",
                ),
                _set("_courier", "target_state.animal_messenger"),
                _when(
                    "_courier.phase == 'outbound' and state.lasting_tick >= _courier.expires_tick",
                    [
                        _call(
                            "expire_srd_animal_messenger",
                            {
                                "target_state": "{_target_state_id}",
                                "instance_id": "{_courier.instance_id}",
                            },
                        ),
                        _set("_courier", "target_state.animal_messenger"),
                    ],
                ),
                _require(
                    "_courier.phase == 'outbound' or _courier.phase == 'returning'",
                    "This messenger's directive is already resolved.",
                ),
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{_target_state_id}"},
                    ["_info_scene_entity", "_info_position"],
                ),
                _set("_courier_scene_id", "_info_scene_entity.id"),
                _require(
                    "_info_scene_entity.metadata.plane == _courier.plane",
                    "The messenger travels physically on its source plane.",
                ),
                _call(
                    "measure_srd_information_distance",
                    {"start": "{_info_position}", "end": "{destination}"},
                    ["_scene_distance"],
                ),
                _set(
                    "_courier_credit",
                    (
                        "_courier.travel_credit + (state.lasting_tick - _courier.last_tic"
                        "k) * _courier.rate_per_tick"
                    ),
                ),
                _require(
                    "_scene_distance <= _courier_credit + 0.000000000001",
                    (
                        "Advance real time to earn the messenger's 25-mile or flying 50-m"
                        "ile daily travel."
                    ),
                ),
                _call(
                    "srd_information_barriers",
                    {"start": "{_info_position}", "end": "{destination}", "rule": "solid"},
                    ["_information_blocked"],
                ),
                _require(
                    "_information_blocked != true",
                    "A solid barrier blocks this physical courier route segment.",
                ),
                {
                    "spatial_mutation": {
                        "scene": "state.spatial_scene",
                        "map_id": "{state.spatial_scene.map.id}",
                        "expected_revision": "{state.spatial_scene.map.revision}",
                        "target": {"kind": "entity", "id": "{_courier_scene_id}"},
                        "patch": {"position": "{destination}"},
                    },
                    "store_as": "_courier_scene_move",
                },
                _set("target_state.animal_messenger.last_tick", "state.lasting_tick"),
                _set(
                    "target_state.animal_messenger.travel_credit",
                    "max(0, _courier_credit - _scene_distance)",
                ),
                _set("_courier_delivered", False),
                _set("_courier_returned", False),
                _when(
                    (
                        "_courier.phase == 'outbound' and destination[0] == _courier.dest"
                        "ination[0] and destination[1] == _courier.destination[1] and des"
                        "tination[2] == _courier.destination[2] and recipient_state != nu"
                        "ll"
                    ),
                    [
                        _require(
                            (
                                "recipient_state.is_alive == true and recipient_matches_d"
                                "escription == true"
                            ),
                            (
                                "Deliver only to the living creature matching the saved g"
                                "eneral description."
                            ),
                        ),
                        _call(
                            "srd_information_scene_entity",
                            {"subject": "{_recipient_state_id}"},
                            ["_info_scene_entity", "_info_position"],
                        ),
                        _require(
                            (
                                "_info_scene_entity.metadata.plane == _courier.plane and "
                                "_info_position[0] == destination[0] and _info_position[1"
                                "] == destination[1] and _info_position[2] == destination"
                                "[2]"
                            ),
                            "The described recipient must actually be at the destination location.",
                        ),
                        _set(
                            "recipient_state.received_animal_message",
                            {
                                "words": "_courier.message_words",
                                "mimics_caster": True,
                                "caster": "_courier.caster",
                                "beast": "_target_state_id",
                                "tick": "state.lasting_tick",
                            },
                        ),
                        _set("target_state.animal_messenger.phase", "'delivered'"),
                        _set("_courier_delivered", True),
                    ],
                ),
                _when(
                    "_courier.phase == 'returning' and destination[0] == _courier.origin[0] and "
                    "destination[1] == _courier.origin[1] and destination[2] == _courier.origin[2]",
                    [
                        _set("target_state.animal_messenger.phase", "'returned'"),
                        _set("_courier_returned", True),
                    ],
                ),
                _set("_courier_position", "destination"),
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
    return {"cast_srd_animal_messenger": cast, "advance_srd_animal_messenger": move}


def _casting_tools():
    "Ordinary/extended casting owns components, costs and time; this owner resolves consequences."
    effects = [
        _lookup("spell", "_divination_profile"),
        _when("caster == null", [_set("_caster_id", None)]),
        _when("state.lasting_tick == null", [_set("state.lasting_tick", 0)]),
        _set("_spell_summary_rows", []),
        _set("_spell_detail_rows", []),
        _set("_divination_answer", "''"),
        _set("_divination_no_answer", False),
        _call("validate_srd_spell_words", {"words": "{message_words}"}),
        _set("_divination_duration", "_divination_profile.effect_rounds"),
        _set("_divination_install", "_divination_duration > 0"),
        _set("_divination_target", "_target_state_id"),
        _when(
            "_divination_profile.range_kind == 'self' and spell != 'scrying'",
            [_set("_divination_target", "_caster_state_id")],
        ),
        _when("state.lasting_serial == null", [_set("state.lasting_serial", 0)]),
        {"increment": {"field": "state.lasting_serial", "amount": 1}},
        _call(
            "format_srd_information_projection",
            {"message": "{_caster_state_id}:{spell}:{state.lasting_serial}"},
            ["_info_projection_message"],
        ),
        _set("_divination_instance_id", "_info_projection_message"),
        _when(
            "spell == 'animal_messenger'",
            [_set("_divination_duration", "14400 + max(0, slot_level - 2) * 28800")],
        ),
        _set(
            "_divination_record",
            {
                "kind": "'divination_spell'",
                "instance_id": "_divination_instance_id",
                "spell": "spell",
                "caster": "_caster_state_id",
                "source_serial": "state.lasting_serial",
                "target": "_divination_target",
                "owner": "'__time_only__'",
                "phase": "'time'",
                "turn": 1000000000,
                "started_tick": "state.lasting_tick",
                "expires_tick": "state.lasting_tick + _divination_duration * 2",
                "concentration": "_divination_profile.concentration",
                "magnitude": 0,
                "dice_count": 0,
                "damage_die": "''",
                "damage_type": "''",
                "questions_used": 0,
                "question_limit": 3,
                "save_dc": "casting_save_dc",
                "members": [],
                "message_words": "message_words",
                "reply_used": False,
                "proxy_id": "''",
                "thought_target": "''",
                "thought_detected": [],
                "probe_started": False,
                "attention_turn": "caster_state.turns_started",
                "attention_tick": "state.lasting_tick",
            },
        ),
        _when(
            "spell == 'augury' or spell == 'commune' or spell == 'divination'",
            [
                _call(
                    "check_srd_repeated_divination",
                    {"caster_state": "{_caster_state_id}", "spell": "{spell}"},
                    ["_divination_no_answer"],
                ),
                _when(
                    "_divination_no_answer == true",
                    [_set("_divination_install", False), _set("_divination_answer", "'No answer'")],
                ),
            ],
        ),
        _when(
            "spell == 'augury'",
            [
                _require(
                    "divination_event_tick >= state.lasting_tick and "
                    "divination_event_tick <= state.lasting_tick + 600",
                    "Augury concerns an action planned within the next 30 minutes.",
                ),
                _when(
                    "_divination_no_answer != true", [_set("_divination_answer", "divination_omen")]
                ),
            ],
        ),
        _when(
            "spell == 'divination'",
            [
                _require(
                    "divination_event_tick >= state.lasting_tick and "
                    "divination_event_tick <= state.lasting_tick + 201600",
                    "Divination concerns a specific event within seven days.",
                ),
                _require(
                    "divination_subject != ''", "Describe the specific goal, event or activity."
                ),
                _when(
                    "_divination_no_answer != true",
                    [
                        _require(
                            "divination_gm_answer != ''",
                            "The GM supplies the source's truthful answer.",
                        ),
                        _set("_divination_answer", "divination_gm_answer"),
                    ],
                ),
            ],
        ),
        _when(
            "spell == 'legend_lore'",
            [
                _require("divination_subject != ''", "Name or describe the subject."),
                {
                    "condition": "divination_subject_famous == true",
                    "on_true": [
                        _require(
                            "divination_gm_answer != ''",
                            "The GM provides accurate lore scaled to existing knowledge.",
                        ),
                        _set("_divination_answer", "divination_gm_answer"),
                    ],
                    "on_false": [
                        _set(
                            "_divination_answer",
                            "'Sad trombone notes; the subject is not famous and the spell fails.'",
                        )
                    ],
                },
            ],
        ),
        _when(
            "spell == 'contact_other_plane'",
            [
                _call(
                    "resolve_srd_divination_save",
                    {
                        "actor": "{_caster_id}",
                        "target_state": "{_caster_state_id}",
                        "ability": "intelligence",
                        "dc": 15,
                    },
                    ["_divination_save_success"],
                ),
                {
                    "condition": "_divination_save_success == true",
                    "on_true": [
                        _set("_question_limit_update", {"question_limit": 5}),
                        _set(
                            "_divination_record",
                            "merge(_divination_record, _question_limit_update)",
                        ),
                    ],
                    "on_false": [
                        _set("_divination_install", False),
                        {"dice": {"expression": "6d6"}, "store_as": "_contact_damage"},
                        _call(
                            "apply_srd_damage",
                            {
                                "target": "{_caster_state_id}",
                                "amount": "{_contact_damage.total}",
                                "adjustment": 0,
                                "damage_type": "psychic",
                                "source_state_id": "{_caster_state_id}",
                            },
                        ),
                        _call(
                            "install_srd_timed_condition_source",
                            {
                                "target_state": "{_caster_state_id}",
                                "lasting_state": "state",
                                "spell": "contact_other_plane",
                                "condition": "incapacitated",
                                "save_dc": 15,
                                "duration_rounds": 0,
                                "repeat_save_timing": "none",
                                "save_ability": "intelligence",
                            },
                        ),
                        _set(
                            "_divination_answer",
                            (
                                "'Contact failed: Psychic damage and source-owned Incapac"
                                "itated until a Long Rest or Greater Restoration.'"
                            ),
                        ),
                    ],
                },
            ],
        ),
    ]
    for sid, helper in (
        ("message", "cast_srd_spell_message"),
        ("sending", "cast_srd_spell_message"),
        ("telepathic_bond", "cast_srd_telepathic_bond"),
        ("commune_with_nature", "cast_srd_nature_divination"),
        ("identify", "cast_srd_identify"),
        ("scrying", "cast_srd_scrying"),
        ("detect_thoughts", "cast_srd_detect_thoughts"),
        ("mind_spike", "cast_srd_mind_spike"),
        ("animal_messenger", "cast_srd_animal_messenger"),
    ):
        params = {p["name"]: "{" + p["name"] + "}" for p in CAST_CHOICES}
        params.update(
            caster="{_caster_id}",
            spell="{spell}",
            caster_state="{_caster_state_id}",
            target_state="{_target_state_id}",
            record="{_divination_record}",
            casting_save_dc="{casting_save_dc}",
            slot_level="{slot_level}",
            targets="{targets}",
            all_targets_willing="{all_targets_willing}",
            caster_can_touch_target="{caster_can_touch_target}",
            material_item_ids="{material_item_ids}",
        )
        effects.append(
            _when(
                f"spell == '{sid}'",
                [
                    _call(
                        helper,
                        params,
                        ["_divination_record", "_divination_install", "_divination_answer"],
                    )
                ],
            )
        )
    effects += [
        _when(
            "_divination_install == true",
            [
                _when(
                    "_divination_profile.concentration == true",
                    [
                        _when(
                            "caster_state.concentration_spell_id != null",
                            [
                                _call(
                                    "end_srd_concentration_node",
                                    {
                                        "source_state": "{_caster_state_id}",
                                        "target_state": "{caster_state."
                                        "concentration_target_state_id}",
                                    },
                                ),
                            ],
                        ),
                        _set("caster_state.concentration_spell_id", "spell"),
                        _set("caster_state.concentration_effect_family", "'divination_spell'"),
                        _set("caster_state.concentration_target_state_id", "_divination_target"),
                        _set("caster_state.concentration_target_state_ids", []),
                        _set("caster_state.concentration_started_tick", "state.lasting_tick"),
                        _set(
                            "caster_state.concentration_expires_tick",
                            "state.lasting_tick + _divination_duration * 2",
                        ),
                        _set("caster_state.concentration_save_modifier", "concentration_modifier"),
                    ],
                ),
                _call("save_srd_divination_record", {"record": "{_divination_record}"}),
                _push(
                    "_spell_detail_rows",
                    {"label": "'Spell instance'", "value": "_divination_record.instance_id"},
                ),
            ],
        ),
        _push("_spell_detail_rows", {"label": "'Divination'", "value": "_divination_answer"}),
    ]
    return {
        "validate_srd_spell_words": _word_tool(),
        "resolve_srd_divination_save": _save_tool(),
        "cast_spell_divination_spell": _tool_scene(
            "Resolve the complete source-defined divination after its real casting costs.",
            [
                _optional("caster", "string", None, resolve="participant"),
                _entity("caster_state"),
                _entity("target_state"),
                _parameter("spell"),
                _parameter("casting_save_dc", "integer"),
                _parameter("concentration_modifier", "integer"),
                _parameter("slot_level", "integer"),
                _parameter("caster_can_touch_target", "boolean"),
                _parameter("material_item_ids", "array", items={"type": "string"}),
                _optional("targets", "array", None, items={"type": "string"}),
                _parameter("all_targets_willing", "boolean"),
                *deepcopy(CAST_CHOICES),
            ],
            effects,
        ),
    }


def _schema():
    properties = {}
    for definition in PROFILES.values():
        for key, value in definition.items():
            if key != "family":
                properties[key] = {
                    "type": "boolean"
                    if type(value) is bool
                    else "integer"
                    if type(value) is int
                    else "string"
                }
    return {
        "type": "object",
        "properties": properties,
        "required": [k for k in properties if k != "duration_rounds"],
        "additionalProperties": False,
    }


def _normalize(changed):
    """Bind only supported expression grammar and simple call templates."""
    argument_index = 0

    def value(item):
        if isinstance(item, list):
            return [value(x) for x in item]
        if isinstance(item, dict):
            return {k: value(v) for k, v in item.items()}
        if isinstance(item, str):

            def membership(m):
                op, joiner = ("!=", " and ") if m[2] == "not in" else ("==", " or ")
                return "(" + joiner.join(f"{m[1]} {op} {x.strip()}" for x in m[3].split(",")) + ")"

            return re.sub(r"([A-Za-z_][A-Za-z0-9_.]*) (not in|in) \[([^\]]+)\]", membership, item)
        return item

    def effects(items):
        nonlocal argument_index
        output = []
        for effect in items:
            for key in ("on_true", "on_false"):
                if key in effect:
                    effect[key] = effects(effect[key])
            if "for_each" in effect:
                effect["for_each"]["do"] = effects(effect["for_each"]["do"])
            if "call" in effect:
                for name, parameter in effect["params"].items():
                    if (
                        isinstance(parameter, str)
                        and re.fullmatch(r"\{[^{}]+\}", parameter)
                        and not re.fullmatch(r"\{[A-Za-z_][A-Za-z0-9_.]*\}", parameter)
                    ):
                        argument_index += 1
                        local = f"_divination_argument_{argument_index}"
                        output.append(_set(local, parameter[1:-1]))
                        effect["params"][name] = "{" + local + "}"
            output.append(effect)
        return output

    for name, tool in changed.items():
        changed[name] = value(tool)
        changed[name]["effects"] = effects(changed[name]["effects"])


def _consumer_hooks(tools):
    """Attach source-owned consequences at the actual sight, movement and casting boundaries."""
    changed = {}
    begin = deepcopy(tools["begin_srd_spell_cast"])
    present = {p["name"] for p in begin["parameters"]}
    begin["parameters"] += [
        deepcopy(p)
        for p in CAST_CHOICES
        if p["name"] in {"divination_subject", "divination_touch_uninterrupted"}
        and p["name"] not in present
    ]
    if "validate_srd_identify_touch" not in str(begin["effects"]):
        begin["effects"].insert(
            0,
            _when(
                "spell == 'identify'",
                [
                    _call(
                        "validate_srd_identify_touch",
                        {
                            "caster_state": "{_caster_state_id}",
                            "subject": "{divination_subject}",
                            "touch_confirmed": "{divination_touch_uninterrupted}",
                        },
                        ["_identify_caster_scene_id", "_identify_subject_scene_id"],
                    ),
                ],
            ),
        )
        begin["effects"].append(
            _when(
                "spell == 'identify' and caster_state.extended_casting != null and "
                "caster_state.extended_casting.spell == spell",
                [
                    _set("caster_state.extended_casting.identify_subject", "divination_subject"),
                    _set(
                        "caster_state.extended_casting.identify_caster_scene_id",
                        "_identify_caster_scene_id",
                    ),
                    _set(
                        "caster_state.extended_casting.identify_subject_scene_id",
                        "_identify_subject_scene_id",
                    ),
                    _set("caster_state.identify_ready_subject", None),
                ],
            )
        )
    changed["begin_srd_spell_cast"] = begin
    continuing = deepcopy(tools["continue_srd_spell_cast"])
    if "divination_touch_uninterrupted" not in {p["name"] for p in continuing["parameters"]}:
        continuing["parameters"].append(
            _optional("divination_touch_uninterrupted", "boolean", False)
        )
    if "validate_srd_identify_touch" not in str(continuing["effects"]):
        continuing["effects"].insert(
            0,
            _when(
                "caster_state.extended_casting.spell == 'identify'",
                [
                    _call(
                        "validate_srd_identify_touch",
                        {
                            "caster_state": "{_caster_state_id}",
                            "subject": "{caster_state.extended_casting.identify_subject}",
                            "touch_confirmed": "{divination_touch_uninterrupted}",
                        },
                    ),
                ],
            ),
        )
    changed["continue_srd_spell_cast"] = continuing
    movement = deepcopy(tools["move_srd_scene_creature_node"])
    if "interrupt_srd_identify_movement" not in str(movement["effects"]):
        movement["effects"].append(
            _call(
                "interrupt_srd_identify_movement", {"scene_entity_id": "{_scene_actor_entity_id}"}
            )
        )
    if "refresh_srd_divination_spatial" not in str(movement["effects"]):
        movement["effects"].append(_call("refresh_srd_divination_spatial", {}))
    changed["move_srd_scene_creature_node"] = movement
    # The senses owner regenerates its public leaf before this continuation on every build.
    sight_name = "resolve_srd_sight"
    sight = deepcopy(tools[sight_name])
    if "check_srd_mind_spike_tracking" not in str(sight["effects"]):
        index = next(
            i
            for i, e in enumerate(sight["effects"])
            if e.get("set", {}).get("field") == "_sight_light"
        )
        sight["effects"].insert(
            index,
            _when(
                "subject_state != null and state.has_divination_spells == true",
                [
                    _call(
                        "check_srd_mind_spike_tracking",
                        {
                            "caster_state": "{_observer_state_id}",
                            "target_state": "{_subject_state_id}",
                        },
                        ["_mind_spike_tracking"],
                    ),
                    _when("_mind_spike_tracking == true", [_set("_sight_invisible", False)]),
                ],
            ),
        )
    bound_sight = _tool(
        sight["description"], sight["parameters"], sight["effects"], sight.get("pre_conditions", [])
    )
    bound_sight["metadata"] = sight["metadata"]
    changed[sight_name] = bound_sight
    attack = deepcopy(tools["resolve_srd_attack_conditions"])
    if "check_srd_mind_spike_tracking" not in str(attack["effects"]):
        attack["effects"].insert(
            0,
            _when(
                "state.has_divination_spells == true",
                [
                    _call(
                        "check_srd_mind_spike_tracking",
                        {"caster_state": "{_actor_state_id}", "target_state": "{_target_state_id}"},
                        ["_mind_spike_tracking"],
                    ),
                    _when(
                        "_mind_spike_tracking == true",
                        [
                            _call(
                                "resolve_srd_scene_relation",
                                {
                                    "actor_state": "{_actor_state_id}",
                                    "target_state": "{_target_state_id}",
                                },
                                ["_scene_can_see"],
                            ),
                            _set("actor_can_see_target", "_scene_can_see"),
                        ],
                    ),
                    _call(
                        "check_srd_mind_spike_tracking",
                        {"caster_state": "{_target_state_id}", "target_state": "{_actor_state_id}"},
                        ["_mind_spike_tracking"],
                    ),
                    _when(
                        "_mind_spike_tracking == true",
                        [
                            _call(
                                "resolve_srd_scene_relation",
                                {
                                    "actor_state": "{_target_state_id}",
                                    "target_state": "{_actor_state_id}",
                                },
                                ["_scene_can_see"],
                            ),
                            _set("target_can_see_actor", "_scene_can_see"),
                        ],
                    ),
                ],
            ),
        )
    bound_attack = _tool(
        attack["description"],
        attack["parameters"],
        attack["effects"],
        attack.get("pre_conditions", []),
    )
    bound_attack["metadata"] = attack["metadata"]
    changed["resolve_srd_attack_conditions"] = bound_attack
    perceive = deepcopy(tools["perceive_srd_information_proxy"])

    def hearing(value):
        if isinstance(value, str):
            return value.replace(
                "record.modality == 'hearing' and record.spell == 'clairvoyance'",
                (
                    "record.modality == 'hearing' and (record.spell == 'clairvoyance' or "
                    "record.spell == 'scrying')"
                ),
            )
        if isinstance(value, list):
            return [hearing(x) for x in value]
        if isinstance(value, dict):
            return {k: hearing(v) for k, v in value.items()}
        return value

    changed["perceive_srd_information_proxy"] = hearing(perceive)
    observe = deepcopy(tools["observe_srd_information_proxy_node"])
    if "collect_srd_divination_record" not in str(observe["effects"]):
        index = next(
            i
            for i, e in enumerate(observe["effects"])
            if e.get("call") == "collect_srd_information_record"
        )
        original = observe["effects"][index]
        divination = _call(
            "collect_srd_divination_record",
            {"caster_state": "{_observed_proxy.metadata.source_caster}", "spell": "scrying"},
        )
        divination["export_context"] = {
            "_info_active": "_divination_active",
            "_info_record": "_divination_record",
        }
        observe["effects"][index] = {
            "condition": "_observed_proxy.metadata.information_proxy == 'scrying'",
            "on_true": [divination],
            "on_false": [original],
        }

        def luminous(value):
            if isinstance(value, str):
                return value.replace(
                    "_observed_proxy.metadata.information_proxy == 'clairvoyance'",
                    (
                        "(_observed_proxy.metadata.information_proxy == 'clairvoyance' or"
                        " _observed_proxy.metadata.information_proxy == 'scrying')"
                    ),
                )
            if isinstance(value, list):
                return [luminous(x) for x in value]
            if isinstance(value, dict):
                return {k: luminous(v) for k, v in value.items()}
            return value

        observe = luminous(observe)
    changed["observe_srd_information_proxy_node"] = observe
    return changed


def _public_wrappers(changed):
    "Model-visible operations hide the internal state binding and export only their result fields."
    for name in PUBLIC_TOOLS:
        if name not in changed:
            continue
        tool = changed[name]
        if not any(p["name"] == "lasting_state" for p in tool["parameters"]):
            continue
        node = deepcopy(tool)
        node["metadata"] = {
            "category": "internal",
            "visible_to_player": False,
            "costs_action": False,
        }
        changed[name + "_node"] = node
        tool["parameters"] = [p for p in tool["parameters"] if p["name"] != "lasting_state"]
        params = {
            p["name"]: "{"
            + (
                "_" + p["name"] + "_id"
                if p.get("resolve") in {"entity", "participant"}
                else p["name"]
            )
            + "}"
            for p in tool["parameters"]
        }
        params["lasting_state"] = "state"
        exports = [
            row["value"]["path"].removeprefix("context.")
            for row in tool["metadata"]["result_card"]["summary"]
        ]
        initializers = [
            _when(p["name"] + " == null", [_set("_" + p["name"] + "_id", None)])
            for p in tool["parameters"]
            if p.get("resolve") in {"entity", "participant"} and p.get("required") is False
        ]
        tool["effects"] = [*initializers, _call(name + "_node", params, exports)]


def extend_runtime(text):
    """Regenerate this owner's tools and narrow casting, cleanup and later-consumer hooks."""
    tools = _load_runtime(text)["tools"]
    changed = (
        _record_tools()
        | _question_tools()
        | _casting_tools()
        | _communication_tools()
        | _nature_tools()
        | _cleanup_tools()
        | _thought_tools()
        | _mind_spike_tools()
        | _scrying_tools()
        | _identify_tools()
        | _courier_tools()
    )
    changed.update(_consumer_hooks(tools))
    deepen_divination_tools(changed, tools)
    deepen_identify_object_sources(changed)
    deepen_courier_tools(changed, tools)
    deepen_mind_spike_feedback(changed)
    _normalize(changed)
    _public_wrappers(changed)
    cast = deepcopy(tools["cast_spell"])
    prior = {p["name"] for p in cast["parameters"]}
    cast["parameters"] += [deepcopy(p) for p in CAST_CHOICES if p["name"] not in prior]
    if "validate_srd_identify_touch" not in str(cast["effects"]):
        identify_completion = _when(
            "spell == 'identify'",
            [
                _require(
                    "caster_state.extended_casting.spell == 'identify' and "
                    "caster_state.extended_casting.identify_subject == divination_subject",
                    "Resolve Identify only for the subject touched during its actual casting.",
                ),
                _call(
                    "validate_srd_identify_touch",
                    {
                        "caster_state": "{_caster_state_id}",
                        "subject": "{divination_subject}",
                        "touch_confirmed": "{divination_touch_uninterrupted}",
                    },
                ),
                _set("caster_state.identify_ready_subject", "divination_subject"),
            ],
        )

        def touch_before_completion(items):
            for effect in items:
                if (
                    "caster_state.extended_casting.progress_rounds" in str(effect)
                    and "on_true" in effect
                ):
                    direct = effect["on_true"]
                    if any(e.get("call") == "end_srd_concentration_node" for e in direct):
                        index = next(
                            i
                            for i, e in enumerate(direct)
                            if e.get("call") == "end_srd_concentration_node"
                        )
                        direct.insert(index, identify_completion)
                        return True
                for key in ("on_true", "on_false"):
                    if key in effect and touch_before_completion(effect[key]):
                        return True
            return False

        if not touch_before_completion(cast["effects"]):
            raise SystemExit("Identify requires the admitted longer-casting completion boundary")
    cast["effects"] = [
        e
        for e in cast["effects"]
        if e.get("condition") != "_spell_definition.family == 'divination_spell'"
    ]
    params = {p["name"]: "{" + p["name"] + "}" for p in CAST_CHOICES}
    params.update(
        caster="{_caster_id}",
        spell="{spell}",
        caster_state="{_caster_state_id}",
        target_state="{_scalar_target_id}",
        targets="{targets}",
        all_targets_willing="{all_targets_willing}",
        caster_can_touch_target="{_caster_can_touch_target}",
        casting_save_dc="{_casting_profile.save_dc}",
        concentration_modifier="{_casting_profile.concentration_save_modifier}",
        slot_level="{_slot_level}",
        material_item_ids="{material_item_ids}",
    )
    branch = _when(
        "_spell_definition.family == 'divination_spell'",
        [_call("cast_spell_divination_spell", params)],
    )
    branch["on_true"][0]["export_context"] = {
        "_nested_divination_summary": "_spell_summary_rows",
        "_nested_divination_details": "_spell_detail_rows",
    }
    branch["on_true"].insert(0, _when("caster == null", [_set("_caster_id", None)]))
    for suffix, local in (
        ("summary", "_nested_divination_summary"),
        ("detail", "_nested_divination_details"),
    ):
        branch["on_true"].append(
            _loop(
                local,
                "_divination_card_row",
                [_push(f"_spell_{suffix}_rows", "_divination_card_row")],
            )
        )
    last = max(
        i
        for i, e in enumerate(cast["effects"])
        if "_spell_definition.family ==" in e.get("condition", "")
    )
    cast["effects"].insert(last + 1, branch)
    deepen_divination_cast(cast)
    changed["cast_spell"] = cast
    rest = deepcopy(tools["complete_srd_rest"])
    if "clear_srd_contact_other_plane" not in str(rest["effects"]):
        rest["effects"].append(
            _when(
                "target_state.rest_kind == 'long'",
                [
                    _call("clear_srd_contact_other_plane", {"target_state": "{_target_state_id}"}),
                ],
            )
        )
    changed["complete_srd_rest"] = rest
    clock = deepcopy(changed.get("advance_srd_lasting_clock", tools["advance_srd_lasting_clock"]))
    if "refresh_srd_divination_spells" not in str(clock["effects"]):
        index = next(
            i
            for i, e in enumerate(clock["effects"])
            if e.get("set", {}).get("field") == "lasting_state.lasting_tick"
        )
        clock["effects"].insert(index + 1, _call("refresh_srd_divination_spells", {}))
    changed["advance_srd_lasting_clock"] = clock
    end = deepcopy(tools["end_srd_concentration_node"])
    if "remove_srd_divination_record" not in str(end["effects"]):
        end["effects"].insert(
            0,
            _when(
                "source_state.concentration_effect_family == 'divination_spell'",
                [
                    _call(
                        "remove_srd_divination_record",
                        {
                            "caster_state": "{_source_state_id}",
                            "spell": "{source_state.concentration_spell_id}",
                        },
                    ),
                ],
            ),
        )
    changed["end_srd_concentration_node"] = end
    text = _replace_tools(text, changed)
    if "      divination_spell:" not in text:
        schema = yaml.dump({"divination_spell": _schema()}, Dumper=_RuntimeDumper, sort_keys=False)
        text = text.replace(
            "      information_spell:",
            "".join("      " + line for line in schema.splitlines(True))
            + "\n      information_spell:",
            1,
        )
    return text
