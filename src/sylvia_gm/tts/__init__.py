"""Multi-provider Text-to-Speech (T5.1).

Provides a pluggable TTS layer with Edge TTS as the free default provider.
"""

from sylvia_gm.tts.engine import TTSEngine

__all__ = ["TTSEngine"]
