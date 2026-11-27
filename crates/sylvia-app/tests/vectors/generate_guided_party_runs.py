"""A guided binding with a saved-library Start's participants and initial state, and a guided
Interview that picks a saved Plan, against Python's `AuthorityOwner`.

Run with the snapshot's own interpreter, from the repository root, after
`generate_guided_draft_runs.py` (its new profile is the starting point):

    .venv/Scripts/python.exe -B crates/sylvia-app/tests/vectors/generate_guided_party_runs.py

It installs Riichi 0.1.1 (whose Ruleset declares a `player` participant schema) and the
ruleset generator's one-package `plain` Ruleset with Python's store, and saves a Rift sibling Plan (`save_reusable_rift_plan`) in the owner's content storage
root, the database's folder. That profile is the base (`guided/party_seed.sqlite3.gz`), the
saved Plan's files are recorded in `party_runs.json`. Then:
- a Draft on Riichi answers its Interview and is bound with participants and initial state:
  first every refusal of `_validate_campaign_inputs` and the store (repeated identities, an
  unlocked package, an undeclared kind, a schema mismatch, data the schema refuses, an adaptation
  without provenance, a quarantined participant, repeated state keys, a reserved channel, an
  ambiguous state namespace), then the binding, its exact replay and a conflicting replay;
- a Draft on `plain` bound with initial state in a namespace no package prefixes, which its one
  package owns;
- a Draft whose Interview picks the saved Plan (the plan step resolves its Interview content).
It writes `guided/party_runs.json`, which `tests/guided.rs` replays. The final database is not
committed (the saved-Plan Interview installs the bundled Rift snapshot, about 45 MB of package
rows that change with every SRD Lite release). Instead the record keeps, with
`installed_base.py`:
- `final_package_digests`: every package table's digest afterwards, which the Rust run, making
  the same install, must match exactly;
- `final_tables`: the full final rows of every other table the run changed, from which the test
  rebuilds Python's database to compare with its own under the usual normalisation. Identities are explicit and the owner's UUIDs are seeded, so a
re-run writes byte-identical files. Reference tooling only.
"""

from __future__ import annotations

import gzip
import io
import json
import random
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path[:0] = [str(ROOT / "src"), str(HERE)]

_IDENTITIES = random.Random(20261123)
uuid.uuid4 = lambda: uuid.UUID(int=_IDENTITIES.getrandbits(128), version=4)  # type: ignore[assignment]

from sylvia_gm.application.authority import AuthorityOwner  # noqa: E402
from sylvia_gm.application.bundled_packages import bundled_rift_campaign_plan  # noqa: E402
from sylvia_gm.application.reusable_plans import save_reusable_rift_plan  # noqa: E402
from sylvia_gm.domain.authority import (  # noqa: E402
    Participant,
    ParticipantValidationStatus,
    StateValue,
    thaw_json_value,
)
from sylvia_gm.domain.session import SessionMessageKind  # noqa: E402
from sylvia_gm.infrastructure import sqlite_authority as a  # noqa: E402
from sylvia_gm.infrastructure.package_archive import admit_package_archive  # noqa: E402

import generate_guided_ruleset_runs as ruleset_runs  # noqa: E402
import installed_base  # noqa: E402

OUT = HERE / "guided"
SEED = OUT / "seed.sqlite3.gz"
OUTPUTS = ("party_seed.sqlite3.gz", "party_runs.json")
RETIRED = ("party_runs.sqlite3.gz",)
RIICHI = ROOT / "rulesets/riichi-mahjong/riichi-mahjong-0.1.1.sylvia-gm-package"
READY = {"text": "Ready to Begin", "interview_step": "ready", "interview_value": {"action": "ready_to_begin"}}
CANDIDATE = "candidate-install-riichi"
PLAIN = "candidate-install-plain"


def install(database: Path) -> tuple[str, str, str, str, str]:
    store = a._open_for_application_owner(database)
    try:
        with admit_package_archive(io.BytesIO(RIICHI.read_bytes())) as admitted:
            store.stage_package_archive(admitted, "install-riichi")
            store.publish_package_archive(admitted, "install-riichi")
            primary = next(m for m in admitted.manifests if m.participant_artifacts)
            declaration = primary.participant_artifacts[0]
            identity = (primary.package_id, primary.version, primary.content_sha256, declaration.schema_id,
                        declaration.schema_sha256)
        plain = ruleset_runs.archive("plain", ruleset_runs.RULESETS["plain"])
        with admit_package_archive(io.BytesIO(plain)) as admitted:
            store.stage_package_archive(admitted, "install-plain")
            store.publish_package_archive(admitted, "install-plain")
        return identity
    finally:
        store.close()


def draft(operation_id: str, session_id: str) -> dict[str, object]:
    return {"op": "create_guided_campaign_draft", "operation_id": operation_id, "display_name": "Table Night",
            "builder_session_id": session_id, "builder_provider_id": "standin",
            "builder_model_id": "standin/standin-narrator"}


def answer(operation_id: str, campaign: str, session_id: str, step: str, revision: int,
           value: dict[str, object]) -> dict[str, object]:
    return {"op": "advance_guided_campaign_draft", "operation_id": operation_id, "campaign": campaign,
            "builder_session_id": session_id, "expected_construction_revision": revision, "current_step": step,
            "visible_answer": "An answer", "answer": value}


def operations(identity: tuple[str, str, str, str, str], sibling: str) -> list[dict[str, object]]:
    package_id, version, sha256, schema_id, schema_sha256 = identity

    def player(participant_id: str = "player-ann", **changes: object) -> dict[str, object]:
        value: dict[str, object] = {
            "participant_id": participant_id, "kind": "player", "package_id": package_id,
            "package_version": version, "data": {"name": "Ann", "control": "player"},
            "package_sha256": sha256, "schema_id": schema_id, "schema_sha256": schema_sha256,
            "validation_status": "validated", "adaptation_id": None,
        }
        value.update(changes)
        return value

    table = [package_id, "table", {"round": 1, "wind": "east"}]

    def bind(operation_id: str, participants: list[dict[str, object]], state: list[list[object]],
             campaign: str = "table", candidate: str = CANDIDATE) -> dict[str, object]:
        return {"op": "bind_guided_campaign_basis", "operation_id": operation_id, "campaign": campaign,
                "display_name": "Table Night", "turn_id": f"turn-construction-{campaign}",
                "builder_session_id": f"session-{campaign}", "candidate": candidate, "participants": participants,
                "initial_state": state}

    return [
        draft("table", "session-table"),
        answer("t-ruleset", "table", "session-table", "ruleset", 0, {"candidate_id": CANDIDATE}),
        answer("t-setting", "table", "session-table", "setting", 1, {"setting_id": None}),
        answer("t-plan", "table", "session-table", "plan", 2, {"plan_id": None}),
        answer("t-name", "table", "session-table", "name", 3, {"display_name": "Table Night"}),
        answer("t-participants", "table", "session-table", "participants", 4, {"participants": []}),
        answer("t-boundaries", "table", "session-table", "boundaries_preferences", 5, {"boundaries": []}),
        answer("t-requests", "table", "session-table", "specific_requests", 6, {"requests": []}),
        {"op": "append_agent_message", "session_id": "session-table", "message_id": "guided-ready-table",
         "content": READY},
        bind("bind-repeated", [player(), player()], [table]),
        bind("bind-unlocked", [player(package_sha256="0" * 64)], [table]),
        bind("bind-kind", [player(kind="dealer")], [table]),
        bind("bind-schema", [player(schema_id="pneuma.riichi-mahjong.player.v0")], [table]),
        bind("bind-data", [player(data={"name": "Ann"})], [table]),
        bind("bind-adaptation", [player(adaptation_id="adaptation-missing")], [table]),
        bind("bind-quarantined", [player(validation_status="quarantined", package_sha256=None, schema_id=None,
                                         schema_sha256=None)], [table]),
        bind("bind-state-repeated", [player()], [table, [package_id, "table", {"round": 2}]]),
        bind("bind-state-reserved", [player()], [[package_id, "notes", {"raw_reasoning": "hidden"}]]),
        bind("bind-state-ambiguous", [player()], [["pneuma.elsewhere", "table", {"round": 1}]]),
        bind("bind", [player(), player("player-ben", data={"name": "Ben", "control": "player"})], [table]),
        bind("bind", [player(), player("player-ben", data={"name": "Ben", "control": "player"})], [table]),
        bind("bind", [player()], [table]),
        draft("plain", "session-plain"),
        answer("p-ruleset", "plain", "session-plain", "ruleset", 0, {"candidate_id": PLAIN}),
        answer("p-setting", "plain", "session-plain", "setting", 1, {"setting_id": None}),
        answer("p-plan", "plain", "session-plain", "plan", 2, {"plan_id": None}),
        answer("p-name", "plain", "session-plain", "name", 3, {"display_name": "Table Night"}),
        answer("p-participants", "plain", "session-plain", "participants", 4, {"participants": []}),
        answer("p-boundaries", "plain", "session-plain", "boundaries_preferences", 5, {"boundaries": []}),
        answer("p-requests", "plain", "session-plain", "specific_requests", 6, {"requests": []}),
        {"op": "append_agent_message", "session_id": "session-plain", "message_id": "guided-ready-plain",
         "content": READY},
        bind("bind-plain", [], [["world", "weather", {"rain": True}]], campaign="plain", candidate=PLAIN),
        draft("saved", "session-saved"),
        answer("s-ruleset", "saved", "session-saved", "ruleset", 0, {"candidate_id": "sylvia.srd-lite.bg3"}),
        answer("s-setting", "saved", "session-saved", "setting", 1, {"setting_id": "pneuma.severed-lands"}),
        answer("s-plan", "saved", "session-saved", "plan", 2, {"plan_id": sibling}),
    ]


def draft_json(result: object) -> dict[str, object]:
    return {key: getattr(result, key) for key in ("campaign_id", "builder_session_id", "current_step",
                                                  "construction_revision", "resulting_revision", "answer_message_id")}


def participant(value: dict[str, object]) -> Participant:
    fields = dict(value)
    fields["validation_status"] = ParticipantValidationStatus(fields["validation_status"])
    return Participant(**fields)  # type: ignore[arg-type]


def run(owner: AuthorityOwner, op: dict[str, object], campaigns: dict[str, str]) -> dict[str, object]:
    campaign = campaigns.get(str(op.get("campaign")), str(op.get("campaign")))
    try:
        kind = op["op"]
        if kind == "create_guided_campaign_draft":
            result = owner.create_guided_campaign_draft(
                operation_id=str(op["operation_id"]), display_name=str(op["display_name"]),
                builder_session_id=str(op["builder_session_id"]),
                builder_provider_id=str(op["builder_provider_id"]), builder_model_id=str(op["builder_model_id"]))
            campaigns[str(op["operation_id"])] = result.campaign_id
            value: object = draft_json(result)
        elif kind == "advance_guided_campaign_draft":
            value = draft_json(owner.advance_guided_campaign_draft(
                operation_id=str(op["operation_id"]), campaign_id=campaign,
                builder_session_id=str(op["builder_session_id"]),
                expected_construction_revision=int(op["expected_construction_revision"]),  # type: ignore[arg-type]
                current_step=str(op["current_step"]), visible_answer=str(op["visible_answer"]),
                answer=op["answer"]))  # type: ignore[arg-type]
        elif kind == "append_agent_message":
            message = owner.append_agent_message(
                session_id=str(op["session_id"]), message_id=str(op["message_id"]),
                kind=SessionMessageKind.USER, content=op["content"])  # type: ignore[arg-type]
            value = {"message_id": message.message_id, "sequence": message.sequence}
        else:
            candidate = owner.package_candidate(str(op["candidate"]))
            confirmation = owner.bind_guided_campaign_basis(
                operation_id=str(op["operation_id"]), campaign_id=campaign, display_name=str(op["display_name"]),
                package_lock=candidate.package_lock, turn_id=str(op["turn_id"]),
                builder_session_id=str(op["builder_session_id"]), package_candidate_id=str(op["candidate"]),
                participants=tuple(participant(item) for item in op["participants"]),  # type: ignore[union-attr]
                initial_state=tuple(StateValue(*item) for item in op["initial_state"]))  # type: ignore[union-attr]
            value = {"campaign_id": confirmation.campaign_id, "resulting_revision": confirmation.resulting_revision}
    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
        return {"error": type(error).__name__, "code": getattr(error, "code", None), "message": str(error)}
    return {"ok": value}


def is_package_table(table: str) -> bool:
    """The package library's tables: compared by digest, not committed as rows."""
    return table == "packages" or table.startswith("package_")


def checkpoint(database: Path) -> bytes:
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()
    return database.read_bytes()


def saved_files(root: Path) -> dict[str, str]:
    return {path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
            for path in sorted(root.rglob("*")) if path.is_file() and not path.name.startswith("authority.sqlite3")}


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for name in OUTPUTS + RETIRED:
        (OUT / name).unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(dir=HERE) as scratch:
        database = Path(scratch) / "authority.sqlite3"
        database.write_bytes(gzip.decompress(SEED.read_bytes()))
        identity = install(database)
        rift = bundled_rift_campaign_plan()
        sibling = save_reusable_rift_plan(Path(scratch), operation_id="guided-party-runs", title="Saved Rift",
                                          source_payload=thaw_json_value(rift.payload),
                                          source_markdown=rift.readable_markdown).artifact_id
        files = saved_files(Path(scratch))
        seed_bytes = checkpoint(database)
        (OUT / "party_seed.sqlite3.gz").write_bytes(gzip.compress(seed_bytes, mtime=0))
        base = Path(scratch) / "base.sqlite3"
        base.write_bytes(seed_bytes)
        ops = operations(identity, sibling)
        campaigns: dict[str, str] = {}
        with AuthorityOwner.open(database, "guided-party-runs") as owner:
            outcomes = [run(owner, op, campaigns) for op in ops]
        installed_base.checkpoint(database)
        changed = installed_base.changed_rows(base, database)
        digests = installed_base.table_digests(database)
    package_digests = {table: digest for table, digest in digests.items() if is_package_table(table)}
    final_tables = {table: rows for table, rows in changed.items() if not is_package_table(table)}
    record = {"python": ".".join(sys.version.split()[0].split(".")[:2]), "sibling": sibling, "files": files,
              "operations": ops, "outcomes": outcomes, "final_package_digests": package_digests,
              "final_tables": final_tables}
    (OUT / "party_runs.json").write_bytes((json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))
    for op, outcome in zip(ops, outcomes):
        print(op["op"][:20], op.get("operation_id"), ascii(outcome)[:150])


if __name__ == "__main__":
    main()
