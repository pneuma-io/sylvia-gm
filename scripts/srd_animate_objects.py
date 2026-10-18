"""Animate admitted objects in place, retaining their original physical authority."""

from copy import deepcopy

if __package__:
    from .srd_divination_spells import _action
    from .srd_scene_relations import POSITION_EXPORTS, _snapshot, _tool_scene
    from .srd_spell_object_targets import _lookup_object, _validate_profile
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _tool,
        _when,
    )
    from .srd_timed_senses import _loop, _optional, _public, _push, _replace_tools
else:
    from srd_divination_spells import _action
    from srd_scene_relations import POSITION_EXPORTS, _snapshot, _tool_scene
    from srd_spell_object_targets import _lookup_object, _validate_profile
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _parameter,
        _require,
        _set,
        _tool,
        _when,
    )
    from srd_timed_senses import _loop, _optional, _public, _push, _replace_tools

PROFILE = {
    "family": "animate_objects",
    "base_level": 5,
    "casting_time": "action",
    "range_kind": "distance",
    "range_feet": 120,
    "concentration": True,
    "duration_rounds": 10,
    "command_range_feet": 500,
}
SOURCE_PAGES = {
    108: "eec00bdb76a6e76d9377dae2ab088a0a8b2c794db28e0e223fd3f4a38f203bfa",
    109: "6bb4bfd81c3db692194c376bee7d02f5c0e9967a2f0374116dcd25d9ae381c28",
}
SOURCE = "sylvia.srd-lite.bg3:spells:p109:" + SOURCE_PAGES[109]
PUBLIC_TOOLS = {
    "record_srd_object_attachment",
    "command_srd_animated_objects",
    "begin_srd_animated_object_slam",
}
CAST_CHOICES = [
    _optional(
        "animated_object_ids",
        "array",
        [],
        items={"type": "string"},
        unique_items=True,
        max_items=10,
    )
]
RESULTS = ("_spell_summary_rows", "_spell_detail_rows")


def schema():
    properties = {
        key: {
            "type": "boolean"
            if type(value) is bool
            else "integer"
            if type(value) is int
            else "string",
            "enum": [value],
        }
        for key, value in PROFILE.items()
        if key != "family"
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def validate_profiles(definitions):
    for spell, row in definitions.items():
        if row.get("family") == "animate_objects" and (
            spell != "animate_objects" or row != PROFILE
        ):
            raise ValueError("Animate Objects differs from the printed fixed profile.")


def _patch(object_id, metadata, **fields):
    return [
        *_snapshot(),
        {
            "spatial_mutation": {
                "scene": "state.spatial_scene",
                "map_id": "{state.spatial_scene.map.id}",
                "expected_revision": "{_info_snapshot.geometry_revision}",
                "target": {"kind": "entity", "id": object_id},
                "patch": {"metadata": metadata, **fields},
            },
            "store_as": "_ao_scene_updated",
        },
    ]


def _attachment():
    return _public(
        _tool_scene(
            "Record whether this existing object is physically fixed to a surface.",
            [_parameter("object_id"), _parameter("fixed_to_surface", "boolean")],
            [
                *_lookup_object(),
                *_validate_profile(),
                _require(
                    "combat.encounter_active != true and combat.pending_action == null",
                    "Admit physical object facts outside a pending combat action.",
                ),
                _set("_ao_attachment", {"fixed_to_surface": "fixed_to_surface"}),
                _set("_ao_metadata", "merge(_object_scene.metadata, _ao_attachment)"),
                *_patch("{object_id}", "{_ao_metadata}"),
            ],
        ),
        "exploration",
        "Object attachment",
        [("Object", "object_id")],
    )


def _distance():
    return _tool_scene(
        "Measure exact supported Box-body separation on the actual shared plane.",
        [_parameter("first"), _parameter("second")],
        [
            _call("srd_information_scene_entity", {"subject": "{first}"}, POSITION_EXPORTS),
            _set("_ao_first", "_info_scene_entity"),
            _call("srd_information_scene_entity", {"subject": "{second}"}, POSITION_EXPORTS),
            _require(
                "_ao_first.footprint.kind == 'box' and "
                "_info_scene_entity.footprint.kind == 'box' and "
                "_ao_first.metadata.plane != null and "
                "_ao_first.metadata.plane == _info_scene_entity.metadata.plane",
                "This body qualifier supports actual same-plane Box bodies.",
            ),
            *[
                _set(
                    f"_ao_gap_{axis}",
                    f"max(0, abs(_ao_first.position[{axis}] - _info_scene_entity.position[{axis}]) "
                    f"- _ao_first.footprint.half_extents[{axis}] "
                    f"- _info_scene_entity.footprint.half_extents[{axis}])",
                )
                for axis in range(2)
            ],
            _set(
                "_ao_gap_2",
                "max(0, _ao_first.position[2] - "
                "(_info_scene_entity.position[2] + _info_scene_entity.height), "
                "_info_scene_entity.position[2] - (_ao_first.position[2] + _ao_first.height))",
            ),
            _set("_ao_distance", "(_ao_gap_0 ** 2 + _ao_gap_1 ** 2 + _ao_gap_2 ** 2) ** 0.5"),
        ],
    )


def _qualification():
    return _tool_scene(
        "Qualify an actual unheld, nonmagical object without accepting an eligibility verdict.",
        [_entity("caster_state"), _parameter("object_id")],
        [
            *_lookup_object(),
            *_validate_profile(),
            _require(
                "_object_scene.active != false and "
                "_object_scene.metadata.is_object == true and "
                "_object_scene.metadata.creature_type == null and "
                "_object_scene.metadata.srd_object_material.magical == false and "
                "_object_scene.metadata.fixed_to_surface == false and "
                "_object_scene.metadata.worn_by == null and "
                "_object_scene.metadata.carried_by == null",
                "Choose an admitted nonmagical object that is not fixed, worn or carried.",
            ),
            _call(
                "measure_srd_animated_body_distance",
                {"first": "{_caster_state_id}", "second": "{object_id}"},
                ("_ao_distance",),
            ),
            _require("_ao_distance <= 120", "This existing object is beyond the spell's range."),
            _call(
                "srd_information_scene_entity", {"subject": "{_caster_state_id}"}, POSITION_EXPORTS
            ),
            _set("_ao_cast_origin", "_info_position"),
            _call(
                "srd_information_barriers",
                {
                    "start": "{_ao_cast_origin}",
                    "end": "{_object_scene.position}",
                    "rule": "solid",
                },
                ("_information_blocked",),
            ),
            _require(
                "_information_blocked != true", "The selected object needs a clear solid path."
            ),
            _set("_ao_sizes", {"tiny": 0, "small": 1, "medium": 2, "large": 3}),
            _set("_ao_size", "_ao_sizes[_object_profile.size]"),
            _set("_ao_weight", 1),
            _when("_ao_size == 3", [_set("_ao_weight", 2)]),
            _set("_ao_hit_points", 10),
            _when("_ao_size == 3", [_set("_ao_hit_points", 20)]),
        ],
    )


def _prepare(tools):
    preparation = deepcopy(tools["prepare_srd_creature"])
    preparation["description"] = (
        "Prepare only the fixed Animated Object creature during its real cast."
    )
    preparation["pre_conditions"] = [
        p
        for p in preparation["pre_conditions"]
        if p["condition"] != "combat.encounter_active != true and combat.turn_active != true"
    ]
    preparation["metadata"] = {
        "category": "internal",
        "visible_to_player": False,
        "costs_action": False,
    }
    preparation["parameters"].append(_parameter("source_id"))
    preparation["parameters"].append(_optional("lasting_state", default="state", resolve="entity"))
    preparation["effects"].insert(
        0,
        _require(
            "source_id != '' and "
            "lasting_state.animated_object_sources[source_id].installed == true",
            "Only a committed Animate Objects source can prepare this fixed actor.",
        ),
    )
    return preparation


def _one():
    return _tool_scene(
        "Animate this original object body without creating a second spatial identity.",
        [
            _entity("caster_state"),
            _parameter("object_id"),
            _parameter("source_id"),
            _parameter("slot_level", "integer"),
            _parameter("casting_modifier", "integer"),
            _parameter("casting_attack_bonus", "integer"),
            _parameter("casting_proficiency_bonus", "integer"),
        ],
        [
            _call(
                "qualify_srd_animate_object",
                {
                    "caster_state": "{_caster_state_id}",
                    "object_id": "{object_id}",
                },
                ("_object_scene", "_object_profile", "_ao_size", "_ao_hit_points"),
            ),
            _call(
                "record_srd_result_text",
                {"text": "animated-{source_id}-{object_id}"},
                ("_recorded_result_text",),
            ),
            _set("_ao_actor_id", "_recorded_result_text"),
            _call(
                "prepare_srd_animated_object_actor",
                {
                    "source_id": "{source_id}",
                    "creature_id": "{_ao_actor_id}",
                    "name": "Animated Object",
                    "source": SOURCE,
                    "armor_class": 15,
                    "creature_type": "construct",
                    "hit_points": "{_ao_hit_points}",
                    "speed": 30,
                    "size_rank": "{_ao_size}",
                    "initiative_modifier": 0,
                    "proficiency_bonus": "{casting_proficiency_bonus}",
                    "damage_immunities": ["poison", "psychic"],
                    **{
                        a + "_score": v
                        for a, v in zip(
                            (
                                "strength",
                                "dexterity",
                                "constitution",
                                "intelligence",
                                "wisdom",
                                "charisma",
                            ),
                            (16, 10, 10, 3, 3, 1),
                            strict=True,
                        )
                    },
                    **{
                        a + "_save": v
                        for a, v in zip(
                            (
                                "strength",
                                "dexterity",
                                "constitution",
                                "intelligence",
                                "wisdom",
                                "charisma",
                            ),
                            (3, 0, 0, -4, -4, -5),
                            strict=True,
                        )
                    },
                    "skill_proficiencies": [],
                    "saving_throw_proficiencies": [],
                },
            ),
            _call(
                "finish_srd_animated_object_actor",
                {
                    "actor_state": "{_ao_actor_id}",
                    "caster_state": "{_caster_state_id}",
                    "object_id": "{object_id}",
                    "source_id": "{source_id}",
                    "slot_level": "{slot_level}",
                    "casting_modifier": "{casting_modifier}",
                    "casting_attack_bonus": "{casting_attack_bonus}",
                },
            ),
            _set(
                "_ao_metadata_patch",
                {
                    "participant_ref": "_ao_actor_id",
                    "is_object": False,
                    "creature_type": "'construct'",
                    "animated_object_source_id": "source_id",
                },
            ),
            _set("_ao_metadata", "merge(_object_scene.metadata, _ao_metadata_patch)"),
            *_patch("{object_id}", "{_ao_metadata}"),
        ],
    )


def _finish():
    return _tool_scene(
        "Save exact native statistics, ownership and original object metadata.",
        [
            _entity("actor_state"),
            _entity("caster_state"),
            _parameter("object_id"),
            _parameter("source_id"),
            _parameter("slot_level", "integer"),
            _parameter("casting_modifier", "integer"),
            _parameter("casting_attack_bonus", "integer"),
        ],
        [
            _call("srd_information_scene_entity", {"subject": "{object_id}"}, POSITION_EXPORTS),
            _set("actor_state.animated_object_source_id", "source_id"),
            _set("actor_state.animated_object_id", "object_id"),
            _set("actor_state.animated_object_active", True),
            _set("actor_state.animated_object_command", "'dodge'"),
            _set("actor_state.animated_command_turn", -1),
            _set("actor_state.created_spell_id", "'animate_objects'"),
            _set("actor_state.created_caster_state_id", "_caster_state_id"),
            _set("actor_state.created_individual_id", "_actor_state_id"),
            _set("actor_state.created_active", True),
            _set("actor_state.summon_caster_state_id", "_caster_state_id"),
            _set("actor_state.blindsight_feet", 30),
            _set("actor_state.passive_perception", 6),
            _set(
                "actor_state.condition_immunities",
                ["charmed", "exhaustion", "frightened", "paralyzed", "poisoned"],
            ),
            _set("actor_state.languages", []),
            _set("actor_state.created_understood_languages", "caster_state.languages"),
            _set("actor_state.animated_attack_bonus", "casting_attack_bonus"),
            _set("actor_state.animated_slam_dice", "slot_level - 4"),
            _set("actor_state.animated_slam_die", "'d4'"),
            _set("actor_state.animated_slam_modifier", 3),
            _when(
                "actor_state.size_rank == 3",
                [
                    _set("actor_state.animated_slam_dice", "slot_level - 3"),
                    _set("actor_state.animated_slam_die", "'d6'"),
                    _set("actor_state.animated_slam_modifier", "3 + casting_modifier"),
                ],
            ),
            _set("actor_state.animated_slam_expression", None),
            *[
                _when(
                    f"actor_state.size_rank <= 2 and slot_level == {slot}",
                    [
                        _set("actor_state.animated_slam_expression", repr(expression)),
                    ],
                )
                for slot, expression in ((5, "d4"), (6, "2d4"))
            ],
            *[
                _when(
                    f"actor_state.size_rank == 3 and slot_level == {slot}",
                    [
                        _set("actor_state.animated_slam_expression", repr(expression)),
                    ],
                )
                for slot, expression in ((5, "2d6"), (6, "3d6"), (8, "5d6"))
            ],
            _set(
                "state.animated_object_bodies.{_actor_state_id}",
                {
                    "source_id": "source_id",
                    "caster": "_caster_state_id",
                    "object_id": "object_id",
                    "actor": "_actor_state_id",
                    "active": True,
                    "original_metadata": "_info_scene_entity.metadata",
                },
            ),
            _when(
                "state.created_actor_ids == null",
                [
                    _set("state.created_actor_ids", []),
                    _set("state.created_actor_records", {}),
                ],
            ),
            _set("_ao_created_ids", "state.created_actor_ids"),
            _push("_ao_created_ids", "_actor_state_id"),
            _set("state.created_actor_ids", "_ao_created_ids"),
            _set(
                "state.created_actor_records.{_actor_state_id}",
                {
                    "caster": "_caster_state_id",
                    "spell": "'animate_objects'",
                    "individual": "_actor_state_id",
                    "active": True,
                    "disappearance": "''",
                    "body_source_id": "object_id",
                    "spell_level": "slot_level",
                },
            ),
            _set("state.has_created_actors", True),
            _when(
                "combat.encounter_active == true",
                [
                    _call(
                        "register_srd_summon_in_initiative",
                        {
                            "caster_state": "{_caster_state_id}",
                            "summon_state": "{_actor_state_id}",
                            "lasting_state": "state",
                        },
                    ),
                ],
            ),
        ],
    )


def _cast():
    return _tool_scene(
        "Commit weighted actual object recipients and their one-minute concentration source.",
        [
            _entity("caster_state"),
            _parameter("spell_cast_id"),
            _parameter("slot_level", "integer"),
            _parameter("casting_modifier", "integer"),
            _parameter("casting_attack_bonus", "integer"),
            _parameter("casting_proficiency_bonus", "integer"),
            _parameter("concentration_modifier", "integer"),
            *deepcopy(CAST_CHOICES),
        ],
        [
            _require(
                "spell_cast_id != '' and "
                "caster_state.last_spell_source_cast_id == spell_cast_id and "
                "caster_state.last_spell_source_spell == 'animate_objects'",
                "Animate Objects requires its authentic committed paid source.",
            ),
            _require("len(animated_object_ids) > 0", "Choose the actual objects to animate."),
            _set("_ao_weight_total", 0),
            _loop(
                "animated_object_ids",
                "_ao_object_id",
                [
                    _call(
                        "qualify_srd_animate_object",
                        {
                            "caster_state": "{_caster_state_id}",
                            "object_id": "{_ao_object_id}",
                        },
                        ("_ao_weight",),
                    ),
                    _set("_ao_weight_total", "_ao_weight_total + _ao_weight"),
                ],
            ),
            _require(
                "_ao_weight_total <= casting_modifier",
                "Medium-or-smaller costs one object, Large two; the limit is the real modifier.",
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
                    ),
                ],
            ),
            _when(
                "state.animated_object_source_ids == null",
                [
                    _set("state.animated_object_source_ids", []),
                    _set("state.animated_object_sources", {}),
                    _set("state.animated_object_bodies", {}),
                ],
            ),
            _set("_ao_source_ids", "state.animated_object_source_ids"),
            _require(
                "spell_cast_id not in _ao_source_ids", "This cast source was already installed."
            ),
            _push("_ao_source_ids", "spell_cast_id"),
            _set("state.animated_object_source_ids", "_ao_source_ids"),
            _set(
                "state.animated_object_sources.{spell_cast_id}",
                {
                    "installed": True,
                    "caster_state_id": "_caster_state_id",
                    "cast_id": "spell_cast_id",
                    "spell_id": "'animate_objects'",
                    "spell_level": "slot_level",
                    "started_tick": "state.lasting_tick",
                    "expires_tick": "state.lasting_tick + 20",
                    "actors": [],
                    "ongoing_duration": True,
                    "suppressed": False,
                },
            ),
            _set("caster_state.concentration_spell_id", "'animate_objects'"),
            _set("caster_state.concentration_cast_id", "spell_cast_id"),
            _set("caster_state.concentration_effect_family", "'animate_objects'"),
            _set("caster_state.concentration_target_state_id", "_caster_state_id"),
            _set("caster_state.concentration_target_state_ids", []),
            _set("caster_state.concentration_started_tick", "state.lasting_tick"),
            _set("caster_state.concentration_expires_tick", "state.lasting_tick + 20"),
            _set("caster_state.concentration_save_modifier", "concentration_modifier"),
            _set("_ao_actor_ids", []),
            _loop(
                "animated_object_ids",
                "_ao_object_id",
                [
                    _call(
                        "animate_srd_object_body",
                        {
                            "caster_state": "{_caster_state_id}",
                            "object_id": "{_ao_object_id}",
                            "source_id": "{spell_cast_id}",
                            "slot_level": "{slot_level}",
                            "casting_modifier": "{casting_modifier}",
                            "casting_attack_bonus": "{casting_attack_bonus}",
                            "casting_proficiency_bonus": "{casting_proficiency_bonus}",
                        },
                        ("_ao_actor_id",),
                    ),
                    _push("_ao_actor_ids", "_ao_actor_id"),
                ],
            ),
            _set("state.animated_object_sources.{spell_cast_id}.actors", "_ao_actor_ids"),
            _set("state.has_animated_objects", True),
            _call(
                "record_srd_spell_target_result",
                {
                    "target": "{_caster_state_id}",
                    "brief": "Objects animated",
                    "detail": (
                        "Actual original objects: {animated_object_ids}; "
                        "weighted count {_ao_weight_total}."
                    ),
                },
                ("_spell_target_summary", "_spell_target_detail"),
            ),
            _set("_spell_summary_rows", ["_spell_target_summary"]),
            _set("_spell_detail_rows", ["_spell_target_detail"]),
        ],
    )


def _retire_actor():
    return _tool_scene(
        "Revert the same physical body and carry only actual remaining damage into its object HP.",
        [_entity("actor_state"), _optional("remaining_damage", "integer", 0, minimum=0)],
        [
            _require(
                "actor_state.created_spell_id == 'animate_objects'",
                "Reversion requires this actual animated object actor.",
            ),
            _set("_ao_body", "state.animated_object_bodies[_actor_state_id]"),
            _when(
                "_ao_body.active == true",
                [
                    _call(
                        "srd_information_scene_entity",
                        {"subject": "{_ao_body.object_id}"},
                        POSITION_EXPORTS,
                    ),
                    _require(
                        "_info_scene_entity.id == _ao_body.object_id and "
                        "_info_scene_entity.metadata.participant_ref == _actor_state_id and "
                        "_info_scene_entity.metadata.animated_object_source_id "
                        "== _ao_body.source_id",
                        "Restore only the exact original physical body.",
                    ),
                    _set("_ao_original_profile", "_ao_body.original_metadata.srd_spell_object"),
                    _set("_ao_object_hp", "max(0, _ao_original_profile.hp - remaining_damage)"),
                    _set(
                        "_ao_hp_patch", {"hp": "_ao_object_hp", "destroyed": "_ao_object_hp == 0"}
                    ),
                    _set("_ao_restored_profile", "merge(_ao_original_profile, _ao_hp_patch)"),
                    _set(
                        "_ao_profile_patch",
                        {
                            "srd_spell_object": "_ao_restored_profile",
                            "participant_ref": None,
                            "is_object": True,
                            "creature_type": None,
                            "animated_object_source_id": None,
                        },
                    ),
                    _set(
                        "_ao_restored_metadata",
                        "merge(_info_scene_entity.metadata, _ao_profile_patch)",
                    ),
                    *_patch("{_ao_body.object_id}", "{_ao_restored_metadata}"),
                    _set("state.animated_object_bodies.{_actor_state_id}.active", False),
                    _set("state.created_actor_records.{_actor_state_id}.active", False),
                    _set(
                        "state.created_actor_records.{_actor_state_id}.disappearance", "'reverted'"
                    ),
                    _set("actor_state.animated_object_active", False),
                    _set("actor_state.created_active", False),
                    _set("actor_state.is_alive", False),
                ],
            ),
        ],
    )


def _retire_source():
    return _tool(
        "End exactly this caster's installed animation source and restore all remaining bodies.",
        [_entity("caster_state"), _parameter("source_id")],
        [
            _set("_ao_ending_source", "state.animated_object_sources[source_id]"),
            _require(
                "_ao_ending_source.caster_state_id == _caster_state_id",
                "This source belongs to another caster.",
            ),
            _when(
                "_ao_ending_source.installed == true",
                [
                    _set("_ao_ending_actor_ids", "_ao_ending_source.actors"),
                    _set("state.animated_object_sources.{source_id}.installed", False),
                    _loop(
                        "_ao_ending_actor_ids",
                        "_ao_ending_actor",
                        [
                            _call(
                                "revert_srd_animated_object_actor",
                                {"actor_state": "{_ao_ending_actor}"},
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _refresh():
    check = _tool(
        "End this authentic source at expiry or after its caster loses its concentration.",
        [_entity("caster_state"), _parameter("source_id")],
        [
            _set("_ao_current_source", "state.animated_object_sources[source_id]"),
            _when(
                "_ao_current_source.installed == true and "
                "(state.lasting_tick + 0.000000001 >= _ao_current_source.expires_tick or "
                "caster_state.concentration_cast_id != source_id or "
                "caster_state.concentration_spell_id != 'animate_objects' or "
                "caster_state.is_alive != true)",
                [
                    _call(
                        "retire_srd_animate_object_source",
                        {
                            "caster_state": "{_caster_state_id}",
                            "source_id": "{source_id}",
                        },
                    ),
                    _when(
                        "caster_state.concentration_cast_id == source_id",
                        [
                            _call(
                                "end_srd_concentration_node",
                                {
                                    "source_state": "{_caster_state_id}",
                                    "target_state": "{_caster_state_id}",
                                },
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    refresh = _tool(
        "Refresh authentic object source lifetimes.",
        [],
        [
            _set("_ao_refresh_ids", "state.animated_object_source_ids"),
            _loop(
                "_ao_refresh_ids",
                "_ao_refresh_id",
                [
                    _set("_ao_refresh_record", "state.animated_object_sources[_ao_refresh_id]"),
                    _when(
                        "_ao_refresh_record.installed == true",
                        [
                            _call(
                                "refresh_srd_animate_object_source",
                                {
                                    "caster_state": "{_ao_refresh_record.caster_state_id}",
                                    "source_id": "{_ao_refresh_id}",
                                },
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    return {"refresh_srd_animate_object_source": check, "refresh_srd_animated_objects": refresh}


def _get_actor():
    return _tool_scene(
        "Authenticate this actual living Construct and its original animated object body.",
        [_entity("actor_state")],
        [
            _call("refresh_srd_animated_objects", {}),
            _set(
                "_ao_source", "state.animated_object_sources[actor_state.animated_object_source_id]"
            ),
            _set("_ao_body", "state.animated_object_bodies[_actor_state_id]"),
            _require(
                "actor_state.animated_object_active == true and actor_state.created_active == true "
                "and actor_state.created_spell_id == 'animate_objects' and "
                "actor_state.is_alive == true and actor_state.hp > 0 and "
                "_ao_source.installed == true and _ao_source.suppressed != true and "
                "_ao_body.active == true and _ao_body.actor == _actor_state_id and "
                "_ao_body.source_id == _ao_source.cast_id",
                "Choose an actual active unsuppressed animated object.",
            ),
            _call(
                "srd_information_scene_entity", {"subject": "{_actor_state_id}"}, POSITION_EXPORTS
            ),
            _require(
                "_info_scene_entity.id == _ao_body.object_id and "
                "_info_scene_entity.metadata.participant_ref == _actor_state_id",
                "This actor must use the same original object proxy.",
            ),
        ],
    )


def _command():
    payment = deepcopy(_action(bonus=True))
    for effect in payment:
        for child in effect.get("on_true", []):
            if child.get("call") == "consume_srd_bonus_action":
                child["params"]["bonus_action"] = "animate_objects"
    return _public(
        _tool_scene(
            "Use one real Bonus Action to mentally issue the same order "
            "to selected actual objects.",
            [
                _entity("caster_state"),
                _parameter(
                    "actor_ids",
                    "array",
                    items={"type": "string"},
                    min_items=1,
                    max_items=10,
                    unique_items=True,
                ),
                _parameter("command", "enum", enum_values=["attack", "dodge"]),
            ],
            [
                _require(
                    "combat.encounter_active == true and combat.turn_active == true and "
                    "combat.current_actor == _caster_state_id and "
                    "combat.active_actor == _caster_state_id",
                    "Give this combat order on the caster's actual turn.",
                ),
                _loop(
                    "actor_ids",
                    "_ao_command_actor",
                    [
                        _call(
                            "qualify_srd_animated_actor",
                            {"actor_state": "{_ao_command_actor}"},
                            ("_ao_source",),
                        ),
                        _require(
                            "_ao_source.caster_state_id == _caster_state_id",
                            "Command only this caster's active creatures.",
                        ),
                        _call(
                            "measure_srd_animated_body_distance",
                            {
                                "first": "{_caster_state_id}",
                                "second": "{_ao_command_actor}",
                            },
                            ("_ao_distance",),
                        ),
                        _require(
                            "_ao_distance <= 500",
                            "The actual creature is outside mental command range.",
                        ),
                    ],
                ),
                *payment,
                _loop(
                    "actor_ids",
                    "_ao_command_actor",
                    [
                        _call(
                            "set_srd_animated_object_order",
                            {
                                "actor_state": "{_ao_command_actor}",
                                "command": "{command}",
                                "caster_state": "{_caster_state_id}",
                            },
                        ),
                    ],
                ),
            ],
        ),
        "combat",
        "Animated object command",
        [("Objects", "actor_ids"), ("Order", "command")],
    )


def _attack():
    return _public(
        _tool_scene(
            "Begin one genuine saved native Slam using this source's actual stats and body.",
            [_entity("actor_state"), _entity("target_state")],
            [
                _call("qualify_srd_animated_actor", {"actor_state": "{_actor_state_id}"}),
                _require(
                    "actor_state.animated_object_command == 'attack'",
                    "The creature has no actual attack command.",
                ),
                _call(
                    "measure_srd_animated_body_distance",
                    {
                        "first": "{_actor_state_id}",
                        "second": "{_target_state_id}",
                    },
                    ("_ao_distance",),
                ),
                _require("_ao_distance <= 5", "Slam has actual five-foot body reach."),
                _call(
                    "qualify_srd_created_actor_attack_geometry",
                    {
                        "actor_state": "{_actor_state_id}",
                        "target_state": "{_target_state_id}",
                    },
                ),
                _require(
                    "actor_state.animated_slam_expression != null",
                    "This upcast Slam expression still needs its actual native dice adapter.",
                ),
                _set("_ao_damage_expression", "actor_state.animated_slam_expression"),
                _call(
                    "resolve_srd_scene_relation",
                    {
                        "actor_state": "{_actor_state_id}",
                        "target_state": "{_target_state_id}",
                    },
                    ("_scene_can_see",),
                ),
                _set("_ao_can_see_target", "_scene_can_see"),
                _call(
                    "resolve_srd_scene_relation",
                    {
                        "actor_state": "{_target_state_id}",
                        "target_state": "{_actor_state_id}",
                    },
                    ("_scene_can_see",),
                ),
                _set("_ao_can_see_actor", "_scene_can_see"),
                _set("_ao_integer_distance", "ceil(_ao_distance)"),
                _call(
                    "begin_srd_creature_attack",
                    {
                        "actor": "{_actor_state_id}",
                        "target": "{_target_state_id}",
                        "attack_name": "Slam",
                        "attack_modifier": "{actor_state.animated_attack_bonus}",
                        "damage_expression": "{_ao_damage_expression}",
                        "damage_modifier": "{actor_state.animated_slam_modifier}",
                        "damage_type": "force",
                        "attack_distance": "{_ao_integer_distance}",
                        "attack_mode": "melee",
                        "normal_range": 5,
                        "long_range": 5,
                        "actor_can_see_target": "{_ao_can_see_target}",
                        "target_can_see_actor": "{_ao_can_see_actor}",
                        "target_is_charmer": False,
                        "fear_source_in_sight": False,
                        "advantage": False,
                        "disadvantage": False,
                        "attack_uses_weapon": False,
                    },
                    ("_pending_action_id",),
                ),
            ],
        ),
        "combat",
        "Animated Slam",
        [("Saved attack", "_pending_action_id")],
    )


def extend_runtime(text):
    tools = _load_runtime(text)["tools"]
    changed = {
        "record_srd_object_attachment": _attachment(),
        "measure_srd_animated_body_distance": _distance(),
        "qualify_srd_animate_object": _qualification(),
        "prepare_srd_animated_object_actor": _prepare(tools),
        "animate_srd_object_body": _one(),
        "finish_srd_animated_object_actor": _finish(),
        "cast_srd_animate_objects": _cast(),
        "revert_srd_animated_object_actor": _retire_actor(),
        "retire_srd_animate_object_source": _retire_source(),
        "qualify_srd_animated_actor": _get_actor(),
        "collect_srd_animated_caster_initiative": _tool(
            "Read this caster's current living animated bodies for linked Initiative.",
            [_entity("caster_state")],
            [
                _set("_initiative_linked_animated_objects", []),
                _set(
                    "_ao_initiative_source",
                    "state.animated_object_sources[caster_state.concentration_cast_id]",
                ),
                _require(
                    "_ao_initiative_source.installed == true and "
                    "_ao_initiative_source.caster_state_id == _caster_state_id",
                    "The Initiative source must be this caster's current animation.",
                ),
                _set("_ao_initiative_actor_ids", "_ao_initiative_source.actors"),
                _loop(
                    "_ao_initiative_actor_ids",
                    "_ao_initiative_actor",
                    [
                        _when(
                            "state.animated_object_bodies[_ao_initiative_actor].active == true",
                            [
                                _push(
                                    "_initiative_linked_animated_objects", "_ao_initiative_actor"
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        "set_srd_animated_object_order": _tool(
            "Retain the authenticated mental order.",
            [_entity("actor_state"), _entity("caster_state"), _parameter("command")],
            [
                _require(
                    "caster_state.turns_started != null", "An order needs this actual caster turn."
                ),
                _set("actor_state.animated_object_command", "command"),
                _set("actor_state.animated_command_turn", "caster_state.turns_started"),
            ],
        ),
        "start_srd_animated_object_turn": _tool(
            "Select the real command for this after-caster turn.",
            [_entity("actor_state")],
            [
                _call(
                    "qualify_srd_animated_actor",
                    {"actor_state": "{_actor_state_id}"},
                    ("_ao_source",),
                ),
                _call(
                    "resolve_srd_animated_object_turn",
                    {
                        "actor_state": "{_actor_state_id}",
                        "caster_state": "{_ao_source.caster_state_id}",
                    },
                ),
            ],
        ),
        "resolve_srd_animated_object_turn": _tool(
            "Dodge unless commanded on the real caster turn.",
            [_entity("actor_state"), _entity("caster_state")],
            [
                _when(
                    "actor_state.animated_command_turn != caster_state.turns_started",
                    [
                        _set("actor_state.animated_object_command", "'dodge'"),
                    ],
                ),
                _when(
                    "actor_state.animated_object_command == 'dodge' and not "
                    "('incapacitated' in actor_state.conditions or "
                    "'stunned' in actor_state.conditions or "
                    "'unconscious' in actor_state.conditions)",
                    [
                        _call("dodge_srd_animated_object", {"actor_state": "{_actor_state_id}"}),
                    ],
                ),
            ],
        ),
        "dodge_srd_animated_object": _tool(
            "Take the printed default Dodge on the actual NPC turn.",
            [_entity("actor_state")],
            [
                _require(
                    "combat.encounter_active == true and combat.turn_active == true and "
                    "combat.current_actor == _actor_state_id and "
                    "combat.active_actor == _actor_state_id and "
                    "actor_state.animated_object_active == true",
                    "Default Dodge needs this actual animated object's turn.",
                ),
                _set("actor_state.dodging", True),
                _call("spend_srd_action", {"magic_action": False}),
                _set("combat.action_taken", "'dodge'"),
            ],
        ),
        "command_srd_animated_objects": _command(),
        "begin_srd_animated_object_slam": _attack(),
        **_refresh(),
    }
    changed = {name: tool for name, tool in changed.items() if tools.get(name) != tool}
    return _replace_tools(text, changed) if changed else text


def _death_branches(effects):
    """Find inline genuine monster-death owners without following Jar's forwarding calls."""
    branches = []
    native = "target.hp == 0 and target.creature_kind == 'monster'"
    admitted = {native, f"({native}) and target.astral_projection_role != 'form'"}
    for effect in effects:
        if effect.get("condition") in admitted:
            branches.append(effect)
        for side in ("on_true", "on_false"):
            if isinstance(effect.get(side), list):
                branches.extend(_death_branches(effect[side]))
    return branches


def _packet_owner(tools, name):
    """Follow only the accepted qualified facade; leave its parameters and effects intact."""
    cores = {
        "apply_srd_qualifying_damage": "execute_srd_qualified_scalar_packet",
        "apply_srd_qualifying_composite_damage": "execute_srd_qualified_composite_packet",
    }
    core = cores.get(name)
    entry = tools[name]
    if not core or not entry["effects"] or entry["effects"][0].get("call") != core:
        return name
    delegate = entry["effects"][0]
    expected = {
        p["name"]: "{"
        + ("_" + p["name"] + "_id" if p.get("resolve") in {"entity", "participant"} else p["name"])
        + "}"
        for p in entry["parameters"]
    }
    expected["render_damage_snapshot"] = True
    if (
        core not in tools
        or delegate != {"call": core, "params": expected, "export_context": "*"}
        or sum(effect.get("call") == core for effect in entry["effects"]) != 1
        or not any(p["name"] == "render_damage_snapshot" for p in tools[core]["parameters"])
    ):
        raise ValueError("Animate Objects found a changed qualified packet facade.")
    return core


def bind_runtime_consumers(text):
    """Join after actual paid/native owners and before saved reactions and packet capture."""
    text = extend_runtime(text)
    tools = _load_runtime(text)["tools"]
    changed = {}
    for name in ("cast_spell", "cast_spell_outside_combat"):
        cast = deepcopy(tools[name])
        if "cast_srd_animate_objects" in str(cast["effects"]) or any(
            e.get("call") == "cast_spell" for e in cast["effects"]
        ):
            continue
        existing = {p["name"] for p in cast["parameters"]}
        cast["parameters"] += [deepcopy(p) for p in CAST_CHOICES if p["name"] not in existing]
        index = next(
            i
            for i, e in enumerate(cast["effects"])
            if e.get("set", {}).get("field") == "_card_slots_after"
        )
        cast["effects"].insert(
            index,
            _when(
                "_spell_cast_failed != true and _pending_spell_operation_lost != true and "
                "_spell_definition.family == 'animate_objects'",
                [
                    _when(
                        "_spell_source_cast_id == null or _spell_source_cast_id == ''",
                        [
                            _call(
                                "begin_srd_spell_source_instance",
                                {
                                    "caster_state": "{_caster_state_id}",
                                    "spell": "animate_objects",
                                    "slot_level": "{_slot_level}",
                                },
                                ("_spell_source_cast_id",),
                            ),
                        ],
                    ),
                    _call(
                        "cast_srd_animate_objects",
                        {
                            "caster_state": "{_caster_state_id}",
                            "spell_cast_id": "{_spell_source_cast_id}",
                            "slot_level": "{_slot_level}",
                            "animated_object_ids": "{animated_object_ids}",
                            "casting_modifier": "{_casting_profile.modifier}",
                            "casting_attack_bonus": "{_casting_profile.attack_bonus}",
                            "casting_proficiency_bonus": "{_casting_profile.proficiency_bonus}",
                            "concentration_modifier": (
                                "{_casting_profile.concentration_save_modifier}"
                            ),
                        },
                        RESULTS,
                    ),
                ],
            ),
        )
        changed[name] = cast
    end = deepcopy(tools["end_srd_concentration_node"])
    if "retire_srd_animate_object_source" not in str(end["effects"]):
        end["effects"].insert(
            0,
            _when(
                "source_state.concentration_effect_family == 'animate_objects'",
                [
                    _call(
                        "retire_srd_animate_object_source",
                        {
                            "caster_state": "{_source_state_id}",
                            "source_id": "{source_state.concentration_cast_id}",
                        },
                    ),
                ],
            ),
        )
        changed["end_srd_concentration_node"] = end
    clock = deepcopy(tools["advance_srd_lasting_clock"])
    if "refresh_srd_animated_objects" not in str(clock["effects"]):
        index = next(
            i
            for i, e in enumerate(clock["effects"])
            if e.get("set", {}).get("field") == "lasting_state.lasting_tick"
        )
        clock["effects"].insert(
            index + 1,
            _when(
                "lasting_state.has_animated_objects == true",
                [
                    _call("refresh_srd_animated_objects", {}),
                ],
            ),
        )
        changed["advance_srd_lasting_clock"] = clock
    classify = deepcopy(tools["classify_srd_summon_initiative_entry"])
    if "animate_objects" not in str(classify["effects"]):
        classify["effects"].append(_set("_initiative_linked_animated_objects", []))
        classify["effects"].append(
            _when(
                "creature_state.concentration_effect_family == 'animate_objects'",
                [
                    _call(
                        "collect_srd_animated_caster_initiative",
                        {
                            "caster_state": "{_creature_state_id}",
                        },
                        ("_initiative_linked_animated_objects",),
                    ),
                ],
            )
        )
        classify["effects"].append(
            _when(
                "creature_state.created_spell_id == 'animate_objects' and "
                "creature_state.animated_object_active == true",
                [
                    _set("_initiative_is_summon", True),
                    _set("_initiative_summon_owner", "creature_state.created_caster_state_id"),
                ],
            )
        )
        changed["classify_srd_summon_initiative_entry"] = classify
    organize = deepcopy(tools["organize_srd_summons_in_initiative"])
    if "_initiative_linked_animated_objects" not in str(organize["effects"]):
        loop = next(e["for_each"] for e in organize["effects"] if "for_each" in e)
        call = next(
            e for e in loop["do"] if e.get("call") == "classify_srd_summon_initiative_entry"
        )
        call["export_context"]["_initiative_linked_animated_objects"] = (
            "_initiative_linked_animated_objects"
        )
        parent = next(
            e["on_false"]
            for e in loop["do"]
            if e.get("condition") == "_initiative_is_summon == true"
        )
        parent.append(
            _loop(
                "_initiative_linked_animated_objects",
                "_ao_linked_actor",
                [
                    _require(
                        "_ao_linked_actor in combatants",
                        "Include each actual living animated object with its caster.",
                    ),
                    _call(
                        "read_srd_summon_initiative_label",
                        {"summon_state": "{_ao_linked_actor}"},
                        ("_initiative_summon_name",),
                    ),
                    _push(
                        "_initiative_order_with_summons",
                        {
                            "entity_id": "_ao_linked_actor",
                            "initiative_result": "_initiative_entry.initiative_result",
                            "roll": "_initiative_entry.roll",
                            "modifier": "_initiative_entry.modifier",
                            "label": "_initiative_summon_name",
                        },
                    ),
                ],
            )
        )
        changed["organize_srd_summons_in_initiative"] = organize
    turn = deepcopy(tools["begin_combat_turn"])
    if "start_srd_animated_object_turn" not in str(turn["effects"]):
        turn["effects"].append(
            _when(
                "actor.animated_object_active == true",
                [
                    _call("start_srd_animated_object_turn", {"actor_state": "{_actor_id}"}),
                ],
            )
        )
        changed["begin_combat_turn"] = turn
    for name in ("begin_srd_creature_attack", "resolve_adjudicated_creature_attack"):
        if name not in tools:
            continue
        attack = deepcopy(tools[name])
        if "animated_object_command" not in str(attack["pre_conditions"]):
            attack["pre_conditions"].append(
                {
                    "condition": "actor.created_spell_id != 'animate_objects' or "
                    "(actor.animated_object_active == true and "
                    "actor.animated_object_command == 'attack' and attack_name == 'Slam' "
                    "and attack_modifier == actor.animated_attack_bonus and "
                    "damage_modifier == actor.animated_slam_modifier and "
                    "damage_expression == actor.animated_slam_expression and "
                    "damage_type == 'force' and attack_mode == 'melee' and "
                    "normal_range == 5 and long_range == 5 and use_multiattack != true)",
                    "error": "An animated object must use its authentic commanded Slam profile.",
                }
            )
            changed[name] = attack
    for name in (
        "apply_srd_damage",
        "apply_srd_qualifying_damage",
        "apply_srd_composite_damage",
        "apply_srd_qualifying_composite_damage",
    ):
        if name not in tools:
            continue
        owner = _packet_owner(tools, name)
        packet = deepcopy(changed.get(owner, tools[owner]))
        lethal = _death_branches(packet["effects"])
        if len(lethal) != 1:
            raise ValueError("Animate Objects needs one genuine monster zero-HP boundary.")
        callback = _when(
            "target.hp == 0 and target.animated_object_active == true",
            [
                _call(
                    "revert_srd_animated_object_actor",
                    {"actor_state": "{target}", "remaining_damage": "{_remaining_damage}"},
                ),
            ],
        )
        existing = lethal[0]["on_true"].count(callback)
        if existing > 1 or (
            existing == 0 and "revert_srd_animated_object_actor" in str(packet["effects"])
        ):
            raise ValueError("Animate Objects found a changed or duplicated reversion owner.")
        if existing == 0:
            lethal[0]["on_true"].append(callback)
            changed[owner] = packet
    return _replace_tools(text, changed) if changed else text
