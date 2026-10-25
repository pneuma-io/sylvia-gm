"""Restart handshake fixtures use inert launch callbacks and a tiny disposable SQLite database."""

import hashlib
import json
import sqlite3
from contextlib import closing

import pytest

from sylvia_gm.application.desktop_update import restart
from sylvia_gm.application.desktop_update.manifest import UpdateError, Version


@pytest.fixture
def fixture(tmp_path):
    root, live = tmp_path / "data", tmp_path / "app"
    root.mkdir()
    live.mkdir()
    identity, attempt = "a" * 32, "b" * 32
    (root / ".application-updates" / identity / "attempts" / attempt).mkdir(parents=True)
    database = root / "authority.sqlite3"
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("CREATE TABLE fictional_progress(value TEXT)")
        connection.execute("INSERT INTO fictional_progress VALUES ('saved progress')")
    handshake = restart.RestartHandshake(root, identity, attempt)
    nonce = handshake.create(
        live=live,
        version=Version.parse("0.3.0-alpha.2"),
        target="windows-x86_64",
        distribution="portable",
        owner_storage_compatibility=1,
    )
    arguments = {
        "nonce": nonce,
        "live": live,
        "desktop_version": "0.3.0-alpha.2",
        "owner_version": "0.3.0-alpha.2",
        "target": "windows-x86_64",
        "distribution": "portable",
        "storage_compatibility": 1,
    }
    return handshake, arguments, database


def test_launch_once_then_exact_owner_acknowledgement_preserves_database(fixture):
    handshake, arguments, database = fixture
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    calls = []
    assert handshake.confirmed() is False
    process = object()
    assert handshake.launch_once(lambda argv: calls.append(argv) or process) is process
    with pytest.raises(UpdateError) as duplicate:
        handshake.launch_once(lambda argv: calls.append(argv))
    assert duplicate.value.code == "restart_already_attempted"
    assert calls == [("--sylvia-update-start", "a" * 32, "b" * 32, arguments["nonce"])]
    assert handshake.confirmed() is False
    handshake.confirm(**arguments)
    handshake.confirm(**arguments)
    assert handshake.confirmed() is True
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    row = json.loads(handshake.receipt.read_bytes())
    assert "saved progress" not in handshake.receipt.read_text()
    assert row["desktop_version"] == row["owner_version"] == row["version"] == "0.3.0-alpha.2"


@pytest.mark.parametrize(
    "field,value",
    [
        ("nonce", "c" * 32),
        ("desktop_version", "0.3.0-alpha.1"),
        ("owner_version", "0.3.0-alpha.1"),
        ("target", "windows-aarch64"),
        ("distribution", "installed"),
        ("storage_compatibility", 2),
        ("storage_compatibility", True),
    ],
)
def test_mismatched_restart_cannot_write_a_success_receipt(fixture, field, value):
    handshake, arguments, database = fixture
    before = database.read_bytes()
    handshake.launch_once(lambda _argv: object())
    with pytest.raises(UpdateError):
        handshake.confirm(**{**arguments, field: value})
    assert not handshake.receipt.exists()
    assert not handshake.confirmed()
    assert database.read_bytes() == before


def test_pending_intent_is_not_a_successful_startup(fixture):
    handshake, arguments, _ = fixture
    with pytest.raises(UpdateError) as pending:
        handshake.confirm(**arguments)
    assert pending.value.code == "wrong_restart"
    assert not handshake.receipt.exists()


def test_launch_receipt_failure_never_replays_the_launch_and_correlated_ack_can_reconcile(
    fixture, monkeypatch
):
    handshake, arguments, _ = fixture
    original = restart.atomic_receipt
    calls = []

    def uncertain(path, value):
        if path == handshake.ticket and value["phase"] == "launched":
            raise UpdateError("journal_failed", "Controlled lost launch acknowledgement")
        return original(path, value)

    monkeypatch.setattr(restart, "atomic_receipt", uncertain)
    with pytest.raises(UpdateError):
        handshake.launch_once(lambda argv: calls.append(argv) or object())
    with pytest.raises(UpdateError) as duplicate:
        handshake.launch_once(lambda argv: calls.append(argv))
    assert duplicate.value.code == "restart_already_attempted" and len(calls) == 1
    monkeypatch.setattr(restart, "atomic_receipt", original)
    handshake.confirm(**arguments)
    assert handshake.confirmed()


def test_copied_data_root_or_other_app_directory_is_not_the_selected_copy(fixture, tmp_path):
    handshake, arguments, _ = fixture
    handshake.launch_once(lambda _argv: object())
    other_app = tmp_path / "independent-app"
    other_app.mkdir()
    with pytest.raises(UpdateError):
        handshake.confirm(**{**arguments, "live": other_app})
    copied_root = tmp_path / "independent-data"
    copied_stage = copied_root / ".application-updates" / ("a" * 32) / "attempts" / ("b" * 32)
    copied_stage.mkdir(parents=True)
    (copied_stage / "restart.json").write_bytes(handshake.ticket.read_bytes())
    copied = restart.RestartHandshake(copied_root, "a" * 32, "b" * 32)
    with pytest.raises(UpdateError):
        copied.confirm(**arguments)
    assert not copied.receipt.exists() and not handshake.receipt.exists()
