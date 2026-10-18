"""Builder draft checking through the ordinary package admission validators."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sylvia_gm.application.package_library import validate_admitted_package_runtimes
from sylvia_gm.builder._package_materializer import materialize_ruleset_draft
from sylvia_gm.domain.errors import AuthorityError
from sylvia_gm.infrastructure.package_archive import admit_package_archive
from sylvia_gm.tools._spec import ToolSpec
from sylvia_gm.tools._toolsets import ToolsetName


def ruleset_validation_failure(error: AuthorityError) -> dict[str, Any]:
    """Retain the validator's public correction details for checking and publication alike."""

    return {
        "success": False,
        "status": "rejected",
        "code": getattr(error, "code", "command_rejected"),
        "message": str(error),
        "validation_errors": [item.as_payload() for item in getattr(error, "diagnostics", ())],
    }


def ruleset_draft_check_tool(workspace: Path) -> ToolSpec:
    """Check the fixed Ruleset draft without exporting, installing, or modifying its files."""

    def check(params: dict[str, Any], _context: Any) -> dict[str, Any]:
        if params:
            return {
                "success": False,
                "status": "rejected",
                "code": "builder_tool_parameters_invalid",
                "message": "check_ruleset_draft takes no arguments.",
            }
        try:
            with materialize_ruleset_draft(workspace) as materialized:
                with admit_package_archive(materialized.archive) as admitted:
                    validate_admitted_package_runtimes(admitted)
                return {
                    "success": True,
                    "status": "valid_draft",
                    "closure_sha256": materialized.closure_sha256,
                    "checked": ["source", "package", "references", "runtime"],
                    "published": False,
                    "message": (
                        "The draft passes package and runtime admission. It remains in the "
                        "workspace. This check does not test gameplay sequences."
                    ),
                }
        except AuthorityError as error:
            return ruleset_validation_failure(error)

    return ToolSpec.from_openai_schema(
        {
            "type": "function",
            "function": {
                "name": "check_ruleset_draft",
                "description": (
                    "Check the current ruleset-draft source, package, references and runtime "
                    "with the same validators used by installation. Returns correction details "
                    "for unfinished or invalid drafts. Preserves files and does not publish, "
                    "install or change any campaign. Does not test mechanics sequences."
                ),
                "parameters": {
                    "type": "object", "properties": {}, "additionalProperties": False,
                },
            },
        },
        handler=check,
        toolsets=frozenset({ToolsetName.BUILDER_TOOLS}),
    )
