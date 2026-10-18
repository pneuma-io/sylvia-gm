"""JSONL history helpers retained for file-backed compatibility callers.

The desktop Builder adapter in agent._builder_session supplies SQLite history
and passes history_path=None, so these helpers do not persist that session.
Current session persistence is owned by infrastructure.sqlite_authority.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Historical file-history limits; these do not cap SQLite session history.
DM_MAX_HISTORY_LINES = 50
BUILDER_MAX_HISTORY_LINES = 200


def save_history(history_path: Path | None, history: list[dict[str, Any]], *, mode: str = "dm") -> None:
    """Write the most recent messages to the history file.

    Args:
        history_path: Path to the JSONL file. If None, silently returns.
        history: List of message dicts.
        mode: "dm" or "builder" — controls the line cap.
    """
    if history_path is None:
        return
    max_lines = BUILDER_MAX_HISTORY_LINES if mode == "builder" else DM_MAX_HISTORY_LINES
    trimmed = history[-max_lines:]

    with open(history_path, "w", encoding="utf-8") as f:
        for msg in trimmed:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")


def load_history(history_path: Path | None) -> list[dict[str, Any]]:
    """Load saved conversation history from disk."""
    if history_path is None or not history_path.exists():
        return []

    messages: list[dict[str, Any]] = []
    with open(history_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError:
                logger.warning(
                    "Skipping malformed history line %d in %s: %.80s...",
                    line_num,
                    history_path,
                    line,
                )
                continue
    return messages
