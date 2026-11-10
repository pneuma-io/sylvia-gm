"""Current player fire, typed preparation and interrupted DEEP stage boundaries."""

import json
import zipfile
from copy import deepcopy
from unittest.mock import patch

import pytest

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.plan_state_admission import validate_plan_state
from sylvia_gm.domain.content_validation import ContentShapeError
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import manifest_from_record
from tests.admitted_library_fixture import admitted_fixture_owner
from tests.test_td_deep_aim import ENTRY, NS, fire, map_request, prepare, scene_for
from tests.test_td_exposure_ownership import game_for
from tests.test_td_rifleman_entry_boundaries import start, step
from tests.td_source_fixture import candidate as candidate_fixture
from tests.test_td_suppression_aim import PackageProcedures

candidate = candidate_fixture


def test_actual_player_deep_preview_and_fire_commit_replay(candidate, tmp_path):
    database = tmp_path / "authority.sqlite3"
    with admitted_fixture_owner(database, "aim-owner", candidate.read_bytes()) as (owner, admitted):
        game = start(owner, admitted, scene_for(candidate))
        prepare(owner, game)
        stage_request = map_request(owner, game)
        before = owner.snapshot(game.campaign)
        with pytest.raises(CommandRejected):
            owner.preview_panel_action(
                **{
                    **stage_request,
                    "selection": {
                        **stage_request["selection"],
                        "actor": game.mover,
                    },
                }
            )
        assert owner.snapshot(game.campaign) == before
        stage_preview = owner.preview_panel_action(**stage_request)
        before = owner.snapshot(game.campaign)
        assert owner.snapshot(game.campaign) == before
        stage = owner.execute_panel_action(
            **stage_request, confirmation=stage_preview.get("confirmation")
        )
        assert stage.status.value == "committed"
        paid = owner.snapshot(game.campaign)
        assert (
            owner.execute_panel_action(
                **stage_request, confirmation=stage_preview.get("confirmation")
            )
            == stage
        )
        assert owner.snapshot(game.campaign) == paid
        request = map_request(owner, game, "fire", "player-shot")
        preview = owner.preview_panel_action(**request)
        assert owner.snapshot(game.campaign) == paid
        assert (
            next(
                row["value"]
                for row in preview["preview"]["summary"]
                if row["label"] == "Deep Aim difficulty"
            )
            == -2
        )
        with patch.object(dice._rng, "randint", return_value=10) as draws:
            receipt = owner.execute_panel_action(**request, confirmation=preview["confirmation"])
        assert draws.call_count == 1 and receipt.status.value == "committed"
        assert game.actors[game.shooter]["ap"] == 2
        assert game.actors[game.shooter]["weapon"]["magazine"]["rounds"] == 9
        assert game.actors[game.shooter]["weapon"]["current_recoil"] == 3
        assert game.world["last_attack"]["actual_difficulty"] == 15
        final = owner.snapshot(game.campaign)
        assert (
            owner.execute_panel_action(**request, confirmation=preview["confirmation"]) == receipt
        )
        assert owner.snapshot(game.campaign) == final
    with AuthorityOwner.open(database, "aim-owner") as owner:
        assert owner.snapshot(game.campaign) == final
        assert (
            owner.execute_panel_action(**request, confirmation=preview["confirmation"]) == receipt
        )
        assert owner.snapshot(game.campaign) == final


@pytest.mark.parametrize("mode", ["fire_burst", "fire_full_auto"])
def test_multiround_prepared_attack_uses_one_modifier_and_consumes_one_stage(
    candidate, tmp_path, mode
):
    rows = []
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        for paid in (False, True):
            scene = scene_for(candidate)
            # Actual M7 supports these modes; no new fire mode is added to the precision rifle.
            scene.actors[scene.shooter]["weapon"] = deepcopy(
                PackageProcedures(candidate).actors["vesper"]["weapon"]
            )
            assert {"BURST", "FULL_AUTO"} <= set(
                scene.actors[scene.shooter]["weapon"]["fire_modes"]
            )
            game = start(owner, admitted, scene, campaign=f"multi-{paid}")
            args = prepare(owner, game)
            if paid:
                step(owner, game, "enter_deep_aim", args)
            before = deepcopy(game.actors[game.shooter])
            attack = fire(owner, game, args, 1, mode)
            after = game.actors[game.shooter]
            assert attack["deep_aim_offset"] == (-2 if paid else 0)
            assert not after["sniper_preparation"]["deep"]
            assert not after["sniper_preparation"]["valid"]
            rows.append((attack, before, after))
        assert rows[0][0]["actual_difficulty"] - rows[1][0]["actual_difficulty"] == 2
        assert rows[0][2]["weapon"] == rows[1][2]["weapon"]
        assert rows[0][1]["ap"] - rows[0][2]["ap"] == rows[1][1]["ap"] - rows[1][2]["ap"]


def test_new_normal_aim_discards_the_paid_stage_without_changing_ordinary_aim(candidate, tmp_path):
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = start(owner, admitted, scene_for(candidate))
        args = prepare(owner, game)
        step(owner, game, "enter_deep_aim", args)
        prepare(owner, game)
        assert game.actors[game.shooter]["sniper_preparation"]["deep"] is False
        assert game.actors[game.shooter]["aim"]["ap"] == 1
        assert fire(owner, game, args)["actual_difficulty"] == 17


def test_paid_state_has_real_plan_types_without_adding_defaults_to_old_actors(candidate, tmp_path):
    with zipfile.ZipFile(candidate) as package:
        manifest = manifest_from_record(
            next(
                row
                for row in json.loads(package.read("manifest.json"))["packages"]
                if row["id"] == NS
            )
        )
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = start(owner, admitted, scene_for(candidate))
        assert "sniper_preparation" not in game.actors[game.shooter]
        args = prepare(owner, game)
        step(owner, game, "enter_deep_aim", args)
        entities = deepcopy(game.actors)
        payload = {
            "initial_state": {
                "mechanics": [{"namespace": NS, "key": "entities", "value": entities}]
            }
        }
        validate_plan_state(payload, manifest)
        for field, value in (("deep", "yes"), ("origin", [10, 10]), ("aim_ap", 3)):
            invalid = deepcopy(payload)
            invalid["initial_state"]["mechanics"][0]["value"][game.shooter]["sniper_preparation"][
                field
            ] = value
            with pytest.raises(ContentShapeError):
                validate_plan_state(invalid, manifest)


def test_weapon_handling_breaks_deep_without_blocking_an_ordinary_followup_shot(
    candidate, tmp_path
):
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3",
        "aim-owner",
        candidate.read_bytes(),
    ) as (owner, admitted):
        game = start(owner, admitted, scene_for(candidate))
        args = prepare(owner, game)
        step(owner, game, "enter_deep_aim", args)
        weapon_id = game.actors[game.shooter]["weapon"]["id"]
        for location in ("STOWED", "READY"):
            step(
                owner,
                game,
                "handle_weapon",
                {
                    "actor": game.shooter,
                    "weapon_id": weapon_id,
                    "location": location,
                    "ap_cost": 1,  # Explicit final GM handling cost for this technical fixture.
                },
            )
            assert game.entry_calls[-1][1].status.value == "committed"
        assert game.actors[game.shooter]["aim"]["point"] is None
        assert game.actors[game.shooter]["ap"] == 2
        attack = fire(owner, game, args)
        assert attack["deep_aim_offset"] == 0
        assert game.actors[game.shooter]["ap"] == 0
        assert game.actors[game.shooter]["weapon"]["magazine"]["rounds"] == 9


@pytest.mark.parametrize("face", [1, 19])
def test_stage_uses_real_incoming_suppression_before_its_own_spend(candidate, tmp_path, face):
    scene = game_for(candidate, True, "visible")
    suppressor, worker = scene.shooter, scene.mover
    scene.shooter, scene.mover = worker, suppressor
    scene.actors[worker].update(class_id="SHARPSHOOTER", specialties=[ENTRY], hp=1, res=0)
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = start(owner, admitted, scene)
        step(owner, game, "end_hostiles_turn", {})
        args = prepare(owner, game)
        step(owner, game, "end_allies_turn", {})
        step(
            owner,
            game,
            "establish_suppression",
            {
                "actor": suppressor,
                "zone_id": "declared-prepared-lane",
                "rounds": 20,
            },
            1,
        )
        step(owner, game, "end_hostiles_turn", {})
        before = deepcopy(game.actors[suppressor])
        capacity = game.world["prepared"]["suppression"]["capacity"]
        invalid = game_for(candidate, True, "visible")
        invalid.state = json.loads(json.dumps(game.state))
        invalid.shooter, invalid.mover = worker, suppressor
        invalid.actors[worker]["specialties"] = []
        negative = start(owner, admitted, invalid, campaign="invalid-stage-under-fire")
        unchanged = owner.snapshot(negative.campaign)
        with pytest.raises(CommandRejected):
            step(owner, negative, "enter_deep_aim", args)
        assert owner.snapshot(negative.campaign) == unchanged
        assert step(owner, game, "enter_deep_aim", args, face) == 1
        assert game.actors[suppressor] == before
        assert game.world["prepared"]["suppression"]["capacity"] == capacity - 1
        result = game.entry_calls[-1][1].result
        if face == 19:
            assert game.actors[worker]["downed"]
            assert not game.actors[worker]["sniper_preparation"]["deep"]
            assert result["context"]["_deep_stage_spent"] == 0
        else:
            assert game.actors[worker]["sniper_preparation"]["deep"]
            assert game.actors[worker]["ap"] == 5
