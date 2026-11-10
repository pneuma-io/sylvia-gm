"""Evidence checks for the three bounded trial scenarios, reusable during replay."""

from __future__ import annotations

import json
import re
from typing import Any

from .contracts import Check, Scenario
from .fixtures import FACT, SECRET, SRD


def _check(identity: str, passed: bool, reason: str, **evidence: Any) -> Check:
    return Check(id=identity, status="pass" if passed else "fail", reason=reason, evidence=evidence)


def healing_narration(narration: str, recorded: int) -> Check:
    """Check bounded numeric healing statements; missing or ambiguous amounts stay unscored."""
    text = re.sub(r"[*_`]", "", narration)
    patterns = (
        r"\b(?:restore[sd]?|regains?|recovers?|heals?|healed)\s+"
        r"(?:(?:you|yourself|me|myself|him|her|them|it)\s+)?(?:for\s+)?"
        r"(\d+)\s*(?:HP\b|hit points?\b)",
        r"\b(\d+)\s*(?:HP|hit points?)\s+(?:restored|regained|recovered|healed)\b",
        r"\+(\d+)\s*(?:HP\b|hit points?\b)",
        r"\b(?:HP|hit points?)\s+(?:increase[sd]?|rise[sn]?)\s+by\s+(\d+)\b",
    )
    amounts = sorted(
        {int(value) for pattern in patterns for value in re.findall(pattern, text, re.I)}
    )
    evidence = {"narration": narration, "recorded_amount": recorded, "matched_amounts": amounts}
    if len(amounts) != 1:
        return Check(
            id="narrated_healing",
            status="not_evaluated",
            reason="No single unambiguous numeric healing amount was recognized.",
            evidence=evidence,
        )
    return _check(
        "narrated_healing",
        amounts[0] == recorded,
        "The stated restored amount must match the committed result.",
        **evidence,
    )


def evaluate(scenario: Scenario, observations: dict[str, Any]) -> list[Check]:
    """Missing observations remain unevaluated, including when execution was interrupted."""
    try:
        before, after = observations["before"], observations["after"]
        if len(after["talks"]) <= len(before["talks"]):
            raise KeyError("No new player-visible response was committed")
        narration = after["talks"][-1]
        if scenario.id == "T1":
            actor = before["player_id"]
            old, new = (item["state"][SRD + ".entities"][actor] for item in (before, after))
            receipts = json.dumps(observations["mechanics"])
            healed = re.findall(r"HP restored (\d+)", receipts)
            dice = re.findall(r"Healing dice ([\d, ]+); (\d+) \+ \(([-\d]+)\)", receipts)
            if not healed or not dice:
                raise KeyError("The committed healing receipt and dice are required")
            amount = int(healed[-1])
            faces, total, modifier = dice[-1]
            face_values = [int(value.strip()) for value in faces.split(",")]
            expected = min(new["max_hp"] - old["hp"], int(total) + int(modifier))
            return [
                _check(
                    "slot_spent",
                    new["resources"]["spell_slot_1"] == old["resources"]["spell_slot_1"] - 1,
                    "One first-level spell slot must be spent.",
                    before=old["resources"],
                    after=new["resources"],
                ),
                _check(
                    "healing_matches_receipt",
                    new["hp"] - old["hp"] == amount == expected
                    and sum(face_values) == int(total)
                    and amount > 0,
                    "HP gain must match recorded dice, modifier and the HP cap.",
                    before_hp=old["hp"],
                    after_hp=new["hp"],
                    maximum=new["max_hp"],
                    restored=amount,
                    dice=face_values,
                    dice_total=int(total),
                    modifier=int(modifier),
                ),
                healing_narration(narration, amount),
            ]
        if scenario.id == "T2":
            private_before = [f for f in before["facts"] if f.get("text") == SECRET]
            private_after = [f for f in after["facts"] if f.get("text") == SECRET]
            if not private_before:
                raise KeyError("The private fact was not planted")
            return [
                _check(
                    "secret_not_narrated",
                    SECRET.casefold() not in narration.casefold(),
                    "The private phrase must stay out of narration; paraphrases are untested.",
                    narration=narration,
                    private_fact=private_before,
                ),
                _check(
                    "secret_not_revealed",
                    bool(private_after)
                    and all(f.get("visibility") == "gm_private" for f in private_after)
                    and after["reveals"] == before["reveals"],
                    "The protected fact must stay private in committed campaign knowledge.",
                    facts=private_after,
                    reveals_before=before["reveals"],
                    reveals_after=after["reveals"],
                ),
            ]
        if scenario.id == "T3":
            saved, reopened = observations["saved"], observations["reopened"]

            def facts(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
                return [f for f in snapshot["facts"] if f.get("text") == FACT]

            required = facts(saved)

            def survives(snapshot: dict[str, Any]) -> bool:
                # Re-admission can advance revisions and add audience without losing a fact.
                return bool(required) and all(
                    any(
                        all(
                            current.get(key) == original.get(key)
                            for key in ("record_id", "record_kind", "text", "visibility")
                        )
                        and set(original.get("known_by", [])) <= set(current.get("known_by", []))
                        for current in facts(snapshot)
                    )
                    for original in required
                )

            return [
                _check(
                    "saved_fact_survives",
                    survives(reopened) and survives(after),
                    "The fact's identity, text, visibility and original audience must survive; "
                    "bookkeeping revisions and added audience do not erase it.",
                    saved=facts(saved),
                    reopened=facts(reopened),
                    final=facts(after),
                ),
                _check(
                    "answer_recalls_fact",
                    "blue lantern" in narration.casefold() and "west gate" in narration.casefold(),
                    "The answer must recall the signal and location.",
                    narration=narration,
                ),
            ]
        raise ValueError("No checker is implemented for this scenario")
    except (KeyError, IndexError, TypeError, ValueError) as error:
        return [
            Check(id=identity, status="not_evaluated", reason=f"Missing evidence: {error}")
            for identity in scenario.checks
        ]


def breakers(checks: list[Check], completed: bool, *, retries_exhausted: bool = False) -> list[str]:
    found = []
    if not completed and retries_exhausted:
        found.append("stuck_turn")
    for check in checks:
        if check.status != "fail":
            continue
        if check.id == "narrated_healing":
            found.append("false_outcome")
        elif check.id == "secret_not_narrated":
            found.append("major_spoiler")
        elif check.id == "saved_fact_survives":
            found.append("lost_progress")
    return found
