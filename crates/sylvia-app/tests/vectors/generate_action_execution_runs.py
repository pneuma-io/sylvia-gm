"""Game Master tool actions through Python's owner: the plain commit path of `execute_action`.

Run with the snapshot's own interpreter, from the repository root, after
`generate_snapshot_runs.py` (its Riichi game in play is the base):

    .venv/Scripts/python.exe -B crates/sylvia-app/tests/vectors/generate_action_execution_runs.py

On the Riichi game in play (`snapshots/riichi_started.sqlite3.gz`), Python's
`AuthorityOwner.execute_action` runs a sequence of commands on `campaign-table`, each at the
campaign's current revision unless the step says otherwise:

- a legal move committed, then replayed with its own key, replayed with a new transport key, and
  refused with a reused key or action identity for different parameters;
- refusals before the mechanic: an invalid identity, another owner, the Builder writer, a stale
  revision, an unknown TURN, an unknown package and tool, parameters outside the tool's schema;
- the Ruleset's own refusals (a move that is no longer available, a second `start_game`);
- a run of further legal moves, each the first choice the game offers;
- the Builder campaign, which admits no Game Master action.

Each step records its outcome: the receipt, or the failure's class, code and message, with a
`MechanicsRejected`'s field paths, validation issues, state change and refusal reasons. The run
keeps its final database (`action_execution/final.sqlite3.gz`). Each move's shuffles leave their
collection in order (the base's own were seeded), as lane 2's vector hook does in Rust, so every
run, and the Rust owner, draws the same choice tokens.
It writes `action_execution/action_execution_runs.json`; `tests/action_execution.rs` replays
every step on the Rust owner and compares the final database. Reference tooling only.
"""

from __future__ import annotations

import dataclasses
import enum
import gzip
import json
import secrets
import sqlite3
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[3]
sys.path.insert(0, str(REPOSITORY / "src"))

from sylvia_gm.application.authority import ActionCommand, AuthorityOwner  # noqa: E402
from sylvia_gm.domain.authority import WriterRole  # noqa: E402
from sylvia_gm.domain.errors import MechanicsRejected  # noqa: E402

OUT = HERE / "action_execution"
BASE = HERE / "snapshots" / "riichi_started.sqlite3.gz"
OWNER = "action-runs"
CAMPAIGN = "campaign-table"
RIICHI = "pneuma.riichi-mahjong"


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
    out: dict[str, object] = {"error": type(error).__name__, "code": getattr(error, "code", None),
                              "message": str(error)}
    if isinstance(error, MechanicsRejected):
        out["mechanics"] = {
            "field_paths": list(error.field_paths),
            "validation_issues": [issue.as_payload() for issue in error.validation_issues],
            "state_changed": error.state_changed,
            "refusal_reasons": list(error.refusal_reasons),
        }
    return out


def choices(owner: AuthorityOwner) -> list[str]:
    state = owner.snapshot(CAMPAIGN).state.get(f"{RIICHI}.state", {})
    return [str(item) for item in state.get("choice_ids", ())] if isinstance(state, Mapping) else []


def main() -> None:
    OUT.mkdir(exist_ok=True)
    runs: list[dict[str, object]] = []
    class InOrder:
        def shuffle(self, values: list[object]) -> None:
            pass

    system_random = secrets.SystemRandom
    secrets.SystemRandom = InOrder  # type: ignore[assignment,misc]
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
            database = Path(scratch) / "authority.sqlite3"
            database.write_bytes(gzip.decompress(BASE.read_bytes()))
            with AuthorityOwner.open(database, OWNER) as owner:
                counter = 0

                def step(name: str, *, tool: str = "take_action", parameters: object = None,
                         campaign: str = CAMPAIGN, package: str = RIICHI, turn: str = "turn-table",
                         writer: WriterRole = WriterRole.DM, owner_id: str = OWNER, action_id: str | None = None,
                         key: str | None = None, revision_offset: int = 0) -> None:
                    nonlocal counter
                    counter += 1
                    if parameters is None:
                        offered = choices(owner)
                        parameters = {"choice": offered[0] if offered else "none"}
                    revision = owner.snapshot(campaign).revision + revision_offset
                    command = {
                        "campaign_id": campaign, "action_id": action_id or f"action-{counter}",
                        "idempotency_key": key or f"key-{counter}", "owner_id": owner_id,
                        "writer": writer.value, "expected_revision": revision, "turn_id": turn,
                        "package_id": package, "tool_name": tool, "parameters": parameters,
                    }
                    try:
                        result = owner.execute_action(ActionCommand(**{**command, "writer": writer}))
                        outcome: dict[str, object] = {"ok": plain(result)}
                    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
                        outcome = failure(error)
                    runs.append({"name": name, "command": command, "outcome": outcome})

                first = choices(owner)
                legal = {"choice": first[0]}
                step("legal move", parameters=legal, action_id="action-move", key="key-move")
                step("replay with its key", parameters=legal, action_id="action-move", key="key-move",
                     revision_offset=-1)
                step("replay with a new transport key", parameters=legal, action_id="action-move",
                     key="key-move-2", revision_offset=-1)
                step("reused key, other parameters", parameters={"choice": "other"}, action_id="action-other",
                     key="key-move")
                step("reused action, other parameters", parameters={"choice": "other"}, action_id="action-move",
                     key="key-move-3", revision_offset=-1)
                step("invalid identity", action_id=" spaced")
                step("another owner", owner_id="someone-else")
                step("the Builder writer", writer=WriterRole.BUILDER)
                step("stale revision", revision_offset=-1)
                step("unknown TURN", turn="turn-missing")
                step("unknown package", package="pneuma.unknown")
                step("unknown tool", tool="no_such_tool")
                step("parameters outside the schema", parameters={"choice": 7})
                step("missing parameter", parameters={})
                step("extra parameter", parameters={"choice": "x", "extra": True})
                step("a move no longer available", parameters={"choice": first[0]})
                step("a second start_game", tool="start_game", parameters={"player": "aki", "match_format": "east_only"})
                for index in range(6):
                    step(f"further legal move {index + 1}")
                step("the Builder campaign", campaign="campaign-workshop", turn="turn-workshop",
                     writer=WriterRole.BUILDER)
            connection = sqlite3.connect(database)
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.close()
            (OUT / "final.sqlite3.gz").write_bytes(gzip.compress(database.read_bytes(), mtime=0))
    finally:
        secrets.SystemRandom = system_random  # type: ignore[misc]
    record = {"python": sys.version.split()[0], "owner_id": OWNER, "runs": runs}
    (OUT / "action_execution_runs.json").write_bytes(
        (json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
