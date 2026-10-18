"""Scoped state snapshots.

Snapshots capture a filtered subset of the current state for use by
tools that need a consistent view of a specific domain (combat, social,
inventory). ``snapshot(\"full\")`` returns the complete state.

The ``snapshot()`` method on ``StateAPI`` returns a deep copy, so the
caller cannot mutate the store through the snapshot.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal

SnapshotScope = Literal["combat", "social", "inventory", "full"]


def take_snapshot(state: dict[str, Any], scope: SnapshotScope) -> dict[str, Any]:
    """Return a filtered deep copy of *state* for the given *scope*.

    Args:
        state: The full state dict.
        scope: One of ``"combat"``, ``"social"``, ``"inventory"``, ``"full"``.

    Returns:
        A deep copy of the filtered state subset.
    """
    if scope == "full":
        return deepcopy(state)

    if scope == "combat":
        return deepcopy(_combat_snapshot(state))

    if scope == "social":
        return deepcopy(_social_snapshot(state))

    if scope == "inventory":
        return deepcopy(_inventory_snapshot(state))

    raise ValueError(f"Unknown snapshot scope: {scope!r}")


def _combat_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    """Extract combat-relevant data."""
    combat = state.get("combat")
    combat_history = state.get("combat_history", [])

    # Collect combat participant data
    participants: dict[str, dict[str, Any]] = {}
    if combat and isinstance(combat, dict):
        for pid in combat.get("participants", []):
            entity = _find_entity(state, pid)
            if entity:
                participants[pid] = _extract_combat_fields(entity)

    return {
        "combat": combat,
        "combat_history": combat_history,
        "participants": participants,
        "metadata": state.get("metadata", {}),
    }


def _social_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    """Extract social/reputation data."""
    party = state.get("party", {})
    return {
        "party": {
            "gold": party.get("gold", 0),
            "members": party.get("members", []),
            "reputation": party.get("reputation", {}),
        },
        "npcs": state.get("npcs", {}),
        "world": {
            "current_scene": state.get("world", {}).get("current_scene"),
            "current_location": state.get("world", {}).get("current_location"),
            "flags": state.get("world", {}).get("flags", {}),
            "time_of_day": state.get("world", {}).get("time_of_day"),
            "day": state.get("world", {}).get("day"),
        },
        "metadata": state.get("metadata", {}),
    }


def _inventory_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    """Extract inventory data for party and characters."""
    characters = state.get("characters", {})
    party = state.get("party", {})

    char_inventories: dict[str, Any] = {}
    for cid, char in characters.items():
        if not isinstance(char, dict):
            continue
        char_inventories[cid] = {
            "name": char.get("name", cid),
            "inventory": char.get("inventory", []),
            "equipment": char.get("equipment", {}),
            "gold": 0,  # Individual gold not tracked; use party gold
        }

    return {
        "party_shared_inventory": party.get("shared_inventory", []),
        "characters": char_inventories,
        "metadata": state.get("metadata", {}),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_entity(state: dict[str, Any], entity_id: str) -> dict[str, Any] | None:
    """Find an entity by ID across all entity types."""
    for section in ("characters", "npcs", "monsters"):
        entities = state.get(section, {})
        if entity_id in entities:
            return entities[entity_id]
    return None


def _extract_combat_fields(entity: dict[str, Any]) -> dict[str, Any]:
    """Extract combat-relevant fields from an entity dict."""
    return {
        "name": entity.get("name", "Unknown"),
        "hp": entity.get("hp"),
        "max_hp": entity.get("max_hp"),
        "temp_hp": entity.get("temp_hp", 0),
        "ac": entity.get("ac"),
        "initiative": entity.get("initiative"),
        "conditions": entity.get("conditions", []),
        "is_alive": entity.get("is_alive", True),
        "abilities": entity.get("abilities", {}),
        "speed": entity.get("speed", 30),
    }
