"""Check updater moves in tests without adding I/O or rejection to production."""

import json
import subprocess
import sys
import threading

import pytest

from sylvia_gm.application.desktop_update import (
    checkpoint,
    controller,
    installed,
    progress,
    replacement,
    restart,
    storage,
)


_CHILD_VIOLATION_PREFIX = "SYLVIA_TEST_UPDATER_MOVE_VIOLATION "
_CHILD_VIOLATION_SINK = None


class _MoveViolations(list):
    def append(self, violation):
        super().append(violation)
        if _CHILD_VIOLATION_SINK is not None:
            _CHILD_VIOLATION_SINK(violation)


def checked_child_program(program):
    """Install the same test observer before the crash child's unchanged program."""
    bootstrap = (
        "from tests.desktop_update_move_checks import install_child_checker\n"
        "_sylvia_move_guard = install_child_checker()\n"
    )
    return program if program.startswith(bootstrap) else bootstrap + program


def install_child_checker():
    """Report violations before os._exit can bypass fixture teardown."""
    global _CHILD_VIOLATION_SINK
    _CHILD_VIOLATION_SINK = lambda violation: print(
        _CHILD_VIOLATION_PREFIX + json.dumps(violation),
        file=sys.stderr,
        flush=True,
    )
    patches = pytest.MonkeyPatch()
    observer = assert_desktop_update_moves.__wrapped__(patches)
    next(observer)
    return observer, patches


@pytest.fixture(autouse=True)
def assert_desktop_update_moves(monkeypatch):
    known = {}
    violations = _MoveViolations()
    lock = threading.Lock()
    update_writer = storage.UpdateStore._move_update

    def remember(key, phase):
        with lock:
            known[key] = phase

    def previous(key):
        with lock:
            return known.get(key)

    def check(kind, old, new, moves, initial):
        allowed = initial if old is None else moves.get(old, ())
        if new not in allowed:
            with lock:
                violations.append((kind, old, new))

    def journal(cls, writer_name, reader_name, moves, phases, key_of, phase_of):
        writer = getattr(cls, writer_name)
        reader = getattr(cls, reader_name)

        def read(self, *args, **kwargs):
            result = reader(self, *args, **kwargs)
            if result is not None:
                remember(key_of(self, result), phase_of(result))
            return result

        def write(self, *args, **kwargs):
            # The controlled crash can occur inside publication. Check its valid
            # replacement intent before that exit; ordinary validation stays first
            # in the parent observer and for every other writer.
            crash_intent = (
                _CHILD_VIOLATION_SINK is not None and cls is replacement.PortableReplacement
            )
            if crash_intent:
                key = key_of(self, args)
                check(cls.__name__, previous(key), args[0], moves, phases)
            # Existing validation and failures happen before checking a successful write.
            result = writer(self, *args, **kwargs)
            key = key_of(self, args)
            old = previous(key)
            phase = args[1] if cls is storage.UpdateStore else args[0]
            if not crash_intent:
                check(cls.__name__, old, phase, moves, phases)
            remember(key, phase)
            return result

        monkeypatch.setattr(cls, reader_name, read)
        monkeypatch.setattr(cls, writer_name, write)

    def update_key(self, value):
        identity = value["operation_id"] if isinstance(value, dict) else value[0]
        return "update", str(self.root), identity

    journal(
        storage.UpdateStore,
        "_move_update",
        "load",
        storage.UPDATE_MOVES,
        storage.UPDATE_INITIAL_PHASES,
        update_key,
        lambda row: row["phase"],
    )
    journal(
        replacement.PortableReplacement,
        "_move_phase",
        "_restore_receipt",
        replacement.REPLACEMENT_MOVES,
        frozenset(replacement.ReplacementPhase),
        lambda self, value: ("replacement", str(self._journal)),
        lambda phase: phase,
    )
    journal(
        installed.InstalledReplacement,
        "_move_phase",
        "_phase",
        installed.INSTALLED_MOVES,
        frozenset(installed.InstalledPhase),
        lambda self, value: ("installed", str(self._journal)),
        lambda phase: phase,
    )

    def memory(cls, moves, initial, kind):
        writer = cls._move_phase

        def move(self, phase):
            old = getattr(self, "_phase", None)
            result = writer(self, phase)
            new = self._phase
            check(kind, old, new, moves, initial)
            if type(new) is not str:
                violations.append((kind, "non-string wire value", type(new).__name__))
            if cls is controller.UpdateController and old == storage.UpdatePhase.IDLE:
                root = getattr(self.store, "root", None)
                if root is not None:
                    key = ("update", str(root), self._identity)
                    saved = previous(key)
                    if saved is not None:
                        check("restored update", saved, new, moves, initial)
            return result

        monkeypatch.setattr(cls, "_move_phase", move)

    memory(
        controller.UpdateController,
        storage.UPDATE_MOVES,
        frozenset({storage.UpdatePhase.IDLE}),
        "controller",
    )
    memory(
        progress.ProgressWindow,
        progress.PROGRESS_MOVES,
        frozenset({progress.ProgressPhase.WAITING}),
        "progress",
    )

    def dictionary(module, helper_name, moves, initial, kind):
        writer = getattr(module, helper_name)

        def move(row, new):
            old = row.get("phase")
            result = writer(row, new)
            check(kind, old, row["phase"], moves, initial)
            if type(row["phase"]) is not str:
                violations.append((kind, "non-string wire value", type(row["phase"]).__name__))
            return result

        monkeypatch.setattr(module, helper_name, move)

    dictionary(
        restart,
        "_move_restart",
        restart.RESTART_MOVES,
        frozenset({restart.RestartPhase.PENDING}),
        "restart ticket",
    )
    dictionary(
        checkpoint,
        "_move_restore",
        checkpoint.RESTORE_MOVES,
        frozenset({checkpoint.RestorePhase.RESTORING}),
        "data restore",
    )
    crash_modules = [
        sys.modules[name]
        for name in (
            "tests.test_desktop_update_crash_recovery",
            "test_desktop_update_crash_recovery",
        )
        if name in sys.modules
    ]
    if crash_modules and _CHILD_VIOLATION_SINK is None:
        for crash_module in crash_modules:
            monkeypatch.setattr(crash_module, "CHILD", checked_child_program(crash_module.CHILD))
        communicate = subprocess.Popen.communicate

        def collect_child_violations(self, *args, **kwargs):
            output, diagnostic = communicate(self, *args, **kwargs)
            text = (
                diagnostic.decode(errors="replace")
                if isinstance(diagnostic, bytes)
                else diagnostic or ""
            )
            for line in text.splitlines():
                if line.startswith(_CHILD_VIOLATION_PREFIX):
                    violation = json.loads(line.removeprefix(_CHILD_VIOLATION_PREFIX))
                    violations.append(("subprocess", *violation))
            return output, diagnostic

        monkeypatch.setattr(subprocess.Popen, "communicate", collect_child_violations)

    def seed_phase(store, identity, phase, version, error_code=None):
        """Create a test's persisted starting condition through the real validated writer."""
        result = update_writer(store, identity, phase, version, error_code)
        value = phase.value if isinstance(phase, storage.UpdatePhase) else phase
        remember(("update", str(store.root), identity), value)
        return result

    yield seed_phase
    # Worker exception handlers may consume assertions. Checking here keeps every
    # observed violation visible while preserving the original test's failure flow.
    assert not violations, violations


@pytest.fixture
def seed_update_phase(assert_desktop_update_moves):
    """Explicit fixture setup; subsequent production moves remain observed."""
    return assert_desktop_update_moves
