"""Extracted builder tool handlers.

These are standalone functions extracted from agent/_runner.py that handle
file I/O operations for the Builder agent. Each accepts (params: dict, context: Any)
where context is the AgentRunner instance (for access to _project_root, _guardrail).

The guardrail-wrapping and path resolution happen in the closure built by
AgentRunner._wrap_builder_handler(), not in these handlers. Handlers expect
params["_resolved_path"] and params["_project_root"] to already be populated.
"""

from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from sylvia_gm.builder import _workspace_files


def _search_result_url(raw_url: str) -> str:
    decoded = unescape(raw_url)
    redirect = parse_qs(urlparse(decoded).query).get("uddg")
    return redirect[0] if redirect else decoded


class _SearchResultParser(HTMLParser):
    """Extract bounded search-result fields without a DOM dependency."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._field: str | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        values = {key: value or "" for key, value in attrs}
        classes = set(values.get("class", "").split())
        if "result__a" in classes:
            self._current = {
                "title": "",
                "url": _search_result_url(values.get("href", "")),
                "description": "",
            }
            self._field = "title"
            self._parts = []
        elif "result__snippet" in classes and self._current is not None:
            self._field = "description"
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._field is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._field is None or self._current is None:
            return
        self._current[self._field] = " ".join("".join(self._parts).split())
        if self._field == "description":
            if self._current["title"] and self._current["url"]:
                self.results.append(self._current)
            self._current = None
        self._field = None
        self._parts = []


def tool_web_search(params: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Search current public sources through DuckDuckGo's HTML endpoint."""

    query = params.get("query")
    limit = params.get("limit", 5)
    if not isinstance(query, str) or not query.strip():
        return {"success": False, "error": "Web search query must be nonempty text"}
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 10:
        return {"success": False, "error": "Web search limit must be an integer from 1 to 10"}
    try:
        response = httpx.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query.strip()},
            headers={"User-Agent": "Sylvia GM-Builder/0.1 (+local desktop agent)"},
            timeout=15.0,
            follow_redirects=True,
        )
        response.raise_for_status()
    except httpx.HTTPError as error:
        return {"success": False, "error": f"Web search failed: {type(error).__name__}"}

    parser = _SearchResultParser()
    parser.feed(response.text)
    return {
        "success": True,
        "query": query.strip(),
        "results": parser.results[:limit],
    }


def tool_read_file(params: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Read a file, returning content with line numbers."""
    file_path: Path = params["_resolved_path"]
    offset: int = params.get("offset", 1)
    limit: int = params.get("limit", 500)
    limit = min(limit, 2000)

    try:
        data = file_path.read_bytes()
        if b"\x00" in data[:1024]:
            return {"success": False, "error": f"Cannot read binary file: {file_path}"}
        lines = data.decode("utf-8").splitlines(keepends=True)

        total_lines = len(lines)
        start = max(0, offset - 1)
        end = min(total_lines, start + limit)
        selected = lines[start:end]

        content = "".join(selected)
        if len(content) > 100_000:
            return {
                "success": False,
                "error": (
                    f"File too large ({len(content)} chars). "
                    f"Use offset/limit to read smaller chunks. Total lines: {total_lines}."
                ),
            }

        numbered = "".join(f"{i + 1:6d}|{line}" for i, line in enumerate(selected, start=start))

        return {
            "success": True,
            "content": numbered,
            "path": str(file_path),
            "lines": len(selected),
            "total_lines": total_lines,
            "version": _workspace_files.content_version(data),
        }
    except FileNotFoundError:
        return {
            "success": False,
            "error": f"File not found: {file_path}",
            "version": _workspace_files.MISSING_VERSION,
        }
    except UnicodeDecodeError:
        return {"success": False, "error": f"Cannot read binary file: {file_path}"}
    except Exception as e:
        return {"success": False, "error": f"Error reading file: {e}"}


def tool_write_file(params: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Save atomically, rejecting replacements based on an outdated file version."""
    file_path: Path = params["_resolved_path"]
    content = params.get("content")

    if content is None:
        return {"success": False, "error": "content must be a non-empty string, got null"}
    if not isinstance(content, str) or not content:
        return {"success": False, "error": "content must be a non-empty string"}

    if file_path.exists() and file_path.is_dir():
        return {"success": False, "error": f"Cannot write to a directory: {file_path}"}

    try:
        version = _workspace_files.write_text(
            file_path, content, params.get("expected_version")
        )
        bytes_written = len(content.encode("utf-8"))
        return {
            "success": True,
            "bytes_written": bytes_written,
            "path": str(file_path),
            "version": version,
        }
    except _workspace_files.StaleWorkspaceFileError as error:
        return {"success": False, "error_code": "stale_file", "error": str(error)}
    except Exception as e:
        return {"success": False, "error": f"Error writing file: {e}"}


def tool_edit_file(params: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Replace one unique literal in an observed workspace file."""

    file_path: Path = params["_resolved_path"]
    old_text = params.get("old_text")
    new_text = params.get("new_text")
    expected_version = params.get("expected_version")
    if not isinstance(old_text, str) or not old_text:
        return {
            "success": False,
            "error_code": "invalid_edit",
            "error": "old_text must be a non-empty string",
        }
    if not isinstance(new_text, str):
        return {
            "success": False,
            "error_code": "invalid_edit",
            "error": "new_text must be a string",
        }
    if not isinstance(expected_version, str) or not expected_version:
        return {
            "success": False,
            "error_code": "stale_file",
            "error": "Read the file first and pass its version as expected_version.",
        }
    try:
        version, bytes_written = _workspace_files.edit_text(
            file_path, old_text, new_text, expected_version
        )
        return {
            "success": True,
            "bytes_written": bytes_written,
            "path": str(file_path),
            "version": version,
        }
    except _workspace_files.StaleWorkspaceFileError as error:
        return {"success": False, "error_code": "stale_file", "error": str(error)}
    except _workspace_files.LiteralMatchError as error:
        return {
            "success": False,
            "error_code": "text_not_found" if error.matches == 0 else "text_not_unique",
            "error": str(error),
        }
    except Exception as error:
        return {"success": False, "error": f"Error editing file: {error}"}


def tool_search_files(params: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Search for files by glob or content regex."""
    # _project_root may be set on params; fall back to resolved_path parent
    raw_search_path = params.get("_resolved_path")
    if raw_search_path is None:
        search_path = Path(params.get("_project_root", Path(".")))
    elif isinstance(raw_search_path, (str, Path)):
        search_path = Path(raw_search_path)
    else:
        search_path = Path(params.get("_project_root", Path(".")))

    pattern: str = params.get("pattern", "*")
    target: str = params.get("target", "files")
    project_root = Path(params.get("_project_root", search_path))

    def owner_internal(path: Path) -> bool:
        try:
            relative = path.relative_to(project_root)
        except ValueError:
            return False
        return (
            bool(relative.parts)
            and relative.parts[0].casefold() == ".builder-sources-purge-pending"
        )

    try:
        if not search_path.exists():
            return {"success": False, "error": f"Search path not found: {search_path}"}
        if target == "files":
            matches = sorted(
                [
                    str(p.relative_to(search_path))
                    for p in search_path.rglob(pattern)
                    if p.is_file() and not owner_internal(p)
                ]
            )
            return {
                "success": True,
                "matches": matches[:50],
                "count": len(matches),
            }
        else:
            try:
                regex = re.compile(pattern)
            except re.error as e:
                return {"success": False, "error": f"Invalid regex pattern: {e}"}

            results: list[dict[str, Any]] = []
            single_file = search_path.is_file()
            candidates = (search_path,) if single_file else search_path.rglob("*")
            result_root = project_root if single_file else search_path
            for file_path in candidates:
                if not file_path.is_file() or owner_internal(file_path):
                    continue
                if file_path.stat().st_size > 1_000_000:
                    continue
                try:
                    file_content = file_path.read_text(encoding="utf-8")
                except (UnicodeDecodeError, PermissionError):
                    continue

                for i, line in enumerate(file_content.splitlines(), 1):
                    if regex.search(line):
                        results.append(
                            {
                                "file": str(file_path.relative_to(result_root)),
                                "line": i,
                                "content": line[:200],
                            }
                        )
                        if len(results) >= 100:
                            break
                if len(results) >= 100:
                    break

            return {
                "success": True,
                "matches": results,
                "count": len(results),
            }
    except Exception as e:
        return {"success": False, "error": f"Error searching files: {e}"}


def tool_delete_file(params: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Delete the observed file version; refuse directories and stale observations."""
    file_path: Path = params["_resolved_path"]
    try:
        if not file_path.exists():
            return {"success": False, "error": f"File not found: {file_path}"}
        if file_path.is_dir():
            return {"success": False, "error": f"Cannot delete a directory: {file_path}"}
        _workspace_files.delete_file(file_path, params.get("expected_version"))
        return {"success": True, "path": str(file_path)}
    except _workspace_files.StaleWorkspaceFileError as error:
        return {"success": False, "error_code": "stale_file", "error": str(error)}
    except Exception as e:
        return {"success": False, "error": f"Error deleting file: {e}"}


def tool_ask_question(params: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Handler for the ask_question builder tool.

    Returns a serializable question with 'status': 'awaiting_answer'.
    The Builder adapter retains it as tool evidence; the desktop renders
    answer controls and submits the player's ordinary saved reply.

    Question payload:
        {
            "type": "select"|"text"|"confirm"|"complete",
            "data": {
                "question": "...",       # for select/text/confirm
                "options": [...],        # for select/confirm
                "id": "...",             # always
                "summary": "...",        # for complete
                "campaign_id": "...",    # for complete
            }
        }
    """
    widget_type: str = params.get("type", "select")
    widget_id: str = params.get("id", "")

    if widget_type == "cascade":
        cascade_question = params.get("question", "")
        options = params.get("options", [])
        action_label = params.get("action_label", "Choose")
        action_style = params.get("action_style", "primary")  # primary, secondary, skip

        if not cascade_question:
            return {
                "success": False,
                "error": "question is required for cascade widget type",
            }

        cascade_widget_data: dict[str, Any] = {
            "question": cascade_question,
            "id": widget_id,
            "options": options,
            "action_label": action_label,
            "action_style": action_style,
        }

        # Allow empty options (e.g., for "No characters found" scenarios)
        if not options:
            cascade_widget_data["options"] = []

        return {
            "success": True,
            "status": "awaiting_answer",
            "widget": {
                "type": "cascade",
                "data": cascade_widget_data,
            },
        }

    if widget_type == "complete":
        return {
            "success": True,
            "status": "awaiting_answer",
            "widget": {
                "type": "complete",
                "data": {
                    "summary": params.get("summary", "Your campaign is ready!"),
                    "campaign_id": params.get("campaign_id", ""),
                    "id": widget_id,
                },
            },
        }

    if widget_type == "plan_complete":
        return {
            "success": True,
            "status": "awaiting_answer",
            "widget": {
                "type": "plan_complete",
                "data": {
                    "summary": params.get("summary", "Campaign plan written!"),
                    "campaign_id": params.get("campaign_id", ""),
                    "id": widget_id,
                },
            },
        }

    question: str = params.get("question", "")
    if not question:
        return {
            "success": False,
            "error": "question is required for select/text/confirm widget types",
        }

    # ── Build data dict in the exact shape renderWidget() expects ──
    widget_data: dict[str, Any] = {
        "question": question,
        "id": widget_id,
    }

    if widget_type in ("select", "confirm"):
        options = params.get("options", [])
        if not options:
            return {
                "success": False,
                "error": f"options list is required for '{widget_type}' widget type",
            }
        widget_data["options"] = options

    return {
        "success": True,
        "status": "awaiting_answer",
        "widget": {
            "type": widget_type,
            "data": widget_data,
        },
    }


def tool_create_campaign_folder(params: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Handler for the create_campaign_folder builder tool.

    Called by the Builder agent during the planning phase (when it
    receives PLAN_CAMPAIGN after completing the interview).  Delegates
    to the existing :func:`sylvia_gm.campaign._folder.create_campaign_folder`
    function which clones the CS, copies plan files, writes handoff.md/
    manifest.md/status.json, and incorporates interview answers.

    Parameters match what the Builder knows from the interview:
    campaign_name, ruleset, setting_slug, plan_slug,
    selected_characters, selected_companions, difficulty, tone,
    boundaries, freeform_requests.
    """
    from sylvia_gm.campaign._folder import (
        _derive_campaign_slug,
        create_campaign_folder,
    )

    campaign_name = params.get("campaign_name", "")
    if not campaign_name:
        return {"success": False, "error": "campaign_name is required"}

    slug = _derive_campaign_slug(campaign_name)
    result = create_campaign_folder(
        ruleset=params["ruleset"],
        setting_slug=params.get("setting_slug", ""),
        plan_slug=params.get("plan_slug", ""),
        selected_characters=params.get("selected_characters", []),
        selected_companions=params.get("selected_companions", []),
        difficulty=params.get("difficulty", "normal"),
        tone=params.get("tone", "dark"),
        boundaries=params.get("boundaries", []),
        freeform_requests=params.get("freeform_requests", ""),
        campaign_slug_override=slug,
    )

    if result.success:
        return {
            "success": True,
            "campaign_slug": slug,
            "campaign_folder": str(result.campaign_folder) if result.campaign_folder else "",
            "handoff_path": str(result.handoff_path) if result.handoff_path else "",
        }
    else:
        return {
            "success": False,
            "error": result.error or "Unknown error creating campaign folder",
        }
