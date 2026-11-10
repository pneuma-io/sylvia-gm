# Sylvia GM Alpha Completion Plan

> **Status:** Active planning and handoff authority
>
> **Updated:** 2026-11-11

This is the repository status surface synchronized with Dot Desk (formerly Project Desk).
The latest owner-selected work item determines scope. The product contract owns requirements,
the architecture contract owns mechanisms, and the development process owns working method.
Historical checkpoints select no new work.

The existing PR #6 current-work paragraphs are retained by that branch and must remain here when
the branches meet. Their desktop/native, merge and release qualifications continue to apply.

## 1. Current work

**9 November 2026: optional onboarding and the complete tutorial suite.** The owner selected a new
PR for all independently accessible lessons and a guided welcome, AI connection, Ready campaign
and first-scene journey. Dot Desk work item **Build the tutorial suite and current new-player
onboarding** records the approved scope. Implementation uses an isolated checkout and preserves
the separate Alpha build owner. The attract-mode showcase and no-AI battles remain future work.
Source/browser checks qualify this PR's UI behavior; genuine-provider, native installed-app,
visual approval, merge and release acceptance remain separate.

Sylvia GM has a public source repository and four published Alpha releases. The releases page
is the download destination; an older installed build does not verify newer source.

The approved Fable refactor preserves game behavior while improving local checks, removing unused
code, organizing command routing and the GM turn, and correcting stale documentation. It also
speeds the Ruleset inspector. Its private-content prerequisite removes Trigger Discipline vault
notes and Siren Protocol payloads from current public fixtures. Historical copies remain in Git
history and old release source archives; this work does not rewrite history.

| Work | Current boundary |
|---|---|
| Frontend overhaul, PR #28 | Merged on main `6526904` before PR #6. PR #6 owns its composed TD qualification; source/browser evidence does not by itself establish final installed acceptance. |
| Trigger Discipline integration, PR #6 | Separate active owner; draft head `882103e` includes main `6526904`; its Python/runtime bytes retain the `9968614` spatial typing repairs, with later caption and Network layout fixes committed. Public compiler source may remain; vault notes and Siren Protocol stay private. Privacy removal is complete. Public packages exclude canonical vault text; full public-session adequacy without the private prose/future Rulebook remains unproven. The dedicated VM is ready; host desktop control remains held. TD build 16 is in progress and native evidence remains on `9968614`, owned separately. The final combined refactor/hotfix candidate still needs rebuilding and its own selected native qualification. |
| Refactor, [PR #33](https://github.com/pneuma-io/sylvia-gm/pull/33) | Stages 1–8, including the approved stage 7A state-machine addendum, are selected. The `app.js` split waits for both PR #28 and PR #6 to merge and may ship as a second PR. Preserve their work and document collisions. |
| SRD Full, PR #3 | Parked, inactive work; it does not expand the 117-spell Lite launch roster. |
| GM memory and casting hotfix | Selected separately on 11 November: restore GM memory, remove disabled tool advertising and repair healing inputs/refusals with a new Lite package. Real-owner request regressions and final checked-source composition remain required. |
| SylviaBench | Outside this refactor. |

Tutorials/onboarding, desktop updates, local diagnostics and compatible campaign Ruleset updates
have implementation in the repository. Their existence does not approve a new release or a new
deployment. Ongoing Trigger Discipline work is no longer a blanket post-Alpha deferral.

For the removed historical checkpoints, use
`git show 39aac5168d7e95f55a444efae98df0db82474616:docs/plans/alpha-rebuild-readiness-plan.md`.

## 2. Milestone record

| Milestone | Status and limits |
|---|---|
| Desktop/owner, Builder, persistence and recovery | Implemented; changes still need affected checks and final-candidate acceptance. |
| SRD Lite and bundled campaigns | The supported roster remains exactly 117 spells without consumables. Preserve exact package pins, existing campaigns and the multi-ruleset architecture. Full remains inactive. |
| Original bundled Ruleset | Removed from Alpha requirements. |
| Initial public publication | Completed; the public repository and Alpha releases supersede the old “private/publication blocked” status. |
| Next release | Requires local combined-source checks and exact installer/portable acceptance. No open PR or historical milestone grants publication approval. |

## 3. Advertised Alpha journeys

Release work protects these outcomes:

1. Start a general Builder chat, converse, create content, Stop, steer, switch models,
   close, reopen and continue.
2. Save private provider configurations, discover or enter models, and connect a supported
   hosted or already-running local provider without exposing credentials.
3. Start a supported Ready campaign or The Rift Awakening, play with authoritative mechanics,
   save, close, reopen, back up and recover.
4. Start a No Ruleset campaign without invented dice, classes, HP or other unselected mechanics.
5. Install, launch, restart and uninstall the Windows application without orphan owners or
   renderer-owned authority. Protect installed/portable data selection and same-dataset refusal.
6. Present usable controls and truthful compatibility information without severe flicker or
   internal jargon in ordinary settings.

## 4. Local verification and release sequence

Checks run locally. The GitHub workflow is manual-only; pushing a branch or opening a PR does
not start GitHub Actions. A PR is ready for review when its description records the locally
checked head commit, commands, results, elapsed time and checks not run.

1. Use a branch for each selected change and run its affected checks locally. Commit and push
   the source; keep the PR description tied to the exact tested head.
2. Bring current `main` into the PR before merging. Resolve conflicts and rerun affected checks
   on the combined result. Tests of two separate branches do not qualify their combination.
3. Merge the selected PRs in the agreed order, then run the full deterministic gate on the
   resulting `main`. Include local Python lint/type checks and affected browser, packaging and
   Rust checks. Record failures and limitations explicitly; baseline failures are not new passes.
4. Build the installer and portable ZIP from that clean, checked commit. Bind source and artifact
   identities with the existing release recipe and required notices and identity checks.
5. Check the actual candidate: Ready campaign turns including dice, Stop/Retry, Builder
   continuation across restart, New Campaign, Models and Rulesets, detached panels and WebUI
   if used. Keep genuine-provider and native-input evidence separate from scripted fixtures.
6. Publish only after owner approval, using the checked artifacts. Later source changes require
   a new candidate and affected verification. Keep the preceding verified deliverable available.

Development checks have no whole-run wall-time cutoff. `--ci` is only the explicitly selected
future budget mode. Missing or failed required checks fail the gate. Known defects must be named,
with their own reproduction; they cannot be hidden as generic skips or called passing behavior.

The deterministic gate cannot establish real provider compatibility, native interaction, or
installer correctness on its own. Follow the
[development process](../operations/quality-preserving-development-process.md) for those checks.

## 5. Verification relevance fence

For a change, verify its owning boundary, representative direct consumers, and persistence,
privacy, packaging or installed behavior where affected. Completed specialist matrices do not
automatically direct new work. A repository-wide run is appropriate for this cross-cutting
refactor; it is not a standing requirement for every later small edit.

Desktop testing uses the environment selected for the current run. The old VM is retired.
Preserve current desktop holds. Historical evidence retains its original source, provider and
input limitations and cannot qualify changed bytes.

## 6. Follow-ups needing their own selection

- A persistent product Ruleset cache, after reviewing this refactor's measurements.
- A reproducible Lite package build and any specialization of unused Full spell machinery.
- New Campaign integrity-sweep timing and any change to that data-safety mechanism.
- Retirement of the older Builder agent loop; decomposition of the authority/store classes.
- Rust command wrappers and command-bus/supervisor changes, with a security review.
- Current provider catalog/reasoning tables and possible fallback paths.
- Additional native platforms, Windows 10, subscriptions, paid signing, cloud play, marketplace,
  executable plugins, and features listed as planned in [proposals](../../proposals/README.md).

Siren Protocol remains private. Trigger Discipline public gameplay limits belong to its own
integration work item. Neither selects additional mechanics in this refactor.

## 7. Repository and release boundary

Exactly one worker writes a checkout; keep work items isolated and preserve unrelated edits.
Dot Desk holds current work items and decisions, GitHub holds project source, and the Pneuma IO
Rulebook governs delivery, reporting, cleanup and merge order. The current human task supplies
side-effect authority; this plan is not standing permission to publish, spend, access private
campaigns, rewrite history or perform destructive data operations.
