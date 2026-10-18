"""F3 package publication, exact closure, privacy, read-barrier, and recovery probes."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import sqlite3
import threading
import unicodedata
import zipfile
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import pytest

from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.application.package_library import project_surfaces
from sylvia_gm.domain.authority import LifecycleState, WriterRole
from sylvia_gm.domain.errors import CommandRejected, SchemaIdentityError
from sylvia_gm.domain.package_library import (
    CandidateStatus,
    PackageLockAvailability,
    canonical_json_bytes,
    content_root_for_record,
    manifest_from_record,
)
from sylvia_gm.infrastructure.package_archive import PACKAGE_CHUNK_LIMIT
from sylvia_gm.infrastructure.sqlite_authority import (
    _MIGRATION_1_SHA256,
    _MIGRATION_2_NAME,
    _MIGRATION_2_SHA256,
    _MIGRATION_3_SHA256,
    _SCHEMA_VERSION,
    _schema_identity,
)

_SENTINEL = "PRIVATE-F3-SENTINEL-C:/Users/example/private-source.pdf"


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file(path: str, data: bytes) -> dict[str, object]:
    return {"path": path, "bytes": len(data), "sha256": _sha(data)}


def _package_record(
    package_id: str,
    version: str,
    files: dict[str, bytes],
    *,
    role: str = "primary",
    dependencies: tuple[dict[str, object], ...] = (),
    capabilities: tuple[str, ...] = (),
    tools: tuple[dict[str, object], ...] = (),
    prompts: tuple[dict[str, object], ...] = (),
    surfaces: tuple[dict[str, object], ...] = (),
    label: str | None = None,
) -> dict[str, object]:
    record: dict[str, object] = {
        "id": package_id,
        "version": version,
        "role": role,
        "metadata": {"label": label or package_id},
        "provenance": {"publisher": "synthetic-tests", "source_sha256": "a" * 64},
        "compatibility": {"host_contract": 1, "runtime_format": 1, "requires": []},
        "minimum_model_capability_tier": "sylvia.capability.t00",
        "dependencies": list(dependencies),
        "capabilities": list(capabilities),
        "tools": list(tools),
        "prompt_fragments": list(prompts),
        "ui_surfaces": list(surfaces),
        "files": [_file(path, data) for path, data in sorted(files.items())],
    }
    record["content_sha256"] = content_root_for_record(record)
    return record


def _dependency(record: dict[str, object], namespace: str = "support") -> dict[str, object]:
    return {
        "namespace": namespace,
        "kind": "declarative-library",
        "id": record["id"],
        "version": record["version"],
        "content_sha256": record["content_sha256"],
    }


def _tool(
    name: str = "advance",
    capability: str = "clockwork.counter",
) -> dict[str, object]:
    return {
        "name": name,
        "capability": capability,
        "input_schema": {
            "type": "object",
            "properties": {"steps": {"type": "integer"}},
            "required": ["steps"],
            "additionalProperties": False,
        },
        "state_predicates": [],
        "turn_statuses": ["open", "completed"],
        "writers": ["dm"],
    }


def _surface(
    identity: str,
    capability: str,
    *,
    namespace: str = "clockwork",
    key: str = "counter",
) -> dict[str, object]:
    return {
        "id": identity,
        "capability": capability,
        "title": "Clockwork",
        "icon": "◇",
        "renderer": "key_value",
        "source": {"kind": "state", "namespace": namespace, "key": key},
        "group": None,
        "order": 10,
        "default_size": 180,
        "minimum_size": 96,
        "default_placement": "docked",
        "allowed_placements": ["docked", "floating", "window"],
        "actions": [],
    }


def _archive(
    packages: Iterable[tuple[dict[str, object], dict[str, bytes]]],
    *,
    compression: int = zipfile.ZIP_DEFLATED,
    reverse_members: bool = False,
    manifest_bytes: bytes | None = None,
    extras: tuple[tuple[str, bytes], ...] = (),
) -> bytes:
    package_values = tuple(packages)
    manifest = {"format": 1, "packages": [record for record, _ in package_values]}
    manifest_bytes = manifest_bytes or canonical_json_bytes(manifest)
    members: list[tuple[str, bytes]] = [("manifest.json", manifest_bytes)]
    for record, files in package_values:
        package_id = str(record["id"])
        members.extend((f"packages/{package_id}/{path}", data) for path, data in files.items())
    members.extend(extras)
    if reverse_members:
        members.reverse()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as archive:
        for name, data in members:
            archive.writestr(name, data)
    return output.getvalue()


def _clockwork(
    version: str = "1",
    *,
    payload: bytes = b'{"ticks":1}',
    dependencies: tuple[dict[str, object], ...] = (),
) -> tuple[dict[str, object], dict[str, bytes]]:
    files = {"data/clock.json": payload}
    capability = "clockwork.counter"
    return (
        _package_record(
            "synthetic.clockwork",
            version,
            files,
            dependencies=dependencies,
            capabilities=(capability,),
            tools=(_tool(capability=capability),),
            prompts=({"id": "clockwork.prompt", "capability": capability},),
            surfaces=(_surface("clockwork.panel", capability),),
            label="Synthetic Clockwork",
        ),
        files,
    )


def test_player_facing_projection_excludes_private_campaign_plan_source() -> None:
    record, _ = _clockwork()
    surface = dict(record["ui_surfaces"][0])
    surface["source"] = {
        "kind": "campaign_plan",
        "path": ["campaign_plan", "objectives"],
    }
    record["ui_surfaces"] = [surface]
    record["content_sha256"] = content_root_for_record(record)

    manifest = manifest_from_record(record)
    projection = project_surfaces(
        manifest.prompt_fragments,
        manifest.ui_surfaces,
        active_capabilities=frozenset(manifest.capabilities),
    )

    assert projection.ui_surfaces == ()
    assert projection.ui_surface_ids == ()


def _zero_capability_primary() -> tuple[dict[str, object], dict[str, bytes]]:
    files = {"data/zero-capability.json": b"{}"}
    return (
        _package_record(
            "synthetic.zero-capability-primary",
            "1",
            files,
            label="Synthetic Zero Capability Primary",
        ),
        files,
    )


def _install(
    owner: AuthorityOwner,
    packages: Iterable[tuple[dict[str, object], dict[str, bytes]]],
    install_id: str,
):
    return owner.install_package_archive(io.BytesIO(_archive(packages)), install_id=install_id)


def _campaign(owner: AuthorityOwner, candidate: Any, campaign_id: str = "campaign-f3"):
    return owner.create_campaign(
        CampaignCreate(
            campaign_id=campaign_id,
            display_name="Synthetic observatory",
            writer=WriterRole.DM,
            package_lock=candidate.package_lock,
            turn_id="turn-f3",
        )
    )


class _BoundedSource(io.BytesIO):
    def __init__(self, value: bytes) -> None:
        super().__init__(value)
        self.maximum_requested = 0

    def read(self, size: int = -1) -> bytes:
        self.maximum_requested = max(self.maximum_requested, size)
        assert 0 <= size <= PACKAGE_CHUNK_LIMIT
        return super().read(size)


def _database_bytes(path: Path) -> bytes:
    value = bytearray()
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{path}{suffix}")
        if candidate.exists():
            value.extend(candidate.read_bytes())
    return bytes(value)


def test_f3_manifest_content_root_is_canonical_exact_and_archive_bytes_are_nonauthoritative(
    tmp_path: Path,
) -> None:
    package = _clockwork()
    first = _archive((package,), compression=zipfile.ZIP_STORED)
    second = _archive((package,), compression=zipfile.ZIP_DEFLATED, reverse_members=True)
    assert first != second

    with AuthorityOwner.open(tmp_path / "authority.db", "owner-f3") as owner:
        one = owner.install_package_archive(io.BytesIO(first), install_id="install-one")
        two = owner.install_package_archive(io.BytesIO(second), install_id="install-two")

    assert one.package_lock == two.package_lock
    assert one.closure_sha256 == two.closure_sha256
    assert one.package_lock.entries[0].sha256 == package[0]["content_sha256"]
    print(
        "F3 CANONICAL ROOT",
        package[0]["content_sha256"],
        "archive hashes",
        _sha(first),
        _sha(second),
        "closure",
        one.closure_sha256,
    )


def test_f3_manifest_rejects_duplicate_nonfinite_missing_extra_mismatched_and_aliased_content(
    tmp_path: Path,
) -> None:
    package = _clockwork()
    record, files = package
    canonical = canonical_json_bytes({"format": 1, "packages": [record]})
    duplicate = canonical.replace(b'{"format":1', b'{"format":1,"format":1', 1)
    nonfinite = canonical.replace(b'"format":1', b'"format":NaN', 1)
    mismatched_record = json.loads(canonical)["packages"][0]
    mismatched_record["files"][0]["sha256"] = "0" * 64
    mismatched = _archive(((mismatched_record, files),))
    aliases = (
        _archive((package,), extras=(("packages/synthetic.clockwork/../escape", b"x"),)),
        _archive((package,), extras=(("PACKAGES/SYNTHETIC.CLOCKWORK/data/clock.json", b"x"),)),
        _archive((package,), extras=(("C:/private", b"x"),)),
        _archive((package,), extras=(("packages\\alias", b"x"),)),
        _archive(
            (package,),
            extras=(
                (
                    unicodedata.normalize("NFD", "packages/caf" + chr(9) + "/data"),
                    b"x",
                ),
            ),
        ),
    )
    cases = {
        "duplicate": _archive((package,), manifest_bytes=duplicate),
        "nonfinite": _archive((package,), manifest_bytes=nonfinite),
        "missing": _archive(((record, {}),)),
        "extra": _archive((package,), extras=(("unindexed.bin", b"x"),)),
        "mismatch": mismatched,
        **{f"alias-{index}": value for index, value in enumerate(aliases)},
    }
    with AuthorityOwner.open(tmp_path / "authority.db", "owner-f3") as owner:
        for label, value in cases.items():
            with pytest.raises(CommandRejected):
                owner.install_package_archive(io.BytesIO(value), install_id=f"bad-{label}")
        assert owner.package_candidates() == ()
    print("F3 ARCHIVE DENIALS", tuple(cases))


def test_f3_install_publishes_complete_candidate_without_activation_and_admits_only_safe_metadata(
    tmp_path: Path,
) -> None:
    dependency_files = {"data/support.json": b'{"support":true}'}
    dependency = _package_record(
        "synthetic.support",
        "1",
        dependency_files,
        role="dependency",
        label="Synthetic Support",
    )
    primary = _clockwork(dependencies=(_dependency(dependency),))
    db = tmp_path / "authority.db"
    with AuthorityOwner.open(db, "owner-f3") as owner:
        candidate = _install(owner, ((dependency, dependency_files), primary), "complete")
        assert candidate.status is CandidateStatus.VALIDATED
        assert [entry.package_id for entry in candidate.package_lock.entries] == [
            "synthetic.clockwork",
            "synthetic.support",
        ]
        assert owner.snapshot if hasattr(owner, "snapshot") else True
        unsafe_record = dict(primary[0])
        unsafe_record["metadata"] = {"label": "Unsafe", "diagnostic_path": _SENTINEL}
        unsafe_record["content_sha256"] = content_root_for_record(unsafe_record)
        with pytest.raises(CommandRejected):
            owner.install_package_archive(
                io.BytesIO(_archive(((unsafe_record, primary[1]),))), install_id="unsafe"
            )
    assert _SENTINEL.encode() not in _database_bytes(db)
    print(
        "F3 CANDIDATE",
        candidate.candidate_id,
        candidate.status.value,
        candidate.closure_sha256,
        [entry.package_id for entry in candidate.package_lock.entries],
    )


def test_f3_dependency_bearing_reinstall_reuses_exact_immutable_closure_and_keeps_both_candidates(
    tmp_path: Path,
) -> None:
    dependency_files = {"data/support.json": b'{"support":"exact"}'}
    dependency = _package_record(
        "synthetic.reinstall-support",
        "1",
        dependency_files,
        role="dependency",
        label="Reinstall Support",
    )
    primary = _clockwork(dependencies=(_dependency(dependency),))
    db = tmp_path / "authority.db"

    with AuthorityOwner.open(db, "owner-f3") as owner:
        first = _install(owner, ((dependency, dependency_files), primary), "reinstall-one")
        second = _install(owner, ((dependency, dependency_files), primary), "reinstall-two")

        assert first.candidate_id != second.candidate_id
        assert first.package_lock == second.package_lock
        assert first.closure_sha256 == second.closure_sha256
        assert {candidate.candidate_id for candidate in owner.package_candidates()} == {
            first.candidate_id,
            second.candidate_id,
        }
        connection = owner._store._connect()
        try:
            dependency_rows = tuple(
                connection.execute(
                    "SELECT package_id, version, content_sha256, dependency_order, "
                    "dependency_namespace, dependency_kind, dependency_id, dependency_version, "
                    "dependency_content_sha256 FROM package_exact_dependencies"
                )
            )
            assert len(dependency_rows) == 1
            assert tuple(dependency_rows[0]) == (
                primary[0]["id"],
                primary[0]["version"],
                primary[0]["content_sha256"],
                0,
                "support",
                "declarative-library",
                dependency["id"],
                dependency["version"],
                dependency["content_sha256"],
            )
        finally:
            connection.close()

    with AuthorityOwner.open(db, "owner-reloaded") as reopened:
        candidates = reopened.package_candidates()
        assert len(candidates) == 2
        assert candidates[0].package_lock == candidates[1].package_lock
        connection = reopened._store._connect()
        try:
            connection.execute(
                "UPDATE package_exact_dependencies SET dependency_kind = 'conflicting-kind' "
                "WHERE package_id = ? AND version = ? AND content_sha256 = ?",
                (
                    primary[0]["id"],
                    primary[0]["version"],
                    primary[0]["content_sha256"],
                ),
            )
            connection.commit()
        finally:
            connection.close()
        with pytest.raises(CommandRejected) as conflict:
            _install(reopened, ((dependency, dependency_files), primary), "reinstall-conflict")
        assert conflict.value.code == "package_identity_conflict"
        assert len(reopened.package_candidates()) == 2
    print(
        "F3 DEPENDENCY REINSTALL",
        first.candidate_id,
        second.candidate_id,
        first.closure_sha256,
        "immutable dependency rows",
        len(dependency_rows),
        conflict.value.code,
    )


def test_f3_publication_and_consumer_reads_are_bounded_hash_exact_and_private_sink_safe(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    payload = b"synthetic-bytes-" * 20000
    package = _clockwork(payload=payload)
    source = _BoundedSource(_archive((package,)))
    delivered: list[bytes] = []
    caplog.set_level(logging.DEBUG)
    db = tmp_path / "authority.db"
    with AuthorityOwner.open(db, "owner-f3") as owner:
        candidate = owner.install_package_archive(source, install_id="bounded")
        blob_sha = str(package[0]["files"][0]["sha256"])
        receipt = owner.consume_package_blob(blob_sha, delivered.append)
    assert source.maximum_requested <= PACKAGE_CHUNK_LIMIT
    assert max(map(len, delivered)) <= PACKAGE_CHUNK_LIMIT
    assert b"".join(delivered) == payload
    assert receipt.sha256 == blob_sha
    assert receipt.maximum_chunk_size <= PACKAGE_CHUNK_LIMIT
    assert _SENTINEL not in caplog.text
    assert _SENTINEL.encode() not in _database_bytes(db)
    print(
        "F3 BOUNDED READ",
        candidate.candidate_id,
        receipt.sha256,
        receipt.byte_count,
        "bound",
        PACKAGE_CHUNK_LIMIT,
        "observed",
        receipt.maximum_chunk_size,
    )


def test_f3_closure_is_one_primary_typed_namespaced_exact_and_deterministically_ordered(
    tmp_path: Path,
) -> None:
    shared_files = {"data/shared.json": b'{"shared":true}'}
    shared = _package_record("synthetic.shared", "1", shared_files, role="dependency")
    left_files = {"data/left.json": b'{"left":true}'}
    left = _package_record(
        "synthetic.left",
        "1",
        left_files,
        role="dependency",
        dependencies=(_dependency(shared, "shared"),),
    )
    right_files = {"data/right.json": b'{"right":true}'}
    right = _package_record(
        "synthetic.right",
        "1",
        right_files,
        role="dependency",
        dependencies=(_dependency(shared, "shared"),),
    )
    primary_files = {"data/root.json": b'{"root":true}'}
    primary = _package_record(
        "synthetic.primary",
        "1",
        primary_files,
        dependencies=(_dependency(left, "left"), _dependency(right, "right")),
    )
    with AuthorityOwner.open(tmp_path / "authority.db", "owner-f3") as owner:
        candidate = _install(
            owner,
            (
                (right, right_files),
                (shared, shared_files),
                (primary, primary_files),
                (left, left_files),
            ),
            "closure",
        )
        reloaded = owner.package_candidate(candidate.candidate_id)
    order = [entry.package_id for entry in reloaded.package_lock.entries]
    assert order == ["synthetic.primary", "synthetic.left", "synthetic.shared", "synthetic.right"]
    assert [entry.load_order for entry in reloaded.package_lock.entries] == list(range(4))
    assert reloaded.package_lock.entries[0].dependencies[0].namespace == "left"
    print("F3 ORDERED CLOSURE", order, reloaded.closure_sha256)


def test_f3_manifest_capabilities_govern_ordinary_synthetic_package_identity(
    tmp_path: Path,
) -> None:
    """Manifest capabilities, not an ordinary synthetic identity, govern mechanics."""

    record, files = _clockwork()
    record["id"] = "synthetic.manifest-governed"
    record["content_sha256"] = content_root_for_record(
        {key: value for key, value in record.items() if key != "content_sha256"}
    )
    with AuthorityOwner.open(
        tmp_path / "manifest-governed-id.db", "owner-f3-manifest-governed-id"
    ) as owner:
        candidate = _install(owner, ((record, files),), "manifest-governed-id")
        assert candidate.package_lock.entries[0].package_id == "synthetic.manifest-governed"
        assert candidate.package_lock.entries[0].capabilities == ("clockwork.counter",)
        print(
            "F3 ORDINARY PACKAGE ID",
            candidate.package_lock.entries[0].package_id,
            candidate.package_lock.entries[0].capabilities,
        )


def test_f3_invalid_package_closures_fail_closed_without_fallback(tmp_path: Path) -> None:
    dep_files = {"data/dep.json": b"{}"}
    dep = _package_record("synthetic.dep", "1", dep_files, role="dependency")
    missing = _clockwork(
        dependencies=(
            {
                "namespace": "missing",
                "kind": "declarative-library",
                "id": "synthetic.missing",
                "version": "1",
                "content_sha256": "f" * 64,
            },
        )
    )
    cyclic_dep = dict(dep)
    cyclic_dep["dependencies"] = [_dependency(cyclic_dep, "self")]
    cyclic_dep["content_sha256"] = content_root_for_record(cyclic_dep)
    peer = _package_record("synthetic.peer", "1", dep_files, role="primary")
    zero_capability_record, zero_capability_files = _zero_capability_primary()
    primary = _clockwork(dependencies=(_dependency(dep, "same"), _dependency(dep, "same")))
    cases = (
        (missing,),
        ((cyclic_dep, dep_files),),
        ((peer, dep_files), _clockwork()),
        ((zero_capability_record, zero_capability_files), (dep, dep_files)),
        ((dep, dep_files), primary),
    )
    with AuthorityOwner.open(tmp_path / "authority.db", "owner-f3") as owner:
        for index, packages in enumerate(cases):
            with pytest.raises(CommandRejected):
                _install(owner, packages, f"closed-{index}")
        assert owner.package_candidates() == ()
    print("F3 CLOSED CLOSURES", len(cases), "no fallback")


def test_f3_authority_first_reference_to_unpublished_closure_is_impossible(tmp_path: Path) -> None:
    db = tmp_path / "authority.db"
    with AuthorityOwner.open(db, "owner-f3") as owner:
        connection = owner._store._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO package_candidate_memberships "
                    "(candidate_id, closure_order, package_id, version, content_sha256) "
                    "VALUES ('ghost', 0, 'missing', '1', ?)",
                    ("1" * 64,),
                )
        finally:
            connection.rollback()
            connection.close()
        assert owner.package_candidates() == ()
    print("F3 AUTHORITY FIRST", "foreign-key rejected unpublished closure")


def test_f3_bytes_first_crash_dedup_mismatch_and_reclaimer_interleaving_preserve_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "authority.db"
    package = _clockwork(payload=b"x" * (PACKAGE_CHUNK_LIMIT + 17))
    owner = AuthorityOwner.open(db, "owner-f3")
    monkeypatch.setattr(
        owner._store,
        "_before_package_publication",
        lambda: (_ for _ in ()).throw(RuntimeError("synthetic prepublication crash")),
    )
    with pytest.raises(RuntimeError, match="prepublication"):
        _install(owner, (package,), "crash-staging")
    monkeypatch.setattr(owner._store, "_before_package_publication", lambda: None)
    staging = owner.package_staging("crash-staging")
    assert staging.status == "staging"
    assert owner.reclaim_package_bytes().staging_roots >= 1
    candidate = _install(owner, (package,), "published")
    duplicate = _install(owner, (package,), "deduplicated")
    assert duplicate.package_lock == candidate.package_lock
    blob_sha = str(package[0]["files"][0]["sha256"])
    connection = owner._store._connect()
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            "UPDATE package_blob_chunks SET data = X'00' WHERE blob_sha256 = ? AND chunk_order = 0",
            (blob_sha,),
        )
    finally:
        connection.close()
    with pytest.raises(CommandRejected):
        _install(owner, (package,), "dedup-conflict")
    owner.close()
    with pytest.raises(SchemaIdentityError):
        AuthorityOwner.open(db, "owner-reopen")
    print(
        "F3 BYTES FIRST",
        staging.status,
        candidate.candidate_id,
        duplicate.candidate_id,
        "dedup mismatch closed",
    )


def test_f3_in_progress_verified_read_survives_reclaimer_without_open_wait_transaction(
    tmp_path: Path,
) -> None:
    payload = b"lease-data-" * 20000
    package = _clockwork(payload=payload)
    entered = threading.Event()
    release = threading.Event()
    delivered: list[bytes] = []
    with AuthorityOwner.open(tmp_path / "authority.db", "owner-f3") as owner:
        _install(owner, (package,), "lease")
        blob_sha = str(package[0]["files"][0]["sha256"])

        def consumer(chunk: bytes) -> None:
            delivered.append(chunk)
            if len(delivered) == 1:
                entered.set()
                assert release.wait(timeout=5)

        thread = threading.Thread(target=lambda: owner.consume_package_blob(blob_sha, consumer))
        thread.start()
        assert entered.wait(timeout=5)
        result = owner.reclaim_package_bytes()
        assert result.read_lease_roots == 1
        assert owner._store.connection_in_transaction_for_test() is False
        release.set()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert b"".join(delivered) == payload
    print("F3 READ LEASE", blob_sha, "gc protected", result.read_lease_roots)


def test_f3_reclamation_protects_active_archived_tombstoned_candidate_lineage_staging_read_and_recovery_roots(  # noqa: E501
    tmp_path: Path,
) -> None:
    observed: list[tuple[str, object]] = []
    for lifecycle in LifecycleState:
        db = tmp_path / f"{lifecycle.value}.db"
        package = _clockwork()
        with AuthorityOwner.open(db, f"owner-{lifecycle.value}") as owner:
            candidate = _install(owner, (package,), f"root-{lifecycle.value}")
            campaign = owner.create_campaign(
                CampaignCreate(
                    campaign_id=f"campaign-{lifecycle.value}",
                    display_name="Synthetic roots",
                    writer=(
                        WriterRole.DM if lifecycle is LifecycleState.ACTIVE else WriterRole.NONE
                    ),
                    lifecycle=lifecycle,
                    package_lock=candidate.package_lock,
                    turn_id="turn-root",
                )
            )
            blob_sha = str(package[0]["files"][0]["sha256"])
            owner.add_package_recovery_root("backup-root", blob_sha)
            roots = owner.reclaim_package_bytes()
            owner.remove_package_recovery_root("backup-root")
            assert owner.consume_package_blob(blob_sha, lambda _chunk: None).sha256 == blob_sha
            assert roots.campaign_lock_roots >= 1
            assert roots.candidate_roots >= 1
            assert owner.snapshot(campaign.campaign_id).package_lock == candidate.package_lock
            observed.append((lifecycle.value, roots))
    print("F3 GC ROOTS", observed)


def test_f3_startup_rejects_blob_candidate_lock_lineage_reference_and_privacy_corruption(
    tmp_path: Path,
) -> None:
    def empty_candidate(db: sqlite3.Connection, _campaign: str, _blob: str) -> None:
        db.execute("DELETE FROM package_candidate_memberships")
        db.execute(
            "UPDATE package_candidates SET closure_sha256 = ?",
            (_sha(b"[]"),),
        )

    mutations: tuple[tuple[str, Callable[[sqlite3.Connection, str, str], None]], ...] = (
        (
            "blob",
            lambda db, campaign, blob: db.execute(
                "UPDATE package_blobs SET byte_count = byte_count + 1 WHERE blob_sha256 = ?",
                (blob,),
            ),
        ),
        (
            "candidate-empty",
            empty_candidate,
        ),
        (
            "candidate",
            lambda db, campaign, blob: db.execute(
                "UPDATE package_candidates SET closure_sha256 = ?",
                ("0" * 64,),
            ),
        ),
        (
            "lock",
            lambda db, campaign, blob: db.execute(
                "UPDATE campaign_locks SET closure_sha256 = ? WHERE campaign_id = ?",
                ("0" * 64, campaign),
            ),
        ),
        (
            "lineage",
            lambda db, campaign, blob: db.execute(
                "UPDATE campaign_locks SET predecessor_lock_id = lock_id WHERE campaign_id = ?",
                (campaign,),
            ),
        ),
        (
            "reference",
            lambda db, campaign, blob: db.execute(
                "INSERT INTO package_recovery_roots(root_id, blob_sha256) VALUES ('bad', ?)",
                ("f" * 64,),
            ),
        ),
        (
            "privacy",
            lambda db, campaign, blob: db.execute(
                "UPDATE package_candidates SET report_json = ?",
                (json.dumps({"code": "ok", "diagnostic": _SENTINEL}),),
            ),
        ),
    )
    rejected: list[str] = []
    for label, corrupt in mutations:
        db_path = tmp_path / f"corrupt-{label}.db"
        package = _clockwork()
        owner = AuthorityOwner.open(db_path, f"owner-{label}")
        candidate = _install(owner, (package,), f"install-{label}")
        campaign = _campaign(owner, candidate, f"campaign-{label}")
        blob_sha = str(package[0]["files"][0]["sha256"])
        owner.close()
        connection = sqlite3.connect(db_path)
        try:
            connection.execute("PRAGMA foreign_keys=OFF")
            corrupt(connection, campaign.campaign_id, blob_sha)
            connection.commit()
        finally:
            connection.close()
        print("F3 CORRUPTION CASE", label)
        with pytest.raises(SchemaIdentityError):
            AuthorityOwner.open(db_path, f"reopen-{label}")
        rejected.append(label)
    print("F3 STARTUP CORRUPTION", tuple(rejected))


def test_f3_migrates_exact_f2_schema_without_inventing_missing_package_bytes(
    tmp_path: Path,
) -> None:
    import importlib.util

    module_path = Path(__file__).with_name("test_alpha_f2_event_protocol.py")
    spec = importlib.util.spec_from_file_location("f2_fixture_module", module_path)
    assert spec is not None and spec.loader is not None
    fixture_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture_module)
    _f1_seed = fixture_module._f1_seed
    _f1_relational_snapshot = fixture_module._f1_relational_snapshot

    db_path = tmp_path / "legacy-f2.db"
    f1_before = _f1_seed(db_path)
    with sqlite3.connect(db_path) as connection:
        before = _f1_relational_snapshot(connection)
    assert before == f1_before
    with AuthorityOwner.open(db_path, "owner-f3") as owner:
        with sqlite3.connect(db_path) as connection:
            after = _f1_relational_snapshot(connection)
        expected_after = dict(before)
        expected_after["schema_migrations"] = (
            *before["schema_migrations"],
            (2, _MIGRATION_2_NAME, _MIGRATION_2_SHA256),
            (3, "f3_sqlite_package_library", _MIGRATION_3_SHA256),
        )
        snapshot = owner.snapshot("campaign-f1-migration")
        lock = owner.current_package_lock("campaign-f1-migration")
        assert expected_after == after
        assert lock.availability is PackageLockAvailability.LEGACY_UNAVAILABLE
        assert snapshot.revision == 1
        with pytest.raises(CommandRejected, match="unavailable"):
            owner.catalog("campaign-f1-migration")
    connection = sqlite3.connect(db_path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == _SCHEMA_VERSION == 3
        migrations = tuple(
            connection.execute("SELECT version, sql_sha256 FROM schema_migrations ORDER BY version")
        )
        assert migrations == (
            (1, _MIGRATION_1_SHA256),
            (2, _MIGRATION_2_SHA256),
            (3, _MIGRATION_3_SHA256),
        )
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchone() is None
        schema_hash = _sha(repr(_schema_identity(connection)).encode())
    finally:
        connection.close()
    print("F3 MIGRATION", migrations, "schema", schema_hash, "legacy", lock.availability.value)
