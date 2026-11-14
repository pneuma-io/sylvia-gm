"""True Polymorph's object forms use one admitted identity and reversible source state."""

from copy import deepcopy

if __package__:
    from .srd_divination_spells import _normalize
    from .srd_scene_relations import POSITION_EXPORTS, _snapshot
    from .srd_spell_object_damage import _packet as _object_packet
    from .srd_spell_object_targets import MATERIAL_AC, OBJECT_HP, OBJECT_SOURCE
    from .srd_timed_actor_defenses import (
        _call as _call_base,
    )
    from .srd_timed_actor_defenses import (
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _tool,
    )
    from .srd_timed_actor_defenses import (
        _when as _when_true,
    )
    from .srd_timed_senses import _loop, _optional, _public, _push, _replace_tools
    from .srd_transformation_spells import FORM_FLAGS, STAT_FIELDS
else:
    from srd_divination_spells import _normalize
    from srd_scene_relations import POSITION_EXPORTS, _snapshot
    from srd_spell_object_damage import _packet as _object_packet
    from srd_spell_object_targets import MATERIAL_AC, OBJECT_HP, OBJECT_SOURCE
    from srd_timed_actor_defenses import (
        _call as _call_base,
    )
    from srd_timed_actor_defenses import (
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _tool,
    )
    from srd_timed_actor_defenses import (
        _when as _when_true,
    )
    from srd_timed_senses import _loop, _optional, _public, _push, _replace_tools
    from srd_transformation_spells import FORM_FLAGS, STAT_FIELDS


SOURCE = (
    "wotc.srd.5.2.1:spells:p170:0c9a596c0d89b51b97c1c0c5b389116824ab5ce124e0f6e7fb8e4745acd6e85b"
)
PUBLIC_TOOLS = {"prepare_srd_true_polymorph_object", "command_srd_true_polymorph_creature"}
CAST_CHOICES = [
    _optional(
        "true_polymorph_mode",
        "enum",
        "creature_to_creature",
        enum_values=["creature_to_creature", "object_to_creature", "creature_to_object"],
    ),
    _optional("true_polymorph_object", resolve="entity"),
    _optional("true_polymorph_material", "enum", "wood", enum_values=list(MATERIAL_AC)),
    _optional("true_polymorph_size", "enum", "medium", enum_values=list(OBJECT_HP)),
    _optional(
        "true_polymorph_durability", "enum", "resilient", enum_values=["fragile", "resilient"]
    ),
    _optional("true_polymorph_description", default=""),
    _optional(
        "true_polymorph_body",
        "array",
        [],
        max_items=1,
        items={
            "type": "object",
            "properties": {
                "footprint": {"type": "object"},
                "height": {"type": "number", "minimum": 0.01},
            },
            "required": ["footprint", "height"],
            "additionalProperties": False,
        },
    ),
]
SIZES = {"tiny": 0, "small": 1, "medium": 2, "large": 3}
SAVED_FIELDS = tuple(
    dict.fromkeys(
        (
            *STAT_FIELDS,
            *FORM_FLAGS,
            "name",
            "source",
            "creature_kind",
            "hp",
            "max_hp",
            "temp_hp",
            "is_alive",
            "conditions",
            "exhaustion_level",
            "bloodied",
            "stable",
            "death_successes",
            "death_failures",
            "ability_scores",
            "proficiency_bonus",
            "challenge_rating_eighths",
            "inventory",
            "held_items",
            "held_attack_names",
            "worn_armor",
            "worn_shield",
            "spellcasting_profile",
            "prepared_spells",
            "spell_definitions",
            "spell_uses_remaining",
            "form_base_statistics",
            "form_base_abilities",
            "form_base_pc_proficiencies",
            "form_base_npc_proficiencies",
            "form_equipment_character_id",
            "has_transformations",
            "transformation_source_ids",
            "temp_hp_transformation_source_id",
            "form_current_source_id",
            "form_ability_scores",
            "form_appearance",
            "is_object",
            "srd_spell_object",
            "magical",
            "worn_by",
            "carried_by",
            "object_description",
            "object_source",
            "object_scene_id",
            "available_bonus_actions",
            "melee_reach",
            "reaction_available",
            "resource_maxima",
            "resources",
            "saving_throw_proficiencies",
            "skill_proficiencies",
            "spell_materials",
            "stable_recovery_hours_remaining",
        )
    )
)
PENDING = "state.pending_srd_action_stack == null or len(state.pending_srd_action_stack) == 0"


def _call(name, params, exports=()):
    result = _call_base(name, params, exports if not isinstance(exports, dict) else ())
    if isinstance(exports, dict):
        result["export_context"] = exports
    return result


def _when(condition, yes, no=None):
    effect = _when_true(condition, yes)
    if no is not None:
        effect["on_false"] = no
    return effect


def _profile(material="material", size="size", durability="durability"):
    return [
        _set("_tp_ac", MATERIAL_AC),
        _set("_tp_hp", OBJECT_HP),
        _set("_tp_sizes", SIZES),
        _set("_tp_size_hp", f"_tp_hp[{size}]"),
        _set(
            "_tp_object_profile",
            {
                "kind": "'srd_spell_object'",
                "source": repr(OBJECT_SOURCE),
                "material": material,
                "size": size,
                "durability": durability,
                "armor_class": f"_tp_ac[{material}]",
                "max_hp": f"_tp_size_hp[{durability}]",
                "hp": f"_tp_size_hp[{durability}]",
                "destroyed": False,
            },
        ),
    ]


def _scene_patch(metadata, **fields):
    return [
        *_snapshot(),
        {
            "spatial_mutation": {
                "scene": "state.spatial_scene",
                "map_id": "{state.spatial_scene.map.id}",
                "expected_revision": "{_info_snapshot.geometry_revision}",
                "target": {"kind": "entity", "id": "{_tp_scene_id}"},
                "patch": {"metadata": metadata, **fields},
            },
            "store_as": "_tp_scene_changed",
        },
    ]


def _admission_tool():
    """Admit the existing object's physical facts without inventing spatial coordinates."""
    return _public(
        _tool(
            "Admit one actual non-creature object identity and physical facts. AC and suggested "
            "average HP come from the SRD Breaking Objects tables; an existing scene object "
            "can be mirrored under the same ID. Preparation never restores damage.",
            [
                _parameter("object_id"),
                _parameter("name"),
                _parameter("source"),
                _parameter("material", "enum", enum_values=list(MATERIAL_AC)),
                _parameter("size", "enum", enum_values=list(OBJECT_HP)),
                _parameter("durability", "enum", enum_values=["fragile", "resilient"]),
                _parameter("magical", "boolean"),
                _optional("worn_by", default=""),
                _optional("carried_by", default=""),
                _optional("scene_object", "boolean", False),
            ],
            [
                _require(PENDING, "Finish the pending action before admitting an object."),
                _require("combat.encounter_active != true", "Admit object facts before combat."),
                _require(
                    "object_id != '' and name != '' and source != ''",
                    "Record actual object identity and provenance.",
                ),
                *_profile(),
                _set("_tp_admitted_object_id", "object_id"),
                _set("_tp_object_metadata", None),
                _when(
                    "scene_object == true",
                    [
                        _call(
                            "srd_information_scene_entity",
                            {"subject": "{object_id}"},
                            POSITION_EXPORTS,
                        ),
                        _require(
                            (
                                "_info_scene_entity.id == object_id and "
                                "_info_scene_entity.metadata.participant_ref == null"
                            ),
                            "Use the same actual non-creature scene identity.",
                        ),
                        _require(
                            "_info_scene_entity.metadata.srd_spell_object == null or "
                            "(_info_scene_entity.metadata.srd_spell_object.material == material "
                            "and "
                            "_info_scene_entity.metadata.srd_spell_object.size == size and "
                            "_info_scene_entity.metadata.srd_spell_object.durability == d"
                            "urability)",
                            "The physical admission must match the existing object profile.",
                        ),
                        _when(
                            "_info_scene_entity.metadata.srd_spell_object != null",
                            [
                                _set(
                                    "_tp_object_profile",
                                    "_info_scene_entity.metadata.srd_spell_object",
                                )
                            ],
                        ),
                        _set("_tp_object_metadata", "_info_scene_entity.metadata"),
                    ],
                ),
                {
                    "create_entity": {
                        "id": "object_id",
                        "value": {
                            "name": "name",
                            "source": "source",
                            "creature_kind": "'object'",
                            "creature_type": "'object'",
                            "is_object": True,
                            "is_alive": True,
                            "hp": "_tp_object_profile.hp",
                            "max_hp": "_tp_object_profile.max_hp",
                            "armor_class": "_tp_object_profile.armor_class",
                            "size_rank": "_tp_sizes[size]",
                            "speed": 0,
                            "temp_hp": 0,
                            "conditions": [],
                            "inventory": [],
                            "srd_spell_object": "_tp_object_profile",
                            "magical": "magical",
                            "worn_by": "worn_by",
                            "carried_by": "carried_by",
                            "object_source": "source",
                            "object_description": "name",
                            "object_scene_id": "''",
                        },
                    }
                },
                _when(
                    "scene_object == true",
                    [_set("object_id.object_scene_id", "_tp_admitted_object_id")],
                ),
            ],
        ),
        "exploration",
        "True Polymorph object facts",
        [("Object profile", "_tp_object_profile")],
    )


def _capture():
    """Save only this spell's explicit original-statistic fields, before changing the target."""
    return [
        _set("_tp_snapshot_fields", [repr(field) for field in SAVED_FIELDS]),
        _loop(
            "_tp_snapshot_fields",
            "_tp_snapshot_field",
            [
                _set(
                    "state.true_polymorph_object_sources.{source_id}.original.{_tp_snapshot_field}",
                    None,
                ),
                _when(
                    "_tp_snapshot_field in target_state and target_state[_tp_snap"
                    "shot_field] != null",
                    [
                        _set(
                            (
                                "state.true_polymorph_object_sources.{source_id}.original.{_t"
                                "p_snapshot"
                                "_field}"
                            ),
                            "target_state[_tp_snapshot_field]",
                        )
                    ],
                ),
            ],
        ),
    ]


def _native_constructor(tools):
    """Reuse native creature preparation in place only for this paid object source."""
    tool = deepcopy(tools["prepare_srd_creature"])
    tool["pre_conditions"] = [
        p
        for p in tool.get("pre_conditions", [])
        if p["condition"] != "combat.encounter_active != true and combat.turn_active != true"
    ]
    tool["parameters"] += [
        _parameter("source_id"),
        _optional("lasting_state", default="state", resolve="entity"),
    ]
    tool["metadata"] = {"category": "internal", "visible_to_player": False, "costs_action": False}
    tool["effects"].insert(
        0,
        _require(
            "lasting_state.true_polymorph_object_sources[source_id].active == true and "
            "lasting_state.true_polymorph_object_sources[source_id].target == creature_id and "
            "lasting_state.true_polymorph_object_sources[source_id].mode == 'object_to_creature'",
            "Only the committed object transformation may prepare this existing identity.",
        ),
    )
    for index, effect in enumerate(tool["effects"]):
        if "create_entity" in effect:
            tool["effects"][index : index + 1] = [
                _set("creature_id." + field, value)
                for field, value in effect["create_entity"]["value"].items()
            ]
            break
    else:
        raise ValueError("True Polymorph needs the existing native creature constructor.")
    return tool


def _creature_body_tool():
    params = [
        _entity("target_state"),
        _entity("caster_state"),
        _parameter("source_id"),
        _parameter("form_id"),
        _parameter("concentration_modifier", "integer"),
    ]
    ctor = {
        "creature_id": "{_target_state_id}",
        "source_id": "{source_id}",
        "name": "{_tp_form.name}",
        "source": SOURCE,
        "armor_class": "{_tp_form.armor_class}",
        "creature_type": "beast",
        "hit_points": "{_tp_form.hit_points}",
        "speed": "{_tp_form.walk_speed}",
        "size_rank": "{_tp_form.size_rank}",
        "initiative_modifier": "{_tp_form.initiative_modifier}",
        "challenge_rating_eighths": "{_tp_form.challenge_rating_eighths}",
        "damage_immunities": "{_tp_form.damage_immunities}",
        "damage_resistances": "{_tp_form.damage_resistances}",
        "damage_vulnerabilities": "{_tp_form.damage_vulnerabilities}",
        "multiattack": "{_tp_form.multiattack}",
    }
    for ability in ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"):
        ctor[ability + "_score"] = "{_tp_form.ability_scores." + ability + "}"
        ctor[ability + "_save"] = "{_tp_form.saving_throw_modifiers." + ability + "}"
    return _tool(
        (
            "Install the admitted creature's real native and body statistics in "
            "this original object identity."
        ),
        params,
        [
            _call(
                "lookup_srd_transformation_form",
                {"form_id": "{form_id}"},
                {"_tp_form": "_transformation_form"},
            ),
            _call("prepare_srd_true_polymorph_creature", ctor),
            _call(
                "install_srd_transformation_source",
                {
                    "target_state": "{_target_state_id}",
                    "caster_state": "{_caster_state_id}",
                    "lasting_state": "state",
                    "spell": "true_polymorph",
                    "form_id": "{form_id}",
                    "mode": "object_to_creature",
                    "actual_spell_level": 9,
                    "accept_temporary_hp": True,
                },
            ),
            _set(
                "state.true_polymorph_object_sources.{source_id}.body_source_id",
                "target_state.form_current_source_id",
            ),
            _set("target_state.temp_hp", 0),
            _set("target_state.temp_hp_transformation_source_id", None),
            _set("target_state.is_object", False),
            _set("target_state.form_can_speak", True),
            _set("target_state.form_can_cast", True),
            _set("target_state.form_no_objects", False),
            _set("target_state.form_equipment_melded", False),
            _set("target_state.summon_active", True),
            _set("target_state.summon_caster_state_id", "_caster_state_id"),
            _set("target_state.true_polymorph_controlled", True),
            _set("target_state.true_polymorph_friendly_to_caster", True),
            _set("target_state.true_polymorph_friendly_to_caster_allies", True),
            _set("target_state.true_polymorph_command", "''"),
            _when(
                "combat.encounter_active == true",
                [
                    _call(
                        "register_srd_summon_in_initiative",
                        {
                            "caster_state": "{_caster_state_id}",
                            "summon_state": "{_target_state_id}",
                            "lasting_state": "state",
                        },
                    )
                ],
            ),
        ],
    )


def _object_projection_tool():
    return _tool(
        (
            "Project only the exact active nonmagical object form, keeping "
            "original gear and actor identity."
        ),
        [_entity("target_state")],
        [
            _set(
                "_tp_source",
                "state.true_polymorph_object_sources[target_state.true_polymorph_object_source_id]",
            ),
            _when(
                "_tp_source.active == true and _tp_source.target == _target_state_id",
                [
                    _when(
                        "_tp_source.mode == 'creature_to_object'",
                        [
                            _set("target_state.creature_kind", "'object'"),
                            _set("target_state.creature_type", "'object'"),
                            _set("target_state.form_statistics_replaced", True),
                            _set("target_state.is_object", True),
                            _set(
                                "target_state.armor_class", "_tp_source.object_profile.armor_class"
                            ),
                            _set("target_state.size_rank", "_tp_source.object_size_rank"),
                            *[
                                _set("target_state." + field, 0)
                                for field in (
                                    "speed",
                                    "base_speed",
                                    "flying_speed",
                                    "swim_speed",
                                    "climb_speed",
                                )
                            ],
                            _set("target_state.damage_immunities", ["'poison'", "'psychic'"]),
                            _set("target_state.damage_resistances", []),
                            _set("target_state.damage_vulnerabilities", []),
                            _set("target_state.form_can_cast", False),
                            _set("target_state.form_can_speak", False),
                            _set("target_state.form_equipment_melded", True),
                            _set("target_state.form_no_objects", True),
                            _set("target_state.form_appearance", "_tp_source.description"),
                            _set("target_state.true_polymorph_unaware", True),
                            _set("target_state.conditions", []),
                        ],
                        [
                            _set("target_state.form_can_speak", True),
                            _set("target_state.form_can_cast", True),
                            _set("target_state.form_no_objects", False),
                            _set("target_state.form_equipment_melded", False),
                        ],
                    ),
                ],
            ),
        ],
    )


def _cast_tool():
    params = [
        _entity("caster_state"),
        _optional("target_state", resolve="entity"),
        _optional("target_character", resolve="participant"),
        _parameter("source_id"),
        _parameter("mode"),
        _optional("form_id", default=""),
        _optional("object_id", resolve="entity"),
        _parameter("save_dc", "integer"),
        _parameter("concentration_modifier", "integer"),
        _parameter("willing", "boolean"),
        _parameter("forego_save", "boolean"),
        _parameter("target_distance", "number"),
        _parameter("can_see", "boolean"),
        _parameter("material"),
        _parameter("size"),
        _parameter("durability"),
        _parameter("description"),
        _optional("body", "array", [], max_items=1, items={"type": "object"}),
    ]
    effects = [
        _require(
            "mode == 'object_to_creature' or mode == 'creature_to_object'",
            "Choose a printed object mode.",
        ),
        _require(
            "can_see == true and target_distance >= 0 and target_distance <= 30",
            "The actual target must be visible within 30 feet.",
        ),
        _set("_tp_target", "_target_state_id"),
        _when(
            "mode == 'object_to_creature'",
            [
                _require(
                    "object_id != null and _target_state_id == _object_id_id",
                    "Select the same actual admitted object as the paid cast target.",
                ),
                _set("_tp_target", "_object_id_id"),
            ],
        ),
        _set("_tp_selected_body", None),
        _when("len(body) == 1", [_set("_tp_selected_body", "body[0]")]),
        _set("_tp_scene_id", "''"),
        _when(
            "state.spatial_scene != null",
            [
                _set("_tp_scene_entities", "state.spatial_scene.entities"),
                _loop(
                    "_tp_scene_entities",
                    "_tp_scene_candidate",
                    [
                        _when(
                            (
                                "_tp_scene_candidate.id == _tp_target or "
                                "_tp_scene_candidate.metadata.participant_ref == _tp_target"
                            ),
                            [_set("_tp_scene_id", "_tp_scene_candidate.id")],
                        )
                    ],
                ),
            ],
        ),
        _when(
            "_tp_scene_id != ''",
            [
                _require(
                    "_tp_selected_body != null and _tp_selected_body.height > 0",
                    (
                        "Admit this chosen form's physical footprint and height for its "
                        "existing Scene body."
                    ),
                ),
                _call(
                    "resolve_srd_scene_relation",
                    {"actor_state": "{_caster_state_id}", "target_state": "{_tp_target}"},
                    ("_scene_same_plane", "_scene_distance", "_scene_can_see"),
                ),
                _require(
                    (
                        "_scene_same_plane == true and _scene_distance <= 30 and "
                        "_scene_can_see == true"
                    ),
                    "The actual Scene target must be visible within 30 feet on the same plane.",
                ),
            ],
        ),
        _call(
            "install_srd_true_polymorph_object_mode",
            {
                "target_state": "{_tp_target}",
                "target_character": "{_target_character_id}",
                "caster_state": "{_caster_state_id}",
                "source_id": "{source_id}",
                "mode": "{mode}",
                "form_id": "{form_id}",
                "save_dc": "{save_dc}",
                "concentration_modifier": "{concentration_modifier}",
                "willing": "{willing}",
                "forego_save": "{forego_save}",
                "material": "{material}",
                "size": "{size}",
                "durability": "{durability}",
                "description": "{description}",
                "body": "{body}",
            },
            ("_tp_save_outcome",),
        ),
        _call(
            "record_srd_spell_target_result",
            {
                "target": "{_tp_target}",
                "brief": "True Polymorph: {_tp_save_outcome}",
                "detail": (
                    "{mode}: {_tp_save_outcome}; exact body and source retained for reversion."
                ),
            },
            ("_spell_target_summary", "_spell_target_detail"),
        ),
        _set("_spell_summary_rows", ["_spell_target_summary"]),
        _set("_spell_detail_rows", ["_spell_target_detail"]),
    ]
    return _tool(
        (
            "Resolve the paid source's object mode using admitted facts and native "
            "object/body statistics."
        ),
        params,
        effects,
    )


def _install_tool():
    params = [
        p
        for p in _cast_tool()["parameters"]
        if p["name"] not in {"object_id", "target_distance", "can_see"}
    ]
    params[1] = _entity("target_state")
    effects = [
        _require(
            "source_id != '' and target_state.is_alive == true",
            "Use a real living or intact target and paid source.",
        ),
        _require(
            "target_state.true_polymorph_object_source_id == null",
            "End the existing object transformation first.",
        ),
        _set("_tp_selected_body", None),
        _when("len(body) == 1", [_set("_tp_selected_body", "body[0]")]),
        _set("_tp_sizes", SIZES),
        _set("_tp_save_outcome", "'failure'"),
        _when(
            "mode == 'object_to_creature'",
            [
                _require(
                    "target_state.is_object == true and target_state.magical == false and "
                    "target_state.hp > 0 and (target_state.worn_by == null or "
                    "target_state.worn_by == '') and "
                    "(target_state.carried_by == null or target_state.carried_by == '')",
                    "Choose an intact nonmagical object that is neither worn nor carried.",
                ),
                _call(
                    "lookup_srd_transformation_form",
                    {"form_id": "{form_id}"},
                    {"_tp_form": "_transformation_form"},
                ),
                _require(
                    "_tp_form.execution_supported == true and "
                    "_tp_form.challenge_rating_eighths <= 72 and "
                    "_tp_form.size_rank <= target_state.size_rank",
                    "The admitted creature must be CR 9 or lower and no larger than the object.",
                ),
            ],
            [
                _require(
                    "target_state.is_object != true and description != '' and "
                    "_tp_sizes[size] <= target_state.size_rank",
                    "The nonmagical object must be no larger than the creature.",
                ),
                _require(
                    (
                        "target_character == null or target_character.participant_id == "
                        "_target_state_id"
                    ),
                    "Use this creature's matching Character File.",
                ),
                _when(
                    "willing != true",
                    [
                        _set("_tp_wisdom", "target_state.saving_throw_modifiers.wisdom"),
                        _when(
                            "target_character != null",
                            [
                                _set(
                                    "_tp_wisdom",
                                    "(target_character.data.abilities.wisdom - 10) // 2",
                                ),
                                _when(
                                    "target_character.data.saving_throw_proficiencies != null"
                                    " and 'wisdom' "
                                    "in "
                                    "target_character.data.saving_throw_proficiencies",
                                    [
                                        _set(
                                            "_tp_wisdom",
                                            (
                                                "_tp_wisdom + 2 + ((target_character.data.lev"
                                                "el - 1) // 4)"
                                            ),
                                        )
                                    ],
                                ),
                            ],
                        ),
                        _require(
                            "_tp_wisdom != null", "Use the target's actual Wisdom save modifier."
                        ),
                        _call(
                            "d20_test",
                            {
                                "actor_state": "{_target_state_id}",
                                "ability": "wisdom",
                                "kind": "saving_throw",
                                "ability_modifier": "{_tp_wisdom}",
                                "proficiency_bonus": 0,
                                "proficiency_factor": "none",
                                "circumstantial_modifier": 0,
                                "target_number": "{save_dc}",
                                "advantage": False,
                                "disadvantage": False,
                                "forego_save": "{forego_save}",
                            },
                            {"_tp_save_outcome": "_outcome"},
                        ),
                    ],
                ),
            ],
        ),
        _when(
            "_tp_save_outcome == 'failure'",
            [
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
                _set("_tp_scene_original", None),
                _set("_tp_actual_scene_id", "''"),
                _when(
                    "state.spatial_scene != null",
                    [
                        _set("_tp_scene_entities", "state.spatial_scene.entities"),
                        _loop(
                            "_tp_scene_entities",
                            "_tp_scene_candidate",
                            [
                                _when(
                                    (
                                        "_tp_scene_candidate.id == _target_state_id or "
                                        "_tp_scene_candidate.metadata.participant_ref == _tar"
                                        "get_state_id"
                                    ),
                                    [
                                        _set("_tp_actual_scene_id", "_tp_scene_candidate.id"),
                                        _set("_tp_scene_original", "_tp_scene_candidate"),
                                    ],
                                )
                            ],
                        ),
                    ],
                ),
                _when("state.lasting_tick == null", [_set("state.lasting_tick", 0)]),
                _when(
                    "state.true_polymorph_object_sources == null",
                    [
                        _set("state.true_polymorph_object_sources", {}),
                        _set("state.true_polymorph_object_source_ids", []),
                    ],
                ),
                _require(
                    "source_id not in state.true_polymorph_object_sources",
                    "This exact cast source is already used.",
                ),
                _set(
                    "state.true_polymorph_object_sources.{source_id}",
                    {
                        "spell": "'true_polymorph'",
                        "spell_level": 9,
                        "cast_id": "source_id",
                        "source_caster_id": "_caster_state_id",
                        "target": "_target_state_id",
                        "mode": "mode",
                        "active": True,
                        "started_tick": "state.lasting_tick",
                        "expires_tick": "state.lasting_tick + 1200",
                        "permanent": False,
                        "original": {},
                        "body_source_id": "''",
                        "scene_id": "_tp_actual_scene_id",
                        "original_scene": None,
                        "body": None,
                        "description": "description",
                        "control_until": "state.lasting_tick + 1200",
                        "source": repr(SOURCE),
                    },
                ),
                *_capture(),
                _when(
                    "_tp_scene_original != null",
                    [
                        _set(
                            "state.true_polymorph_object_sources.{source_id}.original_scene",
                            "_tp_scene_original",
                        )
                    ],
                ),
                _when(
                    "_tp_selected_body != null",
                    [
                        _set(
                            "state.true_polymorph_object_sources.{source_id}.body",
                            "_tp_selected_body",
                        )
                    ],
                ),
                _set("_tp_registry", "state.true_polymorph_object_source_ids"),
                _push("_tp_registry", "source_id"),
                _set("state.true_polymorph_object_source_ids", "_tp_registry"),
                _set("state.has_true_polymorph_objects", True),
                _set("target_state.true_polymorph_object_source_id", "source_id"),
                _set("caster_state.concentration_spell_id", "'true_polymorph'"),
                _set("caster_state.concentration_effect_family", "'transformation_spell'"),
                _set("caster_state.concentration_target_state_id", "_target_state_id"),
                _set("caster_state.concentration_target_state_ids", ["_target_state_id"]),
                _set("caster_state.concentration_started_tick", "state.lasting_tick"),
                _set("caster_state.concentration_expires_tick", "state.lasting_tick + 1200"),
                _set("caster_state.concentration_save_modifier", "concentration_modifier"),
                _when(
                    "mode == 'object_to_creature'",
                    [
                        _call(
                            "install_srd_true_polymorph_creature_body",
                            {
                                "target_state": "{_target_state_id}",
                                "caster_state": "{_caster_state_id}",
                                "source_id": "{source_id}",
                                "form_id": "{form_id}",
                                "concentration_modifier": "{concentration_modifier}",
                            },
                        ),
                    ],
                    [
                        *_profile(),
                        _set(
                            "state.true_polymorph_object_sources.{source_id}.object_profile",
                            "_tp_object_profile",
                        ),
                        _set(
                            "state.true_polymorph_object_sources.{source_id}.object_size_rank",
                            "_tp_sizes[size]",
                        ),
                        _set("target_state.hp", "_tp_object_profile.hp"),
                        _set("target_state.max_hp", "_tp_object_profile.max_hp"),
                        _set("target_state.temp_hp", 0),
                        _set("target_state.srd_spell_object", "_tp_object_profile"),
                        _set("target_state.magical", False),
                        _call(
                            "project_srd_true_polymorph_object_mode",
                            {"target_state": "{_target_state_id}"},
                        ),
                    ],
                ),
                _when(
                    "_tp_actual_scene_id != ''",
                    [
                        _call(
                            "project_srd_true_polymorph_scene_body",
                            {"target_state": "{_target_state_id}", "source_id": "{source_id}"},
                        )
                    ],
                ),
            ],
        ),
    ]
    return _tool(
        (
            "Install only the validated paid True Polymorph object source and its "
            "exact original state."
        ),
        params,
        effects,
    )


def _scene_projection_tool():
    return _tool(
        "Project this exact Source onto the same admitted Scene body, without moving it.",
        [_entity("target_state"), _parameter("source_id")],
        [
            _set("_tp_scene_source", "state.true_polymorph_object_sources[source_id]"),
            _require(
                "_tp_scene_source.active == true and _tp_scene_source.target == _target_state_id",
                "Use the actual installed Source.",
            ),
            _set("_tp_scene_id", "_tp_scene_source.scene_id"),
            _call("srd_information_scene_entity", {"subject": "{_tp_scene_id}"}, POSITION_EXPORTS),
            _set("_tp_scene_metadata", "_info_scene_entity.metadata"),
            _set(
                "_tp_scene_changes",
                {
                    "object_entity_ref": "_target_state_id",
                    "participant_ref": "_target_state_id",
                    "is_object": False,
                    "creature_type": "target_state.creature_type",
                    "srd_spell_object": None,
                },
            ),
            _when(
                "_tp_scene_source.mode == 'creature_to_object'",
                [
                    _set("target_state.object_scene_id", "_tp_scene_id"),
                    _set(
                        "_tp_scene_changes",
                        {
                            "object_entity_ref": "_target_state_id",
                            "participant_ref": None,
                            "is_object": True,
                            "creature_type": "'object'",
                            "srd_spell_object": "target_state.srd_spell_object",
                            "magical": False,
                        },
                    ),
                ],
            ),
            _set("_tp_scene_metadata", "merge(_tp_scene_metadata, _tp_scene_changes)"),
            *_scene_patch(
                "{_tp_scene_metadata}",
                footprint="{_tp_scene_source.body.footprint}",
                height="{_tp_scene_source.body.height}",
            ),
        ],
    )


def _scene_restoration_tool():
    return _tool(
        "Restore the exact original body facts at its current committed position and plane.",
        [_entity("target_state"), _parameter("source_id")],
        [
            _set("_tp_restore_source", "state.true_polymorph_object_sources[source_id]"),
            _require(
                (
                    "_tp_restore_source.target == _target_state_id and "
                    "_tp_restore_source.active != true"
                ),
                "Restore only the actual retired source body.",
            ),
            _set("_tp_scene_id", "_tp_restore_source.scene_id"),
            _call("srd_information_scene_entity", {"subject": "{_tp_scene_id}"}, POSITION_EXPORTS),
            _set("_tp_scene_plane", {"plane": "_info_scene_entity.metadata.plane"}),
            _set(
                "_tp_scene_metadata",
                "merge(_tp_restore_source.original_scene.metadata, _tp_scene_plane)",
            ),
            # Clear only fields installed by this owner before restoring the opaque original.
            _set(
                "_tp_scene_clear",
                {
                    "object_entity_ref": None,
                    "participant_ref": None,
                    "is_object": None,
                    "creature_type": None,
                    "srd_spell_object": None,
                    "magical": None,
                },
            ),
            _set("_tp_scene_metadata", "merge(_tp_scene_clear, _tp_scene_metadata)"),
            *_scene_patch(
                "{_tp_scene_metadata}",
                footprint="{_tp_restore_source.original_scene.footprint}",
                height="{_tp_restore_source.original_scene.height}",
            ),
        ],
    )


def _retirement_tool():
    # Live source membership belongs to the shared form owner, not an old body snapshot.
    live_form_fields = {
        "transformation_source_ids",
        "has_transformations",
        "form_current_source_id",
    }
    restore = [
        _set(
            "_tp_restore_fields",
            [repr(field) for field in SAVED_FIELDS if field not in live_form_fields],
        ),
        _set("_tp_restore_statistics", "_tp_source.original"),
        _loop(
            "_tp_restore_fields",
            "_tp_restore_field",
            [
                _set("target_state.{_tp_restore_field}", None),
                _when(
                    "_tp_restore_field in _tp_restore_statistics and "
                    "_tp_restore_statistics[_tp_restore_field] != null",
                    [
                        _set(
                            "target_state.{_tp_restore_field}",
                            "_tp_restore_statistics[_tp_restore_field]",
                        )
                    ],
                ),
            ],
        ),
    ]
    return _tool(
        (
            "Retire exactly this object transformation and restore its original "
            "identity, gear and state."
        ),
        [_entity("target_state"), _parameter("source_id"), _optional("reason", default="dispel")],
        [
            _set("_tp_source", "state.true_polymorph_object_sources[source_id]"),
            _require(
                (
                    "_tp_source.target == _target_state_id and "
                    "target_state.true_polymorph_object_source_id == source_id"
                ),
                "Select the actual target's exact object source.",
            ),
            _when(
                "_tp_source.active == true",
                [
                    _set("state.true_polymorph_object_sources.{source_id}.active", False),
                    _set(
                        "state.true_polymorph_object_sources.{source_id}.retired_reason", "reason"
                    ),
                    _when(
                        "_tp_source.body_source_id != ''",
                        [
                            _call(
                                "retire_srd_transformation_source",
                                {
                                    "target_state": "{_target_state_id}",
                                    "lasting_state": "state",
                                    "source_id": "{_tp_source.body_source_id}",
                                },
                            )
                        ],
                    ),
                    _set("_tp_live_form_ids", []),
                    _when(
                        "target_state.transformation_source_ids != null",
                        [_set("_tp_live_form_ids", "target_state.transformation_source_ids")],
                    ),
                    _set("_tp_live_temp_hp", "target_state.temp_hp"),
                    _set("_tp_live_temp_owner", None),
                    _when(
                        "target_state.temp_hp_transformation_source_id != null",
                        [
                            _set(
                                "_tp_live_temp_owner",
                                "target_state.temp_hp_transformation_source_id",
                            ),
                        ],
                    ),
                    _set("_tp_live_resources", None),
                    _set("_tp_live_resource_maxima", None),
                    _when(
                        "target_state.resources != null",
                        [_set("_tp_live_resources", "target_state.resources")],
                    ),
                    _when(
                        "target_state.resource_maxima != null",
                        [_set("_tp_live_resource_maxima", "target_state.resource_maxima")],
                    ),
                    *restore,
                    _set("target_state.transformation_source_ids", "_tp_live_form_ids"),
                    _when(
                        "_tp_source.mode == 'creature_to_object' and "
                        "_tp_live_temp_owner != null and _tp_live_temp_owner != "
                        "_tp_source.original.temp_hp_transformation_source_id",
                        [
                            _set("target_state.temp_hp", "_tp_live_temp_hp"),
                            _set(
                                "target_state.temp_hp_transformation_source_id",
                                "_tp_live_temp_owner",
                            ),
                        ],
                    ),
                    _when(
                        "target_state.temp_hp_transformation_source_id != null",
                        [
                            _set(
                                "_tp_restored_temp_source",
                                "target_state.temp_hp_transformation_source_id",
                            ),
                            _when(
                                "state.transformation_sources == null or "
                                "_tp_restored_temp_source not in state.transformation_sources or "
                                "state.transformation_sources[_tp_restored_temp_source].installed "
                                "!= true",
                                [
                                    _set("target_state.temp_hp", 0),
                                    _set("target_state.temp_hp_transformation_source_id", None),
                                ],
                            ),
                        ],
                    ),
                    _when(
                        "_tp_source.mode == 'creature_to_object'",
                        [
                            _set("target_state.resources", None),
                            _set("target_state.resource_maxima", None),
                            _when(
                                "_tp_live_resources != null",
                                [
                                    _set("target_state.resources", "_tp_live_resources"),
                                ],
                            ),
                            _when(
                                "_tp_live_resource_maxima != null",
                                [
                                    _set(
                                        "target_state.resource_maxima", "_tp_live_resource_maxima"
                                    ),
                                ],
                            ),
                        ],
                    ),
                    _when(
                        "_tp_source.scene_id != ''",
                        [
                            _call(
                                "restore_srd_true_polymorph_scene_body",
                                {"target_state": "{_target_state_id}", "source_id": "{source_id}"},
                            )
                        ],
                    ),
                    _call(
                        "clear_srd_true_polymorph_object_concentration",
                        {
                            "caster_state": "{_tp_source.source_caster_id}",
                            "source_id": "{source_id}",
                        },
                    ),
                    _set("target_state.true_polymorph_object_source_id", None),
                    _set(
                        "_tp_needs_body_projection",
                        (
                            "len(_tp_live_form_ids) > 0 or "
                            "_tp_source.original.form_base_statistics != null or "
                            "_tp_source.body_source_id != ''"
                        ),
                    ),
                    _when(
                        "_tp_needs_body_projection == true",
                        [
                            _set("target_state.form_base_statistics", "_tp_source.original"),
                            _when(
                                "_tp_source.original.form_base_statistics != null",
                                [
                                    _set(
                                        "target_state.form_base_statistics",
                                        "_tp_source.original.form_base_statistics",
                                    )
                                ],
                            ),
                            _when(
                                "target_state.form_base_abilities == null and "
                                "_tp_source.original.ability_scores != null",
                                [
                                    _set(
                                        "target_state.form_base_abilities",
                                        "_tp_source.original.ability_scores",
                                    )
                                ],
                            ),
                            _set("target_state.has_transformations", True),
                            _call(
                                "project_srd_transformation",
                                {
                                    "target_state": "{_target_state_id}",
                                    "lasting_state": "state",
                                },
                            ),
                        ],
                        [_set("target_state.form_current_source_id", None)],
                    ),
                    _set("target_state.true_polymorph_controlled", False),
                    _set("target_state.true_polymorph_unaware", False),
                    _set("target_state.summon_active", False),
                    _set("target_state.summon_caster_state_id", None),
                    _set("_tp_remaining_sources", []),
                    _set("_tp_old_sources", "state.true_polymorph_object_source_ids"),
                    _loop(
                        "_tp_old_sources",
                        "_tp_old_source",
                        [
                            _when(
                                "_tp_old_source != source_id",
                                [_push("_tp_remaining_sources", "_tp_old_source")],
                            )
                        ],
                    ),
                    _set("state.true_polymorph_object_source_ids", "_tp_remaining_sources"),
                    _set("state.has_true_polymorph_objects", "len(_tp_remaining_sources) > 0"),
                    _when(
                        "_tp_source.mode == 'creature_to_object'",
                        [
                            _set(
                                "target_state.true_polymorph_memory_gap",
                                {
                                    "started_tick": "_tp_source.started_tick",
                                    "ended_tick": "state.lasting_tick",
                                    "source_id": "source_id",
                                },
                            )
                        ],
                    ),
                ],
            ),
        ],
    )


def _check_tool():
    return _tool(
        (
            "Qualify exact concentration, full-hour persistence, control expiry "
            "and actual target life."
        ),
        [_entity("target_state"), _entity("caster_state"), _parameter("source_id")],
        [
            _set("_tp_source", "state.true_polymorph_object_sources[source_id]"),
            _set(
                "_tp_active",
                (
                    "_tp_source.active == true and "
                    "target_state.true_polymorph_object_source_id == source_id"
                ),
            ),
            _set(
                "_tp_concentration",
                "caster_state.is_alive == true and caster_state.concentration_spell_id "
                "== 'true_polymorph' "
                "and caster_state.concentration_effect_family == 'transformation_spell' and "
                "caster_state.concentration_started_tick == _tp_source.started_tick and "
                "caster_state.concentration_target_state_id == _target_state_id",
            ),
            _when(
                "_tp_active == true and _tp_source.permanent != true and "
                "_tp_concentration == true and "
                "state.lasting_tick >= _tp_source.expires_tick and target_state.is_alive == true",
                [
                    _set("state.true_polymorph_object_sources.{source_id}.permanent", True),
                    _set("_tp_source", "state.true_polymorph_object_sources[source_id]"),
                ],
            ),
            _set(
                "_tp_active",
                "_tp_active == true and target_state.is_alive == true and "
                "(_tp_source.permanent == true or (_tp_concentration == true and "
                "state.lasting_tick < _tp_source.expires_tick))",
            ),
            _when(
                "_tp_active != true and _tp_source.active == true",
                [
                    _call(
                        "retire_srd_true_polymorph_object_source",
                        {
                            "target_state": "{_target_state_id}",
                            "source_id": "{source_id}",
                            "reason": "source_ended",
                        },
                    )
                ],
            ),
            _when(
                "_tp_active == true and _tp_source.mode == 'object_to_creature' and "
                "state.lasting_tick > _tp_source.control_until",
                [
                    _set("target_state.true_polymorph_controlled", False),
                    _set("target_state.summon_active", False),
                    _set("target_state.true_polymorph_friendly_after_hour", None),
                ],
            ),
        ],
    )


def _refresh_tool():
    return _tool(
        "Refresh the actual retained object sources through the one lasting clock.",
        [],
        [
            _set("_tp_refresh_ids", "state.true_polymorph_object_source_ids"),
            _loop(
                "_tp_refresh_ids",
                "_tp_refresh_id",
                [
                    _set("_tp_refresh", "state.true_polymorph_object_sources[_tp_refresh_id]"),
                    _when(
                        "_tp_refresh.active == true",
                        [
                            _call(
                                "check_srd_true_polymorph_object_source",
                                {
                                    "target_state": "{_tp_refresh.target}",
                                    "caster_state": "{_tp_refresh.source_caster_id}",
                                    "source_id": "{_tp_refresh_id}",
                                },
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _command_tool():
    return _public(
        _tool(
            (
                "Give an actual command to this controlled object-created creature; "
                "control ends after an hour."
            ),
            [_entity("actor_state"), _entity("target_state"), _parameter("command")],
            [
                _require(PENDING, "Finish the pending action before giving another command."),
                _require(
                    "target_state.true_polymorph_object_source_id != null",
                    "This target has no active object-to-creature source.",
                ),
                _set(
                    "_tp_command_source",
                    (
                        "state.true_polymorph_object_sources[target_state.true_polymorph_object"
                        "_source_id]"
                    ),
                ),
                _require(
                    (
                        "actor_state.is_alive == true and 'incapacitated' not in "
                        "actor_state.conditions and command != ''"
                    ),
                    "The actual caster must be able to give its command.",
                ),
                _call(
                    "check_srd_true_polymorph_object_source",
                    {
                        "target_state": "{_target_state_id}",
                        "caster_state": "{_actor_state_id}",
                        "source_id": "{target_state.true_polymorph_object_source_id}",
                    },
                    ("_tp_active",),
                ),
                _require(
                    (
                        "_tp_active == true and _tp_command_source.source_caster_id == "
                        "_actor_state_id and "
                        "_tp_command_source.mode == 'object_to_creature' and "
                        "target_state.true_polymorph_controlled == true"
                    ),
                    "Only this creature's actual controlling caster can command it.",
                ),
                _set("target_state.true_polymorph_command", "command"),
            ],
        ),
        "exploration",
        "True Polymorph command",
        [("Command", "command")],
    )


def _end_concentration_tool():
    return _tool(
        "Settle only this caster's exact object mode before the existing concentration cleanup.",
        [_entity("caster_state")],
        [
            _set("_tp_end_ids", "state.true_polymorph_object_source_ids"),
            _loop(
                "_tp_end_ids",
                "_tp_end_id",
                [
                    _set("_tp_end_source", "state.true_polymorph_object_sources[_tp_end_id]"),
                    _when(
                        "_tp_end_source.active == true and _tp_end_source.source_caster_id == "
                        "_caster_state_id and "
                        "_tp_end_source.started_tick == caster_state.concentration_started_tick",
                        [
                            _call(
                                "check_srd_true_polymorph_object_source",
                                {
                                    "caster_state": "{_caster_state_id}",
                                    "target_state": "{_tp_end_source.target}",
                                    "source_id": "{_tp_end_id}",
                                },
                            ),
                            _when(
                                "state.true_polymorph_object_sources[_tp_end_id].active == tr"
                                "ue and "
                                "state.true_polymorph_object_sources[_tp_end_id].permanent != true",
                                [
                                    _call(
                                        "retire_srd_true_polymorph_object_source",
                                        {
                                            "target_state": "{_tp_end_source.target}",
                                            "source_id": "{_tp_end_id}",
                                            "reason": "concentration",
                                        },
                                    )
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _clear_concentration_tool():
    return _tool(
        "Clear only this retired source's actual concentration binding.",
        [_entity("caster_state"), _parameter("source_id")],
        [
            _set("_tp_clear_source", "state.true_polymorph_object_sources[source_id]"),
            _when(
                "caster_state.concentration_spell_id == 'true_polymorph' and "
                "caster_state.concentration_effect_family == 'transformation_spell' and "
                "caster_state.concentration_started_tick == _tp_clear_source.started_tick and "
                "caster_state.concentration_target_state_id == _tp_clear_source.target",
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
    )


def _dispel_tool():
    return _tool(
        "Run the genuine source-level Dispel check on the selected current object-mode target.",
        [
            _entity("caster_state"),
            _optional("caster", resolve="participant"),
            _entity("target_state"),
            _parameter("source_id"),
            _parameter("slot_level", "integer"),
            _parameter("casting_modifier", "integer"),
            _parameter("casting_ability"),
            _parameter("target_distance", "number"),
        ],
        [
            _set("_tp_dispel_source", "state.true_polymorph_object_sources[source_id]"),
            _require(
                "_tp_dispel_source.target == _target_state_id and "
                "target_state.true_polymorph_object_source_id == source_id",
                "Select this actual target's retained Source.",
            ),
            _call(
                "check_srd_true_polymorph_object_source",
                {
                    "target_state": "{_target_state_id}",
                    "caster_state": "{_tp_dispel_source.source_caster_id}",
                    "source_id": "{source_id}",
                },
                ("_tp_active",),
            ),
            _require(
                "_tp_active == true and target_distance >= 0 and target_distance <= 120",
                "Select an actual ongoing source within Dispel Magic range.",
            ),
            _when(
                "_tp_dispel_source.scene_id != ''",
                [
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_tp_dispel_source.scene_id}"},
                        POSITION_EXPORTS,
                    ),
                    _set("_tp_dispel_point", "_info_position"),
                    _set("_tp_dispel_plane", "_info_scene_entity.metadata.plane"),
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_caster_state_id}"},
                        POSITION_EXPORTS,
                    ),
                    _require(
                        "_info_scene_entity.metadata.plane == _tp_dispel_plane",
                        "The actual Source is on another plane.",
                    ),
                    _set("_tp_dispel_caster", "_info_position"),
                    _call(
                        "measure_srd_information_distance",
                        {"start": "{_tp_dispel_caster}", "end": "{_tp_dispel_point}"},
                        ("_scene_distance",),
                    ),
                    _require("_scene_distance <= 120", "The actual Source is beyond Dispel range."),
                    _call(
                        "srd_information_barriers",
                        {
                            "start": "{_tp_dispel_caster}",
                            "end": "{_tp_dispel_point}",
                            "plane": "{_tp_dispel_plane}",
                            "rule": "solid",
                        },
                        ("_information_blocked",),
                    ),
                    _require(
                        "_information_blocked != true",
                        "A solid barrier blocks the actual Dispel path.",
                    ),
                ],
            ),
            _call(
                "resolve_srd_dispel_source_check",
                {
                    "caster_state": "{_caster_state_id}",
                    "caster": "{_caster_id}",
                    "source_level": "{_tp_dispel_source.spell_level}",
                    "slot_level": "{slot_level}",
                    "casting_modifier": "{casting_modifier}",
                    "casting_ability": "{casting_ability}",
                },
                ("_dispel_succeeded", "_dispel_roll"),
            ),
            _when(
                "_dispel_succeeded == true",
                [
                    _call(
                        "retire_srd_true_polymorph_object_source",
                        {
                            "target_state": "{_target_state_id}",
                            "source_id": "{source_id}",
                            "reason": "dispel",
                        },
                    )
                ],
            ),
            _set(
                "_adoption_dispel_check",
                {
                    "spell": "'true_polymorph'",
                    "source_id": "source_id",
                    "ended": "_dispel_succeeded",
                    "roll": None,
                    "dc": "10 + _tp_dispel_source.spell_level",
                },
            ),
            _when(
                "_dispel_roll != null",
                [
                    _set("_tp_dispel_roll_patch", {"roll": "_dispel_roll"}),
                    _set(
                        "_adoption_dispel_check",
                        "merge(_adoption_dispel_check, _tp_dispel_roll_patch)",
                    ),
                ],
            ),
        ],
    )


def _bind_removal(tools, changed):
    "Add one source-specific callback; preserve every existing selected and creature Dispel branch."
    kind = "true_polymorph_object"
    for name in ("cast_spell", "cast_spell_outside_combat", "cast_srd_recovery"):
        tool = changed.get(name, deepcopy(tools[name]))
        parameter = next(p for p in tool["parameters"] if p["name"] == "recovery_choice")
        if "enum_values" in parameter and kind not in parameter["enum_values"]:
            parameter["enum_values"].append(kind)
        changed[name] = tool
    recovery = changed["cast_srd_recovery"]
    for effect in recovery["effects"]:
        if (
            effect.get("set", {}).get("field")
            in ("_recovery_source_intents", "_recovery_source_owners")
            and repr(kind) not in effect["set"]["value"]
        ):
            effect["set"]["value"].append(repr(kind))
    condition = "spell == 'dispel_magic' and recovery_choice == 'true_polymorph_object'"
    if not any(e.get("condition") == condition for e in recovery["effects"]):
        recovery["effects"].append(
            _when(
                condition,
                [
                    _call(
                        "dispel_srd_true_polymorph_object_source",
                        {
                            "caster_state": "{_caster_state_id}",
                            "caster": "{_caster_id}",
                            "target_state": "{_target_state_id}",
                            "source_id": "{recovery_source_id}",
                            "slot_level": "{slot_level}",
                            "target_distance": "{target_distance}",
                            "casting_modifier": "{casting_modifier}",
                            "casting_ability": "{casting_ability}",
                        },
                        ("_adoption_dispel_check",),
                    ),
                    _set("_dispel_checks", ["_adoption_dispel_check"]),
                    _set(
                        "_spell_summary_rows",
                        [
                            {
                                "label": "'Selected Source ended'",
                                "value": "_adoption_dispel_check.ended",
                            }
                        ],
                    ),
                    _set(
                        "_spell_detail_rows",
                        [{"label": "'True Polymorph Dispel'", "value": "_adoption_dispel_check"}],
                    ),
                ],
            )
        )


def _object_damage_tool(tools):
    """Adapt only the shared object's storage suffix; mitigation remains its exact owner."""
    packet = _object_packet(tools)
    candidates = []
    for body in _walk_lists(packet["effects"]):
        starts = [
            i
            for i, e in enumerate(body)
            if e.get("set", {}).get("field") == "_object_hp_before"
            and e["set"]["value"] == "_object_profile.hp"
        ]
        for i in starts:
            candidates.append(deepcopy(body[i:]))
    if len(candidates) != 1:
        raise ValueError("True Polymorph needs the unique shared object durability suffix.")
    suffix = candidates[0]
    persist = next(
        e["on_true"] for e in suffix if e.get("condition") == "_object_damage_applied > 0"
    )
    profile = next(
        i for i, e in enumerate(persist) if e.get("set", {}).get("field") == "_object_profile"
    )
    mutation = next(i for i, e in enumerate(persist) if "spatial_mutation" in e)
    # The shared owner calculates and writes its profile once. Only storage differs
    # when the real object has an entity identity without a spatial projection.
    scene_persist = deepcopy(persist[profile + 1 : mutation + 1])
    persist[profile + 1 : mutation + 1] = [
        _set("target.srd_spell_object", "_object_profile"),
        _set("target.hp", "_object_profile.hp"),
        _when(
            "target.object_scene_id != null and target.object_scene_id != ''",
            [
                _call(
                    "srd_information_scene_entity",
                    {"subject": "{target.object_scene_id}"},
                    {"_object_scene": "_info_scene_entity"},
                ),
                _set("object_id", "target.object_scene_id"),
                *scene_persist,
            ],
        ),
    ]
    for e in persist[profile + 4 :]:
        if "call" in str(e):
            e["condition"] = "state.spatial_scene != null and (" + e["condition"] + ")"

    # Shared packet inputs become private computed locals in this storage adapter.
    def private_inputs(value):
        if isinstance(value, str):
            for field in (
                "component_1_amount",
                "component_2_amount",
                "component_1_damage_type",
                "component_2_damage_type",
                "damage_type",
                "object_id",
            ):
                value = __import__("re").sub(
                    r"(?<![\w.])" + field + r"(?!\w)", "_tp_" + field, value
                )
            return value
        if isinstance(value, list):
            return [private_inputs(item) for item in value]
        if isinstance(value, dict):
            return {key: private_inputs(item) for key, item in value.items()}
        return value

    suffix = private_inputs(suffix)
    durability = next(
        i for i, e in enumerate(suffix) if e.get("condition") == "_object_damage_applied > 0"
    )
    # Native receiving already calculated every genuine typed component and rider.
    # Reuse only the shared one-profile durability write, without mitigating twice.
    if [e.get("set", {}).get("field") for e in suffix[durability - 2 : durability]] != [
        "_object_hp_after",
        "_object_destroyed",
    ]:
        raise ValueError(
            "True Polymorph needs the exact shared object loss/destruction calculation."
        )
    suffix = suffix[durability - 2 :]
    return _tool(
        (
            "Persist the actual final object durability loss, then restore its "
            "living original before death callbacks."
        ),
        [_entity("target"), _parameter("amount", "integer", minimum=0)],
        [
            _set(
                "_tp_damage_source",
                "state.true_polymorph_object_sources[target.true_polymorph_object_source_id]",
            ),
            _require(
                "_tp_damage_source.active == true and _tp_damage_source.mode == "
                "'creature_to_object' "
                "and _tp_damage_source.target == _target_id",
                "Use this Source's actual object recipient.",
            ),
            _set("_object_profile", "target.srd_spell_object"),
            _set("_object_hp_before", "_object_profile.hp"),
            _set("_object_hp_after", "_object_hp_before"),
            _set("_object_destroyed", False),
            _set("_object_damage_rolled", "amount"),
            _set("_object_damage_applied", "min(_object_hp_before, amount)"),
            *suffix,
            _set("_hp_before", "_object_hp_before"),
            _set("_hp_after", "_object_hp_after"),
            _set("_temp_hp_before", 0),
            _set("_temp_hp_after", 0),
            _set("_hp_lost", "_object_damage_applied"),
            _when(
                "_object_destroyed == true",
                [
                    _call(
                        "retire_srd_true_polymorph_object_source",
                        {
                            "target_state": "{_target_id}",
                            "source_id": "{target.true_polymorph_object_source_id}",
                            "reason": "destroyed",
                        },
                    )
                ],
            ),
            _set("_is_alive_after", "target.is_alive == true"),
        ],
    )


def _bind_receiving(tools, changed):
    "Specialize only the actual HP transition, retaining all final typed/dealt/received effects."
    for name in (
        "apply_srd_damage",
        "apply_srd_qualifying_damage",
        "apply_srd_composite_damage",
        "apply_srd_qualifying_composite_damage",
        "execute_srd_qualified_scalar_packet",
        "execute_srd_qualified_composite_packet",
    ):
        if name not in tools:
            continue
        receiving = deepcopy(tools[name])
        if "receive_srd_true_polymorph_object_damage" in str(receiving["effects"]):
            continue
        lists = [
            body
            for body in _walk_lists(receiving["effects"])
            if any(e.get("set", {}).get("field") == "_hp_before" for e in body)
        ]
        if not lists and any(
            e.get("call")
            in ("execute_srd_qualified_scalar_packet", "execute_srd_qualified_composite_packet")
            for e in receiving["effects"]
        ):
            continue
        if len(lists) != 1:
            raise ValueError("True Polymorph needs one final native receiving body: " + name)
        effects = lists[0]
        starts = [i for i, e in enumerate(effects) if e.get("set", {}).get("field") == "_hp_before"]
        ends = [i for i, e in enumerate(effects) if e.get("set", {}).get("field") == "_hp_after"]
        if not starts and any(
            e.get("call")
            in ("execute_srd_qualified_scalar_packet", "execute_srd_qualified_composite_packet")
            for e in effects
        ):
            continue
        if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
            raise ValueError("True Polymorph needs the final native HP transition: " + name)
        start, end = starts[0], ends[0]
        transition = effects[start:end]
        effects[start:end] = [
            _set(
                "_tp_received_object",
                "target.true_polymorph_object_source_id != null and target.is_object == true",
            ),
            _when(
                "_tp_received_object == true",
                [
                    _call(
                        "receive_srd_true_polymorph_object_damage",
                        {"target": "{target}", "amount": "{_effective_damage}"},
                        (
                            "_hp_before",
                            "_hp_after",
                            "_temp_hp_before",
                            "_temp_hp_after",
                            "_hp_lost",
                            "_is_alive_after",
                            "_object_destroyed",
                        ),
                    )
                ],
                transition,
            ),
            _set("_tp_native_ended", False),
            _when(
                (
                    "target.true_polymorph_object_source_id != null and target.is_object "
                    "!= true and target.is_alive != true"
                ),
                [
                    _set("_tp_native_hp_after", "target.hp"),
                    _set("_tp_native_temp_after", "target.temp_hp"),
                    _call(
                        "retire_srd_true_polymorph_object_source",
                        {
                            "target_state": "{target}",
                            "source_id": "{target.true_polymorph_object_source_id}",
                            "reason": "creature_died",
                        },
                    ),
                    _set("_tp_native_ended", True),
                ],
            ),
        ]
        for i, e in enumerate(effects):
            field = e.get("set", {}).get("field")
            if field in ("_hp_after", "_temp_hp_after"):
                saved = "_tp_native_hp_after" if field == "_hp_after" else "_tp_native_temp_after"
                effects[i] = _when(
                    "_tp_received_object != true",
                    [_when("_tp_native_ended == true", [_set(field, saved)], [e])],
                )
            elif field in ("_hp_lost", "_is_alive_after"):
                effects[i] = _when("_tp_received_object != true", [e])
        changed[name] = receiving


def _walk_lists(effects):
    yield effects
    for e in effects:
        for key in ("on_true", "on_false"):
            if isinstance(e.get(key), list):
                yield from _walk_lists(e[key])
        if "for_each" in e:
            yield from _walk_lists(e["for_each"]["do"])


def extend_runtime(text):
    """Emit after transformations; preserve shared paid casts and their final callbacks."""
    tools = _load_runtime(text)["tools"]
    changed = {
        "prepare_srd_true_polymorph_object": _admission_tool(),
        "prepare_srd_true_polymorph_creature": _native_constructor(tools),
        "install_srd_true_polymorph_creature_body": _creature_body_tool(),
        "install_srd_true_polymorph_object_mode": _install_tool(),
        "project_srd_true_polymorph_scene_body": _scene_projection_tool(),
        "restore_srd_true_polymorph_scene_body": _scene_restoration_tool(),
        "cast_srd_true_polymorph_object_mode": _cast_tool(),
        "project_srd_true_polymorph_object_mode": _object_projection_tool(),
        "retire_srd_true_polymorph_object_source": _retirement_tool(),
        "check_srd_true_polymorph_object_source": _check_tool(),
        "refresh_srd_true_polymorph_objects": _refresh_tool(),
        "command_srd_true_polymorph_creature": _command_tool(),
        "end_srd_true_polymorph_object_concentration": _end_concentration_tool(),
        "receive_srd_true_polymorph_object_damage": _object_damage_tool(tools),
        "clear_srd_true_polymorph_object_concentration": _clear_concentration_tool(),
        "dispel_srd_true_polymorph_object_source": _dispel_tool(),
    }
    for name in ("cast_spell", "cast_spell_outside_combat"):
        cast = deepcopy(tools[name])
        names = {p["name"] for p in cast["parameters"]}
        cast["parameters"] += [deepcopy(p) for p in CAST_CHOICES if p["name"] not in names]
        # Remove our own wrapper before revisiting the final ordinary family dispatch.
        for body in _walk_lists(cast["effects"]):
            for index, e in enumerate(body):
                if (
                    e.get("condition")
                    == "spell != 'true_polymorph' or true_polymorph_mode == 'creature_to_creature'"
                ):
                    body[index] = e["on_true"][0]
        for body in list(_walk_lists(cast["effects"])):
            for index, e in enumerate(body):
                if e.get("call") != "cast_spell_transformation":
                    continue
                body[index] = _when(
                    "spell != 'true_polymorph' or true_polymorph_mode == 'creature_to_creature'",
                    [e],
                    [
                        _set("_tp_object_parameter", None),
                        _when(
                            "true_polymorph_object != null",
                            [_set("_tp_object_parameter", "_true_polymorph_object_id")],
                        ),
                        _when(
                            "_spell_source_cast_id == null or _spell_source_cast_id == ''",
                            [
                                _call(
                                    "begin_srd_spell_source_instance",
                                    {
                                        "caster_state": "{_caster_state_id}",
                                        "spell": "true_polymorph",
                                        "slot_level": "{_slot_level}",
                                    },
                                    ("_spell_source_cast_id",),
                                )
                            ],
                        ),
                        _call(
                            "cast_srd_true_polymorph_object_mode",
                            {
                                "caster_state": "{_caster_state_id}",
                                "target_state": "{_scalar_target_id}",
                                "target_character": "{_transformation_character_ref}",
                                "source_id": "{_spell_source_cast_id}",
                                "mode": "{true_polymorph_mode}",
                                "form_id": "{transformation_form}",
                                "object_id": "{_tp_object_parameter}",
                                "save_dc": "{_casting_profile.save_dc}",
                                "concentration_modifier": "{_casting_profile.concentration_sa"
                                "ve_modifier}",
                                "willing": "{target_willing}",
                                "forego_save": "{_scalar_target_id in _forego_save_targets}",
                                "target_distance": "{target_distance}",
                                "can_see": "{_caster_can_see_target}",
                                "material": "{true_polymorph_material}",
                                "size": "{true_polymorph_size}",
                                "durability": "{true_polymorph_durability}",
                                "description": "{true_polymorph_description}",
                                "body": "{true_polymorph_body}",
                            },
                            ("_spell_summary_rows", "_spell_detail_rows"),
                        ),
                    ],
                )
        changed[name] = cast
    project = deepcopy(tools["project_srd_transformation"])
    if "project_srd_true_polymorph_object_mode" not in str(project["effects"]):
        project["effects"].append(
            _when(
                "target_state.true_polymorph_object_source_id != null",
                [
                    _call(
                        "project_srd_true_polymorph_object_mode",
                        {"target_state": "{_target_state_id}"},
                    )
                ],
            )
        )
    changed["project_srd_transformation"] = project
    clock = deepcopy(tools["advance_srd_lasting_clock"])
    if "refresh_srd_true_polymorph_objects" not in str(clock["effects"]):
        index = next(
            i
            for i, e in enumerate(clock["effects"])
            if e.get("set", {}).get("field") == "lasting_state.lasting_tick"
        )
        clock["effects"].insert(
            index + 1,
            _when(
                "state.has_true_polymorph_objects == true",
                [_call("refresh_srd_true_polymorph_objects", {})],
            ),
        )
    changed["advance_srd_lasting_clock"] = clock
    end = deepcopy(tools["end_srd_concentration_node"])
    if "end_srd_true_polymorph_object_concentration" not in str(end["effects"]):
        end["effects"].insert(
            0,
            _when(
                "state.has_true_polymorph_objects == true and "
                "source_state.concentration_spell_id == 'true_polymorph'",
                [
                    _call(
                        "end_srd_true_polymorph_object_concentration",
                        {"caster_state": "{_source_state_id}"},
                    )
                ],
            ),
        )
    changed["end_srd_concentration_node"] = end
    _bind_receiving(tools, changed)
    _bind_removal(tools, changed)
    for name in ("check_srd_active_control_action", "d20_test"):
        if name in tools:
            tool = deepcopy(tools[name])
            guard = "actor_state.creature_kind != 'object'"
            if not any(p.get("condition") == guard for p in tool.get("pre_conditions", [])):
                tool.setdefault("pre_conditions", []).append(
                    {
                        "condition": guard,
                        "error": (
                            "This creature is an object and has no creature actions or ability "
                            "tests."
                        ),
                    }
                )
            changed[name] = tool
    for name in PUBLIC_TOOLS:
        for parameter in changed[name]["parameters"]:
            if parameter["name"] == "lasting_state":
                parameter["model_hidden"] = True
    _normalize(changed)
    for tool in changed.values():
        for body in _walk_lists(tool["effects"]):
            for effect in body:
                for kind in ("spatial_query", "spatial_mutation"):
                    if kind in effect:
                        effect[kind]["scene"] = "state.spatial_scene"
    return _replace_tools(text, changed)
