"""Build the current standalone TD package without a Campaign Setting dependency."""

import json
import sys
from pathlib import Path

import pytest

from sylvia_gm.application.package_materialization import (
    NormalizedPackageDraft,
    materialize_package_archive,
)

SOURCE = Path(__file__).resolve().parents[1] / "development/trigger-discipline/source"
sys.path.insert(0, str(SOURCE))
from sylvia_ruleset import builder  # noqa: E402


def build_archive(directory):
    """Materialize the complete permitted source through the normal package builder."""
    draft = directory / "draft"
    builder.build_draft(SOURCE, draft_path=draft, allow_worktree_sources=True)
    record = json.loads((draft / "package.json").read_bytes())
    files = {
        path.relative_to(draft / "files").as_posix(): path.read_bytes()
        for path in (draft / "files").rglob("*") if path.is_file()
    }
    target = directory / "trigger-discipline.sylvia-gm-package"
    with materialize_package_archive((NormalizedPackageDraft(record, files, 4),)) as artifact:
        artifact.archive.seek(0)
        target.write_bytes(artifact.archive.read())
    return target


@pytest.fixture(scope="session")
def candidate(tmp_path_factory):
    return build_archive(tmp_path_factory.mktemp("td-source-package"))
