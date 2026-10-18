"""Required-party completion checks the actual edited files before saving a selectable Plan."""

import json

import pytest

from sylvia_gm.agent._builder_session import _campaign_source_tool
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.builder_references import capture_launch_references
from sylvia_gm.application.campaign_library_selection import resolve_campaign_library_selection
from sylvia_gm.application.campaign_plan_sources import campaign_plan_sources
from sylvia_gm.interfaces.owner_process import _campaign_creation_catalog
from sylvia_gm.provider._base import ProviderResponse, ToolCall
from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness
from tests.test_builder_library import LibraryProvider
from tests.test_builder_references import open_character_interview
from tests.test_campaign_library_selection import seed_campaign_library
from tests.test_plan_snapshot_publication import write_call


@pytest.mark.parametrize("mistake", ["wrong-id", "missing-hash", "profile-object", None])
def test_required_completion_rejects_unusable_files_and_preserves_the_original(tmp_path, mistake):
    database = tmp_path / "completion.db"
    with AuthorityOwner.open(database, "seed-completion") as owner:
        candidate, setting_id, saved, _ = seed_campaign_library(
            owner, tmp_path, required_party=True,
        )
        sources = campaign_plan_sources(owner, candidate.package_lock, setting_id=setting_id)
        original_guide = sources.read_setting("characters/guide.json")
        guide = json.loads(original_guide)
        identity = _campaign_source_tool(sources).handler(
            {"action": "read", "path": "ruleset/identity.json"}, None,
        )
        exposed = json.loads(identity["content"])["participant_artifacts"][0]
        declaration = owner.package_participant_declarations(candidate.candidate_id)[0]
        assert exposed["schema_id"] == declaration.schema_id
        assert exposed["schema_sha256"] == declaration.schema_sha256
        assert exposed["schema"]["properties"]["callsign"] == {"type": "string"}
        draft = open_character_interview(
            owner, candidate.candidate_id, setting_id, saved.artifact_id,
        )
        parent = owner.session_context(draft.builder_session_id)
        launched = owner.start_interview_builder_chat(
            operation_id="complete-required",
            campaign_id=draft.campaign_id,
            parent_session_id=draft.builder_session_id,
            expected_construction_revision=4,
            purpose="plan",
            visible_answer="Complete required characters in Plan",
            child_session_id="complete-required-builder",
            references=capture_launch_references(
                owner, tmp_path, parent, _campaign_creation_catalog(tmp_path, owner),
            ),
        )
    if mistake == "wrong-id":
        guide["id"] = "internal-actor-id"
    elif mistake == "missing-hash":
        del guide["schema"]["content_sha256"]
    elif mistake == "profile-object":
        guide["authored_profile"] = {"name": "Guide", "background": "Relay veteran"}
    guide["summary"] = "Guide is ready for the relay mission."
    provider = LibraryProvider(responses=[
        ProviderResponse(tool_calls=[
            *[
                ToolCall(
                    id=f"copy-{extension}", name="copy_builder_reference",
                    arguments=json.dumps({
                        "source_path": f"campaign-plan/plan.{extension}",
                        "destination_path": f"campaign-plan-draft/plan.{extension}",
                    }),
                )
                for extension in ("json", "md")
            ],
            write_call(
                "guide", "campaign-plan-draft/setting-files/characters/guide.json",
                json.dumps(guide),
            ),
        ]),
        ProviderResponse(tool_calls=[
            ToolCall(id="publish", name="publish_campaign_plan", arguments="{}"),
        ]),
        ProviderResponse(content="I have checked the publication result."),
    ])
    harness = _OwnerHarness(database, provider)
    try:
        result = harness.request("complete", "submit_session_input", {
            "session_id": launched.child_session_id,
            "attempt_id": "complete-attempt", "message_id": "complete-message",
            "user_text": "", "builder_start": True,
        })
        assert result["interruption"] is None
        receipt = json.loads(next(
            message["content"] for message in provider.calls[-1]["messages"]
            if message.get("role") == "tool" and message.get("tool_call_id") == "publish"
        ))
        if mistake:
            assert receipt["success"] is False
            assert receipt["code"] == "campaign_plan_invalid"
            assert "guide at characters/guide.json" in receipt["message"]
            expected_problem = {
                "wrong-id": "envelope",
                "missing-hash": "schema",
                "profile-object": "authored_profile must be a non-empty text string",
            }[mistake]
            assert expected_problem in receipt["message"]
            assert "library_artifact" not in receipt
            assert len(tuple((tmp_path / "campaign-plans").iterdir())) == 1
        else:
            assert receipt["success"] is True, receipt
    finally:
        harness.close()
    with AuthorityOwner.open(database, "reopen-completion") as owner:
        assert owner.session_context(draft.builder_session_id) == parent
        assert campaign_plan_sources(owner, candidate.package_lock).read_setting(
            "characters/guide.json"
        ) == original_guide
        if not mistake:
            options = resolve_campaign_library_selection(
                owner, tmp_path, candidate_id=candidate.candidate_id, setting_id=setting_id,
                plan_id=receipt["library_artifact"]["id"],
            ).party_options()
            required = next(row for row in options["companions"] if row["id"] == "guide")
            assert required["campaign_ready"] is True
            assert required["summary"] == guide["summary"]
