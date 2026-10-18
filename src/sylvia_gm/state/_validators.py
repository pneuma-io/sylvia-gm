"""Custom state validators beyond JSON Schema.

These validators enforce cross-field constraints (e.g., hp ≤ max_hp),
ruleset-specific rules, and pre-initialized field presence.

All validators return a list of error strings (empty = valid).
"""

from __future__ import annotations

from typing import Any

from ._ruleset_config import (
    VALID_CONDITIONS,
    get_ruleset_config,
)

# Regex for spell slot keys: allows level 0 (cantrips) through 9.
_SPELL_SLOT_KEY_RE = r"^[0-9]$"


def validate_all(state: dict[str, Any]) -> list[str]:
    """Run all custom validators on the state dict.

    Returns a list of error strings. An empty list means valid.
    """
    errors: list[str] = list(_validate_cross_refs(state))
    errors.extend(_validate_hp(state))
    errors.extend(_validate_spell_slots(state))
    errors.extend(_validate_ruleset(state))
    errors.extend(_validate_pre_init(state))
    return errors


# ---------------------------------------------------------------------------
# Cross-reference validators
# ---------------------------------------------------------------------------


def _validate_cross_refs(state: dict[str, Any]) -> list[str]:
    """Validate that combat participants and party members reference real entities."""
    errors: list[str] = []

    combat = state.get("combat")
    if combat is not None and isinstance(combat, dict):
        errors.extend(_validate_combat_refs(state, combat))
        errors.extend(_validate_party_refs(state))

    return errors


def _validate_combat_refs(state: dict[str, Any], combat: dict[str, Any]) -> list[str]:
    """Validate that combat participants reference existing entities."""
    errors: list[str] = []
    all_ids: set[str] = _collect_entity_ids(state)

    participants = combat.get("participants", [])
    for pid in participants:
        if pid not in all_ids:
            errors.append(f"Combat participant {pid!r} does not exist")

    order = combat.get("order", [])
    for pid in order:
        if pid not in all_ids:
            errors.append(f"Combat order entry {pid!r} does not exist")
        elif pid not in participants:
            errors.append(
                f"Combat order entry {pid!r} is not in participants list"
            )

    return errors


def _validate_party_refs(state: dict[str, Any]) -> list[str]:
    """Validate that party members reference existing characters."""
    errors: list[str] = []
    party = state.get("party", {})
    characters = state.get("characters", {})

    members = party.get("members", [])
    for mid in members:
        if mid not in characters:
            errors.append(f"Party member {mid!r} does not exist as a character")

    return errors


# ---------------------------------------------------------------------------
# HP validation
# ---------------------------------------------------------------------------


def _validate_hp(state: dict[str, Any]) -> list[str]:
    """Validate that hp ≤ max_hp for all entities with HP."""
    errors: list[str] = []

    for section, label in [
        ("characters", "Character"),
        ("monsters", "Monster"),
    ]:
        entities = state.get(section, {})
        for eid, entity in entities.items():
            if not isinstance(entity, dict):
                continue
            hp = entity.get("hp")
            max_hp = entity.get("max_hp")
            if hp is not None and max_hp is not None:
                if isinstance(hp, int) and isinstance(max_hp, int):
                    if hp < 0:
                        errors.append(f"{label} {eid!r}: hp cannot be negative")
                    if hp > max_hp:
                        errors.append(
                            f"{label} {eid!r}: hp ({hp}) exceeds max_hp ({max_hp})"
                        )
                    if max_hp <= 0:
                        errors.append(
                            f"{label} {eid!r}: max_hp must be positive"
                        )

    return errors


# ---------------------------------------------------------------------------
# Spell slot validation (H10 — allows cantrip level 0)
# ---------------------------------------------------------------------------


def _validate_spell_slots(state: dict[str, Any]) -> list[str]:
    """Validate spell slot keys match ^[0-9]$ and values are int >= 0."""
    import re

    errors: list[str] = []
    _re = re.compile(_SPELL_SLOT_KEY_RE)

    characters = state.get("characters", {})
    for eid, entity in characters.items():
        if not isinstance(entity, dict):
            continue
        resources = entity.get("resources")
        if not isinstance(resources, dict):
            continue
        spell_slots = resources.get("spell_slots")
        if spell_slots is None or not isinstance(spell_slots, dict):
            continue
        for key, value in spell_slots.items():
            key_str = str(key)
            if not _re.match(key_str):
                errors.append(
                    f"Character {eid!r}: spell slot key {key_str!r} "
                    f"must match ^[0-9]$"
                )
            if not isinstance(value, int) or value < 0:
                errors.append(
                    f"Character {eid!r}: spell slot {key_str!r} "
                    f"value must be int >= 0"
                )

    return errors


# ---------------------------------------------------------------------------
# Ruleset-specific validation
# ---------------------------------------------------------------------------


def _validate_ruleset(state: dict[str, Any]) -> list[str]:
    """Validate ruleset-specific constraints."""
    errors: list[str] = []

    campaign = state.get("campaign", {})
    ruleset = campaign.get("ruleset")

    try:
        rs_config = get_ruleset_config(ruleset)
    except KeyError:
        errors.append(f"Unknown ruleset: {ruleset!r}")
        return errors

    active_abilities = rs_config.get("abilities", ["str", "dex", "wil"])

    # Check all entities that have abilities
    for section, label in [
        ("characters", "Character"),
        ("monsters", "Monster"),
    ]:
        entities = state.get(section, {})
        for eid, entity in entities.items():
            if not isinstance(entity, dict):
                continue
            abilities = entity.get("abilities")
            if not isinstance(abilities, dict):
                continue
            for ab in active_abilities:
                if ab not in abilities:
                    errors.append(
                        f"{label} {eid!r}: missing required ability {ab!r}"
                    )

    return errors


# ---------------------------------------------------------------------------
# Pre-initialized field presence validation (H5)
# ---------------------------------------------------------------------------


def _validate_pre_init(state: dict[str, Any]) -> list[str]:
    """Validate that all pre-initialized optional fields exist on entities.

    Every schema-defined optional field must exist as a key (value may be null).
    This ensures get_field() always finds the field.
    """
    errors: list[str] = []

    # Character required and optional fields (from the schema)
    _char_optional = {
        "temp_hp", "initiative", "conditions", "death_saves", "resources",
        "reaction_used", "inventory", "equipment", "spells_known",
        "romance", "skills", "proficiency_bonus", "speed", "is_alive",
        "notes", "level", "xp",
    }
    _char_required = {
        "id", "name", "kind", "species", "archetype", "hp", "max_hp",
        "ac", "abilities",
    }
    _char_all = _char_required | _char_optional

    characters = state.get("characters", {})
    for eid, entity in characters.items():
        if not isinstance(entity, dict):
            continue
        for field in _char_required:
            if field not in entity:
                errors.append(f"Character {eid!r}: missing required field {field!r}")
        for field in _char_optional:
            if field not in entity:
                errors.append(f"Character {eid!r}: missing optional field {field!r}")

    # Monster optional fields
    _monster_optional = {
        "temp_hp", "initiative", "conditions", "speed", "is_alive",
        "skills", "cr",
    }
    _monster_required = {
        "id", "name", "size", "type", "hp", "max_hp", "ac", "abilities",
    }
    _monster_all = _monster_required | _monster_optional

    monsters = state.get("monsters", {})
    for eid, entity in monsters.items():
        if not isinstance(entity, dict):
            continue
        for field in _monster_required:
            if field not in entity:
                errors.append(f"Monster {eid!r}: missing required field {field!r}")
        for field in _monster_optional:
            if field not in entity:
                errors.append(f"Monster {eid!r}: missing optional field {field!r}")

    # NPC optional fields
    _npc_optional = {
        "location", "role", "faction", "attitude", "alive",
        "relationship", "knowledge", "stat_block", "notes",
    }
    _npc_required = {"id", "name"}
    _npc_all = _npc_required | _npc_optional

    npcs = state.get("npcs", {})
    for eid, entity in npcs.items():
        if not isinstance(entity, dict):
            continue
        for field in _npc_required:
            if field not in entity:
                errors.append(f"NPC {eid!r}: missing required field {field!r}")
        for field in _npc_optional:
            if field not in entity:
                errors.append(f"NPC {eid!r}: missing optional field {field!r}")

    return errors


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _collect_entity_ids(state: dict[str, Any]) -> set[str]:
    """Collect all entity IDs from characters, npcs, and monsters."""
    all_ids: set[str] = set()
    for section in ("characters", "npcs", "monsters"):
        entities = state.get(section, {})
        if isinstance(entities, dict):
            all_ids.update(entities.keys())
    return all_ids


def validate_entity_id(entity_id: str) -> str | None:
    """Validate entity ID format. Returns error string or None."""
    import re

    if not isinstance(entity_id, str):
        return f"Entity ID must be a string, got {type(entity_id).__name__}"
    if not entity_id:
        return "Entity ID must not be empty"
    if not re.match(r"^[a-z0-9_-]+$", entity_id):
        return (
            f"Entity ID {entity_id!r} must match [a-z0-9_-]+"
        )
    return None


def validate_condition(condition: str) -> str | None:
    """Validate a condition string. Returns error string or None."""
    if condition not in VALID_CONDITIONS:
        return f"Invalid condition: {condition!r}"
    return None
