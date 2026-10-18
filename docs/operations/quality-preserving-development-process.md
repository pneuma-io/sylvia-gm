# Sylvia GM Development Process

> **Status:** Active operations authority
>
> **Purpose:** Help one person ship a dependable product without turning development into a
> certification programme.

## 1. Start from the current task

The latest direct human request is the implementation scope. Read `AGENTS.md`, `docs/INDEX.md`, and
only the active authorities relevant to that request. Completed milestones, archived plans, test
inventories, handoffs, and old review findings are background evidence; they do not activate work.

Before editing:

1. inspect HEAD and the worktree;
2. identify the user-visible result and explicit non-scope;
3. find the smallest real boundary that owns the behavior; and
4. identify file ownership and use an isolated copy when another worker owns integration.

Do not silently expand a plain requirement into exhaustive automation, an enterprise framework, or
a new proof programme. If Wan reports a defect in an installed candidate, that defect and the
supported journey it affects take priority over historical backlogs.

## 2. Build the smallest honest vertical

Prefer a complete, observable path over a broad unfinished foundation. A normal change should:

- reproduce the reported behavior or establish the missing user journey;
- correct the owning seam rather than add a compatibility shim;
- preserve the one authoritative owner, persistence, privacy, and fail-closed boundaries;
- reuse established runtime and package primitives; and
- finish with something the user can see or use.

Ruleset content is declarative whenever the established package runtime can express it. Adding
ordinary content does not justify new core code or repeated lower-layer certification. Report
unsupported mechanics plainly. Existing generic tools may execute represented fixed rules;
missing fixed SRD spell effects remain pre-release gaps under product contract section 3.2.
Genuinely open-ended fictional adjudication remains with the GM.

## 3. One writer and simple handoff

Exactly one worker writes the live worktree. Other agents may inspect or test frozen bytes
read-only, but may not make concurrent edits.

When work moves between the host and VM:

- commit a coherent checkpoint when practical and authorized by the current human task;
- leave one short repository-root handoff naming the commit, work performed, real verification,
  remaining defects, and whether writer ownership is relinquished;
- treat the handoff as evidence, then reconcile it against live source and the active plan; and
- replace or remove the handoff after its useful facts have been incorporated into the plan.

Do not create chains of phase reports, verifier reports, evidence packets, or session summaries.
Project Desk holds current planning decisions; Git history and the synchronized active plan
preserve repository evidence.

## 4. Test in proportion to risk

Wan's replacement for the cancelled AR-01 marathon is a comprehensive automated framework plus
short targeted real-model and installed acceptance checks. Preserve continuity, progression,
recovery and supported-journey evidence under product contract section 7.1. The aggregate
deterministic development gate has no whole-run wall-time cutoff; let healthy progressing tests
finish. The 1,800-second allowance applies only to future post-launch CI before pull requests,
selected explicitly with --ci. The removed package-generation timeout is separate. Record actual
coverage and gaps without treating historical runs as current proof. Current tasks select their
own affected checks; this maintenance pass starts no additional framework or live-test scope.

Start with the smallest authentic proof of the changed behavior:

- a focused regression at the boundary that owns a code defect;
- a representative composition test when several proven boundaries meet;
- a live desktop reproduction for presentation, streaming, timing, input, or packaged-runtime bugs;
- an installed Windows journey for release-candidate claims; and
- a real configured provider when provider compatibility is the behavior being promised.

Test shared invariants deeply once at their owning boundary. Direct consumers need representative
coverage, not the complete lower-layer matrix repeated through every feature. Do not use test counts,
freeze cycles, independent-review counts, or byte ledgers as goals.

Run broader suites only when the change has genuinely broad reach, a focused failure indicates
cross-cutting damage, or Wan directly asks for them. An unrelated historical failure does not
authorize repairing that subsystem; confirm that the current change did not cause it and report it
as out of scope.

Use:

```powershell
.venv/Scripts/python.exe -m pytest -s <focused paths>
```

Run applicable formatting or type checks for touched code. For authority-document changes, run
`bash scripts/check-docs-index.sh --worktree` to check staged, unstaged, and untracked documents
against the working documentation index. This command does not stage files. The default/`--staged`
mode checks only staged documents against the staged index for commit hooks. Always run
`git diff --check` before closeout.

## 5. Installed-app verification

Use the Windows environment selected for the current acceptance run. The old test VM is retired;
its 2026-09-24 selection is historical, not an instruction to restart it or install another VM.
The active plan records the exact candidate and open native/installer acceptance. Host-desktop
interaction and Computer Use require Wan's explicit selection for that run. Preserve reserved
or denied checks; continue authorized source, compiled-owner and browser-component work with
their own evidence qualifications.

Installed UI evidence uses the real desktop executable, packaged owner, commands, database and
rendered controls. Browser tests that replace the desktop connection remain component tests;
direct backend calls do not establish UI interaction. Native dialogs, window management and
outside-window panel drops need actual Windows input in the selected environment or an explicitly
selected manual check. A browser-only gesture cannot close a native-input requirement. Qualify the runner
on a representative installed journey before crediting acceptance; report unsupported cases.

Keep automation debugging local to the test process/environment, retain the application's security
settings, and isolate the selected test data from personal profiles. Record whether replies came
from a scripted fixture or the selected real provider. A fixture can prove UI and recovery behavior;
provider compatibility and gameplay judgment still require their selected real provider in short
targeted checks. AR-01's marathon execution requirement is cancelled; retain its evidence.
An instrumented VM is not automatically AR-24 clean-machine proof: its added dependencies must not
hide a missing application dependency. For blind-player tests, expose only player-visible UI to
the player; keep evaluator access to plans and Full Audit separate.

Source-level tests cannot approve a release candidate by themselves. For an advertised desktop
journey:

1. build the sidecar and installer from a known commit;
2. install the exact artifact in the Windows test environment selected for that run and confirm desktop/owner
   identities; identify dirty engineering builds explicitly rather than treating HEAD as their bytes;
3. enable Admin Full Audit before the first tested action and inspect its exact input and output
   throughout the run; the testing request is the authority and needs no separate approval;
4. exercise the real provider, campaign, Builder, persistence, restart, and failure paths selected
   by the active plan;
5. fix defects found in that journey and repeat only the affected proof; and
6. hand the exact installed candidate to Wan for visual and release approval.

An authorized test also permits copying or locally exporting its selected Full Audit records when
useful. Spoilers in the selected test do not require permission or an extra checkpoint. Preserve
records needed for a finding or the selected proof before a restart clears them; exporting empty or
unneeded traces is not a prerequisite for routine continuation. Keep raw traces out of public
commits/reports and retain the credential, selected-data and separate ComfyUI boundaries.

Manual feedback is product evidence. A flickering history, confusing settings field, missing button,
or unusable flow is not dismissed because a source test passes.

## 6. Safety and authority

Preserve:

- one authoritative campaign writer and transaction path;
- exact retry, idempotency, persistence, backup, and recovery behavior where touched;
- credential and private-data boundaries;
- truthful tool and model capability reporting;
- no automatic provider fallback or invisible spending; and
- clear failure rather than fabricated success.

Stop for a genuine unresolved product, architecture-class, privacy, security, destructive-data,
spending, publication, legal, or material-risk choice. Continue safe independent work when one
slice is blocked.

## 7. Completion report

Report plainly:

1. what now works for the user;
2. what was actually verified, including whether it was source, packaged, or live proof;
3. what remains incomplete or deferred; and
4. the exact candidate or commit being handed off.

Do not call an artifact released before publication, call a partial mapping complete, or describe
historical evidence as a current test result.
