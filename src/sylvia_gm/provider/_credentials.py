"""Credential pool for multi-key rotation across providers.

Manages multiple API keys for a single provider endpoint, rotating
through active (non-depleted) credentials round-robin. Rate-limited
keys are temporarily depleted; auth-failed keys are permanently
marked invalid.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Credential:
    """A single authentication credential."""

    type: str  # "api_key" | "aws_profile" | "none" | "oauth_token"
    value: str  # the API key, token, or profile name
    label: str = ""  # human-readable label (e.g., "Work key", "Personal key")
    depleted: bool = False  # set True after 429 exhaustion
    depleted_at: float = 0.0  # monotonic timestamp when marked depleted
    cooldown_seconds: float = 60.0  # wait before retrying depleted credential
    invalid: bool = False  # set True after 401/403 — permanent


class CredentialExhaustedError(Exception):
    """All credentials in the pool are depleted or invalid."""


class CredentialPool:
    """Manages multiple credentials for a single provider.

    Rotation strategy:
    - Round-robin through active (non-depleted, non-invalid) credentials
    - When a credential gets a 429, mark it depleted for cooldown_seconds
    - If ALL credentials are depleted, raise CredentialExhaustedError
      (which triggers provider-level fallback in FallbackChain)
    - Auth errors (401/403) on ANY credential mark that credential as
      permanently invalid
    """

    def __init__(
        self,
        credentials: list[Credential],
        time_source: Callable[[], float] | None = None,
    ) -> None:
        """Initialise the credential pool.

        Args:
            credentials: List of Credential objects.
            time_source: Optional callable returning monotonic seconds.
                         Defaults to time.monotonic. Inject fake_time for tests.
        """
        if not credentials:
            raise ValueError("CredentialPool requires at least one credential")
        self._credentials = credentials
        self._index: int = 0
        self._lock = asyncio.Lock()
        self._time = time_source or time.monotonic

    async def get_credential(self) -> Credential:
        """Return the next active credential (round-robin).

        Raises CredentialExhaustedError if all credentials are depleted/invalid.
        """
        async with self._lock:
            # Check for undepleted credentials
            now = self._time()
            for cred in self._credentials:
                if cred.depleted and now - cred.depleted_at >= cred.cooldown_seconds:
                    cred.depleted = False
                    cred.depleted_at = 0.0

            active = [c for c in self._credentials if not c.depleted and not c.invalid]
            if not active:
                raise CredentialExhaustedError(
                    f"All {len(self._credentials)} credentials are depleted or invalid"
                )

            # Round-robin
            start_idx = self._index % len(active)
            cred = active[start_idx]
            self._index = (self._index + 1) % len(active)
            return cred

    async def mark_depleted(self, credential: Credential) -> None:
        """Mark a credential as rate-limited (429)."""
        async with self._lock:
            credential.depleted = True
            credential.depleted_at = self._time()

    async def mark_invalid(self, credential: Credential) -> None:
        """Mark a credential as permanently invalid (401/403)."""
        async with self._lock:
            credential.invalid = True

    @property
    def active_count(self) -> int:
        """Number of non-depleted, non-invalid credentials."""
        now = self._time()
        return sum(
            1
            for c in self._credentials
            if not c.invalid
            and not (c.depleted and now - c.depleted_at < c.cooldown_seconds)
        )
