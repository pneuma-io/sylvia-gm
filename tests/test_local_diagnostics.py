"""Content exclusion, bounded storage and real interruption at the diagnostic owner."""

from __future__ import annotations

import errno
import json
import subprocess
import sys
import threading
import time

import pytest

from sylvia_gm.interfaces import _local_diagnostics as local


def _wait_until(predicate, seconds=10):
    deadline = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < deadline, "diagnostic fixture did not reach its checkpoint"
        time.sleep(0.01)


def _rows(diagnostics):
    return json.loads(
        diagnostics.preview(kind="app_problem", reproduction="", feedback="")["content"]
    )["diagnostics"]


def test_allowlist_excludes_content_nested_objects_paths_and_fake_error_names(tmp_path, capsys):
    private = [
        r"C:\Users\PrivatePlayer\secret.txt",
        "Bearer TOPSECRET",
        "sk-private-key",
        "private_story_input",
        "private.person@example.test",
        "MyCharacterName",
    ]
    diagnostics = local.LocalDiagnostics(tmp_path)
    try:
        for injection in [*private, {"nested": {"prompt": private, "headers": private}}]:
            diagnostics.record(
                injection,
                operation=injection,
                outcome=injection,
                error_code=injection,
                error_type=injection,
                duration_ms=injection,
            )
        diagnostics.record(
            "command_completed",
            operation="execute_action",
            outcome="error",
            error_code="command_rejected",
            error_type="CommandRejected",
            duration_ms=12,
        )
        rows = _rows(diagnostics)
        assert rows[-1]["operation"] == "execute_action"
        assert rows[-1]["severity"] == "error"
        assert rows[-1]["count"] == 1
        assert rows[-1]["duration_ms"] == 12
        unknown = next(row for row in rows if row["event"] == "request_rejected")
        assert unknown["operation"] == "other_operation"
        assert unknown["error_type"] == "OtherError"
        assert unknown["count"] == len(private) + 1
        assert all(set(row) == set(rows[-1]) for row in rows)
    finally:
        diagnostics.close()
    rendered = diagnostics.path.read_text() + capsys.readouterr().err + json.dumps(rows)
    for value in private:
        assert value not in rendered
        assert json.dumps(value)[1:-1] not in rendered


def test_report_is_exact_frozen_snapshot_and_cancel_invalidates_it(tmp_path):
    diagnostics = local.LocalDiagnostics(tmp_path)
    try:
        before = diagnostics.preview(
            kind="model_feedback",
            reproduction="Click <Continue> ☀",
            feedback="The GM ignored my choice.",
        )
        content = json.loads(before["content"])
        assert content["report_kind"] == "model_feedback"
        assert content["player_entered"]["reproduction_steps"] == "Click <Continue> ☀"
        assert set(content) == {
            "format",
            "application",
            "retention",
            "storage_status",
            "report_kind",
            "player_entered",
            "diagnostics",
        }
        diagnostics.record("command_completed", operation="execute_action")
        target = tmp_path / "selected.json"
        saved = diagnostics.save(before["preview_id"], target)
        assert target.read_bytes() == before["content"].encode("utf-8")
        assert saved["bytes"] == before["bytes"] == target.stat().st_size
        after = diagnostics.preview(kind="app_problem", reproduction="", feedback="")
        diagnostics.cancel(after["preview_id"])
        with pytest.raises(ValueError, match="Preview this report"):
            diagnostics.save(after["preview_id"], tmp_path / "cancelled.json")
        assert not (tmp_path / "cancelled.json").exists()
        with pytest.raises(ValueError, match="4,000"):
            diagnostics.preview(kind="model_feedback", reproduction="x" * 4001, feedback="")
    finally:
        diagnostics.close()


def test_client_faults_accept_only_fixed_categories_without_error_payload(tmp_path):
    diagnostics = local.LocalDiagnostics(tmp_path, clock=lambda: 1_800_000_000.0)
    try:
        fault = diagnostics.client_fault("renderer_rejection", 4000)
        diagnostics.client_fault("renderer_rejection", 6000)
        row = next(row for row in _rows(diagnostics) if row["id"] == fault["diagnostic_id"])
        assert row["event"] == "renderer_rejection"
        assert row["operation"] == "renderer"
        assert row["severity"] == "error"
        assert row["count"] == 10000
        for invalid in (
            True,
            False,
            None,
            "1",
            1.0,
            0,
            -1,
            65536,
            10**100,
            {"input": "PRIVATE"},
            [1],
        ):
            with pytest.raises(ValueError, match="integer from 1 to 65535"):
                diagnostics.client_fault("renderer_rejection", invalid)
        assert (
            sum(row["count"] for row in _rows(diagnostics) if row["event"] == "renderer_rejection")
            == 10000
        )
        maximum = diagnostics.client_fault("renderer_error", 65535)
        assert (
            next(row for row in _rows(diagnostics) if row["id"] == maximum["diagnostic_id"])[
                "count"
            ]
            == 65535
        )
        with pytest.raises(ValueError, match="Unknown app failure category"):
            diagnostics.client_fault({"message": "PRIVATE", "stack": "PRIVATE"}, 1)
        assert "PRIVATE" not in json.dumps(_rows(diagnostics))
    finally:
        diagnostics.close()


def test_failed_save_keeps_preview_and_omits_os_error_text(tmp_path, monkeypatch):
    diagnostics = local.LocalDiagnostics(tmp_path)
    try:
        preview = diagnostics.preview(kind="app_problem", reproduction="Reproduction", feedback="")
        original = local.os.replace
        target = tmp_path / "selected.json"
        target.write_bytes(b"previous report")

        def fail_selected(source, destination):
            if destination.name == "selected.json":
                raise OSError(errno.ENOSPC, "PRIVATE-CREDENTIAL C:\\Private\\name")
            return original(source, destination)

        with monkeypatch.context() as context:
            context.setattr(local.os, "replace", fail_selected)
            with pytest.raises(ValueError, match="Choose a writable local file") as failure:
                diagnostics.save(preview["preview_id"], tmp_path / "selected.json")
            assert "PRIVATE" not in str(failure.value)
            assert target.read_bytes() == b"previous report"
        diagnostics.save(preview["preview_id"], tmp_path / "selected.json")
        assert (tmp_path / "selected.json").read_bytes() == preview["content"].encode()
    finally:
        diagnostics.close()


def test_repeat_counts_ranges_rotation_expiry_and_owned_files_only(tmp_path, monkeypatch):
    now = [1_800_000_000.0]
    peak_bytes = [0]
    replace = local.os.replace

    def measure_replace(source, target):
        if source.name == "runtime.pending.jsonl":
            size = sum(path.stat().st_size for path in source.parent.iterdir())
            peak_bytes[0] = max(peak_bytes[0], size)
            assert size <= local.MAX_STORAGE_BYTES
        return replace(source, target)

    monkeypatch.setattr(local.os, "replace", measure_replace)
    unrelated = tmp_path / "campaigns" / "kept.txt"
    unrelated.parent.mkdir()
    unrelated.write_text("synthetic saved campaign")
    audit = tmp_path / "deliberate-audit-export.json"
    audit.write_text("synthetic deliberate trace")
    diagnostics = local.LocalDiagnostics(tmp_path, clock=lambda: now[0])
    try:
        first_id = diagnostics.record(
            "command_completed",
            operation="execute_action",
            outcome="error",
            error_code="command_rejected",
            error_type="CommandRejected",
        )
        now[0] += 10
        for _ in range(20_000):
            diagnostics.record(
                "command_completed",
                operation="execute_action",
                outcome="error",
                error_code="command_rejected",
                error_type="CommandRejected",
            )
        repeated = next(row for row in _rows(diagnostics) if row["id"] == first_id)
        assert repeated["count"] == 20_001
        assert repeated["last_seen"] - repeated["first_seen"] == 10
        for _ in range(2000):
            now[0] += 60
            diagnostics.record(
                "command_completed",
                operation="execute_player_action",
                outcome="error",
                error_code="internal_error",
            )
        rows = _rows(diagnostics)
        assert len(rows) == local.MAX_RECORDS
        assert all(row["count"] == 1 for row in rows)
        _wait_until(lambda: diagnostics._persisted == diagnostics._generation)
        print(f"rotation: {len(rows)} records, {diagnostics.path.stat().st_size} bytes")
        assert diagnostics.path.stat().st_size <= local.MAX_SNAPSHOT_BYTES
        assert not diagnostics.pending_path.exists()
        diagnostics.record(
            "command_completed",
            operation="execute_player_action",
            outcome="error",
            error_code="internal_error",
        )
        _wait_until(lambda: diagnostics._persisted == diagnostics._generation)
        now[0] += local.MAX_AGE_SECONDS + 1
        assert _rows(diagnostics) == []
        diagnostics.record("command_completed", operation="campaign_index")
    finally:
        diagnostics.close()
    assert unrelated.read_text() == "synthetic saved campaign"
    assert audit.read_text() == "synthetic deliberate trace"
    assert len(diagnostics.path.read_text().splitlines()) == 2
    print(f"peak storage across atomic replacement: {peak_bytes[0]} bytes")


def test_disk_full_and_stalled_writer_never_block_capture(tmp_path, monkeypatch, capsys):
    started = threading.Event()
    release = threading.Event()
    attempts = []

    def stall_then_fail(self, rows):
        attempts.append(1)
        started.set()
        release.wait()
        raise OSError(errno.ENOSPC, "PRIVATE disk path and secret")

    monkeypatch.setattr(local.LocalDiagnostics, "_write_snapshot", stall_then_fail)
    diagnostics = local.LocalDiagnostics(tmp_path)
    assert started.wait(5)
    try:
        start = time.perf_counter()
        for _ in range(10_000):
            diagnostics.record(
                "command_completed",
                operation="execute_action",
                outcome="error",
                error_code="internal_error",
            )
        elapsed = time.perf_counter() - start
        print(f"10,000 captures with stalled disk: {elapsed:.3f}s")
        assert elapsed < 5, "capture must remain independent of the deliberately blocked writer"
        assert sum(row["count"] for row in _rows(diagnostics)) == 10_001
        assert len(attempts) == 1
        release.set()
        _wait_until(lambda: diagnostics._storage == "unavailable")
        report = json.loads(
            diagnostics.preview(kind="app_problem", reproduction="", feedback="")["content"]
        )
        assert report["storage_status"] == "unavailable"
        assert "PRIVATE" not in json.dumps(report)
        assert not diagnostics.path.exists()
        captured = capsys.readouterr()
        assert captured.err == ""
        print(captured.out, end="")
    finally:
        release.set()
        diagnostics.close()


def test_interrupt_during_atomic_replace_keeps_committed_snapshot_and_recovers(tmp_path):
    initial = local.LocalDiagnostics(tmp_path)
    initial.record("command_completed", operation="campaign_index")
    initial.close()
    before = initial.path.read_bytes()
    # The child really exits between flush/fsync and os.replace, without close/finally.
    script = """
import os, sys, threading
from pathlib import Path
from sylvia_gm.interfaces import _local_diagnostics as local
written = threading.Event()
def interrupted_replace(source, target):
    written.set()
    os._exit(37)
local.os.replace = interrupted_replace
diagnostics = local.LocalDiagnostics(Path(sys.argv[1]))
written.wait(10)
raise RuntimeError('checkpoint did not execute')
"""
    child = subprocess.run([sys.executable, "-c", script, str(tmp_path)], check=False)
    assert child.returncode == 37
    assert initial.path.read_bytes() == before
    assert initial.pending_path.stat().st_size <= local.MAX_SNAPSHOT_BYTES
    assert (
        sum(path.stat().st_size for path in initial.path.parent.iterdir())
        <= local.MAX_STORAGE_BYTES
    )
    recovered = local.LocalDiagnostics(tmp_path)
    try:
        assert any(row["operation"] == "campaign_index" for row in _rows(recovered))
    finally:
        recovered.close()
    assert not recovered.pending_path.exists()
    for line in recovered.path.read_text().splitlines():
        json.loads(line)


def test_abrupt_process_death_is_categorized_on_restart(tmp_path):
    script = """
import os, sys, time
from pathlib import Path
from sylvia_gm.interfaces._local_diagnostics import LocalDiagnostics
diagnostics = LocalDiagnostics(Path(sys.argv[1]))
while diagnostics._persisted < 1:
    time.sleep(.01)
os._exit(38)
"""
    child = subprocess.run([sys.executable, "-c", script, str(tmp_path)], check=False)
    assert child.returncode == 38
    recovered = local.LocalDiagnostics(tmp_path)
    try:
        interrupted = [row for row in _rows(recovered) if row["event"] == "owner_interrupted"]
        assert len(interrupted) == 1
        assert interrupted[0]["severity"] == "warning"
    finally:
        recovered.close()


def test_writer_start_failure_keeps_bounded_memory_diagnostics_available(tmp_path, monkeypatch):
    def fail_start(self):
        raise RuntimeError("PRIVATE thread failure")

    monkeypatch.setattr(threading.Thread, "start", fail_start)
    diagnostics = local.LocalDiagnostics(tmp_path)
    try:
        diagnostics.record("command_completed", operation="campaign_index")
        report = diagnostics.preview(kind="app_problem", reproduction="", feedback="")
        assert json.loads(report["content"])["storage_status"] == "unavailable"
        assert "PRIVATE" not in report["content"]
        assert len(_rows(diagnostics)) == 2
    finally:
        diagnostics.close()


def test_legacy_oversize_partial_and_unapproved_fields_never_enter_reports(tmp_path):
    folder = tmp_path / "diagnostics"
    folder.mkdir()
    path = folder / "runtime.jsonl"
    path.write_text(
        '{"event":"owner_started","prompt":"PRIVATE"}\n' + "x" * local.MAX_SNAPSHOT_BYTES
    )
    (folder / "runtime.previous.jsonl").write_text("PRIVATE legacy backup")
    diagnostics = local.LocalDiagnostics(tmp_path)
    diagnostics.close()
    assert path.stat().st_size <= local.MAX_SNAPSHOT_BYTES
    assert not (folder / "runtime.previous.jsonl").exists()
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[0].update(prompt="PRIVATE", nested={"token": "PRIVATE"}, error_type="PRIVATE")
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + '\n{"partial":\n' + "[" * 2000 + "]" * 2000
    )
    recovered = local.LocalDiagnostics(tmp_path)
    try:
        assert "PRIVATE" not in json.dumps(_rows(recovered))
    finally:
        recovered.close()
    assert "PRIVATE" not in path.read_text()
