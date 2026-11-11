from __future__ import annotations

from html import escape
import json
from pathlib import Path
from typing import Any


def _badge(value: str) -> str:
    normalized = value.upper().replace(" ", "_")
    kind = "good" if normalized in {"TRUE", "PASS", "IMPLEMENTED", "IMPLEMENTED_WITH_PARAMETERS", "CANDIDATE"} else ("bad" if normalized in {"FALSE", "FAIL", "STRUCTURAL_FAILURE"} else "review")
    return f'<span class="badge {kind}">{escape(value)}</span>'


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    body = []
    for row in rows:
        cells = "".join(f"<td>{value}</td>" for value in row)
        body.append(f"<tr>{cells}</tr>")
    return f'<div class="filter-table"><input class="table-filter" type="search" placeholder="Filter this table" aria-label="Filter table"><div class="table-wrap"><table><thead><tr>{"".join(f"<th>{escape(header)}</th>" for header in headers)}</tr></thead><tbody>{"".join(body)}</tbody></table></div></div>'


def _number(value: Any, digits: int = 3) -> str:
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return escape(str(value))


def render_html(result: dict[str, Any], output_path: Path) -> None:
    invariant_rows = [[escape(key), _badge("PASS" if value else "FAIL")] for key, value in sorted(result["invariants"].items())]
    scenario_rows = [
        [
            escape(item["scenario_id"]),
            escape(item["family"]),
            escape(item["map_id"]),
            _badge(item["support_status"]),
            escape("; ".join(item["unsupported_dependencies"]) or "—"),
        ]
        for item in result["scenario_coverage"]
    ]
    class_rows = [
        [row["level"], escape(row["class_id"]), escape(row["metric"]), _number(row["control"]), _number(row["candidate"]), _number(row["relative_delta"])]
        for row in result["exact"]["classes"]["rows"]
    ]
    progression_rows = [
        [
            row["level"],
            _number(row["outcome_share"]["estimate"]),
            f'{_number(row["outcome_share"]["low"])}–{_number(row["outcome_share"]["high"])}',
            _number(row["terminal_time_seconds"]["estimate"]),
            _badge(row["qualification"]),
        ]
        for row in result["progression"]["levels"]
    ]
    fire_summary = []
    for weapon in sorted({row["weapon_id"] for row in result["exact"]["fire_range"]["rows"]}):
        selected = [row for row in result["exact"]["fire_range"]["rows"] if row["weapon_id"] == weapon and row["distance_m"] in {5, 50, 400, 950}]
        for row in selected:
            fire_summary.append([escape(weapon), escape(row["armor_id"]), row["distance_m"], _number(row["hit_rate"]), _number(row["expected_res_damage"]), _number(row["expected_hp_damage"]), row["range_modifier"]])
    specialty_rows = []
    for specialty, data in result["specialty_coverage"]["specialties"].items():
        for node in data["nodes"]:
            findings = "; ".join(finding["code"] for finding in node["findings"])
            specialty_rows.append(
                [
                    escape(data["class_id"]),
                    escape(specialty),
                    escape(node["node"]["tier"]),
                    escape(node["node"]["name"]),
                    _badge(node["state"]),
                    escape(node["mechanic_id"] or "—"),
                    escape(findings or "—"),
                ]
            )
    finding_counts: dict[tuple[str, str], int] = {}
    for finding in result["findings"]:
        code = finding.get("code", "UNCLASSIFIED")
        category = finding.get("category", "UNCLASSIFIED")
        finding_counts[(category, code)] = finding_counts.get((category, code), 0) + 1
    finding_rows = [[escape(category), escape(code), count] for (category, code), count in sorted(finding_counts.items(), key=lambda item: (-item[1], item[0]))]
    parameter_rows = [
        [escape(parameter_id), escape(json.dumps(item["seed"])), escape(json.dumps(item["value"]))]
        for parameter_id, item in sorted(result["parameters"]["movement_from_seed"].items())
    ] or [["—", "No movement", "v0.1 seeds"]]
    mc = result["monte_carlo"]
    team_mc = result["mixed_team_objectives"]["team_control"]
    coverage_counts = result["specialty_coverage"]["coverage_counts"]
    team_metric_rows = [
        [
            escape(metric),
            _number(values["subject"]["estimate"]),
            _number(values["control"]["estimate"]),
            _number(values["paired_effect"]["estimate"]),
            f'{_number(values["paired_effect"]["low"])}–{_number(values["paired_effect"]["high"])}',
        ]
        for metric, values in team_mc.get("aggregate_metrics", {}).items()
    ]
    team_variant_rows = [
        [
            escape(variant),
            escape(", ".join(sorted(set(result["sample_replay"]["policies"].values()))) if result.get("sample_replay") else "—"),
            result["paired_mirrored_runs"],
            _number(result["subject_outcome_share"]["estimate"]),
            _number(result["terminal_time_seconds"]["estimate"]),
            _badge(result["qualification"]),
        ]
        for variant, result in sorted(result["mixed_team_objectives"]["team_control_variants"].items())
    ]
    objective_rows = []
    for item in result["mixed_team_objectives"]["results"]:
        outcome = item.get("outcome_share") or {}
        terminal = item.get("terminal_time_seconds") or {}
        objective_rows.append(
            [
                escape(item["scenario_id"]),
                _badge(item["status"]),
                _badge(item.get("qualification", "NOT_RECORDED")),
                _number(outcome.get("estimate", "—")),
                f'{_number(outcome.get("low", "—"))}–{_number(outcome.get("high", "—"))}',
                _number(terminal.get("estimate", "—")),
            ]
        )
    armor_rows = [
        [
            row["level"],
            escape(row["armor_id"]),
            escape(row["weapon_id"]),
            row["armor_res"],
            row["burden"],
            _number(row["movement_m_per_ap"]),
            _number(row["expected_shots_to_downed"]),
            _number(row["expected_ap_to_downed"]),
        ]
        for row in result["exact"]["armor"]["rows"]
    ]
    automatic_fire_rows = [
        [
            escape(row["weapon_id"]),
            escape(row["mode"]),
            row["rounds"],
            _number(row["expected_connections"]),
            _number(row["expected_recoil"]),
            _number(row["expected_ap"]),
            _number(row["connections_per_round"]),
        ]
        for row in result["exact"]["auto_strings"]["rows"]
    ]
    cover_counts: dict[tuple[str, str], int] = {}
    for row in result["exact"]["cover"]["routes"]:
        routing = row.get("routing")
        key = (
            row["exposure"],
            routing["outcome"] if isinstance(routing, dict) else "NO DIRECT ATTACK",
        )
        cover_counts[key] = cover_counts.get(key, 0) + 1
    cover_rows = [
        [escape(exposure), escape(outcome), count]
        for (exposure, outcome), count in sorted(cover_counts.items())
    ]
    suppression_rows = [
        [
            row["committed_rounds"],
            row["exposure_capacity"],
            row["setup_ap"],
            _badge(str(row["first_exposure_resolves_attack"])),
            row["fully_protected_damage"],
        ]
        for row in result["exact"]["suppression"]["rows"]
    ]
    overwatch = result["exact"]["overwatch"]
    overwatch_rows = [[
        overwatch["declared_arc_degrees"],
        overwatch["setup_ap"],
        overwatch["shot_allowance"],
        escape(json.dumps(overwatch["trigger_position"], sort_keys=True)),
        _badge(str(overwatch["movement_pauses_at_trigger"])),
        _number(overwatch["normal_attack_distribution"]["hit_rate"]),
    ]]
    cqc_rows = [
        [
            escape(row["maneuver"]),
            row["attacker_skill"],
            row["defender_skill"],
            row["defense"],
            _number(row["success_rate"]),
            row["ap"],
        ]
        for row in result["exact"]["cqc"]["rows"]
    ]
    explosive_rows = [
        [
            escape(row["material"]),
            escape(row["geometry"]),
            row["cover_hp_after"],
            _badge(str(row["cover_active_after"])),
            escape(", ".join(component["component"] for component in row["snapshot"])),
        ]
        for row in result["exact"]["explosives"]["blast_snapshot"]
    ]
    medical_rows = [
        [
            row["level"],
            escape(row["casualty_id"]),
            escape(row["action"]),
            _badge("CORPSMAN" if row["corpsman"] else "CONTROL"),
            _number(row["success_rate"]),
            row["ap"],
            _number(row["deterioration_seconds"]),
            _number(row["reversible_window_seconds"]),
        ]
        for row in result["exact"]["medical"]["rows"]
    ]
    specialty_effect_rows = []
    seen_nodes: set[str] = set()
    for item in result["specialty_coverage"].get("scenario_matrix", []):
        if item.get("role") != "PRIMARY" or item["node_id"] in seen_nodes:
            continue
        seen_nodes.add(item["node_id"])
        fixture = item.get("scenario_fixture", {})
        specialty_effect_rows.append(
            [
                escape(item["specialty"]),
                escape(item["node_id"]),
                item["level"],
                escape(fixture.get("map_id", "—")),
                escape(fixture.get("objective_id", "—")),
                escape(json.dumps(item.get("effect_measurements", {}), sort_keys=True)),
            ]
        )
    if not specialty_effect_rows:
        specialty_effect_rows = [["—", "No scenario measurement records", "—", "—", "—", "—"]]
    specialty_objective_rows = [
        [
            escape(item["specialty"]),
            item["level"],
            escape(item["role"]),
            escape(item["build_id"]),
            escape(item["fixture"]["map_id"]),
            escape(item["fixture"]["objective_id"]),
            escape(item["fixture"]["actual_scenario_id"]),
            _badge(str(item["candidate_metrics"]["objective_completed"])),
            item["candidate_metrics"][
                "structurally_executed_specialty_nodes"
            ],
            escape(json.dumps(item["objective_metric_deltas"], sort_keys=True)),
            _badge(str(item["generic_dominance_detected"])),
        ]
        for item in result["specialty_coverage"].get(
            "objective_scenario_matrix", []
        )
    ]
    if not specialty_objective_rows:
        specialty_objective_rows = [
            ["—", "—", "No specialty objective records", "—", "—", "—", "—", "—", "—", "—", "—"]
        ]
    artifacts = [
        ("Raw baseline JSON", "baseline.json"),
        ("Exact JSON", "exact-results.json"),
        ("Monte Carlo JSON", "monte-carlo-results.json"),
        ("Specialty coverage JSON", "specialty-coverage.json"),
        ("Findings JSON", "findings.json"),
        ("Reusable parameter set JSON", "parameter-set.json"),
        ("Parameter metadata JSON", "parameter-metadata.json"),
        ("Scenario execution JSON", "scenario-results.json"),
        ("Sample deterministic replay", "replays/sample-duel.json"),
        ("Sample 4v4 deterministic replay", "replays/sample-team-control.json"),
        ("Sample 4v4 combat-policy replay", "replays/sample-team-control-combat-postures.json"),
        ("Sample extraction replay", "replays/sample-team-extract.json"),
        ("Sample breach replay", "replays/sample-team-breach.json"),
        ("Sample recon/hack replay", "replays/sample-team-recon-hack.json"),
        ("Check CSV", "check-curves.csv"),
        ("Fire CSV", "fire-range.csv"),
        ("Class/progression CSV", "class-progression.csv"),
        ("Specialty CSV", "specialty-coverage.csv"),
        ("Structural specialty counterfactual matrix CSV", "specialty-scenarios.csv"),
        ("Specialty objective matrix CSV", "specialty-objectives.csv"),
        ("Team objectives CSV", "team-objectives.csv"),
        ("Scenario execution CSV", "scenario-execution.csv"),
    ]
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trigger Discipline v0.1 Balance Baseline</title>
<style>
:root{{--bg:#0b0e13;--panel:#121722;--ink:#e8edf5;--muted:#9ca9ba;--line:#2b3442;--accent:#f0bd66;--good:#58c98b;--bad:#ff6d72;--review:#e5a952}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,Segoe UI,sans-serif}} main{{max-width:1500px;margin:auto;padding:28px}}
h1{{font-size:clamp(28px,4vw,52px);line-height:1.04;margin:.2em 0}} h2{{margin-top:42px;border-top:1px solid var(--line);padding-top:24px}} h3{{color:var(--accent)}}
.lede,.muted{{color:var(--muted)}} .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px}} .card{{background:var(--panel);border:1px solid var(--line);padding:16px;border-radius:8px}}
.metric{{font-size:28px;font-weight:700}} .badge{{display:inline-block;padding:2px 8px;border-radius:99px;font-size:12px;font-weight:700;white-space:nowrap}} .good{{background:#153d2a;color:var(--good)}} .bad{{background:#451d24;color:var(--bad)}} .review{{background:#46351a;color:var(--review)}}
.table-wrap{{overflow:auto;border:1px solid var(--line);border-radius:8px}} .table-filter{{width:min(100%,420px);margin:0 0 8px;padding:8px 10px;color:var(--ink);background:var(--panel);border:1px solid var(--line);border-radius:6px}} table{{width:100%;border-collapse:collapse;background:var(--panel)}} th,td{{padding:9px 11px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}} th{{position:sticky;top:0;background:#1a2130;color:var(--accent)}} tr:hover td{{background:#171e2a}}
a{{color:#81b8ff}} code{{color:#d7e3f6}} details{{margin:12px 0}} .warning{{border-left:4px solid var(--review);padding:10px 14px;background:#1d1911}}
</style></head><body><main>
<p class="muted">TRIGGER DISCIPLINE / ENGINEERING INSTRUMENTATION</p><h1>v0.1 balance baseline</h1>
<p class="lede">Deterministic rules evidence, exact finite probes, paired mirrored Monte Carlo, progression, structural specialty diagnostics, and replay provenance. This report does not promote any value into player canon.</p>
<div class="grid">
<div class="card"><div class="muted">Invariant status</div><div class="metric">{sum(result['invariants'].values())}/{len(result['invariants'])}</div></div>
<div class="card"><div class="muted">Paired mirrored runs</div><div class="metric">{mc['paired_mirrored_runs']:,}</div><div>{_badge(mc['qualification'])}</div></div>
<div class="card"><div class="muted">4v4 control pairs</div><div class="metric">{team_mc['paired_mirrored_runs']:,}</div><div>{_badge(team_mc['qualification'])}</div></div>
<div class="card"><div class="muted">Mirror outcome share</div><div class="metric">{mc['subject_outcome_share']['estimate']:.3f}</div><div>95% CI {mc['subject_outcome_share']['low']:.3f}–{mc['subject_outcome_share']['high']:.3f}</div></div>
<div class="card"><div class="muted">Specialty nodes</div><div class="metric">{result['specialty_coverage']['node_count']}</div><div>{result['specialty_coverage']['specialty_count']} directions</div></div>
<div class="card"><div class="muted">Executable/counterfactual</div><div class="metric">{coverage_counts.get('IMPLEMENTED',0)+coverage_counts.get('IMPLEMENTED_WITH_PARAMETERS',0)} / {len(result['specialty_coverage']['counterfactuals'])}</div></div>
<div class="card"><div class="muted">Formal findings</div><div class="metric">{len(result['findings'])}</div></div>
</div>
<h2>Canonical correctness and invariants</h2>{_table(['Invariant','Status'], invariant_rows)}
<p class="muted">Repository commit <code>{escape(result['provenance']['repository_commit'])}</code> · encoded source checkpoint <code>{escape(result['provenance']['encoded_source_commit'])}</code> · parameter hash <code>{escape(result['parameters']['hash'])}</code> · seed <code>{result['run_configuration']['master_seed']}</code>.</p>
<h2>Artifacts and replays</h2><ul>{''.join(f'<li><a href="{href}">{escape(label)}</a></li>' for label,href in artifacts)}</ul>
<h2>Scenario coverage</h2>{_table(['Scenario','Family','Map','State','Unsupported dependency'],scenario_rows)}
<h2>Parameter movement</h2><p class="muted">Baseline is the unoptimized v0.1 seed set.</p>{_table(['Parameter','Seed','Active'],parameter_rows)}
<h2>Class baselines and progression</h2>{_table(['Level','Class','Primary metric','Control','Class baseline','Relative delta'],class_rows)}
<h3>Mirrored progression sweep</h3>{_table(['Level','Outcome share','95% CI','Terminal seconds','Qualification'],progression_rows)}
  <h2>Weapons, range, protection and hit quality</h2>{_table(['Weapon','Armor','Range m','Hit rate','Expected RES damage','Expected HP damage','Range difficulty'],fire_summary)}
  <p class="muted">The table samples close, ordinary, and long-range anchors for every weapon and armor fixture. Raw exact files retain every d20 outcome and HIT/CLEAN/DECISIVE/CRITICAL distribution.</p>
  <h3>Armor survivability distributions</h3>{_table(['Level','Armor','Incoming weapon','Armor RES','Burden','Movement m/AP','Expected shots to DOWNED','Expected AP to DOWNED'],armor_rows)}
  <h3>Firing-mode, recoil and ammunition tradeoffs</h3>{_table(['Weapon','Mode','Rounds','Expected connections','Expected recoil','Expected AP','Connections / round'],automatic_fire_rows)}
  <h2>COVER, SUPPRESSION, OVERWATCH, CQC, explosives and medicine</h2>
  <h3>COVER routing</h3>{_table(['Exposure','Resolved route','Exact totals'],cover_rows)}
  <h3>SUPPRESSION exposure</h3>{_table(['Committed rounds','Exposure capacity','Setup AP','First exposure attacks','Protected damage'],suppression_rows)}
  <h3>OVERWATCH crossing</h3>{_table(['Arc degrees','Setup AP','Shot allowance','Trigger position','Movement pauses','Hit rate'],overwatch_rows)}
  <h3>CQC exact comparisons</h3>{_table(['Maneuver','Attacker skill','Defender skill','Defense','Success rate','AP'],cqc_rows)}
  <h3>Explosive frozen-snapshot results</h3>{_table(['Material','Geometry','Cover HP after','Cover active','Frozen components'],explosive_rows)}
  <h3>Medical results</h3>{_table(['Level','Casualty','Action','Actor','Success rate','AP','Deterioration seconds','Reversible window'],medical_rows)}
<h2>Mixed-team objectives</h2>{_table(['Scenario','Status','Qualification','Outcome share','95% CI','Terminal seconds'],objective_rows)}
<h3>TEAM-CONTROL policy variants</h3>{_table(['Variant','Policies','Pairs','Outcome share','Terminal seconds','Qualification'],team_variant_rows)}
<h3>TEAM-CONTROL aggregate measurements</h3>{_table(['Metric','Subject mean','Control mean','Paired effect','Effect 95% CI'],team_metric_rows)}
  <h2>All 26 specialties / node-level coverage</h2><p class="warning">Tertiary approval permits testing, not invention. Missing admitted magnitudes remain gaps; explicit UNRESOLVED nodes stay unresolved.</p>{_table(['Class','Specialty','Tier','Node','Coverage','Executable mechanic','Finding'],specialty_rows)}
  <h3>Specialty objective execution</h3><p class="muted">Each designated Level 1/5/10/15/20 build runs PRIMARY, SECONDARY, and OFF_ROLE through the team-objective runner. Outcome and resource deltas come only from that runner. Structural node vectors are recorded separately; where the objective runtime did not consume them, the report records a formal integration gap.</p>{_table(['Specialty','Level','Role','Build ID','Map','Objective context','Actual runner','Completed','Structurally executed nodes','Objective/resource deltas','Generic dominance'],specialty_objective_rows)}
  <h3>Structural specialty counterfactual measurements</h3><p class="muted">These records prove prerequisite, branch, effect, and limitation execution. They do not claim objective completion or generic-dominance measurement.</p>{_table(['Specialty','Node ID','Level','Context map','Context objective','Control → candidate measurement'],specialty_effect_rows)}
<h2>Findings</h2>{_table(['Category','Finding code','Count'],finding_rows)}
<details><summary>Full machine finding records</summary><pre>{escape(json.dumps(result['findings'],indent=2,sort_keys=True))}</pre></details>
</main><script>document.querySelectorAll('.filter-table').forEach(function(box){{const input=box.querySelector('.table-filter');const rows=box.querySelectorAll('tbody tr');input.addEventListener('input',function(){{const query=input.value.toLowerCase();rows.forEach(function(row){{row.hidden=!row.textContent.toLowerCase().includes(query)}})}})}});</script></body></html>"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
