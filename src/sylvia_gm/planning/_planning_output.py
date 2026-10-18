"""Planning output envelope — Pydantic models and error types.

Defines:
- PlanningOutput: what the one-shot planning LLM call returns as structured JSON.
- PlanningResult: dataclass returned by PlanningEngine.generate().
- PlanningError hierarchy: typed exceptions for planning failures.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from sylvia_gm.planning._campaign_bible import CampaignBible
from sylvia_gm.planning._reveal_registry import RevealRegistry


# ── Planning Output ───────────────────────────────────────────────────────


class PlanningOutput(BaseModel):
    """What the one-shot planning LLM call returns as structured JSON.

    Contains all three layers of the campaign data model:
    - campaign_bible: world-building entities (factions, NPCs, locations, items)
    - chapters: list of chapter dicts, each with frontmatter + markdown body
    - reveal_registry: anti-spoiler secret tracking
    """

    campaign_bible: CampaignBible
    chapters: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Each dict has 'frontmatter' (dict) and 'body' (str) keys",
    )
    reveal_registry: RevealRegistry

    model_config = {"extra": "forbid"}


# ── Planning Result ───────────────────────────────────────────────────────


@dataclass
class PlanningResult:
    """Result of a planning generation run.

    Returned by PlanningEngine.generate() — success/failure state plus
    paths to written files for verification / UI display.
    """

    success: bool
    error: str = ""
    planning_dir: Path | None = None
    files_written: list[str] = field(default_factory=list)


# ── Planning Errors ───────────────────────────────────────────────────────


class PlanningError(Exception):
    """Base exception for all planning-phase failures."""


class PlanningBudgetError(PlanningError):
    """Raised when the planning call exceeds the token or cost budget."""


class PlanningTimeoutError(PlanningError):
    """Raised when the planning LLM call times out."""
