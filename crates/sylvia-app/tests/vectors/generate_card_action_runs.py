"""A player's click on a saved result card's button, through Python's owner: `player_action`
with a `result_card` source.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -B crates/sylvia-app/tests/vectors/generate_card_action_runs.py

The base (`card_action/base.sqlite3.gz`) is `tests/test_player_actions.py`'s synthetic coordinate
control, with confirmation required and an automatic response, and a saved result card
(`tests/test_result_card_actions.py`'s `CARD`), after its panel move committed the card. Then the
steps those tests take on the card: its preview, a click without confirming, replacement
parameters, a forged source, a click while the GM session holds campaign work, the confirmed
click (its public call and result placed in the session), its replay, another source under the
same operation, a preview once it resolved, and a button the card does not have. Each step
records its answer (a receipt, or the preview with its confirmation token) or the failure's
class, code and message; the run keeps its final database (`card_action/final.sqlite3.gz`). It
writes `card_action/card_action_runs.json`; `tests/card_action.rs` replays every step on the
Rust owner. Reference tooling only.
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

from sylvia_gm.application.authority import AuthorityOwner  # noqa: E402
from tests.test_player_actions import _create, _move, _request  # noqa: E402
from tests.test_result_card_actions import CARD  # noqa: E402

OUT = HERE / "card_action"
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


def card_request(**changes: object) -> dict[str, object]:
    request: dict[str, object] = {
        "campaign_id": "campaign", "session_id": "gm", "operation_id": "card-move", "expected_revision": 1,
        "source": {"kind": "result_card", "action_id": "move", "button_index": 0},
        "selection": None, "confirmation": None, "preview_only": False,
    }
    return {**request, **changes}


def main() -> None:
    OUT.mkdir(exist_ok=True)
    runs: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        database = Path(scratch) / "authority.sqlite3"
        with AuthorityOwner.open(database, OWNER) as owner:
            _create(owner, controls={"confirmation": "required", "response": "automatic"}, card=CARD)
            proposal = owner.preview_panel_action(**_request())
            _move(owner, confirmation=proposal["confirmation"])
        checkpoint(database)
        (OUT / "base.sqlite3.gz").write_bytes(gzip.compress(database.read_bytes(), mtime=0))
        with AuthorityOwner.open(database, OWNER) as owner:

            def click(name: str, request: dict[str, object], holding: str | None = None) -> object:
                def call() -> object:
                    if holding is None:
                        return owner.player_action(**request)
                    with owner.agent_session_work(holding):
                        return owner.player_action(**request)

                answer = None
                try:
                    answer = call()
                    outcome: dict[str, object] = {"ok": plain(answer)}
                except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
                    outcome = {"error": type(error).__name__, "code": getattr(error, "code", None),
                               "message": str(error)}
                runs.append({"name": name, "request": request, "holding_session": holding, "outcome": outcome})
                return answer

            preview = click("the card's preview", card_request(preview_only=True))
            token = preview["confirmation"]  # type: ignore[index]
            click("a click without confirming", card_request())
            click("replacement parameters", card_request(selection={"actor": "other"}))
            click("a forged source", card_request(source={
                "kind": "result_card", "action_id": "move", "button_index": 0, "parameters": {"actor": "other"}}))
            click("a click while the GM session holds campaign work", card_request(confirmation=token), holding="gm")
            click("the confirmed click", card_request(confirmation=token))
            click("its replay", card_request(confirmation=token))
            click("another source under the same operation", card_request(source={
                "kind": "result_card", "action_id": "card-move", "button_index": 0}))
            click("a preview once it resolved", card_request(preview_only=True))
            click("a button the card does not have", card_request(
                operation_id="card-missing", expected_revision=2,
                source={"kind": "result_card", "action_id": "move", "button_index": 3}))
        checkpoint(database)
        (OUT / "final.sqlite3.gz").write_bytes(gzip.compress(database.read_bytes(), mtime=0))
    record = {"python": sys.version.split()[0], "owner_id": OWNER, "runs": runs}
    (OUT / "card_action_runs.json").write_bytes(
        (json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
