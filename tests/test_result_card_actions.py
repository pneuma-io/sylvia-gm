"""Saved card controls execute only their immutable request under current action admission."""

from dataclasses import replace
from pathlib import Path

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.errors import CommandRejected, IdempotencyConflict
from sylvia_gm.interfaces.owner_process import _dispatch
from tests.test_player_actions import _create, _move, _request

CARD = {
    "version": 1,
    "title": "Movement",
    "summary": [{"label": "Destination", "value": {"path": "parameters.destination"}}],
    "actions": [{
        "label": "Continue to the next position", "tool": "move_unit",
        "parameters": {"actor": "operator", "destination": [5, 4], "mode": "careful"},
    }],
}


def _card_request(**changes):
    request = {
        "campaign_id": "campaign", "session_id": "gm", "operation_id": "card-move",
        "expected_revision": 1,
        "source": {"kind": "result_card", "action_id": "move", "button_index": 0},
    }
    return {**request, **changes}


def _projection(owner):
    return next(item for item in owner.player_projection("campaign") if item.action_id == "move")


def test_panel_and_saved_card_confirmation_identify_the_controlled_actor(tmp_path, monkeypatch):
    with AuthorityOwner.open(tmp_path / "actor-confirmation.sqlite3", "owner") as owner:
        _create(owner, controls={"confirmation": "required", "actor_parameter": "actor"}, card=CARD)
        monkeypatch.setattr(owner, "_player_control_scope", lambda snapshot: {
            "available": True, "actors": [
                {"id": "another-operator", "name": "Other operator"},
                {"id": "operator", "name": "Vesper"},
            ],
        })
        panel = owner.preview_panel_action(**_request())
        assert panel["actor"] == {"id": "operator", "name": "Vesper"}
        assert panel["selection"]["actor"] == "operator"
        _move(owner, confirmation=panel["confirmation"])
        card = owner.player_action(**_card_request(), preview_only=True)
        assert card["selection"] == {}
        assert card["actor"] == panel["actor"]
        owner.player_action(**_card_request(confirmation=card["confirmation"]))
        assert owner.player_action_context("campaign")[-1]["parameters"]["actor"] == "operator"


def test_card_click_uses_saved_request_confirmation_and_existing_action_transport(tmp_path: Path):
    database = tmp_path / "cards.sqlite3"
    with AuthorityOwner.open(database, "owner") as owner:
        _create(owner, controls={"confirmation": "required", "response": "automatic"}, card=CARD)
        proposal = owner.preview_panel_action(**_request())
        _move(owner, confirmation=proposal["confirmation"])
        saved = _projection(owner).safe_payload["result"]["result_card"]
        assert _projection(owner).safe_payload["result_card_action_availability"] == [True]
    with AuthorityOwner.open(database, "owner") as owner:
        proposal = owner.player_action(**_card_request(), preview_only=True)
        assert proposal["confirmation_required"] is True
        assert proposal["label"] == "Continue to the next position"
        with pytest.raises(CommandRejected, match="confirm"):
            owner.player_action(**_card_request())
        receipt = _dispatch(
            None, "execute_player_action",
            _card_request(confirmation=proposal["confirmation"]),
            application_owner=owner, owner_id="owner", provider_resolver=None,
            model_choices=lambda: (), session_loop=None, storage_root=tmp_path,
        )
        assert receipt.resulting_revision == 2
        state = owner.snapshot("campaign").state["synthetic.coordinate.state"]
        assert state["last_destination"] == [5, 4]
        assert owner.player_action(**_card_request()) == receipt
        with pytest.raises(IdempotencyConflict):
            owner.player_action(**_card_request(source={
                "kind": "result_card", "action_id": "card-move", "button_index": 0,
            }))
        origin = owner.player_action_context("campaign")[-1]["origin"]
        assert origin["control"] == "result_card"
        assert origin["source_action_id"] == "move"
        assert origin["policy"]["response"] == "automatic"
        assert _projection(owner).safe_payload["result"]["result_card"] == saved


def test_card_click_rejects_forged_arguments_and_busy_work_without_executing(tmp_path: Path):
    with AuthorityOwner.open(tmp_path / "admission.sqlite3", "owner") as owner:
        _create(owner, card=CARD)
        _move(owner)
        with pytest.raises(CommandRejected, match="replacement parameters"):
            owner.player_action(**_card_request(selection={"actor": "other"}))
        with pytest.raises(CommandRejected, match="source is invalid"):
            owner.player_action(**_card_request(source={
                "kind": "result_card", "action_id": "move", "button_index": 0,
                "parameters": {"actor": "other"},
            }))
        with owner.agent_session_work("gm"):
            assert _projection(owner).safe_payload["result_card_action_availability"] == [False]
            with pytest.raises(CommandRejected) as rejected:
                owner.player_action(**_card_request())
            assert rejected.value.code == "campaign_work_in_progress"
        assert len(owner.snapshot("campaign").actions) == 1
        owner.complete_turn("campaign", "turn")
        assert _projection(owner).safe_payload["result_card_action_availability"] == [False]
        with pytest.raises(CommandRejected):
            owner.player_action(**_card_request())


def test_old_card_checks_current_tool_schema_without_rewriting_display(tmp_path: Path, monkeypatch):
    with AuthorityOwner.open(tmp_path / "schema.sqlite3", "owner") as owner:
        _create(owner, card=CARD)
        _move(owner)
        saved = _projection(owner).safe_payload["result"]["result_card"]
        catalog = owner._catalog

        def changed_schema(campaign_id):
            return tuple(replace(tool, input_schema={
                "type": "object", "properties": {"new_parameter": {"type": "string"}},
                "required": ["new_parameter"], "additionalProperties": False,
            }) for tool in catalog(campaign_id))

        monkeypatch.setattr(owner, "_catalog", changed_schema)
        projection = _projection(owner)
        assert projection.safe_payload["result_card_action_availability"] == [False]
        assert projection.safe_payload["result"]["result_card"] == saved
        with pytest.raises(CommandRejected, match="parameters"):
            owner.player_action(**_card_request())
        assert len(owner.snapshot("campaign").actions) == 1
