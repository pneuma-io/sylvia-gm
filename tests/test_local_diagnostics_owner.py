"""The real owner routes compact reports without promoting Full Audit or logging input."""

import json

from sylvia_gm.interfaces import owner_process
from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness


def test_owner_errors_reports_and_tool_rejections_exclude_injected_content(
    tmp_path, monkeypatch, capsys
):
    sentinel = "private_story_input"
    exception_type = type(sentinel, (RuntimeError,), {})
    original = owner_process._dispatch_selected

    def dispatch(*args, **kwargs):
        if args[1] == "campaign_index" and args[2].get("fixture_internal_error"):
            raise exception_type("Bearer sk-private-token C:\\Users\\PrivatePlayer\\story.txt")
        return original(*args, **kwargs)

    monkeypatch.setattr(owner_process, "_dispatch_selected", dispatch)
    harness = _OwnerHarness(tmp_path / "owner.db", None)
    ids = []
    try:
        assert harness.request("ordinary", "campaign_index", {"unused_prompt": sentinel}) == []
        assert harness.request("audit-off", "admin_full_audit_status")["enabled"] is False
        fault = harness.request(
            "renderer-fault",
            "record_local_fault",
            {
                "category": "renderer_error",
                "delta": 10000,
                "message": sentinel,
                "stack": str(tmp_path),
            },
        )
        ids.append(fault["diagnostic_id"])
        for invalid in (True, False, None, "1", 1.0, 0, -1, 65536, {"input": sentinel}, [1]):
            harness.send(
                "invalid-delta",
                {
                    "method": "record_local_fault",
                    "params": {
                        "category": "renderer_error",
                        "delta": invalid,
                    },
                },
            )
            rejected = harness.read()
            assert rejected["ok"] is False
            assert "integer from 1 to 65535" in rejected["error"]["message"]
        harness.send(
            "invalid-category",
            {
                "method": "record_local_fault",
                "params": {
                    "category": sentinel,
                    "delta": 1,
                },
            },
        )
        assert "Unknown app failure category" in harness.read()["error"]["message"]
        for method, params in [
            (sentinel, {"nested": {"prompt": sentinel}}),
            ("execute_action", {"tool_name": sentinel, "arguments": {"input": sentinel}}),
            ("campaign_index", {"fixture_internal_error": True}),
        ]:
            harness.send(sentinel, {"method": method, "params": params})
            reply = harness.read()
            assert reply["ok"] is False
            ids.append(reply["error"]["diagnostic_id"])
            if method == "campaign_index":
                assert reply["error"]["code"] == "internal_error"
                assert sentinel not in reply["error"]["message"]
        preview = harness.request(
            "preview",
            "preview_local_report",
            {
                "kind": "model_feedback",
                "reproduction": "Choose Continue.",
                "feedback": "Repeated choice.",
            },
        )
        report = json.loads(preview["content"])
        assert report["report_kind"] == "model_feedback"
        assert {row["id"] for row in report["diagnostics"]}.issuperset(ids)
        assert (
            next(row for row in report["diagnostics"] if row["id"] == fault["diagnostic_id"])[
                "count"
            ]
            == 10000
        )
        internal = next(row for row in report["diagnostics"] if row["id"] == ids[-1])
        assert internal["error_type"] == "OtherError"
        assert internal["severity"] == "error"
        target = tmp_path / "selected-report.json"
        saved = harness.request(
            "save",
            "save_local_report",
            {
                "preview_id": preview["preview_id"],
                "destination_path": str(target),
            },
        )
        assert saved["status"] == "saved"
        assert target.read_bytes() == preview["content"].encode("utf-8")
        preview = harness.request(
            "another-preview",
            "preview_local_report",
            {
                "kind": "app_problem",
                "reproduction": "",
                "feedback": "",
            },
        )
        harness.request("cancel", "cancel_local_report", {"preview_id": preview["preview_id"]})
        harness.send(
            "save-after-cancel",
            {
                "method": "save_local_report",
                "params": {
                    "preview_id": preview["preview_id"],
                    "destination_path": str(tmp_path / "cancelled.json"),
                },
            },
        )
        assert harness.read()["ok"] is False
        assert not (tmp_path / "cancelled.json").exists()
        assert harness.request("audit-still-off", "admin_full_audit_status")["enabled"] is False
    finally:
        harness.close()
    captured = capsys.readouterr()
    stored = (tmp_path / "diagnostics/runtime.jsonl").read_text()
    output = stored + target.read_text() + captured.out + captured.err
    for private in (sentinel, "sk-private-token", "PrivatePlayer", str(tmp_path)):
        assert private not in output
        assert json.dumps(private)[1:-1] not in output
    rows = [json.loads(line) for line in stored.splitlines()]
    assert any(row["operation"] == "execute_action" and row["outcome"] == "error" for row in rows)
    assert any(row["operation"] == "other_operation" and row["outcome"] == "error" for row in rows)


def test_unexpected_startup_failure_has_no_raw_traceback_or_error_name(monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise RuntimeError("PRIVATE startup input C:\\Users\\PrivatePlayer\\file")

    class Stream:
        def reconfigure(self, **kwargs):
            pass

    monkeypatch.setattr(owner_process, "run", fail)
    monkeypatch.setattr(owner_process.sys, "stdin", Stream())
    assert owner_process.main() == 2
    output = capsys.readouterr().err
    assert output.strip() == "Sylvia GM owner startup failed"
