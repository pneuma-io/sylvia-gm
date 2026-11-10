"""Rebuild a standalone, plain HTML report from validated result records."""

from __future__ import annotations

import html
import math
import random
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from .contracts import WEIGHTS, Result, digest, read_json, write_json


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def score(rows: list[Result], categories: dict[str, str]) -> dict[str, Any]:
    """Average run pass rates within scenarios; unknown categories never redistribute weight."""
    by_scenario: dict[str, list[int]] = defaultdict(list)
    unresolved: set[str] = set()
    for row in rows:
        if row.outcome != "pass" and row.failure_cause not in {"gm_behavior", "scripted_driver"}:
            unresolved.add(row.scenario_id)
            continue
        by_scenario[row.scenario_id].append(int(row.outcome == "pass"))
    scores: dict[str, float | None] = {}
    bands: dict[str, Any] = {}
    rng = random.Random(0)
    for category in WEIGHTS:
        members = [
            values for identity, values in by_scenario.items() if categories[identity] == category
        ]
        selected_ids = {identity for identity, value in categories.items() if value == category}
        if selected_ids - by_scenario.keys() or selected_ids & unresolved:
            members = []
        scores[category] = (
            100 * statistics.mean(statistics.mean(v) for v in members) if members else None
        )
        if members and all(len(v) >= 2 for v in members):
            samples = [
                100 * statistics.mean(statistics.mean(rng.choices(v, k=len(v))) for v in members)
                for _ in range(1000)
            ]
            bands[category] = [percentile(samples, 0.025), percentile(samples, 0.975)]
        else:
            bands[category] = None
    headline = (
        sum(WEIGHTS[c] * value for c, value in scores.items() if value is not None) / 100
        if all(scores[c] is not None for c in WEIGHTS)
        else None
    )
    waits = [a.elapsed_seconds for row in rows for a in row.attempts]
    turns: dict[tuple[str, int], list[Any]] = defaultdict(list)
    for row in rows:
        for attempt in row.attempts:
            turns[row.run_id, attempt.turn].append(attempt)
    total_waits = [sum(a.elapsed_seconds for a in attempts) for attempts in turns.values()]
    return {
        "categories": scores,
        "category_95pct_bands": bands,
        "headline": headline,
        "runs": len(rows),
        "passed": sum(row.outcome == "pass" for row in rows),
        "incomplete": sum(row.outcome in {"incomplete", "error"} for row in rows),
        "missing_attempt_records": sum(
            not row.attempts and bool((row.observations.get("provider") or {}).get("requests"))
            for row in rows
        ),
        "session_breaker_share": statistics.mean(bool(row.session_breakers) for row in rows)
        if rows
        else None,
        "interrupted_first_attempts": sum(not attempts[0].completed for attempts in turns.values()),
        "turns": len(turns),
        "median_turn_seconds": statistics.median(total_waits) if total_waits else None,
        "p90_turn_seconds": percentile(total_waits, 0.9),
        "median_attempt_seconds": statistics.median(waits) if waits else None,
    }


def load_results(directory: Path) -> tuple[dict[str, Any], list[Result]]:
    manifest = read_json(directory / "manifest.json")
    specification = {k: value for k, value in manifest.items() if k != "suite_id"}
    if digest(specification) != manifest["suite_id"]:
        raise ValueError("Manifest identity is invalid")
    expected_setups = {s["id"]: s for s in manifest["setups"]}
    expected_scenarios = {s["id"]: s for s in manifest["scenarios"]}
    seed_record = read_json(directory / "seed.json") if (directory / "seed.json").exists() else None
    results = []
    identities = set()
    for path in sorted((directory / "results").glob("*.json")):
        row = Result.model_validate(read_json(path))
        scenario = expected_scenarios.get(row.scenario_id)
        key = (row.setup.id, row.scenario_id, row.repetition)
        if (
            row.suite_id != manifest["suite_id"]
            or row.source != manifest["source"]
            or row.setup.model_dump() != expected_setups.get(row.setup.id)
            or scenario is None
            or row.scenario_sha256 != digest(scenario)
            or row.scenario_version != scenario["version"]
            or row.category != scenario["category"]
            or {c.id for c in row.checks} != set(scenario["checks"])
            or row.repetition >= manifest["repetitions"]
            or seed_record is None
            or row.fixture_sha256 != seed_record["database_sha256"]
            or (
                manifest.get("fixture_pack_sha256") is not None
                and seed_record.get("fixture_pack_sha256") != manifest["fixture_pack_sha256"]
            )
            or (
                "randomness_policy" in manifest
                and (
                    row.observations.get("randomness", {}).get("policy")
                    != manifest["randomness_policy"]
                    or row.observations.get("challenge")
                    != {
                        "baseline_sha256": digest(seed_record["observation"]),
                        "player_messages_sha256": digest(scenario["player_messages"]),
                    }
                )
            )
            or (
                row.outcome == "pass"
                and {a.turn for a in row.attempts} != set(range(len(scenario["player_messages"])))
            )
            or key in identities
        ):
            raise ValueError(f"Mismatched or duplicate result: {path.name}")
        identities.add(key)
        results.append(row)
    return manifest, results


def challenge_comparison(rows: list[Result]) -> list[dict[str, Any]]:
    """Compare the actual starting observations and first provider input in matched runs."""
    groups: dict[tuple[str, int], list[Result]] = defaultdict(list)
    for row in rows:
        groups[row.scenario_id, row.repetition].append(row)
    comparisons = []
    for (scenario, repetition), members in sorted(groups.items()):
        baselines, requests, seeds = set(), set(), defaultdict(set)
        observed_inputs = 0
        for row in members:
            challenge = row.observations.get("challenge")
            if challenge:
                baselines.add(digest(challenge))
            provider = row.observations.get("provider") or {}
            calls = provider.get("requests", [])
            if calls:
                gm = next((r for r in calls if r.get("stage") == "gm"), None)
                if gm is None and not any("stage" in r for r in calls):
                    gm = next((r for r in calls if r["tool_count"] > 0), None)
                if gm is not None:
                    requests.add((gm["messages_sha256"], gm["tools_sha256"]))
                    observed_inputs += 1
            for item in row.observations.get("randomness", {}).get("trace", []):
                key = (item["turn"], item["package"], item["tool"], item["occurrence"])
                seeds[key].add(item["seed"])
        observed_challenges = sum(bool(r.observations.get("challenge")) for r in members)
        comparisons.append(
            {
                "scenario": scenario,
                "repetition": repetition,
                "runs": len(members),
                "starting_challenge": "identical"
                if len(baselines) == 1 and observed_challenges == len(members) and len(members) > 1
                else "unverified"
                if len(baselines) < 2
                else "different",
                "first_provider_input": "identical"
                if len(requests) == 1 and observed_inputs == len(members) and len(members) > 1
                else "unverified"
                if len(requests) < 2
                else "different",
                "paired_seed_conflicts": sum(len(values) > 1 for values in seeds.values()),
            }
        )
    return comparisons


def report(directory: Path, destination: Path | None = None) -> Path:
    manifest, rows = load_results(directory)
    return render(directory, manifest, rows, destination)


def render(
    directory: Path, manifest: dict[str, Any], rows: list[Result], destination: Path | None = None
) -> Path:
    """Render validated records without rewriting their execution lineage."""
    destination = destination or directory / "report.html"
    categories = {s["id"]: s["category"] for s in manifest["scenarios"]}
    grouped: dict[str, list[Result]] = defaultdict(list)
    for row in rows:
        grouped[row.setup.id].append(row)
    summaries = {s["id"]: score(grouped[s["id"]], categories) for s in manifest["setups"]}
    expected = len(manifest["setups"]) * len(manifest["scenarios"]) * manifest["repetitions"]
    scripted = all(s["kind"] == "scripted" for s in manifest["setups"])
    comparisons = challenge_comparison(rows)

    def escape(value: Any) -> str:
        return html.escape(str(value), quote=True)

    def number(value: float | None) -> str:
        return "Untested" if value is None else f"{value:.1f}"

    body = [
        "<!doctype html><html lang='en'><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<title>SylviaBench results</title><h1>SylviaBench</h1>",
        "<p><strong>Framework self-test. No model performance was measured.</strong></p>"
        if scripted
        else "<p><strong>Uncalibrated runs. No player verdicts or recommendations.</strong></p>",
        f"<p>{len(rows)} of {expected} planned runs recorded. Missing runs remain untested.</p>",
        "<p>Rules 30%, memory 25%, fair play 25%, story 20%. The headline stays untested until "
        "all four categories have observations. Scripted and model runs are labelled separately. "
        "No comparison or confidence claim is made for an incomplete experiment.</p>",
        "<table border='1' cellpadding='6'><caption>Recorded results</caption><thead><tr>"
        "<th scope='col'>Setup / execution</th><th>Rules</th><th>Memory</th><th>Fair play</th>"
        "<th>Story</th><th>Headline</th><th>Runs passed</th><th>Interrupted turns</th>"
        "<th>Median / p90 wait (s)</th></tr></thead><tbody>",
    ]
    for setup in manifest["setups"]:
        summary = summaries[setup["id"]]
        complete = len(grouped[setup["id"]]) == len(manifest["scenarios"]) * manifest["repetitions"]
        body.append(f"<tr><th scope='row'>{escape(setup['name'])}<br>{escape(setup['kind'])}</th>")
        for category in WEIGHTS:
            value = summary["categories"][category] if complete else None
            band = summary["category_95pct_bands"][category] if complete else None
            body.append(
                "<td>"
                + number(value)
                + (f" [{number(band[0])}, {number(band[1])}]" if band else "")
                + "</td>"
            )
        body.append(
            f"<td>{number(summary['headline']) if complete else 'Untested'}</td>"
            f"<td>{summary['passed']}/{summary['runs']}</td>"
            f"<td>{summary['interrupted_first_attempts']}/{summary['turns']}</td>"
            f"<td>{number(summary['median_turn_seconds'])} / "
            f"{number(summary['p90_turn_seconds'])}</td></tr>"
        )
        if summary["missing_attempt_records"]:
            body.append(
                "<tr><td colspan='9'>Attempt details are unavailable for "
                + str(summary["missing_attempt_records"])
                + " run(s) with provider activity. Wait and first-attempt interruption figures "
                "cover recorded attempts only; missing time is not replaced with zero.</td></tr>"
            )
    body += [
        "</tbody></table><p>Bracketed ranges are 95% bootstrap intervals within each scenario, "
        "only when every observed scenario has at least two runs. They describe this fixed "
        "scenario set. With very few runs or identical outcomes these empirical ranges can "
        "collapse; they do not establish general reliability. Wait includes Retry attempts. "
        "Usage and cost stay unknown unless reported.</p>",
        "<h2>Challenge comparison</h2><p>The starting save and fixed player messages are checked "
        "separately from the first GM input at Sylvia's provider boundary. Preparation calls are "
        "recorded separately. A different input needs "
        "inspection before treating setups as an equivalent challenge. Transport serialization "
        "may differ. Paired dice applies to matching package/tool occurrences; models can choose "
        "different actions and consume different numbers of rolls.</p><pre>",
        escape(json_text(comparisons)),
        "</pre><h2>Run details</h2>",
    ]
    for row in rows:
        body.append(
            f"<details><summary>{escape(row.setup.name)} / {escape(row.scenario_id)} / "
            f"run {row.repetition + 1}: {escape(row.outcome)} "
            f"({escape(row.execution_kind)})</summary>"
        )
        breaker_text = ", ".join(row.session_breakers) or (
            "Not evaluated" if row.outcome == "error" else "None observed"
        )
        body.append(
            f"<p>Session-breakers: {escape(breaker_text)}. "
            f"Cause: {escape(row.failure_cause or 'No failure')}. "
            f"{escape(row.usage_note)}</p>"
        )
        body.append(
            f"<p>Whole run elapsed: {number(row.elapsed_seconds)} seconds, including "
            "setup and observation. This is separate from the recorded turn waits.</p>"
        )
        body.append(
            "<table border='1' cellpadding='4'><tr><th>Check</th>"
            "<th>Outcome</th><th>Evidence</th></tr>"
        )
        for check in row.checks:
            evidence = json_text(check.evidence)
            body.append(
                f"<tr><th scope='row'>{escape(check.id)}</th><td>{escape(check.status)}</td>"
                f"<td>{escape(check.reason)}<pre>{escape(evidence)}</pre></td></tr>"
            )
        body.append(
            "</table><p>Run started: "
            + escape(row.started_at)
            + "; requested provider: "
            + escape(row.setup.provider_id)
            + "; requested reasoning: "
            + escape(row.setup.reasoning or "No override")
            + "; requested model: "
            + escape(row.setup.model_id)
            + "; observed model: "
            + escape(row.observed_model or "Unknown")
            + ". "
            + escape(row.identity_note)
            + "</p>"
        )
        body.append(
            "<details><summary>Attempts and waits</summary><pre>"
            + escape(json_text([a.model_dump() for a in row.attempts]))
            + "</pre></details><details><summary>Provider metadata and usage</summary><pre>"
            + escape(json_text({"provider": row.observations.get("provider"), "usage": row.usage}))
            + "</pre></details></details>"
        )
    body += [
        "<h2>Scope and source</h2><p>Three SRD trial scenarios. Secret checking uses a declared "
        "phrase and knowledge visibility; semantic leaks, story judgement, long context and "
        "compaction are untested. Provider selections and request hashes are recorded per run; "
        "adapter-reported identity is not automatically provider confirmation. "
        "This report supplies no calibrated verdict or model recommendation.</p><pre>",
        escape(json_text(manifest["source"])),
        "</pre></html>",
    ]
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(body), encoding="utf-8")
    write_json(
        directory / "summary.json",
        {
            "suite_id": manifest["suite_id"],
            "recorded_runs": len(rows),
            "planned_runs": expected,
            "setups": summaries,
            "challenge_comparison": comparisons,
        },
    )
    return destination


def json_text(value: Any) -> str:
    import json

    return json.dumps(value, indent=2, ensure_ascii=False)
