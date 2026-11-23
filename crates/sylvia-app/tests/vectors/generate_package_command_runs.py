"""The package export commands through Python's handler.

Run with the snapshot's own interpreter, from the repository root, after
`crates/sylvia-store/tests/vectors/generate_package_runs.py` (its install profile is the base):

    .venv/Scripts/python.exe -B crates/sylvia-app/tests/vectors/generate_package_command_runs.py

Python's `handle_export_package_candidate` runs as the owner runs it, for `export_package_candidate`
and `export_package_candidate_file`, on the package-install profile (three candidates): the
handler's checks (a missing or unsafe export identity, a missing candidate identity), a missing
candidate, each candidate exported, and an export over an earlier one. Each run records the reply
(the file command's path relative to the storage root) or the failure's class, code and message,
and the `exports/` folder after it: each file's name and SHA-256. It writes
`package_export/package_command_runs.json`; the owner's `commands::packages` test replays every
run through its dispatch. Reference tooling only.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import sys
import tempfile
import types
from collections.abc import Mapping
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[3]
sys.path.insert(0, str(REPOSITORY / "src"))

from sylvia_gm.application.authority import AuthorityOwner  # noqa: E402
from sylvia_gm.interfaces.commands import packages as handlers  # noqa: E402

OUT = HERE / "package_export"
BASE = REPOSITORY / "crates" / "sylvia-store" / "tests" / "vectors" / "packages" / "install.sqlite3.gz"


def runs() -> list[dict[str, object]]:
    def export(method: str, **params: object) -> dict[str, object]:
        return {"op": method, "params": params}
    plain, file = "export_package_candidate", "export_package_candidate_file"
    return [
        export(plain, candidate_id="candidate-install-1"),
        export(plain, export_id="-bad", candidate_id="candidate-install-1"),
        export(plain, export_id="first"),
        export(plain, export_id="missing", candidate_id="candidate-missing"),
        export(plain, export_id="first", candidate_id="candidate-install-1"),
        export(file, export_id="third", candidate_id="candidate-install-3"),
        export(file, export_id="first", candidate_id="candidate-install-2"),
        export(file, export_id="a" * 128, candidate_id="candidate-install-3"),
    ]


def plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): plain(item) for key, item in value.items()}
    return value


def tree(root: Path) -> list[list[object]]:
    folder = root / "exports"
    if not folder.exists():
        return []
    return [[f"exports/{path.name}", hashlib.sha256(path.read_bytes()).hexdigest()]
            for path in sorted(folder.iterdir())]


def main() -> None:
    OUT.mkdir(exist_ok=True)
    recorded = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        root = Path(scratch) / "storage"
        root.mkdir()
        database = root / "authority.sqlite3"
        database.write_bytes(gzip.decompress(BASE.read_bytes()))
        resolved_root = root.resolve()
        with AuthorityOwner.open(database, "package-command-runs") as owner:
            for run in runs():
                ctx = types.SimpleNamespace(storage_root=root, application_owner=owner, bus=owner,
                                            method=run["op"])
                try:
                    result = plain(handlers.handle_export_package_candidate(ctx, run["params"]))  # type: ignore[arg-type]
                    if isinstance(result, dict) and "artifact_path" in result:
                        result = {**result, "artifact_path":
                                  Path(result["artifact_path"]).relative_to(resolved_root).as_posix()}
                    outcome: dict[str, object] = {"ok": result}
                except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
                    outcome = {"error": type(error).__name__, "code": getattr(error, "code", None),
                               "message": str(error)}
                recorded.append({**run, "outcome": outcome, "tree": tree(root)})
    record = {"python": sys.version.split()[0], "runs": recorded}
    (OUT / "package_command_runs.json").write_bytes((json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
