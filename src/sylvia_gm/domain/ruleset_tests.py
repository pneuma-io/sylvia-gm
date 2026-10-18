"""Validate saved scenario data before any disposable campaign is created."""

from __future__ import annotations

import json
import math
from typing import Any, cast

from sylvia_gm.domain._content_schema_ruleset_tests import RULESET_TESTS_PATH
from sylvia_gm.domain.content_validation import ContentShapeError, validate_content_shape
from sylvia_gm.domain.errors import RulesetDiagnostic


def _invalid(path: str, message: str) -> ContentShapeError:
    """Report an authored field without echoing arbitrary submitted content."""
    return ContentShapeError(
        (
            RulesetDiagnostic(
                code="ruleset_tests_invalid",
                phase="source",
                message=message,
                file=RULESET_TESTS_PATH,
                path=path,
            ),
        )
    )


def validate_ruleset_tests(value: object) -> dict[str, Any]:
    """Check shape and cross-field identities; execution owns real game validation."""
    pending = [(value, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if depth > 64 or nodes > 250_000:
            raise _invalid("$", "Scenario data exceeds the nesting or size limit.")
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise _invalid("$", "Scenario object keys must be text.")
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
        elif item is None or isinstance(item, (str, bool, int)):
            continue
        elif not isinstance(item, float) or not math.isfinite(item):
            raise _invalid("$", "Scenario values must be finite JSON data.")
    validate_content_shape("ruleset_tests", value, file=RULESET_TESTS_PATH)
    suite = cast(dict[str, Any], value)
    seen: set[str] = set()
    for index, scenario in enumerate(suite["scenarios"]):
        path = f"$.scenarios[{index}]"
        if scenario["id"] in seen:
            raise _invalid(path + ".id", "Scenario identities must be unique.")
        seen.add(scenario["id"])
        states: set[tuple[str | None, str]] = set()
        for offset, state in enumerate(scenario["initial_state"]):
            identity = (state.get("namespace"), state["key"])
            if identity in states:
                raise _invalid(
                    path + f".initial_state[{offset}]", "State identities must be unique."
                )
            states.add(identity)
        participants: set[str] = set()
        for offset, participant in enumerate(scenario["participants"]):
            if participant["id"] in participants:
                raise _invalid(
                    path + f".participants[{offset}].id", "Participant identities must be unique."
                )
            participants.add(participant["id"])
        for offset, roll in enumerate(scenario["rolls"]):
            if roll["value"] > roll["sides"]:
                raise _invalid(path + f".rolls[{offset}].value", "Roll exceeds its die size.")
    return suite


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate JSON keys instead of silently replacing a test expectation."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate scenario field")
        result[key] = value
    return result


def parse_ruleset_tests(data: bytes) -> dict[str, Any]:
    """Read bounded UTF-8 scenario JSON with the same checks used by compact source."""
    if len(data) > 2 * 1024 * 1024:
        raise _invalid("$", "Scenario file exceeds the size limit.")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise _invalid("$", "Scenario file must contain unambiguous UTF-8 JSON.") from error
    return validate_ruleset_tests(value)
