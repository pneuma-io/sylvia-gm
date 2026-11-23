"""Portable campaign export through Python's owner handler.

Run with the snapshot's own interpreter, from the repository root, after
`crates/sylvia-store/tests/vectors/generate_export_capture_runs.py` (its story profile is the
base):

    .venv/Scripts/python.exe -B crates/sylvia-app/tests/vectors/generate_export_runs.py

Python's `handle_export_campaign` runs as the owner runs it, for `export_campaign` and
`export_campaign_file`, with `AuthorityOwner.export_campaign`: the handler's parameter checks
(the artifact identity first, then the operation and campaign identities and the thinking flag),
the owner's identity checks, exports of the story campaign with and without its private provider
records, a replacement of an earlier export, the Builder campaign, and a campaign that does not
exist. Each run records the reply (the file command's `artifact_path` relative to the resolved
storage root) or the failure's class, code and message, and everything under `exports/`
afterwards. It writes `export/export_runs.json`; the Rust owner replays it through its dispatch
(`commands/campaigns.rs`). Reference tooling only.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import random
import sys
import tempfile
import types
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3] / "src"))

_IDENTITIES = random.Random(20261127)
uuid.uuid4 = lambda: uuid.UUID(int=_IDENTITIES.getrandbits(128), version=4)  # type: ignore[assignment]

from sylvia_gm.application.authority import AuthorityOwner  # noqa: E402
from sylvia_gm.interfaces.commands import campaigns as handlers  # noqa: E402

OUT = HERE / "export"
BASE = HERE.parents[3] / "crates" / "sylvia-store" / "tests" / "vectors" / "export_capture" / "story.sqlite3.gz"
STORY = "campaign-story"


def export(method: str, export_id: object = "story-1", *, operation_id: object = "export-1",
           campaign_id: object = STORY, **extra: object) -> dict[str, object]:
    params: dict[str, object] = {}
    for key, value in (("export_id", export_id), ("operation_id", operation_id), ("campaign_id", campaign_id)):
        if value is not None:
            params[key] = value
    params.update(extra)
    return {"op": method, "params": params}


OPERATIONS: list[dict[str, object]] = [
    # The handler's checks: the artifact identity comes first, before the exports folder exists.
    export("export_campaign", None, operation_id=None),
    export("export_campaign", "-story", operation_id=None),
    export("export_campaign", "story/1"),
    export("export_campaign", "s" * 129),
    export("export_campaign", "story-1", operation_id=None),
    export("export_campaign", "story-1", campaign_id=""),
    export("export_campaign", "story-1", include_thinking="yes"),
    export("export_campaign", "story-1", include_thinking=None),
    # The owner's identity checks.
    export("export_campaign", "story-1", operation_id=" export-1"),
    export("export_campaign", "story-1", campaign_id="   "),
    # Exports.
    export("export_campaign", "story-1"),
    export("export_campaign_file", "S" * 128, include_thinking=True),
    export("export_campaign_file", "story.2_x-y", include_thinking=False),
    # The same identity again replaces the earlier archive.
    export("export_campaign", "story-1", include_thinking=True),
    export("export_campaign_file", "build-1", campaign_id="campaign-build"),
    export("export_campaign", "missing-1", campaign_id="campaign-missing"),
]


def tree(root: Path) -> list[list[object]]:
    """Every entry under `exports/`, relative, with a file's SHA-256."""
    exports = root / "exports"
    if not exports.exists():
        return []
    entries: list[list[object]] = [["exports", "dir", None]]
    for path in sorted(exports.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            entries.append([relative, "dir", None])
        else:
            entries.append([relative, "file", hashlib.sha256(path.read_bytes()).hexdigest()])
    return entries


def failure(error: BaseException) -> dict[str, object]:
    return {"error": type(error).__name__, "code": getattr(error, "code", None), "message": str(error)}


def value(result: object, root: Path) -> object:
    def plain(item: object) -> dict[str, object]:
        return {"campaign_id": item.campaign_id, "captured_revision": item.captured_revision,  # type: ignore[attr-defined]
                "archive_sha256": item.archive_sha256, "byte_count": item.byte_count}  # type: ignore[attr-defined]

    if isinstance(result, dict):
        resolved = Path(str(result["artifact_path"]))
        return {"export": plain(result["export"]),
                "artifact_path": resolved.relative_to(root.resolve(strict=True)).as_posix()}
    return plain(result)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    runs = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        root = Path(scratch) / "storage"
        root.mkdir()
        database = root / "authority.sqlite3"
        database.write_bytes(gzip.decompress(BASE.read_bytes()))
        with AuthorityOwner.open(database, "export-handler") as owner:
            for op in OPERATIONS:
                ctx = types.SimpleNamespace(storage_root=root, application_owner=owner, bus=owner, method=op["op"])
                try:
                    outcome: dict[str, object] = {
                        "ok": value(handlers.handle_export_campaign(ctx, op["params"]), root)}  # type: ignore[arg-type]
                except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
                    outcome = failure(error)
                runs.append({**op, "outcome": outcome, "tree": tree(root)})
    record = {"python": sys.version.split()[0], "runs": runs}
    (OUT / "export_runs.json").write_bytes((json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
