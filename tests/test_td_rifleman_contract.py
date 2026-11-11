"""Provisional package metadata, old-state compatibility and paused owner replay."""

import json
import zipfile
from copy import deepcopy
from unittest.mock import patch

import pytest
import yaml

from sylvia_gm import dice
from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import (
    ActionCommand,
    AuthorityOwner,
    ResolveMechanicsChoiceCommand,
)
from sylvia_gm.application.initial_character_state import (
    materialize_initial_character_state,
    verify_initial_character_state,
)
from sylvia_gm.domain.authority import AwaitingChoiceReceipt, WriterRole
from tests.admitted_library_fixture import admitted_fixture_owner
from tests.test_td_ordinary_movement import SESSION
from tests.test_td_rifleman_entry_boundaries import (
    CHAIN,
    GUNNER,
    NS,
    OPERATOR,
    fire,
    scene_for,
    start,
    step,
)
from tests.td_source_fixture import candidate as candidate_fixture

candidate = candidate_fixture


def test_approved_values_and_truthful_package_support(candidate):
    from sylvia_ruleset import rifleman_entries

    values = {rifleman_entries.GUNNER_PARAMETER: 1, rifleman_entries.OPERATOR_PARAMETER: 1}
    rifleman_entries.validate_configuration(values)
    for identity in values:
        with pytest.raises(ValueError, match="approved provisional"):
            rifleman_entries.validate_configuration({**values, identity: 2})
    with zipfile.ZipFile(candidate) as archive:
        runtime = yaml.safe_load(archive.read(f"packages/{NS}/runtime/ruleset.yaml"))
        support = json.loads(archive.read(f"packages/{NS}/support/gameplay-resolution.json"))
        reference = archive.read(f"packages/{NS}/references/rifleman-entry-provisional.md").decode()
    status = runtime["definitions"]["rifleman_entry_status"]
    assert status["status"] == "owner_approved_provisional_first_playable"
    assert not status["final_balance"] and not status["native_acceptance"]
    assert "TDH-09 r58" in status["approval_evidence"] and "03:00:36" in reference
    records = {row["id"]: row for row in support["records"]}
    assert records[GUNNER]["gameplay_resolution"] == "engine_specialized"
    assert records[OPERATOR]["gameplay_resolution"] == "hybrid"
    assert "final GM" in records[OPERATOR]["gm_part"]


def test_optional_chain_round_trips_without_adding_history_to_old_characters(candidate):
    from sylvia_ruleset import builder

    with zipfile.ZipFile(candidate) as archive:
        record = next(
            row for row in json.loads(archive.read("manifest.json"))["packages"] if row["id"] == NS
        )
    declaration = record["participant_artifacts"][0]
    participant = builder._conformance_character()["artifact"]
    authored = deepcopy(scene_for(candidate).actors["vesper"])
    for field in ("position_cell", "center", "muzzle", "control"):
        authored.pop(field)
    for field in ("target", "point"):
        authored["aim"].pop(field)
    for field in ("cyclic_rate_rpm", "controlled_by"):
        authored["weapon"].pop(field)
    for calculation in declaration["initial_state_calculations"]:
        authored.pop(calculation["set"]["field"], None)
    for marker in ("absent", None, "service_rifle"):
        supplied = deepcopy(authored)
        if marker != "absent":
            supplied[CHAIN] = marker
        complete = materialize_initial_character_state(
            participant,
            supplied,
            entity_state_schema=declaration["entity_state_schema"],
            initial_state_calculations=declaration["initial_state_calculations"],
        )
        assert (CHAIN in complete) is (marker != "absent")
        if marker != "absent":
            assert complete[CHAIN] == marker
        assert (
            verify_initial_character_state(
                participant,
                complete,
                entity_state_schema=declaration["entity_state_schema"],
                initial_state_calculations=declaration["initial_state_calculations"],
            )
            == complete
        )


@pytest.mark.parametrize("mirrored", [False, True])
def test_real_overwatch_choice_recovery_commits_recoil_chain_once(candidate, tmp_path, mirrored):
    exported = tmp_path / "pending.sylvia-gm-campaign"
    with admitted_fixture_owner(
        tmp_path / "owner.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        scene = scene_for(candidate, mirrored, (GUNNER,))
        scene.actors[scene.mover].update(hp=100, res=100)
        game = start(owner, admitted, scene)
        assert fire(owner, game) == 1
        step(owner, game, "prepare_overwatch", {"actor": game.shooter, "zone_id": "overwatch-lane"})
        side = game.actors[game.shooter]["side"]
        step(owner, game, "end_allies_turn" if side == "allies" else "end_hostiles_turn", {})
        before = owner.snapshot(game.campaign)
        command = ActionCommand(
            campaign_id=game.campaign,
            action_id="critical-crossing",
            idempotency_key="critical-crossing",
            owner_id="aim-owner",
            writer=WriterRole.DM,
            expected_revision=before.revision,
            turn_id="turn",
            package_id=NS,
            tool_name="move_actor",
            parameters={"actor": game.mover, "destination": [7, 2]},
            initiating_session_id=f"{SESSION}-{game.campaign}",
        )
        with patch.object(dice._rng, "randint", return_value=20) as draws:
            pending = StatelessMcpMechanics(owner).execute_action(command)
        assert isinstance(pending, AwaitingChoiceReceipt) and draws.call_count == 1
        assert owner.snapshot(game.campaign) == before
        owner.export_campaign("pending-gunner", game.campaign, exported)
    database = tmp_path / "restored.sqlite3"
    with AuthorityOwner.open(database, "aim-owner") as owner:
        owner.set_admin_full_audit(True)
        recovered = owner.recover_campaign(recovery_id="pending-gunner", source=exported)
        assert owner.recover_campaign(recovery_id="pending-gunner", source=exported) == recovered
        assert owner.snapshot(game.campaign) == before
        with patch.object(dice._rng, "randint") as draws:
            assert owner.execute_action(command) == pending
        assert draws.call_count == 0
        public = next(
            row.safe_payload
            for row in owner.player_projection(game.campaign)
            if row.kind.value == "mechanics_choice"
        )
        assert public["choice_id"] == "overwatch-critical-effect"
        resolution = ResolveMechanicsChoiceCommand(
            campaign_id=game.campaign,
            action_id=command.action_id,
            resolution_id="choose-critical",
            owner_id="aim-owner",
            expected_revision=before.revision,
            choice_id=public["choice_id"],
            choice_sequence=public.get("choice_sequence", 0),
            option_id="DEEP_HIT",
            session_id=f"{SESSION}-{game.campaign}",
        )
        with patch.object(dice._rng, "randint", return_value=20) as draws:
            receipt = owner.resolve_mechanics_choice(resolution)
        assert str(receipt.status) == "committed" and draws.call_count == 1
        final = owner.snapshot(game.campaign)
        actor = final.state[NS + ".entities"][game.shooter]
        assert actor["weapon"]["current_recoil"] == 3 and actor[CHAIN] == "service_rifle"
        assert actor["weapon"]["magazine"]["rounds"] == 28 and actor["ap"] == 0
        with patch.object(dice._rng, "randint") as draws:
            assert owner.resolve_mechanics_choice(resolution) == receipt
            assert owner.execute_action(command) == receipt
        assert draws.call_count == 0 and owner.snapshot(game.campaign) == final
    with AuthorityOwner.open(database, "aim-owner") as owner:
        assert owner.snapshot(game.campaign) == final
        with patch.object(dice._rng, "randint") as draws:
            assert owner.execute_action(command) == receipt
        assert draws.call_count == 0 and owner.snapshot(game.campaign) == final
