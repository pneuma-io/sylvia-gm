# How Sylvia is checked before release

CI means repeatable checks of a change. Sylvia runs them locally. GitHub Actions is
manual-only; a push or PR does not start it. This guide explains the commands. The
[Development Process](quality-preserving-development-process.md) remains policy.

A branch is a work version. A PR asks for review; a merge combines its changes into
`main`. Check the branch, then check the combined source before building a release.
Record the exact checked commit so the build can use those same bytes.

## Choose the work that needs checking

| Check | When to use it |
|---|---|
| Focused | Test the changed behavior at its owning boundary. |
| Affected | Let the selector explain which checks the complete source diff needs. |
| Full | Run every configured product journey for combined-source qualification. |
| Historical GM checkpoint | Explicitly reproduce the original refactor selection when requested. |

The fast `gm-turn` group collects both complete provider-adapter and generic-session
modules. It currently has 282 behavioral cases. Campaign/action-sequence and authored-plan
checks are selected for their relevant risks and full qualification. Documentation-only
changes select copy/link checks, without campaign replay or a build.

The historical `gm-turn-checkpoint` preserves the two full modules plus the original
campaign group. Its original count was 430; future collection can grow. It includes one
retained milestone-label test that is explicitly excluded from product qualification.
Exclusions are reported by exact test ID and reason, separately from passes and skips.

## Run the checks

Use PowerShell in the checkout after `uv sync --locked --all-extras`. For JavaScript/browser
checks, install the locked dependencies once with `npm ci --prefix tests --ignore-scripts`.
Choose a fresh output directory beside the checkout, within the current work item.

```powershell
$checks = '..\checks'

# One focused file, or append ::test_name for one scenario.
.venv/Scripts/python.exe -m pytest -s -p pytest_asyncio.plugin -p no:cacheprovider tests/test_command_registry.py

# The fast GM behavioral group.
.venv/Scripts/python.exe scripts/check_supported_journeys.py --journey gm-turn --jobs 1 --output-dir "$checks\gm-turn"

# Affected checks for the branch's complete diff.
git fetch origin
.venv/Scripts/python.exe scripts/select_checks.py --base origin/main --output "$checks\affected-selection.json"
.venv/Scripts/python.exe scripts/check_document_links.py --selection-manifest "$checks\affected-selection.json"
.venv/Scripts/python.exe scripts/check_supported_journeys.py --selection-manifest "$checks\affected-selection.json" --jobs 2 --case-workers 1 --scheduler case --output-dir "$checks\affected"

# Full configured product qualification.
.venv/Scripts/python.exe scripts/select_checks.py --full --output "$checks\full-selection.json"
.venv/Scripts/python.exe scripts/check_supported_journeys.py --selection-manifest "$checks\full-selection.json" --jobs 2 --case-workers 1 --scheduler case --output-dir "$checks\full"

# Historical checkpoint, only when explicitly wanted.
.venv/Scripts/python.exe scripts/select_checks.py --check gm-turn-checkpoint --output "$checks\historical-selection.json"
.venv/Scripts/python.exe scripts/check_supported_journeys.py --selection-manifest "$checks\historical-selection.json" --jobs 2 --case-workers 1 --scheduler case --output-dir "$checks\historical"
```

For authority-document edits, also run `bash scripts/check-docs-index.sh --worktree`.
The source quality group runs `ruff check src` and `mypy src`. Type checking still has
recorded legacy exceptions in `pyproject.toml`; new refactor modules stay strict.

Development runs have no whole-run time cutoff. `--ci` explicitly selects the separate
future post-launch budget; it is not a development setting or a promised full-suite runtime.
Coordinate worker counts with other builds. Keep healthy checks running.

## Read and retain the result

Use a clean committed head for final qualification. Record its commit, the commands, selection
and reasons, pass/fail/expected-failure counts, elapsed time, and checks not run in the PR.
`summary.json` records the revision, a hash of the checked files and whether source stayed unchanged.
Required-case manifests, XML reports and timing files show what actually ran. The runner rejects
missing cases and duplicate coverage; file inventory alone does not prove case coverage.

Recorded defects execute as strict expected failures and appear separately from passes.
An unexpected pass, unexpected skip, missing case or changed source fails the run. A known
failure does not establish that the affected feature works. Preserve its reproduction and limits.

Timing-sensitive tests should synchronize on events or use controlled clocks. For example,
cancellation starts after provider admission; stream-timeout tests distinguish inactivity from
a total deadline without narrow wall-clock sleeps. Their scoped safety limits are not CI quotas.

The cancellation test allows five seconds for provider admission and checks the existing
two-second response bound from the actual cancellation. Stream tests use virtual time:
four four-second gaps fit a ten-second inactivity limit even though the total is sixteen;
a stalled read expires at one virtual second. These timings test the selected behavior.

## From checked source to a release

After the selected PRs are combined, qualify that exact source and build its installer and
portable package with the [tracked release recipe](../../README.md#build-from-source).
Retain source, owner and artifact identities and required notices.

Scripted tests cover deterministic behavior. Real-provider replies, packaged builds and native
Windows interaction have separate acceptance checks in the [Development Process](quality-preserving-development-process.md#5-installed-app-verification).
Use the selected test environment and data, record actual outcomes and remaining gaps, and obtain
owner release approval. Source checks and a PR merge do not grant release approval.
