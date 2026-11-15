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
modules, plus their small turn-state checks. Collection determines its current size.
Campaign/action-sequence and authored-plan
checks are selected for their relevant risks and full qualification. Documentation-only
changes select copy/link checks, without campaign replay or a build.

The historical `gm-turn-checkpoint` preserves the two full modules plus the original
campaign group. Its original count was 430; future collection can grow. It includes one
retained milestone-label test that is explicitly excluded from product qualification.
Exclusions are reported by exact test ID and reason, separately from passes and skips.

Some cases can only run on one platform. Mark them `@pytest.mark.runs_on("linux", reason="...")`
(or several platforms, such as `runs_on("win32", "linux", reason="...")`); the names are
`sys.platform` values, and only `win32` and `linux` are accepted. A marker without a platform,
with an unknown platform or without a reason stops collection. On any other platform a direct
`pytest` run shows the case as skipped with "Runs only on linux: ...", and the runner reports it
as a platform exclusion: not required, not run, and listed by exact test ID and reason in
`summary.json` (`kind` is `platform`; the historical exclusion above is `historical`). Do not use
`pytest.mark.skipif` for this: in the gate a skip always fails, and a check rejects it under `tests/`.

## Run the checks

Use PowerShell in the checkout after `uv sync --locked --all-extras`. For JavaScript/browser
checks, install the locked dependencies once with `npm ci --prefix tests --ignore-scripts`.
Choose a fresh output directory beside the checkout, within the current work item.

A few checks need something the machine provides. Without it the case skips or fails while
it runs, and because a skip is never a pass the run fails either way. That is a missing
prerequisite, not a platform exclusion, so provide it and run again:

- Node.js on `PATH`, for the browser group (`ui-controls`). Without it the runner fails that
  group with "Node.js is required".
- bash, for `tests/test_docs_index.py`. On Windows the test uses
  `%ProgramFiles%\Git\bin\bash.exe` when it exists, so `PATH` cannot select the retired WSL
  launcher; otherwise it uses `bash` on `PATH`.
- A published Claude Code binary, for both cases in `tests/test_claude_native_protocol.py`.
  Set `SYLVIA_TEST_CLAUDE_CODE` to the executable's path, or install it at
  `~/.local/bin/claude.exe` (`~/.local/bin/claude` on Linux). The test copies it; Sylvia itself
  never looks there.
- Directory links in the temporary folder, for the outside-root cases in
  `tests/test_fc01_release_exclusions.py` and `tests/test_ready_campaigns.py`. They try a
  directory symlink, then on Windows a junction (`mklink /J`, which needs no extra privilege),
  and skip only when both fail.
- A tmpfs `/dev/shm` (as `/proc/self/mountinfo` reports it), for the Linux case
  `tests/test_safe_file_adapter.py::test_linux_refuses_tmpfs_destinations`.
- The PortAudio shared library on Linux, for
  `tests/test_local_dictation.py::test_model_metadata_and_missing_model_never_start_audio_or_network`.
  The `sounddevice` package loads it from the system there; on Windows its wheel includes it.
- The release codec inputs on Windows, for
  `tests/test_fc01_release_exclusions.py::test_actual_sidecar_arguments_cover_all_synthetic_directory_sentinels`.
  Set `SYLVIA_FFMPEG_LGPL_DIR` to the LGPL FFmpeg DLLs, which
  `src-tauri/scripts/provision_ffmpeg_lgpl.py --work-dir <folder>` downloads from the pinned
  public release into `<folder>/ffmpeg-lgpl`. Set `SYLVIA_TEST_PYAV_LGPL_STAGE` to a new folder
  staged from them with `src-tauri/scripts/stage_pyav_lgpl.py --ffmpeg <DLL folder> --output
  <stage folder> --manifest licenses/dictation/PYAV-LGPL-BUILD.json`.

The runner starts its child Python processes with `PYTHONUTF8=1` and `PYTHONIOENCODING=utf-8`,
so results do not depend on the machine's console encoding. A direct `pytest` run on a machine
without UTF-8 mode (a default Windows install) may need `$env:PYTHONUTF8=1` first.

By default, pytest and the runner materialize the current public Lite source into the
run's own `inputs` directory. The committed legacy package stays available for explicit
compatibility checks. Set `SRD_LITE_CANDIDATE` only to deliberately select another input;
the runner records and guards its exact bytes as well as the checked source.

```powershell
$checks = '..\checks'
$env:PYTHONDONTWRITEBYTECODE=1
# Both standalone pytest and the journey runner use normal plugin autoload.
# Clear the setting if this shell previously used the README's explicit-plugin command.
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD=$null
# Use the current source unless this check deliberately selects a historical package.
$env:SRD_LITE_CANDIDATE=$null

# One focused file, or append ::test_name for one scenario.
.venv/Scripts/python.exe -m pytest -s -p no:cacheprovider tests/test_command_registry.py

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

The runner removes each passed test's scratch folder when the test ends and keeps a failed
test's scratch for diagnosis. To keep passed tests' scratch as well, set
`SYLVIA_RETAIN_SUCCESSFUL_FIXTURES` to `1` (`$env:SYLVIA_RETAIN_SUCCESSFUL_FIXTURES = '1'`, or
`export SYLVIA_RETAIN_SUCCESSFUL_FIXTURES=1`); that takes 40 GB or more for a full run. Unset
or `0` is the default, and any other value stops the runner before it starts. Even with the
default, leave about 20 GB free for a full run's output folder: the measured peak on Linux was
19.5 GB, about 12 GB of it the benchmark group's session data, one copy per worker.

## Run the checks on Linux

Linux runs the same checks with the same runner command, using `.venv/bin/python` in place of
`.venv/Scripts/python.exe`. The first complete run took 2 h 40 min on 16 cores with 15 GB of
memory at `--jobs 4 --case-workers 3`. Keep the checkout and the output folder on ext4, xfs or
btrfs; the saved-file checks need one of those file systems. Cases that run only on Windows are
excluded by platform and listed in `summary.json`. Windows remains the platform for release
builds and for installer and updater qualification.

## Read and retain the result

Use a clean committed head for final qualification. Record its commit, the commands, selection
and reasons, pass/fail/expected-failure counts, elapsed time, and checks not run in the PR.
`summary.json` records the revision, a hash of the checked files and whether source stayed unchanged.
Required-case manifests, XML reports and timing files show what actually ran. The runner rejects
missing cases and duplicate coverage; file inventory alone does not prove case coverage.

Recorded defects execute as strict expected failures and appear separately from passes.
An unexpected pass, unexpected skip, missing case or changed source fails the run. A known
failure does not establish that the affected feature works. Preserve its reproduction and limits.
Cases excluded by platform are counted in `excluded_case_counts` and printed as "Excluded by
platform"; they were not run on this machine, so record them with the checks not run and check
them on their own platform.

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

## Versions, tags and channel

- A release version is plain `X.Y.Z`, for example `0.4.1`. Its tag is `vX.Y.Z`, and its files
  use the same version, such as `sylvia-gm-0.4.1-windows-x86_64-portable.zip`.
- The release channel is declared in code, not read from the version: `__release_channel__` in
  `src/sylvia_gm/__init__.py` and `RELEASE_CHANNEL` in `src-tauri/src/lib.rs`. Both say `alpha`
  today. The release version check fails if they differ.
- While the channel is `alpha`, mark the GitHub release as a Pre-release. The updater treats a
  plain tag marked Pre-release as part of the alpha channel, and an unmarked one as stable.
- The Windows installer version equals the release version.
- The earlier form `X.Y.Z-alpha.N` is still accepted so that past releases such as
  `0.4.0-alpha.1` stay valid. For that form only, the installer version is `X.Y.Z-N`.
- Change the version in every pinned place together: `pyproject.toml`, `uv.lock`,
  `src/sylvia_gm/__init__.py`, `config/config.yaml`, `src-tauri/Cargo.toml`,
  `src-tauri/Cargo.lock` and `src-tauri/tauri.conf.json`.
