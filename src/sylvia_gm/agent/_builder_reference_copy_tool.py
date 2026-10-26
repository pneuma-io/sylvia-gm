"""Copy a complete saved Builder reference into an editable workspace file."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sylvia_gm.application.builder_references import ReferenceSource
from sylvia_gm.builder import _workspace_files
from sylvia_gm.builder._guardrail import GuardrailBlockedError, PathGuardrail
from sylvia_gm.domain.errors import AuthorityError
from sylvia_gm.tools._spec import ToolSpec
from sylvia_gm.tools._toolsets import ToolsetName


def builder_reference_copy_tool(source_reader: ReferenceSource, workspace: Path) -> ToolSpec:
    """Preserve unedited source content without requiring the model to regenerate it."""

    guardrail = PathGuardrail(workspace)

    def copy(params: dict[str, Any], _context: Any) -> dict[str, Any]:
        references = source_reader() if callable(source_reader) else source_reader
        if references is None:
            return {"success": False, "code": "builder_ruleset_selection_required"}
        source = params.get("source_path")
        destination = params.get("destination_path")
        expected_version = params.get("expected_version")
        if (
            set(params) - {"source_path", "destination_path", "expected_version"}
            or not isinstance(source, str)
            or source not in references.paths
            or not isinstance(destination, str)
            or not destination.strip()
            or (expected_version is not None and not isinstance(expected_version, str))
        ):
            return {"success": False, "code": "reference_copy_parameters_invalid"}
        try:
            target = guardrail.validate(destination, for_write=True)
            content = references.read(source)
            version = _workspace_files.write_text(target, content, expected_version)
        except GuardrailBlockedError:
            return {"success": False, "code": "reference_copy_path_denied"}
        except _workspace_files.StaleWorkspaceFileError as error:
            return {"success": False, "error_code": "stale_file", "error": str(error)}
        except AuthorityError as error:
            reason = getattr(error, "code", "")
            return {"success": False, "code": "reference_copy_unavailable", **(
                {"reason": reason} if reason in {
                    "package_file_oversized", "package_unavailable", "package_file_unavailable",
                    "package_corrupt",
                } else {}
            )}
        except (OSError, UnicodeError, ValueError):
            return {"success": False, "code": "reference_copy_unavailable"}
        return {
            "success": True,
            "source_path": source,
            "path": target.relative_to(guardrail.allowed_root).as_posix(),
            "bytes_written": len(content.encode("utf-8")),
            "version": version,
        }

    return ToolSpec.from_openai_schema(
        {
            "type": "function",
            "function": {
                "name": "copy_builder_reference",
                "description": (
                    "Copy one complete selected source reference into an editable workspace file. "
                    "Use this before editing an existing Plan or inherited file; a paged read "
                    "is not a complete copy. Source bytes remain unchanged. Existing destination "
                    "files require their observed version from read_file; reconcile local edits "
                    "before replacing them. If the complete source content is already in context, "
                    "use edit_file with the successful copy's returned version for focused changes "
                    "without rereading. Read content you have not seen. After a stale-file error, "
                    "read and reconcile the current destination before retrying."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "source_path": {
                            "type": "string",
                            "description": "Exact path listed by inspect_builder_references.",
                        },
                        "destination_path": {"type": "string"},
                        "expected_version": {
                            "type": "string",
                            "description": "Observed destination version when replacing a file.",
                        },
                    },
                    "required": ["source_path", "destination_path"],
                    "additionalProperties": False,
                },
            },
        },
        handler=copy,
        toolsets=frozenset({ToolsetName.BUILDER_TOOLS}),
    )
