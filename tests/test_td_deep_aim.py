"""Distinct authored DEEP AIM stage through the actual admitted TD owner."""

import hashlib
import json
from copy import deepcopy

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import thaw_json_value
from sylvia_gm.domain.errors import CommandRejected, StaleRevision
from tests.admitted_library_fixture import admitted_fixture_owner
from tests.test_td_ordinary_movement import SESSION
from tests.test_td_rifleman_entry_boundaries import start, step
from tests.td_source_fixture import builder
from tests.td_source_fixture import candidate as candidate_fixture
from tests.test_td_suppression_aim import PackageProcedures

candidate = candidate_fixture
ENTRY = "specialty.sniper.entry.deep_aim"
NS = "pneuma-io.trigger-discipline"


def scene_for(candidate, enabled=True, mirrored=False):
    scene = PackageProcedures(candidate, mirrored)
    scene.shooter, scene.mover = "vesper", "hostile_alpha"
    values = builder.default_manifest().seed_set().values
    for identity, cell in ((scene.shooter, [10, 10]), (scene.mover, [710, 10])):
        side = scene.actors[identity]["side"]
        actor = builder._entity(
            identity,
            side=side,
            class_id="SHARPSHOOTER",
            position=cell,
            weapon="precision_rifle",
            parameter_values=values,
        )
        actor.update(level=5, hp=10, res=8, max_hp=10, max_res=8)
        actor["skills"]["marksmanship"] = 5
        actor["aptitudes"]["precision"] = 1
        actor["specialties"] = [ENTRY] if identity == scene.shooter and enabled else []
        scene.actors[identity] = actor
        body = next(
            row for row in scene.world["spatial_scene"]["entities"] if row["id"] == identity
        )
        body["position"] = [cell[0] + 0.5, cell[1] + 0.5, 0]
    world_map = scene.world["spatial_scene"]["map"]
    world_map["bounds"]["max"][0] = 1000
    return scene


def prepare(owner, game, ap=1):
    args = {
        "attacker": game.shooter,
        "target": game.mover,
        "aim_point": game.actors[game.mover]["center"],
    }
    assert step(owner, game, "aim", {**args, "ap": ap}) == 0
    assert game.actors[game.shooter]["aim"]["ap"] == ap
    assert game.actors[game.shooter]["aim"]["target"] == game.mover
    return args


def test_separate_paid_deep_stage_exists_after_ordinary_aim(candidate, tmp_path):
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = start(owner, admitted, scene_for(candidate))
        args = prepare(owner, game)
        before = deepcopy(game.actors[game.shooter])
        step(owner, game, "enter_deep_aim", args)
        assert game.entry_calls[-1][1].status.value == "committed"
        assert game.actors[game.shooter]["ap"] == before["ap"] - 1
        assert game.actors[game.shooter]["aim"] == before["aim"]
        assert game.actors[game.shooter]["sniper_preparation"]["deep"] is True


def fire(owner, game, args, face=10, mode="fire_single"):
    supplied = {**args, "critical_effect": "DEVASTATING_HIT"}
    if mode == "fire_full_auto":
        supplied["rounds"] = 5
    assert step(owner, game, mode, supplied, face) == 1
    assert game.entry_calls[-1][1].status.value == "committed"
    return deepcopy(game.world["last_attack"])


@pytest.mark.parametrize("aim_ap", [1, 2])
@pytest.mark.parametrize("face", [1, 10, 20])
def test_retained_faces_distinguish_normal_aim_from_separate_paid_deep(
    candidate,
    tmp_path,
    aim_ap,
    face,
):
    rows = []
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        for enabled, paid in ((False, False), (True, False), (True, True)):
            game = start(
                owner, admitted, scene_for(candidate, enabled), campaign=f"case-{enabled}-{paid}"
            )
            args = prepare(owner, game, aim_ap)
            aimed = deepcopy(game.actors[game.shooter])
            if paid:
                step(owner, game, "enter_deep_aim", args)
                assert game.actors[game.shooter]["aim"] == aimed["aim"]
            attack = fire(owner, game, args, face)
            shooter = game.actors[game.shooter]
            assert attack["actual_difficulty"] == 19 - 2 * aim_ap - (2 if paid else 0)
            assert attack["deep_aim_offset"] == (-2 if paid else 0)
            assert shooter["ap"] == 6 - aim_ap - int(paid) - 2
            assert shooter["weapon"]["magazine"]["rounds"] == 9
            assert shooter["weapon"]["current_recoil"] == 3
            assert shooter["weapon"]["chamber"] == "LIVE"
            assert shooter["weapon"]["spare_magazines"] == aimed["weapon"]["spare_magazines"]
            assert shooter["hp"] == 10 and shooter["res"] == 8
            if enabled:
                assert not shooter["sniper_preparation"]["deep"]
                assert not shooter["sniper_preparation"]["valid"]
            else:
                assert "sniper_preparation" not in shooter
            rows.append(
                {
                    "enabled": enabled,
                    "paid": paid,
                    "attack": attack,
                    "shooter": shooter,
                    "target": game.actors[game.mover],
                }
            )
        assert rows[0]["attack"] == rows[1]["attack"]
        assert rows[0]["target"] == rows[1]["target"]
        if face == 1:
            assert all(row["target"]["hp"] == 10 and row["target"]["res"] == 8 for row in rows)
        if aim_ap == 1 and face == 10:
            assert rows[0]["attack"]["outcome"] == "WIDE_MISS"
            assert rows[2]["attack"]["outcome"] == "HIT"
            assert (rows[2]["target"]["hp"], rows[2]["target"]["res"]) == (7, 5)
        (tmp_path / "proof.json").write_text(
            json.dumps(
                {
                    "aim_ap": aim_ap,
                    "face": face,
                    "package_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
                    "rows": rows,
                },
                indent=2,
            )
            + "\n"
        )


def map_request(owner, game, action="deep-aim", operation="stage"):
    panel = next(
        row
        for row in owner.campaign_panels(game.campaign)["surfaces"]
        if row["id"] == "operations.map"
    )
    return {
        "campaign_id": game.campaign,
        "session_id": f"{SESSION}-{game.campaign}",
        "surface_id": panel["id"],
        "panel_action_id": action,
        "operation_id": operation,
        "expected_revision": owner.snapshot(game.campaign).revision,
        "selection": {
            "actor": game.shooter,
            "target": game.mover,
            "aim_point": game.actors[game.mover]["center"],
            "map_revision": panel["data"]["map_revision"],
        },
    }


@pytest.mark.parametrize("aim_ap", [1, 2])
def test_player_saved_queue_paid_stage_recovery_and_fire_preview(candidate, tmp_path, aim_ap):
    database, exported = tmp_path / "authority.sqlite3", tmp_path / "deep.sylvia-gm-campaign"
    with admitted_fixture_owner(database, "aim-owner", candidate.read_bytes()) as (owner, admitted):
        game = start(owner, admitted, scene_for(candidate))
        args = prepare(owner, game, aim_ap)
        before = owner.snapshot(game.campaign)
        request = map_request(owner, game)
        preview = owner.preview_panel_action(**request)
        assert (
            next(row["value"] for row in preview["preview"]["summary"] if row["label"] == "AP cost")
            == 1
        )
        source = {"kind": "panel", "surface_id": "operations.map", "action_id": "deep-aim"}
        sequence = owner.compose_action_sequence(
            campaign_id=game.campaign,
            sequence_id="deep-queue",
            session_id=request["session_id"],
            expected_revision=before.revision,
            steps=[{"step_id": "deep-stage", "source": source, "selection": request["selection"]}],
        )
        assert owner.snapshot(game.campaign) == before
    with AuthorityOwner.open(database, "aim-owner") as owner:
        assert owner.snapshot(game.campaign) == before
        result = owner.execute_action_sequence(
            campaign_id=game.campaign,
            sequence_id="deep-queue",
            session_id=request["session_id"],
            expected_version=sequence["version"],
            confirmation=sequence["confirmation"],
        )
        assert result["steps"][0]["status"] == "committed", result
        paid = owner.snapshot(game.campaign)
        assert paid.state[NS + ".entities"][game.shooter]["ap"] == 5 - aim_ap
        assert paid.state[NS + ".entities"][game.shooter]["sniper_preparation"]["deep"]
        owner.export_campaign("paid-deep", game.campaign, exported)
    with AuthorityOwner.open(tmp_path / "restored.sqlite3", "aim-owner") as owner:
        recovery = owner.recover_campaign(recovery_id="paid-deep", source=exported)
        assert owner.recover_campaign(recovery_id="paid-deep", source=exported) == recovery
        assert owner.snapshot(game.campaign) == paid
        assert (
            owner.execute_action_sequence(
                campaign_id=game.campaign,
                sequence_id="deep-queue",
                session_id=request["session_id"],
                expected_version=sequence["version"],
                confirmation=sequence["confirmation"],
            )
            == result
        )
        assert owner.snapshot(game.campaign) == paid
        game.owner = owner
        with pytest.raises(StaleRevision):
            owner.preview_panel_action(**{**request, "operation_id": "stale"})
        with pytest.raises(CommandRejected):
            owner.preview_panel_action(**map_request(owner, game, operation="repeat-stage"))
        shot_request = map_request(owner, game, "fire", "prepared-shot")
        shot_preview = owner.preview_panel_action(**shot_request)
        assert (
            next(
                row["value"]
                for row in shot_preview["preview"]["summary"]
                if row["label"] == "Deep Aim difficulty"
            )
            == -2
        )
        assert (
            next(
                row["value"]
                for row in shot_preview["preview"]["summary"]
                if row["label"] == "AP cost"
            )
            == 2
        )
        assert owner.snapshot(game.campaign) == paid
        attack = fire(owner, game, args)
        assert attack["actual_difficulty"] == 17 - 2 * aim_ap
        final = owner.snapshot(game.campaign)
        owner.export_campaign("spent-deep", game.campaign, tmp_path / "spent.sylvia-gm-campaign")
    with AuthorityOwner.open(tmp_path / "restored.sqlite3", "aim-owner") as owner:
        assert owner.snapshot(game.campaign) == final
        for call, receipt in game.entry_calls:
            assert call(owner) == receipt
        assert owner.snapshot(game.campaign) == final


@pytest.mark.parametrize(
    "case",
    [
        "node",
        "class",
        "no-aim",
        "legacy-aim",
        "ap",
        "inactive",
        "downed",
        "controlled",
        "stowed",
        "target",
        "point",
        "current-point",
        "blocked",
    ],
)
def test_invalid_stage_is_atomic_and_does_not_roll_or_spend(candidate, tmp_path, case):
    scene = scene_for(candidate)
    if case == "node":
        scene.actors[scene.shooter]["specialties"] = []
    if case == "class":
        scene.actors[scene.shooter]["class_id"] = "RIFLEMAN"
    if case == "blocked":
        scene.world["spatial_scene"]["geometry"].append(
            {
                "id": "test-full-cover",
                "map_id": scene.world["spatial_scene"]["map"]["id"],
                "volume": {"kind": "box", "min": [300.95, 0, 0], "max": [301.05, 59, 3]},
                "occupies_cells": False,
                "active": True,
                "movement": "blocking",
                "visibility": "opaque",
                "metadata": {},
                "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
            }
        )
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = start(owner, admitted, scene)
        args = {
            "attacker": game.shooter,
            "target": game.mover,
            "aim_point": game.actors[game.mover]["center"],
        }
        if case not in {"no-aim", "legacy-aim"}:
            args = prepare(owner, game)
        # Author prerequisite controls in a fresh campaign before testing the action.
        if case in {
            "ap",
            "inactive",
            "downed",
            "controlled",
            "stowed",
            "target",
            "legacy-aim",
            "current-point",
        }:
            changed = scene_for(candidate)
            changed.state = thaw_json_value(owner.snapshot(game.campaign).state)
            actor = changed.actors[changed.shooter]
            if case == "ap":
                actor["ap"] = 0
            if case == "inactive":
                actor["active"] = False
            if case == "downed":
                actor["downed"] = True
            if case == "controlled":
                actor["weapon"]["controlled_by"] = changed.mover
            if case == "stowed":
                actor["weapon"]["location"] = "STOWED"
            if case == "target":
                changed.actors[changed.mover]["active"] = False
            if case == "legacy-aim":
                actor["aim"].update(ap=1, target=changed.mover, point=args["aim_point"])
            if case == "current-point":
                actor["aim"]["point"] = [711, 10.5, 0.9]
            game = start(owner, admitted, changed, campaign="changed-prerequisite")
        if case == "point":
            args["aim_point"] = [711, 10.5, 0.9]
        before = owner.snapshot(game.campaign)
        with pytest.raises(CommandRejected):
            step(owner, game, "enter_deep_aim", args)
        assert owner.snapshot(game.campaign) == before


def test_stage_affordability_is_independent_and_cannot_stack(candidate, tmp_path):
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        scene = scene_for(candidate)
        scene.actors[scene.shooter]["ap"] = 2  # Diagnostic budget: AIM1 then DEEP1, no firing AP.
        game = start(owner, admitted, scene)
        args = prepare(owner, game)
        step(owner, game, "enter_deep_aim", args)
        assert game.actors[game.shooter]["ap"] == 0
        paid = owner.snapshot(game.campaign)
        with pytest.raises(CommandRejected):
            step(owner, game, "enter_deep_aim", args)
        assert owner.snapshot(game.campaign) == paid
        step(owner, game, "fire_single", {**args, "critical_effect": "DEVASTATING_HIT"})
        assert game.actors[game.shooter] == thaw_json_value(
            paid.state[NS + ".entities"][game.shooter]
        )


@pytest.mark.parametrize("moved", ["shooter", "target"])
def test_real_ordinary_movement_away_and_back_cannot_revive_paid_solution(
    candidate, tmp_path, moved
):
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = start(owner, admitted, scene_for(candidate))
        args = prepare(owner, game)
        step(owner, game, "enter_deep_aim", args)
        actor_id = game.shooter if moved == "shooter" else game.mover
        if moved == "target":
            step(owner, game, "end_allies_turn", {})
        origin = deepcopy(game.actors[actor_id]["position_cell"])
        step(
            owner,
            game,
            "move_actor",
            {"actor": actor_id, "destination": [origin[0], origin[1] + 1]},
        )
        step(owner, game, "move_actor", {"actor": actor_id, "destination": origin})
        if moved == "target":
            step(owner, game, "end_hostiles_turn", {})
        attack = fire(owner, game, args)
        assert attack["deep_aim_offset"] == 0 and attack["actual_difficulty"] == 17


def test_first_prepared_attack_consumes_deep_even_when_it_misses(candidate, tmp_path):
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = start(owner, admitted, scene_for(candidate))
        args = prepare(owner, game)
        step(owner, game, "enter_deep_aim", args)
        first = fire(owner, game, args, 1)
        second = fire(owner, game, args, 1)
        assert first["deep_aim_offset"] == -2 and second["deep_aim_offset"] == 0
        assert game.actors[game.shooter]["ap"] == 0
        assert game.actors[game.shooter]["weapon"]["magazine"]["rounds"] == 8
