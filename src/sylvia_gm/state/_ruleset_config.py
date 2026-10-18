"""Ruleset configuration for state validation and pre-initialization.

Defines what fields are active under each ruleset. The active ruleset
determines which fields are semantically active, what default values
to use, and what operations are permitted.

NOTE: RULESET_CONFIG is a hardcoded stopgap. It should be replaced by
deriving entity defaults from the loaded ruleset YAML's entity_fields.
"""

from __future__ import annotations

from typing import Any

RULESET_CONFIG: dict[str, dict[str, Any]] = {
    "sylvia.srd-lite.bg3": {
        "abilities": ["str", "dex", "con", "int", "wis", "cha"],
        "death_saves_enabled": True,
        "spell_slots_enabled": True,
        "concentration_enabled": True,
        "proficiency_bonus_enabled": True,
        "hit_dice_enabled": True,
        "death_at_zero_hp": False,
    },
}

# All ability scores that the schema supports (superset).
ALL_ABILITIES: tuple[str, ...] = ("str", "dex", "con", "int", "wis", "cha", "wil")

# D&D abilities.
DND_ABILITIES: tuple[str, ...] = ("str", "dex", "con", "int", "wis", "cha")

# All equipment slots (superset).
EQUIPMENT_SLOTS: tuple[str, ...] = (
    "main_hand",
    "off_hand",
    "armor",
    "head",
    "neck",
    "ring_1",
    "ring_2",
    "cloak",
    "boots",
)

# All resource types (superset).
RESOURCE_TYPES: tuple[str, ...] = (
    "spell_slots",
    "ki_points",
    "hit_dice",
    "channel_divinity",
    "rage_uses",
    "bardic_inspiration",
    "sorcery_points",
)

# Valid conditions (superset — all common TTRPG conditions).
VALID_CONDITIONS: frozenset[str] = frozenset(
    {
        "blinded",
        "charmed",
        "deafened",
        "frightened",
        "grappled",
        "incapacitated",
        "invisible",
        "paralyzed",
        "petrified",
        "poisoned",
        "prone",
        "restrained",
        "stunned",
        "unconscious",
        "concentrating",
        "exhaustion_1",
        "exhaustion_2",
        "exhaustion_3",
        "exhaustion_4",
        "exhaustion_5",
        "exhaustion_6",
        "blessed",
        "cursed",
        "hidden",
        "dodging",
        "hastened",
        "slowed",
        "enlarged",
        "reduced",
        "raging",
        "inspired",
        # Compatibility conditions retained for user-authored packages.
        "deprived",
        "fatigued",
        "scarred",
    }
)

# Valid combat statuses.
VALID_COMBAT_STATUSES: frozenset[str] = frozenset(
    {"active", "victory", "defeat", "fled"}
)

# Valid quest statuses.
VALID_QUEST_STATUSES: frozenset[str] = frozenset(
    {"available", "active", "completed", "failed"}
)

# Valid NPC attitudes.
VALID_NPC_ATTITUDES: frozenset[str] = frozenset(
    {"hostile", "neutral", "friendly", "allied"}
)

# Valid monster sizes.
VALID_MONSTER_SIZES: frozenset[str] = frozenset(
    {"tiny", "small", "medium", "large", "huge", "gargantuan"}
)

# Valid time-of-day periods.
VALID_TIME_PERIODS: frozenset[str] = frozenset(
    {"morning", "afternoon", "evening", "night", "midnight", "dawn"}
)


def get_ruleset_config(ruleset: str) -> dict[str, Any]:
    """Return the config dict for *ruleset*, or raise KeyError."""
    if ruleset not in RULESET_CONFIG:
        available = ", ".join(sorted(RULESET_CONFIG))
        raise KeyError(
            f"Unknown ruleset {ruleset!r}. Available: {available}"
        )
    return RULESET_CONFIG[ruleset]
