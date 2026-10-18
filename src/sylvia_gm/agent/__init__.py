"""Agent package with lazy public exports.

The authority owner imports focused session modules and must not transitively
load optional MCP/UI runner dependencies.  Public compatibility exports remain
available through PEP 562 lazy attribute resolution.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sylvia_gm.agent._mode import AgentMode
    from sylvia_gm.agent._runner import (
        AgentLoopError,
        AgentRunner,
        AgentTurnResult,
        StreamEvent,
    )

__all__ = [
    "AgentMode",
    "AgentRunner",
    "AgentTurnResult",
    "AgentLoopError",
    "StreamEvent",
]


def __getattr__(name: str) -> Any:
    if name == "AgentMode":
        from sylvia_gm.agent._mode import AgentMode

        return AgentMode
    if name in {"AgentRunner", "AgentTurnResult", "AgentLoopError", "StreamEvent"}:
        from sylvia_gm.agent._runner import (
            AgentLoopError,
            AgentRunner,
            AgentTurnResult,
            StreamEvent,
        )

        return {
            "AgentRunner": AgentRunner,
            "AgentTurnResult": AgentTurnResult,
            "AgentLoopError": AgentLoopError,
            "StreamEvent": StreamEvent,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
