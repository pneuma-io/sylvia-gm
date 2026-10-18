"""Actual rope manipulation and source-qualified queued drops preserve source and body identity."""

import os
from copy import deepcopy

import pytest

from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_action_budget as budget
from tests import test_srd_spell_travel_rope as rope
from tests.srd_full.test_srd_spell_travel import SRD


@pytest.fixture
def rope_state_scope(monkeypatch):
    monkeypatch.setenv(
        "FC14_ROPE_RUNTIME",
        os.environ.get("FC14_ROPE_STATE_RUNTIME", ".scratch/rope-state-composition/runtime.yaml"),
    )
    yield from rope.rope_scope.__wrapped__(monkeypatch)


def _real_rope(state):
    return next(
        row for row in state[f"{SRD}.state"]["spatial_scene"]["entities"] if row["id"] == "rope"
    )


def test_actual_pull_drop_and_expiry_restore_physical_rope_once(rope_state_scope):
    state, _ = rope._rope_case()
    initial = deepcopy(_real_rope(state))
    rope._install(state)
    rope._enter(state)
    source = deepcopy(rope._source(state))
    budget._run(
        "manipulate_srd_rope_trick_rope",
        state,
        actor_state="hero",
        source_id=source["id"],
        operation="pull",
    )
    assert _real_rope(state)["metadata"]["plane"] == source["room_id"]
    assert _real_rope(state)["height"] == initial["height"]
    assert rope._source(state)["rope_pulled"] is True
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="inside or outside"):
        budget._run(
            "manipulate_srd_rope_trick_rope",
            state,
            actor_state="hero",
            source_id=source["id"],
            operation="pull",
        )
    assert state == before
    budget._run(
        "manipulate_srd_rope_trick_rope",
        state,
        actor_state="hero",
        source_id=source["id"],
        operation="drop",
    )
    assert _real_rope(state)["position"] == initial["position"]
    assert _real_rope(state)["height"] == source["hung_height"]
    assert rope._source(state) == source
    hp = state[f"{SRD}.entities"]["hero"]["hp"]
    budget._run("advance_srd_lasting_time", state, elapsed_rounds=600)
    assert rope._source(state)["return_pending"] is True
    budget._run("settle_srd_extradimensional_returns", state)
    assert rope._body(state, "hero")["metadata"]["plane"] == source["origin_plane"]
    assert (
        rope._body(state, "hero")["position"][2]
        == source["origin"][2] - rope._body(state, "hero")["height"]
    )
    assert state[f"{SRD}.entities"]["hero"]["hp"] == hp
    assert rope._source(state)["return_pending"] is False
    assert _real_rope(state)["height"] == initial["height"]
    assert _real_rope(state)["metadata"]["rope_trick_source_id"] is None
    before = deepcopy(state)
    budget._run("settle_srd_extradimensional_returns", state)
    assert state == before


def test_one_actual_body_per_drop_keeps_sibling_queued_until_next_outer(rope_state_scope):
    state, _ = rope._rope_case()
    rope._install(state)
    rope._enter(state)
    body = rope._body(state, "hero")
    budget._run(
        "move_srd_scene_creature",
        state,
        actor_state="hero",
        mode="walk",
        destination=[body["position"][0] + 2, body["position"][1], body["position"][2]],
    )
    rope._enter(state, "guard")
    source = deepcopy(rope._source(state))
    occupants = [
        row["metadata"]["participant_ref"]
        for row in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if row["metadata"].get("plane") == source["room_id"]
        and row["metadata"].get("participant_ref")
    ]
    budget._run("advance_srd_lasting_time", state, elapsed_rounds=600)
    budget._run("settle_srd_extradimensional_returns", state)
    assert rope._body(state, occupants[0])["metadata"]["plane"] == source["origin_plane"]
    assert rope._body(state, occupants[1])["metadata"]["plane"] == source["room_id"]
    assert rope._source(state)["return_pending"] is True
    assert state[f"{SRD}.state"]["has_extradimensional_pending_returns"] is True
    budget._run("settle_srd_extradimensional_returns", state)
    assert rope._body(state, occupants[1])["metadata"]["plane"] == source["origin_plane"]
    assert rope._source(state)["return_pending"] is False
    assert state[f"{SRD}.state"]["has_extradimensional_pending_returns"] is False


def test_actual_independent_rope_movement_cannot_be_reset_by_pull(rope_state_scope):
    state, _ = rope._rope_case()
    rope._install(state)
    rope._enter(state)
    source = deepcopy(rope._source(state))
    budget._run(
        "move_srd_travel_subject",
        state,
        subject="rope",
        destination=[2, 0, 0],
        plane=source["origin_plane"],
        teleport=False,
    )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="independently"):
        budget._run(
            "manipulate_srd_rope_trick_rope",
            state,
            actor_state="hero",
            source_id=source["id"],
            operation="pull",
        )
    assert state == before
