"""Reserve/checkpoint/defer composition; inert artifacts use an explicit protocol verifier stub."""

import threading
from types import SimpleNamespace

import pytest

from sylvia_gm import __version__
from sylvia_gm.application.desktop_update import service
from sylvia_gm.application.desktop_update.controller import UpdateController
from sylvia_gm.application.desktop_update.launch import Installation
from sylvia_gm.application.desktop_update.manifest import UpdateError, Version
from tests.test_desktop_update_lifecycle import ReleaseFixture
from tests.test_desktop_update_manifest import accept_stub
from tests.test_desktop_update_preparation import fixture


def setup(tmp_path):
    update, _, backup, _, _, _ = fixture(tmp_path)
    context = {
        "live": str(update.live), "data_root": str(update.root), "version": __version__,
        "target": "windows-x86_64", "distribution": "portable",
    }
    installation = Installation.from_launch(context, update.root)
    controller = UpdateController(
        root=update.root / ".application-updates", current=Version.parse(__version__),
        target="windows-x86_64", distribution="portable", transport=ReleaseFixture(),
        verify=accept_stub,
    )
    reservations, released = {update.identity}, []

    def release(identity):
        assert identity in reservations
        reservations.remove(identity)
        released.append(identity)

    boundary = service.DesktopUpdateService(
        update.root, installation=installation, backup_database=backup,
        reserved=lambda identity: identity in reservations, release=release,
    )
    boundary._controller = controller
    return boundary, controller, update, reservations, released


def prepare(boundary, controller, identity):
    return boundary._prepare(
        controller, identity, SimpleNamespace(verify=accept_stub),
        "windows-x86_64", "portable",
    )


def test_async_prepare_retains_checkpoint_and_deferral_releases_only_matching_reservation(tmp_path):
    boundary, controller, update, reservations, released = setup(tmp_path)
    try:
        result = prepare(boundary, controller, update.identity)
        assert result["phase"] == "preparing"
        controller.wait()
        state = controller.state()
        assert state["phase"] == "prepared" and state["prepared_attempt"] is not None
        assert reservations == {update.identity} and released == []
        attempt = update.base / "attempts" / state["prepared_attempt"]
        assert (attempt / "data-checkpoint/authority.sqlite3").is_file()
        assert (attempt / "helper/sylvia-gm-owner.exe").is_file()
        boundary.abort_preparation(update.identity)
        boundary._release_if_reserved(update.identity)
        assert released == [update.identity] and reservations == set()
        assert controller.state()["phase"] == "deferred"
        assert (attempt / "data-checkpoint/authority.sqlite3").is_file()
    finally:
        boundary.close()


def test_checkpoint_failure_releases_reservation_and_retry_takes_fresh_attempt(tmp_path):
    boundary, controller, update, reservations, released = setup(tmp_path)
    original = boundary._backup_database

    def fail(_target):
        raise OSError("controlled checkpoint failure")

    boundary._backup_database = fail
    try:
        prepare(boundary, controller, update.identity)
        controller.wait()
        assert controller.state()["phase"] == "error"
        assert released == [update.identity] and reservations == set()
        attempts = set((update.base / "attempts").iterdir())
        assert len(attempts) == 1
        with pytest.raises(UpdateError) as blocked:
            prepare(boundary, controller, update.identity)
        assert blocked.value.code == "update_busy"
        reservations.add(update.identity)
        boundary._backup_database = original
        prepare(boundary, controller, update.identity)
        controller.wait()
        assert controller.state()["phase"] == "prepared"
        assert len(list((update.base / "attempts").iterdir())) == 2
    finally:
        boundary.close()


def test_abort_and_shutdown_cannot_close_database_during_admitted_checkpoint(tmp_path):
    boundary, controller, update, reservations, released = setup(tmp_path)
    entered, resume = threading.Event(), threading.Event()
    original = boundary._backup_database

    def held(target):
        entered.set()
        assert resume.wait(5), "release controlled backup"
        original(target)

    boundary._backup_database = held
    closing = threading.Thread(target=boundary.close)
    try:
        prepare(boundary, controller, update.identity)
        assert entered.wait(3)
        with pytest.raises(UpdateError) as blocked:
            boundary.abort_preparation(update.identity)
        assert blocked.value.code == "update_busy" and reservations == {update.identity}
        closing.start()
        closing.join(0.05)
        assert closing.is_alive(), "owner close must wait before closing its database"
        resume.set()
        closing.join(5)
        assert not closing.is_alive()
        assert controller.state()["phase"] == "cancelled"
        assert controller.state()["error_code"] == "application_busy"
        assert released == [update.identity]
    finally:
        resume.set()
        if closing.ident is not None:
            closing.join(5)
        boundary.close()


def test_source_launch_and_changed_native_context_never_acquire_install_capability(tmp_path):
    assert Installation.from_launch(None, tmp_path) is None
    boundary, controller, update, _, _ = setup(tmp_path)
    try:
        with pytest.raises(UpdateError) as wrong:
            boundary._installation.check("windows-x86_64", "installed")
        assert wrong.value.code == "changed_update_context"
        boundary._installation = None
        with pytest.raises(UpdateError) as missing:
            prepare(boundary, controller, update.identity)
        assert missing.value.code == "unsupported_launch_context"
        assert not (update.base / "attempts").exists()
    finally:
        boundary.close()


def test_handoff_returns_only_bound_helper_identity_and_rechecks_retained_bytes(tmp_path):
    boundary, controller, update, _, _ = setup(tmp_path)
    try:
        prepare(boundary, controller, update.identity)
        controller.wait()
        metadata = boundary._handoff(
            controller, update.identity, SimpleNamespace(verify=accept_stub),
            "windows-x86_64", "portable",
        )
        assert set(metadata) == {"operation_id", "attempt", "helper"}
        assert metadata["operation_id"] == update.identity
        assert set(metadata["helper"]) == {"size", "sha256"}
        helper = update.base / "attempts" / metadata["attempt"] / "helper/sylvia-gm-owner.exe"
        helper.write_bytes(b"changed retained helper")
        with pytest.raises(UpdateError) as changed:
            boundary._handoff(
                controller, update.identity, SimpleNamespace(verify=accept_stub),
                "windows-x86_64", "portable",
            )
        assert changed.value.code == "changed_helper"
    finally:
        boundary.close()


def test_native_shutdown_is_armed_before_owner_release_and_can_only_be_armed_once(
    tmp_path, monkeypatch,
):
    boundary, controller, update, reservations, _ = setup(tmp_path)
    monkeypatch.setattr(service, "KeyRing", lambda _keys: SimpleNamespace(verify=accept_stub))
    try:
        prepare(boundary, controller, update.identity)
        controller.wait()
        boundary.arm_shutdown(update.identity)
        assert update.store.load()["phase"] == "closing"
        assert reservations == {update.identity}
        with pytest.raises(UpdateError):
            boundary.arm_shutdown(update.identity)
        assert update.store.load()["phase"] == "closing"
    finally:
        boundary.close()
