"""Run isolated campaigns, preserve attempts and resume only an identical experiment."""

from __future__ import annotations

import hashlib
import subprocess
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sylvia_gm.application.authority import AuthorityOwner

from .checks import breakers, evaluate
from .contracts import (
    ROOT,
    Attempt,
    Check,
    FailureCause,
    Outcome,
    Result,
    Scenario,
    Setup,
    digest,
    read_json,
    write_json,
)
from .fixtures import ScriptedResolver, copy_save, observe, receipt_summary, script, seed
from .owner import OwnerClient, OwnerError


def source_identity() -> dict[str, Any]:
    """Bind source HEAD and actual implementation/content changes, including this framework."""

    def git(*args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()

    files = sorted(Path(__file__).parent.glob("*.py")) + [
        Path(__file__).with_name("scenarios.json")
    ]
    return {
        "commit": git("rev-parse", "HEAD"),
        "working_diff_sha256": hashlib.sha256(
            subprocess.check_output(
                [
                    "git",
                    "diff",
                    "HEAD",
                    "--",
                    "src",
                    "rulesets",
                    "campaign-plans",
                    "campaign-settings",
                ],
                cwd=ROOT,
            )
        ).hexdigest(),
        "framework_sha256": digest(
            {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
        ),
    }


def _resolver(setup: Setup, provider: Any) -> Any:
    if setup.kind == "scripted":
        return ScriptedResolver(provider)
    # Imported only after run()'s explicit opt-in. Offline paths cannot construct this resolver.
    from sylvia_gm.interfaces.owner_process import ConfiguredProviderResolver

    if setup.owner_config is None:
        raise ValueError("A live connection requires an explicitly selected configuration")
    return ConfiguredProviderResolver(Path(setup.owner_config).resolve())


def execute(
    scenario: Scenario,
    setup: Setup,
    repetition: int,
    directory: Path,
    frozen: Path,
    ids: dict[str, str],
    suite_id: str,
    source: dict[str, Any],
    retries: int,
) -> Result:
    """A run owns a fresh save; retries go through Sylvia's manual Retry command."""
    started = datetime.now(UTC).isoformat()
    begin = time.monotonic()
    directory.mkdir(parents=True, exist_ok=False)
    database = directory / "campaign.sqlite3"
    copy_save(frozen, database)
    before = observe(database, **ids)
    observations: dict[str, Any] = {"before": before, "saved": before}
    if scenario.id == "T3":
        reopened = directory / "reopened" / "campaign.sqlite3"
        copy_save(database, reopened)
        database = reopened
        observations["reopened"] = observe(database, **ids)
    with AuthorityOwner.open(database, "bench-switch") as owner:
        owner.update_agent_session_model(
            ids["session"], provider_id=setup.provider_id, model_id=setup.model_id
        )
        if setup.reasoning is not None:
            owner.update_agent_session_reasoning(ids["session"], effort=setup.reasoning, once=None)
    provider = script(scenario.id, setup, before) if setup.kind == "scripted" else None
    attempts: list[Attempt] = []
    completed = True
    error: Exception | None = None
    try:
        with OwnerClient(database, _resolver(setup, provider), timeout=300) as client:
            client.request("set_admin_full_audit", {"enabled": True})
            for turn_number, message in enumerate(scenario.player_messages):
                basis = client.request("display_snapshot", {"campaign_id": ids["campaign"]})
                identity = uuid.uuid4().hex
                params = {
                    "session_id": ids["session"],
                    "attempt_id": identity,
                    "message_id": identity,
                    "turn_id": next(
                        (key for key, status in basis["turns"].items() if status == "open"),
                        identity + "-turn",
                    ),
                    "expected_revision": basis["revision"],
                    "user_text": message,
                }
                succeeded = False
                for attempt in range(retries + 1):
                    start = time.monotonic()
                    interruption: str | None = None
                    reply: Any = None
                    try:
                        if attempt == 0:
                            reply = client.request(
                                "submit_session_input",
                                params,
                                campaign=ids["campaign"],
                                attempt=identity,
                            )
                        else:
                            retry = client.request(
                                "retryable_interruption",
                                {
                                    "campaign_id": ids["campaign"],
                                    "failed_attempt_id": identity,
                                },
                            )
                            if not retry.get("retryable"):
                                interruption = (
                                    "Owner reports that this interruption is not retryable"
                                )
                                break
                            retry_id = uuid.uuid4().hex
                            identity = retry_id
                            reply = client.request(
                                "manual_retry_session",
                                {
                                    "session_id": ids["session"],
                                    "interruption_id": retry["interruption_id"],
                                    "retry_id": retry_id,
                                },
                                campaign=ids["campaign"],
                                attempt=retry_id,
                            )
                        if reply.get("interruption"):
                            interruption = "The owner reported a narration interruption"
                        else:
                            succeeded = True
                    except OwnerError as rejected:
                        interruption = str(rejected)
                    attempts.append(
                        Attempt(
                            turn=turn_number,
                            number=attempt,
                            elapsed_seconds=time.monotonic() - start,
                            completed=succeeded,
                            interruption=interruption,
                        )
                    )
                    write_json(directory / "attempts.json", [a.model_dump() for a in attempts])
                    if succeeded:
                        break
                if not succeeded:
                    completed = False
                    break
    except Exception as caught:
        completed = False
        error = caught
    observations["after"] = observe(database, **ids)
    observations["receipts"] = (
        [{"id": row["id"], **receipt_summary(row["result"])} for row in provider.receipts]
        if provider
        else []
    )
    baseline_sequences = {event["sequence"] for event in before["mechanics"]}
    observations["mechanics"] = [
        event
        for event in observations["after"]["mechanics"]
        if event["sequence"] not in baseline_sequences
    ]
    if error:
        # Error types are safe; arbitrary provider exception text can contain private credentials.
        observations["execution_error"] = type(error).__name__
    checks = (
        evaluate(scenario, observations)
        if completed
        else [
            Check(id=identity, status="not_evaluated", reason="The scenario did not complete.")
            for identity in scenario.checks
        ]
    )
    passed = completed and all(c.status == "pass" for c in checks)
    outcome: Outcome = (
        ("error" if error else "incomplete") if not completed else ("pass" if passed else "fail")
    )
    if completed and any(check.status == "not_evaluated" for check in checks):
        outcome = "incomplete"
    injected = setup.kind == "scripted" and (
        (
            setup.behavior == "bad"
            and any(
                check.id == scenario.expected_bad_check and check.status == "fail"
                for check in checks
            )
        )
        or (setup.behavior == "stuck" and len(attempts) == retries + 1 and not completed)
    )
    cause: FailureCause | None = (
        None if passed else ("scripted_driver" if injected and not error else "unknown")
    )
    return Result(
        run_id=directory.name,
        suite_id=suite_id,
        source=source,
        scenario_id=scenario.id,
        scenario_version=scenario.version,
        scenario_sha256=digest(scenario.model_dump()),
        fixture_sha256=hashlib.sha256(frozen.read_bytes()).hexdigest(),
        category=scenario.category,
        repetition=repetition,
        execution_kind="framework_selftest" if setup.kind == "scripted" else "model_run",
        setup=setup,
        started_at=started,
        elapsed_seconds=time.monotonic() - begin,
        attempts=attempts,
        checks=checks,
        outcome=outcome,
        failure_cause=cause,
        failure_evidence=None
        if passed
        else (
            "The selected scripted behavior injected the observed failure."
            if cause == "scripted_driver"
            else "Execution or checker evidence is insufficient to assign a specific cause."
        ),
        session_breakers=breakers(checks, completed) if not error else [],
        observations=observations,
        identity_note="Scripted provider; no model inference occurred."
        if setup.kind == "scripted"
        else "Requested selection only; provider-confirmed identity is unavailable.",
        usage_note="Scripted execution; no model usage or monetary cost."
        if setup.kind == "scripted"
        else "Usage and cost were not reported; no estimate is substituted.",
    )


def run(
    output: Path,
    selected: list[Setup],
    suite: list[Scenario],
    *,
    repetitions: int = 1,
    retries: int = 2,
    resume: bool = False,
    allow_model_calls: bool = False,
) -> list[Result]:
    """Freeze configuration before any owner starts; never guess a setup or resume identity."""
    if repetitions < 1 or repetitions > 100 or retries not in range(3):
        raise ValueError("Use 1–100 repetitions and 0–2 retries")
    if any(s.kind == "model" for s in selected) and not allow_model_calls:
        raise ValueError("Model execution requires --allow-model-calls and an explicit model setup")
    output = output.resolve()
    if output == ROOT or ROOT.is_relative_to(output):
        raise ValueError(
            "Output must be a dedicated results directory, not the source or its parent"
        )
    source = source_identity()
    specification = {
        "schema_version": 1,
        "source": source,
        "setups": [s.model_dump() for s in selected],
        "scenarios": [s.model_dump() for s in suite],
        "repetitions": repetitions,
        "retries": retries,
    }
    suite_id = digest(specification)
    manifest = {**specification, "suite_id": suite_id}
    if output.exists() and not resume:
        raise ValueError("Output already exists; choose a fresh directory or use --resume")
    output.mkdir(parents=True, exist_ok=True)
    lock = output / "run.lock"
    with lock.open("x", encoding="utf-8") as handle:
        handle.write(str(uuid.uuid4()))
    try:
        manifest_path = output / "manifest.json"
        if resume:
            if not manifest_path.exists() or read_json(manifest_path) != manifest:
                raise ValueError(
                    "Resume identity mismatch: source, suite, setup or run policy changed"
                )
        else:
            write_json(manifest_path, manifest)
        results: list[Result] = []
        pending = []
        for setup in selected:
            for scenario in suite:
                for repetition in range(repetitions):
                    key = digest([setup.id, scenario.id, repetition])[:24]
                    result_path = output / "results" / (key + ".json")
                    if result_path.exists():
                        result = Result.model_validate(read_json(result_path))
                        if (
                            result.suite_id != suite_id
                            or result.setup != setup
                            or result.scenario_id != scenario.id
                            or result.repetition != repetition
                            or result.fixture_sha256
                            != read_json(output / "seed.json")["database_sha256"]
                        ):
                            raise ValueError("A saved result does not belong to this manifest")
                        results.append(result)
                    else:
                        pending.append((setup, scenario, repetition, key, result_path))
        if not pending:
            return results
        seed_path = output / "seed" / "campaign.sqlite3"
        ids_path = output / "seed.json"
        if seed_path.exists() or ids_path.exists():
            if not (seed_path.exists() and ids_path.exists()):
                raise ValueError("Incomplete frozen seed: use a fresh results directory")
            seed_record = read_json(ids_path)
            if hashlib.sha256(seed_path.read_bytes()).hexdigest() != seed_record["database_sha256"]:
                raise ValueError("Frozen campaign identity mismatch")
            ids = {k: seed_record[k] for k in ("campaign", "session")}
        else:
            print("Preparing the synthetic campaign through Sylvia's owner...", flush=True)
            seed_path, ids = seed(output)
        for setup, scenario, repetition, key, result_path in pending:
            print(
                f"Running {setup.id}/{scenario.id}/{repetition + 1} ({setup.kind})...", flush=True
            )
            # A previously interrupted process keeps its directory and attempt journal as evidence.
            directory = output / "runs" / (key + "-" + uuid.uuid4().hex[:8])
            result = execute(
                scenario, setup, repetition, directory, seed_path, ids, suite_id, source, retries
            )
            write_json(result_path, result.model_dump())
            results.append(result)
        return results
    finally:
        lock.unlink()
