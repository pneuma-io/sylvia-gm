"""Exact provisional entry values, continuity, physical steps and replay boundaries."""

import json
from copy import deepcopy
from unittest.mock import patch

import pytest

from sylvia_gm import dice
from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import WriterRole, thaw_json_value
from tests.admitted_library_fixture import admitted_fixture_owner
from tests.test_td_exposure_owner import create
from tests.test_td_ordinary_movement import SESSION
from tests.test_td_ordinary_pose import NS, session
from tests.td_source_fixture import candidate as candidate_fixture
from tests.test_td_suppression_aim import PackageProcedures

candidate = candidate_fixture
GUNNER = "specialty.gunner.entry.sustained_fire_discipline"
OPERATOR = "specialty.operator.entry.weapon_manipulation"
CHAIN = "gunner_chain_weapon_id"


def scene_for(candidate, mirrored=False, entries=(GUNNER, OPERATOR)):
    scene = PackageProcedures(candidate, mirrored)
    scene.shooter, scene.mover = "vesper", "hostile_alpha"
    scene.actors[scene.shooter]["specialties"] = list(entries)
    return scene


def start(owner, admitted, scene, campaign="entries"):
    owner.set_admin_full_audit(True)
    game = create(owner, admitted, scene, campaign=campaign)
    session(owner, game)
    game.entry_calls = []
    return game


def step(owner, game, name, parameters, face=1):
    operation = f"entry-{name}-{owner.snapshot(game.campaign).revision}"
    command = ActionCommand(
        campaign_id=game.campaign,
        action_id=operation,
        idempotency_key=operation,
        owner_id="aim-owner",
        writer=WriterRole.DM,
        expected_revision=owner.snapshot(game.campaign).revision,
        turn_id="turn",
        package_id=NS,
        tool_name=name,
        parameters=parameters,
        initiating_session_id=f"{SESSION}-{game.campaign}",
    )
    with patch.object(dice._rng, "randint", return_value=face) as draws:
        receipt = StatelessMcpMechanics(owner).execute_action(command)
    count = draws.call_count
    after = owner.snapshot(game.campaign)
    # A turn-ending tool leaves the current MCP catalog. Its saved command still
    # replays at the owning authority without re-executing or reopening that tool.
    def call(current=None):
        return (current or owner).execute_action(command)
    with patch.object(dice._rng, "randint") as replay_draws:
        assert call() == receipt
    assert replay_draws.call_count == 0 and owner.snapshot(game.campaign) == after
    game.entry_calls.append((call, receipt))
    return count


def fire(owner, game, mode="fire_single"):
    parameters = {
        "attacker": game.shooter,
        "target": game.mover,
        "aim_point": game.actors[game.mover]["center"],
        "critical_effect": "DEVASTATING_HIT",
    }
    if mode == "fire_full_auto":
        parameters["rounds"] = 5
    return step(owner, game, mode, parameters)


def replay_all(owner, game, expected):
    assert owner.snapshot(game.campaign) == expected
    with patch.object(dice._rng, "randint") as draws:
        for call, receipt in game.entry_calls:
            assert call(owner) == receipt
    assert draws.call_count == 0 and owner.snapshot(game.campaign) == expected


@pytest.mark.parametrize("mirrored", [False, True])
def test_chain_survives_reload_partial_recovery_turn_save_and_fresh_recovery(
    candidate, tmp_path, mirrored
):
    database = tmp_path / "authority.sqlite3"
    export = tmp_path / "warm-chain.sylvia-gm-campaign"
    with admitted_fixture_owner(database, "aim-owner", candidate.read_bytes()) as (owner, admitted):
        game = start(owner, admitted, scene_for(candidate, mirrored))
        assert fire(owner, game) == 1
        assert game.actors[game.shooter]["weapon"]["current_recoil"] == 2
        assert fire(owner, game) == 1
        assert game.actors[game.shooter]["weapon"]["current_recoil"] == 3
        step(owner, game, "reload_weapon", {"actor": game.shooter, "disposition": "RETAIN"})
        assert game.actors[game.shooter]["ap"] == 1
        step(owner, game, "recover_recoil", {"actor": game.shooter, "ap": 1})
        actor = game.actors[game.shooter]
        assert actor["weapon"]["current_recoil"] == 1 and actor["ap"] == 0
        assert actor[CHAIN] == actor["weapon"]["id"]
        warm = owner.snapshot(game.campaign)
        owner.export_campaign("warm-entry", game.campaign, export)
    with AuthorityOwner.open(database, "aim-owner") as owner:
        replay_all(owner, game, warm)
    recovered_database = tmp_path / "recovered.sqlite3"
    with AuthorityOwner.open(recovered_database, "aim-owner") as owner:
        owner.set_admin_full_audit(True)
        receipt = owner.recover_campaign(recovery_id="warm-entry", source=export)
        assert owner.recover_campaign(recovery_id="warm-entry", source=export) == receipt
        replay_all(owner, game, warm)
        game.owner = owner
        side = game.actors[game.shooter]["side"]
        first, second = (
            ("end_allies_turn", "end_hostiles_turn")
            if side == "allies"
            else ("end_hostiles_turn", "end_allies_turn")
        )
        for turn in (first, second):
            step(owner, game, turn, {})
            assert game.actors[game.shooter][CHAIN] == "service_rifle"
            assert game.actors[game.shooter]["weapon"]["current_recoil"] == 1
        assert fire(owner, game) == 1
        assert game.actors[game.shooter]["weapon"]["current_recoil"] == 2
        step(owner, game, "recover_recoil", {"actor": game.shooter, "ap": 1})
        assert game.actors[game.shooter]["weapon"]["current_recoil"] == 0
        assert game.actors[game.shooter][CHAIN] is None
        assert fire(owner, game) == 1
        assert game.actors[game.shooter]["weapon"]["current_recoil"] == 2
        final = owner.snapshot(game.campaign)
        replay_all(owner, game, final)
        (tmp_path / "continuity.json").write_text(
            json.dumps(
                {
                    "mirrored": mirrored,
                    "warm": thaw_json_value(warm.state[NS + ".entities"][game.shooter]),
                    "final": thaw_json_value(final.state[NS + ".entities"][game.shooter]),
                    "receipts": len(game.entry_calls),
                },
                indent=2,
            )
            + "\n"
        )
    with AuthorityOwner.open(recovered_database, "aim-owner") as owner:
        replay_all(owner, game, final)


@pytest.mark.parametrize("mirrored", [False, True])
def test_zero_added_gain_preserves_old_recoil_ammunition_and_first_shot(
    candidate, tmp_path, mirrored
):
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        for enabled in (False, True):
            scene = scene_for(candidate, mirrored, (GUNNER,) if enabled else ())
            scene.actors[scene.shooter]["weapon"].update(recoil=1, current_recoil=5)
            game = start(owner, admitted, scene, campaign=f"floor-{enabled}")
            for index in range(3):
                assert fire(owner, game) == 1
                actor = game.actors[game.shooter]
                assert actor["weapon"]["current_recoil"] == (6 if enabled else 6 + index)
                assert actor["weapon"]["magazine"]["rounds"] == 29 - index
            assert actor["ap"] == 0


@pytest.mark.parametrize("mirrored", [False, True])
@pytest.mark.parametrize("mode", ["fire_burst", "fire_full_auto"])
def test_consecutive_multiround_fire_reduces_one_action_gain_not_rounds(
    candidate, tmp_path, mirrored, mode
):
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        results = []
        for enabled in (False, True):
            game = start(
                owner,
                admitted,
                scene_for(candidate, mirrored, (GUNNER,) if enabled else ()),
                campaign=f"multi-{enabled}",
            )
            assert fire(owner, game, mode) == 1
            first = deepcopy(game.actors[game.shooter])
            assert fire(owner, game, mode) == 1
            final = game.actors[game.shooter]
            assert final["weapon"]["current_recoil"] == first["weapon"]["current_recoil"] * 2 - int(
                enabled
            )
            results.append(final)
        assert results[0]["ap"] == results[1]["ap"]
        for key in ("magazine", "chamber", "needs_cycle"):
            assert results[0]["weapon"][key] == results[1]["weapon"][key]


@pytest.mark.parametrize("mirrored", [False, True])
@pytest.mark.parametrize("kind", ["overwatch", "suppression"])
def test_actual_prepared_fire_uses_gain_once_at_the_owning_commit(
    candidate, tmp_path, mirrored, kind
):
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = start(owner, admitted, scene_for(candidate, mirrored, (GUNNER,)))
        assert fire(owner, game) == 1
        parameters = {"actor": game.shooter, "zone_id": kind + "-lane"}
        if kind == "suppression":
            parameters["rounds"] = 20
        step(
            owner,
            game,
            "prepare_overwatch" if kind == "overwatch" else "establish_suppression",
            parameters,
        )
        before = deepcopy(game.actors[game.shooter])
        if kind == "suppression":
            assert before["weapon"]["current_recoil"] == 6
        side = before["side"]
        step(owner, game, "end_allies_turn" if side == "allies" else "end_hostiles_turn", {})
        assert (
            step(
                owner,
                game,
                "resolve_trigger_crossing",
                {
                    "actor": game.mover,
                    "destination": [7, 2],
                    "overwatcher": game.shooter,
                    "suppressor": game.shooter,
                    "first_effect": kind.upper(),
                    "overwatch_critical_effect": "DEEP_HIT",
                    "suppression_critical_effect": "DEEP_HIT",
                },
            )
            == 1
        )
        actor = game.actors[game.shooter]
        assert actor["ap"] == 0 and actor[CHAIN] == "service_rifle"
        if kind == "overwatch":
            assert actor["weapon"]["current_recoil"] == 3
            assert (
                actor["weapon"]["magazine"]["rounds"] == before["weapon"]["magazine"]["rounds"] - 1
            )
        else:
            assert actor["weapon"] == before["weapon"]


@pytest.mark.parametrize("mirrored", [False, True])
def test_switch_resets_both_guns_but_final_handling_totals_are_not_discounted(
    candidate, tmp_path, mirrored
):
    scene = scene_for(candidate, mirrored)
    other = deepcopy(scene.actors[scene.shooter]["weapon"])
    other.update(id="diagnostic-other-physical-gun", location="QUICK_ACCESS", current_recoil=4)
    scene.actors[scene.shooter]["reserve_weapons"] = [other]
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = start(owner, admitted, scene)
        assert fire(owner, game) == 1
        before = deepcopy(game.actors[game.shooter])
        step(
            owner,
            game,
            "switch_weapon",
            {
                "actor": game.shooter,
                "current_weapon_id": "service_rifle",
                "next_weapon_id": "absent",
                "store_location": "QUICK_ACCESS",
                "ap_cost": 2,
            },
        )
        assert game.actors[game.shooter] == before
        step(
            owner,
            game,
            "switch_weapon",
            {
                "actor": game.shooter,
                "current_weapon_id": "service_rifle",
                "next_weapon_id": other["id"],
                "store_location": "QUICK_ACCESS",
                "ap_cost": 2,
            },
        )
        assert game.actors[game.shooter]["ap"] == 2
        assert game.actors[game.shooter][CHAIN] is None
        assert fire(owner, game) == 1
        assert game.actors[game.shooter]["weapon"]["current_recoil"] == 6
        side = game.actors[game.shooter]["side"]
        for turn in (
            ("end_allies_turn", "end_hostiles_turn")
            if side == "allies"
            else ("end_hostiles_turn", "end_allies_turn")
        ):
            step(owner, game, turn, {})
        step(
            owner,
            game,
            "switch_weapon",
            {
                "actor": game.shooter,
                "current_weapon_id": other["id"],
                "next_weapon_id": "service_rifle",
                "store_location": "QUICK_ACCESS",
                "ap_cost": 1,
            },
        )
        assert game.actors[game.shooter]["ap"] == 5 and game.actors[game.shooter][CHAIN] is None
        assert fire(owner, game) == 1
        actor = game.actors[game.shooter]
        assert actor["weapon"]["current_recoil"] == 4 and actor["ap"] == 3
        for location in ("STOWED", "READY"):
            step(
                owner,
                game,
                "handle_weapon",
                {
                    "actor": game.shooter,
                    "weapon_id": "service_rifle",
                    "location": location,
                    "ap_cost": 0,
                },
            )
            assert game.actors[game.shooter]["ap"] == 3
            assert game.actors[game.shooter][CHAIN] == "service_rifle"


@pytest.mark.parametrize("mirrored", [False, True])
@pytest.mark.parametrize("ordinary_cost", [1, 2])
def test_operator_tube_load_and_manual_chamber_costs_preserve_every_physical_step(
    candidate, tmp_path, mirrored, ordinary_cost
):
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        for enabled in (False, True):
            scene = scene_for(candidate, mirrored, (OPERATOR,) if enabled else ())
            scene.actors[scene.shooter]["weapon"].update(
                id="diagnostic-manual-tube",
                feed="TUBE",
                mechanism="MANUAL_ACTION",
                tube_capacity=5,
                spare_shells=2,
                shell_load_ap=ordinary_cost,
                cycle_ap=ordinary_cost,
                chamber="EMPTY",
                needs_cycle=False,
                magazine={"id": "diagnostic-tube", "rounds": 3},
                fire_modes=["SEMI_AUTO"],
            )
            game = start(owner, admitted, scene, campaign=f"manual-{enabled}")
            before = deepcopy(game.actors[game.shooter])
            step(owner, game, "cycle_weapon", {"actor": game.shooter})
            assert game.actors[game.shooter] == before
            step(owner, game, "load_one_shell", {"actor": game.shooter})
            cost = 1 if enabled else ordinary_cost
            actor = game.actors[game.shooter]
            assert actor["ap"] == 6 - cost and actor["weapon"]["magazine"]["rounds"] == 4
            assert actor["weapon"]["chamber"] == "EMPTY" and actor["weapon"]["spare_shells"] == 1
            before = deepcopy(actor)
            assert fire(owner, game) == 0
            assert game.actors[game.shooter] == before
            step(owner, game, "cycle_weapon", {"actor": game.shooter})
            actor = game.actors[game.shooter]
            assert actor["ap"] == 6 - 2 * cost
            assert actor["weapon"]["magazine"]["rounds"] == 3
            assert actor["weapon"]["chamber"] == "LIVE" and not actor["weapon"]["needs_cycle"]
            assert actor["weapon"]["cycle_ap"] == ordinary_cost
            assert fire(owner, game) == 1
            assert game.actors[game.shooter]["weapon"]["chamber"] == "SPENT"
            assert game.actors[game.shooter]["weapon"]["needs_cycle"]


@pytest.mark.parametrize("mirrored", [False, True])
def test_another_class_cannot_gain_rifleman_entry_effects(candidate, tmp_path, mirrored):
    scene = scene_for(candidate, mirrored)
    scene.actors[scene.shooter]["class_id"] = "HACKER"
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = start(owner, admitted, scene)
        assert fire(owner, game) == 1 and fire(owner, game) == 1
        assert game.actors[game.shooter]["weapon"]["current_recoil"] == 4
        assert CHAIN not in game.actors[game.shooter]
        step(owner, game, "reload_weapon", {"actor": game.shooter, "disposition": "DROP"})
        assert game.actors[game.shooter]["ap"] == 0
        assert game.world["last_reload"]["ap_spent"] == 2


@pytest.mark.parametrize("mirrored", [False, True])
def test_ground_retrieval_retains_only_the_same_physical_firearm_chain(
    candidate, tmp_path, mirrored
):
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        for same in (True, False):
            scene = scene_for(candidate, mirrored)
            other = deepcopy(scene.actors[scene.shooter]["weapon"])
            other.update(id="other-ground-gun", location="GROUND", current_recoil=4)
            scene.world.setdefault("ground_objects", {})["other-ground-object"] = {
                "kind": "WEAPON",
                "map_id": scene.world["spatial_scene"]["map"]["id"],
                "position_cell": scene.actors[scene.shooter]["position_cell"],
                "weapon": other,
            }
            game = start(owner, admitted, scene, campaign=f"retrieve-{same}")
            assert fire(owner, game) == 1
            step(
                owner,
                game,
                "drop_weapon",
                {"actor": game.shooter, "weapon_id": "service_rifle", "ap_cost": 0},
            )
            assert game.actors[game.shooter]["weapon"] is None
            ground_id = (
                next(
                    identity
                    for identity, item in game.world["ground_objects"].items()
                    if item and item["weapon"]["id"] == "service_rifle"
                )
                if same
                else "other-ground-object"
            )
            weapon_id = "service_rifle" if same else "other-ground-gun"
            step(
                owner,
                game,
                "pickup_weapon",
                {
                    "actor": game.shooter,
                    "ground_id": ground_id,
                    "weapon_id": weapon_id,
                    "location": "READY",
                    "ap_cost": 1,
                },
            )
            actor = game.actors[game.shooter]
            assert actor["weapon"]["id"] == weapon_id and actor["ap"] == 3
            assert actor[CHAIN] == ("service_rifle" if same else None)
            assert fire(owner, game) == 1
            assert game.actors[game.shooter]["weapon"]["current_recoil"] == (3 if same else 6)
