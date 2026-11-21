"""Build authority databases with the frozen Python store and record how Python opens them.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -B crates/sylvia-store/tests/vectors/generate_schema_fixtures.py

For each supported schema state it writes `fixtures/<name>.sqlite3.gz`, a database made by the
Python modules themselves, and an entry in `schema_fixtures.json` recording, for a copy of it:

- `open`: the state after `SQLiteAuthorityStore` opens it (the startup migration chain);
- `campaign`: the state after a campaign operation then advances it (`_ensure_campaign_schema`
  inside `BEGIN IMMEDIATE`, as `builder_chat_index` does);
- each state's `user_version`, `sqlite_master` rows (by digest into `schemas`),
  `schema_migrations` rows, `authority_schema_extensions` rows and the pre-migration copies
  Python wrote.

The Rust tests open the same bytes and must reach the same states. Reference tooling only.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.infrastructure import (  # noqa: E402
    sqlite_authority as a,
    sqlite_campaign,
    sqlite_continuity,
    sqlite_draft_lifecycle,
    sqlite_effort,
    sqlite_mechanics_choice,
    sqlite_reasoning,
    sqlite_recovery,
    sqlite_state_ownership,
)

OUT = Path(__file__).resolve().parent
FIXTURES = OUT / "fixtures"
DATABASE = "authority.sqlite3"
# Distinct `sqlite_master` listings by the SHA-256 of their JSON, so each is written once.
SCHEMAS: dict[str, list[list[object]]] = {}


def open_store(path: Path) -> a.SQLiteAuthorityStore:
    return a._open_for_application_owner(path)


def statements(connection: sqlite3.Connection, sql: str) -> None:
    for statement in sql.split(";"):
        if statement.strip():
            connection.execute(statement)


def migration(connection: sqlite3.Connection, version: int, name: str, sha256: str) -> None:
    connection.execute(
        "INSERT INTO schema_migrations(version, applied_name, sql_sha256) VALUES (?, ?, ?)",
        (version, name, sha256),
    )
    connection.execute(f"PRAGMA user_version = {version}")


def extension(connection: sqlite3.Connection, row: tuple[str, str, str, str]) -> None:
    connection.execute(
        "INSERT INTO authority_schema_extensions (extension_name, extension_sha256, "
        "predecessor_migration_name, predecessor_sql_sha256) VALUES (?, ?, ?, ?)",
        row,
    )


IDENTITY_ROW = (
    a._CAMPAIGN_IDENTITY_EXTENSION_NAME,
    a._CAMPAIGN_IDENTITY_DELTA_SHA256,
    a._MIGRATION_5_NAME,
    a._PREDECESSOR_MIGRATION_5_SHA256,
)
LIFECYCLE_ROW = (
    a._LIFECYCLE_EXTENSION_NAME,
    a._LIFECYCLE_EXTENSION_SHA256,
    a._CAMPAIGN_IDENTITY_EXTENSION_NAME,
    a._CAMPAIGN_IDENTITY_DELTA_SHA256,
)


# Each step advances a schema-4 database in Python's own order (`_ensure_campaign_schema`).
def to_predecessor_5(c: sqlite3.Connection) -> None:
    statements(c, a._PREDECESSOR_MIGRATION_5)
    migration(c, 5, a._MIGRATION_5_NAME, a._PREDECESSOR_MIGRATION_5_SHA256)


def to_probe01_5(c: sqlite3.Connection) -> None:
    to_predecessor_5(c)
    statements(c, a._CAMPAIGN_IDENTITY_DELTA)
    extension(c, IDENTITY_ROW)


def to_w11_5(c: sqlite3.Connection) -> None:
    statements(c, a._MIGRATION_5)
    sqlite_campaign.migrate_v4_participants(c)
    extension(c, IDENTITY_ROW)
    sqlite_campaign.apply_lifecycle_extension(c)
    extension(c, LIFECYCLE_ROW)
    migration(c, 5, a._MIGRATION_5_NAME, a._MIGRATION_5_SHA256)


def to_w12_5(c: sqlite3.Connection) -> None:
    to_w11_5(c)
    a._apply_portable_recovery_extension(c)


def to_purge_5(c: sqlite3.Connection) -> None:
    to_w12_5(c)
    sqlite_campaign.apply_purge_extension(c)
    extension(
        c,
        (
            a._PURGE_EXTENSION_NAME,
            a._PURGE_EXTENSION_SHA256,
            sqlite_recovery.RECOVERY_EXTENSION_NAME,
            sqlite_recovery.RECOVERY_EXTENSION_SHA256,
        ),
    )


def to_6(c: sqlite3.Connection, base: Callable[[sqlite3.Connection], None] = to_w12_5) -> None:
    base(c)
    sqlite_state_ownership.apply_migration(c)
    migration(c, 6, a._MIGRATION_6_NAME, a._MIGRATION_6_SHA256)


def to_7(c: sqlite3.Connection, base: Callable[[sqlite3.Connection], None] = to_6) -> None:
    base(c)
    sqlite_mechanics_choice.apply_migration(c)
    migration(c, 7, a._MIGRATION_7_NAME, a._MIGRATION_7_SHA256)


def to_8(c: sqlite3.Connection, base: Callable[[sqlite3.Connection], None] = to_7) -> None:
    base(c)
    statements(c, a._MIGRATION_8)
    migration(c, 8, a._MIGRATION_8_NAME, a._MIGRATION_8_SHA256)


def to_9(c: sqlite3.Connection, base: Callable[[sqlite3.Connection], None] = to_8) -> None:
    base(c)
    sqlite_continuity.apply_migration(c)
    migration(c, 9, a._MIGRATION_9_NAME, a._MIGRATION_9_SHA256)


def to_10(c: sqlite3.Connection) -> None:
    to_9(c)
    sqlite_draft_lifecycle.apply_migration(c)
    migration(c, 10, a._MIGRATION_10_NAME, a._MIGRATION_10_SHA256)


def to_11(c: sqlite3.Connection) -> None:
    to_10(c)
    sqlite_reasoning.apply_migration(c)
    migration(c, 11, sqlite_reasoning.MIGRATION_11_NAME, sqlite_reasoning.MIGRATION_11_SHA256)


def to_12(c: sqlite3.Connection) -> None:
    to_11(c)
    sqlite_effort.apply_migration(c)
    migration(c, 12, sqlite_effort.MIGRATION_12_NAME, sqlite_effort.MIGRATION_12_SHA256)


def to_12_purge(c: sqlite3.Connection) -> None:
    to_12(c)
    a.SQLiteAuthorityStore._ensure_purge_schema(_IdentityOnly(), c)


class _IdentityOnly:
    """Lets `_ensure_purge_schema` run without an open store; it only verifies identity."""

    def _verify_exact_schema_identity(self, connection: sqlite3.Connection, **kw: object) -> int:
        return a.SQLiteAuthorityStore._verify_exact_schema_identity(self, connection, **kw)


ADVANCE: dict[str, Callable[[sqlite3.Connection], None] | None] = {
    "v3": None,
    "v4": None,
    "v5_predecessor": to_predecessor_5,
    "v5_probe01": to_probe01_5,
    "v5_w11": to_w11_5,
    "v5_w12": to_w12_5,
    "v5_purge": to_purge_5,
    "v6": to_6,
    "v6_purge": lambda c: to_6(c, to_purge_5),
    "v7": to_7,
    "v7_purge": lambda c: to_7(c, lambda d: to_6(d, to_purge_5)),
    "v8": to_8,
    "v8_purge": lambda c: to_8(c, lambda d: to_7(d, lambda e: to_6(e, to_purge_5))),
    "v9": to_9,
    "v9_purge": lambda c: to_9(
        c, lambda d: to_8(d, lambda e: to_7(e, lambda f: to_6(f, to_purge_5)))
    ),
    "v10": to_10,
    "v11": to_11,
    "v12": to_12,
    "v12_purge": to_12_purge,
}


def build(folder: Path, name: str) -> Path:
    """A database in the named state, made by the Python store and modules."""

    database = folder / DATABASE
    open_store(database).close()  # a fresh open stops at schema 3
    if name == "v3":
        return database
    store = open_store(database)
    store._ensure_session_schema()
    store.close()
    shutil.rmtree(folder / "migration-sources", ignore_errors=True)
    advance = ADVANCE[name]
    if advance is not None:
        connection = sqlite3.connect(database, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        advance(connection)
        connection.commit()
        connection.close()
    checkpoint(database)
    return database


def checkpoint(database: Path) -> None:
    connection = sqlite3.connect(database, isolation_level=None)
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()


def state(database: Path) -> dict[str, object]:
    connection = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
    try:
        tables = {
            str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master")
        }
        result: dict[str, object] = {
            "user_version": connection.execute("PRAGMA user_version").fetchone()[0],
            "journal_mode": connection.execute("PRAGMA journal_mode").fetchone()[0],
            "schema": schema_digest(
                [
                    list(row)
                    for row in connection.execute(
                        "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
                    )
                ]
            ),
            "schema_migrations": [
                list(row)
                for row in connection.execute("SELECT * FROM schema_migrations ORDER BY rowid")
            ],
            "authority_schema_extensions": (
                [
                    list(row)
                    for row in connection.execute(
                        "SELECT * FROM authority_schema_extensions ORDER BY rowid"
                    )
                ]
                if "authority_schema_extensions" in tables
                else None
            ),
        }
    finally:
        connection.close()
    sources = database.parent / "migration-sources"
    result["migration_sources"] = (
        sorted(path.name for path in sources.iterdir()) if sources.exists() else []
    )
    return result


def schema_digest(rows: list[list[object]]) -> str:
    digest = hashlib.sha256(json.dumps(rows).encode("utf-8")).hexdigest()
    SCHEMAS[digest] = rows
    return digest


def outcome(folder: Path, action: Callable[[], None]) -> dict[str, object]:
    try:
        action()
    except Exception as error:  # noqa: BLE001 - the error is the recorded behaviour
        return {"error": {"type": type(error).__name__, "message": str(error)}}
    return {"state": state(folder / DATABASE)}


def ensure_campaign(database: Path) -> None:
    store = open_store(database)
    try:
        connection = store._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            store._ensure_campaign_schema(connection)
            connection.commit()
        finally:
            if connection.in_transaction:
                connection.rollback()
            connection.close()
    finally:
        store.close()


def main() -> None:
    FIXTURES.mkdir(exist_ok=True)
    record: dict[str, object] = {"python": sys.version.split()[0], "fixtures": {}}
    with tempfile.TemporaryDirectory(dir=OUT) as scratch:
        for name in ADVANCE:
            made = Path(scratch) / name / "made"
            made.mkdir(parents=True)
            database = build(made, name)
            data = database.read_bytes()
            (FIXTURES / f"{name}.sqlite3.gz").write_bytes(gzip.compress(data, mtime=0))
            entry: dict[str, object] = {"made": state(database)}

            opened = Path(scratch) / name / "open"
            opened.mkdir()
            (opened / DATABASE).write_bytes(data)
            entry["open"] = outcome(opened, lambda: open_store(opened / DATABASE).close())

            advanced = Path(scratch) / name / "campaign"
            advanced.mkdir()
            (advanced / DATABASE).write_bytes(data)
            entry["campaign"] = outcome(advanced, lambda: ensure_campaign(advanced / DATABASE))
            record["fixtures"][name] = entry  # type: ignore[index]

        fresh = Path(scratch) / "fresh"
        fresh.mkdir()

        def fresh_open() -> None:
            store = open_store(fresh / DATABASE)
            store.prepare_new_campaign_storage()
            store.close()

        record["fresh"] = outcome(fresh, fresh_open)
    record["schemas"] = dict(sorted(SCHEMAS.items()))
    text = json.dumps(record, indent=1, ensure_ascii=True) + "\n"
    (OUT / "schema_fixtures.json").write_bytes(text.encode("utf-8"))


if __name__ == "__main__":
    main()
