"""Retired pre-authority SessionRunner adapter.

Construction now fails closed because DM mechanics must use the canonical
owner-managed CampaignSessionLoop and stateless MCP boundary.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from sylvia_gm.agent._mode import AgentMode
from sylvia_gm.agent._runner import AgentLoopError, AgentRunner, AgentTurnResult
from sylvia_gm.provider._base import Provider
from sylvia_gm.ruleset.schema import RulesetFile
from sylvia_gm.state.state_api import StateAPI

# Backward-compat aliases
TurnResult = AgentTurnResult
SessionLoopError = AgentLoopError


class SessionRunner(AgentRunner):
    """Fail-closed adapter retaining the old constructor signature.

    Old callers receive AgentRunner's explicit retirement error.
    """

    def __init__(
        self,
        store: StateAPI,
        ruleset: RulesetFile,
        provider: Provider,
        *,
        history_path: Path | None = None,
    ) -> None:
        if history_path is None:
            # Create a temp history file (old SessionRunner didn't require one)
            tmpdir = Path(tempfile.gettempdir()) / "sylvia_gm_test_sessions"
            tmpdir.mkdir(parents=True, exist_ok=True)
            import uuid

            history_path = tmpdir / f"session_{uuid.uuid4().hex}.jsonl"

        super().__init__(
            mode=AgentMode.DM_SESSION,
            provider=provider,
            history_path=history_path,
            store=store,
            ruleset=ruleset,
        )


__all__ = [
    "AgentLoopError",
    "AgentRunner",
    "AgentTurnResult",
    "SessionLoopError",
    "SessionRunner",
    "TurnResult",
]
