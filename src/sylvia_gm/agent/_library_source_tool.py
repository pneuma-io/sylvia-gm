"""Builder tool adapter for explicit installed-library source inspection."""

from __future__ import annotations

from typing import Any

from sylvia_gm.application.library_sources import LibrarySourceReader
from sylvia_gm.domain.authority import thaw_json_value
from sylvia_gm.domain.errors import AuthorityError
from sylvia_gm.tools._spec import ToolSpec
from sylvia_gm.tools._toolsets import ToolsetName


def library_source_tool(reader: LibrarySourceReader) -> ToolSpec:
    """Create the read-only tool used when the user requests a source update."""

    def inspect(params: dict[str, Any], _context: Any) -> dict[str, Any]:
        if set(params) - {"action", "candidate_id", "path", "query", "offset", "limit"}:
            return {"success": False, "code": "library_source_parameters_invalid"}
        action = params.get("action")
        if "query" in params and action != "candidates":
            return {
                "success": False,
                "code": "library_source_query_unsupported",
                "message": (
                    "query filters candidates only; it does not search source file contents. "
                    "Use files to list paths, then read with character offset and limit. "
                    "For package declarations, use record with a package prefix."
                ),
            }
        candidate_id = params.get("candidate_id")
        offset = params.get("offset", 0)
        limit = params.get("limit", 20 if action in {"candidates", "files"} else 8000)
        if (
            not isinstance(offset, int)
            or isinstance(offset, bool)
            or offset < 0
            or not isinstance(limit, int)
            or isinstance(limit, bool)
            or limit < 1
        ):
            return {"success": False, "code": "library_source_page_invalid"}
        try:
            if action == "candidates":
                query = params.get("query", "")
                if not isinstance(query, str) or len(query) > 120:
                    return {"success": False, "code": "library_source_query_invalid"}
                candidates = reader.candidates(query=query)
                end = min(len(candidates), offset + min(limit, 20))
                return {
                    "success": True,
                    "candidates": [thaw_json_value(item) for item in candidates[offset:end]],
                    "total": len(candidates),
                    "next_offset": end if end < len(candidates) else None,
                }
            if not isinstance(candidate_id, str) or not candidate_id:
                return {"success": False, "code": "library_source_candidate_required"}
            if action == "files":
                source = thaw_json_value(reader.files(candidate_id))
                paths = source["paths"]
                end = min(len(paths), offset + min(limit, 100))
                return {
                    "success": True,
                    **{key: value for key, value in source.items() if key != "paths"},
                    "paths": paths[offset:end],
                    "total": len(paths),
                    "next_offset": end if end < len(paths) else None,
                }
            if action not in {"read", "record"} or not isinstance(params.get("path"), str):
                return {"success": False, "code": "library_source_path_unavailable"}
            source = thaw_json_value(
                reader.read(candidate_id, params["path"]) if action == "read"
                else reader.record(candidate_id, params["path"])
            )
            content = source["content"]
            end = min(len(content), offset + min(limit, 32000))
            return {
                "success": True,
                **{key: value for key, value in source.items() if key != "content"},
                "content": content[offset:end],
                "total_characters": len(content),
                "next_offset": end if end < len(content) else None,
            }
        except (AuthorityError, OSError, UnicodeError, ValueError):
            return {
                "success": False,
                "code": "library_source_unavailable",
                "message": (
                    "Confirm the source with candidates. Use read with an exact path returned "
                    "by files, or record with the ruleset, setting, or dependencies/<id> prefix. "
                    "Both read and record support character offset and limit."
                ),
            }

    return ToolSpec.from_openai_schema(
        {
            "type": "function",
            "function": {
                "name": "inspect_library_sources",
                "description": (
                    "Inspect installed Ruleset or Campaign Setting sources for requested edits "
                    "or source updates. Read editable declarations with action record and path "
                    "ruleset, setting, or dependencies/<id>. Compare a chosen source with the "
                    "saved launch "
                    "references and current draft, then write only accepted overlay changes. "
                    "Ask the user when both source and draft changed the same content. Candidate "
                    "order does not identify the newest version."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["candidates", "files", "read", "record"],
                        },
                        "candidate_id": {
                            "type": "string",
                            "description": "Exact candidate ID returned by candidates.",
                        },
                        "path": {
                            "type": "string",
                            "description": "Exact file path, or package prefix for action record.",
                        },
                        "query": {
                            "type": "string",
                            "description": "Optional candidate identity or package-label filter.",
                        },
                        "offset": {
                            "type": "integer",
                            "description": "Zero-based candidate, path, or character offset.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": (
                                "Candidates (max 20), paths (max 100), or characters (max 32000)."
                            ),
                        },
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            },
        },
        handler=inspect,
        toolsets=frozenset({ToolsetName.BUILDER_TOOLS}),
    )
