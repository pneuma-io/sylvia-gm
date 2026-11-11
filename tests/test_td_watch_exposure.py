"""Ordinary watch setup resolves real incoming fire before its own commitment."""

import hashlib
import json
from unittest.mock import patch

import pytest

from sylvia_gm import dice
from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import (
    ActionCommand,
    AuthorityOwner,
    ResolveMechanicsChoiceCommand,
)
from sylvia_gm.domain.authority import WriterRole, thaw_json_value
from sylvia_gm.domain.errors import CommandRejected, StaleRevision
from tests.admitted_library_fixture import admitted_fixture_owner
from tests.test_td_exposure_owner import create
from tests.test_td_exposure_ownership import NS, game_for, setup
from tests.test_td_ordinary_movement import SESSION, prepare_owner
from tests.td_source_fixture import candidate as candidate_fixture

candidate = candidate_fixture
ZONE = "declared-prepared-lane"


def watch_request(owner, game, identity="watch"):
    panel = next(
        row
        for row in owner.campaign_panels(game.campaign)["surfaces"]
        if row["id"] == "operations.map"
    )
    assert (
        next(row for row in panel["actions"] if row["id"] == "overwatch")["intent"]
        == "prepare_overwatch_lane"
    )
    return {
        "campaign_id": game.campaign,
        "session_id": SESSION,
        "surface_id": panel["id"],
        "panel_action_id": "overwatch",
        "operation_id": identity,
        "expected_revision": owner.snapshot(game.campaign).revision,
        "selection": {
            "actor": game.mover,
            "lane": [{"pattern": "lane", "direction": "east", "extent": 4, "half_width": 0,
                      "origin": list(next(e for e in panel["data"]["entities"] if e["id"] == game.mover)["position"]),
                      "facing": list(next(e for e in panel["data"]["entities"] if e["id"] == game.mover)["facing"])}],
            "map_revision": panel["data"]["map_revision"],
        },
    }


def watch_call(owner, game, consumer, identity="watch", *, zone=ZONE):
    before = owner.snapshot(game.campaign)
    if consumer == "chat":
        tool = next(
            row
            for row in StatelessMcpMechanics(owner).list_tools(game.campaign)
            if row.package_id == NS and row.name == "prepare_overwatch"
        )
        assert set(tool.input_schema["properties"]) == {"actor", "zone_id", "counter_prep"}
        assert tool.input_schema["properties"]["counter_prep"]["default"] is False
        assert "counter_prep" not in tool.input_schema["required"]
        command = ActionCommand(
            campaign_id=game.campaign,
            action_id=identity,
            idempotency_key=identity,
            owner_id="aim-owner",
            writer=WriterRole.DM,
            expected_revision=before.revision,
            turn_id="turn",
            package_id=NS,
            tool_name=tool.name,
            parameters={"actor": game.mover, "zone_id": zone},
            initiating_session_id=SESSION,
        )
        return lambda current=None: StatelessMcpMechanics(current or owner).execute_action(command)
    request = watch_request(owner, game, identity)
    if consumer == "map":
        owner.preview_panel_action(**request)
        assert owner.snapshot(game.campaign) == before
        return lambda current=None: (current or owner).execute_panel_action(**request)
    queue = owner.compose_action_sequence(
        campaign_id=game.campaign,
        session_id=SESSION,
        sequence_id=identity,
        expected_revision=before.revision,
        steps=[
            {
                "step_id": "watch",
                "source": {
                    "kind": "panel",
                    "surface_id": "operations.map",
                    "action_id": "overwatch",
                },
                "selection": request["selection"],
            }
        ],
    )
    assert owner.snapshot(game.campaign) == before
    return lambda current=None: (current or owner).execute_action_sequence(
        campaign_id=game.campaign,
        session_id=SESSION,
        sequence_id=identity,
        expected_version=queue["version"],
        confirmation=queue.get("confirmation"),
    )


@pytest.mark.parametrize(
    "consumer,mirrored", [("map", True), ("queue", True), ("chat", True), ("chat", False)]
)
def test_exposed_watch_setup_is_downed_before_own_ap_or_watch_commit(
    candidate, tmp_path, consumer, mirrored
):
    scene = game_for(candidate, mirrored, "visible", wall_end=5.71)
    scene.actors[scene.mover].update(hp=1, res=0)
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        with patch.object(dice._rng, "randint", return_value=1):
            game = prepare_owner(owner, admitted, scene)
        before = owner.snapshot(game.campaign)
        call = watch_call(owner, game, consumer)
        with patch.object(dice._rng, "randint", return_value=19) as draws:
            receipt = call()
        after = owner.snapshot(game.campaign)
        actor = after.state[NS + ".entities"][game.mover]
        state = after.state[NS + ".state"]
        (tmp_path / "witness.json").write_bytes(
            (
                json.dumps(
                    {
                        "package_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
                        "consumer": consumer,
                        "mirrored": mirrored,
                        "draws": draws.call_count,
                        "before_ap": before.state[NS + ".entities"][game.mover]["ap"],
                        "after_actor": thaw_json_value(actor),
                        "prepared": thaw_json_value(state["prepared"]),
                        "incoming": thaw_json_value(state.get("last_suppression_exposure", {})),
                    },
                    indent=2,
                )
                + "\n"
            ).encode()
        )
        assert draws.call_count == 1
        assert actor["downed"] and state["last_suppression_exposure"]["target_reached"]
        assert actor["ap"] == before.state[NS + ".entities"][game.mover]["ap"]
        assert (
            state["prepared"]["overwatch"] == before.state[NS + ".state"]["prepared"]["overwatch"]
        )
        assert state["prepared"].get("overwatchers", {}) == before.state[NS + ".state"]["prepared"].get("overwatchers", {})
        assert state["prepared"]["suppression"]["capacity"] == 0
        assert (
            after.state[NS + ".entities"][game.shooter]
            == before.state[NS + ".entities"][game.shooter]
        )
        with patch.object(dice._rng, "randint") as draws:
            assert call() == receipt
        assert draws.call_count == 0 and owner.snapshot(game.campaign) == after


def test_already_downed_chat_actor_cannot_prepare_watch(candidate, tmp_path):
    scene = game_for(candidate, True, "visible", wall_end=5.71)
    scene.actors[scene.mover].update(hp=0, res=0, downed=True, ap=6)
    scene.world["active_side"] = scene.actors[scene.mover]["side"]
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        owner.set_admin_full_audit(True)
        game = create(owner, admitted, scene, campaign="downed-watch")
        owner.create_agent_session(
            campaign_id=game.campaign,
            session_id=SESSION,
            role=WriterRole.DM,
            provider_id="synthetic",
            model_id="synthetic",
        )
        before = owner.snapshot(game.campaign)
        with patch.object(dice._rng, "randint") as draws:
            watch_call(owner, game, "chat")()
        after = owner.snapshot(game.campaign)
        assert draws.call_count == 0
        assert after.state[NS + ".entities"] == before.state[NS + ".entities"]
        assert after.state[NS + ".state"]["prepared"] == before.state[NS + ".state"]["prepared"]


@pytest.mark.parametrize("consumer,mirrored", [("map", True), ("queue", True), ("chat", False)])
def test_surviving_watch_setup_pays_only_normal_cost(candidate, tmp_path, consumer, mirrored):
    scene = game_for(candidate, mirrored, "visible", wall_end=5.71)
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        with patch.object(dice._rng, "randint", return_value=1):
            game = prepare_owner(owner, admitted, scene)
        before = owner.snapshot(game.campaign)
        call = watch_call(owner, game, consumer)
        with patch.object(dice._rng, "randint", return_value=1) as draws:
            call()
        after = owner.snapshot(game.campaign)
        assert draws.call_count == 1
        actor = after.state[NS + ".entities"][game.mover]
        watch = (after.state[NS + ".state"]["prepared"]["overwatch"] if consumer == "chat"
                 else after.state[NS + ".state"]["prepared"]["overwatchers"][game.mover])
        assert (
            not actor["downed"]
            and actor["ap"] == before.state[NS + ".entities"][game.mover]["ap"] - 2
        )
        assert watch["active"] and watch["owner"] == game.mover and watch["shots"] == 1
        if consumer == "chat":
            assert watch["stationary_origin"] is None and watch["zone_id"] == ZONE
        else:
            assert watch["stationary_origin"] == actor["position_cell"]
            zone = next(z for z in after.state[NS + ".state"]["spatial_scene"]["zones"] if z["id"] == watch["zone_id"])
            assert zone["shape"]["kind"] == "grid_cells" and zone["metadata"]["owner"] == game.mover
        assert (
            after.state[NS + ".state"]["last_suppression_exposure"]["event"] == "PREPARE_OVERWATCH"
        )
        assert (
            after.state[NS + ".entities"][game.shooter]
            == before.state[NS + ".entities"][game.shooter]
        )


@pytest.mark.parametrize(
    "invalid",
    [
        "protected",
        "outside",
        "source-downed",
        "source-inactive",
        "source-weapon",
        "source-controlled",
        "depleted",
        "ended",
        "own-ap",
        "own-chamber",
        "own-controlled",
        "own-inactive",
        "zone",
    ],
)
def test_rejected_or_unavailable_watch_exposure_does_not_invent_shots(candidate, tmp_path, invalid):
    scene = game_for(
        candidate,
        True,
        "protected" if invalid == "protected" else "empty" if invalid == "outside" else "visible",
        wall_end=5.71 if invalid != "protected" else None,
    )
    with patch.object(dice._rng, "randint", return_value=1):
        setup(scene)
    scene.execute("end_hostiles_turn", {})
    # These are explicit changed-prerequisite initial-state fixtures. The
    # positive journeys above establish fire through the admitted owner itself.
    if invalid == "source-downed":
        scene.actors[scene.shooter].update(downed=True, hp=0)
    elif invalid == "source-inactive":
        scene.actors[scene.shooter]["active"] = False
    elif invalid == "source-weapon":
        scene.actors[scene.shooter]["weapon"]["location"] = "SLUNG"
    elif invalid == "source-controlled":
        scene.actors[scene.shooter]["weapon"]["controlled_by"] = scene.mover
    elif invalid == "depleted":
        scene.world["prepared"]["suppression"].update(active=False, capacity=0)
    elif invalid == "ended":
        scene.world["prepared"]["suppression"]["active"] = False
    elif invalid == "own-ap":
        scene.actors[scene.mover]["ap"] = 0
    elif invalid == "own-chamber":
        scene.actors[scene.mover]["weapon"]["chamber"] = "EMPTY"
    elif invalid == "own-controlled":
        scene.actors[scene.mover]["weapon"]["controlled_by"] = scene.shooter
    elif invalid == "own-inactive":
        scene.actors[scene.mover]["active"] = False
    legal = not invalid.startswith("own-") and invalid != "zone"
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        owner.set_admin_full_audit(True)
        game = create(owner, admitted, scene)
        owner.create_agent_session(
            campaign_id=game.campaign,
            session_id=SESSION,
            role=WriterRole.DM,
            provider_id="synthetic",
            model_id="synthetic",
        )
        before = owner.snapshot(game.campaign)
        with patch.object(dice._rng, "randint") as draws:
            receipt = watch_call(
                owner, game, "chat", zone="not-a-zone" if invalid == "zone" else ZONE
            )()
        after = owner.snapshot(game.campaign)
        assert draws.call_count == 0 and str(receipt.status) == "committed"
        assert (
            after.state[NS + ".entities"][game.shooter]
            == before.state[NS + ".entities"][game.shooter]
        )
        assert (
            after.state[NS + ".state"]["prepared"]["suppression"]
            == before.state[NS + ".state"]["prepared"]["suppression"]
        )
        assert after.state[NS + ".entities"][game.mover]["ap"] == before.state[NS + ".entities"][
            game.mover
        ]["ap"] - (2 if legal else 0)
        assert after.state[NS + ".state"]["prepared"]["overwatch"]["active"] == legal


@pytest.mark.parametrize("mirrored,downed", [(False, False), (True, True)])
def test_watch_map_rejects_hostile_or_downed_control_without_any_action(
    candidate, tmp_path, mirrored, downed
):
    scene = game_for(candidate, mirrored, "empty")
    scene.world["active_side"] = scene.actors[scene.mover]["side"]
    scene.actors[scene.mover].update(ap=6, downed=downed)
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        owner.set_admin_full_audit(True)
        game = create(owner, admitted, scene)
        owner.create_agent_session(
            campaign_id=game.campaign,
            session_id=SESSION,
            role=WriterRole.DM,
            provider_id="synthetic",
            model_id="synthetic",
        )
        before = owner.snapshot(game.campaign)
        with patch.object(dice._rng, "randint") as draws, pytest.raises(CommandRejected):
            watch_call(owner, game, "map")()
        assert draws.call_count == 0 and owner.snapshot(game.campaign) == before


def test_watch_stale_clear_and_lost_response_do_not_reexecute(candidate, tmp_path):
    scene = game_for(candidate, True, "visible", wall_end=5.71)
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        with patch.object(dice._rng, "randint", return_value=1):
            game = prepare_owner(owner, admitted, scene)
        before = owner.snapshot(game.campaign)
        request = watch_request(owner, game)
        step = {
            "step_id": "watch",
            "source": {"kind": "panel", "surface_id": "operations.map", "action_id": "overwatch"},
            "selection": request["selection"],
        }
        queue = owner.compose_action_sequence(
            campaign_id=game.campaign,
            session_id=SESSION,
            sequence_id="clear",
            expected_revision=before.revision,
            steps=[step],
        )
        cleared = owner.compose_action_sequence(
            campaign_id=game.campaign,
            session_id=SESSION,
            sequence_id="clear",
            expected_revision=before.revision,
            expected_version=queue["version"],
            steps=[],
        )
        assert not cleared["steps"] and owner.snapshot(game.campaign) == before
        with (
            patch.object(dice._rng, "randint") as draws,
            pytest.raises((CommandRejected, StaleRevision)),
        ):
            owner.execute_panel_action(**{**request, "expected_revision": before.revision - 1})
        assert draws.call_count == 0 and owner.snapshot(game.campaign) == before
        # Intentionally drop the result; only read its saved receipt afterward.
        with patch.object(dice._rng, "randint", return_value=1) as draws:
            owner.execute_panel_action(**request)
        saved = owner.snapshot(game.campaign)
        assert draws.call_count == 1
        with patch.object(dice._rng, "randint") as draws:
            receipt = owner.panel_action_receipt(**request)
        assert str(receipt.status) == "committed" and draws.call_count == 0
        assert owner.snapshot(game.campaign) == saved


def test_incoming_downing_stops_next_queued_watch_and_reports_interruption(candidate, tmp_path):
    scene = game_for(candidate, True, "visible", wall_end=5.71)
    scene.actors[scene.mover].update(hp=1, res=0)
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        with patch.object(dice._rng, "randint", return_value=1):
            game = prepare_owner(owner, admitted, scene)
        before = owner.snapshot(game.campaign)
        request = watch_request(owner, game)
        step = {
            "source": {"kind": "panel", "surface_id": "operations.map", "action_id": "overwatch"},
            "selection": request["selection"],
        }
        queue = owner.compose_action_sequence(
            campaign_id=game.campaign,
            session_id=SESSION,
            sequence_id="two-watch",
            expected_revision=before.revision,
            steps=[{**step, "step_id": "first"}, {**step, "step_id": "second"}],
        )
        with patch.object(dice._rng, "randint", return_value=19) as draws:
            result = owner.execute_action_sequence(
                campaign_id=game.campaign,
                session_id=SESSION,
                sequence_id="two-watch",
                expected_version=queue["version"],
                confirmation=queue["confirmation"],
            )
        after = owner.snapshot(game.campaign)
        assert draws.call_count == 1 and after.revision == before.revision + 1
        assert result["steps"][0]["status"] == "committed"
        assert result["steps"][1]["status"] == "pending" and "downed" in result["error"].lower()
        card = result["steps"][0]["receipt"]["result"]["result_card"]
        assert card["summary"][0]["value"] == "Incoming suppression stopped watch setup"
        assert (
            next(row["value"] for row in card["details"] if row["label"] == "Setup AP spent") == 0
        )
        assert (
            after.state[NS + ".entities"][game.mover]["ap"]
            == before.state[NS + ".entities"][game.mover]["ap"]
        )
        assert not after.state[NS + ".state"]["prepared"]["overwatch"]["active"]
        assert not after.state[NS + ".state"]["prepared"].get("overwatchers", {}).get(game.mover, {}).get("active", False)


@pytest.mark.parametrize("consumer", ["map", "queue", "chat"])
def test_successive_watch_critical_choices_recover_before_each_own_spend(
    candidate, tmp_path, consumer
):
    scene = game_for(candidate, True, "visible", wall_end=5.71)
    # Diagnostic pools keep two real critical exposure choices reachable. Thirty
    # rounds are an explicit legal setup input, never a new ammunition default.
    scene.actors[scene.mover].update(hp=100, res=100)
    calls, resolutions, receipts, exports = [], [], [], []
    first_pending = tmp_path / "pending-1.sylvia-gm-campaign"
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        owner.set_admin_full_audit(True)
        game = create(owner, admitted, scene, campaign="watch-choices")
        with patch.object(dice._rng, "randint", return_value=1):
            game.execute(
                "establish_suppression", {"actor": game.shooter, "zone_id": ZONE, "rounds": 30}
            )
        game.execute("end_hostiles_turn", {})
        owner.create_agent_session(
            campaign_id=game.campaign,
            session_id=SESSION,
            role=WriterRole.DM,
            provider_id="synthetic",
            model_id="synthetic",
        )
        before = owner.snapshot(game.campaign)
        assert before.state[NS + ".state"]["prepared"]["suppression"]["capacity"] == 2
        calls.append(watch_call(owner, game, consumer, "watch-1"))
        with patch.object(dice._rng, "randint", return_value=20) as draws:
            calls[0]()
        assert draws.call_count == 1 and owner.snapshot(game.campaign) == before
        owner.export_campaign("pending-1", game.campaign, first_pending)
        exports.append(first_pending)
    pending_source = first_pending
    final = None
    for index in range(2):
        database = tmp_path / f"recovered-{index}.sqlite3"
        with AuthorityOwner.open(database, "aim-owner") as owner:
            owner.set_admin_full_audit(True)
            owner.recover_campaign(recovery_id=f"watch-{index}", source=pending_source)
            assert owner.snapshot(game.campaign) == before
            public = next(
                row.safe_payload
                for row in owner.player_projection(game.campaign)
                if row.kind.value == "mechanics_choice"
            )
            assert public["choice_id"] == "suppression-critical-effect"
            resolution = ResolveMechanicsChoiceCommand(
                campaign_id=game.campaign,
                action_id=public["action_id"],
                resolution_id=f"watch-choice-{index}",
                owner_id="aim-owner",
                expected_revision=before.revision,
                choice_id=public["choice_id"],
                choice_sequence=public.get("choice_sequence", 0),
                option_id="DEEP_HIT",
                session_id=SESSION,
            )
            with patch.object(dice._rng, "randint", return_value=20) as draws:
                receipt = owner.resolve_mechanics_choice(resolution)
            # Resolution replays the deterministic action with the same declared
            # face; only a subsequent saved receipt replay skips execution.
            assert str(receipt.status) == "committed" and draws.call_count == 1
            resolutions.append(resolution)
            receipts.append(receipt)
            final = owner.snapshot(game.campaign)
            actor = final.state[NS + ".entities"][game.mover]
            assert not actor["downed"]
            assert actor["ap"] == 6 - 2 * (index + 1)
            assert final.state[NS + ".state"]["prepared"]["suppression"]["capacity"] == 1 - index
            assert (final.state[NS + ".state"]["prepared"]["overwatch"] if consumer == "chat"
                    else final.state[NS + ".state"]["prepared"]["overwatchers"][game.mover])["owner"] == game.mover
            assert (
                final.state[NS + ".entities"][game.shooter]
                == before.state[NS + ".entities"][game.shooter]
            )
            with patch.object(dice._rng, "randint") as draws:
                assert owner.resolve_mechanics_choice(resolution) == receipt
                calls[index](owner)
            assert draws.call_count == 0 and owner.snapshot(game.campaign) == final
            if index == 0:
                before = final
                calls.append(watch_call(owner, game, consumer, "watch-2"))
                with patch.object(dice._rng, "randint", return_value=20) as draws:
                    calls[1]()
                assert draws.call_count == 1 and owner.snapshot(game.campaign) == before
                pending_source = tmp_path / "pending-2.sylvia-gm-campaign"
                owner.export_campaign("pending-2", game.campaign, pending_source)
                exports.append(pending_source)
            else:
                final_export = tmp_path / "final.sylvia-gm-campaign"
                owner.export_campaign("final-watch", game.campaign, final_export)
                exports.append(final_export)
        with AuthorityOwner.open(database, "aim-owner") as owner:
            owner.set_admin_full_audit(True)
            assert owner.snapshot(game.campaign) == final
    with AuthorityOwner.open(tmp_path / "final-fresh.sqlite3", "aim-owner") as owner:
        owner.set_admin_full_audit(True)
        owner.recover_campaign(recovery_id="final-watch", source=final_export)
        assert owner.snapshot(game.campaign) == final
        with patch.object(dice._rng, "randint") as draws:
            for resolution, receipt in zip(resolutions, receipts, strict=True):
                assert owner.resolve_mechanics_choice(resolution) == receipt
            for call in calls:
                call(owner)
        assert draws.call_count == 0 and owner.snapshot(game.campaign) == final
    (tmp_path / "proof.json").write_bytes(
        (
            json.dumps(
                {
                    "consumer": consumer,
                    "package_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
                    "state_sha256": hashlib.sha256(
                        json.dumps(
                            thaw_json_value(final.state), sort_keys=True, separators=(",", ":")
                        ).encode()
                    ).hexdigest(),
                    "final_revision": final.revision,
                    "final_actor": thaw_json_value(final.state[NS + ".entities"][game.mover]),
                    "prepared": thaw_json_value(final.state[NS + ".state"]["prepared"]),
                    "exports": [
                        {
                            "name": path.name,
                            "bytes": path.stat().st_size,
                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        }
                        for path in exports
                    ],
                    "choices_resolved_once": len(resolutions),
                    "qualification": (
                        "synthetic owner proof; two explicit setup actions, "
                        "no periodic watch-maintenance policy"
                    ),
                },
                indent=2,
            )
            + "\n"
        ).encode()
    )
