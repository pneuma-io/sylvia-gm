"""Execute one saved scenario in a disposable worker, using the production campaign loop."""

from __future__ import annotations

import dataclasses
import io
import random
import re
from collections.abc import Mapping
from enum import Enum
from pathlib import Path
from typing import Any

from mcp.shared.exceptions import MCPError

from sylvia_gm import dice
from sylvia_gm.agent._provider_session import ProviderSessionAdapter
from sylvia_gm.agent._ruleset_test_coverage import checked_outcomes
from sylvia_gm.agent._ruleset_test_provider import RulesetTestProvider, ScenarioScriptError
from sylvia_gm.agent._session_loop import CampaignSessionLoop, SessionInputKind, SessionRunRequest
from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.domain.authority import (
    Participant,
    ParticipantValidationStatus,
    StateValue,
    WriterRole,
    owned_payload,
)
from sylvia_gm.domain.errors import AuthorityError


def _failure_message(error: Exception) -> str:
    """Expose bounded nested error types and authority codes, never raw runtime messages."""
    if isinstance(error, ScenarioScriptError):
        return str(error)
    pending = [error]
    details = []
    for _ in range(32):
        if not pending or len(details) >= 4:
            break
        current = pending.pop(0)
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions[:8])
            continue
        detail = type(current).__name__
        code = getattr(current, "code", None) if isinstance(current, AuthorityError) else None
        if isinstance(current, MCPError) and isinstance(current.data, dict):
            code = current.data.get("code")
        if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,79}", code):
            detail += f" ({code})"
        if detail not in details:
            details.append(detail)
    return "; ".join(details) or type(error).__name__


def _plain(value: Any) -> Any:
    """Project result data without exposing provider messages or runtime handles."""
    if isinstance(value, Enum):
        return value.value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _plain(getattr(value, field.name)) for field in dataclasses.fields(value)
        }
    if isinstance(value, Mapping):
        return {key: _plain(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(child) for child in value]
    return value


class ScenarioDice(random.Random):
    """Supply individual die faces to the normal dice helpers inside the isolated worker only."""

    def __init__(self, rolls: list[dict[str, int]]) -> None:
        super().__init__()
        self.rolls = rolls
        self.position = 0
        self.failure: str | None = None

    def randint(self, a: int, b: int) -> int:
        """Translate saved one-based faces for both ordinary dice and zero-based table draws."""
        if self.position >= len(self.rolls):
            self.failure = "The game requested more rolls than the scenario supplies."
            raise ScenarioScriptError(self.failure)
        roll = self.rolls[self.position]
        self.position += 1
        if roll["sides"] != b - a + 1:
            self.failure = "The requested die size differs from the scripted roll."
            raise ScenarioScriptError(self.failure)
        return a + roll["value"] - 1

    def assert_finished(self) -> None:
        if self.failure:
            raise ScenarioScriptError(self.failure)
        if self.position != len(self.rolls):
            raise ScenarioScriptError("The scenario finished with unused scripted rolls.")


def _assertions(
    expected: list[dict[str, Any]], observations: dict[str, Any]
) -> list[dict[str, Any]]:
    """Compare exact saved expectations; missing paths never compare equal to null."""
    failures = []
    for assertion in expected:
        actual = observations[assertion["subject"]]
        missing = False
        for part in assertion["path"]:
            if (
                isinstance(actual, dict)
                and isinstance(part, str)
                and part in actual
                or isinstance(actual, list)
                and type(part) is int
                and 0 <= part < len(actual)
            ):
                actual = actual[part]
            else:
                missing = True
                break
        if (
            missing
            or type(actual) is not type(assertion["equals"])
            or actual != assertion["equals"]
        ):
            failures.append(
                {
                    "subject": assertion["subject"],
                    "path": assertion["path"],
                    "expected": assertion["equals"],
                    "actual": None if missing else actual,
                    "missing": missing,
                }
            )
    return failures


def run_scenario(archive: bytes, scenario: dict[str, Any], directory: Path) -> dict[str, Any]:
    """Worker-only entry; its fresh database and process-local dice cannot touch live campaigns."""
    owner_id, campaign_id, session_id = "scenario-owner", "scenario-campaign", "scenario-session"
    rolls = ScenarioDice(scenario["rolls"])
    previous_rng, previous_seed = dice._rng, dice._current_seed
    dice._rng, dice._current_seed = rolls, None
    steps: list[dict[str, Any]] = []
    try:
        with AuthorityOwner.open(
            directory / "campaign.sqlite3",
            owner_id,
            disconnect_grace_seconds=1.0,
        ) as owner:
            candidate = owner.install_package_archive(io.BytesIO(archive), install_id="scenario")
            primary = next(
                entry for entry in candidate.package_lock.entries if entry.role.value == "primary"
            )
            declarations = owner.exact_participant_declarations(
                primary.package_id,
                primary.version,
                primary.sha256,
            )
            participants = []
            for record in scenario["participants"]:
                matches = [item for item in declarations if item.schema_id == record["schema_id"]]
                if len(matches) != 1:
                    raise ScenarioScriptError(
                        "Participant schema is not declared by the tested Ruleset."
                    )
                participants.append(
                    Participant(
                        participant_id=record["id"],
                        kind=record["kind"],
                        package_id=primary.package_id,
                        package_version=primary.version,
                        package_sha256=primary.sha256,
                        schema_id=record["schema_id"],
                        schema_sha256=matches[0].schema_sha256,
                        validation_status=ParticipantValidationStatus.VALIDATED,
                        data=owned_payload(record["data"]),
                    )
                )
            owner.create_campaign(
                CampaignCreate(
                    campaign_id=campaign_id,
                    display_name="Ruleset test",
                    writer=WriterRole.DM,
                    package_lock=candidate.package_lock,
                    turn_id="scenario-turn-0",
                    participants=tuple(participants),
                    initial_state=tuple(
                        StateValue(
                            item.get("namespace", primary.package_id),
                            item["key"],
                            item["value"],
                        )
                        for item in scenario["initial_state"]
                    ),
                )
            )
            owner.create_agent_session(
                campaign_id=campaign_id,
                role=WriterRole.DM,
                session_id=session_id,
                provider_id="ruleset-test",
                model_id="ruleset-test/scripted",
            )
            loop = CampaignSessionLoop(owner)
            receipt: Any = None
            tool_results: list[dict[str, Any]] = []
            for index, step in enumerate(scenario["steps"]):
                provider = None
                try:
                    if step["kind"] in {"gm_turn", "player_action_response"}:
                        responding = step["kind"] == "player_action_response"
                        player_action_id = (
                            receipt.get("action_id") if responding and receipt else None
                        )
                        if responding and not player_action_id:
                            raise ScenarioScriptError("A response requires a saved player action.")
                        receipt = None
                        tool_results = []
                        turn_id = owner.current_open_turn(campaign_id)
                        if responding:
                            basis = owner.player_action_response_basis(
                                session_id, player_action_id,
                                automatic=step.get("automatic", False),
                            )
                            turn_id = basis["turn_id"]
                        if turn_id is None:
                            turn_id = f"scenario-turn-{index}"
                            owner.open_turn(campaign_id, turn_id)

                        def decision_owner() -> str:
                            boundary = owner.model_decision_boundary(campaign_id)
                            return "none" if boundary is None else boundary.owner.value

                        provider = RulesetTestProvider(
                            step["replies"], decision_owner=decision_owner
                        )
                        adapter = ProviderSessionAdapter(owner, provider, owner_id=owner_id)
                        result = loop.run(
                            SessionRunRequest(
                                session_id=session_id,
                                turn_id=turn_id,
                                attempt_id=f"scenario-attempt-{index}",
                                message_id=f"scenario-message-{index}",
                                user_text="" if responding else step["message"],
                                input_kind=SessionInputKind.PLAYER_ACTION_RESPONSE
                                if responding else SessionInputKind.USER,
                                player_action_id=player_action_id,
                                automatic_response=step.get("automatic", False),
                            ),
                            provider.observe(adapter.for_turn(turn_id)),
                        )
                        provider.assert_finished()
                        tool_results = provider.tool_results
                        if result.interruption is not None:
                            raise ScenarioScriptError("The campaign turn did not finish.")
                        if result.receipts:
                            receipt = _plain(result.receipts[-1])
                    elif step["kind"] == "player_action":
                        tool_results = []
                        receipt = _plain(
                            owner.player_action(
                                campaign_id=campaign_id,
                                source=owned_payload(step["source"]),
                                operation_id=f"scenario-action-{index}",
                                expected_revision=owner.snapshot(campaign_id).revision,
                                selection=owned_payload(step["selection"])
                                if "selection" in step
                                else None,
                                session_id=session_id,
                                confirmation=step.get("confirmation"),
                                preview_only=step.get("preview_only", False),
                            )
                        )
                    boundary = owner.model_decision_boundary(campaign_id)
                    observed = {
                        "state": _plain(owner.snapshot(campaign_id).state),
                        "receipt": receipt,
                        "tool_results": tool_results,
                        "control": _plain(boundary),
                        "available_tools": sorted(tool.name for tool in owner.catalog(campaign_id)),
                    }
                    failures = _assertions(step["expect"], observed)
                    coverage = checked_outcomes(step["expect"], receipt, tool_results)
                    coverage["handoff_phases"] = []
                    if provider:
                        for reply in step["replies"]:
                            request = reply.get("expect_request", {})
                            if request.get("decision_owner") == "gm" and (
                                "update_campaign_file" in request.get("unavailable_tools", [])
                            ):
                                coverage["handoff_phases"].append("held")
                            if request.get("decision_owner") == "player" and (
                                "update_campaign_file" in request.get("available_tools", [])
                            ):
                                coverage["handoff_phases"].append("released")
                    steps.append(
                        {
                            "step": index,
                            "status": "failed" if failures else "passed",
                            "failures": failures,
                            "coverage": coverage
                            if not failures else {"actions": [], "rejections": []},
                        }
                    )
                    if failures:
                        return {"id": scenario["id"], "status": "failed", "steps": steps}
                except Exception as error:
                    message = provider.failure if provider and provider.failure else rolls.failure
                    if message is None:
                        message = _failure_message(error)
                    steps.append(
                        {"step": index, "status": "failed", "failures": [{"message": message}]}
                    )
                    return {"id": scenario["id"], "status": "failed", "steps": steps}
            rolls.assert_finished()
            return {"id": scenario["id"], "status": "passed", "steps": steps}
    except Exception as error:
        message = _failure_message(error)
        return {
            "id": scenario["id"],
            "status": "failed",
            "steps": steps,
            "failures": [{"message": message}],
        }
    finally:
        dice._rng, dice._current_seed = previous_rng, previous_seed
