"""Read-only controls and committed mechanics share current package prerequisites."""

from copy import deepcopy
from pathlib import Path

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.errors import CommandRejected, StaleRevision
from sylvia_gm.interfaces.owner_process import _dispatch
from tests.test_player_actions import _create, _move, _request
from tests.test_result_card_actions import CARD, _card_request, _projection


def _one_move(runtime):
    """Bind the prior actor from real state so the first move consumes its availability."""

    tool = runtime["tools"]["move_unit"]
    tool["parameters"].append({
        "name": "previous_actor", "type": "string", "required": False,
        "from_state": "state.last_actor",
    })


def _checks(owner, *, revision=0, actor="operator", destination=None):
    return owner.player_action_availability(
        campaign_id="campaign", expected_revision=revision, requests=[{
            "source": {"kind": "panel", "surface_id": "operations.map", "action_id": "move"},
            "selection": {"actor": actor, "destination": destination or [4, 4], "map_revision": 7},
        }],
    )["results"][0]


def test_non_map_preview_is_read_only_and_returned_only_after_eligibility(tmp_path, monkeypatch):
    controls = {
        "checks": [{"condition": "destination[0] < 5", "reason": "Destination unavailable"}],
        "calculations": [{"set": {"field": "_selected", "value": "actor"}}],
        "preview": {"version": 1, "title": "Selected actor", "summary": [
            {"label": "Actor", "value": {"path": "context._selected"}},
        ]},
    }
    with AuthorityOwner.open(tmp_path / "text-preview.sqlite3", "owner") as owner:
        _create(owner, controls=controls)
        before = owner.snapshot("campaign")
        def forbidden(*args, **kwargs):
            pytest.fail("Read-only availability attempted effects or dice")
        monkeypatch.setattr("sylvia_gm.runtime._tool_executor.execute_effects", forbidden)
        monkeypatch.setattr("sylvia_gm.dice.set_seed", forbidden)
        accepted = _checks(owner)
        assert accepted["available"] is True
        assert accepted["preview"]["summary"][0]["value"] == "operator"
        assert _checks(owner, destination=[5, 4]) == {
            "available": False, "reason": "Destination unavailable",
        }
        assert owner.snapshot("campaign") == before


@pytest.mark.parametrize("count, available", [(24, True), (25, False)])
def test_preview_scalar_budget_is_bounded_and_read_only(tmp_path, count, available):
    controls = {
        "calculations": [
            {"set": {"field": f"_value_{index}", "value": str(index)}}
            for index in range(count)
        ],
        "preview": {"version": 1, "summary": [
            {"label": "Last value", "value": {"path": f"context._value_{count - 1}"}},
        ]},
    }
    with AuthorityOwner.open(tmp_path / "scalar-budget.sqlite3", "owner") as owner:
        _create(owner, controls=controls)
        before = owner.snapshot("campaign")
        result = _checks(owner)
        assert result["available"] is available
        if available:
            assert result["preview"]["summary"][0]["value"] == count - 1
        else:
            assert "preview" not in result
        assert owner.snapshot("campaign") == before


def test_checks_do_not_execute_and_spent_action_disables_old_card_after_reopen(
    tmp_path: Path, monkeypatch,
):
    database = tmp_path / "readiness.sqlite3"
    with AuthorityOwner.open(database, "owner") as owner:
        _create(owner, runtime_edit=_one_move, initial_last_actor="", card=CARD,
                controls={"checks": [{
            "condition": "previous_actor != 'operator'", "reason": "This action is already spent",
        }]})
        before = owner.snapshot("campaign")
        with monkeypatch.context() as patch:
            def forbidden(*args, **kwargs):
                pytest.fail("An availability check tried to run effects or seed the dice")
            patch.setattr("sylvia_gm.runtime._tool_executor.execute_effects", forbidden)
            patch.setattr("sylvia_gm.dice.set_seed", forbidden)
            assert _checks(owner) == {"available": True, "reason": None}
            owner.preview_panel_action(**_request())
        assert owner.snapshot("campaign") == before
        receipt = _move(owner)
        assert receipt.resulting_revision == 1
        saved_card = _projection(owner).safe_payload["result"]["result_card"]
        assert _projection(owner).safe_payload["result_card_action_availability"] == [False]
        assert _projection(owner).safe_payload["result_card_action_reasons"] == [
            "This action is already spent",
        ]
        assert _checks(owner, revision=1) == {
            "available": False, "reason": "This action is already spent",
        }
        assert _move(owner) == receipt  # The original retry remains idempotent after spending.
        with pytest.raises(StaleRevision):
            _checks(owner)
    with AuthorityOwner.open(database, "owner") as owner:
        for preview in (False, True):
            with pytest.raises(CommandRejected, match="already spent"):
                owner.player_action(**_card_request(), preview_only=preview)
        with pytest.raises(CommandRejected, match="already spent"):
            _move(owner, operation_id="another-move", expected_revision=1)
        assert _projection(owner).safe_payload["result"]["result_card"] == saved_card
        assert len(owner.snapshot("campaign").actions) == 1


def test_runtime_preconditions_also_gate_preview_without_exposing_their_template(tmp_path: Path):
    def restrict(runtime):
        runtime["tools"]["move_unit"]["pre_conditions"] = [{
            "condition": "destination[0] < 5", "error": "Private mechanic detail",
        }]
    with AuthorityOwner.open(tmp_path / "preconditions.sqlite3", "owner") as owner:
        _create(owner, runtime_edit=restrict)
        assert _checks(owner)["available"] is True
        unavailable = _checks(owner, destination=[5, 4])
        assert unavailable == {
            "available": False, "reason": "This action's prerequisites are not currently met",
        }
        with pytest.raises(CommandRejected, match="prerequisites"):
            owner.preview_panel_action(**_request(selection={
                "actor": "operator", "destination": [5, 4], "map_revision": 7,
            }))
        assert not owner.snapshot("campaign").actions


@pytest.mark.parametrize("checks", [
    [{"condition": "roll('d20') > 10", "reason": "Needs a roll"}],
    [{"condition": "missing.value > 0", "reason": "Missing value"}],
    [{"condition": "true", "reason": "Should fail", "effects": []}],
])
def test_invalid_or_impure_checks_fail_closed(tmp_path: Path, checks):
    with AuthorityOwner.open(tmp_path / "bad-check.sqlite3", "owner") as owner:
        _create(owner, controls={"checks": checks})
        assert _checks(owner)["available"] is False
        with pytest.raises(CommandRejected):
            _move(owner)
        assert not owner.snapshot("campaign").actions


def test_batch_transport_rechecks_busy_scope_and_hidden_selection(tmp_path: Path):
    with AuthorityOwner.open(tmp_path / "batch.sqlite3", "owner") as owner:
        _create(owner)
        requests = [{
            "source": {"kind": "panel", "surface_id": "operations.map", "action_id": "move"},
            "selection": {"actor": actor, "destination": [4, 4], "map_revision": 7},
        } for actor in ("operator", "ambusher")]
        def dispatch():
            return _dispatch(
                None, "player_action_availability", {
                    "campaign_id": "campaign", "expected_revision": 0, "requests": requests,
                }, application_owner=owner, owner_id="owner", provider_resolver=None,
                model_choices=lambda: (), session_loop=None, storage_root=tmp_path,
            )
        result = dispatch()
        assert [item["available"] for item in result["results"]] == [True, False]
        assert "concealed-hostile" not in str(result)
        with owner.agent_session_work("gm"):
            assert all(item["reason"] == "Sylvia is responding" for item in dispatch()["results"])
        assert dispatch() == result
        assert not owner.snapshot("campaign").actions


def test_path_bound_control_uses_the_complete_owner_scene(tmp_path: Path):
    with AuthorityOwner.open(tmp_path / "path-control.sqlite3", "owner") as owner:
        _create(owner, path_binding=True)
        assert _checks(owner, destination=[7, 2])["available"] is True
        receipt = _move(owner, selection={
            "actor": "operator", "destination": [7, 2], "map_revision": 7,
        })
        path = owner.snapshot("campaign").actions[0].parameters["path"]
        assert path[0] == [1, 2] and path[-1] == [7, 2]
        assert [6, 2] not in path  # The hidden occupying actor still obstructs the real route.
        assert receipt.resulting_revision == 1


@pytest.mark.parametrize('confirmation', ['none', 'required'])
def test_path_binding_cannot_disclose_a_hidden_occupant_before_admission(tmp_path: Path, confirmation):
    with AuthorityOwner.open(tmp_path / 'hidden-path-binding.sqlite3', 'owner') as owner:
        _create(owner, path_binding=True, controls={'confirmation': confirmation})
        before = owner.snapshot('campaign')
        selected = {'actor': 'operator', 'destination': [6, 2], 'map_revision': 7}
        preview = owner.preview_panel_action(**_request(selection=selected))
        assert _checks(owner, destination=[6, 2])['available'] is True
        with pytest.raises(CommandRejected) as rejected:
            _move(owner, selection=selected, confirmation=preview['confirmation'])
        assert rejected.value.code == 'panel_action_invalid'
        assert owner.snapshot('campaign') == before


def test_confirmation_binds_the_known_intent_when_the_full_route_differs(tmp_path: Path):
    from sylvia_gm import dice
    database = tmp_path / 'confirmed-hidden-route.sqlite3'
    with AuthorityOwner.open(database, 'owner') as owner:
        _create(owner, path_binding=True, controls={'confirmation': 'required'})
        selected = {'actor': 'operator', 'destination': [7, 2], 'map_revision': 7}
        request = _request(selection=selected)
        before, rng = owner.snapshot('campaign'), dice._rng.getstate()
        preview = owner.preview_panel_action(**request)
        assert owner.preview_panel_action(**request) == preview
        for change in (
            {'confirmation': None},
            {'confirmation': 'invalid'},
            {'operation_id': 'another-move', 'confirmation': preview['confirmation']},
            {'selection': {**selected, 'destination': [7, 3]}, 'confirmation': preview['confirmation']},
        ):
            with pytest.raises(CommandRejected) as rejected:
                owner.execute_panel_action(**{**request, **change})
            assert rejected.value.code == 'action_confirmation_required'
            assert owner.snapshot('campaign') == before
        with pytest.raises(StaleRevision):
            owner.execute_panel_action(**{**request, 'expected_revision': 1}, confirmation=preview['confirmation'])
        assert owner.snapshot('campaign') == before and dice._rng.getstate() == rng
        receipt = owner.execute_panel_action(**request, confirmation=preview['confirmation'])
        path = owner.snapshot('campaign').actions[0].parameters['path']
        assert path[-1] == [7, 2] and [6, 2] not in path
        after = owner.snapshot('campaign')
        assert owner.execute_panel_action(**request) == receipt
        assert owner.snapshot('campaign') == after
        assert after.revision == 1 and len(after.actions) == 1
    with AuthorityOwner.open(database, 'owner') as owner:
        assert owner.execute_panel_action(**request) == receipt
        assert owner.snapshot('campaign').state == after.state
        assert owner.snapshot('campaign').revision == 1


def _route_controls():
    return {
        "queries": [{
            "spatial_query": {
                "scene": "state.spatial_scene", "operation": "path", "query": {
                    "map_id": "training-floor", "start": [1, 2], "goal": "{destination}",
                    "moving_entity_id": "{actor}", "allow_diagonal": True,
                    "corner_policy": "forbid_cutting", "include_entities": True,
                    "max_steps": 256,
                },
            }, "store_as": "_path",
        }],
        "calculations": [{
            "set": {"field": "_route_ap", "value": "(_path.cost + 1.999999) // 2"},
            "condition": "_path.found == true",
        }],
        "checks": [
            {"condition": "_path.found == true", "reason": "No route is currently available"},
            {"condition": "_route_ap <= 1", "reason": "This route costs too many action points"},
        ],
    }


def test_spatial_readiness_uses_known_route_cost_without_effects_or_private_output(
    tmp_path: Path, monkeypatch,
):
    with AuthorityOwner.open(tmp_path / "route-check.sqlite3", "owner") as owner:
        _create(owner, controls=_route_controls())
        before = owner.snapshot("campaign")
        with monkeypatch.context() as patch:
            def forbidden(*args, **kwargs):
                pytest.fail("Spatial readiness attempted an effect or dice operation")
            patch.setattr("sylvia_gm.runtime._tool_executor.execute_effects", forbidden)
            patch.setattr("sylvia_gm.dice.set_seed", forbidden)
            assert _checks(owner, destination=[2, 2])["available"] is True
            assert _checks(owner, destination=[4, 4]) == {
                "available": False, "reason": "This route costs too many action points",
            }
            assert _checks(owner, destination=[3, 2]) == {
                "available": False, "reason": "No route is currently available",
            }
            assert _checks(owner, destination=[6, 2]) == {
                "available": False, "reason": "This route costs too many action points",
            }
            with pytest.raises(CommandRejected, match="No route is currently available"):
                _move(owner, selection={'actor': 'operator', 'destination': [6, 2], 'map_revision': 7})
            with pytest.raises(CommandRejected, match="too many action points"):
                _move(owner)
        assert owner.snapshot("campaign") == before
        receipt = _move(owner, selection={
            "actor": "operator", "destination": [2, 2], "map_revision": 7,
        })
        assert receipt.resulting_revision == 1
        assert "_path" not in str(receipt.result)


@pytest.mark.parametrize("change", [
    {"queries": [{"spatial_mutation": {}, "store_as": "_path"}]},
    {"calculations": [{"set": {"field": "state.ap", "value": "10"}}]},
    {"calculations": [{"set": {"field": "_route_ap", "value": "roll('d20')"}}]},
])
def test_player_queries_cannot_mutate_or_roll(tmp_path: Path, change):
    with AuthorityOwner.open(tmp_path / "invalid-query.sqlite3", "owner") as owner:
        controls = deepcopy(_route_controls())
        controls.update(change)
        _create(owner, controls=controls)
        before = owner.snapshot("campaign")
        assert _checks(owner)["available"] is False
        with pytest.raises(CommandRejected):
            _move(owner)
        assert owner.snapshot("campaign") == before
