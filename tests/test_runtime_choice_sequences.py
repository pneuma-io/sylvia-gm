"""Successive decisions keep one mechanics reservation and one final commit."""

from dataclasses import replace

import pytest
import yaml

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import AuthorityOwner, ResolveMechanicsChoiceCommand
from sylvia_gm.domain.authority import ActionStatus, AwaitingChoiceReceipt
from sylvia_gm.domain.errors import IdempotencyConflict
from tests.test_alpha_f3_package_execution import _archive, _choice_runtime_yaml, _command, _create


@pytest.mark.parametrize("portable_repeated", [False, True])
def test_two_choices_reopen_retry_and_commit_once(tmp_path, portable_repeated):
    raw = yaml.safe_load(_choice_runtime_yaml())
    raw["tools"]["advance"]["effects"].extend([
        {"request_choice": {"choice_id": "confirm-response",
                            "prompt": "Signal {_signal.total}: confirm the response?",
                            "options": [{"id": "yes", "label": "Confirm"},
                                        {"id": "no", "label": "Decline"}]},
         "store_as": "_confirmation"},
        {"condition": "_confirmation == 'yes'",
         "on_true": [{"increment": {"field": "state.counter", "amount": 10}}]},
    ])
    if portable_repeated:
        raw["tools"]["advance"]["effects"][3]["request_choice"] = (
            raw["tools"]["advance"]["effects"][1]["request_choice"]
        )
        raw["tools"]["advance"]["effects"][4]["condition"] = "_confirmation == 'ground'"
    archive = _archive(runtime_bytes=yaml.safe_dump(raw, sort_keys=False).encode())
    database = tmp_path / "choices.db"
    action = _command(revision=0, suffix="sequence", steps=3)
    first_answer = ResolveMechanicsChoiceCommand(
        campaign_id="campaign-runtime", action_id=action.action_id,
        resolution_id="answer-first", owner_id="owner-runtime", expected_revision=0,
        choice_id="signal-response", option_id="ground",
    )
    second_answer = replace(first_answer, resolution_id="answer-second",
                            choice_id="confirm-response", option_id="yes", choice_sequence=1)
    if portable_repeated:
        second_answer = replace(second_answer, choice_id="signal-response", option_id="ground")
    with AuthorityOwner.open(database, "owner-runtime") as owner:
        _create(owner, archive)
        first = StatelessMcpMechanics(owner).execute_action(action)
        assert isinstance(first, AwaitingChoiceReceipt)
        baseline = owner.snapshot("campaign-runtime")
        second = owner.resolve_mechanics_choice(first_answer)
        assert isinstance(second, AwaitingChoiceReceipt)
        assert second.choice.choice_id == second_answer.choice_id
        assert second.choice_sequence == 1
        assert second.basis_revision == 0
        assert owner.snapshot("campaign-runtime") == baseline
        assert owner.resolve_mechanics_choice(first_answer) == second
        with pytest.raises(IdempotencyConflict):
            owner.resolve_mechanics_choice(replace(first_answer, option_id="echo"))
        with pytest.raises(IdempotencyConflict):
            owner.resolve_mechanics_choice(replace(second_answer, choice_sequence=0))
        assert owner.execute_action(action) == second
        projected = next(p.safe_payload for p in owner.player_projection("campaign-runtime")
                         if p.kind.value == "mechanics_choice")
        assert projected["choice_sequence"] == 1
        assert "prior_answers" not in projected
        if portable_repeated:
            owner.export_campaign(operation_id="export-second-choice",
                                  campaign_id="campaign-runtime",
                                  target=tmp_path / "pending.sylvia-gm-campaign")
    if portable_repeated:
        database = tmp_path / "recovered.db"
        with AuthorityOwner.open(database, "owner-runtime") as owner:
            owner.recover_campaign(recovery_id="recover-second-choice",
                                   source=tmp_path / "pending.sylvia-gm-campaign")
    with AuthorityOwner.open(database, "owner-runtime") as owner:
        assert owner.execute_action(action) == second
        assert owner.resolve_mechanics_choice(first_answer) == second
        final = owner.resolve_mechanics_choice(second_answer)
        assert final.status is ActionStatus.COMMITTED
        saved = owner.snapshot("campaign-runtime")
        assert saved.revision == 1
        assert len(saved.actions) == 1
        assert saved.state["synthetic.clockwork.state"] == {"counter": 10, "bonus": 3}
    with AuthorityOwner.open(database, "owner-runtime") as owner:
        assert owner.resolve_mechanics_choice(first_answer) == final
        assert owner.resolve_mechanics_choice(second_answer) == final
        assert owner.execute_action(action) == final
        assert owner.snapshot("campaign-runtime") == saved
