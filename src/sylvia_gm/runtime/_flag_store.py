"""FlagStore — persistent campaign-level boolean flag store.

Flags are stored on the _campaign entity via StateBridge.
Before condition evaluation, active flags are injected as
ctx.vars["_flag_X"] = True (LOCK-2).
"""

from __future__ import annotations

from typing import Any


class FlagStore:
    """Persistent campaign-level boolean flag store.

    Flags are stored on the _campaign entity via StateBridge.
    Before condition evaluation, active flags are injected as
    ctx.vars["_flag_X"] = True.

    Usage::

        store = FlagStore(state_bridge)
        store.set("dragon_defeated", True)
        store.set("chapter_2")
        assert store.has("dragon_defeated") is True
        store.clear("dragon_defeated")
        assert store.has("dragon_defeated") is False
    """

    def __init__(self, state_bridge: Any = None) -> None:
        """Initialize the flag store.

        Args:
            state_bridge: StateBridge instance for persistence.
                If None, flags are stored in memory only.
        """
        self._state_bridge = state_bridge
        self._flags: dict[str, bool] = {}
        self._load()

    def _load(self) -> None:
        """Load flags from _campaign entity."""
        if self._state_bridge is None:
            return
        try:
            self._state_bridge._ensure_campaign_entity()
            flags = self._state_bridge.read_field("_campaign", "flags") or {}
            if isinstance(flags, dict):
                self._flags = dict(flags)
        except Exception:
            self._flags = {}

    def _save(self) -> None:
        """Persist flags to _campaign entity via write_field."""
        if self._state_bridge is None:
            return
        try:
            self._state_bridge.write_field("_campaign", "flags", dict(self._flags))
        except Exception:
            pass

    def set(self, flag: str, value: bool = True) -> None:
        """Set a campaign flag. Persisted through StateBridge.

        Args:
            flag: Flag name (e.g., "dragon_defeated").
            value: True to set, False to clear.
        """
        self._flags[flag] = value
        self._save()

    def clear(self, flag: str) -> None:
        """Remove a flag entirely.

        Args:
            flag: Flag name to remove.
        """
        self._flags.pop(flag, None)
        self._save()

    def has(self, flag: str) -> bool:
        """Check if a flag is set to True.

        Args:
            flag: Flag name to check.

        Returns:
            True if the flag exists and is True.
        """
        return self._flags.get(flag, False)

    def all_flags(self) -> dict[str, bool]:
        """Return all current flags (for context injection + State Memo).

        Returns:
            Dict of flag_name → bool.
        """
        return dict(self._flags)
