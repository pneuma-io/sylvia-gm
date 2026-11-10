"""Versioned benchmark records, validated without constructing any provider."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Category = Literal["rules", "memory", "fair_play", "story"]
Status = Literal["pass", "fail", "not_evaluated"]
Outcome = Literal["pass", "fail", "incomplete", "error"]
FailureCause = Literal[
    "gm_behavior", "scripted_driver", "sylvia_bug", "service_failure", "benchmark_fault", "unknown"
]
WEIGHTS: dict[str, int] = {"rules": 30, "memory": 25, "fair_play": 25, "story": 20}
ROOT = Path(__file__).resolve().parents[1]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Setup(Record):
    id: str
    name: str
    kind: Literal["scripted", "model"] = "scripted"
    provider_id: str = "sylviabench.scripted"
    model_id: str = "sylviabench.scripted/good"
    reasoning: str | None = None
    behavior: Literal["good", "bad", "recover", "stuck"] = "good"
    owner_config: str | None = None

    @field_validator("id")
    @classmethod
    def safe_id(cls, value: str) -> str:
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", value):
            raise ValueError("Use a simple setup ID, without paths")
        return value

    @model_validator(mode="after")
    def explicit_route(self) -> Setup:
        if not self.name.strip() or not self.provider_id.strip() or not self.model_id.strip():
            raise ValueError("Setup name and exact provider/model IDs are required")
        if self.kind == "model" and not self.owner_config:
            raise ValueError("A model setup needs an explicitly selected owner_config path")
        if self.kind == "scripted" and self.owner_config is not None:
            raise ValueError("Scripted execution cannot read provider settings")
        return self


class SetupFile(Record):
    schema_version: Literal[1] = 1
    setups: list[Setup]

    @model_validator(mode="after")
    def distinct(self) -> SetupFile:
        if not self.setups or len({item.id for item in self.setups}) != len(self.setups):
            raise ValueError("Provide at least one setup, with unique IDs")
        return self


class Scenario(Record):
    id: str
    version: int = Field(ge=1)
    category: Category
    title: str
    player_messages: list[str] = Field(min_length=1)
    checks: list[str] = Field(min_length=1)
    expected_bad_check: str
    description: str

    @field_validator("id")
    @classmethod
    def safe_id(cls, value: str) -> str:
        return Setup.safe_id(value)

    @model_validator(mode="after")
    def valid_checks(self) -> Scenario:
        if len(self.checks) != len(set(self.checks)) or self.expected_bad_check not in self.checks:
            raise ValueError("Checks must be unique and include the known-bad failure")
        return self


class Check(Record):
    id: str
    status: Status
    reason: str
    evidence: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def evidence_required(self) -> Check:
        if self.status != "not_evaluated" and not self.evidence:
            raise ValueError("Evaluated checks require evidence")
        return self


class Attempt(Record):
    turn: int = Field(ge=0)
    number: int = Field(ge=0, le=2)
    elapsed_seconds: float = Field(ge=0)
    completed: bool
    interruption: str | None = None


class Result(Record):
    schema_version: Literal[1] = 1
    run_id: str
    suite_id: str
    source: dict[str, Any]
    scenario_id: str
    scenario_version: int = Field(ge=1)
    scenario_sha256: str
    fixture_sha256: str
    category: Category
    repetition: int = Field(ge=0)
    execution_kind: Literal["framework_selftest", "model_run"]
    setup: Setup
    observed_model: str | None = None
    observed_reasoning: str | None = None
    identity_note: str = "The owner selection is a request, not provider confirmation."
    started_at: str
    elapsed_seconds: float = Field(ge=0)
    attempts: list[Attempt]
    checks: list[Check]
    outcome: Outcome
    failure_cause: FailureCause | None = None
    failure_evidence: str | None = None
    session_breakers: list[str] = Field(default_factory=list)
    observations: dict[str, Any] = Field(default_factory=dict)
    usage: dict[str, Any] | None = None
    usage_note: str = "Usage and cost were not reported; no estimate is substituted."

    @model_validator(mode="after")
    def honest_outcome(self) -> Result:
        if not self.checks or len({c.id for c in self.checks}) != len(self.checks):
            raise ValueError("A result needs unique checks")
        expected_kind = "framework_selftest" if self.setup.kind == "scripted" else "model_run"
        if self.execution_kind != expected_kind:
            raise ValueError("Scripted and model executions cannot be relabelled")
        expected: dict[int, int] = {}
        finished: set[int] = set()
        for attempt in self.attempts:
            if attempt.turn in finished or attempt.number != expected.get(attempt.turn, 0):
                raise ValueError(
                    "Attempts must preserve consecutive first-attempt and retry records"
                )
            expected[attempt.turn] = attempt.number + 1
            if attempt.completed:
                finished.add(attempt.turn)
        if self.outcome == "pass" and (
            any(c.status != "pass" for c in self.checks)
            or not self.attempts
            or any(not a.completed for a in self._final_attempts())
        ):
            raise ValueError("A pass needs every check and final turn attempt to pass")
        if self.failure_cause is not None and not self.failure_evidence:
            raise ValueError("Failure attribution needs supporting evidence")
        return self

    def _final_attempts(self) -> list[Attempt]:
        return list({a.turn: a for a in self.attempts}.values())


def digest(value: Any) -> str:
    """Hash canonical JSON so resume compares values, not whitespace."""
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


def read_json(path: Path) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in items:
            if key in value:
                raise ValueError(f"Duplicate JSON key: {key}")
            value[key] = item
        return value

    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs)


def write_json(path: Path, value: Any) -> None:
    """Replace a single owned record atomically; callers own the output directory lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def scenarios() -> list[Scenario]:
    rows = read_json(Path(__file__).with_name("scenarios.json"))
    records = [Scenario.model_validate(item) for item in rows]
    if len({s.id for s in records}) != len(records):
        raise ValueError("Duplicate scenario IDs")
    return records


def setups(path: Path | None = None) -> list[Setup]:
    if path:
        return SetupFile.model_validate(read_json(path)).setups
    return [
        Setup(
            id=behavior,
            name="Scripted " + behavior,
            behavior=behavior,
            model_id="sylviabench.scripted/" + behavior,
        )
        for behavior in ("good", "bad", "recover", "stuck")
    ]
