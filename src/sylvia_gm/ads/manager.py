"""AdManager — controls ad delivery across the three-tier model."""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AdEntry:
    """A single sponsor ad entry loaded from ad_sponsors.json."""

    name: str
    image_url: str
    link: str
    impressions: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "image_url": self.image_url,
            "link": self.link,
            "impressions": self.impressions,
        }


class AdManager:
    """Manages the three-tier ad experience.

    Tiers:
      1. Splash ad — shown once per session during loading.
      2. Companion ad — sidebar ad that rotates every ~10 minutes.
      3. Disable toggle — respectful opt-out with a friendly message.
    """

    _DEFAULT_COMPANION_INTERVAL: float = 600.0  # 10 minutes

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self._enabled: bool = cfg.get("ads_enabled", True)
        self._splash_dismissed: bool = False
        self._companion_interval: float = cfg.get(
            "companion_interval", self._DEFAULT_COMPANION_INTERVAL
        )

        # Companion rotation tracking
        self._last_companion_at: float = 0.0
        self._last_companion_index: int = -1

        # Pool of sponsor entries
        self._sponsors: list[AdEntry] = []
        sponsors_path = cfg.get("sponsors_path", None)
        if sponsors_path:
            self._load_sponsors(Path(sponsors_path))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_splash_ad(self) -> AdEntry | None:
        """Return the splash ad if enabled and not yet dismissed.

        Once returned, the splash is marked as dismissed for the session.
        """
        if not self._enabled or self._splash_dismissed or not self._sponsors:
            return None

        self._splash_dismissed = True
        # Pick a random sponsor for the splash impression
        entry = random.choice(self._sponsors)
        return entry

    def get_companion_ad(self) -> AdEntry | None:
        """Return the sidebar companion ad, rotating every ``companion_interval``.

        Returns None when ads are disabled, no sponsors are loaded, or the
        rotation interval has not elapsed since the last companion ad.
        """
        if not self._enabled or not self._sponsors:
            return None

        now = time.monotonic()
        if now - self._last_companion_at < self._companion_interval:
            return None

        self._last_companion_at = now
        # Rotate to the next sponsor (round-robin)
        self._last_companion_index = (self._last_companion_index + 1) % len(self._sponsors)
        return self._sponsors[self._last_companion_index]

    def disable(self) -> str:
        """Disable all ads and return a respectful opt-out message."""
        self._enabled = False
        self._splash_dismissed = True  # Don't show queued splash after disable
        return (
            "Ads have been disabled. Thanks for checking out Sylvia GM — "
            "your experience will now be ad-free."
        )

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """Whether ads are currently enabled."""
        return self._enabled

    @property
    def sponsors(self) -> tuple[AdEntry, ...]:
        """Immutable copy of the loaded sponsor pool."""
        return tuple(self._sponsors)

    def enable(self) -> None:
        """Re-enable ads after a disable call."""
        self._enabled = True
        self._splash_dismissed = False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_sponsors(self, path: Path) -> None:
        """Load sponsor entries from a JSON file.

        Expected format:
            [{"name": "...", "image_url": "...", "link": "..."}, ...]
        """
        if not path.is_file():
            return

        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            return

        self._sponsors = [
            AdEntry(
                name=str(item.get("name", "")),
                image_url=str(item.get("image_url", "")),
                link=str(item.get("link", "")),
                impressions=int(item.get("impressions", 0)),
            )
            for item in raw
            if isinstance(item, dict)
        ]

    def _set_now_for_test(self, elapsed: float) -> None:
        """Internal test hook — simulate that *elapsed* seconds have passed.

        Sets the last-companion timestamp so that ``time.monotonic()``
        minus ``_last_companion_at`` equals *elapsed*.  Callers control
        companion-ad rotation timing without real sleep.
        """
        self._last_companion_at = time.monotonic() - elapsed
