"""The Linux development setup script: how it is stored, and its read-only modes on Linux."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RELATIVE = "scripts/setup_linux_dev.sh"
SCRIPT = ROOT / RELATIVE
STEPS = [
    "Preconditions",
    "Filesystem",
    "Disk and memory",
    "System packages",
    "uv",
    "Python environment",
    "Node.js",
    "Browser test dependencies",
    "Claude Code binary",
    "Verify",
    "Next steps",
]
# Left out of the stripped PATH so the script sees a machine without these tools.
HIDDEN_TOOLS = {"uv", "uvx", "node", "nodejs", "npm", "npx", "corepack", "claude"}
SYSTEM_TOOL_FOLDERS = (
    "/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin",
)
linux_only = pytest.mark.runs_on("linux", reason="Runs the bash setup script on this Linux machine")


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout


def test_script_is_stored_executable_with_lf_and_bash_shebang():
    staged = _git("ls-files", "-s", "--", RELATIVE).split()
    assert staged and staged[0] == "100755", staged
    assert "i/lf" in _git("ls-files", "--eol", "--", RELATIVE).split()
    data = SCRIPT.read_bytes()
    assert b"\r" not in data
    assert data.startswith(b"#!/usr/bin/env bash\n")


def test_pinned_versions_and_network_addresses_are_declared_once():
    text = SCRIPT.read_text(encoding="utf-8")
    first_function = re.search(r"^\w+\(\) \{$", text, re.MULTILINE).start()
    for name in ("UV_VERSION", "NODE_VERSION", "PYTHON_VERSION", "NODE_MIN_MAJOR"):
        assignments = re.findall(rf"^\s*(?:readonly\s+)?{name}=(.*)$", text, re.MULTILINE)
        assert len(assignments) == 1, name
        assert text.index(f"readonly {name}=") < first_function, name
        value = assignments[0].strip('"')
        if name != "NODE_MIN_MAJOR":
            assert text.count(value) == 1, (name, value)

    addresses = re.findall(r"https?://[^\s\"')]+", text)
    assert addresses and len(addresses) == len(set(addresses)), addresses
    for address in addresses:
        line = next(line for line in text.splitlines() if address in line)
        assert re.match(r'readonly [A-Z_]+_URL="https://', line), line
        assert text.index(address) < first_function, address
    # One downloader, and every download starts from one of the declared addresses.
    assert len(re.findall(r"^\s*curl ", text, re.MULTILINE)) == 1
    downloads = re.findall(r'^\s*download "\$(\w+)', text, re.MULTILINE)
    assert downloads and set(downloads) <= {"UV_INSTALLER_URL", "NODE_DIST_URL"}, downloads
    assert not re.search(r"\beval\b", text)


def test_changing_the_script_selects_its_checks_without_a_broad_fallback():
    from scripts.select_checks import select_checks

    plan = select_checks([RELATIVE], source="a" * 40, base="b" * 40, root=ROOT)
    selectors = [item for items in plan["python"].values() for item in items]
    assert "tests/test_linux_dev_setup.py" in selectors
    assert not plan["broad_fallback"]


def _bash() -> str:
    return shutil.which("bash") or "/bin/bash"


def _run(environment: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_bash(), str(SCRIPT), *args],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=600,
    )


@pytest.fixture
def fresh_machine(tmp_path):
    """An empty home, and a PATH with every system tool except uv, Node.js and Claude Code."""
    home = tmp_path / "home"
    home.mkdir()
    tools = tmp_path / "bin"
    tools.mkdir()
    for folder in SYSTEM_TOOL_FOLDERS:
        directory = Path(folder)
        if not directory.is_dir():
            continue
        for entry in sorted(directory.iterdir()):
            link = tools / entry.name
            if entry.name in HIDDEN_TOOLS or link.is_symlink() or link.exists():
                continue
            if entry.is_file() and os.access(entry, os.X_OK):
                link.symlink_to(entry)
    return home, {"HOME": str(home), "PATH": str(tools), "LANG": "C.UTF-8"}


@linux_only
def test_bash_accepts_the_script_syntax():
    result = subprocess.run([_bash(), "-n", str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@linux_only
def test_help_exits_zero_and_names_every_mode():
    result = subprocess.run([_bash(), str(SCRIPT), "--help"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    for option in ("--check", "--dry-run", "--full-run", "--help"):
        assert option in result.stdout


@linux_only
@pytest.mark.parametrize("args", [["--no-such-option"], ["--check", "--dry-run"]])
def test_unknown_or_conflicting_options_exit_non_zero(args):
    result = subprocess.run([_bash(), str(SCRIPT), *args], capture_output=True, text=True)
    assert result.returncode != 0
    assert "Usage:" in result.stderr


@linux_only
def test_dry_run_lists_every_step_in_order_and_changes_nothing(fresh_machine):
    home, environment = fresh_machine
    status_before = _git("status", "--porcelain")
    result = _run(environment, "--dry-run")
    assert result.returncode == 0, result.stdout + result.stderr
    headers = re.findall(r"^==> \[(\d+)/(\d+)\] (.+)$", result.stdout, re.MULTILINE)
    assert headers == [(str(number), str(len(STEPS)), name) for number, name in
                       enumerate(STEPS, start=1)], result.stdout
    assert "astral.sh" in result.stdout and "nodejs.org" in result.stdout
    assert "DRY RUN: nothing was changed" in result.stdout
    assert list(home.iterdir()) == []
    assert _git("status", "--porcelain") == status_before


@linux_only
def test_check_names_what_is_missing_and_changes_nothing(fresh_machine):
    home, environment = fresh_machine
    status_before = _git("status", "--porcelain")
    result = _run(environment, "--check")
    assert result.returncode != 0, result.stdout
    assert re.search(r"^\s+MISSING\s+uv:", result.stdout, re.MULTILINE), result.stdout
    assert re.search(r"^\s+MISSING\s+Node\.js:", result.stdout, re.MULTILINE), result.stdout
    assert "NOT READY" in result.stderr
    assert list(home.iterdir()) == []
    assert _git("status", "--porcelain") == status_before
