"""Campaign snapshots: what Python's owner and store read for every campaign of several profiles.

Run with the snapshot's own interpreter, from the repository root, after the store generators
whose profiles are the bases (`generate_session_page_runs.py`, `generate_stored_authority_runs.py`,
`generate_campaign_index.py`, `generate_continuity_runs.py`,
`generate_package_runs.py`):

    .venv/Scripts/python.exe -B crates/sylvia-app/tests/vectors/generate_snapshot_runs.py

Each base is opened with `AuthorityOwner.open`. For every campaign it records
`AuthorityOwner.snapshot`, `AuthorityOwner.model_context_snapshot` and the store's
`display_snapshot` with seven queries (no history; events after 0 and after a later sequence;
actions after revision 0 and after the first action's own revision; one action by identity; events after 0 without continuity), and a
read of a campaign that does not exist. A value is written as plain JSON: a dataclass as an
object whose `__class__` is its class name, followed by its fields in order; an enum as its value;
a mapping as an object in its order; any other sequence (a tuple, a frozen JSON list) as a
list. A failure is its class, code and message.
It writes `snapshots/snapshot_runs.json`; `tests/snapshots.rs` replays every read on the Rust
store. Reference tooling only.
"""

from __future__ import annotations

import dataclasses
import enum
import gzip
import json
import sqlite3
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[3]
sys.path.insert(0, str(REPOSITORY / "src"))

from sylvia_gm.application.authority import AuthorityOwner  # noqa: E402

OUT = HERE / "snapshots"
STORE = REPOSITORY / "crates" / "sylvia-store" / "tests" / "vectors"
BASES = {
    "session_pages": STORE / "session_pages" / "seed.sqlite3.gz",
    "failed": STORE / "stored_authority" / "failed.sqlite3.gz",
    "cast": STORE / "stored_authority" / "cast.sqlite3.gz",
    "archived": STORE / "stored_authority" / "archived.sqlite3.gz",
    "trashed": STORE / "stored_authority" / "trashed.sqlite3.gz",
    "campaigns": STORE / "campaigns" / "seed.sqlite3.gz",
    "continuity": STORE / "continuity" / "batch_and_view.sqlite3.gz",
    # Schema 4: startup does not advance it, so participants are read without F5 identity.
    "v4": STORE / "packages" / "participants_v4.sqlite3.gz",
}


def plain(value: object) -> object:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        out: dict[str, object] = {"__class__": type(value).__name__}
        for field in dataclasses.fields(value):
            out[field.name] = plain(getattr(value, field.name))
        return out
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [plain(item) for item in value]
    return value


def failure(error: BaseException) -> dict[str, object]:
    return {"error": type(error).__name__, "code": getattr(error, "code", None), "message": str(error)}


def outcome(read) -> dict[str, object]:  # noqa: ANN001
    try:
        return {"ok": plain(read())}
    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
        return failure(error)


def queries(database: Path, campaign_id: str) -> list[dict[str, object]]:
    connection = sqlite3.connect(database)
    sequences = [row[0] for row in connection.execute(
        "SELECT sequence FROM canonical_events WHERE campaign_id = ? ORDER BY sequence", (campaign_id,))]
    actions = connection.execute(
        "SELECT action_id, resulting_revision FROM actions WHERE campaign_id = ? ORDER BY resulting_revision",
        (campaign_id,)).fetchall()
    connection.close()
    later = sequences[-2] if len(sequences) >= 2 else 0
    return [
        {},
        {"event_after": 0, "include_continuity": True},
        {"event_after": later, "include_continuity": True},
        {"actions_after_revision": 0},
        {"actions_after_revision": actions[0][1] if actions else 1},
        {"action_ids": [action for action, _ in actions[:1]]},
        {"event_after": 0},
    ]


def main() -> None:
    OUT.mkdir(exist_ok=True)
    runs = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        for base, source in BASES.items():
            database = Path(scratch) / base / "authority.sqlite3"
            database.parent.mkdir()
            database.write_bytes(gzip.decompress(source.read_bytes()))
            with AuthorityOwner.open(database, "snapshot-runs") as owner:
                store = owner._store  # noqa: SLF001 - the store's own display read
                connection = sqlite3.connect(database)
                campaigns = [row[0] for row in connection.execute(
                    "SELECT campaign_id FROM campaigns ORDER BY campaign_id")]
                connection.close()
                for campaign_id in [*campaigns, "campaign-missing"]:
                    runs.append({"base": base, "campaign_id": campaign_id, "read": "snapshot",
                                 "outcome": outcome(lambda: owner.snapshot(campaign_id))})
                    runs.append({"base": base, "campaign_id": campaign_id, "read": "model_context_snapshot",
                                 "outcome": outcome(lambda: owner.model_context_snapshot(campaign_id))})
                    for query in queries(database, campaign_id):
                        arguments = {**query, "action_ids": tuple(query.get("action_ids", ()))}
                        runs.append({"base": base, "campaign_id": campaign_id, "read": "display_snapshot",
                                     "query": query,
                                     "outcome": outcome(lambda: store.display_snapshot(campaign_id, **arguments))})
    record = {"python": sys.version.split()[0], "runs": runs}
    (OUT / "snapshot_runs.json").write_bytes((json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
