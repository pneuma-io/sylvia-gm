"""Completed attempt identities remain fenced across a clean owner restart."""

from dataclasses import replace

import pytest

from sylvia_gm.agent._session_loop import CampaignSessionLoop, ProviderEvent, SessionRunRequest
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import WriterRole
from sylvia_gm.domain.errors import IdempotencyConflict
from sylvia_gm.provider._exceptions import ProviderConnectionError
from tests.test_alpha_f4_generic_sessions import _clockwork_campaign


@pytest.mark.parametrize("ooc", [None, "Finish the story."])
def test_completed_input_is_rejected_without_provider_after_narration_and_reopen(tmp_path, ooc):
    database = tmp_path / "replay.sqlite3"
    request = SessionRunRequest(
        session_id="session-replay", turn_id="turn-replay", attempt_id="attempt-replay",
        message_id="message-replay", user_text="Continue the saved scene.", ooc_text=ooc,
    )

    def provider(_input):
        yield ProviderEvent.talk("The saved scene continues.")

    def forbidden_provider(_input):
        raise AssertionError("Completed replay must not invoke a provider")
        yield  # pragma: no cover

    with AuthorityOwner.open(database, "owner-replay", disconnect_grace_seconds=1.0) as owner:
        _clockwork_campaign(owner, campaign_id="campaign-replay", writer=WriterRole.DM,
                            turn_id="turn-replay", calls=[])
        owner.create_agent_session(campaign_id="campaign-replay", role=WriterRole.DM,
                                   provider_id="synthetic", model_id="synthetic/model",
                                   session_id="session-replay")
        first = CampaignSessionLoop(owner).run(request, provider)
        saved = owner.snapshot("campaign-replay")
        message = next(m for m in owner.session_context(request.session_id).messages
                       if m.message_id == request.message_id)
        assert message.content["canonical_start_sequence"] == 0
        assert saved.events[-1].sequence > 0
        assert first.canonical
        with pytest.raises(IdempotencyConflict, match="already terminal"):
            CampaignSessionLoop(owner).run(request, forbidden_provider)
        assert owner.snapshot("campaign-replay") == saved
        with pytest.raises(IdempotencyConflict):
            CampaignSessionLoop(owner).run(replace(request, user_text="Different input."),
                                           forbidden_provider)
        assert owner.snapshot("campaign-replay") == saved
    with AuthorityOwner.open(database, "owner-reopen", disconnect_grace_seconds=1.0) as reopened:
        with pytest.raises(IdempotencyConflict, match="already terminal"):
            CampaignSessionLoop(reopened).run(request, forbidden_provider)
        assert reopened.snapshot("campaign-replay") == saved


def test_interrupted_input_needs_saved_retry_and_cannot_restart_after_reopen(tmp_path):
    database = tmp_path / "interrupted.sqlite3"
    request = SessionRunRequest(
        session_id="session-interrupted", turn_id="turn-interrupted",
        attempt_id="attempt-interrupted", message_id="message-interrupted",
        user_text="Continue the saved scene.",
    )

    def failing_provider(_input):
        raise ProviderConnectionError("synthetic disconnect")
        yield  # pragma: no cover

    def forbidden_provider(_input):
        raise AssertionError("An interrupted attempt must use the saved Retry path")
        yield  # pragma: no cover

    with AuthorityOwner.open(database, "owner-interrupted", disconnect_grace_seconds=1.0) as owner:
        _clockwork_campaign(owner, campaign_id="campaign-interrupted", writer=WriterRole.DM,
                            turn_id="turn-interrupted", calls=[])
        owner.create_agent_session(campaign_id="campaign-interrupted", role=WriterRole.DM,
                                   provider_id="synthetic", model_id="synthetic/model",
                                   session_id="session-interrupted")
        with pytest.raises(ProviderConnectionError, match="synthetic disconnect"):
            CampaignSessionLoop(owner).run(request, failing_provider)
        saved = owner.snapshot("campaign-interrupted")
        interruption = owner.retryable_interruption(
            "campaign-interrupted", failed_attempt_id="attempt-interrupted",
        )
        assert interruption is not None
    with AuthorityOwner.open(database, "owner-reopen", disconnect_grace_seconds=1.0) as reopened:
        with pytest.raises(IdempotencyConflict, match="already terminal"):
            CampaignSessionLoop(reopened).run(request, forbidden_provider)
        assert reopened.snapshot("campaign-interrupted") == saved
        assert reopened.retryable_interruption(
            "campaign-interrupted", failed_attempt_id="attempt-interrupted",
        ) == interruption
