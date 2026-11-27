"""The GM's Campaign File through Python's owner: `AuthorityOwner.update_campaign_file`.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -B crates/sylvia-app/tests/vectors/generate_campaign_file_runs.py

Two runs, each recording every step's answer (the payload, or the failure's class, code and
message, with a `MechanicsRejected`'s details) and its final database:

- `campaign_file/campaign_file_runs.json` on a small committed base
  (`campaign_file/base.sqlite3.gz`, kept with `final.sqlite3.gz`): a rule-less campaign without a
  Plan, and one whose accepted Plan is the Plan format's example with two legacy secrets, each with
  an active DM session. The steps: a scene, the same scene again (unchanged), its exact replay,
  memory and entities together, every refusal of the batch (no updates, repeated or unknown
  provenance, an unavailable act, a submitted internal operation, an unknown operation, one record
  changed twice, character bookkeeping without a Ruleset, a TURN that is not open, a stored message
  `owned_json_value` refuses, written through SQL and restored), the fallback
  inventory (holdings, a transfer, its replay, a conflicting replay, a stale revision), and on the
  Plan campaign a secret revealed, the same reveal again, a protected fact refused and then
  admitted once revealed, Plan Progress, an ending claimed too early and a scene with its act.
- `campaign_file/campaign_file_srd_runs.json` on a base built at test time (`installed_base.py`):
  the bundled SRD Lite package, one Fighter and a DM session. The steps: a character update with
  a note (the bookkeeping action and the note in one transaction), its replay, a field the schema
  refuses, a stale character revision, the fallback inventory refused, and a narrative entity
  writing an editable field.

`tests/campaign_file.rs` replays both. Reference tooling only.
"""

from __future__ import annotations

import copy
import dataclasses
import enum
import gzip
import io
import json
import shutil
import sqlite3
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[3]
sys.path.insert(0, str(REPOSITORY / "src"))
sys.path.insert(0, str(REPOSITORY))
sys.path.insert(0, str(HERE))

import installed_base  # noqa: E402
from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate  # noqa: E402
from sylvia_gm.application.bundled_packages import bundled_srd_archive  # noqa: E402
from sylvia_gm.domain._content_schema_plan import EXAMPLE  # noqa: E402
from sylvia_gm.domain.authority import PackageLock, StateValue, WriterRole, owned_payload  # noqa: E402
from sylvia_gm.domain.errors import MechanicsRejected  # noqa: E402
from sylvia_gm.domain.session import SessionMessageKind  # noqa: E402
from tests.test_alpha_f4_generic_sessions import _prepare_handoff  # noqa: E402
from tests.test_srd_second_wind import PACKAGE_ID, _installed_fighter  # noqa: E402

OUT = HERE / "campaign_file"
OWNER = "campaign-file-runs"


def plain(value: object) -> object:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {field.name: plain(getattr(value, field.name)) for field in dataclasses.fields(value)}
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


class Runs:
    def __init__(self, owner: AuthorityOwner, database: Path) -> None:
        self.owner = owner
        self.database = database
        self.runs: list[dict[str, object]] = []

    def update(self, name: str, session: str, update_id: str, updates: list[dict[str, object]],
               basis: list[str], turn_id: str | None = None) -> None:
        command = {"session_id": session, "update_id": update_id, "updates": updates,
                   "basis_message_ids": basis, "turn_id": turn_id}
        try:
            outcome: dict[str, object] = {"ok": plain(self.owner.update_campaign_file(
                session_id=session, update_id=update_id, updates=copy.deepcopy(updates),
                basis_message_ids=tuple(basis), turn_id=turn_id))}
        except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
            outcome = failure(error)
        self.runs.append({"name": name, "kind": "update", "command": command, "outcome": outcome})

    def sql(self, name: str, statement: str, parameters: list[str]) -> None:
        connection = sqlite3.connect(self.database)
        with connection:
            connection.execute(statement, parameters)
        connection.close()
        self.runs.append({"name": name, "kind": "sql",
                          "command": {"statement": statement, "parameters": parameters}, "outcome": None})


def holding(**changes: object) -> dict[str, object]:
    return {"holding_id": "arrows-store", "item_id": "ordinary-arrow", "name": "Arrow", "kind": "item",
            "stackable": True, "quantity": 5, "owner_id": "waystation", "holder_id": "chest",
            "visibility": "player_known", "known_by": ["player"], **changes}


def entity(identity: str, kind: str = "person") -> dict[str, object]:
    return {"operation": "record_entity", "entity_id": identity, "entity_kind": kind,
            "name": identity.title(), "attributes": {}, "visibility": "player_known", "known_by": ["player"]}


def scene(summary: str, **extra: object) -> dict[str, object]:
    return {"operation": "update_scene", "summary": summary, **extra}


def memory(fact_id: str, text: str, visibility: str = "player_known") -> dict[str, object]:
    return {"operation": "record_memory", "fact_id": fact_id, "category": "continuity", "text": text,
            "visibility": visibility, "known_by": ["player"]}


SECRETS = ["The beacon was rewired by the duty operator.", "The station keeps a second recorder."]


def seed_plain(database: Path) -> dict[str, str]:
    with AuthorityOwner.open(database, OWNER) as owner:
        owner.create_builder_chat(provider_id="test", model_id="test", session_id="init")
        owner.create_campaign(CampaignCreate(display_name="Synthetic planless", writer=WriterRole.DM,
                                             package_lock=PackageLock(entries=()), turn_id="turn-planless",
                                             campaign_id="planless"))
        owner.create_agent_session(campaign_id="planless", role=WriterRole.DM, provider_id="scripted",
                                   model_id="scripted", session_id="session-planless")
        for message, text in (("m1", "I wait beneath the nearest glass tree."), ("m2", "I ask for arrows.")):
            owner.append_agent_message(session_id="session-planless", message_id=message,
                                       kind=SessionMessageKind.USER, content={"text": text})
        plan = copy.deepcopy(EXAMPLE)
        plan.pop("bindings")
        plan["secrets"] = list(SECRETS)
        request = _prepare_handoff(owner, suffix="planned", plan_content={"campaign_plan": plan})
        campaign = str(request["campaign_id"])
        owner.complete_turn(campaign, str(request["completed_turn_id"]))
        owner.handoff_builder_to_dm(**request)
        if owner.current_open_turn(campaign) is None:
            owner.open_turn(campaign, "turn-planned-play")
        session = str(request["dm_session_id"])
        owner.append_agent_message(session_id=session, message_id="p1", kind=SessionMessageKind.USER,
                                   content={"text": "I compare the recorder's timestamps."})
        turn = owner.current_open_turn(campaign)
    installed_base.checkpoint(database)
    return {"campaign": campaign, "session": session, "turn": turn}


def run_plain(database: Path, planned: dict[str, str]) -> list[dict[str, object]]:
    with AuthorityOwner.open(database, OWNER) as owner:
        runs = Runs(owner, database)
        s, b = "session-planless", ["m1"]
        runs.update("a scene", s, "scene-1", [scene("Rain falls in the glass orchard.", location_id="glass-orchard")], b)
        runs.update("the same scene again", s, "scene-2", [scene("Rain falls in the glass orchard.", location_id="glass-orchard")], b)
        runs.update("its exact replay", s, "scene-1", [scene("Rain falls in the glass orchard.", location_id="glass-orchard")], b)
        runs.update("memory and entities together", s, "notes-1", [
            memory("orchard-oath", "The keeper promised shelter until dawn."),
            entity("mira"), entity("waystation", "place"), entity("chest", "object")], b)
        runs.update("no updates", s, "empty-1", [], b)
        runs.update("repeated provenance", s, "repeat-1", [scene("Still raining.")], ["m1", "m1"])
        runs.update("unknown provenance", s, "unknown-1", [scene("Still raining.")], ["m9"])
        runs.update("an unavailable act", s, "act-1", [scene("A phantom act.", act_id="act-1")], b)
        runs.update("a submitted internal operation", s, "internal-1",
                    [{"operation": "prepared_inventory", "payload": {}}], b)
        runs.update("an unknown operation", s, "unknown-op-1", [{"operation": "rewrite_history"}], b)
        runs.update("one record changed twice", s, "twice-1", [scene("First."), scene("Second.")], b)
        runs.update("character bookkeeping without a Ruleset", s, "character-1", [
            {"operation": "update_character", "participant_id": "mira", "expected_revision": 0, "fields": {}}], b)
        runs.update("a TURN that is not open", s, "closed-turn-1", [scene("Elsewhere.")], b, turn_id="turn-missing")
        # `sqlite_session._load_value`: a stored message `owned_json_value` refuses.
        content = "UPDATE agent_session_messages SET content_json = ? WHERE session_id = ? AND message_id = ?"
        original = sqlite3.connect(database).execute(
            "SELECT content_json FROM agent_session_messages WHERE session_id = ? AND message_id = ?",
            [s, "m2"]).fetchone()[0]
        runs.sql("a stored message with NaN", content, ['{"text":NaN}', s, "m2"])
        runs.update("its session read", s, "nan-1", [scene("Unreadable.")], b)
        runs.sql("the stored message restored", content, [original, s, "m2"])
        revision = owner.snapshot("planless").revision
        runs.update("the fallback inventory", s, "inventory-1", [
            {"operation": "update_inventory", "expected_revision": revision, "holdings": [holding()]}], ["m2"])
        seeded = owner.snapshot("planless").revision
        transfer = {"operation": "update_inventory", "expected_revision": seeded, "transfers": [
            {"source_id": "arrows-store", "destination_id": "arrows-mira", "quantity": 2, "holder_id": "mira"}]}
        runs.update("a transfer", s, "inventory-2", [transfer, memory("borrowed-arrows", "Mira borrowed two arrows.")], ["m2"])
        runs.update("the transfer's replay", s, "inventory-2", [transfer, memory("borrowed-arrows", "Mira borrowed two arrows.")], ["m2"])
        runs.update("a conflicting replay", s, "inventory-2", [transfer, memory("borrowed-arrows", "Mira borrowed three arrows.")], ["m2"])
        runs.update("a stale inventory revision", s, "inventory-3", [
            {"operation": "update_inventory", "expected_revision": seeded, "holdings": [holding(quantity=9)]}], ["m2"])
        p, pb, turn = planned["session"], ["p1"], planned["turn"]
        runs.update("a protected fact before its reveal", p, "fact-1", [memory("rewired", SECRETS[0])], pb)
        runs.update("a secret revealed", p, "reveal-1", [
            {"operation": "reveal_secret", "secret_id": "legacy-secret-0", "audience": ["player"]}], pb)
        runs.update("the same reveal again", p, "reveal-2", [
            {"operation": "reveal_secret", "secret_id": "legacy-secret-0", "audience": ["player"]}], pb)
        runs.update("the protected fact once revealed", p, "fact-2", [memory("rewired", SECRETS[0])], pb)
        runs.update("an unrevealed secret's fact kept private", p, "fact-3",
                    [memory("second-recorder", SECRETS[1], visibility="gm_private")], pb)
        runs.update("Plan Progress", p, "progress-1", [
            {"operation": "update_plan_progress", "plan_item_id": "understand-signal", "progress_status": "engaged",
             "progress_summary": "The recorder comparison has begun."}], pb, turn_id=turn)
        runs.update("an ending claimed too early", p, "progress-2", [
            {"operation": "update_plan_progress", "plan_item_id": "ending:signal-settled", "progress_status": "resolved",
             "progress_summary": "Claimed early."}], pb)
        runs.update("a scene with its act", p, "scene-act-1", [
            scene("The reply repeats with one changed word.", act_id="act-1", location_id="relay-room")], pb)
        return runs.runs


def seed_srd(database: Path) -> None:
    with AuthorityOwner.open(database, OWNER) as owner:
        installed = owner.package_candidates()[0]
        fighter = _installed_fighter(installed.package_lock, fighter=True)
        owner.create_campaign(CampaignCreate(
            campaign_id="bookkeeping", display_name="Synthetic bookkeeping", writer=WriterRole.DM,
            package_lock=installed.package_lock, turn_id="bookkeeping-turn", participants=(fighter,),
            initial_state=(
                StateValue(PACKAGE_ID, "combat", {"encounter_active": False}),
                StateValue(PACKAGE_ID, "entities", owned_payload({"hero": {
                    "hp": 10, "max_hp": 10, "inventory": ["chain_mail", "greatsword"], "name": "Hero"}})),
            )))
        owner.create_agent_session(campaign_id="bookkeeping", role=WriterRole.DM, provider_id="scripted",
                                   model_id="scripted", session_id="session-bookkeeping")
        owner.append_agent_message(session_id="session-bookkeeping", message_id="borrow",
                                   kind=SessionMessageKind.USER,
                                   content={"text": "I accept the quartermaster's loan of a tent."})
    installed_base.checkpoint(database)


def run_srd(database: Path) -> list[dict[str, object]]:
    with AuthorityOwner.open(database, OWNER) as owner:
        runs = Runs(owner, database)
        s, b = "session-bookkeeping", ["borrow"]
        revision = owner.snapshot("bookkeeping").revision
        loan = ["chain_mail", "greatsword", "two_person_tent"]

        def character(fields: object, at: int = revision, participant: str = "hero") -> dict[str, object]:
            return {"operation": "update_character", "participant_id": participant, "expected_revision": at,
                    "fields": fields}

        note = memory("tent-loan", "The tent must be returned tomorrow.")
        runs.update("a field the schema refuses", s, "bad-field-1", [character({"hp": 900}), note], b)
        runs.update("a stale character revision", s, "stale-1", [character({"inventory": loan}, at=revision + 5), note], b)
        runs.update("the fallback inventory refused", s, "fallback-1", [
            {"operation": "update_inventory", "expected_revision": revision, "holdings": [holding()]}], b)
        runs.update("a narrative entity writing an editable field", s, "disguised-1", [
            {**entity("hero"), "attributes": {"inventory": loan}}], b)
        runs.update("a character update with a note", s, "loan-1", [character({"inventory": loan}), note], b)
        runs.update("its replay", s, "loan-1", [character({"inventory": loan}), note], b)
        return runs.runs


def write(path: Path, record: dict[str, object]) -> None:
    path.write_bytes((json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


def main() -> None:
    OUT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        database = Path(scratch) / "plain.sqlite3"
        planned = seed_plain(database)
        (OUT / "base.sqlite3.gz").write_bytes(gzip.compress(database.read_bytes(), mtime=0))
        runs = run_plain(database, planned)
        installed_base.checkpoint(database)
        (OUT / "final.sqlite3.gz").write_bytes(gzip.compress(database.read_bytes(), mtime=0))
        write(OUT / "campaign_file_runs.json", {"python": sys.version.split()[0], "owner_id": OWNER, "runs": runs})

        srd = Path(scratch) / "srd.sqlite3"
        installed_base.seed_copy(srd)
        with AuthorityOwner.open(srd, OWNER) as owner:
            owner.install_package_archive(io.BytesIO(bundled_srd_archive()), install_id="srd")
        installed_base.checkpoint(srd)
        digests = installed_base.table_digests(srd)
        installed = Path(scratch) / "srd-installed.sqlite3"
        shutil.copyfile(srd, installed)
        seed_srd(srd)
        setup = installed_base.changed_rows(installed, srd)
        base = Path(scratch) / "srd-base.sqlite3"
        shutil.copyfile(srd, base)
        srd_runs = run_srd(srd)
        installed_base.checkpoint(srd)
        changed = installed_base.changed_rows(base, srd)
        write(OUT / "campaign_file_srd_runs.json", {
            "python": sys.version.split()[0], "owner_id": OWNER, "install_id": "srd",
            "installed_table_digests": digests, "setup_tables": setup, "runs": srd_runs,
            "final_changed_tables": changed})


if __name__ == "__main__":
    main()
