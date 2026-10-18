"""Edge TTS provider — Microsoft's free TTS API via edge-tts.

No API key required. Uses the edge-tts Python package to communicate
with Microsoft Edge's online TTS service.

Edge TTS voice list (common neural voices):
    en-US-AriaNeural, en-US-GuyNeural, en-US-JennyNeural,
    en-US-ChristopherNeural, en-US-EricNeural, en-US-AnaNeural,
    en-GB-SoniaNeural, en-GB-RyanNeural, en-GB-LibbyNeural,
    en-AU-NatashaNeural, en-AU-WilliamNeural,
    en-CA-ClaraNeural, en-CA-LiamNeural,
    en-IE-ConnorNeural, en-IE-EmilyNeural,
    en-IN-NeerjaNeural, en-IN-PrabhatNeural,
"""

from __future__ import annotations

import io
import logging
from typing import TYPE_CHECKING

import edge_tts  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from sylvia_gm.config.loader import TTSConfig

logger = logging.getLogger(__name__)


class EdgeTTSError(RuntimeError):
    """Raised when Edge TTS synthesis fails."""


async def synthesize(
    text: str,
    voice: str = "en-US-AriaNeural",
    *,
    rate: str = "+0%",
    volume: str = "+0%",
    pitch: str = "+0Hz",
    connect_timeout: int = 10,
    receive_timeout: int = 60,
) -> bytes:
    """Synthesize speech via Microsoft Edge TTS.

    Args:
        text: The text to convert to speech.
        voice: Edge TTS voice name (e.g. ``"en-US-AriaNeural"``).
        rate: Speed adjustment (e.g. ``"+10%"``, ``"-5%"``).
        volume: Volume adjustment (e.g. ``"+20%"``).
        pitch: Pitch adjustment (e.g. ``"+5Hz"``).
        connect_timeout: Connection timeout in seconds.
        receive_timeout: Receive timeout in seconds.

    Returns:
        MP3 audio bytes.

    Raises:
        EdgeTTSError: If synthesis fails (network, invalid voice, etc.).
    """
    try:
        communicate = edge_tts.Communicate(
            text=text,
            voice=voice,
            rate=rate,
            volume=volume,
            pitch=pitch,
            connect_timeout=connect_timeout,
            receive_timeout=receive_timeout,
        )

        buffer = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buffer.write(chunk["data"])

        audio_bytes = buffer.getvalue()
        if not audio_bytes:
            raise EdgeTTSError("No audio data received from Edge TTS")

        return audio_bytes

    except EdgeTTSError:
        raise

    except edge_tts.exceptions.NoAudioReceived as exc:
        raise EdgeTTSError(f"No audio received: {exc}") from exc

    except Exception as exc:
        raise EdgeTTSError(
            f"Edge TTS synthesis failed (voice={voice!r}): {exc}"
        ) from exc


async def list_voices(*, proxy: str | None = None) -> list[dict[str, str]]:
    """List available Edge TTS voices.

    Args:
        proxy: Optional proxy URL.

    Returns:
        List of voice dicts with keys like ``Name``, ``ShortName``,
        ``Locale``, ``Gender``, etc.
    """
    try:
        voices = await edge_tts.list_voices(proxy=proxy)
        return voices
    except Exception as exc:
        logger.warning("Failed to list Edge TTS voices: %s", exc)
        return []
