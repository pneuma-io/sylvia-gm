---
type: implementation-documentation
domain: balance-testing
status: provisional
player_canon: false
tags: [type/implementation-documentation, domain/balance-testing, status/provisional]
---
# BALANCE SUITE IMPLEMENTATION

**At a glance:** Local Python implementation of the locked [[CODEX BALANCE SUITE HANDOFF]]. It executes and measures canonical mechanics, records source provenance, and writes ignored artifacts under `balance-results/`; it is not a second rules source.

## Runtime boundary

- `balance_suite/` contains typed state, executable rule adapters, geometry, deterministic events/RNG, fixtures, scenarios, policies, probes, specialty coverage, replay, optimization, regression, and reporting.
- The event engine records choices, procedure stages, RNG substreams, pre/post state hashes, trigger windows, and rejected actions. Duel, grid-combat, casualty-extraction, breach, and recon/network objectives all use replayable state transitions.
- Specialty execution uses a frozen, audited per-node parameter manifest. Runtime code does not infer tunable IDs or magnitudes from specialty prose.
- `tests/balance_suite/` tests the instrumentation and canonical invariants.
- Canonical Markdown and locked Testing inputs are read-only. Every CLI command runs inside a before/after protected-path fingerprint guard.
- Executable modules identify their canonical source paths and the encoded source checkpoint. `validate` reports source drift for review.
- The implementation uses the Python standard library and requires no service, database, model call, or network access.

## Commands

Run commands from the repository root:

```powershell
python -m balance_suite validate
python -m balance_suite coverage --output balance-results/coverage.json
python -m balance_suite exact --output balance-results/exact.json
python -m balance_suite baseline --runs 50000 --progression-runs 50000 --team-runs 50000 --workers 16 --seed 20260824 --output balance-results/specialty-integration-audit/final-qualified-50k
python -m balance_suite simulate --scenario MIRROR-DUEL-V1 --runs 10000 --seed 20260824 --output balance-results/simulation.json
python -m balance_suite simulate --scenario TEAM-CONTROL-4V4-V1 --runs 10000 --level 10 --classes-a BULWARK,RIFLEMAN,GHOST,CORPSMAN --classes-b BULWARK,RIFLEMAN,GHOST,CORPSMAN --policies-a POLICY_OBJECTIVE,POLICY_FIRE_DISCIPLINE,POLICY_FLANK,POLICY_SUPPORT --policies-b POLICY_OBJECTIVE,POLICY_FIRE_DISCIPLINE,POLICY_FLANK,POLICY_SUPPORT --output balance-results/team-control.json
python -m balance_suite simulate --family FIRE-SINGLE-RANGE --level 10 --output balance-results/fire-single-range.json
python -m balance_suite replay --artifact balance-results/v0.1/replays/sample-duel.json --output balance-results/v0.1/replay-check.json
python -m balance_suite replay --artifact balance-results/v0.1/replays/sample-team-control.json --output balance-results/v0.1/team-replay-check.json
python -m balance_suite optimize --parameter hit_quality.clean_margin --output balance-results/optimization.json --parameter-output balance-results/optimized-parameter-set.json
python -m balance_suite optimize --objective-scenario TEAM-EXTRACT-4V4-V1 --parameter casualty.carry.lift_ap --parameter casualty.carry.rate_multiplier --parameter medicine.stabilize_ap --pairs 100 --seed 20260825 --output balance-results/extract-search.json
python -m balance_suite optimize --objective-scenario TEAM-BREACH-4V4-V1 --parameter class.breacher.close_move_delta_m_per_ap --parameter class.breacher.close_envelope_m --parameter equipment.shotgun.damage --pairs 100 --seed 20260825 --output balance-results/breach-search.json
python -m balance_suite baseline --runs 100 --progression-runs 100 --team-runs 100 --seed 20260825 --objective-result balance-results/extract-qualified.json --objective-result balance-results/breach-qualified.json --output balance-results/objective-report
python -m balance_suite regress --accepted balance-results/accepted/baseline.json --current balance-results/v0.1/baseline.json --output balance-results/regression.json
python -m balance_suite report --input balance-results/v0.1/baseline.json --output balance-results/v0.1/report-regenerated.html
```

Developer smoke runs may use smaller `--runs` values. Output labels those results `SMOKE_NON_QUALIFYING`; ordinary comparison qualification begins at 10,000 paired/mirrored runs and final qualification at 50,000.

Objective optimization uses the same manifest-only candidate interface and common paired/mirrored seeds. Its broad exclusive `(0.05, 0.95)` outcome interval is an automaticity review only: it detects confidently near-certain or near-impossible fixtures and is not a target win rate. A result is flagged only when the complete 95% confidence interval remains in one automaticity tail. No asymmetric objective is pushed toward 50/50.

The baseline command can reuse an already-generated objective result to avoid rerunning an identical expensive qualification. Reuse validates scenario identity, parameter-set hash, master seed, and the presence of a deterministic sample replay. It rejects mismatched or duplicate artifacts and records every override ID and per-scenario run count in the new baseline provenance.

Run the suite self-tests with:

```powershell
python -m unittest discover -s tests -t . -v
```

## Generated artifacts

A baseline output directory contains:

- `baseline.json` — aggregate result and complete run provenance;
- `exact-results.json` and `monte-carlo-results.json`;
- `specialty-coverage.json`, `specialty-coverage.csv`, and `specialty-scenarios.csv`;
- `findings.json`;
- `parameter-set.json`, `parameter-metadata.json`, and `provenance.json`;
- `check-curves.csv`, `fire-range.csv`, `class-progression.csv`, `team-objectives.csv`, and `scenario-execution.csv`;
- `scenario-results.json` with an execution or formal-block record for every registered scenario;
- `replays/sample-duel.json`, both TEAM-CONTROL policy replays, and TEAM-EXTRACT, TEAM-BREACH, and TEAM-RECON-HACK replays;
- `report.html` — self-contained local dashboard linking the machine artifacts.

Artifacts are ignored by Git. Filenames are convenience only; identities and replay data are embedded in each result.

## Honest coverage boundary

The specialty adapter inventories Entry, Tier 1–4, and Keystone nodes directly from all 26 canonical specialty notes. It accounts for 364 nodes, emits one handoff coverage state for every node, and creates counterfactuals only for deterministic executable deltas. The current audit has 364 executable structural counterfactuals and 3,900 node/level/role records across PRIMARY, SECONDARY, and OFF_ROLE uses. Those records prove prerequisite, branch, effect, limitation, and role-gating behavior. A separate 390-record objective matrix gives every specialty a stable designated build at Levels 1/5/10/15/20 and carries each build through PRIMARY, SECONDARY, and OFF_ROLE TEAM-CONTROL, TEAM-EXTRACT, TEAM-BREACH, or TEAM-RECON-HACK objectives. Each record retains the build in actor state, terminal outcome, objective/resource metrics, event and state hashes, replay specification, and the separate structural node vector.

Current node coverage is 69 `IMPLEMENTED` and 295 `IMPLEMENTED_WITH_PARAMETERS`. NETWORKER `FOOTHOLD` has a distinct durable network delta and live package hook in addition to its counterfactual. No node remains dead, permission-only, explicitly `UNRESOLVED`, ambiguous, or dependency-blocked. Every designated PRIMARY and SECONDARY build now produces an objective-runtime effect through its assigned scenario, while the same OFF_ROLE build remains present and correctly produces none. Structural vectors are still kept separate from objective and resource deltas.

FIELD ENGINEER `POWER BUDGET` now uses the connected equal-load FIELD SENSOR / RELAY NODE pair, conserving one-for-one runtime with a 2 AP / 300-second seed exchange. TRAUMA `SALVAGE` now uses `INJURY_REPAIRABLE_FOREARM_TRAUMA_V1`, reducing its eventual persistent DIFFICULTY from +4 to +2 exactly once while restoring no immediate HP.

All 35 declared scenarios now execute. This includes partial/destructible cover, grenade placement/scatter, casualty movement, TEAM-EXTRACT, TEAM-BREACH, TEAM-RECON-HACK, and armed-drone combat. The network scenario uses authored stable NODE/LINK fixture IDs anchored to physical terminal positions; intrusion AP is measured as opportunity cost alongside combat AP. All eight TEAM-CONTROL policy families are exercised across two composition variants.

## Verification boundary

The suite distinguishes three kinds of result so design defects cannot be mistaken for implementation defects:

- `STRUCTURAL_SPEC` findings identify missing, contradictory, ambiguous, duplicated, dead, permission-only, or explicitly unresolved canonical mechanics;
- `NUMERIC_BALANCE` findings identify measured dominance or boundary pressure in mechanics that are executable;
- `INVARIANT_RUNTIME` findings identify simulator, replay, provenance, or execution failures.

Every declared scenario and every specialty structural counterfactual is mechanically executable. PRIMARY, SECONDARY, and OFF_ROLE builds run through actual objective runners, but build presence alone is not evidence that a node affected the objective. Only changes produced by the objective runtime count as objective/resource deltas or generic-dominance evidence. The final 390-record matrix contains 260 intended-role effects and 130 correctly gated OFF_ROLE cases, with no integration finding. Remaining bounded package GM-judgment areas are recorded separately in [[SYLVIA RULESET IMPLEMENTATION]].

## v0.1 qualifying verification

The pre-fixture-resolution seed `20260824` baseline at result ID `TD-v0.1-b26d8edd842b-20260824` completed with 10,000 paired/mirrored duel runs, 10,000 pairs at each Level 1/5/10/15/20 snapshot, 10,000 pairs for each of the two TEAM-CONTROL policy/composition variants, and 10,000 pairs for each of TEAM-EXTRACT, TEAM-BREACH, and TEAM-RECON-HACK. This is `ORDINARY` qualification under the handoff. All eleven baseline invariants passed, and all 34 registered scenarios executed.

All six representative deterministic replays matched winner, event count, event-log hash, and final-state hash. The optimizer inspected five legal candidates, made zero non-manifest mutations, rejected fixed and unknown IDs, and selected the unchanged seed value. Self-regression compared 35,794 numeric metrics with zero changes. The full local suite passed 98 tests. The regenerated HTML report was inspected through a local browser: layout, eleven table filters, 21 artifact links, and an error-free console all passed.

That earlier baseline emitted the two now-resolved fixture findings, one numeric-balance review finding, and zero invariant/runtime findings. At that checkpoint, the numeric finding was the Level-1 `TRAINING_TALENT_CONFLICT`; it has since been resolved by the approved benchmark fixture described in the current qualification below. It emitted no `GENERAL DOMINANCE` or `BOUNDARY PRESSURE` finding. The original qualification captured TEAM-EXTRACT 0.9997, TEAM-RECON-HACK 0.7760, and TEAM-BREACH 0.1255 under the v0.1 seeds; the extraction and breach measurements are superseded by the corrected objective-runtime qualification below.

## Objective runtime correction and balance pass

The 2026-08-25 objective pass corrected measurement-layer defects without changing canonical player mechanics or promoting any parameter value:

- every capable actor now spends its full legal AP across an interleaved TEAM TURN instead of support/defender actors receiving only one action;
- objective actors follow their declared `POLICY_*` role, with separate objective, support, pressure, flank, cover-first, and fire behavior;
- extraction handoff remains with declared `POLICY_SUPPORT` / `POLICY_OBJECTIVE` rescuers rather than silently converting a fire-discipline actor into another carrier after both objective actors are DOWNED;
- the extraction opposition is an explicit repository fixture at `(19,20)`, `(19,24)`, `(19,28)`, and `(19,32)`, forming a forward screen on the evacuation route instead of an uncontestable distant firing line;
- the breach objective role is assigned to the durable BULWARK while the RIFLEMAN supplies disciplined fire;
- BREACHER CLOSE ASSAULT movement receives its delta only when the enemy position is actually known and the resolved path genuinely closes distance;
- objective movement now records physical exposure distance;
- optimizer results retain their complete deterministic best-candidate replay, while the console prints only a compact summary.

Transparent common-seed searches evaluated 24 extraction candidates across `casualty.carry.lift_ap`, `casualty.carry.rate_multiplier`, and `medicine.stabilize_ap`, plus 225 breach candidates across `class.breacher.close_move_delta_m_per_ap`, `class.breacher.close_envelope_m`, and `equipment.shotgun.damage`. Both searches selected the unchanged v0.1 seed with zero normalized movement and no `GENERAL DOMINANCE`, `BOUNDARY PRESSURE`, automaticity, invariant, or runtime finding. No parameter promotion is requested.

Ordinary qualification used master seed `20260825` and 10,000 paired/mirrored runs (20,000 trials) per objective:

- TEAM-EXTRACT: `0.9293`, 95% CI `0.9257..0.9328`, mean terminal time `22.7856` seconds;
- TEAM-BREACH: `0.0796`, 95% CI `0.0759..0.0834`, mean terminal time `136.5288` seconds.

Both objective sample replays matched winner, event count, event-log hash, and final-state hash exactly. At that pre-resolution checkpoint, the regenerated report bundle passed all eleven invariants and retained 81 `IMPLEMENTED`, 281 `IMPLEMENTED_WITH_PARAMETERS`, 2 `MISSING_DEPENDENCY`, and three formal findings. Browser verification confirmed the report layout, objective values, eleven working table filters, all 21 artifact links, and an error-free console.

Ignored evidence is generated under `balance-results/objective-balance-pass/`: `extract-qualified.json`, `breach-qualified.json`, their replay checks, both complete search histories/best parameter sets, and `report-bundle/report.html` with its linked machine-readable artifacts. The report bundle reuses the two ordinary-qualified objective results and freshly evaluates unaffected report sections at smoke scale; their individual qualification labels remain explicit.

## Specialty fixture resolution pass

The 2026-08-26 human decisions for POWER BUDGET and SALVAGE are recorded in the canonical specialty notes and their test-only fixtures. That checkpoint used 607 manifest parameters with seed hash `aaae1f1c701a3575580273d421fd548565e35bc5af9f146101136b7334ff6bfd`, reported 80 `IMPLEMENTED`, 283 `IMPLEMENTED_WITH_PARAMETERS`, and one `STRUCTURAL_FINDING`, with zero specialty dependency findings. Focused mechanics tests prove conservation, physical compatibility, connection loss, no output bonus, one-step persistent injury reduction, non-stacking, no immediate HP, and the irreparable-anatomy limit.

A complete 100-pair smoke baseline at master seed `20260826` executed all 34 scenarios and passed all eleven invariants. Its six saved replays reproduced exact event counts, event-log hashes, final-state hashes, and winners. That artifact predates the subsequent NETWORKER `FOOTHOLD` baseline-duplication audit and therefore emitted only the then-existing Level-1 `TRAINING_TALENT_CONFLICT` review finding. A constrained search over POWER BUDGET action AP retained the unchanged 2 AP seed with no numeric finding. The later FOOTHOLD structural finding remained visible until its approved mechanical delta was implemented.

## Optimization safety

The optimizer receives candidates only through the validated manifest interface. Unknown, missing, fixed, review-only, out-of-range, wrong-type, and coupling-breaking updates are rejected. Search history is retained, bounds never widen automatically, and structural failure cannot trigger canonical writes or topology/procedure changes.

## Specialty-integration closure qualification

The current approved baseline is result `TD-v0.1-2172a26f7282-20260824`, using master seed `20260824` and parameter-set SHA-256 `2172a26f7282a59b09c720b66692aa6913ad943107c92c44ff551083962af747`. It ran 50,000 paired/mirrored duel executions, 50,000 pairs at each Level 1/5/10/15/20 snapshot, 50,000 pairs for each TEAM-CONTROL policy variant, and 50,000 pairs for TEAM-EXTRACT, TEAM-BREACH, and TEAM-RECON-HACK across 16 deterministic worker processes. Stochastic comparison outputs are `FINAL` qualification; closed-form probes remain `EXACT`.

The approved benchmark fixture resolves the former Level-1 control conflict: Level 1 TRAINING uses SKILL 5 / APTITUDE 0, while Levels 5/10/15/20 use the specialist's primary skill / APTITUDE 0. This makes the Level-1 training, talent, and specialist standard-check controls all 60% without changing the canonical additive CHECK rule.

The completed baseline records suite commit `c9ccc0c38c68f2d5e633a2f83dd3e683ab8a4a00` and canonical source checkpoint `1b5b500ebd931c65392c6d8ce9794a38dd48ff7e`. It executes 35 declared scenarios, all 26 specialties, all 364 specialty nodes, 364 same-character structural counterfactuals, and 390 specialty/objective build records. Coverage classifies 69 nodes as `IMPLEMENTED` and 295 as `IMPLEMENTED_WITH_PARAMETERS`. All twelve invariants pass, with no source drift or scenario error.

The balance verdict is `READY`, with zero structural, numeric-balance, invariant-runtime, `GENERAL DOMINANCE`, or `BOUNDARY PRESSURE` findings. All 130 PRIMARY and all 130 SECONDARY objective records produce their intended runtime effect; all 130 OFF_ROLE records retain the designated build while suppressing its role-dependent effect. The three asymmetric reference objectives remain deliberately unequal rather than being tuned toward 50/50: TEAM-BREACH records 0.08310 subject outcome share, TEAM-EXTRACT 0.92952, and TEAM-RECON-HACK 0.91678. None has a confidently automatic 95% interval under the suite's declared review boundary.

All six representative replays reproduce their event counts, event-log hashes, final-state hashes, and winners. The broad self-regression comparison shares 806,551 numeric metrics with zero changes. The exact-probability run completes 20 probe families. The constrained five-candidate search keeps the unchanged seed value and parameter hash, reports zero normalized movement, and cannot write outside the 597-value manifest. The baseline report and an independent regeneration are byte-identical at SHA-256 `ecf4059ec570dea08e9bb80d32948736886aa9996ca9ab77ac8fecf370dea6f5`. The final repository run passes 200 tests in 260.826 seconds.

Ignored local evidence is under `balance-results/specialty-integration-audit/final-qualified-50k/`: the qualifying machine/HTML bundle, six replay-check JSON files, self-regression, exact and coverage results, validation result, and optimizer search/parameter artifacts. The tracked Sylvia package qualification is under `ruleset-draft/output/`. The first-handoff Definition of Done is fully satisfied without changing canonical mechanics to obtain a passing result.

## Connections

[[CODEX BALANCE SUITE HANDOFF]] · [[BALANCE CONTRACT]] · [[BALANCE PARAMETERS]] · [[BALANCE SCENARIOS]] · [[SPECIALTY TEST APPROVAL]]
