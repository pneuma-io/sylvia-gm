"""The owner's safe mechanics diagnostics: what Python's `AuthorityOwner` keeps and reports.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-store/tests/vectors/generate_mechanics_diagnostics_vectors.py

On a fresh authority, Python's owner runs `record_mechanics_diagnostic` (one diagnostic with Full
Audit off, one with it on, then enough to pass the 200 it keeps) between `set_admin_full_audit`
changes and Full Audit traces, and reads `admin_full_audit_status()` along the way. Each record
step keeps the payload Python built (the turn's caller builds it the same way in Rust); each
status step keeps the status. It writes `mechanics_diagnostics_vectors.json`;
`tests/mechanics_diagnostics.rs` replays the steps on the Rust state. Reference tooling only.
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

OUTPUT = HERE / "mechanics_diagnostics_vectors.json"
BASE = HERE / "packages" / "seed_fresh.sqlite3.gz"


def plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [plain(item) for item in value]
    return value


def diagnostic(index: int) -> dict[str, object]:
    return {
        "session_id": None if index % 3 == 0 else f"session-{index % 4}",
        "turn_id": f"turn-{index}",
        "action_id": f"action-{index}",
        "tool_name": ("update_campaign_file", "d20_test", "advance")[index % 3],
        "rejection_code": ("invalid_arguments", "mechanics_validation_failed")[index % 2],
        "validation_field_paths": tuple(f"field_{item}" for item in range(index % 3)),
        "state_changed": index % 5 == 0,
    }


def main() -> None:
    steps: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        database = Path(scratch) / "authority.sqlite3"
        database.write_bytes(gzip.decompress(BASE.read_bytes()))
        with AuthorityOwner.open(database, "mechanics-diagnostics") as owner:

            def status() -> None:
                steps.append({"op": "status", "status": plain(owner.admin_full_audit_status())})

            def record(index: int) -> None:
                owner.record_mechanics_diagnostic(**diagnostic(index))
                kept = owner._mechanics_diagnostics[-1]  # noqa: SLF001
                steps.append({"op": "record", "diagnostic": plain(kept)})

            def audit(enabled: bool) -> None:
                owner.set_admin_full_audit(enabled)
                steps.append({"op": "set", "value": enabled})

            def trace(exchange: str) -> None:
                owner.record_admin_full_audit({"kind": "provider_request", "exchange_id": exchange})
                steps.append({"op": "trace", "exchange_id": exchange})

            status()
            record(0)
            status()
            audit(True)
            trace("x-1")
            record(1)
            status()
            audit(False)
            status()
            for index in range(2, 205):
                record(index)
            status()
            audit(True)
            trace("x-2")
            record(205)
            status()
    record_out = {"python": sys.version.split()[0], "steps": steps}
    OUTPUT.write_bytes((json.dumps(record_out, separators=(",", ":"), ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
