"""Simulacrum constructs one source-owned native duplicate without copying equipment."""

from copy import deepcopy

import yaml

if __package__:
    from .srd_control_behaviors import _when
    from .srd_scene_relations import MEASURE_EXPORTS, POSITION_EXPORTS, _tool_scene
    from .srd_timed_actor_defenses import _call, _entity, _load_runtime, _parameter, _require, _set
    from .srd_timed_senses import _optional, _replace_tools
else:
    from srd_control_behaviors import _when
    from srd_scene_relations import MEASURE_EXPORTS, POSITION_EXPORTS, _tool_scene
    from srd_timed_actor_defenses import _call, _entity, _load_runtime, _parameter, _require, _set
    from srd_timed_senses import _optional, _replace_tools

SOURCE_SHA = "46caf9afebae4016c8ee304af967edfa90b4cbcfd5b89c0619e471eaf5b41ef2"
PROFILE = {
    "family": "simulacrum_spell",
    "base_level": 7,
    "casting_time": "12_hours",
    "range_kind": "touch",
    "range_feet": 0,
    "concentration": False,
    "duration_rounds": 0,
}
CAST_CHOICES = [
    _optional("simulacrum_pile", default=""),
    _optional("simulacrum_friends", "array", [], items={"type": "string"}, max_items=64),
]
PUBLIC_TOOLS = set()
HIDDEN_SOURCE = [
    _optional("spell_cast_id", default="", model_hidden=True),
    _optional("spell_cast_owner", resolve="entity", default=None, model_hidden=True),
    _optional("actual_spell_level", "integer", -1, model_hidden=True),
]
# Copy native statistics and current resources, never identities, external effect IDs or inventory.
NATIVE_FIELDS = [
    "ability_scores",
    "constitution_modifier",
    "native_hit_dice",
    "hit_die_size",
    "hit_dice_pools",
    "attacks",
    "proficiency_bonus",
    "challenge_rating_eighths",
    "saving_throw_modifiers",
    "saving_throw_proficiencies",
    "skill_check_modifiers",
    "skill_proficiencies",
    "condition_immunities",
    "damage_immunities",
    "damage_resistances",
    "damage_vulnerabilities",
    "speed",
    "flying_speed",
    "climb_speed",
    "swim_speed",
    "melee_reach",
    "size_rank",
    "initiative_modifier",
    "base_passive_perception",
    "passive_perception",
    "darkvision_range",
    "blindsight_range",
    "truesight_range",
    "tremorsense_range",
    "languages",
    "does_not_sleep",
    "multiattack",
    "available_bonus_actions",
    "spellcasting_profile",
    "spell_definitions",
    "spell_uses_remaining",
    "prepared_spells",
    "spell_component_exemptions",
    "resources",
    "resource_maxima",
]


def validate_profiles(definitions):
    for spell, profile in definitions.items():
        if profile.get("family") == PROFILE["family"] and (
            spell != "simulacrum" or profile != PROFILE
        ):
            raise ValueError(
                "Simulacrum differs from the pinned twelve-hour native snapshot profile"
            )


def _native_qualification():
    return _tool_scene(
        "Require the actual native original; replacement/PC authority is not inferred.",
        [_entity("original_state")],
        [
            _require(
                "original_state.creature_kind == 'monster' and "
                "original_state.form_statistics_replaced != true and "
                "original_state.current_body_source_id == null and "
                "original_state.magic_jar_role == null",
                "PC or replaced/transferred Simulacrum originals remain unsupported.",
            ),
            _set(
                "_sim_eligible",
                "original_state.is_alive == true and "
                "(original_state.creature_type == 'beast' or original_state.creature_type == "
                "'humanoid')",
            ),
            _when(
                "_sim_eligible == true",
                [
                    _require(
                        "original_state.ability_scores != null and "
                        "original_state.native_hit_dice != null",
                        "The native original needs all six genuine scores and original Hit "
                        "Point Dice.",
                    ),
                    _require(
                        "original_state.max_hp >= 2 and len(original_state.inventory) == 0",
                        "The native snapshot needs positive half-maximum HP and an unequipped "
                        "original.",
                    ),
                    _require(
                        "original_state.temp_hp == 0 and original_state.exhaustion_level == 0 and "
                        "original_state.dodging != true and "
                        "(original_state.timed_condition_source_ids == null "
                        "or len(original_state.timed_condition_source_ids) == 0)",
                        "Temporary HP, exhaustion, Dodge or retained condition sources need "
                        "their own copy semantics.",
                    ),
                    _require(
                        "len(original_state.conditions) == 0 and "
                        "original_state.has_transformations != true "
                        "and original_state.has_timed_actor_defenses != true and "
                        "original_state.has_actor_spell_modifiers != true and "
                        "original_state.has_timed_senses != true",
                        "Copying active external condition/statistics effects needs their "
                        "separate source owners.",
                    ),
                ],
            ),
        ],
    )


def _geometry():
    return _tool_scene(
        "Qualify actual original proximity and the real same-size ice/snow pile.",
        [
            _entity("caster_state"),
            _entity("original_state"),
            _optional("pile", default=""),
            _parameter("finishing", "boolean"),
        ],
        [
            _call(
                "srd_information_scene_entity", {"subject": "{_caster_state_id}"}, POSITION_EXPORTS
            ),
            _set("_sim_caster_scene", "_info_scene_entity"),
            _call(
                "srd_information_scene_entity",
                {"subject": "{_original_state_id}"},
                POSITION_EXPORTS,
            ),
            _set("_sim_original_scene", "_info_scene_entity"),
            _require(
                "_sim_caster_scene.metadata.plane != null and "
                "_sim_caster_scene.metadata.plane == _sim_original_scene.metadata.plane",
                "The original must remain on the caster's actual plane.",
            ),
            _call(
                "measure_srd_information_distance",
                {"start": "{_sim_caster_scene.position}", "end": "{_sim_original_scene.position}"},
                MEASURE_EXPORTS,
            ),
            _require(
                "_scene_distance <= 10", "The original must remain within ten feet while casting."
            ),
            _when(
                "finishing == true",
                [
                    _require("_scene_distance <= 5", "Finish by touching the actual original."),
                    _call(
                        "srd_information_barriers",
                        {
                            "start": "{_sim_caster_scene.position}",
                            "end": "{_sim_original_scene.position}",
                            "plane": "{_sim_caster_scene.metadata.plane}",
                            "rule": "solid",
                        },
                        ["_information_blocked"],
                    ),
                    _require(
                        "_information_blocked != true",
                        "A solid barrier prevents touching the original.",
                    ),
                    _call("srd_information_scene_entity", {"subject": "{pile}"}, POSITION_EXPORTS),
                    _set("_sim_pile_scene", "_info_scene_entity"),
                    _require(
                        "_sim_pile_scene.metadata.material == 'ice' or "
                        "_sim_pile_scene.metadata.material == 'snow'",
                        "The admitted pile must genuinely be ice or snow.",
                    ),
                    _require(
                        "_sim_pile_scene.metadata.participant_ref == null and "
                        "_sim_pile_scene.metadata.plane == _sim_original_scene.metadata.plane",
                        "The pile must be a scene object on the original's actual plane.",
                    ),
                    _require(
                        "_sim_original_scene.footprint.kind == 'box' and "
                        "_sim_pile_scene.footprint.kind == 'box'",
                        "The pile must match the actual original's full Box body; other "
                        "geometry is unsupported.",
                    ),
                    _require(
                        "_sim_original_scene.footprint.half_extents[0] == "
                        "_sim_pile_scene.footprint.half_extents[0] and "
                        "_sim_original_scene.footprint.half_extents[1] == "
                        "_sim_pile_scene.footprint.half_extents[1] and "
                        "_sim_original_scene.height == _sim_pile_scene.height",
                        "The actual snow/ice pile must be the same size as the original.",
                    ),
                    _call(
                        "measure_srd_information_distance",
                        {
                            "start": "{_sim_caster_scene.position}",
                            "end": "{_sim_pile_scene.position}",
                        },
                        MEASURE_EXPORTS,
                    ),
                    _require("_scene_distance <= 5", "Finish by touching the real ice/snow pile."),
                    _call(
                        "srd_information_barriers",
                        {
                            "start": "{_sim_caster_scene.position}",
                            "end": "{_sim_pile_scene.position}",
                            "plane": "{_sim_caster_scene.metadata.plane}",
                            "rule": "solid",
                        },
                        ["_information_blocked"],
                    ),
                    _require(
                        "_information_blocked != true",
                        "A solid barrier prevents touching the pile.",
                    ),
                ],
            ),
        ],
    )


def _read():
    return _tool_scene(
        "Read one exact native Simulacrum source, including an already-retired record.",
        [_entity("target_state")],
        [
            _require(
                "target_state.created_spell_id == 'simulacrum' and "
                "target_state.created_individual_id == _target_state_id and "
                "_target_state_id in state.created_actor_ids",
                "The target is not an actual source-owned Simulacrum.",
            ),
            _set("_sim_record", "state.created_actor_records[_target_state_id]"),
            _require(
                f"_sim_record.spell == 'simulacrum' and _sim_record.source_sha256 == "
                f"'{SOURCE_SHA}' "
                "and _sim_record.caster == target_state.created_caster_state_id",
                "The Simulacrum must retain its exact creator and printed source.",
            ),
            _set(
                "_sim_active", "_sim_record.active == true and target_state.created_active == true"
            ),
        ],
    )


def _retire():
    return _tool_scene(
        "End only this Simulacrum; its artificial body melts and actual carried items remain.",
        [_entity("target_state"), _parameter("reason")],
        [
            _call(
                "read_srd_simulacrum_source",
                {"target_state": "{_target_state_id}"},
                ["_sim_record", "_sim_active"],
            ),
            _when(
                "_sim_active == true",
                [
                    _call(
                        "retire_srd_created_actor",
                        {"actor_state": "{_target_state_id}", "reason": "{reason}"},
                    ),
                    _set("target_state.simulacrum_melted", True),
                    _set("target_state.body_available", False),
                    _set("target_state.vital_parts_present", False),
                ],
            ),
        ],
    )


def _recast():
    return _tool_scene(
        "A committed recast destroys every actual prior Simulacrum of this caster only.",
        [_entity("caster_state")],
        [
            _set("_sim_prior_ids", []),
            _when(
                "state.created_actor_ids != null",
                [_set("_sim_prior_ids", "state.created_actor_ids")],
            ),
            {
                "for_each": {
                    "collection": "_sim_prior_ids",
                    "as": "_sim_prior_id",
                    "do": [
                        _set("_sim_prior_record", "state.created_actor_records[_sim_prior_id]"),
                        _when(
                            "_sim_prior_record.spell == 'simulacrum' and "
                            "_sim_prior_record.active == true "
                            "and _sim_prior_record.caster == _caster_state_id",
                            [
                                _call(
                                    "end_srd_simulacrum_source",
                                    {"target_state": "{_sim_prior_id}", "reason": "recast"},
                                ),
                            ],
                        ),
                    ],
                }
            },
        ],
    )


def _constructor(tools):
    """Reuse actual native preparation, with parameters populated only from the original."""
    prepare = deepcopy(tools["prepare_srd_creature"])
    prepare["pre_conditions"] = [
        e
        for e in prepare.get("pre_conditions", [])
        if "Prepare new creatures" not in e.get("error", "")
    ]
    prepare["metadata"] = {
        "category": "internal",
        "visible_to_player": False,
        "costs_action": False,
    }
    return prepare


def _install():
    params = [
        _entity("caster_state"),
        _entity("original_state"),
        _parameter("pile"),
        _parameter("slot_level", "integer", minimum=7, maximum=9),
        _parameter("friends", "array", items={"type": "string"}),
    ] + HIDDEN_SOURCE
    prepare_params = {
        "creature_id": "{pile}",
        "name": "Simulacrum",
        "source": f"Simulacrum p162-163 {SOURCE_SHA}",
        "creature_type": "construct",
        "armor_class": "{original_state.armor_class}",
        "hit_points": "{_sim_max_hp}",
        "speed": "{original_state.speed}",
        "size_rank": "{original_state.size_rank}",
        "initiative_modifier": "{original_state.initiative_modifier}",
        "proficiency_bonus": "{original_state.proficiency_bonus}",
        "melee_reach": "{original_state.melee_reach}",
        **{
            f"{a}_score": "{original_state.ability_scores." + a + "}"
            for a in ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma")
        },
        **{
            f"{a}_save": "{original_state.saving_throw_modifiers." + a + "}"
            for a in ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma")
        },
        "native_hit_dice_count": "{original_state.native_hit_dice.count}",
        "native_hit_die_size": "{original_state.native_hit_dice.size}",
    }
    copied = [
        _when(
            f"original_state.{field} != null",
            [
                _set(f"duplicate_state.{field}", f"original_state.{field}"),
            ],
        )
        for field in NATIVE_FIELDS
    ]
    return {
        "install_srd_simulacrum": _tool_scene(
            "Finish the native original's actual game-stat snapshot at this committed casting.",
            params,
            [
                _call(
                    "qualify_srd_simulacrum_original",
                    {"original_state": "{_original_state_id}"},
                    ["_sim_eligible"],
                ),
                _call(
                    "qualify_srd_simulacrum_geometry",
                    {
                        "caster_state": "{_caster_state_id}",
                        "original_state": "{_original_state_id}",
                        "pile": "{pile}",
                        "finishing": True,
                    },
                    ["_sim_pile_scene"],
                ),
                _when(
                    "spell_cast_id != ''",
                    [
                        _require(
                            "spell_cast_owner != null and _spell_cast_owner_id == "
                            "_caster_state_id and "
                            "caster_state.last_spell_source_cast_id == spell_cast_id and "
                            "caster_state.last_spell_source_spell == 'simulacrum' and "
                            "actual_spell_level == slot_level",
                            "The installer requires the genuine committed caster, source "
                            "identity and slot.",
                        ),
                    ],
                ),
                _call("retire_srd_prior_simulacra", {"caster_state": "{_caster_state_id}"}),
                _when(
                    "_sim_eligible == true",
                    [
                        _set("_sim_max_hp", "original_state.max_hp // 2"),
                        _call("prepare_srd_simulacrum_native_body", prepare_params),
                        _call(
                            "snapshot_srd_simulacrum_body",
                            {
                                "caster_state": "{_caster_state_id}",
                                "original_state": "{_original_state_id}",
                                "duplicate_state": "{pile}",
                                "slot_level": "{slot_level}",
                                "friends": "{friends}",
                                "spell_cast_id": "{spell_cast_id}",
                                "spell_cast_owner": "{_caster_state_id}",
                                "actual_spell_level": "{actual_spell_level}",
                            },
                        ),
                        _set(
                            "_sim_meta_patch",
                            {
                                "participant_ref": "pile",
                                "creature_type": "'construct'",
                                "is_object": False,
                                "source_ref": "'simulacrum'",
                                "source_caster": "_caster_state_id",
                                "srd_spell_object": None,
                            },
                        ),
                        _set("_sim_metadata", "merge(_sim_pile_scene.metadata, _sim_meta_patch)"),
                        {
                            "spatial_mutation": {
                                "scene": "state.spatial_scene",
                                "map_id": "{state.spatial_scene.map.id}",
                                "expected_revision": "{state.spatial_scene.map.revision}",
                                "target": {"kind": "entity", "id": "{pile}"},
                                "patch": {"metadata": "{_sim_metadata}", "movement": "blocking"},
                            },
                            "store_as": "_sim_mutation",
                        },
                    ],
                ),
                _set(
                    "_spell_summary_rows",
                    [{"label": "'Simulacrum created'", "value": "_sim_eligible"}],
                ),
                _set("_spell_detail_rows", []),
            ],
        ),
        "snapshot_srd_simulacrum_body": _tool_scene(
            "Copy only native statistics/resources, retaining one new source-owned body identity.",
            [
                _entity("caster_state"),
                _entity("original_state"),
                _entity("duplicate_state"),
                _parameter("slot_level", "integer"),
                _parameter("friends", "array", items={"type": "string"}),
            ]
            + HIDDEN_SOURCE,
            [
                _set("_sim_source_owner", None),
                _when(
                    "spell_cast_id != ''",
                    [
                        _require(
                            "spell_cast_owner != null and _spell_cast_owner_id == "
                            "_caster_state_id and "
                            "caster_state.last_spell_source_cast_id == spell_cast_id and "
                            "caster_state.last_spell_source_spell == 'simulacrum' and "
                            "actual_spell_level == slot_level",
                            "A nonblank cast identity must come from the actual committed "
                            "Simulacrum caster.",
                        ),
                        _set("_sim_source_owner", "_caster_state_id"),
                    ],
                ),
                *copied,
                _set("duplicate_state.hp", "min(original_state.hp, duplicate_state.max_hp)"),
                _set(
                    "duplicate_state.bloodied", "duplicate_state.hp <= duplicate_state.max_hp // 2"
                ),
                _set("duplicate_state.temp_hp", 0),
                _set("duplicate_state.inventory", []),
                _set("duplicate_state.spell_materials", []),
                _set("duplicate_state.held_items", []),
                _set("duplicate_state.created_spell_id", "'simulacrum'"),
                _set("duplicate_state.created_caster_state_id", "_caster_state_id"),
                _set("duplicate_state.created_individual_id", "_duplicate_state_id"),
                _set("duplicate_state.created_active", True),
                _set("duplicate_state.simulacrum_original", "_original_state_id"),
                _set("duplicate_state.simulacrum_friends", "friends"),
                _set("duplicate_state.simulacrum_melted", False),
                _set("duplicate_state.body_available", True),
                _set("duplicate_state.vital_parts_present", True),
                _set("duplicate_state.death_tick", None),
                _call(
                    "record_srd_creature_relationship",
                    {
                        "actor_state": "{_duplicate_state_id}",
                        "toward_state": "{_caster_state_id}",
                        "attitude": "friendly",
                        "is_ally": False,
                    },
                ),
                {
                    "for_each": {
                        "collection": "friends",
                        "as": "_sim_friend",
                        "do": [
                            _call(
                                "record_srd_creature_relationship",
                                {
                                    "actor_state": "{_duplicate_state_id}",
                                    "toward_state": "{_sim_friend}",
                                    "attitude": "friendly",
                                    "is_ally": False,
                                },
                            ),
                        ],
                    }
                },
                _when("state.lasting_tick == null", [_set("state.lasting_tick", 0)]),
                _set("_sim_started", "state.lasting_tick"),
                _when("state.created_actor_ids == null", [_set("state.created_actor_ids", [])]),
                _when(
                    "state.created_actor_records == null", [_set("state.created_actor_records", {})]
                ),
                _when(
                    "state.created_actor_dropped_items == null",
                    [_set("state.created_actor_dropped_items", [])],
                ),
                _set("_sim_created_ids", "state.created_actor_ids"),
                {"list_push": {"list": "_sim_created_ids", "value": "_duplicate_state_id"}},
                _set("state.created_actor_ids", "_sim_created_ids"),
                _set(
                    "state.created_actor_records.{_duplicate_state_id}",
                    {
                        "caster": "_caster_state_id",
                        "spell": "'simulacrum'",
                        "individual": "_duplicate_state_id",
                        "active": True,
                        "disappearance": None,
                        "original": "_original_state_id",
                        "source_sha256": f"'{SOURCE_SHA}'",
                        "source_page": 163,
                        "spell_level": "slot_level",
                        "cast_id": "spell_cast_id",
                        "source_caster_id": None,
                        "started_tick": "_sim_started",
                        "expires_tick": None,
                        "dispel_ongoing": True,
                        "ongoing_duration": False,
                    },
                ),
                _when(
                    "spell_cast_id != ''",
                    [
                        _set(
                            "state.created_actor_records.{_duplicate_state_id}.source_caster_id",
                            "_caster_state_id",
                        ),
                    ],
                ),
            ],
        ),
    }


def extend_runtime(text):
    """Emit one actual native-construction family before the longer-casting and final owners."""
    tools = _load_runtime(text)["tools"]
    changed = _install() | {
        "qualify_srd_simulacrum_original": _native_qualification(),
        "qualify_srd_simulacrum_geometry": _geometry(),
        "read_srd_simulacrum_source": _read(),
        "end_srd_simulacrum_source": _retire(),
        "retire_srd_prior_simulacra": _recast(),
        "prepare_srd_simulacrum_native_body": _constructor(tools),
    }
    for name in ("cast_spell", "cast_spell_outside_combat"):
        if name not in tools:
            continue
        cast = deepcopy(tools[name])
        cast["parameters"] = [
            p for p in cast["parameters"] if p["name"] not in {c["name"] for c in CAST_CHOICES}
        ] + deepcopy(CAST_CHOICES)
        branch = "_spell_definition.family == 'simulacrum_spell' and _spell_cast_failed != true"
        cast["effects"] = [e for e in cast["effects"] if e.get("condition") != branch]
        insertion = next(
            i
            for i, e in enumerate(cast["effects"])
            if e.get("set", {}).get("field") == "_spell_card_summary"
        )
        cast["effects"].insert(
            insertion,
            _when(
                branch,
                [
                    _when(
                        "_spell_source_cast_id == ''",
                        [
                            _call(
                                "begin_srd_spell_source_instance",
                                {
                                    "caster_state": "{_caster_state_id}",
                                    "spell": "simulacrum",
                                    "slot_level": "{_slot_level}",
                                },
                                ["_spell_source_cast_id"],
                            ),
                        ],
                    ),
                    _call(
                        "install_srd_simulacrum",
                        {
                            "caster_state": "{_caster_state_id}",
                            "original_state": "{_target_id}",
                            "pile": "{simulacrum_pile}",
                            "slot_level": "{_slot_level}",
                            "friends": "{simulacrum_friends}",
                            "spell_cast_id": "{_spell_source_cast_id}",
                            "spell_cast_owner": "{_caster_state_id}",
                            "actual_spell_level": "{_slot_level}",
                        },
                        ["_spell_summary_rows", "_spell_detail_rows"],
                    ),
                    _call("observe_srd_simulacrum_channels", {}),
                ],
            ),
        )
        changed[name] = cast
    text = _replace_tools(text, changed)
    if "      simulacrum_spell:" not in text:
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
        text = text.replace(
            "      timed_senses:",
            "".join(
                "      " + line for line in yaml.safe_dump(schema, sort_keys=False).splitlines(True)
            )
            + "\n      timed_senses:",
            1,
        )
    return text
