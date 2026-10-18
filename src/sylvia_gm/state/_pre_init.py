"""Pre-initialization of entity fields (H5).

When an entity is created, all schema-defined optional fields are
pre-initialized to their defaults (or null). This eliminates the ambiguity
where ``get_field()`` returns ``None`` for both "field doesn't exist" and
"field is explicitly null."

After initialization, every schema-defined field exists as a key on the
entity dict. ``None`` always means "not applicable."
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from ._ruleset_config import (
    EQUIPMENT_SLOTS,
    RESOURCE_TYPES,
    get_ruleset_config,
)

# ---------------------------------------------------------------------------
# Character defaults
# ---------------------------------------------------------------------------

_CHARACTER_CORE_DEFAULTS: dict[str, Any] = {
    "temp_hp": 0,
    "level": 1,
    "xp": 0,
    "initiative": None,
    "conditions": [],
    "death_saves": None,
    "reaction_used": False,
    "inventory": [],
    "equipment": dict.fromkeys(EQUIPMENT_SLOTS),
    "spells_known": [],
    "romance": None,
    "skills": None,
    "proficiency_bonus": None,
    "speed": 30,
    "is_alive": True,
    "notes": [],
    "combat_control": "dm",
}

_CHARACTER_RESOURCE_DEFAULTS: dict[str, Any] = dict.fromkeys(RESOURCE_TYPES)


def _build_ability_defaults(ruleset_config: dict[str, Any]) -> dict[str, Any]:
    """Build ability score defaults for the given ruleset.

    Active abilities get {score: 10, modifier: None} (modifier computed
    at runtime by T1.4). Inactive abilities get None.

    Under Cairn: str/dex/wil active, others null.
    Under D&D: all six active.
    """
    active = ruleset_config.get("abilities", ["str", "dex", "wil"])
    abilities: dict[str, Any] = {}
    all_ab = ["str", "dex", "con", "int", "wis", "cha", "wil"]
    for ab in all_ab:
        if ab in active:
            abilities[ab] = {"score": 10, "modifier": None}
        else:
            abilities[ab] = None
    return abilities


def character_defaults(ruleset: str) -> dict[str, Any]:
    """Return the fully initialized character defaults dict for *ruleset*."""
    rs_config = get_ruleset_config(ruleset)
    defaults: dict[str, Any] = {}

    # Core defaults
    defaults.update(deepcopy(_CHARACTER_CORE_DEFAULTS))

    # Resources
    resources: dict[str, Any] = deepcopy(_CHARACTER_RESOURCE_DEFAULTS)
    # Under Cairn: spell_slots/hit_dice etc. are null.
    # Under D&D: these would get populated (future extension).
    if rs_config.get("spell_slots_enabled"):
        resources["spell_slots"] = {str(i): 0 for i in range(10)}  # 0-9
    if rs_config.get("hit_dice_enabled"):
        resources["hit_dice"] = {"current": 0, "max": 0, "size": 8}
    defaults["resources"] = resources

    # Abilities (ruleset-dependent)
    defaults["abilities"] = _build_ability_defaults(rs_config)
    return defaults


# ---------------------------------------------------------------------------
# Monster defaults
# ---------------------------------------------------------------------------

_MONSTER_CORE_DEFAULTS: dict[str, Any] = {
    "temp_hp": 0,
    "initiative": None,
    "conditions": [],
    "speed": 30,
    "is_alive": True,
    "skills": None,
    "cr": None,
}


def monster_defaults(ruleset: str) -> dict[str, Any]:
    """Return the fully initialized monster defaults dict for *ruleset*."""
    rs_config = get_ruleset_config(ruleset)
    defaults: dict[str, Any] = deepcopy(_MONSTER_CORE_DEFAULTS)
    defaults["abilities"] = _build_ability_defaults(rs_config)
    return defaults


# ---------------------------------------------------------------------------
# NPC defaults
# ---------------------------------------------------------------------------

_NPC_CORE_DEFAULTS: dict[str, Any] = {
    "location": None,
    "role": None,
    "faction": None,
    "attitude": "neutral",
    "alive": True,
    "relationship": 0,
    "knowledge": {},
    "stat_block": None,
    "notes": [],
}


def npc_defaults() -> dict[str, Any]:
    """Return the fully initialized NPC defaults dict."""
    return deepcopy(_NPC_CORE_DEFAULTS)


# ---------------------------------------------------------------------------
# Apply defaults
# ---------------------------------------------------------------------------


def apply_character_defaults(
    provided: dict[str, Any], ruleset: str
) -> dict[str, Any]:
    """Merge provided character fields with pre-initialized defaults.

    Args:
        provided: Fields provided by the caller.
        ruleset: Active ruleset ID.

    Returns:
        Fully initialized character dict with all optional fields set.
    """
    defaults = character_defaults(ruleset)
    # Start with defaults, then override with provided values
    result = deepcopy(defaults)
    # Resources require special merge (don't replace the whole dict)
    if "resources" in provided and isinstance(provided["resources"], dict):
        result["resources"].update(provided["resources"])
        del provided["resources"]
    # Equipment slot merge
    if "equipment" in provided and isinstance(provided["equipment"], dict):
        result["equipment"].update(provided["equipment"])
        del provided["equipment"]
    # Abilities merge
    if "abilities" in provided and isinstance(provided["abilities"], dict):
        for k, v in provided["abilities"].items():
            if k in result["abilities"] and result["abilities"][k] is not None:
                if isinstance(v, dict):
                    result["abilities"][k] = v
                else:
                    # Numeric score shorthand: convert to {score, modifier: None}
                    result["abilities"][k] = {"score": v, "modifier": None}
            else:
                result["abilities"][k] = v
        del provided["abilities"]
    # Apply remaining provided fields
    result.update(provided)
    return result


def apply_monster_defaults(
    provided: dict[str, Any], ruleset: str
) -> dict[str, Any]:
    """Merge provided monster fields with pre-initialized defaults."""
    defaults = monster_defaults(ruleset)
    result = deepcopy(defaults)
    # Abilities merge
    if "abilities" in provided and isinstance(provided["abilities"], dict):
        for k, v in provided["abilities"].items():
            if k in result["abilities"] and result["abilities"][k] is not None:
                if isinstance(v, dict):
                    result["abilities"][k] = v
                else:
                    result["abilities"][k] = {"score": v, "modifier": None}
            else:
                result["abilities"][k] = v
        del provided["abilities"]
    result.update(provided)
    return result


def apply_npc_defaults(provided: dict[str, Any]) -> dict[str, Any]:
    """Merge provided NPC fields with pre-initialized defaults."""
    defaults = npc_defaults()
    result = deepcopy(defaults)
    result.update(provided)
    return result
