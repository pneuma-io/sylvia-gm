"""Party actions honor the existing single-actor and whole-side Ruleset declarations."""

import hashlib
import io
import json
import sqlite3
import zipfile
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from sylvia_gm.application.authority import ActionCommand, AuthorityOwner, CampaignCreate
from sylvia_gm.domain.authority import ActionStatus, Participant, StateValue, WriterRole
from sylvia_gm.domain.errors import CommandRejected, SchemaIdentityError
from sylvia_gm.domain.package_library import canonical_json_bytes, content_root_for_record
from tests.test_result_card_actions import CARD
from tests.test_tactical_grid_ui import _installed_archive, _scene

PACKAGE = "synthetic.coordinate"


def _campaign(
    owner: AuthorityOwner,
    scope: str,
    *,
    focused_actor: str | None = "operator",
    renderer="party",
    fail_at_x: int | None = None,
    entity_only: bool = False,
) -> None:
    runtime_path = f"packages/{PACKAGE}/runtime/ruleset.yaml"
    with zipfile.ZipFile(io.BytesIO(_installed_archive())) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        runtime = yaml.safe_load(archive.read(runtime_path))

    def reference(key):
        return {"namespace": PACKAGE, "key": f"state.{key}"}

    if scope == "single":
        runtime["model_decision_boundary"] = {
            "active_actor": reference("focused_actor"),
            "actor_entities": reference("actors"),
            "participant_control": {"field": "party_role", "player_values": ["player_character"]},
            "default_owner": "gm",
        }
    else:
        runtime["model_decision_scope"] = {
            "decision_owner": reference("owner"),
            "active_side": reference("side"),
            "eligible_actors": reference("eligible"),
            "focused_actor": reference("focused_actor"),
            "actor_entities": reference("actors"),
        }
    runtime["tools"]["move_unit"]["metadata"].update(
        player_controls={"actor_parameter": "actor"},
        result_card=CARD,
    )
    if fail_at_x is not None:
        runtime["tools"]["move_unit"]["effects"].append(
            {
                "condition": f"destination[0] == {fail_at_x}",
                "apply_condition": {"target": "missing-actor", "condition": "synthetic"},
            }
        )
    runtime["tools"]["hand_to_gm"] = {
        "description": "Hand this synthetic side to the GM.",
        "parameters": [],
        "effects": [{"set": {"field": "state.owner", "value": "'gm'"}}],
        "metadata": {"costs_action": False},
    }
    record = manifest["packages"][0]
    record["tools"].append(
        {
            **record["tools"][0],
            "name": "hand_to_gm",
            "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        }
    )
    party = deepcopy(record["ui_surfaces"][0])
    party.update(
        id="party",
        renderer=renderer,
        title="Party",
        source={
            "kind": "object",
            "fields": {
                "participants": {"kind": "participants", "kinds": ["character"]},
                "entities": {"kind": "state", **reference("actors")},
            },
        },
        actions=[
            {
                "id": "move",
                "label": "Move",
                "intent": "move_unit",
                "params": {"mode": "careful", "destination": [4, 4]},
                "bindings": {"actor": "actor"},
            }
        ],
    )
    party["actions"].append(
        {
            "id": "move-bravo",
            "label": "Move Bravo",
            "intent": "move_unit",
            "params": {"mode": "careful", "destination": [4, 4], "actor": "bravo"},
        }
    )
    record["ui_surfaces"].append(party)
    runtime_bytes = yaml.safe_dump(runtime, sort_keys=False).encode()
    record["files"][0].update(
        bytes=len(runtime_bytes),
        sha256=hashlib.sha256(runtime_bytes).hexdigest(),
    )
    record["content_sha256"] = content_root_for_record(record)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("manifest.json", canonical_json_bytes(manifest))
        archive.writestr(runtime_path, runtime_bytes)
    candidate = owner.install_package_archive(io.BytesIO(output.getvalue()), install_id="package")
    scene = _scene()
    scene["entities"].append(
        {**deepcopy(scene["entities"][0]), "id": "bravo", "position": [2.5, 1.5, 0]}
    )
    owner.create_campaign(
        CampaignCreate(
            campaign_id="campaign",
            display_name="Party control",
            writer=WriterRole.DM,
            package_lock=candidate.package_lock,
            turn_id="turn",
            participants=tuple(
                Participant(
                    actor,
                    "character",
                    PACKAGE,
                    "1.0.0",
                    {"name": actor.title(), "party_role": role},
                )
                for actor, role in [("operator", "player_character"), ("bravo", "companion")]
                if not entity_only or actor == "operator"
            ),
            initial_state=(
                StateValue(
                    PACKAGE,
                    "state",
                    {
                        "owner": "player",
                        "side": "allies",
                        "eligible": ["operator", "bravo"],
                        "focused_actor": focused_actor,
                        "actors": {
                            "operator": {
                                "name": "Operator",
                                "private_detail": "not a UI control field",
                            },
                            "bravo": {"name": "Bravo"},
                        },
                        "spatial_scene": scene,
                        "objectives": [],
                        "selection": {},
                        "preview": {},
                        "last_actor": None,
                        "last_destination": None,
                    },
                ),
            ),
        )
    )
    owner.create_agent_session(
        campaign_id="campaign",
        session_id="gm",
        role=WriterRole.DM,
        provider_id="test",
        model_id="test",
    )


def _party(owner, actor, revision=0):
    return owner.player_action(
        campaign_id="campaign",
        session_id="gm",
        operation_id=f"party-{actor}",
        expected_revision=revision,
        source={"kind": "panel", "surface_id": "party", "action_id": "move"},
        selection={"actor": actor},
    )


@pytest.mark.parametrize("control", ["panel", "result_card"])
def test_failed_player_action_reopens_with_origin_and_without_reexecution(tmp_path, control):
    database = tmp_path / "failed-action.sqlite3"
    with AuthorityOwner.open(database, "owner") as owner:
        _campaign(owner, "single", fail_at_x=4 if control == "panel" else 5)
        if control == "result_card":
            _party(owner, "operator")
        before = owner.snapshot("campaign")
        request = {
            "campaign_id": "campaign",
            "session_id": "gm",
            "operation_id": "failed-move",
            "expected_revision": before.revision,
            "source": {"kind": "panel", "surface_id": "party", "action_id": "move"},
            "selection": {"actor": "operator"},
        }
        if control == "result_card":
            request.update(
                source={"kind": "result_card", "action_id": "party-operator", "button_index": 0},
                selection=None,
            )
        receipt = owner.player_action(**request)
        failed = owner.snapshot("campaign")
        assert receipt.status is ActionStatus.FAILED
        assert receipt.result == {"status": "failed", "code": "tool_execution_failed"}
        assert failed.state == before.state
        assert failed.mutations == before.mutations
        (marker,) = [item for item in failed.consequences if item.action_id == "failed-move"]
        assert marker.payload["kind"] == "sylvia_player_action"
        assert marker.payload["origin"]["control"] == control

    with AuthorityOwner.open(database, "owner") as owner:
        assert owner.snapshot("campaign") == failed
        assert owner.player_action(**request) == receipt
        assert owner.snapshot("campaign") == failed
        saved = owner.player_action_context("campaign")[-1]
        assert saved["status"] == "failed"
        assert saved["origin"] == marker.payload["origin"]


@pytest.mark.parametrize("corruption", ["mechanic", "extra_effect", "revision", "policy"])
def test_failed_action_reopen_rejects_gameplay_or_invalid_origin(tmp_path, corruption):
    database = tmp_path / "invalid-failed-action.sqlite3"
    with AuthorityOwner.open(database, "owner") as owner:
        _campaign(owner, "single", fail_at_x=4)
        assert _party(owner, "operator").status is ActionStatus.FAILED

    with sqlite3.connect(database) as connection:
        payload = json.loads(
            connection.execute(
                "SELECT payload_json FROM consequences WHERE action_id = 'party-operator'",
            ).fetchone()[0]
        )
        if corruption == "mechanic":
            payload = {"kind": "damage", "amount": 3}
        elif corruption == "extra_effect":
            payload["damage"] = 3
        elif corruption == "revision":
            payload["origin"]["expected_revision"] = 7
        else:
            payload["origin"]["policy"]["response"] = "invented-policy"
        encoded = json.dumps(payload)
        connection.execute("UPDATE consequences SET payload_json = ?", (encoded,))
        connection.execute(
            "UPDATE canonical_events SET safe_payload_json = ? WHERE event_type = 'consequence'",
            (encoded,),
        )

    with pytest.raises(SchemaIdentityError, match="stored integrity"):
        AuthorityOwner.open(database, "owner")


@pytest.mark.parametrize("renderer", ["party", "unit_list"])
def test_single_character_control_rejects_a_visible_companion(tmp_path: Path, renderer):
    with AuthorityOwner.open(tmp_path / "single.sqlite3", "owner") as owner:
        _campaign(owner, "single", renderer=renderer)
        scope = owner.campaign_panels("campaign")["player_control"]
        assert scope["actors"] == [{"id": "operator", "name": "Operator"}]
        assert "private_detail" not in str(scope)
        party = next(
            panel
            for panel in owner.campaign_panels("campaign")["surfaces"]
            if panel["id"] == "party"
        )
        assert party["actions"][0]["actor_parameter"] == "actor"
        assert party["actions"][1]["available"] is False
        with pytest.raises(CommandRejected, match="not currently controlled"):
            _party(owner, "bravo")
        with pytest.raises(CommandRejected, match="party member is unavailable"):
            _party(owner, "hidden")
        assert not owner.snapshot("campaign").actions
        assert _party(owner, "operator").resulting_revision == 1


def test_map_and_fixed_party_actions_recheck_the_same_actor_scope(tmp_path: Path):
    with AuthorityOwner.open(tmp_path / "map.sqlite3", "owner") as owner:
        _campaign(owner, "single")
        source = {"kind": "panel", "surface_id": "operations.map", "action_id": "move"}
        with pytest.raises(CommandRejected, match="not currently controlled"):
            owner.player_action(
                campaign_id="campaign",
                session_id="gm",
                operation_id="map-bravo",
                expected_revision=0,
                source=source,
                selection={"actor": "bravo", "destination": [4, 4], "map_revision": 7},
            )
        with pytest.raises(CommandRejected, match="not currently controlled"):
            owner.player_action(
                campaign_id="campaign",
                session_id="gm",
                operation_id="fixed-bravo",
                expected_revision=0,
                source={"kind": "panel", "surface_id": "party", "action_id": "move-bravo"},
            )
        assert owner.snapshot("campaign").actions == ()
        result = owner.player_action(
            campaign_id="campaign",
            session_id="gm",
            operation_id="map-operator",
            expected_revision=0,
            source=source,
            selection={"actor": "operator", "destination": [4, 4], "map_revision": 7},
        )
        assert result.resulting_revision == 1


def test_single_character_control_remains_available_outside_an_active_encounter(tmp_path: Path):
    with AuthorityOwner.open(tmp_path / "exploration.sqlite3", "owner") as owner:
        _campaign(owner, "single", focused_actor=None)
        assert owner.model_decision_boundary("campaign") is None
        scope = owner.campaign_panels("campaign")["player_control"]
        assert scope == {
            "available": True,
            "focused_actor_id": None,
            "actors": [{"id": "operator", "name": "Operator"}],
        }
        with pytest.raises(CommandRejected, match="not currently controlled"):
            _party(owner, "bravo")
        assert _party(owner, "operator").resulting_revision == 1


def test_whole_squad_control_and_gm_handoff_update_party_and_old_cards(tmp_path: Path):
    database = tmp_path / "squad.sqlite3"
    with AuthorityOwner.open(database, "owner") as owner:
        _campaign(owner, "squad")
        actors = owner.campaign_panels("campaign")["player_control"]["actors"]
        assert [item["id"] for item in actors] == [
            "operator",
            "bravo",
        ]
        _party(owner, "operator")
        _party(owner, "bravo", 1)
        assert owner.snapshot("campaign").state[f"{PACKAGE}.state"]["last_actor"] == "bravo"
        owner.execute_action(
            ActionCommand(
                campaign_id="campaign",
                action_id="gm-phase",
                idempotency_key="gm-phase",
                owner_id="owner",
                writer=WriterRole.DM,
                expected_revision=2,
                turn_id="turn",
                package_id=PACKAGE,
                tool_name="hand_to_gm",
                parameters={},
            )
        )
    with AuthorityOwner.open(database, "owner") as owner:
        panels = owner.campaign_panels("campaign")
        assert panels["direct_actions_available"] is False
        assert panels["player_control"]["actors"] == []
        assert panels["response_action"]["available"] is True
        with pytest.raises(CommandRejected, match="requires a GM decision"):
            owner.player_action(
                campaign_id="campaign",
                session_id="gm",
                operation_id="another",
                expected_revision=3,
                source={
                    "kind": "result_card",
                    "action_id": "party-operator",
                    "button_index": 0,
                },
            )
        projection = next(
            item
            for item in owner.player_projection("campaign")
            if item.action_id == "party-operator"
        )
        assert projection.safe_payload["result_card_action_availability"] == [False]
        assert len(owner.snapshot("campaign").actions) == 3


@pytest.mark.parametrize("next_turn", ["a-next-turn", "z-next-turn"])
def test_controls_follow_the_open_turn_after_a_turn_without_state_changes(tmp_path, next_turn):
    database = tmp_path / "next-turn.sqlite3"
    with AuthorityOwner.open(database, "owner") as owner:
        _campaign(owner, "squad")
        owner.complete_turn("campaign", "turn")
        assert owner.catalog("campaign") == ()
        owner.open_turn("campaign", next_turn)
        assert owner.snapshot("campaign").revision == 0

    with AuthorityOwner.open(database, "owner") as owner:
        assert "move_unit" in {tool.name for tool in owner.catalog("campaign")}
        party = next(
            surface
            for surface in owner.campaign_panels("campaign")["surfaces"]
            if surface["id"] == "party"
        )
        assert party["actions"][0]["available"] is True
        assert _party(owner, "operator").resulting_revision == 1


@pytest.mark.parametrize("renderer", ["party", "unit_list"])
def test_entity_only_controls_keep_party_membership_and_player_scope_fences(tmp_path, renderer):
    with AuthorityOwner.open(tmp_path / "entity-only.sqlite3", "owner") as owner:
        _campaign(owner, "squad", renderer=renderer, entity_only=True)
        before = owner.snapshot("campaign")
        if renderer == "party":
            with pytest.raises(CommandRejected, match="party member is unavailable"):
                _party(owner, "bravo")
            assert owner.snapshot("campaign") == before
        else:
            assert _party(owner, "bravo").resulting_revision == 1
    with AuthorityOwner.open(tmp_path / "single-entity.sqlite3", "owner") as owner:
        _campaign(owner, "single", renderer=renderer, entity_only=True)
        before = owner.snapshot("campaign")
        with pytest.raises(
            CommandRejected, match="not currently controlled|party member is unavailable"
        ):
            _party(owner, "bravo")
        assert owner.snapshot("campaign") == before
