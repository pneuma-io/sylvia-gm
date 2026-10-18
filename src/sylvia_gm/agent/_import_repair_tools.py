"""Check and publish only the repair copy bound to this saved Builder chat."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sylvia_gm.domain.authority import SafePayload
from sylvia_gm.domain.errors import AuthorityError
from sylvia_gm.tools._spec import ToolSpec
from sylvia_gm.tools._toolsets import ToolsetName


def import_repair_tools(
    checker: Callable[[], SafePayload],
    publisher: Callable[[], SafePayload],
) -> tuple[ToolSpec, ...]:
    """Keep publication explicit while both operations use the same repaired-content checks."""

    def tool(name: str, description: str, operation: Callable[[], SafePayload]) -> ToolSpec:
        def run(params: dict[str, Any], _context: Any) -> dict[str, Any]:
            if params:
                return {"success": False, "code": "builder_tool_parameters_invalid"}
            try:
                return {"success": True, **dict(operation())}
            except (AuthorityError, ValueError) as error:
                return {
                    "success": False,
                    "status": "rejected",
                    "code": getattr(error, "code", "import_repair_invalid"),
                    "message": str(error),
                    "validation_errors": [
                        item.as_payload() for item in getattr(error, "diagnostics", ())[:16]
                    ],
                }

        return ToolSpec.from_openai_schema(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                },
            },
            handler=run,
            toolsets=frozenset({ToolsetName.BUILDER_TOOLS}),
        )

    return (
        tool(
            "check_import_repair",
            (
                "Check this chat's saved repair-draft files using normal package, runtime and Plan "
                "admission as applicable. Return correction details without publishing or changing "
                "the files, original import or any campaign."
            ),
            checker,
        ),
        tool(
            "publish_import_repair",
            (
                "Validate this chat's repaired import and save a separate usable library entry. "
                "Reject unfinished or invalid content. Preserve the original imported file and "
                "existing campaign copies. Use after correcting the reported errors."
            ),
            publisher,
        ),
    )
