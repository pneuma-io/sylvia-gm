"""Ordinary and prepared movement retain the same authored physical pose."""

import json
from unittest.mock import patch

import pytest

from sylvia_gm import dice
from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import WriterRole, thaw_json_value
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.spatial import SpatialEngine
from tests.admitted_library_fixture import admitted_fixture_owner
from tests.test_td_crossing_pose import NS, authored_scene
from tests.test_td_exposure_owner import create
from tests.test_td_ordinary_movement import SESSION, map_request
from tests.td_source_fixture import candidate as candidate_fixture

candidate = candidate_fixture


def tool_call(owner, game, tool_name, parameters, operation):
    tool = next(
        row
        for row in StatelessMcpMechanics(owner).list_tools(game.campaign)
        if row.package_id == NS and row.name == tool_name
    )
    command = ActionCommand(
        campaign_id=game.campaign,
        action_id=operation,
        idempotency_key=operation,
        owner_id="aim-owner",
        writer=WriterRole.DM,
        expected_revision=owner.snapshot(game.campaign).revision,
        turn_id="turn",
        package_id=NS,
        tool_name=tool.name,
        parameters=parameters,
        initiating_session_id=f"{SESSION}-{game.campaign}",
    )
    return lambda current=None: StatelessMcpMechanics(current or owner).execute_action(command)


@pytest.mark.parametrize("allegiance", ["ordinary", "mirror"])
def test_retained_watch_both_misses_reach_same_authored_pose(candidate, tmp_path, allegiance):
    witnesses = []
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        owner.set_admin_full_audit(True)
        for arm in ("second", "prerequisites"):
            scene = authored_scene(candidate, f"{allegiance}-{arm}")
            game = create(owner, admitted, scene, campaign=arm)
            owner.create_agent_session(
                campaign_id=game.campaign,
                session_id=f"{SESSION}-{game.campaign}",
                role=WriterRole.DM,
                provider_id="synthetic",
                model_id="synthetic",
            )
            first = tool_call(
                owner,
                game,
                "resolve_trigger_crossing",
                {
                    "actor": game.mover,
                    "destination": [10, 6],
                    "overwatcher": game.shooter,
                    "suppressor": game.mover,
                    "first_effect": "OVERWATCH",
                    "overwatch_critical_effect": "DEEP_HIT",
                    "suppression_critical_effect": "DEEP_HIT",
                },
                "first-crossing",
            )
            with patch.object(dice._rng, "randint", return_value=1) as draws:
                first()
            assert draws.call_count == 1
            first_trigger = thaw_json_value(game.world["last_trigger"])
            assert first_trigger["overwatch"]["outcome"] == "MISS"
            returned = tool_call(
                owner,
                game,
                "fire_burst",
                {
                    "attacker": game.mover,
                    "target": game.shooter,
                    "aim_point": game.actors[game.shooter]["center"],
                    "critical_effect": "DEVASTATING_HIT",
                },
                "return-burst",
            )
            with patch.object(dice._rng, "randint", return_value=1) as draws:
                returned()
            assert draws.call_count == 1
            returning_attack = thaw_json_value(game.world["last_attack"])
            assert returning_attack["outcome"] == "WIDE_MISS"
            second = game.mover.replace("_1", "_2")
            before = owner.snapshot(game.campaign)
            original = before.state[NS + ".entities"][second]
            parameters = {"actor": second, "destination": [9, 6]}
            tool = "move_actor"
            if arm == "second":
                tool = "resolve_trigger_crossing"
                parameters.update(
                    overwatcher=game.shooter,
                    suppressor=game.mover,
                    first_effect="OVERWATCH",
                    overwatch_critical_effect="DEEP_HIT",
                    suppression_critical_effect="DEEP_HIT",
                )
            call = tool_call(owner, game, tool, parameters, "second-mover")
            with patch.object(dice._rng, "randint", return_value=1) as draws:
                receipt = call()
            after = owner.snapshot(game.campaign)
            actor = after.state[NS + ".entities"][second]
            witnesses.append(
                {
                    "arm": arm,
                    "allegiance": allegiance,
                    "tool": tool,
                    "before": thaw_json_value(original),
                    "after": thaw_json_value(actor),
                    "watcher": thaw_json_value(game.actors[game.shooter]),
                    "returner": thaw_json_value(game.actors[game.mover]),
                    "prepared": thaw_json_value(game.world["prepared"]),
                    "first_trigger": first_trigger,
                    "return_attack": returning_attack,
                    "last_trigger": thaw_json_value(game.world["last_trigger"]),
                    "second_draws": draws.call_count,
                }
            )
            assert str(receipt.status) == "committed"
            assert draws.call_count == (1 if arm == "second" else 0)
            if arm == "second":
                assert game.world["last_trigger"]["overwatch"]["outcome"] == "MISS"
            assert actor["position_cell"] == (9, 6) and actor["center"] == (9.5, 6.5, 0.9)
            assert actor["ap"] == 5 and actor["hp"] == original["hp"]
            assert actor["res"] == original["res"] and not actor["downed"]
            with patch.object(dice._rng, "randint") as draws:
                assert call() == receipt
            assert draws.call_count == 0 and owner.snapshot(game.campaign) == after
    (tmp_path / "both-misses.json").write_text(json.dumps(witnesses, indent=2) + "\n")
    assert [row["after"]["muzzle"] for row in witnesses] == [[8.91, 6.5, 1.4]] * 2


def movement_call(owner, game, route, consumer="chat", *, destination=None):
    destination = destination or [10, 6]
    if route == "explicit":
        return tool_call(
            owner,
            game,
            "resolve_trigger_crossing",
            {
                "actor": game.mover,
                "destination": destination,
                "overwatcher": game.shooter,
                "suppressor": game.mover,
                "first_effect": "OVERWATCH",
                "overwatch_critical_effect": "DEEP_HIT",
                "suppression_critical_effect": "DEEP_HIT",
            },
            "pose-move",
        )
    if consumer == "chat":
        return tool_call(
            owner,
            game,
            "move_actor",
            {"actor": game.mover, "destination": destination},
            "pose-move",
        )
    request = map_request(owner, game, destination=destination)
    request["session_id"] = f"{SESSION}-{game.campaign}"
    before = owner.snapshot(game.campaign)
    if consumer == "map":
        owner.preview_panel_action(**request)
        assert owner.snapshot(game.campaign) == before
        return lambda current=None: (current or owner).execute_panel_action(**request)
    queue = owner.compose_action_sequence(
        campaign_id=game.campaign,
        session_id=request["session_id"],
        sequence_id="pose-queue",
        expected_revision=before.revision,
        steps=[
            {
                "step_id": "move",
                "source": {"kind": "panel", "surface_id": "operations.map", "action_id": "move"},
                "selection": request["selection"],
            }
        ],
    )
    assert owner.snapshot(game.campaign) == before
    return lambda current=None: (current or owner).execute_action_sequence(
        campaign_id=game.campaign,
        session_id=request["session_id"],
        sequence_id=queue["sequence_id"],
        expected_version=queue["version"],
        confirmation=queue.get("confirmation"),
    )


def pose_scene(candidate, allegiance, route):
    scene = authored_scene(candidate, f"{allegiance}-second")
    scene.world["decision_owner"] = "player"
    watch = scene.world["prepared"]["overwatch"]
    if route == "none":
        watch["active"] = False
    elif route == "invalidated":
        # Retain a stale preparation prerequisite, without changing any physical pose.
        watch["stationary_origin"] = [5, 4]
    return scene


def session(owner, game):
    owner.create_agent_session(
        campaign_id=game.campaign,
        session_id=f"{SESSION}-{game.campaign}",
        role=WriterRole.DM,
        provider_id="synthetic",
        model_id="synthetic",
    )


@pytest.mark.parametrize("allegiance", ["ordinary", "mirror"])
@pytest.mark.parametrize("stance", ["STANDING", "CROUCHED"])
@pytest.mark.parametrize("route", ["none", "invalidated", "automatic", "explicit"])
def test_all_movement_routes_preserve_authored_pose_and_owned_height(
    candidate, tmp_path, allegiance, stance, route
):
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        owner.set_admin_full_audit(True)
        game = create(owner, admitted, pose_scene(candidate, allegiance, route))
        session(owner, game)
        if stance == "CROUCHED":
            game.execute("change_stance", {"actor": game.mover, "stance": stance})
        before = owner.snapshot(game.campaign)
        original = before.state[NS + ".entities"][game.mover]
        call = movement_call(owner, game, route)
        with patch.object(dice._rng, "randint", return_value=1) as draws:
            receipt = call()
        after = owner.snapshot(game.campaign)
        actor = after.state[NS + ".entities"][game.mover]
        (tmp_path / "route-pose.json").write_text(
            json.dumps(
                {
                    "allegiance": allegiance,
                    "stance": stance,
                    "route": route,
                    "before": thaw_json_value(original),
                    "after": thaw_json_value(actor),
                    "draws": draws.call_count,
                },
                indent=2,
            )
            + "\n"
        )
        assert str(receipt.status) == "committed"
        assert draws.call_count == (1 if route in {"automatic", "explicit"} else 0)
        assert actor["position_cell"] == (10, 6)
        assert actor["center"] == (10.5, 6.5, original["center"][2])
        assert actor["muzzle"] == (9.91, 6.5, original["muzzle"][2])
        assert actor["stance"] == stance
        assert actor["ap"] == original["ap"] - (2 if stance == "CROUCHED" else 1)
        for field in ("hp", "res", "downed", "weapon"):
            assert actor[field] == original[field]
        bodies = before.state[NS + ".state"]["spatial_scene"]["entities"]
        before_body = next(row for row in bodies if row["id"] == game.mover)
        body = next(
            row
            for row in after.state[NS + ".state"]["spatial_scene"]["entities"]
            if row["id"] == game.mover
        )
        assert body["position"] == (10.5, 6.5, 0)
        assert {key: value for key, value in body.items() if key != "position"} == {
            key: value for key, value in before_body.items() if key != "position"
        }
        with patch.object(dice._rng, "randint") as draws:
            assert call() == receipt
        assert draws.call_count == 0 and owner.snapshot(game.campaign) == after


@pytest.mark.parametrize("consumer", ["map", "queue"])
@pytest.mark.parametrize("route", ["none", "automatic"])
def test_allied_panel_movement_preserves_authored_pose(candidate, tmp_path, consumer, route):
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        owner.set_admin_full_audit(True)
        game = create(owner, admitted, pose_scene(candidate, "mirror", route))
        session(owner, game)
        call = movement_call(owner, game, route, consumer)
        with patch.object(dice._rng, "randint", return_value=1) as draws:
            receipt = call()
        after = owner.snapshot(game.campaign)
        actor = after.state[NS + ".entities"][game.mover]
        assert actor["muzzle"] == (9.91, 6.5, 1.4) and actor["ap"] == 5
        assert draws.call_count == (1 if route == "automatic" else 0)
        with patch.object(dice._rng, "randint") as draws:
            assert call() == receipt
        assert draws.call_count == 0 and owner.snapshot(game.campaign) == after


@pytest.mark.parametrize("allegiance", ["ordinary", "mirror"])
def test_automatic_watch_incapacity_keeps_original_pose_at_actual_pause(
    candidate, tmp_path, allegiance
):
    scene = pose_scene(candidate, allegiance, "automatic")
    scene.actors[scene.mover].update(hp=1, res=0)
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        owner.set_admin_full_audit(True)
        game = create(owner, admitted, scene)
        session(owner, game)
        call = movement_call(owner, game, "automatic")
        with patch.object(dice._rng, "randint", return_value=19) as draws:
            receipt = call()
        after = owner.snapshot(game.campaign)
        actor = after.state[NS + ".entities"][game.mover]
        trigger = after.state[NS + ".state"]["last_trigger"]
        assert draws.call_count == 1 and actor["downed"]
        assert trigger["overwatch"]["target_reached"] and trigger["status"] == "INTERRUPTED"
        assert actor["center"] == (10.5, 6.2, 0.9) and actor["muzzle"] == (9.91, 6.2, 1.4)
        assert actor["position_cell"] == (10, 6) and actor["ap"] == 5
        with patch.object(dice._rng, "randint") as draws:
            assert call() == receipt
        assert draws.call_count == 0 and owner.snapshot(game.campaign) == after


@pytest.mark.parametrize("endpoint", ["first", "second"])
def test_fallback_endpoint_keeps_control_and_exact_once_recovery(candidate, tmp_path, endpoint):
    database = tmp_path / "authority.sqlite3"
    export = tmp_path / "fallback-fire.sylvia-gm-campaign"
    with admitted_fixture_owner(database, "aim-owner", candidate.read_bytes()) as (owner, admitted):
        owner.set_admin_full_audit(True)
        scene = pose_scene(candidate, "mirror", "none")
        if endpoint == "second":
            scene = authored_scene(candidate, "mirror-prerequisites")
            # Declare a normal player-ready phase; retain every physical/numeric input.
            scene.world["decision_owner"] = "player"
        game = create(owner, admitted, scene)
        session(owner, game)
        prefix = []
        if endpoint == "second":
            crossed = movement_call(owner, game, "explicit")
            with patch.object(dice._rng, "randint", return_value=1) as draws:
                crossing_receipt = crossed()
            assert draws.call_count == 1
            prefix.append((crossed, crossing_receipt))
            returned = tool_call(
                owner,
                game,
                "fire_burst",
                {
                    "attacker": game.mover,
                    "target": game.shooter,
                    "aim_point": game.actors[game.shooter]["center"],
                    "critical_effect": "DEVASTATING_HIT",
                },
                "return-for-second",
            )
            with patch.object(dice._rng, "randint", return_value=1) as draws:
                return_receipt = returned()
            assert draws.call_count == 1
            prefix.append((returned, return_receipt))
            game.mover = "A_VESPER_2"
        if endpoint == "second":
            denied_state = owner.snapshot(game.campaign)
            with (
                patch.object(dice._rng, "randint") as draws,
                pytest.raises(CommandRejected, match="not currently controlled"),
            ):
                movement_call(owner, game, "none", "queue", destination=[9, 6])
            assert draws.call_count == 0 and owner.snapshot(game.campaign) == denied_state
            move = tool_call(
                owner,
                game,
                "move_actor",
                {"actor": game.mover, "destination": [9, 6]},
                "second-move-chat",
            )
        else:
            move = movement_call(owner, game, "none", "queue")
        with patch.object(dice._rng, "randint") as draws:
            moved = move()
        before = owner.snapshot(game.campaign)
        assert draws.call_count == 0
        moved_actor = before.state[NS + ".entities"][game.mover]
        assert moved_actor["muzzle"] == (
            (8.91, 6.5, 1.4) if endpoint == "second" else (9.91, 6.5, 1.4)
        )
        if endpoint == "second":
            world = before.state[NS + ".state"]
            assert world["focused_actor"] == game.mover
            assert game.mover not in world["eligible_actor_ids"]
            with (
                patch.object(dice._rng, "randint") as draws,
                pytest.raises(CommandRejected, match="focused actor is not eligible"),
            ):
                owner.campaign_panels(game.campaign)
            assert draws.call_count == 0 and owner.snapshot(game.campaign) == before
            exposure = (
                SpatialEngine(world["spatial_scene"])
                .query(
                    {
                        "kind": "exposure",
                        "map_id": world["spatial_scene"]["map"]["id"],
                        "observer": {"point": list(moved_actor["muzzle"])},
                        "target": {"entity_id": game.shooter},
                        "samples": {"horizontal": 3, "vertical": 3},
                        "include": ["geometry", "entities"],
                    }
                )
                .as_payload()
            )
            assert exposure["exposure"] > 0
            (tmp_path / "endpoint-scope.json").write_text(
                json.dumps(
                    {
                        "actor": game.mover,
                        "pose": thaw_json_value(moved_actor),
                        "exposure": exposure,
                        "eligible": thaw_json_value(world["eligible_actor_ids"]),
                        "qualification": (
                            "Exact second endpoint has a direct physical path, but this retained "
                            "fixture does not grant player control or eligible-actor scope. "
                            "No later ordinary player fire is claimed; "
                            "no eligibility was manufactured."
                        ),
                    },
                    indent=2,
                )
                + "\n"
            )
            owner.export_campaign("second-endpoint-export", game.campaign, export)
            recovered_database = tmp_path / "recovered.sqlite3"
            for reopen in (False, True):
                with AuthorityOwner.open(recovered_database, "aim-owner") as recovered:
                    recovered.set_admin_full_audit(True)
                    if not reopen:
                        receipt = recovered.recover_campaign(recovery_id="second", source=export)
                        assert (
                            recovered.recover_campaign(recovery_id="second", source=export)
                            == receipt
                        )
                    assert recovered.snapshot(game.campaign) == before
                    with patch.object(dice._rng, "randint") as draws:
                        for replay, original_receipt in prefix:
                            assert replay(recovered) == original_receipt
                        assert move(recovered) == moved
                    assert draws.call_count == 0 and recovered.snapshot(game.campaign) == before
            return
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
            "session_id": f"{SESSION}-{game.campaign}",
            "surface_id": "operations.map",
            "panel_action_id": "burst",
            "operation_id": "fallback-burst",
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

        def fire(current=None):
            return (current or owner).execute_panel_action(**request)

        with patch.object(dice._rng, "randint", return_value=1) as draws:
            fired = fire()
        after = owner.snapshot(game.campaign)
        actor = after.state[NS + ".entities"][game.mover]
        assert draws.call_count == 1 and actor["ap"] == 3
        assert actor["weapon"]["magazine"]["rounds"] == 27
        assert actor["weapon"]["current_recoil"] == 3
        assert after.state[NS + ".state"]["last_attack"]["outcome"] == "WIDE_MISS"
        (tmp_path / "follow-on.json").write_text(
            json.dumps(
                {
                    "endpoint": endpoint,
                    "before": thaw_json_value(moved_actor),
                    "after": thaw_json_value(actor),
                    "available": thaw_json_value(available),
                    "fire_consumer": "player-panel",
                    "prefix_receipts": len(prefix),
                    "final_revision": after.revision,
                },
                indent=2,
            )
            + "\n"
        )
        owner.export_campaign("fallback-export", game.campaign, export)
    for path in (database, tmp_path / "recovered.sqlite3"):
        with AuthorityOwner.open(path, "aim-owner") as owner:
            owner.set_admin_full_audit(True)
            if path != database:
                receipt = owner.recover_campaign(recovery_id="fallback-recover", source=export)
                assert (
                    owner.recover_campaign(recovery_id="fallback-recover", source=export) == receipt
                )
            assert owner.snapshot(game.campaign) == after
            with patch.object(dice._rng, "randint") as draws:
                for replay, original_receipt in prefix:
                    assert replay(owner) == original_receipt
                assert move(owner) == moved
                assert fire(owner) == fired
            assert draws.call_count == 0 and owner.snapshot(game.campaign) == after


def test_valid_authored_opposing_sources_keep_nonactive_side_priority(candidate, tmp_path):
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        owner.set_admin_full_audit(True)
        scene = pose_scene(candidate, "mirror", "automatic")
        game = create(owner, admitted, scene)
        session(owner, game)
        # Move this existing left-facing rifle to the right of the mover. This
        # changes its position through authority, without reorienting the muzzle.
        friendly = "A_VESPER_2"
        game.execute("move_actor", {"actor": friendly, "destination": [11, 5]})
        assert game.actors[friendly]["center"] == [11.5, 5.5, 0.9]
        assert game.actors[friendly]["muzzle"] == [10.91, 5.5, 1.4]
        original_pose = {key: game.actors[friendly][key] for key in ("center", "muzzle", "stance")}
        game.execute(
            "establish_suppression",
            {"actor": friendly, "zone_id": "declared-prepared-lane", "rounds": 20},
        )
        before = owner.snapshot(game.campaign)
        call = movement_call(owner, game, "automatic")
        with patch.object(dice._rng, "randint", return_value=1) as draws:
            receipt = call()
        after = owner.snapshot(game.campaign)
        world = after.state[NS + ".state"]
        assert draws.call_count == 2 and str(receipt.status) == "committed"
        assert world["last_trigger"]["first_effect"] == "OVERWATCH"
        assert world["last_trigger"]["overwatch"]["outcome"] == "MISS"
        assert world["last_trigger"]["suppression"]["status"] == "RESOLVED"
        assert world["prepared"]["overwatch"]["shots"] == 1
        assert world["prepared"]["suppression"]["capacity"] == 1
        actor = after.state[NS + ".entities"][game.mover]
        assert actor["position_cell"] == (10, 6) and actor["ap"] == 5
        assert actor["muzzle"] == (9.91, 6.5, 1.4)
        assert after.state[NS + ".entities"][friendly] == before.state[NS + ".entities"][friendly]
        watcher = after.state[NS + ".entities"][game.shooter]
        prior_watcher = before.state[NS + ".entities"][game.shooter]
        assert watcher["ap"] == prior_watcher["ap"]
        assert watcher["weapon"]["magazine"]["rounds"] == (
            prior_watcher["weapon"]["magazine"]["rounds"] - 1
        )
        assert {key: game.actors[friendly][key] for key in original_pose} == original_pose
        with patch.object(dice._rng, "randint") as draws:
            assert call() == receipt
        assert draws.call_count == 0 and owner.snapshot(game.campaign) == after
        (tmp_path / "valid-priority.json").write_text(
            json.dumps(
                {
                    "actor": friendly,
                    "unchanged_pose": original_pose,
                    "trigger": thaw_json_value(world["last_trigger"]),
                    "prepared": thaw_json_value(world["prepared"]),
                },
                indent=2,
            )
            + "\n"
        )
