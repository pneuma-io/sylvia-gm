"""Record what Python's closed owner answers for lane 5's guarded reads, as owner_close_reads.json.

Run from the repository root with the snapshot's interpreter:

    .venv/Scripts/python.exe -I crates/sylvia-app/tests/vectors/generate_owner_close_reads.py

Opens `AuthorityOwner` on lane 3's `owner_close/base.sqlite3.gz`, closes it, then calls
`inspect_package_candidate`, `package_candidate_text` (an export-only path, whose check comes
before the owner's operation, and a text path), `compare_package_candidates` and
`desktop_snapshot` (complete and display-only). Each answer is the class and message raised.
Deterministic; reference tooling for the port, retired with the Python source.
"""

from __future__ import annotations

import gzip
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.application.authority import AuthorityOwner  # noqa: E402

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "owner_close_reads.json"

CALLS = [
    ("inspect_package_candidate", lambda owner: owner.inspect_package_candidate("candidate-missing")),
    ("package_candidate_text export-only",
     lambda owner: owner.package_candidate_text("candidate-missing", "pkg", "data/archive.bin")),
    ("package_candidate_text text",
     lambda owner: owner.package_candidate_text("candidate-missing", "pkg", "notes.md")),
    ("compare_package_candidates", lambda owner: owner.compare_package_candidates("left", "right")),
    ("desktop_snapshot", lambda owner: owner.desktop_snapshot("campaign")),
    ("desktop_snapshot display_only", lambda owner: owner.desktop_snapshot("campaign", display_only=True)),
]


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "authority.sqlite3"
        database.write_bytes(gzip.decompress((HERE / "owner_close" / "base.sqlite3.gz").read_bytes()))
        owner = AuthorityOwner.open(database, "owner-close-reads")
        owner.close()
        cases = []
        for name, call in CALLS:
            try:
                call(owner)
                outcome = {"ok": True}
            except Exception as error:  # every answer here is a raise
                outcome = {"raise": type(error).__name__, "code": getattr(error, "code", None),
                           "message": str(error)}
            cases.append({"name": name, "outcome": outcome})
    OUTPUT.write_text(json.dumps({"cases": cases}, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(cases)} cases -> {OUTPUT}")


if __name__ == "__main__":
    main()
