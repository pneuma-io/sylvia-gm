"""F6 launch handshake — ephemeral per-launch secret for authenticated ownership.

The desktop supervisor generates one short-lived secret per launch and proves
to itself that the local owner process it spawned is the genuine owner — not a
stray process that grabbed the loopback port.  The secret is never written to
URLs, logs, or persistent storage; it lives only in supervisor memory and is
erased once the handshake completes.

Contract
--------
1. ``LaunchHandshake.issue()`` returns a one-time bearer secret plus a derived
   challenge token.  The supervisor injects the secret into the owner process
   via an ephemeral channel (environment variable, named-pipe FD, or stdin
   payload) that never touches URLs or log files.
2. The owner process derives a response token (HMAC-SHA256 of the secret with a
   fixed transcript label) and exposes it via the authenticated ``/handshake``
   surface.
3. ``LaunchHandshake.verify()`` confirms the response without ever logging the
   secret.  A mismatch raises ``LaunchHandshakeError`` so the supervisor can
   refuse to attach and surface a clear recovery action.
4. After verification the secret is erased from memory; subsequent calls raise.

This module is deliberately free of process/IO concerns — it owns only the
cryptographic transcript.  The supervisor wires the transport.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

_HANDSHAKE_TRANSCRIPT = b"sylvia-gm-f6-local-owner-handshake-v1"


class LaunchHandshakeError(RuntimeError):
    """The launch handshake failed — refuse to attach to the owner process."""


@dataclass(frozen=True)
class _Sealed:
    """Internal sealed secret — never logged, never serialized."""

    secret: bytes
    expected_response: str


@dataclass(frozen=True)
class OwnerCredential:
    """The credential the supervisor injects into the owner process."""

    #: A 256-bit ephemeral bearer secret encoded as 64 lowercase hex chars.
    secret_hex: str

    #: The HMAC-SHA256 response token the owner must expose to prove possession.
    expected_response_hex: str


class LaunchHandshake:
    """One-shot per-launch handshake state owned by the desktop supervisor.

    Lifecycle: ``issue()`` → inject secret → ``verify(response)`` → sealed.
    Re-calling ``issue`` verifies exactly once and then locks.
    """

    __slots__ = ("_sealed", "_done")

    def __init__(self) -> None:
        self._sealed: _Sealed | None = None
        self._done: bool = False

    def __repr__(self) -> str:
        return "LaunchHandshake(<sealed>)"

    def issue(self) -> OwnerCredential:
        """Generate a fresh per-launch credential.  May be called exactly once."""

        if self._done:
            raise LaunchHandshakeError("Launch handshake already consumed")
        if self._sealed is not None:
            raise LaunchHandshakeError("Launch handshake already issued")
        secret = secrets.token_bytes(32)
        response = hmac.new(secret, _HANDSHAKE_TRANSCRIPT, hashlib.sha256).hexdigest()
        self._sealed = _Sealed(secret=secret, expected_response=response)
        return OwnerCredential(
            secret_hex=secret.hex(),
            expected_response_hex=response,
        )

    def verify(self, response_hex: str) -> None:
        """Prove the owner process produced the expected response token.

        Raises ``LaunchHandshakeError`` on any mismatch or reuse.  After a
        successful (or failed) verify the secret is erased and the handshake
        will not issue again.
        """

        sealed = self._sealed
        # Always erase first so a timing oracle cannot reuse the secret.
        self._sealed = None
        self._done = True
        if sealed is None:
            raise LaunchHandshakeError("Launch handshake was not issued or already consumed")
        if not isinstance(response_hex, str) or not response_hex:
            raise LaunchHandshakeError("Owner response is missing")
        if not hmac.compare_digest(
            hmac.new(sealed.secret, _HANDSHAKE_TRANSCRIPT, hashlib.sha256).hexdigest(),
            response_hex,
        ):
            raise LaunchHandshakeError("Owner response did not match the per-launch secret")
        # success — secret already erased by clearing _sealed above.

    @property
    def issued(self) -> bool:
        return self._sealed is not None

    @property
    def consumed(self) -> bool:
        return self._done and self._sealed is None


def derive_owner_response(secret_hex: str) -> str:
    """Owner-side helper: derive the response token from the injected secret.

    The owner process receives the 64-char hex secret via the ephemeral
    channel, calls this helper, and exposes only the response hex on the
    ``/handshake`` surface.  The raw secret never leaves the owner process
    after derivation.
    """

    if not isinstance(secret_hex, str) or len(secret_hex) != 64:
        raise LaunchHandshakeError("Owner secret must be 64 hex characters")
    try:
        secret = bytes.fromhex(secret_hex)
    except ValueError as error:
        raise LaunchHandshakeError("Owner secret is not valid hexadecimal") from error
    return hmac.new(secret, _HANDSHAKE_TRANSCRIPT, hashlib.sha256).hexdigest()
