"""Copy an exact installed library source into the Builder's owned workspace."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sylvia_gm.application.library_sources import LibrarySourceReader
from sylvia_gm.builder import _workspace_files
from sylvia_gm.builder._guardrail import GuardrailBlockedError, PathGuardrail
from sylvia_gm.domain.errors import AuthorityError
from sylvia_gm.tools._spec import ToolSpec
from sylvia_gm.tools._toolsets import ToolsetName


def library_source_copy_tool(reader: LibrarySourceReader, workspace: Path) -> ToolSpec:
    """Copy one complete compatible source without passing its content through model context."""

    guardrail = PathGuardrail(workspace)

    def copy(params: dict[str, Any], _context: Any) -> dict[str, Any]:
        candidate_id = params.get("candidate_id")
        expected_closure = params.get("expected_closure_sha256")
        source_type = params.get("source_type")
        source_path = params.get("source_path")
        destination = params.get("destination_path")
        expected_version = params.get("expected_version")
        if (
            set(params)
            - {
                "candidate_id",
                "expected_closure_sha256",
                "source_type",
                "source_path",
                "destination_path",
                "expected_version",
            }
            or not isinstance(candidate_id, str)
            or not candidate_id
            or not isinstance(expected_closure, str)
            or not expected_closure
            or not isinstance(source_type, str)
            or source_type not in {"file", "editable_record"}
            or not isinstance(source_path, str)
            or not source_path
            or not isinstance(destination, str)
            or not destination.strip()
            or (expected_version is not None and not isinstance(expected_version, str))
        ):
            return {"success": False, "code": "library_source_copy_parameters_invalid"}
        try:
            target = guardrail.validate(destination, for_write=True)
            source = (
                reader.read(candidate_id, source_path)
                if source_type == "file"
                else reader.record(candidate_id, source_path)
            )
            actual_closure = source.get("closure_sha256")
            if actual_closure != expected_closure:
                return {
                    "success": False,
                    "code": "library_source_copy_changed",
                    "candidate_id": candidate_id,
                    "closure_sha256": actual_closure,
                }
            content = source.get("content")
            package = source.get("package")
            if not isinstance(content, str) or not isinstance(package, Mapping):
                raise ValueError("Library source copy is incomplete")
            if source_type == "editable_record":
                # Local search/read tools address lines; preserve record data in an editable layout.
                content = json.dumps(json.loads(content), ensure_ascii=False, indent=2) + "\n"
            version = _workspace_files.write_text(target, content, expected_version)
        except GuardrailBlockedError:
            return {"success": False, "code": "library_source_copy_path_denied"}
        except _workspace_files.StaleWorkspaceFileError as error:
            return {"success": False, "error_code": "stale_file", "error": str(error)}
        except (AuthorityError, OSError, UnicodeError, ValueError):
            return {"success": False, "code": "library_source_copy_unavailable"}
        return {
            "success": True,
            "candidate_id": candidate_id,
            "closure_sha256": actual_closure,
            "source_type": source_type,
            "source_path": source_path,
            "package": dict(package),
            "path": target.relative_to(guardrail.allowed_root).as_posix(),
            "bytes_written": len(content.encode("utf-8")),
            "version": version,
        }

    return ToolSpec.from_openai_schema(
        {
            "type": "function",
            "function": {
                "name": "copy_library_source",
                "description": (
                    "Copy one complete file or editable package record from an exact installed "
                    "library candidate into the owned Builder workspace without returning the "
                    "source content to model context. First use inspect_library_sources to choose "
                    "the candidate and exact source path. Use source_type file for a path listed "
                    "by files, or editable_record for a package prefix accepted by record. The "
                    "editable record is formatted across lines for local search and editing. The "
                    "candidate closure must still match. Existing destinations require the version "
                    "returned by read_file; reconcile local edits before replacing them. Source "
                    "packages remain immutable."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "candidate_id": {
                            "type": "string",
                            "description": (
                                "Exact candidate ID returned by inspect_library_sources."
                            ),
                        },
                        "expected_closure_sha256": {
                            "type": "string",
                            "description": "Exact closure hash returned for that candidate.",
                        },
                        "source_type": {
                            "type": "string",
                            "enum": ["file", "editable_record"],
                        },
                        "source_path": {
                            "type": "string",
                            "description": (
                                "Exact file path from files, or exact package prefix for "
                                "editable_record."
                            ),
                        },
                        "destination_path": {
                            "type": "string",
                            "description": "Owned workspace path to create or replace.",
                        },
                        "expected_version": {
                            "type": "string",
                            "description": "Observed destination version when replacing a file.",
                        },
                    },
                    "required": [
                        "candidate_id",
                        "expected_closure_sha256",
                        "source_type",
                        "source_path",
                        "destination_path",
                    ],
                    "additionalProperties": False,
                },
            },
        },
        handler=copy,
        toolsets=frozenset({ToolsetName.BUILDER_TOOLS}),
    )
