from __future__ import annotations

import hashlib
import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

if __package__:
    from .srd_spell_grants import SUBCLASSES, grants_schema
else:
    from srd_spell_grants import SUBCLASSES, grants_schema

import yaml
from pypdf import PdfReader

from sylvia_gm.domain.package_library import (
    CONTENT_DECLARATION_LINK_FAMILY_LIMIT,
    participant_schema_sha256,
)
from sylvia_gm.runtime._parameter_declarations import resolve_parameter_declarations

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = ROOT / "rulesets" / "dnd-srd-5.2.1"
SOURCE_PDF = ROOT / "sources" / "dnd-srd-5.2.1" / "SRD_CC_v5.2.1.pdf"
RECORD_PATH = PACKAGE_ROOT / "package-record.json"
DECLARATIONS_PATH = PACKAGE_ROOT / "content" / "content-declarations.json"
RUNTIME_PATH = PACKAGE_ROOT / "runtime" / "ruleset.yaml"
SOURCE_SHA256 = "8974902d109d6e63672d7c490bde9ccf052410503d9cfa768237154fbc5e3d87"
RESOURCE_NAMES = (
    "action_surge",
    "arcane_recovery",
    "bardic_inspiration",
    "channel_divinity",
    "focus_point",
    "hit_die",
    "indomitable",
    "second_wind",
    "wholeness_of_body",
    "spell_slot_1",
    "spell_slot_2",
    "spell_slot_3",
    "spell_slot_4",
    "spell_slot_5",
    "spell_slot_6",
    "spell_slot_7",
    "spell_slot_8",
    "spell_slot_9",
)
SKILL_NAMES = (
    "acrobatics",
    "animal_handling",
    "arcana",
    "athletics",
    "deception",
    "history",
    "insight",
    "intimidation",
    "investigation",
    "medicine",
    "nature",
    "perception",
    "performance",
    "persuasion",
    "religion",
    "sleight_of_hand",
    "stealth",
    "survival",
)
TOOL_PROFICIENCY_NAMES = (
    "alchemists_supplies",
    "brewers_supplies",
    "calligraphers_supplies",
    "carpenters_tools",
    "cartographers_tools",
    "cobblers_tools",
    "cooks_utensils",
    "disguise_kit",
    "forgery_kit",
    "gaming_set_dice",
    "gaming_set_dragonchess",
    "gaming_set_playing_cards",
    "gaming_set_three_dragon_ante",
    "glassblowers_tools",
    "herbalism_kit",
    "jewelers_tools",
    "leatherworkers_tools",
    "masons_tools",
    "musical_instrument_bagpipes",
    "musical_instrument_drum",
    "musical_instrument_dulcimer",
    "musical_instrument_flute",
    "musical_instrument_horn",
    "musical_instrument_lute",
    "musical_instrument_lyre",
    "musical_instrument_pan_flute",
    "musical_instrument_shawm",
    "musical_instrument_viol",
    "navigators_tools",
    "painters_supplies",
    "poisoners_kit",
    "potters_tools",
    "smiths_tools",
    "thieves_tools",
    "tinkers_tools",
    "weavers_tools",
    "woodcarvers_tools",
)
PLAYER_CHARACTER_SCHEMA_ID = "wotc.srd.5.2.1.player-character"
LEGACY_PLAYER_CHARACTER_SCHEMA_ID = "wotc.srd.5.2.1.player-character.alpha04-d20-basis"
DISTANCE_CAPABILITY = "dnd.srd.5.2.1.distance.measured-feet"
UI_SURFACES = [
    {
        "id": "dnd-party",
        "capability": "dnd.srd.5.2.1.damage-healing-core",
        "title": "Party",
        "icon": "✦",
        "renderer": "party",
        "source": {
            "kind": "object",
            "fields": {
                "entities": {
                    "kind": "state",
                    "namespace": "wotc.srd.5.2.1",
                    "key": "entities",
                },
                "participants": {"kind": "participants", "kinds": ["player_character"]},
            },
        },
        "group": None,
        "order": 10,
        "default_size": 260,
        "minimum_size": 140,
        "default_placement": "docked",
        "allowed_placements": ["docked", "floating", "window"],
        "actions": [],
    },
    {
        "id": "dnd-encounter",
        "capability": "dnd.srd.5.2.1.combat-turn-core",
        "title": "Encounter",
        "icon": "⚔",
        "renderer": "encounter",
        "source": {
            "kind": "object",
            "fields": {
                "combat": {
                    "kind": "state",
                    "namespace": "wotc.srd.5.2.1",
                    "key": "combat",
                },
                "entities": {
                    "kind": "state",
                    "namespace": "wotc.srd.5.2.1",
                    "key": "entities",
                },
                "participants": {"kind": "participants", "kinds": ["player_character"]},
            },
        },
        "group": None,
        "order": 20,
        "default_size": 300,
        "minimum_size": 160,
        "default_placement": "docked",
        "allowed_placements": ["docked", "floating", "window"],
        "actions": [
            {
                "id": "end-combat",
                "label": "End combat",
                "intent": "end_combat_encounter",
                "params": {"end_reason": "fighting_stopped"},
            }
        ],
    },
    {
        "id": "dnd-inventory",
        "capability": "dnd.srd.5.2.1.resources",
        "title": "Inventory",
        "icon": "▣",
        "renderer": "inventory",
        "source": {
            "kind": "object",
            "fields": {
                "entities": {
                    "kind": "state",
                    "namespace": "wotc.srd.5.2.1",
                    "key": "entities",
                },
                "participants": {"kind": "participants", "kinds": ["player_character"]},
            },
        },
        "group": None,
        "order": 30,
        "default_size": 220,
        "minimum_size": 120,
        "default_placement": "docked",
        "allowed_placements": ["docked", "floating", "window"],
        "actions": [],
    },
]
WEAPON_CAPABILITY = "dnd.srd.5.2.1.weapons.base-attacks"
WEAPONS: dict[str, dict[str, Any]] = {
    "battleaxe": {
        "category": "martial",
        "kind": "melee",
        "damage": "1d8",
        "damage_type": "slashing",
        "properties": ["versatile"],
        "mastery": "topple",
        "normal_range": 5,
        "long_range": 5,
        "versatile_damage": "1d10",
    },
    "blowgun": {
        "category": "martial",
        "kind": "ranged",
        "damage": "fixed-1",
        "damage_type": "piercing",
        "properties": ["ammunition", "loading"],
        "mastery": "vex",
        "normal_range": 25,
        "long_range": 100,
        "versatile_damage": None,
    },
    "club": {
        "category": "simple",
        "kind": "melee",
        "damage": "1d4",
        "damage_type": "bludgeoning",
        "properties": ["light"],
        "mastery": "slow",
        "normal_range": 5,
        "long_range": 5,
        "versatile_damage": None,
    },
    "dagger": {
        "category": "simple",
        "kind": "melee",
        "damage": "1d4",
        "damage_type": "piercing",
        "properties": ["finesse", "light", "thrown"],
        "mastery": "nick",
        "normal_range": 20,
        "long_range": 60,
        "versatile_damage": None,
    },
    "dart": {
        "category": "simple",
        "kind": "ranged",
        "damage": "1d4",
        "damage_type": "piercing",
        "properties": ["finesse", "thrown"],
        "mastery": "vex",
        "normal_range": 20,
        "long_range": 60,
        "versatile_damage": None,
    },
    "flail": {
        "category": "martial",
        "kind": "melee",
        "damage": "1d8",
        "damage_type": "bludgeoning",
        "properties": [],
        "mastery": "sap",
        "normal_range": 5,
        "long_range": 5,
        "versatile_damage": None,
    },
    "glaive": {
        "category": "martial",
        "kind": "melee",
        "damage": "1d10",
        "damage_type": "slashing",
        "properties": ["heavy", "reach", "two_handed"],
        "mastery": "graze",
        "normal_range": 10,
        "long_range": 10,
        "versatile_damage": None,
    },
    "greataxe": {
        "category": "martial",
        "kind": "melee",
        "damage": "1d12",
        "damage_type": "slashing",
        "properties": ["heavy", "two_handed"],
        "mastery": "cleave",
        "normal_range": 5,
        "long_range": 5,
        "versatile_damage": None,
    },
    "greatclub": {
        "category": "simple",
        "kind": "melee",
        "damage": "1d8",
        "damage_type": "bludgeoning",
        "properties": ["two_handed"],
        "mastery": "push",
        "normal_range": 5,
        "long_range": 5,
        "versatile_damage": None,
    },
    "greatsword": {
        "category": "martial",
        "kind": "melee",
        "damage": "2d6",
        "damage_type": "slashing",
        "properties": ["heavy", "two_handed"],
        "mastery": "graze",
        "normal_range": 5,
        "long_range": 5,
        "versatile_damage": None,
    },
    "halberd": {
        "category": "martial",
        "kind": "melee",
        "damage": "1d10",
        "damage_type": "slashing",
        "properties": ["heavy", "reach", "two_handed"],
        "mastery": "cleave",
        "normal_range": 10,
        "long_range": 10,
        "versatile_damage": None,
    },
    "hand_crossbow": {
        "category": "martial",
        "kind": "ranged",
        "damage": "1d6",
        "damage_type": "piercing",
        "properties": ["ammunition", "light", "loading"],
        "mastery": "vex",
        "normal_range": 30,
        "long_range": 120,
        "versatile_damage": None,
    },
    "handaxe": {
        "category": "simple",
        "kind": "melee",
        "damage": "1d6",
        "damage_type": "slashing",
        "properties": ["light", "thrown"],
        "mastery": "vex",
        "normal_range": 20,
        "long_range": 60,
        "versatile_damage": None,
    },
    "heavy_crossbow": {
        "category": "martial",
        "kind": "ranged",
        "damage": "1d10",
        "damage_type": "piercing",
        "properties": ["ammunition", "heavy", "loading", "two_handed"],
        "mastery": "push",
        "normal_range": 100,
        "long_range": 400,
        "versatile_damage": None,
    },
    "javelin": {
        "category": "simple",
        "kind": "melee",
        "damage": "1d6",
        "damage_type": "piercing",
        "properties": ["thrown"],
        "mastery": "slow",
        "normal_range": 30,
        "long_range": 120,
        "versatile_damage": None,
    },
    "lance": {
        "category": "martial",
        "kind": "melee",
        "damage": "1d10",
        "damage_type": "piercing",
        "properties": ["heavy", "reach", "two_handed_unless_mounted"],
        "mastery": "topple",
        "normal_range": 10,
        "long_range": 10,
        "versatile_damage": None,
    },
    "light_crossbow": {
        "category": "simple",
        "kind": "ranged",
        "damage": "1d8",
        "damage_type": "piercing",
        "properties": ["ammunition", "loading", "two_handed"],
        "mastery": "slow",
        "normal_range": 80,
        "long_range": 320,
        "versatile_damage": None,
    },
    "light_hammer": {
        "category": "simple",
        "kind": "melee",
        "damage": "1d4",
        "damage_type": "bludgeoning",
        "properties": ["light", "thrown"],
        "mastery": "nick",
        "normal_range": 20,
        "long_range": 60,
        "versatile_damage": None,
    },
    "longbow": {
        "category": "martial",
        "kind": "ranged",
        "damage": "1d8",
        "damage_type": "piercing",
        "properties": ["ammunition", "heavy", "two_handed"],
        "mastery": "slow",
        "normal_range": 150,
        "long_range": 600,
        "versatile_damage": None,
    },
    "longsword": {
        "category": "martial",
        "kind": "melee",
        "damage": "1d8",
        "damage_type": "slashing",
        "properties": ["versatile"],
        "mastery": "sap",
        "normal_range": 5,
        "long_range": 5,
        "versatile_damage": "1d10",
    },
    "mace": {
        "category": "simple",
        "kind": "melee",
        "damage": "1d6",
        "damage_type": "bludgeoning",
        "properties": [],
        "mastery": "sap",
        "normal_range": 5,
        "long_range": 5,
        "versatile_damage": None,
    },
    "maul": {
        "category": "martial",
        "kind": "melee",
        "damage": "2d6",
        "damage_type": "bludgeoning",
        "properties": ["heavy", "two_handed"],
        "mastery": "topple",
        "normal_range": 5,
        "long_range": 5,
        "versatile_damage": None,
    },
    "morningstar": {
        "category": "martial",
        "kind": "melee",
        "damage": "1d8",
        "damage_type": "piercing",
        "properties": [],
        "mastery": "sap",
        "normal_range": 5,
        "long_range": 5,
        "versatile_damage": None,
    },
    "musket": {
        "category": "martial",
        "kind": "ranged",
        "damage": "1d12",
        "damage_type": "piercing",
        "properties": ["ammunition", "loading", "two_handed"],
        "mastery": "slow",
        "normal_range": 40,
        "long_range": 120,
        "versatile_damage": None,
    },
    "pike": {
        "category": "martial",
        "kind": "melee",
        "damage": "1d10",
        "damage_type": "piercing",
        "properties": ["heavy", "reach", "two_handed"],
        "mastery": "push",
        "normal_range": 10,
        "long_range": 10,
        "versatile_damage": None,
    },
    "pistol": {
        "category": "martial",
        "kind": "ranged",
        "damage": "1d10",
        "damage_type": "piercing",
        "properties": ["ammunition", "loading"],
        "mastery": "vex",
        "normal_range": 30,
        "long_range": 90,
        "versatile_damage": None,
    },
    "quarterstaff": {
        "category": "simple",
        "kind": "melee",
        "damage": "1d6",
        "damage_type": "bludgeoning",
        "properties": ["versatile"],
        "mastery": "topple",
        "normal_range": 5,
        "long_range": 5,
        "versatile_damage": "1d8",
    },
    "rapier": {
        "category": "martial",
        "kind": "melee",
        "damage": "1d8",
        "damage_type": "piercing",
        "properties": ["finesse"],
        "mastery": "vex",
        "normal_range": 5,
        "long_range": 5,
        "versatile_damage": None,
    },
    "scimitar": {
        "category": "martial",
        "kind": "melee",
        "damage": "1d6",
        "damage_type": "slashing",
        "properties": ["finesse", "light"],
        "mastery": "nick",
        "normal_range": 5,
        "long_range": 5,
        "versatile_damage": None,
    },
    "shortbow": {
        "category": "simple",
        "kind": "ranged",
        "damage": "1d6",
        "damage_type": "piercing",
        "properties": ["ammunition", "two_handed"],
        "mastery": "vex",
        "normal_range": 80,
        "long_range": 320,
        "versatile_damage": None,
    },
    "shortsword": {
        "category": "martial",
        "kind": "melee",
        "damage": "1d6",
        "damage_type": "piercing",
        "properties": ["finesse", "light"],
        "mastery": "vex",
        "normal_range": 5,
        "long_range": 5,
        "versatile_damage": None,
    },
    "sickle": {
        "category": "simple",
        "kind": "melee",
        "damage": "1d4",
        "damage_type": "slashing",
        "properties": ["light"],
        "mastery": "nick",
        "normal_range": 5,
        "long_range": 5,
        "versatile_damage": None,
    },
    "sling": {
        "category": "simple",
        "kind": "ranged",
        "damage": "1d4",
        "damage_type": "bludgeoning",
        "properties": ["ammunition"],
        "mastery": "slow",
        "normal_range": 30,
        "long_range": 120,
        "versatile_damage": None,
    },
    "spear": {
        "category": "simple",
        "kind": "melee",
        "damage": "1d6",
        "damage_type": "piercing",
        "properties": ["thrown", "versatile"],
        "mastery": "sap",
        "normal_range": 20,
        "long_range": 60,
        "versatile_damage": "1d8",
    },
    "trident": {
        "category": "martial",
        "kind": "melee",
        "damage": "1d8",
        "damage_type": "piercing",
        "properties": ["thrown", "versatile"],
        "mastery": "topple",
        "normal_range": 20,
        "long_range": 60,
        "versatile_damage": "1d10",
    },
    "war_pick": {
        "category": "martial",
        "kind": "melee",
        "damage": "1d8",
        "damage_type": "piercing",
        "properties": ["versatile"],
        "mastery": "sap",
        "normal_range": 5,
        "long_range": 5,
        "versatile_damage": "1d10",
    },
    "warhammer": {
        "category": "martial",
        "kind": "melee",
        "damage": "1d8",
        "damage_type": "bludgeoning",
        "properties": ["versatile"],
        "mastery": "push",
        "normal_range": 5,
        "long_range": 5,
        "versatile_damage": "1d10",
    },
    "whip": {
        "category": "martial",
        "kind": "melee",
        "damage": "1d4",
        "damage_type": "slashing",
        "properties": ["finesse", "reach"],
        "mastery": "slow",
        "normal_range": 10,
        "long_range": 10,
        "versatile_damage": None,
    },
}
WEAPON_AMMUNITION = {
    "blowgun": "needle",
    "hand_crossbow": "bolt",
    "heavy_crossbow": "bolt",
    "light_crossbow": "bolt",
    "longbow": "arrow",
    "musket": "bullet",
    "pistol": "bullet",
    "shortbow": "arrow",
    "sling": "sling_bullet",
}
for weapon_id, ammunition_type in WEAPON_AMMUNITION.items():
    WEAPONS[weapon_id]["ammunition_type"] = ammunition_type
TOOL_CAPABILITIES = {
    "record_srd_creature_skill_checks": "dnd.srd.5.2.1.d20-test",
    "resolve_srd_condition_saving_throw": "dnd.srd.5.2.1.d20-test",
    "resolve_srd_fall": "dnd.srd.5.2.1.damage-healing-core",
    "finish_srd_monster_turn": "dnd.srd.5.2.1.combat-turn-core",
    "prepare_srd_spells": "dnd.srd.5.2.1.resources",
    "begin_srd_rest": "dnd.srd.5.2.1.resources",
    "set_srd_rest_activity": "dnd.srd.5.2.1.resources",
    "complete_srd_rest": "dnd.srd.5.2.1.resources",
    "prepare_srd_creature": "dnd.srd.5.2.1.combat-turn-core",
    "ability_check": "dnd.srd.5.2.1.d20-test",
    "apply_srd_condition": "dnd.srd.5.2.1.conditions",
    "advance_group_combat_encounter_turn": "dnd.srd.5.2.1.combat-turn-core",
    "end_hidden_state": "dnd.srd.5.2.1.action-economy",
    "escape_srd_grapple_action": "dnd.srd.5.2.1.action-economy",
    "gain_srd_exhaustion": "dnd.srd.5.2.1.conditions",
    "help_srd_ability_check_action": "dnd.srd.5.2.1.action-economy",
    "help_srd_attack_action": "dnd.srd.5.2.1.action-economy",
    "hide_srd_action": "dnd.srd.5.2.1.action-economy",
    "influence_srd_action": "dnd.srd.5.2.1.action-economy",
    "reduce_srd_exhaustion": "dnd.srd.5.2.1.conditions",
    "recover_srd_ammunition": WEAPON_CAPABILITY,
    "remove_srd_condition": "dnd.srd.5.2.1.conditions",
    "reset_srd_movement": "dnd.srd.5.2.1.movement-modes",
    "resolve_combat_attack": WEAPON_CAPABILITY,
    "resolve_combat_attack_followup": WEAPON_CAPABILITY,
    "resolve_adjudicated_creature_attack": "dnd.srd.5.2.1.combat-turn-core",
    "command_srd_summoned_creature": "dnd.srd.5.2.1.spells",
    "resolve_srd_summon_breath": "dnd.srd.5.2.1.spells",
    "resolve_opportunity_attack": "dnd.srd.5.2.1.action-economy",
    "resolve_unarmed_strike": "dnd.srd.5.2.1.action-economy",
    "resolve_monk_bonus_strike": "dnd.srd.5.2.1.action-economy",
    "use_monk_focus_action": "dnd.srd.5.2.1.action-economy",
    "resolve_unarmed_opportunity_attack": "dnd.srd.5.2.1.action-economy",
    "restore_srd_resource": "dnd.srd.5.2.1.resources",
    "search_srd_action": "dnd.srd.5.2.1.action-economy",
    "spend_srd_bonus_action": "dnd.srd.5.2.1.action-economy",
    "spend_srd_movement": "dnd.srd.5.2.1.movement-modes",
    "spend_srd_reaction": "dnd.srd.5.2.1.action-economy",
    "spend_srd_resource": "dnd.srd.5.2.1.resources",
    "start_group_combat_encounter": "dnd.srd.5.2.1.combat-turn-core",
    "start_combat_encounter_entities": "dnd.srd.5.2.1.combat-turn-core",
    "study_srd_action": "dnd.srd.5.2.1.action-economy",
    "take_disengage_action": "dnd.srd.5.2.1.action-economy",
    "take_dodge_action": "dnd.srd.5.2.1.action-economy",
    "take_srd_short_rest_hit_die": "dnd.srd.5.2.1.resources",
    "use_srd_second_wind": "dnd.srd.5.2.1.resources",
    "use_monk_wholeness_of_body": "dnd.srd.5.2.1.resources",
    "use_srd_action_surge": "dnd.srd.5.2.1.action-economy",
    "trigger_opportunity_attack": "dnd.srd.5.2.1.action-economy",
    "unbound_d20_test": "dnd.srd.5.2.1.d20-test",
}
MANIFEST_PARAMETER_CONSTRAINTS: dict[str, dict[str, dict[str, Any]]] = {
    "prepare_srd_creature": {
        "creature_id": {"pattern": r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$(?![\s\S])"},
        "name": {"minLength": 1, "maxLength": 120},
        "source": {"minLength": 1, "maxLength": 1000},
    },
    "restore_hit_points": {"amount": {"minimum": 0}},
}


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _replace_bytes(path: Path, value: bytes) -> None:
    """Publish one fully written generated output through same-directory replace."""

    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _tool_schema(
    tool: dict[str, Any],
    existing_schema: dict[str, Any] | None = None,
    *,
    tool_name: str = "",
) -> dict[str, Any]:
    existing_schema = existing_schema or {}
    existing_properties = existing_schema.get("properties", {})
    properties: dict[str, Any] = {}
    required: list[str] = []
    for parameter in tool.get("parameters", []):
        if parameter.get("from_state") is not None:
            continue
        parameter_type = parameter.get("type", "string")
        child: dict[str, Any]
        if parameter_type == "enum":
            child = {"type": "string", "enum": list(parameter["enum_values"])}
        elif parameter_type in {"string", "entity_id"}:
            child = {"type": "string"}
        elif parameter_type == "entity_id_list":
            child = {"type": "array", "items": {"type": "string"}}
        else:
            child = {"type": parameter_type}
        parameter_name = parameter["name"]
        existing_child = existing_properties.get(parameter_name, {})
        if isinstance(existing_child, dict):
            child = {**existing_child, **child}
        reference_kind = parameter.get("resolve")
        if reference_kind == "participant":
            child["x-sylvia-gm-reference"] = "participant"
        elif reference_kind == "entity":
            child["x-sylvia-gm-reference"] = "entity"
        elif reference_kind == "entity_list" and isinstance(child.get("items"), dict):
            child["items"]["x-sylvia-gm-reference"] = "entity"
        if parameter.get("description"):
            child["description"] = parameter["description"]
        if "model_derive" in parameter:
            child["x-sylvia-gm-derived"] = parameter["model_derive"]
        if parameter_type == "array" and "items" in parameter:
            child["items"] = parameter["items"]
        if parameter.get("model_hidden") is True:
            child["x-sylvia-gm-model-hidden"] = True
        if parameter.get("model_required") is True:
            child["x-sylvia-gm-model-required"] = True
        for constraint in ("minimum", "maximum"):
            if constraint in parameter:
                child[constraint] = parameter[constraint]
        for source_name, schema_name in (
            ("min_items", "minItems"),
            ("max_items", "maxItems"),
            ("unique_items", "uniqueItems"),
        ):
            if source_name in parameter:
                child[schema_name] = parameter[source_name]
        child.update(MANIFEST_PARAMETER_CONSTRAINTS.get(tool_name, {}).get(parameter_name, {}))
        properties[parameter_name] = child
        # The runtime treats omitted `required` as required; the declaration
        # generator must preserve that admission contract rather than silently
        # weakening an exposed tool schema.
        if parameter.get("required", True):
            required.append(parameter_name)
    return {
        **existing_schema,
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _range_sha(reader: PdfReader, first: int, last: int) -> str:
    text = "\n\f\n".join(
        (reader.pages[page - 1].extract_text() or "") for page in range(first, last + 1)
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _split_spell_runtime_tool_links(
    declarations: dict[str, Any], public_tools: list[dict[str, Any]]
) -> None:
    """Link spell tools to their own content group within the existing inclusion bound."""
    gameplay = next(
        row for row in declarations["inclusions"] if row["id"] == "srd-5.2.1-gameplay-runtime"
    )
    spell_names = {
        row["name"] for row in public_tools if row["capability"] == "dnd.srd.5.2.1.spells"
    }
    gameplay["links"]["tools"] = sorted({row["name"] for row in public_tools} - spell_names)
    spell_id = "srd-5.2.1-spell-runtime"
    declarations["inclusions"] = [
        row
        for row in declarations["inclusions"]
        if row["id"] != spell_id and not row["id"].startswith(spell_id + "-")
    ]
    ordered = sorted(spell_names)
    for offset in range(0, len(ordered), CONTENT_DECLARATION_LINK_FAMILY_LIMIT):
        spell_content = {key: deepcopy(value) for key, value in gameplay.items() if key != "links"}
        ordinal = offset // CONTENT_DECLARATION_LINK_FAMILY_LIMIT + 1
        spell_content.update(
            id=spell_id if ordinal == 1 else f"{spell_id}-{ordinal}", family="spells"
        )
        spell_content["links"] = {
            "artifacts": [
                "content/spell-mechanics-source.json",
                "content/spell-mechanics.yaml",
                "content/spell-status-inventory.json",
                "runtime/ruleset.yaml",
            ],
            "capabilities": ["dnd.srd.5.2.1.spells"],
            "mechanics": [],
            "procedures": [],
            "prompt_fragments": [],
            "schemas": [],
            "state_namespaces": deepcopy(gameplay["links"]["state_namespaces"]),
            "tools": ordered[offset : offset + CONTENT_DECLARATION_LINK_FAMILY_LIMIT],
            "ui_surfaces": [],
        }
        declarations["inclusions"].append(spell_content)
    declarations["inclusions"].sort(key=lambda row: row["id"])


def main(*, character_creation_only: bool = False, runtime_declarations_only: bool = False) -> None:
    if hashlib.sha256(SOURCE_PDF.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise SystemExit("Refusing to build mechanics declarations from unrecognized SRD bytes")
    runtime = resolve_parameter_declarations(
        yaml.safe_load(RUNTIME_PATH.read_text(encoding="utf-8"))
    )
    tools = runtime["tools"]
    missing = sorted(set(TOOL_CAPABILITIES) - set(tools))
    if missing:
        raise SystemExit(f"Runtime is missing selected mechanics tools: {missing}")

    record = json.loads(RECORD_PATH.read_text(encoding="utf-8"))
    public_predicates = {
        declaration["name"]: declaration.get("state_predicates", [])
        for declaration in record["tools"]
    }
    public_predicates["record_srd_creature_skill_checks"] = []
    public_predicates["resolve_srd_condition_saving_throw"] = []
    public_predicates["resolve_srd_fall"] = []
    public_predicates["finish_srd_monster_turn"] = [
        {"namespace": "wotc.srd.5.2.1", "key": "combat.phase", "equals": "turn_active"},
        {"namespace": "wotc.srd.5.2.1", "key": "combat.active_actor_kind", "equals": "monster"},
        {"namespace": "wotc.srd.5.2.1", "key": "combat.action_available", "equals": False},
        {"namespace": "wotc.srd.5.2.1", "key": "combat.attack_available", "equals": False},
    ]
    public_predicates["trigger_opportunity_attack"] = [
        {
            "namespace": "wotc.srd.5.2.1",
            "key": "combat.phase",
            "equals": "turn_active",
        },
        {
            "namespace": "wotc.srd.5.2.1",
            "key": "combat.opportunity_attack_pending",
            "equals": True,
            "negated": True,
        },
    ]
    public_predicates["resolve_opportunity_attack"] = [
        {
            "namespace": "wotc.srd.5.2.1",
            "key": "combat.opportunity_attack_pending",
            "equals": True,
        }
    ]
    public_predicates["resolve_unarmed_opportunity_attack"] = [
        *public_predicates["resolve_opportunity_attack"],
        {"namespace": "wotc.srd.5.2.1", "key": "combat.pending_reaction.unarmed", "equals": True},
    ]
    public_predicates["resolve_opportunity_attack"].append(
        {
            "namespace": "wotc.srd.5.2.1",
            "key": "combat.pending_reaction.unarmed",
            "equals": True,
            "negated": True,
        }
    )
    inactive_combat_predicate = {
        "namespace": "wotc.srd.5.2.1",
        "key": "combat.encounter_active",
        "equals": True,
        "negated": True,
    }
    public_predicates["start_group_combat_encounter"] = [inactive_combat_predicate]
    public_predicates["prepare_srd_creature"] = [inactive_combat_predicate]
    # The GM must be able to stop fighting at a decision boundary. Requiring an inactive
    # turn makes this unreachable because the engine automatically advances/begins turns.
    public_predicates["end_combat_encounter"] = [
        {
            "namespace": "wotc.srd.5.2.1",
            "key": "combat.encounter_active",
            "equals": True,
        }
    ]
    public_predicates["take_srd_short_rest_hit_die"] = [inactive_combat_predicate]
    for rest_operation in (
        "begin_srd_rest",
        "set_srd_rest_activity",
        "complete_srd_rest",
        "prepare_srd_spells",
    ):
        public_predicates[rest_operation] = [inactive_combat_predicate]
    public_predicates["use_srd_second_wind"] = []
    public_predicates["use_monk_wholeness_of_body"] = []
    public_predicates["command_srd_summoned_creature"] = []
    for attack_name in (
        "resolve_combat_attack",
        "resolve_unarmed_strike",
        "resolve_adjudicated_creature_attack",
    ):
        public_predicates[attack_name] = [
            {**predicate, "key": "combat.attack_available"}
            if predicate.get("key") == "combat.action_available"
            else predicate
            for predicate in public_predicates[attack_name]
        ]
    public_predicates["spend_combat_movement"] = [
        predicate
        for predicate in public_predicates["spend_combat_movement"]
        if predicate.get("key") != "combat.active_actor_kind"
    ]
    can_act = {"namespace": "wotc.srd.5.2.1", "key": "combat.active_actor_can_act", "equals": True}
    if can_act not in public_predicates["resolve_adjudicated_creature_attack"]:
        public_predicates["resolve_adjudicated_creature_attack"].append(can_act)
    public_predicates["resolve_srd_summon_breath"] = [
        {"namespace": "wotc.srd.5.2.1", "key": "combat.phase", "equals": "turn_active"},
        {"namespace": "wotc.srd.5.2.1", "key": "combat.attack_available", "equals": True},
        {"namespace": "wotc.srd.5.2.1", "key": "combat.active_actor_kind", "equals": "monster"},
        can_act,
    ]
    public_predicates["resolve_monk_bonus_strike"] = [
        {"namespace": "wotc.srd.5.2.1", "key": "combat.phase", "equals": "turn_active"},
        {"namespace": "wotc.srd.5.2.1", "key": "combat.active_actor_kind", "equals": "character"},
        can_act,
        {
            "namespace": "wotc.srd.5.2.1",
            "key": "combat.monk_bonus_strike_available",
            "equals": True,
        },
    ]
    public_predicates["use_monk_focus_action"] = [
        {**predicate, "key": "combat.monk_focus_action_available"}
        if predicate.get("key") == "combat.monk_bonus_strike_available"
        else predicate
        for predicate in public_predicates["resolve_monk_bonus_strike"]
    ]
    public_predicates["use_srd_action_surge"] = [
        {"namespace": "wotc.srd.5.2.1", "key": "combat.phase", "equals": "turn_active"},
        {"namespace": "wotc.srd.5.2.1", "key": "combat.active_actor_kind", "equals": "character"},
        {"namespace": "wotc.srd.5.2.1", "key": "combat.active_actor_can_act", "equals": True},
        {
            "namespace": "wotc.srd.5.2.1",
            "key": "combat.action_surge_used",
            "equals": True,
            "negated": True,
        },
    ]
    public_predicates["ability_check"] = [inactive_combat_predicate]
    public_predicates["advance_group_combat_encounter_turn"] = [
        {
            "namespace": "wotc.srd.5.2.1",
            "key": "combat.phase",
            "equals": "awaiting_advance",
        },
        {
            "namespace": "wotc.srd.5.2.1",
            "key": "combat.group_order",
            "equals": True,
        },
    ]
    legacy_advance_predicates = [
        predicate
        for predicate in public_predicates["advance_combat_encounter_turn"]
        if not (
            predicate.get("namespace") == "wotc.srd.5.2.1"
            and predicate.get("key") == "combat.group_order"
        )
    ]
    public_predicates["advance_combat_encounter_turn"] = [
        *legacy_advance_predicates,
        {
            "namespace": "wotc.srd.5.2.1",
            "key": "combat.group_order",
            "equals": True,
            "negated": True,
        },
    ]
    participant_artifact = next(
        item for item in record["participant_artifacts"] if item["kind"] == "player_character"
    )
    participant_schema = participant_artifact["schema"]
    participant_schema["$id"] = PLAYER_CHARACTER_SCHEMA_ID
    participant_schema["properties"]["abilities"]["description"] = (
        "Final ability scores after generation and all selected creation adjustments. "
        "Do not save the unadjusted standard array. Apply the selected background's "
        "required increases: +2 to one and +1 to another of its three listed abilities, "
        "or +1 to all three, without increasing any score above 20 through that choice. "
        "Read the selected background in the contained SRD Character Origins reference."
    )
    participant_schema["properties"]["background"]["description"] = (
        "The selected SRD background. Read its ability-score choices, feat, skill and "
        "tool proficiencies, and starting equipment before completing the character."
    )
    participant_schema["properties"]["species"]["description"] = (
        "The selected SRD species. Read all fixed traits and choices for that species. Record "
        "granted proficiencies in their participant fields and every selected Origin feat in "
        "origin_feats; preserve fixed traits that have no declared engine field in the authored "
        "character profile. Human Skillful grants one extra skill; Human Versatile grants an "
        "Origin feat in addition to the background feat. These are separate choices."
    )
    participant_schema["properties"]["origin_feats"] = {
        "type": "array",
        "description": (
            "Every selected SRD Origin feat, including the background feat and any additional "
            "species-granted choice. This records the choices; it does not claim their mechanics "
            "are automated. Preserve separate selections of a repeatable feat and record its "
            "chosen benefits in the authored profile."
        ),
        "items": {
            "type": "string",
            "enum": ["alert", "magic_initiate", "savage_attacker", "skilled"],
        },
        "minItems": 1,
    }
    participant_schema["properties"]["skill_proficiencies"] = {
        "type": "array",
        "description": (
            "Complete skill and tool choices source by source: class, background, species, then "
            "each Origin feat. Record each source's required number and selected proficiencies "
            "in the authored profile, then put the complete distinct skill list here. Human "
            "Skillful grants one skill; each Skilled selection grants three skill or tool "
            "proficiencies. Put tool choices in tool_proficiencies. A repeated proficiency does "
            "not fulfill another choice: select a different legal proficiency where permitted."
        ),
        "items": {"type": "string", "enum": list(SKILL_NAMES)},
        "uniqueItems": True,
    }
    participant_schema["properties"]["tool_proficiencies"] = {
        "type": "array",
        "items": {"type": "string", "enum": list(TOOL_PROFICIENCY_NAMES)},
        "uniqueItems": True,
    }
    participant_schema["properties"]["languages"] = {
        "type": "array",
        "description": "Known languages from the character's actual source choices and grants.",
        "items": {"type": "string", "minLength": 1},
        "uniqueItems": True,
    }
    participant_schema["properties"]["weapon_proficiencies"] = {
        "type": "array",
        "items": {
            "type": "string",
            "enum": ["simple_weapons", "martial_weapons", *sorted(WEAPONS)],
        },
        "uniqueItems": True,
    }
    participant_schema["properties"]["weapon_masteries"] = {
        "type": "array",
        "items": {
            "type": "string",
            "enum": sorted(WEAPONS),
        },
        "uniqueItems": True,
    }
    participant_schema["properties"]["saving_throw_proficiencies"] = {
        "type": "array",
        "items": {
            "type": "string",
            "enum": [
                "strength",
                "dexterity",
                "constitution",
                "intelligence",
                "wisdom",
                "charisma",
            ],
        },
        "uniqueItems": True,
    }
    participant_schema["properties"].pop("spellcasting_ability_by_spell", None)
    participant_schema["properties"].pop("spellcasting_focus_by_spell", None)
    participant_schema["properties"]["spellcasting_grants"] = grants_schema()
    participant_schema["properties"]["subclasses"] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            name: {"type": "string", "enum": [choice]} for name, choice in SUBCLASSES.items()
        },
    }
    participant_schema["properties"]["wizard_spellbook"] = {
        "type": "array",
        "items": {"type": "string", "minLength": 1},
        "uniqueItems": True,
        "maxItems": 339,
    }
    participant_schema["properties"]["size_rank"] = {
        "type": "integer",
    }
    participant_schema["properties"]["party_role"] = {
        "type": "string",
        "enum": ["player_character", "companion"],
    }
    participant_schema["properties"]["level"]["maximum"] = 20
    participant_schema["properties"]["classes"]["minItems"] = 1
    participant_schema["properties"]["classes"]["maxItems"] = 20
    participant_schema["properties"]["classes"]["items"]["properties"]["level"]["maximum"] = 20
    participant_artifact["schema_id"] = PLAYER_CHARACTER_SCHEMA_ID
    creation_contract = json.loads(
        (PACKAGE_ROOT / "content" / "character-creation-state.json").read_text(encoding="utf-8")
    )
    participant_artifact.update(creation_contract)
    for field in ("speed", "size_rank", "saving_throw_proficiencies", "origin_feats"):
        if field not in participant_schema["required"]:
            participant_schema["required"].append(field)
    participant_schema["additionalProperties"] = False
    participant_artifact["schema_sha256"] = participant_schema_sha256(
        participant_schema, **creation_contract
    )
    if character_creation_only:
        _replace_bytes(RECORD_PATH, _canonical(record))
        print("Built SRD character-creation declaration and exact schema hash")
        return
    retained: list[dict[str, Any]] = []
    for declaration in record["tools"]:
        if declaration["name"] in TOOL_CAPABILITIES:
            continue
        synchronized = dict(declaration)
        runtime_tool = tools.get(declaration["name"])
        if isinstance(runtime_tool, dict):
            synchronized["input_schema"] = _tool_schema(
                runtime_tool,
                synchronized.get("input_schema"),
                tool_name=declaration["name"],
            )
        retained.append(synchronized)
    generated = [
        {
            "name": name,
            "capability": TOOL_CAPABILITIES[name],
            "writers": ["dm"],
            "turn_statuses": ["open"],
            "state_predicates": [],
            "input_schema": _tool_schema(
                tools[name],
                next(
                    (
                        declaration.get("input_schema")
                        for declaration in record["tools"]
                        if declaration["name"] == name
                    ),
                    None,
                ),
                tool_name=name,
            ),
        }
        for name in sorted(TOOL_CAPABILITIES)
    ]
    all_declarations = {item["name"]: item for item in [*retained, *generated]}
    bookkeeping_name = "update_srd_carried_inventory"
    all_declarations[bookkeeping_name] = {
        "name": bookkeeping_name,
        "capability": "dnd.srd.5.2.1.damage-healing-core",
        "writers": ["dm"],
        "turn_statuses": ["open"],
        "state_predicates": [],
        "input_schema": {
            **_tool_schema(tools[bookkeeping_name], tool_name=bookkeeping_name),
            "x-sylvia-gm-character-update": {
                "namespace": "wotc.srd.5.2.1",
                "key": "entities",
            },
            "x-sylvia-gm-inventory": {
                "field": "inventory",
                "format": "string_list",
                "surface_id": "dnd-inventory",
            },
        },
    }
    # Equipment changes still need a saved disposition during an encounter. This schema
    # only records carried items; it cannot replace an action, HP or resource mechanic.
    public_predicates[bookkeeping_name] = []
    missing_model_tools = sorted(set(public_predicates) - set(all_declarations))
    if missing_model_tools:
        raise SystemExit(f"Model availability references undeclared tools: {missing_model_tools}")
    record["tools"] = []
    for name in sorted(public_predicates):
        declaration = dict(all_declarations[name])
        declaration["state_predicates"] = public_predicates[name]
        record["tools"].append(declaration)
    record["capabilities"] = sorted(
        set(record["capabilities"])
        | set(TOOL_CAPABILITIES.values())
        | {"dnd.srd.5.2.1.corpus", DISTANCE_CAPABILITY}
    )
    record["ui_surfaces"] = UI_SURFACES
    record["compatibility"]["requires"] = sorted(
        set(record["compatibility"]["requires"]) | {"entity_creation.v1"}
    )
    record["prompt_fragments"] = [
        {
            "id": "gm-operating-guide",
            "capability": "dnd.srd.5.2.1.corpus",
        }
    ]
    if runtime_declarations_only:
        declarations = json.loads(DECLARATIONS_PATH.read_text(encoding="utf-8"))
        _split_spell_runtime_tool_links(declarations, record["tools"])
        _replace_bytes(RECORD_PATH, _canonical(record))
        _replace_bytes(DECLARATIONS_PATH, _canonical(declarations))
        print("Built SRD character and runtime declarations without historical evidence changes")
        return
    reader = PdfReader(SOURCE_PDF)
    weapon_page_sha256 = _range_sha(reader, 89, 91)
    weapon_source_path = PACKAGE_ROOT / "content" / "weapon-catalog.source.json"
    _replace_bytes(
        weapon_source_path,
        _canonical(
            {
                "authority_id": "wotc.srd.5.2.1.pdf",
                "claim_status": "BASE_ATTACKS_MAPPED_PROPERTIES_IN_PROGRESS",
                "format_id": "sylvia-gm-srd-weapon-catalog",
                "format_version": 1,
                "source_sha256": SOURCE_SHA256,
                "source_unit_id": "srd.5.2.1.mechanics.weapon-catalog-and-properties",
                "locator": {
                    "kind": "pdf_page_range",
                    "pages": [89, 91],
                    "content_sha256": weapon_page_sha256,
                },
                "weapons": WEAPONS,
                "mapped": [
                    "weapon category and melee/ranged classification",
                    "base and versatile damage dice and damage type",
                    "normal and long range including Reach",
                    "ability and proficiency derivation",
                    "Finesse, Heavy, Reach, Thrown, Two-Handed, and Versatile attack constraints",
                    "ammunition availability and per-attack expenditure",
                    "post-fight recovery of half the expended ammunition after one minute",
                    "Light-property extra Bonus Action attack with a different Light weapon",
                    "Nick mastery moves the Light extra attack into the Attack action",
                    (
                        "Graze mastery optionally deals only the attack ability modifier "
                        "when the Attack-action weapon path misses"
                    ),
                ],
                "not_yet_mapped": [
                    "loading attack-count enforcement",
                    "Graze on Opportunity Attacks and other weapon-attack paths",
                    "weapon mastery effects other than Nick and Graze",
                ],
            }
        ),
    )
    runtime_definitions = runtime.setdefault("definitions", {})
    if not isinstance(runtime_definitions, dict):
        raise SystemExit("Runtime definitions must be an object")
    if runtime_definitions.get("weapons") != WEAPONS:
        raise SystemExit("Runtime weapon definitions do not match the traced SRD catalog")

    _replace_bytes(RECORD_PATH, _canonical(record))

    source_ranges = (
        ("srd.5.2.1.mechanics.actions-bonus-actions-reactions", 9, 10),
        ("srd.5.2.1.mechanics.combat-turn-core", 13, 15),
        ("srd.5.2.1.mechanics.class-resources", 28, 82),
        ("srd.5.2.1.mechanics.movement", 14, 14),
        ("srd.5.2.1.mechanics.measured-feet-distance", 14, 15),
        ("srd.5.2.1.mechanics.short-rest-hit-die", 187, 187),
        ("srd.5.2.1.mechanics.long-rest", 185, 185),
        ("srd.5.2.1.mechanics.conditions-and-movement-glossary", 176, 191),
        ("srd.5.2.1.mechanics.tool-proficiency-catalog", 93, 94),
        ("srd.5.2.1.mechanics.weapon-catalog-and-properties", 89, 91),
    )
    sources = sorted(
        [
            {
                "authority_id": "wotc.srd.5.2.1.pdf",
                "source_unit_id": source_unit_id,
                "locator": {
                    "kind": "pdf_page_range",
                    "pages": [first, last],
                    "content_sha256": _range_sha(reader, first, last),
                },
            }
            for source_unit_id, first, last in source_ranges
        ],
        key=lambda item: (item["authority_id"], item["source_unit_id"]),
    )
    help_source = json.loads(
        (PACKAGE_ROOT / "content" / "help-action.source.json").read_text(encoding="utf-8")
    )
    unarmed_source = json.loads(
        (PACKAGE_ROOT / "content" / "unarmed-strike.source.json").read_text(encoding="utf-8")
    )
    sources = sorted(
        [
            *sources,
            *[
                {
                    "authority_id": help_source["authority_id"],
                    "source_unit_id": fragment["source_unit_id"],
                    "locator": {
                        "kind": "pdf_page_section",
                        "page": fragment["page"],
                        "section_path": fragment["section_path"],
                        "derived_text_sha256": fragment["derived_text_sha256"],
                    },
                }
                for fragment in help_source["fragments"]
            ],
            *[
                {
                    "authority_id": unarmed_source["authority_id"],
                    "source_unit_id": fragment["source_unit_id"],
                    "locator": {
                        "kind": "pdf_page_section",
                        "page": fragment["page"],
                        "section_path": fragment["section_path"],
                        "derived_text_sha256": fragment["derived_text_sha256"],
                    },
                }
                for fragment in unarmed_source["fragments"]
            ],
        ],
        key=lambda item: (item["authority_id"], item["source_unit_id"]),
    )
    mechanics = sorted(TOOL_CAPABILITIES)
    capabilities = sorted(set(TOOL_CAPABILITIES.values()) | {DISTANCE_CAPABILITY})
    inclusion = {
        "id": "alpha04-generic-resources-conditions-movement",
        "family": "generic-srd-mechanics",
        "kind": "mechanic",
        "disposition": "included_mechanical",
        "classifications": ["official_licensed_content", "sylvia_gm_implementation"],
        "sources": sources,
        "links": {
            "artifacts": [
                "content/help-action.source.json",
                "content/unarmed-strike.source.json",
                "content/weapon-catalog.source.json",
                "runtime/ruleset.yaml",
            ],
            "capabilities": capabilities,
            "mechanics": mechanics,
            "procedures": [],
            "prompt_fragments": [],
            "schemas": [PLAYER_CHARACTER_SCHEMA_ID],
            "state_namespaces": [],
            "tools": sorted(set(mechanics) & set(public_predicates)),
            "ui_surfaces": sorted(surface["id"] for surface in UI_SURFACES),
        },
        "evidence": {
            "tests": [
                "alpha04-srd-help-action",
                "alpha04-srd-resources-conditions-movement",
                "alpha04-srd-unarmed-strike",
            ]
        },
    }
    guide_inclusion = {
        "id": "sylvia-gm-operating-guide",
        "family": "sylvia-gm-agent-guidance",
        "kind": "prompt_fragment",
        "disposition": "included_nonmechanical",
        "classifications": ["sylvia_gm_implementation"],
        "sources": [],
        "links": {
            "artifacts": ["prompts/gm-operating-guide.md"],
            "capabilities": [],
            "mechanics": [],
            "procedures": [],
            "prompt_fragments": ["gm-operating-guide"],
            "schemas": [],
            "state_namespaces": [],
            "tools": [],
            "ui_surfaces": [],
        },
        "evidence": {"tests": ["model-input-ruleset-operating-guide"]},
    }
    declarations = json.loads(DECLARATIONS_PATH.read_text(encoding="utf-8"))
    for item in declarations["inclusions"]:
        classifications = item["classifications"]
        if len(classifications) != len(set(classifications)):
            raise SystemExit(f"Content declaration {item['id']!r} has duplicate classifications")
        item["classifications"] = sorted(classifications)
        item["links"]["schemas"] = [
            PLAYER_CHARACTER_SCHEMA_ID
            if schema_id == LEGACY_PLAYER_CHARACTER_SCHEMA_ID
            else schema_id
            for schema_id in item["links"]["schemas"]
        ]
        if item["family"] == "official-srd-corpus":
            item["links"]["capabilities"] = sorted(
                set(item["links"]["capabilities"]) | {"dnd.srd.5.2.1.corpus"}
            )
        if item["id"] == "character-d20-basis":
            item["links"]["schemas"] = [PLAYER_CHARACTER_SCHEMA_ID]
    declarations["inclusions"] = sorted(
        [
            item
            for item in declarations["inclusions"]
            if item["id"] not in {inclusion["id"], guide_inclusion["id"]}
        ]
        + [inclusion, guide_inclusion],
        key=lambda item: item["id"],
    )
    _replace_bytes(DECLARATIONS_PATH, _canonical(declarations))
    print(
        "Built SRD 5.2.1 selected mechanics declarations:",
        len(generated),
        "tools,",
        len(capabilities),
        "capabilities",
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--character-creation-only",
        action="store_true",
        help="Update character declarations without rebuilding historical mechanics evidence.",
    )
    parser.add_argument(
        "--runtime-declarations-only",
        action="store_true",
        help="Update character and public tools without rebuilding historical evidence.",
    )
    args = parser.parse_args()
    main(
        character_creation_only=args.character_creation_only,
        runtime_declarations_only=args.runtime_declarations_only,
    )
