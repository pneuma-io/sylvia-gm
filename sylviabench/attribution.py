"""Reviewed failure causes, kept beside a batch without rewriting its results."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import model_validator

from .contracts import FailureCause, Record, Result, read_json, write_json


class Attribution(Record):
    schema_version: Literal[1] = 1
    run_id: str
    suite_id: str
    result_sha256: str
    cause: FailureCause
    evidence: str
    reviewer: str
    decided_at: str

    @model_validator(mode="after")
    def accountable(self) -> Attribution:
        if not self.evidence.strip() or not self.reviewer.strip():
            raise ValueError("An attribution needs its evidence and a named reviewer")
        if self.cause == "scripted_driver":
            raise ValueError("Scripted causes belong to the framework self-test only")
        return self


def result_hashes(directory: Path) -> dict[str, str]:
    """Identify each recorded run by the exact bytes of its result file."""
    hashes = {}
    for path in sorted((directory / "results").glob("*.json")):
        hashes[str(read_json(path)["run_id"])] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def _check(decision: Attribution, row: Result) -> None:
    """A review can explain a recorded failure; it cannot create or erase one."""
    if row.setup.kind != "model" or row.outcome == "pass":
        raise ValueError("Only a model run that did not pass can be attributed")
    if (
        decision.cause == "gm_behavior"
        and row.outcome != "fail"
        and "stuck_turn" not in row.session_breakers
    ):
        raise ValueError("GM behaviour needs a failed check or a stuck turn in the record")


def load_attributions(directory: Path, rows: list[Result]) -> dict[str, Attribution]:
    """Accept only decisions that still describe the exact recorded run."""
    folder = directory / "attributions"
    if not folder.is_dir():
        return {}
    by_run = {row.run_id: row for row in rows}
    hashes = result_hashes(directory)
    decisions = {}
    for path in sorted(folder.glob("*.json")):
        decision = Attribution.model_validate(read_json(path))
        row = by_run.get(decision.run_id)
        if (
            row is None
            or path.stem != decision.run_id
            or decision.suite_id != row.suite_id
            or decision.result_sha256 != hashes.get(decision.run_id)
        ):
            raise ValueError(f"Attribution does not match a recorded run: {path.name}")
        _check(decision, row)
        decisions[decision.run_id] = decision
    return decisions


def pending(rows: list[Result], decisions: dict[str, Attribution]) -> list[dict[str, Any]]:
    """List the model runs whose recorded cause still keeps a category untested."""
    return [
        {
            "run_id": row.run_id,
            "setup": row.setup.id,
            "scenario": row.scenario_id,
            "repetition": row.repetition,
            "outcome": row.outcome,
            "recorded_cause": row.failure_cause,
            "failed_checks": [c.id for c in row.checks if c.status == "fail"],
            "session_breakers": row.session_breakers,
        }
        for row in rows
        if row.setup.kind == "model" and row.outcome != "pass" and row.run_id not in decisions
    ]


def attribute(
    directory: Path, rows: list[Result], run_id: str, cause: str, evidence: str, reviewer: str
) -> Path:
    """Record one reviewed cause. An existing decision is never silently replaced."""
    row = next((r for r in rows if r.run_id == run_id), None)
    if row is None:
        raise ValueError("Choose a run ID recorded in this batch")
    path = directory / "attributions" / (run_id + ".json")
    if path.exists():
        raise ValueError("This run already has an attribution; move it aside to decide again")
    decision = Attribution.model_validate(
        {
            "run_id": run_id,
            "suite_id": row.suite_id,
            "result_sha256": result_hashes(directory)[run_id],
            "cause": cause,
            "evidence": evidence,
            "reviewer": reviewer,
            "decided_at": datetime.now(UTC).isoformat(),
        }
    )
    _check(decision, row)
    write_json(path, decision.model_dump())
    return path
