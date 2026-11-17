"""Turn grouping preserves independent flags and lazy reads of unassigned locals."""

from types import SimpleNamespace

import pytest

from sylvia_gm.agent._provider_turn_decisions import (
    _buffered_narration_is_settled,
    _campaign_file_requires_atomic_completion,
    _narration_needs_private_correction,
    _required_tool_call_is_missing,
    _round_allows_history_tool,
    _round_requires_tool_call,
    _tool_call_needs_fresh_catalog,
)
from sylvia_gm.agent._provider_turn_state import ProviderTurnState
from sylvia_gm.domain.authority import WriterRole


def test_turn_groups_keep_unassigned_reads_and_independent_instances():
    first, second = ProviderTurnState(), ProviderTurnState()
    with pytest.raises(UnboundLocalError, match="buffered_narration_needs_disposition"):
        _ = first.obligations.buffered_narration_needs_disposition
    first.obligations.buffered_narration_needs_disposition = True
    with pytest.raises(UnboundLocalError, match="buffered_narration_needs_disposition"):
        _ = second.obligations.buffered_narration_needs_disposition


def test_turn_decisions_keep_short_circuit_reads_before_flags_are_assigned():
    state = ProviderTurnState()
    state.buffered_narration = None
    assert not _buffered_narration_is_settled(state)
    state.provider_tool_call_required = False
    assert not _required_tool_call_is_missing(state)
    assert not _tool_call_needs_fresh_catalog(state, 0)
    builder_input = SimpleNamespace(session=SimpleNamespace(role=WriterRole.BUILDER))
    assert not _campaign_file_requires_atomic_completion(state, builder_input)
    state.obligations.engine_transition_review_pending = True
    assert _round_requires_tool_call(state, False)
    state.obligations.force_campaign_file_update = True
    assert not _round_allows_history_tool(state)


def test_private_narration_decision_does_not_read_or_classify_forced_disposition():
    state = ProviderTurnState()
    calls = []

    def classify(text):
        calls.append(text)
        return True

    state.obligations.force_campaign_file_update = True
    assert not _narration_needs_private_correction(state, classify)
    assert calls == []
    state.obligations.force_campaign_file_update = False
    with pytest.raises(UnboundLocalError, match="narration"):
        _narration_needs_private_correction(state, classify)
    assert calls == []
    state.narration = "held private reasoning"
    assert _narration_needs_private_correction(state, classify)
    assert calls == ["held private reasoning"]
