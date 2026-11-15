#!/usr/bin/env bash
# Prepare a Linux machine for Sylvia GM's complete local check run, and optionally run it.
#
#   scripts/setup_linux_dev.sh [--check | --dry-run | --full-run [--machine-share I/N] | --help]
#
# Supported: Linux on x86_64 with apt (tested on Ubuntu 24.04). Tools are installed for the
# current user. sudo is used only for apt packages and never prompts for a password. The
# script does not edit shell profiles; it prints the PATH line to add instead.
# See docs/operations/linux-development.md.

set -euo pipefail

# Pinned versions. Each is declared once here; everything below derives from these.
readonly UV_VERSION="0.11.6"
readonly NODE_VERSION="24.16.0"
readonly PYTHON_VERSION="3.12"
# The Playwright release locked in tests/package-lock.json requires Node 20 or newer.
readonly NODE_MIN_MAJOR=20

# Every address this script downloads from itself.
readonly UV_INSTALLER_URL="https://astral.sh/uv/${UV_VERSION}/install.sh"
readonly NODE_DIST_URL="https://nodejs.org/dist/v${NODE_VERSION}"
# The tools it runs contact these as well:
#   apt-get                    the package mirrors configured in /etc/apt on this machine
#   uv installer               github.com (the uv release archive)
#   uv sync                    github.com (the Python build), pypi.org, files.pythonhosted.org
#   npm ci                     registry.npmjs.org
#   playwright install         Playwright's browser download servers (cdn.playwright.dev)
#   playwright install-deps    the apt mirrors above

readonly WARN_DISK_GB=40
readonly FULL_RUN_DISK_GB=25
readonly WARN_MEMORY_GB=8
readonly SMOKE_TEST="tests/test_command_registry.py"
# libportaudio2 is the audio library the dictation checks load; the rest fetch and unpack tools.
readonly REQUIRED_PACKAGES=(git curl ca-certificates xz-utils libportaudio2)
readonly STEP_COUNT=11

CHECKOUT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
CHECKS_DIR="$(dirname -- "$CHECKOUT")/$(basename -- "$CHECKOUT")-checks"
TESTS_DIR="$CHECKOUT/tests"
VENV_PYTHON="$CHECKOUT/.venv/bin/python"
PLAYWRIGHT_CLI="$TESTS_DIR/node_modules/playwright/cli.js"
NODE_ARCHIVE="node-v${NODE_VERSION}-linux-x64.tar.xz"
readonly CHECKOUT CHECKS_DIR TESTS_DIR VENV_PYTHON PLAYWRIGHT_CLI NODE_ARCHIVE
UV_SYNC_ARGS=(sync --frozen --extra dev --extra dictation --python "$PYTHON_VERSION")
readonly UV_SYNC_ARGS

# Launch headless Chromium once through the locked Playwright and render a page.
readonly CHROMIUM_PROBE_JS='const { chromium } = require("playwright");
chromium.launch({ headless: true, args: ["--disable-gpu"] })
  .then(async (browser) => {
    const page = await browser.newPage();
    await page.setContent("<p>ready</p>");
    await browser.close();
  })
  .catch((error) => {
    console.error(String((error && error.message) || error));
    process.exit(1);
  });'

MODE="setup"
MACHINE_SHARE=""
ORIGINAL_PATH="${PATH:-}"
STEP_NUMBER=0
MISSING=()
ADDED_PATH_DIRS=()
SCRATCH_DIRS=()
MISSING_PACKAGES=()
CREATED_CHECKS_DIR=0
DRY_RUN_STOP=""
UV_DIR=""
TOOLS_DIR=""
NODE_DIR=""
UV_BIN=""
UV_FOUND_VERSION=""
UV_REJECTED=""
NODE_BIN=""
NPM_BIN=""
NODE_FOUND_VERSION=""
NODE_REJECTED=""
PLAYWRIGHT_VERSION=""
CHROMIUM_PROBLEM=""
CHROMIUM_DETAIL=""
PLATFORM_PROBLEM=""
PLATFORM_DESC=""
OS_ID=""
OS_VERSION_ID=""
OS_NAME=""
FS_PROBLEM=""
FS_DESC=""
SCRATCH=""
JOBS=1
CASE_WORKERS=1

usage() {
  cat <<'USAGE'
Usage: scripts/setup_linux_dev.sh [--check | --dry-run | --full-run [--machine-share I/N] | --help]

Prepare this Linux machine for Sylvia GM's complete local check run.

  (no option)  Set up whatever is missing, verify the result, and print how to run the checks.
               Safe to run again: steps that are already satisfied are skipped.
  --check      Verify only. Print one line per requirement and exit non-zero if any is missing.
  --dry-run    Print the steps the default mode would take on this machine, without doing them.
  --full-run   Set up, then run the complete check run and report its time and result.
               Exits with the check runner's exit code.
  --machine-share I/N
               With --full-run: run only share I of N of the complete run on this machine.
               Start the other shares on copies of this machine, then join the N output
               folders with scripts/merge_machine_shares.py.
  --help       Show this help.

Supported: Linux on x86_64 with apt, tested on Ubuntu 24.04. Run as a normal user, not root.
See docs/operations/linux-development.md.
USAGE
}

# Output helpers: step headers and one line per requirement.
step() {
  STEP_NUMBER=$((STEP_NUMBER + 1))
  printf '\n==> [%d/%d] %s\n' "$STEP_NUMBER" "$STEP_COUNT" "$1"
}
info() {
  if [ -z "$*" ]; then
    printf '\n'
  else
    printf '    %s\n' "$*"
  fi
}
ok() { printf '    OK       %s\n' "$*"; }
warn() { printf '    WARN     %s\n' "$*"; }
note() { printf '    NOTE     %s\n' "$*"; }
would() { printf '    WOULD    %s\n' "$*"; }
missing() {
  local name=$1
  shift
  MISSING+=("$name")
  printf '    MISSING  %s: %s\n' "$name" "$*"
}

# Stop the setup with a clear final line.
die() {
  printf '\nNOT READY: %s\n' "$*" >&2
  exit 1
}

join_by() {
  local separator=$1 out="" item
  shift
  for item in "$@"; do
    out+="${out:+$separator}$item"
  done
  printf '%s' "$out"
}

# Shell-quote a command so it can be copied and run exactly.
quote_command() {
  local out="" arg
  for arg in "$@"; do
    out+="$(printf '%q' "$arg") "
  done
  printf '%s' "${out% }"
}

# Remove this run's temporary folders, and an empty checks folder this run created.
cleanup() {
  local dir
  for dir in "${SCRATCH_DIRS[@]}"; do
    if [ -n "$dir" ] && [ -d "$dir" ]; then
      rm -rf -- "$dir"
    fi
  done
  if [ "$CREATED_CHECKS_DIR" = 1 ]; then
    rmdir -- "$CHECKS_DIR" 2>/dev/null || true
  fi
}

# Create a temporary folder under a parent the script is allowed to write to.
new_scratch() {
  local dir
  dir=$(mktemp -d "$1/setup-tmp.XXXXXX")
  SCRATCH_DIRS+=("$dir")
  SCRATCH="$dir"
}

ensure_checks_dir() {
  if [ ! -d "$CHECKS_DIR" ]; then
    mkdir -p -- "$CHECKS_DIR"
    CREATED_CHECKS_DIR=1
  fi
}

# Download one pinned address to a file, refusing anything but HTTPS (also on redirects).
download() {
  curl --fail --location --silent --show-error --retry 3 \
    --proto '=https' --proto-redir '=https' --tlsv1.2 \
    --output "$2" "$1" || die "Could not download $1."
}

# True when version $1 is at least version $2.
version_at_least() {
  [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n 1)" = "$2" ]
}

# Put a tool folder first on PATH for this run, and remember it for the printed export line.
use_tool_dir() {
  local dir=$1
  case ":$PATH:" in
    *":$dir:"*) ;;
    *) PATH="$dir:$PATH" ;;
  esac
  case ":$ORIGINAL_PATH:" in
    *":$dir:"*) return 0 ;;
  esac
  local existing
  for existing in "${ADDED_PATH_DIRS[@]}"; do
    [ "$existing" = "$dir" ] && return 0
  done
  ADDED_PATH_DIRS+=("$dir")
}

# Is a folder on the PATH this script was started with?
on_original_path() {
  case ":$ORIGINAL_PATH:" in
    *":$1:"*) return 0 ;;
  esac
  return 1
}

# ---------------------------------------------------------------------------------------------
# Probes. Each only reads the machine and reports through its return status and globals.
# ---------------------------------------------------------------------------------------------

read_os_release() {
  local key value
  [ -r /etc/os-release ] || return 0
  while IFS='=' read -r key value || [ -n "$key" ]; do
    value=${value#\"}
    value=${value%\"}
    case "$key" in
      ID) OS_ID=$value ;;
      VERSION_ID) OS_VERSION_ID=$value ;;
      PRETTY_NAME) OS_NAME=$value ;;
    esac
  done </etc/os-release
}

probe_platform() {
  local kernel arch
  kernel=$(uname -s 2>/dev/null) || kernel="unknown"
  arch=$(uname -m 2>/dev/null) || arch="unknown"
  local supported="Supported: Linux on x86_64 with apt (tested on Ubuntu 24.04)."
  if [ "$kernel" != "Linux" ]; then
    PLATFORM_PROBLEM="This machine runs $kernel. $supported"
    return 1
  fi
  if [ "$arch" != "x86_64" ]; then
    PLATFORM_PROBLEM="This machine is $arch. $supported"
    return 1
  fi
  if ! command -v apt-get >/dev/null 2>&1 || ! command -v dpkg-query >/dev/null 2>&1; then
    PLATFORM_PROBLEM="This Linux system has no apt-get or dpkg-query. $supported"
    return 1
  fi
  read_os_release
  PLATFORM_DESC="${OS_NAME:-unknown distribution} (${OS_ID:-?} ${OS_VERSION_ID:-?}), $arch"
  return 0
}

is_tested_distribution() {
  [ "$OS_ID" = "ubuntu" ] && [ "$OS_VERSION_ID" = "24.04" ]
}

is_root() {
  [ "$(id -u)" = "0" ]
}

home_is_usable() {
  [ -n "${HOME:-}" ] && [ -d "$HOME" ] && [ -w "$HOME" ]
}

filesystem_type() {
  local type
  type=$(findmnt -no FSTYPE -T "$1" 2>/dev/null | head -n 1) || type=""
  printf '%s' "${type:-unknown}"
}

# The saved-file checks admit only these (src/sylvia_gm/infrastructure/safe_file.py).
probe_filesystem() {
  local checkout_type output_type output_target=$CHECKS_DIR
  FS_PROBLEM=""
  [ -d "$output_target" ] || output_target=$(dirname -- "$CHECKOUT")
  checkout_type=$(filesystem_type "$CHECKOUT")
  output_type=$(filesystem_type "$output_target")
  FS_DESC="checkout: $checkout_type; check output folder: $output_type"
  local type
  for type in "$checkout_type" "$output_type"; do
    case "$type" in
      ext4 | xfs | btrfs) ;;
      *)
        FS_PROBLEM="The checkout or its check output folder is on '$type'. The saved-file checks need ext4, xfs or btrfs; move the checkout (and its sibling $(basename -- "$CHECKS_DIR") folder) to one of those."
        return 1
        ;;
    esac
  done
  return 0
}

# Free space in KiB at the nearest existing folder.
free_kb() {
  local path=$1
  while [ ! -d "$path" ]; do
    path=$(dirname -- "$path")
  done
  df -Pk -- "$path" | awk 'NR == 2 { print $4 }'
}

memory_kb() {
  awk '/^MemTotal:/ { print $2 }' /proc/meminfo
}

as_gb() {
  awk -v kb="$1" 'BEGIN { printf "%.1f", kb / 1048576 }'
}

collect_missing_packages() {
  MISSING_PACKAGES=()
  local package status
  for package in "${REQUIRED_PACKAGES[@]}"; do
    status=$(dpkg-query -W -f='${Status}' "$package" 2>/dev/null) || status=""
    if [ "$status" != "install ok installed" ]; then
      MISSING_PACKAGES+=("$package")
    fi
  done
}

# uv on PATH if new enough, else the copy this script installs.
find_uv() {
  UV_BIN=""
  UV_REJECTED=""
  local candidate out version
  for candidate in "$(command -v uv 2>/dev/null || true)" "$UV_DIR/uv"; do
    if [ -z "$candidate" ] || [ ! -x "$candidate" ]; then
      continue
    fi
    out=$("$candidate" --version 2>/dev/null) || continue
    read -r _ version _ <<<"$out"
    if [ -n "$version" ] && version_at_least "$version" "$UV_VERSION"; then
      UV_BIN=$candidate
      UV_FOUND_VERSION=$version
      return 0
    fi
    UV_REJECTED="$candidate is uv ${version:-of unknown version}"
  done
  return 1
}

probe_python_env() {
  [ -n "$UV_BIN" ] && [ -x "$VENV_PYTHON" ] || return 1
  (cd -- "$CHECKOUT" && "$UV_BIN" "${UV_SYNC_ARGS[@]}" --check --offline --no-python-downloads) \
    >/dev/null 2>&1
}

# node on PATH if its major version is new enough, else the copy this script installs.
find_node() {
  NODE_BIN=""
  NPM_BIN=""
  NODE_REJECTED=""
  local candidate version major npm
  for candidate in "$(command -v node 2>/dev/null || true)" "$NODE_DIR/bin/node"; do
    if [ -z "$candidate" ] || [ ! -x "$candidate" ]; then
      continue
    fi
    version=$("$candidate" --version 2>/dev/null) || continue
    version=${version#v}
    major=${version%%.*}
    case "$major" in
      '' | *[!0-9]*) continue ;;
    esac
    if [ "$major" -lt "$NODE_MIN_MAJOR" ]; then
      NODE_REJECTED="$candidate is Node $version"
      continue
    fi
    npm="$(dirname -- "$candidate")/npm"
    if [ ! -x "$npm" ]; then
      npm=$(command -v npm 2>/dev/null || true)
    fi
    if [ -z "$npm" ]; then
      NODE_REJECTED="$candidate has no npm beside it"
      continue
    fi
    NODE_BIN=$candidate
    NPM_BIN=$npm
    NODE_FOUND_VERSION=$version
    return 0
  done
  return 1
}

# The locked Playwright package is installed in tests/node_modules.
probe_npm_packages() {
  [ -n "$NODE_BIN" ] || return 1
  local expected actual
  expected=$(cd -- "$TESTS_DIR" &&
    "$NODE_BIN" -p 'require("./package.json").devDependencies.playwright' 2>/dev/null) || return 1
  PLAYWRIGHT_VERSION=$expected
  [ -f "$PLAYWRIGHT_CLI" ] || return 1
  actual=$(cd -- "$TESTS_DIR" &&
    "$NODE_BIN" -p 'require("./node_modules/playwright/package.json").version' 2>/dev/null) ||
    return 1
  [ "$expected" = "$actual" ]
}

# Launch headless Chromium. On failure CHROMIUM_PROBLEM is "download" or "libraries".
probe_chromium() {
  CHROMIUM_PROBLEM=""
  CHROMIUM_DETAIL=""
  if [ -z "$NODE_BIN" ] || [ ! -f "$PLAYWRIGHT_CLI" ]; then
    CHROMIUM_PROBLEM="prerequisites"
    CHROMIUM_DETAIL="needs Node.js and the browser test packages first"
    return 1
  fi
  # Like the browser tests, the throwaway browser profile lives in the system temporary
  # folder; Playwright removes it when the browser closes.
  local output status=0
  output=$(cd -- "$TESTS_DIR" && timeout 180 "$NODE_BIN" -e "$CHROMIUM_PROBE_JS" 2>&1) ||
    status=$?
  [ "$status" -eq 0 ] && return 0
  CHROMIUM_DETAIL=$(printf '%s\n' "$output" | grep -m 1 -v '^[[:space:]]*$' || true)
  case "$output" in
    *"install-deps"* | *"missing dependencies"* | *"shared libraries"*) CHROMIUM_PROBLEM="libraries" ;;
    *"Executable doesn"* | *"playwright install"*) CHROMIUM_PROBLEM="download" ;;
    *) CHROMIUM_PROBLEM="libraries" ;;
  esac
  return 1
}

# Read-only: does the locked Playwright already have its Chromium build on disk?
chromium_downloaded() {
  [ -n "$NODE_BIN" ] && [ -f "$PLAYWRIGHT_CLI" ] || return 1
  (cd -- "$TESTS_DIR" && "$NODE_BIN" -e \
    'process.exit(require("fs").existsSync(require("playwright").chromium.executablePath()) ? 0 : 1)' \
    >/dev/null 2>&1)
}

# The place tests/test_claude_native_protocol.py looks for a published Claude Code binary.
claude_binary_path() {
  if [ -n "${SYLVIA_TEST_CLAUDE_CODE:-}" ]; then
    printf '%s' "$SYLVIA_TEST_CLAUDE_CODE"
  else
    printf '%s' "${HOME:-/nonexistent}/.local/bin/claude"
  fi
}

claude_binary_hint() {
  printf '%s' "tests/test_claude_native_protocol.py copies a published Claude Code binary from \$SYLVIA_TEST_CLAUDE_CODE, or from ~/.local/bin/claude when that variable is unset. Without it that case skips, and the complete run fails. This script does not install it."
}

choose_workers() {
  local cores best=0 jobs workers
  cores=$(nproc 2>/dev/null) || cores=1
  JOBS=1
  CASE_WORKERS=1
  if [ "$cores" -ge 12 ]; then
    JOBS=4
    CASE_WORKERS=3
    return 0
  fi
  for jobs in 4 3 2 1; do
    workers=$((cores / jobs))
    [ "$workers" -gt 3 ] && workers=3
    [ "$workers" -ge 1 ] || continue
    if [ $((jobs * workers)) -gt "$best" ]; then
      best=$((jobs * workers))
      JOBS=$jobs
      CASE_WORKERS=$workers
    fi
  done
}

# Print the commands an administrator must run, then stop.
stop_for_admin() {
  local purpose=$1
  shift
  printf '\n    %s needs administrator rights, and sudo cannot run here without a password.\n' "$purpose"
  printf '    Ask an administrator to run:\n\n'
  local line
  for line in "$@"; do
    printf '        %s\n' "$line"
  done
  printf '\n    Then run this script again.\n'
  die "$purpose is waiting for an administrator"
}

can_sudo() {
  command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null
}

apt_admin_lines() {
  printf '%s\n' "sudo apt-get update" \
    "sudo apt-get install -y --no-install-recommends ${MISSING_PACKAGES[*]}"
}

apt_admin_command() {
  printf '%s' "sudo apt-get update && sudo apt-get install -y --no-install-recommends ${MISSING_PACKAGES[*]}"
}

install_deps_command() {
  quote_command sudo env DEBIAN_FRONTEND=noninteractive "$NODE_BIN" "$PLAYWRIGHT_CLI" \
    install-deps chromium
}

# ---------------------------------------------------------------------------------------------
# The requirement list shared by --check and the Verify step.
# ---------------------------------------------------------------------------------------------

check_requirements() {
  ok "Platform: $PLATFORM_DESC"
  if ! is_tested_distribution; then
    warn "Tested on Ubuntu 24.04 only; continuing on ${OS_NAME:-this distribution}."
  fi
  if is_root; then
    missing "Normal user" "running as root. Run as the user who will develop; sudo is used only for apt."
  else
    ok "Normal user: $(id -un)"
  fi
  if home_is_usable; then
    ok "Home folder: $HOME"
  else
    missing "Home folder" "HOME is unset or not a writable folder."
  fi

  if probe_filesystem; then
    ok "Filesystem: $FS_DESC"
  else
    missing "Filesystem" "$FS_PROBLEM"
  fi

  report_disk_and_memory

  collect_missing_packages
  if [ "${#MISSING_PACKAGES[@]}" -eq 0 ]; then
    ok "System packages: ${REQUIRED_PACKAGES[*]}"
  else
    missing "System packages" "${MISSING_PACKAGES[*]} not installed. Run this script, or have an administrator run: $(apt_admin_command)"
  fi

  if find_uv; then
    ok "uv $UV_FOUND_VERSION: $UV_BIN$(on_original_path "$(dirname -- "$UV_BIN")" || printf ' (not on PATH; see the export line below)')"
    use_tool_dir "$(dirname -- "$UV_BIN")"
  else
    missing "uv" "uv $UV_VERSION or newer not found${UV_REJECTED:+ ($UV_REJECTED)}. Run this script to install uv $UV_VERSION into $UV_DIR."
  fi

  if probe_python_env; then
    ok "Python environment: $CHECKOUT/.venv matches uv.lock (dev and dictation extras)"
  elif [ -z "$UV_BIN" ]; then
    missing "Python environment" "cannot be checked without uv. Run this script."
  else
    missing "Python environment" "$CHECKOUT/.venv is missing or out of date. Run this script (it runs: uv $(quote_command "${UV_SYNC_ARGS[@]}"))."
  fi

  if find_node; then
    ok "Node.js $NODE_FOUND_VERSION: $NODE_BIN$(on_original_path "$(dirname -- "$NODE_BIN")" || printf ' (not on PATH; see the export line below)')"
    use_tool_dir "$(dirname -- "$NODE_BIN")"
  else
    missing "Node.js" "Node.js $NODE_MIN_MAJOR or newer with npm not found${NODE_REJECTED:+ ($NODE_REJECTED)}. Run this script to install Node $NODE_VERSION into $NODE_DIR."
  fi

  if probe_npm_packages; then
    ok "Browser test packages: Playwright $PLAYWRIGHT_VERSION in tests/node_modules"
  elif [ -z "$NODE_BIN" ]; then
    missing "Browser test packages" "cannot be checked without Node.js. Run this script."
  else
    missing "Browser test packages" "tests/node_modules does not hold the locked Playwright. Run this script (it runs npm ci in tests/)."
  fi

  if probe_chromium; then
    ok "Headless Chromium: starts and renders a page through Playwright"
  else
    case "$CHROMIUM_PROBLEM" in
      prerequisites) missing "Headless Chromium" "$CHROMIUM_DETAIL." ;;
      download) missing "Headless Chromium" "not downloaded. Run this script (it runs playwright install chromium)." ;;
      *) missing "Headless Chromium" "does not start (${CHROMIUM_DETAIL:-no message}). Its system libraries are probably missing; run this script, or have an administrator run: $(install_deps_command)" ;;
    esac
  fi

  report_claude_binary
}

report_disk_and_memory() {
  local free memory
  free=$(free_kb "$CHECKS_DIR")
  memory=$(memory_kb)
  if [ "$free" -lt $((WARN_DISK_GB * 1048576)) ]; then
    warn "Disk: $(as_gb "$free") GB free for the checkout and check output; below $WARN_DISK_GB GB. One complete run writes about 20 GB."
  else
    ok "Disk: $(as_gb "$free") GB free for the checkout and check output"
  fi
  if [ "$memory" -lt $((WARN_MEMORY_GB * 1048576)) ]; then
    warn "Memory: $(as_gb "$memory") GB; below $WARN_MEMORY_GB GB, the complete run may be slow or fail."
  else
    ok "Memory: $(as_gb "$memory") GB"
  fi
}

report_claude_binary() {
  local path
  path=$(claude_binary_path)
  if [ -f "$path" ]; then
    ok "Claude Code binary for one check: $path"
  else
    note "Claude Code binary not found at $path. $(claude_binary_hint)"
  fi
}

# ---------------------------------------------------------------------------------------------
# Setup steps. Each checks first and skips when already satisfied.
# ---------------------------------------------------------------------------------------------

require_preconditions() {
  if ! probe_platform; then
    die "$PLATFORM_PROBLEM"
  fi
  ok "Platform: $PLATFORM_DESC"
  if ! is_tested_distribution; then
    warn "Tested on Ubuntu 24.04 only; continuing on ${OS_NAME:-this distribution}."
  fi
  if is_root; then
    die "Running as root. Run this script as the user who will develop; it uses sudo only for apt packages."
  fi
  if ! home_is_usable; then
    die "HOME is unset or not a writable folder; the tools are installed under it."
  fi
  ok "User $(id -un), home $HOME"
}

require_filesystem() {
  if ! probe_filesystem; then
    die "$FS_PROBLEM"
  fi
  ok "Filesystem: $FS_DESC"
}

ensure_system_packages() {
  collect_missing_packages
  if [ "${#MISSING_PACKAGES[@]}" -eq 0 ]; then
    ok "Already installed: ${REQUIRED_PACKAGES[*]}"
    return 0
  fi
  info "Missing: ${MISSING_PACKAGES[*]}"
  if ! can_sudo; then
    local lines=()
    mapfile -t lines < <(apt_admin_lines)
    stop_for_admin "Installing ${MISSING_PACKAGES[*]}" "${lines[@]}"
  fi
  sudo -n env DEBIAN_FRONTEND=noninteractive apt-get update || die "apt-get update failed."
  sudo -n env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    "${MISSING_PACKAGES[@]}" || die "apt-get install failed."
  collect_missing_packages
  if [ "${#MISSING_PACKAGES[@]}" -ne 0 ]; then
    die "apt-get finished but these are still not installed: ${MISSING_PACKAGES[*]}"
  fi
  ok "Installed: ${REQUIRED_PACKAGES[*]}"
}

ensure_uv() {
  if find_uv; then
    ok "Using uv $UV_FOUND_VERSION at $UV_BIN"
    use_tool_dir "$(dirname -- "$UV_BIN")"
    return 0
  fi
  [ -z "$UV_REJECTED" ] || info "Found $UV_REJECTED, older than $UV_VERSION."
  info "Installing uv $UV_VERSION into $UV_DIR from $UV_INSTALLER_URL"
  mkdir -p -- "$UV_DIR" "$TOOLS_DIR"
  new_scratch "$TOOLS_DIR"
  download "$UV_INSTALLER_URL" "$SCRATCH/uv-installer.sh"
  env UV_INSTALL_DIR="$UV_DIR" UV_NO_MODIFY_PATH=1 INSTALLER_NO_MODIFY_PATH=1 \
    sh "$SCRATCH/uv-installer.sh" || die "The uv $UV_VERSION installer failed."
  rm -rf -- "$SCRATCH"
  if ! find_uv || [ "$UV_BIN" != "$UV_DIR/uv" ]; then
    die "The uv installer finished, but $UV_DIR/uv is not uv $UV_VERSION or newer."
  fi
  ok "Installed uv $UV_FOUND_VERSION at $UV_BIN"
  use_tool_dir "$UV_DIR"
}

ensure_python_env() {
  if probe_python_env; then
    ok "$CHECKOUT/.venv already matches uv.lock"
    return 0
  fi
  info "Running: uv $(quote_command "${UV_SYNC_ARGS[@]}")"
  (cd -- "$CHECKOUT" && "$UV_BIN" "${UV_SYNC_ARGS[@]}") || die "uv sync failed."
  if ! probe_python_env; then
    die "uv sync finished, but $CHECKOUT/.venv still does not match uv.lock."
  fi
  ok "$CHECKOUT/.venv matches uv.lock"
}

ensure_node() {
  if find_node; then
    ok "Using Node.js $NODE_FOUND_VERSION at $NODE_BIN"
    use_tool_dir "$(dirname -- "$NODE_BIN")"
    return 0
  fi
  [ -z "$NODE_REJECTED" ] || info "Found $NODE_REJECTED; Node.js $NODE_MIN_MAJOR or newer with npm is needed."
  if [ -e "$NODE_DIR" ]; then
    die "$NODE_DIR exists but does not hold a working Node $NODE_VERSION. Remove it and run this script again."
  fi
  info "Installing Node $NODE_VERSION into $NODE_DIR from $NODE_DIST_URL"
  mkdir -p -- "$TOOLS_DIR"
  new_scratch "$TOOLS_DIR"
  download "$NODE_DIST_URL/$NODE_ARCHIVE" "$SCRATCH/$NODE_ARCHIVE"
  download "$NODE_DIST_URL/SHASUMS256.txt" "$SCRATCH/SHASUMS256.txt"
  local expected actual
  expected=$(awk -v name="$NODE_ARCHIVE" \
    '{ file = $2; sub(/^\*/, "", file) } file == name { print $1 }' "$SCRATCH/SHASUMS256.txt")
  if [ "${#expected}" -ne 64 ]; then
    die "SHASUMS256.txt from $NODE_DIST_URL has no single SHA-256 for $NODE_ARCHIVE."
  fi
  actual=$(sha256sum -- "$SCRATCH/$NODE_ARCHIVE" | awk '{ print $1 }')
  if [ "$actual" != "$expected" ]; then
    die "SHA-256 mismatch for $NODE_ARCHIVE: expected $expected, downloaded $actual. Nothing was unpacked."
  fi
  ok "SHA-256 of $NODE_ARCHIVE matches SHASUMS256.txt"
  tar -xJf "$SCRATCH/$NODE_ARCHIVE" -C "$SCRATCH" || die "Could not unpack $NODE_ARCHIVE."
  local unpacked="$SCRATCH/node-v${NODE_VERSION}-linux-x64"
  if [ ! -x "$unpacked/bin/node" ]; then
    die "The Node archive did not contain bin/node."
  fi
  mv -- "$unpacked" "$NODE_DIR"
  rm -rf -- "$SCRATCH"
  if ! find_node || [ "$NODE_BIN" != "$NODE_DIR/bin/node" ]; then
    die "Node $NODE_VERSION was unpacked into $NODE_DIR, but it does not run."
  fi
  ok "Installed Node.js $NODE_FOUND_VERSION at $NODE_BIN"
  use_tool_dir "$NODE_DIR/bin"
}

ensure_browser_dependencies() {
  if probe_npm_packages; then
    ok "Playwright $PLAYWRIGHT_VERSION already installed in tests/node_modules"
  else
    info "Running: npm ci --ignore-scripts in tests/"
    (cd -- "$TESTS_DIR" &&
      NPM_CONFIG_UPDATE_NOTIFIER=false "$NPM_BIN" ci --ignore-scripts --no-audit --no-fund) ||
      die "npm ci in tests/ failed."
    if ! probe_npm_packages; then
      die "npm ci finished, but tests/node_modules does not hold the locked Playwright."
    fi
    ok "Installed Playwright $PLAYWRIGHT_VERSION in tests/node_modules"
  fi

  if probe_chromium; then
    ok "Headless Chromium already starts"
    return 0
  fi
  if [ "$CHROMIUM_PROBLEM" = "download" ]; then
    info "Downloading Chromium for Playwright $PLAYWRIGHT_VERSION"
    (cd -- "$TESTS_DIR" && "$NODE_BIN" "$PLAYWRIGHT_CLI" install chromium) ||
      die "playwright install chromium failed."
    if probe_chromium; then
      ok "Headless Chromium starts"
      return 0
    fi
    if [ "$CHROMIUM_PROBLEM" = "download" ]; then
      die "Chromium is still missing after playwright install chromium: $CHROMIUM_DETAIL"
    fi
  fi
  info "Chromium does not start (${CHROMIUM_DETAIL:-no message}); installing its system libraries."
  if ! can_sudo; then
    stop_for_admin "Installing Chromium's system libraries" "$(install_deps_command)"
  fi
  sudo -n env DEBIAN_FRONTEND=noninteractive "$NODE_BIN" "$PLAYWRIGHT_CLI" install-deps chromium ||
    die "playwright install-deps chromium failed."
  if ! probe_chromium; then
    die "Headless Chromium still does not start: ${CHROMIUM_DETAIL:-no message}"
  fi
  ok "Headless Chromium starts"
}

verify() {
  MISSING=()
  check_requirements
  if [ "${#MISSING[@]}" -ne 0 ]; then
    die "still missing after setup: $(join_by ', ' "${MISSING[@]}")"
  fi
  info "Smoke test: importing sylvia_gm"
  (cd -- "$CHECKOUT" && "$VENV_PYTHON" -c 'import sylvia_gm') ||
    die "The sylvia_gm package does not import from $CHECKOUT/.venv."
  info "Smoke test: $SMOKE_TEST"
  (cd -- "$CHECKOUT" &&
    env -u PYTEST_DISABLE_PLUGIN_AUTOLOAD -u SRD_LITE_CANDIDATE \
      PYTHONDONTWRITEBYTECODE=1 SYLVIA_RETAIN_SUCCESSFUL_FIXTURES=0 \
      "$VENV_PYTHON" -m pytest -q -p no:cacheprovider "$SMOKE_TEST") ||
    die "The smoke test $SMOKE_TEST failed."
  ok "Smoke test passed: package import, $SMOKE_TEST, headless Chromium"
}

print_path_line() {
  if [ "${#ADDED_PATH_DIRS[@]}" -eq 0 ]; then
    return 0
  fi
  local joined
  joined=$(join_by ':' "${ADDED_PATH_DIRS[@]}")
  info "This script does not edit shell profiles. In each new shell, first run:"
  info ""
  info "  export PATH=$(printf '%q' "$joined"):\"\$PATH\""
  info ""
}

print_next_steps() {
  choose_workers
  local stamp
  stamp=$(date +%Y%m%d-%H%M%S)
  print_path_line
  info "Then, in the checkout:"
  info ""
  info "  cd $(printf '%q' "$CHECKOUT")"
  info "  export PYTHONDONTWRITEBYTECODE=1 SYLVIA_RETAIN_SUCCESSFUL_FIXTURES=0"
  info "  unset PYTEST_DISABLE_PLUGIN_AUTOLOAD SRD_LITE_CANDIDATE"
  info ""
  info "Focused check (one file; append ::test_name for one case):"
  info "  .venv/bin/python -m pytest -s -p no:cacheprovider $SMOKE_TEST"
  info ""
  info "Complete run (measured at 2 h 40 min on 16 cores; its output needs about 20 GB):"
  info "  $(quote_command .venv/bin/python scripts/check_supported_journeys.py --jobs "$JOBS" --case-workers "$CASE_WORKERS" --output-dir "$CHECKS_DIR/full-$stamp")"
  info ""
  info "Or let this script run it and report the time and result:"
  info "  scripts/setup_linux_dev.sh --full-run"
}

run_full() {
  printf '\n==> Complete check run\n'
  ensure_checks_dir
  local free
  free=$(free_kb "$CHECKS_DIR")
  if [ "$free" -lt $((FULL_RUN_DISK_GB * 1048576)) ]; then
    die "Only $(as_gb "$free") GB free at $CHECKS_DIR. The complete run needs at least $FULL_RUN_DISK_GB GB for its output; free some space first."
  fi
  if [ ! -f "$(claude_binary_path)" ]; then
    warn "No Claude Code binary at $(claude_binary_path). $(claude_binary_hint)"
  fi
  choose_workers
  local output_dir
  output_dir="$CHECKS_DIR/full-$(date +%Y%m%d-%H%M%S)"
  if [ -n "$MACHINE_SHARE" ]; then
    output_dir+="-share-${MACHINE_SHARE%/*}-of-${MACHINE_SHARE#*/}"
  fi
  mkdir -- "$output_dir" || die "Could not create a new empty output folder $output_dir."
  local command=("$VENV_PYTHON" scripts/check_supported_journeys.py --jobs "$JOBS"
    --case-workers "$CASE_WORKERS" --output-dir "$output_dir")
  if [ -n "$MACHINE_SHARE" ]; then
    command+=(--machine-share "$MACHINE_SHARE")
  fi
  info "Output: $output_dir"
  info "Running: $(quote_command "${command[@]}")"
  local started=$SECONDS status=0
  (cd -- "$CHECKOUT" &&
    env -u PYTEST_DISABLE_PLUGIN_AUTOLOAD -u SRD_LITE_CANDIDATE \
      PYTHONDONTWRITEBYTECODE=1 SYLVIA_RETAIN_SUCCESSFUL_FIXTURES=0 \
      "${command[@]}") || status=$?
  local elapsed=$((SECONDS - started))
  local result="PASS" what="Complete check run"
  [ "$status" -eq 0 ] || result="FAIL"
  if [ -n "$MACHINE_SHARE" ]; then
    what="Share ${MACHINE_SHARE%/*} of ${MACHINE_SHARE#*/} of the complete check run"
  fi
  printf '\n%s: %s (runner exit code %d) after %dh %02dm %02ds.\n' \
    "$what" "$result" "$status" $((elapsed / 3600)) $((elapsed % 3600 / 60)) $((elapsed % 60))
  printf 'Result: %s\n' "$output_dir/summary.json"
  if [ -n "$MACHINE_SHARE" ]; then
    printf 'Join all %s output folders with scripts/merge_machine_shares.py.\n' "${MACHINE_SHARE#*/}"
  fi
  exit "$status"
}

run_setup() {
  step "Preconditions"
  require_preconditions
  step "Filesystem"
  require_filesystem
  step "Disk and memory"
  report_disk_and_memory
  step "System packages"
  ensure_system_packages
  step "uv"
  ensure_uv
  step "Python environment"
  ensure_python_env
  step "Node.js"
  ensure_node
  step "Browser test dependencies"
  ensure_browser_dependencies
  step "Claude Code binary"
  report_claude_binary
  step "Verify"
  verify
  step "Next steps"
  print_next_steps
  printf '\nREADY: %s can start the complete local check run.\n' "$CHECKOUT"
}

run_check() {
  printf 'Checking Sylvia GM Linux development requirements for %s\n\n' "$CHECKOUT"
  if ! probe_platform; then
    missing "Platform" "$PLATFORM_PROBLEM"
    die "unsupported platform"
  fi
  check_requirements
  print_path_line
  if [ "${#MISSING[@]}" -ne 0 ]; then
    die "missing: $(join_by ', ' "${MISSING[@]}"). Each MISSING line above says what to do."
  fi
  printf '\nREADY: every requirement is met.\n'
}

# Record where the default mode would stop first, and keep listing the later steps.
dry_stop() {
  would "stop here: $1"
  [ -n "$DRY_RUN_STOP" ] || DRY_RUN_STOP="step $STEP_NUMBER ($2): $1"
}

run_dry_run() {
  printf 'Dry run for %s: the steps the default mode would take. Nothing is changed.\n' "$CHECKOUT"
  step "Preconditions"
  if ! probe_platform; then
    would "stop here: $PLATFORM_PROBLEM"
    die "$PLATFORM_PROBLEM"
  fi
  ok "Platform: $PLATFORM_DESC"
  is_tested_distribution || warn "Tested on Ubuntu 24.04 only; would continue on ${OS_NAME:-this distribution}."
  if is_root; then
    dry_stop "running as root; run as the user who will develop." "Preconditions"
  elif ! home_is_usable; then
    dry_stop "HOME is unset or not a writable folder." "Preconditions"
  else
    ok "User $(id -un), home $HOME"
  fi

  step "Filesystem"
  if probe_filesystem; then
    ok "Filesystem: $FS_DESC"
  else
    dry_stop "$FS_PROBLEM" "Filesystem"
  fi

  step "Disk and memory"
  report_disk_and_memory

  step "System packages"
  collect_missing_packages
  if [ "${#MISSING_PACKAGES[@]}" -eq 0 ]; then
    ok "Already installed: ${REQUIRED_PACKAGES[*]}; would skip"
  else
    would "run with sudo (never prompting): apt-get update; apt-get install -y --no-install-recommends ${MISSING_PACKAGES[*]}"
    would "or, if sudo needs a password, print those commands for an administrator and stop"
  fi

  step "uv"
  if find_uv; then
    ok "uv $UV_FOUND_VERSION at $UV_BIN; would skip"
  else
    [ -z "$UV_REJECTED" ] || info "Found $UV_REJECTED, older than $UV_VERSION."
    would "download $UV_INSTALLER_URL and run it with UV_INSTALL_DIR=$UV_DIR and UV_NO_MODIFY_PATH=1"
  fi

  step "Python environment"
  if probe_python_env; then
    ok "$CHECKOUT/.venv already matches uv.lock; would skip"
  else
    would "run in the checkout: uv $(quote_command "${UV_SYNC_ARGS[@]}")"
  fi

  step "Node.js"
  if find_node; then
    ok "Node.js $NODE_FOUND_VERSION at $NODE_BIN; would skip"
  else
    [ -z "$NODE_REJECTED" ] || info "Found $NODE_REJECTED; Node.js $NODE_MIN_MAJOR or newer with npm is needed."
    would "download $NODE_DIST_URL/$NODE_ARCHIVE and SHASUMS256.txt, verify the SHA-256, unpack into $NODE_DIR"
  fi

  step "Browser test dependencies"
  if probe_npm_packages; then
    ok "Playwright $PLAYWRIGHT_VERSION in tests/node_modules; would skip npm ci"
  else
    would "run in tests/: npm ci --ignore-scripts --no-audit --no-fund"
  fi
  if chromium_downloaded; then
    ok "Chromium for Playwright is downloaded; would skip the download"
  else
    would "run: playwright install chromium (into Playwright's browser cache in your home folder)"
  fi
  would "launch headless Chromium once; if it cannot start, install its system libraries with sudo (never prompting): playwright install-deps chromium"

  step "Claude Code binary"
  report_claude_binary

  step "Verify"
  would "repeat the --check requirement list, then import sylvia_gm and run $SMOKE_TEST"

  step "Next steps"
  would "print the PATH line and the commands for a focused check, the complete run and --full-run"

  printf '\n'
  if [ -n "$DRY_RUN_STOP" ]; then
    printf 'DRY RUN: nothing was changed. The default mode would stop at %s\n' "$DRY_RUN_STOP"
  else
    printf 'DRY RUN: nothing was changed.\n'
  fi
}

# I/N in plain digits with 1 <= I <= N <= 64, the form the check runner accepts.
valid_machine_share() {
  local index count
  case "$1" in
    '' | */*/* | /* | */ | *[!0-9/]*) return 1 ;;
    */*) ;;
    *) return 1 ;;
  esac
  index=${1%/*}
  count=${1#*/}
  [ "${#index}" -le 2 ] && [ "${#count}" -le 2 ] || return 1
  index=$((10#$index))
  count=$((10#$count))
  [ "$count" -ge 1 ] && [ "$count" -le 64 ] && [ "$index" -ge 1 ] && [ "$index" -le "$count" ]
}

parse_args() {
  local chosen=""
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --help | -h)
        usage
        exit 0
        ;;
      --check | --dry-run | --full-run)
        if [ -n "$chosen" ] && [ "$chosen" != "$1" ]; then
          printf 'Choose only one of --check, --dry-run and --full-run.\n\n' >&2
          usage >&2
          exit 2
        fi
        chosen=$1
        ;;
      --machine-share)
        if [ "$#" -lt 2 ] || ! valid_machine_share "$2"; then
          printf -- '--machine-share needs I/N with 1 <= I <= N <= 64, for example 2/6.\n\n' >&2
          usage >&2
          exit 2
        fi
        MACHINE_SHARE=$2
        shift
        ;;
      *)
        printf 'Unknown option: %s\n\n' "$1" >&2
        usage >&2
        exit 2
        ;;
    esac
    shift
  done
  if [ -n "$MACHINE_SHARE" ] && [ "$chosen" != "--full-run" ]; then
    printf -- '--machine-share is only used with --full-run.\n\n' >&2
    usage >&2
    exit 2
  fi
  case "$chosen" in
    --check) MODE="check" ;;
    --dry-run) MODE="dry-run" ;;
    --full-run) MODE="full-run" ;;
    *) MODE="setup" ;;
  esac
}

main() {
  parse_args "$@"
  trap cleanup EXIT
  # Tool locations depend on HOME; an unusable HOME is reported by the preconditions.
  UV_DIR="${HOME:-/nonexistent}/.local/bin"
  TOOLS_DIR="${HOME:-/nonexistent}/.local/share/sylvia-gm-dev"
  NODE_DIR="$TOOLS_DIR/node-v${NODE_VERSION}-linux-x64"
  # The checks use the checkout's own .venv.
  unset UV_PROJECT_ENVIRONMENT
  case "$MODE" in
    check) run_check ;;
    dry-run) run_dry_run ;;
    setup) run_setup ;;
    full-run)
      run_setup
      run_full
      ;;
  esac
}

main "$@"
