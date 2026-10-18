"""Observe the actual failed cast without changing its mechanics or assertion."""
import json

from sylvia_gm.application import package_runtime
from sylvia_gm.runtime import _tool_executor
from sylvia_gm.runtime._types import ToolResult
from tests.test_srd_lite_healing import healing_case
from tests.test_srd_lite_false_life import test_fixed_pool_does_not_roll_or_stack_and_retries_persist as _original_failed_case


def test_actual_false_life_rejection_diagnostic(healing_case, monkeypatch):
    actual = package_runtime.execute_tool
    prepare = _tool_executor.prepare_tool_context

    def observed(*args, **kwargs):
        result = actual(*args, **kwargs)
        if not result.success:
            print(json.dumps({'actual_tool': result.tool_name, 'error': result.error,
                              'failure_kind': result.failure_kind}), flush=True)
        return result

    def observed_prepare(*args, **kwargs):
        result = prepare(*args, **kwargs)
        if isinstance(result, ToolResult) and not result.success:
            print(json.dumps({'prepared_tool': result.tool_name, 'error': result.error,
                              'failure_kind': result.failure_kind, 'parameters': args[1]}), flush=True)
        return result

    monkeypatch.setattr(package_runtime, 'execute_tool', observed)
    monkeypatch.setattr(_tool_executor, 'prepare_tool_context', observed_prepare)
    _original_failed_case(healing_case, monkeypatch, 1, 0, 7)
