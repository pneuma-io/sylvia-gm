"""Owner-transport coverage for unbound sources and independently saved adaptations."""

import hashlib
import json

import pytest

from sylvia_gm.agent._builder_session import _provider_history
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.builder_references import launch_references, reference_reader
from sylvia_gm.application.character_profiles import load_character_profile, save_character_profile
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.provider._base import ProviderResponse, ToolCall
from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness
from tests.test_builder_library import LibraryProvider
from tests.test_campaign_library_selection import seed_campaign_library
from tests.test_character_profiles import source
from sylvia_gm.domain._content_schema_character_profile_v2 import EXAMPLE as RICH_PROFILE

def source_version(version):
    return source() if version == 1 else canonical_json_bytes(RICH_PROFILE)


def call(name, arguments, identity=None):
    return ProviderResponse(
        tool_calls=[
            ToolCall(
                id=identity or name,
                name=name,
                arguments=json.dumps(arguments),
            )
        ]
    )


def submit(harness, session_id, identity="work"):
    return harness.request(
        identity,
        "submit_session_input",
        {
            "session_id": session_id,
            "attempt_id": identity,
            "message_id": identity,
            "user_text": "Save the requested character, retaining its source.",
        },
    )


def receipts(provider):
    return {
        message["tool_call_id"]: json.loads(message["content"])
        for message in provider.calls[-1]["messages"]
        if message.get("role") == "tool"
    }


@pytest.mark.parametrize("version", [1, 2])
def test_unbound_builder_save_discovery_and_reopen(tmp_path, version):
    data = source_version(version)
    provider = LibraryProvider(
        responses=[
            call("get_content_definition", {"name": "character_profile" if version == 1 else "character_profile_v2"}),
            call("write_file", {"path": "vela.json", "content": data.decode()}),
            call("check_character_profile_draft", {"path": "vela.json"}),
            call(
                "publish_character_profile",
                {
                    "path": "vela.json",
                    "expected_version": hashlib.sha256(data).hexdigest(),
                },
            ),
            call("discover_campaign_content", {"query": "Vela"}),
            ProviderResponse(content="The general source is saved, ready to adapt."),
        ]
    )
    database = tmp_path / "owner.db"
    harness = _OwnerHarness(database, provider)
    try:
        chat = harness.request(
            "new",
            "create_builder_chat",
            {
                "operation_id": "new",
                "provider_id": "test.transport",
                "model_id": "test.transport/model0",
            },
        )
        session_id = chat["session"]["session_id"]
        result = submit(harness, session_id)
        assert result["interruption"] is None, result
        outcomes = receipts(provider)
        assert outcomes["check_character_profile_draft"]["valid"]
        saved = outcomes["publish_character_profile"]
        assert saved["success"] and saved["campaign_ready"] is False
        assert any(
            item["id"] == saved["id"] for item in outcomes["discover_campaign_content"]["items"]
        )
    finally:
        harness.close()
    restored = _OwnerHarness(database, None)
    try:
        catalog = restored.request("catalog", "campaign_creation_catalog")
        assert (
            next(item for item in catalog if item["id"] == saved["id"])["kind"]
            == "character_profile"
        )
        assert (
            dict(load_character_profile(tmp_path, saved["id"]).profile)
            == json.loads(data)
        )
    finally:
        restored.close()


def prepare(harness, candidate, setting_id, plan):
    draft = harness.request(
        "new",
        "create_guided_campaign_draft",
        {
            "operation_id": "interview",
            "display_name": "Synthetic adaptation",
            "provider_id": "test.transport",
            "model_id": "test.transport/model0",
        },
    )
    for revision, (step, answer) in enumerate(
        (
            ("ruleset", {"candidate_id": candidate.candidate_id}),
            ("setting", {"setting_id": setting_id, "candidate_id": candidate.candidate_id}),
            ("plan", {"plan_id": plan.artifact_id}),
            ("name", {"display_name": "Synthetic adaptation"}),
        )
    ):
        harness.request(
            step,
            "advance_guided_campaign_draft",
            {
                "operation_id": step,
                "campaign_id": draft["campaign_id"],
                "builder_session_id": draft["builder_session_id"],
                "expected_construction_revision": revision,
                "current_step": step,
                "visible_answer": step,
                "answer": answer,
            },
        )
    return draft


@pytest.mark.parametrize("version", [1, 2])
def test_pinned_adaptation_replays_reopens_and_preserves_source_and_repair(tmp_path, version):
    database = tmp_path / "adapt.db"
    with AuthorityOwner.open(database, "seed") as owner:
        candidate, setting, plan, _characters = seed_campaign_library(owner, tmp_path)
    profile = save_character_profile(tmp_path, operation_id="source", source=source_version(version))
    before = canonical_json_bytes(profile.profile)
    adapted_fields = {
        "summary": "A relay archivist.",
        "authored_profile": "Vela now keeps the northern relay.",
        "participant": {"name": "Vela", "callsign": "Northstar"},
        "entity_state": json.dumps({"stress": 0}),
    }
    provider = LibraryProvider(
        responses=[
            call(
                "copy_builder_reference",
                {
                    "source_path": "character-profile/profile.json",
                    "destination_path": "source.json",
                },
            ),
            call("check_participant_draft", {**adapted_fields, "entity_state": "{}"}, "incomplete"),
            call("check_participant_draft", adapted_fields),
            call("publish_participant_artifact", adapted_fields),
            ProviderResponse(content="The adaptation is saved."),
        ]
    )
    harness = _OwnerHarness(database, provider)
    try:
        draft = prepare(harness, candidate, setting, plan)
        launch = {
            "operation_id": "adapt",
            "campaign_id": draft["campaign_id"],
            "parent_session_id": draft["builder_session_id"],
            "expected_construction_revision": 4,
            "purpose": "companion",
            "visible_answer": "Adapt Vela as Companion",
            "character_profile_id": profile.artifact_id,
            "character_profile_sha256": profile.content_sha256,
        }
        child = harness.request("launch", "start_guided_creation_subflow", launch)
        assert harness.request("replay", "start_guided_creation_subflow", launch) == child
        with pytest.raises(AssertionError, match="stale_character_profile"):
            harness.request(
                "conflict",
                "start_guided_creation_subflow",
                {
                    **launch,
                    "character_profile_sha256": "0" * 64,
                },
            )
        second = harness.request(
            "second",
            "start_guided_creation_subflow",
            {
                **launch,
                "operation_id": "second-adaptation",
            },
        )
        for changes in (
            {"purpose": "player_character"},
            {"character_profile_sha256": None},
        ):
            with pytest.raises(AssertionError, match="character_profile_invalid"):
                harness.request("invalid-profile", "start_guided_creation_subflow", {
                    **launch, "operation_id": "invalid-profile", **changes,
                })
        result = submit(harness, child["child_session_id"])
        assert result["interruption"] is None, result
        outcomes = receipts(provider)
        assert outcomes["copy_builder_reference"]["success"], outcomes
        assert not outcomes["incomplete"]["success"]
        assert outcomes["check_participant_draft"]["success"]
        saved = outcomes["publish_participant_artifact"]
        assert saved["success"] and saved["source_profile"] == profile.provenance(), saved
        assert (
            canonical_json_bytes(load_character_profile(tmp_path, profile.artifact_id).profile)
            == before
        )
        options = harness.request(
            "options",
            "campaign_party_options",
            {
                "ruleset_selection": candidate.candidate_id,
                "setting_id": setting,
                "plan_id": plan.artifact_id,
            },
        )
        assert any(item["id"] == saved["id"] for item in options["companions"])
        assert all(item["id"] != profile.artifact_id for item in options["companions"])
    finally:
        harness.close()
    # A source leaving the library must not invalidate its already-captured adaptation context.
    (tmp_path / "character-profiles" / profile.artifact_id).rename(tmp_path / "offline-source")
    second_provider = LibraryProvider(
        responses=[
            call("publish_participant_artifact", {**adapted_fields, "summary": "A second copy."}),
            ProviderResponse(content="Another independent adaptation is saved."),
        ]
    )
    with AuthorityOwner.open(database, "inspect") as owner:
        context = owner.session_context(child["child_session_id"])
        assert (
            launch_references(context)["character_profile"]["content_sha256"]
            == profile.content_sha256
        )
        assert json.loads(
            reference_reader(owner, context).read("character-profile/profile.json")
        ) == dict(profile.profile)
        history = _provider_history(context)
        launch_text = next(
            item["content"]
            for item in history
            if "builder_launch_context" in item.get("content", "")
        )
        reference_text = (profile.profile["authored_profile"] if version == 1
                          else profile.profile["biography"]["description"])
        assert reference_text not in launch_text
    reopened = _OwnerHarness(database, second_provider)
    try:
        assert reopened.request("replay-offline", "start_guided_creation_subflow", launch) == child
        submit(reopened, second["child_session_id"], "second-save")
        sibling = receipts(second_provider)["publish_participant_artifact"]
        assert sibling["success"] and sibling["id"] != saved["id"], sibling
        assert sibling["source_profile"] == saved["source_profile"]
        repair_launch = {
            key: value for key, value in launch.items()
            if key not in {"character_profile_id", "character_profile_sha256"}
        }
        repair = reopened.request("repair-launch", "start_guided_creation_subflow", {
            **repair_launch, "operation_id": "repair-launch",
        })
    finally:
        reopened.close()
    opening = LibraryProvider(responses=[
        call("open_character_draft", {
            "artifact_id": saved["id"], "expected_content_sha256": saved["content_sha256"],
            "destination_path": "repair.json",
        }),
        ProviderResponse(content="The saved adaptation is open."),
    ])
    repair_owner = _OwnerHarness(database, opening)
    try:
        submit(repair_owner, repair["child_session_id"], "open-repair")
        assert receipts(opening)["open_character_draft"]["success"]
    finally:
        repair_owner.close()
    correcting = LibraryProvider(responses=[
        call("publish_participant_artifact", {**adapted_fields, "summary": "Corrected copy."}),
        ProviderResponse(content="The repaired adaptation is saved."),
    ])
    repair_owner = _OwnerHarness(database, correcting)
    try:
        submit(repair_owner, repair["child_session_id"], "save-repair")
        corrected = receipts(correcting)["publish_participant_artifact"]
        assert corrected["success"], corrected
        assert corrected["source_profile"] == saved["source_profile"]
        assert corrected["id"] != saved["id"]
    finally:
        repair_owner.close()
