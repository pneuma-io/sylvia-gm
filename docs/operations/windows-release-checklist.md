# Windows release checklist

Day-to-day development and testing can happen on Linux
([Linux development](linux-development.md)), and the Linux app has its own
[Linux release checklist](linux-release-checklist.md). Windows remains the platform for the
Windows release build, the installer, the updater and the Windows installed-app checks. Work through these steps on the Windows
machine for each release candidate, in order, from the clean committed source selected for the
release. Each step links to the section that explains it; this page does not replace those.

Commands are for PowerShell in the checkout. `$checks` is a fresh output folder beside the
checkout, within the current work item.

## 1. Run the checks on Windows

These cases only run on Windows:

- the cases marked `runs_on("win32", ...)`: the release packaging, NSIS installer, updater,
  update signing and code-signature checks. List them with
  `git grep -n "runs_on(\"win32\"" -- tests`; a Linux run reports each one by exact ID as a
  platform exclusion in its `summary.json`;
- the Windows branches of other tests, such as the Microsoft Edge browser channel in the browser
  tests, the junction fallback for directory links and the Windows saved-credential protection in
  `tests/test_protected_provider_settings.py`.

Run the full configured product qualification
([Run the checks](local-checks-and-release.md#run-the-checks)):

```powershell
$checks = '..\checks'
$env:PYTHONDONTWRITEBYTECODE=1
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD=$null
$env:SRD_LITE_CANDIDATE=$null
.venv/Scripts/python.exe scripts/select_checks.py --full --output "$checks\full-selection.json"
.venv/Scripts/python.exe scripts/check_supported_journeys.py --selection-manifest "$checks\full-selection.json" --jobs 2 --case-workers 1 --scheduler case --output-dir "$checks\full"
```

Then confirm in `summary.json` that only the Linux-only cases were excluded by platform and that
nothing else was skipped:

```powershell
$summary = Get-Content "$checks\full\summary.json" -Raw | ConvertFrom-Json
$summary | Select-Object platform, passed, complete_gate, revision, unchanged_source
$summary.python_case_counts
$summary.excluded_case_counts
$summary.excluded_cases | Where-Object kind -eq 'platform' | Select-Object nodeid, platforms
```

Expect `platform` `win32`, `passed` and `complete_gate` true, `unchanged_source` true and
`unexpected_skips` 0. Every platform exclusion must list `linux` and not `win32`. Historical
exclusions (`kind` `historical`) are described in
[Choose the work that needs checking](local-checks-and-release.md#choose-the-work-that-needs-checking).

To repeat only the files with Windows-specific cases while diagnosing a failure:

```powershell
$env:PYTHONUTF8=1
.venv/Scripts/python.exe -m pytest -s -p no:cacheprovider tests/test_claude_native_protocol.py tests/test_desktop_update_helper_pipe.py tests/test_desktop_update_installed_launch.py tests/test_desktop_update_processes.py tests/test_desktop_update_progress.py tests/test_protected_provider_settings.py
```

## 2. Windows prerequisites for those checks

Install the environment with `uv sync --locked --all-extras` and the browser test packages with
`npm ci --prefix tests --ignore-scripts`. The prerequisites that make a case skip, and so fail the
run, are listed in [Run the checks](local-checks-and-release.md#run-the-checks): Node.js on
`PATH`, Git's bash, a published Claude Code binary (on Windows both cases in
`tests/test_claude_native_protocol.py` need it, including the code-signature case), and directory
links or junctions in the temporary folder. The browser tests use Microsoft Edge on Windows
([Developer tests](../../README.md#developer-tests)).

`tests/test_fc01_release_exclusions.py::test_actual_sidecar_arguments_cover_all_synthetic_directory_sentinels`
also reads `SYLVIA_FFMPEG_LGPL_DIR` (the codec folder from step 3) and
`SYLVIA_TEST_PYAV_LGPL_STAGE`, a verified PyAV stage folder. How to produce that stage folder for
the test: Not documented in this repository.

## 3. Release build

The toolchain (Rust with MSVC, the C++ Build Tools, a Windows SDK, WebView2, the Tauri CLI and the
LGPL FFmpeg DLLs) is listed in [Build from source](../../README.md#build-from-source). The release
inputs are described in
[Development and release checks](../launch-compatibility.md#development-and-release-checks).

In an x64 Visual Studio Developer PowerShell, in a clean checkout of the selected commit:

```powershell
git status --short          # must print nothing; the builder refuses a dirty tree
git rev-parse HEAD          # record this commit
uv sync --locked --all-extras
$env:SYLVIA_FFMPEG_LGPL_DIR = 'C:\path\to\ffmpeg-lgpl\bin'
uv run --locked --all-extras python scripts/check_release_identity.py --forbidden-name-file EXTERNAL_FILE
uv run --locked --all-extras python scripts/build_windows_prerelease.py --dependency-notices-sha256 SHA256 --forbidden-name-file EXTERNAL_FILE
```

`EXTERNAL_FILE` is the external literal-name list, kept outside public source. `SHA256` is the
lowercase SHA-256 pin of the reconciled `dependency-notices.txt`; it is a pin, so take it from the
notice review rather than from whatever file is in the tree. How the notices are reconciled and
where the pin is recorded: Not documented in this repository.

The builder writes the NSIS installer, the portable ZIP and `release-manifest.json` to
`dist\sylvia-gm-<version>-windows-x86_64` unless `--output` names another new folder. It builds
only the NSIS installer; `scripts/qualify_windows_msi.py` qualifies an MSI, which the current
builder does not make. Its `--windows-10` and `--windows-11` options record qualification states
and default to `pending`.

## 4. Installer and updater qualification

Installer: on Windows 11, run the NSIS qualification against the release folder from step 3.

```powershell
uv run --locked --all-extras python scripts/qualify_windows_nsis.py dist\sylvia-gm-<version>-windows-x86_64
```

It installs and uninstalls the manifest-bound installer in an isolated folder, writes a copy of
the release to `<release folder>-qualified` with `windows_11` set to `passed`, and prints a
receipt that includes the source commit and installer SHA-256. No document explains this step;
the command comes from the script's own arguments. How Windows 10 qualification is done: Not
documented in this repository.

Updater: the Windows-only updater cases run in step 1. Signing an update with
`scripts/sign_desktop_update.py` needs the release owner's separate approval, as the script
states. A procedure for qualifying a real update from one installed version to the next: Not
documented in this repository.

## 5. Installed-application pass

Follow [Installed-app verification](quality-preserving-development-process.md#5-installed-app-verification):
install the exact artifact in the selected Windows test environment and confirm its identities,
enable Admin Full Audit before the first tested action, exercise the real provider, campaign,
Builder, persistence, restart and failure paths the active plan selects, fix and repeat only the
affected proof, then hand the exact installed candidate to the owner for visual and release
approval. The final Windows checks listed in
[Development and release checks](../launch-compatibility.md#development-and-release-checks) also
apply: installed and portable coexistence, same-dataset refusal, save and reopen, portable folder
movement and updates, notice delivery, and credential re-entry under another Windows account.

Commands or a qualified runner for this pass: Not documented in this repository.

## 6. Publish

Publishing needs the owner's release approval from step 5. Follow
[Versions, tags and channel](local-checks-and-release.md#versions-tags-and-channel) for the
version, the tag and the file names. While the declared channel is `alpha`, publish the GitHub
release as a Pre-release, even when the tag is plain (`v0.4.1`): the Pre-release mark is what
places a plain tag in the alpha channel for the updater. From 0.4.1 the same release also holds
the Linux files ([Linux release checklist, Publish](linux-release-checklist.md#6-publish)).

A fuller publishing procedure: Not documented in this repository.

## 7. Record

As [Read and retain the result](local-checks-and-release.md#read-and-retain-the-result) and
[From checked source to a release](local-checks-and-release.md#from-checked-source-to-a-release)
describe, record:

- the checked commit (`git rev-parse HEAD`), which the build must also use;
- the commands from each step, and the full selection with its reasons;
- from `summary.json`: passed, failed and expected-failure counts, unexpected skips, the
  platform exclusions and elapsed time;
- checks not run, including the Linux-only cases excluded here and where they were run;
- the build's `release-manifest.json` and the NSIS receipt (source commit, installer SHA-256);
- the installed-app outcomes and remaining gaps; and
- the owner's release approval. Source checks and a merge do not grant it.
