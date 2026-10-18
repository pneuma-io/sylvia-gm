"""Shared SRD equipment records for worn-item state and its mechanical consumers."""

from copy import deepcopy

if __package__:
    from scripts.srd_fighting_styles import (
        STYLES,
        choice_schema,
        creation_effects,
        grant_policies,
        validation_effects,
    )
else:
    from srd_fighting_styles import (
        STYLES,
        choice_schema,
        creation_effects,
        grant_policies,
        validation_effects,
    )

# SRD 5.2.1 Equipment: Armor. Shields use a Utilize action, not a minute-based change.
_ARMOR_ROWS = (
    ("padded_armor", "Padded Armor", "light", 11, 0, True),
    ("leather_armor", "Leather Armor", "light", 11, 0, False),
    ("studded_leather_armor", "Studded Leather Armor", "light", 12, 0, False),
    ("hide_armor", "Hide Armor", "medium", 12, 0, False),
    ("chain_shirt", "Chain Shirt", "medium", 13, 0, False),
    ("scale_mail", "Scale Mail", "medium", 14, 0, True),
    ("breastplate", "Breastplate", "medium", 14, 0, False),
    ("half_plate_armor", "Half Plate Armor", "medium", 15, 0, True),
    ("ring_mail", "Ring Mail", "heavy", 14, 0, True),
    ("chain_mail", "Chain Mail", "heavy", 16, 13, True),
    ("splint_armor", "Splint Armor", "heavy", 17, 15, True),
    ("plate_armor", "Plate Armor", "heavy", 18, 15, True),
    ("shield", "Shield", "shield", 2, 0, False),
)

# SRD 5.2.1 Feats, pp. 87–88; do not import other editions' style lists.
_FIGHTING_STYLES = STYLES

_STARTING_TRAINING = {
    "barbarian": ["light", "medium", "shield"],
    "bard": ["light"],
    "cleric": ["light", "medium", "shield"],
    "druid": ["light", "shield"],
    "fighter": ["light", "medium", "heavy", "shield"],
    "monk": [],
    "paladin": ["light", "medium", "heavy", "shield"],
    "ranger": ["light", "medium", "shield"],
    "rogue": ["light"],
    "sorcerer": [],
    "warlock": ["light"],
    "wizard": [],
}


def equipment_definitions():
    """Return fresh package data; starting-class and multiclass grants remain distinct."""
    armor = {
        item: {
            "name": name, "category": category, "base_ac": ac,
            "strength_required": strength, "stealth_disadvantage": stealth,
            "dexterity_cap": {"light": None, "medium": 2, "heavy": 0, "shield": 0}[category],
            "uses_dexterity": category in ("light", "medium"),
            "don_minutes": {"light": 1, "medium": 5, "heavy": 10, "shield": 0}[category],
            "doff_minutes": {"light": 1, "medium": 1, "heavy": 5, "shield": 0}[category],
        }
        for item, name, category, ac, strength, stealth in _ARMOR_ROWS
    }
    multiclass = deepcopy(_STARTING_TRAINING)
    multiclass["barbarian"] = ["shield"]
    for name in ("fighter", "paladin"):
        multiclass[name] = ["light", "medium", "shield"]
    return {
        "srd_armor": armor,
        "srd_fighting_styles": list(_FIGHTING_STYLES),
        "srd_fighting_style_grants": grant_policies(),
        "srd_starting_armor_training": deepcopy(_STARTING_TRAINING),
        "srd_multiclass_armor_training": multiclass,
    }


def worn_item_schema(*, shield=False):
    """Bind a worn item to carried inventory without treating every carried item as worn."""
    types = [row[0] for row in _ARMOR_ROWS if (row[2] == "shield") is shield]
    return {
        "type": "object",
        "properties": {
            "inventory_item": {"type": "string", "minLength": 1, "maxLength": 300},
            "armor_type": {"type": "string", "enum": types},
            "magic_bonus": {"type": "integer", "minimum": 0, "maximum": 3},
        },
        "required": ["inventory_item", "armor_type", "magic_bonus"],
        "additionalProperties": False,
    }


def creation_equipment(schema, creation):
    """Project the shared read-only equipment results into declared starting fields."""
    schema["properties"].update({
        "fighting_styles": choice_schema(),
        "cleric_order": {"type": "string", "enum": ["protector", "thaumaturge"]},
        "druid_order": {"type": "string", "enum": ["warden", "magician"]},
    })
    schema["properties"]["speed"]["description"] = (
        "Base walking Speed from species, before class movement bonuses, armor, Exhaustion "
        "or temporary effects. The engine applies those separately."
    )
    entity = creation["entity_state_schema"]
    for slot in ("worn_armor", "worn_shield"):
        item = worn_item_schema(shield=slot == "worn_shield")
        item["type"] = ["object", "null"]
        item["description"] = "The actual worn carried item, or null when this slot is empty."
        entity["properties"][slot] = item
        if slot not in entity["required"]:
            entity["required"].append(slot)
    entity["properties"].update({
        "armor_speed_penalty": {"type": "integer", "minimum": 0},
        "armor_untrained": {"type": "boolean"},
        "armor_stealth_disadvantage": {"type": "boolean"},
        "climb_speed": {"type": "integer", "minimum": 0},
        "swim_speed": {"type": "integer", "minimum": 0},
    })
    results = {
        "armor_class": "rules._equipment_ac",
        "armor_speed_penalty": "rules._equipment_speed_penalty",
        "armor_untrained": "rules._equipment_untrained_armor",
        "armor_stealth_disadvantage": "rules._equipment_stealth_disadvantage",
        "speed": "rules._equipment_speed",
        "climb_speed": "rules._equipment_climb_speed",
        "swim_speed": "rules._equipment_swim_speed",
    }
    creation["initial_state_calculations"] = [
        step for step in creation["initial_state_calculations"]
        if step["set"]["field"] not in results
    ] + [{"set": {"field": field, "value": value}} for field, value in results.items()]
    for field in results:
        if field not in entity["required"]:
            entity["required"].append(field)


def creation_validation(program):
    """Verify equipment with the same runtime calculation after starting values are derived."""
    import re

    def rebind(value):
        if isinstance(value, dict):
            return {key: (item if key == "error" else rebind(item)) for key, item in value.items()}
        if isinstance(value, list):
            return [rebind(item) for item in value]
        if isinstance(value, str):
            return re.sub(r"\bactor_state\b", "state", value).replace("actor.data", "participant")
        return value

    program["definitions"].update(equipment_definitions())
    program["effects"].extend(rebind(equipment_tool()["effects"]))
    program["effects"].extend(creation_effects())
    for field, calculated in (("armor_class", "_equipment_ac"),
                              ("armor_untrained", "_equipment_untrained_armor"),
                              ("armor_speed_penalty", "_equipment_speed_penalty"),
                              ("armor_stealth_disadvantage", "_equipment_stealth_disadvantage"),
                              ("speed", "_equipment_speed"),
                              ("climb_speed", "_equipment_climb_speed"),
                              ("swim_speed", "_equipment_swim_speed")):
        program["effects"].append({"condition": f"state.{field} != null", "on_true": [
            {"require": {
                "condition": f"state.{field} == {calculated}",
                "error": "Starting defenses must match worn equipment and character grants.",
            }},
        ]})
    program["description"] += (
        " Record worn_armor and worn_shield explicitly, using null for empty slots. "
        "Worn items must be carried; defenses are calculated from gear and class choices, "
        "including selected Fighting Styles and Divine/Primal Order."
    )


def equipment_tool():
    """Calculate equipment consequences from character grants and exact worn inventory."""
    def assign(field, value):
        return {"set": {"field": field, "value": value}}

    def require(condition, error):
        return {"require": {"condition": condition, "error": error}}

    effects = validation_effects("actor.data") + [
        require("actor_state.worn_armor == null or actor_state.worn_shield == null "
                "or actor_state.worn_armor.inventory_item "
                "!= actor_state.worn_shield.inventory_item",
                "One carried item cannot occupy both the armor and shield slots."),
        assign("_equipment_training", []),
        assign("_equipment_classes", "actor.data.classes"),
        assign("_equipment_first_class", True),
        assign("_equipment_barbarian", False),
        assign("_equipment_monk", False),
        assign("_equipment_draconic", False),
        assign("_equipment_style_eligible", False),
        assign("_equipment_cleric", False),
        assign("_equipment_druid", False),
        {"for_each": {"collection": "_equipment_classes", "as": "_equipment_class", "do": [
            require("_equipment_class.id in definitions.srd_starting_armor_training",
                    "Equipment requires a supported SRD class."),
            assign("_equipment_class_training",
                   "definitions.srd_multiclass_armor_training[_equipment_class.id]"),
            {"condition": "_equipment_first_class == true", "on_true": [
                assign("_equipment_class_training",
                       "definitions.srd_starting_armor_training[_equipment_class.id]"),
            ]},
            assign("_equipment_first_class", False),
            {"condition": "_equipment_class.id == 'cleric'",
             "on_true": [assign("_equipment_cleric", True)]},
            {"condition": "_equipment_class.id == 'druid'",
             "on_true": [assign("_equipment_druid", True)]},
            {"condition": "_equipment_class.id == 'barbarian'",
             "on_true": [assign("_equipment_barbarian", True)]},
            {"condition": "_equipment_class.id == 'monk'",
             "on_true": [assign("_equipment_monk", True)]},
            {"condition": "_equipment_class.id == 'sorcerer' and _equipment_class.level >= 3 "
             "and actor.data.subclasses.sorcerer == 'draconic_sorcery'",
             "on_true": [assign("_equipment_draconic", True)]},
            {"condition": "_equipment_class.id == 'fighter' or "
             "((_equipment_class.id == 'paladin' or _equipment_class.id == 'ranger') "
             "and _equipment_class.level >= 2)",
             "on_true": [assign("_equipment_style_eligible", True)]},
            {"for_each": {"collection": "_equipment_class_training", "as": "_training", "do": [
                {"condition": "_training not in _equipment_training", "on_true": [
                    {"list_push": {"list": "_equipment_training", "value": "_training"}},
                ]},
            ]}},
            {"condition": "(_equipment_class.id == 'cleric' "
             "and actor.data.cleric_order == 'protector') "
             "or (_equipment_class.id == 'druid' and actor.data.druid_order == 'warden')",
             "on_true": [
                 assign("_order_training", "'medium'"),
                 {"condition": "_equipment_class.id == 'cleric'", "on_true": [
                     assign("_order_training", "'heavy'"),
                 ]},
                 {"list_push": {"list": "_equipment_training", "value": "_order_training"}},
             ]},
        ]}},
        assign("_equipment_dexterity", "(actor.data.abilities.dexterity - 10) // 2"),
        require("actor.data.cleric_order == null or _equipment_cleric == true",
                "Divine Order requires a Cleric class grant."),
        require("actor.data.druid_order == null or _equipment_druid == true",
                "Primal Order requires a Druid class grant."),
        assign("_equipment_ac", "10 + _equipment_dexterity"),
        assign("_equipment_speed_penalty", 0),
        assign("_equipment_untrained_armor", False),
        assign("_equipment_stealth_disadvantage", False),
        {"condition": "actor_state.worn_armor == null", "on_true": [
            {"condition": "_equipment_barbarian == true", "on_true": [
                assign("_equipment_ac", "max(_equipment_ac, 10 + _equipment_dexterity "
                       "+ (actor.data.abilities.constitution - 10) // 2)"),
            ]},
            {"condition": "_equipment_monk == true "
             "and actor_state.worn_shield == null", "on_true": [
                 assign("_equipment_ac", "max(_equipment_ac, 10 + _equipment_dexterity "
                        "+ (actor.data.abilities.wisdom - 10) // 2)"),
             ]},
            {"condition": "_equipment_draconic == true", "on_true": [
                assign("_equipment_ac", "max(_equipment_ac, 10 + _equipment_dexterity "
                       "+ (actor.data.abilities.charisma - 10) // 2)"),
            ]},
        ]},
    ]
    for slot, category_check in (("worn_armor", "!= 'shield'"), ("worn_shield", "== 'shield'")):
        item = f"actor_state.{slot}"
        steps = [
            require(f"len({item}.inventory_item) <= 300",
                    "The carried item identifier is too long."),
            require(f"{item}.inventory_item in actor_state.inventory",
                    "Worn equipment must belong to a carried inventory item."),
            require(f"{item}.armor_type in definitions.srd_armor",
                    "Select a supported SRD armor or shield type."),
            assign("_equipment_item", f"definitions.srd_armor[{item}.armor_type]"),
            require(f"_equipment_item.category {category_check}",
                    "Armor and shields must use their matching equipment slot."),
            require(f"{item}.magic_bonus >= 0 and {item}.magic_bonus <= 3",
                    "An armor or shield magic bonus must be between zero and three."),
        ]
        if slot == "worn_armor":
            steps += [
                assign("_equipment_ac", f"_equipment_item.base_ac + {item}.magic_bonus"),
                {"condition": "_equipment_item.uses_dexterity == true", "on_true": [
                    assign("_equipment_dexterity_bonus", "_equipment_dexterity"),
                    {"condition": "_equipment_item.dexterity_cap != null", "on_true": [
                        assign("_equipment_dexterity_bonus",
                               "min(_equipment_dexterity, _equipment_item.dexterity_cap)"),
                    ]},
                    assign("_equipment_ac", "_equipment_ac + _equipment_dexterity_bonus"),
                ]},
                assign("_equipment_untrained_armor",
                       "_equipment_item.category not in _equipment_training"),
                assign("_equipment_stealth_disadvantage", "_equipment_item.stealth_disadvantage"),
                {"condition": "actor.data.abilities.strength < _equipment_item.strength_required",
                 "on_true": [assign("_equipment_speed_penalty", 10)]},
            ]
        else:
            steps += [{"condition": "'shield' in _equipment_training", "on_true": [
                assign("_equipment_ac",
                       f"_equipment_ac + _equipment_item.base_ac + {item}.magic_bonus"),
            ]}]
        effects.append({"condition": f"{item} != null", "on_true": steps})
    effects.append({"condition": "'defense' in _style_selected", "on_true": [
        {"condition": "actor_state.worn_armor != null", "on_true": [
            assign("_equipment_ac", "_equipment_ac + 1"),
        ]},
    ]})
    effects += [
        assign("_equipment_heavy", False),
        {"condition": "actor_state.worn_armor != null", "on_true": [
            assign("_equipment_heavy",
                   "definitions.srd_armor[actor_state.worn_armor.armor_type].category == 'heavy'"),
        ]},
        assign("_equipment_speed_bonus", 0),
        assign("_equipment_roving", False),
        {"for_each": {"collection": "_equipment_classes", "as": "_movement_class", "do": [
            {"condition": "_movement_class.id == 'barbarian' and _movement_class.level >= 5 "
             "and _equipment_heavy == false", "on_true": [
                 assign("_equipment_speed_bonus", "_equipment_speed_bonus + 10"),
             ]},
            {"condition": "_movement_class.id == 'monk' and _movement_class.level >= 2 "
             "and actor_state.worn_armor == null and actor_state.worn_shield == null",
             "on_true": [assign(
                 "_equipment_speed_bonus",
                 "_equipment_speed_bonus + 10 + 5 * ((_movement_class.level - 2) // 4)",
             )]},
            {"condition": "_movement_class.id == 'ranger' and _movement_class.level >= 6",
             "on_true": [
                 assign("_equipment_roving", True),
                 {"condition": "_equipment_heavy == false", "on_true": [
                     assign("_equipment_speed_bonus", "_equipment_speed_bonus + 10"),
                 ]},
             ]},
        ]}},
        assign("_equipment_speed",
               "max(0, actor.data.speed + _equipment_speed_bonus - _equipment_speed_penalty)"),
        assign("_equipment_climb_speed", 0),
        assign("_equipment_swim_speed", 0),
        {"condition": "_equipment_roving == true", "on_true": [
            assign("_equipment_climb_speed", "_equipment_speed"),
            assign("_equipment_swim_speed", "_equipment_speed"),
        ]},
    ]
    return {
        "description": "Internal shared armor/shield calculation; "
        "does not mutate equipment or spend time.",
        "parameters": [
            {"name": "actor", "type": "string", "resolve": "participant", "required": True},
            {"name": "actor_state", "type": "string", "resolve": "entity", "required": True},
        ],
        "effects": effects,
    }


def prospective_equipment_effects():
    """Reuse the equipment calculation against a level-up proposal before player confirmation."""
    def rebind(value):
        if isinstance(value, dict):
            return {key: (item if key == "error" else rebind(item)) for key, item in value.items()}
        if isinstance(value, list):
            return [rebind(item) for item in value]
        if isinstance(value, str):
            return value.replace("actor.data", "_next_equipment_profile").replace(
                "actor_state", "character_state",
            )
        return value

    return rebind(equipment_tool()["effects"])


def change_equipment_tool():
    """Change one worn slot through the normal action and elapsed-time primitives."""
    item_schema = worn_item_schema()
    item_schema["properties"]["inventory_item"].pop("maxLength")
    item_schema["properties"]["armor_type"]["enum"].append("shield")

    def assign(field, value):
        return {"set": {"field": field, "value": value}}

    def require(condition, error):
        return {"require": {"condition": condition, "error": error}}

    effects = [
        require("len(item) <= 1", "Change one equipment slot at a time."),
        assign("_equipment_old", None),
        {"condition": "actor_state.worn_armor != null", "on_true": [
            assign("_equipment_old", "actor_state.worn_armor"),
        ]},
        {"condition": "slot == 'shield'", "on_true": [
            assign("_equipment_old", None),
            {"condition": "actor_state.worn_shield != null", "on_true": [
                assign("_equipment_old", "actor_state.worn_shield"),
            ]},
        ]},
        assign("_equipment_new", None),
        {"condition": "len(item) == 1", "on_true": [
            require("_equipment_old == null", "Remove the currently worn item first."),
            assign("_equipment_new", "item[0]"),
            assign("_equipment_changed", "_equipment_new"),
        ], "on_false": [
            require("_equipment_old != null", "There is no item worn in that slot."),
            assign("_equipment_changed", "_equipment_old"),
        ]},
        require("_equipment_changed.armor_type in definitions.srd_armor",
                "Select a supported SRD armor or shield type."),
        assign("_equipment_record", "definitions.srd_armor[_equipment_changed.armor_type]"),
        require("len(_equipment_changed.inventory_item) <= 300",
                "The carried item identifier is too long."),
        require("(slot == 'shield' and _equipment_record.category == 'shield') "
                "or (slot == 'armor' and _equipment_record.category != 'shield')",
                "Armor and shields must use their matching equipment slot."),
        assign("_equipment_rounds", "_equipment_record.doff_minutes * 10"),
        {"condition": "_equipment_new != null", "on_true": [
            assign("_equipment_rounds", "_equipment_record.don_minutes * 10"),
        ]},
        {"condition": "slot == 'shield'", "on_true": [
            assign("_equipment_rounds", 1),
            assign("actor_state.worn_shield", None),
            {"condition": "_equipment_new != null", "on_true": [
                assign("actor_state.worn_shield", "_equipment_new"),
            ]},
        ], "on_false": [
            assign("actor_state.worn_armor", None),
            {"condition": "_equipment_new != null", "on_true": [
                assign("actor_state.worn_armor", "_equipment_new"),
            ]},
        ]},
        {"condition": "actor != null", "on_true": [
            {"call": "resolve_srd_equipment", "params": {
                "actor": "{_actor_id}", "actor_state": "{_actor_state_id}",
            }, "export_context": equipment_exports()},
            assign("_equipment_actor_name", "actor.data.name"),
        ], "on_false": [
            {"call": "resolve_srd_npc_equipment", "params": {
                "actor_state": "{_actor_state_id}",
            }, "export_context": equipment_exports()},
            assign("_equipment_actor_name", "actor_state.name"),
        ]},
        assign("actor_state.armor_class", "_equipment_ac"),
        assign("actor_state.armor_speed_penalty", "_equipment_speed_penalty"),
        assign("actor_state.armor_untrained", "_equipment_untrained_armor"),
        assign("actor_state.armor_stealth_disadvantage", "_equipment_stealth_disadvantage"),
        assign("actor_state.speed", "_equipment_speed"),
        assign("actor_state.climb_speed", "_equipment_climb_speed"),
        assign("actor_state.swim_speed", "_equipment_swim_speed"),
        {"call": "refresh_srd_movement_budget", "params": {"actor_state": "{_actor_state_id}"}},
        {"condition": "combat.turn_active == true and combat.active_actor == _actor_state_id",
         "on_true": [{"call": "refresh_srd_monk_bonus_availability",
                      "params": {"actor_state": "{_actor_state_id}"}}]},
        {"condition": "combat.encounter_active == true", "on_true": [
            {"call": "spend_srd_action", "params": {"magic_action": False}},
        ], "on_false": [
            {"call": "advance_srd_lasting_time", "params": {
                "elapsed_rounds": "{_equipment_rounds}",
            }},
        ]},
    ]
    return {
        "description": "Don or doff one carried armor or shield. Empty item removes the worn "
        "item; one item equips it. Remove an existing item before replacing it. Use established "
        "item type and magic bonus, never invent equipment. Armor uses its SRD don/doff minutes "
        "outside combat; a shield uses a Utilize action in combat or one round outside combat.",
        "parameters": [
            {"name": "actor", "type": "string", "resolve": "participant", "required": False},
            {"name": "actor_state", "type": "string", "resolve": "entity", "required": False,
             "model_derive": {"kind": "copy", "source": "actor"}},
            {"name": "slot", "type": "enum", "enum_values": ["armor", "shield"],
             "required": True},
            {"name": "item", "type": "array", "items": item_schema, "max_items": 1,
             "required": True},
        ],
        "pre_conditions": [
            {"condition": "(actor != null and actor.validation_status == 'validated' "
             "and _actor_id == _actor_state_id) or (actor == null "
             "and actor_state.creature_kind == 'monster' "
             "and actor_state.equipment_profile != null)",
             "error": "Select an admitted character or a creature with an equipment profile."},
            {"condition": "actor_state.hp > 0 and actor_state.is_alive == true "
             "and not ('incapacitated' in actor_state.conditions or "
             "'paralyzed' in actor_state.conditions or 'petrified' in actor_state.conditions "
             "or 'stunned' in actor_state.conditions or 'unconscious' in actor_state.conditions)",
             "error": "The character cannot change equipment while incapacitated."},
            {"condition": "combat.encounter_active != true or (slot == 'shield' "
             "and combat.turn_active == true and combat.active_actor == _actor_state_id)",
             "error": "Armor changes require time outside combat; "
             "shields require your active turn."},
        ],
        "effects": effects,
        "metadata": {"category": "equipment", "costs_action": False, "result_card": {
            "version": 1, "footprint": "compact", "title": "Equipment changed",
            "summary": [
                {"label": "Creature", "value": {"path": "context._equipment_actor_name"}},
                {"label": "Armor Class", "value": {"path": "context.actor_state.armor_class"}},
                {"label": "Speed", "value": {"path": "context.actor_state.speed"}},
            ],
            "details": [
                {"label": "Item", "value": {"path": "context._equipment_record.name"}},
                {"label": "Untrained armor",
                 "value": {"path": "context._equipment_untrained_armor"}},
            ],
        }},
    }


def movement_budget_tool():
    """Recalculate remaining movement while preserving Dash and movement already spent."""
    return {
        "description": "Internal projection of the active creature's current movement allowance.",
        "parameters": [{"name": "actor_state", "type": "string", "resolve": "entity",
                        "required": True}],
        "effects": [{"condition": "combat.turn_active == true "
                     "and combat.active_actor == _actor_state_id", "on_true": [
            {"set": {"field": "_movement_dashes", "value": 0}},
            {"set": {"field": "_movement_spent", "value": 0}},
            {"set": {"field": "_movement_reduction", "value": 0}},
            {"condition": "combat.dashes_taken != null", "on_true": [
                {"set": {"field": "_movement_dashes", "value": "combat.dashes_taken"}},
            ]},
            {"condition": "combat.movement_spent != null", "on_true": [
                {"set": {"field": "_movement_spent", "value": "combat.movement_spent"}},
            ]},
            {"condition": "actor_state.spell_speed_reduction != null", "on_true": [
                {"set": {"field": "_movement_reduction",
                         "value": "actor_state.spell_speed_reduction"}},
            ]},
            {"set": {"field": "_movement_speed", "value": "max(0, actor_state.speed "
                     "- actor_state.exhaustion_level * 5 - _movement_reduction)"}},
            {"condition": "actor_state.turn_speed_halved == true", "on_true": [
                {"set": {"field": "_movement_speed", "value": "_movement_speed // 2"}},
            ]},
            {"set": {"field": "combat.movement_remaining", "value": "max(0, "
                     "_movement_speed * (1 + _movement_dashes) - _movement_spent)"}},
        ]}],
    }


def equipment_exports():
    """Expose only calculated defenses and movement to equipment consumers."""
    return {name: name for name in (
        "_equipment_ac", "_equipment_speed_penalty", "_equipment_untrained_armor",
        "_equipment_stealth_disadvantage", "_equipment_speed",
        "_equipment_climb_speed", "_equipment_swim_speed",
    )}


def npc_equipment_tool():
    """Adapt stat-block facts to the same armor calculation without character grants."""
    def adapt(value):
        if isinstance(value, dict):
            if "set" in value:
                field = value["set"]["field"]
                replacements = {
                    "_equipment_classes": [],
                    "_equipment_training": "actor_state.equipment_profile.armor_training",
                    "_equipment_ac": "actor_state.equipment_profile.unarmored_ac",
                    "_equipment_climb_speed": "actor_state.equipment_profile.climb_speed",
                    "_equipment_swim_speed": "actor_state.equipment_profile.swim_speed",
                }
                if field in replacements and (
                    field != "_equipment_ac" or
                    value["set"]["value"] == "10 + _equipment_dexterity"
                ):
                    return {"set": {"field": field, "value": replacements[field]}}
            return {key: adapt(item) for key, item in value.items()}
        if isinstance(value, list):
            return [adapt(item) for item in value]
        if isinstance(value, str):
            return value.replace("actor.data.abilities.", "actor_state.equipment_profile.").replace(
                "actor.data", "actor_state.equipment_profile")
        return value

    result = adapt(equipment_tool())
    result["parameters"] = [
        {"name": "actor_state", "type": "string", "resolve": "entity", "required": True},
    ]
    result["pre_conditions"] = [{"condition": "actor_state.creature_kind == 'monster' "
                               "and actor_state.equipment_profile != null",
                               "error": "NPC equipment requires saved stat-block equipment facts."}]
    effects = result["effects"]
    shield_index = next(i for i, effect in enumerate(effects)
                        if effect.get("condition") == "actor_state.worn_shield != null")
    effects.insert(shield_index, {"set": {"field": "_equipment_ac", "value":
        "max(_equipment_ac, actor_state.equipment_profile.unarmored_ac)"}})
    return result


def npc_equipment_parameter():
    """Require explicit source facts rather than reverse-engineering a creature's AC."""
    props = {name: {"type": "integer", "minimum": 0, "maximum": 1000}
             for name in ("speed", "climb_speed", "swim_speed")}
    props.update({name: {"type": "integer", "minimum": 1, "maximum": 30}
                  for name in ("strength", "dexterity")})
    props["unarmored_ac"] = {"type": "integer", "minimum": 1, "maximum": 40}
    props["armor_training"] = {"type": "array", "uniqueItems": True, "items": {
        "type": "string", "enum": ["light", "medium", "heavy", "shield"],
    }}
    for slot in ("armor", "shield"):
        item = worn_item_schema(shield=slot == "shield")
        item["properties"]["inventory_item"].pop("maxLength")
        props[slot] = {"type": "array", "items": item}
    return {"name": "equipment", "type": "array", "required": False, "default": [],
            "max_items": 1, "description":
            "Optional one equipment profile from the stat block or recorded GM adaptation. "
            "Record base speeds before armor, AC without worn gear (including natural defenses), "
            "Strength/Dexterity, armor training, and zero or one carried item in armor/shield. "
            "Supplied final AC and walking Speed must agree. "
            "Omit only when gear changes are not needed.",
            "items": {"type": "object", "properties": props,
                      "required": list(props), "additionalProperties": False}}


def initialize_npc_equipment_tool():
    """Admit optional worn NPC gear atomically with its source stat block."""
    effects = []
    for slot in ("armor", "shield"):
        effects += [
            {"require": {"condition": f"len(equipment[0].{slot}) <= 1",
                         "error": "Only one item may occupy an equipment slot."}},
            {"set": {"field": f"actor_state.worn_{slot}", "value": None}},
            {"condition": f"len(equipment[0].{slot}) == 1", "on_true": [
                {"set": {"field": f"actor_state.worn_{slot}",
                         "value": f"equipment[0].{slot}[0]"}},
            ]},
        ]
    effects += [
        {"set": {"field": "actor_state.equipment_profile", "value": {
            name: f"equipment[0].{name}" for name in (
                "strength", "dexterity", "unarmored_ac", "speed", "climb_speed",
                "swim_speed", "armor_training",
            )
        }}},
        {"call": "resolve_srd_npc_equipment", "params": {"actor_state": "{_actor_state_id}"},
         "export_context": equipment_exports()},
        {"require": {"condition": "actor_state.armor_class == _equipment_ac "
                     "and actor_state.speed == _equipment_speed",
                     "error": "Creature AC and Speed must match the recorded equipment profile."}},
    ]
    for field, local in (
        ("armor_speed_penalty", "_equipment_speed_penalty"),
        ("armor_untrained", "_equipment_untrained_armor"),
        ("armor_stealth_disadvantage", "_equipment_stealth_disadvantage"),
        ("climb_speed", "_equipment_climb_speed"), ("swim_speed", "_equipment_swim_speed"),
    ):
        effects.append({"set": {"field": f"actor_state.{field}", "value": local}})
    return {"description": "Internal NPC equipment admission; no additional time or action cost.",
            "parameters": [{"name": "actor_state", "type": "string", "resolve": "entity",
                            "required": True}, npc_equipment_parameter()],
            "effects": [{"condition": "len(equipment) > 0", "on_true": effects}]}
