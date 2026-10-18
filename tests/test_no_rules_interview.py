"""The installed no-rules route must reach a saved narrative campaign without a package."""

from pathlib import Path

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import WriterRole
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.provider._base import FakeProvider, ModelInfo
from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness


@pytest.mark.parametrize("requests", [
    "I am a passenger waiting for a night train.",
    "A detailed journey preference. 🦊\n" * 2200,
], ids=["short", "long-unicode"])
def test_no_rules_interview_starts_without_package_content_and_survives_reopen(
    tmp_path: Path, requests: str,
) -> None:
    provider = FakeProvider()
    provider.model_info = ModelInfo(  # type: ignore[attr-defined]
        id="test.transport/model0",
        name="Narrative test",
        provider_name="Test",
        provider_key="test.transport",
        provider_mode="test",
        context_window=65_536,
        max_output_tokens=4_096,
    )
    database = tmp_path / "no-rules.sqlite3"
    harness = _OwnerHarness(database, provider)
    try:
        draft = harness.request("draft", "create_guided_campaign_draft", {
            "operation_id": "no-rules-draft",
            "display_name": "Narrative interview",
            "provider_id": "test.transport",
            "model_id": "test.transport/model0",
        })
        campaign_id = draft["campaign_id"]
        session_id = draft["builder_session_id"]
        answers = (
            ("ruleset", {"candidate_id": None}, "name"),
            ("name", {"display_name": "The Night Train"}, "boundaries_preferences"),
            ("boundaries_preferences", {"boundaries": "No horror", "preferences": "Quiet"},
             "specific_requests"),
            ("specific_requests", {"no_additional_requests": False,
                                   "requests": requests},
             "review"),
        )
        for revision, (step, answer, next_step) in enumerate(answers):
            result = harness.request(f"answer-{step}", "advance_guided_campaign_draft", {
                "operation_id": f"no-rules-answer-{step}",
                "campaign_id": campaign_id,
                "builder_session_id": session_id,
                "expected_construction_revision": revision,
                "current_step": step,
                "visible_answer": str(answer),
                "answer": answer,
            })
            assert result["current_step"] == next_step
        premise = (
            "Hard boundaries: No horror\n"
            f"Specific requests: {requests.strip()}"
        )
        start = {
            "operation_id": "start-narrative-interview",
            "campaign_id": campaign_id,
            "builder_session_id": session_id,
            "display_name": "The Night Train",
            "candidate_id": None,
            "setting_id": None,
            "plan_id": None,
            "player_character_id": None,
            "companion_ids": [],
            "premise": premise,
        }
        begun = harness.request("start", "start_guided_campaign", start)
        plan_path = tmp_path / "campaigns" / campaign_id / "campaign-plan.md"
        plan_text = plan_path.read_text(encoding="utf-8")
        assert "The Night Train" in plan_text
        assert premise in plan_text
        assert not plan_path.with_suffix(".json").exists()
        assert harness.request("repeat-start", "start_guided_campaign", start) == begun
        assert plan_path.read_text(encoding="utf-8") == plan_text
        finalized = harness.request("finalize", "finalize_campaign_construction", {
            "campaign_id": campaign_id,
            "dm_provider_id": "test.transport",
            "dm_model_id": "test.transport/model0",
        })
        assert finalized["handoff"]["dm_session_id"]
    finally:
        harness.close()
    with AuthorityOwner.open(database, "owner-no-rules-reopen") as owner:
        from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics

        client = StatelessMcpMechanics(owner)
        parts = []
        path = "readable/page/0"
        while path is not None:
            reference = client.read_reference(campaign_id, source="campaign_plan", path=path)
            assert len(reference["content"]) <= 16000
            assert reference["selection"]["offset"] == len("".join(parts))
            parts.append(reference["content"])
            path = reference["selection"]["next_path"]
        assert "".join(parts) == plan_text
        assert requests.strip() in "".join(parts)
        assert reference["selection"]["total_characters"] == len(plan_text)
        for invalid_path in ("readable/page/-1", "readable/page/9999999999"):
            with pytest.raises(CommandRejected):
                client.read_reference(campaign_id, source="campaign_plan", path=invalid_path)
        if len(requests) > 16_000:
            assert len(plan_text.encode("utf-8")) > 65_536
            with pytest.raises(CommandRejected):
                client.read_reference(campaign_id, source="campaign_plan", path="readable")
        snapshot = owner.snapshot(campaign_id)
        assert snapshot.writer is WriterRole.DM
        assert not snapshot.participants
        assert not snapshot.state
        assert owner.catalog(campaign_id) == ()
        assert owner.current_package_lock(campaign_id).package_lock.entries == ()
        assert owner.package_surfaces(campaign_id).ui_surface_ids == ()
