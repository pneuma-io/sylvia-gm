"""The owned quiet-held-metal reference, not a generic hiding/acoustics model.

All origins, noise bounds, equipment, aptitude and difficulty below are explicit
synthetic encounter facts. They supply no starting character or balance defaults.
"""

import hashlib
import json
from copy import deepcopy
from unittest.mock import patch

import pytest

from sylvia_gm import dice
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import WriterRole, thaw_json_value
from sylvia_gm.domain.errors import CommandRejected, StaleRevision, WrongWriter
from sylvia_gm.spatial import SpatialEngine
from tests.admitted_library_fixture import admitted_fixture_owner
from tests.test_td_deep_aim import map_request
from tests.test_td_rifleman_entry_boundaries import replay_all, step
from tests.test_td_rifleman_entry_boundaries import start as start_owner
from tests.td_source_fixture import candidate as candidate_fixture
from tests.test_td_suppression_aim import PackageProcedures

candidate = candidate_fixture
NS = "pneuma-io.trigger-discipline"
ENTRY = "specialty.evader.entry.break_contact"


def validate_scene(scene):
    from sylvia_gm.application.plan_state_admission import validate_plan_state

    validate_plan_state(
        {
            "initial_state": {
                "mechanics": [
                    {"namespace": NS, "key": key.removeprefix(NS + "."), "value": value}
                    for key, value in scene.state.items()
                ]
            }
        },
        scene.manifest,
    )


def start(owner, admitted, scene, campaign="entries"):
    # This is the real Plan authoring validator, before the trusted low-level
    # CampaignCreate seam. CampaignCreate itself does not enforce these schemas.
    validate_scene(scene)
    return start_owner(owner, admitted, scene, campaign)


def place(scene, identity, cell):
    actor = scene.actors[identity]
    dx, dy = cell[0] - actor["position_cell"][0], cell[1] - actor["position_cell"][1]
    actor["position_cell"] = list(cell)
    for key in ("center", "muzzle"):
        actor[key][0] += dx
        actor[key][1] += dy
    body = next(row for row in scene.world["spatial_scene"]["entities"] if row["id"] == identity)
    body["position"][0] += dx
    body["position"][1] += dy


def scene_for(candidate, enabled=True, renamed=False, mirrored=False):
    scene = PackageProcedures(candidate, mirrored)
    replacements = (
        (("vesper", "quarry"), ("hostile_alpha", "listener-a"), ("drone_target", "listener-b"))
        if renamed
        else ()
    )
    text = json.dumps(scene.state)
    for old, new in replacements:
        text = text.replace(old, new)
    scene.state = json.loads(text)
    actor, a, b = (
        ("quarry", "listener-a", "listener-b")
        if renamed
        else ("vesper", "hostile_alpha", "drone_target")
    )
    scene.shooter, scene.mover = actor, a
    subject = scene.actors[actor]
    subject.update(class_id="GHOST", level=5, specialties=[ENTRY] if enabled else [])
    subject["skills"]["stealth"] = 3
    subject["aptitudes"]["precision"] = 1
    for identity, cell in ((actor, [4, 4]), (a, [9, 4]), (b, [10, 3])):
        place(scene, identity, cell)
    # The second conformance target is an enemy observer in this explicit encounter.
    scene.actors[b]["side"] = scene.actors[a]["side"]
    equipment = "held-loose-metal"
    contacts = {}
    for identity in (a, b):
        observer = scene.actors[identity]
        contacts[identity] = {
            "source_id": "private-visual-" + identity,
            "source_kind": "DIRECT_VISUAL",
            "origin": [*observer["center"][:2], 1.6],
            "basis_center": deepcopy(observer["center"]),
            "basis_stance": observer["stance"],
            "knowledge": "CONFIRMED",
            "last_exact": deepcopy(subject["center"]),
            "last_exact_revision": 1,
            "region": None,
            "evidence": [
                {
                    "id": "private-observation-" + identity,
                    "kind": "DIRECT_VISUAL",
                    "region": None,
                    "map_revision": 1,
                }
            ],
        }
    activity = {
        "id": "quiet-metal",
        "actor_id": actor,
        "kind": "QUIET_HELD_METAL_SEARCH",
        "uncertainty": "UNCERTAIN",
        "inevitable_evidence": "NONE",
        "map_id": scene.world["spatial_scene"]["map"]["id"],
        "map_revision": 2,
        "actor_center": [4.5, 6.5, subject["center"][2]],
        "equipment_id": equipment,
        "consumed": False,
        "base_difficulty": 14,
        "aptitude": "precision",
        "failure": {
            "kind": "LOCALIZABLE_NOISE",
            "listener": a,
            "region": {"min": [3, 5, 0], "max": [5.5, 8, 2]},
        },
    }
    second = deepcopy(activity)
    second["id"] = "second-quiet-metal"
    scene.world["break_contact"] = {
        "actor_ids": [actor],
        "actors": {
            actor: {
                "observer_ids": [a, b],
                "contacts": contacts,
                "activities": {"quiet-metal": activity, "second-quiet-metal": second},
                "had_exact": True,
                "fully_lost": False,
                "epoch": 0,
                "used_epoch": -1,
                "checked_revision": 1,
                "proof_supported": False,
                "all_blocked": False,
            }
        },
        "equipment": {
            equipment: {
                "id": equipment,
                "kind": "LOOSE_METAL_EQUIPMENT",
                "held_by": actor,
                "held": True,
                "active": True,
            }
        },
    }
    return scene


def record(game):
    return game.world["break_contact"]["actors"][game.shooter]


def quiet_request(owner, game, operation="quiet-player"):
    request = map_request(owner, game, "keep-metal-quiet", operation)
    request["selection"] = {
        key: value
        for key, value in request["selection"].items()
        if key in {"actor", "map_revision"}
    }
    return request


def move_out(owner, game):
    assert step(owner, game, "move_actor", {"actor": game.shooter, "destination": [4, 6]}) == 0
    assert game.entry_calls[-1][1].status.value == "committed"
    assert game.actors[game.shooter]["ap"] == 4
    assert game.world["spatial_scene"]["map"]["revision"] == 2


def attempt(owner, game, face=20, activity="quiet-metal"):
    assert (
        step(
            owner,
            game,
            "attempt_break_contact",
            {
                "actor": game.shooter,
                "activity_id": activity,
            },
            face,
        )
        == 1
    )
    assert game.entry_calls[-1][1].status.value == "committed"


@pytest.mark.parametrize(
    "enabled,renamed,mirrored,face",
    [
        (True, False, False, 20),
        (True, True, True, 1),
        (False, True, False, 20),
        (False, False, True, 1),
    ],
)
def test_owned_quiet_activity_price_evidence_replay_and_recovery(
    candidate,
    tmp_path,
    enabled,
    renamed,
    mirrored,
    face,
):
    database = tmp_path / "authority.sqlite3"
    export = tmp_path / "quiet-metal.sylvia-gm-campaign"
    with admitted_fixture_owner(database, "aim-owner", candidate.read_bytes()) as (owner, admitted):
        game = start(owner, admitted, scene_for(candidate, enabled, renamed, mirrored))
        assert any(tool.name == "attempt_break_contact" for tool in owner.catalog(game.campaign))
        move_out(owner, game)
        before = deepcopy(record(game))
        (tmp_path / "after-move.json").write_text(
            json.dumps(
                {
                    "record": before,
                    "actors": game.actors,
                    "scene": game.world["spatial_scene"],
                    "receipt": str(game.entry_calls[-1][1]),
                },
                indent=2,
            )
            + "\n"
        )
        assert before["proof_supported"] and before["all_blocked"] and before["epoch"] == 1, before
        assert all(row["knowledge"] == "LAST_KNOWN" for row in before["contacts"].values())
        assert all(row["last_exact"] == [4.5, 4.5, 0.9] for row in before["contacts"].values())
        physical = deepcopy(game.actors)
        other_world = deepcopy(game.world)
        other_world.pop("break_contact")
        attempt(owner, game, face)
        result = record(game)
        paid, difficulty = (1, 12) if enabled else (2, 14)
        assert result["last_attempt"] == {
            "activity_id": "quiet-metal",
            "success": face == 20,
            "roll": face,
            "difficulty": difficulty,
            "ap_spent": paid,
            "map_revision": 2,
        }
        assert result["used_epoch"] == result["epoch"] == 1
        assert result["activities"]["quiet-metal"]["consumed"] is True
        expected_actor = deepcopy(physical[game.shooter])
        expected_actor["ap"] -= paid
        assert game.actors == {**physical, game.shooter: expected_actor}
        after_world = deepcopy(game.world)
        after_world.pop("break_contact")
        assert after_world == other_world
        listener = result["activities"]["quiet-metal"]["failure"]["listener"]
        for identity, contact in result["contacts"].items():
            assert contact["last_exact"] == before["contacts"][identity]["last_exact"]
            if face == 1 and identity == listener:
                assert contact["knowledge"] == "APPROXIMATE"
                assert contact["region"] == {"min": [3, 5, 0], "max": [5.5, 8, 2]}
                assert len(contact["evidence"]) == 2
            else:
                assert contact == before["contacts"][identity]
        # A second explicitly authored activity is paid normally; mere refreshed
        # contact records or another request never re-arm the same Entry event.
        if enabled:
            attempt(owner, game, 20, "second-quiet-metal")
            assert record(game)["last_attempt"]["ap_spent"] == 2
            assert record(game)["last_attempt"]["difficulty"] == 14
        final = owner.snapshot(game.campaign)
        owner.export_campaign("quiet-final", game.campaign, export)
        proof = {
            "package_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
            "enabled": enabled,
            "renamed": renamed,
            "mirrored": mirrored,
            "final_state": thaw_json_value(final.state),
            "receipts": [
                receipt.to_record() if hasattr(receipt, "to_record") else str(receipt)
                for _, receipt in game.entry_calls
            ],
        }
        (tmp_path / "proof.json").write_text(json.dumps(proof, indent=2) + "\n")
    with AuthorityOwner.open(database, "aim-owner") as owner:
        replay_all(owner, game, final)
    with AuthorityOwner.open(tmp_path / "fresh.sqlite3", "aim-owner") as owner:
        restored = owner.recover_campaign(recovery_id="quiet-restored", source=export)
        assert owner.recover_campaign(recovery_id="quiet-restored", source=export) == restored
        replay_all(owner, game, final)


def configure_observer(scene, identity, cell):
    place(scene, identity, cell)
    source = record(scene)["contacts"][identity]
    source["basis_center"] = deepcopy(scene.actors[identity]["center"])
    source["origin"] = [*scene.actors[identity]["center"][:2], 1.6]


def test_owned_refusals_are_atomic_and_cannot_supply_facts_or_costs(candidate, tmp_path):
    cases = (
        "remaining-observer",
        "sensor",
        "origin",
        "moved-observer",
        "certain",
        "inevitable",
        "equipment",
        "activity-revision",
        "region",
        "class",
        "ap",
        "downed",
        "inactive-side",
        "duplicate-activity",
        "unknown-activity",
        "forged",
    )
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        for case in cases:
            scene = scene_for(candidate)
            owned = record(scene)
            activity = owned["activities"]["quiet-metal"]
            if case == "remaining-observer":
                configure_observer(scene, owned["observer_ids"][1], [5, 6])
            if case == "sensor":
                owned["contacts"][scene.mover]["source_kind"] = "UNSUPPORTED_THROUGH_WALL_SENSOR"
            if case == "origin":
                owned["contacts"][scene.mover]["origin"][0] += 20
            if case == "moved-observer":
                owned["contacts"][scene.mover]["basis_center"][0] += 1
            if case == "certain":
                activity["uncertainty"] = "CERTAIN"
            if case == "inevitable":
                activity["inevitable_evidence"] = "EXACT_VISUAL"
            if case == "equipment":
                scene.world["break_contact"]["equipment"][activity["equipment_id"]]["held"] = False
            if case == "activity-revision":
                activity["map_revision"] = 1
            if case == "region":
                activity["failure"]["region"]["max"][0] = 4.6
            if case == "class":
                scene.actors[scene.shooter]["class_id"] = "RIFLEMAN"
            if case == "ap":
                scene.actors[scene.shooter]["ap"] = 2
            if case == "downed":
                scene.actors[scene.shooter]["downed"] = True
            if case == "duplicate-activity":
                activity["consumed"] = True
            game = start(owner, admitted, scene, campaign=case)
            # The movement is a real ordinary action; no direct adoption repairs
            # the pose, geometry, knowledge or activity after admission.
            step(owner, game, "move_actor", {"actor": game.shooter, "destination": [4, 6]})
            if case == "inactive-side":
                step(owner, game, "end_allies_turn", {})
            before = owner.snapshot(game.campaign)
            if case not in {"unknown-activity", "forged"}:
                with pytest.raises(CommandRejected):
                    owner.preview_panel_action(**quiet_request(owner, game, case))
            args = {
                "actor": game.shooter,
                "activity_id": "unknown" if case == "unknown-activity" else "quiet-metal",
            }
            if case == "forged":
                args.update(ap=0, observers=[], success=True, difficulty=0)
            with patch.object(dice._rng, "randint") as draws, pytest.raises(CommandRejected):
                step(owner, game, "attempt_break_contact", args)
            assert not draws.called
            assert owner.snapshot(game.campaign) == before, case
        (tmp_path / "refused-cases.json").write_text(json.dumps(list(cases), indent=2) + "\n")


@pytest.mark.parametrize("enabled", [True, False])
def test_player_preview_saved_queue_cancel_stale_and_private_projection(
    candidate, tmp_path, enabled
):
    database, exported = tmp_path / "authority.sqlite3", tmp_path / "player.sylvia-gm-campaign"
    with admitted_fixture_owner(database, "aim-owner", candidate.read_bytes()) as (owner, admitted):
        game = start(owner, admitted, scene_for(candidate, enabled))
        move_out(owner, game)
        before = owner.snapshot(game.campaign)
        request = quiet_request(owner, game)
        preview = owner.preview_panel_action(**request)
        summary = {row["label"]: row["value"] for row in preview["preview"]["summary"]}
        assert summary["AP cost"] == (1 if enabled else 2)
        assert summary["Difficulty"] == (12 if enabled else 14)
        steps = [
            {
                "step_id": "quiet",
                "source": {
                    "kind": "panel",
                    "surface_id": "operations.map",
                    "action_id": "keep-metal-quiet",
                },
                "selection": request["selection"],
            }
        ]
        sequence = owner.compose_action_sequence(
            campaign_id=game.campaign,
            sequence_id="quiet-queue",
            session_id=request["session_id"],
            expected_revision=before.revision,
            steps=steps,
        )
        assert owner.snapshot(game.campaign) == before
        # Clearing queued intent uses the real shared composer, then re-adding it
        # leaves campaign resources and next-use eligibility unchanged.
        cleared = owner.compose_action_sequence(
            campaign_id=game.campaign,
            sequence_id="quiet-queue",
            session_id=request["session_id"],
            expected_revision=before.revision,
            expected_version=sequence["version"],
            steps=[],
        )
        sequence = owner.compose_action_sequence(
            campaign_id=game.campaign,
            sequence_id="quiet-queue",
            session_id=request["session_id"],
            expected_revision=before.revision,
            expected_version=cleared["version"],
            steps=steps,
        )
        assert owner.snapshot(game.campaign) == before
        # No dispatch is cancellation: preview/queued intent changes no campaign
        # state, resources, observer information or first-use eligibility.
        assert not record(game)["activities"]["quiet-metal"]["consumed"]
        visible, boundary = owner.model_context_snapshot(game.campaign)
        assert NS + ".state" not in visible.state
        assert boundary is not None
        public = json.dumps(
            {
                "panels": thaw_json_value(owner.campaign_panels(game.campaign)),
                "preview": thaw_json_value(preview),
                "sequence": thaw_json_value(sequence),
                "model": thaw_json_value(visible.state),
            }
        )
        for secret in (
            "private-observation-",
            "private-visual-",
            "QUIET_HELD_METAL_SEARCH",
            "LOCALIZABLE_NOISE",
            "observer_ids",
            "held-loose-metal",
        ):
            assert secret not in public
        owner.export_campaign("queued-quiet", game.campaign, exported)
    with AuthorityOwner.open(tmp_path / "restored.sqlite3", "aim-owner") as owner:
        owner.recover_campaign(recovery_id="queued-quiet", source=exported)
        assert owner.snapshot(game.campaign) == before
        with patch.object(dice._rng, "randint", return_value=1) as draws:
            result = owner.execute_action_sequence(
                campaign_id=game.campaign,
                sequence_id="quiet-queue",
                session_id=request["session_id"],
                expected_version=sequence["version"],
                confirmation=sequence["confirmation"],
            )
        assert draws.call_count == 1
        assert result["steps"][0]["status"] == "committed", result
        final = owner.snapshot(game.campaign)
        assert final.state[NS + ".entities"][game.shooter]["ap"] == 4 - (1 if enabled else 2)
        replay = owner.execute_action_sequence(
            campaign_id=game.campaign,
            sequence_id="quiet-queue",
            session_id=request["session_id"],
            expected_version=sequence["version"],
            confirmation=sequence["confirmation"],
        )
        assert replay == result and owner.snapshot(game.campaign) == final
        with pytest.raises(StaleRevision):
            owner.preview_panel_action(**request)
        game.owner = owner
        with pytest.raises(CommandRejected):
            owner.preview_panel_action(**quiet_request(owner, game, "used"))
        # The public action/result and spatial evidence may identify this actor's
        # selected activity, never private listeners/origins/regions/evidence.
        text = json.dumps(thaw_json_value(result))
        for secret in (
            "private-observation-",
            "private-visual-",
            "LOCALIZABLE_NOISE",
            "observer_ids",
            "source_kind",
            "basis_center",
            "failure",
            "region",
        ):
            assert secret not in text
        (tmp_path / "player-result.json").write_text(
            json.dumps(
                {
                    "preview": thaw_json_value(preview),
                    "result": thaw_json_value(result),
                    "final_revision": final.revision,
                    "final_ap": final.state[NS + ".entities"][game.shooter]["ap"],
                    "private_state_omitted_from_model": True,
                },
                indent=2,
            )
            + "\n"
        )


@pytest.mark.parametrize("case", ["self-body", "narrow-slit"])
def test_sampled_zero_or_unsupported_visibility_never_proves_loss(candidate, tmp_path, case):
    scene = scene_for(candidate)
    physical = scene.world["spatial_scene"]
    # Preserve the encounter's existing network->geometry references; these
    # negative visual scenes explicitly disable their unrelated opaque walls.
    for geometry in physical["geometry"]:
        geometry.update(active=False, visibility="clear", movement="passable")
    source = record(scene)["contacts"][scene.mover]
    if case == "self-body":
        source["origin"] = deepcopy(scene.actors[scene.mover]["center"])
    else:
        wall = {
            "map_id": physical["map"]["id"],
            "occupies_cells": False,
            "active": True,
            "movement": "blocking",
            "visibility": "opaque",
            "metadata": {},
            "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
        }
        physical["geometry"] += [
            {
                **deepcopy(wall),
                "id": "below-slit",
                "volume": {"kind": "box", "min": [6, 0, 0], "max": [6.01, 5.751, 3]},
            },
            {
                **deepcopy(wall),
                "id": "above-slit",
                "volume": {"kind": "box", "min": [6, 5.761, 0], "max": [6.01, 60, 3]},
            },
        ]
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = start(owner, admitted, scene)
        move_out(owner, game)
        observed = record(game)
        assert not observed["all_blocked"] and observed["epoch"] == 0
        if case == "self-body":
            assert observed["contacts"][game.mover]["knowledge"] == "CONFIRMED"
            query = {
                "kind": "exposure",
                "map_id": physical["map"]["id"],
                "observer": {"point": source["origin"]},
                "target": {"entity_id": game.shooter},
                "samples": {"horizontal": 3, "vertical": 3},
                "include": ["geometry", "entities"],
            }
            probe = SpatialEngine(game.world["spatial_scene"]).query(query).as_payload()
            assert probe["exposure"] == 0
        else:
            assert not observed["proof_supported"]
            y = 4.5 + (5.755 - 4.5) * 5 / (9.5 - 6.005)
            destination = [4.5, y, 0.9]
            assert 6.1 < y < 6.9
            probe = (
                SpatialEngine(game.world["spatial_scene"])
                .query(
                    {
                        "kind": "ray",
                        "map_id": physical["map"]["id"],
                        "origin": source["origin"],
                        "destination": destination,
                        "include": ["geometry"],
                        "max_intersections": 64,
                    }
                )
                .as_payload()
            )
            assert not probe["intersections"]
        before = owner.snapshot(game.campaign)
        with pytest.raises(CommandRejected):
            step(
                owner,
                game,
                "attempt_break_contact",
                {"actor": game.shooter, "activity_id": "quiet-metal"},
            )
        assert owner.snapshot(game.campaign) == before
        (tmp_path / "visibility-negative.json").write_text(
            json.dumps(
                {
                    "case": case,
                    "knowledge": observed,
                    "component_probe": probe,
                    "owning_action_refused_without_spend": True,
                },
                indent=2,
            )
            + "\n"
        )


def test_typed_admission_old_state_and_writer_boundary(candidate, tmp_path):
    from sylvia_gm.domain.content_validation import ContentShapeError

    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        for case in ("origin-type", "origin-size", "knowledge", "observer", "listener", "consumed"):
            scene = scene_for(candidate)
            owned = record(scene)
            activity = owned["activities"]["quiet-metal"]
            if case == "origin-type":
                owned["contacts"][scene.mover]["origin"][0] = "secret"
            if case == "origin-size":
                owned["contacts"][scene.mover]["origin"].pop()
            if case == "knowledge":
                owned["contacts"][scene.mover]["knowledge"] = "INVISIBLE"
            if case == "observer":
                owned["observer_ids"].append("not-an-entity")
            if case == "listener":
                activity["failure"]["listener"] = "not-an-observer"
            if case == "consumed":
                activity["consumed"] = "no"
            with pytest.raises(ContentShapeError):
                validate_scene(scene)
        scene = scene_for(candidate)
        scene.world.pop("break_contact")
        game = start(owner, admitted, scene, campaign="legacy-optional-state")
        move_out(owner, game)
        assert "break_contact" not in game.world
        before = owner.snapshot(game.campaign)
        with pytest.raises(CommandRejected):
            step(
                owner,
                game,
                "attempt_break_contact",
                {"actor": game.shooter, "activity_id": "quiet-metal"},
            )
        assert owner.snapshot(game.campaign) == before
        command = ActionCommand(
            campaign_id=game.campaign,
            action_id="player-raw",
            idempotency_key="player-raw",
            owner_id="aim-owner",
            writer=WriterRole.BUILDER,
            expected_revision=before.revision,
            turn_id="turn",
            package_id=NS,
            tool_name="attempt_break_contact",
            parameters={"actor": game.shooter, "activity_id": "quiet-metal"},
        )
        with pytest.raises(WrongWriter):
            owner.execute_action(command)
        assert owner.snapshot(game.campaign) == before


def test_stronger_noise_region_and_next_use_survive_real_side_transitions(candidate, tmp_path):
    scene = scene_for(candidate)
    listener = record(scene)["contacts"][scene.mover]
    narrower = {"min": [4, 6, 0], "max": [5, 7, 1.9]}
    listener.update(knowledge="APPROXIMATE", region=deepcopy(narrower))
    original = deepcopy(listener["evidence"])
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = start(owner, admitted, scene)
        move_out(owner, game)
        attempt(owner, game, 1)
        current = record(game)["contacts"][game.mover]
        assert current["knowledge"] == "APPROXIMATE" and current["region"] == narrower
        assert current["evidence"][:-1] == original and len(current["evidence"]) == 2
        retained = deepcopy(record(game))
        step(owner, game, "end_allies_turn", {})
        step(owner, game, "end_hostiles_turn", {})
        assert record(game) == retained
        attempt(owner, game, 20, "second-quiet-metal")
        assert record(game)["last_attempt"]["ap_spent"] == 2
        assert record(game)["last_attempt"]["difficulty"] == 14
        assert record(game)["contacts"][game.mover] == current
