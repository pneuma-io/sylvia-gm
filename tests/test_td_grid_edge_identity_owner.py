"""Real owner failures must preserve resources when a junction lacks layer identity."""

import json
import zipfile
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
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.spatial import SpatialEngine, admit_spatial_scene
from tests.admitted_library_fixture import admitted_fixture_owner
from tests.test_spatial_engine import _ray
from tests.test_spatial_grid_edge import orient
from tests.test_spatial_grid_edge_identity import boundary_scene, mixed_join
from tests.test_td_grid_edge_owner import NS, candidate  # noqa: F401


def _inputs(package_archive):
    with zipfile.ZipFile(package_archive) as package:
        initial = json.loads(package.read(f"packages/{NS}/conformance/scenario.json"))[
            "initial_state"
        ]
        character = json.loads(package.read(f"packages/{NS}/conformance/character.json"))[
            "artifact"
        ]
        record = next(
            row for row in json.loads(package.read("manifest.json"))["packages"] if row["id"] == NS
        )
    return initial, character, record


def _start(database, package_archive, initial, character, record):
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
    with admitted_fixture_owner(database, "identity-owner", package_archive.read_bytes()) as (
        owner,
        admitted,
    ):
        owner.create_campaign(
            CampaignCreate(
                campaign_id="identity",
                display_name="Synthetic continuity review",
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


@pytest.mark.parametrize("declared", [False, True])
@pytest.mark.parametrize("horizontal", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_owner_retains_steel_or_fails_ambiguous_join_without_resource_loss(
    candidate,  # noqa: F811
    tmp_path,
    declared,
    horizontal,
    reverse,
):
    initial, character, record = _inputs(candidate)
    actors = initial[NS + ".entities"]
    scene = initial[NS + ".state"]["spatial_scene"]
    scene["geometry"] = mixed_join(horizontal, declared)["geometry"]
    scene["zones"] = []
    for edge in scene["geometry"]:
        edge["map_id"] = scene["map"]["id"]
        edge["volume"]["max"][2] = 0.9 if edge["id"] == "a-steel" else 0.6
    a, b = ([2.5, 3, 0], [1.5, 3, 0]) if reverse else ([1.5, 3, 0], [2.5, 3, 0])
    for identity, point in [("vesper", a), ("hostile_alpha", b)]:
        direction = (-1 if reverse else 1) * (1 if identity == "vesper" else -1)
        position = orient(point, horizontal)
        actors[identity].update(
            position_cell=[int(v) for v in position[:2]],
            center=orient([*point[:2], 0.9], horizontal),
            muzzle=orient([point[0] + 0.41 * direction, point[1], 0.5], horizontal),
            ap=100,
        )
        next(row for row in scene["entities"] if row["id"] == identity).update(
            position=position, height=1.8
        )
    for body in scene["entities"]:
        if body["id"] not in ("vesper", "hostile_alpha"):
            body["active"] = False
    database = tmp_path / "identity.sqlite3"
    _start(database, candidate, initial, character, record)
    with AuthorityOwner.open(database, "identity-owner") as owner:
        before = owner.snapshot("identity")
        command = ActionCommand(
            campaign_id="identity",
            action_id="shot",
            idempotency_key="shot",
            owner_id="identity-owner",
            writer=WriterRole.DM,
            expected_revision=before.revision,
            turn_id="turn",
            package_id=NS,
            tool_name="fire_single",
            parameters={
                "attacker": "vesper",
                "target": "hostile_alpha",
                "aim_point": orient([*b[:2], 0.5], horizontal),
                "critical_effect": "DEEP_HIT",
            },
        )
        with patch.object(dice._rng, "randint", return_value=14):
            if declared:
                receipt = owner.execute_action(command)
            else:
                with pytest.raises(RulesetPreconditionRejected, match="map author") as rejection:
                    owner.execute_action(command)
                assert "edge_layer_id" not in rejection.value.safe_reason
                assert "a-steel" not in rejection.value.safe_reason
        after = owner.snapshot("identity")
        if not declared:
            assert after == before  # No resource, geometry or authoritative revision change.
        else:
            assert receipt.status.value == "committed"
            state = thaw_json_value(after.state[NS + ".state"])
            attack = state["last_attack"]
            assert attack["cover_id"] == "a-steel" and attack["material_outcome"] == "STOP"
            assert attack["target_reached"] is False
            for edge in state["spatial_scene"]["geometry"]:
                assert edge["metadata"]["cover_hp"] == (29 if edge["id"] == "a-steel" else 12)
            assert state["spatial_scene"]["map"]["revision"] == scene["map"]["revision"] + 1
        if declared:
            assert owner.execute_action(command) == receipt
        else:
            with pytest.raises(RulesetPreconditionRejected, match="map author"):
                owner.execute_action(command)
        assert owner.snapshot("identity") == after
    with AuthorityOwner.open(database, "identity-owner") as owner:
        assert owner.snapshot("identity") == after
        if declared:
            assert owner.execute_action(command) == receipt
        else:
            with pytest.raises(RulesetPreconditionRejected, match="map author"):
                owner.execute_action(command)
        assert owner.snapshot("identity") == after


@pytest.mark.parametrize("horizontal", [False, True])
def test_generated_outer_boundary_persists_and_reopens_without_rewriting_bounds(
    candidate,  # noqa: F811
    tmp_path,
    horizontal,
):
    initial, character, record = _inputs(candidate)
    scene = boundary_scene(horizontal)
    initial[NS + ".state"]["spatial_scene"] = scene
    database = tmp_path / "boundary.sqlite3"
    _start(database, candidate, initial, character, record)
    with AuthorityOwner.open(database, "identity-owner") as owner:
        saved = owner.snapshot("identity")
    with AuthorityOwner.open(database, "identity-owner") as owner:
        assert owner.snapshot("identity") == saved
        restored = thaw_json_value(saved.state[NS + ".state"]["spatial_scene"])
        assert restored == scene
        maximum = 0.3 + 2 * 0.3
        admitted = admit_spatial_scene(restored).as_payload()
        assert admitted["map"]["bounds"] == scene["map"]["bounds"]
        assert admitted["geometry"][0]["volume"]["max"][int(not horizontal)] == maximum
        result = (
            SpatialEngine(restored)
            .query(
                _ray(
                    orient([0.3, maximum, 1], horizontal), orient([maximum, maximum, 1], horizontal)
                )
            )
            .as_payload()
        )
        assert [row["id"] for row in result["intersections"]] == ["shared-wall"]
