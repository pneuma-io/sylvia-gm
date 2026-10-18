"""TTSEngine — multi-provider text-to-speech with voice routing.

Default provider is Edge TTS (free, no API key required).

Usage::

    from sylvia_gm.config.loader import TTSConfig
    from sylvia_gm.tts import TTSEngine

    config = TTSConfig(enabled=True, provider="edge")
    engine = TTSEngine(config)

    # Raw synthesis
    audio = await engine.speak("Hello world", voice="en-US-AriaNeural")

    # Narrate a ChatMessage (voice mapped by role)
    from sylvia_gm.formatting.messages import ChatMessage, MessageType
    msg = ChatMessage(type=MessageType.DM_NARRATION, content="You enter the cave.")
    audio = await engine.narrate(msg)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sylvia_gm.tts._edge import synthesize

if TYPE_CHECKING:
    from sylvia_gm.config.loader import TTSConfig
    from sylvia_gm.formatting.messages import ChatMessage

logger = logging.getLogger(__name__)

# Mapping from MessageType / speaker role to TTS voice config key.
_VOICE_KEY_BY_ROLE: dict[str, str] = {
    "dm": "dm",
    "dm_narration": "dm",
    "npc_female": "npc_female",
    "npc_male": "npc_male",
}


class TTSEngine:
    """Multi-provider TTS engine.

    Currently supports Edge TTS as the default (and only built-in) provider.
    Designed to accept additional providers (ElevenLabs, OpenAI TTS) in the future.

    Voice routing:
        The engine maps message roles to voices using the ``voices`` dict
        in :class:`TTSConfig`.  For example a :class:`ChatMessage` with
        ``type=DM_NARRATION`` uses the ``"dm"`` voice; NPC speakers use
        ``"npc_female"`` or ``"npc_male"``.
    """

    def __init__(self, config: TTSConfig) -> None:
        """Initialize the TTS engine.

        Args:
            config: Frozen TTS configuration (provider, voice, enabled, etc.).
        """
        self._config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def config(self) -> TTSConfig:
        """The frozen TTS configuration used by this engine."""
        return self._config

    async def speak(
        self,
        text: str,
        voice: str | None = None,
    ) -> bytes:
        """Convert *text* to audio bytes.

        Args:
            text: The text to synthesise.
            voice: Override voice name.  When *None* the configured
                   default voice (``config.voice``) is used.

        Returns:
            MP3 audio bytes.

        Raises:
            RuntimeError: If the provider call fails.
        """
        voice = voice or self._config.voice

        if self._config.provider == "edge":
            return await synthesize(text, voice=voice)

        raise RuntimeError(
            f"Unknown TTS provider: {self._config.provider!r}. "
            f"Supported: 'edge'."
        )

    async def narrate(self, message: ChatMessage) -> bytes | None:
        """Narrate a single chat message.

        Returns *None* when TTS is not enabled or the message type
        is not narratable (e.g. system messages or player input).

        Voice selection priority:
            1. Explicit ``voice`` override passed to this method (not yet implemented).
            2. Voice from ``config.voices`` mapped by message role.
            3. The default voice from ``config.voice``.

        Args:
            message: A :class:`ChatMessage` to narrate.

        Returns:
            MP3 audio bytes, or *None* if TTS is disabled / not applicable.
        """
        if not self._config.enabled:
            return None

        voice = self._resolve_voice(message)
        if voice is None:
            return None  # Not a narratable message type

        try:
            return await self.speak(message.content, voice=voice)
        except Exception:
            logger.exception("TTS narration failed for message type=%s", message.type)
            return None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _resolve_voice(self, message: ChatMessage) -> str | None:
        """Determine the TTS voice for a ChatMessage.

        Returns:
            Voice name string, or *None* if the message should not be narrated.
        """
        from sylvia_gm.formatting.messages import MessageType

        msg_type = message.type

        # System and OOC messages are not narrated.
        if msg_type in (MessageType.SYSTEM, MessageType.OOC):
            return None

        # Player messages are not narrated (the player knows what they said).
        if msg_type == MessageType.PLAYER:
            return None

        # DM narration → dm voice
        if msg_type == MessageType.DM_NARRATION:
            return self._config.voices.get("dm", self._config.voice)

        # NPC → npc_female or npc_male based on speaker detail / heuristics
        if msg_type == MessageType.NPC:
            role = self._infer_npc_role(message.speaker_detail)
            return self._config.voices.get(role, self._config.voice)

        # Fallback: try to find a voice key matching the string representation
        for role_key, config_key in _VOICE_KEY_BY_ROLE.items():
            if role_key in str(msg_type.value).lower():
                return self._config.voices.get(config_key, self._config.voice)

        return self._config.voice

    @staticmethod
    def _infer_npc_role(speaker_detail: str) -> str:
        """Heuristic to classify NPC voice role.

        Checks *speaker_detail* for gender hints.  Defaults to ``"npc_male"``
        (the more common NPC gender in fantasy RPGs).

        Args:
            speaker_detail: The ``speaker_detail`` field from ChatMessage
                            (e.g. class/race/gender info).

        Returns:
            Voice config key such as ``"npc_female"`` or ``"npc_male"``.
        """
        detail_lower = speaker_detail.lower()

        female_hints = {"female", "woman", "girl", "lady", "woman", "she", "her", "sister", "queen", "witch", "mother"}
        male_hints = {"male", "man", "boy", "lord", "he", "him", "his", "brother", "king", "wizard", "father"}

        for hint in female_hints:
            if hint in detail_lower:
                return "npc_female"

        for hint in male_hints:
            if hint in detail_lower:
                return "npc_male"

        # Default: male voice (most common for monsters/NPCs in fantasy)
        return "npc_male"
