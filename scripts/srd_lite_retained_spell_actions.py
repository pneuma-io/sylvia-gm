"""Keep selected SRD follow-ups reachable without enabling excluded saved sources."""

from copy import deepcopy

LONGER_CASTS = frozenset(
    {
        "commune",
        "contact_other_plane",
        "foresight",
        "geas",
        "legend_lore",
        "speak_with_animals",
    }
)
RETAINED_SPELL_ACTIONS = {
    "attack_srd_altered_natural_weapon": frozenset({"alter_self"}),
    "attack_srd_transformed_form": frozenset({"polymorph"}),
    "change_srd_transformation": frozenset({"alter_self"}),
    "dismiss_srd_information_spell": frozenset({"arcane_eye", "speak_with_animals"}),
    "dismiss_srd_divination_spell": frozenset({"commune", "contact_other_plane"}),
    "dismiss_srd_illumination_spell": frozenset({"light", "daylight"}),
    "dismiss_srd_senses_spell": frozenset({"darkvision"}),
    "begin_srd_extended_spell_action": LONGER_CASTS,
    "continue_srd_extended_spell_action": LONGER_CASTS,
    "resolve_srd_fear_turn": frozenset({"fear"}),
    "communicate_srd_spell": frozenset({"speak_with_animals"}),
    "set_srd_illumination_cover": frozenset({"light", "daylight"}),
}
SELECTED_SPELL = (
    "spell in definitions.srd_lite_implemented or "
    "(spell in definitions.srd_lite_aliases and "
    "definitions.srd_lite_aliases[spell] in definitions.srd_lite_implemented)"
)


def _require(condition, error):
    return {"require": {"condition": condition, "error": error}}


def _after_assignment(effects, field, effect):
    """Add a source check at the original record binding, before its consumer runs."""
    matches = 0
    for index in range(len(effects) - 1, -1, -1):
        current = effects[index]
        if current.get("set", {}).get("field") == field:
            effects.insert(index + 1, deepcopy(effect))
            matches += 1
        for branch in ("on_true", "on_false"):
            if isinstance(current.get(branch), list):
                matches += _after_assignment(current[branch], field, effect)
        if isinstance(current.get("for_each", {}).get("do"), list):
            matches += _after_assignment(current["for_each"]["do"], field, effect)
    return matches


def _bind_attack_card(operation, tool):
    """Use the already exported dice and totals; never reroll or change the outcome."""
    effects = tool["effects"]
    calls = [
        i
        for i, row in enumerate(effects)
        if row.get("call") == "resolve_adjudicated_creature_attack"
    ]
    if len(calls) != 1:
        raise ValueError("Review the changed transformation attack owner: " + operation)
    call = effects[calls[0]]
    exports = call.get("export_context", {})
    expected = {
        "_attack_roll": "_attack_roll",
        "_attack_total": "_attack_adjusted_total",
        "_attack_outcome": "_attack_outcome",
        "_effective_damage": "_damage_applied",
    }
    if any(exports.get(key) != value for key, value in expected.items()):
        raise ValueError(
            "Review the changed transformation attack receipt: " + operation
        )
    effects.insert(
        calls[0] + 1,
        {
            "set": {"field": "_lite_form_attack_modifier", "value": None},
        },
    )
    effects.insert(
        calls[0] + 2,
        {
            "set": {
                "field": "_lite_form_attack_modifier",
                "value": "_attack_total - _attack_roll.total",
            },
            "condition": "_attack_roll != null and _attack_total != null",
        },
    )
    tool.setdefault("metadata", {})["result_card"] = {
        "version": 1,
        "footprint": "compact",
        "title": "Alter Self attack"
        if operation.endswith("natural_weapon")
        else "Polymorph attack",
        "equation": {
            "die_sides": 20,
            "roll": {"path": "context._attack_roll.total"},
            "modifier": {"path": "context._lite_form_attack_modifier"},
            "total": {"path": "context._attack_total"},
            "optional": True,
        },
        "summary": [
            {"label": "Actor", "value": {"path": "context._actor_state_id"}},
            {"label": "Target", "value": {"path": "context._target_state_id"}},
            {
                "label": "Outcome",
                "value": {
                    "path": "context._attack_outcome",
                    "labels": {
                        "hit": "Hit",
                        "critical_hit": "Critical hit",
                        "miss": "Miss",
                    },
                },
            },
            {
                "label": "Damage applied",
                "value": {"path": "context._effective_damage"},
                "optional": True,
            },
        ],
        "details_label": "Recorded attack and damage dice",
        "details": [
            {
                "label": "D20 results",
                "value": {"path": "context._attack_roll.results"},
                "optional": True,
            },
            {
                "label": "Damage dice",
                "value": {"path": "context._damage_roll.results"},
                "optional": True,
            },
            {
                "label": "Hit Points lost",
                "value": {"path": "context._hp_lost"},
                "optional": True,
            },
        ],
    }


def bind_retained_spell_actions(runtime, selected):
    """Preserve native effects and add only the selected-source and display boundaries."""
    selected = set(selected)
    restored = set()
    for operation, owners in RETAINED_SPELL_ACTIONS.items():
        if not selected & owners:
            continue
        tool = deepcopy(runtime["tools"].get(operation))
        if not isinstance(tool, dict) or not tool.get("effects"):
            raise ValueError("Missing original retained spell action: " + operation)
        if any(
            row.get("require", {}).get("condition") == "false"
            for row in tool["effects"]
        ):
            raise ValueError(
                "Bind retained spell actions before Lite pending guards: " + operation
            )
        if (
            operation.startswith("dismiss_")
            or operation == "begin_srd_extended_spell_action"
        ):
            tool.setdefault("pre_conditions", []).insert(
                0,
                {
                    "condition": SELECTED_SPELL,
                    "error": "This follow-up action requires a selected SRD Lite spell.",
                },
            )
        elif operation == "continue_srd_extended_spell_action":
            tool["effects"].insert(
                0,
                _require(
                    "caster_state.extended_casting != null and "
                    "caster_state.extended_casting.spell in definitions.srd_lite_implemented",
                    "Continue only an existing selected SRD Lite cast.",
                ),
            )
        elif operation == "attack_srd_transformed_form":
            if (
                _after_assignment(
                    tool["effects"],
                    "_attacking_form",
                    _require(
                        "_attacking_form.spell_id == 'polymorph'",
                        "This form attack requires the selected Polymorph source.",
                    ),
                )
                != 1
            ):
                raise ValueError("Review the changed Polymorph source binding")
        elif operation == "change_srd_transformation":
            # The source is rebound for validation, then for the paid change.
            if (
                _after_assignment(
                    tool["effects"],
                    "_change_source",
                    _require(
                        "_change_source.spell_id == 'alter_self'",
                        "Only the selected Alter Self source can change its option here.",
                    ),
                )
                != 2
            ):
                raise ValueError("Review the changed Alter Self source binding")
        elif operation == "resolve_srd_fear_turn":
            tool["effects"][:0] = [
                _require(
                    "actor_state.control_behavior_source_ids != null",
                    "Fear's turn requires actual saved control sources.",
                ),
                {
                    "for_each": {
                        "collection": "actor_state.control_behavior_source_ids",
                        "as": "_lite_fear_source_id",
                        "do": [
                            {
                                "set": {
                                    "field": "_lite_fear_source",
                                    "value": (
                                        "lasting_state.timed_condition_sources[_lite_fear_source_id]"
                                    ),
                                }
                            },
                            _require(
                                "_lite_fear_source.control_profile != 'fear' or "
                                "_lite_fear_source.spell_id == 'fear'",
                                "This turn action supports only the selected Fear source.",
                            ),
                        ],
                    }
                },
            ]
            tool.setdefault("metadata", {})["result_card"] = {
                "version": 1,
                "footprint": "inline",
                "title": "Fear turn",
                "summary": [
                    {"label": "Actor", "value": {"path": "context._actor_state_id"}}
                ],
            }
        elif operation == "communicate_srd_spell":
            # This public consumer is restored for the selected Beast grant.
            tool["effects"].insert(
                0,
                _require(
                    "channel == 'speech' and speaker_state.creature_type == 'beast'",
                    "This SRD Lite communication action requires actual Beast speech.",
                ),
            )
            language_calls = [
                i
                for i, row in enumerate(tool["effects"])
                if row.get("call") == "resolve_srd_spell_language"
            ]
            if len(language_calls) != 1:
                raise ValueError("Review the changed Beast language owner")
            tool["effects"].insert(
                language_calls[0] + 1,
                _require(
                    "_language_influence_all_skills == true",
                    "Communicating with a Beast requires an active Speak with Animals grant.",
                ),
            )
            card = tool["metadata"]["result_card"]
            card["title"] = "Speak with Animals"
            for row in card["summary"]:
                if row["value"]["path"] in {
                    "context._language_influence_total",
                    "context._language_influence_outcome",
                }:
                    row["optional"] = True
                else:
                    row["value"]["labels"] = {"true": "Yes", "false": "No"}
        elif operation == "set_srd_illumination_cover":
            # Light and Daylight are timed, non-concentration object sources.
            tool["effects"][:0] = [
                {"set": {"field": "_lite_cover_allowed", "value": False}},
                {
                    "condition": "lasting_state.turn_effects != null",
                    "on_true": [
                        {
                            "set": {
                                "field": "_lite_cover_records",
                                "value": "lasting_state.turn_effects",
                            }
                        },
                        {
                            "for_each": {
                                "collection": "_lite_cover_records",
                                "as": "_lite_cover_record",
                                "do": [
                                    {
                                        "set": {
                                            "field": "_lite_cover_allowed",
                                            "value": True,
                                        },
                                        "condition": (
                                            "_lite_cover_record.kind == 'illumination_spell' and "
                                            "_lite_cover_record.source == object_id and "
                                            "(_lite_cover_record.spell == 'light' or "
                                            "_lite_cover_record.spell == 'daylight') and "
                                            "_lite_cover_record.spell in "
                                            "definitions.srd_lite_implemented and "
                                            "(_lite_cover_record.expires_tick == null or "
                                            "lasting_state.lasting_tick < "
                                            "_lite_cover_record.expires_tick)"
                                        ),
                                    }
                                ],
                            }
                        },
                    ],
                },
                _require(
                    "_lite_cover_allowed == true",
                    "Covering requires this object's active selected Light or Daylight source.",
                ),
            ]
            card = tool["metadata"]["result_card"]
            card["summary"][0]["value"]["labels"] = {"true": "Yes", "false": "No"}
        if operation.startswith("attack_srd_"):
            _bind_attack_card(operation, tool)
        if operation == "change_srd_transformation":
            tool.setdefault("metadata", {})["result_card"] = {
                "version": 1,
                "footprint": "inline",
                "title": "Alter Self changes",
                "summary": [
                    {"label": "Actor", "value": {"path": "context._actor_state_id"}},
                    {"label": "Option", "value": {"path": "parameters.option"}},
                ],
            }
        runtime["tools"][operation] = tool
        restored.add(operation)
    return restored
