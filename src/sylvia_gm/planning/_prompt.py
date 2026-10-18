"""Prompts for the retained one-shot bible/chapters/registry planning call.

Current structured plans use domain.campaign_plan, with Builder instructions
in agent._builder_session and the tracked BUILD CAMPAIGN PLAN skill. This
legacy prompt does not define the current campaign-construction contract.
"""

from __future__ import annotations

import json

from sylvia_gm.builder._interview_manifest import InterviewManifest


# ── Planning System Prompt (Rules 1–9) ──────────────────────────


def build_planning_system_prompt() -> str:
    """Return the planning system prompt that directs the LLM to produce a
    complete three-layer campaign plan.

    The prompt includes the desired JSON output shape and 9 structural /
    content rules the model must follow.
    """
    return """\
You are a Dungeon Master planning engine. Given an interview manifest
describing a player's campaign preferences, produce a complete campaign plan.

OUTPUT FORMAT (JSON):
{
  "campaign_bible": {
    "version": "1.0",
    "campaign_id": "...",
    "world": { "id": "...", "name": "...", "description": "...", "history": "...", "magic_system": "...", "tech_level": "...", "secrets": [] },
    "factions": [ { "id": "...", "name": "...", "description": "...", "goals": "...", "secrets": [] } ],
    "npcs": [ { "id": "...", "name": "...", "description": "...", "species": "...", "role": "...", "personality": "...", "voice_cadence": "...", "secrets": [] } ],
    "locations": [ { "id": "...", "name": "...", "description": "...", "region": "...", "atmosphere": "...", "secrets": [] } ],
    "items": [ { "id": "...", "name": "...", "description": "...", "rarity": "...", "properties": "...", "secrets": [] } ]
  },
  "chapters": [
    {
      "frontmatter": { "chapter": 1, "title": "...", "synopsis": "...", "key_locations": [], "npcs_appearing": [], "secrets_planted": [], "combat_encounters": [], "chapter_transition": "..." },
      "body": "## Synopsis\\n...\\n## Story Beats\\n..."
    }
  ],
  "reveal_registry": {
    "version": "1.0",
    "campaign_id": "...",
    "secrets": [
      { "id": "...", "title": "...", "summary": "...", "planted_in_chapter": 1, "reveal_trigger": "...", "revealed": false, "consequences_if_revealed_early": "..." }
    ]
  }
}

RULES:
1. Generate 3-5 chapters. Each chapter has 3-5 story beats.
2. Every entity gets a unique, descriptive snake_case id.
3. Every secret in the reveal registry must be linked from at least one entity's secrets[] array.
4. NPCs should have distinct personalities, voice patterns, and combat styles.
5. Locations should connect to each other via connecting_locations fields.
6. Combat encounters should be balanced for a level 1-3 party.
7. All ids must match [a-z0-9_]+ — no hyphens, no spaces.
8. The output MUST be valid JSON. No markdown wrappers. No code blocks.
9. Respect all content_boundaries from the interview manifest — do not include themes, scenarios, or descriptions that violate the player's stated boundaries."""


# ── Planning User Prompt (Manifest Injection) ───────────────────


def build_planning_user_prompt(manifest: InterviewManifest, ruleset: str) -> str:
    """Build the user message that injects the interview manifest as JSON
    and instructs the LLM to generate the complete campaign plan.

    Args:
        manifest: The validated InterviewManifest from the interview phase.
        ruleset: The ruleset ID to use for the campaign.

    Returns:
        The complete user-prompt string for the one-shot planning call.
    """
    manifest_json = json.dumps(manifest.model_dump(), indent=2)
    return f"""CAMPAIGN ID: {manifest.campaign_id}
RULESET: {ruleset}

INTERVIEW MANIFEST:
{manifest_json}

Generate the complete campaign plan based on the above preferences."""
