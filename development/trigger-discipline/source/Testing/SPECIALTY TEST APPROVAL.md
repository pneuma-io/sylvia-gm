---
type: testing-approval
domain: balance-testing
status: locked
player_canon: false
decision_date: 2026-08-24
tags: [type/testing-approval, domain/balance-testing, status/locked]
---
# SPECIALTY TEST APPROVAL

**At a glance:** The creator approves the current detailed specialty trees at `progression_status: tertiary` for immediate Codex implementation and automated testing. This is permission to test the working designs, not promotion to final player-facing progression canon.

> [!note]- Approval boundary
> - All 26 current specialty directions are in scope for the automated balance/structural test suite now.
> - Their current tree topology and written behavior are the test subject. Codex must not redesign them merely to make tests pass.
> - `progression_status: tertiary` means **approved working progression**: good enough to implement where mechanically executable, exercise, compare, and collect evidence against.
> - This decision removes the previous requirement that a final human HTML specialty audit must be completed before the serious suite can be built.
> - The suite is expected to catch specialty defects as well as numeric imbalance: dead nodes, permission perks, duplicated baseline behavior, invalid dependencies, ambiguous timing, unsupported subsystems, dominance, and missing measurable deltas are legitimate findings.
> - An explicit `UNRESOLVED` slot remains unresolved. Codex must represent it as unsupported/known-gap coverage and continue testing the rest of the tree. It must not invent a replacement ability.
> - A non-`UNRESOLVED` node that cannot be implemented without choosing between materially different interpretations must be reported as `AMBIGUOUS NODE` or `UNIMPLEMENTABLE SPEC GAP`; Codex must not pick a new design silently.
> - Where a specialty node is mechanically clear but its exact numeric magnitude is not admitted to [[BALANCE PARAMETERS]], the suite may implement the structural behavior and parameter interface, but optimization of that missing magnitude remains blocked until it has a stable parameter ID/seed/range.
> - The existing specialty magnitude language in [[BALANCE PARAMETERS]] remains conservative for **numeric optimization and promotion evidence**, but it is no longer a gate on suite construction, structural coverage, baseline simulation, or defect reporting.
> - A later human pass may repair tertiary findings using the suite's replayable evidence. Repair is not required before the first Codex handoff.
> - Nothing in this approval changes `status: locked` core mechanics, class/specialty identities, the player-canon boundary, or Codex's non-redesign restrictions in [[BALANCE CONTRACT]].

## Handoff consequence

The pre-Codex design/prep path is now complete enough to begin implementation:

`core rules locked -> HIT QUALITY/CRITICAL locked -> TRIGGER ORDERING locked -> benchmark fixtures -> reference equipment -> BALANCE CONTRACT / PARAMETERS / SCENARIOS -> tertiary specialty test approval -> CODEX BALANCE SUITE HANDOFF`

The next owner is the Codex implementation task described by [[CODEX BALANCE SUITE HANDOFF]].

## Connections
[[BALANCE CONTRACT]] · [[BALANCE PARAMETERS]] · [[BALANCE SCENARIOS]] · [[BENCHMARK FIXTURES]] · [[SPECIALTY]] · [[CODEX BALANCE SUITE HANDOFF]]
