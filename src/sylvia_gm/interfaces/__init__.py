"""F6 application interfaces — local owner, API, and stream surfaces.

These are the surfaces the Tauri desktop host and any second
instance use to route commands to the local owner process.  Clients never
receive the SQLite handle; they send commands and render projections.
"""

from __future__ import annotations

from sylvia_gm.interfaces.local_owner import (
    DEFAULT_DISCONNECT_GRACE_SECONDS,
    LocalOwnerSupervisor,
    NarrationLease,
    OwnerLaunchDecision,
)

__all__ = (
    "DEFAULT_DISCONNECT_GRACE_SECONDS",
    "LocalOwnerSupervisor",
    "NarrationLease",
    "OwnerLaunchDecision",
)
