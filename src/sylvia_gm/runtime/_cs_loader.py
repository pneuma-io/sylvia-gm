"""CSLoader — discovers and indexes Campaign Setting content files.

Reads setting.json from a Campaign Setting directory, enumerates all
content directories (monsters, NPCs, spells, items, classes, races,
backgrounds), and builds a path index mapping entity slugs to file paths.

Resolution chain:
  1. Check CS directory for content (e.g. severed-lands/content/monsters/)
  2. If slug not found, check parent ruleset (via setting.json 'extends')
  3. Return Path or None
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default CS base path
_DEFAULT_CS_BASE = Path.home() / ".sylvia-gm" / "campaign-settings"

# Directory mapping: entity type → path relative to CS root
_CONTENT_DIRS: dict[str, str] = {
    "monsters": "content/monsters",
    "npcs": "npcs",
    "spells": "content/spells",
    "items": "content/items",
    "classes": "content/classes",
    "races": "content/races",
    "backgrounds": "content/backgrounds",
}


class CSLoader:
    """Discovers and indexes Campaign Setting content files.

    Usage::

        loader = CSLoader("sylvia.srd-lite.bg3", "severed-lands")
        monsters = loader.list_entities("monsters")
        path = loader.resolve_path("monsters", "hollowling")
        # → Path(".../severed-lands/content/monsters/hollowling.md")
    """

    def __init__(
        self,
        ruleset: str = "sylvia.srd-lite.bg3",
        slug: str | None = None,
        base_path: Path | str | None = None,
    ) -> None:
        """Initialize the CSLoader.

        Args:
            ruleset: Ruleset package id (e.g. "example.ruleset").
            slug: Campaign Setting slug (e.g. "severed-lands"). If None,
                  only the ruleset directory is indexed (parent lookup).
            base_path: Override base path. Defaults to
                       ~/.sylvia-gm/campaign-settings/.
        """
        self._base = Path(base_path) if base_path else _DEFAULT_CS_BASE
        self._ruleset = ruleset
        self._slug = slug

        # Path to the CS directory
        self._cs_dir = self._base / ruleset / slug if slug else self._base / ruleset
        self._parent_dir = self._base / ruleset  # parent ruleset dir

        # setting.json data
        self._setting: dict[str, Any] = {}
        self._extends: str | None = None

        # Path index: entity_type → {slug → Path}
        self._index: dict[str, dict[str, Path]] = {}
        # Parent index (for extending rulesets)
        self._parent_index: dict[str, dict[str, Path]] = {}
        self._loaded = False

    # ── Properties ──────────────────────────────────────────────────────

    @property
    def setting(self) -> dict[str, Any]:
        """The parsed setting.json data."""
        self._ensure_loaded()
        return self._setting

    @property
    def extends(self) -> str | None:
        """The parent ruleset this CS extends."""
        self._ensure_loaded()
        return self._extends

    @property
    def cs_dir(self) -> Path:
        """The Campaign Setting directory."""
        return self._cs_dir

    # ── Initialization ──────────────────────────────────────────────────

    def load(self) -> None:
        """Load setting.json and index all content directories."""
        if self._loaded:
            return

        self._load_setting_json()
        self._index_content()
        self._loaded = True
        logger.debug(
            "CSLoader loaded: ruleset=%s slug=%s entities=%d",
            self._ruleset, self._slug,
            sum(len(v) for v in self._index.values()),
        )

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def _load_setting_json(self) -> None:
        """Read and parse setting.json from the CS directory."""
        setting_path = self._cs_dir / "setting.json"
        if not setting_path.exists():
            logger.warning("setting.json not found at %s", setting_path)
            self._setting = {}
            return

        try:
            text = setting_path.read_text(encoding="utf-8")
            self._setting = json.loads(text)
            meta = self._setting.get("meta", {})
            self._extends = meta.get("extends")
            if self._extends:
                logger.debug(
                    "CS %s extends %s", self._slug, self._extends
                )
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load setting.json: %s", e)
            self._setting = {}

    def _index_content(self) -> None:
        """Walk all content directories and build slug→path maps."""
        self._index.clear()
        self._parent_index.clear()

        for entity_type, rel_dir in _CONTENT_DIRS.items():
            self._index[entity_type] = {}

            # Index CS own directory
            cs_content_dir = self._cs_dir / rel_dir
            if cs_content_dir.is_dir():
                for md_file in cs_content_dir.glob("*.md"):
                    slug = md_file.stem
                    self._index[entity_type][slug] = md_file
                logger.debug(
                    "Indexed %d %s from %s",
                    len(self._index[entity_type]), entity_type, self._cs_dir,
                )

            # Index parent ruleset directory (for extending)
            if self._extends and self._extends != self._ruleset:
                parent_cs_dir = self._base / self._extends / rel_dir
                if parent_cs_dir.is_dir():
                    if entity_type not in self._parent_index:
                        self._parent_index[entity_type] = {}
                    for md_file in parent_cs_dir.glob("*.md"):
                        slug = md_file.stem
                        self._parent_index[entity_type][slug] = md_file
                # Also check the parent ruleset without a campaign setting.
                parent_ruleset_dir = self._base / self._extends
                # Parent ruleset content is at the ruleset level
                parent_content_dir = parent_ruleset_dir / rel_dir
                if parent_content_dir.is_dir():
                    if entity_type not in self._parent_index:
                        self._parent_index[entity_type] = {}
                    for md_file in parent_content_dir.glob("*.md"):
                        slug = md_file.stem
                        # Only add if not already found
                        if slug not in self._parent_index[entity_type]:
                            self._parent_index[entity_type][slug] = md_file

    # ── Resolution ──────────────────────────────────────────────────────

    def resolve_path(self, entity_type: str, slug: str) -> Path | None:
        """Resolve an entity slug to its file path.

        Checks the CS directory first, then the parent ruleset.

        Args:
            entity_type: One of "monsters", "npcs", "spells", "items",
                         "classes", "races", "backgrounds".
            slug: Entity slug (filename without .md extension).

        Returns:
            Path to the .md file, or None if not found.
        """
        self._ensure_loaded()

        entity_map = self._index.get(entity_type, {})
        if slug in entity_map:
            return entity_map[slug]

        # Check parent
        parent_map = self._parent_index.get(entity_type, {})
        if slug in parent_map:
            return parent_map[slug]

        return None

    def list_entities(self, entity_type: str) -> list[str]:
        """List all entity slugs of a given type.

        Args:
            entity_type: One of "monsters", "npcs", "spells", "items",
                         "classes", "races", "backgrounds".

        Returns:
            Sorted list of entity slugs.
        """
        self._ensure_loaded()

        slugs: set[str] = set()

        entity_map = self._index.get(entity_type, {})
        slugs.update(entity_map.keys())

        parent_map = self._parent_index.get(entity_type, {})
        slugs.update(parent_map.keys())

        return sorted(slugs)

    def list_entity_types(self) -> list[str]:
        """List all available entity types with content."""
        self._ensure_loaded()
        types: list[str] = []
        for entity_type in _CONTENT_DIRS:
            entity_map = self._index.get(entity_type, {})
            parent_map = self._parent_index.get(entity_type, {})
            if entity_map or parent_map:
                types.append(entity_type)
        return sorted(types)

    def entity_count(self, entity_type: str) -> int:
        """Return the number of entities of a given type."""
        self._ensure_loaded()
        cs_count = len(self._index.get(entity_type, {}))
        parent_count = len(self._parent_index.get(entity_type, {}))
        return cs_count + parent_count
