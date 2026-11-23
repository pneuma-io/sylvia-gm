"""A base built at test time instead of committed: a small seed, a bundled package installed
through each side's own install path, then the rows a generator's setup wrote.

Generators that need a large installed package (the bundled SRD Lite archive is ~6 MB gzipped
once installed) use this instead of committing the installed database:

- `seed_copy(path)` writes the small committed seed, `sylvia-store`'s fresh profile
  (`tests/vectors/packages/seed_fresh.sqlite3.gz`), to `path`;
- after installing with Python's owner, `table_digests(path)` records each table's digest;
- after the generator's own setup (campaigns, participants, ...), `changed_rows(before, after)`
  records the full final rows of each table the setup changed, typed for JSON;
- after the run, `changed_rows` again records what the run changed.

The Rust half is `crates/sylvia-app/tests/support/installed_base.rs`: it writes the same seed,
installs the same archive through the ported `install_package_archive`, checks every table's
digest against Python's, writes the setup's rows back, and compares the run's changed tables.

A table's digest is the SHA-256 of its name, its row count and its rows sorted by their
encoding, where a row is its values in column order, each one a type tag (`n` null, `i`
integer, `r` real, `t` text, `b` blob), an 8-byte big-endian length and its bytes (an integer
in decimal ASCII, a real as its 8 IEEE-754 bytes, big-endian; text as UTF-8). Reference tooling
only.
"""

from __future__ import annotations

import gzip
import hashlib
import sqlite3
import struct
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[4]
SEED = REPOSITORY / "crates" / "sylvia-store" / "tests" / "vectors" / "packages" / "seed_fresh.sqlite3.gz"


def seed_copy(path: Path) -> None:
    """The committed seed, decompressed to `path`."""
    path.write_bytes(gzip.decompress(SEED.read_bytes()))


def checkpoint(path: Path) -> None:
    """Fold the WAL into the main file, so a second connection reads every committed row."""
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()


def _tables(connection: sqlite3.Connection) -> list[str]:
    return [row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]


def _value_bytes(value: object) -> bytes:
    if value is None:
        tag, body = b"n", b""
    elif isinstance(value, int):
        tag, body = b"i", str(value).encode("ascii")
    elif isinstance(value, float):
        tag, body = b"r", struct.pack(">d", value)
    elif isinstance(value, str):
        tag, body = b"t", value.encode("utf-8")
    elif isinstance(value, bytes):
        tag, body = b"b", value
    else:
        raise TypeError(f"unexpected SQLite value {type(value).__name__}")
    return tag + struct.pack(">Q", len(body)) + body


def table_digests(path: Path) -> dict[str, str]:
    """Each table's digest (see the module documentation), by table name."""
    connection = sqlite3.connect(path)
    try:
        out = {}
        for table in _tables(connection):
            rows = sorted(b"".join(_value_bytes(value) for value in row)
                          for row in connection.execute(f'SELECT * FROM "{table}"'))
            digest = hashlib.sha256()
            digest.update(table.encode("utf-8") + b"\0" + str(len(rows)).encode("ascii") + b"\0")
            for row in rows:
                digest.update(struct.pack(">Q", len(row)) + row)
            out[table] = digest.hexdigest()
        return out
    finally:
        connection.close()


def _typed(value: object) -> object:
    if isinstance(value, float):
        return {"real": struct.pack(">d", value).hex()}
    if isinstance(value, bytes):
        return {"blob": value.hex()}
    return value


def table_rows(path: Path, tables: list[str] | None = None) -> dict[str, list[list[object]]]:
    """The rows of `tables` (all when `None`), typed for JSON (`{"real": hex}`, `{"blob": hex}`)
    and in table order (`rowid` where there is one, so a replay inserts them in their order)."""
    connection = sqlite3.connect(path)
    try:
        out = {}
        for table in _tables(connection) if tables is None else tables:
            out[table] = [[_typed(value) for value in row]
                          for row in connection.execute(f'SELECT * FROM "{table}"')]
        return out
    finally:
        connection.close()


def changed_rows(before: Path, after: Path) -> dict[str, list[list[object]]]:
    """The full rows, in `after`, of each table whose digest differs between the two databases."""
    old, new = table_digests(before), table_digests(after)
    if set(old) != set(new):
        raise ValueError(f"the tables differ: {sorted(set(old) ^ set(new))}")
    return table_rows(after, sorted(table for table in new if new[table] != old[table]))
