"""Owner-local image preferences; authentication remains with the existing connection."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

from sylvia_gm.domain.image_art import IMAGE_MODES

_LOCK = threading.RLock()


class ImageSettings:
    """Persist an explicit image permission independently of language-model selection."""

    def __init__(self, root: Path) -> None:
        self.path = root / "image-generation.json"

    def read(self) -> dict[str, str]:
        with _LOCK:
            mode = "review"
            if self.path.exists():
                value = json.loads(self.path.read_text(encoding="utf-8"))
                if value.get("mode") not in IMAGE_MODES:
                    raise ValueError("Saved image generation mode is invalid")
                mode = value["mode"]
            return {
                "mode": mode,
                "connection": "gpt.subscription",
                "billing": "subscription",
                "usage_note": "Uses ChatGPT usage limits and eligible credits. No API fallback.",
            }

    def save(self, mode: str) -> dict[str, str]:
        if mode not in IMAGE_MODES:
            raise ValueError("Choose Off, Review or Auto image generation")
        with _LOCK:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle, name = tempfile.mkstemp(prefix=".image-settings-", dir=self.path.parent)
            try:
                with os.fdopen(handle, "w", encoding="utf-8") as output:
                    json.dump({"mode": mode}, output)
                    output.write("\n")
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(name, self.path)
            finally:
                if os.path.exists(name):
                    os.unlink(name)
        return self.read()
