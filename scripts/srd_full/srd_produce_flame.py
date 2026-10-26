"""Produce Flame's retained Magic action consumes actual sources, scenes and damage owners."""

from copy import deepcopy

if __package__:
    from .srd_scene_relations import POSITION_EXPORTS, _snapshot, _spatial, _tool_scene
    from .srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _require,
        _set,
        _when,
    )
    from .srd_timed_senses import _loop, _optional, _public, _replace_tools
else:
    from srd_scene_relations import POSITION_EXPORTS, _snapshot, _spatial, _tool_scene
    from srd_timed_actor_defenses import (
        _call,
        _entity,
        _load_runtime,
        _require,
        _set,
        _when,
    )
    from srd_timed_senses import _loop, _optional, _public, _replace_tools


PUBLIC_TOOLS = {"hurl_srd_produce_flame"}
SOURCE_EXPORTS = (
    "_produce_flame_casting_ability",
    "_produce_flame_casting_modifier",
    "_produce_flame_attack_bonus",
    "_produce_flame_scaling_level",
    "_produce_flame_dice_count",
    "_produce_flame_damage_die",
    "_produce_flame_damage_type",
    "_produce_flame_max_range",
    "_produce_flame_origin",
    "_produce_flame_plane",
    "_produce_flame_source_instance",
)
OBJECT_EXPORTS = (
    "_object_attack_roll",
    "_object_attack_total",
    "_object_attack_kind",
    "_object_damage_roll",
    "_object_damage_rolled",
    "_object_damage_applied",
    "_object_hp_before",
    "_object_hp_after",
    "_object_destroyed",
    "_object_effective_armor_class",
    "_object_can_see",
    "_object_advantage",
    "_object_disadvantage",
)


def _nearby_enemy_tools():
    """Use the saved enemy relation and its actual creature's sight within five feet."""
    qualify = _tool_scene(
        "Derive a nearby enemy's ability to interfere with this ranged spell attack.",
        [_entity("caster_state"), _entity("enemy_state")],
        [
            _set("_produce_enemy_qualifies", False),
            _when(
                "enemy_state.is_alive == true and enemy_state.hp > 0 and not "
                "('incapacitated' in enemy_state.conditions or 'paralyzed' in "
                "enemy_state.conditions or 'petrified' in enemy_state.conditions or "
                "'stunned' in enemy_state.conditions or 'unconscious' in enemy_state.conditions)",
                [
                    _call(
                        "qualify_srd_scene_enemy",
                        {"caster_id": "{_caster_state_id}", "target_id": "{_enemy_state_id}"},
                        ["_scene_is_enemy"],
                    ),
                    _when(
                        "_scene_is_enemy == true",
                        [
                            _call(
                                "resolve_srd_scene_relation",
                                {
                                    "actor_state": "{_enemy_state_id}",
                                    "target_state": "{_caster_state_id}",
                                },
                                ["_scene_distance", "_scene_same_plane", "_scene_can_see"],
                            ),
                            _set(
                                "_produce_enemy_qualifies",
                                "_scene_same_plane == true and _scene_distance <= 5 and "
                                "_scene_can_see == true",
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )
    nearby = _tool_scene(
        "Find actual seeing, capable scene enemies within five feet of the source caster.",
        [_entity("caster_state")],
        [
            _call(
                "srd_information_scene_entity",
                {"subject": "{_caster_state_id}"},
                POSITION_EXPORTS,
            ),
            _set("_produce_nearby_origin", "_info_position"),
            _set("_produce_nearby_plane", "_info_scene_entity.metadata.plane"),
            _set("_produce_nearby_disadvantage", False),
            _set("_produce_nearby_rows", "state.spatial_scene.entities"),
            _loop(
                "_produce_nearby_rows",
                "_produce_nearby_row",
                [
                    _when(
                        "_produce_nearby_row.active == true and "
                        "_produce_nearby_row.metadata.participant_ref != null and "
                        "_produce_nearby_row.metadata.participant_ref != _caster_state_id and "
                        "_produce_nearby_row.metadata.plane == _produce_nearby_plane",
                        [
                            _set(
                                "_produce_nearby_squared",
                                "(_produce_nearby_row.position[0] - _produce_nearby_origin[0]) ** "
                                "2 + (_produce_nearby_row.position[1] - "
                                "_produce_nearby_origin[1]) ** 2 + "
                                "(_produce_nearby_row.position[2] - "
                                "_produce_nearby_origin[2]) ** 2",
                            ),
                            _when(
                                "_produce_nearby_squared <= 25",
                                [
                                    _call(
                                        "qualify_srd_produce_flame_close_enemy",
                                        {
                                            "caster_state": "{_caster_state_id}",
                                            "enemy_state": (
                                                "{_produce_nearby_row.metadata.participant_ref}"
                                            ),
                                        },
                                        ["_produce_enemy_qualifies"],
                                    ),
                                    _when(
                                        "_produce_enemy_qualifies == true",
                                        [_set("_produce_nearby_disadvantage", True)],
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
        "qualify_srd_produce_flame_close_enemy": qualify,
        "resolve_srd_produce_flame_nearby_enemies": nearby,
    }


def _creature_attack():
    """Resolve actual cover, conditions, one attack roll and one typed source damage packet."""
    return [
        _require(
            "target.armor_class != null and target.conditions != null and target.hp != null",
            "A creature target needs its admitted combat state.",
        ),
        _call(
            "resolve_srd_scene_relation",
            {"actor_state": "{_caster_state_id}", "target_state": "{_target_id}"},
            [
                "_scene_distance",
                "_scene_same_plane",
                "_scene_line_of_sight",
                "_scene_actor_position",
                "_scene_target_entity_id",
                "_scene_can_see",
            ],
        ),
        _require(
            "_scene_same_plane == true and _scene_distance <= _produce_flame_max_range",
            "Produce Flame's target must be within 60 feet on the caster's plane.",
        ),
        _require(
            "_scene_line_of_sight == true",
            "Produce Flame cannot attack a creature behind total cover.",
        ),
        _set("_produce_attack_distance", "ceil(_scene_distance)"),
        _set("_produce_can_see", "_scene_can_see"),
        _snapshot()[2],
        _spatial(
            "exposure",
            {
                "map_id": "{state.spatial_scene.map.id}",
                "observer": {"point": "{_scene_actor_position}"},
                "target": {"entity_id": "{_scene_target_entity_id}"},
                "samples": {"horizontal": 3, "vertical": 3},
                "include": ["geometry"],
            },
            "_produce_exposure",
        ),
        _set("_produce_cover_bonus", 0),
        _when("_produce_exposure.exposure <= 0.5", [_set("_produce_cover_bonus", 2)]),
        _when("_produce_exposure.exposure <= 0.25", [_set("_produce_cover_bonus", 5)]),
        _call(
            "resolve_srd_scene_relation",
            {"actor_state": "{_target_id}", "target_state": "{_caster_state_id}"},
            ["_scene_can_see"],
        ),
        _set("_produce_target_can_see", "_scene_can_see"),
        _call(
            "resolve_srd_attack_conditions",
            {
                "actor_state": "{_caster_state_id}",
                "target_state": "{_target_id}",
                "attack_distance": "{_produce_attack_distance}",
                "actor_can_see_target": "{_produce_can_see}",
                "target_can_see_actor": "{_produce_target_can_see}",
                "fear_source_in_sight": "{_produce_fear_visible}",
                "attack_kind": "ranged",
            },
            ["_condition_advantage", "_condition_disadvantage", "_condition_critical_hit"],
        ),
        _call(
            "resolve_srd_effective_armor_class",
            {
                "target_state": "{_target_id}",
                "base_armor_class": "{target.armor_class}",
                "lasting_state": "state",
            },
            ["_effective_armor_class"],
        ),
        _set("_produce_armor_class", "_effective_armor_class + _produce_cover_bonus"),
        _set(
            "_produce_disadvantage",
            "_condition_disadvantage == true or _produce_nearby_disadvantage == true",
        ),
        _call(
            "d20_test",
            {
                "kind": "attack_roll",
                "actor_state": "{_caster_state_id}",
                "ability": "{_produce_flame_casting_ability}",
                "ability_modifier": "{_produce_flame_attack_bonus}",
                "proficiency_bonus": 0,
                "proficiency_factor": "none",
                "circumstantial_modifier": 0,
                "target_number": "{_produce_armor_class}",
                "advantage": "{_condition_advantage}",
                "disadvantage": "{_produce_disadvantage}",
                "target_can_see_actor": "{_produce_target_can_see}",
                "fear_source_in_sight": "{_produce_fear_visible}",
            },
            [
                "_d20_test",
                "_attack_outcome",
                "_outcome",
                "_adjusted_total",
                "_effective_advantage",
                "_effective_disadvantage",
            ],
        ),
        _set("_produce_attack", "_d20_test"),
        _set("_produce_attack_total", "_adjusted_total"),
        _set("_produce_attack_kind", "'miss'"),
        _when("_outcome == 'success'", [_set("_produce_attack_kind", "'hit'")]),
        _when(
            "_produce_attack_kind == 'hit' and (_attack_outcome == 'critical_hit' or "
            "_condition_critical_hit == true)",
            [_set("_produce_attack_kind", "'critical_hit'")],
        ),
        _set("_produce_advantage", "_effective_advantage == true"),
        _set("_produce_disadvantage", "_effective_disadvantage == true"),
        _set("_produce_hp_before", "target.hp"),
        _set("_produce_temp_before", "target.temp_hp"),
        _set("_produce_damage", None),
        _set("_produce_damage_rolled", 0),
        _set("_produce_damage_applied", 0),
        _when(
            "_produce_attack_kind != 'miss'",
            [
                {
                    "dice": {
                        "expression": "{_produce_flame_dice_count}d8",
                        "critical": "_produce_attack_kind == 'critical_hit'",
                    },
                    "store_as": "_produce_damage",
                },
                _set("_produce_damage_rolled", "_produce_damage.total"),
                _set("_produce_critical_hit", "_produce_attack_kind == 'critical_hit'"),
                _call(
                    "apply_srd_damage",
                    {
                        "target": "{_target_id}",
                        "amount": "{_produce_damage_rolled}",
                        "adjustment": 0,
                        "damage_type": "fire",
                        "critical_hit": "{_produce_critical_hit}",
                        "source_state_id": "{_caster_state_id}",
                    },
                    ["_effective_damage"],
                ),
                _set("_produce_damage_applied", "_effective_damage"),
            ],
        ),
        _set("_produce_hp_after", "target.hp"),
        _set("_produce_temp_after", "target.temp_hp"),
        _set("_produce_target", "target.name"),
        _when("target.name == null", [_set("_produce_target", "_target_id")]),
    ]


def _object_attack():
    """Delegate object AC, HP, sight and real fire damage to the pinned object owner."""
    return [
        _set(
            "_produce_object_disadvantage",
            "_produce_nearby_disadvantage == true or _produce_fear_visible == true",
        ),
        _call(
            "resolve_srd_spell_object_attack",
            {
                "caster_state": "{_caster_state_id}",
                "object_id": "{object_id}",
                "attack_modifier": "{_produce_flame_attack_bonus}",
                "damage_die": "d8",
                "dice_count": "{_produce_flame_dice_count}",
                "damage_type": "fire",
                "max_range_feet": 60,
                "disadvantage": "{_produce_object_disadvantage}",
            },
            OBJECT_EXPORTS,
        ),
        _set("_produce_target", "object_id"),
        _set("_produce_attack", "_object_attack_roll"),
        _set("_produce_attack_total", "_object_attack_total"),
        _set("_produce_attack_kind", "_object_attack_kind"),
        _set("_produce_armor_class", "_object_effective_armor_class"),
        _set("_produce_can_see", "_object_can_see"),
        _set("_produce_advantage", "_object_advantage"),
        _set("_produce_disadvantage", "_object_disadvantage"),
        _set("_produce_damage", "_object_damage_roll"),
        _set("_produce_damage_rolled", "_object_damage_rolled"),
        _set("_produce_damage_applied", "_object_damage_applied"),
        _set("_produce_hp_before", "_object_hp_before"),
        _set("_produce_hp_after", "_object_hp_after"),
        _set("_produce_temp_before", 0),
        _set("_produce_temp_after", 0),
    ]


def _hurl_tool():
    tool = _tool_scene(
        "Take Produce Flame's later Magic action against an actual creature or prepared object.",
        [
            _entity("caster_state"),
            _optional("caster", resolve="participant"),
            _optional("target", resolve="entity"),
            _optional("object_id", default=""),
        ],
        [
            _require(
                "(target != null and object_id == '') or (target == null and object_id != '')",
                "Choose exactly one creature target or prepared scene object.",
            ),
            _call(
                "prepare_srd_produce_flame_source",
                {"caster_state": "{_caster_state_id}", "caster": "{_caster_id}"},
                SOURCE_EXPORTS,
            ),
            _call(
                "spend_spell_casting_budget",
                {
                    "caster_state": "{_caster_state_id}",
                    "casting_time": "action",
                    "slot_resource": "cantrip",
                },
            ),
            _call(
                "resolve_srd_produce_flame_nearby_enemies",
                {"caster_state": "{_caster_state_id}"},
                ["_produce_nearby_disadvantage"],
            ),
            _set("_produce_fear_visible", False),
            _when(
                "'frightened' in caster_state.conditions",
                [
                    _require(
                        "caster_state.control_fear_active == true",
                        "A frightened source needs its actual saved Fear source visibility.",
                    ),
                    _call(
                        "resolve_srd_control_fear_visibility",
                        {"actor_state": "{_caster_state_id}"},
                    ),
                    _require(
                        "caster_state.control_fear_facts_known == true",
                        "Resolve the actual Fear source before hurling Produce Flame.",
                    ),
                    _set("_produce_fear_visible", "caster_state.control_fear_visible == true"),
                ],
            ),
            {
                "condition": "target != null",
                "on_true": _creature_attack(),
                "on_false": _object_attack(),
            },
            _set("_produce_attack_dice", "_produce_attack.results"),
            _set("_produce_attack_selected", "_produce_attack.total"),
            _set("_produce_attack_modifier", "_produce_attack_total - _produce_attack.total"),
            _set("_produce_damage_dice", []),
            _when(
                "_produce_damage != null",
                [_set("_produce_damage_dice", "_produce_damage.results")],
            ),
        ],
    )
    public = _public(
        tool,
        "combat",
        "Produce Flame",
        [
            ("Target", "_produce_target"),
            ("Attack", "_produce_attack_kind"),
            ("Attack dice", "_produce_attack_dice"),
            ("Attack total", "_produce_attack_total"),
            ("Target AC", "_produce_armor_class"),
            ("Damage dice", "_produce_damage_dice"),
            ("Fire damage", "_produce_damage_applied"),
            ("HP before", "_produce_hp_before"),
            ("HP after", "_produce_hp_after"),
            ("Temp HP before", "_produce_temp_before"),
            ("Temp HP after", "_produce_temp_after"),
            ("Flame source", "_produce_flame_source_instance"),
        ],
    )
    public["metadata"]["result_card"]["equation"] = {
        "die_sides": 20,
        "roll": {"path": "context._produce_attack_selected"},
        "modifier": {"path": "context._produce_attack_modifier"},
        "total": {"path": "context._produce_attack_total"},
    }
    return public


def extend_runtime(text):
    """Add the retained action after illumination, object and scene enemy ownership exists."""
    tools = _load_runtime(text)["tools"]
    required = {
        "prepare_srd_produce_flame_source",
        "qualify_srd_scene_enemy",
        "resolve_srd_spell_object_attack",
    }
    missing = sorted(required - tools.keys())
    if missing:
        raise SystemExit("Produce Flame later actions need owning tools: " + ", ".join(missing))
    tool = _hurl_tool()
    node = deepcopy(tool)
    node["metadata"] = {"category": "internal", "visible_to_player": False, "costs_action": False}
    node["effects"][:0] = [
        _when("caster == null", [_set("_caster_id", None)]),
        _when("target == null", [_set("_target_id", None)]),
    ]
    tool["parameters"] = [p for p in tool["parameters"] if p["name"] != "lasting_state"]
    exports = list(SOURCE_EXPORTS) + [
        "_produce_target",
        "_produce_attack",
        "_produce_attack_kind",
        "_produce_attack_total",
        "_produce_armor_class",
        "_produce_can_see",
        "_produce_advantage",
        "_produce_disadvantage",
        "_produce_damage",
        "_produce_damage_rolled",
        "_produce_damage_applied",
        "_produce_hp_before",
        "_produce_hp_after",
        "_produce_temp_before",
        "_produce_temp_after",
        "_produce_nearby_disadvantage",
        "_produce_attack_dice",
        "_produce_attack_selected",
        "_produce_attack_modifier",
        "_produce_damage_dice",
    ]
    tool["effects"] = [
        _when("caster == null", [_set("_caster_id", None)]),
        _when("target == null", [_set("_target_id", None)]),
        _call(
            "hurl_srd_produce_flame_node",
            {
                "caster_state": "{_caster_state_id}",
                "caster": "{_caster_id}",
                "target": "{_target_id}",
                "object_id": "{object_id}",
                "lasting_state": "state",
            },
            exports,
        ),
    ]
    changed = _nearby_enemy_tools() | {
        "hurl_srd_produce_flame": tool,
        "hurl_srd_produce_flame_node": node,
    }
    return _replace_tools(text, changed)
