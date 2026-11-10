"""Admitted suppression events persist and cannot repeat their paid resources."""

import hashlib
import json
from copy import deepcopy

import pytest

from sylvia_gm.application.authority import (
    ActionCommand,
    AuthorityOwner,
    CampaignCreate,
    ResolveMechanicsChoiceCommand,
)
from sylvia_gm.domain.authority import (
    AwaitingChoiceReceipt,
    StateValue,
    WriterRole,
    thaw_json_value,
)
from tests.admitted_library_fixture import admitted_fixture_owner
from tests.test_td_exposure_ownership import NS, cross, game_for, setup
from tests.td_source_fixture import candidate as candidate_fixture
from tests.test_td_suppression_aim import OwnedProcedures

candidate = candidate_fixture


def create(owner, admitted, game, campaign="exposure"):
    owner.create_campaign(
        CampaignCreate(
            campaign_id=campaign,
            display_name="Synthetic authored exposure proof",
            writer=WriterRole.DM,
            package_lock=admitted.package_lock,
            turn_id="turn",
            initial_state=tuple(
                StateValue(NS, key.removeprefix(NS + "."), value)
                for key, value in game.state.items()
            ),
        )
    )
    owned = OwnedProcedures(owner, campaign)
    owned.shooter, owned.mover = game.shooter, game.mover
    return owned


@pytest.mark.parametrize("mirrored", [False, True])
@pytest.mark.parametrize("journey", ["initial-aim", "cross-aim", "cross-fire"])
def test_exposure_save_reopen_export_recovery_and_receipt_replay(
    candidate,
    tmp_path,
    mirrored,
    journey,  # noqa: F811
):
    from unittest.mock import patch

    from sylvia_gm import dice

    scene = game_for(
        candidate, mirrored, "visible" if journey.startswith("initial") else "empty", wall_end=6.05
    )
    database = tmp_path / "authority.sqlite3"
    with admitted_fixture_owner(database, "aim-owner", candidate.read_bytes()) as (owner, admitted):
        game = create(owner, admitted, scene)
        with patch.object(dice._rng, "randint", return_value=1) as draws:
            setup(game)
        assert draws.call_count == (1 if journey.startswith("initial") else 0)
        if journey.startswith("cross"):
            with patch.object(dice._rng, "randint", return_value=1) as draws:
                cross(game)
            assert draws.call_count == 1
        else:
            side = game.actors[game.shooter]["side"]
            game.execute("end_allies_turn" if side == "allies" else "end_hostiles_turn", {})
        prepared = owner.snapshot(game.campaign)
        assert game.world["prepared"]["suppression"]["capacity"] == 1
    with AuthorityOwner.open(database, "aim-owner") as owner:
        game.owner = owner
        game.replay(owner, prepared)
        shooter_before = deepcopy(game.actors[game.shooter])
        actor_before = deepcopy(game.actors[game.mover])
        tool = "fire_single" if journey.endswith("fire") else "aim"
        params = {
            "attacker": game.mover,
            "target": game.shooter,
            "aim_point": game.actors[game.shooter]["center"],
        }
        params.update({"ap": 1} if tool == "aim" else {"critical_effect": "DEEP_HIT"})
        with patch.object(dice._rng, "randint", return_value=1) as draws:
            game.execute(tool, params)
        assert draws.call_count == (2 if tool == "fire_single" else 1)
        assert game.world["prepared"]["suppression"]["capacity"] == 0
        assert game.actors[game.shooter] == shooter_before
        assert game.actors[game.mover]["ap"] == actor_before["ap"] - (
            2 if tool == "fire_single" else 1
        )
        final = owner.snapshot(game.campaign)
        game.replay(owner, final)
        export = tmp_path / "final.sylvia-gm-campaign"
        owner.export_campaign("export-final", game.campaign, export)
    with AuthorityOwner.open(database, "aim-owner") as owner:
        game.replay(owner, final)
    with AuthorityOwner.open(tmp_path / "restored.sqlite3", "aim-owner") as owner:
        result = owner.recover_campaign(recovery_id="recover-final", source=export)
        assert owner.recover_campaign(recovery_id="recover-final", source=export) == result
        game.replay(owner, final)
    with AuthorityOwner.open(tmp_path / "restored.sqlite3", "aim-owner") as owner:
        game.replay(owner, final)
    (tmp_path / "proof.json").write_text(
        json.dumps(
            {
                "journey": journey,
                "mirrored": mirrored,
                "package_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
                "prepared_revision": prepared.revision,
                "final_revision": final.revision,
                "state_sha256": hashlib.sha256(
                    json.dumps(
                        thaw_json_value(final.state),
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest(),
                "final_shooter": thaw_json_value(final.state[NS + ".entities"][game.shooter]),
                "final_actor": thaw_json_value(final.state[NS + ".entities"][game.mover]),
                "trigger": thaw_json_value(final.state[NS + ".state"].get("last_trigger", {})),
                "exposure": thaw_json_value(
                    final.state[NS + ".state"]["last_suppression_exposure"]
                ),
                "receipts_replayed": len(game.commands),
                "export_bytes": export.stat().st_size,
                "export_sha256": hashlib.sha256(export.read_bytes()).hexdigest(),
                "qualification": "synthetic owner only; no native/provider/balance acceptance",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize("mirrored", [False, True])
def test_automatic_critical_uses_durable_choice_without_partial_setup_spend(
    candidate,
    tmp_path,
    mirrored,  # noqa: F811
):
    from unittest.mock import patch

    from sylvia_gm import dice

    scene = game_for(candidate, mirrored, "visible")
    database = tmp_path / "authority.sqlite3"
    with admitted_fixture_owner(database, "aim-owner", candidate.read_bytes()) as (owner, admitted):
        game = create(owner, admitted, scene)
        before = owner.snapshot(game.campaign)
        command = ActionCommand(
            campaign_id=game.campaign,
            action_id="critical-setup",
            idempotency_key="critical-setup",
            owner_id="aim-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="turn",
            package_id=NS,
            tool_name="establish_suppression",
            parameters={"actor": game.shooter, "zone_id": "declared-prepared-lane", "rounds": 20},
        )
        with patch.object(dice._rng, "randint", return_value=20) as draws:
            pending = owner.execute_action(command)
        assert draws.call_count == 1 and isinstance(pending, AwaitingChoiceReceipt)
        assert owner.snapshot(game.campaign) == before
        assert owner.execute_action(command) == pending
    with AuthorityOwner.open(database, "aim-owner") as owner:
        assert owner.execute_action(command) == pending
        resolution = ResolveMechanicsChoiceCommand(
            campaign_id=game.campaign,
            action_id=command.action_id,
            resolution_id="critical-choice",
            owner_id="aim-owner",
            expected_revision=0,
            choice_id=pending.choice.choice_id,
            option_id="DEEP_HIT",
        )
        # This is a declared fixed-roll owner test. The resumed evaluation must
        # use the same controlled face as the suspended evaluation.
        with patch.object(dice._rng, "randint", return_value=20):
            receipt = owner.resolve_mechanics_choice(resolution)
        assert receipt.status.value == "committed"
        final = owner.snapshot(game.campaign)
        assert final.revision == 1
        assert final.state[NS + ".state"]["prepared"]["suppression"]["capacity"] == 1
        assert (
            final.state[NS + ".entities"][game.shooter]["ap"]
            == scene.actors[game.shooter]["ap"] - 2
        )
        assert owner.execute_action(command) == receipt
        assert owner.resolve_mechanics_choice(resolution) == receipt
        assert owner.snapshot(game.campaign) == final


@pytest.mark.parametrize("kind", ["critical", "order"])
def test_automatic_choices_do_not_publish_hidden_actor_names_or_ids(candidate, tmp_path, kind):
    from unittest.mock import patch

    from sylvia_gm import dice

    scene = game_for(candidate, False, "visible")
    hidden = [scene.mover]
    if kind == "order":
        second = "B_VESPER_2"
        actor = scene.actors[second]
        shift = [10.5 - actor["center"][0], 6.5 - actor["center"][1]]
        actor["position_cell"], actor["center"] = [10, 6], [10.5, 6.5, 0.9]
        actor["muzzle"] = [
            actor["muzzle"][0] + shift[0],
            actor["muzzle"][1] + shift[1],
            actor["muzzle"][2],
        ]
        next(row for row in scene.world["spatial_scene"]["entities"] if row["id"] == second)[
            "position"
        ] = [10.5, 6.5, 0]
        hidden.append(second)
    for index, actor_id in enumerate(hidden):
        scene.actors[actor_id]["name"] = f"NOT_PUBLIC_CONTACT_{index}"
        next(row for row in scene.world["spatial_scene"]["entities"] if row["id"] == actor_id)[
            "metadata"
        ].pop("player", None)
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = create(owner, admitted, scene)
        command = ActionCommand(
            campaign_id=game.campaign,
            action_id="hidden-exposures",
            idempotency_key="hidden-exposures",
            owner_id="aim-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="turn",
            package_id=NS,
            tool_name="establish_suppression",
            parameters={"actor": game.shooter, "zone_id": "declared-prepared-lane", "rounds": 20},
        )
        with patch.object(dice._rng, "randint", return_value=20 if kind == "critical" else 1):
            pending = owner.execute_action(command)
        assert isinstance(pending, AwaitingChoiceReceipt)
        text = json.dumps(
            {
                "prompt": pending.choice.prompt,
                "options": [(row.option_id, row.label) for row in pending.choice.options],
            }
        )
        assert "NOT_PUBLIC_CONTACT" not in text
        assert all(actor_id not in text for actor_id in hidden)
        assert len({row.label for row in pending.choice.options}) == len(pending.choice.options)


@pytest.mark.parametrize("mirrored", [False, True])
@pytest.mark.parametrize(
    "literal_label",
    [pytest.param(False, id="plain-label"), pytest.param(True, id="literal-template-label")],
)
def test_multiple_initial_exposures_use_controller_order_and_one_finite_commit(
    candidate,
    tmp_path,
    mirrored,
    literal_label,
):
    from unittest.mock import patch

    from sylvia_gm import dice

    scene = game_for(candidate, mirrored, "visible")
    if literal_label:
        next(row for row in scene.world["spatial_scene"]["entities"] if row["id"] == scene.mover)[
            "metadata"
        ]["player"]["label"] = "Contact {actor.name}"
    second = "A_VESPER_2" if mirrored else "B_VESPER_2"
    actor = scene.actors[second]
    shift = [10.5 - actor["center"][0], 6.5 - actor["center"][1]]
    actor["position_cell"], actor["center"] = [10, 6], [10.5, 6.5, 0.9]
    actor["muzzle"] = [
        actor["muzzle"][0] + shift[0],
        actor["muzzle"][1] + shift[1],
        actor["muzzle"][2],
    ]
    next(row for row in scene.world["spatial_scene"]["entities"] if row["id"] == second)[
        "position"
    ] = [10.5, 6.5, 0]
    with admitted_fixture_owner(
        tmp_path / "authority.sqlite3", "aim-owner", candidate.read_bytes()
    ) as (owner, admitted):
        game = create(owner, admitted, scene)
        before = owner.snapshot(game.campaign)
        command = ActionCommand(
            campaign_id=game.campaign,
            action_id="ordered-setup",
            idempotency_key="ordered-setup",
            owner_id="aim-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="turn",
            package_id=NS,
            tool_name="establish_suppression",
            parameters={"actor": game.shooter, "zone_id": "declared-prepared-lane", "rounds": 20},
        )
        with patch.object(dice._rng, "randint", return_value=1) as draws:
            pending = owner.execute_action(command)
        assert isinstance(pending, AwaitingChoiceReceipt) and draws.call_count == 0
        assert pending.choice.choice_id == "initial-suppression-order"
        assert {row.option_id for row in pending.choice.options} == {"exposure-0", "exposure-1"}
        bodies = scene.world["spatial_scene"]["entities"]
        expected_labels = [
            next(row for row in bodies if row["id"] == actor_id)["metadata"]["player"]["label"]
            + f" [{index + 1}]"
            for index, actor_id in enumerate((game.mover, second))
        ]
        assert [row.label for row in pending.choice.options] == expected_labels
        assert owner.snapshot(game.campaign) == before
        export = tmp_path / "pending.sylvia-gm-campaign"
        owner.export_campaign("pending-export", game.campaign, export)
    with AuthorityOwner.open(tmp_path / "restored.sqlite3", "aim-owner") as owner:
        owner.recover_campaign(recovery_id="pending-order", source=export)
        assert owner.execute_action(command) == pending
        resolution = ResolveMechanicsChoiceCommand(
            campaign_id=game.campaign,
            action_id=command.action_id,
            resolution_id="ordered-choice",
            owner_id="aim-owner",
            expected_revision=0,
            choice_id=pending.choice.choice_id,
            option_id="exposure-1",
        )
        with patch.object(dice._rng, "randint", return_value=1) as draws:
            receipt = owner.resolve_mechanics_choice(resolution)
        assert draws.call_count == 2
        final = owner.snapshot(game.campaign)
        assert final.revision == 1
        state = final.state[NS + ".state"]
        assert state["prepared"]["suppression"]["capacity"] == 0
        assert not state["prepared"]["suppression"]["active"]
        assert state["last_suppression_exposure"]["actor"] == game.mover
        shooter = final.state[NS + ".entities"][game.shooter]
        assert shooter["ap"] == scene.actors[game.shooter]["ap"] - 2
        assert shooter["weapon"]["magazine"]["rounds"] == (
            scene.actors[game.shooter]["weapon"]["magazine"]["rounds"] - 20
        )
        assert owner.execute_action(command) == receipt
        assert owner.resolve_mechanics_choice(resolution) == receipt
        assert owner.snapshot(game.campaign) == final
