"""Authorize an exact update restart before migration; acknowledge only usable app startup."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

from .launch import Installation
from .manifest import UpdateError, Version, bounded_json
from .restart import RestartHandshake
from .storage import UpdateStore, checked_file, operation_id

if TYPE_CHECKING:
    from .preparation import PreparedUpdate


class _RestartContext(TypedDict):
    nonce: str
    live: Path
    desktop_version: str
    owner_version: str
    target: str
    distribution: str
    storage_compatibility: int

_UNSAFE_ORDINARY_START = frozenset(
    {"closing", "applying", "awaiting_restart", "recovery_required", "rolling_back",
     "rollback_ready", "rollback_awaiting_restart"}
)


@dataclass
class AuthorizedStartup:
    prepared: PreparedUpdate
    handshake: RestartHandshake
    nonce: str
    installation: Installation
    version: str
    desktop_version: str
    compatibility: int
    retained_compatibility: int
    recovery: bool = False
    completed: bool = False

    def _context(self) -> _RestartContext:
        return {
            "nonce": self.nonce, "live": self.installation.live,
            "desktop_version": self.desktop_version, "owner_version": self.version,
            "target": self.installation.target, "distribution": self.installation.distribution,
            "storage_compatibility": self.compatibility,
        }

    def confirm(self) -> bool:
        """The real new owner calls this only after the native main app reports usable startup."""
        self.installation.check(self.installation.target, self.installation.distribution)
        if self.completed:
            return True
        self.prepared.require_selected(checkpoint_compatibility=self.retained_compatibility)
        self.handshake.confirm(**self._context())
        # The helper alone finalizes the global operation. Until it does, owner admission
        # remains closed; this prevents a late helper write overtaking a user's next check.
        row = self.prepared.store.load()
        if row is None or row["operation_id"] != self.prepared.identity:
            raise UpdateError("changed_restart", "The startup operation changed before completion.")
        if row["phase"] == ("rolled_back" if self.recovery else "confirmed"):
            self.completed = True
            return True
        error = self.handshake.error_receipt
        if error.exists() or error.is_symlink():
            checked_file(error, maximum=4096)
            raise UpdateError(
                "update_completion_failed", "The update helper could not finish. Use recovery.",
            )
        return False


def authorize_startup(
    data: Path,
    installation: Installation | None,
    request: object,
    *,
    running: Version,
    desktop_version: str,
    storage_compatibility: int,
    verify: Callable[[str, bytes, bytes], None],
) -> AuthorizedStartup | None:
    """Read update-owned bytes only. No owner acquisition, SQL migration, apply or launch."""
    update_root = data / ".application-updates"
    journal = update_root / "current.json"
    if not journal.exists() and not journal.is_symlink():
        if request is not None:
            raise UpdateError("invalid_restart", "This update restart has no retained operation.")
        return None
    row = UpdateStore(update_root).load()
    assert row is not None
    if request is None:
        if row["phase"] in _UNSAFE_ORDINARY_START:
            raise UpdateError(
                "update_recovery_required",
                "Reconcile the interrupted application update before opening saved data.",
            )
        return None
    recovery = isinstance(request, dict) and request.get("recovery") is True
    request_keys = {"operation_id", "attempt", "nonce"} | ({"recovery"} if recovery else set())
    permitted_phases = (
        {"rollback_awaiting_restart", "rolled_back"} if recovery
        else {"awaiting_restart", "recovery_required", "confirmed"}
    )
    if (
        not isinstance(request, dict)
        or set(request) != request_keys
        or row["phase"] not in permitted_phases
        or installation is None
        or installation.data != data
    ):
        raise UpdateError("invalid_restart", "The native update restart context is unavailable.")
    identity, attempt, nonce = (
        operation_id(request[name]) for name in ("operation_id", "attempt", "nonce")
    )
    if identity != row["operation_id"] or desktop_version != running.text:
        raise UpdateError("wrong_restart", "The selected app and update operation do not match.")
    installation.check(installation.target, installation.distribution)
    stage = update_root / identity / "attempts" / attempt
    binding = bounded_json(checked_file(stage / "prepared-update.json", maximum=8192))
    if not isinstance(binding, dict) or not isinstance(binding.get("checkpoint"), dict):
        raise UpdateError("invalid_restart", "The retained restart preparation is incomplete.")
    old = Version.parse(binding.get("current_version"))
    old_compatibility = binding["checkpoint"].get("storage_compatibility")
    if type(old_compatibility) is not int or not 0 < old_compatibility < 2**31:
        raise UpdateError("invalid_checkpoint", "The retained storage contract is invalid.")

    from .preparation import PreparedUpdate
    from .replacement import file_matches

    prepared = PreparedUpdate(
        data, installation.live, identity, attempt, current=old, target=installation.target,
        distribution=installation.distribution, verify=verify,
    )
    prepared.require_selected(checkpoint_compatibility=old_compatibility)
    expected_version = old.text if recovery else prepared.manifest.version.text
    if expected_version != running.text or row["version"] != prepared.manifest.version.text:
        raise UpdateError("wrong_restart", "The running owner is not the signed update version.")
    if recovery:
        prepared.replacement().verify_rollback()
        if storage_compatibility != old_compatibility:
            raise UpdateError(
                "wrong_restart", "The restored owner's data contract is inconsistent.",
            )
    else:
        for payload in prepared.manifest.files:
            if not file_matches(installation.live / payload.path, payload):
                raise UpdateError("application_changed", "The updated application is incomplete.")
    handshake = RestartHandshake(data, identity, attempt, recovery=recovery)
    result = AuthorizedStartup(
        prepared, handshake, nonce, installation, running.text, desktop_version,
        storage_compatibility, old_compatibility, recovery=recovery,
    )
    handshake.authorize(**result._context())
    return result


def owner_startup_context(
    launch: Mapping[str, object],
) -> tuple[Installation | None, AuthorizedStartup | None]:
    """Only the private native launch envelope supplies app identity and restart arguments."""
    from sylvia_gm import __version__

    from .identity import STORAGE_COMPATIBILITY
    from .manifest import KeyRing
    from .service import PUBLIC_KEYS

    raw_database = launch.get("database_path")
    if not isinstance(raw_database, str) or not raw_database:
        raise UpdateError("invalid_restart", "The selected authority database is unavailable.")
    data = Path(raw_database).expanduser().resolve().parent
    installation = None
    native = launch.get("desktop_update_context")
    if native is not None:
        # No update capability; an ordinary launch can still play safely.
        with suppress(UpdateError):
            installation = Installation.from_launch(native, data)
    start = authorize_startup(
        data, installation, launch.get("desktop_update_start"),
        running=Version.parse(__version__), desktop_version=__version__,
        storage_compatibility=STORAGE_COMPATIBILITY, verify=KeyRing(PUBLIC_KEYS).verify,
    )
    return installation, start
