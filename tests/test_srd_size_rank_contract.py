"""Synthetic creature declarations reach actual Lite movement and turn-end consumers."""

from copy import deepcopy

import pytest

from sylvia_gm.domain.errors import CommandRejected, RulesetPreconditionRejected
from tests.lite_imported_fixture import ImportedCase
from tests.test_srd_action_budget import SRD, _run
from tests.test_srd_attack_budget import _combat_case
from tests.test_srd_creature_space_owner import request
from tests.test_srd_lite_foundation import Case


def test_budget_combat_declares_medium_creatures_without_changing_physical_bodies():
    state, data = _combat_case()
    assert data["size_rank"] == 2
    assert state[f"{SRD}.entities"]["hero"]["size_rank"] == 2
    assert state[f"{SRD}.entities"]["guard"]["size_rank"] == 2
    for body in state[f"{SRD}.state"]["spatial_scene"]["entities"]:
        assert body["footprint"] == {"kind": "box", "half_extents": [0.4, 0.4]}


def test_declared_budget_creatures_move_then_end_turn_without_repeating_cost():
    state, data = _combat_case()
    result = _run(
        "move_srd_scene_creature",
        state,
        participant_data=data,
        actor_state="hero",
        destination=[0, 10, 0],
    )
    assert result.result["status"] == "committed"
    assert state[f"{SRD}.combat"]["movement_remaining"] == 20
    assert state[f"{SRD}.combat"]["movement_spent"] == 10
    result = _run("end_combat_turn", state, participant_data=data, actor="hero")
    assert result.result["status"] == "committed"
    assert state[f"{SRD}.combat"]["turn_active"] is False
    assert state[f"{SRD}.combat"]["movement_spent"] == 10


@pytest.mark.parametrize("tool", ["move_srd_scene_creature", "end_combat_turn"])
@pytest.mark.parametrize("subject", ["hero", "guard"])
@pytest.mark.parametrize("rank", ["missing", None, -1, 6, True, False, "Medium"])
def test_size_consumers_refuse_missing_or_unsupported_state_without_mutation(tool, subject, rank):
    state, data = _combat_case()
    # Declare both creatures explicitly so each negative case isolates one bad fact.
    for identity in ("hero", "guard"):
        state[f"{SRD}.entities"][identity]["size_rank"] = 2
    creature = state[f"{SRD}.entities"][subject]
    if rank == "missing":
        del creature["size_rank"]
    else:
        creature["size_rank"] = rank
    before = deepcopy(state)
    parameters = (
        {"actor_state": "hero", "destination": [0, 10, 0]}
        if tool == "move_srd_scene_creature"
        else {"actor": "hero"}
    )
    with pytest.raises(RulesetPreconditionRejected, match="size_rank|rank"):
        _run(tool, state, participant_data=data, **parameters)
    assert state == before


@pytest.mark.parametrize("rank", [1, 2])
@pytest.mark.parametrize("factory", [ImportedCase, Case])
def test_imported_human_keeps_selected_size_in_saved_creature_state(tmp_path, rank, factory):
    value = factory(
        tmp_path / "declared-size.sqlite3", "fire_bolt", participant_changes={"size_rank": rank}
    )
    try:
        value.owner.set_admin_full_audit(True)
        before = value.snapshot()
        assert before.participants[0].data["size_rank"] == rank
        assert before.state[f"{SRD}.entities"]["hero"]["size_rank"] == rank
        for identity in ("guard", "other", "extra"):
            assert before.state[f"{SRD}.entities"][identity]["size_rank"] == 2
        value.reopen()
        assert value.snapshot() == before
    finally:
        value.close()


@pytest.mark.parametrize("rank", [None, True, 6])
def test_saved_invalid_size_stays_refused_without_mutation_after_reopen(tmp_path, rank):
    value = ImportedCase(
        tmp_path / "invalid-size.sqlite3",
        "fire_bolt",
        positions={"hero": [0, 0, 0], "guard": [5, 0, 0]},
        participant_changes={"party_role": "player_character"},
        entity_changes={"hero": {"size_rank": rank}},
        scene_metadata_changes={
            identity: {"player": {"label": identity, "kind": "unit"}}
            for identity in ("hero", "guard")
        },
    )
    try:
        value.owner.set_admin_full_audit(True)
        value.start()
        before = value.snapshot()
        for reopened in (False, True):
            if reopened:
                value.reopen()
            proposed = request(value, [0, 10, 0])
            availability = value.owner.player_action_availability(
                campaign_id="lite",
                expected_revision=before.revision,
                requests=[
                    {
                        "source": {
                            "kind": "panel",
                            "surface_id": proposed["surface_id"],
                            "action_id": proposed["panel_action_id"],
                        },
                        "selection": proposed["selection"],
                    }
                ],
            )
            assert availability["results"][0]["available"] is False
            with pytest.raises(CommandRejected):
                value.owner.preview_panel_action(**proposed)
            for tool, parameters in (
                ("move_srd_scene_creature", {"actor_state": "hero", "destination": [0, 10, 0]}),
                ("end_combat_turn", {}),
            ):
                with pytest.raises(RulesetPreconditionRejected, match="size_rank|rank"):
                    value.execute(value.command(tool, parameters))
            assert value.snapshot() == before
            assert value.owner.panel_action_receipt(**proposed) is None
    finally:
        value.close()
