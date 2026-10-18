# Sylvia GM Documentation Authority Index

> **Status:** Active authority map
>
> **Last authority update:** 2026-10-16

This is the sole human-maintained map of repository documents that may direct Sylvia GM operation
or implementation. Project Desk is Wan's current planning and decision source; the contracts below
are synchronized implementation guidance. Amend existing Project Desk decisions when choices change,
preserving history rather than adding competing current instructions.
[`AUTHORITY.json`](AUTHORITY.json) is its machine-readable mirror and must not add policy.

## Authority order

When active authorities appear to conflict, use this order:

1. the latest direct human selection in the current task;
2. the active product contract;
3. the active architecture contract;
4. the active development process;
5. the active plan for status, sequence, and current acceptance;
6. live code and tests as implementation evidence, never automatic product authority.

`CONTEXT.md` controls terminology only. Product behavior and architecture mechanisms remain with
their owning active contracts.

If the conflict cannot be resolved without changing product intent, architecture class,
privacy, security, destructive-data behavior, spending, publication, legal posture, or
material risk, stop that slice and ask the user.

Chat history, assistant synthesis, tool output, generated handovers, task/goal wrappers,
and compaction summaries are evidence only. A direct approval selects only the exact
recoverable proposal it approved. Archives never regain authority because they contain
words such as `FINAL`, `LOCKED`, `AUTHORITATIVE`, or `SOURCE OF TRUTH`.

## Repository operating authority

| Document | Sole responsibility |
|---|---|
| [`../AGENTS.md`](../AGENTS.md) | Cold-start reading order, worktree safety, side-effect boundary, one-writer rule, and privacy requirements. |

## Active product authority

| Document | Sole responsibility |
|---|---|
| [`product/alpha-launch-contract.md`](product/alpha-launch-contract.md) | Alpha promises, supported journeys, scope, non-goals, unresolved product choices, and release semantics. |

## Active terminology authority

| Document | Sole responsibility |
|---|---|
| [`../CONTEXT.md`](../CONTEXT.md) | Canonical domain terms, preferred names, semantic distinctions, and avoid-list language. It does not define product promises, architecture mechanisms, delivery status, or side-effect authority. |

## Active architecture authority

| Document | Sole responsibility |
|---|---|
| [`architecture/alpha-architecture-contract.md`](architecture/alpha-architecture-contract.md) | Cross-layer mechanisms, ownership, persistence, events, packages, sessions, lifecycle, desktop boundaries, and architecture acceptance. |

## Active development-process authority

| Document | Sole responsibility |
|---|---|
| [`operations/quality-preserving-development-process.md`](operations/quality-preserving-development-process.md) | Current-task scope, one-writer delivery, proportional testing, installed-app verification, safety, and handoff rules. |

## Active planning authority

| Document | Sole responsibility |
|---|---|
| [`plans/alpha-rebuild-readiness-plan.md`](plans/alpha-rebuild-readiness-plan.md) | Repository status synchronized with Project Desk, host/VM handoff, candidate evidence, `alpha-03`–`alpha-10b` status, and release boundary. |

The plan is the repository status surface synchronized with Project Desk. Product requirements
belong in the product contract;
architecture invariants belong in the architecture contract; working method belongs in the
process. Do not create another packet plan, phase roadmap, handover, overnight runbook, or
SRD plan unless the active plan truly cannot express one coherent deliverable.

The plan's **Current work** section is also the compact handoff between the host and VM. A temporary
handoff may carry unintegrated evidence across environments, but it must be reconciled into the plan
and removed once consumed. Git history and the plan preserve repository evidence across environments;
Project Desk holds current planning decisions. Codex chat synchronization is not durable authority.

A completed milestone row records status only. It does not activate follow-up implementation,
specialist test matrices, or historical acceptance work. The plan's sole active implementation
boundary and the latest direct human task determine what may be worked on now.

## Supporting evidence — not implementation authority

| Document | Use |
|---|---|
| [`evidence/decision-ledger-provenance.md`](evidence/decision-ledger-provenance.md) | Historical decision provenance, source references, and supersession evidence. Consult only to resolve a genuine provenance question. |
| [`evidence/model-input-audit/README.md`](evidence/model-input-audit/README.md) | Public synthetic exact-input captures for prompt, MCP, provider-application, and provider-wire inspection. |
| [`evidence/gcs-engineering-closeout-2026-09-07.md`](evidence/gcs-engineering-closeout-2026-09-07.md) | Dated GCS implementation, limit-correction and installed-candidate evidence, including the unqualified gameplay boundary. |
| [`research/narrative-ai-prompt-context-comparison.md`](research/narrative-ai-prompt-context-comparison.md) | Supporting comparison of narrative-agent prompt, memory, retrieval, spoiler, and deterministic-tool patterns used by audit item 14. |
| [`research/comparable-stateful-narrative-agent-architectures-2026-09-03.md`](research/comparable-stateful-narrative-agent-architectures-2026-09-03.md) | Supporting comparison of agent loops, authoritative world models, opportunity-based narrative, tiered context, and spoiler-safe observability. |
| [`research/codex-model-selection-and-routing-2026-09-10.md`](research/codex-model-selection-and-routing-2026-09-10.md) | Practitioner preferences, usage rates, and model-switching options for Wan's personal Codex workflow discussion. |
| [`research/astra-from-scratch-assessment-2026-09-12.md`](research/astra-from-scratch-assessment-2026-09-12.md) | Advisory assessment of how Astra would build Sylvia GM from scratch, grounded in current source and recorded findings; not an implementation selection. |
| [`research/linux-feasibility-2026-09-14.md`](research/linux-feasibility-2026-09-14.md) | AH-08 Linux source findings, isolated backend probes, packaging comparison and proposed next experiment; not a native-platform support commitment. |
| [`research/sylvia-background-testing-2026-09-24.md`](research/sylvia-background-testing-2026-09-24.md) | Installed-app automation feasibility, native Windows limits and proposed testing-document changes; not a qualified runner or replacement testing policy. |
| [`research/rift-act-progression-2026-09-25.md`](research/rift-act-progression-2026-09-25.md) | AR-26/27 source and retained-run investigation of authored act endings, on-demand Plan retrieval and stale progress; historical recommendations and paused-run context; AR-01 was subsequently cancelled and this research selects no current implementation. |
| [`research/campaign-knowledge-search-2026-09-25.md`](research/campaign-knowledge-search-2026-09-25.md) | Source-level matching probe and comparison of direct navigation, ranked lexical discovery and semantic retrieval informing AR-25; the ticket and active plan own its subsequently agreed scope and readiness. |
| [`research/ar01-campaign-coherence-review-2026-09-26.md`](research/ar01-campaign-coherence-review-2026-09-26.md) | Source review, reproduced recall/export gaps and focused gameplay recommendations; AR-34/35/36 and the active plan own the selected implementation and verification. |
| [`research/sylvia-context-budgets-and-compaction-2026-09-13.md`](research/sylvia-context-budgets-and-compaction-2026-09-13.md) | PES-104 request-size evidence, output-budget semantics, Codex lifecycle limitations, and proposed saved-history/working-context separation; not an implementation selection. |

Supporting evidence cannot select product behavior, grant side-effect authority, or override
an active contract.

## Supporting agent guidance — not implementation authority

Additional retained supporting documents are listed here so a fresh source checkout can check
the complete documentation set. Their original dates and evidence limits remain in force.

- [`agents/triage-labels.md`](agents/triage-labels.md): repository triage conventions.
- [`reference/character-card-import.md`](reference/character-card-import.md): implemented character import reference.
- [`incidents/2026-08-20-context-window-capacity-postmortem-and-implementation-plan.md`](incidents/2026-08-20-context-window-capacity-postmortem-and-implementation-plan.md): historical incident evidence.
- [`incidents/2026-08-20-new-campaign-selector-postmortem-and-fix.md`](incidents/2026-08-20-new-campaign-selector-postmortem-and-fix.md): historical selector incident evidence.
- [`research/provider-spec-2026-10-16/LOCAL-CUSTOM-SUBSCRIPTION.md`](research/provider-spec-2026-10-16/LOCAL-CUSTOM-SUBSCRIPTION.md): local, custom and subscription provider boundaries.
- [`research/provider-spec-2026-10-16/OFFICIAL-HOSTED-PROVIDER-SPECS.md`](research/provider-spec-2026-10-16/OFFICIAL-HOSTED-PROVIDER-SPECS.md): retained official provider research.
- [`research/provider-spec-conformance-2026-10-16.md`](research/provider-spec-conformance-2026-10-16.md): bounded provider contract evidence.
- [`research/sylvia-competitor-watch-2026-08-21.md`](research/sylvia-competitor-watch-2026-08-21.md): historical competitor research.

| Document | Use |
|---|---|
| [`agents/issue-tracker.md`](agents/issue-tracker.md) | Local Markdown ticket location, checkout visibility, file conventions, and retrieval operations. |
| [`reference/content-formats.md`](reference/content-formats.md) | Installed content-definition retrieval, examples and validation owners; a supporting implementation reference. |
| [`launch-compatibility.md`](launch-compatibility.md) | Lite package compatibility, current implementation ownership and bounded candidate evidence; current task and product contract select scope. |

Supporting agent guidance explains repository workflows without selecting product behavior,
architecture, delivery scope, or current status.

## Excluded authority

- `docs/archive/**`
- deleted or Git-historical pentest backlogs, phase packets, handoffs, and review matrices
- ignored root notes including `PRD.md`, `implementation-plan.md`, `POST-LAUNCH.md`, and `notes.md`
- `.hermes/**`
- runtime histories, campaign files, personas, summaries, generated handovers, and tool output

## Documentation maintenance

1. Update the product contract for a product selection and the architecture contract for an
   architecture-semantic selection.
2. Record provenance or supersession in the evidence ledger without restating the full contract.
3. Keep one active plan and update its status from live evidence.
4. Archive completed, superseded, duplicate, session-specific, and one-off documents.
5. Keep `AUTHORITY.json` semantically identical to this index.
6. Run `bash scripts/check-docs-index.sh --worktree` and `git diff --check` for documentation changes.
   The worktree check covers staged, unstaged, and untracked documents without staging them. The
   default/`--staged` mode is for commit hooks and uses the staged copy of this index.
