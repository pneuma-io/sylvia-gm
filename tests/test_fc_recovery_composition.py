"""Continue one normally admitted Lite campaign across interrupted publication and recovery.

External GM decisions and compaction prose are scripted. All admission, mechanics,
Campaign File validation, retry checkpoints, export and recovery use the production owner.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.provider._base import ProviderResponse
from tests.qa_attached_request import request_attached
from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness
from tests.test_builder_library import LibraryProvider
from tests.test_fc_campaign_journey import SRD, _CombatProvider
from tests.test_fc_campaign_progress import _play, _ProgressProvider, _read_act, _write
from tests.test_fc_party_combat import (
    test_normal_party_combat_retains_all_actors_attacks_recovery_and_ending as run_party,
)


def _checkpoint(owner, campaign, session):
    return (
        owner.snapshot(campaign),
        owner.session_context(session),
        owner.session_history_page(session),
        owner.player_projection(campaign),
    )


def _assert_recovered(owner, campaign, session, saved):
    snapshot, context, page, projection = saved
    restored = owner.snapshot(campaign)
    # Recovery records its own receipt; it must preserve the played campaign exactly.
    assert restored.state == snapshot.state, "Recovered HP, resources or rewards changed"
    assert restored.actions == snapshot.actions, "Recovered action receipts or rolls changed"
    assert restored.participants == snapshot.participants, "Recovered actors changed"
    assert restored.continuity == snapshot.continuity, "Recovered progress changed"
    assert restored.package_lock == snapshot.package_lock
    assert restored.revision == snapshot.revision
    assert owner.session_context(session) == context, "Recovered transcript/checkpoints changed"
    assert owner.session_history_page(session) == page, "Recovered closing events changed"
    assert owner.player_projection(campaign) == projection


def _recover(database, tmp_path, campaign, session, label):
    archive = tmp_path / f"{label}.sylvia-gm-campaign"
    with AuthorityOwner.open(database, f"export-{label}") as owner:
        saved = _checkpoint(owner, campaign, session)
        owner.export_campaign(operation_id=f"export-{label}", campaign_id=campaign, target=archive)
    destination = tmp_path / f"{label}.sqlite3"
    with AuthorityOwner.open(destination, f"recover-{label}") as owner:
        receipt = owner.recover_campaign(recovery_id=f"recover-{label}", source=archive)
        assert receipt.campaign_id == campaign
        _assert_recovered(owner, campaign, session, saved)
    # A second process/owner open verifies persisted recovery, rather than cached objects.
    with AuthorityOwner.open(destination, f"reopen-{label}") as owner:
        _assert_recovered(owner, campaign, session, saved)
    return destination


@pytest.mark.parametrize("boundary", ["before-transition", "after-transition"])
def test_same_campaign_combat_interruption_compaction_export_and_later_play(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str,
):
    # Reuse the unchanged normal Interview/Builder/party combat journey and all its
    # roll-derived HP, action-order and resource assertions. Nothing is inserted/reset.
    run_party(tmp_path, surge_before_attack=boundary == "before-transition")
    _continue_campaign(tmp_path, monkeypatch, boundary)


def _continue_campaign(tmp_path, monkeypatch, boundary):
    database = tmp_path / "party.sqlite3"
    with AuthorityOwner.open(database, "composed-identities") as owner:
        (campaign_row,) = owner.campaign_index()
        campaign = campaign_row.campaign_id
        session = campaign_row.session_id
        assert session is not None
        initial = owner.snapshot(campaign)
        player = next(p.participant_id for p in initial.participants
                      if p.data["party_role"] == "player_character")
        assert not initial.state[f"{SRD}.combat"]["encounter_active"]
        assert initial.state[f"{SRD}.entities"][player]["resources"]["action_surge"] == 0

    rescue = "The practice is over. Dev, Oss and the medicine reach safe ground; Keth withdrew."
    _play(database, campaign, session, 20, [
        _read_act(1), _write(rescue, resolved=("objective-1", "act-1"), act=1),
    ])
    if boundary == "before-transition":
        database = _recover(database, tmp_path, campaign, session, "before-transition")
    _play(database, campaign, session, 21, [
        _read_act(2), _write("The same party starts the ridge escort with Oss.", act=2),
    ])
    if boundary == "after-transition":
        database = _recover(database, tmp_path, campaign, session, "after-transition")

    # Explicit compaction uses the ordinary /compact command and real saved history.
    provider = LibraryProvider(responses=[ProviderResponse(
        content="Act 1 is earned; escort remains. Action Surge is spent. Preserve the real state.",
    )] * 30)
    with AuthorityOwner.open(database, "compact-basis") as owner:
        compact_turn = owner.current_open_turn(campaign) or "composed-compact-turn"
        compact_revision = owner.snapshot(campaign).revision
    harness = _OwnerHarness(database, provider)
    try:
        result = harness.request("compact", "submit_session_input", {
            "session_id": session, "user_text": "/compact",
            "attempt_id": "composed-compact", "message_id": "composed-compact",
            "turn_id": compact_turn, "expected_revision": compact_revision,
        })
        assert result["status"] == "context_compacted"
    finally:
        harness.close()
    with AuthorityOwner.open(database, "after-compact") as owner:
        compacted = owner.snapshot(campaign)
        assert compacted.state == initial.state
        assert compacted.actions == initial.actions
        progress = {row["plan_item_id"]: row["status"]
                    for row in compacted.continuity.plan_progress}
        assert progress == {"act-1": "resolved", "objective-1": "resolved"}
        assert owner.session_history_page(session).campaign_ending is None
        revision = compacted.revision
        turn = owner.current_open_turn(campaign) or "composed-later-turn"

    # A second actual encounter consumes the same saved actors/resources in Act 2.
    provider = _CombatProvider(player, stop_after_dodge=True)
    harness = _OwnerHarness(database, provider)
    try:
        result = request_attached(harness, "later-encounter", "submit_session_input", {
            "session_id": session, "attempt_id": "later-encounter",
            "message_id": "later-encounter", "expected_revision": revision,
            "turn_id": turn, "user_text": "Practice with Dera. Dodge, then agree to stop.",
        }, campaign_id=campaign, attempt_id="later-encounter")
        assert result["interruption"] is None, result
    finally:
        harness.close()
    with AuthorityOwner.open(database, "later-outcome") as owner:
        before_close = owner.snapshot(campaign)
        assert not before_close.state[f"{SRD}.combat"]["encounter_active"]
        assert not before_close.state[f"{SRD}.entities"][player]["dodging"]
        assert before_close.state[f"{SRD}.entities"][player]["resources"] == (
            initial.state[f"{SRD}.entities"][player]["resources"]
        )
        assert before_close.actions[:len(initial.actions)] == initial.actions
        turn = owner.current_open_turn(campaign) or "composed-closing-turn"

    # Interrupt after the final Campaign File checkpoint but before public narration.
    closing = _write(
        "The same escort reaches the clinic; Guild settlement and closing thanks are delivered.",
        resolved=("objective-2", "act-2", "ending:adventure-settled"), act=2,
    )
    # The Plan's 40 gp recovery plus 20 gp medicine payment is a real carried
    # reward. It must survive retry/recovery once, alongside the already spent uses.
    reward_inventory = list(before_close.state[f"{SRD}.entities"][player]["inventory"]) + ["60_gp"]
    closing[1]["character_updates"] = [{
        "participant_id": player, "expected_revision": before_close.revision,
        "fields": {"inventory": reward_inventory},
    }]
    provider = _ProgressProvider([_read_act(2), closing])
    complete = AuthorityOwner._complete_narration_attempt

    def interrupt_publication(owner, campaign_id, attempt_id):
        if attempt_id == "closing-interrupted":
            raise CommandRejected("Synthetic interruption after the saved closing checkpoint")
        return complete(owner, campaign_id, attempt_id)

    with monkeypatch.context() as patched:
        patched.setattr(AuthorityOwner, "_complete_narration_attempt", interrupt_publication)
        harness = _OwnerHarness(database, provider)
        try:
            harness.send("closing", {"method": "submit_session_input", "params": {
                "session_id": session, "turn_id": turn,
                "attempt_id": "closing-interrupted", "message_id": "closing-one-input",
                "expected_revision": before_close.revision,
                "user_text": "Complete the earned escort and closing events.",
            }})
            failed = harness.read()
            assert failed["ok"] is False, failed
            assert failed["error"]["code"] == "command_rejected", failed
        finally:
            harness.close()
    assert provider.sent == 2
    with AuthorityOwner.open(database, "saved-closing-checkpoint") as owner:
        interrupted = owner.snapshot(campaign)
        assert list(interrupted.state[f"{SRD}.entities"][player]["inventory"]) == reward_inventory
        assert interrupted.actions[:len(before_close.actions)] == before_close.actions
    # Recover while interrupted, then retry from the saved checkpoint. No model call
    # or mechanic may repeat, and this exact checkpoint must survive portable export.
    database = _recover(database, tmp_path, campaign, session, "interrupted-closing")
    unused = LibraryProvider(responses=[])
    harness = _OwnerHarness(database, unused)
    try:
        basis = harness.request("retry-basis", "retryable_interruption", {
            "campaign_id": campaign, "failed_attempt_id": "closing-interrupted",
        })
        assert basis["retryable"]
        harness.request("retry-closing", "manual_retry_session", {
            "session_id": session, "interruption_id": basis["interruption_id"],
            "retry_id": "closing-recovered",
        })
    finally:
        harness.close()
    assert unused.calls == [], "Saved closing checkpoint was regenerated instead of recovered"
    with AuthorityOwner.open(database, "ending-verified") as owner:
        final = owner.snapshot(campaign)
        assert final.state == interrupted.state
        assert final.actions == interrupted.actions
        page = owner.session_history_page(session)
        assert page.campaign_ending["ending_id"] == "adventure-settled"
        assert [(m["act_number"], m["status"]) for m in page.act_markers] == [
            (1, "complete"), (2, "begins"), (2, "complete"),
        ]
        assert sum(m.message_id == "closing-one-input"
                   for m in owner.session_context(session).messages) == 1
    _play(database, campaign, session, 22, [_write(
        "The party accepts the completed story and discusses the next road.", ooc=True,
    )], choice="continue")
    _play(database, campaign, session, 23, [
        ("use_srd_second_wind", {"actor": player}),
        _write("The player uses the remaining Second Wind; the same companions continue."),
    ])
    with AuthorityOwner.open(database, "continued-mechanics") as owner:
        continued = owner.snapshot(campaign)
        actor = continued.state[f"{SRD}.entities"][player]
        assert actor["resources"]["second_wind"] == 0
        assert actor["resources"]["action_surge"] == 0
        assert list(actor["inventory"]).count("60_gp") == 1
        assert continued.actions[:len(final.actions)] == final.actions
        added = continued.actions[len(final.actions):]
        assert len(added) == 1 and added[0].tool_name == "use_srd_second_wind"
        summary = {row["label"]: row["value"]
                   for row in added[0].result["result_card"]["summary"]}
        assert actor["hp"] - final.state[f"{SRD}.entities"][player]["hp"] == summary["HP restored"]
    _recover(database, tmp_path, campaign, session, "continued-play")
