"""Authored QUICK SOLUTION price/history through admitted owning procedures."""

import json
import zipfile
from copy import deepcopy
from pathlib import Path

import pytest

from sylvia_gm.application.authority import AuthorityOwner, ResolveMechanicsChoiceCommand
from sylvia_gm.domain.errors import CommandRejected, StaleRevision
from sylvia_gm.spatial import SpatialEngine
from tests.admitted_library_fixture import admitted_fixture_owner
from tests.test_td_deep_aim import map_request
from tests.test_td_rifleman_entry_boundaries import start, step
from tests.td_source_fixture import candidate as candidate_fixture
from tests.test_td_suppression_aim import PackageProcedures

candidate = candidate_fixture
ENTRY = "specialty.snapshot.entry.quick_solution"
NS = "pneuma-io.trigger-discipline"
STATE = "quick_solution_turn"


def scene_for(candidate, enabled=True, mirrored=False):
    scene = PackageProcedures(candidate, mirrored)
    scene.shooter, scene.mover = "vesper", "hostile_alpha"
    actor = scene.actors[scene.shooter]
    actor["class_id"] = "SHARPSHOOTER"
    actor["specialties"] = [ENTRY] if enabled else []
    return scene


def aim(owner, game, ap=2, target=None):
    target = target or game.mover
    return step(
        owner,
        game,
        "aim",
        {
            "attacker": game.shooter,
            "target": target,
            "aim_point": game.actors[target]["center"],
            "ap": ap,
        },
    )


@pytest.mark.parametrize("mirrored", [False, True])
def test_first_aim2_costs_one_but_retains_two_investment(candidate, tmp_path, mirrored):
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = start(owner, admitted, scene_for(candidate, mirrored=mirrored))
        before = deepcopy(game.actors[game.shooter])
        assert aim(owner, game) == 0
        actor = game.actors[game.shooter]
        assert actor["ap"] == before["ap"] - 1
        assert actor["aim"]["ap"] == 2
        assert actor["weapon"] == before["weapon"]


@pytest.mark.parametrize("enabled,nominal,paid", [(False, 2, 2), (True, 2, 1), (True, 1, 1)])
def test_price_floor_and_first_performed_aim_consumption(
    candidate, tmp_path, enabled, nominal, paid
):
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = start(owner, admitted, scene_for(candidate, enabled))
        assert STATE not in game.actors[game.shooter]
        aim(owner, game, nominal)
        actor = game.actors[game.shooter]
        assert actor["ap"] == 6 - paid and actor["aim"]["ap"] == nominal
        assert actor[STATE] == {"aimed_targets": [game.mover], "attacked_targets": []}
        aim(owner, game, 2)
        assert game.actors[game.shooter]["ap"] == 6 - paid - 2
        assert game.actors[game.shooter][STATE]["aimed_targets"] == [game.mover]


def test_distinct_targets_and_interleaved_allies_do_not_share_or_reset_history(candidate, tmp_path):
    scene = scene_for(candidate)
    scene.actors["hex"].update(class_id="SHARPSHOOTER", specialties=[ENTRY])
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = start(owner, admitted, scene)
        aim(owner, game)
        step(
            owner,
            game,
            "aim",
            {
                "attacker": "hex",
                "target": game.mover,
                "aim_point": game.actors[game.mover]["center"],
                "ap": 2,
            },
        )
        assert game.actors["hex"]["ap"] == 5
        aim(owner, game)
        assert game.actors[game.shooter]["ap"] == 3
        aim(owner, game, target="drone_target")
        assert game.actors[game.shooter]["ap"] == 2
        assert game.actors[game.shooter][STATE]["aimed_targets"] == [game.mover, "drone_target"]
        assert game.actors["hex"][STATE]["aimed_targets"] == [game.mover]


@pytest.mark.parametrize("mode", ["fire_single", "fire_burst", "fire_full_auto"])
def test_performed_miss_blocks_discount_without_aim_before_it(candidate, tmp_path, mode):
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = start(owner, admitted, scene_for(candidate))
        args = {
            "attacker": game.shooter,
            "target": game.mover,
            "aim_point": game.actors[game.mover]["center"],
            "critical_effect": "DEEP_HIT",
        }
        if mode == "fire_full_auto":
            args["rounds"] = 5
        assert step(owner, game, mode, args, 1) == 1
        before = deepcopy(game.actors[game.shooter])
        assert before[STATE] == {"aimed_targets": [], "attacked_targets": [game.mover]}
        aim(owner, game)
        assert game.actors[game.shooter]["ap"] == before["ap"] - 2
        assert game.actors[game.shooter]["aim"]["ap"] == 2


@pytest.mark.parametrize("mirrored", [False, True])
def test_actual_side_transition_resets_but_actor_selection_does_not(candidate, tmp_path, mirrored):
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = start(owner, admitted, scene_for(candidate, mirrored=mirrored))
        aim(owner, game)
        first, other = (
            ("end_hostiles_turn", "end_allies_turn")
            if mirrored
            else ("end_allies_turn", "end_hostiles_turn")
        )
        step(owner, game, first, {})
        replay_old_turn, old_turn_receipt = game.entry_calls[-1]
        assert game.actors[game.shooter][STATE] == {"aimed_targets": [], "attacked_targets": []}
        assert game.actors[game.shooter]["ap"] == 0
        step(owner, game, other, {})
        aim(owner, game)
        assert game.actors[game.shooter]["ap"] == 5
        saved = owner.snapshot(game.campaign)
        assert replay_old_turn() == old_turn_receipt
        assert owner.snapshot(game.campaign) == saved
        aim(owner, game)
        assert game.actors[game.shooter]["ap"] == 3


@pytest.mark.parametrize("enabled,price", [(False, 2), (True, 1)])
def test_real_player_preview_saved_queue_reopen_export_and_replay(
    candidate, tmp_path, enabled, price
):
    database, exported = tmp_path / "authority.sqlite3", tmp_path / "quick.sylvia-gm-campaign"
    with admitted_fixture_owner(database, "aim-owner", candidate.read_bytes()) as (owner, admitted):
        game = start(owner, admitted, scene_for(candidate, enabled))
        before = owner.snapshot(game.campaign)
        request = map_request(owner, game, action="aim-two", operation="quick-preview")
        preview = owner.preview_panel_action(**request)
        rows = {row["label"]: row["value"] for row in preview["preview"]["summary"]}
        assert rows["AP cost"] == price and rows["AIM investment"] == 2
        assert owner.snapshot(game.campaign) == before
        source = {"kind": "panel", "surface_id": "operations.map", "action_id": "aim-two"}
        queue = owner.compose_action_sequence(
            campaign_id=game.campaign,
            session_id=request["session_id"],
            sequence_id="quick-queue",
            expected_revision=0,
            steps=[{"step_id": "quick", "source": source, "selection": request["selection"]}],
        )
        assert owner.snapshot(game.campaign) == before
    command = {
        "campaign_id": game.campaign,
        "session_id": request["session_id"],
        "sequence_id": "quick-queue",
        "expected_version": queue["version"],
        "confirmation": queue["confirmation"],
    }
    with AuthorityOwner.open(database, "aim-owner") as owner:
        assert owner.snapshot(game.campaign) == before
        result = owner.execute_action_sequence(**command)
        assert result["steps"][0]["status"] == "committed", result
        paid = owner.snapshot(game.campaign)
        actor = paid.state[NS + ".entities"][game.shooter]
        assert actor["ap"] == 6 - price and actor["aim"]["ap"] == 2
        assert owner.execute_action_sequence(**command) == result
        assert owner.snapshot(game.campaign) == paid
        owner.export_campaign("quick-export", game.campaign, exported)
    with AuthorityOwner.open(tmp_path / "restored.sqlite3", "aim-owner") as owner:
        recovery = owner.recover_campaign(recovery_id="quick-recovery", source=exported)
        assert owner.recover_campaign(recovery_id="quick-recovery", source=exported) == recovery
        assert owner.snapshot(game.campaign) == paid
        assert owner.execute_action_sequence(**command) == result
        assert owner.snapshot(game.campaign) == paid


def test_floor_affordability_stale_invalid_and_noeffect_do_not_consume_history(candidate, tmp_path):
    scene = scene_for(candidate)
    scene.actors[scene.shooter]["ap"] = 1
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = start(owner, admitted, scene)
        before = owner.snapshot(game.campaign)
        request = map_request(owner, game, action="aim-two", operation="quick-exact")
        preview = owner.preview_panel_action(**request)
        assert owner.snapshot(game.campaign) == before
        for ap in (0, 3):
            with pytest.raises(CommandRejected):
                aim(owner, game, ap)
            assert owner.snapshot(game.campaign) == before
        bad = {**request, "selection": {**request["selection"], "actor": game.mover}}
        with pytest.raises(CommandRejected):
            owner.preview_panel_action(**bad)
        assert owner.snapshot(game.campaign) == before
        receipt = owner.execute_panel_action(**request, confirmation=preview["confirmation"])
        assert game.actors[game.shooter]["ap"] == 0
        saved = owner.snapshot(game.campaign)
        assert (
            owner.execute_panel_action(**request, confirmation=preview["confirmation"]) == receipt
        )
        assert owner.snapshot(game.campaign) == saved
        with pytest.raises(StaleRevision):
            owner.execute_panel_action(
                **{**request, "operation_id": "stale"}, confirmation=preview["confirmation"]
            )
        assert owner.snapshot(game.campaign) == saved
        history = deepcopy(game.actors[game.shooter][STATE])
        aim(owner, game)
        assert game.actors[game.shooter][STATE] == history
        assert game.actors[game.shooter]["ap"] == 0
        assert game.world["last_attack"]["outcome"] == "AIM_REJECTED"


def test_quick_aim_keeps_deep_preparation_and_ordinary_shot_benefit(candidate, tmp_path):
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        scene = scene_for(candidate)
        scene.actors[scene.shooter]["specialties"].append("specialty.sniper.entry.deep_aim")
        game = start(owner, admitted, scene)
        aim(owner, game)
        args = {
            "attacker": game.shooter,
            "target": game.mover,
            "aim_point": game.actors[game.mover]["center"],
        }
        assert game.actors[game.shooter]["sniper_preparation"]["aim_ap"] == 2
        step(owner, game, "enter_deep_aim", args)
        assert game.actors[game.shooter]["ap"] == 4
        step(owner, game, "fire_single", {**args, "critical_effect": "DEEP_HIT"}, 1)
        assert game.actors[game.shooter]["ap"] == 2
        assert game.world["last_attack"]["deep_aim_offset"] == -2
        assert game.actors[game.shooter][STATE]["attacked_targets"] == [game.mover]


def test_typed_state_old_actor_and_only_quick_support_row(candidate, tmp_path):
    from sylvia_gm.application.plan_state_admission import validate_plan_state
    from sylvia_gm.domain.content_validation import ContentShapeError
    from sylvia_gm.domain.package_library import manifest_from_record

    with zipfile.ZipFile(candidate) as package:
        manifest = manifest_from_record(
            next(
                row
                for row in json.loads(package.read("manifest.json"))["packages"]
                if row["id"] == NS
            )
        )
        support = json.loads(package.read(f"packages/{NS}/support/gameplay-resolution.json"))[
            "records"
        ]
    assert (
        next(row for row in support if row["id"] == ENTRY)["gameplay_resolution"]
        == "engine_specialized"
    )
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = start(owner, admitted, scene_for(candidate))
        assert STATE not in game.actors[game.shooter]
        aim(owner, game)
        payload = {
            "initial_state": {
                "mechanics": [{"namespace": NS, "key": "entities", "value": deepcopy(game.actors)}]
            }
        }
        validate_plan_state(payload, manifest)
        for value in (
            {},
            {"aimed_targets": "bad", "attacked_targets": []},
            {"aimed_targets": [True], "attacked_targets": []},
            {"aimed_targets": [game.mover, game.mover], "attacked_targets": []},
        ):
            invalid = deepcopy(payload)
            invalid["initial_state"]["mechanics"][0]["value"][game.shooter][STATE] = value
            with pytest.raises(ContentShapeError):
                validate_plan_state(invalid, manifest)


def test_changed_price_stops_queued_suffix_without_second_spend(candidate, tmp_path):
    scene = scene_for(candidate)
    scene.actors[scene.shooter]["ap"] = 2
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = start(owner, admitted, scene)
        request = map_request(owner, game, action="aim-two")
        queue = owner.compose_action_sequence(
            campaign_id=game.campaign,
            session_id=request["session_id"],
            sequence_id="quick-twice",
            expected_revision=0,
            steps=[
                {
                    "step_id": identity,
                    "source": {
                        "kind": "panel",
                        "surface_id": "operations.map",
                        "action_id": "aim-two",
                    },
                    "selection": request["selection"],
                }
                for identity in ("first", "second")
            ],
        )
        assert owner.snapshot(game.campaign).revision == 0
        result = owner.execute_action_sequence(
            campaign_id=game.campaign,
            session_id=request["session_id"],
            sequence_id="quick-twice",
            expected_version=queue["version"],
            confirmation=queue["confirmation"],
        )
        assert result["steps"][0]["status"] == "committed"
        assert result["steps"][1]["status"] != "committed"
        assert len(result["committed_action_ids"]) == 1
        assert game.actors[game.shooter]["ap"] == 1
        assert game.actors[game.shooter][STATE]["aimed_targets"] == [game.mover]


@pytest.mark.parametrize("face", [1, 19, 20])
def test_incoming_suppression_interrupts_before_quick_use_and_pending_recovery(
    candidate, tmp_path, face
):
    from unittest.mock import patch

    from sylvia_gm import dice
    from tests.test_td_exposure_ownership import game_for
    from tests.test_td_ordinary_movement import SESSION

    scene = game_for(candidate, True, "visible")
    suppressor, worker = scene.shooter, scene.mover
    scene.shooter, scene.mover = worker, suppressor
    scene.actors[worker].update(class_id="SHARPSHOOTER", specialties=[ENTRY], hp=1, res=0)
    exported = tmp_path / "pending.sylvia-gm-campaign"
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = start(owner, admitted, scene)
        step(
            owner,
            game,
            "establish_suppression",
            {"actor": suppressor, "zone_id": "declared-prepared-lane", "rounds": 20},
            1,
        )
        step(owner, game, "end_hostiles_turn", {})
        before = owner.snapshot(game.campaign)
        assert STATE not in game.actors[worker]
        aim(owner, game, 2) if face == 1 else step(
            owner,
            game,
            "aim",
            {
                "attacker": worker,
                "target": suppressor,
                "aim_point": game.actors[suppressor]["center"],
                "ap": 2,
            },
            face,
        )
        if face == 20:
            assert owner.snapshot(game.campaign) == before
            assert str(game.entry_calls[-1][1].status) == "awaiting_choice"
            owner.export_campaign("pending-quick", game.campaign, exported)
        elif face == 19:
            assert game.actors[worker]["downed"] and game.actors[worker]["ap"] == 6
            assert STATE not in game.actors[worker]
        else:
            assert game.actors[worker]["ap"] == 5
            assert game.actors[worker]["aim"]["ap"] == 2
            assert game.actors[worker][STATE]["aimed_targets"] == [suppressor]
    if face == 20:
        with AuthorityOwner.open(tmp_path / "recovered.sqlite3", "aim-owner") as owner:
            owner.recover_campaign(recovery_id="pending-quick", source=exported)
            assert owner.snapshot(game.campaign) == before
            choice = next(
                row.safe_payload
                for row in owner.player_projection(game.campaign)
                if row.kind.value == "mechanics_choice"
            )
            command = ResolveMechanicsChoiceCommand(
                campaign_id=game.campaign,
                action_id=choice["action_id"],
                resolution_id="quick-critical",
                owner_id="aim-owner",
                expected_revision=before.revision,
                choice_id=choice["choice_id"],
                choice_sequence=choice.get("choice_sequence", 0),
                option_id="DEEP_HIT",
                session_id=f"{SESSION}-{game.campaign}",
            )
            with patch.object(dice._rng, "randint", return_value=20):
                receipt = owner.resolve_mechanics_choice(command)
            after = owner.snapshot(game.campaign)
            actor = after.state[NS + ".entities"][worker]
            assert actor["downed"] and actor["ap"] == 6 and STATE not in actor
            assert owner.resolve_mechanics_choice(command) == receipt
            assert owner.snapshot(game.campaign) == after


def test_e039_aim2_single_and_remaining_ap_ordinary_movement(candidate, tmp_path):
    import hashlib
    import math

    fixture = Path(__file__).parent / "fixtures/td-snapshot-e039-v99.json"
    assert (
        hashlib.sha256(fixture.read_bytes()).hexdigest()
        == "df9778d8a1a7274b4497c06cdb3367a98288dca91f25a60dd40ab321a10949a7"
    )
    control = json.loads(fixture.read_bytes())["controls"][1]
    scene = PackageProcedures(candidate)
    scene.state = deepcopy(control["raw"])
    scene.shooter, scene.mover = control["actor"], control["target"]
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = start(owner, admitted, scene, campaign="e039")
        initial = deepcopy(game.actors)
        aim(owner, game, 2)
        assert game.actors[game.shooter]["ap"] == 5
        step(
            owner,
            game,
            "fire_single",
            {
                "attacker": game.shooter,
                "target": game.mover,
                "aim_point": game.actors[game.mover]["center"],
                "critical_effect": "DEEP_HIT",
            },
            1,
        )
        actor = game.actors[game.shooter]
        assert actor["ap"] == 3
        goal = control["objective_goal"]
        query = {
            "kind": "path",
            "map_id": game.world["spatial_scene"]["map"]["id"],
            "start": actor["position_cell"],
            "goal": [goal["x"], goal["y"]],
            "allow_diagonal": True,
            "corner_policy": "forbid_cutting",
            "include_entities": True,
            "moving_entity_id": game.shooter,
            "max_steps": 256,
        }
        engine = SpatialEngine(game.world["spatial_scene"])
        route = engine.query(query).as_payload()
        assert route["found"]
        reachable = []
        for cell in route["cells"][1:5]:
            prefix = engine.query({**query, "goal": cell}).as_payload()
            if (
                prefix["found"]
                and math.ceil((prefix["cost"] - 0.000001) / actor["movement_m_per_ap"])
                <= actor["ap"]
            ):
                reachable.append((cell, prefix))
        assert reachable
        destination, prefix = reachable[-1]
        before_move = deepcopy(actor)
        step(owner, game, "move_actor", {"actor": game.shooter, "destination": destination})
        moved = game.actors[game.shooter]
        assert moved["position_cell"] == destination
        assert moved["ap"] == before_move["ap"] - math.ceil(
            (prefix["cost"] - 0.000001) / actor["movement_m_per_ap"]
        )
        assert moved["weapon"] == before_move["weapon"]
        assert moved[STATE] == before_move[STATE]
        assert all(
            game.actors[key] == value for key, value in initial.items() if key != game.shooter
        )
        (tmp_path / "e039-proof.json").write_text(
            json.dumps(
                {
                    "input_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
                    "original_goal": goal,
                    "source_path_prefix": prefix,
                    "destination": destination,
                    "nominal_aim": 2,
                    "paid_aim": 1,
                    "after": moved,
                    "scope": "one current admitted owner path; no historical640-result transfer",
                },
                indent=2,
            )
            + "\n"
        )


@pytest.mark.parametrize("blocked", ["no-ap", "stowed", "downed", "empty-chamber"])
def test_rejected_or_noeffect_attempt_never_creates_performed_history(candidate, tmp_path, blocked):
    scene = scene_for(candidate)
    actor = scene.actors[scene.shooter]
    if blocked == "no-ap":
        actor["ap"] = 0
    elif blocked == "stowed":
        actor["weapon"]["location"] = "STOWED"
    elif blocked == "downed":
        actor["downed"] = True
    else:
        actor["weapon"]["chamber"] = "EMPTY"
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = start(owner, admitted, scene)
        before = deepcopy(game.actors[game.shooter])
        if blocked == "empty-chamber":
            assert (
                step(
                    owner,
                    game,
                    "fire_single",
                    {
                        "attacker": game.shooter,
                        "target": game.mover,
                        "aim_point": game.actors[game.mover]["center"],
                        "critical_effect": "DEEP_HIT",
                    },
                )
                == 0
            )
        else:
            assert aim(owner, game) == 0
        assert game.actors[game.shooter] == before
        assert STATE not in game.actors[game.shooter]
        if blocked == "empty-chamber":
            aim(owner, game)
            assert game.actors[game.shooter]["ap"] == 5


def test_ordinary_prepared_miss_records_history_until_actual_next_turn(candidate, tmp_path):
    from tests.test_td_exposure_ownership import game_for

    scene = game_for(candidate, False, "empty")
    scene.actors[scene.shooter].update(class_id="SHARPSHOOTER", specialties=[ENTRY])
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = start(owner, admitted, scene)
        step(
            owner,
            game,
            "prepare_overwatch",
            {"actor": game.shooter, "zone_id": "declared-prepared-lane"},
        )
        rounds = game.actors[game.shooter]["weapon"]["magazine"]["rounds"]
        step(owner, game, "end_allies_turn", {})
        assert (
            step(owner, game, "move_actor", {"actor": game.mover, "destination": [10, 7]}, 1) == 1
        )
        assert game.actors[game.shooter][STATE]["attacked_targets"] == [game.mover]
        assert game.actors[game.shooter]["weapon"]["magazine"]["rounds"] == rounds - 1
        assert game.actors[game.shooter]["ap"] == 0
        step(owner, game, "end_hostiles_turn", {})
        aim(owner, game)
        assert game.actors[game.shooter]["ap"] == 5


def test_existing_combined_sightline_records_aim_without_rediscounting_final_total(
    candidate, tmp_path
):
    from tests.test_td_exposure_ownership import game_for

    scene = game_for(candidate, False, "visible")
    scene.actors[scene.shooter].update(
        class_id="SHARPSHOOTER", specialties=[ENTRY, "specialty.sentinel.entry.prepared_sightline"]
    )
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = start(owner, admitted, scene)
        step(
            owner,
            game,
            "prepare_sentinel_sightline",
            {
                "actor": game.shooter,
                "target": game.mover,
                "zone_id": "declared-prepared-lane",
                "aim_point": game.actors[game.mover]["center"],
                "aim_ap": 2,
                "ap_cost": 2,
                "shot_allowance": 0,
            },
        )
        assert game.actors[game.shooter]["ap"] == 4
        assert game.actors[game.shooter]["aim"]["ap"] == 2
        assert game.actors[game.shooter][STATE]["aimed_targets"] == [game.mover]
        aim(owner, game)
        assert game.actors[game.shooter]["ap"] == 2


def test_wrong_class_does_not_gain_the_specialty_discount(candidate, tmp_path):
    scene = scene_for(candidate)
    scene.actors[scene.shooter]["class_id"] = "RIFLEMAN"
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = start(owner, admitted, scene)
        aim(owner, game)
        assert game.actors[game.shooter]["ap"] == 4
        assert STATE not in game.actors[game.shooter]
