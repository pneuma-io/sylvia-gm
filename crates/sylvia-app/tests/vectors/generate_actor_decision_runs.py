"""An isolated participant's decision through Python's owner: `actor_decision_request` and
`actor_decision_command`, with `execute_action` running the command.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -B crates/sylvia-app/tests/vectors/generate_actor_decision_runs.py

The base (`actor_decisions/base.sqlite3.gz`) is `tests/test_isolated_participants.py`'s
`isolated_game`: its synthetic hidden-token Ruleset (`token_ruleset`) installed, a campaign whose
three GM-controlled seats and one human seat each hold a secret, a persona and one offer, and a
DM session. The steps follow that file's binding test:
- the request (the first seat's observation and offer), and a request for a TURN that is not open;
- commands refused: another seat's choice, an unknown choice, a request for another actor (stale),
  a session that does not exist, and a provider or model that is not the session's;
- the command for the offered choice, executed, and executed again (its replay);
- the next request (the counter advanced), and the old request's command refused as stale.

Each step records its answer (a request or command as its dataclass fields in order) or its
failure; `final.sqlite3.gz` is the database afterwards. Reference tooling only.
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

import yaml

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[3]
sys.path.insert(0, str(REPOSITORY / "src"))
sys.path.insert(0, str(REPOSITORY))

from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate  # noqa: E402
from sylvia_gm.application.package_materialization import materialize_package_archive  # noqa: E402
from sylvia_gm.builder._ruleset_source import compile_ruleset_source  # noqa: E402
from sylvia_gm.domain.authority import (  # noqa: E402
    Participant,
    ParticipantValidationStatus,
    StateValue,
    WriterRole,
)
from tests.test_isolated_participants import NS, token_ruleset  # noqa: E402

OUT = HERE / "actor_decisions"
OWNER = "isolated-test"
CAMPAIGN = "tokens"
SESSION = "gm-tokens"


def plain(value: object) -> object:
    """A dataclass as its fields in order, an enum as its value, a frozen payload as plain JSON."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {field.name: plain(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [plain(item) for item in value]
    return value


def setup(owner: AuthorityOwner) -> None:
    """`isolated_game` without its parameters: no housekeeping family, not blocked."""
    compiled = compile_ruleset_source(yaml.safe_dump(token_ruleset()).encode())
    with materialize_package_archive((compiled.draft,)) as artifact:
        manifest = artifact.manifest
        declaration = manifest.participant_artifacts[0]
        candidate = owner.install_package_archive(artifact.archive, install_id="tokens")
    entities = {
        seat: {
            "name": seat,
            "control": "player" if seat == "human" else "gm",
            "observation": {"secret": seat.upper() + "_HAND_SENTINEL", "public_counter": 0},
            "persona": seat.upper() + "_PERSONA_SENTINEL",
            "offers": [{"id": seat + ":0", "label": "Move a token", "operation": "advance",
                        "arguments": {"choice": seat + ":0"}}],
        }
        for seat in ("a", "b", "c", "human")
    }
    owner.create_campaign(CampaignCreate(
        campaign_id=CAMPAIGN, display_name="Hidden tokens", writer=WriterRole.DM,
        package_lock=candidate.package_lock, turn_id="turn",
        participants=(Participant(
            participant_id="human", kind="player", package_id=NS, package_version=manifest.version,
            package_sha256=manifest.content_sha256, schema_id=declaration.schema_id,
            schema_sha256=declaration.schema_sha256, validation_status=ParticipantValidationStatus.VALIDATED,
            data={"name": "Human", "control": "player"}),),
        initial_state=(
            StateValue(NS, "entities", entities),
            StateValue(NS, "state", {"actor": "a", "counter": 0, "blocked": False, "pending": False,
                                     "public": {"counter": 0}}),
        ),
    ))
    owner.create_agent_session(campaign_id=CAMPAIGN, session_id=SESSION, role=WriterRole.DM,
                               provider_id="synthetic", model_id="synthetic")


STEPS: list[dict[str, object]] = [
    {"op": "request", "turn": "turn", "keep": "first"},
    {"op": "request", "turn": "elsewhere"},
    {"op": "command", "request": "first", "choice": "b:0"},
    {"op": "command", "request": "first", "choice": "unknown"},
    {"op": "command", "request": "first", "choice": "a:0", "actor": "b"},
    {"op": "command", "request": "first", "choice": "a:0", "session": "gm-missing"},
    {"op": "command", "request": "first", "choice": "a:0", "provider": "another"},
    {"op": "command", "request": "first", "choice": "a:0", "model": "another"},
    {"op": "command", "request": "first", "choice": "a:0", "provider": "synthetic", "model": "synthetic",
     "keep": "command"},
    {"op": "execute", "command": "command"},
    {"op": "execute", "command": "command"},
    {"op": "request", "turn": "turn"},
    {"op": "command", "request": "first", "choice": "a:0"},
]


def run(owner: AuthorityOwner, step: dict[str, object], kept: dict[str, object]) -> dict[str, object]:
    op = step["op"]
    try:
        if op == "request":
            request = owner.actor_decision_request(CAMPAIGN, turn_id=str(step["turn"]))
            if "keep" in step:
                kept[str(step["keep"])] = request
            value: object = plain(request)
        elif op == "command":
            request = kept[str(step["request"])]
            if "actor" in step:
                request = dataclasses.replace(request, actor_id=step["actor"])  # type: ignore[type-var]
            command = owner.actor_decision_command(
                request, choice_id=str(step["choice"]), session_id=str(step.get("session", SESSION)),  # type: ignore[arg-type]
                owner_id=OWNER, provider_id=step.get("provider"), model_id=step.get("model"))  # type: ignore[arg-type]
            if "keep" in step:
                kept[str(step["keep"])] = command
            value = plain(command)
        elif op == "execute":
            value = plain(owner.execute_action(kept[str(step["command"])]))  # type: ignore[arg-type]
        else:
            raise AssertionError(op)
    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
        return {"error": type(error).__name__, "code": getattr(error, "code", None), "message": str(error)}
    return {"ok": value}


def checkpoint(database: Path) -> None:
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()


def main() -> None:
    OUT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        database = Path(scratch) / "authority.sqlite3"
        with AuthorityOwner.open(database, OWNER) as owner:
            setup(owner)
        checkpoint(database)
        (OUT / "base.sqlite3.gz").write_bytes(gzip.compress(database.read_bytes(), mtime=0))
        kept: dict[str, object] = {}
        with AuthorityOwner.open(database, OWNER) as owner:
            runs = [{"step": step, "outcome": run(owner, step, kept)} for step in STEPS]
        checkpoint(database)
        (OUT / "final.sqlite3.gz").write_bytes(gzip.compress(database.read_bytes(), mtime=0))
    record = {"python": sys.version.split()[0], "owner_id": OWNER, "runs": runs}
    (OUT / "actor_decision_runs.json").write_bytes((json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
