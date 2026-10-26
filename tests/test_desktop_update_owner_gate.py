"""Restart reservation is independent of turn/action state and never stops existing work."""

import pytest

from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.infrastructure.launch_handshake import derive_owner_response
from sylvia_gm.interfaces.local_owner import LocalOwnerSupervisor

OPERATION = "a" * 32


@pytest.fixture
def supervisor(tmp_path):
    owner = LocalOwnerSupervisor()
    credential = owner.acquire_owner(tmp_path / "authority.sqlite3", "update-fixture")
    owner.complete_handshake(derive_owner_response(credential.secret_hex))
    try:
        yield owner
    finally:
        owner.shutdown()


@pytest.mark.parametrize("kind", ["gm", "builder"])
def test_live_work_rejects_reservation_without_cancellation_or_lease_change(supervisor, kind):
    cancelled = []
    track = supervisor.track_narration_attempt if kind == "gm" else supervisor.track_provider_work
    release = (
        supervisor.release_narration_attempt if kind == "gm" else supervisor.release_provider_work
    )
    track("fixture-campaign", "fixture-attempt", lambda: cancelled.append(True))
    with pytest.raises(CommandRejected) as busy:
        supervisor.begin_desktop_update(OPERATION)
    assert busy.value.code == "update_busy"
    assert cancelled == []
    assert supervisor.desktop_update_operation() is None
    leases = supervisor._leases if kind == "gm" else supervisor._provider_work
    assert ("fixture-campaign", "fixture-attempt") in leases
    release("fixture-campaign", "fixture-attempt")
    supervisor.begin_desktop_update(OPERATION)
    assert supervisor.desktop_update_operation() == OPERATION


def test_stale_abort_cannot_release_another_reservation_and_no_request_is_replayed(supervisor):
    supervisor.begin_desktop_update(OPERATION)
    supervisor.begin_desktop_update(OPERATION)
    with pytest.raises(CommandRejected) as stale:
        supervisor.abort_desktop_update("b" * 32)
    assert stale.value.code == "stale_update_operation"
    assert supervisor.desktop_update_operation() == OPERATION
    with pytest.raises(CommandRejected) as other:
        supervisor.begin_desktop_update("b" * 32)
    assert other.value.code == "update_in_progress"
    supervisor.abort_desktop_update(OPERATION)
    assert supervisor.desktop_update_operation() is None
    assert not supervisor._leases and not supervisor._provider_work
    supervisor.track_provider_work("fixture-campaign", "fresh", lambda: None)
    supervisor.release_provider_work("fixture-campaign", "fresh")


@pytest.mark.parametrize("kind", ["gm", "builder"])
def test_late_work_cannot_overtake_a_reserved_restart(supervisor, kind):
    supervisor.begin_desktop_update(OPERATION)
    cancelled = []
    track = supervisor.track_narration_attempt if kind == "gm" else supervisor.track_provider_work
    with pytest.raises(CommandRejected) as blocked:
        track("fixture-campaign", "late", lambda: cancelled.append(True))
    assert blocked.value.code == "update_in_progress"
    assert not supervisor._leases and not supervisor._provider_work
    assert cancelled == ([True] if kind == "gm" else [])
    assert supervisor.desktop_update_operation() == OPERATION


def test_reserved_owner_rejects_writes_before_dispatch_and_preserves_saved_chat(
    tmp_path, monkeypatch
):
    from sylvia_gm.interfaces import owner_process
    from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness

    captured, dispatched = [], []
    original_launch = owner_process._launch
    original_dispatch = owner_process._dispatch

    def launch(envelope, **kwargs):
        result = original_launch(envelope, **kwargs)
        captured.append(result[0])
        return result

    def dispatch(*args, **kwargs):
        dispatched.append(args[1])
        return original_dispatch(*args, **kwargs)

    monkeypatch.setattr(owner_process, "_launch", launch)
    monkeypatch.setattr(owner_process, "_dispatch", dispatch)
    monkeypatch.setattr(owner_process, "_preinstall_bundled_srd", lambda _bus: None)
    monkeypatch.setattr(owner_process, "_preinstall_bundled_riichi", lambda _bus: None)
    harness = _OwnerHarness(tmp_path / "authority.sqlite3", None)
    try:
        created = harness.request(
            "create",
            "create_builder_chat",
            {
                "operation_id": "saved-update-chat",
                "provider_id": "fixture",
                "model_id": "fixture",
                "display_name": "Saved fictional chat",
            },
        )
        session_id = created["session"]["session_id"]
        before = harness.request("before", "session_context", {"session_id": session_id})
        navigation = harness.request("navigation-before", "builder_chat_index")
        captured[0].begin_desktop_update(OPERATION)
        dispatched.clear()
        for method, params in [
            ("rename_builder_chat", {"session_id": session_id, "display_name": "must not commit"}),
            ("submit_session_input", {"session_id": session_id, "user_text": "must not send"}),
            ("execute_action", {"campaign_id": "fixture-campaign"}),
        ]:
            harness.send(method, {"method": method, "params": params})
            rejected = harness.read()
            assert rejected["ok"] is False
            assert rejected["error"]["code"] == "update_in_progress"
        assert dispatched == []
        assert harness.request("navigation-after", "builder_chat_index") == navigation
        assert not captured[0]._provider_work and not captured[0]._leases
        captured[0].abort_desktop_update(OPERATION)
        after = harness.request("after", "session_context", {"session_id": session_id})
        assert after == before
    finally:
        harness.close()
