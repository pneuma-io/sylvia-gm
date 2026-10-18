"""Exercise documentation checks in disposable repositories, without touching this index."""

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check-docs-index.sh"


def _git(repo: Path, *args: str) -> None:
    """Set up only the disposable repository used by one test."""
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _check(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run the real checker against the test repository."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("The documentation checker requires bash")
    return subprocess.run([bash, SCRIPT.name, *args], cwd=repo, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Create a fresh Git index containing only a documentation index."""
    _git(tmp_path, "init", "-q")
    (tmp_path / SCRIPT.name).write_bytes(SCRIPT.read_bytes())
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/INDEX.md").write_text("# Documentation index\n", encoding="utf-8")
    _git(tmp_path, "add", "docs/INDEX.md")
    return tmp_path


@pytest.mark.parametrize("name", ["docs/new-plan.md", "docs/design/plan with spaces.md"])
def test_worktree_checks_untracked_documents_without_staging(repo: Path, name: str) -> None:
    document = repo / name
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_text("# A new plan\n", encoding="utf-8")
    index_before = (repo / ".git/index").read_bytes()

    missing = _check(repo, "--worktree")
    assert missing.returncode == 1
    assert name in missing.stderr
    assert _check(repo).returncode == 0

    (repo / "docs/INDEX.md").write_text(f"# Documentation index\n{name}\n", encoding="utf-8")
    assert _check(repo, "--worktree").returncode == 0
    assert (repo / ".git/index").read_bytes() == index_before


@pytest.mark.parametrize("staged_reference", [False, True])
def test_each_mode_uses_its_own_index_copy(repo: Path, staged_reference: bool) -> None:
    name = "docs/selected-plan.md"
    (repo / name).write_text("# Plan\n", encoding="utf-8")
    index = repo / "docs/INDEX.md"
    index.write_text(name if staged_reference else "# Index\n", encoding="utf-8")
    _git(repo, "add", "docs")
    index_before = (repo / ".git/index").read_bytes()

    index.write_text("# Index\n" if staged_reference else name, encoding="utf-8")
    (repo / name).write_text("# Plan with an unstaged edit\n", encoding="utf-8")

    assert _check(repo, "--staged").returncode == (0 if staged_reference else 1)
    assert _check(repo).returncode == (0 if staged_reference else 1)
    assert _check(repo, "--worktree").returncode == (1 if staged_reference else 0)
    assert (repo / ".git/index").read_bytes() == index_before


def test_archives_are_exempt_and_missing_working_index_fails(repo: Path) -> None:
    archive = repo / "docs/archive/old-plan.md"
    archive.parent.mkdir()
    archive.write_text("# Historical plan\n", encoding="utf-8")
    assert _check(repo, "--worktree").returncode == 0

    (repo / "docs/current-plan.md").write_text("# Current plan\n", encoding="utf-8")
    (repo / "docs/INDEX.md").unlink()
    missing = _check(repo, "--worktree")
    assert missing.returncode == 1
    assert "docs/INDEX.md" in missing.stderr


def test_git_read_failure_does_not_report_success(repo: Path) -> None:
    (repo / ".git/index").write_bytes(b"invalid Git index for this test")
    result = _check(repo, "--worktree")
    assert result.returncode != 0
    assert "No documentation changes" not in result.stdout
