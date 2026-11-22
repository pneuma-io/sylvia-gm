"""Finishing a guided campaign: Python's `AuthorityOwner.finalize_campaign_construction`.

Run with the snapshot's own interpreter, from the repository root, after
`generate_guided_draft_runs.py` (its new profile is the base):

    .venv/Scripts/python.exe -B crates/sylvia-store/tests/vectors/generate_guided_finalize_runs.py

It takes a No Ruleset Draft through its Interview, the Ready to Begin message, the binding and the
begin, as `handle_start_guided_campaign` does, then finalizes it: identity refusals, an empty
plan, structured plans with a wrong format and stale bindings, the guided start, its replay, a
replay with another DM model, and a campaign whose construction has not begun. It writes
`guided/finalize_runs.json` and `guided/finalize_runs.sqlite3.gz` (the database afterwards);
`tests/guided.rs` replays them. Identities are explicit and the owner's UUIDs are seeded, so a
re-run writes byte-identical files. Reference tooling only.
"""

from __future__ import annotations

import gzip
import json
import random
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_IDENTITIES = random.Random(20261127)
uuid.uuid4 = lambda: uuid.UUID(int=_IDENTITIES.getrandbits(128), version=4)  # type: ignore[assignment]

from generate_guided_start_runs import READY, answer, begin, bind, draft  # noqa: E402
from generate_guided_start_runs import run as start_run  # noqa: E402

from sylvia_gm.application.authority import AuthorityOwner  # noqa: E402

OUT = Path(__file__).resolve().parent / "guided"
SEED = OUT / "seed.sqlite3.gz"
STORM, HARBOUR = "session-storm", "session-harbour"
PLAN = "# Storm Courier\n\nA courier shelters from a storm.\n"


def finalize(campaign: str = "storm", provider: str = "standin", model: str = "standin/standin-narrator",
             plan_text: str = PLAN, structured: object = None, override: bool = False) -> dict[str, object]:
    return {"op": "finalize_campaign_construction", "campaign": campaign, "dm_provider_id": provider,
            "dm_model_id": model, "plan_artifact_text": plan_text, "structured_plan": structured,
            "model_guidance_override": override}


OPERATIONS: list[dict[str, object]] = [
    draft("storm", "Storm Courier", STORM), draft("harbour", "Harbour Lights", HARBOUR),
    answer("a-ruleset", "ruleset", 0, {"candidate_id": None}),
    answer("a-name", "name", 1, {"display_name": "Storm Courier"}),
    answer("a-boundaries", "boundaries_preferences", 2, {"boundaries": []}),
    answer("a-requests", "specific_requests", 3, {"requests": []}),
    {"op": "append_agent_message", "session_id": STORM, "message_id": "guided-ready-storm", "content": READY},
    bind("storm-bind"),
    # Not begun yet.
    finalize(),
    begin("storm-begin", 0, lane="customize", canonical_input={"premise": "A courier shelters from a storm."}),
    # Refusals before anything is written.
    finalize(campaign=" storm"), finalize(provider=""), finalize(model="standin/ narrator "),
    finalize(plan_text="   \n"), finalize(structured={"format_id": "other"}),
    finalize(structured={"format_id": "sylvia-gm-campaign-plan", "format_version": 2, "bindings": {
        "ruleset": None, "campaign_setting": None, "package_closure_sha256": "0" * 64}}),
    finalize(campaign="harbour"), finalize(campaign="campaign-missing"),
    # The guided start, its replay, and a replay that names another DM model.
    finalize(), finalize(), finalize(model="standin/other"),
]


def run(owner: AuthorityOwner, op: dict[str, object], campaigns: dict[str, str]) -> dict[str, object]:
    if op["op"] != "finalize_campaign_construction":
        return start_run(owner, op, campaigns)
    campaign = campaigns.get(str(op["campaign"]), str(op["campaign"]))
    try:
        completed, plan, handoff = owner.finalize_campaign_construction(
            campaign_id=campaign, dm_provider_id=str(op["dm_provider_id"]), dm_model_id=str(op["dm_model_id"]),
            plan_artifact_text=str(op["plan_artifact_text"]), structured_plan=op["structured_plan"],  # type: ignore[arg-type]
            model_guidance_override=bool(op["model_guidance_override"]))
    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
        return {"error": type(error).__name__, "code": getattr(error, "code", None), "message": str(error)}
    return {"ok": {
        "completed": {"campaign_id": completed.campaign_id, "lane": completed.lane,
                      "canonical_representation_sha256": completed.canonical_representation_sha256,
                      "resulting_revision": completed.resulting_revision},
        "plan": {"plan_id": plan.plan_id, "content_sha256": plan.content_sha256, "plan_revision": plan.plan_revision,
                 "resulting_campaign_revision": plan.resulting_campaign_revision,
                 "payload_sha256": plan.payload_sha256},
        "handoff": {"handoff_id": handoff.handoff_id, "dm_session_id": handoff.dm_session_id,
                    "resulting_campaign_revision": handoff.resulting_campaign_revision,
                    "payload_sha256": handoff.payload_sha256},
    }}


def checkpoint(database: Path) -> bytes:
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()
    return database.read_bytes()


def main() -> None:
    with tempfile.TemporaryDirectory(dir=OUT.parent) as scratch:
        database = Path(scratch) / "authority.sqlite3"
        database.write_bytes(gzip.decompress(SEED.read_bytes()))
        campaigns: dict[str, str] = {}
        with AuthorityOwner.open(database, "guided-finalize-runs") as owner:
            outcomes = [run(owner, op, campaigns) for op in OPERATIONS]
        (OUT / "finalize_runs.sqlite3.gz").write_bytes(gzip.compress(checkpoint(database), mtime=0))
    record = {"python": sys.version.split()[0], "operations": OPERATIONS, "outcomes": outcomes}
    text = json.dumps(record, indent=1, ensure_ascii=True) + "\n"
    (OUT / "finalize_runs.json").write_bytes(text.encode("utf-8"))


if __name__ == "__main__":
    main()
