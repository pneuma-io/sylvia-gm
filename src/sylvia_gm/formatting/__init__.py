"""Chat message formatting engine.

Renders the five Sylvia GM message types as HTML (desktop UI)
or plain text (IM platforms).
"""

from sylvia_gm.formatting.messages import (
    ChatMessage,
    MessageFormatter,
    MessageType,
)

__all__ = [
    "ChatMessage",
    "MessageFormatter",
    "MessageType",
]
