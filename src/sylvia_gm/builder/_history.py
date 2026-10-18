"""Builder history persistence (JSONL).

Chat message history is saved/loaded as newline-delimited JSON to a file
within the campaign directory.
"""

from __future__ import annotations

import json
from pathlib import Path

MAX_HISTORY_LINES = 200


def load_builder_history(path: Path) -> list[dict]:
    """Load builder conversation history from a JSONL file.

    Blank lines and malformed JSON lines are silently skipped.
    Returns an empty list if the file does not exist.
    """
    if not path.exists():
        return []

    messages: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                msg = json.loads(stripped)
                messages.append(msg)
            except json.JSONDecodeError:
                continue
    return messages


def save_builder_history(path: Path, messages: list[dict]) -> None:
    """Save builder conversation history to a JSONL file.

    Only the last MAX_HISTORY_LINES messages are retained.
    """
    trimmed = messages[-MAX_HISTORY_LINES:]

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for msg in trimmed:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
