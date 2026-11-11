"""Admitted TD shots must not pay a second material charge at a collinear seam."""

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
from tests.admitted_library_fixture import admitted_fixture_owner
from tests.test_spatial_grid_edge import orient
from tests.test_spatial_grid_edge_review import joined_scene
from tests.test_td_grid_edge_owner import NS, candidate  # noqa: F401


@pytest.mark.parametrize("independent", [False, True])
@pytest.mark.parametrize("resistance", [1, 3])
@pytest.mark.parametrize("horizontal", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_owner_seam_charges_one_material_source_and_reopens(
    candidate,  # noqa: F811
    tmp_path,
    resistance,
    horizontal,
    reverse,
    independent,
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
    scene = initial[NS + ".state"]["spatial_scene"]
    scene["geometry"] = joined_scene(horizontal)["geometry"]
    if independent:
        # Two distinct coincident layers, not two adjoining halves of a surface.
        scene["geometry"][0]["volume"] = deepcopy(scene["geometry"][1]["volume"])
        for edge in scene["geometry"]:
            edge["edge_layer_id"] = edge["id"]
    scene["zones"] = []
    for edge in scene["geometry"]:
        edge["map_id"] = scene["map"]["id"]
        edge["volume"]["max"][2] = 1
        edge["metadata"].update(material_resistance=resistance, cover_hp=12)
    a, b = ([2.5, 3, 0], [1.5, 3, 0]) if reverse else ([1.5, 3, 0], [2.5, 3, 0])
    for identity, point in [("vesper", a), ("hostile_alpha", b)]:
        direction = (-1 if reverse else 1) * (1 if identity == "vesper" else -1)
        position = orient(point, horizontal)
        actors[identity].update(
            position_cell=[int(v) for v in position[:2]],
            center=orient([*point[:2], 0.9], horizontal),
            muzzle=orient([point[0] + 0.41 * direction, point[1], 0.9], horizontal),
            ap=100,
        )
        next(row for row in scene["entities"] if row["id"] == identity).update(
            position=position, height=1.8
        )
    for body in scene["entities"]:
        if body["id"] not in ("vesper", "hostile_alpha"):
            body["active"] = False
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
    database = tmp_path / "seam.sqlite3"
    owner_id, campaign = "seam-owner", "seam"
    with admitted_fixture_owner(database, owner_id, candidate.read_bytes()) as (owner, admitted):
        owner.create_campaign(
            CampaignCreate(
                campaign_id=campaign,
                display_name="Synthetic edge seam",
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
        before = owner.snapshot(campaign)
        command = ActionCommand(
            campaign_id=campaign,
            action_id="shot",
            idempotency_key="shot",
            owner_id=owner_id,
            writer=WriterRole.DM,
            expected_revision=before.revision,
            turn_id="turn",
            package_id=NS,
            tool_name="fire_single",
            parameters={
                "attacker": "vesper",
                "target": "hostile_alpha",
                "aim_point": orient([*b[:2], 0.9], horizontal),
                "critical_effect": "DEEP_HIT",
            },
        )
        with patch.object(dice._rng, "randint", return_value=14):
            receipt = owner.execute_action(command)
        assert receipt.status.value == "committed"
        after = owner.snapshot(campaign)
        state = thaw_json_value(after.state[NS + ".state"])
        attack = state["last_attack"]
        assert attack["target_reached"] is not (independent and resistance == 3), attack
        damage = actors["vesper"]["weapon"]["damage"] - resistance
        for edge in state["spatial_scene"]["geometry"]:
            if not independent:
                expected_hp = 12 - damage if edge["id"] == "layer-0-segment-3" else 12
            else:
                expected_hp = 12 - damage
                if resistance == 3 and edge["id"] == "layer-0-segment-3":
                    expected_hp = 12  # A real second layer stops the reduced projectile.
            assert edge["metadata"]["cover_hp"] == expected_hp
        assert attack["cover_id"] == "layer-0-segment-3"
        assert attack["cover_damage"] == (0 if independent and resistance == 3 else damage)
        revisions = 2 if independent and resistance == 1 else 1
        assert state["spatial_scene"]["map"]["revision"] == scene["map"]["revision"] + revisions
        assert owner.execute_action(command) == receipt
        assert owner.snapshot(campaign) == after
    with AuthorityOwner.open(database, owner_id) as owner:
        assert owner.snapshot(campaign) == after
        assert owner.execute_action(command) == receipt
        assert owner.snapshot(campaign) == after
