"""True Strike reuses one settled weapon attack with an authenticated paid context."""

from copy import deepcopy

if __package__:
    from .srd_scene_hand_occupancy import EXPORTS as HAND_EXPORTS
    from .srd_scene_relations import _snapshot, _spatial, _tool_scene
    from .srd_scene_sight_origins import EXPORTS as SIGHT_EXPORTS
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
    from .srd_timed_senses import _loop, _replace_tools
else:
    from srd_scene_hand_occupancy import EXPORTS as HAND_EXPORTS
    from srd_scene_relations import _snapshot, _spatial, _tool_scene
    from srd_scene_sight_origins import EXPORTS as SIGHT_EXPORTS
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
    from srd_timed_senses import _loop, _replace_tools


PROFILE = {
    "family": "true_strike",
    "casting_time": "action",
    "base_level": 0,
    "range_kind": "self",
    "range_feet": 0,
    "concentration": False,
}
WEAPON_NODE = "resolve_srd_settled_weapon_attack"
HIT_NODE = "apply_srd_true_strike_weapon_hit"
CAST_NODE = "cast_srd_true_strike"


def schema():
    return {
        "type": "object",
        "properties": {
            key: {
                "type": "boolean"
                if isinstance(value, bool)
                else "integer"
                if isinstance(value, int)
                else "string",
                "enum": [value],
            }
            for key, value in PROFILE.items()
            if key != "family"
        },
        "required": [key for key in PROFILE if key != "family"],
        "additionalProperties": False,
    }


def validate_profiles(definitions):
    for spell, definition in definitions.items():
        if definition.get("family") == "true_strike" and (
            spell != "true_strike" or definition != PROFILE
        ):
            raise ValueError("True Strike differs from the pinned instantaneous weapon profile.")


def _lists(effects):
    yield effects
    for effect in effects:
        for key in ("on_true", "on_false"):
            if isinstance(effect.get(key), list):
                yield from _lists(effect[key])
        if "for_each" in effect:
            yield from _lists(effect["for_each"]["do"])


def _private_context():
    return _parameter(
        "_true_strike_context",
        "object",
        required=False,
        model_hidden=True,
        from_state="state.turn_effects",
    )


def _authenticate(actor="actor_state"):
    return _require(
        "_true_strike_context != null and "
        f"_true_strike_context.cast_id == {actor}.last_spell_source_cast_id and "
        f"{actor}.last_spell_source_spell == 'true_strike' and "
        f"_true_strike_context.caster_id == _{actor}_id and "
        "_true_strike_context.weapon == weapon and "
        "_true_strike_context.material.inventory_item == weapon and "
        "_true_strike_context.material.value_cp >= 1 and "
        "'true_strike.1' in _true_strike_context.material.components",
        "True Strike needs its actual committed caster, weapon and retained material receipt.",
    )


def _packet_tool():
    """Coalesce matching Radiant dice before mitigation, using the one existing HP packet."""
    effects = [
        _authenticate(),
        _set("_true_strike_base_amount", "max(0, amount)"),
        _set("_true_strike_debt", "unallocated_roll_penalty"),
        _set("_true_strike_extra_roll", None),
        _set("_true_strike_extra_faces", []),
        _set("_true_strike_extra_raw", 0),
        _set("_divine_favor_roll", None),
        _set("_divine_favor_amount", 0),
        _when(
            "_true_strike_context.extra_dice > 0",
            [
                {
                    "dice": {
                        "expression": "{_true_strike_context.extra_dice}d6",
                        "critical": "critical_hit",
                    },
                    "store_as": "_true_strike_extra_roll",
                },
                _set("_true_strike_extra_faces", "_true_strike_extra_roll.results"),
                _set("_true_strike_extra_raw", "_true_strike_extra_roll.total"),
                _when(
                    "great_weapon_fighting == true",
                    [
                        _loop(
                            "_true_strike_extra_faces",
                            "_true_strike_face",
                            [
                                _set(
                                    "_true_strike_extra_raw",
                                    "_true_strike_extra_raw + max(0, 3 - _true_strike_face)",
                                ),
                            ],
                        )
                    ],
                ),
            ],
        ),
        _when(
            "actor_state.spell_divine_favor == true",
            [
                {
                    "dice": {"expression": "1d4", "critical": "critical_hit"},
                    "store_as": "_divine_favor_roll",
                },
                _set("_divine_favor_amount", "_divine_favor_roll.total"),
                _when(
                    "great_weapon_fighting == true",
                    [
                        _set("_true_strike_favor_faces", "_divine_favor_roll.results"),
                        _loop(
                            "_true_strike_favor_faces",
                            "_true_strike_face",
                            [
                                _set(
                                    "_divine_favor_amount",
                                    "_divine_favor_amount + max(0, 3 - _true_strike_face)",
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        _set("_true_strike_radiant_raw", "_true_strike_extra_raw + _divine_favor_amount"),
        _when(
            "base_damage_was_rolled != true and _true_strike_radiant_raw > 0 and "
            "actor_state.spell_ray_of_enfeeblement == true",
            [
                _call(
                    "modify_srd_actor_raw_damage_roll",
                    {
                        "actor_state": "{_actor_state_id}",
                        "lasting_state": "{_lasting_state_id}",
                        "amount": "{_true_strike_base_amount}",
                    },
                    ["_ray_raw_damage"],
                ),
                _set("_true_strike_base_amount", "max(0, _ray_raw_damage)"),
                _set("_true_strike_debt", "max(0, -_ray_raw_damage)"),
            ],
        ),
        _set(
            "_true_strike_radiant_amount",
            "max(0, _true_strike_radiant_raw - _true_strike_debt)",
        ),
        _set("_true_strike_roll_carry", "max(0, _true_strike_debt - _true_strike_radiant_raw)"),
    ]
    common = {
        "target": "{_target_id}",
        "source_state_id": "{_actor_state_id}",
        "critical_hit": "{critical_hit}",
        "game_damage_roll": True,
        "actual_attack_hit": True,
        "witness_state_ids": "{witness_state_ids}",
        "unallocated_roll_penalty": "{_true_strike_roll_carry}",
        "minor_elementals_damage_type": "{minor_elementals_damage_type}",
        "lasting_state": "{_lasting_state_id}",
    }
    scalar = _call(
        "apply_srd_qualifying_damage",
        common
        | {
            "amount": "{_true_strike_packet_amount}",
            "adjustment": 0,
            "damage_type": "{damage_type}",
        },
    )
    composite = _call(
        "apply_srd_qualifying_composite_damage",
        common
        | {
            "component_1_amount": "{_true_strike_base_amount}",
            "component_1_damage_type": "{damage_type}",
            "component_2_amount": "{_true_strike_radiant_amount}",
            "component_2_damage_type": "radiant",
        },
    )
    scalar["export_context"] = composite["export_context"] = "*"
    effects += [
        {
            "condition": "damage_type == 'radiant' or _true_strike_radiant_raw == 0",
            "on_true": [
                _set(
                    "_true_strike_packet_amount",
                    "_true_strike_base_amount + _true_strike_radiant_amount",
                ),
                scalar,
            ],
            "on_false": [composite],
        }
    ]
    effects.append(_set("_hp_lost", "max(0, _hp_before - _hp_after)"))
    return _tool(
        "Apply the authenticated True Strike weapon hit and all real typed riders once.",
        [
            _entity("actor_state"),
            _entity("target"),
            _parameter("weapon"),
            _private_context(),
            _parameter("amount", "number"),
            _parameter("damage_type"),
            _parameter("lasting_state", resolve="entity", required=False, default="state"),
            _parameter("critical_hit", "boolean"),
            _parameter("unallocated_roll_penalty", "integer", minimum=0),
            _parameter("great_weapon_fighting", "boolean"),
            _parameter("base_damage_was_rolled", "boolean"),
            _parameter(
                "witness_state_ids", "array", required=False, default=None, items={"type": "string"}
            ),
            _parameter(
                "minor_elementals_damage_type",
                "enum",
                required=False,
                default="fire",
                enum_values=["acid", "cold", "fire", "lightning"],
            ),
        ],
        effects,
    )


def _weapon_tools(original):
    """Leave the public Attack signature intact and alter only private source-qualified branches."""
    node = deepcopy(original)
    node["parameters"].append(_private_context())
    node["metadata"] = {"category": "internal", "visible_to_player": False, "costs_action": False}
    node.pop("narrative", None)
    for pre in node["pre_conditions"]:
        if pre["error"] in {
            "No package combat turn is active.",
            "Only the active creature can attack.",
            "The required Action or Bonus Action is unavailable.",
        }:
            pre["condition"] = "_true_strike_context != null or (" + pre["condition"] + ")"
    node["effects"].insert(
        0,
        _when(
            "_true_strike_context != null",
            [
                _authenticate(),
                _require(
                    "attack_sequence == 'primary' and use_graze_on_miss != true",
                    "True Strike makes one attack as part of its paid Magic action.",
                ),
                _require(
                    "_true_strike_context.casting_ability in "
                    "actor.data.abilities and _true_strike_context.level == actor.data.level",
                    "True Strike needs the actual paid character's ability and level.",
                ),
            ],
        ),
    )
    ability_anchor = None
    packet_count = 0
    for sequence in list(_lists(node["effects"])):
        for i in range(len(sequence) - 1, -1, -1):
            effect = sequence[i]
            if effect.get("call") == "spend_srd_attack":
                sequence[i] = _when("_true_strike_context == null", [effect])
            if effect.get("condition", "").startswith("attack_sequence == 'primary'") and (
                "light_attack_source" in str(effect) or "light_unrestricted_source" in str(effect)
            ):
                effect["condition"] += " and _true_strike_context == null"
            if effect.get("set", {}).get("field") == "_proficiency_bonus":
                ability_anchor = (sequence, i)
            if effect.get("call") in {
                "apply_srd_qualifying_damage",
                "apply_srd_divine_favor_weapon_damage",
            } and "_graze" not in str(effect):
                params = effect["params"]
                replacement = _call(
                    HIT_NODE,
                    {
                        "actor_state": "{_actor_state_id}",
                        "target": "{_target_id}",
                        "weapon": "{weapon}",
                        "_true_strike_context": "{_true_strike_context}",
                        "amount": params["amount"],
                        "damage_type": params["damage_type"],
                        "critical_hit": params["critical_hit"],
                        "unallocated_roll_penalty": "{_attack_unallocated_roll_penalty}",
                        "great_weapon_fighting": "{_size_great_weapon_fighting}",
                        "base_damage_was_rolled": "{_true_strike_base_rolled}",
                        "witness_state_ids": "{witness_state_ids}",
                        "minor_elementals_damage_type": "{minor_elementals_damage_type}",
                        "lasting_state": "{_lasting_state_id}",
                    },
                )
                replacement["export_context"] = deepcopy(effect["export_context"]) | {
                    field: field
                    for field in (
                        "_true_strike_extra_faces",
                        "_true_strike_extra_raw",
                        "_true_strike_radiant_amount",
                        "_true_strike_radiant_raw",
                        "_true_strike_roll_carry",
                    )
                }
                sequence[i] = {
                    "condition": "_true_strike_context != null",
                    "on_true": [replacement],
                    "on_false": [effect],
                }
                packet_count += 1
    if ability_anchor is None or packet_count < 2:
        raise ValueError(
            "True Strike needs the final weapon ability and actual typed hit boundaries."
        )
    sequence, index = ability_anchor
    sequence[index:index] = [
        _when(
            "_true_strike_context != null",
            [
                _set("_actual_weapon_attack_ability", "_true_strike_context.casting_ability"),
                _set(
                    "_ability_modifier",
                    "(actor.data.abilities[_true_strike_context.casting_ability] - 10) // 2",
                ),
            ],
        )
    ]
    # Proficiency is resolved by the actual weapon/category owner before the first roll.
    index = next(
        i
        for i, e in enumerate(node["effects"])
        if e.get("set", {}).get("field") == "_archery_bonus"
    )
    node["effects"].insert(
        index,
        _when(
            "_true_strike_context != null",
            [
                _require(
                    "_weapon_proficient == true", "True Strike requires actual weapon proficiency."
                ),
            ],
        ),
    )
    index = next(
        i
        for i, e in enumerate(node["effects"])
        if e.get("set", {}).get("field") == "actor_state.last_attack_weapon_magical"
    )
    node["effects"].insert(
        index,
        _when(
            "_true_strike_context != null and _true_strike_context.damage_choice == 'radiant'",
            [_set("_weapon_damage_type", "'radiant'")],
        ),
    )
    for effect in node["effects"]:
        if effect.get("set", {}).get("field") == "_attack_effective_armor_class":
            effect["set"]["value"] += (
                " + (_true_strike_context.cover_bonus if _true_strike_context != null else 0)"
            )
    # Calling the exact default path retains public signature, native cards and ordinary budgets.
    public = deepcopy(original)
    public["pre_conditions"] = []
    params = {
        p["name"]: "{_" + p["name"] + "_id}"
        if p.get("resolve") in {"entity", "participant"}
        else "{" + p["name"] + "}"
        for p in public["parameters"]
    }
    params["_true_strike_context"] = None
    index = next(
        i for i, e in enumerate(node["effects"]) if e.get("call") == "resolve_srd_attack_conditions"
    )
    node["effects"].insert(
        index,
        _when(
            "_true_strike_context != null",
            [
                _set("_true_strike_base_rolled", False),
                _when("_damage_expression != 'fixed-1'", [_set("_true_strike_base_rolled", True)]),
            ],
        ),
    )
    rolls = [
        (sequence, i)
        for sequence in _lists(node["effects"])
        for i, effect in enumerate(sequence)
        if effect.get("dice", {}).get("expression") == "1d20"
    ]
    if len(rolls) != 1:
        raise ValueError("True Strike needs the one actual settled weapon D20.")
    sequence, index = rolls[0]
    sequence.insert(
        index + 1,
        _when(
            "_true_strike_context != null and actor_state.illumination_has_invisibility == true",
            [
                _call(
                    "end_srd_invisibility_for_actor",
                    {"actor_state": "{_actor_state_id}", "event": "attack_roll"},
                )
            ],
        ),
    )
    public["effects"] = [{"call": WEAPON_NODE, "params": params, "export_context": "*"}]
    return {WEAPON_NODE: node, "resolve_combat_attack": public}


def _cast_tool(weapon_parameter):
    effects = [
        _require(
            "caster != null and caster.validation_status == 'validated'",
            "True Strike currently needs the actual validated character weapon owner.",
        ),
        _require(
            "caster_state.last_spell_source_spell == 'true_strike' and "
            "caster_state.last_spell_source_cast_id == spell_cast_id",
            "True Strike needs the successful paid spell identity.",
        ),
        _call(
            "validate_srd_inventory_spell_weapon",
            {"actor_state": "{_caster_state_id}", "weapon": "{weapon}"},
        ),
        _set("_true_strike_material", None),
        _require(
            "caster_state.spell_materials != null",
            "True Strike needs the saved actual material weapon supply.",
        ),
        _set("_true_strike_materials", "caster_state.spell_materials"),
        _loop(
            "_true_strike_materials",
            "_true_strike_supply",
            [
                _when(
                    "_true_strike_supply.id in material_item_ids and "
                    "_true_strike_supply.inventory_item == weapon and "
                    "_true_strike_supply.kind == 'specific' and "
                    "_true_strike_supply.value_cp >= 1 and _true_strike_supply.quantity >= 1 and "
                    "'true_strike.1' in _true_strike_supply.components",
                    [
                        _require(
                            "_true_strike_material == null", "Select one actual material weapon."
                        ),
                        _set("_true_strike_material", "_true_strike_supply"),
                    ],
                ),
            ],
        ),
        _require(
            "_true_strike_material != null",
            "Attack with the same proficient weapon used as True Strike's priced material.",
        ),
        _call(
            "qualify_srd_scene_hand_occupancy", {"actor_state": "{_caster_state_id}"}, HAND_EXPORTS
        ),
        _set("_true_strike_grasps", "_scene_hand_record.holdings"),
        _set("_true_strike_weapon_hands", 0),
        _set("_true_strike_other_weapons", []),
        _loop(
            "_true_strike_grasps",
            "_true_strike_grasp",
            [
                _when(
                    "_true_strike_grasp.inventory_item == weapon",
                    [
                        _set("_true_strike_weapon_hands", "len(_true_strike_grasp.hand_indices)"),
                    ],
                ),
                _when(
                    "_true_strike_grasp.inventory_item != weapon and "
                    "_true_strike_grasp.inventory_item in definitions.weapons",
                    [
                        {
                            "list_push": {
                                "list": "_true_strike_other_weapons",
                                "value": "_true_strike_grasp.inventory_item",
                            }
                        },
                    ],
                ),
            ],
        ),
        _require(
            "_true_strike_weapon_hands >= 1 and "
            "(wielding_two_handed != true or _true_strike_weapon_hands >= 2)",
            "The actual material weapon must be held in the hands used for this attack.",
        ),
        _call(
            "validate_srd_charmer_harm",
            {"actor_state": "{_caster_state_id}", "target_state": "{_target_id}"},
        ),
        _set("_true_strike_fear_visible", False),
        _when(
            "'frightened' in caster_state.conditions",
            [
                _require(
                    "caster_state.control_behavior_source_ids != null",
                    "Qualify this creature's actual Fear source before attacking.",
                ),
                _call("resolve_srd_control_fear_visibility", {"actor_state": "{_caster_state_id}"}),
                _set("_true_strike_fear_visible", "caster_state.control_fear_visible == true"),
            ],
        ),
        _call(
            "resolve_srd_scene_relation",
            {"actor_state": "{_caster_state_id}", "target_state": "{_target_id}"},
            [
                "_scene_distance",
                "_scene_same_plane",
                "_scene_actor_position",
                "_scene_target_entity_id",
                "_scene_line_of_sight",
            ],
        ),
        _require(
            "_scene_same_plane == true",
            "True Strike needs the actual same-plane weapon target.",
        ),
        _set("_true_strike_distance", "ceil(_scene_distance)"),
        _call(
            "resolve_srd_scene_sight_origin",
            {"observer_state": "{_caster_state_id}"},
            SIGHT_EXPORTS,
        ),
        *_snapshot(),
        _spatial(
            "exposure",
            {
                "map_id": "{state.spatial_scene.map.id}",
                "observer": {"point": "{_sight_origin}"},
                "target": {"entity_id": "{_scene_target_entity_id}"},
                "samples": {"horizontal": 3, "vertical": 3},
                "include": ["geometry"],
            },
            "_true_strike_exposure",
        ),
        _require(
            "_true_strike_exposure.visible_samples > 0",
            "The actual sampled weapon target has Total Cover.",
        ),
        _set("_true_strike_cover_bonus", 0),
        _when("_true_strike_exposure.exposure <= 0.5", [_set("_true_strike_cover_bonus", 2)]),
        _when("_true_strike_exposure.exposure <= 0.25", [_set("_true_strike_cover_bonus", 5)]),
        _call(
            "resolve_srd_illumination_target_sight",
            {"observer_state": "{_caster_state_id}", "target_state": "{_target_id}"},
            ["_illumination_target_can_see"],
        ),
        _set("_true_strike_can_see", "_illumination_target_can_see"),
        _call(
            "resolve_srd_illumination_target_sight",
            {"observer_state": "{_target_id}", "target_state": "{_caster_state_id}"},
            ["_illumination_target_can_see"],
        ),
        _set("_true_strike_target_can_see", "_illumination_target_can_see"),
        _set("_true_strike_nearby_disadvantage", False),
        _when(
            "attack_mode != 'melee'",
            [
                _call(
                    "resolve_srd_produce_flame_nearby_enemies",
                    {"caster_state": "{_caster_state_id}"},
                    ["_produce_nearby_disadvantage"],
                ),
                _set("_true_strike_nearby_disadvantage", "_produce_nearby_disadvantage"),
            ],
        ),
        _set("_true_strike_extra_dice", 0),
        _when("caster.data.level >= 5", [_set("_true_strike_extra_dice", 1)]),
        _when("caster.data.level >= 11", [_set("_true_strike_extra_dice", 2)]),
        _when("caster.data.level >= 17", [_set("_true_strike_extra_dice", 3)]),
        _set(
            "_true_strike_context",
            {
                "cast_id": "spell_cast_id",
                "caster_id": "_caster_state_id",
                "weapon": "weapon",
                "material": "_true_strike_material",
                "casting_ability": "casting_ability",
                "level": "caster.data.level",
                "extra_dice": "_true_strike_extra_dice",
                "damage_choice": "damage_choice",
                "cover_bonus": "_true_strike_cover_bonus",
            },
        ),
        _set("_true_strike_weapon_ability", "'strength'"),
        _when(
            "weapon_info.kind == 'ranged' or weapon == 'dart'",
            [
                _set("_true_strike_weapon_ability", "'dexterity'"),
            ],
        ),
        {
            "call": WEAPON_NODE,
            "export_context": "*",
            "params": {
                "actor": "{_caster_id}",
                "actor_state": "{_caster_state_id}",
                "target": "{_target_id}",
                "weapon": "{weapon}",
                "attack_sequence": "primary",
                "use_graze_on_miss": False,
                "attack_mode": "{attack_mode}",
                "attack_ability": "{_true_strike_weapon_ability}",
                "wielding_two_handed": "{wielding_two_handed}",
                "mounted": False,
                "circumstantial_modifier": 0,
                "advantage": False,
                "disadvantage": "{_true_strike_nearby_disadvantage}",
                "actor_can_see_target": "{_true_strike_can_see}",
                "target_can_see_actor": "{_true_strike_target_can_see}",
                "target_is_charmer": False,
                "fear_source_in_sight": "{_true_strike_fear_visible}",
                "attack_distance": "{_true_strike_distance}",
                "other_wielded_weapons": "{_true_strike_other_weapons}",
                "use_shillelagh_ability": False,
                "shillelagh_damage_type": "weapon",
                "minor_elementals_damage_type": "{minor_elementals_damage_type}",
                "_true_strike_context": "{_true_strike_context}",
                "lasting_state": "{_lasting_state_id}",
            },
        },
        _set(
            "_spell_summary_rows",
            [
                {"label": "'Weapon'", "value": "weapon"},
                {"label": "'Attack'", "value": "_attack_outcome"},
                {"label": "'Damage'", "value": "_damage_applied"},
            ],
        ),
        _set(
            "_spell_detail_rows",
            [
                {"label": "'D20 results'", "value": "_attack_roll.results"},
                {"label": "'Attack total'", "value": "_attack_adjusted_total"},
                {"label": "'Target AC'", "value": "_attack_effective_armor_class"},
                {"label": "'Cover AC bonus'", "value": "_true_strike_cover_bonus"},
                {"label": "'Casting ability'", "value": "casting_ability"},
                {"label": "'Ability modifier'", "value": "_ability_modifier"},
                {"label": "'Proficiency'", "value": "_weapon_proficiency_modifier"},
                {"label": "'Weapon damage type'", "value": "_weapon_damage_type"},
                {"label": "'HP lost'", "value": "_damage_hp_lost"},
                {"label": "'Temporary HP absorbed'", "value": "_damage_absorbed"},
            ],
        ),
        _when(
            "_damage_roll != null",
            [
                {
                    "list_push": {
                        "list": "_spell_detail_rows",
                        "value": {"label": "'Weapon damage dice'", "value": "_damage_roll.results"},
                    }
                }
            ],
        ),
        _when(
            "_true_strike_extra_faces != null",
            [
                {
                    "list_push": {
                        "list": "_spell_detail_rows",
                        "value": {
                            "label": "'Extra Radiant dice'",
                            "value": "_true_strike_extra_faces",
                        },
                    }
                }
            ],
        ),
    ]
    return _tool_scene(
        "Resolve True Strike's one material-weapon attack after its real Magic action payment.",
        [
            _entity("caster_state"),
            _parameter("caster", resolve="participant"),
            _entity("target"),
            deepcopy(weapon_parameter),
            _parameter("attack_mode", "enum", enum_values=["melee", "ranged", "thrown"]),
            _parameter("wielding_two_handed", "boolean"),
            _parameter("damage_choice", "enum", enum_values=["weapon", "radiant"]),
            _parameter(
                "casting_ability", "enum", enum_values=["intelligence", "wisdom", "charisma"]
            ),
            _parameter("spell_cast_id"),
            _parameter("material_item_ids", "array", items={"type": "string"}),
            _parameter(
                "minor_elementals_damage_type",
                "enum",
                required=False,
                default="fire",
                enum_values=["acid", "cold", "fire", "lightning"],
            ),
        ],
        effects,
    )


def extend_cast_parameters(text):
    """Expose only genuine weapon/type intents before Reaction saves the cast's parameters."""
    tools = _load_runtime(text)["tools"]
    weapon = next(p for p in tools["resolve_combat_attack"]["parameters"] if p["name"] == "weapon")
    changed = {}
    for name in ("cast_spell", "cast_spell_outside_combat"):
        cast = deepcopy(tools[name])
        choices = [
            deepcopy(weapon)
            | {"name": "true_strike_weapon", "required": False, "default": "quarterstaff"},
            _parameter(
                "true_strike_attack_mode",
                "enum",
                required=False,
                default="melee",
                enum_values=["melee", "ranged", "thrown"],
            ),
            _parameter("true_strike_two_handed", "boolean", required=False, default=False),
            _parameter(
                "true_strike_damage_choice",
                "enum",
                required=False,
                default="weapon",
                enum_values=["weapon", "radiant"],
            ),
        ]
        existing = {p["name"] for p in cast["parameters"]}
        cast["parameters"] += [p for p in choices if p["name"] not in existing]
        if cast != tools[name]:
            changed[name] = cast
    return _replace_tools(text, changed) if changed else text


def _ordinary_weapon(tools):
    """Invert only this owner's branches before ordinary Reaction windows are regenerated."""
    public = tools["resolve_combat_attack"]
    if not public["effects"] or public["effects"][0].get("call") != WEAPON_NODE:
        return None
    if WEAPON_NODE not in tools:
        raise ValueError("True Strike's delegate needs its actual settled weapon owner.")
    original = deepcopy(tools[WEAPON_NODE])
    original["parameters"] = [
        p for p in original["parameters"] if p["name"] != "_true_strike_context"
    ]
    original["metadata"] = deepcopy(public["metadata"])
    if "narrative" in public:
        original["narrative"] = deepcopy(public["narrative"])
    for pre in original["pre_conditions"]:
        prefix = "_true_strike_context != null or ("
        if pre["condition"].startswith(prefix):
            pre["condition"] = pre["condition"][len(prefix) : -1]

    def inverse(effects):
        restored = []
        for effect in effects:
            clause = effect.get("condition", "")
            if clause == "_true_strike_context == null":
                if len(effect.get("on_true", [])) != 1 or (
                    effect["on_true"][0].get("call") != "spend_srd_attack"
                ):
                    raise ValueError("True Strike's inverse needs its actual attack-cost wrapper.")
                restored += deepcopy(effect["on_true"])
                continue
            if "_true_strike_context" in clause and "on_false" in effect:
                if len(effect["on_false"]) != 1 or not any(
                    child.get("call") == HIT_NODE for child in effect.get("on_true", [])
                ):
                    raise ValueError("True Strike's inverse needs its exact typed packet wrapper.")
                restored += deepcopy(effect["on_false"])
                continue
            if clause.startswith("_true_strike_context != null"):
                continue
            if clause.endswith(" and _true_strike_context == null"):
                effect["condition"] = clause.removesuffix(" and _true_strike_context == null")
            if effect.get("set", {}).get("field") == "_attack_effective_armor_class":
                effect["set"]["value"] = effect["set"]["value"].removesuffix(
                    " + (_true_strike_context.cover_bonus if _true_strike_context != null else 0)"
                )
            for branch in ("on_true", "on_false"):
                if branch in effect:
                    effect[branch] = inverse(effect[branch])
            if "for_each" in effect:
                effect["for_each"]["do"] = inverse(effect["for_each"]["do"])
            restored.append(effect)
        return restored

    original["effects"] = inverse(original["effects"]) + deepcopy(public["effects"][1:])
    if "_true_strike_context" in str(original):
        raise ValueError("True Strike's inverse left an unrecognized source transformation.")
    return original


def _outer_completion_tail(effects, tools):
    """Keep genuine late action settlements outside the private attack during regeneration."""
    owners = {
        "settle_srd_travel_exile_returns",
        "settle_srd_levitate_returns",
        "handoff_srd_telekinesis_releases",
        "handoff_srd_mirage_support_losses",
    } & tools.keys()
    first = len(effects)
    while first:
        effect = effects[first - 1]
        children = effect.get("on_true", [])
        if (
            not effect.get("condition")
            or "on_false" in effect
            or len(children) != 1
            or children[0].get("call") not in owners
        ):
            break
        first -= 1
    return deepcopy(effects[first:])


def restore_runtime(text):
    """Restore the ordinary source structure before final family and Reaction regeneration."""
    tools = _load_runtime(text)["tools"]
    original = _ordinary_weapon(tools)
    return _replace_tools(text, {"resolve_combat_attack": original}) if original else text


def bind_runtime_consumers(text):
    """Bind after ordinary Reaction clones capture the final weapon/Mirror owner structure."""
    text = extend_cast_parameters(text)
    runtime = _load_runtime(text)
    tools = runtime["tools"]
    if _ordinary_weapon(tools) is not None:
        if CAST_NODE not in tools or HIT_NODE not in tools:
            raise ValueError("True Strike needs its complete source-owned cast and typed packet.")
        return text
    if "resolve_srd_scene_sight_origin" not in tools:
        raise ValueError("True Strike needs the accepted factual sight-origin owner.")
    original = deepcopy(tools["resolve_combat_attack"])
    completion_tail = _outer_completion_tail(original["effects"], tools)
    if completion_tail:
        original["effects"] = original["effects"][: -len(completion_tail)]
    changed = _weapon_tools(original)
    changed["resolve_combat_attack"]["effects"].extend(completion_tail)
    changed[HIT_NODE] = _packet_tool()
    weapon = next(p for p in original["parameters"] if p["name"] == "weapon")
    changed[CAST_NODE] = _cast_tool(weapon)
    for name in ("cast_spell", "cast_spell_outside_combat"):
        cast = deepcopy(tools[name])
        if any(e.get("call") == "cast_spell" for s in _lists(cast["effects"]) for e in s):
            continue
        if any(e.get("call") == CAST_NODE for s in _lists(cast["effects"]) for e in s):
            continue
        index = next(
            i
            for i, e in enumerate(cast["effects"])
            if e.get("set", {}).get("field") == "_card_slots_after"
        )
        cast["effects"].insert(
            index,
            _when(
                "_spell_cast_failed != true and _pending_spell_operation_lost != true and "
                "_spell_definition.family == 'true_strike'",
                [
                    _when(
                        "_spell_source_cast_id == null or _spell_source_cast_id == ''",
                        [
                            _call(
                                "begin_srd_spell_source_instance",
                                {
                                    "caster_state": "{_caster_state_id}",
                                    "lasting_state": "{_lasting_state_id}",
                                    "spell": "true_strike",
                                    "slot_level": 0,
                                },
                                ["_spell_source_cast_id"],
                            ),
                        ],
                    ),
                    {
                        "call": CAST_NODE,
                        "export_context": "*",
                        "params": {
                            "caster_state": "{_caster_state_id}",
                            "caster": "{_caster_id}",
                            "target": "{_target_id}",
                            "weapon": "{true_strike_weapon}",
                            "attack_mode": "{true_strike_attack_mode}",
                            "wielding_two_handed": "{true_strike_two_handed}",
                            "damage_choice": "{true_strike_damage_choice}",
                            "casting_ability": "{_casting_profile.ability}",
                            "spell_cast_id": "{_spell_source_cast_id}",
                            "material_item_ids": "{material_item_ids}",
                            "minor_elementals_damage_type": "{minor_elementals_damage_type}",
                            "lasting_state": "{_lasting_state_id}",
                        },
                    },
                ],
            ),
        )
        changed[name] = cast
    return _replace_tools(text, changed)
