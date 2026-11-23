"""Package candidate exports: what Python's owner writes for every installed candidate.

Run with the snapshot's own interpreter, from the repository root, after the store generators
whose profiles are the bases:

    .venv/Scripts/python.exe -B crates/sylvia-app/tests/vectors/generate_package_export_runs.py

Four Python-made profiles with installed candidates: the Riichi participant profile (`cast`, a
two-package closure), the session-page and campaign-reads profiles (one synthetic package each)
and the package-install profile (`install`, three candidates). For every candidate, and one
that does not exist, `AuthorityOwner.export_package_candidate(candidate_id, target)` into an
in-memory target: the result, or the failure's class, code and message, and the SHA-256 and
length of the bytes written. It writes `package_export/package_export_runs.json`;
`tests/package_export.rs` replays every export on the Rust store. Reference tooling only.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[3]
sys.path.insert(0, str(REPOSITORY / "src"))

from sylvia_gm.application.authority import AuthorityOwner  # noqa: E402

OUT = HERE / "package_export"
STORE = REPOSITORY / "crates" / "sylvia-store" / "tests" / "vectors"
BASES = {
    "cast": STORE / "stored_authority" / "cast.sqlite3.gz",
    "session_pages": STORE / "session_pages" / "seed.sqlite3.gz",
    "campaigns": STORE / "campaigns" / "seed.sqlite3.gz",
    "install": STORE / "packages" / "install.sqlite3.gz",
}


def main() -> None:
    OUT.mkdir(exist_ok=True)
    runs = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        for base, source in BASES.items():
            database = Path(scratch) / base / "authority.sqlite3"
            database.parent.mkdir()
            database.write_bytes(gzip.decompress(source.read_bytes()))
            connection = sqlite3.connect(database)
            candidates = [row[0] for row in connection.execute(
                "SELECT candidate_id FROM package_candidates ORDER BY candidate_id")]
            connection.close()
            with AuthorityOwner.open(database, "package-export-runs") as owner:
                for candidate_id in [*candidates, "candidate-missing"]:
                    target = io.BytesIO()
                    try:
                        outcome: dict[str, object] = {"ok": dict(owner.export_package_candidate(candidate_id, target))}
                    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
                        outcome = {"error": type(error).__name__, "code": getattr(error, "code", None),
                                   "message": str(error)}
                    written = target.getvalue()
                    runs.append({"base": base, "candidate_id": candidate_id, "outcome": outcome,
                                 "written_sha256": hashlib.sha256(written).hexdigest(),
                                 "written_bytes": len(written)})
    record = {"python": sys.version.split()[0], "runs": runs}
    (OUT / "package_export_runs.json").write_bytes((json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
