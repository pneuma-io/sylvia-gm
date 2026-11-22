"""The frozen portable archive ABI the store shares with the export, from `campaign_archive.py`.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -B crates/sylvia-store/tests/vectors/generate_campaign_archive_vectors.py

It writes `campaign_archive/archive_abi.json`: `TABLES_IN_ORDER` with each table's
`TABLE_COLUMNS` and `TABLE_KEYS`, `source_authority_schema_claim_sha256` for every source schema,
the expected migration and extension lineages, and `validate_migration_provenance`'s outcome
on valid and damaged provenance, with and without `enforce_declared_order_bytes`.
`tests/campaign_archive.rs` checks the Rust module against it. Reference tooling only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3] / "src"))

from sylvia_gm.infrastructure import campaign_archive as c  # noqa: E402

OUT = HERE / "campaign_archive"


def lineage(version: int) -> dict[str, object]:
    migrations = {
        9: c.PREDECESSOR_SCHEMA_MIGRATION_PROVENANCE,
        10: c.PRE_REASONING_SCHEMA_MIGRATION_PROVENANCE,
        11: c.PRE_EFFORT_SCHEMA_MIGRATION_PROVENANCE,
        12: c.EXPECTED_SCHEMA_MIGRATION_PROVENANCE,
    }[version]
    return {
        "schema_migrations": [dict(zip(c.SCHEMA_MIGRATION_FIELDS, row)) for row in migrations],
        "schema_extensions": [dict(zip(c.SCHEMA_EXTENSION_FIELDS, row)) for row in c.EXPECTED_SCHEMA_EXTENSION_PROVENANCE],
    }


def migrations(value: dict, change) -> dict:  # noqa: ANN001
    return {**value, "schema_migrations": change(value["schema_migrations"])}


def extensions(value: dict, change) -> dict:  # noqa: ANN001
    return {**value, "schema_extensions": change(value["schema_extensions"])}


def first(items: list, **changes: object) -> list:
    return [{**items[0], **changes}, *items[1:]]


BASE = lineage(12)
PURGE = dict(zip(c.SCHEMA_EXTENSION_FIELDS, c.PURGE_SCHEMA_EXTENSION_PROVENANCE))
CASES: list[tuple[str, object, object]] = [
    ("valid", BASE, 12),
    ("valid_11", lineage(11), 11),
    ("valid_10", lineage(10), 10),
    ("valid_9", lineage(9), 9),
    ("lineage_for_other_schema", lineage(11), 12),
    ("with_purge", extensions(BASE, lambda items: sorted([*items, PURGE], key=lambda item: item["extension_name"])), 12),
    ("purge_unsorted", extensions(BASE, lambda items: [*items, PURGE]), 12),
    ("not_an_object", [], 12),
    ("missing_member", {"schema_migrations": BASE["schema_migrations"]}, 12),
    ("members_reordered", {"schema_extensions": BASE["schema_extensions"], "schema_migrations": BASE["schema_migrations"]}, 12),
    ("migrations_empty", migrations(BASE, lambda items: []), 12),
    ("migrations_not_list", migrations(BASE, lambda items: {}), 12),
    ("extensions_not_list", extensions(BASE, lambda items: "x"), 12),
    ("migration_not_object", migrations(BASE, lambda items: [1, *items[1:]]), 12),
    ("migration_fields_reordered", migrations(BASE, lambda items: [
        {"applied_name": item["applied_name"], "version": item["version"], "sql_sha256": item["sql_sha256"]}
        for item in items]), 12),
    ("migration_extra_field", migrations(BASE, lambda items: first(items, note="x")), 12),
    ("migration_version_true", migrations(BASE, lambda items: first(items, version=True)), 12),
    ("migration_version_float", migrations(BASE, lambda items: first(items, version=1.0)), 12),
    ("migration_version_text", migrations(BASE, lambda items: first(items, version="1")), 12),
    ("migration_gap", migrations(BASE, lambda items: items[1:]), 12),
    ("migration_name_upper", migrations(BASE, lambda items: first(items, applied_name="F1_neutral")), 12),
    ("migration_name_long", migrations(BASE, lambda items: first(items, applied_name="a" * 129)), 12),
    ("migration_sha_upper", migrations(BASE, lambda items: first(items, sql_sha256=items[0]["sql_sha256"].upper())), 12),
    ("migration_sha_other", migrations(BASE, lambda items: first(items, sql_sha256="0" * 64)), 12),
    ("extension_names_unsorted", extensions(BASE, lambda items: list(reversed(items))), 12),
    ("extension_names_duplicate", extensions(BASE, lambda items: [items[0], *items]), 12),
    ("extension_name_number", extensions(BASE, lambda items: first(items, extension_name=5)), 12),
    ("extension_names_numbers", extensions(BASE, lambda items: [{**item, "extension_name": index}
                                                                for index, item in enumerate(items)]), 12),
    ("extension_names_mixed", extensions(BASE, lambda items: [{**items[0], "extension_name": 5}, *items[1:]]), 12),
    ("extension_name_bad", extensions(BASE, lambda items: [{**items[0], "extension_name": "_x"}, *items[1:]]), 12),
    ("extension_predecessor_sha_bad", extensions(BASE, lambda items: first(items, predecessor_sql_sha256="x")), 12),
    ("extensions_empty", extensions(BASE, lambda items: []), 12),
    ("schema_13", BASE, 13),
    ("schema_true", BASE, True),
    ("schema_float", BASE, 12.0),
    ("schema_text", BASE, "12"),
]


def outcome(value: object, enforce: bool, version: object) -> dict[str, object]:
    try:
        c.validate_migration_provenance(value, enforce_declared_order_bytes=enforce, source_schema_version=version)
    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
        return {"error": type(error).__name__, "code": getattr(error, "code", None)}
    return {"ok": None}


def main() -> None:
    OUT.mkdir(exist_ok=True)
    record = {
        "python": sys.version.split()[0],
        "tables": [[table, list(c.TABLE_COLUMNS[table]), list(c.TABLE_KEYS[table])] for table in c.TABLES_IN_ORDER],
        "schema_claims": {str(version): c.source_authority_schema_claim_sha256(version) for version in (9, 10, 11, 12)},
        "migration_lineages": {str(version): lineage(version)["schema_migrations"] for version in (9, 10, 11, 12)},
        "extension_lineage": lineage(12)["schema_extensions"],
        "purge_extension": PURGE,
        "provenance_cases": [
            {"name": name, "value": value, "version": version, "enforce": enforce,
             "outcome": outcome(value, enforce, version)}
            for name, value, version in CASES for enforce in (False, True)
        ],
    }
    (OUT / "archive_abi.json").write_bytes((json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
