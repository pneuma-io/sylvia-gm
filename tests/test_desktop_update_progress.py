"""Status-window and helper-order tests replace Win32 calls; no native window is opened."""

import ctypes
import io
import json
import threading
from types import SimpleNamespace

import pytest

from sylvia_gm.application.desktop_update import helper, progress
from sylvia_gm.application.desktop_update.checkpoint import acquire_update_data_lock
from sylvia_gm.application.desktop_update.manifest import UpdateError
from tests.test_desktop_update_manifest import accept_stub
from tests.test_desktop_update_preparation import fixture


def test_status_thread_closes_normally_after_success_and_has_no_percentage():
    observed = []
    changed = threading.Event()

    def runner(state):
        state.created()
        while True:
            snapshot = state.snapshot()
            observed.append(snapshot)
            if snapshot[0] == progress.STAGES["applying"]:
                changed.set()
            if snapshot[2]:
                return
            state.done.wait(0.001)

    window = progress.ProgressWindow(runner).start()
    window.stage("applying")
    assert changed.wait(2)
    window.close()
    assert window.done.is_set()
    assert all("%" not in row[0] for row in observed)


def test_error_remains_until_owned_dialog_acknowledges_and_never_displays_raw_errors():
    observed, release = threading.Event(), threading.Event()
    messages = []

    def runner(state):
        state.created()
        while True:
            text, failed, _ = state.snapshot()
            if failed:
                messages.append(text)
                observed.set()
                release.wait()
                return
            state.done.wait(0.001)

    window = progress.ProgressWindow(runner).start()
    failed = threading.Thread(target=window.fail, args=("private-machine-path-or-error",))
    failed.start()
    try:
        assert observed.wait(2)
        assert failed.is_alive()
        assert "private-machine-path" not in messages[0]
        assert "recovery files are retained" in messages[0]
    finally:
        release.set()
        failed.join()
    window.close()


def test_failed_dialog_creation_refuses_readiness_and_lost_window_refuses_later_stages():
    with pytest.raises(UpdateError) as failure:
        progress.ProgressWindow(lambda _: None).start()
    assert failure.value.code == "progress_unavailable"
    state = progress.ProgressWindow(lambda _: None)
    state.done.set()
    with pytest.raises(UpdateError) as lost:
        state.stage("applying")
    assert lost.value.code == "progress_lost"


@pytest.mark.skipif(not hasattr(ctypes, "WINFUNCTYPE"), reason="Windows callback ABI")
def test_task_dialog_packing_callback_timer_and_own_window_close_rules_without_native_calls(
    monkeypatch,
):
    config_type, button_type, _ = progress._dialog_types()
    assert ctypes.sizeof(ctypes.c_void_p) == 8
    assert ctypes.sizeof(config_type) == 160 and ctypes.sizeof(button_type) == 12
    messages, texts = [], []
    state = progress.ProgressWindow(lambda _: None)

    class ApiCall:
        def __init__(self, fn):
            self.fn = fn

        def __call__(self, *args):
            return self.fn(*args)

    def send(hwnd, message, first, second):
        assert hwnd == 12345
        messages.append((message, first, second))
        if message == 1132:
            texts.append(ctypes.wstring_at(second))
        return 0

    def task_dialog(pointer, *_):
        config = pointer._obj
        assert config.flags == 0x8C00
        assert not config.flags & 8  # No unrequested dialog cancellation.
        assert config.buttons[0].identity == 1000
        callback = config.callback
        callback(12345, 0, 0, 0, 0)
        assert callback(12345, 2, 1000, 0, 0) == 1
        state.stage("applying")
        callback(12345, 4, 0, 0, 0)
        assert texts[-1] == progress.STAGES["applying"]
        state._failure = "disk_full"
        callback(12345, 4, 0, 0, 0)
        assert "not enough free space" in texts[-1]
        assert callback(12345, 2, 1000, 0, 0) == 0
        callback(12345, 5, 0, 0, 0)
        count = len(messages)
        callback(12345, 4, 0, 0, 0)
        assert len(messages) == count  # Never send to a destroyed handle.
        return 0

    controls = SimpleNamespace(TaskDialogIndirect=ApiCall(task_dialog))
    user = SimpleNamespace(SendMessageW=ApiCall(send))
    monkeypatch.setattr(
        ctypes, "WinDLL", lambda name, **_: controls if name == "comctl32" else user,
    )
    progress._windows_dialog(state)


def main_fixture(tmp_path, monkeypatch, factory, execute):
    update, _, backup, _, _, database = fixture(tmp_path)
    update.prepare(backup, lambda: True)
    events = []
    monkeypatch.setattr(helper, "KeyRing", lambda _: SimpleNamespace(keys={"stub": b"public"},
                                                                    verify=accept_stub))
    monkeypatch.setattr(helper, "WindowsHandles", lambda: None)
    monkeypatch.setattr(helper, "OwnedProcesses", lambda *_: SimpleNamespace(
        all_exited=lambda: True, close=lambda: events.append("handles-closed"),
    ))
    monkeypatch.setattr(helper, "_apply_and_restart_locked", execute)
    request = {
        "type": "desktop_update_apply", "operation_id": update.identity,
        "attempt": update.attempt, "data_root": str(update.root), "live": str(update.live),
        "distribution": "portable", "processes": [
            {"role": "desktop", "handle": 101, "pid": 10, "creation_time": 1},
            {"role": "owner", "handle": 102, "pid": 11, "creation_time": 2},
        ],
    }
    output = io.StringIO()

    class Input:
        first = True

        def readline(self, _limit):
            if self.first:
                self.first = False
                return (json.dumps(request) + "\n").encode()
            ready = json.loads(output.getvalue().splitlines()[-1])
            return (json.dumps({"action": "commit", "operation_id": update.identity,
                               "attempt": update.attempt, "helper_nonce": ready["helper_nonce"]})
                    + "\n").encode()

    before = database.read_bytes()
    result = helper.main(Input(), output, presentation_factory=lambda: factory(update, events))
    assert database.read_bytes() == before
    return update, result, [json.loads(row) for row in output.getvalue().splitlines()], events


def test_missing_presentation_never_acknowledges_readiness_or_calls_apply(tmp_path, monkeypatch):
    def unavailable(*_):
        raise UpdateError("progress_unavailable", "No status window")

    _, result, rows, events = main_fixture(
        tmp_path, monkeypatch, unavailable, lambda *_args, **_kwargs: pytest.fail("must not apply"),
    )
    assert result == 2 and rows == [{"type": "error", "code": "progress_unavailable"}]
    assert events == ["handles-closed"]


def test_post_commit_error_is_durable_and_releases_helper_ownership_before_error_ui(
    tmp_path, monkeypatch,
):
    def factory(update, events):
        class Presentation:
            def stage(self, phase):
                events.append(phase)

            def fail(self, code):
                assert (update.stage / "helper-error.json").is_file()
                assert events[-1] == "handles-closed"
                lock = acquire_update_data_lock(update.stage / "helper-runtime")
                lock.close()
                events.append("visible-error:" + code)

            def close(self):
                events.append("dialog-closed")

        return Presentation()

    def execute(_prepared, _processes, *, notify):
        notify("applying")
        raise UpdateError("disk_full", "not exported native details")

    _, result, rows, events = main_fixture(tmp_path, monkeypatch, factory, execute)
    assert result == 2 and rows[0]["type"] == "ready" and rows[-1]["code"] == "disk_full"
    assert events == ["applying", "handles-closed", "visible-error:disk_full", "dialog-closed"]
