"""Compatibility schemas for the original one-shot planning pipeline.

Provides Pydantic models for:
- Campaign Bible (world, factions, NPCs, locations, items)
- Reveal Registry (anti-spoiler secret tracking)
- Planning Output (one-shot LLM call result envelope)

These describe the retained bible/chapters/registry output, not the current
structured Campaign Plan in domain.campaign_plan. Current campaign construction
is coordinated by application.authority and agent._builder_session.
"""

from sylvia_gm.planning._campaign_bible import (
    BibleEntity,
    CampaignBible,
    FactionEntity,
    ItemEntity,
    LocationEntity,
    NPCEntity,
    WorldEntity,
)
from sylvia_gm.planning._planning_output import (
    PlanningBudgetError,
    PlanningError,
    PlanningOutput,
    PlanningResult,
    PlanningTimeoutError,
)
from sylvia_gm.planning._reveal_registry import (
    RevealRegistry,
    SecretEntry,
)

__all__ = [
    # Campaign Bible
    "BibleEntity",
    "CampaignBible",
    "FactionEntity",
    "ItemEntity",
    "LocationEntity",
    "NPCEntity",
    "WorldEntity",
    # Reveal Registry
    "RevealRegistry",
    "SecretEntry",
    # Planning Output
    "PlanningBudgetError",
    "PlanningError",
    "PlanningOutput",
    "PlanningResult",
    "PlanningTimeoutError",
]
