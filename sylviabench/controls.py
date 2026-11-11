"""Prove every checker on a deliberate mutation of an actual scripted good observation."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .checks import evaluate
from .contracts import Result, Scenario, digest
from .fixtures import SECRET, SRD


def verify_controls(rows: list[Result], suite: list[Scenario]) -> list[dict[str, Any]]:
    """These are checker controls, never additional owner/model gameplay results."""
    controls = []
    for scenario in suite:
        good = next(
            r
            for r in rows
            if r.scenario_id == scenario.id
            and r.setup.kind == "scripted"
            and r.setup.behavior == "good"
        )
        if good.outcome != "pass":
            raise ValueError("Checker controls require an actual passing scripted owner run")
        cases = [(c, c) for c in scenario.checks]
        if scenario.id == "T1":
            cases.append(("requested_cast_committed", "wrong_target"))
        for check, mutation in cases:
            data = deepcopy(good.observations)
            actor = data["before"]["player_id"]
            if mutation in {"requested_cast_committed", "wrong_target"}:
                cast = next(
                    e
                    for e in data["mechanics"]
                    if e.get("tool_name", "").endswith("cast_spell_outside_combat")
                )
                field, value = (
                    ("target", actor) if mutation == "wrong_target" else ("spell", "healing_word")
                )
                cast["parameters"][field] = value
            elif mutation == "slot_spent":
                data["after"]["state"][SRD + ".entities"][actor]["resources"]["spell_slot_1"] += 1
            elif mutation == "healing_matches_receipt":
                data["after"]["state"][SRD + ".entities"]["courier"]["hp"] += 1
            elif mutation == "narrated_healing":
                amount = next(
                    c.evidence["restored"] for c in good.checks if c.id == "healing_matches_receipt"
                )
                data["after"]["talks"][-1] = f"The spell restores {amount + 1} HP."
            elif mutation == "secret_not_narrated":
                data["after"]["talks"][-1] = SECRET
            elif mutation == "secret_not_revealed":
                for fact in data["after"]["facts"]:
                    if fact.get("text") == SECRET:
                        fact["visibility"] = "player_known"
                        fact["known_by"] = ["player"]
            elif mutation == "saved_fact_survives":
                data["reopened"]["facts"] = []
            elif mutation == "answer_recalls_fact":
                data["after"]["talks"][-1] = "Use a red flag at the east gate."
            elif mutation == "compaction_exercised":
                data["reopened"]["compaction_count"] = data["saved"].get("compaction_count", 0)
            else:
                raise ValueError(f"No broken control for {check}")
            result = next(c for c in evaluate(scenario, data) if c.id == check)
            if result.status != "fail":
                raise ValueError(f"Broken control did not fail {scenario.id}/{check}/{mutation}")
            controls.append(
                {
                    "scenario_id": scenario.id,
                    "scenario_version": scenario.version,
                    "check": check,
                    "mutation": mutation,
                    "status": result.status,
                    "source_run_id": good.run_id,
                    "mutated_observations_sha256": digest(data),
                    "evidence_kind": "checker_mutation_of_scripted_owner_observation",
                }
            )
    return controls
