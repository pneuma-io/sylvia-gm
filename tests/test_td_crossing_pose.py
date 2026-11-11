"""Explicit prepared-fire movement preserves authored pose on both allegiances."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from sylvia_gm import dice
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import WriterRole, thaw_json_value
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.spatial import SpatialEngine
from tests.admitted_library_fixture import admitted_fixture_owner
from tests.test_td_explicit_weighted_move import explicit_call
from tests.test_td_exposure_owner import create
from tests.test_td_ordinary_movement import SESSION
from tests.td_source_fixture import candidate as candidate_fixture
from tests.test_td_suppression_aim import PackageProcedures
from tests.test_td_weighted_move import weighted_scene

candidate = candidate_fixture
NS = "pneuma-io.trigger-discipline"
FIXTURE = Path(__file__).parent / "fixtures/td-crossing-pose-v53.json"


def authored_scene(candidate, stratum):
    game = PackageProcedures(candidate)
    game.state = json.loads(FIXTURE.read_bytes())["states"][stratum]
    mirrored = stratum.startswith("mirror")
    game.mover = ("A" if mirrored else "B") + "_VESPER_1"
    game.shooter = ("B" if mirrored else "A") + "_SABLE_4"
    return game


@pytest.mark.parametrize(
    "stratum",
    [
        "ordinary-second",
        "ordinary-prerequisites",
        "mirror-second",
        "mirror-prerequisites",
    ],
)
@pytest.mark.parametrize("face", [1, 20])
def test_explicit_crossing_preserves_authored_pose(candidate, tmp_path, stratum, face):
    scene = authored_scene(candidate, stratum)
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        owner.set_admin_full_audit(True)
        game = create(owner, admitted, scene, campaign="authored-pose")
        call = explicit_call(owner, game, destination=[10, 6], first="OVERWATCH")
        with patch.object(dice._rng, "randint", return_value=face) as draws:
            receipt = call()
        snapshot = owner.snapshot(game.campaign)
        actor = snapshot.state[NS + ".entities"][game.mover]
        trigger = snapshot.state[NS + ".state"]["last_trigger"]
        (tmp_path / "witness.json").write_text(
            json.dumps(
                {
                    "stratum": stratum,
                    "before": scene.actors[game.mover],
                    "after": thaw_json_value(actor),
                    "trigger": thaw_json_value(trigger),
                    "draws": draws.call_count,
                    "status": str(receipt.status),
                },
                indent=2,
            )
            + "\n"
        )
        assert str(receipt.status) == "committed" and draws.call_count == 1
        assert trigger["status"] == "RESUMED"
        assert actor["position_cell"] == (10, 6)
        assert actor["center"] == (10.5, 6.5, 0.9)
        assert actor["muzzle"] == (9.91, 6.5, 1.4)
        expected = json.loads(FIXTURE.read_bytes())["expected_first_face_discriminators"][stratum][
            str(face)
        ]["state"]
        assert {key: actor[key] for key in expected} == expected
        engine = SpatialEngine(snapshot.state[NS + ".state"]["spatial_scene"])
        query = {
            "kind": "exposure",
            "map_id": scene.world["spatial_scene"]["map"]["id"],
            "observer": {"point": list(actor["muzzle"])},
            "target": {"entity_id": game.shooter},
            "samples": {"horizontal": 3, "vertical": 3},
            "include": ["geometry", "entities"],
        }
        engine.query(query, snapshot=engine.snapshot)
        cached = engine.query(query, snapshot=engine.snapshot).as_payload()
        fresh = (
            SpatialEngine(snapshot.state[NS + ".state"]["spatial_scene"]).query(query).as_payload()
        )
        assert cached == fresh and fresh["exposure"] > 0
        before_replay = owner.snapshot(game.campaign)
        with patch.object(dice._rng, "randint") as draws:
            assert call() == receipt
        assert draws.call_count == 0 and owner.snapshot(game.campaign) == before_replay


@pytest.mark.parametrize("action", ["fire", "burst", "full-auto"])
def test_ordinary_direct_fire_rejects_retained_no_path(candidate, tmp_path, action):
    retained = json.loads((FIXTURE.parent / "td-direct-no-path-v53.json").read_bytes())
    scene = PackageProcedures(candidate)
    scene.state = retained["state"]
    scene.mover, scene.shooter = retained["actor"], retained["target"]
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        owner.set_admin_full_audit(True)
        game = create(owner, admitted, scene, campaign="no-direct-path")
        owner.create_agent_session(
            campaign_id=game.campaign,
            session_id=SESSION,
            role=WriterRole.DM,
            provider_id="synthetic",
            model_id="synthetic",
        )
        before = owner.snapshot(game.campaign)
        state = thaw_json_value(before.state)
        actor = state[NS + ".entities"][game.mover]
        world = state[NS + ".state"]
        query = {
            "kind": "exposure",
            "map_id": world["spatial_scene"]["map"]["id"],
            "observer": {"point": actor["muzzle"]},
            "target": {"entity_id": game.shooter},
            "samples": {"horizontal": 3, "vertical": 3},
            "include": ["geometry", "entities"],
        }
        assert SpatialEngine(world["spatial_scene"]).query(query).as_payload()["exposure"] == 0
        panel = next(
            row
            for row in owner.campaign_panels(game.campaign)["surfaces"]
            if row["id"] == "operations.map"
        )
        selection = {
            "actor": game.mover,
            "target": game.shooter,
            "aim_point": state[NS + ".entities"][game.shooter]["center"],
            "map_revision": panel["data"]["map_revision"],
        }
        with patch.object(dice._rng, "randint") as draws:
            result = owner.player_action_availability(
                campaign_id=game.campaign,
                expected_revision=before.revision,
                requests=[
                    {
                        "source": {
                            "kind": "panel",
                            "surface_id": "operations.map",
                            "action_id": action,
                        },
                        "selection": selection,
                    }
                ],
            )["results"][0]
        assert draws.call_count == 0 and owner.snapshot(game.campaign) == before
        (tmp_path / "no-path-source-state.json").write_text(
            json.dumps(
                {
                    "state": state,
                    "availability": thaw_json_value(result),
                    "selection": selection,
                    "actor": game.mover,
                    "target": game.shooter,
                },
                indent=2,
            )
            + "\n"
        )
        assert result["available"] is False
        assert "direct path" in result["reason"]
        request = {
            "campaign_id": game.campaign,
            "session_id": SESSION,
            "surface_id": "operations.map",
            "panel_action_id": action,
            "operation_id": "blocked-direct-burst",
            "expected_revision": before.revision,
            "selection": selection,
        }
        for preview in (True, False):
            with (
                patch.object(dice._rng, "randint") as draws,
                pytest.raises(CommandRejected, match="direct path"),
            ):
                (owner.preview_panel_action if preview else owner.execute_panel_action)(**request)
            assert draws.call_count == 0 and owner.snapshot(game.campaign) == before


@pytest.mark.parametrize("stratum", ["ordinary-second", "mirror-second"])
def test_explicit_crossing_preserves_owned_crouched_height(candidate, tmp_path, stratum):
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = create(owner, admitted, authored_scene(candidate, stratum), campaign="crouched-pose")
        owner.set_admin_full_audit(True)
        game.execute("change_stance", {"actor": game.mover, "stance": "CROUCHED"})
        before = owner.snapshot(game.campaign)
        original = before.state[NS + ".entities"][game.mover]
        call = explicit_call(owner, game, destination=[10, 6], first="OVERWATCH")
        with patch.object(dice._rng, "randint", return_value=1):
            call()
        after = owner.snapshot(game.campaign)
        actor = after.state[NS + ".entities"][game.mover]
        assert actor["center"] == (10.5, 6.5, original["center"][2])
        assert actor["muzzle"] == (9.91, 6.5, original["muzzle"][2])
        assert actor["stance"] == "CROUCHED" and actor["ap"] == original["ap"] - 2
        before_body = next(
            row
            for row in before.state[NS + ".state"]["spatial_scene"]["entities"]
            if row["id"] == game.mover
        )
        body = next(
            row
            for row in after.state[NS + ".state"]["spatial_scene"]["entities"]
            if row["id"] == game.mover
        )
        assert body["height"] == before_body["height"] and body["facing"] == before_body["facing"]
        assert body["position"] == (10.5, 6.5, 0)


@pytest.mark.parametrize("mirrored", [False, True])
def test_explicit_incapacity_keeps_actual_pause_pose_and_weighted_cost(
    candidate, tmp_path, mirrored
):
    scene = weighted_scene(candidate, mirrored)
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        from tests.test_td_ordinary_movement import prepare_owner

        game = prepare_owner(owner, admitted, scene)
        before = owner.snapshot(game.campaign)
        original = before.state[NS + ".entities"][game.mover]
        call = explicit_call(owner, game)
        with patch.object(dice._rng, "randint", return_value=19) as draws:
            receipt = call()
        after = owner.snapshot(game.campaign)
        actor = after.state[NS + ".entities"][game.mover]
        trigger = after.state[NS + ".state"]["last_trigger"]
        assert actor["downed"] and trigger["status"] == "INTERRUPTED"
        assert trigger["suppression"]["target_reached"] and draws.call_count == 1
        assert actor["center"] == (10.5, 6.5, original["center"][2])
        assert actor["muzzle"] == (original["muzzle"][0], 6.5, original["muzzle"][2])
        assert actor["position_cell"] == (10, 6) and actor["ap"] == original["ap"] - 3
        assert trigger["travelled_cost"] == 3 and trigger["movement_ap_spent"] == 3
        with patch.object(dice._rng, "randint") as draws:
            assert call() == receipt
        assert draws.call_count == 0 and owner.snapshot(game.campaign) == after


def test_follow_on_ordinary_burst_saves_reopens_recovers_and_replays(candidate, tmp_path):
    scene = authored_scene(candidate, "mirror-second")
    scene.world["decision_owner"] = "player"
    database = tmp_path / "authority.sqlite3"
    export = tmp_path / "ordinary-follow-on.sylvia-gm-campaign"
    with admitted_fixture_owner(database, "aim-owner", candidate.read_bytes()) as (owner, admitted):
        owner.set_admin_full_audit(True)
        game = create(owner, admitted, scene, campaign="ordinary-follow-on")
        owner.create_agent_session(
            campaign_id=game.campaign,
            session_id=SESSION,
            role=WriterRole.DM,
            provider_id="synthetic",
            model_id="synthetic",
        )
        cross = explicit_call(owner, game, destination=[10, 6], first="OVERWATCH")
        with patch.object(dice._rng, "randint", return_value=1):
            cross_receipt = cross()
        before = owner.snapshot(game.campaign)
        panel = next(
            row
            for row in owner.campaign_panels(game.campaign)["surfaces"]
            if row["id"] == "operations.map"
        )
        selection = {
            "actor": game.mover,
            "target": game.shooter,
            "aim_point": thaw_json_value(before.state[NS + ".entities"][game.shooter]["center"]),
            "map_revision": panel["data"]["map_revision"],
        }
        request = {
            "campaign_id": game.campaign,
            "session_id": SESSION,
            "surface_id": "operations.map",
            "panel_action_id": "burst",
            "operation_id": "ordinary-burst",
            "expected_revision": before.revision,
            "selection": selection,
        }
        with patch.object(dice._rng, "randint") as draws:
            available = owner.player_action_availability(
                campaign_id=game.campaign,
                expected_revision=before.revision,
                requests=[
                    {
                        "source": {
                            "kind": "panel",
                            "surface_id": "operations.map",
                            "action_id": "burst",
                        },
                        "selection": selection,
                    }
                ],
            )["results"][0]
            preview = owner.preview_panel_action(**request)
        assert available["available"] and draws.call_count == 0
        assert owner.snapshot(game.campaign) == before
        request["confirmation"] = preview["confirmation"]
        with patch.object(dice._rng, "randint", return_value=1) as draws:
            fired = owner.execute_panel_action(**request)
        after = owner.snapshot(game.campaign)
        actor = after.state[NS + ".entities"][game.mover]
        assert draws.call_count == 1 and actor["ap"] == 3
        assert actor["weapon"]["magazine"]["rounds"] == 27
        assert actor["weapon"]["current_recoil"] == 3
        assert after.state[NS + ".state"]["last_attack"]["outcome"] == "WIDE_MISS"
        assert (
            after.state[NS + ".entities"][game.shooter]
            == before.state[NS + ".entities"][game.shooter]
        )
        owner.export_campaign("follow-on-export", game.campaign, export)
    for database_path in (database, tmp_path / "fresh.sqlite3"):
        with AuthorityOwner.open(database_path, "aim-owner") as owner:
            owner.set_admin_full_audit(True)
            if database_path != database:
                owner.recover_campaign(recovery_id="follow-on", source=export)
            assert owner.snapshot(game.campaign) == after
            with patch.object(dice._rng, "randint") as draws:
                assert cross(owner) == cross_receipt
                assert owner.execute_panel_action(**request) == fired
            assert draws.call_count == 0 and owner.snapshot(game.campaign) == after


def test_raw_gm_no_path_paid_miss_is_not_direct_player_admission(candidate, tmp_path):
    retained = json.loads((FIXTURE.parent / "td-direct-no-path-v53.json").read_bytes())
    scene = PackageProcedures(candidate)
    scene.state = retained["state"]
    scene.mover, scene.shooter = retained["actor"], retained["target"]
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = create(owner, admitted, scene, campaign="raw-gm-boundary")
        owner.set_admin_full_audit(True)
        before = owner.snapshot(game.campaign)
        command = ActionCommand(
            campaign_id=game.campaign,
            action_id="raw-gm-fire",
            idempotency_key="raw-gm-fire",
            owner_id="aim-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="turn",
            package_id=NS,
            tool_name="fire_burst",
            parameters={
                "attacker": game.mover,
                "target": game.shooter,
                "aim_point": retained["selection"]["aim_point"],
                "critical_effect": "DEVASTATING_HIT",
            },
        )
        with patch.object(dice._rng, "randint", return_value=1) as draws:
            receipt = owner.execute_action(command)
        after = owner.snapshot(game.campaign)
        actor = after.state[NS + ".entities"][game.mover]
        original = before.state[NS + ".entities"][game.mover]
        assert str(receipt.status) == "committed" and draws.call_count == 1
        assert actor["ap"] == original["ap"] - 2
        assert actor["weapon"]["magazine"]["rounds"] == original["weapon"]["magazine"]["rounds"] - 3
        assert after.state[NS + ".state"]["last_attack"]["outcome"] == "WIDE_MISS"


@pytest.mark.parametrize("stratum", ["ordinary-second", "mirror-second"])
def test_authored_pose_incapacity_keeps_exact_subcell_pause(candidate, tmp_path, stratum):
    scene = authored_scene(candidate, stratum)
    # Reuse the existing incapacity diagnostic pools, explicitly distinct from
    # the unmodified healthy Lab discriminators above. Never move its muzzle.
    scene.actors[scene.mover].update(hp=1, res=0)
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        owner.set_admin_full_audit(True)
        game = create(owner, admitted, scene, campaign="authored-pause-incapacity")
        before = owner.snapshot(game.campaign)
        call = explicit_call(owner, game, destination=[10, 6], first="OVERWATCH")
        with patch.object(dice._rng, "randint", return_value=20) as draws:
            receipt = call()
        after = owner.snapshot(game.campaign)
        actor = after.state[NS + ".entities"][game.mover]
        watcher = after.state[NS + ".entities"][game.shooter]
        prior_watcher = before.state[NS + ".entities"][game.shooter]
        trigger = after.state[NS + ".state"]["last_trigger"]
        assert draws.call_count == 1 and str(receipt.status) == "committed"
        assert actor["downed"] and trigger["overwatch"]["target_reached"]
        assert trigger["status"] == "INTERRUPTED"
        assert actor["center"] == (10.5, 6.2, 0.9)
        assert actor["muzzle"] == (9.91, 6.2, 1.4)
        assert actor["position_cell"] == (10, 6) and actor["ap"] == 5
        assert watcher["ap"] == prior_watcher["ap"]
        assert watcher["weapon"]["magazine"]["rounds"] == (
            prior_watcher["weapon"]["magazine"]["rounds"] - 1
        )
        with patch.object(dice._rng, "randint") as draws:
            assert call() == receipt
        assert draws.call_count == 0 and owner.snapshot(game.campaign) == after
