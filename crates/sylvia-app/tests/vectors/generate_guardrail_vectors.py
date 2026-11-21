"""Generate reference vectors for sylvia_app::path_guardrail (builder/_guardrail.py).

Run with the snapshot's own interpreter, from the repository root, on each platform:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_guardrail_vectors.py
    .venv/bin/python -I crates/sylvia-app/tests/vectors/generate_guardrail_vectors.py

`Path.resolve()` and `relative_to` behave differently on Windows and POSIX, so each platform
writes its own file (`guardrail_vectors.windows.json` or `guardrail_vectors.linux.json`) and the
Rust test reads the one for the platform it runs on.

The script builds a small project tree in a fresh temporary directory (files, folders, the
internal and read-only roots, and a symbolic link where the platform allows one), then validates
raw paths against it for reading and writing. Outcomes are the resolved path or the error text,
with the tree's location written as `<ROOT>` and its parent as `<PARENT>` (`<ROOT-UPPER>` and
`<PARENT-UPPER>` for their upper-case forms, which POSIX keeps unresolved), so no machine path is
recorded. The Rust test builds the same tree and substitutes its own locations.
This script is reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.builder._guardrail import GuardrailBlockedError, PathGuardrail  # noqa: E402

PLATFORM = "windows" if os.name == "nt" else "linux"
OUTPUT = Path(__file__).with_name(f"guardrail_vectors.{PLATFORM}.json")

TREE_DIRS = ["docs", "docs/deep", ".builder-sources", ".builder-sources/pack", ".builder-sources-purge-pending",
             "Mixed Case"]
TREE_FILES = ["docs/readme.md", "docs/deep/notes.txt", ".builder-sources/pack/a.json", "Mixed Case/File.TXT",
              ".env", "secret.key"]
OUTSIDE = "outside"
LINKS = [("link-out", "../outside"), ("link-in", "docs")]

RAW_PATHS = [
    "", ".", "docs", "docs/readme.md", "docs/missing/new.md", "docs/../docs/readme.md", "docs/./deep/notes.txt",
    "../outside", "../outside/x.txt", "docs/../../outside", "missing.md", "missing/a/b/c.md",
    ".builder-sources/pack/a.json", ".builder-sources/new.json", ".BUILDER-SOURCES/new.json",
    ".builder-sources-purge-pending/x", ".Builder-Sources-Purge-Pending", "mixed case/file.txt", "MIXED CASE",
    ".env", ".env.local", "config/.env.production", "my.env", "secret.key", "cert.pem", "a.pem.bak", "key", ".keys",
    "notes.KEY", "link-out", "link-out/x.txt", "link-in/readme.md", "docs\\deep\\notes.txt", "docs/deep/../readme.md",
    "a\x00b", "docs//readme.md", "docs/readme.md/", "  spaced.md", "trailing. ", "C:relative.md", "/absolute.md",
    "\\\\server\\share\\x.md", "<ABSROOT>/docs/readme.md", "<ABSROOT>/../outside", "<ABSPARENT>/outside/y",
    "<ABSROOT-UPPER>/docs/readme.md",
]


def build(base: Path) -> tuple[Path, list[str]]:
    root = base / "project"
    for name in TREE_DIRS:
        (root / name).mkdir(parents=True, exist_ok=True)
    for name in TREE_FILES:
        (root / name).write_text("x", encoding="utf-8")
    (base / OUTSIDE).mkdir()
    (base / OUTSIDE / "x.txt").write_text("x", encoding="utf-8")
    links = []
    for name, target in LINKS:
        try:
            os.symlink(target, root / name, target_is_directory=True)
            links.append(name)
        except OSError:
            pass
    return root, links


def masked(text: str, root: Path, parent: Path) -> str:
    marks = ((str(root), "<ROOT>"), (str(root).upper(), "<ROOT-UPPER>"), (str(parent), "<PARENT>"),
             (str(parent).upper(), "<PARENT-UPPER>"))
    for value, mark in marks:
        text = text.replace(value, mark)
    return text


def expand(raw: str, root: Path, parent: Path) -> str:
    return (raw.replace("<ABSROOT-UPPER>", str(root).upper()).replace("<ABSROOT>", str(root))
            .replace("<ABSPARENT>", str(parent)))


def main() -> None:
    base = Path(tempfile.mkdtemp(prefix="sylvia-guardrail-")).resolve()
    try:
        root, links = build(base)
        guardrail = PathGuardrail(root)
        assert str(guardrail.allowed_root) == str(root), (guardrail.allowed_root, root)
        cases = []
        for raw in RAW_PATHS:
            if raw.startswith(("link-out", "link-in")) and raw.split("/")[0] not in links:
                continue
            if raw == "<ABSROOT-UPPER>/docs/readme.md" and str(root).upper() == str(root):
                continue
            actual_raw = expand(raw, root, base)
            for for_write in (False, True):
                try:
                    resolved = guardrail.validate(actual_raw, for_write=for_write)
                    outcome = ["ok", masked(str(resolved), root, base)]
                except GuardrailBlockedError as error:
                    outcome = ["blocked", masked(str(error), root, base), masked(str(error.resolved), root, base),
                               error.reason]
                cases.append({"raw": raw, "for_write": for_write, "outcome": outcome})
        text = json.dumps({"python": sys.version.split()[0], "platform": PLATFORM, "links": links,
                           "dirs": TREE_DIRS, "files": TREE_FILES, "link_targets": dict(LINKS), "cases": cases},
                          ensure_ascii=True, indent=1) + "\n"
        for marker in (str(base), str(base).upper(), os.path.expanduser("~")):
            assert marker not in text, "a machine path leaked into the vectors"
        OUTPUT.write_text(text, encoding="utf-8", newline="\n")
        print(PLATFORM, len(cases), "cases, links:", links)
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    main()
