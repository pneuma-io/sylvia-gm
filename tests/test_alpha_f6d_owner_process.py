"""Executable F6D owner-process integration smoke.

This owns only the transport boundary: a real child process proves launch
possession, routes commands through the existing authority owner, emits JSON
projections, and preserves SQLite authority across a clean restart.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any, TextIO

import pytest

from sylvia_gm.domain.package_library import canonical_json_bytes, content_root_for_record
from sylvia_gm.infrastructure.launch_handshake import LaunchHandshake
from sylvia_gm.interfaces.owner_process import _write_message


def test_owner_process_wire_messages_are_ascii_safe() -> None:
    stream = io.StringIO()

    _write_message(stream, {"text": "Rift—ready"})

    encoded = stream.getvalue()
    assert encoded.isascii()
    assert json.loads(encoded) == {"text": "Rift—ready"}


def test_chatgpt_connection_preserves_safe_rejection_message(tmp_path: Path) -> None:
    process, input_stream, output_stream = _launch(tmp_path / "authority.sqlite3")
    try:
        _write(
            input_stream,
            {
                "id": "subscription-rejection",
                "method": "chatgpt_connection",
                "params": {"action": "unavailable-action"},
            },
        )
        reply = _read(output_stream)
        assert reply["ok"] is False
        assert reply["error"]["code"] == "command_rejected"
        assert reply["error"]["message"] == "This ChatGPT connection action is unavailable."
    finally:
        _shutdown(process, input_stream, output_stream)


def _executable_package_archive() -> bytes:
    runtime = b"""sylvia_gm_runtime:
  format: 1
  state_namespace: synthetic.desktop
features: {}
abilities:
  measure:
    name: Measure
    default_min: 1
    default_max: 20
    generation: 1d20
    modifier_formula: score
    used_for: []
tools:
  advance:
    description: Advance durable desktop state.
    parameters:
      - name: steps
        type: integer
        minimum: 1
        required: true
    effects:
      - increment:
          field: state.counter
          amount: steps
procedures: {}
definitions: {}
narrative: {}
"""
    tool = {
        "name": "advance",
        "capability": "desktop.counter",
        "input_schema": {
            "type": "object",
            "properties": {"steps": {"type": "integer", "minimum": 1}},
            "required": ["steps"],
            "additionalProperties": False,
        },
        "state_predicates": [],
        "turn_statuses": ["open"],
        "writers": ["dm"],
    }
    file_record = {
        "path": "runtime/ruleset.yaml",
        "bytes": len(runtime),
        "sha256": hashlib.sha256(runtime).hexdigest(),
    }
    draft: dict[str, Any] = {
        "id": "synthetic.desktop",
        "version": "1.0.0",
        "role": "primary",
        "metadata": {"label": "Synthetic Desktop"},
        "provenance": {
            "publisher": "sylvia-gm-tests",
            "source_sha256": hashlib.sha256(runtime).hexdigest(),
        },
        "compatibility": {"host_contract": 1, "runtime_format": 1, "requires": []},
        "minimum_model_capability_tier": "sylvia.capability.t00",
        "dependencies": [],
        "capabilities": ["desktop.counter"],
        "tools": [tool],
        "prompt_fragments": [],
        "ui_surfaces": [],
        "files": [file_record],
    }
    record = {**draft, "content_sha256": content_root_for_record(draft)}
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            canonical_json_bytes({"format": 1, "packages": [record]}),
        )
        archive.writestr("packages/synthetic.desktop/runtime/ruleset.yaml", runtime)
    return stream.getvalue()


def _write(stream: TextIO, payload: dict[str, Any]) -> None:
    stream.write(json.dumps(payload, separators=(",", ":")) + "\n")
    stream.flush()


def _read(stream: TextIO) -> dict[str, Any]:
    line = stream.readline()
    assert line, "owner process exited before replying"
    decoded = json.loads(line)
    assert isinstance(decoded, dict)
    return decoded


def _launch(database: Path) -> tuple[subprocess.Popen[str], TextIO, TextIO]:
    process = subprocess.Popen(
        [sys.executable, "-m", "sylvia_gm.interfaces.owner_process"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )
    assert process.stdin is not None
    assert process.stdout is not None

    handshake = LaunchHandshake()
    credential = handshake.issue()
    _write(
        process.stdin,
        {
            "type": "launch",
            "secret_hex": credential.secret_hex,
            "database_path": str(database),
            "owner_id": "owner-sidecar-test",
        },
    )
    ready_line = process.stdout.readline()
    if not ready_line:
        stderr_text = process.stderr.read() if process.stderr is not None else ""
        diagnostic = stderr_text.replace(credential.secret_hex, "[REDACTED]")
        raise AssertionError(f"owner process exited before ready: {diagnostic}")
    ready = json.loads(ready_line)
    assert isinstance(ready, dict)
    assert ready["ok"] is True
    assert ready["type"] == "ready"
    assert credential.secret_hex not in json.dumps(ready)
    handshake.verify(ready["response_hex"])
    return process, process.stdin, process.stdout


def _request(
    input_stream: TextIO,
    output_stream: TextIO,
    request_id: str,
    method: str,
    params: dict[str, Any] | None = None,
) -> Any:
    _write(
        input_stream,
        {"id": request_id, "method": method, "params": params or {}},
    )
    reply = _read(output_stream)
    assert reply["id"] == request_id
    assert reply["ok"] is True, reply
    return reply["result"]


def _construct_campaign(
    input_stream: TextIO,
    output_stream: TextIO,
    database: Path,
    *,
    slug: str,
    display_name: str,
    candidate_id: str | None = None,
) -> tuple[str, str, int]:
    confirmation = _request(
        input_stream,
        output_stream,
        f"confirm-{slug}",
        "confirm_new_campaign",
        {
            "operation_id": f"confirm-{slug}",
            "display_name": display_name,
            "candidate_id": candidate_id,
            "turn_id": f"turn-builder-{slug}",
            "provider_id": "test.builder",
            "model_id": "test.builder/model0",
        },
    )
    campaign_id = confirmation["campaign_id"]
    _request(
        input_stream,
        output_stream,
        f"begin-{slug}",
        "begin_campaign_construction",
        {
            "operation_id": f"begin-{slug}",
            "campaign_id": campaign_id,
            "lane": "quick_start",
            "expected_revision": 0,
            "canonical_input": {"premise": display_name},
        },
    )
    plan_path = database.parent / "campaigns" / campaign_id / "campaign-plan.md"
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text(f"# {display_name}\n", encoding="utf-8")
    finalized = _request(
        input_stream,
        output_stream,
        f"finalize-{slug}",
        "finalize_campaign_construction",
        {
            "campaign_id": campaign_id,
            "dm_provider_id": "test.dm",
            "dm_model_id": "test.dm/model0",
            "model_guidance_override": True,
        },
    )
    return (
        campaign_id,
        finalized["dm_turn_id"],
        finalized["handoff"]["resulting_campaign_revision"],
    )


def _shutdown(process: subprocess.Popen[str], input_stream: TextIO, output_stream: TextIO) -> None:
    if process.poll() is not None:
        error = process.stderr.read() if process.stderr is not None else ""
        raise AssertionError(f"owner process exited unexpectedly: {error}")
    try:
        _request(input_stream, output_stream, "shutdown", "shutdown")
    except (AssertionError, OSError) as shutdown_error:
        process.wait(timeout=10)
        error = process.stderr.read() if process.stderr is not None else ""
        raise AssertionError(f"owner process transport failed: {error}") from shutdown_error
    process.wait(timeout=10)
    assert process.returncode == 0


def test_owner_process_reads_utf8_desktop_commands_under_windows_encoding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTHONIOENCODING", "cp1252")
    database = tmp_path / "authority.sqlite3"
    name = "Renée — 雪 🌱"
    process, input_stream, output_stream = _launch(database)
    try:
        # Rust's desktop bridge writes literal UTF-8 JSON, without ASCII escaping.
        input_stream.write(
            json.dumps(
                {
                    "id": "unicode-chat",
                    "method": "create_builder_chat",
                    "params": {
                        "operation_id": "unicode-chat",
                        "display_name": name,
                        "provider_id": "test.builder",
                        "model_id": "test.builder/model0",
                    },
                },
                ensure_ascii=False,
            )
            + "\n"
        )
        input_stream.flush()
        reply = _read(output_stream)
        assert reply["ok"] is True, reply
        assert reply["result"]["display_name"] == name
    finally:
        _shutdown(process, input_stream, output_stream)

    process, input_stream, output_stream = _launch(database)
    try:
        chats = _request(input_stream, output_stream, "saved-chats", "builder_chat_index")
        assert [chat["display_name"] for chat in chats] == [name]
    finally:
        _shutdown(process, input_stream, output_stream)


def test_owner_process_writes_content_free_runtime_diagnostics(tmp_path: Path) -> None:
    database = tmp_path / "authority.sqlite3"
    private_sentinel = "PRIVATE-CAMPAIGN-CONTENT-MUST-NOT-BE-LOGGED"

    process, input_stream, output_stream = _launch(database)
    try:
        assert (
            _request(
                input_stream,
                output_stream,
                "diagnostic-index",
                "campaign_index",
                {"unused_private_text": private_sentinel},
            )
            == []
        )
        _write(
            input_stream,
            {
                "id": "diagnostic-rejection",
                "method": "unsupported_operation",
                "params": {"private_text": private_sentinel},
            },
        )
        rejected = _read(output_stream)
        assert rejected["ok"] is False
    finally:
        _shutdown(process, input_stream, output_stream)

    diagnostic_path = tmp_path / "diagnostics" / "runtime.jsonl"
    lines = diagnostic_path.read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines]
    assert records[0]["event"] == "owner_started"
    command = next(record for record in records if record["event"] == "command_completed")
    assert command["operation"] == "campaign_index"
    assert command["outcome"] == "ok"
    assert isinstance(command["duration_ms"], float)
    rejected_command = next(
        record for record in records
        if record["event"] == "command_completed" and record["operation"] == "other_operation"
    )
    assert rejected_command["outcome"] == "error"
    assert rejected_command["error_code"] == "command_rejected"
    assert rejected_command["error_type"] == "OwnerProtocolError"
    assert records[-1]["event"] == "owner_stopped"
    rendered = json.dumps(records)
    assert private_sentinel not in rendered
    assert str(database) not in rendered


def test_owner_process_handshake_command_restart_smoke(tmp_path: Path) -> None:
    database = tmp_path / "authority.sqlite3"

    process, input_stream, output_stream = _launch(database)
    try:
        assert _request(input_stream, output_stream, "index-empty", "campaign_index") == []
        campaign_id, _dm_turn_id, revision = _construct_campaign(
            input_stream,
            output_stream,
            database,
            slug="sidecar-smoke",
            display_name="Sidecar Smoke",
        )
        snapshot = _request(
            input_stream,
            output_stream,
            "snapshot",
            "snapshot",
            {"campaign_id": campaign_id},
        )
        assert snapshot["revision"] == revision == 4
        assert snapshot["package_lock"]["entries"] == []
        assert snapshot["state"] == {}
    finally:
        _shutdown(process, input_stream, output_stream)

    reopened, reopened_input, reopened_output = _launch(database)
    try:
        campaign_index = _request(
            reopened_input,
            reopened_output,
            "index-reopened",
            "campaign_index",
        )
        assert [entry["campaign_id"] for entry in campaign_index] == [campaign_id]
        assert campaign_index[0]["ruleset_summary"] == "No rules"
        assert campaign_index[0]["playable"] is True
        assert campaign_index[0]["provider_id"] == "test.dm"
        assert campaign_index[0]["model_id"] == "test.dm/model0"
        print(
            "F6D OWNER PROCESS",
            campaign_index[0]["campaign_id"],
            campaign_index[0]["ruleset_summary"],
            "restart=preserved",
        )
    finally:
        _shutdown(reopened, reopened_input, reopened_output)


def test_owner_process_installs_selected_package_file_without_returning_private_path(
    tmp_path: Path,
) -> None:
    database = tmp_path / "authority.sqlite3"
    selected = tmp_path / "selected package.sylvia-gm-package"
    selected.write_bytes(_executable_package_archive())

    process, input_stream, output_stream = _launch(database)
    try:
        result = _request(
            input_stream,
            output_stream,
            "install-selected-package",
            "install_package_file",
            {
                "install_id": "install-selected-package",
                "source_path": str(selected),
            },
        )
        assert result["package_lock"]["entries"][0]["package_id"] == "synthetic.desktop"
        assert str(tmp_path) not in json.dumps(result)
        print(
            "F6D SELECTED PACKAGE",
            selected.name,
            result["candidate_id"],
            "private-path=excluded",
        )
    finally:
        _shutdown(process, input_stream, output_stream)


def test_owner_process_browses_and_exports_installed_package_without_campaign_data(
    tmp_path: Path,
) -> None:
    database = tmp_path / "authority.sqlite3"
    archive_base64 = base64.b64encode(_executable_package_archive()).decode("ascii")

    process, input_stream, output_stream = _launch(database)
    try:
        candidate = _request(
            input_stream,
            output_stream,
            "inspect-install",
            "install_package",
            {"install_id": "inspect-install", "archive_base64": archive_base64},
        )
        candidate_id = candidate["candidate_id"]
        inspected = _request(
            input_stream,
            output_stream,
            "inspect-candidate",
            "inspect_package_candidate",
            {"candidate_id": candidate_id},
        )
        assert inspected["candidate_id"] == candidate_id
        assert inspected["packages"][0]["id"] == "synthetic.desktop"
        assert inspected["packages"][0]["public_actions"][0]["name"] == "advance"
        assert "campaign" not in json.dumps(inspected).casefold()

        preview = _request(
            input_stream,
            output_stream,
            "preview-runtime",
            "package_candidate_text",
            {
                "candidate_id": candidate_id,
                "package_id": "synthetic.desktop",
                "path": "runtime/ruleset.yaml",
            },
        )
        assert preview["content"].startswith("sylvia_gm_runtime:")

        comparison = _request(
            input_stream,
            output_stream,
            "compare-candidate",
            "compare_package_candidates",
            {"left_candidate_id": candidate_id, "right_candidate_id": candidate_id},
        )
        assert comparison["same_closure"] is True
        assert comparison["changes"] == []

        exported = _request(
            input_stream,
            output_stream,
            "export-candidate",
            "export_package_candidate_file",
            {"export_id": "inspect-export", "candidate_id": candidate_id},
        )
        artifact = Path(exported["artifact_path"])
        assert artifact.name == "inspect-export.sylvia-gm-package"
        assert artifact.is_file()
        assert exported["export"]["candidate_id"] == candidate_id
        assert (
            exported["export"]["archive_sha256"]
            == hashlib.sha256(artifact.read_bytes()).hexdigest()
        )
    finally:
        _shutdown(process, input_stream, output_stream)


def test_owner_process_executes_admitted_package_across_restart(tmp_path: Path) -> None:
    database = tmp_path / "authority.sqlite3"
    archive_base64 = base64.b64encode(_executable_package_archive()).decode("ascii")

    process, input_stream, output_stream = _launch(database)
    try:
        candidate = _request(
            input_stream,
            output_stream,
            "install-package",
            "install_package",
            {"install_id": "install-desktop", "archive_base64": archive_base64},
        )
        assert candidate["package_lock"]["entries"][0]["package_id"] == "synthetic.desktop"
        confirmation = _request(
            input_stream,
            output_stream,
            "confirm-builder-campaign",
            "confirm_new_campaign",
            {
                "operation_id": "confirm-builder-campaign-op",
                "display_name": "Builder Process Campaign",
                "candidate_id": candidate["candidate_id"],
                "turn_id": "builder-turn-one",
                "provider_id": "test.builder",
                "model_id": "test.builder/model0",
            },
        )
        builder_campaign_id = confirmation["campaign_id"]
        builder_session_id = confirmation["builder_session_id"]
        assert builder_session_id.startswith("session-builder-")
        assert confirmation["resulting_revision"] == 0
        begun = _request(
            input_stream,
            output_stream,
            "begin-builder-campaign",
            "begin_campaign_construction",
            {
                "operation_id": "begin-builder-campaign-op",
                "campaign_id": builder_campaign_id,
                "lane": "quick_start",
                "expected_revision": 0,
                "canonical_input": {
                    "setting": "A storm-bound observatory",
                    "premise": "Recover the missing star map",
                },
            },
        )
        assert begun["lane"] == "quick_start"
        assert begun["resulting_revision"] == 1
        plan_path = database.parent / "campaigns" / builder_campaign_id / "campaign-plan.md"
        plan_path.parent.mkdir(parents=True)
        plan_path.write_text("# Builder Process Campaign\n", encoding="utf-8")
        guidance = _request(
            input_stream,
            output_stream,
            "guidance-builder-campaign",
            "model_guidance",
            {
                "campaign_id": builder_campaign_id,
                "model_id": "test.dm/model0",
                "override_accepted": False,
            },
        )
        assert guidance["status"] == "unrated"
        assert guidance["minimum_tier_id"] == "sylvia.capability.t00"
        assert guidance["warning_required"] is True
        assert guidance["may_continue"] is False
        _write(
            input_stream,
            {
                "id": "finalize-builder-campaign-unconfirmed",
                "method": "finalize_campaign_construction",
                "params": {
                    "campaign_id": builder_campaign_id,
                    "dm_provider_id": "test.dm",
                    "dm_model_id": "test.dm/model0",
                    "model_guidance_override": False,
                },
            },
        )
        warning_reply = _read(output_stream)
        assert warning_reply["id"] == "finalize-builder-campaign-unconfirmed"
        assert warning_reply["ok"] is False
        assert warning_reply["error"]["code"] == "model_capability_unrated"
        unmodified = _request(
            input_stream,
            output_stream,
            "snapshot-builder-campaign-unconfirmed",
            "snapshot",
            {"campaign_id": builder_campaign_id},
        )
        assert unmodified["writer"] == "builder"
        assert unmodified["revision"] == 1
        finalized = _request(
            input_stream,
            output_stream,
            "finalize-builder-campaign",
            "finalize_campaign_construction",
            {
                "campaign_id": builder_campaign_id,
                "dm_provider_id": "test.dm",
                "dm_model_id": "test.dm/model0",
                "model_guidance_override": True,
            },
        )
        assert finalized["construction"]["lane"] == "quick_start"
        assert finalized["construction"]["resulting_revision"] == 2
        assert finalized["plan"]["content"] == {
            "campaign_plan_markdown": plan_path.read_bytes().decode("utf-8")
        }
        assert finalized["plan"]["plan_revision"] == 1
        assert finalized["plan"]["resulting_campaign_revision"] == 3
        assert finalized["handoff"]["builder_session_id"] == builder_session_id
        assert finalized["handoff"]["package_candidate_id"] == candidate["candidate_id"]
        assert finalized["handoff"]["model_guidance"] == {
            **{
                key: guidance[key]
                for key in (
                    "minimum_tier_id",
                    "model_id",
                    "placement_evidence_sha256",
                    "placement_tier_id",
                    "registry_version",
                    "status",
                    "warning_code",
                )
            },
            "override_accepted": True,
        }
        assert finalized["handoff"]["resulting_campaign_revision"] == 4
        builder_context = _request(
            input_stream,
            output_stream,
            "builder-context-after-handoff",
            "session_context",
            {"session_id": builder_session_id},
        )
        assert builder_context["session"]["status"] == "active"
        dm_session_id = finalized["handoff"]["dm_session_id"]
        dm_context = _request(
            input_stream,
            output_stream,
            "dm-context-after-handoff",
            "session_context",
            {"session_id": dm_session_id},
        )
        assert dm_context["session"]["status"] == "active"
        replay = _request(
            input_stream,
            output_stream,
            "finalize-builder-campaign-replay",
            "finalize_campaign_construction",
            {
                "campaign_id": builder_campaign_id,
                "dm_provider_id": "test.dm",
                "dm_model_id": "test.dm/model0",
            },
        )
        assert replay == finalized
        campaign_id = builder_campaign_id
        dm_turn_id = finalized["dm_turn_id"]
        dm_session_id = finalized["handoff"]["dm_session_id"]
        catalog = _request(
            input_stream,
            output_stream,
            "catalog",
            "catalog",
            {"campaign_id": campaign_id},
        )
        assert [tool["name"] for tool in catalog] == ["advance"]
        result = _request(
            input_stream,
            output_stream,
            "action-one",
            "execute_action",
            {
                "campaign_id": campaign_id,
                "action_id": "action-desktop-one",
                "idempotency_key": "idem-desktop-one",
                "writer": "dm",
                "expected_revision": 4,
                "turn_id": dm_turn_id,
                "package_id": "synthetic.desktop",
                "tool_name": "advance",
                "parameters": {"steps": 3},
            },
        )
        assert result["status"] == "committed"
        snapshot = _request(
            input_stream,
            output_stream,
            "snapshot-one",
            "snapshot",
            {"campaign_id": campaign_id},
        )
        assert snapshot["state"]["synthetic.desktop.state"] == {"counter": 3}
    finally:
        _shutdown(process, input_stream, output_stream)

    reopened, reopened_input, reopened_output = _launch(database)
    try:
        campaign_index = _request(
            reopened_input,
            reopened_output,
            "construction-index-after-restart",
            "campaign_index",
            {},
        )
        builder_entry = next(
            entry for entry in campaign_index if entry["campaign_id"] == builder_campaign_id
        )
        assert builder_entry["writer"] == "dm"
        assert builder_entry["revision"] == 5
        assert builder_entry["lifecycle"] == "active"
        assert builder_entry["provider_id"] == "test.dm"
        assert builder_entry["model_id"] == "test.dm/model0"
        second = _request(
            reopened_input,
            reopened_output,
            "action-two",
            "execute_action",
            {
                "campaign_id": campaign_id,
                "action_id": "action-desktop-two",
                "idempotency_key": "idem-desktop-two",
                "writer": "dm",
                "expected_revision": 5,
                "turn_id": dm_turn_id,
                "package_id": "synthetic.desktop",
                "tool_name": "advance",
                "parameters": {"steps": 2},
            },
        )
        assert second["status"] == "committed"
        snapshot = _request(
            reopened_input,
            reopened_output,
            "snapshot-two",
            "snapshot",
            {"campaign_id": campaign_id},
        )
        assert snapshot["state"]["synthetic.desktop.state"] == {"counter": 5}

        _request(
            reopened_input,
            reopened_output,
            "complete-turn-one",
            "complete_turn",
            {
                "campaign_id": campaign_id,
                "turn_id": dm_turn_id,
            },
        )
        renamed = _request(
            reopened_input,
            reopened_output,
            "rename",
            "rename_campaign",
            {
                "operation_id": "rename-desktop",
                "campaign_id": campaign_id,
                "display_name": "Package Desktop Resumed",
                "expected_revision": 6,
            },
        )
        assert renamed["resulting_revision"] == 7
        archived = _request(
            reopened_input,
            reopened_output,
            "archive",
            "archive_campaign",
            {
                "operation_id": "archive-desktop",
                "campaign_id": campaign_id,
                "expected_revision": 7,
                "designated_session_id": dm_session_id,
            },
        )
        assert archived["lifecycle"] == "archived"
        restored = _request(
            reopened_input,
            reopened_output,
            "restore-archive",
            "restore_archived_campaign",
            {
                "operation_id": "restore-archive-desktop",
                "campaign_id": campaign_id,
                "expected_revision": 8,
                "session_id": dm_session_id,
            },
        )
        assert restored["lifecycle"] == "active"
        _request(
            reopened_input,
            reopened_output,
            "open-turn-two",
            "open_turn",
            {
                "campaign_id": campaign_id,
                "turn_id": "turn-package-desktop-two",
            },
        )
        third = _request(
            reopened_input,
            reopened_output,
            "action-three",
            "execute_action",
            {
                "campaign_id": campaign_id,
                "action_id": "action-desktop-three",
                "idempotency_key": "idem-desktop-three",
                "writer": "dm",
                "expected_revision": 9,
                "turn_id": "turn-package-desktop-two",
                "package_id": "synthetic.desktop",
                "tool_name": "advance",
                "parameters": {"steps": 1},
            },
        )
        assert third["status"] == "committed"
        _request(
            reopened_input,
            reopened_output,
            "complete-turn-two",
            "complete_turn",
            {
                "campaign_id": campaign_id,
                "turn_id": "turn-package-desktop-two",
            },
        )
        backup = _request(
            reopened_input,
            reopened_output,
            "backup",
            "backup_database",
            {"backup_id": "desktop-backup"},
        )
        assert backup == {"backup_id": "desktop-backup", "status": "created"}
        exported = _request(
            reopened_input,
            reopened_output,
            "export",
            "export_campaign",
            {
                "export_id": "desktop-export",
                "operation_id": "export-desktop",
                "campaign_id": campaign_id,
            },
        )
        assert exported["captured_revision"] == 10
        trashed = _request(
            reopened_input,
            reopened_output,
            "trash",
            "trash_campaign",
            {
                "operation_id": "trash-desktop",
                "campaign_id": campaign_id,
                "expected_revision": 10,
                "designated_session_id": dm_session_id,
            },
        )
        assert trashed["lifecycle"] == "tombstoned"
        purged = _request(
            reopened_input,
            reopened_output,
            "purge",
            "purge_campaign",
            {
                "operation_id": "purge-desktop",
                "campaign_id": campaign_id,
                "expected_revision": 11,
                "confirmation": f"permanently purge {campaign_id}",
            },
        )
        assert purged["campaign_id"] == campaign_id
        recovered = _request(
            reopened_input,
            reopened_output,
            "recover",
            "recover_campaign",
            {
                "export_id": "desktop-export",
                "recovery_id": "recover-desktop",
            },
        )
        assert recovered["captured_revision"] == 10
        recovered_snapshot = _request(
            reopened_input,
            reopened_output,
            "snapshot-recovered",
            "snapshot",
            {"campaign_id": campaign_id},
        )
        assert recovered_snapshot["display_name"] == "Package Desktop Resumed"
        assert recovered_snapshot["state"]["synthetic.desktop.state"] == {"counter": 6}
        print(
            "F6D PACKAGE PROCESS",
            "install=ok",
            "restart=ok",
            "lifecycle=ok",
            "recovery=ok",
            "state=6",
        )
    finally:
        _shutdown(reopened, reopened_input, reopened_output)


def test_owner_process_recovers_archive_imported_from_other_storage_root(
    tmp_path: Path,
) -> None:
    owner_a_root = tmp_path / "owner-a"
    owner_b_root = tmp_path / "owner-b"
    inbound_root = tmp_path / "selected-inbound"
    owner_a_root.mkdir()
    owner_b_root.mkdir()
    inbound_root.mkdir()

    process_a, input_a, output_a = _launch(owner_a_root / "authority.sqlite3")
    try:
        candidate = _request(
            input_a,
            output_a,
            "install-portable-package",
            "install_package",
            {
                "install_id": "portable-install",
                "archive_base64": base64.b64encode(_executable_package_archive()).decode("ascii"),
            },
        )
        campaign_id, dm_turn_id, revision = _construct_campaign(
            input_a,
            output_a,
            owner_a_root / "authority.sqlite3",
            slug="portable",
            display_name="Portable Campaign",
            candidate_id=candidate["candidate_id"],
        )
        _request(
            input_a,
            output_a,
            "mutate-portable",
            "execute_action",
            {
                "campaign_id": campaign_id,
                "action_id": "portable-action",
                "idempotency_key": "portable-action-idem",
                "writer": "dm",
                "expected_revision": revision,
                "turn_id": dm_turn_id,
                "package_id": "synthetic.desktop",
                "tool_name": "advance",
                "parameters": {"steps": 1},
            },
        )
        _request(
            input_a,
            output_a,
            "complete-portable-turn",
            "complete_turn",
            {"campaign_id": campaign_id, "turn_id": dm_turn_id},
        )
        exported = _request(
            input_a,
            output_a,
            "export-portable",
            "export_campaign",
            {
                "export_id": "portable-export",
                "operation_id": "portable-export-op",
                "campaign_id": campaign_id,
            },
        )
        assert exported["campaign_id"] == campaign_id
    finally:
        _shutdown(process_a, input_a, output_a)

    selected_archive = inbound_root / "received.sylvia-gm-campaign"
    shutil.copyfile(
        owner_a_root / "exports" / "portable-export.sylvia-gm-campaign",
        selected_archive,
    )
    assert not (owner_b_root / "exports").exists()

    process_b, input_b, output_b = _launch(owner_b_root / "authority.sqlite3")
    try:
        recovered = _request(
            input_b,
            output_b,
            "recover-selected",
            "recover_campaign_file",
            {
                "recovery_id": "selected-recovery",
                "source_path": str(selected_archive),
            },
        )
        assert recovered["campaign_id"] == campaign_id
        assert recovered["captured_revision"] == 5
        assert recovered["archive_sha256"] == exported["archive_sha256"]
        assert recovered["byte_count"] == exported["byte_count"]
        campaign_index = _request(input_b, output_b, "index-recovered", "campaign_index")
        assert [entry["campaign_id"] for entry in campaign_index] == [campaign_id]
        print(
            "F6D EXTERNAL RECOVERY",
            "source=selected-file",
            "foreign-storage-root=yes",
            f"recovered={campaign_id}",
        )
    finally:
        _shutdown(process_b, input_b, output_b)
