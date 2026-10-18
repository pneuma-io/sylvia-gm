"""Legacy off-plan detection and pause/resume helpers (originally T3.4).

The retained detector classifies output using its original improvisation tiers:
    Tier 1 (Forbidden): New plot, NPCs, mechanics → blocked, player asked for direction
    Tier 2 (Delegated): Environmental description, flavor, NPC small talk → allowed, logged
    Tier 3 (Allowed):  Rephrasing pre-authored content, narrating outcomes → allowed, not logged

It also includes a pause/resume cycle with OOC markers (⏸️/▶️). These prototype
policies do not define current GM behavior; use the product and architecture
contracts listed in docs/INDEX.md for that decision.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sylvia_gm.agent._runner import AgentRunner as SessionRunner, AgentTurnResult as TurnResult


# ── DivergenceResult ─────────────────────────────────────────────────────


@dataclass
class DivergenceResult:
    """The outcome of a divergence analysis on LLM output."""

    tier: int
    """Which tier of the Graded Improvisation Model this falls under (1, 2, or 3)."""

    blocked: bool
    """True if the LLM response should be blocked from reaching the player (Tier 1)."""

    reason: str = ""
    """Human-readable explanation of why the content was flagged."""

    suggestion: str = ""
    """Suggested alternative the player might choose (for Tier 1 pauses)."""


# ── DivergenceDetector ───────────────────────────────────────────────────


class DivergenceDetector:
    """Detect when LLM output goes beyond improvisation bounds.

    Uses pattern matching against known Tier 1 phrases (new content creation).
    In v0.1 this is keyword-based; future versions will use the campaign plan
    for semantic comparison.

    Usage::

        detector = DivergenceDetector()
        result = detector.analyze(llm_response, turn_context={})
        if result.blocked:
            # pause the session, ask player for direction
    """

    # Keywords that suggest Tier 1 divergence (new content creation)
    FORBIDDEN_PATTERNS: list[tuple[str, str]] = [
        (r"suddenly\s+appears?", "introducing a new element suddenly"),
        (r"you\s+discover\b", "introducing a discovery the player didn't make"),
        (r"you\s+find\s+a\b", "introducing an unplanned item or location"),
        (r"new\s+character\b", "introducing a new character"),
        (r"secret\s+door", "introducing a secret door"),
        (r"hidden\s+passage", "introducing a hidden passage"),
        (r"changes?\s+the\s+DC", "changing a difficulty class arbitrarily"),
        (r"reduces?\s+the\s+difficulty", "reducing difficulty arbitrarily"),
    ]

    # Patterns that strongly suggest Tier 3 (mechanical rephrasing / outcomes)
    TIER3_PATTERNS: list[str] = [
        r"\broll(?:ed|s|ing)?\b",
        r"\b(?:d\d+|dice)\b",
        r"\b(?:deal(?:s|ing)?|takes?|taking|suffers?|suffering)\s+\d+\s+(?:damage|hp)\b",
        r"\b(?:you|they)\s+(?:succeed|fail|pass|miss)\b",
        r"\b(?:saving\s+throw|ability\s+check|skill\s+check)\b",
        r"\b(?:HP|hit\s+points?)\b.*\b(?:restored?|lost|gained?|reduced?)\b",
        r"\b(?:critical\s+(?:hit|miss|success|failure))\b",
        r"\b(?:AC|armor\s+class)\b",
        r"\b(?:initiative)\b",
    ]

    def analyze(
        self,
        llm_response: str,
        turn_context: dict | None = None,
    ) -> DivergenceResult:
        """Analyze LLM output for divergence.

        Args:
            llm_response: The LLM's narrative text to analyze.
            turn_context: Optional context about the current turn (v0.1: unused).

        Returns:
            DivergenceResult with tier classification and blocking decision.
        """
        if not llm_response or not llm_response.strip():
            # Empty response is unusual but not a Tier 1 violation
            return DivergenceResult(tier=3, blocked=False, reason="Empty response")

        response_lower = llm_response.lower()

        # ── Step 1: Check Tier 1 (Forbidden) ──────────────────────────
        for pattern, reason in self.FORBIDDEN_PATTERNS:
            if re.search(pattern, response_lower):
                return DivergenceResult(
                    tier=1,
                    blocked=True,
                    reason=f"Response contains content that appears to be {reason}.",
                    suggestion=(
                        "Would you like me to (a) improvise something here, "
                        "(b) steer back toward the nearest planned beat, or "
                        "(c) give me some direction?"
                    ),
                )

        # ── Step 2: Check Tier 3 (Mechanical rephrasing) ─────────────
        for pattern in self.TIER3_PATTERNS:
            if re.search(pattern, response_lower):
                return DivergenceResult(
                    tier=3,
                    blocked=False,
                    reason="Mechanical narration / rephrasing of outcomes.",
                )

        # ── Step 3: Default to Tier 2 (Environmental/flavor) ──────────
        return DivergenceResult(
            tier=2,
            blocked=False,
            reason="Environmental description or flavor narration.",
        )


# ── DivergenceHandler ────────────────────────────────────────────────────


class DivergenceHandler:
    """Handle detected divergence with a pause/resume cycle.

    When Tier 1 divergence is detected, this handler:
    1. Blocks the LLM's response from reaching the player
    2. Pauses the session (sets runner._paused = True)
    3. Presents the player with options via OOC markers
    4. On resume, interprets the player's choice

    Usage::

        handler = DivergenceHandler()
        result = handler.handle_divergence(div_result, runner)
        # result.dm_response contains the pause message
        # runner._paused is now True
    """

    def handle_divergence(
        self,
        result: DivergenceResult,
        runner: SessionRunner,
    ) -> TurnResult:
        """Handle a detected divergence by pausing the session.

        Args:
            result: The DivergenceResult from the detector.
            runner: The SessionRunner to pause.

        Returns:
            TurnResult containing the pause message with OOC markers.
        """
        # Mark the session as paused
        runner._paused = True

        # Build the legacy pause message with OOC markers.
        pause_message = self._build_pause_message(result)

        # Import here to avoid circular imports at module level
        from sylvia_gm.agent._runner import AgentTurnResult as TurnResult

        return TurnResult(
            dm_response=pause_message,
            tool_calls_made=[],
            state_changed=False,
            is_ooc=True,
        )

    def _build_pause_message(self, result: DivergenceResult) -> str:
        """Construct the OOC pause message with player options.

        Retained message format:
            ⏸️ SESSION PAUSED

            I noticed [reason]. Since this isn't in the campaign plan:
            (a) I can improvise something here and we retcon if needed
            (b) Want to steer back toward [nearest planned beat]?
            (c) What direction were you thinking?

            ▶️ RESUME when ready
        """
        lines = [
            "⏸️ SESSION PAUSED",
            "",
            f"I noticed the AI was about to introduce content that appears to be {result.reason}",
            "Since this isn't in the campaign plan:",
            "(a) I can improvise something here and we retcon if needed",
            "(b) Want to steer back toward the nearest planned beat?",
            "(c) What direction were you thinking?",
            "",
            "▶️ RESUME when ready",
        ]
        return "\n".join(lines)

    @staticmethod
    def interpret_resume_choice(player_input: str) -> str:
        """Interpret the player's response to a divergence pause.

        Args:
            player_input: The player's response to the pause prompt.

        Returns:
            One of 'improvise', 'steer', or 'direction'.
        """
        inp = player_input.lower().strip()

        # Check for option (a) — improvise
        if "improvise" in inp or inp in ("a", "(a)"):
            return "improvise"

        # Check for option (b) — steer
        if "steer" in inp or inp in ("b", "(b)"):
            return "steer"

        # Default: player gave custom direction
        return "direction"
