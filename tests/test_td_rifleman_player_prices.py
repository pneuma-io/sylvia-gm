"""Real character-panel prices and manipulation prerequisites on the admitted package."""

import json
from copy import deepcopy
from unittest.mock import patch

import pytest

from sylvia_gm import dice
from sylvia_gm.domain.authority import thaw_json_value
from sylvia_gm.domain.errors import CommandRejected, StaleRevision
from tests.admitted_library_fixture import admitted_fixture_owner
from tests.test_td_ordinary_movement import SESSION
from tests.test_td_rifleman_entry_boundaries import OPERATOR, scene_for, start, step
from tests.td_source_fixture import candidate as candidate_fixture

candidate = candidate_fixture


@pytest.mark.parametrize("operation", ["reload-retain", "cycle", "load-shell"])
def test_operator_character_panel_preview_queue_commit_and_control(candidate, tmp_path, operation):
    with admitted_fixture_owner(
        tmp_path / "owner.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        proofs = []
        for enabled in (False, True):
            scene = scene_for(candidate, entries=(OPERATOR,) if enabled else ())
            actor = scene.actors[scene.shooter]
            actor["ap"] = 1 if enabled else 2
            if operation != "reload-retain":
                actor["weapon"].update(
                    feed="TUBE",
                    mechanism="MANUAL_ACTION",
                    tube_capacity=5,
                    spare_shells=2,
                    shell_load_ap=2,
                    cycle_ap=2,
                    chamber="EMPTY",
                    needs_cycle=True,
                    magazine={"id": "diagnostic-tube", "rounds": 3},
                )
            game = start(owner, admitted, scene, campaign=f"panel-{enabled}")
            before = owner.snapshot(game.campaign)
            source = {"kind": "panel", "surface_id": "operations.character", "action_id": operation}
            selection = {"actor": game.shooter}
            available = owner.player_action_availability(
                campaign_id=game.campaign,
                expected_revision=before.revision,
                requests=[{"source": source, "selection": selection}],
            )["results"][0]
            assert available["available"], available
            request = {
                "campaign_id": game.campaign,
                "surface_id": "operations.character",
                "panel_action_id": operation,
                "operation_id": "manipulate",
                "expected_revision": before.revision,
                "selection": selection,
            }
            preview = owner.preview_panel_action(**request)
            cost = next(
                row["value"] for row in preview["preview"]["summary"] if row["label"] == "AP cost"
            )
            assert cost == (1 if enabled else 2)
            assert owner.snapshot(game.campaign) == before
            sequence = owner.compose_action_sequence(
                campaign_id=game.campaign,
                sequence_id="manipulation-queue",
                session_id=f"{SESSION}-{game.campaign}",
                expected_revision=before.revision,
                steps=[{"step_id": "manipulate", "source": source, "selection": selection}],
            )
            assert owner.snapshot(game.campaign) == before
            with patch.object(dice._rng, "randint") as draws:
                result = owner.execute_action_sequence(
                    campaign_id=game.campaign,
                    sequence_id="manipulation-queue",
                    session_id=f"{SESSION}-{game.campaign}",
                    expected_version=sequence["version"],
                    confirmation=sequence["confirmation"],
                )
            assert draws.call_count == 0 and result["steps"][0]["status"] == "committed"
            final = owner.snapshot(game.campaign)
            assert game.actors[game.shooter]["ap"] == 0
            assert game.world["last_reload"]["ap_spent"] == cost
            with patch.object(dice._rng, "randint") as draws:
                assert (
                    owner.execute_action_sequence(
                        campaign_id=game.campaign,
                        sequence_id="manipulation-queue",
                        session_id=f"{SESSION}-{game.campaign}",
                        expected_version=sequence["version"],
                        confirmation=sequence["confirmation"],
                    )
                    == result
                )
            assert draws.call_count == 0 and owner.snapshot(game.campaign) == final
            with pytest.raises(StaleRevision):
                owner.preview_panel_action(**{**request, "operation_id": "stale"})
            with pytest.raises(CommandRejected):
                owner.preview_panel_action(
                    **{
                        **request,
                        "operation_id": "foreign",
                        "expected_revision": final.revision,
                        "selection": {"actor": game.mover},
                    }
                )
            proofs.append({"enabled": enabled, "cost": cost, "queue": thaw_json_value(result)})
        (tmp_path / "panel-proof.json").write_text(json.dumps(proofs, indent=2) + "\n")


@pytest.mark.parametrize("invalid", ["no-weapon", "missing-shell-cost", "controlled", "stowed"])
def test_invalid_manipulation_never_spends_or_changes_actor(candidate, tmp_path, invalid):
    with admitted_fixture_owner(
        tmp_path / "owner.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        scene = scene_for(candidate)
        actor = scene.actors[scene.shooter]
        if invalid == "no-weapon":
            actor["weapon"] = None
        elif invalid == "controlled":
            actor["weapon"]["controlled_by"] = scene.mover
        elif invalid == "stowed":
            actor["weapon"]["location"] = "STOWED"
        else:
            actor["weapon"].update(
                feed="TUBE", mechanism="MANUAL_ACTION", tube_capacity=5, spare_shells=2
            )
            actor["weapon"].pop("shell_load_ap", None)
        game = start(owner, admitted, scene)
        for name in ("reload_weapon", "cycle_weapon", "load_one_shell"):
            before = deepcopy(game.actors[game.shooter])
            args = {"actor": game.shooter}
            if name == "reload_weapon":
                args["disposition"] = "RETAIN"
            assert step(owner, game, name, args) == 0
            assert game.actors[game.shooter] == before


def test_operator_floor_is_one_even_for_an_explicit_zero_base_reload(candidate, tmp_path):
    """The approved automatic floor applies; explicit final GM totals are separate."""
    with admitted_fixture_owner(
        tmp_path / "owner.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        for enabled in (False, True):
            scene = scene_for(candidate, entries=(OPERATOR,) if enabled else ())
            scene.actors[scene.shooter]["weapon"]["reload_ap"] = 0
            game = start(owner, admitted, scene, campaign=f"zero-base-{enabled}")
            before = deepcopy(game.actors[game.shooter])
            preview = owner.preview_panel_action(
                campaign_id=game.campaign,
                surface_id="operations.character",
                panel_action_id="reload-retain",
                operation_id="floor-preview",
                expected_revision=owner.snapshot(game.campaign).revision,
                selection={"actor": game.shooter},
            )
            assert next(
                row["value"] for row in preview["preview"]["summary"] if row["label"] == "AP cost"
            ) == int(enabled)
            assert game.actors[game.shooter] == before
            step(owner, game, "reload_weapon", {"actor": game.shooter, "disposition": "RETAIN"})
            assert game.actors[game.shooter]["ap"] == before["ap"] - int(enabled)
            assert game.actors[game.shooter]["weapon"]["reload_ap"] == 0
            assert (
                game.actors[game.shooter]["weapon"]["magazine"]
                == before["weapon"]["spare_magazines"][-1]
            )
