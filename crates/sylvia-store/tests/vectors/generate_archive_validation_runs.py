"""Portable campaign archive validation: Python's verdict on its own exports and damaged copies.

Run with the snapshot's own interpreter, from the repository root, after
`generate_export_capture_runs.py`, `generate_campaign_index.py` and
`generate_stored_authority_runs.py` (their profiles are the bases):

    .venv/Scripts/python.exe -B crates/sylvia-store/tests/vectors/generate_archive_validation_runs.py

Python's owner exports four campaigns (`AuthorityOwner.export_campaign`): the story campaign
with its private provider records, the clockwork campaign (one blob), a campaign without blobs
and the participant workshop (six blobs). Each archive is then damaged the way a foreign or
corrupted archive would arrive: rebuilt with `zipfile` and changed members, names, order or
metadata, or patched byte by byte. Where the damage must reach a later check, the manifest's
member sizes and digests are recomputed and it is written canonically again.
`campaign_archive.validate_campaign_archive` gives each verdict: the SHA-256 of the manifest it
returns, or the code of its refusal. It writes `archive_validation/archives.bin.gz` (every
archive, concatenated) and `archive_validation/validation_runs.json` (each case's name, offset,
length, expected campaign and verdict). `tests/archive_validation.rs` validates every archive
with the Rust store. Reference tooling only.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import random
import shutil
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3] / "src"))

_IDENTITIES = random.Random(20261126)
uuid.uuid4 = lambda: uuid.UUID(int=_IDENTITIES.getrandbits(128), version=4)  # type: ignore[assignment]

from sylvia_gm.application.authority import AuthorityOwner  # noqa: E402
from sylvia_gm.domain.errors import CommandRejected  # noqa: E402
from sylvia_gm.infrastructure import campaign_archive  # noqa: E402

OUT = HERE / "archive_validation"
EXPORTS = {
    "story": (HERE / "export_capture" / "story.sqlite3.gz", "campaign-story", True),
    "clockwork": (HERE / "campaigns" / "seed.sqlite3.gz", "campaign-clockwork", False),
    "plain": (HERE / "campaigns" / "seed.sqlite3.gz", "campaign-b", False),
    "workshop": (HERE / "stored_authority" / "cast.sqlite3.gz", "campaign-workshop", False),
}


def export(scratch: Path, name: str) -> bytes:
    source, campaign_id, thinking = EXPORTS[name]
    folder = scratch / f"export-{name}"
    folder.mkdir()
    database = folder / "authority.sqlite3"
    database.write_bytes(gzip.decompress(source.read_bytes()))
    target = folder / "archive.sylvia-gm-campaign"
    with AuthorityOwner.open(database, "archive-validation") as owner:
        owner.export_campaign("export-1", campaign_id, target, include_thinking=thinking)
    return target.read_bytes()


def members(data: bytes) -> list[tuple[str, bytes]]:
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        return [(info.filename, source.read(info)) for info in source.infolist()]


def rebuild(items: list[tuple[str, bytes]], *, comment: bytes = b"", overrides: dict | None = None) -> bytes:
    """A stored archive of `items` with `fixed_zip_info` members; `overrides[name]` sets
    `ZipInfo` attributes of that member."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as output:
        for name, data in items:
            info = campaign_archive.fixed_zip_info(name)
            for attribute, value in (overrides or {}).get(name, {}).items():
                setattr(info, attribute, value)
            output.writestr(info, data)
        output.comment = comment
    return buffer.getvalue()


def manifest_of(items: list[tuple[str, bytes]]) -> dict:
    return json.loads(dict(items)["manifest.json"])


def with_manifest(items: list[tuple[str, bytes]], manifest: dict) -> list[tuple[str, bytes]]:
    encoded = campaign_archive._json_bytes(manifest)  # noqa: SLF001 - the canonical writer
    return [(name, encoded if name == "manifest.json" else data) for name, data in items]


def redeclare(items: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
    """The manifest's member sizes and digests recomputed for the members as they now are."""
    manifest = manifest_of(items)
    data = dict(items)
    for member in manifest["non_manifest_members"]:
        if member["name"] in data:
            member["byte_count"] = len(data[member["name"]])
            member["sha256"] = hashlib.sha256(data[member["name"]]).hexdigest()
    return with_manifest(items, manifest)


def replace_member(items: list[tuple[str, bytes]], name: str, data: bytes) -> list[tuple[str, bytes]]:
    return [(member, data if member == name else value) for member, value in items]


def authority_lines(items: list[tuple[str, bytes]]) -> list[bytes]:
    return dict(items)["authority.jsonl"].split(b"\n")[:-1]


def with_authority(items: list[tuple[str, bytes]], lines: list[bytes], *, final_newline: bool = True) -> list[tuple[str, bytes]]:
    data = b"\n".join(lines) + (b"\n" if final_newline else b"")
    return redeclare(replace_member(items, "authority.jsonl", data))


def edit_rows(items: list[tuple[str, bytes]], table: str, change) -> list[tuple[str, bytes]]:  # noqa: ANN001
    """The first `table` row of the authority member changed by `change(row)`, written back
    canonically, with the manifest's member digests recomputed."""
    lines = authority_lines(items)
    for index, line in enumerate(lines):
        record = json.loads(line)
        if record["table"] == table:
            row = dict(record["row"])
            change(row)
            lines[index] = campaign_archive.canonical_authority_record(table, row)[:-1]
            break
    else:
        raise AssertionError(f"no {table} row")
    return with_authority(items, lines)


def edit_manifest(items: list[tuple[str, bytes]], change) -> list[tuple[str, bytes]]:  # noqa: ANN001
    manifest = manifest_of(items)
    change(manifest)
    return with_manifest(items, manifest)


def patch_member_byte(data: bytes, member: str) -> bytes:
    """Flip one stored byte of `member` without updating its CRC."""
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        info = source.getinfo(member)
    name_length = int.from_bytes(data[info.header_offset + 26:info.header_offset + 28], "little")
    extra_length = int.from_bytes(data[info.header_offset + 28:info.header_offset + 30], "little")
    start = info.header_offset + 30 + name_length + extra_length
    patched = bytearray(data)
    patched[start] = ord("[") if patched[start] != ord("[") else ord("{")
    return bytes(patched)


def cases(archives: dict[str, bytes]) -> list[tuple[str, bytes, str]]:
    story, clockwork, plain, workshop = (members(archives[name]) for name in ("story", "clockwork", "plain", "workshop"))
    campaign = {name: EXPORTS[name][1] for name in EXPORTS}

    def clock(data: bytes) -> tuple[bytes, str]:
        return data, campaign["clockwork"]

    lines = authority_lines(clockwork)
    blobs = [name for name, _ in workshop if name.startswith("blobs/")]
    out: list[tuple[str, bytes, str]] = [(f"{name}_exported", data, campaign[name]) for name, data in archives.items()]
    built: list[tuple[str, tuple[bytes, str]]] = [
        ("rebuilt", clock(rebuild(clockwork))),
        ("truncated", clock(archives["clockwork"][:100])),
        ("not_zip", clock(b"not a zip archive")),
        ("archive_comment", clock(rebuild(clockwork, comment=b"x"))),
        ("member_comment", clock(rebuild(clockwork, overrides={"authority.jsonl": {"comment": b"x"}}))),
        ("member_extra", clock(rebuild(clockwork, overrides={"authority.jsonl": {"extra": b"\x99\x99\x00\x00"}}))),
        ("member_date", clock(rebuild(clockwork, overrides={"manifest.json": {"date_time": (2020, 1, 1, 0, 0, 0)}}))),
        ("member_deflated", clock(rebuild(clockwork, overrides={"manifest.json": {"compress_type": zipfile.ZIP_DEFLATED}}))),
        ("member_mode", clock(rebuild(clockwork, overrides={"manifest.json": {"external_attr": 0o100644 << 16}}))),
        ("member_create_system", clock(rebuild(clockwork, overrides={"manifest.json": {"create_system": 0}}))),
        ("member_internal_attr", clock(rebuild(clockwork, overrides={"manifest.json": {"internal_attr": 1}}))),
        ("duplicate_member", clock(rebuild([clockwork[0], *clockwork]))),
        ("alias_case", clock(rebuild([clockwork[0], ("AUTHORITY.jsonl", b"x"), *clockwork[1:]]))),
        ("alias_nfc", clock(rebuild([clockwork[0], ("café.txt", b"x"), ("café.txt", b"y"), *clockwork[1:]]))),
        ("alias_sharp_s", clock(rebuild([clockwork[0], ("straße.txt", b"x"), ("strasse.txt", b"y"), *clockwork[1:]]))),
        ("alias_long_s", clock(rebuild([clockwork[0], ("ſ.txt", b"x"), ("s.txt", b"y"), *clockwork[1:]]))),
        ("no_alias_distinct", clock(rebuild([clockwork[0], ("café.txt", b"x"), ("cafe.txt", b"y"), *clockwork[1:]]))),
        ("path_parent", clock(rebuild([clockwork[0], ("blobs/../x", b"x"), *clockwork[1:]]))),
        ("path_drive", clock(rebuild([clockwork[0], ("C:x", b"x"), *clockwork[1:]]))),
        ("path_absolute", clock(rebuild([clockwork[0], ("/x", b"x"), *clockwork[1:]]))),
        ("path_dot", clock(rebuild([clockwork[0], ("./x", b"x"), *clockwork[1:]]))),
        ("order_manifest_first", clock(rebuild([clockwork[-1], *clockwork[:-1]]))),
        ("order_authority_last", clock(rebuild([*clockwork[1:-1], clockwork[0], clockwork[-1]]))),
        ("undeclared_member", clock(rebuild([*clockwork[:-1], ("notes.txt", b"x"), clockwork[-1]]))),
        ("manifest_not_json", clock(rebuild(replace_member(clockwork, "manifest.json", b"{")))),
        ("manifest_bom", clock(rebuild(replace_member(clockwork, "manifest.json", b"\xef\xbb\xbf" + dict(clockwork)["manifest.json"])))),
        ("manifest_spaced", clock(rebuild(replace_member(clockwork, "manifest.json", json.dumps(manifest_of(clockwork), indent=1).encode())))),
        ("manifest_missing_member", clock(rebuild(edit_manifest(clockwork, lambda m: m.pop("blob_count"))))),
        ("manifest_format_version", clock(rebuild(edit_manifest(clockwork, lambda m: m.update(format_version=2))))),
        ("manifest_format_version_true", clock(rebuild(edit_manifest(clockwork, lambda m: m.update(format_version=True))))),
        ("manifest_captured_revision", clock(rebuild(edit_manifest(clockwork, lambda m: m.update(captured_revision=m["captured_revision"] + 1))))),
        ("manifest_table_count", clock(rebuild(edit_manifest(clockwork, lambda m: m["table_row_counts"].update(campaigns=2))))),
        ("manifest_closure_index", clock(rebuild(edit_manifest(clockwork, lambda m: m["closure_index"]["lock_ids"].append("lock-zzz"))))),
        ("manifest_member_hash", clock(rebuild(edit_manifest(clockwork, lambda m: m["non_manifest_members"][0].update(sha256="0" * 64))))),
        ("manifest_member_size", clock(rebuild(edit_manifest(clockwork, lambda m: m["non_manifest_members"][0].update(byte_count=m["non_manifest_members"][0]["byte_count"] + 1))))),
        ("manifest_member_size_text", clock(rebuild(edit_manifest(clockwork, lambda m: m["non_manifest_members"][0].update(byte_count=str(m["non_manifest_members"][0]["byte_count"])))))),
        ("manifest_blob_count", clock(rebuild(edit_manifest(clockwork, lambda m: m.update(blob_count=m["blob_count"] + 1))))),
        ("manifest_schema_claim", clock(rebuild(edit_manifest(clockwork, lambda m: m.update(source_authority_schema_identity_sha256="1" * 64))))),
        ("manifest_product", clock(rebuild(edit_manifest(clockwork, lambda m: m.update(product_generation="beta"))))),
        ("authority_cr", clock(rebuild(with_authority(clockwork, [lines[0] + b"\r", *lines[1:]])))),
        ("authority_unknown_table", clock(rebuild(with_authority(clockwork, [lines[0].replace(b'"table":"campaigns"', b'"table":"nope"'), *lines[1:]])))),
        ("authority_spaced", clock(rebuild(with_authority(clockwork, [json.dumps(json.loads(lines[0])).encode(), *lines[1:]])))),
        ("authority_order", clock(rebuild(with_authority(clockwork, [lines[1], lines[0], *lines[2:]])))),
        ("authority_empty_line", clock(rebuild(with_authority(clockwork, [lines[0], b"", *lines[1:]])))),
        ("authority_no_final_newline", clock(rebuild(with_authority(clockwork, lines, final_newline=False)))),
        ("authority_duplicate_row", clock(rebuild(with_authority(clockwork, [lines[0], lines[0], *lines[1:]])))),
        ("authority_extra_column", clock(rebuild(with_authority(clockwork, [lines[0].replace(b'"row":{', b'"row":{"extra":1,'), *lines[1:]])))),
        ("authority_other_campaign", clock(rebuild(with_authority(clockwork, [lines[0].replace(b'"campaign_id":"campaign-clockwork"', b'"campaign_id":"campaign-other"', 1), *lines[1:]])))),
        ("crc_damage", clock(patch_member_byte(archives["clockwork"], "manifest.json"))),
        ("expected_other", (archives["clockwork"], "campaign-other")),
    ]
    out.extend((name, data, expected) for name, (data, expected) in built)
    private = campaign_archive.private_records_archive.MEMBER_NAME
    story_index = [name for name, _ in story].index(private)
    out.extend([
        ("private_misplaced", rebuild([story[0], story[story_index], *story[1:story_index], *story[story_index + 1:]]), campaign["story"]),
        ("private_bad_line", rebuild(redeclare(replace_member(story, private, b"x\n"))), campaign["story"]),
        ("private_foreign_session", rebuild(redeclare(replace_member(story, private, dict(story)[private].replace(b'"session_id":"dm-1"', b'"session_id":"dm-9"')))), campaign["story"]),
        ("private_without_format", rebuild(edit_manifest(story, lambda m: m.update(format_version=1))), campaign["story"]),
        ("blobs_unsorted", rebuild([workshop[0], *reversed(workshop[1:-1]), workshop[-1]]), campaign["workshop"]),
        ("blob_removed", rebuild(edit_manifest(
            [item for item in workshop if item[0] != blobs[0]],
            lambda m: (m.update(blob_count=m["blob_count"] - 1),
                       m.update(non_manifest_members=[x for x in m["non_manifest_members"] if x["name"] != blobs[0]])))),
         campaign["workshop"]),
        ("blob_altered", rebuild(redeclare(replace_member(workshop, blobs[0], bytes(reversed(dict(workshop)[blobs[0]]))))), campaign["workshop"]),
        ("plain_as_story", archives["plain"], campaign["story"]),
    ])
    # The streamed closure and package graph: one authority row changed, canonically.
    for name, items, table, change in (
        ("closure_missing_parent", story, "idempotency_records", lambda row: row.update(action_id="action-x")),
        ("closure_revision_ahead", story, "campaigns", lambda row: row.update(revision=row["revision"] + 1)),
        ("closure_current_lock_unknown", story, "campaigns", lambda row: row.update(current_lock_id="lock-x")),
        ("closure_namespace_owner", story, "campaign_state_namespace_owners", lambda row: row.update(owner_package_id="other.package")),
        ("closure_adaptation", workshop, "participants", lambda row: row.update(adaptation_id="adapt-x")),
        ("graph_package_digest", story, "package_tools", lambda row: row.update(capability="other")),
        ("graph_manifest_noncanonical", story, "package_manifests", lambda row: row.update(canonical_json=row["canonical_json"] + " ")),
        ("graph_lock_closure", story, "campaign_locks", lambda row: row.update(closure_sha256="0" * 64)),
        ("graph_lock_role", story, "campaign_lock_memberships", lambda row: row.update(role="dependency")),
        ("graph_staging_status", story, "package_staging", lambda row: row.update(status="staging")),
        ("graph_candidate_report", story, "package_candidates", lambda row: row.update(report_json=row["report_json"] + " ")),
        ("graph_action_tool", story, "actions", lambda row: row.update(tool_name="unknown_tool")),
        ("graph_participant_schema", workshop, "participants", lambda row: row.update(schema_sha256="0" * 64)),
        ("graph_handoff_candidate", story, "builder_dm_handoffs", lambda row: row.update(package_candidate_id=None)),
    ):
        owner = campaign["story"] if items is story else campaign["workshop"]
        out.append((name, rebuild(edit_rows(items, table, change)), owner))
    return out


def verdict(path: Path, expected: str) -> dict[str, object]:
    try:
        manifest = campaign_archive.validate_campaign_archive(path, expected_campaign_id=expected)
    except CommandRejected as error:
        return {"error": type(error).__name__, "code": error.code, "message": str(error)}
    return {"ok": hashlib.sha256(campaign_archive._json_bytes(dict(manifest))).hexdigest()}  # noqa: SLF001


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    blob = bytearray()
    records = []
    with tempfile.TemporaryDirectory(dir=HERE, ignore_cleanup_errors=True) as scratch_name:
        scratch = Path(scratch_name)
        archives = {name: export(scratch, name) for name in EXPORTS}
        for name, data, expected in cases(archives):
            path = scratch / f"{name}.sylvia-gm-campaign"
            path.write_bytes(data)
            records.append({"name": name, "offset": len(blob), "length": len(data), "expected_campaign_id": expected,
                            "verdict": verdict(path, expected)})
            blob.extend(data)
    (OUT / "archives.bin.gz").write_bytes(gzip.compress(bytes(blob), mtime=0))
    record = {"python": sys.version.split()[0], "cases": records}
    (OUT / "validation_runs.json").write_bytes((json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
