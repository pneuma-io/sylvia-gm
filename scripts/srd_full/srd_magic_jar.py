"""Magic Jar keeps one caster, an actual material container and distinct physical bodies."""

from copy import deepcopy

import yaml

if __package__:
    from .srd_body_soul_spells import _clock, _public
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
        _when,
    )
    from .srd_scene_relations import POSITION_EXPORTS, _snapshot, _tool_scene
    from .srd_spell_object_content import _metadata_patch
    from .srd_spell_object_targets import _lookup_object
    from .srd_timed_senses import _replace_tools
else:
    from srd_body_soul_spells import _clock, _public
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
        _when,
    )
    from srd_scene_relations import POSITION_EXPORTS, _snapshot, _tool_scene
    from srd_spell_object_content import _metadata_patch
    from srd_spell_object_targets import _lookup_object
    from srd_timed_senses import _replace_tools


PROFILE = {
    "family": "magic_jar_spell",
    "base_level": 6,
    "casting_time": "1_minute",
    "range_kind": "self",
    "range_feet": 0,
    "concentration": False,
    "projection_feet": 100,
    "resisted_immunity_rounds": 14400,
}
PUBLIC_TOOLS = {
    "record_srd_magic_jar_container",
    "project_srd_magic_jar_soul",
    "return_srd_magic_jar_soul",
    "read_srd_magic_jar",
}
CAST_CHOICES = [_param("magic_jar_container", required=False, default="")]
PHYSICAL_ABILITIES = ("strength", "dexterity", "constitution")
SENSE_FIELDS = (
    "darkvision_feet",
    "blindsight_feet",
    "tremorsense_feet",
    "truesight_feet",
    "base_passive_perception",
)
BODY_FIELDS = (
    "hp",
    "max_hp",
    "temp_hp",
    "creature_kind",
    "ability_scores",
    "constitution_modifier",
    "saving_throw_modifiers",
    "skill_check_modifiers",
    "speed",
    "base_speed",
    "flying_speed",
    "swim_speed",
    "climb_speed",
    "can_hover",
    "hit_die_size",
    "hit_dice_pools",
    "native_hit_dice",
    *SENSE_FIELDS,
)


def validate_profiles(definitions):
    """Bind Magic Jar's fixed self-cast and later projection limits to its pinned text."""
    for name, definition in definitions.items():
        if definition.get("family") == "magic_jar_spell" and (
            name != "magic_jar" or definition != PROFILE
        ):
            raise ValueError(f"{name} differs from the pinned Magic Jar profile")


def _character(name):
    return _param(name, required=False, default=None, resolve="participant")


def _raw_scene(subject, destination):
    """Read a retained physical source even after its actual scene object is destroyed."""
    return [
        _set(destination, None),
        _set("_jar_scene_rows", "state.spatial_scene.entities"),
        _each(
            "_jar_scene_rows",
            "_jar_scene_row",
            [
                _when(
                    f"_jar_scene_row.id == {subject}",
                    [
                        _require(destination + " == null", "Physical scene identity is ambiguous."),
                        _set(destination, "_jar_scene_row"),
                    ],
                ),
            ],
        ),
        _require(destination + " != null", "The exact physical scene subject is unavailable."),
    ]


def _patch_scene(scene_row, patch):
    return [
        *_snapshot(),
        {
            "spatial_mutation": {
                "scene": "state.spatial_scene",
                "map_id": "{state.spatial_scene.map.id}",
                "expected_revision": "{_info_snapshot.geometry_revision}",
                "target": {"kind": "entity", "id": "{" + scene_row + ".id}"},
                "patch": patch,
            },
            "store_as": "_jar_scene_write",
        },
    ]


def _write_source():
    return [
        *_raw_scene("_jar_source.container", "_jar_container"),
        _set("_jar_source_patch", {"srd_magic_jar": "_jar_source"}),
        _set("_jar_metadata", "merge(_jar_container.metadata, _jar_source_patch)"),
        *_patch_scene("_jar_container", {"metadata": "{_jar_metadata}"}),
    ]


def _capture(entity, destination):
    """Store typed absence instead of unresolved expressions for optional body statistics."""
    effects = [_set(destination, {})]
    for field in (*BODY_FIELDS, "current_body_source_id"):
        effects.extend(
            [
                _set("_jar_saved_field", {field: None}),
                _when(
                    entity + "." + field + " != null",
                    [
                        _set("_jar_saved_field", {field: entity + "." + field}),
                    ],
                ),
                _set(destination, "merge(" + destination + ", _jar_saved_field)"),
            ]
        )
    for field in ("hit_die",):
        effects.extend(
            [
                _set("_jar_saved_field", {field: None, field + "_maximum": None}),
                _when(
                    entity + ".resources." + field + " != null",
                    [
                        _set(
                            "_jar_saved_field",
                            {
                                field: entity + ".resources." + field,
                                field + "_maximum": entity + ".resource_maxima." + field,
                            },
                        ),
                    ],
                ),
                _set(destination, "merge(" + destination + ", _jar_saved_field)"),
            ]
        )
    return effects


def _restore(entity, profile, omit=()):
    effects = []
    fields = [(field, field) for field in BODY_FIELDS]
    fields += [
        ("current_body_source_id", "current_body_source_id"),
        ("resources.hit_die", "hit_die"),
        ("resource_maxima.hit_die", "hit_die_maximum"),
    ]
    for target, saved in fields:
        if target in omit:
            continue
        effects += [
            _set(entity + "." + target, None),
            _when(
                profile + "." + saved + " != null",
                [
                    _set(entity + "." + target, profile + "." + saved),
                ],
            ),
        ]
    return effects


def _source_tools():
    read = _tool_scene(
        "Read the exact caster-bound Magic Jar source on its actual material container.",
        [_entity("caster_state"), _lasting()],
        [
            _require(
                "caster_state.magic_jar_container_id != null", "No actual Magic Jar is active."
            ),
            *_raw_scene("caster_state.magic_jar_container_id", "_jar_container"),
            _set("_jar_source", "_jar_container.metadata.srd_magic_jar"),
            _require(
                "_jar_source != null and _jar_source.installed == true and "
                "_jar_source.caster == _caster_state_id and "
                "_jar_source.source_id == caster_state.magic_jar_source_id",
                "The physical container does not own this exact active caster source.",
            ),
        ],
    )
    bind = _tool_scene(
        "Bind Magic Jar's actual prepared 500 GP material lot to the possessed scene object.",
        [_entity("caster_state"), _param("object_id"), _param("material_item_id")],
        [
            *_lookup_object(),
            _set("_jar_material", None),
            _set("_jar_materials", "caster_state.spell_materials"),
            _each(
                "_jar_materials",
                "_jar_material_lot",
                [
                    _when(
                        "_jar_material_lot.id == material_item_id",
                        [
                            _require("_jar_material == null", "Material identity is ambiguous."),
                            _set("_jar_material", "_jar_material_lot"),
                        ],
                    ),
                ],
            ),
            _require(
                "_jar_material != null and _jar_material.quantity >= 1 and "
                "_jar_material.value_cp >= 50000 and _jar_material.kind == 'specific' and "
                "'magic_jar.1' in _jar_material.components and "
                "_jar_material.inventory_item in caster_state.inventory",
                "Use this caster's actual prepared gem, crystal or reliquary "
                "worth at least 500 GP.",
            ),
            *_metadata_patch(
                {
                    "srd_magic_jar_material": {
                        "caster": "_caster_state_id",
                        "material_id": "material_item_id",
                        "inventory_item": "_jar_material.inventory_item",
                    }
                }
            ),
        ],
    )
    return {
        "read_srd_magic_jar_source": read,
        "record_srd_magic_jar_container": _public(bind),
        "read_srd_magic_jar": _public(deepcopy(read), [("Source", "_jar_source")]),
    }


def _caster_body_profile():
    """PC physical scores come from the validated participant; NPC absence stays unknown."""
    return [
        *_capture("caster_state", "_jar_own_profile"),
        _set("_jar_caster_charisma_proficiency", "'none'"),
        _set("_jar_abilities", "caster_state.ability_scores"),
        _when(
            "caster_character != null",
            [
                _require(
                    "caster_character.kind == 'player_character' and "
                    "_caster_character_id == _caster_state_id and "
                    "caster_character.validation_status == 'validated' and "
                    "caster_character.package_id == 'wotc.srd.5.2.1'",
                    "A PC body needs its actual validated character artifact.",
                ),
                _set("_jar_abilities", "caster_character.data.abilities"),
                _when(
                    "'charisma' in caster_character.data.saving_throw_proficiencies",
                    [
                        _set("_jar_caster_charisma_proficiency", "'normal'"),
                    ],
                ),
            ],
        ),
        _require("_jar_abilities != null", "This body needs its actual source ability scores."),
        _set(
            "_jar_ability_patch",
            {
                "ability_scores": "_jar_abilities",
                "constitution_modifier": "(_jar_abilities.constitution - 10) // 2",
            },
        ),
        _set("_jar_own_profile", "merge(_jar_own_profile, _jar_ability_patch)"),
    ]


def _cast_tool():
    """The paid caster leaves a nonautonomous physical body; its soul stays the same actor."""
    shell_fields = {
        "name": "caster_state.name",
        "creature_kind": "caster_state.creature_kind",
        "creature_type": "caster_state.creature_type",
        "hp": "caster_state.hp",
        "max_hp": "caster_state.max_hp",
        "temp_hp": 0,
        "armor_class": "caster_state.armor_class",
        "speed": 0,
        "is_alive": True,
        "conditions": [],
        "exhaustion_level": 0,
        "stable": False,
        "death_successes": 0,
        "death_failures": 0,
        "bloodied": "caster_state.bloodied == true",
        "reaction_available": False,
        "resources": {},
        "resource_maxima": {},
        "ability_scores": "_jar_abilities",
        "saving_throw_modifiers": {},
        "damage_immunities": "caster_state.damage_immunities",
        "damage_resistances": "caster_state.damage_resistances",
        "damage_vulnerabilities": "caster_state.damage_vulnerabilities",
        "magic_jar_role": "'body'",
        "magic_jar_caster_id": "_caster_state_id",
        "magic_jar_source_id": "_jar_source_id",
        "logical_actor_id": "_caster_state_id",
        "body_available": True,
        "vital_parts_present": True,
    }
    return _tool_scene(
        "Install Magic Jar only after its actual paid minute and material binding.",
        [
            _entity("caster_state"),
            _character("caster_character"),
            _param("object_id"),
            _param("slot_level", "integer"),
            _param("save_dc", "integer"),
            _param("material_item_ids", "array", items={"type": "string"}),
            _lasting(),
        ],
        [
            _clock(),
            *_lookup_object(),
            _require(
                "len(caster_state.conditions) == 0 and caster_state.has_transformations != true",
                "This bounded Jar path currently needs a body without "
                "independent conditions or transformations.",
            ),
            _require(
                "_object_scene.metadata.srd_magic_jar_material != null and "
                "_object_scene.metadata.srd_magic_jar_material.caster == _caster_state_id and "
                "_object_scene.metadata.srd_magic_jar_material.material_id in material_item_ids",
                "Use the actual same prepared container material.",
            ),
            _require(
                "_object_scene.metadata.srd_magic_jar == null",
                "This physical container already holds a Magic Jar source.",
            ),
            _call(
                "resolve_srd_object_content_geometry",
                {
                    "caster_state": "{_caster_state_id}",
                    "object_id": "{object_id}",
                    "range_feet": 5,
                    "requires_sight": False,
                },
            ),
            _call(
                "srd_information_scene_entity", {"subject": "{_caster_state_id}"}, POSITION_EXPORTS
            ),
            _set("_jar_own_scene", "_info_scene_entity"),
            *_caster_body_profile(),
            _call(
                "begin_srd_spell_source_instance",
                {
                    "caster_state": "{_caster_state_id}",
                    "spell": "magic_jar",
                    "slot_level": "{slot_level}",
                },
                ("_spell_source_cast_id",),
            ),
            _set("_jar_source_id", "_spell_source_cast_id"),
            _call(
                "format_srd_information_projection",
                {
                    "message": "{_jar_source_id}:original_body",
                },
                ("_info_projection_message",),
            ),
            _set("_jar_body_id", "_info_projection_message"),
            {"create_entity": {"id": "_jar_body_id", "value": shell_fields}},
            _set(
                "_jar_source",
                {
                    "kind": "'magic_jar'",
                    "spell": "'magic_jar'",
                    "spell_level": "slot_level",
                    "source_id": "_jar_source_id",
                    "cast_id": "_jar_source_id",
                    "source_caster_id": "_caster_state_id",
                    "caster": "_caster_state_id",
                    "container": "object_id",
                    "body_subject": "_jar_body_id",
                    "body_scene": "_jar_own_scene.id",
                    "body_profile": "_jar_own_profile",
                    "body_metadata": "_jar_own_scene.metadata",
                    "installed": True,
                    "mode": "'container'",
                    "host": None,
                    "started_tick": "lasting_state.lasting_tick",
                    "resisted_hosts": [],
                    "dispel_ongoing": True,
                    "ongoing_duration": False,
                    "save_dc": "save_dc",
                    "caster_character": None,
                    "caster_charisma_proficiency": "_jar_caster_charisma_proficiency",
                },
            ),
            _when(
                "caster_character != null",
                [
                    _set(
                        "_jar_caster_character_patch", {"caster_character": "_caster_character_id"}
                    ),
                    _set("_jar_source", "merge(_jar_source, _jar_caster_character_patch)"),
                ],
            ),
            _set("caster_state.magic_jar_source_id", "_jar_source_id"),
            _set("caster_state.magic_jar_container_id", "object_id"),
            _set("caster_state.magic_jar_role", "'container'"),
            _set(
                "_jar_body_meta_patch",
                {
                    "participant_ref": "_jar_body_id",
                    "magic_jar_body_source": "_jar_source_id",
                },
            ),
            _set("_jar_body_metadata", "merge(_jar_own_scene.metadata, _jar_body_meta_patch)"),
            *_patch_scene("_jar_own_scene", {"metadata": "{_jar_body_metadata}"}),
            _when(
                "lasting_state.magic_jar_subject_refs == null",
                [
                    _set("lasting_state.magic_jar_subject_refs", {}),
                    _set("lasting_state.magic_jar_container_ids", []),
                ],
            ),
            _set(
                "lasting_state.magic_jar_subject_refs.{_caster_state_id}",
                {
                    "source_id": "_jar_source_id",
                    "container": "object_id",
                    "scene_id": "object_id",
                    "caster": "_caster_state_id",
                    "role": "'container'",
                },
            ),
            _set(
                "lasting_state.magic_jar_subject_refs.{_jar_body_id}",
                {
                    "source_id": "_jar_source_id",
                    "container": "object_id",
                    "scene_id": "_jar_own_scene.id",
                    "caster": "_caster_state_id",
                    "role": "'body'",
                },
            ),
            _set("_jar_container_ids", "lasting_state.magic_jar_container_ids"),
            _push("_jar_container_ids", "object_id"),
            _set("lasting_state.magic_jar_container_ids", "_jar_container_ids"),
            _set("lasting_state.has_magic_jar_sources", True),
            *_write_source(),
            _set("_spell_summary_rows", [{"label": "'Magic Jar'", "value": "_jar_source_id"}]),
            _set("_spell_detail_rows", [{"label": "'Physical body'", "value": "_jar_body_id"}]),
        ],
    )


def _schema():
    properties = {
        k: {"type": "boolean" if type(v) is bool else "integer" if type(v) is int else "string"}
        for k, v in PROFILE.items()
        if k != "family"
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _magic_action():
    """Use the real combat Action; an exploration projection takes one actual round."""
    return [
        _require(
            "caster_state.is_alive == true and caster_state.hp > 0 and "
            "not ('incapacitated' in caster_state.conditions or "
            "'paralyzed' in caster_state.conditions or 'petrified' in caster_state.conditions or "
            "'stunned' in caster_state.conditions or 'unconscious' in caster_state.conditions)",
            "The actual caster must be able to take this Magic action.",
        ),
        _when(
            "combat.encounter_active == true",
            [
                _require(
                    "combat.turn_active == true and combat.active_actor == _caster_state_id",
                    "Use the caster's own active turn for this Magic action.",
                ),
                _set("combat.magic_jar_action_allowed", True),
                _call(
                    "spend_srd_action", {"magic_action": True, "control_action_kind": "magic_jar"}
                ),
                _set("combat.magic_jar_action_allowed", False),
            ],
            [_call("advance_srd_lasting_time", {"elapsed_rounds": 1})],
        ),
    ]


def _host_profile():
    """Possession cannot invent missing native Hit Dice or source physical scores."""
    return [
        _require(
            "host_state.is_alive == true and host_state.creature_type == 'humanoid'",
            "Project only into an actual living Humanoid body.",
        ),
        _require(
            "host_state.form_statistics_replaced != true and host_state.form_cloud != true and "
            "host_state.magic_jar_source_id == null and host_state.magic_jar_role == null",
            "This host must be an untransformed body not owned by another Jar source.",
        ),
        _require(
            "len(host_state.conditions) == 0",
            "This bounded possession path needs a host without independent body conditions.",
        ),
        *_capture("host_state", "_jar_host_profile"),
        _set("_jar_host_abilities", "host_state.ability_scores"),
        _when(
            "host_character != null",
            [
                _require(
                    "host_character.kind == 'player_character' and "
                    "_host_character_id == _host_state_id and "
                    "host_character.validation_status == 'validated' and "
                    "host_character.package_id == 'wotc.srd.5.2.1'",
                    "A PC host needs its actual validated character artifact.",
                ),
                _set("_jar_host_abilities", "host_character.data.abilities"),
                _call(
                    "derive_srd_control_character_facts",
                    {
                        "target_state": "{_host_state_id}",
                        "actor": "{_host_character_id}",
                    },
                ),
                _set(
                    "_jar_host_sense_patch",
                    {
                        "base_passive_perception": "host_state.base_passive_perception",
                    },
                ),
                _set("_jar_host_profile", "merge(_jar_host_profile, _jar_host_sense_patch)"),
            ],
        ),
        _require(
            "_jar_host_abilities != null and host_state.resources.hit_die != null and "
            "host_state.resource_maxima.hit_die != null and host_state.hit_die_size != null",
            "Possession needs this body's actual source scores and remaining/maximal Hit Dice.",
        ),
        _set("_jar_host_ability_patch", {"ability_scores": "_jar_host_abilities"}),
        _set("_jar_host_profile", "merge(_jar_host_profile, _jar_host_ability_patch)"),
    ]


def _possession_defenses():
    """Only the named protections prevent possession; Charmed immunity is not substituted."""
    return [
        _set("_jar_possession_blocked", False),
        _when(
            "host_state.has_timed_actor_defenses == true",
            [
                _set("_jar_defenses", "lasting_state.turn_effects"),
                _each(
                    "_jar_defenses",
                    "_jar_defense",
                    [
                        _when(
                            "_jar_defense.kind == 'timed_actor_defense' and "
                            "_jar_defense.target == _host_state_id and "
                            "_jar_defense.spell == 'protection_from_evil_and_good'",
                            [
                                _call(
                                    "check_srd_timed_actor_defense_active",
                                    {
                                        "source_state": "{_jar_defense.caster}",
                                        "target_state": "{_host_state_id}",
                                        "lasting_state": "{_lasting_state_id}",
                                        "spell": "{_jar_defense.spell}",
                                        "started_tick": "{_jar_defense.started_tick}",
                                        "concentration": "{_jar_defense.concentration}",
                                        "expires_tick": "{_jar_defense.expires_tick}",
                                    },
                                    ("_actor_defense_active",),
                                ),
                                _when(
                                    "_actor_defense_active == true",
                                    [_set("_jar_possession_blocked", True)],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        # Magic Circle's named ward still uses its printed causing types and normal/reversed area.
        _when(
            "lasting_state.has_magic_circle_sources == true",
            [
                _call(
                    "check_srd_magic_circle_condition",
                    {
                        "target_state": "{_host_state_id}",
                        "condition": "possession",
                        "source_state_id": "{_caster_state_id}",
                    },
                    ("_secure_circle_condition_immune",),
                ),
                _when(
                    "_secure_circle_condition_immune == true",
                    [_set("_jar_possession_blocked", True)],
                ),
            ],
        ),
    ]


def _project_statistics():
    """Replace the listed statistics while keeping the caster's own unlisted game statistics."""
    effects = [
        _set("_jar_caster_abilities", "_jar_source.body_profile.ability_scores"),
        _set("_jar_physical_patch", {a: "_jar_host_abilities." + a for a in PHYSICAL_ABILITIES}),
        _set("caster_state.ability_scores", "merge(_jar_caster_abilities, _jar_physical_patch)"),
        _set("caster_state.magic_jar_physical_scores", "_jar_physical_patch"),
        _set("caster_state.constitution_modifier", "(_jar_host_abilities.constitution - 10) // 2"),
        _set("caster_state.magic_jar_host_passive", None),
        _when(
            "_jar_host_profile.base_passive_perception != null",
            [
                _set(
                    "caster_state.magic_jar_host_passive",
                    "_jar_host_profile.base_passive_perception",
                ),
            ],
        ),
    ]
    for field in (
        "hp",
        "max_hp",
        "speed",
        "base_speed",
        "flying_speed",
        "swim_speed",
        "climb_speed",
        "can_hover",
        "hit_die_size",
        "hit_dice_pools",
        "native_hit_dice",
        *SENSE_FIELDS,
    ):
        effects.extend(
            [
                _set("caster_state." + field, None),
                _when(
                    "_jar_host_profile." + field + " != null",
                    [
                        _set("caster_state." + field, "_jar_host_profile." + field),
                    ],
                ),
            ]
        )
    effects += [
        _set("caster_state.resources.hit_die", "_jar_host_profile.hit_die"),
        _set("caster_state.resource_maxima.hit_die", "_jar_host_profile.hit_die_maximum"),
        _set("caster_state.current_body_source_id", "_host_state_id"),
        _when(
            "host_state.current_body_source_id != null",
            [
                _set("caster_state.current_body_source_id", "host_state.current_body_source_id"),
            ],
        ),
    ]
    # A saved NPC's explicit proficiency bonus remains part of its own save/skill modifier.
    for ability in PHYSICAL_ABILITIES:
        effects.append(
            _when(
                "_jar_source.body_profile.saving_throw_modifiers != null",
                [
                    _set(
                        "caster_state.saving_throw_modifiers." + ability,
                        f"_jar_source.body_profile.saving_throw_modifiers.{ability} - "
                        f"((_jar_source.body_profile.ability_scores.{ability} - 10) // 2) + "
                        f"((_jar_host_abilities.{ability} - 10) // 2)",
                    ),
                ],
            )
        )
    return effects


def _project_tool():
    return _public(
        _tool_scene(
            "Spend the actual Magic action to project into one visible Humanoid within 100 feet.",
            [
                _entity("caster_state"),
                _entity("host_state"),
                _character("host_character"),
                _lasting(),
            ],
            [
                _call(
                    "read_srd_magic_jar_source",
                    {"caster_state": "{_caster_state_id}"},
                    ("_jar_source", "_jar_container"),
                ),
                _require(
                    "_jar_source.mode == 'container' and "
                    "caster_state.magic_jar_role == 'container'",
                    "The caster's actual soul must inhabit this container.",
                ),
                _require(
                    "_caster_state_id != _host_state_id", "Return to the original body separately."
                ),
                *_host_profile(),
                _call(
                    "resolve_srd_scene_relation",
                    {
                        "actor_state": "{_caster_state_id}",
                        "target_state": "{_host_state_id}",
                    },
                    (
                        "_scene_distance",
                        "_scene_same_plane",
                        "_scene_can_see",
                        "_scene_target_entity_id",
                    ),
                ),
                _require(
                    "_scene_same_plane == true and _scene_distance <= 100 "
                    "and _scene_can_see == true",
                    "See this actual Humanoid within 100 feet of the container.",
                ),
                _set("_jar_resistance_deadline", 0),
                _set("_jar_retained_resistances", []),
                _set("_jar_resistances", "_jar_source.resisted_hosts"),
                _each(
                    "_jar_resistances",
                    "_jar_resistance",
                    [
                        _when(
                            "_jar_resistance.until_tick > lasting_state.lasting_tick",
                            [
                                _push("_jar_retained_resistances", "_jar_resistance"),
                                _when(
                                    "_jar_resistance.host == _host_state_id",
                                    [
                                        _set(
                                            "_jar_resistance_deadline", "_jar_resistance.until_tick"
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
                _require(
                    "lasting_state.lasting_tick >= _jar_resistance_deadline",
                    "This actual host resisted this casting within the past 24 hours.",
                ),
                *_possession_defenses(),
                *_magic_action(),
                _set("_jar_possessed", False),
                _set("_d20_test", None),
                _when(
                    "_jar_possession_blocked != true",
                    [
                        _set("_jar_save_modifier", "host_state.saving_throw_modifiers.charisma"),
                        _when(
                            "host_character != null",
                            [
                                _set(
                                    "_jar_save_modifier",
                                    "(host_character.data.abilities.charisma - 10) // 2",
                                ),
                                _set("_jar_save_proficiency", "'none'"),
                                _when(
                                    "'charisma' in host_character.data.saving_throw_proficiencies",
                                    [
                                        _set("_jar_save_proficiency", "'normal'"),
                                    ],
                                ),
                            ],
                        ),
                        _when(
                            "host_character != null",
                            [
                                _call(
                                    "d20_test",
                                    {
                                        "actor": "{_host_character_id}",
                                        "actor_state": "{_host_state_id}",
                                        "ability": "charisma",
                                        "kind": "saving_throw",
                                        "proficiency_factor": "{_jar_save_proficiency}",
                                        "circumstantial_modifier": 0,
                                        "target_number": "{_jar_source.save_dc}",
                                    },
                                    ("_d20_test", "_outcome"),
                                )
                            ],
                            [
                                _call(
                                    "d20_test",
                                    {
                                        "actor_state": "{_host_state_id}",
                                        "ability": "charisma",
                                        "kind": "saving_throw",
                                        "ability_modifier": "{_jar_save_modifier}",
                                        "proficiency_bonus": 0,
                                        "proficiency_factor": "none",
                                        "target_number": "{_jar_source.save_dc}",
                                        "circumstantial_modifier": 0,
                                    },
                                    ("_d20_test", "_outcome"),
                                )
                            ],
                        ),
                        _set("_jar_possessed", "_outcome == 'failure'"),
                        _when(
                            "_jar_possessed == true",
                            [
                                _set(
                                    "_jar_mode_patch",
                                    {
                                        "mode": "'possessed'",
                                        "host": "_host_state_id",
                                        "host_scene": "_scene_target_entity_id",
                                        "host_profile": "_jar_host_profile",
                                        "host_original_reaction": (
                                            "host_state.reaction_available == true"
                                        ),
                                    },
                                ),
                                _set("_jar_source", "merge(_jar_source, _jar_mode_patch)"),
                                *_project_statistics(),
                                _set("caster_state.magic_jar_role", "'possessed'"),
                                _set("host_state.magic_jar_role", "'host_soul'"),
                                _set("host_state.magic_jar_caster_id", "_caster_state_id"),
                                _set("host_state.magic_jar_source_id", "_jar_source.source_id"),
                                _set("host_state.reaction_available", False),
                                _call(
                                    "install_srd_timed_condition_source",
                                    {
                                        "lasting_state": "{_lasting_state_id}",
                                        "target_state": "{_host_state_id}",
                                        "caster_state_id": "{_caster_state_id}",
                                        "spell": "magic_jar",
                                        "condition": "incapacitated",
                                        "duration_rounds": 0,
                                        "save_dc": "{_jar_source.save_dc}",
                                        "save_ability": "charisma",
                                        "repeat_save_timing": "none",
                                        "repeat_save_advantage": False,
                                        "remove_on_damage": False,
                                        "control_profile": "none",
                                        "spell_cast_id": "{_jar_source.cast_id}",
                                        "spell_cast_owner": "{_jar_source.caster}",
                                        "actual_spell_level": "{_jar_source.spell_level}",
                                    },
                                ),
                                _set(
                                    "_jar_condition_patch",
                                    {"host_condition_source_id": "_timed_condition_source_id"},
                                ),
                                _set("_jar_source", "merge(_jar_source, _jar_condition_patch)"),
                                _set(
                                    "lasting_state.magic_jar_subject_refs.{_caster_state_id}",
                                    {
                                        "source_id": "_jar_source.source_id",
                                        "container": "_jar_source.container",
                                        "scene_id": "_scene_target_entity_id",
                                        "caster": "_caster_state_id",
                                        "role": "'possessed'",
                                    },
                                ),
                                _set(
                                    "lasting_state.magic_jar_subject_refs.{_host_state_id}",
                                    {
                                        "source_id": "_jar_source.source_id",
                                        "container": "_jar_source.container",
                                        "scene_id": "_scene_target_entity_id",
                                        "caster": "_caster_state_id",
                                        "role": "'host'",
                                    },
                                ),
                            ],
                            [
                                _push(
                                    "_jar_retained_resistances",
                                    {
                                        "host": "_host_state_id",
                                        "until_tick": "lasting_state.lasting_tick + 28800",
                                    },
                                ),
                                _set(
                                    "_jar_resist_patch",
                                    {"resisted_hosts": "_jar_retained_resistances"},
                                ),
                                _set("_jar_source", "merge(_jar_source, _jar_resist_patch)"),
                            ],
                        ),
                    ],
                ),
                *_write_source(),
            ],
        ),
        [("Possessed", "_jar_possessed"), ("Saving throw", "_d20_test")],
    )


def _alias_tools():
    """All body aliases require the retained physical container's exact installed source."""
    scene = _tool_scene(
        "Resolve an actual Jar current-body scene identity without creating another position.",
        [_param("subject"), _lasting()],
        [
            _call(
                "format_srd_information_projection",
                {"message": "{subject}"},
                ("_info_projection_message",),
            ),
            _set("_magic_jar_scene_id", "_info_projection_message"),
            _set("_jar_subject_ref", None),
            _when(
                "_magic_jar_scene_id in lasting_state.magic_jar_subject_refs",
                [
                    _set(
                        "_jar_subject_ref",
                        "lasting_state.magic_jar_subject_refs[_magic_jar_scene_id]",
                    ),
                ],
            ),
            _when(
                "_jar_subject_ref != null",
                [
                    *_raw_scene("_jar_subject_ref.container", "_jar_container"),
                    _set("_jar_source", "_jar_container.metadata.srd_magic_jar"),
                    _require(
                        "_jar_source != null and _jar_source.installed == true and "
                        "_jar_source.source_id == _jar_subject_ref.source_id and "
                        "_jar_source.caster == _jar_subject_ref.caster",
                        "The current-body reference must belong to an actual active Jar source.",
                    ),
                    _set("_magic_jar_scene_id", "_jar_subject_ref.scene_id"),
                ],
            ),
        ],
    )
    target = _tool_scene(
        "Forward the exact occupied host body to its one actual caster statistics owner.",
        [_entity("target_state"), _lasting()],
        [
            _set("_magic_jar_target_id", "_target_state_id"),
            _set("_jar_target_ref", None),
            _when(
                "_target_state_id in lasting_state.magic_jar_subject_refs",
                [
                    _set(
                        "_jar_target_ref", "lasting_state.magic_jar_subject_refs[_target_state_id]"
                    ),
                ],
            ),
            _when(
                "_jar_target_ref != null",
                [
                    *_raw_scene("_jar_target_ref.container", "_jar_container"),
                    _set("_jar_source", "_jar_container.metadata.srd_magic_jar"),
                    _require(
                        "_jar_source != null and _jar_source.installed == true and "
                        "_jar_source.source_id == _jar_target_ref.source_id and "
                        "_jar_source.caster == _jar_target_ref.caster",
                        "Forward only an exact source-qualified physical target.",
                    ),
                    _when(
                        "_jar_target_ref.role == 'host'",
                        [
                            _require(
                                "_jar_source.mode == 'possessed' and "
                                "_jar_source.host == _target_state_id",
                                "Only the actual occupied body forwards to its current caster.",
                            ),
                            _set("_magic_jar_target_id", "_jar_source.caster"),
                        ],
                    ),
                    _require(
                        "_jar_target_ref.role != 'container'",
                        "This caster's soul is in the container; "
                        "target the actual body or container.",
                    ),
                ],
            ),
        ],
    )
    return {
        "resolve_srd_magic_jar_scene_subject": scene,
        "resolve_srd_magic_jar_target": target,
    }


def _caster_original_body():
    return [
        _call(
            "read_srd_magic_jar_original_body",
            {
                "body_state": "{_jar_source.body_subject}",
                "caster_state": "{_caster_state_id}",
            },
            ("_jar_body_alive", "_jar_body_hp", "_jar_body_max_hp"),
        ),
        _set(
            "_jar_restore_patch",
            {
                "hp": "_jar_body_hp",
                "max_hp": "_jar_body_max_hp",
            },
        ),
        _set("_jar_restore_profile", "merge(_jar_source.body_profile, _jar_restore_patch)"),
        *_restore("caster_state", "_jar_restore_profile", omit=("temp_hp",)),
        _set("caster_state.magic_jar_physical_scores", None),
        _set("caster_state.magic_jar_host_passive", None),
    ]


def _release_host_tool():
    """Source-specific HP/HD writes do not restore unrelated game state or old conditions."""
    return _tool(
        "Return the actual trapped host soul and its current living physical body.",
        [_entity("caster_state"), _entity("host_state"), _lasting()],
        [
            _call(
                "read_srd_magic_jar_source",
                {"caster_state": "{_caster_state_id}"},
                ("_jar_source", "_jar_container"),
            ),
            _require(
                "_jar_source.host == _host_state_id and _jar_source.mode == 'possessed'",
                "Release only the actual host of this exact source.",
            ),
            _set("host_state.hp", "caster_state.hp"),
            _set("host_state.max_hp", "caster_state.max_hp"),
            _set("host_state.resources.hit_die", "caster_state.resources.hit_die"),
            _set("host_state.resource_maxima.hit_die", "caster_state.resource_maxima.hit_die"),
            _set("host_state.hit_die_size", "caster_state.hit_die_size"),
            _set("host_state.hit_dice_pools", None),
            _when(
                "caster_state.hit_dice_pools != null",
                [
                    _set("host_state.hit_dice_pools", "caster_state.hit_dice_pools"),
                ],
            ),
            _call(
                "retire_srd_timed_condition_source",
                {
                    "lasting_state": "{_lasting_state_id}",
                    "source_id": "{_jar_source.host_condition_source_id}",
                },
            ),
            _call(
                "refresh_srd_timed_condition_target",
                {
                    "target_state": "{_host_state_id}",
                    "lasting_state": "{_lasting_state_id}",
                },
            ),
            _set("host_state.magic_jar_role", None),
            _set("host_state.magic_jar_caster_id", None),
            _set("host_state.magic_jar_source_id", None),
            _set("host_state.reaction_available", "_jar_source.host_original_reaction"),
            _set("lasting_state.magic_jar_subject_refs.{_host_state_id}", None),
            *_caster_original_body(),
            _set("caster_state.magic_jar_role", "'container'"),
            _set(
                "lasting_state.magic_jar_subject_refs.{_caster_state_id}",
                {
                    "source_id": "_jar_source.source_id",
                    "container": "_jar_source.container",
                    "scene_id": "_jar_source.container",
                    "caster": "_caster_state_id",
                    "role": "'container'",
                },
            ),
            _set("_jar_return_patch", {"mode": "'container'", "host": None}),
            _set("_jar_source", "merge(_jar_source, _jar_return_patch)"),
            *_write_source(),
        ],
    )


def _return_tool():
    return _public(
        _tool_scene(
            "Take the actual Magic action to return from host to container, "
            "or from container to body.",
            [
                _entity("caster_state"),
                _param("destination", "enum", enum_values=["container", "body"]),
                _lasting(),
            ],
            [
                _call(
                    "read_srd_magic_jar_source",
                    {"caster_state": "{_caster_state_id}"},
                    ("_jar_source", "_jar_container"),
                ),
                _require(
                    "(destination == 'container' and _jar_source.mode == 'possessed') or "
                    "(destination == 'body' and _jar_source.mode == 'container')",
                    "Return from the actual current soul location to its printed next destination.",
                ),
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{_caster_state_id}"},
                    POSITION_EXPORTS,
                ),
                _set("_jar_return_origin", "_info_scene_entity"),
                _set("_jar_return_destination", "_jar_container"),
                _when(
                    "destination == 'body'",
                    [
                        *_raw_scene("_jar_source.body_scene", "_jar_return_destination"),
                        _call(
                            "read_srd_magic_jar_original_body",
                            {
                                "body_state": "{_jar_source.body_subject}",
                                "caster_state": "{_caster_state_id}",
                            },
                            ("_jar_body_alive",),
                        ),
                        _require(
                            "_jar_body_alive == true",
                            "The original actual body must still be living.",
                        ),
                    ],
                ),
                _call(
                    "measure_srd_information_distance",
                    {
                        "start": "{_jar_return_origin.position}",
                        "end": "{_jar_return_destination.position}",
                    },
                    ("_scene_distance",),
                ),
                _require(
                    "_scene_distance <= 100 and _jar_return_origin.metadata.plane == "
                    "_jar_return_destination.metadata.plane",
                    "The actual destination must be within 100 feet.",
                ),
                *_magic_action(),
                _when(
                    "destination == 'container'",
                    [
                        _call(
                            "release_srd_magic_jar_host",
                            {
                                "caster_state": "{_caster_state_id}",
                                "host_state": "{_jar_source.host}",
                            },
                        ),
                    ],
                    [
                        _call(
                            "end_srd_magic_jar_source",
                            {"caster_state": "{_caster_state_id}", "reason": "returned"},
                        )
                    ],
                ),
            ],
        )
    )


def _end_tool():
    """Actual source ending resolves both souls before destroying only its own container."""
    return _tool_scene(
        "End one exact Magic Jar, return each eligible soul or resolve the printed death.",
        [_entity("caster_state"), _param("reason", required=False, default="ended"), _lasting()],
        [
            _call(
                "read_srd_magic_jar_source",
                {"caster_state": "{_caster_state_id}"},
                ("_jar_source", "_jar_container"),
            ),
            _set("_jar_end_origin", "_jar_container"),
            _when(
                "_jar_source.mode == 'possessed'",
                [
                    *_raw_scene("_jar_source.host_scene", "_jar_end_origin"),
                ],
            ),
            *_raw_scene("_jar_source.body_scene", "_jar_end_body_scene"),
            _call(
                "measure_srd_information_distance",
                {
                    "start": "{_jar_end_origin.position}",
                    "end": "{_jar_end_body_scene.position}",
                },
                ("_scene_distance",),
            ),
            _set(
                "_jar_return_eligible",
                "_scene_distance <= 100 and "
                "_jar_end_origin.metadata.plane == _jar_end_body_scene.metadata.plane",
            ),
            _call(
                "read_srd_magic_jar_original_body",
                {
                    "body_state": "{_jar_source.body_subject}",
                    "caster_state": "{_caster_state_id}",
                },
                ("_jar_body_alive",),
            ),
            _set(
                "_jar_caster_dies",
                "_jar_source.mode == 'caster_dead' or "
                "_jar_return_eligible != true or _jar_body_alive != true",
            ),
            _when(
                "_jar_source.mode == 'possessed'",
                [
                    _call(
                        "settle_srd_magic_jar_end_host",
                        {
                            "caster_state": "{_caster_state_id}",
                            "host_state": "{_jar_source.host}",
                        },
                    ),
                    _call(
                        "read_srd_magic_jar_source",
                        {"caster_state": "{_caster_state_id}"},
                        ("_jar_source", "_jar_container"),
                    ),
                ],
            ),
            *_caster_original_body(),
            _set("caster_state.magic_jar_role", None),
            _set("caster_state.magic_jar_source_id", None),
            _set("caster_state.magic_jar_container_id", None),
            _set("lasting_state.magic_jar_subject_refs.{_caster_state_id}", None),
            _set("lasting_state.magic_jar_subject_refs.{_jar_source.body_subject}", None),
            *_raw_scene("_jar_source.body_scene", "_jar_end_body_scene"),
            _set(
                "_jar_final_body_patch",
                {
                    "participant_ref": "_caster_state_id",
                    "magic_jar_body_source": None,
                },
            ),
            _set(
                "_jar_final_body_metadata",
                "merge(_jar_end_body_scene.metadata, _jar_final_body_patch)",
            ),
            *_patch_scene("_jar_end_body_scene", {"metadata": "{_jar_final_body_metadata}"}),
            _call(
                "retire_srd_magic_jar_original_body",
                {
                    "body_state": "{_jar_source.body_subject}",
                    "caster_state": "{_caster_state_id}",
                },
            ),
            _set(
                "_jar_end_patch",
                {
                    "installed": False,
                    "ending": "reason",
                    "ended_tick": "lasting_state.lasting_tick",
                },
            ),
            _set("_jar_source", "merge(_jar_source, _jar_end_patch)"),
            *_write_source(),
            *_raw_scene("_jar_source.container", "_jar_container"),
            *_patch_scene("_jar_container", {"active": False}),
            _call("refresh_srd_magic_jar_index", {}),
            _when(
                "_jar_caster_dies == true",
                [
                    _call("kill_srd_magic_jar_soul", {"target_state": "{_caster_state_id}"}),
                ],
            ),
        ],
    )


def _body_tools():
    read = _tool(
        "Read the exact source-owned abandoned physical body independently of its caster soul.",
        [_entity("body_state"), _entity("caster_state")],
        [
            _require(
                "body_state.logical_actor_id == _caster_state_id and "
                "body_state.magic_jar_role == 'body' and "
                "body_state.magic_jar_source_id == caster_state.magic_jar_source_id",
                "This is not the actual abandoned body of the exact caster source.",
            ),
            _set("_jar_body_alive", "body_state.is_alive == true"),
            _set("_jar_body_hp", "body_state.hp"),
            _set("_jar_body_max_hp", "body_state.max_hp"),
        ],
    )
    retire = _tool(
        "Retire the nonautonomous mechanical body alias without creating a second soul death.",
        [_entity("body_state"), _entity("caster_state")],
        [
            _require(
                "body_state.logical_actor_id == _caster_state_id and "
                "body_state.magic_jar_role == 'body'",
                "Retire only this caster's own mechanical body alias.",
            ),
            _set("body_state.magic_jar_role", "'retired_body'"),
            _set("body_state.is_alive", False),
            _set("body_state.body_available", False),
        ],
    )
    kill = _tool(
        "Resolve the printed soul death as actual creature death, preserving body-source identity.",
        [_entity("target_state"), _lasting()],
        [
            _set("target_state.is_alive", False),
            _set("target_state.hp", 0),
            {"apply_condition": {"target": "target_state", "condition": "dead"}},
            _call("record_srd_actual_death", {"target_state": "{_target_state_id}"}),
            _when(
                "target_state.concentration_active == true",
                [
                    _call(
                        "end_srd_concentration_node",
                        {
                            "source_state": "{_target_state_id}",
                            "target_state": "{_target_state_id}",
                        },
                    ),
                ],
            ),
            _when(
                "lasting_state.has_clone_bodies == true",
                [
                    _call("settle_srd_clone_death", {"target_state": "{_target_state_id}"}),
                ],
            ),
        ],
    )
    index = _tool_scene(
        "Keep the fast active-source flag truthful without inventing another lifetime authority.",
        [_lasting()],
        [
            _set("_jar_live_containers", []),
            _set("_jar_index", "lasting_state.magic_jar_container_ids"),
            _each(
                "_jar_index",
                "_jar_container_id",
                [
                    *_raw_scene("_jar_container_id", "_jar_container"),
                    _when(
                        "_jar_container.metadata.srd_magic_jar.installed == true",
                        [
                            _push("_jar_live_containers", "_jar_container_id"),
                        ],
                    ),
                ],
            ),
            _set("lasting_state.magic_jar_container_ids", "_jar_live_containers"),
            _set("lasting_state.has_magic_jar_sources", "len(_jar_live_containers) > 0"),
        ],
    )
    return {
        "read_srd_magic_jar_original_body": read,
        "retire_srd_magic_jar_original_body": retire,
        "kill_srd_magic_jar_soul": kill,
        "refresh_srd_magic_jar_index": index,
    }


def _host_death_tool():
    return _tool_scene(
        "On actual occupied body death save the one caster soul against its own saved DC.",
        [_entity("caster_state"), _lasting()],
        [
            _call(
                "read_srd_magic_jar_source",
                {"caster_state": "{_caster_state_id}"},
                ("_jar_source", "_jar_container"),
            ),
            _require(
                "_jar_source.mode == 'possessed' and caster_state.is_alive == false",
                "Resolve an actual occupied host body's death once.",
            ),
            *_raw_scene("_jar_source.host_scene", "_jar_dead_host_scene"),
            _call(
                "measure_srd_information_distance",
                {
                    "start": "{_jar_dead_host_scene.position}",
                    "end": "{_jar_container.position}",
                },
                ("_scene_distance",),
            ),
            _set(
                "_jar_container_reachable",
                "_scene_distance <= 100 and "
                "_jar_dead_host_scene.metadata.plane == _jar_container.metadata.plane and "
                "_jar_container.active == true",
            ),
            _set(
                "_jar_escape_modifier",
                "(_jar_source.body_profile.ability_scores.charisma - 10) // 2",
            ),
            _when(
                "_jar_source.body_profile.saving_throw_modifiers != null",
                [
                    _when(
                        "_jar_source.body_profile.saving_throw_modifiers.charisma != null",
                        [
                            _set(
                                "_jar_escape_modifier",
                                "_jar_source.body_profile.saving_throw_modifiers.charisma",
                            ),
                        ],
                    ),
                ],
            ),
            _when(
                "_jar_source.caster_character != null",
                [
                    _call(
                        "d20_test",
                        {
                            "actor": "{_jar_source.caster_character}",
                            "actor_state": "{_caster_state_id}",
                            "kind": "saving_throw",
                            "ability": "charisma",
                            "target_number": "{_jar_source.save_dc}",
                            "proficiency_factor": "{_jar_source.caster_charisma_proficiency}",
                            "circumstantial_modifier": 0,
                        },
                        ("_d20_test", "_outcome"),
                    )
                ],
                [
                    _call(
                        "d20_test",
                        {
                            "actor_state": "{_caster_state_id}",
                            "kind": "saving_throw",
                            "ability": "charisma",
                            "ability_modifier": "{_jar_escape_modifier}",
                            "proficiency_bonus": 0,
                            "proficiency_factor": "none",
                            "target_number": "{_jar_source.save_dc}",
                            "circumstantial_modifier": 0,
                        },
                        ("_d20_test", "_outcome"),
                    )
                ],
            ),
            _set("_jar_escaped", "_outcome == 'success' and _jar_container_reachable == true"),
            _set("_jar_dead_host", "_jar_source.host"),
            _call(
                "release_srd_magic_jar_host",
                {
                    "caster_state": "{_caster_state_id}",
                    "host_state": "{_jar_source.host}",
                },
            ),
            _call("kill_srd_magic_jar_soul", {"target_state": "{_jar_dead_host}"}),
            _when(
                "_jar_escaped == true",
                [
                    _set("caster_state.is_alive", True),
                    {"remove_condition": {"target": "caster_state", "condition": "dead"}},
                    _set("caster_state.death_tick", None),
                ],
                [
                    _call(
                        "read_srd_magic_jar_source",
                        {"caster_state": "{_caster_state_id}"},
                        ("_jar_source", "_jar_container"),
                    ),
                    _set("_jar_soul_death_patch", {"mode": "'caster_dead'"}),
                    _set("_jar_source", "merge(_jar_source, _jar_soul_death_patch)"),
                    *_write_source(),
                    _call("kill_srd_magic_jar_soul", {"target_state": "{_caster_state_id}"}),
                ],
            ),
        ],
    )


def _end_host_tool():
    return _tool_scene(
        "On source ending return the host soul only to its actual living body within 100 feet.",
        [_entity("caster_state"), _entity("host_state"), _lasting()],
        [
            _call(
                "read_srd_magic_jar_source",
                {"caster_state": "{_caster_state_id}"},
                ("_jar_source", "_jar_container"),
            ),
            _require(
                "_jar_source.mode == 'possessed' and _jar_source.host == _host_state_id",
                "Resolve only the actual trapped host of this source.",
            ),
            *_raw_scene("_jar_source.host_scene", "_jar_host_scene"),
            _call(
                "measure_srd_information_distance",
                {
                    "start": "{_jar_container.position}",
                    "end": "{_jar_host_scene.position}",
                },
                ("_scene_distance",),
            ),
            _set(
                "_jar_host_dies",
                "caster_state.is_alive != true or _scene_distance > 100 or "
                "_jar_container.metadata.plane != _jar_host_scene.metadata.plane",
            ),
            _call(
                "release_srd_magic_jar_host",
                {
                    "caster_state": "{_caster_state_id}",
                    "host_state": "{_host_state_id}",
                },
            ),
            _when(
                "_jar_host_dies == true",
                [
                    _call("kill_srd_magic_jar_soul", {"target_state": "{_host_state_id}"}),
                ],
            ),
        ],
    )


def _dispel_tool():
    """Dispel selects the actual container and cast; its real ability check decides retirement."""
    return _tool_scene(
        "Dispel one actual Magic Jar source through its owning soul-return callback.",
        [
            _entity("caster_state"),
            _character("caster"),
            _param("object_id"),
            _param("source_id"),
            _param("slot_level", "integer"),
            _param("casting_modifier", "integer"),
            _param("casting_ability"),
            _lasting(),
        ],
        [
            *_lookup_object(),
            _set("_jar_dispel_source", "_object_scene.metadata.srd_magic_jar"),
            _require(
                "_jar_dispel_source != null and _jar_dispel_source.installed == true and "
                "_jar_dispel_source.source_id == source_id and "
                "_jar_dispel_source.spell == 'magic_jar' and "
                "_jar_dispel_source.dispel_ongoing == true",
                "Select this container's exact actual ongoing Magic Jar casting.",
            ),
            _call(
                "resolve_srd_object_content_geometry",
                {
                    "caster_state": "{_caster_state_id}",
                    "object_id": "{object_id}",
                    "range_feet": 120,
                    "requires_sight": False,
                },
            ),
            _call(
                "resolve_srd_dispel_source_check",
                {
                    "caster_state": "{_caster_state_id}",
                    "caster": "{_caster_id}",
                    "source_level": "{_jar_dispel_source.spell_level}",
                    "slot_level": "{slot_level}",
                    "casting_modifier": "{casting_modifier}",
                    "casting_ability": "{casting_ability}",
                },
                ("_dispel_succeeded", "_dispel_roll"),
            ),
            _set(
                "_jar_dispel_check",
                {
                    "spell": "'magic_jar'",
                    "source_id": "source_id",
                    "ended": "_dispel_succeeded",
                    "roll": None,
                },
            ),
            _when(
                "_dispel_roll != null",
                [
                    _set("_jar_dispel_roll_patch", {"roll": "_dispel_roll"}),
                    _set("_jar_dispel_check", "merge(_jar_dispel_check, _jar_dispel_roll_patch)"),
                ],
            ),
            _when(
                "_dispel_succeeded == true",
                [
                    _call(
                        "end_srd_magic_jar_source",
                        {
                            "caster_state": "{_jar_dispel_source.caster}",
                            "reason": "dispel",
                        },
                    ),
                ],
            ),
        ],
    )


def extend_runtime(text):
    """Install the source-owning family before its final physical/action consumers."""
    runtime = yaml.load(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
    tools = runtime["tools"]
    changed = _source_tools() | _alias_tools() | _body_tools()
    changed["cast_srd_magic_jar_spell"] = _cast_tool()
    changed["project_srd_magic_jar_soul"] = _project_tool()
    changed["return_srd_magic_jar_soul"] = _return_tool()
    changed["release_srd_magic_jar_host"] = _release_host_tool()
    changed["end_srd_magic_jar_source"] = _end_tool()
    changed["settle_srd_magic_jar_end_host"] = _end_host_tool()
    changed["settle_srd_magic_jar_host_death"] = _host_death_tool()
    changed["dispel_srd_magic_jar_source"] = _dispel_tool()
    recovery = deepcopy(tools["cast_srd_recovery"])
    marker = "spell == 'dispel_magic' and recovery_choice == 'magic_jar'"
    if len(recovery["effects"]) == 1 and recovery["effects"][0].get("condition") == marker:
        recovery["effects"] = recovery["effects"][0]["on_false"]
    recovery["effects"] = [
        _when(
            marker,
            [
                _set("_revival_succeeded", False),
                _call(
                    "dispel_srd_magic_jar_source",
                    {
                        "caster_state": "{_caster_state_id}",
                        "caster": "{_caster_id}",
                        "object_id": "{recovery_surface}",
                        "source_id": "{recovery_source_id}",
                        "slot_level": "{slot_level}",
                        "casting_modifier": "{casting_modifier}",
                        "casting_ability": "{casting_ability}",
                    },
                    ("_jar_dispel_check",),
                ),
                _set("_dispel_checks", ["_jar_dispel_check"]),
                _set(
                    "_spell_summary_rows",
                    [{"label": "'Selected Jar ended'", "value": "_jar_dispel_check.ended"}],
                ),
                _set(
                    "_spell_detail_rows",
                    [{"label": "'Source decision'", "value": "_jar_dispel_check"}],
                ),
            ],
            recovery["effects"],
        )
    ]
    changed["cast_srd_recovery"] = recovery
    cast = deepcopy(tools["cast_spell"])
    for p in CAST_CHOICES:
        if not any(old["name"] == p["name"] for old in cast["parameters"]):
            cast["parameters"].append(deepcopy(p))
    cast["effects"] = [e for e in cast["effects"] if "cast_srd_magic_jar_spell" not in str(e)]
    params = {
        "caster_state": "{_caster_state_id}",
        "object_id": "{magic_jar_container}",
        "slot_level": "{_slot_level}",
        "save_dc": "{_casting_profile.save_dc}",
        "material_item_ids": "{material_item_ids}",
    }
    cast["effects"].append(
        _when(
            "_spell_cast_failed != true and _spell_definition.family == 'magic_jar_spell'",
            [
                _when(
                    "caster != null",
                    [
                        _call(
                            "cast_srd_magic_jar_spell",
                            params | {"caster_character": "{_caster_id}"},
                            ("_spell_summary_rows", "_spell_detail_rows"),
                        ),
                    ],
                    [
                        _call(
                            "cast_srd_magic_jar_spell",
                            params,
                            ("_spell_summary_rows", "_spell_detail_rows"),
                        )
                    ],
                )
            ],
        )
    )
    guard = {
        "condition": "spell != 'magic_jar' or (caster_state.form_statistics_replaced != true and "
        "caster_state.form_cloud != true and caster_state.magic_jar_source_id == null)",
        "error": "Magic Jar needs an actual untransformed body with no active Jar source.",
    }
    if guard not in cast["pre_conditions"]:
        cast["pre_conditions"].append(guard)
    changed["cast_spell"] = cast
    text = _replace_tools(text, changed)
    if "      magic_jar_spell:" not in text:
        schema = yaml.safe_dump({"magic_jar_spell": _schema()}, sort_keys=False)
        text = text.replace(
            "      timed_senses:",
            "".join("      " + line for line in schema.splitlines(True)) + "\n      timed_senses:",
            1,
        )
    return text
