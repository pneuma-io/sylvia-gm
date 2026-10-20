"""Explicit offline recognition proof using an existing controlled WAV; never a microphone.

Run with the dictation extra installed:
  python tests/windows_dictation_smoke.py --model-dir LOCAL_CACHE --audio CONTROLLED.wav \
      --audio-sha256 EXPECTED_SHA256 --expected "I walk north and open the door."

No model download, audio synthesis, playback, device stream or provider call occurs.
Physical microphone quality remains a separate, explicitly authorised user trial.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import time
import wave
from pathlib import Path

from sylvia_gm.local_dictation import MODEL_ID, MODEL_REVISION, SpeechError, transcribe


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--audio-sha256", required=True)
    parser.add_argument("--expected", required=True)
    args = parser.parse_args()
    payload = args.audio.read_bytes()
    if hashlib.sha256(payload).hexdigest() != args.audio_sha256:
        parser.error("Controlled audio SHA256 mismatch")
    with wave.open(io.BytesIO(payload), "rb") as audio:
        if not 0 < audio.getnframes() / audio.getframerate() <= 120:
            parser.error("Controlled WAV must contain between zero and 120 seconds")
    started = time.monotonic()
    text = transcribe(io.BytesIO(payload), args.model_dir, "en")

    def words(value: str) -> list[str]:
        return re.findall(r"\w+", value.casefold())

    if words(text) != words(args.expected):
        raise AssertionError("Controlled recognition differed from the selected expected words")
    silence = io.BytesIO()
    with wave.open(silence, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(bytes(16000 * 2 * 3))
    silence.seek(0)
    try:
        transcribe(silence, args.model_dir, "auto")
    except SpeechError as error:
        if str(error) != "empty":
            raise
    else:
        raise AssertionError("Silence produced a transcript")
    print(
        json.dumps(
            {
                "model": MODEL_ID,
                "revision": MODEL_REVISION,
                "device": "cpu",
                "compute_type": "int8",
                "cpu_threads": 2,
                "controlled_fixture": "passed",
                "silence": "empty",
                "seconds": round(time.monotonic() - started, 3),
                "physical_quality": "not tested",
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
