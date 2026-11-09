"""A starter follows the existing Builder workspace, validation and copy publication path."""

import json

import pytest

from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.application.compatible_character_copy import authored_character_state
from sylvia_gm.builder._workspace_files import content_version
from sylvia_gm.domain.authority import WriterRole
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.domain.session import SessionMessageKind
from sylvia_gm.provider._base import ProviderResponse, ToolCall
from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness
from tests.test_builder_library import LibraryProvider
from tests.test_builder_references import open_character_interview
from tests.test_bundled_starter_characters import authority, selection


@pytest.mark.parametrize("file_commands", [False, True])
def test_builtin_starter_opens_in_builder_and_publishes_an_independent_copy(
    tmp_path, file_commands,
):
    database = tmp_path / "builder.db"
    with AuthorityOwner.open(database, "seed") as owner:
        selected = selection(owner, tmp_path)
        _, declaration = authority(owner, selected)
        source = selected.participants[0]
        original = canonical_json_bytes(source.rift_source())
        interview = open_character_interview(
            owner,
            selected.candidate.candidate_id,
            selected.sources.setting.package_id,
            selected.plan.artifact_id,
        )

    def call(name, arguments, identity):
        return ProviderResponse(
            tool_calls=[
                ToolCall(
                    id=identity,
                    name=name,
                    arguments=json.dumps(arguments),
                )
            ]
        )

    responses = [
        call(
            "open_character_draft",
            {
                "artifact_id": source.artifact_id,
                "expected_content_sha256": source.content_sha256,
                "destination_path": "my-starter.json",
            },
            "open",
        ),
        ProviderResponse(content="The independent draft is ready. What would you like to change?"),
    ]
    provider = LibraryProvider(responses=responses)
    harness = _OwnerHarness(database, provider)
    try:
        launch_params = {
            "operation_id": "edit-starter",
            "campaign_id": interview.campaign_id,
            "parent_session_id": interview.builder_session_id,
            "expected_construction_revision": 4,
            "purpose": "player_character",
            "visible_answer": f"Edit a copy of {source.name}",
            "character_artifact_id": source.artifact_id,
            "character_artifact_sha256": source.content_sha256,
        }
        harness.send(
            "stale-launch",
            {
                "method": "start_guided_creation_subflow",
                "params": {
                    **launch_params,
                    "character_artifact_sha256": "0" * 64,
                },
            },
        )
        assert harness.read()["error"]["code"] == "stale_character"
        launched = harness.request("launch", "start_guided_creation_subflow", launch_params)
        assert (
            harness.request("retry-launch", "start_guided_creation_subflow", launch_params)
            == launched
        )
        session_id = launched["child_session_id"]
        context = harness.request("context", "session_context", {"session_id": session_id})
        launch_context = next(
            m["content"]
            for m in context["messages"]
            if m["content"].get("kind") == "builder_launch_context"
        )
        assert launch_context["references"]["character_copy"] == {
            "artifact_id": source.artifact_id,
            "content_sha256": source.content_sha256,
            "name": source.name,
        }
        harness.send(
            "retarget-launch",
            {
                "method": "start_guided_creation_subflow",
                "params": {
                    **launch_params,
                    "character_artifact_sha256": "0" * 64,
                },
            },
        )
        assert harness.read()["error"]["code"] == "stale_character"
        result = harness.request(
            "open",
            "submit_session_input",
            {
                "session_id": session_id,
                "attempt_id": "open",
                "message_id": "open",
                "user_text": "Edit an independent copy of this saved starter.",
            },
        )
        assert result["interruption"] is None
        opened = json.loads(
            next(
                message["content"]
                for message in provider.calls[-1]["messages"]
                if message.get("tool_call_id") == "open"
            )
        )
        assert opened["success"] is True and opened["published"] is False
        draft_path = tmp_path / "builder-chats" / session_id / opened["path"]
        draft = json.loads(draft_path.read_text())
        assert draft["participant"] == source.participant
        assert draft["entity_state"] == authored_character_state(source.entity_state, declaration)
        revised = {
            **draft,
            "participant": {**draft["participant"], "name": "Mara Rain"},
            "authored_profile": "Mara is softly spoken and loves riddles.",
            "entity_state": json.dumps(draft["entity_state"]),
            "setting_data": json.dumps(draft["setting_data"]),
        }
        command_fields = revised
        check_name, save_name = "check_participant_draft", "publish_participant_artifact"
        if file_commands:
            changed = draft_path.read_bytes().replace(b'"Mara Flint"', b'"Mara Rain"', 1)
            command_fields = {"path": opened["path"], "expected_version": content_version(changed)}
            check_name = "check_participant_draft_file"
            save_name = "publish_participant_artifact_file"
        responses.extend(
            [
                call(
                    "edit_file",
                    {
                        "path": opened["path"],
                        "old_text": '"Mara Flint"',
                        "new_text": '"Mara Rain"',
                        "expected_version": opened["version"],
                    },
                    "edit",
                ),
                call(check_name, command_fields, "check"),
                call(save_name, command_fields, "save"),
                ProviderResponse(content="Mara Rain is saved as a separate character."),
            ]
        )
        result = harness.request(
            "save",
            "submit_session_input",
            {
                "session_id": session_id,
                "attempt_id": "save",
                "message_id": "save",
                "user_text": "Change the name and personality, then save my copy.",
            },
        )
        assert result["interruption"] is None
        results = {
            message["tool_call_id"]: json.loads(message["content"])
            for message in provider.calls[-1]["messages"]
            if message.get("role") == "tool"
        }
        assert results["edit"]["success"] is True
        assert results["check"]["success"] is True
        saved = results["save"]
        assert saved["success"] is True and saved["campaign_ready"] is True
        assert saved["id"] != source.artifact_id and not saved.get("builtin_starter")
        assert json.loads(draft_path.read_text())["participant"]["name"] == "Mara Rain"
    finally:
        harness.close()
    with AuthorityOwner.open(database, "reopened") as owner:
        refreshed = selection(owner, tmp_path)
        assert canonical_json_bytes(refreshed.participants[0].rift_source()) == original
        copy = next(item for item in refreshed.participants if item.artifact_id == saved["id"])
        assert copy.participant["name"] == "Mara Rain"
        assert copy.authored_profile == (
            draft["authored_profile"] if file_commands else revised["authored_profile"]
        )


def test_campaign_inventory_edit_cannot_change_starter_or_another_campaign(tmp_path):
    from sylvia_gm.application.campaign_library_selection import materialize_library_party

    with AuthorityOwner.open(tmp_path / "independent.db", "independent") as owner:
        selected = selection(owner, tmp_path)
        source = selected.participants[0]
        original = canonical_json_bytes(source.rift_source())
        players = {}
        for identity in ("first", "second"):
            participants, initial, _ = materialize_library_party(
                owner,
                selected,
                operation_id=identity,
                player_character_id=source.artifact_id,
                companion_ids=(),
            )
            owner.create_campaign(
                CampaignCreate(
                    campaign_id=identity,
                    display_name=identity,
                    writer=WriterRole.DM,
                    package_lock=selected.candidate.package_lock,
                    turn_id="play-" + identity,
                    participants=participants,
                    initial_state=initial,
                )
            )
            players[identity] = participants[0].participant_id
        other = owner.snapshot("second")
        session = owner.create_agent_session(
            campaign_id="first", role=WriterRole.DM, provider_id="scripted", model_id="scripted"
        )
        owner.append_agent_message(
            session_id=session.session_id,
            message_id="accept-token",
            kind=SessionMessageKind.USER,
            content={"text": "I keep the wooden token."},
        )
        before = owner.snapshot("first")
        inventory = list(source.entity_state["inventory"]) + ["wooden_token"]
        owner.update_campaign_file(
            session_id=session.session_id,
            update_id="keep-token",
            basis_message_ids=("accept-token",),
            updates=[
                {
                    "operation": "update_character",
                    "expected_revision": before.revision,
                    "participant_id": players["first"],
                    "fields": {"inventory": inventory},
                }
            ],
        )
        changed = owner.snapshot("first").state["sylvia.srd-lite.bg3.entities"][players["first"]]
        assert "wooden_token" in changed["inventory"]
        assert owner.snapshot("second") == other
        assert (
            canonical_json_bytes(selection(owner, tmp_path).participants[0].rift_source())
            == original
        )
