"""Updater state naming preserves saved values and keeps move checks in tests."""

import ast
from pathlib import Path

from sylvia_gm.application.desktop_update import storage
from tests.desktop_update_move_checks import assert_desktop_update_moves as assert_desktop_update_moves


def test_persistent_phase_set_keeps_exact_twenty_values_and_excludes_idle():
    expected = frozenset({
        "checking", "available", "current", "downloading", "verified", "deferred",
        "cancelled", "interrupted", "error", "preparing", "prepared", "closing",
        "applying", "awaiting_restart", "recovery_required", "rolling_back",
        "rollback_ready", "rollback_awaiting_restart", "confirmed", "rolled_back",
    })
    assert storage.PHASES == expected
    assert all(isinstance(phase, storage.UpdatePhase) for phase in storage.PHASES)
    assert storage.UpdatePhase.IDLE not in storage.PHASES
    assert storage.EMPTY_TERMINAL_PHASES == {"current", "error", "cancelled", "interrupted"}


def test_updater_phase_writes_have_one_owner_and_tables_are_not_runtime_guards():
    writers = {
        ("checkpoint.py", "_move_restore"),
        ("controller.py", "UpdateController._move_phase"),
        ("installed.py", "InstalledReplacement._move_phase"),
        ("progress.py", "ProgressWindow._move_phase"),
        ("replacement.py", "PortableReplacement._move_phase"),
        ("restart.py", "_move_restart"),
        ("storage.py", "UpdateStore._move_update"),
    }
    projections = {
        ("controller.py", "UpdateController.state"),
        ("recovery.py", "RecoveryOperation.inspect"),
        ("restart.py", "RestartHandshake.confirm"),  # A separate startup acknowledgement.
        ("service.py", "DesktopUpdateService.handle"),
    }

    def field(node):
        if isinstance(node, ast.Name):
            return node.id.lstrip("_")
        if isinstance(node, ast.Attribute):
            return node.attr.lstrip("_")
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            return node.slice.value
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute) and node.func.attr == "get" and node.args:
                return node.args[0].value if isinstance(node.args[0], ast.Constant) else None
            if isinstance(node.func, ast.Name) and node.func.id == "str" and node.args:
                return field(node.args[0])
            return field(node.func)
        return None

    for path in Path(storage.__file__).parent.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}

        def owner(node):
            names = []
            while node in parents:
                node = parents[node]
                if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
                    names.insert(0, node.name)
            return path.name, ".".join(names)

        for node in ast.walk(tree):
            if (isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store)
                    and node.attr == "_phase"):
                assert owner(node) in writers
            if (isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store)
                    and isinstance(node.slice, ast.Constant) and node.slice.value == "phase"):
                assert owner(node) in writers
            if isinstance(node, ast.Dict):
                for key in node.keys:
                    if isinstance(key, ast.Constant) and key.value == "phase":
                        assert owner(node) in writers | projections
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id.endswith("_MOVES"):
                raise AssertionError(f"Production reads move table at {path.name}:{node.lineno}")
            if isinstance(node, ast.Compare):
                operands = [node.left, *node.comparators]
                if any(field(value) == "phase" for value in operands):
                    for value in operands:
                        if field(value) != "phase":
                            assert not any(isinstance(part, ast.Constant) and isinstance(part.value, str)
                                           for part in ast.walk(value)), (path.name, node.lineno)
