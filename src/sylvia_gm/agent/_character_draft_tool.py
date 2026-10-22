"""Open an exact saved character as an editable, unpublished workspace draft."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Protocol

from sylvia_gm.builder import _workspace_files
from sylvia_gm.builder._guardrail import GuardrailBlockedError, PathGuardrail
from sylvia_gm.domain.authority import SafePayload
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.tools._spec import ToolSpec
from sylvia_gm.tools._toolsets import ToolsetName


class ParticipantDraftReader(Protocol):
    """Read an exact saved source for the owner-bound destination and role."""

    def __call__(
        self, *, artifact_id: str, expected_content_sha256: str, schema_id: str | None = None,
    ) -> SafePayload: ...


def character_draft_tool(
    read: ParticipantDraftReader, workspace: Path, schema_ids: tuple[str, ...],
) -> ToolSpec:
    """Copy complete source data without overwriting an unobserved local draft."""

    guardrail = PathGuardrail(workspace)
    properties: dict[str, Any] = {
        "artifact_id": {"type": "string"},
        "expected_content_sha256": {"type": "string"},
        "destination_path": {"type": "string"},
        "expected_version": {"type": "string"},
    }
    required = ["artifact_id", "expected_content_sha256", "destination_path"]
    if schema_ids:
        properties["schema_id"] = {"type": "string", "enum": list(schema_ids)}
        if len(schema_ids) > 1:
            required.append("schema_id")

    def open_draft(params: dict[str, Any], _context: Any) -> dict[str, Any]:
        if (
            not set(required) <= set(params)
            or set(params) - set(properties)
            or any(not isinstance(value, str) for value in params.values())
            or re.fullmatch(r"participant-[0-9a-f]{24}-[0-9a-f]{16}", params["artifact_id"]) is None
            or re.fullmatch(r"[0-9a-f]{64}", params["expected_content_sha256"]) is None
            or not params["destination_path"].strip()
            or ("schema_id" in params and params["schema_id"] not in schema_ids)
        ):
            return {"success": False, "code": "character_draft_parameters_invalid"}
        try:
            target = guardrail.validate(params["destination_path"], for_write=True)
            draft = read(
                artifact_id=params["artifact_id"],
                expected_content_sha256=params["expected_content_sha256"],
                schema_id=params.get("schema_id"),
            )
            content = json.dumps(dict(draft), ensure_ascii=False, indent=2) + "\n"
            version = _workspace_files.write_text(target, content, params.get("expected_version"))
        except GuardrailBlockedError:
            return {"success": False, "code": "character_draft_path_denied"}
        except _workspace_files.StaleWorkspaceFileError as error:
            return {"success": False, "error_code": "stale_file", "error": str(error)}
        except CommandRejected as error:
            return {"success": False, "code": error.code, "message": str(error)}
        except (OSError, UnicodeError, TypeError, ValueError):
            return {"success": False, "code": "character_draft_unavailable"}
        return {
            "success": True, "published": False,
            "source_artifact_id": params["artifact_id"],
            "source_content_sha256": params["expected_content_sha256"],
            "path": target.relative_to(guardrail.allowed_root).as_posix(),
            "version": version, "bytes_written": len(content.encode("utf-8")),
        }

    return ToolSpec.from_openai_schema(
        {"type": "function", "function": {
            "name": "open_character_draft",
            "description": (
                "Open a saved character for an explicitly requested repair or adaptation. "
                "Get its exact artifact ID and content_sha256 from discover_campaign_content. "
                "Copies the full character into a new JSON workspace file; originals remain "
                "unchanged. Existing destinations require their observed read_file version. "
                "Use read_file and edit_file to preserve other choices. Calculated state is "
                "omitted for recalculation. This does not validate or save a library character: "
                "check the draft against this chat's selected Setting and Ruleset, then use "
                "check_participant_draft_file and publish_participant_artifact_file with the "
                "draft path and observed version. Inline character tools remain available. "
                "A different Setting may require explicit adaptation; never invent missing choices."
            ),
            "parameters": {"type": "object", "properties": properties,
                           "required": required, "additionalProperties": False},
        }},
        handler=open_draft, toolsets=frozenset({ToolsetName.BUILDER_TOOLS}),
    )
