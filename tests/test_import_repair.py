"""Safely readable imports can be repaired without mutating their selected source."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from sylvia_gm.application import import_repair
from sylvia_gm.application.import_repair import (
    inspect_repair_source,
    materialize_repair_draft,
    stage_repair_copy,
)
from sylvia_gm.application.package_materialization import (
    NormalizedPackageDraft,
    materialize_package_archive,
)
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.infrastructure.package_archive import admit_package_archive
from tests.test_plan_bundles import portable_plan

_FIXTURE = Path(__file__).parent / "fixtures" / "rulesets" / "signal-and-static"


def _valid_package_archive() -> bytes:
    record = json.loads((_FIXTURE / "package-record.json").read_text(encoding="utf-8"))
    payloads = {
        path.relative_to(_FIXTURE).as_posix(): path.read_bytes()
        for path in _FIXTURE.rglob("*")
        if path.is_file() and path.name != "package-record.json"
    }
    with materialize_package_archive((NormalizedPackageDraft(record, payloads),)) as built:
        return built.archive.read()


def _rewrite_zip(source: bytes, changes: dict[str, bytes | None]) -> bytes:
    result = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(source)) as original, zipfile.ZipFile(result, "w") as output:
        existing = {item.filename: original.read(item) for item in original.infolist()}
        existing.update(changes)
        for name, value in existing.items():
            if value is None:
                continue
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            output.writestr(info, value)
    return result.getvalue()


def test_package_repair_stages_once_and_materializes_through_full_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_checks = 0
    validate_runtimes = import_repair.validate_admitted_package_runtimes

    def counted_runtime_check(admitted):
        nonlocal runtime_checks
        runtime_checks += 1
        return validate_runtimes(admitted)

    monkeypatch.setattr(import_repair, "validate_admitted_package_runtimes", counted_runtime_check)
    original = _valid_package_archive()
    with zipfile.ZipFile(io.BytesIO(original)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    manifest["unexpected"] = "leave for Builder"
    manifest["packages"][0]["unexpected"] = {"kept": True}
    broken = _rewrite_zip(
        original,
        {"manifest.json": json.dumps(manifest, separators=(",", ":")).encode()},
    )
    source = tmp_path / "broken.sylvia-gm-package"
    source.write_bytes(broken)
    source_hash = hashlib.sha256(broken).hexdigest()

    inspected = inspect_repair_source(source)
    assert inspected.kind == "package"
    assert inspected.source_sha256 == source_hash
    assert inspected.diagnostics

    workspace = tmp_path / "workspace"
    staged = stage_repair_copy(
        source,
        source_hash,
        workspace,
        expected_kind="package",
    )
    manifest_path = staged.draft_root / "manifest.json"
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["packages"][0]["unexpected"] == {
        "kept": True
    }
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["unexpected"] == (
        "leave for Builder"
    )
    with pytest.raises(CommandRejected, match="manifest fields") as rejected:
        materialize_repair_draft(workspace, expected_kind="package")
    assert rejected.value.diagnostics[0].path == "$.unexpected"

    repaired_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    repaired_manifest.pop("unexpected")
    repaired_manifest["packages"][0].pop("unexpected")
    manifest_path.write_text(json.dumps(repaired_manifest), encoding="utf-8", newline="\n")
    edited = manifest_path.read_bytes()
    repeated = stage_repair_copy(
        source,
        source_hash,
        workspace,
        expected_kind="package",
        bound_source_sha256=source_hash,
    )
    assert repeated.draft_root == staged.draft_root
    assert manifest_path.read_bytes() == edited

    with materialize_repair_draft(workspace, expected_kind="package") as materialized:
        assert materialized.kind == "package"
        assert materialized.plan is None
        assert materialized.interview_contribution is None
        with admit_package_archive(materialized.archive) as admitted:
            assert admitted.package_lock.entries[0].package_id == (
                "sylvia-gm.conformance.signal-static"
            )
            assert materialized.closure_sha256
    assert runtime_checks == 1


def test_repair_stage_rejects_source_changes_and_a_different_binding(tmp_path: Path) -> None:
    source = tmp_path / "broken.sylvia-gm-package"
    source.write_bytes(_rewrite_zip(_valid_package_archive(), {"manifest.json": b"{}"}))
    expected_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    workspace = tmp_path / "workspace"
    stage_repair_copy(source, expected_hash, workspace, expected_kind="package")

    with pytest.raises(ValueError, match="bound source"):
        stage_repair_copy(
            source,
            expected_hash,
            workspace,
            expected_kind="package",
            bound_source_sha256="0" * 64,
        )
    source.write_bytes(source.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="changed"):
        stage_repair_copy(
            source,
            expected_hash,
            workspace,
            expected_kind="package",
            bound_source_sha256=expected_hash,
        )


@pytest.mark.parametrize("member_name", [
    "../outside.txt", "notes.md:payload", "nested/NUL.txt", "COM1/notes.md",
    "notes.md.", "bad|name.md", "nested/bad\nname.md",
])
def test_repair_inspection_rejects_unsafe_archive_members(
    tmp_path: Path, member_name: str,
) -> None:
    source = tmp_path / "unsafe.sylvia-gm-package"
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("manifest.json", b"{}")
        archive.writestr(member_name, b"no")
    source.write_bytes(output.getvalue())

    with pytest.raises(ValueError, match="unsafe|portable"):
        inspect_repair_source(source)
    workspace = tmp_path / "workspace"
    with pytest.raises(ValueError, match="unsafe|portable"):
        stage_repair_copy(
            source, hashlib.sha256(source.read_bytes()).hexdigest(), workspace,
            expected_kind="package",
        )
    assert not workspace.exists()
    assert not (tmp_path / "outside.txt").exists()


def test_plan_repair_unfolds_snapshot_and_preserves_identity_and_setup(tmp_path: Path) -> None:
    original_plan, _, valid = portable_plan(tmp_path / "source-library")
    with zipfile.ZipFile(io.BytesIO(valid)) as archive:
        record = json.loads(archive.read("record.json"))
        payload = json.loads(archive.read("plan.json"))
    original_setup_id = record["setup_id"]
    record["unknown_import_field"] = "leave for Builder"
    payload["bindings"]["package_closure_sha256"] = "0" * 64
    broken = _rewrite_zip(
        valid,
        {
            "record.json": json.dumps(record, separators=(",", ":")).encode(),
            "plan.json": json.dumps(payload, separators=(",", ":")).encode(),
        },
    )
    source = tmp_path / "broken.SYLVIA-GM-PLAN"
    source.write_bytes(broken)
    source_hash = hashlib.sha256(broken).hexdigest()

    inspected = inspect_repair_source(source)
    assert inspected.kind == "plan"
    assert inspected.identity == original_plan.artifact_id
    staged = stage_repair_copy(source, source_hash, tmp_path / "workspace", expected_kind="plan")
    assert not (staged.draft_root / "setting.sylvia-gm-package").exists()
    assert (staged.draft_root / "snapshot" / "manifest.json").is_file()
    staged_record_path = staged.draft_root / "record.json"
    staged_record = json.loads(staged_record_path.read_text(encoding="utf-8"))
    assert staged_record["unknown_import_field"] == "leave for Builder"
    assert staged_record["artifact_id"] == original_plan.artifact_id
    assert staged_record["setup_id"] == original_setup_id
    with pytest.raises(ValueError, match="record"):
        materialize_repair_draft(tmp_path / "workspace", expected_kind="plan")

    staged_record.pop("unknown_import_field")
    staged_record_path.write_text(json.dumps(staged_record), encoding="utf-8", newline="\n")
    with materialize_repair_draft(tmp_path / "workspace", expected_kind="plan") as materialized:
        assert materialized.kind == "plan"
        assert materialized.plan is not None
        assert materialized.setup_id == original_setup_id
        assert materialized.plan.payload["bindings"]["package_closure_sha256"] == (
            materialized.closure_sha256
        )
        with admit_package_archive(materialized.archive) as admitted:
            assert admitted.package_lock
    assert source.read_bytes() == broken


def test_nested_plan_snapshot_safety_failure_is_not_repairable(tmp_path: Path) -> None:
    _, _, valid = portable_plan(tmp_path / "nested-source-library")
    with zipfile.ZipFile(io.BytesIO(valid)) as archive:
        snapshot = archive.read("setting.sylvia-gm-package")
    unsafe_snapshot = _rewrite_zip(snapshot, {"../outside.txt": b"no"})
    broken = _rewrite_zip(valid, {"setting.sylvia-gm-package": unsafe_snapshot})
    source = tmp_path / "unsafe.sylvia-gm-plan"
    source.write_bytes(broken)

    with pytest.raises(ValueError, match="unsafe|portable"):
        inspect_repair_source(source)
    assert not (tmp_path / "outside.txt").exists()
