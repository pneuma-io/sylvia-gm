"""Build an importable Trigger Discipline package from the selected source revision."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from sylvia_gm.application.package_library import validate_admitted_package_runtimes
from sylvia_gm.application.package_materialization import (
    NormalizedPackageDraft,
    materialize_package_archive,
)
from sylvia_gm.infrastructure.package_archive import admit_package_archive

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "development/trigger-discipline/source"


def build_package(output: Path, *, source_commit: str, allow_worktree: bool = False) -> dict:
    """Compile and admit the standalone Ruleset without selecting a Setting or tuning it."""
    sys.path.insert(0, str(SOURCE))
    from sylvia_ruleset import builder

    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="td-build-", dir=output.parent) as temporary:
        draft = Path(temporary) / "draft"
        builder.build_draft(
            SOURCE, source_commit=source_commit, draft_path=draft,
            allow_worktree_sources=allow_worktree,
        )
        record = json.loads((draft / "package.json").read_bytes())
        files = {
            path.relative_to(draft / "files").as_posix(): path.read_bytes()
            for path in (draft / "files").rglob("*") if path.is_file()
        }
        with materialize_package_archive((NormalizedPackageDraft(record, files, 4),)) as artifact:
            artifact.archive.seek(0)
            with admit_package_archive(artifact.archive) as admitted:
                validate_admitted_package_runtimes(admitted)
                content_sha256 = next(
                    entry.sha256 for entry in admitted.package_lock.entries
                    if entry.package_id == builder.PACKAGE_ID
                )
            artifact.archive.seek(0)
            archive = artifact.archive.read()
        with output.open("xb") as stream:
            stream.write(archive)
        parameters = json.loads(files["provenance/parameter-manifest.json"])
        identity = {
            "package_id": record["id"],
            "version": record["version"],
            "source_commit": source_commit,
            "source_state": "worktree" if allow_worktree else "committed",
            "archive_sha256": hashlib.sha256(archive).hexdigest(),
            "content_sha256": content_sha256,
            "runtime_sha256": hashlib.sha256(files["runtime/ruleset.yaml"]).hexdigest(),
            "parameter_set_sha256": parameters["parameter_set_sha256"],
            "bytes": len(archive),
            "balance_status": "retained values; balance qualification pending",
        }
        output.with_suffix(".identity.json").write_text(
            json.dumps(identity, indent=2) + "\n", encoding="utf8",
        )
        return identity


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-worktree", action="store_true", help="Label a development build")
    args = parser.parse_args()
    revision = subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True,
    ).strip()
    if not args.allow_worktree:
        changed = subprocess.check_output(
            ["git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=all", "--",
             "development/trigger-discipline/source", "src/sylvia_gm",
             "scripts/build_trigger_discipline_package.py"], text=True,
        ).strip()
        if changed:
            parser.error(
                "Commit the selected implementation, or use --allow-worktree for development"
            )
    print(json.dumps(build_package(
        args.output, source_commit=revision, allow_worktree=args.allow_worktree,
    ), indent=2))


if __name__ == "__main__":
    main()
