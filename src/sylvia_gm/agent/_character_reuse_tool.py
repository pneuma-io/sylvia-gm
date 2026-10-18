"""Explicit compatible character copies into a Builder chat's saved Setting."""

from __future__ import annotations

import re
from typing import Any, Protocol

from sylvia_gm.domain.authority import SafePayload
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.tools._spec import ToolSpec
from sylvia_gm.tools._toolsets import ToolsetName


class ParticipantReuser(Protocol):
    """Copy one exact source after the owner validates its unchanged data for this Setting."""

    def __call__(
        self,
        *,
        artifact_id: str,
        expected_content_sha256: str,
        schema_id: str | None = None,
    ) -> SafePayload: ...


def reuse_character_copy_tool(
    reuse: ParticipantReuser,
    schema_ids: tuple[str, ...],
) -> ToolSpec:
    """Offer reuse only on an explicit request, with the destination bound outside model input."""

    properties: dict[str, Any] = {
        "artifact_id": {
            "type": "string",
            "description": "Existing character ID from the local content catalog.",
        },
        "expected_content_sha256": {
            "type": "string",
            "description": "The exact content_sha256 returned for that catalog character.",
        },
    }
    required = ["artifact_id", "expected_content_sha256"]
    if schema_ids:
        properties["schema_id"] = {"type": "string", "enum": list(schema_ids)}
        if len(schema_ids) > 1:
            required.append("schema_id")

    def copy(params: dict[str, Any], _context: Any) -> dict[str, Any]:
        if (
            not set(required) <= set(params)
            or set(params) - set(properties)
            or not isinstance(params.get("artifact_id"), str)
            or re.fullmatch(r"participant-[0-9a-f]{24}-[0-9a-f]{16}", params["artifact_id"]) is None
            or not isinstance(params.get("expected_content_sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", params["expected_content_sha256"]) is None
            or ("schema_id" in params and params["schema_id"] not in schema_ids)
        ):
            return {"success": False, "code": "character_reuse_parameters_invalid"}
        try:
            result = reuse(**params)
        except CommandRejected as error:
            return {
                "success": False,
                "status": "rejected",
                "state_changed": False,
                "code": error.code,
                "message": str(error),
                "validation_errors": [item.as_payload() for item in error.diagnostics[:16]],
            }
        except (OSError, UnicodeError, TypeError, ValueError):
            return {
                "success": False,
                "status": "rejected",
                "state_changed": False,
                "code": "character_reuse_unavailable",
                "message": "Refresh the source character and correct its file before reuse.",
            }
        return {"success": True, "status": "copied", **dict(result)}

    return ToolSpec.from_openai_schema(
        {
            "type": "function",
            "function": {
                "name": "reuse_character_copy",
                "description": (
                    "On the user's explicit request, reuse one saved character unchanged in "
                    "this chat's selected Setting. Call discover_campaign_content with the "
                    "character's name to obtain its ID and content_sha256 from the local catalog; "
                    "chat history is not the saved-character catalog. Checks the new exact "
                    "Setting and contained Ruleset and saves a "
                    "compatible copy only when every requirement already holds. Original files "
                    "and campaign characters remain unchanged. If adaptation is required, explain "
                    "the missing choices and ask before changing them in a new Builder draft. "
                    "Never use this to rebind all characters automatically after a Setting update."
                ),
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
            },
        },
        handler=copy,
        toolsets=frozenset({ToolsetName.BUILDER_TOOLS}),
    )
