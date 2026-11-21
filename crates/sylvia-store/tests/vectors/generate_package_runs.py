"""Paired runs for package installation: Python's starting databases, archives and outcomes.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -B crates/sylvia-store/tests/vectors/generate_package_runs.py

It writes, under `packages/`:
- `archives/riichi.archive`, the bundled Riichi closure (`bundled_riichi_archive()`: two packages,
  a participant artifact, content declarations and a three-chunk file), and
  `archives/synthetic.archive`, one small format-1 package with an empty file, both byte-stable
  (zip files, named `.archive` because the repository ignores `*.zip`);
- `seed_fresh.sqlite3.gz` (a new owner profile) and `seed_riichi.sqlite3.gz` (the same with the
  Riichi closure installed as `install-1`);
- `<run>.sqlite3.gz`, the database after each run marked `compare_db`;
- `package_runs.json`, each run's seed, operations and Python's outcome of each.

Operations go through `SQLiteAuthorityStore` with an archive admitted by
`package_archive.admit_package_archive`; `sql` changes the database through a second
connection, as a damaged or interrupted profile would be; `reopen` closes the store and opens it
again, which runs the startup verifiers. `tests/packages.rs` replays every run on the Rust store.
Identities are explicit and the owner's UUIDs are seeded, so a re-run writes byte-identical
files. Reference tooling only.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import random
import shutil
import sqlite3
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3] / "src"))
sys.path.insert(0, str(HERE))

_IDENTITIES = random.Random(20261126)
uuid.uuid4 = lambda: uuid.UUID(int=_IDENTITIES.getrandbits(128), version=4)  # type: ignore[assignment]

from sylvia_gm.application.authority import AuthorityOwner  # noqa: E402
from sylvia_gm.application.bundled_packages import bundled_riichi_archive  # noqa: E402
from sylvia_gm.domain.package_library import canonical_json_bytes, content_root_for_record  # noqa: E402
from sylvia_gm.infrastructure import sqlite_authority as a  # noqa: E402
from sylvia_gm.infrastructure.package_archive import admit_package_archive  # noqa: E402

OUT = HERE / "packages"
RIICHI_PRIMARY = "pneuma.riichi-mahjong"
RIICHI_DEPENDENCY = "pneuma.riichi-club"


def synthetic_archive() -> bytes:
    """One format-1 primary package with an empty file, written with fixed member times."""
    payload = canonical_json_bytes({"fixture": "lane-3-package-runs"})
    files = {"payload/data.json": payload, "payload/empty.txt": b""}
    draft: dict[str, object] = {
        "id": "synthetic.packages", "version": "1.0.0", "role": "primary",
        "metadata": {"label": "Synthetic package runs"},
        "provenance": {"publisher": "sylvia-gm-rust-port", "source_sha256": hashlib.sha256(payload).hexdigest()},
        "compatibility": {"host_contract": 1, "runtime_format": 1, "requires": []},
        "minimum_model_capability_tier": "sylvia.capability.t00",
        "dependencies": [], "capabilities": ["synthetic.checks"],
        "tools": [{"name": "roll_check", "capability": "synthetic.checks", "input_schema": {"type": "object"},
                   "state_predicates": [{"namespace": "synthetic.packages", "key": "mode", "equals": "ready"},
                                        {"namespace": "synthetic.packages", "key": "locked", "equals": True,
                                         "negated": True}],
                   "turn_statuses": ["open"], "writers": ["dm"]}],
        "prompt_fragments": [], "ui_surfaces": [],
        "files": [{"path": path, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                  for path, data in files.items()],
    }
    root = content_root_for_record(draft)
    stream = io.BytesIO()
    # Stored, not deflated: zlib's output differs between platforms, stored bytes do not.
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        def put(name: str, data: bytes) -> None:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            # ZipInfo records the writing platform; pin it so every platform writes the same bytes.
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data, compress_type=zipfile.ZIP_STORED)
        put("manifest.json", canonical_json_bytes({"format": 1, "packages": [{**draft, "content_sha256": root}]}))
        for path, data in files.items():
            put(f"packages/synthetic.packages/{path}", data)
    return stream.getvalue()


ARCHIVES = {"riichi": bundled_riichi_archive(), "synthetic": synthetic_archive()}


def stage(archive: str, install_id: str) -> dict[str, object]:
    return {"op": "stage_package_archive", "archive": archive, "install_id": install_id}


def publish(archive: str, install_id: str, fault: bool = False) -> dict[str, object]:
    return {"op": "publish_package_archive", "archive": archive, "install_id": install_id, "fault": fault}


def sql(statement: str) -> dict[str, object]:
    return {"op": "sql", "statement": statement}


REOPEN = {"op": "reopen"}
CANDIDATES = {"op": "package_candidates"}


def candidate(candidate_id: str) -> dict[str, object]:
    return {"op": "package_candidate", "candidate_id": candidate_id}


def staging(install_id: str) -> dict[str, object]:
    return {"op": "package_staging", "install_id": install_id}


PRIMARY_IDENTITY = f"(SELECT package_id, version, content_sha256 FROM package_manifests WHERE package_id = '{RIICHI_PRIMARY}')"
PRIMARY_WHERE = (f"package_id = '{RIICHI_PRIMARY}' AND content_sha256 = "
                 f"(SELECT content_sha256 FROM package_manifests WHERE package_id = '{RIICHI_PRIMARY}')")


def conflict(statement: str) -> list[dict[str, object]]:
    """Damage installed rows, then publish the same closure under a new install."""
    return [sql(statement), stage("riichi", "install-2"), publish("riichi", "install-2"), REOPEN]


def damaged(statement: str) -> list[dict[str, object]]:
    return [sql(statement), REOPEN]


RUNS: list[dict[str, object]] = [
    {"name": "install", "seed": "fresh", "compare_db": True, "operations": [
        staging("install-1"), stage("riichi", "install-1"), staging("install-1"), CANDIDATES,
        publish("riichi", "install-1"), staging("install-1"), candidate("candidate-install-1"), CANDIDATES,
        {"op": "exact_package_manifest", "package": RIICHI_PRIMARY},
        {"op": "exact_package_manifest", "package": RIICHI_DEPENDENCY},
        {"op": "exact_package_manifest_missing"},
        {"op": "package_participant_declarations", "candidate_id": "candidate-install-1"},
        {"op": "package_participant_declarations", "candidate_id": "candidate-missing"},
        {"op": "exact_participant_declarations", "package": RIICHI_PRIMARY},
        {"op": "exact_participant_declarations", "package": RIICHI_DEPENDENCY},
        candidate("candidate-missing"), staging("install-missing"),
        stage("riichi", "install-1"), publish("riichi", "install-1"), stage("synthetic", "install-1"),
        stage("riichi", " install-2"), stage("riichi", ""),
        stage("riichi", "install-2"), publish("riichi", "install-2"),
        stage("synthetic", "install-3"), staging("install-3"), publish("synthetic", "install-3"),
        publish("synthetic", "install-4"), CANDIDATES, REOPEN, CANDIDATES,
    ]},
    {"name": "retry_and_fault", "seed": "fresh", "compare_db": True, "operations": [
        stage("riichi", "install-1"),
        sql("DELETE FROM package_staging_chunks WHERE install_id = 'install-1' AND file_order >= 3"),
        staging("install-1"), REOPEN, staging("install-1"),
        stage("riichi", "install-1"), staging("install-1"),
        publish("riichi", "install-1", fault=True), staging("install-1"), CANDIDATES,
        publish("riichi", "install-1"), CANDIDATES, REOPEN,
    ]},
    {"name": "staging_damage", "seed": "fresh", "compare_db": True, "operations": [
        stage("riichi", "install-1"),
        sql("DELETE FROM package_staging_chunks WHERE install_id = 'install-1' AND file_order = 2 AND chunk_order = 1"),
        publish("riichi", "install-1"),
        stage("riichi", "install-1"),
        sql("UPDATE package_staging_chunks SET chunk_sha256 = '" + "0" * 64 + "' WHERE install_id = 'install-1' "
            "AND file_order = 0"),
        publish("riichi", "install-1"),
        stage("riichi", "install-1"),
        sql("DELETE FROM package_staging_chunks WHERE install_id = 'install-1' AND file_order = 1"),
        publish("riichi", "install-1"),
        stage("riichi", "install-1"),
        sql("INSERT INTO package_staging_chunks SELECT install_id, 99, 0, package_id, version, content_sha256, "
            "portable_path, chunk_sha256, data FROM package_staging_chunks WHERE install_id = 'install-1' "
            "AND file_order = 0"),
        publish("riichi", "install-1"),
        stage("riichi", "install-1"), publish("riichi", "install-1"), REOPEN,
    ]},
    {"name": "conflict_capabilities", "seed": "riichi", "compare_db": True, "operations": conflict(
        f"UPDATE packages SET capabilities_json = '[\"other\"]' WHERE package_id = '{RIICHI_PRIMARY}'")},
    {"name": "conflict_dependencies", "seed": "riichi", "operations": conflict(
        f"UPDATE package_dependencies SET dependency_kind = 'other' WHERE package_id = '{RIICHI_DEPENDENCY}'")},
    {"name": "conflict_manifest", "seed": "riichi", "operations": conflict(
        f"UPDATE package_manifests SET metadata_json = '{{}}' WHERE package_id = '{RIICHI_PRIMARY}'")},
    # Reopening this one reaches `verify_f5_campaign_authority`, which is still to come in Rust.
    {"name": "conflict_declarations", "seed": "riichi", "operations": conflict(
        f"UPDATE package_participant_artifacts SET schema_id = 'other' WHERE package_id = '{RIICHI_PRIMARY}'")[:-1]},
    {"name": "conflict_blob", "seed": "riichi", "operations": conflict(
        "UPDATE package_blob_chunks SET chunk_sha256 = '" + "1" * 64 + "' WHERE chunk_order = 0 AND blob_sha256 = "
        "(SELECT blob_sha256 FROM package_manifest_files WHERE portable_path = 'world.md')")},
    {"name": "conflict_file", "seed": "riichi", "operations": conflict(
        "UPDATE package_manifest_files SET portable_path = 'moved.md' WHERE portable_path = 'world.md'")},
    {"name": "conflict_exact_dependencies", "seed": "riichi", "operations": conflict(
        f"UPDATE package_exact_dependencies SET dependency_kind = 'other' WHERE package_id = '{RIICHI_DEPENDENCY}'")},
    {"name": "damaged_staging_format", "seed": "riichi", "operations": damaged(
        "UPDATE package_staging SET manifest_json = '{\"format\":9,\"packages\":[]}'")},
    {"name": "damaged_staging_report", "seed": "riichi", "operations": damaged(
        "UPDATE package_staging SET report_json = '{\"code\":\"validated\"}'")},
    {"name": "damaged_staging_prefix", "seed": "fresh", "operations": [
        stage("riichi", "install-1"),
        sql("DELETE FROM package_staging_chunks WHERE install_id = 'install-1' AND file_order = 0"), REOPEN]},
    {"name": "damaged_staging_tail", "seed": "fresh", "operations": [
        stage("riichi", "install-1"),
        sql("DELETE FROM package_staging_chunks WHERE install_id = 'install-1' AND file_order = 2 AND chunk_order = 2"),
        REOPEN, staging("install-1")]},
    {"name": "damaged_blob_bytes", "seed": "riichi", "operations": damaged(
        "UPDATE package_blob_chunks SET data = X'00' WHERE chunk_order = 1")},
    {"name": "damaged_blob_count", "seed": "riichi", "operations": damaged(
        "UPDATE package_blobs SET chunk_count = chunk_count + 1 WHERE byte_count > 100000")},
    {"name": "damaged_manifest_metadata", "seed": "riichi", "operations": damaged(
        f"UPDATE package_manifests SET metadata_json = '{{\"label\":\"Other\"}}' WHERE {PRIMARY_WHERE}")},
    {"name": "damaged_manifest_files", "seed": "riichi", "operations": damaged(
        "UPDATE package_manifest_files SET byte_count = byte_count + 1 WHERE portable_path = 'world.md'")},
    {"name": "damaged_candidate_closure", "seed": "riichi", "operations": damaged(
        "UPDATE package_candidates SET closure_sha256 = '" + "2" * 64 + "'")},
    {"name": "damaged_candidate_membership", "seed": "riichi", "operations": damaged(
        "DELETE FROM package_candidate_memberships WHERE closure_order = 1")},
    # Damage `_verify_stored_authority` refuses first (an unbound campaign row, an activation
    # row) waits for that verifier: Python reports it before `verify_package_authority` runs.
]


def plain(value: object) -> object:
    return json.loads(a._dump(value))


def candidate_json(item: object) -> dict[str, object]:
    return {
        "candidate_id": item.candidate_id, "install_id": item.install_id,  # type: ignore[attr-defined]
        "status": item.status.value, "closure_sha256": item.closure_sha256,  # type: ignore[attr-defined]
        "validation_report": plain(item.validation_report),  # type: ignore[attr-defined]
        "entries": [
            {"package_id": e.package_id, "version": e.version, "sha256": e.sha256, "role": e.role.value,
             "load_order": e.load_order, "capabilities": list(e.capabilities),
             "dependencies": [[d.package_id, d.version, d.sha256, d.kind, d.namespace]
                              for d in e.dependencies]}
            for e in item.package_lock.entries  # type: ignore[attr-defined]
        ],
    }


def declaration_json(item: object) -> dict[str, object]:
    return {"kind": item.kind, "schema_id": item.schema_id, "schema_sha256": item.schema_sha256,  # type: ignore[attr-defined]
            "schema": plain(item.schema)}  # type: ignore[attr-defined]


class _Fault(Exception):
    """Stands in for a crash between the publication writes and the commit."""


def identity(store: a.SQLiteAuthorityStore, package_id: str) -> tuple[str, str, str]:
    connection = store._connect()
    try:
        row = connection.execute("SELECT package_id, version, content_sha256 FROM package_manifests "
                                 "WHERE package_id = ?", (package_id,)).fetchone()
        return (str(row[0]), str(row[1]), str(row[2]))
    finally:
        connection.close()


def run(state: dict[str, object], op: dict[str, object]) -> dict[str, object]:
    store: a.SQLiteAuthorityStore = state["store"]  # type: ignore[assignment]
    kind = str(op["op"])
    try:
        if kind in ("stage_package_archive", "publish_package_archive"):
            with admit_package_archive(io.BytesIO(ARCHIVES[str(op["archive"])])) as admitted:
                if kind == "stage_package_archive":
                    store.stage_package_archive(admitted, str(op["install_id"]))
                    value: object = None
                else:
                    if op["fault"]:
                        def fail() -> None:
                            raise _Fault("fault before publication")
                        store._before_package_publication = fail  # type: ignore[method-assign]
                    try:
                        value = candidate_json(store.publish_package_archive(admitted, str(op["install_id"])))
                    finally:
                        store.__dict__.pop("_before_package_publication", None)
        elif kind == "sql":
            connection = sqlite3.connect(state["path"], isolation_level=None)  # type: ignore[arg-type]
            try:
                connection.execute(str(op["statement"]))
            finally:
                connection.close()
            value = None
        elif kind == "reopen":
            store.close()
            state["store"] = None
            state["store"] = a._open_for_application_owner(state["path"])  # type: ignore[arg-type]
            value = None
        elif kind == "package_candidates":
            value = [candidate_json(item) for item in store.package_candidates()]
        elif kind == "package_candidate":
            value = candidate_json(store.package_candidate(str(op["candidate_id"])))
        elif kind == "package_staging":
            record = store.package_staging(str(op["install_id"]))
            value = {"install_id": record.install_id, "status": record.status, "chunk_count": record.chunk_count}
        elif kind == "exact_package_manifest":
            manifest = store.exact_package_manifest(*identity(store, str(op["package"])))
            value = {"identity": list(manifest.identity), "canonical_json": manifest.canonical_json}
        elif kind == "exact_package_manifest_missing":
            store.exact_package_manifest(RIICHI_PRIMARY, "0.0.0", "3" * 64)
            value = None
        elif kind == "package_participant_declarations":
            value = [declaration_json(item) for item in store.package_participant_declarations(str(op["candidate_id"]))]
        elif kind == "exact_participant_declarations":
            value = [declaration_json(item) for item in store.exact_participant_declarations(*identity(store, str(op["package"])))]
        else:
            raise AssertionError(kind)
    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
        return {"error": type(error).__name__, "code": getattr(error, "code", None), "message": str(error)}
    return {"ok": value}


# ---- migration 5's participant backfill, function-level (`sqlite_campaign.migrate_v4_participants`) ----

MAHJONG = (f"(SELECT version FROM package_manifests WHERE package_id = '{RIICHI_PRIMARY}')",
           f"(SELECT content_sha256 FROM package_manifests WHERE package_id = '{RIICHI_PRIMARY}')")


def lock(lock_id: str, campaign_id: str, revision: int = 0, sha256: str | None = None) -> list[str]:
    content = MAHJONG[1] if sha256 is None else f"'{sha256}'"
    return [
        f"INSERT INTO campaign_locks VALUES ('{lock_id}', '{campaign_id}', '{'e' * 64}', 'available', NULL, {revision})",
        f"INSERT INTO campaign_lock_memberships VALUES ('{lock_id}', 0, '{RIICHI_PRIMARY}', {MAHJONG[0]}, {content}, 'primary')",
    ]


def participant(campaign_id: str, participant_id: str, data: object, kind: str = "player",
                version: str = MAHJONG[0]) -> str:
    return (f"INSERT INTO participants (campaign_id, participant_id, kind, package_id, package_version, "
            f"data_json) VALUES ('{campaign_id}', '{participant_id}', '{kind}', '{RIICHI_PRIMARY}', "
            f"{version}, {quote(json.dumps(data, sort_keys=True, separators=(',', ':')))})")


def quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


MIGRATION_5 = [statement for statement in a._MIGRATION_5.split(";") if statement.strip()]
PARTICIPANT_CASES: list[dict[str, object]] = [
    {"name": "backfill", "sql": MIGRATION_5 + lock("lock-1", "campaign-1") + lock("lock-2", "campaign-2")
     + lock("lock-3a", "campaign-3") + lock("lock-3b", "campaign-3", sha256="f" * 64)
     + lock("lock-4", "campaign-4", revision=1) + [
        participant("campaign-1", "p-valid", {"name": "Aki", "control": "player"}),
        participant("campaign-1", "p-invalid", {"name": ""}),
        participant("campaign-1", "p-other-kind", {"name": "Aki", "control": "player"}, kind="dealer"),
        participant("campaign-2", "p-valid", {"name": "Bo", "control": "player"}),
        "INSERT INTO participant_quarantines(campaign_id, participant_id, reason_code) "
        "VALUES ('campaign-2', 'p-valid', 'exact_schema_proof_unavailable')",
        participant("campaign-3", "p-ambiguous", {"name": "Cy", "control": "player"}),
        participant("campaign-4", "p-later-lock", {"name": "Di", "control": "player"}),
        participant("campaign-5", "p-no-lock", {"name": "Ed", "control": "player"}),
        participant("campaign-1", "p-other-version", {"name": "Fu", "control": "player"}, version="'9.9.9'"),
     ]},
    {"name": "data_not_object", "sql": MIGRATION_5 + lock("lock-1", "campaign-1") + [
        participant("campaign-1", "p-list", ["Aki"])]},
    {"name": "declaration_conflict", "sql": MIGRATION_5 + [
        f"INSERT INTO package_participant_artifacts SELECT package_id, version, content_sha256, 0, 'player', "
        f"'other', '{'a' * 64}', '{{}}' FROM package_manifests WHERE package_id = '{RIICHI_PRIMARY}'"]},
]


def participant_outcome(connection: sqlite3.Connection) -> dict[str, object]:
    def rows(query: str) -> list[list[object]]:
        return [list(row) for row in connection.execute(query)]
    return {
        "participants": rows("SELECT * FROM participants ORDER BY campaign_id, participant_id"),
        "quarantines": rows("SELECT * FROM participant_quarantines ORDER BY campaign_id, participant_id"),
        "declarations": rows("SELECT package_id, artifact_order, kind, schema_id, schema_sha256, schema_json "
                             "FROM package_participant_artifacts ORDER BY package_id, artifact_order"),
    }


def participant_cases(folder: Path) -> tuple[bytes, list[dict[str, object]]]:
    from generate_schema_fixtures import build
    from sylvia_gm.infrastructure import sqlite_campaign

    (folder / "v4").mkdir()
    database = build(folder / "v4", "v4")
    store = a._open_for_application_owner(database)
    try:
        with admit_package_archive(io.BytesIO(ARCHIVES["riichi"])) as admitted:
            store.stage_package_archive(admitted, "install-1")
            store.publish_package_archive(admitted, "install-1")
    finally:
        store.close()
    base = checkpoint(database)
    results = []
    for case in PARTICIPANT_CASES:
        path = folder / f"participants-{case['name']}.sqlite3"
        path.write_bytes(base)
        connection = sqlite3.connect(path, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            for statement in case["sql"]:  # type: ignore[union-attr]
                connection.execute(statement)
            try:
                sqlite_campaign.migrate_v4_participants(connection)
                outcome: dict[str, object] = {"ok": None}
            except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
                outcome = {"error": type(error).__name__, "message": str(error)}
            connection.row_factory = None
            outcome["rows"] = participant_outcome(connection)
        finally:
            connection.close()
        results.append({**case, "outcome": json.loads(json.dumps(outcome))})
    return base, results


def checkpoint(database: Path) -> bytes:
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()
    return database.read_bytes()


def seeds(folder: Path) -> dict[str, bytes]:
    fresh = folder / "fresh" / "authority.sqlite3"
    fresh.parent.mkdir()
    with AuthorityOwner.open(fresh, "package-runs") as owner:
        owner.prepare_new_campaign_storage()
    fresh_bytes = checkpoint(fresh)
    riichi = folder / "riichi" / "authority.sqlite3"
    riichi.parent.mkdir()
    riichi.write_bytes(fresh_bytes)
    store = a._open_for_application_owner(riichi)
    try:
        with admit_package_archive(io.BytesIO(ARCHIVES["riichi"])) as admitted:
            store.stage_package_archive(admitted, "install-1")
            store.publish_package_archive(admitted, "install-1")
    finally:
        store.close()
    return {"fresh": fresh_bytes, "riichi": checkpoint(riichi)}


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "archives").mkdir(parents=True)
    for name, data in ARCHIVES.items():
        (OUT / "archives" / f"{name}.archive").write_bytes(data)
    results = []
    with tempfile.TemporaryDirectory(dir=HERE, ignore_cleanup_errors=True) as scratch:
        seed_bytes = seeds(Path(scratch))
        for name, data in seed_bytes.items():
            (OUT / f"seed_{name}.sqlite3.gz").write_bytes(gzip.compress(data, mtime=0))
        for spec in RUNS:
            folder = Path(scratch) / str(spec["name"])
            folder.mkdir()
            path = folder / "authority.sqlite3"
            path.write_bytes(seed_bytes[str(spec["seed"])])
            state: dict[str, object] = {"path": path, "store": a._open_for_application_owner(path)}
            try:
                outcomes = []
                for op in spec["operations"]:  # type: ignore[union-attr]
                    if state["store"] is None:
                        break
                    outcomes.append(run(state, op))
            finally:
                if state["store"] is not None:
                    state["store"].close()  # type: ignore[union-attr]
            if spec.get("compare_db"):
                (OUT / f"{spec['name']}.sqlite3.gz").write_bytes(gzip.compress(checkpoint(path), mtime=0))
            results.append({**spec, "outcomes": outcomes})
        participant_base, participant_results = participant_cases(Path(scratch))
        (OUT / "participants_v4.sqlite3.gz").write_bytes(gzip.compress(participant_base, mtime=0))
    record = {"python": sys.version.split()[0], "runs": results, "participant_cases": participant_results}
    (OUT / "package_runs.json").write_bytes((json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
