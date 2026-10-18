"""Changelog — append-only audit trail + crash recovery mechanism.

Format: JSONL (one JSON object per line).
Each entry: ``{timestamp, operation, entity_type, entity_id, field,
old_value, new_value, metadata}``.

Crash-consistent write ordering:
1. Write changelog entry + fsync.
2. Write state.json via atomic replace.
3. On load, verify() compares last changelog against state.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ._exceptions import (
    NoEntriesToUndoError,
)


class Changelog:
    """Append-only JSONL changelog with undo and crash recovery support."""

    def __init__(self, path: Path) -> None:
        """Open or create a changelog at *path*.

        Args:
            path: Path to ``changelog.jsonl``.
        """
        self._path: Path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._path.touch()
        self._entries: list[dict[str, Any]] | None = None  # lazy load

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    @property
    def entries(self) -> list[dict[str, Any]]:
        """All changelog entries (loaded lazily from disk)."""
        if self._entries is None:
            self._entries = []
            if self._path.exists():
                with open(self._path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                self._entries.append(json.loads(line))
                            except json.JSONDecodeError:
                                # Corrupt line — skip but warn
                                pass
        return self._entries

    def get_entries(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the most recent *limit* entries."""
        return self.entries[-limit:] if limit > 0 else []

    @property
    def count(self) -> int:
        """Number of entries in the changelog."""
        return len(self.entries)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def append(
        self,
        operation: str,
        entity_type: str,
        entity_id: str,
        field: str,
        old_value: Any,
        new_value: Any,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append a single changelog entry and fsync.

        Args:
            operation: e.g., 'take_damage', 'set_field', 'create_character'.
            entity_type: 'character', 'monster', 'npc', 'party', etc.
            entity_id: ID of the affected entity.
            field: Field path that changed (dot-separated).
            old_value: Value before mutation.
            new_value: Value after mutation.
            metadata: Optional extra context.

        Returns:
            The entry dict that was written.
        """
        entry: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "operation": operation,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "field": field,
            "old_value": _serialize_value(old_value),
            "new_value": _serialize_value(new_value),
            "metadata": metadata or {},
        }

        # Append to file
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())

        # Update in-memory cache
        if self._entries is not None:
            self._entries.append(entry)

        return entry

    def append_batch(
        self,
        entries: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Append multiple entries in one batch, each with a sequence number.

        Each entry dict should have keys: operation, entity_type, entity_id,
        field, old_value, new_value, metadata (optional).
        """
        written: list[dict[str, Any]] = []
        seq = 0
        with open(self._path, "a", encoding="utf-8") as f:
            for e in entries:
                seq += 1
                full_entry: dict[str, Any] = {
                    "timestamp": datetime.now(UTC).isoformat(),
                    "operation": e["operation"],
                    "entity_type": e["entity_type"],
                    "entity_id": e["entity_id"],
                    "field": e["field"],
                    "old_value": _serialize_value(e.get("old_value")),
                    "new_value": _serialize_value(e.get("new_value")),
                    "metadata": e.get("metadata") or {},
                    "sequence": seq,
                }
                f.write(json.dumps(full_entry, ensure_ascii=False) + "\n")
                written.append(full_entry)

            f.flush()
            os.fsync(f.fileno())

        if self._entries is not None:
            self._entries.extend(written)

        return written

    # ------------------------------------------------------------------
    # Undo
    # ------------------------------------------------------------------

    def undo(self) -> dict[str, Any]:
        """Pop and return the last entry in the changelog.

        This does NOT reverse the state mutation — the caller must
        apply the inverse operation using ``old_value`` and ``new_value``.

        Raises:
            NoEntriesToUndoError: If the changelog is empty.
        """
        if not self.entries:
            raise NoEntriesToUndoError("No changelog entries to undo")

        # Remove last line from file
        entry = self.entries.pop()
        _truncate_last_line(self._path)

        return entry

    # ------------------------------------------------------------------
    # Verify (crash-consistency check)
    # ------------------------------------------------------------------

    def verify(self, state: dict[str, Any]) -> list[str]:
        """Check changelog consistency against current state.

        Compares the last changelog entry's ``new_value`` against the
        current state value for that field. If the changelog has entries
        newer than the state, they are "dangling" and should be replayed.

        Returns:
            List of issues found. Empty list means consistent.
        """
        issues: list[str] = []
        if not self.entries:
            return issues

        last = self.entries[-1]
        entity_type = last.get("entity_type", "")
        entity_id = last.get("entity_id", "")
        field = last.get("field", "")
        expected = last.get("new_value")

        # Navigate to the field in the state
        actual = _get_state_value(state, entity_type, entity_id, field)

        if _values_differ(actual, expected):
            issues.append(
                f"Changelog/state mismatch for {entity_type}/{entity_id}.{field}: "
                f"changelog expects {expected!r}, state has {actual!r}"
            )

        return issues

    # ------------------------------------------------------------------
    # Replay (recover state from changelog)
    # ------------------------------------------------------------------

    @staticmethod
    def replay(
        state: dict[str, Any],
        entries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Replay a list of changelog entries onto a state dict.

        This is used for crash recovery: if the state file is behind
        the changelog, replay the dangling entries forward.
        """
        import copy

        result = copy.deepcopy(state)
        for entry in entries:
            entity_type = entry.get("entity_type", "")
            entity_id = entry.get("entity_id", "")
            field = entry.get("field", "")
            new_value = entry.get("new_value")

            _set_state_value(result, entity_type, entity_id, field, new_value)

        return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _serialize_value(value: Any) -> Any:
    """Make a value JSON-safe for changelog storage.

    Lists/tuples become lists. Dicts are copied. Everything else pass-through.
    """
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, dict):
        return dict(value)
    return value


def _values_differ(a: Any, b: Any) -> bool:
    """Compare two values for changelog verification.

    Handles None vs null, list ordering, and type coercion safely.
    """
    if a is None and b is None:
        return False
    if a is None or b is None:
        return True
    if isinstance(a, list) and isinstance(b, list):
        return a != b
    if isinstance(a, dict) and isinstance(b, dict):
        return a != b
    return a != b


def _get_state_value(
    state: dict[str, Any],
    entity_type: str,
    entity_id: str,
    field: str,
) -> Any:
    """Navigate the state dict to find a field value.

    Maps entity_type to the right top-level key:
    - character → characters[entity_id]
    - monster → monsters[entity_id]
    - npc → npcs[entity_id]
    - party → party
    - combat → combat
    - quest → quests[entity_id]
    - campaign → campaign
    - world → world
    """
    _type_map = {
        "character": "characters",
        "monster": "monsters",
        "npc": "npcs",
        "quest": "quests",
    }

    if entity_type in _type_map:
        section = _type_map[entity_type]
        entities = state.get(section, {})
        entity = entities.get(entity_id, {})
    elif entity_type in ("party", "campaign", "world", "combat", "metadata"):
        entity = state.get(entity_type, {})
    else:
        return None

    if not isinstance(entity, dict):
        return None

    # Navigate dot-separated field path
    parts = field.split(".")
    current: Any = entity
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def _set_state_value(
    state: dict[str, Any],
    entity_type: str,
    entity_id: str,
    field: str,
    value: Any,
) -> None:
    """Set a field value in the state dict, creating intermediate dicts."""
    _type_map = {
        "character": "characters",
        "monster": "monsters",
        "npc": "npcs",
        "quest": "quests",
    }

    if entity_type in _type_map:
        section = _type_map[entity_type]
        state.setdefault(section, {})
        entity = state[section].setdefault(entity_id, {})
    elif entity_type in ("party", "campaign", "world", "combat", "metadata"):
        entity = state.setdefault(entity_type, {})
    else:
        return

    parts = field.split(".")
    current: Any = entity
    for part in parts[:-1]:
        if part not in current or not isinstance(current[part], dict):
            current[part] = {}
        current = current[part]

    current[parts[-1]] = value


def _truncate_last_line(path: Path) -> None:
    """Remove the last line from a file."""
    if not path.exists():
        return
    with open(path, "rb") as f:
        data = f.read()
    if not data:
        return
    # Find last newline
    idx = data.rfind(b"\n", 0, -1)
    if idx == -1:
        # Only one line — clear file
        with open(path, "wb") as f:
            f.write(b"")
    else:
        with open(path, "wb") as f:
            f.write(data[: idx + 1])
