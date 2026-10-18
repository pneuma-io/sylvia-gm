"""An independent Builder copies an unchanged character into its saved revised Setting."""

from __future__ import annotations

import json
from pathlib import Path

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.campaign_plan_sources import campaign_plan_sources
from sylvia_gm.application.reusable_participants import (
    load_reusable_participant,
    save_reusable_participant,
)
from sylvia_gm.builder._setting_materializer import materialize_setting_draft
from sylvia_gm.provider._base import ProviderResponse, ToolCall
from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness
from tests.test_builder_library import LibraryProvider, launch_library_builder
from tests.test_setting_character_composition import _install_setting


def test_character_save_reports_missing_state_location_instead_of_campaign_ready(tmp_path):
    database, candidate_id = _install_setting(tmp_path)
    provider = LibraryProvider(responses=[
        ProviderResponse(tool_calls=[ToolCall(
            id="save-character", name="publish_participant_artifact",
            arguments=json.dumps({
                "summary": "Signal keeper.", "authored_profile": "A keeper from Glass Harbor.",
                "participant": {"name": "Vela", "rank": 3}, "entity_state": "{}",
                "setting_data": json.dumps({"home": "Glass Harbor"}),
            }),
        )]),
        ProviderResponse(content="The file is saved; campaign readiness needs attention."),
    ])
    harness = _OwnerHarness(database, provider)
    try:
        _, session_id = launch_library_builder(
            harness, candidate_id, "example.glass-harbor", "companion",
        )
        result = harness.request("save", "submit_session_input", {
            "session_id": session_id, "attempt_id": "save", "message_id": "save",
            "user_text": "Save this companion and explain whether it can be used in a campaign.",
        })
        assert result["interruption"] is None
        saved = next(
            json.loads(message["content"]) for message in provider.calls[-1]["messages"]
            if message.get("tool_call_id") == "save-character"
        )
        assert saved["success"] is True, saved
        assert saved["profile_status"] == "authored"
        assert saved["campaign_ready"] is False
        assert "state location" in saved["unavailable_reason"]
        catalog = harness.request("catalog", "campaign_creation_catalog")
        entry = next(item for item in catalog if item.get("id") == saved["id"])
        assert entry["campaign_ready"] is False
        assert entry["unavailable_reason"] == saved["unavailable_reason"]
    finally:
        harness.close()


def test_builder_explicit_character_reuse_retains_the_source(tmp_path: Path) -> None:
    database, original_id = _install_setting(tmp_path)
    with AuthorityOwner.open(database, "reuse-source") as owner:
        original = owner.package_candidate(original_id)
        sources = campaign_plan_sources(owner, original.package_lock)
        assert sources is not None
        (declaration,) = owner.exact_participant_declarations(
            sources.ruleset.package_id, sources.ruleset.version, sources.ruleset.content_sha256
        )
        source = save_reusable_participant(
            tmp_path,
            operation_id="original-vela",
            role="companion",
            summary="Signal keeper.",
            authored_profile="# Vela\n\nA keeper from Glass Harbor.",
            participant={"name": "Vela", "rank": 3},
            entity_state={},
            setting_data={"home": "Glass Harbor"},
            package=original.package_lock.entries[0],
            declaration=declaration,
            campaign_setting=sources.setting,
            setting_character_schema=sources.setting_character_schema,
        )
        with materialize_setting_draft(
            owner,
            tmp_path / "workspace",
            candidate_id=original_id,
            package_id=sources.setting.package_id,
            version="1.1.0",
            title="Glass Harbor Revised",
        ) as revised:
            destination = owner.install_package_archive(
                revised.archive, install_id="revised-setting"
            )
    source_path = tmp_path / "participants" / source.artifact_id
    before = {path.name: path.read_bytes() for path in source_path.iterdir()}
    provider = LibraryProvider(
        responses=[
            ProviderResponse(
                tool_calls=[ToolCall(
                    id="find-vela",
                    name="discover_campaign_content",
                    arguments=json.dumps({"query": "Vela"}),
                )]
            ),
            ProviderResponse(
                tool_calls=[
                    ToolCall(
                        id="reuse-vela",
                        name="reuse_character_copy",
                        arguments=json.dumps(
                            {
                                "artifact_id": source.artifact_id,
                                "expected_content_sha256": source.content_sha256,
                            }
                        ),
                    )
                ]
            ),
            ProviderResponse(content="Vela has a compatible copy in the revised Setting."),
        ]
    )
    harness = _OwnerHarness(database, provider)
    try:
        _draft, session_id = launch_library_builder(
            harness, destination.candidate_id, sources.setting.package_id, "companion"
        )
        result = harness.request(
            "reuse-character",
            "submit_session_input",
            {
                "session_id": session_id,
                "attempt_id": "reuse-attempt",
                "message_id": "reuse-message",
                "user_text": "Reuse an unchanged copy of Vela in this revised Setting.",
            },
        )
        assert result["interruption"] is None
        discovery = json.loads(next(
            message["content"] for message in provider.calls[-1]["messages"]
            if message.get("tool_call_id") == "find-vela"
        ))
        discovered = next(item for item in discovery["items"] if item["id"] == source.artifact_id)
        assert discovered["content_sha256"] == source.content_sha256
        tool_result = json.loads(
            next(
                message["content"]
                for message in provider.calls[-1]["messages"]
                if message.get("tool_call_id") == "reuse-vela"
            )
        )
        assert tool_result["success"] is True, tool_result
        assert tool_result["id"] != source.artifact_id
        assert tool_result["campaign_setting"]["version"] == "1.1.0"
        assert tool_result["source_artifact_id"] == source.artifact_id
        assert {path.name: path.read_bytes() for path in source_path.iterdir()} == before
    finally:
        harness.close()


def test_plan_builder_can_check_a_required_character_without_saving_it(tmp_path: Path) -> None:
    database, candidate_id = _install_setting(tmp_path)
    provider = LibraryProvider(
        responses=[
            ProviderResponse(
                tool_calls=[
                    ToolCall(
                        id="character-requirements",
                        name="get_content_definition",
                        arguments=json.dumps({"name": "character_creation"}),
                    )
                ]
            ),
            ProviderResponse(
                tool_calls=[
                    ToolCall(
                        id="check-required",
                        name="check_participant_draft",
                        arguments=json.dumps(
                            {
                                "role": "companion",
                                "summary": "Signal keeper.",
                                "authored_profile": "# Vela\n\nA keeper from Glass Harbor.",
                                "participant": {"name": "Vela", "rank": 3},
                                "entity_state": "{}",
                                "setting_data": json.dumps({"home": "Glass Harbor"}),
                            }
                        ),
                    )
                ]
            ),
            ProviderResponse(content="The companion's starting data is valid; charge is six."),
        ]
    )
    harness = _OwnerHarness(database, provider)
    try:
        _draft, session_id = launch_library_builder(
            harness, candidate_id, "example.glass-harbor", "plan"
        )
        result = harness.request(
            "check-required-character",
            "submit_session_input",
            {
                "session_id": session_id,
                "attempt_id": "check-attempt",
                "message_id": "check-message",
                "user_text": "Check this Plan's required companion draft before I finish it.",
            },
        )
        assert result["interruption"] is None
        names = {item["function"]["name"] for item in provider.calls[0]["tools"]}
        assert "check_participant_draft" in names
        assert "publish_participant_artifact" not in names
        results = {
            message["tool_call_id"]: json.loads(message["content"])
            for message in provider.calls[-1]["messages"]
            if message.get("tool_call_id")
        }
        assert results["character-requirements"]["definition"]["setting_data"]["required"] == [
            "home"
        ]
        checked = results["check-required"]
        assert checked["success"] is True, checked
        assert checked["complete_entity_state"] == {"charge": 6}
        assert checked["published"] is False
        assert not (tmp_path / "participants").exists()
    finally:
        harness.close()


def test_builder_opens_repairs_and_saves_a_character_without_changing_source(
    tmp_path: Path,
) -> None:
    database, candidate_id = _install_setting(tmp_path)
    with AuthorityOwner.open(database, "repair-source") as owner:
        candidate = owner.package_candidate(candidate_id)
        sources = campaign_plan_sources(owner, candidate.package_lock)
        assert sources is not None
        (declaration,) = owner.exact_participant_declarations(
            sources.ruleset.package_id, sources.ruleset.version, sources.ruleset.content_sha256
        )
        source = save_reusable_participant(
            tmp_path, operation_id="repair-vela", role="companion",
            summary="Signal keeper.", authored_profile="# Vela\n\nA keeper from Glass Harbor.",
            participant={"name": "Vela", "rank": 3}, entity_state={},
            setting_data={"home": "Glass Harbor"}, package=candidate.package_lock.entries[0],
            declaration=declaration, campaign_setting=sources.setting,
            setting_character_schema=sources.setting_character_schema,
        )
    source_path = tmp_path / "participants" / source.artifact_id
    before = {path.name: path.read_bytes() for path in source_path.iterdir()}
    open_args = {
        "artifact_id": source.artifact_id, "expected_content_sha256": source.content_sha256,
        "destination_path": "vela.json",
    }

    def call(name, args, call_id):
        return ProviderResponse(tool_calls=[ToolCall(
            id=call_id, name=name, arguments=json.dumps(args),
        )])

    responses = [
        call("open_character_draft", {**open_args, "expected_content_sha256": "0" * 64}, "stale"),
        call("open_character_draft", {**open_args, "destination_path": "../escape.json"}, "escape"),
        call("open_character_draft", open_args, "open"),
        call("open_character_draft", open_args, "overwrite"),
        ProviderResponse(content="The source is open as a draft."),
    ]
    provider = LibraryProvider(responses=responses)
    harness = _OwnerHarness(database, provider)
    try:
        _draft, session_id = launch_library_builder(
            harness, candidate_id, sources.setting.package_id, "companion"
        )
        result = harness.request("open-character", "submit_session_input", {
            "session_id": session_id, "attempt_id": "open-attempt", "message_id": "open-message",
            "user_text": "Open Vela for a rank correction while preserving all other choices.",
        })
        assert result["interruption"] is None
        results = {
            message["tool_call_id"]: json.loads(message["content"])
            for message in provider.calls[-1]["messages"] if message.get("tool_call_id")
        }
        assert results["stale"]["code"] == "stale_character"
        assert results["escape"]["code"] == "character_draft_path_denied"
        assert results["overwrite"]["error_code"] == "stale_file"
        opened = results["open"]
        assert opened["success"] is True, opened
        assert opened["published"] is False
        workspace_file = tmp_path / "builder-chats" / session_id / opened["path"]
        draft = json.loads(workspace_file.read_text())
        assert draft == {
            "schema_id": declaration.schema_id,
            "summary": source.summary, "authored_profile": source.authored_profile,
            "participant": dict(source.participant), "entity_state": {},
            "setting_data": dict(source.setting_data),
        }
        assert source.entity_state == {"charge": 6}
        assert len(list((tmp_path / "participants").iterdir())) == 1
        corrected = {**draft, "participant": {**draft["participant"], "rank": 4},
                     "entity_state": "{}", "setting_data": json.dumps(draft["setting_data"])}
        responses.extend([
            call("edit_file", {"path": "vela.json", "old_text": '"rank": 3',
                               "new_text": '"rank": 4',
                               "expected_version": opened["version"]}, "edit"),
            call("check_participant_draft", corrected, "check"),
            call("publish_participant_artifact", corrected, "save"),
            ProviderResponse(content="The corrected copy has been saved."),
        ])
        result = harness.request("repair-character", "submit_session_input", {
            "session_id": session_id, "attempt_id": "repair-attempt",
            "message_id": "repair-message", "user_text": "Correct rank to four and save a copy.",
        })
        assert result["interruption"] is None
        results = {
            message["tool_call_id"]: json.loads(message["content"])
            for message in provider.calls[-1]["messages"] if message.get("tool_call_id")
        }
        assert results["edit"]["success"] is True, results["edit"]
        assert results["check"]["success"] is True, results["check"]
        assert results["save"]["success"] is True, results["save"]
        saved = load_reusable_participant(
            tmp_path, results["save"]["id"], package=candidate.package_lock.entries[0],
            declaration=declaration, campaign_setting=sources.setting,
            setting_character_schema=sources.setting_character_schema,
        )
        assert saved.participant == {"name": "Vela", "rank": 4}
        assert saved.entity_state == {"charge": 8}
        assert saved.authored_profile == source.authored_profile
        assert saved.setting_data == source.setting_data
        assert saved.summary == source.summary
        assert saved.artifact_id != source.artifact_id
        assert {path.name: path.read_bytes() for path in source_path.iterdir()} == before
    finally:
        harness.close()
