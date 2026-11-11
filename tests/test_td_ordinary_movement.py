"""Ordinary map, queue and chat movement must use real prepared-fire authority."""

import hashlib
import json
from copy import deepcopy
from unittest.mock import patch

import pytest

from sylvia_gm import dice
from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import (
    ActionCommand,
    AuthorityOwner,
    ResolveMechanicsChoiceCommand,
)
from sylvia_gm.domain.authority import WriterRole, thaw_json_value
from sylvia_gm.domain.errors import CommandRejected, StaleRevision
from sylvia_gm.spatial import SpatialEngine
from tests.admitted_library_fixture import admitted_fixture_owner
from tests.test_td_exposure_owner import create
from tests.test_td_exposure_ownership import NS, game_for, setup
from tests.td_source_fixture import candidate as candidate_fixture

candidate = candidate_fixture
SESSION = "ordinary-move-gm"


def prepare_owner(owner, admitted, scene, *, watcher=None):
    owner.set_admin_full_audit(True)
    game = create(owner, admitted, scene, campaign="ordinary-move")
    setup(game)
    if watcher is not None:
        game.execute(
            "prepare_overwatch",
            {
                "actor": watcher,
                "zone_id": "declared-prepared-lane",
            },
        )
    side = game.actors[game.shooter]["side"]
    game.execute("end_allies_turn" if side == "allies" else "end_hostiles_turn", {})
    owner.create_agent_session(
        campaign_id=game.campaign,
        session_id=SESSION,
        role=WriterRole.DM,
        provider_id="synthetic",
        model_id="synthetic",
    )
    return game


def map_request(owner, game, operation="ordinary-move", destination=None):
    panel = next(
        row
        for row in owner.campaign_panels(game.campaign)["surfaces"]
        if row["id"] == "operations.map"
    )
    move = next(row for row in panel["actions"] if row["id"] == "move")
    assert move["intent"] == "move_actor"
    return {
        "campaign_id": game.campaign,
        "session_id": SESSION,
        "surface_id": panel["id"],
        "panel_action_id": move["id"],
        "operation_id": operation,
        "expected_revision": owner.snapshot(game.campaign).revision,
        "selection": {
            "actor": game.mover,
            "destination": destination or [10, 7],
            "map_revision": panel["data"]["map_revision"],
        },
    }


def ordinary_call(owner, game, consumer):
    request = map_request(owner, game)
    before = owner.snapshot(game.campaign)
    if consumer == "map":
        owner.preview_panel_action(**request)
        assert owner.snapshot(game.campaign) == before
        return lambda current=None: (current or owner).execute_panel_action(**request)
    if consumer == "queue":
        queue = owner.compose_action_sequence(
            campaign_id=game.campaign,
            session_id=SESSION,
            sequence_id="ordinary-move-queue",
            expected_revision=before.revision,
            steps=[
                {
                    "step_id": "move",
                    "source": {
                        "kind": "panel",
                        "surface_id": "operations.map",
                        "action_id": "move",
                    },
                    "selection": request["selection"],
                }
            ],
        )
        assert owner.snapshot(game.campaign) == before
        return lambda current=None: (current or owner).execute_action_sequence(
            campaign_id=game.campaign,
            session_id=SESSION,
            sequence_id=queue["sequence_id"],
            expected_version=queue["version"],
            confirmation=queue.get("confirmation"),
        )
    mechanics = StatelessMcpMechanics(owner)
    tool = next(
        row
        for row in mechanics.list_tools(game.campaign)
        if row.package_id == NS and row.name == "move_actor"
    )
    assert set(tool.input_schema["properties"]) == {"actor", "destination"}
    command = ActionCommand(
        campaign_id=game.campaign,
        action_id="ordinary-chat-move",
        idempotency_key="ordinary-chat-move",
        owner_id="aim-owner",
        writer=WriterRole.DM,
        expected_revision=before.revision,
        turn_id="turn",
        package_id=NS,
        tool_name=tool.name,
        parameters={"actor": game.mover, "destination": [10, 7]},
        initiating_session_id=SESSION,
    )
    return lambda current=None: StatelessMcpMechanics(current or owner).execute_action(command)


@pytest.mark.parametrize(
    ("consumer", "mirrored"),
    [("map", True), ("queue", True), ("chat", False), ("chat", True)],
)
def test_ordinary_consumers_resolve_first_visible_exposure_once(
    candidate,
    tmp_path,
    mirrored,
    consumer,
):
    scene = game_for(candidate, mirrored, "empty", wall_end=6.05)
    database = tmp_path / "authority.sqlite3"
    with admitted_fixture_owner(
        database,
        "aim-owner",
        candidate.read_bytes(),
    ) as (owner, admitted):
        game = prepare_owner(owner, admitted, scene)
        before = owner.snapshot(game.campaign)
        shooter_before = deepcopy(game.actors[game.shooter])
        actor_before = deepcopy(game.actors[game.mover])
        call = ordinary_call(owner, game, consumer)
        with patch.object(dice._rng, "randint", return_value=1) as draws:
            result = call()
        assert draws.call_count == 1
        after = owner.snapshot(game.campaign)
        state = after.state[NS + ".state"]
        assert state["prepared"]["suppression"]["capacity"] == 1
        assert state["last_trigger"]["status"] == "RESUMED"
        assert thaw_json_value(after.state[NS + ".entities"][game.shooter]) == shooter_before
        actor = after.state[NS + ".entities"][game.mover]
        assert actor["position_cell"] == (10, 7)
        assert actor["ap"] == actor_before["ap"] - 2
        assert after.revision == before.revision + 1
        with patch.object(dice._rng, "randint") as draws:
            assert call() == result
        assert draws.call_count == 0 and owner.snapshot(game.campaign) == after
        export = tmp_path / "ordinary-move.sylvia-gm-campaign"
        owner.export_campaign("ordinary-export", game.campaign, export)
        (tmp_path / "proof.json").write_text(
            json.dumps(
                {
                    "consumer": consumer,
                    "mirrored": mirrored,
                    "package_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
                    "before_revision": before.revision,
                    "after_revision": after.revision,
                    "actor": thaw_json_value(actor),
                    "trigger": thaw_json_value(state["last_trigger"]),
                    "state_sha256": hashlib.sha256(
                        json.dumps(
                            thaw_json_value(after.state),
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode()
                    ).hexdigest(),
                    "export_sha256": hashlib.sha256(export.read_bytes()).hexdigest(),
                    "qualification": "synthetic admitted owner, no rendered/provider/native",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    with AuthorityOwner.open(database, "aim-owner") as owner:
        owner.set_admin_full_audit(True)
        game.replay(owner, after)
        with patch.object(dice._rng, "randint") as draws:
            assert call(owner) == result
        assert draws.call_count == 0 and owner.snapshot(game.campaign) == after
    with AuthorityOwner.open(tmp_path / "fresh.sqlite3", "aim-owner") as owner:
        owner.set_admin_full_audit(True)
        recovered = owner.recover_campaign(recovery_id="ordinary-recover", source=export)
        assert owner.recover_campaign(recovery_id="ordinary-recover", source=export) == recovered
        game.replay(owner, after)
        with patch.object(dice._rng, "randint") as draws:
            assert call(owner) == result
        assert draws.call_count == 0 and owner.snapshot(game.campaign) == after


@pytest.mark.parametrize(
    "case",
    [
        "inactive-source",
        "downed-source",
        "unready-source",
        "depleted",
        "ended",
        "protected",
        "no-ap",
        "no-path",
        "noop",
        "own-fire",
        "own-noop",
        "downed-mover",
    ],
)
def test_ordinary_route_preserves_negative_and_noop_boundaries(candidate, case):
    game = game_for(candidate, True, "empty", wall_end=20 if case == "protected" else 6.05)
    setup(game)
    game.execute("end_hostiles_turn", {})
    source = game.actors[game.shooter]
    if case == "inactive-source":
        source["active"] = False
    elif case == "downed-source":
        source.update(downed=True, hp=0)
    elif case == "unready-source":
        source["weapon"]["location"] = "SLUNG"
    elif case == "depleted":
        game.world["prepared"]["suppression"]["capacity"] = 0
    elif case == "ended":
        game.world["prepared"]["suppression"]["active"] = False
    elif case == "no-ap":
        game.actors[game.mover]["ap"] = 0
    elif case == "downed-mover":
        game.actors[game.mover].update(downed=True, hp=0)
    elif case.startswith("own-"):
        game.world["prepared"]["suppression"]["owner"] = game.mover
    before = deepcopy(game.state)
    destination = (
        game.actors[game.mover]["position_cell"]
        if case in {"noop", "own-noop"}
        else game.actors[game.shooter]["position_cell"]
        if case == "no-path"
        else [10, 7]
    )
    with patch.object(dice._rng, "randint") as draws:
        game.execute("move_actor", {"actor": game.mover, "destination": destination})
    assert draws.call_count == 0
    original = before[NS + ".entities"][game.mover]
    mover = game.actors[game.mover]
    no_move = case in {"no-ap", "no-path", "noop", "own-noop", "downed-mover"}
    assert mover["ap"] == original["ap"] - (0 if no_move else 2)
    assert mover["position_cell"] == (original["position_cell"] if no_move else [10, 7])
    assert game.actors[game.shooter] == before[NS + ".entities"][game.shooter]
    capacity = game.world["prepared"]["suppression"]["capacity"]
    assert capacity == (
        0 if case == "own-fire" else before[NS + ".state"]["prepared"]["suppression"]["capacity"]
    )
    motions = game.world.get("network_motion", {})
    previous = before[NS + ".state"].get("network_motion", {})
    assert motions.get(game.mover, 0) == previous.get(game.mover, 0) + (0 if no_move else 1)


def test_ordinary_queue_incapacitation_preserves_partial_move_and_stops_next_step(
    candidate, tmp_path
):
    scene = game_for(candidate, True, "empty", wall_end=5.71)
    scene.actors[scene.mover].update(hp=1, res=0)
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3",
        "aim-owner",
        candidate.read_bytes(),
    ) as (owner, admitted):
        game = prepare_owner(owner, admitted, scene)
        before = owner.snapshot(game.campaign)
        request = map_request(owner, game)
        step = {
            "step_id": "move",
            "source": {"kind": "panel", "surface_id": "operations.map", "action_id": "move"},
            "selection": request["selection"],
        }
        queue = owner.compose_action_sequence(
            campaign_id=game.campaign,
            session_id=SESSION,
            sequence_id="downed-queue",
            expected_revision=before.revision,
            steps=[
                step,
                {
                    **step,
                    "step_id": "later",
                    "selection": {**step["selection"], "destination": [10, 8]},
                },
            ],
        )
        assert owner.snapshot(game.campaign) == before
        # Face 19 is a real HIT at the first exposure; the retained face-15
        # case legitimately missed under the unchanged cover/recoil modifiers.
        with patch.object(dice._rng, "randint", return_value=19) as draws:
            result = owner.execute_action_sequence(
                campaign_id=game.campaign,
                session_id=SESSION,
                sequence_id=queue["sequence_id"],
                expected_version=queue["version"],
                confirmation=queue["confirmation"],
            )
        assert draws.call_count == 1
        final = owner.snapshot(game.campaign)
        actor = final.state[NS + ".entities"][game.mover]
        assert actor["downed"] and actor["position_cell"] != (10, 7)
        assert actor["ap"] == before.state[NS + ".entities"][game.mover]["ap"] - 1
        hit = final.state[NS + ".state"]["last_trigger"]["suppression"]
        assert hit["outcome"] == "HIT" and hit["target_reached"]
        assert final.state[NS + ".state"]["last_move"]["status"] == "INTERRUPTED_BY_PREPARED_FIRE"
        assert result["steps"][0]["status"] == "committed"
        assert result["steps"][1]["status"] == "pending"
        assert result["status"] in {"failed", "paused"}
        assert final.revision == before.revision + 1


def test_ordinary_stale_cancel_and_lost_reply_do_not_replay_movement(candidate, tmp_path):
    scene = game_for(candidate, True, "empty", wall_end=6.05)
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3",
        "aim-owner",
        candidate.read_bytes(),
    ) as (owner, admitted):
        game = prepare_owner(owner, admitted, scene)
        before = owner.snapshot(game.campaign)
        request = map_request(owner, game)
        step = {
            "step_id": "move",
            "source": {
                "kind": "panel",
                "surface_id": "operations.map",
                "action_id": "move",
            },
            "selection": request["selection"],
        }
        queue = owner.compose_action_sequence(
            campaign_id=game.campaign,
            session_id=SESSION,
            sequence_id="cancel",
            expected_revision=before.revision,
            steps=[step],
        )
        cleared = owner.compose_action_sequence(
            campaign_id=game.campaign,
            session_id=SESSION,
            sequence_id="cancel",
            expected_revision=before.revision,
            expected_version=queue["version"],
            steps=[],
        )
        assert not cleared["steps"] and owner.snapshot(game.campaign) == before
        with pytest.raises((CommandRejected, StaleRevision)):
            owner.execute_panel_action(**{**request, "expected_revision": before.revision - 1})
        assert owner.snapshot(game.campaign) == before
        # Drop the response deliberately. No automatic retry or second call follows.
        with patch.object(dice._rng, "randint", return_value=1) as draws:
            owner.execute_panel_action(**request)
        assert draws.call_count == 1
        saved = owner.snapshot(game.campaign)
        with patch.object(dice._rng, "randint") as draws:
            receipt = owner.panel_action_receipt(**request)
        assert draws.call_count == 0 and receipt.status.value == "committed"
        assert owner.snapshot(game.campaign) == saved


def test_ordinary_posture_keeps_real_height_muzzle_and_preview_cost(candidate, tmp_path):
    scene = game_for(candidate, True, "empty", wall_end=5.71)
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3",
        "aim-owner",
        candidate.read_bytes(),
    ) as (owner, admitted):
        game = prepare_owner(owner, admitted, scene)
        game.execute("change_stance", {"actor": game.mover, "stance": "CROUCHED"})
        before = owner.snapshot(game.campaign)
        actor = before.state[NS + ".entities"][game.mover]
        request = map_request(owner, game)
        preview = owner.preview_panel_action(**request)
        assert owner.snapshot(game.campaign) == before
        cost = next(
            row["value"] for row in preview["preview"]["summary"] if row["label"] == "AP cost"
        )
        assert cost == 4
        with patch.object(dice._rng, "randint", return_value=1) as draws:
            owner.execute_panel_action(**request)
        assert draws.call_count == 1
        after = owner.snapshot(game.campaign)
        moved = after.state[NS + ".entities"][game.mover]
        assert moved["ap"] == actor["ap"] - cost
        assert moved["center"][2] == actor["center"][2]
        assert moved["muzzle"][2] == actor["muzzle"][2]
        assert moved["position_cell"] == (10, 7)


def test_ordinary_starting_exposed_can_stop_before_any_distance_or_ap(candidate, tmp_path):
    scene = game_for(candidate, True, "visible")
    scene.actors[scene.mover].update(hp=1, res=0)
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3",
        "aim-owner",
        candidate.read_bytes(),
    ) as (owner, admitted):
        # Preserve the real initial-exposure dispatch, with a declared miss.
        with patch.object(dice._rng, "randint", return_value=1):
            game = prepare_owner(owner, admitted, scene)
        before = owner.snapshot(game.campaign)
        actor = before.state[NS + ".entities"][game.mover]
        request = map_request(owner, game, destination=[10, 6])
        with patch.object(dice._rng, "randint", return_value=19) as draws:
            owner.execute_panel_action(**request)
        assert draws.call_count == 1
        after = owner.snapshot(game.campaign)
        moved = after.state[NS + ".entities"][game.mover]
        assert moved["downed"]
        assert moved["ap"] == actor["ap"]
        assert moved["position_cell"] == actor["position_cell"]
        state, prior = after.state[NS + ".state"], before.state[NS + ".state"]
        assert state["last_move"]["ap"] == 0 and state["last_move"]["cost"] == 0
        assert state.get("network_motion") == prior.get("network_motion")


def test_ordinary_separate_trigger_windows_fail_closed(candidate):
    from sylvia_gm.domain.errors import RulesetPreconditionRejected

    game = game_for(candidate, True, "empty", wall_end=5.71)
    later = deepcopy(game.world["spatial_scene"]["zones"][0])
    later["id"] = "later-watch-zone"
    later["shape"]["min"][1] = 7
    game.world["spatial_scene"]["zones"].append(later)
    setup(game)
    game.execute("prepare_overwatch", {"actor": "B_VESPER_3", "zone_id": later["id"]})
    game.execute("end_hostiles_turn", {})
    before = deepcopy(game.state)
    with (
        patch.object(dice._rng, "randint") as draws,
        pytest.raises(
            RulesetPreconditionRejected,
            match="unsupported",
        ),
    ):
        game.execute("move_actor", {"actor": game.mover, "destination": [10, 7]})
    assert draws.call_count == 0 and game.state == before


@pytest.mark.parametrize("consumer", ["map", "queue"])
def test_gm_only_side_keeps_player_controls_blocked(candidate, tmp_path, consumer):
    scene = game_for(candidate, False, "empty", wall_end=6.05)
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3",
        "aim-owner",
        candidate.read_bytes(),
    ) as (owner, admitted):
        game = prepare_owner(owner, admitted, scene)
        before = owner.snapshot(game.campaign)
        with pytest.raises(CommandRejected, match="GM decision"):
            ordinary_call(owner, game, consumer)
        assert owner.snapshot(game.campaign) == before


@pytest.mark.parametrize("kind", ["overwatch", "suppression"])
def test_ordinary_friendly_watch_is_not_friendly_suppression(candidate, kind):
    game = game_for(candidate, True, "empty", wall_end=5.71)
    game.actors[game.mover]["side"] = game.actors[game.shooter]["side"]
    # Start this declared friendly actor's real team turn, rather than borrowing
    # the inactive enemy's zero-AP state from the original crossing fixture.
    side = game.actors[game.shooter]["side"]
    game.execute("end_allies_turn" if side == "allies" else "end_hostiles_turn", {})
    game.execute("end_hostiles_turn" if side == "allies" else "end_allies_turn", {})
    if kind == "suppression":
        setup(game)
    else:
        game.execute(
            "prepare_overwatch",
            {
                "actor": game.shooter,
                "zone_id": "declared-prepared-lane",
            },
        )
    before = deepcopy(game.actors[game.shooter])
    with patch.object(dice._rng, "randint", return_value=1) as draws:
        game.execute("move_actor", {"actor": game.mover, "destination": [10, 7]})
    assert draws.call_count == (1 if kind == "suppression" else 0)
    assert game.actors[game.shooter] == before
    assert game.actors[game.mover]["position_cell"] == [10, 7]
    if kind == "suppression":
        assert game.world["prepared"][kind]["capacity"] == 1
    else:
        assert game.world["prepared"][kind]["shots"] == 1


def test_ordinary_movement_does_not_reorient_a_self_occluded_suppression_source(candidate):
    game = game_for(candidate, True, "empty", wall_end=5.71)
    watcher = game.shooter
    friendly = "A_VESPER_2"
    side = game.actors[watcher]["side"]
    # This authored left-facing rifle cannot fire right through its own body.
    # Ordinary movement must retain that pose, rather than silently reorient it.
    game.execute("end_allies_turn" if side == "allies" else "end_hostiles_turn", {})
    game.execute("move_actor", {"actor": friendly, "destination": [9, 4]})
    game.execute("end_hostiles_turn" if side == "allies" else "end_allies_turn", {})
    game.execute("prepare_overwatch", {
        "actor": watcher, "zone_id": "declared-prepared-lane",
    })
    game.execute("end_allies_turn" if side == "allies" else "end_hostiles_turn", {})
    assert game.actors[friendly]["side"] == game.actors[game.mover]["side"]
    game.execute("establish_suppression", {
        "actor": friendly, "zone_id": "declared-prepared-lane", "rounds": 20,
    })
    before = deepcopy(game.actors)
    with patch.object(dice._rng, "randint", return_value=1) as draws:
        game.execute("move_actor", {"actor": game.mover, "destination": [10, 7]})
    assert draws.call_count == 1
    assert game.world["last_trigger"]["first_effect"] == "OVERWATCH"
    assert game.actors[game.mover]["position_cell"] == [10, 7]
    assert game.actors[friendly] == before[friendly]
    assert game.actors[watcher]["ap"] == before[watcher]["ap"]
    assert game.actors[watcher]["weapon"]["magazine"]["rounds"] == (
        before[watcher]["weapon"]["magazine"]["rounds"] - 1
    )
    assert game.world["prepared"]["overwatch"]["shots"] == 0
    assert game.world["prepared"]["suppression"]["capacity"] == 2
    assert game.actors[friendly]["muzzle"] == [8.91, 4.5, 1.4]
    exposure = SpatialEngine(game.world["spatial_scene"]).query({
        "kind": "exposure",
        "map_id": game.world["spatial_scene"]["map"]["id"],
        "observer": {"point": game.actors[friendly]["muzzle"]},
        "target": {"entity_id": game.mover},
        "samples": {"horizontal": 3, "vertical": 3},
        "include": ["geometry", "entities"],
    }).as_payload()
    assert exposure["exposure"] == 0
    assert exposure["occluders"][0]["id"] == friendly


@pytest.mark.parametrize("consumer", ["map", "queue"])
def test_ordinary_unsupported_shape_rejects_atomically_without_fallback(
    candidate, tmp_path, consumer
):
    scene = game_for(candidate, True, "empty", wall_end=6.05)
    next(row for row in scene.world["spatial_scene"]["entities"] if row["id"] == scene.mover)[
        "footprint"
    ] = {"kind": "circle", "radius": 0.4}
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3",
        "aim-owner",
        candidate.read_bytes(),
    ) as (owner, admitted):
        game = prepare_owner(owner, admitted, scene)
        before = owner.snapshot(game.campaign)
        call = ordinary_call(owner, game, consumer)
        with patch.object(dice._rng, "randint") as draws:
            if consumer == "map":
                with pytest.raises(CommandRejected, match="unsupported"):
                    call()
            else:
                result = call()
                assert result["status"] == "failed"
                assert result["steps"][0]["status"] != "committed"
        assert draws.call_count == 0 and owner.snapshot(game.campaign) == before


@pytest.mark.parametrize("consumer", ["map", "queue"])
@pytest.mark.parametrize("kind", ["critical", "order"])
def test_ordinary_choices_recover_before_any_partial_spend(candidate, tmp_path, consumer, kind):
    scene = game_for(candidate, True, "empty", wall_end=5.71)
    # Deliberately large diagnostic pools keep both critical choices reachable.
    # These values never enter package defaults or the starting-profile proposal.
    if kind == "order":
        scene.actors[scene.mover].update(hp=100, res=100)
    database = tmp_path / "authority.sqlite3"
    with admitted_fixture_owner(database, "aim-owner", candidate.read_bytes()) as (owner, admitted):
        watcher = "B_VESPER_3" if kind == "order" else None
        game = prepare_owner(owner, admitted, scene, watcher=watcher)
        before = owner.snapshot(game.campaign)
        call = ordinary_call(owner, game, consumer)
        with patch.object(dice._rng, "randint", return_value=20) as draws:
            call()
        assert draws.call_count == (0 if kind == "order" else 1)
        assert owner.snapshot(game.campaign) == before
        public = next(
            row.safe_payload
            for row in owner.player_projection(game.campaign)
            if row.kind.value == "mechanics_choice"
        )
        assert public["choice_id"] == (
            "movement-trigger-order" if kind == "order" else "suppression-critical-effect"
        )
        pending_export = tmp_path / "pending.sylvia-gm-campaign"
        owner.export_campaign("pending-ordinary", game.campaign, pending_export)
    with AuthorityOwner.open(tmp_path / "fresh.sqlite3", "aim-owner") as owner:
        owner.set_admin_full_audit(True)
        owner.recover_campaign(recovery_id="pending-ordinary", source=pending_export)
        assert owner.snapshot(game.campaign) == before
        resolutions = []
        while True:
            public_choices = [
                row.safe_payload
                for row in owner.player_projection(game.campaign)
                if row.kind.value == "mechanics_choice"
            ]
            if not public_choices:
                break
            choice = public_choices[0]
            resolution = ResolveMechanicsChoiceCommand(
                campaign_id=game.campaign,
                action_id=choice["action_id"],
                resolution_id=f"ordinary-choice-{len(resolutions)}",
                owner_id="aim-owner",
                expected_revision=before.revision,
                choice_id=choice["choice_id"],
                choice_sequence=choice.get("choice_sequence", 0),
                option_id="SUPPRESSION"
                if choice["choice_id"] == "movement-trigger-order"
                else "DEEP_HIT",
                session_id=SESSION,
            )
            with patch.object(dice._rng, "randint", return_value=20):
                receipt = owner.resolve_mechanics_choice(resolution)
            resolutions.append(resolution)
            assert len(resolutions) <= 3
            if str(receipt.status) != "committed":
                assert owner.snapshot(game.campaign) == before
        assert len(resolutions) == (3 if kind == "order" else 1)
        final = owner.snapshot(game.campaign)
        assert final.revision == before.revision + 1
        assert final.state[NS + ".state"]["prepared"]["suppression"]["capacity"] == 1
        actor = final.state[NS + ".entities"][game.mover]
        assert actor["position_cell"] == (10, 7)
        assert actor["ap"] == before.state[NS + ".entities"][game.mover]["ap"] - 2
        if kind == "order":
            state = final.state[NS + ".state"]
            assert state["last_trigger"]["first_effect"] == "SUPPRESSION"
            assert state["prepared"]["overwatch"]["shots"] == 0
            assert (
                final.state[NS + ".entities"][watcher]["weapon"]["magazine"]["rounds"]
                == before.state[NS + ".entities"][watcher]["weapon"]["magazine"]["rounds"] - 1
            )
        with patch.object(dice._rng, "randint") as draws:
            assert owner.resolve_mechanics_choice(resolutions[-1]) == receipt
        assert draws.call_count == 0 and owner.snapshot(game.campaign) == final
        if consumer == "queue":
            reconciled = owner.action_sequence(
                session_id=SESSION, sequence_id="ordinary-move-queue"
            )
            assert reconciled["steps"][0]["status"] == "committed"
            assert owner.snapshot(game.campaign) == final
