"""DM campaign-file tools (R2 — Context & Campaign Architecture §4).

The DM Agent reads its own campaign through these tools instead of relying on
a static boot blob. The campaign folder is the DM's brain; these are how the
DM consults it at runtime.

Tools:
  - read_act(n)            — load an act plan; defaults to status.json:current_act
  - read_campaign_file(p)  — read any file in the campaign folder (handoff,
                             DM-SECRETS, changelog) — path is folder-relative
  - write_act_changelog    — append an entry to session/act-N-changelog.md
  - advance_act()          — promote status.json to Act N+1 and mark it

Safety: all paths are confined to the session's campaign folder (no traversal).
Secrets are NOT special-cased here — per the architecture, the model may know
and reason about secrets freely (thinking is hidden); the reveal_guard polices
player-facing narration, not context. So read_campaign_file may read
DM-SECRETS.md like any other file.

The handlers read the campaign folder path from ``params["_campaign_folder"]``
which the runner injects from store.state["campaign_folder_path"].
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ── Helpers ───────────────────────────────────────────────────────────────


def _campaign_root(params: dict[str, Any]) -> Path | None:
    """Resolve the campaign folder root from injected params."""
    raw = params.get("_campaign_folder")
    if not raw:
        return None
    return Path(raw).resolve()


def _confine(root: Path, rel: str) -> Path | None:
    """Join a folder-relative path and refuse traversal outside the root."""
    # Strip any leading slashes / drive so it's treated as relative
    rel = rel.lstrip("/\\").replace("\\", "/")
    candidate = (root / rel).resolve()
    if candidate != root and root not in candidate.parents:
        return None
    return candidate


def _read_status(root: Path) -> dict[str, Any]:
    status_path = root / "status.json"
    if not status_path.is_file():
        return {}
    try:
        return json.loads(status_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


# ── Tools ─────────────────────────────────────────────────────────────────


def tool_read_act(params: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Read an act plan from the campaign folder.

    Params:
      act (int, optional): act number. Defaults to status.json: current_act.

    Returns the act markdown, the act number used, and the current_act pointer.
    """
    root = _campaign_root(params)
    if root is None:
        return {"success": False, "error": "No campaign folder for this session."}

    status = _read_status(root)
    current_act = status.get("current_act", 1)

    act = params.get("act")
    if act is None:
        act = current_act
    try:
        act = int(act)
    except (TypeError, ValueError):
        return {"success": False, "error": f"Invalid act number: {act!r}"}

    act_path = root / "acts" / f"act-{act}.md"
    if not act_path.is_file():
        return {
            "success": False,
            "error": f"No act file at acts/act-{act}.md",
            "current_act": current_act,
        }

    content = act_path.read_text(encoding="utf-8")
    return {
        "success": True,
        "context": {
            "act": act,
            "current_act": current_act,
            "content": content,
            "path": str(act_path),
        },
        # Top-level mirrors for direct/test access
        "act": act,
        "current_act": current_act,
        "content": content,
        "path": str(act_path),
    }


def tool_read_campaign_file(params: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Read any file in the campaign folder (folder-relative path).

    Params:
      path (str): e.g. "handoff.md", "DM-SECRETS.md", "session/act-1-changelog.md"
    """
    root = _campaign_root(params)
    if root is None:
        return {"success": False, "error": "No campaign folder for this session."}

    rel = params.get("path", "")
    if not rel:
        return {"success": False, "error": "path is required"}

    file_path = _confine(root, rel)
    if file_path is None:
        return {"success": False, "error": f"Path escapes campaign folder: {rel}"}
    if not file_path.is_file():
        return {"success": False, "error": f"File not found: {rel}"}

    try:
        content = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {"success": False, "error": f"Cannot read binary file: {rel}"}

    return {
        "success": True,
        "context": {"path": rel, "content": content},
        "path": rel,
        "content": content,
    }


def tool_write_act_changelog(params: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Append an entry to session/act-N-changelog.md.

    Params:
      entry (str): the changelog line(s) to append.
      act (int, optional): defaults to status.json: current_act.
    """
    root = _campaign_root(params)
    if root is None:
        return {"success": False, "error": "No campaign folder for this session."}

    entry = params.get("entry", "")
    if not isinstance(entry, str) or not entry.strip():
        return {"success": False, "error": "entry must be a non-empty string"}

    status = _read_status(root)
    act = params.get("act", status.get("current_act", 1))
    try:
        act = int(act)
    except (TypeError, ValueError):
        return {"success": False, "error": f"Invalid act number: {act!r}"}

    session_dir = root / "session"
    session_dir.mkdir(parents=True, exist_ok=True)
    log_path = session_dir / f"act-{act}-changelog.md"

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    block = f"\n- **{stamp}** — {entry.strip()}\n"

    try:
        if log_path.is_file():
            existing = log_path.read_text(encoding="utf-8")
        else:
            existing = f"# Act {act} — Changelog\n"
        log_path.write_text(existing.rstrip("\n") + "\n" + block, encoding="utf-8")
    except OSError as e:
        return {"success": False, "error": f"Failed to write changelog: {e}"}

    return {
        "success": True,
        "context": {"act": act, "path": str(log_path)},
        "act": act,
        "path": str(log_path),
    }


def tool_advance_act(params: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Advance the campaign to the next act (current_act += 1).

    Updates status.json atomically. The DM is expected to have finished the
    current act's beats; this flips the pointer so subsequent read_act calls
    (and the next session boot) load the new act. The DM should also rewrite
    handoff.md for the new act via the builder path (out of scope here).

    Params:
      to_act (int, optional): explicit act to jump to. Defaults to current_act + 1.
    """
    root = _campaign_root(params)
    if root is None:
        return {"success": False, "error": "No campaign folder for this session."}

    status = _read_status(root)
    current_act = status.get("current_act", 1)

    to_act = params.get("to_act")
    if to_act is None:
        to_act = current_act + 1
    try:
        to_act = int(to_act)
    except (TypeError, ValueError):
        return {"success": False, "error": f"Invalid act number: {to_act!r}"}

    # Sanity: the target act should exist before we point at it
    act_path = root / "acts" / f"act-{to_act}.md"
    if not act_path.is_file():
        return {
            "success": False,
            "error": f"Cannot advance — no acts/act-{to_act}.md exists",
            "current_act": current_act,
        }

    # Atomic status.json update (mirror _folder._update_status fields)
    status["current_act"] = to_act
    status["modified"] = datetime.now(timezone.utc).isoformat()
    status.setdefault("created", status["modified"])
    status.setdefault("campaign_slug", root.name)

    status_path = root / "status.json"
    tmp_path = root / ".status.json.tmp"
    try:
        tmp_path.write_text(json.dumps(status, indent=2), encoding="utf-8")
        os.replace(tmp_path, status_path)
    except OSError as e:
        return {"success": False, "error": f"Failed to update status.json: {e}"}

    return {
        "success": True,
        "context": {
            "previous_act": current_act,
            "current_act": to_act,
            "path": str(status_path),
        },
        "previous_act": current_act,
        "current_act": to_act,
        "path": str(status_path),
    }
