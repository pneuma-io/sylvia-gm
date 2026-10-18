"""Synthetic optional Thinking archive validation; no private campaign fixtures."""

import json

import pytest

from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.infrastructure import private_records_archive as codec


def record(**changes: object) -> dict[str, object]:
    return {
        "session_id": "selected", "attempt_id": "attempt", "response_id": "response",
        "turn_id": "turn", "basis_message_id": "input", "source_order": 0,
        "provider_id": "synthetic", "model_id": "synthetic", "kind": "thinking",
        "payload": "Synthetic reasoning\nwith Unicode: ✨", "status": "completed", **changes,
    }


def test_private_archive_round_trip_preserves_native_payload_bytes() -> None:
    native = '{ "anthropic_content" : [{"type":"redacted_thinking", "data":"opaque"}] }'
    rows = [record(), record(source_order=1, kind="continuation", payload=native)]
    encoded = codec.encode(rows, session_ids={"selected"})
    assert codec.decode(encoded, session_ids={"selected"}) == tuple(rows)
    assert codec.decode(b"", session_ids={"selected"}) == ()


@pytest.mark.parametrize("changes", [
    {"session_id": "foreign"}, {"source_order": True}, {"source_order": -1},
    {"kind": "public"}, {"status": "done"}, {"response_id": ""},
    {"kind": "continuation", "payload": "[]"},
    {"kind": "continuation", "payload": '{"signature":1,"signature":2}'},
])
def test_private_archive_rejects_invalid_records_without_echoing_payload(changes: dict) -> None:
    with pytest.raises(CommandRejected) as rejected:
        codec.encode([record(**changes)], session_ids={"selected"})
    assert "Synthetic reasoning" not in str(rejected.value)


def test_private_archive_rejects_duplicate_identity_and_mixed_attempt_status() -> None:
    for rows in ([record(), record()], [record(), record(source_order=1, status="partial")]):
        with pytest.raises(CommandRejected):
            codec.encode(rows, session_ids={"selected"})


def test_private_archive_bounds_and_framing(monkeypatch: pytest.MonkeyPatch) -> None:
    encoded = codec.encode([record()], session_ids={"selected"})
    for invalid in (encoded[:-1], b"[]\n", b'{"a":1,"a":2}\n', b"\xff\n"):
        with pytest.raises(CommandRejected):
            codec.decode(invalid, session_ids={"selected"})
    monkeypatch.setattr(codec, "MAX_BYTES", len(encoded) - 1)
    with pytest.raises(CommandRejected):
        codec.decode(encoded, session_ids={"selected"})
    with pytest.raises(CommandRejected):
        codec.encode([record()], session_ids={"selected"})
    assert json.loads(encoded)["payload"] == record()["payload"]


@pytest.mark.parametrize("nested_field", ["row", "continuation"])
def test_private_archive_rejects_deep_json_without_an_internal_error(nested_field: str) -> None:
    """Optional saved provider records share the recovery parser's bounded error contract."""
    nested = "[" * 1600 + "0" + "]" * 1600
    encoded = (
        nested.encode() + b"\n" if nested_field == "row"
        else (json.dumps(record(kind="continuation", payload=nested)) + "\n").encode()
    )
    with pytest.raises(CommandRejected) as rejected:
        codec.decode(encoded, session_ids={"selected"})
    assert rejected.value.code == "portable_export_invalid_private_records"
    assert nested not in str(rejected.value)
