"""Real admitted TD projectiles share one edge's material and durable damage identity."""

import json
import zipfile
from copy import deepcopy
from unittest.mock import patch

import pytest

from sylvia_gm import dice
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner, CampaignCreate
from sylvia_gm.domain.authority import (
    Participant,
    ParticipantValidationStatus,
    StateValue,
    WriterRole,
    thaw_json_value,
)
from sylvia_gm.spatial import SpatialEngine
from tests.admitted_library_fixture import admitted_fixture_owner
from tests.td_source_fixture import candidate as candidate_fixture
from tests.test_spatial_engine import _ray
from tests.test_spatial_grid_edge import edge_scene, orient

candidate = candidate_fixture


NS = "pneuma-io.trigger-discipline"


@pytest.mark.parametrize("roll", [12, 14])
@pytest.mark.parametrize("horizontal", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_authored_edge_reopens_then_real_td_shots_damage_and_destroy_one_source(
    candidate, tmp_path, horizontal, reverse, roll
):
    with zipfile.ZipFile(candidate) as package:
        initial = json.loads(package.read(f"packages/{NS}/conformance/scenario.json"))[
            "initial_state"
        ]
        character = json.loads(package.read(f"packages/{NS}/conformance/character.json"))[
            "artifact"
        ]
        record = next(
            row for row in json.loads(package.read("manifest.json"))["packages"] if row["id"] == NS
        )
    actors = initial[NS + ".entities"]
    spatial = initial[NS + ".state"]["spatial_scene"]
    source = deepcopy(edge_scene(horizontal)["geometry"][0])
    source["map_id"] = spatial["map"]["id"]
    source["volume"]["max"][2] = (
        1.0  # Low cover exposes the upper silhouette; aim still crosses it.
    )
    spatial["geometry"] = [source]
    spatial["zones"] = []
    a, b = ([2.5, 2.5, 0], [1.5, 2.5, 0]) if reverse else ([1.5, 2.5, 0], [2.5, 2.5, 0])
    for identity, position in [("vesper", a), ("hostile_alpha", b)]:
        point = orient(position, horizontal)
        direction = -1 if position[0] > (b if identity == "vesper" else a)[0] else 1
        muzzle = orient([position[0] + 0.41 * direction, position[1], 0.9], horizontal)
        actors[identity].update(
            position_cell=[int(v) for v in point[:2]],
            center=[point[0], point[1], 0.9],
            muzzle=muzzle,
            ap=100,
        )
        body = next(row for row in spatial["entities"] if row["id"] == identity)
        body.update(position=point, height=1.8)
    for body in spatial["entities"]:
        if body["id"] not in ("vesper", "hostile_alpha"):
            body["active"] = False
    weapon = actors["vesper"]["weapon"]
    resistance = weapon["material_pen"] + 1
    damage = max(0, weapon["damage"] - resistance)
    assert damage > 0, "use the real weapon's cover damage, without changing balance"
    source["metadata"].update(
        material_resistance=resistance,
        cover_hp=damage * 2,
        material_profile={"fixture": "retained verbatim"},
    )
    source["metadata"]["player"]["material_label"] = f"Resistance {resistance}"
    declaration = record["participant_artifacts"][0]
    participant = Participant(
        participant_id=character["id"],
        kind=declaration["kind"],
        package_id=NS,
        package_version=record["version"],
        package_sha256=record["content_sha256"],
        schema_id=declaration["schema_id"],
        schema_sha256=declaration["schema_sha256"],
        validation_status=ParticipantValidationStatus.VALIDATED,
        data=character,
    )
    database = tmp_path / "edge.sqlite3"
    campaign, owner_id = "edge", "edge-owner"
    with admitted_fixture_owner(database, owner_id, candidate.read_bytes()) as (owner, admitted):
        owner.create_campaign(
            CampaignCreate(
                campaign_id=campaign,
                display_name="Synthetic authored edge",
                writer=WriterRole.DM,
                package_lock=admitted.package_lock,
                turn_id="turn",
                participants=(participant,),
                initial_state=tuple(
                    StateValue(NS, key.removeprefix(NS + "."), value)
                    for key, value in initial.items()
                ),
            )
        )
    with AuthorityOwner.open(database, owner_id) as owner:
        restored = thaw_json_value(owner.snapshot(campaign).state[NS + ".state"]["spatial_scene"])
        assert restored["geometry"] == [source]
        query = _ray(orient([1.5, 2.5, 1], horizontal), orient([2.5, 2.5, 1], horizontal))
        query["map_id"] = spatial["map"]["id"]
        query["include"] = ["geometry"]
        assert (
            SpatialEngine(restored).query(query).as_payload()["intersections"][0]["id"]
            == source["id"]
        )
        for index in range(2):
            before = owner.snapshot(campaign)
            command = ActionCommand(
                campaign_id=campaign,
                action_id=f"shot-{index}",
                idempotency_key=f"shot-{index}",
                owner_id=owner_id,
                writer=WriterRole.DM,
                expected_revision=before.revision,
                turn_id="turn",
                package_id=NS,
                tool_name="fire_single",
                parameters={
                    "attacker": "vesper",
                    "target": "hostile_alpha",
                    "aim_point": orient([b[0], b[1], 0.9], horizontal),
                    "critical_effect": "DEEP_HIT",
                },
            )
            with patch.object(dice._rng, "randint", return_value=roll):
                receipt = owner.execute_action(command)
            assert receipt.status.value == "committed"
            after = owner.snapshot(campaign)
            state = thaw_json_value(after.state[NS + ".state"])
            attack = state["last_attack"]
            assert attack.get("cover_id") == "shared-wall", json.dumps(attack, sort_keys=True)
            assert attack["cover_damage"] == damage
            assert attack["cover_hp_before"] == damage * (2 - index)
            assert attack["cover_hp_after"] == damage * (1 - index)
            if roll == 12 and index == 1:
                assert attack["outcome"] == "COVER_INTERCEPT"
            else:
                assert attack["material_outcome"] == "STOP"
                assert not attack["target_reached"]
            assert (
                after.state[NS + ".entities"]["hostile_alpha"]["hp"]
                == before.state[NS + ".entities"]["hostile_alpha"]["hp"]
            )
            wall = state["spatial_scene"]["geometry"][0]
            assert wall["volume"] == source["volume"]
            assert wall["metadata"] == {**source["metadata"], "cover_hp": damage * (1 - index)}
            assert wall["active"] is (index == 0)
            assert (
                state["spatial_scene"]["map"]["revision"] == spatial["map"]["revision"] + index + 1
            )
            assert owner.execute_action(command) == receipt
            assert owner.snapshot(campaign) == after
    with AuthorityOwner.open(database, owner_id) as owner:
        final = thaw_json_value(owner.snapshot(campaign).state[NS + ".state"]["spatial_scene"])
        assert not final["geometry"][0]["active"]
        assert not [
            hit
            for hit in SpatialEngine(final).query(query).as_payload()["intersections"]
            if hit["kind"] == "geometry"
        ]
        crossing = (
            SpatialEngine(final)
            .query(
                {
                    "kind": "sweep",
                    "map_id": spatial["map"]["id"],
                    "moving_entity_id": "vesper",
                    "destination": orient(b, horizontal),
                    "include_entities": False,
                }
            )
            .as_payload()
        )
        assert crossing["supported"] and crossing["clear"]
