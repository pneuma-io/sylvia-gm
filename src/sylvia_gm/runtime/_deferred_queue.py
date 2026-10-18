"""Deferred (delayed / scheduled) effect execution.

DeferredQueue holds DeferredCall objects — serializable descriptions
of tool calls that should be executed later (end of turn, end of round,
or when explicitly drained).

The queue is stored in ctx.extras["deferred_queue"] and drained via
the drain() method, which receives a fresh tool_executor at drain time
(no stale closures).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DeferredCall:
    """A serializable deferred tool call.

    Attributes:
        call_id: Unique identifier for this deferred call.
        tool_name: Name of the tool to execute.
        params: Parameters for the tool.
        reason: Human-readable description of why this was deferred.
        priority: Integer priority (lower = executes first).
        context_vars: Context variables to inject before execution.
    """

    call_id: str
    tool_name: str
    params: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    priority: int = 100
    context_vars: dict[str, Any] = field(default_factory=dict)


class DeferredQueue:
    """Stores deferred tool calls for later execution.

    Stored in ctx.extras["deferred_queue"].  Calls are drained at
    specific points (end-of-turn, end-of-round, combat-end) by calling
    drain() with the current tool_executor.

    Usage::

        queue = DeferredQueue()
        queue.enqueue("fx_001", "lair_action", {"target": "hero"},
                       reason="Lair action on count 20")
        # ... later ...
        queue.drain(tool_executor, ctx)
    """

    def __init__(self) -> None:
        self._queue: list[DeferredCall] = []

    # ── public API ────────────────────────────────────────────────────

    def enqueue(
        self,
        call_id: str,
        tool_name: str,
        params: dict[str, Any] | None = None,
        *,
        reason: str = "",
        priority: int = 100,
        context_vars: dict[str, Any] | None = None,
    ) -> DeferredCall:
        """Enqueue a deferred tool call.

        Args:
            call_id: Unique ID for this call.
            tool_name: Tool to execute on drain.
            params: Tool parameters.
            reason: Human-readable reason for deferral.
            priority: Sort key (lower executes first).
            context_vars: Context variables to inject into ctx.vars
                before executing the deferred call.

        Returns:
            The created DeferredCall.
        """
        call = DeferredCall(
            call_id=call_id,
            tool_name=tool_name,
            params=dict(params or {}),
            reason=reason,
            priority=priority,
            context_vars=dict(context_vars or {}),
        )
        self._queue.append(call)
        return call

    def drain(
        self,
        tool_executor: Any,
        ctx: Any,
        *,
        filter_reason: str | None = None,
    ) -> list[Any]:
        """Execute all deferred calls in priority order.

        Args:
            tool_executor: Callable (tool_name, params, ctx) -> ToolResult.
            ctx: ExecutionContext (will have context_vars injected).
            filter_reason: If set, only drain calls whose reason
                contains this substring.

        Returns:
            List of ToolResult / ProcedureResult from executed calls.
        """
        results: list[Any] = []

        calls = self._queue[:]
        self._queue.clear()

        # Sort by priority (ascending)
        calls.sort(key=lambda c: c.priority)

        for call in calls:
            if filter_reason and filter_reason not in call.reason:
                # Re-enqueue filtered calls
                self._queue.append(call)
                continue

            # Inject context vars
            for key, value in call.context_vars.items():
                ctx.vars[key] = value

            try:
                result = tool_executor(call.tool_name, call.params, ctx)
                results.append(result)
            except Exception:
                # Deferred calls that fail shouldn't crash the drain —
                # the caller can inspect results for errors.
                pass

        return results

    def drain_end_of_turn(
        self,
        tool_executor: Any,
        ctx: Any,
    ) -> list[Any]:
        """Drain only end-of-turn deferred calls."""
        return self.drain(tool_executor, ctx, filter_reason="end_of_turn")

    def drain_end_of_round(
        self,
        tool_executor: Any,
        ctx: Any,
    ) -> list[Any]:
        """Drain only end-of-round deferred calls."""
        return self.drain(tool_executor, ctx, filter_reason="end_of_round")

    def remove(self, call_id: str) -> DeferredCall | None:
        """Remove a deferred call by ID without executing it.

        Args:
            call_id: The call ID to remove.

        Returns:
            The removed DeferredCall, or None if not found.
        """
        for i, call in enumerate(self._queue):
            if call.call_id == call_id:
                return self._queue.pop(i)
        return None

    def peek(self) -> list[DeferredCall]:
        """Return a snapshot of all pending calls (sorted by priority)."""
        return sorted(self._queue, key=lambda c: c.priority)

    def clear(self) -> None:
        """Remove all pending deferred calls."""
        self._queue.clear()

    def __bool__(self) -> bool:
        return True  # Always truthy — use __len__ for emptiness check

    def __len__(self) -> int:
        return len(self._queue)

    def __repr__(self) -> str:
        return f"DeferredQueue(pending={len(self._queue)})"
