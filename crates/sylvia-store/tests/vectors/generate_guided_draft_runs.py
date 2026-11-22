"""Guided Campaign Drafts: Python's `AuthorityOwner.create_guided_campaign_draft` and its outcomes.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -B crates/sylvia-store/tests/vectors/generate_guided_draft_runs.py

It drives `create_guided_campaign_draft` on a new profile: identity refusals, a first Draft,
an exact replay, replays that differ (name, Builder model, Builder session), a second Draft, and a
new operation that reuses a Draft's Builder session. It writes, under `guided/`,
`seed.sqlite3.gz` (the new profile), `draft_runs.sqlite3.gz` (the database afterwards) and
`draft_runs.json` (the operations and Python's outcome of each). `tests/guided.rs` replays them
through the Rust store handle and compares the database. Identities are explicit and the
owner's UUIDs are seeded, so a re-run writes byte-identical files. Reference tooling only.
"""

from __future__ import annotations

import gzip
import json
import random
import shutil
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))

_IDENTITIES = random.Random(20261122)
uuid.uuid4 = lambda: uuid.UUID(int=_IDENTITIES.getrandbits(128), version=4)  # type: ignore[assignment]

from sylvia_gm.application.authority import AuthorityOwner  # noqa: E402

OUT = Path(__file__).resolve().parent / "guided"
PROVIDER, MODEL = "standin", "standin/standin-narrator"


def draft(operation_id: str, display_name: str = "Storm Courier", session_id: str = "session-guided-1",
          provider_id: str = PROVIDER, model_id: str = MODEL) -> dict[str, object]:
    return {"op": "create_guided_campaign_draft", "operation_id": operation_id, "display_name": display_name,
            "builder_session_id": session_id, "builder_provider_id": provider_id, "builder_model_id": model_id}


OPERATIONS: list[dict[str, object]] = [
    # Every identity is checked before anything is read.
    draft(""), draft(" storm"), draft("storm", display_name="  "), draft("storm", display_name=" Storm"),
    draft("storm", session_id=""), draft("storm", provider_id="standin "), draft("storm", model_id="\t"),
    # A first Draft, then an exact replay.
    draft("storm"), draft("storm"),
    # Replays that differ are conflicts.
    draft("storm", display_name="Other Name"), draft("storm", model_id="standin/other"),
    draft("storm", provider_id="other"),
    # A different Builder session for the same operation finds no such session.
    draft("storm", session_id="session-guided-2"),
    # A second Draft.
    draft("harbour", display_name="Harbour Lights", session_id="session-guided-2"),
    # A new operation that reuses a Draft's Builder session.
    draft("lantern", display_name="Lantern", session_id="session-guided-1"),
    # A display name with interior spaces and non-ASCII text.
    draft("tide", display_name="Tide  of  Glass — Ünder", session_id="session-guided-3"),
]


def run(owner: AuthorityOwner, op: dict[str, object]) -> dict[str, object]:
    try:
        result = owner.create_guided_campaign_draft(
            operation_id=str(op["operation_id"]), display_name=str(op["display_name"]),
            builder_session_id=str(op["builder_session_id"]),
            builder_provider_id=str(op["builder_provider_id"]), builder_model_id=str(op["builder_model_id"]))
    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
        return {"error": type(error).__name__, "code": getattr(error, "code", None), "message": str(error)}
    return {"ok": {
        "campaign_id": result.campaign_id, "builder_session_id": result.builder_session_id,
        "current_step": result.current_step, "construction_revision": result.construction_revision,
        "resulting_revision": result.resulting_revision, "answer_message_id": result.answer_message_id,
    }}


def checkpoint(database: Path) -> bytes:
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()
    return database.read_bytes()


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    with tempfile.TemporaryDirectory(dir=OUT.parent) as scratch:
        database = Path(scratch) / "authority.sqlite3"
        with AuthorityOwner.open(database, "guided-draft-runs") as owner:
            owner.prepare_new_campaign_storage()
        (OUT / "seed.sqlite3.gz").write_bytes(gzip.compress(checkpoint(database), mtime=0))
        with AuthorityOwner.open(database, "guided-draft-runs") as owner:
            outcomes = [run(owner, op) for op in OPERATIONS]
        (OUT / "draft_runs.sqlite3.gz").write_bytes(gzip.compress(checkpoint(database), mtime=0))
    record = {"python": sys.version.split()[0], "operations": OPERATIONS, "outcomes": outcomes}
    text = json.dumps(record, indent=1, ensure_ascii=True) + "\n"
    (OUT / "draft_runs.json").write_bytes(text.encode("utf-8"))


if __name__ == "__main__":
    main()
