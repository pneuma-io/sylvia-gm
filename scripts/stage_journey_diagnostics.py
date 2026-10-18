"""Retain compact test evidence, never whole trees of reproducible scenario databases."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path, PurePosixPath

JSON_NAMES = {
    "summary.json",
    "selection.json",
    "source-identity.json",
    "pool-assignments.json",
    "case-timings.json",
    "runner-capacity.json",
    "performance.json",
}


def _contained_file(root, relative):
    value = PurePosixPath(relative)
    path = root / relative
    if value.is_absolute() or ".." in value.parts or "\\" in relative:
        raise ValueError("Evidence must be a contained relative file")
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Evidence is missing, a directory or outside the selected run")
    for item in [path, *path.parents]:
        if item == root.parent:
            break
        if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
            raise ValueError("Evidence aliases are not admitted")
    return path


def stage_diagnostics(raw, destination, *, runner_log=None, exit_code=None, evidence=()):
    """Copy allowlisted diagnostics; preserve a failed summary even before collection ends."""
    raw, destination = Path(raw), Path(destination)
    if destination.exists():
        raise ValueError("Diagnostic destination must be new; earlier evidence is preserved")
    if destination.resolve().is_relative_to(raw.resolve()):
        raise ValueError("Retained diagnostics must be outside the raw output tree")
    selected = []
    if raw.exists():
        for path in sorted(raw.iterdir()):
            name = path.name
            if path.is_file() and (
                name in JSON_NAMES
                or name.endswith(
                    (".xml", ".log", "-required-cases.json", ".arguments.json", "-timings.jsonl")
                )
            ):
                selected.append((_contained_file(raw, name), name))
    selected.extend((_contained_file(raw, name), "failed-evidence/" + name) for name in evidence)
    destination.mkdir(parents=True)
    for path, name in selected:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    if runner_log and Path(runner_log).is_file():
        shutil.copyfile(runner_log, destination / "runner.log")
    summary_path = destination / "summary.json"
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        if summary.get("status") not in (None, "completed"):
            raise ValueError("Runner did not complete its summary")
        if not isinstance(summary.get("passed"), bool):
            raise ValueError("Runner summary has no pass/fail result")
    except (OSError, ValueError, TypeError) as error:
        if summary_path.exists():
            shutil.copyfile(summary_path, destination / "summary-incomplete.txt")
        summary = {
            "status": "incomplete",
            "passed": False,
            "complete_gate": False,
            "runner_exit_code": exit_code,
            "reason": f"No completed valid summary: {error}",
        }
    if exit_code not in (None, 0):
        summary["passed"] = False
        summary["runner_exit_code"] = exit_code
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return finalize_manifest(destination, evidence=evidence)


def finalize_manifest(destination, *, evidence=()):
    """Hash the final retained bytes, including metadata added by early-failure handling."""
    destination = Path(destination)
    manifest_path = destination / "diagnostic-manifest.json"
    files = [
        {
            "path": path.relative_to(destination).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted(destination.rglob("*"))
        if path.is_file()
        and path != manifest_path
        and _contained_file(destination, path.relative_to(destination).as_posix())
    ]
    manifest = {
        "files": files,
        "retained_bytes": sum(item["bytes"] for item in files),
        "successful_scratch_retained": False,
        "explicit_failed_evidence": list(evidence),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(
        f"Retained diagnostics: {len(files)} files, {manifest['retained_bytes']} bytes", flush=True
    )
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--runner-log", type=Path)
    parser.add_argument("--exit-code", type=int)
    parser.add_argument("--failed-evidence", action="append", default=[])
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Rehash final staged metadata after early-failure handling",
    )
    args = parser.parse_args()
    if args.refresh:
        finalize_manifest(args.destination, evidence=args.failed_evidence)
        return
    if args.raw is None:
        parser.error("--raw is required unless --refresh is selected")
    stage_diagnostics(
        args.raw,
        args.destination,
        runner_log=args.runner_log,
        exit_code=args.exit_code,
        evidence=args.failed_evidence,
    )


if __name__ == "__main__":
    main()
