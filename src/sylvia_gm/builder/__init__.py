"""Builder agent package.

Provides a chat-based agent with file I/O tools (read_file, write_file, search_files)
for creating and managing tabletop RPG campaigns.

Backward compat:
    BuilderRunner (class-wrapper), BuilderTurnResult, BuilderLoopError are
    now provided by sylvia_gm.agent._runner as thin wrappers.
"""

# Direct submodule imports (no circular risk — these don't touch agent._runner)
from sylvia_gm.builder._guardrail import GuardrailBlockedError, PathGuardrail
from sylvia_gm.builder._interview_extractor import ExtractionError, InterviewExtractor
from sylvia_gm.builder._prompts import build_builder_system_prompt

# Lazy imports for backward-compat symbols that transitively touch agent._runner.
_BUILDER_LAZY = {
    "BuilderLoopError": "sylvia_gm.agent._runner",
    "BuilderRunner": "sylvia_gm.agent._runner",
    "BuilderTurnResult": "sylvia_gm.agent._runner",
}


def __getattr__(name: str):
    if name in _BUILDER_LAZY:
        import importlib
        mod = importlib.import_module(_BUILDER_LAZY[name])
        if name == "BuilderLoopError":
            return getattr(mod, "AgentLoopError")
        elif name == "BuilderRunner":
            return getattr(mod, "AgentRunner")
        elif name == "BuilderTurnResult":
            return getattr(mod, "AgentTurnResult")
    raise AttributeError(f"module 'sylvia_gm.builder' has no attribute {name!r}")


__all__ = [
    "BuilderLoopError",
    "BuilderRunner",
    "BuilderTurnResult",
    "ExtractionError",
    "GuardrailBlockedError",
    "InterviewExtractor",
    "PathGuardrail",
    "build_builder_system_prompt",
]
