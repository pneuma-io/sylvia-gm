"""Source-owned created forces use the scene, spell attacks and lasting clock."""

import re
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import yaml

if __package__:
    from .srd_divination_spells import PENDING, _action, _normalize
    from .srd_scene_relations import _array, _snapshot, _spatial, _tool_scene
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
    from srd_divination_spells import PENDING, _action, _normalize
    from srd_scene_relations import _array, _snapshot, _spatial, _tool_scene
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
    "arcane_sword": (110, "Arcane Sword", "Arcanist’s Magic Aura"),
    "spiritual_weapon": (165, "Spiritual Weapon", "Stinking Cloud"),
    "unseen_servant": (171, "Unseen Servant", "Vampiric Touch"),
}
PROFILES = {}
GUARDIAN_PROFILE = {
    "family": "created_spell_guardian",
    "base_level": 4,
    "casting_time": "action",
    "range_kind": "distance",
    "range_feet": 30,
    "duration_rounds": 4800,
    "concentration": False,
    "size_rank": 3,
    "reach_feet": 10,
    "damage_amount": 20,
    "damage_type": "radiant",
    "damage_limit": 60,
    "save_ability": "dexterity",
    "success_damage": "half",
}
HOUND_PROFILE = {
    "family": "created_spell_hound",
    "base_level": 4,
    "casting_time": "action",
    "range_kind": "distance",
    "range_feet": 30,
    "duration_rounds": 4800,
    "concentration": False,
    "move_feet": 30,
    "caster_separation_feet": 300,
    "truesight_feet": 30,
    "alert_feet": 30,
    "alert_min_size_rank": 1,
    "bite_reach_feet": 5,
    "bite_dice": 4,
    "bite_die": "d8",
    "bite_damage_type": "force",
    "bite_save_ability": "dexterity",
}
STEED_PROFILE = {
    "family": "independent_created_creature",
    "base_level": 2,
    "casting_time": "action",
    "range_kind": "distance",
    "range_feet": 30,
    "concentration": False,
    "size_rank": 3,
    "armor_class_base": 10,
    "base_hit_points": 5,
    "hit_points_per_level": 10,
    "speed": 60,
    "flying_speed": 60,
    "flight_min_level": 4,
    "hit_die": "d10",
    "melee_reach": 5,
    "attack_die": "d8",
    "passive_perception": 11,
    "telepathy_feet": 5280,
    **dict(
        zip(
            (
                a + "_score"
                for a in (
                    "strength",
                    "dexterity",
                    "constitution",
                    "intelligence",
                    "wisdom",
                    "charisma",
                )
            ),
            (18, 12, 14, 6, 12, 8),
            strict=True,
        )
    ),
    **dict(
        zip(
            (
                a + "_save"
                for a in (
                    "strength",
                    "dexterity",
                    "constitution",
                    "intelligence",
                    "wisdom",
                    "charisma",
                )
            ),
            (4, 1, 2, -2, 1, -1),
            strict=True,
        )
    ),
}
INDEPENDENT_CAST_CHOICES = [
    _optional("created_individual_id", default=""),
    _optional(
        "created_creature_type", "enum", "celestial", enum_values=["celestial", "fey", "fiend"]
    ),
    _optional("return_created_individual", "boolean", False),
]
FAMILIAR_PROFILE = {
    "family": "familiar_created_creature",
    "base_level": 1,
    "casting_time": "1_hour",
    "range_kind": "distance",
    "range_feet": 10,
    "concentration": False,
    "telepathy_feet": 100,
    "reappearance_feet": 30,
}
FAMILIAR_CAST_CHOICES = [
    _optional("familiar_form_id", default="bat"),
    *deepcopy(INDEPENDENT_CAST_CHOICES),
]
ANIMATED_PROFILE = {
    "family": "animated_undead_creature", "base_level": 3, "casting_time": "1_minute",
    "range_kind": "distance", "range_feet": 10, "concentration": False,
    "control_ticks": 28800, "base_creation_count": 1, "base_reassert_count": 4,
    "extra_per_level": 2, "command_range_feet": 60,
}
CREATED_UNDEAD_PROFILE = {
    "family": "created_undead_creature", "base_level": 6, "casting_time": "1_minute",
    "range_kind": "distance", "range_feet": 10, "concentration": False,
    "control_ticks": 28800, "command_range_feet": 120,
}
CREATED_UNDEAD_CAST_CHOICES = [
    _optional("created_undead_mode", "enum", "create", enum_values=["create", "reassert"]),
    _optional("created_undead_form", "enum", "ghoul",
              enum_values=["ghoul", "ghast", "wight", "mummy"]),
    _optional("created_undead_bodies", "array", [], max_items=6, items={
        "type": "object", "additionalProperties": False,
        "required": ["corpse_state_id", "individual_id", "appearance_height"],
        "properties": {
            "corpse_state_id": {"type": "string", "minLength": 1},
            "individual_id": {"type": "string", "minLength": 1},
            "appearance_height": {"type": "number", "minimum": 0.01, "maximum": 1000},
            "other_languages": {"type": "array", "uniqueItems": True,
                                "items": {"type": "string", "minLength": 1}},
        },
    }),
    _optional("created_undead_actor_ids", "array", [], items={"type": "string"},
              unique_items=True, max_items=6),
]
GIANT_INSECT_PROFILE = {
    "family": "summoned_insect_creature", "base_level": 4, "casting_time": "action",
    "range_kind": "distance", "range_feet": 60, "requires_sight": True,
    "duration_rounds": 100, "concentration": True, "size_rank": 3,
}
INSECT_CAST_CHOICES = [
    _optional("insect_form", "enum", "centipede", enum_values=["centipede", "spider", "wasp"]),
]
ANIMATED_CAST_CHOICES = [
    _optional("animated_mode", "enum", "create", enum_values=["create", "reassert"]),
    _optional("animated_bodies", "array", [], max_items=13, items={
        "type": "object", "additionalProperties": False,
        "required": ["corpse_state_id", "individual_id", "other_language", "appearance_height"],
        "properties": {
            "corpse_state_id": {"type": "string", "minLength": 1},
            "individual_id": {"type": "string", "minLength": 1},
            "other_language": {"type": "string", "minLength": 1},
            "appearance_height": {"type": "number", "minimum": 0.01, "maximum": 1000},
        },
    }),
    _optional("animated_actor_ids", "array", [], items={"type": "string"},
              unique_items=True, max_items=16),
]
for _spell, _level, _casting, _range, _rounds, _concentration, _move, _dice, _die, _scale in (
    ("arcane_sword", 7, "action", 90, 10, True, 30, 4, "d12", 0),
    ("spiritual_weapon", 2, "bonus_action", 60, 10, True, 20, 1, "d8", 1),
    ("unseen_servant", 1, "action", 60, 600, False, 15, 0, "d4", 0),
):
    PROFILES[_spell] = {
        "family": "created_spell_force",
        "base_level": _level,
        "casting_time": _casting,
        "range_kind": "distance",
        "range_feet": _range,
        "duration_rounds": _rounds,
        "concentration": _concentration,
        "move_feet": _move,
        "base_dice": _dice,
        "damage_die": _die,
        "dice_per_slot_level": _scale,
    }

PUBLIC_TOOLS = {"command_srd_spell_force"}
ANIMATED_PUBLIC_TOOLS = {"command_srd_animated_undead", "record_srd_map_time_of_day"}
HOUND_PUBLIC_TOOLS = {"move_srd_faithful_hound", "observe_srd_faithful_hound"}
FAMILIAR_PUBLIC_TOOLS = {
    "dismiss_srd_familiar", "reappear_srd_familiar", "dodge_srd_familiar",
    "communicate_srd_familiar",
}
CAST_CHOICES = [
    _optional("force_id", default=""),
    _array("force_position", default=[0, 0, 0]),
    _optional("force_target", default=None, resolve="entity"),
    _optional("force_on_ground", "boolean", False),
]
POSITION = ("_info_scene_entity", "_info_position")
MEASURE = ("_scene_distance", "_scene_direction")
FORCE = ("_force_record", "_force_profile")
ACTOR_PLACEMENT_CHOICES = [
    _array("created_actor_position", default=[0, 0, 0]),
    _optional("created_actor_height", "number", None, minimum=0.01, maximum=1000),
    _optional(
        "created_actor_lighting",
        "enum",
        "bright",
        enum_values=["bright", "dim", "darkness", "magical_darkness"],
    ),
]


def validate_profiles(definitions):
    """Reject fixed profiles differing from the pinned printed spell rules."""
    for spell, definition in definitions.items():
        if definition.get("family") == "created_undead_creature":
            if spell != "create_undead" or definition != CREATED_UNDEAD_PROFILE:
                raise SystemExit(f"Create Undead source profile differs: {spell}")
            _created_undead_source_profile()
        if definition.get("family") == "summoned_insect_creature":
            if spell != "giant_insect" or definition != GIANT_INSECT_PROFILE:
                raise SystemExit(f"Giant Insect source profile differs: {spell}")
            _giant_insect_source_profile()
        if definition.get("family") == "animated_undead_creature":
            if spell != "animate_dead" or definition != ANIMATED_PROFILE:
                raise SystemExit(f"Animate Dead source profile differs: {spell}")
            _validate_animate_dead_source()
            _zombie_source_profile()
        if definition.get("family") == "familiar_created_creature":
            if spell != "find_familiar" or definition != FAMILIAR_PROFILE:
                raise SystemExit(f"Familiar source profile differs: {spell}")
            _familiar_source_forms()
        if definition.get("family") == "independent_created_creature":
            if spell != "find_steed" or definition != STEED_PROFILE:
                raise SystemExit(f"Created-creature source profile differs: {spell}")
            _validate_steed_source()
        if definition.get("family") == "created_spell_hound" and (
            spell != "faithful_hound" or definition != HOUND_PROFILE
        ):
            raise SystemExit(f"Hound source profile differs: {spell}")
        if definition.get("family") == "created_spell_guardian" and (
            spell != "guardian_of_faith" or definition != GUARDIAN_PROFILE
        ):
            raise SystemExit(f"Guardian source profile differs: {spell}")
        if definition.get("family") == "created_spell_force" and definition != PROFILES.get(spell):
            raise SystemExit(f"Created-force source profile differs: {spell}")


def _validate_steed_source():
    """Bind the fixed installer to the actual pinned printed Otherworldly Steed block."""
    import json

    path = Path(__file__).resolve().parents[1] / "rulesets/srd-lite/content/corpus/spells.json"
    corpus = json.loads(path.read_text(encoding="utf-8"))
    page = next(row["text"] for row in corpus["page_records"] if row["page"] == 131)
    block = page.split("Otherworldly Steed\n", 1)[1].split("Find the Path\n", 1)[0]
    text = " ".join(block.split())
    clauses = (
        "Large Celestial, Fey, or Fiend (Your Choice), Neutral",
        "AC 10 + 1 per spell level",
        "HP 5 + 10 per spell level",
        "Dice [d10s] equal to the spell’s level)",
        "Speed 60 ft., Fly 60 ft. (requires level 4+ spell)",
        "Str 18 +4 +4 dex 12 +1 +1 con 14 +2 +2",
        "int 6 −2 −2 WiS 12 +1 +1 chA 8 −1 −1",
        "Senses Passive Perception 11",
        "Languages Telepathy 1 mile (works only with you)",
        "CR None (XP 0; PB equals your Proficiency Bonus)",
        "your spell attack modifier, reach 5 ft. Hit: 1d8 plus the spell’s level",
    )
    if any(clause not in text for clause in clauses):
        raise SystemExit("Otherworldly Steed fixed source block differs from its installer.")


def _validate_animate_dead_source():
    """Bind creation, renewal and permanent-body control to the printed instant spell."""
    import json

    path = Path(__file__).resolve().parents[1] / "rulesets/srd-lite/content/corpus/spells.json"
    corpus = json.loads(path.read_text(encoding="utf-8"))
    page = next(p["text"] for p in corpus["page_records"] if p["page"] == 108)
    text = " ".join(page.split("Animate Dead\n", 1)[1].split("Animate Objects\n", 1)[0].split())
    clauses = (
        "Casting Time: 1 minute", "Range: 10 feet", "Duration: Instantaneous",
        "a drop of blood, a piece of flesh, and a pinch of bone dust",
        "a corpse of a Medium or Small Humanoid within range",
        "a Zombie if you chose a corpse", "the creature is within 60 feet of you",
        "the same command to each one", "on its next turn", "takes the Dodge action",
        "continues to follow it until its task is complete", "under your control for 24 hours",
        "before the current 24-hour period ends",
        "reasserts your control over up to four creatures",
        "two additional Undead crea- tures for each spell slot level above 3",
        "Each of the creatures must come from a different corpse or pile of bones",
    )
    if any(clause not in text for clause in clauses):
        raise SystemExit("Review the changed Animate Dead source before execution.")


def _schema():
    properties = {
        k: {"type": "boolean" if type(v) is bool else "integer" if type(v) is int else "string"}
        for k, v in next(iter(PROFILES.values())).items()
        if k != "family"
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _save_record():
    return _set("state.spell_force_records.{force_id}", "_force_record")


def _scene_position(subject, local):
    return [
        _call("srd_information_scene_entity", {"subject": subject}, POSITION),
        _set(local, "_info_position"),
    ]


def _command_action():
    effects = deepcopy(_action(bonus=True))
    for effect in _walk(effects):
        if effect.get("call") == "consume_srd_bonus_action":
            effect["params"]["bonus_action"] = "spell_force"
    return effects


def _scene_enemy_tool():
    return _tool_scene(
        "Qualify an enemy from the target's explicit admitted scene relation to the caster.",
        [_parameter("caster_id"), _parameter("target_id")],
        [
            _call("srd_information_scene_entity", {"subject": "{target_id}"}, POSITION),
            _set("_scene_is_enemy", False),
            _when(
                "_info_scene_entity.metadata.enemy_of != null",
                [_set("_scene_is_enemy", "caster_id in _info_scene_entity.metadata.enemy_of")],
            ),
        ],
    )


def _proxy_tools():
    create = _tool_scene(
        "Place one exact saved magical force in the existing admitted scene.",
        [_entity("caster_state"), _parameter("force_id"), _parameter("spell"), _array("position")],
        _snapshot()
        + _scene_position("{_caster_state_id}", "_force_caster_position")
        + [
            _set("_force_prior_scene", "state.spatial_scene.entities"),
            _loop(
                "_force_prior_scene",
                "_force_prior_entity",
                [
                    _require(
                        "_force_prior_entity.id != force_id",
                        "A spell force needs a unique scene ID.",
                    ),
                ],
            ),
            _set(
                "_force_scene_entity",
                {
                    "id": "force_id",
                    "map_id": "state.spatial_scene.map.id",
                    "position": "position",
                    "footprint": {"kind": "'circle'", "radius": 0.01},
                    "height": 0.01,
                    "facing": [1, 0],
                    "active": True,
                    "movement": "'passable'",
                    "visibility": "'clear'",
                    "metadata": {
                        "source_ref": "spell",
                        "source_caster": "_caster_state_id",
                        "spell_force": True,
                        "plane": "_info_scene_entity.metadata.plane",
                        "lighting": "_info_scene_entity.metadata.lighting",
                        "is_object": True,
                    },
                },
            ),
            _push("_force_prior_scene", "_force_scene_entity"),
            _set("state.spatial_scene.entities", "_force_prior_scene"),
            _set("state.spatial_scene.map.revision", "state.spatial_scene.map.revision + 1"),
            *_snapshot(),
        ],
    )
    update = _tool_scene(
        "Move or retire one exact scene-owned spell force without a parallel position store.",
        [
            _parameter("force_id"),
            _array("position"),
            _parameter("active", "boolean"),
            _optional(
                "lighting", "enum", enum_values=["bright", "dim", "darkness", "magical_darkness"]
            ),
        ],
        [
            _set("_force_scene_after", []),
            _set("_force_scene_before", "state.spatial_scene.entities"),
            _loop(
                "_force_scene_before",
                "_force_scene_item",
                [
                    _when(
                        "_force_scene_item.id == force_id",
                        [
                            _when(
                                "lighting != null",
                                [
                                    _set("_force_light_patch", {"lighting": "lighting"}),
                                    _set(
                                        "_force_scene_metadata",
                                        "merge(_force_scene_item.metadata, _force_light_patch)",
                                    ),
                                    _set(
                                        "_force_metadata_patch",
                                        {"metadata": "_force_scene_metadata"},
                                    ),
                                    _set(
                                        "_force_scene_item",
                                        "merge(_force_scene_item, _force_metadata_patch)",
                                    ),
                                ],
                            ),
                            _set(
                                "_force_scene_patch", {"position": "position", "active": "active"}
                            ),
                            _set(
                                "_force_scene_item", "merge(_force_scene_item, _force_scene_patch)"
                            ),
                        ],
                    ),
                    _push("_force_scene_after", "_force_scene_item"),
                ],
            ),
            _set("state.spatial_scene.entities", "_force_scene_after"),
            _set("state.spatial_scene.map.revision", "state.spatial_scene.map.revision + 1"),
            *_snapshot(),
        ],
    )
    return {"create_srd_spell_force_scene": create, "update_srd_spell_force_scene": update}


def _record_tools():
    collect = _tool(
        "Resolve the selected saved source and enforce concentration and the actual clock.",
        [_entity("caster_state"), _parameter("force_id")],
        [
            _set("_force_record", "state.spell_force_records[force_id]"),
            _require(
                "_force_record != null and _force_record.active == true and "
                "_force_record.caster == _caster_state_id and "
                "state.lasting_tick < _force_record.expires_tick",
                "There is no active selected force owned by this caster.",
            ),
            _lookup("_force_record.spell", "_force_profile"),
            _when(
                "_force_profile.concentration == true",
                [
                    _require(
                        "caster_state.concentration_spell_id == _force_record.spell and "
                        "caster_state.concentration_effect_family == 'created_spell_force' and "
                        "caster_state.concentration_spell_force_id == force_id and "
                        "caster_state.concentration_started_tick == _force_record.started_tick",
                        "This force's concentration source has ended.",
                    ),
                ],
            ),
        ],
    )
    remove = _tool_scene(
        "Retire this source's mechanical force and its exact scene proxy.",
        [_parameter("force_id")],
        [
            _set("_force_ending", "state.spell_force_records[force_id]"),
            _when(
                "_force_ending != null and _force_ending.active == true",
                [
                    _set("state.spell_force_records.{force_id}.active", False),
                    *_scene_position("{force_id}", "_force_ending_position"),
                    _call(
                        "update_srd_spell_force_scene",
                        {
                            "force_id": "{force_id}",
                            "position": "{_force_ending_position}",
                            "active": False,
                        },
                    ),
                    _when(
                        "_force_ending.spell == 'unseen_servant'",
                        [
                            _call("finish_srd_servant_entity", {"servant_state": "{force_id}"}),
                        ],
                    ),
                    _set("_force_retained_clock_ids", []),
                    _set("_force_registered_ids", "state.spell_force_ids"),
                    _loop(
                        "_force_registered_ids",
                        "_force_registered_id",
                        [
                            _when(
                                "_force_registered_id != force_id",
                                [_push("_force_retained_clock_ids", "_force_registered_id")],
                            )
                        ],
                    ),
                    _set("state.spell_force_ids", "_force_retained_clock_ids"),
                    _set("state.has_spell_forces", "len(_force_retained_clock_ids) > 0"),
                    _when(
                        "_force_ending.spell == 'guardian_of_faith'",
                        [
                            _set("state.has_guardian_sources", False),
                            _loop(
                                "_force_retained_clock_ids",
                                "_guardian_remaining_id",
                                [
                                    _set(
                                        "_guardian_remaining_record",
                                        "state.spell_force_records[_guardian_remaining_id]",
                                    ),
                                    _when(
                                        "_guardian_remaining_record.spell == "
                                        "'guardian_of_faith' and "
                                        "_guardian_remaining_record.active == true",
                                        [_set("state.has_guardian_sources", True)],
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    refresh = _tool(
        "Expire forces at their saved deadline; concentration cleanup stays with its owner.",
        [],
        [
            _when(
                "state.has_spell_forces == true",
                [
                    _set("_force_clock_ids", "state.spell_force_ids"),
                    _loop(
                        "_force_clock_ids",
                        "_force_clock_id",
                        [
                            _set(
                                "_force_clock_record", "state.spell_force_records[_force_clock_id]"
                            ),
                            _when(
                                "_force_clock_record.active == true and "
                                "state.lasting_tick >= _force_clock_record.expires_tick",
                                [
                                    _call(
                                        "expire_srd_spell_force",
                                        {
                                            "force_id": "{_force_clock_id}",
                                            "caster_state": "{_force_clock_record.caster}",
                                        },
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            )
        ],
    )
    expire = _tool(
        "End an expired concentration force through its actual concentration owner.",
        [_parameter("force_id"), _entity("caster_state")],
        [
            {
                "condition": "caster_state.concentration_effect_family == 'created_spell_force' "
                "and caster_state.concentration_spell_force_id == force_id",
                "on_true": [
                    _call(
                        "end_srd_concentration_node",
                        {
                            "source_state": "{_caster_state_id}",
                            "target_state": "{_caster_state_id}",
                        },
                    )
                ],
                "on_false": [_call("remove_srd_spell_force", {"force_id": "{force_id}"})],
            }
        ],
    )
    finish = _tool(
        "End the servant's one Hit Point body together with its lasting source.",
        [_entity("servant_state")],
        [
            _set("servant_state.hp", 0),
            _set("servant_state.is_alive", False),
            _set("servant_state.spell_force_active", False),
        ],
    )
    return {
        "collect_srd_spell_force": collect,
        "remove_srd_spell_force": remove,
        "refresh_srd_spell_forces": refresh,
        "expire_srd_spell_force": expire,
        "finish_srd_servant_entity": finish,
    }


def _attack_call():
    params = {
        "spell": "{_force_record.spell}",
        "family": "spell_attack_damage",
        "caster_state": "{_caster_state_id}",
        "target": "{_force_target_id}",
        "casting_ability": "{_force_record.casting_ability}",
        "casting_modifier": "{_force_record.casting_modifier}",
        "casting_attack_bonus": "{_force_record.attack_bonus}",
        "casting_save_dc": "{_force_record.save_dc}",
        "casting_scaling_level": "{_force_record.scaling_level}",
        "slot_resource": "no_slot",
        "slot_level": "{_force_record.slot_level}",
        "scaling_mode": "slot_level",
        "base_level": "{_force_profile.base_level}",
        "base_dice": "{_force_profile.base_dice}",
        "dice_per_slot_level": "{_force_profile.dice_per_slot_level}",
        "cantrip_upgrade_dice": 0,
        "upgrade_level_1": 5,
        "upgrade_level_2": 11,
        "upgrade_level_3": 17,
        "damage_die": "{_force_profile.damage_die}",
        "damage_type_mode": "fixed",
        "miss_damage": "none",
        "attack_kind": "melee",
        "range_kind": "touch",
        "range_feet": 0,
        "requires_sight": False,
        "target_distance": 5,
        "caster_can_see_target": "{_scene_can_see}",
        "target_can_see_caster": "{_force_target_can_see}",
        "advantage": False,
        "disadvantage": False,
        "enemy_within_5_feet": False,
        "close_enemy_can_see_caster": False,
        "close_enemy_incapacitated": True,
        "cover": "{_force_cover}",
        "flat_damage": "{_force_record.casting_modifier}",
        "force_source_id": "{force_id}",
    }
    for kind in (
        "acid",
        "bludgeoning",
        "cold",
        "fire",
        "force",
        "lightning",
        "necrotic",
        "piercing",
        "poison",
        "psychic",
        "radiant",
        "slashing",
        "thunder",
    ):
        params["allows_" + kind] = kind == "force"
    return _call(
        "cast_spell_attack_damage",
        params,
        (
            "_spell_summary_rows",
            "_spell_detail_rows",
            "_spell_attack_outcome",
            "_spell_damage_amount",
        ),
    )


def _attack_tools():
    attack = _tool_scene(
        "Resolve the source-owned force's melee attack using actual force-target positions.",
        [_entity("caster_state"), _parameter("force_id"), _entity("force_target")],
        [
            _call(
                "collect_srd_spell_force",
                {"caster_state": "{_caster_state_id}", "force_id": "{force_id}"},
                FORCE,
            ),
            _require("_force_profile.base_dice > 0", "An Unseen Servant cannot attack."),
            *_scene_position("{force_id}", "_force_attack_position"),
            _set("_force_attack_plane", "_info_scene_entity.metadata.plane"),
            *_scene_position("{_force_target_id}", "_force_attack_target"),
            _set("_force_target_scene_id", "_info_scene_entity.id"),
            _set("_force_attack_target_metadata", "_info_scene_entity.metadata"),
            _require(
                "_force_attack_plane == _force_attack_target_metadata.plane",
                "The force and target must occupy the same plane.",
            ),
            _call(
                "measure_srd_information_distance",
                {"start": "{_force_attack_position}", "end": "{_force_attack_target}"},
                MEASURE,
            ),
            _set("_force_target_distance", "_scene_distance"),
            _require(
                "_force_target_distance <= 5", "The target must be within 5 feet of the force."
            ),
            _call(
                "resolve_srd_scene_relation",
                {"actor_state": "{_caster_state_id}", "target_state": "{_force_target_id}"},
                ("_scene_can_see",),
            ),
            _set("_force_caster_can_see", "_scene_can_see"),
            _call(
                "resolve_srd_scene_relation",
                {"actor_state": "{_force_target_id}", "target_state": "{_caster_state_id}"},
                ("_scene_can_see",),
            ),
            _set("_force_target_can_see", "_scene_can_see"),
            _spatial(
                "exposure",
                {
                    "map_id": "{state.spatial_scene.map.id}",
                    "observer": {"point": "{_force_attack_position}"},
                    "target": {"entity_id": "{_force_target_scene_id}"},
                    "samples": {"horizontal": 3, "vertical": 3},
                    "include": ["geometry"],
                },
                "_force_exposure",
            ),
            _require(
                "_force_exposure.visible_samples > 0",
                "The force cannot attack through Total Cover.",
            ),
            _set("_force_cover", "'none'"),
            _when(
                "_force_exposure.visible_samples < len(_force_exposure.samples)",
                [
                    _require(
                        "_force_attack_target_metadata.cover_from != null",
                        "Partly occluded geometry requires an admitted Cover relation "
                        "from this actual force origin.",
                    ),
                    _set("_force_cover", "_force_attack_target_metadata.cover_from[force_id]"),
                    _require(
                        "_force_cover == 'none' or _force_cover == 'half' or "
                        "_force_cover == 'three_quarters'",
                        "Qualify the actual partial Cover as none, half, or three_quarters "
                        "before attacking.",
                    ),
                ],
            ),
            _set("_scene_can_see", "_force_caster_can_see"),
            _attack_call(),
        ],
    )
    return {"attack_srd_spell_force": attack}


def _casting_tools():
    cast = _tool_scene(
        "Install a source-owned force and resolve only its printed immediate attack.",
        [
            _entity("caster_state"),
            _parameter("spell"),
            _parameter("slot_level", "integer"),
            _parameter("casting_ability"),
            _parameter("casting_modifier", "integer"),
            _parameter("casting_attack_bonus", "integer"),
            _parameter("casting_save_dc", "integer"),
            _parameter("casting_scaling_level", "integer"),
            _parameter("concentration_modifier", "integer"),
            *deepcopy(CAST_CHOICES),
        ],
        [
            _lookup("spell", "_force_profile"),
            _require("len(force_id) > 0", "Specify a unique identity for the created force."),
            _set("_force_identity", "force_id"),
            _when(
                "state.spell_force_ids == null",
                [
                    _set("state.spell_force_ids", []),
                    _set("state.spell_force_records", {}),
                ],
            ),
            _require(
                "force_id not in state.spell_force_ids", "The force identity is already used."
            ),
            *_scene_position("{_caster_state_id}", "_force_cast_origin"),
            _call(
                "measure_srd_information_distance",
                {"start": "{_force_cast_origin}", "end": "{force_position}"},
                MEASURE,
            ),
            _require(
                "_scene_distance <= _force_profile.range_feet", "The force origin is outside range."
            ),
            _call(
                "srd_information_barriers",
                {"start": "{_force_cast_origin}", "end": "{force_position}", "rule": "solid"},
                ("_information_blocked",),
            ),
            _require(
                "_information_blocked != true", "A spell force requires a clear path to its origin."
            ),
            _when(
                "spell == 'unseen_servant'",
                [
                    _require("force_on_ground == true", "The servant must appear on the ground."),
                    _spatial(
                        "area",
                        {
                            "map_id": "{state.spatial_scene.map.id}",
                            "shape": {
                                "kind": "cylinder",
                                "center": "{force_position}",
                                "radius": 0.01,
                                "height": 0.01,
                            },
                            "include": ["entities", "geometry"],
                            "max_results": 4096,
                        },
                        "_force_space",
                    ),
                    _require(
                        "len(_force_space.results) == 0 and _force_space.truncated != true",
                        "The servant requires an unoccupied space.",
                    ),
                    {
                        "create_entity": {
                            "id": "force_id",
                            "value": {
                                "name": "'Unseen Servant'",
                                "source": "spell",
                                "creature_kind": "'spell_force'",
                                "hp": 1,
                                "max_hp": 1,
                                "armor_class": 10,
                                "abilities": {"strength": 2},
                                "is_alive": True,
                                "temp_hp": 0,
                                "conditions": ["'invisible'"],
                                "size_rank": 2,
                                "speed": 0,
                                "exhaustion_level": 0,
                                "damage_immunities": [],
                                "damage_resistances": [],
                                "damage_vulnerabilities": [],
                                "spell_speed_reduction": 0,
                                "spell_force_active": True,
                                "spell_force_id": "force_id",
                                "inventory": [],
                            },
                        }
                    },
                ],
            ),
            _when(
                "_force_profile.concentration == true and "
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
            _when("state.lasting_tick == null", [_set("state.lasting_tick", 0)]),
            _set("_force_created_turn", 0),
            _when(
                "caster_state.turns_started != null",
                [_set("_force_created_turn", "caster_state.turns_started")],
            ),
            _set(
                "_force_record",
                {
                    "id": "_force_identity",
                    "spell": "spell",
                    "caster": "_caster_state_id",
                    "active": True,
                    "started_tick": "state.lasting_tick",
                    "expires_tick": "state.lasting_tick + _force_profile.duration_rounds * 2",
                    "slot_level": "slot_level",
                    "casting_ability": "casting_ability",
                    "casting_modifier": "casting_modifier",
                    "attack_bonus": "casting_attack_bonus",
                    "save_dc": "casting_save_dc",
                    "scaling_level": "casting_scaling_level",
                    "task": "''",
                    "interaction_subject": "''",
                    "last_command_tick": -1,
                    "created_turn": "_force_created_turn",
                },
            ),
            _save_record(),
            _set("_force_ids", "state.spell_force_ids"),
            _push("_force_ids", "_force_identity"),
            _set("state.spell_force_ids", "_force_ids"),
            _set("state.has_spell_forces", True),
            _when(
                "_force_profile.concentration == true",
                [
                    _set("caster_state.concentration_spell_id", "spell"),
                    _set("caster_state.concentration_effect_family", "'created_spell_force'"),
                    _set("caster_state.concentration_spell_force_id", "force_id"),
                    _set("caster_state.concentration_target_state_id", "_caster_state_id"),
                    _set("caster_state.concentration_target_state_ids", []),
                    _set("caster_state.concentration_started_tick", "state.lasting_tick"),
                    _set("caster_state.concentration_expires_tick", "_force_record.expires_tick"),
                    _set("caster_state.concentration_save_modifier", "concentration_modifier"),
                ],
            ),
            _call(
                "create_srd_spell_force_scene",
                {
                    "caster_state": "{_caster_state_id}",
                    "force_id": "{force_id}",
                    "spell": "{spell}",
                    "position": "{force_position}",
                },
            ),
            _set("_spell_summary_rows", []),
            _set("_spell_detail_rows", []),
            _call(
                "record_srd_spell_target_result",
                {
                    "target": "{_caster_state_id}",
                    "brief": "Created {spell}",
                    "detail": (
                        "Force {force_id}; source lasts {_force_profile.duration_rounds} rounds."
                    ),
                },
                ("_spell_target_summary", "_spell_target_detail"),
            ),
            _push("_spell_summary_rows", "_spell_target_summary"),
            _push("_spell_detail_rows", "_spell_target_detail"),
            _when(
                "_force_profile.base_dice > 0 and force_target != null",
                [
                    _call(
                        "attack_srd_spell_force",
                        {
                            "caster_state": "{_caster_state_id}",
                            "force_id": "{force_id}",
                            "force_target": "{_force_target_id}",
                        },
                        ("_spell_summary_rows", "_spell_detail_rows"),
                    ),
                ],
            ),
            _when(
                "spell == 'arcane_sword'",
                [
                    _require(
                        "force_target != null", "Arcane Sword makes its attack when it appears."
                    ),
                ],
            ),
        ],
    )
    return {"cast_spell_created_spell_force": cast}


def _command_tools():
    command = _public(
        _tool_scene(
            "Spend the real Bonus Action to move a saved source and perform its printed follow-up.",
            [
                _entity("caster_state"),
                _parameter("force_id"),
                _array("destination"),
                _optional(
                    "destination_lighting",
                    "enum",
                    "bright",
                    enum_values=["bright", "dim", "darkness", "magical_darkness"],
                ),
                _optional("force_target", default=None, resolve="entity"),
                _optional("task", default=""),
                _optional("interaction_subject", default=""),
            ],
            [
                _call(
                    "collect_srd_spell_force",
                    {"caster_state": "{_caster_state_id}", "force_id": "{force_id}"},
                    FORCE,
                ),
                _require(
                    "_force_profile.family == 'created_spell_force'",
                    "This created source has no movable Bonus Action command.",
                ),
                _require(
                    "combat.encounter_active != true or _force_record.spell == 'unseen_servant' or "
                    "_force_record.created_turn != caster_state.turns_started",
                    "Use the force again only on a later turn.",
                ),
                _require(
                    "combat.encounter_active == true or "
                    "_force_record.last_command_tick != state.lasting_tick",
                    "The force can receive one Bonus Action command in this exploration round.",
                ),
                *_command_action(),
                _set(
                    "state.spell_force_records.{force_id}.last_command_tick", "state.lasting_tick"
                ),
                *_scene_position("{force_id}", "_force_command_origin"),
                _call(
                    "measure_srd_information_distance",
                    {"start": "{_force_command_origin}", "end": "{destination}"},
                    MEASURE,
                ),
                _require(
                    "_scene_distance <= _force_profile.move_feet",
                    "The force cannot move that far with this command.",
                ),
                _when(
                    "_force_record.spell == 'arcane_sword'",
                    [
                        *_scene_position("{_caster_state_id}", "_force_command_caster"),
                        _call(
                            "srd_information_barriers",
                            {
                                "start": "{_force_command_caster}",
                                "end": "{destination}",
                                "rule": "light",
                            },
                            ("_information_blocked",),
                        ),
                        _call(
                            "measure_srd_information_distance",
                            {"start": "{_force_command_caster}", "end": "{destination}"},
                            MEASURE,
                        ),
                        _call(
                            "resolve_srd_sight",
                            {
                                "observer_state": "{_caster_state_id}",
                                "distance": "{ceil(_scene_distance)}",
                                "lighting": "{destination_lighting}",
                                "obstruction": "clear",
                            },
                            ("_sight_can_see",),
                        ),
                        _require(
                            "_information_blocked != true and _sight_can_see == true",
                            "Arcane Sword moves to a spot the caster can see.",
                        ),
                    ],
                ),
                _set("_force_command_ended", False),
                _when(
                    "_force_record.spell == 'unseen_servant'",
                    [
                        *_scene_position("{_caster_state_id}", "_force_command_caster"),
                        _call(
                            "measure_srd_information_distance",
                            {"start": "{_force_command_caster}", "end": "{destination}"},
                            MEASURE,
                        ),
                        _when(
                            "_scene_distance > 60",
                            [
                                _call("remove_srd_spell_force", {"force_id": "{force_id}"}),
                                _set("_force_command_ended", True),
                            ],
                        ),
                        _when(
                            "_force_command_ended != true",
                            [
                                _require(
                                    "len(task) > 0 and len(interaction_subject) > 0",
                                    "Record the servant's simple task and actual object.",
                                ),
                                *_scene_position("{interaction_subject}", "_servant_task_position"),
                                _require(
                                    "_info_scene_entity.metadata.is_object == true",
                                    "The servant interacts with an actual admitted object.",
                                ),
                                _call(
                                    "measure_srd_information_distance",
                                    {"start": "{destination}", "end": "{_servant_task_position}"},
                                    MEASURE,
                                ),
                                _require(
                                    "_scene_distance <= 5",
                                    "The servant must reach the task's object.",
                                ),
                                _set("state.spell_force_records.{force_id}.task", "task"),
                                _set(
                                    "state.spell_force_records.{force_id}.interaction_subject",
                                    "interaction_subject",
                                ),
                            ],
                        ),
                    ],
                ),
                _when(
                    "_force_command_ended != true",
                    [
                        _call(
                            "update_srd_spell_force_scene",
                            {
                                "force_id": "{force_id}",
                                "position": "{destination}",
                                "active": True,
                                "lighting": "{destination_lighting}",
                            },
                        ),
                        _when(
                            "_force_profile.base_dice > 0 and force_target != null",
                            [
                                _call(
                                    "attack_srd_spell_force",
                                    {
                                        "caster_state": "{_caster_state_id}",
                                        "force_id": "{force_id}",
                                        "force_target": "{_force_target_id}",
                                    },
                                    ("_spell_attack_outcome", "_spell_damage_amount"),
                                ),
                            ],
                        ),
                    ],
                ),
                _set("_force_command_spell", "_force_record.spell"),
            ],
        ),
        "exploration",
        "Spell force command",
        [("Spell", "_force_command_spell"), ("Ended", "_force_command_ended")],
    )
    return {"command_srd_spell_force": command}


def _walk(effects):
    for effect in effects:
        yield effect
        for key in ("on_true", "on_false"):
            yield from _walk(effect.get(key, []))
        yield from _walk(effect.get("for_each", {}).get("do", []))


def _replace_created_cast_dispatch(cast, family, branch):
    """Replace our dispatch once, retaining the existing interrupted-casting guard."""
    marker = "family == '" + family + "'"

    def remove_owned(effects):
        effects[:] = [e for e in effects if marker not in e.get("condition", "")]
        for effect in effects:
            for key in ("on_true", "on_false"):
                remove_owned(effect.get(key, []))
            remove_owned(effect.get("for_each", {}).get("do", []))

    remove_owned(cast["effects"])
    dispatch = next(
        (
            e["on_true"]
            for e in _walk(cast["effects"])
            if e.get("condition") == "_pending_spell_operation_lost != true"
        ),
        cast["effects"],
    )
    dispatch.append(branch)


def _choose(condition, on_true, on_false):
    """Render a closed runtime branch with both outcomes."""
    return {"condition": condition, "on_true": on_true, "on_false": on_false}


def _dragon_language_hooks(tools):
    """Save printed caster-language comprehension without granting those spoken languages."""
    install = deepcopy(tools["install_srd_summoned_creature"])
    creation = next(e for e in install["effects"] if "create_entity" in e)
    creation["create_entity"]["value"]["summon_understood_languages"] = "_summon_known_languages"
    if not any(
        e.get("set", {}).get("field") == "_summon_known_languages" for e in install["effects"]
    ):
        index = install["effects"].index(creation)
        install["effects"][index:index] = [
            _set("_summon_known_languages", []),
            _when(
                "spell == 'summon_dragon' and understands_caster_languages == true "
                "and caster_state.languages != null",
                [_set("_summon_known_languages", "caster_state.languages")],
            ),
        ]
    language = deepcopy(tools["resolve_srd_spell_language"])
    condition = (
        "listener_state.summon_spell_id == 'summon_dragon' and "
        "listener_state.summon_active == true and listener_state.is_alive == true and "
        "listener_state.understands_caster_languages == true and "
        "listener_state.summon_understood_languages != null"
    )
    language["effects"] = [e for e in language["effects"] if e.get("condition") != condition]
    index = next(
        i
        for i, e in enumerate(language["effects"])
        if e.get("set", {}).get("value")
        == "_language_understands == true and _language_perceived == true"
    )
    language["effects"].insert(
        index,
        _when(
            condition,
            [
                _when(
                    "_utterance_language != '' and _utterance_language in "
                    "listener_state.summon_understood_languages and "
                    "_utterance_language in _language_speaker_native",
                    [_set("_language_understands", True)],
                ),
            ],
        ),
    )
    return {"install_srd_summoned_creature": install, "resolve_srd_spell_language": language}


def _created_actor_occupancy_tools():
    """Use neutral overlap candidates and actual living bodies for source birth occupancy."""
    living = _tool(
        "Read the actual bound creature's life and type before treating its volume as occupied.",
        [_entity("actor_state")],
        [
            _set(
                "_occupant_living",
                "actor_state.is_alive == true and actor_state.creature_type != 'object' and "
                "actor_state.creature_type != 'spell_force'",
            )
        ],
    )
    living["pre_conditions"] = [
        {
            "condition": "actor_state.is_alive != true or actor_state.creature_type != null",
            "error": "Unoccupied source space requires the overlapping living actor's actual type.",
        }
    ]
    unoccupied = _tool_scene(
        "Check actual living creature interiors regardless of their movement collision flag.",
        [_parameter("summon_id")],
        _scene_position("{summon_id}", "_birth_origin")
        + [
            _set("_birth_body", "_info_scene_entity"),
            _require(
                "_birth_body.footprint.kind == 'box'",
                "The printed source's actual occupied space must be an admitted box.",
            ),
            _set(
                "_birth_min",
                [
                    "_birth_origin[0] - _birth_body.footprint.half_extents[0]",
                    "_birth_origin[1] - _birth_body.footprint.half_extents[1]",
                    "_birth_origin[2]",
                ],
            ),
            _set(
                "_birth_max",
                [
                    "_birth_origin[0] + _birth_body.footprint.half_extents[0]",
                    "_birth_origin[1] + _birth_body.footprint.half_extents[1]",
                    "_birth_origin[2] + _birth_body.height",
                ],
            ),
            _spatial(
                "area",
                {
                    "map_id": "{state.spatial_scene.map.id}",
                    "shape": {
                        "kind": "box",
                        "min": "{_birth_min}",
                        "max": "{_birth_max}",
                    },
                    "include": ["entities"],
                    "max_results": 4096,
                },
                "_birth_overlap",
            ),
            _require(
                "_birth_overlap.truncated != true",
                "The actual occupied-space candidates must be complete.",
            ),
            _set("_birth_candidates", "_birth_overlap.results"),
            _require(
                "len(_birth_candidates) > 0", "The actual staged source volume must be present."
            ),
            _set("_birth_candidate_ids", []),
            _loop(
                "_birth_candidates",
                "_birth_candidate",
                [
                    _push("_birth_candidate_ids", "_birth_candidate.id"),
                ],
            ),
            _set("_birth_scene_rows", "state.spatial_scene.entities"),
            _loop(
                "_birth_scene_rows",
                "_occupant",
                [
                    _when(
                        "_occupant.id in _birth_candidate_ids and _occupant.id != summon_id and "
                        "(_occupant.metadata.participant_ref != null or "
                        "(_occupant.metadata.creature_type != null and "
                        "_occupant.metadata.creature_type != 'spell_force' and "
                        "_occupant.metadata.creature_type != 'object' and "
                        "_occupant.metadata.is_object != true))",
                        [
                            _set("_occupant_origin", "_occupant.position"),
                            _when(
                                "(_occupant.metadata.plane == _birth_body.metadata.plane "
                                "or (_occupant.metadata.plane == null and "
                                "_birth_body.metadata.plane == 'material')) and "
                                "_occupant_origin[2] < _birth_max[2] and "
                                "_occupant_origin[2] + _occupant.height > _birth_min[2]",
                                [
                                    _set("_occupant_interior", False),
                                    _when(
                                        "_occupant.footprint.kind == 'box'",
                                        [
                                            _set(
                                                "_occupant_interior",
                                                "_occupant_origin[0] - "
                                                "_occupant.footprint.half_extents[0] < "
                                                "_birth_max[0] and _occupant_origin[0] + "
                                                "_occupant.footprint.half_extents[0] > "
                                                "_birth_min[0] and _occupant_origin[1] - "
                                                "_occupant.footprint.half_extents[1] < "
                                                "_birth_max[1] and _occupant_origin[1] + "
                                                "_occupant.footprint.half_extents[1] > "
                                                "_birth_min[1]",
                                            ),
                                        ],
                                    ),
                                    _when(
                                        "_occupant.footprint.kind == 'circle'",
                                        [
                                            _set(
                                                "_occupant_nearest",
                                                {
                                                    "x": "min(_birth_max[0], max(_birth_min[0], "
                                                    "_occupant_origin[0]))",
                                                    "y": "min(_birth_max[1], max(_birth_min[1], "
                                                    "_occupant_origin[1]))",
                                                },
                                            ),
                                            _set(
                                                "_occupant_interior",
                                                "(_occupant_origin[0] - _occupant_nearest.x) "
                                                "** 2 + (_occupant_origin[1] - "
                                                "_occupant_nearest.y) ** 2 < "
                                                "_occupant.footprint.radius ** 2",
                                            ),
                                        ],
                                    ),
                                    _when(
                                        "_occupant_interior == true",
                                        [
                                            _require(
                                                "_occupant.metadata.participant_ref != null",
                                                "The overlapping creature needs its actual "
                                                "bound mechanics actor.",
                                            ),
                                            _call(
                                                "qualify_srd_created_actor_occupant",
                                                {
                                                    "actor_state": "{_occupant.metadata."
                                                    "participant_ref}",
                                                },
                                                ("_occupant_living",),
                                            ),
                                            _require(
                                                "_occupant_living != true",
                                                "The source needs unoccupied space outside "
                                                "actual living creature interiors.",
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
    return {
        "qualify_srd_created_actor_unoccupied": unoccupied,
        "qualify_srd_created_actor_occupant": living,
    }
def _created_actor_placement_tools():
    """Place a printed Large actor using the sole admitted scene and real sight owner."""
    create = _tool_scene(
        "Qualify and place a source-sized created actor in an actually unoccupied visible space.",
        [
            _entity("caster_state"),
            _parameter("summon_id"),
            _parameter("spell"),
            _parameter("range_feet", "number"),
            _parameter("half_extent", "number"),
            _parameter("creature_type"),
            _optional("requires_sight", "boolean", True),
            *deepcopy(ACTOR_PLACEMENT_CHOICES),
        ],
        _snapshot()
        + _scene_position("{_caster_state_id}", "_created_actor_caster_origin")
        + [
            _require(
                "summon_id != null and len(summon_id) > 0", "Choose a unique created actor ID."
            ),
            _require(
                "created_actor_height != null", "Admit the created actor's physical scene height."
            ),
            _require(
                "requires_sight == true or spell == 'conjure_elemental' or "
                "spell == 'find_familiar' or spell == 'find_steed' or spell == 'animate_dead'",
                "Only a printed source without sight-qualified placement can omit sight.",
            ),
            _set("_created_actor_plane", "_info_scene_entity.metadata.plane"),
            _require("_created_actor_plane != null", "The created actor needs its actual plane."),
            _set(
                "_created_actor_sight_origin",
                [
                    "_created_actor_caster_origin[0]",
                    "_created_actor_caster_origin[1]",
                    "_created_actor_caster_origin[2] + _info_scene_entity.height * 0.5",
                ],
            ),
            _call(
                "measure_srd_information_distance",
                {"start": "{_created_actor_caster_origin}", "end": "{created_actor_position}"},
                MEASURE,
            ),
            _require(
                "_scene_distance <= range_feet", "The created actor's space is outside spell range."
            ),
            _call(
                "srd_information_barriers",
                {
                    "start": "{_created_actor_sight_origin}",
                    "end": "{created_actor_position}",
                    "rule": "solid",
                },
                ("_information_blocked",),
            ),
            _require(
                "_information_blocked != true", "The created actor needs a clear path to its space."
            ),
            _call(
                "resolve_srd_sight",
                {
                    "observer_state": "{_caster_state_id}",
                    "distance": "{ceil(_scene_distance)}",
                    "lighting": "{created_actor_lighting}",
                    "obstruction": "clear",
                },
                ("_sight_can_see",),
            ),
            _require(
                "requires_sight != true or _sight_can_see == true",
                "The caster must see the created actor's space.",
            ),
            *[
                _require(
                    f"created_actor_position[{axis}] - half_extent >= "
                    f"state.spatial_scene.map.bounds.min[{axis}] and "
                    f"created_actor_position[{axis}] + half_extent <= "
                    f"state.spatial_scene.map.bounds.max[{axis}]",
                    "The full printed footprint requires an unoccupied space within the map.",
                )
                for axis in range(2)
            ],
            _require(
                "created_actor_position[2] >= state.spatial_scene.map.bounds.min[2] and "
                "created_actor_position[2] + created_actor_height <= "
                "state.spatial_scene.map.bounds.max[2]",
                "The full printed footprint requires an unoccupied space within the map.",
            ),
            _set("_created_actor_prior_scene", "state.spatial_scene.entities"),
            _loop(
                "_created_actor_prior_scene",
                "_created_actor_existing",
                [
                    _require(
                        "_created_actor_existing.id != summon_id",
                        "The created actor needs a unique scene ID.",
                    ),
                ],
            ),
            _set(
                "_created_actor_scene",
                {
                    "id": "summon_id",
                    "map_id": "state.spatial_scene.map.id",
                    "position": "created_actor_position",
                    "footprint": {"kind": "'box'", "half_extents": ["half_extent", "half_extent"]},
                    "height": "created_actor_height",
                    "facing": [1, 0],
                    "active": True,
                    "movement": "'blocking'",
                    "visibility": "'opaque'",
                    "metadata": {
                        "source_ref": "spell",
                        "source_caster": "_caster_state_id",
                        "participant_ref": "summon_id",
                        "creature_type": "creature_type",
                        "plane": "_created_actor_plane",
                        "lighting": "created_actor_lighting",
                    },
                },
            ),
            _push("_created_actor_prior_scene", "_created_actor_scene"),
            _set("state.spatial_scene.entities", "_created_actor_prior_scene"),
            _set("state.spatial_scene.map.revision", "state.spatial_scene.map.revision + 1"),
            *_snapshot(),
            _set("_created_actor_missing_plane_matches", "_created_actor_plane == 'material'"),
            _spatial(
                "placement",
                {
                    "map_id": "{state.spatial_scene.map.id}",
                    "moving_entity_id": "{summon_id}",
                    "origin": "{created_actor_position}",
                    "exclude_entity_ids": [],
                    "metadata_filter": {
                        "key": "plane",
                        "value": "{_created_actor_plane}",
                        "missing_matches": "{_created_actor_missing_plane_matches}",
                    },
                    "max_candidates": 10000,
                },
                "_created_actor_placement",
            ),
            _require(
                "_created_actor_placement.supported == true",
                "Exact created-actor placement is unsupported for curved solids or this "
                "candidate bound.",
            ),
            _require(
                "_created_actor_placement.found == true and _created_actor_placement.distance == 0",
                "The full printed footprint requires an unoccupied space within the map.",
            ),
            _call("qualify_srd_created_actor_unoccupied", {"summon_id": "{summon_id}"}),
            *_snapshot(),
        ],
    )
    return _created_actor_occupancy_tools() | {"create_srd_source_actor_scene": create}


def _guardian_tools():
    """Resolve the printed guardian's fixed body, lifetime and actual enemy turn-start damage."""
    casting = _tool(
        "Create the invulnerable guardian without inventing a creature stat block.",
        [
            _entity("caster_state"),
            _parameter("spell"),
            _parameter("slot_level", "integer"),
            _parameter("casting_save_dc", "integer"),
            _parameter("force_id"),
            *deepcopy(ACTOR_PLACEMENT_CHOICES),
        ],
        [
            _lookup("spell", "_guardian_profile"),
            _require("spell == 'guardian_of_faith'", "This owner creates Guardian of Faith."),
            _require("force_id != null and len(force_id) > 0", "Choose a unique guardian ID."),
            _when(
                "state.spell_force_ids == null",
                [
                    _set("state.spell_force_ids", []),
                    _set("state.spell_force_records", {}),
                ],
            ),
            _require(
                "force_id not in state.spell_force_records",
                "The guardian identity is already used.",
            ),
            _call(
                "create_srd_source_actor_scene",
                {
                    "caster_state": "{_caster_state_id}",
                    "summon_id": "{force_id}",
                    "spell": "{spell}",
                    "range_feet": "{_guardian_profile.range_feet}",
                    "half_extent": 5,
                    "creature_type": "spell_force",
                    **{p["name"]: "{" + p["name"] + "}" for p in ACTOR_PLACEMENT_CHOICES},
                },
            ),
            _when("state.lasting_tick == null", [_set("state.lasting_tick", 0)]),
            _set(
                "_guardian_record",
                {
                    "id": "force_id",
                    "spell": "spell",
                    "caster": "_caster_state_id",
                    "active": True,
                    "started_tick": "state.lasting_tick",
                    "expires_tick": "state.lasting_tick + _guardian_profile.duration_rounds * 2",
                    "slot_level": "slot_level",
                    "save_dc": "casting_save_dc",
                    "damage_dealt": 0,
                },
            ),
            _set("state.spell_force_records.{force_id}", "_guardian_record"),
            _set("_guardian_registry", "state.spell_force_ids"),
            _push("_guardian_registry", "force_id"),
            _set("state.spell_force_ids", "_guardian_registry"),
            _set("state.has_spell_forces", True),
            _set("state.has_guardian_sources", True),
            _call(
                "record_srd_spell_target_result",
                {
                    "target": "{_caster_state_id}",
                    "brief": "Guardian of Faith created",
                    "detail": "Large invulnerable guardian {force_id}; "
                    "lasts 8 hours or until 60 damage dealt.",
                },
                ("_spell_target_summary", "_spell_target_detail"),
            ),
            _set("_spell_summary_rows", ["_spell_target_summary"]),
            _set("_spell_detail_rows", ["_spell_target_detail"]),
        ],
    )
    distance = _tool_scene(
        "Measure exact nearest distance between the guardian box and an admitted target box.",
        [_parameter("force_id"), _entity("target_state")],
        _scene_position("{force_id}", "_guardian_origin")
        + [
            _set("_guardian_scene", "_info_scene_entity"),
            *_scene_position("{_target_state_id}", "_guardian_target_position"),
            _set("_guardian_target_scene", "_info_scene_entity"),
            _require(
                "_guardian_scene.footprint.kind == 'box' and "
                "_guardian_target_scene.footprint.kind == 'box'",
                "Guardian radius qualification currently requires admitted box footprints.",
            ),
            _set(
                "_guardian_target_plane_matches",
                "_guardian_target_scene.metadata.plane == _guardian_scene.metadata.plane",
            ),
            _set(
                "_guardian_gap_x",
                "max(0, abs(_guardian_target_position[0] - _guardian_origin[0]) - "
                "_guardian_scene.footprint.half_extents[0] - "
                "_guardian_target_scene.footprint.half_extents[0])",
            ),
            _set(
                "_guardian_gap_y",
                "max(0, abs(_guardian_target_position[1] - _guardian_origin[1]) - "
                "_guardian_scene.footprint.half_extents[1] - "
                "_guardian_target_scene.footprint.half_extents[1])",
            ),
            _set(
                "_guardian_gap_z",
                "max(0, _guardian_target_position[2] - _guardian_origin[2] - "
                "_guardian_scene.height, _guardian_origin[2] - "
                "_guardian_target_position[2] - _guardian_target_scene.height)",
            ),
            _set(
                "_guardian_distance_squared",
                "_guardian_gap_x ** 2 + _guardian_gap_y ** 2 + _guardian_gap_z ** 2",
            ),
            _set(
                "_guardian_observer",
                [
                    "_guardian_origin[0]",
                    "_guardian_origin[1]",
                    "_guardian_origin[2] + _guardian_scene.height * 0.5",
                ],
            ),
        ],
    )
    damage = _tool_scene(
        "Resolve a qualified guardian event through the existing saving throw and damage owners.",
        [_parameter("force_id"), _entity("target_state")],
        _snapshot()
        + [
            _set("_guardian_record", "state.spell_force_records[force_id]"),
            _lookup("_guardian_record.spell", "_guardian_profile"),
            _set("_guardian_half_cover", []),
            _set("_guardian_three_quarters", []),
            _spatial(
                "exposure",
                {
                    "map_id": "{state.spatial_scene.map.id}",
                    "observer": {"point": "{_guardian_observer}"},
                    "target": {"entity_id": "{_guardian_target_scene.id}"},
                    "samples": {"horizontal": 3, "vertical": 3},
                    "include": ["geometry"],
                },
                "_guardian_exposure",
            ),
            _when(
                "_guardian_exposure.visible_samples > 0",
                [
                    _when(
                        "_guardian_exposure.visible_samples < len(_guardian_exposure.samples)",
                        [
                            _require(
                                "_guardian_target_scene.metadata.cover_from != null",
                                "Admit partial Cover from the actual guardian source.",
                            ),
                            _set(
                                "_guardian_cover",
                                "_guardian_target_scene.metadata.cover_from[force_id]",
                            ),
                            _require(
                                "_guardian_cover == 'none' or _guardian_cover == 'half' or "
                                "_guardian_cover == 'three_quarters'",
                                "Qualify actual guardian partial Cover.",
                            ),
                            _when(
                                "_guardian_cover == 'half'",
                                [_push("_guardian_half_cover", "_target_state_id")],
                            ),
                            _when(
                                "_guardian_cover == 'three_quarters'",
                                [_push("_guardian_three_quarters", "_target_state_id")],
                            ),
                        ],
                    ),
                    _call(
                        "resolve_spell_area_save_damage_target",
                        {
                            "spell": "guardian_of_faith",
                            "target": "{_target_state_id}",
                            "save_ability": "{_guardian_profile.save_ability}",
                            "spell_save_dc": "{_guardian_record.save_dc}",
                            "damage_total": "{_guardian_profile.damage_amount}",
                            "damage_type": "{_guardian_profile.damage_type}",
                            "success_damage": "{_guardian_profile.success_damage}",
                            "construct_save_disadvantage": False,
                            "half_cover_targets": "{_guardian_half_cover}",
                            "three_quarters_cover_targets": "{_guardian_three_quarters}",
                            "source_state_id": "{_guardian_record.caster}",
                        },
                        ("_spell_summary_rows", "_spell_detail_rows", "_affliction_trigger_damage"),
                    ),
                    _set(
                        "state.spell_force_records.{force_id}.damage_dealt",
                        "_guardian_record.damage_dealt + _affliction_trigger_damage",
                    ),
                    _when(
                        "_guardian_record.damage_dealt + _affliction_trigger_damage >= "
                        "_guardian_profile.damage_limit",
                        [
                            _call("remove_srd_spell_force", {"force_id": "{force_id}"}),
                        ],
                    ),
                ],
            ),
        ],
    )
    # Scene-derived positions remain local to the qualification call and are passed to damage.
    damage["parameters"] += [
        _parameter("observer", "array", items={"type": "number"}, min_items=3, max_items=3),
        _parameter("target_scene", "object", from_state="state.spatial_scene"),
    ]
    for e in _walk(damage["effects"]):
        if "spatial_query" in e:
            query = e["spatial_query"].get("query", {})
            if query.get("observer"):
                query["observer"]["point"] = "{observer}"
                query["target"]["entity_id"] = "{target_scene.id}"
        setting = e.get("set", {})
        if isinstance(setting.get("value"), str):
            setting["value"] = setting["value"].replace("_guardian_target_scene", "target_scene")
        requirement = e.get("require", {})
        if isinstance(requirement.get("condition"), str):
            requirement["condition"] = requirement["condition"].replace(
                "_guardian_target_scene", "target_scene"
            )
    event = _tool(
        "Trigger active guardian sources only at the actual enemy's turn start.",
        [_entity("target_state")],
        [
            _when(
                "state.has_guardian_sources == true and target_state.is_alive == true",
                [
                    _set("_guardian_event_ids", "state.spell_force_ids"),
                    _loop(
                        "_guardian_event_ids",
                        "_guardian_id",
                        [
                            _set(
                                "_guardian_event_record", "state.spell_force_records[_guardian_id]"
                            ),
                            _when(
                                "_guardian_event_record.active == true and "
                                "_guardian_event_record.spell == 'guardian_of_faith' and "
                                "target_state.is_alive == true and "
                                "state.lasting_tick < _guardian_event_record.expires_tick",
                                [
                                    _call(
                                        "qualify_srd_scene_enemy",
                                        {
                                            "caster_id": "{_guardian_event_record.caster}",
                                            "target_id": "{_target_state_id}",
                                        },
                                        ("_scene_is_enemy",),
                                    ),
                                    _when(
                                        "_scene_is_enemy == true",
                                        [
                                            _lookup(
                                                "_guardian_event_record.spell",
                                                "_guardian_event_profile",
                                            ),
                                            _call(
                                                "measure_srd_guardian_distance",
                                                {
                                                    "force_id": "{_guardian_id}",
                                                    "target_state": "{_target_state_id}",
                                                },
                                                (
                                                    "_guardian_distance_squared",
                                                    "_guardian_target_plane_matches",
                                                    "_guardian_observer",
                                                    "_guardian_target_scene",
                                                ),
                                            ),
                                            _when(
                                                "_guardian_target_plane_matches == true and "
                                                "_guardian_distance_squared <= "
                                                "_guardian_event_profile.reach_feet ** 2",
                                                [
                                                    _call(
                                                        "damage_srd_guardian_target",
                                                        {
                                                            "force_id": "{_guardian_id}",
                                                            "target_state": "{_target_state_id}",
                                                            "observer": "{_guardian_observer}",
                                                            "target_scene": (
                                                                "{_guardian_target_scene}"
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
            ),
        ],
    )
    return {
        "cast_spell_created_guardian": casting,
        "measure_srd_guardian_distance": distance,
        "damage_srd_guardian_target": damage,
        "start_srd_guardian_turn": event,
    }


def _guardian_hooks(tools):
    """Compose guardian source events with the existing public cast, clock and turn owners."""
    changed = _guardian_tools()
    for name in ("cast_spell", "cast_spell_outside_combat"):
        if name not in tools:
            continue
        cast = deepcopy(tools[name])
        guardian_condition = (
            "_spell_cast_failed != true and "
            + "(_spell_definition.family == 'created_spell_guardian')"
        )
        _replace_created_cast_dispatch(
            cast,
            "created_spell_guardian",
            _when(
                guardian_condition,
                [
                    _call(
                        "cast_spell_created_guardian",
                        {
                            "caster_state": "{_caster_state_id}",
                            "spell": "{spell}",
                            "slot_level": "{_slot_level}",
                            "casting_save_dc": "{_casting_profile.save_dc}",
                            "force_id": "{force_id}",
                            **{p["name"]: "{" + p["name"] + "}" for p in ACTOR_PLACEMENT_CHOICES},
                        },
                        ("_spell_summary_rows", "_spell_detail_rows"),
                    ),
                ],
            ),
        )
        changed[name] = cast
    start = deepcopy(tools["begin_combat_turn"])
    if not any(p["name"] == "lasting_state" for p in start["parameters"]):
        start["parameters"].append(
            _optional("lasting_state", "string", "state", resolve="entity", model_hidden=True)
        )
    start["effects"] = [e for e in start["effects"] if "start_srd_guardian_turn" not in str(e)]
    start["effects"].append(
        _when(
            "lasting_state.has_guardian_sources == true",
            [
                _call("start_srd_guardian_turn", {"target_state": "{_actor_id}"}),
            ],
        )
    )
    changed["begin_combat_turn"] = start
    return changed


def _dragon_placement_hooks(tools):
    """Replace caller placement verdicts while retaining creature and concentration owners."""
    changed = _created_actor_placement_tools()
    install = deepcopy(tools["install_srd_summoned_creature"])
    if not any(p["name"] == "scene_proxy_created" for p in install["parameters"]):
        install["parameters"].append(_optional("scene_proxy_created", "boolean", False))
    body = next(e["create_entity"]["value"] for e in install["effects"] if "create_entity" in e)
    body["summon_has_scene_proxy"] = "scene_proxy_created"
    body["initiative_modifier"] = "(dexterity_score - 10) // 2"
    changed["install_srd_summoned_creature"] = install
    for name in ("cast_spell", "cast_spell_node", "cast_spell_outside_combat"):
        if name not in tools:
            continue
        cast = deepcopy(tools[name])
        branches = [
            e
            for e in _walk(cast["effects"])
            if "family == 'summoned_creature'" in e.get("condition", "")
        ]
        if not branches:
            continue
        existing = {p["name"] for p in cast["parameters"]}
        cast["parameters"] += [
            deepcopy(p) for p in ACTOR_PLACEMENT_CHOICES if p["name"] not in existing
        ]
        for branch in branches:
            branch["on_true"] = [
                e
                for e in branch["on_true"]
                if "summon_space_unoccupied" not in e.get("require", {}).get("condition", "")
            ]
            if "create_srd_source_actor_scene" not in str(branch["on_true"]):
                branch["on_true"].insert(
                    0,
                    _call(
                        "create_srd_source_actor_scene",
                        {
                            "caster_state": "{_caster_state_id}",
                            "summon_id": "{summon_id}",
                            "spell": "{spell}",
                            "range_feet": "{_spell_definition.range_feet}",
                            "half_extent": 5,
                            "creature_type": "{_spell_definition.creature_type}",
                            **{p["name"]: "{" + p["name"] + "}" for p in ACTOR_PLACEMENT_CHOICES},
                        },
                    ),
                )
            next(e for e in branch["on_true"] if e.get("call") == "install_srd_summoned_creature")[
                "params"
            ]["scene_proxy_created"] = True
        changed[name] = cast
    dismiss = deepcopy(tools["dismiss_srd_summoned_creature"])
    if "summon_has_scene_proxy" not in str(dismiss["effects"]):
        dismiss["effects"].append(
            _when(
                "summon_state.summon_has_scene_proxy == true",
                [
                    *_scene_position("{_summon_state_id}", "_summon_retired_position"),
                    _call(
                        "update_srd_spell_force_scene",
                        {
                            "force_id": "{_summon_state_id}",
                            "position": "{_summon_retired_position}",
                            "active": False,
                        },
                    ),
                    _set("summon_state.summon_has_scene_proxy", False),
                ],
            )
        )
    changed["dismiss_srd_summoned_creature"] = dismiss
    return changed


def _hound_anchor_tools():
    """Keep the intangible Hound's origin separate from its nonmechanical depiction."""
    sight = _tool_scene(
        "Resolve actual sight to an intangible anchor from the caster's admitted scene.",
        [_entity("caster_state"), _array("position"), _parameter("lighting")],
        _scene_position("{_caster_state_id}", "_hound_observer_origin")
        + [
            _set("_hound_observer_plane", "_info_scene_entity.metadata.plane"),
            _set(
                "_hound_eye",
                [
                    "_hound_observer_origin[0]",
                    "_hound_observer_origin[1]",
                    "_hound_observer_origin[2] + _info_scene_entity.height * 0.5",
                ],
            ),
            _call(
                "measure_srd_information_distance",
                {"start": "{_hound_observer_origin}", "end": "{position}"},
                MEASURE,
            ),
            _set("_hound_observer_distance", "_scene_distance"),
            _call(
                "srd_information_barriers",
                {"start": "{_hound_eye}", "end": "{position}", "rule": "light"},
                ("_information_blocked",),
            ),
            _set("_hound_sight_blocked", "_information_blocked"),
            _call(
                "resolve_srd_environment_segment",
                {
                    "start": "{_hound_eye}",
                    "end": "{position}",
                    "plane": "{_hound_observer_plane}",
                },
                ("_environment_sight_obscured",),
            ),
            _call(
                "resolve_srd_information_light",
                {
                    "point": "{position}",
                    "plane": "{_hound_observer_plane}",
                    "ambient": "{lighting}",
                },
                ("_information_light",),
            ),
            _call(
                "resolve_srd_sight",
                {
                    "observer_state": "{_caster_state_id}",
                    "distance": "{ceil(_hound_observer_distance)}",
                    "lighting": "{_information_light}",
                    "obstruction": "clear",
                    "subject_invisible": False,
                    "subject_ethereal": False,
                },
                ("_sight_can_see",),
            ),
            _set(
                "_hound_anchor_visible",
                "_sight_can_see == true and _hound_sight_blocked != true and "
                "_environment_sight_obscured != true",
            ),
        ],
    )
    occupy = _tool_scene(
        "Check exact point occupancy without assigning the intangible Hound a mechanical size.",
        [_array("position"), _parameter("plane")],
        [
            _set("_hound_origin_unoccupied", True),
            _set("_hound_bodies", "state.spatial_scene.entities"),
            _loop(
                "_hound_bodies",
                "_hb",
                [
                    _when(
                        "_hb.active == true and _hb.movement == 'blocking' and "
                        "_hb.metadata.plane == plane and position[2] >= _hb.position[2] "
                        "and position[2] <= _hb.position[2] + _hb.height",
                        [
                            _choose(
                                "_hb.footprint.kind == 'box'",
                                [
                                    _when(
                                        "abs(position[0] - _hb.position[0]) <= "
                                        "_hb.footprint.half_extents[0] and "
                                        "abs(position[1] - _hb.position[1]) <= "
                                        "_hb.footprint.half_extents[1]",
                                        [_set("_hound_origin_unoccupied", False)],
                                    )
                                ],
                                [
                                    _require(
                                        "_hb.footprint.kind == 'circle'",
                                        "Hound point placement requires admitted "
                                        "box or circle bodies.",
                                    ),
                                    _when(
                                        "(position[0] - _hb.position[0]) ** 2 + "
                                        "(position[1] - _hb.position[1]) ** 2 <= "
                                        "_hb.footprint.radius ** 2",
                                        [_set("_hound_origin_unoccupied", False)],
                                    ),
                                ],
                            )
                        ],
                    )
                ],
            ),
            _set("_hound_geometry", "state.spatial_scene.geometry"),
            _loop(
                "_hound_geometry",
                "_hg",
                [
                    _when(
                        "_hg.active == true and _hg.movement == 'blocking' and "
                        "(_hg.metadata.plane == null or _hg.metadata.plane == plane)",
                        [
                            _call(
                                "srd_information_volume_point",
                                {"origin": "{position}", "volume": "{_hg.volume}"},
                                ("_volume_point",),
                            ),
                            _when(
                                "_volume_point[0] == position[0] and "
                                "_volume_point[1] == position[1] and "
                                "_volume_point[2] == position[2]",
                                [_set("_hound_origin_unoccupied", False)],
                            ),
                        ],
                    )
                ],
            ),
        ],
    )
    casting = _tool_scene(
        "Create the printed phantom Hound as an invulnerable intangible anchor "
        "without creature stats.",
        [
            _entity("caster_state"),
            _parameter("spell"),
            _parameter("casting_save_dc", "integer"),
            _parameter("force_id"),
            _array("force_position"),
            _optional("hound_password", default=None),
            _parameter("hound_lighting"),
        ],
        [
            _require("spell == 'faithful_hound'", "This owner creates Faithful Hound."),
            _require(
                "force_id != null and len(force_id) > 0 and "
                "hound_password != null and len(hound_password) > 0",
                "Specify the Hound's unique identity and password when casting.",
            ),
            _require("hound_lighting != null", "Admit the Hound origin's actual ambient lighting."),
            _when(
                "state.spell_force_records == null",
                [_set("state.spell_force_ids", []), _set("state.spell_force_records", {})],
            ),
            _require(
                "force_id not in state.spell_force_records", "The Hound identity is already used."
            ),
            _lookup("spell", "_hound_profile"),
            *_scene_position("{_caster_state_id}", "_hound_cast_origin"),
            _set("_hound_cast_plane", "_info_scene_entity.metadata.plane"),
            _call(
                "measure_srd_information_distance",
                {"start": "{_hound_cast_origin}", "end": "{force_position}"},
                MEASURE,
            ),
            _require(
                "_scene_distance <= _hound_profile.range_feet", "The Hound origin is outside range."
            ),
            _call(
                "qualify_srd_hound_point_occupancy",
                {"position": "{force_position}", "plane": "{_hound_cast_plane}"},
                ("_hound_origin_unoccupied",),
            ),
            _require("_hound_origin_unoccupied == true", "The Hound needs an unoccupied origin."),
            _call(
                "srd_information_barriers",
                {"start": "{_hound_cast_origin}", "end": "{force_position}", "rule": "solid"},
                ("_information_blocked",),
            ),
            _require(
                "_information_blocked != true", "The Hound origin needs a physical clear path."
            ),
            _call(
                "qualify_srd_hound_anchor_sight",
                {
                    "caster_state": "{_caster_state_id}",
                    "position": "{force_position}",
                    "lighting": "{hound_lighting}",
                },
                ("_hound_anchor_visible",),
            ),
            _require("_hound_anchor_visible == true", "The caster must see the Hound's origin."),
            _when("state.lasting_tick == null", [_set("state.lasting_tick", 0)]),
            _set("_hound_created_turn", 0),
            _when(
                "caster_state.turns_started != null",
                [_set("_hound_created_turn", "caster_state.turns_started")],
            ),
            _set(
                "_hound_record",
                {
                    "id": "force_id",
                    "spell": "spell",
                    "caster": "_caster_state_id",
                    "active": True,
                    "started_tick": "state.lasting_tick",
                    "expires_tick": "state.lasting_tick + _hound_profile.duration_rounds * 2",
                    "created_turn": "_hound_created_turn",
                    "last_bite_turn": "_hound_created_turn",
                    "save_dc": "casting_save_dc",
                    "password": "hound_password",
                },
            ),
            _set("state.spell_force_records.{force_id}", "_hound_record"),
            _set("_hound_registry", "state.spell_force_ids"),
            _push("_hound_registry", "force_id"),
            _set("state.spell_force_ids", "_hound_registry"),
            _set("state.has_spell_forces", True),
            _set("state.has_hound_sources", True),
            _call(
                "create_srd_spell_force_scene",
                {
                    "caster_state": "{_caster_state_id}",
                    "force_id": "{force_id}",
                    "spell": "{spell}",
                    "position": "{force_position}",
                },
            ),
            _call(
                "update_srd_spell_force_scene",
                {
                    "force_id": "{force_id}",
                    "position": "{force_position}",
                    "active": True,
                    "lighting": "{hound_lighting}",
                },
            ),
            _call(
                "record_srd_spell_target_result",
                {
                    "target": "{_caster_state_id}",
                    "brief": "Faithful Hound created",
                    "detail": "Intangible invulnerable anchor {force_id}; "
                    "eight-hour source, caster-only visibility and Truesight30.",
                },
                ("_spell_target_summary", "_spell_target_detail"),
            ),
            _set("_spell_summary_rows", ["_spell_target_summary"]),
            _set("_spell_detail_rows", ["_spell_target_detail"]),
        ],
    )
    registry = _tool(
        "Retain a cheap guard only while a live Hound source remains registered.",
        [],
        [
            _set("state.has_hound_sources", False),
            _set("_hound_flag_ids", "state.spell_force_ids"),
            _loop(
                "_hound_flag_ids",
                "_hound_flag_id",
                [
                    _when(
                        "state.spell_force_records[_hound_flag_id].spell == 'faithful_hound' and "
                        "state.spell_force_records[_hound_flag_id].active == true",
                        [_set("state.has_hound_sources", True)],
                    )
                ],
            ),
        ],
    )
    separation = _tool_scene(
        "End a Hound when its exact admitted origin and caster are more than300feet apart.",
        [_entity("caster_state"), _parameter("force_id")],
        _scene_position("{_caster_state_id}", "_hound_caster_origin")
        + [
            _set("_hound_caster_plane", "_info_scene_entity.metadata.plane"),
            *_scene_position("{force_id}", "_hound_anchor_origin"),
            _require(
                "_hound_caster_plane == _info_scene_entity.metadata.plane",
                "Cross-plane Hound separation is not yet supported.",
            ),
            _call(
                "measure_srd_information_distance",
                {"start": "{_hound_caster_origin}", "end": "{_hound_anchor_origin}"},
                MEASURE,
            ),
            _when(
                "_scene_distance > 300",
                [_call("remove_srd_spell_force", {"force_id": "{force_id}"})],
            ),
        ],
    )
    refresh = _tool(
        "Check active Hound separation after an actual movement or clock boundary.",
        [],
        [
            _when(
                "state.has_hound_sources == true",
                [
                    _set("_hound_separation_ids", "state.spell_force_ids"),
                    _loop(
                        "_hound_separation_ids",
                        "_hound_separation_id",
                        [
                            _set(
                                "_hound_separation_record",
                                "state.spell_force_records[_hound_separation_id]",
                            ),
                            _when(
                                "_hound_separation_record.spell == 'faithful_hound' and "
                                "_hound_separation_record.active == true",
                                [
                                    _call(
                                        "check_srd_hound_separation",
                                        {
                                            "caster_state": "{_hound_separation_record.caster}",
                                            "force_id": "{_hound_separation_id}",
                                        },
                                    )
                                ],
                            ),
                        ],
                    ),
                ],
            )
        ],
    )
    return {
        "qualify_srd_hound_anchor_sight": sight,
        "qualify_srd_hound_point_occupancy": occupy,
        "cast_spell_created_hound": casting,
        "refresh_srd_hound_registry": registry,
        "check_srd_hound_separation": separation,
        "refresh_srd_hound_separation": refresh,
    }


HOUND_CAST_CHOICES = [
    _optional("hound_password", default=None),
    _optional(
        "hound_lighting",
        "enum",
        None,
        enum_values=["bright", "dim", "darkness", "magical_darkness"],
    ),
]


def _hound_hooks(tools):
    """Compose Hound anchors through actual casting, Magic action, movement and clock owners."""
    changed = _hound_anchor_tools()
    movement = _public(
        _tool_scene(
            "Take a later Magic action to move the intangible Hound up to30feet.",
            [
                _entity("caster_state"),
                _parameter("force_id"),
                _array("destination"),
                _parameter(
                    "destination_lighting",
                    "enum",
                    enum_values=["bright", "dim", "darkness", "magical_darkness"],
                ),
            ],
            [
                _call(
                    "collect_srd_spell_force",
                    {"caster_state": "{_caster_state_id}", "force_id": "{force_id}"},
                    FORCE,
                ),
                _require("_force_record.spell == 'faithful_hound'", "Select this caster's Hound."),
                _require(
                    "(combat.encounter_active == true and caster_state.turns_started != null "
                    "and caster_state.turns_started > _force_record.created_turn) or "
                    "(combat.encounter_active != true and "
                    "state.lasting_tick > _force_record.started_tick)",
                    "Move the Hound on a later turn after its casting.",
                ),
                *_scene_position("{force_id}", "_hound_prior_position"),
                _call(
                    "measure_srd_information_distance",
                    {"start": "{_hound_prior_position}", "end": "{destination}"},
                    MEASURE,
                ),
                _require(
                    "_scene_distance <= _force_profile.move_feet", "Move the Hound up to30feet."
                ),
                _set("_hound_map_bounds", "state.spatial_scene.map.bounds"),
                _require(
                    "destination[0] >= _hound_map_bounds.min[0] and "
                    "destination[0] <= _hound_map_bounds.max[0] and "
                    "destination[1] >= _hound_map_bounds.min[1] and "
                    "destination[1] <= _hound_map_bounds.max[1] and "
                    "destination[2] >= _hound_map_bounds.min[2] and "
                    "destination[2] <= _hound_map_bounds.max[2]",
                    "The Hound destination must remain in the admitted map.",
                ),
                *_action(),
                _call(
                    "update_srd_spell_force_scene",
                    {
                        "force_id": "{force_id}",
                        "position": "{destination}",
                        "active": True,
                        "lighting": "{destination_lighting}",
                    },
                ),
                _call(
                    "check_srd_hound_separation",
                    {"force_id": "{force_id}", "caster_state": "{_caster_state_id}"},
                ),
                _set("_hound_active", "state.spell_force_records[force_id].active"),
                _set("_hound_position", "destination"),
            ],
        ),
        "spells",
        "Faithful Hound movement",
        [("Active", "_hound_active"), ("Origin", "_hound_position")],
    )
    observe = _public(
        _tool_scene(
            "Observe the saved intangible Hound only with its own caster's actual sight.",
            [_entity("observer_state"), _parameter("force_id")],
            [
                _set("_hound_observed_record", "state.spell_force_records[force_id]"),
                _require(
                    "_hound_observed_record.spell == 'faithful_hound' and "
                    "_hound_observed_record.active == true and "
                    "state.lasting_tick < _hound_observed_record.expires_tick",
                    "Select an active Faithful Hound source.",
                ),
                _set("_hound_visible", False),
                _when(
                    "_observer_state_id == _hound_observed_record.caster",
                    [
                        *_scene_position("{force_id}", "_hound_observed_position"),
                        _set("_hound_observed_lighting", "_info_scene_entity.metadata.lighting"),
                        _set("_hound_observed_plane", "_info_scene_entity.metadata.plane"),
                        *_scene_position("{_observer_state_id}", "_hound_current_caster_position"),
                        _when(
                            "_info_scene_entity.metadata.plane == _hound_observed_plane",
                            [
                                _call(
                                    "qualify_srd_hound_anchor_sight",
                                    {
                                        "caster_state": "{_observer_state_id}",
                                        "position": "{_hound_observed_position}",
                                        "lighting": "{_hound_observed_lighting}",
                                    },
                                    ("_hound_anchor_visible",),
                                ),
                                _set("_hound_visible", "_hound_anchor_visible"),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        "exploration",
        "Faithful Hound observation",
        [("Visible", "_hound_visible")],
    )
    changed.update(move_srd_faithful_hound=movement, observe_srd_faithful_hound=observe)
    for name in ("cast_spell", "cast_spell_outside_combat"):
        if name not in tools:
            continue
        cast = deepcopy(tools[name])
        owned = {p["name"] for p in HOUND_CAST_CHOICES}
        cast["parameters"] = [p for p in cast["parameters"] if p["name"] not in owned]
        cast["parameters"] += deepcopy(HOUND_CAST_CHOICES)
        condition = (
            "_spell_cast_failed != true and _spell_definition.family == 'created_spell_hound'"
        )
        _replace_created_cast_dispatch(
            cast,
            "created_spell_hound",
            _when(
                condition,
                [
                    _call(
                        "cast_spell_created_hound",
                        {
                            "caster_state": "{_caster_state_id}",
                            "spell": "{spell}",
                            "casting_save_dc": "{_casting_profile.save_dc}",
                            "force_id": "{force_id}",
                            "force_position": "{force_position}",
                            **{p["name"]: "{" + p["name"] + "}" for p in HOUND_CAST_CHOICES},
                        },
                        ("_spell_summary_rows", "_spell_detail_rows"),
                    )
                ],
            ),
        )
        changed[name] = cast
    create = deepcopy(tools["create_srd_spell_force_scene"])
    marker = "spell == 'faithful_hound'"
    create["effects"] = [e for e in create["effects"] if e.get("condition") != marker]
    insertion = next(
        i
        for i, e in enumerate(create["effects"])
        if e.get("list_push", {}).get("value") == "_force_scene_entity"
    )
    create["effects"].insert(
        insertion,
        _when(
            marker,
            [
                _set(
                    "_hound_scene_metadata_patch",
                    {
                        "intangible_anchor": "'faithful_hound'",
                        "source_only_visible_to": "_caster_state_id",
                        "invisible": True,
                        "truesight_feet": 30,
                    },
                ),
                _set(
                    "_hound_scene_metadata",
                    "merge(_force_scene_entity.metadata, _hound_scene_metadata_patch)",
                ),
                _set("_hound_scene_patch", {"metadata": "_hound_scene_metadata"}),
                _set("_force_scene_entity", "merge(_force_scene_entity, _hound_scene_patch)"),
            ],
        ),
    )
    changed["create_srd_spell_force_scene"] = create
    remove = deepcopy(tools["remove_srd_spell_force"])
    nested = next(e["on_true"] for e in remove["effects"] if "_force_ending != null" in str(e))
    if "refresh_srd_hound_registry" not in str(nested):
        nested.append(
            _when(
                "_force_ending.spell == 'faithful_hound'",
                [_call("refresh_srd_hound_registry", {})],
            )
        )
    changed["remove_srd_spell_force"] = remove
    refresh = deepcopy(tools["refresh_srd_spell_forces"])
    if "refresh_srd_hound_separation" not in str(refresh["effects"]):
        refresh["effects"].append(
            _when("state.has_hound_sources == true", [_call("refresh_srd_hound_separation", {})])
        )
    changed["refresh_srd_spell_forces"] = refresh
    movement_name = (
        "move_srd_scene_creature_environment_segment"
        if "move_srd_scene_creature_environment_segment" in tools
        else "move_srd_scene_creature_node"
    )
    if movement_name in tools:
        move = deepcopy(tools[movement_name])
        if "refresh_srd_hound_separation" not in str(move["effects"]):
            index = next(i for i, e in enumerate(move["effects"]) if "spatial_mutation" in e)
            move["effects"].insert(
                index + 1,
                _when(
                    "lasting_state.has_hound_sources == true",
                    [_call("refresh_srd_hound_separation", {})],
                ),
            )
        changed[movement_name] = move
    return changed


HOUND_BITE_CHOICES = _parameter(
    "hound_bite_choices",
    "array",
    required=False,
    default=[],
    max_items=64,
    items={
        "type": "object",
        "required": ["force_id", "target_id"],
        "additionalProperties": False,
        "properties": {
            "force_id": {"type": "string", "minLength": 1},
            "target_id": {"type": "string", "minLength": 1},
        },
    },
    description="Optional Hound target intent; actual enemies, range and Cover are derived.",
)


def _hound_bite_tools():
    """Resolve one genuine enemy bite per active source on its caster's actual turn start."""
    actor = _tool(
        "Read the actual typed living creature before offering it as the Hound's enemy.",
        [_entity("target_state")],
        [
            _set(
                "_hound_enemy_live",
                "target_state.is_alive == true and target_state.creature_type != 'object' and "
                "target_state.creature_type != 'spell_force'",
            ),
            _set("_hound_enemy_name", "target_state.name"),
        ],
    )
    actor["pre_conditions"] = [
        {
            "condition": "target_state.creature_type != null",
            "error": "Hound enemy qualification needs its actual typed mechanics creature.",
        }
    ]
    candidates = _tool_scene(
        "Derive actual enemies within five feet of the Hound point with physical Cover.",
        [_entity("caster_state"), _parameter("force_id")],
        [
            _set("_hound_source_record", "state.spell_force_records[force_id]"),
            _require(
                "_hound_source_record.active == true and _hound_source_record.spell == "
                "'faithful_hound' and _hound_source_record.caster == _caster_state_id and "
                "state.lasting_tick < _hound_source_record.expires_tick",
                "Select this caster's active Hound source.",
            ),
            *_scene_position("{force_id}", "_hound_bite_origin"),
            _set("_hound_bite_plane", "_info_scene_entity.metadata.plane"),
            _require("_hound_bite_plane != null", "The Hound anchor needs its actual plane."),
            _set("_hound_bite_options", []),
            _set("_hound_bite_ids", []),
            _set("_hound_bite_half_cover", []),
            _set("_hound_bite_three_quarters", []),
            _set("_hound_bite_geometry", []),
            _set("_hound_bite_bodies", []),
            *_snapshot(),
            _set("_hound_scene_geometry", "state.spatial_scene.geometry"),
            _loop(
                "_hound_scene_geometry",
                "_hound_barrier",
                [
                    _when(
                        "_hound_barrier.active == true and _hound_barrier.movement == 'blocking' "
                        "and (_hound_barrier.metadata.plane == null or "
                        "_hound_barrier.metadata.plane == _hound_bite_plane)",
                        [_push("_hound_bite_geometry", "_hound_barrier.id")],
                    )
                ],
            ),
            _set("_hound_scene_bodies", "state.spatial_scene.entities"),
            _loop(
                "_hound_scene_bodies",
                "_hound_body",
                [
                    _when(
                        "_hound_body.active == true and _hound_body.movement == 'blocking' "
                        "and _hound_body.metadata.plane == _hound_bite_plane",
                        [_push("_hound_bite_bodies", "_hound_body.id")],
                    )
                ],
            ),
            _set(
                "_hound_bite_nearest_faces",
                [
                    "_hound_bite_origin[0]",
                    "_hound_bite_origin[0]",
                    "_hound_bite_origin[1]",
                    "_hound_bite_origin[1]",
                ],
            ),
            _loop(
                "_hound_scene_bodies",
                "_hound_enemy_body",
                [
                    _when(
                        "_hound_enemy_body.active == true and "
                        "_hound_enemy_body.metadata.plane == _hound_bite_plane and "
                        "_hound_enemy_body.metadata.participant_ref != null and "
                        "_hound_enemy_body.metadata.is_object != true and "
                        "_hound_enemy_body.metadata.spell_force != true and "
                        "_hound_enemy_body.metadata.enemy_of != null and "
                        "_caster_state_id in _hound_enemy_body.metadata.enemy_of",
                        [
                            _require(
                                "_hound_enemy_body.footprint.kind == 'box'",
                                "Hound bite qualification currently needs actual enemy boxes.",
                            ),
                            _set(
                                "_hound_enemy_dx",
                                "max(abs(_hound_enemy_body.position[0] - _hound_bite_origin[0]) - "
                                "_hound_enemy_body.footprint.half_extents[0], 0)",
                            ),
                            _set(
                                "_hound_enemy_dy",
                                "max(abs(_hound_enemy_body.position[1] - _hound_bite_origin[1]) - "
                                "_hound_enemy_body.footprint.half_extents[1], 0)",
                            ),
                            _set(
                                "_hound_enemy_dz",
                                "max(_hound_enemy_body.position[2] - _hound_bite_origin[2], "
                                "_hound_bite_origin[2] - _hound_enemy_body.position[2] - "
                                "_hound_enemy_body.height, 0)",
                            ),
                            _when(
                                "_hound_enemy_dx ** 2 + _hound_enemy_dy ** 2 + "
                                "_hound_enemy_dz ** 2 <= 25",
                                [
                                    _call(
                                        "qualify_srd_hound_bite_actor",
                                        {
                                            "target_state": "{_hound_enemy_body.metadata."
                                            "participant_ref}"
                                        },
                                        ("_hound_enemy_live", "_hound_enemy_name"),
                                    ),
                                    _when(
                                        "_hound_enemy_live == true",
                                        [
                                            _call(
                                                "qualify_srd_spell_area_target_cover",
                                                {
                                                    "source_id": "{force_id}",
                                                    "target_state": "{_hound_enemy_body.metadata."
                                                    "participant_ref}",
                                                    "origin": "{_hound_bite_origin}",
                                                    "geometry_blockers": "{_hound_bite_geometry}",
                                                    "entity_blockers": "{_hound_bite_bodies}",
                                                    "target_scene": "{_hound_enemy_body}",
                                                    "scene_snapshot": "{_info_snapshot}",
                                                    "nearest_faces": "{_hound_bite_nearest_faces}",
                                                },
                                                ("_breath_target_affected", "_breath_target_cover"),
                                            ),
                                            _when(
                                                "_breath_target_affected == true",
                                                [
                                                    _require(
                                                        "_hound_enemy_body.metadata."
                                                        "participant_ref not in _hound_bite_ids",
                                                        "Hound enemy has ambiguous scene proxies.",
                                                    ),
                                                    _push(
                                                        "_hound_bite_ids",
                                                        "_hound_enemy_body.metadata.participant_ref",
                                                    ),
                                                    _push(
                                                        "_hound_bite_options",
                                                        {
                                                            "id": "_hound_enemy_body.metadata."
                                                            "participant_ref",
                                                            "label": "_hound_enemy_name",
                                                        },
                                                    ),
                                                    _when(
                                                        "_breath_target_cover == 'half'",
                                                        [
                                                            _push(
                                                                "_hound_bite_half_cover",
                                                                "_hound_enemy_body.metadata."
                                                                "participant_ref",
                                                            )
                                                        ],
                                                    ),
                                                    _when(
                                                        "_breath_target_cover == 'three_quarters'",
                                                        [
                                                            _push(
                                                                "_hound_bite_three_quarters",
                                                                "_hound_enemy_body.metadata."
                                                                "participant_ref",
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
                ],
            ),
            _require("len(_hound_bite_ids) <= 64", "Hound bite supports at most64 actual enemies."),
        ],
    )
    event = _tool(
        "Resolve each active Hound's one enemy bite at its caster's actual turn start.",
        [
            _entity("caster_state"),
            deepcopy(HOUND_BITE_CHOICES),
            _parameter(
                "witness_state_ids", "array", required=False, default=None,
                items={"type": "string"},
            ),
        ],
        [
            _set("_hound_bite_rows", []),
            _set("_hound_bite_damage", 0),
            _set("_hound_turn_plans", []),
            _set("_hound_native_choices", 0),
            _loop(
                "hound_bite_choices", "_hound_intent",
                [
                    _require(
                        "state.spell_force_records != null and _hound_intent.force_id in "
                        "state.spell_force_records",
                        "Select an existing Hound source for its enemy intent.",
                    ),
                    _set(
                        "_hound_intent_source", "state.spell_force_records[_hound_intent.force_id]"
                    ),
                    _require(
                        "_hound_intent_source.active == true and _hound_intent_source.spell == "
                        "'faithful_hound' and _hound_intent_source.caster == _caster_state_id and "
                        "state.lasting_tick < _hound_intent_source.expires_tick",
                        "Enemy intent must belong to this turn actor's active Hound.",
                    ),
                ],
            ),
            _set("_hound_start_ids", "state.spell_force_ids"),
            _loop(
                "_hound_start_ids", "_hound_start_id",
                [
                    _set("_hound_start_record", "state.spell_force_records[_hound_start_id]"),
                    _when(
                        "_hound_start_record.active == true and _hound_start_record.spell == "
                        "'faithful_hound' and _hound_start_record.caster == _caster_state_id and "
                        "state.lasting_tick < _hound_start_record.expires_tick and "
                        "(_hound_start_record.last_bite_turn == null or "
                        "_hound_start_record.last_bite_turn < caster_state.turns_started)",
                        [
                            _call(
                                "collect_srd_hound_bite_enemies",
                                {
                                    "caster_state": "{_caster_state_id}",
                                    "force_id": "{_hound_start_id}",
                                },
                                ("_hound_bite_options", "_hound_bite_ids", "_hound_bite_half_cover",
                                 "_hound_bite_three_quarters"),
                            ),
                            _set("_hound_proposed_target", "''"),
                            _loop(
                                "hound_bite_choices", "_hound_chosen_enemy",
                                [
                                    _when(
                                        "_hound_chosen_enemy.force_id == _hound_start_id",
                                        [
                                            _require(
                                                "_hound_proposed_target == '' and "
                                                "_hound_chosen_enemy.target_id in _hound_bite_ids",
                                                "Choose one eligible enemy per Hound source.",
                                            ),
                                            _set(
                                                "_hound_proposed_target",
                                                "_hound_chosen_enemy.target_id",
                                            ),
                                        ],
                                    )
                                ],
                            ),
                            _when(
                                "_hound_proposed_target == '' and len(_hound_bite_options) == 1",
                                [_set("_hound_proposed_target", "_hound_bite_options[0].id")],
                            ),
                            _when(
                                "_hound_proposed_target == '' and len(_hound_bite_options) > 1",
                                [_set("_hound_native_choices", "_hound_native_choices + 1")],
                            ),
                            _push(
                                "_hound_turn_plans",
                                {
                                    "force_id": "_hound_start_id",
                                    "target_id": "_hound_proposed_target",
                                    "options": "_hound_bite_options",
                                    "record": "_hound_start_record",
                                    "half_cover": "_hound_bite_half_cover",
                                    "three_quarters": "_hound_bite_three_quarters",
                                },
                            ),
                        ],
                    ),
                ],
            ),
            _require(
                "_hound_native_choices <= 1",
                "Supply enemy intents when several Hounds each need a target choice; one native "
                "choice per turn is supported.",
            ),
            _loop(
                "_hound_turn_plans", "_hound_turn_plan",
                [
                    _set(
                        "state.spell_force_records.{_hound_turn_plan.force_id}.last_bite_turn",
                        "caster_state.turns_started",
                    ),
                    _set("_hound_bite_target", "_hound_turn_plan.target_id"),
                    _when(
                        "_hound_bite_target == '' and len(_hound_turn_plan.options) > 1",
                        [
                            _require(
                                "len(_hound_turn_plan.options) <= 16",
                                "Supply the Hound enemy intent when more than16 enemies qualify.",
                            ),
                            _set("_hound_choice_options", "_hound_turn_plan.options"),
                            {
                                "request_choice": {
                                    "choice_id": "srd-hound-bite",
                                    "prompt": "Choose Faithful Hound {_hound_turn_plan.force_id}'s "
                                    "enemy for this turn-start bite.",
                                    "options": "_hound_choice_options",
                                },
                                "store_as": "_hound_bite_target",
                            },
                        ],
                    ),
                    _when(
                        "_hound_bite_target != ''",
                        [
                            _call(
                                "qualify_srd_hound_bite_actor",
                                {"target_state": "{_hound_bite_target}"},
                                ("_hound_enemy_live",),
                            ),
                            _when(
                                "_hound_enemy_live == false",
                                [_set("_hound_bite_target", "''")],
                            ),
                        ],
                    ),
                    _when(
                        "_hound_bite_target != ''",
                        [
                            {"dice": {"expression": "4d8"}, "store_as": "_hound_bite_roll"},
                            _call(
                                "resolve_spell_area_save_damage_target",
                                {
                                    "spell": "faithful_hound", "target": "{_hound_bite_target}",
                                    "save_ability": "dexterity",
                                    "spell_save_dc": "{_hound_turn_plan.record.save_dc}",
                                    "damage_total": "{_hound_bite_roll.total}",
                                    "damage_type": "force",
                                    "success_damage": "none", "construct_save_disadvantage": False,
                                    "half_cover_targets": "{_hound_turn_plan.half_cover}",
                                    "three_quarters_cover_targets": (
                                        "{_hound_turn_plan.three_quarters}"
                                    ),
                                    "source_state_id": "{_caster_state_id}",
                                    "witness_state_ids": "{witness_state_ids}",
                                },
                                ("_spell_detail_rows", "_affliction_trigger_damage"),
                            ),
                            _loop(
                                "_spell_detail_rows", "_hound_bite_row",
                                [_push("_hound_bite_rows", "_hound_bite_row")],
                            ),
                            _set(
                                "_hound_bite_damage",
                                "_hound_bite_damage + _affliction_trigger_damage",
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    event["pre_conditions"] = [
        {
            "condition": "combat.turn_active == true and combat.current_actor == _caster_state_id "
            "and combat.active_actor == _caster_state_id and caster_state.turns_started > 0",
            "error": "Hound bites belong to the caster's actual turn-start lifecycle.",
        }
    ]
    return {
        "qualify_srd_hound_bite_actor": actor,
        "collect_srd_hound_bite_enemies": candidates,
        "start_srd_hound_turn": event,
    }


def _hound_bite_turn_hooks(tools):
    """Bind source enemy intent and consequences after the final actual begin-turn lifecycle."""
    begin = deepcopy(tools["begin_combat_turn"])
    begin["parameters"] = [p for p in begin["parameters"] if p["name"] != "hound_bite_choices"]
    begin["parameters"].append(deepcopy(HOUND_BITE_CHOICES))
    begin["effects"] = [e for e in begin["effects"] if "start_srd_hound_turn" not in str(e)]
    begin["effects"].append(
        _when(
            "lasting_state.has_hound_sources == true or len(hound_bite_choices) > 0",
            [
                _call(
                    "start_srd_hound_turn",
                    {
                        "caster_state": "{_actor_id}", "hound_bite_choices": "{hound_bite_choices}",
                        "witness_state_ids": "{witness_state_ids}",
                    },
                    ("_hound_bite_rows", "_hound_bite_damage"),
                ),
                _loop(
                    "_hound_bite_rows", "_hound_turn_row",
                    [_push("_turn_effect_rows", "_hound_turn_row")],
                ),
                _set("_turn_effect_count", "len(_turn_effect_rows)"),
                _set(
                    "_turn_effect_damage_applied",
                    "_turn_effect_damage_applied + _hound_bite_damage",
                ),
            ],
        )
    )
    return {"begin_combat_turn": begin}


def bind_hound_turn_consumers(text):
    """Rebind only the Hound callback after other emitters finish the shared turn owner."""
    changed = _hound_bite_turn_hooks(_load_runtime(text)["tools"])
    _normalize(changed)
    return _replace_tools(text, changed)


def _area_cover_tools():
    """Share physical ray qualification and categorical Cover among package area consumers."""

    def axis_target_near(target):
        return (
            f"({target}.position[0] - origin[0]) * axis[0] + "
            f"({target}.position[1] - origin[1]) * axis[1] - "
            f"{target}.footprint.half_extents[0] * abs(axis[0]) - "
            f"{target}.footprint.half_extents[1] * abs(axis[1])"
        )

    def projected_box(position, half):
        center = f"({position}[0] - origin[0]) * axis[0] + ({position}[1] - origin[1]) * axis[1]"
        radius = f"{half}[0] * abs(axis[0]) + {half}[1] * abs(axis[1])"
        return _set(
            "_area_axis_bounds", {"min": f"{center} - ({radius})", "max": f"{center} + ({radius})"}
        )

    def projected_circle(position, radius):
        center = f"({position}[0] - origin[0]) * axis[0] + ({position}[1] - origin[1]) * axis[1]"
        return _set(
            "_area_axis_bounds", {"min": f"{center} - {radius}", "max": f"{center} + {radius}"}
        )

    axis_bounds = _tool_scene(
        "Project an actual admitted body or volume onto this Cone owner's horizontal axis.",
        [
            _parameter("candidate", "object"),
            _array("origin"),
            _parameter("axis", "array", items={"type": "number"}, min_items=2, max_items=2),
        ],
        [
            _choose(
                "candidate.footprint != null",
                [
                    _require(
                        "candidate.footprint.kind == 'box' or candidate.footprint.kind == 'circle'",
                        "Cone physical qualification requires admitted box or circle blockers.",
                    ),
                    _choose(
                        "candidate.footprint.kind == 'box'",
                        [projected_box("candidate.position", "candidate.footprint.half_extents")],
                        [projected_circle("candidate.position", "candidate.footprint.radius")],
                    ),
                ],
                [
                    _require(
                        "candidate.volume.kind == 'box' or candidate.volume.kind == 'cylinder'",
                        "Cone physical qualification requires admitted box or cylinder geometry.",
                    ),
                    _choose(
                        "candidate.volume.kind == 'box'",
                        [
                            _set(
                                "_area_axis_box_center",
                                [
                                    "(candidate.volume.min[0] + candidate.volume.max[0]) * 0.5",
                                    "(candidate.volume.min[1] + candidate.volume.max[1]) * 0.5",
                                ],
                            ),
                            _set(
                                "_area_axis_box_half",
                                [
                                    "(candidate.volume.max[0] - candidate.volume.min[0]) * 0.5",
                                    "(candidate.volume.max[1] - candidate.volume.min[1]) * 0.5",
                                ],
                            ),
                            projected_box("_area_axis_box_center", "_area_axis_box_half"),
                        ],
                        [projected_circle("candidate.volume.center", "candidate.volume.radius")],
                    ),
                ],
            ),
        ],
    )

    def fast_clear(faces):
        x, y = "_area_p[0]", "_area_p[1]"
        half_x, half_y = "_area_h[0]", "_area_h[1]"
        along_x = f"abs({x} - origin[0]) >= abs({y} - origin[1])"
        expression = (
            f"(({along_x}) and (({x} >= origin[0] and {x} - {half_x} <= {faces}[0]) "
            f"or ({x} < origin[0] and {x} + {half_x} >= {faces}[1]))) or "
            f"((not ({along_x})) and (({y} >= origin[1] and {y} - {half_y} <= {faces}[2]) "
            f"or ({y} < origin[1] and {y} + {half_y} >= {faces}[3])))"
        )
        assert len(expression) <= 500
        return expression

    bounds = _tool_scene(
        "Derive conservative physical bounds from an actual admitted scene body or volume.",
        [_parameter("candidate", "object")],
        [
            _choose(
                "candidate.footprint != null",
                [
                    _require(
                        "candidate.footprint.kind == 'box' or candidate.footprint.kind == 'circle'",
                        "Area physical qualification requires admitted box or circle blockers.",
                    ),
                    _choose(
                        "candidate.footprint.kind == 'box'",
                        [
                            _set(
                                "_area_blocker_bounds",
                                [
                                    "candidate.position[0] - candidate.footprint.half_extents[0]",
                                    "candidate.position[0] + candidate.footprint.half_extents[0]",
                                    "candidate.position[1] - candidate.footprint.half_extents[1]",
                                    "candidate.position[1] + candidate.footprint.half_extents[1]",
                                ],
                            )
                        ],
                        [
                            _set(
                                "_area_blocker_bounds",
                                [
                                    "candidate.position[0] - candidate.footprint.radius",
                                    "candidate.position[0] + candidate.footprint.radius",
                                    "candidate.position[1] - candidate.footprint.radius",
                                    "candidate.position[1] + candidate.footprint.radius",
                                ],
                            )
                        ],
                    ),
                ],
                [
                    _require(
                        "candidate.volume.kind == 'box' or candidate.volume.kind == 'cylinder'",
                        "Area physical qualification requires admitted box or cylinder geometry.",
                    ),
                    _choose(
                        "candidate.volume.kind == 'box'",
                        [
                            _set(
                                "_area_blocker_bounds",
                                [
                                    "candidate.volume.min[0]",
                                    "candidate.volume.max[0]",
                                    "candidate.volume.min[1]",
                                    "candidate.volume.max[1]",
                                ],
                            )
                        ],
                        [
                            _set(
                                "_area_blocker_bounds",
                                [
                                    "candidate.volume.center[0] - candidate.volume.radius",
                                    "candidate.volume.center[0] + candidate.volume.radius",
                                    "candidate.volume.center[1] - candidate.volume.radius",
                                    "candidate.volume.center[1] + candidate.volume.radius",
                                ],
                            )
                        ],
                    ),
                ],
            ),
        ],
    )
    cover = _tool_scene(
        "Qualify physical clear path and admitted categorical Cover from an actual area origin.",
        [
            _parameter("source_id"),
            _entity("target_state"),
            _array("origin"),
            _parameter("geometry_blockers", "array", items={"type": "string"}),
            _parameter("entity_blockers", "array", items={"type": "string"}),
            _parameter("target_scene", "object"),
            _parameter("scene_snapshot", "object"),
            _array("nearest_faces", size=4),
            _parameter(
                "axis",
                "array",
                required=False,
                default=[],
                items={"type": "number"},
                min_items=0,
                max_items=2,
            ),
            _optional("axis_nearest", "number", 0),
        ],
        _snapshot()
        + [
            _require(
                "target_scene.footprint.kind == 'box'",
                "Area physical qualification requires actual target boxes.",
            ),
            _set("_area_p", "target_scene.position"),
            _set("_area_h", "target_scene.footprint.half_extents"),
            _choose(
                "len(axis) == 2",
                [_set("_breath_fast_clear", axis_target_near("target_scene") + " <= axis_nearest")],
                [_set("_breath_fast_clear", fast_clear("nearest_faces"))],
            ),
            _set("_breath_target_cover", "'none'"),
            _set("_breath_path_obstructed", False),
            _when(
                "_breath_fast_clear != true",
                [
                    _spatial(
                        "exposure",
                        {
                            "map_id": "{state.spatial_scene.map.id}",
                            "observer": {"point": "{origin}"},
                            "target": {"entity_id": "{target_scene.id}"},
                            "samples": {"horizontal": 3, "vertical": 3},
                            "include": ["geometry"],
                        },
                        "_breath_cover_samples",
                    ),
                    _set("_breath_sample_points", "_breath_cover_samples.samples"),
                    _loop(
                        "_breath_sample_points",
                        "_breath_sample",
                        [
                            _when(
                                "_breath_path_obstructed != true",
                                [
                                    _spatial(
                                        "ray",
                                        {
                                            "map_id": "{state.spatial_scene.map.id}",
                                            "origin": "{origin}",
                                            "destination": "{_breath_sample.point}",
                                            "include": ["geometry", "entities"],
                                            "max_intersections": 256,
                                        },
                                        "_breath_path_ray",
                                    ),
                                    _require(
                                        "_breath_path_ray.truncated != true",
                                        "Area clear-path qualification exceeded "
                                        "the admitted ray bound.",
                                    ),
                                    _set("_breath_path_hits", "_breath_path_ray.intersections"),
                                    _loop(
                                        "_breath_path_hits",
                                        "_breath_path_hit",
                                        [
                                            _when(
                                                "_breath_path_hit.t < 1 and "
                                                "_breath_path_hit.id != source_id and "
                                                "_breath_path_hit.id != target_scene.id",
                                                [
                                                    _when(
                                                        "_breath_path_hit.kind == 'geometry' and "
                                                        "_breath_path_hit.id in geometry_blockers",
                                                        [_set("_breath_path_obstructed", True)],
                                                    ),
                                                    _when(
                                                        "_breath_path_hit.kind == 'entity' and "
                                                        "_breath_path_hit.id in entity_blockers",
                                                        [_set("_breath_path_obstructed", True)],
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
            ),
            _when(
                "target_scene.metadata.cover_from != null and "
                "source_id in target_scene.metadata.cover_from",
                [
                    _set(
                        "_breath_target_cover",
                        "target_scene.metadata.cover_from[source_id]",
                    ),
                ],
            ),
            _require(
                "_breath_target_cover == 'none' or _breath_target_cover == 'half' or "
                "_breath_target_cover == 'three_quarters' or _breath_target_cover == 'total'",
                "Admit categorical Cover from the actual area source.",
            ),
            _require(
                "_breath_path_obstructed != true or _breath_target_cover != 'none'",
                "Physical obstruction needs admitted categorical Cover "
                "from the actual area source.",
            ),
            _set(
                "_breath_target_affected",
                "_breath_target_cover != 'total' and target_state.is_alive != false",
            ),
        ],
    )
    # The parent owns one immutable scene snapshot and selected scene rows for the whole area.
    cover["effects"] = cover["effects"][3:]
    for parameter in cover["parameters"]:
        if parameter["name"] in {"target_scene", "scene_snapshot"}:
            parameter["from_state"] = "state.spatial_scene"
    for effect in _walk(cover["effects"]):
        if "spatial_query" in effect:
            effect["spatial_query"]["snapshot"] = "{scene_snapshot}"
    nearest = [
        _call(
            "derive_srd_spell_area_blocker_bounds",
            {"candidate": "{_breath_blocker}"},
            ("_area_blocker_bounds",),
        ),
        _when(
            "_area_blocker_bounds[1] > origin[0] and "
            "_area_blocker_bounds[0] < _breath_nearest_positive_x",
            [
                _set(
                    "_breath_nearest_positive_x",
                    "_area_blocker_bounds[0]",
                )
            ],
        ),
        _when(
            "_area_blocker_bounds[0] < origin[0] and "
            "_area_blocker_bounds[1] > _breath_nearest_negative_x",
            [
                _set(
                    "_breath_nearest_negative_x",
                    "_area_blocker_bounds[1]",
                )
            ],
        ),
        _when(
            "_area_blocker_bounds[3] > origin[1] and "
            "_area_blocker_bounds[2] < _breath_nearest_positive_y",
            [
                _set(
                    "_breath_nearest_positive_y",
                    "_area_blocker_bounds[2]",
                )
            ],
        ),
        _when(
            "_area_blocker_bounds[2] < origin[1] and "
            "_area_blocker_bounds[3] > _breath_nearest_negative_y",
            [
                _set(
                    "_breath_nearest_negative_y",
                    "_area_blocker_bounds[3]",
                )
            ],
        ),
    ]
    forward_nearest = [
        _choose(
            "_breath_blocker.footprint != null and _breath_blocker.footprint.kind == 'box'",
            [projected_box("_breath_blocker.position", "_breath_blocker.footprint.half_extents")],
            [
                _call(
                    "derive_srd_spell_area_axis_bounds",
                    {"candidate": "{_breath_blocker}", "origin": "{origin}", "axis": "{axis}"},
                    ("_area_axis_bounds",),
                )
            ],
        ),
        _when(
            "_area_axis_bounds.max > 0 and _area_axis_bounds.min < _breath_axis_nearest",
            [_set("_breath_axis_nearest", "_area_axis_bounds.min")],
        ),
    ]
    nearest = [_choose("len(axis) == 2", forward_nearest, nearest)]
    collect = _tool_scene(
        "Filter an owning area's actual geometric candidates through physical path and Cover.",
        [
            _entity("source_state"),
            _parameter(
                "targets", "entity_id_list", resolve="entity_list", max_items=64, unique_items=True
            ),
            _array("origin"),
            _parameter(
                "axis",
                "array",
                required=False,
                default=[],
                items={"type": "number"},
                min_items=0,
                max_items=2,
                model_hidden=True,
            ),
            _parameter(
                "source_frames",
                "array",
                required=False,
                default=[],
                items={"type": "object"},
                min_items=0,
                max_items=1,
                model_hidden=True,
            ),
        ],
        _snapshot()
        + [
            _set("_breath_axis_nearest", 0),
            _choose(
                "len(axis) == 2",
                [
                    _require(
                        "len(source_frames) == 1 and source_frames[0].active == true and "
                        "source_frames[0].metadata.participant_ref == _source_state_id and "
                        "source_frames[0].metadata.plane != null and "
                        "abs(axis[0] ** 2 + axis[1] ** 2 - 1) <= 0.000001",
                        "Cone physical qualification needs this action's actual source frame.",
                    ),
                    _set("_breath_source_scene_id", "source_frames[0].id"),
                    _set("_breath_source_plane", "source_frames[0].metadata.plane"),
                    _set(
                        "_breath_axis_nearest",
                        "max(abs(state.spatial_scene.map.bounds.min[0] - origin[0]), "
                        "abs(state.spatial_scene.map.bounds.max[0] - origin[0])) * abs(axis[0]) + "
                        "max(abs(state.spatial_scene.map.bounds.min[1] - origin[1]), "
                        "abs(state.spatial_scene.map.bounds.max[1] - origin[1])) * abs(axis[1])",
                    ),
                ],
                [
                    *_scene_position("{_source_state_id}", "_breath_source_position"),
                    _set("_breath_source_scene_id", "_info_scene_entity.id"),
                    _set("_breath_source_plane", "_info_scene_entity.metadata.plane"),
                ],
            ),
            _require("_breath_source_plane != null", "Area source needs its admitted plane."),
            _set("_breath_nearest_positive_x", "state.spatial_scene.map.bounds.max[0]"),
            _set("_breath_nearest_negative_x", "state.spatial_scene.map.bounds.min[0]"),
            _set("_breath_nearest_positive_y", "state.spatial_scene.map.bounds.max[1]"),
            _set("_breath_nearest_negative_y", "state.spatial_scene.map.bounds.min[1]"),
            _set("_breath_geometry_blockers", []),
            _set("_breath_entity_blockers", []),
            _set("_breath_geometry", "state.spatial_scene.geometry"),
            _loop(
                "_breath_geometry",
                "_breath_barrier",
                [
                    _when(
                        "_breath_barrier.active == true and _breath_barrier.movement == 'blocking' "
                        "and (_breath_barrier.metadata.plane == null or "
                        "_breath_barrier.metadata.plane == _breath_source_plane)",
                        [
                            _push("_breath_geometry_blockers", "_breath_barrier.id"),
                            _set("_breath_blocker", "_breath_barrier"),
                            *deepcopy(nearest),
                        ],
                    )
                ],
            ),
            _set("_breath_entities", "state.spatial_scene.entities"),
            _loop(
                "_breath_entities",
                "_breath_body",
                [
                    _when(
                        "_breath_body.active == true and _breath_body.movement == 'blocking' and "
                        "_breath_body.metadata.plane == _breath_source_plane",
                        [
                            _push("_breath_entity_blockers", "_breath_body.id"),
                            _when(
                                "_breath_body.id != _breath_source_scene_id",
                                [_set("_breath_blocker", "_breath_body"), *deepcopy(nearest)],
                            ),
                        ],
                    )
                ],
            ),
            _set("_srd_area_targets", []),
            _set("_srd_area_half_cover", []),
            _set("_srd_area_three_quarters", []),
            _set(
                "_area_f",
                [
                    "_breath_nearest_positive_x",
                    "_breath_nearest_negative_x",
                    "_breath_nearest_positive_y",
                    "_breath_nearest_negative_y",
                ],
            ),
            _loop(
                "_breath_entities",
                "_breath_candidate_scene",
                [
                    _when(
                        "_breath_candidate_scene.active == true and "
                        "_breath_candidate_scene.metadata.participant_ref in targets",
                        [
                            _set(
                                "_breath_candidate",
                                "_breath_candidate_scene.metadata.participant_ref",
                            ),
                            _require(
                                "_breath_candidate_scene.footprint.kind == 'box'",
                                "Area physical qualification requires actual target boxes.",
                            ),
                            _choose(
                                "len(axis) == 2",
                                [
                                    _set(
                                        "_breath_candidate_clear",
                                        axis_target_near("_breath_candidate_scene")
                                        + " <= _breath_axis_nearest",
                                    )
                                ],
                                [
                                    _set("_area_p", "_breath_candidate_scene.position"),
                                    _set(
                                        "_area_h", "_breath_candidate_scene.footprint.half_extents"
                                    ),
                                    _set("_breath_candidate_clear", fast_clear("_area_f")),
                                ],
                            ),
                            _choose(
                                "_breath_candidate_clear == true and "
                                "(_breath_candidate_scene.metadata.cover_from == null or "
                                "_breath_source_scene_id not in "
                                "_breath_candidate_scene.metadata.cover_from)",
                                [_push("_srd_area_targets", "_breath_candidate")],
                                [
                                    _call(
                                        "qualify_srd_spell_area_target_cover",
                                        {
                                            "source_id": "{_breath_source_scene_id}",
                                            "target_state": "{_breath_candidate}",
                                            "origin": "{origin}",
                                            "geometry_blockers": "{_breath_geometry_blockers}",
                                            "entity_blockers": "{_breath_entity_blockers}",
                                            "target_scene": "{_breath_candidate_scene}",
                                            "scene_snapshot": "{_info_snapshot}",
                                            "nearest_faces": "{_area_f}",
                                            "axis": "{axis}",
                                            "axis_nearest": "{_breath_axis_nearest}",
                                        },
                                        ("_breath_target_affected", "_breath_target_cover"),
                                    ),
                                    _when(
                                        "_breath_target_affected == true",
                                        [
                                            _push("_srd_area_targets", "_breath_candidate"),
                                            _when(
                                                "_breath_target_cover == 'half'",
                                                [
                                                    _push(
                                                        "_srd_area_half_cover", "_breath_candidate"
                                                    )
                                                ],
                                            ),
                                            _when(
                                                "_breath_target_cover == 'three_quarters'",
                                                [
                                                    _push(
                                                        "_srd_area_three_quarters",
                                                        "_breath_candidate",
                                                    )
                                                ],
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    )
                ],
            ),
        ],
    )

    # Select the axis algorithm once per traversal. The row facts still come from
    # this action's admitted scene, and generic callers retain the four-face path.
    def specialize(effects, axis_enabled):
        specialized = []
        for effect in effects:
            if effect.get("condition") == "len(axis) == 2":
                specialized.extend(
                    specialize(
                        effect.get("on_true" if axis_enabled else "on_false", []), axis_enabled
                    )
                )
                continue
            for branch in ("on_true", "on_false"):
                if branch in effect:
                    effect[branch] = specialize(effect[branch], axis_enabled)
            specialized.append(effect)
        return specialized

    def bind_alias(value, old, actual):
        if isinstance(value, str):
            return re.sub(r"\b" + old + r"\b", actual, value)
        if isinstance(value, list):
            return [bind_alias(child, old, actual) for child in value]
        if isinstance(value, dict):
            return {key: bind_alias(child, old, actual) for key, child in value.items()}
        return value

    for index, effect in enumerate(collect["effects"]):
        loop = effect.get("for_each", {})
        alias = loop.get("as")
        if alias not in {"_breath_barrier", "_breath_body", "_breath_candidate_scene"}:
            continue
        owning = loop["do"][0]
        body = owning["on_true"]
        if alias == "_breath_body":
            owning["condition"] += " and _breath_body.id != _breath_source_scene_id"
            source_guard = next(
                e
                for e in body
                if e.get("condition") == "_breath_body.id != _breath_source_scene_id"
            )
            body[body.index(source_guard) : body.index(source_guard) + 1] = source_guard["on_true"]
        if alias == "_breath_candidate_scene":
            body[:] = [e for e in body if e.get("set", {}).get("field") != "_breath_candidate"]
            loop["do"] = bind_alias(
                loop["do"], "_breath_candidate", "_breath_candidate_scene.metadata.participant_ref"
            )
        else:
            body[:] = [e for e in body if e.get("set", {}).get("field") != "_breath_blocker"]
            loop["do"] = bind_alias(loop["do"], "_breath_blocker", alias)
        axis_loop, generic_loop = deepcopy(effect), deepcopy(effect)
        axis_loop["for_each"]["do"] = specialize(axis_loop["for_each"]["do"], True)
        generic_loop["for_each"]["do"] = specialize(generic_loop["for_each"]["do"], False)
        if alias == "_breath_candidate_scene":
            axis_body = axis_loop["for_each"]["do"][0]["on_true"]
            certificate = next(
                e for e in axis_body if e.get("set", {}).get("field") == "_breath_candidate_clear"
            )
            axis_body.remove(certificate)
            for row in axis_body:
                if "_breath_candidate_clear" in row.get("condition", ""):
                    row["condition"] = row["condition"].replace(
                        "_breath_candidate_clear == true", "(" + certificate["set"]["value"] + ")"
                    )
                    if len(row["condition"]) > 500:
                        raise SystemExit(
                            "The actual forward Cover certificate exceeds the expression bound."
                        )
        collect["effects"][index] = _choose("len(axis) == 2", [axis_loop], [generic_loop])
    return {
        "derive_srd_spell_area_blocker_bounds": bounds,
        "derive_srd_spell_area_axis_bounds": axis_bounds,
        "qualify_srd_spell_area_target_cover": cover,
        "collect_srd_spell_area_cover_targets": collect,
    }


def _dragon_breath_row_tools(tools):
    """Adapt only row assembly from the final area leaf and its actual native card formatter."""
    prior_rows = _parameter(
        "prior_rows",
        "array",
        items={"type": "object"},
        min_items=0,
        max_items=64,
        model_hidden=True,
    )
    record = deepcopy(tools["record_srd_spell_area_target_result"])
    record["parameters"].append(deepcopy(prior_rows))
    # Breath's final native card consumes the cumulative detail rows only.
    record["effects"] = [
        e for e in record["effects"] if e.get("set", {}).get("field") != "_spell_summary_rows"
    ]
    details = next(
        e for e in record["effects"] if e.get("set", {}).get("field") == "_spell_detail_rows"
    )
    actual_rows = details["set"]["value"]
    if not isinstance(actual_rows, list) or len(actual_rows) != 1:
        raise SystemExit("The final native area card row contract changed; rebind Breath rows.")
    index = record["effects"].index(details)
    record["effects"][index : index + 1] = [
        _set("_spell_detail_rows", "prior_rows"),
        _push("_spell_detail_rows", deepcopy(actual_rows[0])),
    ]
    target = deepcopy(tools["resolve_spell_area_save_damage_target"])
    target["parameters"].append(deepcopy(prior_rows))
    target["pre_conditions"].append(
        {
            "condition": "spell == 'summon_dragon' and failed_save_condition == 'none' and "
            "construct_save_disadvantage == false and save_ability == 'dexterity'",
            "error": "This adapter assembles only the printed Dragon Breath damage rows.",
        }
    )
    # This source cannot apply the formatter's optional Blinded effect; the adapter's
    # precondition fixes that option to none before any save or packet executes.
    target["effects"] = [
        e
        for e in target["effects"]
        if e.get("condition")
        != "_spell_save_outcome == 'failure' and failed_save_condition == 'blinded'"
    ]
    # Inline these single-use inputs into the same final D20 owner; its exported
    # effective advantage and disadvantage still drive the actual native result.
    d20 = next(e for e in target["effects"] if e.get("call") == "d20_test")
    for variable, parameter in (
        ("_spell_save_modifier", "ability_modifier"),
        ("_spell_save_disadvantage", "disadvantage"),
    ):
        setting = next(e for e in target["effects"] if e.get("set", {}).get("field") == variable)
        if d20["params"][parameter] != "{" + variable + "}":
            raise SystemExit("The final area D20 input changed; rebind the Breath adapter.")
        value = setting["set"]["value"]
        if parameter == "ability_modifier":
            if value != "target.saving_throw_modifiers[save_ability]":
                raise SystemExit("The final area save modifier changed; rebind the Breath adapter.")
            value = "target.saving_throw_modifiers.dexterity"
        d20["params"][parameter] = (
            False if parameter == "disadvantage" else "{" + value + "}"
        )
        target["effects"].remove(setting)
    for effect in _walk(target["effects"]):
        if effect.get("call") == "record_srd_spell_area_target_result":
            effect["call"] = "record_srd_dragon_breath_target_result"
            effect["params"]["prior_rows"] = "{prior_rows}"
            effect["export_context"].pop("_spell_summary_rows", None)
    return {
        "record_srd_dragon_breath_target_result": record,
        "resolve_srd_dragon_breath_target": target,
    }


def _dragon_breath_hooks(tools):
    """Derive the complete Breath Cone and its physical Cover before spending the saved action."""
    qualify = _tool(
        "Derive the saved Dragon's complete Cone targets, apex and actual Cover.",
        [
            _entity("actor_state"),
            _parameter("apex", "array", items={"type": "number"}, min_items=0, max_items=3),
            _parameter(
                "direction",
                "array",
                required=False,
                default=[],
                items={"type": "number"},
                min_items=0,
                max_items=3,
            ),
            _optional("include_origin", "boolean", False),
        ],
        [
            _call(
                "collect_srd_cone_creature_targets",
                {
                    "source_state": "{_actor_state_id}",
                    "length_feet": "{actor_state.summon_breath_cone_feet}",
                    "apex": "{apex}",
                    "direction": "{direction}",
                    "include_origin": "{include_origin}",
                },
                ("_srd_cone_targets", "_srd_cone_origin", "_srd_cone_axis", "_srd_cone_source"),
            ),
            _set("_srd_cone_source_frames", ["_srd_cone_source"]),
            _call(
                "collect_srd_spell_area_cover_targets",
                {
                    "source_state": "{_actor_state_id}",
                    "targets": "{_srd_cone_targets}",
                    "origin": "{_srd_cone_origin}",
                    "axis": "{_srd_cone_axis}",
                    "source_frames": "{_srd_cone_source_frames}",
                },
                ("_srd_area_targets", "_srd_area_half_cover", "_srd_area_three_quarters"),
            ),
        ],
    )
    breath = deepcopy(tools["resolve_srd_summon_breath"])
    removed = {
        "targets",
        "targets_max_distance",
        "all_targets_in_cone",
        "all_targets_have_clear_path",
        "half_cover_targets",
        "three_quarters_cover_targets",
    }
    breath["parameters"] = [
        p
        for p in breath["parameters"]
        if p["name"] not in removed | {"breath_apex", "breath_direction", "breath_include_origin"}
    ]
    breath["parameters"].append(
        _parameter(
            "breath_apex",
            "array",
            required=False,
            default=[],
            items={"type": "number"},
            min_items=0,
            max_items=3,
        )
    )
    breath["parameters"] += [
        _parameter(
            "breath_direction",
            "array",
            required=False,
            default=[],
            items={"type": "number"},
            min_items=0,
            max_items=3,
        ),
        _optional("breath_include_origin", "boolean", False),
    ]
    breath["pre_conditions"] = [
        p for p in breath["pre_conditions"] if "targets_max_distance" not in p["condition"]
    ]
    source_condition = (
        "actor_state.summon_spell_id == 'summon_dragon' and "
        "actor_state.summon_has_scene_proxy == true"
    )
    if not any(p["condition"] == source_condition for p in breath["pre_conditions"]):
        breath["pre_conditions"].append(
            {
                "condition": source_condition,
                "error": "Breath requires the actual source-owned Dragon scene body.",
            }
        )
    breath["effects"] = [
        e for e in breath["effects"] if e.get("call") != "qualify_srd_summon_breath_targets"
    ]
    breath["effects"].insert(
        0,
        _call(
            "qualify_srd_summon_breath_targets",
            {
                "actor_state": "{_actor_state_id}",
                "apex": "{breath_apex}",
                "direction": "{breath_direction}",
                "include_origin": "{breath_include_origin}",
            },
            (
                "_srd_area_targets",
                "_srd_area_half_cover",
                "_srd_area_three_quarters",
                "_srd_cone_origin",
            ),
        ),
    )
    for effect in _walk(breath["effects"]):
        if effect.get("for_each", {}).get("collection") in {
            "targets",
            "_srd_breath_targets",
            "_srd_area_targets",
        }:
            effect["for_each"]["collection"] = "_srd_area_targets"
        if effect.get("call") in {
            "resolve_spell_area_save_damage_target",
            "resolve_srd_dragon_breath_target",
        }:
            effect["call"] = "resolve_srd_dragon_breath_target"
            effect["params"]["half_cover_targets"] = "{_srd_area_half_cover}"
            effect["params"]["three_quarters_cover_targets"] = "{_srd_area_three_quarters}"
            effect["params"]["prior_rows"] = "{_summon_breath_rows}"
            effect["export_context"] = {"_summon_breath_rows": "_spell_detail_rows"}
    # The existing raw-damage owner finishes before target saves; this one shared roll
    # has the same nonnegative value for every target in the atomic Breath action.
    for index, effect in enumerate(breath["effects"]):
        loop = effect.get("for_each", {})
        if loop.get("collection") != "_srd_area_targets":
            continue
        shared = [
            e
            for e in loop["do"]
            if e.get("set", {}).get("value") == "max(0, _summon_breath_roll.total)"
        ]
        if shared:
            loop["do"] = [e for e in loop["do"] if e not in shared]
            breath["effects"][index:index] = shared
        loop["do"] = [
            e
            for e in loop["do"]
            if e.get("for_each", {}).get("collection") != "_breath_target_rows"
        ]
        break
    return (
        _area_cover_tools()
        | _dragon_breath_row_tools(tools)
        | {
            "qualify_srd_summon_breath_targets": qualify,
            "resolve_srd_summon_breath": breath,
        }
    )


def _independent_actor_tools(tools):
    """Prepare and return only source-owned individuals, retaining the original NPC owner."""
    preparation = deepcopy(tools["prepare_srd_creature"])
    preparation["description"] = (
        "Install only the fixed printed Steed profile or return its saved individual."
    )
    preparation["metadata"] = {
        "category": "internal",
        "visible_to_player": False,
        "costs_action": False,
    }
    preparation["parameters"] += [
        _entity("source_caster"),
        _parameter("source_spell"),
        _parameter("source_level", "integer"),
        _parameter("returning", "boolean"),
        _parameter("source_proficiency_bonus", "integer", minimum=2, maximum=9),
        _optional("lasting_state", resolve="entity", default="state"),
    ]
    preparation["pre_conditions"] = [
        p
        for p in preparation["pre_conditions"]
        if p["condition"] != "combat.encounter_active != true and combat.turn_active != true"
    ]
    preparation["effects"].insert(0, _lookup("source_spell", "_created_definition"))
    preparation["effects"].insert(
        1,
        _require(
            "source_spell == 'find_steed' and source_level >= 2 and source_level <= 9",
            "Only the printed Find Steed source can use this actor installer.",
        ),
    )
    fixed = {
        "armor_class": "_created_definition.armor_class_base + source_level",
        "hit_points": "_created_definition.base_hit_points + source_level * "
        "_created_definition.hit_points_per_level",
        "speed": "_created_definition.speed",
        "size_rank": "_created_definition.size_rank",
        "initiative_modifier": "(_created_definition.dexterity_score - 10) // 2",
        "proficiency_bonus": "source_proficiency_bonus",
        "melee_reach": "_created_definition.melee_reach",
        **{
            a + suffix: "_created_definition." + a + suffix
            for a in ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma")
            for suffix in ("_score", "_save")
        },
    }
    for name, value in fixed.items():
        preparation["effects"].insert(
            2,
            _require(
                name + " == " + value,
                "The created actor must retain its exact printed source statistics.",
            ),
        )
    preparation["effects"].insert(
        2,
        _require(
            "(source_level < 4 and flying_speed == 0) or "
            "(source_level >= 4 and flying_speed == _created_definition.flying_speed)",
            "Only a level 4+ Otherworldly Steed has its printed Flying Speed.",
        ),
    )
    preparation["effects"].insert(
        2,
        _require(
            "creature_type == 'celestial' or creature_type == 'fey' or creature_type == 'fiend'",
            "Choose only a printed Otherworldly Steed creature type.",
        ),
    )
    preparation["effects"].insert(
        2,
        _require(
            "len(damage_immunities) == 0 and len(damage_resistances) == 0 and "
            "len(damage_vulnerabilities) == 0 and len(inventory) == 0 and len(spells) == 0 and "
            "len(multiattack) == 0 and len(equipment) == 0",
            "The Steed's printed creation profile supplies no additional defenses, "
            "equipment or spells.",
        ),
    )
    for index, effect in enumerate(preparation["effects"]):
        if "create_entity" not in effect:
            continue
        body = effect["create_entity"]["value"]
        returning = _when(
            "returning == true",
            [
                _require(
                    "creature_id in lasting_state.created_actor_ids",
                    "A returning source creature needs its original saved record.",
                ),
                _set("_returning_record", "lasting_state.created_actor_records[creature_id]"),
                _require(
                    "_returning_record.caster == _source_caster_id and "
                    "_returning_record.spell == source_spell and _returning_record.active != true",
                    "The saved individual must belong to this caster and spell.",
                ),
                _call(
                    "qualify_srd_created_actor_return",
                    {
                        "actor_state": "{creature_id}",
                        "caster_state": "{_source_caster_id}",
                        "spell": "{source_spell}",
                    },
                ),
                *[_set("creature_id." + field, value) for field, value in body.items()],
            ],
        )
        returning["on_false"] = [effect]
        preparation["effects"][index] = returning
        break
    creation = _tool_scene(
        "Cast Find Steed with exact source stats, actual space, and saved individual ownership.",
        [
            _entity("caster_state"),
            _parameter("slot_level", "integer"),
            _parameter("casting_attack_bonus", "integer"),
            _parameter("casting_save_dc", "integer"),
            _parameter("casting_proficiency_bonus", "integer", minimum=2, maximum=9),
            *deepcopy(INDEPENDENT_CAST_CHOICES),
            *deepcopy(ACTOR_PLACEMENT_CHOICES),
        ],
        [
            _lookup("'find_steed'", "_created_profile"),
            _require(
                "len(created_individual_id) > 0", "Choose the created individual's stable identity."
            ),
            _when(
                "state.created_actor_ids == null",
                [
                    _set("state.created_actor_ids", []),
                    _set("state.created_actor_records", {}),
                    _set("state.created_actor_dropped_items", []),
                ],
            ),
            _set("_created_returning", "created_individual_id in state.created_actor_ids"),
            _require(
                "_created_returning == return_created_individual",
                "Choose return only for this saved disappeared individual; "
                "a new individual needs a new ID.",
            ),
            _when(
                "_created_returning == true",
                [
                    _set("_created_prior", "state.created_actor_records[created_individual_id]"),
                    _require(
                        "_created_prior.caster == _caster_state_id and "
                        "_created_prior.spell == 'find_steed' and "
                        "_created_prior.active != true and "
                        "_created_prior.disappearance != 'forever'",
                        "Only this caster's disappeared Find Steed individual can return.",
                    ),
                ],
            ),
            _when(
                "caster_state.created_steed_id != null",
                [
                    _set("_created_replaced_id", "caster_state.created_steed_id"),
                    _call(
                        "retire_srd_created_actor",
                        {
                            "actor_state": "{_created_replaced_id}",
                            "reason": "replaced",
                        },
                    ),
                    _call(
                        "remove_srd_created_actor_initiative",
                        {"actor_id": "{_created_replaced_id}"},
                    ),
                ],
            ),
            _when(
                "_created_returning == true",
                [
                    _call(
                        "remove_srd_created_actor_initiative",
                        {"actor_id": "{created_individual_id}"},
                    ),
                    _call(
                        "remove_srd_inactive_created_scene",
                        {
                            "caster_state": "{_caster_state_id}",
                            "actor_id": "{created_individual_id}",
                            "spell": "find_steed",
                        },
                    ),
                ],
            ),
            _call(
                "create_srd_source_actor_scene",
                {
                    "caster_state": "{_caster_state_id}",
                    "summon_id": "{created_individual_id}",
                    "spell": "find_steed",
                    "range_feet": 30,
                    "half_extent": 5,
                    "creature_type": "{created_creature_type}",
                    "requires_sight": False,
                    **{p["name"]: "{" + p["name"] + "}" for p in ACTOR_PLACEMENT_CHOICES},
                },
            ),
            _set("_created_fly_speed", 0),
            _when(
                "slot_level >= _created_profile.flight_min_level",
                [
                    _set("_created_fly_speed", "_created_profile.flying_speed"),
                ],
            ),
            _call(
                "prepare_srd_source_created_actor",
                {
                    "source_caster": "{_caster_state_id}",
                    "source_spell": "find_steed",
                    "source_level": "{slot_level}",
                    "returning": "{_created_returning}",
                    "source_proficiency_bonus": "{casting_proficiency_bonus}",
                    "creature_id": "{created_individual_id}",
                    "name": "Otherworldly Steed",
                    "source": "SRD5.2.1 Otherworldly Steed p131",
                    "armor_class": "{_created_profile.armor_class_base + slot_level}",
                    "hit_points": "{_created_profile.base_hit_points + "
                    "slot_level * _created_profile.hit_points_per_level}",
                    "speed": "{_created_profile.speed}",
                    "size_rank": "{_created_profile.size_rank}",
                    "initiative_modifier": "{(_created_profile.dexterity_score - 10) // 2}",
                    "proficiency_bonus": "{casting_proficiency_bonus}",
                    "creature_type": "{created_creature_type}",
                    "melee_reach": "{_created_profile.melee_reach}",
                    "flying_speed": "{_created_fly_speed}",
                    **{
                        a + suffix: "{_created_profile." + a + suffix + "}"
                        for a in (
                            "strength",
                            "dexterity",
                            "constitution",
                            "intelligence",
                            "wisdom",
                            "charisma",
                        )
                        for suffix in ("_score", "_save")
                    },
                },
            ),
            _call(
                "finish_srd_steed_source_actor",
                {
                    "caster_state": "{_caster_state_id}",
                    "actor_state": "{created_individual_id}",
                    "slot_level": "{slot_level}",
                    "attack_bonus": "{casting_attack_bonus}",
                    "save_dc": "{casting_save_dc}",
                },
            ),
            _set("_created_registered_ids", "state.created_actor_ids"),
            _when(
                "_created_returning != true",
                [_push("_created_registered_ids", "created_individual_id")],
            ),
            _set("state.created_actor_ids", "_created_registered_ids"),
            _set(
                "state.created_actor_records.{created_individual_id}",
                {
                    "caster": "_caster_state_id",
                    "spell": "'find_steed'",
                    "individual": "created_individual_id",
                    "active": True,
                    "disappearance": "''",
                    "spell_level": "slot_level",
                    "source_page": 131,
                },
            ),
            _set("state.has_created_actors", True),
            _set("caster_state.created_steed_id", "created_individual_id"),
            _when(
                "combat.encounter_active == true",
                [
                    _call(
                        "register_srd_summon_in_initiative",
                        {
                            "caster_state": "{_caster_state_id}",
                            "summon_state": "{created_individual_id}",
                            "lasting_state": "state",
                        },
                    ),
                ],
            ),
            _set(
                "_spell_summary_rows",
                [
                    {"label": "Spell", "value": "'find_steed'"},
                    {"label": "Individual", "value": "created_individual_id"},
                ],
            ),
            _set(
                "_spell_detail_rows",
                [
                    {"label": "Source", "value": "'Otherworldly Steed, SRD5.2.1 p131'"},
                    {"label": "Spell level", "value": "slot_level"},
                    {"label": "Returned individual", "value": "_created_returning"},
                ],
            ),
        ],
    )
    finish = _tool(
        "Retain the Steed's printed source identity and slot-scaled attack on its real body.",
        [
            _entity("caster_state"),
            _entity("actor_state"),
            _parameter("slot_level", "integer"),
            _parameter("attack_bonus", "integer"),
            _parameter("save_dc", "integer"),
        ],
        [
            _set("actor_state.created_spell_id", "'find_steed'"),
            _set("actor_state.created_caster_state_id", "_caster_state_id"),
            _set("actor_state.created_individual_id", "_actor_state_id"),
            _set("actor_state.created_active", True),
            _set("actor_state.created_disappearance", "''"),
            _set("actor_state.created_spell_level", "slot_level"),
            _set("actor_state.created_attack_bonus", "attack_bonus"),
            _set("actor_state.created_save_dc", "save_dc"),
            _set("actor_state.created_attack_damage_type", "'radiant'"),
            _when(
                "actor_state.creature_type == 'fey'",
                [_set("actor_state.created_attack_damage_type", "'psychic'")],
            ),
            _when(
                "actor_state.creature_type == 'fiend'",
                [_set("actor_state.created_attack_damage_type", "'necrotic'")],
            ),
            _set("actor_state.hit_dice", "slot_level"),
            _set("actor_state.hit_die", "'d10'"),
            _set("actor_state.passive_perception", 11),
            _set("actor_state.summon_caster_state_id", "_caster_state_id"),
            _set("actor_state.summon_active", False),
            _set("actor_state.languages", []),
            _set("actor_state.telepathy_feet", 5280),
            _set("actor_state.death_tick", None),
            _set("actor_state.body_available", True),
            _set("actor_state.vital_parts_present", True),
        ],
    )
    retire = _tool_scene(
        "Retire exactly this created body and leave its actual carried items at its former space.",
        [_entity("actor_state"), _parameter("reason")],
        [
            _when(
                "actor_state.created_active == true",
                [
                    _require(
                        "_actor_state_id in state.created_actor_ids",
                        "The created body needs its actual source record.",
                    ),
                    *_scene_position("{_actor_state_id}", "_created_last_position"),
                    _set("_created_last_plane", "_info_scene_entity.metadata.plane"),
                    _set("_created_carried", "actor_state.inventory"),
                    _set("_created_dropped", "state.created_actor_dropped_items"),
                    _loop(
                        "_created_carried",
                        "_created_item",
                        [
                            _push(
                                "_created_dropped",
                                {
                                    "item_id": "_created_item",
                                    "position": "_created_last_position",
                                    "plane": "_created_last_plane",
                                    "former_carrier": "_actor_state_id",
                                },
                            ),
                        ],
                    ),
                    _set("state.created_actor_dropped_items", "_created_dropped"),
                    _set("actor_state.inventory", []),
                    _set("actor_state.held_items", []),
                    _set("actor_state.created_active", False),
                    _set("actor_state.created_disappearance", "reason"),
                    _set("actor_state.is_alive", False),
                    _set("actor_state.body_available", False),
                    _set("actor_state.vital_parts_present", False),
                    _set("state.created_actor_records.{_actor_state_id}.active", False),
                    _set("state.created_actor_records.{_actor_state_id}.disappearance", "reason"),
                    _call(
                        "update_srd_spell_force_scene",
                        {
                            "force_id": "{_actor_state_id}",
                            "position": "{_created_last_position}",
                            "active": False,
                        },
                    ),
                    _call(
                        "clear_srd_created_actor_link",
                        {
                            "caster_state": "{actor_state.created_caster_state_id}",
                            "actor_id": "{_actor_state_id}",
                        },
                    ),
                ],
            ),
        ],
    )
    remove_scene = _tool_scene(
        "Replace only the inactive scene body belonging to this exact saved source individual.",
        [_entity("caster_state"), _parameter("actor_id"), _parameter("spell")],
        [
            _set("_created_record", "state.created_actor_records[actor_id]"),
            _require(
                "_created_record.caster == _caster_state_id and _created_record.spell == spell "
                "and _created_record.active != true",
                "The inactive source scene belongs to another individual.",
            ),
            _set("_created_before", "state.spatial_scene.entities"),
            _set("_created_after", []),
            _loop(
                "_created_before",
                "_created_row",
                [
                    _when(
                        "_created_row.id == actor_id",
                        [
                            _require(
                                "_created_row.active != true and "
                                "_created_row.metadata.source_caster == _caster_state_id "
                                "and _created_row.metadata.source_ref == spell",
                                "The old source body must be inactive.",
                            ),
                        ],
                    ),
                    _when("_created_row.id != actor_id", [_push("_created_after", "_created_row")]),
                ],
            ),
            _set("state.spatial_scene.entities", "_created_after"),
            _set("state.spatial_scene.map.revision", "state.spatial_scene.map.revision + 1"),
            *_snapshot(),
        ],
    )
    remove_order = _tool(
        "Remove only this disappeared body from the real initiative order.",
        [_parameter("actor_id")],
        [
            _when(
                "combat.group_order == true",
                [
                    _require(
                        "combat.encounter_active != true or combat.active_actor != actor_id",
                        "The initiative owner must finish and skip the disappeared active body.",
                    ),
                    _set("_created_old_order", "combat.order"),
                    _set("_created_new_order", []),
                    _set("_created_order_index", 0),
                    _set("_created_turn_index", "combat.turn_index"),
                    _loop(
                        "_created_old_order",
                        "_created_order_row",
                        [
                            _when(
                                "_created_order_row.entity_id == actor_id and "
                                "_created_order_index < combat.turn_index",
                                [
                                    _set("_created_turn_index", "max(0, _created_turn_index - 1)"),
                                ],
                            ),
                            _when(
                                "_created_order_row.entity_id != actor_id",
                                [_push("_created_new_order", "_created_order_row")],
                            ),
                            _set("_created_order_index", "_created_order_index + 1"),
                        ],
                    ),
                    _set("combat.order", "_created_new_order"),
                    _set("combat.turn_index", "_created_turn_index"),
                    _when(
                        "combat.encounter_active == true",
                        [
                            _set("combat.lasting_epoch_round", "combat.round"),
                            _set(
                                "combat.lasting_epoch_tick",
                                "state.lasting_tick - "
                                "2 * combat.turn_index * (len(combat.order) ** -1)",
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    clear_link = _tool(
        "Clear only the disappeared individual's current source link on its caster.",
        [_entity("caster_state"), _parameter("actor_id")],
        [
            _when(
                "caster_state.created_steed_id == actor_id",
                [_set("caster_state.created_steed_id", None)],
            )
        ],
    )
    qualify_return = _tool(
        "Qualify the actual bound individual before source-owned reappearance changes its body.",
        [_entity("actor_state"), _entity("caster_state"), _parameter("spell")],
        [
            _require(
                "actor_state.created_caster_state_id == _caster_state_id and "
                "actor_state.created_spell_id == spell and actor_state.created_active != true and "
                "actor_state.created_disappearance != 'forever'",
                "Only this caster's disappeared source individual can reappear.",
            )
        ],
    )
    return {
        "prepare_srd_source_created_actor": preparation,
        "cast_spell_independent_created_creature": creation,
        "finish_srd_steed_source_actor": finish,
        "retire_srd_created_actor": retire,
        "remove_srd_inactive_created_scene": remove_scene,
        "remove_srd_created_actor_initiative": remove_order,
        "clear_srd_created_actor_link": clear_link,
        "qualify_srd_created_actor_return": qualify_return,
    }


def _giant_insect_source_profile():
    """Bind every insect form to the complete printed spell and its actual following-page block."""
    import hashlib
    import json

    path = Path(__file__).resolve().parents[1] / "rulesets/srd-lite/content/corpus/spells.json"
    corpus = json.loads(path.read_text(encoding="utf-8"))
    pages = {row["page"]: row for row in corpus["page_records"] if row["page"] in (135, 136)}
    hashes = {
        135: "594c76619899b19a5161c202e0c5a9608455d2054170e6d7c939f15691b491d9",
        136: "6266baae3951af98d36e2356bea4fdd3c641843023714dc3a316372f92a8e932",
    }
    if any(pages[page]["text_sha256"] != expected or
           hashlib.sha256(pages[page]["text"].encode()).hexdigest() != expected
           for page, expected in hashes.items()):
        raise SystemExit("Giant Insect differs from its pinned actual source pages.")
    spell = " ".join(pages[135]["text"].split("Giant Insect\n", 1)[1].split())
    block = pages[136]["text"].split("Giant Insect\n", 1)[1].split("Glibness\n", 1)[0]
    text = " ".join(block.split())
    clauses = (
        "Large Beast, Unaligned", "AC 11 + the spell’s level",
        "HP 30 + 10 for each spell level above 4",
        "Speed 40 ft., Climb 40 ft., Fly 40 ft. (Wasp only)",
        "Str 17 +3 +3 dex 13 +1 +1 con 15 +2 +2",
        "int 4 −3 −3 WiS 14 +2 +2 chA 3 −4 −4",
        "Senses Darkvision 60 ft.; Passive Perception 12",
        "Languages Understands the languages you know",
        "CR None (XP 0; PB equals your Proficiency Bonus)",
        "Spider Climb. The insect can climb difficult surfaces",
        "including along ceilings, without needing to make an ability check.",
        "equal to half this spell’s level (round down).",
        "Poison Jab. Melee Attack Roll: Bonus equals your spell attack modifier, reach 10 ft.",
        "1d6 + 3 plus the spell’s level Piercing damage plus 1d4 Poison damage.",
        "Web Bolt (Spider Only). Ranged Attack Roll:", "range 60 ft.",
        "1d10 + 3 plus the spell’s level Bludgeoning damage",
        "target’s Speed is reduced to 0 until the start of the in- sect’s next turn.",
        "Venomous Spew (Centipede Only). Constitution Saving Throw: Your spell save DC",
        "one creature the insect can see within 10 feet.",
        "Poisoned condition until the start of the insect’s next turn.",
    )
    spell_clauses = (
        "Level 4 Conjuration (Druid)", "Casting Time: Action", "Range: 60 feet",
        "Components: V, S", "Duration: Concentration, up to 10 minutes",
        "giant centipede, spider, or wasp", "un - occupied space you can see within range",
        "creature disappears when it drops to 0 Hit Points or when the spell ends.",
        "creature is an ally to you and your allies", "shares your Initiative count",
        "takes its turn immediately after yours", "obeys your verbal commands",
        "no action required by you", "takes the Dodge action",
        "uses its movement to avoid danger",
    )
    if any(clause not in text for clause in clauses) or any(
        clause not in spell for clause in spell_clauses
    ):
        raise SystemExit("Giant Insect's complete printed actor or lifetime clauses changed.")
    abilities = ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma")
    return {
        "source_pages": [{"page": page, "page_text_sha256": hashes[page]} for page in (135, 136)],
        "source_stat_block": block,
        "source_stat_block_sha256": hashlib.sha256(block.encode()).hexdigest(),
        "ability_scores": dict(zip(abilities, (17, 13, 15, 4, 14, 3), strict=True)),
        "saving_throw_modifiers": dict(zip(abilities, (3, 1, 2, -3, 2, -4), strict=True)),
        "armor_class_base": 11, "base_hit_points": 30, "base_level": 4,
        "hit_points_per_level": 10, "size_rank": 3, "speed": 40, "climbing_speed": 40,
        "wasp_flying_speed": 40, "initiative_modifier": 1, "darkvision_feet": 60,
        "passive_perception": 12, "challenge_rating_eighths": None,
        "attacks_per_level_divisor": 2,
        "attacks": {
            "Poison Jab": {"kind": "melee", "normal_range": 10, "long_range": 10,
                           "damage_expression": "d6", "damage_modifier_base": 3,
                           "damage_type": "piercing", "extra_damage_expression": "d4",
                           "extra_damage_type": "poison"},
            "Web Bolt": {"kind": "ranged", "form": "spider", "normal_range": 60,
                         "long_range": 60, "damage_expression": "d10",
                         "damage_modifier_base": 3, "damage_type": "bludgeoning"},
        },
    }


def _insect_actor_tools(tools):
    """Retain the genuine summoned-actor lifecycle with only the printed insect body's fields."""
    source = _giant_insect_source_profile()
    install = deepcopy(tools["install_srd_summoned_creature"])
    keep = {"caster_state", "lasting_state", "summon_id", "slot_level", "attack_bonus",
            "save_dc", "concentration_save_modifier", "caster_proficiency_bonus",
            "actual_spell_level", "scene_proxy_created"}
    install["parameters"] = [p for p in install["parameters"] if p["name"] in keep]
    install["parameters"] += deepcopy(INSECT_CAST_CHOICES)
    install["description"] = "Install the printed Giant Insect through the summon lifetime owner."
    install["pre_conditions"] = [
        {"condition": "caster_state.is_alive == true and caster_state.hp > 0",
         "error": "The actual summoner must be alive."},
        {"condition": "slot_level >= 4 and slot_level <= 9",
         "error": "Use the actual level4+ spell slot for the printed insect body."},
        {"condition": "len(summon_id) >= 1 and len(summon_id) <= 80",
         "error": "Choose one stable actual insect identity."},
    ]
    fixed = {
        "spell": "'giant_insect'", "duration_rounds": 100,
        "creature_name": "'Giant Insect'", "creature_type": "'beast'", "alignment": "'unaligned'",
        "base_level": source["base_level"], "base_hit_points": source["base_hit_points"],
        "hit_points_per_level": source["hit_points_per_level"],
        "armor_class_base": source["armor_class_base"], "speed": source["speed"],
        "flying_speed": 0, "swimming_speed": 0, "size_rank": source["size_rank"],
        "melee_reach": 10, "rend_name": "'Poison Jab'", "rend_damage_die": "'d6'",
        "rend_damage_type": "'piercing'", "rend_base_damage_modifier": 3,
        "condition_immunities": [], "damage_resistances": [], "blindsight_feet": 0,
        "darkvision_feet": source["darkvision_feet"],
        "passive_perception": source["passive_perception"], "languages": [],
        "understands_caster_languages": True,
        **{ability + "_score": score for ability, score in source["ability_scores"].items()},
        **{ability + "_save": score for ability, score in source["saving_throw_modifiers"].items()},
    }
    source_names = re.compile(r"(?<![\w.])(" + "|".join(map(re.escape, fixed)) + r")\b")

    def bind_printed_locals(value):
        if isinstance(value, str):
            return source_names.sub(lambda match: "_insect_" + match[0], value)
        if isinstance(value, list):
            return [bind_printed_locals(item) for item in value]
        if isinstance(value, dict):
            return {key: bind_printed_locals(item) for key, item in value.items()}
        return value

    install["effects"] = bind_printed_locals(install["effects"])
    install["effects"][:0] = [
        *[_set("_insect_" + name, value) for name, value in fixed.items()],
        _when("insect_form == 'wasp'", [_set("_insect_flying_speed", source["wasp_flying_speed"])]),
    ]
    language = next(e for e in install["effects"] if
                    "spell == 'summon_dragon'" in e.get("condition", ""))
    language["condition"] = "caster_state.languages != null"
    creation = next(e["create_entity"]["value"] for e in install["effects"]
                    if "create_entity" in e)
    for key in tuple(creation):
        if key.startswith("summon_breath_") or key in ("summon_damage_type", "multiattack"):
            del creation[key]
    creation.update({
        "ability_scores": deepcopy(creation["abilities"]),
        "climbing_speed": source["climbing_speed"],
        "challenge_rating_eighths": None,
        "created_spell_id": "_insect_spell", "created_spell_level": "slot_level",
        "created_caster_state_id": "_caster_state_id", "created_individual_id": "summon_id",
        "created_active": True, "created_presence": "'present'", "created_disappearance": None,
        "created_insect_form": "insect_form",
        "created_insect_attacks_per_action": "slot_level // 2",
        "created_source_pages": source["source_pages"],
        "created_source_stat_block": source["source_stat_block"],
        "created_source_block_sha256": source["source_stat_block_sha256"],
        "created_insect_attacks": source["attacks"],
        "created_spider_climb": True,
        "body_available": False, "vital_parts_present": False, "death_tick": None,
        "living_creature_type": "'beast'",
    })
    selection = next(e for e in install["effects"] if e.get("set", {}).get("field") ==
                     "caster_state.concentration_selected_damage_type")
    selection["set"]["value"] = None
    cast = _tool_scene(
        "Create the printed insect in an actual visible unoccupied source-sized space.", [
            _entity("caster_state"), _parameter("slot_level", "integer"),
            _parameter("casting_attack_bonus", "integer"),
            _parameter("casting_save_dc", "integer"),
            _parameter("casting_proficiency_bonus", "integer"),
            _parameter("concentration_modifier", "integer"), _parameter("summon_id"),
            *deepcopy(INSECT_CAST_CHOICES), *deepcopy(ACTOR_PLACEMENT_CHOICES),
        ], [
            _require("slot_level >= 4 and slot_level <= 9",
                     "Giant Insect requires its actual level 4+ slot."),
            _when("caster_state.concentration_spell_id != null", [
                _call("end_srd_concentration_node", {
                    "source_state": "{_caster_state_id}",
                    "target_state": "{caster_state.concentration_target_state_id}",
                }),
            ]),
            _call("create_srd_source_actor_scene", {
                "caster_state": "{_caster_state_id}", "summon_id": "{summon_id}",
                "spell": "giant_insect", "range_feet": 60, "half_extent": 5,
                "creature_type": "beast",
                **{p["name"]: "{" + p["name"] + "}" for p in ACTOR_PLACEMENT_CHOICES},
            }),
            _call("install_srd_source_insect", {
                "caster_state": "{_caster_state_id}", "lasting_state": "state",
                "summon_id": "{summon_id}", "slot_level": "{slot_level}",
                "attack_bonus": "{casting_attack_bonus}", "save_dc": "{casting_save_dc}",
                "caster_proficiency_bonus": "{casting_proficiency_bonus}",
                "concentration_save_modifier": "{concentration_modifier}",
                "actual_spell_level": "{slot_level}", "scene_proxy_created": True,
                "insect_form": "{insect_form}",
            }, ("_summon_state_id",)),
            _set("_spell_summary_rows", [{"label": "'Giant Insect'", "value": "summon_id"}]),
            _set("_spell_detail_rows", [
                {"label": "'Form'", "value": "insect_form"},
                {"label": "'Printed spell level'", "value": "slot_level"},
                {"label": "'Concentration duration, rounds'", "value": 100},
            ]),
        ],
    )
    return {"install_srd_source_insect": install, "cast_spell_source_insect": cast}


def _insect_actor_hooks(tools):
    """Bind the exact source body to paid casting and the existing concentration dismissal."""
    changed = _insect_actor_tools(tools)
    for name in ("cast_spell", "cast_spell_node", "cast_spell_outside_combat"):
        if name not in tools:
            continue
        cast = deepcopy(tools[name])
        names = {p["name"] for p in cast["parameters"]}
        cast["parameters"] += [deepcopy(p) for p in INSECT_CAST_CHOICES + ACTOR_PLACEMENT_CHOICES
                               if p["name"] not in names]
        branch = _when("_spell_cast_failed != true and "
                       "_spell_definition.family == 'summoned_insect_creature'", [
            _require("_casting_profile.proficiency_bonus != null",
                     "The insect needs its actual caster's Proficiency Bonus."),
            _call("cast_spell_source_insect", {
                "caster_state": "{_caster_state_id}", "slot_level": "{_slot_level}",
                "summon_id": "{summon_id}",
                "casting_attack_bonus": "{_casting_profile.attack_bonus}",
                "casting_save_dc": "{_casting_profile.save_dc}",
                "casting_proficiency_bonus": "{_casting_profile.proficiency_bonus}",
                "concentration_modifier": "{_casting_profile.concentration_save_modifier}",
                **{p["name"]: "{" + p["name"] + "}"
                   for p in INSECT_CAST_CHOICES + ACTOR_PLACEMENT_CHOICES},
            }, ("_spell_summary_rows", "_spell_detail_rows")),
        ])
        _replace_created_cast_dispatch(cast, "summoned_insect_creature", branch)
        changed[name] = cast
    dismiss = deepcopy(tools["dismiss_srd_summoned_creature"])
    dismiss["effects"] = [
        e for e in dismiss["effects"]
        if e.get("condition") != "summon_state.created_spell_id == 'giant_insect'"
    ]
    dismiss["effects"].append(_when("summon_state.created_spell_id == 'giant_insect'", [
        _set("summon_state.created_active", False),
        _set("summon_state.created_presence", "'gone'"),
        _set("summon_state.created_disappearance", "'spell_ended'"),
    ]))
    changed["dismiss_srd_summoned_creature"] = dismiss
    language = deepcopy(tools["resolve_srd_spell_language"])
    listener = "listener_state.created_spell_id == 'giant_insect' and " \
        "listener_state.created_active == true and listener_state.form_statistics_replaced != true"
    language["effects"] = [e for e in language["effects"] if e.get("condition") != listener]
    language["effects"].append(_when(listener, [
        _when("_utterance_language != '' and _utterance_language in "
              "listener_state.summon_understood_languages and "
              "_utterance_language in _language_speaker_native and "
              "_language_perceived == true", [_set("_language_understands", True)]),
    ]))
    changed["resolve_srd_spell_language"] = language
    return changed


def _insect_attack_tools():
    """Qualify a real native insect and retain its second damage roll in one typed packet."""
    source = _giant_insect_source_profile()
    qualify = _tool("Qualify this actual Giant Insect against its caster and pinned source body.", [
        _entity("actor_state"), _entity("caster_state"),
    ], [
        _require("actor_state.created_spell_id == 'giant_insect' and "
                 "actor_state.created_active == true and actor_state.summon_active == true and "
                 "actor_state.is_alive == true and actor_state.hp > 0 and "
                 "actor_state.form_statistics_replaced != true",
                 "Use the actual present native Giant Insect."),
        _require("actor_state.created_caster_state_id == _caster_state_id and "
                 "actor_state.summon_caster_state_id == _caster_state_id and "
                 "caster_state.concentration_spell_id == 'giant_insect' and "
                 "caster_state.concentration_effect_family == 'summoned_creature' and "
                 "caster_state.concentration_target_state_id == _actor_state_id",
                 "The insect must retain its genuine current concentration source."),
        _require("actor_state.created_source_block_sha256 == '" +
                 source["source_stat_block_sha256"] + "' and "
                 "actor_state.created_individual_id == _actor_state_id and "
                 "actor_state.summon_spell_level >= 4 and actor_state.summon_spell_level <= 9 and "
                 "actor_state.created_spell_level == actor_state.summon_spell_level and "
                 "actor_state.created_insect_attacks_per_action == "
                 "actor_state.summon_spell_level // 2",
                 "The saved individual needs its exact source block and actual slot level."),
    ])
    damage = _tool("Apply Poison Jab's actual Piercing and Poison through one native packet.", [
        _entity("actor_state"), _entity("target_state"),
        _optional("lasting_state", resolve="entity", default="state"),
        _parameter("amount", "integer"), _parameter("critical_hit", "boolean"),
        _optional("witness_state_ids", "array", None, items={"type": "string"}),
        _optional("unallocated_roll_penalty", "integer", 0),
        _optional("minor_elementals_damage_type", "enum", "fire",
                  enum_values=["acid", "cold", "fire", "lightning"]),
    ], [
        _call("qualify_srd_source_insect", {
            "actor_state": "{_actor_state_id}",
            "caster_state": "{actor_state.created_caster_state_id}",
        }),
        {"dice": {"expression": "d4", "critical": "critical_hit"},
         "store_as": "_insect_poison_damage_roll"},
        _set("_insect_poison_damage_amount", "max(0, _insect_poison_damage_roll.total)"),
        _call("apply_srd_qualifying_composite_damage", {
            "target": "{_target_state_id}", "lasting_state": "{_lasting_state_id}",
            "component_1_amount": "{amount}", "component_1_damage_type": "piercing",
            "component_2_amount": "{_insect_poison_damage_amount}",
            "component_2_damage_type": "poison", "source_state_id": "{_actor_state_id}",
            "critical_hit": "{critical_hit}", "witness_state_ids": "{witness_state_ids}",
            "unallocated_roll_penalty": "{unallocated_roll_penalty}",
            "game_damage_roll": True, "actual_attack_hit": True, "observe_attack_components": True,
            "minor_elementals_damage_type": "{minor_elementals_damage_type}",
        }, ("_effective_damage", "_hp_before", "_hp_after", "_temp_hp_absorbed", "_incoming_damage",
            "_is_alive_after", "_conjure_minor_qualifies", "_conjure_minor_amount",
            "_conjure_minor_type", "_conjure_minor_roll", "_concentration_save_dc",
            "_component_1_effective_damage", "_component_2_effective_damage")),
        _set("_hp_lost", "max(0, _hp_before - _hp_after)"),
    ])
    return {"qualify_srd_source_insect": qualify, "apply_srd_insect_poison_jab_damage": damage}


def _insect_attack_hooks(tools):
    """Adapt the final actual creature leaf before the reaction owner derives its continuation."""
    changed = _insect_attack_tools()
    native = "actor.created_spell_id == 'giant_insect' and actor.form_attack_in_progress != true"
    attack = deepcopy(tools["resolve_adjudicated_creature_attack"])
    unsupported_web = "actor.created_spell_id != 'giant_insect' or attack_name != 'Web Bolt'"
    if not any(p["condition"] == unsupported_web for p in attack["pre_conditions"]):
        attack["pre_conditions"].insert(0, {
            "condition": unsupported_web,
            "error": "Web Bolt's source-owned next-turn Speed consumer remains unsupported.",
        })
    attack["effects"] = [e for e in attack["effects"]
                         if e.get("condition") != native or
                         "qualify_srd_source_insect" not in str(e)]
    attack["effects"].insert(0, _when(native, [
        _call("qualify_srd_source_insect", {
            "actor_state": "{_actor_id}", "caster_state": "{actor.created_caster_state_id}",
        }),
        _require("actor.form_statistics_replaced != true",
                 "The insect's replacement-form attacks need their own actual source owner."),
        _require("attack_name == 'Poison Jab'",
                 "Web Bolt's source-owned next-turn Speed consumer remains unsupported."),
        _require("attack_uses_weapon != true",
                 "The printed insect attack has no admitted held weapon."),
        _call("qualify_srd_created_actor_attack_geometry", {
            "actor_state": "{_actor_id}", "target_state": "{_target_id}",
        }, ("_created_attack_distance",)),
        _require("attack_distance == _created_attack_distance",
                 "Use the actual insect and target body distance."),
    ]))
    def receiving_packets(effects):
        for effect in effects:
            if effect.get("condition") == native + " and attack_name == 'Poison Jab'":
                continue
            yield effect
            for key in ("on_true", "on_false"):
                yield from receiving_packets(effect.get(key, []))
            yield from receiving_packets(effect.get("for_each", {}).get("do", []))

    for effect in tuple(receiving_packets(attack["effects"])):
        if effect.get("call") != "apply_srd_qualifying_damage":
            continue
        if "game_damage_roll" not in effect.get("params", {}):
            continue
        original = deepcopy(effect)
        branch = _call("apply_srd_insect_poison_jab_damage", {
            "actor_state": "{_actor_id}", "target_state": "{_target_id}",
            "lasting_state": effect["params"].get("lasting_state", "state"),
            "amount": effect["params"]["amount"],
            "critical_hit": effect["params"]["critical_hit"],
            "witness_state_ids": effect["params"].get("witness_state_ids", "{witness_state_ids}"),
            "unallocated_roll_penalty": effect["params"].get("unallocated_roll_penalty", 0),
            "minor_elementals_damage_type": "{minor_elementals_damage_type}",
        })
        branch["export_context"] = deepcopy(effect.get("export_context", {}))
        branch["export_context"].update({name: name for name in (
            "_insect_poison_damage_roll", "_insect_poison_damage_amount",
            "_component_1_effective_damage", "_component_2_effective_damage",
        )})
        effect.clear()
        effect.update(_choose(native + " and attack_name == 'Poison Jab'", [branch], [original]))
    details = attack["metadata"]["result_card"]["details"]
    if not any(r["label"] == "Poison Jab Poison dice" for r in details):
        details += [
            {"label": "Poison Jab Poison dice", "value": {
                "path": "context._insect_poison_damage_roll.results"}, "optional": True},
            {"label": "Poison Jab Poison before defenses", "value": {
                "path": "context._insect_poison_damage_amount"}, "optional": True},
        ]
    changed["resolve_adjudicated_creature_attack"] = attack
    spend = deepcopy(tools["spend_srd_creature_attack"])
    if not any(e.get("condition") == native for e in spend["effects"]):
        spend["effects"] = [_choose(native, [
            _call("qualify_srd_source_insect", {
                "actor_state": "{_actor_id}", "caster_state": "{actor.created_caster_state_id}",
            }),
            _require("attack_name == 'Poison Jab' or "
                     "(attack_name == 'Web Bolt' and actor.created_insect_form == 'spider')",
                     "Use only a printed attack belonging to this insect form."),
            _when("combat.attacks_remaining == null or combat.attacks_remaining == 0", [
                _call("spend_srd_action", {"magic_action": False, "control_action_kind": "attack"}),
                _set("combat.attacks_remaining", 1),
                _set("combat.multiattack_remaining", []),
                _set("combat.insect_multiattack_active", "use_multiattack"),
                _when("use_multiattack == true", [
                    _set("combat.attacks_remaining", "actor.created_insect_attacks_per_action"),
                ]),
            ]),
            _require("use_multiattack == combat.insect_multiattack_active",
                     "Finish this same printed insect attack action."),
            _set("combat.attacks_remaining", "combat.attacks_remaining - 1"),
            _call("refresh_srd_attack_availability", {}),
            _call("finish_srd_control_attack", {"actor_state": "{_actor_id}"}),
        ], spend["effects"])]
    changed["spend_srd_creature_attack"] = spend
    return changed


def _insect_turn_hooks(tools):
    """Use final ordinary Dodge checks for the insect's default turn without inventing movement."""
    dodge = deepcopy(tools["take_dodge_action"])
    dodge["description"] = "Take the printed insect's default ordinary Dodge action."
    dodge["parameters"] = [p for p in dodge["parameters"] if p["name"] != "actor"]
    for requirement in dodge["pre_conditions"]:
        if requirement["condition"] == "actor.validation_status == 'validated'":
            requirement["condition"] = "actor_state.created_spell_id == 'giant_insect' and " \
                "actor_state.created_active == true and actor_state.is_alive == true"
            requirement["error"] = "Only the actual present source insect can take this action."
        requirement["condition"] = requirement["condition"].replace(
            "actor.participant_id", "_actor_state_id")
    dodge["metadata"] = {"category": "internal", "visible_to_player": False, "costs_action": False}
    benefit = [p["condition"] for p in dodge["pre_conditions"] if
               "actor_state.speed" in p["condition"] or "actor_state.conditions" in p["condition"]]
    assert len(benefit) == 2
    can_act = next(p["condition"] for p in tools["resolve_adjudicated_creature_attack"][
        "pre_conditions"] if "actor.conditions" in p["condition"] and
        "incapacitated" in p["condition"]).replace("actor.", "actor_state.")
    start = _tool("Resolve the printed default Dodge after the actual insect turn begins.", [
        _entity("actor_state"),
    ], [
        _set("_insect_dodge_taken", False), _set("_insect_dodge_benefit", False),
        _call("qualify_srd_source_insect", {
            "actor_state": "{_actor_state_id}",
            "caster_state": "{actor_state.created_caster_state_id}",
        }),
        _when("(actor_state.summon_command == null or actor_state.summon_command == 'dodge') and "
              "combat.action_available == true and (" + can_act + ")", [
            _choose(" and ".join("(" + c + ")" for c in benefit), [
                _call("take_srd_insect_dodge", {"actor_state": "{_actor_state_id}"}),
            ], [
                _call("spend_srd_action", {"magic_action": False}),
                _set("combat.action_taken", "'dodge'"),
            ]),
            _set("_insect_dodge_taken", True),
            _set("_insect_dodge_benefit", "actor_state.dodging"),
        ]),
    ])
    begin = deepcopy(tools["begin_combat_turn"])
    for e in begin["effects"]:
        if "actor.summon_command == null" in e.get("condition", "") and \
                "actor.created_spell_id" not in e["condition"]:
            e["condition"] = "actor.created_spell_id != 'giant_insect' and (" + e["condition"] + ")"
    begin["effects"] = [e for e in begin["effects"]
                        if "start_srd_insect_turn" not in str(e)]
    begin["effects"].append(_when(
        "actor.created_spell_id == 'giant_insect' and actor.created_active == true and "
        "actor.summon_active == true and actor.is_alive == true and actor.hp > 0 and "
        "actor.form_statistics_replaced != true", [
            _call("start_srd_insect_turn", {"actor_state": "{_actor_id}"},
                  ("_insect_dodge_taken", "_insect_dodge_benefit")),
            _when("_insect_dodge_taken == true", [
                _push("_turn_effect_rows", {"label": "'Giant Insect Dodge'",
                                           "value": "_insect_dodge_benefit"}),
                _set("_turn_effect_count", "len(_turn_effect_rows)"),
            ]),
        ]))
    return {"take_srd_insect_dodge": dodge, "start_srd_insect_turn": start,
            "begin_combat_turn": begin}


def bind_insect_runtime_consumers(text):
    """Bind exact native insect action/packet owners before the final reaction clones."""
    tools = _load_runtime(text)["tools"]
    changed = _insect_attack_hooks(tools) | _insect_turn_hooks(tools)
    _normalize(changed)
    return _replace_tools(text, changed)


def _familiar_source_forms():
    """Qualify ordinary CR 0 source bodies; transformation certification alone is insufficient."""
    import json

    corpus_path = Path(__file__).resolve().parents[1] / (
        "rulesets/srd-lite/content/corpus/spells.json"
    )
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    page = next(row["text"] for row in corpus["page_records"] if row["page"] == 130)
    spell = " ".join(page.split("Find Familiar\n", 1)[1].split("Find Steed\n", 1)[0].split())
    clauses = (
        "Casting Time: 1 hour or Ritual", "Range: 10 feet", "Duration: Instantaneous",
        "burning incense worth 10+ GP", "which the spell consumes",
        "another Beast that has a Challenge Rating of 0",
        "Appearing in an unoccupied space within range",
        "it is a Celestial, Fey, or Fiend", "It rolls its own Initiative",
        "A familiar can’t attack", "When the famil - iar drops to 0 Hit Points, it disappears",
        "within 100 feet of you, you can communicate with it telepathically",
        "It reappears after you cast this spell again",
        "As a Magic action, you can temporarily dismiss the familiar to a pocket dimension",
        "Alternatively, you can dismiss it forever",
        "As a Magic action while it is temporarily dismissed",
        "reappear in an unoccupied space within 30 feet of you",
        "it leaves behind in its space anything it was wearing or carrying",
    )
    if any(clause not in spell for clause in clauses):
        raise SystemExit("Find Familiar differs from its pinned source-owned behavior.")
    if __package__:
        from .build_srd_beast_forms import build_index
    else:
        from build_srd_beast_forms import build_index
    forms = {
        key: row
        for key, row in build_index()["forms"].items()
        if row["challenge_rating_eighths"] == 0
        and row["execution_supported"]
        and "Traits" not in row["source_stat_block"]
    }
    if set(forms) != {"bat", "eagle", "hawk", "jackal", "scorpion", "weasel"}:
        raise SystemExit("Review the changed ordinary Familiar source qualification.")
    if any("PB +2" not in row["source_stat_block"] for row in forms.values()):
        raise SystemExit("Familiar source Proficiency Bonus is not the printed +2.")
    for key, original in tuple(forms.items()):
        hit_dice = re.search(
            r"\bHP\s+\d+\s+\((\d+)d(4|6|8|10|12)(?:\s*[+−-]\s*\d+)?\)",
            original["source_stat_block"],
        )
        if hit_dice is None:
            raise SystemExit("The qualified Familiar source must retain its printed Hit Dice.")
        forms[key] = {**original, "source_hit_dice": int(hit_dice[1]),
                      "source_hit_die_size": int(hit_dice[2])}
    return forms


def _zombie_source_profile():
    """Recover Zombie's whole printed block across the Monsters/Animals page boundary."""
    import hashlib
    import json

    if __package__:
        from .build_srd_beast_forms import source_form_profile
    else:
        from build_srd_beast_forms import source_form_profile
    root = Path(__file__).resolve().parents[1] / "rulesets/srd-lite/content/corpus"
    pages = []
    for corpus_name, page_number in (("monsters", 343), ("animals", 344)):
        corpus = json.loads((root / (corpus_name + ".json")).read_text(encoding="utf-8"))
        page = next(p for p in corpus["page_records"] if p["page"] == page_number)
        if hashlib.sha256(page["text"].encode()).hexdigest() != page["text_sha256"]:
            raise SystemExit("Zombie's admitted source page hash differs.")
        pages.append(page)
    block = "Zombie\nMedium Undead" + pages[0]["text"].split(
        "Zombie\nMedium Undead", 1
    )[1] + "\n" + pages[1]["text"].split("Ogre Zombie\n", 1)[0]
    clean = " ".join(block.split())
    clauses = (
        "Medium Undead, Neutral Evil", "AC 8 Initiative −2 (8)",
        "HP 15 (2d8 + 6)", "Speed 20 ft.",
        "Immunities Poison; Exhaustion, Poisoned",
        "Languages Understands Common plus one other language but can’t speak",
        "CR 1/4 (XP 50; PB +2)",
        "Undead Fortitude. If damage reduces the zombie to 0 Hit Points, it makes a "
        "Constitution saving throw (DC 5 plus the damage taken) unless the damage is "
        "Radiant or from a Critical Hit. On a successful save, the zombie drops to "
        "1 Hit Point instead.",
        "Slam. Melee Attack Roll: +3, reach 5 ft. Hit: 5 (1d8 + 1) Bludgeoning damage.",
    )
    if any(clause not in clean for clause in clauses):
        raise SystemExit("Review the changed complete Zombie source before execution.")
    row = source_form_profile("Zombie", "Medium Undead, Neutral Evil", "8", block, pages[0])
    expected_gaps = {
        "Immunities: Poison; Exhaustion",
        "Traits: " + clauses[7],
    }
    if set(row["unsupported_clauses"]) != expected_gaps or set(row["attacks"]) != {"Slam"}:
        raise SystemExit("Zombie has additional unowned source clauses or actions.")
    # These printed fields have summoning consumers; transformation certification is separate.
    return {**row, "source_pages": [
        {"page": page["page"], "page_text_sha256": page["text_sha256"]} for page in pages
    ], "damage_immunities": ["poison"], "condition_immunities": ["exhaustion", "poisoned"],
        "source_hit_dice": 2, "source_hit_die_size": 8, "languages": [],
        "source_understands_common": True, "source_can_speak": False}


def _created_undead_source_profile():
    """Bind Create Undead and its complete printed Ghoul body; retain unowned actions explicitly."""
    import hashlib
    import json

    if __package__:
        from .build_srd_beast_forms import source_form_profile
    else:
        from build_srd_beast_forms import source_form_profile
    root = Path(__file__).resolve().parents[1] / "rulesets/srd-lite/content/corpus"
    spells = json.loads((root / "spells.json").read_text())
    spell_page = next(p for p in spells["page_records"] if p["page"] == 121)
    expected_spell_page = "564c41de7bcb0225e84a7af1d5b36062c0f42ee3be59dba253a51a3502d3ad9d"
    if hashlib.sha256(spell_page["text"].encode()).hexdigest() != expected_spell_page:
        raise SystemExit("Review the changed Create Undead printed source before execution.")
    monsters = json.loads((root / "monsters.json").read_text())
    page = next(p for p in monsters["page_records"] if p["page"] == 288)
    if hashlib.sha256(page["text"].encode()).hexdigest() != page["text_sha256"]:
        raise SystemExit("Ghoul's admitted printed page hash differs.")
    block = "Ghoul\nMedium Undead" + page["text"].split(
        "Ghoul\nMedium Undead", 1
    )[1].split("Gibbering Mouther\n", 1)[0]
    row = source_form_profile("Ghoul", "Medium Undead, Chaotic Evil", "12", block, page)
    if row["source_stat_block_sha256"] != (
        "ca93598a7266b964cec2a4c96725334f2e20800bfa8ccc9693c82aa0657870e1"
    ) or set(row["attacks"]) != {"Bite", "Claw"}:
        raise SystemExit("Review the changed complete Ghoul source before execution.")
    expected_gaps = {
        "Immunities: Poison; Charmed",
        "Claw rider: If the target is a creature that isn’t an Undead or elf, it is subjected "
        "to the following effect. Constitution Saving Throw: DC 10. Failure: The target has "
        "the Paralyzed condition until the end of its next turn.",
    }
    if set(row["unsupported_clauses"]) != expected_gaps or not row["attacks"]["Bite"]["supported"]:
        raise SystemExit("Ghoul has additional unowned source clauses or attacks.")
    return {**row, "source_spell_page": 121, "source_spell_page_sha256": expected_spell_page,
            "damage_immunities": ["poison"],
            "condition_immunities": ["charmed", "exhaustion", "poisoned"],
            "source_hit_dice": 5, "source_hit_die_size": 8, "languages": ["common"]}


@lru_cache(maxsize=1)
def _created_undead_additional_profiles():
    """Retain the exact additional printed bodies without borrowing form certification."""
    if __package__:
        from .build_srd_non_beast_forms import build_index
    else:
        from build_srd_non_beast_forms import build_index
    rows = build_index()["forms"]
    bindings = {
        "ghast": (287, "9ada001041b41a1e129de34eeabbecc5672dabdd2d455d08e105a66c6f2cfe86",
                  8, ["necrotic"], ["poison"], [], ["charmed", "exhaustion", "poisoned"], 0),
        "wight": (341, "d65414f9f850be8380a004e3eafaae25f8f4788687d1cebcb7596fb6cb7dd568",
                  11, ["necrotic"], ["poison"], [], ["exhaustion", "poisoned"], 1),
        "mummy": (309, "f0308dcb8736fc921617097ec97d466c7b2183ec039272f305f8d8f93fa2ec26",
                  9, [], ["necrotic", "poison"], ["fire"],
                  ["charmed", "exhaustion", "frightened", "paralyzed", "poisoned"], 2),
    }
    result = {}
    page_hashes = {
        287: "94b422a925ff687b6b29cc881e897c5bdb90276d0fdb69905711240050e0e312",
        341: "2286d2281200be5ddd068c74203241781d3a4ce3e3e1813c58a9803dfb613025",
        309: "701b787fccc7867b0061f2e762360f88609e28420c959a97e74775fccd10646b",
    }
    for form, (page, block_hash, hit_dice, resistances, immunities, vulnerabilities,
               conditions, extra_languages) in bindings.items():
        row = rows[form]
        if row["source"]["page"] != page or row["source_stat_block_sha256"] != block_hash or \
                row["source"]["page_text_sha256"] != page_hashes[page]:
            raise SystemExit("Review the changed complete " + row["name"] + " source.")
        if row["size_rank"] != 2 or row["other_speeds"] or any(
            key not in row for key in ("initiative_modifier", "ability_scores",
                                      "saving_throw_modifiers", "skills", "senses")
        ):
            raise SystemExit("The selected source body has unqualified fixed statistics.")
        result[form] = {**row, "damage_resistances": resistances,
                       "damage_immunities": immunities, "damage_vulnerabilities": vulnerabilities,
                       "condition_immunities": conditions, "source_hit_dice": hit_dice,
                       "source_hit_die_size": 8, "languages": ["common"],
                       "source_other_language_count": extra_languages}
    return result


def _ghoul_actor_tools(tools, form_id="ghoul"):
    """Reuse the corpse/NPC installer with one closed, exact printed source body."""
    row = (_created_undead_source_profile() if form_id == "ghoul" else
           _created_undead_additional_profiles()[form_id])
    owned = _zombie_actor_tools(tools)
    prepare = owned["prepare_srd_source_zombie"]
    finish = owned["finish_srd_zombie_source_actor"]
    prepare["description"] = (
        "Install " + row["name"] + "'s exact printed body from one actual Humanoid corpse.")
    prepare["parameters"] = [p for p in prepare["parameters"] if p["name"] != "other_language"]
    prepare["pre_conditions"] = [p for p in prepare["pre_conditions"]
                                 if "other_language" not in p["condition"]]
    if form_id != "ghoul":
        prepare["parameters"].append(_optional("other_languages", "array", [],
            items={"type": "string"}, unique_items=True, max_items=2))
        prepare["pre_conditions"].append({
            "condition": "len(other_languages) == " + str(row["source_other_language_count"]),
            "error": "Admit exactly this printed body's other spoken languages.",
        })
    fixed = {
        "name": repr(row["name"]),
        "source": repr("SRD5.2.1 " + row["name"] + " p" + str(row["source"]["page"])),
        "armor_class": row["armor_class"], "hit_points": row["hit_points"],
        "speed": row["walk_speed"], "initiative_modifier": row["initiative_modifier"],
        "challenge_rating_eighths": row["challenge_rating_eighths"],
        "damage_immunities": row["damage_immunities"],
        "damage_resistances": row.get("damage_resistances", []),
        "damage_vulnerabilities": row.get("damage_vulnerabilities", []),
        "multiattack": [{"attack": repr(r["attack"]), "count": r["count"]}
                        for r in row["multiattack"]],
        **{a + "_score": value for a, value in row["ability_scores"].items()},
        **{a + "_save": value for a, value in row["saving_throw_modifiers"].items()},
    }
    if form_id == "wight":
        fixed["inventory"] = ["'Studded Leather Armor'"]
        fixed["equipment"] = ["'Studded Leather Armor'"]
        fixed["skill_proficiencies"] = [repr(skill) for skill in row["skills"]]
    if form_id == "mummy":
        fixed["size_rank"] = "corpse_state.size_rank"
    prepare["effects"] = [e for e in prepare["effects"]
                          if "Admit the printed other understood language" not in str(e)]
    for effect in _walk(prepare["effects"]):
        setting = effect.get("set", {})
        field = setting.get("field", "")
        if field.startswith("_zombie_") and field[8:] in fixed:
            setting["value"] = deepcopy(fixed[field[8:]])
        if effect.get("call") == "finish_srd_zombie_source_actor":
            effect["call"] = "finish_srd_" + form_id + "_source_actor"
            effect["params"].pop("other_language")
            if form_id != "ghoul":
                effect["params"]["other_languages"] = "{other_languages}"
    finish["description"] = (
        "Retain " + row["name"] + "'s permanent actual body and original consumed corpse.")
    finish["parameters"] = [p for p in finish["parameters"] if p["name"] != "other_language"]
    if form_id != "ghoul":
        finish["parameters"].append(_optional("other_languages", "array", [],
                                             items={"type": "string"}, max_items=2))
    first_attack = next(iter(row["attacks"]))
    replacements = {
        "actor_state.created_spell_id": "'create_undead'",
        "actor_state.created_form_id": repr(form_id),
        "actor_state.created_source_attack": {
            "name": repr(first_attack), **{k: repr(v) if isinstance(v, str) else v
                                  for k, v in row["attacks"][first_attack].items() if k != "name"},
        },
        "actor_state.created_source_block_sha256": repr(row["source_stat_block_sha256"]),
        "actor_state.condition_immunities": row["condition_immunities"],
        "actor_state.passive_perception": row["passive_perception"],
        "actor_state.languages": ["'common'"],
        "actor_state.created_understood_languages": ["'common'"],
        "actor_state.resources.hit_die": row["source_hit_dice"],
        "actor_state.resource_maxima": {"hit_die": row["source_hit_dice"]},
        "actor_state.constitution_modifier": (row["ability_scores"]["constitution"] - 10) // 2,
    }
    for effect in _walk(finish["effects"]):
        setting = effect.get("set", {})
        if setting.get("field") in replacements:
            setting["value"] = deepcopy(replacements[setting["field"]])
        if setting.get("field") == "state.created_actor_records.{_actor_state_id}" or \
                setting.get("field") == "lasting_state.created_actor_records.{_actor_state_id}":
            setting["value"]["spell"] = "'create_undead'"
            setting["value"]["source_block_sha256"] = repr(row["source_stat_block_sha256"])
    if form_id != "ghoul":
        prepare["effects"].insert(0, _loop("other_languages", "_undead_other_language", [
            _require("len(_undead_other_language) >= 1 and len(_undead_other_language) <= 80 "
                     "and _undead_other_language != 'common'",
                     "Use distinct actual other language names, not Common again."),
        ]))
        finish["effects"] += [
            _set("_undead_languages", "actor_state.languages"),
            _loop("other_languages", "_undead_other_language", [
                _push("_undead_languages", "_undead_other_language"),
            ]),
            _set("actor_state.languages", "_undead_languages"),
            _set("actor_state.created_understood_languages", "_undead_languages"),
        ]
        finish["effects"].append(_set("actor_state.created_source_attacks", {
            name: {key: repr(value) if isinstance(value, str) else value
                   for key, value in profile.items()} for name, profile in row["attacks"].items()
        }))
    if form_id == "ghast":
        finish["effects"].append(_when("state.created_ghast_ids == null", [
            _set("state.created_ghast_ids", []),
        ]))
        finish["effects"] += [
            _set("_created_ghasts", "state.created_ghast_ids"),
            _push("_created_ghasts", "_actor_state_id"),
            _set("state.created_ghast_ids", "_created_ghasts"),
        ]
        finish["effects"].append(_set("state.has_created_ghasts", True))
    if form_id == "wight":
        finish["effects"].append(_set("actor_state.worn_armor", {
            "inventory_item": "'Studded Leather Armor'", "armor_type": "'studded_leather_armor'",
            "magic_bonus": 0,
        }))
        finish["effects"].append(_set("actor_state.created_source_armor", {
            "block_sha256": repr(row["source_stat_block_sha256"]),
            "inventory_item": "'Studded Leather Armor'", "armor_type": "'studded_leather_armor'",
        }))
    return {"prepare_srd_source_" + form_id: prepare,
            "finish_srd_" + form_id + "_source_actor": finish}


def _zombie_actor_tools(tools):
    """Consume one actual eligible corpse while the existing NPC owner installs its source body."""
    row = _zombie_source_profile()
    prepare = deepcopy(tools["prepare_srd_creature"])
    original_parameters = prepare["parameters"]
    prepare["description"] = (
        "Install Zombie's exact printed body from an unconsumed Humanoid corpse."
    )
    prepare["metadata"] = {"category": "internal", "visible_to_player": False,
                           "costs_action": False}
    prepare["parameters"] = [
        _parameter("creature_id"), _entity("corpse_state"), _entity("source_caster"),
        _parameter("other_language"),
        _optional("lasting_state", resolve="entity", default="state"),
    ]
    prepare["pre_conditions"] = [
        p for p in prepare["pre_conditions"] if p["condition"] ==
        "combat.encounter_active != true and combat.turn_active != true"
    ]
    prepare["pre_conditions"].append({
        "condition": "len(other_language) >= 1 and len(other_language) <= 80",
        "error": "Admit the other understood language in 1 to 80 characters.",
    })
    fixed = {
        "name": "Zombie", "source": "SRD5.2.1 Zombie pp343–344",
        "creature_type": "undead", "armor_class": row["armor_class"],
        "hit_points": row["hit_points"], "speed": row["walk_speed"],
        "size_rank": row["size_rank"], "initiative_modifier": row["initiative_modifier"],
        "challenge_rating_eighths": row["challenge_rating_eighths"], "proficiency_bonus": 2,
        "damage_immunities": row["damage_immunities"],
        "melee_reach": row["attacks"]["Slam"]["normal_range"],
        **{a + "_score": value for a, value in row["ability_scores"].items()},
        **{a + "_save": value for a, value in row["saving_throw_modifiers"].items()},
    }
    prefix = [
        _require("corpse_state.is_alive == false and corpse_state.death_tick != null",
                 "Animate only a recorded actual death, never pocket absence."),
        _require("corpse_state.body_available == true and "
                 "corpse_state.body_animated_actor_id == null",
                 "The actual corpse is unavailable or already belongs to an animated body."),
        _require("corpse_state.death_creature_type == 'humanoid' and "
                 "corpse_state.death_was_undead != true and "
                 "(corpse_state.size_rank == 1 or corpse_state.size_rank == 2)",
                 "Animate Dead requires a Small or Medium Humanoid corpse."),
        _require("other_language != 'common'", "Admit the printed other understood language."),
    ]
    removed = {p["name"] for p in original_parameters if p["name"] != "creature_id"}
    for parameter in original_parameters:
        name = parameter["name"]
        if name == "creature_id":
            continue
        value = fixed.get(name, deepcopy(parameter.get("default")))
        if isinstance(value, str):
            value = repr(value)
        prefix.append(_set(name, value))
    pattern = re.compile(
        r"(?<![.\w])(" + "|".join(sorted(removed, key=len, reverse=True)) + r")(?!\w)"
    )

    def localize(value):
        if isinstance(value, str):
            return pattern.sub(lambda match: "_zombie_" + match.group(), value)
        if isinstance(value, list):
            return [localize(item) for item in value]
        if isinstance(value, dict):
            return {key: localize(item) for key, item in value.items()}
        return value

    prepare["effects"] = localize(prefix + prepare["effects"]) + [
        _call("finish_srd_zombie_source_actor", {
            "actor_state": "{creature_id}", "corpse_state": "{_corpse_state_id}",
            "caster_state": "{_source_caster_id}", "other_language": "{other_language}",
        }),
    ]
    finish = _tool("Retain the new body and the consumed corpse's original provenance.", [
        _entity("actor_state"), _entity("corpse_state"), _entity("caster_state"),
        _parameter("other_language"),
    ], [
        _when("state.created_actor_ids == null", [
            _set("state.created_actor_ids", []), _set("state.created_actor_records", {}),
            _set("state.created_actor_dropped_items", []),
        ]),
        _set("_zombie_created_ids", "state.created_actor_ids"),
        _push("_zombie_created_ids", "_actor_state_id"),
        _set("state.created_actor_ids", "_zombie_created_ids"),
        _set("actor_state.created_spell_id", "'animate_dead'"),
        _set("actor_state.created_caster_state_id", "_caster_state_id"),
        _set("actor_state.created_individual_id", "_actor_state_id"),
        _set("actor_state.created_active", True),
        _set("actor_state.created_form_id", "'zombie'"),
        _set("actor_state.animated_control_until", "state.lasting_tick + 28800"),
        _set("actor_state.animated_control_active", True),
        _set("actor_state.animated_order", None),
        _set("actor_state.created_source_attack", {
            "name": "'Slam'", "attack_modifier": row["attacks"]["Slam"]["attack_modifier"],
            "damage_expression": repr(row["attacks"]["Slam"]["damage_expression"]),
            "damage_modifier": row["attacks"]["Slam"]["damage_modifier"],
            "damage_type": repr(row["attacks"]["Slam"]["damage_type"]),
            "reach_feet": row["attacks"]["Slam"]["normal_range"],
        }),
        _set("actor_state.created_source_block_sha256", repr(row["source_stat_block_sha256"])),
        _set("actor_state.created_body_source_state_id", "_corpse_state_id"),
        _set("_zombie_physical_body_source", "_corpse_state_id"),
        _when("corpse_state.current_body_source_id != null", [
            _set("_zombie_physical_body_source", "corpse_state.current_body_source_id"),
        ]),
        _set("actor_state.created_body_source_id", "_zombie_physical_body_source"),
        _set("actor_state.condition_immunities", row["condition_immunities"]),
        _set("actor_state.darkvision_feet", row["senses"]["darkvision_feet"]),
        _set("actor_state.passive_perception", row["passive_perception"]),
        _set("actor_state.languages", []),
        _set("actor_state.created_understood_languages", ["'common'", "other_language"]),
        _set("actor_state.resources.hit_die", row["source_hit_dice"]),
        _set("actor_state.resource_maxima", {"hit_die": row["source_hit_dice"]}),
        _set("actor_state.hit_die_size", row["source_hit_die_size"]),
        _set("actor_state.constitution_modifier",
             (row["ability_scores"]["constitution"] - 10) // 2),
        _set("actor_state.death_tick", None), _set("actor_state.body_available", True),
        _set("actor_state.vital_parts_present", True),
        _set("corpse_state.body_available", False),
        _set("corpse_state.body_animated_actor_id", "_actor_state_id"),
        _set("state.created_actor_records.{_actor_state_id}", {
            "caster": "_caster_state_id", "spell": "'animate_dead'",
            "individual": "_actor_state_id",
            "active": True, "disappearance": "''", "body_source": "_corpse_state_id",
            "body_source_id": "_zombie_physical_body_source",
            "source_block_sha256": repr(row["source_stat_block_sha256"]),
        }),
        _set("state.has_created_actors", True),
        _when("state.animated_actor_ids == null", [_set("state.animated_actor_ids", [])]),
        _set("_zombie_animated_ids", "state.animated_actor_ids"),
        _push("_zombie_animated_ids", "_actor_state_id"),
        _set("state.animated_actor_ids", "_zombie_animated_ids"),
        _set("state.has_animated_undead", True),
    ])
    return {"prepare_srd_source_zombie": prepare, "finish_srd_zombie_source_actor": finish}


def _undead_fortitude_tools():
    """Save only the actual source Zombie at its real pre-death zero-HP boundary."""
    row = _zombie_source_profile()
    return {"resolve_srd_created_undead_fortitude": _tool(
        "Resolve printed Undead Fortitude from the actual source body and typed lethal packet.",
        [_entity("target_state"), _parameter("damage_taken", "integer", minimum=1),
         _parameter("radiant_damage", "boolean"), _parameter("critical_hit", "boolean")],
        [
            _require("target_state.hp == 0 and target_state.is_alive == true and "
                     "target_state.created_spell_id == 'animate_dead' and "
                     "target_state.created_form_id == 'zombie' and "
                     "target_state.form_statistics_replaced != true",
                     "Undead Fortitude requires this actual native Zombie before death."),
            _require("target_state.created_individual_id == _target_state_id and "
                     "target_state.created_source_block_sha256 == " +
                     repr(row["source_stat_block_sha256"]),
                     "Undead Fortitude must retain the complete printed Zombie source."),
            _set("_undead_record", "state.created_actor_records[_target_state_id]"),
            _require("_undead_record.active == true and target_state.created_active == true and "
                     "_undead_record.spell == 'animate_dead' and "
                     "_undead_record.individual == _target_state_id and "
                     "_undead_record.caster == target_state.created_caster_state_id and "
                     "_undead_record.body_source == target_state.created_body_source_state_id and "
                     "_undead_record.source_block_sha256 == "
                     "target_state.created_source_block_sha256",
                     "The actual animated body must retain its original source identity."),
            _set("_undead_fortitude_dc", "5 + damage_taken"),
            _set("_undead_fortitude_success", False),
            _when("radiant_damage != true and critical_hit != true", [
                _call("resolve_srd_divination_save", {
                    "target_state": "{_target_state_id}", "ability": "constitution",
                    "dc": "{_undead_fortitude_dc}",
                }, ("_divination_save_success", "_d20_test", "_adjusted_total")),
                _set("_undead_fortitude_success", "_divination_save_success"),
                _when("_undead_fortitude_success == true", [_set("target_state.hp", 1)]),
            ]),
            _set("target_state.last_undead_fortitude", {
                "damage_taken": "damage_taken", "dc": "_undead_fortitude_dc",
                "radiant": "radiant_damage", "critical": "critical_hit",
                "success": "_undead_fortitude_success", "hit_points_after": "target_state.hp",
            }),
        ],
    )}


def bind_undead_fortitude_damage_consumers(text):
    """Bind final damage owners without adding work to ordinary surviving packets."""
    tools = _load_runtime(text)["tools"]
    changed = _undead_fortitude_tools()

    def effect_lists(effects):
        yield effects
        for effect in effects:
            for key in ("on_true", "on_false"):
                yield from effect_lists(effect.get(key, []))
            yield from effect_lists(effect.get("for_each", {}).get("do", []))

    for name in ("apply_srd_damage", "apply_srd_composite_damage",
                 "apply_srd_qualifying_damage", "apply_srd_qualifying_composite_damage"):
        if name not in tools:
            continue
        tool = deepcopy(tools[name])
        lethal = next(e for e in tool["effects"] if e.get("condition") ==
                      "target.hp == 0 and target.creature_kind == 'monster'")
        radiant_prefix = []
        radiant = "damage_type == 'radiant' and _effective_damage > 0"
        if name.endswith("composite_damage"):
            radiant = " or ".join(
                f"(component_{index}_damage_type == 'radiant' and "
                f"_component_{index}_effective_damage > 0)" for index in (1, 2)
            )
        elif name == "apply_srd_qualifying_damage":
            receipt_owners = [
                effect for effect in tool["effects"] if effect.get("condition") and any(
                    row.get("set", {}).get("field") == "_source_base_effective_damage"
                    for row in _walk(effect.get("on_true", []))
                )
            ]
            if len(receipt_owners) != 1:
                raise ValueError("Qualified scalar Fortitude needs its actual typed base receipt.")
            radiant_prefix = [
                _set("_undead_base_effective_damage", "_effective_damage"),
                _when(receipt_owners[0]["condition"], [
                    _set("_undead_base_effective_damage", "_source_base_effective_damage"),
                ]),
            ]
            radiant = "damage_type == 'radiant' and _undead_base_effective_damage > 0"
        existing = [
            (effects, index, effect)
            for effects in effect_lists(lethal["on_true"])
            for index, effect in enumerate(effects)
            if effect.get("call") == "resolve_srd_created_undead_fortitude"
        ]
        if existing:
            if len(existing) != 1:
                raise ValueError("One actual lethal packet must own one Fortitude resolution.")
            effects, index, callback = existing[0]
            argument = callback["params"]["radiant_damage"]
            bound_argument = next((
                (i, effect["set"]) for i, effect in enumerate(effects[:index])
                if effect.get("set", {}).get("field") == argument.strip("{}")
            ), None)
            current_radiant = (bound_argument[1]["value"] if bound_argument else
                               argument.strip("{}"))
            if current_radiant == radiant:
                continue
            if bound_argument:
                bound_argument[1]["value"] = radiant
                effects[bound_argument[0]:bound_argument[0]] = radiant_prefix
            else:
                effects[index:index] = radiant_prefix
                callback["params"]["radiant_damage"] = "{" + radiant + "}"
            changed[name] = tool
            continue
        original_death = lethal["on_true"]
        lethal["on_true"] = [
            _when("_hp_before > 0 and _effective_damage > 0 and target.is_alive == true and "
                  "target.created_spell_id == 'animate_dead' and "
                  "target.created_form_id == 'zombie' and "
                  "target.form_statistics_replaced != true", [
                *radiant_prefix,
                _call("resolve_srd_created_undead_fortitude", {
                    "target_state": "{target}", "damage_taken": "{_effective_damage}",
                    "radiant_damage": "{" + radiant + "}", "critical_hit": "{critical_hit}",
                }),
            ]),
            _when("target.hp == 0", original_death),
        ]
        changed[name] = tool
    _normalize(changed)
    return _replace_tools(text, changed)


def _created_world_time_tools():
    """Admit one categorical world fact at the actual map/plane/current clock boundary."""
    record = _public(_tool_scene(
        "Record this admitted map plane's current day/night fact independently of any spell.", [
            _parameter("plane"),
            _parameter("phase", "enum", enum_values=["day", "night"]),
        ], [
            *_snapshot(),
            _require("len(plane) >= 1 and len(plane) <= 80",
                     "Use the actual admitted plane identifier in 1 to 80 characters."),
            _require("state.lasting_tick != null",
                     "Initialize the actual world clock before admitting its time-of-day fact."),
            _set("_world_time_map", "state.spatial_scene.map.id"),
            _set("_world_time_plane_found", False),
            _set("_world_time_scene_rows", "state.spatial_scene.entities"),
            _loop("_world_time_scene_rows", "_world_time_scene_row", [
                _when("_world_time_scene_row.active == true and "
                      "_world_time_scene_row.map_id == _world_time_map and "
                      "_world_time_scene_row.metadata.plane == plane", [
                    _set("_world_time_plane_found", True),
                ]),
            ]),
            _require("_world_time_plane_found == true",
                     "Admit time only for a plane actually present in this admitted map."),
            _when("state.map_time_of_day_records == null", [
                _set("state.map_time_of_day_records", []),
            ]),
            _set("_world_time_rows", "state.map_time_of_day_records"),
            _require("len(_world_time_rows) <= 64", "Keep at most 64 admitted map/plane facts."),
            _set("_world_time_kept", []),
            _loop("_world_time_rows", "_world_time_row", [
                _when("_world_time_row.map_id != _world_time_map or "
                      "_world_time_row.plane != plane", [
                    _push("_world_time_kept", "_world_time_row"),
                ]),
            ]),
            _require("len(_world_time_kept) < 64", "Keep at most 64 admitted map/plane facts."),
            _push("_world_time_kept", {"map_id": "_world_time_map", "plane": "plane",
                                       "phase": "phase", "tick": "state.lasting_tick"}),
            _set("state.map_time_of_day_records", "_world_time_kept"),
            _set("_world_time_plane", "plane"), _set("_world_time_phase", "phase"),
            _set("_world_time_tick", "state.lasting_tick"),
        ]), "world", "Map time of day", [
            ("Map", "_world_time_map"), ("Plane", "_world_time_plane"),
            ("Time of day", "_world_time_phase"), ("World tick", "_world_time_tick"),
        ])
    read = _tool_scene("Read the fresh world fact through this caster's actual map and plane.", [
        _entity("caster_state"),
    ], [
        *_scene_position("{_caster_state_id}", "_world_time_caster_origin"),
        _set("_world_time_map", "state.spatial_scene.map.id"),
        _set("_world_time_plane", "_info_scene_entity.metadata.plane"),
        _require("_info_scene_entity.map_id == _world_time_map and "
                 "_world_time_plane != null and state.lasting_tick != null",
                 "Use the caster's actual admitted map, plane and initialized world clock."),
        _set("_world_time_rows", []),
        _when("state.map_time_of_day_records != null", [
            _set("_world_time_rows", "state.map_time_of_day_records"),
        ]),
        _require("len(_world_time_rows) <= 64", "Keep at most 64 admitted map/plane facts."),
        _set("_world_time_fact", None),
        _loop("_world_time_rows", "_world_time_row", [
            _when("_world_time_row.map_id == _world_time_map and "
                  "_world_time_row.plane == _world_time_plane", [
                _require("_world_time_fact == null", "The map/plane world fact is ambiguous."),
                _set("_world_time_fact", "_world_time_row"),
            ]),
        ]),
        _require("_world_time_fact != null", "Admit this map plane's actual day/night world fact."),
        _require("_world_time_fact.tick == state.lasting_tick",
                 "The world clock changed; admit a current day/night fact before using it."),
        _require("_world_time_fact.phase == 'day' or _world_time_fact.phase == 'night'",
                 "The world fact must retain its actual day/night category."),
        _set("_world_time_phase", "_world_time_fact.phase"),
        _set("_world_time_tick", "_world_time_fact.tick"),
    ])
    night = _tool("Require printed Create Undead night through an actual fresh world fact.", [
        _entity("caster_state"),
    ], [
        _call("read_srd_map_time_of_day", {"caster_state": "{_caster_state_id}"},
              ("_world_time_phase", "_world_time_tick", "_world_time_map", "_world_time_plane")),
        _require("_world_time_phase == 'night'", "Create Undead can be cast only at night."),
    ])
    return {"record_srd_map_time_of_day": record, "read_srd_map_time_of_day": read,
            "require_srd_created_undead_night": night}


def _animated_actor_tools():
    """Create at the real corpse space or renew only this caster's still-controlled individuals."""
    _validate_animate_dead_source()
    refresh = _tool("Expire mental control without dismissing or killing the permanent Undead.", [
        _entity("actor_state"),
    ], [
        _when("actor_state.animated_control_active == true and "
              "state.lasting_tick >= actor_state.animated_control_until", [
            _set("actor_state.animated_control_active", False),
            _set("actor_state.animated_order", None),
            _set("actor_state.animated_turn_order", None),
        ]),
        _set("_animated_control_active", "actor_state.animated_control_active"),
    ])
    all_control = _tool("Refresh saved animated bodies through their actual actors.", [], [
        _set("_animated_current_ids", "state.animated_actor_ids"),
        _set("_animated_controlled_ids", []),
        _loop("_animated_current_ids", "_animated_current_id", [
            _call("refresh_srd_animated_undead_control", {"actor_state": "{_animated_current_id}"},
                  ("_animated_control_active",)),
            _when("_animated_control_active == true", [
                _push("_animated_controlled_ids", "_animated_current_id"),
            ]),
        ]),
        _set("state.animated_actor_ids", "_animated_controlled_ids"),
        _set("state.has_animated_undead", "len(_animated_controlled_ids) > 0"),
    ])
    qualify = _tool("Renew only an actual owned Undead before its existing control expires.", [
        _entity("actor_state"), _entity("caster_state"),
    ], [
        _call("refresh_srd_animated_undead_control", {"actor_state": "{_actor_state_id}"}),
        _require("actor_state.created_spell_id == 'animate_dead' and "
                 "actor_state.created_caster_state_id == _caster_state_id and "
                 "actor_state.created_individual_id == _actor_state_id and "
                 "actor_state.created_active == true and actor_state.is_alive == true and "
                 "actor_state.hp > 0 and actor_state.animated_control_active == true",
                 "Reassert only your actual living animated individual before control expires."),
        _set("_animated_record", "state.created_actor_records[_actor_state_id]"),
        _require("_animated_record.caster == _caster_state_id and "
                 "_animated_record.individual == _actor_state_id and "
                 "_animated_record.spell == 'animate_dead'",
                 "Reassert the original caster and individual, not an unrelated actor."),
    ])
    renew = _tool_scene("Reassert this saved individual within the actual spell range and plane.", [
        _entity("actor_state"), _entity("caster_state"),
    ], [
        _call("qualify_srd_animated_undead_control", {
            "actor_state": "{_actor_state_id}", "caster_state": "{_caster_state_id}",
        }),
        *_scene_position("{_caster_state_id}", "_animated_renew_caster_origin"),
        _set("_animated_renew_plane", "_info_scene_entity.metadata.plane"),
        *_scene_position("{_actor_state_id}", "_animated_renew_origin"),
        _require("_animated_renew_plane != null and "
                 "_info_scene_entity.metadata.plane == _animated_renew_plane",
                 "Reassert control only within the same actual plane."),
        _call("measure_srd_information_distance", {
            "start": "{_animated_renew_caster_origin}", "end": "{_animated_renew_origin}",
        }, MEASURE),
        _require("_scene_distance <= 10", "Reassert control only within Animate Dead's range."),
        _call("srd_information_barriers", {
            "start": "{_animated_renew_caster_origin}", "end": "{_animated_renew_origin}",
            "rule": "solid",
        }, ("_information_blocked",)),
        _require("_information_blocked != true", "Reassert control requires a clear spell path."),
        _set("actor_state.animated_control_until", "state.lasting_tick + 28800"),
        _set("actor_state.animated_control_active", True),
    ])
    create = _tool_scene("Transform the actual available corpse at its source scene position.", [
        _entity("corpse_state"), _entity("caster_state"), _parameter("individual_id"),
        _parameter("other_language"), _parameter("appearance_height", "number"),
    ], [
        _require("corpse_state.is_alive == false and corpse_state.death_tick != null and "
                 "corpse_state.body_available == true and "
                 "corpse_state.body_animated_actor_id == null",
                 "The selected corpse must be actually dead, available and unconsumed."),
        _require("corpse_state.form_statistics_replaced != true and "
                 "(corpse_state.inventory == null or len(corpse_state.inventory) == 0) and "
                 "(corpse_state.equipment == null or len(corpse_state.equipment) == 0)",
                 "Transformed corpses and carried/worn corpse gear remain unsupported."),
        *_scene_position("{_corpse_state_id}", "_animated_corpse_origin"),
        _set("_animated_corpse_scene", "_info_scene_entity"),
        _require("_animated_corpse_scene.metadata.participant_ref == _corpse_state_id and "
                 "_animated_corpse_scene.footprint.kind == 'box' and "
                 "_animated_corpse_scene.metadata.creature_type == 'humanoid'",
                 "Select the actual admitted Humanoid corpse's unambiguous box body."),
        *_scene_position("{_caster_state_id}", "_animated_caster_origin"),
        _require("_info_scene_entity.metadata.plane != null and "
                 "_info_scene_entity.metadata.plane == _animated_corpse_scene.metadata.plane",
                 "Animate only the actual corpse within your plane."),
        _call("update_srd_spell_force_scene", {
            "force_id": "{_animated_corpse_scene.id}", "position": "{_animated_corpse_origin}",
            "active": False,
        }),
        _call("create_srd_source_actor_scene", {
            "caster_state": "{_caster_state_id}", "summon_id": "{individual_id}",
            "spell": "animate_dead", "range_feet": 10, "half_extent": 2.5,
            "creature_type": "undead", "requires_sight": False,
            "created_actor_position": "{_animated_corpse_origin}",
            "created_actor_height": "{appearance_height}",
            "created_actor_lighting": "{_animated_corpse_scene.metadata.lighting}",
        }),
        _call("prepare_srd_source_zombie", {
            "creature_id": "{individual_id}", "corpse_state": "{_corpse_state_id}",
            "source_caster": "{_caster_state_id}", "other_language": "{other_language}",
        }),
    ])
    cast = _tool("Resolve Animate Dead's selected source bodies or genuine saved renewals.", [
        _entity("caster_state"), _parameter("slot_level", "integer", minimum=3, maximum=9),
        *deepcopy(ANIMATED_CAST_CHOICES),
    ], [
        _require("combat.encounter_active != true",
                 "Adding independent Undead Initiative during existing combat is unsupported."),
        _lookup("'animate_dead'", "_animated_profile"),
        _set("_animated_maximum", "_animated_profile.base_creation_count + "
             "(slot_level - 3) * _animated_profile.extra_per_level"),
        _set("_animated_body_choices", "animated_bodies"),
        _set("_animated_renew_choices", "animated_actor_ids"),
        _choose("animated_mode == 'create'", [
            _require("len(_animated_renew_choices) == 0 and len(_animated_body_choices) > 0 and "
                     "len(_animated_body_choices) <= _animated_maximum",
                     "Select only the printed number of distinct corpses for this cast."),
            _set("_animated_selected_corpses", []), _set("_animated_selected_ids", []),
            _loop("_animated_body_choices", "_animated_body_choice", [
                _require("_animated_body_choice.corpse_state_id not in _animated_selected_corpses "
                         "and _animated_body_choice.individual_id not in _animated_selected_ids",
                         "Each genuine new individual requires a different corpse."),
                _push("_animated_selected_corpses", "_animated_body_choice.corpse_state_id"),
                _push("_animated_selected_ids", "_animated_body_choice.individual_id"),
                _call("create_srd_animated_zombie_body", {
                    "corpse_state": "{_animated_body_choice.corpse_state_id}",
                    "caster_state": "{_caster_state_id}",
                    "individual_id": "{_animated_body_choice.individual_id}",
                    "other_language": "{_animated_body_choice.other_language}",
                    "appearance_height": "{_animated_body_choice.appearance_height}",
                }),
            ]),
            _set("_animated_resolved_count", "len(_animated_body_choices)"),
        ], [
            _set("_animated_maximum", "_animated_profile.base_reassert_count + "
                 "(slot_level - 3) * _animated_profile.extra_per_level"),
            _require("len(_animated_body_choices) == 0 and len(_animated_renew_choices) > 0 and "
                     "len(_animated_renew_choices) <= _animated_maximum",
                     "Select only the printed number of original controlled Undead for renewal."),
            _loop("_animated_renew_choices", "_animated_renew_id", [
                _call("reassert_srd_animated_undead_control", {
                    "actor_state": "{_animated_renew_id}", "caster_state": "{_caster_state_id}",
                }),
            ]),
            _set("_animated_resolved_count", "len(_animated_renew_choices)"),
        ]),
        _set("_spell_summary_rows", [{"label": "Spell", "value": "'animate_dead'"},
             {"label": "Undead individuals", "value": "_animated_resolved_count"}]),
        _set("_spell_detail_rows", [{"label": "Source action", "value": "animated_mode"},
             {"label": "Undead individuals", "value": "_animated_resolved_count"},
             {"label": "Control until tick", "value": "state.lasting_tick + 28800"}]),
    ])
    return {"refresh_srd_animated_undead_control": refresh,
            "refresh_srd_all_animated_undead_control": all_control,
            "qualify_srd_animated_undead_control": qualify,
            "reassert_srd_animated_undead_control": renew,
            "create_srd_animated_zombie_body": create, "cast_spell_animated_undead_creature": cast}


def _created_undead_tools(tools):
    """Transform eligible bodies or renew the exact selected printed Undead individuals."""
    _created_undead_source_profile()
    base = _animated_actor_tools()
    create = deepcopy(base["create_srd_animated_zombie_body"])
    create["description"] = "Create a source Ghoul at the actual eligible Humanoid corpse's space."
    create["parameters"] = [p for p in create["parameters"] if p["name"] != "other_language"]
    for effect in _walk(create["effects"]):
        if effect.get("call") == "create_srd_source_actor_scene":
            effect["params"]["spell"] = "create_undead"
        if effect.get("call") == "prepare_srd_source_zombie":
            effect["call"] = "prepare_srd_source_ghoul"
            effect["params"].pop("other_language")
    renew = deepcopy(base["reassert_srd_animated_undead_control"])
    renew["description"] = "Renew this caster's existing Create Undead individual within range."
    renew["parameters"].append(_optional("form_id", "enum", "ghoul",
        enum_values=["ghoul", "ghast", "wight", "mummy"]))
    renew["effects"].insert(0, _require("actor_state.created_spell_id == 'create_undead' and "
        "actor_state.created_form_id == form_id",
        "Reassert this spell's exact selected original body, never an Animate Dead body."))
    creators = {"create_srd_created_ghoul_body": create}
    create_calls = {"ghoul": _call("create_srd_created_ghoul_body", {
        "corpse_state": "{_created_undead_body.corpse_state_id}",
        "caster_state": "{_caster_state_id}",
        "individual_id": "{_created_undead_body.individual_id}",
        "appearance_height": "{_created_undead_body.appearance_height}",
    })}
    for form_id in ("ghast",):
        body = deepcopy(create)
        body["description"] = (
            "Create this exact printed " + form_id + " at the eligible corpse space.")
        body["parameters"].append(_optional("other_languages", "array", [],
                                             items={"type": "string"}, max_items=2))
        for effect in _walk(body["effects"]):
            if effect.get("call") == "prepare_srd_source_ghoul":
                effect["call"] = "prepare_srd_source_" + form_id
                effect["params"]["other_languages"] = "{other_languages}"
        creators["create_srd_created_" + form_id + "_body"] = body
        create_calls[form_id] = deepcopy(create_calls["ghoul"])
        create_calls[form_id]["call"] = "create_srd_created_" + form_id + "_body"
        create_calls[form_id]["params"]["other_languages"] = "{_created_undead_body_languages}"
    dispatch = [_choose("created_undead_form == 'ghoul'",
                        [create_calls["ghoul"]], [create_calls["ghast"]])]
    cast = _tool("Resolve source Ghouls at night with actual selected corpses or owned renewals.", [
        _entity("caster_state"), _parameter("slot_level", "integer", minimum=6, maximum=9),
        *deepcopy(CREATED_UNDEAD_CAST_CHOICES),
    ], [
        _require("combat.encounter_active != true",
                 "Adding independent Undead Initiative during existing combat is unsupported."),
        _call("require_srd_created_undead_night", {"caster_state": "{_caster_state_id}"}),
        _set("_created_undead_maximum", "3 + slot_level - 6"),
        _when("created_undead_form == 'ghast' or created_undead_form == 'wight'", [
            _require("slot_level >= 8", "Ghast and Wight bodies require a level8 or9 slot."),
            _set("_created_undead_maximum", "slot_level - 6"),
        ]),
        _when("created_undead_form == 'mummy'", [
            _require("slot_level == 9", "Mummy bodies require a level9 slot."),
            _set("_created_undead_maximum", 2),
        ]),
        _set("_created_undead_body_choices", "created_undead_bodies"),
        _set("_created_undead_renew_choices", "created_undead_actor_ids"),
        _choose("created_undead_mode == 'create'", [
            _require("len(_created_undead_renew_choices) == 0 and "
                     "len(_created_undead_body_choices) > 0 and "
                     "len(_created_undead_body_choices) <= _created_undead_maximum",
                     "Choose only this slot's printed number of distinct actual corpses."),
            _set("_created_undead_selected_corpses", []),
            _set("_created_undead_selected_ids", []),
            _loop("_created_undead_body_choices", "_created_undead_body", [
                _require("_created_undead_body.corpse_state_id not in "
                         "_created_undead_selected_corpses and "
                         "_created_undead_body.individual_id not in _created_undead_selected_ids",
                         "Each new individual requires a distinct original corpse."),
                _push("_created_undead_selected_corpses", "_created_undead_body.corpse_state_id"),
                _push("_created_undead_selected_ids", "_created_undead_body.individual_id"),
                _set("_created_undead_body_languages", []),
                _when("_created_undead_body.other_languages != null", [
                    _set("_created_undead_body_languages", "_created_undead_body.other_languages"),
                ]),
                _when("created_undead_form == 'ghoul'", [
                    _require("len(_created_undead_body_languages) == 0",
                             "A printed Ghoul speaks Common only."),
                ]),
                *dispatch,
            ]),
            _set("_created_undead_count", "len(_created_undead_body_choices)"),
        ], [
            _require("len(_created_undead_body_choices) == 0 and "
                     "len(_created_undead_renew_choices) > 0 and "
                     "len(_created_undead_renew_choices) <= _created_undead_maximum",
                     "Renew only this slot's printed number of original controlled Ghouls."),
            _loop("_created_undead_renew_choices", "_created_undead_renew_id", [
                _call("reassert_srd_created_undead_control", {
                    "actor_state": "{_created_undead_renew_id}",
                    "caster_state": "{_caster_state_id}",
                    "form_id": "{created_undead_form}",
                }),
            ]),
            _set("_created_undead_count", "len(_created_undead_renew_choices)"),
        ]),
        _set("_spell_summary_rows", [{"label": "Spell", "value": "'create_undead'"},
             {"label": "Undead individuals", "value": "_created_undead_count"}]),
        _set("_spell_detail_rows", [{"label": "Source action", "value": "created_undead_mode"},
             {"label": "Undead individuals", "value": "_created_undead_count"},
             {"label": "Printed body", "value": "created_undead_form"},
             {"label": "Control until tick", "value": "state.lasting_tick + 28800"}]),
    ])
    cast["effects"].insert(0, _require(
        "created_undead_form == 'ghoul' or created_undead_form == 'ghast'",
        "Join Wight's and Mummy's full native automatic/routine receivers before creation."))
    return {**creators,
            "reassert_srd_created_undead_control": renew,
            "cast_spell_created_undead_creature": cast}


def _created_undead_control_hooks(tools):
    """Share the actual mental-command and Dodge owners while preserving each printed source."""
    changed = {}
    qualify = deepcopy(tools["qualify_srd_animated_undead_control"])
    for effect in _walk(qualify["effects"]):
        requirement = effect.get("require", {})
        if requirement and "actor_state.created_spell_id == 'create_undead'" not in \
                requirement["condition"]:
            requirement["condition"] = requirement["condition"].replace(
                "actor_state.created_spell_id == 'animate_dead'",
                "(actor_state.created_spell_id == 'animate_dead' or "
                "actor_state.created_spell_id == 'create_undead')").replace(
                "_animated_record.spell == 'animate_dead'",
                "_animated_record.spell == actor_state.created_spell_id")
    changed["qualify_srd_animated_undead_control"] = qualify
    renew = deepcopy(tools["reassert_srd_animated_undead_control"])
    if "Renew only this spell's original body" not in str(renew["effects"]):
        renew["effects"].insert(0, _require("actor_state.created_spell_id == 'animate_dead'",
                                          "Renew only this spell's original body."))
    changed["reassert_srd_animated_undead_control"] = renew
    native_body = "((actor_state.created_spell_id == 'animate_dead' and " \
        "actor_state.created_form_id == 'zombie') or " \
        "(actor_state.created_spell_id == 'create_undead' and " \
        "(actor_state.created_form_id == 'ghoul' or actor_state.created_form_id == 'ghast' or " \
        "actor_state.created_form_id == 'wight' or actor_state.created_form_id == 'mummy')))"
    for name in ("save_srd_animated_undead_order", "start_srd_animated_undead_turn"):
        tool = deepcopy(tools[name])
        for effect in _walk(tool["effects"]):
            requirement = effect.get("require", {})
            if "actor_state.created_form_id == 'zombie'" in requirement.get("condition", ""):
                requirement["condition"] = requirement["condition"].replace(
                    "actor_state.created_form_id == 'zombie'", native_body)
                requirement["error"] = "Use this spell's actual installed native Undead body."
        changed[name] = tool
    dodge = deepcopy(tools["take_srd_animated_undead_dodge"])
    for requirement in dodge["pre_conditions"]:
        requirement["condition"] = requirement["condition"].replace(
            "actor_state.created_spell_id == 'animate_dead'",
            "(actor_state.created_spell_id == 'animate_dead' or "
            "actor_state.created_spell_id == 'create_undead')")
    changed["take_srd_animated_undead_dodge"] = dodge
    command = deepcopy(tools["command_srd_animated_undead"])
    command["metadata"]["result_card"]["title"] = {"path": "context._animated_command_title"}
    command["effects"].append(_set("_animated_command_title", "'Animate Dead mental command'"))
    command["effects"].append(_when("_animated_command_source == 'create_undead'", [
        _set("_animated_command_title", "'Create Undead mental command'"),
    ]))
    sequence = next(e["for_each"]["do"] for e in command["effects"] if "for_each" in e)
    old_range = next(e for e in sequence if e.get("require", {}).get("condition") ==
                     "_scene_distance <= 60")
    old_range["require"] = {"condition": "_scene_distance <= _animated_command_range",
                            "error": "The actual Undead must be within its printed command range: "
                            "60 feet for Animate Dead or 120 feet for Create Undead."}
    prefix = [
        _call("qualify_srd_animated_undead_control", {
            "actor_state": "{_animated_command_id}", "caster_state": "{_caster_state_id}",
        }, ("_animated_record",)),
        _when("_animated_command_source == null", [
            _set("_animated_command_source", "_animated_record.spell"),
        ]),
        _require("_animated_record.spell == _animated_command_source",
                 "One mental Bonus Action commands individuals created by the same spell."),
        _set("_animated_command_range", 60),
        _when("_animated_record.spell == 'create_undead'", [
            _set("_animated_command_range", 120),
        ]),
    ]
    if not any(e.get("set", {}).get("field") == "_animated_command_source"
               for e in command["effects"]):
        command["effects"].insert(0, _set("_animated_command_source", None))
        sequence[:0] = prefix
    changed["command_srd_animated_undead"] = command
    begin = deepcopy(tools["begin_combat_turn"])
    for effect in begin["effects"]:
        if "start_srd_animated_undead_turn" in str(effect):
            effect["condition"] = effect["condition"].replace(
                "actor.created_spell_id == 'animate_dead' and actor.created_form_id == 'zombie'",
                native_body.replace("actor_state.", "actor."))
            effect["on_true"].insert(0, _set("_animated_dodge_spell_label", "'Animate Dead'"))
            effect["on_true"].insert(1, _when("actor.created_spell_id == 'create_undead'", [
                _set("_animated_dodge_spell_label", "'Create Undead'"),
            ]))
            for child in _walk(effect["on_true"]):
                value = child.get("list_push", {}).get("value", {})
                if value.get("label") == "'Animate Dead'":
                    value["label"] = "_animated_dodge_spell_label"
    changed["begin_combat_turn"] = begin
    return changed


def _created_undead_hooks(tools):
    """Bind night and actual corpse count before the existing grant/component/cost owners."""
    changed = _ghoul_actor_tools(tools) | _created_undead_tools(tools)
    for form_id in ("ghast", "mummy"):
        changed.update(_ghoul_actor_tools(tools, form_id))
    for name in ("cast_spell", "cast_spell_node", "cast_spell_outside_combat"):
        if name not in tools:
            continue
        cast = deepcopy(tools[name])
        names = {p["name"] for p in cast["parameters"]}
        cast["parameters"] += [deepcopy(p) for p in CREATED_UNDEAD_CAST_CHOICES
                               if p["name"] not in names]
        branch = _when("_spell_definition.family == 'created_undead_creature'", [
            _call("cast_spell_created_undead_creature", {
                "caster_state": "{_caster_state_id}", "slot_level": "{_slot_level}",
                **{p["name"]: "{" + p["name"] + "}" for p in CREATED_UNDEAD_CAST_CHOICES},
            }, ("_spell_summary_rows", "_spell_detail_rows")),
        ])
        _replace_created_cast_dispatch(cast, "created_undead_creature", branch)
        cast["effects"] = [e for e in cast["effects"]
                           if "Create Undead's actual selected source count" not in str(e)]
        material = next((i for i,e in enumerate(cast["effects"])
                         if e.get("call") == "resolve_srd_spell_materials"), None)
        if material is not None:
            cast["effects"].insert(material, _when("spell == 'create_undead'", [
                _call("require_srd_created_undead_night", {"caster_state": "{_caster_state_id}"}),
                _set("_material_target_count", "len(created_undead_bodies)"),
                _when("created_undead_mode == 'reassert'", [
                    _set("_material_target_count", "len(created_undead_actor_ids)"),
                ]),
                _require("_material_target_count >= 1 and _material_target_count <= 6",
                         "Create Undead's actual selected source count determines "
                         "its onyx stones."),
            ]))
        changed[name] = cast
    for name in ("begin_srd_spell_cast", "continue_srd_spell_cast"):
        tool = deepcopy(tools[name])
        tool["effects"] = [e for e in tool["effects"]
                           if "require_srd_created_undead_night" not in str(e)]
        condition = "spell == 'create_undead'" if name.startswith("begin") else \
            "caster_state.extended_casting != null and " \
            "caster_state.extended_casting.spell == 'create_undead'"
        tool["effects"].insert(0, _when(condition, [
            _call("require_srd_created_undead_night", {"caster_state": "{_caster_state_id}"}),
        ]))
        changed[name] = tool
    creator = deepcopy(tools["create_srd_source_actor_scene"])
    for effect in _walk(creator["effects"]):
        requirement = effect.get("require", {})
        if "Only a printed source without sight-qualified" in requirement.get("error", "") and \
                "spell == 'create_undead'" not in requirement["condition"]:
            requirement["condition"] += " or spell == 'create_undead'"
    changed["create_srd_source_actor_scene"] = creator
    attack = deepcopy(tools["resolve_adjudicated_creature_attack"])
    condition = "actor.created_spell_id != 'create_undead' or actor.form_attack_in_progress == true"
    if not any(p["condition"] == condition for p in attack["pre_conditions"]):
        attack["pre_conditions"].append({"condition": condition,
            "error": "Ghoul's native Bite and Claw action consumers remain unsupported."})
    changed["resolve_adjudicated_creature_attack"] = attack
    return changed


def _animated_actor_hooks(tools):
    """Bind source casting/control while refusing unowned commanded attack behavior."""
    changed = _animated_actor_tools()
    for name in ("cast_spell", "cast_spell_node", "cast_spell_outside_combat"):
        if name not in tools:
            continue
        cast = deepcopy(tools[name])
        names = {p["name"] for p in cast["parameters"]}
        cast["parameters"] += [deepcopy(p) for p in ANIMATED_CAST_CHOICES if p["name"] not in names]
        branch = _when("_spell_definition.family == 'animated_undead_creature'", [
            _call("cast_spell_animated_undead_creature", {
                "caster_state": "{_caster_state_id}", "slot_level": "{_slot_level}",
                **{p["name"]: "{" + p["name"] + "}" for p in ANIMATED_CAST_CHOICES},
            }, ("_spell_summary_rows", "_spell_detail_rows")),
        ])
        _replace_created_cast_dispatch(cast, "animated_undead_creature", branch)
        changed[name] = cast
    clock = deepcopy(tools["advance_srd_lasting_clock"])
    if "refresh_srd_all_animated_undead_control" not in str(clock["effects"]):
        clock["effects"].append(_when("lasting_state.has_animated_undead == true", [
            _call("refresh_srd_all_animated_undead_control", {}),
        ]))
    changed["advance_srd_lasting_clock"] = clock
    attack = deepcopy(tools["resolve_adjudicated_creature_attack"])
    condition = "actor.created_spell_id != 'animate_dead' or actor.form_attack_in_progress == true"
    attack["pre_conditions"] = [p for p in attack["pre_conditions"] if p["condition"] != condition]
    attack["pre_conditions"].append({"condition": condition,
        "error": "Animated Undead's native attack/command owner remains unsupported."})
    changed["resolve_adjudicated_creature_attack"] = attack
    language = deepcopy(tools["resolve_srd_spell_language"])
    native = "created_spell_id == 'animate_dead' and {subject}.created_form_id == 'zombie' and " \
             "{subject}.created_active == true and {subject}.form_statistics_replaced != true"
    speaker_native = "speaker_state." + native.format(subject="speaker_state")
    listener_native = "listener_state." + native.format(subject="listener_state")
    language["effects"] = [e for e in language["effects"] if
                           "A native Zombie cannot speak" not in str(e) and
                           e.get("condition") != listener_native]
    language["effects"].insert(0, _require(
        "channel != 'speech' or not (" + speaker_native + ")",
        "A native Zombie cannot speak, including through an outgoing Tongues grant.",
    ))
    language["effects"].append(_when(listener_native, [
        _when("_utterance_language != '' and _utterance_language in "
              "listener_state.created_understood_languages and "
              "_utterance_language in _language_speaker_native and "
              "_language_perceived == true", [_set("_language_understands", True)]),
        _when("channel == 'speech'", [_set("_language_can_reply", False)]),
    ]))
    changed["resolve_srd_spell_language"] = language
    return changed


def _animated_command_tools(tools):
    """Save one source mental order and use the actual ordinary Dodge owner on its turn."""
    bonus_cost = deepcopy(_action(bonus=True))
    for effect in _walk(bonus_cost):
        if effect.get("call") == "consume_srd_bonus_action":
            effect["params"]["bonus_action"] = "animate_dead_command"
    command = _public(_tool_scene(
        "Spend one Bonus Action to issue the same mental intent to selected controlled Undead.", [
            _entity("caster_state"),
            _parameter("actor_ids", "array", items={"type": "string"}, min_items=1,
                       max_items=64, unique_items=True),
            _parameter("order_kind", "enum", enum_values=["specific", "general"]),
            _optional("action", "enum", default="dodge",
                      enum_values=["dodge", "attack", "move", "other"]),
            _optional("task", default=""),
        ], [
            _require("state.time_stop_source == null or state.time_stop_source.active != true",
                     "Mental-command effects during Time Stop remain unsupported."),
            _require("(order_kind == 'specific' and task == '') or "
                     "(order_kind == 'general' and len(task) > 0 and len(task) <= 500)",
                     "Save general task intent in 1 to 500 characters; "
                     "specific orders use no task."),
            _require("combat.encounter_active == true or "
                     "caster_state.animated_last_command_tick != state.lasting_tick",
                     "Issue one Bonus Action mental command in this exploration round."),
            *bonus_cost,
            _set("caster_state.animated_last_command_tick", "state.lasting_tick"),
            *_scene_position("{_caster_state_id}", "_animated_command_origin"),
            _set("_animated_command_plane", "_info_scene_entity.metadata.plane"),
            _require("_animated_command_plane != null", "Admit the caster's actual plane."),
            _set("_animated_command_ids", "actor_ids"),
            _loop("_animated_command_ids", "_animated_command_id", [
                *_scene_position("{_animated_command_id}", "_animated_command_target_origin"),
                _require("_info_scene_entity.metadata.plane == _animated_command_plane",
                         "A mental command reaches only the same actual plane."),
                _call("measure_srd_information_distance", {
                    "start": "{_animated_command_origin}",
                    "end": "{_animated_command_target_origin}",
                }, MEASURE),
                _require("_scene_distance <= 60", "The controlled Undead must be within 60 feet."),
                _call("save_srd_animated_undead_order", {
                    "actor_state": "{_animated_command_id}", "caster_state": "{_caster_state_id}",
                    "actor_ids": "{_animated_command_ids}", "order_kind": "{order_kind}",
                    "action": "{action}", "task": "{task}",
                }),
            ]),
            _set("_animated_command_count", "len(_animated_command_ids)"),
            _set("_animated_command_kind", "order_kind"),
        ]), "spells", "Animate Dead mental command", [
            ("Controlled individuals", "_animated_command_count"),
            ("Order kind", "_animated_command_kind"),
        ])
    save = _tool("Save source intent on this exact controlled native body.", [
        _entity("actor_state"), _entity("caster_state"),
        _parameter("actor_ids", "array", items={"type": "string"}),
        _parameter("order_kind"), _parameter("action"), _optional("task", default=""),
    ], [
        _call("qualify_srd_animated_undead_control", {
            "actor_state": "{_actor_state_id}", "caster_state": "{_caster_state_id}",
        }, ("_animated_record",)),
        _require("actor_state.created_form_id == 'zombie' and "
                 "actor_state.form_statistics_replaced != true and "
                 "actor_state.created_source_block_sha256 == _animated_record.source_block_sha256 "
                 "and _animated_record.active == true",
                 "The selected command consumer requires the actual native Zombie source body."),
        _set("actor_state.animated_order", {
            "caster": "_caster_state_id", "selected_actor_ids": "actor_ids",
            "kind": "order_kind", "action": "action", "task": "task",
            "issued_tick": "state.lasting_tick",
        }),
    ])
    dodge = deepcopy(tools["take_dodge_action"])
    dodge["description"] = "Take ordinary Dodge on this controlled animated body's actual turn."
    dodge["parameters"] = [p for p in dodge["parameters"] if p["name"] != "actor"]
    dodge["parameters"].append(_optional("lasting_state", resolve="entity", default="state"))
    for requirement in dodge["pre_conditions"]:
        if requirement["condition"] == "actor.validation_status == 'validated'":
            requirement["condition"] = (
                "actor_state.created_spell_id == 'animate_dead' and "
                "actor_state.created_active == true and actor_state.is_alive == true and "
                "actor_state.hp > 0 and actor_state.animated_control_active == true"
            )
            requirement["error"] = "Only an actual present controlled Undead can use this action."
        requirement["condition"] = requirement["condition"].replace(
            "actor.participant_id", "_actor_state_id")
    dodge["metadata"] = {"category": "internal", "visible_to_player": False, "costs_action": False}
    benefit_conditions = [p["condition"] for p in dodge["pre_conditions"]
                          if "actor_state.speed" in p["condition"] or
                          "actor_state.conditions" in p["condition"]]
    assert len(benefit_conditions) == 2
    start = _tool("Apply a saved next-turn order or default Dodge after actual turn setup.", [
        _entity("actor_state"),
    ], [
        _call("qualify_srd_animated_undead_control", {
            "actor_state": "{_actor_state_id}",
            "caster_state": "{actor_state.created_caster_state_id}",
        }),
        _require("actor_state.created_form_id == 'zombie' and "
                 "actor_state.form_statistics_replaced != true",
                 "The native Zombie turn consumer does not execute replacement-form orders."),
        _set("actor_state.animated_turn_order", None),
        _when("actor_state.animated_order != null", [
            _set("actor_state.animated_turn_order", "actor_state.animated_order"),
        ]),
        _when("actor_state.animated_order != null and "
              "actor_state.animated_order.kind == 'specific'",
              [_set("actor_state.animated_order", None)]),
        _when("actor_state.animated_turn_order == null or "
              "(actor_state.animated_turn_order.kind == 'specific' and "
              "actor_state.animated_turn_order.action == 'dodge')", [
            _when("combat.action_available == true", [
                _choose(" and ".join("(" + condition + ")" for condition in benefit_conditions), [
                    _call("take_srd_animated_undead_dodge", {"actor_state": "{_actor_state_id}"}),
                ], [
                    _call("spend_srd_action", {"magic_action": False}),
                    _set("combat.action_taken", "'dodge'"),
                ]),
                _set("_animated_dodge_taken", True),
                _set("_animated_dodge_benefit", "actor_state.dodging"),
                _set("_animated_dodge_label", "'Default Dodge'"),
                _when("actor_state.animated_turn_order != null", [
                    _set("_animated_dodge_label", "'Commanded Dodge'"),
                ]),
                _when("_animated_dodge_benefit != true", [
                    _set("_animated_dodge_label", "'Dodge without benefit'"),
                ]),
            ]),
        ]),
    ])
    start["effects"] = [
        _set("_animated_dodge_taken", False), _set("_animated_dodge_benefit", False),
        _set("_animated_dodge_label", "''"),
        _call("refresh_srd_animated_undead_control", {"actor_state": "{_actor_state_id}"},
              ("_animated_control_active",)),
        _when("_animated_control_active == true", start["effects"]),
    ]
    return {"command_srd_animated_undead": command, "save_srd_animated_undead_order": save,
            "take_srd_animated_undead_dodge": dodge, "start_srd_animated_undead_turn": start}


def _animated_turn_hooks(tools):
    """Observe only actual controlled native bodies after the final shared turn lifecycle."""
    begin = deepcopy(tools["begin_combat_turn"])
    begin["effects"] = [e for e in begin["effects"]
                        if "start_srd_animated_undead_turn" not in str(e)]
    begin["effects"].append(_when(
        "actor.created_spell_id == 'animate_dead' and actor.created_form_id == 'zombie' and "
        "actor.created_active == true and actor.is_alive == true and actor.hp > 0 and "
        "actor.animated_control_active == true and actor.form_statistics_replaced != true", [
            _call("start_srd_animated_undead_turn", {"actor_state": "{_actor_id}"},
                  ("_animated_dodge_taken", "_animated_dodge_benefit", "_animated_dodge_label")),
            _when("_animated_dodge_taken == true", [
                _push("_turn_effect_rows", {"label": "'Animate Dead'",
                                           "value": "_animated_dodge_label"}),
                _set("_turn_effect_count", "len(_turn_effect_rows)"),
            ]),
        ]))
    return {"begin_combat_turn": begin}


def _animated_attack_tools():
    """Qualify the permanent source body and its actual saved next-turn attack intent."""
    source = _zombie_source_profile()
    qualify = _tool("Qualify this actual Zombie body and any still-active mental control.", [
        _entity("actor_state"),
    ], [
        _require("actor_state.created_spell_id == 'animate_dead' and "
                 "actor_state.created_form_id == 'zombie' and actor_state.created_active == true "
                 "and actor_state.created_individual_id == _actor_state_id and "
                 "actor_state.is_alive == true and actor_state.hp > 0 and "
                 "actor_state.creature_type == 'undead' and "
                 "actor_state.form_statistics_replaced != true and "
                 "actor_state.created_source_block_sha256 == '" +
                 source["source_stat_block_sha256"] + "'",
                 "Use the actual present native Zombie source body."),
        _set("_animated_slam_record", "state.created_actor_records[_actor_state_id]"),
        _require("_animated_slam_record.active == true and "
                 "_animated_slam_record.spell == 'animate_dead' and "
                 "_animated_slam_record.individual == _actor_state_id and "
                 "_animated_slam_record.caster == actor_state.created_caster_state_id and "
                 "_animated_slam_record.source_block_sha256 == "
                 "actor_state.created_source_block_sha256",
                 "Keep the exact individual and installed source record."),
        _require("_animated_slam_record.body_source == actor_state.created_body_source_state_id "
                 "and (actor_state.created_body_source_id == null or "
                 "_animated_slam_record.body_source_id == actor_state.created_body_source_id)",
                 "Keep this body's exact original consumed corpse provenance."),
        _call("refresh_srd_animated_undead_control", {"actor_state": "{_actor_state_id}"},
              ("_animated_control_active",)),
        _when("_animated_control_active == true", [
            _require("actor_state.animated_turn_order != null and "
                     "actor_state.animated_turn_order.kind == 'specific' and "
                     "actor_state.animated_turn_order.action == 'attack' and "
                     "actor_state.animated_turn_order.caster == "
                     "actor_state.created_caster_state_id and "
                     "_actor_state_id in actor_state.animated_turn_order.selected_actor_ids and "
                     "actor_state.animated_turn_order.issued_tick <= state.lasting_tick",
                     "A controlled Zombie needs its actual saved next-turn attack command; "
                     "general task execution remains unsupported."),
        ]),
    ])
    return {"qualify_srd_animated_zombie_attack": qualify}


def _animated_attack_hooks(tools):
    """Bind printed Slam to the final real NPC leaf without changing its roll or packet owners."""
    source = _zombie_source_profile()["attacks"]["Slam"]
    native = "actor.created_spell_id == 'animate_dead' and actor.form_attack_in_progress != true"
    attack = deepcopy(tools["resolve_adjudicated_creature_attack"])
    old_guard = "actor.created_spell_id != 'animate_dead' or actor.form_attack_in_progress == true"
    body_guard = old_guard + " or actor.created_form_id == 'zombie'"
    profile_guard = old_guard + " or (attack_name == 'Slam' and attack_modifier == " + \
        str(source["attack_modifier"]) + " and damage_expression == '" + \
        source["damage_expression"] + "' and damage_modifier == " + \
        str(source["damage_modifier"]) + " and damage_type == '" + source["damage_type"] + \
        "' and attack_mode == 'melee' and normal_range == " + str(source["normal_range"]) + \
        " and long_range == " + str(source["normal_range"]) + \
        " and use_multiattack != true and attack_uses_weapon != true)"
    attack["pre_conditions"] = [p for p in attack["pre_conditions"] if p["condition"] not in
                                {old_guard, body_guard, profile_guard}]
    attack["pre_conditions"] += [
        {"condition": body_guard,
         "error": "Only Zombie's native attack is currently source-qualified."},
        {"condition": profile_guard,
         "error": "Use Zombie's printed single unarmed Slam attack and damage profile."},
    ]
    attack["effects"] = [e for e in attack["effects"]
                         if "qualify_srd_animated_zombie_attack" not in str(e)]
    attack["effects"].insert(0, _when(native, [
        _call("qualify_srd_animated_zombie_attack", {"actor_state": "{_actor_id}"}),
        _call("qualify_srd_created_actor_attack_geometry", {
            "actor_state": "{_actor_id}", "target_state": "{_target_id}",
        }, ("_created_attack_distance",)),
        _require("attack_distance == _created_attack_distance",
                 "Use the actual Zombie and target body distance."),
    ]))
    return _animated_attack_tools() | {"resolve_adjudicated_creature_attack": attack}


def _created_undead_attack_tools(form_id="ghoul"):
    """Qualify the actual Ghoul and retain Bite's second real roll in one received packet."""
    source = (_created_undead_source_profile() if form_id == "ghoul" else
              _created_undead_additional_profiles()[form_id])
    zombie_hash = _zombie_source_profile()["source_stat_block_sha256"]
    qualify = deepcopy(_animated_attack_tools()["qualify_srd_animated_zombie_attack"])

    def bind_source(value):
        if isinstance(value, str):
            return value.replace(zombie_hash, source["source_stat_block_sha256"]).replace(
                "animate_dead", "create_undead").replace("zombie", form_id).replace(
                "Zombie", source["name"])
        if isinstance(value, list):
            return [bind_source(child) for child in value]
        if isinstance(value, dict):
            return {key: bind_source(child) for key, child in value.items()}
        return value

    qualify = bind_source(qualify)
    if form_id == "ghoul":
        qualify["effects"].insert(1, _require(
        "actor_state.multiattack != null and len(actor_state.multiattack) == 1 and "
        "actor_state.multiattack[0].attack == 'Bite' and actor_state.multiattack[0].count == 2",
        "Keep Ghoul's printed two-Bite Multiattack routine."))
    attack = source["attacks"]["Bite"]
    damage = _tool("Receive Bite's actual Piercing and Necrotic through one native packet.", [
        _entity("actor_state"), _entity("target_state"),
        _optional("lasting_state", resolve="entity", default="state"),
        _parameter("amount", "integer"), _parameter("critical_hit", "boolean"),
        _optional("adjustment", "integer", 0),
        _optional("witness_state_ids", "array", None, items={"type": "string"}),
        _optional("unallocated_roll_penalty", "integer", 0),
        _optional("minor_elementals_damage_type", "enum", "fire",
                  enum_values=["acid", "cold", "fire", "lightning"]),
    ], [
        _call("qualify_srd_created_ghoul_attack", {"actor_state": "{_actor_state_id}"}),
        {"dice": {"expression": attack["extra_damage_expression"], "critical": "critical_hit"},
         "store_as": "_ghoul_necrotic_damage_roll"},
        _set("_ghoul_necrotic_damage_amount", "max(0, _ghoul_necrotic_damage_roll.total + " +
             str(attack["extra_damage_modifier"]) + ")"),
        _set("_ghoul_piercing_damage_amount", "max(0, amount + adjustment)"),
        _call("apply_srd_qualifying_composite_damage", {
            "target": "{_target_state_id}", "lasting_state": "{_lasting_state_id}",
            "component_1_amount": "{_ghoul_piercing_damage_amount}",
            "component_1_damage_type": attack["damage_type"],
            "component_2_amount": "{_ghoul_necrotic_damage_amount}",
            "component_2_damage_type": attack["extra_damage_type"],
            "source_state_id": "{_actor_state_id}", "critical_hit": "{critical_hit}",
            "witness_state_ids": "{witness_state_ids}",
            "unallocated_roll_penalty": "{unallocated_roll_penalty}",
            "game_damage_roll": True, "actual_attack_hit": True, "observe_attack_components": True,
            "minor_elementals_damage_type": "{minor_elementals_damage_type}",
        }, ("_effective_damage", "_hp_before", "_hp_after", "_temp_hp_absorbed", "_incoming_damage",
            "_is_alive_after", "_conjure_minor_qualifies", "_conjure_minor_amount",
            "_conjure_minor_type", "_conjure_minor_roll", "_concentration_save_dc",
            "_component_1_effective_damage", "_component_2_effective_damage")),
        _set("_hp_lost", "max(0, _hp_before - _hp_after)"),
    ])
    result = {"qualify_srd_created_ghoul_attack": qualify,
              "apply_srd_created_ghoul_bite_damage": damage}
    return _bind_created_undead_body_tokens(result, form_id)


def _bind_created_undead_body_tokens(value, form_id):
    """Generate distinct closed native receivers while retaining each actual source body."""
    if isinstance(value, str):
        return value.replace("ghoul", form_id).replace("Ghoul", form_id.title())
    if isinstance(value, list):
        return [_bind_created_undead_body_tokens(child, form_id) for child in value]
    if isinstance(value, dict):
        return {_bind_created_undead_body_tokens(key, form_id):
                _bind_created_undead_body_tokens(child, form_id) for key, child in value.items()}
    return value


def _created_ghoul_claw_tools(form_id="ghoul"):
    """Own the printed physical rider and its next actual target-turn expiry."""
    row = (_created_undead_source_profile() if form_id == "ghoul" else
           _created_undead_additional_profiles()[form_id])
    character = _tool("Read Elf identity only from this actual validated native character.", [
        _entity("target_state"), _parameter("target_character", resolve="participant"),
    ], [
        _require("target_character.participant_id == _target_state_id and "
                 "target_character.validation_status == 'validated' and "
                 "target_character.data.species != null",
                 "Ghoul Claw needs this actual validated character's native species."),
        _set("_ghoul_claw_elf", "target_character.data.species == 'elf'"),
    ])
    target = _tool("Qualify the physical Claw exemptions without supplied eligibility.", [
        _entity("target_state"),
    ], [
        _when("target_state.has_actor_spell_modifiers == true", [
            _call("resolve_srd_actor_spell_modifiers", {
                "target_state": "{_target_state_id}", "lasting_state": "state",
            }, ("_actor_modifier_freedom",)),
            _require("_actor_modifier_freedom != true",
                     "Join Ghoul Claw's authenticated native Freedom classification first."),
        ]),
        _require("target_state.form_statistics_replaced != true and "
                 "target_state.current_body_source_id == null",
                 "Ghoul Claw's transferred or replacement-body species is unsupported."),
        _set("_ghoul_claw_exempt", "target_state.creature_type == 'undead'"),
        _when("_ghoul_claw_exempt != true", [
            _require("target_state.creature_kind == 'character'",
                     "Ghoul Claw's unknown NPC species qualification is unsupported."),
            _call("qualify_srd_ghoul_claw_character", {
                "target_state": "{_target_state_id}", "target_character": "{_target_state_id}",
            }, ("_ghoul_claw_elf",)),
            _set("_ghoul_claw_exempt", "_ghoul_claw_elf"),
        ]),
    ])
    if form_id == "ghast":
        target["effects"] = target["effects"][:1] + [
            _require("target_state.creature_type != null",
                     "Admit the actual target's current creature type."),
            _set("_ghoul_claw_exempt", "target_state.creature_type == 'undead'"),
        ]
    projection = _tool("Reproject only Paralyzed after exact physical-source retirement.", [
        _entity("target_state"), _optional("lasting_state", resolve="entity", default="state"),
    ], [
        _set("_ghoul_paralyzed", "target_state.timed_condition_unowned_paralyzed == true"),
        _set("_ghoul_paralysis_index", []),
        _when("target_state.timed_condition_source_ids != null", [
            _set("_ghoul_paralysis_index", "target_state.timed_condition_source_ids"),
        ]),
        _loop("_ghoul_paralysis_index", "_ghoul_paralysis_id", [
            _set("_ghoul_paralysis_source", "lasting_state.timed_condition_sources["
                 "_ghoul_paralysis_id]"),
            _when("_ghoul_paralysis_source != null and "
                  "_ghoul_paralysis_source.installed == true and "
                  "_ghoul_paralysis_source.target_state_id == _target_state_id and "
                  "_ghoul_paralysis_source.condition == 'paralyzed' and "
                  "_ghoul_paralysis_source.suppressed != true", [
                _set("_ghoul_paralyzed", True),
            ]),
        ]),
        {"remove_condition": {"target": "target_state", "condition": "paralyzed"}},
        _when("_ghoul_paralyzed == true", [
            {"apply_condition": {"target": "target_state", "condition": "paralyzed"}},
        ]),
        _set("target_state.timed_condition_paralyzed", "_ghoul_paralyzed and "
             "target_state.timed_condition_unowned_paralyzed != true"),
    ])
    resolve = _tool("Resolve the actual native Claw's fixed Constitution save after its hit.", [
        _entity("actor_state"), _entity("target_state"),
        _optional("lasting_state", resolve="entity", default="state"),
    ], [
        _call("qualify_srd_created_ghoul_attack", {"actor_state": "{_actor_state_id}"}),
        _call("qualify_srd_ghoul_claw_target", {"target_state": "{_target_state_id}"},
              ("_ghoul_claw_exempt",)),
        _set("_ghoul_claw_save_outcome", "'exempt'"),
        _set("_ghoul_claw_save_total", None), _set("_ghoul_claw_save_roll", None),
        _set("_ghoul_claw_source_id", None),
        _when("_ghoul_claw_exempt != true and target_state.is_alive == true", [
            _call("check_srd_target_condition_immunity", {
                "target_state": "{_target_state_id}", "condition": "paralyzed",
                "source_state_id": "{_actor_state_id}",
            }, ("_source_condition_immune",)),
            _set("_ghoul_claw_save_outcome", "'immune'"),
            _when("_source_condition_immune != true", [
                _call("resolve_srd_divination_save", {
                    "target_state": "{_target_state_id}", "actor": "{_target_state_id}",
                    "ability": "constitution", "dc": 10,
                }, ("_divination_save_success", "_d20_test", "_adjusted_total")),
                _set("_ghoul_claw_save_total", "_adjusted_total"),
                _set("_ghoul_claw_save_roll", "_d20_test"),
                _set("_ghoul_claw_save_outcome", "'success'"),
                _when("_divination_save_success != true", [
                    _set("_ghoul_claw_save_outcome", "'failure'"),
                    _call("install_srd_timed_condition_source", {
                        "target_state": "{_target_state_id}",
                        "lasting_state": "{_lasting_state_id}",
                        "caster_state": "{_actor_state_id}", "spell": "create_undead",
                        "condition": "paralyzed", "duration_rounds": 0,
                        "save_dc": 10, "repeat_save_timing": "none", "save_ability": "constitution",
                    }, ("_timed_condition_source_id",)),
                    _set("_ghoul_claw_source_id", "_timed_condition_source_id"),
                    _set("_ghoul_claw_target_turn", 0),
                    _when("target_state.turns_started != null", [
                        _set("_ghoul_claw_target_turn", "target_state.turns_started"),
                    ]),
                    _set("_ghoul_claw_native_stamp", {
                        "target_end_turn": "_ghoul_claw_target_turn + 1",
                        "native_body_source": "_actor_state_id", "native_attack": "'Claw'",
                        "native_body_block_sha256": repr(row["source_stat_block_sha256"]),
                        "native_body_source_sequence": "lasting_state.timed_condition_sources["
                        "_timed_condition_source_id].sequence",
                        "dispel_ongoing": False, "source_is_magical": False,
                        "ongoing_duration": False, "cast_id": "''",
                    }),
                    _set("lasting_state.timed_condition_sources.{_timed_condition_source_id}",
                         "merge(lasting_state.timed_condition_sources[_timed_condition_source_id], "
                         "_ghoul_claw_native_stamp)"),
                    _set("_ghoul_claw_index", []),
                    _when("target_state.ghoul_claw_source_ids != null", [
                        _set("_ghoul_claw_index", "target_state.ghoul_claw_source_ids"),
                    ]),
                    _push("_ghoul_claw_index", "_timed_condition_source_id"),
                    _set("target_state.ghoul_claw_source_ids", "_ghoul_claw_index"),
                    _set("target_state.has_ghoul_claw_sources", True),
                ]),
            ]),
        ]),
    ])
    if form_id == "ghast":
        for effect in _walk(resolve["effects"]):
            if effect.get("call") == "resolve_srd_divination_save":
                effect["params"]["actor"] = "{_ghast_claw_character}"
        resolve["effects"].insert(0, _set("_ghast_claw_character", None))
        resolve["effects"].insert(1, _when("target_state.creature_kind == 'character'", [
            _set("_ghast_claw_character", "_target_state_id"),
        ]))
    expire = _tool("Retire only Claw sources due at this target's actual completed next turn.", [
        _entity("target_state"), _optional("lasting_state", resolve="entity", default="state"),
    ], [
        _set("_ghoul_claw_retained", []), _set("_ghoul_claw_retired_count", 0),
        _set("_ghoul_claw_due_index", "target_state.ghoul_claw_source_ids"),
        _loop("_ghoul_claw_due_index", "_ghoul_claw_due_id", [
            _set("_ghoul_claw_due", "lasting_state.timed_condition_sources[_ghoul_claw_due_id]"),
            _when("_ghoul_claw_due != null and _ghoul_claw_due.installed == true and "
                  "_ghoul_claw_due.target_state_id == _target_state_id and "
                  "_ghoul_claw_due.native_attack == 'Claw' and "
                  "_ghoul_claw_due.native_body_source_sequence == _ghoul_claw_due.sequence", [
                _choose("target_state.turns_started >= _ghoul_claw_due.target_end_turn", [
                    _call("retire_srd_timed_condition_source", {
                        "lasting_state": "{_lasting_state_id}", "source_id": "{_ghoul_claw_due_id}",
                    }),
                    _set("_ghoul_claw_retired_count", "_ghoul_claw_retired_count + 1"),
                ], [
                    _push("_ghoul_claw_retained", "_ghoul_claw_due_id"),
                ]),
            ]),
        ]),
        _set("target_state.ghoul_claw_source_ids", "_ghoul_claw_retained"),
        _set("target_state.has_ghoul_claw_sources", "len(_ghoul_claw_retained) > 0"),
        _call("reproject_srd_ghoul_claw_paralysis", {
            "target_state": "{_target_state_id}", "lasting_state": "{_lasting_state_id}",
        }),
    ])
    result = {
        "qualify_srd_ghoul_claw_character": character,
        "qualify_srd_ghoul_claw_target": target, "resolve_srd_created_ghoul_claw": resolve,
        "reproject_srd_ghoul_claw_paralysis": projection,
        "expire_srd_created_ghoul_claw_sources": expire}
    if form_id == "ghast":
        result.pop("qualify_srd_ghoul_claw_character")
    return _bind_created_undead_body_tokens(result, form_id)


def _strip_created_undead_receivers(effects, receiver):
    """Recompose only our own packet branches around the unchanged final receiving call."""
    for effect in effects:
        while effect.get("condition", "").startswith(
                "actor.created_spell_id == 'create_undead'") and receiver in str(effect):
            fallback = deepcopy(effect["on_false"][0])
            effect.clear()
            effect.update(fallback)
        for key in ("on_true", "on_false"):
            _strip_created_undead_receivers(effect.get(key, []), receiver)
        _strip_created_undead_receivers(effect.get("for_each", {}).get("do", []), receiver)


def _created_ghoul_claw_hooks(tools):
    """Bind each printed physical Claw to its genuine hit and actual target end turn."""
    changed = {}
    attack = deepcopy(tools["resolve_adjudicated_creature_attack"])
    native = "actor.created_spell_id == 'create_undead' and actor.form_attack_in_progress != true"
    guard = "actor.created_spell_id != 'create_undead' or actor.form_attack_in_progress == true"
    _strip_created_undead_receivers(attack["effects"], "resolve_srd_created_gh")
    attack["effects"] = [e for e in attack["effects"] if not (
        "qualify_srd_ghoul_claw_target" in str(e) or
        "qualify_srd_ghast_claw_target" in str(e))]
    attack["pre_conditions"] = [p for p in attack["pre_conditions"] if
        p["condition"] != guard and p["error"] not in {
        "Use the actual Ghoul's printed Bite; Claw's paralysis consumer is unsupported.",
        "Use the actual Ghoul's printed native Bite or Claw.",
        "Use the actual Ghast's printed native Bite or Claw."}]
    for form_id in ("ghoul", "ghast"):
        changed.update(_created_ghoul_claw_tools(form_id))
        source = (_created_undead_source_profile() if form_id == "ghoul" else
                  _created_undead_additional_profiles()[form_id])
        common = source["attacks"]["Bite"]
        assert all(common[key] == source["attacks"]["Claw"][key] for key in (
            "kind", "attack_modifier", "damage_modifier", "normal_range", "long_range"))
        profiles = []
        for name in ("Bite", "Claw"):
            row = source["attacks"][name]
            profiles.append("(attack_name == '" + name + "' and damage_expression == '" +
                            row["damage_expression"] + "' and damage_type == '" +
                            row["damage_type"] + "')")
        profile = guard + " or actor.created_form_id != '" + form_id + "' or (" + \
            "attack_modifier == " + str(common["attack_modifier"]) + \
            " and damage_modifier == " + str(common["damage_modifier"]) + \
            " and attack_mode == 'melee' and normal_range == " + str(common["normal_range"]) + \
            " and long_range == " + str(common["long_range"]) + \
            " and attack_uses_weapon != true and (" + " or ".join(profiles) + "))"
        assert len(profile) <= 500
        attack["pre_conditions"].append({"condition": profile,
            "error": "Use the actual " + source["name"] + "'s printed native Bite or Claw."})
        marker = native + " and actor.created_form_id == '" + form_id + \
            "' and attack_name == 'Claw'"
        call_name = "qualify_srd_" + form_id + "_claw_target"
        attack["effects"].insert(1, _when(marker, [
            _require("use_multiattack != true", "Claw is a separate printed Action."),
            _call(call_name, {"target_state": "{_target_id}"}),
        ]))

        def receiving_packets(effects, bound_form=form_id):
            for effect in effects:
                if "resolve_srd_created_" + bound_form + "_claw" in str(effect):
                    continue
                yield effect
                for key in ("on_true", "on_false"):
                    yield from receiving_packets(effect.get(key, []))
                yield from receiving_packets(effect.get("for_each", {}).get("do", []))

        for effect in tuple(receiving_packets(attack["effects"])):
            if effect.get("call") != "apply_srd_qualifying_damage" or \
                    "game_damage_roll" not in effect.get("params", {}):
                continue
            original = deepcopy(effect)
            effect.clear()
            exports = tuple("_" + form_id + "_claw_" + suffix for suffix in (
                "exempt", "save_outcome", "save_total", "save_roll", "source_id"))
            effect.update(_choose(marker, [original,
                _call("resolve_srd_created_" + form_id + "_claw", {
                    "actor_state": "{_actor_id}", "target_state": "{_target_id}",
                }, exports)], [deepcopy(original)]))
    details = attack["metadata"]["result_card"]["details"]
    if not any(r["label"] == "Claw Constitution save" for r in details):
        details += [{"label": "Claw Constitution save", "value": {
            "path": "context._ghoul_claw_save_outcome"}, "optional": True},
            {"label": "Claw save total", "value": {
                "path": "context._ghoul_claw_save_total"}, "optional": True}]
    if not any(r["label"] == "Ghast Claw Constitution save" for r in details):
        details += [{"label": "Ghast Claw Constitution save", "value": {
            "path": "context._ghast_claw_save_outcome"}, "optional": True},
            {"label": "Ghast Claw save total", "value": {
                "path": "context._ghast_claw_save_total"}, "optional": True}]
    changed["resolve_adjudicated_creature_attack"] = attack
    end = deepcopy(tools["end_combat_turn"])
    end["effects"] = [e for e in end["effects"] if not (
        "expire_srd_created_ghoul_claw_sources" in str(e) or
        "expire_srd_created_ghast_claw_sources" in str(e))]
    for form_id in ("ghoul", "ghast"):
        end["effects"].append(_when("actor.has_" + form_id + "_claw_sources == true", [
            _call("expire_srd_created_" + form_id + "_claw_sources", {
                "target_state": "{_actor_id}"}, ("_" + form_id + "_claw_retired_count",)),
        ]))
    changed["end_combat_turn"] = end
    return changed


def _created_undead_attack_hooks(tools):
    """Bind exact native attacks to the final real NPC and typed receiving owners."""
    native = "actor.created_spell_id == 'create_undead' and actor.form_attack_in_progress != true"
    guard = "actor.created_spell_id != 'create_undead' or actor.form_attack_in_progress == true"
    changed = {}
    attack = deepcopy(tools["resolve_adjudicated_creature_attack"])
    _strip_created_undead_receivers(attack["effects"], "apply_srd_created_gh")
    attack["pre_conditions"] = [p for p in attack["pre_conditions"] if
        p["condition"] != guard and p["error"] not in {
        "Use the actual Ghoul's printed Bite; Claw's paralysis consumer is unsupported.",
        "Use the actual Ghoul's printed native Bite or Claw.",
        "Use the actual Ghast's printed native Bite or Claw.",
        "The selected created Undead native attack routine remains unsupported."}]
    attack["pre_conditions"].append({"condition": guard +
        " or actor.created_form_id == 'ghoul' or actor.created_form_id == 'ghast'",
        "error": "The selected created Undead native attack routine remains unsupported."})
    attack["effects"] = [e for e in attack["effects"] if not (
        "qualify_srd_created_ghoul_attack" in str(e) or
        "qualify_srd_created_ghast_attack" in str(e))]
    qualify_body = []
    for form_id in ("ghoul", "ghast"):
        changed.update(_created_undead_attack_tools(form_id))
        qualify_body.append(_when("actor.created_form_id == '" + form_id + "'", [
            _call("qualify_srd_created_" + form_id + "_attack", {
                "actor_state": "{_actor_id}"}),
        ]))
    attack["effects"].insert(0, _when(native, qualify_body + [
        _when("actor.created_form_id == 'ghast'", [
            _require("use_multiattack != true", "Ghast has no printed Multiattack."),
        ]),
        _call("qualify_srd_created_actor_attack_geometry", {
            "actor_state": "{_actor_id}", "target_state": "{_target_id}",
        }, ("_created_attack_distance",)),
        _require("attack_distance == _created_attack_distance",
                 "Use the actual created Undead and target body distance."),
    ]))

    def receiving_packets(effects):
        for effect in effects:
            if "apply_srd_created_ghoul_bite_damage" in str(effect) or \
                    "apply_srd_created_ghast_bite_damage" in str(effect):
                continue
            yield effect
            for key in ("on_true", "on_false"):
                yield from receiving_packets(effect.get(key, []))
            yield from receiving_packets(effect.get("for_each", {}).get("do", []))

    for effect in tuple(receiving_packets(attack["effects"])):
        if effect.get("call") != "apply_srd_qualifying_damage" or \
                "game_damage_roll" not in effect.get("params", {}):
            continue
        original = deepcopy(effect)
        choices = original
        for form_id in ("ghast", "ghoul"):
            branch = _call("apply_srd_created_" + form_id + "_bite_damage", {
                "actor_state": "{_actor_id}", "target_state": "{_target_id}",
                "lasting_state": original["params"].get("lasting_state", "state"),
                "amount": original["params"]["amount"],
                "adjustment": original["params"].get("adjustment", 0),
                "critical_hit": original["params"]["critical_hit"],
                "witness_state_ids": original["params"].get(
                    "witness_state_ids", "{witness_state_ids}"),
                "unallocated_roll_penalty": original["params"].get("unallocated_roll_penalty", 0),
                "minor_elementals_damage_type": "{minor_elementals_damage_type}",
            })
            branch["export_context"] = deepcopy(original.get("export_context", {}))
            branch["export_context"].update({name: name for name in (
                "_" + form_id + "_necrotic_damage_roll", "_" + form_id + "_necrotic_damage_amount",
                "_" + form_id + "_piercing_damage_amount", "_component_1_effective_damage",
                "_component_2_effective_damage")})
            choices = _choose(native + " and actor.created_form_id == '" + form_id +
                              "' and attack_name == 'Bite'", [branch], [choices])
        effect.clear()
        effect.update(choices)
    details = attack["metadata"]["result_card"]["details"]
    for form_id in ("ghoul", "ghast"):
        label = "Bite Necrotic dice" if form_id == "ghoul" else "Ghast Bite Necrotic dice"
        if not any(r["label"] == label for r in details):
            details += [{"label": label, "value": {
                "path": "context._" + form_id + "_necrotic_damage_roll.results"}, "optional": True},
                {"label": form_id.title() + " Bite Necrotic before defenses", "value": {
                    "path": "context._" + form_id + "_necrotic_damage_amount"}, "optional": True}]
    changed["resolve_adjudicated_creature_attack"] = attack
    return changed


def _created_ghast_stench_tools():
    """Resolve one actual native Emanation and its exact per-individual immunity/expiry."""
    source = _created_undead_additional_profiles()["ghast"]
    native = deepcopy(_created_undead_attack_tools("ghast")["qualify_srd_created_ghast_attack"])
    native["effects"] = [e for e in native["effects"] if not (
        e.get("call") == "refresh_srd_animated_undead_control" or
        e.get("condition") == "_animated_control_active == true")]
    native["description"] = "Qualify the actual permanent Ghast body without command intent."
    geometry = _created_actor_attack_geometry_tool()
    effects = geometry["effects"]
    cutoff = next(i for i, e in enumerate(effects) if
                  e.get("set", {}).get("field") == "_created_attack_targets")
    geometry["effects"] = effects[:cutoff] + [
        _set("_ghast_stench_in_area", False),
        _when("_created_attack_distance <= 5", [
            _set("_created_attack_targets", ["_target_state_id"]),
            _call("collect_srd_spell_area_cover_targets", {
                "source_state": "{_actor_state_id}", "targets": "{_created_attack_targets}",
                "origin": "{_created_attack_eye}",
            }, ("_srd_area_targets",)),
            _set("_ghast_stench_in_area", "_target_state_id in _srd_area_targets"),
        ]),
    ]
    plane_index = next(i for i, e in enumerate(geometry["effects"])
                       if "another plane" in e.get("require", {}).get("error", ""))
    plane_check = geometry["effects"][plane_index]
    plane_check["require"]["condition"] = (
        "_created_attacker_scene.metadata.plane != null and "
        "_info_scene_entity.metadata.plane != null")
    plane_check["require"]["error"] = "Admit both actual body planes."
    geometry["effects"] = geometry["effects"][:plane_index + 1] + [
        _set("_ghast_stench_in_area", False),
        _when("_created_attacker_scene.metadata.plane == _info_scene_entity.metadata.plane",
              geometry["effects"][plane_index + 1:]),
    ]
    geometry["description"] = "Qualify actual Ghast body Emanation membership and physical path."
    projection = deepcopy(_created_ghoul_claw_tools()["reproject_srd_ghoul_claw_paralysis"])

    def poisoned(value):
        if isinstance(value, str):
            return value.replace("ghoul", "ghast_stench").replace("paralyzed", "poisoned").replace(
                "paralysis", "poison").replace("Paralyzed", "Poisoned")
        if isinstance(value, list):
            return [poisoned(child) for child in value]
        if isinstance(value, dict):
            return {key: poisoned(child) for key, child in value.items()}
        return value

    projection = poisoned(projection)
    projection["effects"][0]["set"]["value"] += " or target_state.turn_effect_poisoned == true"
    expire = _tool("Retire Stench at this target's actual next turn start, preserving siblings.", [
        _entity("target_state"), _optional("lasting_state", resolve="entity", default="state"),
    ], [
        _set("_stench_retained", []), _set("_stench_retired_count", 0),
        _set("_stench_due_ids", "target_state.ghast_stench_source_ids"),
        _loop("_stench_due_ids", "_stench_due_id", [
            _set("_stench_due", "lasting_state.timed_condition_sources[_stench_due_id]"),
            _when("_stench_due != null and _stench_due.installed == true and "
                  "_stench_due.target_state_id == _target_state_id and "
                  "_stench_due.native_attack == 'Stench' and "
                  "_stench_due.native_body_source_sequence == _stench_due.sequence", [
                _choose("target_state.turns_started >= _stench_due.target_start_turn", [
                    _call("retire_srd_timed_condition_source", {
                        "source_id": "{_stench_due_id}", "lasting_state": "{_lasting_state_id}"}),
                    _set("_stench_retired_count", "_stench_retired_count + 1"),
                ], [_push("_stench_retained", "_stench_due_id")]),
            ]),
        ]),
        _set("target_state.ghast_stench_source_ids", "_stench_retained"),
        _set("target_state.has_ghast_stench_sources", "len(_stench_retained) > 0"),
        _call("reproject_srd_ghast_stench_poison", {
            "target_state": "{_target_state_id}", "lasting_state": "{_lasting_state_id}"}),
    ])
    resolve = _tool_scene("Resolve the actual native Ghast's Stench at target turn start.", [
        _entity("actor_state"), _entity("target_state"),
        _optional("lasting_state", resolve="entity", default="state"),
    ], [
        _call("qualify_srd_created_ghast_native_body", {"actor_state": "{_actor_state_id}"}),
        _call("qualify_srd_created_ghast_stench_geometry", {
            "actor_state": "{_actor_state_id}", "target_state": "{_target_state_id}"},
            ("_ghast_stench_in_area",)),
        _set("_ghast_stench_outcome", "'outside'"),
        _set("_ghast_stench_source_id", None), _set("_ghast_stench_save_total", None),
        _set("_ghast_stench_save_roll", None),
        _when("_ghast_stench_in_area == true", [
            _set("_ghast_stench_immunity_until", 0),
            _when("target_state.ghast_stench_immunities != null and "
                  "_actor_state_id in target_state.ghast_stench_immunities", [
                _set("_ghast_stench_immunity_until", "target_state.ghast_stench_immunities["
                     "_actor_state_id]"),
            ]),
            _set("_ghast_stench_outcome", "'immune'"),
            _when("_ghast_stench_immunity_until <= lasting_state.lasting_tick", [
                _call("check_srd_target_condition_immunity", {
                    "target_state": "{_target_state_id}", "condition": "poisoned",
                    "source_state_id": "{_actor_state_id}"}, ("_source_condition_immune",)),
                _when("_source_condition_immune != true", [
                    _set("_stench_character", None),
                    _when("target_state.creature_kind == 'character'", [
                        _set("_stench_character", "_target_state_id")]),
                    _call("resolve_srd_divination_save", {
                        "target_state": "{_target_state_id}", "actor": "{_stench_character}",
                        "ability": "constitution", "dc": 10,
                    }, ("_divination_save_success", "_d20_test", "_adjusted_total")),
                    _set("_ghast_stench_save_total", "_adjusted_total"),
                    _set("_ghast_stench_save_roll", "_d20_test"),
                    _choose("_divination_save_success == true", [
                        _set("_ghast_stench_outcome", "'success'"),
                        _when("target_state.ghast_stench_immunities == null", [
                            _set("target_state.ghast_stench_immunities", {})]),
                        _set("target_state.ghast_stench_immunities.{_actor_state_id}",
                             "lasting_state.lasting_tick + 28800"),
                    ], [
                        _set("_ghast_stench_outcome", "'failure'"),
                        _call("install_srd_timed_condition_source", {
                            "target_state": "{_target_state_id}",
                            "lasting_state": "{_lasting_state_id}",
                            "caster_state": "{_actor_state_id}", "spell": "create_undead",
                            "condition": "poisoned", "duration_rounds": 0, "save_dc": 10,
                            "repeat_save_timing": "none", "save_ability": "constitution",
                        }, ("_timed_condition_source_id",)),
                        _set("_ghast_stench_source_id", "_timed_condition_source_id"),
                        _set("_stench_native_stamp", {
                            "target_start_turn": "target_state.turns_started + 1",
                            "native_body_source": "_actor_state_id", "native_attack": "'Stench'",
                            "native_body_block_sha256": repr(source["source_stat_block_sha256"]),
                            "native_body_source_sequence": "lasting_state.timed_condition_sources["
                            "_timed_condition_source_id].sequence",
                            "dispel_ongoing": False, "source_is_magical": False,
                            "ongoing_duration": False, "cast_id": "''",
                        }),
                        _set("lasting_state.timed_condition_sources.{_timed_condition_source_id}",
                             "merge(lasting_state.timed_condition_sources["
                             "_timed_condition_source_id], _stench_native_stamp)"),
                        _when("target_state.ghast_stench_source_ids == null", [
                            _set("target_state.ghast_stench_source_ids", [])]),
                        _set("_stench_target_source_ids", "target_state.ghast_stench_source_ids"),
                        _push("_stench_target_source_ids", "_timed_condition_source_id"),
                        _set("target_state.ghast_stench_source_ids", "_stench_target_source_ids"),
                        _set("target_state.has_ghast_stench_sources", True),
                        _call("reproject_srd_ghast_stench_poison", {
                            "target_state": "{_target_state_id}",
                            "lasting_state": "{_lasting_state_id}"}),
                    ]),
                ]),
            ]),
        ]),
    ])
    observe = _tool("Observe all actual retained created Ghasts on this creature's real turn.", [
        _entity("target_state"),
    ], [
        _set("_stench_ghasts", "state.created_ghast_ids"), _set("_ghast_stench_rows", []),
        _loop("_stench_ghasts", "_stench_ghast", [
            _call("observe_srd_created_ghast_stench_body", {
                "actor_state": "{_stench_ghast}", "target_state": "{_target_state_id}"},
                ("_ghast_stench_outcome", "_ghast_stench_save_total", "_ghast_stench_save_roll")),
            _when("_ghast_stench_outcome != 'inactive' and _ghast_stench_outcome != 'outside'", [
                _push("_ghast_stench_rows", {"label": "'Ghast Stench source'",
                     "value": "_stench_ghast"}),
                _push("_ghast_stench_rows", {"label": "'Ghast Stench'",
                     "value": "_ghast_stench_outcome"}),
                _when("_ghast_stench_save_roll != null", [
                    _push("_ghast_stench_rows", {"label": "'Stench Constitution dice'",
                         "value": "_ghast_stench_save_roll.results"}),
                    _push("_ghast_stench_rows", {"label": "'Stench Constitution total'",
                         "value": "_ghast_stench_save_total"}),
                ]),
            ]),
        ]),
    ])
    body = _tool("Skip absent, dead or replaced bodies before actual native Stench.", [
        _entity("actor_state"), _entity("target_state"),
    ], [
        _set("_ghast_stench_outcome", "'inactive'"),
        _set("_ghast_stench_save_total", None), _set("_ghast_stench_save_roll", None),
        _when("actor_state.created_active == true and actor_state.is_alive == true and "
              "actor_state.hp > 0 and actor_state.form_statistics_replaced != true and "
              "target_state.is_alive == true and target_state.hp > 0", [
            _call("resolve_srd_created_ghast_stench", {
                "actor_state": "{_actor_state_id}", "target_state": "{_target_state_id}"},
                ("_ghast_stench_outcome", "_ghast_stench_save_total", "_ghast_stench_save_roll")),
        ]),
    ])
    return {"qualify_srd_created_ghast_native_body": native,
            "qualify_srd_created_ghast_stench_geometry": geometry,
            "resolve_srd_created_ghast_stench": resolve,
            "observe_srd_created_ghast_stench": observe,
            "observe_srd_created_ghast_stench_body": body,
            "reproject_srd_ghast_stench_poison": projection,
            "expire_srd_created_ghast_stench_sources": expire}


def _created_ghast_stench_hooks(tools):
    """Bind exact Stench expiry and renewed exposure after actual native turn setup."""
    changed = _created_ghast_stench_tools()
    begin = deepcopy(tools["begin_combat_turn"])
    begin["effects"] = [e for e in begin["effects"] if not (
        "expire_srd_created_ghast_stench_sources" in str(e) or
        "observe_srd_created_ghast_stench" in str(e))]
    begin["effects"].append(_when("actor.has_ghast_stench_sources == true", [
        _call("expire_srd_created_ghast_stench_sources", {"target_state": "{_actor_id}"}),
    ]))
    begin["effects"].append(_when("state.has_created_ghasts == true", [
        _call("observe_srd_created_ghast_stench", {"target_state": "{_actor_id}"},
              ("_ghast_stench_rows",)),
        _loop("_ghast_stench_rows", "_ghast_stench_row", [
            _push("_turn_effect_rows", "_ghast_stench_row"),
        ]),
        _set("_turn_effect_count", "len(_turn_effect_rows)"),
    ]))
    changed["begin_combat_turn"] = begin
    refresh = deepcopy(tools["refresh_srd_timed_condition_target"])
    refresh["effects"] = [e for e in refresh["effects"] if
                          "reproject_srd_ghast_stench_poison" not in str(e)]
    refresh["effects"].append(_when("target_state.has_ghast_stench_sources == true", [
        _call("reproject_srd_ghast_stench_poison", {
            "target_state": "{_target_state_id}", "lasting_state": "{_lasting_state_id}"}),
    ]))
    changed["refresh_srd_timed_condition_target"] = refresh
    return changed


def bind_created_undead_cast_consumers(text):
    """Keep initial material intent at cast start and selected corpse count at completion."""
    tools = _load_runtime(text)["tools"]
    changed = {}
    marker = "Create Undead's actual selected source count determines its onyx stones."
    for name in ("begin_srd_spell_cast_internal", "begin_srd_spell_cast"):
        if name not in tools:
            continue
        tool = deepcopy(tools[name])
        for effect in _walk(tool["effects"]):
            if not any(child.get("require", {}).get("error") == marker
                       for child in effect.get("on_true", [])):
                continue
            assert effect["condition"] == "spell == 'create_undead'"
            night = [child for child in effect["on_true"]
                     if child.get("call") == "require_srd_created_undead_night"]
            assert len(night) == 1
            effect["on_true"] = night
            changed[name] = tool
    return _replace_tools(text, changed) if changed else text


def bind_animated_undead_turn_consumers(text):
    """Bind animated body turns and native attacks after final action/packet composition."""
    tools = _load_runtime(text)["tools"]
    changed = _animated_command_tools(tools) | _animated_turn_hooks(tools) | \
        _animated_attack_hooks(tools)
    changed.update(_created_undead_attack_hooks(tools | changed))
    changed.update(_created_ghoul_claw_hooks(tools | changed))
    changed.update(_created_undead_control_hooks(tools | changed))
    changed.update(_created_ghast_stench_hooks(tools | changed))
    _normalize(changed)
    return _replace_tools(text, changed)


def _familiar_actor_tools(tools):
    """Derive a Familiar's real NPC body from the existing exact Beast catalog."""
    forms = _familiar_source_forms()
    prepare = deepcopy(tools["prepare_srd_creature"])
    original_parameters = prepare["parameters"]
    prepare["description"] = "Install only a source-qualified Familiar's actual creature body."
    prepare["metadata"] = {
        "category": "internal", "visible_to_player": False, "costs_action": False,
    }
    prepare["parameters"] = [
        _parameter("creature_id"), _parameter("form_id"), _entity("source_caster"),
        _parameter("creature_type", "enum", enum_values=["celestial", "fey", "fiend"]),
        _parameter("returning", "boolean"),
        _optional("lasting_state", resolve="entity", default="state"),
    ]
    prepare["pre_conditions"] = []
    source_fields = {
        "name": "_familiar_form.name", "armor_class": "_familiar_form.armor_class",
        "hit_points": "_familiar_form.hit_points", "speed": "_familiar_form.walk_speed",
        "size_rank": "_familiar_form.size_rank",
        "initiative_modifier": "_familiar_form.initiative_modifier",
        "challenge_rating_eighths": "_familiar_form.challenge_rating_eighths",
        "proficiency_bonus": 2,
        **{a + "_score": "_familiar_form.ability_scores." + a for a in (
            "strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma")},
        **{a + "_save": "_familiar_form.saving_throw_modifiers." + a for a in (
            "strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma")},
    }
    prefix = [
        _require(" or ".join("form_id == '" + key + "'" for key in forms),
                 "This Familiar form has unowned source traits or actions; "
                 "choose a qualified form."),
        {"table_lookup": {"table": "definitions.beast_forms", "key": "form_id", "required": True},
         "store_as": "_familiar_form"},
        _require("_familiar_form.challenge_rating_eighths == 0 and "
                 "_familiar_form.execution_supported == true",
                 "A Familiar must retain its actual source CR 0 body."),
    ]
    for parameter in original_parameters:
        name = parameter["name"]
        if name in {"creature_id", "creature_type", "source"}:
            continue
        prefix.append(_set(name, source_fields.get(name, deepcopy(parameter.get("default")))))
    for key, row in forms.items():
        prefix.append(_when("form_id == '" + key + "'", [
            _set("source", "'SRD5.2.1 " + row["name"] + " p" + str(row["source"]["page"]) + "'"),
            _set("flying_speed", row["other_speeds"].get("fly", 0)),
            _set("skill_checks", [{"skill": key, "modifier": value}
                                  for key, value in row["skills"].items()]),
        ]))
    prepare["effects"] = prefix + prepare["effects"]
    for index, effect in enumerate(prepare["effects"]):
        if "create_entity" not in effect:
            continue
        body = effect["create_entity"]["value"]
        returned = _choose("returning == true", [
            _call("qualify_srd_created_actor_return", {
                "actor_state": "{creature_id}", "caster_state": "{_source_caster_id}",
                "spell": "find_familiar",
            }),
            *[_set("creature_id." + key, value) for key, value in body.items()],
        ], [effect])
        prepare["effects"][index] = returned
        break
    removed_names = {
        p["name"] for p in original_parameters if p["name"] not in {"creature_id", "creature_type"}
    }
    local_pattern = re.compile(
        r"(?<![.\w])(" + "|".join(sorted(removed_names, key=len, reverse=True)) + r")(?!\w)"
    )

    def localize(value):
        if isinstance(value, str):
            return local_pattern.sub(lambda match: "_familiar_" + match.group(), value)
        if isinstance(value, list):
            return [localize(item) for item in value]
        if isinstance(value, dict):
            return {key: localize(item) for key, item in value.items()}
        return value

    prepare["effects"] = localize(prepare["effects"])
    finish = _tool("Retain the source form, real senses and stable Familiar provenance.", [
        _entity("actor_state"), _entity("caster_state"), _parameter("form_id"),
    ], [
        {"table_lookup": {"table": "definitions.beast_forms", "key": "form_id", "required": True},
         "store_as": "_familiar_form"},
        _set("actor_state.created_spell_id", "'find_familiar'"),
        _set("actor_state.created_caster_state_id", "_caster_state_id"),
        _set("actor_state.created_individual_id", "_actor_state_id"),
        _set("actor_state.created_active", True),
        _set("actor_state.created_disappearance", "''"),
        _set("actor_state.created_form_id", "form_id"),
        _set("actor_state.created_source_page", "_familiar_form.source.page"),
        _set("actor_state.created_source_block_sha256", "_familiar_form.source_stat_block_sha256"),
        _set("actor_state.passive_perception", "_familiar_form.passive_perception"),
        _set("actor_state.languages", "_familiar_form.languages"),
        _set("actor_state.telepathy_feet", 100),
        _set("actor_state.death_tick", None),
        _set("actor_state.body_available", True), _set("actor_state.vital_parts_present", True),
        *[_set("actor_state." + field, "_familiar_form.senses." + field) for field in (
            "darkvision_feet", "blindsight_feet", "tremorsense_feet", "truesight_feet")],
        *[_set("actor_state." + field, "_familiar_form." + field) for field in (
            "breathes_air", "breathes_water", "hold_breath_ticks")],
        *[_when("form_id == '" + key + "'", [
              _set("actor_state.climb_speed", row["other_speeds"].get("climb", 0)),
              _set("actor_state.swim_speed", row["other_speeds"].get("swim", 0)),
              _set("actor_state.resources.hit_die", row["source_hit_dice"]),
              _set("actor_state.resource_maxima", {"hit_die": row["source_hit_dice"]}),
              _set("actor_state.hit_die_size", row["source_hit_die_size"]),
              _set("actor_state.constitution_modifier",
                   "(_familiar_form.ability_scores.constitution - 10) // 2"),
          ]) for key, row in forms.items()],
    ])
    create = _tool_scene("Create one source-owned Familiar after its actual long casting.", [
        _entity("caster_state"), *deepcopy(FAMILIAR_CAST_CHOICES),
        *deepcopy(ACTOR_PLACEMENT_CHOICES),
    ], [
        _require("combat.encounter_active != true",
                 "Joining an already active encounter with independent Initiative is unsupported."),
        _require("len(created_individual_id) > 0", "Choose the Familiar's stable individual ID."),
        _when("state.created_actor_ids == null", [
            _set("state.created_actor_ids", []), _set("state.created_actor_records", {}),
            _set("state.created_actor_dropped_items", []),
        ]),
        _set("_familiar_returning", "created_individual_id in state.created_actor_ids"),
        _require("_familiar_returning == return_created_individual",
                 "Return only this Familiar's original saved individual."),
        _when("caster_state.created_familiar_id != null", [
            _require("created_individual_id == caster_state.created_familiar_id and "
                     "_familiar_returning == true",
                     "One Familiar only: reuse the existing individual's identity."),
        ]),
        _when("_familiar_returning == true", [
            _set("_familiar_prior", "state.created_actor_records[created_individual_id]"),
            _require("_familiar_prior.caster == _caster_state_id and "
                     "_familiar_prior.spell == 'find_familiar' and "
                     "_familiar_prior.active != true and "
                     "_familiar_prior.disappearance == 'zero_hit_points'",
                     "Only the original Familiar lost at zero HP can return by casting; "
                     "active form adoption and pocket reappearance use their own source behavior."),
            _call("remove_srd_inactive_created_scene", {
                "caster_state": "{_caster_state_id}", "actor_id": "{created_individual_id}",
                "spell": "find_familiar",
            }),
        ]),
        {"table_lookup": {"table": "definitions.beast_forms", "key": "familiar_form_id",
                          "required": True}, "store_as": "_familiar_form"},
        _set("_familiar_half_extent", 1.25),
        _when("_familiar_form.size_rank == 1", [_set("_familiar_half_extent", 2.5)]),
        _call("create_srd_source_actor_scene", {
            "caster_state": "{_caster_state_id}", "summon_id": "{created_individual_id}",
            "spell": "find_familiar", "range_feet": 10,
            "half_extent": "{_familiar_half_extent}", "creature_type": "{created_creature_type}",
            "requires_sight": False,
            **{p["name"]: "{" + p["name"] + "}" for p in ACTOR_PLACEMENT_CHOICES},
        }),
        _call("prepare_srd_source_familiar", {
            "creature_id": "{created_individual_id}", "form_id": "{familiar_form_id}",
            "source_caster": "{_caster_state_id}", "creature_type": "{created_creature_type}",
            "returning": "{_familiar_returning}",
        }),
        _call("finish_srd_familiar_source_actor", {
            "actor_state": "{created_individual_id}", "caster_state": "{_caster_state_id}",
            "form_id": "{familiar_form_id}",
        }),
        _set("_familiar_registry", "state.created_actor_ids"),
        _when("_familiar_returning != true", [
            _push("_familiar_registry", "created_individual_id"),
        ]),
        _set("state.created_actor_ids", "_familiar_registry"),
        _set("state.created_actor_records.{created_individual_id}", {
            "caster": "_caster_state_id", "spell": "'find_familiar'",
            "individual": "created_individual_id", "active": True, "disappearance": "''",
            "spell_level": 1, "source_page": "_familiar_form.source.page",
        }),
        _set("state.has_created_actors", True),
        _set("caster_state.created_familiar_id", "created_individual_id"),
        _set("_spell_summary_rows", [
            {"label": "Spell", "value": "'find_familiar'"},
            {"label": "Individual", "value": "created_individual_id"},
        ]),
        _set("_spell_detail_rows", [
            {"label": "Actual source form", "value": "_familiar_form.name"},
            {"label": "Source page", "value": "_familiar_form.source.page"},
            {"label": "Returned individual", "value": "_familiar_returning"},
        ]),
    ])
    return {"prepare_srd_source_familiar": prepare, "finish_srd_familiar_source_actor": finish,
            "cast_spell_familiar_created_creature": create}


def _familiar_actor_hooks(tools):
    """Bind only this printed creation family, leaving ordinary actor preparation unchanged."""
    changed = _familiar_actor_tools(tools)
    for name in ("cast_spell", "cast_spell_node", "cast_spell_outside_combat"):
        if name not in tools:
            continue
        cast = deepcopy(tools[name])
        names = {p["name"] for p in cast["parameters"]}
        choices = FAMILIAR_CAST_CHOICES + ACTOR_PLACEMENT_CHOICES
        cast["parameters"] += [deepcopy(p) for p in choices if p["name"] not in names]
        branch = _when("_spell_definition.family == 'familiar_created_creature'", [
            _call("cast_spell_familiar_created_creature", {
                "caster_state": "{_caster_state_id}",
                **{p["name"]: "{" + p["name"] + "}" for p in choices},
            }, ("_spell_summary_rows", "_spell_detail_rows")),
        ])
        _replace_created_cast_dispatch(cast, "familiar_created_creature", branch)
        changed[name] = cast
    return changed



def _familiar_dodge_tools(tools):
    """Retain the real Dodge action while qualifying the Familiar as an independent NPC."""
    dodge = deepcopy(tools["take_dodge_action"])
    dodge["description"] = "Take the ordinary Dodge action on this actual Familiar's own turn."
    dodge["parameters"] = [p for p in dodge["parameters"] if p["name"] != "actor"]
    dodge["parameters"].append(_optional("lasting_state", resolve="entity", default="state"))
    for requirement in dodge["pre_conditions"]:
        if requirement["condition"] == "actor.validation_status == 'validated'":
            requirement["condition"] = (
                "actor_state.created_spell_id == 'find_familiar' and "
                "actor_state.created_active == true and actor_state.is_alive == true "
                "and actor_state.hp > 0"
            )
            requirement["error"] = "Only an actual present Familiar can use this source action."
        requirement["condition"] = requirement["condition"].replace(
            "actor.participant_id", "_actor_state_id")
    dodge["effects"].insert(0, _call("qualify_srd_familiar_source", {
        "caster_state": "{actor_state.created_caster_state_id}",
        "familiar_state": "{_actor_state_id}",
    }))
    dodge["effects"].append(_set("_familiar_present", True))
    actor_row = dodge["metadata"]["result_card"]["summary"][0]
    actor_row["value"] = {"path": "context.actor_state.name"}
    return {"dodge_srd_familiar": dodge}


def _familiar_communication_tools():
    """Resolve the printed telepathic link from the actual saved individual and scene positions."""
    communicate = _public(_tool_scene(
        "Communicate with your present Familiar through its actual 100-foot telepathic link.",
        [_entity("caster_state"), _entity("familiar_state"), _parameter("communication"),
         _optional("from_familiar", "boolean", False)],
        [
            _require(PENDING, "Complete the pending action before communicating."),
            _call("qualify_srd_familiar_source", {
                "caster_state": "{_caster_state_id}", "familiar_state": "{_familiar_state_id}",
            }),
            _require("familiar_state.created_active == true and familiar_state.is_alive == true "
                     "and familiar_state.hp > 0 and caster_state.is_alive == true "
                     "and caster_state.hp > 0",
                     "The telepathic link needs both actual present living creatures."),
            _require("'unconscious' not in caster_state.conditions and "
                     "'unconscious' not in familiar_state.conditions",
                     "An Unconscious creature cannot exchange intentional telepathic messages."),
            *_scene_position("{_caster_state_id}", "_familiar_link_origin"),
            _set("_familiar_link_plane", "_info_scene_entity.metadata.plane"),
            *_scene_position("{_familiar_state_id}", "_familiar_link_destination"),
            _require("_familiar_link_plane != null and "
                     "_info_scene_entity.metadata.plane == _familiar_link_plane",
                     "A Familiar's distance-bounded link needs the same actual plane."),
            _call("measure_srd_information_distance", {
                "start": "{_familiar_link_origin}", "end": "{_familiar_link_destination}",
            }, ("_scene_distance",)),
            _require("_scene_distance <= 100", "Your Familiar must be within 100 feet."),
            _require("communication != ''", "Supply the actual intended communication."),
            _choose("from_familiar == true", [
                _set("caster_state.received_familiar_communication", {
                    "caster": "_caster_state_id", "sender": "_familiar_state_id",
                    "communication": "communication", "tick": "state.lasting_tick",
                }),
            ], [
                _set("familiar_state.received_familiar_communication", {
                    "caster": "_caster_state_id", "sender": "_caster_state_id",
                    "communication": "communication", "tick": "state.lasting_tick",
                }),
            ]),
            _set("_familiar_understood", True),
        ],
    ), "exploration", "Familiar communication", [("Understood", "_familiar_understood")])
    return {"communicate_srd_familiar": communicate}

def _familiar_disappearance_tools():
    """Pocket disappearance preserves the individual and its current body statistics."""
    qualify = _tool("Qualify this caster's real saved Familiar and its current source registry.", [
        _entity("caster_state"), _entity("familiar_state"),
    ], [
        _require("familiar_state.created_spell_id == 'find_familiar' and "
                 "familiar_state.created_caster_state_id == _caster_state_id and "
                 "familiar_state.created_individual_id == _familiar_state_id and "
                 "caster_state.created_familiar_id == _familiar_state_id",
                 "Only this caster's actual saved Familiar can use the source action."),
        _require("_familiar_state_id in state.created_actor_ids",
                 "The Familiar needs its original source registry."),
        _set("_familiar_record", "state.created_actor_records[_familiar_state_id]"),
        _require("_familiar_record.caster == _caster_state_id and "
                 "_familiar_record.spell == 'find_familiar' and "
                 "_familiar_record.individual == _familiar_state_id and "
                 "_familiar_record.active == familiar_state.created_active and "
                 "_familiar_record.disappearance == familiar_state.created_disappearance",
                 "The actual body and saved Familiar source must agree."),
    ])
    dismiss = _public(_tool_scene(
        "Use a Magic action to dismiss your Familiar temporarily or forever.", [
        _entity("caster_state"), _entity("familiar_state"),
        _optional("forever", "boolean", False),
    ], [
        _call("qualify_srd_familiar_source", {
            "caster_state": "{_caster_state_id}", "familiar_state": "{_familiar_state_id}",
        }),
        _require("familiar_state.created_active == true or "
                 "(forever == true and (familiar_state.created_disappearance == 'pocket' or "
                 "familiar_state.created_disappearance == 'zero_hit_points'))",
                 "Temporary dismissal needs the present Familiar; zero HP requires recasting."),
        _require("familiar_state.form_statistics_replaced != true and "
                 "familiar_state.form_equipment_melded != true and "
                 "familiar_state.worn_armor == null and familiar_state.worn_shield == null",
                 "Transformed, melded or worn-gear pocket bodies need their actual owners."),
        {"table_lookup": {"table": "definitions.beast_forms",
                          "key": "familiar_state.created_form_id", "required": True},
         "store_as": "_familiar_pocket_source"},
        _require("familiar_state.size_rank == _familiar_pocket_source.size_rank and "
                 "(familiar_state.size_rank == 0 or familiar_state.size_rank == 1)",
                 "Resized pocket bodies need their actual source geometry owner."),
        *_action(),
        _when("familiar_state.created_active == true", [
            *_scene_position("{_familiar_state_id}", "_familiar_dismissed_position"),
            _require("_info_scene_entity.footprint.kind == 'box'",
                     "Familiar pocket reappearance currently needs an actual box body."),
            _set("familiar_state.created_pocket_height", "_info_scene_entity.height"),
            _set("familiar_state.created_pocket_size_rank", "familiar_state.size_rank"),
            _set("_familiar_disappearance", "'pocket'"),
            _when("forever == true", [_set("_familiar_disappearance", "'forever'")]),
            _call("retire_srd_created_actor", {
                "actor_state": "{_familiar_state_id}", "reason": "{_familiar_disappearance}",
            }),
        ]),
        _when("forever == true", [
            _set("familiar_state.created_disappearance", "'forever'"),
            _set("state.created_actor_records.{_familiar_state_id}.disappearance", "'forever'"),
            _set("caster_state.created_familiar_id", None),
        ]),
        _set("_familiar_present", False),
    ]), "spells", "Familiar disappearance", [("Present", "_familiar_present")])
    reappear = _public(_tool_scene(
        "Use a Magic action to return the pocket Familiar to unoccupied space within 30 feet.",
        [_entity("caster_state"), _entity("familiar_state"),
         _array("position"), _optional("lighting", "enum", "bright", enum_values=[
             "bright", "dim", "darkness", "magical_darkness"])],
        [
            _call("qualify_srd_familiar_source", {
                "caster_state": "{_caster_state_id}", "familiar_state": "{_familiar_state_id}",
            }),
            _require("familiar_state.created_active != true and "
                     "familiar_state.created_disappearance == 'pocket' and familiar_state.hp > 0",
                     "Only your temporarily pocketed Familiar can reappear without recasting."),
            _require("familiar_state.form_statistics_replaced != true and "
                     "familiar_state.size_rank == familiar_state.created_pocket_size_rank",
                     "A changed pocket body needs its source geometry owner before reappearing."),
            _when("combat.encounter_active == true", [
                _require("combat.group_order == true",
                         "Pocket reappearance needs the actual independent initiative owner."),
                _set("_familiar_in_order", False),
                _set("_familiar_order", "combat.order"),
                _loop("_familiar_order", "_familiar_entry", [
                    _when("_familiar_entry.entity_id == _familiar_state_id", [
                        _set("_familiar_in_order", True),
                    ]),
                ]),
                _require("_familiar_in_order == true",
                         "Joining combat needs its real initiative insertion owner."),
            ]),
            *_action(),
            _call("remove_srd_inactive_created_scene", {
                "caster_state": "{_caster_state_id}", "actor_id": "{_familiar_state_id}",
                "spell": "find_familiar",
            }),
            _set("_familiar_half_extent", 1.25),
            _when("familiar_state.size_rank == 1", [_set("_familiar_half_extent", 2.5)]),
            _require("familiar_state.size_rank == 0 or familiar_state.size_rank == 1",
                     "This source return currently owns ordinary Tiny or Small Familiar bodies."),
            _call("create_srd_source_actor_scene", {
                "caster_state": "{_caster_state_id}", "summon_id": "{_familiar_state_id}",
                "spell": "find_familiar", "range_feet": 30,
                "half_extent": "{_familiar_half_extent}",
                "creature_type": "{familiar_state.creature_type}", "requires_sight": False,
                "created_actor_position": "{position}",
                "created_actor_height": "{familiar_state.created_pocket_height}",
                "created_actor_lighting": "{lighting}",
            }),
            _set("familiar_state.created_active", True),
            _set("familiar_state.created_disappearance", "''"),
            _set("familiar_state.is_alive", True),
            _set("familiar_state.body_available", True),
            _set("familiar_state.vital_parts_present", True),
            _set("state.created_actor_records.{_familiar_state_id}.active", True),
            _set("state.created_actor_records.{_familiar_state_id}.disappearance", "''"),
            _set("_familiar_present", True),
        ],
    ), "spells", "Familiar reappearance", [("Present", "_familiar_present")])
    return {"qualify_srd_familiar_source": qualify, "dismiss_srd_familiar": dismiss,
            "reappear_srd_familiar": reappear}


def _created_actor_attack_geometry_tool():
    """Use real source and target boxes for fixed reach, physical path and categorical Cover."""
    return _tool_scene(
        "Qualify the Steed's actual body reach without accepting a caller distance verdict.",
        [_entity("actor_state"), _entity("target_state")],
        _scene_position("{_actor_state_id}", "_created_attack_origin")
        + [_set("_created_attacker_scene", "_info_scene_entity")]
        + _scene_position("{_target_state_id}", "_created_attack_target_origin")
        + [
            _require(
                "_created_attacker_scene.footprint.kind == 'box' and "
                "_info_scene_entity.footprint.kind == 'box'",
                "Created actor reach currently requires actual box bodies.",
            ),
            _require(
                "_created_attacker_scene.metadata.plane != null and "
                "_created_attacker_scene.metadata.plane == _info_scene_entity.metadata.plane",
                "The source attack cannot reach another plane.",
            ),
            *[
                _set(
                    "_created_gap_" + str(axis),
                    "max(0, abs(_created_attack_origin[" + str(axis) + "] - "
                    "_created_attack_target_origin[" + str(axis) + "]) - "
                    "_created_attacker_scene.footprint.half_extents[" + str(axis) + "] - "
                    "_info_scene_entity.footprint.half_extents[" + str(axis) + "])",
                )
                for axis in range(2)
            ],
            _set(
                "_created_gap_2",
                "max(0, _created_attack_origin[2] - "
                "(_created_attack_target_origin[2] + _info_scene_entity.height), "
                "_created_attack_target_origin[2] - "
                "(_created_attack_origin[2] + _created_attacker_scene.height))",
            ),
            _set(
                "_created_attack_distance",
                "ceil((_created_gap_0 ** 2 + _created_gap_1 ** 2 + _created_gap_2 ** 2) ** 0.5)",
            ),
            _set(
                "_created_attack_eye",
                [
                    "_created_attack_origin[0]",
                    "_created_attack_origin[1]",
                    "_created_attack_origin[2] + _created_attacker_scene.height * 0.5",
                ],
            ),
            _set("_created_attack_targets", ["_target_state_id"]),
            _call(
                "collect_srd_spell_area_cover_targets",
                {
                    "source_state": "{_actor_state_id}",
                    "targets": "{_created_attack_targets}",
                    "origin": "{_created_attack_eye}",
                },
                ("_srd_area_targets", "_srd_area_half_cover", "_srd_area_three_quarters"),
            ),
            _require(
                "_target_state_id in _srd_area_targets",
                "The Steed cannot attack through Total Cover.",
            ),
            _require(
                "len(_srd_area_half_cover) == 0 and len(_srd_area_three_quarters) == 0",
                "The Steed's partial Cover attack consumer is not yet supported.",
            ),
        ],
    )


def _independent_actor_hooks(tools):
    """Bind fixed creation, disappearance and shared initiative without a concentration source."""
    changed = _independent_actor_tools(tools)
    for name in ("cast_spell", "cast_spell_node", "cast_spell_outside_combat"):
        if name not in tools:
            continue
        cast = deepcopy(tools[name])
        names = {p["name"] for p in cast["parameters"]}
        cast["parameters"] += [
            deepcopy(p)
            for p in INDEPENDENT_CAST_CHOICES + ACTOR_PLACEMENT_CHOICES
            if p["name"] not in names
        ]
        cast["effects"] = [
            e
            for e in cast["effects"]
            if e.get("condition") != "_spell_definition.family == 'independent_created_creature'"
        ]
        branch = _when(
            "_spell_definition.family == 'independent_created_creature'",
            [
                _call(
                    "cast_spell_independent_created_creature",
                    {
                        "caster_state": "{_caster_state_id}",
                        "slot_level": "{_slot_level}",
                        "casting_attack_bonus": "{_casting_profile.attack_bonus}",
                        "casting_save_dc": "{_casting_profile.save_dc}",
                        "casting_proficiency_bonus": "{_casting_profile.proficiency_bonus}",
                        **{
                            p["name"]: "{" + p["name"] + "}"
                            for p in INDEPENDENT_CAST_CHOICES + ACTOR_PLACEMENT_CHOICES
                        },
                    },
                    ("_spell_summary_rows", "_spell_detail_rows"),
                ),
            ],
        )
        index = max(
            i
            for i, e in enumerate(cast["effects"])
            if "_spell_definition.family ==" in e.get("condition", "")
        )
        cast["effects"].insert(index + 1, branch)
        changed[name] = cast
    classify = deepcopy(tools["classify_srd_summon_initiative_entry"])
    classify["effects"] = [e for e in classify["effects"] if "created_spell_id" not in str(e)]
    classify["effects"] = [
        e
        for e in classify["effects"]
        if e.get("set", {}).get("field") != "_initiative_linked_steed"
    ]
    classify["effects"].append(_set("_initiative_linked_steed", "''"))
    classify["effects"].append(
        _when(
            "creature_state.created_steed_id != null",
            [_set("_initiative_linked_steed", "creature_state.created_steed_id")],
        )
    )
    classify["effects"].append(
        _when(
            "creature_state.created_spell_id == 'find_steed' and "
            "creature_state.created_active == true",
            [
                _set("_initiative_is_summon", True),
                _set("_initiative_summon_owner", "creature_state.created_caster_state_id"),
            ],
        )
    )
    changed["classify_srd_summon_initiative_entry"] = classify
    organize = deepcopy(tools["organize_srd_summons_in_initiative"])
    for effect in _walk(organize["effects"]):
        if effect.get("call") == "classify_srd_summon_initiative_entry":
            effect["export_context"]["_initiative_linked_steed"] = "_initiative_linked_steed"
        if effect.get("condition") == "_initiative_is_summon == true":
            effect["on_false"] = [
                e
                for e in effect["on_false"]
                if e.get("condition") != "_initiative_linked_steed != ''"
            ]
            effect["on_false"].append(
                _when(
                    "_initiative_linked_steed != ''",
                    [
                        _require(
                            "_initiative_linked_steed in combatants",
                            "Include the caster's actual Steed in group Initiative.",
                        ),
                        _call(
                            "read_srd_summon_initiative_label",
                            {"summon_state": "{_initiative_linked_steed}"},
                            ("_initiative_summon_name",),
                        ),
                        _push(
                            "_initiative_order_with_summons",
                            {
                                "entity_id": "_initiative_linked_steed",
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
    changed["qualify_srd_created_actor_attack_geometry"] = _created_actor_attack_geometry_tool()
    attack = deepcopy(tools["resolve_adjudicated_creature_attack"])
    attack["pre_conditions"] = [
        p for p in attack["pre_conditions"] if "created_spell_id" not in p["condition"]
    ]
    attack["pre_conditions"] += [
        {
            "condition": "actor.created_spell_id == null or actor.created_active == true",
            "error": "A disappeared source creature cannot attack.",
        },
        {
            "condition": "actor.created_spell_id != 'find_familiar'",
            "error": "A Familiar cannot attack.",
        },
        {
            "condition": "actor.created_spell_id != 'find_steed' or "
            "actor.form_attack_in_progress == true or (attack_name == 'Otherworldly Slam' and "
            "attack_modifier == actor.created_attack_bonus and "
            "damage_expression == 'd8' and damage_modifier == actor.created_spell_level and "
            "damage_type == actor.created_attack_damage_type and attack_mode == 'melee' and "
            "normal_range == 5 and long_range == 5 and use_multiattack != true)",
            "error": "The Steed must use its actual saved Otherworldly Slam source profile.",
        },
    ]
    attack["effects"] = [
        e for e in attack["effects"] if "qualify_srd_created_actor_attack_geometry" not in str(e)
    ]
    attack["effects"].insert(
        0,
        _when(
            "actor.created_spell_id == 'find_steed' and actor.form_attack_in_progress != true",
            [
                _call(
                    "qualify_srd_created_actor_attack_geometry",
                    {
                        "actor_state": "{_actor_id}",
                        "target_state": "{_target_id}",
                    },
                    ("_created_attack_distance",),
                ),
                _require(
                    "attack_distance == _created_attack_distance",
                    "Use the actual source body distance.",
                ),
            ],
        ),
    )
    changed["resolve_adjudicated_creature_attack"] = attack
    death = deepcopy(tools["record_srd_actual_death"])
    death["effects"] = [e for e in death["effects"] if "retire_srd_created_actor" not in str(e)]
    death["effects"].append(
        _when(
            "target_state.is_alive == false",
            [
                _when(
                    "target_state.created_active == true and "
                    "(target_state.created_spell_id == 'find_familiar' or "
                    "target_state.created_spell_id == 'find_steed')",
                    [
                        _call(
                            "retire_srd_created_actor",
                            {"actor_state": "{_target_state_id}", "reason": "zero_hit_points"},
                        ),
                    ],
                ),
                _when(
                    "target_state.created_steed_id != null",
                    [
                        _call(
                            "retire_srd_created_actor",
                            {
                                "actor_state": "{target_state.created_steed_id}",
                                "reason": "caster_died",
                            },
                        ),
                    ],
                ),
            ],
        )
    )
    changed["record_srd_actual_death"] = death
    return changed


def extend_runtime(text):
    """Compose fixed force profiles through existing casting, damage, scene and clock owners."""
    tools = _load_runtime(text)["tools"]
    changed = (
        _proxy_tools() | _record_tools() | _casting_tools() | _command_tools() | _attack_tools()
    )
    changed["qualify_srd_scene_enemy"] = _scene_enemy_tool()
    changed.update(_dragon_language_hooks(tools))
    leaf = deepcopy(tools["cast_spell_attack_damage"])
    if not any(p["name"] == "flat_damage" for p in leaf["parameters"]):
        leaf["parameters"].append(_optional("flat_damage", "integer", 0))
    if not any(p["name"] == "force_source_id" for p in leaf["parameters"]):
        leaf["parameters"].append(_optional("force_source_id", default=""))
    force_source = (
        "force_source_id != '' and lasting_state.spell_force_records[force_source_id] != null and "
        "lasting_state.spell_force_records[force_source_id].active == true and "
        "lasting_state.spell_force_records[force_source_id].caster == _caster_state_id and "
        "lasting_state.spell_force_records[force_source_id].spell == spell"
    )
    for precondition in leaf["pre_conditions"]:
        if (
            "caster_state.melee_reach" in precondition["condition"]
            and "force_source_id" not in precondition["condition"]
        ):
            precondition["condition"] = (
                "("
                + precondition["condition"]
                + ") or (force_source_id != '' and target_distance <= 5)"
            )
        if precondition["condition"] == "spell in caster_state.prepared_spells":
            precondition["condition"] += " or force_source_id != ''"
    if "Validate the saved force attack source" not in str(leaf["effects"]):
        leaf["effects"].insert(
            0,
            _require(
                "force_source_id == '' or (" + force_source + ")",
                "Validate the saved force attack source before resolving its attack.",
            ),
        )
    for effect in _walk(leaf["effects"]):
        setting = effect.get("set", {})
        if setting.get("value") == "max(0, _spell_damage_amount + 0)":
            setting["value"] = "max(0, _spell_damage_amount + flat_damage)"
    changed["cast_spell_attack_damage"] = leaf
    cast = deepcopy(tools["cast_spell"])
    previous = {p["name"] for p in cast["parameters"]}
    cast["parameters"] += [deepcopy(p) for p in CAST_CHOICES if p["name"] not in previous]
    cast["effects"] = [
        e
        for e in cast["effects"]
        if e.get("condition") != "_spell_definition.family == 'created_spell_force'"
    ]
    params = {p["name"]: "{" + p["name"] + "}" for p in CAST_CHOICES}
    params["force_target"] = "{_force_target_id}"
    params.update(
        caster_state="{_caster_state_id}",
        spell="{spell}",
        slot_level="{_slot_level}",
        casting_ability="{_casting_profile.ability}",
        casting_modifier="{_casting_profile.modifier}",
        casting_attack_bonus="{_casting_profile.attack_bonus}",
        casting_save_dc="{_casting_profile.save_dc}",
        casting_scaling_level="{_casting_profile.scaling_level}",
        concentration_modifier="{_casting_profile.concentration_save_modifier}",
    )
    branch = _when(
        "_spell_definition.family == 'created_spell_force'",
        [
            _call(
                "cast_spell_created_spell_force",
                params,
                ("_spell_summary_rows", "_spell_detail_rows"),
            )
        ],
    )
    branch["on_true"].insert(0, _when("force_target == null", [_set("_force_target_id", None)]))
    index = max(
        i
        for i, e in enumerate(cast["effects"])
        if "_spell_definition.family ==" in e.get("condition", "")
    )
    cast["effects"].insert(index + 1, branch)
    changed["cast_spell"] = cast
    changed.update(_dragon_placement_hooks(tools | changed))
    changed.update(_guardian_hooks(tools | changed))
    changed.update(_dragon_breath_hooks(tools | changed))
    changed.update(_hound_hooks(tools | changed))
    changed.update(_hound_bite_tools())
    changed.update(_hound_bite_turn_hooks(tools | changed))
    changed.update(_independent_actor_hooks(tools | changed))
    changed.update(_familiar_actor_hooks(tools | changed))
    changed.update(_familiar_disappearance_tools())
    changed.update(_familiar_dodge_tools(tools | changed))
    changed.update(_familiar_communication_tools())
    changed.update(_zombie_actor_tools(tools | changed))
    changed.update(_undead_fortitude_tools())
    changed.update(_created_world_time_tools())
    changed.update(_animated_actor_hooks(tools | changed))
    changed.update(_animated_command_tools(tools | changed))
    changed.update(_animated_turn_hooks(tools | changed))
    changed.update(_animated_attack_hooks(tools | changed))
    changed.update(_created_undead_hooks(tools | changed))
    changed.update(_created_undead_attack_hooks(tools | changed))
    changed.update(_created_ghoul_claw_hooks(tools | changed))
    changed.update(_created_undead_control_hooks(tools | changed))
    changed.update(_created_ghast_stench_hooks(tools | changed))
    changed.update(_insect_actor_hooks(tools | changed))
    changed.update(_insect_attack_hooks(tools | changed))
    changed.update(_insect_turn_hooks(tools | changed))
    clock = deepcopy((tools | changed)["advance_srd_lasting_clock"])
    if "refresh_srd_spell_forces" not in str(clock["effects"]):
        clock["effects"].append(
            _when("lasting_state.has_spell_forces == true", [_call("refresh_srd_spell_forces", {})])
        )
    changed["advance_srd_lasting_clock"] = clock
    end = deepcopy(tools["end_srd_concentration_node"])
    end["effects"] = [
        e
        for e in end["effects"]
        if e.get("condition") != "source_state.concentration_effect_family == 'created_spell_force'"
    ]
    end["effects"].insert(
        0,
        _when(
            "source_state.concentration_effect_family == 'created_spell_force'",
            [
                _call(
                    "remove_srd_spell_force",
                    {"force_id": "{source_state.concentration_spell_force_id}"},
                ),
                _set("source_state.concentration_spell_force_id", None),
            ],
        ),
    )
    changed["end_srd_concentration_node"] = end
    damage = deepcopy(tools["apply_srd_damage"])
    if "spell_force_active" not in str(damage["effects"]):
        zero_hp = next(
            e
            for e in damage["effects"]
            if e.get("condition") == "_hp_before > 0 and target.hp == 0"
        )
        zero_hp["on_true"].append(
            _when(
                "target.spell_force_active == true and target.hp == 0",
                [_call("remove_srd_spell_force", {"force_id": "{target}"})],
            )
        )
    changed["apply_srd_damage"] = damage
    _normalize(changed)
    # Public wrappers use this family's explicit selection rather than the divination registry.
    for name in PUBLIC_TOOLS:
        tool = changed[name]
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
            + ("_" + p["name"] + "_id" if p.get("resolve") == "entity" else p["name"])
            + "}"
            for p in tool["parameters"]
        }
        params["lasting_state"] = "state"
        tool["effects"] = [
            _when("force_target == null", [_set("_force_target_id", None)]),
            _call(name + "_node", params, ("_force_command_spell", "_force_command_ended")),
        ]
    tool = changed["record_srd_map_time_of_day"]
    node = deepcopy(tool)
    node["metadata"] = {"category": "internal", "visible_to_player": False, "costs_action": False}
    changed["record_srd_map_time_of_day_node"] = node
    tool["parameters"] = [p for p in tool["parameters"] if p["name"] != "lasting_state"]
    tool["effects"] = [_call("record_srd_map_time_of_day_node", {
        "plane": "{plane}", "phase": "{phase}", "lasting_state": "state",
    }, ("_world_time_map", "_world_time_plane", "_world_time_phase", "_world_time_tick"))]
    for name in HOUND_PUBLIC_TOOLS:
        tool = changed[name]
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
            + ("_" + p["name"] + "_id" if p.get("resolve") == "entity" else p["name"])
            + "}"
            for p in tool["parameters"]
        }
        params["lasting_state"] = "state"
        exports = (
            ("_hound_visible",)
            if name == "observe_srd_faithful_hound"
            else ("_hound_active", "_hound_position")
        )
        tool["effects"] = [_call(name + "_node", params, exports)]
    for name in FAMILIAR_PUBLIC_TOOLS:
        tool = changed[name]
        node = deepcopy(tool)
        node["metadata"] = {
            "category": "internal", "visible_to_player": False, "costs_action": False,
        }
        changed[name + "_node"] = node
        tool["parameters"] = [p for p in tool["parameters"] if p["name"] != "lasting_state"]
        params = {
            p["name"]: "{" + ("_" + p["name"] + "_id" if p.get("resolve") == "entity"
                              else p["name"]) + "}"
            for p in tool["parameters"]
        }
        params["lasting_state"] = "state"
        exports = ("_familiar_understood",) if name == "communicate_srd_familiar" else (
            "_familiar_present",)
        tool["effects"] = [_call(name + "_node", params, exports)]
    text = _replace_tools(text, changed)
    properties = {
        k: {"type": "boolean" if type(v) is bool else "integer" if type(v) is int else "string"}
        for k, v in GUARDIAN_PROFILE.items()
        if k != "family"
    }
    guardian_schema = {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }
    hound_properties = {
        k: {"type": "boolean" if type(v) is bool else "integer" if type(v) is int else "string"}
        for k, v in HOUND_PROFILE.items()
        if k != "family"
    }
    hound_schema = {
        "type": "object",
        "properties": hound_properties,
        "required": list(hound_properties),
        "additionalProperties": False,
    }
    steed_properties = {
        k: {"type": "boolean" if type(v) is bool else "integer" if type(v) is int else "string"}
        for k, v in STEED_PROFILE.items() if k != "family"
    }
    steed_schema = {
        "type": "object", "properties": steed_properties,
        "required": list(steed_properties), "additionalProperties": False,
    }
    familiar_properties = {
        k: {"type": "boolean" if type(v) is bool else "integer" if type(v) is int else "string"}
        for k, v in FAMILIAR_PROFILE.items() if k != "family"
    }
    familiar_schema = {
        "type": "object", "properties": familiar_properties,
        "required": list(familiar_properties), "additionalProperties": False,
    }
    animated_properties = {
        k: {"type": "boolean" if type(v) is bool else "integer" if type(v) is int else "string"}
        for k, v in ANIMATED_PROFILE.items() if k != "family"
    }
    animated_schema = {
        "type": "object", "properties": animated_properties,
        "required": list(animated_properties), "additionalProperties": False,
    }
    insect_properties = {
        k: {"type": "boolean" if type(v) is bool else "integer" if type(v) is int else "string"}
        for k, v in GIANT_INSECT_PROFILE.items() if k != "family"
    }
    insect_schema = {
        "type": "object", "properties": insect_properties,
        "required": list(insect_properties), "additionalProperties": False,
    }
    created_undead_properties = {
        k: {"type": "boolean" if type(v) is bool else "integer" if type(v) is int else "string"}
        for k, v in CREATED_UNDEAD_PROFILE.items() if k != "family"
    }
    created_undead_schema = {
        "type": "object", "properties": created_undead_properties,
        "required": list(created_undead_properties), "additionalProperties": False,
    }
    for family, definition in (
        ("created_spell_force", _schema()),
        ("created_spell_guardian", guardian_schema),
        ("created_spell_hound", hound_schema),
        ("independent_created_creature", steed_schema),
        ("familiar_created_creature", familiar_schema),
        ("animated_undead_creature", animated_schema),
        ("created_undead_creature", created_undead_schema),
        ("summoned_insect_creature", insect_schema),
    ):
        if "      " + family + ":" not in text:
            schema = yaml.dump({family: definition}, Dumper=_RuntimeDumper, sort_keys=False)
            text = text.replace(
                "      information_spell:",
                "".join("      " + line for line in schema.splitlines(True))
                + "\n      information_spell:",
                1,
            )
    return text
