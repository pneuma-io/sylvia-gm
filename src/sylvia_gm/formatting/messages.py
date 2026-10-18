"""Five-category message formatter retained for compatibility callers.

This module defines its own HTML and plain-text renderings. Current desktop
conversation events and presentation are specified by the contracts listed
in docs/INDEX.md, not by AGENTS.md or this formatter's enum.
"""

from dataclasses import dataclass, field
from enum import Enum


class MessageType(Enum):
    """The five categories supported by this formatter."""

    DM_NARRATION = "dm_narration"
    PLAYER = "player"
    NPC = "npc"
    SYSTEM = "system"
    OOC = "ooc"


@dataclass
class ChatMessage:
    """A single message in the chat stream."""

    type: MessageType
    content: str
    speaker: str = ""  # Character/NPC name (empty for DM narration)
    speaker_detail: str = ""  # Class/level for players
    dice_rolls: list[str] = field(default_factory=list)  # Inline dice results
    timestamp: str = ""


class MessageFormatter:
    """Renders ChatMessage instances for display."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def format(self, message: ChatMessage, channel: str = "desktop") -> str:
        """Render *message* for the target *channel*.

        ``channel`` is one of ``"desktop"`` (HTML), ``"signal"`` (plain text),
        or ``"telegram"`` (plain text alias — identical to ``"signal"``).
        """
        if channel == "desktop":
            return self.format_html(message)
        # All IM channels (signal, telegram) use the same plain-text path.
        return self.format_plain(message)

    def format_html(self, message: ChatMessage) -> str:
        """HTML rendering for the desktop UI."""
        handler = self._html_handlers.get(message.type)
        if handler is None:
            return ""
        return handler(message)

    def format_plain(self, message: ChatMessage) -> str:
        """Plain-text rendering for IM platforms."""
        handler = self._plain_handlers.get(message.type)
        if handler is None:
            return ""
        return handler(message)

    # ------------------------------------------------------------------
    # HTML handlers
    # ------------------------------------------------------------------

    @staticmethod
    def _html_dm(msg: ChatMessage) -> str:
        """DM narration — un-bubbled, left-aligned, no speaker label."""
        return f'<div class="msg-dm"><p>{_escape(msg.content)}</p></div>'

    @staticmethod
    def _html_player(msg: ChatMessage) -> str:
        """Player message — bubbled, right-aligned, name + class."""
        speaker_label = msg.speaker
        if msg.speaker_detail:
            speaker_label += f" ({msg.speaker_detail})"
        speaker_span = (
            f'<span class="msg-speaker">{_escape(speaker_label)}</span>'
            if speaker_label
            else ""
        )
        return (
            f'<div class="msg-player">'
            f'{speaker_span}'
            f'<div class="msg-bubble">{_escape(msg.content)}</div>'
            f'</div>'
        )

    @staticmethod
    def _html_npc(msg: ChatMessage) -> str:
        """NPC message — left-border accent, name only, no "(NPC)" tag."""
        speaker_span = (
            f'<span class="msg-speaker">{_escape(msg.speaker)}</span>'
            if msg.speaker
            else ""
        )
        return (
            f'<div class="msg-npc">'
            f'{speaker_span}'
            f'<p>"{_escape(msg.content)}"</p>'
            f'</div>'
        )

    @staticmethod
    def _html_system(msg: ChatMessage) -> str:
        """System message — centered, muted background, border."""
        dice_spans = "".join(
            f'<span class="dice-inline">{_escape(d)}</span>'
            for d in msg.dice_rolls
        )
        return (
            f'<div class="msg-system">'
            f'<div class="msg-system-inner">'
            f'{_escape(msg.content)}'
            f'{dice_spans}'
            f'</div>'
            f'</div>'
        )

    @staticmethod
    def _html_ooc(msg: ChatMessage) -> str:
        """OOC message — centered, dashed border, ◆ prefix."""
        return (
            f'<div class="msg-ooc">'
            f'<div class="msg-ooc-inner">◆ {_escape(msg.content)}</div>'
            f'</div>'
        )

    # ------------------------------------------------------------------
    # Plain-text handlers
    # ------------------------------------------------------------------

    @staticmethod
    def _plain_dm(msg: ChatMessage) -> str:
        """DM narration — plain text, no decoration."""
        return msg.content

    @staticmethod
    def _plain_player(msg: ChatMessage) -> str:
        """Player — *Name (detail):* "content"."""
        label = msg.speaker
        if msg.speaker_detail:
            label += f" ({msg.speaker_detail})"
        return f'*{label}:* "{msg.content}"'

    @staticmethod
    def _plain_npc(msg: ChatMessage) -> str:
        """NPC — Name: "content"."""
        return f'{msg.speaker}: "{msg.content}"'

    @staticmethod
    def _plain_system(msg: ChatMessage) -> str:
        """System — boxed with header/footer, inline dice backtick-wrapped."""
        lines = ["─── System ───", msg.content]
        for d in msg.dice_rolls:
            lines.append(f"`{d}`")
        lines.append("──────────────")
        return "\n".join(lines)

    @staticmethod
    def _plain_ooc(msg: ChatMessage) -> str:
        """OOC — pause markers, [OOC] prefix, resume marker."""
        return (
            "⏸️ SESSION PAUSED\n"
            f"[OOC] {msg.content}\n"
            "▶️ RESUME when ready"
        )

    # ------------------------------------------------------------------
    # Handler dispatch tables
    # ------------------------------------------------------------------

    _html_handlers = {
        MessageType.DM_NARRATION: _html_dm,
        MessageType.PLAYER: _html_player,
        MessageType.NPC: _html_npc,
        MessageType.SYSTEM: _html_system,
        MessageType.OOC: _html_ooc,
    }

    _plain_handlers = {
        MessageType.DM_NARRATION: _plain_dm,
        MessageType.PLAYER: _plain_player,
        MessageType.NPC: _plain_npc,
        MessageType.SYSTEM: _plain_system,
        MessageType.OOC: _plain_ooc,
    }


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _escape(text: str) -> str:
    """Minimal XML/HTML escaping for safe attribute and text content."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
