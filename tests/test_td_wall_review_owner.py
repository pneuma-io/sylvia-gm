"""Actual admitted wall authoring: private evidence and continuity membership regressions."""

import sqlite3
from copy import deepcopy
from dataclasses import replace

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.errors import IdempotencyConflict, RulesetPreconditionRejected, StaleRevision
from tests.test_td_grid_edge_owner import candidate  # noqa: F401
from tests.test_td_wall_authoring import edit, segment, setup_owner, state


@pytest.fixture(scope="module")
def wall_archive(candidate):  # noqa: F811
    """Exercise the declared TD producer built from the current checkout."""
    return candidate


def public_results(owner):
    return [
        item.safe_payload
        for item in owner.player_projection("identity")
        if item.kind.value == "tool_card"
    ]


@pytest.mark.parametrize("hidden", [False, True])
def test_private_wall_metadata_is_not_a_player_safe_tool_result(wall_archive, tmp_path, hidden):
    database = setup_owner(tmp_path, wall_archive)
    pieces = [segment(), segment(3, 4, visible=not hidden)]
    secret = "SYNTHETIC-UNSEEN-WALL-METADATA"
    pieces[1]["metadata"]["nested"] = {"private": [{"details": secret}]}
    with AuthorityOwner.open(database, "identity-owner") as owner:
        command, receipt = edit(owner, "draw", segments=pieces, key="draw")
        geometry = state(owner)["spatial_scene"]["geometry"]
        assert geometry[1]["metadata"]["nested"]["private"][0]["details"] == secret
        # The canonical owner receipt retains the complete reproduction evidence.
        assert secret in str(receipt.result)
        player = public_results(owner)
        desktop = owner.desktop_snapshot("identity")
        desktop_display = owner.desktop_snapshot("identity", display_only=True)
        replay = owner.canonical_replay("identity", acknowledged_sequence=0)
        page = owner.player_projection_page("identity", after_sequence=0)
        assert player
        assert secret not in str(player)
        assert "spatial_evidence" not in str(player)
        assert "spatial_evidence" not in str(desktop)
        assert "spatial_evidence" not in str(desktop_display)
        assert secret not in str(desktop) and secret not in str(desktop_display)
        assert secret not in str(replay) and secret not in str(page)
        public_text = str((player, desktop, desktop_display, replay, page))
        for item in geometry:
            assert item["id"] not in public_text
            assert item["edge_layer_id"] not in public_text
        assert owner.execute_action(command) == receipt
        after = owner.snapshot("identity")
    with AuthorityOwner.open(database, "identity-owner") as owner:
        assert owner.snapshot("identity") == after
        assert owner.execute_action(command) == receipt
        assert public_results(owner) == player
        assert owner.desktop_snapshot("identity") == desktop
        assert owner.desktop_snapshot("identity", display_only=True) == desktop_display
        assert owner.canonical_replay("identity", acknowledged_sequence=0) == replay
        assert owner.player_projection_page("identity", after_sequence=0) == page
        assert owner.snapshot("identity") == after  # Reading legacy evidence performs no migration.


@pytest.mark.parametrize("horizontal", [False, True])
def test_middle_separation_preserves_connected_wall_memberships(wall_archive, tmp_path, horizontal):
    database = setup_owner(tmp_path, wall_archive)
    with AuthorityOwner.open(database, "identity-owner") as owner:
        draw, drawn_receipt = edit(
            owner,
            "draw",
            segments=[
                segment(i, i + 1, horizontal, hp=12 + 8 * index, visible=index != 2)
                for index, i in enumerate((2, 3, 4))
            ],
            key="draw",
        )
        before = deepcopy(state(owner)["spatial_scene"])
        edit(owner, "draw", segments=[segment(3, 4, horizontal)], key="independent")
        independent = deepcopy(state(owner)["spatial_scene"]["geometry"][-1])
        selected = before["geometry"][1]["id"]
        stale = owner.snapshot("identity")
        with pytest.raises(RulesetPreconditionRejected, match="map changed"):
            edit(owner, "separate", [selected], key="stale-map", revision=0)
        assert owner.snapshot("identity") == stale
        separate, separated_receipt = edit(owner, "separate", [selected], key="separate")
        separated = state(owner)["spatial_scene"]
        assert separated["map"]["revision"] == before["map"]["revision"] + 2
        assert len({item["edge_layer_id"] for item in separated["geometry"][:3]}) == 3
        assert separated["geometry"][-1] == independent
        for old, new in zip(before["geometry"], separated["geometry"][:3], strict=True):
            assert {key: value for key, value in new.items() if key != "edge_layer_id"} == {
                key: value for key, value in old.items() if key != "edge_layer_id"
            }
        saved = owner.snapshot("identity")
        assert owner.execute_action(separate) == separated_receipt
        assert owner.snapshot("identity") == saved
        with pytest.raises(StaleRevision):
            owner.execute_action(
                replace(separate, action_id="stale-campaign", idempotency_key="stale-campaign")
            )
        assert owner.snapshot("identity") == saved
        edit(owner, "draw", segments=[segment(1, 2, horizontal)], key="left-neighbor")
        neighbor = state(owner)["spatial_scene"]["geometry"][-1]["id"]
        left, left_receipt = edit(
            owner, "connect", [before["geometry"][0]["id"], neighbor], key="reconnect-left"
        )
        edit(owner, "draw", segments=[segment(5, 6, horizontal)], key="right-neighbor")
        neighbor = state(owner)["spatial_scene"]["geometry"][-1]["id"]
        right, right_receipt = edit(
            owner, "connect", [before["geometry"][2]["id"], neighbor], key="reconnect-right"
        )
        after = owner.snapshot("identity")
        assert (
            next(
                item
                for item in state(owner)["spatial_scene"]["geometry"]
                if item["id"] == independent["id"]
            )
            == independent
        )
        for command, receipt in (
            (draw, drawn_receipt),
            (separate, separated_receipt),
            (left, left_receipt),
            (right, right_receipt),
        ):
            assert owner.execute_action(command) == receipt
        assert owner.snapshot("identity") == after
        owner.backup_database(tmp_path / "recovery.sqlite3")
    for selected in (database, tmp_path / "recovery.sqlite3"):
        with AuthorityOwner.open(selected, "identity-owner") as owner:
            assert owner.snapshot("identity") == after
            assert owner.execute_action(separate) == separated_receipt
            assert owner.execute_action(left) == left_receipt
            assert owner.execute_action(right) == right_receipt
            assert owner.snapshot("identity") == after


@pytest.mark.parametrize("same_action", [False, True])
def test_divergent_payload_retry_refuses_without_new_identity_or_mutation(
    wall_archive,
    tmp_path,
    same_action,
):
    database = setup_owner(tmp_path, wall_archive)
    with AuthorityOwner.open(database, "identity-owner") as owner:
        command, receipt = edit(owner, "draw", segments=[segment(), segment(3, 4)], key="draw")
        after = owner.snapshot("identity")
        parameters = deepcopy(dict(command.parameters))
        parameters["segments"][0]["metadata"]["private"] = "SYNTHETIC-DIVERGENT-PAYLOAD"
        divergent = replace(
            command,
            parameters=parameters,
            action_id=command.action_id if same_action else "other-action",
        )
        with pytest.raises(IdempotencyConflict):
            owner.execute_action(divergent)
        assert owner.snapshot("identity") == after
        assert owner.execute_action(command) == receipt
    with AuthorityOwner.open(database, "identity-owner") as owner:
        with pytest.raises(IdempotencyConflict):
            owner.execute_action(divergent)
        assert owner.execute_action(command) == receipt
        assert owner.snapshot("identity") == after


def test_sqlite_save_failure_rolls_back_then_same_command_retries_after_reopen(
    wall_archive,
    tmp_path,
    monkeypatch,
):
    database = setup_owner(tmp_path, wall_archive)
    with AuthorityOwner.open(database, "identity-owner") as owner:
        before = owner.snapshot("identity")
        original_connect = owner._store._connect

        def fail_late_sqlite_write():
            connection = original_connect()
            connection.execute("""
                CREATE TEMP TRIGGER synthetic_wall_save_failure
                BEFORE INSERT ON main.idempotency_records
                WHEN NEW.action_id = 'save-failure'
                BEGIN SELECT RAISE(ABORT, 'synthetic SQLite wall-save failure'); END
            """)
            return connection

        # A real SQLite trigger aborts the late receipt write, after scene/events were staged.
        with monkeypatch.context() as failure:
            failure.setattr(owner._store, "_connect", fail_late_sqlite_write)
            with pytest.raises(sqlite3.IntegrityError, match="synthetic SQLite") as caught:
                edit(owner, "draw", segments=[segment(), segment(3, 4)], key="save-failure")
            assert caught.value.sqlite_errorname == "SQLITE_CONSTRAINT_TRIGGER"
        assert owner.snapshot("identity") == before
        with sqlite3.connect(database) as connection:
            assert (
                connection.execute(
                    "SELECT count(*) FROM idempotency_records WHERE campaign_id = 'identity'"
                ).fetchone()[0]
                == 0
            )
    with AuthorityOwner.open(database, "identity-owner") as owner:
        assert owner.snapshot("identity") == before
        command, receipt = edit(
            owner, "draw", segments=[segment(), segment(3, 4)], key="save-failure"
        )
        after = owner.snapshot("identity")
        assert after.revision == before.revision + 1
        geometry = state(owner)["spatial_scene"]["geometry"]
        assert len(geometry) == len({item["id"] for item in geometry}) == 2
        assert len({item["edge_layer_id"] for item in geometry}) == 1
        assert owner.execute_action(command) == receipt
        assert owner.snapshot("identity") == after
    with AuthorityOwner.open(database, "identity-owner") as owner:
        assert owner.execute_action(command) == receipt
        assert owner.snapshot("identity") == after


def test_separating_branch_preserves_the_connected_remnant_identity(wall_archive, tmp_path):
    database = setup_owner(tmp_path, wall_archive)
    arm = segment(2, 3, horizontal=True)
    arm["volume"]["min"][1] = arm["volume"]["max"][1] = 3
    with AuthorityOwner.open(database, "identity-owner") as owner:
        edit(owner, "draw", segments=[segment(), segment(3, 4), arm], key="branch")
        original = deepcopy(state(owner)["spatial_scene"]["geometry"])
        edit(owner, "separate", [original[-1]["id"]], key="separate-arm")
        separated = state(owner)["spatial_scene"]["geometry"]
        assert separated[:2] == original[:2]
        assert separated[-1]["edge_layer_id"] != original[-1]["edge_layer_id"]
        edit(owner, "connect", [item["id"] for item in original], key="reconnect-arm")
        connected = state(owner)["spatial_scene"]["geometry"]
        assert len({item["edge_layer_id"] for item in connected}) == 1
        for old, new in zip(original, connected, strict=True):
            assert {key: value for key, value in old.items() if key != "edge_layer_id"} == {
                key: value for key, value in new.items() if key != "edge_layer_id"
            }
