"""Expose read-only Plan draft validation through an independently bound owner callback."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sylvia_gm.domain.authority import SafePayload
from sylvia_gm.domain.errors import AuthorityError
from sylvia_gm.tools._spec import ToolSpec
from sylvia_gm.tools._toolsets import ToolsetName


def plan_draft_check_tool(checker: Callable[[], SafePayload]) -> ToolSpec:
    def check(params: dict[str, Any], _context: Any) -> dict[str, Any]:
        if params:
            return {"success": False, "code": "builder_tool_parameters_invalid"}
        try:
            result = checker()
        except (AuthorityError, ValueError) as error:
            return {
                "success": False,
                "status": "rejected",
                "state_changed": False,
                "code": getattr(error, "code", "campaign_plan_invalid"),
                "message": str(error),
                "validation_errors": [
                    item.as_payload() for item in getattr(error, "diagnostics", ())[:16]
                ],
            }
        return {"success": True, "status": "valid_draft", **dict(result)}

    return ToolSpec.from_openai_schema(
        {
            "type": "function",
            "function": {
                "name": "check_campaign_plan_draft",
                "description": (
                    "Check campaign-plan-draft with publication's Plan, package and runtime "
                    "validators. Includes local replacement requirements. "
                    "Reports required-character readiness separately: a valid draft can still need "
                    "Character Files completed. Preserves all draft files and does not install "
                    "packages, publish a Plan, rename a session or change a campaign."
                ),
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        },
        handler=check,
        toolsets=frozenset({ToolsetName.BUILDER_TOOLS}),
    )
