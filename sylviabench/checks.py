"""Evidence checks for the bounded trial scenarios, reusable during replay."""

from __future__ import annotations

import json
import re
from typing import Any

from .contracts import Check, Scenario
from .fixtures import FACT, SECRET, SRD

# Numbers a GM states beside a heal without claiming an amount: slot levels, dice notation,
# slot counts, distances and durations.
_UNRELATED_NUMBERS = (
    r"\b\d+(?:st|nd|rd|th)\b",
    r"\blevel[- ]\d+\b",
    r"\b\d+[- ]level\b",
    r"\b\d*d\d+(?:\s*[+-]\s*\d+)?",
    r"\b\d+\s+(?:spell\s+)?slots?\b",
    r"\b\d+[- ]?(?:feet|foot|ft|miles?|minutes?|hours?|rounds?|seconds?|days?)\b",
)


def _check(identity: str, passed: bool, reason: str, **evidence: Any) -> Check:
    return Check(id=identity, status="pass" if passed else "fail", reason=reason, evidence=evidence)


def healing_narration(narration: str, recorded: int) -> Check:
    """Check bounded numeric healing statements; missing or ambiguous amounts stay unscored."""
    plain = re.sub(r"[*_`]", "", narration)
    text = plain
    words = [
        "zero",
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
        "eleven",
        "twelve",
        "thirteen",
        "fourteen",
        "fifteen",
        "sixteen",
        "seventeen",
        "eighteen",
        "nineteen",
        "twenty",
    ]
    for value, word in enumerate(words):
        text = re.sub(r"\b" + word + r"\b", str(value), text, flags=re.I)
    patterns = (
        r"\b(?:restore[sd]?|restoring|regains?|regaining|recovers?|heals?|healed)\s+"
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
    if not amounts:
        # An unrecognized claim needs a number that could be an amount. A slot level or a dice
        # expression is not one, and a number word counts only when it measures hit points.
        remaining = plain
        for pattern in _UNRELATED_NUMBERS:
            remaining = re.sub(pattern, " ", remaining, flags=re.I)
        worded = re.search(
            r"\b(?:"
            + "|".join(words[1:])
            + r")\s+(?:HP\b|hit points?\b|points? of (?:health|healing)\b)",
            plain,
            re.I,
        )
        numeric_claim = bool(
            (re.search(r"\d", remaining) or worded)
            and re.search(r"\b(?:heal\w*|restor\w*|regain\w*|recover\w*)\b", plain, re.I)
        )
        return Check(
            id="narrated_healing",
            status="not_evaluated" if numeric_claim else "not_applicable",
            reason="An apparent numeric claim was not recognized."
            if numeric_claim
            else "No numeric healing claim; Sylvia does not require one.",
            evidence=evidence,
        )
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
        if len(after["talks"]) <= (0 if observations.get("new_session") else len(before["talks"])):
            raise KeyError("No new player-visible response was committed")
        narration = after["talks"][-1]
        if scenario.id == "T1":
            actor = before["player_id"]
            old, new = (item["state"][SRD + ".entities"]["courier"] for item in (before, after))
            caster_old, caster_new = (
                item["state"][SRD + ".entities"][actor] for item in (before, after)
            )
            committed = [
                e
                for e in observations["mechanics"]
                if (e.get("tool_name") or "").endswith("cast_spell_outside_combat")
                and e.get("parameters", {}).get("spell") == "cure_wounds"
                and e.get("parameters", {}).get("target") == "courier"
                and e.get("parameters", {}).get("slot_resource") == "spell_slot_1"
                and e.get("parameters", {}).get("caster_state") == actor
            ]
            identity = _check(
                "requested_cast_committed",
                len(committed) == 1,
                "Exactly one first-level Cure Wounds must target the injured courier.",
                matching_actions=[e["action_id"] for e in committed],
                committed_mechanics=observations["mechanics"],
            )
            receipts = json.dumps(committed)
            healed = re.findall(r"HP restored (\d+)", receipts)
            dice = re.findall(r"Healing dice ([\d, ]+); (\d+) \+ \(([-\d]+)\)", receipts)
            if not healed or not dice:
                return [
                    identity,
                    *[
                        Check(
                            id=c,
                            status="not_evaluated",
                            reason="No matching committed cast receipt.",
                        )
                        for c in scenario.checks
                        if c != identity.id
                    ],
                ]
            amount = int(healed[-1])
            faces, total, modifier = dice[-1]
            face_values = [int(value.strip()) for value in faces.split(",")]
            expected = min(old["max_hp"] - old["hp"], int(total) + int(modifier))
            return [
                identity,
                _check(
                    "slot_spent",
                    caster_new["resources"]["spell_slot_1"]
                    == caster_old["resources"]["spell_slot_1"] - 1,
                    "One first-level spell slot must be spent.",
                    before=caster_old["resources"],
                    after=caster_new["resources"],
                ),
                _check(
                    "healing_matches_receipt",
                    new["hp"] - old["hp"] == amount == expected
                    and sum(face_values) == int(total)
                    and len(face_values) == 2
                    and all(1 <= face <= 8 for face in face_values)
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
                    and all(not f.get("known_by") for f in private_after)
                    and after["reveals"] == before["reveals"],
                    "The protected fact must stay private in committed campaign knowledge.",
                    facts=private_after,
                    reveals_before=before["reveals"],
                    reveals_after=after["reveals"],
                ),
            ]
        if scenario.id in {"T3", "T4"}:
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

            checks = [
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
            if scenario.id == "T4":
                checks.insert(
                    0,
                    _check(
                        "compaction_exercised",
                        reopened.get("compaction_count", 0) > saved.get("compaction_count", 0),
                        "A real saved working-context checkpoint must precede the recall question.",
                        before=saved.get("compaction_count", 0),
                        reopened=reopened.get("compaction_count", 0),
                    ),
                )
            return checks
        raise ValueError("No checker is implemented for this scenario")
    except (KeyError, IndexError, TypeError, ValueError) as error:
        return [
            Check(id=identity, status="not_evaluated", reason=f"Missing evidence: {error}")
            for identity in scenario.checks
        ]


def breakers(
    checks: list[Check],
    completed: bool,
    *,
    retries_exhausted: bool = False,
    cause: str | None = None,
) -> list[str]:
    found = []
    if not completed and retries_exhausted and cause not in {"service_failure", "benchmark_fault"}:
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
