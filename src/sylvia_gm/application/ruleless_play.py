"""No Ruleset play: keep the campaign memory, refuse game mechanics.

A No Ruleset campaign has an admitted empty package closure. It still uses Sylvia's
Campaign File (people, places, objects, history, scene, Fallback Inventory) and its
Campaign Plan, but has no combat mode, dice, checks or other mechanical resolution.
This module owns the application guide sent to that campaign's GM and the
deterministic check that keeps mechanical language out of its player-facing replies.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from sylvia_gm.domain.authority import CampaignState, WriterRole

RULELESS_MECHANICS_REJECTION_CODE = "ruleless_mechanics_in_response"

RULELESS_GM_GUIDE = """\
NO RULESET PLAY (Sylvia application-owned guide; this campaign has no Ruleset)

There is no Ruleset, so there are no game mechanics here: no combat mode, turn order or \
rounds, no dice or rolls, no checks, saves or difficulty numbers, no numeric health, \
damage, defence or other statistics, and no levels or experience. Never ask the player \
to roll or to make a check, and never present a number as a mechanical result, in \
narration, character speech or out-of-character notes. Parts of the shared GM contract \
about Ruleset tools, game panels, action queues, character classes or exposed mechanics \
do not apply to this campaign.

Resolve uncertain, risky or contested actions with GM judgment in prose. Weigh the \
established fiction, what the character has shown they can do, their situation and \
the Campaign Plan, then narrate a clear and fair outcome: success, partial success, \
a cost, or a consequence. Fights and danger still happen as story. Describe them in \
the fiction and give the player meaningful choices, without rounds or turn order. If \
the player asks for a roll or a statistic, briefly say out of character that this \
campaign uses story judgment instead, then resolve the action.

Sylvia is this campaign's memory, so ground every response in it. Campaign State in the \
fresh context is current truth. Before introducing a person, place, object, clue or \
piece of history, check whether it already exists: search the campaign knowledge and \
read the Campaign Plan or the exact record. Follow the Campaign Plan's premise, \
direction and prepared material, and never contradict it or recorded facts. When play \
needs something new, make it consistent with both and record it.

Record lasting truth in the Campaign File in the same call as the response: people and \
places as entities with their current location, status and relationships; decisions, \
discoveries, promises and consequences as memories; the current scene and location; \
and Plan Progress when it is offered.

Track useful items. Record each notable object as an entity with entity_kind "object" \
and attributes for: description (what it is and looks like); purpose (what it does or \
is for, in story terms, never as a numeric bonus); location (where it is, or who has \
it); and any limits or conditions play has established. When Fallback Inventory \
updates are offered, also record who holds and owns a carried item there, using the \
object's entity_id as its item_id. Update the object's record whenever it moves, \
changes hands, is used up, breaks or reveals something new. An item does only what its \
record and the Plan say. A new ability needs a story reason and an updated record.
"""

RULELESS_MECHANICS_INSTRUCTION = (
    "This campaign has No Ruleset, so the response must not use game mechanics. The held "
    "response contains mechanical language: {findings}. Rewrite the complete response as "
    "story prose with the same decided outcome, resolving any uncertainty by GM judgment. "
    "Do not ask the player to roll or make a check, and do not give dice, difficulty "
    "numbers, numeric health or damage, turn order or other game statistics. Resubmit the "
    "complete Campaign File update, with the same durable changes and the rewritten "
    "response in revised_player_facing_response."
)

_SKILL_WORDS = (
    "skill|ability|perception|stealth|athletics|acrobatics|insight|persuasion|deception|"
    "intimidation|investigation|arcana|history|religion|nature|survival|medicine|"
    "sleight of hand|performance|animal handling|strength|dexterity|constitution|"
    "intelligence|wisdom|charisma|luck|sanity|willpower"
)

# Each pattern names one mechanical idiom. They target rules language, not ordinary
# fiction: a character may gamble with dice or a storm may damage a roof, but "roll a
# d20", "a DC 15 check" or "takes 7 damage" are mechanics with no Ruleset to own them.
_MECHANICS_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "dice notation",
        re.compile(r"(?<![\w-])\d{0,3}d(?:4|6|8|10|12|20|100)(?![\w-])", re.IGNORECASE),
    ),
    (
        "a request to roll",
        re.compile(
            r"\broll(?:ing)?\s+(?:for\b|initiative\b|to\s+(?:see|hit|save|resist)\b"
            r"|(?:a|an|your)\s+(?:[a-z]+\s+){0,2}(?:check|save|roll|die|dice|d\d+)\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "a check or saving throw",
        re.compile(
            r"\b(?:(?:" + _SKILL_WORDS + r")\s+(?:check|save|saving\s+throw|test|roll)s?"
            r"|saving\s+throws?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "a request to make a check",
        re.compile(
            r"\b(?:make|attempt|give\s+me)\s+(?:a|an)\s+(?:[a-z]+\s+){0,2}"
            r"(?:check|save)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "a difficulty number",
        re.compile(r"\b(?:DC\s*\d+|difficulty\s+class|target\s+number\s+\d+)\b", re.IGNORECASE),
    ),
    (
        "a numeric defence statistic",
        re.compile(r"\b(?:armou?r\s+class|AC\s*\d+)\b", re.IGNORECASE),
    ),
    (
        "numeric health",
        re.compile(r"\b(?:hit\s+points?|health\s+points?|HP)\b", re.IGNORECASE),
    ),
    (
        "numeric damage",
        re.compile(
            r"\b\d+\s+(?:points?\s+of\s+)?(?:[a-z]+\s+)?damage\b", re.IGNORECASE,
        ),
    ),
    (
        "turn order",
        re.compile(
            r"\b(?:initiative\s+(?:order|roll|count|score)|roll(?:s|ed)?\s+initiative"
            r"|combat\s+(?:mode|round)s?|round\s+\d+\s+of\s+combat)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "an action economy term",
        re.compile(
            r"\b(?:attack\s+rolls?|to-hit|bonus\s+actions?|spell\s+slots?"
            r"|experience\s+points|\d+\s*XP|level(?:s|ed)?\s+up)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "a mechanical advantage",
        re.compile(
            r"\b(?:with|at|has|have|gains?|grants?|roll\s+with)\s+(?:dis)?advantage\b"
            r"(?!\s+(?:of|over|to|in)\b)",
            re.IGNORECASE,
        ),
    ),
)

# Out-of-character notes may truthfully explain that there are no rolls or checks.
_NEGATION = re.compile(r"\b(?:no|not|never|without|nor|instead\s+of)\b|n't\b", re.IGNORECASE)


def is_ruleless_play(snapshot: CampaignState) -> bool:
    """Return whether *snapshot* is an admitted No Ruleset campaign under DM authority."""

    return snapshot.writer is WriterRole.DM and not snapshot.package_lock.entries


def ruleless_gm_guide(snapshot: CampaignState) -> str | None:
    """Return the application guide for a No Ruleset DM campaign, otherwise ``None``."""

    return RULELESS_GM_GUIDE if is_ruleless_play(snapshot) else None


def _block_text(block: Mapping[str, object]) -> str:
    for key in ("text", "spoken_words"):
        value = block.get(key)
        if isinstance(value, str):
            return value
    return ""


def _findings_in_text(text: str, *, allow_negated: bool) -> list[str]:
    found: list[str] = []
    for label, pattern in _MECHANICS_PATTERNS:
        for match in pattern.finditer(text):
            if allow_negated and _NEGATION.search(text[max(0, match.start() - 32):match.start()]):
                continue
            found.append(label)
            break
    return found


def mechanics_in_response(
    blocks: Sequence[Mapping[str, object]] | None,
    text: str | None = None,
) -> tuple[str, ...]:
    """Name each mechanical idiom found in a held response, in a stable order.

    *blocks* are the held response blocks; *text* is plain held narration used when no
    blocks exist. Narration and character speech are checked strictly. An ``ooc`` block
    may say that rolls or checks are not used, so a phrase directly after a negation is
    ignored there.
    """

    found: list[str] = []
    if blocks is not None:
        for block in blocks:
            found.extend(
                _findings_in_text(_block_text(block), allow_negated=block.get("kind") == "ooc")
            )
    elif text:
        found.extend(_findings_in_text(text, allow_negated=False))
    order = {label: index for index, (label, _pattern) in enumerate(_MECHANICS_PATTERNS)}
    return tuple(sorted(set(found), key=order.__getitem__))


def ruleless_mechanics_rejection(findings: Sequence[str]) -> dict[str, object]:
    """Build the provider-facing correction for a response that used mechanics."""

    return {
        "status": "rejected",
        "code": RULELESS_MECHANICS_REJECTION_CODE,
        "field_paths": ["revised_player_facing_response"],
        "findings": list(findings),
        "state_changed": False,
        "instruction": RULELESS_MECHANICS_INSTRUCTION.format(findings=", ".join(findings)),
    }
