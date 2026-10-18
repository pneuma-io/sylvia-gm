"""A failed import becomes a separate saved repair chat only after the player's request."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.provider._base import ProviderResponse, ToolCall
from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness
from tests.test_builder_library import LibraryProvider
from tests.test_import_repair import _rewrite_zip
from tests.test_plan_bundles import portable_plan
from tests.test_spatial_runtime import _archive, _runtime


class SameTurnRepairDiscoveryProvider(LibraryProvider):
    """Select the Plan ID returned by same-turn discovery."""

    async def generate(self, messages, tools=None, **kwargs):
        if self._index == 4:
            self.calls.append({"messages": messages, "tools": tools})
            self._index += 1
            discovered = json.loads(next(
                message["content"] for message in reversed(messages)
                if message.get("tool_call_id") == "discover-published-plan"
            ))
            return ProviderResponse(tool_calls=[ToolCall(
                id="prepare-published-plan",
                name="prepare_campaign_setup",
                arguments=json.dumps({
                    "campaign_plan_id": (
                        discovered["items"][0]["id"]
                        if discovered["items"] else "campaign-plan-unavailable"
                    ),
                }),
            )])
        if self._index == 5:
            self.calls.append({"messages": messages, "tools": tools})
            self._index += 1
            return ProviderResponse(content="The repaired Plan is available.")
        return await super().generate(messages, tools, **kwargs)


def test_failed_import_repair_survives_restart_and_preserves_original(tmp_path: Path) -> None:
    original_archive = _archive(_runtime([]))
    with zipfile.ZipFile(io.BytesIO(original_archive)) as original:
        members = {name: original.read(name) for name in original.namelist()}
    original_manifest = members["manifest.json"]
    bad_manifest = json.loads(original_manifest)
    bad_manifest["invented"] = True
    bad_bytes = canonical_json_bytes(bad_manifest)
    members["manifest.json"] = bad_bytes
    source_path = tmp_path / "Relay.sylvia-gm-package"
    with zipfile.ZipFile(source_path, "w") as source:
        for name, value in members.items():
            source.writestr(name, value)
    source_bytes = source_path.read_bytes()
    database = tmp_path / "owner.sqlite3"
    harness = _OwnerHarness(database, LibraryProvider(responses=[]))
    try:
        before_chats = harness.request("before-chats", "builder_chat_index")
        offered = harness.request(
            "bad-import",
            "install_package_file",
            {
                "install_id": "bad-import",
                "source_path": str(source_path),
            },
        )
        assert offered["status"] == "repairable", offered
        assert "source_path" not in offered
        assert harness.request("after-chats", "builder_chat_index") == before_chats
        command = {
            "repair_id": offered["repair_id"],
            "operation_id": "repair-relay",
            "provider_id": "test.transport",
            "model_id": "test.transport/model0",
        }
        chat = harness.request("start-repair", "start_import_repair", command)
        session_id = chat["session"]["session_id"]
        assert chat["session"]["campaign_id"] is None
        assert source_path.read_bytes() == source_bytes
    finally:
        harness.close()

    provider = LibraryProvider(
        responses=[
            ProviderResponse(
                tool_calls=[
                    ToolCall(
                        id="check-incomplete",
                        name="check_import_repair",
                        arguments="{}",
                    )
                ]
            ),
            ProviderResponse(
                tool_calls=[
                    ToolCall(
                        id="repair-field",
                        name="write_file",
                        arguments=json.dumps(
                            {
                                "path": "repair-draft/manifest.json",
                                "content": original_manifest.decode(),
                                "expected_version": hashlib.sha256(bad_bytes).hexdigest(),
                            }
                        ),
                    )
                ]
            ),
            ProviderResponse(
                tool_calls=[
                    ToolCall(
                        id="check-fixed",
                        name="check_import_repair",
                        arguments="{}",
                    )
                ]
            ),
            ProviderResponse(
                tool_calls=[
                    ToolCall(
                        id="publish-fixed",
                        name="publish_import_repair",
                        arguments="{}",
                    )
                ]
            ),
            ProviderResponse(content="The repaired package is available in the library."),
        ]
    )
    harness = _OwnerHarness(database, provider)
    try:
        resumed = harness.request("repeat-start", "start_import_repair", command)
        assert resumed["session"]["session_id"] == session_id
        result = harness.request(
            "fix-relay",
            "submit_session_input",
            {
                "session_id": session_id,
                "attempt_id": "fix-relay",
                "message_id": "fix-relay",
                "user_text": "",
                "builder_start": True,
            },
        )
        assert result["interruption"] is None
        receipts = {
            message["tool_call_id"]: json.loads(message["content"])
            for message in provider.calls[-1]["messages"]
            if message.get("tool_call_id")
        }
        assert receipts["check-incomplete"]["success"] is False
        assert receipts["check-incomplete"]["validation_errors"][0]["path"] == "$.invented"
        assert receipts["repair-field"]["success"] is True, receipts["repair-field"]
        assert receipts["check-fixed"]["status"] == "valid_draft", receipts["check-fixed"]
        assert receipts["check-fixed"]["published"] is False
        saved = receipts["publish-fixed"]
        assert saved["success"] is True, saved
        assert saved["published"] is True
        assert any(
            item["candidate_id"] == saved["candidate_id"]
            for item in harness.request("saved-candidates", "package_candidates")
        )
        assert source_path.read_bytes() == source_bytes
    finally:
        harness.close()


def test_repaired_plan_is_published_with_its_contained_setting(tmp_path: Path) -> None:
    original, _source, plan_bytes = portable_plan(tmp_path / "plan-source")
    with zipfile.ZipFile(io.BytesIO(plan_bytes)) as archive:
        good_record = archive.read("record.json")
    record = json.loads(good_record)
    record["unsupported_field"] = True
    bad_record = canonical_json_bytes(record)
    bad_bytes = _rewrite_zip(plan_bytes, {"record.json": bad_record})
    source_path = tmp_path / "Relay.sylvia-gm-plan"
    source_path.write_bytes(bad_bytes)
    provider = SameTurnRepairDiscoveryProvider(
        responses=[
            ProviderResponse(
                tool_calls=[
                    ToolCall(
                        id="repair-record",
                        name="write_file",
                        arguments=json.dumps(
                            {
                                "path": "repair-draft/record.json",
                                "content": good_record.decode(),
                                "expected_version": hashlib.sha256(bad_record).hexdigest(),
                            }
                        ),
                    )
                ]
            ),
            ProviderResponse(
                tool_calls=[
                    ToolCall(
                        id="check-plan",
                        name="check_import_repair",
                        arguments="{}",
                    )
                ]
            ),
            ProviderResponse(
                tool_calls=[
                    ToolCall(
                        id="publish-plan",
                        name="publish_import_repair",
                        arguments="{}",
                    )
                ]
            ),
            ProviderResponse(
                tool_calls=[
                    ToolCall(
                        id="discover-published-plan",
                        name="discover_campaign_content",
                        arguments=json.dumps({"query": original.plan.payload["title"]}),
                    )
                ]
            ),
        ]
    )
    destination = tmp_path / "destination"
    destination.mkdir()
    harness = _OwnerHarness(destination / "owner.sqlite3", provider)
    try:
        offered = harness.request(
            "bad-plan",
            "install_package_file",
            {
                "install_id": "bad-plan",
                "source_path": str(source_path),
            },
        )
        assert offered["status"] == "repairable", offered
        chat = harness.request(
            "repair-plan",
            "start_import_repair",
            {
                "repair_id": offered["repair_id"],
                "operation_id": "repair-plan",
                "provider_id": "test.transport",
                "model_id": "test.transport/model0",
            },
        )
        result = harness.request(
            "start-plan",
            "submit_session_input",
            {
                "session_id": chat["session"]["session_id"],
                "attempt_id": "fix-plan",
                "message_id": "fix-plan",
                "user_text": "",
                "builder_start": True,
            },
        )
        assert result["interruption"] is None
        receipts = {
            message["tool_call_id"]: json.loads(message["content"])
            for message in provider.calls[-1]["messages"]
            if message.get("tool_call_id")
        }
        assert receipts["repair-record"]["success"] is True, receipts
        assert receipts["check-plan"]["status"] == "valid_draft", receipts
        saved = receipts["publish-plan"]
        assert saved["success"] is True and saved["published"] is True, saved
        assert saved["title"] == original.plan.payload["title"]
        discovered = receipts["discover-published-plan"]
        assert [item["id"] for item in discovered["items"]] == [saved["id"]]
        prepared = receipts["prepare-published-plan"]
        assert prepared["status"] == "setup_ready"
        assert prepared["campaign_plan_id"] == saved["id"]
        assert any(
            item.get("id") == saved["id"]
            for item in harness.request("plans", "campaign_creation_catalog")
        )
        assert source_path.read_bytes() == bad_bytes
    finally:
        harness.close()
