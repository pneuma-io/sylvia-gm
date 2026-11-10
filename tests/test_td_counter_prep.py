"""Configured COUNTER-PREP uses current ordinary authority, not a lab transform."""

import json
import zipfile
from dataclasses import replace
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
from sylvia_gm.domain.content_validation import ContentShapeError
from sylvia_gm.domain.errors import CommandRejected, StaleRevision
from tests.admitted_library_fixture import admitted_fixture_owner
from tests.test_td_break_contact import validate_scene
from tests.test_td_exposure_owner import create as create_owner
from tests.test_td_exposure_ownership import NS, game_for
from tests.td_source_fixture import candidate as candidate_fixture

candidate = candidate_fixture
ENTRY = "specialty.sentinel.entry.prepared_sightline"
COUNTER = "specialty.sentinel.tier_3.counter_prep"
NODES = [
    ENTRY,
    "specialty.sentinel.tier_1.broad_sector",
    "specialty.sentinel.tier_1.stable_nest",
    "specialty.sentinel.tier_2.second_trigger",
    "specialty.sentinel.tier_2.tracking_sector",
    COUNTER,
]
ZONE = "declared-prepared-lane"
SESSION = "counter-prep-consumer"


def scene_for(candidate, mirrored=False, condition="visible", *, node=True):
    from sylvia_ruleset.character_build import build_issues

    scene = game_for(
        candidate, mirrored, condition, wall_end=None if condition == "protected" else 5.71
    )
    watcher = scene.actors[scene.shooter]
    watcher.update(class_id="SHARPSHOOTER", level=6, specialties=NODES if node else NODES[:-1])
    with zipfile.ZipFile(candidate) as package:
        catalog = json.loads(package.read(f"packages/{NS}/references/specialty-eligibility.json"))
    assert not build_issues(watcher, catalog)
    assert not scene.world["prepared"]["suppression"]["active"]
    return scene


def create(owner, admitted, scene, campaign="counter"):
    """Use the real Plan state validator before the trusted CampaignCreate seam."""
    validate_scene(scene)
    return create_owner(owner, admitted, scene, campaign=campaign)


def configured(owner, admitted, scene, campaign="counter", *, enabled=True):
    game = create(owner, admitted, scene, campaign=campaign)
    owner.set_admin_full_audit(True)
    game.session = f"{SESSION}-{campaign}"
    owner.create_agent_session(
        campaign_id=game.campaign,
        session_id=game.session,
        role=WriterRole.DM,
        provider_id="synthetic",
        model_id="synthetic",
    )
    setup_command = ActionCommand(
        campaign_id=game.campaign,
        action_id=f"{campaign}-configure",
        idempotency_key=f"{campaign}-configure",
        owner_id="aim-owner",
        writer=WriterRole.DM,
        expected_revision=owner.snapshot(game.campaign).revision,
        turn_id="turn",
        package_id=NS,
        tool_name="prepare_overwatch",
        parameters={"actor": game.shooter, "zone_id": ZONE, "counter_prep": enabled},
        initiating_session_id=game.session,
    )
    assert str(StatelessMcpMechanics(owner).execute_action(setup_command).status) == "committed"
    assert game.world["prepared"]["overwatch"]["counter_prep"] is enabled
    game.execute(
        "end_allies_turn" if game.actors[game.shooter]["side"] == "allies" else "end_hostiles_turn",
        {},
    )
    return game


def command(owner, game, tool, identity="event"):
    parameters = (
        {
            "attacker": game.mover,
            "target": game.shooter,
            "aim_point": game.actors[game.shooter]["center"],
            "ap": 1,
        }
        if tool == "aim"
        else {"actor": game.mover, "zone_id": ZONE}
    )
    return ActionCommand(
        campaign_id=game.campaign,
        action_id=identity,
        idempotency_key=identity,
        owner_id="aim-owner",
        writer=WriterRole.DM,
        expected_revision=owner.snapshot(game.campaign).revision,
        turn_id="turn",
        package_id=NS,
        tool_name=tool,
        parameters=parameters,
        initiating_session_id=game.session,
    )


@pytest.mark.parametrize("tool,mirrored", [("aim", False), ("prepare_overwatch", True)])
def test_configured_stationary_preparation_fires_once_and_restores(
    candidate, tmp_path, tool, mirrored
):
    database = tmp_path / "authority.sqlite3"
    source = scene_for(candidate, mirrored)
    with admitted_fixture_owner(database, "aim-owner", candidate.read_bytes()) as (owner, admitted):
        game = configured(owner, admitted, source)
        before = owner.snapshot(game.campaign)
    # Ordinary owner reopen precedes the actual prep event; configuration must survive.
    with AuthorityOwner.open(database, "aim-owner") as owner:
        game.owner = owner
        assert owner.snapshot(game.campaign) == before
        proposed = command(owner, game, tool)
        with patch.object(dice._rng, "randint", return_value=1) as draws:
            receipt = StatelessMcpMechanics(owner).execute_action(proposed)
        assert str(receipt.status) == "committed"
        assert draws.call_count == 1  # no movement, no suppression: a real COUNTER-PREP shot
        after = owner.snapshot(game.campaign)
        old, new = before.state[NS + ".entities"], after.state[NS + ".entities"]
        assert new[game.mover]["position_cell"] == old[game.mover]["position_cell"]
        assert new[game.shooter]["ap"] == old[game.shooter]["ap"] == 0
        assert (
            new[game.shooter]["weapon"]["magazine"]["rounds"]
            == old[game.shooter]["weapon"]["magazine"]["rounds"] - 1
        )
        assert new[game.shooter]["weapon"]["current_recoil"] == old[game.shooter]["weapon"][
            "current_recoil"
        ] + max(1, old[game.shooter]["weapon"]["recoil"])
        assert new[game.mover]["ap"] == old[game.mover]["ap"] - (1 if tool == "aim" else 2)
        assert after.state[NS + ".state"]["last_counter_prep"]["event"] == tool.upper()
        assert after.state[NS + ".state"]["last_counter_prep"]["status"] == "RESOLVED"
        with patch.object(dice._rng, "randint") as draws:
            assert StatelessMcpMechanics(owner).execute_action(proposed) == receipt
        assert draws.call_count == 0 and owner.snapshot(game.campaign) == after
        with pytest.raises(StaleRevision):
            StatelessMcpMechanics(owner).execute_action(
                replace(proposed, action_id="stale", idempotency_key="stale")
            )
        export = tmp_path / "completed.sylvia-gm-campaign"
        owner.export_campaign("export", game.campaign, export)
        (tmp_path / "witness.json").write_text(
            json.dumps(
                {
                    "tool": tool,
                    "mirrored": mirrored,
                    "before": thaw_json_value(before.state),
                    "after": thaw_json_value(after.state),
                    "receipt": str(receipt),
                },
                indent=2,
            )
            + "\n"
        )
    with AuthorityOwner.open(tmp_path / "recovered.sqlite3", "aim-owner") as owner:
        owner.recover_campaign(recovery_id="fresh", source=export)
        assert owner.snapshot(game.campaign) == after
        with patch.object(dice._rng, "randint") as draws:
            assert owner.execute_action(proposed) == receipt
        assert draws.call_count == 0 and owner.snapshot(game.campaign) == after


def test_negative_exposures_preserve_watch_resources(candidate, tmp_path):
    with admitted_fixture_owner(
        tmp_path / "controls.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        for case in ("no-node", "not-configured", "full-cover", "outside-zone", "no-ap"):
            source = scene_for(
                candidate,
                condition=(
                    "protected"
                    if case == "full-cover"
                    else "empty"
                    if case == "outside-zone"
                    else "visible"
                ),
                node=case != "no-node",
            )
            if case == "no-ap":
                source.actors[source.mover]["ap"] = 0
                source.actors[source.mover]["max_ap"] = 0
            game = configured(
                owner,
                admitted,
                source,
                campaign=case,
                enabled=case not in ("no-node", "not-configured"),
            )
            before = owner.snapshot(game.campaign)
            with patch.object(dice._rng, "randint") as draws:
                owner.execute_action(command(owner, game, "aim", identity=case))
            assert draws.call_count == 0, case
            after = owner.snapshot(game.campaign)
            assert (
                after.state[NS + ".entities"][game.shooter]
                == before.state[NS + ".entities"][game.shooter]
            )
            assert after.state[NS + ".state"]["prepared"] == before.state[NS + ".state"]["prepared"]
            if case == "no-ap":
                assert (
                    after.state[NS + ".entities"][game.mover]
                    == before.state[NS + ".entities"][game.mover]
                )


def test_configuration_guard_and_typed_old_watch_compatibility(candidate, tmp_path):
    with admitted_fixture_owner(
        tmp_path / "configuration.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        for case in ("no-node", "wrong-class", "low-level", "missing-tier"):
            source = scene_for(candidate)
            actor = source.actors[source.shooter]
            actor["specialties"] = list(NODES)
            if case == "no-node":
                actor["specialties"].remove(COUNTER)
            elif case == "wrong-class":
                actor["class_id"] = "RIFLEMAN"
            elif case == "low-level":
                actor["level"] = 5
            else:
                actor["specialties"].remove("specialty.sentinel.tier_2.tracking_sector")
            game = create(owner, admitted, source, campaign=case)
            before = owner.snapshot(game.campaign)
            with patch.object(dice._rng, "randint") as draws:
                game.execute(
                    "prepare_overwatch",
                    {"actor": game.shooter, "zone_id": ZONE, "counter_prep": True},
                )
            after = owner.snapshot(game.campaign)
            assert draws.call_count == 0
            assert after.state[NS + ".entities"] == before.state[NS + ".entities"]
            assert after.state[NS + ".state"]["prepared"] == before.state[NS + ".state"]["prepared"]
            assert after.state[NS + ".state"]["last_trigger"]["status"] == "PREPARATION_REJECTED"
        source = scene_for(candidate)
        source.world["prepared"]["overwatch"]["counter_prep"] = "not-a-boolean"
        with pytest.raises(ContentShapeError) as rejected:
            create(owner, admitted, source, campaign="malformed-config")
        assert rejected.value.diagnostics[0].path.endswith(".prepared.overwatch.counter_prep")
        old = scene_for(candidate)
        game = create(owner, admitted, old, campaign="old-default")
        before = owner.snapshot(game.campaign)
        game.execute("prepare_overwatch", {"actor": game.shooter, "zone_id": ZONE})
        assert game.world["prepared"]["overwatch"]["counter_prep"] is False
        assert (
            game.actors[game.shooter]["ap"]
            == before.state[NS + ".entities"][game.shooter]["ap"] - 2
        )


def test_critical_counter_prep_pauses_recovers_and_interrupts_once(candidate, tmp_path):
    source = scene_for(candidate)
    source.actors[source.mover].update(hp=1, res=0)
    pending_export = tmp_path / "pending.sylvia-gm-campaign"
    with admitted_fixture_owner(
        tmp_path / "pending.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = configured(owner, admitted, source)
        before = owner.snapshot(game.campaign)
        proposed = command(owner, game, "aim")
        with patch.object(dice._rng, "randint", return_value=20) as draws:
            pending = StatelessMcpMechanics(owner).execute_action(proposed)
        assert str(pending.status) == "awaiting_choice" and draws.call_count == 1
        assert owner.snapshot(game.campaign) == before
        with patch.object(dice._rng, "randint") as draws:
            assert owner.execute_action(proposed) == pending
        assert draws.call_count == 0
        owner.export_campaign("pending", game.campaign, pending_export)
    with AuthorityOwner.open(tmp_path / "recovered-pending.sqlite3", "aim-owner") as owner:
        owner.recover_campaign(recovery_id="pending", source=pending_export)
        assert owner.snapshot(game.campaign) == before
        choice = next(
            row.safe_payload
            for row in owner.player_projection(game.campaign)
            if row.kind.value == "mechanics_choice"
        )
        assert choice["choice_id"] == "overwatch-critical-effect"
        answer = ResolveMechanicsChoiceCommand(
            campaign_id=game.campaign,
            action_id=choice["action_id"],
            resolution_id="counter-critical",
            owner_id="aim-owner",
            expected_revision=before.revision,
            choice_id=choice["choice_id"],
            choice_sequence=choice.get("choice_sequence", 0),
            option_id="DEEP_HIT",
            session_id=game.session,
        )
        # Choice replay recomputes an uncommitted action with its retained seed.
        # Apply the declared test face to that recomputation too; only committed
        # receipt replay is expected to perform no draw.
        with patch.object(dice._rng, "randint", return_value=20) as draws:
            completed = owner.resolve_mechanics_choice(answer)
        assert str(completed.status) == "committed" and draws.call_count == 1
        final = owner.snapshot(game.campaign)
        actor = final.state[NS + ".entities"][game.mover]
        assert actor["downed"] and actor["ap"] == before.state[NS + ".entities"][game.mover]["ap"]
        assert actor["aim"] == before.state[NS + ".entities"][game.mover]["aim"]
        assert final.state[NS + ".state"]["last_counter_prep"]["target_reached"]
        assert final.state[NS + ".state"]["last_attack"]["outcome"] == "INTERRUPTED_BY_COUNTER_PREP"
        assert final.state[NS + ".state"]["prepared"]["overwatch"]["shots"] == 0
        assert (
            final.state[NS + ".entities"][game.shooter]["weapon"]["magazine"]["rounds"]
            == before.state[NS + ".entities"][game.shooter]["weapon"]["magazine"]["rounds"] - 1
        )
        with patch.object(dice._rng, "randint") as draws:
            assert owner.resolve_mechanics_choice(answer) == completed
            assert owner.execute_action(proposed) == completed
        assert draws.call_count == 0 and owner.snapshot(game.campaign) == final

    with AuthorityOwner.open(tmp_path / "recovered-pending.sqlite3", "aim-owner") as owner:
        assert owner.snapshot(game.campaign) == final
        with patch.object(dice._rng, "randint") as draws:
            assert owner.execute_action(proposed) == completed
        assert draws.call_count == 0 and owner.snapshot(game.campaign) == final


def test_player_preview_clear_queue_and_lost_ack_keep_one_prep_event(candidate, tmp_path):
    with admitted_fixture_owner(
        tmp_path / "queue.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = configured(owner, admitted, scene_for(candidate, mirrored=True))
        before = owner.snapshot(game.campaign)
        panel = next(
            row
            for row in owner.campaign_panels(game.campaign)["surfaces"]
            if row["id"] == "operations.map"
        )
        selection = {
            "actor": game.mover,
            "target": game.shooter,
            "aim_point": game.actors[game.shooter]["center"],
            "map_revision": panel["data"]["map_revision"],
        }
        request = {
            "campaign_id": game.campaign,
            "session_id": game.session,
            "surface_id": "operations.map",
            "panel_action_id": "aim",
            "operation_id": "preview",
            "expected_revision": before.revision,
            "selection": selection,
        }
        preview = owner.preview_panel_action(**request)
        assert (
            next(r["value"] for r in preview["preview"]["summary"] if r["label"] == "AP cost") == 1
        )
        assert owner.snapshot(game.campaign) == before
        step = {
            "step_id": "aim",
            "source": {"kind": "panel", "surface_id": "operations.map", "action_id": "aim"},
            "selection": selection,
        }
        queued = owner.compose_action_sequence(
            campaign_id=game.campaign,
            session_id=game.session,
            sequence_id="prep-queue",
            expected_revision=before.revision,
            steps=[step],
        )
        assert owner.snapshot(game.campaign) == before
        cleared = owner.compose_action_sequence(
            campaign_id=game.campaign,
            session_id=game.session,
            sequence_id="prep-queue",
            expected_revision=before.revision,
            steps=[],
            expected_version=queued["version"],
        )
        assert owner.snapshot(game.campaign) == before
        with pytest.raises(StaleRevision):
            owner.execute_action_sequence(
                campaign_id=game.campaign,
                session_id=game.session,
                sequence_id="prep-queue",
                expected_version=queued["version"],
                confirmation=queued["confirmation"],
            )
        queued = owner.compose_action_sequence(
            campaign_id=game.campaign,
            session_id=game.session,
            sequence_id="prep-queue",
            expected_revision=before.revision,
            steps=[step],
            expected_version=cleared["version"],
        )
        with patch.object(dice._rng, "randint", return_value=1) as draws:
            result = owner.execute_action_sequence(
                campaign_id=game.campaign,
                session_id=game.session,
                sequence_id="prep-queue",
                expected_version=queued["version"],
                confirmation=queued["confirmation"],
            )
        assert result["steps"][0]["status"] == "committed" and draws.call_count == 1
        final = owner.snapshot(game.campaign)
        with patch.object(dice._rng, "randint") as draws:
            assert (
                owner.execute_action_sequence(
                    campaign_id=game.campaign,
                    session_id=game.session,
                    sequence_id="prep-queue",
                    expected_version=queued["version"],
                    confirmation=queued["confirmation"],
                )
                == result
            )
        assert draws.call_count == 0 and owner.snapshot(game.campaign) == final
        with pytest.raises(CommandRejected):
            owner.preview_panel_action(
                **{
                    **request,
                    "operation_id": "hostile-control",
                    "expected_revision": final.revision,
                    "selection": {**selection, "actor": game.shooter},
                }
            )
        assert owner.snapshot(game.campaign) == final


def test_configured_retained_watch_keeps_movement_then_exhausts_on_preparation(candidate, tmp_path):
    source = scene_for(candidate, condition="empty")
    with admitted_fixture_owner(
        tmp_path / "movement.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = create(owner, admitted, source)
        game.execute(
            "prepare_sentinel_overwatch",
            {"actor": game.shooter, "zone_id": ZONE, "shot_allowance": 2, "counter_prep": True},
        )
        game.execute("end_allies_turn", {})
        before = game.actors[game.shooter]
        with patch.object(dice._rng, "randint", return_value=1) as draws:
            game.execute("move_actor", {"actor": game.mover, "destination": [10, 7]})
        assert draws.call_count == 1 and game.world["prepared"]["overwatch"]["shots"] == 1
        assert game.world["prepared"]["overwatch"]["active"]
        for expected in (1, 0):
            with patch.object(dice._rng, "randint", return_value=1) as draws:
                game.execute(
                    "aim",
                    {
                        "attacker": game.mover,
                        "target": game.shooter,
                        "aim_point": game.actors[game.shooter]["center"],
                        "ap": 1,
                    },
                )
            assert draws.call_count == expected
        assert not game.world["prepared"]["overwatch"]["active"]
        assert (
            game.actors[game.shooter]["weapon"]["magazine"]["rounds"]
            == before["weapon"]["magazine"]["rounds"] - 2
        )


def test_same_side_preparation_order_rechecks_and_preserves_committed_suppression(
    candidate, tmp_path
):
    source = scene_for(candidate)
    with admitted_fixture_owner(
        tmp_path / "ordering.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = create(owner, admitted, source)
        game.session = SESSION
        game.execute(
            "prepare_overwatch", {"actor": game.shooter, "zone_id": ZONE, "counter_prep": True}
        )
        with patch.object(dice._rng, "randint", return_value=1):
            game.execute(
                "establish_suppression", {"actor": game.shooter, "zone_id": ZONE, "rounds": 20}
            )
        assert game.world["prepared"]["suppression"]["capacity"] == 1
        game.execute("end_allies_turn", {})
        owner.create_agent_session(
            campaign_id=game.campaign,
            session_id=game.session,
            role=WriterRole.DM,
            provider_id="synthetic",
            model_id="synthetic",
        )
        before = owner.snapshot(game.campaign)
        proposed = command(owner, game, "aim")
        with patch.object(dice._rng, "randint") as draws:
            pending = StatelessMcpMechanics(owner).execute_action(proposed)
        assert str(pending.status) == "awaiting_choice" and draws.call_count == 0
        assert owner.snapshot(game.campaign) == before
        choice = next(
            r.safe_payload
            for r in owner.player_projection(game.campaign)
            if r.kind.value == "mechanics_choice"
        )
        assert choice["choice_id"] == "preparation-trigger-order"
        answer = ResolveMechanicsChoiceCommand(
            campaign_id=game.campaign,
            action_id=choice["action_id"],
            resolution_id="prep-order",
            owner_id="aim-owner",
            expected_revision=before.revision,
            choice_id=choice["choice_id"],
            choice_sequence=choice.get("choice_sequence", 0),
            option_id="OVERWATCH",
            session_id=game.session,
        )
        with patch.object(dice._rng, "randint", return_value=1) as draws:
            receipt = owner.resolve_mechanics_choice(answer)
        assert str(receipt.status) == "committed" and draws.call_count == 2
        after = owner.snapshot(game.campaign)
        actor = after.state[NS + ".entities"][game.shooter]
        assert actor["ap"] == 0
        assert (
            actor["weapon"]["magazine"]["rounds"]
            == before.state[NS + ".entities"][game.shooter]["weapon"]["magazine"]["rounds"] - 1
        )
        assert after.state[NS + ".state"]["prepared"]["suppression"]["capacity"] == 0
        assert after.state[NS + ".state"]["prepared"]["overwatch"]["shots"] == 0
        assert after.state[NS + ".state"]["last_suppression_exposure"]["event"] == "AIM"
        with patch.object(dice._rng, "randint") as draws:
            assert owner.resolve_mechanics_choice(answer) == receipt
        assert draws.call_count == 0 and owner.snapshot(game.campaign) == after

        export = tmp_path / "resolved.sylvia-gm-campaign"
        owner.export_campaign("resolved", game.campaign, export)
        (tmp_path / "order-witness.json").write_text(
            json.dumps(
                {
                    "before": thaw_json_value(before.state),
                    "after": thaw_json_value(after.state),
                    "action": proposed.action_id,
                    "choice": choice["choice_id"],
                    "receipt": str(receipt),
                },
                indent=2,
            )
            + "\n"
        )
    with AuthorityOwner.open(tmp_path / "recovered-final.sqlite3", "aim-owner") as owner:
        owner.recover_campaign(recovery_id="fresh", source=export)
        assert owner.snapshot(game.campaign) == after
        with patch.object(dice._rng, "randint") as draws:
            assert owner.execute_action(proposed) == receipt
        assert draws.call_count == 0 and owner.snapshot(game.campaign) == after


def test_watch_owner_incapacitated_by_real_fire_cannot_counter_prep(candidate, tmp_path):
    source = scene_for(candidate)
    source.actors[source.shooter].update(hp=1, res=0)
    with admitted_fixture_owner(
        tmp_path / "incapacitated.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = configured(owner, admitted, source)
        with patch.object(dice._rng, "randint", return_value=19) as draws:
            game.execute(
                "fire_single",
                {
                    "attacker": game.mover,
                    "target": game.shooter,
                    "aim_point": game.actors[game.shooter]["center"],
                    "critical_effect": "DEEP_HIT",
                },
            )
        assert draws.call_count == 1 and game.actors[game.shooter]["downed"]
        assert game.world["prepared"]["overwatch"]["shots"] == 1
        before = owner.snapshot(game.campaign)
        with patch.object(dice._rng, "randint") as draws:
            game.execute(
                "aim",
                {
                    "attacker": game.mover,
                    "target": game.shooter,
                    "aim_point": game.actors[game.shooter]["center"],
                    "ap": 1,
                },
            )
        assert draws.call_count == 0
        after = owner.snapshot(game.campaign)
        assert (
            after.state[NS + ".entities"][game.shooter]
            == before.state[NS + ".entities"][game.shooter]
        )
        assert after.state[NS + ".state"]["prepared"] == before.state[NS + ".state"]["prepared"]
