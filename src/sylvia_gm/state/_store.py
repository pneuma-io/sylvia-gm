"""File I/O for state.json — atomic writes, dirty flag, load/save.

All file operations go through this module. The state is kept in memory
and flushed to disk via ``save()``. Writes are atomic using ``.tmp`` +
``os.replace()`` to prevent corruption on crash.

Campaign data is stored in ``~/Sylvia GM Campaigns/<slug>/``,
not in the project directory.
"""

from __future__ import annotations

import json
import os
import shutil
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ._exceptions import (
    StateCorruptionError,
    StateFileNotFoundError,
    StateValidationError,
)
from ._schema import (
    CURRENT_SCHEMA_VERSION,
    load_schema,
    validate_state,
)

# State format version (the software version that writes this format).
STATE_FORMAT_VERSION: str = "0.2.0"


# ---------------------------------------------------------------------------
# Default empty state factory
# ---------------------------------------------------------------------------


def empty_state(ruleset: str) -> dict[str, Any]:
    """Return a minimal valid empty state dict for the given ruleset.

    This is used by ``StateAPI.create()`` to bootstrap a new campaign.
    """
    now = datetime.now(UTC).isoformat()
    return {
        "campaign": {
            "id": "",
            "name": "",
            "ruleset": ruleset,
            "created_at": now,
            "session_count": 0,
        },
        "party": {
            "gold": 0,
            "members": [],
            "shared_inventory": [],
            "reputation": {},
            "world_flags": [],
        },
        "characters": {},
        "npcs": {},
        "monsters": {},
        "combat": None,
        "combat_history": [],
        "quests": {},
        "world": {
            "current_scene": None,
            "current_location": None,
            "timeline": [],
            "flags": {},
            "time_of_day": "morning",
            "day": 1,
            "weather": None,
        },
        "metadata": {
            "version": STATE_FORMAT_VERSION,
            "last_modified": now,
            "changelog_entries": 0,
            "schema_version": CURRENT_SCHEMA_VERSION,
        },
    }


# ---------------------------------------------------------------------------
# StateStore — in-memory state holder with file persistence
# ---------------------------------------------------------------------------


class StateStore:
    """Holds in-memory state and provides load/save/validate operations.

    The ``StateAPI`` class wraps this store and provides the public API.
    """

    def __init__(
        self,
        state_path: Path,
        schema_path: Path | None = None,
    ) -> None:
        """Load state from *state_path*, validate, and hold in memory.

        Args:
            state_path: Path to ``state.json``.
            schema_path: Path to ``state_schema.json``. If None, uses the
                         master schema shipped with the module.

        Raises:
            StateFileNotFoundError: If state.json doesn't exist.
            StateValidationError: If the loaded state fails validation.
            StateCorruptionError: If state.json is not valid JSON.
        """
        self._state_path: Path = Path(state_path)
        self._schema_path: Path | None = (
            Path(schema_path) if schema_path else None
        )
        self._dirty: bool = False

        # Load the schema once
        self._schema: dict[str, Any] = load_schema(self._schema_path)

        # Load state
        if not self._state_path.exists():
            raise StateFileNotFoundError(
                f"State file not found: {self._state_path}"
            )

        try:
            with open(self._state_path, encoding="utf-8") as f:
                self._data: dict[str, Any] = json.load(f)
        except json.JSONDecodeError as e:
            raise StateCorruptionError(
                f"State file is not valid JSON: {e}"
            ) from e

        # Validate loaded state (non-fatal for load — warns via verify)
        errors = validate_state(self._data, self._schema)
        if errors:
            # We still load, but mark the issues for verify()
            self._load_errors = errors
        else:
            self._load_errors = []

    @classmethod
    def create(
        cls,
        state_path: Path,
        campaign: dict[str, Any],
        ruleset: str,
        schema_path: Path | None = None,
    ) -> StateStore:
        """Create a new state file at *state_path*.

        Args:
            state_path: Where to create ``state.json``.
            campaign: Campaign info dict (id, name, ruleset).
            ruleset: Active ruleset ID.
            schema_path: Path to schema file to copy into campaign dir.

        Returns:
            A new StateStore with the created state.
        """
        state_path = Path(state_path)
        state_path.parent.mkdir(parents=True, exist_ok=True)

        # Build empty state
        state = empty_state(ruleset)
        state["campaign"].update(campaign)
        state["campaign"]["ruleset"] = campaign.get("ruleset", ruleset)

        # Copy schema to campaign dir if schema_path provided
        if schema_path is not None:
            dest_schema = state_path.parent / "state_schema.json"
            shutil.copy2(schema_path, dest_schema)

        # Write initial state
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)

        return cls(state_path, schema_path)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def dirty(self) -> bool:
        """True if there are unsaved in-memory changes."""
        return self._dirty

    @property
    def data(self) -> dict[str, Any]:
        """Return a deep copy of the full state dict."""
        return deepcopy(self._data)

    @property
    def load_errors(self) -> list[str]:
        """Validation errors found on load (empty if clean)."""
        return list(self._load_errors)

    # ------------------------------------------------------------------
    # Read helpers — return references to internal data (CARE: deepcopy
    # at API layer, not here, for performance)
    # ------------------------------------------------------------------

    def get_raw(self) -> dict[str, Any]:
        """Return the raw internal state dict (NOT a copy).

        WARNING: Callers must not mutate the returned dict directly.
        All mutations should go through the API layer which marks dirty.
        """
        return self._data

    def get_schema(self) -> dict[str, Any]:
        """Return the loaded JSON Schema dict."""
        return self._schema

    # ------------------------------------------------------------------
    # Mark dirty
    # ------------------------------------------------------------------

    def mark_dirty(self) -> None:
        """Mark the state as having unsaved changes."""
        self._dirty = True

    # ------------------------------------------------------------------
    # Save (atomic write)
    # ------------------------------------------------------------------

    def save(self) -> None:
        """Persist in-memory state to disk atomically.

        No-op if state is not dirty (no mutations since last save).
        Uses .tmp + os.replace() for crash-safe atomic write.

        Raises:
            StateValidationError: If state fails validation before write.
        """
        if not self._dirty:
            return

        # Validate before write
        errors = validate_state(self._data, self._schema)
        if errors:
            raise StateValidationError(errors)

        # Update metadata
        self._data["metadata"]["last_modified"] = datetime.now(
            UTC
        ).isoformat()
        self._data["metadata"]["version"] = STATE_FORMAT_VERSION
        self._data["metadata"]["schema_version"] = CURRENT_SCHEMA_VERSION

        # Atomic write: write to .tmp, then os.replace
        tmp_path = self._state_path.with_suffix(".json.tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, self._state_path)
        except Exception:
            # Clean up tmp on failure
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)  # type: ignore[attr-defined]
            raise

        self._dirty = False

    # ------------------------------------------------------------------
    # Validate
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        """Run full validation on current state. Returns error list."""
        return validate_state(self._data, self._schema)

    # ------------------------------------------------------------------
    # Reload (discard in-memory changes)
    # ------------------------------------------------------------------

    def reload(self) -> None:
        """Reload state from disk, discarding all in-memory changes."""
        if not self._state_path.exists():
            raise StateFileNotFoundError(
                f"State file not found: {self._state_path}"
            )
        with open(self._state_path, encoding="utf-8") as f:
            self._data = json.load(f)
        self._dirty = False
        self._load_errors = []
