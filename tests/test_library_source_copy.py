"""Installed source copies enter only the Builder's guarded workspace."""

import json

from sylvia_gm.agent._library_source_copy_tool import library_source_copy_tool
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.library_sources import LibrarySourceReader
from sylvia_gm.application.package_materialization import materialize_package_archive
from sylvia_gm.builder._package_snapshots import ruleset_copy
from sylvia_gm.builder._setting_materializer import _world_draft
from sylvia_gm.builder._workspace_files import content_version
from sylvia_gm.provider._base import ProviderResponse, ToolCall
from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness
from tests.test_builder_library import (
    LibraryProvider,
    install_character_packages,
    launch_library_builder,
)
from tests.test_owned_package_updates import newer_source


def _install_foreign_setting_source(owner, original):
    standalone, _, _, _ = newer_source(owner, original, setting=False)
    incoming = ruleset_copy(owner, standalone.candidate_id)
    ruleset = next(draft for draft in incoming if draft.record["content_kind"] == "ruleset")
    foreign_setting = _world_draft(
        package_id="local.conformance-setting",
        version="1.0.0",
        title="Conformance Setting",
        payloads={"world.md": b"This world must not enter another Setting's draft."},
        ruleset=ruleset,
    )
    with materialize_package_archive((*incoming, foreign_setting)) as archive:
        candidate = owner.install_package_archive(
            archive.archive, install_id="copy-source-with-foreign-setting"
        )
    return candidate, ruleset


def _copy_call(identity, candidate, source_type, source_path, destination_path):
    return ToolCall(
        id=identity,
        name="copy_library_source",
        arguments=json.dumps(
            {
                "candidate_id": candidate.candidate_id,
                "expected_closure_sha256": candidate.closure_sha256,
                "source_type": source_type,
                "source_path": source_path,
                "destination_path": destination_path,
            }
        ),
    )


def test_plan_builder_copies_an_incoming_ruleset_file_and_record_before_editing(tmp_path):
    database = tmp_path / "library-source-copy.db"
    runtime_destination = "campaign-plan-draft/ruleset-files/runtime/ruleset.yaml"
    record_destination = "campaign-plan-draft/package-records/signal-and-static.json"
    with AuthorityOwner.open(database, "seed") as owner:
        original, setting_id = install_character_packages(owner)
        incoming, ruleset = _install_foreign_setting_source(owner, original)
        reader = LibrarySourceReader(owner, ruleset.record["id"], setting_id)
        runtime_source = reader.read(incoming.candidate_id, "ruleset/runtime/ruleset.yaml")[
            "content"
        ]
        record_source = reader.record(incoming.candidate_id, "ruleset")["content"]
        source_schema_hash = json.loads(record_source)["participant_artifacts"][0]["schema_sha256"]

    provider = LibraryProvider(
        responses=[
            ProviderResponse(
                tool_calls=[
                    _copy_call(
                        "copy-runtime",
                        incoming,
                        "file",
                        "ruleset/runtime/ruleset.yaml",
                        runtime_destination,
                    ),
                    _copy_call(
                        "copy-record",
                        incoming,
                        "editable_record",
                        "ruleset",
                        record_destination,
                    ),
                ]
            ),
            ProviderResponse(
                tool_calls=[
                    ToolCall(
                        id="edit-runtime",
                        name="edit_file",
                        arguments=json.dumps(
                            {
                                "path": runtime_destination,
                                "old_text": "source_ping:",
                                "new_text": "source_ping_customized:",
                                "expected_version": content_version(runtime_source.encode("utf-8")),
                            }
                        ),
                    ),
                    ToolCall(
                        id="search-runtime",
                        name="search_files",
                        arguments=json.dumps(
                            {
                                "path": "campaign-plan-draft/ruleset-files",
                                "pattern": "source_ping_customized",
                                "target": "content",
                            }
                        ),
                    ),
                    ToolCall(
                        id="search-record-schema",
                        name="search_files",
                        arguments=json.dumps(
                            {
                                "path": record_destination,
                                "pattern": '"schema_sha256"',
                                "target": "content",
                            }
                        ),
                    ),
                ]
            ),
            ProviderResponse(content="The owned Ruleset copy is ready for focused changes."),
        ]
    )
    harness = _OwnerHarness(database, provider)
    try:
        _, session_id = launch_library_builder(harness, original.candidate_id, setting_id, "plan")
        result = harness.request(
            "copy-library-source",
            "submit_session_input",
            {
                "session_id": session_id,
                "attempt_id": "copy-library-source",
                "message_id": "copy-library-source",
                "user_text": "",
                "builder_start": True,
            },
        )
        assert result["interruption"] is None
        tool_names = {tool["function"]["name"] for tool in provider.calls[0]["tools"]}
        assert "copy_library_source" in tool_names
        results = {
            message["tool_call_id"]: json.loads(message["content"])
            for message in provider.calls[-1]["messages"]
            if message.get("role") == "tool"
        }
        runtime_copy = results["copy-runtime"]
        assert set(runtime_copy) == {
            "success",
            "candidate_id",
            "closure_sha256",
            "source_type",
            "source_path",
            "package",
            "path",
            "bytes_written",
            "version",
        }
        assert runtime_copy["success"] is True
        assert runtime_copy["candidate_id"] == incoming.candidate_id
        assert runtime_copy["closure_sha256"] == incoming.closure_sha256
        assert runtime_copy["package"]["id"] == ruleset.record["id"]
        assert runtime_copy["path"] == runtime_destination
        assert "content" not in runtime_copy
        assert results["copy-record"]["source_type"] == "editable_record"
        assert results["copy-record"]["path"] == record_destination
        assert "content" not in results["copy-record"]
        assert results["edit-runtime"]["success"] is True
        assert results["search-runtime"]["count"] == 1
        schema_match = results["search-record-schema"]["matches"][0]
        assert source_schema_hash in schema_match["content"]
        assert schema_match["line"] > 1

        workspace = tmp_path / "builder-chats" / session_id
        copied_record_source = (workspace / record_destination).read_text(encoding="utf-8")
        assert json.loads(copied_record_source) == json.loads(record_source)
        assert (
            json.loads(copied_record_source)["participant_artifacts"][0]["schema_sha256"]
            == source_schema_hash
        )
        edited_runtime = (workspace / runtime_destination).read_text(encoding="utf-8")
        assert "source_ping_customized:" in edited_runtime
        assert "source_ping:" not in edited_runtime
    finally:
        harness.close()

    with AuthorityOwner.open(database, "verify-source") as owner:
        reader = LibrarySourceReader(owner, ruleset.record["id"], setting_id)
        assert (
            reader.read(incoming.candidate_id, "ruleset/runtime/ruleset.yaml")["content"]
            == runtime_source
        )


def test_library_source_copy_rejects_changed_foreign_and_stale_inputs_without_writing(tmp_path):
    database = tmp_path / "library-source-copy-rejections.db"
    with AuthorityOwner.open(database, "owner") as owner:
        original, setting_id = install_character_packages(owner)
        incoming, ruleset = _install_foreign_setting_source(owner, original)
        reader = LibrarySourceReader(owner, ruleset.record["id"], setting_id)
        source_path = "ruleset/runtime/ruleset.yaml"
        source_before = reader.read(incoming.candidate_id, source_path)["content"]
        tool = library_source_copy_tool(reader, tmp_path)
        base = {
            "candidate_id": incoming.candidate_id,
            "expected_closure_sha256": incoming.closure_sha256,
            "source_type": "file",
            "source_path": source_path,
        }

        changed = tool.handler(
            {
                **base,
                "expected_closure_sha256": "0" * 64,
                "destination_path": "changed.yaml",
            },
            None,
        )
        assert changed["code"] == "library_source_copy_changed"
        assert changed["closure_sha256"] == incoming.closure_sha256
        assert not (tmp_path / "changed.yaml").exists()

        foreign = tool.handler(
            {
                **base,
                "source_path": "dependencies/local.conformance-setting/world.md",
                "destination_path": "foreign.md",
            },
            None,
        )
        assert foreign == {"success": False, "code": "library_source_copy_unavailable"}
        assert not (tmp_path / "foreign.md").exists()

        invalid = tool.handler(
            {**base, "destination_path": "invalid.yaml", "unexpected": True}, None
        )
        assert invalid == {
            "success": False,
            "code": "library_source_copy_parameters_invalid",
        }
        assert not (tmp_path / "invalid.yaml").exists()

        denied = tool.handler({**base, "destination_path": "../outside.yaml"}, None)
        assert denied == {"success": False, "code": "library_source_copy_path_denied"}
        assert not (tmp_path.parent / "outside.yaml").exists()

        target = tmp_path / "existing.yaml"
        target.write_text("Keep this local edit.", encoding="utf-8")
        stale = tool.handler({**base, "destination_path": target.name}, None)
        assert stale["error_code"] == "stale_file"
        assert target.read_text(encoding="utf-8") == "Keep this local edit."
        assert reader.read(incoming.candidate_id, source_path)["content"] == source_before
