"""Legacy output schema for builder._interview_extractor.

The extractor writes interview_manifest.json beneath its configured data
directory and campaign ID. This model does not own current Campaign Interview
state; see application.authority and the construction contracts in docs/INDEX.md.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CharacterBlock(BaseModel):
    """Player character extracted from interview answers."""

    name: str = Field(..., min_length=1, max_length=100)
    species: str = "Human"
    archetype: str = "Adventurer"
    max_hp: int = Field(default=18, ge=1, le=999)
    hp: int = Field(default=18, ge=0, le=999)
    ac: int = Field(default=10, ge=0, le=50)
    abilities: dict[str, int] = Field(
        default_factory=lambda: {"str": 10, "dex": 10, "wil": 10}
    )
    traits: list[str] = Field(default_factory=list)
    backstory: str = ""
    personality: str = ""


class InterviewManifest(BaseModel):
    """Structured result of the legacy post-interview extraction call."""

    # ── Metadata ──
    version: Literal["1.0"] = "1.0"
    campaign_id: str = Field(..., min_length=1)
    extracted_at: str = ""  # ISO 8601 timestamp

    # ── Campaign Dimensions ──
    genre: str = Field(..., min_length=1, max_length=100)
    setting: str = Field(..., min_length=1, max_length=500)
    tone: Literal["dark", "heroic", "whimsical", "gritty", "mysterious", "mixed"] = (
        "heroic"
    )
    world_name: str = Field(default="", max_length=100)

    # ── Content Boundaries ──
    content_boundaries: list[str] = Field(default_factory=list)
    difficulty: Literal["easy", "normal", "hard", "deadly"] = "normal"

    # ── Character ──
    character_concept: str = Field(..., min_length=1, max_length=1000)
    character_block: CharacterBlock = Field(default_factory=CharacterBlock)

    # ── Raw widget answers (for debugging / fallback) ──
    raw_answers: dict[str, str] = Field(default_factory=dict)
