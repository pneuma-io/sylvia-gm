"""T47: Companion registration and state injection.

Companions are NPC party members controlled by the LLM during combat.
This module provides registration and CS loading only — the LLM handles all
decision-making using the companion's personality, relationships, and the
combat situation provided by scene awareness and State Memo.

The LLM IS the DM. Companions are just more NPCs it controls.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CompanionEntry:
    """A registered companion with minimal metadata."""
    entity_id: str
    display_name: str = ""
    initiative_modifier: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


class CompanionRegistry:
    """Thin registry of NPC companions.

    Provides:
    - Registration (entity_id → state entity)
    - CS loading (inject NPC stats from CS files into state)
    - Initiative integration (get list of companion IDs for initiative rolling)
    - Query (is this entity a companion?)

    Does NOT provide:
    - Action selection (the LLM handles this)
    - Target assessment (the LLM handles this)
    - Role-based behavior (the LLM handles this via NPC personality)
    """

    def __init__(self, content_registry: Any = None, state_bridge: Any = None) -> None:
        self._registry = content_registry
        self._bridge = state_bridge
        self._companions: dict[str, CompanionEntry] = {}

    def register(self, entity_id: str, *, display_name: str = "",
                 initiative_modifier: int = 0) -> CompanionEntry:
        """Register an NPC as a companion."""
        entry = CompanionEntry(
            entity_id=entity_id,
            display_name=display_name or entity_id,
            initiative_modifier=initiative_modifier,
        )
        self._companions[entity_id] = entry
        logger.debug("Registered companion: %s", entity_id)
        return entry

    def inject_all(self) -> dict[str, str]:
        """Load all companions from CS and inject into game state.

        Returns dict mapping entity_id → state entity_id.
        """
        result: dict[str, str] = {}
        for entity_id in self._companions:
            injected = self._inject_one(entity_id)
            if injected:
                result[entity_id] = injected
        return result

    def _inject_one(self, entity_id: str) -> str | None:
        """Inject a single companion from CS into state."""
        if self._registry is None or self._bridge is None:
            return entity_id

        # Try NPC first, then monster
        for entity_type in ("npcs", "monsters"):
            data = (self._registry.get_npc(entity_id) if entity_type == "npcs"
                    else self._registry.get_monster(entity_id))
            if data:
                return self._registry.inject_into_state(
                    entity_id, self._bridge, entity_type=entity_type
                )
        return entity_id  # may already exist in state

    @property
    def companion_ids(self) -> list[str]:
        """Sorted list of registered companion entity IDs (for initiative)."""
        return sorted(self._companions.keys())

    def is_companion(self, entity_id: str) -> bool:
        """Check if an entity is a registered companion."""
        return entity_id in self._companions

    def get_initiative_expressions(self) -> dict[str, str]:
        """Get initiative expressions for all companions.

        Returns dict of entity_id → initiative expression (e.g. "d20 + 2").
        """
        return {
            eid: f"d20 + {entry.initiative_modifier}"
            for eid, entry in self._companions.items()
        }
