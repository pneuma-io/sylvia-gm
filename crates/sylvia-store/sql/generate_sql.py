"""Copy the authority migration SQL out of the frozen Python reference, byte for byte.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -B crates/sylvia-store/sql/generate_sql.py

It writes one `.sql` file per SQL constant the Python store executes or hashes, and
`schema_vectors.json` with every migration name and SHA-256, the expected extension rows, the
expected schema identities (as digests) and the trigger identity, all computed by the Python modules
themselves. The Rust store loads the `.sql` files with `include_str!` and its tests check the
vectors, so no Python is needed at run time. Reference tooling only; retired with the snapshot.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.infrastructure import (  # noqa: E402
    sqlite_authority as authority,
    sqlite_campaign,
    sqlite_continuity,
    sqlite_draft_lifecycle,
    sqlite_effort,
    sqlite_mechanics_choice,
    sqlite_packages,
    sqlite_reasoning,
    sqlite_recovery,
    sqlite_session,
    sqlite_state_ownership,
)

OUT = Path(__file__).resolve().parent

# File name -> exact Python constant.
SQL_FILES = {
    "m01_f1_neutral_authority.sql": authority._MIGRATION_1,
    "m02_f2_provisional_event_protocol.sql": authority._MIGRATION_2,
    "m03_f3_sqlite_package_library.sql": sqlite_packages.MIGRATION_3_SQL,
    "m04_f4_builder_dm_sessions.sql": sqlite_session.MIGRATION_4_SQL,
    "m05_predecessor.sql": sqlite_campaign.PREDECESSOR_MIGRATION_5_SQL,
    "m05_campaign_identity_delta.sql": sqlite_campaign.CAMPAIGN_IDENTITY_DELTA_SQL,
    "ext_lifecycle_v3.sql": sqlite_campaign.LIFECYCLE_EXTENSION_SQL,
    "ext_recovery_v1.sql": sqlite_recovery.RECOVERY_EXTENSION_SQL,
    "ext_purge_campaign_operations.sql": sqlite_campaign.PURGE_CAMPAIGN_OPERATIONS_SQL,
    "ext_purge_v1.sql": sqlite_campaign.PURGE_EXTENSION_SQL,
    "m06_state_ownership.sql": sqlite_state_ownership.MIGRATION_6_SQL,
    "m07_mechanics_choice.sql": sqlite_mechanics_choice.MIGRATION_7_SQL,
    "m08_standalone_builder_chats.sql": sqlite_session.MIGRATION_8_SQL,
    "m09_campaign_state_continuity.sql": sqlite_continuity.MIGRATION_9_SQL,
    "m10_unbound_draft_lifecycle.sql": sqlite_draft_lifecycle.MIGRATION_10_SQL,
    "m11_private_provider_records.sql": sqlite_reasoning.MIGRATION_11_SQL,
    "m12_session_reasoning_preferences.sql": sqlite_effort.MIGRATION_12_SQL,
}


def trigger_fixture_sql() -> str:
    """The literal schema `expected_trigger_identity` builds before applying migration 6."""

    tree = ast.parse(inspect.getsource(sqlite_state_ownership.expected_trigger_identity))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "executescript"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            return node.args[0].value
    raise SystemExit("trigger fixture schema not found")


def identity(value: tuple[tuple[str, str], ...]) -> list[list[str]]:
    return [list(item) for item in value]


def identity_digest(value: tuple[tuple[str, str], ...]) -> dict[str, object]:
    """Table count and SHA-256 of `json.dumps` (default options) of the identity."""
    encoded = json.dumps(identity(value)).encode("utf-8")
    return {"tables": len(value), "sha256": hashlib.sha256(encoded).hexdigest()}


def python_whitespace() -> list[int]:
    """Code points `str.split()` separates on."""
    return [code for code in range(sys.maxunicode + 1) if chr(code).isspace()]


def casefold_to_ascii() -> dict[str, str]:
    """Non-ASCII code points whose `casefold()` is entirely ASCII."""
    table: dict[str, str] = {}
    for code in range(0x80, sys.maxunicode + 1):
        if 0xD800 <= code <= 0xDFFF:
            continue
        folded = chr(code).casefold()
        if folded.isascii():
            table[f"{code:04X}"] = folded
    return table


def main() -> None:
    for name, text in SQL_FILES.items():
        (OUT / name).write_bytes(text.encode("utf-8"))
    (OUT / "trigger_fixture.sql").write_bytes(trigger_fixture_sql().encode("utf-8"))

    a = authority
    vectors = {
        "python": sys.version.split()[0],
        "sqlite_version": sqlite3.sqlite_version,
        "migrations": [
            [1, a._MIGRATION_1_NAME, a._MIGRATION_1_SHA256],
            [2, a._MIGRATION_2_NAME, a._MIGRATION_2_SHA256],
            [3, a._MIGRATION_3_NAME, a._MIGRATION_3_SHA256],
            [4, a._MIGRATION_4_NAME, a._MIGRATION_4_SHA256],
            [5, a._MIGRATION_5_NAME, a._MIGRATION_5_SHA256],
            [6, a._MIGRATION_6_NAME, a._MIGRATION_6_SHA256],
            [7, a._MIGRATION_7_NAME, a._MIGRATION_7_SHA256],
            [8, a._MIGRATION_8_NAME, a._MIGRATION_8_SHA256],
            [9, a._MIGRATION_9_NAME, a._MIGRATION_9_SHA256],
            [10, a._MIGRATION_10_NAME, a._MIGRATION_10_SHA256],
            [11, sqlite_reasoning.MIGRATION_11_NAME, sqlite_reasoning.MIGRATION_11_SHA256],
            [12, sqlite_effort.MIGRATION_12_NAME, sqlite_effort.MIGRATION_12_SHA256],
        ],
        "predecessor_migration_5_sha256": a._PREDECESSOR_MIGRATION_5_SHA256,
        "extensions": {
            "campaign_identity": [
                a._CAMPAIGN_IDENTITY_EXTENSION_NAME,
                a._CAMPAIGN_IDENTITY_DELTA_SHA256,
            ],
            "lifecycle": [a._LIFECYCLE_EXTENSION_NAME, a._LIFECYCLE_EXTENSION_SHA256],
            "recovery": [
                sqlite_recovery.RECOVERY_EXTENSION_NAME,
                sqlite_recovery.RECOVERY_EXTENSION_SHA256,
            ],
            "purge": [a._PURGE_EXTENSION_NAME, a._PURGE_EXTENSION_SHA256],
        },
        "extension_rows": {
            f"recovery={recovery},purge={purge}": [
                list(row)
                for row in a._expected_campaign_extension_rows(
                    include_recovery=recovery, include_purge=purge
                )
            ]
            for recovery in (False, True)
            for purge in (False, True)
        },
        "schema_identities": {
            (
                name.removeprefix("_EXPECTED_").removesuffix("SCHEMA_IDENTITY").rstrip("_").lower()
                or "base"
            ): identity_digest(getattr(a, name))
            for name in sorted(dir(a))
            if name.startswith("_EXPECTED_") and name.endswith("SCHEMA_IDENTITY")
        },
        "trigger_identity": identity(sqlite_state_ownership.EXPECTED_TRIGGER_IDENTITY),
        "python_whitespace": python_whitespace(),
        "casefold_to_ascii": casefold_to_ascii(),
    }
    text = json.dumps(vectors, indent=1, ensure_ascii=True) + "\n"
    (OUT / "schema_vectors.json").write_bytes(text.encode("utf-8"))


if __name__ == "__main__":
    main()
