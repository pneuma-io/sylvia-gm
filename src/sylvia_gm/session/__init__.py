"""Retained session helpers and lazy compatibility exports.

SessionRunner here aliases AgentRunner, whose DM_SESSION mode is retired.
Current gameplay starts through agent._session_loop.CampaignSessionLoop and
agent._provider_session.ProviderSessionAdapter. Context, formatting, and
analysis helpers remain available to their callers; their presence does not
make the old session loop the current desktop entry point.

Runner/result/error aliases are loaded lazily to avoid circular imports.
"""

from sylvia_gm.session._context import ContextManager
from sylvia_gm.session._divergence import (
    DivergenceDetector,
    DivergenceHandler,
    DivergenceResult,
)
from sylvia_gm.session._history import load_history, save_history
from sylvia_gm.session._narrative_scanner import NarrativeScanner, ScanResult
from sylvia_gm.session._npc_dialogue import NPCDialogueManager
from sylvia_gm.session._prompt_assembler import PromptAssembler

__all__ = [
    "SessionRunner",
    "TurnResult",
    "SessionLoopError",
    "DivergenceDetector",
    "DivergenceHandler",
    "DivergenceResult",
    "NPCDialogueManager",
    "ContextManager",
    "NarrativeScanner",
    "ScanResult",
    "PromptAssembler",
    "load_history",
    "save_history",
]

# Lazy backward-compat aliases (avoid circular import with agent._runner → session._tools)
_COMPAT_ALIASES: dict[str, type] = {}


def __getattr__(name: str):
    """Lazy-load backward-compat aliases from agent._runner."""
    if name in _COMPAT_ALIASES:
        return _COMPAT_ALIASES[name]
    if name in ("SessionRunner", "TurnResult", "SessionLoopError"):
        from sylvia_gm.agent._runner import AgentLoopError, AgentRunner, AgentTurnResult

        mapping = {
            "SessionRunner": AgentRunner,
            "TurnResult": AgentTurnResult,
            "SessionLoopError": AgentLoopError,
        }
        _COMPAT_ALIASES.update(mapping)
        return mapping[name]
    raise AttributeError(f"module 'sylvia_gm.session' has no attribute {name!r}")
