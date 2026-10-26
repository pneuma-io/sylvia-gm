"""Owner-pipe restart admission includes pre-lane workers and projections, not just leases."""

import threading
import time

import pytest

from sylvia_gm.interfaces import owner_process
from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness

OPERATION = "a" * 32


@pytest.fixture
def owner(tmp_path, monkeypatch):
    monkeypatch.setattr(owner_process, "_preinstall_bundled_srd", lambda _bus: None)
    monkeypatch.setattr(owner_process, "_preinstall_bundled_riichi", lambda _bus: None)
    harness = _OwnerHarness(tmp_path / "authority.sqlite3", None)
    try:
        yield harness
    finally:
        harness.close()


def request_error(owner, request_id, method, params):
    owner.send(request_id, {"method": method, "params": params})
    reply = owner.read()
    assert reply["id"] == request_id and reply["ok"] is False
    return reply["error"]["code"]


def test_reserve_abort_and_stale_shutdown_leave_owner_and_data_usable(owner):
    assert (
        request_error(
            owner, "no-reservation", "desktop_update_shutdown", {"operation_id": OPERATION}
        )
        == "stale_update_operation"
    )
    assert owner.request("reserve", "desktop_update_reserve", {"operation_id": OPERATION}) == {
        "status": "reserved"
    }
    assert owner.request("duplicate", "desktop_update_reserve", {"operation_id": OPERATION}) == {
        "status": "reserved"
    }
    assert (
        request_error(owner, "stale", "desktop_update_abort", {"operation_id": "b" * 32})
        == "stale_update_operation"
    )
    assert request_error(owner, "write", "create_builder_chat", {}) == "update_in_progress"
    assert owner.request("abort", "desktop_update_abort", {"operation_id": OPERATION}) == {
        "status": "aborted"
    }
    assert owner.request("still-usable", "builder_chat_index") == []
    assert owner.request("unchanged", "campaign_index") == []


def test_worker_admitted_before_lease_publication_cannot_be_overtaken(owner, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = threading.Thread

    class DelayedWorker(original):
        def run(self):
            if self.name.startswith("sylvia-gm-session-"):
                entered.set()
                assert release.wait(5), "release controlled pre-lane worker"
            super().run()

    monkeypatch.setattr(owner_process.threading, "Thread", DelayedWorker)
    owner.send("earlier", {"method": "customize_ready_campaign", "params": {}})
    try:
        assert entered.wait(3)
        assert (
            request_error(owner, "must-wait", "desktop_update_reserve", {"operation_id": OPERATION})
            == "update_busy"
        )
    finally:
        release.set()
    assert owner.read()["id"] == "earlier"
    assert owner.request("after-worker", "desktop_update_reserve", {"operation_id": OPERATION}) == {
        "status": "reserved"
    }
    owner.request("abort-worker-check", "desktop_update_abort", {"operation_id": OPERATION})


def test_admitted_projection_must_finish_before_restart_reservation(owner, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    readers = []
    original_dispatch = owner_process._dispatch
    original_idle = owner_process.DisplayReads.idle

    def idle(reader):
        readers.append(reader)
        return original_idle(reader)

    def dispatch(*args, **kwargs):
        if args[1] == "player_projection":
            entered.set()
            assert release.wait(5), "release controlled projection"
            return {"projection": "read only"}
        return original_dispatch(*args, **kwargs)

    monkeypatch.setattr(owner_process, "_dispatch", dispatch)
    monkeypatch.setattr(owner_process.DisplayReads, "idle", idle)
    owner.send(
        "projection",
        {"method": "player_projection", "params": {"campaign_id": "fictional-campaign"}},
    )
    try:
        assert entered.wait(3)
        assert (
            request_error(
                owner, "wait-for-display", "desktop_update_reserve", {"operation_id": OPERATION}
            )
            == "update_busy"
        )
    finally:
        release.set()
    assert owner.read()["id"] == "projection"
    deadline = time.monotonic() + 3
    while not original_idle(readers[-1]) and time.monotonic() < deadline:
        time.sleep(0.001)
    assert original_idle(readers[-1])
    assert owner.request(
        "after-display", "desktop_update_reserve", {"operation_id": OPERATION}
    ) == {"status": "reserved"}
    owner.request("abort-display-check", "desktop_update_abort", {"operation_id": OPERATION})


def test_reserved_shutdown_exits_normally_without_model_or_mechanics_replay(owner):
    owner.request("reserve-close", "desktop_update_reserve", {"operation_id": OPERATION})
    assert owner.request(
        "close-update", "desktop_update_shutdown", {"operation_id": OPERATION}
    ) == {"status": "closed"}
    owner._thread.join(5)
    assert not owner._thread.is_alive()
