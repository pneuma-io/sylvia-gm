"""The owner boundary is fail-closed and never accepts trust, URL or path choices from IPC."""

import threading

import pytest

from sylvia_gm import __version__
from sylvia_gm.application.desktop_update import service
from sylvia_gm.application.desktop_update.manifest import UpdateError

PUBLIC_VECTOR = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")


def host():
    return {"version": __version__, "target": "windows-x86_64", "distribution": "portable"}


def test_recovery_initialization_does_not_block_owner_lane_or_start_a_check(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls, closed = [], []

    class ControlledController:
        def __init__(self, **_kwargs):
            entered.set()
            assert release.wait(5), "controlled recovery was not released"

        def state(self):
            return {"phase": "idle", "busy": False}

        def check(self):
            calls.append("check")
            return self.state()

        def close(self):
            closed.append(True)

        def wait(self):
            pass

    monkeypatch.setattr(service, "PUBLIC_KEYS", {"public-vector-only": PUBLIC_VECTOR})
    monkeypatch.setattr(service, "UpdateController", ControlledController)
    monkeypatch.setattr(service, "HttpsTransport", object)
    updater = service.DesktopUpdateService(tmp_path)
    try:
        state = updater.handle("state", {"host": host()})
        assert state["phase"] == "recovering" and state["busy"] is True
        assert entered.wait(2)
        first = updater._restore_thread
        assert updater.handle("state", {"host": host()})["phase"] == "recovering"
        assert updater._restore_thread is first and calls == []
        release.set()
        first.join(3)
        assert not first.is_alive()
        assert updater.handle("state", {"host": host()})["phase"] == "idle"
        assert calls == []
        updater.handle("check", {"host": host()})
        assert calls == ["check"]
        assert list(tmp_path.iterdir()) == []
    finally:
        release.set()
        updater.close()
        updater._restore_thread.join(3)
    assert closed == [True]


def test_close_during_initialization_closes_result_and_never_checks_automatically(
    tmp_path, monkeypatch
):
    entered, release = threading.Event(), threading.Event()
    closed = []

    class Controller:
        def __init__(self, **_kwargs):
            entered.set()
            assert release.wait(5)

        def close(self):
            closed.append(True)

        def wait(self):
            pass

    monkeypatch.setattr(service, "PUBLIC_KEYS", {"public-vector-only": PUBLIC_VECTOR})
    monkeypatch.setattr(service, "UpdateController", Controller)
    monkeypatch.setattr(service, "HttpsTransport", object)
    updater = service.DesktopUpdateService(tmp_path)
    updater.handle("state", {"host": host()})
    assert entered.wait(2)
    updater.close()
    release.set()
    updater._restore_thread.join(3)
    assert closed == [True]
    with pytest.raises(UpdateError) as blocked:
        updater.handle("check", {"host": host()})
    assert blocked.value.code == "updater_closed"
    assert list(tmp_path.iterdir()) == []


def test_recovery_worker_failure_is_visible_without_an_automatic_retry(tmp_path, monkeypatch):
    calls = []

    def failed_controller(**_kwargs):
        calls.append(True)
        raise UpdateError("invalid_journal", "The stored update needs recovery.")

    monkeypatch.setattr(service, "PUBLIC_KEYS", {"public-vector-only": PUBLIC_VECTOR})
    monkeypatch.setattr(service, "UpdateController", failed_controller)
    monkeypatch.setattr(service, "HttpsTransport", object)
    updater = service.DesktopUpdateService(tmp_path)
    updater.handle("state", {"host": host()})
    updater._restore_thread.join(3)
    state = updater.handle("state", {"host": host()})
    assert state["phase"] == "error" and state["busy"] is False
    assert state["error_code"] == "invalid_journal"
    updater.handle("state", {"host": host()})
    assert calls == [True]
    updater.close()


def test_worker_start_failure_never_leaves_an_infinite_busy_status(tmp_path, monkeypatch):
    starts = []

    class RefusedThread:
        def __init__(self, **_kwargs):
            pass

        def start(self):
            starts.append(True)
            raise RuntimeError("controlled thread-start failure")

        def is_alive(self):
            return False

    monkeypatch.setattr(service, "PUBLIC_KEYS", {"public-vector-only": PUBLIC_VECTOR})
    monkeypatch.setattr(service.threading, "Thread", RefusedThread)
    updater = service.DesktopUpdateService(tmp_path)
    state = updater.handle("state", {"host": host()})
    assert state["phase"] == "error" and state["busy"] is False
    assert state["error_code"] == "worker_unavailable"
    updater.handle("state", {"host": host()})
    assert starts == [True]
    updater.close()


def test_unconfigured_trust_has_no_network_or_staging_side_effect(tmp_path, monkeypatch):
    def no_transport():
        raise AssertionError("Missing trust must fail before creating transport")

    monkeypatch.setattr(service, "HttpsTransport", no_transport)
    updater = service.DesktopUpdateService(tmp_path)
    result = updater.handle("state", {"host": host()})
    assert result["error_code"] == "trust_not_configured"
    assert result["can_apply"] is False
    assert result["distribution"] == "portable"
    with pytest.raises(UpdateError) as error:
        updater.handle("check", {"host": host()})
    assert error.value.code == "trust_not_configured"
    assert list(tmp_path.iterdir()) == []
    updater.close()


@pytest.mark.parametrize("extra", ["url", "path", "command", "public_key", "verify", "force"])
def test_renderer_cannot_choose_update_authority_or_execution_inputs(tmp_path, extra):
    updater = service.DesktopUpdateService(tmp_path)
    with pytest.raises(UpdateError) as error:
        updater.handle("check", {"host": host(), extra: "untrusted"})
    assert error.value.code == "invalid_update_request"
    assert list(tmp_path.iterdir()) == []


def test_mismatched_or_changed_native_context_cannot_retarget_a_download(tmp_path):
    updater = service.DesktopUpdateService(tmp_path)
    altered = {**host(), "version": "100.0.0"}
    with pytest.raises(UpdateError) as error:
        updater.handle("state", {"host": altered})
    assert error.value.code == "wrong_component_version"
    updater.handle("state", {"host": host()})
    with pytest.raises(UpdateError) as changed:
        updater.handle("state", {"host": {**host(), "distribution": "installed"}})
    assert changed.value.code == "changed_update_context"


def test_real_owner_routes_update_status_without_loading_provider_or_creating_updater_files(
    tmp_path, monkeypatch
):
    from sylvia_gm.interfaces import owner_process
    from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness

    # This test qualifies protocol wiring, not package admission or Lite content.
    monkeypatch.setattr(owner_process, "_preinstall_bundled_srd", lambda _bus: None)
    monkeypatch.setattr(owner_process, "_preinstall_bundled_riichi", lambda _bus: None)
    harness = _OwnerHarness(tmp_path / "authority.sqlite3", None)
    try:
        result = harness.request("update-status", "desktop_update_state", {"host": host()})
        assert result["error_code"] == "trust_not_configured"
        assert not (tmp_path / ".application-updates").exists()
        harness.send(
            "invalid-update",
            {"method": "desktop_update_check", "params": {"host": host(), "url": "untrusted"}},
        )
        response = harness.read()
        assert response["ok"] is False
        assert response["error"]["code"] == "invalid_update_request"
    finally:
        harness.close()


def test_unusable_update_launch_context_cannot_break_ordinary_owner_startup(tmp_path, monkeypatch):
    from sylvia_gm.interfaces import owner_process
    from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness

    monkeypatch.setattr(owner_process, "_preinstall_bundled_srd", lambda _bus: None)
    monkeypatch.setattr(owner_process, "_preinstall_bundled_riichi", lambda _bus: None)

    class InvalidContextHarness(_OwnerHarness):
        def send(self, request_id, payload, *, envelope=False):
            if envelope:
                payload = {**payload, "desktop_update_context": {"unsupported": "location"}}
            super().send(request_id, payload, envelope=envelope)

    owner = InvalidContextHarness(tmp_path / "authority.sqlite3", None)
    try:
        assert owner.request("still-playable", "campaign_index") == []
        state = owner.request("update-disabled", "desktop_update_state", {"host": host()})
        assert state["can_apply"] is False and state["error_code"] == "trust_not_configured"
        assert not (tmp_path / ".application-updates").exists()
    finally:
        owner.close()


def test_late_boot_recovery_projection_cannot_overwrite_completed_startup(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    created, closed = [], []

    class ControlledController:
        def __init__(self, **_kwargs):
            self.first = not created
            created.append(self)
            if self.first:
                entered.set()
                assert release.wait(5), "release controlled boot recovery"

        def state(self):
            return {"phase": "recovery_required" if self.first else "confirmed", "busy": False}

        def close(self):
            closed.append(self)

        def wait(self):
            pass

    monkeypatch.setattr(service, "PUBLIC_KEYS", {"public-vector-only": PUBLIC_VECTOR})
    monkeypatch.setattr(service, "UpdateController", ControlledController)
    boundary = service.DesktopUpdateService(tmp_path)
    try:
        boundary.handle("state", {"host": host()})
        first = boundary._restore_thread
        assert entered.wait(3)
        boundary.startup_completed()
        release.set()
        first.join(3)
        assert boundary._controller is None and closed == [created[0]]
        boundary.handle("state", {"host": host()})
        boundary._restore_thread.join(3)
        assert boundary.handle("state", {"host": host()})["phase"] == "confirmed"
        assert len(created) == 2
    finally:
        release.set()
        boundary.close()
