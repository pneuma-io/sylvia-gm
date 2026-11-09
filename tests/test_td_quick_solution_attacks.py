"""Targeted FPV and offensive CQC count as performed attacks, not raw snapshots."""

import json
from copy import deepcopy
from unittest.mock import patch

import pytest

from sylvia_gm import dice
from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import WriterRole
from sylvia_gm.domain.errors import CommandRejected, StaleRevision
from tests.admitted_library_fixture import admitted_fixture_owner
from tests.test_td_deep_aim import map_request
from tests.test_td_ordinary_movement import SESSION
from tests.test_td_quick_solution import NS, STATE, aim, scene_for
from tests.test_td_rifleman_entry_boundaries import start
from tests.td_source_fixture import candidate as candidate_fixture

candidate = candidate_fixture


def perform(owner, game, tool, parameters, face=1, expected_status="committed"):
    """Use the actual admitted GM/MCP consumer and retain its durable replay."""
    identity = f"targeted-{tool}-{owner.snapshot(game.campaign).revision}"
    command = ActionCommand(
        campaign_id=game.campaign,
        action_id=identity,
        idempotency_key=identity,
        owner_id="aim-owner",
        writer=WriterRole.DM,
        expected_revision=owner.snapshot(game.campaign).revision,
        turn_id="turn",
        package_id=NS,
        tool_name=tool,
        parameters=parameters,
        initiating_session_id=f"{SESSION}-{game.campaign}",
    )
    with patch.object(dice._rng, "randint", side_effect=lambda lo, hi: min(face, hi)) as draws:
        receipt = StatelessMcpMechanics(owner).execute_action(command)
    assert str(receipt.status) == expected_status
    saved = owner.snapshot(game.campaign)

    def replay(current=None):
        return (current or owner).execute_action(command)

    with patch.object(dice._rng, "randint") as replay_draws:
        assert replay() == receipt
    assert replay_draws.call_count == 0 and owner.snapshot(game.campaign) == saved
    game.entry_calls.append((replay, receipt))
    return draws.call_count


def fpv_scene(candidate, mirrored=False, enabled=True):
    scene = scene_for(candidate, enabled=enabled, mirrored=mirrored)
    scene.mover = "drone_target"
    scene.actors["fpv_alpha"]["controlled_by"] = scene.shooter
    return scene


def fpv_parameters(game):
    return {
        "actor": game.shooter,
        "drone": "fpv_alpha",
        "target": game.mover,
        "landing_point": [31.0, 30.5, 0.5],
    }


def cqc_scene(candidate, mirrored=False, enabled=True):
    scene = scene_for(candidate, enabled=enabled, mirrored=mirrored)
    target = scene.actors[scene.mover]
    dx, dy = 3 - target["position_cell"][0], 2 - target["position_cell"][1]
    target["position_cell"] = [3, 2]
    for key in ("center", "muzzle"):
        target[key][0] += dx
        target[key][1] += dy
    body = next(row for row in scene.world["spatial_scene"]["entities"] if row["id"] == scene.mover)
    body["position"][0] += dx
    body["position"][1] += dy
    return scene


def witness(path, game, before, kind):
    path.write_text(
        json.dumps(
            {
                "kind": kind,
                "before_aim": before,
                "after_aim": game.actors[game.shooter],
                "world": game.world,
                "receipts": [str(receipt.status) for _, receipt in game.entry_calls],
            },
            indent=2,
        )
        + "\n"
    )


@pytest.mark.parametrize("mirrored", [False, True])
@pytest.mark.parametrize("face", [1, 20])
@pytest.mark.parametrize("enabled", [False, True])
def test_fpv_performed_attack_blocks_quick_price(candidate, tmp_path, mirrored, face, enabled):
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = start(owner, admitted, fpv_scene(candidate, mirrored, enabled))
        weapon = deepcopy(game.actors[game.shooter]["weapon"])
        others = deepcopy(
            {
                k: v
                for k, v in game.actors.items()
                if k not in {game.shooter, game.mover, "fpv_alpha"}
            }
        )
        assert perform(owner, game, "fpv_fragmentation_strike", fpv_parameters(game), face) == 2
        assert game.world["last_drone_attack"]["status"] == "DETONATED"
        assert game.world["last_drone_attack"]["placement_success"] is (face == 20)
        assert others == {k: v for k, v in game.actors.items() if k in others}
        assert game.actors["fpv_alpha"]["payload"] is None
        assert not game.actors["fpv_alpha"]["active"]
        before = deepcopy(game.actors[game.shooter])
        assert before["ap"] == 5 and before["weapon"] == weapon
        aim(owner, game)
        witness(tmp_path / "fpv-witness.json", game, before, "performed FPV then AIM2")
        assert game.actors[game.shooter]["ap"] == before["ap"] - 2
        assert before[STATE] == {"aimed_targets": [], "attacked_targets": [game.mover]}
        assert game.actors[game.shooter]["weapon"] == weapon


@pytest.mark.parametrize(
    "maneuver", ["TRIP", "DISARM", "BODY_CONTROL", "WEAPON_CONTROL", "SHOVE", "THROW"]
)
@pytest.mark.parametrize("face", [1, 20])
@pytest.mark.parametrize("mirrored", [False, True])
def test_offensive_cqc_blocks_quick_price(candidate, tmp_path, maneuver, face, mirrored):
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = start(owner, admitted, cqc_scene(candidate, mirrored))
        weapon = deepcopy(game.actors[game.shooter]["weapon"])
        args = {"actor": game.shooter, "target": game.mover, "maneuver": maneuver}
        tool = "resolve_cqc"
        if maneuver in {"SHOVE", "THROW"}:
            tool = "resolve_cqc_displacement"
            args["destination"] = [4, 2]
        elif maneuver in {"DISARM", "WEAPON_CONTROL"}:
            args["held_item"] = "PRIMARY_WEAPON"
        assert perform(owner, game, tool, args, face) == 1
        assert game.world["last_cqc"]["status"] == ("COMMITTED" if face == 20 else "FAILED_CHECK")
        assert game.world["last_cqc"]["success"] is (face == 20)
        before = deepcopy(game.actors[game.shooter])
        assert before["ap"] == 4 and before["weapon"] == weapon
        aim(owner, game)
        witness(
            tmp_path / "cqc-witness.json",
            game,
            before,
            f"performed {maneuver} face{face} then AIM2",
        )
        assert game.actors[game.shooter]["ap"] == before["ap"] - 2
        assert before[STATE] == {"aimed_targets": [], "attacked_targets": [game.mover]}
        assert game.actors[game.shooter]["weapon"] == weapon


@pytest.mark.parametrize("mirrored", [False, True])
@pytest.mark.parametrize("face", [1, 20])
def test_defensive_escape_spends_but_does_not_count_as_attack(candidate, tmp_path, mirrored, face):
    scene = cqc_scene(candidate, mirrored)
    scene.actors[scene.shooter]["control"] = {"controller": scene.mover, "focus": "BODY_CONTROL"}
    scene.actors[scene.mover]["control"] = {"target": scene.shooter, "focus": "BODY_CONTROL"}
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = start(owner, admitted, scene)
        assert (
            perform(
                owner,
                game,
                "resolve_cqc",
                {"actor": game.shooter, "target": game.mover, "maneuver": "ESCAPE"},
                face,
            )
            == 1
        )
        assert game.world["last_cqc"]["success"] is (face == 20)
        before = deepcopy(game.actors[game.shooter])
        assert before["ap"] < 6 and STATE not in before
        aim(owner, game)
        assert game.actors[game.shooter]["ap"] == before["ap"] - 1
        assert game.actors[game.shooter][STATE] == {
            "aimed_targets": [game.mover],
            "attacked_targets": [],
        }


def test_refused_fpv_does_not_record_performed_attack(candidate, tmp_path):
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        for case in [
            "control",
            "payload",
            "inactive-drone",
            "inactive-target",
            "no-ap",
            "wrong-side",
        ]:
            scene = fpv_scene(candidate)
            if case == "control":
                scene.actors["fpv_alpha"]["controlled_by"] = "hex"
            if case == "payload":
                scene.actors["fpv_alpha"]["payload"] = None
            if case == "inactive-drone":
                scene.actors["fpv_alpha"]["active"] = False
            if case == "inactive-target":
                scene.actors[scene.mover]["active"] = False
            if case == "no-ap":
                scene.actors[scene.shooter]["ap"] = 0
            if case == "wrong-side":
                scene.world["active_side"] = "hostiles"
            game = start(owner, admitted, scene, campaign=case)
            before = deepcopy(game.actors)
            assert perform(owner, game, "fpv_fragmentation_strike", fpv_parameters(game)) == 0
            assert game.actors == before and STATE not in game.actors[game.shooter]
            snapshot = owner.snapshot(game.campaign)
            assert (
                perform(
                    owner,
                    game,
                    "fpv_fragmentation_strike",
                    {**fpv_parameters(game), "drone": "not-a-unit"},
                    expected_status="failed",
                )
                == 0
            )
            failed = owner.snapshot(game.campaign)
            assert failed.state == snapshot.state
            assert failed.revision == snapshot.revision + 1
            assert len(failed.actions) == len(snapshot.actions) + 1
            assert game.actors == before and STATE not in game.actors[game.shooter]


def test_invalid_cqc_and_raw_snapshot_do_not_invent_attack(candidate, tmp_path):
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        for case in [
            "reach",
            "no-ap",
            "held-item",
            "inactive-target",
            "destination",
            "escape-no-hold",
        ]:
            scene = scene_for(candidate) if case == "reach" else cqc_scene(candidate)
            args = {"actor": scene.shooter, "target": scene.mover, "maneuver": "TRIP"}
            tool = "resolve_cqc"
            if case == "no-ap":
                scene.actors[scene.shooter]["ap"] = 0
            if case == "held-item":
                args["maneuver"] = "DISARM"
            if case == "inactive-target":
                scene.actors[scene.mover]["active"] = False
            if case == "destination":
                tool = "resolve_cqc_displacement"
                args.update(maneuver="SHOVE", destination=[3, 2])
            if case == "escape-no-hold":
                args["maneuver"] = "ESCAPE"
            game = start(owner, admitted, scene, campaign=case)
            before = deepcopy(game.actors)
            assert perform(owner, game, tool, args) == 0
            assert game.actors == before and STATE not in game.actors[game.shooter]
        game = start(owner, admitted, scene_for(candidate), campaign="raw-snapshot")
        before = deepcopy(game.actors)
        assert (
            perform(
                owner,
                game,
                "resolve_blast_snapshot",
                {"actor": game.shooter, "center": [6.25, 4.5, 0.0], "radius": 3.0},
            )
            == 0
        )
        assert game.world["last_blast"]["status"] == "SNAPSHOT_RESOLVED"
        assert game.actors == before
        aim(owner, game)
        assert game.actors[game.shooter]["ap"] == 5


def test_fpv_failed_geometry_after_roll_rolls_back_history_and_resources(candidate, tmp_path):
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = start(owner, admitted, fpv_scene(candidate))
        before = owner.snapshot(game.campaign)
        assert (
            perform(
                owner,
                game,
                "fpv_fragmentation_strike",
                {
                    **fpv_parameters(game),
                    "landing_point": [5000.0, 30.5, 0.5],
                },
                expected_status="failed",
            )
            == 2
        )
        failed = owner.snapshot(game.campaign)
        assert failed.state == before.state and failed.revision == before.revision + 1
        assert STATE not in game.actors[game.shooter]
        aim(owner, game)
        assert game.actors[game.shooter]["ap"] == 5


@pytest.mark.parametrize("kind", ["fpv", "cqc"])
def test_targeted_attack_reopen_fresh_recovery_and_replay(candidate, tmp_path, kind):
    database, exported = tmp_path / "authority.sqlite3", tmp_path / "attacked.sylvia-gm-campaign"
    with admitted_fixture_owner(database, "aim-owner", candidate.read_bytes()) as (owner, admitted):
        scene = fpv_scene(candidate) if kind == "fpv" else cqc_scene(candidate)
        game = start(owner, admitted, scene)
        if kind == "fpv":
            perform(owner, game, "fpv_fragmentation_strike", fpv_parameters(game))
        else:
            perform(
                owner,
                game,
                "resolve_cqc",
                {
                    "actor": game.shooter,
                    "target": game.mover,
                    "maneuver": "DISARM",
                    "held_item": "PRIMARY_WEAPON",
                },
            )
        saved = owner.snapshot(game.campaign)
        replay, receipt = game.entry_calls[-1]
        owner.export_campaign("targeted-export", game.campaign, exported)
    with AuthorityOwner.open(database, "aim-owner") as owner:
        assert owner.snapshot(game.campaign) == saved and replay(owner) == receipt
        assert owner.snapshot(game.campaign) == saved
    with AuthorityOwner.open(tmp_path / "recovered.sqlite3", "aim-owner") as owner:
        owner.set_admin_full_audit(True)
        restored = owner.recover_campaign(recovery_id="targeted-recover", source=exported)
        assert owner.recover_campaign(recovery_id="targeted-recover", source=exported) == restored
        assert owner.snapshot(game.campaign) == saved and replay(owner) == receipt
        assert owner.snapshot(game.campaign) == saved
        game.owner = owner
        before = deepcopy(game.actors[game.shooter])
        assert before[STATE] == {"aimed_targets": [], "attacked_targets": [game.mover]}
        aim(owner, game)
        assert game.actors[game.shooter]["ap"] == before["ap"] - 2
        paid = owner.snapshot(game.campaign)
        assert replay(owner) == receipt and owner.snapshot(game.campaign) == paid


def test_actual_player_fpv_queue_then_aim_price_and_duplicate(candidate, tmp_path):
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = start(owner, admitted, fpv_scene(candidate))
        request = map_request(owner, game, "fpv-strike", "targeted-player")
        request["selection"].update(device="fpv_alpha", aim_point=[31.0, 30.5, 0.5])
        before = owner.snapshot(game.campaign)
        preview = owner.preview_panel_action(**request)
        assert (
            next(row["value"] for row in preview["preview"]["summary"] if row["label"] == "AP cost")
            == 1
        )
        queue = owner.compose_action_sequence(
            campaign_id=game.campaign,
            session_id=request["session_id"],
            sequence_id="fpv-queue",
            expected_revision=before.revision,
            steps=[
                {
                    "step_id": "fpv",
                    "source": {
                        "kind": "panel",
                        "surface_id": "operations.map",
                        "action_id": "fpv-strike",
                    },
                    "selection": request["selection"],
                }
            ],
        )
        assert owner.snapshot(game.campaign) == before
        command = {
            "campaign_id": game.campaign,
            "session_id": request["session_id"],
            "sequence_id": queue["sequence_id"],
            "expected_version": queue["version"],
            "confirmation": queue["confirmation"],
        }
        with patch.object(dice._rng, "randint", return_value=1):
            result = owner.execute_action_sequence(**command)
        assert result["steps"][0]["status"] == "committed"
        paid = owner.snapshot(game.campaign)
        assert game.actors[game.shooter]["ap"] == 5
        assert (
            owner.execute_action_sequence(**command) == result
            and owner.snapshot(game.campaign) == paid
        )
        with pytest.raises(StaleRevision):
            owner.preview_panel_action(**request)
        with pytest.raises(CommandRejected):
            owner.preview_panel_action(
                **{**request, "operation_id": "spent-drone", "expected_revision": paid.revision}
            )
        request = map_request(owner, game, "aim-two", "after-targeted-fpv")
        preview = owner.preview_panel_action(**request)
        assert (
            next(row["value"] for row in preview["preview"]["summary"] if row["label"] == "AP cost")
            == 2
        )
        assert owner.snapshot(game.campaign) == paid
        receipt = owner.execute_panel_action(**request, confirmation=preview["confirmation"])
        assert game.actors[game.shooter]["ap"] == 3
        final = owner.snapshot(game.campaign)
        assert (
            owner.execute_panel_action(**request, confirmation=preview["confirmation"]) == receipt
        )
        assert owner.snapshot(game.campaign) == final


def test_autonomous_drone_shot_is_not_operator_attack(candidate, tmp_path):
    scene = fpv_scene(candidate)
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = start(owner, admitted, scene)
        before = deepcopy(game.actors[game.shooter])
        assert (
            perform(
                owner,
                game,
                "autodrone_fire",
                {
                    "actor": "autodrone_alpha",
                    "target": game.mover,
                    "aim_point": game.actors[game.mover]["center"],
                },
            )
            == 1
        )
        assert game.actors[game.shooter] == before
        assert game.actors["autodrone_alpha"]["ap"] == 0
        aim(owner, game)
        assert game.actors[game.shooter]["ap"] == 5
