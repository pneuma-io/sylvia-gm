"""Interrupted ordinary movement retains the actual route's terrain-weighted cost."""

import hashlib
import json
from unittest.mock import patch

import pytest

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner, ResolveMechanicsChoiceCommand
from sylvia_gm.domain.authority import thaw_json_value
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.spatial import SpatialEngine
from tests.admitted_library_fixture import admitted_fixture_owner
from tests.test_td_exposure_ownership import NS, game_for
from tests.test_td_ordinary_movement import SESSION, map_request, ordinary_call, prepare_owner
from tests.td_source_fixture import candidate as candidate_fixture

candidate = candidate_fixture


def weighted_scene(candidate, mirrored=False):
    scene = game_for(candidate, mirrored, "empty", wall_end=5.71)
    scene.world["spatial_scene"]["zones"][0]["shape"]["min"][1] = 6.5
    scene.world["spatial_scene"]["terrain"] = [
        {"cell": [x, y], "layer_id": "floor", "cost": 3} for x in range(8, 14) for y in range(3, 11)
    ]
    scene.actors[scene.mover].update(hp=1, res=0)
    return scene


def test_ordinary_admitted_hit_at_weighted_cell_centre_pays_actual_prefix(candidate, tmp_path):
    scene = weighted_scene(candidate)
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3",
        "aim-owner",
        candidate.read_bytes(),
    ) as (owner, admitted):
        game = prepare_owner(owner, admitted, scene)
        before = owner.snapshot(game.campaign)
        call = ordinary_call(owner, game, "chat")
        with patch.object(dice._rng, "randint", return_value=19) as draws:
            receipt = call()
        assert str(receipt.status) == "committed"
        after = owner.snapshot(game.campaign)
        actor = after.state[NS + ".entities"][game.mover]
        state = after.state[NS + ".state"]
        assert draws.call_count == 1
        assert actor["downed"] and state["last_trigger"]["suppression"]["target_reached"]
        assert state["last_trigger"]["point"] == (10.5, 6.5, 0.9)
        spatial = thaw_json_value(before.state[NS + ".state"]["spatial_scene"])
        prefix = (
            SpatialEngine(spatial)
            .query(
                {
                    "kind": "path",
                    "map_id": spatial["map"]["id"],
                    "start": [10, 5],
                    "goal": [10, 6],
                    "allow_diagonal": True,
                    "corner_policy": "forbid_cutting",
                    "include_entities": True,
                    "moving_entity_id": game.mover,
                    "max_steps": 256,
                }
            )
            .as_payload()
        )
        assert prefix["cost"] == 3
        spent = before.state[NS + ".entities"][game.mover]["ap"] - actor["ap"]
        witness = {
            "package_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
            "before_ap": before.state[NS + ".entities"][game.mover]["ap"],
            "after_ap": actor["ap"],
            "actual_prefix": prefix,
            "last_move": thaw_json_value(state["last_move"]),
            "last_trigger": thaw_json_value(state["last_trigger"]),
            "downed": actor["downed"],
            "spent": spent,
            "expected_spent": 3,
        }
        (tmp_path / "witness.json").write_bytes((json.dumps(witness, indent=2) + "\n").encode())
        assert spent == 3
        assert state["last_move"]["cost"] == 3
        assert state["last_trigger"]["travelled_distance"] == 1
        export = tmp_path / "weighted.sylvia-gm-campaign"
        owner.export_campaign("weighted-export", game.campaign, export)
    with AuthorityOwner.open(tmp_path / "authority.sqlite3", "aim-owner") as owner:
        owner.set_admin_full_audit(True)
        game.replay(owner, after)
        with patch.object(dice._rng, "randint") as draws:
            assert call(owner) == receipt
        assert draws.call_count == 0 and owner.snapshot(game.campaign) == after
    with AuthorityOwner.open(tmp_path / "fresh.sqlite3", "aim-owner") as owner:
        owner.set_admin_full_audit(True)
        recovered = owner.recover_campaign(recovery_id="weighted", source=export)
        assert owner.recover_campaign(recovery_id="weighted", source=export) == recovered
        with patch.object(dice._rng, "randint") as draws:
            assert call(owner) == receipt
        assert draws.call_count == 0 and owner.snapshot(game.campaign) == after


@pytest.mark.parametrize(
    "mode,face,cost,ap",
    [
        ("map", 19, 3, 3),
        ("queue", 19, 3, 3),
        ("map", 1, 6, 6),
        ("transition-centre", 19, 3, 3),
        ("uniform-subcell", 19, 2.25, 3),
        ("diagonal-centre", 19, 2 * 2**0.5, 3),
        ("diagonal-centre", 1, 4 * 2**0.5, 6),
    ],
)
def test_weighted_ordinary_map_queue_completion_and_boundaries(
    candidate, tmp_path, mode, face, cost, ap
):
    scene = weighted_scene(candidate, True)
    destination = [10, 7]
    if mode == "transition-centre":
        next(row for row in scene.world["spatial_scene"]["terrain"] if row["cell"] == [10, 5])[
            "cost"
        ] = 1
    elif mode == "uniform-subcell":
        scene.world["spatial_scene"]["zones"][0]["shape"]["min"][1] = 6.25
    elif mode == "diagonal-centre":
        for row in scene.world["spatial_scene"]["terrain"]:
            row["cost"] = 2
        scene.world["spatial_scene"]["zones"][0]["shape"]["min"] = [11.5, 6.5, 0]
        destination = [12, 7]
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3",
        "aim-owner",
        candidate.read_bytes(),
    ) as (owner, admitted):
        game = prepare_owner(owner, admitted, scene)
        before = owner.snapshot(game.campaign)
        command = map_request(owner, game, destination=destination)
        if mode == "queue":
            step = {
                "step_id": "weighted",
                "source": {
                    "kind": "panel",
                    "surface_id": "operations.map",
                    "action_id": "move",
                },
                "selection": command["selection"],
            }
            queue = owner.compose_action_sequence(
                campaign_id=game.campaign,
                session_id=SESSION,
                sequence_id="weighted",
                expected_revision=before.revision,
                steps=[
                    step,
                    {
                        **step,
                        "step_id": "return",
                        "selection": {**step["selection"], "destination": [10, 5]},
                    },
                ],
            )

            def call():
                return owner.execute_action_sequence(
                    campaign_id=game.campaign,
                    session_id=SESSION,
                    sequence_id="weighted",
                    expected_version=queue["version"],
                    confirmation=queue["confirmation"],
                )
        else:
            owner.preview_panel_action(**command)

            def call():
                return owner.execute_panel_action(**command)

        assert owner.snapshot(game.campaign) == before
        with patch.object(dice._rng, "randint", return_value=face) as draws:
            receipt = call()
        if mode != "queue":
            assert str(receipt.status) == "committed"
        assert draws.call_count == 1
        after = owner.snapshot(game.campaign)
        actor = after.state[NS + ".entities"][game.mover]
        state = after.state[NS + ".state"]
        assert actor["ap"] == before.state[NS + ".entities"][game.mover]["ap"] - ap
        assert state["last_move"]["cost"] == pytest.approx(cost)
        assert state["prepared"]["suppression"]["capacity"] == 1
        assert (
            after.state[NS + ".entities"][game.shooter]
            == before.state[NS + ".entities"][game.shooter]
        )
        assert after.revision == before.revision + 1
        if face == 19:
            assert actor["downed"] and state["last_trigger"]["suppression"]["target_reached"]
            assert state["last_move"]["status"] == "INTERRUPTED_BY_PREPARED_FIRE"
        else:
            assert not actor["downed"] and actor["position_cell"] == tuple(destination)
            assert state["last_move"]["status"] == "MOVED"
        if mode == "queue":
            assert receipt["steps"][0]["status"] == "committed"
            assert receipt["steps"][1]["status"] == "pending"
        with patch.object(dice._rng, "randint") as draws:
            assert call() == receipt
        assert draws.call_count == 0 and owner.snapshot(game.campaign) == after


def test_weighted_pending_choice_fresh_recovery_retains_exact_prefix(candidate, tmp_path):
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3",
        "aim-owner",
        candidate.read_bytes(),
    ) as (owner, admitted):
        game = prepare_owner(owner, admitted, weighted_scene(candidate, True))
        before = owner.snapshot(game.campaign)
        call = ordinary_call(owner, game, "map")
        with patch.object(dice._rng, "randint", return_value=20):
            pending = call()
        assert str(pending.status) == "awaiting_choice" and owner.snapshot(game.campaign) == before
        export = tmp_path / "pending.sylvia-gm-campaign"
        owner.export_campaign("pending-weighted", game.campaign, export)
    with AuthorityOwner.open(tmp_path / "fresh.sqlite3", "aim-owner") as owner:
        owner.set_admin_full_audit(True)
        owner.recover_campaign(recovery_id="pending-weighted", source=export)
        assert owner.snapshot(game.campaign) == before
        resolution = ResolveMechanicsChoiceCommand(
            campaign_id=game.campaign,
            action_id=pending.action_id,
            resolution_id="weighted-choice",
            owner_id="aim-owner",
            expected_revision=before.revision,
            choice_id=pending.choice.choice_id,
            option_id="DEEP_HIT",
            session_id=SESSION,
        )
        with patch.object(dice._rng, "randint", return_value=20):
            receipt = owner.resolve_mechanics_choice(resolution)
        after = owner.snapshot(game.campaign)
        actor = after.state[NS + ".entities"][game.mover]
        assert (
            actor["downed"] and actor["ap"] == before.state[NS + ".entities"][game.mover]["ap"] - 3
        )
        assert after.state[NS + ".state"]["last_move"]["cost"] == 3
        with patch.object(dice._rng, "randint") as draws:
            assert owner.resolve_mechanics_choice(resolution) == receipt
            assert call(owner) == receipt
        assert draws.call_count == 0 and owner.snapshot(game.campaign) == after


def test_weight_change_within_edge_rejects_before_shots_or_spend(candidate, tmp_path):
    scene = weighted_scene(candidate, True)
    scene.world["spatial_scene"]["zones"][0]["shape"]["min"][1] = 6.25
    next(row for row in scene.world["spatial_scene"]["terrain"] if row["cell"] == [10, 5])[
        "cost"
    ] = 1
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3",
        "aim-owner",
        candidate.read_bytes(),
    ) as (owner, admitted):
        game = prepare_owner(owner, admitted, scene)
        before = owner.snapshot(game.campaign)
        call = ordinary_call(owner, game, "map")
        with (
            patch.object(dice._rng, "randint") as draws,
            pytest.raises(
                CommandRejected,
                match="partial-edge cost policy is unspecified",
            ),
        ):
            call()
        assert draws.call_count == 0 and owner.snapshot(game.campaign) == before


def test_weighted_zero_distance_exposure_spends_no_movement_ap(candidate, tmp_path):
    scene = game_for(candidate, True, "visible", wall_end=5.71)
    scene.world["spatial_scene"]["terrain"] = [
        {"cell": [x, y], "layer_id": "floor", "cost": 3} for x in range(8, 14) for y in range(3, 11)
    ]
    scene.actors[scene.mover].update(hp=1, res=0)
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3",
        "aim-owner",
        candidate.read_bytes(),
    ) as (owner, admitted):
        with patch.object(dice._rng, "randint", return_value=1):
            game = prepare_owner(owner, admitted, scene)
        before = owner.snapshot(game.campaign)
        command = map_request(owner, game, destination=[10, 6])
        with patch.object(dice._rng, "randint", return_value=19) as draws:
            receipt = owner.execute_panel_action(**command)
        assert str(receipt.status) == "committed" and draws.call_count == 1
        after = owner.snapshot(game.campaign)
        actor = after.state[NS + ".entities"][game.mover]
        assert actor["downed"]
        assert actor["ap"] == before.state[NS + ".entities"][game.mover]["ap"]
        assert after.state[NS + ".state"]["last_move"]["cost"] == 0
        assert after.state[NS + ".state"].get("network_motion") == before.state[NS + ".state"].get(
            "network_motion"
        )
