"""The owner's shutdown through Python's owner: `AuthorityOwner.close()`.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -B crates/sylvia-app/tests/vectors/generate_owner_close_runs.py

The base (`owner_close/base.sqlite3.gz`) is `tests/test_player_actions.py`'s campaign. One owner,
with a five second disconnect grace, completes one narration, starts a second and streams prose
into it, then closes: the live attempt is interrupted (its interruption saved, its prose
discarded) and cancelled once. A second close returns at once, and every later operation is
refused with Python's `RuntimeError`. Each step records its answer or failure and the attempts'
cancel counts; `final.sqlite3.gz` is the database after the close. `uuid.uuid4` is a counter so
the record is reproducible; `tests/owner_close.rs` compares interruption identities by order of
appearance. Reference tooling only.
"""

from __future__ import annotations

import gzip
import json
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[3]
sys.path.insert(0, str(REPOSITORY / "src"))
sys.path.insert(0, str(REPOSITORY))

_counter = iter(range(1, 1 << 30))
uuid.uuid4 = lambda: uuid.UUID(int=next(_counter), version=4)  # noqa: E731

from sylvia_gm.application.authority import AuthorityOwner  # noqa: E402
from sylvia_gm.domain.authority import ProseChannel  # noqa: E402
from tests.test_player_actions import _create  # noqa: E402

OUT = HERE / "owner_close"
OWNER = "owner"
CAMPAIGN = "campaign"

STEPS: list[dict[str, object]] = [
    {"op": "start", "attempt": "done"},
    {"op": "emit", "attempt": "done", "token": "The door opens."},
    {"op": "complete", "attempt": "done"},
    {"op": "start", "attempt": "live"},
    {"op": "emit", "attempt": "live", "token": "Half a sent"},
    {"op": "close"},
    {"op": "close"},
    {"op": "status", "attempt": "live"},
    {"op": "start", "attempt": "after"},
    {"op": "current_open_turn"},
]


def checkpoint(database: Path) -> None:
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()


def main() -> None:
    OUT.mkdir(exist_ok=True)
    cancels: dict[str, list[int]] = {}
    runs = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        database = Path(scratch) / "authority.sqlite3"
        with AuthorityOwner.open(database, OWNER) as owner:
            _create(owner)
        checkpoint(database)
        (OUT / "base.sqlite3.gz").write_bytes(gzip.compress(database.read_bytes(), mtime=0))
        owner = AuthorityOwner.open(database, OWNER, disconnect_grace_seconds=5.0)
        for step in STEPS:
            op = step["op"]
            try:
                if op == "start":
                    count = cancels.setdefault(str(step["attempt"]), [0])
                    value: object = owner.start_narration_attempt(
                        campaign_id=CAMPAIGN, attempt_id=str(step["attempt"]), turn_id="turn",
                        cancel=lambda count=count: count.__setitem__(0, count[0] + 1))
                elif op == "emit":
                    value = owner.emit_narration_token(CAMPAIGN, str(step["attempt"]), ProseChannel.TALK,
                                                       str(step["token"]))
                elif op == "complete":
                    value = len(owner.complete_narration_attempt(CAMPAIGN, str(step["attempt"])))
                elif op == "close":
                    value = owner.close()
                elif op == "status":
                    value = owner.narration_attempt_status(CAMPAIGN, str(step["attempt"]))
                elif op == "current_open_turn":
                    value = owner.current_open_turn(CAMPAIGN)
                else:
                    raise AssertionError(op)
                outcome: dict[str, object] = {"ok": value}
            except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
                outcome = {"error": type(error).__name__, "code": getattr(error, "code", None), "message": str(error)}
            runs.append({"step": step, "outcome": outcome,
                         "cancels": {key: count[0] for key, count in sorted(cancels.items())}})
        checkpoint(database)
        (OUT / "final.sqlite3.gz").write_bytes(gzip.compress(database.read_bytes(), mtime=0))
    record = {"python": sys.version.split()[0], "owner_id": OWNER, "campaign_id": CAMPAIGN, "runs": runs}
    (OUT / "owner_close_runs.json").write_bytes((json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
