"""Validate and publish the exact workspace draft without retransmitting its body."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sylvia_gm.builder._guardrail import GuardrailBlockedError, PathGuardrail
from sylvia_gm.builder._workspace_files import content_version
from sylvia_gm.tools._spec import ToolSpec

_MAX_DRAFT_BYTES = 256 * 1024


def participant_file_tool(
    inline_tool: ToolSpec, workspace: Path, decode: Callable[[object], dict[str, Any]],
) -> ToolSpec:
    """Delegate captured, version-checked bytes to the existing role-bound validator."""
    checking = inline_tool.name == "check_participant_draft"
    name = "check_participant_draft_file" if checking else "publish_participant_artifact_file"
    guardrail = PathGuardrail(workspace)

    def use_file(params: dict[str, Any], context: Any) -> dict[str, Any]:
        rejected = {"success": False, "status": "rejected", "state_changed": False}
        if (
            set(params) != {"path", "expected_version"}
            or not isinstance(params.get("path"), str)
            or not params["path"].strip()
            or not isinstance(params.get("expected_version"), str)
            or re.fullmatch(r"[0-9a-f]{64}", params["expected_version"]) is None
        ):
            return {**rejected, "code": "participant_file_parameters_invalid"}
        try:
            target = guardrail.validate(params["path"])
            # Atomic workspace replacements cannot change this captured file instance.
            with target.open("rb") as source:
                data = source.read(_MAX_DRAFT_BYTES + 1)
            if len(data) > _MAX_DRAFT_BYTES:
                raise ValueError("Draft exceeds 256 KiB")
            version = content_version(data)
            if version != params["expected_version"]:
                return {**rejected, "code": "stale_file", "message": (
                    "Draft changed. Read and reconcile it before checking or publishing again."
                )}
            draft = decode(data.decode("utf-8"))
            for key in ("entity_state", "setting_data"):
                if isinstance(draft.get(key), dict):
                    draft[key] = json.dumps(draft[key], ensure_ascii=False, allow_nan=False)
        except GuardrailBlockedError:
            return {**rejected, "code": "participant_file_path_denied"}
        except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
            return {**rejected, "code": "participant_file_invalid", "message": (
                "Supply one UTF-8 JSON draft object, with unique keys and finite values, "
                "at most 256 KiB and 32 nested levels."
            )}
        result = inline_tool.handler(draft, context)
        return {**result, "draft_path": target.relative_to(guardrail.allowed_root).as_posix(),
                "draft_version": version}

    return ToolSpec.from_openai_schema(
        {"type": "function", "function": {
            "name": name,
            "description": (
                ("Check" if checking else "Validate and save")
                + " the exact JSON character draft in this Builder workspace using its observed "
                "read_file/write_file/edit_file/copy_builder_reference version. Same validation "
                "and role as the inline character tool; no need to repeat the full draft. "
                "The file contains summary, authored_profile, participant, entity_state, optional "
                "setting_data/schema_id/source_profile. State and Setting data may be objects. "
                "A check does not save or change a campaign. Maximum 256 KiB."
            ),
            "parameters": {"type": "object", "additionalProperties": False,
                           "required": ["path", "expected_version"], "properties": {
                               "path": {"type": "string"},
                               "expected_version": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                           }},
        }},
        handler=use_file, toolsets=inline_tool.toolsets,
    )
