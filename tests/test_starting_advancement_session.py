"""Real owner/session startup permits choice questions and rejects premature story progress."""
import json

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.package_library import canonical_json_bytes
from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness
from tests.test_fc_campaign_journey import _start_campaign
from tests.test_fc_campaign_progress import _ProgressProvider, _write
from tests.qa_attached_request import request_attached


class _PreparationProvider(_ProgressProvider):
    def __init__(self, steps):
        super().__init__(steps)
        self.observed = []

    async def generate_stream(self, messages, tools=None, **kwargs):
        self.observed.append({"messages": messages, "tools": tools or []})
        async for chunk in super().generate_stream(messages, tools=tools, **kwargs):
            yield chunk


def test_base_card_interview_queues_before_story_and_allows_only_ooc_choice_questions(tmp_path):
    database = tmp_path / "campaign.db"
    campaign, session, turn = _start_campaign(database, with_companion=True, character_level=1)
    with AuthorityOwner.open(database, "before") as owner:
        before = owner.snapshot(campaign)
        assert all(p.data["level"] == 1 for p in before.participants)
        preparation = owner.campaign_preparation_boundary(campaign)
        assert preparation is not None and len(preparation.preparation["events"]) == 2
        basis = owner.campaign_construction_basis(campaign)
        selected = json.loads(canonical_json_bytes(basis.canonical_input))
        retry_params = {
            "operation_id": "journey-ready", "campaign_id": campaign,
            "builder_session_id": basis.builder_session_id, "display_name": basis.display_name,
            "candidate_id": "sylvia.srd-lite.bg3", "setting_id": selected["setting_id"],
            "plan_id": selected["plan_id"],
            "player_character_id": selected["player_character"]["archetype_id"],
            "companion_ids": selected["companion_ids"], "premise": "",
            "construction_intent": selected["construction_intent"],
        }
    provider = _PreparationProvider([
        _write("The adventure begins in Act 1 now.", act=1),
        _write("Before we begin, choose the class and HP method for the queued level-ups. Your original cards remain unchanged.", ooc=True),
    ])
    harness = _OwnerHarness(database, provider)
    try:
        # An interrupted/reopened Ready request must not reseed events or rewrite the party.
        ready_before = harness.request("before-ready-replay", "snapshot", {"campaign_id": campaign})
        for index in range(2):
            replay = harness.request(f"ready-replay-{index}", "start_guided_campaign", retry_params)
            assert replay["campaign_id"] == campaign
            assert harness.request(f"after-ready-replay-{index}", "snapshot", {"campaign_id": campaign}) == ready_before
        reply = request_attached(harness, "prepare", "submit_session_input", {
            "session_id": session, "turn_id": turn, "attempt_id": "prepare",
            "message_id": "prepare", "expected_revision": before.revision,
            "user_text": "Prepare this party for the Plan's declared starting level.",
        }, campaign_id=campaign, attempt_id="prepare")
        assert reply["interruption"] is None, reply
    finally:
        harness.close()
    assert provider.failure is None and provider.sent == 2
    assert any(row.get("code") == "campaign_preparation_pending" and row.get("state_changed") is False
               for row in provider.receipts), provider.receipts
    assert any("SYLVIA_GM_CAMPAIGN_PREPARATION" in str(message.get("content", ""))
               for observed in provider.observed for message in observed["messages"])
    for observed in provider.observed:
        names = [tool["function"]["name"] for tool in observed["tools"]]
        assert any(name.endswith("_level_up_srd_character") for name in names)
        assert not any(name.endswith("_resolve_combat_attack") for name in names)
    with AuthorityOwner.open(database, "after") as owner:
        after = owner.snapshot(campaign)
        assert after.state == before.state and after.participants == before.participants
        assert not after.continuity.plan_progress
        assert after.continuity.current_scene == before.continuity.current_scene
        page = owner.session_history_page(session)
        assert any("Before we begin" in str(message.content) for message in page.messages)
        assert owner.campaign_preparation_boundary(campaign) is not None
