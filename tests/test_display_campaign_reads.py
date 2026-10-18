"""Display reads preserve current state and fetch only the requested history."""

from pathlib import Path

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import CampaignSnapshot, CanonicalEventType
from sylvia_gm.infrastructure.sqlite_authority import SQLiteAuthorityStore
from tests.test_player_action_availability import _checks
from tests.test_player_actions import _create, _move
from tests.test_result_card_actions import CARD


def test_display_state_skips_history_and_paged_controls_match_complete_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    with AuthorityOwner.open(tmp_path / "display.sqlite3", "owner") as owner:
        _create(owner, card=CARD)
        _move(owner)
        complete = owner.snapshot("campaign")
        projected = owner.player_projection("campaign")
        result = next(e for e in complete.events if e.event_type is CanonicalEventType.TOOL_RESULT)
        assert complete.actions and complete.events and complete.mutations

        def reject_complete(*args, **kwargs):
            raise AssertionError("Ordinary display must not read the complete engine snapshot")

        monkeypatch.setattr(SQLiteAuthorityStore, "snapshot", reject_complete)
        current = owner.display_snapshot("campaign")
        assert not isinstance(current, CampaignSnapshot)
        assert current.state == complete.state
        assert current.participants == complete.participants
        assert current.package_lock == complete.package_lock
        assert current.revision == complete.revision
        assert current.actions == current.events == ()
        assert not hasattr(current, "mutations")
        page = owner.player_projection_page("campaign", after_sequence=result.sequence - 1)
        assert page == projected
        assert owner.player_projection_page("campaign", after_sequence=result.sequence) == ()
        assert owner.player_projection_page("campaign") == ()
        panels = owner.campaign_panels("campaign")
        assert panels["response_action"]["action_id"] == complete.actions[-1].action_id
        assert panels["shot_effects"] == []
        assert _checks(owner, revision=1)["available"] is True
        assert owner.player_action_availability(
            campaign_id="campaign", expected_revision=1, requests=[{
                "source": {"kind": "result_card", "action_id": "move", "button_index": 0},
                "selection": {},
            }],
        )["results"][0]["available"] is True
