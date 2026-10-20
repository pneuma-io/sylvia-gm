# Sylvia launch compatibility

SRD Lite is the first-party compatibility baseline for all Sylvia launch work. It has a reduced spell list of 117 supported spells, without consumables. Consumables belong to future SRD Full. New mechanics, content, examples, importers, builders, defaults, discovery, schemas, fixtures, CI and packaging must use and verify the actual Lite package. Keep the application multi-ruleset architecture and unrelated proprietary rulesets intact.

Full (`wotc.srd.5.2.1`) is archived as a separate inactive subproject outside the active source/package/gate roots. Its original implementation, assets, source documents, tests and generators remain recoverable. Wizards' source-document identifiers remain provenance. The bundled reference implementation uses pneuma.srd.5.2.1.reference and preserves the source creator's attribution; retained old reference IDs remain valid in saved package closures. These identifiers do not activate an executable Full ruleset. Existing user data and installations are preserved.

Migration preserves narrative, characters, encounters, settings, plans and intended mechanics. Rebind actual package/schema identities and hashes, adapt supported schema details with explicit provenance, and verify content equivalence plus load/start/save/reopen. Do not silently replace or drop unsupported content. The final active launch roster is exactly 117 spells: the unchanged 96-spell Lite base plus exactly 21 preserved Full-only implementations. Do not add another spell; preserve out-of-scope source content in the inactive archive and report any content conflict. A stale Full executable binding must require migration rather than reactivate the archived package.

Development qualification has no whole-run wall-time cutoff. Let healthy progressing tests finish. The 1,800-second allowance applies only to future post-launch CI before each pull request; select it explicitly with `--ci`. It must not stop current development tests or mark a healthy development run as failed. The approved deterministic effect-execution allowance is 20,000 operations per top-level action, shared by all nested calls. Call-depth and transaction safety remain in force. Over-limit mechanics must terminate without partial corruption. Synthetic 64-target Ice Knife robustness is reported separately from realistic ordinary casting.

Native Windows and live-provider acceptance remain separate evidence layers. Browser fixtures and compiled protocol proof cannot close those criteria. Respect access denials and provider authorization/cost boundaries.

## Current combined source and remaining qualification

The reviewed combined code/content import is private Git commit
`cebc66f2245af071f6b2f4cfdeeefbeb980a70de`, followed by the README correction
`56b0164885834ccac2a1c6f1f98607da2b0202a1`. Current SRD Lite is 0.3.13, primary content
identity `68b5a9d43e99244f4874daa83a59e31b6f8aec0210b06e4a94a2118b2b83d347`.
This successor retains base species Speed during advancement, validates resulting walking Speed,
and displays the computed result before confirmation. Its 16 SRD Plan closures were admitted;
the independent Riichi Plan is unchanged. The preceding 0.3.12 qualification admitted all 17
Plan closures and installed the package without changing an existing synthetic 0.3.11 pin. Subsequent scoped source
repairs are recorded in Git history and their delivery receipt. Existing user saves stay intact.

The collated final sprint covers documentation, reproducible portable/installer packaging,
proportionate CI, generic procedure/modifier failures, shipping optimization, dataset-safe
launches and invocation-owned build cleanup. The Lite package's procedures remain empty;
the generic correction does not select another full spell replay.

The tracked recipe produces an unsigned NSIS installer and portable EXE ZIP, with exact source,
binary and payload identities and required notices. It excludes mutable settings, saves and
credentials. Final rebuilt artifacts, installed notice delivery and clean-Windows qualification
remain open. Older executable/provider evidence cannot validate changed source bytes.
Preserve unique work and complete the separate disk-space sequence before clean-Windows tests;
then reconcile acceptance and pause for go-live planning. The repository remains private.
Public publication requires separate approval.

application/bundled_packages.py materializes exact Ruleset/reference/Setting closures.
application/bundled_campaign_plans.py selects Plan bytes and checks containment before reads.
src-tauri/scripts/build_owner_sidecar.py stages only the explicit sidecar-data-files.json
inventory. Update that inventory deliberately when legitimate embedded paths change.
The owner process remains the sole authority; the renderer presents ordered actions and results.
One Execute for the whole ordered batch, with one closing narration, remains a final integration
check. This pass makes no new native Windows or whole-campaign acceptance claim.

### Retained historical evidence

The Build1970/Lite0.3.9 supply report records 164 source cases, exact compiled inventory recovery and two
genuine Sol exchanges. Those results are reused with their fixture/provider qualifications;
they are not a fresh test of this cleanup or the final combined candidate. Compiled signed-in Sol capability parity, Builder/GM tool use and recovery now have bounded
Build 2001/2025 evidence. The broader live-provider matrix, final native input and installed
notice/installer checks remain open.
Full Audit logging is unrelated to the inactive Full ruleset. AH01–06 refactors remain deferred.

See [licensing status](../LICENSING-STATUS.md) and
[dependency notice status](../licenses/DEPENDENCY-NOTICES-STATUS.md). Required notice delivery
and legal rights/adoption decisions are separate. The [documentation index](INDEX.md) links the current product, architecture,
development process and readiness plan. Historical build records retain their original limits.
Current private Git delivery is not public release or final installed acceptance.


## Development and release checks

PRs and pushes to main select checks from the complete source diff and retain the source/base
identities and reasons. Documentation-only edits check links and copy without campaign replay or
owner compilation. Runtime, frontend, package, native, licensing and build changes select their
affected boundaries and composed journeys. Unknown or incomplete diffs select broad coverage;
runner/selector edits verify selection itself. Affected checks do not prove whole-product acceptance.
Branch pushes do not duplicate PR executions. No healthy existing run is cancelled.

Explicit `qualification: full` workflow dispatch retains every supported Python/JS journey and the
separate Windows sidecar/Cargo job for combined-candidate qualification. Missing, failed or skipped
required checks fail. Native/installed and genuine-provider acceptance remain separate. Development
has no whole-run quota. Only explicit full `post_launch_ci_budget: true` selects the future
post-launch 1,800-second allowance; no complete-gate latency guarantee has been established.
GitHub platform limits remain separate constraints.

The standard private Windows runner uses two Python workers with measured case balancing; browser
journeys run before the heavy Python pool. Case progress, collection, pytest phases, package seed
preparation, database copying and optional archival have separate receipts. Group case durations
sum worker time, not shared wall time. The explicit `benchmark` dispatch compares identical work
with one/two workers and case/module assignment on the actual runner. The first 16-case comparison
took 468.7 seconds with one worker, 420.6 with two/module affinity and 415.3 with two/case balancing.
This supports the bounded two-worker default, not an established optimum or whole-gate guarantee.
Successful synthetic-fixture
archival is optional evidence retention; save/export/recovery and full archive-restore assertions
remain required. Compact uploads retain summaries, JUnit, manifests, timing and failure logs;
reproducible scratch databases, caches and environments stay outside uploaded diagnostics.

Release builds also require an external plain-text literal-name list. Run
`python scripts/check_release_identity.py --forbidden-name-file EXTERNAL_FILE` against the
selected source. The external exact regression module and list are retained with the
delivery evidence. They must stay outside public source; the repository's synthetic-token
test is not an equivalent literal-zero check. The release builder requires this external
gate before compiling and binds source inputs plus installer/ZIP hashes in its manifests.

`uv run --locked --all-extras python scripts/build_windows_prerelease.py` requires
`--dependency-notices-sha256 SHA256` and `--forbidden-name-file EXTERNAL_FILE`. Run it from
the clean committed source selected for qualification. It uses the normal optimized release
profile and fresh invocation-owned output directories. Successful invocations remove only
their own disposable work after copying/verifying final artifacts; failed work is retained.
Set process-local `SYLVIA_KEEP_OWNER_BUILD=1` when build intermediates are needed as evidence.
This mechanism never sweeps older builds, another live invocation, user databases or profiles.

Final Windows checks must cover installed/portable coexistence, same-dataset refusal,
save/reopen, portable folder movement and updates, required notice delivery, and credential
re-entry after moving to another Windows account. DPAPI protects credentials for the current
user; folder portability does not make credentials portable. Use selected synthetic data,
never record credentials, and keep genuine-provider and final native-input results separate.
