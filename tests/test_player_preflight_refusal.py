"""Admitted controls preserve domain refusals and hide unexpected preflight faults."""

from copy import deepcopy

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.errors import CommandRejected
from tests.test_player_actions import _create, _move, _request

REASON = "The creature does not have enough movement remaining."
GENERIC = "This action's availability could not be determined"


def _budget_preflight(runtime):
    """Exercise nested qualification through an admitted synthetic control."""
    move = runtime["tools"]["move_unit"]
    move["metadata"]["player_controls"] = {"preflight": ["qualify"]}
    parameters = deepcopy(move["parameters"])
    runtime["tools"]["qualify"] = {
        "parameters": parameters,
        "effects": [
            {"call": "budget", "params": {p["name"]: "{" + p["name"] + "}" for p in parameters}}
        ],
    }
    runtime["tools"]["budget"] = {
        "parameters": deepcopy(parameters),
        "effects": [
            {"set": {"field": "state.last_destination", "value": "[99, 99]"}},
            {"require": {"condition": "destination[0] <= 4", "error": REASON}},
        ],
    }


def _availability(owner, request):
    return owner.player_action_availability(
        campaign_id="campaign",
        expected_revision=request["expected_revision"],
        requests=[
            {
                "source": {
                    "kind": "panel",
                    "surface_id": request["surface_id"],
                    "action_id": request["panel_action_id"],
                },
                "selection": request["selection"],
            }
        ],
    )["results"][0]


def _assert_refused(owner, request, reason):
    before = owner.snapshot("campaign")
    assert owner.panel_action_receipt(**request) is None
    ready = _availability(owner, request)
    assert ready["available"] is False
    assert ready["reason"] == reason
    with pytest.raises(CommandRejected, match=reason):
        owner.execute_panel_action(**request)
    assert owner.snapshot("campaign") == before
    assert owner.panel_action_receipt(**request) is None
    assert not owner.player_action_context("campaign")


def test_nested_preflight_refusal_is_atomic_and_exact_budget_retry_commits_once(tmp_path):
    database = tmp_path / "budget.sqlite3"
    with AuthorityOwner.open(database, "owner") as owner:
        _create(owner, runtime_edit=_budget_preflight)
        before = owner.snapshot("campaign")
        request = _request(
            selection={"actor": "operator", "destination": [5, 4], "map_revision": 7}
        )
        _assert_refused(owner, request, REASON)
    with AuthorityOwner.open(database, "owner") as owner:
        assert owner.snapshot("campaign") == before
        _assert_refused(owner, request, REASON)
        # The refused operation has no receipt: the same identity can use its legal budget.
        exact = _request()
        assert _availability(owner, exact)["available"] is True
        assert owner.snapshot("campaign") == before
        receipt = _move(owner)
        after = owner.snapshot("campaign")
        assert after.revision == before.revision + 1
        assert len(after.actions) == len(before.actions) + 1
        assert after.state["synthetic.coordinate.state"]["last_destination"] == (4, 4)
        assert owner.panel_action_receipt(**exact) == receipt
    with AuthorityOwner.open(database, "owner") as owner:
        assert _move(owner) == receipt
        assert owner.snapshot("campaign") == after


@pytest.mark.parametrize("fault", ["effect", "expression", "exception"])
def test_unexpected_preflight_fault_is_generic_and_never_commits(tmp_path, monkeypatch, fault):
    import sylvia_gm.application.package_runtime as package_runtime

    def edit(runtime):
        _budget_preflight(runtime)
        if fault == "effect":
            runtime["tools"]["budget"]["effects"] = [
                {"require": {"condition": "1 // state.last_actor > 0", "error": REASON}},
            ]
        elif fault == "expression":
            runtime["tools"]["budget"]["pre_conditions"] = [
                {
                    "condition": "1 // state.last_actor > 0",
                    "error": "Private expression failure",
                }
            ]

    with AuthorityOwner.open(tmp_path / "fault.sqlite3", "owner") as owner:
        _create(owner, runtime_edit=edit, initial_last_actor=0)
        failures = []
        execute = package_runtime.execute_tool

        def observed(*args, **kwargs):
            result = execute(*args, **kwargs)
            if not result.success:
                failures.append(result.failure_kind)
            return result

        monkeypatch.setattr(package_runtime, "execute_tool", observed)
        if fault == "exception":

            def broken(*args, **kwargs):
                raise RuntimeError("Private implementation failure")

            monkeypatch.setattr(package_runtime, "_player_preflight", broken)
        _assert_refused(owner, _request(), GENERIC)
        if fault != "exception":
            assert (
                "effect_execution" if fault == "effect" else "precondition_evaluation"
            ) in failures


@pytest.mark.parametrize("value", [True, False])
def test_integer_selection_rejects_boolean_before_preflight(tmp_path, value):
    with AuthorityOwner.open(tmp_path / "boolean.sqlite3", "owner") as owner:
        _create(owner, runtime_edit=_budget_preflight)
        request = _request(
            selection={
                "actor": "operator",
                "destination": [value, 4],
                "map_revision": 7,
            }
        )
        before = owner.snapshot("campaign")
        assert _availability(owner, request)["available"] is False
        with pytest.raises(CommandRejected):
            owner.execute_panel_action(**request)
        assert owner.snapshot("campaign") == before
        assert owner.panel_action_receipt(**request) is None
