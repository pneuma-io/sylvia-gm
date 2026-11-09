"""The admitted explicit crossing tool prices the actual retained movement route."""

import hashlib
import json
from unittest.mock import patch

import pytest

from sylvia_gm import dice
from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import WriterRole, thaw_json_value
from tests.admitted_library_fixture import admitted_fixture_owner
from tests.test_td_exposure_ownership import NS, game_for
from tests.test_td_ordinary_movement import SESSION, prepare_owner
from tests.td_source_fixture import candidate as candidate_fixture
from tests.test_td_weighted_move import weighted_scene

candidate = candidate_fixture


def explicit_call(owner, game, *, destination=None, first="SUPPRESSION"):
    tool = next(
        row
        for row in StatelessMcpMechanics(owner).list_tools(game.campaign)
        if row.package_id == NS and row.name == "resolve_trigger_crossing"
    )
    assert set(tool.input_schema["properties"]) == {
        "actor",
        "overwatcher",
        "suppressor",
        "destination",
        "first_effect",
        "overwatch_critical_effect",
        "suppression_critical_effect",
    }
    command = ActionCommand(
        campaign_id=game.campaign,
        action_id="explicit-crossing",
        idempotency_key="explicit-crossing",
        owner_id="aim-owner",
        writer=WriterRole.DM,
        expected_revision=owner.snapshot(game.campaign).revision,
        turn_id="turn",
        package_id=NS,
        tool_name=tool.name,
        parameters={
            "actor": game.mover,
            "overwatcher": game.shooter,
            "suppressor": game.shooter,
            "destination": destination or [10, 7],
            "first_effect": first,
            "overwatch_critical_effect": "DEEP_HIT",
            "suppression_critical_effect": "DEEP_HIT",
        },
        initiating_session_id=SESSION,
    )
    return lambda current=None: StatelessMcpMechanics(current or owner).execute_action(command)


def test_admitted_explicit_downing_pays_retained_weighted_prefix(candidate, tmp_path):
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3",
        "aim-owner",
        candidate.read_bytes(),
    ) as (owner, admitted):
        game = prepare_owner(owner, admitted, weighted_scene(candidate))
        before = owner.snapshot(game.campaign)
        call = explicit_call(owner, game)
        with patch.object(dice._rng, "randint", return_value=19) as draws:
            receipt = call()
        assert str(receipt.status) == "committed" and draws.call_count == 1
        after = owner.snapshot(game.campaign)
        actor = after.state[NS + ".entities"][game.mover]
        trigger = after.state[NS + ".state"]["last_trigger"]
        assert actor["downed"] and trigger["suppression"]["target_reached"]
        assert trigger["point"] == (10.5, 6.5, 0.9)
        assert trigger["first_effect"] == "SUPPRESSION" and trigger["status"] == "INTERRUPTED"
        spent = before.state[NS + ".entities"][game.mover]["ap"] - actor["ap"]
        (tmp_path / "witness.json").write_bytes(
            (
                json.dumps(
                    {
                        "package_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
                        "before_ap": before.state[NS + ".entities"][game.mover]["ap"],
                        "after_ap": actor["ap"],
                        "spent": spent,
                        "expected_spent": 3,
                        "downed": actor["downed"],
                        "trigger": thaw_json_value(trigger),
                        "shooter_unchanged": after.state[NS + ".entities"][game.shooter]
                        == before.state[NS + ".entities"][game.shooter],
                    },
                    indent=2,
                )
                + "\n"
            ).encode()
        )
        assert spent == 3
        assert trigger["travelled_cost"] == 3 and trigger["travelled_distance"] == 1
        assert (
            after.state[NS + ".entities"][game.shooter]
            == before.state[NS + ".entities"][game.shooter]
        )
        assert after.state[NS + ".state"]["prepared"]["suppression"]["capacity"] == 1
        export = tmp_path / "explicit.sylvia-gm-campaign"
        owner.export_campaign("explicit-export", game.campaign, export)
    with AuthorityOwner.open(tmp_path / "authority.sqlite3", "aim-owner") as owner:
        owner.set_admin_full_audit(True)
        with patch.object(dice._rng, "randint") as draws:
            assert call(owner) == receipt
        assert draws.call_count == 0 and owner.snapshot(game.campaign) == after
    with AuthorityOwner.open(tmp_path / "fresh.sqlite3", "aim-owner") as owner:
        owner.set_admin_full_audit(True)
        recovered = owner.recover_campaign(recovery_id="explicit", source=export)
        assert owner.recover_campaign(recovery_id="explicit", source=export) == recovered
        with patch.object(dice._rng, "randint") as draws:
            assert call(owner) == receipt
        assert draws.call_count == 0 and owner.snapshot(game.campaign) == after


@pytest.mark.parametrize("mirrored", [False, True])
@pytest.mark.parametrize(
    "scenario,face,cost,ap",
    [
        ("full", 1, 6, 6),
        ("subcell", 19, 2.25, 3),
        ("transition-centre", 19, 3, 3),
        ("diagonal", 19, 2 * 2**0.5, 3),
        ("diagonal", 1, 4 * 2**0.5, 6),
        ("critical", 20, 3, 3),
        ("cheap-full", 1, 0.5, 1),
    ],
)
def test_explicit_weighted_order_and_exact_once_costs(
    candidate, tmp_path, mirrored, scenario, face, cost, ap
):
    scene = weighted_scene(candidate, mirrored)
    destination = [10, 7]
    if scenario == "subcell":
        scene.world["spatial_scene"]["zones"][0]["shape"]["min"][1] = 6.25
    elif scenario == "transition-centre":
        next(row for row in scene.world["spatial_scene"]["terrain"] if row["cell"] == [10, 5])[
            "cost"
        ] = 1
    elif scenario == "diagonal":
        for row in scene.world["spatial_scene"]["terrain"]:
            row["cost"] = 2
        scene.world["spatial_scene"]["zones"][0]["shape"]["min"] = [11.5, 6.5, 0]
        destination = [12, 7]
    elif scenario == "cheap-full":
        for row in scene.world["spatial_scene"]["terrain"]:
            row["cost"] = 0.25
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = prepare_owner(owner, admitted, scene)
        before = owner.snapshot(game.campaign)
        first = "OVERWATCH" if mirrored else "SUPPRESSION"
        call = explicit_call(owner, game, destination=destination, first=first)
        with patch.object(dice._rng, "randint", return_value=face) as draws:
            receipt = call()
        assert str(receipt.status) == "committed" and draws.call_count == 1
        after = owner.snapshot(game.campaign)
        actor = after.state[NS + ".entities"][game.mover]
        trigger = after.state[NS + ".state"]["last_trigger"]
        assert actor["ap"] == before.state[NS + ".entities"][game.mover]["ap"] - ap
        assert trigger["travelled_cost"] == pytest.approx(cost)
        assert trigger["movement_ap_spent"] == ap
        assert trigger["movement_cost_status"] == "ROUTE_WEIGHTED"
        assert trigger["first_effect"] == first
        assert (
            after.state[NS + ".entities"][game.shooter]
            == before.state[NS + ".entities"][game.shooter]
        )
        assert after.state[NS + ".state"]["prepared"]["suppression"]["capacity"] == 1
        assert actor["downed"] == (face > 1)
        assert trigger["status"] == ("INTERRUPTED" if face > 1 else "RESUMED")
        if face > 1:
            assert trigger["suppression"]["target_reached"]
        else:
            assert actor["position_cell"] == tuple(destination)
        with patch.object(dice._rng, "randint") as draws:
            assert call() == receipt
        assert draws.call_count == 0 and owner.snapshot(game.campaign) == after


@pytest.mark.parametrize("mirrored", [False, True])
def test_explicit_initial_exposure_downing_spends_zero_movement_ap(candidate, tmp_path, mirrored):
    scene = game_for(candidate, mirrored, "visible", wall_end=5.71)
    scene.actors[scene.mover].update(hp=1, res=0)
    scene.world["spatial_scene"]["terrain"] = [
        {"cell": [x, y], "layer_id": "floor", "cost": 3} for x in range(8, 14) for y in range(3, 11)
    ]
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        with patch.object(dice._rng, "randint", return_value=1):
            game = prepare_owner(owner, admitted, scene)
        before = owner.snapshot(game.campaign)
        call = explicit_call(owner, game, destination=[10, 6])
        with patch.object(dice._rng, "randint", return_value=19) as draws:
            receipt = call()
        assert str(receipt.status) == "committed" and draws.call_count == 1
        after = owner.snapshot(game.campaign)
        actor = after.state[NS + ".entities"][game.mover]
        trigger = after.state[NS + ".state"]["last_trigger"]
        assert actor["downed"] and trigger["suppression"]["target_reached"]
        assert actor["ap"] == before.state[NS + ".entities"][game.mover]["ap"]
        assert (
            trigger["travelled_cost"]
            == trigger["travelled_distance"]
            == trigger["movement_ap_spent"]
            == 0
        )
        with patch.object(dice._rng, "randint") as draws:
            assert call() == receipt
        assert draws.call_count == 0 and owner.snapshot(game.campaign) == after


@pytest.mark.parametrize("destination,status", [([10, 8], "INSUFFICIENT_AP"), ([5, 5], "NO_PATH")])
def test_explicit_unaffordable_or_blocked_path_keeps_resources(
    candidate, tmp_path, destination, status
):
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = prepare_owner(owner, admitted, weighted_scene(candidate))
        before = owner.snapshot(game.campaign)
        call = explicit_call(owner, game, destination=destination)
        with patch.object(dice._rng, "randint") as draws:
            receipt = call()
        assert str(receipt.status) == "committed" and draws.call_count == 0
        after = owner.snapshot(game.campaign)
        actual, expected = thaw_json_value(after.state), thaw_json_value(before.state)
        assert actual[NS + ".state"]["last_trigger"]["status"] == status
        actual[NS + ".state"].pop("last_trigger", None)
        expected[NS + ".state"].pop("last_trigger", None)
        assert actual == expected


@pytest.mark.parametrize("face", [1, 19])
def test_explicit_unresolved_transition_preserves_and_labels_legacy_charge(
    candidate, tmp_path, face
):
    scene = weighted_scene(candidate)
    scene.world["spatial_scene"]["zones"][0]["shape"]["min"][1] = 6.25
    next(row for row in scene.world["spatial_scene"]["terrain"] if row["cell"] == [10, 5])[
        "cost"
    ] = 1
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = prepare_owner(owner, admitted, scene)
        before = owner.snapshot(game.campaign)
        call = explicit_call(owner, game)
        with patch.object(dice._rng, "randint", return_value=face) as draws:
            receipt = call()
        assert str(receipt.status) == "committed" and draws.call_count == 1
        after = owner.snapshot(game.campaign)
        trigger = after.state[NS + ".state"]["last_trigger"]
        # This is preserved legacy behavior, not acceptance of a fractional policy.
        assert trigger["movement_cost_status"] == "UNRESOLVED_TERRAIN_TRANSITION_LEGACY_AP"
        assert trigger["movement_ap_spent"] == (1 if face == 19 else 6)
        assert trigger["travelled_cost"] == (None if face == 19 else 6)
        assert (
            after.state[NS + ".entities"][game.mover]["ap"]
            == before.state[NS + ".entities"][game.mover]["ap"] - trigger["movement_ap_spent"]
        )
        card = thaw_json_value(receipt.result)["result_card"]
        assert (
            next(row["value"] for row in card["details"] if row["label"] == "Cost qualification")
            == "Terrain-transition fraction unresolved; legacy AP charging retained"
        )
