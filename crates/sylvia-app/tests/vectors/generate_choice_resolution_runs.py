"""Mechanics choices through Python's owner: `execute_action` asking for a choice, and
`resolve_mechanics_choice` answering it.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -B crates/sylvia-app/tests/vectors/generate_choice_resolution_runs.py

The base (`choice_resolution/base.sqlite3.gz`) is made here by Python's owner from two copies of
the F3 clockwork package in `tests/test_alpha_f3_package_execution.py`:

- `campaign-one` runs `advance` with the F3 choice runtime: a seeded die, then one choice
  (`signal-response`: echo or ground), then a branch on the answer;
- `campaign-two` runs a copy whose `advance` asks a second choice (`signal-strength`) after the
  first, so an answer is saved and the next choice awaited before the commit.

Then a sequence of steps: an action awaiting its choice, its replays (the same key, a new key),
another action while one is pending, the choice's refusals (an invalid identity, another owner, a
stale revision, a sequence, choice or option that does not match), the answer committing, its
replay and a conflicting replay under the same resolution, a resolution with nothing pending,
a second transport key seeded on the reservation (as step 4's direct actions record one) and the
transport keys after the commit, another full action answered the other way, and the two-choice campaign answered in two steps
with each replay. Each step records its answer (a receipt or an awaiting-choice receipt) or the
failure's class, code and message. The run keeps its final database
(`choice_resolution/final.sqlite3.gz`). It writes `choice_resolution/choice_resolution_runs.json`;
`tests/choice_resolution.rs` replays every step on the Rust owner and compares the final
database. Reference tooling only.
"""

from __future__ import annotations

import dataclasses
import enum
import gzip
import io
import json
import sqlite3
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[3]
sys.path.insert(0, str(REPOSITORY / "src"))
sys.path.insert(0, str(REPOSITORY))

from sylvia_gm.application.authority import (  # noqa: E402
    ActionCommand,
    AuthorityOwner,
    CampaignCreate,
    ResolveMechanicsChoiceCommand,
)
from sylvia_gm.domain.authority import StateValue, WriterRole  # noqa: E402
from tests.test_alpha_f3_package_execution import _archive, _choice_runtime_yaml  # noqa: E402

OUT = HERE / "choice_resolution"
OWNER = "choice-runs"
PACKAGE = "synthetic.clockwork"


def two_choice_runtime() -> bytes:
    raw = yaml.safe_load(_choice_runtime_yaml())
    effects = raw["tools"]["advance"]["effects"]
    effects.insert(2, {
        "request_choice": {
            "choice_id": "signal-strength",
            "prompt": "Choose the signal's strength.",
            "options": [{"id": "faint", "label": "Faint"}, {"id": "strong", "label": "Strong"}],
        },
        "store_as": "_strength",
    })
    return yaml.safe_dump(raw, sort_keys=False).encode("utf-8")


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


def seed(database: Path) -> None:
    with AuthorityOwner.open(database, OWNER) as owner:
        owner.prepare_new_campaign_storage()
        for campaign_id, runtime, install in (("campaign-one", _choice_runtime_yaml(), "install-one"),
                                              ("campaign-two", two_choice_runtime(), "install-two")):
            candidate = owner.install_package_archive(io.BytesIO(_archive(runtime_bytes=runtime)),
                                                      install_id=install)
            owner.create_campaign(CampaignCreate(
                campaign_id=campaign_id, display_name=campaign_id, writer=WriterRole.DM,
                package_lock=candidate.package_lock, turn_id=f"turn-{campaign_id}",
                initial_state=(StateValue(PACKAGE, "state", {"counter": 0, "bonus": 0}),)))
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()


def main() -> None:
    OUT.mkdir(exist_ok=True)
    runs: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        database = Path(scratch) / "authority.sqlite3"
        seed(database)
        (OUT / "base.sqlite3.gz").write_bytes(gzip.compress(database.read_bytes(), mtime=0))
        with AuthorityOwner.open(database, OWNER) as owner:

            def record(name: str, kind: str, command: dict[str, object], call) -> None:  # noqa: ANN001
                try:
                    outcome: dict[str, object] = {"ok": plain(call())}
                except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
                    outcome = {"error": type(error).__name__, "code": getattr(error, "code", None),
                               "message": str(error)}
                runs.append({"name": name, "kind": kind, "command": command, "outcome": outcome})

            def act(name: str, campaign: str, action: str, key: str, *, steps: int = 2,
                    revision: int | None = None) -> None:
                command = {"campaign_id": campaign, "action_id": action, "idempotency_key": key,
                           "owner_id": OWNER, "writer": "dm",
                           "expected_revision": owner.snapshot(campaign).revision if revision is None else revision,
                           "turn_id": f"turn-{campaign}", "package_id": PACKAGE, "tool_name": "advance",
                           "parameters": {"steps": steps}}
                record(name, "act", command, lambda: owner.execute_action(
                    ActionCommand(**{**command, "writer": WriterRole.DM})))

            def resolve(name: str, campaign: str, action: str, resolution: str, choice: str, option: str, *,
                        sequence: int = 0, owner_id: str = OWNER, revision: int | None = None) -> None:
                command = {"campaign_id": campaign, "action_id": action, "resolution_id": resolution,
                           "owner_id": owner_id,
                           "expected_revision": owner.snapshot(campaign).revision if revision is None else revision,
                           "choice_id": choice, "option_id": option, "session_id": None,
                           "choice_sequence": sequence}
                record(name, "resolve", command, lambda: owner.resolve_mechanics_choice(
                    ResolveMechanicsChoiceCommand(**command)))

            def seed_alias(name: str, campaign: str, action: str, key: str) -> None:
                # A second transport key on the reservation, as a player action's direct-action
                # work records one (step 4); the commit uses the first in order and keeps the rest.
                statement = ("INSERT INTO pending_mechanics_action_aliases SELECT campaign_id, ?, action_id, "
                             "payload_hash FROM pending_mechanics_action_aliases WHERE campaign_id = ? "
                             "AND action_id = ?")
                parameters = [key, campaign, action]
                connection = sqlite3.connect(database)
                with connection:
                    connection.execute(statement, parameters)
                connection.close()
                runs.append({"name": name, "kind": "sql", "command": {"statement": statement,
                                                                       "parameters": parameters},
                             "outcome": None})

            one, two = "campaign-one", "campaign-two"
            act("an action awaiting its choice", one, "action-1", "key-1")
            act("its replay with its key", one, "action-1", "key-1")
            act("its replay with a new key", one, "action-1", "key-1b")
            act("another action while one is pending", one, "action-2", "key-2")
            resolve("an invalid resolution identity", one, "action-1", " bad", "signal-response", "echo")
            resolve("another owner", one, "action-1", "res-1", "signal-response", "echo", owner_id="someone")
            resolve("a stale revision", one, "action-1", "res-1", "signal-response", "echo", revision=5)
            resolve("a sequence that does not match", one, "action-1", "res-1", "signal-response", "echo", sequence=1)
            resolve("a choice that does not match", one, "action-1", "res-1", "signal-other", "echo")
            resolve("an option that is not offered", one, "action-1", "res-1", "signal-response", "loud")
            resolve("an action that is not pending", one, "action-9", "res-1", "signal-response", "echo")
            seed_alias("a second transport key", one, "action-1", "key-0")
            resolve("the answer committing", one, "action-1", "res-1", "signal-response", "echo")
            resolve("its replay", one, "action-1", "res-1", "signal-response", "echo")
            resolve("a conflicting replay", one, "action-1", "res-1", "signal-response", "ground")
            resolve("nothing pending", one, "action-1", "res-2", "signal-response", "echo")
            act("the transport key after the commit", one, "action-1", "key-1b")
            act("the first transport key after the commit", one, "action-1", "key-0", revision=0)
            act("a second action", one, "action-3", "key-3", steps=4)
            resolve("answered the other way", one, "action-3", "res-3", "signal-response", "ground")
            act("a two-choice action", two, "action-1", "key-1", steps=3)
            resolve("its first answer", two, "action-1", "res-a", "signal-response", "echo")
            resolve("the first answer replayed", two, "action-1", "res-a", "signal-response", "echo")
            resolve("the first answer at the wrong sequence", two, "action-1", "res-b", "signal-response", "echo")
            resolve("its second answer", two, "action-1", "res-b", "signal-strength", "strong", sequence=1)
            resolve("the second answer replayed", two, "action-1", "res-b", "signal-strength", "strong", sequence=1)
            resolve("the first answer after the commit", two, "action-1", "res-a", "signal-response", "echo")
        connection = sqlite3.connect(database)
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.close()
        (OUT / "final.sqlite3.gz").write_bytes(gzip.compress(database.read_bytes(), mtime=0))
    record_out = {"python": sys.version.split()[0], "owner_id": OWNER, "runs": runs}
    (OUT / "choice_resolution_runs.json").write_bytes(
        (json.dumps(record_out, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
