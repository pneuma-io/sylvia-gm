# Linux development

Linux is a development and test platform for Sylvia GM. This page is for a developer or agent
starting on a new Linux machine. It does not make Linux a supported player platform. Release
builds, the installer and the updater stay on Windows; see
[What stays on Windows](#what-stays-on-windows).

## What you need

- Linux on x86_64 with apt. Tested on Ubuntu 24.04; the setup script warns on anything else
  and continues.
- A normal user account. Tools are installed for that user. Installing apt packages needs
  sudo without a password prompt, or an administrator to run the commands the script prints.
- The checkout, and the sibling `<checkout>-checks` folder that tests and the runner write to,
  on ext4, xfs or btrfs. The saved-file checks refuse other filesystems, including network
  mounts, tmpfs and Windows drives mounted into Linux. Move the checkout if it is elsewhere.
- Disk: one complete run wrote about 20 GB to its output folder. The script warns below 40 GB
  free and `--full-run` refuses to start below 25 GB.
- Memory: the script warns below 8 GB. The measured complete run used a machine with 15 GB.

## Set up with one command

From the checkout:

```bash
scripts/setup_linux_dev.sh
```

It checks each requirement first and skips whatever is already in place, so it is safe to run
again. It then verifies the result with a short smoke test (import the package, run one fast
test file, start headless Chromium once) and prints the commands for the next steps. The pinned
uv, Node.js and Python versions, and every address the script downloads from, are declared at
the top of the script.

| What | Where |
|---|---|
| git, curl, ca-certificates, xz-utils and libportaudio2 (the audio library the dictation checks load), if missing | System, with `sudo apt-get install --no-install-recommends` |
| uv, unless a new enough uv is on `PATH` | `~/.local/bin` |
| Python and the project environment (`dev` and `dictation` extras) | uv's Python folder in your home; `.venv` in the checkout |
| Node.js, unless a new enough Node.js is on `PATH` | `~/.local/share/sylvia-gm-dev`, after checking the archive against the official SHA-256 list |
| The locked Playwright package | `tests/node_modules` (`npm ci`) |
| Chromium for Playwright | Playwright's browser cache in your home |
| Chromium's system libraries, if it cannot start | System, with `sudo` and `playwright install-deps chromium` |

The script never edits shell profiles, git configuration or credentials. When it uses a tool
folder that is not on your `PATH`, it prints the `export PATH=...` line to run in each new shell.

Other modes:

- `--check` changes nothing and prints one line per requirement, `OK` or `MISSING` with what to
  do. It exits non-zero if anything is missing.
- `--dry-run` prints the steps the default mode would take on this machine, without doing them.
- `--full-run` sets up, then runs the complete check run and prints its time and result.

One check also needs a published Claude Code binary to copy, which the script reports but does
not install. Anthropic's installer for Claude Code puts it at `~/.local/bin/claude`, where the
check looks; otherwise set `SYLVIA_TEST_CLAUDE_CODE` to its path. No sign-in is needed: the
check runs the copy against a local scripted server. Without the binary that case skips and
the complete run fails. The prerequisite list in
[Run the checks](local-checks-and-release.md#run-the-checks) explains why.

## Run the checks

In the checkout, after the `export PATH=...` line if the script printed one:

```bash
export PYTHONDONTWRITEBYTECODE=1
unset PYTEST_DISABLE_PLUGIN_AUTOLOAD SRD_LITE_CANDIDATE

# One focused file; append ::test_name for one case.
.venv/bin/python -m pytest -s -p no:cacheprovider tests/test_command_registry.py

# One group, into a fresh empty folder.
.venv/bin/python scripts/check_supported_journeys.py --journey gm-turn --jobs 1 --output-dir ../sylvia-gm-checks/gm-turn-1

# The complete run, into a fresh empty folder.
.venv/bin/python scripts/check_supported_journeys.py --jobs 4 --case-workers 3 --output-dir ../sylvia-gm-checks/full-1
```

Replace `sylvia-gm` in the output paths with your checkout's folder name, so the output lands in
its `-checks` folder. The runner keeps temporary folders only for failed tests;
[Run the checks](local-checks-and-release.md#run-the-checks) says how to keep the rest.

Measured complete run, from a fresh machine with `scripts/setup_linux_dev.sh --full-run`:
2 h 50 min on 16 cores at `--jobs 4 --case-workers 3` (ten worker processes), every required
case passing. Peak memory was 12.9 GB of 15.9 GB, about 1.3 GB per worker, and the output
folder peaked at 18.8 GB. `--full-run` uses those settings with 12 or more cores and scales
down on smaller machines.

The result is written to `summary.json` in the output folder, beside each group's XML report and
log. [Read and retain the result](local-checks-and-release.md#read-and-retain-the-result) explains
what to record. The [affected and full selections](local-checks-and-release.md#run-the-checks)
work the same way on Linux, with `.venv/bin/python` in place of `.venv/Scripts/python.exe`.

## Split a complete run across machines

The complete run is about 22 hours of test time, which one machine works through with
several workers. A machine with fewer cores has fewer workers and takes longer. To shorten
the wall-clock time, run shares of the suite on several machines at once:

1. Prepare one machine with `scripts/setup_linux_dev.sh`, at the commit to be checked.
2. Copy that machine `N` times.
3. On copy `I`, run `scripts/setup_linux_dev.sh --full-run --machine-share I/N`, for
   example `--machine-share 2/6` on the second of six. Each copy collects the whole suite,
   computes the same split, and runs only its share. The browser tests are in one share.
4. Collect the `N` output folders (`full-<time>-share-I-of-N`) onto one machine.
5. Join them:

   ```bash
   .venv/bin/python scripts/merge_machine_shares.py SHARE_FOLDER... --output merged-summary.json
   ```

The merge writes one receipt and prints PASS or FAIL. It exits 0 when every share passed and 1
when the set is complete but a share failed. It refuses, with exit code 2, anything that is
not every share of one run: a missing or repeated share, shares from different commits,
sources, platforms or splits, a share whose source changed while it ran, case lists that
overlap or leave a case out, or the browser tests in no share or in more than one. It never
runs tests. A merged receipt from a valid set is the same evidence as one complete run on
that platform.

To see a split before running it, without running any test:

```bash
.venv/bin/python scripts/check_supported_journeys.py --machine-share 1/6 --list
```

It prints each share's case count, its estimated test time and its longest single case. The
estimates come from `scripts/journey_case_timings.json` and are for scheduling only.

Sizing a share:

- Measured: share 1 of 6 on a machine with 4 cores and 8 GB of memory, four workers, took
  1 h 35 min, with peak memory of 4.8 GB and an output folder of 2.7 GB. Its test time was
  about 244 minutes, so on a small machine allow about one and a half times the share's
  test time divided by its worker count. That covers collecting the suite, which every
  share does first and which takes several minutes.
- A share never takes less than its longest case. The two longest cases take about 25
  minutes each, so no number of machines brings a complete run much below half an hour.
- Allow about 1.3 GB of memory per worker plus the system's own use. `--full-run` chooses
  workers from the core count alone, so on a machine with little memory per core, start
  the runner yourself with a smaller `--jobs` and `--case-workers`.
- Each machine needs room for its own output folder: about 20 GB for a whole run and a few
  gigabytes for a share.

## What is excluded on Linux

Cases marked `runs_on("win32", ...)` cannot run on Linux. The runner reports them as platform
exclusions, not failures, and lists each by exact ID in `summary.json`. The
[platform paragraph](local-checks-and-release.md#choose-the-work-that-needs-checking) explains
the marker and how to record the exclusions. They are checked on Windows with the
[Windows release checklist](windows-release-checklist.md).

## Provider keys on shared or rented machines

The checks need no provider keys. When you run Sylvia itself against a real provider on a machine
you share or rent, pass the key as an environment variable instead of saving it in Settings.
Sylvia reads `SYLVIA_GM_PROVIDERS__<PROVIDER_ID>__API_KEY` (the provider's settings ID in capital
letters, for example `SYLVIA_GM_PROVIDERS__OPENROUTER__API_KEY`), and the environment value takes
precedence over a saved key. The mapping is in `src/sylvia_gm/config/_env.py`.

If you do save a key on Linux, it is protected with a per-user key file kept outside the data
folder. The [architecture contract](../architecture/alpha-architecture-contract.md#131-shared-session-substrate)
describes that protection and its limits: it guards a settings file copied on its own, not a copy
of the whole home folder from an unencrypted disk or another program running as the same user.

## What stays on Windows

Windows remains the platform for release builds, the installer, the updater, the installed-app
checks and the Windows-only test cases. Before each release, work through the
[Windows release checklist](windows-release-checklist.md).
