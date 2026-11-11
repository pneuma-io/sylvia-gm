"""The test observer preserves failures and cannot lose a swallowed violation."""

from pathlib import Path

import pytest

from tests import desktop_update_move_checks as checks
from tests import action_sequence_move_checks as action_checks
from sylvia_gm.application import action_sequences
from sylvia_gm.application.desktop_update import restart, storage
from sylvia_gm.application.desktop_update.manifest import UpdateError


def test_observer_accepts_existing_ticket_trace_and_plain_values(monkeypatch):
    observer = checks.assert_desktop_update_moves.__wrapped__(monkeypatch)
    next(observer)
    row = {}
    for phase in (
        restart.RestartPhase.PENDING,
        restart.RestartPhase.LAUNCHING,
        restart.RestartPhase.LAUNCHED,
        restart.RestartPhase.PENDING,
        restart.RestartPhase.LAUNCHING,
        restart.RestartPhase.LAUNCH_FAILED,
    ):
        restart._move_restart(row, phase)
        assert type(row["phase"]) is str
    with pytest.raises(StopIteration):
        next(observer)


def test_observer_keeps_violation_even_if_a_worker_would_swallow_assertions(monkeypatch):
    observer = checks.assert_desktop_update_moves.__wrapped__(monkeypatch)
    next(observer)
    row = {"phase": restart.RestartPhase.LAUNCHED.value}
    try:
        restart._move_restart(row, restart.RestartPhase.LAUNCHED)
    except AssertionError:
        pass
    # The production helper still writes without enforcing the table.
    assert row["phase"] == "launched"
    with pytest.raises(AssertionError, match="restart ticket"):
        next(observer)


@pytest.mark.parametrize("identity", ["a" * 32, [], {}])
def test_observer_propagates_existing_invalid_input_failure_before_edge_check(
    monkeypatch, identity
):
    def reject(self, *args):
        raise UpdateError("invalid_journal", "Synthetic existing rejection")

    monkeypatch.setattr(storage.UpdateStore, "_move_update", reject)
    observer = checks.assert_desktop_update_moves.__wrapped__(monkeypatch)
    next(observer)
    store = object.__new__(storage.UpdateStore)
    store.root = Path("synthetic-no-io")
    with pytest.raises(UpdateError, match="Synthetic existing rejection"):
        store._move_update(identity, "not-a-phase", None)
    with pytest.raises(StopIteration):
        next(observer)


def test_action_observer_keeps_a_swallowed_illegal_step_move(monkeypatch):
    observer = action_checks.assert_action_sequence_moves.__wrapped__(monkeypatch)
    next(observer)
    row = {"status": action_sequences.StepStatus.COMMITTED.value}
    try:
        action_sequences._move_step(row, action_sequences.StepStatus.PENDING)
    except AssertionError:
        pass
    assert row["status"] == "pending"
    with pytest.raises(AssertionError, match="step"):
        next(observer)


def test_child_violation_survives_abrupt_exit_and_is_checked_by_parent(monkeypatch):
    import os
    import subprocess
    import sys

    from tests import test_desktop_update_crash_recovery

    observer = checks.assert_desktop_update_moves.__wrapped__(monkeypatch)
    next(observer)
    assert test_desktop_update_crash_recovery.CHILD.startswith(
        "from tests.desktop_update_move_checks import install_child_checker"
    )
    program = checks.checked_child_program(
        "import os\n"
        "from sylvia_gm.application.desktop_update import restart\n"
        "restart._move_restart({'phase': 'launched'}, restart.RestartPhase.LAUNCHED)\n"
        "os._exit(73)\n"
    )
    root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join((str(root / "src"), str(root))))
    result = subprocess.run(
        [sys.executable, "-B", "-c", program],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 73, result.stdout + result.stderr
    assert checks._CHILD_VIOLATION_PREFIX in result.stderr
    with pytest.raises(AssertionError, match="subprocess.*restart ticket"):
        next(observer)


def test_fixture_seed_keeps_real_validation_and_later_moves_checked(tmp_path, monkeypatch):
    observer = checks.assert_desktop_update_moves.__wrapped__(monkeypatch)
    seed = next(observer)
    store = storage.UpdateStore(tmp_path / "updates")
    identity, _ = store.create()
    seed(store, identity, storage.UpdatePhase.CANCELLED, "0.3.0-alpha.2")
    seed(store, identity, storage.UpdatePhase.RECOVERY_REQUIRED, "0.3.0-alpha.2")
    assert store.load()["phase"] == "recovery_required"
    with pytest.raises(UpdateError):
        seed(store, identity, storage.UpdatePhase.IDLE, "0.3.0-alpha.2")
    assert store.load()["phase"] == "recovery_required"
    store.save(identity, "available", "0.3.0-alpha.2")
    with pytest.raises(AssertionError, match="UpdateStore"):
        next(observer)
