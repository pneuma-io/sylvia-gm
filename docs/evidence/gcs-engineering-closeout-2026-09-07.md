# GCS engineering closeout, 2026-09-07

This is dated implementation and verification evidence. The
[Alpha Completion Plan](../plans/alpha-rebuild-readiness-plan.md) (development progress and release
readiness) owns current status. Wan selected completion, commit and push of the GCS branch. Merge
and release publication are not part of this handoff.

Subsequent decision, 2026-09-07: Wan closed GCS-03 after this delivery and will request further
model testing or prompt tuning if needed. The open-ticket disposition in the original report below
is historical. Its verification results and recorded limitations are unchanged.

## Delivered behavior

GCS-02 gives the GM one continuous conversation with its tools, ending with Campaign File updates
(durable campaign memory) and narration together. There is no mandatory preparation form or
separate prose-generation request. GCS-05 rejects premature prose when a native tool call is
required, while preserving the legal reference, Ruleset and Campaign File choices. This enforces
tool use; it does not prove the model chose the right skill check.

GCS-08 shows skill results using committed engine data. The default card includes the check,
outcome, total and calculation, plus difficulty when available. Existing campaign knowledge and
Character File retrieval work remains included. Native MCP V2, exact native provider history,
stable tool names and current-only application controls are retained.

The GM's shared 16-iteration cutoff is removed. Model requests and automatic engine transitions can
continue until completion, cancellation or an actual failure. Invalid-call correction limits,
provider timeouts and context-fit checks remain. A review caught cancellation during tool-list
discovery; a failing regression reproduced it, and a cancellation check now runs immediately after
discovery, before any automatic engine step.

The earlier hidden 4096 output clamp is gone. A valid selected model's configured output value
reaches the provider unchanged. Normal saved OpenCode Go/M3 output was raised from 4096 to 131072;
no other projected provider setting changed. Sparse Go model discovery supplies that M3 value when
output metadata is absent. Unknown models no longer get an arbitrary 4096 Settings autofill.
Settings calls the field "Output token limit" and explains that it includes reasoning, tool
arguments and narration. This is a maximum, not a requested response length. Unselected/default
provider paths still have their legacy default; no claim is made that provider limits disappear.

## Verification

The final source passed 388 targeted tests across the provider-session adapter, stateless MCP,
Admin Full Audit, generic sessions, frontend, provider context capacity and provider management.
The tests cover exchanges beyond 16 steps, final Campaign File delivery, pre-cancellation and
cancellation during discovery, native continuation, configured output handling and Settings
validation. Sol independently reviewed the limit changes and confirmed the cancellation fix;
its focused two-case Stop regression passed.

All 10 test-runner checks also passed, including synthetic in-process and owner-process journeys,
restart/isolation behavior and output-limit preflight. These are offline fixtures, not new M3
samples. Ruff passed on the ten limit-related Python files, JavaScript syntax passed, scoped mypy
passed for the provider-session adapter, and `git diff --check` passed. A wider type check retained
three pre-existing errors in unchanged context-fit/settings code; it is not reported as clean.

The sidecar and unsigned NSIS installer built successfully. The sidecar's build manifest resolves
the changed provider session and settings modules to the isolated GCS checkout. A receipt covering
556 production inputs matched before and after packaging. The installer exited successfully.
Installed owner bytes match the retained owner exactly. A complete desktop byte comparison found
only NSIS's expected three-byte `UNK` to `NSS` bundle marker change.

No new live-provider playtest or installed campaign journey was run for v136. The app remains
closed. Skill-card success/failure and reopen visual checks belong to the retained v132/v133
evidence, not this run. The original v135 test configuration and failures were preserved.

## Candidate identity

Candidate: `GCS-v136-final`, unsigned local Windows engineering build, package version `0.3.0-1`.
Branch: `codex/gm-context-seams`. The source was frozen on base HEAD
`5c17a1b037694ef70170b6e0d2df6ddf5d77fc9a` with uncommitted GCS changes; that base alone does not
identify the build. The containing delivery commit records those changes. Build-input fingerprint:
`61ae0cf64b2edfc92cf9c26d99d5bd136e324845b559906513334b11e4143642`.

| Artifact | SHA-256 |
| --- | --- |
| Retained NSIS installer | `0524118683ea11fd7c621ac4dabefd708783706476af90096a349dde7a78de61` |
| Retained desktop executable | `39a39a53347214f5b5970238d88c32f9bf1787849def7733b40e6319c0c45c4d` |
| Installed desktop executable | `3a84aa675a1e8cb7dfc3d13d569dc60af651379f4cf88905ad339de3e53f0dd7` |
| Retained and installed owner executable | `6864acd468d52fea93cde5fb26b6833d44c0b250b2c745e7767f59917c6943de` |

## Deferred work

GCS-03's gameplay qualification remains open for future product review. The retained samples
include player-agency and skill-selection mistakes. The final v135 combat replay stopped during
action 2 at the now-removed shared loop cutoff; later actions and the planned restart were not
reached. Offline fixes do not turn that failed live sample into a pass, and the evidence does not
establish MiniMax M3's intelligence ceiling. Wan asked to put model steering aside after that test.

GCS-06 (explicit OOC messages), GCS-07 (NPC dialogue boxes), and GCS-09 (an interruption message that
incorrectly denies saved mechanics) remain separate unimplemented follow-ups. Raw audit payloads,
private campaign databases, runtime configuration and local generated evidence are excluded from
the delivery commit.
