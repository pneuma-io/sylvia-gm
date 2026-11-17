# Compact local diagnostics

Status: planning draft only. Daniel selected this as high priority among future work on 19 October 2026. It does not displace the four selected implementation tracks or start another worker.

Desk work item: `19e8bed8-801d-4ab5-96ff-f29e195cca80`, **Keep local diagnostics compact**. Read the complete current item and mandatory references before implementation; the coordinator owns its reciprocal PR link and scheduling updates.

## Intended outcome

Keep routine local logging small and useful for programmatic bugs: crashes, exceptions, failed operations and enough safe reproduction context to understand them. Individual player feedback remains the principal channel for model-behaviour problems. A player must be able to report a problem without recording a complete conversation or enabling exhaustive model tracing.

Preserve normal saved campaigns and the existing off-by-default Model logging / Full Audit feature, including its provider fidelity, deliberate enable/view/copy/export controls and credential exclusions. An app error must not automatically enable Full Audit. This proposal does not reopen completed Model logging naming/navigation or campaign-export work.

## Proposed local scope

Use compact structured events with severity, stable error/category codes, app/build/platform version, operation names, safe correlation IDs and useful timing/state-transition outcomes. Add only minimal non-content reproduction context, such as an action category or safe feature flags. Ruleset/model/version metadata may be relevant; token-level conversation content is not routine diagnostic context.

Exclude raw prompts and responses, chat text, tool payloads by default, hidden reasoning, credentials, auth/session tokens, headers, personal paths, names and unrelated saved data. Exception messages and stack details need sanitization because they can contain user input or filesystem paths. Use deliberate safe fields rather than serializing whole objects and hoping a final string filter removes private data.

Choose a bounded rotating local file or bounded ring buffer using the smallest suitable existing owner boundary. Set and document byte/age limits before implementation. Deduplicate or rate-limit repeated errors while retaining counts and time ranges. Writes must tolerate interruption, stay responsive and avoid blocking gameplay. Disk-full or logging failure must not crash the app or create an unbounded fallback log. Diagnostic rotation must not delete campaigns, user files or deliberate Model logging exports.

Provide a deliberate local support-bundle export if selected in the final UI design. It may combine sanitized diagnostics, user-entered reproduction steps and an optional individual feedback note. Show the exact contents, permit cancellation and retain normal file-save controls. Do not automatically append campaign/model logs; those require their own explicit existing flow. No automatic upload, support recipient, network destination or standing sharing is selected. The report UI and any eventual destination remain undecided.

## Ownership and separation

Local diagnostics support app bugs. Optional Full Audit supports deliberate developer inspection. Individual feedback describes the player's model-behaviour experience. The separately planned gameplay contribution flow has a dataset-only purpose and separate consent. None of these settings or exports implicitly authorizes another purpose, training use or remote collection.

Inspect current diagnostic/error paths before implementation and place collection, sanitization, retention and export at their existing owners. Keep the application ruleset-neutral. Use synthetic injection fixtures and disposable profiles; no access to unrelated private campaigns, credentials or traces is needed to implement this feature.

## Future acceptance

1. Fresh ordinary play creates bounded local programmatic diagnostics without full prompts, responses, tool bodies or chat. Representative crashes, exceptions and failed operations provide useful safe IDs/categories and minimal reproduction context.
2. Inject private paths, credentials, input text and identifying values into nested failures and stack/error messages. Verify they are absent from stored diagnostics, fallback output and exported bundles. Check unknown/unapproved fields do not enter the report.
3. Exercise rotation/expiry, repeated errors, process interruption, disk-full and logging-write failures. Measure storage against the approved caps, preserve error counts/time ranges and show that normal gameplay remains responsive. Do not claim crash safety from an uninterrupted file write alone.
4. Check the actual preview/export/cancel flow, including user-entered reproduction steps and optional feedback. The saved bundle must match the preview. Cancellation creates no shared report; no unsolicited network upload occurs.
5. Preserve explicit Model logging enable/view/copy/export behaviour, provider fidelity and credential exclusions. Verify ordinary saved campaigns and gameplay remain intact. A model-behaviour report must remain possible without exhaustive tracing.
6. When implementation is selected, run focused owner regressions and a representative installed-app journey on exact final bytes. Return commands, pass/fail/not-run results, measured log sizes and any unresolved UI/destination decisions. Source checks alone do not establish installed behaviour.

## Decisions and scheduling

Choose exact retention/size caps, the minimal event allowlist, sanitization boundary, crash-report handling and report UI when implementation is activated. Resolve any eventual sharing destination separately; keeping a local bundle does not choose a service or grant upload permission.

High future priority is recorded without inventing a deadline or consuming another implementation slot. The existing diagnostics item remains the owner; the coordinator maintains its current status and manual edits. Other discussed backlog work stays deferred.

This draft adds one proposal document only. No runtime collection, transmission, server, deployment, purchase, credentials, security settings, build, merge, tag or release changes occur. No GitHub Actions before November 2026. Feature acceptance remains unrun.
