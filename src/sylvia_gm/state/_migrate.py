"""Schema version migration.

When loading a state file with an older schema version, migration
functions transform the data to the current schema format.

Migration paths define how to get from one version to the next.
If no path exists from the loaded version to the current version,
``StateMigrationError`` is raised.
"""

from __future__ import annotations

from typing import Any

from ._exceptions import StateMigrationError

# Current schema version from _schema.py
from ._schema import CURRENT_SCHEMA_VERSION

# Ordered migration paths: from_version → to_version
# Each entry maps a source version to the function that migrates it.
MIGRATION_PATHS: dict[str, str] = {
    "0.1.0": "0.2.0",
}

# Current version — what we migrate TO.
CURRENT: str = CURRENT_SCHEMA_VERSION


def migrate_state(
    state: dict[str, Any],
    from_version: str,
    to_version: str = CURRENT,
) -> dict[str, Any]:
    """Migrate *state* from *from_version* to *to_version*.

    Walks the MIGRATION_PATHS chain. Raises StateMigrationError if no
    path exists.

    Args:
        state: The loaded state dict.
        from_version: The schema version of the loaded state.
        to_version: Target version (default: current).

    Returns:
        Migrated state dict (modified in place).
    """
    current = from_version

    while current != to_version:
        next_version = MIGRATION_PATHS.get(current)
        if next_version is None:
            raise StateMigrationError(
                f"No migration path from schema version {current!r}. "
                f"Supported: {', '.join(sorted(MIGRATION_PATHS))}. "
                f"Current target: {to_version!r}"
            )

        # Find the migration function
        func_name = f"_migrate_{current.replace('.', '_')}_to_{next_version.replace('.', '_')}"
        func = globals().get(func_name)
        if func is None:
            raise StateMigrationError(
                f"Migration function {func_name!r} not found"
            )

        state = func(state)
        current = next_version

    # Update metadata
    state.setdefault("metadata", {})["schema_version"] = to_version

    return state


# ---------------------------------------------------------------------------
# Migration functions
# ---------------------------------------------------------------------------


def _migrate_0_1_0_to_0_2_0(state: dict[str, Any]) -> dict[str, Any]:
    """Migrate from 0.1.0 to 0.2.0.

    v0.1.0 → v0.2.0 changes:
    - Add ``combat_history`` top-level array if missing.
    - Add ``spells_known`` to all characters if missing.
    - Add ``notes`` to all characters if missing.
    - Add ``romance`` to all characters if missing.
    - Add ``skills`` and ``proficiency_bonus`` to characters if missing.
    - Ensure ``temp_hp`` exists on characters and monsters.
    - Ensure ``initiative`` field on characters and monsters.
    - Add ``cr`` to monsters if missing.
    """
    # Add combat_history
    if "combat_history" not in state:
        state["combat_history"] = []

    # Fix characters
    characters = state.get("characters", {})
    for cid, char in characters.items():
        if not isinstance(char, dict):
            continue
        char.setdefault("spells_known", [])
        char.setdefault("notes", [])
        char.setdefault("romance", None)
        char.setdefault("skills", None)
        char.setdefault("proficiency_bonus", None)
        char.setdefault("temp_hp", 0)
        char.setdefault("initiative", None)
        # Ensure equipment slots
        equipment = char.setdefault("equipment", {})
        for slot in (
            "main_hand", "off_hand", "armor", "head", "neck",
            "ring_1", "ring_2", "cloak", "boots",
        ):
            equipment.setdefault(slot, None)

    # Fix monsters
    monsters = state.get("monsters", {})
    for mid, mon in monsters.items():
        if not isinstance(mon, dict):
            continue
        mon.setdefault("cr", None)
        mon.setdefault("temp_hp", 0)
        mon.setdefault("initiative", None)
        mon.setdefault("skills", None)

    # Fix NPCs
    npcs = state.get("npcs", {})
    for nid, npc in npcs.items():
        if not isinstance(npc, dict):
            continue
        npc.setdefault("notes", [])
        npc.setdefault("stat_block", None)

    # Ensure metadata
    state.setdefault("metadata", {})
    state["metadata"]["schema_version"] = "0.2.0"

    return state
