"""Successful-build cleanup cannot consume another invocation or retained evidence."""

from dataclasses import replace

import pytest

from sylvia_gm.build_workspace import BuildWorkspace


def test_success_removes_only_own_disposable_work_and_keeps_receipt(tmp_path):
    parent = tmp_path / "work"
    first, live = BuildWorkspace.create(parent), BuildWorkspace.create(parent)
    (first.path / "scratch").write_text("disposable")
    (live.path / "evidence").write_text("live")
    output = tmp_path / "owner.exe"
    output.write_bytes(b"synthetic final owner")
    receipt = first.finish(output)
    assert receipt["cleanup"] == "completed"
    assert not first.path.exists()
    assert (live.path / "evidence").read_text() == "live"
    assert (parent / (first.path.name + ".receipt.json")).is_file()
    assert output.read_bytes() == b"synthetic final owner"


def test_retained_failed_or_explicit_evidence_is_not_swept(tmp_path):
    failed = BuildWorkspace.create(tmp_path / "work")
    retained = BuildWorkspace.create(tmp_path / "work")
    output = tmp_path / "owner.exe"
    output.write_bytes(b"synthetic")
    assert retained.finish(output, keep=True)["cleanup"] == "retained"
    assert failed.path.is_dir() and retained.path.is_dir()


def test_foreign_target_and_wrong_owner_are_refused_without_deletion(tmp_path):
    first, second = (BuildWorkspace.create(tmp_path / "work") for _ in range(2))
    output = tmp_path / "owner.exe"
    output.write_bytes(b"synthetic")
    with pytest.raises(ValueError, match="ownership"):
        replace(first, path=second.path).finish(output)
    with pytest.raises(ValueError, match="direct workspace"):
        replace(first, path=tmp_path).finish(output)
    assert first.path.is_dir() and second.path.is_dir() and output.is_file()
