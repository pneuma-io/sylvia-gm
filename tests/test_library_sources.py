"""A saved Builder can inspect another source version without adopting it implicitly."""

import json
from dataclasses import replace

import pytest

from sylvia_gm.agent._library_source_tool import library_source_tool
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.builder_references import launch_references
from sylvia_gm.application.library_sources import LibrarySourceReader
from sylvia_gm.application.package_materialization import materialize_package_archive
from sylvia_gm.builder._package_snapshots import retained_package_drafts, select_snapshot_sources
from sylvia_gm.builder._setting_materializer import _ruleset_copy, _world_draft
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.provider._base import ProviderResponse, ToolCall
from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness
from tests.test_builder_library import (
    LibraryProvider,
    install_character_packages,
    launch_library_builder,
)
from tests.test_owned_package_updates import newer_source


def test_saved_plan_builder_reads_standalone_update_without_replacing_launch_sources(tmp_path):
    database = tmp_path / "library.db"
    with AuthorityOwner.open(database, "seed") as owner:
        original, setting_id = install_character_packages(owner)
        drafts = _ruleset_copy(owner, original.candidate_id)
        original_text = drafts[0].payloads["runtime/ruleset.yaml"].decode()
        changed_text = original_text.replace("expression: 2d6", "expression: 3d6")
        draft = replace(
            drafts[0],
            record={**drafts[0].record, "version": "0.2.0"},
            payloads={**drafts[0].payloads, "runtime/ruleset.yaml": changed_text.encode()},
        )
        with materialize_package_archive((draft, *drafts[1:])) as archive:
            updated = owner.install_package_archive(archive.archive, install_id="updated-rules")
        ruleset_id = draft.record["id"]

    def inspect(identity, action, **params):
        return ToolCall(
            id=identity,
            name="inspect_library_sources",
            arguments=json.dumps({"action": action, **params}),
        )

    provider = LibraryProvider(
        responses=[
            ProviderResponse(
                tool_calls=[
                    inspect("versions", "candidates"),
                    inspect("files", "files", candidate_id=updated.candidate_id, limit=100),
                    inspect(
                        "read",
                        "read",
                        candidate_id=updated.candidate_id,
                        path="ruleset/runtime/ruleset.yaml",
                        limit=32000,
                    ),
                    inspect(
                        "bad-path",
                        "read",
                        candidate_id=updated.candidate_id,
                        path="../../other.txt",
                    ),
                ]
            ),
            ProviderResponse(
                content="I can compare these source changes with your saved custom rules."
            ),
        ]
    )
    harness = _OwnerHarness(database, provider)
    try:
        _, session_id = launch_library_builder(harness, original.candidate_id, setting_id, "plan")
        result = harness.request(
            "inspect",
            "submit_session_input",
            {
                "session_id": session_id,
                "attempt_id": "inspect",
                "message_id": "inspect",
                "user_text": "",
                "builder_start": True,
            },
        )
        assert result["interruption"] is None
        results = {
            message["tool_call_id"]: json.loads(message["content"])
            for message in provider.calls[-1]["messages"]
            if message.get("role") == "tool"
        }
        candidates = results["versions"]["candidates"]
        assert {item["candidate_id"] for item in candidates} == {
            original.candidate_id,
            updated.candidate_id,
        }
        assert (
            next(item for item in candidates if item["candidate_id"] == updated.candidate_id)[
                "source_kind"
            ]
            == "ruleset"
        )
        assert "ruleset/runtime/ruleset.yaml" in results["files"]["paths"]
        assert results["read"]["package"]["version"] == "0.2.0"
        assert results["read"]["content"] == changed_text
        assert results["read"]["closure_sha256"] == updated.closure_sha256
        assert results["bad-path"]["success"] is False
        assert "record" in results["bad-path"]["message"]
        assert "files" in results["bad-path"]["message"]
    finally:
        harness.close()

    with AuthorityOwner.open(database, "reopen") as owner:
        assert launch_references(owner.session_context(session_id))["candidate_id"] == (
            original.candidate_id
        )
        reader = LibrarySourceReader(owner, ruleset_id, setting_id)
        tool = library_source_tool(reader)
        unsupported_search = tool.handler(
            {
                "action": "read",
                "candidate_id": updated.candidate_id,
                "path": "ruleset/runtime/ruleset.yaml",
                "query": "move",
                "limit": 30,
            },
            None,
        )
        assert unsupported_search["success"] is False
        assert unsupported_search["code"] == "library_source_query_unsupported"
        assert "candidates" in unsupported_search["message"]
        assert "content" not in unsupported_search
        first = tool.handler(
            {
                "action": "read",
                "candidate_id": updated.candidate_id,
                "path": "ruleset/runtime/ruleset.yaml",
                "limit": 10,
            },
            None,
        )
        second = tool.handler(
            {
                "action": "read",
                "candidate_id": updated.candidate_id,
                "path": "ruleset/runtime/ruleset.yaml",
                "offset": 10,
                "limit": 32000,
            },
            None,
        )
        assert first["content"] + second["content"] == changed_text
        assert first["next_offset"] == 10 and second["next_offset"] is None
        assert reader.read(original.candidate_id, "ruleset/runtime/ruleset.yaml")["content"] == (
            original_text
        )
        foreign = library_source_tool(LibrarySourceReader(owner, "unrelated.rules"))
        assert foreign.handler({"action": "candidates"}, None)["candidates"] == []
        assert (
            foreign.handler(
                {
                    "action": "read",
                    "candidate_id": updated.candidate_id,
                    "path": "ruleset/runtime/ruleset.yaml",
                },
                None,
            )["success"]
            is False
        )


def test_builder_can_adopt_ruleset_from_candidate_with_another_setting(tmp_path):
    """A foreign Setting must not hide or travel with its compatible Ruleset source."""

    database = tmp_path / "different-setting-source.db"
    with AuthorityOwner.open(database, "owner") as owner:
        original, selected_setting_id = install_character_packages(owner)
        standalone_update, _, _, _ = newer_source(owner, original, setting=False)
        incoming = _ruleset_copy(owner, standalone_update.candidate_id)
        incoming_ruleset = next(
            draft for draft in incoming if draft.record["content_kind"] == "ruleset"
        )
        ruleset_id = incoming_ruleset.record["id"]

        foreign_only_support = _world_draft(
            package_id="local.conformance-notes",
            version="1.0.0",
            title="Conformance-only Field Notes",
            payloads={"notes.md": b"Only the conformance Setting can reach these notes."},
            ruleset=incoming_ruleset,
        )
        foreign_only_support = replace(
            foreign_only_support,
            record={
                **foreign_only_support.record,
                "content_kind": "support",
                "dependencies": [],
            },
        )
        foreign_setting = _world_draft(
            package_id="local.conformance-setting",
            version="1.0.0",
            title="Conformance Setting",
            payloads={"world.md": b"A world that must not replace the selected Setting."},
            ruleset=incoming_ruleset,
        )
        foreign_setting = replace(
            foreign_setting,
            record={
                **foreign_setting.record,
                "dependencies": [
                    *foreign_setting.record["dependencies"],
                    {
                        "namespace": "setting_notes",
                        "kind": "reference",
                        "id": foreign_only_support.record["id"],
                        "version": foreign_only_support.record["version"],
                    },
                ],
            },
        )
        with materialize_package_archive(
            (*incoming, foreign_setting, foreign_only_support)
        ) as archive:
            foreign_candidate = owner.install_package_archive(
                archive.archive, install_id="different-setting-source"
            )

        reader = LibrarySourceReader(owner, ruleset_id, selected_setting_id)
        candidates = {item["candidate_id"]: item for item in reader.candidates(query=ruleset_id)}
        assert candidates[original.candidate_id]["source_kind"] == "setting"
        assert candidates[standalone_update.candidate_id]["source_kind"] == "ruleset"
        foreign_summary = candidates[foreign_candidate.candidate_id]
        assert foreign_summary["source_kind"] == "ruleset"
        assert foreign_summary["closure_sha256"] == foreign_candidate.closure_sha256
        assert {(package["prefix"], package["id"]) for package in foreign_summary["packages"]} == {
            ("ruleset", ruleset_id),
            ("dependencies/local.signal-manual", "local.signal-manual"),
        }

        no_setting_summary = next(
            item
            for item in LibrarySourceReader(owner, ruleset_id).candidates(query=ruleset_id)
            if item["candidate_id"] == foreign_candidate.candidate_id
        )
        assert no_setting_summary["source_kind"] == "ruleset"
        assert no_setting_summary["packages"] == foreign_summary["packages"]
        assert foreign_candidate.candidate_id not in {
            item["candidate_id"] for item in reader.candidates(query="Conformance-only Field Notes")
        }

        files = reader.files(foreign_candidate.candidate_id)
        assert files["closure_sha256"] == foreign_candidate.closure_sha256
        assert "ruleset/runtime/ruleset.yaml" in files["paths"]
        assert "dependencies/local.signal-manual/manual.md" in files["paths"]
        assert not any("local.conformance-setting" in path for path in files["paths"])
        assert not any("local.conformance-notes" in path for path in files["paths"])
        assert (
            reader.read(
                foreign_candidate.candidate_id,
                "dependencies/local.signal-manual/manual.md",
            )["content"]
            == "The updated signal protocol has a new ping operation."
        )
        record_result = reader.record(foreign_candidate.candidate_id, "ruleset")
        assert record_result["closure_sha256"] == foreign_candidate.closure_sha256
        ruleset_record = json.loads(record_result["content"])
        assert ruleset_record["id"] == ruleset_id
        assert ruleset_record["version"] == "0.2.0"
        with pytest.raises(CommandRejected, match="path is unavailable"):
            reader.read(
                foreign_candidate.candidate_id,
                "dependencies/local.conformance-notes/notes.md",
            )

        retained = retained_package_drafts(owner, original.candidate_id, include_setting=True)
        old_setting = next(draft for draft in retained if draft.record["id"] == selected_setting_id)
        adoption_root = tmp_path / "ruleset-adoption"
        adoption_root.mkdir()
        (adoption_root / "ruleset-source.json").write_text(
            json.dumps(
                {
                    "candidate_id": foreign_candidate.candidate_id,
                    "expected_closure_sha256": foreign_candidate.closure_sha256,
                }
            ),
            encoding="utf-8",
        )
        adopted = select_snapshot_sources(owner, adoption_root, adoption_root, retained)
        adopted_by_id = {draft.record["id"]: draft for draft in adopted}
        assert set(adopted_by_id) == {
            ruleset_id,
            "local.signal-manual",
            selected_setting_id,
        }
        assert adopted_by_id[ruleset_id].record["version"] == "0.2.0"
        assert adopted_by_id[selected_setting_id].record["version"] == old_setting.record["version"]
        assert adopted_by_id[selected_setting_id].payloads == old_setting.payloads
        assert (
            next(
                dependency
                for dependency in adopted_by_id[selected_setting_id].record["dependencies"]
                if dependency["kind"] == "ruleset_snapshot"
            )["version"]
            == "0.2.0"
        )
        assert "local.conformance-setting" not in adopted_by_id
        assert "local.conformance-notes" not in adopted_by_id

        invalid_root = tmp_path / "setting-adoption"
        invalid_root.mkdir()
        (invalid_root / "setting-source.json").write_text(
            (adoption_root / "ruleset-source.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        with pytest.raises(CommandRejected, match="retain the logical Setting"):
            select_snapshot_sources(owner, invalid_root, invalid_root, retained)
