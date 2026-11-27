"""A player action's mechanics choice through Python's owner: the reservation a player's panel
move makes, then `resolve_mechanics_choice` answering it under the direct-action work.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -B crates/sylvia-app/tests/vectors/generate_player_choice_runs.py

The base (`player_choice/base.sqlite3.gz`) is `tests/test_player_actions.py`'s synthetic
coordinate control with a route choice (`_create(choice=True)`), after its panel move reserved the
choice (`_move`). Then the steps that test takes: the answer while the GM session holds campaign
work (refused: a player click never waits behind GM work), the answer without the session that
made the reservation (a conflict), the answer committing with the player action's origin, and its
replay. Each step records its answer or the failure's class, code and message; the run keeps its
final database (`player_choice/final.sqlite3.gz`). It writes `player_choice/player_choice_runs.json`;
`tests/player_choice.rs` replays every step on the Rust owner. Reference tooling only.
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
sys.path.insert(0, str(REPOSITORY))

from sylvia_gm.application.authority import AuthorityOwner, ResolveMechanicsChoiceCommand  # noqa: E402
from tests.test_player_actions import _create, _move  # noqa: E402

OUT = HERE / "player_choice"
OWNER = "owner"


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


def checkpoint(database: Path) -> None:
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()


def main() -> None:
    OUT.mkdir(exist_ok=True)
    runs: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        database = Path(scratch) / "authority.sqlite3"
        with AuthorityOwner.open(database, OWNER) as owner:
            _create(owner, choice=True)
            reservation = _move(owner)
            assert reservation.status == "awaiting_choice"
        checkpoint(database)
        (OUT / "base.sqlite3.gz").write_bytes(gzip.compress(database.read_bytes(), mtime=0))
        with AuthorityOwner.open(database, OWNER) as owner:

            def resolve(name: str, session_id: str | None, holding: str | None = None) -> None:
                command = {"campaign_id": "campaign", "action_id": "move", "resolution_id": "resolve",
                           "owner_id": OWNER, "expected_revision": 0, "choice_id": "route",
                           "option_id": "north", "session_id": session_id, "choice_sequence": 0}

                def call() -> object:
                    request = ResolveMechanicsChoiceCommand(**command)
                    if holding is None:
                        return owner.resolve_mechanics_choice(request)
                    with owner.agent_session_work(holding):
                        return owner.resolve_mechanics_choice(request)

                try:
                    outcome: dict[str, object] = {"ok": plain(call())}
                except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
                    outcome = {"error": type(error).__name__, "code": getattr(error, "code", None),
                               "message": str(error)}
                runs.append({"name": name, "command": command, "holding_session": holding, "outcome": outcome})

            resolve("the answer while the GM session holds campaign work", "gm", holding="gm")
            resolve("the answer without the reserving session", None)
            resolve("the answer committing with the player action's origin", "gm")
            resolve("its replay", "gm")
        checkpoint(database)
        (OUT / "final.sqlite3.gz").write_bytes(gzip.compress(database.read_bytes(), mtime=0))
    record = {"python": sys.version.split()[0], "owner_id": OWNER, "runs": runs}
    (OUT / "player_choice_runs.json").write_bytes(
        (json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
