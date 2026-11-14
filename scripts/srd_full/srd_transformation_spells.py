"Source-owned transformations with real statistics, actions, and reversible lifetimes."

import re
from copy import deepcopy

import yaml

if __package__:
    from . import srd_actor_spell_modifiers as actor
    from . import srd_form_descent as descent
    from . import srd_form_proficiencies as proficiencies
    from . import srd_form_recharge as recharge
    from .build_srd_beast_forms import build_index
    from .build_srd_non_beast_forms import build_index as build_non_beast_index
    from .srd_scene_relations import _tool_scene
    from .srd_timed_senses import _replace_tools
else:
    import srd_actor_spell_modifiers as actor
    import srd_form_descent as descent
    import srd_form_proficiencies as proficiencies
    import srd_form_recharge as recharge
    from build_srd_beast_forms import build_index
    from build_srd_non_beast_forms import build_index as build_non_beast_index
    from srd_scene_relations import _tool_scene
    from srd_timed_senses import _replace_tools

_set = actor._set
_when = actor._when
_call = actor._call
_require = actor._require
_tool = actor._tool
_entity = actor._entity
_parameter = actor._parameter

PROFILES = {
    "alter_self": (2, "self", 0, 600, True, 1, 0, True, False, "none", "alter"),
    "polymorph": (4, "distance", 60, 600, True, 1, 0, False, True, "wisdom", "beast"),
    "animal_shapes": (8, "distance", 30, 14400, False, 64, 0, True, True, "none", "beast"),
    "shapechange": (9, "self", 0, 600, True, 1, 0, True, False, "none", "beast"),
    "true_polymorph": (9, "distance", 30, 600, True, 1, 0, False, True, "wisdom", "beast"),
    "gaseous_form": (3, "touch", 0, 600, True, 1, 1, True, False, "none", "mist"),
    "wind_walk": (6, "distance", 30, 4800, False, 11, 0, True, False, "none", "wind"),
}
PROFILE_FIELDS = (
    "base_level",
    "range_kind",
    "range_feet",
    "duration_rounds",
    "concentration",
    "base_targets",
    "targets_per_slot_level",
    "requires_willing",
    "requires_sight",
    "save_ability",
    "profile",
)
PUBLIC_TOOLS = {
    "change_srd_transformation",
    "end_srd_animal_shape",
    "attack_srd_transformed_form",
    "attack_srd_altered_natural_weapon",
    "read_srd_transformed_appearance",
}
STAT_FIELDS = (
    "speed",
    "base_speed",
    "flying_speed",
    "swim_speed",
    "climb_speed",
    "size_rank",
    "armor_class",
    "armor_speed_penalty",
    "armor_untrained",
    "armor_stealth_disadvantage",
    "can_hover",
    "initiative_modifier",
    "saving_throw_modifiers",
    "skill_check_modifiers",
    "passive_perception",
    "darkvision_feet",
    "blindsight_feet",
    "tremorsense_feet",
    "truesight_feet",
    "languages",
    "multiattack",
    "creature_type",
    "condition_immunities",
    "damage_immunities",
    "damage_resistances",
    "damage_vulnerabilities",
    "concentration_save_modifier",
)
FORM_FLAGS = (
    "form_has_recharge",
    "form_statistics_replaced",
    "form_can_speak",
    "form_can_cast",
    "form_equipment_melded",
    "form_no_objects",
    "form_cloud",
    "form_hover",
    "form_prone_immune",
    "form_physical_save_advantage",
    "form_physical_resistance",
    "form_can_breathe_water",
    "form_retain_proficiencies",
    "form_wind_only_actions",
)


def _optional(name, kind="string", default=None, **extra):
    return {"name": name, "type": kind, "required": False, "default": default, **extra}


def _loop(collection, item, body):
    return {"for_each": {"collection": collection, "as": item, "do": body}}


def _push(collection, value):
    return {"list_push": {"list": collection, "value": value}}


def _lookup(table, key, destination):
    return {"table_lookup": {"table": table, "key": key, "required": True}, "store_as": destination}


def validate_profiles(definitions):
    """Bind every transformation profile to the fixed admitted spell clauses."""
    for spell, definition in definitions.items():
        if definition.get("family") != "transformation_spell":
            continue
        if spell not in PROFILES:
            raise SystemExit(f"Unknown transformation profile: {spell}")
        expected = dict(zip(PROFILE_FIELDS, PROFILES[spell], strict=True))
        expected.update(
            family="transformation_spell",
            casting_time="1_minute" if spell == "wind_walk" else "action",
        )
        if definition != expected:
            raise SystemExit(f"Transformation profile differs from source: {spell}")


def _schema():
    properties = {
        "casting_time": {"type": "string", "enum": ["action", "1_minute"]},
        "base_level": {"type": "integer", "minimum": 2, "maximum": 9},
        "range_kind": {"type": "string", "enum": ["self", "touch", "distance"]},
        "range_feet": {"type": "integer", "minimum": 0, "maximum": 60},
        "duration_rounds": {"type": "integer", "minimum": 600, "maximum": 14400},
        "concentration": {"type": "boolean"},
        "requires_willing": {"type": "boolean"},
        "requires_sight": {"type": "boolean"},
        "base_targets": {"type": "integer", "minimum": 1, "maximum": 64},
        "targets_per_slot_level": {"type": "integer", "minimum": 0, "maximum": 1},
        "save_ability": {"type": "string", "enum": ["none", "wisdom"]},
        "profile": {"type": "string", "enum": ["beast", "alter", "mist", "wind"]},
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _source_tools():
    check = _tool(
        ("Resolve the exact saved caster, expiry, death, and transformation ending clauses."),
        [
            _entity("target_state"),
            _entity("caster_state"),
            _entity("lasting_state"),
            _parameter("source_id"),
        ],
        [
            _set("_form_source", "lasting_state.transformation_sources[source_id]"),
            _set("_form_source_active", "_form_source.installed == true"),
            _set(
                "_form_concentration_matches",
                (
                    "caster_state.is_alive == true and "
                    "caster_state.concentration_spell_id == _form_source.spell_id and "
                    ""
                    ""
                    ""
                    ""
                    ""
                    ""
                    ""
                    ""
                    "caster_state.concentration_effect_family == "
                    "'transformation_spell' "
                    "and caster_state.concentration_started_tick == "
                    "_form_source.started_tick"
                ),
            ),
            _when(
                (
                    "_form_source.spell_id == 'true_polymorph' and "
                    "_form_source.permanent "
                    "!= true and _form_concentration_matches == true and "
                    "lasting_state.lasting_tick >= _form_source.expires_tick"
                ),
                [
                    _set("lasting_state.transformation_sources.{source_id}.permanent", True),
                    _set("lasting_state.transformation_sources.{source_id}.concentration", False),
                    _set("lasting_state.transformation_sources.{source_id}.expires_tick", None),
                    _set("_form_source", "lasting_state.transformation_sources[source_id]"),
                ],
            ),
            _when(
                "_form_source.permanent != true",
                [
                    _set(
                        "_form_source_active",
                        (
                            "_form_source_active == true and "
                            "lasting_state.lasting_tick + "
                            "0.000000001 < _form_source.expires_tick"
                        ),
                    )
                ],
            ),
            _when(
                "_form_source.concentration == true",
                [
                    _set(
                        "_form_source_active",
                        ("_form_source_active == true and _form_concentration_matches == true"),
                    )
                ],
            ),
            _when(
                "_form_source.spell_id == 'polymorph' and target_state.temp_hp == 0",
                [_set("_form_source_active", False)],
            ),
            _when(
                ("_form_source.spell_id == 'true_polymorph' and target_state.is_alive != true"),
                [_set("_form_source_active", False)],
            ),
            _when(
                "_form_source.spell_id == 'gaseous_form' and target_state.hp == 0",
                [_set("_form_source_active", False)],
            ),
            _when(
                (
                    "_form_source_active == true and _form_source.spell_id == "
                    "'wind_walk' "
                    "and (_form_source.mode == 'to_normal' or _form_source.mode == "
                    "'to_cloud') and lasting_state.lasting_tick >= "
                    "_form_source.transition_tick"
                ),
                [
                    _set("_settled_wind_mode", "'normal'"),
                    _when(
                        "_form_source.mode == 'to_cloud'", [_set("_settled_wind_mode", "'cloud'")]
                    ),
                    _set(
                        "lasting_state.transformation_sources.{source_id}.mode",
                        "_settled_wind_mode",
                    ),
                    _set("_form_source", "lasting_state.transformation_sources[source_id]"),
                ],
            ),
        ],
    )
    settle = _tool(
        ("Clear only the exact caster Concentration whose final transformed subject has ended."),
        [
            _entity("caster_state"),
            _entity("lasting_state"),
            _parameter("spell"),
            _parameter("started_tick", "number"),
        ],
        [
            _when(
                (
                    "caster_state.concentration_spell_id == spell and "
                    "caster_state.concentration_effect_family == "
                    "'transformation_spell' "
                    "and caster_state.concentration_started_tick == started_tick"
                ),
                [
                    _set("_remaining_caster_form", False),
                    _set("_remaining_form_pool", "lasting_state.transformation_source_ids"),
                    _loop(
                        "_remaining_form_pool",
                        "_remaining_form_source_id",
                        [
                            _set(
                                "_remaining_form_source",
                                ("lasting_state.transformation_sources[_remaining_form_source_id]"),
                            ),
                            _when(
                                (
                                    "_remaining_form_source.installed == true and "
                                    "_remaining_form_source.concentration == true and "
                                    ""
                                    ""
                                    ""
                                    ""
                                    ""
                                    ""
                                    ""
                                    ""
                                    "_remaining_form_source.caster_state_id == "
                                    "_caster_state_id and "
                                    "_remaining_form_source.spell_id "
                                    "== spell and _remaining_form_source.started_tick "
                                    ""
                                    ""
                                    ""
                                    ""
                                    ""
                                    ""
                                    ""
                                    "== "
                                    "started_tick"
                                ),
                                [_set("_remaining_caster_form", True)],
                            ),
                        ],
                    ),
                    _when(
                        "_remaining_caster_form != true",
                        [
                            *[
                                _set("caster_state." + field, None)
                                for field in (
                                    "concentration_spell_id",
                                    "concentration_effect_family",
                                    "concentration_target_state_id",
                                    "concentration_started_tick",
                                    "concentration_expires_tick",
                                    "concentration_save_modifier",
                                )
                            ],
                            _set("caster_state.concentration_target_state_ids", []),
                        ],
                    ),
                ],
            ),
        ],
    )
    end = _tool(
        ("Remove one exact transformation source and only its remaining Temporary Hit Points."),
        [
            _entity("target_state"),
            _parameter("source_id"),
            _optional("lasting_state", default="state", resolve="entity", model_hidden=True),
        ],
        [
            _require(
                (
                    "lasting_state.transformation_sources != null and source_id in "
                    "lasting_state.transformation_sources"
                ),
                "The transformation source does not exist.",
            ),
            _set("_ending_form", "lasting_state.transformation_sources[source_id]"),
            _require(
                "_ending_form.target_state_id == _target_state_id",
                "The transformation source belongs to another creature.",
            ),
            _when(
                "_ending_form.installed == true",
                [
                    _set("lasting_state.transformation_sources.{source_id}.installed", False),
                    _when(
                        "target_state.temp_hp_transformation_source_id == source_id",
                        [
                            _set("target_state.temp_hp", 0),
                            _set("target_state.temp_hp_transformation_source_id", None),
                        ],
                    ),
                    _set("_remaining_form_ids", []),
                    _set("_old_form_ids", "target_state.transformation_source_ids"),
                    _loop(
                        "_old_form_ids",
                        "_old_form_id",
                        [
                            _when(
                                "_old_form_id != source_id",
                                [_push("_remaining_form_ids", "_old_form_id")],
                            )
                        ],
                    ),
                    _set("target_state.transformation_source_ids", "_remaining_form_ids"),
                    _set("_free_form_ids", "lasting_state.transformation_free_source_ids"),
                    _push("_free_form_ids", "source_id"),
                    _set("lasting_state.transformation_free_source_ids", "_free_form_ids"),
                    _when(
                        "_ending_form.concentration == true",
                        [
                            _call(
                                "settle_srd_transformation_caster",
                                {
                                    "caster_state": "{_ending_form.caster_state_id}",
                                    "lasting_state": "{_lasting_state_id}",
                                    "spell": "{_ending_form.spell_id}",
                                    "started_tick": "{_ending_form.started_tick}",
                                },
                            )
                        ],
                    ),
                ],
            ),
        ],
    )
    refresh = _tool(
        (
            "Refresh only indexed transformation subjects after actual elapsed time "
            "or "
            "concentration loss."
        ),
        [_entity("lasting_state")],
        [
            _set("_form_targets", "lasting_state.transformation_target_ids"),
            _loop(
                "_form_targets",
                "_form_target_id",
                [
                    _call(
                        "project_srd_transformation",
                        {
                            "target_state": "{_form_target_id}",
                            "lasting_state": "{_lasting_state_id}",
                        },
                    )
                ],
            ),
        ],
    )
    public_end = _tool(
        "End one exact transformation and immediately reproject its actual subject.",
        deepcopy(end["parameters"]),
        [
            _call(
                "retire_srd_transformation_source",
                {
                    "target_state": "{_target_state_id}",
                    "source_id": "{source_id}",
                    "lasting_state": "{_lasting_state_id}",
                },
            ),
            _call(
                "project_srd_transformation",
                {"target_state": "{_target_state_id}", "lasting_state": "{_lasting_state_id}"},
            ),
        ],
    )
    return {
        "settle_srd_transformation_caster": settle,
        "check_srd_transformation_source": check,
        "retire_srd_transformation_source": end,
        "end_srd_transformation_source": public_end,
        "refresh_srd_transformations": refresh,
    }


def _projection_tool():
    reset = [
        _set("target_state.form_current_source_id", None),
        _set("target_state.form_ability_scores", None),
        _set("target_state.alter_self_option", None),
        _set("target_state.form_appearance", None),
        _set("target_state.form_water_until", 0),
        _set("target_state.form_natural_armor_class", None),
        _set("target_state.form_natural_walk_speed", None),
        _set("target_state.form_natural_climb_speed", 0),
        _set("target_state.form_natural_swim_speed", 0),
        _set("target_state.form_respiration_native_air", None),
        _set("target_state.form_respiration_native_water", None),
        _set("target_state.form_respiration_capacity_ticks", None),
        _set("target_state.form_body_condition_immunities", []),
    ]
    reset.extend(_set("target_state." + field, False) for field in FORM_FLAGS)
    reset.extend(
        [_set("target_state.form_can_cast", True), _set("target_state.form_can_speak", True)]
    )
    restore = []
    for field in STAT_FIELDS:
        restore.extend(
            [
                _set("target_state." + field, None),
                _when(
                    "target_state.form_base_statistics." + field + " != null",
                    [
                        _set("target_state." + field, "target_state.form_base_statistics." + field),
                    ],
                ),
            ]
        )
    beast = [
        _lookup("definitions.beast_forms", "_winning_form.form_id", "_projected_beast"),
        _set("target_state.form_has_recharge", "_projected_beast.recharge_attack_names != null"),
        _set("target_state.form_statistics_replaced", True),
        _set("target_state.form_can_cast", "_winning_form.spell_id == 'shapechange'"),
        _set(
            "target_state.form_can_speak",
            (
                "_winning_form.spell_id == 'shapechange' or _winning_form.spell_id == "
                ""
                ""
                ""
                ""
                ""
                ""
                ""
                ""
                "'animal_shapes'"
            ),
        ),
        _set("target_state.form_equipment_melded", "_winning_form.spell_id != 'shapechange'"),
        _set("target_state.form_no_objects", "_winning_form.spell_id != 'shapechange'"),
        _set("target_state.form_retain_proficiencies", "_winning_form.spell_id == 'shapechange'"),
        _set("target_state.form_appearance", "_projected_beast.name"),
        _set("target_state.armor_class", "_projected_beast.armor_class"),
        _set("target_state.form_natural_armor_class", "_projected_beast.armor_class"),
        _set("target_state.form_natural_walk_speed", "_projected_beast.walk_speed"),
        _set("target_state.initiative_modifier", "_projected_beast.initiative_modifier"),
        _set("target_state.size_rank", "_projected_beast.size_rank"),
        _set("target_state.speed", "_projected_beast.walk_speed"),
        _set("target_state.base_speed", "_projected_beast.walk_speed"),
        _set("target_state.can_hover", "'(hover)' in _projected_beast.speed_text"),
        _set("target_state.form_ability_scores", "_projected_beast.ability_scores"),
        _set("target_state.saving_throw_modifiers", "_projected_beast.saving_throw_modifiers"),
        _set("target_state.multiattack", "_projected_beast.multiattack"),
        _set("target_state.languages", "_projected_beast.languages"),
        _set("target_state.passive_perception", "_projected_beast.passive_perception"),
        _set("target_state.form_respiration_native_air", "_projected_beast.breathes_air"),
        _set("target_state.form_respiration_native_water", "_projected_beast.breathes_water"),
        _set(
            "target_state.form_body_condition_immunities", "_projected_beast.condition_immunities"
        ),
        _set(
            "target_state.form_respiration_capacity_ticks",
            "max(_projected_beast.hold_breath_ticks, max(5, (1 + "
            "(_projected_beast.ability_scores.constitution - 10) // 2) * 10) * 2)",
        ),
        *(
            _set("target_state." + field, "_projected_beast." + field)
            for field in (
                "condition_immunities",
                "damage_immunities",
                "damage_resistances",
                "damage_vulnerabilities",
            )
        ),
        _when(
            "_winning_form.spell_id == 'true_polymorph'",
            [
                _set("target_state.creature_type", "'beast'"),
                _when(
                    "_projected_beast.creature_type != null",
                    [_set("target_state.creature_type", "_projected_beast.creature_type")],
                ),
            ],
        ),
    ]
    for kind, destination in (
        ("fly", "flying_speed"),
        ("swim", "swim_speed"),
        ("climb", "climb_speed"),
    ):
        beast.extend(
            [
                _set("target_state." + destination, 0),
                _when(
                    f"'{kind}' in _projected_beast.other_speeds",
                    [_set("target_state." + destination, f"_projected_beast.other_speeds.{kind}")],
                ),
            ]
        )
        if kind in ("climb", "swim"):
            beast.append(
                _set("target_state.form_natural_" + kind + "_speed", "target_state." + destination)
            )
    for sense in ("darkvision_feet", "blindsight_feet", "tremorsense_feet", "truesight_feet"):
        beast.append(_set("target_state." + sense, "_projected_beast.senses." + sense))
    mental = []
    for ability in ("intelligence", "wisdom", "charisma"):
        mental.extend(
            [
                _set("_retained_form_score", "target_state.form_base_abilities." + ability),
                _set(
                    "_retained_form_bonus",
                    (
                        f"_projected_beast.saving_throw_modifiers.{ability} - "
                        f"((_projected_beast.ability_scores.{ability} - 10) // 2)"
                    ),
                ),
                _set("_retained_ability_patch", {ability: "_retained_form_score"}),
                _set(
                    "target_state.form_ability_scores",
                    "merge(target_state.form_ability_scores, _retained_ability_patch)",
                ),
                _set(
                    "_retained_save_patch",
                    {ability: "((_retained_form_score - 10) // 2) + _retained_form_bonus"},
                ),
                _set(
                    "target_state.saving_throw_modifiers",
                    "merge(target_state.saving_throw_modifiers, _retained_save_patch)",
                ),
            ]
        )
    mental.append(_set("target_state.languages", "target_state.form_base_statistics.languages"))
    beast.append(
        _when(
            (
                "_winning_form.spell_id == 'animal_shapes' or _winning_form.spell_id "
                "== "
                "'shapechange'"
            ),
            mental,
        )
    )
    retained_saves = []
    beast.extend(proficiencies.projected_training())
    for ability in ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"):
        retained_saves.append(
            _when(
                f"'{ability}' in _form_retained_training.saves",
                [
                    _set(
                        "target_state.saving_throw_modifiers." + ability,
                        f"max(target_state.saving_throw_modifiers.{ability}, "
                        f"(target_state.form_ability_scores.{ability} - 10) // 2 + "
                        "_form_retained_training.proficiency_bonus)",
                    )
                ],
            )
        )
    beast.extend(
        [
            _when(
                "_winning_form.spell_id == 'shapechange' and "
                "_form_retained_training != null",
                retained_saves,
            ),
            _when(
                "_winning_form.spell_id == 'shapechange' or "
                "_winning_form.spell_id == 'animal_shapes'",
                [
                    _set("_form_perception_proficiency", 0),
                    _when(
                        "'perception' in _projected_beast.skills",
                        [
                            _set(
                                "_form_perception_proficiency",
                                "_projected_beast.skills.perception - "
                                "((_projected_beast.ability_scores.wisdom - 10) // 2)",
                            )
                        ],
                    ),
                    _when(
                        "_winning_form.spell_id == 'shapechange' and "
                        "_form_retained_training != null and "
                        "'perception' in _form_retained_training.skills",
                        [
                            _set(
                                "_form_perception_proficiency",
                                "max(_form_perception_proficiency, "
                                "_form_retained_training.proficiency_bonus)",
                            )
                        ],
                    ),
                    _set(
                        "target_state.passive_perception",
                        "10 + ((target_state.form_ability_scores.wisdom - 10) // 2) + "
                        "_form_perception_proficiency",
                    ),
                ],
            ),
        ]
    )
    beast.append(
        _when(
            "target_state.concentration_spell_id != null",
            [
                _set(
                    "target_state.concentration_save_modifier",
                    "target_state.saving_throw_modifiers.constitution",
                )
            ],
        )
    )
    beast.extend(proficiencies.projected_skills())
    cloud = [
        _set("target_state.form_cloud", True),
        _set("target_state.form_wind_only_actions", "_winning_form.spell_id == 'wind_walk'"),
        _set("target_state.form_can_cast", False),
        _set("target_state.form_can_speak", False),
        _set("target_state.form_no_objects", True),
        _set("target_state.form_equipment_melded", True),
        _set("target_state.form_hover", True),
        _set("target_state.can_hover", True),
        _set("target_state.form_prone_immune", True),
        _set("target_state.form_physical_resistance", True),
        _set(
            "target_state.form_physical_save_advantage", "_winning_form.spell_id == 'gaseous_form'"
        ),
        _set("target_state.speed", 0),
        _set("target_state.base_speed", 0),
        _set("target_state.climb_speed", 0),
        _set("target_state.swim_speed", 0),
        _set("target_state.flying_speed", 10),
        _when("_winning_form.spell_id == 'wind_walk'", [_set("target_state.flying_speed", 300)]),
        _set("target_state.form_appearance", "'misty cloud'"),
    ]
    effects = [
        _when(
            "target_state.has_transformations == true",
            [
                _set("target_state.form_projection_in_progress", True),
                _set("_form_was_melded", "target_state.form_equipment_melded == true"),
                _set("_winning_form", None),
                _set("_winning_form_id", None),
                _set("_project_form_ids", "target_state.transformation_source_ids"),
                _loop(
                    "_project_form_ids",
                    "_project_form_id",
                    [
                        _set(
                            "_project_form",
                            "lasting_state.transformation_sources[_project_form_id]",
                        ),
                        _when(
                            "_project_form.installed == true",
                            [
                                _call(
                                    "check_srd_transformation_source",
                                    {
                                        "target_state": "{_target_state_id}",
                                        "caster_state": "{_project_form.caster_state_id}",
                                        "lasting_state": "{_lasting_state_id}",
                                        "source_id": "{_project_form_id}",
                                    },
                                    ["_form_source_active", "_form_source"],
                                ),
                                _when(
                                    "_form_source_active == true",
                                    [
                                        _when(
                                            (
                                                "_winning_form == null or "
                                                "_form_source.serial > "
                                                "_winning_form.serial"
                                            ),
                                            [
                                                _set("_winning_form", "_form_source"),
                                                _set("_winning_form_id", "_project_form_id"),
                                            ],
                                        ),
                                    ],
                                    [
                                        _call(
                                            "retire_srd_transformation_source",
                                            {
                                                "target_state": "{_target_state_id}",
                                                "source_id": "{_project_form_id}",
                                                "lasting_state": "{_lasting_state_id}",
                                            },
                                        )
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
                *reset,
                _when("target_state.form_base_statistics != null", restore),
                _when(
                    "_winning_form != null",
                    [
                        _set("target_state.form_current_source_id", "_winning_form_id"),
                        _when("_winning_form.profile == 'beast'", beast),
                        _when(
                            (
                                "_winning_form.profile == 'mist' or "
                                "(_winning_form.profile == 'wind' and "
                                "(_winning_form.mode "
                                "== 'cloud' or _winning_form.mode == 'to_normal'))"
                            ),
                            cloud,
                        ),
                        _when(
                            "_winning_form.profile == 'alter'",
                            [
                                _set("target_state.alter_self_option", "_winning_form.mode"),
                                _set("target_state.form_appearance", "_winning_form.appearance"),
                                _when(
                                    "_winning_form.mode == 'aquatic'",
                                    [
                                        _set("target_state.swim_speed", "target_state.speed"),
                                        _set("target_state.form_can_breathe_water", True),
                                        _set("target_state.form_respiration_native_water", True),
                                        _set(
                                            "target_state.form_water_until",
                                            "_winning_form.expires_tick",
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
                _when(
                    ("target_state.form_equipment_melded == true and _form_was_melded != true"),
                    [
                        _set("target_state.form_melded_worn_armor", "target_state.worn_armor"),
                        _set("target_state.form_melded_worn_shield", "target_state.worn_shield"),
                        _set("target_state.worn_armor", None),
                        _set("target_state.worn_shield", None),
                    ],
                ),
                _when(
                    ("target_state.form_equipment_melded != true and _form_was_melded == true"),
                    [
                        _when(
                            (
                                "target_state.form_melded_worn_armor != null and "
                                "target_state.inventory != null and "
                                "target_state.form_melded_worn_armor.inventory_item "
                                "in "
                                "target_state.inventory"
                            ),
                            [
                                _set(
                                    "target_state.worn_armor", "target_state.form_melded_worn_armor"
                                )
                            ],
                        ),
                        _when(
                            (
                                "target_state.form_melded_worn_shield != null and "
                                "target_state.inventory != null and "
                                "target_state.form_melded_worn_shield.inventory_item "
                                "in "
                                "target_state.inventory"
                            ),
                            [
                                _set(
                                    "target_state.worn_shield",
                                    "target_state.form_melded_worn_shield",
                                )
                            ],
                        ),
                        _set("target_state.form_melded_worn_armor", None),
                        _set("target_state.form_melded_worn_shield", None),
                    ],
                ),
                _when(
                    (
                        "target_state.form_statistics_replaced == true or "
                        "target_state.form_cloud == true"
                    ),
                    [
                        _set("target_state.armor_speed_penalty", 0),
                        _set("target_state.armor_untrained", False),
                        _set("target_state.armor_stealth_disadvantage", False),
                    ],
                ),
                _when(
                    (
                        "target_state.form_equipment_melded != true and "
                        "(target_state.form_statistics_replaced == true or "
                        "_winning_form "
                        "== null or _form_was_melded == true)"
                    ),
                    [
                        _set("_form_equipment_calculated", False),
                        _when(
                            "target_state.form_equipment_character_id != null",
                            [
                                _call(
                                    "resolve_srd_equipment",
                                    {
                                        "actor": "{target_state.form_equipment_character_id}",
                                        "actor_state": "{_target_state_id}",
                                    },
                                    [
                                        "_equipment_ac",
                                        "_equipment_speed",
                                        "_equipment_climb_speed",
                                        "_equipment_swim_speed",
                                        "_equipment_speed_penalty",
                                        "_equipment_untrained_armor",
                                        "_equipment_stealth_disadvantage",
                                    ],
                                ),
                                _set("_form_equipment_calculated", True),
                            ],
                            [
                                _when(
                                    "target_state.equipment_profile != null",
                                    [
                                        _call(
                                            "resolve_srd_npc_equipment",
                                            {"actor_state": "{_target_state_id}"},
                                            [
                                                "_equipment_ac",
                                                "_equipment_speed",
                                                "_equipment_climb_speed",
                                                "_equipment_swim_speed",
                                                "_equipment_speed_penalty",
                                                "_equipment_untrained_armor",
                                                "_equipment_stealth_disadvantage",
                                            ],
                                        ),
                                        _set("_form_equipment_calculated", True),
                                    ],
                                ),
                            ],
                        ),
                        _when(
                            "_form_equipment_calculated == true",
                            [
                                _set("target_state.armor_class", "_equipment_ac"),
                                _set("target_state.speed", "_equipment_speed"),
                                _set("target_state.climb_speed", "_equipment_climb_speed"),
                                _set("target_state.swim_speed", "_equipment_swim_speed"),
                                _set(
                                    "target_state.armor_speed_penalty", "_equipment_speed_penalty"
                                ),
                                _set("target_state.armor_untrained", "_equipment_untrained_armor"),
                                _set(
                                    "target_state.armor_stealth_disadvantage",
                                    "_equipment_stealth_disadvantage",
                                ),
                            ],
                        ),
                    ],
                ),
                _when(
                    ("target_state.form_cloud != true and target_state.spell_speed_bonus != null"),
                    [
                        _set(
                            "target_state.speed",
                            "target_state.speed + target_state.spell_speed_bonus",
                        )
                    ],
                ),
                _when(
                    "target_state.alter_self_option == 'aquatic'",
                    [_set("target_state.swim_speed", "target_state.speed")],
                ),
                _set(
                    "_wind_transforming",
                    (
                        "_winning_form != null and _winning_form.profile == 'wind' "
                        "and "
                        "(_winning_form.mode == 'to_cloud' or _winning_form.mode == "
                        "'to_normal')"
                    ),
                ),
                _when(
                    "target_state.timed_condition_unowned_stunned == null",
                    [
                        _set(
                            "target_state.timed_condition_unowned_stunned",
                            (
                                "'stunned' in target_state.conditions and "
                                "target_state.timed_condition_stunned != true and "
                                "target_state.turn_effect_stunned != true and "
                                "target_state.transformation_stunned != true"
                            ),
                        )
                    ],
                ),
                _when(
                    "_wind_transforming == true",
                    [{"apply_condition": {"target": "target_state", "condition": "stunned"}}],
                ),
                _when(
                    (
                        "_wind_transforming != true and "
                        "target_state.transformation_stunned == true and "
                        "target_state.timed_condition_unowned_stunned != true and "
                        "target_state.timed_condition_stunned != true and "
                        "target_state.turn_effect_stunned != true"
                    ),
                    [{"remove_condition": {"target": "target_state", "condition": "stunned"}}],
                ),
                _set("target_state.transformation_stunned", "_wind_transforming"),
                _when(
                    "_winning_form == null",
                    [
                        _set("target_state.form_base_statistics", None),
                        _set("target_state.form_base_abilities", None),
                        _set("target_state.form_base_pc_proficiencies", None),
                        _set("target_state.form_base_npc_proficiencies", None),
                        _set("target_state.form_equipment_character_id", None),
                    ],
                ),
                _when(
                    ("combat.turn_active == true and combat.active_actor == _target_state_id"),
                    [
                        _set(
                            "combat.active_actor_form_wind_only_actions",
                            "target_state.form_wind_only_actions",
                        ),
                        _set(
                            "combat.active_actor_form_restricted",
                            (
                                "target_state.form_statistics_replaced and "
                                "target_state.form_can_cast != true"
                            ),
                        ),
                    ],
                ),
                _set("_projected_body_immunities", "target_state.form_body_condition_immunities"),
                _when(
                    "target_state.form_prone_immune == true",
                    [_push("_projected_body_immunities", "'prone'")],
                ),
                _set("target_state.form_body_condition_immunities", "_projected_body_immunities"),
                _when(
                    "len(target_state.form_body_condition_immunities) > 0 or "
                    "target_state.form_body_suppression_active == true",
                    [
                        _call(
                            "resolve_srd_form_condition_immunity",
                            {
                                "target_state": "{_target_state_id}",
                                "lasting_state": "{_lasting_state_id}",
                            },
                        )
                    ],
                ),
                _set("target_state.form_projection_in_progress", False),
                _when(
                    "target_state.respiration_medium != null",
                    [
                        _call(
                            "sync_srd_transformation_respiration",
                            {"target_state": "{_target_state_id}"},
                        )
                    ],
                ),
            ],
        ),
    ]
    return _tool(
        (
            "Project the newest effective form, restoring actual pre-form statistics "
            "and "
            "independent sources."
        ),
        [_entity("target_state"), _entity("lasting_state")],
        effects,
    )


def _validate_tool():
    return _tool(
        (
            "Admit source-linked Beast choices against actual target identity and "
            "source-specific limits."
        ),
        [
            _entity("target_state"),
            _parameter("spell"),
            _parameter("form_id"),
            _optional("target_character", resolve="participant"),
            _optional("seen_before", "boolean", False),
        ],
        [
            _require("target_state.is_alive == true", "Only a living creature can shape-shift."),
            _require(
                (
                    "target_character == null or (target_character.validation_status "
                    "== "
                    "'validated' and target_character.participant_id == "
                    "_target_state_id)"
                ),
                "The form's Character File must match its saved target identity.",
            ),
            _require(
                ("target_state.creature_kind != 'character' or target_character != null"),
                ("A character transformation needs its matching validated Character File."),
            ),
            proficiencies.qualification(),
            _lookup("definitions.beast_forms", "form_id", "_validated_form"),
            _when(
                "spell == 'polymorph' or spell == 'animal_shapes'",
                [
                    _require(
                        "form_id in definitions.beast_forms", "This spell requires a Beast form."
                    )
                ],
            ),
            _when(
                "spell == 'shapechange'",
                [
                    _require(
                        "_validated_form.creature_type != 'construct' and "
                        "_validated_form.creature_type != 'undead'",
                        "Shapechange cannot use a Construct or Undead body.",
                    )
                ],
            ),
            _require(
                "_validated_form.execution_supported == true",
                (
                    "This source stat block contains traits or actions without an "
                    "owning "
                    "transformation consumer."
                ),
            ),
            _when(
                "spell != 'animal_shapes'",
                [
                    _call(
                        "validate_srd_body_form_for_target",
                        {
                            "target_state": "{_target_state_id}",
                            "target_character": "{_target_character_id}",
                            "form_id": "{form_id}",
                        },
                    ),
                ],
            ),
            _when(
                "spell == 'animal_shapes'",
                [
                    _require(
                        (
                            "_validated_form.challenge_rating_eighths <= 32 and "
                            "_validated_form.size_rank <= 3"
                        ),
                        ("Animal Shapes requires a Large or smaller Beast with CR 4 or lower."),
                    )
                ],
            ),
            _when(
                "spell == 'shapechange'",
                [
                    _require(
                        "seen_before == true",
                        ("Shapechange requires a kind of creature the caster has seen before."),
                    )
                ],
            ),
            _set("_form_abilities", "target_state.ability_scores"),
            _when(
                "target_character != null",
                [_set("_form_abilities", "target_character.data.abilities")],
            ),
            _require(
                ("spell != 'animal_shapes' and spell != 'shapechange' or _form_abilities != null"),
                ("Retaining mental abilities requires the target's authoritative scores."),
            ),
        ],
    )


def _install_tool():
    params = [
        _entity("target_state"),
        _entity("caster_state"),
        _entity("lasting_state"),
        _parameter("spell"),
        _optional("form_id", default=""),
        _optional("mode", default=""),
        _optional("appearance", default=""),
        _optional("actual_spell_level", "integer", -1, minimum=-1, maximum=9),
        _optional("accept_temporary_hp", "boolean", True),
        _optional("target_character", resolve="participant"),
        _optional("casting_ability", default="wisdom"),
        _optional("casting_modifier", "integer", 0),
        _optional("equipment_policy", default="meld"),
    ]
    capture = [_set("_form_original_statistics", dict.fromkeys(STAT_FIELDS))]
    for field in STAT_FIELDS:
        capture.append(
            _when(
                "target_state." + field + " != null",
                [
                    _set("_form_original_statistic", {field: "target_state." + field}),
                    _set(
                        "_form_original_statistics",
                        "merge(_form_original_statistics, _form_original_statistic)",
                    ),
                ],
            )
        )
    capture.append(
        _when(
            "target_state.spell_speed_bonus != null",
            [
                _set(
                    "_form_original_statistic",
                    {"speed": "max(0, target_state.speed - target_state.spell_speed_bonus)"},
                ),
                _set(
                    "_form_original_statistics",
                    "merge(_form_original_statistics, _form_original_statistic)",
                ),
            ],
        )
    )
    effects = [
        _lookup("definitions.spells", "spell", "_installed_form_profile"),
        proficiencies.qualification(),
        _when("lasting_state.lasting_tick == null", [_set("lasting_state.lasting_tick", 0)]),
        _when(
            "lasting_state.transformation_sources == null",
            [
                _set("lasting_state.transformation_sources", {}),
                _set(
                    "lasting_state.transformation_free_source_ids",
                    [f"transformation_{i}" for i in range(128)],
                ),
                _set("lasting_state.transformation_target_ids", []),
                _set("lasting_state.transformation_serial", 0),
                _set("lasting_state.transformation_source_ids", []),
            ],
        ),
        _when(
            "target_state.transformation_source_ids == null",
            [_set("target_state.transformation_source_ids", [])],
        ),
        _when(
            "target_state.form_base_statistics == null",
            [
                *capture,
                _set("target_state.form_base_statistics", "_form_original_statistics"),
                _set("target_state.form_base_abilities", "target_state.ability_scores"),
                _set("target_state.form_base_pc_proficiencies", None),
                *proficiencies.capture(),
                _when(
                    "target_character != null",
                    [
                        _set("target_state.form_base_abilities", "target_character.data.abilities"),
                        _set("target_state.form_equipment_character_id", "_target_character_id"),
                        _set("_form_original_pc_skills", []),
                        _set("_form_original_pc_saves", []),
                        _when(
                            "target_character.data.skill_proficiencies != null",
                            [
                                _set(
                                    "_form_original_pc_skills",
                                    "target_character.data.skill_proficiencies",
                                ),
                            ],
                        ),
                        _when(
                            "target_character.data.saving_throw_proficiencies != null",
                            [
                                _set(
                                    "_form_original_pc_saves",
                                    "target_character.data.saving_throw_proficiencies",
                                ),
                            ],
                        ),
                        _set(
                            "target_state.form_base_pc_proficiencies",
                            {
                                "proficiency_bonus": "2 + ((target_character.data.level - 1) // 4)",
                                "skills": "_form_original_pc_skills",
                                "saves": "_form_original_pc_saves",
                            },
                        ),
                    ],
                ),
            ],
        ),
        _require(
            "len(lasting_state.transformation_free_source_ids) > 0",
            "End existing transformations before allocating another source.",
        ),
        _set("_free_form_ids", "lasting_state.transformation_free_source_ids"),
        {"list_pop": {"list": "_free_form_ids"}, "store_as": "_new_form_id"},
        _set("lasting_state.transformation_free_source_ids", "_free_form_ids"),
        {"increment": {"field": "lasting_state.transformation_serial", "amount": 1}},
        _set(
            "lasting_state.transformation_sources.{_new_form_id}",
            {
                "installed": True,
                "spell_id": "spell",
                "spell_level": "actual_spell_level",
                "target_state_id": "_target_state_id",
                "caster_state_id": "_caster_state_id",
                "profile": "_installed_form_profile.profile",
                "form_id": "form_id",
                "mode": "mode",
                "appearance": "appearance",
                "equipment_policy": "equipment_policy",
                "started_tick": "lasting_state.lasting_tick",
                "expires_tick": (
                    "lasting_state.lasting_tick + _installed_form_profile.duration_rounds * 2"
                ),
                "concentration": "_installed_form_profile.concentration",
                "permanent": False,
                "serial": "lasting_state.transformation_serial",
                "casting_ability": "casting_ability",
                "casting_modifier": "casting_modifier",
                "transition_tick": 0,
            },
        ),
        _set("_all_form_source_ids", "lasting_state.transformation_source_ids"),
        _when(
            "_new_form_id not in _all_form_source_ids",
            [
                _push("_all_form_source_ids", "_new_form_id"),
                _set("lasting_state.transformation_source_ids", "_all_form_source_ids"),
            ],
        ),
        _set("_form_target_ids", "lasting_state.transformation_target_ids"),
        _when(
            "_target_state_id not in _form_target_ids",
            [
                _push("_form_target_ids", "_target_state_id"),
                _set("lasting_state.transformation_target_ids", "_form_target_ids"),
            ],
        ),
        _set("_form_index", "target_state.transformation_source_ids"),
        _push("_form_index", "_new_form_id"),
        _set("target_state.transformation_source_ids", "_form_index"),
        _set("target_state.has_transformations", True),
        _when(
            "_installed_form_profile.profile == 'beast'",
            [
                _lookup("definitions.beast_forms", "form_id", "_form_hp_profile"),
                _set("_accept_form_hp", "target_state.temp_hp == 0 or accept_temporary_hp == true"),
                _call(
                    "grant_temporary_hit_points",
                    {
                        "target": "{_target_state_id}",
                        "amount": "{_form_hp_profile.hit_points}",
                        "accept_new": "{accept_temporary_hp}",
                    },
                ),
                _when(
                    "_accept_form_hp == true",
                    [_set("target_state.temp_hp_transformation_source_id", "_new_form_id")],
                ),
            ],
        ),
        _when(
            "spell == 'shapechange' and equipment_policy == 'drop'",
            [
                _set("_dropped_form_items", []),
                _when(
                    "target_state.dropped_items != null",
                    [_set("_dropped_form_items", "target_state.dropped_items")],
                ),
                _set("_carried_form_items", []),
                _when(
                    "target_state.inventory != null",
                    [_set("_carried_form_items", "target_state.inventory")],
                ),
                _loop(
                    "_carried_form_items",
                    "_carried_form_item",
                    [_push("_dropped_form_items", "_carried_form_item")],
                ),
                _set("target_state.dropped_items", "_dropped_form_items"),
                _set("target_state.inventory", []),
                _set("target_state.held_items", []),
                _set("target_state.held_attack_names", []),
                _set("target_state.worn_armor", None),
                _set("target_state.worn_shield", None),
                _set("target_state.form_melded_worn_armor", None),
                _set("target_state.form_melded_worn_shield", None),
            ],
        ),
        _call(
            "project_srd_transformation",
            {"target_state": "{_target_state_id}", "lasting_state": "{_lasting_state_id}"},
        ),
    ]
    return _tool(
        (
            "Install one exact transformation and its chosen Temporary Hit Points "
            "without "
            "replacing HP."
        ),
        params,
        effects,
    )


def _target_cast_tool():
    params = [
        _entity("target_state"),
        _entity("caster_state"),
        _entity("lasting_state"),
        _parameter("spell"),
        _optional("form_id", default=""),
        _parameter("mode"),
        _optional("appearance", default=""),
        _parameter("save_dc", "integer"),
        _parameter("actual_spell_level", "integer"),
        _parameter("forego_save", "boolean"),
        _parameter("willing", "boolean"),
        _parameter("seen_before", "boolean"),
        _parameter("accept_temporary_hp", "boolean"),
        _parameter("casting_ability"),
        _parameter("casting_modifier", "integer"),
        _parameter("equipment_policy"),
        _optional("target_character", resolve="participant"),
    ]
    effects = [
        _lookup("definitions.spells", "spell", "_target_form_profile"),
        _require("target_state.is_alive == true", "A transformation target must be alive."),
        _require(
            "target_state.actor_size_projected != true",
            ("Resolve the existing resizing source before replacing its form statistics."),
        ),
        _set("_form_abilities", "target_state.ability_scores"),
        _when(
            "target_character != null",
            [
                _require(
                    (
                        "target_character.participant_id == _target_state_id and "
                        "target_character.validation_status == 'validated'"
                    ),
                    ("The selected Character File must match the transformation target."),
                ),
                _set("_form_abilities", "target_character.data.abilities"),
            ],
        ),
        _when(
            "_target_form_profile.profile == 'beast'",
            [
                _call(
                    "validate_srd_transformation_form",
                    {
                        "target_state": "{_target_state_id}",
                        "target_character": "{_target_character_id}",
                        "spell": "{spell}",
                        "form_id": "{form_id}",
                        "seen_before": "{seen_before}",
                    },
                    ["_form_abilities"],
                )
            ],
        ),
        _set("_transformation_save_outcome", "'failure'"),
        _when(
            (
                "_target_form_profile.save_ability != 'none' and (spell != "
                "'true_polymorph' or willing != true)"
            ),
            [
                _set("_form_save_modifier", None),
                _when("forego_save == true", [_set("_form_save_modifier", 0)]),
                _when(
                    "target_character != null",
                    [
                        _set(
                            "_form_save_modifier",
                            "(target_character.data.abilities.wisdom - 10) // 2",
                        ),
                        _when(
                            (
                                "target_character.data.saving_throw_proficiencies != "
                                "null "
                                "and 'wisdom' in "
                                "target_character.data.saving_throw_proficiencies"
                            ),
                            [
                                _set(
                                    "_form_save_modifier",
                                    (
                                        "_form_save_modifier + 2 + "
                                        "((target_character.data.level - 1) // 4)"
                                    ),
                                )
                            ],
                        ),
                        _when(
                            "target_state.form_statistics_replaced == true",
                            [
                                _set(
                                    "_form_save_modifier",
                                    "target_state.saving_throw_modifiers.wisdom",
                                )
                            ],
                        ),
                    ],
                    [
                        _when(
                            "target_state.saving_throw_modifiers != null and "
                            "target_state.saving_throw_modifiers.wisdom != null",
                            [
                                _set(
                                    "_form_save_modifier",
                                    "target_state.saving_throw_modifiers.wisdom",
                                )
                            ],
                        )
                    ],
                ),
                _require(
                    "_form_save_modifier != null",
                    ("The transformation target needs its saved Wisdom saving throw modifier."),
                ),
                _call(
                    "d20_test",
                    {
                        "actor_state": "{_target_state_id}",
                        "ability": "wisdom",
                        "kind": "saving_throw",
                        "ability_modifier": "{_form_save_modifier}",
                        "proficiency_bonus": 0,
                        "proficiency_factor": "none",
                        "circumstantial_modifier": 0,
                        "target_number": "{save_dc}",
                        "advantage": False,
                        "disadvantage": False,
                        "forego_save": "{forego_save}",
                    },
                    {"_transformation_save_outcome": "_outcome"},
                ),
            ],
        ),
        _when(
            "_transformation_save_outcome == 'failure'",
            [
                _call(
                    "install_srd_transformation_source",
                    {
                        "target_state": "{_target_state_id}",
                        "caster_state": "{_caster_state_id}",
                        "lasting_state": "{_lasting_state_id}",
                        "spell": "{spell}",
                        "form_id": "{form_id}",
                        "mode": "{mode}",
                        "appearance": "{appearance}",
                        "actual_spell_level": "{actual_spell_level}",
                        "accept_temporary_hp": "{accept_temporary_hp}",
                        "target_character": "{_target_character_id}",
                        "casting_ability": "{casting_ability}",
                        "casting_modifier": "{casting_modifier}",
                        "equipment_policy": "{equipment_policy}",
                    },
                )
            ],
        ),
    ]
    return _tool(
        "Resolve a real source-defined transformation save, choice, and installation.",
        params,
        effects,
    )


def _cast_tool():
    params = [
        _entity("caster_state"),
        _entity("lasting_state"),
        _parameter("spell"),
        _parameter("actual_spell_level", "integer"),
        _parameter("save_dc", "integer"),
        _parameter("casting_modifier", "integer"),
        _parameter("casting_ability"),
        _optional("targets", "array", [], items={"type": "string"}),
        _optional("target_state", resolve="entity"),
        _optional("target_character", resolve="participant"),
        _optional("form_id", default=""),
        _optional(
            "form_choices",
            "array",
            [],
            items={
                "type": "object",
                "properties": {"target": {"type": "string"}, "form_id": {"type": "string"}},
                "required": ["target", "form_id"],
                "additionalProperties": False,
            },
        ),
        _optional("option", default="aquatic"),
        _optional("appearance", default=""),
        _optional("same_basic_shape", "boolean", False),
        _optional("same_size", "boolean", False),
        _optional("seen_before", "boolean", False),
        _optional("accept_temporary_hp", "boolean", True),
        _optional("equipment_policy", default="resize"),
        _optional("target_distance", "integer", 0),
        _optional("targets_max_distance", "integer", 0),
        _optional("caster_can_see_target", "boolean", False),
        _optional("caster_can_see_all_targets", "boolean", False),
        _optional("caster_can_touch_target", "boolean", False),
        _optional("caster_can_touch_all_targets", "boolean", False),
        _optional("target_willing", "boolean", False),
        _optional("all_targets_willing", "boolean", False),
        _optional("forego_save_targets", "array", [], items={"type": "string"}),
        _optional("concentration_save_modifier", "integer"),
    ]
    effects = [
        _lookup("definitions.spells", "spell", "_cast_form_profile"),
        _set("_cast_form_targets", "targets"),
        _when(
            "_cast_form_profile.range_kind == 'self'",
            [_set("_cast_form_targets", ["_caster_state_id"])],
        ),
        _when(
            "_cast_form_profile.range_kind != 'self' and len(_cast_form_targets) == 0",
            [
                _require("target_state != null", "Select a transformation target."),
                _set("_cast_form_targets", ["_target_state_id"]),
            ],
        ),
        _require(
            ("target_character == null or target_character.participant_id in _cast_form_targets"),
            "The selected Character File must match an actual transformation target.",
        ),
        _require(
            (
                "len(_cast_form_targets) > 0 and len(_cast_form_targets) <= "
                "_cast_form_profile.base_targets + max(0, actual_spell_level - "
                "_cast_form_profile.base_level) * "
                "_cast_form_profile.targets_per_slot_level"
            ),
            "The transformation target limit is exceeded.",
        ),
        _require(
            "spell != 'wind_walk' or _caster_state_id in _cast_form_targets",
            "Wind Walk includes the caster and at most ten other creatures.",
        ),
        _when(
            "_cast_form_profile.range_kind != 'self'",
            [
                _when(
                    "len(targets) > 0",
                    [
                        _require(
                            (
                                "_cast_form_profile.requires_willing != true or "
                                "all_targets_willing == true"
                            ),
                            "Every selected transformation target must be willing.",
                        ),
                        _require(
                            (
                                "_cast_form_profile.requires_sight != true or "
                                "caster_can_see_all_targets == true"
                            ),
                            ("The caster must see every selected transformation target."),
                        ),
                        _require(
                            (
                                "_cast_form_profile.range_kind != 'touch' or "
                                "caster_can_touch_all_targets == true"
                            ),
                            ("The caster must touch every selected transformation target."),
                        ),
                        _require(
                            (
                                "_cast_form_profile.range_kind != 'distance' or "
                                "targets_max_distance <= "
                                "_cast_form_profile.range_feet"
                            ),
                            "A selected transformation target is out of range.",
                        ),
                    ],
                    [
                        _require(
                            (
                                "_cast_form_profile.requires_willing != true or "
                                "target_willing == true"
                            ),
                            "The transformation target must be willing.",
                        ),
                        _require(
                            (
                                "_cast_form_profile.requires_sight != true or "
                                "caster_can_see_target == true"
                            ),
                            "The caster must see the transformation target.",
                        ),
                        _require(
                            (
                                "_cast_form_profile.range_kind != 'touch' or "
                                "caster_can_touch_target == true"
                            ),
                            "The caster must touch the transformation target.",
                        ),
                        _require(
                            (
                                "_cast_form_profile.range_kind != 'distance' or "
                                "target_distance <= _cast_form_profile.range_feet"
                            ),
                            "The transformation target is out of range.",
                        ),
                    ],
                ),
            ],
        ),
        _set("_cast_form_mode", "'cloud'"),
        _when(
            "spell == 'alter_self'",
            [
                _require(
                    (
                        "option == 'aquatic' or option == 'appearance' or option == "
                        "'claws' or option == 'fangs' or option == 'horns' or option "
                        "== "
                        "'hooves'"
                    ),
                    "Choose an actual Alter Self option.",
                ),
                _require(
                    (
                        "option != 'appearance' or (same_basic_shape == true and "
                        "same_size == true and appearance != '')"
                    ),
                    "Alter Self appearance keeps the actual size and basic anatomy.",
                ),
                _set("_cast_form_mode", "option"),
            ],
        ),
        _require(
            (
                "spell != 'shapechange' or equipment_policy == 'resize' or "
                "equipment_policy == 'drop'"
            ),
            "Shapechange equipment must resize to fit or drop to the ground.",
        ),
        _when("lasting_state.lasting_tick == null", [_set("lasting_state.lasting_tick", 0)]),
        _when(
            "_cast_form_profile.concentration == true",
            [
                _require(
                    "concentration_save_modifier != null",
                    (
                        "The caster needs its actual Constitution saving throw "
                        "modifier "
                        "for Concentration."
                    ),
                ),
                _when(
                    "caster_state.concentration_spell_id != null",
                    [
                        _call(
                            "end_srd_concentration_node",
                            {
                                "source_state": "{_caster_state_id}",
                                "target_state": "{caster_state.concentration_target_state_id}",
                            },
                        )
                    ],
                ),
                _set("caster_state.concentration_spell_id", "spell"),
                _set("caster_state.concentration_effect_family", "'transformation_spell'"),
                _set("caster_state.concentration_target_state_id", "_cast_form_targets[0]"),
                _set("caster_state.concentration_target_state_ids", "_cast_form_targets"),
                _set("caster_state.concentration_started_tick", "lasting_state.lasting_tick"),
                _set(
                    "caster_state.concentration_expires_tick",
                    ("lasting_state.lasting_tick + _cast_form_profile.duration_rounds * 2"),
                ),
                _set("caster_state.concentration_save_modifier", "concentration_save_modifier"),
            ],
        ),
        _set("_distinct_form_targets", []),
        _set("_spell_summary_rows", []),
        _set("_spell_detail_rows", []),
        _loop(
            "_cast_form_targets",
            "_cast_form_target",
            [
                _require(
                    "_cast_form_target not in _distinct_form_targets",
                    "Each transformation target must be distinct.",
                ),
                _push("_distinct_form_targets", "_cast_form_target"),
                _set("_selected_form_id", "form_id"),
                _loop(
                    "form_choices",
                    "_form_choice",
                    [
                        _require(
                            "_form_choice.target in _cast_form_targets",
                            ("A form choice must belong to a selected transformation target."),
                        ),
                        _when(
                            "_form_choice.target == _cast_form_target",
                            [_set("_selected_form_id", "_form_choice.form_id")],
                        ),
                    ],
                ),
                _set("_selected_form_character", None),
                _when(
                    (
                        "target_character != null and target_character.participant_id "
                        ""
                        ""
                        ""
                        ""
                        ""
                        ""
                        ""
                        "== "
                        "_cast_form_target"
                    ),
                    [_set("_selected_form_character", "_target_character_id")],
                ),
                _call(
                    "resolve_srd_transformation_target_cast",
                    {
                        "target_state": "{_cast_form_target}",
                        "target_character": "{_selected_form_character}",
                        "caster_state": "{_caster_state_id}",
                        "lasting_state": "{_lasting_state_id}",
                        "spell": "{spell}",
                        "form_id": "{_selected_form_id}",
                        "mode": "{_cast_form_mode}",
                        "appearance": "{appearance}",
                        "save_dc": "{save_dc}",
                        "actual_spell_level": "{actual_spell_level}",
                        "forego_save": "{_cast_form_target in forego_save_targets}",
                        "willing": "{target_willing == true or all_targets_willing == true}",
                        "seen_before": "{seen_before}",
                        "accept_temporary_hp": "{accept_temporary_hp}",
                        "casting_ability": "{casting_ability}",
                        "casting_modifier": "{casting_modifier}",
                        "equipment_policy": "{equipment_policy}",
                    },
                    ["_transformation_save_outcome"],
                ),
                _call(
                    "record_srd_spell_target_result",
                    {
                        "target": "{_cast_form_target}",
                        "brief": "{spell}: {_transformation_save_outcome}",
                        "detail": (
                            "{spell}: {_transformation_save_outcome}; the actual "
                            "source-owned form and statistics persist."
                        ),
                    },
                    ["_spell_target_summary", "_spell_target_detail"],
                ),
                _push("_spell_summary_rows", "_spell_target_summary"),
                _push("_spell_detail_rows", "_spell_target_detail"),
            ],
        ),
    ]
    return _tool(
        (
            "Resolve the complete validated transformation target set at the ordinary "
            ""
            ""
            ""
            ""
            ""
            ""
            ""
            ""
            "casting and cost owner."
        ),
        params,
        effects,
    )


def _followup_tools():
    def selected_form():
        return [
            _set("_change_form_id", "_change_request.form_id"),
            _set("_change_target_character_id", None),
            _when(
                "target_character != null",
                [_set("_change_target_character_id", "_target_character_id")],
            ),
            _when(
                "len(source_forms) > 0",
                [
                    _require(
                        "_change_source.spell_id == 'animal_shapes'",
                        "Per-source form choices belong to Animal Shapes.",
                    ),
                    _when(
                        "_change_request.target_character != null",
                        [
                            _set(
                                "_change_target_character_id",
                                "_change_request.target_character",
                            )
                        ],
                    ),
                ],
            ),
        ]

    change_leaf = _tool(
        (
            "Change one original transformation's form without refreshing its first "
            "Temporary Hit Point grant."
        ),
        [
            _entity("actor_state"),
            _entity("target_state"),
            _entity("lasting_state"),
            _parameter("source_id"),
            _optional("form_id", default=""),
            _parameter("option"),
            _optional("appearance", default=""),
            _parameter("seen_before", "boolean"),
            _parameter("same_size", "boolean"),
            _parameter("same_basic_shape", "boolean"),
            _parameter("validate_only", "boolean"),
            _optional("target_character", resolve="participant"),
        ],
        [
            _set("_changing_form", "lasting_state.transformation_sources[source_id]"),
            _require(
                (
                    "_changing_form.installed == true and "
                    "_changing_form.target_state_id "
                    "== _target_state_id"
                ),
                "The original transformation is no longer installed on this target.",
            ),
            _require(
                (
                    "(_changing_form.spell_id == 'animal_shapes' and "
                    "_changing_form.caster_state_id == _actor_state_id) or "
                    "(_target_state_id == _actor_state_id and "
                    "(_changing_form.spell_id == "
                    "'alter_self' or _changing_form.spell_id == 'shapechange' or "
                    "_changing_form.spell_id == 'wind_walk' or "
                    "_changing_form.spell_id == "
                    "'gaseous_form'))"
                ),
                (
                    "Only the spell's actual caster or permitted transformed creature "
                    ""
                    ""
                    ""
                    ""
                    ""
                    ""
                    ""
                    "can "
                    "change this form."
                ),
            ),
            _when(
                (
                    "_changing_form.spell_id == 'animal_shapes' or "
                    "_changing_form.spell_id == 'shapechange'"
                ),
                [
                    _call(
                        "validate_srd_transformation_form",
                        {
                            "target_state": "{_target_state_id}",
                            "target_character": "{_target_character_id}",
                            "spell": "{_changing_form.spell_id}",
                            "form_id": "{form_id}",
                            "seen_before": "{seen_before}",
                        },
                    )
                ],
            ),
            _when(
                "_changing_form.spell_id == 'alter_self'",
                [
                    _require(
                        (
                            "option == 'aquatic' or option == 'appearance' or option "
                            "== "
                            "'claws' or option == 'fangs' or option == 'horns' or "
                            "option "
                            "== 'hooves'"
                        ),
                        "Choose an actual Alter Self option.",
                    ),
                    _require(
                        (
                            "option != 'appearance' or (same_size == true and "
                            "same_basic_shape == true and appearance != '')"
                        ),
                        ("Alter Self appearance preserves the actual size and basic anatomy."),
                    ),
                ],
            ),
            _require(
                (
                    "_changing_form.spell_id != 'wind_walk' or _changing_form.mode == "
                    ""
                    ""
                    ""
                    ""
                    ""
                    ""
                    ""
                    ""
                    "'normal' or _changing_form.mode == 'cloud'"
                ),
                ("Finish the actual one-minute transformation before changing form again."),
            ),
            _when(
                "validate_only != true",
                [
                    _when(
                        (
                            "_changing_form.spell_id == 'animal_shapes' or "
                            "_changing_form.spell_id == 'shapechange'"
                        ),
                        [
                            _set(
                                ("lasting_state.transformation_sources.{source_id}.form_id"),
                                "form_id",
                            )
                        ],
                    ),
                    _when(
                        "_changing_form.spell_id == 'alter_self'",
                        [
                            _set("lasting_state.transformation_sources.{source_id}.mode", "option"),
                            _set(
                                ("lasting_state.transformation_sources.{source_id}.appearance"),
                                "appearance",
                            ),
                        ],
                    ),
                    _when(
                        "_changing_form.spell_id == 'gaseous_form'",
                        [
                            _call(
                                "end_srd_transformation_source",
                                {"target_state": "{_target_state_id}", "source_id": "{source_id}"},
                            )
                        ],
                    ),
                    _when(
                        "_changing_form.spell_id == 'wind_walk'",
                        [
                            _set("_new_wind_mode", "'to_cloud'"),
                            _when(
                                "_changing_form.mode == 'cloud'",
                                [_set("_new_wind_mode", "'to_normal'")],
                            ),
                            _set(
                                ("lasting_state.transformation_sources.{source_id}.mode"),
                                "_new_wind_mode",
                            ),
                            _set(
                                (
                                    "lasting_state.transformation_sources.{source_id}"
                                    ".tra"
                                    "nsition_tick"
                                ),
                                "lasting_state.lasting_tick + 20",
                            ),
                            _when(
                                "target_state.concentration_spell_id != null",
                                [
                                    _call(
                                        "end_srd_concentration_node",
                                        {
                                            "source_state": "{_target_state_id}",
                                            "target_state": (
                                                "{target_state.concentration_target_state_id}"
                                            ),
                                        },
                                    )
                                ],
                            ),
                        ],
                    ),
                    _call(
                        "project_srd_transformation",
                        {
                            "target_state": "{_target_state_id}",
                            "lasting_state": "{_lasting_state_id}",
                        },
                    ),
                ],
            ),
        ],
    )
    change = _tool(
        (
            "Spend one Magic action to change the original eligible form or selected "
            "Animal Shapes subjects."
        ),
        [
            _entity("actor_state"),
            _optional("source_ids", "array", [], items={"type": "string"}),
            _optional(
                "source_forms",
                "array",
                [],
                max_items=64,
                items={
                    "type": "object",
                    "properties": {
                        "source_id": {"type": "string"},
                        "form_id": {"type": "string"},
                        "target_character": {"type": "string"},
                    },
                    "required": ["source_id", "form_id"],
                    "additionalProperties": False,
                },
            ),
            _optional("form_id", default=""),
            _optional("option", default="aquatic"),
            _optional("appearance", default=""),
            _optional("seen_before", "boolean", False),
            _optional("same_size", "boolean", False),
            _optional("same_basic_shape", "boolean", False),
            _optional("target_character", resolve="participant"),
            _optional("lasting_state", default="state", resolve="entity", model_hidden=True),
        ],
        [
            _call(
                "project_srd_transformation",
                {"target_state": "{_actor_state_id}", "lasting_state": "{_lasting_state_id}"},
            ),
            _set("_change_source_ids", []),
            _loop(
                "source_ids",
                "_change_selected_source",
                [_push("_change_source_ids", "_change_selected_source")],
            ),
            _set("_change_requests", []),
            _require(
                "len(source_forms) == 0 or len(source_ids) == 0 or "
                "len(source_forms) == len(source_ids)",
                "Supply one form choice for each selected source.",
            ),
            _loop(
                "source_forms",
                "_change_form_row",
                [
                    _require(
                        "_change_form_row.source_id != '' and _change_form_row.form_id != ''",
                        "Select distinct sources and a form for each.",
                    ),
                    _when(
                        "len(source_ids) > 0",
                        [
                            _require(
                                "_change_form_row.source_id in source_ids",
                                "Each form choice must match a selected source.",
                            )
                        ],
                    ),
                    _push("_change_requests", "_change_form_row"),
                    _when(
                        "len(source_ids) == 0",
                        [_push("_change_source_ids", "_change_form_row.source_id")],
                    ),
                ],
            ),
            _when(
                "len(_change_source_ids) == 0",
                [
                    _require(
                        "actor_state.form_current_source_id != null",
                        "Select an actual active transformation.",
                    ),
                    _set("_change_source_ids", ["actor_state.form_current_source_id"]),
                ],
            ),
            _when(
                "len(source_forms) == 0",
                [
                    _loop(
                        "_change_source_ids",
                        "_change_source_id",
                        [
                            _push(
                                "_change_requests",
                                {"source_id": "_change_source_id", "form_id": "form_id"},
                            )
                        ],
                    )
                ],
            ),
            _set("_change_unique_ids", []),
            _loop(
                "_change_requests",
                "_change_request",
                [
                    _set("_change_source_id", "_change_request.source_id"),
                    _require(
                        (
                            "lasting_state.transformation_sources != null and "
                            "_change_source_id in "
                            "lasting_state.transformation_sources "
                            "and _change_source_id not in _change_unique_ids"
                        ),
                        "Select distinct existing transformation sources.",
                    ),
                    _push("_change_unique_ids", "_change_source_id"),
                    _set(
                        "_change_source", "lasting_state.transformation_sources[_change_source_id]"
                    ),
                    _require(
                        (
                            "len(_change_source_ids) == 1 or _change_source.spell_id "
                            "== "
                            "'animal_shapes'"
                        ),
                        ("Only Animal Shapes changes multiple subjects with one Magic action."),
                    ),
                    *selected_form(),
                    _call(
                        "change_srd_transformation_source",
                        {
                            "actor_state": "{_actor_state_id}",
                            "target_state": "{_change_source.target_state_id}",
                            "target_character": "{_change_target_character_id}",
                            "lasting_state": "{_lasting_state_id}",
                            "source_id": "{_change_source_id}",
                            "form_id": "{_change_form_id}",
                            "option": "{option}",
                            "appearance": "{appearance}",
                            "seen_before": "{seen_before}",
                            "same_size": "{same_size}",
                            "same_basic_shape": "{same_basic_shape}",
                            "validate_only": True,
                        },
                    ),
                ],
            ),
            _require(
                (
                    "not ('incapacitated' in actor_state.conditions or 'stunned' in "
                    "actor_state.conditions or 'unconscious' in "
                    "actor_state.conditions or "
                    "'paralyzed' in actor_state.conditions or 'petrified' in "
                    "actor_state.conditions)"
                ),
                ("An incapacitated creature cannot take the transformation's Magic action."),
            ),
            _when(
                "combat.encounter_active == true",
                [
                    _require(
                        ("combat.turn_active == true and combat.active_actor == _actor_state_id"),
                        "Change form on the acting creature's actual turn.",
                    ),
                    _set("combat.transformation_magic_allowed", True),
                    _call(
                        "spend_srd_action",
                        {"magic_action": True, "control_action_kind": "transformation"},
                    ),
                    _set("combat.transformation_magic_allowed", False),
                ],
            ),
            _loop(
                "_change_requests",
                "_change_request",
                [
                    _set("_change_source_id", "_change_request.source_id"),
                    _set(
                        "_change_source", "lasting_state.transformation_sources[_change_source_id]"
                    ),
                    *selected_form(),
                    _call(
                        "change_srd_transformation_source",
                        {
                            "actor_state": "{_actor_state_id}",
                            "target_state": "{_change_source.target_state_id}",
                            "target_character": "{_change_target_character_id}",
                            "lasting_state": "{_lasting_state_id}",
                            "source_id": "{_change_source_id}",
                            "form_id": "{_change_form_id}",
                            "option": "{option}",
                            "appearance": "{appearance}",
                            "seen_before": "{seen_before}",
                            "same_size": "{same_size}",
                            "same_basic_shape": "{same_basic_shape}",
                            "validate_only": False,
                        },
                    ),
                ],
            ),
        ],
    )
    end_animal = _tool(
        ("The actual Animal Shapes subject spends its Bonus Action to end only that source."),
        [
            _entity("actor_state"),
            _parameter("source_id"),
            _optional("lasting_state", default="state", resolve="entity", model_hidden=True),
        ],
        [
            _require(
                (
                    "lasting_state.transformation_sources != null and source_id in "
                    "lasting_state.transformation_sources"
                ),
                "Select the actual Animal Shapes source.",
            ),
            _set("_animal_source", "lasting_state.transformation_sources[source_id]"),
            _require(
                (
                    "_animal_source.installed == true and _animal_source.spell_id == "
                    "'animal_shapes' and _animal_source.target_state_id == "
                    "_actor_state_id"
                ),
                "Only this Animal Shapes subject can end its transformation.",
            ),
            _when(
                "combat.encounter_active == true",
                [
                    _require(
                        ("combat.turn_active == true and combat.active_actor == _actor_state_id"),
                        "End Animal Shapes on the subject's actual turn.",
                    ),
                    _call(
                        "consume_srd_bonus_action",
                        {"actor_state": "{_actor_state_id}", "bonus_action": "end_animal_shape"},
                    ),
                ],
            ),
            _call(
                "end_srd_transformation_source",
                {"target_state": "{_actor_state_id}", "source_id": "{source_id}"},
            ),
        ],
    )
    appearance = _tool(
        (
            "Read the actual effective form; Truesight distinguishes its preserved "
            "original identity."
        ),
        [
            _entity("observer_state"),
            _entity("target_state"),
            _parameter("distance", "number", minimum=0),
            _optional("lasting_state", default="state", resolve="entity", model_hidden=True),
        ],
        [
            _call(
                "project_srd_transformation",
                {"target_state": "{_target_state_id}", "lasting_state": "{_lasting_state_id}"},
            ),
            _set("_transformed_appearance", "target_state.name"),
            _set("_transformed_true_identity", None),
            _when(
                ("target_state.form_appearance != null and target_state.form_appearance != ''"),
                [_set("_transformed_appearance", "target_state.form_appearance")],
            ),
            _call(
                "resolve_srd_timed_senses",
                {"target_state": "{_observer_state_id}"},
                ["_sense_truesight"],
            ),
            _when(
                (
                    "_sense_truesight >= distance and _sense_truesight > 0 and "
                    "'blinded' "
                    "not in observer_state.conditions"
                ),
                [_set("_transformed_true_identity", "target_state.name")],
            ),
            _set("_transformation_source_ids", "target_state.transformation_source_ids"),
        ],
    )
    for tool in (change, end_animal, appearance):
        tool["metadata"] = {"category": "spell-effect", "costs_action": False}
    return {
        "change_srd_transformation_source": change_leaf,
        "change_srd_transformation": change,
        "end_srd_animal_shape": end_animal,
        "read_srd_transformed_appearance": appearance,
    }


def _attack_tool():
    tool = _tool(
        ("Use one actual named Beast-form attack and its source-owned numbers and anatomy."),
        [
            _entity("actor_state"),
            _entity("target_state"),
            _parameter("attack_name"),
            _parameter("attack_distance", "integer", minimum=0),
            _parameter("actor_can_see_target", "boolean"),
            _parameter("target_can_see_actor", "boolean"),
            _parameter("target_is_charmer", "boolean"),
            _parameter("fear_source_in_sight", "boolean"),
            _optional("advantage", "boolean", False),
            _optional("disadvantage", "boolean", False),
            _optional("use_multiattack", "boolean", False),
            _optional("witness_state_ids", "array", None, items={"type": "string"}),
            _optional("lasting_state", default="state", resolve="entity", model_hidden=True),
        ],
        [
            _call(
                "project_srd_transformation",
                {"target_state": "{_actor_state_id}", "lasting_state": "{_lasting_state_id}"},
            ),
            _require(
                (
                    "actor_state.form_statistics_replaced == true and "
                    "actor_state.form_current_source_id != null"
                ),
                "Use the actual active Beast form's attack.",
            ),
            _set(
                "_attacking_form",
                ("lasting_state.transformation_sources[actor_state.form_current_source_id]"),
            ),
            _lookup("definitions.beast_forms", "_attacking_form.form_id", "_attacking_beast"),
            _require(
                "attack_name in _attacking_beast.attacks",
                "This attack is absent from the admitted form's stat block.",
            ),
            _set("_form_attack", "_attacking_beast.attacks[attack_name]"),
            _require(
                "_form_attack.supported == true",
                "The selected source attack has an unimplemented fixed rider.",
            ),
            _require(
                ("_form_attack.prone_max_size_rank < 0 or target_state.size_rank != null"),
                "This source rider needs the target's actual saved size.",
            ),
            _set("_form_attack_advantage", "advantage"),
            _when(
                "_attacking_beast.pack_tactics == true",
                [
                    _require(
                        "actor_state.ally_state_ids != null",
                        "Pack Tactics requires the actor's saved ally relationships.",
                    ),
                    _set("_form_pack_allies", "actor_state.ally_state_ids"),
                    _loop(
                        "_form_pack_allies",
                        "_form_pack_ally_id",
                        [
                            _when(
                                "_form_pack_ally_id != _actor_state_id",
                                [
                                    _call(
                                        "check_srd_form_pack_ally",
                                        {
                                            "ally_state": "{_form_pack_ally_id}",
                                            "target_state": "{_target_state_id}",
                                        },
                                        ["_form_pack_ally_eligible"],
                                    ),
                                    _when(
                                        "_form_pack_ally_eligible == true",
                                        [_set("_form_attack_advantage", True)],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            _when(
                (
                    "_form_attack.bloodied_damage_expression != '' or "
                    "_form_attack.target_not_full_hp_advantage == true"
                ),
                [
                    _require(
                        "target_state.hp != null and target_state.max_hp != null",
                        (
                            "This source attack needs the target's actual current and "
                            "maximum Hit "
                            "Points."
                        ),
                    )
                ],
            ),
            _when(
                (
                    "_form_attack.bloodied_damage_expression != '' and "
                    "target_state.hp * 2 <= "
                    "target_state.max_hp"
                ),
                [
                    _set(
                        "_form_attack_patch",
                        {
                            "damage_expression": "_form_attack.bloodied_damage_expression",
                            "damage_modifier": "_form_attack.bloodied_damage_modifier",
                        },
                    ),
                    _set("_form_attack", "merge(_form_attack, _form_attack_patch)"),
                ],
            ),
            _when(
                (
                    "_form_attack.target_not_full_hp_advantage == true and "
                    "target_state.hp < "
                    "target_state.max_hp"
                ),
                [_set("_form_attack_advantage", True)],
            ),
            _set(
                "actor_state.form_attack_has_extra_damage",
                "_form_attack.extra_damage_expression != ''",
            ),
            _set("actor_state.form_attack_in_progress", True),
            _when(
                "_form_attack.recharge_min != null",
                [_call("consume_srd_form_recharge_attack", {
                    "target_state": "{_actor_state_id}", "lasting_state": "{_lasting_state_id}",
                    "attack_name": "{attack_name}",
                })],
            ),
            _call(
                "resolve_adjudicated_creature_attack",
                {
                    "actor": "{_actor_state_id}",
                    "target": "{_target_state_id}",
                    "attack_name": "{attack_name}",
                    "attack_modifier": "{_form_attack.attack_modifier}",
                    "damage_expression": "{_form_attack.damage_expression}",
                    "damage_modifier": "{_form_attack.damage_modifier}",
                    "damage_type": "{_form_attack.damage_type}",
                    "attack_mode": "{_form_attack.kind}",
                    "attack_distance": "{attack_distance}",
                    "normal_range": "{_form_attack.normal_range}",
                    "long_range": "{_form_attack.long_range}",
                    "actor_can_see_target": "{actor_can_see_target}",
                    "target_can_see_actor": "{target_can_see_actor}",
                    "target_is_charmer": "{target_is_charmer}",
                    "fear_source_in_sight": "{fear_source_in_sight}",
                    "advantage": "{_form_attack_advantage}",
                    "disadvantage": "{disadvantage}",
                    "use_multiattack": "{use_multiattack}",
                    "witness_state_ids": "{witness_state_ids}",
                    "attack_uses_weapon": False,
                },
                {
                    "_attack_roll": "_attack_roll",
                    "_attack_total": "_attack_adjusted_total",
                    "_attack_outcome": "_attack_outcome",
                    "_damage_roll": "_damage_roll",
                    "_effective_damage": "_damage_applied",
                    "_hp_lost": "_damage_hp_lost",
                    "_form_extra_damage_roll": "_form_extra_damage_roll",
                    "_form_extra_damage_amount": "_form_extra_damage_amount",
                    "_form_primary_damage_amount": "_form_primary_damage_amount",
                    "_form_concentration_save_dc": "_form_concentration_save_dc",
                },
            ),
            _set("actor_state.form_attack_in_progress", False),
            _set("actor_state.form_attack_has_extra_damage", False),
            _when(
                (
                    "(_attack_outcome == 'hit' or _attack_outcome == 'critical_hit') "
                    "and "
                    "_form_attack.prone_max_size_rank >= 0 and "
                    "target_state.size_rank <= _form_attack.prone_max_size_rank"
                ),
                [
                    _call(
                        "apply_srd_condition",
                        {
                            "target": "{_target_state_id}",
                            "condition": "prone",
                            "source_state_id": "{_actor_state_id}",
                        },
                    )
                ],
            ),
        ],
    )
    tool["metadata"] = {"category": "combat", "costs_action": False}
    return tool


def _altered_natural_attack_tool():
    "Derive a prepared NPC's damaging Unarmed Strike from its actual Alter Self growth."
    tool = _tool(
        "Attack with the active Alter Self growth using saved proficiency and caster modifier.",
        [
            _entity("actor_state"),
            _entity("target_state"),
            _parameter("attack_distance", "integer", minimum=0),
            _parameter("actor_can_see_target", "boolean"),
            _parameter("target_can_see_actor", "boolean"),
            _parameter("target_is_charmer", "boolean"),
            _parameter("fear_source_in_sight", "boolean"),
            _optional("advantage", "boolean", False),
            _optional("disadvantage", "boolean", False),
            _optional("witness_state_ids", "array", None, items={"type": "string"}),
            _optional("lasting_state", default="state", resolve="entity", model_hidden=True),
        ],
        [
            _call(
                "project_srd_transformation",
                {"target_state": "{_actor_state_id}", "lasting_state": "{_lasting_state_id}"},
            ),
            _require(
                "actor_state.creature_kind == 'monster'",
                "Characters use their artifact-derived Unarmed Strike with the growth option.",
            ),
            _require(
                "actor_state.form_current_source_id != null",
                "This creature has no active Alter Self growth.",
            ),
            _set(
                "_natural_source",
                "lasting_state.transformation_sources[actor_state.form_current_source_id]",
            ),
            _require(
                "_natural_source.spell_id == 'alter_self' and "
                "_natural_source.caster_state_id == _actor_state_id and "
                "_natural_source.target_state_id == _actor_state_id",
                "The natural weapon must belong to this creature's actual self-cast spell.",
            ),
            _require(
                "actor_state.alter_self_option == 'claws' or "
                "actor_state.alter_self_option == 'fangs' or "
                "actor_state.alter_self_option == 'horns' or "
                "actor_state.alter_self_option == 'hooves'",
                "The active Alter Self option does not grant a natural weapon.",
            ),
            _require(
                "actor_state.proficiency_bonus != null",
                "A damaging Unarmed Strike needs the creature's actual saved Proficiency Bonus.",
            ),
            _require(
                "actor_state.proficiency_bonus >= 2 and actor_state.proficiency_bonus <= 9",
                "The saved Proficiency Bonus is outside the admitted creature range.",
            ),
            _set(
                "_natural_attack_modifier",
                "actor_state.proficiency_bonus + _natural_source.casting_modifier",
            ),
            _set("_natural_damage_type", "'piercing'"),
            _when(
                "actor_state.alter_self_option == 'claws'",
                [_set("_natural_damage_type", "'slashing'")],
            ),
            _when(
                "actor_state.alter_self_option == 'hooves'",
                [_set("_natural_damage_type", "'bludgeoning'")],
            ),
            _set("actor_state.altered_natural_attack_in_progress", True),
            _call(
                "resolve_adjudicated_creature_attack",
                {
                    "actor": "{_actor_state_id}",
                    "target": "{_target_state_id}",
                    "attack_name": "Alter Self Unarmed Strike",
                    "attack_modifier": "{_natural_attack_modifier}",
                    "damage_expression": "d6",
                    "damage_modifier": "{_natural_source.casting_modifier}",
                    "damage_type": "{_natural_damage_type}",
                    "attack_mode": "melee",
                    "attack_distance": "{attack_distance}",
                    "normal_range": 5,
                    "long_range": 5,
                    "actor_can_see_target": "{actor_can_see_target}",
                    "target_can_see_actor": "{target_can_see_actor}",
                    "target_is_charmer": "{target_is_charmer}",
                    "fear_source_in_sight": "{fear_source_in_sight}",
                    "advantage": "{advantage}",
                    "disadvantage": "{disadvantage}",
                    "use_multiattack": False,
                    "witness_state_ids": "{witness_state_ids}",
                    "attack_uses_weapon": False,
                },
                {
                    "_attack_roll": "_attack_roll",
                    "_attack_total": "_attack_adjusted_total",
                    "_attack_outcome": "_attack_outcome",
                    "_damage_roll": "_damage_roll",
                    "_effective_damage": "_damage_applied",
                    "_hp_lost": "_damage_hp_lost",
                },
            ),
            _set("actor_state.altered_natural_attack_in_progress", False),
        ],
    )
    tool["metadata"] = {"category": "combat", "costs_action": False}
    return tool


def _form_movement_permissions_tool():
    "Expose only the effective Gaseous Form source's printed movement permissions."
    return _tool(
        "Source-qualified narrow-opening, creature-space and solid-liquid permissions.",
        [
            _entity("actor_state"),
            _optional("lasting_state", default="state", resolve="entity", model_hidden=True),
        ],
        [
            _set("_form_narrow_openings", False),
            _set("_form_share_creature_space", False),
            _set("_form_liquids_solid", False),
            _when(
                "actor_state.has_transformations == true",
                [
                    _call(
                        "project_srd_transformation",
                        {
                            "target_state": "{_actor_state_id}",
                            "lasting_state": "{_lasting_state_id}",
                        },
                    ),
                    _when(
                        "actor_state.form_cloud == true and "
                        "actor_state.form_current_source_id != null",
                        [
                            _set(
                                "_form_movement_source",
                                "lasting_state.transformation_sources["
                                "actor_state.form_current_source_id]",
                            ),
                            _when(
                                "_form_movement_source.installed == true and "
                                "_form_movement_source.spell_id == 'gaseous_form' and "
                                "_form_movement_source.mode == 'cloud'",
                                [
                                    _set("_form_narrow_openings", True),
                                    _set("_form_share_creature_space", True),
                                    _set("_form_liquids_solid", True),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _walk_lists(effects):
    yield effects
    for effect in effects:
        for key in ("on_true", "on_false"):
            if key in effect:
                yield from _walk_lists(effect[key])
        if "for_each" in effect:
            yield from _walk_lists(effect["for_each"]["do"])


def _owned_hook(tool, condition, name, params, *, first=False):
    """Rebuild one uniquely owned wrapper, keeping repeated generation byte stable."""
    tool["effects"] = [
        e for e in tool["effects"] if not (e.get("condition") == condition and name in str(e))
    ]
    hook = _when(condition, [_call(name, params)])
    tool["effects"].insert(0, hook) if first else tool["effects"].append(hook)


def _respiration_interval_tool():
    "Settle real elapsed breathing through saved form boundaries before projection changes."
    period_body = [
        _set("_form_respiration_air", "target_state.respiration_native_air"),
        _set("_form_respiration_water", "target_state.respiration_native_water"),
        _set("_form_respiration_capacity", "target_state.respiration_capacity_ticks"),
        _when(
            "target_state.respiration_base_capacity_ticks != null",
            [_set("_form_respiration_capacity", "target_state.respiration_base_capacity_ticks")],
        ),
        _when(
            "_form_respiration_winner != null",
            [
                _when(
                    "_form_respiration_winner.profile == 'beast'",
                    [
                        _lookup(
                            "definitions.beast_forms",
                            "_form_respiration_winner.form_id",
                            "_respiration_beast",
                        ),
                        _set("_form_respiration_air", "_respiration_beast.breathes_air"),
                        _set("_form_respiration_water", "_respiration_beast.breathes_water"),
                        _set(
                            "_form_respiration_capacity",
                            "max(_respiration_beast.hold_breath_ticks, "
                            "max(5, (1 + "
                            "(_respiration_beast.ability_scores.constitution - 10) // "
                            ""
                            ""
                            ""
                            ""
                            ""
                            "2) * 10) * 2)",
                        ),
                    ],
                ),
                _when(
                    (
                        "_form_respiration_winner.profile == 'alter' and "
                        "_form_respiration_winner.mode == 'aquatic'"
                    ),
                    [
                        _set("_form_respiration_water", True),
                    ],
                ),
            ],
        ),
        _call(
            "resolve_srd_respiration_interval",
            {
                "target_state": "{_target_state_id}",
                "free_time": True,
                "form_at_tick": "{_form_respiration_end}",
                "form_native_air": "{_form_respiration_air}",
                "form_native_water": "{_form_respiration_water}",
                "form_capacity_ticks": "{_form_respiration_capacity}",
            },
        ),
        _set("_form_respiration_cursor", "_form_respiration_end"),
    ]
    return _tool(
        (
            "Consume elapsed airlessness in the actual winning source body, including "
            ""
            ""
            ""
            ""
            ""
            "expiry and overlapping forms."
        ),
        [_entity("target_state"), _entity("lasting_state")],
        [
            _when(
                (
                    "target_state.respiration_medium != null and "
                    "target_state.respiration_tick < lasting_state.lasting_tick"
                ),
                [
                    _set("_form_respiration_ids", []),
                    _when(
                        "target_state.transformation_source_ids != null",
                        [
                            _set("_form_respiration_ids", "target_state.transformation_source_ids"),
                        ],
                    ),
                    _loop(
                        "_form_respiration_ids",
                        "_respiration_source_id",
                        [
                            _set(
                                "_respiration_source",
                                ("lasting_state.transformation_sources[_respiration_source_id]"),
                            ),
                            _when(
                                "_respiration_source.installed == true",
                                [
                                    _call(
                                        "check_srd_transformation_source",
                                        {
                                            "target_state": "{_target_state_id}",
                                            "caster_state": "{_respiration_source.caster_state_id}",
                                            "lasting_state": "{_lasting_state_id}",
                                            "source_id": "{_respiration_source_id}",
                                        },
                                    ),
                                ],
                            ),
                        ],
                    ),
                    _set("_form_respiration_cursor", "target_state.respiration_tick"),
                    _set("_form_respiration_steps", []),
                    _loop(
                        "_form_respiration_ids",
                        "_respiration_step_id",
                        [
                            _push("_form_respiration_steps", "_respiration_step_id"),
                        ],
                    ),
                    _push("_form_respiration_steps", None),
                    _loop(
                        "_form_respiration_steps",
                        "_respiration_unused_step",
                        [
                            _when(
                                ("_form_respiration_cursor < lasting_state.lasting_tick"),
                                [
                                    _set("_form_respiration_end", "lasting_state.lasting_tick"),
                                    _set("_form_respiration_winner", None),
                                    _loop(
                                        "_form_respiration_ids",
                                        "_respiration_candidate_id",
                                        [
                                            _set(
                                                "_respiration_candidate",
                                                (
                                                    "lasting_state.transformation_sou"
                                                    "rces"
                                                    "[_respiration_candidate_id]"
                                                ),
                                            ),
                                            _when(
                                                ("_respiration_candidate.installed == true"),
                                                [
                                                    _when(
                                                        (
                                                            "_respiration_candidate.s"
                                                            "tart"
                                                            "ed_tick > "
                                                            "_form_respiration_cursor"
                                                        ),
                                                        [
                                                            _set(
                                                                ("_form_respiration_end"),
                                                                (
                                                                    "min(_form_respiratio"
                                                                    "n_end, "
                                                                    "_respiration_candida"
                                                                    "te.started_tick)"
                                                                ),
                                                            ),
                                                        ],
                                                    ),
                                                    _when(
                                                        (
                                                            "_respiration_candidate.e"
                                                            "xpir"
                                                            "es_tick != null and "
                                                            "_respiration_candidate.e"
                                                            "xpir"
                                                            "es_tick > "
                                                            "_form_respiration_cursor"
                                                        ),
                                                        [
                                                            _set(
                                                                ("_form_respiration_end"),
                                                                (
                                                                    "min(_form_respiratio"
                                                                    "n_end, "
                                                                    "_respiration_candida"
                                                                    "te.expires_tick)"
                                                                ),
                                                            ),
                                                        ],
                                                    ),
                                                    _when(
                                                        "_respiration_candidate.start"
                                                        "ed_tick <= "
                                                        "_form_respiration_cursor and "
                                                        ""
                                                        ""
                                                        ""
                                                        ""
                                                        ""
                                                        ""
                                                        "(_respiration_candidate.perm"
                                                        "anent == true or "
                                                        "_respiration_candidate.expir"
                                                        "es_tick > "
                                                        "_form_respiration_cursor)",
                                                        [
                                                            _when(
                                                                "_form_respiration_wi"
                                                                "nner == null or "
                                                                "_respiration_candida"
                                                                "te.serial > "
                                                                "_form_respiration_wi"
                                                                "nner.serial",
                                                                [
                                                                    _set(
                                                                        (
                                                                            "_form_respiration_wi"
                                                                            "nner"
                                                                        ),
                                                                        ("_respiration_candidate"),
                                                                    ),
                                                                ],
                                                            ),
                                                        ],
                                                    ),
                                                ],
                                            ),
                                        ],
                                    ),
                                    *period_body,
                                ],
                            ),
                        ],
                    ),
                    _require(
                        "_form_respiration_cursor == lasting_state.lasting_tick",
                        ("Respiration cannot skip an unresolved transformation time boundary."),
                    ),
                ],
            )
        ],
    )


def _pack_ally_tool():
    "Qualify a real saved ally by current incapacitation and authoritative scene distance."
    return _tool(
        (
            "Derive Pack Tactics from a living, non-incapacitated ally actually "
            "within 5 "
            "feet of the target."
        ),
        [
            _entity("ally_state"),
            _entity("target_state"),
            _optional("lasting_state", default="state", resolve="entity", model_hidden=True),
        ],
        [
            _call(
                "refresh_srd_timed_condition_target",
                {"target_state": "{_ally_state_id}", "lasting_state": "{_lasting_state_id}"},
            ),
            _require(
                "ally_state.conditions != null",
                "Pack Tactics needs the ally's actual condition state.",
            ),
            _set("_form_pack_ally_eligible", False),
            _when(
                "ally_state.is_alive == true and not ('incapacitated' in "
                "ally_state.conditions or "
                "'paralyzed' in ally_state.conditions or 'petrified' in "
                "ally_state.conditions or "
                "'stunned' in ally_state.conditions or 'unconscious' in "
                "ally_state.conditions)",
                [
                    _call(
                        "resolve_srd_scene_geometry_relation",
                        {"actor_state": "{_ally_state_id}", "target_state": "{_target_state_id}"},
                        ["_scene_distance", "_scene_same_plane"],
                    ),
                    _set(
                        "_form_pack_ally_eligible",
                        "_scene_same_plane == true and _scene_distance <= 5",
                    ),
                ],
            ),
        ],
    )


def _extra_attack_damage_tool(*, qualified=False):
    "Apply a source attack's separate printed damage types in one actual damage event."
    return _tool(
        ("Roll the named body's extra source damage, preserving critical dice and type defenses."),
        [
            _entity("actor_state"),
            _entity("target_state"),
            _entity("lasting_state"),
            _parameter("attack_name"),
            _parameter("amount", "number"),
            _parameter("adjustment", "number"),
            _parameter("damage_type"),
            _parameter("critical_hit", "boolean"),
            _optional("witness_state_ids", "array", None, items={"type": "string"}),
        ],
        [
            _require(
                "actor_state.form_attack_in_progress == true",
                "Only the actual named form attack owner can resolve this damage.",
            ),
            _set(
                "_extra_attack_source",
                ("lasting_state.transformation_sources[actor_state.form_current_source_id]"),
            ),
            _lookup(
                "definitions.beast_forms", "_extra_attack_source.form_id", "_extra_attack_body"
            ),
            _set("_extra_attack", "_extra_attack_body.attacks[attack_name]"),
            _require(
                "_extra_attack.extra_damage_expression != ''",
                "The source attack has no additional damage type.",
            ),
            _set("_form_extra_damage_roll", None),
            _set("_form_extra_damage_amount", 0),
            _when(
                "_extra_attack.extra_damage_expression == 'fixed-1'",
                [_set("_form_extra_damage_amount", 1)],
                [
                    {
                        "dice": {
                            "expression": "{_extra_attack.extra_damage_expression}",
                            "critical": "critical_hit",
                        },
                        "store_as": "_form_extra_damage_roll",
                    },
                    _set("_form_extra_damage_amount", "_form_extra_damage_roll.total"),
                ],
            ),
            _when(
                "actor_state.spell_ray_of_enfeeblement == true",
                [
                    _call(
                        "modify_srd_actor_raw_damage_roll",
                        {
                            "actor_state": "{_actor_state_id}",
                            "lasting_state": "{_lasting_state_id}",
                            "amount": "{_form_extra_damage_amount}",
                        },
                        {"_form_extra_damage_amount": "_ray_raw_damage"},
                    ),
                ],
            ),
            _set(
                "_form_extra_damage_amount",
                ("max(0, _form_extra_damage_amount + _extra_attack.extra_damage_modifier)"),
            ),
            _set("_form_primary_damage_amount", "max(0, amount + adjustment)"),
            _call(
                "apply_srd_qualifying_composite_damage"
                if qualified
                else "apply_srd_composite_damage",
                {
                    "target": "{_target_state_id}",
                    "component_1_amount": "{_form_primary_damage_amount}",
                    "component_1_damage_type": "{damage_type}",
                    "component_2_amount": "{_form_extra_damage_amount}",
                    "component_2_damage_type": "{_extra_attack.extra_damage_type}",
                    "critical_hit": "{critical_hit}",
                    "source_state_id": "{_actor_state_id}",
                    "witness_state_ids": "{witness_state_ids}",
                },
                [
                    "_effective_damage",
                    "_hp_before",
                    "_hp_after",
                    "_is_alive_after",
                    "_temp_hp_absorbed",
                    "_incoming_damage",
                    "_concentration_save_dc",
                    "_concentration_save_outcome",
                ],
            ),
            _set("_hp_lost", "max(0, _hp_before - _hp_after)"),
        ],
    )


def _body_light_tool():
    "Derive natural body light from the newest active source-qualified transformation."
    return _tool(
        ("Read the active body's natural light independently of magical spell illumination."),
        [_entity("actor_state"), _entity("lasting_state")],
        [
            _set("_form_bright_light_feet", 0),
            _set("_form_dim_light_feet", 0),
            _when(
                "actor_state.has_transformations == true",
                [
                    _set("_body_light_source", None),
                    _set("_body_light_ids", "actor_state.transformation_source_ids"),
                    _loop(
                        "_body_light_ids",
                        "_body_light_id",
                        [
                            _set(
                                "_body_light_candidate",
                                "lasting_state.transformation_sources[_body_light_id]",
                            ),
                            _when(
                                "_body_light_candidate.installed == true",
                                [
                                    _call(
                                        "check_srd_transformation_source",
                                        {
                                            "target_state": "{_actor_state_id}",
                                            "caster_state": (
                                                "{_body_light_candidate.caster_state_id}"
                                            ),
                                            "lasting_state": "{_lasting_state_id}",
                                            "source_id": "{_body_light_id}",
                                        },
                                        ["_form_source_active", "_form_source"],
                                    ),
                                    _when(
                                        (
                                            "_form_source_active == true and "
                                            "(_body_light_source == null or "
                                            "_form_source.serial > "
                                            "_body_light_source.serial)"
                                        ),
                                        [
                                            _set("_body_light_source", "_form_source"),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                    _when(
                        ("_body_light_source != null and _body_light_source.profile == 'beast'"),
                        [
                            _lookup(
                                "definitions.beast_forms",
                                "_body_light_source.form_id",
                                "_body_light_profile",
                            ),
                            _set(
                                "_form_bright_light_feet", "_body_light_profile.bright_light_feet"
                            ),
                            _set("_form_dim_light_feet", "_body_light_profile.dim_light_feet"),
                        ],
                    ),
                ],
            ),
        ],
    )


def _compose_body_light(tools, changed):
    "Use the existing scene and light barriers for natural transformed-body illumination."
    if "resolve_srd_information_light" not in tools:
        return
    light = deepcopy(
        changed.get("resolve_srd_information_light", tools["resolve_srd_information_light"])
    )
    light["effects"] = [
        effect for effect in light["effects"] if "_form_lighting_targets" not in str(effect)
    ]
    light["effects"].append(
        _when(
            ("ambient != 'magical_darkness' and state.transformation_target_ids != null"),
            [
                _set("_form_lighting_targets", "state.transformation_target_ids"),
                _loop(
                    "_form_lighting_targets",
                    "_form_lighting_actor_id",
                    [
                        _call(
                            "check_srd_form_light",
                            {"actor_state": "{_form_lighting_actor_id}", "lasting_state": "state"},
                            ["_form_bright_light_feet", "_form_dim_light_feet"],
                        ),
                        _when(
                            "_form_dim_light_feet > 0",
                            [
                                _call(
                                    "srd_information_scene_entity",
                                    {"subject": "{_form_lighting_actor_id}"},
                                    ["_info_position", "_info_scene_entity"],
                                ),
                                _set("_form_light_origin", "_info_position"),
                                _set(
                                    "_form_light_same_plane",
                                    "_info_scene_entity.metadata.plane == plane",
                                ),
                                _call(
                                    "measure_srd_information_distance",
                                    {"start": "{_form_light_origin}", "end": "{point}"},
                                    ["_scene_distance"],
                                ),
                                _set("_form_light_distance", "_scene_distance"),
                                _call(
                                    "srd_information_barriers",
                                    {
                                        "start": "{_form_light_origin}",
                                        "end": "{point}",
                                        "rule": "light",
                                    },
                                    ["_information_blocked"],
                                ),
                                _when(
                                    (
                                        "_form_light_same_plane == true and "
                                        "_information_blocked != true"
                                    ),
                                    [
                                        _when(
                                            ("_form_light_distance <= _form_bright_light_feet"),
                                            [_set("_information_light", "'bright'")],
                                        ),
                                        _when(
                                            (
                                                "_information_light == 'darkness' and "
                                                ""
                                                ""
                                                "_form_light_distance <= "
                                                "_form_dim_light_feet"
                                            ),
                                            [_set("_information_light", "'dim'")],
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        )
    )
    changed["resolve_srd_information_light"] = _tool_scene(
        light["description"], light["parameters"], light["effects"]
    )


def _compose_respiration(tools, changed):
    "Deepen the existing respiration owner with actual body capacity and native breathing."
    refresh = deepcopy(tools["refresh_srd_respiration"])
    if "resolve_srd_respiration_interval" in str(refresh["effects"]):
        refresh = deepcopy(tools["resolve_srd_respiration_interval"])
    if not any(p["name"] == "form_at_tick" for p in refresh["parameters"]):
        refresh["parameters"].extend(
            [
                _optional("form_at_tick", "number", -1, model_hidden=True),
                _optional("form_native_air", "boolean", None, model_hidden=True),
                _optional("form_native_water", "boolean", None, model_hidden=True),
                _optional("form_capacity_ticks", "integer", None, model_hidden=True),
            ]
        )
        water_snapshot = ["_sense_spell_water_breathing", "_sense_spell_water_until"]
        refresh["effects"][0]["export_context"].update({name: name for name in water_snapshot})
        prefix = [
            _when(
                "target_state.respiration_base_capacity_ticks == null",
                [
                    _set(
                        "target_state.respiration_base_capacity_ticks",
                        "target_state.respiration_capacity_ticks",
                    )
                ],
            ),
            _set("_form_respiration_native_air", "target_state.respiration_native_air"),
            _set("_form_respiration_native_water", "target_state.respiration_native_water"),
            _set("_form_respiration_new_capacity", "target_state.respiration_capacity_ticks"),
            _when(
                "target_state.respiration_base_capacity_ticks != null",
                [
                    _set(
                        "_form_respiration_new_capacity",
                        "target_state.respiration_base_capacity_ticks",
                    ),
                ],
            ),
            _when(
                "target_state.form_respiration_native_air != null",
                [
                    _set(
                        "_form_respiration_native_air", "target_state.form_respiration_native_air"
                    ),
                ],
            ),
            _when(
                "target_state.form_respiration_native_water != null",
                [
                    _set(
                        "_form_respiration_native_water",
                        "target_state.form_respiration_native_water",
                    ),
                ],
            ),
            _when(
                "target_state.form_respiration_capacity_ticks != null",
                [
                    _set(
                        "_form_respiration_new_capacity",
                        "target_state.form_respiration_capacity_ticks",
                    ),
                ],
            ),
            _when(
                "form_native_air != null", [_set("_form_respiration_native_air", "form_native_air")]
            ),
            _when(
                "form_native_water != null",
                [_set("_form_respiration_native_water", "form_native_water")],
            ),
            _when(
                "form_capacity_ticks != null",
                [_set("_form_respiration_new_capacity", "form_capacity_ticks")],
            ),
            _set(
                "_form_respiration_spent",
                (
                    "max(0, target_state.respiration_capacity_ticks - "
                    "target_state.respiration_breath_ticks)"
                ),
            ),
            _set("target_state.respiration_capacity_ticks", "_form_respiration_new_capacity"),
            _set(
                "target_state.respiration_breath_ticks",
                "max(0, _form_respiration_new_capacity - _form_respiration_spent)",
            ),
        ]
        refresh["effects"][1:1] = prefix
        for effects in _walk_lists(refresh["effects"]):
            for effect in effects:
                if effect.get("set", {}).get("field") == "_respiration_now":
                    continue
                for key in ("condition",):
                    if key in effect:
                        effect[key] = (
                            effect[key]
                            .replace(
                                "target_state.respiration_native_air",
                                "_form_respiration_native_air",
                            )
                            .replace(
                                "target_state.respiration_native_water",
                                "_form_respiration_native_water",
                            )
                        )
                if (
                    effect.get("set", {}).get("field", "").startswith("_respiration_")
                    or effect.get("set", {}).get("field") == "target_state.respiration_water_until"
                ):
                    value = effect["set"]["value"]
                    if isinstance(value, str):
                        effect["set"]["value"] = (
                            value.replace(
                                "target_state.respiration_native_air",
                                "_form_respiration_native_air",
                            )
                            .replace(
                                "target_state.respiration_native_water",
                                "_form_respiration_native_water",
                            )
                            .replace("_sense_water_breathing", "_sense_spell_water_breathing")
                            .replace("_sense_water_until", "_sense_spell_water_until")
                        )
        now_index = next(
            i
            for i, e in enumerate(refresh["effects"])
            if e.get("set", {}).get("field") == "_respiration_elapsed"
        )
        refresh["effects"].insert(
            now_index,
            _when(
                "form_at_tick >= 0",
                [
                    _require(
                        (
                            "form_at_tick >= target_state.respiration_tick and "
                            "form_at_tick <= _respiration_now"
                        ),
                        ("A form respiration boundary must come from the actual elapsed clock."),
                    ),
                    _set("_respiration_now", "form_at_tick"),
                ],
            ),
        )
    changed["resolve_srd_respiration_interval"] = refresh
    sync = deepcopy(refresh)
    sync["effects"] = [
        e
        for e in sync["effects"]
        if not e.get("condition", "").startswith("_respiration_exhaustion_levels > 0")
    ]
    elapsed_index = next(
        i
        for i, e in enumerate(sync["effects"])
        if e.get("set", {}).get("field") == "_respiration_elapsed"
    )
    sync["effects"].insert(
        elapsed_index + 1,
        _require(
            "_respiration_elapsed == 0",
            "Settle actual elapsed respiration before changing the source body.",
        ),
    )
    changed["sync_srd_transformation_respiration"] = sync
    changed["refresh_srd_respiration"] = _tool(
        (
            "Settle actual elapsed respiration and source body boundaries, then apply "
            ""
            ""
            ""
            ""
            "the "
            "real turn consequence."
        ),
        deepcopy(refresh["parameters"]),
        [
            _when(
                "target_state.has_transformations == true",
                [
                    _call(
                        "settle_srd_transformation_respiration",
                        {"target_state": "{_target_state_id}", "lasting_state": "state"},
                    ),
                    _call(
                        "project_srd_transformation",
                        {"target_state": "{_target_state_id}", "lasting_state": "state"},
                    ),
                ],
            ),
            _call(
                "resolve_srd_respiration_interval",
                {
                    "target_state": "{_target_state_id}",
                    "end_turn": "{end_turn}",
                    "free_time": "{free_time}",
                    "form_at_tick": "{form_at_tick}",
                    "form_native_air": "{form_native_air}",
                    "form_native_water": "{form_native_water}",
                    "form_capacity_ticks": "{form_capacity_ticks}",
                },
                ["_respiration_can_breathe"],
            ),
        ],
    )
    environment_name = (
        "set_srd_respiration_node" if "set_srd_respiration_node" in tools else "set_srd_respiration"
    )
    environment = deepcopy(changed.get(environment_name, tools[environment_name]))
    for effects in _walk_lists(environment["effects"]):
        for index, effect in enumerate(list(effects)):
            if effect.get("set", {}).get(
                "field"
            ) == "target_state.respiration_capacity_ticks" and not any(
                e.get("set", {}).get("field") == "target_state.respiration_base_capacity_ticks"
                for e in effects
            ):
                effects.insert(
                    index + 1,
                    _set(
                        "target_state.respiration_base_capacity_ticks",
                        "target_state.respiration_capacity_ticks",
                    ),
                )
    changed[environment_name] = environment
    changed["settle_srd_transformation_respiration"] = _respiration_interval_tool()
    for name, actor_name in (
        ("install_srd_transformation_source", "target_state"),
        ("end_srd_transformation_source", "target_state"),
        ("change_srd_transformation_source", "target_state"),
    ):
        tool = changed[name]
        tool["effects"].insert(
            0,
            _when(
                actor_name + ".respiration_medium != null",
                [
                    _call(
                        "settle_srd_transformation_respiration",
                        {
                            "target_state": "{_" + actor_name + "_id}",
                            "lasting_state": "{_lasting_state_id}",
                        },
                    ),
                ],
            ),
        )


def _compose_equipment(tools, changed):
    "Use actual form statistics at the existing armor owner and preserve worn item identity."
    for name in ("resolve_srd_equipment", "resolve_srd_npc_equipment"):
        tool = deepcopy(tools[name])
        effects = tool["effects"]
        if not any(e.get("set", {}).get("field") == "_equipment_form_statistics" for e in effects):
            effects[:0] = [
                _set("_equipment_form_statistics", "actor_state.form_statistics_replaced == true"),
                _set(
                    "_equipment_ability_scores",
                    "actor_state.equipment_profile"
                    if name.endswith("npc_equipment")
                    else "actor.data.abilities",
                ),
                _when(
                    "_equipment_form_statistics == true",
                    [_set("_equipment_ability_scores", "actor_state.form_ability_scores")],
                ),
            ]
            for rows in list(_walk_lists(effects)):
                for effect in rows:
                    setting = effect.get("set", {})
                    value = setting.get("value")
                    if (
                        isinstance(value, str)
                        and setting.get("field") != "_equipment_ability_scores"
                    ):
                        for prefix in ("actor.data.abilities.", "actor_state.equipment_profile."):
                            for ability in (
                                "strength",
                                "dexterity",
                                "constitution",
                                "wisdom",
                                "charisma",
                            ):
                                value = value.replace(
                                    prefix + ability, "_equipment_ability_scores." + ability
                                )
                        setting["value"] = value
                    condition = effect.get("condition")
                    if condition and "_equipment_item.strength_required" in condition:
                        effect["condition"] = (
                            "_equipment_ability_scores.strength < _equipment_item.strength_required"
                        )
                    if (
                        setting.get("field")
                        in ("_equipment_monk", "_equipment_barbarian", "_equipment_draconic")
                        and value is True
                    ):
                        setting["value"] = "_equipment_form_statistics != true"
                    if condition == "'defense' in _style_selected":
                        effect["condition"] += " and _equipment_form_statistics != true"
            # The NPC's old natural armor belongs to its original body.
            if name.endswith("npc_equipment"):
                for i, effect in enumerate(effects):
                    if effect.get("set", {}).get("value") == (
                        "max(_equipment_ac, actor_state.equipment_profile.unarmored_ac)"
                    ):
                        effects[i] = _when("_equipment_form_statistics != true", [effect])
                initial = next(
                    e
                    for e in effects
                    if e.get("set", {}).get("value") == "actor_state.equipment_profile.unarmored_ac"
                )
                i = effects.index(initial) + 1
                effects.insert(
                    i,
                    _when(
                        "_equipment_form_statistics == true",
                        [_set("_equipment_ac", "actor_state.form_natural_armor_class")],
                    ),
                )
            shield_index = next(
                i
                for i, e in enumerate(effects)
                if e.get("condition") == "actor_state.worn_shield != null"
            )
            effects.insert(
                shield_index,
                _when(
                    "_equipment_form_statistics == true",
                    [
                        _set(
                            "_equipment_ac",
                            "max(_equipment_ac, actor_state.form_natural_armor_class)",
                        )
                    ],
                ),
            )
            effects.append(
                _when(
                    "_equipment_form_statistics == true",
                    [
                        _set(
                            "_equipment_speed",
                            (
                                "max(0, actor_state.form_natural_walk_speed - "
                                "_equipment_speed_penalty)"
                            ),
                        ),
                        _set("_equipment_climb_speed", "actor_state.form_natural_climb_speed"),
                        _set("_equipment_swim_speed", "actor_state.form_natural_swim_speed"),
                    ],
                )
            )
        changed[name] = tool

    for name, tool in tools.items():
        if not any(
            e.get("set", {}).get("field") == "actor_state.worn_armor"
            for rows in _walk_lists(tool.get("effects", []))
            for e in rows
        ):
            continue
        if not any(
            e.get("call") == "resolve_srd_equipment"
            for rows in _walk_lists(tool.get("effects", []))
            for e in rows
        ):
            continue
        tool = deepcopy(tool)
        if not any(
            "form_equipment_melded" in e.get("require", {}).get("condition", "")
            for e in tool["effects"]
        ):
            tool["effects"].insert(
                0,
                _require(
                    (
                        "actor_state.form_equipment_melded != true and "
                        "actor_state.form_no_objects != true"
                    ),
                    "Melded or cloud equipment cannot be donned or removed.",
                ),
            )
        if not any(p["name"] == "lasting_state" for p in tool["parameters"]):
            tool["parameters"].append(
                _optional("lasting_state", default="state", resolve="entity", model_hidden=True)
            )
        _owned_hook(
            tool,
            "actor_state.has_transformations == true",
            "project_srd_transformation",
            {"target_state": "{_actor_state_id}", "lasting_state": "{_lasting_state_id}"},
        )
        changed[name] = tool

    defense = deepcopy(tools["validate_srd_actor_defense_target"])
    for effects in list(_walk_lists(defense["effects"])):
        for i, effect in enumerate(effects):
            if effect.get("require", {}).get(
                "condition"
            ) == "_defense_target_dexterity != null" and not any(
                e.get("condition") == "target_state.form_statistics_replaced == true"
                for e in effects
            ):
                effects.insert(
                    i,
                    _when(
                        "target_state.form_statistics_replaced == true",
                        [
                            _set(
                                "_defense_target_dexterity",
                                "target_state.form_ability_scores.dexterity",
                            ),
                            _set(
                                "_defense_target_shield_trained",
                                (
                                    "_defense_target_shield_trained == true and "
                                    "target_state.form_retain_proficiencies == true"
                                ),
                            ),
                        ],
                    ),
                )
                break
    changed["validate_srd_actor_defense_target"] = defense


def _compose_consumers(tools, changed):
    def edit(name):
        if name not in changed:
            changed[name] = deepcopy(tools[name])
        return changed[name]

    # Ordinary damage inherits projected resistances without an inactive helper call.
    for name in ("apply_srd_damage", "apply_srd_composite_damage"):
        damage = edit(name)
        damage["effects"] = [
            e
            for e in damage["effects"]
            if e.get("condition") != "target.has_transformations == true"
        ]
        lifecycle = next(
            e
            for e in damage["effects"]
            if e.get("condition", "").startswith("target.concentration_spell_id != null or")
        )
        if "target.has_transformations == true" not in lifecycle["condition"]:
            lifecycle["condition"] += " or target.has_transformations == true"
        lifecycle["on_true"] = [
            e
            for e in lifecycle["on_true"]
            if e.get("condition") != "target.has_transformations == true"
        ]
        lifecycle["on_true"].append(
            _when(
                "target.has_transformations == true",
                [
                    _call(
                        "project_srd_transformation",
                        {"target_state": "{target}", "lasting_state": "{_lasting_state_id}"},
                    )
                ],
            )
        )

    temp = edit("grant_temporary_hit_points")
    for effects in _walk_lists(temp["effects"]):
        for i, e in enumerate(list(effects)):
            if e.get("set", {}).get("field") == "target.temp_hp" and not any(
                x.get("set", {}).get("field") == "target.temp_hp_transformation_source_id"
                for x in effects
            ):
                effects.insert(i + 1, _set("target.temp_hp_transformation_source_id", None))

    immunity = edit("check_srd_target_condition_immunity")
    immunity["effects"] = [
        e
        for e in immunity["effects"]
        if e.get("condition") != "target_state.form_prone_immune == true and condition == 'prone'"
    ]
    immunity["effects"].append(
        _when(
            "target_state.form_prone_immune == true and condition == 'prone'",
            [_set("_source_condition_immune", True)],
        )
    )
    condition = edit("apply_srd_condition")
    condition["effects"][0]["condition"] = (
        "target.has_timed_actor_defenses == true or target.has_actor_spell_modifiers "
        "== true or target.has_transformations == true"
    )

    ac = edit("resolve_srd_effective_armor_class")
    ac["effects"] = [
        e
        for e in ac["effects"]
        if e.get("condition") != "target_state.form_statistics_replaced == true"
    ]
    ac["effects"].insert(
        1,
        _when(
            "target_state.form_statistics_replaced == true",
            [_set("_effective_armor_class", "target_state.armor_class")],
        ),
    )

    d20 = edit("d20_test")
    for e in d20["effects"]:
        if e.get("condition") == "actor != null" and any(
            x.get("set", {}).get("field") == "_ability_modifier" for x in e.get("on_true", [])
        ):
            e["on_true"] = [
                x
                for x in e["on_true"]
                if x.get("condition") != "actor_state.form_statistics_replaced == true"
            ]
            e["on_true"].append(
                _when(
                    "actor_state.form_statistics_replaced == true",
                    [
                        _set(
                            "_ability_modifier",
                            "(actor_state.form_ability_scores[ability] - 10) // 2",
                        )
                    ],
                )
            )
        if e.get("set", {}).get("field") == "_condition_advantage":
            e["set"]["value"] = (
                "actor_state.form_physical_save_advantage == true and kind == "
                "'saving_throw' and (ability == 'strength' or ability == 'dexterity' "
                "or ability == 'constitution')"
            )
    # Merge the two original participant-only branches, keeping ordinary save operation counts flat.
    participant = next(
        e
        for e in d20["effects"]
        if e.get("condition") == "actor != null"
        and any(x.get("set", {}).get("field") == "_ability_modifier" for x in e["on_true"])
    )
    bonus_default = next(
        e for e in d20["effects"] if e.get("set", {}).get("field") == "_proficiency_bonus"
    )
    d20["effects"].remove(bonus_default)
    d20["effects"].insert(d20["effects"].index(participant), bonus_default)
    participant["on_true"] = [
        e for e in participant["on_true"] if e.get("set", {}).get("field") != "_proficiency_bonus"
    ]
    participant["on_true"].append(_set("_proficiency_bonus", "2 + ((actor.data.level - 1) // 4)"))
    d20["effects"] = [
        e
        for e in d20["effects"]
        if e is participant
        or not (
            e.get("condition") == "actor != null"
            and any(
                x.get("set", {}).get("field") == "_proficiency_bonus" for x in e.get("on_true", [])
            )
        )
    ]
    # Replaced proficiency uses the form's actual skill/save bonus, only inside the PC path.
    d20["effects"] = [
        e
        for e in d20["effects"]
        if e.get("condition") != "actor != null and actor_state.form_statistics_replaced == true"
    ]
    index = (
        next(
            i for i, e in enumerate(d20["effects"]) if e.get("set", {}).get("field") == "_modifier"
        )
        + 1
    )
    d20["effects"].insert(
        index,
        _when(
            "actor != null and actor_state.form_statistics_replaced == true",
            [
                _set(
                    "_d20_form_source",
                    ("lasting_state.transformation_sources[actor_state.form_current_source_id]"),
                ),
                _lookup("definitions.beast_forms", "_d20_form_source.form_id", "_d20_beast"),
                _set("_form_test_modifier", "_ability_modifier"),
                _when(
                    "kind == 'saving_throw'",
                    [_set("_form_test_modifier", "actor_state.saving_throw_modifiers[ability]")],
                ),
                _when(
                    ("kind == 'ability_check' and skill != null and skill in _d20_beast.skills"),
                    [
                        _set(
                            "_form_test_modifier",
                            (
                                "_d20_beast.skills[skill] - "
                                "((_d20_beast.ability_scores[ability] - 10) // 2) + "
                                "_ability_modifier"
                            ),
                        )
                    ],
                ),
                _when(
                    "actor_state.form_retain_proficiencies == true",
                    [
                        _set(
                            "_form_test_modifier",
                            ("max(_form_test_modifier, _ability_modifier + _proficiency_modifier)"),
                        )
                    ],
                ),
                _set("_modifier", "_form_test_modifier + circumstantial_modifier"),
            ],
        ),
    )
    if not any(p["name"] == "lasting_state" for p in d20["parameters"]):
        d20["parameters"].append(
            _optional("lasting_state", default="state", resolve="entity", model_hidden=True)
        )

    casting = edit("cast_spell")
    casting["pre_conditions"].append(
        {
            "condition": "caster_state.form_can_cast != false",
            "error": "This actual transformation cannot cast spells.",
        }
    ) if not any(
        "form_can_cast" in p.get("condition", "") for p in casting["pre_conditions"]
    ) else None
    for name in (
        "resolve_combat_attack",
        "resolve_combat_attack_followup",
        "resolve_opportunity_attack",
        "resolve_srd_unarmed_effect",
    ):
        if name in tools:
            tool = edit(name)
            if not any(
                "form_statistics_replaced" in p.get("condition", "") for p in tool["pre_conditions"]
            ):
                tool["pre_conditions"].append(
                    {
                        "condition": (
                            "actor_state.form_statistics_replaced != true and "
                            "actor_state.form_cloud != true"
                        ),
                        "error": (
                            "Use the actual transformed form's source-defined attacks and anatomy."
                        ),
                    }
                )
    for name in ("use_srd_action_surge", "use_srd_second_wind"):
        tool = edit(name)
        if not any(
            "form_statistics_replaced" in p.get("condition", "") for p in tool["pre_conditions"]
        ):
            tool["pre_conditions"].append(
                {
                    "condition": "actor_state.form_statistics_replaced != true",
                    "error": "The replaced form's statistics do not retain this class feature.",
                }
            )
    npc = edit("resolve_adjudicated_creature_attack")
    for effect in npc["effects"]:
        guard = effect.get("require", {})
        if guard.get("condition") == "actor.has_actor_weapon_modifiers != true":
            guard["condition"] += " or actor.altered_natural_attack_in_progress == true"
    expressions = {
        attack["damage_expression"]
        for form in build_index()["forms"].values()
        if form["execution_supported"]
        for attack in form["attacks"].values()
    }
    damage_parameter = next(p for p in npc["parameters"] if p["name"] == "damage_expression")
    damage_parameter["enum_values"].extend(
        sorted(expressions - set(damage_parameter["enum_values"]))
    )
    npc["effects"] = [
        e
        for e in npc["effects"]
        if not (
            e.get("condition") == "actor.form_attack_in_progress == true"
            and "_form_extra_damage_roll" in str(e)
        )
    ]
    npc["effects"].insert(
        0,
        _when(
            "actor.form_attack_in_progress == true",
            [
                _set("_form_extra_damage_roll", None),
                _set("_form_extra_damage_amount", 0),
                _set("_form_primary_damage_amount", 0),
                _set("_form_concentration_save_dc", None),
            ],
        ),
    )
    if not any(e.get("set", {}).get("field") == "_damage_roll" for e in npc["effects"]):
        # Fixed damage and misses have no dice receipt to export.
        npc["effects"].insert(0, _set("_damage_roll", None))
    for pre in npc["pre_conditions"]:
        if pre["condition"] == "actor.creature_kind == 'monster'":
            pre["condition"] = (
                "actor.creature_kind == 'monster' or actor.form_attack_in_progress == true"
            )
    if not any(
        "form_attack_in_progress" in e.get("require", {}).get("condition", "")
        for e in npc["effects"]
    ):
        npc["effects"].insert(
            0,
            _require(
                (
                    "(actor.form_statistics_replaced != true and actor.form_cloud != "
                    "true) or actor.form_attack_in_progress == true"
                ),
                ("Use the admitted named form attack; supplied numbers cannot bypass its source."),
            ),
        )

    # Keep one composite Damage boundary for the attack, including Concentration and hit riders.
    for effects in list(_walk_lists(npc["effects"])):
        for index, effect in enumerate(list(effects)):
            if effect.get("condition") == "actor.form_attack_has_extra_damage == true":
                effects[index] = effect["on_false"][0]
    for effects in list(_walk_lists(npc["effects"])):
        for index, effect in enumerate(list(effects)):
            if effect.get("call") not in {"apply_srd_damage", "apply_srd_qualifying_damage"}:
                continue
            params = effect["params"]
            replacement = _call(
                "apply_srd_form_attack_damage",
                {
                    "actor_state": "{_actor_id}",
                    "target_state": "{_target_id}",
                    "lasting_state": "state",
                    "attack_name": "{attack_name}",
                    "amount": params["amount"],
                    "adjustment": params["adjustment"],
                    "damage_type": params["damage_type"],
                    "critical_hit": params.get("critical_hit", False),
                    "witness_state_ids": params.get("witness_state_ids", None),
                },
                effect.get("export_context", {})
                | {
                    "_form_extra_damage_roll": "_form_extra_damage_roll",
                    "_form_extra_damage_amount": "_form_extra_damage_amount",
                    "_form_primary_damage_amount": "_form_primary_damage_amount",
                    "_form_concentration_save_dc": "_concentration_save_dc",
                },
            )
            effects[index] = _when(
                "actor.form_attack_has_extra_damage == true", [replacement], [effect]
            )

    unarmed = edit("resolve_srd_unarmed_effect")
    if not any(p["name"] == "use_altered_natural_weapon" for p in unarmed["parameters"]):
        unarmed["parameters"].append(_optional("use_altered_natural_weapon", "boolean", False))
    unarmed["effects"] = [
        e
        for e in unarmed["effects"]
        if e.get("set", {}).get("field")
        not in (
            "_natural_actor_ref",
            "_natural_attack_ability",
            "_natural_damage_type",
            "_natural_proficiency_bonus",
        )
        and e.get("condition") != "use_altered_natural_weapon == true"
    ]
    index = (
        next(
            i
            for i, e in enumerate(unarmed["effects"])
            if e.get("set", {}).get("field") == "_unarmed_ability_modifier"
            and "actor.data.abilities" in str(e)
        )
        + 1
    )
    unarmed["effects"][index:index] = [
        _set("_natural_actor_ref", "_actor_id"),
        _set("_natural_attack_ability", "attack_ability"),
        _set("_natural_damage_type", "unarmed_damage_type"),
        _set("_natural_proficiency_bonus", "2 + ((actor.data.level - 1) // 4)"),
        _when(
            "use_altered_natural_weapon == true",
            [
                _require(
                    (
                        "effect == 'damage' and use_martial_arts_die != true and "
                        "actor_state.form_current_source_id != null"
                    ),
                    ("Choose the actual Alter Self growth for a damaging Unarmed Strike."),
                ),
                _require(
                    (
                        "actor_state.alter_self_option == 'claws' or "
                        "actor_state.alter_self_option == 'fangs' or "
                        "actor_state.alter_self_option == 'horns' or "
                        "actor_state.alter_self_option == 'hooves'"
                    ),
                    "The active Alter Self option does not grant a natural weapon.",
                ),
                _set(
                    "_natural_source",
                    ("lasting_state.transformation_sources[actor_state.form_current_source_id]"),
                ),
                _set("_unarmed_ability_modifier", "_natural_source.casting_modifier"),
                _set("_natural_attack_ability", "_natural_source.casting_ability"),
                _set("_natural_actor_ref", None),
                _set("_natural_damage_type", "'piercing'"),
                _when(
                    "actor_state.alter_self_option == 'claws'",
                    [_set("_natural_damage_type", "'slashing'")],
                ),
                _when(
                    "actor_state.alter_self_option == 'hooves'",
                    [_set("_natural_damage_type", "'bludgeoning'")],
                ),
                _set("_unarmed_damage_type", "_natural_damage_type"),
            ],
        ),
    ]
    for effects in list(_walk_lists(unarmed["effects"])):
        effects[:] = [
            e
            for e in effects
            if e.get("condition")
            != "_unarmed_success == true and use_altered_natural_weapon == true"
            and not (
                e.get("set", {}).get("field", "").startswith("_actor_call_expression_")
                and e.get("set", {}).get("value") == "2 + ((actor.data.level - 1) // 4)"
            )
        ]
        for i, e in enumerate(list(effects)):
            if e.get("call") == "d20_test" and e.get("params", {}).get("kind") == "attack_roll":
                e["params"].update(
                    actor="{_natural_actor_ref}",
                    ability="{_natural_attack_ability}",
                    ability_modifier="{_unarmed_ability_modifier}",
                    proficiency_bonus="{_natural_proficiency_bonus}",
                )
            if e.get("call") == "apply_srd_damage":
                e["params"]["damage_type"] = "{_natural_damage_type}"
            if e.get("set", {}).get(
                "field"
            ) == "_actor_call_expression_1" and "_unarmed_base_damage" in str(e):
                effects.insert(
                    i,
                    _when(
                        ("_unarmed_success == true and use_altered_natural_weapon == true"),
                        [
                            {
                                "dice": {"expression": "1d6", "critical": "_unarmed_critical_hit"},
                                "store_as": "_unarmed_damage_roll",
                            },
                            _set("_unarmed_base_damage", "_unarmed_damage_roll.total"),
                        ],
                    ),
                )
    for name in ("resolve_unarmed_strike",):
        wrapper = edit(name)
        if not any(p["name"] == "use_altered_natural_weapon" for p in wrapper["parameters"]):
            wrapper["parameters"].append(_optional("use_altered_natural_weapon", "boolean", False))
        for e in wrapper["effects"]:
            if e.get("call") == "resolve_srd_unarmed_effect":
                e["params"]["use_altered_natural_weapon"] = "{use_altered_natural_weapon}"

    for name in ("record_srd_control_held_items", "drop_srd_control_held_items"):
        tool = edit(name)
        if not any(
            "form_no_objects" in e.get("require", {}).get("condition", "") for e in tool["effects"]
        ):
            tool["effects"].insert(
                0,
                _require(
                    "target_state.form_no_objects != true",
                    ("Objects cannot be manipulated, dropped, or held while this form melds them."),
                ),
            )
    for name in ("derive_srd_inventory_weapon", "set_srd_carried_inventory"):
        if name in tools:
            tool = edit(name)
            if not any(
                "form_equipment_melded" in e.get("require", {}).get("condition", "")
                for e in tool["effects"]
            ):
                tool["effects"].insert(
                    0,
                    _require(
                        "actor_state.form_equipment_melded != true",
                        ("Melded equipment cannot be used or changed during the transformation."),
                    ),
                )

    language = edit("resolve_srd_spell_language")
    if not any(
        "form_can_speak" in e.get("require", {}).get("condition", "") for e in language["effects"]
    ):
        language["effects"].insert(
            0,
            _require(
                "speaker_state.form_can_speak != false",
                "The actual transformed speaker cannot talk.",
            ),
        )
    senses = edit("resolve_srd_timed_senses")
    senses["effects"] = [
        e
        for e in senses["effects"]
        if e.get("condition") != "target_state.form_can_breathe_water == true"
        and e.get("set", {}).get("field")
        not in ("_sense_spell_water_breathing", "_sense_spell_water_until")
    ]
    senses["effects"].append(_set("_sense_spell_water_breathing", "_sense_water_breathing"))
    senses["effects"].append(_set("_sense_spell_water_until", "_sense_water_until"))
    senses["effects"].append(
        _when(
            "target_state.form_can_breathe_water == true",
            [
                _set("_sense_water_breathing", True),
                _set(
                    "_sense_water_until", "max(_sense_water_until, target_state.form_water_until)"
                ),
            ],
        )
    )

    action = edit("spend_srd_action")
    if not any(
        "active_actor_form_wind_only_actions" in e.get("require", {}).get("condition", "")
        for e in action["effects"]
    ):
        action["effects"].insert(
            0,
            _require(
                (
                    "combat.active_actor_form_wind_only_actions != true or "
                    "control_action_kind == 'dash' or "
                    "combat.transformation_magic_allowed "
                    "== true"
                ),
                "This cloud form permits only Dash or its own reversion Magic action.",
            ),
        )
    bonus = edit("consume_srd_bonus_action")
    bonus["pre_conditions"] = [
        p for p in bonus["pre_conditions"] if "form_cloud" not in p.get("condition", "")
    ]
    if not any("form_wind_only_actions" in p.get("condition", "") for p in bonus["pre_conditions"]):
        bonus["pre_conditions"].append(
            {
                "condition": "actor_state.form_wind_only_actions != true",
                "error": "Wind Walk permits only Dash or its own reversion Magic action.",
            }
        )
    turn = edit("begin_combat_turn")
    turn["effects"] = [
        e
        for e in turn["effects"]
        if e.get("set", {}).get("field")
        not in ("combat.active_actor_form_wind_only_actions", "combat.active_actor_form_restricted")
    ]
    turn["effects"].extend(
        [
            _set(
                "combat.active_actor_form_wind_only_actions", "actor.form_wind_only_actions == true"
            ),
            _set(
                "combat.active_actor_form_restricted",
                ("actor.form_statistics_replaced == true and actor.form_can_cast != true"),
            ),
        ]
    )
    _owned_hook(
        turn,
        "actor.has_transformations == true",
        "project_srd_transformation",
        {"target_state": "{_actor_id}", "lasting_state": "state"},
        first=True,
    )

    for name in ("spend_combat_movement", "derive_srd_scene_movement_budget"):
        tool = edit(name)
        arg = "movement_kind" if name == "spend_combat_movement" else "mode"
        if not any(
            "form_cloud" in e.get("require", {}).get("condition", "") for e in tool["effects"]
        ):
            tool["effects"].insert(
                0,
                _require(
                    f"actor_state.form_cloud != true or {arg} == 'fly'",
                    "The cloud form's only movement method is its actual Fly Speed.",
                ),
            )
    # Project cloud resistances into the owning array without adding damage expressions.
    projection = changed["project_srd_transformation"]
    branch = projection["effects"][0]["on_true"]
    idx = next(
        i
        for i, e in enumerate(branch)
        if e.get("set", {}).get("field") == "target_state.form_projection_in_progress"
        and e["set"]["value"] is False
    )
    branch[idx:idx] = [
        _when(
            "target_state.form_physical_resistance == true",
            [
                _set("_cloud_resistances", []),
                _when(
                    "target_state.damage_resistances != null",
                    [_set("_cloud_resistances", "target_state.damage_resistances")],
                ),
                *[
                    _when(
                        f"'{kind}' not in _cloud_resistances",
                        [_push("_cloud_resistances", "'" + kind + "'")],
                    )
                    for kind in ("bludgeoning", "piercing", "slashing")
                ],
                _set("target_state.damage_resistances", "_cloud_resistances"),
            ],
        )
    ]
    prep = edit("prepare_srd_creature")
    abilities = ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma")
    if not any(p["name"] == "strength_score" for p in prep["parameters"]):
        prep["parameters"].extend(
            _optional(a + "_score", "integer", None, minimum=1, maximum=30) for a in abilities
        )
        missing = " and ".join(a + "_score == null" for a in abilities)
        present = " and ".join(a + "_score != null" for a in abilities)
        prep["effects"].insert(
            0,
            _require(
                "(" + missing + ") or (" + present + ")",
                (
                    "Supply all six original stat-block ability scores or leave the "
                    "unknown scores unset."
                ),
            ),
        )
        prep["effects"].append(_set("creature_id.ability_scores", None))
        prep["effects"].append(
            _when(
                "strength_score != null",
                [_set("creature_id.ability_scores", {a: a + "_score" for a in abilities})],
            )
        )


def _casting_dispatch(tools, changed):
    cast = changed.get("cast_spell", deepcopy(tools["cast_spell"]))
    extras = [
        _optional("transformation_form", default=""),
        _optional(
            "transformation_forms",
            "array",
            [],
            items={
                "type": "object",
                "properties": {"target": {"type": "string"}, "form_id": {"type": "string"}},
                "required": ["target", "form_id"],
                "additionalProperties": False,
            },
        ),
        _optional("transformation_option", default="aquatic"),
        _optional("transformation_appearance", default=""),
        _optional("transformation_same_size", "boolean", False),
        _optional("transformation_same_basic_shape", "boolean", False),
        _optional("transformation_seen_before", "boolean", False),
        _optional("transformation_accept_temporary_hp", "boolean", True),
        _optional("transformation_equipment", default="resize"),
    ]
    for parameter in extras:
        if not any(p["name"] == parameter["name"] for p in cast["parameters"]):
            cast["parameters"].append(parameter)
    cast["effects"] = [
        e
        for e in cast["effects"]
        if "_spell_definition.family == 'transformation_spell'" not in e.get("condition", "")
    ]
    branch = _when(
        "_spell_definition.family == 'transformation_spell'",
        [
            _when(
                "_spell_cast_failed != true and _pending_spell_operation_lost != true",
                [
                    _set("_transformation_character_ref", None),
                    _when(
                        "target_character != null",
                        [_set("_transformation_character_ref", "_target_character_id")],
                    ),
                    _set("_transformation_dispatch_targets", []),
                    _when("targets != null", [_set("_transformation_dispatch_targets", "targets")]),
                    _when(
                        (
                            "(_spell_definition.range_kind == 'self' or spell == "
                            "'wind_walk') "
                            "and caster != null"
                        ),
                        [_set("_transformation_character_ref", "_caster_id")],
                    ),
                    _call(
                        "cast_spell_transformation",
                        {
                            "caster_state": "{_caster_state_id}",
                            "lasting_state": "state",
                            "spell": "{spell}",
                            "target_state": "{_target_id}",
                            "target_character": "{_transformation_character_ref}",
                            "targets": "{_transformation_dispatch_targets}",
                            "actual_spell_level": "{_slot_level}",
                            "save_dc": "{_casting_profile.save_dc}",
                            "casting_modifier": "{_casting_profile.modifier}",
                            "casting_ability": "{_casting_profile.ability}",
                            "concentration_save_modifier": (
                                "{_casting_profile.concentration_save_modifier}"
                            ),
                            "form_id": "{transformation_form}",
                            "form_choices": "{transformation_forms}",
                            "option": "{transformation_option}",
                            "appearance": "{transformation_appearance}",
                            "same_size": "{transformation_same_size}",
                            "same_basic_shape": "{transformation_same_basic_shape}",
                            "seen_before": "{transformation_seen_before}",
                            "accept_temporary_hp": "{transformation_accept_temporary_hp}",
                            "equipment_policy": "{transformation_equipment}",
                            "target_distance": "{target_distance}",
                            "targets_max_distance": "{targets_max_distance}",
                            "caster_can_see_target": "{caster_can_see_target}",
                            "caster_can_see_all_targets": "{caster_can_see_all_targets}",
                            "caster_can_touch_target": "{caster_can_touch_target}",
                            "caster_can_touch_all_targets": "{caster_can_touch_all_targets}",
                            "target_willing": "{target_willing}",
                            "all_targets_willing": "{all_targets_willing}",
                            "forego_save_targets": "{_forego_save_targets}",
                        },
                        ["_spell_summary_rows", "_spell_detail_rows"],
                    ),
                ],
            ),
        ],
    )
    index = next(
        i
        for i, e in enumerate(cast["effects"])
        if "_spell_definition.family == 'timed_actor_modifier'" in e.get("condition", "")
    )
    cast["effects"].insert(index, branch)
    changed["cast_spell"] = cast
    outside = deepcopy(cast)
    outside["metadata"] = deepcopy(tools["cast_spell_outside_combat"]["metadata"])
    outside["pre_conditions"] = deepcopy(tools["cast_spell_outside_combat"]["pre_conditions"])
    outside["pre_conditions"].append(
        {
            "condition": "caster_state.form_can_cast != false",
            "error": "The actual transformation cannot cast spells.",
        }
    ) if not any(
        "form_can_cast" in p.get("condition", "") for p in outside["pre_conditions"]
    ) else None
    changed["cast_spell_outside_combat"] = outside


BODY_IMMUNITY_CONDITIONS = ("blinded", "charmed", "deafened", "frightened", "prone")


def _body_condition_immunity_tool():
    "Derive exact body-owned immunity flags while retaining original condition sources."
    effects = []
    for condition in BODY_IMMUNITY_CONDITIONS:
        unowned = "target_state.timed_condition_unowned_" + condition
        effects.extend(
            [
                _set(
                    "target_state.form_body_suppressed_" + condition,
                    f"'{condition}' in target_state.form_body_condition_immunities",
                ),
                _when(
                    f"'{condition}' in target_state.conditions and "
                    f"target_state.timed_condition_{condition} != true and "
                    f"target_state.turn_effect_{condition} != true",
                    [_set(unowned, True)],
                ),
            ]
        )
    effects.extend(
        [
            _set("_body_condition_source_ids", []),
            _when(
                "target_state.timed_condition_source_ids != null",
                [_set("_body_condition_source_ids", "target_state.timed_condition_source_ids")],
            ),
            _loop(
                "_body_condition_source_ids",
                "_body_condition_source_id",
                [
                    _set(
                        "_body_condition_source",
                        "lasting_state.timed_condition_sources[_body_condition_source_id]",
                    ),
                    _when(
                        "_body_condition_source != null",
                        [
                            _set(
                                "lasting_state.timed_condition_sources.{_body_condition_source_id}.body_immunity_suppressed",
                                "_body_condition_source.condition in "
                                "target_state.form_body_condition_immunities",
                            )
                        ],
                    ),
                ],
            ),
            _call(
                "resolve_srd_timed_actor_condition_suppression",
                {
                    "target_state": "{_target_state_id}",
                    "lasting_state": "{_lasting_state_id}",
                },
            ),
            _call(
                "resolve_srd_control_behavior_projection",
                {
                    "target_state": "{_target_state_id}",
                    "lasting_state": "{_lasting_state_id}",
                },
            ),
            _set(
                "target_state.form_body_suppression_active",
                "len(target_state.form_body_condition_immunities) > 0",
            ),
        ]
    )
    return _tool(
        "Suppress the exact active body's condition immunity "
        "without retiring its underlying sources.",
        [_entity("target_state"), _entity("lasting_state")],
        effects,
    )


def _compose_body_condition_immunity(tools, changed):
    "Compose body immunity into shared suppression and preserve linked-control projection tails."
    suppression = deepcopy(tools["resolve_srd_timed_actor_condition_suppression"])
    for effects in _walk_lists(suppression["effects"]):
        for effect in effects:
            assignment = effect.get("set", {})
            value = assignment.get("value")
            if assignment.get("field") == "_suppress_actor_condition" and isinstance(value, str):
                for condition in ("charmed", "frightened"):
                    if f"'{condition}' in " in value and "form_body_suppressed" not in value:
                        assignment["value"] += (
                            " or target_state.form_body_suppressed_" + condition + " == true"
                        )
            if (
                str(assignment.get("field", "")).endswith(".suppressed")
                and isinstance(value, str)
                and "body_immunity_suppressed" not in value
            ):
                assignment["value"] += (
                    " or _actor_suppression_source.body_immunity_suppressed == true"
                )
    suppression["effects"] = [
        e
        for e in suppression["effects"]
        if not (
            e.get("condition") == "target_state.has_transformations == true"
            and "form_condition_was_suppressed" in str(e)
        )
    ]
    native = []
    for condition in ("blinded", "deafened", "prone"):
        flag = "target_state.form_body_suppressed_" + condition
        prior = "target_state.form_condition_was_suppressed_" + condition
        native.extend(
            [
                _when(
                    flag + " == true",
                    [{"remove_condition": {"target": "target_state", "condition": condition}}],
                ),
                _when(
                    flag + " != true and " + prior + " == true and ("
                    f"target_state.timed_condition_unowned_{condition} == true or "
                    f"target_state.timed_condition_{condition} == true or "
                    f"target_state.turn_effect_{condition} == true)",
                    [{"apply_condition": {"target": "target_state", "condition": condition}}],
                ),
                _set(prior, flag + " == true"),
            ]
        )
    suppression["effects"].append(_when("target_state.has_transformations == true", native))
    changed["resolve_srd_timed_actor_condition_suppression"] = suppression
    projection = deepcopy(tools["resolve_srd_control_behavior_projection"])
    for effects in _walk_lists(projection["effects"]):
        for effect in effects:
            if (
                effect.get("condition") == "target_state.timed_control_speed_zero == true"
                and "_control_hypnotic_active" in str(effect)
            ):
                effect["condition"] = (
                    "target_state.timed_control_speed_zero == true or "
                    "target_state.has_transformations == true"
                )
                effect["on_true"].append(
                    _when(
                        "_control_hypnotic_active == true",
                        [
                            {
                                "apply_condition": {
                                    "target": "target_state",
                                    "condition": "incapacitated",
                                }
                            }
                        ],
                    )
                )

    def guard_conditions(effects):
        for i, effect in enumerate(effects):
            if effect.get("condition", "").startswith("target_state.form_body_suppressed_"):
                continue
            condition = effect.get("apply_condition", {}).get("condition")
            if condition in BODY_IMMUNITY_CONDITIONS:
                effects[i] = _when(
                    "target_state.form_body_suppressed_" + condition + " != true", [effect]
                )
            else:
                for key in ("on_true", "on_false"):
                    if key in effect:
                        guard_conditions(effect[key])
                if "for_each" in effect:
                    guard_conditions(effect["for_each"]["do"])

    guard_conditions(projection["effects"])
    changed["resolve_srd_control_behavior_projection"] = projection
    changed["resolve_srd_form_condition_immunity"] = _body_condition_immunity_tool()


def _form_lookup_tool():
    "Read only immutable executable body profiles from their separate source tables."
    return _tool(
        "Read the selected admitted body profile without accepting supplied statistics.",
        [_parameter("form_id")],
        [
            _require(
                "form_id in definitions.beast_forms or form_id in definitions.non_beast_forms",
                "The selected source form has no complete owning body consumers.",
            ),
            _when(
                "form_id in definitions.beast_forms",
                [_lookup("definitions.beast_forms", "form_id", "_transformation_form")],
                [_lookup("definitions.non_beast_forms", "form_id", "_transformation_form")],
            ),
        ],
    )


def _compose_form_lookups(tools, changed):
    "Keep Beast-only callers intact while transformations share the same body stat owners."
    body_validation = deepcopy(tools["validate_srd_beast_form_for_target"])
    body_validation["description"] = (
        "Check the printed body CR against the actual saved target level or CR."
    )
    changed["validate_srd_body_form_for_target"] = body_validation
    for tool in changed.values():
        for effects in _walk_lists(tool["effects"]):
            for i, effect in enumerate(effects):
                lookup = effect.get("table_lookup", {})
                if lookup.get("table") == "definitions.beast_forms":
                    effects[i] = _call(
                        "lookup_srd_transformation_form",
                        {"form_id": "{" + lookup["key"] + "}"},
                        {effect["store_as"]: "_transformation_form"},
                    )
    changed["lookup_srd_transformation_form"] = _form_lookup_tool()


def _emit_non_beast_forms(text, beast_schema, forms):
    "Emit only executable profiles; the public catalogue retains all unsupported source blocks."
    schema = deepcopy(beast_schema)
    schema["properties"]["source"]["properties"]["page"].update(minimum=1, maximum=400)
    schema["properties"]["creature_type"] = {"type": "string"}
    schema["required"].append("creature_type")
    section = yaml.dump(
        {"definitions.non_beast_forms": schema}, Dumper=actor._RuntimeDumper, sort_keys=False
    )
    section = "".join("  " + line for line in section.splitlines(True))
    pattern = r"(?ms)^  definitions\.non_beast_forms:.*?\n(?=^  definitions\.|^definitions:)"
    if re.search(pattern, text):
        text = re.sub(pattern, lambda _: section, text, count=1)
    else:
        text = text.replace("\ndefinitions:\n", "\n" + section + "definitions:\n", 1)
    rows = yaml.dump({"non_beast_forms": forms}, Dumper=actor._RuntimeDumper, sort_keys=False)
    rows = "".join("  " + line for line in rows.splitlines(True))
    pattern = r"(?ms)^  non_beast_forms:\n.*?(?=^  [A-Za-z_][^\n]*:|^tools:)"
    if re.search(pattern, text):
        return re.sub(pattern, lambda _: rows, text, count=1)
    return text.replace("\ndefinitions:\n", "\ndefinitions:\n" + rows, 1)


def extend_equipment_runtime(text):
    "Restore form consumers after the advancement owner regenerates ordinary equipment."
    tools = actor.defense._load_runtime(text)["tools"]
    if "project_srd_transformation" not in tools:
        return text
    changed = {}
    _compose_equipment(tools, changed)
    return actor._emit(text, changed)


def extend_runtime(text):
    "Compose transformations after final shared actor, action, information, and reaction owners."
    runtime = actor.defense._load_runtime(text)
    tools = runtime["tools"]
    changed = _source_tools()
    changed.update(_followup_tools())
    changed.update(
        {
            "project_srd_transformation": _projection_tool(),
            "validate_srd_transformation_form": _validate_tool(),
            "install_srd_transformation_source": _install_tool(),
            "resolve_srd_transformation_target_cast": _target_cast_tool(),
            "cast_spell_transformation": _cast_tool(),
            "attack_srd_transformed_form": _attack_tool(),
            "attack_srd_altered_natural_weapon": _altered_natural_attack_tool(),
            "check_srd_form_movement_permissions": _form_movement_permissions_tool(),
            "check_srd_form_pack_ally": _pack_ally_tool(),
            "check_srd_form_light": _body_light_tool(),
            "apply_srd_form_attack_damage": _extra_attack_damage_tool(
                qualified="apply_srd_qualifying_composite_damage" in tools
            ),
        }
    )
    # Optional participant resolution has no identity variable when absent.
    for tool in changed.values():
        if any(p["name"] == "target_character" for p in tool["parameters"]):
            for effects in list(_walk_lists(tool["effects"])):
                for effect in effects:
                    if (
                        effect.get("call")
                        and effect.get("params", {}).get("target_character")
                        == "{_target_character_id}"
                    ):
                        effect["params"]["target_character"] = (
                            "{_transformation_optional_character_ref}"
                        )
            tool["effects"][0:0] = [
                _set("_transformation_optional_character_ref", None),
                _when(
                    "target_character != null",
                    [_set("_transformation_optional_character_ref", "_target_character_id")],
                ),
            ]
    _compose_equipment(tools, changed)
    _compose_consumers(tools, changed)
    _compose_respiration(tools, changed)
    _compose_body_light(tools, changed)
    _compose_body_condition_immunity(tools, changed)
    proficiencies.compose_preparation(tools, changed)
    _casting_dispatch(tools, changed)
    _compose_form_lookups(tools, changed)
    for name in (
        "advance_srd_lasting_clock",
        "end_srd_concentration_node",
        "refresh_srd_timed_condition_target",
    ):
        tool = changed.get(name, deepcopy(tools[name]))
        if name == "refresh_srd_timed_condition_target":
            _owned_hook(
                tool,
                "target_state.has_transformations == true",
                "project_srd_transformation",
                {"target_state": "{_target_state_id}", "lasting_state": "{_lasting_state_id}"},
            )
        else:
            _owned_hook(
                tool,
                "lasting_state.transformation_target_ids != null",
                "refresh_srd_transformations",
                {"lasting_state": "{_lasting_state_id}"},
            )
        changed[name] = tool
    descent.compose(tools, changed)
    recharge.compose(tools, changed)
    # Spatial effects retain their canonical package-owned scene path.
    light = changed.pop("resolve_srd_information_light", None)
    descent_tools = {
        name: changed.pop(name)
        for name in ("start_srd_wind_walk_descent", "settle_srd_wind_walk_descent_target")
    }
    text = actor._emit(text, changed)
    text = _replace_tools(text, descent_tools)
    if light is not None:
        text = _replace_tools(text, {"resolve_srd_information_light": light})
    # Source-defined action names are closed over the immutable corpus, not arbitrary profiles.
    non_beasts = {
        name: row
        for name, row in build_non_beast_index()["forms"].items()
        if row["execution_supported"]
    }
    forms = build_index()["forms"] | non_beasts
    beast_schema = deepcopy(runtime["definition_row_schemas"]["definitions.beast_forms"])
    attack_properties = {
        "name": {"type": "string"},
        "kind": {"type": "string", "enum": ["melee", "ranged"]},
        "attack_modifier": {"type": "integer"},
        "normal_range": {"type": "integer", "minimum": 0},
        "long_range": {"type": "integer", "minimum": 0},
        "damage_expression": {"type": "string"},
        "damage_modifier": {"type": "integer"},
        "damage_type": {"type": "string"},
        "bloodied_damage_expression": {"type": "string"},
        "bloodied_damage_modifier": {"type": "integer"},
        "target_not_full_hp_advantage": {"type": "boolean"},
        "extra_damage_expression": {"type": "string"},
        "extra_damage_modifier": {"type": "integer"},
        "extra_damage_type": {"type": "string"},
        "prone_large_or_smaller": {"type": "boolean"},
        "prone_max_size_rank": {"type": "integer", "minimum": -1, "maximum": 5},
        "supported": {"type": "boolean"},
    }
    attack_schema = {
        "type": "object",
        "properties": attack_properties,
        "required": list(attack_properties),
        "additionalProperties": False,
    }
    attack_properties["recharge_min"] = {"type": "integer", "minimum": 1, "maximum": 6}
    beast_schema["properties"].update(
        {
            "initiative_modifier": {"type": "integer"},
            "breathes_air": {"type": "boolean"},
            "breathes_water": {"type": "boolean"},
            "hold_breath_ticks": {"type": "integer", "minimum": 0},
            "pack_tactics": {"type": "boolean"},
            "bright_light_feet": {"type": "integer", "minimum": 0},
            "dim_light_feet": {"type": "integer", "minimum": 0},
            **{
                field: {"type": "array", "items": {"type": "string"}}
                for field in (
                    "condition_immunities",
                    "damage_immunities",
                    "damage_resistances",
                    "damage_vulnerabilities",
                )
            },
            "source_stat_block": {"type": "string"},
            "source_stat_block_sha256": {"type": "string"},
            "skills": {
                "type": "object",
                "properties": {
                    s: {"type": "integer"}
                    for s in ("acrobatics", "athletics", "perception", "stealth")
                },
                "additionalProperties": False,
            },
            "senses": {
                "type": "object",
                "properties": {
                    s: {"type": "integer", "minimum": 0}
                    for s in (
                        "darkvision_feet",
                        "blindsight_feet",
                        "tremorsense_feet",
                        "truesight_feet",
                    )
                },
                "required": [
                    "darkvision_feet",
                    "blindsight_feet",
                    "tremorsense_feet",
                    "truesight_feet",
                ],
                "additionalProperties": False,
            },
            "passive_perception": {"type": "integer"},
            "languages": {"type": "array", "items": {"type": "string"}},
            "attacks": {
                "type": "object",
                "properties": {
                    n: deepcopy(attack_schema) for form in forms.values() for n in form["attacks"]
                },
                "additionalProperties": False,
            },
            "multiattack": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "attack": {"type": "string"},
                        "count": {"type": "integer", "minimum": 1},
                    },
                    "required": ["attack", "count"],
                    "additionalProperties": False,
                },
            },
            "unsupported_clauses": {"type": "array", "items": {"type": "string"}},
            "execution_supported": {"type": "boolean"},
        }
    )
    beast_schema["required"] = [
        field for field in beast_schema["properties"] if field != "recharge_attack_names"
    ]
    beast_schema["properties"]["recharge_attack_names"] = {
        "type": "array", "items": {"type": "string"},
    }
    serialized = yaml.dump(
        {"definitions.beast_forms": beast_schema}, Dumper=actor._RuntimeDumper, sort_keys=False
    )
    serialized = "".join("  " + line for line in serialized.splitlines(True))
    text = re.sub(
        r"(?ms)^  definitions\.beast_forms:.*?\n(?=^  definitions\.|^definitions:)",
        lambda _: serialized,
        text,
        count=1,
    )
    schema = yaml.dump(
        {"transformation_spell": _schema()}, Dumper=actor._RuntimeDumper, sort_keys=False
    )
    schema = "".join("      " + line for line in schema.splitlines(True))
    pattern = r"(?ms)^      transformation_spell:\n.*?(?=^      [A-Za-z_][^\n]*:\n)"
    if re.search(pattern, text):
        text = re.sub(pattern, lambda _: schema, text, count=1)
    else:
        text = text.replace("      timed_actor_defense:", schema + "      timed_actor_defense:", 1)
    return _emit_non_beast_forms(text, beast_schema, non_beasts)
