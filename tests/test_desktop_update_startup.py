"""Before-migration restart admission and after-startup acknowledgement use inert files."""

import hashlib
import io
import json
import sqlite3
from contextlib import closing
from types import SimpleNamespace

import pytest

from sylvia_gm.application.desktop_update.checkpoint import DataCheckpoint
from sylvia_gm.application.desktop_update.launch import Installation
from sylvia_gm.application.desktop_update.manifest import UpdateError, Version
from sylvia_gm.application.desktop_update.startup import authorize_startup
from sylvia_gm.application.desktop_update.storage import UpdateStore, directory_identity
from tests.test_desktop_update_manifest import accept_stub
from tests.test_desktop_update_preparation import fixture


def prepared(tmp_path, compatibility=1):
    update, _, backup, _, _, database = fixture(tmp_path)
    update.prepare(backup, lambda: True)
    update.apply(lambda: True)
    handshake = update.create_restart({
        "format_version": 1, "product": "Sylvia GM", "component": "owner",
        "version": "0.3.0-alpha.2", "target": "windows-x86_64",
        "storage_compatibility": compatibility,
    })
    arguments = []
    handshake.launch_once(lambda argv: arguments.extend(argv))
    installation = Installation(
        update.live, update.root, "portable", "windows-x86_64",
        directory_identity(update.live), directory_identity(update.root),
    )
    request = {"operation_id": update.identity, "attempt": update.attempt, "nonce": arguments[-1]}
    return update, handshake, installation, request, database


def authorize(installation, request, compatibility=1, desktop="0.3.0-alpha.2"):
    return authorize_startup(
        installation.data, installation, request,
        running=Version.parse("0.3.0-alpha.2"), desktop_version=desktop,
        storage_compatibility=compatibility, verify=accept_stub,
    )


def test_authorization_is_read_only_and_success_waits_for_usable_app_confirmation(tmp_path):
    update, handshake, installation, request, database = prepared(tmp_path)
    before = database.read_bytes()
    journal = (update.base.parent / "current.json").read_bytes()
    start = authorize(installation, request)
    assert start is not None
    assert not handshake.confirmed()
    assert not handshake.receipt.exists()
    assert database.read_bytes() == before
    assert (update.base.parent / "current.json").read_bytes() == journal
    assert start.confirm() is False  # Usable-app acknowledgement precedes helper completion.
    assert update.store.load()["phase"] == "awaiting_restart"
    update.confirm()  # The serialized old helper finalizes once, before owner admission opens.
    assert start.confirm() is True
    assert start.confirm() is True
    assert handshake.confirmed()
    assert update.store.load()["phase"] == "confirmed"
    assert database.read_bytes() == before


def test_new_storage_contract_can_check_old_checkpoint_without_admitting_old_app_rollback(tmp_path):
    update, handshake, installation, request, database = prepared(tmp_path, compatibility=2)
    start = authorize(installation, request, compatibility=2)
    assert start.retained_compatibility == 1
    assert not handshake.confirmed()
    # The new owner performs its migration only after admission; confirmation records that schema.
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("ALTER TABLE fictional_progress ADD COLUMN new_semantics TEXT")
    checkpoint = DataCheckpoint(database, update.stage)
    with pytest.raises(UpdateError) as incompatible:
        checkpoint.require_compatible(2)
    assert incompatible.value.code == "restore_choice_required"
    assert start.confirm() is False
    update.confirm()
    assert start.confirm() is True
    receipt = json.loads(handshake.receipt.read_bytes())
    assert receipt["storage_compatibility"] == 2
    assert receipt["schema_sha256"] != checkpoint._load()["schema_sha256"]


@pytest.mark.parametrize("change", ["nonce", "desktop", "operation", "extra", "tampered_app"])
def test_wrong_restart_never_opens_or_changes_database(tmp_path, change):
    update, handshake, installation, request, database = prepared(tmp_path)
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    desktop = "0.3.0-alpha.2"
    if change == "nonce":
        request["nonce"] = "c" * 32
    elif change == "desktop":
        desktop = "0.3.0-alpha.99"
    elif change == "operation":
        request["operation_id"] = "c" * 32
    elif change == "extra":
        request["path"] = "untrusted"
    else:
        (update.live / "sylvia-gm-owner.exe").write_bytes(b"tampered inert file")
    with pytest.raises(UpdateError):
        authorize(installation, request, desktop=desktop)
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    assert not handshake.receipt.exists()
    assert update.store.load()["phase"] == "awaiting_restart"


def test_ordinary_pending_start_rejects_before_owner_acquisition_or_migration(
    tmp_path, monkeypatch,
):
    from sylvia_gm.interfaces import owner_process

    root = tmp_path / "data"
    root.mkdir()
    store = UpdateStore(root / ".application-updates")
    identity, _ = store.create()
    store.save(identity, "applying", "0.3.0-alpha.2")
    database = root / "authority.sqlite3"
    database.write_bytes(b"must never be opened or migrated")
    before = database.read_bytes()

    def no_launch(*_args, **_kwargs):
        pytest.fail("pending apply must reject before authority acquisition")

    monkeypatch.setattr(owner_process, "_launch", no_launch)
    launch = {"type": "launch", "database_path": str(database),
              "secret_hex": "a1" * 32, "owner_id": "fixture-unopened"}
    output = io.StringIO()
    assert owner_process.run(io.StringIO(json.dumps(launch) + "\n"), output) == 2
    result = json.loads(output.getvalue())
    assert result["type"] == "update_recovery_required" and result["ok"] is False
    assert database.read_bytes() == before


def test_normal_new_profile_needs_no_updater_files_or_trust(tmp_path):
    assert authorize_startup(
        tmp_path / "not-created", None, None, running=Version.parse("0.3.0-alpha.1"),
        desktop_version="0.3.0-alpha.1", storage_compatibility=1, verify=accept_stub,
    ) is None
    assert list(tmp_path.iterdir()) == []


def test_retained_checkpoint_contract_cannot_be_changed_after_preparation(tmp_path):
    update, handshake, installation, request, database = prepared(tmp_path)
    path = update.stage / "data-checkpoint/checkpoint.json"
    row = json.loads(path.read_bytes())
    row["storage_compatibility"] = 2
    path.write_text(json.dumps(row), encoding="utf-8")
    before = database.read_bytes()
    with pytest.raises(UpdateError) as error:
        authorize(installation, request)
    assert error.value.code == "changed_preparation"
    assert not handshake.receipt.exists()
    assert database.read_bytes() == before


def test_owner_refuses_new_work_until_native_confirmation_without_automatic_replay(
    tmp_path, monkeypatch,
):
    from sylvia_gm import __version__
    from sylvia_gm.application.desktop_update import service, startup
    from sylvia_gm.interfaces import owner_process
    from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness

    confirmations = []
    completions = []
    original_completed = service.DesktopUpdateService.startup_completed

    def completed(boundary):
        completions.append("completed")
        original_completed(boundary)

    monkeypatch.setattr(service.DesktopUpdateService, "startup_completed", completed)
    def complete():
        confirmations.append("acknowledged")
        return len(confirmations) > 1

    pending = SimpleNamespace(
        version=__version__, installation=SimpleNamespace(
            target="windows-x86_64", distribution="portable",
        ), confirm=complete,
    )
    # This case tests owner admission, not authenticity; the preceding cases test authorization.
    monkeypatch.setattr(startup, "owner_startup_context", lambda _launch: (None, pending))
    monkeypatch.setattr(owner_process, "_preinstall_bundled_srd", lambda _bus: None)
    monkeypatch.setattr(owner_process, "_preinstall_bundled_riichi", lambda _bus: None)
    owner = _OwnerHarness(tmp_path / "authority.sqlite3", None)
    try:
        host = {"version": __version__, "target": "windows-x86_64", "distribution": "portable"}
        owner.request("boot-update-projection", "desktop_update_state", {"host": host})
        assert owner.request("before", "builder_chat_index") == []
        owner.send("blocked", {"method": "create_builder_chat", "params": {}})
        assert owner.read()["error"]["code"] == "update_startup_unconfirmed"
        owner.send("wrong-native", {"method": "desktop_update_confirm_startup", "params": {
            "host": {"version": "wrong", "target": "windows-x86_64", "distribution": "portable"},
        }})
        assert owner.read()["ok"] is False and confirmations == []
        assert owner.request("acknowledge", "desktop_update_confirm_startup", {"host": {
            "version": __version__, "target": "windows-x86_64", "distribution": "portable",
        }}) == {"status": "waiting_for_helper"}
        owner.send("still-blocked", {"method": "create_builder_chat", "params": {}})
        assert owner.read()["error"]["code"] == "update_startup_unconfirmed"
        assert owner.request("confirm", "desktop_update_confirm_startup", {"host": {
            "version": __version__, "target": "windows-x86_64", "distribution": "portable",
        }}) == {"status": "confirmed"}
        assert confirmations == ["acknowledged", "acknowledged"]
        assert owner.request("duplicate-confirm", "desktop_update_confirm_startup", {
            "host": host,
        }) == {"status": "confirmed"}
        assert completions == ["completed"]
        assert owner.request("no-replay", "builder_chat_index") == []
        owner.request("explicit-write", "create_builder_chat", {
            "operation_id": "fixture-explicit", "provider_id": "fixture",
            "model_id": "no-provider-called",
        })
        assert len(owner.request("after-explicit", "builder_chat_index")) == 1
    finally:
        owner.close()
