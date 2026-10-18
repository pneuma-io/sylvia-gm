"""Portable export measures actual rows while keeping allocation and archive bounds."""

import sqlite3

import pytest

from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.infrastructure import campaign_archive
from sylvia_gm.infrastructure.sqlite_authority import SQLiteAuthorityStore


@pytest.mark.parametrize("value", [
    "", "ordinary text", '"\\\n\r\t\b\f\x00\x1f',
    "\u00e9\u754c\U0001f9ed" * 20_000, "\x01" * 20_000,
    None, 0, -42, 1.5,
], ids=["empty", "plain", "escapes", "unicode-chunks", "control-chunks",
        "null", "zero", "negative", "float"])
def test_preflight_size_matches_canonical_bytes_including_escaping(value):
    row = {"operation_id": "synthetic", "semantic_payload_json": value, "result_json": None}
    assert campaign_archive.canonical_authority_record_size("campaign_operations", row) == len(
        campaign_archive.canonical_authority_record("campaign_operations", row)
    )


@pytest.fixture
def relations():
    """Isolate size preflight; admitted-campaign recovery is covered by the Rift journey test."""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    for table in campaign_archive.TABLES_IN_ORDER:
        columns = ", ".join(f'"{column}"' for column in campaign_archive.TABLE_COLUMNS[table])
        connection.execute(f'CREATE TABLE "{table}" ({columns})')
    connection.execute("INSERT INTO campaigns (campaign_id) VALUES ('synthetic')")
    try:
        yield connection
    finally:
        connection.close()


def _preflight(connection):
    return SQLiteAuthorityStore._preflight_export_relations(
        None, connection,
        dict.fromkeys(campaign_archive.TABLES_IN_ORDER, ("1=1", ())),
    )


def test_many_small_operations_do_not_repeat_the_large_construction_size(relations):
    for index in range(75):
        relations.execute(
            "INSERT INTO campaign_operations (operation_id, semantic_payload_json) VALUES (?, ?)",
            (str(index), "x" * (160_000 if index == 0 else 10)),
        )
    _preflight(relations)


@pytest.mark.parametrize("limit_name,limit,text,expected", [
    ("single_text_or_json_value_bytes", 100, "x" * 101,
     "single_text_or_json_value_bytes"),
    ("single_authority_row_bytes", 1000, "x" * 1001, "single_authority_row_bytes"),
    ("single_authority_row_bytes", 1000, "\x01" * 200, "single_authority_row_bytes"),
    ("authority_jsonl_bytes", 1000, "x" * 800, "authority_jsonl_bytes"),
])
def test_preflight_still_rejects_actual_value_row_and_total_overflow(
    relations, monkeypatch, limit_name, limit, text, expected,
):
    monkeypatch.setattr(campaign_archive, "LIMITS", {
        **campaign_archive.LIMITS, limit_name: limit,
    })
    relations.execute(
        "INSERT INTO campaign_operations (operation_id, semantic_payload_json) VALUES (?, ?)",
        ("operation", text),
    )
    with pytest.raises(CommandRejected, match="portable_export_limit_" + expected):
        _preflight(relations)
