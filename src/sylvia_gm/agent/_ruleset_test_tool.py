"""Expose saved Ruleset scenarios to Builder without granting general code execution."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from sylvia_gm.agent._ruleset_draft_check_tool import ruleset_validation_failure
from sylvia_gm.application.ruleset_testing import run_ruleset_tests
from sylvia_gm.builder._package_materializer import materialize_ruleset_draft
from sylvia_gm.domain.errors import AuthorityError
from sylvia_gm.tools._spec import ToolSpec
from sylvia_gm.tools._toolsets import ToolsetName


def ruleset_test_tool(workspace: Path, *, cancelled: Callable[[], bool]) -> ToolSpec:
    """Run the current draft's saved cases; edits require a fresh run of the new archive."""

    def run(params: dict[str, Any], _context: Any) -> dict[str, Any]:
        if params:
            return {
                "success": False,
                "status": "rejected",
                "code": "builder_tool_parameters_invalid",
                "message": "test_ruleset_draft takes no arguments.",
            }
        try:
            with materialize_ruleset_draft(workspace) as materialized:
                result = run_ruleset_tests(materialized.archive, cancelled=cancelled)
        except AuthorityError as error:
            return ruleset_validation_failure(error)
        scenarios = result.get("scenarios", [])
        passed = sum(item["status"] == "passed" for item in scenarios)
        return {
            **result,
            "success": result["status"] == "passed",
            "published": False,
            "summary": {
                "passed": passed,
                "total": len(scenarios),
                "failed": len(scenarios) - passed,
            },
            "message": (
                "Saved scenarios ran through the campaign loop in disposable state. "
                "Inspect failures and coverage before declaring the Ruleset ready. "
                "The draft remains editable; no library or campaign was changed."
            ),
        }

    return ToolSpec.from_openai_schema(
        {
            "type": "function",
            "function": {
                "name": "test_ruleset_draft",
                "description": (
                    "Run the current ruleset-draft's saved ruleset_tests scenarios through "
                    "the real campaign loop with scripted GM choices and dice. Returns "
                    "per-step expected/actual failures. Preserves drafts, uses isolated "
                    "temporary state, and never calls an external model. Retrieve the "
                    "ruleset_tests content definition for authoring instructions."
                ),
                "parameters": {
                    "type": "object", "properties": {}, "additionalProperties": False,
                },
            },
        },
        handler=run,
        toolsets=frozenset({ToolsetName.BUILDER_TOOLS}),
    )
