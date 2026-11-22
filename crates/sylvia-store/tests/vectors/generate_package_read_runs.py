"""Package reads for library sources: Python's `AuthorityOwner.package_file_paths`,
`package_text_file`, `package_record` and `package_label`, and their outcomes.

Run with the snapshot's own interpreter, from the repository root, after
`generate_package_runs.py` (its Riichi profile is the starting point):

    .venv/Scripts/python.exe -B crates/sylvia-store/tests/vectors/generate_package_read_runs.py

The base is the Riichi profile plus one small synthetic package installed by Python's store
(`package_reads/base.sqlite3.gz`). Its `notes/broken.txt` holds bytes that are not UTF-8 and its
`notes/glossary.txt` holds non-ASCII UTF-8, so both text outcomes have a case. Each read runs on
the installed packages: file lists under their count bound, text files
under their byte bound, missing paths and packages, and the exact manifest record and label. A
text file or record is recorded by its length and SHA-256 (of the UTF-8 text, or of the record's
canonical JSON as the store's `_dump` writes it), so the file stays small. It writes `package_reads/package_read_runs.json`;
`tests/package_reads.rs` replays every read on the Rust store handle. Reference tooling only.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import random
import sqlite3
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3] / "src"))

_IDENTITIES = random.Random(20261123)
uuid.uuid4 = lambda: uuid.UUID(int=_IDENTITIES.getrandbits(128), version=4)  # type: ignore[assignment]

from sylvia_gm.application.authority import AuthorityOwner  # noqa: E402
from sylvia_gm.domain.package_library import canonical_json_bytes, content_root_for_record  # noqa: E402
from sylvia_gm.infrastructure.package_archive import admit_package_archive  # noqa: E402
from sylvia_gm.infrastructure import sqlite_authority as a  # noqa: E402

BASE = HERE / "packages" / "seed_riichi.sqlite3.gz"
OUT = HERE / "package_reads"


SYNTHETIC = "synthetic.package-reads"
FILES = {
    "notes/broken.txt": b"Caf\xe9 au lait \xff\xfe",
    "notes/glossary.txt": "Kan — a quad; Ryūkōku 龍".encode("utf-8"),
}


def member(name: str) -> zipfile.ZipInfo:
    """A fixed-metadata zip member, so the archive bytes are the same on every run."""
    info = zipfile.ZipInfo(name, date_time=(2026, 11, 22, 0, 0, 0))
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def synthetic_archive() -> bytes:
    """One small primary package whose files include text that is not UTF-8."""
    draft: dict[str, object] = {
        "id": SYNTHETIC, "version": "1.0.0", "role": "primary",
        "metadata": {"label": "Synthetic package reads"},
        "provenance": {"publisher": "sylvia-gm-rust-port",
                       "source_sha256": hashlib.sha256(b"".join(FILES.values())).hexdigest()},
        "compatibility": {"host_contract": 1, "runtime_format": 1, "requires": []},
        "minimum_model_capability_tier": "sylvia.capability.t00",
        "dependencies": [], "capabilities": [], "tools": [], "prompt_fragments": [], "ui_surfaces": [],
        "files": [{"path": path, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                  for path, data in FILES.items()],
    }
    root = content_root_for_record(draft)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(member("manifest.json"),
                         canonical_json_bytes({"format": 1, "packages": [{**draft, "content_sha256": root}]}))
        for path, data in FILES.items():
            archive.writestr(member(f"packages/{SYNTHETIC}/{path}"), data)
    return stream.getvalue()


def install(database: Path) -> None:
    store = a._open_for_application_owner(database)
    try:
        with admit_package_archive(io.BytesIO(synthetic_archive())) as admitted:
            store.stage_package_archive(admitted, "install-reads")
            store.publish_package_archive(admitted, "install-reads")
    finally:
        store.close()
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()


def identities(database: Path) -> dict[str, tuple[str, str, str]]:
    connection = sqlite3.connect(database)
    try:
        rows = connection.execute("SELECT package_id, version, sha256 FROM packages ORDER BY package_id").fetchall()
    finally:
        connection.close()
    return {str(row[0]): (str(row[0]), str(row[1]), str(row[2])) for row in rows}


def operations(packages: dict[str, tuple[str, str, str]]) -> list[dict[str, object]]:
    ruleset, setting = packages["pneuma.riichi-mahjong"], packages["pneuma.riichi-club"]
    synthetic = packages[SYNTHETIC]
    unknown = ("pneuma.unknown", "0.1.0", "0" * 64)
    wrong_sha = (ruleset[0], ruleset[1], "f" * 64)

    def op(kind: str, identity: tuple[str, str, str], **extra: object) -> dict[str, object]:
        return {"op": kind, "package_id": identity[0], "version": identity[1], "sha256": identity[2], **extra}

    return [
        op("package_file_paths", ruleset), op("package_file_paths", setting),
        op("package_file_paths", ruleset, maximum_files=4), op("package_file_paths", ruleset, maximum_files=3),
        op("package_file_paths", ruleset, maximum_files=1), op("package_file_paths", ruleset, maximum_files=0),
        op("package_file_paths", unknown), op("package_file_paths", wrong_sha),
        op("package_text_file", ruleset, portable_path="content/content-declarations.json"),
        op("package_text_file", setting, portable_path="world.md"),
        op("package_text_file", ruleset, portable_path="runtime/ruleset.yaml"),
        op("package_text_file", ruleset, portable_path="runtime/ruleset.yaml", maximum_bytes=200_000),
        op("package_text_file", ruleset, portable_path="support/gameplay-resolution.json", maximum_bytes=421),
        op("package_text_file", ruleset, portable_path="support/gameplay-resolution.json", maximum_bytes=420),
        op("package_text_file", ruleset, portable_path="support/gameplay-resolution.json", maximum_bytes=0),
        op("package_text_file", ruleset, portable_path="support/gameplay-resolution.json", maximum_bytes=-1),
        op("package_text_file", ruleset, portable_path="missing.md"),
        op("package_text_file", ruleset, portable_path="World.md"),
        op("package_text_file", setting, portable_path="world.md", maximum_bytes=-1),
        op("package_text_file", unknown, portable_path="world.md"),
        op("package_file_paths", synthetic),
        op("package_text_file", synthetic, portable_path="notes/broken.txt"),
        op("package_text_file", synthetic, portable_path="notes/broken.txt", maximum_bytes=3),
        op("package_text_file", synthetic, portable_path="notes/glossary.txt"),
        op("package_label", synthetic), op("package_record", synthetic),
        op("package_record", ruleset), op("package_record", setting), op("package_record", unknown),
        op("package_record", wrong_sha),
        op("package_label", ruleset), op("package_label", setting), op("package_label", unknown),
    ]


def digest(text: str) -> dict[str, object]:
    return {"length": len(text), "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def run(owner: AuthorityOwner, op: dict[str, object]) -> dict[str, object]:
    identity = (str(op["package_id"]), str(op["version"]), str(op["sha256"]))
    try:
        if op["op"] == "package_file_paths":
            limit = {"maximum_files": op["maximum_files"]} if "maximum_files" in op else {}
            value: object = list(owner.package_file_paths(*identity, **limit))  # type: ignore[arg-type]
        elif op["op"] == "package_text_file":
            limit = {"maximum_bytes": op["maximum_bytes"]} if "maximum_bytes" in op else {}
            value = digest(owner.package_text_file(*identity, str(op["portable_path"]), **limit))  # type: ignore[arg-type]
        elif op["op"] == "package_record":
            record = owner.package_record(*identity)
            value = digest(a._dump(record))
        else:
            value = owner.package_label(*identity)
    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
        return {"error": type(error).__name__, "code": getattr(error, "code", None), "message": str(error)}
    return {"ok": value}


def main() -> None:
    OUT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=HERE) as scratch:
        database = Path(scratch) / "authority.sqlite3"
        database.write_bytes(gzip.decompress(BASE.read_bytes()))
        install(database)
        (OUT / "base.sqlite3.gz").write_bytes(gzip.compress(database.read_bytes(), mtime=0))
        reads = operations(identities(database))
        with AuthorityOwner.open(database, "package-read-runs") as owner:
            outcomes = [run(owner, op) for op in reads]
    record = {"python": sys.version.split()[0], "operations": reads, "outcomes": outcomes}
    text = json.dumps(record, indent=1, ensure_ascii=True) + "\n"
    (OUT / "package_read_runs.json").write_bytes(text.encode("utf-8"))


if __name__ == "__main__":
    main()
