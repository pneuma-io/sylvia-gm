"""Awaken applies permanent native Intelligence/language and one independent printed Charm."""

from copy import deepcopy

import yaml

if __package__:
    from .srd_body_soul_spells import _clock
    from .srd_control_behaviors import _when
    from .srd_scene_relations import MEASURE_EXPORTS, POSITION_EXPORTS, _tool_scene
    from .srd_timed_actor_defenses import _call, _entity, _load_runtime, _parameter, _require, _set
    from .srd_timed_senses import _optional, _public, _push, _replace_tools
else:
    from srd_body_soul_spells import _clock
    from srd_control_behaviors import _when
    from srd_scene_relations import MEASURE_EXPORTS, POSITION_EXPORTS, _tool_scene
    from srd_timed_actor_defenses import _call, _entity, _load_runtime, _parameter, _require, _set
    from srd_timed_senses import _optional, _public, _push, _replace_tools

SOURCE_SHA = "eecccf60877d85440c472b8d05bd5a0ed9b0278d94f4b1c494b474a2e28d3d4d"
PLANT_SHA = "5dc8fd7cec04e437a675fcafeac50dc751bc7f502fd6d2574433f3a1c98b70b8"
PROFILE = {
    "family": "awaken_spell",
    "base_level": 5,
    "casting_time": "8_hours",
    "range_kind": "touch",
    "range_feet": 0,
    "concentration": False,
    "duration_rounds": 0,
}
CAST_CHOICES = [
    _optional("awaken_subject", default=""),
    _optional("awaken_language", default=""),
    _optional("awaken_plant_form", "enum", "shrub", enum_values=["shrub", "tree"]),
]
PUBLIC_TOOLS = {"test_srd_awakened_intelligence"}
INT_SKILLS = ["arcana", "history", "investigation", "nature", "religion"]
PLANTS = {
    "shrub": {
        "name": "Awakened Shrub",
        "armor_class": 9,
        "hit_points": 10,
        "speed": 20,
        "size_rank": 1,
        "initiative_modifier": -1,
        "proficiency_bonus": 2,
        "challenge_rating_eighths": 0,
        "native_hit_dice_count": 3,
        "native_hit_die_size": 6,
        "strength_score": 3,
        "dexterity_score": 8,
        "constitution_score": 11,
        "intelligence_score": 10,
        "wisdom_score": 10,
        "charisma_score": 6,
        "strength_save": -4,
        "dexterity_save": -1,
        "constitution_save": 0,
        "intelligence_save": 0,
        "wisdom_save": 0,
        "charisma_save": -2,
        "damage_resistances": ["piercing"],
        "melee_reach": 5,
    },
    "tree": {
        "name": "Awakened Tree",
        "armor_class": 13,
        "hit_points": 59,
        "speed": 20,
        "size_rank": 4,
        "initiative_modifier": -2,
        "proficiency_bonus": 2,
        "challenge_rating_eighths": 16,
        "native_hit_dice_count": 7,
        "native_hit_die_size": 12,
        "strength_score": 19,
        "dexterity_score": 6,
        "constitution_score": 15,
        "intelligence_score": 10,
        "wisdom_score": 10,
        "charisma_score": 7,
        "strength_save": 4,
        "dexterity_save": -2,
        "constitution_save": 2,
        "intelligence_save": 0,
        "wisdom_save": 0,
        "charisma_save": -2,
        "damage_resistances": ["bludgeoning", "piercing"],
        "melee_reach": 10,
    },
}


def validate_profiles(definitions):
    """Keep the paid time and permanent consequences equal to the pinned Awaken spell."""
    for spell, definition in definitions.items():
        if definition.get("family") == PROFILE["family"] and (
            spell != "awaken" or definition != PROFILE
        ):
            raise ValueError("Awaken differs from its pinned profile")


def _character_score():
    return _tool_scene(
        "Read actual native Intelligence from the validated target character.",
        [_entity("target_state"), _parameter("actor", resolve="participant")],
        [
            _require(
                "actor.validation_status == 'validated' and actor.package_id == 'wotc.srd.5.2.1'",
                "Awaken needs the actual validated character statistics.",
            ),
            _set("_awaken_old_score", "actor.data.abilities.intelligence"),
            _set("_awaken_native_scores", "actor.data.abilities"),
            _when(
                "target_state.awakened_source != null",
                [_set("_awaken_old_score", "target_state.ability_scores.intelligence")],
            ),
        ],
    )


def _existing():
    return _tool_scene(
        "Qualify the actual native Beast/Plant and apply permanent effects independently of Charm.",
        [
            _entity("target_state"),
            _entity("caster_state"),
            _parameter("language"),
            _parameter("slot_level", "integer", minimum=5, maximum=9),
        ],
        [
            _require(
                "target_state.form_statistics_replaced != true and "
                "target_state.magic_jar_role == null and "
                "target_state.current_body_source_id == null and "
                "target_state.has_recovery_ability_reductions != true",
                "Awaken on a replaced/transferred body needs its native statistics owner.",
            ),
            _set("_awaken_eligible", False),
            _when(
                "target_state.is_alive == true and "
                "(target_state.creature_type == 'beast' or target_state.creature_type == 'plant')",
                [
                    _set("_awaken_old_score", None),
                    _when(
                        "target_state.ability_scores != null",
                        [_set("_awaken_old_score", "target_state.ability_scores.intelligence")],
                    ),
                    _when(
                        "target_state.creature_kind == 'character'",
                        [
                            _call(
                                "read_srd_awaken_character_intelligence",
                                {
                                    "target_state": "{_target_state_id}",
                                    "actor": "{_target_state_id}",
                                },
                                ["_awaken_old_score", "_awaken_native_scores"],
                            )
                        ],
                    ),
                    _require(
                        "_awaken_old_score != null",
                        "Native Intelligence is unknown; do not infer it.",
                    ),
                    _when(
                        "_awaken_old_score <= 3",
                        [
                            _set("_awaken_eligible", True),
                            _when(
                                "target_state.creature_kind == 'character' and "
                                "target_state.ability_scores == null",
                                [_set("target_state.ability_scores", "_awaken_native_scores")],
                            ),
                            _call(
                                "apply_srd_awaken_permanent",
                                {
                                    "target_state": "{_target_state_id}",
                                    "caster_state": "{_caster_state_id}",
                                    "language": "{language}",
                                    "old_score": "{_awaken_old_score}",
                                    "slot_level": "{slot_level}",
                                },
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _permanent():
    """Adjust only Intelligence-derived native modifiers; retain HP, training and other scores."""
    adjustments = [
        _when(
            "target_state.saving_throw_modifiers.intelligence != null",
            [
                _set(
                    "target_state.saving_throw_modifiers.intelligence",
                    "target_state.saving_throw_modifiers.intelligence + _awaken_int_delta",
                )
            ],
        ),
    ]
    for skill in INT_SKILLS:
        adjustments.append(
            _when(
                f"target_state.skill_check_modifiers.{skill} != null",
                [
                    _set(
                        f"target_state.skill_check_modifiers.{skill}",
                        f"target_state.skill_check_modifiers.{skill} + _awaken_int_delta",
                    )
                ],
            )
        )
    adjustments.append(
        _when(
            "target_state.spellcasting_profile.ability == 'intelligence'",
            [
                *[
                    _set(
                        f"target_state.spellcasting_profile.{field}",
                        f"target_state.spellcasting_profile.{field} + _awaken_int_delta",
                    )
                    for field in ("modifier", "attack_bonus", "save_dc")
                ]
            ],
        )
    )
    return _tool_scene(
        "Apply the Instantaneous Intelligence/language grant, then independently qualify Charm.",
        [
            _entity("target_state"),
            _entity("caster_state"),
            _parameter("language"),
            _parameter("old_score", "integer", minimum=1, maximum=10),
            _parameter("slot_level", "integer", minimum=5, maximum=9),
        ],
        [
            _clock(),
            _set("_awaken_int_delta", "0 - ((old_score - 10) // 2)"),
            _when("target_state.ability_scores == null", [_set("target_state.ability_scores", {})]),
            _set("target_state.ability_scores.intelligence", 10),
            _when("target_state.creature_kind == 'monster'", adjustments),
            _set("_awaken_languages", []),
            _when(
                "target_state.languages != null",
                [_set("_awaken_languages", "target_state.languages")],
            ),
            _when("language not in _awaken_languages", [_push("_awaken_languages", "language")]),
            _set("target_state.languages", "_awaken_languages"),
            _set(
                "target_state.awakened_source",
                {
                    "spell": "'awaken'",
                    "source_sha256": f"'{SOURCE_SHA}'",
                    "caster": "_caster_state_id",
                    "started_tick": "state.lasting_tick",
                    "language": "language",
                    "spell_level": "slot_level",
                    "dispel_ongoing": False,
                },
            ),
            _call(
                "check_srd_target_condition_immunity",
                {
                    "target_state": "{_target_state_id}",
                    "condition": "charmed",
                    "source_state_id": "{_caster_state_id}",
                },
                ["_source_condition_immune"],
            ),
            _set("target_state.awakened_charm_source_id", None),
            _when(
                "_source_condition_immune != true",
                [
                    _call(
                        "install_srd_timed_condition_source",
                        {
                            "target_state": "{_target_state_id}",
                            "lasting_state": "state",
                            "caster_state": "{_caster_state_id}",
                            "spell": "awaken",
                            "condition": "charmed",
                            "save_dc": 1,
                            "duration_rounds": 432000,
                            "repeat_save_timing": "none",
                            "control_profile": "none",
                            "actual_spell_level": "{slot_level}",
                        },
                        ["_timed_condition_source_id"],
                    ),
                    _set("target_state.awakened_charm_source_id", "_timed_condition_source_id"),
                    _set(
                        "state.timed_condition_sources.{_timed_condition_source_id}.dispel_ongoing",
                        False,
                    ),
                    _set(
                        "state.timed_condition_sources.{_timed_condition_source_id}.ongoing_duration",
                        False,
                    ),
                    _set(
                        "state.timed_condition_sources.{_timed_condition_source_id}.awaken_source_sha256",
                        f"'{SOURCE_SHA}'",
                    ),
                ],
            ),
        ],
    )


def _natural():
    creates = []
    for form, stats in PLANTS.items():
        values = dict(stats)
        values.update(
            creature_id="{subject}",
            source=f"SRD5.2.1 Awaken111-112; {stats['name']}260 {PLANT_SHA}",
            creature_type="plant",
            damage_vulnerabilities=["fire"],
            skill_proficiencies=[],
            saving_throw_proficiencies=[],
        )
        creates.append(_when(f"form == '{form}'", [_call("prepare_srd_creature", values)]))
    return _tool_scene(
        "Create one actual selected printed Plant creature in its existing physical scene body.",
        [
            _entity("caster_state"),
            _parameter("subject"),
            _parameter("language"),
            _parameter("form", "enum", enum_values=list(PLANTS)),
            _parameter("slot_level", "integer", minimum=5, maximum=9),
        ],
        creates
        + [
            _call(
                "finish_srd_awaken_natural_plant",
                {
                    "target_state": "{subject}",
                    "caster_state": "{_caster_state_id}",
                    "language": "{language}",
                    "form": "{form}",
                    "slot_level": "{slot_level}",
                },
            ),
            _call("srd_information_scene_entity", {"subject": "{subject}"}, POSITION_EXPORTS),
            _set(
                "_awaken_scene_meta_patch",
                {
                    "participant_ref": "subject",
                    "is_object": False,
                    "nonmagical_plant": False,
                    "awakened_plant": True,
                },
            ),
            _set(
                "_awaken_scene_metadata",
                "merge(_info_scene_entity.metadata, _awaken_scene_meta_patch)",
            ),
            {
                "spatial_mutation": {
                    "scene": "state.spatial_scene",
                    "map_id": "{state.spatial_scene.map.id}",
                    "expected_revision": "{state.spatial_scene.map.revision}",
                    "target": {"kind": "entity", "id": "{_info_scene_entity.id}"},
                    "patch": {"metadata": "{_awaken_scene_metadata}", "movement": "passable"},
                },
                "store_as": "_awaken_scene_mutation",
            },
        ],
    )


def _finish_natural():
    return _tool_scene(
        "Stamp the actual awakened Plant's human senses and printed physical source.",
        [
            _entity("target_state"),
            _entity("caster_state"),
            _parameter("language"),
            _parameter("form"),
            _parameter("slot_level", "integer"),
        ],
        [
            _set("target_state.awakened_plant_form", "form"),
            _set("target_state.awakened_plant_source_sha256", f"'{PLANT_SHA}'"),
            _set("target_state.base_passive_perception", 10),
            _set("target_state.native_darkvision_feet", 0),
            _set("target_state.native_blindsight_feet", 0),
            _set("target_state.native_truesight_feet", 0),
            _set("target_state.native_tremorsense_feet", 0),
            _call(
                "apply_srd_awaken_permanent",
                {
                    "target_state": "{_target_state_id}",
                    "caster_state": "{_caster_state_id}",
                    "language": "{language}",
                    "old_score": 10,
                    "slot_level": "{slot_level}",
                },
            ),
        ],
    )


def _cast():
    return _tool_scene(
        "At actual paid completion touch the selected native creature or natural plant.",
        [
            _entity("caster_state"),
            _entity("target_state"),
            _optional("subject", default=""),
            _parameter("language"),
            _parameter("form"),
            _parameter("slot_level", "integer", minimum=5, maximum=9),
        ],
        [
            _call("refresh_srd_control_actor_facts", {"target_state": "{_caster_state_id}"}),
            _require(
                "caster_state.languages != null and language in caster_state.languages",
                "Choose one language the actual caster knows.",
            ),
            _set("_awaken_subject", "_target_state_id"),
            _when(
                "subject != ''",
                [
                    _call(
                        "format_srd_information_projection",
                        {"message": "{subject}"},
                        ["_info_projection_message"],
                    ),
                    _set("_awaken_subject", "_info_projection_message"),
                ],
            ),
            _call(
                "srd_information_scene_entity", {"subject": "{_caster_state_id}"}, POSITION_EXPORTS
            ),
            _set("_awaken_caster_scene", "_info_scene_entity"),
            _call(
                "srd_information_scene_entity", {"subject": "{_awaken_subject}"}, POSITION_EXPORTS
            ),
            _set("_awaken_subject_scene", "_info_scene_entity"),
            _require(
                "_awaken_caster_scene.metadata.plane != null and "
                "_awaken_caster_scene.metadata.plane == _awaken_subject_scene.metadata.plane",
                "Awaken requires the actual touched subject on the same plane.",
            ),
            _call(
                "measure_srd_information_distance",
                {
                    "start": "{_awaken_caster_scene.position}",
                    "end": "{_awaken_subject_scene.position}",
                },
                MEASURE_EXPORTS,
            ),
            _require("_scene_distance <= 5", "The actual subject is beyond touch reach."),
            _call(
                "srd_information_barriers",
                {
                    "start": "{_awaken_caster_scene.position}",
                    "end": "{_awaken_subject_scene.position}",
                    "rule": "solid",
                    "plane": "{_awaken_caster_scene.metadata.plane}",
                },
                ["_information_blocked"],
            ),
            _require(
                "_information_blocked != true", "A solid barrier prevents touching the subject."
            ),
            _set("_awaken_eligible", True),
            _when(
                "_awaken_subject_scene.metadata.participant_ref != null",
                [
                    _call(
                        "apply_srd_awaken_existing_creature",
                        {
                            "target_state": "{_awaken_subject_scene.metadata.participant_ref}",
                            "caster_state": "{_caster_state_id}",
                            "language": "{language}",
                            "slot_level": "{slot_level}",
                        },
                        ["_awaken_eligible"],
                    )
                ],
                [
                    _require(
                        "_awaken_subject_scene.metadata.nonmagical_plant == true",
                        "This scene-only subject is not an admitted natural plant.",
                    ),
                    _require(
                        "_awaken_subject_scene.footprint.kind == 'box'",
                        "Awaken's non-Box natural plant body remains unsupported.",
                    ),
                    _call(
                        "create_srd_awaken_natural_plant",
                        {
                            "caster_state": "{_caster_state_id}",
                            "subject": "{_awaken_subject}",
                            "language": "{language}",
                            "form": "{form}",
                            "slot_level": "{slot_level}",
                        },
                    ),
                ],
            ),
            _set(
                "_spell_summary_rows", [{"label": "'Awaken applied'", "value": "_awaken_eligible"}]
            ),
            _set("_spell_detail_rows", [{"label": "'Granted language'", "value": "language"}]),
        ],
    )


def _intelligence_test():
    tool = _tool_scene(
        "Test an awakened native NPC's saved Intelligence/training against an uncertain task.",
        [
            _entity("target_state"),
            _optional("skill", "enum", "none", enum_values=["none", *INT_SKILLS]),
            _parameter("target_number", "integer"),
            _optional("circumstantial_modifier", "integer", 0),
            _optional("advantage", "boolean", False),
            _optional("disadvantage", "boolean", False),
        ],
        [
            _require(
                "target_state.creature_kind == 'monster' and target_state.awakened_source != null "
                f"and target_state.awakened_source.source_sha256 == '{SOURCE_SHA}' and "
                "target_state.form_statistics_replaced != true",
                "Use this source-bound test only for an actually awakened native NPC.",
            ),
            _set("_awaken_check_modifier", 0),
            _when(
                "skill != 'none'",
                [
                    _when(
                        "target_state.skill_check_modifiers[skill] != null",
                        [
                            _set(
                                "_awaken_check_modifier",
                                "target_state.skill_check_modifiers[skill]",
                            )
                        ],
                        [
                            _require(
                                "target_state.skill_proficiencies != null",
                                "This NPC's unlisted skill training is unknown.",
                            ),
                            _when(
                                "skill in target_state.skill_proficiencies",
                                [_set("_awaken_check_modifier", "target_state.proficiency_bonus")],
                            ),
                        ],
                    ),
                ],
            ),
            _call(
                "d20_test",
                {
                    "actor_state": "{_target_state_id}",
                    "ability": "intelligence",
                    "kind": "ability_check",
                    "ability_modifier": "{_awaken_check_modifier}",
                    "proficiency_bonus": 0,
                    "proficiency_factor": "none",
                    "circumstantial_modifier": "{circumstantial_modifier}",
                    "target_number": "{target_number}",
                    "advantage": "{advantage}",
                    "disadvantage": "{disadvantage}",
                },
                ["_d20_test", "_outcome"],
            ),
        ],
    )
    return _public(tool, "exploration", "Awakened Intelligence", [("Outcome", "_outcome")])


def extend_runtime(text):
    """Bind the actual paid eight-hour cast; its consequences are not an ongoing spell."""
    tools = _load_runtime(text)["tools"]
    cast = deepcopy(tools["cast_spell"])
    names = {p["name"] for p in cast["parameters"]}
    cast["parameters"].extend(deepcopy(p) for p in CAST_CHOICES if p["name"] not in names)
    marker = "_spell_cast_failed != true and _spell_definition.family == 'awaken_spell'"
    cast["effects"] = [e for e in cast["effects"] if e.get("condition") != marker]
    cast["effects"].append(
        _when(
            marker,
            [
                _call(
                    "cast_srd_awaken",
                    {
                        "caster_state": "{_caster_state_id}",
                        "target_state": "{_target_id}",
                        "subject": "{awaken_subject}",
                        "language": "{awaken_language}",
                        "form": "{awaken_plant_form}",
                        "slot_level": "{_slot_level}",
                    },
                    ["_spell_summary_rows", "_spell_detail_rows"],
                )
            ],
        )
    )
    text = _replace_tools(
        text,
        {
            "cast_spell": cast,
            "cast_srd_awaken": _cast(),
            "read_srd_awaken_character_intelligence": _character_score(),
            "apply_srd_awaken_existing_creature": _existing(),
            "apply_srd_awaken_permanent": _permanent(),
            "create_srd_awaken_natural_plant": _natural(),
            "finish_srd_awaken_natural_plant": _finish_natural(),
            "test_srd_awakened_intelligence": _intelligence_test(),
        },
    )
    if "      awaken_spell:" not in text:
        schema = {
            PROFILE["family"]: {
                "type": "object",
                "properties": {
                    k: {
                        "type": "boolean"
                        if type(v) is bool
                        else "integer"
                        if type(v) is int
                        else "string"
                    }
                    for k, v in PROFILE.items()
                    if k != "family"
                },
                "required": [k for k in PROFILE if k != "family"],
                "additionalProperties": False,
            }
        }
        rendered = yaml.safe_dump(schema, sort_keys=False)
        text = text.replace(
            "      timed_senses:",
            "".join("      " + line for line in rendered.splitlines(True))
            + "\n      timed_senses:",
            1,
        )
    return text


def _awaken_damage():
    """Use the causing creature and the caster's current admitted ally relationship."""
    return _tool_scene(
        "End only the Awaken Charm when its caster or an actual current ally causes damage.",
        [
            _entity("target_state"),
            _entity("caster_state"),
            _parameter("source_id"),
            _optional("damage_source", default=""),
        ],
        [
            _set("_awaken_damage_actor", "''"),
            _when(
                "damage_source != ''",
                [
                    _call(
                        "format_srd_information_projection",
                        {"message": "{damage_source}"},
                        ["_info_projection_message"],
                    ),
                    _set("_awaken_damage_actor", "_info_projection_message"),
                ],
            ),
            _set("_awaken_damage_allies", []),
            _when(
                "caster_state.ally_state_ids != null",
                [_set("_awaken_damage_allies", "caster_state.ally_state_ids")],
            ),
            _when(
                "_awaken_damage_actor == _caster_state_id or "
                "_awaken_damage_actor in _awaken_damage_allies",
                [
                    _call(
                        "retire_srd_timed_condition_source",
                        {"lasting_state": "state", "source_id": "{source_id}"},
                    )
                ],
            ),
        ],
    )


def _plant_attack():
    """Use only the chosen printed native Plant attack and its actual current scene reach."""
    choices = []
    for form, name, bonus, dice, modifier, damage_type, reach in (
        ("shrub", "Rake", 1, "fixed-1", 0, "slashing", 5),
        ("tree", "Slam", 6, "3d6", 4, "bludgeoning", 10),
    ):
        choices.append(
            _when(
                f"actor_state.awakened_plant_form == '{form}'",
                [
                    _require(
                        f"attack_name == '{name}' and attack_modifier == {bonus} and "
                        f"damage_expression == '{dice}' and damage_modifier == {modifier} and "
                        f"damage_type == '{damage_type}' and normal_range == {reach}",
                        "Use the actual chosen Awakened Plant's printed attack.",
                    )
                ],
            )
        )
    return _tool_scene(
        "Qualify the awakened Plant's native source attack and actual scene geometry.",
        [
            _entity("actor_state"),
            _entity("target_state"),
            _parameter("attack_name"),
            _parameter("attack_modifier", "integer"),
            _parameter("damage_expression"),
            _parameter("damage_modifier", "integer"),
            _parameter("damage_type"),
            _parameter("normal_range", "integer"),
            _parameter("attack_distance", "integer"),
        ],
        [
            _require(
                f"actor_state.awakened_plant_source_sha256 == '{PLANT_SHA}' and "
                "actor_state.form_statistics_replaced != true",
                "This native attack requires its real awakened Plant body source.",
            ),
            *choices,
            _call(
                "resolve_srd_scene_geometry_relation",
                {"actor_state": "{_actor_state_id}", "target_state": "{_target_state_id}"},
                ["_scene_distance", "_scene_same_plane", "_scene_line_of_sight"],
            ),
            _require(
                "_scene_same_plane == true and _scene_line_of_sight == true and "
                "_scene_distance <= normal_range and attack_distance == ceil(_scene_distance)",
                "Use the actual Plant body's clear, same-plane melee reach.",
            ),
        ],
    )


def bind_consumers(text):
    """Compose permanent stat/language and source-qualified damage owners before clones."""
    tools = _load_runtime(text)["tools"]
    damage = deepcopy(tools["resolve_srd_control_after_damage"])
    branch = (
        "_control_damage_source.installed == true and _control_damage_source.spell_id == 'awaken'"
    )
    loop = next(e["for_each"]["do"] for e in damage["effects"] if "for_each" in e)
    loop[:] = [e for e in loop if e.get("condition") != branch]
    loop.append(
        _when(
            branch,
            [
                _require(
                    "source_state_id != null", "Awaken damage needs the real causing creature ID."
                ),
                _call(
                    "end_srd_awaken_charm_after_damage",
                    {
                        "target_state": "{_target_state_id}",
                        "caster_state": "{_control_damage_source.caster_state_id}",
                        "source_id": "{_control_damage_source_id}",
                        "damage_source": "{source_state_id}",
                    },
                ),
            ],
        )
    )
    character = deepcopy(tools["derive_srd_control_character_facts"])
    flag = "target_state.awakened_source != null"
    character["effects"] = [e for e in character["effects"] if e.get("condition") != flag]
    character["effects"].append(
        _when(
            flag,
            [
                _set("_awaken_character_languages", "target_state.languages"),
                _when(
                    "target_state.awakened_source.language not in _awaken_character_languages",
                    [_push("_awaken_character_languages", "target_state.awakened_source.language")],
                ),
                _set("target_state.languages", "_awaken_character_languages"),
            ],
        )
    )
    d20 = deepcopy(tools["d20_test"])
    guard = "actor != null and actor_state.awakened_source != null and ability == 'intelligence' "
    guard += "and actor_state.form_statistics_replaced != true"
    d20["effects"] = [e for e in d20["effects"] if e.get("condition") != guard]
    index = next(
        i for i, e in enumerate(d20["effects"]) if e.get("set", {}).get("field") == "_modifier"
    )
    d20["effects"].insert(
        index,
        _when(
            guard,
            [_set("_ability_modifier", "(actor_state.ability_scores.intelligence - 10) // 2")],
        ),
    )
    attack = deepcopy(tools["resolve_adjudicated_creature_attack"])
    attack_guard = "actor.awakened_plant_form != null and actor.form_attack_in_progress != true"
    attack["effects"] = [e for e in attack["effects"] if e.get("condition") != attack_guard]
    attack["effects"].insert(
        0,
        _when(
            attack_guard,
            [
                _call(
                    "qualify_srd_awakened_plant_attack",
                    {
                        "actor_state": "{_actor_id}",
                        "target_state": "{_target_id}",
                        **{
                            p: "{" + p + "}"
                            for p in (
                                "attack_name",
                                "attack_modifier",
                                "damage_expression",
                                "damage_modifier",
                                "damage_type",
                                "normal_range",
                                "attack_distance",
                            )
                        },
                    },
                )
            ],
        ),
    )
    return _replace_tools(
        text,
        {
            "resolve_srd_control_after_damage": damage,
            "end_srd_awaken_charm_after_damage": _awaken_damage(),
            "derive_srd_control_character_facts": character,
            "d20_test": d20,
            "resolve_adjudicated_creature_attack": attack,
            "qualify_srd_awakened_plant_attack": _plant_attack(),
        },
    )
