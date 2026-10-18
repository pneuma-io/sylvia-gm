"""Reveal Registry Pydantic models.

Tracks campaign secrets — what they are, where they're planted, when they
should be revealed, and what happens if players discover them early.

Part of the anti-spoiler guardrail system (§6 of the implementation plan):
the DM session prompt is injected with a "DO NOT REVEAL" section built from
this registry.

Written to: ~/.sylvia-gm/{campaign_id}/planning/reveal_registry.json
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# ── Entity ID pattern ─────────────────────────────────────────────────────

_SECRET_ID_FIELD = Field(
    ...,
    min_length=1,
    max_length=64,
    pattern=r"^[a-z0-9_]+$",
    description="Unique snake_case secret identifier (no hyphens, no spaces)",
)


# ── Secret Entry ──────────────────────────────────────────────────────────


class SecretEntry(BaseModel):
    """A single secret tracked by the reveal registry.

    Each secret has a unique id, a planting chapter, a natural-language
    trigger for when it should surface, and a revealed flag that the DM
    session toggles after the secret is disclosed.
    """

    id: str = _SECRET_ID_FIELD
    title: str = Field(..., min_length=1, max_length=200, description="Short secret title")
    summary: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Brief summary of the secret content",
    )
    planted_in_chapter: int = Field(
        ...,
        ge=0,
        description="Chapter index where this secret is first introduced (0 = prologue)",
    )
    reveal_trigger: str = Field(
        default="",
        description="Natural-language description of when this secret should surface",
    )
    revealed: bool = Field(
        default=False,
        description="Whether the secret has been disclosed to players",
    )
    revealed_at_chapter: int | None = Field(
        default=None,
        description="Chapter index where the secret was actually revealed (if revealed)",
    )
    consequences_if_revealed_early: str = Field(
        default="",
        description="What happens if players discover this secret before the intended chapter",
    )

    model_config = {"extra": "forbid"}


# ── Root Schema ───────────────────────────────────────────────────────────


class RevealRegistry(BaseModel):
    """Root schema for reveal_registry.json.

    The anti-spoiler layer of the three-layer campaign data model.
    Every secret in this registry is linked from at least one entity's
    ``secrets[]`` array in the campaign bible.

    Written to: ~/.sylvia-gm/{campaign_id}/planning/reveal_registry.json
    """

    version: Literal["1.0"] = "1.0"
    campaign_id: str = Field(..., min_length=1, description="Campaign identifier")
    secrets: list[SecretEntry] = Field(
        default_factory=list,
        description="All tracked campaign secrets",
    )

    model_config = {"extra": "forbid"}
