import json

import pytest
from scripts import journey_progress


def test_timing_keeps_original_result_exception_and_records_real_phase(tmp_path, monkeypatch):
    target = tmp_path / "fixture-timings.jsonl"
    monkeypatch.setenv("SYLVIA_JOURNEY_FIXTURE_TIMINGS", str(target))

    @journey_progress.timed("prepare")
    def success():
        return {"exact": 7}

    assert success() == {"exact": 7}
    with pytest.raises(ValueError, match="preserved"), journey_progress.phase("failed prepare"):
        raise ValueError("preserved")
    rows = [json.loads(line) for line in target.read_text().splitlines()]
    assert [row["phase"] for row in rows] == ["prepare", "failed prepare"]
    assert all(row["seconds"] >= 0 for row in rows)


def test_optional_scenario_retention_does_not_change_or_remove_closed_database(
    tmp_path, monkeypatch
):
    from tests.lite_fixture_archive import archive_closed_database

    database = tmp_path / "closed.sqlite3"
    database.write_bytes(b"unchanged closed synthetic fixture")
    monkeypatch.setenv("SYLVIA_RETAIN_SUCCESSFUL_FIXTURES", "0")
    archive_closed_database(database, tmp_path)
    assert database.read_bytes() == b"unchanged closed synthetic fixture"
    assert not list(tmp_path.glob("*.parts.json"))


def test_explicit_fixture_archival_still_verifies_complete_restore(tmp_path, monkeypatch):
    from tests import lite_fixture_archive

    root = tmp_path / "owned"
    work = root / "attempts/case"
    work.mkdir(parents=True)
    monkeypatch.setattr(lite_fixture_archive, "ROOT", root)
    monkeypatch.setenv("SYLVIA_RETAIN_SUCCESSFUL_FIXTURES", "1")
    database = work / "closed.sqlite3"
    original = b"synthetic SQLite evidence\0" * 10000
    database.write_bytes(original)
    lite_fixture_archive.archive_closed_database(database, work)
    receipt = json.loads(database.with_suffix(".archive.json").read_text())
    assert receipt["full_restore_digest_verified"] is True
    assert receipt["original_bytes"] == len(original)
    assert not database.exists()
    import hashlib
    import zipfile

    manifest = json.loads(database.with_name(database.name + ".parts.json").read_text())
    restored = b""
    for block in manifest["blocks"]:
        with zipfile.ZipFile(block["archive"]) as archive:
            restored += archive.read(block["member"])
    assert restored == original
    assert receipt["original_sha256"] == hashlib.sha256(original).hexdigest()
