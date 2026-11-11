"""Authoritative panel fire preserves directional cover, shot contacts and resources."""

import json
import zipfile
from copy import deepcopy
from unittest.mock import patch

import pytest

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.domain.authority import StateValue, WriterRole, thaw_json_value
from sylvia_gm.domain.errors import CommandRejected
from tests.admitted_library_fixture import admitted_fixture_owner
from tests.td_source_fixture import candidate as candidate

NS = "pneuma-io.trigger-discipline"


def directional_scene(candidate, *, height, target_x, stance, actor_x, resistance):
    """Author physical test geometry while retaining the package's mechanics and weapons."""
    with zipfile.ZipFile(candidate) as archive:
        state = json.loads(archive.read(f"packages/{NS}/conformance/scenario.json"))[
            "initial_state"
        ]
    world, entities = state[NS + ".state"], state[NS + ".entities"]
    scene = world["spatial_scene"]
    wall = deepcopy(next(g for g in scene["geometry"] if g["id"] == "partial-cover"))
    wall["volume"]["max"][2] = height
    wall["metadata"]["material_resistance"] = resistance
    scene["geometry"] = [wall]
    for identity, x, pose in (
        ("vesper", actor_x, "STANDING"),
        ("hostile_alpha", target_x, stance),
    ):
        body_height = {"STANDING": 1.8, "CROUCHED": 1.15}[pose]
        direction = 1 if x < target_x else -1
        entities[identity].update(
            position_cell=[int(x), 2],
            stance=pose,
            center=[x, 2.5, body_height / 2],
            muzzle=[x + direction * 0.41, 2.5, body_height * 7 / 9],
        )
        body = next(e for e in scene["entities"] if e["id"] == identity)
        body.update(position=[x, 2.5, 0], height=body_height, facing=[direction, 0])
        body["metadata"]["player"]["stance"] = pose.lower()
    for body in scene["entities"]:
        body["active"] = body["id"] in {"vesper", "hostile_alpha"}
    return state


def start(owner, admitted, state):
    owner.set_admin_full_audit(True)
    owner.create_campaign(
        CampaignCreate(
            campaign_id="directional-cover",
            display_name="Synthetic directional cover",
            writer=WriterRole.DM,
            package_lock=admitted.package_lock,
            turn_id="turn",
            initial_state=tuple(
                StateValue(NS, key.removeprefix(NS + "."), value) for key, value in state.items()
            ),
        )
    )
    owner.create_agent_session(
        campaign_id="directional-cover",
        session_id="dm",
        role=WriterRole.DM,
        provider_id="synthetic",
        model_id="synthetic",
    )


def command(owner, aim_height, target_x):
    panel = next(
        s
        for s in owner.campaign_panels("directional-cover")["surfaces"]
        if s["id"] == "operations.map"
    )
    return {
        "campaign_id": "directional-cover",
        "session_id": "dm",
        "surface_id": "operations.map",
        "panel_action_id": "fire",
        "operation_id": "cover-fire",
        "expected_revision": 0,
        "selection": {
            "actor": "vesper",
            "target": "hostile_alpha",
            "aim_point": [target_x, 2.5, aim_height],
            "map_revision": panel["data"]["map_revision"],
        },
    }


@pytest.mark.parametrize(
    "height,target_x,stance,actor_x,aim_height",
    [
        pytest.param(1.0, 9.5, "STANDING", 2.5, 1.2, id="reel-low-wall-upper-body"),
        pytest.param(1.2, 9.5, "STANDING", 2.5, 1.5, id="half-wall-upper-body"),
        pytest.param(1.2, 6.5, "CROUCHED", 10.5, 0.7, id="crouched-unprotected-side"),
    ],
)
def test_clear_upper_body_or_same_side_shot_has_no_fabricated_cover_contact(
    candidate,
    tmp_path,
    height,
    target_x,
    stance,
    actor_x,
    aim_height,
):
    state = directional_scene(
        candidate, height=height, target_x=target_x, stance=stance, actor_x=actor_x, resistance=8
    )
    database = tmp_path / "clear.sqlite3"
    with admitted_fixture_owner(database, "cover-owner", candidate.read_bytes()) as (
        owner,
        admitted,
    ):
        start(owner, admitted, state)
        before = owner.snapshot("directional-cover")
        request = command(owner, aim_height, target_x)
        preview = owner.preview_panel_action(**request)
        assert owner.snapshot("directional-cover") == before
        # A fixed successful roll isolates routing from the separate cover-intercept miss band.
        with patch.object(dice._rng, "randint", return_value=12):
            receipt = owner.execute_panel_action(
                **request, confirmation=preview.get("confirmation")
            )
        after = owner.snapshot("directional-cover")
        effects = thaw_json_value(receipt.result)["shot_effects"]
        assert effects["unavailable"] is False
        assert len(effects["shots"]) == 1
        assert [contact["cue"] for contact in effects["shots"][0]["contacts"]] == ["character"]
        before_actor = before.state[NS + ".entities"]["vesper"]
        after_actor = after.state[NS + ".entities"]["vesper"]
        assert before_actor["ap"] - after_actor["ap"] == 2
        assert (
            before_actor["weapon"]["magazine"]["rounds"]
            - after_actor["weapon"]["magazine"]["rounds"]
            == 1
        )
        assert (
            owner.execute_panel_action(**request, confirmation=preview.get("confirmation"))
            == receipt
        )
        assert owner.snapshot("directional-cover") == after
    with AuthorityOwner.open(database, "cover-reopen") as owner:
        assert owner.snapshot("directional-cover") == after
        assert (
            owner.execute_panel_action(**request, confirmation=preview.get("confirmation"))
            == receipt
        )
        assert owner.snapshot("directional-cover") == after


@pytest.mark.parametrize(
    "height,target_x,stance,resistance",
    [
        pytest.param(1.2, 6.5, "CROUCHED", 8, id="crouched-protected-side"),
        pytest.param(2.4, 9.5, "STANDING", 8, id="distant-resistant-wall"),
        pytest.param(2.4, 9.5, "STANDING", 1, id="distant-penetrable-wall"),
    ],
)
def test_fully_hidden_target_refuses_ordinary_direct_fire_without_spending(
    candidate,
    tmp_path,
    height,
    target_x,
    stance,
    resistance,
):
    state = directional_scene(
        candidate,
        height=height,
        target_x=target_x,
        stance=stance,
        actor_x=2.5,
        resistance=resistance,
    )
    database = tmp_path / "hidden.sqlite3"
    with admitted_fixture_owner(database, "cover-owner", candidate.read_bytes()) as (
        owner,
        admitted,
    ):
        start(owner, admitted, state)
        before = owner.snapshot("directional-cover")
        request = command(owner, 0.7, target_x)
        for operation in (owner.preview_panel_action, owner.execute_panel_action):
            with patch.object(dice._rng, "randint") as roll:
                with pytest.raises(CommandRejected, match="No valid direct path"):
                    operation(**request)
                roll.assert_not_called()
            assert owner.snapshot("directional-cover") == before
    with AuthorityOwner.open(database, "cover-reopen") as owner:
        assert owner.snapshot("directional-cover") == before
