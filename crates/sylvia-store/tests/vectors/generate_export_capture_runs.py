"""Portable export capture: what Python's store captures from each profile, and its failures.

Run with the snapshot's own interpreter, from the repository root, after
`generate_package_runs.py`, `generate_session_page_runs.py`, `generate_campaign_index.py` and
`generate_stored_authority_runs.py` (their profiles are the bases):

    .venv/Scripts/python.exe -B crates/sylvia-store/tests/vectors/generate_export_capture_runs.py

Each case opens one base with `_open_for_application_owner` and calls
`SQLiteAuthorityStore._capture_campaign_export(campaign_id, include_thinking=...)`, optionally
with the `_export_failure_stage` seam raising at named stages. The bases are the session-page
profile with evidence to stub and rewrite and private provider records (`story`, written here
as `export_capture/story.sqlite3.gz`), the campaign-reads profile (`constructed`), the
participant profile (`cast`), the new and Riichi profiles, and a schema-4 profile (`v4`). A capture is recorded by its
campaign, revision, schema claim, provenance, table counts, the SHA-256 and length of its
authority and private members, its blobs, and the bytes of the manifest `make_manifest` makes
from it; a failure by its class, code, message and supplemental codes. It writes
`export_capture/capture_runs.json`; `tests/export_capture.rs` replays every case on the Rust
store. Reference tooling only.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import random
import shutil
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3] / "src"))

_IDENTITIES = random.Random(20261125)
uuid.uuid4 = lambda: uuid.UUID(int=_IDENTITIES.getrandbits(128), version=4)  # type: ignore[assignment]

from sylvia_gm.application.authority import AuthorityOwner  # noqa: E402
from sylvia_gm.domain.provider_records import ProviderPrivateRecord  # noqa: E402
from sylvia_gm.domain.session import SessionMessageKind  # noqa: E402
from sylvia_gm.infrastructure import campaign_archive, private_records_archive  # noqa: E402
from sylvia_gm.infrastructure import sqlite_authority as a  # noqa: E402

OUT = HERE / "export_capture"
BASES = {
    "constructed": HERE / "campaigns" / "seed.sqlite3.gz",
    "cast": HERE / "stored_authority" / "cast.sqlite3.gz",
    "fresh": HERE / "packages" / "seed_fresh.sqlite3.gz",
    "riichi": HERE / "packages" / "seed_riichi.sqlite3.gz",
    # Schema 4: startup does not advance it, so the export refuses its source schema.
    "v4": HERE / "packages" / "participants_v4.sqlite3.gz",
}
STORY_SEED = HERE / "session_pages" / "seed.sqlite3.gz"


def story_base(folder: Path) -> bytes:
    """The session-page profile plus evidence the export stubs or rewrites and private provider
    records for `dm-1`, made by Python's owner."""
    path = folder / "story" / "authority.sqlite3"
    path.parent.mkdir()
    path.write_bytes(gzip.decompress(STORY_SEED.read_bytes()))
    with AuthorityOwner.open(path, "export-capture") as owner:
        for message_id, content in (
            ("ev-journal", {"kind": "gm_wire_journal", "entries": [{"turn": 1, "note": "x" * 40}]}),
            ("ev-native", {"kind": "gm_native_records", "records": ["C:/Users/someone/.claude/x.jsonl"]}),
            ("ev-source", {"kind": "builder_tool_result", "source_locator": "C:/Users/someone/notes.md", "q": 2}),
            ("ev-plain", {"kind": "builder_tool_result", "tool_name": "ask_question", "q": 3}),
            ("ev-unicode", {"kind": "builder_tool_result", "source_locator": "D:/Ünder/ß.md", "q": "ö"}),
        ):
            owner.append_agent_message(session_id="dm-1", message_id=message_id, kind=SessionMessageKind.EVIDENCE,
                                       content=content)
        for order, (kind, payload) in enumerate((
            ("thinking", "Considering the tide."),
            ("continuation", json.dumps({"type": "reasoning", "id": "r-1"})),
        )):
            owner.append_private_provider_record(ProviderPrivateRecord(
                session_id="dm-1", attempt_id="att-9", response_id="resp-9", turn_id="turn-2",
                basis_message_id="ev-plain", source_order=order, provider_id="standin",
                model_id="standin/standin-narrator", kind=kind, payload=payload))
        owner.finish_private_provider_attempt("dm-1", "att-9", completed=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()
    return path.read_bytes()


STAGES = ("capture_open", "capture_begin", "capture_preflight", "capture_read", "capture_encode",
          "capture_commit")
CASES: list[dict[str, object]] = [
    {"base": "story", "campaign_id": "campaign-story", "include_thinking": False},
    {"base": "story", "campaign_id": "campaign-story", "include_thinking": True},
    {"base": "story", "campaign_id": "campaign-build", "include_thinking": True},
    {"base": "story", "campaign_id": "campaign-missing", "include_thinking": False},
    *({"base": "constructed", "campaign_id": campaign_id, "include_thinking": False} for campaign_id in (
        "1509bb0a-9bf1-4331-8a11-8783593942a4", "campaign-clockwork", "f5ec52ed-0108-4475-92e1-bc7656670b14",
        "campaign-b", "campaign-a2", "campaign-a1")),
    {"base": "cast", "campaign_id": "campaign-table", "include_thinking": False},
    {"base": "cast", "campaign_id": "campaign-workshop", "include_thinking": False},
    {"base": "fresh", "campaign_id": "campaign-story", "include_thinking": False},
    {"base": "riichi", "campaign_id": "campaign-story", "include_thinking": False},
    {"base": "v4", "campaign_id": "campaign-1", "include_thinking": False},
    # A value past 16 MiB, set by a second connection: the preflight's value bound refuses it.
    {"base": "story", "campaign_id": "campaign-story", "include_thinking": False, "sql": [
        "UPDATE campaigns SET display_name = replace(hex(zeroblob(8388609)), '0', 'x') "
        "WHERE campaign_id = 'campaign-story'"]},
    *({"base": "story", "campaign_id": "campaign-story", "include_thinking": True, "faults": [stage]}
      for stage in STAGES),
    {"base": "story", "campaign_id": "campaign-story", "include_thinking": False,
     "faults": ["capture_read", "capture_rollback"]},
    {"base": "story", "campaign_id": "campaign-story", "include_thinking": False, "faults": ["capture_close"]},
    {"base": "story", "campaign_id": "campaign-story", "include_thinking": False,
     "faults": ["capture_encode", "capture_rollback", "capture_close"]},
]


def digest(data: bytes | None) -> dict[str, object] | None:
    return None if data is None else {"sha256": hashlib.sha256(data).hexdigest(), "length": len(data)}


def captured(capture: object) -> dict[str, object]:
    authority = capture.authority_bytes  # type: ignore[attr-defined]
    members = [{"name": "authority.jsonl", "byte_count": len(authority), "sha256": hashlib.sha256(authority).hexdigest()}]
    private = capture.private_bytes  # type: ignore[attr-defined]
    if private is not None:
        members.append({"name": private_records_archive.MEMBER_NAME, "byte_count": len(private),
                        "sha256": hashlib.sha256(private).hexdigest()})
    manifest = campaign_archive.manifest_bytes(campaign_archive.make_manifest(
        source_schema_version=capture.source_schema_version,  # type: ignore[attr-defined]
        source_schema_identity_sha256=capture.source_schema_identity_sha256,  # type: ignore[attr-defined]
        migration_provenance=capture.migration_provenance,  # type: ignore[attr-defined]
        campaign_id=capture.campaign_id,  # type: ignore[attr-defined]
        captured_revision=capture.captured_revision,  # type: ignore[attr-defined]
        rows_by_table=capture.rows_by_table,  # type: ignore[attr-defined]
        non_manifest_members=members))
    return {
        "campaign_id": capture.campaign_id,  # type: ignore[attr-defined]
        "captured_revision": capture.captured_revision,  # type: ignore[attr-defined]
        "source_schema_version": capture.source_schema_version,  # type: ignore[attr-defined]
        "source_schema_identity_sha256": capture.source_schema_identity_sha256,  # type: ignore[attr-defined]
        "migration_provenance": json.loads(json.dumps(capture.migration_provenance)),  # type: ignore[attr-defined]
        "table_row_counts": {table: len(rows) for table, rows in capture.rows_by_table.items()},  # type: ignore[attr-defined]
        "authority": digest(authority),
        "private": digest(private),
        "blob_sha256s": list(capture.blob_sha256s),  # type: ignore[attr-defined]
        "members": members,
        "manifest": digest(manifest),
    }


def run(path: Path, case: dict[str, object]) -> dict[str, object]:
    store = a._open_for_application_owner(path)
    faults = set(case.get("faults", ()))  # type: ignore[arg-type]

    def stage_fault(stage: str) -> None:
        if stage in faults:
            raise RuntimeError(f"fault at {stage}")

    store._export_failure_stage = stage_fault  # type: ignore[method-assign]  # noqa: SLF001
    try:
        capture = store._capture_campaign_export(  # noqa: SLF001
            str(case["campaign_id"]), include_thinking=bool(case["include_thinking"]))
    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
        return {"error": type(error).__name__, "code": getattr(error, "code", None), "message": str(error),
                "supplemental_codes": list(getattr(error, "supplemental_codes", ()))}
    finally:
        store.close()
    return {"ok": captured(capture)}


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    results = []
    with tempfile.TemporaryDirectory(dir=HERE, ignore_cleanup_errors=True) as scratch:
        bases = {name: gzip.decompress(path.read_bytes()) for name, path in BASES.items()}
        bases["story"] = story_base(Path(scratch))
        (OUT / "story.sqlite3.gz").write_bytes(gzip.compress(bases["story"], mtime=0))
        for index, case in enumerate(CASES):
            folder = Path(scratch) / f"case-{index}"
            folder.mkdir()
            path = folder / "authority.sqlite3"
            path.write_bytes(bases[str(case["base"])])
            if case.get("sql"):
                connection = sqlite3.connect(path)
                for statement in case["sql"]:  # type: ignore[union-attr]
                    connection.execute(statement)
                connection.commit()
                connection.close()
            results.append({**case, "outcome": run(path, case)})
    record = {"python": sys.version.split()[0], "cases": results}
    (OUT / "capture_runs.json").write_bytes((json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
