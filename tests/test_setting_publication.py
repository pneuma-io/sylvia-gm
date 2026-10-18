"""A new world becomes selectable only with its complete, independently retained rules."""

import hashlib
import io
import json

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.builder._setting_materializer import materialize_setting_draft
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import campaign_setting_entries
from sylvia_gm.provider._base import ProviderResponse, ToolCall
from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness
from tests.test_builder_library import LibraryProvider, install_character_packages


@pytest.mark.parametrize("stale_source", [False, True])
def test_builder_publishes_a_setting_and_refreshes_the_original_interview(tmp_path, stale_source):
    database = tmp_path / "setting-builder.db"
    with AuthorityOwner.open(database, "source-setting") as owner:
        source, original_setting_id = install_character_packages(owner)
    responses = [
        ProviderResponse(
            tool_calls=[
                ToolCall(
                    id="write-world",
                    name="write_file",
                    arguments=json.dumps(
                        {
                            "path": "setting-draft/files/locations/island.md",
                            "content": "# Relay Island\n\nA silent radio tower.\n",
                        }
                    ),
                )
            ]
        ),
        ProviderResponse(
            tool_calls=[
                ToolCall(
                    id="save-world",
                    name="publish_campaign_setting",
                    arguments=json.dumps(
                        {
                            "package_id": "local.relay-island",
                            "version": "1.0.0",
                            "title": "Relay Island",
                        }
                    ),
                )
            ]
        ),
        ProviderResponse(
            tool_calls=[
                ToolCall(
                    id="save-world-again",
                    name="publish_campaign_setting",
                    arguments=json.dumps(
                        {
                            "package_id": "local.relay-island",
                            "version": "1.0.0",
                            "title": "Relay Island",
                        }
                    ),
                )
            ]
        ),
        ProviderResponse(content="Relay Island is available in the Setting picker."),
    ]
    if stale_source:
        source_text = json.dumps(
            {"candidate_id": source.candidate_id, "expected_closure_sha256": "0" * 64}
        )
        responses[0].tool_calls.append(
            ToolCall(
                id="write-stale-source",
                name="write_file",
                arguments=json.dumps(
                    {
                        "path": "setting-draft/ruleset-source.json",
                        "content": source_text,
                    }
                ),
            )
        )
        responses.insert(
            1,
            ProviderResponse(
                tool_calls=[
                    ToolCall(
                        id="save-stale-world",
                        name="publish_campaign_setting",
                        arguments=json.dumps(
                            {
                                "package_id": "local.relay-island",
                                "version": "1.0.0",
                                "title": "Relay Island",
                            }
                        ),
                    )
                ]
            ),
        )
        responses.insert(
            2,
            ProviderResponse(
                tool_calls=[
                    ToolCall(
                        id="remove-stale-source",
                        name="delete_file",
                        arguments=json.dumps(
                            {
                                "path": "setting-draft/ruleset-source.json",
                                "expected_version": hashlib.sha256(
                                    source_text.encode()
                                ).hexdigest(),
                            }
                        ),
                    )
                ]
            ),
        )
    provider = LibraryProvider(responses=responses)
    harness = _OwnerHarness(database, provider)
    try:
        draft = harness.request(
            "new-world-interview",
            "create_guided_campaign_draft",
            {
                "operation_id": "world-interview",
                "display_name": "Island campaign",
                "provider_id": "test.transport",
                "model_id": "test.transport/model0",
            },
        )
        harness.request(
            "select-rules",
            "advance_guided_campaign_draft",
            {
                "operation_id": "select-rules",
                "campaign_id": draft["campaign_id"],
                "builder_session_id": draft["builder_session_id"],
                "expected_construction_revision": 0,
                "current_step": "ruleset",
                "visible_answer": "Signal and Static",
                "answer": {"candidate_id": source.candidate_id},
            },
        )
        launched = harness.request(
            "new-setting",
            "start_guided_creation_subflow",
            {
                "operation_id": "new-setting",
                "campaign_id": draft["campaign_id"],
                "parent_session_id": draft["builder_session_id"],
                "expected_construction_revision": 1,
                "purpose": "setting",
                "visible_answer": "New Setting",
            },
        )
        session_id = launched["child_session_id"]
        result = harness.request(
            "make-island",
            "submit_session_input",
            {
                "session_id": session_id,
                "attempt_id": "make-island",
                "message_id": "make-island",
                "user_text": "",
                "builder_start": True,
            },
        )
        assert result["interruption"] is None
        if stale_source:
            rejected = json.loads(
                next(
                    item["content"]
                    for item in provider.calls[2]["messages"]
                    if item.get("tool_call_id") == "save-stale-world"
                )
            )
            assert rejected["success"] is False
            assert "Source selection changed" in rejected.get("message", "")
            diagnostic = rejected["validation_errors"][0]
            assert diagnostic["file"] == "ruleset-source.json"
            assert diagnostic["path"] == "$.expected_closure_sha256"
            assert diagnostic["guidance"]
        saved = json.loads(
            next(
                item["content"]
                for item in provider.calls[-1]["messages"]
                if item.get("tool_call_id") == "save-world"
            )
        )
        assert saved["success"] is True, saved
        repeated = json.loads(
            next(
                item["content"]
                for item in provider.calls[-1]["messages"]
                if item.get("tool_call_id") == "save-world-again"
            )
        )
        assert repeated == saved
        assert saved["kind"] == "campaign_setting"
        catalog = harness.request("refresh-worlds", "campaign_creation_catalog")
        world = next(item for item in catalog if item.get("id") == "local.relay-island")
        assert world["title"] == "Relay Island"
        choice = next(
            item for item in catalog if item.get("id") == ("sylvia-gm.conformance.signal-static")
        )
        assert any(item["id"] == world["id"] for item in choice["compatible_settings"])
        assert source.candidate_id in choice["selection_aliases"]
        assert any(
            item["id"] == original_setting_id and item["candidate_id"] == source.candidate_id
            for item in choice["compatible_settings"]
        )
        parent = harness.request(
            "retained-interview",
            "session_context",
            {
                "session_id": draft["builder_session_id"],
            },
        )
        assert parent["messages"][-1]["content"]["construction_revision"] == 1
    finally:
        harness.close()
    with AuthorityOwner.open(database, "reopened-world") as owner:
        installed = owner.package_candidate(saved["candidate_id"])
        assert campaign_setting_entries(installed.package_lock)[0].package_id == world["id"]
        exported = io.BytesIO()
        owner.export_package_candidate(installed.candidate_id, exported)
        assert exported.getvalue()
        assert owner.package_candidate(source.candidate_id) == source
    with AuthorityOwner.open(tmp_path / "fresh-library.db", "fresh-library") as owner:
        exported.seek(0)
        imported = owner.install_package_archive(exported, install_id="imported-island")
        assert imported.package_lock == installed.package_lock
        assert len(owner.package_candidates()) == 1
        setting = campaign_setting_entries(imported.package_lock)[0]
        assert owner.package_text_file(
            setting.package_id,
            setting.version,
            setting.sha256,
            "locations/island.md",
        ).startswith("# Relay Island")


def test_setting_customization_keeps_source_files_and_rejects_broken_rules(tmp_path):
    workspace = tmp_path / "builder"
    files = workspace / "setting-draft" / "files"
    files.mkdir(parents=True)
    (files / "world.md").write_text("# Relay Island\nAn isolated signal station.")
    with AuthorityOwner.open(tmp_path / "copies.db", "copy-owner") as owner:
        source, _ = install_character_packages(owner)
        arguments = {
            "candidate_id": source.candidate_id,
            "package_id": "local.island",
            "version": "1.0.0",
            "title": "Island",
        }
        with materialize_setting_draft(owner, workspace, **arguments) as plain:
            original_rules = plain.manifests[0].identity
        replacements = workspace / "setting-draft" / "ruleset-files" / "runtime"
        replacements.mkdir(parents=True)
        (replacements / "ruleset.yaml").write_text("not: [valid runtime")
        with (
            materialize_setting_draft(owner, workspace, **arguments) as broken,
            pytest.raises(CommandRejected, match="runtime admission"),
        ):
            owner.install_package_archive(broken.archive, install_id="broken-world")
        assert len(owner.package_candidates()) == 1
        # Replacing a copied file with a valid change produces a new retained Ruleset identity.
        primary = source.package_lock.entries[0]
        runtime = owner.package_text_file(
            primary.package_id,
            primary.version,
            primary.sha256,
            "runtime/ruleset.yaml",
        )
        assert "expression: 2d6" in runtime
        (replacements / "ruleset.yaml").write_text(
            runtime.replace("expression: 2d6", "expression: 3d6")
        )
        with materialize_setting_draft(owner, workspace, **arguments) as changed:
            assert changed.manifests[0].identity != original_rules
            customized = owner.install_package_archive(changed.archive, install_id="custom-world")
        copied_rules = customized.package_lock.entries[0]
        assert "expression: 3d6" in owner.package_text_file(
            copied_rules.package_id,
            copied_rules.version,
            copied_rules.sha256,
            "runtime/ruleset.yaml",
        )
        assert (
            owner.package_text_file(
                primary.package_id,
                primary.version,
                primary.sha256,
                "runtime/ruleset.yaml",
            )
            == runtime
        )
