"""Checked-in Lite records must describe the exact payload before bundle adaptation."""

import hashlib
import io
import json
import zipfile
from copy import deepcopy
from dataclasses import replace

import pytest

from sylvia_gm.application import bundled_packages as bundled
from sylvia_gm.application.package_materialization import materialize_package_archive
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import (
    canonical_json_bytes,
    content_root_for_record,
    manifest_from_record,
)
from sylvia_gm.infrastructure.package_archive import admit_package_archive


def _checked_record():
    return json.loads((bundled._bundled_srd_root() / "package-record.json").read_bytes())


def test_checked_lite_record_is_canonical_and_describes_every_payload_byte():
    record = _checked_record()
    manifest = manifest_from_record(record, format_version=4)
    rules, reference = bundled._srd_drafts()
    assert manifest.content_sha256 == content_root_for_record(record)
    assert record == rules.record
    for draft in (rules, reference):
        manifest_from_record(draft.record, format_version=4)
        assert draft.record["files"] == [
            {"path": path, "bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()}
            for path, value in sorted(draft.payloads.items())
        ]


def test_record_regeneration_is_idempotent_and_archive_admission_keeps_its_root():
    drafts = bundled._srd_drafts()
    before = deepcopy(drafts)
    with materialize_package_archive(drafts) as first:
        raw = first.archive.read()
        expected_lock = first.package_lock
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        completed = json.loads(archive.read("manifest.json"))["packages"]
    by_id = {record["id"]: record for record in completed}
    assert by_id[drafts[0].record["id"]] == _checked_record()
    regenerated = tuple(replace(draft, record=by_id[draft.record["id"]]) for draft in drafts)
    with materialize_package_archive(regenerated) as second:
        assert second.archive.read() == raw
        assert second.package_lock == expected_lock
    with admit_package_archive(io.BytesIO(raw)) as admitted:
        assert admitted.package_lock == expected_lock
        primary = next(item for item in admitted.manifests if item.role.value == "primary")
        assert primary.content_sha256 == _checked_record()["content_sha256"]
    assert drafts == before


@pytest.mark.parametrize("corruption", ["root", "hash", "size", "missing", "dependency"])
def test_bundle_export_rejects_independently_stale_source_claims(monkeypatch, corruption):
    rules, reference = bundled._srd_source_drafts()
    record = deepcopy(rules.record)
    runtime = next(item for item in record["files"] if item["path"] == "runtime/ruleset.yaml")
    if corruption == "root":
        record["content_sha256"] = "0" * 64
        message = "content-root"
    else:
        if corruption == "hash":
            runtime["sha256"] = "0" * 64
        elif corruption == "size":
            runtime["bytes"] += 1
        elif corruption == "missing":
            record["files"].remove(runtime)
        else:
            record["dependencies"][0]["content_sha256"] = "0" * 64
        record["content_sha256"] = content_root_for_record(record)
        message = "dependency hash" if corruption == "dependency" else "payload descriptor"
    monkeypatch.setattr(
        bundled, "_srd_source_drafts", lambda: (replace(rules, record=record), reference)
    )
    with pytest.raises(ValueError, match=message):
        bundled.bundled_srd_archive()


@pytest.mark.parametrize("corruption", ["root", "hash", "size"])
def test_actual_archive_admission_rejects_one_corrupted_claim(corruption):
    raw = bundled.bundled_srd_archive()
    output = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(raw)) as source,
        zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as target,
    ):
        document = json.loads(source.read("manifest.json"))
        record = next(item for item in document["packages"] if item["role"] == "primary")
        if corruption == "root":
            record["content_sha256"] = "0" * 64
            message = "content-root"
        else:
            runtime = next(
                item for item in record["files"] if item["path"] == "runtime/ruleset.yaml"
            )
            if corruption == "hash":
                runtime["sha256"] = "0" * 64
            else:
                runtime["bytes"] += 1
            record["content_sha256"] = content_root_for_record(record)
            message = "Indexed package file"
        for member in source.infolist():
            target.writestr(
                member,
                canonical_json_bytes(document)
                if member.filename == "manifest.json"
                else source.read(member.filename),
            )
    output.seek(0)
    with (
        pytest.raises(CommandRejected, match="Package archive admission failed") as rejected,
        admit_package_archive(output),
    ):
        pass
    assert isinstance(rejected.value.__cause__, ValueError)
    assert message in str(rejected.value.__cause__)
