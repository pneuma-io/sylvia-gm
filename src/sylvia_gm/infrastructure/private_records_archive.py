"""Bounded optional archive member for private provider records, never ordinary history."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping

from sylvia_gm.domain.errors import CommandRejected

MEMBER_NAME = "provider-private.jsonl"
MAX_BYTES = 67_108_864
MAX_ROW_BYTES = 16_777_216
MAX_ROWS = 250_000
FIELDS = (
    "session_id", "attempt_id", "response_id", "turn_id", "basis_message_id",
    "source_order", "provider_id", "model_id", "kind", "payload", "status",
)


def _reject() -> CommandRejected:
    return CommandRejected(
        "Invalid private provider archive member", code="portable_export_invalid_private_records",
    )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _reject()
        result[key] = value
    return result


def validate_rows(
    rows: Iterable[Mapping[str, object]], *, session_ids: set[str],
) -> tuple[dict[str, object], ...]:
    """Reject foreign sessions, ambiguous identities and inconsistent attempt status."""
    result: list[dict[str, object]] = []
    identities: set[tuple[str, str, int]] = set()
    attempts: dict[tuple[str, str], str] = {}
    for row in rows:
        if len(result) >= MAX_ROWS or not isinstance(row, Mapping) or set(row) != set(FIELDS):
            raise _reject()
        if any(not isinstance(row[key], str) or not row[key] for key in FIELDS
               if key != "source_order"):
            raise _reject()
        session_id, attempt_id = str(row["session_id"]), str(row["attempt_id"])
        order = row["source_order"]
        if session_id not in session_ids or type(order) is not int or order < 0:
            raise _reject()
        if row["kind"] not in {"thinking", "continuation"} or row["status"] not in {
            "partial", "completed", "interrupted",
        }:
            raise _reject()
        identity = (session_id, attempt_id, order)
        if identity in identities:
            raise _reject()
        identities.add(identity)
        status = str(row["status"])
        if attempts.setdefault((session_id, attempt_id), status) != status:
            raise _reject()
        if row["kind"] == "continuation":
            try:
                native = json.loads(str(row["payload"]), object_pairs_hook=_unique_object)
            except (ValueError, TypeError, RecursionError):
                raise _reject() from None
            if not isinstance(native, dict):
                raise _reject()
        result.append({key: row[key] for key in FIELDS})
    return tuple(result)


def encode(rows: Iterable[Mapping[str, object]], *, session_ids: set[str]) -> bytes:
    """Encode an explicitly selected private component without changing payload strings."""
    output = bytearray()
    for row in validate_rows(rows, session_ids=session_ids):
        try:
            line = (json.dumps(row, ensure_ascii=False, allow_nan=False,
                               separators=(",", ":")) + "\n").encode("utf-8")
        except (ValueError, UnicodeError):
            raise _reject() from None
        if len(line) > MAX_ROW_BYTES or len(output) + len(line) > MAX_BYTES:
            raise _reject()
        output.extend(line)
    return bytes(output)


def decode(data: bytes, *, session_ids: set[str]) -> tuple[dict[str, object], ...]:
    """Decode bounded JSONL; absence is handled by the archive, never fabricated here."""
    if len(data) > MAX_BYTES or (data and not data.endswith(b"\n")):
        raise _reject()
    rows: list[dict[str, object]] = []
    for line in data.splitlines(keepends=True):
        if len(line) > MAX_ROW_BYTES or len(rows) >= MAX_ROWS:
            raise _reject()
        try:
            row = json.loads(line, object_pairs_hook=_unique_object)
        except (ValueError, UnicodeError, RecursionError):
            raise _reject() from None
        if not isinstance(row, dict):
            raise _reject()
        rows.append(row)
    return validate_rows(rows, session_ids=session_ids)
