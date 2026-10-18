"""Legacy pattern-based filters for generated narrative text.

The retained prototype filters below are heuristic text checks, not current
Ruleset mechanics or a specification of GM behavior. Current product policy
belongs to the contracts listed in docs/INDEX.md.

Categories:
    - D&D mechanics in dialogue (AC, DC, spell slots, proficiency, Ki cost, etc.)
    - Meta/structural terms (session, chapter, act, scene)
    - Kill competition language
    - Sycophantic NPC awe
    - Over-analytical NPC deductions
    - Death assumptions
    - Sample Player-specific: 2nd person references to Sample Player
    - Negative-stacking constructions
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ── ScanResult ────────────────────────────────────────────────────────────


@dataclass
class ScanResult:
    """The result of a narrative scan on AI DM output.

    Attributes:
        clean: True if no violations were found.
        violations: Human-readable descriptions of each violation.
        filtered_output: The text with violating patterns replaced by
            ``[FILTERED]`` markers. Identical to the original if clean.
    """

    clean: bool
    """True when no rule violations were detected."""

    violations: list[str] = field(default_factory=list)
    """Descriptive strings for each detected violation (e.g. 'D&D mechanics: AC')."""

    filtered_output: str = ""
    """The output text with violating substrings replaced by ``[FILTERED]``."""


# ── NarrativeScanner ──────────────────────────────────────────────────────


class NarrativeScanner:
    """Apply the retained prototype's narrative text filters.

    Scans the AI DM's narrative output for the eight categories of language-level
    patterns listed in this module. Each category is expressed as one or more
    regular expression patterns; matches are collected as human-readable
    ``violations`` and the ``filtered_output`` text has matched spans replaced
    with ``[FILTERED]``.

    Usage::

        scanner = NarrativeScanner()
        result = scanner.scan(llm_response)
        if not result.clean:
            log.warning("Narrative violations: %s", result.violations)
            # Optionally use result.filtered_output instead of the raw text.
    """

    # Regex escape helper — precompile all patterns at class level for speed.
    _FILTER_PLACEHOLDER = "[FILTERED]"

    # ── Rule definitions ────────────────────────────────────────────────
    #
    # Each entry: (human_label, compiled_regex)
    # The label becomes the violation description (e.g. "D&D mechanics: AC").
    # Patterns are tested case-insensitively.

    RULES: list[tuple[str, re.Pattern]] = []

    @classmethod
    def _compile_rules(cls) -> list[tuple[str, re.Pattern]]:
        """Build the compiled rule list (lazy, cached on the class)."""
        if cls.RULES:
            return cls.RULES

        flags = re.IGNORECASE

        def _c(pattern: str) -> re.Pattern:
            return re.compile(pattern, flags)

        rules: list[tuple[str, re.Pattern]] = []

        # ── 1. D&D mechanics in dialogue ────────────────────────────────
        rules.extend([
            ("D&D mechanics: AC referenced",
             _c(r"\bAC\s+\d+")),
            ("D&D mechanics: DC referenced",
             _c(r"\bDC\s+\d+")),
            ("D&D mechanics: spell slots",
             _c(r"\bspell\s*slots?\b")),
            ("D&D mechanics: proficiency bonus",
             _c(r"\bproficiency\s*(?:bonus|modifier)?\b")),
            ("D&D mechanics: Ki cost",
             _c(r"\bKi\s+cost\b")),
            ("D&D mechanics: death saves",
             _c(r"\bdeath\s+saves?\b")),
            ("D&D mechanics: saving throw",
             _c(r"\bsaving\s+throw\b")),
            ("D&D mechanics: armor class term",
             _c(r"\barmor\s+class\b")),
        ])

        # ── 2. Meta / structural terms in narration ─────────────────────
        rules.extend([
            ("meta term: 'session'",
             _c(r"\b(?:this|our|the|during|for)\s+session\b")),
            ("meta term: 'chapter'",
             _c(r"\b(?:next|this|the|new)\s+chapter\b")),
            ("meta term: 'act' (structural)",
             _c(r"\bAct\s+[IVX]+\b")),
            ("meta term: 'scene' (structural)",
             _c(r"\b(?:scene\s+transition|end\s+of\s+scene|cut\s+scene)\b")),
        ])

        # ── 3. Kill competition language ────────────────────────────────
        rules.extend([
            ("kill competition: kill-stealing",
             _c(r"\b(?:before\s+you\s+(?:can|get\s+to|finish|land)|"
                r"steals?\s+(?:your|the)\s+kill|"
                r"finishes?\s+(?:it|them)\s+off\s+before\s+you|"
                r"beat(?:s|ing)?\s+you\s+to\s+(?:the|a)\s+(?:kill|blow|punch))")),
        ])

        # ── 4. Sycophantic NPC awe ──────────────────────────────────────
        rules.extend([
            ("sycophantic NPC: over-the-top awe",
             _c(r"\b(?:the\s+(?:most|greatest)\s+(?:amazing|incredible|impressive|"
                r"skilled|powerful|brave)\s+(?:person|warrior|being|fighter|"
                r"adventurer|soul)\s+I'?ve?\s+ever\s+(?:seen|met|known|encountered)|"
                r"I'?m\s+not\s+worthy|"
                r"I'?ve\s+never\s+seen\s+(?:any(?:one|thing)|someone)\s+(?:so|as|this)\s+"
                r"(?:amazing|incredible|impressive|skilled|powerful|brave|beautiful|"
                r"strong|magnificent|perfect|talented))")),
        ])

        # ── 5. Over-analytical NPC deductions ───────────────────────────
        rules.extend([
            ("over-analytical NPC: leaps to conclusions",
             _c(r"\b(?:I\s+can\s+(?:tell|see|sense)\s+(?:from|by|that)\s+(?:your|the|"
                r"how|what)|"
                r"clearly\s+you\s+(?:are|must\s+be|have)|"
                r"you\s+must\s+be\s+the\s+(?:one|chosen|prophesied|hero)|"
                r"it'?s?\s+obvious\s+(?:that\s+)?you'?re?\s+(?:the|a))")),
        ])

        # ── 6. Death assumptions ────────────────────────────────────────
        rules.extend([
            ("death assumption: declared dead",
             _c(r"\b(?:you\s+(?:died|were\s+dead|are\s+dead|have\s+died)|"
                r"when\s+you\s+died|"
                r"your\s+death|"
                r"after\s+you\s+died|"
                r"since\s+you\s+died)\b")),
        ])

        # ── 7. Sample Player-specific: 2nd person references ───────────────────
        # The DM must address the player as "you", never by character name.
        rules.extend([
            ("Sample Player-specific: character name in narration",
             _c(r"\bSample Player\b")),
        ])

        # ── 8. Negative-stacking constructions ──────────────────────────
        rules.extend([
            ("negative-stacking: double negative",
             _c(r"\b(?:can'?t\s+not|don'?t\s+have\s+no|doesn'?t\s+have\s+no|"
                r"isn'?t\s+no|aren'?t\s+no|"
                r"not\s+never|not\s+no\s+(?:way|chance|hope)|"
                r"won'?t\s+not|wouldn'?t\s+not)\b")),
        ])

        cls.RULES = rules
        return rules

    # ── Public API ──────────────────────────────────────────────────────

    def scan(self, text: str, streaming: bool = False) -> ScanResult:
        """Scan DM narrative output for Tier 2 rule violations.

        Args:
            text: The AI DM's full narrative response text.
            streaming: If True, use a lighter rule set that only filters
                ``<thinking>`` and similar meta-tags (deferred to non-streaming
                scanner for full Tier 2 enforcement).

        Returns:
            ScanResult with ``clean`` status, ``violations`` list, and
            ``filtered_output`` (original text with matched spans replaced).
        """
        if not text or not text.strip():
            return ScanResult(clean=True, filtered_output=text)

        rules = self._compile_rules()

        if streaming:
            # Streaming mode: only filter <thinking>-style tags that may leak
            # from providers that embed reasoning inline rather than via the
            # dedicated reasoning_content field.
            streaming_patterns = [
                (f"streaming: {tag} tag leaked",
                 re.compile(tag, re.IGNORECASE | re.DOTALL))
                for tag in [
                    r"<thinking>.*?</thinking>",
                    r"<reasoning>.*?</reasoning>",
                    r"<think>.*?</think>",
                    r"<thought>.*?</thought>",
                    r"<REASONING_SCRATCHPAD>.*?</REASONING_SCRATCHPAD>",
                ]
            ]
            rules = streaming_patterns

        violations: list[str] = []
        filtered = text

        for label, pattern in rules:
            if pattern.search(filtered):
                violations.append(label)
                filtered = pattern.sub(self._FILTER_PLACEHOLDER, filtered)

        clean = len(violations) == 0
        return ScanResult(
            clean=clean,
            violations=violations,
            filtered_output=filtered if not clean else text,
        )
