"""A separate GM survey on cloned completed saves, after the scored batch is sealed."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from .contracts import PROTOCOL, ROOT, digest, write_json
from .fixtures import ScriptedGM, copy_save, observe, settle
from .owner import OwnerClient
from .provenance import ObservedResolver, configuration
from .report import load_results
from .runner import _resolver, source_identity

SURVEY_VERSION = 3
SURVEY = """Out of character: scored gameplay has concluded. You were acting as GM in
SylviaBench, helping us benchmark this model connection and Sylvia's GM experience.
Please complete this survey about the session you actually received. Your answers cannot
change the frozen gameplay score. Report observable examples, not private reasoning.
Say 'none' or 'unknown' when appropriate; do not invent problems or assume a failure occurred.
Earlier dialogue was prepared by a scripted GM and inherited by you. It is not your authored
gameplay. The scope below identifies the player messages you were tested on, including any
compaction you performed for that segment. Discuss your own actions in that segment. Label any
problem in earlier material as inherited context; do not claim you performed those earlier calls.
If authorship or chronology is unclear, say so. Do not assess this survey reply as gameplay.
The fresh Campaign State supplied with this survey is after the tested gameplay. It is not
evidence of which state you saw before your action. If that earlier input is unavailable,
say unknown rather than inferring its contents from the current state.

1. What helped you carry out the GM role?
2. Where did you become confused, blocked, or forced to guess? Give a concrete example if available.
3. Were instructions, tools, required inputs, or error messages unclear or contradictory?
4. Was information difficult to find or retain, or was context unnecessarily repetitive?
5. What specific change would have made the session easier to run?
6. Is there anything else about the experience we should investigate?

Answer as an out-of-character debrief, without advancing the story or taking gameplay actions.
Use the normal final-response mechanism if Sylvia requires it.
"""


def collect(batch: Path, output: Path, *, allow_model_calls: bool = False) -> Path:
    """Keep scored saves/results untouched; the survey gets its own cloned owner and usage."""
    batch, output = batch.resolve(), output.resolve()
    manifest, rows = load_results(batch)
    if manifest.get("protocol") not in {PROTOCOL, "sylviabench-gameplay-v2"}:
        raise ValueError("Debrief requires protocol v2 results; original trials remain untouched")
    if (batch / "run.lock").exists() or len(rows) != (
        len(manifest["setups"]) * len(manifest["scenarios"]) * manifest["repetitions"]
    ):
        raise ValueError("Finish and close the entire scored batch before revealing the benchmark")
    if any(row.setup.kind == "model" for row in rows) and not allow_model_calls:
        raise ValueError("Debrief inference requires --allow-model-calls")
    source = source_identity()
    if source["application_sha256"] != manifest["source"]["application_sha256"]:
        raise ValueError("Debrief requires identical Sylvia application/content bytes")
    if (
        output.exists()
        or output == ROOT
        or ROOT.is_relative_to(output)
        or (output == batch or output.is_relative_to(batch) or batch.is_relative_to(output))
    ):
        raise ValueError("Use a fresh dedicated debrief directory separate from source and results")
    output.mkdir(parents=True)
    result_files = sorted((batch / "results").glob("*.json"))
    seal = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in result_files}
    write_json(
        output / "manifest.json",
        {
            "schema_version": 1,
            "survey_version": SURVEY_VERSION,
            "survey": SURVEY,
            "survey_sha256": digest(SURVEY),
            "gameplay_suite_id": manifest["suite_id"],
            "gameplay_results": seal,
            "source": source,
            "gameplay_source": manifest["source"],
            "note": "Model self-reports; unscored hypotheses requiring independent evidence.",
        },
    )
    for row in rows:
        case = next(s for s in manifest["scenarios"] if s["id"] == row.scenario_id)
        scope = {
            "tested_player_messages": case["player_messages"],
            "earlier_history": "inherited scripted fixture, not your actions",
            "own_committed_actions": [
                {"action_id": e["action_id"], "tool_name": e.get("tool_name")}
                for e in row.observations.get("mechanics", [])
            ],
        }
        previous, final = row.observations["before"]["talks"], row.observations["after"]["talks"]
        if len(final) > len(previous):
            scope["own_last_response"] = final[-1]
        prompt = SURVEY + "\nSYLVIABENCH_SURVEY_SCOPE:\n" + json.dumps(scope, ensure_ascii=False)
        directory = output / row.run_id
        if directory.parent != output:
            raise ValueError("Unsafe run identity")
        original = (
            batch / "runs" / row.run_id / row.observations["database_relative_path"]
        ).resolve()
        if not original.is_relative_to(batch / "runs" / row.run_id):
            raise ValueError("Gameplay database path escaped its run")
        if (
            hashlib.sha256(original.read_bytes()).hexdigest()
            != row.observations["closed_database_sha256"]
        ):
            raise ValueError("The completed gameplay save changed before debrief")
        if configuration(row.setup) != manifest["provider_configuration"][row.setup.id]:
            raise ValueError("The selected provider configuration changed before debrief")
        database = directory / "campaign.sqlite3"
        copy_save(original, database)
        ids = row.observations["session_ids"]
        before = observe(database, **ids)
        scripted = (
            ScriptedGM(
                [
                    settle(
                        "1. Saved state helped. 2. None. 3. None. 4. Unknown. "
                        "5. No change identified in this scripted control. 6. None."
                    )
                ]
            )
            if row.setup.kind == "scripted"
            else None
        )
        observer = ObservedResolver(_resolver(row.setup, scripted))
        record: dict[str, Any] = {
            "schema_version": 1,
            "survey_version": SURVEY_VERSION,
            "survey_sha256": digest(SURVEY),
            "scope": scope,
            "prompt_sha256": digest(prompt),
            "gameplay_run_id": row.run_id,
            "setup": row.setup.model_dump(),
            "status": "failed",
            "response": None,
            "evidence_kind": "scripted_control" if scripted else "model_self_report",
        }
        try:
            with OwnerClient(database, observer, timeout=300) as client:
                client.request("set_admin_full_audit", {"enabled": True})
                basis = client.request("display_snapshot", {"campaign_id": ids["campaign"]})
                identity = uuid.uuid4().hex
                reply = client.request(
                    "submit_session_input",
                    {
                        "session_id": ids["session"],
                        "attempt_id": identity,
                        "message_id": identity,
                        "turn_id": next(
                            (k for k, v in basis["turns"].items() if v == "open"), identity
                        ),
                        "expected_revision": basis["revision"],
                        "user_text": prompt,
                    },
                    campaign=ids["campaign"],
                    attempt=identity,
                )
            after = observe(database, **ids)
            if not reply.get("interruption") and len(after["talks"]) > len(before["talks"]):
                record.update(status="completed", response=after["talks"][-1])
        except Exception as error:
            record["error_type"] = type(error).__name__
        record.update(provider=observer.summary(), usage=observer.usage())
        write_json(output / "responses" / (row.run_id + ".json"), record)
        if any(f.get("pause_batch") for f in observer.failures):
            write_json(output / "pause.json", {"reason": "provider_usage_reset"})
            break
    if seal != {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in result_files}:
        raise ValueError("Scored results changed during debrief")
    return output / "manifest.json"
