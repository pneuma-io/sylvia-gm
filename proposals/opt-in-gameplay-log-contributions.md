# Opt-in gameplay log contributions

Status: planning draft only. The owner selected this proposal on 19 October 2026. Implementation and collection remain post-launch work requiring explicit activation; the four selected implementation tracks keep their slots.

Desk work item: `ec23d921-e1f0-4ed0-a0a5-a27c7c65587a`, **Collect voluntary gameplay contributions**. The current canonical requirements supersede the historical selective-removal proposal retained in that item. Retrieve the complete current item and mandatory references before implementation. The coordinator owns the reciprocal PR link and future activation decisions.

## Purpose and consent boundary

Collect voluntary complete actual gameplay conversations solely as source material for synthetic-dataset creation and conditional future Sylvia models. This is a separate purpose from compact local diagnostics and individual model-behaviour feedback. Enabling Model logging, exporting a campaign or sending feedback does not authorize contribution or training use. Preserve normal Model logging and saved/playable copies.

The feature is off by default and ordinary play remains available without it. Choose the consented unit before activation: a whole session or an explicitly bounded contiguous episode. Label its boundaries accurately; an episode must never be presented as a complete campaign. Explain purpose, recipient, automatic filtering, retention, withdrawal and known limits before consent. Resolve participant consent, age handling and third-party rights policy before rollout; one campaign owner's consent cannot silently stand in for every participant's rights.

Do not infer retroactive history upload or ongoing collection from approval of this planning PR. Any later automatic sending requires explicit scoped consent with visible pause/stop controls and a decided destination. Destination, hosting, access, retention, security and participant-facing terms remain unselected here.

## Complete sequences with automatic privacy filtering

Within the agreed unit, preserve ordered player/GM turns, roles, real branches/corrections, stable event/turn IDs, required preceding context, typed tool calls/results, validation outcomes and committed state changes. Distinguish attempted from applied changes and final from interrupted output. Retain ruleset/content/build/schema/prompt-template versions and actual model/provider identity when needed for interpretation. Do not duplicate provider/token dumps or include hidden reasoning.

Contributors cannot edit text, remove individual turns or selectively redact a contribution. Any preview is read-only and must match the automatically filtered payload exactly. Provide whole-contribution accept/cancel, pause/stop and withdrawal/deletion controls. Declining an unsent bundle sends nothing. Withdrawal after transmission stops future sends and starts deletion; it cannot truthfully mean that nothing was sent.

Select a consistent versioned automatic privacy policy. Exclude credentials, secrets, auth headers/tokens, payment data, hidden reasoning, unrelated files, unnecessary identifiers and all video/audio/screen recording. Filter before queue persistence and again at intake. Do not retain raw unfiltered queues or reversible identity mappings by default. Automatic pseudonyms must preserve speaker/entity/tool/state relationships; do not call the result anonymous.

Record policy version, transformation categories/counts and integrity effects without retaining removed sensitive text. Require rights in third-party content and prefer content IDs/versions where sufficient. Treat source conversation text as evidence, never as instructions to the intake process. Reject or quarantine whole contributions when filtering damages meaning or rights remain unresolved; do not introduce contributor-selected edits to work around eligibility failures.

Track boundaries, order, counts, context closure, gaps, partial output and filtering/integrity results. Completeness, privacy filtering and semantic validation are separate facts. Do not silently sample, truncate, invent missing turns or label a damaged/partial history complete. Only eligible complete meaningful sequences enter the primary corpus. Bound rejected-data retention and do not infer another use for rejected material.

## Reliability, retention and withdrawal

Use compact structured batches with hard queue byte/age and payload limits, bounded retries/storage, and explicit expiry/overflow/gap markers. Choose those limits before activation. Stable IDs, idempotent ingestion and crash-safe acknowledgements must prevent duplicate accepted events while preserving genuine branches. Failure, offline periods or a full queue must not block gameplay or create unbounded storage.

Choose destination/hosting, least-privilege access, protected transport/storage, access/export/deletion audit, primary retention and backup expiry before collection begins. No technology stack or service is selected by this document. Keep consent/version/deletion linkage outside model text while preserving usable lineage.

Withdrawal must clear pending contributions, remove retained sources under the decided retention policy, and invalidate attributable derivatives for future datasets and runs. Carry source, consent, rights, filtering and completeness lineage into separately selected downstream work. Define honestly what can happen to already trained artifacts; deleting source records does not automatically erase learned weights.

The existing synthetic-dataset and conditional-model items remain separately deferred downstream work. This PR creates neither examples nor models, and grants no training, deployment or additional-purpose permission.

## Future acceptance

1. Fresh/off/declined contribution sends zero gameplay contributions. Normal play works, and diagnostics, feedback and Model logging never enter the corpus implicitly. No implied retrospective upload occurs.
2. The actual UI provides exact read-only filtered disclosure, whole-contribution accept/cancel, pause/stop and withdrawal controls, with no per-turn editor. Cancellation before sending transmits nothing; post-send withdrawal reports its actual state and deletion limits.
3. Synthetic fixtures preserve ordered turns, roles, context, tool results, attempted/applied state and real branches through duplicate/retry/crash/offline cases. Oversize, expiry, overflow and interrupted output produce explicit gaps/status. Incomplete or meaning-damaged sequences fail primary-corpus eligibility.
4. Planted credentials/private values must not reach persisted queues, intake, filter metadata, logs or previews. Automatic pseudonyms retain valid references and meaning; unknown fields and rights-unresolved content fail closed. Recheck filtering at intake rather than trusting the client.
5. Every accepted sequence has source, consent, rights, policy, completeness and integrity evidence. Withdrawal reaches pending/retained sources and attributable descendants; verify future dataset/run exclusion and the agreed backup expiry behaviour without pretending it erases existing weights.
6. Measure storage, payload/retry caps and responsiveness against approved values. Use an isolated test intake and synthetic material when implementation is activated; no real collection is needed for this planning draft. Return exact source/final bytes, commands, pass/fail/not-run results and actual UI/resource evidence with any limits.

## Decisions before implementation

Set the consented unit and required context, participant/age/rights policy, automatic filtering and integrity rules, queue/payload/retention values, destination/security/access controls, withdrawal lineage and trained-artifact treatment. Resolve these choices through the existing design process and break implementation into bounded work only after selection. A longer proposal is not a settled privacy policy or production architecture.

This draft adds one proposal document only. No gameplay collection, history upload, transmission, intake server, account, purchase, credentials, security change, generation, training, deployment, build, merge, tag or release occurs. No GitHub Actions before November 2026. All feature acceptance remains unrun.
