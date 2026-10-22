---
type: testing-contract
domain: balance-testing
status: locked
player_canon: false
tags: [type/testing-contract, domain/balance-testing, status/locked]
---
# BALANCE CONTRACT

**At a glance:** This contract defines the authority boundary for automated TRIGGER DISCIPLINE balance work. The balance suite may tune only explicitly declared numeric parameters, must preserve the canonical mechanics and specialty identities, and must report structural design conflicts instead of changing the game to make metrics pass.

> [!note]- Full contract
> ## 1. Authority and purpose
> - Canonical Markdown notes remain the rules source. This contract does not restate or replace them.
> - [[BALANCE PARAMETERS]] is the complete initial manifest of values the automated suite is permitted to vary. A number, formula coefficient, threshold, vector, or profile that is not listed there is read-only even if changing it would improve a metric.
> - [[BALANCE SCENARIOS]] defines the required scenario families, controls, measurements, and acceptance checks.
> - [[BENCHMARK FIXTURES]] supplies standardized Level 1 / 5 / 10 / 15 / 20 characters and controls. Their SKILL/APTITUDE ladder and named fixture identities are measurement equipment, not optimization targets.
> - [[REFERENCE EQUIPMENT]] defines the locked representative equipment families/schema. [[REFERENCE EQUIPMENT VALUES]] supplies the current v0.1 numerical seed values.
> - [[SPECIALTY TEST APPROVAL]] records the creator's current decision that all present `progression_status: tertiary` specialty trees are approved as test subjects for the Codex suite without a blocking final human rewrite pass.
> - The balance suite exists to discover usable numeric tuning and structural problems inside the accepted game design. It is not an autonomous game designer.
>
> ## 2. What Codex may tune
> Codex may:
> - vary only parameter IDs explicitly listed as `tunable` in [[BALANCE PARAMETERS]];
> - search permitted integer, discrete, or continuous ranges and coupled parameter sets;
> - tune a specialty's explicit numeric magnitude only after that magnitude has a stable admitted parameter ID; tertiary approval alone does not authorize Codex to invent a missing number;
> - implement and exercise current tertiary specialty behavior exactly where the canonical text is executable enough to do so;
> - compare alternate values using deterministic tests, exhaustive finite sweeps where practical, and reproducible Monte Carlo simulation;
> - recommend that a permitted range be widened when every tested value lands against a boundary, but it may not widen that range itself;
> - report that a current v0.1 seed is poor, dominated, unstable, or incompatible with another target;
> - report `DEAD NODE`, `AMBIGUOUS NODE`, `UNIMPLEMENTABLE SPEC GAP`, or `STRUCTURAL FAILURE` when the current tertiary specialty text cannot produce a valid executable/measurable delta;
> - report that no permitted parameter set satisfies the contract.
>
> ## 3. What Codex may never redesign
> Codex must not, merely to improve a balance metric:
> - add, remove, merge, rename, or reorder canonical mechanics, classes, specialty directions, specialty nodes, HIT QUALITY tiers, states, actions, trigger windows, or equipment fields;
> - change a `status: locked` rule's qualitative procedure, prerequisites, state transitions, physical interpretation, ownership, or action permissions;
> - replace the CHECK formula, change the benchmark SKILL/APTITUDE ladder, or make class membership grant an underlying SKILL unless canon explicitly says so;
> - collapse target PENETRATION and MATERIAL PENETRATION into one value, turn COVER into RES, convert SUPPRESSION into a fear/status mechanic, convert OVERWATCH into generic reaction fire, or otherwise substitute a simpler mechanic for a locked one;
> - invent a hidden quality score, universal class multiplier, encounter-level correction, rubber-banding rule, artificial damage floor/ceiling, or scenario-specific secret bonus to force results toward a target;
> - infer new equipment functions, access routes, social permissions, medical capabilities, drone capabilities, or physical interactions that the canonical source does not provide;
> - change player-facing canon status. Test fixtures and tuned candidates remain `player_canon: false` until the creator explicitly promotes values into the relevant canonical rules/equipment notes;
> - silently rewrite, fill, or reinterpret a specialty that fails its mechanical-delta test or contains an explicit `UNRESOLVED` slot.
>
> If a metric cannot be satisfied without one of those changes, the correct result is **STRUCTURAL FAILURE**, not a green test.
>
> ## 4. Tertiary specialty test approval
> The serious automated suite may be implemented now. A final human HTML specialty rewrite pass is **not** a precondition for implementation.
>
> Current specialty direction identities remain locked and their existing detailed trees remain `progression_status: tertiary`: approved working designs, not final player-facing progression canon.
>
> The suite must treat all 26 current specialty directions as coverage targets and apply [[SPECIALTY]]'s validity gate as an automated structural audit, especially:
> - BASELINE COUNTERFACTUAL;
> - MECHANICAL DELTA;
> - NO PERMISSION PERKS;
> - COMPETENT-NONSPECIALIST COMPARISON;
> - SPECIALTY IDENTITY;
> - NO INVENTED SUPPORTING SUBSYSTEM;
> - DEPENDENCY AUDIT.
>
> An explicit `UNRESOLVED` slot is a known test/design gap. It does **not** block building or running the suite. Codex must mark the node unsupported/unresolved, continue all unaffected coverage, and surface the gap in machine-readable and HTML reports. It must not invent the replacement ability.
>
> A non-`UNRESOLVED` node that cannot state `when it applies -> what actually changes -> what limitation remains`, cannot be implemented without guessing, or repeatedly produces no measurable delta should likewise be reported rather than silently repaired.
>
> Tertiary specialty findings are therefore an intended output of the suite. Human design repair may happen after the first automated evidence exists.
>
> ## 5. Simulation layers
> The implementation must keep these layers distinct so a failure can be diagnosed rather than averaged away.
>
> **Layer A — deterministic rule invariants**
> - State-transition, resource-conservation, geometry, trigger-order, projectile-routing, ammunition, and legality tests.
> - These have zero tolerated violations.
>
> **Layer B — exact probability/math probes**
> - Enumerate finite d20 outcome spaces where practical rather than Monte Carlo sampling them.
> - Verify success curves, HIT QUALITY frequencies, penetration routing, and other closed-form or finite distributions.
>
> **Layer C — atomic comparative simulations**
> - One controlled variable at a time: weapon vs weapon, armor vs armor, specialist vs competent nonspecialist, one specialty node present vs absent, one range band, one cover geometry, one movement burden, and similar isolated comparisons.
>
> **Layer D — tactical scenario simulations**
> - Multi-action encounters using the scenario policies in [[BALANCE SCENARIOS]].
> - Run mirrored starting sides, initiative/team-turn order, equivalent starting resources, and multiple legitimate tactical policies so one scripted policy cannot define the game's balance by itself.
>
> **Layer E — progression and regression sweeps**
> - Repeat designated comparisons at Levels 1 / 5 / 10 / 15 / 20.
> - Every accepted candidate parameter set becomes a reproducible regression baseline; future changes report deltas against it rather than replacing history.
>
> ## 6. Optimization objective order
> Optimization is lexicographic, not one blended score that can trade away correctness.
>
> 1. **Canonical correctness.** No rule-invariant violation is negotiable.
> 2. **Required directional behavior.** Class, specialty, weapon, protection, and scenario identities in [[BALANCE SCENARIOS]] must hold.
> 3. **No dominant general solution.** A weapon, class path, action loop, firing mode, or strategy must not solve materially unrelated scenarios when canon intends tradeoffs.
> 4. **Meaningful specialization.** Relevant specialization must produce a measurable advantage in its intended problem without becoming a permission gate for everyone else.
> 5. **Reasonable progression.** Later benchmark levels should gain options and role effectiveness without requiring enormous HP inflation or making lower-level physical rules irrelevant.
> 6. **Economy and usability.** AP, ammunition, movement, treatment, deployment, and other resource costs should create actual choices rather than automatic always/never actions.
> 7. **Minimum unnecessary movement from the seed.** If multiple parameter sets satisfy the higher priorities, prefer the one requiring the smallest normalized change from the current v0.1/test seeds.
>
> The optimizer must **not** treat universal 50/50 duel win rate as the objective. TRIGGER DISCIPLINE has asymmetric roles; balance means useful tradeoffs and scenario value, not identical combat output.
>
> ## 7. Statistical and reproducibility requirements
> - Every stochastic run has an explicit seed. A result report records the master seed, derived seed range, scenario version, parameter-set hash, and source commit.
> - Exact finite probability enumeration is preferred when possible.
> - Monte Carlo scenario comparisons use at least **10,000 paired/mirrored runs per candidate comparison** for ordinary tuning. Borderline acceptance results, rare-event tests, and final candidate qualification use at least **50,000** paired/mirrored runs or an exact calculation.
> - Report 95% confidence intervals for stochastic rates and effect-size uncertainty for continuous/resource metrics.
> - Use common random numbers / paired seeds for A/B parameter or build comparisons where possible so noise does not masquerade as an improvement.
> - Swap sides, starting TEAM TURN, symmetric map positions, and equivalent loadout placement where the scenario permits it.
> - Do not discard inconvenient seeds. Crashes, illegal states, infinite loops, deadlocks, and impossible continuations are test failures with replay data.
> - Simulations must run without an LLM in the decision or rules loop. Tactical policies may be deterministic/state-machine policies or seeded stochastic policies, but they must be inspectable and replayable.
>
> ## 8. Default acceptance / review bands
> These are balance-engineering gates, not player-facing rules.
>
> **Hard failure**
> - Any canonical invariant violation.
> - Any impossible resource creation, double-spend, illegal state transition, non-deterministic trigger-order result, projectile teleportation, or scenario result that requires a rule not present in canon.
> - Any optimizer mutation outside the manifest.
>
> **Mirror neutrality**
> - A truly symmetric fixture with mirrored side/order/position should converge to **50% outcome share within +/-2 percentage points** at final qualification, or have a 95% interval containing 50% with total width <=4 points.
> - A persistent larger advantage is a simulator/scenario bias until proven to arise from an intentionally asymmetric rule.
>
> **Meaningful mechanical delta**
> - In a scenario specifically designed to exercise an ability, a balance-candidate class baseline or specialty node should normally change at least one primary outcome/resource metric by **>=5% relative** or **>=5 percentage points absolute** versus the competent nonspecialist / same-build-without-node control.
> - A smaller effect is not automatically invalid when the node changes a rare but strategically decisive state; the report must identify that state explicitly instead of hiding behind an aggregate score.
> - A node that repeatedly produces no measurable delta in every designated scenario is a **DEAD NODE** design finding, not a signal to inflate unrelated numbers.
>
> **Cross-role dominance review**
> - If a class baseline, specialty node, item, or action improves primary outcomes by **>25% relative** across three or more materially unrelated scenario families, flag **GENERAL DOMINANCE** for review even when every individual scenario still passes.
> - This is a review flag rather than an automatic nerf because some high-tier/keystone effects may intentionally be strong.
>
> **Boundary pressure**
> - If an accepted optimum places a parameter at its min/max bound, or more than 30% of accepted top candidates cluster at that bound, flag **BOUNDARY PRESSURE**. Do not widen the range automatically.
>
> ## 9. Structural failure protocol
> A STRUCTURAL FAILURE report must contain:
> - the smallest reproducible set of scenarios/constraints that cannot be satisfied together;
> - the best parameter set found inside the permitted manifest and how it fails;
> - which parameter bounds were exhausted;
> - whether the conflict appears mathematical, procedural, topology/specialty-design, or fixture/scenario-related;
> - replay seeds or exact enumerations;
> - the smallest categories of human design change that *could* resolve it, clearly labelled as proposals only.
>
> Codex must stop there. It must not implement the proposed design change in the same balancing task.
>
> ## 10. Required implementation outputs
> The eventual Codex balance suite must provide at minimum:
> - a non-interactive CLI suitable for local repeated runs;
> - machine-readable parameter-set input/output;
> - deterministic replay of any scenario from scenario ID + fixture IDs + parameter hash + seed;
> - exact/Monte-Carlo test runners with selectable scenario families and benchmark levels;
> - a baseline command that evaluates the current v0.1 seeds without optimizing them;
> - a constrained search/optimization command that cannot mutate non-manifest values;
> - JSON results containing raw measurements and provenance;
> - CSV/Parquet-style tabular exports for large sweeps;
> - a human HTML report showing pass/fail/review flags, distributions, effect sizes, class/specialty/equipment comparisons, parameter movement, structural failures, unresolved/ambiguous specialty coverage, and dead-node findings;
> - regression comparison against a named accepted baseline;
> - tests proving the optimizer cannot write or alter canonical rules files.
>
> ## 11. Promotion boundary
> A statistically good parameter set is only a **balance candidate**.
>
> Promotion requires human acceptance. Until then:
> - no tuned value becomes player canon;
> - canonical notes are not rewritten by the balance tool;
> - generated result artifacts stay separate from the atomic rules corpus;
> - the accepted parameter set may be used as the next regression baseline without implying final publication balance.

## Connections
[[BALANCE PARAMETERS]] · [[BALANCE SCENARIOS]] · [[BENCHMARK FIXTURES]] · [[REFERENCE EQUIPMENT]] · [[REFERENCE EQUIPMENT VALUES]] · [[SPECIALTY TEST APPROVAL]] · [[SPECIALTY]] · [[CHECK]] · [[HIT QUALITY]] · [[CRITICAL HIT]] · [[AP]] · [[RES]] · [[HP]]
