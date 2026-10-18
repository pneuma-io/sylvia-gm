"""ContentRegistry — lazy-loading cache for Campaign Setting entities.

Maps entity slugs to their loaded data (monster stats, NPC backstories,
spell effects, item properties). Loads on demand, caches for session duration.

Provides state injection: when a monster is loaded for combat, creates a
state entity with its stats via StateBridge.

Usage::

    loader = CSLoader("sylvia.srd-lite.bg3", "severed-lands")
    registry = ContentRegistry(loader)
    hollowling = registry.get_monster("hollowling")
    # → {"name": "Hollowling", "cr": "1/4", "ac": 15, "hp": 7, ...}

    registry.inject_into_state("hollowling", state_bridge)
    # → creates state entity with stats loaded from CS

    # Auto-resolve by slug across all entity types:
    entity = registry.resolve("rift-blade")
    # → checks: monsters, spells, items, npcs (in order)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from sylvia_gm.runtime._cs_loader import CSLoader
from sylvia_gm.runtime._frontmatter import (
    parse_monster_file,
    parse_npc_file,
    parse_spell_file,
    parse_item_file,
)

# Entity type → (parser function, description)
_ENTITY_PARSERS: dict[str, Any] = {
    "monsters": parse_monster_file,
    "npcs": parse_npc_file,
    "spells": parse_spell_file,
    "items": parse_item_file,
}


class ContentRegistry:
    """Lazy-loading cache for Campaign Setting content.

    All content is loaded on first access and cached for the session
    lifetime. The registry can inject loaded monsters into the game
    state as combat entities.
    """

    def __init__(self, loader: CSLoader) -> None:
        """Initialize with a CSLoader instance.

        Args:
            loader: A CSLoader that has already indexed content.
        """
        self._loader = loader
        loader.load()  # ensure indexed

        # Cache: entity_type → {slug → parsed_data}
        self._cache: dict[str, dict[str, dict[str, Any]]] = {}

        # Reverse lookup: slug → entity_type (for resolve())
        self._slug_types: dict[str, str] = {}

    # ── Properties ──────────────────────────────────────────────────────

    @property
    def loader(self) -> CSLoader:
        """The underlying CSLoader."""
        return self._loader

    @property
    def setting(self) -> dict[str, Any]:
        """The CS setting.json data."""
        return self._loader.setting

    # ── Entity Loading (lazy, cached) ───────────────────────────────────

    def get_monster(self, slug: str) -> dict[str, Any] | None:
        """Load a monster by slug. Returns None if not found."""
        return self._load_entity("monsters", slug)

    def get_npc(self, slug: str) -> dict[str, Any] | None:
        """Load an NPC by slug. Returns None if not found."""
        return self._load_entity("npcs", slug)

    def get_spell(self, slug: str) -> dict[str, Any] | None:
        """Load a spell by slug. Returns None if not found."""
        return self._load_entity("spells", slug)

    def get_item(self, slug: str) -> dict[str, Any] | None:
        """Load an item by slug. Returns None if not found."""
        return self._load_entity("items", slug)

    def _load_entity(self, entity_type: str, slug: str) -> dict[str, Any] | None:
        """Load an entity with lazy caching.

        Args:
            entity_type: "monsters", "npcs", "spells", or "items".
            slug: Entity slug.

        Returns:
            Parsed entity data dict, or None if not found.
        """
        # Check cache first
        cache = self._cache.setdefault(entity_type, {})
        if slug in cache:
            return cache[slug]

        # Resolve path
        path = self._loader.resolve_path(entity_type, slug)
        if path is None:
            logger.debug("Entity not found: %s/%s", entity_type, slug)
            return None

        # Parse
        try:
            parser = _ENTITY_PARSERS.get(entity_type)
            if parser is None:
                logger.warning("No parser for entity type: %s", entity_type)
                return None

            data = parser(path)
            # Store slug in data too (redundant safety)
            data["slug"] = slug
            data["entity_type"] = entity_type
            cache[slug] = data

            # Register slug → type mapping for resolve()
            if slug not in self._slug_types:
                self._slug_types[slug] = entity_type

            logger.debug(
                "Loaded %s/%s: %s", entity_type, slug, data.get("name", slug)
            )
            return data

        except FileNotFoundError:
            logger.debug("File not found for %s/%s", entity_type, slug)
            return None
        except Exception as e:
            logger.warning("Failed to parse %s/%s: %s", entity_type, slug, e)
            return None

    # ── Auto-Resolution ─────────────────────────────────────────────────

    def resolve(self, slug: str) -> dict[str, Any] | None:
        """Resolve a slug across all entity types.

        Checks slugs in the cache first, then tries to load from each
        entity type in order: monsters, spells, items, npcs.

        Args:
            slug: Entity slug (e.g. "hollowling", "rift-blade").

        Returns:
            Parsed entity data dict, or None if not found.
        """
        # Check known type from prior loads
        if slug in self._slug_types:
            entity_type = self._slug_types[slug]
            return self._load_entity(entity_type, slug)

        # Try each type in order
        for entity_type in ("monsters", "spells", "items", "npcs"):
            data = self._load_entity(entity_type, slug)
            if data is not None:
                return data

        return None

    # ── State Injection ─────────────────────────────────────────────────

    def inject_into_state(
        self,
        slug: str,
        state_bridge: Any,
        entity_type: str | None = None,
    ) -> str | None:
        """Load an entity and inject it as a state entity via StateBridge.

        Creates a game entity from the CS data, allowing the engine to
        track HP, conditions, and combat state for this creature.

        Args:
            slug: Entity slug.
            state_bridge: StateBridge instance.
            entity_type: Override entity type detection. If None, tries
                         monsters first, then auto-resolves.

        Returns:
            The entity_id of the created state entity, or None if the
            entity could not be loaded.
        """
        if state_bridge is None:
            logger.warning("Cannot inject %s: no StateBridge", slug)
            return None

        if entity_type:
            data = self._load_entity(entity_type, slug)
        else:
            data = self.resolve(slug)

        if data is None:
            logger.debug("Cannot inject unknown entity: %s", slug)
            return None

        # Use slug as the entity_id
        entity_id = slug

        # Build entity data based on type
        entity_type = data.get("entity_type", entity_type or "monsters")

        if entity_type == "monsters":
            return self._inject_monster(entity_id, data, state_bridge)
        elif entity_type == "npcs":
            return self._inject_npc(entity_id, data, state_bridge)
        else:
            logger.debug(
                "State injection not supported for entity type: %s",
                entity_type,
            )
            return None

    def _inject_monster(
        self,
        entity_id: str,
        data: dict[str, Any],
        bridge: Any,
    ) -> str:
        """Create a state entity for a monster with its CS stats.

        Creates the entity if it doesn't exist; updates stats if it does.
        Uses the same pattern as StateBridge._ensure_campaign_entity().
        """
        try:
            # Check if entity exists
            if bridge.entity_exists(entity_id):
                logger.debug(
                    "Entity %s already exists in state, updating", entity_id
                )
                # Update stats
                bridge.write_field(entity_id, "hp", data.get("hp", 1))
                bridge.write_field(entity_id, "ac", data.get("ac", 10))
                bridge.write_field(entity_id, "max_hp", data.get("hp", 1))
                return entity_id

            # Create entity via the store's raw data interface
            store = bridge._store
            raw = store.get_raw()
            characters = raw.setdefault("characters", {})

            hp = data.get("hp", 1)
            ac = data.get("ac", 10)
            stats = data.get("stats", {})

            characters[entity_id] = {
                "id": entity_id,
                "name": data.get("name", entity_id),
                "kind": "monster",
                "species": data.get("type", "unknown"),
                "archetype": data.get("size", "Medium"),
                "max_hp": hp,
                "hp": hp,
                "ac": ac,
                "abilities": stats,
                "flags": {},
            }
            store.mark_dirty()
            logger.info(
                "Injected monster %s into state: hp=%d ac=%d cr=%s",
                entity_id, hp, ac, data.get("cr", "?"),
            )

            return entity_id

        except Exception as e:
            logger.warning(
                "Failed to inject monster %s into state: %s", entity_id, e
            )
            # Fallback: try write_field for key stats
            try:
                bridge.write_field(entity_id, "hp", data.get("hp", 1))
                bridge.write_field(entity_id, "ac", data.get("ac", 10))
                bridge.write_field(entity_id, "max_hp", data.get("hp", 1))
                bridge.write_field(entity_id, "name", data.get("name", entity_id))
                bridge.write_field(entity_id, "kind", "monster")
                return entity_id
            except Exception:
                return entity_id

    def _inject_npc(
        self,
        entity_id: str,
        data: dict[str, Any],
        bridge: Any,
    ) -> str | None:
        """Create a state entity for an NPC."""
        try:
            if bridge.entity_exists(entity_id):
                logger.debug("NPC %s already exists in state", entity_id)
                return entity_id

            store = bridge._store
            raw = store.get_raw()
            characters = raw.setdefault("characters", {})

            characters[entity_id] = {
                "id": entity_id,
                "name": data.get("name", entity_id),
                "kind": "npc",
                "species": data.get("species", "human"),
                "archetype": data.get("role", ""),
                "max_hp": 10,
                "hp": 10,
                "ac": 10,
                "abilities": {},
                "flags": {},
            }
            store.mark_dirty()
            return entity_id

        except Exception as e:
            logger.warning(
                "Failed to inject NPC %s into state: %s", entity_id, e
            )
            try:
                bridge.write_field(entity_id, "name", data.get("name", entity_id))
                bridge.write_field(entity_id, "kind", "npc")
                return entity_id
            except Exception:
                return entity_id

    # ── Listing ─────────────────────────────────────────────────────────

    def list_monsters(self) -> list[str]:
        """Return all available monster slugs."""
        return self._loader.list_entities("monsters")

    def list_npcs(self) -> list[str]:
        """Return all available NPC slugs."""
        return self._loader.list_entities("npcs")

    def list_spells(self) -> list[str]:
        """Return all available spell slugs."""
        return self._loader.list_entities("spells")

    def list_items(self) -> list[str]:
        """Return all available item slugs."""
        return self._loader.list_entities("items")

    # ── Cache Management ────────────────────────────────────────────────

    def clear_cache(self, entity_type: str | None = None) -> None:
        """Clear the cache.

        Args:
            entity_type: If provided, clear only this type's cache.
                         If None, clear all caches.
        """
        if entity_type:
            self._cache.pop(entity_type, None)
        else:
            self._cache.clear()
            self._slug_types.clear()

    def cache_stats(self) -> dict[str, int]:
        """Return cache statistics."""
        return {
            entity_type: len(entities)
            for entity_type, entities in self._cache.items()
        }
