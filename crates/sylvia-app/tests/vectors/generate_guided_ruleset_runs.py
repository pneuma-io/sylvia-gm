"""A guided campaign on an installed Ruleset: Interview Contributions through the Interview,
the binding, construction and finalize, against Python's `AuthorityOwner`.

Run with the snapshot's own interpreter, from the repository root, after
`generate_guided_draft_runs.py` (its new profile is the starting point):

    .venv/Scripts/python.exe -B crates/sylvia-app/tests/vectors/generate_guided_ruleset_runs.py

It installs three small synthetic Rulesets with Python's store: `tone`, whose
`interview-contribution.json` asks one choice question; `plain`, with no Contribution; and
`broken`, whose Contribution names the wrong owner kind. The profile with them installed is the
base (`guided/ruleset_seed.sqlite3.gz`). Then a Draft on `tone` answers its Interview (Ruleset,
Setting, Plan, the Ruleset's Contribution with four wrong answers and a right one, name, participants,
boundaries, requests), is bound to the Ruleset's lock, begun and finalized (refused without, then accepted with, the
model guidance override); Drafts on `plain`,
`broken` and a candidate that is not installed show the Contribution refusals. It writes
`guided/ruleset_runs.json` and `guided/ruleset_runs.sqlite3.gz` (the database afterwards);
`tests/guided.rs` replays them. Identities are explicit and the owner's UUIDs are seeded, so a
re-run writes byte-identical files. Reference tooling only.

The guided generators share `guided/` and build on the draft generator's seed. Run them in order
with `generate_guided_all.py` (draft, advance, start, finalize, ruleset).
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import random
import sqlite3
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3] / "src"))

_IDENTITIES = random.Random(20261128)
uuid.uuid4 = lambda: uuid.UUID(int=_IDENTITIES.getrandbits(128), version=4)  # type: ignore[assignment]

from sylvia_gm.application.authority import AuthorityOwner  # noqa: E402
from sylvia_gm.domain.package_library import canonical_json_bytes, content_root_for_record  # noqa: E402
from sylvia_gm.domain.session import SessionMessageKind  # noqa: E402
from sylvia_gm.infrastructure import sqlite_authority as a  # noqa: E402
from sylvia_gm.infrastructure.package_archive import admit_package_archive  # noqa: E402

OUT = HERE / "guided"
SEED = OUT / "seed.sqlite3.gz"
OUTPUTS = ("ruleset_seed.sqlite3.gz", "ruleset_runs.json", "ruleset_runs.sqlite3.gz")
READY = {"text": "Ready to Begin", "interview_step": "ready", "interview_value": {"action": "ready_to_begin"}}
PLAN = "# Lantern Watch\n\nA lighthouse keeper keeps a long night.\n"


def contribution(owner_kind: str) -> bytes:
    return canonical_json_bytes({
        "format_id": "sylvia-gm-interview-contribution", "format_version": 1, "id": "synthetic-tone",
        "owner_kind": owner_kind, "title": "Tone",
        "questions": [{"adaptive": False, "condition": None, "id": "tone", "prompt": "Which tone suits the night?",
                       "required": True, "type": "choice",
                       "options": [{"id": "grim", "label": "Grim"}, {"id": "bright", "label": "Bright"}]}],
    })


RULESETS: dict[str, dict[str, bytes]] = {
    "tone": {"payload/rules.json": canonical_json_bytes({"fixture": "tone"}),
             "interview-contribution.json": contribution("ruleset")},
    "plain": {"payload/rules.json": canonical_json_bytes({"fixture": "plain"})},
    "broken": {"payload/rules.json": canonical_json_bytes({"fixture": "broken"}),
               "interview-contribution.json": contribution("campaign_plan")},
}


def member(name: str) -> zipfile.ZipInfo:
    """A fixed-metadata zip member, so the archive bytes are the same on every run."""
    info = zipfile.ZipInfo(name, date_time=(2026, 11, 22, 0, 0, 0))
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def archive(name: str, files: dict[str, bytes]) -> bytes:
    package_id = f"synthetic.guided-{name}"
    draft: dict[str, object] = {
        "id": package_id, "version": "1.0.0", "role": "primary",
        "metadata": {"label": f"Guided {name.title()} Ruleset"},
        "provenance": {"publisher": "sylvia-gm-rust-port",
                       "source_sha256": hashlib.sha256(b"".join(files.values())).hexdigest()},
        "compatibility": {"host_contract": 1, "runtime_format": 1, "requires": []},
        "minimum_model_capability_tier": "sylvia.capability.t00",
        "dependencies": [], "capabilities": [], "tools": [], "prompt_fragments": [], "ui_surfaces": [],
        "files": [{"path": path, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                  for path, data in sorted(files.items())],
    }
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as zipped:
        zipped.writestr(member("manifest.json"), canonical_json_bytes(
            {"format": 1, "packages": [{**draft, "content_sha256": content_root_for_record(draft)}]}))
        for path, data in files.items():
            zipped.writestr(member(f"packages/{package_id}/{path}"), data)
    return stream.getvalue()


def install(database: Path) -> None:
    store = a._open_for_application_owner(database)
    try:
        for name, files in RULESETS.items():
            with admit_package_archive(io.BytesIO(archive(name, files))) as admitted:
                store.stage_package_archive(admitted, f"install-{name}")
                store.publish_package_archive(admitted, f"install-{name}")
    finally:
        store.close()


def draft(operation_id: str, session_id: str) -> dict[str, object]:
    return {"op": "create_guided_campaign_draft", "operation_id": operation_id, "display_name": "Lantern Watch",
            "builder_session_id": session_id, "builder_provider_id": "standin",
            "builder_model_id": "standin/standin-narrator"}


def answer(operation_id: str, campaign: str, session_id: str, step: str, revision: int,
           value: dict[str, object]) -> dict[str, object]:
    return {"op": "advance_guided_campaign_draft", "operation_id": operation_id, "campaign": campaign,
            "builder_session_id": session_id, "expected_construction_revision": revision, "current_step": step,
            "visible_answer": "An answer", "answer": value}


TONE, PLAIN, BROKEN, GONE = "candidate-install-tone", "candidate-install-plain", "candidate-install-broken", \
    "candidate-install-gone"
OPERATIONS: list[dict[str, object]] = [
    draft("lantern", "session-lantern"),
    answer("l-ruleset", "lantern", "session-lantern", "ruleset", 0, {"candidate_id": TONE}),
    answer("l-setting", "lantern", "session-lantern", "setting", 1, {"setting_id": None}),
    answer("l-plan", "lantern", "session-lantern", "plan", 2, {"plan_id": None}),
    answer("l-tone-wrong", "lantern", "session-lantern", "ruleset_contribution", 3,
           {"contribution_id": "synthetic-tone", "answers": {"tone": "dim"}}),
    answer("l-tone-unknown", "lantern", "session-lantern", "ruleset_contribution", 3,
           {"contribution_id": "synthetic-tone", "answers": {"tone": "grim", "weather": "rain"}}),
    answer("l-tone-stale", "lantern", "session-lantern", "ruleset_contribution", 3,
           {"contribution_id": "synthetic-other", "answers": {"tone": "grim"}}),
    answer("l-tone-bare", "lantern", "session-lantern", "ruleset_contribution", 3, {"tone": "grim"}),
    answer("l-tone", "lantern", "session-lantern", "ruleset_contribution", 3,
           {"contribution_id": "synthetic-tone", "answers": {"tone": "grim"}}),
    answer("l-name", "lantern", "session-lantern", "name", 4, {"display_name": "Lantern Watch"}),
    answer("l-participants", "lantern", "session-lantern", "participants", 5, {"participants": []}),
    answer("l-boundaries", "lantern", "session-lantern", "boundaries_preferences", 6, {"boundaries": []}),
    answer("l-requests", "lantern", "session-lantern", "specific_requests", 7, {"requests": []}),
    {"op": "append_agent_message", "session_id": "session-lantern", "message_id": "guided-ready-lantern",
     "content": READY},
    {"op": "bind_guided_campaign_basis", "operation_id": "lantern-bind", "campaign": "lantern",
     "display_name": "Lantern Watch", "turn_id": "turn-construction-lantern", "builder_session_id": "session-lantern",
     "candidate": TONE},
    {"op": "begin_campaign_construction", "operation_id": "lantern-begin", "campaign": "lantern",
     "lane": "quick_start", "expected_revision": 0, "canonical_input": {}},
    {"op": "finalize_campaign_construction", "campaign": "lantern", "dm_provider_id": "standin",
     "dm_model_id": "standin/standin-narrator", "plan_artifact_text": PLAN, "structured_plan": None,
     "model_guidance_override": False},
    # The Ruleset names a minimum capability tier and the owner's registry rates no model, so the
    # guided start needs the guidance override.
    {"op": "finalize_campaign_construction", "campaign": "lantern", "dm_provider_id": "standin",
     "dm_model_id": "standin/standin-narrator", "plan_artifact_text": PLAN, "structured_plan": None,
     "model_guidance_override": True},
    # A Ruleset without a Contribution: the Plan step goes on to the name, and its Contribution
    # step is refused.
    draft("plain", "session-plain"),
    answer("p-ruleset", "plain", "session-plain", "ruleset", 0, {"candidate_id": PLAIN}),
    answer("p-setting", "plain", "session-plain", "setting", 1, {"setting_id": None}),
    answer("p-plan", "plain", "session-plain", "plan", 2, {"plan_id": None}),
    answer("p-tone", "plain", "session-plain", "ruleset_contribution", 3,
           {"contribution_id": "synthetic-tone", "answers": {"tone": "grim"}}),
    # A Contribution with the wrong owner kind, and a Ruleset candidate that is not installed.
    draft("broken", "session-broken"),
    answer("b-ruleset", "broken", "session-broken", "ruleset", 0, {"candidate_id": BROKEN}),
    answer("b-setting", "broken", "session-broken", "setting", 1, {"setting_id": None}),
    answer("b-plan", "broken", "session-broken", "plan", 2, {"plan_id": None}),
    draft("gone", "session-gone"),
    answer("g-ruleset", "gone", "session-gone", "ruleset", 0, {"candidate_id": GONE}),
    answer("g-setting", "gone", "session-gone", "setting", 1, {"setting_id": None}),
    answer("g-plan", "gone", "session-gone", "plan", 2, {"plan_id": None}),
    answer("g-setting-chosen", "gone", "session-gone", "setting", 3, {"setting_id": "severed-lands"}),
]


def draft_json(result: object) -> dict[str, object]:
    return {key: getattr(result, key) for key in ("campaign_id", "builder_session_id", "current_step",
                                                  "construction_revision", "resulting_revision", "answer_message_id")}


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
        elif kind == "bind_guided_campaign_basis":
            candidate = owner.package_candidate(str(op["candidate"]))
            confirmation = owner.bind_guided_campaign_basis(
                operation_id=str(op["operation_id"]), campaign_id=campaign, display_name=str(op["display_name"]),
                package_lock=candidate.package_lock, turn_id=str(op["turn_id"]),
                builder_session_id=str(op["builder_session_id"]), package_candidate_id=str(op["candidate"]))
            value = {"campaign_id": confirmation.campaign_id, "resulting_revision": confirmation.resulting_revision}
        elif kind == "begin_campaign_construction":
            begun = owner.begin_campaign_construction(
                operation_id=str(op["operation_id"]), campaign_id=campaign, lane=str(op["lane"]),
                expected_revision=int(op["expected_revision"]),  # type: ignore[arg-type]
                canonical_input=op["canonical_input"])  # type: ignore[arg-type]
            value = {"campaign_id": begun.campaign_id, "lane": begun.lane, "resulting_revision": begun.resulting_revision}
        else:
            completed, plan, handoff = owner.finalize_campaign_construction(
                campaign_id=campaign, dm_provider_id=str(op["dm_provider_id"]), dm_model_id=str(op["dm_model_id"]),
                plan_artifact_text=str(op["plan_artifact_text"]), structured_plan=op["structured_plan"],  # type: ignore[arg-type]
                model_guidance_override=bool(op["model_guidance_override"]))
            value = {"completion_revision": completed.resulting_revision, "plan_id": plan.plan_id,
                     "plan_payload_sha256": plan.payload_sha256, "handoff_id": handoff.handoff_id,
                     "handoff_payload_sha256": handoff.payload_sha256,
                     "handoff_revision": handoff.resulting_campaign_revision}
    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
        return {"error": type(error).__name__, "code": getattr(error, "code", None), "message": str(error)}
    return {"ok": value}


def checkpoint(database: Path) -> bytes:
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()
    return database.read_bytes()


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for name in OUTPUTS:
        (OUT / name).unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(dir=HERE) as scratch:
        database = Path(scratch) / "authority.sqlite3"
        database.write_bytes(gzip.decompress(SEED.read_bytes()))
        install(database)
        (OUT / "ruleset_seed.sqlite3.gz").write_bytes(gzip.compress(checkpoint(database), mtime=0))
        campaigns: dict[str, str] = {}
        with AuthorityOwner.open(database, "guided-ruleset-runs") as owner:
            outcomes = [run(owner, op, campaigns) for op in OPERATIONS]
        (OUT / "ruleset_runs.sqlite3.gz").write_bytes(gzip.compress(checkpoint(database), mtime=0))
    record = {"python": sys.version.split()[0], "operations": OPERATIONS, "outcomes": outcomes}
    (OUT / "ruleset_runs.json").write_bytes((json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
