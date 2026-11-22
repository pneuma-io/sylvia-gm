"""The owner's admin full audit state: what Python's `AuthorityOwner` keeps for each step.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-store/tests/vectors/generate_admin_audit_vectors.py

On a fresh authority, Python's owner runs `set_admin_full_audit` and `record_admin_full_audit`
through the cases the provider wrappers produce: payloads while disabled (ignored); a request,
then late usage before its response (held), then the response (which takes the held usage); late
usage after a response (credited in place, keeping the trace's position and key order); a
stream response; an exchange whose later usage replaces earlier held usage; late usage with no
saved request or response (dropped); payloads without an exchange identity or not mappings;
late usage without `usage` (Python's `KeyError`); re-enabling, which keeps the traces, and
disabling, which clears them. After each step it records the reply or failure,
`admin_full_audit_enabled()`, the trace list and the held usage. It writes
`admin_audit_vectors.json`; `tests/admin_audit.rs` replays the steps on the Rust state.
Reference tooling only.
"""

from __future__ import annotations

import gzip
import json
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3] / "src"))

from sylvia_gm.application.authority import AuthorityOwner  # noqa: E402

OUTPUT = HERE / "admin_audit_vectors.json"
BASE = HERE / "packages" / "seed_fresh.sqlite3.gz"


def step(op: str, value: object = None) -> dict[str, object]:
    return {"op": op, "value": value}


def request(exchange: str) -> dict[str, object]:
    return {"kind": "provider_request", "exchange_id": exchange, "model": "m", "body": {"messages": [1, 2]}}


def response(exchange: str, kind: str = "provider_response", usage: object = None) -> dict[str, object]:
    payload: dict[str, object] = {"kind": kind, "exchange_id": exchange, "status": 200}
    if usage is not None:
        payload["usage"] = usage
    payload["text"] = "done"
    return payload


def late(exchange: str, usage: object) -> dict[str, object]:
    return {"kind": "provider_late_usage", "exchange_id": exchange, "usage": usage}


STEPS = [
    step("record", request("x-0")),
    step("enabled"),
    step("set", True),
    step("record", request("x-1")),
    step("record", late("x-1", {"input_tokens": 10})),
    step("record", response("x-1")),
    step("record", request("x-2")),
    step("record", response("x-2", usage={"input_tokens": 1})),
    step("record", late("x-2", {"input_tokens": 20, "output_tokens": 5})),
    step("record", request("x-3")),
    step("record", late("x-3", {"input_tokens": 1})),
    step("record", late("x-3", {"input_tokens": 2})),
    step("record", response("x-3", kind="provider_stream_response")),
    step("record", late("x-unknown", {"input_tokens": 3})),
    step("record", {"kind": "defect", "detail": "no exchange"}),
    step("record", {"kind": "provider_response", "exchange_id": 7}),
    step("record", ["not", "a", "mapping"]),
    step("record", "text"),
    step("record", {"kind": "provider_late_usage", "exchange_id": "x-1"}),
    step("set", True),
    step("enabled"),
    step("record", request("x-4")),
    step("record", late("x-4", {"input_tokens": 4})),
    step("set", False),
    step("record", request("x-5")),
    step("set", True),
    step("record", response("x-4")),
]


def plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [plain(item) for item in value]
    return value


def main() -> None:
    records = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        database = Path(scratch) / "authority.sqlite3"
        database.write_bytes(gzip.decompress(BASE.read_bytes()))
        with AuthorityOwner.open(database, "admin-audit") as owner:
            for item in STEPS:
                try:
                    if item["op"] == "set":
                        outcome: dict[str, object] = {"ok": plain(owner.set_admin_full_audit(item["value"]))}
                    elif item["op"] == "record":
                        outcome = {"ok": owner.record_admin_full_audit(item["value"])}
                    else:
                        outcome = {"ok": owner.admin_full_audit_enabled()}
                except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
                    outcome = {"error": type(error).__name__, "message": str(error)}
                records.append({**item, "outcome": outcome,
                                "enabled": owner._admin_full_audit_enabled,  # noqa: SLF001
                                "traces": plain(owner._inspector_trace),  # noqa: SLF001
                                "pending": plain(owner._pending_audit_usage)})  # noqa: SLF001
    record = {"python": sys.version.split()[0], "steps": records}
    OUTPUT.write_bytes((json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
