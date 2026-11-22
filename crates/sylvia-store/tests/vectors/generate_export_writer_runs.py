"""Portable campaign export: what Python's store publishes, and how each failure unwinds.

Run with the snapshot's own interpreter, from the repository root, after
`generate_export_capture_runs.py` (its story profile is a base):

    .venv/Scripts/python.exe -B crates/sylvia-store/tests/vectors/generate_export_writer_runs.py

Each case copies one base into a fresh folder, opens it with `_open_for_application_owner` and
calls `SQLiteAuthorityStore.export_campaign(campaign_id, target, include_thinking=...)`, with
the `_export_failure_stage` seam recording every stage it is called at and acting at the named
ones: raising, raising an `_ExportFailure` of its own, creating the target or replacing it with
a new file, blocking the temporary's journal sidecar (a directory where the cleanup expects a
file), or damaging package blob rows through a connection of its own (the store verifies every
blob when it opens, so they are damaged after the capture, at `metadata_spool`).

The bases are the story profile with private provider records, the campaign-reads profile
(the clockwork campaign with one blob and a campaign without blobs) and the participant profile
(the workshop with six blobs, one of three chunks, and the table). The cases are every export
that succeeds; a fault at the first and the last call of every stage the workshop's export
reaches; cleanup faults stacked on a failure; the destination refusals; namespace changes
before and after the final envelope check; an incomplete cleanup; and package blobs damaged in
the database.

A case records the outcome (the `CampaignExportResult`, or the failure's class, code, message
and supplemental codes), the stages in order, what is left under `exports/` (the private
temporary's random name written as `<temporary>`), the target's SHA-256 and the package read
leases left. The outcomes are the same on Windows and Linux. It writes
`export_writer/export_runs.json`; `tests/export_writer.rs` replays every case on the Rust store.
Reference tooling only.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3] / "src"))

from sylvia_gm.infrastructure import sqlite_authority as a  # noqa: E402

OUT = HERE / "export_writer"
BASES = {
    "story": HERE / "export_capture" / "story.sqlite3.gz",
    "constructed": HERE / "campaigns" / "seed.sqlite3.gz",
    "cast": HERE / "stored_authority" / "cast.sqlite3.gz",
}
TARGET = "exports/archive.sylvia-gm-campaign"
TEMPORARY = re.compile(r"\.[^/]*\.backup-[0-9a-f]{32}\.tmp")
BIG_BLOB = "SELECT blob_sha256 FROM package_blobs WHERE chunk_count = 3"


def case(name: str, base: str, campaign_id: str, **fields: object) -> dict[str, object]:
    return {"name": name, "base": base, "campaign_id": campaign_id, "include_thinking": False,
            "target": TARGET, "setup": [], "sql": [], "faults": [], **fields}


def fault(stage: str, action: str = "raise", occurrence: int | None = 0) -> dict[str, object]:
    return {"stage": stage, "action": action, "occurrence": occurrence}


def workshop(name: str, **fields: object) -> dict[str, object]:
    return case(name, "cast", "campaign-workshop", **fields)


def fixed_cases() -> list[dict[str, object]]:
    return [
        case("story", "story", "campaign-story"),
        case("story_thinking", "story", "campaign-story", include_thinking=True),
        case("clockwork", "constructed", "campaign-clockwork"),
        case("no_blobs", "constructed", "campaign-b"),
        workshop("workshop"),
        case("table", "cast", "campaign-table"),
        case("unknown_campaign", "constructed", "campaign-missing"),
        # Stacked failures: the spool's and the lease's own failures follow the first.
        workshop("read_spool_close_lease", faults=[
            fault("blob_spool_read"), fault("blob_spool_close"), fault("lease_release")]),
        workshop("spool_close_and_lease", faults=[fault("blob_spool_close"), fault("lease_release")]),
        workshop("own_failure_in_member", faults=[fault("blob_spool_read", "export_failure", 2)]),
        workshop("own_failure_at_seal", faults=[fault("seal", "export_failure")]),
        workshop("own_failure_at_lease_release", faults=[fault("lease_release", "export_failure")]),
        workshop("every_member_close", faults=[fault("zip_member_close", occurrence=None)]),
        # The destination.
        case("existing_target_replaced", "constructed", "campaign-clockwork",
             setup=[{"write": TARGET, "text": "an older archive"}]),
        case("parent_missing", "constructed", "campaign-b", target="missing/archive.sylvia-gm-campaign"),
        case("parent_is_file", "constructed", "campaign-b", target="exports/plain.txt/archive.sylvia-gm-campaign",
             setup=[{"write": "exports/plain.txt", "text": "plain"}]),
        case("target_is_directory", "constructed", "campaign-b", setup=[{"mkdir": TARGET}]),
        case("target_is_database", "constructed", "campaign-b", target="authority.sqlite3"),
        case("target_is_database_wal", "constructed", "campaign-b", target="authority.sqlite3-wal"),
        case("target_is_owner_lock", "constructed", "campaign-b", target="authority.sqlite3.owner.lock"),
        case("target_links_database", "constructed", "campaign-b",
             setup=[{"mkdir": "exports"}, {"link": TARGET, "to": "authority.sqlite3"}]),
        # The namespace.
        case("target_appears", "constructed", "campaign-clockwork",
             faults=[fault("verifier_close", "create_target")]),
        case("target_recreated", "constructed", "campaign-clockwork",
             setup=[{"write": TARGET, "text": "an older archive"}],
             faults=[fault("verifier_close", "recreate_target")]),
        case("target_appears_after_check", "constructed", "campaign-clockwork",
             faults=[fault("move_file_ex", "create_target")]),
        case("cleanup_incomplete", "constructed", "campaign-clockwork",
             faults=[fault("hash", "block_journal"), fault("hash")]),
        # Package blobs damaged in the database.
        workshop("chunk_digest_damaged", faults=[fault("metadata_spool", "damage")], sql=[
            f"UPDATE package_blob_chunks SET chunk_sha256 = '{'0' * 64}' "
            f"WHERE chunk_order = 1 AND blob_sha256 = ({BIG_BLOB})"]),
        workshop("chunk_missing", faults=[fault("metadata_spool", "damage")], sql=[
            f"DELETE FROM package_blob_chunks WHERE chunk_order = 2 AND blob_sha256 = ({BIG_BLOB})"]),
        workshop("chunk_count_wrong", faults=[fault("metadata_spool", "damage")], sql=[
            f"UPDATE package_blobs SET chunk_count = 4 WHERE blob_sha256 = ({BIG_BLOB})"]),
        workshop("chunk_text", faults=[fault("metadata_spool", "damage")], sql=[
            f"UPDATE package_blob_chunks SET data = CAST(data AS TEXT) WHERE blob_sha256 = ({BIG_BLOB})"]),
        workshop("chunk_integer", faults=[fault("metadata_spool", "damage")], sql=[
            f"UPDATE package_blob_chunks SET data = 3 WHERE chunk_order = 0 AND blob_sha256 = ({BIG_BLOB})"]),
        workshop("chunk_order_text", faults=[fault("metadata_spool", "damage")], sql=[
            f"UPDATE package_blob_chunks SET chunk_order = 'one' WHERE chunk_order = 1 "
            f"AND blob_sha256 = ({BIG_BLOB})"]),
        workshop("chunk_bytes_wrong", faults=[fault("metadata_spool", "damage")], sql=[
            f"UPDATE package_blob_chunks SET byte_count = 7 WHERE chunk_order = 0 AND blob_sha256 = ({BIG_BLOB})"]),
    ]


def listing(folder: Path, spec: dict[str, object]) -> list[list[object]]:
    """Everything under `exports/`, relative, with each file's SHA-256 (a hard link the case
    made to the database is listed without one: the database's bytes are not compared)."""
    exports = folder / "exports"
    if not exports.exists():
        return []
    links = {step["link"] for step in spec["setup"] if "link" in step}  # type: ignore[union-attr]
    entries = []
    for path in sorted(exports.rglob("*"), key=lambda item: item.relative_to(folder).as_posix()):
        relative = TEMPORARY.sub("<temporary>", path.relative_to(folder).as_posix())
        if relative in links:
            entries.append([relative, "link", None])
        elif path.is_dir():
            entries.append([relative, "dir", None])
        else:
            entries.append([relative, "file", hashlib.sha256(path.read_bytes()).hexdigest()])
    return entries


def prepare(folder: Path, spec: dict[str, object]) -> None:
    for step in spec["setup"]:  # type: ignore[union-attr]
        if "mkdir" in step:
            (folder / step["mkdir"]).mkdir(parents=True)
        elif "write" in step:
            path = folder / step["write"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(step["text"].encode("utf-8"))
        else:
            os.link(folder / step["to"], folder / step["link"])
    (folder / "exports").mkdir(exist_ok=True)


def damage(database: Path, statements: list[str]) -> None:
    """The case's statements, through a connection of their own (the store verifies every
    package blob when it opens, so blobs are damaged only once it is open)."""
    connection = sqlite3.connect(database)
    for statement in statements:
        connection.execute(statement)
    connection.commit()
    connection.close()


def run(folder: Path, spec: dict[str, object]) -> dict[str, object]:
    store = a._open_for_application_owner(folder / "authority.sqlite3")  # noqa: SLF001
    target = folder / str(spec["target"])
    stages: list[str] = []
    seen: dict[str, int] = {}

    def stage_fault(stage: str) -> None:
        stages.append(stage)
        occurrence = seen.get(stage, 0)
        seen[stage] = occurrence + 1
        for armed in spec["faults"]:  # type: ignore[union-attr]
            if armed["stage"] != stage or armed["occurrence"] not in (None, occurrence):
                continue
            action = armed["action"]
            if action == "raise":
                raise RuntimeError(f"fault at {stage}")
            if action == "export_failure":
                raise a._ExportFailure("portable_export_injected")  # noqa: SLF001
            if action == "create_target":
                target.write_bytes(b"an intruder")
            elif action == "recreate_target":
                # Written beside it, then renamed over it: a new file identity on every platform
                # (an unlink and a new file can reuse the freed inode on ext4).
                replacement = target.with_name(f"{target.name}.new")
                replacement.write_bytes(b"an intruder")
                os.replace(replacement, target)
            elif action == "damage":
                damage(folder / "authority.sqlite3", spec["sql"])  # type: ignore[arg-type]
            elif action == "block_journal":
                (temporary,) = [path for path in target.parent.iterdir() if TEMPORARY.fullmatch(path.name)]
                (Path(f"{temporary}-journal") / "held").mkdir(parents=True)

    store._export_failure_stage = stage_fault  # type: ignore[method-assign]  # noqa: SLF001
    try:
        result = store.export_campaign(str(spec["campaign_id"]), target,
                                       include_thinking=bool(spec["include_thinking"]))
        outcome: dict[str, object] = {"ok": {
            "campaign_id": result.campaign_id, "captured_revision": result.captured_revision,
            "archive_sha256": result.archive_sha256, "byte_count": result.byte_count}}
    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
        outcome = {"error": type(error).__name__, "code": getattr(error, "code", None), "message": str(error),
                   "supplemental_codes": list(getattr(error, "supplemental_codes", ()))}
    finally:
        store.close()
    connection = sqlite3.connect(folder / "authority.sqlite3")
    leases = connection.execute("SELECT COUNT(*) FROM package_read_leases").fetchone()[0]
    connection.close()
    published = None
    linked = any(step.get("link") == spec["target"] for step in spec["setup"])  # type: ignore[union-attr]
    if target.is_file() and target.parent.name == "exports" and not linked:
        published = hashlib.sha256(target.read_bytes()).hexdigest()
    return {"outcome": outcome, "stages": stages, "exports": listing(folder, spec), "target_sha256": published,
            "leases": leases}


def sweep(reached: list[str]) -> list[dict[str, object]]:
    """A fault at the first and the last call of every stage the workshop's export reaches."""
    counts: dict[str, int] = {}
    for stage in reached:
        counts[stage] = counts.get(stage, 0) + 1
    cases = []
    for stage, count in counts.items():
        cases.append(workshop(f"fault_{stage}_first", faults=[fault(stage)]))
        if count > 1:
            cases.append(workshop(f"fault_{stage}_last", faults=[fault(stage, occurrence=count - 1)]))
    return cases


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    bases = {name: gzip.decompress(path.read_bytes()) for name, path in BASES.items()}
    results = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        def replay(spec: dict[str, object]) -> dict[str, object]:
            folder = Path(scratch) / f"case-{len(results)}"
            folder.mkdir()
            (folder / "authority.sqlite3").write_bytes(bases[str(spec["base"])])
            prepare(folder, spec)
            recorded = {**spec, **run(folder, spec)}
            results.append(recorded)
            shutil.rmtree(folder, ignore_errors=True)
            return recorded

        cases = fixed_cases()
        reached = replay(cases[4])["stages"]
        assert cases[4]["name"] == "workshop"
        for spec in cases[:4] + cases[5:] + sweep(reached):  # type: ignore[arg-type]
            replay(spec)
    record = {"python": sys.version.split()[0], "cases": results}
    (OUT / "export_runs.json").write_bytes((json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
