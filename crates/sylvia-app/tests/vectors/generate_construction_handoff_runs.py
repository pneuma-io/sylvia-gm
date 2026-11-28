"""Finishing a construction one operation at a time through Python's owner:
`AuthorityOwner.record_builder_evidence`, `admit_campaign_plan`, `campaign_plan`,
`handoff_builder_to_dm`, `builder_dm_handoff`, and the prepared and legacy Builder branch of
`finalize_campaign_construction`.

Run with the snapshot's own interpreter, from the repository root, after
`generate_guided_ruleset_runs.py` (its `guided/ruleset_seed.sqlite3.gz`, a profile with three
small Rulesets installed, is the starting point):

    .venv/Scripts/python.exe -B crates/sylvia-app/tests/vectors/generate_construction_handoff_runs.py

The base (`construction_handoff/base.sqlite3.gz`) holds five confirmed campaigns, so their
identities are fixed: `legacy` (No Ruleset, begun), `prepared` (the `plain` Ruleset's candidate,
a User message naming a prepared campaign, begun), `manual` (No Ruleset, begun and completed),
`skillful` (its Builder on BUILD RULESET, begun and completed) and `unbegun`. The steps:
evidence and its refusals; Plan admission's refusals, admission, its replay and a conflicting
replay; the handoff's refusals, the construction TURN completed, the handoff, its replay and a
conflicting replay; the two reads; then finalize: unbegun, a blank plan file, the legacy branch,
its replay, a changed plan file, and the prepared branch refused without, then accepted with, the
model guidance override, and the model guidance reads (with and without the override, a
missing campaign, an invalid identity). Each step records its answer or failure; `final.sqlite3.gz` is the
database afterwards. `tests/construction_handoff.rs` replays them. Identities are explicit and
the owner's UUIDs are seeded, so a re-run writes byte-identical files. Reference tooling only.
"""

from __future__ import annotations

import gzip
import json
import random
import sqlite3
import sys
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3] / "src"))

_IDENTITIES = random.Random(20261128)
uuid.uuid4 = lambda: uuid.UUID(int=_IDENTITIES.getrandbits(128), version=4)  # type: ignore[assignment]

from sylvia_gm.application.authority import AuthorityOwner  # noqa: E402
from sylvia_gm.domain.authority import PackageLock, WriterRole  # noqa: E402
from sylvia_gm.domain.package_library import canonical_json_bytes  # noqa: E402
from sylvia_gm.domain.model_capability import model_guidance_payload  # noqa: E402
from sylvia_gm.domain.session import SessionMessageKind  # noqa: E402

import hashlib  # noqa: E402

SEED = HERE / "guided" / "ruleset_seed.sqlite3.gz"
OUT = HERE / "construction_handoff"
OWNER = "construction-handoff-runs"
PLAIN = "candidate-install-plain"
MODEL = "standin/standin-narrator"
PLAN_TEXT = "# Hollow Road\n\nA road runs under a hollow hill.\n"
CONTENT = {"campaign_plan_markdown": PLAN_TEXT}
CONTENT_SHA = hashlib.sha256(canonical_json_bytes(CONTENT)).hexdigest()
EVIDENCE_SHA = "a" * 64

CAMPAIGNS: dict[str, dict[str, object]] = {
    "legacy": {"candidate": None, "skill": "BUILD CAMPAIGN PLAN", "begin": True, "complete": False},
    "prepared": {"candidate": PLAIN, "skill": "BUILD CAMPAIGN PLAN", "begin": True, "complete": False,
                 "message": {"text": "Start the prepared road.", "ready_campaign_id": "ready-hollow"}},
    "manual": {"candidate": None, "skill": "BUILD CAMPAIGN PLAN", "begin": True, "complete": True},
    "skillful": {"candidate": None, "skill": "BUILD RULESET", "begin": True, "complete": True},
    "unbegun": {"candidate": None, "skill": "BUILD CAMPAIGN PLAN", "begin": False, "complete": False},
}


def evidence(session: str, evidence_id: str, kind: str = "user_provided", sha: str = EVIDENCE_SHA,
             locator: str | None = None, excerpt: str = "A user-provided note.") -> dict[str, object]:
    return {"op": "evidence", "session": session, "evidence_id": evidence_id, "source_kind": kind,
            "content_sha256": sha, "source_locator": locator, "safe_excerpt": excerpt, "claims": ["construction-input"]}


def admit(plan_id: str = "plan-manual", campaign: str = "manual", session: str = "session-builder-manual",
          revision: int = 2, lock: str | None = None, content: dict[str, object] | None = None,
          sha: str = CONTENT_SHA, provenance: list[str] | None = None) -> dict[str, object]:
    return {"op": "admit", "plan_id": plan_id, "campaign": campaign, "session": session, "expected_revision": revision,
            "lock": lock, "content": CONTENT if content is None else content, "content_sha256": sha,
            "provenance": ["manual-evidence"] if provenance is None else provenance}


def handoff(handoff_id: str = "handoff-manual", revision: int = 3, plan_id: str = "plan-manual",
            plan_revision: int = 1, dm_session: str = "session-dm-manual", model: str = MODEL,
            turn: str = "turn-manual") -> dict[str, object]:
    return {"op": "handoff", "handoff_id": handoff_id, "campaign": "manual", "builder": "session-builder-manual",
            "dm_session": dm_session, "dm_model": model, "expected_revision": revision, "plan_id": plan_id,
            "plan_revision": plan_revision, "plan_sha": CONTENT_SHA, "turn": turn}


def finalize(campaign: str, text: str = PLAN_TEXT, override: bool = False) -> dict[str, object]:
    return {"op": "finalize", "campaign": campaign, "plan_text": text, "override": override}


STEPS: list[dict[str, object]] = [
    {"op": "plan", "plan_id": "plan-none"},
    {"op": "handoff_read", "handoff_id": "handoff-none"},
    evidence("session-nobody", "e-nobody"),
    evidence("session-builder-manual", "e-short", sha="abc"),
    evidence("session-builder-manual", "e-open", kind="open_source"),
    evidence("session-builder-manual", "e-located", locator="https://example.invalid/note"),
    evidence("session-builder-manual", "manual-evidence"),
    evidence("session-builder-manual", "manual-evidence"),
    admit(content={**CONTENT, "sequel": {"source_id": "x"}}),
    admit(plan_id=""),
    admit(revision=-1),
    admit(provenance=[]),
    admit(sha="b" * 64),
    admit(revision=1),
    admit(lock="lock-wrong"),
    admit(provenance=["missing-evidence"]),
    admit(),
    admit(),
    admit(revision=3),
    evidence("session-builder-skill", "skill-evidence"),
    admit(plan_id="plan-skill", campaign="skillful", session="session-builder-skill", provenance=["skill-evidence"]),
    handoff(handoff_id=""),
    handoff(plan_id="plan-none"),
    handoff(),
    {"op": "complete_turn", "campaign": "manual", "turn": "turn-manual"},
    handoff(revision=2),
    handoff(plan_revision=2),
    handoff(dm_session="session-builder-legacy"),
    handoff(),
    handoff(),
    handoff(model="standin/another-model"),
    {"op": "handoff_read", "handoff_id": "handoff-manual"},
    {"op": "plan", "plan_id": "plan-manual"},
    finalize("unbegun"),
    finalize("legacy", text="   "),
    finalize("legacy"),
    finalize("legacy"),
    finalize("legacy", text=PLAN_TEXT + "\nA changed line.\n"),
    finalize("prepared"),
    finalize("prepared", override=True),
    evidence("session-builder-legacy", "late-evidence"),
    {"op": "guidance", "campaign": "prepared", "model": MODEL, "override": False},
    {"op": "guidance", "campaign": "prepared", "model": MODEL, "override": True},
    {"op": "guidance", "campaign": "legacy", "model": MODEL, "override": False},
    {"op": "guidance", "campaign_id": "campaign-nowhere", "model": MODEL, "override": False},
    {"op": "guidance", "campaign_id": "  ", "model": MODEL, "override": False},
]


def ordered(value: object) -> object:
    """A frozen payload as plain JSON, keeping Python's own key order (never sorted)."""
    if isinstance(value, Mapping):
        return {str(key): ordered(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [ordered(item) for item in value]
    return value


def plan_json(plan: object) -> dict[str, object]:
    return {"plan_id": plan.plan_id, "campaign_id": plan.campaign_id, "package_lock_id": plan.package_lock_id,  # type: ignore[attr-defined]
            "source_session_id": plan.source_session_id, "content_sha256": plan.content_sha256,  # type: ignore[attr-defined]
            "content": ordered(plan.content), "provenance_evidence_ids": list(plan.provenance_evidence_ids),  # type: ignore[attr-defined]
            "plan_revision": plan.plan_revision, "resulting_campaign_revision": plan.resulting_campaign_revision,  # type: ignore[attr-defined]
            "payload_sha256": plan.payload_sha256}  # type: ignore[attr-defined]


def handoff_json(handoff: object) -> dict[str, object]:
    return {"handoff_id": handoff.handoff_id, "campaign_id": handoff.campaign_id,  # type: ignore[attr-defined]
            "builder_session_id": handoff.builder_session_id, "dm_session_id": handoff.dm_session_id,  # type: ignore[attr-defined]
            "package_lock_id": handoff.package_lock_id, "package_candidate_id": handoff.package_candidate_id,  # type: ignore[attr-defined]
            "plan_id": handoff.plan_id, "plan_revision": handoff.plan_revision,  # type: ignore[attr-defined]
            "expected_revision": handoff.expected_revision,  # type: ignore[attr-defined]
            "resulting_campaign_revision": handoff.resulting_campaign_revision,  # type: ignore[attr-defined]
            "completed_turn_id": handoff.completed_turn_id, "payload_sha256": handoff.payload_sha256}  # type: ignore[attr-defined]


def run(owner: AuthorityOwner, step: dict[str, object], campaigns: dict[str, dict[str, str]]) -> dict[str, object]:
    op = step["op"]
    try:
        if op == "plan":
            value: object = plan_json(owner.campaign_plan(str(step["plan_id"])))
        elif op == "handoff_read":
            value = handoff_json(owner.builder_dm_handoff(str(step["handoff_id"])))
        elif op == "evidence":
            recorded = owner.record_builder_evidence(
                session_id=str(step["session"]), evidence_id=str(step["evidence_id"]),
                source_kind=str(step["source_kind"]), content_sha256=str(step["content_sha256"]),
                source_locator=step["source_locator"], safe_excerpt=str(step["safe_excerpt"]),  # type: ignore[arg-type]
                claims=tuple(step["claims"]))  # type: ignore[arg-type]
            value = ordered(recorded.payload())
        elif op == "admit":
            campaign = campaigns[str(step["campaign"])]
            value = plan_json(owner.admit_campaign_plan(
                plan_id=str(step["plan_id"]), campaign_id=campaign["campaign_id"],
                builder_session_id=str(step["session"]), expected_revision=step["expected_revision"],  # type: ignore[arg-type]
                expected_package_lock_id=str(step["lock"] or campaign["lock_id"]),
                content=step["content"], content_sha256=str(step["content_sha256"]),  # type: ignore[arg-type]
                provenance_evidence_ids=tuple(step["provenance"]),  # type: ignore[arg-type]
                validation_outcome="valid", acceptance_status="accepted"))
        elif op == "handoff":
            campaign = campaigns[str(step["campaign"])]
            value = handoff_json(owner.handoff_builder_to_dm(
                handoff_id=str(step["handoff_id"]), campaign_id=campaign["campaign_id"],
                builder_session_id=str(step["builder"]), dm_session_id=str(step["dm_session"]),
                dm_provider_id="standin", dm_model_id=str(step["dm_model"]), expected_writer=WriterRole.BUILDER,
                expected_revision=step["expected_revision"], expected_package_lock_id=campaign["lock_id"],  # type: ignore[arg-type]
                expected_package_candidate_id=None, plan_id=str(step["plan_id"]),
                expected_plan_revision=step["plan_revision"],  # type: ignore[arg-type]
                expected_plan_content_sha256=str(step["plan_sha"]), completed_turn_id=str(step["turn"])))
        elif op == "guidance":
            campaign_id = str(step["campaign_id"]) if "campaign_id" in step else campaigns[str(step["campaign"])]["campaign_id"]
            guidance = owner.model_guidance(campaign_id=campaign_id, model_id=str(step["model"]),
                                            override_accepted=bool(step["override"]))
            value = {"payload": ordered(model_guidance_payload(guidance)), "may_continue": guidance.may_continue}
        elif op == "complete_turn":
            owner.complete_turn(campaigns[str(step["campaign"])]["campaign_id"], str(step["turn"]))
            value = None
        elif op == "finalize":
            completed, plan, handed = owner.finalize_campaign_construction(
                campaign_id=campaigns[str(step["campaign"])]["campaign_id"], dm_provider_id="standin",
                dm_model_id=MODEL, plan_artifact_text=str(step["plan_text"]),
                model_guidance_override=bool(step["override"]))
            value = {"completed": {"campaign_id": completed.campaign_id, "lane": completed.lane,
                                   "canonical_representation_sha256": completed.canonical_representation_sha256,
                                   "resulting_revision": completed.resulting_revision},
                     "plan": plan_json(plan), "handoff": handoff_json(handed)}
        else:
            raise AssertionError(op)
    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
        return {"error": type(error).__name__, "code": getattr(error, "code", None), "message": str(error)}
    return {"ok": value}


def setup(owner: AuthorityOwner) -> dict[str, dict[str, str]]:
    campaigns: dict[str, dict[str, str]] = {}
    for name, spec in CAMPAIGNS.items():
        candidate = spec["candidate"]
        lock = PackageLock(entries=()) if candidate is None else owner.package_candidate(str(candidate)).package_lock
        session = "session-builder-skill" if name == "skillful" else f"session-builder-{name}"
        confirmation = owner.confirm_new_campaign(
            operation_id=f"confirm-{name}", display_name=f"{name.title()} Road", package_lock=lock,
            turn_id=f"turn-{name}", package_candidate_id=candidate,  # type: ignore[arg-type]
            builder_session_id=session, builder_provider_id="standin", builder_model_id=MODEL,
            builder_selected_skill=str(spec["skill"]))
        campaign_id = confirmation.campaign_id
        if "message" in spec:
            owner.append_agent_message(session_id=session, message_id=f"{name}-choice", kind=SessionMessageKind.USER,
                                       content=spec["message"])  # type: ignore[arg-type]
        if spec["begin"]:
            owner.begin_campaign_construction(operation_id=f"begin-{name}", campaign_id=campaign_id,
                                              lane="quick_start", expected_revision=0, canonical_input={})
        if spec["complete"]:
            owner.complete_campaign_construction(operation_id=f"complete-{name}", campaign_id=campaign_id,
                                                 expected_revision=1, canonical_plan=CONTENT)
        campaigns[name] = {"campaign_id": campaign_id, "lock_id": owner.current_package_lock(campaign_id).lock_id}
    return campaigns


def checkpoint(database: Path) -> bytes:
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()
    return database.read_bytes()


def main() -> None:
    OUT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        database = Path(scratch) / "authority.sqlite3"
        database.write_bytes(gzip.decompress(SEED.read_bytes()))
        with AuthorityOwner.open(database, OWNER) as owner:
            campaigns = setup(owner)
        (OUT / "base.sqlite3.gz").write_bytes(gzip.compress(checkpoint(database), mtime=0))
        with AuthorityOwner.open(database, OWNER) as owner:
            runs = [{"step": step, "outcome": run(owner, step, campaigns)} for step in STEPS]
        (OUT / "final.sqlite3.gz").write_bytes(gzip.compress(checkpoint(database), mtime=0))
    record = {"python": sys.version.split()[0], "owner_id": OWNER, "campaigns": campaigns, "runs": runs}
    (OUT / "runs.json").write_bytes((json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
