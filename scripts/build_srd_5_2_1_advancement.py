"""Build the SRD's declarative advancement tools and Plan-owned policy requirement."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path

import yaml

from sylvia_gm.domain.package_library import canonical_json_bytes, participant_schema_sha256

if __package__:
    from scripts.build_srd_5_2_1_mechanics import RESOURCE_NAMES, _tool_schema
    from scripts.srd_equipment import (
        change_equipment_tool,
        creation_equipment,
        creation_validation,
        equipment_definitions,
        equipment_tool,
        initialize_npc_equipment_tool,
        movement_budget_tool,
        npc_equipment_tool,
        prospective_equipment_effects,
    )
    from scripts.srd_fighting_styles import level_up_effects as style_level_up_effects
    from scripts.srd_fighting_styles import tool as fighting_style_tool
    from scripts.srd_spell_grants import character_validation
else:
    from build_srd_5_2_1_mechanics import RESOURCE_NAMES, _tool_schema
    from srd_equipment import (
        change_equipment_tool,
        creation_equipment,
        creation_validation,
        equipment_definitions,
        equipment_tool,
        initialize_npc_equipment_tool,
        movement_budget_tool,
        npc_equipment_tool,
        prospective_equipment_effects,
    )
    from srd_fighting_styles import level_up_effects as style_level_up_effects
    from srd_fighting_styles import tool as fighting_style_tool
    from srd_spell_grants import character_validation

ROOT = Path(__file__).resolve().parents[1] / "rulesets" / "srd-lite"
PACKAGE_ID = "sylvia.srd-lite.bg3"
ABILITIES = ["strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"]
HIT_DICE = {
    "barbarian": 12,
    "bard": 8,
    "cleric": 8,
    "druid": 8,
    "fighter": 10,
    "monk": 8,
    "paladin": 10,
    "ranger": 10,
    "rogue": 8,
    "sorcerer": 6,
    "warlock": 8,
    "wizard": 6,
}
PRIMARY_ABILITIES = {
    "barbarian": ["strength"],
    "bard": ["charisma"],
    "cleric": ["wisdom"],
    "druid": ["wisdom"],
    "fighter": [],
    "monk": ["dexterity", "wisdom"],
    "paladin": ["strength", "charisma"],
    "ranger": ["dexterity", "wisdom"],
    "rogue": ["dexterity"],
    "sorcerer": ["charisma"],
    "warlock": ["charisma"],
    "wizard": ["intelligence"],
}
XP = [
    0,
    0,
    300,
    900,
    2700,
    6500,
    14000,
    23000,
    34000,
    48000,
    64000,
    85000,
    100000,
    120000,
    140000,
    165000,
    195000,
    225000,
    265000,
    305000,
    355000,
]


def text_schema(description=""):
    return {"type": "string", "minLength": 1, "description": description}


def object_schema(properties, required=None, *, additional=False):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties) if required is None else required,
        "additionalProperties": additional,
    }


def policy_declaration():
    """Require an explicit method and the information needed to adjudicate its awards."""
    level = {"type": "integer", "minimum": 1, "maximum": 20}
    milestone = object_schema(
        {
            "id": text_schema(
                "Stable award identity; use letters, digits, underscores or hyphens."
            ),
            "trigger": text_schema("Concrete event that earns the award."),
            "target_level": level,
            "alternative_outcomes": text_schema(
                "How failure or alternative outcomes affect the award."
            ),
        }
    )
    policy = object_schema(
        {
            "method": {"type": "string", "enum": ["fixed", "milestone", "xp"]},
            "starting_level": level,
            "maximum_level": level,
            "recipients": {"type": "string", "enum": ["whole_party", "per_character"]},
            "timing": text_schema("When earned advancement takes effect."),
            "allocation": text_schema(
                "Who receives each award and how XP is divided, if applicable."
            ),
            "milestones": {"type": "array", "items": milestone, "minItems": 1, "maxItems": 100},
            "xp_award_sources": text_schema(
                "Retained creature/stat-block and other award sources."
            ),
            "xp_threshold_source": {"type": "string", "enum": ["srd_5_2_1_character_advancement"]},
        },
        ["method", "starting_level", "maximum_level", "recipients", "timing", "allocation"],
    )
    policy["allOf"] = [
        {
            "type": "object",
            "if": object_schema({"method": {"type": "string", "enum": [method]}}, additional=True),
            "then": object_schema(
                {field: policy["properties"][field] for field in fields}, additional=True
            ),
        }
        for method, fields in [
            ("milestone", ["milestones"]),
            ("xp", ["xp_award_sources", "xp_threshold_source"]),
        ]
    ]
    for starting_level in range(1, 21):
        policy["allOf"].append(
            {
                "type": "object",
                "if": object_schema(
                    {"starting_level": {"type": "integer", "enum": [starting_level]}},
                    additional=True,
                ),
                "then": object_schema(
                    {"maximum_level": {"type": "integer", "minimum": starting_level}},
                    additional=True,
                ),
            }
        )
    for maximum in range(1, 21):
        policy["allOf"].append(
            {
                "type": "object",
                "if": object_schema(
                    {"maximum_level": {"type": "integer", "enum": [maximum]}}, additional=True
                ),
                "then": object_schema(
                    {
                        "milestones": {
                            "type": "array",
                            "items": object_schema(
                                {
                                    "target_level": {"type": "integer", "maximum": maximum},
                                },
                                additional=True,
                            ),
                        }
                    },
                    [],
                    additional=True,
                ),
            }
        )
    return {
        "key": "state",
        "required": True,
        "references": [],
        "schema": object_schema({"advancement": policy}, additional=True),
    }


def set_value(field, value):
    return {"set": {"field": field, "value": value}}


def require(condition, error):
    return {"require": {"condition": condition, "error": error}}


def conditional(condition, yes, no=None):
    return {"condition": condition, "on_true": yes, "on_false": no or []}


def each(collection, name, effects):
    local = f"_collection{name}"
    return conditional(
        "true",
        [
            set_value(local, collection),
            {"for_each": {"collection": local, "as": name, "do": effects}},
        ],
    )


def participant(field, value):
    return {"set_participant": {"participant": "_character_id", "field": field, "value": value}}


def hit_die_pools():
    """Retain each class's unspent dice without guessing a legacy multiclass spend history."""
    return [
        set_value("_pools", []),
        set_value("_pool_total", 0),
        each(
            "character.data.classes",
            "_die_class",
            [
                set_value("_available", "_die_class.level"),
                conditional(
                    "character_state.hit_dice_pools != null",
                    [
                        set_value("_pool_found", False),
                        each(
                            "character_state.hit_dice_pools",
                            "_old_pool",
                            [
                                conditional(
                                    "_old_pool.class_id == _die_class.id",
                                    [
                                        require(
                                            "_pool_found == false",
                                            "Hit Die class pools must be unique.",
                                        ),
                                        set_value("_pool_found", True),
                                        set_value("_available", "_old_pool.remaining"),
                                    ],
                                ),
                            ],
                        ),
                        require(
                            "_pool_found == true", "A class is missing its retained Hit Die pool."
                        ),
                    ],
                    [
                        conditional(
                            "len(character.data.classes) == 1",
                            [
                                set_value("_available", "character_state.resources.hit_die"),
                            ],
                            [
                                require(
                                    "character_state.resources.hit_die == character.data.level",
                                    "This character lacks spent Hit Dice by class. "
                                    "Restore its pools before levelling; no dice were guessed.",
                                ),
                            ],
                        ),
                    ],
                ),
                require(
                    "_available >= 0 and _available <= _die_class.level",
                    "Remaining Hit Dice must be within the previous class level.",
                ),
                set_value("_pool_total", "_pool_total + _available"),
                set_value("_pool_maximum", "_die_class.level"),
                conditional(
                    "_die_class.id == class_id",
                    [
                        set_value("_available", "_available + 1"),
                        set_value("_pool_maximum", "_pool_maximum + 1"),
                    ],
                ),
                {
                    "list_push": {
                        "list": "_pools",
                        "value": {
                            "class_id": "_die_class.id",
                            "size": "definitions.srd_advancement_hit_dice[_die_class.id]",
                            "remaining": "_available",
                            "maximum": "_pool_maximum",
                        },
                    },
                    "store_as": "_pools",
                },
            ],
        ),
        require(
            "_pool_total == character_state.resources.hit_die",
            "The class Hit Die pools must match the total remaining Hit Dice.",
        ),
        conditional(
            "_found == false",
            [
                {
                    "list_push": {
                        "list": "_pools",
                        "value": {
                            "class_id": "class_id",
                            "size": "definitions.srd_advancement_hit_dice[class_id]",
                            "remaining": 1,
                            "maximum": 1,
                        },
                    },
                    "store_as": "_pools",
                },
            ],
        ),
    ]


def short_rest_tool():
    """Spend a selected class die, retaining the existing single-pool path."""
    return {
        "description": "Immediately after a tracked Short Rest completes, spend one Hit Die "
        "and apply its healing. Choose further dice after each result, before advancing time "
        "or resuming other activity. For mixed "
        "class pools, ask which class's die to spend and supply hit_die_class. "
        "Omit it for older single-die characters or a single class pool.",
        "parameters": [
            parameter("target_state", "string", resolve="entity"),
            {"name": "hit_die_class", "type": "string", "required": False, "default": ""},
        ],
        "pre_conditions": [
            {
                "condition": "combat.encounter_active != true",
                "error": "Finish combat before a Short Rest.",
            },
            {
                "condition": "target_state.is_alive == true and target_state.hp >= 1 "
                "and target_state.hp < target_state.max_hp",
                "error": "The creature must be alive and wounded.",
            },
            {
                "condition": "target_state.resources.hit_die >= 1",
                "error": "No unspent Hit Die remains.",
            },
            {
                "condition": "target_state.constitution_modifier >= -10 "
                "and target_state.constitution_modifier <= 20",
                "error": "A Constitution modifier is required.",
            },
        ],
        "effects": [
            {
                "call": "read_srd_rest_clock",
                "params": {"lasting_state": "state"},
                "export_context": {"_rest_now": "_rest_now"},
            },
            require(
                "target_state.short_rest_hit_dice_available == true and "
                "target_state.short_rest_hit_dice_tick == _rest_now",
                "Complete an uninterrupted Short Rest and choose Hit Dice before continuing play.",
            ),
            set_value("_rest_hp_before", "target_state.hp"),
            set_value("_rest_die", "target_state.hit_die_size"),
            conditional(
                "target_state.hit_dice_pools != null",
                [
                    set_value("_selected_class", "hit_die_class"),
                    conditional(
                        "hit_die_class == '' and len(target_state.hit_dice_pools) == 1",
                        [
                            set_value("_selected_class", "target_state.hit_dice_pools[0].class_id"),
                        ],
                    ),
                    set_value("_selected", False),
                    set_value("_rest_pools", []),
                    each(
                        "target_state.hit_dice_pools",
                        "_pool",
                        [
                            conditional(
                                "_pool.class_id == _selected_class",
                                [
                                    require(
                                        "_selected == false and _pool.remaining >= 1",
                                        "Select a class with an unspent Hit Die.",
                                    ),
                                    set_value("_selected", True),
                                    set_value("_rest_die", "_pool.size"),
                                    {
                                        "list_push": {
                                            "list": "_rest_pools",
                                            "value": {
                                                "class_id": "_pool.class_id",
                                                "size": "_pool.size",
                                                "remaining": "_pool.remaining - 1",
                                                "maximum": "_pool.maximum",
                                            },
                                        },
                                        "store_as": "_rest_pools",
                                    },
                                ],
                                [
                                    {
                                        "list_push": {"list": "_rest_pools", "value": "_pool"},
                                        "store_as": "_rest_pools",
                                    }
                                ],
                            ),
                        ],
                    ),
                    require(
                        "_selected == true",
                        "Select hit_die_class from the character's available class pools.",
                    ),
                    set_value("{_target_state_id}.hit_dice_pools", "_rest_pools"),
                ],
            ),
            require(
                "_rest_die == 4 or _rest_die == 6 or _rest_die == 8 "
                "or _rest_die == 10 or _rest_die == 12",
                "An authoritative Hit Die size is required.",
            ),
            {
                "dice": {
                    "expression": "d{_rest_die}",
                    "modifier": "target_state.constitution_modifier",
                },
                "store_as": "_short_rest_hit_die_roll",
            },
            set_value(
                "_short_rest_roll_total",
                "_short_rest_hit_die_roll.total + _short_rest_hit_die_roll.modifier",
            ),
            set_value(
                "_short_rest_healing",
                "max(1, _short_rest_hit_die_roll.total + _short_rest_hit_die_roll.modifier)",
            ),
            {"decrement": {"field": "{_target_state_id}.resources.hit_die", "amount": 1}},
            {"heal": {"target": "target_state", "amount": "_short_rest_healing"}},
            set_value("_rest_hp_after", "target_state.hp"),
            set_value("_rest_hp_restored", "_rest_hp_after - _rest_hp_before"),
            {
                "log": {
                    "message": "One d{_rest_die} Hit Die was spent; Short Rest healing was applied."
                }
            },
        ],
        "metadata": {"category": "recovery", "costs_action": False, "result_card": {
            "version": 1, "footprint": "compact", "title": "Short Rest",
            "subtitle": "Hit Die healing",
            "equation": {
                "die_sides": {"path": "context._rest_die"},
                "roll": {"path": "context._short_rest_hit_die_roll.total"},
                "modifier": {"path": "context._short_rest_hit_die_roll.modifier"},
                "total": {"path": "context._short_rest_roll_total"},
            },
            "summary": [
                {"label": "Character", "value": {"path": "context.target_state.name"},
                 "optional": True},
                {"label": "HP restored", "value": {"path": "context._rest_hp_restored"},
                 "emphasis": True},
                {"label": "Hit Dice remaining", "value": {
                    "path": "context.target_state.resources.hit_die"}},
            ],
            "details_label": "Calculation",
            "details": [
                {"label": "Dice", "value": {"path": "context._short_rest_hit_die_roll.results"}},
                {"label": "Healing before HP cap",
                 "value": {"path": "context._short_rest_healing"}},
                {"label": "HP before", "value": {"path": "context._rest_hp_before"}},
                {"label": "HP after", "value": {"path": "context._rest_hp_after"}},
                {"label": "Hit Dice spent", "value": 1},
            ],
        }},
    }


def parameter(name, kind, **kwargs):
    return {"name": name, "type": kind, "required": True, **kwargs}


def focus_capacity(classes, output):
    """Derive Focus capacity from Monk class level, including zero before level two."""
    return [
        set_value(output, 0),
        each(classes, "_focus_class", [
            conditional("_focus_class.id == 'monk' and _focus_class.level >= 2", [
                set_value(output, "_focus_class.level"),
            ]),
        ]),
    ]


def creation_focus(creation):
    """Share the class-derived Focus maximum between starting validation and projection."""
    program = creation["initial_state_validation"]
    program["effects"].extend(focus_capacity("participant.classes", "_focus_maximum"))
    for parent in ("resources", "resource_maxima"):
        creation["entity_state_schema"]["properties"][parent]["properties"]["focus_point"] = {
            "type": "integer", "minimum": 0,
        }
    for path in ("resources.focus_point", "resource_maxima.focus_point"):
        program["effects"].append(conditional(f"state.{path} != null", [
            require(f"state.{path} == _focus_maximum",
                    "Starting Focus must match the Monk class level."),
        ]))
        creation["initial_state_calculations"] = [
            row for row in creation["initial_state_calculations"]
            if row["set"]["field"] != path
        ] + [{**set_value(path, "rules._focus_maximum"),
              "condition": "rules._focus_maximum > 0"}]


def wholeness_capacity(profile, output):
    """Derive the Open Hand healing pool from its earned feature and Wisdom."""
    return [
        set_value(output, 0),
        each(f"{profile}.classes", "_wholeness_class", [
            conditional(
                "_wholeness_class.id == 'monk' and _wholeness_class.level >= 6 and "
                f"{profile}.subclasses.monk == 'warrior_of_the_open_hand'", [
                    set_value(output, f"max(1, ({profile}.abilities.wisdom - 10) // 2)"),
                ],
            ),
        ]),
    ]


def creation_wholeness(creation):
    """Materialize the earned healing pool without requiring an authored maximum."""
    program = creation["initial_state_validation"]
    program["effects"].extend(wholeness_capacity("participant", "_wholeness_maximum"))
    for parent in ("resources", "resource_maxima"):
        creation["entity_state_schema"]["properties"][parent]["properties"]["wholeness_of_body"] = {
            "type": "integer", "minimum": 0,
        }
        path = f"{parent}.wholeness_of_body"
        program["effects"].append(conditional(f"state.{path} != null", [
            require(f"state.{path} == _wholeness_maximum",
                    "Starting Wholeness of Body uses must match the earned feature and Wisdom."),
        ]))
        creation["initial_state_calculations"] = [
            row for row in creation["initial_state_calculations"] if row["set"]["field"] != path
        ] + [{**set_value(path, "rules._wholeness_maximum"),
              "condition": "rules._wholeness_maximum > 0"}]


def resource_tool(*, restore):
    """Keep the existing resource operations coherent with per-class Hit Dice."""
    operation = "Restore" if restore else "Spend"
    regular = (
        "min(target.resource_maxima[resource], target.resources[resource] + amount)"
        if restore
        else "target.resources[resource] - amount"
    )
    selected = (
        "min(_pool.maximum, _pool.remaining + amount)" if restore else "_pool.remaining - amount"
    )
    labels = {name: name.replace("_", " ").title() for name in RESOURCE_NAMES}
    labels.update({f"spell_slot_{level}": f"Level {level} spell slot" for level in range(1, 10)})
    return {
        "description": f"{operation} a package-owned resource. For class Hit Dice, select "
        "hit_die_class for a partial change; restoring the full total restores every class pool. "
        + ("" if restore else "For Action Surge, call use_srd_action_surge instead: "
           "it also grants the action."),
        "parameters": [
            parameter("target", "string", resolve="entity"),
            parameter("resource", "enum", enum_values=list(RESOURCE_NAMES)),
            parameter("amount", "integer", minimum=1),
            {"name": "hit_die_class", "type": "string", "required": False, "default": ""},
        ],
        "pre_conditions": [
            {"condition": "amount >= 1", "error": "A resource change must be at least 1."},
            *([] if restore else [{
                "condition": "resource != 'action_surge'",
                "error": "Use use_srd_action_surge to spend its charge and grant "
                "the extra action atomically.",
            }]),
            {
                "condition": "target.resources[resource] != null and "
                "target.resource_maxima[resource] != null"
                if restore
                else "target.resources[resource] >= amount",
                "error": "The target needs the recorded resource and sufficient capacity.",
            },
        ],
        "effects": [
            set_value("_resource_before", "target.resources[resource]"),
            set_value("_resource_maximum", None),
            conditional("target.resource_maxima != null", [
                set_value("_resource_maximum", "target.resource_maxima[resource]"),
            ]),
            set_value("_resource_after", regular),
            conditional(
                "resource == 'hit_die' and target.hit_dice_pools != null",
                [
                    set_value("_selected_class", "hit_die_class"),
                    conditional(
                        "hit_die_class == '' and len(target.hit_dice_pools) == 1",
                        [set_value("_selected_class", "target.hit_dice_pools[0].class_id")],
                    ),
                    set_value("_restore_all", False),
                    *(
                        [
                            conditional(
                                "hit_die_class == '' and amount >= "
                                "target.resource_maxima.hit_die - target.resources.hit_die",
                                [set_value("_restore_all", True)],
                            )
                        ]
                        if restore
                        else []
                    ),
                    set_value("_matched", False),
                    set_value("_resource_after", 0),
                    set_value("_resource_pools", []),
                    each(
                        "target.hit_dice_pools",
                        "_pool",
                        [
                            set_value("_available", "_pool.remaining"),
                            conditional(
                                "_restore_all == true",
                                [
                                    set_value("_available", "_pool.maximum"),
                                    set_value("_matched", True),
                                ],
                                [
                                    conditional(
                                        "_pool.class_id == _selected_class",
                                        [
                                            require(
                                                "_matched == false",
                                                "Class Hit Die pools must be unique.",
                                            ),
                                            set_value("_matched", True),
                                            set_value("_available", selected),
                                        ],
                                    )
                                ],
                            ),
                            require(
                                "_available >= 0 and _available <= _pool.maximum",
                                "The selected class lacks enough available Hit Dice.",
                            ),
                            set_value("_resource_after", "_resource_after + _available"),
                            {
                                "list_push": {
                                    "list": "_resource_pools",
                                    "value": {
                                        "class_id": "_pool.class_id",
                                        "size": "_pool.size",
                                        "maximum": "_pool.maximum",
                                        "remaining": "_available",
                                    },
                                },
                                "store_as": "_resource_pools",
                            },
                        ],
                    ),
                    require("_matched == true", "Select the class Hit Die pool to change."),
                    set_value("{_target_id}.hit_dice_pools", "_resource_pools"),
                ],
            ),
            set_value("{_target_id}.resources.{resource}", "_resource_after"),
            conditional("resource == 'focus_point' and combat.turn_active == true "
                        "and combat.active_actor == _target_id", [{
                "call": "refresh_srd_monk_bonus_availability",
                "params": {"actor_state": "{_target_id}"},
            }]),
            set_value("_resource_changed", "_resource_after - _resource_before" if restore
                      else "_resource_before - _resource_after"),
        ],
        "metadata": {"category": "resources", "costs_action": False, "result_card": {
            "version": 1, "footprint": "inline",
            "title": "Resource restored" if restore else "Resource spent",
            "summary": [
                {"label": "Actor", "value": {"path": "context.target.name"}, "optional": True},
                {"label": "Resource", "value": {"path": "parameters.resource", "labels": labels}},
                {"label": "Recovered" if restore else "Spent",
                 "value": {"path": "context._resource_changed"}},
                {"label": "Remaining", "value": {"path": "context._resource_after"}},
                {"label": "Effect", "value": "Resource balance updated"},
            ],
            "details": [
                {"label": "Before", "value": {"path": "context._resource_before"}},
                {"label": "Maximum", "value": {"path": "context._resource_maximum"},
                 "optional": True},
            ],
        }},
    }


def references():
    return [
        parameter("character", "string", resolve="participant"),
        parameter(
            "character_state",
            "string",
            resolve="entity",
            description="The same character ID, selecting its live mechanical state.",
        ),
        parameter("policy", "object", from_state="state.advancement"),
    ]


def common_checks():
    return [
        {
            "condition": "_character_id == _character_state_id",
            "error": "Character profile and mechanical state must identify the same character.",
        },
        {
            "condition": "policy != null and "
            "(policy.method == 'milestone' or policy.method == 'xp')",
            "error": "This campaign needs an advancement policy that permits level-ups.",
        },
        {
            "condition": "combat.encounter_active != true",
            "error": "Finish combat before applying advancement.",
        },
    ]


def award_tool():
    """Record one named award for one character, independently of unresolved build choices."""
    effects = [
        set_value("_earned", "character.data.level"),
        conditional(
            "character_state.advancement_target != null",
            [set_value("_earned", "max(_earned, character_state.advancement_target)")],
        ),
        conditional(
            "policy.method == 'milestone'",
            [
                set_value("_found", False),
                each(
                    "policy.milestones",
                    "_milestone",
                    [
                        conditional(
                            "_milestone.id == award_id",
                            [
                                require(
                                    "_found == false", "Milestone IDs must be unique in the Plan."
                                ),
                                set_value("_found", True),
                                set_value("_earned", "max(_earned, _milestone.target_level)"),
                            ],
                        ),
                    ],
                ),
                require(
                    "_found == true", "Award ID must identify a milestone in the retained Plan."
                ),
                require("xp == 0", "Milestone campaigns do not award levelling XP."),
            ],
            [
                require("xp > 0", "XP awards require a positive per-character allocation."),
                set_value("_xp", 0),
                conditional(
                    "character_state.experience_points != null",
                    [set_value("_xp", "character_state.experience_points")],
                    [set_value("_xp", "definitions.srd_advancement_xp[character.data.level]")],
                ),
                set_value("_xp", "_xp + xp"),
                *[
                    conditional(
                        f"_xp >= definitions.srd_advancement_xp[{level}]",
                        [set_value("_earned", level)],
                    )
                    for level in range(2, 21)
                ],
                set_value("{_character_state_id}.experience_points", "_xp"),
            ],
        ),
        set_value("_earned", "min(_earned, policy.maximum_level)"),
        require("_earned >= character.data.level", "An award cannot reduce a character's level."),
        set_value("_awards", []),
        conditional(
            "character_state.advancement_awards != null",
            [set_value("_awards", "character_state.advancement_awards")],
        ),
        require("not (award_id in _awards)", "This character has already received this award."),
        {"list_push": {"list": "_awards", "value": "award_id"}, "store_as": "_awards"},
        set_value("{_character_state_id}.advancement_awards", "_awards"),
        set_value("{_character_state_id}.advancement_target", "_earned"),
        set_value(
            "_advancement_award",
            {"award_id": "award_id", "target_level": "_earned", "evidence": "evidence", "xp": "xp"},
        ),
        {"log": {"message": "Advancement award {award_id}: eligible through level {_earned}."}},
    ]
    return {
        "description": "Record a Plan milestone or allocated XP award once for this character. "
        "Read the policy and verify its trigger, timing and recipients first. Repeat "
        "for every eligible party member when the policy awards the whole party. "
        "An award does not apply a level or resolve player choices.",
        "parameters": references()
        + [
            parameter(
                "award_id", "string", description="Stable milestone ID, or unique XP event ID."
            ),
            parameter(
                "xp",
                "integer",
                minimum=0,
                description="Allocated XP for this character; 0 for milestones.",
            ),
            parameter(
                "evidence",
                "string",
                description="Played event satisfying the policy, including allocation.",
            ),
        ],
        "pre_conditions": common_checks(),
        "effects": effects,
        "metadata": {"category": "advancement", "model_behavior": "optional", "result_card": {
            "version": 1, "footprint": "inline", "title": "Advancement earned",
            "summary": [
                {"label": "Character", "value": {"path": "context.character.data.name"}},
                {"label": "Current level", "value": {"path": "context.character.data.level"}},
                {"label": "Eligible through level", "value": {"path": "context._earned"}},
            ],
            "details": [
                {"label": "Award", "value": {"path": "parameters.award_id"}},
                {"label": "XP awarded", "value": {"path": "parameters.xp"}, "omit_zero": True},
                {"label": "Played event", "value": {"path": "parameters.evidence"}},
                {"label": "Next step", "value": "Resolve and confirm any earned level-up choices"},
            ],
        }},
    }


def level_tool(schema):
    """Apply one earned level and its explicitly resolved build choices."""
    # The model resolves class/feat/spell choices from retained SRD content; these records do
    # not advertise automatic execution of every feature. Shared numerical changes are computed.
    build_schema = object_schema(
        {
            "abilities": deepcopy(schema["properties"]["abilities"]),
            "skill_proficiencies": deepcopy(schema["properties"]["skill_proficiencies"]),
            "tool_proficiencies": deepcopy(schema["properties"]["tool_proficiencies"]),
            "weapon_proficiencies": deepcopy(schema["properties"]["weapon_proficiencies"]),
            "weapon_masteries": deepcopy(schema["properties"]["weapon_masteries"]),
            "saving_throw_proficiencies": deepcopy(
                schema["properties"]["saving_throw_proficiencies"]
            ),
            "armor_class": {"type": "integer", "minimum": 0},
            "speed": {"type": "integer", "minimum": 0, "description":
                      "Base walking Speed before class movement, armor, Exhaustion "
                      "and spell effects."},
            "feature_hp_gain": {"type": "integer", "minimum": 0, "maximum": 100},
            "mechanical_sources": text_schema(
                "Explain feature HP gains (0 if none), AC, speed, saves and resource maxima "
                "using retained features. Include retroactive grants such as Draconic Resilience."
            ),
            "spellcasting": schema["properties"]["spellcasting_grants"],
            "resources": {
                "type": "array",
                "items": object_schema(
                    {
                        "id": {
                            "type": "string",
                            "enum": [
                                "action_surge",
                                "arcane_recovery",
                                "bardic_inspiration",
                                "channel_divinity",
                                "focus_point",
                                "wholeness_of_body",
                                "indomitable",
                                "rage",
                                "sorcery_point",
                                "wild_shape",
                                *[f"spell_slot_{rank}" for rank in range(1, 10)],
                            ],
                        },
                        "maximum": {"type": "integer", "minimum": 0, "maximum": 100},
                        "source": text_schema("Class table or feature defining the new maximum."),
                    }
                ),
                "maxItems": 30,
            },
            "prepared_spells": {"type": "array", "items": text_schema(), "uniqueItems": True},
            "features": {
                "type": "array",
                "items": object_schema(
                    {
                        "id": text_schema(),
                        "source": text_schema("Retained SRD section."),
                        "choice": text_schema(
                            "Resolved grant or player choice; no pending placeholders."
                        ),
                    }
                ),
                "minItems": 1,
                "maxItems": 50,
            },
        }
    )
    for optional_field in (
        "wizard_spellbook", "subclasses", "fighting_styles", "cleric_order", "druid_order",
    ):
        build_schema["properties"][optional_field] = schema["properties"][optional_field]
    effects = [
        set_value("_prior_character", "character.data"),
        set_value("_build", "build[0]"),
        require(
            "len(_build.features) >= 1 and len(_build.features) <= 50",
            "Record all resolved level features and choices (1 to 50 entries).",
        ),
        require(
            "len(_build.spellcasting) <= 32 and len(choices_summary) <= 1000",
            "Use at most 32 spellcasting sources and a choice summary of at most 1000 characters.",
        ),
        set_value("_new_level", "_prior_character.level + 1"),
        require(
            "character_state.advancement_target >= _new_level",
            "Record an earned award before applying the next level.",
        ),
        require(
            "_new_level <= policy.maximum_level and _new_level <= 20",
            "The next level exceeds the declared campaign or SRD level range.",
        ),
        set_value("_classes", []),
        set_value("_class_level", 1),
        set_value("_found", False),
        set_value("_total", 0),
        set_value("_seen_classes", []),
        each(
            "_prior_character.classes",
            "_class",
            [
                require(
                    "_class.id in definitions.srd_advancement_hit_dice "
                    "and not (_class.id in _seen_classes)",
                    "Each existing SRD class must be recognized and appear once.",
                ),
                {
                    "list_push": {"list": "_seen_classes", "value": "_class.id"},
                    "store_as": "_seen_classes",
                },
                set_value("_total", "_total + _class.level"),
                conditional(
                    "_class.id == class_id",
                    [
                        require(
                            "_found == false", "The character contains duplicate class entries."
                        ),
                        set_value("_found", True),
                        set_value("_class_level", "_class.level + 1"),
                        {
                            "list_push": {
                                "list": "_classes",
                                "value": {"id": "_class.id", "level": "_class_level"},
                            },
                            "store_as": "_classes",
                        },
                    ],
                    [
                        {
                            "list_push": {"list": "_classes", "value": "_class"},
                            "store_as": "_classes",
                        }
                    ],
                ),
            ],
        ),
        require(
            "_total == _prior_character.level",
            "Character level must equal the sum of class levels.",
        ),
        conditional(
            "_found == false",
            [
                {
                    "list_push": {"list": "_classes", "value": {"id": "class_id", "level": 1}},
                    "store_as": "_classes",
                },
            ],
        ),
        conditional(
            "len(_classes) > 1",
            [
                each(
                    "_classes",
                    "_multiclass",
                    [
                        conditional(
                            "_multiclass.id == 'fighter'",
                            [
                                require(
                                    "_prior_character.abilities.strength >= 13 "
                                    "or _prior_character.abilities.dexterity >= 13",
                                    "Multiclass Fighter requires Strength or Dexterity 13.",
                                ),
                            ],
                            [
                                each(
                                    "definitions.srd_advancement_primary_abilities[_multiclass.id]",
                                    "_primary",
                                    [
                                        require(
                                            "_prior_character.abilities[_primary] >= 13",
                                            "Each class's primary abilities must be at least 13.",
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        set_value("_ability_budget", 0),
        conditional(
            "_class_level == 4 or _class_level == 8 or _class_level == 12 "
            "or _class_level == 16 or (class_id == 'fighter' and "
            "(_class_level == 6 or _class_level == 14)) "
            "or (class_id == 'rogue' and _class_level == 10)",
            [
                set_value("_ability_budget", 2),
            ],
        ),
        conditional("_class_level == 19", [set_value("_ability_budget", 1)]),
        conditional(
            "class_id == 'barbarian' and _class_level == 20",
            [
                set_value("_ability_budget", 8),
            ],
        ),
        set_value("_ability_total", 0),
        *[
            effect
            for ability in ABILITIES
            for effect in [
                require(
                    f"_build.abilities.{ability} >= _prior_character.abilities.{ability}",
                    "A level-up cannot remove existing ability-score gains.",
                ),
                require(
                    f"_build.abilities.{ability} <= max(20, _prior_character.abilities.{ability}) "
                    f"or (_class_level == 19 and _build.abilities.{ability} <= 30) "
                    f"or (class_id == 'barbarian' and _class_level == 20 "
                    f"and _build.abilities.{ability} <= 25)",
                    "An ability increase exceeds the selected level's score limit.",
                ),
                set_value(
                    "_ability_total",
                    f"_ability_total + _build.abilities.{ability} "
                    f"- _prior_character.abilities.{ability}",
                ),
            ]
        ],
        require(
            "_ability_total <= _ability_budget",
            "Ability increases exceed this class level's available advancement.",
        ),
        conditional(
            "class_id == 'barbarian' and _class_level == 20",
            [
                *[
                    require(
                        f"_build.abilities.{ability} == "
                        f"min(25, _prior_character.abilities.{ability} + 4)",
                        "Primal Champion raises Strength and Constitution by 4 (maximum 25).",
                    )
                    for ability in ("strength", "constitution")
                ],
                *[
                    require(
                        f"_build.abilities.{ability} == _prior_character.abilities.{ability}",
                        "Primal Champion does not increase this ability.",
                    )
                    for ability in ("dexterity", "intelligence", "wisdom", "charisma")
                ],
            ],
        ),
        *[
            each(
                f"_prior_character.{field}",
                "_proficiency",
                [
                    require(
                        f"_proficiency in _build.{field}",
                        "Preserve existing character proficiencies.",
                    ),
                ],
            )
            for field in (
                "skill_proficiencies",
                "tool_proficiencies",
                "weapon_proficiencies",
                "saving_throw_proficiencies",
            )
        ],
        set_value("_subclass_recorded", False),
        each(
            "_build.features",
            "_feature",
            [
                conditional("_feature.id == 'subclass'", [set_value("_subclass_recorded", True)]),
            ],
        ),
        require(
            "_class_level != 3 or _subclass_recorded == true",
            "At class level 3, include a feature with id subclass and the selected subclass.",
        ),
        set_value("_old_con", "(_prior_character.abilities.constitution - 10) // 2"),
        set_value("_new_con", "(_build.abilities.constitution - 10) // 2"),
        set_value("_hp_base", "definitions.srd_advancement_hit_dice[class_id] // 2 + 1"),
        conditional(
            "hp_method == 'roll'",
            [
                set_value("_hit_die", "definitions.srd_advancement_hit_dice[class_id]"),
                {"dice": {"expression": "1d{_hit_die}"}, "store_as": "_hp_roll"},
                set_value("_hp_base", "_hp_roll.total"),
            ],
        ),
        set_value(
            "_hp_gain",
            "max(1, _hp_base + _new_con) + (_new_con - _old_con) * _prior_character.level",
        ),
        set_value("_hp_gain", "_hp_gain + _build.feature_hp_gain"),
        require("_hp_gain >= 1", "Level-up choices cannot reduce maximum HP."),
        set_value("_next_spellbook", []),
        conditional("_prior_character.wizard_spellbook != null", [
            set_value("_next_spellbook", "_prior_character.wizard_spellbook"),
        ]),
        conditional("_build.wizard_spellbook != null", [
            set_value("_next_spellbook", "_build.wizard_spellbook"),
        ]),
        set_value("_next_subclasses", {}),
        conditional("_prior_character.subclasses != null", [
            set_value("_next_subclasses", "_prior_character.subclasses"),
        ]),
        conditional("_build.subclasses != null", [
            set_value("_next_subclasses", "_build.subclasses"),
        ]),
        set_value("_subclass_count", 0),
        each("_classes", "_subclass_class", [
            require(
                "_subclass_class.level < 3 or _subclass_class.id in _next_subclasses",
                "Record the selected subclass as structured data "
                "for every class of level 3 or higher.",
            ),
            conditional("_subclass_class.id in _next_subclasses", [
                require("_subclass_class.level >= 3", "A subclass requires class level 3."),
                set_value("_subclass_count", "_subclass_count + 1"),
            ]),
            conditional(
                "_prior_character.subclasses != null "
                "and _subclass_class.id in _prior_character.subclasses",
                [require(
                    "_subclass_class.id in _next_subclasses and "
                    "_next_subclasses[_subclass_class.id] == "
                    "_prior_character.subclasses[_subclass_class.id]",
                    "A level-up must retain each existing subclass selection.",
                )],
            ),
        ]),
        require("len(_next_subclasses) == _subclass_count",
                "A subclass must belong to one of the character's actual classes."),
        set_value("_next_wizard_subclass", "''"),
        conditional("'wizard' in _next_subclasses", [
            set_value("_next_wizard_subclass", "_next_subclasses.wizard"),
        ]),
        {"call": "validate_srd_level_spell_changes", "params": {
            "caster": "{_character_id}", "grants": "{_build.spellcasting}",
            "prepared": "{_build.prepared_spells}", "spellbook": "{_next_spellbook}",
            "gained_class": "{class_id}", "class_level": "{_class_level}",
            "wizard_subclass": "{_next_wizard_subclass}",
        }},
        set_value("_prospective_casting", [{
            "level": "_new_level", "classes": "_classes", "abilities": "_build.abilities",
            "species": "_prior_character.species", "origin_feats": "_prior_character.origin_feats",
            "subclasses": "_next_subclasses", "wizard_spellbook": "_next_spellbook",
            "spellcasting_grants": "_build.spellcasting",
        }]),
        {"call": "validate_srd_spell_grants", "params": {
            "caster": "{_character_id}", "proposal": "{_prospective_casting}",
            "spell": "", "selected_source": "", "complete": True,
        }},
        set_value("_equipment_updates", {
            "classes": "_classes", "abilities": "_build.abilities",
            "subclasses": "_next_subclasses", "speed": "_build.speed",
            "spellcasting_grants": "_build.spellcasting",
        }),
        set_value("_next_equipment_profile", "merge(_prior_character, _equipment_updates)"),
        *[
            conditional(f"_build.{field} != null", [
                set_value("_equipment_choice_update", {field: f"_build.{field}"}),
                set_value("_next_equipment_profile",
                          "merge(_next_equipment_profile, _equipment_choice_update)"),
            ])
            for field in ("fighting_styles", "cleric_order", "druid_order")
        ],
        *[
            require(
                f"_prior_character.{field} == null or "
                f"_next_equipment_profile.{field} == _prior_character.{field}",
                "A level-up must preserve the selected Divine or Primal Order.",
            )
            for field in ("cleric_order", "druid_order")
        ],
        *prospective_equipment_effects(),
        *style_level_up_effects(),
        require("_build.armor_class == _equipment_ac",
                "Proposed Armor Class must match the new abilities, "
                "class grants and worn equipment."),
        *focus_capacity("_classes", "_next_focus_maximum"),
        *focus_capacity("_prior_character.classes", "_prior_focus_maximum"),
        *wholeness_capacity("_next_equipment_profile", "_next_wholeness_maximum"),
        *wholeness_capacity("_prior_character", "_prior_wholeness_maximum"),
        set_value("_prior_wholeness_remaining", 0),
        conditional("character_state.resources.wholeness_of_body != null", [
            set_value("_prior_wholeness_remaining", "character_state.resources.wholeness_of_body"),
        ]),
        require("_prior_wholeness_remaining >= 0 and "
                "_prior_wholeness_remaining <= _prior_wholeness_maximum and "
                "(_prior_wholeness_maximum == 0 or "
                "character_state.resource_maxima.wholeness_of_body == _prior_wholeness_maximum)",
                "Saved Wholeness of Body uses must match the previous feature and Wisdom."),
        each("_build.resources", "_wholeness_proposal", [
            require("_wholeness_proposal.id != 'wholeness_of_body' or "
                    "_wholeness_proposal.maximum == _next_wholeness_maximum",
                    "Wholeness of Body capacity is determined by "
                    "the Open Hand feature and Wisdom."),
        ]),
        set_value("_prior_focus_remaining", 0),
        conditional("character_state.resources.focus_point != null", [
            set_value("_prior_focus_remaining", "character_state.resources.focus_point"),
        ]),
        require("_prior_focus_remaining >= 0 and "
                "_prior_focus_remaining <= _prior_focus_maximum and "
                "(_prior_focus_maximum == 0 or "
                "character_state.resource_maxima.focus_point == _prior_focus_maximum)",
                "Saved Focus must match the previous Monk class capacity."),
        each("_build.resources", "_focus_proposal", [
            require("_focus_proposal.id != 'focus_point' or "
                    "_focus_proposal.maximum == _next_focus_maximum",
                    "Focus capacity is determined by Monk class level."),
        ]),
        *hit_die_pools(),
        {
            "request_choice": {
                "choice_id": "srd-level-up",
                "prompt": "Advance {_prior_character.name} to level {_new_level} "
                "({class_id} {_class_level}) using {hp_method} Hit Points? {choices_summary}",
                "options": [
                    {"id": "apply", "label": "Apply level-up"},
                    {"id": "cancel", "label": "Keep current level"},
                ],
            },
            "store_as": "_decision",
        },
        conditional(
            "_decision == 'apply'",
            [
                set_value("_applied_hp_gain", "_hp_gain"),
                conditional("hp_method == 'roll'", [
                    set_value("_applied_hp_roll", "_hp_roll"),
                    set_value("_applied_hp_modifier", "_hp_gain - _hp_roll.total"),
                ]),
                participant("level", "_new_level"),
                participant("classes", "_classes"),
                *[
                    participant(field, f"_build.{field}")
                    for field in (
                        "abilities",
                        "skill_proficiencies",
                        "tool_proficiencies",
                        "weapon_proficiencies",
                        "weapon_masteries",
                        "saving_throw_proficiencies",
                    )
                ],
                participant("spellcasting_grants", "_build.spellcasting"),
                *[
                    conditional(
                        f"_build.{field} != null", [participant(field, f"_build.{field}")],
                    )
                    for field in (
                        "wizard_spellbook", "subclasses", "fighting_styles", "cleric_order",
                        "druid_order",
                    )
                ],
                set_value("_advancements", []),
                conditional(
                    "_prior_character.advancements != null",
                    [set_value("_advancements", "_prior_character.advancements")],
                ),
                {
                    "list_push": {
                        "list": "_advancements",
                        "value": {
                            "level": "_new_level",
                            "class_id": "class_id",
                            "features": "_build.features",
                            "summary": "choices_summary",
                        },
                    },
                    "store_as": "_advancements",
                },
                participant("advancements", "_advancements"),
                participant("speed", "_build.speed"),
                set_value("{_character_state_id}.speed", "_equipment_speed"),
                set_value("{_character_state_id}.climb_speed", "_equipment_climb_speed"),
                set_value("{_character_state_id}.swim_speed", "_equipment_swim_speed"),
                set_value("{_character_state_id}.armor_class", "_equipment_ac"),
                set_value("{_character_state_id}.armor_speed_penalty", "_equipment_speed_penalty"),
                set_value("{_character_state_id}.armor_untrained", "_equipment_untrained_armor"),
                set_value("{_character_state_id}.armor_stealth_disadvantage",
                          "_equipment_stealth_disadvantage"),
                set_value("{_character_state_id}.max_hp", "character_state.max_hp + _hp_gain"),
                # Do not heal, refill slots or undo spent resources as a side effect of levelling.
                set_value("{_character_state_id}.constitution_modifier", "_new_con"),
                set_value("{_character_state_id}.prepared_spells", "_build.prepared_spells"),
                *[
                    conditional(
                        f"character_state.saving_throw_modifiers.{ability} != null",
                        [
                            set_value(
                                "_save",
                                f"character_state.saving_throw_modifiers.{ability} + "
                                f"(_build.abilities.{ability} - 10) // 2 - "
                                f"(_prior_character.abilities.{ability} - 10) // 2",
                            ),
                            conditional(
                                f"'{ability}' in _prior_character.saving_throw_proficiencies",
                                [
                                    set_value(
                                        "_save",
                                        "_save - 2 - (_prior_character.level - 1) // 4",
                                    ),
                                ],
                            ),
                            conditional(
                                f"'{ability}' in _build.saving_throw_proficiencies",
                                [
                                    set_value("_save", "_save + 2 + (_new_level - 1) // 4"),
                                ],
                            ),
                            set_value(
                                f"{{_character_state_id}}.saving_throw_modifiers.{ability}", "_save"
                            ),
                        ],
                    )
                    for ability in ABILITIES
                ],
                conditional(
                    "character_state.initiative_modifier != null",
                    [
                        set_value(
                            "{_character_state_id}.initiative_modifier",
                            "character_state.initiative_modifier "
                            "+ (_build.abilities.dexterity - 10) // 2 "
                            "- (_prior_character.abilities.dexterity - 10) // 2",
                        ),
                    ],
                ),
                conditional(
                    "'alert' in _prior_character.origin_feats "
                    "and character_state.initiative_modifier != null",
                    [
                        set_value(
                            "{_character_state_id}.initiative_modifier",
                            "character_state.initiative_modifier + (_new_level - 1) // 4 "
                            "- (_prior_character.level - 1) // 4",
                        ),
                    ],
                ),
                each(
                    "_build.resources",
                    "_resource",
                    [
                        set_value("_old_maximum", 0),
                        set_value("_remaining", 0),
                        conditional(
                            "_resource.id in character_state.resources",
                            [
                                require(
                                    "_resource.id in character_state.resource_maxima",
                                    "A resource needs its previous maximum to preserve spent uses.",
                                ),
                                set_value("_remaining", "character_state.resources[_resource.id]"),
                                set_value(
                                    "_old_maximum", "character_state.resource_maxima[_resource.id]"
                                ),
                            ],
                        ),
                        require(
                            "_resource.maximum >= _old_maximum",
                            "A level-up cannot reduce an existing resource maximum.",
                        ),
                        set_value(
                            "{_character_state_id}.resource_maxima.{_resource.id}",
                            "_resource.maximum",
                        ),
                        set_value(
                            "{_character_state_id}.resources.{_resource.id}",
                            "_remaining + _resource.maximum - _old_maximum",
                        ),
                    ],
                ),
                conditional("_next_focus_maximum > 0", [
                    set_value("{_character_state_id}.resource_maxima.focus_point",
                              "_next_focus_maximum"),
                    set_value("{_character_state_id}.resources.focus_point",
                              "_prior_focus_remaining + _next_focus_maximum "
                              "- _prior_focus_maximum"),
                ]),
                conditional("_next_wholeness_maximum > 0", [
                    set_value("{_character_state_id}.resource_maxima.wholeness_of_body",
                              "_next_wholeness_maximum"),
                    set_value("{_character_state_id}.resources.wholeness_of_body",
                              "max(0, _prior_wholeness_remaining + _next_wholeness_maximum "
                              "- _prior_wholeness_maximum)"),
                ]),
                set_value("{_character_state_id}.resource_maxima.hit_die", "_new_level"),
                set_value("{_character_state_id}.hit_dice_pools", "_pools"),
                set_value("_hit_dice", 0),
                conditional(
                    "character_state.resources.hit_die != null",
                    [set_value("_hit_dice", "character_state.resources.hit_die")],
                ),
                set_value("{_character_state_id}.resources.hit_die", "_hit_dice + 1"),
                each(
                    "_classes",
                    "_class",
                    [
                        conditional(
                            "_class.id == 'fighter'",
                            [
                                set_value("_prior_maximum", 0),
                                conditional(
                                    "character_state.resource_maxima.second_wind != null",
                                    [
                                        set_value(
                                            "_prior_maximum",
                                            "character_state.resource_maxima.second_wind",
                                        ),
                                    ],
                                    [
                                        conditional(
                                            "character_state.fighter_level >= 1",
                                            [
                                                set_value(
                                                    "_prior_maximum",
                                                    "2 + min(1, "
                                                    "character_state.fighter_level // 4) "
                                                    "+ min(1, character_state.fighter_level // 10)",
                                                ),
                                            ],
                                        ),
                                    ],
                                ),
                                set_value("_remaining_wind", 0),
                                conditional(
                                    "character_state.resources.second_wind != null",
                                    [
                                        set_value(
                                            "_remaining_wind",
                                            "character_state.resources.second_wind",
                                        ),
                                    ],
                                ),
                                set_value("{_character_state_id}.fighter_level", "_class.level"),
                                set_value(
                                    "_maximum",
                                    "2 + min(1, _class.level // 4) + min(1, _class.level // 10)",
                                ),
                                set_value(
                                    "{_character_state_id}.resource_maxima.second_wind", "_maximum"
                                ),
                                set_value(
                                    "{_character_state_id}.resources.second_wind",
                                    "min(_maximum, _remaining_wind "
                                    "+ max(0, _maximum - _prior_maximum))",
                                ),
                            ],
                        ),
                    ],
                ),
                conditional(
                    "len(_build.spellcasting) > 0",
                    [{"call": "validate_srd_spell_grants", "params": {
                        "caster": "{_character_id}", "spell": "", "selected_source": "",
                    }}],
                ),
                {
                    "log": {
                        "message": "Level-up applied: {_prior_character.name}, level {_new_level}, "
                        "{class_id} {_class_level}; maximum HP +{_hp_gain}. {choices_summary}"
                    }
                },
            ],
            [{"log": {"message": "Level-up cancelled; earned advancement remains available."}}],
        ),
    ]
    return {
        "description": "Apply one earned SRD level. Read the retained class level, subclass, "
        "feat and spell rules; resolve every player choice before proposing the build. "
        "Provide complete proficiency lists preserving existing grants and all new features. "
        "The player confirms the proposal. Feature records are not automatic feature execution.",
        "parameters": references()
        + [
            parameter("class_id", "enum", enum_values=list(HIT_DICE)),
            parameter("hp_method", "enum", enum_values=["fixed", "roll"]),
            parameter(
                "choices_summary",
                "string",
                description="Explain all gains and choices to the player; at most 1000 characters.",
            ),
            parameter("build", "array", min_items=1, max_items=1, items=build_schema),
        ],
        "pre_conditions": common_checks(),
        "effects": effects,
        "metadata": {"category": "advancement", "model_behavior": "optional", "result_card": {
            "version": 1, "footprint": "compact",
            "title": {"path": "context._decision", "labels": {
                "apply": "Level gained", "cancel": "Level-up cancelled"}},
            "equation": {
                "die_sides": {"path": "context._applied_hp_roll.sides"},
                "roll": {"path": "context._applied_hp_roll.total"},
                "modifier": {"path": "context._applied_hp_modifier"},
                "total": {"path": "context._applied_hp_gain"}, "optional": True,
            },
            "summary": [
                {"label": "Character", "value": {"path": "context.character.data.name"}},
                {"label": "Level", "value": {"path": "context.character.data.level"},
                 "emphasis": True},
                {"label": "Maximum HP", "value": {"path": "context.character_state.max_hp"}},
                {"label": "Maximum HP gained", "value": {"path": "context._applied_hp_gain"},
                 "optional": True},
            ],
            "details": [
                {"label": "Proposed choices", "value": {"path": "parameters.choices_summary"}},
                {"label": "HP method", "value": {"path": "parameters.hp_method", "labels": {
                    "fixed": "Fixed increase", "roll": "Rolled increase"}}},
            ],
        }},
    }


def replace_generated(source, key, content):
    """Replace only our marked YAML block, preserving the rest of the runtime verbatim."""
    start, end = f"  # FC-09 {key} begin\n", f"  # FC-09 {key} end\n"
    if start in source or end in source:
        if source.count(start) != 1 or source.count(end) != 1:
            raise ValueError(f"Malformed advancement-generated {key} section")
        begin, finish = source.index(start) + len(start), source.index(end)
        if finish < begin:
            raise ValueError(f"Malformed advancement-generated {key} section")
        existing = yaml.safe_load(source[begin:finish]) or {}
        unexpected = set(existing) - set(content)
        if unexpected:
            raise ValueError(
                f"Refusing to remove authored {key} from advancement-generated section: "
                + ", ".join(sorted(unexpected))
            )
    source = re.sub(re.escape(start) + r".*?" + re.escape(end), "", source, flags=re.S)
    block = yaml.safe_dump(content, sort_keys=False, allow_unicode=True, width=110)
    block = "".join(
        "  " + line if line.strip() else line for line in block.splitlines(keepends=True)
    )
    return source.replace(f"{key}:\n", f"{key}:\n{start}{block}{end}", 1)


def main():
    raise RuntimeError("Full generation is archived. Use scripts.build_lite_launch for the checked-in Lite package.")
    record_path = ROOT / "package-record.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    declaration = record["participant_artifacts"][0]
    schema = declaration["schema"]
    creation = json.loads(
        (ROOT / "content/character-creation-state.json").read_text(encoding="utf-8")
    )
    creation_equipment(schema, creation)
    tools = {
        "award_srd_advancement": award_tool(),
        "level_up_srd_character": level_tool(schema),
        "take_srd_short_rest_hit_die": short_rest_tool(),
        "restore_srd_resource": resource_tool(restore=True),
        "spend_srd_resource": resource_tool(restore=False),
        "change_srd_equipment": change_equipment_tool(),
    }
    build = tools["level_up_srd_character"]["parameters"][-1]["items"]
    schema["properties"]["advancements"] = {
        "type": "array",
        "maxItems": 19,
        "items": object_schema(
            {
                "level": {"type": "integer", "minimum": 2, "maximum": 20},
                "class_id": {"type": "string", "enum": list(HIT_DICE)},
                "features": build["properties"]["features"],
                "summary": text_schema(),
            }
        ),
    }
    creation["initial_state_validation"] = character_validation(yaml.safe_load(
        (ROOT / "runtime/ruleset.yaml").read_text(encoding="utf-8")
    ))
    creation_validation(creation["initial_state_validation"])
    creation_focus(creation)
    creation_wholeness(creation)
    (ROOT / "content/character-creation-state.json").write_bytes(canonical_json_bytes(creation))
    declaration.update(creation)
    declaration["schema_sha256"] = participant_schema_sha256(schema, **creation)

    def runtime_items(value):
        if isinstance(value, dict):
            return {
                key: runtime_items(item)
                for key, item in value.items()
                if key not in {"description", "maxItems", "minItems"}
            }
        if isinstance(value, list):
            return [runtime_items(item) for item in value]
        return value

    tools["level_up_srd_character"]["parameters"][-1]["items"] = runtime_items(build)
    record["state_schemas"] = [policy_declaration()]
    requirements = [
        "starting_state_validation.v1",
        "required_starting_state.v1",
        "participant_mutation.v1",
        "runtime_require.v1",
        "character_rule_results.v1",
        "runtime_immutable_collections.v1",
        "runtime_choice_sequences.v1",
    ]
    record["compatibility"]["requires"] = sorted(
        set(record["compatibility"]["requires"]) | set(requirements)
    )
    capability = "dnd.srd.5.2.1.advancement"
    equipment_capability = "dnd.srd.5.2.1.equipment"
    record["capabilities"] = sorted(
        set(record["capabilities"]) | {capability, equipment_capability}
    )
    record["tools"] = [item for item in record["tools"] if item["name"] not in tools]
    for name, tool in tools.items():
        record["tools"].append(
            {
                "name": name,
                "capability": (
                    equipment_capability if name == "change_srd_equipment" else
                    "dnd.srd.5.2.1.resources"
                    if name
                    in {"take_srd_short_rest_hit_die", "restore_srd_resource", "spend_srd_resource"}
                    else capability
                ),
                "writers": ["dm"],
                "turn_statuses": ["open"],
                "state_predicates": [],
                "input_schema": _tool_schema(tool, tool_name=name),
            }
        )
    record["tools"].sort(key=lambda item: item["name"])
    record_path.write_bytes(canonical_json_bytes(record))
    content_path = ROOT / "content/content-declarations.json"
    content = json.loads(content_path.read_text(encoding="utf-8"))
    links = next(
        item for item in content["inclusions"] if item["id"] == "srd-5.2.1-gameplay-runtime"
    )["links"]
    links["tools"] = sorted(set(links["tools"]) | set(tools))
    links["capabilities"] = sorted(
        set(links["capabilities"]) | {capability, equipment_capability}
    )
    links["artifacts"] = sorted(
        set(links["artifacts"]) | {"content/advancement-policy.schema.json"}
    )
    content_path.write_bytes(canonical_json_bytes(content))
    runtime_path = ROOT / "runtime/ruleset.yaml"
    runtime = runtime_path.read_text(encoding="utf-8")
    # Validate and replace the marked section before removing older unmarked tools.
    # Authored operations accidentally inserted inside the section must fail visibly.
    runtime = replace_generated(
        runtime, "tools", {**tools, "resolve_srd_equipment": equipment_tool(),
                           "validate_srd_fighting_styles": fighting_style_tool(),
                           "refresh_srd_movement_budget": movement_budget_tool(),
                           "resolve_srd_npc_equipment": npc_equipment_tool(),
                           "initialize_srd_npc_equipment": initialize_npc_equipment_tool()},
    )
    marked_start = runtime.index("  # FC-09 tools begin\n")
    marked_end = runtime.index("  # FC-09 tools end\n") + len("  # FC-09 tools end\n")
    marked = runtime[marked_start:marked_end]
    runtime = runtime[:marked_start] + runtime[marked_end:]
    runtime = re.sub(
        r"(?ms)^  (?:take_srd_short_rest_hit_die|restore_srd_resource|spend_srd_resource):\n.*?"
        r"(?=^  [a-z][a-z_0-9]*:|^procedures:|^definitions:)",
        "",
        runtime,
    )
    runtime = runtime.replace("tools:\n", "tools:\n" + marked, 1)
    runtime = replace_generated(
        runtime,
        "definitions",
        {
            "srd_advancement_xp": XP,
            "srd_advancement_hit_dice": HIT_DICE,
            "srd_advancement_primary_abilities": PRIMARY_ABILITIES,
            **equipment_definitions(),
        },
    )
    runtime_path.write_text(runtime, encoding="utf-8")
    identity_path = ROOT / "package-identity.json"
    identity = json.loads(identity_path.read_text(encoding="utf-8"))
    identity["primary"]["compatibility"]["requires"] = record["compatibility"]["requires"]
    identity_path.write_text(json.dumps(identity, indent=2) + "\n", encoding="utf-8")
    (ROOT / "content/advancement-policy.schema.json").write_text(
        json.dumps(policy_declaration(), indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    raise SystemExit("The Full generator is archived. Build checked-in Lite with python -m scripts.build_lite_launch OUTPUT.")
    main()
