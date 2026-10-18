"""YAML frontmatter parser for Campaign Setting markdown files.

Extracts structured data from YAML frontmatter (delimited by ---) and
returns the body text. Specialized parsers extract type-specific fields
for monsters, NPCs, spells, and items.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Regex to match YAML frontmatter between opening/closing ---
_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL
)


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from a markdown string.

    Args:
        text: Raw markdown text with optional YAML frontmatter.

    Returns:
        (frontmatter_dict, body_text). If no frontmatter found,
        frontmatter_dict is empty and body_text is the full text.
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text

    yaml_text = match.group(1)
    body = text[match.end():].strip()

    import yaml

    try:
        fm = yaml.safe_load(yaml_text) or {}
    except yaml.YAMLError as e:
        logger.warning("Failed to parse YAML frontmatter: %s", e)
        return {}, text

    if not isinstance(fm, dict):
        logger.warning("Frontmatter is not a dict: %s", type(fm).__name__)
        return {}, text

    return fm, body


def parse_file(filepath: Path | str) -> tuple[dict[str, Any], str]:
    """Parse a markdown file with YAML frontmatter.

    Args:
        filepath: Path to a .md file.

    Returns:
        (frontmatter_dict, body_text).

    Raises:
        FileNotFoundError: If file does not exist.
    """
    path = Path(filepath)
    text = path.read_text(encoding="utf-8")
    return parse_frontmatter(text)


def parse_monster_file(filepath: Path | str) -> dict[str, Any]:
    """Parse a monster .md file and return structured stat block.

    Extracts: name, size, type, alignment, cr, xp, ac, hp, hit_dice,
    speed, stats (str/dex/con/int/wis/cha), saves, skills, damage_resistances,
    damage_immunities, condition_immunities, senses, languages, habitat.

    Returns a dict with 'fm' (frontmatter), 'body' (narrative text),
    and normalized fields.
    """
    fm, body = parse_file(filepath)

    # Normalize stats: always return a dict with all six abilities
    stats = fm.get("stats", {})
    normalized_stats = {
        "str": stats.get("str", 10),
        "dex": stats.get("dex", 10),
        "con": stats.get("con", 10),
        "int": stats.get("int", 10),
        "wis": stats.get("wis", 10),
        "cha": stats.get("cha", 10),
    }

    return {
        "name": fm.get("name", ""),
        "slug": _slug_from_filepath(filepath),
        "size": fm.get("size", "Medium"),
        "type": fm.get("type", ""),
        "alignment": fm.get("alignment", ""),
        "cr": fm.get("cr", 0),
        "xp": fm.get("xp", 0),
        "ac": fm.get("ac", 10),
        "ac_type": fm.get("ac_type", ""),
        "hp": fm.get("hp", 0),
        "hit_dice": fm.get("hit_dice", ""),
        "speed": fm.get("speed", ""),
        "stats": normalized_stats,
        "saves": fm.get("saves", {}),
        "skills": fm.get("skills", {}),
        "damage_resistances": fm.get("damage_resistances", []),
        "damage_immunities": fm.get("damage_immunities", []),
        "condition_immunities": fm.get("condition_immunities", []),
        "senses": fm.get("senses", {}),
        "languages": fm.get("languages", ""),
        "habitat": fm.get("habitat", []),
        "fm": fm,          # raw frontmatter
        "body": body,      # narrative prose
    }


def parse_npc_file(filepath: Path | str) -> dict[str, Any]:
    """Parse an NPC .md file and return structured data.

    Extracts: name, role, disposition, species, location.
    """
    fm, body = parse_file(filepath)

    return {
        "name": fm.get("name", ""),
        "slug": _slug_from_filepath(filepath),
        "role": fm.get("role", ""),
        "disposition": fm.get("disposition", ""),
        "species": fm.get("species", ""),
        "location": fm.get("location", ""),
        "fm": fm,
        "body": body,
    }


def parse_spell_file(filepath: Path | str) -> dict[str, Any]:
    """Parse a spell .md file and return structured data.

    Extracts: name, level, school, casting_time, range, components,
    duration, concentration, ritual, classes, material.
    """
    fm, body = parse_file(filepath)

    return {
        "name": fm.get("name", ""),
        "slug": _slug_from_filepath(filepath),
        "level": fm.get("level", 0),
        "school": fm.get("school", ""),
        "casting_time": fm.get("casting_time", ""),
        "range": fm.get("range", ""),
        "components": fm.get("components", []),
        "duration": fm.get("duration", ""),
        "concentration": fm.get("concentration", False),
        "ritual": fm.get("ritual", False),
        "classes": fm.get("classes", []),
        "material": fm.get("material", ""),
        "fm": fm,
        "body": body,
    }


def parse_item_file(filepath: Path | str) -> dict[str, Any]:
    """Parse an item .md file and return structured data.

    Extracts: name, type, rarity, attunement, weight, value, tags.
    """
    fm, body = parse_file(filepath)

    return {
        "name": fm.get("name", ""),
        "slug": _slug_from_filepath(filepath),
        "type": fm.get("type", ""),
        "rarity": fm.get("rarity", ""),
        "attunement": fm.get("attunement", False),
        "weight": fm.get("weight", 0),
        "value": fm.get("value", 0),
        "tags": fm.get("tags", []),
        "fm": fm,
        "body": body,
    }


def _slug_from_filepath(filepath: str | Path) -> str:
    """Extract the entity slug from the filename (without extension)."""
    return Path(filepath).stem
