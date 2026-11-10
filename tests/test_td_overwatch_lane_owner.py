"""Real owner preview, control, persistence and receipts on exact generated TD bytes."""

import json
import os
import zipfile
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.domain.authority import StateValue, WriterRole, thaw_json_value
from sylvia_gm.domain.errors import AuthorityError
from tests.admitted_library_fixture import admitted_fixture_owner
from tests.test_td_overwatch_lanes import NS


@pytest.fixture(scope="module")
def archive(tmp_path_factory):
    if selected := os.environ.get("SYLVIA_TD_CANDIDATE"):
        target = Path(selected)
        assert target.is_file()
        return target
    from tests.td_source_fixture import build_archive

    return build_archive(tmp_path_factory.mktemp("lane-owner-package"))


def start(owner, admitted, archive, configure=None):
    with zipfile.ZipFile(archive) as package:
        state = json.loads(package.read(f"packages/{NS}/conformance/scenario.json"))[
            "initial_state"
        ]
    if configure is not None:
        configure(state)
    owner.set_admin_full_audit(True)
    owner.create_campaign(
        CampaignCreate(
            campaign_id="lanes",
            display_name="Scoped Overwatch candidate",
            writer=WriterRole.DM,
            package_lock=admitted.package_lock,
            turn_id="turn",
            initial_state=tuple(
                StateValue(NS, k.removeprefix(NS + "."), v) for k, v in state.items()
            ),
        )
    )
    owner.create_agent_session(
        campaign_id="lanes",
        session_id="lane-session",
        role=WriterRole.DM,
        provider_id="synthetic",
        model_id="synthetic",
    )


def request(owner, identity="lane-setup", actor_id="vesper", **changes):
    panel = next(
        p for p in owner.campaign_panels("lanes")["surfaces"] if p["id"] == "operations.map"
    )
    action = next(a for a in panel["actions"] if a["id"] == "overwatch")
    assert action["intent"] == "prepare_overwatch_lane"
    actor = next(e for e in panel["data"]["entities"] if e["id"] == actor_id)
    selection = {
        "actor": actor_id,
        "map_revision": panel["data"]["map_revision"],
        "lane": [
            {
                "pattern": "triangle",
                "direction": "east",
                "extent": 6,
                "half_width": 2,
                "origin": list(actor["position"]),
                "facing": list(actor["facing"]),
                **changes,
            }
        ],
    }
    return {
        "campaign_id": "lanes",
        "session_id": "lane-session",
        "surface_id": "operations.map",
        "panel_action_id": "overwatch",
        "operation_id": identity,
        "expected_revision": owner.snapshot("lanes").revision,
        "selection": selection,
    }


def test_desktop_dispatch_accepts_owned_lane_arrays_and_retries_once(archive, tmp_path):
    from sylvia_gm.interfaces.owner_process import _dispatch

    with admitted_fixture_owner(
        tmp_path / "desktop.sqlite3", "lane-owner", archive.read_bytes(),
    ) as (owner, admitted):
        start(owner, admitted, archive)
        command = request(owner, "desktop-lane")

        def dispatch(method):
            return _dispatch(
                None, method, command, application_owner=owner, owner_id="lane-owner",
                provider_resolver=None, model_choices=lambda: (), session_loop=None,
                storage_root=tmp_path,
            )

        before = owner.snapshot("lanes")
        dispatch("preview_panel_action")
        assert owner.snapshot("lanes") == before
        receipt = dispatch("execute_panel_action")
        after = owner.snapshot("lanes")
        assert after.state[NS + ".entities"]["vesper"]["ap"] == 4
        assert dispatch("execute_panel_action") == receipt
        assert owner.snapshot("lanes") == after


def test_real_owner_preview_costs_nothing_and_commit_reopens_exactly(archive, tmp_path):
    db = tmp_path / "owner.sqlite3"
    with admitted_fixture_owner(db, "lane-owner", archive.read_bytes()) as (owner, admitted):
        start(owner, admitted, archive)
        before = owner.snapshot("lanes")
        command = request(owner)
        with patch.object(dice._rng, "randint") as rng:
            preview = owner.preview_panel_action(**command)
            assert owner.snapshot("lanes") == before
            receipt = owner.execute_panel_action(**command)
            after = owner.snapshot("lanes")
            assert owner.execute_panel_action(**command) == receipt
        assert rng.call_count == 0
        world = after.state[NS + ".state"]
        watch = world["prepared"]["overwatchers"]["vesper"]
        zone = next(z for z in world["spatial_scene"]["zones"] if z["id"] == watch["zone_id"])
        assert thaw_json_value(zone["shape"]) == preview["map_preview"]["grid_lane"]["shape"]
        assert (
            after.state[NS + ".entities"]["vesper"]["ap"]
            == before.state[NS + ".entities"]["vesper"]["ap"] - 2
        )
        assert (
            after.state[NS + ".entities"]["vesper"]["weapon"]
            == before.state[NS + ".entities"]["vesper"]["weapon"]
        )
        changed = deepcopy(command)
        changed["selection"]["lane"][0]["half_width"] = 1
        with pytest.raises(AuthorityError):
            owner.execute_panel_action(**changed)
        assert owner.snapshot("lanes") == after
        assert "hidden_hostile" not in json.dumps(thaw_json_value(preview))
        panels = owner.campaign_panels("lanes")
        data = next(p for p in panels["surfaces"] if p["id"] == "operations.map")["data"]
        displayed = next(z for z in data["zones"] if z["id"] == watch["zone_id"])
        assert displayed["display"]["state"] == "armed"
        (tmp_path / "preview.json").write_text(json.dumps(thaw_json_value(preview), indent=2))
    with AuthorityOwner.open(db, "lane-owner") as owner:
        assert owner.snapshot("lanes") == after
        assert owner.execute_panel_action(**command) == receipt


@pytest.mark.parametrize(
    "change",
    [
        {"pattern": "circle"},
        {"direction": "northeast"},
        {"origin": [3.5, 2.5, 0]},
        {"half_width": -1},
    ],
)
def test_owner_refuses_invalid_or_stale_lane_atomically(archive, tmp_path, change):
    with admitted_fixture_owner(tmp_path / "owner.sqlite3", "lane-owner", archive.read_bytes()) as (
        owner,
        admitted,
    ):
        start(owner, admitted, archive)
        command = request(owner, **change)
        before = owner.snapshot("lanes")
        with patch.object(dice._rng, "randint") as rng:
            with pytest.raises((AuthorityError, ValueError)):
                owner.preview_panel_action(**command)
            with pytest.raises((AuthorityError, ValueError)):
                owner.execute_panel_action(**command)
        assert rng.call_count == 0 and owner.snapshot("lanes") == before


def test_queue_composition_and_cancellation_never_spend_or_roll(archive, tmp_path):
    with admitted_fixture_owner(tmp_path / "owner.sqlite3", "lane-owner", archive.read_bytes()) as (
        owner,
        admitted,
    ):
        start(owner, admitted, archive)
        before = owner.snapshot("lanes")
        command = request(owner)
        with patch.object(dice._rng, "randint") as rng:
            queue = owner.compose_action_sequence(
                campaign_id="lanes",
                session_id="lane-session",
                sequence_id="queued-lane",
                expected_revision=before.revision,
                steps=[
                    {
                        "step_id": "watch",
                        "source": {
                            "kind": "panel",
                            "surface_id": "operations.map",
                            "action_id": "overwatch",
                        },
                        "selection": command["selection"],
                    }
                ],
            )
            assert owner.snapshot("lanes") == before
            empty = owner.compose_action_sequence(
                campaign_id="lanes",
                session_id="lane-session",
                sequence_id="queued-lane",
                expected_revision=before.revision,
                expected_version=queue["version"],
                steps=[],
            )
        assert not empty["steps"] and rng.call_count == 0 and owner.snapshot("lanes") == before


@pytest.mark.parametrize("lethal", [False, True])
def test_overlapping_watchers_choose_recheck_and_reopen_once(archive, tmp_path, lethal):
    from sylvia_gm.application.authority import ActionCommand, ResolveMechanicsChoiceCommand

    def configure(state):
        state[NS + ".state"]["spatial_scene"]["geometry"] = []
        if lethal:
            state[NS + ".entities"]["hostile_alpha"].update(hp=1, res=0)

    db = tmp_path / "multi.sqlite3"
    with admitted_fixture_owner(db, "lane-owner", archive.read_bytes()) as (owner, admitted):
        start(owner, admitted, archive, configure)
        for actor in ("vesper", "hex"):
            owner.execute_panel_action(
                **request(owner, actor, actor_id=actor, pattern="lane", half_width=1, extent=6)
            )

        def action(tool, parameters, identity):
            return ActionCommand(
                campaign_id="lanes",
                action_id=identity,
                idempotency_key=identity,
                owner_id="lane-owner",
                writer=WriterRole.DM,
                expected_revision=owner.snapshot("lanes").revision,
                turn_id="turn",
                package_id=NS,
                tool_name=tool,
                parameters=parameters,
                initiating_session_id="lane-session",
            )

        owner.execute_action(action("end_allies_turn", {}, "side"))
        before = owner.snapshot("lanes")
        move = action("move_actor", {"actor": "hostile_alpha", "destination": [5, 2]}, "crossing")
        with patch.object(dice._rng, "randint", return_value=10 if lethal else 1) as rng:
            owner.execute_action(move)
        assert rng.call_count == 0 and owner.snapshot("lanes") == before
    with AuthorityOwner.open(db, "lane-owner") as owner:
        public = next(
            row.safe_payload
            for row in owner.player_projection("lanes")
            if row.kind.value == "mechanics_choice"
        )
        assert public["choice_id"] == "overwatch-window-order"
        command = ResolveMechanicsChoiceCommand(
            campaign_id="lanes",
            action_id=public["action_id"],
            resolution_id="choose-hex",
            owner_id="lane-owner",
            expected_revision=before.revision,
            choice_id=public["choice_id"],
            choice_sequence=public.get("choice_sequence", 0),
            option_id=next(
                o["option_id"] for o in public["options"] if o["label"].startswith("hex (")
            ),
            session_id="lane-session",
        )
        with patch.object(dice._rng, "randint", return_value=10 if lethal else 1) as rng:
            receipt = owner.resolve_mechanics_choice(command)
        assert str(receipt.status) == "committed"
        assert len(receipt.result["shot_effects"]["shots"]) == (1 if lethal else 2)
        assert not receipt.result["shot_effects"]["unavailable"]
        assert rng.call_count == (1 if lethal else 2)
        after = owner.snapshot("lanes")
        world = after.state[NS + ".state"]
        assert after.state[NS + ".entities"]["hostile_alpha"]["ap"] == before.state[
            NS + ".entities"
        ]["hostile_alpha"]["ap"] - (1 if lethal else 4)
        assert [r["owner"] for r in world["last_trigger"]["reactions"]] == (
            ["hex"] if lethal else ["hex", "vesper"]
        )
        for actor in ("hex", "vesper"):
            spent = 1 if actor == "hex" or not lethal else 0
            assert world["prepared"]["overwatchers"][actor]["shots"] == 1 - spent
            assert (
                after.state[NS + ".entities"][actor]["weapon"]["magazine"]["rounds"]
                == before.state[NS + ".entities"][actor]["weapon"]["magazine"]["rounds"] - spent
            )
            assert (
                after.state[NS + ".entities"][actor]["ap"]
                == before.state[NS + ".entities"][actor]["ap"]
            )
        with patch.object(dice._rng, "randint") as rng:
            assert owner.resolve_mechanics_choice(command) == receipt
            assert owner.execute_action(move) == receipt
        assert rng.call_count == 0 and owner.snapshot("lanes") == after
    with AuthorityOwner.open(db, "lane-owner") as owner:
        assert owner.snapshot("lanes") == after
