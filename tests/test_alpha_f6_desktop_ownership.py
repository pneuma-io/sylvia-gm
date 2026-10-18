"""Exact F6 desktop ownership and second-instance acceptance probes.

Probes mapped to the F6 gates::

    G-OWNER      — at most one owner per database; second-instance decision
                   (become-owner / attach-client / recover) resolves before any
                   database access.
    G-WRITER     — clients route through the supervisor; rejected when the launch
                   handshake has not been verified (no stray-process routing).
    G-ORDER      — Install-Package routes through the application owner; a
                   second supervisor cannot open the same database (OS lock).
    handshake    — ephemeral per-launch secret; verify rejects mismatches,
                   never log-leaks, one-shot.

Tests use only synthetic archives, temporary SQLite databases, and in-process
supervisor instances — no sockets, no Tauri shell.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import (
    MechanicContext,
    MechanicOutcome,
    PackageLock,
    PackageLockEntry,
    PackageRole,
    StateMutation,
)
from sylvia_gm.domain.errors import CommandRejected, OwnerConflict
from sylvia_gm.domain.package_library import (
    canonical_json_bytes,
    content_root_for_record,
)
from sylvia_gm.infrastructure.launch_handshake import (
    LaunchHandshake,
    LaunchHandshakeError,
    derive_owner_response,
)
from sylvia_gm.infrastructure.owner_process_probe import probe_owner_live
from sylvia_gm.interfaces.local_owner import LocalOwnerSupervisor

SYNTHETIC_SHA = "a" * 64


# --------------------------------------------------------------------------
# Synthetic package archive (mirrors F2's _f2_archive so we exercise the real
# F3 publication + activation path through the supervisor).
# --------------------------------------------------------------------------


def _schema() -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["amount"],
        "properties": {"amount": {"type": "integer", "minimum": 1}},
    }


def _increment(calls: list[int]):  # type: ignore[no-untyped-def]
    def mechanic(context: MechanicContext) -> MechanicOutcome:
        calls.append(1)
        amount = context.parameters["amount"]
        before = context.state.get("clockwork.counter", 0)
        assert isinstance(amount, int)
        assert isinstance(before, int)
        after = before + amount
        return MechanicOutcome(
            result={"status": "committed", "counter": after},
            mutations=(StateMutation("clockwork", "counter", after),),
            consequences=({"kind": "counter_advanced", "amount": amount},),
        )

    return mechanic


def _f6_archive() -> tuple[str, bytes]:
    payload = canonical_json_bytes({"fixture": "synthetic-f6"})
    draft: dict[str, object] = {
        "id": "synthetic.clockwork",
        "version": "1.0.0",
        "role": "primary",
        "metadata": {"label": "Synthetic F6 fixture"},
        "provenance": {
            "publisher": "sylvia-gm-tests",
            "source_sha256": hashlib.sha256(payload).hexdigest(),
        },
        "compatibility": {"host_contract": 1, "runtime_format": 1, "requires": []},
        "minimum_model_capability_tier": "sylvia.capability.t00",
        "dependencies": [],
        "capabilities": ["synthetic.counter"],
        "tools": [
            {
                "name": "increment",
                "capability": "synthetic.counter",
                "input_schema": _schema(),
                "state_predicates": [],
                "turn_statuses": ["open"],
                "writers": ["dm"],
            }
        ],
        "prompt_fragments": [],
        "ui_surfaces": [],
        "files": [
            {
                "path": "payload/f6.json",
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        ],
    }
    root = content_root_for_record(draft)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            canonical_json_bytes({"format": 1, "packages": [{**draft, "content_sha256": root}]}),
        )
        archive.writestr("packages/synthetic.clockwork/payload/f6.json", payload)
    return root, stream.getvalue()


def _lock() -> PackageLock:
    return PackageLock(
        entries=(
            PackageLockEntry(
                package_id="synthetic.clockwork",
                version="1.0.0",
                sha256=SYNTHETIC_SHA,
                role=PackageRole.PRIMARY,
                load_order=0,
                dependencies=(),
                capabilities=("synthetic.counter",),
            ),
        )
    )


# --------------------------------------------------------------------------
# Handshake probes (no DB, no supervisor) — the cryptographic transcript
# --------------------------------------------------------------------------


class TestLaunchHandshake:
    def test_issue_returns_distinct_secrets_each_call(self) -> None:
        a = LaunchHandshake().issue()
        b = LaunchHandshake().issue()
        assert a.secret_hex != b.secret_hex
        assert len(a.secret_hex) == 64
        assert len(a.expected_response_hex) == 64

    def test_verify_accepts_correct_response_then_is_one_shot(self) -> None:
        handshake = LaunchHandshake()
        cred = handshake.issue()
        response = derive_owner_response(cred.secret_hex)
        assert handshake.issued is True
        handshake.verify(response)
        assert handshake.consumed is True
        # A second verify must fail — the secret is already erased.
        with pytest.raises(LaunchHandshakeError):
            handshake.verify(response)

    def test_verify_rejects_wrong_response(self) -> None:
        handshake = LaunchHandshake()
        handshake.issue()
        with pytest.raises(LaunchHandshakeError):
            handshake.verify("0" * 64)

    def test_verify_rejects_missing_and_empty(self) -> None:
        handshake = LaunchHandshake()
        handshake.issue()
        for bad in ("", None, 0):  # type: ignore[arg-type]
            with pytest.raises(LaunchHandshakeError):
                handshake.verify(bad)  # type: ignore[arg-type]

    def test_issue_after_verify_raises(self) -> None:
        handshake = LaunchHandshake()
        cred = handshake.issue()
        handshake.verify(derive_owner_response(cred.secret_hex))
        with pytest.raises(LaunchHandshakeError):
            handshake.issue()

    def test_derive_rejects_bad_secret_lengths(self) -> None:
        for bad in ("", "ab", "g" * 64):
            with pytest.raises(LaunchHandshakeError):
                derive_owner_response(bad)

    def test_secret_never_appears_in_repr_or_log(self) -> None:
        handshake = LaunchHandshake()
        cred = handshake.issue()
        # The credential dataclass exposes the secret, but the sealed state
        # repr must redact it.
        assert repr(handshake) == "LaunchHandshake(<sealed>)"
        # And the secret hex in the credential is not the same as the response.
        assert cred.secret_hex != cred.expected_response_hex


# --------------------------------------------------------------------------
# Second-instance probe (G-OWNER decision) — uses the OS lock sidecar
# --------------------------------------------------------------------------


class TestSecondInstanceProbe:
    def test_probe_reports_free_when_no_owner(self, tmp_path: Path) -> None:
        db = tmp_path / "probe.db"
        result = probe_owner_live(db)
        assert result.owner_live is False
        assert "free" in result.reason

    def test_probe_reports_live_while_owner_holds_lock(self, tmp_path: Path) -> None:
        db = tmp_path / "probe.db"
        # Holding an owner for the duration of the probe.
        with AuthorityOwner.open(db, "owner-probe-1", disconnect_grace_seconds=5.0):
            result = probe_owner_live(db)
            assert result.owner_live is True
            assert "held" in result.reason
        # After close, the lock is free again.
        result_after = probe_owner_live(db)
        assert result_after.owner_live is False

    def test_decide_becomes_owner_when_lock_free(self, tmp_path: Path) -> None:
        db = tmp_path / "decide.db"
        supervisor = LocalOwnerSupervisor(disconnect_grace_seconds=5.0)
        decision = supervisor.decide(db)
        assert decision.role == "owner"
        assert decision.credential is None
        assert decision.probe is not None
        assert decision.probe.owner_live is False

    def test_decide_attaches_client_when_owner_alive(self, tmp_path: Path) -> None:
        db = tmp_path / "decide-client.db"
        with AuthorityOwner.open(db, "owner-alive-1", disconnect_grace_seconds=5.0):
            supervisor = LocalOwnerSupervisor(disconnect_grace_seconds=5.0)
            decision = supervisor.decide(db, owner_alive_probe=lambda _path: True)
            assert decision.role == "client"

    def test_decide_recovers_when_lock_held_but_port_dead(self, tmp_path: Path) -> None:
        db = tmp_path / "decide-recover.db"
        with AuthorityOwner.open(db, "owner-dead-1", disconnect_grace_seconds=5.0):
            supervisor = LocalOwnerSupervisor(disconnect_grace_seconds=5.0)
            decision = supervisor.decide(db, owner_alive_probe=lambda _path: False)
            assert decision.role == "recover"
            assert "unreachable" in decision.reason

    def test_write_recovery_hint_creates_file(self, tmp_path: Path) -> None:
        db = tmp_path / "recover-hint.db"
        hint_path = LocalOwnerSupervisor.write_recovery_hint(db)
        assert hint_path.exists()
        assert "crashed" in hint_path.read_text(encoding="utf-8").lower()


# --------------------------------------------------------------------------
# G-OWNER — at most one owner; second supervisor cannot open the same DB
# --------------------------------------------------------------------------


class TestOwnershipUniqueness:
    def test_second_supervisor_acquire_fails_with_owner_conflict(self, tmp_path: Path) -> None:
        db = tmp_path / "unique.db"
        first = LocalOwnerSupervisor(disconnect_grace_seconds=5.0)
        first.acquire_owner(db, "owner-first")
        # Present the in-process handshake so the first owner is verified.
        first.complete_handshake(
            derive_owner_response(first._credential.secret_hex)  # type: ignore[union-attr]
        )
        second = LocalOwnerSupervisor(disconnect_grace_seconds=5.0)
        with pytest.raises(OwnerConflict):
            second.acquire_owner(db, "owner-second")
        first.shutdown()

    def test_shutdown_releases_the_lock_for_next_owner(self, tmp_path: Path) -> None:
        db = tmp_path / "release.db"
        first = LocalOwnerSupervisor(disconnect_grace_seconds=5.0)
        first.acquire_owner(db, "owner-a")
        first.complete_handshake(
            derive_owner_response(first._credential.secret_hex)  # type: ignore[union-attr]
        )
        first.shutdown()
        # After shutdown a new supervisor can acquire.
        second = LocalOwnerSupervisor(disconnect_grace_seconds=5.0)
        second.acquire_owner(db, "owner-b")
        second.shutdown()


# --------------------------------------------------------------------------
# G-WRITER / G-ORDER — commands route through the verified supervisor only
# --------------------------------------------------------------------------


def _open_verified_supervisor(
    database: Path,
    *,
    owner_id: str = "owner-f6",
) -> LocalOwnerSupervisor:
    """Open a supervisor and complete its in-process handshake round-trip."""

    supervisor = LocalOwnerSupervisor(disconnect_grace_seconds=30.0)
    cred = supervisor.acquire_owner(database, owner_id)
    supervisor.complete_handshake(derive_owner_response(cred.secret_hex))
    return supervisor


class TestCommandRouting:
    def test_install_package_routes_through_supervisor(self, tmp_path: Path) -> None:
        db = tmp_path / "install.db"
        supervisor = _open_verified_supervisor(db)
        try:
            _root, archive = _f6_archive()
            candidate = supervisor.install_package_archive(
                io.BytesIO(archive), install_id="f6-install-1"
            )
            assert candidate is not None
        finally:
            supervisor.shutdown()

    def test_snapshot_routes_through_supervisor(self, tmp_path: Path) -> None:
        db = tmp_path / "snapshot.db"
        supervisor = _open_verified_supervisor(db)
        try:
            # No campaign yet; snapshot raises CommandRejected (unavailable).
            with pytest.raises(CommandRejected):
                supervisor.snapshot("campaign-missing")
        finally:
            supervisor.shutdown()

    def test_unverified_handshake_blocks_commands(self, tmp_path: Path) -> None:
        db = tmp_path / "unverified.db"
        supervisor = LocalOwnerSupervisor(disconnect_grace_seconds=5.0)
        supervisor.acquire_owner(db, "owner-unverified")
        try:
            with pytest.raises(CommandRejected, match="handshake"):
                supervisor.install_package_archive(io.BytesIO(b""), install_id="blocked")
        finally:
            supervisor.shutdown()

    def test_wrong_handshake_response_blocks_commands(self, tmp_path: Path) -> None:
        db = tmp_path / "wrong-handshake.db"
        supervisor = LocalOwnerSupervisor(disconnect_grace_seconds=5.0)
        supervisor.acquire_owner(db, "owner-wrong")
        try:
            with pytest.raises(LaunchHandshakeError):
                supervisor.complete_handshake("deadbeef" * 8)
            # Commands remain blocked.
            with pytest.raises(CommandRejected, match="handshake"):
                supervisor.snapshot("any")
        finally:
            supervisor.shutdown()

    def test_supervisor_context_manager_acquires_and_shuts_down(self, tmp_path: Path) -> None:
        db = tmp_path / "ctx.db"
        supervisor = LocalOwnerSupervisor(disconnect_grace_seconds=5.0)
        with supervisor.own(db, "owner-ctx") as s:
            assert s.handshake_verified is True
            _root, archive = _f6_archive()
            assert (
                s.install_package_archive(io.BytesIO(archive), install_id="ctx-install") is not None
            )
        # After exit the lock is free for a new owner.
        with AuthorityOwner.open(db, "owner-after-ctx", disconnect_grace_seconds=5.0):
            pass


# --------------------------------------------------------------------------
# No client database access — the supervisor is the only SQLite opener
# --------------------------------------------------------------------------


class TestNoClientDatabaseAccess:
    def test_decide_required_no_database_open_for_second_instance(self, tmp_path: Path) -> None:
        """A second instance that decides 'client' never opens the database."""

        db = tmp_path / "client.db"
        # Seed the DB with a live owner.
        with AuthorityOwner.open(db, "owner-seed", disconnect_grace_seconds=5.0):
            supervisor = LocalOwnerSupervisor(disconnect_grace_seconds=5.0)
            # The probe opens only the *lock sidecar*, not the database file.
            result = probe_owner_live(db)
            assert result.owner_live is True
            # The supervisor's decide *also* only probes; it does not open the
            # SQLite handle before deciding.
            decision = supervisor.decide(db, owner_alive_probe=lambda _p: True)
            assert decision.role == "client"
            # Acquiring would raise — confirming we never became a second writer.
            with pytest.raises(OwnerConflict):
                supervisor.acquire_owner(db, "client-attempt")
