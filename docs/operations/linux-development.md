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
export PYTHONDONTWRITEBYTECODE=1 SYLVIA_RETAIN_SUCCESSFUL_FIXTURES=0
unset PYTEST_DISABLE_PLUGIN_AUTOLOAD SRD_LITE_CANDIDATE

# One focused file; append ::test_name for one case.
.venv/bin/python -m pytest -s -p no:cacheprovider tests/test_command_registry.py

# One group, into a fresh empty folder.
.venv/bin/python scripts/check_supported_journeys.py --journey gm-turn --jobs 1 --output-dir ../sylvia-gm-checks/gm-turn-1

# The complete run, into a fresh empty folder.
.venv/bin/python scripts/check_supported_journeys.py --jobs 4 --case-workers 3 --output-dir ../sylvia-gm-checks/full-1
```

Replace `sylvia-gm` in the output paths with your checkout's folder name, so the output lands in
its `-checks` folder. With `SYLVIA_RETAIN_SUCCESSFUL_FIXTURES=0` the runner tells pytest to keep
temporary folders only for failed tests; without it, every passing test's folder is kept too.

Measured complete run: 2 h 40 min on 16 cores with 15 GB of memory, at `--jobs 4
--case-workers 3`, writing about 20 GB to its output folder. `scripts/setup_linux_dev.sh --full-run`
uses those settings with 12 or more cores and scales down on smaller machines.

The result is written to `summary.json` in the output folder, beside each group's XML report and
log. [Read and retain the result](local-checks-and-release.md#read-and-retain-the-result) explains
what to record. The [affected and full selections](local-checks-and-release.md#run-the-checks)
work the same way on Linux, with `.venv/bin/python` in place of `.venv/Scripts/python.exe`.

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
