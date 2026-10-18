"""Conversation history persistence — DEPRECATED thin wrapper.

All functionality now lives in sylvia_gm.agent._history.
"""

from sylvia_gm.agent._history import load_history, save_history

__all__ = ["load_history", "save_history"]
