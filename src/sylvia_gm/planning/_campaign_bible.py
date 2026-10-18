"""Campaign Bible Pydantic models.

Defines the entity hierarchy and root schema for campaign_bible.json —
the world-building layer of the three-layer campaign data model.

Written to: ~/.sylvia-gm/{campaign_id}/planning/campaign_bible.json
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# ── Entity ID pattern ─────────────────────────────────────────────────────

_ENTITY_ID_FIELD = Field(
    ...,
    min_length=1,
    max_length=64,
    pattern=r"^[a-z0-9_]+$",
    description="Unique snake_case identifier (no hyphens, no spaces)",
)


# ── Base Entity ───────────────────────────────────────────────────────────


class BibleEntity(BaseModel):
    """Base class for all bible entities.

    Every entity shares an id, human-readable name, freeform description,
    and a list of secret IDs that link into the reveal registry.
    """

    id: str = _ENTITY_ID_FIELD
    name: str = Field(..., min_length=1, max_length=200, description="Human-readable display name")
    description: str = Field(default="", description="Freeform entity description")
    secrets: list[str] = Field(
        default_factory=list,
        description="References to reveal_registry.json secret IDs",
    )

    model_config = {"extra": "forbid"}


# ── World ─────────────────────────────────────────────────────────────────


class WorldEntity(BibleEntity):
    """Top-level world description.

    Contains broad-strokes setting details — history, magic system, and
    tech level — that apply globally across the campaign.
    """

    type: Literal["world"] = "world"
    history: str = Field(default="", description="World history and lore")
    magic_system: str = Field(default="", description="How magic works in this world")
    tech_level: str = Field(default="", description="Overall technological era")


# ── Faction ───────────────────────────────────────────────────────────────


class FactionEntity(BibleEntity):
    """Political faction or organization.

    Tracks goals, leadership, headquarters, and political relationships
    (allies and rivals) with other factions.
    """

    type: Literal["faction"] = "faction"
    goals: str = Field(default="", description="Primary objectives of the faction")
    leader_id: str = Field(default="", description="Reference to an NPC entity ID")
    headquarters: str = Field(default="", description="Location or description of HQ")
    allies: list[str] = Field(
        default_factory=list,
        description="Faction IDs that are allied with this faction",
    )
    rivals: list[str] = Field(
        default_factory=list,
        description="Faction IDs that are rivals of this faction",
    )


# ── NPC ───────────────────────────────────────────────────────────────────


class NPCEntity(BibleEntity):
    """Non-player character.

    Includes personality, voice direction, optional combat stats, and
    role classification for DM system-prompt injection.
    """

    type: Literal["npc"] = "npc"
    species: str = Field(default="Human", description="Character species / race")
    role: str = Field(default="", description="e.g., 'quest giver', 'antagonist', 'ally'")
    personality: str = Field(default="", description="Personality traits and demeanor")
    voice_cadence: str = Field(default="", description="Speech pattern, accent, pacing")
    never_says: str = Field(default="", description="Things this NPC would never say")
    combat_style: dict[str, Any] = Field(
        default_factory=dict,
        description="Descriptive combat style (preferred/melee/ranged/weakness)",
    )
    # Optional combat stats (for statblock lookup)
    hp: int | None = Field(default=None, ge=0, description="Current hit points")
    max_hp: int | None = Field(default=None, ge=0, description="Maximum hit points")
    ac: int | None = Field(default=None, ge=0, le=50, description="Armour Class")
    abilities: dict[str, int] = Field(
        default_factory=dict,
        description="Ability scores (e.g., {'str': 14, 'dex': 12, 'wil': 10})",
    )


# ── Location ──────────────────────────────────────────────────────────────


class LocationEntity(BibleEntity):
    """Game location.

    Locations can nest (parent_location) and connect to other locations
    via the connecting_locations list, forming a traversable graph.
    """

    type: Literal["location"] = "location"
    region: str = Field(default="", description="Broader region, e.g. 'The Severed Lands'")
    parent_location: str = Field(
        default="",
        description="Reference to another LocationEntity ID (parent in hierarchy)",
    )
    atmosphere: str = Field(default="", description="e.g., 'eerie', 'peaceful', 'dangerous'")
    connecting_locations: list[str] = Field(
        default_factory=list,
        description="Location IDs reachable from this location",
    )


# ── Item ──────────────────────────────────────────────────────────────────


class ItemEntity(BibleEntity):
    """Significant item (quest item, artifact, key equipment).

    Tracks rarity, magical/non-magical properties, and current owner.
    """

    type: Literal["item"] = "item"
    rarity: Literal["common", "uncommon", "rare", "legendary", "artifact"] = Field(
        default="common",
        description="Item rarity tier",
    )
    properties: str = Field(default="", description="Magical or mechanical properties")
    owner: str = Field(default="", description="Reference to an NPC or location ID")


# ── Root Schema ───────────────────────────────────────────────────────────


class CampaignBible(BaseModel):
    """Root schema for campaign_bible.json.

    The world-building layer of the three-layer campaign data model.
    Contains one world, plus lists of factions, NPCs, locations, and items.

    Written to: ~/.sylvia-gm/{campaign_id}/planning/campaign_bible.json
    """

    version: Literal["1.0"] = "1.0"
    campaign_id: str = Field(..., min_length=1, description="Campaign identifier")
    world: WorldEntity
    factions: list[FactionEntity] = Field(default_factory=list)
    npcs: list[NPCEntity] = Field(default_factory=list)
    locations: list[LocationEntity] = Field(default_factory=list)
    items: list[ItemEntity] = Field(default_factory=list)

    model_config = {"extra": "forbid"}
