"""Scan release source and archive bytes using an explicit external literal-name list."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import subprocess
import zipfile
from pathlib import Path

MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
ARCHIVE_SUFFIXES = {".zip", ".sylvia-gm-package", ".sylvia-gm-plan", ".sylvia-gm-campaign"}


def load_names(path: Path, root: Path) -> tuple[str, ...]:
    if path.resolve(strict=True).is_relative_to(root.resolve(strict=True)):
        raise ValueError("The literal-name list must remain outside release source")
    names = tuple(
        dict.fromkeys(
            line.strip().casefold() for line in path.read_text().splitlines() if line.strip()
        )
    )
    if not names or any(re.fullmatch(r"[a-z][a-z0-9_-]*", name) is None for name in names):
        raise ValueError("The external literal-name list is empty or invalid")
    return names


def scan_paths(root: Path, paths: list[str], names: tuple[str, ...]) -> dict[str, object]:
    if not names:
        raise ValueError("Exact identity qualification requires explicit literal names")
    violations = []
    archives = 0
    needles = tuple(name.encode(encoding) for name in names for encoding in ("ascii", "utf-16le"))

    def inspect(label: str, raw: bytes, *, archive: bool, depth: int = 0) -> None:
        nonlocal archives
        if any(name in label.casefold() for name in names):
            violations.append("path: " + label)
        lowercase = raw.lower()
        if any(needle in lowercase for needle in needles):
            violations.append("content: " + label)
        if archive:
            if depth > 3:
                raise ValueError("Archive nesting exceeds the release identity scan bound")
            with zipfile.ZipFile(io.BytesIO(raw)) as package:
                members = package.infolist()
                if sum(member.file_size for member in members) > MAX_ARCHIVE_BYTES:
                    raise ValueError("Archive exceeds the release identity scan bound")
                archives += 1
                for member in members:
                    if member.is_dir():
                        continue
                    child = label + "!/" + member.filename
                    inspect(
                        child,
                        package.read(member),
                        archive=Path(member.filename).suffix in ARCHIVE_SUFFIXES,
                        depth=depth + 1,
                    )

    root = root.resolve(strict=True)
    for relative in paths:
        path = root / relative
        if path.resolve(strict=True) != path.absolute() or not path.is_file():
            raise ValueError("Release identity source must contain only real contained files")
        if not path.resolve().is_relative_to(root):
            raise ValueError("Release identity path escapes source")
        inspect(relative, path.read_bytes(), archive=path.suffix in ARCHIVE_SUFFIXES)
    return {
        "passed": not violations,
        "files_scanned": len(paths),
        "archives_scanned": archives,
        "violations": violations,
        "literal_names_supplied_externally": True,
    }


def check_source(root: Path, name_file: Path) -> dict[str, object]:
    names = load_names(name_file, root)
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0")
    untracked = (
        subprocess.check_output(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=root
        )
        .decode()
        .split("\0")
    )
    paths = sorted(set(filter(None, tracked + untracked)))
    if not paths:
        raise ValueError("Release identity qualification cannot scan an empty source")
    result = scan_paths(root, paths, names)
    result["external_list_sha256"] = hashlib.sha256(name_file.read_bytes()).hexdigest()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--forbidden-name-file", type=Path, required=True)
    args = parser.parse_args()
    result = check_source(args.source, args.forbidden_name_file)
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
