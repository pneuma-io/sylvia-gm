"""Character data model and generation for the Builder agent.

Provides:
- Ability schema extraction from ruleset YAML files.
- Lenient parsing of freeform character Markdown (``characters/<name>.md``).
- State-compatible character dict generation with randomized stats.
"""

from __future__ import annotations

import logging
import random
import re
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────────────────

# Minimum HP per ruleset (Cairn 2e: 1d6 → min 1, max 6)
HP_BY_RULESET: dict[str, tuple[int, int]] = {
    "dnd-5e": (1, 10),  # varies by class; conservative default
}

# Stat ranges per ruleset
STAT_RANGE: dict[str, tuple[int, int]] = {
    "3d6": (3, 18),   # Cairn 2e
    "4d6k3": (3, 18), # D&D 5e
}

# Expected section headers in general character Markdown
KNOWN_SECTIONS = {
    "name", "species", "archetype", "kind",
    "traits", "flaws", "bonds",
    "combat style", "combat_style", "combat-style",
    "backstory", "notes", "dialogue", "dialogue examples",
}


# ── Ability Schema Extraction ────────────────────────────────────────────


def extract_ability_schema(ruleset_path: str | Path) -> list[str]:
    """Extract the list of active ability names from a ruleset YAML file.

    Reads the ``abilities`` section, returning only keys where ``active: true``.
    Order is preserved from the YAML.

    Args:
        ruleset_path: Path to a ruleset ``.yaml`` file.

    Returns:
        List of ability key names, e.g. ``["str","dex","wil"]`` for Cairn 2e
        or ``["str","dex","con","int","wis","cha"]`` for D&D 5e.

    Raises:
        FileNotFoundError: If *ruleset_path* does not exist.
        ValueError: If the YAML has no ``abilities`` section.
    """
    path = Path(ruleset_path)
    if not path.exists():
        raise FileNotFoundError(f"Ruleset file not found: {ruleset_path}")

    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    abilities = data.get("abilities")
    if not abilities or not isinstance(abilities, dict):
        raise ValueError(
            f"No 'abilities' section found in ruleset: {ruleset_path}"
        )

    active: list[str] = []
    for key, cfg in abilities.items():
        if isinstance(cfg, dict) and cfg.get("active", False):
            active.append(str(key))

    logger.debug("extract_ability_schema path=%s → %s", path.name, active)
    return active


# ── General Markdown Parser ──────────────────────────────────────────────


def parse_general_md(path: str | Path) -> dict[str, Any]:
    """Parse a freeform character Markdown file into a structured dict.

    The function is **lenient**: missing sections produce empty defaults
    rather than raising exceptions. Structure is inferred from ``## Header``
    sections and ``- Key: Value`` bullet patterns.

    Expected sections (all optional):
        ## Name
        ## Species / Archetype
        ## Kind  (``pc`` or ``npc``)
        ## Traits
        ## Flaws
        ## Bonds
        ## Combat Style
        ## Backstory
        ## Dialogue

    Args:
        path: Path to ``characters/<name>.md``.

    Returns:
        A dict with keys: ``name``, ``species``, ``archetype``, ``kind``,
        ``traits``, ``flaws``, ``bonds``, ``combat_style``, ``backstory``,
        ``dialogue_examples``. Missing sections default to empty strings or lists.
    """
    path = Path(path)
    raw_text = path.read_text(encoding="utf-8") if path.exists() else ""

    result: dict[str, Any] = {
        "name": "",
        "species": "Unknown",
        "archetype": "Unknown",
        "kind": "pc",
        "traits": [],
        "flaws": [],
        "bonds": [],
        "combat_style": {},
        "backstory": "",
        "dialogue_examples": [],
    }

    if not raw_text.strip():
        logger.warning("parse_general_md: empty or missing file: %s", path)
        return result

    # Split on ## headers
    sections = _split_sections(raw_text)

    # Top-level title (first line starting with #, but not ##)
    for line in raw_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("## "):
            result["name"] = stripped[2:].strip()
            break

    # --- Name / Species / Archetype / Kind ---
    header_block = sections.get("") or sections.get("name") or ""
    _parse_header_fields(header_block, result)

    # --- Traits ---
    if "traits" in sections:
        result["traits"] = _parse_bullet_list(sections["traits"])

    # --- Flaws ---
    if "flaws" in sections:
        result["flaws"] = _parse_bullet_list(sections["flaws"])

    # --- Bonds ---
    if "bonds" in sections:
        result["bonds"] = _parse_bullet_list(sections["bonds"])

    # --- Combat Style ---
    for key in ("combat style", "combat_style", "combat-style"):
        if key in sections:
            result["combat_style"] = _parse_combat_style(sections[key])
            break

    # --- Backstory ---
    if "backstory" in sections:
        result["backstory"] = sections["backstory"].strip()

    # --- Dialogue ---
    for key in ("dialogue", "dialogue examples", "dialogue_examples"):
        if key in sections:
            result["dialogue_examples"] = _parse_dialogue(sections[key])
            break

    return result


def _split_sections(text: str) -> dict[str, str]:
    """Split Markdown text into a dict of ``{section_name: body}`` by ``##`` headers."""
    sections: dict[str, str] = {}
    pattern = re.compile(r"^##\s+(.+)$", re.MULTILINE)
    matches = list(pattern.finditer(text))

    if not matches:
        # No headers found — entire text is preamble
        sections[""] = text.strip()
        return sections

    # Preamble before first ##
    first = matches[0]
    preamble = text[:first.start()].strip()
    if preamble:
        sections[""] = preamble

    for i, match in enumerate(matches):
        key = match.group(1).strip().lower()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections[key] = text[start:end].strip()

    return sections


def _parse_header_fields(block: str, result: dict[str, Any]) -> None:
    """Extract species, archetype, and kind from a header/preamble block."""
    if not block:
        return

    lines = block.splitlines()
    for line in lines:
        stripped = line.strip().lstrip("- ").strip()

        # Key: Value pattern
        m = re.match(r"(?i)^(species|archetype|kind)\s*[:=]\s*(.+)$", stripped)
        if m:
            key = m.group(1).lower()
            val = m.group(2).strip()
            if key == "kind":
                val = val.lower()
                if val not in ("pc", "npc"):
                    val = "pc"
            result[key] = val
            continue

        # "X the Y" → name + archetype (only if not already set)
        if not result.get("species") or result["species"] == "Unknown":
            m2 = re.match(
                r"^(.+?)\s+the\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s*$", stripped
            )
            if m2:
                result["name"] = m2.group(1).strip()
                result["archetype"] = m2.group(2).strip()
                continue


def _parse_bullet_list(block: str) -> list[str]:
    """Extract a list of items from bullet or comma-separated text."""
    items: list[str] = []
    for line in block.splitlines():
        stripped = line.strip()
        # Bullet lines: "- item" or "* item" or "1. item"
        m = re.match(r"^(?:[-*]\s+|\d+\.\s+)(.+)$", stripped)
        if m:
            items.append(m.group(1).strip())
        elif stripped:
            # Could be comma-separated prose
            for part in re.split(r",(?:\s+|$)", stripped):
                part = part.strip().rstrip(".")
                if part:
                    items.append(part)
    return items


def _parse_combat_style(block: str) -> dict[str, str]:
    """Parse combat style into preferred / melee / ranged / weakness dict."""
    result: dict[str, str] = {}
    for line in block.splitlines():
        stripped = line.strip().lstrip("- ").strip()
        m = re.match(r"(?i)^(preferred|melee|ranged|weakness|style)\s*[:=]\s*(.+)$", stripped)
        if m:
            result[m.group(1).lower()] = m.group(2).strip()
        else:
            # Freeform line — store as preferred
            if stripped and "preferred" not in result:
                result["preferred"] = stripped
    # Ensure defaults
    result.setdefault("preferred", "balanced")
    result.setdefault("melee", "")
    result.setdefault("ranged", "")
    result.setdefault("weakness", "")
    return result


def _parse_dialogue(block: str) -> list[dict[str, Any]]:
    """Parse dialogue examples into a list of {text, tags} dicts."""
    examples: list[dict[str, Any]] = []
    current_text: list[str] = []
    current_tags: list[str] = []

    for line in block.splitlines():
        stripped = line.strip()
        if not stripped:
            # Blank line flushes current example
            if current_text:
                examples.append({
                    "text": " ".join(current_text),
                    "tags": current_tags or ["casual"],
                })
                current_text = []
                current_tags = []
            continue

        # Line with a tag prefix: "[combat] text" or "[social] text"
        tag_m = re.match(r"^\[(\w+(?:/\w+)*)\]\s*(.*)$", stripped)
        if tag_m:
            if current_text:
                examples.append({
                    "text": " ".join(current_text),
                    "tags": current_tags or ["casual"],
                })
                current_text = []
                current_tags = []
            current_tags = tag_m.group(1).split("/")
            if tag_m.group(2):
                current_text.append(tag_m.group(2))
        else:
            # Plain dialogue line
            current_text.append(stripped)

    # Flush final
    if current_text:
        examples.append({
            "text": " ".join(current_text),
            "tags": current_tags or ["casual"],
        })

    return examples


# ── Character State Generation ───────────────────────────────────────────


def generate_character_state(
    general_data: dict[str, Any],
    ability_schema: list[str],
    *,
    seed: int | None = None,
) -> dict[str, Any]:
    """Generate a fully-populated character dict ready for ``StateAPI.create_character``.

    Randomizes stats within ruleset-appropriate ranges. Uses a simple 3d6
    per ability by default (Cairn 2e compatible). For D&D-style rulesets
    (6 abilities), uses the same 3d6 baseline.

    Args:
        general_data: Output of :func:`parse_general_md`.
        ability_schema: Output of :func:`extract_ability_schema`.
        seed: Optional RNG seed for deterministic generation.

    Returns:
        Dict with all required StateAPI character fields: ``id``, ``name``,
        ``kind``, ``species``, ``archetype``, ``hp``, ``max_hp``, ``ac``,
        ``abilities``, plus optional fields: ``traits``, ``flaws``, ``bonds``,
        ``combat_style``, ``backstory``, ``dialogue_examples``.
    """
    rng = random.Random(seed)

    # Derive a machine-readable ID from the name
    name = general_data.get("name", "unknown")
    char_id = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "unnamed"

    # Generate abilities
    abilities: dict[str, int] = {}
    for ab in ability_schema:
        abilities[ab] = sum(rng.randint(1, 6) for _ in range(3))  # 3d6

    # Generate HP based on ruleset-appropriate range (default: 1d6)
    hp = rng.randint(1, 6)
    # Determine AC — for Cairn 2e, AC = 0 (no base, armor provides reduction)
    # For D&D 5e, base AC = 10 + DEX mod (but we don't know the formula here)
    # Default: 10 for D&D-like, 0 for Cairn-like
    if len(ability_schema) >= 6:
        # D&D-style
        dex_val = abilities.get("dex", 10)
        dex_mod = (dex_val - 10) // 2
        ac = 10 + dex_mod
        # HP: 1d10 for fighter-like, 1d6 for wizard-like
        hp = rng.randint(1, 10)
    else:
        # Cairn-style
        ac = 0  # Cairn doesn't use base AC; armor provides damage reduction
        hp = rng.randint(1, 6)

    # Build the state dict
    state: dict[str, Any] = {
        "id": char_id,
        "name": name,
        "kind": general_data.get("kind", "pc"),
        "species": general_data.get("species", "Unknown"),
        "archetype": general_data.get("archetype", "Unknown"),
        "hp": hp,
        "max_hp": hp,
        "ac": ac,
        "abilities": abilities,
        "traits": general_data.get("traits", []),
        "combat_style": general_data.get("combat_style", {}),
        "conditions": [],
        "is_alive": True,
    }

    # Optional / extended fields
    for opt_key in ("flaws", "bonds"):
        if general_data.get(opt_key):
            state[opt_key] = general_data[opt_key]

    if general_data.get("backstory"):
        state["notes"] = [general_data["backstory"]]

    if general_data.get("dialogue_examples"):
        state["dialogue_examples"] = general_data["dialogue_examples"]

    logger.info(
        "generate_character_state id=%s name=%r abilities=%s hp=%d ac=%d",
        char_id, name, ability_schema, hp, ac,
    )
    return state
